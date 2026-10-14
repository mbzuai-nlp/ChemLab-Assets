"""CHEM-170 Cytiva Biacore T200 surface plasmon resonance system: process runtime.

Device (data file CY12747, Operating Instructions 28976883 AH, Instrument Handbook 28-9768-63 AC, Software Handbook 28-9768-78 AA, Getting
Started 28-9840-98 AB, Sensor Surface Handbook BR-1005-71 AB, Cytiva concentration application guide): processing unit 600 W x 615 D x 690 H mm,
60 kg; four flow cells (0.06 uL, 40 um high; single, paired 1-2 / 3-4 or serial 1-2-3-4; Fc1 reference for 2, 3, 4, Fc3 for 4), flow 1-100
uL/min, injection 2-350 uL, analysis 4-45 C (max 20 C below ambient), sample compartment 4-45 C (max 15 C below ambient), baseline noise typically
< 0.03 RU RMS, drift < 0.3 RU/min, sample refractive index 1.33-1.40, data 1 or 10 Hz; buffer tray (four bottles A-D, buffer selector, in-line
degasser, two syringe pumps in the left pump compartment), waste and water tray (peristaltic pump with its clamp in the right pump compartment),
temperature-controlled sample compartment with the autosampler (rack tray with a microplate and a reagent rack, or the combined sample and
reagent rack; software-controlled rack tray port with automatic retraction), sensor chip port (cover opened by software when undocked),
five status lamps (ready, system, temperature, sensor chip, run), rear mains switch.  Control-software functions: insert / dock / eject chip,
prime, normalize, standby, maintenance tools (Desorb, Desorb and Sanitize, Superclean, buffer tubing tools, System Check, Shutdown), set
temperature, eject rack, manual run (flow rate, flow path, sample / regeneration injection, wait, new cycle, report point, pause, stop),
immobilization and pH scouting wizards, Method Builder runs (startup cycles, sample cycles with contact and dissociation times, regeneration,
single-cycle kinetics, solvent correction cycles); evaluation: report points, 1:1 kinetics with mass transfer (chi2, SE / T-value, U-value,
quality control), steady-state affinity, calibrated and calibration-free concentration, solvent correction, van 't Hoff / Eyring.

Process model (fixed 0.1 s grid while anything moves or is recorded, 10 s blocks when idle; state between grid points is held): temperature
control (rate-limited first order, 5 K in about 1 h), liquid delivery per flow cell (sample plug with first-order dispersion and serial delays),
bulk refractive index of every solution from the water n(T) (IAPWS) plus solute increments (CRC concentrative-property table; DMSO 1200 RU per
1 %) with the excluded-volume effect of the immobilized ligand, 1:1 binding with two-compartment mass transport (km from the documented flow-cell
expression, D from the documented molecular-weight estimate) and Eyring temperature dependence, regeneration (strength index) and ligand damage,
amine coupling (EDC/NHS activation and hydrolysis, electrostatic pre-concentration by pH, pI and ionic strength, pH-dependent coupling,
ethanolamine deactivation), streptavidin-biotin and Ni-NTA / His capture, detector gain (normalization), docking drift, noise, air in
injections, consumption of buffer, water and samples, waste."""
import copy
import json
import math
import re
import zlib
from pathlib import Path

import numpy as np

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.cytiva_biacoret200"
CATALOG_ID = "CHEM-170"
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch04/t4_measure/cytiva_biacoret200'
LIBS = {'interactions': 'interaction_library.json', 'solutions': 'solution_library.json', 'chips': 'chip_library.json', 'instrument': 'instrument_model.json'}


class ActionRejected(ValueError):
    """A capability call refused with a declared error code (no state change)."""


# ---------------------------------------------------------------- libraries
_LIBC = {}


def _lib(name):
    if name not in _LIBC:
        _LIBC[name] = json.loads((ASSET_DIR / LIBS[name]).read_text())
    return _LIBC[name]


def interactions():
    return _lib('interactions')['entries']


def solution_lib():
    return _lib('solutions')


def chips():
    return _lib('chips')['chips']


def instrument():
    return _lib('instrument')


DOC = instrument()['documented']
ENG = {k: v['value'] for k, v in instrument()['engineering'].items()}
ENG_NOTE = {k: v['note_zh'] for k, v in instrument()['engineering'].items()}
REGEN_S = instrument()['regeneration_strength']
DT = 0.1                                     # fine physics grid (s): the 10 Hz data rate
IDLE_BLOCK = 10.0                            # idle block (s): nothing injected, recorded or moving
R_GAS = 8.314462618
KB = 1.380649e-23
H_PLANCK = 6.62607015e-34
S_BULK = ENG['bulk_sensitivity_RU_per_RIU']  # RU per refractive index unit (engineering)
G_SURF = ENG['surface_RU_per_kg_m2']         # RU per kg/m2 of bound protein (1 RU ~ 1 pg/mm2)
FC_H = DOC['flow_cell_height_m']             # 40 um (documented)
FC_W = ENG['flow_cell_width_m']              # 0.5 mm (assumption: area from the documented volume and height)
FC_L = ENG['flow_cell_length_m']             # 3.0 mm (assumption)
FLOW_RANGE = tuple(DOC['flow_rate_m3_s'])    # 1-100 uL/min
INJ_RANGE = tuple(DOC['injection_volume_m3'])  # 2-350 uL
T_RANGE = tuple(DOC['analysis_temperature_K'])
AMBIENT = tuple(DOC['ambient_operation_K'])
RI_RANGE = tuple(DOC['refractive_index_range'])
BOTTLE_GRADUATION_M3 = dict(buffer_a=25e-6, buffer_b=10e-6, buffer_c=10e-6, buffer_d=10e-6, water=25e-6, waste=50e-6)   # read-off steps (assumption)
RI_DETECT = (RI_RANGE[0] - 0.005, RI_RANGE[1] + 0.005)   # out-of-range judgement: documented 1.33–1.40 + 0.005 engineering margin (water at 45 °C is 1.3290 at 632.8 nm)
MAINS = tuple(DOC['mains_V'])
FLOW_PATHS = ['1', '2', '3', '4', '1-2', '3-4', '1-2-3-4']
REF_SUBTRACTIONS = ['none', '2-1', '3-1', '4-1', '4-3']
DETECTIONS = ['single', 'dual', 'multi']
CHIP_TYPES = sorted(chips())
BOTTLE_SLOTS = ['buffer_a', 'buffer_b', 'buffer_c', 'buffer_d', 'water', 'waste']
BOTTLE_CAP = dict(buffer_a=DOC['buffer_bottles_m3'][0], buffer_b=DOC['buffer_bottles_m3'][1], buffer_c=DOC['buffer_bottles_m3'][2],
                  buffer_d=DOC['buffer_bottles_m3'][3], water=DOC['water_bottle_m3'], waste=DOC['waste_bottle_m3'])
MAINT = ['desorb', 'desorb_and_sanitize', 'superclean', 'wash_buffer_tubing', 'empty_buffer_tubing', 'flow_system_wash']
MAINT_S = dict(ENG['maintenance_s'], superclean=DOC['superclean_s'])
FAULTS = ['power_loss', 'system_error', 'ifc_leak', 'needle_clogged', 'degasser_failure', 'syringe_pump_leak']
MAX_SOLUTIONS = 120
MAX_RUNS = 60
MAX_CYCLES = 400
MAX_EVENTS_LOG = 1500
MAX_POINTS = 20000
LAMPS = ['ready', 'system', 'temperature', 'sensor_chip', 'run']
UL = 1e-9                                    # m3 per uL
UL_MIN = 1e-9 / 60.0                         # m3/s per uL/min
_M64 = (1 << 64) - 1


def _mix64(x):
    x = (x + 0x9E3779B97F4A7C15) & _M64
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & _M64
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & _M64
    return x ^ (x >> 31)


def _uni(seed, k, ch):
    return ((_mix64(_mix64(_mix64(seed & _M64) ^ (k & _M64)) ^ ch) >> 11) + 0.5) / 9007199254740992.0


def _gauss(seed, k, ch):
    u1, u2 = _uni(seed, k, 2 * ch), _uni(seed, k, 2 * ch + 1)
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2 * math.pi * u2)


def _h(text):
    return zlib.crc32(str(text).encode()) & 0x7FFFFFFF


class _Sum:
    def __init__(self):
        self.s = 0.0
        self.c = 0.0

    def add(self, x):
        t = self.s + x
        if abs(self.s) >= abs(x):
            self.c += (self.s - t) + x
        else:
            self.c += (x - t) + self.s
        self.s = t

    def value(self):
        return self.s + self.c


# ---------------------------------------------------------------- optics and transport of solutions
_WT = solution_lib()['water']
_WPOLY = np.polyfit(np.array(_WT['temperature_K']) - 298.15, np.array(_WT['n']), 3)


def n_water(T):
    """Refractive index of water at 632.8 nm, 0.1 MPa (IAPWS formulation values of Harvey et al. 1998, cubic fit 0-60 C)."""
    return float(np.polyval(_WPOLY, T - 298.15))


def eta_water(T):
    """Dynamic viscosity of water (Pa s): Vogel correlation 2.414e-5 x 10^(247.8 / (T - 140)) (engineering; 1.002 mPa s at 20 C)."""
    return 2.414e-5 * 10 ** (247.8 / (T - 140.0))


CRC = solution_lib()['crc_solutes']
ASSUMED = solution_lib()['assumed_solutes']
DMSO_RU_PCT = solution_lib()['dmso']['bulk_RU_per_pct_v_v']
SOLUTIONS_LIB = solution_lib()['solutions']
IONIC = {'sodium_chloride': 1.0, 'potassium_chloride': 1.0, 'magnesium_chloride': 3.0, 'calcium_chloride': 3.0, 'hydrochloric_acid': 1.0, 'sodium_hydroxide': 1.0,
         'disodium_ethylenediamine_tetraacetate_edta_sodium': 3.0, 'hepes': 0.5, 'sodium_hydrogen_phosphate': 3.0, 'sodium_dihydrogen_phosphate': 1.0,
         'potassium_hydrogen_phosphate': 3.0, 'potassium_dihydrogen_phosphate': 1.0, 'nickel_chloride': 3.0, 'guanidine_hcl': 1.0, 'ethanolamine_hcl': 1.0,
         'sodium_bicarbonate': 1.0, 'edc': 1.0, 'sds': 1.0, 'lithium_dodecyl_sulfate': 1.0, 'sodium_hypochlorite': 1.0, 'potassium_sulfate': 3.0,
         'sodium_sulfate': 3.0, 'ammonium_sulfate': 3.0, 'sodium_citrate': 6.0, 'tris_hydroxymethyl_methylamine': 0.5}   # I per mol (engineering)


def _molar_mass(sid):
    if sid in CRC:
        return CRC[sid]['molar_mass_g_mol']
    return ASSUMED[sid]['molar_mass_g_mol']


def _crc_dn(sid, w_pct):
    """Refractive index increment of a CRC solute at mass % w (20 C, 589 nm): table interpolation, linear below the first row and beyond the last."""
    rows = [(r['mass_pct'], r['n_589']) for r in CRC[sid]['table'] if r['n_589'] is not None]
    if not rows or w_pct <= 0:
        return 0.0
    if w_pct <= rows[0][0]:
        return (rows[0][1] - 1.3330) * w_pct / rows[0][0]
    for (w0, n0), (w1, n1) in zip(rows, rows[1:]):
        if w_pct <= w1:
            return (n0 + (n1 - n0) * (w_pct - w0) / (w1 - w0)) - 1.3330
    (w0, n0), (w1, n1) = rows[-2], rows[-1]
    return n1 + (n1 - n0) / (w1 - w0) * (w_pct - w1) - 1.3330


def _crc_eta_rel(sid, w_pct):
    rows = [(r['mass_pct'], r['viscosity_mPa_s']) for r in CRC[sid]['table'] if r['viscosity_mPa_s'] is not None]
    if not rows or w_pct <= 0:
        return 1.0
    pts = [(0.0, 1.002)] + rows
    for (w0, e0), (w1, e1) in zip(pts, pts[1:]):
        if w_pct <= w1:
            return (e0 + (e1 - e0) * (w_pct - w0) / (w1 - w0)) / 1.002
    return pts[-1][1] / 1.002


def solution_optics(comp):
    """Refractive index increment over water, relative viscosity (to water), ionic strength (mol/m3) of a composition dict
    {solutes: {id: mol/m3}, mass_pct: {id: %}, v_v_pct: {id: %}, species: {id: mol/m3}} (additive increments)."""
    dn, eta, I = 0.0, 1.0, 0.0
    for sid, c in comp.get('solutes', {}).items():
        w = c * _molar_mass(sid) * 1e-4                 # mass % from mol/m3 (density ~1 kg/L)
        if sid in CRC:
            dn += _crc_dn(sid, w)
            eta *= _crc_eta_rel(sid, w)
        else:
            dn += ASSUMED[sid]['dn_per_mass_pct'] * w
        I += IONIC.get(sid, 0.0) * c
    for sid, w in comp.get('mass_pct', {}).items():
        if sid in CRC:
            dn += _crc_dn(sid, w)
            eta *= _crc_eta_rel(sid, w)
        else:
            dn += ASSUMED[sid]['dn_per_mass_pct'] * w
    for sid, pct in comp.get('v_v_pct', {}).items():
        if sid == 'dmso':
            dn += DMSO_RU_PCT * pct / S_BULK
            eta *= 1.0 + 0.012 * pct                     # DMSO viscosity increase (engineering)
        elif sid in CRC:
            w = pct * 0.79 if sid in ('ethanol', 'methanol', '1_propanol', '2_propanol') else pct
            dn += _crc_dn(sid, w)
            eta *= _crc_eta_rel(sid, w)
        else:
            dn += ASSUMED[sid]['dn_per_mass_pct'] * pct
    return dn, eta, I


def library_composition(sol_id):
    s = SOLUTIONS_LIB[sol_id]
    return dict(solutes={k: v * 1000.0 for k, v in s.get('components', {}).items()}, mass_pct=dict(s.get('mass_pct', {})),
                v_v_pct=dict(s.get('v_v_pct', {})), species={}, pH=s.get('pH'), base=sol_id)


# ---------------------------------------------------------------- species (analytes / ligands) and interactions
def _slug(text, n=28):
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')[:n].strip('_')


def _species_table():
    sp, pairs = {}, {}
    for iid, e in interactions().items():
        tag = (e.get('pdb_complex') or iid).split('_')[0].lower() if e.get('pdb_complex') else 'gskit'
        ids = []
        for key in ('partner_1', 'partner_2'):
            p = e[key]
            sid = f'{_slug(p["name"])}:{tag}'
            sp[sid] = dict(name=p['name'], mw_da=float(p['mw_da']), D_m2_s=float(p['diffusion_m2_s']), pI=p.get('isoelectric_point') or 7.0,
                           tags=[p['his_tag']] if p.get('his_tag') else [], kind='protein', evidence=p.get('mw_evidence'), interaction=iid)
            ids.append(sid)
        pairs[iid] = dict(a=ids[0], b=ids[1], ka=e['ka_m3_mol_s'], kd=e['kd_s'], T0=e['reference_temperature_K'], dHa=e['activation_enthalpy_association_J_mol'],
                          dHd=e['activation_enthalpy_dissociation_J_mol'], sites=e.get('stoichiometry', 1), activity=e.get('ligand_activity_after_amine_coupling', 0.8),
                          evidence=e['evidence'], library=True, s_req=e.get('regeneration_requirement', ENG['regeneration']['complex_tolerance_default']))
    return sp, pairs


SHELF, LIB_PAIRS = _species_table()
GS_LIGAND, GS_ANALYTE = LIB_PAIRS['gs_kit_anti_b2m']['a'], LIB_PAIRS['gs_kit_anti_b2m']['b']


def diffusion_estimate(mw_da, T=298.15, eta=None, f=1.2):
    """Cytiva estimate D = 342.3e-11 / (M^(1/3) f eta_rel) at 25 C (eta_rel 0.89), scaled with T / eta (Stokes-Einstein)."""
    d25 = 342.3e-11 / (mw_da ** (1.0 / 3.0) * f * 0.89)
    eta = eta_water(T) if eta is None else eta
    return d25 * (T / 298.15) * (eta_water(298.15) / eta)


def km_flow_cell(D, flow_m3_s):
    """Mass transport coefficient (m/s): km = 0.98 (D^2 f / (0.3 h^2 w l))^(1/3) (Software Handbook 9.8)."""
    return 0.98 * (D * D * flow_m3_s / (0.3 * FC_H * FC_H * FC_W * FC_L)) ** (1.0 / 3.0)


def eyring(k0, dH, T0, T):
    return k0 * (T / T0) * math.exp(-dH / R_GAS * (1.0 / T - 1.0 / T0))


# ---------------------------------------------------------------- schema helpers
def number(unit='1', lo=None, hi=None):
    s = {'type': 'number', 'x-unit': unit}
    if lo is not None:
        s['minimum'] = lo
    if hi is not None:
        s['maximum'] = hi
    return s


def obj(properties, optional=()):
    return dict(type='object', properties=properties, required=[k for k in properties if k not in optional], additionalProperties=False)


def nullable(s):
    return {'anyOf': [s, {'type': 'null'}]}


def array(s, minimum=0, maximum=4096):
    return dict(type='array', items=s, minItems=minimum, maxItems=maximum)


def enum(items):
    return {'type': 'string', 'enum': list(items)}


def integer(lo, hi):
    return {'type': 'integer', 'x-unit': '1', 'minimum': lo, 'maximum': hi}


BOOL = {'type': 'boolean'}
TEXT = {'type': 'string'}
INT = {'type': 'integer', 'x-unit': '1'}


def validate(s, v):
    if 'anyOf' in s:
        for o in s['anyOf']:
            try:
                validate(o, v)
                return
            except ActionRejected:
                pass
        raise ActionRejected('INVALID_ARGUMENT')
    k = s.get('type')
    if k in ('number', 'integer'):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or (k == 'integer' and not float(v).is_integer()):
            raise ActionRejected('INVALID_ARGUMENT')
        if not s.get('minimum', -math.inf) <= v <= s.get('maximum', math.inf):
            raise ActionRejected('INVALID_ARGUMENT')
    elif k == 'object':
        if not isinstance(v, dict):
            raise ActionRejected('INVALID_ARGUMENT')
        if 'properties' in s:
            req = set(s.get('required', s['properties']))
            if not req <= set(v) or not set(v) <= set(s['properties']):
                raise ActionRejected('INVALID_ARGUMENT')
            for key, value in v.items():
                validate(s['properties'][key], value)
        elif 'additionalProperties' in s and isinstance(s['additionalProperties'], dict):
            if len(v) > s.get('maxProperties', math.inf) or len(v) < s.get('minProperties', 0):
                raise ActionRejected('INVALID_ARGUMENT')
            for key, value in v.items():
                if 'propertyNames' in s:
                    validate(s['propertyNames'], key)
                validate(s['additionalProperties'], value)
    elif k == 'array':
        if not isinstance(v, list) or not s.get('minItems', 0) <= len(v) <= s.get('maxItems', math.inf):
            raise ActionRejected('INVALID_ARGUMENT')
        for x in v:
            validate(s['items'], x)
    elif k == 'boolean' and type(v) is not bool:
        raise ActionRejected('INVALID_ARGUMENT')
    elif k == 'string':
        if not isinstance(v, str) or len(v) > s.get('maxLength', 10 ** 6) or len(v) < s.get('minLength', 0):
            raise ActionRejected('INVALID_ARGUMENT')
        if 'pattern' in s and not re.fullmatch(s['pattern'], v):
            raise ActionRejected('INVALID_ARGUMENT')
    elif k == 'null' and v is not None:
        raise ActionRejected('INVALID_ARGUMENT')
    if 'enum' in s and v not in s['enum']:
        raise ActionRejected('INVALID_ARGUMENT')


# ---------------------------------------------------------------- rack geometry (positions, capacities)
def _plate_positions(fmt):
    rows, cols = ('ABCDEFGH', 12) if fmt.startswith('96') else ('ABCDEFGHIJKLMNOP', 24)
    return [f'P1{r}{c}' for r in rows for c in range(1, cols + 1)]


PLATES = {'96_shallow': 0.25e-6, '96_deep': 1.0e-6, '384_shallow': 0.08e-6, '384_deep': 0.20e-6}           # well capacity (m3; assumption)
REAGENT_RACKS = {
    'type1': {f'R2A{i}': 1.5e-6 for i in range(1, 21)},                                                     # 20 vials 11 mm 1.5 mL (documented)
    'type2': dict({f'R2A{i}': 4.0e-6 for i in range(1, 10)}, **{f'R2B{i}': 0.8e-6 for i in range(1, 25)}),  # 9 x 16 mm 4 mL + 24 x 7.5 mm 0.8 mL
}
SAMPLE_REAGENT_RACK = dict({f'R1A{i}': 4.0e-6 for i in range(1, 10)}, **{f'R1B{i}': 1.5e-6 for i in range(1, 25)}, **{f'R1C{i}': 0.8e-6 for i in range(1, 46)})
RACK_CONFIGS = ['tray_plate_type1', 'tray_plate_type2', 'tray_type2_only', 'sample_and_reagent_rack', 'empty']   # empty: plates and racks taken off the tray


def rack_layout(config, plate):
    if config == 'sample_and_reagent_rack':
        return dict(SAMPLE_REAGENT_RACK)
    out = {}
    if config in ('tray_plate_type1', 'tray_plate_type2') and plate:
        out.update({p: PLATES[plate] for p in _plate_positions(plate)})
    out.update(REAGENT_RACKS['type1' if config == 'tray_plate_type1' else 'type2'])
    return out


def _dead_volume(pos):
    return ENG['liquids']['dead_volume_well_m3'] if pos.startswith('P1') else ENG['liquids']['dead_volume_vial_m3']


# ---------------------------------------------------------------- capabilities
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=c, function_zh=meaning, visibility=visibility, kind=kind)


SID = dict(type='string', minLength=1, maxLength=40, pattern='^[A-Za-z0-9_.:+-]+$')
SPID = dict(type='string', minLength=1, maxLength=60, pattern='^[A-Za-z0-9_.:+-]+$')
POS = dict(type='string', pattern='^(P1[A-P](?:[1-9]|1[0-9]|2[0-4])|R1[ABC](?:[1-9]|[1-3][0-9]|4[0-5])|R2[AB](?:[1-9]|1[0-9]|2[0-4]))$')
LIB_SOLUTIONS = sorted(SOLUTIONS_LIB)
FLOW = number('m3/s', FLOW_RANGE[0] * (1 - 1e-9), FLOW_RANGE[1] * (1 + 1e-9))
CONC = number('mol/m3', 0.0, 1.0e4)
OPERATION = obj(dict(operation_id=TEXT))
SOURCE = obj(dict(source_id=SID, volume_m3=number('m3', 1.0e-10, 1.0e-3)))
SPECIES_C = obj(dict(species_id=SPID, concentration_mol_m3=CONC))
EVENT = obj(dict(time_s=number('s'), kind=enum(['power', 'temperature', 'chip', 'flow', 'rack', 'run', 'injection', 'maintenance', 'solution', 'warning', 'error',
                                               'fault', 'truth', 'data']), code=TEXT, message_zh=TEXT))
PLANT_EXAMPLE = dict(room_k=295.15, mains_voltage_v=230.0, noise_seed=1)
PLANT = obj(dict(room_k=number('K', 283.15, 313.15), mains_voltage_v=number('V', 0.0, 264.0), noise_seed=integer(0, 2 ** 31 - 1),
                 relative_humidity=number('1', 0.0, 1.0), rack_auto_retract_s=number('s', 10.0, 3600.0)),
            optional=('relative_humidity', 'rack_auto_retract_s'))
FC_ROW = obj(dict(fc=integer(1, 4), response_ru=nullable(number('RU')), raw_gain=number('1'), normalized=BOOL))
COMMAND = obj(dict(command=enum(['flow_rate', 'flow_path', 'sample_injection', 'regeneration_injection', 'wait', 'new_cycle', 'eject_rack', 'report_point']),
                   flow_rate_m3_s=FLOW, flow_path=enum(FLOW_PATHS), reference_subtraction=enum(REF_SUBTRACTIONS), position=POS,
                   contact_s=number('s', 1.0, 21000.0), high_viscosity=BOOL, wait_s=number('s', 0.0, 86400.0), label=dict(type='string', maxLength=40)),
              optional=('flow_rate_m3_s', 'flow_path', 'reference_subtraction', 'position', 'contact_s', 'high_viscosity', 'wait_s', 'label'))
CYCLE_CMD = obj(dict(type=enum(['sample', 'regeneration', 'capture', 'wait']), position=POS, contact_s=number('s', 1.0, 21000.0),
                     dissociation_s=number('s', 0.0, 21600.0), flow_rate_m3_s=FLOW, stabilization_s=number('s', 0.0, 3600.0), wait_s=number('s', 0.0, 86400.0)),
                optional=('position', 'contact_s', 'dissociation_s', 'flow_rate_m3_s', 'stabilization_s', 'wait_s'))
KEYWORDS = obj(dict(sample_name=dict(type='string', maxLength=40), concentration_mol_m3=CONC, mw_da=number('Da', 1.0, 1.0e7), analyte_label=dict(type='string', maxLength=60)),
               optional=('sample_name', 'concentration_mol_m3', 'mw_da', 'analyte_label'))
CYCLE = obj(dict(purpose=enum(['startup', 'sample', 'blank', 'solvent_correction', 'control']), commands=array(CYCLE_CMD, 1, 16), keywords=KEYWORDS,
                 analysis_temperature_k=number('K', *T_RANGE)), optional=('keywords', 'analysis_temperature_k'))
METHOD_EXAMPLE = dict(run_id='kin_b2m', data_rate_hz=10, detection='dual', flow_path='3-4', reference_subtraction='4-3',
                      cycles=[dict(purpose='startup', commands=[dict(type='sample', position='R2B1', contact_s=120.0, dissociation_s=120.0, flow_rate_m3_s=30 * UL_MIN),
                                                                dict(type='regeneration', position='R2A1', contact_s=30.0, flow_rate_m3_s=30 * UL_MIN, stabilization_s=60.0)])])
METHOD = obj(dict(run_id=SID, data_rate_hz=enum([1, 10]) | {'type': 'integer', 'x-unit': '1/s'}, detection=enum(DETECTIONS), flow_path=enum(FLOW_PATHS),
                  reference_subtraction=enum(REF_SUBTRACTIONS), buffer_tube=enum(['buffer_a', 'buffer_b', 'buffer_c', 'buffer_d']), cycles=array(CYCLE, 1, MAX_CYCLES),
                  sample_compartment_k=number('K', *T_RANGE), prime_before=BOOL, normalize_position=POS, ignore_temperature=BOOL),
             optional=('detection', 'reference_subtraction', 'buffer_tube', 'sample_compartment_k', 'prime_before', 'normalize_position', 'ignore_temperature'))
IMMOB_FC = obj(dict(fc=integer(1, 4), chemistry=enum(['amine', 'his_capture', 'biotin_capture']), mode=enum(['contact', 'aim', 'blank']), ligand_position=POS,
                    ligand_name=dict(type='string', maxLength=40), contact_s=number('s', 1.0, 3600.0), flow_rate_m3_s=FLOW, target_ru=number('RU', 10.0, 40000.0),
                    edc_position=POS, nhs_position=POS, mix_position=POS, ethanolamine_position=POS, wash_position=POS, nickel_position=POS),
               optional=('ligand_position', 'ligand_name', 'contact_s', 'flow_rate_m3_s', 'target_ru', 'edc_position', 'nhs_position', 'mix_position',
                         'ethanolamine_position', 'wash_position', 'nickel_position'))
IMMOB = obj(dict(run_id=SID, flow_cells=array(IMMOB_FC, 1, 4), prime_before=BOOL, normalize_position=POS, ignore_temperature=BOOL),
            optional=('prime_before', 'normalize_position', 'ignore_temperature'))
IMMOB_EXAMPLE = dict(run_id='immob_fc4', flow_cells=[dict(fc=4, chemistry='amine', mode='contact', ligand_position='R1A5', ligand_name='anti-b2m', contact_s=420.0,
                                                          flow_rate_m3_s=10 * UL_MIN, edc_position='R1A1', nhs_position='R1A2', mix_position='R1A3',
                                                          ethanolamine_position='R1A4')])
SCOUT = obj(dict(run_id=SID, fc=integer(1, 4), ligand_positions=array(POS, 1, 8), contact_s=number('s', 10.0, 1200.0), flow_rate_m3_s=FLOW, wash_position=POS),
            optional=('contact_s', 'flow_rate_m3_s'))
RP_ROW = obj(dict(cycle=INT, curve=TEXT, label=TEXT, time_s=number('s'), absolute_ru=number('RU'), relative_ru=number('RU'), window_s=number('s')))
FITPAR = obj(dict(name=TEXT, value=number('1'), unit=TEXT, se=nullable(number('1')), t_value=nullable(number('1')), scope=enum(['global', 'local']), fixed=BOOL))
QC = obj(dict(item=TEXT, status=enum(['pass', 'warning', 'fail', 'not_evaluated']), detail_zh=TEXT))
KIN_IN = obj(dict(run_id=SID, cycles=array(integer(1, MAX_CYCLES), 1, 64), subtraction=enum(['2-1', '3-1', '4-1', '4-3', '1', '2', '3', '4']),
                  blank_cycles=array(integer(1, MAX_CYCLES), 0, 16), fit_mass_transfer=BOOL, fit_ri=BOOL, concentrations_mol_m3=array(CONC, 0, 64),
                  mw_da=number('Da', 1.0, 1.0e7)), optional=('blank_cycles', 'fit_mass_transfer', 'fit_ri', 'concentrations_mol_m3', 'mw_da'))
KIN_OUT = obj(dict(model=TEXT, parameters=array(FITPAR, 0, 80), ka_m3_mol_s=number('m3/(mol*s)'), kd_s=number('1/s'), KD_mol_m3=number('mol/m3'), rmax_ru=number('RU'),
                   kt_ru_m3_mol_s=nullable(number('RU*m3/(mol*s)')), tc=nullable(number('1')), chi2_ru2=number('RU^2'), u_value=number('1'), quality=array(QC, 0, 12),
                   curves=INT, points=INT))
AFF_OUT = obj(dict(KD_mol_m3=number('mol/m3'), rmax_ru=number('RU'), offset_ru=number('RU'), chi2_ru2=number('RU^2'), points=array(obj(dict(concentration_mol_m3=number('mol/m3'),
                   req_ru=number('RU'), fit_ru=number('RU'))), 0, 64), quality=array(QC, 0, 8)))
CONC_IN = obj(dict(run_id=SID, mode=enum(['calibration', 'cfca']), subtraction=enum(['2-1', '3-1', '4-1', '4-3', '1', '2', '3', '4']),
                   standard_cycles=array(integer(1, MAX_CYCLES), 0, 32), standard_concentrations_mol_m3=array(CONC, 0, 32), sample_cycles=array(integer(1, MAX_CYCLES), 1, 32),
                   dilution_factors=array(number('1', 1.0, 1e6), 0, 32), mw_da=number('Da', 100.0, 1.0e7), diffusion_m2_s=number('m2/s', 1e-12, 1e-8)),
              optional=('standard_cycles', 'standard_concentrations_mol_m3', 'dilution_factors', 'mw_da', 'diffusion_m2_s'))
CONC_OUT = obj(dict(mode=TEXT, samples=array(obj(dict(cycle=INT, concentration_mol_m3=nullable(number('mol/m3')), report_ru=number('RU'), detail_zh=TEXT)), 0, 32),
                    calibration=nullable(obj(dict(model=TEXT, parameters=array(number('1'), 0, 4), chi2_ru2=number('RU^2')))), quality=array(QC, 0, 8)))
SC_IN = obj(dict(run_id=SID, correction_cycles=array(integer(1, MAX_CYCLES), 3, 16), sample_cycles=array(integer(1, MAX_CYCLES), 1, 64), active_fc=integer(2, 4),
                 reference_fc=integer(1, 3)))
SC_OUT = obj(dict(coefficients=array(number('1'), 3, 3), chi2_ru2=number('RU^2'), range_ru=array(number('RU'), 2, 2),
                  corrected=array(obj(dict(cycle=INT, reference_ru=number('RU'), uncorrected_ru=number('RU'), correction_ru=nullable(number('RU')),
                                           corrected_ru=nullable(number('RU')))), 0, 64), quality=array(QC, 0, 4)))
THERMO_ROW = obj(dict(temperature_k=number('K', *T_RANGE), KD_mol_m3=number('mol/m3', 1e-15, 1e6), ka_m3_mol_s=number('m3/(mol*s)', 1e-6, 1e8), kd_s=number('1/s', 1e-8, 10.0)),
                 optional=('ka_m3_mol_s', 'kd_s'))
THERMO_OUT = obj(dict(dH_j_mol=number('J/mol'), dS_j_k_mol=number('J/(K*mol)'), dG298_j_mol=number('J/mol'), dCp_j_k_mol=nullable(number('J/(K*mol)')),
                      association=nullable(obj(dict(dH_j_mol=number('J/mol'), T_dS298_j_mol=number('J/mol'), dG298_j_mol=number('J/mol')))),
                      dissociation=nullable(obj(dict(dH_j_mol=number('J/mol'), T_dS298_j_mol=number('J/mol'), dG298_j_mol=number('J/mol')))), quality=array(QC, 0, 4)))
LEDGER_KEYS = ['electrical_energy_j', 'buffer_used_m3', 'water_used_m3', 'waste_added_m3', 'waste_spilled_m3', 'sample_injected_m3', 'sample_aspirated_m3',
               'solution_prepared_m3', 'stock_used_m3', 'ligand_immobilized_ru', 'injections', 'cycles', 'runs_completed', 'chips_docked', 'maintenance_runs']
LEDGER = obj({k: number('J' if k.endswith('_j') else 'm3' if k.endswith('_m3') else 'RU' if k.endswith('_ru') else '1') for k in LEDGER_KEYS})
KITS = ['getting_started', 'amine_coupling', 'maintenance', 'his_capture']

cap('plant.configure', PLANT, obj(dict(plant=obj(dict(room_k=number('K'), mains_voltage_v=number('V'), noise_seed=INT, relative_humidity=number('1'),
                                                     rack_auto_retract_s=number('s'))))),
    PLANT_EXAMPLE, ['cabinet'], '宿主声明实验室条件：室温（厂家运行环境18–33 °C）、供电电压（100–240 V）、相对湿度、托盘自动收回时间（Tools→Preferences）与噪声种子（只在首次声明时生效）',
    'evaluator', 'simulation')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), ['power_switch', 'rear_panel', 'lamp_ready'],
    '处理单元开机/关机（后部右侧的电源开关；开机时全部指示灯亮几秒，随后ready亮，控制软件约30 s建立连接）')
cap('power.press_switch', obj({}), obj(dict(enabled=BOOL)), {}, ['power_switch'], '按一下后部的电源开关（切换开/关）')
cap('temperature.set', obj(dict(analysis_k=number('K', *T_RANGE), sample_compartment_k=number('K', *T_RANGE)), optional=('analysis_k', 'sample_compartment_k')),
    obj(dict(analysis_setpoint_k=number('K'), sample_compartment_setpoint_k=number('K'))), dict(analysis_k=298.15), ['detection_unit', 'sample_compartment_box'],
    'Tools→Set Temperature：分析温度（流通池，4–45 °C，最多比环境低20 °C）与样品舱温度（4–45 °C，最多比环境低15 °C）；运行中不能改')
cap('kit.provide', obj(dict(kit=enum(KITS))), obj(dict(items=array(TEXT, 0, 16))), dict(kit='getting_started'), ['cabinet'],
    '评测者把试剂盒放到实验台：入门试剂盒（10×HBS-EP+、抗β2-微球蛋白单抗、β2-微球蛋白、乙酸钠pH 5.0、甘氨酸-HCl pH 2.5、BIAnormalizing）、胺偶联试剂盒（EDC、NHS、乙醇胺）、'
    '维护试剂盒（BIAdesorb 1/2、BIAtest、BIAnormalizing、10×HBS-N）或His捕获（NiCl2、EDTA）', 'evaluator', 'simulation')
cap('stock.provide', obj(dict(stock_id=SID, volume_m3=number('m3', 1.0e-9, 1.0e-3), base=enum(LIB_SOLUTIONS), species=array(SPECIES_C, 1, 6), label=array(SPECIES_C, 0, 6)),
                         optional=('label',)),
    obj(dict(stock_id=TEXT, volume_m3=number('m3'))),
    dict(stock_id='barstar_stock', volume_m3=1.0e-7, base='hbs_ep_plus', species=[dict(species_id='barstar:1brs', concentration_mol_m3=0.01)]), ['cabinet'],
    '评测者给一份储备液：真实浓度与缓冲液（agent只看到标签浓度）', 'evaluator', 'simulation')
cap('species.define', obj(dict(species_id=SPID, name=dict(type='string', maxLength=80), mw_da=number('Da', 50.0, 1.0e7), isoelectric_point=number('1', 2.0, 12.0),
                               tags=array(enum(['his6', 'his10', 'biotin']), 0, 2), kind=enum(['protein', 'small_molecule', 'nucleic_acid']), diffusion_m2_s=number('m2/s', 1e-12, 1e-8)),
                          optional=('isoelectric_point', 'tags', 'kind', 'diffusion_m2_s')),
    obj(dict(species_id=TEXT, diffusion_m2_s=number('m2/s'))), dict(species_id='cmpd_x', name='compound X', mw_da=350.0, kind='small_molecule'), ['cabinet'],
    '评测者定义库外物质（分子量、等电点、标签、扩散系数；缺省扩散系数按厂家分子量公式估计）', 'evaluator', 'simulation')
cap('interaction.define', obj(dict(interaction_id=SID, ligand=SPID, analyte=SPID, ka_m3_mol_s=number('m3/(mol*s)', 1e-3, 1e7), kd_s=number('1/s', 1e-7, 10.0),
                                   temperature_k=number('K', *T_RANGE), activation_enthalpy_association_j_mol=number('J/mol', -1e5, 3e5),
                                   activation_enthalpy_dissociation_j_mol=number('J/mol', -1e5, 3e5), sites=integer(1, 4), ligand_activity=number('1', 0.0, 1.0),
                                   regeneration_requirement=number('1', 0.0, 5.0)),
                              optional=('temperature_k', 'activation_enthalpy_association_j_mol', 'activation_enthalpy_dissociation_j_mol', 'sites', 'ligand_activity',
                                        'regeneration_requirement')),
    obj(dict(interaction_id=TEXT)), dict(interaction_id='x_ca2', ligand='ca2:custom', analyte='cmpd_x', ka_m3_mol_s=50.0, kd_s=0.05), ['cabinet'],
    '评测者定义两种物质之间相互作用的真值（ka、kd、参考温度、活化焓、配体位点数、胺偶联后配体活性、再生所需强度）', 'evaluator', 'simulation')
cap('solution.prepare', obj(dict(solution_id=SID, volume_m3=number('m3', 1.0e-8, 1.0e-3), base=enum(LIB_SOLUTIONS), sources=array(SOURCE, 0, 4), dmso_volume_fraction=number('1', 0.0, 0.5)),
                            optional=('sources', 'dmso_volume_fraction')),
    obj(dict(solution_id=TEXT, volume_m3=number('m3'), label=array(SPECIES_C, 0, 8))),
    dict(solution_id='barstar_32nm', volume_m3=6.0e-7, base='hbs_ep_plus', sources=[dict(source_id='barstar_stock', volume_m3=1.92e-9)]), ['sample_compartment_box'],
    '在实验台配溶液：以库中的缓冲液/试剂为溶剂（base），移取储备液或已配溶液（sources）并定容，可加DMSO（体积%）；移液有误差（真值不可见）', 'agent', 'robot_skill')
cap('rack.configure', obj(dict(config=enum(RACK_CONFIGS), plate=enum(sorted(PLATES) + ['none']), correctly_mounted=BOOL), optional=('correctly_mounted',)),
    obj(dict(config=TEXT, positions=INT)), dict(config='sample_and_reagent_rack', plate='none'), ['rack_tray', 'rack_tray_carriage'],
    '在弹出的托盘上装微孔板（96/384、浅孔/深孔，A1朝前）与试剂架（type 1/2），或换成样品与试剂组合架，或全部取下（empty）；装反时托盘不能送入', 'agent', 'robot_skill')
cap('rack.place', obj(dict(position=POS, solution_id=SID, volume_m3=number('m3', 1.0e-9, 4.0e-6), capped=BOOL), optional=('capped',)),
    obj(dict(position=TEXT, solution_id=TEXT, volume_m3=number('m3'))), dict(position='R1A5', solution_id='ligand_ph50', volume_m3=1.2e-7, capped=True), ['rack_tray'],
    '把溶液分装到托盘上的孔/小瓶（盖帽或贴膜防蒸发）；托盘须弹出', 'agent', 'robot_skill')
cap('rack.remove', obj(dict(position=POS)), obj(dict(position=TEXT, removed_m3=number('m3'))), dict(position='R1A5'), ['rack_tray'], '取出某位置的小瓶/吸空孔（托盘须弹出）',
    'agent', 'robot_skill')
cap('bottle.place', obj(dict(slot=enum(['buffer_a', 'buffer_b', 'buffer_c', 'buffer_d', 'water']), solution_id=SID, volume_m3=number('m3', 1.0e-5, 1.0e-3),
                             tube_inserted=BOOL), optional=('tube_inserted',)),
    obj(dict(slot=TEXT, volume_m3=number('m3'))), dict(slot='buffer_a', solution_id='hbs_run', volume_m3=5.0e-4), ['buffer_tray', 'bottle_buffer_a', 'water_bottle'],
    '把缓冲液瓶放到左侧缓冲液托盘（A为1 L，B–D为250 mL）并插入对应管（A–D），或把蒸馏水瓶（500 mL）放到右侧托盘；原瓶内容倒掉', 'agent', 'robot_skill')
cap('bottle.remove', obj(dict(slot=enum(BOTTLE_SLOTS))), obj(dict(slot=TEXT, removed_m3=number('m3'))), dict(slot='buffer_b'), ['buffer_tray', 'waste_water_tray'],
    '取走一个瓶（管放回左泵舱门内的管夹）', 'agent', 'robot_skill')
cap('bottle.empty_waste', obj({}), obj(dict(emptied_m3=number('m3'))), {}, ['waste_bottle'], '倒空废液瓶（2 L；每次运行后）并装回带两根管的瓶盖', 'agent', 'robot_skill')
cap('door.set', obj(dict(door=enum(['left_pump', 'right_pump']), open=BOOL)), obj(dict(door=TEXT, open=BOOL)), dict(door='right_pump', open=True),
    ['left_pump_door', 'right_pump_door'], '按门内边打开/关上左泵舱门（脱气机、两个注射泵、缓冲液选择阀）或右泵舱门（蠕动泵与夹）', 'agent', 'robot_skill')
cap('clamp.set', obj(dict(closed=BOOL)), obj(dict(closed=BOOL)), dict(closed=True), ['peristaltic_clamp_lever', 'peristaltic_pump'],
    '右泵舱内蠕动泵的管夹：手柄竖直为夹紧（运行前须夹紧），向右扳到底为松开（关机存放时）；须先开右泵舱门', 'agent', 'robot_skill')
cap('sample_compartment.open_door', obj({}), obj(dict(open=BOOL)), {}, ['sample_compartment_door'],
    '用一字螺丝刀把锁螺钉逆时针转1/8圈，样品舱门向上弹开（须先关电源、托盘收回且无板与试剂架）', 'agent', 'robot_skill')
cap('sample_compartment.close_door', obj({}), obj(dict(open=BOOL)), {}, ['sample_compartment_door'], '拉下样品舱门并压到卡住', 'agent', 'robot_skill')
cap('sample_compartment.clean', obj({}), obj(dict(cleaned=BOOL)), {}, ['sample_compartment_box', 'autosampler_needle'],
    '清洁样品舱（溢出物、针与供液块上的盐结晶）；须舱门打开', 'agent', 'robot_skill')
cap('sample_compartment.set_light', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['inspection_window'], '软件开关样品舱照明')
cap('chip.open_port', obj({}), obj(dict(port_open=BOOL)), {}, ['sensor_chip_port', 'chip_port_cover'],
    'Insert Chip：没有对接芯片时芯片口盖自动打开（盖不能手动打开）')
cap('chip.insert', obj(dict(chip_type=enum(CHIP_TYPES), chip_id=SID, reuse=BOOL), optional=('reuse',)), obj(dict(chip_id=TEXT, chip_type=TEXT)),
    dict(chip_type='CM5', chip_id='cm5_20261001'), ['sensor_chip_port'],
    '把Series S芯片（在保护盒中）箭头朝里插入芯片口；新芯片的Chip id须唯一，复用芯片须是本仪器用过的', 'agent', 'robot_skill')
cap('chip.close_cover', obj({}), obj(dict(port_open=BOOL)), {}, ['chip_port_cover'], '轻压芯片口盖直到卡住', 'agent', 'robot_skill')
cap('chip.dock', obj({}), OPERATION, {}, ['sensor_chip_port', 'ifc_block'], 'Dock chip：芯片压到IFC上形成四个流通池，完成后自动开始运行缓冲液的待机流（异步）')
cap('chip.eject', obj({}), OPERATION, {}, ['sensor_chip_port', 'ifc_block'], 'Eject Chip：排空流通池并退出芯片，芯片口盖打开（异步）')
cap('chip.remove', obj({}), obj(dict(chip_id=TEXT)), {}, ['sensor_chip_port'], '从打开的芯片口取出芯片（存放；之后可复用）', 'agent', 'robot_skill')
cap('flow.select_buffer', obj(dict(tube=enum(['buffer_a', 'buffer_b', 'buffer_c', 'buffer_d']))), obj(dict(tube=TEXT)), dict(tube='buffer_a'), ['buffer_valve'],
    '软件切换缓冲液选择阀（A–D；默认A；换缓冲液后应Prime）')
cap('flow.prime', obj({}), OPERATION, {}, ['syringe_pumps', 'buffer_valve', 'degasser', 'peristaltic_pump', 'ifc_block'],
    'Tools→Prime：用所选缓冲液冲洗全部流路（约6–7 min，异步）')
cap('flow.standby', obj({}), obj(dict(standby=BOOL)), {}, ['syringe_pumps', 'ifc_block'], 'Tools→Standby：缓冲液管A的低速待机流（最长7天，约65 mL/24 h）')
cap('flow.stop_standby', obj({}), obj(dict(standby=BOOL)), {}, ['syringe_pumps', 'ifc_block'], 'Tools→Stop Standby：停止待机流')
cap('detector.normalize', obj(dict(position=POS)), OPERATION, dict(position='R2A2'), ['detection_unit', 'ifc_block'],
    'Normalize：在用于运行的芯片上注入BIAnormalizing（70 %甘油）校正各流通池的检测响应（约8 min，异步；不能用Maintenance芯片）')
cap('maintenance.run', obj(dict(procedure=enum(MAINT), positions=dict(type='object', propertyNames=enum(LIB_SOLUTIONS), additionalProperties=POS, maxProperties=6)),
                           optional=('positions',)),
    OPERATION, dict(procedure='desorb', positions=dict(biadesorb_1='R2A1', biadesorb_2='R2A2')), ['syringe_pumps', 'ifc_block', 'autosampler_needle'],
    '维护工具（More Tools）：Desorb（BIAdesorb 1、2，每周）、Desorb and Sanitize（另加消毒液与HBS，每月）、Superclean（约1.5 h）、Wash/Empty Buffer Tubing、Flow System Wash；'
    'BIAdesorb 1在20 °C以下析出（用0.5 %十二烷基硫酸锂代替）；会损坏芯片上的配体（应使用Maintenance芯片，异步）')
cap('system.check', obj(dict(position=POS)), OPERATION, dict(position='R2A1'), ['cabinet', 'ifc_block', 'syringe_pumps'],
    'System Check：用BIAtest（14.9 %蔗糖）与HBS-N在新CM5芯片上检查泵、混合、折射计、注射、噪声与缓冲液选择阀（约1 h，异步；结果表）')
cap('system.shutdown', obj({}), OPERATION, {}, ['ifc_block', 'syringe_pumps', 'peristaltic_pump'],
    'Shutdown：先Desorb and Sanitize，取出托盘；管A接蒸馏水、管B接70 %乙醇，冲洗并排空IFC（约20 min，异步）；完成后松开蠕动泵夹')
cap('rack.eject', obj({}), obj(dict(state=TEXT, auto_retract_s=number('s'))), {}, ['rack_tray_carriage', 'rack_tray_port'],
    'Eject Rack：托盘车从托盘口弹出（自动运行中不可；预设时间后自动收回）')
cap('rack.retract', obj({}), obj(dict(state=TEXT)), {}, ['rack_tray_carriage', 'rack_tray_port'], '在对话框点OK：托盘立即送回仪器（架子装反时送不进）')
cap('run.manual_start', obj(dict(run_id=SID, flow_rate_m3_s=FLOW, flow_path=enum(FLOW_PATHS), reference_subtraction=enum(REF_SUBTRACTIONS), data_rate_hz=enum([1, 10]) | INT,
                                 ignore_temperature=BOOL), optional=('reference_subtraction', 'data_rate_hz', 'ignore_temperature')),
    obj(dict(run_id=TEXT, cycle=INT)), dict(run_id='manual_1', flow_rate_m3_s=10 * UL_MIN, flow_path='4'), ['cabinet', 'ifc_block'],
    'Run→Manual Run：以设定流速、流路与参比扣除开始手动运行（数据持续记录；先Prime、Normalize并等温度稳定）')
cap('run.command', obj(dict(command=COMMAND)), obj(dict(queued=INT, position_in_queue=INT)),
    dict(command=dict(command='sample_injection', position='R1A1', contact_s=60.0)), ['ifc_block', 'autosampler_needle', 'syringe_pumps'],
    '手动运行命令（忙时排队）：流速、流路（周期内只能在起始范围内改）、样品注入（位置、接触时间）、再生注入（可选高黏度）、等待、新周期、弹出托盘（插到当前命令之后）、报告点')
cap('run.queue_delete', obj(dict(index=integer(1, 200))), obj(dict(remaining=INT)), dict(index=1), ['cabinet'], '删除队列中尚未开始的命令（序号从1起）')
cap('run.stop_command', obj({}), obj(dict(stopped=nullable(TEXT))), {}, ['cabinet'], 'Stop <command>：停止正在执行的命令（弹出托盘不能停）')
cap('run.pause', obj({}), obj(dict(state=TEXT)), {}, ['cabinet'], 'Pause Run：暂停命令执行（缓冲液继续流过，数据继续记录）')
cap('run.resume', obj({}), obj(dict(state=TEXT)), {}, ['cabinet'], 'Resume Run：继续')
cap('run.stop', obj({}), obj(dict(state=TEXT)), {}, ['cabinet', 'lamp_run'], 'Stop Run：结束当前运行（数据保留；仪器转入待机流）')
cap('wizard.immobilization', IMMOB, OPERATION, IMMOB_EXAMPLE, ['ifc_block', 'autosampler_needle', 'syringe_pumps'],
    '固定化向导：每个流通池一个周期——胺偶联（EDC/NHS混合活化7 min、配体按接触时间或“目标固定量”脉冲、乙醇胺封闭7 min；报告Response bound与final）、'
    'His捕获（NiCl2后注入His标签配体）或生物素捕获（SA芯片）；也可空白固定化（异步）')
cap('wizard.ph_scouting', SCOUT, OPERATION, dict(run_id='ph_scout', fc=2, ligand_positions=['R2B1', 'R2B2', 'R2B3', 'R2B4'], contact_s=120.0, flow_rate_m3_s=10 * UL_MIN,
                                                 wash_position='R2A1'),
    ['ifc_block', 'autosampler_needle'], '固定化pH筛选向导：在未活化表面依次注入不同pH乙酸钠中的配体（推荐120 s、5或10 µL/min），每次后用50 mM NaOH洗表面（异步）')
cap('method.run', METHOD, OPERATION, METHOD_EXAMPLE, ['ifc_block', 'autosampler_needle', 'syringe_pumps', 'lamp_run'],
    'Method Builder方法运行：通用设置（数据采集1或10 Hz、检测Single/Dual/Multi、样品舱温度）、系统准备（Prime、Normalize）、按周期执行的样品/捕获/再生注入与等待（接触时间、解离时间、流速、'
    '稳定时间）、关键词（样品名、浓度、分子量）、启动周期、空白、溶剂校正周期与单循环动力学（同一周期多次注入）；温度不稳定时先等待（异步）')
cap('runs.list', obj({}), obj(dict(runs=array(obj(dict(run_id=TEXT, kind=TEXT, state=TEXT, cycles=INT, started_s=number('s'), finished_s=nullable(number('s')))), 0, MAX_RUNS))),
    {}, ['cabinet'], '列出运行（结果文件）', 'agent', 'query')
cap('data.sensorgram', obj(dict(run_id=SID, cycle=integer(1, MAX_CYCLES), curves=array(enum(['1', '2', '3', '4', '2-1', '3-1', '4-1', '4-3']), 1, 8), max_points=integer(10, MAX_POINTS)),
                           optional=('max_points',)),
    obj(dict(run_id=TEXT, cycle=INT, time_s=array(number('s'), 0, MAX_POINTS), curves=dict(type='object', additionalProperties=array(nullable(number('RU')), 0, MAX_POINTS)),
             injections=array(obj(dict(kind=TEXT, position=TEXT, start_s=number('s'), end_s=number('s'), flow_rate_m3_s=number('m3/s'))), 0, 32),
             keywords=dict(type='object'), data_rate_hz=INT)),
    dict(run_id='kin_b2m', cycle=2, curves=['4-3']), ['cabinet'], '读一个周期的传感图（绝对响应或参比扣除；时间从周期开始计）、注入时刻与关键词', 'agent', 'query')
cap('data.report_points', obj(dict(run_id=SID)), obj(dict(rows=array(RP_ROW, 0, 4096))), dict(run_id='kin_b2m'), ['cabinet'],
    '读报告点表：每次注入前的基线、注入结束前的binding、结束后的stability（绝对与相对响应）', 'agent', 'query')
cap('eval.kinetics', KIN_IN, KIN_OUT, dict(run_id='kin_b2m', cycles=[3, 4, 5, 6, 7], subtraction='4-3', blank_cycles=[2], fit_mass_transfer=True), ['cabinet'],
    '动力学评估（1:1结合含传质）：全局ka、kd、Rmax、tc，局部RI；给出KD、Chi²、SE与T值、U值与质量控制（常数范围、唯一性、体折射率、曲线曲率、残差）', 'agent', 'query')
cap('eval.affinity', obj(dict(run_id=SID, cycles=array(integer(1, MAX_CYCLES), 2, 64), subtraction=enum(['2-1', '3-1', '4-1', '4-3', '1', '2', '3', '4']),
                              blank_cycles=array(integer(1, MAX_CYCLES), 0, 16), concentrations_mol_m3=array(CONC, 0, 64)), optional=('blank_cycles', 'concentrations_mol_m3')),
    AFF_OUT, dict(run_id='kin_b2m', cycles=[3, 4, 5, 6, 7], subtraction='4-3'), ['cabinet'],
    '稳态亲和评估：注入结束前的平衡响应Req对浓度拟合1:1（KD、Rmax、偏移、Chi²；KD应小于最高浓度的一半）', 'agent', 'query')
cap('eval.concentration', CONC_IN, CONC_OUT, dict(run_id='conc_1', mode='calibration', subtraction='2-1', standard_cycles=[2, 3, 4, 5], standard_concentrations_mol_m3=[1e-6, 2e-6, 4e-6, 8e-6],
                                                 sample_cycles=[6]), ['cabinet'],
    '浓度分析：标准曲线（四参数拟合后内插）或免标定浓度分析CFCA（两个流速下的初始结合速率与传质系数）', 'agent', 'query')
cap('eval.solvent_correction', SC_IN, SC_OUT, dict(run_id='lmw', correction_cycles=[2, 3, 4, 5], sample_cycles=[6, 7], active_fc=2, reference_fc=1), ['cabinet'],
    '溶剂校正：由一组DMSO浓度的校正周期得到（活性−参比）对参比相对响应的二次曲线，校正样品的报告点（曲线Chi²应<2 RU）', 'agent', 'query')
cap('eval.thermodynamics', obj(dict(entries=array(THERMO_ROW, 3, 32))), THERMO_OUT,
    dict(entries=[dict(temperature_k=288.15, KD_mol_m3=1e-6), dict(temperature_k=298.15, KD_mol_m3=2e-6), dict(temperature_k=308.15, KD_mol_m3=4e-6)]), ['cabinet'],
    '热力学评估：van \'t Hoff（KD对1/T，≥4个温度时含ΔCp）与过渡态Eyring分析（ka、kd）', 'agent', 'query')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS_LOG), dropped=INT)), dict(since_s=0.0), ['cabinet'], '读事件日志',
    'agent', 'query')
cap('provenance.read', obj({}), obj(dict(libraries=array(obj(dict(name=TEXT, file=TEXT, entries=INT)), 0, 8), species=array(TEXT, 0, 400), solutions=array(TEXT, 0, 80),
                                         chips=array(TEXT, 0, 16))),
    {}, ['cabinet'], '读模板库来源、库中物质名、可配制的缓冲液/试剂与芯片类型（不含动力学真值）', 'agent', 'query')
cap('surface.inspect', obj({}), obj(dict(chip=nullable(TEXT), flow_cells=array(obj(dict(fc=INT, ligands=array(dict(type='object'), 0, 8), complexes=dict(type='object'),
                                                                                         active_esters=number('1'), excluded_volume=number('1'), refractive_index=number('1'),
                                                                                         gain=number('1'))), 0, 4), contamination=number('1'), system_buffer=nullable(TEXT))),
    {}, ['detection_unit'], '评测者读表面真值：各流通池的配体（物质、固定量、活性）、复合物响应、活性酯、排除体积、流通池中液体的折射率与检测增益', 'evaluator', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=LEDGER)), {}, ['cabinet'], '读守恒账目（缓冲液/水/样品用量、废液与溢出、固定的配体、注入与周期数、电能）', 'evaluator', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT, 0, 16))), dict(fault='needle_clogged', active=True), ['cabinet'],
    '评测者故障注入：断电、系统错误（红灯）、IFC泄漏、针堵塞、脱气机故障、注射泵泄漏', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 8 * 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=60.0), ['cabinet'], '推进宿主时钟（0.1 s物理网格；空闲时10 s块）',
    'evaluator', 'simulation')
UNSUPPORTED = {
    'sample.recovery': '回收结合在表面的分析物（厂家1.5 µL回收体积）不在本运行层模型内',
    'gxp.audit_trail': 'GxP软件包（审计追踪、电子签名）是选配，不在所选配置内',
    'lipid.capture': 'L1/HPA芯片的脂质体与单层脂捕获不在本运行层模型内',
}
for k, v in UNSUPPORTED.items():
    cap(k, obj({}), obj({}), {}, ['cabinet'], v)


# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference)


_PN = lambda u: nullable(number(u))                                             # noqa: E731
CHIP_STATES = ['none', 'port_open', 'inserted', 'docking', 'docked', 'ejecting', 'ejected']
FLOW_STATES = ['stopped', 'standby', 'running', 'prime', 'normalize', 'maintenance', 'docking', 'ejecting', 'dry']
RUN_STATES = ['idle', 'waiting_temperature', 'preparing', 'running', 'paused', 'completed', 'stopped', 'failed']
RACK_STATES = ['inside', 'ejecting', 'ejected', 'retracting', 'stopped']        # stopped: the tray motor lost power between the end positions
FCMAP = obj({str(i): _PN('RU') for i in range(1, 5)})
slot('clock.time_s', number('s'), 's', 'derived', 'cabinet', '仿真时钟（0.1 s物理网格；空闲时10 s块）')
slot('plant.configured', BOOL, '1', 'setpoint', 'cabinet', '已声明实验室条件')
slot('power.enabled', BOOL, '1', 'derived', 'power_switch', '处理单元通电（开关开且供电正常）')
slot('system.connected', BOOL, '1', 'derived', 'cabinet', '控制软件已与仪器建立连接（开机后约30 s）')
for lamp, zh in (('ready', '绿色ready灯（通电后亮）'), ('system', '红色system灯（开机后亮几秒；其它时候亮表示系统错误）'),
                 ('temperature', '黄色temperature灯（流通池温度稳定在设定值时常亮，不稳定时闪烁）'), ('sensor_chip', '绿色sensor chip灯（芯片对接就绪时常亮，插入未对接时闪烁）'),
                 ('run', '绿色run灯（运行中亮）')):
    slot(f'lamp.{lamp}', BOOL, '1', 'derived', f'lamp_{lamp}', zh + '；按0.5 s闪烁相位给出瞬时亮灭（场景灯镜像）')
slot('temperature.analysis_setpoint_k', number('K'), 'K', 'setpoint', 'detection_unit', '分析（流通池）温度设定')
slot('temperature.analysis_k', _PN('K'), 'K', 'sensor', 'detection_unit', '流通池温度读数（控制软件状态栏；未通电为空）')
slot('temperature.analysis_stable', BOOL, '1', 'derived', 'detection_unit', '分析温度稳定在设定值（±0.01 K，运行可以开始）')
slot('temperature.sample_setpoint_k', number('K'), 'K', 'setpoint', 'sample_compartment_box', '样品舱温度设定')
slot('temperature.sample_k', _PN('K'), 'K', 'sensor', 'sample_compartment_box', '样品舱温度读数（未通电为空）')
slot('chip.state', enum(CHIP_STATES), '1', 'derived', 'sensor_chip_port', '芯片状态（无/芯片口开/已插入/对接中/已对接/退出中/已退出在口中）')
slot('chip.id', nullable(TEXT), '1', 'derived', 'sensor_chip_port', '芯片口中或已对接芯片的Chip id')
slot('chip.type', nullable(TEXT), '1', 'derived', 'sensor_chip_port', '芯片类型（CM5、CM4、CM3、C1、SA、NTA、L1、HPA、MAINTENANCE）')
slot('chip.normalized', BOOL, '1', 'derived', 'detection_unit', '对接后已做Normalize')
slot('chip.port_cover_angle_rad', number('rad'), 'rad', 'derived', 'chip_port_cover', '芯片口盖开角（0关，1.2 rad开；场景关节镜像）', reference='关闭位置')
slot('response.ru', FCMAP, 'RU', 'sensor', 'detection_unit', '四个流通池的当前绝对响应（RU；未对接或超量程为空）')
slot('flow.state', enum(FLOW_STATES), '1', 'derived', 'syringe_pumps', '流路状态（停/待机/运行/Prime/Normalize/维护/对接/退出/干燥）')
slot('flow.rate_m3_s', number('m3/s'), 'm3/s', 'derived', 'syringe_pumps', '当前流过流通池的流速（液体实际体积流量）')
slot('flow.path', nullable(enum(FLOW_PATHS)), '1', 'derived', 'ifc_block', '当前流路（运行中）')
slot('flow.selected_tube', enum(['buffer_a', 'buffer_b', 'buffer_c', 'buffer_d']), '1', 'setpoint', 'buffer_valve', '缓冲液选择阀位置')
slot('flow.prime_recommended', BOOL, '1', 'derived', 'syringe_pumps', '所选瓶的缓冲液与流路中的不同（应Prime）')
slot('standby.elapsed_s', number('s'), 's', 'derived', 'syringe_pumps', '本次待机已持续时间（最长7天）')
slot('bottle.volumes_m3', obj({b: _PN('m3') for b in BOTTLE_SLOTS}), 'm3', 'sensor', 'buffer_tray', '各瓶液体体积的目视刻度读数（1 L瓶按25 mL、250 mL瓶按10 mL、水瓶按25 mL、废液瓶按50 mL取整；无瓶为空；准确值在truth.bottle_volumes_m3）')
slot('bottle.tubes_inserted', obj({b: BOOL for b in ['buffer_a', 'buffer_b', 'buffer_c', 'buffer_d', 'water']}), '1', 'derived', 'buffer_tray', '各管是否插在瓶中')
slot('bottle.solutions', obj({b: nullable(TEXT) for b in BOTTLE_SLOTS}), '1', 'derived', 'buffer_tray', '各瓶装的溶液（agent放入的记录）')
slot('door.left_pump_angle_rad', number('rad'), 'rad', 'derived', 'left_pump_door', '左泵舱门开角（0关；场景关节镜像）', reference='关闭位置')
slot('door.right_pump_angle_rad', number('rad'), 'rad', 'derived', 'right_pump_door', '右泵舱门开角（0关；场景关节镜像）', reference='关闭位置')
slot('clamp.closed', BOOL, '1', 'derived', 'peristaltic_clamp_lever', '蠕动泵管夹夹紧（手柄竖直）')
slot('clamp.lever_angle_rad', number('rad'), 'rad', 'derived', 'peristaltic_clamp_lever', '管夹手柄转角（0竖直夹紧，1.4 rad扳到右侧松开；场景关节镜像）', reference='竖直')
slot('sample_compartment.door_angle_rad', number('rad'), 'rad', 'derived', 'sample_compartment_door', '样品舱门上翻角（0关；场景关节镜像）', reference='关闭位置')
slot('sample_compartment.light', BOOL, '1', 'setpoint', 'inspection_window', '样品舱照明')
slot('rack.state', enum(RACK_STATES), '1', 'derived', 'rack_tray_carriage', '托盘车状态（在内/弹出中/已弹出/送入中/停在中途：移动中断电）')
slot('rack.carriage_position_m', number('m'), 'm', 'derived', 'rack_tray_carriage', '托盘车伸出距离（0在内，0.14 m弹出；场景关节镜像）', reference='在内位置')
slot('rack.auto_retract_in_s', _PN('s'), 's', 'derived', 'rack_tray_carriage', '弹出后距自动收回的时间（不在弹出状态为空）')
slot('rack.config', nullable(obj(dict(config=TEXT, plate=TEXT, correctly_mounted=BOOL))), '1', 'derived', 'rack_tray', '托盘配置（agent装上的记录）')
slot('rack.positions', dict(type='object', additionalProperties=obj(dict(solution_id=TEXT, placed_m3=number('m3'), capped=BOOL))), '1', 'derived', 'rack_tray',
     '托盘上各位置放的溶液与放入体积（agent的记录；软件不监测剩余体积）')
slot('run.state', enum(RUN_STATES), '1', 'derived', 'cabinet', '运行状态')
slot('run.id', nullable(TEXT), '1', 'derived', 'cabinet', '当前/最近一次运行（结果文件）')
slot('run.kind', nullable(enum(['manual', 'method', 'immobilization', 'ph_scouting'])), '1', 'derived', 'cabinet', '运行类型')
slot('run.cycle', INT, '1', 'derived', 'cabinet', '当前周期序号')
slot('run.current_command', nullable(TEXT), '1', 'derived', 'cabinet', '正在执行的命令（手动运行/方法）')
slot('run.queue_length', INT, '1', 'derived', 'cabinet', '待执行命令数（手动运行队列）')
slot('maintenance.due', array(TEXT, 0, 4), '1', 'derived', 'cabinet', '维护提醒（Desorb每周、Desorb and Sanitize每月；维护计划器）')
slot('system_check.last', nullable(obj(dict(time_s=number('s'), passed=BOOL, failed_tests=array(TEXT, 0, 24)))), '1', 'derived', 'cabinet', '最近一次System Check结果')
slot('operations.running', array(TEXT, 0, 8), '1', 'derived', 'cabinet', '进行中的异步操作类型')
slot('operation.started_by_other_agent', BOOL, '1', 'derived', 'cabinet', '进行中的操作或运行是否由另一个agent启动')
slot('warnings.active', array(TEXT, 0, 24), '1', 'derived', 'cabinet', '当前警告（AMBIENT_OUT_OF_RANGE、PRIME_RECOMMENDED、WATER_OLD、WASTE_NEARLY_FULL、CLAMP_OPEN、STANDBY_EXPIRED等）')
slot('errors.active', array(TEXT, 0, 16), '1', 'derived', 'cabinet', '当前错误（BUFFER_EMPTY、SYSTEM_ERROR、WASTE_OVERFLOW等）')
slot('solutions.available', array(TEXT, 0, MAX_SOLUTIONS * 2), '1', 'derived', 'sample_compartment_box', '实验台上可取用的溶液与储备液')
slot('runs.count', INT, '1', 'derived', 'cabinet', '运行（结果文件）数')
slot('truth.analysis_temperature_k', _PN('K'), 'K', 'process_truth', 'detection_unit', '流通池温度真值', 'evaluator')
slot('truth.sample_temperature_k', _PN('K'), 'K', 'process_truth', 'sample_compartment_box', '样品舱温度真值', 'evaluator')
slot('truth.flow_cell_refractive_index', obj({str(i): number('1') for i in range(1, 5)}), '1', 'process_truth', 'ifc_block', '各流通池中液体的折射率真值', 'evaluator')
slot('truth.flow_cell_mass_ru', obj({str(i): number('RU') for i in range(1, 5)}), 'RU', 'process_truth', 'detection_unit', '各流通池表面质量响应真值（配体+复合物+预富集+活化）', 'evaluator')
slot('truth.complexes_ru', dict(type='object', additionalProperties=number('RU')), 'RU', 'process_truth', 'detection_unit', '各流通池各复合物的响应真值（键 fc:配体:分析物）', 'evaluator')
slot('truth.ligand_activity', dict(type='object', additionalProperties=number('1')), '1', 'process_truth', 'detection_unit', '各流通池配体活性真值（键 fc:配体）', 'evaluator')
slot('truth.active_esters', obj({str(i): number('1') for i in range(1, 5)}), '1', 'process_truth', 'detection_unit', '各流通池活性酯分数真值', 'evaluator')
slot('truth.system_buffer', nullable(TEXT), '1', 'process_truth', 'syringe_pumps', '流路中实际的运行缓冲液（瓶内溶液ID；换瓶后渐变）', 'evaluator')
slot('truth.contamination', number('1'), '1', 'process_truth', 'ifc_block', '流路污染指数（吸附物与盐沉积；0–1）', 'evaluator')
slot('truth.bottle_volumes_m3', obj({b: number('m3') for b in BOTTLE_SLOTS}), 'm3', 'process_truth', 'buffer_tray', '各瓶液体体积真值', 'evaluator')
slot('truth.rack_volumes_m3', dict(type='object', additionalProperties=number('m3')), 'm3', 'process_truth', 'rack_tray', '托盘各位置剩余液体体积真值（蒸发、取样）', 'evaluator')
slot('truth.faults', array(TEXT, 0, 16), '1', 'process_truth', 'cabinet', '注入的故障', 'evaluator')


# ---------------------------------------------------------------- liquid properties (per composition) used by the flow-cell physics
REGEN_TOL = ENG['regeneration']
PC = ENG['preconcentration']
CPL = ENG['coupling']
ACT = ENG['activation']
CAPT = ENG['capture']
LIQ = ENG['liquids']
TEMPC = ENG['temperature_control']
TEMPC_NOISE = ENG['temperature_noise_K']
DRIFT = ENG['docking_drift']
SERIAL_DV = ENG['serial_delay_volume_m3']
RACK_TRAVEL_M = 0.14
COVER_OPEN_RAD = 1.2
DOOR_OPEN_RAD = 1.6
CLAMP_OPEN_RAD = 1.4
SC_DOOR_OPEN_RAD = 1.75


def _ph_of(comp):
    return comp.get('pH')


def liquid_props(comp, mw=None):
    """Optical, transport and chemical properties of a composition (used for the running buffer and for every sample plug)."""
    dn, eta_rel, I = solution_optics(comp)
    sp = {k: v for k, v in comp.get('species', {}).items() if v > 0}
    for sid, c in sp.items():                              # macromolecule / analyte contribution to the bulk refractive index (dn/dc 0.185 mL/g; assumption)
        dn += ASSUMED['protein']['dn_per_mass_pct'] * c * (mw or SHELF_MW)(sid) * 1e-4
    sol = comp.get('solutes', {})
    base = comp.get('base')
    s = REGEN_S.get(base, 0.0) if base else 0.0
    if not s:                                              # mixtures: strongest regeneration component present at >= 30 % of its library concentration
        for lib_id, strength in REGEN_S.items():
            ref = SOLUTIONS_LIB[lib_id].get('components', {})
            if ref and all(sol.get(k, 0.0) >= 0.3 * v * 1000.0 for k, v in ref.items()):
                s = max(s, strength)
    ph = _ph_of(comp)
    return dict(dn=dn, eta_rel=eta_rel, I=I, pH=ph if ph is not None else 7.0, s=s, species=sp, edc=sol.get('edc', 0.0), nhs=sol.get('nhs', 0.0),
                ethanolamine=sol.get('ethanolamine_hcl', 0.0), ni=sol.get('nickel_chloride', 0.0), edta=sol.get('disodium_ethylenediamine_tetraacetate_edta_sodium', 0.0),
                glycerol=comp.get('mass_pct', {}).get('glycerol', 0.0), sucrose=comp.get('mass_pct', {}).get('sucrose', 0.0), base=base)


def SHELF_MW(sid):
    """Molecular weight of a library species (Da); evaluator-defined species live on the runtime instance (CytivaBiacoret200Runtime._mw)."""
    return SHELF.get(sid, {}).get('mw_da', 50000.0)


def _mix_comp(parts):
    """Mix compositions [(comp, volume_m3)] into one (concentrations volume-weighted; pH of the most concentrated buffer-like part)."""
    V = sum(v for _, v in parts)
    out = dict(solutes={}, mass_pct={}, v_v_pct={}, species={}, pH=None, base=None)
    if V <= 0:
        return out
    best = (-1.0, None, None)
    for c, v in parts:
        f = v / V
        for key in ('solutes', 'mass_pct', 'v_v_pct', 'species'):
            for k, x in c.get(key, {}).items():
                out[key][k] = out[key].get(k, 0.0) + x * f
        strength = v * (sum(c.get('solutes', {}).values()) + 1.0)
        if c.get('pH') is not None and strength > best[0]:
            best = (strength, c.get('pH'), c.get('base'))
    out['pH'] = best[1]
    big = max(parts, key=lambda q: q[1])
    out['base'] = big[0].get('base') if big[1] >= 0.5 * V else None
    return out


def _temp_advance(T, Tset, dt, k, r):
    """Rate-limited first-order approach (exact for any dt): linear at rate r until |error| = r / k, then exponential with rate k."""
    e = Tset - T
    band = r / k
    if abs(e) > band:
        t_lin = (abs(e) - band) / r
        if dt <= t_lin:
            return T + math.copysign(r * dt, e)
        T = Tset - math.copysign(band, e)
        dt -= t_lin
        e = Tset - T
    return Tset - e * math.exp(-k * dt)


def _serial_delays(path, flow):
    fcs = [int(x) for x in path.split('-')]
    out, acc = {}, 0.0
    for i, fc in enumerate(fcs):
        if i:
            acc += SERIAL_DV[fc - 1] / max(flow, 1e-15)
        out[fc] = acc
    return out


def _plug_fraction(t, t0, t1, tau):
    def S(u):
        return 0.0 if u <= 0 else 1.0 - math.exp(-u / tau)
    return max(0.0, S(t - t0) - S(t - t1))


def _new_fc(fc, chip_type, seed, chip_id):
    cap_rel = chips()[chip_type].get('capacity_relative') or (1.0 if chip_type in ('SA', 'NTA') else 0.0)
    base = ENG['chip_raw_baseline_RU'][chip_type]
    return dict(fc=fc, ligands=[], complexes={}, esters=0.0, act_ru=0.0, deact_ru=0.0, preconc={}, ni=0.0, cap_rel=cap_rel,
                R0=base + 150.0 * _gauss(_h(chip_id), fc, 7) if base else 0.0, gain_raw=1.0 + ENG['unnormalized_gain_sigma'] * _gauss(_h(chip_id), fc, 11),
                sa_ru=ENG['capture']['sa_streptavidin_RU'] if chip_type == 'SA' else 0.0, used=False)


class CytivaBiacoret200Runtime:
    def __init__(self, instance_id='biacore_t200_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.tick = 0
        self.plant = None
        self.seed = 0
        self.switch_on = False
        self.power = False
        self.t_on = None
        self.T_room = 295.15
        self.rh = 0.45
        self.T_a = self.T_sc = 295.15
        self.T_a_set = self.T_sc_set = 298.15
        self.T_noise = 0.0
        self.chip = dict(state='none', id=None, type=None, cover=0.0, cover_target=0.0, t_dock=None)
        self.chip_store = {}
        self.chip_history = []
        self.flow = dict(state='stopped', rate=0.0, path=None, tube='buffer_a', standby_t0=None, sys_comp=None, sys_id=None, target_id=None, target_comp=None,
                         mix=1.0, dry=True)
        self.bottles = {b: None for b in BOTTLE_SLOTS}
        self.water_t = None
        self.doors = dict(left_pump=0.0, right_pump=0.0)
        self.door_targets = dict(left_pump=0.0, right_pump=0.0)
        self.clamp_closed = True
        self.clamp_angle = 0.0
        self.clamp_target = 0.0
        self.sc_door = 0.0
        self.sc_door_target = 0.0
        self.sc_light = False
        self.salt_deposits = 0.0
        self.rack = dict(state='inside', pos=0.0, t_out=None, config=None, positions={})
        self.solutions = {}
        self.species = {k: dict(v) for k, v in SHELF.items()}
        self.pairs = {k: dict(v) for k, v in LIB_PAIRS.items()}
        self.runs = {}
        self.run = None
        self.plugs = []
        self.pending_waste = 0.0
        self.empty_tube = None
        self.spikes = []
        self.operations = {}
        self.warnings = {}
        self.errors = {}
        self.faults = set()
        self.contamination = 0.0
        self.maint_last = dict(desorb=0.0, desorb_and_sanitize=0.0)
        self.syscheck = None
        self.led = {k: _Sum() for k in LEDGER_KEYS}
        self.events, self.events_dropped = [], 0
        self.revision = 0
        self.seq = dict(op=0, run=0)
        self._principals = {}
        self._requests = {}
        self._owners = {}
        self._caller = None
        self._acc = {}
        self._last_reading = {}
        self.auto_retract_s = ENG['rack_auto_retract_s']

    def _mw(self, sid):
        """Molecular weight (Da) of a species of THIS instance (library shelf or evaluator species.define); never shared between instances."""
        sp = self.species.get(sid)
        return sp['mw_da'] if sp and sp.get('mw_da') else SHELF_MW(sid)

    # ---------------------------------------------------------------- sessions
    @property
    def time_s(self):
        return self.t

    def register_principal(self, name, visibility):
        if visibility not in ('agent', 'evaluator') or name in self._principals:
            raise ActionRejected('INVALID_ARGUMENT')
        self._principals[name] = visibility

    def session(self, name):
        if name not in self._principals:
            raise ActionRejected('ACCESS_DENIED')
        return CytivaBiacoret200Session(self, name)

    def _event(self, kind, code, message, ref=None, t=None):
        if len(self.events) >= MAX_EVENTS_LOG:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.t if t is None else t, kind=kind, code=code, message_zh=message, _ref=ref))

    def _error(self, code, message):
        if code not in self.errors:
            self.errors[code] = message
            self._event('error', code, message)

    def _own(self, object_id):
        if self._caller is not None:
            self._owners[object_id] = self._caller

    def _need_plant(self):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')

    def _need_power(self):
        self._need_plant()
        if not self.power:
            raise ActionRejected('POWER_OFF')

    def _connected(self):
        return self.power and self.t_on is not None and self.t - self.t_on >= DOC['software_connect_s'] - 1e-9

    def _need_connected(self):
        self._need_power()
        if not self._connected():
            raise ActionRejected('NOT_CONNECTED')

    def _mains_ok(self):
        v = self.plant['mains_voltage_v']
        return MAINS[0] * 0.9 <= v <= MAINS[1] * 1.1 and 'power_loss' not in self.faults

    def _busy(self):
        return [op for op in self._running_ops() if op['kind'] in ('chip.dock', 'chip.eject', 'flow.prime', 'detector.normalize', 'maintenance.run', 'system.check',
                                                                    'system.shutdown')]

    def _need_not_busy(self, allow_manual=False):
        if 'system_error' in self.faults:
            raise ActionRejected('SYSTEM_ERROR')
        if self.run is not None and not (allow_manual and self.run['kind'] == 'manual'):
            raise ActionRejected('RUN_IN_PROGRESS')
        if self._busy():
            raise ActionRejected('INSTRUMENT_BUSY')

    # ---------------------------------------------------------------- operations
    def _new_op(self, kind, duration=None, **extra):
        self.seq['op'] += 1
        oid = f'op{self.seq["op"]}'
        self.operations[oid] = dict(op_id=oid, kind=kind, phase='running', t0=self.t, t_end=None if duration is None else self.t + duration, error=None, result={}, **extra)
        self._own(oid)
        return self.operations[oid]

    def _running_ops(self, *kinds):
        return [op for op in self.operations.values() if op['phase'] == 'running' and (not kinds or op['kind'] in kinds)]

    def _finish_op(self, op, phase, code=None):
        op['phase'] = phase
        op['error'] = code
        op['t1'] = self.t

    def _cancel_op(self, op):
        if op['kind'] in ('method.run', 'wizard.immobilization', 'wizard.ph_scouting'):
            if self.run is not None and self.run.get('op_id') == op['op_id']:
                self._end_run('stopped', None)
        elif op['kind'] in ('flow.prime', 'detector.normalize', 'maintenance.run', 'system.check', 'system.shutdown'):
            self._end_flow_op(op, cancelled=True)
        if op['phase'] == 'running':
            self._finish_op(op, 'cancelled')

    # ---------------------------------------------------------------- plant and power
    def _cmd_plant_configure(self, room_k, mains_voltage_v, noise_seed, relative_humidity=0.45, rack_auto_retract_s=None):
        if self.plant is not None and noise_seed != self.plant['noise_seed']:
            raise ActionRejected('SEED_FIXED')
        first = self.plant is None
        self.plant = dict(room_k=float(room_k), mains_voltage_v=float(mains_voltage_v), noise_seed=int(noise_seed), relative_humidity=float(relative_humidity),
                          rack_auto_retract_s=float(rack_auto_retract_s if rack_auto_retract_s is not None else self.auto_retract_s))
        self.seed = int(noise_seed)
        self.T_room = float(room_k)
        self.rh = float(relative_humidity)
        self.auto_retract_s = self.plant['rack_auto_retract_s']
        if first:
            self.T_a = self.T_sc = self.T_room
        self._update_power()
        self._warnings()
        return dict(plant=dict(self.plant))

    def _update_power(self):
        on = bool(self.switch_on and self.plant is not None and self._mains_ok())
        if on and not self.power:
            self.power = True
            self.t_on = self.t
            self._event('power', 'POWER_ON', '开机：全部指示灯亮几秒后ready灯亮；控制软件约30 s后连接')
            if self.chip['state'] == 'docked':
                self.flow['state'] = 'stopped'
        elif not on and self.power:
            self._power_lost()

    def _power_lost(self):
        self.power = False
        self.t_on = None
        self._event('power', 'POWER_OFF', '断电：流动、温控与运行停止（流路中液体静止）')
        if self.run is not None:
            self._end_run('failed', 'POWER_OFF_INTERRUPT')
        for op in self._running_ops():
            if op['kind'] in ('chip.dock', 'chip.eject'):
                self._finish_op(op, 'failed', 'POWER_OFF_INTERRUPT')
                if op['kind'] == 'chip.dock':
                    self.chip['state'] = 'inserted'
            elif op['kind'] in ('flow.prime', 'detector.normalize', 'maintenance.run', 'system.check', 'system.shutdown'):
                self._end_flow_op(op, cancelled=True)
                self._finish_op(op, 'failed', 'POWER_OFF_INTERRUPT')
        self._drop_plugs(self.plugs)
        if self.flow['state'] not in ('dry',):
            self.flow['state'] = 'stopped'
        self.flow['rate'] = 0.0
        self.flow['standby_t0'] = None
        if self.rack['state'] in ('ejecting', 'retracting'):           # the tray motor stops where it is (rack.eject / rack.retract move it on again)
            self.rack['state'], self.rack['t_out'] = 'stopped', None
        if self.chip['cover_target'] > self.chip['cover']:             # the cover is opened by its motor only (closing is a light press by hand)
            self.chip['cover_target'] = self.chip['cover']
        self._last_reading = {str(i): None for i in range(1, 5)}      # no detector signal without power (not only from the next grid point)

    def _cmd_power_set(self, enabled):
        self._need_plant()
        if enabled and not self.switch_on and not self._mains_ok():
            raise ActionRejected('MAINS_OUT_OF_RANGE')
        if enabled and self.sc_door > 1e-6:
            raise ActionRejected('SAMPLE_COMPARTMENT_OPEN')
        self.switch_on = bool(enabled)
        self._update_power()
        return dict(enabled=self.power)

    def _cmd_power_press_switch(self):
        return self._cmd_power_set(not self.switch_on)

    def _cmd_temperature_set(self, analysis_k=None, sample_compartment_k=None):
        self._need_connected()
        if self.run is not None:
            raise ActionRejected('RUN_IN_PROGRESS')
        if analysis_k is None and sample_compartment_k is None:
            raise ActionRejected('INVALID_ARGUMENT')
        if analysis_k is not None and analysis_k < self.T_room - DOC['analysis_below_ambient_max_K'] - 1e-9:
            raise ActionRejected('BELOW_AMBIENT_LIMIT')
        if sample_compartment_k is not None and sample_compartment_k < self.T_room - DOC['sample_below_ambient_max_K'] - 1e-9:
            raise ActionRejected('BELOW_AMBIENT_LIMIT')
        if analysis_k is not None:
            self.T_a_set = float(analysis_k)
        if sample_compartment_k is not None:
            self.T_sc_set = float(sample_compartment_k)
        self._event('temperature', 'TEMPERATURE_SET', f'分析温度{self.T_a_set - 273.15:.2f} °C，样品舱{self.T_sc_set - 273.15:.2f} °C')
        return dict(analysis_setpoint_k=self.T_a_set, sample_compartment_setpoint_k=self.T_sc_set)

    def _stable(self):
        return self.power and abs(self.T_a - self.T_a_set) < TEMPC['stable_band_K']

    # ---------------------------------------------------------------- bench: kits, stocks, species, interactions, solutions
    def _bench_add(self, sid, comp, V, label, kind):
        if sid in self.solutions:
            raise ActionRejected('ITEM_EXISTS')
        if len(self.solutions) >= MAX_SOLUTIONS:
            raise ActionRejected('TOO_MANY_ITEMS')
        self.solutions[sid] = dict(comp=comp, V=float(V), label=label, kind=kind, t=self.t)
        self._own('solution:' + sid)

    def _cmd_kit_provide(self, kit):
        self._need_plant()
        items = []

        def add(sid, base, V, species=None, label=None):
            comp = library_composition(base)
            if species:
                comp['species'] = dict(species)
            if sid in self.solutions:
                return
            self._bench_add(sid, comp, V, [dict(species_id=k, concentration_mol_m3=v) for k, v in (label or species or {}).items()], 'kit')
            items.append(sid)
        if kit == 'getting_started':
            ab, b2m = GS_LIGAND, GS_ANALYTE
            ab_c = 1000.0 / self._mw(ab)                          # 1 mg/mL = 1 kg/m3 stock (derived: 6 uL + 194 uL -> 30 ug/mL)
            b2m_c = 32e-6 * (600.26 / 2.26)                       # 8.5 uM stock (derived: 2.26 uL + 598 uL -> 32 nM)
            add('gs_hbs_ep_plus_10x', 'hbs_ep_plus', 50e-6)
            add('gs_ligand_anti_b2m', 'hbs_ep_plus', 50e-9, {ab: ab_c})
            add('gs_analyte_b2m', 'hbs_ep_plus', 50e-9, {b2m: b2m_c})
            add('gs_acetate_5_0', 'acetate_5_0', 2e-6)
            add('gs_glycine_2_5', 'glycine_2_5', 2e-6)
            add('gs_bianormalizing', 'bianormalizing', 1.5e-6)
        elif kit == 'amine_coupling':
            add('edc_0_4m', 'edc_0_4m', 1.0e-3 * 0.75)
            add('nhs_0_1m', 'nhs_0_1m', 1.0e-3 * 0.75)
            add('ethanolamine_1m', 'ethanolamine_1m', 1.0e-3 * 1.0)
        elif kit == 'maintenance':
            add('biadesorb_1', 'biadesorb_1', 190e-6)
            add('biadesorb_2', 'biadesorb_2', 190e-6)
            add('biatest', 'biatest', 65e-6)
            add('bianormalizing', 'bianormalizing', 90e-6)
            add('hbs_n_10x', 'hbs_n', 50e-6)
        else:
            add('nickel_0_5mm', 'nickel_0_5mm', 2e-6)
            add('edta_350mm', 'edta_350mm', 2e-6)
        self._event('solution', 'KIT', f'试剂盒{kit}：' + '、'.join(items))
        return dict(items=items)

    def _cmd_stock_provide(self, stock_id, volume_m3, base, species, label=None):
        self._need_plant()
        for s in species + (label or []):
            if s['species_id'] not in self.species:
                raise ActionRejected('UNKNOWN_SPECIES')
        comp = library_composition(base)
        comp['species'] = {s['species_id']: float(s['concentration_mol_m3']) for s in species}
        self._bench_add(stock_id, comp, volume_m3, [dict(x) for x in (label if label is not None else species)], 'stock')
        self._event('solution', 'STOCK', f'储备液{stock_id}')
        return dict(stock_id=stock_id, volume_m3=float(volume_m3))

    def _cmd_species_define(self, species_id, name, mw_da, isoelectric_point=7.0, tags=(), kind='protein', diffusion_m2_s=None):
        self._need_plant()
        if species_id in self.species:
            raise ActionRejected('ITEM_EXISTS')
        D = float(diffusion_m2_s) if diffusion_m2_s is not None else diffusion_estimate(mw_da, f=1.0 if kind == 'small_molecule' else 1.2)
        self.species[species_id] = dict(name=name, mw_da=float(mw_da), D_m2_s=D, pI=float(isoelectric_point), tags=list(tags), kind=kind, evidence='evaluator', interaction=None)
        return dict(species_id=species_id, diffusion_m2_s=D)

    def _cmd_interaction_define(self, interaction_id, ligand, analyte, ka_m3_mol_s, kd_s, temperature_k=298.15, activation_enthalpy_association_j_mol=25000.0,
                                activation_enthalpy_dissociation_j_mol=70000.0, sites=1, ligand_activity=0.8, regeneration_requirement=None):
        self._need_plant()
        if interaction_id in self.pairs:
            raise ActionRejected('ITEM_EXISTS')
        if ligand not in self.species or analyte not in self.species or ligand == analyte:
            raise ActionRejected('UNKNOWN_SPECIES')
        if self._pair(ligand, analyte) is not None:
            raise ActionRejected('ITEM_EXISTS')
        self.pairs[interaction_id] = dict(a=ligand, b=analyte, ka=float(ka_m3_mol_s), kd=float(kd_s), T0=float(temperature_k), dHa=float(activation_enthalpy_association_j_mol),
                                          dHd=float(activation_enthalpy_dissociation_j_mol), sites=int(sites), activity=float(ligand_activity), evidence='evaluator', library=False,
                                          s_req=float(regeneration_requirement if regeneration_requirement is not None else REGEN_TOL['complex_tolerance_default']))
        return dict(interaction_id=interaction_id)

    def _pair(self, ligand, analyte):
        for p in self.pairs.values():
            if (p['a'], p['b']) in ((ligand, analyte), (analyte, ligand)):
                return p
        return None

    def _source(self, sid):
        s = self.solutions.get(sid)
        if s is None:
            raise ActionRejected('UNKNOWN_SOLUTION')
        return s

    def _cmd_solution_prepare(self, solution_id, volume_m3, base, sources=(), dmso_volume_fraction=0.0):
        dmso_pct = 100.0 * float(dmso_volume_fraction)                 # internal compositions keep % v/v
        self._need_plant()
        if solution_id in self.solutions:
            raise ActionRejected('ITEM_EXISTS')
        tot = 0.0
        for s in sources:
            src = self._source(s['source_id'])
            if src['V'] < s['volume_m3'] - 1e-15:
                raise ActionRejected('INSUFFICIENT_SOLUTION')
            tot += s['volume_m3']
        if tot > volume_m3 * (1.0 + 1e-9) or (dmso_pct / 100.0) * volume_m3 + tot > volume_m3 * (1.0 + 1e-9):
            raise ActionRejected('VOLUME_OUT_OF_RANGE')
        parts, label = [], {}
        for i, s in enumerate(sources):
            src = self.solutions[s['source_id']]
            err = 1.0 + 0.01 * _gauss(self.seed ^ _h(solution_id), i, 3)               # pipetting error sigma 1 % (assumption)
            v_true = s['volume_m3'] * err
            parts.append((src['comp'], v_true))
            src['V'] = max(0.0, src['V'] - s['volume_m3'])
            self.led['stock_used_m3'].add(s['volume_m3'])
            for l_ in src['label']:
                label[l_['species_id']] = label.get(l_['species_id'], 0.0) + l_['concentration_mol_m3'] * s['volume_m3'] / volume_m3
        diluent = volume_m3 - tot - dmso_pct / 100.0 * volume_m3
        parts.append((library_composition(base), max(diluent, 0.0) + (sum(v for _, v in parts) - tot) * 0.0))
        comp = _mix_comp(parts)
        if dmso_pct:
            comp['v_v_pct']['dmso'] = comp['v_v_pct'].get('dmso', 0.0) + float(dmso_pct) * (1.0 + 0.003 * _gauss(self.seed ^ _h(solution_id), 9, 1))
        if not sources:
            comp['base'] = base
        else:
            comp['base'] = base if diluent >= 0.5 * volume_m3 else comp['base']
            if diluent >= 0.5 * volume_m3:
                comp['pH'] = SOLUTIONS_LIB[base].get('pH')
        self._bench_add(solution_id, comp, volume_m3, [dict(species_id=k, concentration_mol_m3=v) for k, v in label.items()], 'prepared')
        self.led['solution_prepared_m3'].add(volume_m3)
        return dict(solution_id=solution_id, volume_m3=float(volume_m3), label=[dict(species_id=k, concentration_mol_m3=v) for k, v in label.items()])

    # ---------------------------------------------------------------- rack tray and positions
    def _layout(self):
        c = self.rack['config']
        return rack_layout(c['config'], c['plate'] if c['plate'] != 'none' else None) if c else {}

    def _need_rack_out(self):
        if self.rack['state'] != 'ejected':
            raise ActionRejected('RACK_NOT_EJECTED')

    def _cmd_rack_configure(self, config, plate, correctly_mounted=True):
        self._need_plant()
        self._need_rack_out()
        if (config in ('sample_and_reagent_rack', 'tray_type2_only', 'empty')) != (plate == 'none'):
            raise ActionRejected('INVALID_ARGUMENT')
        for pos in list(self.rack['positions']):
            self._discard_position(pos)
        if config == 'empty':                                       # plates and racks taken off the tray (Shutdown: "remove the rack tray contents")
            self.rack['config'] = None
            return dict(config='empty', positions=0)
        self.rack['config'] = dict(config=config, plate=plate, correctly_mounted=bool(correctly_mounted))
        return dict(config=config, positions=len(self._layout()))

    def _discard_position(self, pos):
        p = self.rack['positions'].pop(pos)
        return p['V']

    def _cmd_rack_place(self, position, solution_id, volume_m3, capped=True):
        self._need_plant()
        self._need_rack_out()
        lay = self._layout()
        if position not in lay:
            raise ActionRejected('UNKNOWN_POSITION')
        if position in self.rack['positions']:
            raise ActionRejected('POSITION_OCCUPIED')
        if volume_m3 > lay[position] + 1e-15:
            raise ActionRejected('VOLUME_OUT_OF_RANGE')
        src = self._source(solution_id)
        if src['V'] < volume_m3 - 1e-15:
            raise ActionRejected('INSUFFICIENT_SOLUTION')
        src['V'] = max(0.0, src['V'] - volume_m3)
        self.rack['positions'][position] = dict(solution_id=solution_id, comp=copy.deepcopy(src['comp']), V=float(volume_m3), placed=float(volume_m3), capped=bool(capped),
                                                t=self.t, label=copy.deepcopy(src['label']))
        self._own('position:' + position)
        return dict(position=position, solution_id=solution_id, volume_m3=float(volume_m3))

    def _cmd_rack_remove(self, position):
        self._need_plant()
        self._need_rack_out()
        if position not in self.rack['positions']:
            raise ActionRejected('POSITION_EMPTY')
        return dict(position=position, removed_m3=float(self._discard_position(position)))

    def _cmd_rack_eject(self):
        self._need_connected()
        if self.run is not None:
            raise ActionRejected('RUN_IN_PROGRESS')
        if self.sc_door > 1e-6:
            raise ActionRejected('SAMPLE_COMPARTMENT_OPEN')
        if self.rack['state'] in ('ejecting', 'ejected', 'retracting'):
            raise ActionRejected('RACK_NOT_INSIDE')
        self.rack['state'] = 'ejecting'
        self._event('rack', 'RACK_EJECT', '托盘弹出')
        return dict(state='ejecting', auto_retract_s=float(self.auto_retract_s))

    def _cmd_rack_retract(self):
        self._need_connected()
        if self.rack['state'] not in ('ejected', 'stopped'):
            raise ActionRejected('RACK_NOT_EJECTED')
        if self.rack['config'] is not None and not self.rack['config']['correctly_mounted']:
            raise ActionRejected('RACK_NOT_SEATED')
        self.rack['state'] = 'retracting'
        return dict(state='retracting')

    # ---------------------------------------------------------------- bottles, doors, clamp, sample compartment
    def _cmd_bottle_place(self, slot, solution_id, volume_m3, tube_inserted=True):
        self._need_plant()
        if volume_m3 > BOTTLE_CAP[slot] + 1e-12:
            raise ActionRejected('VOLUME_OUT_OF_RANGE')
        src = self._source(solution_id)
        if src['V'] < volume_m3 - 1e-15:
            raise ActionRejected('INSUFFICIENT_SOLUTION')
        if self.run is not None and slot == self.flow['tube'] and self.run['kind'] != 'manual':
            raise ActionRejected('RUN_IN_PROGRESS')
        src['V'] = max(0.0, src['V'] - volume_m3)
        self.bottles[slot] = dict(solution_id=solution_id, comp=copy.deepcopy(src['comp']), V=float(volume_m3), tube=bool(tube_inserted))
        if slot == self.empty_tube and tube_inserted and volume_m3 > 0:
            self.errors.pop('BUFFER_EMPTY', None)                     # the emptied tube has buffer again
            self.empty_tube = None
        if slot == 'water':
            self.water_t = self.t
        self._own('bottle:' + slot)
        return dict(slot=slot, volume_m3=float(volume_m3))

    def _cmd_bottle_remove(self, slot):
        self._need_plant()
        b = self.bottles.get(slot)
        if b is None:
            raise ActionRejected('NO_BOTTLE')
        if self.run is not None and slot in (self.flow['tube'], 'water', 'waste'):
            raise ActionRejected('RUN_IN_PROGRESS')
        self.bottles[slot] = None
        return dict(slot=slot, removed_m3=float(b['V']))

    def _cmd_bottle_empty_waste(self):
        self._need_plant()
        if self.run is not None and self.run['kind'] != 'manual':
            raise ActionRejected('RUN_IN_PROGRESS')
        b = self.bottles.get('waste')
        v = b['V'] if b else 0.0
        self.bottles['waste'] = dict(solution_id=None, comp=None, V=0.0, tube=True)
        self.errors.pop('WASTE_OVERFLOW', None)
        return dict(emptied_m3=float(v))

    def _cmd_door_set(self, door, open):
        self._need_plant()
        if not open and door == 'right_pump' and abs(self.clamp_angle - self.clamp_target) > 1e-6:
            raise ActionRejected('CLAMP_MOVING')
        self.door_targets[door] = DOOR_OPEN_RAD if open else 0.0
        return dict(door=door, open=bool(open))

    def _cmd_clamp_set(self, closed):
        self._need_plant()
        if self.doors['right_pump'] < 0.8 or self.door_targets['right_pump'] < 0.8:
            raise ActionRejected('DOOR_CLOSED')
        self.clamp_closed = bool(closed)
        self.clamp_target = 0.0 if closed else CLAMP_OPEN_RAD
        self.warnings.pop('CLAMP_RELEASE_REQUIRED', None) if not closed else None
        return dict(closed=self.clamp_closed)

    def _cmd_sample_compartment_open_door(self):
        self._need_plant()
        if self.power:
            raise ActionRejected('POWER_ON')
        if self.rack['state'] != 'inside':
            raise ActionRejected('RACK_NOT_INSIDE')
        if self.rack['positions'] or self.rack['config'] is not None:
            raise ActionRejected('RACK_NOT_EMPTY')
        if self.sc_door_target > 0:
            raise ActionRejected('ALREADY_OPEN')
        self.sc_door_target = SC_DOOR_OPEN_RAD
        return dict(open=True)

    def _cmd_sample_compartment_close_door(self):
        self._need_plant()
        if self.sc_door_target <= 0:
            raise ActionRejected('ALREADY_CLOSED')
        self.sc_door_target = 0.0
        return dict(open=False)

    def _cmd_sample_compartment_clean(self):
        self._need_plant()
        if self.sc_door < 1.0:
            raise ActionRejected('DOOR_CLOSED')
        self.salt_deposits = 0.0
        self.faults.discard('needle_clogged')
        return dict(cleaned=True)

    def _cmd_sample_compartment_set_light(self, on):
        self._need_connected()
        self.sc_light = bool(on)
        return dict(on=self.sc_light)

    # ---------------------------------------------------------------- sensor chip
    def _docked(self):
        return self.chip['state'] == 'docked'

    def _chip_obj(self):
        return self.chip_store.get(self.chip['id']) if self.chip['id'] else None

    def _cmd_chip_open_port(self):
        self._need_connected()
        if self.chip['state'] in ('docked', 'docking'):
            raise ActionRejected('CHIP_DOCKED')
        if self._busy():
            raise ActionRejected('INSTRUMENT_BUSY')
        if self.chip['state'] == 'none':
            self.chip['state'] = 'port_open'
        self.chip['cover_target'] = COVER_OPEN_RAD
        return dict(port_open=True)

    def _cmd_chip_insert(self, chip_type, chip_id, reuse=False):
        self._need_plant()
        if self.chip['state'] != 'port_open' or self.chip['cover'] < COVER_OPEN_RAD - 1e-6:
            raise ActionRejected('PORT_CLOSED' if self.chip['state'] in ('none', 'port_open') else 'CHIP_PRESENT')
        if reuse:
            c = self.chip_store.get(chip_id)
            if c is None:
                raise ActionRejected('CHIP_UNKNOWN')
            if c['type'] != chip_type:
                raise ActionRejected('CHIP_TYPE_MISMATCH')
        else:
            if chip_id in self.chip_store:
                raise ActionRejected('CHIP_ID_EXISTS')
            self.chip_store[chip_id] = dict(id=chip_id, type=chip_type, fcs=[_new_fc(i, chip_type, self.seed, chip_id) for i in range(1, 5)], docks=0, used=False,
                                            normalized=False, gains=None)
        self.chip.update(state='inserted', id=chip_id, type=chip_type)
        self._event('chip', 'CHIP_INSERTED', f'插入芯片{chip_id}（{chip_type}）')
        return dict(chip_id=chip_id, chip_type=chip_type)

    def _cmd_chip_close_cover(self):
        self._need_plant()
        if self.chip['cover_target'] <= 0:
            raise ActionRejected('PORT_CLOSED')
        if self.chip['state'] == 'ejected':
            raise ActionRejected('CHIP_PRESENT')
        self.chip['cover_target'] = 0.0
        if self.chip['state'] == 'port_open':
            self.chip['state'] = 'none'
        return dict(port_open=False)

    def _cmd_chip_dock(self):
        self._need_connected()
        if self.chip['state'] != 'inserted':
            raise ActionRejected('NO_CHIP')
        if self.chip['cover'] > 1e-6 or self.chip['cover_target'] > 0:
            raise ActionRejected('COVER_OPEN')
        self._need_not_busy()
        self.chip['state'] = 'docking'
        self.flow['state'] = 'docking'
        op = self._new_op('chip.dock', ENG['docking_s'])
        return dict(operation_id=op['op_id'])

    def _cmd_chip_eject(self):
        self._need_connected()
        if not self._docked():
            raise ActionRejected('CHIP_NOT_DOCKED')
        self._need_not_busy()
        self.chip['state'] = 'ejecting'
        self.flow.update(state='ejecting', standby_t0=None)
        op = self._new_op('chip.eject', ENG['eject_s'])
        return dict(operation_id=op['op_id'])

    def _cmd_chip_remove(self):
        self._need_plant()
        if self.chip['state'] != 'ejected' or self.chip['cover'] < COVER_OPEN_RAD - 1e-6:
            raise ActionRejected('NO_CHIP')
        cid = self.chip['id']
        self.chip.update(state='port_open', id=None, type=None, t_dock=None)
        return dict(chip_id=cid)

    def _finish_dock(self, op):
        c = self._chip_obj()
        c['docks'] += 1
        c['normalized'] = False
        c['gains'] = [f['gain_raw'] * (1.0 + 0.004 * _gauss(self.seed ^ _h(c['id']), c['docks'], f['fc'])) for f in c['fcs']]
        c['drift'] = [DRIFT['steady_RU_per_s'] * (2.0 * _uni(self.seed ^ _h(c['id']), c['docks'], 20 + f['fc']) - 1.0) for f in c['fcs']]
        c['drift_A0'] = DRIFT['initial_RU_per_s'] if c['docks'] == 1 else 0.2 * DRIFT['initial_RU_per_s']
        self.chip.update(state='docked', t_dock=self.t)
        if self.chip['id'] not in self.chip_history:
            self.chip_history.append(self.chip['id'])
        self.led['chips_docked'].add(1)
        self._finish_op(op, 'succeeded')
        self._event('chip', 'CHIP_DOCKED', f'芯片{c["id"]}已对接；开始待机流')
        self._start_standby()

    def _finish_eject(self, op):
        self.chip.update(state='ejected', cover_target=COVER_OPEN_RAD, t_dock=None)
        self.flow.update(state='dry', rate=0.0, standby_t0=None)
        self._drop_plugs(self.plugs)
        self._finish_op(op, 'succeeded')
        self._event('chip', 'CHIP_EJECTED', '流通池已排空，芯片退出，芯片口打开')

    # ---------------------------------------------------------------- flow system
    def _tube_bottle(self, tube):
        b = self.bottles.get(tube)
        return b if (b is not None and b['tube'] and b['V'] > 0) else None

    def _start_standby(self):
        if not self._docked() or not self.power:
            return
        b = self._tube_bottle('buffer_a')
        if b is None:
            self.flow.update(state='stopped', rate=0.0, standby_t0=None)
            self._event('warning', 'NO_STANDBY_BUFFER', '管A没有缓冲液：不能开始待机流')
            return
        if self.flow['sys_comp'] is None:
            self.flow.update(sys_comp=copy.deepcopy(b['comp']), sys_id=b['solution_id'], mix=1.0)
        self.flow.update(state='standby', rate=LIQ['standby_flow_m3_s'], path='1-2-3-4', standby_t0=self.t, dry=False)

    def _cmd_flow_select_buffer(self, tube):
        self._need_connected()
        if self.run is not None and self.run['kind'] != 'manual':
            raise ActionRejected('RUN_IN_PROGRESS')
        self.flow['tube'] = tube
        return dict(tube=tube)

    def _need_liquids(self, tube):
        if self._tube_bottle(tube) is None:
            raise ActionRejected('NO_BUFFER')
        w = self.bottles.get('water')
        if w is None or not w['tube'] or w['V'] <= 0:
            raise ActionRejected('NO_WATER')
        if self.bottles.get('waste') is None:
            raise ActionRejected('NO_WASTE_BOTTLE')

    def _cmd_flow_prime(self):
        self._need_connected()
        if not self._docked():
            raise ActionRejected('CHIP_NOT_DOCKED')
        self._need_not_busy()
        self._need_liquids(self.flow['tube'])
        op = self._new_op('flow.prime', sum(DOC['prime_s']) / 2.0, tube=self.flow['tube'])
        self.flow.update(state='prime', rate=LIQ['prime_flow_m3_s'], path='1-2-3-4', standby_t0=None)
        self._event('flow', 'PRIME', 'Prime：冲洗流路（约6.5 min）')
        return dict(operation_id=op['op_id'])

    def _cmd_flow_standby(self):
        self._need_connected()
        if not self._docked():
            raise ActionRejected('CHIP_NOT_DOCKED')
        self._need_not_busy()
        if self._tube_bottle('buffer_a') is None:
            raise ActionRejected('NO_BUFFER')
        self._start_standby()
        return dict(standby=self.flow['state'] == 'standby')

    def _cmd_flow_stop_standby(self):
        self._need_connected()
        if self.flow['state'] != 'standby':
            raise ActionRejected('NOT_IN_STANDBY')
        self.flow.update(state='stopped', rate=0.0, standby_t0=None)
        return dict(standby=False)

    def _position(self, pos):
        p = self.rack['positions'].get(pos)
        if p is None or self.rack['state'] != 'inside':
            raise ActionRejected('POSITION_EMPTY' if self.rack['state'] == 'inside' else 'RACK_NOT_INSIDE')
        return p

    def _cmd_detector_normalize(self, position):
        self._need_connected()
        if not self._docked():
            raise ActionRejected('CHIP_NOT_DOCKED')
        if self.chip['type'] == 'MAINTENANCE':
            raise ActionRejected('MAINTENANCE_CHIP')
        self._need_not_busy()
        p = self._position(position)
        if p['comp'].get('base') != 'bianormalizing':
            raise ActionRejected('WRONG_SOLUTION')
        if p['V'] < 100e-9:
            raise ActionRejected('INSUFFICIENT_SOLUTION')
        self._need_liquids(self.flow['tube'])
        op = self._new_op('detector.normalize', DOC['normalize_s'], position=position)
        self.flow.update(state='normalize', rate=20 * UL_MIN, path='1-2-3-4', standby_t0=None)
        for k in range(2):
            self._add_plug(self.t + 60.0 + 200.0 * k, 45.0, p, 'normalize', '1-2-3-4', 20 * UL_MIN, op_id=op['op_id'])
        return dict(operation_id=op['op_id'])

    MAINT_NEED = {'desorb': ['biadesorb_1|lds_0_5', 'biadesorb_2'], 'desorb_and_sanitize': ['biadesorb_1|lds_0_5', 'biadesorb_2', 'hypochlorite_1', 'hbs_n|hbs_ep_plus'],
                  'superclean': ['acetic_acid_1', 'bicarbonate_0_2m', 'guanidine_6m|dmso_50', 'hcl_10mm'], 'wash_buffer_tubing': ['biadesorb_1|lds_0_5', 'biadesorb_2'],
                  'empty_buffer_tubing': ['ethanol_70', 'biadesorb_2'], 'flow_system_wash': []}

    def _cmd_maintenance_run(self, procedure, positions=None):
        self._need_connected()
        if not self._docked():
            raise ActionRejected('CHIP_NOT_DOCKED')
        self._need_not_busy()
        positions = dict(positions or {})
        use = []
        for need in self.MAINT_NEED[procedure]:
            opts = need.split('|')
            got = [o for o in opts if o in positions]
            if not got:
                raise ActionRejected('MISSING_SOLUTION')
            sol = got[0]
            p = self._position(positions[sol])
            if p['comp'].get('base') != sol:
                raise ActionRejected('WRONG_SOLUTION')
            if p['V'] < 250e-9:
                raise ActionRejected('INSUFFICIENT_SOLUTION')
            if sol == 'biadesorb_1' and min(self.T_a_set, self.T_sc_set) < 293.15 - 1e-9:
                raise ActionRejected('DESORB_BELOW_20C')
            use.append((sol, positions[sol]))
        self._need_liquids(self.flow['tube'])
        dur = MAINT_S[procedure]
        op = self._new_op('maintenance.run', dur, procedure=procedure, assay_chip=self.chip['type'] != 'MAINTENANCE' and self._chip_obj()['used'])
        self.flow.update(state='maintenance', rate=LIQ['maintenance_flow_m3_s'], path='1-2-3-4', standby_t0=None)
        for i, (sol, pos) in enumerate(use):
            self._add_plug(self.t + (i + 0.5) * dur / max(1, len(use)) - 60.0, 120.0, self.rack['positions'][pos], 'maintenance', '1-2-3-4', LIQ['maintenance_flow_m3_s'],
                           op_id=op['op_id'])
        self._event('maintenance', 'MAINTENANCE', f'{procedure}开始（{dur / 60:.0f} min）')
        return dict(operation_id=op['op_id'])

    def _cmd_system_check(self, position):
        self._need_connected()
        if not self._docked():
            raise ActionRejected('CHIP_NOT_DOCKED')
        self._need_not_busy()
        p = self._position(position)
        if p['comp'].get('base') != 'biatest':
            raise ActionRejected('WRONG_SOLUTION')
        if p['V'] < 250e-9:
            raise ActionRejected('INSUFFICIENT_SOLUTION')
        self._need_liquids('buffer_a')
        op = self._new_op('system.check', MAINT_S['system_check'], position=position)
        self.flow.update(state='maintenance', rate=30 * UL_MIN, path='1-2-3-4', standby_t0=None)
        for k in range(4):
            self._add_plug(self.t + 300.0 + 600.0 * k, 60.0, p, 'system_check', '1-2-3-4', 30 * UL_MIN, op_id=op['op_id'])
        return dict(operation_id=op['op_id'])

    def _cmd_system_shutdown(self):
        self._need_connected()
        if not self._docked():
            raise ActionRejected('CHIP_NOT_DOCKED')
        self._need_not_busy()
        if self.rack['state'] != 'ejected' or self.rack['config'] is not None:
            raise ActionRejected('RACK_NOT_REMOVED')
        a, b = self._tube_bottle('buffer_a'), self._tube_bottle('buffer_b')
        if a is None or b is None:
            raise ActionRejected('NO_BUFFER')
        if a['comp'].get('base') != 'water' or b['comp'].get('base') != 'ethanol_70':
            raise ActionRejected('WRONG_SOLUTION')
        op = self._new_op('system.shutdown', DOC['shutdown_s'])
        self.flow.update(state='maintenance', rate=LIQ['maintenance_flow_m3_s'], path='1-2-3-4', standby_t0=None)
        return dict(operation_id=op['op_id'])

    def _drop_plugs(self, plugs):
        """Plugs removed before they passed (power loss, eject, cancelled operation): their liquid is flushed to the waste on the next grid point."""
        gone = list(plugs)
        self.pending_waste += sum(p.get('waste_m3', 0.0) for p in gone)
        self.plugs = [p for p in self.plugs if all(p is not g for g in gone)]

    def _end_flow_op(self, op, cancelled=False):
        self._drop_plugs([p for p in self.plugs if p.get('op_id') == op['op_id']])
        if op['kind'] == 'flow.prime' and not cancelled:
            b = self._tube_bottle(op['tube'])
            if b is not None:
                self.flow.update(sys_comp=copy.deepcopy(b['comp']), sys_id=b['solution_id'], mix=1.0, target_id=None, target_comp=None)
                self.errors.pop('BUFFER_EMPTY', None)                 # primed from a tube with buffer
                self.empty_tube = None
            self.contamination *= 0.9
        if op['kind'] == 'detector.normalize' and not cancelled:
            c = self._chip_obj()
            c['normalized'] = True
            c['gains'] = [1.0 + 0.0005 * _gauss(self.seed ^ _h(c['id']), c['docks'] + 100, f['fc']) for f in c['fcs']]
        if op['kind'] == 'maintenance.run' and not cancelled:
            pr = op['procedure']
            f = ENG['contamination']
            self.contamination *= {'desorb': f['desorb_factor'], 'desorb_and_sanitize': f['sanitize_factor'], 'superclean': 0.0}.get(pr, 0.7)
            if pr in self.maint_last:
                self.maint_last[pr] = self.t
            if pr == 'desorb_and_sanitize':
                self.maint_last['desorb'] = self.t
            self.led['maintenance_runs'].add(1)
        if op['kind'] == 'system.check' and not cancelled:
            self.syscheck = dict(time_s=self.t, **self._system_check_results())
            op['result'] = dict(self.syscheck)
        if op['kind'] == 'system.shutdown' and not cancelled:
            self.flow.update(state='dry', rate=0.0, standby_t0=None, sys_comp=None, sys_id=None, dry=True)
            self.warnings['CLAMP_RELEASE_REQUIRED'] = '关机完成：打开右泵舱门并松开蠕动泵管夹'
            return
        if self.power and self._docked():
            self._start_standby()
        elif self.flow['state'] not in ('dry',):
            self.flow.update(state='stopped', rate=0.0)

    def _system_check_results(self):
        fails = []
        c = self._chip_obj()
        if not self.clamp_closed:
            fails.append('reagent_pump')
        w = self.bottles.get('water')
        if w is None or not w['tube'] or w['V'] <= 0:
            fails.append('water')
        if self._tube_bottle('buffer_a') is None:
            fails.append('buffer')
        if self.faults & {'ifc_leak', 'syringe_pump_leak'}:
            fails += ['mix_1', 'mix_2', 'injections_fc1', 'injections_fc2', 'injections_fc3', 'injections_fc4', 'dual_injections']
        if c['used'] or c['type'] != 'CM5' or c['docks'] > 1:
            fails += ['refractometer_fc1', 'refractometer_fc2', 'refractometer_fc3', 'refractometer_fc4', 'noise']
        elif (self.flow['sys_comp'] or {}).get('base') not in ('hbs_n', 'hbs_ep_plus'):
            fails += ['refractometer_fc1', 'refractometer_fc2', 'refractometer_fc3', 'refractometer_fc4']
        if not self._stable() or self.contamination > 0.5:
            fails.append('noise')
        for t in ('buffer_b', 'buffer_c', 'buffer_d'):
            if self._tube_bottle(t) is None:
                fails.append('buffer_selector_' + t[-1])
        return dict(passed=not fails, failed_tests=sorted(set(fails)))

    # ---------------------------------------------------------------- sample plugs (liquid delivery through the flow path)
    def _add_plug(self, t0, contact_s, p, kind, path, flow, op_id=None, cycle=None):
        """Aspirate from a rack position and schedule a plug entering the flow path at t0; a position with less than the injected volume plus its
        dead volume delivers a shorter plug followed by air (spike)."""
        need = flow * contact_s
        loss = LIQ['needle_loss_m3']
        dead = _dead_volume(p.get('pos', 'R1A1'))
        usable = max(0.0, p['V'] - dead - loss)
        air = usable < need - 1e-15 or 'needle_clogged' in self.faults or not self.clamp_closed
        delivered = min(need, usable)
        if 'needle_clogged' in self.faults:
            delivered *= 0.2
        contact_eff = delivered / flow if flow > 0 else 0.0
        conc = 1.0
        if p.get('placed') and p['V'] > 0:
            conc = 1.0
        comp = copy.deepcopy(p['comp'])
        taken = min(p['V'], delivered + loss)
        p['V'] = max(0.0, p['V'] - taken)
        self.led['sample_aspirated_m3'].add(taken)
        self.led['sample_injected_m3'].add(delivered)
        plug = dict(t0=t0, t1=t0 + contact_eff, comp=comp, props=liquid_props(comp, self._mw), path=path, flow=flow, kind=kind, op_id=op_id, cycle=cycle, air=air,
                    contact_nominal=contact_s, conc=conc, waste_m3=taken)   # aspirated volume: ends in the waste bottle when the plug has passed
        self.plugs.append(plug)
        if air:
            self.spikes.append(dict(t0=t0 + contact_eff, t1=t0 + contact_eff + ENG['air_spike']['duration_s'], path=path))
            self._event('injection', 'AIR_IN_INJECTION', '注入中带入空气（样品体积不足、针堵塞或蠕动泵管夹未夹紧）')
        return plug

    def _sys_props(self):
        if self.flow['sys_comp'] is None:
            return liquid_props(dict(solutes={}, base='water', pH=7.0), self._mw)
        key = id(self.flow['sys_comp']), self.flow.get('target_id'), round(self.flow['mix'], 6)
        if getattr(self, '_sys_cache_key', None) != key:
            a = liquid_props(self.flow['sys_comp'], self._mw)
            if self.flow.get('target_comp') is not None and self.flow['mix'] < 1.0:
                b = liquid_props(self.flow['target_comp'], self._mw)
                w = 1.0 - self.flow['mix']
                a = self._blend(a, b, w)
            self._sys_cache, self._sys_cache_key = a, key
        return self._sys_cache

    @staticmethod
    def _blend(a, b, w):
        out = dict(a)
        for k in ('dn', 'eta_rel', 'I', 's', 'edc', 'nhs', 'ethanolamine', 'ni', 'edta', 'glycerol', 'sucrose'):
            out[k] = a[k] + w * (b[k] - a[k])
        sp = dict(a['species'])
        for k in set(a['species']) | set(b['species']):
            sp[k] = a['species'].get(k, 0.0) + w * (b['species'].get(k, 0.0) - a['species'].get(k, 0.0))
        out['species'] = {k: v for k, v in sp.items() if v > 0}
        if w > 0.5:
            out['pH'], out['base'] = b['pH'], b['base']
        return out

    def _fc_liquid(self, fc, tm):
        L = self._sys_props()
        for pl in self.plugs:
            fcs = pl['path'].split('-')
            if str(fc) not in fcs:
                continue
            delay = _serial_delays(pl['path'], pl['flow'])[fc]
            tau = max(0.05, ENG['dispersion_volume_m3'] / max(pl['flow'], 1e-15))
            x = _plug_fraction(tm - delay, pl['t0'], pl['t1'], tau)
            if x > 1e-9:
                L = self._blend(L, pl['props'], x)
                if x > 0.5:
                    L['pH'], L['base'] = pl['props']['pH'], pl['props']['base']
        return L

    # ---------------------------------------------------------------- runs: manual, method, wizards
    def _new_run(self, run_id, kind, data_rate, detection, path, ref, ignore_temperature, owner_op=None):
        if run_id in self.runs:
            raise ActionRejected('ITEM_EXISTS')
        if len(self.runs) >= MAX_RUNS:
            raise ActionRejected('TOO_MANY_ITEMS')
        fcs = [int(x) for x in path.split('-')]
        if detection == 'multi':
            det = [1, 2, 3, 4]
        elif detection == 'dual':
            det = [1, 2] if fcs[0] in (1, 2) else [3, 4]
            if any(f not in det for f in fcs):
                raise ActionRejected('DETECTION_MISMATCH')
        else:
            det = fcs if detection == 'path' else fcs[:1]
            if detection == 'single' and len(fcs) > 1:
                raise ActionRejected('DETECTION_MISMATCH')
        run = dict(run_id=run_id, kind=kind, state='preparing', t0=self.t, t1=None, data_rate=int(data_rate), detection=detection, det=det, flow_path=path, ref=ref,
                   cycles=[], steps=[], queue=[], cur=None, op_id=owner_op, ignore_temperature=bool(ignore_temperature), paused=False, results={}, marks={},
                   flow_rate=None, chip_id=self.chip['id'], chip_type=self.chip['type'], temperature_k=self.T_a_set, error=None)
        self.runs[run_id] = run
        self._own('run:' + run_id)
        return run

    def _check_run_start(self, path, ref):
        self._need_connected()
        if not self._docked():
            raise ActionRejected('CHIP_NOT_DOCKED')
        self._need_not_busy()
        if self.rack['state'] != 'inside':
            raise ActionRejected('RACK_NOT_INSIDE')
        self._need_liquids(self.flow['tube'])
        w = self.bottles.get('waste')
        if w is not None and w['V'] >= 0.95 * BOTTLE_CAP['waste']:
            raise ActionRejected('WASTE_FULL')
        if ref != 'none':
            act, refc = (int(x) for x in ref.split('-'))
            fcs = [int(x) for x in path.split('-')]
            if act not in fcs or refc not in fcs:
                raise ActionRejected('REFERENCE_NOT_IN_PATH')

    def _open_cycle(self, run, purpose, path, ref, keywords=None):
        if run['cycles'] and run['cycles'][-1]['t_end'] is None:
            run['cycles'][-1]['t_end'] = self.t
        if len(run['cycles']) >= MAX_CYCLES:
            raise ActionRejected('TOO_MANY_ITEMS')
        fcs = [int(x) for x in path.split('-')]
        det = run['det'] if run['kind'] != 'manual' else fcs
        cyc = dict(index=len(run['cycles']) + 1, purpose=purpose, path=path, ref=ref, t_start=self.t, t_end=None, keywords=dict(keywords or {}), injections=[],
                   t=[], r={str(f): [] for f in det}, report_points=[], temperature_k=self.T_a_set, flow_rates=[])
        run['cycles'].append(cyc)
        run['flow_path'] = path
        run['ref'] = ref
        self.flow['path'] = path
        self._acc = {}
        self.led['cycles'].add(1)
        return cyc

    def _cmd_run_manual_start(self, run_id, flow_rate_m3_s, flow_path, reference_subtraction='none', data_rate_hz=10, ignore_temperature=False):
        self._check_run_start(flow_path, reference_subtraction)
        if not ignore_temperature and not self._stable():
            raise ActionRejected('TEMPERATURE_NOT_STABLE')
        if data_rate_hz not in (1, 10):
            raise ActionRejected('INVALID_ARGUMENT')
        run = self._new_run(run_id, 'manual', data_rate_hz, 'path', flow_path, reference_subtraction, ignore_temperature)
        run['state'] = 'running'
        self.run = run
        self.flow.update(state='running', rate=float(flow_rate_m3_s), path=flow_path, standby_t0=None)
        run['flow_rate'] = float(flow_rate_m3_s)
        self._open_cycle(run, 'manual', flow_path, reference_subtraction)
        self._event('run', 'RUN_START', f'手动运行{run_id}开始', ref='run:' + run_id)
        return dict(run_id=run_id, cycle=1)

    def _need_manual(self):
        if self.run is None or self.run['kind'] != 'manual':
            raise ActionRejected('NO_MANUAL_RUN')

    def _cmd_run_command(self, command):
        self._need_connected()
        self._need_manual()
        c = command['command']
        need = dict(flow_rate=['flow_rate_m3_s'], flow_path=['flow_path'], sample_injection=['position', 'contact_s'], regeneration_injection=['position', 'contact_s'],
                    wait=['wait_s'], new_cycle=['flow_path'], eject_rack=[], report_point=[])[c]
        if any(k not in command for k in need):
            raise ActionRejected('INVALID_ARGUMENT')
        run = self.run
        step = dict(op=c, **{k: v for k, v in command.items() if k != 'command'})
        if c in ('sample_injection', 'regeneration_injection'):
            rate = run['flow_rate']
            for q in run['queue']:
                if q['op'] == 'flow_rate':
                    rate = q['flow_rate_m3_s']
            vol = rate * command['contact_s']
            if not INJ_RANGE[0] - 1e-15 <= vol <= INJ_RANGE[1] + 1e-15:
                raise ActionRejected('INJECTION_VOLUME_OUT_OF_RANGE')
            if command.get('high_viscosity') and command['contact_s'] > 600.0:
                raise ActionRejected('INJECTION_VOLUME_OUT_OF_RANGE')
            if command['position'] not in self._layout():
                raise ActionRejected('UNKNOWN_POSITION')
        if c == 'flow_path':
            cur = [int(x) for x in self.run['cycles'][-1]['path'].split('-')]
            new = [int(x) for x in command['flow_path'].split('-')]
            if not set(new) <= set(cur):
                raise ActionRejected('FLOW_PATH_NOT_ALLOWED')
        if c == 'new_cycle' and command.get('reference_subtraction', 'none') != 'none':
            act, refc = (int(x) for x in command['reference_subtraction'].split('-'))
            if act not in [int(x) for x in command['flow_path'].split('-')] or refc not in [int(x) for x in command['flow_path'].split('-')]:
                raise ActionRejected('REFERENCE_NOT_IN_PATH')
        if c == 'eject_rack':
            run['queue'].insert(0, step)
            pos = 1
        else:
            run['queue'].append(step)
            pos = len(run['queue'])
        return dict(queued=len(run['queue']), position_in_queue=pos)

    def _cmd_run_queue_delete(self, index):
        self._need_connected()
        self._need_manual()
        if index > len(self.run['queue']):
            raise ActionRejected('NO_SUCH_COMMAND')
        self.run['queue'].pop(index - 1)
        return dict(remaining=len(self.run['queue']))

    def _cmd_run_stop_command(self):
        self._need_connected()
        if self.run is None:
            raise ActionRejected('NO_RUN')
        cur = self.run['cur']
        if cur is None:
            return dict(stopped=None)
        if cur['op'] == 'eject_rack':
            raise ActionRejected('COMMAND_NOT_STOPPABLE')
        if cur.get('plug') is not None:
            pl = cur['plug']
            if pl['t1'] > self.t:
                pl['t1'] = max(pl['t0'], self.t)
        name = cur['op']
        self.run['cur'] = None
        return dict(stopped=name)

    def _cmd_run_pause(self):
        self._need_connected()
        if self.run is None or self.run['state'] not in ('running', 'waiting_temperature', 'preparing'):
            raise ActionRejected('NO_RUN')
        self.run['paused'] = True
        self.run['state'] = 'paused'
        return dict(state='paused')

    def _cmd_run_resume(self):
        self._need_connected()
        if self.run is None or self.run['state'] != 'paused':
            raise ActionRejected('NOT_PAUSED')
        self.run['paused'] = False
        self.run['state'] = 'running'
        return dict(state='running')

    def _cmd_run_stop(self):
        self._need_connected()
        if self.run is None:
            raise ActionRejected('NO_RUN')
        rid = self.run['run_id']
        self._end_run('completed' if self.run['kind'] == 'manual' else 'stopped', None)
        return dict(state=self.runs[rid]['state'])

    def _end_run(self, state, code):
        run = self.run
        if run is None:
            return
        cur = run['cur']
        if cur is not None and cur.get('plug') is not None and cur['plug']['t1'] > self.t:
            cur['plug']['t1'] = max(cur['plug']['t0'], self.t)
        if run['cycles'] and run['cycles'][-1]['t_end'] is None:
            run['cycles'][-1]['t_end'] = self.t
        run.update(state=state, t1=self.t, cur=None, queue=[], error=code)
        self.run = None
        if state == 'completed':
            self.led['runs_completed'].add(1)
        if run.get('op_id') in self.operations and self.operations[run['op_id']]['phase'] == 'running':
            op = self.operations[run['op_id']]
            op['result'] = dict(run_id=run['run_id'], state=state, cycles=len(run['cycles']), results=copy.deepcopy(run['results']))
            self._finish_op(op, 'succeeded' if state == 'completed' else 'cancelled' if state == 'stopped' else 'failed', code)
        self._event('run', 'RUN_END', f'运行{run["run_id"]}结束（{state}）', ref='run:' + run['run_id'])
        if self.power and self._docked():
            self._start_standby()

    def _expand_cycle(self, cyc, default_flow):
        steps = [dict(op='new_cycle', purpose=cyc['purpose'], keywords=cyc.get('keywords', {}), temperature=cyc.get('analysis_temperature_k'))]
        for c in cyc['commands']:
            t = c['type']
            if t == 'wait':
                steps.append(dict(op='wait', wait_s=c.get('wait_s', 0.0)))
                continue
            if 'position' not in c or 'contact_s' not in c:
                raise ActionRejected('INVALID_ARGUMENT')
            flow = c.get('flow_rate_m3_s', default_flow)
            vol = flow * c['contact_s']
            if not INJ_RANGE[0] - 1e-15 <= vol <= INJ_RANGE[1] + 1e-15:
                raise ActionRejected('INJECTION_VOLUME_OUT_OF_RANGE')
            steps.append(dict(op='inject', kind=t, position=c['position'], contact_s=c['contact_s'], flow_rate_m3_s=flow,
                              dissociation_s=c.get('dissociation_s', 0.0), stabilization_s=c.get('stabilization_s', 0.0)))
        return steps

    def _start_auto_run(self, kind, run_id, steps, path, ref, data_rate, detection, ignore_temperature, cap_kind):
        lay = self._layout()
        for s in steps:
            for key in ('position', 'edc', 'nhs', 'ethanolamine', 'wash', 'nickel'):
                if s.get(key) is not None and s.get(key) not in lay:
                    raise ActionRejected('UNKNOWN_POSITION')
        op = self._new_op(cap_kind)
        run = self._new_run(run_id, kind, data_rate, detection, path, ref, ignore_temperature, owner_op=op['op_id'])
        op['run_id'] = run_id
        run['steps'] = steps
        run['state'] = 'running' if (ignore_temperature or self._stable()) else 'waiting_temperature'
        run['flow_rate'] = 10 * UL_MIN
        self.run = run
        self.flow.update(state='running', rate=run['flow_rate'], path=path, standby_t0=None)
        self._event('run', 'RUN_START', f'{kind}运行{run_id}开始', ref='run:' + run_id)
        return op

    def _cmd_method_run(self, run_id, data_rate_hz, flow_path, cycles, detection='multi', reference_subtraction='none', buffer_tube=None, sample_compartment_k=None,
                        prime_before=False, normalize_position=None, ignore_temperature=False):
        if buffer_tube is not None:
            self.flow['tube'] = buffer_tube
        self._check_run_start(flow_path, reference_subtraction)
        if data_rate_hz not in (1, 10):
            raise ActionRejected('INVALID_ARGUMENT')
        steps = []
        if prime_before:
            steps.append(dict(op='prime'))
        if normalize_position is not None:
            steps.append(dict(op='normalize', position=normalize_position))
        for cyc in cycles:
            if cyc.get('analysis_temperature_k') is not None and cyc['analysis_temperature_k'] < self.T_room - DOC['analysis_below_ambient_max_K'] - 1e-9:
                raise ActionRejected('BELOW_AMBIENT_LIMIT')
            steps += self._expand_cycle(cyc, 30 * UL_MIN)
        if sample_compartment_k is not None:
            self._cmd_temperature_set(sample_compartment_k=sample_compartment_k)
        op = self._start_auto_run('method', run_id, steps, flow_path, reference_subtraction, data_rate_hz, detection, ignore_temperature, 'method.run')
        return dict(operation_id=op['op_id'])

    def _cmd_wizard_immobilization(self, run_id, flow_cells, prime_before=False, normalize_position=None, ignore_temperature=False):
        self._check_run_start('1-2-3-4', 'none')
        ct = self.chip['type']
        seen, steps = set(), []
        if prime_before:
            steps.append(dict(op='prime'))
        if normalize_position is not None:
            steps.append(dict(op='normalize', position=normalize_position))
        for f in flow_cells:
            fc = f['fc']
            if fc in seen:
                raise ActionRejected('INVALID_ARGUMENT')
            seen.add(fc)
            chem, mode = f['chemistry'], f['mode']
            if chem == 'amine' and ct not in ('CM5', 'CM4', 'CM3', 'C1'):
                raise ActionRejected('CHEMISTRY_NOT_SUPPORTED')
            if chem == 'his_capture' and ct != 'NTA' or chem == 'biotin_capture' and ct != 'SA':
                raise ActionRejected('CHEMISTRY_NOT_SUPPORTED')
            if mode != 'blank' and 'ligand_position' not in f:
                raise ActionRejected('INVALID_ARGUMENT')
            if mode == 'contact' and 'contact_s' not in f or mode == 'aim' and 'target_ru' not in f:
                raise ActionRejected('INVALID_ARGUMENT')
            if chem == 'amine' and any(k not in f for k in ('edc_position', 'nhs_position', 'mix_position', 'ethanolamine_position')):
                raise ActionRejected('INVALID_ARGUMENT')
            if chem == 'amine' and (f['mix_position'] in self.rack['positions'] or f['mix_position'] not in self._layout()):
                raise ActionRejected('MIX_POSITION_OCCUPIED')
            if chem == 'his_capture' and 'nickel_position' not in f:
                raise ActionRejected('INVALID_ARGUMENT')
            if mode == 'aim' and chem == 'amine' and 'wash_position' not in f:
                raise ActionRejected('INVALID_ARGUMENT')
            flow = f.get('flow_rate_m3_s', 10 * UL_MIN)
            if mode == 'contact' and not INJ_RANGE[0] - 1e-15 <= flow * f['contact_s'] <= INJ_RANGE[1] + 1e-15:
                raise ActionRejected('INJECTION_VOLUME_OUT_OF_RANGE')
            steps.append(dict(op='new_cycle', purpose='immobilization', keywords=dict(sample_name=f.get('ligand_name', 'blank')), path=str(fc), temperature=None))
            P = AMINE = DOC['amine_protocol']
            steps.append(dict(op='wait', wait_s=60.0))
            steps.append(dict(op='mark', fc=fc, label='a'))
            if mode == 'aim' and chem == 'amine':
                pc = DOC['preconc_pulse']
                steps.append(dict(op='inject', kind='preconc', position=f['ligand_position'], contact_s=pc['volume_m3'] / pc['flow_m3_s'], flow_rate_m3_s=pc['flow_m3_s'],
                                  dissociation_s=30.0, stabilization_s=0.0, fc=fc))
                steps.append(dict(op='inject', kind='wash', position=f['wash_position'], contact_s=30.0, flow_rate_m3_s=10 * UL_MIN, dissociation_s=60.0, stabilization_s=0.0))
                steps.append(dict(op='mark', fc=fc, label='a'))
            if chem == 'amine':
                steps.append(dict(op='mix', a=f['edc_position'], b=f['nhs_position'], dest=f['mix_position'], volume_m3=P['flow_m3_s'] * P['activation_s'] / 2 + 25e-9))
                steps.append(dict(op='inject', kind='edc_nhs', position=f['mix_position'], contact_s=P['activation_s'], flow_rate_m3_s=P['flow_m3_s'], dissociation_s=60.0,
                                  stabilization_s=0.0))
            elif chem == 'his_capture':
                steps.append(dict(op='inject', kind='nickel', position=f['nickel_position'], contact_s=60.0, flow_rate_m3_s=10 * UL_MIN, dissociation_s=60.0, stabilization_s=0.0))
            steps.append(dict(op='mark', fc=fc, label='c'))
            if mode == 'contact':
                steps.append(dict(op='inject', kind='ligand', position=f['ligand_position'], contact_s=f['contact_s'], flow_rate_m3_s=flow, dissociation_s=60.0,
                                  stabilization_s=0.0))
            elif mode == 'aim':
                steps.append(dict(op='aim', fc=fc, position=f['ligand_position'], flow_rate_m3_s=flow, target_ru=f['target_ru'], chem=chem))
            steps.append(dict(op='mark', fc=fc, label='e'))
            if chem == 'amine':
                steps.append(dict(op='inject', kind='ethanolamine', position=f['ethanolamine_position'], contact_s=P['deactivation_s'], flow_rate_m3_s=P['flow_m3_s'],
                                  dissociation_s=120.0, stabilization_s=0.0))
            else:
                steps.append(dict(op='wait', wait_s=120.0))
            steps.append(dict(op='mark', fc=fc, label='g'))
            steps.append(dict(op='immob_result', fc=fc, chem=chem, mode=mode, target=f.get('target_ru'), ligand=f.get('ligand_name')))
        op = self._start_auto_run('immobilization', run_id, steps, '1-2-3-4', 'none', 10, 'multi', ignore_temperature, 'wizard.immobilization')
        return dict(operation_id=op['op_id'])

    def _cmd_wizard_ph_scouting(self, run_id, fc, ligand_positions, wash_position, contact_s=120.0, flow_rate_m3_s=10 * UL_MIN):
        self._check_run_start(str(fc), 'none')
        if not INJ_RANGE[0] - 1e-15 <= flow_rate_m3_s * contact_s <= INJ_RANGE[1] + 1e-15:
            raise ActionRejected('INJECTION_VOLUME_OUT_OF_RANGE')
        steps = []
        for pos in ligand_positions:
            steps.append(dict(op='new_cycle', purpose='ph_scouting', keywords=dict(sample_name=pos), path=str(fc), temperature=None))
            steps.append(dict(op='wait', wait_s=30.0))
            steps.append(dict(op='inject', kind='preconc', position=pos, contact_s=contact_s, flow_rate_m3_s=flow_rate_m3_s, dissociation_s=60.0, stabilization_s=0.0))
            steps.append(dict(op='inject', kind='wash', position=wash_position, contact_s=30.0, flow_rate_m3_s=10 * UL_MIN, dissociation_s=60.0, stabilization_s=0.0))
            steps.append(dict(op='scout_result', fc=fc, position=pos))
        op = self._start_auto_run('ph_scouting', run_id, steps, str(fc), 'none', 10, 'single', False, 'wizard.ph_scouting')
        return dict(operation_id=op['op_id'])

    def _cycle_mean(self, cyc, fc, t_end, window=5.0):
        """Mean recorded response of a flow cell over the window ending at the absolute time t_end."""
        ts, rs = cyc['t'], cyc['r'].get(str(fc), [])
        te = t_end - cyc['t_start']
        vals = [r for t, r in zip(ts, rs) if te - window - 1e-9 <= t <= te + 1e-9 and r is not None]
        return float(np.mean(vals)) if vals else None

    def _run_tick(self):
        run = self.run
        if run is None or run['state'] in ('paused',):
            return
        if run['state'] == 'waiting_temperature':
            if self._stable() or run['ignore_temperature']:
                run['state'] = 'running'
            else:
                return
        for _ in range(8):                                        # immediate steps chain within one grid point
            cur = run['cur']
            if cur is None:
                cur = self._next_step(run)
                if cur is None:
                    return
                run['cur'] = cur
            if not self._exec_step(run, cur):
                return
            run['cur'] = None
            if self.run is not run:
                return

    def _next_step(self, run):
        if run['kind'] == 'manual':
            if not run['queue']:
                return None
            s = run['queue'].pop(0)
        else:
            if not run['steps']:
                self._end_run('completed', None)
                return None
            s = run['steps'].pop(0)
        s = dict(s, t_start=self.t, phase='start')
        return s

    def _exec_step(self, run, s):
        """Advance one step; True when the step is finished."""
        op = s['op']
        cyc = run['cycles'][-1] if run['cycles'] else None
        if op == 'flow_rate':
            run['flow_rate'] = s['flow_rate_m3_s']
            self.flow['rate'] = s['flow_rate_m3_s']
            return True
        if op == 'flow_path':
            self.flow['path'] = s['flow_path']
            run['flow_path'] = s['flow_path']
            if s.get('reference_subtraction'):
                cyc['ref'] = s['reference_subtraction']
            return True
        if op == 'new_cycle':
            path = s.get('flow_path') or s.get('path') or run['flow_path']
            if run['kind'] in ('immobilization', 'ph_scouting'):
                path = s['path']
            self._open_cycle(run, s.get('purpose', 'manual'), path, s.get('reference_subtraction', run['ref']) if run['kind'] == 'manual' else run['ref'] if run['kind'] == 'method' else 'none',
                             s.get('keywords'))
            if s.get('temperature') is not None and abs(s['temperature'] - self.T_a_set) > 1e-9:
                self.T_a_set = float(s['temperature'])
                run['cycles'][-1]['temperature_k'] = self.T_a_set
                if not run['ignore_temperature']:
                    run['state'] = 'waiting_temperature'
            return True
        if op == 'report_point':
            cyc['report_points'].append(dict(label=s.get('label') or 'manual', time_s=self.t - cyc['t_start']))
            return True
        if op == 'wait':
            if s['phase'] == 'start':
                s['phase'], s['t_end'] = 'wait', self.t + s['wait_s']
            return self.t >= s['t_end'] - 1e-9
        if op == 'eject_rack':
            if s['phase'] == 'start':
                if self.rack['state'] == 'inside':
                    self.rack['state'] = 'ejecting'
                s['phase'] = 'moving'
            return self.rack['state'] in ('ejected', 'inside') and self.rack['state'] != 'ejecting'
        if op == 'mark':
            run['marks'].setdefault(s['fc'], {})[s['label']] = self.t
            return True
        if op == 'mix':
            if s['phase'] == 'start':
                pa, pb = self.rack['positions'].get(s['a']), self.rack['positions'].get(s['b'])
                if pa is None or pb is None or pa['V'] < s['volume_m3'] or pb['V'] < s['volume_m3']:
                    self._event('error', 'MIX_FAILED', 'EDC/NHS体积不足：活化失败')
                    run['results'].setdefault('warnings', []).append('MIX_FAILED')
                    s['mixed'] = False
                else:
                    v = s['volume_m3']
                    pa['V'] -= v
                    pb['V'] -= v
                    self.rack['positions'][s['dest']] = dict(solution_id='edc_nhs_mix', comp=_mix_comp([(pa['comp'], v), (pb['comp'], v)]), V=2 * v, placed=2 * v, capped=True,
                                                             t=self.t, label=[])
                    s['mixed'] = True
                s['phase'], s['t_end'] = 'mixing', self.t + 30.0
            return self.t >= s['t_end'] - 1e-9
        if op in ('inject', 'sample_injection', 'regeneration_injection'):
            return self._exec_inject(run, s, cyc)
        if op == 'aim':
            return self._exec_aim(run, s, cyc)
        if op == 'immob_result':
            m = run['marks'].get(s['fc'], {})
            cyc_ = cyc
            R = lambda lab: self._cycle_mean(cyc_, s['fc'], m[lab] - 10.0) if lab in m else None      # noqa: E731
            a, c, e, g = R('a'), R('c'), R('e'), R('g')
            res = dict(fc=s['fc'], ligand=s.get('ligand'), chemistry=s['chem'], mode=s['mode'],
                       response_bound_ru=None if e is None or c is None else e - c, response_final_ru=None if g is None or a is None else g - a,
                       target_ru=s.get('target'), target_reached=None if s['mode'] != 'aim' else bool(run['results'].get(f'aim_{s["fc"]}', {}).get('reached')),
                       aim=run['results'].get(f'aim_{s["fc"]}'))
            run['results'].setdefault('immobilization', []).append(res)
            if res['response_final_ru'] is not None:
                self.led['ligand_immobilized_ru'].add(max(0.0, res['response_final_ru']))
            self._event('run', 'IMMOBILIZED', f'Fc{s["fc"]}固定化：final {res["response_final_ru"] or 0:.0f} RU', ref='run:' + run['run_id'])
            return True
        if op == 'scout_result':
            injs = [i for i in cyc['injections'] if i['kind'] == 'preconc']
            if injs:
                i = injs[-1]
                base = self._cycle_mean(cyc, s['fc'], i['start_s'] + cyc['t_start'] - 5.0)
                top = self._cycle_mean(cyc, s['fc'], i['end_s'] + cyc['t_start'] - 1.0)
                end = self._cycle_mean(cyc, s['fc'], self.t)
                run['results'].setdefault('ph_scouting', []).append(dict(position=s['position'], solution_id=self.rack['positions'].get(s['position'], {}).get('solution_id'),
                                                                         preconcentration_ru=None if base is None or top is None else top - base,
                                                                         residual_ru=None if base is None or end is None else end - base))
            return True
        if op in ('prime', 'normalize'):
            if s['phase'] == 'start':
                if op == 'prime':
                    s['t_end'] = self.t + sum(DOC['prime_s']) / 2.0
                    self.flow['rate'] = LIQ['prime_flow_m3_s']
                else:
                    p = self.rack['positions'].get(s['position'])
                    if p is None or p['comp'].get('base') != 'bianormalizing':
                        run['results'].setdefault('warnings', []).append('NORMALIZE_SKIPPED_WRONG_SOLUTION')
                        return True
                    s['t_end'] = self.t + DOC['normalize_s']
                    for k in range(2):
                        self._add_plug(self.t + 60.0 + 200.0 * k, 45.0, p, 'normalize', '1-2-3-4', 20 * UL_MIN)
                s['phase'] = 'busy'
            if self.t >= s['t_end'] - 1e-9:
                if op == 'prime':
                    b = self._tube_bottle(self.flow['tube'])
                    if b is not None:
                        self.flow.update(sys_comp=copy.deepcopy(b['comp']), sys_id=b['solution_id'], mix=1.0, target_id=None, target_comp=None)
                else:
                    c = self._chip_obj()
                    c['normalized'] = True
                    c['gains'] = [1.0 + 0.0005 * _gauss(self.seed ^ _h(c['id']), c['docks'] + 100, f['fc']) for f in c['fcs']]
                self.flow['rate'] = run['flow_rate']
                return True
            return False
        return True

    def _exec_inject(self, run, s, cyc):
        if s['phase'] == 'start':
            flow = s.get('flow_rate_m3_s') or run['flow_rate']
            s['flow'] = flow
            if run['kind'] != 'manual':
                self.flow['rate'] = flow
                run['flow_rate'] = flow
            p = self.rack['positions'].get(s['position'])
            if p is None or self.rack['state'] != 'inside':
                self._event('injection', 'POSITION_EMPTY', f'{s["position"]}没有溶液：注入空气', ref='run:' + run['run_id'])
                p = dict(V=0.0, comp=dict(solutes={}, species={}, base=None, pH=None), pos=s['position'])
            else:
                p['pos'] = s['position']
            s['p'] = p
            vol = flow * s['contact_s']
            s['t_plug'] = self.t + LIQ['needle_prep_s'] + vol / LIQ['aspiration_m3_s']
            s['phase'] = 'prep'
        if s['phase'] == 'prep':
            if self.t < s['t_plug'] - 1e-9:
                return False
            kind = s['op'] if s['op'] != 'inject' else s.get('kind', 'sample')
            kind = {'sample_injection': 'sample', 'regeneration_injection': 'regeneration'}.get(s['op'], s.get('kind', 'sample'))
            pl = self._add_plug(self.t, s['contact_s'], s['p'], kind, self.flow['path'], s['flow'], cycle=cyc['index'] if cyc else None)
            s['plug'] = pl
            self.led['injections'].add(1)
            if cyc is not None:
                rec = dict(kind=kind, position=s['position'], solution_id=s['p'].get('solution_id'), start_s=pl['t0'] - cyc['t_start'], end_s=pl['t1'] - cyc['t_start'],
                           nominal_end_s=pl['t0'] + s['contact_s'] - cyc['t_start'], flow_rate_m3_s=s['flow'], air=pl['air'],
                           label=copy.deepcopy(s['p'].get('label', [])))
                cyc['injections'].append(rec)
                s['rec'] = rec
            s['phase'] = 'contact'
            return False
        if s['phase'] == 'contact':
            pl = s['plug']
            if self.t < pl['t1'] - 1e-9:
                return False
            s['phase'], s['t_end'] = 'after', pl['t1'] + float(s.get('dissociation_s') or 0.0) + float(s.get('stabilization_s') or 0.0)
        return self.t >= s['t_end'] - 1e-9

    def _exec_aim(self, run, s, cyc):
        """Aim for immobilized level: ligand pulses sized from the observed coupling until the bound level reaches the target (documented: the
        pre-concentration pulse is used to size the contact times; maximum total ligand 150 uL; too slow pre-concentration is reported).  Pulse
        sizing (engineering equivalent): the pre-concentration rise time tau from the pre-concentration pulse; the coupled gain of a pulse of length d
        is ~ k (d - tau (1 - exp(-d / tau))); each new pulse covers 90 % of the remaining gap."""
        fc = s['fc']
        st = run['results'].setdefault(f'aim_{fc}', dict(pulses=0, volume_m3=0.0, reached=False, level_ru=0.0, reason=None))

        def lag(d, tau):
            return d - tau * (1.0 - math.exp(-d / tau))
        if s['phase'] == 'start':
            s['base'] = self._cycle_mean(cyc, fc, self.t - 1.0) or 0.0
            pc_inj = [i for i in cyc['injections'] if i['kind'] == 'preconc']
            s['tau'], plateau = 12.5, None
            if pc_inj:
                i = pc_inj[-1]
                t0, t1 = i['start_s'] + cyc['t_start'], i['end_s'] + cyc['t_start']
                b0 = self._cycle_mean(cyc, fc, t0 - 5.0)
                b1 = self._cycle_mean(cyc, fc, t1 - 1.0)
                if b0 is not None and b1 is not None:
                    plateau = b1 - b0
                    ts, rs = cyc['t'], cyc['r'].get(str(fc), [])
                    hit = next((t for t, rv in zip(ts, rs) if rv is not None and t >= t0 - cyc['t_start'] and rv - b0 >= 0.632 * plateau), None)
                    if hit is not None:
                        s['tau'] = float(np.clip(hit - (t0 - cyc['t_start']), 1.0, 60.0))
            if plateau is not None and plateau < 0.05 * s['target_ru']:
                st['reason'] = 'preconcentration_too_slow'
                return True
            if plateau is not None and plateau > 40.0 * s['target_ru']:      # even the shortest pulse would overshoot the target
                st['reason'] = 'preconcentration_too_fast'
                return True
            s['dur'] = 15.0 if plateau is None else float(np.clip(15.0 * 10.0 * s['target_ru'] / max(plateau, 1e-9), 5.0, 15.0))
            s['phase'] = 'pulse'
            s['sub'] = None
        if s['phase'] == 'pulse':
            if s['sub'] is None:
                vol = s['flow_rate_m3_s'] * s['dur']
                if st['volume_m3'] + vol > DOC['aim_max_ligand_m3'] + 1e-15:
                    st['reason'] = 'maximum_ligand_volume'
                    return True
                s['sub'] = dict(op='inject', kind='ligand', position=s['position'], contact_s=s['dur'], flow_rate_m3_s=s['flow_rate_m3_s'], dissociation_s=30.0,
                                stabilization_s=0.0, phase='start')
                s['lvl0'] = (self._cycle_mean(cyc, fc, self.t - 1.0) or s['base']) - s['base']
                st['volume_m3'] += vol
                st['pulses'] += 1
            if not self._exec_inject(run, s['sub'], cyc):
                return False
            lvl = (self._cycle_mean(cyc, fc, self.t - 1.0) or 0.0) - s['base']
            st['level_ru'] = lvl
            gain = lvl - s['lvl0']
            if lvl >= 0.95 * s['target_ru']:
                st['reached'] = True
                return True
            if st['pulses'] >= 12:
                st['reason'] = 'too_many_pulses'
                return True
            if gain <= 0:
                s['dur'] = min(300.0, 2.0 * s['dur'])
            else:
                k = gain / lag(s['dur'], s['tau'])
                need = 0.9 * (s['target_ru'] - lvl) / k
                lo, hi = 1.0, 600.0
                for _ in range(40):
                    mid = 0.5 * (lo + hi)
                    lo, hi = (mid, hi) if lag(mid, s['tau']) < need else (lo, mid)
                s['dur'] = float(np.clip(0.5 * (lo + hi), 5.0, 300.0))
            s['sub'] = None
        return False

    # ---------------------------------------------------------------- time advance
    def step(self, dt_s):
        """Advance the host clock: physics on the fixed 0.1 s grid (k x 0.1 s) while anything is injected, recorded or moving, in whole 10 s blocks
        aligned to the grid when idle; state between grid points is held, so any split of the same interval gives identical results."""
        if dt_s < 0:
            raise ActionRejected('INVALID_ARGUMENT')
        t_end = self.t + float(dt_s)
        if self.plant is None:
            self.t = t_end
            return self.t
        nb = int(round(IDLE_BLOCK / DT))
        while True:
            if self.tick % nb == 0 and (self.tick + nb) * DT <= t_end + 1e-9 and self._idle():
                self.tick += nb
                self.t = self.tick * DT
                self._tick(IDLE_BLOCK, idle=True)
            elif (self.tick + 1) * DT <= t_end + 1e-9:
                self.tick += 1
                self.t = self.tick * DT
                self._tick(DT)
            else:
                break
        self.t = t_end
        return self.t

    def _idle(self):
        if self.run is not None or self._running_ops() or self.plugs or self.spikes:
            return False
        rk = self.rack
        if rk['state'] in ('ejecting', 'retracting') or (rk['state'] == 'ejected' and self.power and rk['t_out'] is not None):
            return False                                               # tray moving, or the auto-retract timer running (powered)
        if abs(self.chip['cover'] - self.chip['cover_target']) > 1e-9 or abs(self.sc_door - self.sc_door_target) > 1e-9:
            return False
        if any(abs(self.doors[d] - self.door_targets[d]) > 1e-9 for d in self.doors) or abs(self.clamp_angle - self.clamp_target) > 1e-9:
            return False
        if self.power and self.t - self.t_on < DOC['software_connect_s'] + 2.0:
            return False
        if self.flow.get('target_comp') is not None:
            return False
        return True

    @staticmethod
    def _move(x, target, speed, dt):
        return target if abs(target - x) <= speed * dt else x + math.copysign(speed * dt, target - x)

    def _tick(self, dt, idle=False):
        t = self.t
        if self.pending_waste > 0.0:
            self._to_waste(self.pending_waste)
            self.pending_waste = 0.0
        if self.power and self.t_on is not None and t - dt < self.t_on + DOC['software_connect_s'] <= t + 1e-9:
            self._event('power', 'CONNECTED', '控制软件已与仪器连接', t=self.t_on + DOC['software_connect_s'])
        # temperatures
        if self.power:
            self.T_a = _temp_advance(self.T_a, self.T_a_set, dt, TEMPC['k_1_s'], TEMPC['rate_K_s'])
            self.T_sc = self.T_sc_set + (self.T_sc - self.T_sc_set) * math.exp(-dt / TEMPC['sample_tau_s'])
        else:
            f = math.exp(-dt / TEMPC['off_tau_s'])
            self.T_a = self.T_room + (self.T_a - self.T_room) * f
            self.T_sc = self.T_room + (self.T_sc - self.T_room) * f
        # mechanics
        if self.power or self.chip['cover_target'] < self.chip['cover']:   # opening needs the motor; closing by hand works without power
            self.chip['cover'] = self._move(self.chip['cover'], self.chip['cover_target'], 1.2, dt)
        for d in self.doors:
            self.doors[d] = self._move(self.doors[d], self.door_targets[d], 2.0, dt)
        self.clamp_angle = self._move(self.clamp_angle, self.clamp_target, 2.0, dt)
        self.sc_door = self._move(self.sc_door, self.sc_door_target, 1.5, dt)
        r = self.rack
        v_rack = RACK_TRAVEL_M / ENG['rack_travel_s']
        if r['state'] == 'ejecting':
            r['pos'] = self._move(r['pos'], RACK_TRAVEL_M, v_rack, dt)
            if r['pos'] >= RACK_TRAVEL_M - 1e-12:
                r['state'], r['t_out'] = 'ejected', t
        elif r['state'] == 'retracting':
            r['pos'] = self._move(r['pos'], 0.0, v_rack, dt)
            if r['pos'] <= 1e-12:
                r['state'], r['t_out'] = 'inside', None
                self._event('rack', 'RACK_IN', '托盘已送回')
        elif r['state'] == 'ejected' and self.power and r['t_out'] is not None and t - r['t_out'] >= self.auto_retract_s - 1e-9:
            if r['config'] is not None and not r['config']['correctly_mounted']:
                if 'RACK_NOT_SEATED' not in self.warnings:
                    self._event('warning', 'RACK_NOT_SEATED', '托盘装反：不能自动送回')
            else:
                r['state'] = 'retracting'
        # asynchronous operations
        for op in list(self._running_ops()):
            if op['t_end'] is not None and t >= op['t_end'] - 1e-9:
                if op['kind'] == 'chip.dock':
                    self._finish_dock(op)
                elif op['kind'] == 'chip.eject':
                    self._finish_eject(op)
                else:
                    self._end_flow_op(op)
                    self._finish_op(op, 'succeeded')
                    self._event('maintenance', 'DONE', f'{op["kind"]}完成')
        # run engine
        if self.power:
            self._run_tick()
        # liquids
        self._liquids(dt)
        # flow-cell physics
        self._surface(dt, t - 0.5 * dt)
        keep = [p for p in self.plugs if t < p['t1'] + 30.0 * max(0.05, ENG['dispersion_volume_m3'] / max(p['flow'], 1e-15)) + 5.0]
        for p in self.plugs:
            if p not in keep:
                self._to_waste(p.get('waste_m3', 0.0))
        self.plugs = keep
        self.spikes = [s for s in self.spikes if t < s['t1'] + 1.0]
        self._readings(dt, idle)
        e = (200.0 + (100.0 if self.run is not None else 0.0)) if self.power else 0.0
        self.led['electrical_energy_j'].add(e * dt)
        self._warnings()

    def _to_waste(self, vol):
        ws = self.bottles.get('waste')
        if ws is None:
            self.led['waste_spilled_m3'].add(vol)
            return
        add = min(BOTTLE_CAP['waste'] - ws['V'], vol)
        ws['V'] += add
        self.led['waste_added_m3'].add(add)
        if vol > add + 1e-18:
            self.led['waste_spilled_m3'].add(vol - add)
            self._error('WASTE_OVERFLOW', '废液瓶满：液体溢出到托盘')

    def _liquids(self, dt):
        f = self.flow
        if not self.power or f['state'] in ('stopped', 'dry'):
            return
        tube = 'buffer_a' if f['state'] == 'standby' else f['tube']
        b = self.bottles.get(tube)
        draw = f['rate'] * dt
        peri = LIQ['peristaltic_m3_s'] * dt if self.clamp_closed else 0.0
        if f['state'] in ('docking', 'ejecting'):
            draw, peri = 0.0, 0.0
        if b is None or not b['tube'] or b['V'] <= draw + 0.5 * peri:
            if draw + peri > 0:
                if b is not None:
                    b['V'] = 0.0
                self._error('BUFFER_EMPTY', f'{tube}没有缓冲液：注射泵抽空，响应超出量程')
                self.empty_tube = tube
                if self.run is not None:
                    self._end_run('failed', 'BUFFER_EMPTY')
                f.update(state='stopped', rate=0.0, standby_t0=None)
            return
        b['V'] -= draw + 0.5 * peri
        self.led['buffer_used_m3'].add(draw + 0.5 * peri)
        w = self.bottles.get('water')
        wdraw = 0.0
        if w is not None and w['tube'] and w['V'] > 0:
            wdraw = min(w['V'], 0.5 * peri)
            w['V'] -= wdraw
            self.led['water_used_m3'].add(wdraw)
        self._to_waste(draw + 0.5 * peri + wdraw)
        # running buffer in the flow system follows the bottle of the selected tube (gradual change without Prime)
        if b['solution_id'] != f['sys_id'] and f['sys_comp'] is not None:
            if f.get('target_id') != b['solution_id']:
                f.update(target_id=b['solution_id'], target_comp=copy.deepcopy(b['comp']), mix=1.0)
            tau = LIQ['buffer_switch_tau_s'] * (30 * UL_MIN) / max(f['rate'], 1e-12)
            f['mix'] *= math.exp(-dt / tau)
            if f['mix'] < 1e-3:
                f.update(sys_comp=f['target_comp'], sys_id=f['target_id'], target_comp=None, target_id=None, mix=1.0)
        elif f['sys_comp'] is None:
            f.update(sys_comp=copy.deepcopy(b['comp']), sys_id=b['solution_id'], mix=1.0)
        # evaporation of uncovered rack positions (concentrates the solutes)
        ev = LIQ['evaporation_uncovered_m3_s'] * dt * (2.0 ** ((self.T_sc - 298.15) / 10.0))
        for p in self.rack['positions'].values():
            if not p['capped'] and p['V'] > ev:
                k = p['V'] / (p['V'] - ev)
                p['V'] -= ev
                for key in ('solutes', 'species'):
                    p['comp'][key] = {s_: c_ * k for s_, c_ in p['comp'].get(key, {}).items()}

    def _species_activity(self, sid):
        for p in self.pairs.values():
            if sid in (p['a'], p['b']):
                return p['activity']
        return 0.8

    def _surface(self, dt, tm):
        if not self._docked() or self.flow['state'] == 'dry':
            self._liq = {}
            return
        c = self._chip_obj()
        f_rate = self.flow['rate'] if self.power and self.flow['state'] not in ('stopped', 'docking', 'ejecting') else 0.0
        T = self.T_a
        self._liq = {}
        dextran = c['type'] in ('CM5', 'CM4', 'CM3', 'C1', 'SA', 'NTA', 'L1')
        amine = c['type'] in ('CM5', 'CM4', 'CM3', 'C1')
        for fc in c['fcs']:
            L = self._fc_liquid(fc['fc'], tm)
            self._liq[fc['fc']] = L
            ph, I = L['pH'], L['I']
            # amine chemistry: activation, hydrolysis, deactivation
            if amine:
                if L['edc'] > 0:
                    fc['esters'] += (1.0 - fc['esters']) * (1.0 - math.exp(-ACT['k_act_1_s'] * (L['edc'] / 200.0) * dt))
                fc['esters'] *= math.exp(-dt / ACT['hydrolysis_tau_s'])
                if L['ethanolamine'] > 0:
                    da = fc['esters'] * (1.0 - math.exp(-ENG['deactivation']['k_deact_1_s'] * (L['ethanolamine'] / 1000.0) * dt))
                    fc['esters'] -= da
                    fc['deact_ru'] += da * 0.25 * ACT['activation_RU'] * fc['cap_rel']
                fc['act_ru'] = ACT['activation_RU'] * fc['cap_rel'] * fc['esters'] + fc['deact_ru']
            # electrostatic pre-concentration and covalent coupling
            cov_tot = sum(l['level'] for l in fc['ligands'] if l['kind'] == 'covalent')
            for sid in set(L['species']) | set(fc['preconc']):
                sp = self.species[sid]
                c_kg = L['species'].get(sid, 0.0) * sp['mw_da'] * 1e-3
                Ke = 0.0
                if dextran and sp['kind'] != 'small_molecule':
                    Ke = PC['K0_m3_kg'] * max(0.0, math.exp(PC['gamma_per_pH'] * (sp['pI'] - ph)) - 1.0) / (1.0 + 10 ** (PC['pKa_surface'] - ph)) * PC['I0_mol_m3'] / (PC['I0_mol_m3'] + I)
                Req = PC['R_pc_max_RU'] * fc['cap_rel'] * Ke * c_kg / (1.0 + Ke * c_kg)
                R = fc['preconc'].get(sid, 0.0)
                k_rel = PC['k_pc_1_s'] if Req >= R else PC['k_release_1_s']          # uptake transport-limited; release (charge reversal, salt) fast
                R = Req + (R - Req) * math.exp(-k_rel * dt)
                if amine and fc['esters'] > 1e-6 and R > 0 and Ke > 0:          # only ligand held in the activated matrix couples
                    f_am = 1.0 / (1.0 + 10 ** (CPL['pKa_amine'] - ph))
                    cap = CPL['capacity_RU'] * fc['cap_rel']
                    rate = CPL['k_c_1_s'] * fc['esters'] * f_am * max(0.0, 1.0 - cov_tot / max(cap, 1e-9))
                    dRc = R * (1.0 - math.exp(-rate * dt))
                    if dRc > 0:
                        R -= dRc
                        cov_tot += dRc
                        fc['esters'] = max(0.0, fc['esters'] - dRc / (CPL['ester_capacity_RU'] * fc['cap_rel']))
                        lig = next((l for l in fc['ligands'] if l['species'] == sid and l['kind'] == 'covalent'), None)
                        if lig is None:
                            lig = dict(lid=f'{fc["fc"]}:{sid}:covalent', species=sid, kind='covalent', level=0.0, activity=self._species_activity(sid), kd_cap=0.0)
                            fc['ligands'].append(lig)
                        lig['level'] += dRc
                        fc['used'] = True
                        c['used'] = True
                if R > 1e-9 or sid in L['species']:
                    fc['preconc'][sid] = R
                else:
                    fc['preconc'].pop(sid, None)
            # capture surfaces
            if c['type'] == 'NTA':
                if L['ni'] > 0:
                    fc['ni'] += (1.0 - fc['ni']) * (1.0 - math.exp(-CAPT['ni_loading_1_s'] * dt))
                if L['edta'] > 0:                                              # chelation by EDTA: 350 mM strips at ~0.2 s^-1, rate proportional to C_EDTA
                    fc['ni'] *= math.exp(-0.2 * (L['edta'] / 350.0) * dt)
            for sid, cm in L['species'].items():
                sp = self.species[sid]
                if c['type'] == 'SA' and 'biotin' in sp['tags'] or c['type'] == 'NTA' and any(t_.startswith('his') for t_ in sp['tags']) and fc['ni'] > 0.01:
                    rcap = float(np.clip(2000.0 * sp['mw_da'] / 50000.0, 100.0, 6000.0)) * (fc['ni'] if c['type'] == 'NTA' else 1.0)
                    lig = next((l for l in fc['ligands'] if l['species'] == sid and l['kind'] == 'capture'), None)
                    if lig is None:
                        kd_cap = 0.0 if c['type'] == 'SA' else CAPT['his10_kd_1_s'] if 'his10' in sp['tags'] else CAPT['his6_kd_1_s']
                        lig = dict(lid=f'{fc["fc"]}:{sid}:capture', species=sid, kind='capture', level=0.0, activity=self._species_activity(sid), kd_cap=kd_cap)
                        fc['ligands'].append(lig)
                    D = diffusion_estimate(sp['mw_da'], T, eta_water(T) * L['eta_rel']) if sp.get('D_m2_s') is None else sp['D_m2_s'] * (T / 298.15) * eta_water(298.15) / (eta_water(T) * L['eta_rel'])
                    kt = km_flow_cell(D, f_rate) * sp['mw_da'] * 1e6 if f_rate > 0 else 0.0
                    k = kt * cm / max(rcap, 1e-9)
                    if rcap > lig['level']:
                        lig['level'] = rcap - (rcap - lig['level']) * math.exp(-k * dt)
                    fc['used'] = True
                    c['used'] = True
            for lig in fc['ligands']:
                if lig['kind'] == 'capture' and (lig['kd_cap'] > 0 or L['edta'] > 0):
                    strip = min(1.0, L['edta'] / 350.0) if lig['kd_cap'] > 0 else 0.0      # His capture released with the nickel
                    kdc = lig['kd_cap'] + 0.5 * strip
                    old = lig['level']
                    lig['level'] *= math.exp(-kdc * dt)
                    for key in list(fc['complexes']):
                        if key[0] == lig['lid'] and old > 0:
                            fc['complexes'][key] *= lig['level'] / old
            fc['ligands'] = [l for l in fc['ligands'] if l['level'] > 1e-6 or l['kind'] == 'covalent']
            # analyte binding (1:1 per ligand site, competing analytes, two-compartment mass transport, regeneration, Eyring temperature dependence)
            self._binding(fc, L, dt, T, f_rate)
            # ligand damage by harsh solutions
            excess = L['s'] - REGEN_TOL['ligand_tolerance_default']
            if excess > 0:
                for lig in fc['ligands']:
                    lig['activity'] *= math.exp(-REGEN_TOL['damage_beta'] * excess * dt)

    def _binding(self, fc, L, dt, T, f_rate):
        comps = fc['complexes']
        analytes = set(L['species']) | {k[1] for k in comps}
        if not analytes or not fc['ligands']:
            return
        rmax = {}
        for lig in fc['ligands']:
            for j in analytes:
                p = self._pair(lig['species'], j)
                if p is None:
                    continue
                sites = p['sites'] if lig['species'] == p['a'] else 1
                rmax[(lig['lid'], j)] = (lig, p, lig['level'] * self._mw(j) / self._mw(lig['species']) * sites * lig['activity'])
        if not rmax:
            return
        occ = {}
        for (lid, j), (lig, p, rm) in rmax.items():
            occ[lid] = occ.get(lid, 0.0) + (comps.get((lid, j), 0.0) / rm if rm > 0 else 0.0)
        for j in analytes:
            sp = self.species[j]
            C = L['species'].get(j, 0.0)
            D = sp['D_m2_s'] * (T / 298.15) * eta_water(298.15) / (eta_water(T) * L['eta_rel'])
            kt = km_flow_cell(D, f_rate) * sp['mw_da'] * 1e6 if f_rate > 0 else 0.0
            terms = [(lig, p, rm) for (lid, jj), (lig, p, rm) in rmax.items() if jj == j]
            num, den = kt * C, kt
            ks = []
            for lig, p, rm in terms:
                ka = eyring(p['ka'], p['dHa'], p['T0'], T)
                kd = eyring(p['kd'], p['dHd'], p['T0'], T)
                if L['s'] > 0:
                    kd += 0.1 * 10 ** (REGEN_TOL['alpha'] * (L['s'] - p['s_req']))
                R = comps.get((lig['lid'], j), 0.0)
                theta = max(0.0, 1.0 - occ[lig['lid']])
                num += kd * R
                den += ka * theta * rm
                ks.append((lig, p, rm, ka, kd, R))
            Cs = num / den if den > 0 else C
            for lig, p, rm, ka, kd, R in ks:
                theta_o = max(0.0, 1.0 - occ[lig['lid']] + (R / rm if rm > 0 else 0.0))
                kobs = ka * Cs + kd
                Req = ka * Cs * theta_o * rm / kobs if kobs > 0 else R
                Rn = Req + (R - Req) * math.exp(-kobs * dt)
                Rn = min(max(Rn, 0.0), rm)
                if Rn > 1e-9 or C > 0:
                    comps[(lig['lid'], j)] = Rn
                else:
                    comps.pop((lig['lid'], j), None)
                occ[lig['lid']] = occ[lig['lid']] - (R / rm if rm > 0 else 0.0) + (Rn / rm if rm > 0 else 0.0)

    def _mass_ru(self, fc):
        return (sum(l['level'] for l in fc['ligands']) + sum(fc['complexes'].values()) + sum(fc['preconc'].values()) + fc['act_ru'] + fc['sa_ru']
                + CAPT['nta_ni_RU'] * fc['ni'])

    def _readings(self, dt, idle):
        out = {}
        c = self._chip_obj() if self._docked() else None
        if c is None or not self.power or self.flow['state'] == 'dry' or 'system_error' in self.faults:
            self._last_reading = {str(i): None for i in range(1, 5)}
            self._record()
            return
        dT = TEMPC_NOISE * _gauss(self.seed, self.tick, 3)
        nw = n_water(self.T_a + dT)
        nref = n_water(298.15)
        sigma = ENG['noise_RU_per_sqrt_hz'] * (1.0 if idle else math.sqrt(1.0 / DT)) * (1.0 + ENG['contamination']['noise_gain'] * self.contamination)
        if 'degasser_failure' in self.faults:
            sigma *= 5.0
        age = self.t - (self.chip['t_dock'] or self.t)
        for fc in c['fcs']:
            i = fc['fc']
            L = self._liq.get(i) or self._sys_props()
            n = nw + L['dn']
            if not RI_DETECT[0] <= n <= RI_DETECT[1]:
                out[str(i)] = None
                continue
            phi = ENG['excluded_volume_per_RU'] * (sum(l['level'] for l in fc['ligands']) + fc['sa_ru'])
            g = c['gains'][i - 1] if c.get('gains') else 1.0
            drift = c['drift_A0'] * DRIFT['tau_s'] * (1.0 - math.exp(-age / DRIFT['tau_s'])) + c['drift'][i - 1] * age
            v = g * (fc['R0'] + S_BULK * (n - nref) * (1.0 - phi) + self._mass_ru(fc)) + drift + sigma * _gauss(self.seed, self.tick, 40 + i)
            for s_ in self.spikes:
                if str(i) in s_['path'].split('-') and s_['t0'] <= self.t <= s_['t1']:
                    v += ENG['air_spike']['depth_RU']
            out[str(i)] = float(v)
        self._last_reading = out
        self._record()

    def _record(self):
        run = self.run
        if run is None or not run['cycles']:
            return
        cyc = run['cycles'][-1]
        if cyc['t_end'] is not None:
            return
        if run['data_rate'] == 10:
            cyc['t'].append(round(self.t - cyc['t_start'], 6))
            for k in cyc['r']:
                cyc['r'][k].append(self._last_reading.get(k))
            return
        for k in cyc['r']:
            v = self._last_reading.get(k)
            a = self._acc.setdefault(k, [0.0, 0, False])
            if v is None:
                a[2] = True
            else:
                a[0] += v
                a[1] += 1
        if self.tick % 10 == 0:
            cyc['t'].append(round(self.t - cyc['t_start'], 6))
            for k in cyc['r']:
                a = self._acc.get(k, [0.0, 0, True])
                cyc['r'][k].append(None if a[2] or a[1] == 0 else a[0] / a[1])
            self._acc = {}

    def _dew_point(self):
        a, b = 17.62, 243.12
        tc = self.T_room - 273.15
        g = math.log(max(self.rh, 1e-3)) + a * tc / (b + tc)
        return b * g / (a - g) + 273.15

    def _warnings(self):
        w = {}
        if not AMBIENT[0] <= self.T_room <= AMBIENT[1]:
            w['AMBIENT_OUT_OF_RANGE'] = '室温超出厂家运行环境18–33 °C'
        if self.power:
            b = self._tube_bottle(self.flow['tube'])
            if b is not None and self.flow['sys_id'] is not None and b['solution_id'] != self.flow['sys_id']:
                w['PRIME_RECOMMENDED'] = '所选瓶中的缓冲液与流路中的不同：应Prime'
            wt = self.bottles.get('water')
            if wt is None:
                w['NO_WATER'] = '没有蒸馏水瓶（不能在没有水的情况下运行）'
            elif self.water_t is not None and self.t - self.water_t > DOC['water_max_age_s']:
                w['WATER_OLD'] = '蒸馏水超过48 h：应换新水'
            if not self.clamp_closed and self.flow['state'] not in ('stopped', 'dry'):
                w['CLAMP_OPEN'] = '蠕动泵管夹未夹紧：缓冲液与水不能送到供液块'
            if self.doors['right_pump'] > 0.3 and self.flow['state'] not in ('stopped', 'dry'):
                w['RIGHT_DOOR_OPEN'] = '右泵舱门开着：蠕动泵可能随时转动（注意手）'
            if self.flow['state'] == 'standby' and self.flow['standby_t0'] is not None and self.t - self.flow['standby_t0'] >= DOC['standby_max_s']:
                self.flow.update(state='stopped', rate=0.0, standby_t0=None)
                self._event('flow', 'STANDBY_EXPIRED', '待机已满7天：流动停止')
                self._standby_expired = True
            if getattr(self, '_standby_expired', False) and self.flow['state'] == 'stopped':
                w['STANDBY_EXPIRED'] = '待机已满7天，流动已停（流路中液体静止）'
            if self._docked() and not self._chip_obj()['normalized'] and self.chip['type'] != 'MAINTENANCE':
                w['NOT_NORMALIZED'] = '对接后尚未Normalize'
            if self._docked() and any(v is None for v in self._last_reading.values()) and self.flow['state'] not in ('dry',):
                w['RESPONSE_OUT_OF_RANGE'] = '有流通池的响应超出量程（折射率1.33–1.40之外或系统错误）'
            if min(self.T_a, self.T_sc) < self._dew_point():
                w['CONDENSATION'] = '温度低于露点：样品舱窗与仪器底部可能凝水（正常）'
            if self.t - self.maint_last['desorb'] > 7 * 86400.0:
                w['MAINTENANCE_DESORB_DUE'] = '距上次Desorb已超过一周'
            if self.t - self.maint_last['desorb_and_sanitize'] > 30 * 86400.0:
                w['MAINTENANCE_SANITIZE_DUE'] = '距上次Desorb and Sanitize已超过一个月'
        ws = self.bottles.get('waste')
        if ws is not None and ws['V'] >= 0.9 * BOTTLE_CAP['waste']:
            w['WASTE_NEARLY_FULL'] = '废液瓶将满（2 L）'
        if 'CLAMP_RELEASE_REQUIRED' in self.warnings and self.clamp_closed:
            w['CLAMP_RELEASE_REQUIRED'] = self.warnings['CLAMP_RELEASE_REQUIRED']
        if self.rack['state'] == 'ejected' and self.rack['config'] is not None and not self.rack['config']['correctly_mounted']:
            w['RACK_NOT_SEATED'] = '托盘上的架子装反：不能送入'
        for code in w:
            if code not in self.warnings:
                self._event('warning', code, w[code])
        self.warnings = w
        if 'system_error' in self.faults:
            self._error('SYSTEM_ERROR', '系统错误（红灯亮）：关机并联系服务')

    # ---------------------------------------------------------------- read
    def _scale_reading(self, b):
        """What an operator reads on the bottle graduation: the volume rounded to the graduation step of that bottle (engineering steps)."""
        step = BOTTLE_GRADUATION_M3[b]
        return float(round(self.bottles[b]['V'] / step) * step)

    def _lamps(self):
        on = self.power
        test = on and self.t_on is not None and self.t - self.t_on < 3.0
        blink = (int(math.floor(self.t * 2.0 + 1e-9)) % 2) == 0
        st = self.chip['state']
        return dict(ready=bool(on), system=bool(test or (on and 'system_error' in self.faults)),
                    temperature=bool(test or (on and (self._stable() or blink))),
                    sensor_chip=bool(test or (on and (st == 'docked' or (st in ('inserted', 'docking', 'ejecting', 'ejected') and blink)))),
                    run=bool(test or (on and self.run is not None)))

    def read(self, visibility='agent'):
        on = self.power
        lamps = self._lamps()
        c = self._chip_obj()
        r = self.run
        last = r['run_id'] if r is not None else (max(self.runs.values(), key=lambda x: x['t0'])['run_id'] if self.runs else None)
        lr = self.runs.get(last) if last else None
        cur = None
        if r is not None and r['cur'] is not None:
            op_ = r['cur']['op']
            kind_ = {'sample_injection': 'sample', 'regeneration_injection': 'regeneration'}.get(op_, r['cur'].get('kind', 'sample'))
            cur = 'inject:' + kind_ if op_ in ('inject', 'sample_injection', 'regeneration_injection') else op_
        rack = self.rack
        rt_in = None
        if rack['state'] == 'ejected' and rack['t_out'] is not None:
            rt_in = max(0.0, self.auto_retract_s - (self.t - rack['t_out']))
        liq = getattr(self, '_liq', {}) or {}
        nw = n_water(self.T_a)
        v = {
            'clock.time_s': self.t, 'plant.configured': self.plant is not None, 'power.enabled': on, 'system.connected': self._connected(),
            **{f'lamp.{k}': lamps[k] for k in LAMPS},
            'temperature.analysis_setpoint_k': float(self.T_a_set), 'temperature.analysis_k': float(self.T_a + TEMPC_NOISE * _gauss(self.seed, self.tick, 5)) if on else None,
            'temperature.analysis_stable': bool(self._stable()), 'temperature.sample_setpoint_k': float(self.T_sc_set),
            'temperature.sample_k': float(self.T_sc + 0.02 * _gauss(self.seed, self.tick, 6)) if on else None,
            'chip.state': self.chip['state'], 'chip.id': self.chip['id'], 'chip.type': self.chip['type'], 'chip.normalized': bool(c['normalized']) if c and self._docked() else False,
            'chip.port_cover_angle_rad': float(self.chip['cover']),
            'response.ru': {str(i): self._last_reading.get(str(i)) for i in range(1, 5)},
            'flow.state': self.flow['state'] if on or self.flow['state'] == 'dry' else ('dry' if self.flow['state'] == 'dry' else 'stopped'),
            'flow.rate_m3_s': float(self.flow['rate'] if on and self.flow['state'] not in ('stopped', 'dry', 'docking', 'ejecting') else 0.0),
            'flow.path': self.flow['path'] if r is not None else None, 'flow.selected_tube': self.flow['tube'],
            'flow.prime_recommended': 'PRIME_RECOMMENDED' in self.warnings,
            'standby.elapsed_s': float(self.t - self.flow['standby_t0']) if self.flow['state'] == 'standby' and self.flow['standby_t0'] is not None else 0.0,
            'bottle.volumes_m3': {b: (self._scale_reading(b) if self.bottles[b] is not None else None) for b in BOTTLE_SLOTS},
            'bottle.tubes_inserted': {b: bool(self.bottles[b] is not None and self.bottles[b]['tube']) for b in ['buffer_a', 'buffer_b', 'buffer_c', 'buffer_d', 'water']},
            'bottle.solutions': {b: (self.bottles[b]['solution_id'] if self.bottles[b] is not None else None) for b in BOTTLE_SLOTS},
            'door.left_pump_angle_rad': float(self.doors['left_pump']), 'door.right_pump_angle_rad': float(self.doors['right_pump']),
            'clamp.closed': bool(self.clamp_closed), 'clamp.lever_angle_rad': float(self.clamp_angle),
            'sample_compartment.door_angle_rad': float(self.sc_door), 'sample_compartment.light': bool(self.sc_light and on),
            'rack.state': rack['state'], 'rack.carriage_position_m': float(rack['pos']), 'rack.auto_retract_in_s': rt_in,
            'rack.config': dict(rack['config']) if rack['config'] else None,
            'rack.positions': {k: dict(solution_id=p['solution_id'], placed_m3=float(p['placed']), capped=bool(p['capped'])) for k, p in sorted(rack['positions'].items())},
            'run.state': r['state'] if r is not None else (lr['state'] if lr else 'idle'), 'run.id': last, 'run.kind': lr['kind'] if lr else None,
            'run.cycle': len(lr['cycles']) if lr else 0, 'run.current_command': cur, 'run.queue_length': len(r['queue']) if r is not None else 0,
            'maintenance.due': sorted(k for k in ('MAINTENANCE_DESORB_DUE', 'MAINTENANCE_SANITIZE_DUE') if k in self.warnings),
            'system_check.last': copy.deepcopy(self.syscheck),
            'operations.running': sorted({op['kind'] for op in self._running_ops()}), 'operation.started_by_other_agent': False,
            'warnings.active': sorted(self.warnings), 'errors.active': sorted(self.errors), 'solutions.available': sorted(self.solutions), 'runs.count': len(self.runs),
            'truth.analysis_temperature_k': float(self.T_a), 'truth.sample_temperature_k': float(self.T_sc),
            'truth.flow_cell_refractive_index': {str(i): float(nw + (liq.get(i) or self._sys_props())['dn']) for i in range(1, 5)},
            'truth.flow_cell_mass_ru': {str(f['fc']): float(self._mass_ru(f)) for f in c['fcs']} if c else {str(i): 0.0 for i in range(1, 5)},
            'truth.complexes_ru': {f'{k[0]}:{k[1]}': float(v_) for f in (c['fcs'] if c else []) for k, v_ in f['complexes'].items()},
            'truth.ligand_activity': {l['lid']: float(l['activity']) for f in (c['fcs'] if c else []) for l in f['ligands']},
            'truth.active_esters': {str(f['fc']): float(f['esters']) for f in c['fcs']} if c else {str(i): 0.0 for i in range(1, 5)},
            'truth.system_buffer': self.flow['sys_id'], 'truth.contamination': float(self.contamination),
            'truth.bottle_volumes_m3': {b: (float(self.bottles[b]['V']) if self.bottles[b] is not None else 0.0) for b in BOTTLE_SLOTS},
            'truth.rack_volumes_m3': {k: float(p['V']) for k, p in sorted(rack['positions'].items())}, 'truth.faults': sorted(self.faults),
        }
        if visibility == 'agent':
            v = {k: x for k, x in v.items() if SLOTS[k]['visibility'] == 'agent'}
        return v

    # ---------------------------------------------------------------- data queries and evaluation
    def _run_of(self, rid):
        r = self.runs.get(rid)
        if r is None:
            raise ActionRejected('UNKNOWN_RUN')
        return r

    def _cycle_of(self, run, k):
        if not 1 <= k <= len(run['cycles']):
            raise ActionRejected('UNKNOWN_CYCLE')
        return run['cycles'][k - 1]

    @staticmethod
    def _curve(cyc, name):
        if '-' in name:
            a, b = name.split('-')
            ra, rb = cyc['r'].get(a), cyc['r'].get(b)
            if ra is None or rb is None:
                raise ActionRejected('CURVE_NOT_RECORDED')
            y = [None if (x is None or z is None) else x - z for x, z in zip(ra, rb)]
        else:
            if name not in cyc['r']:
                raise ActionRejected('CURVE_NOT_RECORDED')
            y = list(cyc['r'][name])
        return np.array(cyc['t'], float), np.array([np.nan if v is None else v for v in y], float)

    def _cmd_runs_list(self):
        return dict(runs=[dict(run_id=r['run_id'], kind=r['kind'], state=r['state'], cycles=len(r['cycles']), started_s=float(r['t0']), finished_s=r['t1'])
                          for r in self.runs.values()])

    def _cmd_data_sensorgram(self, run_id, cycle, curves, max_points=4000):
        run = self._run_of(run_id)
        cyc = self._cycle_of(run, cycle)
        out, t = {}, None
        for name in curves:
            t, y = self._curve(cyc, name)
            out[name] = y
        n = len(t)
        idx = np.unique(np.linspace(0, n - 1, min(n, max_points)).round().astype(int)) if n else np.array([], int)
        return dict(run_id=run_id, cycle=cycle, time_s=[float(x) for x in t[idx]] if n else [],
                    curves={k: [float(v) if np.isfinite(v) else None for v in y[idx]] for k, y in out.items()} if n else {k: [] for k in out},   # out of range -> null
                    injections=[dict(kind=i['kind'], position=i['position'], start_s=float(i['start_s']), end_s=float(i['end_s']), flow_rate_m3_s=float(i['flow_rate_m3_s']))
                                for i in cyc['injections']], keywords=copy.deepcopy(cyc['keywords']), data_rate_hz=run['data_rate'])

    @staticmethod
    def _window_mean(t, y, t_end, w=5.0):
        m = (t >= t_end - w - 1e-9) & (t <= t_end + 1e-9) & np.isfinite(y)
        return float(np.mean(y[m])) if m.any() else None

    def _report_rows(self, run):
        rows = []
        for cyc in run['cycles']:
            names = sorted(cyc['r']) + ([cyc['ref']] if cyc['ref'] != 'none' and all(x in cyc['r'] for x in cyc['ref'].split('-')) else [])
            for name in names:
                t, y = self._curve(cyc, name)
                for inj in cyc['injections']:
                    base = self._window_mean(t, y, inj['start_s'] - 10.0)
                    for label, te in (('baseline', inj['start_s'] - 10.0), ('binding', inj['end_s'] - 5.0), ('stability', inj['end_s'] + 15.0)):
                        val = self._window_mean(t, y, te)
                        if val is None or base is None:
                            continue
                        rows.append(dict(cycle=cyc['index'], curve=name, label=f'{inj["kind"]}:{label}', time_s=float(te), absolute_ru=val, relative_ru=val - base, window_s=5.0))
                for rp in cyc['report_points']:
                    val = self._window_mean(t, y, rp['time_s'])
                    if val is not None:
                        rows.append(dict(cycle=cyc['index'], curve=name, label=rp['label'], time_s=float(rp['time_s']), absolute_ru=val, relative_ru=val, window_s=5.0))
        return rows

    def _cmd_data_report_points(self, run_id):
        return dict(rows=self._report_rows(self._run_of(run_id))[:4096])

    def _conc_of(self, cyc, inj, override=None):
        if override is not None:
            return override
        if cyc['keywords'].get('concentration_mol_m3') is not None and len([i for i in cyc['injections'] if i['kind'] == 'sample']) <= 1:
            return float(cyc['keywords']['concentration_mol_m3'])
        lab = [x for x in inj.get('label', []) if x['concentration_mol_m3'] > 0]
        return float(lab[0]['concentration_mol_m3']) if lab else 0.0

    def _kin_data(self, run, cycles, subtraction, blank_cycles, concentrations):
        blank = None
        if blank_cycles:
            ys = []
            for b in blank_cycles:
                cyc = self._cycle_of(run, b)
                inj = next((i for i in cyc['injections'] if i['kind'] == 'sample'), None)
                if inj is None:
                    raise ActionRejected('NO_SAMPLE_INJECTION')
                t, y = self._curve(cyc, subtraction)
                ys.append((t - inj['start_s'], y - (self._window_mean(t, y, inj['start_s'] - 1.0, 9.0) or 0.0)))
            blank = ys
        data = []
        k = 0
        for ci in cycles:
            cyc = self._cycle_of(run, ci)
            injs = [i for i in cyc['injections'] if i['kind'] == 'sample']
            if not injs:
                raise ActionRejected('NO_SAMPLE_INJECTION')
            t, y = self._curve(cyc, subtraction)
            t0 = injs[0]['start_s']
            base = self._window_mean(t, y, t0 - 1.0, 9.0) or 0.0
            y = y - base
            if blank:
                for bt, by in blank:
                    y = y - np.interp(t - t0, bt, np.nan_to_num(by)) / len(blank)
            nxt = [i['start_s'] for i in cyc['injections'] if i['start_s'] > injs[-1]['end_s'] and i['kind'] != 'sample']
            t_stop = (min(nxt) - 2.0) if nxt else (t[-1] if len(t) else t0)
            m = (t >= t0 - 10.0) & (t <= t_stop) & np.isfinite(y)
            tt, yy = t[m] - t0, y[m]
            if len(tt) > 2 and tt[1] - tt[0] < 0.5:                       # 10 Hz data averaged to 1 s for fitting
                edges = np.arange(np.floor(tt[0]), tt[-1] + 1.0, 1.0)
                idx = np.digitize(tt, edges)
                tt = np.array([tt[idx == j].mean() for j in np.unique(idx)])
                yy = np.array([yy[idx == j].mean() for j in np.unique(idx)])
            segs = []
            for inj in injs:
                cval = concentrations[k] if concentrations and k < len(concentrations) else self._conc_of(cyc, inj)
                k += 1
                segs.append((inj['start_s'] - t0, inj['end_s'] - t0, cval * 1e-3, inj['flow_rate_m3_s']))
            data.append(dict(cycle=ci, t=tt, y=yy, segs=segs))
        if not any(s[2] > 0 for d in data for s in d['segs']):
            raise ActionRejected('NO_CONCENTRATION')
        return data

    @staticmethod
    def _simulate(d, ka, kd, rmax, tc, ri):
        """1:1 two-compartment model in Biacore units (C in M, kt = tc f^(1/3) with f in uL/min) on the curve's time points (0.25 s sub-steps)."""
        t = d['t']
        out = np.zeros(len(t))
        R, tp = 0.0, t[0]
        for i, ti in enumerate(t):
            while tp < ti - 1e-12:
                h = min(0.25, ti - tp)
                tm = tp + 0.5 * h
                C, f = 0.0, d['segs'][0][3]
                for s0, s1, c, fl in d['segs']:
                    if s0 <= tm < s1:
                        C, f = c, fl
                kt = 1e30 if tc is None else tc * (f / UL_MIN) ** (1.0 / 3.0)
                Cs = (kt * C + kd * R) / (kt + ka * max(rmax - R, 0.0)) if kt < 1e29 else C
                kobs = ka * Cs + kd
                Req = ka * Cs * rmax / kobs if kobs > 0 else R
                R = Req + (R - Req) * math.exp(-kobs * h)
                tp += h
            inj = any(s0 <= ti < s1 for s0, s1, _, _ in d['segs'])
            out[i] = R + (ri if inj else 0.0)
        return out

    def _cmd_eval_kinetics(self, run_id, cycles, subtraction, blank_cycles=(), fit_mass_transfer=True, fit_ri=True, concentrations_mol_m3=(), mw_da=None):
        run = self._run_of(run_id)
        data = self._kin_data(run, cycles, subtraction, blank_cycles, list(concentrations_mol_m3))
        if mw_da is None:
            mws = [self._cycle_of(run, ci)['keywords'].get('mw_da') for ci in cycles]
            mw_da = next((m for m in mws if m), None)
        res = fit_kinetics(data, fit_mass_transfer, fit_ri, self._simulate, mw_da)
        return res

    def _cmd_eval_affinity(self, run_id, cycles, subtraction, blank_cycles=(), concentrations_mol_m3=()):
        run = self._run_of(run_id)
        data = self._kin_data(run, cycles, subtraction, blank_cycles, list(concentrations_mol_m3))
        pts = []
        for d in data:
            for s0, s1, c, _ in d['segs']:
                v = self._window_mean(d['t'], d['y'], s1 - 4.0, 5.0)
                if v is not None:
                    pts.append((c * 1e3, v))
        return fit_affinity(pts)

    def _cmd_eval_concentration(self, run_id, mode, subtraction, sample_cycles, standard_cycles=(), standard_concentrations_mol_m3=(), dilution_factors=(), mw_da=None,
                                diffusion_m2_s=None):
        run = self._run_of(run_id)
        if mode == 'calibration':
            if len(standard_cycles) < 4 or len(standard_cycles) != len(standard_concentrations_mol_m3):
                raise ActionRejected('INVALID_ARGUMENT')
            rp = lambda ci: self._binding_level(run, ci, subtraction)                     # noqa: E731
            std = [(c, rp(ci)) for ci, c in zip(standard_cycles, standard_concentrations_mol_m3)]
            smp = [(ci, rp(ci)) for ci in sample_cycles]
            return calibrate_concentration(std, smp, list(dilution_factors))
        if mw_da is None and diffusion_m2_s is None:
            raise ActionRejected('INVALID_ARGUMENT')
        D = diffusion_m2_s if diffusion_m2_s is not None else diffusion_estimate(mw_da, self.T_a)
        mw = mw_da if mw_da is not None else 50000.0
        rows = []
        for ci in sample_cycles:
            cyc = self._cycle_of(run, ci)
            inj = next((i for i in cyc['injections'] if i['kind'] == 'sample'), None)
            if inj is None:
                raise ActionRejected('NO_SAMPLE_INJECTION')
            t, y = self._curve(cyc, subtraction)
            m = (t >= inj['start_s'] + 3.0) & (t <= inj['start_s'] + 13.0) & np.isfinite(y)
            slope = float(np.polyfit(t[m], y[m], 1)[0]) if m.sum() >= 3 else None
            rows.append(dict(cycle=ci, flow=inj['flow_rate_m3_s'], slope=slope))
        return cfca(rows, D, mw, list(dilution_factors))

    def _binding_level(self, run, ci, subtraction):
        cyc = self._cycle_of(run, ci)
        inj = next((i for i in cyc['injections'] if i['kind'] == 'sample'), None)
        if inj is None:
            raise ActionRejected('NO_SAMPLE_INJECTION')
        t, y = self._curve(cyc, subtraction)
        b = self._window_mean(t, y, inj['start_s'] - 10.0)
        v = self._window_mean(t, y, inj['end_s'] + 15.0)
        if b is None or v is None:
            raise ActionRejected('NO_DATA')
        return v - b

    def _cmd_eval_solvent_correction(self, run_id, correction_cycles, sample_cycles, active_fc, reference_fc):
        if active_fc == reference_fc:
            raise ActionRejected('INVALID_ARGUMENT')
        run = self._run_of(run_id)

        def levels(ci):
            cyc = self._cycle_of(run, ci)
            inj = next((i for i in cyc['injections'] if i['kind'] == 'sample'), None)
            if inj is None:
                raise ActionRejected('NO_SAMPLE_INJECTION')
            out = []
            for name in (str(reference_fc), f'{active_fc}-{reference_fc}'):
                t, y = self._curve(cyc, name)
                b = self._window_mean(t, y, inj['start_s'] - 10.0)
                v = self._window_mean(t, y, inj['end_s'] - 5.0)
                if b is None or v is None:
                    raise ActionRejected('NO_DATA')
                out.append(v - b)
            return out
        cal = [levels(ci) for ci in correction_cycles]
        smp = [(ci, *levels(ci)) for ci in sample_cycles]
        return solvent_correction(cal, smp)

    def _cmd_eval_thermodynamics(self, entries):
        return thermodynamics(entries)

    def _cmd_events_read(self, since_s):
        return dict(events=[{k: v for k, v in e.items() if k != '_ref'} for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def _cmd_provenance_read(self):
        return dict(libraries=[dict(name=k, file=f'assets/expansion_batch04/t4_measure/cytiva_biacoret200/{v}', entries=len(_lib(k).get('entries', _lib(k).get('chips',
                    _lib(k).get('crc_solutes', _lib(k).get('documented', {})))))) for k, v in LIBS.items()],
                    species=sorted(f'{k} | {v["name"]}' for k, v in self.species.items())[:400], solutions=LIB_SOLUTIONS, chips=CHIP_TYPES)

    def _cmd_surface_inspect(self):
        c = self._chip_obj()
        fcs = []
        if c is not None:
            nw = n_water(self.T_a)
            for f in c['fcs']:
                L = (getattr(self, '_liq', {}) or {}).get(f['fc']) or self._sys_props()
                fcs.append(dict(fc=f['fc'], ligands=[dict(species=l['species'], kind=l['kind'], level_ru=float(l['level']), activity=float(l['activity'])) for l in f['ligands']],
                                complexes={f'{k[0]}|{k[1]}': float(v) for k, v in f['complexes'].items()}, active_esters=float(f['esters']),
                                excluded_volume=float(ENG['excluded_volume_per_RU'] * (sum(l['level'] for l in f['ligands']) + f['sa_ru'])),
                                refractive_index=float(nw + L['dn']), gain=float(c['gains'][f['fc'] - 1]) if c.get('gains') else 1.0))
        return dict(chip=self.chip['id'], flow_cells=fcs, contamination=float(self.contamination), system_buffer=self.flow['sys_id'])

    def ledger(self):
        return {k: v.value() for k, v in self.led.items()}

    def _cmd_ledger_read(self):
        return dict(ledger=self.ledger())

    def _cmd_fault_inject(self, fault, active):
        self._need_plant()
        (self.faults.add if active else self.faults.discard)(fault)
        if fault == 'power_loss':
            self._update_power()
        if fault == 'system_error' and active:
            self._error('SYSTEM_ERROR', '系统错误（红灯亮）：关机并联系服务')
            if self.run is not None:
                self._end_run('failed', 'SYSTEM_ERROR')
        if fault == 'system_error' and not active:
            self.errors.pop('SYSTEM_ERROR', None)
        self._event('fault', 'FAULT', f'{fault} {"on" if active else "off"}')
        return dict(faults=sorted(self.faults))

    def _cmd_sim_step(self, dt_s):
        self.step(dt_s)
        return dict(time_s=self.t)

    # ---------------------------------------------------------------- invoke
    def invoke(self, capability_id, **a):
        if capability_id not in CAPS:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        validate(CAPS[capability_id]['input_schema'], a)
        snap = None
        if CAPS[capability_id]['kind'] != 'query' and capability_id != 'sim.step':
            snap = self._snapshot()
        try:
            out = getattr(self, '_cmd_' + capability_id.replace('.', '_'))(**a)
        except ActionRejected:
            if snap is not None:
                self._restore(snap)
            raise
        if CAPS[capability_id]['kind'] != 'query' and capability_id != 'sim.step':
            self.revision += 1
        return out

    _NOCOPY = ('_principals', '_requests')

    def _snapshot(self):
        """Atomic rejection: deep copy of the mutable state except the recorded cycle data of finished runs and the session tables."""
        d = {}
        for k, v in self.__dict__.items():
            if k in self._NOCOPY:
                continue
            if k == 'runs':
                d[k] = {rid: (r if r is not self.run else copy.deepcopy(r)) for rid, r in v.items()}
            elif k == 'run':
                continue
            else:
                d[k] = copy.deepcopy(v)
        d['run'] = d['runs'][self.run['run_id']] if self.run is not None else None
        return d

    def _restore(self, snap):
        for k, v in snap.items():
            self.__dict__[k] = v


# ---------------------------------------------------------------- evaluation algorithms (Evaluation Software equivalents)
def _lm(fun, p0, y, lo=None, hi=None, max_iter=60, max_step=None):
    p = np.array(p0, float)
    dp = np.zeros(len(p))
    r = fun(p) - y
    ssr = float(r @ r)
    lam = 1e-3
    for _ in range(max_iter):
        J = np.empty((len(y), len(p)))
        for k in range(len(p)):
            h = 1e-4 * max(1.0, abs(p[k]))
            q = p.copy()
            q[k] += h
            J[:, k] = (fun(q) - y - r) / h
        A = J.T @ J
        g = J.T @ r
        improved = False
        for _ in range(8):
            try:
                dp = -np.linalg.solve(A + lam * np.diag(np.diag(A) + 1e-12), g)
            except np.linalg.LinAlgError:
                lam *= 10
                continue
            if max_step is not None:
                big = np.max(np.abs(dp) / max_step)
                if big > 1.0:
                    dp = dp / big
            q = p + dp
            if lo is not None:
                q = np.maximum(q, lo)
            if hi is not None:
                q = np.minimum(q, hi)
            rq = fun(q) - y
            sq = float(rq @ rq)
            if sq < ssr:
                p, r, ssr = q, rq, sq
                lam = max(lam / 3, 1e-9)
                improved = True
                break
            lam *= 4
        if not improved or abs(float(dp @ dp)) < 1e-14:
            break
    J = np.empty((len(y), len(p)))
    for k in range(len(p)):
        h = 1e-4 * max(1.0, abs(p[k]))
        q = p.copy()
        q[k] += h
        J[:, k] = (fun(q) - y - r) / h
    return p, ssr, J


def fit_kinetics(data, fit_mt, fit_ri, sim, mw=None):
    """Global 1:1 fit with mass transfer (Software Handbook 9.8.1): ka, kd, Rmax, tc global; RI local (bulk step during each injection)."""
    y = np.concatenate([d['y'] for d in data])
    n_c = len(data)
    top = max(data, key=lambda d: max(s[2] for s in d['segs']))
    ymax = float(np.nanmax(top['y'])) if len(top['y']) else 1.0
    # kd start from the dissociation phase of the highest curve
    s1 = top['segs'][-1][1]
    m = (top['t'] > s1 + 2.0) & (top['t'] < s1 + 120.0) & (top['y'] > 0.05 * max(ymax, 1e-6))
    kd0 = 1e-3
    if m.sum() > 5:
        sl = np.polyfit(top['t'][m], np.log(np.maximum(top['y'][m], 1e-9)), 1)[0]
        kd0 = float(np.clip(-sl, 1e-5, 1.0))
    rmax0 = max(1.0, 1.15 * ymax)
    c_top = max(s[2] for s in top['segs'])
    s0 = top['segs'][0][0]
    mi = (top['t'] >= s0 + 1.0) & (top['t'] <= s0 + 8.0) & np.isfinite(top['y'])
    slope0 = float(np.polyfit(top['t'][mi], top['y'][mi], 1)[0]) if mi.sum() >= 3 else 0.0
    ka0 = float(np.clip(slope0 / max(c_top * rmax0, 1e-30), 1e3, 1e8)) if slope0 > 0 else 1e5
    f0 = top['segs'][0][3]
    tc0 = (km_flow_cell(diffusion_estimate(mw), f0) * mw * 1e9 / (f0 / UL_MIN) ** (1.0 / 3.0)) if mw else 1e8
    npar = 3 + (1 if fit_mt else 0) + (n_c if fit_ri else 0)

    def unpack(p):
        ka, kd, rmax = 10 ** p[0], 10 ** p[1], p[2]
        tc = 10 ** p[3] if fit_mt else None
        ri = list(p[4 if fit_mt else 3:]) if fit_ri else [0.0] * n_c
        return ka, kd, rmax, tc, ri

    def fun(p):
        ka, kd, rmax, tc, ri = unpack(p)
        return np.concatenate([sim(d, ka, kd, rmax, tc, ri[i]) for i, d in enumerate(data)])
    best = None
    for lka in np.log10(ka0) + np.array([-1.0, -0.5, 0.0, 0.5, 1.0]):
        for ltc in ([math.log10(tc0), math.log10(tc0) + 1.0] if fit_mt else [None]):
            p0 = [lka, math.log10(kd0), rmax0] + ([ltc] if fit_mt else []) + ([0.0] * n_c if fit_ri else [])
            r = fun(np.array(p0)) - y
            s = float(r @ r)
            if best is None or s < best[0]:
                best = (s, p0)
    lo = np.array([1.0, -7.0, 0.0] + ([4.0] if fit_mt else []) + ([-1e5] * n_c if fit_ri else []))
    hi = np.array([10.0, 1.0, 1e6] + ([10.5] if fit_mt else []) + ([1e5] * n_c if fit_ri else []))
    step = np.array([0.5, 0.5, max(rmax0, 10.0)] + ([0.5] if fit_mt else []) + ([max(5.0, 0.1 * rmax0)] * n_c if fit_ri else []))
    p, ssr, J = _lm(fun, best[1], y, lo, hi, max_iter=80, max_step=step)
    dof = max(1, len(y) - npar)
    chi2 = ssr / dof
    try:
        cov = np.linalg.inv(J.T @ J) * chi2
        se_raw = np.sqrt(np.maximum(np.diag(cov), 0.0))
    except np.linalg.LinAlgError:
        se_raw = np.full(len(p), np.nan)
    ka, kd, rmax, tc, ri = unpack(p)
    vals = [('ka', ka, 'M^-1 s^-1', ka * math.log(10) * se_raw[0]), ('kd', kd, 's^-1', kd * math.log(10) * se_raw[1]), ('Rmax', rmax, 'RU', se_raw[2])]
    if fit_mt:
        vals.append(('tc', tc, 'RU M^-1 s^-1 (uL/min)^-1/3', tc * math.log(10) * se_raw[3]))
    pars = []
    for name, v, unit, se in vals:
        se_ = float(se) if np.isfinite(se) else None
        pars.append(dict(name=name, value=float(v), unit=unit, se=se_, t_value=(float(v / se_) if se_ else None), scope='global', fixed=False))
    for i, x in enumerate(ri):
        se = se_raw[(4 if fit_mt else 3) + i] if fit_ri else None
        pars.append(dict(name=f'RI[{data[i]["cycle"]}]', value=float(x), unit='RU', se=float(se) if se is not None and np.isfinite(se) else None,
                         t_value=None, scope='local', fixed=not fit_ri))
    # uniqueness (U-value equivalent): chi2 change when ka and kd are scaled together by M = 2 (KD fixed) with Rmax re-fitted
    q = []
    for M in (2.0, 0.5):
        def f2(pp, M=M):
            return fun(np.concatenate([[p[0] + math.log10(M), p[1] + math.log10(M)], pp, p[3:]]))
        pp, s2, _ = _lm(f2, [p[2]], y, np.array([0.0]), np.array([1e6]), max_iter=20)
        q.append(s2 / max(ssr, 1e-12))
    qmin = min(q)
    u = float(100.0 / (1.0 + 10.0 * max(0.0, qmin - 1.0)))
    resid = fun(p) - y
    rng = float(np.nanmax(y) - np.nanmin(y)) if len(y) else 1.0
    lim = max(1.0, 0.02 * rng)
    qc = []
    lmw = mw is not None and mw < 5000.0
    ka_hi = 5e7 if lmw else 3e9
    st = 'pass' if 1e3 <= ka <= ka_hi and 1e-5 <= kd <= 1.0 else 'fail'
    if st == 'pass' and (ka > ka_hi / 3 or kd < 3e-5 or kd > 0.3 or ka < 3e3):
        st = 'warning'
    qc.append(dict(item='kinetic_constants_range', status=st, detail_zh=f'ka {ka:.3g} M⁻¹s⁻¹（{"小分子" if lmw else "蛋白"}上限{ka_hi:.0e}），kd {kd:.3g} s⁻¹（10⁻⁵–1 s⁻¹）'))
    qc.append(dict(item='parameter_uniqueness', status='pass' if u < 15 else 'warning' if u <= 25 else 'fail', detail_zh=f'U值{u:.0f}（<15唯一，>25相关）'))
    big_ri = [x for x in ri if abs(x) > max(2.0, 0.05 * abs(rmax))] if fit_ri else []
    qc.append(dict(item='bulk_refractive_index', status='not_evaluated' if not fit_ri else 'warning' if big_ri else 'pass',
                   detail_zh='拟合给出明显的体折射率贡献' if big_ri else '体折射率贡献小'))
    top_i = data.index(top)
    ytop = top['y']
    end_assoc = sim(top, ka, kd, rmax, tc, 0.0)
    m_d = (top['t'] > s1) & np.isfinite(ytop)
    fall = None
    if m_d.sum() > 3:
        y_end_inj = float(np.interp(s1 - 1.0, top['t'], end_assoc))
        fall = 1.0 - float(end_assoc[m_d][-1]) / max(y_end_inj, 1e-9)
    curv_ok = fall is not None and fall >= 0.10
    qc.append(dict(item='sensorgram_curvature', status='pass' if curv_ok else 'warning',
                   detail_zh=f'最高浓度解离期下降{(fall or 0) * 100:.0f} %（应≥10–15 %）'))
    frac = float(np.mean(np.abs(resid) <= lim)) if len(resid) else 0.0
    qc.append(dict(item='residuals', status='pass' if frac >= 0.95 else 'warning', detail_zh=f'{frac * 100:.0f} %的残差在±{lim:.1f} RU以内'))
    tvals = [x['t_value'] for x in pars if x['scope'] == 'global' and x['t_value'] is not None]
    qc.append(dict(item='t_values', status='pass' if tvals and min(abs(v) for v in tvals) > 10 else 'warning', detail_zh='全局参数T值>10' if tvals else '无法估计'))
    if fit_mt:
        f_ref = data[0]['segs'][0][3]
        kt = tc * (f_ref / UL_MIN) ** (1.0 / 3.0)
        ratio = ka * rmax / kt
        qc.append(dict(item='mass_transfer', status='pass' if ratio < 1.0 else 'warning', detail_zh=f'ka·Rmax/kt = {ratio:.2g}（>1时结合部分受传质限制，见Check Kinetic Data）'))
    kt_val = tc * (data[0]['segs'][0][3] / UL_MIN) ** (1.0 / 3.0) if fit_mt else None
    return dict(model='1:1 binding with mass transfer' if fit_mt else '1:1 binding', parameters=pars, ka_m3_mol_s=float(ka * 1e-3), kd_s=float(kd), KD_mol_m3=float(kd / ka * 1e3),
                rmax_ru=float(rmax), kt_ru_m3_mol_s=None if kt_val is None else float(kt_val * 1e-3), tc=None if tc is None else float(tc), chi2_ru2=float(chi2),
                u_value=u, quality=qc, curves=n_c, points=int(len(y)))


def fit_affinity(pts):
    if len(pts) < 3:
        raise ActionRejected('NO_DATA')
    c = np.array([p[0] for p in pts])
    y = np.array([p[1] for p in pts])
    cpos = c[c > 0]
    if not len(cpos):
        raise ActionRejected('NO_CONCENTRATION')

    def fun(p):
        kd, rm, off = 10 ** p[0], p[1], p[2]
        return rm * c / (kd + c) + off
    best = None
    for lk in np.log10(cpos.min()) + np.arange(-1, 4):
        p0 = [lk, max(1.0, 1.2 * y.max()), 0.0]
        r = fun(np.array(p0)) - y
        if best is None or float(r @ r) < best[0]:
            best = (float(r @ r), p0)
    p, ssr, _ = _lm(fun, best[1], y, np.array([-12.0, 0.0, -1e4]), np.array([6.0, 1e6, 1e4]))
    kd = 10 ** p[0]
    chi2 = ssr / max(1, len(y) - 3)
    qc = [dict(item='kd_below_half_top_concentration', status='pass' if kd < 0.5 * c.max() else 'warning',
               detail_zh=f'KD {kd:.3g} mol/m³，最高浓度{c.max():.3g} mol/m³（KD应小于最高浓度的一半）')]
    return dict(KD_mol_m3=float(kd), rmax_ru=float(p[1]), offset_ru=float(p[2]), chi2_ru2=float(chi2),
                points=[dict(concentration_mol_m3=float(ci), req_ru=float(yi), fit_ru=float(fi)) for ci, yi, fi in zip(c, y, fun(p))], quality=qc)


def calibrate_concentration(std, smp, dil):
    c = np.array([s[0] for s in std], float)
    y = np.array([s[1] for s in std], float)
    if (c <= 0).any():
        raise ActionRejected('INVALID_ARGUMENT')

    def fun(p):
        lo_, hi_, lc50, h = p
        return hi_ + (lo_ - hi_) / (1.0 + (c / 10 ** lc50) ** h)
    p0 = [min(y.min(), 0.0), y.max() * 1.5, math.log10(np.median(c) * 2.0), 1.0]
    p, ssr, _ = _lm(fun, p0, y, np.array([-1e5, -1e5, -12.0, 0.3]), np.array([1e5, 1e6, 6.0, 3.0]))
    lo_, hi_, lc50, h = p
    out = []
    for i, (ci, v) in enumerate(smp):
        f = dil[i] if i < len(dil) else 1.0
        x = None
        detail = '在标准曲线范围内'
        if not min(y) <= v <= max(y):
            detail = '响应超出标准曲线范围'
        frac = (lo_ - hi_) / (v - hi_) - 1.0 if abs(v - hi_) > 1e-12 else None
        if frac is not None and frac > 0:
            x = float(10 ** lc50 * frac ** (1.0 / h) * f)
        out.append(dict(cycle=ci, concentration_mol_m3=x, report_ru=float(v), detail_zh=detail))
    qc = [dict(item='samples_within_range', status='pass' if all(o['detail_zh'] == '在标准曲线范围内' for o in out) else 'warning', detail_zh='样品响应与标准曲线范围比较')]
    return dict(mode='calibration', samples=out, calibration=dict(model='4-parameter logistic', parameters=[float(x) for x in p], chi2_ru2=float(ssr / max(1, len(y) - 4))),
                quality=qc)


def cfca(rows, D, mw, dil):
    """Calibration-free concentration analysis: initial binding rates at two flow rates with km from the documented flow-cell expression
    (s = C kt K / (kt + K), K = ka Rmax unknown)."""
    if len(rows) < 2 or len(rows) % 2:
        raise ActionRejected('INVALID_ARGUMENT')
    out, qc_ok = [], True
    for i in range(0, len(rows), 2):
        a, b = sorted(rows[i:i + 2], key=lambda r: r['flow'])
        detail, C = '', None
        if a['slope'] is None or b['slope'] is None or a['slope'] <= 0 or b['flow'] <= a['flow'] * 1.5:
            detail, qc_ok = '初始速率不可用（须两个不同流速、正的初始斜率）', False
        else:
            kt1 = km_flow_cell(D, a['flow']) * mw * 1e6
            kt2 = km_flow_cell(D, b['flow']) * mw * 1e6
            r = b['slope'] / a['slope']
            if not 1.02 < r < 0.98 * kt2 / kt1:
                detail, qc_ok = f'速率比{r:.3f}：传质限制不足或过强，无法确定', False
            else:
                K = kt1 * kt2 * (1.0 - r) / (r * kt1 - kt2)
                C = a['slope'] * (kt1 + K) / (kt1 * K)
                f = dil[i // 2] if i // 2 < len(dil) else 1.0
                C *= f
                detail = f'速率比{r:.3f}（最大{kt2 / kt1:.3f}），传质限制分数{K / (kt1 + K):.2f}'
        out.append(dict(cycle=a['cycle'], concentration_mol_m3=None if C is None else float(C), report_ru=float(a['slope'] or 0.0), detail_zh=detail))
    return dict(mode='cfca', samples=out, calibration=None, quality=[dict(item='transport_limitation', status='pass' if qc_ok else 'fail', detail_zh='两个流速的初始速率比须落在1与(f2/f1)^(1/3)之间')])


def solvent_correction(cal, smp):
    x = np.array([c[0] for c in cal])
    y = np.array([c[1] for c in cal])
    coef = np.polyfit(x, y, 2)
    chi2 = float(np.mean((np.polyval(coef, x) - y) ** 2))
    lo, hi = float(x.min()), float(x.max())
    ext = 0.1 * (hi - lo)
    rows = []
    for ci, xs, ys in smp:
        ok = lo - ext <= xs <= hi + ext
        corr = float(np.polyval(coef, xs)) if ok else None
        rows.append(dict(cycle=ci, reference_ru=float(xs), uncorrected_ru=float(ys), correction_ru=corr, corrected_ru=None if corr is None else float(ys - corr)))
    return dict(coefficients=[float(v) for v in coef], chi2_ru2=chi2, range_ru=[lo, hi], corrected=rows,
                quality=[dict(item='correction_curve_chi2', status='pass' if chi2 < 2.0 else 'warning', detail_zh=f'校正曲线Chi² {chi2:.2f} RU²（应<2）')])


def thermodynamics(entries):
    T = np.array([e['temperature_k'] for e in entries])
    if len(set(np.round(T, 3))) < 3:
        raise ActionRejected('INVALID_ARGUMENT')
    KD = np.array([e['KD_mol_m3'] * 1e-3 for e in entries])               # M (standard state 1 M)
    lnK = np.log(KD)
    T0 = 298.15
    if len(set(np.round(T, 3))) >= 4:
        A = np.column_stack([1.0 / (R_GAS * T), -np.ones_like(T) / R_GAS, ((T - T0) / (R_GAS * T)) - np.log(T / T0) / R_GAS])
        (dH, dS, dCp), *_ = np.linalg.lstsq(A, lnK, rcond=None)
    else:
        A = np.column_stack([1.0 / (R_GAS * T), -np.ones_like(T) / R_GAS])
        (dH, dS), *_ = np.linalg.lstsq(A, lnK, rcond=None)
        dCp = None
    fit = A @ (np.array([dH, dS] + ([dCp] if dCp is not None else [])))
    r2 = 1.0 - float(np.sum((lnK - fit) ** 2)) / max(float(np.sum((lnK - lnK.mean()) ** 2)), 1e-30)

    def eyring_fit(key):
        if not all(key in e for e in entries):
            return None
        k = np.array([e[key] * (1e3 if key == 'ka_m3_mol_s' else 1.0) for e in entries])
        y = np.log(k * H_PLANCK / (KB * T))
        B = np.column_stack([-1.0 / (R_GAS * T), np.ones_like(T) / R_GAS])
        (dHt, dSt), *_ = np.linalg.lstsq(B, y, rcond=None)
        return dict(dH_j_mol=float(dHt), T_dS298_j_mol=float(T0 * dSt), dG298_j_mol=float(dHt - T0 * dSt))
    dG = dH - T0 * dS + (0.0 if dCp is None else 0.0)
    return dict(dH_j_mol=float(dH), dS_j_k_mol=float(dS), dG298_j_mol=float(dG), dCp_j_k_mol=None if dCp is None else float(dCp),
                association=eyring_fit('ka_m3_mol_s'), dissociation=eyring_fit('kd_s'),
                quality=[dict(item='van_t_hoff_linearity', status='pass' if r2 > 0.98 else 'warning', detail_zh=f'van \'t Hoff拟合R² {r2:.4f}')])


# ---------------------------------------------------------------- session (agent / evaluator isolation, idempotency)
ASYNC = {'chip.dock', 'chip.eject', 'flow.prime', 'detector.normalize', 'maintenance.run', 'system.check', 'system.shutdown', 'wizard.immobilization',
         'wizard.ph_scouting', 'method.run'}
_DATA_QUERIES = {'data.sensorgram', 'data.report_points', 'eval.kinetics', 'eval.affinity', 'eval.concentration', 'eval.solvent_correction'}
_CONTROL_GUARDED = {k for k, v in CAPS.items() if v['visibility'] == 'agent' and v['kind'] in ('device_command', 'robot_skill') and k not in UNSUPPORTED
                    and k != 'solution.prepare'}


class CytivaBiacoret200Session:
    def __init__(self, host, principal_id):
        self._host = host
        self._principal = principal_id

    @property
    def _role(self):
        return self._host._principals[self._principal]

    def _target(self, instance_id):
        if not isinstance(instance_id, str) or instance_id != self._host.instance_id:
            raise ActionRejected('UNKNOWN_INSTANCE')

    def describe(self, *, instance_id=None, asset_type_id=None):
        if (instance_id is None) == (asset_type_id is None):
            raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None:
            self._target(instance_id)
        if asset_type_id is not None and asset_type_id != ASSET_TYPE_ID:
            raise ActionRejected('UNKNOWN_INSTANCE')
        meta = copy.deepcopy(export_cytiva_biacoret200_contract_metadata())
        caps = {k: v for k, v in meta['capabilities'].items() if v['visibility'] == 'agent' or self._role != 'agent'}
        slots = {k: v for k, v in meta['state_slots'].items() if v['visibility'] == 'agent' or self._role != 'agent'}
        if self._role == 'agent':
            for c in caps.values():
                c['state_effects'] = [e for e in c['state_effects'] if e['state_id'] in slots]
                c['observes'] = [x for x in c['observes'] if x in slots]
                for pre in c['preconditions']:
                    pre['state_ids'] = [x for x in pre['state_ids'] if x in slots]
                c['execution']['completion_state_ids'] = [x for x in c['execution']['completion_state_ids'] if x in slots]
            for m in meta['mechanisms'].values():
                m['state_ids'] = [x for x in m['state_ids'] if x in slots]
        return dict(protocol_version=PROTOCOL_VERSION, asset_type_id=ASSET_TYPE_ID, asset_version='1.0.0', capabilities=caps, state_slots=slots)

    def _foreign(self, object_id):
        owner = self._host._owners.get(object_id)
        return owner is not None and owner != self._principal and self._host._principals.get(owner) == 'agent'

    def _foreign_op(self):
        h = self._host
        return any(self._foreign(op['op_id']) for op in h._running_ops()) or (h.run is not None and self._foreign('run:' + h.run['run_id']))

    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id)
        h = self._host
        values = h.read(self._role)
        values['operation.started_by_other_agent'] = self._role == 'agent' and self._foreign_op()
        if self._role == 'agent':
            values['solutions.available'] = [x for x in values['solutions.available'] if not self._foreign('solution:' + x)]
        ids = list(values) if state_ids is None else state_ids
        if not isinstance(ids, list) or any(not isinstance(k, str) for k in ids):
            raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in values for k in ids):
            raise ActionRejected('ACCESS_DENIED')
        return dict(instance_id=instance_id, status='succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                    observations={k: dict(value=copy.deepcopy(values[k]), unit=SLOTS[k]['unit'],
                                          quality='unavailable' if values[k] is None else 'estimated' if SLOTS[k]['origin'] in ('sensor', 'derived') else 'valid',
                                          sample_time_s=h.time_s, sample_window_s=None)
                                  for k in ids})

    def _guard(self, capability_id, arguments):
        if self._role != 'agent':
            return
        h = self._host
        if capability_id in _CONTROL_GUARDED and self._foreign_op():
            if (capability_id == 'power.set' and arguments.get('enabled') is True and not h.power) or (capability_id == 'power.press_switch' and not h.switch_on):
                return
            raise ActionRejected('ACCESS_DENIED')
        sid = arguments.get('solution_id')
        if isinstance(sid, str) and self._foreign('solution:' + sid):
            raise ActionRejected('ACCESS_DENIED')
        if capability_id == 'solution.prepare' and any(self._foreign('solution:' + s['source_id']) for s in arguments.get('sources', [])):
            raise ActionRejected('ACCESS_DENIED')
        if capability_id in _DATA_QUERIES:
            rid = arguments.get('run_id')
            if isinstance(rid, str) and self._foreign('run:' + rid):
                raise ActionRejected('ACCESS_DENIED')

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        h = self._host
        key = digest = None
        try:
            self._target(instance_id)
            if not isinstance(request_id, str) or not request_id:
                raise ActionRejected('INVALID_ARGUMENT')
            key = (self._principal, request_id)
            try:
                digest = json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False)
            except (TypeError, ValueError):
                digest = None
            if key in h._requests:
                old, response = h._requests[key]
                if digest is None or old != digest or not isinstance(response, dict):
                    raise ActionRejected('REQUEST_CONFLICT')
                return copy.deepcopy(response)
            if not isinstance(capability_id, str) or capability_id not in CAPS:
                raise ActionRejected('UNKNOWN_CAPABILITY')
            if self._role == 'agent' and CAPS[capability_id]['visibility'] != 'agent':
                raise ActionRejected('ACCESS_DENIED')
            if not isinstance(arguments, dict):
                raise ActionRejected('INVALID_ARGUMENT')
            validate(CAPS[capability_id]['input_schema'], arguments)
            self._guard(capability_id, arguments)
            h._caller = self._principal
            try:
                result = h.invoke(capability_id, **arguments)
            finally:
                h._caller = None
            if self._role == 'agent':
                if capability_id == 'runs.list':
                    result = dict(runs=[r for r in result['runs'] if not self._foreign('run:' + r['run_id'])])
                elif capability_id == 'events.read':
                    result = dict(events=[{k: v for k, v in e.items() if k != '_ref'} for e in h.events
                                          if e['time_s'] >= arguments['since_s'] and not self._foreign(str(e.get('_ref')))
                                          and e['kind'] not in ('fault', 'truth')], dropped=h.events_dropped)
            operation = result.get('operation_id') if capability_id in ASYNC else None
            response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=operation,
                            status='accepted' if operation else 'succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                            result=copy.deepcopy(result), observations={}, error=None)
            h._requests[key] = (digest, copy.deepcopy(response))
            return response
        except ActionRejected as exc:
            response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=None,
                            status='unsupported' if str(exc) == 'UNSUPPORTED' else 'rejected', state_revision=h.revision,
                            clock={'domain': 'simulation', 'time_s': h.time_s}, result={}, observations={},
                            error=dict(code=str(exc), message=ERROR_ZH.get(str(exc), str(exc)), retryable=False, side_effects='none'))
            if key is not None and digest is not None and key not in h._requests:
                h._requests[key] = (digest, copy.deepcopy(response))
            return response

    def write(self, *, instance_id, state_id, value, request_id):
        self._target(instance_id)
        raise ActionRejected('ACCESS_DENIED')

    def poll(self, operation_id):
        h = self._host
        if not isinstance(operation_id, str):
            raise ActionRejected('INVALID_ARGUMENT')
        op = h.operations.get(operation_id)
        if op is None:
            raise ActionRejected('UNKNOWN_OPERATION')
        if h._owners.get(operation_id) != self._principal:
            raise ActionRejected('ACCESS_DENIED')
        res = dict(kind=op['kind'], error=op['error'], **copy.deepcopy(op.get('result', {})))
        if op.get('run_id'):
            r = h.runs.get(op['run_id'])
            res.update(run_id=op['run_id'], run_state=r['state'] if r else None, cycles=len(r['cycles']) if r else 0)
        return dict(operation_id=operation_id, status=op['phase'], result=res)

    def cancel(self, operation_id, request_id):
        h = self._host
        if not isinstance(request_id, str) or not request_id:
            raise ActionRejected('INVALID_ARGUMENT')
        key = (self._principal, request_id)
        digest = json.dumps(['cancel', operation_id]) if isinstance(operation_id, str) else None
        if key in h._requests:
            old, response = h._requests[key]
            if digest is None or old != digest:
                raise ActionRejected('REQUEST_CONFLICT')
            if isinstance(response, str):
                raise ActionRejected(response)
            return copy.deepcopy(response)
        try:
            self.poll(operation_id)
            op = h.operations[operation_id]
            if op['phase'] != 'running':
                raise ActionRejected('NOT_RUNNING')
            if op['kind'] in ('chip.dock', 'chip.eject'):
                raise ActionRejected('NOT_CANCELLABLE')
            h._cancel_op(op)
        except ActionRejected as exc:
            if digest is not None:
                h._requests[key] = (digest, str(exc))
            raise
        h.revision += 1
        response = dict(protocol_version=PROTOCOL_VERSION, instance_id=h.instance_id, request_id=request_id, operation_id=operation_id, status='succeeded',
                        state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s}, result=dict(phase=op['phase']), observations={}, error=None)
        h._requests[key] = (digest, copy.deepcopy(response))
        return response


# ---------------------------------------------------------------- contract metadata
ERROR_ZH = {
    'INVALID_ARGUMENT': '参数不符合输入模式或取值范围（或组合不成立）', 'REQUEST_CONFLICT': '同一request_id对应不同的调用', 'UNKNOWN_INSTANCE': '实例不存在',
    'UNKNOWN_CAPABILITY': '能力不存在', 'UNSUPPORTED': '本配置不支持该能力', 'ACCESS_DENIED': '无权访问（真值、另一个agent的溶液、运行或进行中的操作）',
    'PLANT_REQUIRED': '尚未声明实验室条件', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'MAINS_OUT_OF_RANGE': '供电不在100–240 V ±10 %（或断电）',
    'POWER_OFF': '仪器未通电', 'NOT_CONNECTED': '控制软件尚未与仪器连接（开机后约30 s）', 'SAMPLE_COMPARTMENT_OPEN': '样品舱门开着',
    'RUN_IN_PROGRESS': '运行进行中', 'INSTRUMENT_BUSY': '对接、退出、Prime、Normalize、维护、System Check或Shutdown进行中', 'BELOW_AMBIENT_LIMIT': '设定低于室温减20 °C（分析）或15 °C（样品舱）',
    'ITEM_EXISTS': 'ID已存在', 'TOO_MANY_ITEMS': '数量已达上限', 'UNKNOWN_SPECIES': '物质不存在', 'UNKNOWN_SOLUTION': '溶液或储备液不存在',
    'INSUFFICIENT_SOLUTION': '溶液体积不够', 'VOLUME_OUT_OF_RANGE': '体积超出容器或配制范围', 'RACK_NOT_EJECTED': '托盘没有弹出', 'UNKNOWN_POSITION': '当前托盘配置中没有这个位置',
    'POSITION_OCCUPIED': '位置已放了溶液', 'POSITION_EMPTY': '位置没有溶液', 'RACK_NOT_INSIDE': '托盘不在仪器内（或正在移动）', 'RACK_NOT_SEATED': '架子装反，托盘送不进',
    'NO_BOTTLE': '没有这个瓶', 'CLAMP_MOVING': '管夹手柄正在扳动', 'DOOR_CLOSED': '门没开', 'POWER_ON': '须先关电源', 'RACK_NOT_EMPTY': '须先取出微孔板与试剂架',
    'ALREADY_OPEN': '已打开', 'ALREADY_CLOSED': '已关上', 'CHIP_DOCKED': '芯片已对接（先Eject Chip）', 'PORT_CLOSED': '芯片口盖关着（先Insert Chip打开）',
    'CHIP_PRESENT': '芯片口中已有芯片', 'CHIP_UNKNOWN': '本仪器没用过这个Chip id', 'CHIP_TYPE_MISMATCH': '复用芯片的类型不符', 'CHIP_ID_EXISTS': '新芯片的Chip id须唯一',
    'NO_CHIP': '芯片口中没有可对接/可取出的芯片', 'COVER_OPEN': '芯片口盖没关好', 'CHIP_NOT_DOCKED': '没有对接芯片', 'NO_BUFFER': '所需管的瓶不在、管没插或瓶空',
    'NO_WATER': '没有蒸馏水瓶', 'NO_WASTE_BOTTLE': '没有废液瓶', 'NOT_IN_STANDBY': '不在待机', 'MAINTENANCE_CHIP': 'Normalize不能用Maintenance芯片',
    'WRONG_SOLUTION': '位置或瓶中的溶液不是该程序要求的', 'MISSING_SOLUTION': '没有给出该程序要求的溶液位置', 'DESORB_BELOW_20C': 'BIAdesorb 1在20 °C以下析出（用0.5 %十二烷基硫酸锂）',
    'RACK_NOT_REMOVED': 'Shutdown前须弹出并取出托盘内容', 'WASTE_FULL': '废液瓶将满，先倒空', 'REFERENCE_NOT_IN_PATH': '参比扣除的流通池不在流路中',
    'DETECTION_MISMATCH': '检测设置（Single/Dual/Multi）与流路不符', 'TEMPERATURE_NOT_STABLE': '分析温度未稳定（可选ignore_temperature）', 'NO_MANUAL_RUN': '没有进行中的手动运行',
    'INJECTION_VOLUME_OUT_OF_RANGE': '注入体积（流速×接触时间）须在2–350 µL（高黏度溶液接触时间≤600 s）', 'FLOW_PATH_NOT_ALLOWED': '周期内只能在起始流路范围内改流路',
    'NO_SUCH_COMMAND': '队列中没有这个命令', 'NO_RUN': '没有进行中的运行', 'COMMAND_NOT_STOPPABLE': '弹出托盘命令不能停止', 'NOT_PAUSED': '运行没有暂停',
    'CHEMISTRY_NOT_SUPPORTED': '芯片类型不支持所选固定化化学', 'MIX_POSITION_OCCUPIED': 'EDC/NHS混合位须为空位', 'UNKNOWN_RUN': '运行不存在', 'UNKNOWN_CYCLE': '周期不存在',
    'CURVE_NOT_RECORDED': '该周期没有记录这条曲线', 'NO_SAMPLE_INJECTION': '周期中没有样品注入', 'NO_CONCENTRATION': '没有浓度（关键词或溶液标签）',
    'NO_DATA': '数据不足', 'UNKNOWN_OPERATION': '操作不存在', 'NOT_RUNNING': '操作不在进行中', 'NOT_CANCELLABLE': '对接/退出不能取消',
    'POWER_OFF_INTERRUPT': '断电中止', 'BUFFER_EMPTY': '缓冲液瓶空：注射泵抽空，运行失败', 'SYSTEM_ERROR': '系统错误未清除（红灯亮）：不能开始运行与流路操作',
}
_P = ['PLANT_REQUIRED']
_CONN = ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_CONNECTED']
_BUSY = ['SYSTEM_ERROR', 'RUN_IN_PROGRESS', 'INSTRUMENT_BUSY']
_LIQ_E = ['NO_BUFFER', 'NO_WATER', 'NO_WASTE_BOTTLE']
_POS_E = ['POSITION_EMPTY', 'RACK_NOT_INSIDE', 'WRONG_SOLUTION', 'INSUFFICIENT_SOLUTION']
_RUN_E = _CONN + ['CHIP_NOT_DOCKED'] + _BUSY + ['RACK_NOT_INSIDE'] + _LIQ_E + ['WASTE_FULL', 'ITEM_EXISTS', 'TOO_MANY_ITEMS']
_EVAL_E = ['UNKNOWN_RUN', 'UNKNOWN_CYCLE', 'CURVE_NOT_RECORDED', 'NO_SAMPLE_INJECTION']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED'], 'power.set': _P + ['MAINS_OUT_OF_RANGE', 'SAMPLE_COMPARTMENT_OPEN'], 'power.press_switch': _P + ['MAINS_OUT_OF_RANGE', 'SAMPLE_COMPARTMENT_OPEN'],
    'temperature.set': _CONN + ['RUN_IN_PROGRESS', 'INVALID_ARGUMENT', 'BELOW_AMBIENT_LIMIT'],
    'kit.provide': _P + ['TOO_MANY_ITEMS'], 'stock.provide': _P + ['UNKNOWN_SPECIES', 'ITEM_EXISTS', 'TOO_MANY_ITEMS'], 'species.define': _P + ['ITEM_EXISTS'],
    'interaction.define': _P + ['ITEM_EXISTS', 'UNKNOWN_SPECIES'],
    'solution.prepare': _P + ['ITEM_EXISTS', 'TOO_MANY_ITEMS', 'UNKNOWN_SOLUTION', 'INSUFFICIENT_SOLUTION', 'VOLUME_OUT_OF_RANGE'],
    'rack.configure': _P + ['RACK_NOT_EJECTED', 'INVALID_ARGUMENT'],
    'rack.place': _P + ['RACK_NOT_EJECTED', 'UNKNOWN_POSITION', 'POSITION_OCCUPIED', 'VOLUME_OUT_OF_RANGE', 'UNKNOWN_SOLUTION', 'INSUFFICIENT_SOLUTION'],
    'rack.remove': _P + ['RACK_NOT_EJECTED', 'POSITION_EMPTY'],
    'bottle.place': _P + ['VOLUME_OUT_OF_RANGE', 'UNKNOWN_SOLUTION', 'INSUFFICIENT_SOLUTION', 'RUN_IN_PROGRESS'], 'bottle.remove': _P + ['NO_BOTTLE', 'RUN_IN_PROGRESS'],
    'bottle.empty_waste': _P + ['RUN_IN_PROGRESS'], 'door.set': _P + ['CLAMP_MOVING'], 'clamp.set': _P + ['DOOR_CLOSED'],
    'sample_compartment.open_door': _P + ['POWER_ON', 'RACK_NOT_INSIDE', 'RACK_NOT_EMPTY', 'ALREADY_OPEN'], 'sample_compartment.close_door': _P + ['ALREADY_CLOSED'],
    'sample_compartment.clean': _P + ['DOOR_CLOSED'], 'sample_compartment.set_light': _CONN,
    'chip.open_port': _CONN + ['CHIP_DOCKED', 'INSTRUMENT_BUSY'], 'chip.insert': _P + ['PORT_CLOSED', 'CHIP_PRESENT', 'CHIP_UNKNOWN', 'CHIP_TYPE_MISMATCH', 'CHIP_ID_EXISTS'],
    'chip.close_cover': _P + ['PORT_CLOSED', 'CHIP_PRESENT'], 'chip.dock': _CONN + ['NO_CHIP', 'COVER_OPEN'] + _BUSY, 'chip.eject': _CONN + ['CHIP_NOT_DOCKED'] + _BUSY,
    'chip.remove': _P + ['NO_CHIP'], 'flow.select_buffer': _CONN + ['RUN_IN_PROGRESS'], 'flow.prime': _CONN + ['CHIP_NOT_DOCKED'] + _BUSY + _LIQ_E,
    'flow.standby': _CONN + ['CHIP_NOT_DOCKED'] + _BUSY + ['NO_BUFFER'], 'flow.stop_standby': _CONN + ['NOT_IN_STANDBY'],
    'detector.normalize': _CONN + ['CHIP_NOT_DOCKED', 'MAINTENANCE_CHIP'] + _BUSY + _POS_E + _LIQ_E,
    'maintenance.run': _CONN + ['CHIP_NOT_DOCKED'] + _BUSY + ['MISSING_SOLUTION'] + _POS_E + ['DESORB_BELOW_20C'] + _LIQ_E,
    'system.check': _CONN + ['CHIP_NOT_DOCKED'] + _BUSY + _POS_E + _LIQ_E,
    'system.shutdown': _CONN + ['CHIP_NOT_DOCKED'] + _BUSY + ['RACK_NOT_REMOVED', 'NO_BUFFER', 'WRONG_SOLUTION'],
    'rack.eject': _CONN + ['RUN_IN_PROGRESS', 'SAMPLE_COMPARTMENT_OPEN', 'RACK_NOT_INSIDE'], 'rack.retract': _CONN + ['RACK_NOT_EJECTED', 'RACK_NOT_SEATED'],
    'run.manual_start': _RUN_E + ['REFERENCE_NOT_IN_PATH', 'TEMPERATURE_NOT_STABLE', 'INVALID_ARGUMENT'],
    'run.command': _CONN + ['NO_MANUAL_RUN', 'INVALID_ARGUMENT', 'INJECTION_VOLUME_OUT_OF_RANGE', 'UNKNOWN_POSITION', 'FLOW_PATH_NOT_ALLOWED', 'REFERENCE_NOT_IN_PATH'],
    'run.queue_delete': _CONN + ['NO_MANUAL_RUN', 'NO_SUCH_COMMAND'], 'run.stop_command': _CONN + ['NO_RUN', 'COMMAND_NOT_STOPPABLE'], 'run.pause': _CONN + ['NO_RUN'],
    'run.resume': _CONN + ['NOT_PAUSED'], 'run.stop': _CONN + ['NO_RUN'],
    'wizard.immobilization': _RUN_E + ['INVALID_ARGUMENT', 'CHEMISTRY_NOT_SUPPORTED', 'MIX_POSITION_OCCUPIED', 'INJECTION_VOLUME_OUT_OF_RANGE', 'UNKNOWN_POSITION'],
    'wizard.ph_scouting': _RUN_E + ['INJECTION_VOLUME_OUT_OF_RANGE', 'UNKNOWN_POSITION', 'DETECTION_MISMATCH'],
    'method.run': _RUN_E + ['REFERENCE_NOT_IN_PATH', 'INVALID_ARGUMENT', 'INJECTION_VOLUME_OUT_OF_RANGE', 'UNKNOWN_POSITION', 'BELOW_AMBIENT_LIMIT', 'DETECTION_MISMATCH'],
    'runs.list': [], 'data.sensorgram': _EVAL_E[:3], 'data.report_points': ['UNKNOWN_RUN'], 'eval.kinetics': _EVAL_E + ['NO_CONCENTRATION'],
    'eval.affinity': _EVAL_E + ['NO_CONCENTRATION', 'NO_DATA'], 'eval.concentration': _EVAL_E + ['NO_DATA', 'INVALID_ARGUMENT'],
    'eval.solvent_correction': _EVAL_E + ['NO_DATA', 'INVALID_ARGUMENT'], 'eval.thermodynamics': ['INVALID_ARGUMENT'], 'events.read': [], 'provenance.read': [],
    'surface.inspect': [], 'ledger.read': [], 'fault.inject': _P, 'sim.step': [],
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
PRECONDITIONS = {
    'plant.configure': ('首次声明时设定噪声种子；之后再声明须给同一种子', ['plant.configured']),
    'power.set': ('已声明实验室条件；开机时供电在100–240 V ±10 %；样品舱门关着', ['plant.configured', 'sample_compartment.door_angle_rad']),
    'power.press_switch': ('同power.set', ['plant.configured', 'sample_compartment.door_angle_rad']),
    'temperature.set': ('通电且软件已连接；不在运行中；至少给一个温度；分析温度≥室温−20 °C、样品舱≥室温−15 °C', ['system.connected', 'run.state']),
    'kit.provide': ('已声明实验室条件（已在台上的试剂不重复）', ['plant.configured']), 'stock.provide': ('已声明实验室条件；ID唯一；物质存在', ['solutions.available']),
    'species.define': ('已声明实验室条件；ID唯一', ['plant.configured']), 'interaction.define': ('已声明实验室条件；ID唯一；两种物质存在、不同且尚无相互作用', ['plant.configured']),
    'solution.prepare': ('已声明实验室条件；ID唯一、溶液数≤120；来源存在且体积足够；来源与DMSO之和不超过终体积', ['solutions.available']),
    'rack.configure': ('托盘已弹出；组合架、只放试剂架与全部取下（empty）时plate为none，带板配置须给板型', ['rack.state']),
    'rack.place': ('托盘已弹出且已配置；位置在当前配置中且为空；体积不超过孔/瓶容量；溶液体积足够', ['rack.state', 'rack.config', 'rack.positions', 'solutions.available']),
    'rack.remove': ('托盘已弹出；位置有溶液', ['rack.state', 'rack.positions']),
    'bottle.place': ('体积不超过瓶容量（A 1 L、B–D 250 mL、水500 mL）；溶液体积足够；自动运行中不能换所用的瓶', ['bottle.volumes_m3', 'solutions.available', 'run.state']),
    'bottle.remove': ('瓶存在；运行中不能取所用的缓冲液瓶、水瓶与废液瓶', ['bottle.volumes_m3', 'run.state']),
    'bottle.empty_waste': ('自动运行中不能倒废液', ['run.state']), 'door.set': ('关右泵舱门时管夹手柄不在扳动', ['clamp.lever_angle_rad']),
    'clamp.set': ('右泵舱门打开', ['door.right_pump_angle_rad']),
    'sample_compartment.open_door': ('已关电源；托盘在内且没有板与试剂架；门关着', ['power.enabled', 'rack.state', 'rack.config', 'sample_compartment.door_angle_rad']),
    'sample_compartment.close_door': ('门开着', ['sample_compartment.door_angle_rad']), 'sample_compartment.clean': ('门开着', ['sample_compartment.door_angle_rad']),
    'sample_compartment.set_light': ('通电且软件已连接', ['system.connected']),
    'chip.open_port': ('通电且软件已连接；没有对接芯片；不在对接/退出/维护中', ['system.connected', 'chip.state', 'operations.running']),
    'chip.insert': ('芯片口打开且为空；复用芯片须是本仪器用过的同型芯片，新芯片ID须唯一', ['chip.state', 'chip.port_cover_angle_rad']),
    'chip.close_cover': ('芯片口打开且口中没有已退出的芯片', ['chip.state', 'chip.port_cover_angle_rad']),
    'chip.dock': ('通电且软件已连接；芯片已插入且盖已关；不在运行或其它操作中', ['system.connected', 'chip.state', 'chip.port_cover_angle_rad', 'run.state', 'operations.running']),
    'chip.eject': ('通电且软件已连接；芯片已对接；不在运行或其它操作中', ['system.connected', 'chip.state', 'run.state', 'operations.running']),
    'chip.remove': ('芯片已退出且口盖已开', ['chip.state', 'chip.port_cover_angle_rad']),
    'flow.select_buffer': ('通电且软件已连接；不在自动运行中', ['system.connected', 'run.state']),
    'flow.prime': ('通电且软件已连接；芯片已对接；不在运行或其它操作中；所选管的瓶、水瓶与废液瓶在位', ['system.connected', 'chip.state', 'run.state', 'operations.running',
                                                                                  'bottle.volumes_m3', 'bottle.tubes_inserted']),
    'flow.standby': ('通电且软件已连接；芯片已对接；不在运行或其它操作中；管A有缓冲液', ['system.connected', 'chip.state', 'run.state', 'operations.running', 'bottle.volumes_m3']),
    'flow.stop_standby': ('通电且软件已连接；在待机', ['system.connected', 'flow.state']),
    'detector.normalize': ('通电且软件已连接；芯片已对接且不是Maintenance芯片；不在运行或其它操作中；托盘在内、位置有≥100 µL BIAnormalizing；液体瓶在位',
                           ['system.connected', 'chip.state', 'chip.type', 'run.state', 'operations.running', 'rack.state', 'rack.positions', 'bottle.volumes_m3']),
    'maintenance.run': ('通电且软件已连接；芯片已对接；不在运行或其它操作中；按程序给出所需溶液的位置（每种≥250 µL，溶液种类须对）；分析与样品舱设定<20 °C时不能用BIAdesorb 1；液体瓶在位',
                        ['system.connected', 'chip.state', 'run.state', 'operations.running', 'rack.state', 'rack.positions', 'temperature.analysis_setpoint_k',
                         'temperature.sample_setpoint_k', 'bottle.volumes_m3']),
    'system.check': ('通电且软件已连接；芯片已对接（应为新CM5芯片）；不在运行或其它操作中；位置有≥250 µL BIAtest；管A、水瓶与废液瓶在位',
                     ['system.connected', 'chip.state', 'run.state', 'operations.running', 'rack.state', 'rack.positions', 'bottle.volumes_m3']),
    'system.shutdown': ('通电且软件已连接；芯片已对接；不在运行或其它操作中；托盘已弹出且已取出内容（rack.configure config=empty）；管A接蒸馏水、管B接70 %乙醇',
                        ['system.connected', 'chip.state', 'run.state', 'operations.running', 'rack.state', 'rack.config', 'bottle.solutions']),
    'rack.eject': ('通电且软件已连接；不在运行中（手动运行用run.command eject_rack）；样品舱门关着；托盘在内或停在中途（移动中断电）', ['system.connected', 'run.state', 'rack.state',
                                                                                          'sample_compartment.door_angle_rad']),
    'rack.retract': ('通电且软件已连接；托盘已弹出或停在中途（移动中断电）；架子装正', ['system.connected', 'rack.state', 'rack.config']),
    'run.manual_start': ('通电且软件已连接；芯片已对接；不在运行或其它操作中；托盘在内；液体瓶在位、废液瓶未满；参比扣除的流通池在流路中；温度稳定（或ignore_temperature）；数据率1或10 Hz；运行ID唯一',
                         ['system.connected', 'chip.state', 'run.state', 'operations.running', 'rack.state', 'bottle.volumes_m3', 'temperature.analysis_stable', 'runs.count']),
    'run.command': ('有进行中的手动运行；命令所需参数齐全；注入体积2–350 µL（高黏度≤600 s）；位置在托盘配置中；周期内流路只能缩小；新周期的参比在流路中',
                    ['run.kind', 'run.state', 'rack.config', 'flow.path']),
    'run.queue_delete': ('有进行中的手动运行；序号在队列内', ['run.kind', 'run.queue_length']), 'run.stop_command': ('有进行中的运行；弹出托盘命令不能停', ['run.state', 'run.current_command']),
    'run.pause': ('有进行中的运行', ['run.state']), 'run.resume': ('运行已暂停', ['run.state']), 'run.stop': ('有进行中的运行', ['run.state']),
    'wizard.immobilization': ('同run.manual_start（温度不稳定时先等待）；每个流通池参数齐全且只出现一次；胺偶联须CM5/CM4/CM3/C1芯片、His捕获须NTA、生物素捕获须SA；EDC/NHS混合位须为空位',
                              ['system.connected', 'chip.state', 'chip.type', 'run.state', 'operations.running', 'rack.state', 'rack.positions', 'bottle.volumes_m3', 'runs.count']),
    'wizard.ph_scouting': ('同run.manual_start（温度不稳定时先等待）；注入体积2–350 µL', ['system.connected', 'chip.state', 'run.state', 'operations.running', 'rack.state', 'bottle.volumes_m3',
                                                                      'runs.count']),
    'method.run': ('同run.manual_start（温度不稳定时先等待）；检测设置与流路相符；每个注入命令有位置与接触时间、体积2–350 µL；周期温度不低于室温−20 °C',
                   ['system.connected', 'chip.state', 'run.state', 'operations.running', 'rack.state', 'rack.config', 'bottle.volumes_m3', 'runs.count']),
    'data.sensorgram': ('运行与周期存在，曲线已记录', ['runs.count']), 'data.report_points': ('运行存在', ['runs.count']),
    'eval.kinetics': ('运行与周期存在；每个周期有样品注入；有浓度（关键词、溶液标签或输入）', ['runs.count']),
    'eval.affinity': ('同eval.kinetics；至少3个点', ['runs.count']), 'eval.concentration': ('运行与周期存在；标准曲线至少4个标准；CFCA须按两个流速成对给出周期并给分子量或扩散系数', ['runs.count']),
    'eval.solvent_correction': ('运行与周期存在；活性与参比流通池不同', ['runs.count']), 'eval.thermodynamics': ('至少3个不同温度', []),
    'fault.inject': ('已声明实验室条件', ['plant.configured']),
}
MODEL_NONE_ZH = {
    'plant.configure': '只声明宿主实验室条件', 'temperature.set': '温度只在过程层（指示灯在之后的step中变化）', 'kit.provide': '试剂没有场景几何（实验台抽象）', 'stock.provide': '同左',
    'species.define': '只在过程层', 'interaction.define': '只在过程层', 'solution.prepare': '溶液没有场景几何', 'rack.configure': '微孔板与试剂架没有场景几何（托盘为接口面）',
    'rack.place': '小瓶与孔中的液体没有场景几何', 'rack.remove': '同左', 'bottle.place': '瓶为固定外观（液位只在过程层）', 'bottle.remove': '同左', 'bottle.empty_waste': '同左',
    'sample_compartment.clean': '清洁没有可见变化', 'sample_compartment.set_light': '样品舱照明不绑定场景灯', 'chip.insert': '芯片没有场景几何（芯片口为接口点）',
    'chip.remove': '同左', 'flow.select_buffer': '选择阀在泵舱内（不建几何）', 'flow.prime': '流路只在过程层', 'flow.standby': '同左', 'flow.stop_standby': '同左',
    'detector.normalize': '同左', 'maintenance.run': '同左', 'system.check': '同左', 'system.shutdown': '同左', 'run.command': '命令队列只在过程层', 'run.queue_delete': '同左',
    'run.stop_command': '同左', 'run.pause': '同左', 'run.resume': '同左', 'runs.list': '只读', 'data.sensorgram': '只读', 'data.report_points': '只读',
    'eval.kinetics': '只读计算', 'eval.affinity': '只读计算', 'eval.concentration': '只读计算', 'eval.solvent_correction': '只读计算', 'eval.thermodynamics': '只读计算',
    'events.read': '只读', 'provenance.read': '只读', 'surface.inspect': '只读真值（evaluator）', 'ledger.read': '只读账目（evaluator）', 'fault.inject': '评估者故障注入',
    'sim.step': '推进时钟'}
SCENE_MIRRORED = {'chip.port_cover_angle_rad', 'rack.carriage_position_m', 'door.left_pump_angle_rad', 'door.right_pump_angle_rad', 'clamp.lever_angle_rad',
                  'sample_compartment.door_angle_rad', 'lamp.ready', 'lamp.system', 'lamp.temperature', 'lamp.sensor_chip', 'lamp.run'}
EXEC_TEXT = {
    'chip.dock': ('约45 s后chip.state=docked（操作succeeded），随后自动开始待机流', '断电：failed（POWER_OFF_INTERRUPT），芯片回到inserted；不能取消'),
    'chip.eject': ('约30 s后流通池排空、芯片退出、芯片口打开（操作succeeded，chip.state=ejected）', '断电：failed；不能取消'),
    'flow.prime': ('约6.5 min后流路充满所选缓冲液并回到待机流（操作succeeded）', '取消或断电：流路缓冲液不变（操作cancelled/failed）'),
    'detector.normalize': ('约8 min后各流通池检测增益归一化（chip.normalized=true，操作succeeded）', '取消或断电：增益不变'),
    'maintenance.run': ('程序时长结束（Desorb约25 min、Desorb and Sanitize约50 min、Superclean约1.5 h等）后操作succeeded，流路污染下降', '取消或断电：已注入的溶液效果保留，污染不按程序下降'),
    'system.check': ('约1 h后给出结果表（system_check.last，操作succeeded）', '取消或断电：不给结果'),
    'system.shutdown': ('约20 min后流路排空（flow.state=dry），提示松开蠕动泵管夹（操作succeeded）', '取消或断电：流路状态不变'),
    'wizard.immobilization': ('全部流通池固定化周期完成后操作succeeded（结果含Response bound与final、目标是否达到）',
                              '取消：运行stopped，已固定的配体保留；断电：failed；缓冲液瓶空：failed（BUFFER_EMPTY）；系统错误：failed（SYSTEM_ERROR）'),
    'wizard.ph_scouting': ('全部pH周期完成后操作succeeded（结果含各pH的预富集响应）', '取消：stopped；断电或瓶空：failed；系统错误：failed（SYSTEM_ERROR）'),
    'method.run': ('全部周期完成后运行completed（操作succeeded）；温度不稳定时先等待',
                   '取消/Stop Run：运行stopped（已记录的数据保留）；断电：failed（POWER_OFF_INTERRUPT）；瓶空：failed（BUFFER_EMPTY）；系统错误：failed（SYSTEM_ERROR）'),
}
RESOURCES = {'power.set': ['mains'], 'power.press_switch': ['mains'], 'kit.provide': ['bench'], 'stock.provide': ['bench'], 'solution.prepare': ['bench'],
             'rack.configure': ['robot_arm', 'rack_tray'], 'rack.place': ['robot_arm', 'rack_tray'], 'rack.remove': ['robot_arm', 'rack_tray'],
             'bottle.place': ['robot_arm', 'bottle_trays'], 'bottle.remove': ['robot_arm', 'bottle_trays'], 'bottle.empty_waste': ['robot_arm', 'waste_bottle'],
             'door.set': ['robot_arm'], 'clamp.set': ['robot_arm'], 'sample_compartment.open_door': ['robot_arm', 'screwdriver'], 'sample_compartment.close_door': ['robot_arm'],
             'sample_compartment.clean': ['robot_arm'], 'chip.insert': ['robot_arm', 'sensor_chip'], 'chip.close_cover': ['robot_arm'], 'chip.remove': ['robot_arm'],
             'chip.dock': ['ifc', 'host_clock'], 'chip.eject': ['ifc', 'host_clock'], 'flow.prime': ['syringe_pumps', 'host_clock'], 'detector.normalize': ['syringe_pumps', 'autosampler', 'host_clock'],
             'maintenance.run': ['syringe_pumps', 'autosampler', 'host_clock'], 'system.check': ['syringe_pumps', 'autosampler', 'host_clock'], 'system.shutdown': ['syringe_pumps', 'host_clock'],
             'rack.eject': ['rack_carriage', 'host_clock'], 'rack.retract': ['rack_carriage', 'host_clock'], 'run.manual_start': ['syringe_pumps', 'detector', 'host_clock'],
             'wizard.immobilization': ['syringe_pumps', 'autosampler', 'detector', 'host_clock'], 'wizard.ph_scouting': ['syringe_pumps', 'autosampler', 'detector', 'host_clock'],
             'method.run': ['syringe_pumps', 'autosampler', 'detector', 'host_clock'], 'plant.configure': ['room', 'mains'], 'fault.inject': ['evaluator'], 'sim.step': ['host_clock']}
OBSERVES = {'power.set': ['power.enabled', 'lamp.ready', 'system.connected'], 'power.press_switch': ['power.enabled', 'lamp.ready', 'system.connected'],
            'temperature.set': ['temperature.analysis_setpoint_k', 'temperature.analysis_k', 'temperature.analysis_stable', 'lamp.temperature'],
            'chip.open_port': ['chip.state', 'chip.port_cover_angle_rad'], 'chip.insert': ['chip.state', 'lamp.sensor_chip'], 'chip.dock': ['chip.state', 'lamp.sensor_chip', 'operations.running'],
            'chip.eject': ['chip.state', 'chip.port_cover_angle_rad', 'operations.running'], 'flow.prime': ['flow.state', 'operations.running'],
            'detector.normalize': ['chip.normalized', 'operations.running'], 'maintenance.run': ['operations.running', 'maintenance.due'], 'system.check': ['system_check.last', 'operations.running'],
            'system.shutdown': ['flow.state', 'operations.running'], 'rack.eject': ['rack.state', 'rack.carriage_position_m'], 'rack.retract': ['rack.state', 'rack.carriage_position_m'],
            'run.manual_start': ['run.state', 'response.ru', 'lamp.run'], 'run.command': ['run.queue_length', 'run.current_command'], 'method.run': ['run.state', 'run.cycle', 'response.ru', 'lamp.run'],
            'wizard.immobilization': ['run.state', 'run.cycle', 'response.ru'], 'wizard.ph_scouting': ['run.state', 'run.cycle', 'response.ru'],
            'door.set': ['door.left_pump_angle_rad', 'door.right_pump_angle_rad'], 'clamp.set': ['clamp.closed', 'clamp.lever_angle_rad'],
            'sample_compartment.open_door': ['sample_compartment.door_angle_rad'], 'sample_compartment.close_door': ['sample_compartment.door_angle_rad'],
            'bottle.place': ['bottle.volumes_m3'], 'rack.place': ['rack.positions']}
_WB, _PR, _DF, _OI, _IH, _SH, _GS, _SSH, _CG, _SK, _PDB, _HV, _CRC, _SIM = (
    'user_workbook', 'manufacturer_primary', 'manufacturer_datafile', 'manufacturer_operating_instructions', 'manufacturer_instrument_handbook',
    'manufacturer_software_handbook', 'manufacturer_getting_started', 'manufacturer_sensor_surface_handbook', 'manufacturer_concentration_guide', 'skempi_2', 'rcsb_pdb',
    'iapws_harvey_1998', 'crc_concentrative_properties', 'simulation_assumption')
CAP_SOURCES = {k: [_SIM] for k in CAPS}
CAP_SOURCES.update({k: [_OI, _IH] for k in ('power.set', 'power.press_switch')})
CAP_SOURCES.update({'temperature.set': [_OI, _IH, _DF, _SH], 'kit.provide': [_GS, _OI, _SSH, _SIM], 'stock.provide': [_SK, _PDB, _SIM], 'species.define': [_CG, _SIM],
                    'interaction.define': [_SIM, _SK], 'solution.prepare': [_CRC, _SH, _SIM], 'rack.configure': [_OI, _IH], 'rack.place': [_OI, _GS, _DF], 'rack.remove': [_OI],
                    'bottle.place': [_OI, _IH], 'bottle.remove': [_OI], 'bottle.empty_waste': [_OI], 'door.set': [_OI], 'clamp.set': [_OI, _IH],
                    'sample_compartment.open_door': [_OI], 'sample_compartment.close_door': [_OI], 'sample_compartment.clean': [_OI], 'sample_compartment.set_light': [_OI],
                    'chip.open_port': [_OI, _GS], 'chip.insert': [_OI, _GS, _SSH], 'chip.close_cover': [_OI, _GS], 'chip.dock': [_OI, _GS, _SIM], 'chip.eject': [_OI, _GS],
                    'chip.remove': [_OI], 'flow.select_buffer': [_OI, _IH], 'flow.prime': [_OI, _GS], 'flow.standby': [_OI, _IH], 'flow.stop_standby': [_OI],
                    'detector.normalize': [_OI, _GS], 'maintenance.run': [_OI], 'system.check': [_OI], 'system.shutdown': [_OI], 'rack.eject': [_OI, _GS, _SH],
                    'rack.retract': [_OI, _SH], 'run.manual_start': [_SH, _OI, _DF, _SIM], 'run.command': [_SH, _DF], 'run.queue_delete': [_SH], 'run.stop_command': [_SH],
                    'run.pause': [_SH], 'run.resume': [_SH], 'run.stop': [_SH], 'wizard.immobilization': [_SH, _GS, _SSH, _SIM], 'wizard.ph_scouting': [_SH, _GS, _SSH, _SIM],
                    'method.run': [_SH, _DF, _GS, _SK, _HV, _CRC, _CG, _SIM], 'runs.list': [_SH], 'data.sensorgram': [_SH], 'data.report_points': [_SH, _GS],
                    'eval.kinetics': [_SH, _CG], 'eval.affinity': [_SH], 'eval.concentration': [_SH, _CG], 'eval.solvent_correction': [_SH], 'eval.thermodynamics': [_SH, _DF],
                    'provenance.read': [_SK, _PDB, _CRC, _HV, _SSH], 'surface.inspect': [_SIM], 'fault.inject': [_SIM, _OI], 'plant.configure': [_OI, _SIM]})
CAP_SOURCES.update({k: [_DF, _WB] for k in UNSUPPORTED})
PARAM_ZH = {'room_k': '实验室室温', 'mains_voltage_v': '供电电压', 'noise_seed': '噪声随机数种子（只在首次声明时生效）', 'relative_humidity': '相对湿度（凝水提示）',
            'rack_auto_retract_s': '托盘弹出后自动收回的时间（Tools→Preferences）'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.spr_biosensor_process.limitations。'
CAP_LIMITS = {
    'run.manual_start': ['手动运行的数据持续记录；软件不监测托盘各位置的剩余体积（厂家）；位置体积不足时注入带空气（尖峰）。'],
    'run.command': ['命令忙时排队；暂停只在当前命令完成后生效（厂家没有说明注入中暂停的行为）；高黏度再生接触时间上限600 s为假设。'],
    'wizard.immobilization': ['胺偶联：EDC/NHS（0.2 M/0.05 M混合）10 µL/min 7 min、配体、乙醇胺7 min（厂家协议）；预富集、偶联、活性酯水解与去活化的速率常数为工程值（按入门指南30 µg/mL pH 5.0、420 s→>8000 RU校准）；'
                              '“目标固定量”先做10 µL@5 µL/min预富集脉冲（厂家），再按观测的偶联速率给脉冲（规则为工程等价），总配体≤150 µL（厂家）；捕获化学的容量与解离速率为工程值。'],
    'wizard.ph_scouting': ['预富集按配体等电点（由序列推导）、pH、离子强度与葡聚糖羧基pKa 3.5计算（工程模型，趋势符合入门指南的pH筛选示例）。'],
    'method.run': ['结合为1:1（每个配体位点独立；同一配体上的不同分析物竞争）加两室传质（km按厂家流通池公式，宽0.5 mm、长3.0 mm为假设）；温度依赖按Eyring（活化焓为假设）；'
                   '再生按溶液强度指数加速解离并损伤配体（工程模型）；单循环动力学即同一周期内多次样品注入；无非特异性结合与亲和力（多价）效应。'],
    'eval.kinetics': ['1:1含传质模型的全局拟合（Levenberg–Marquardt、数值雅可比、多初值；数据平均到1 s）；U值为等价实现：ka与kd同乘/除2（KD不变）并重拟合Rmax时Chi²的增幅映射到0–100（厂家算法未公开）。'],
    'eval.concentration': ['标准曲线为四参数逻辑拟合；CFCA用注入开始3–13 s的初始斜率与两个流速的传质系数（扩散系数缺省按厂家分子量公式、形状因子1.2）。'],
    'eval.solvent_correction': ['二次校正曲线（厂家）；外推限于范围的10 %（厂家：超过约10 %危险）。'],
    'detector.normalize': ['未归一化时各流通池增益有σ=1.5 %的偏差（假设）；BIAnormalizing（70 %甘油）的折射率超出1.33–1.40量程，注入期间读数为空。'],
    'maintenance.run': ['程序时长除Superclean约1.5 h外为假设；清洗效果以流路污染指数表示（系数为假设）；在装有配体的芯片上运行会按溶液强度损伤配体。'],
    'system.check': ['结果按仪器状态判定（管夹、水、缓冲液、泄漏故障、芯片是否新CM5、运行缓冲液是否HBS、温度与污染、管B–D是否插在瓶中），不是逐项仿真曲线。'],
}
LIMITATIONS = [
    '选定配置：Biacore T200处理单元（含控制与评估软件的功能等价）；电脑、GxP软件包与Biacore Insight不在配置内；L1/HPA脂质捕获与表面结合物回收不支持。',
    '检测为集总模型：每个流通池的响应=增益×（芯片基线+体折射率响应×（1−配体排除体积）+表面质量）+漂移+噪声；没有SPR角度谱、倏逝场深度与葡聚糖层内浓度分布的模型；1 RU≈1e-6 RIU、1 RU≈1 pg/mm²为常用换算。',
    '体折射率：水n(T)取IAPWS（Harvey 1998）在632.8 nm的值，溶质增量取CRC表（20 °C、589 nm）并按质量分数相加；增量的温度与波长依赖不计；无数据的溶质（HEPES、甘氨酸、EDC、NHS、乙醇胺、SDS、P20等）用假设增量。',
    '结合动力学取自SKEMPI 2.0中SPR实测的野生型kon/koff（文献值，非本仪器实测）；分子量由PDB SEQRES计算（不含糖基化）；温度依赖的活化焓与入门试剂盒的动力学常数为假设。',
    '固定化化学（预富集、偶联、水解、去活化）、捕获化学与再生为工程模型；没有非特异性结合、亲和力（多价）效应、配体异质性与表面构象变化。',
    '流路：样品塞按一阶弥散与串联延迟进入各流通池；空气段只以尖峰表示；未Prime时流路缓冲液按时间常数渐变；注射泵、阀与IFC的内部流动不建模。',
    '溶液、小瓶、微孔板与芯片没有场景几何；装瓶、放样、换芯片、开门与扳管夹为宿主/机器人抽象，没有接触与液体仿真。',
    '评估软件为功能等价实现（不是原软件）：报告点窗口、拟合算法、U值与质量控制阈值部分为假设（见各能力limitations）。',
]


def _sampling_text(k):
    if k in ('response.ru', 'temperature.analysis_k', 'temperature.sample_k') or k.startswith('lamp.'):
        return 'Value of the last host grid point (0.1 s grid while active, 10 s blocks when idle; noise drawn per grid point; flashing lamps by the 0.5 s phase).'
    if k.startswith(('truth.', 'flow.', 'rack.', 'run.', 'bottle.', 'door.', 'clamp.', 'chip.', 'standby.', 'temperature.', 'operations', 'warnings', 'errors',
                     'sample_compartment.door', 'maintenance', 'system_check')) or k == 'clock.time_s':
        return 'Integrated on the host grid (0.1 s while anything is injected, recorded or moving; whole 10 s blocks when idle); between grid points the last grid value is held.'
    return 'Changes when a command is accepted; no other time dynamics.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if (k in _CONTROL_GUARDED or k in _DATA_QUERIES or k == 'solution.prepare' or CAPS[k]['visibility'] != 'agent') else []
    return list(dict.fromkeys(CAP_ERRORS.get(k, []) + _COMMON_ERRORS + extra))


def _foreign_text(k):
    if k in _CONTROL_GUARDED:
        return '；另一个agent的运行或异步操作进行中时agent调用被拒（ACCESS_DENIED；未通电时开机除外）；只能用自己配的溶液'
    if k in _DATA_QUERIES:
        return '；agent只能读自己的运行（ACCESS_DENIED）'
    if k == 'solution.prepare':
        return '；agent只能用自己配的溶液与评测者给的储备液（ACCESS_DENIED）'
    return ''


def _pre_slots(k):
    errs = CAP_ERRORS.get(k, [])
    ids = (['plant.configured'] if 'PLANT_REQUIRED' in errs else []) + (['power.enabled'] if 'POWER_OFF' in errs else []) + list(PRECONDITIONS[k][1]) \
        + (['errors.active'] if 'SYSTEM_ERROR' in errs else [])
    return list(dict.fromkeys(ids + (['operation.started_by_other_agent'] if k in _CONTROL_GUARDED else [])))


def _pre_text(k):
    errs, text = CAP_ERRORS.get(k, []), PRECONDITIONS[k][0]
    if 'SYSTEM_ERROR' in errs and '系统错误' not in text:
        text = text + '；没有未清除的系统错误（否则SYSTEM_ERROR）'
    if 'POWER_OFF' in errs and '通电' not in text:
        text = '通电（否则POWER_OFF）；' + text
    if 'PLANT_REQUIRED' in errs and '实验室条件' not in text:
        text = '已声明实验室条件（否则PLANT_REQUIRED）；' + text
    return text


def _model_effects(k, un):
    if un:
        return dict(mode='none', description_zh='不支持：不实现，没有模型变化', binding_ids=[])
    seen = sorted((set(EFFECTS.get(k, [])) | set(ON_STEP.get(k, []))) & SCENE_MIRRORED)
    if seen:
        return dict(mode='host_required', binding_ids=[],
                    description_zh='状态经宿主桥（Matterix）镜像到场景关节或指示灯（' + '、'.join(seen) + '；绑定见合同model_bindings），运行层本身不驱动模型'
                                   + ('；' + MODEL_NONE_ZH[k] if k in MODEL_NONE_ZH else ''))
    return dict(mode='none', description_zh=MODEL_NONE_ZH.get(k, '没有镜像到场景的状态变化'), binding_ids=[])


_u = lambda *groups: sorted(set(x for g in groups for x in g))                    # noqa: E731
_LAMPS = [f'lamp.{x}' for x in LAMPS]
_TEMP = ['temperature.analysis_k', 'temperature.analysis_stable', 'temperature.sample_k', 'truth.analysis_temperature_k', 'truth.sample_temperature_k', 'lamp.temperature']
_RESP = ['response.ru', 'truth.flow_cell_mass_ru', 'truth.complexes_ru', 'truth.ligand_activity', 'truth.active_esters', 'truth.flow_cell_refractive_index']
_FLOW = ['flow.state', 'flow.rate_m3_s', 'standby.elapsed_s', 'bottle.volumes_m3', 'truth.bottle_volumes_m3', 'truth.system_buffer', 'flow.prime_recommended']
_RACKM = ['rack.state', 'rack.carriage_position_m', 'rack.auto_retract_in_s']
_RUN = ['run.state', 'run.cycle', 'run.current_command', 'run.queue_length', 'lamp.run', 'flow.path', 'truth.rack_volumes_m3']
_OPS = ['operations.running', 'warnings.active', 'errors.active']
_CHIPM = ['chip.state', 'chip.port_cover_angle_rad', 'lamp.sensor_chip', 'chip.normalized']
_PWR = _u(['power.enabled', 'system.connected', 'temperature.analysis_k', 'temperature.sample_k', 'temperature.analysis_stable', 'flow.state', 'flow.rate_m3_s', 'standby.elapsed_s', 'run.state',
           'run.current_command', 'run.queue_length', 'operations.running', 'chip.state', 'response.ru', 'rack.state', 'errors.active',
           'warnings.active', 'flow.path', 'sample_compartment.light', 'rack.auto_retract_in_s'], _LAMPS)
EFFECTS = {
    'plant.configure': _u(['plant.configured', 'truth.analysis_temperature_k', 'truth.sample_temperature_k', 'truth.flow_cell_refractive_index'], _PWR),
    'power.set': _PWR, 'power.press_switch': _PWR,
    'temperature.set': ['temperature.analysis_setpoint_k', 'temperature.sample_setpoint_k', 'temperature.analysis_stable', 'lamp.temperature'],
    'kit.provide': ['solutions.available'], 'stock.provide': ['solutions.available'], 'species.define': [], 'interaction.define': [], 'solution.prepare': ['solutions.available'],
    'rack.configure': ['rack.config', 'rack.positions', 'truth.rack_volumes_m3'], 'rack.place': ['rack.positions', 'truth.rack_volumes_m3'], 'rack.remove': ['rack.positions', 'truth.rack_volumes_m3'],
    'bottle.place': ['bottle.volumes_m3', 'bottle.tubes_inserted', 'bottle.solutions', 'truth.bottle_volumes_m3', 'errors.active'],
    'bottle.remove': ['bottle.volumes_m3', 'bottle.tubes_inserted', 'bottle.solutions', 'truth.bottle_volumes_m3'],
    'bottle.empty_waste': ['bottle.volumes_m3', 'bottle.solutions', 'truth.bottle_volumes_m3', 'errors.active'], 'door.set': [], 'clamp.set': ['clamp.closed', 'warnings.active'],
    'sample_compartment.open_door': [], 'sample_compartment.close_door': [], 'sample_compartment.clean': ['truth.faults'], 'sample_compartment.set_light': ['sample_compartment.light'],
    'chip.open_port': ['chip.state'], 'chip.insert': ['chip.state', 'chip.id', 'chip.type', 'lamp.sensor_chip'], 'chip.close_cover': ['chip.state'],
    'chip.dock': ['chip.state', 'flow.state', 'operations.running', 'lamp.sensor_chip'],
    'chip.eject': ['chip.state', 'flow.state', 'flow.rate_m3_s', 'standby.elapsed_s', 'operations.running', 'lamp.sensor_chip', 'chip.normalized'],
    'chip.remove': ['chip.state', 'chip.id', 'chip.type', 'lamp.sensor_chip'], 'flow.select_buffer': ['flow.selected_tube'],
    'flow.prime': ['operations.running', 'flow.state', 'flow.rate_m3_s', 'standby.elapsed_s'], 'flow.standby': ['flow.state', 'flow.rate_m3_s', 'standby.elapsed_s'],
    'flow.stop_standby': ['flow.state', 'flow.rate_m3_s', 'standby.elapsed_s'],
    'detector.normalize': ['operations.running', 'flow.state', 'flow.rate_m3_s', 'standby.elapsed_s', 'truth.rack_volumes_m3'],
    'maintenance.run': ['operations.running', 'flow.state', 'flow.rate_m3_s', 'standby.elapsed_s', 'truth.rack_volumes_m3'],
    'system.check': ['operations.running', 'flow.state', 'flow.rate_m3_s', 'standby.elapsed_s', 'truth.rack_volumes_m3'],
    'system.shutdown': ['operations.running', 'flow.state', 'flow.rate_m3_s', 'standby.elapsed_s'],
    'rack.eject': ['rack.state'], 'rack.retract': ['rack.state', 'rack.auto_retract_in_s'],
    'run.manual_start': ['run.state', 'run.id', 'run.kind', 'run.cycle', 'runs.count', 'flow.state', 'flow.rate_m3_s', 'flow.path', 'standby.elapsed_s', 'lamp.run'],
    'run.command': ['run.queue_length'], 'run.queue_delete': ['run.queue_length'], 'run.stop_command': ['run.current_command'], 'run.pause': ['run.state'], 'run.resume': ['run.state'],
    'run.stop': ['run.state', 'run.current_command', 'run.queue_length', 'flow.state', 'flow.rate_m3_s', 'flow.path', 'standby.elapsed_s', 'lamp.run', 'operations.running'],
    'wizard.immobilization': ['run.state', 'run.id', 'run.kind', 'run.cycle', 'runs.count', 'operations.running', 'flow.state', 'flow.rate_m3_s', 'flow.path', 'standby.elapsed_s',
                              'lamp.run'],
    'wizard.ph_scouting': ['run.state', 'run.id', 'run.kind', 'run.cycle', 'runs.count', 'operations.running', 'flow.state', 'flow.rate_m3_s', 'flow.path', 'standby.elapsed_s',
                           'lamp.run'],
    'method.run': ['run.state', 'run.id', 'run.kind', 'run.cycle', 'runs.count', 'operations.running', 'flow.state', 'flow.rate_m3_s', 'flow.path', 'standby.elapsed_s', 'lamp.run',
                   'temperature.sample_setpoint_k', 'flow.selected_tube'],
    'fault.inject': _u(['truth.faults'], _PWR), 'sim.step': ['clock.time_s'],
}
_ALL_PHYS = sorted(k for k in SLOTS if k not in ('plant.configured', 'temperature.analysis_setpoint_k', 'temperature.sample_setpoint_k', 'solutions.available',
                                                 'flow.selected_tube', 'operation.started_by_other_agent', 'truth.faults', 'chip.id', 'chip.type', 'rack.config',
                                                 'bottle.solutions', 'bottle.tubes_inserted', 'sample_compartment.light', 'runs.count', 'run.id', 'run.kind'))
_MANUAL_MECH = ('door.left_pump_angle_rad', 'door.right_pump_angle_rad', 'clamp.lever_angle_rad', 'clamp.closed', 'sample_compartment.door_angle_rad')
_PWR_STEP = [k for k in _ALL_PHYS if k not in _MANUAL_MECH]          # power loss stops the cover motor and the tray where they are; hand-operated parts are not affected
ON_STEP = {k: _PWR_STEP for k in ('plant.configure', 'power.set', 'power.press_switch', 'fault.inject')}
ON_STEP['sim.step'] = _ALL_PHYS
ON_STEP.update({
    'temperature.set': _u(_TEMP, _RESP),
    'bottle.place': _u(_FLOW, _OPS, _RESP), 'bottle.remove': _u(_FLOW, _OPS, _RESP), 'bottle.empty_waste': _u(_FLOW, _OPS),
    'door.set': ['door.left_pump_angle_rad', 'door.right_pump_angle_rad', 'warnings.active'], 'clamp.set': _u(['clamp.lever_angle_rad'], _FLOW, _OPS),
    'sample_compartment.open_door': ['sample_compartment.door_angle_rad'], 'sample_compartment.close_door': ['sample_compartment.door_angle_rad'],
    'chip.open_port': ['chip.port_cover_angle_rad'], 'chip.close_cover': ['chip.port_cover_angle_rad'], 'chip.insert': ['lamp.sensor_chip'],
    'chip.dock': _u(_CHIPM, _FLOW, _OPS, _RESP), 'chip.eject': _u(_CHIPM, _FLOW, _OPS, _RESP),
    'flow.select_buffer': _u(_FLOW, _OPS, _RESP), 'flow.prime': _u(_FLOW, _OPS, _RESP), 'flow.standby': _u(_FLOW, _OPS, _RESP), 'flow.stop_standby': _u(_FLOW, _OPS, _RESP),
    'detector.normalize': _u(_FLOW, _OPS, _RESP, ['chip.normalized', 'truth.rack_volumes_m3']), 'maintenance.run': _u(_FLOW, _OPS, _RESP, ['truth.contamination', 'maintenance.due']),
    'system.check': _u(_FLOW, _OPS, _RESP, ['system_check.last']), 'system.shutdown': _u(_FLOW, _OPS, _RESP),
    'rack.eject': _u(_RACKM, _OPS), 'rack.retract': _u(_RACKM, _OPS), 'rack.place': ['truth.rack_volumes_m3'],
    'run.manual_start': _u(_RUN, _FLOW, _OPS, _RESP, _RACKM), 'run.command': _u(_RUN, _FLOW, _OPS, _RESP, _RACKM), 'run.queue_delete': _u(_RUN, _FLOW, _RESP),
    'run.stop_command': _u(_RUN, _FLOW, _RESP), 'run.pause': _u(_RUN, _FLOW, _RESP), 'run.resume': _u(_RUN, _FLOW, _RESP), 'run.stop': _u(_RUN, _FLOW, _OPS, _RESP),
    'wizard.immobilization': _u(_RUN, _FLOW, _OPS, _RESP, _TEMP, ['chip.normalized', 'run.state']), 'wizard.ph_scouting': _u(_RUN, _FLOW, _OPS, _RESP, _TEMP),
    'method.run': _u(_RUN, _FLOW, _OPS, _RESP, _TEMP, ['chip.normalized', 'temperature.analysis_setpoint_k']),
    'interaction.define': _RESP, 'stock.provide': [], 'solution.prepare': [],
})
ON_STEP_ZH = {
    'plant.configure': '声明后所有过程按宿主网格演化（室温驱动温度；凝水提示；供电超限时等同断电）',
    'fault.inject': '故障作用于随后的各过程（断电：同power.set断电，托盘与正在打开的芯片口盖停在原处；系统错误：红灯亮、运行failed、不能开始新运行；泄漏、针堵塞、脱气机、注射泵泄漏）',
    'power.set': '通电后约3 s指示灯自检、30 s软件连接，温控与流动工作；断电后温度回落室温、流动与运行停止，移动中的托盘停在中途（rack.state=stopped，'
                 '再用rack.eject或rack.retract继续），正在打开的芯片口盖停住（关盖可用手）；泵舱门、管夹与样品舱门为手动件，不受影响',
    'power.press_switch': '同power.set',
    'temperature.set': '分析温度按0.1 K/min限速逼近设定（5 K约1 h稳定），温度灯闪烁到稳定；水的折射率随温度变化使响应漂移',
    'bottle.place': '换瓶后流路缓冲液逐渐变成新瓶内容（未Prime；响应漂移）', 'bottle.remove': '取走所用瓶后流动无缓冲液（BUFFER_EMPTY）', 'bottle.empty_waste': '废液瓶不再溢出',
    'door.set': '门按约2 rad/s转到目标', 'clamp.set': '管夹手柄转到目标；松开时供液块不供液（注入带空气）',
    'sample_compartment.open_door': '样品舱门上翻', 'sample_compartment.close_door': '样品舱门放下', 'chip.open_port': '芯片口盖打开', 'chip.close_cover': '芯片口盖关上',
    'chip.insert': '插入未对接时sensor chip灯闪烁', 'chip.dock': '约45 s后对接完成并开始待机流；之后的响应与漂移', 'chip.eject': '约30 s后流通池排空、芯片口打开',
    'flow.select_buffer': '流路缓冲液逐渐变成所选瓶的内容（未Prime）', 'flow.prime': '约6.5 min冲洗后流路为所选缓冲液', 'flow.standby': '低速待机流消耗缓冲液（7天后停止）',
    'flow.stop_standby': '流动停止', 'detector.normalize': '甘油脉冲后检测增益归一化', 'maintenance.run': '按程序注入维护溶液（在有配体的芯片上损伤配体），完成后流路污染下降',
    'system.check': '约1 h后给出结果', 'system.shutdown': '约20 min后流路排空', 'rack.eject': '托盘车约6 s弹出，预设时间后自动收回', 'rack.retract': '托盘车约6 s送回',
    'rack.place': '敞口位置随时间蒸发（浓缩）', 'run.manual_start': '按流速流动并记录数据；命令按队列执行', 'run.command': '命令按队列执行（注入、等待、新周期等）',
    'run.queue_delete': '少执行一个命令', 'run.stop_command': '当前注入提前结束', 'run.pause': '暂停后不再执行新命令', 'run.resume': '继续执行命令', 'run.stop': '运行结束后转入待机流',
    'wizard.immobilization': '活化、偶联、封闭的表面化学与响应变化（每个流通池一个周期）', 'wizard.ph_scouting': '各pH下的预富集与洗脱', 'method.run': '按周期注入、结合、解离与再生，记录传感图',
    'interaction.define': '之后注入时按新的相互作用结合', 'sim.step': '按宿主网格积分',
}


def _params():
    P = {}

    def doc(name, val, unit, meaning, src):
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)

    def asm(name, val, unit, meaning, unc, src=(_SIM,)):
        P[name.lower()] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=list(src), uncertainty=unc)   # contract keys are lower case
    doc('flow_rate_range_m3_s', list(FLOW_RANGE), 'm3/s', '流速1–100 µL/min', [_DF, _WB, _PR])
    doc('injection_volume_range_m3', list(INJ_RANGE), 'm3', '进样体积2–350 µL', [_DF, _WB, _PR])
    doc('flow_cells', 4, '1', '4个流通池（单个、成对1-2/3-4或串联1-2-3-4；Fc1可作2、3、4的参比，Fc3作4的参比）', [_DF, _IH, _WB])
    doc('flow_cell_volume_m3', DOC['flow_cell_volume_m3'], 'm3', '每个流通池约0.06 µL（60 nL）', [_DF, _OI, _IH, _WB])
    doc('flow_cell_height_m', FC_H, 'm', '流通池高约40 µm', [_DF, _OI, _IH])
    doc('analysis_temperature_range_k', list(T_RANGE), 'K', '分析温度4–45 °C，最多比环境低20 °C', [_DF, _OI, _IH, _WB, _PR])
    doc('sample_compartment_temperature_range_k', list(T_RANGE), 'K', '样品舱4–45 °C，最多比环境低15 °C', [_DF, _OI, _IH])
    doc('baseline_noise_ru', DOC['baseline_noise_RU_rms'], 'RU', '基线噪声典型<0.03 RU（RMS）', [_DF, _WB, _PR])
    doc('baseline_drift_ru_s', DOC['baseline_drift_RU_per_s'], 'RU/s', '基线漂移典型<0.3 RU/min', [_DF, _PR])
    doc('refractive_index_range', list(RI_RANGE), '1', '样品折射率范围1.33–1.40', [_DF])
    doc('ka_range_proteins_m3_mol_s', DOC['ka_range_proteins_m3_mol_s'], 'm3/(mol*s)', '蛋白ka典型范围10³–3×10⁹ M⁻¹s⁻¹', [_DF, _PR])
    doc('ka_range_lmw_m3_mol_s', DOC['ka_range_lmw_m3_mol_s'], 'm3/(mol*s)', '小分子ka典型范围10³–5×10⁷ M⁻¹s⁻¹', [_DF, _PR])
    doc('kd_range_s', DOC['kd_range_s'], '1/s', 'kd典型范围10⁻⁵–1 s⁻¹', [_DF, _PR])
    doc('data_rates_hz', [1, 10], '1/s', '数据采集1或10 Hz', [_SH])
    doc('sample_extra_volume_m3', DOC['sample_extra_volume_m3'], 'm3', '所需样品体积=进样体积+20–50 µL', [_DF, _PR])
    doc('dimensions_m', [0.600, 0.615, 0.690], 'm', '处理单元600 W×615 D×690 H mm（GE仪器手册W×D×H；数据文件W×H×D 600×690×615；操作手册高680 mm，冲突见source_evidence）', [_IH, _DF, _OI, _WB])
    doc('mass_kg', 60.0, 'kg', '净重60 kg', [_DF, _OI, _IH, _WB, _PR])
    doc('mains_v', list(MAINS), 'V', '100–240 V~（±10 %）', [_OI, _DF])
    doc('max_power_va', 400.0, 'V*A', '最大功耗400 VA', [_OI])
    doc('ambient_operation_k', list(AMBIENT), 'K', '运行环境18–33 °C', [_OI, _IH])
    doc('standby_consumption_m3_per_s', DOC['standby_consumption_m3_per_day'] / 86400.0, 'm3/s', '待机液体消耗约65 mL/24 h', [_OI, _IH])
    doc('standby_max_s', DOC['standby_max_s'], 's', '最长待机7天', [_OI, _IH])
    doc('prime_s', DOC['prime_s'], 's', 'Prime约6–7 min', [_OI])
    doc('normalize_s', DOC['normalize_s'], 's', 'Normalize约8 min（入门指南）', [_GS])
    doc('shutdown_s', DOC['shutdown_s'], 's', 'Shutdown约20 min', [_OI])
    doc('superclean_s', DOC['superclean_s'], 's', 'Superclean约1.5 h', [_OI])
    doc('temperature_change_5k_s', DOC['temperature_change_5K_s'], 's', '温度变化5 °C约1 h', [_OI])
    doc('software_connect_s', DOC['software_connect_s'], 's', '控制软件约30 s建立连接', [_OI, _GS])
    doc('bottle_capacity_m3', dict(BOTTLE_CAP), 'm3', '缓冲液1×1 L+3×250 mL、废液2 L、水500 mL', [_OI, _IH])
    doc('water_max_age_s', DOC['water_max_age_s'], 's', '水至少每48 h更换', [_OI])
    doc('amine_protocol', dict(activation_s=420.0, ligand_s=420.0, deactivation_s=420.0, flow_m3_s=10 * UL_MIN), '1', '胺偶联推荐协议：EDC/NHS、配体、乙醇胺各10 µL/min 7 min', [_SSH])
    doc('preconc_pulse', dict(volume_m3=10e-9, flow_m3_s=5 * UL_MIN), '1', '“目标固定量”预富集脉冲10 µL@5 µL/min', [_SH])
    doc('aim_max_ligand_m3', 150e-9, 'm3', '无预富集方法时配体总量上限150 µL', [_SH])
    doc('mass_transfer_km', '0.98 (D^2 f / (0.3 h^2 w l))^(1/3)', '1', '传质系数km公式；kt = km × MW × 10⁹（蛋白在CM5上）', [_SH, _CG])
    doc('diffusion_estimate', 'D = 342.3e-11 / (M^(1/3) f eta_rel)', '1', '由分子量估计扩散系数（球形f=1，球蛋白约1.2；25 °C水η_rel=0.89）', [_CG])
    doc('dmso_bulk_ru_per_volume_fraction', DMSO_RU_PCT * 100.0, 'RU', 'DMSO体响应系数：每单位体积分数1.2e5 RU（软件手册：1 % DMSO约1200 RU）', [_SH])
    doc('solvent_correction_chi2_ru2', 2.0, 'RU^2', '溶剂校正曲线Chi²应<2 RU', [_SH])
    doc('kinetics_quality', dict(DOC['kinetics_qc']), '1', '质量控制阈值：T值>10显著、U值>25相关/<15唯一、解离期下降≥10–15 %、KD<最高浓度一半', [_SH])
    doc('chip_capacity_relative', {k: v.get('capacity_relative') for k, v in chips().items()}, '1', '芯片固定化容量相对CM5（CM4约30 %羧甲基化、CM3约30 %、C1约10 %）', [_SSH])
    for k, unit in (('flow_cell_width_m', 'm'), ('flow_cell_length_m', 'm'), ('bulk_sensitivity_RU_per_RIU', 'RU'), ('surface_RU_per_kg_m2', 'RU*m2/kg'),
                    ('excluded_volume_per_RU', '1/RU'), ('dispersion_volume_m3', 'm3'), ('serial_delay_volume_m3', 'm3'), ('noise_RU_per_sqrt_hz', 'RU/Hz^0.5'),
                    ('temperature_noise_K', 'K'), ('unnormalized_gain_sigma', '1'), ('rack_auto_retract_s', 's'), ('docking_s', 's'), ('eject_s', 's'), ('rack_travel_s', 's')):
        asm(k, copy.deepcopy(ENG[k]), unit, ENG_NOTE[k], '工程值（见instrument_model.json的engineering说明）')
    for k, fields in (('docking_drift', dict(initial_RU_per_s='RU/s', tau_s='s', steady_RU_per_s='RU/s')),
                      ('temperature_control', dict(rate_K_s='K/s', k_1_s='1/s', stable_band_K='K', sample_tau_s='s', off_tau_s='s')),
                      ('activation', dict(k_act_1_s='1/s', hydrolysis_tau_s='s', activation_RU='RU')),
                      ('preconcentration', dict(R_pc_max_RU='RU', K0_m3_kg='m3/kg', gamma_per_pH='1', I0_mol_m3='mol/m3', k_pc_1_s='1/s', k_release_1_s='1/s', pKa_surface='1')),
                      ('coupling', dict(k_c_1_s='1/s', pKa_amine='1', capacity_RU='RU', ester_capacity_RU='RU')), ('deactivation', dict(k_deact_1_s='1/s', wash_off_fraction='1')),
                      ('regeneration', dict(alpha='1', damage_beta='1/s', complex_tolerance_default='1', ligand_tolerance_default='1')),
                      ('capture', dict(ni_loading_1_s='1/s', his6_kd_1_s='1/s', his10_kd_1_s='1/s', biotin_kd_1_s='1/s', sa_streptavidin_RU='RU', nta_ni_RU='RU')),
                      ('air_spike', dict(depth_RU='RU', duration_s='s')),
                      ('liquids', dict(prime_volume_m3='m3', normalize_volume_m3='m3', peristaltic_m3_s='m3/s', standby_flow_m3_s='m3/s', prime_flow_m3_s='m3/s',
                                       maintenance_flow_m3_s='m3/s', dead_volume_vial_m3='m3', dead_volume_well_m3='m3', needle_loss_m3='m3', aspiration_m3_s='m3/s',
                                       needle_prep_s='s', evaporation_uncovered_m3_s='m3/s', buffer_switch_tau_s='s')),
                      ('contamination', dict(per_sticky_injection='1', desorb_factor='1', sanitize_factor='1', noise_gain='1'))):
        for f, unit in fields.items():
            asm(f'{k}.{f}', copy.deepcopy(ENG[k][f]), unit, ENG_NOTE[k], '工程值')
    asm('chip_raw_baseline_ru', dict(ENG['chip_raw_baseline_RU']), 'RU', ENG_NOTE['chip_raw_baseline_RU'], '工程值')
    asm('maintenance_durations_s', dict(MAINT_S), 's', ENG_NOTE['maintenance_s'], 'Superclean为厂家值，其余为工程值', (_OI, _SIM))
    asm('regeneration_strength', dict(REGEN_S), '1', '再生溶液强度指数（工程值；厂家只给“先温和后剧烈”的筛选原则）', '工程值', (_SSH, _SIM))
    asm('water_viscosity', 'eta = 2.414e-5 x 10^(247.8 / (T - 140)) Pa s', '1', '水的黏度（Vogel关联式；20 °C 1.002 mPa s）', '常用工程关联式（缓存资料只给25 °C的0.89相对值）')
    asm('regeneration_k0_1_s', 0.1, '1/s', '再生强度等于复合物需求时的附加解离速率', '工程值')
    return P


SLOT_REFERENCE = {
    'clock.time_s': '仿真时钟（宿主时钟）；0为运行层创建时', 'temperature.analysis_setpoint_k': '热力学温度（ITS-90），流通池目标温度',
    'temperature.analysis_k': '热力学温度（ITS-90），流通池温度读数', 'temperature.sample_setpoint_k': '热力学温度（ITS-90），样品舱目标温度',
    'temperature.sample_k': '热力学温度（ITS-90），样品舱温度读数', 'truth.analysis_temperature_k': '热力学温度（ITS-90），流通池', 'truth.sample_temperature_k': '热力学温度（ITS-90），样品舱',
    'response.ru': 'SPR响应（RU；1 RU≈1e-6折射率单位≈1 pg/mm²蛋白）：芯片在水中的绝对基线+运行缓冲液相对25 °C水的体折射率响应+表面质量',
    'truth.flow_cell_refractive_index': '流通池中液体的折射率（632.8 nm的水值加CRC 589 nm溶质增量）', 'truth.flow_cell_mass_ru': '表面质量的响应（配体、复合物、预富集、活化与捕获层）',
    'truth.complexes_ru': '各复合物的响应（RU，按分析物质量）', 'flow.rate_m3_s': '流过流通池的液体实际体积流量（分析温度下）',
    'bottle.volumes_m3': '瓶中液体体积的刻度读数（室温；按刻度取整）', 'truth.bottle_volumes_m3': '瓶中液体体积（室温；准确值）', 'truth.rack_volumes_m3': '托盘各位置的液体体积（样品舱温度）',
    'rack.carriage_position_m': '托盘车伸出距离（0在内）', 'chip.port_cover_angle_rad': '芯片口盖开角（0关）', 'door.left_pump_angle_rad': '左泵舱门开角（0关）',
    'door.right_pump_angle_rad': '右泵舱门开角（0关）', 'clamp.lever_angle_rad': '管夹手柄转角（0竖直=夹紧）', 'sample_compartment.door_angle_rad': '样品舱门上翻角（0关）',
    'standby.elapsed_s': '本次待机持续时间', 'rack.auto_retract_in_s': '距自动收回的时间', 'rack.positions': '各位置放入体积（agent记录，非剩余体积）',
}


def export_cytiva_biacoret200_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=SLOT_REFERENCE.get(k, v['reference']), origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'CytivaBiacoret200Session.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.cytiva_biacoret200.CytivaBiacoret200Session.invoke',
                                              invocation=f'session.invoke(instance_id="biacore_t200_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=_pre_text(k) + _foreign_text(k) + '；被拒时状态不变。', state_ids=_pre_slots(k),
                                                                              enforced_by='runtime', check='CytivaBiacoret200Runtime.invoke / CytivaBiacoret200Session._guard')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量') for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置或查询（温度、流动、关节与表面化学在之后的step中演化）',
                                completion_state_ids=['operations.running'] if async_ else [], timeout_s=None,
                                cancellation='unsupported' if (not async_ or k in ('chip.dock', 'chip.eject')) else 'supported', retry='same_request_id',
                                resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=EXEC_TEXT[k][1] if k in EXEC_TEXT else ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, e), side_effects='none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in dict.fromkeys(ON_STEP[k])
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    mechanism = dict(
        component_ids=['cabinet', 'detection_unit', 'ifc_block', 'sensor_chip_port', 'chip_port_cover', 'syringe_pumps', 'buffer_valve', 'degasser', 'peristaltic_pump',
                       'peristaltic_clamp_lever', 'autosampler_needle', 'rack_tray', 'rack_tray_carriage', 'sample_compartment_box', 'buffer_tray', 'waste_water_tray',
                       'lamp_ready', 'lamp_system', 'lamp_temperature', 'lamp_sensor_chip', 'lamp_run', 'power_switch'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='表面等离子共振生物传感：温控（流通池与样品舱）、液路（缓冲液瓶与选择阀、注射泵流动、蠕动泵供液、待机、Prime）、自动进样（托盘、位置、针、空气段）、四个流通池中的样品塞输送、'
                   '体折射率（水n(T)与溶质增量、配体排除体积）、表面化学（胺偶联：活化、预富集、偶联、水解、封闭；SA与NTA捕获）、1:1结合与两室传质、再生与配体损伤、'
                   '检测（芯片基线、增益与归一化、对接漂移、噪声、空气尖峰）、数据记录（周期、注入、报告点）与评估（动力学、亲和、浓度、溶剂校正、热力学）',
        equations=['n_fc = n_water(T) + sum_i dn_i(w_i) [CRC] + 1200 RU/% DMSO / S; R = g (R0 + S (n_fc - n_water(298.15 K)) (1 - phi) + R_surface) + drift + noise, phi = 1.2e-6 RU^-1 x R_ligand',
                   'plug at flow cell k: x(t) = S(t - t0 - delay_k) - S(t - t1 - delay_k), S(u) = 1 - exp(-u / tau), tau = 0.15 uL / f; delay from serial dead volumes',
                   'km = 0.98 (D^2 f / (0.3 h^2 w l))^(1/3); kt = km MW G (G ~ 1e9 RU per kg/m2); D = 342.3e-11 / (M^(1/3) f_r eta_rel) (T / 298.15) (eta_298 / eta_T)',
                   'two-compartment QSSA: Cs = (kt C + sum kd R) / (kt + sum ka theta Rmax); dR/dt = ka Cs (theta_o Rmax - R) - (kd + k_reg) R, exact over each grid step with Cs frozen',
                   'Rmax = R_ligand (MW_A / MW_L) n_sites activity; ka(T) = ka0 (T / T0) exp(-dHa/R (1/T - 1/T0)), kd(T) likewise (Eyring)',
                   'k_reg = 0.1 s^-1 x 10^(2.2 (s - s_req)) during a regeneration solution of strength s; ligand activity decays at 0.004 (s - 2.6) s^-1 for s > 2.6',
                   'amine coupling: da/dt = k_act (C_EDC / 0.2 M)(1 - a) - a / tau_hyd - k_deact (C_eth / 1 M) a; R_pc -> R_pc,max K c / (1 + K c), K = K0 (exp(1.6 (pI - pH)) - 1) / (1 + 10^(3.5 - pH)) x I0 / (I0 + I);',
                   'dR_cov/dt = k_c a R_pc / (1 + 10^(7.2 - pH)) (1 - R_cov / R_cap); NTA: Ni loading, His capture with kd 2e-3 (His6) / 2e-4 (His10) s^-1; SA: transport-limited biotin capture',
                   'T_a: linear at 0.1 K/min until |T_set - T_a| = 0.5 K, then first order 300 s; stable when |T_a - T_set| < 0.01 K',
                   'kinetics fit: global ka, kd, Rmax, tc (kt = tc f^(1/3)), local RI; chi2 = sum (r_f - r_x)^2 / (n - p); affinity: Req = Rmax C / (KD + C) + offset; van t Hoff: ln KD = dH/RT - dS/R (+ dCp)'],
        parameters=_params(), source_ids=[_SIM, _WB, _PR, _DF, _OI, _IH, _SH, _GS, _SSH, _CG, _SK, _PDB, _HV, _CRC], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_t200_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('cytiva_biacoret200', caps, slots, {'spr_biosensor_process': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if SLOT_REFERENCE.get(k) or v['reference']:
            slots[k]['reference_conditions'] = SLOT_REFERENCE.get(k, v['reference'])
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'spr_biosensor_process': mechanism})

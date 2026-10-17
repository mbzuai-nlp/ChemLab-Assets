"""CHEM-091 ECO PHYSICS nCLD 822 M dual-chamber chemiluminescence NO / NO2 / NOx analyzer: runtime (process model + session).

What is simulated (SI; mole fractions in mol/mol, the GUI shows ppm = 1e-6 mol/mol with 0.1 ppm resolution):

* inlet: the gas at the sample inlet is either room air (disconnected), a calibration cylinder from the template library
  (NIST SRM NO-in-N2 / O2-in-N2 / CO2-in-N2, connected by the agent) or a host-supplied sample (process truth, evaluator only);
  inlet pressure 600-1200 mbar absolute (brochure), sample flow 1.0 L/min at 101325 Pa through critical restrictors (flow ~ p_in);
* sample path: optional external sample line (host: length, inner diameter, PTFE / stainless, temperature) with wall memory of NO and
  NO2 from NASA TM X-3229 (rise / decay time per wetted area); condensation in unheated paths (sample dew point above the path
  temperature, option "h" hot tubing absent) removes soluble species by Henry's-law equilibrium with the condensate (Sander 2023);
  dark gas-phase chemistry during the residence time (IUPAC rows 53-64: 2NO+O2, NO+O3, NO+NO3, NO2+O3, N2O3 / N2O4 / N2O5
  formation and decomposition, N2O5 hydrolysis) integrated with backward Euler (N-atom conserving);
* two chambers: NO channel = NO arriving; NOx channel = after the metal converter (NO2 efficiency = host parameter, default 1.0;
  other NOy by the qualitative reducibility classes of Dunlea et al. 2007: efficient -> 1, not efficient -> 0, partial -> explicit
  host assumption); O2-dependent NOx-channel bias from NASA TM X-3229 Table VII;
* chemiluminescence: signal ~ NO x quench factor x flow, quench 1 / (1 + sum a_i x_i) with measured interference factors (CO2 0.60,
  H2O 2.1, O2 0) and, for Ar / H2 / N2O / NO2, a_i = q_i / q_N2 - 1 from the relative quenching efficiencies (upper bound: the sample
  is taken as the whole third-body bath); the instrument compensates the indicated inlet pressure (option "r" regulates it);
* dynamics: internal lag 2.0 s at 1.0 L/min (brochure: lag < 3 s), mixing tau 0.35 s (0-90 % in 0.81 s, brochure < 1 s), filter
  tau = setting; zero noise 0.06 ppm (1 sigma) at the reference filter 1 s scaled with sqrt(1 s / tau) (brochure: 0.06 ppm, LDL
  0.12 ppm "depending on filter setting"); warm-up of converter and cooled PMT (time constants and set points are assumptions);
* four freely selectable full-scale ranges 5-5000 ppm (over-range is flagged), zero / span calibration (asynchronous averaging),
  1 Hz time series of NO, NO2 = NOx - NO and NOx while measuring.

Not simulated: V1 / V2 calibration valves, the analog output box, data interfaces, fault probabilities, PMT gain drift.
Matterix bridge: RECIPES module="asset_runtime.eco_ncld822", cls="EcoNcld822Runtime", instance_in_ctor=True, advance=STEP_DT."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import random
from collections import deque
from pathlib import Path

import numpy as np

from asset_runtime import _annotations as _ann

PROTOCOL_VERSION = '1.0.0'
ASSET_TYPE_ID = 'instrument.eco_ncld822'
CATALOG_ID = 'CHEM-091'
ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_REL = 'assets/reactions/eco_ncld822_nox_library/templates.json'

R_GAS, K_B = 8.314462618, 1.380649e-23
P_REF, T_REF = 101325.0, 298.15
PPM = 1e-6
Q_NOM = 1.0e-3 / 60.0                  # 1.0 L/min (brochure), taken at 101325 Pa / 298.15 K (assumption: no reference state given)
P_MIN, P_MAX = 60000.0, 120000.0       # 600-1200 mbar abs (brochure)
FS_MIN, FS_MAX = 5 * PPM, 5000 * PPM   # four freely selectable ranges 5-5000 ppm (brochure)
LAG_INT_S = 2.0                        # internal transport lag at Q_NOM (assumption within the brochure's < 3 s)
TAU_MIX_S = 0.35                       # chamber / path mixing (assumption; 0-90 % = 2.303 tau = 0.81 s < 1 s brochure)
NOISE0 = 0.06 * PPM                    # zero noise 1 sigma (brochure, "depending on filter setting")
FILTER_REF_S = 1.0                     # filter setting at which NOISE0 holds (assumption)
TAU_P_SENSOR_S = 0.5                   # inlet pressure sensor lag used by the pressure compensation (assumption)
T_CONV_SET, TAU_CONV_S = 648.15, 150.0  # metal converter set point / warm-up tau (assumptions: brochure gives neither)
T_PMT_SET, TAU_PMT_S = 268.15, 90.0    # cooled PMT set point / tau (assumptions)
TAU_O3_S = 20.0                        # ozone generator ramp (assumption)
T_HOT_S = 453.15                       # option "h" hot tubing temperature (assumption)
DIGITS = 1e-7                          # 0.1 ppm display resolution (GUI screenshot)
SERIES_DT = 1.0                        # 1 Hz series while measuring (assumption; brochure: second-level signals)
SERIES_MAX = 7200
DEFAULT_FS = [5e-6, 5e-5, 5e-4, 5e-3]
STANDARD_EXTRA = {'zero_nitrogen': dict(nitrogen=1.0), 'zero_air': dict(nitrogen=0.7808, oxygen=0.2095, argon=0.0093, carbon_dioxide=0.0004)}
CHEM = ['nitric_oxide', 'nitrogen_dioxide', 'ozone', 'nitrate_radical', 'dinitrogen_pentoxide', 'nitric_acid', 'dinitrogen_trioxide',
        'dinitrogen_tetroxide', 'oxygen', 'water']
N_ATOMS = {'nitric_oxide': 1, 'nitrogen_dioxide': 1, 'nitrate_radical': 1, 'dinitrogen_pentoxide': 2, 'nitric_acid': 1, 'dinitrogen_trioxide': 2,
           'dinitrogen_tetroxide': 2, 'nitrous_acid': 1, 'ammonia': 1, 'peroxyacetyl_nitrate': 1, 'peroxynitric_acid': 1, 'methyl_nitrate': 1,
           'ethyl_nitrate': 1, 'n_propyl_nitrate': 1, 'n_butyl_nitrate': 1, 'nitrous_oxide': 2, 'hydrogen_cyanide': 1, 'acetonitrile': 1,
           'nitromethane': 1, 'particulate_nitrate': 1}
NO_PER_MOLECULE = {'nitrogen_dioxide': 1, 'nitrate_radical': 1, 'dinitrogen_pentoxide': 2, 'nitric_acid': 1, 'dinitrogen_trioxide': 2,
                   'dinitrogen_tetroxide': 2, 'methyl_nitrate': 1, 'ethyl_nitrate': 1, 'n_propyl_nitrate': 1, 'n_butyl_nitrate': 1,
                   'nitrous_acid': 1, 'ammonia': 1, 'peroxyacetyl_nitrate': 1, 'peroxynitric_acid': 1, 'nitrous_oxide': 2, 'hydrogen_cyanide': 1,
                   'acetonitrile': 1, 'nitromethane': 1, 'particulate_nitrate': 1}
CLASS_ALIAS = {'peroxyacetyl_nitrate': 'peroxy_nitrates_ro2no2', 'dinitrogen_trioxide': 'nitrogen_dioxide', 'dinitrogen_tetroxide': 'nitrogen_dioxide'}


class ActionRejected(ValueError):
    """Precondition or argument failure; the runtime state is unchanged.  Messages never disclose sample composition."""


# ------------------------------------------------------------------------------------------------------------------- template library
_LIB = None


def library():
    global _LIB
    if _LIB is None:
        path = ROOT / TEMPLATE_REL
        raw = path.read_bytes()
        data = json.loads(raw)
        _LIB = dict(sha256=hashlib.sha256(raw).hexdigest(), by_id={r['record_id']: r for r in data['templates']}, data=data)
    return _LIB


def _rec(rid):
    return library()['by_id'][rid]


def _v(rid, key):
    return _rec(rid)['values'][key]['value']


def certificate_validity(standard_id):
    c = library()['by_id'][standard_id]['conditions']
    return c.get('validity')


def certificates():
    """{standard_id: (species, certificate value, U95, value kind)} of the connectable SRM cylinders.  Individually certified lots have no
    public value: the simulated cylinder's certificate is taken at the nominal fraction (assumption, flagged in the result)."""
    out = {}
    for rid, r in library()['by_id'].items():
        if r['family'] != 'calibration_standard':
            continue
        v = r['values']
        cert = v.get('certified_mole_fraction') or v.get('example_certified_mole_fraction')
        x = (cert or v['nominal_mole_fraction'])['value']
        out[rid] = (r['substance_id'], float(x), float(v['expanded_uncertainty_U95']['value']),
                    r['conditions'].get('value_kind') if cert else 'nominal_used_individually_certified')
    return out


def standards():
    """Certificate compositions of the connectable cylinders (balance N2) + two built-in zero gases."""
    out = {rid: {sp: x, 'nitrogen': 1.0 - x} for rid, (sp, x, u, kind) in certificates().items()}
    out.update(copy.deepcopy(STANDARD_EXTRA))
    return out


def cylinder_truth(instance_id, standard_id):
    """True mole fraction of the simulated cylinder: certificate value x (1 + d), d ~ N(0, (U95 / 2) / x) drawn deterministically from the
    instance and standard ids (the cylinder content is unknown within its certified uncertainty)."""
    sp, x, u, kind = certificates()[standard_id]
    h = hashlib.sha256(f'{instance_id}|{standard_id}'.encode()).digest()
    u1, u2 = (int.from_bytes(h[:8], 'big') + 0.5) / 2 ** 64, (int.from_bytes(h[8:16], 'big') + 0.5) / 2 ** 64
    z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
    return sp, x * (1.0 + z * (u / 2.0) / x)


def quench_coefficients(phi=0.0):
    """a_i in S/S0 = 1 / (1 + sum a_i x_i), relative to a nitrogen bath.  CO2 / H2O / O2: measured interference factors (NASA TM X-3229).
    Ar / H2 / N2O / NO2: relative quenching efficiencies (Clyne et al. via the same report) scaled by phi = the fraction of the chamber's third
    bodies contributed by the sample; phi is an explicit host assumption (default 0: the measured O2 factor ~0 contradicts phi = 1, which would
    give O2 +11 % over N2)."""
    a = {}
    for rid in ('quench_if_carbon_dioxide', 'quench_if_water', 'quench_if_oxygen'):
        a[_rec(rid)['substance_id']] = 0.0 - float(_v(rid, 'dlnS_dx'))
    q_n2 = float(_v('quench_rel_nitrogen', 'relative_quenching_efficiency_k3M_over_k3O2'))
    for rid in ('quench_rel_argon', 'quench_rel_hydrogen', 'quench_rel_nitrous_oxide', 'quench_rel_nitrogen_dioxide'):
        sid = _rec(rid)['substance_id']
        a.setdefault(sid, phi * (float(_v(rid, 'relative_quenching_efficiency_k3M_over_k3O2')) / q_n2 - 1.0))
    return a


def converter_class(sid):
    rid = 'converter_' + CLASS_ALIAS.get(sid, sid)
    r = library()['by_id'].get(rid)
    return r['values']['reducibility_class']['value'] if r else None


def henry_constant(sid, T):
    r = library()['by_id'].get('henry_' + sid)
    if r is None:
        return None
    h = float(r['values']['Hscp_298']['value'])
    b = r['values'].get('temperature_coefficient_B', {}).get('value')
    return h * math.exp(float(b) * (1.0 / T - 1.0 / 298.15)) if b is not None else h


def adsorption_tau(sid, material, area_m2):
    """(tau_rise_s, tau_decay_s) of the line wall memory: time per wetted area x area / ln(1000) (0.1 % criterion of the report)."""
    r = library()['by_id'].get(f"line_adsorption_{sid}_{ {'ptfe': 'teflon'}.get(material, material)}")
    if r is None or area_m2 <= 0:
        return 0.0, 0.0
    rise = r['values']['rise_time_per_area']['value']
    decay = r['values']['decay_time_per_area']['value']
    rise = decay if rise is None else rise
    return float(rise) * area_m2 / math.log(1000.0), float(decay) * area_m2 / math.log(1000.0)


def o2_converter_bias(x_o2):
    v = _rec('converter_o2_effect_stainless_800c')['values']
    xs, es = v['oxygen_mole_fraction']['value'], v['nox_mode_relative_error']['value']
    if x_o2 <= xs[0]:
        return es[0]
    for i in range(1, len(xs)):
        if x_o2 <= xs[i]:
            return es[i - 1] + (es[i] - es[i - 1]) * (x_o2 - xs[i - 1]) / (xs[i] - xs[i - 1])
    return es[-1]


def p_sat_water(T):
    """Saturation vapour pressure of water over liquid (Magnus-Tetens / Alduchov-Eskridge 1996 form; engineering formula)."""
    tc = T - 273.15
    return 610.94 * math.exp(17.625 * tc / (tc + 243.04))


# ------------------------------------------------------------------------------------------------------------------- dark chemistry
def _arr(v, T, key='A', ekey='E_over_R'):
    return float(v[key]['value']) * math.exp(-float(v[ekey]['value']) / T)


def _clamp(T, rng):
    return min(max(T, rng[0]), rng[1]) if rng else T


def _troe(v, T, n_m, clamped=None, row=None):
    """IUPAC Troe falloff; k0 and kinf are each evaluated with T clamped into their own evaluated range."""
    k0p, kip = v['k0_prefactor'], v['kinf_prefactor']
    T0, Ti = _clamp(T, k0p.get('T_range_K')), _clamp(T, kip.get('T_range_K'))
    if clamped is not None and (T0 != T or Ti != T):
        clamped.append(row)
    k0 = float(k0p['value']) * (T0 / 300.0) ** float(k0p['T_exponent_n'])
    if k0p.get('exp_minus_E_over_RT_K'):
        k0 *= math.exp(-float(k0p['exp_minus_E_over_RT_K']) / T0)
    ki = float(kip['value']) * (Ti / 300.0) ** float(kip['T_exponent_n'])
    if kip.get('exp_minus_E_over_RT_K'):
        ki *= math.exp(-float(kip['exp_minus_E_over_RT_K']) / Ti)
    k0m = k0 * n_m
    fc = float(v['Fc']['value'])
    nn = 0.75 - 1.27 * math.log10(fc)
    logf = math.log10(fc) / (1.0 + (math.log10(k0m / ki) / nn) ** 2)
    return k0m * ki / (k0m + ki) * 10 ** logf


def t_range(v):
    return v['T_range']['value'] if isinstance(v.get('T_range'), dict) else None


def rate_constants(T, n_m, clamped=None):
    """SI rate coefficients (molecule, m^3, s) of IUPAC rows 53-64 at T and bath number density n_m; T is clamped into each record's
    evaluated temperature range (the clamped rows are appended to `clamped`)."""
    k = {}
    T0 = T
    for i in range(53, 65):
        r = _rec(f'kinetics_iupac_{i}')
        law, v = r['conditions']['rate_law'], r['values']
        if law.startswith('troe'):
            k[i] = _troe(v, T0, n_m, clamped, i)
            continue
        T = _clamp(T0, t_range(v))
        if clamped is not None and T != T0:
            clamped.append(i)
        if law in ('termolecular_arrhenius', 'bimolecular_arrhenius'):
            k[i] = _arr(v, T)
        else:
            k[i] = float(v['k']['value'])
    return k


def _rates(n, k):
    no, no2, o3, no3, n2o5, hno3, n2o3, n2o4, o2, h2o = n
    return np.array([k[53] * no * no * o2, k[54] * no * o3, k[55] * no * no2, k[56] * n2o3, k[57] * no * no3, k[58] * no2 * o3,
                     k[59] * no2 * no2, k[60] * n2o4, k[61] * no2 * no3, k[62] * n2o5, k[63] * n2o5 * h2o, k[64] * n2o5 * h2o * h2o])


# stoichiometry: rows = species (CHEM order), columns = reactions 53..64
_S = np.zeros((10, 12))
_REACTIONS = [{0: -2, 8: -1, 1: 2}, {0: -1, 2: -1, 1: 1, 8: 1}, {0: -1, 1: -1, 6: 1}, {6: -1, 0: 1, 1: 1}, {0: -1, 3: -1, 1: 2},
              {1: -1, 2: -1, 3: 1, 8: 1}, {1: -2, 7: 1}, {7: -1, 1: 2}, {1: -1, 3: -1, 4: 1}, {4: -1, 1: 1, 3: 1}, {4: -1, 9: -1, 5: 2},
              {4: -1, 9: -1, 5: 2}]
for _j, _nu in enumerate(_REACTIONS):
    for _i, _c in _nu.items():
        _S[_i, _j] = _c


def integrate_dark_chemistry(x, T, p, t_res, clamped=None):
    """Mole fractions of the CHEM species after t_res seconds at (T, p): backward Euler + Newton on number densities, geometric substeps.
    Linear invariants (N atoms, O atoms) are preserved by construction up to the Newton tolerance."""
    n_m = p / (K_B * T)
    n0 = np.array([x.get(s, 0.0) for s in CHEM]) * n_m
    if t_res <= 0 or (n0[0] <= 0 and n0[1] <= 0 and n0[3] <= 0 and n0[4] <= 0 and n0[6] <= 0 and n0[7] <= 0):
        return {s: float(v / n_m) for s, v in zip(CHEM, n0)}
    k = rate_constants(T, n_m, clamped)
    n = n0.copy()
    t, h = 0.0, min(1e-7, t_res)
    while t < t_res - 1e-15:
        h = min(h, t_res - t)
        y = n.copy()
        for _ in range(30):
            f = y - n - h * (_S @ _rates(y, k))
            J = np.eye(10)
            for i in range(10):                                  # finite-difference Jacobian of the rates (10 x 10)
                d = max(abs(y[i]) * 1e-7, 1e-30 * n_m + 1.0)
                yp = y.copy()
                yp[i] += d
                J[:, i] -= h * (_S @ (_rates(yp, k) - _rates(y, k))) / d
            dy = np.linalg.solve(J, -f)
            y = y + dy
            if np.all(np.abs(dy) <= 1e-10 * np.maximum(np.abs(y), 1e-12 * n_m)):
                break
        y = np.where(y < 0, np.where(y > -1e-12 * n_m, 0.0, y), y)
        n = y
        t += h
        h *= 1.6
    return {s: float(v / n_m) for s, v in zip(CHEM, n)}


def n_atoms(x):
    return sum(N_ATOMS.get(s, 0) * v for s, v in x.items())


# ------------------------------------------------------------------------------------------------------------------- schemas
def number(unit, lo=None, hi=None):
    d = {'type': 'number', 'x-unit': unit}
    if lo is not None:
        d['minimum'] = lo
    if hi is not None:
        d['maximum'] = hi
    return d


def obj(props, required=None):
    return dict(type='object', properties=props, required=list(props) if required is None else required, additionalProperties=False)


def nullable(s):
    return {'anyOf': [s, {'type': 'null'}]}


BOOL, TEXT = {'type': 'boolean'}, {'type': 'string'}
FRACTION = number('mol/mol', 0.0, 1.0)
FS_ARRAY = {'type': 'array', 'x-unit': 'mol/mol', 'items': number('mol/mol', FS_MIN, FS_MAX), 'minItems': 4, 'maxItems': 4}
STATUS = {'type': 'string', 'enum': ['off', 'warming', 'ready']}
MEAS = {'type': 'string', 'enum': ['idle', 'measuring']}
CALP = {'type': 'string', 'enum': ['idle', 'zeroing', 'spanning']}
FLAGS = {'type': 'array', 'items': {'type': 'string', 'enum': ['warming_up', 'over_range', 'inlet_pressure_out_of_range', 'ambient_out_of_range',
                                                                'no_ozone', 'calibration_running']}}
MATERIAL = {'type': 'string', 'enum': ['ptfe', 'stainless']}
COMPOSITION = {'type': 'object', 'additionalProperties': number('mol/mol', 0.0, 1.0)}
POINT = obj({'time_s': number('s', 0), 'no_mol_mol': nullable(number('mol/mol')), 'no2_mol_mol': nullable(number('mol/mol')),
             'nox_mol_mol': nullable(number('mol/mol'))})
PHASE_OP = {'type': 'string', 'enum': ['running', 'succeeded', 'cancelled', 'failed']}
CAL_RESULT = obj({'phase': CALP, 'operation_id': TEXT})
MEASURE = obj({'status': STATUS, 'measurement': MEAS, 'quality': {'type': 'string', 'enum': ['valid', 'invalid', 'off']},
               'no_mol_mol': nullable(number('mol/mol')), 'no2_mol_mol': nullable(number('mol/mol')), 'nox_mol_mol': nullable(number('mol/mol')),
               'no_ppm': nullable(number('ppm')), 'no2_ppm': nullable(number('ppm')), 'nox_ppm': nullable(number('ppm')),
               'range_index': {'type': 'integer', 'minimum': 0, 'maximum': 3}, 'full_scale_mol_mol': number('mol/mol', FS_MIN, FS_MAX),
               'flags': FLAGS, 'series': {'type': 'array', 'items': POINT}, 'template_source': TEXT, 'library_sha256': TEXT,
               'template_records': {'type': 'array', 'items': TEXT},
               'evidence': {'type': 'string', 'const': 'simulated_from_cited_response_parameters'}})
STANDARD_IDS = sorted(standards())
MODEL_RECORDS = sorted(library()['by_id'])     # every record family is used by the response model (static: no per-sample leak)
PLANT = obj({'hot_tubing_installed': BOOL, 'pressure_regulation_installed': BOOL, 'line_length_m': number('m', 0.0, 50.0),
             'line_inner_diameter_m': number('m', 0.001, 0.02), 'line_material': MATERIAL, 'line_temperature_k': number('K', 253.15, 473.15),
             'ambient_temperature_k': number('K', 253.15, 333.15), 'ambient_pressure_pa': number('Pa', 60000.0, 120000.0),
             'ambient_relative_humidity': number('1', 0.0, 1.0), 'converter_no2_efficiency': number('1', 0.0, 1.0),
             'partial_conversion_fraction': nullable(number('1', 0.0, 1.0)), 'quench_relative_scale': number('1', 0.0, 1.0), 'noise_enabled': BOOL, 'seed': {'type': 'integer', 'minimum': 0, 'x-unit': '1'}},
            required=[])
PLANT_EXAMPLE = dict(hot_tubing_installed=False, pressure_regulation_installed=False, line_length_m=2.0, line_inner_diameter_m=0.004,
                     line_material='ptfe', line_temperature_k=298.15, ambient_temperature_k=298.15, ambient_pressure_pa=101325.0,
                     ambient_relative_humidity=0.5, converter_no2_efficiency=1.0, partial_conversion_fraction=None, quench_relative_scale=0.0,
                     noise_enabled=True, seed=0)
INLET = obj({'composition': COMPOSITION, 'pressure_pa': number('Pa', 10000.0, 300000.0), 'temperature_k': number('K', 253.15, 773.15), 'label': TEXT})
INLET_EXAMPLE = dict(composition={'nitric_oxide': 250e-6, 'nitrogen_dioxide': 20e-6, 'oxygen': 0.06, 'carbon_dioxide': 0.08, 'water': 0.012},
                     pressure_pa=101325.0, temperature_k=298.15, label='scr_outlet')

# capability: input, output, components, meaning, kind, visibility, example, state effects
CAPS = {
    'power.set': (obj({'enabled': BOOL}), obj({'enabled': BOOL}), ['switch_power', 'power_inlet', 'touchscreen'],
                  '整机通断；断电停止测量、取消校准，转换器与PMT回到环境温度', 'device_command', 'agent', {'enabled': True},
                  ['power.enabled', 'status.phase', 'measurement.phase', 'calibration.phase']),
    'power.toggle': (obj({}), obj({'enabled': BOOL}), ['switch_power'], '电源翘板按一次切换通断（Matterix按键）', 'device_command', 'agent', {},
                     ['power.enabled', 'status.phase']),
    'range.configure': (obj({'full_scales_mol_mol': FS_ARRAY}), obj({'full_scales_mol_mol': FS_ARRAY}), ['touchscreen', 'display_digits'],
                        '四个可自由选择的满量程（5–5000 ppm，升序）', 'configuration', 'agent', {'full_scales_mol_mol': DEFAULT_FS}, ['range.full_scales_mol_mol']),
    'range.select': (obj({'index': {'type': 'integer', 'minimum': 0, 'maximum': 3, 'x-unit': '1'}}), obj({'index': {'type': 'integer', 'minimum': 0, 'maximum': 3, 'x-unit': '1'}}),
                     ['touchscreen', 'display_digits'], '选择当前量程（超量程只标记，不自动切换）', 'configuration', 'agent', {'index': 2},
                     ['range.index', 'range.active_full_scale_mol_mol']),
    'filter.set': (obj({'time_constant_s': number('s', 0.0, 60.0)}), obj({'time_constant_s': number('s', 0.0, 60.0)}), ['touchscreen', 'detector_block'],
                   '输出一阶滤波时间常数；噪声与最低可检浓度随之变化', 'configuration', 'agent', {'time_constant_s': 1.0}, ['filter.time_constant_s']),
    'measurement.start': (obj({}), obj({'phase': MEAS}), ['softkey_measure', 'detector_block', 'converter', 'vacuum_pump', 'display_digits'],
                          '开始连续测量并以1 Hz记录NO/NO2/NOx时间序列（需预热完成）', 'device_command', 'agent', {}, ['measurement.phase', 'measurement.points']),
    'measurement.stop': (obj({}), obj({'phase': MEAS}), ['softkey_measure', 'display_digits'], '停止连续测量（保留已记录序列）', 'device_command', 'agent', {},
                         ['measurement.phase']),
    'measurement.toggle': (obj({}), obj({'phase': MEAS}), ['softkey_measure'], '测量触摸键：开始/停止切换', 'device_command', 'agent', {}, ['measurement.phase']),
    'calibration.configure': (obj({'span_no_mol_mol': number('mol/mol', FS_MIN * 0.2, FS_MAX), 'span_nox_mol_mol': number('mol/mol', FS_MIN * 0.2, FS_MAX),
                                   'window_s': number('s', 5.0, 600.0)}),
                              obj({'span_no_mol_mol': number('mol/mol'), 'span_nox_mol_mol': number('mol/mol'), 'window_s': number('s')}),
                              ['touchscreen'], '输入跨度气证书浓度（NO与NOx通道）与平均窗口', 'configuration', 'agent',
                              {'span_no_mol_mol': 2954e-6, 'span_nox_mol_mol': 2954e-6, 'window_s': 30.0},
                              ['calibration.span_no_mol_mol', 'calibration.span_nox_mol_mol', 'calibration.window_s']),
    'calibration.zero': (obj({}), CAL_RESULT, ['softkey_zero', 'detector_block', 'converter'], '零点校准：在入口零气下按窗口平均两通道读数并写入零点偏置（异步）',
                         'device_command', 'agent', {}, ['calibration.phase', 'calibration.zero_offset_no_mol_mol', 'calibration.zero_offset_nox_mol_mol']),
    'calibration.span': (obj({}), CAL_RESULT, ['softkey_span', 'detector_block', 'converter'], '跨度校准：在入口跨度气下按窗口平均并按证书浓度写入增益（异步）',
                         'device_command', 'agent', {}, ['calibration.phase', 'calibration.gain_no', 'calibration.gain_nox']),
    'calibration.cancel': (obj({}), obj({'phase': CALP}), ['touchscreen'], '取消正在进行的校准，保留原校准系数', 'device_command', 'agent', {}, ['calibration.phase']),
    'inlet.connect_standard': (obj({'standard_id': {'type': 'string', 'enum': STANDARD_IDS}}),
                               obj({'standard_id': TEXT, 'source': TEXT, 'certificate_mole_fraction': nullable(number('mol/mol', 0, 1)),
                                    'expanded_uncertainty_U95': nullable(number('mol/mol', 0, 1)), 'value_kind': TEXT,
                                    'certificate_validity': nullable(TEXT), 'certificate_expired': BOOL}),
                               ['sample_inlet', 'inlet_filter'], '把标准气瓶（模板库NIST SRM或零气）经减压阀接到样气入口（101325 Pa，有放空）', 'device_command',
                               'agent', {'standard_id': 'srm_2631a'}, ['inlet.source']),
    'inlet.disconnect': (obj({}), obj({'source': TEXT}), ['sample_inlet'], '断开入口：泵抽入室内空气', 'device_command', 'agent', {}, ['inlet.source']),
    'inlet.connect': (INLET, obj({'source': TEXT}), ['sample_inlet', 'inlet_filter'], '宿主接入样品气（组成、绝压、温度为过程真值）', 'configuration',
                      'evaluator', INLET_EXAMPLE, ['inlet.source', 'inlet.composition', 'inlet.pressure_pa', 'inlet.temperature_k']),
    'plant.configure': (PLANT, obj({'configured': BOOL}), ['housing', 'converter', 'sample_inlet'],
                        '宿主配置已装选件（h热管路、r压力调节）、外部样气管、环境与转换器参数', 'configuration', 'evaluator', PLANT_EXAMPLE,
                        ['line.condensed_water_fraction']),
    'nox.measure': (obj({'max_points': {'type': 'integer', 'minimum': 0, 'maximum': SERIES_MAX, 'x-unit': '1'}}, required=[]), MEASURE,
                    ['display_digits', 'detector_block', 'converter'], '读取当前NO/NO2/NOx显示值、量程、状态标志与时间序列（纯读取，不推进时钟）', 'query',
                    'agent', {'max_points': 60}, []),
}
UNSUPPORTED = {
    'calibration.valve_select': ('V1/V2校准电磁阀选件（单阀或零/跨度双阀、2–3 bar加压校准）未建模', ['sample_inlet']),
    'analog_output.configure': ('外接模拟输出盒（0–10 V，4–20 mA）未建模', ['power_inlet']),
    'data.export': ('USB/LAN/WLAN/蓝牙/HDMI数据通信与导出未实现', ['usb_ports', 'lan_port', 'hdmi_port']),
}

# slot: schema, unit, origin, visibility, components, write (capability, argument), meaning
SLOTS = {
    'power.enabled': (BOOL, '1', 'setpoint', 'agent', ['switch_power'], ('power.set', 'enabled'), '整机电源设定'),
    'status.phase': (STATUS, '1', 'derived', 'agent', ['touchscreen'], None, '预热状态：off/warming/ready（转换器±10 K、PMT±1 K内为ready）'),
    'status.flags': (FLAGS, '1', 'derived', 'agent', ['touchscreen'], None, 'GUI状态标志（预热、超量程、入口压力越界、环境越界、无臭氧、校准中、部分转换按假设）'),
    'converter.temperature_k': (number('K', 0), 'K', 'process_truth', 'evaluator', ['converter'], None, '金属转换器温度真值（设定点648 K为假设）'),
    'pmt.temperature_k': (number('K', 0), 'K', 'process_truth', 'evaluator', ['detector_block'], None, '冷却PMT温度真值（设定点268 K为假设）'),
    'ozone.generator_fraction': (number('1', 0, 1), '1', 'process_truth', 'evaluator', ['ozone_generator'], None, '臭氧发生器相对输出（0–1）'),
    'range.full_scales_mol_mol': (FS_ARRAY, 'mol/mol', 'setpoint', 'agent', ['touchscreen'], ('range.configure', 'full_scales_mol_mol'), '四个满量程'),
    'range.index': ({'type': 'integer', 'minimum': 0, 'maximum': 3, 'x-unit': '1'}, '1', 'setpoint', 'agent', ['touchscreen'], ('range.select', 'index'), '当前量程序号'),
    'range.active_full_scale_mol_mol': (number('mol/mol', FS_MIN, FS_MAX), 'mol/mol', 'derived', 'agent', ['display_digits'], None, '当前量程满量程'),
    'filter.time_constant_s': (number('s', 0.0, 60.0), 's', 'setpoint', 'agent', ['touchscreen'], ('filter.set', 'time_constant_s'), '输出滤波时间常数'),
    'measurement.phase': (MEAS, '1', 'derived', 'agent', ['display_digits'], None, '连续测量状态'),
    'measurement.points': ({'type': 'integer', 'minimum': 0}, '1', 'derived', 'agent', ['display_digits'], None, '已记录的1 Hz序列点数'),
    'calibration.phase': (CALP, '1', 'derived', 'agent', ['touchscreen'], None, '校准状态'),
    'calibration.span_no_mol_mol': (number('mol/mol'), 'mol/mol', 'setpoint', 'agent', ['touchscreen'], None, 'NO通道跨度气证书浓度'),
    'calibration.span_nox_mol_mol': (number('mol/mol'), 'mol/mol', 'setpoint', 'agent', ['touchscreen'], None, 'NOx通道跨度气证书浓度'),
    'calibration.window_s': (number('s', 5.0, 600.0), 's', 'setpoint', 'agent', ['touchscreen'], None, '校准平均窗口'),
    'calibration.zero_offset_no_mol_mol': (number('mol/mol'), 'mol/mol', 'derived', 'agent', ['detector_block'], None, 'NO通道零点偏置（归一化信号单位）'),
    'calibration.zero_offset_nox_mol_mol': (number('mol/mol'), 'mol/mol', 'derived', 'agent', ['detector_block'], None, 'NOx通道零点偏置'),
    'calibration.gain_no': (number('1', 0), '1', 'derived', 'agent', ['detector_block'], None, 'NO通道增益（出厂1.0）'),
    'calibration.gain_nox': (number('1', 0), '1', 'derived', 'agent', ['detector_block'], None, 'NOx通道增益（出厂1.0）'),
    'inlet.source': (TEXT, '1', 'derived', 'agent', ['sample_inlet'], None, '入口接的是什么：ambient_air / standard:<id> / sample（宿主样品，标签只对evaluator可见）'),
    'inlet.indicated_pressure_pa': (number('Pa', 0), 'Pa', 'derived', 'agent', ['sample_inlet', 'touchscreen'], None, '入口绝压显示（一阶0.5 s，1 Pa分辨率）'),
    'flow.indicated_sample_m3_s': (number('m^3/s', 0), 'm^3/s', 'derived', 'agent', ['flow_restrictors', 'touchscreen'], None, '样气体积流量显示'),
    'inlet.composition': (COMPOSITION, 'mol/mol', 'process_truth', 'evaluator', ['sample_inlet'], None, '入口气体组成真值（含平衡N2）'),
    'inlet.pressure_pa': (number('Pa', 0), 'Pa', 'process_truth', 'evaluator', ['sample_inlet'], None, '入口绝压真值'),
    'inlet.temperature_k': (number('K', 0), 'K', 'process_truth', 'evaluator', ['sample_inlet'], None, '入口气体温度真值'),
    'chamber.no_mol_mol': (number('mol/mol', 0), 'mol/mol', 'process_truth', 'evaluator', ['detector_block'], None, 'NO反应室的等效NO摩尔分数真值（传输、混合后）'),
    'chamber.nox_mol_mol': (number('mol/mol', 0), 'mol/mol', 'process_truth', 'evaluator', ['detector_block', 'converter'], None, 'NOx反应室（转换后）的等效NO真值'),
    'inlet.label': (nullable(TEXT), '1', 'process_truth', 'evaluator', ['sample_inlet'], None, '宿主样品的标签（agent只看到sample）'),
    'converter.partial_assumption_applied': (BOOL, '1', 'process_truth', 'evaluator', ['converter'], None,
                                             '到达气中含“部分还原”类物种且使用了缺省0.5假设（未由宿主给定）'),
    'line.condensed_water_fraction': (number('mol/mol', 0, 1), 'mol/mol', 'process_truth', 'evaluator', ['sample_inlet'], None, '未加热路径中冷凝的水（每摩尔样气）'),
    'display.no_mol_mol': (nullable(number('mol/mol')), 'mol/mol', 'derived', 'agent', ['display_digits'], None, '屏幕NO读数（0.1 ppm分辨率；关机为null）'),
    'display.no2_mol_mol': (nullable(number('mol/mol')), 'mol/mol', 'derived', 'agent', ['display_digits'], None, '屏幕NO2读数=NOx−NO'),
    'display.nox_mol_mol': (nullable(number('mol/mol')), 'mol/mol', 'derived', 'agent', ['display_digits'], None, '屏幕NOx读数'),
}
SCENE_MIRRORED = ('display.no_mol_mol', 'display.no2_mol_mol', 'display.nox_mol_mol', 'power.enabled', 'status.phase', 'measurement.phase', 'calibration.phase')
REFERENCE_ZH = {
    'inlet.indicated_pressure_pa': '绝压（厂家进口600–1200 mbar abs.）。',
    'inlet.pressure_pa': '绝压。',
    'flow.indicated_sample_m3_s': '气体体积流量，按101325 Pa、298.15 K参考状态（假设；厂家1.0 l/min未注明参考状态）；临界限流下与入口绝压成正比。',
}
for _k in ('range.full_scales_mol_mol', 'range.active_full_scale_mol_mol', 'calibration.span_no_mol_mol', 'calibration.span_nox_mol_mol',
           'display.no_mol_mol', 'display.no2_mol_mol', 'display.nox_mol_mol', 'chamber.no_mol_mol', 'chamber.nox_mol_mol', 'inlet.composition',
           'calibration.zero_offset_no_mol_mol', 'calibration.zero_offset_nox_mol_mol'):
    REFERENCE_ZH[_k] = '摩尔分数（湿基：样气按到达状态，不干燥；1 ppm = 1e-6 mol/mol），与温压无关。'
REFERENCE_ZH['line.condensed_water_fraction'] = '每摩尔入口湿样气在未加热路径中冷凝出的水（摩尔比）。'


def validate(schema, value):
    if 'anyOf' in schema:
        for s in schema['anyOf']:
            try:
                validate(s, value)
                return
            except ActionRejected:
                continue
        raise ActionRejected('INVALID_ARGUMENT')
    typ = schema.get('type')
    if typ == 'number':
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ActionRejected('INVALID_ARGUMENT')
        if value < schema.get('minimum', -math.inf) or value > schema.get('maximum', math.inf):
            raise ActionRejected('INVALID_ARGUMENT')
    elif typ == 'integer':
        if isinstance(value, bool) or not isinstance(value, int):
            raise ActionRejected('INVALID_ARGUMENT')
        if value < schema.get('minimum', -math.inf) or value > schema.get('maximum', math.inf):
            raise ActionRejected('INVALID_ARGUMENT')
    elif typ == 'boolean' and type(value) is not bool:
        raise ActionRejected('INVALID_ARGUMENT')
    elif typ == 'null' and value is not None:
        raise ActionRejected('INVALID_ARGUMENT')
    elif typ == 'string' and not isinstance(value, str):
        raise ActionRejected('INVALID_ARGUMENT')
    elif typ == 'array':
        if not isinstance(value, list) or len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', 10 ** 9):
            raise ActionRejected('INVALID_ARGUMENT')
        for v in value:
            validate(schema.get('items', {}), v)
    elif typ == 'object':
        if not isinstance(value, dict):
            raise ActionRejected('INVALID_ARGUMENT')
        props = schema.get('properties', {})
        if not set(schema.get('required', [])) <= value.keys():
            raise ActionRejected('INVALID_ARGUMENT')
        for key, val in value.items():
            if not isinstance(key, str):
                raise ActionRejected('INVALID_ARGUMENT')
            sub = props.get(key, schema.get('additionalProperties', True))
            if sub is False:
                raise ActionRejected('INVALID_ARGUMENT')
            if isinstance(sub, dict):
                validate(sub, val)
    if 'enum' in schema and value not in schema['enum']:
        raise ActionRejected('INVALID_ARGUMENT')
    if 'const' in schema and value != schema['const']:
        raise ActionRejected('INVALID_ARGUMENT')


def _clean_composition(comp):
    known = set(N_ATOMS) | set(quench_coefficients()) | {'oxygen', 'nitrogen', 'water', 'ozone', 'carbon_dioxide', 'carbon_monoxide', 'methane',
                                                          'sulfur_dioxide', 'hydrogen_sulfide', 'argon', 'hydrogen'}
    if not set(comp) <= known:
        raise ActionRejected('INVALID_ARGUMENT')
    total = sum(comp.values())
    if total > 1.0 + 1e-9:
        raise ActionRejected('INVALID_ARGUMENT')
    out = {k: float(v) for k, v in comp.items() if v > 0}
    out['nitrogen'] = out.get('nitrogen', 0.0) + max(0.0, 1.0 - total)     # balance N2 (documented)
    return out


class EcoNcld822Runtime:
    """Process model + host clock state for one analyzer instance (the host is the clock authority: step(dt_s) only)."""

    def __init__(self, instance_id='ncld822_01'):
        self.instance_id = instance_id
        self.time_s, self.revision = 0.0, 0
        self.powered = False
        self.t_conv = self.t_pmt = T_REF
        self.o3_frac = 0.0
        self.full_scales = list(DEFAULT_FS)
        self.range_index = 2
        self.filter_tau = 1.0
        self.measuring = False
        self.cal_phase, self.cal_op, self._cal_t, self._cal_sum = 'idle', None, 0.0, [0.0, 0.0]
        self.cal_last = None
        self.span_no = self.span_nox = 2954e-6                   # SRM 2631a lot value (valid certificate)
        self.cal_window = 30.0
        self.zero_no = self.zero_nox = 0.0
        self.gain_no = self.gain_nox = 1.0
        self.plant = dict(PLANT_EXAMPLE, line_length_m=0.0)
        self.source = 'ambient_air'
        self.comp = self._ambient_air()
        self.p_in, self.T_in = self.plant['ambient_pressure_pa'], self.plant['ambient_temperature_k']
        self.p_ind = self.p_in
        self.wall = dict(no_ch=0.0, no=0.0, no2=0.0)                     # channel parts after the line wall memory
        self.queue = deque()                                                # (arrival time, chamber NO, chamber NOx, quench, condensed)
        self.ch = [0.0, 0.0]                                                # mixed chamber equivalents (NO, NOx), quench applied
        self.ch_true = [0.0, 0.0]
        self.cond = 0.0
        self.y = [0.0, 0.0]                                                 # filtered indicated (raw units, before calibration)
        self.noise = [0.0, 0.0]
        self.series = []
        self._next_sample_t = 0.0
        self._rng = random.Random(f'{instance_id}:0')
        self._cache = {}
        self._operation_count = 0
        self._last_event = 0.0
        self.label = None
        self._principals = {'__public_agent__': 'agent'}
        self._requests, self._owners = {}, {}
        self._arrived = self._transport(self.comp)
        self.queue.append((0.0, self._arrived))                           # the line is already filled with room air

    # -------------------------------------------------------------------------------------------------------------- helpers
    def _ambient_air(self):
        T, p, rh = self.plant['ambient_temperature_k'], self.plant['ambient_pressure_pa'], self.plant['ambient_relative_humidity']
        xw = min(0.2, rh * p_sat_water(T) / p)
        base = {'nitrogen': 0.7808, 'oxygen': 0.2095, 'argon': 0.0093, 'carbon_dioxide': 0.0004}
        return _clean_composition({k: v * (1 - xw) for k, v in base.items()} | {'water': xw})

    def _flow(self):
        return Q_NOM * self.p_in / P_REF

    def _path_temperature(self):
        return T_HOT_S if self.plant['hot_tubing_installed'] else min(self.plant['line_temperature_k'] if self.plant['line_length_m'] > 0 else 1e9,
                                                                       self.plant['ambient_temperature_k'])

    def _line_volume(self):
        return math.pi * self.plant['line_inner_diameter_m'] ** 2 / 4.0 * self.plant['line_length_m']

    def _residence(self):
        q = max(self._flow(), 1e-12)
        return LAG_INT_S * Q_NOM / q + self._line_volume() * (self.p_in / P_REF) / q

    def _transport(self, comp):
        """Inlet composition -> (NO channel eq., NOx channel eq., quench factor, condensed water, used records); cached."""
        key = json.dumps([sorted(comp.items()), self.p_in, self.T_in, sorted(self.plant.items())], sort_keys=True, default=str)
        if key in self._cache:
            return self._cache[key]
        used = set()
        x = dict(comp)
        cond = 0.0
        T_path = self._path_temperature()
        xw = x.get('water', 0.0)
        ps = p_sat_water(T_path)
        if xw * self.p_in > ps:                                             # condensation in an unheated path: Henry partition
            x_sat = ps / self.p_in
            cond = (xw - x_sat) / (1.0 - x_sat)                             # mol water condensed per mol wet inlet gas
            v_l_per_v_g = cond * (0.018015 / 997.0) / (R_GAS * T_path / self.p_in)
            for sid in list(x):
                h = henry_constant(sid, T_path)
                if h is None or sid in ('water',):
                    continue
                f = 1.0 / (1.0 + h * R_GAS * T_path * v_l_per_v_g)
                x[sid] *= f
                used.add('henry_' + sid)
            x['water'] = x_sat * (1.0 - cond)
            tot = sum(x.values())
            x = {k: v / tot for k, v in x.items()}
        chem_in = {s: x.get(s, 0.0) for s in CHEM}
        clamped = []
        out = integrate_dark_chemistry(chem_in, T_path if T_path < 1e8 else self.T_in, self.p_in, self._residence(), clamped)
        if any(chem_in[s] > 0 for s in ('nitric_oxide', 'nitrogen_dioxide', 'ozone', 'nitrate_radical', 'dinitrogen_pentoxide')):
            used.update(f'kinetics_iupac_{i}' for i in range(53, 65))
        for s in CHEM:
            x[s] = out[s]
        no_ch = x.get('nitric_oxide', 0.0)
        eff = self.plant['converter_no2_efficiency']
        part = self.plant['partial_conversion_fraction']
        nox_no2, nox_other, partial_used = 0.0, 0.0, False
        for sid, v in x.items():
            if sid == 'nitric_oxide' or v <= 0 or sid not in NO_PER_MOLECULE:
                continue
            cls = converter_class(sid)
            if cls is None:
                continue
            used.add('converter_' + CLASS_ALIAS.get(sid, sid))
            if cls == 'efficient':
                frac = eff if sid in ('nitrogen_dioxide', 'dinitrogen_trioxide', 'dinitrogen_tetroxide') else 1.0
            elif cls == 'not_efficient':
                frac = 0.0
            else:
                frac = 0.5 if part is None else part
                partial_used = True
            if sid in ('nitrogen_dioxide', 'dinitrogen_trioxide', 'dinitrogen_tetroxide'):
                nox_no2 += frac * NO_PER_MOLECULE[sid] * v
            else:
                nox_other += frac * NO_PER_MOLECULE[sid] * v
        bias = 1.0 + o2_converter_bias(x.get('oxygen', 0.0))
        parts = dict(no=no_ch * bias, no2=nox_no2 * bias, other=nox_other * bias)       # NOx channel = NO-, NO2- and other-NOy-derived NO
        nox_ch = sum(parts.values())
        used.add('converter_o2_effect_stainless_800c')
        a = quench_coefficients(self.plant['quench_relative_scale'])
        qf = 1.0 / (1.0 + sum(a.get(s, 0.0) * v for s, v in x.items()))
        used.update({'quench_if_carbon_dioxide', 'quench_if_water', 'quench_if_oxygen', 'quench_rel_nitrogen'})
        if self.plant['quench_relative_scale'] > 0:
            used.update('quench_rel_' + s for s in ('argon', 'hydrogen', 'nitrous_oxide', 'nitrogen_dioxide') if x.get(s, 0) > 0)
        res = dict(no=no_ch, nox=nox_ch, nox_parts=parts, quench=qf, cond=cond, used=sorted(used), partial=partial_used, after=x,
                   kinetics_clamped=clamped)
        if len(self._cache) > 256:
            self._cache.clear()
        self._cache[key] = res
        return res

    def _ready(self):
        return self.powered and abs(self.t_conv - T_CONV_SET) < 10.0 and abs(self.t_pmt - T_PMT_SET) < 1.0

    def _status(self):
        return 'off' if not self.powered else ('ready' if self._ready() else 'warming')

    def _indicated(self):
        """(NO, NOx) indicated mole fractions after calibration (None when off)."""
        if not self.powered:
            return None
        return [self.gain_no * (self.y[0] + self.noise[0] - self.zero_no), self.gain_nox * (self.y[1] + self.noise[1] - self.zero_nox)]

    def _display(self):
        ind = self._indicated()
        if ind is None:
            return None, None, None
        no, nox = (round(v / DIGITS) * DIGITS for v in ind)
        return no, round((nox - no) / DIGITS) * DIGITS, nox

    def _flags(self):
        f = []
        if self.powered and not self._ready():
            f.append('warming_up')
        no, no2, nox = self._display()
        fs = self.full_scales[self.range_index]
        if nox is not None and max(abs(no), abs(nox)) > fs:
            f.append('over_range')
        if not (P_MIN <= self.p_ind <= P_MAX):                              # the analyzer only knows its indicated inlet pressure
            f.append('inlet_pressure_out_of_range')
        if not (278.15 <= self.plant['ambient_temperature_k'] <= 313.15):
            f.append('ambient_out_of_range')
        if self.powered and self.o3_frac < 0.5:
            f.append('no_ozone')
        if self.cal_phase != 'idle':
            f.append('calibration_running')
        return f

    def _values(self):
        no, no2, nox = self._display()
        return {'power.enabled': self.powered, 'status.phase': self._status(), 'status.flags': self._flags(),
                'converter.temperature_k': self.t_conv, 'pmt.temperature_k': self.t_pmt, 'ozone.generator_fraction': self.o3_frac,
                'range.full_scales_mol_mol': list(self.full_scales), 'range.index': self.range_index,
                'range.active_full_scale_mol_mol': self.full_scales[self.range_index], 'filter.time_constant_s': self.filter_tau,
                'measurement.phase': 'measuring' if self.measuring else 'idle', 'measurement.points': len(self.series),
                'calibration.phase': self.cal_phase, 'calibration.span_no_mol_mol': self.span_no, 'calibration.span_nox_mol_mol': self.span_nox,
                'calibration.window_s': self.cal_window, 'calibration.zero_offset_no_mol_mol': self.zero_no,
                'calibration.zero_offset_nox_mol_mol': self.zero_nox, 'calibration.gain_no': self.gain_no, 'calibration.gain_nox': self.gain_nox,
                'inlet.source': self.source, 'inlet.indicated_pressure_pa': float(round(self.p_ind)),
                'flow.indicated_sample_m3_s': self._flow() if self.powered else 0.0, 'inlet.composition': dict(self.comp), 'inlet.pressure_pa': self.p_in,
                'inlet.temperature_k': self.T_in, 'chamber.no_mol_mol': self.ch_true[0], 'chamber.nox_mol_mol': self.ch_true[1],
                'line.condensed_water_fraction': self._arrived['cond'], 'inlet.label': self.label,
                'converter.partial_assumption_applied': bool(self._arrived.get('partial') and self.plant['partial_conversion_fraction'] is None), 'display.no_mol_mol': no, 'display.no2_mol_mol': no2, 'display.nox_mol_mol': nox}

    def read(self, visibility='agent'):
        return copy.deepcopy({k: v for k, v in self._values().items() if SLOTS[k][3] == 'agent' or visibility in ('evaluator', 'debug')})

    # -------------------------------------------------------------------------------------------------------------- protocol
    def register_principal(self, principal_id, visibility='agent'):
        if visibility not in ('agent', 'evaluator', 'debug'):
            raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals:
            raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id] = visibility

    def session(self, principal_id='__public_agent__'):
        if principal_id not in self._principals:
            raise ActionRejected('ACCESS_DENIED')
        return EcoNcld822Session(self, principal_id)

    def describe(self):
        return self.session().describe(instance_id=self.instance_id)

    def debug_snapshot(self):
        return copy.deepcopy(dict(state=self.read('evaluator'), queue=list(self.queue), ch=self.ch, y=self.y, noise=self.noise, series=self.series,
                                  wall=self.wall, cal=(self.cal_phase, self.cal_op, self._cal_t, self._cal_sum),
                                  records_used=self._arrived['used'], kinetics_clamped=self._arrived.get('kinetics_clamped')))

    def _measure(self, max_points=60):
        no, no2, nox = self._display()
        flags = self._flags()
        quality = 'off' if not self.powered else ('valid' if not flags else 'invalid')
        lib = library()
        ppm = (lambda v: None if v is None else round(v / PPM, 1))
        return dict(status=self._status(), measurement='measuring' if self.measuring else 'idle', quality=quality,
                    no_mol_mol=no, no2_mol_mol=no2, nox_mol_mol=nox, no_ppm=ppm(no), no2_ppm=ppm(no2), nox_ppm=ppm(nox),
                    range_index=self.range_index, full_scale_mol_mol=self.full_scales[self.range_index], flags=flags,
                    series=copy.deepcopy(self.series[-max_points:] if max_points else []), template_source=TEMPLATE_REL, library_sha256=lib['sha256'],
                    template_records=MODEL_RECORDS, evidence='simulated_from_cited_response_parameters')

    def invoke(self, capability, **a):
        if capability in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED_OPTION')
        if capability not in CAPS:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability][0], a)
        if capability == 'nox.measure':
            return self._measure(a.get('max_points', 60))
        if capability in ('range.configure', 'calibration.configure', 'plant.configure') and self.cal_phase != 'idle':
            raise ActionRejected('BUSY')
        if capability == 'power.set' or capability == 'power.toggle':
            on = a['enabled'] if capability == 'power.set' else not self.powered
            self.powered = on
            if not on:
                self.measuring = False
                if self.cal_phase != 'idle':
                    self.cal_phase, self.cal_last = 'idle', dict(operation_id=self.cal_op, status='cancelled')
            result = {'enabled': on}
        elif capability == 'range.configure':
            fs = [float(v) for v in a['full_scales_mol_mol']]
            if any(b <= c for c, b in zip(fs, fs[1:])):
                raise ActionRejected('INVALID_ARGUMENT')
            self.full_scales = fs
            result = {'full_scales_mol_mol': list(fs)}
        elif capability == 'range.select':
            self.range_index = a['index']
            result = {'index': a['index']}
        elif capability == 'filter.set':
            self.filter_tau = float(a['time_constant_s'])
            result = {'time_constant_s': self.filter_tau}
        elif capability in ('measurement.start', 'measurement.stop', 'measurement.toggle'):
            start = capability == 'measurement.start' or (capability == 'measurement.toggle' and not self.measuring)
            if start:
                if not self._ready():
                    raise ActionRejected('PRECONDITION_FAILED')
                if not self.measuring:
                    self.measuring, self.series, self._next_sample_t = True, [], self.time_s
                    self._record()
            else:
                self.measuring = False
            result = {'phase': 'measuring' if self.measuring else 'idle'}
        elif capability == 'calibration.configure':
            self.span_no, self.span_nox, self.cal_window = float(a['span_no_mol_mol']), float(a['span_nox_mol_mol']), float(a['window_s'])
            result = dict(span_no_mol_mol=self.span_no, span_nox_mol_mol=self.span_nox, window_s=self.cal_window)
        elif capability in ('calibration.zero', 'calibration.span'):
            if self.cal_phase != 'idle':
                raise ActionRejected('BUSY')
            if not self._ready():
                raise ActionRejected('PRECONDITION_FAILED')
            self._operation_count += 1
            kind = 'zeroing' if capability == 'calibration.zero' else 'spanning'
            self.cal_phase, self.cal_op = kind, f'{self.instance_id}:{kind}:{self._operation_count}'
            self._cal_t, self._cal_sum = 0.0, [0.0, 0.0]
            result = {'phase': kind, 'operation_id': self.cal_op}
        elif capability == 'calibration.cancel':
            if self.cal_phase != 'idle':
                self.cal_phase, self.cal_last = 'idle', dict(operation_id=self.cal_op, status='cancelled')
            result = {'phase': self.cal_phase}
        elif capability in ('inlet.connect_standard', 'inlet.disconnect', 'inlet.connect'):
            if capability == 'inlet.connect_standard':
                sid = a['standard_id']
                if sid in STANDARD_EXTRA:
                    comp = _clean_composition(STANDARD_EXTRA[sid])
                else:
                    sp, xt = cylinder_truth(self.instance_id, sid)
                    comp = _clean_composition({sp: xt, 'nitrogen': 1.0 - xt})
                p, T, src = P_REF, self.plant['ambient_temperature_k'], 'standard:' + sid
            elif capability == 'inlet.disconnect':
                comp, p, T, src = self._ambient_air(), self.plant['ambient_pressure_pa'], self.plant['ambient_temperature_k'], 'ambient_air'
            else:
                if not a['label'] or len(a['label']) > 64:
                    raise ActionRejected('INVALID_ARGUMENT')
                comp, p, T, src = _clean_composition(a['composition']), float(a['pressure_pa']), float(a['temperature_k']), 'sample'
                self.label = a['label']
            if capability != 'inlet.connect':
                self.label = None
            self.comp, self.p_in, self.T_in, self.source = comp, p, T, src
            self._arrived = self._transport(comp)
            self._enqueue()
            if capability == 'inlet.connect_standard':
                if a['standard_id'] in STANDARD_EXTRA:
                    cert = dict(certificate_mole_fraction=None, expanded_uncertainty_U95=None, value_kind='built_in_zero_gas', certificate_validity=None,
                                certificate_expired=False)
                else:
                    sp, x, u, kind = certificates()[a['standard_id']]
                    validity = certificate_validity(a['standard_id'])
                    cert = dict(certificate_mole_fraction=x, expanded_uncertainty_U95=u, value_kind=kind, certificate_validity=validity,
                                certificate_expired=bool(validity and 'expired' in validity))
                result = dict(standard_id=a['standard_id'], source=src, **cert)
            else:
                result = {'source': src}
        elif capability == 'plant.configure':
            new = dict(self.plant)
            new.update(a)
            if new['line_length_m'] > 0 and new['line_material'] not in ('ptfe', 'stainless'):
                raise ActionRejected('INVALID_ARGUMENT')
            self.plant = new
            if self.source == 'ambient_air':
                self.comp, self.p_in, self.T_in = self._ambient_air(), new['ambient_pressure_pa'], new['ambient_temperature_k']
            self._cache.clear()
            if 'seed' in a:                                                  # re-seed only when a seed is given
                self._rng = random.Random(f'{self.instance_id}:{new["seed"]}')
            self._arrived = self._transport(self.comp)
            self._enqueue()
            result = {'configured': True}
        self.revision += 1
        self._last_event = self.time_s
        return copy.deepcopy(result)

    def _enqueue(self):
        self.queue.append((self.time_s + self._residence(), self._arrived))

    def _record(self):
        no, no2, nox = self._display()
        self.series.append(dict(time_s=self.time_s, no_mol_mol=no, no2_mol_mol=no2, nox_mol_mol=nox))
        if len(self.series) > SERIES_MAX:
            del self.series[0]

    # -------------------------------------------------------------------------------------------------------------- physics
    def step(self, dt_s):
        if isinstance(dt_s, bool) or not isinstance(dt_s, (int, float)) or not math.isfinite(dt_s) or dt_s <= 0:
            raise ValueError('dt_s must be finite and positive')
        left = float(dt_s)
        while left > 1e-12:
            quiet = self.time_s - self._last_event > 30.0                    # all first-order updates are exact for any h; only the
            h = min(left, 0.5 if quiet else 0.05)                            # arrival of the delayed gas needs fine steps after a change
            self._advance(h)
            left -= h
        self.revision += 1
        return self.read('evaluator')

    def _advance(self, h):
        t1 = self.time_s + h
        amb = self.plant['ambient_temperature_k']
        tc, tp = (T_CONV_SET, T_PMT_SET) if self.powered else (amb, amb)
        self.t_conv = tc + (self.t_conv - tc) * math.exp(-h / TAU_CONV_S)
        self.t_pmt = tp + (self.t_pmt - tp) * math.exp(-h / TAU_PMT_S)
        o3t = 1.0 if self.powered else 0.0
        self.o3_frac = o3t + (self.o3_frac - o3t) * math.exp(-h / TAU_O3_S)
        # inlet pressure sensor (compensation input); option r regulates the chamber feed, so no transient error
        self.p_ind = self.p_in + (self.p_ind - self.p_in) * math.exp(-h / TAU_P_SENSOR_S)
        # delayed arrival at the chambers, then the external-line wall memory (NASA TM X-3229 rise / decay times per wetted area) on the
        # NO-derived and NO2-derived parts of each channel (linear filters commute with the plug-flow delay; species without wall data pass)
        while len(self.queue) > 1 and self.queue[1][0] <= t1:
            self.queue.popleft()
        arr = self.queue[0][1] if self.queue and self.queue[0][0] <= t1 else None
        area = math.pi * self.plant['line_inner_diameter_m'] * self.plant['line_length_m']
        raw = dict(no_ch=0.0, no=0.0, no2=0.0, other=0.0) if arr is None else dict(no_ch=arr['no'], **arr['nox_parts'])
        for key, sid in (('no_ch', 'nitric_oxide'), ('no', 'nitric_oxide'), ('no2', 'nitrogen_dioxide')):
            tr, td = adsorption_tau(sid, self.plant['line_material'], area)
            tau = tr if raw[key] > self.wall[key] else td
            self.wall[key] = raw[key] if tau <= 0 else raw[key] + (self.wall[key] - raw[key]) * math.exp(-h / tau)
        truth = [self.wall['no_ch'], self.wall['no'] + self.wall['no2'] + raw['other']]
        if arr is not None:
            p_comp = 1.0 if self.plant['pressure_regulation_installed'] else (self.p_in / max(self.p_ind, 1.0))
            target = [v * arr['quench'] * self.o3_frac * p_comp for v in truth]
        else:
            target = [0.0, 0.0]
        a_mix = 1.0 - math.exp(-h / TAU_MIX_S)
        for c in (0, 1):
            self.ch[c] += (target[c] - self.ch[c]) * a_mix
            self.ch_true[c] += (truth[c] - self.ch_true[c]) * a_mix
        # output filter and correlated zero noise (Ornstein-Uhlenbeck with the filter's correlation time)
        tau_f = self.filter_tau
        a_f = 1.0 if tau_f <= 0 else 1.0 - math.exp(-h / tau_f)
        tau_n = max(tau_f, 0.1)
        sig = NOISE0 * math.sqrt(FILTER_REF_S / tau_n) if (self.plant['noise_enabled'] and self.powered) else 0.0
        rho = math.exp(-h / tau_n)
        for c in (0, 1):
            self.y[c] += (self.ch[c] - self.y[c]) * a_f
            self.noise[c] = self.noise[c] * rho + sig * math.sqrt(max(0.0, 1.0 - rho * rho)) * self._rng.gauss(0.0, 1.0)
        self.time_s = t1
        # calibration averaging
        if self.cal_phase != 'idle' and self.powered:
            for c in (0, 1):
                self._cal_sum[c] += (self.y[c] + self.noise[c]) * h
            self._cal_t += h
            if self._cal_t >= self.cal_window - 1e-9:
                m = [s / self._cal_t for s in self._cal_sum]
                if self.cal_phase == 'zeroing':
                    self.zero_no, self.zero_nox = m
                    self.cal_last = dict(operation_id=self.cal_op, status='succeeded', kind='zero', mean_raw=m)
                else:
                    resp = [m[0] - self.zero_no, m[1] - self.zero_nox]
                    if resp[0] < 0.2 * self.span_no or resp[1] < 0.2 * self.span_nox:
                        self.cal_last = dict(operation_id=self.cal_op, status='failed', kind='span', reason='SPAN_RESPONSE_TOO_LOW')
                    else:
                        self.gain_no, self.gain_nox = self.span_no / resp[0], self.span_nox / resp[1]
                        self.cal_last = dict(operation_id=self.cal_op, status='succeeded', kind='span', mean_raw=m)
                self.cal_phase = 'idle'
        if self.measuring:
            while self._next_sample_t + SERIES_DT <= self.time_s + 1e-9:
                self._next_sample_t += SERIES_DT
                self._record()


class EcoNcld822Session:
    """Identity-filtered protocol facade (registered principals; public calls have no visibility override)."""

    def __init__(self, host, principal_id):
        self._host, self._principal = host, principal_id

    @property
    def _role(self):
        return self._host._principals[self._principal]

    def _target(self, i):
        if i != self._host.instance_id:
            raise ActionRejected('UNKNOWN_INSTANCE')

    def describe(self, *, instance_id=None, asset_type_id=None):
        if (instance_id is None) == (asset_type_id is None):
            raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None:
            self._target(instance_id)
        if asset_type_id is not None and asset_type_id != ASSET_TYPE_ID:
            raise ActionRejected('UNKNOWN_INSTANCE')
        meta = export_eco_ncld822_contract_metadata()
        return dict(protocol_version=PROTOCOL_VERSION, asset_type_id=ASSET_TYPE_ID, asset_version='1.0.0',
                    capabilities={k: v for k, v in meta['capabilities'].items() if v['visibility'] == 'agent' or self._role != 'agent'},
                    state_slots={k: v for k, v in meta['state_slots'].items() if v['visibility'] == 'agent' or self._role != 'agent'})

    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id)
        h = self._host
        vals = h.read(self._role)
        ids = list(vals) if state_ids is None else state_ids
        if not isinstance(ids, list) or any(not isinstance(k, str) for k in ids):
            raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in vals for k in ids):
            raise ActionRejected('ACCESS_DENIED')
        quality = {'derived': 'estimated', 'setpoint': 'valid', 'process_truth': 'valid'}
        return dict(status='succeeded', instance_id=instance_id, state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                    observations={k: dict(value=copy.deepcopy(vals[k]), unit=SLOTS[k][1], quality=quality.get(SLOTS[k][2], 'valid'),
                                          sample_time_s=h.time_s, sample_window_s=None) for k in ids})

    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        try:
            return self._invoke(instance_id=instance_id, capability_id=capability_id, arguments=arguments, request_id=request_id)
        except ActionRejected as exc:
            return dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=None, status='rejected',
                        state_revision=self._host.revision, clock={'domain': 'simulation', 'time_s': self._host.time_s}, result={}, observations={},
                        error=dict(code=str(exc), message=str(exc), retryable=False, side_effects='none'))

    def _invoke(self, *, instance_id, capability_id, arguments, request_id):
        self._target(instance_id)
        h = self._host
        if not isinstance(capability_id, str) or (capability_id not in CAPS and capability_id not in UNSUPPORTED):
            raise ActionRejected('UNKNOWN_CAPABILITY')
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED_OPTION')
        if self._role == 'agent' and CAPS[capability_id][5] != 'agent':
            raise ActionRejected('ACCESS_DENIED')
        if not isinstance(request_id, str) or not request_id:
            raise ActionRejected('INVALID_ARGUMENT')
        validate(CAPS[capability_id][0], arguments)
        key = (self._principal, instance_id, request_id)
        digest = json.dumps([capability_id, arguments], sort_keys=True, allow_nan=False)
        if key in h._requests:
            old, result = h._requests[key]
            if old != digest:
                raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(result)
        result = h.invoke(capability_id, **arguments)
        op = result.get('operation_id') if isinstance(result, dict) else None
        if op:
            h._owners[op] = self._principal
        response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=op,
                        status='accepted' if op else 'succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                        result=result, observations={}, error=None)
        h._requests[key] = (digest, copy.deepcopy(response))
        return response

    def write(self, *, instance_id, state_id, value, request_id):
        self._target(instance_id)
        if state_id not in SLOTS or not SLOTS[state_id][5]:
            raise ActionRejected('ACCESS_DENIED')
        cap, arg = SLOTS[state_id][5]
        return self.invoke(instance_id=instance_id, capability_id=cap, arguments={arg: value}, request_id=request_id)

    def poll(self, operation_id):
        h = self._host
        if h._owners.get(operation_id) != self._principal:
            raise ActionRejected('ACCESS_DENIED')
        if operation_id == h.cal_op and h.cal_phase != 'idle':
            return dict(operation_id=operation_id, status='running', result=dict(phase=h.cal_phase, elapsed_s=h._cal_t))
        last = h.cal_last if (h.cal_last and h.cal_last.get('operation_id') == operation_id) else None
        if last is None:
            raise ActionRejected('ACCESS_DENIED')
        return dict(operation_id=operation_id, status=last['status'], result=copy.deepcopy(last))

    def cancel(self, operation_id, request_id):
        self.poll(operation_id)
        r = self.invoke(instance_id=self._host.instance_id, capability_id='calibration.cancel', arguments={}, request_id=request_id)
        return dict(operation_id=operation_id, status=self.poll(operation_id)['status'], result=r['result'])


# ------------------------------------------------------------------------------------------------------------------- contract metadata
def _p(value, unit, meaning, evidence='assumption', sources=('simulation_assumption',), uncertainty=None):
    return dict(value=value, unit=unit, meaning_zh=meaning, evidence=evidence, source_ids=list(sources), uncertainty=uncertainty)


def export_eco_ncld822_contract_metadata():
    caps, slots = {}, {}
    for key, (schema, unit, origin, visibility, components, write, meaning) in SLOTS.items():
        slots[key] = dict(component_ids=components, value_schema=copy.deepcopy(schema), unit=unit, reference_conditions=REFERENCE_ZH.get(key),
                          meaning_zh=meaning, origin=origin, visibility=visibility, access='read_write' if write else 'read_only',
                          read_binding=dict(backend='host', artifact_id='runtime', accessor=f'EcoNcld822Session.read(state_ids=[{key!r}])'),
                          write_via=dict(capability_id=write[0], argument=write[1], fixed_arguments={}) if write else None,
                          sampling=dict(mode='instant', time_domain='simulation', window_description=None),
                          source_ids=['manufacturer_primary', 'simulation_assumption'] if origin != 'process_truth' else ['simulation_assumption'])
    conditions = {
        'measurement.start': ('预热完成（转换器、冷却PMT到位）', ['status.phase'], 'status.phase == ready'),
        'measurement.toggle': ('开始时要求预热完成；停止无前提', ['status.phase', 'measurement.phase'], 'measuring or status.phase == ready'),
        'calibration.zero': ('预热完成且无校准在进行；入口应接零气（运行层不检查气体身份）', ['status.phase', 'calibration.phase'], 'ready and calibration.phase == idle'),
        'calibration.span': ('预热完成且无校准在进行；窗口平均响应须≥0.2×设定跨度，否则失败且不改增益', ['status.phase', 'calibration.phase'],
                             'ready and calibration.phase == idle'),
        'range.configure': ('四值升序且在5–5000 ppm；校准中锁定', ['calibration.phase'], 'strictly ascending and calibration.phase == idle'),
        'calibration.configure': ('校准中锁定', ['calibration.phase'], 'calibration.phase == idle'),
        'plant.configure': ('校准中锁定', ['calibration.phase'], 'calibration.phase == idle'),
        'inlet.connect': ('组成键须为模板库/基质已知物种，总和≤1（余量补N2）', [], 'known species and sum <= 1'),
    }
    for key, (inp, out, components, meaning, kind, vis, example, effects) in CAPS.items():
        async_ = key in ('calibration.zero', 'calibration.span')
        pre = []
        if key in conditions:
            d, states, check = conditions[key]
            pre = [dict(description_zh=d, state_ids=states, enforced_by='runtime', check=check)]
        src = ['manufacturer_primary', 'simulation_assumption']
        if key in ('nox.measure', 'inlet.connect', 'inlet.connect_standard', 'calibration.span'):
            src += ['template_library']
        caps[key] = dict(function_zh=meaning, kind=kind, visibility=vis, component_ids=components, availability='implemented',
                         input_schema=copy.deepcopy(inp), output_schema=copy.deepcopy(out),
                         implementation=dict(process='implemented', scene='absent', robot='absent', hardware='absent', host='implemented'),
                         binding=dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.eco_ncld822.EcoNcld822Session.invoke',
                                      invocation=f'session.invoke(instance_id="ncld822_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
                         preconditions=pre, state_effects=[dict(state_id=k, timing='on_completion' if async_ else 'immediate', description_zh=meaning)
                                                           for k in effects],
                         observes=[], model_effects=dict(mode='none', description_zh='过程能力；屏幕读数与按键联动由Matterix桥接另行验证。', binding_ids=[]),
                         execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                        completion_criterion='calibration.phase回到idle；poll给出succeeded/failed/cancelled' if async_ else
                                        '设定写入或观测返回；不推进物理时钟',
                                        completion_state_ids=['calibration.phase'] if async_ else effects, timeout_s=None,
                                        cancellation='supported' if async_ else 'unsupported', retry='same_request_id',
                                        resources=['target_instance', 'principal_request_cache'],
                                        partial_failure_zh='拒绝不改状态；取消/断电保留原校准系数；去重只在当前进程内存有效。'),
                         errors=[dict(code=e, meaning_zh=e, side_effects='none') for e in
                                 ['UNKNOWN_INSTANCE', 'UNKNOWN_CAPABILITY', 'ACCESS_DENIED', 'INVALID_ARGUMENT', 'PRECONDITION_FAILED', 'BUSY',
                                  'REQUEST_CONFLICT']],
                         example_arguments=copy.deepcopy(example), verification=[],
                         limitations=['工程过程模型，无真实硬件连接；进程内身份过滤不能替代部署隔离。'], source_ids=src)
    for key, (meaning, components) in UNSUPPORTED.items():
        caps[key] = dict(function_zh=meaning, kind='device_command', visibility='agent', component_ids=components, availability='unsupported',
                         input_schema=obj({}), output_schema=obj({}),
                         implementation=dict(process='absent', scene='absent', robot='absent', hardware='absent', host='absent'),
                         binding=None, preconditions=[], state_effects=[], observes=[],
                         model_effects=dict(mode='none', description_zh='未实现', binding_ids=[]),
                         execution=dict(mode='synchronous', time_domain='simulation', completion_criterion='始终返回UNSUPPORTED_OPTION', completion_state_ids=[],
                                        timeout_s=None, cancellation='unsupported', retry='never_automatic', resources=[], partial_failure_zh='无副作用'),
                         errors=[dict(code='UNSUPPORTED_OPTION', meaning_zh='选件或接口未实现', side_effects='none')], example_arguments={},
                         verification=[], limitations=[meaning], source_ids=['manufacturer_primary'])
    lib = 'template_library'
    mechanisms = {
        'chemiluminescence_detection': dict(
            component_ids=['detector_block', 'ozone_generator', 'converter', 'display_digits'],
            state_ids=['chamber.no_mol_mol', 'chamber.nox_mol_mol', 'display.no_mol_mol', 'display.no2_mol_mol', 'display.nox_mol_mol'],
            meaning_zh='双反应室：NO通道直接、NOx通道经金属转换器；NO+O3化学发光信号按第三体猝灭校正前的比例给出，读数=增益×(滤波信号+噪声−零点)，NO2=NOx−NO',
            implementation='lumped',
            equations=['S_c = x_c,eq * Q_f * O3_frac * p_in / p_ind (option r: p_in / p_ind = 1)', 'Q_f = 1 / (1 + sum_i a_i x_i)',
                       'a_CO2 = 0.60, a_H2O = 2.1, a_O2 = 0 (measured interference factors); a_M = phi (q_M / q_N2 - 1) for Ar, H2, N2O, NO2 (phi host assumption, default 0)',
                       'x_NOx,eq = x_NO + eta_NO2 * x_NO2 + sum_j f_class(j) * nu_j * x_j; f: efficient 1, not_efficient 0, partial host assumption',
                       'x_NOx,eq *= 1 + bias_O2(x_O2) (stainless 800 C table)',
                       'reading_c = gain_c * (y_c + n_c - z_c); y: first-order filter tau_f; n: OU noise sigma = 0.06 ppm * sqrt(1 s / max(tau_f, 0.1 s))'],
            parameters={'noise_zero_1sigma': _p(NOISE0, 'mol/mol', '零点噪声1σ（厂家0.06 ppm，依滤波设定）', 'documented', ['manufacturer_primary']),
                        'min_detectable': _p(0.12 * PPM, 'mol/mol', '最低可检浓度（厂家0.12 ppm=2σ，依滤波设定）', 'documented', ['manufacturer_primary']),
                        'noise_reference_filter_s': _p(FILTER_REF_S, 's', '厂家噪声值对应的滤波时间常数（厂家未给，假设1 s）'),
                        'full_scale_limits': _p([FS_MIN, FS_MAX], 'mol/mol', '四个满量程可选5–5000 ppm', 'documented', ['manufacturer_primary']),
                        'display_resolution': _p(DIGITS, 'mol/mol', 'GUI一位小数ppm（手册截图）', 'documented', ['manufacturer_primary']),
                        'quench_co2': _p(0.60, '1', 'CO2干扰因子 −0.60 %/%（NASA TM X-3229）', 'documented', [lib]),
                        'quench_h2o': _p(2.1, '1', 'H2O干扰因子 −2.1 %/%（NASA TM X-3229）', 'documented', [lib]),
                        'converter_no2_efficiency_default': _p(1.0, '1', 'NO2转换效率缺省1.0（Dunlea 2007 “efficiently reduce”类别映射；宿主可改）'),
                        'partial_conversion_default': _p(0.5, '1', '“部分还原”类（HONO、NH3、PAN、HO2NO2、颗粒硝酸盐）缺省0.5为显式假设，无文献数值；宿主可改'),
                        'quench_relative_scale_default': _p(0.0, '1', '相对猝灭效率的换算系数φ（样气占反应室第三体的份额）缺省0：同一来源实测O2干扰≈0与φ=1矛盾；宿主可设'),
                        'converter_temperature_set': _p(T_CONV_SET, 'K', '金属转换器设定温度（厂家未给，假设375 °C）'),
                        'pmt_temperature_set': _p(T_PMT_SET, 'K', '冷却PMT设定温度（厂家未给，假设−5 °C）')},
            source_ids=['manufacturer_primary', lib, 'simulation_assumption'],
            limitations=['Ar/H2/N2O/NO2的猝灭只在宿主给定φ>0时生效（缺省只用CO2、H2O、O2实测干扰因子）；转换器材料与温度未公开，借用钼转换器分类与不锈钢转换器氧效应表；无PMT增益漂移与故障概率。']),
        'sample_transport': dict(
            component_ids=['sample_inlet', 'inlet_filter', 'flow_restrictors', 'vacuum_pump'],
            state_ids=['inlet.composition', 'inlet.pressure_pa', 'flow.indicated_sample_m3_s', 'line.condensed_water_fraction', 'chamber.no_mol_mol'],
            meaning_zh='入口→（外部样气管）→过滤与限流→两反应室：临界限流流量∝入口绝压；内部滞后2.0 s、混合τ0.35 s；样气管壁吸附记忆（NO、NO2，作为延迟之后的一阶滤波，按通道中NO与NO2来源部分分别取上升/衰减时间）；未加热路径冷凝的亨利定律洗除；停留时间内的暗反应（IUPAC 53–64，N原子守恒，温度夹在各反应评估范围内）；标准气瓶真值=证书值±U95/2的确定性偏离',
            implementation='lumped',
            equations=['Q = 1.0 L/min * p_in / 101325 Pa', 't_res = 2.0 s * Q_nom / Q + V_line * (p_in / p_ref) / Q',
                       'tau_wall = (t/A)_record * A_wetted / ln(1000) (rise for increasing, decay for decreasing), applied after the delay to the NO- and NO2-derived channel parts',
                       'x_cond = (x_w - p_sat(T_path)/p) / (1 - p_sat/p); f_i = 1 / (1 + Hscp_i(T) R T V_l / V_g)',
                       'dn/dt = S r(n), r from IUPAC rows 53-64 (Troe falloff with [M] = p / kT), backward Euler + Newton'],
            parameters={'sample_flow_nominal': _p(Q_NOM, 'm^3/s', '样气流量1.0 l/min（厂家；参考状态假设101325 Pa/298.15 K）', 'documented', ['manufacturer_primary']),
                        'inlet_pressure_range': _p([P_MIN, P_MAX], 'Pa', '进口绝压600–1200 mbar（厂家）', 'documented', ['manufacturer_primary']),
                        'lag_time_bound': _p(3.0, 's', '厂家滞后<3 s（上界）', 'documented', ['manufacturer_primary']),
                        'rise_time_bound': _p(1.0, 's', '厂家0–90%上升<1 s（上界）', 'documented', ['manufacturer_primary']),
                        'internal_lag': _p(LAG_INT_S, 's', '内部传输滞后（假设，满足<3 s）'),
                        'mixing_tau': _p(TAU_MIX_S, 's', '混合时间常数（假设；无滤波时0–90% 0.81 s）'),
                        'hot_tubing_temperature': _p(T_HOT_S, 'K', 'h选件加热管路温度（厂家未给，假设180 °C）')},
            source_ids=['manufacturer_primary', lib, 'simulation_assumption'],
            limitations=['单级平衡冷凝洗除（真实管路有累积冷凝液与传质限制）；管壁记忆只对NO、NO2有数据；外部样气管由宿主配置。']),
        'warmup': dict(
            component_ids=['converter', 'detector_block', 'ozone_generator'], state_ids=['converter.temperature_k', 'pmt.temperature_k', 'ozone.generator_fraction',
                                                                                          'status.phase'],
            meaning_zh='上电后转换器、冷却PMT、臭氧发生器一阶趋近设定（时间常数为假设）；转换器±10 K、PMT±1 K内为ready',
            implementation='lumped', equations=['T_next = T_set + (T - T_set) exp(-dt/tau)', 'O3_next = 1 + (O3 - 1) exp(-dt/20 s)'],
            parameters={'converter_tau': _p(TAU_CONV_S, 's', '转换器预热时间常数（假设）'), 'pmt_tau': _p(TAU_PMT_S, 's', 'PMT冷却时间常数（假设）'),
                        'ozone_tau': _p(TAU_O3_S, 's', '臭氧发生器响应（假设）')},
            source_ids=['simulation_assumption'], limitations=['厂家未给预热时间；温度不对外显示。']),
    }
    _ann.annotate_export('eco_ncld822', caps, slots, mechanisms, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE), 'inlet.connect': (INLET, INLET_EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms=mechanisms)

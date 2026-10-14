"""CHEM-169 MicroCal PEAQ-ITC isothermal titration calorimeter (standard model with the washing module; not the PEAQ-ITC Automated): process
runtime.

Device (brochure PN12309, user manual MAN0573, analysis manual MAN0576, getting started MAN0578, technical notes TN240816 / TN161202):
coin-shaped Hastelloy sample and reference cells (200 uL active volume, >= 280 uL loaded with the loading syringe), power-compensation
differential power (DP) with a reference power on the reference-cell offset heater (3-10 ucal/s recommended), High / Low / None feedback
(response time 8 s with High: time to 63 % of the peak height; nominal noise 0.15 ncal/s), 2-80 C, stability +-0.00012 C, 40 uL titration
syringe (pipette) whose paddle stirs the cell (750 rpm recommended), injections of volume / duration / spacing after an initial delay (60 s),
manual loading (Fill Port Adapter, pipette locations Cell / Rest / Load / Clean, PCR tube with ~60 uL titrant, Plunger Down, Load,
Purge/Refill), the washing module (detergent, methanol, water, waste and overflow bottles; Cell Cleaning Tool; a full waste bottle fills the
overflow bottle and shuts the vacuum pump down), and the analysis software (integration, fitted offset or control subtraction, One Set of
Sites, Two Sets of Sites, Sequential Binding Sites, Competitive, Dissociation, Enzyme Kinetics multiple / single injection, Bin comments).

Process model (fixed 0.2 s grid): Peltier jacket; sample and reference cell contents relaxing to the jacket; DP = reference power - first-order
response (tau 8 / 20 / 60 s) to the heat released in the sample cell (+ stirring friction, reference fill, heat-capacity mismatch, drift, noise,
clipping at 0); plunger-driven injections with precision noise; needle-tip diffusion before the first injection; mixing of the injected slug
(tau 2.5 s at 750 rpm); continuous displacement of the overfilled cell; mass-action chemistry in the active volume: binding networks (one /
two sets of sites, competing ligands, sequential sites, optional association kinetics, proton release), dimer dissociation, Michaelis-Menten
kinetics, buffer protonation (Goldberg 2002 pK / dH / dCp with the extended Debye-Hueckel equation, B = 1.6), virial dilution heats (IUPAC
propan-1-ol and sucrose) and methanol residue; the heat is the enthalpy change of reaction and mixing at the cell temperature."""
import copy
import json
import math
import zlib
from pathlib import Path

import numpy as np

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.malvern_microcalpeaq"
CATALOG_ID = "CHEM-169"
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch04/t4_measure/malvern_microcalpeaq'
LIBS = {'buffers': 'buffer_library.json', 'binding': 'binding_library.json', 'dilution': 'dilution_library.json', 'instrument': 'instrument_model.json'}


class ActionRejected(ValueError):
    """A capability call refused with a declared error code (no state change)."""


# ---------------------------------------------------------------- libraries
_LIBC = {}


def _lib(name):
    if name not in _LIBC:
        _LIBC[name] = json.loads((ASSET_DIR / LIBS[name]).read_text())
    return _LIBC[name]


def buffers():
    return _lib('buffers')['substances']


def bindings():
    return _lib('binding')['substances']


def dilutions():
    return _lib('dilution')['substances']


def instrument():
    return _lib('instrument')


DOC = instrument()['documented']
ENG = instrument()['engineering']
DT = 0.2                                    # physics grid (s)
REC_DT = 1.0                                # recorded DP data period (s; assumption)
R_GAS = 8.314462618
CAL = 4.184
UCAL = 4.184e-6                             # J
V0 = DOC['cell_volume_m3']                  # 200 uL active cell volume (documented)
V_LOAD_MIN = DOC['sample_volume_m3']        # 280 uL minimum loading volume (documented)
V_SYR = DOC['syringe_volume_m3']            # 40 uL titration syringe (documented)
V_TUBE_MAX = 2.0e-7                         # PCR tube capacity (assumption: 0.2 mL tube)
T_RANGE = tuple(DOC['temperature_range_k'])  # 2-80 C (documented)
AMBIENT = tuple(DOC['ambient_temperature_k'])  # 10-28 C (documented)
MAINS = tuple(DOC['voltage_v'])             # 100-240 V (documented)
PLUNGER_STROKE = ENG['plunger_stroke_m']
LIFT_TRAVEL = ENG['pipette_lift_m']
LOCATION_Q = {'cell': 0.0, 'rest': 1.0569023710683039, 'load': 1.7550340718660358, 'clean': 2.2786328474643347}   # interface definition (rad)
SWING_MAX = 2.2986
LOCATIONS = list(LOCATION_Q)
FEEDBACK = ['high', 'low', 'none']
BOTTLES = ['detergent', 'methanol', 'water', 'waste', 'overflow']
CELL_METHODS = ['wash', 'rinse', 'soak', 'none']
SYRINGE_METHODS = ['rinse', 'wash', 'none']
MODELS = ['one_set_of_sites', 'two_sets_of_sites', 'sequential_binding_sites', 'competitive', 'dissociation', 'enzyme_kinetics_multiple',
          'enzyme_kinetics_single']
FAULTS = ['power_loss', 'wash_pump_failure', 'bottle_filter_clogged', 'syringe_damaged', 'stir_motor_failure', 'cell_bubble', 'reference_cell_empty']
MAX_SOLUTIONS = 60
MAX_EXPERIMENTS = 40
MAX_EVENTS_LOG = 1000
MAX_INJECTIONS = 100
MAX_SPECIES_PER_SOLUTION = 8
DEBYE_B = 1.6                               # kg^1/2 mol^-1/2 (Goldberg 2002 eq. 13)
WATER_ID = 'water'
METHANOL_DH = ENG['methanol_mixing_dh_j_mol']


def _am(T):
    """Debye-Hueckel constant A_m (natural-log basis, kg^1/2 mol^-1/2): Clarke & Glew 1980 fit (1.1758 at 298.15 K)."""
    return 1.10708 - 1.54508e-3 * T + 5.95584e-6 * T * T


def _dam_dt(T):
    return -1.54508e-3 + 2 * 5.95584e-6 * T


# ---------------------------------------------------------------- species registry (shelf reagents)
def _species_table():
    """Shelf species: buffers of the Goldberg 2002 library, the binding partners of the binding library, dilution solutes, salts and methanol."""
    sp = {}
    for k, v in buffers().items():
        if k == WATER_ID:
            continue
        sp[k] = dict(kind='buffer', name=v['name'], ionic=0.0, macromolecule=False, source='nist_goldberg_2002')
    ion = {'ca2+': 3.0, 'ba2+': 3.0, 'edta': 3.0}                  # CaCl2 / BaCl2 (I = 3c); Na2H2EDTA at pH ~5.6 (H2Y2- + 2 Na+, I = 3c): assumption
    macro = {'rnase_a', 'carbonic_anhydrase_ii', 'invertase'}
    for b in bindings().values():
        for role in ('receptor', 'ligand'):
            k = b[role]
            sp.setdefault(k, dict(kind='solute', name=k, ionic=ion.get(k, 0.0), macromolecule=k in macro, source='binding_library'))
    for k, v in dilutions().items():
        name = {'cacl2': 'ca2+', 'bacl2': 'ba2+'}.get(k, k)
        sp.setdefault(name, dict(kind='solute', name=v['name'], ionic=ion.get(name, 0.0), macromolecule=False, source='dilution_library'))
    sp['nacl'] = dict(kind='salt', name='sodium chloride', ionic=1.0, macromolecule=False, source='simulation_assumption')
    sp['kcl'] = dict(kind='salt', name='potassium chloride', ionic=1.0, macromolecule=False, source='simulation_assumption')
    sp['methanol'] = dict(kind='solute', name='methanol (cleaning residue)', ionic=0.0, macromolecule=False, source='simulation_assumption')
    return sp


SHELF = _species_table()


def _dilution_params(species, T):
    """Excess-enthalpy coefficients (hxx J kg mol^-2, hxxx J kg^2 mol^-3) of a solute: H_E per kg water = hxx m^2 + hxxx m^3."""
    d = dilutions()
    if species == 'propan_1_ol':
        v = d['propan_1_ol']
        return v['hxx_j_kg_mol2'], v['hxxx_j_kg2_mol3']
    if species == 'sucrose':
        v = d['sucrose']
        A = v['A']['c0'] + v['A']['c1'] * T
        B = v['B']['c0'] + v['B']['c1'] * T + v['B']['c2'] * T * T
        return A, B
    return None


# ---------------------------------------------------------------- acid-base speciation (buffers + water, extended Debye-Hueckel)
_BUFC = {}


def _buffer_steps(bid):
    """Ionisation steps of a buffer ordered from the most acidic: list of (pK298, dH, dCp); base charge."""
    if bid not in _BUFC:
        v = buffers()[bid]
        st = [(s['pK'], s['dH_J_mol'] if s['dH_J_mol'] is not None else 0.0, s['dCp_J_K_mol'] if s['dCp_J_K_mol'] is not None else 0.0)
              for s in v['steps'] if s['pK'] is not None]
        _BUFC[bid] = (st, v['base_charge'] if v['base_charge'] is not None else 0)
    return _BUFC[bid]


def _vh(pk298, dh, dcp, T):
    """pK and dH at T (Goldberg 2002 eq. 12: van 't Hoff with constant dCp)."""
    th = 298.15
    lnk = -pk298 * math.log(10) - dh / R_GAS * (1 / T - 1 / th) + dcp / R_GAS * (th / T - 1 + math.log(T / th))
    return -lnk / math.log(10), dh + dcp * (T - th)


def _water(T):
    w = buffers()[WATER_ID]['steps'][0]
    return _vh(w['pK'], w['dH_J_mol'], w['dCp_J_K_mol'], T)


def _dh_f(I):
    s = math.sqrt(max(I, 0.0))
    return s / (1 + DEBYE_B * s)


def _excess_enthalpy(I, T):
    """Excess enthalpy (J per kg water ~ J/L) of the extended Debye-Hueckel model: G_E / RT = -A_m F(I) with dF/dI = 2 sqrt(I) / (1 + B sqrt(I)),
    F = (4 / B^3) (ln(1 + B s) - B s + B^2 s^2 / 2), s = sqrt(I); H_E = R T^2 (dA_m/dT) F (gives Goldberg 2002 eq. 14 for each species)."""
    s = math.sqrt(max(I, 0.0))
    F = 4.0 / DEBYE_B ** 3 * (math.log1p(DEBYE_B * s) - DEBYE_B * s + DEBYE_B ** 2 * s * s / 2)
    return R_GAS * T * T * _dam_dt(T) * F


_SPC = {}


def _consts(bufs, T, fI):
    out = {}
    for b in bufs:
        st, z0 = _buffer_steps(b)
        n = len(st)
        ks, cum, acc = [], [0.0], 0.0
        for j, (pk, dh, dcp) in enumerate(st):
            pkT, _ = _vh(pk, dh, dcp, T)
            z = z0 + (n - j - 1)                         # charge of the base produced by step j (H_(n-j)L -> H+ + H_(n-j-1)L)
            ks.append(math.exp(-pkT * math.log(10) - 2 * z * fI))   # ln Kc = ln K0 + A f (z_H^2 + z^2 - (z+1)^2)
        for m in range(1, n + 1):
            pk, dh, dcp = st[n - m]
            acc -= _vh(pk, dh, dcp, T)[1]                # protonation reverses the step: enthalpy of H_mL relative to L + m H+ (I = 0)
            cum.append(acc)
        out[b] = (ks, cum, z0)
    return out


def _speciate(buf, th_m, salt_i, T, logh0=None):
    """Solve the proton condition th_m = [H+] - [OH-] + sum_b C_b nbar_b for log10 [H+] (mol/L) with Debye-Hueckel conditional constants.
    buf: {buffer_id: total mol/L}; th_m: proton excess (mol/L) over the fully deprotonated buffers and water; salt_i: ionic strength of the other
    solutes (mol/L).  Returns dict(logh, ph (activity scale), I, species {(b, m): mol/L}, oh, enthalpy J/L incl. the excess enthalpy)."""
    I = salt_i + 0.5 * sum(buf.values())
    logh = -7.0 if logh0 is None else logh0
    pkw, dhw = _water(T)
    for _it in range(4):
        fI = _am(T) * _dh_f(I)
        kw = math.exp(-pkw * math.log(10) + 2 * fI)       # ln Kw,c = ln Kw0 - ln g_H - ln g_OH
        consts = _consts(buf, T, fI)

        def polys(h):
            out = {}
            for b, (ks, cum, z0) in consts.items():
                n = len(ks)
                terms, acc = [1.0], 1.0
                for m in range(1, n + 1):
                    acc *= h / ks[n - m]
                    terms.append(acc)
                out[b] = terms
            return out

        def f(lh):
            h = 10.0 ** lh
            tot = h - kw / h
            for b, terms in polys(h).items():
                tot += buf[b] * sum(m * t for m, t in enumerate(terms)) / sum(terms)
            return tot - th_m
        lo, hi = -15.5, 1.5
        if f(lo) > 0:
            logh = lo
        elif f(hi) < 0:
            logh = hi
        else:
            x = min(max(logh, lo + 1e-6), hi - 1e-6)
            for _ in range(100):
                fx = f(x)
                if fx > 0:
                    hi = x
                else:
                    lo = x
                if hi - lo < 1e-12:
                    break
                der = (f(x + 1e-7) - fx) / 1e-7
                xn = x - fx / der if der > 0 else 0.5 * (lo + hi)
                x = xn if lo < xn < hi else 0.5 * (lo + hi)
            logh = x
        h = 10.0 ** logh
        oh = kw / h
        spec, I_new, counter = {}, salt_i + 0.5 * (h + oh), h - oh
        for b, terms in polys(h).items():
            P = sum(terms)
            z0 = consts[b][2]
            for m, t in enumerate(terms):
                cm = buf[b] * t / P
                spec[(b, m)] = cm
                I_new += 0.5 * cm * (z0 + m) ** 2
                counter += cm * (z0 + m)
        I_new += 0.5 * abs(counter)                       # monovalent counter-ions (Na+ or Cl-) keep the solution neutral
        done = abs(I_new - I) < 1e-9 * max(1.0, I)
        I = I_new
        if done:
            break
    E = oh * dhw + _excess_enthalpy(I, T)                  # OH- relative to water and free H+; excess enthalpy of all ions
    for b, (ks, cum, z0) in consts.items():
        for m in range(1, len(ks) + 1):
            E += spec[(b, m)] * cum[m]
    return dict(logh=logh, ph=-logh + _am(T) * _dh_f(I) / math.log(10), I=I, species=spec, oh=oh, enthalpy=E)


def proton_excess_for_ph(buf, ph, salt_i, T):
    """Proton excess (mol/L) of a buffer solution titrated to the given pH (activity scale) with NaOH / HCl."""
    lo, hi = -1.0, 1.0 + sum(len(_buffer_steps(b)[0]) * c for b, c in buf.items())
    for _ in range(90):
        mid = 0.5 * (lo + hi)
        if _speciate(buf, mid, salt_i, T)['ph'] > ph:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-14:
            break
    return 0.5 * (lo + hi)


# ---------------------------------------------------------------- binding networks
def _k_at(kd_m, dh, dcp, T0, T):
    """Association constant (1/M) and enthalpy at T: van 't Hoff with constant dCp."""
    ka = 1.0 / kd_m
    dcp = dcp or 0.0
    lnk = math.log(ka) - dh / R_GAS * (1 / T - 1 / T0) + dcp / R_GAS * (T0 / T - 1 + math.log(T / T0))
    return math.exp(lnk), dh + dcp * (T - T0)


def _solve_free(lig_tot, classes):
    """Free ligand concentrations for site classes competing for ligands.
    lig_tot: {ligand: total M}; classes: list of dict(sites=M of sites, K={ligand: 1/M}).  Damped Newton in log space."""
    ligs = [l for l in lig_tot if lig_tot[l] > 0]
    if not ligs:
        return {l: 0.0 for l in lig_tot}
    x = np.array([math.log(lig_tot[l] * 0.5) for l in ligs])

    def resid(xv):
        L = np.exp(xv)
        r = L - np.array([lig_tot[l] for l in ligs])
        for c in classes:
            den = 1.0 + sum(c['K'].get(l, 0.0) * L[i] for i, l in enumerate(ligs))
            for i, l in enumerate(ligs):
                r[i] += c['sites'] * c['K'].get(l, 0.0) * L[i] / den
        return r / np.array([lig_tot[l] for l in ligs])
    if len(ligs) == 1:
        lt = lig_tot[ligs[0]]
        lo, hi = math.log(lt) - 60.0, math.log(lt)
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            if resid(np.array([mid]))[0] > 0:
                hi = mid
            else:
                lo = mid
            if hi - lo < 1e-12:
                break
        return {ligs[0]: math.exp(0.5 * (lo + hi))}
    for _ in range(100):
        r = resid(x)
        if np.max(np.abs(r)) < 1e-12:
            break
        J = np.zeros((len(ligs), len(ligs)))
        for j in range(len(ligs)):
            d = np.zeros(len(ligs))
            d[j] = 1e-7
            J[:, j] = (resid(x + d) - r) / 1e-7
        try:
            step = np.linalg.solve(J, -r)
        except np.linalg.LinAlgError:
            step = -r
        step = np.clip(step, -2.0, 2.0)
        x = np.minimum(x + step, np.log([lig_tot[l] for l in ligs]))
    return {l: float(math.exp(x[i])) for i, l in enumerate(ligs)}


def _competing_free(lig_tot, c):
    """Several ligands competing for one class of sites: bisection on the free-site concentration S (S_t = S (1 + sum K L_t / (1 + K S)))."""
    St = c['sites']
    lo, hi = math.log(St) - 80.0, math.log(St)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        S = math.exp(mid)
        tot = S * (1 + sum(c['K'][l] * lt / (1 + c['K'][l] * S) for l, lt in lig_tot.items()))
        if tot > St:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-13:
            break
    S = math.exp(0.5 * (lo + hi))
    return {l: lt / (1 + c['K'][l] * S) for l, lt in lig_tot.items()}


def _sequential_bound(lt, mt, ks):
    """Sequential (stepwise) binding: free ligand from the binding polynomial; returns (free, [fraction of macromolecule with i ligands])."""
    def frac(L):
        terms = [1.0]
        acc = 1.0
        for k in ks:
            acc *= k * L
            terms.append(acc)
        P = sum(terms)
        return [t / P for t in terms]

    def bound(L):
        return mt * sum(i * f for i, f in enumerate(frac(L)))
    if lt <= 0 or mt <= 0:
        return 0.0, [1.0] + [0.0] * len(ks)
    lo, hi = math.log(lt) - 60.0, math.log(lt)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        L = math.exp(mid)
        if L + bound(L) > lt:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-12:
            break
    L = math.exp(0.5 * (lo + hi))
    return L, frac(L)


# ---------------------------------------------------------------- MicroCal analysis model (Wiseman; MAN0576)
def _one_set_q(Mt, Xt, n, ka, dh, v0):
    """Heat content Q (J) of the active volume for one set of sites (MicroCal / Wiseman closed form)."""
    if Mt <= 0 or n <= 0:
        return 0.0
    ka = min(max(ka, 1e-6), 1e16)
    a = 1 + Xt / (n * Mt) + 1 / (n * ka * Mt)
    return n * Mt * dh * v0 * 1000 / 2 * (a - math.sqrt(max(a * a - 4 * Xt / (n * Mt), 0.0)))


def _cum_conc(M0, X0, vols, v0):
    """Total concentrations after each injection with the MicroCal displaced-volume correction."""
    out = []
    dv = 0.0
    for v in vols:
        dv += v
        r = dv / v0
        out.append((M0 * (1 - r / 2) / (1 + r / 2), X0 * r * (1 - r / 2)))
    return out


def _delta_q(Q, vols, v0):
    dq = []
    prev = 0.0
    for i, q in enumerate(Q):
        dq.append(q + vols[i] / v0 * (q + prev) / 2 - prev)
        prev = q
    return dq


# ---------------------------------------------------------------- counter-based noise (call-order independent)
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


# ---------------------------------------------------------------- schema helpers (as CHEM-168)
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
        if 'pattern' in s:
            import re
            if not re.fullmatch(s['pattern'], v):
                raise ActionRejected('INVALID_ARGUMENT')
    elif k == 'null' and v is not None:
        raise ActionRejected('INVALID_ARGUMENT')
    if 'enum' in s and v not in s['enum']:
        raise ActionRejected('INVALID_ARGUMENT')


# ---------------------------------------------------------------- capabilities
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=c, function_zh=meaning, visibility=visibility, kind=kind)


SID = dict(type='string', minLength=1, maxLength=40, pattern='^[A-Za-z0-9_.:+-]+$')
SPID = dict(type='string', minLength=1, maxLength=60, pattern='^[A-Za-z0-9_.:+-]+$')
CONC = number('mol/m3', 0.0, 5000.0)
W_UCAL = lambda x: x * UCAL                                                         # noqa: E731
BUFFER_IDS = sorted(k for k in buffers() if k != WATER_ID)
RECIPE = obj(dict(buffer_id=enum(BUFFER_IDS), concentration_mol_m3=number('mol/m3', 0.0, 1000.0), ph=number('1', 1.0, 13.0),
                  nacl_mol_m3=number('mol/m3', 0.0, 2000.0)), optional=('nacl_mol_m3',))
SOLUTE = obj(dict(species_id=SPID, concentration_mol_m3=CONC))
SOURCE = obj(dict(source_id=SID, volume_m3=number('m3', 1.0e-10, 5.0e-5)))
INJ = obj(dict(volume_m3=number('m3', 1.0e-11, V_SYR), duration_s=number('s', 0.2, 120.0), spacing_s=number('s', 1.0, 7200.0)))
OPERATION = obj(dict(operation_id=TEXT))
EVENT = obj(dict(time_s=number('s'), kind=enum(['power', 'wash', 'method', 'temperature', 'solution', 'cell', 'pipette', 'syringe', 'run', 'injection', 'clean',
                                               'data', 'warning', 'error', 'fault', 'truth']), code=TEXT, message_zh=TEXT))
PLANT_EXAMPLE = dict(room_k=295.15, mains_voltage_v=230.0, noise_seed=1)
PLANT = obj(dict(room_k=number('K', 278.15, 313.15), mains_voltage_v=number('V', 0.0, 264.0), noise_seed=integer(0, 2 ** 31 - 1),
                 room_drift_k_per_s=number('K/s', -1e-3, 1e-3), reference_age_s=number('s', 0.0, 120 * 86400.0)),
            optional=('room_drift_k_per_s', 'reference_age_s'))
METHOD_EXAMPLE = dict(temperature_k=298.15, reference_power_w=W_UCAL(5.0), feedback='high', stir_rate_hz=12.5, initial_delay_s=60.0,
                      injections=[dict(volume_m3=4.0e-10, duration_s=0.8, spacing_s=150.0)] + [dict(volume_m3=2.0e-9, duration_s=4.0, spacing_s=150.0)] * 18,
                      cell_concentration_mol_m3=0.1, syringe_concentration_mol_m3=1.0)
METHOD = obj(dict(temperature_k=number('K', *T_RANGE), reference_power_w=number('W', 0.0, W_UCAL(20.0)), feedback=enum(FEEDBACK),
                  stir_rate_hz=number('1/s', 0.0, 20.0), initial_delay_s=number('s', 0.0, 3600.0), injections=array(INJ, 1, MAX_INJECTIONS),
                  cell_concentration_mol_m3=number('mol/m3', 0.0, 1.0e4), syringe_concentration_mol_m3=number('mol/m3', 0.0, 1.0e5), comment=dict(type='string', maxLength=200)),
             optional=('comment',))
COMPONENT = obj(dict(species_id=TEXT, nominal_concentration_mol_m3=number('mol/m3')))
FITPAR = obj(dict(name=TEXT, value=number('1'), error=nullable(number('1')), unit=TEXT, fixed=BOOL))
INJ_ROW = obj(dict(index=INT, volume_m3=number('m3'), heat_j=number('J'), ndh_j_mol=nullable(number('J/mol')), molar_ratio=nullable(number('1')),
                   fit_heat_j=nullable(number('J')), excluded=BOOL, rate_mol_m3_s=nullable(number('mol/(m3*s)')), substrate_mol_m3=nullable(number('mol/m3'))))
ANALYZE = obj(dict(experiment_id=SID, model=enum(MODELS), exclude_injections=array(integer(1, MAX_INJECTIONS), 0, MAX_INJECTIONS),
                   control=enum(['none', 'fitted_offset', 'experiment']), control_experiment_id=SID, ligand_in_cell=BOOL,
                   cell_concentration_mol_m3=number('mol/m3', 1e-9, 1.0e4), syringe_concentration_mol_m3=number('mol/m3', 1e-9, 1.0e5),
                   competitor=obj(dict(kd_mol_m3=number('mol/m3', 1e-12, 1e5), dh_j_mol=number('J/mol', -1e6, 1e6), concentration_mol_m3=number('mol/m3', 0.0, 1e5))),
                   sites=integer(2, 3), enzyme_dh_j_mol=number('J/mol', -1e6, 1e6),
                   fix=obj(dict(n=number('1', 1e-3, 100.0), kd=number('mol/m3', 1e-11, 1e6), dh=number('J/mol', -1e6, 1e6), n2=number('1', 1e-3, 100.0),
                                kd2=number('mol/m3', 1e-11, 1e6), dh2=number('J/mol', -1e6, 1e6), offset=number('J/mol', -1e6, 1e6)),
                           optional=('n', 'kd', 'dh', 'n2', 'kd2', 'dh2', 'offset'))),
              optional=('exclude_injections', 'control', 'control_experiment_id', 'ligand_in_cell', 'cell_concentration_mol_m3', 'syringe_concentration_mol_m3',
                        'competitor', 'sites', 'enzyme_dh_j_mol', 'fix'))
ANALYZE_OUT = obj(dict(experiment_id=TEXT, model=TEXT, parameters=array(FITPAR, 0, 12), injections=array(INJ_ROW, 0, MAX_INJECTIONS),
                       derived=obj(dict(ka_m3_mol=nullable(number('m3/mol')), dg_j_mol=nullable(number('J/mol')), minus_tds_j_mol=nullable(number('J/mol')),
                                        ds_j_k_mol=nullable(number('J/(K*mol)')), c_value=nullable(number('1')), chi2_reduced=nullable(number('1')),
                                        temperature_k=number('K'), total_heat_j=number('J'))),
                       bin=enum(['OK', 'Binding', 'No binding', 'Control', 'Check data']), bin_comments=array(TEXT, 0, 16), warnings=array(TEXT, 0, 16)))
EXP_ROW = obj(dict(experiment_id=TEXT, state=TEXT, injections_done=INT, injections_total=INT, started_s=number('s'), finished_s=nullable(number('s')),
                   cell_solution=nullable(TEXT), syringe_solution=nullable(TEXT)))
LEDGER_KEYS = ['electrical_energy_j', 'sample_loaded_m3', 'sample_rinse_m3', 'titrant_loaded_m3', 'titrant_injected_m3', 'titrant_to_waste_m3', 'syringe_spilled_m3',
               'detergent_used_m3', 'methanol_used_m3', 'water_used_m3', 'waste_added_m3', 'overflow_added_m3', 'reference_water_m3', 'reference_evaporated_m3',
               'chemical_heat_j', 'injections', 'runs_completed', 'cleanings_completed']
LEDGER = obj({k: number('J' if k.endswith('_j') else 'm3' if k.endswith('_m3') else '1') for k in LEDGER_KEYS})
SPECMAP = dict(type='object', additionalProperties=number('mol/m3'))

cap('plant.configure', PLANT, obj(dict(plant=obj(dict(room_k=number('K'), mains_voltage_v=number('V'), noise_seed=INT, room_drift_k_per_s=number('K/s'),
                                                     reference_age_s=number('s'))))),
    PLANT_EXAMPLE, ['cell_body', 'wash_body'],
    '宿主声明实验室条件：室温（厂家环境10–28 °C）、供电电压（100–240 V）、室温漂移、参比池上次换水后的时间与噪声种子（只在首次声明时生效）；参比池装满水、洗涤模块各瓶装满、废液瓶空',
    'evaluator', 'simulation')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), ['power_switch', 'cell_rear_panel', 'status_light'],
    '量热池单元开机/关机（后部电源开关；外部24 VDC电源接100–240 V）')
cap('power.press_switch', obj({}), obj(dict(enabled=BOOL)), {}, ['power_switch'], '按一下量热池单元后部的电源开关（切换开/关）')
cap('wash.power_set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), ['wash_power_switch', 'wash_rear_panel', 'wash_status_light'],
    '洗涤模块开机/关机（真空泵与阀；装样与清洗需要）')
cap('wash.press_switch', obj({}), obj(dict(enabled=BOOL)), {}, ['wash_power_switch'], '按一下洗涤模块后部的电源开关（切换开/关）')
cap('method.set', METHOD, obj(dict(injections=INT, total_volume_m3=number('m3'), duration_s=number('s'))), METHOD_EXAMPLE, ['cell_body'],
    '控制软件里的方法（Edit settings）：实验温度（2–80 °C）、参比功率、反馈模式（High/Low/None）、搅拌转速（1/s，750 rpm = 12.5 s⁻¹）、初始延迟、注射序列（每针体积、时长、间隔），'
    '以及实验信息里的池浓度与注射器浓度（分析用的标称值）；运行中不能改')
cap('temperature.set', obj(dict(temperature_k=number('K', *T_RANGE))), obj(dict(setpoint_k=number('K'))), dict(temperature_k=298.15), ['cell_body'],
    '仪器控制栏的Temperature/Set：改变样品池目标温度（运行中不能改）')
cap('solution.prepare', obj(dict(solution_id=SID, volume_m3=number('m3', 1.0e-8, 5.0e-5), buffer=RECIPE, buffer_from=SID, solutes=array(SOLUTE, 0, 6),
                                 sources=array(SOURCE, 0, 4)), optional=('buffer', 'buffer_from', 'solutes', 'sources')),
    obj(dict(solution_id=TEXT, volume_m3=number('m3'), ph_nominal=nullable(number('1')), components=array(COMPONENT, 0, 16))),
    dict(solution_id='edta_cell', volume_m3=1.0e-6, buffer=dict(buffer_id='mes', concentration_mol_m3=10.0, ph=5.6), solutes=[dict(species_id='edta', concentration_mol_m3=0.1)]),
    ['loading_syringe'], '在实验台上配溶液：取已有溶液或评测者给的储备液分装（sources），称入试剂（solutes，按终浓度），用新配缓冲液（buffer：缓冲剂、浓度、pH、NaCl）或与某溶液相同的缓冲液'
    '（buffer_from，例如透析外液）定容；称量与配pH有误差（真值不可见）', 'agent', 'robot_skill')
cap('solution.degas', obj(dict(solution_id=SID)), obj(dict(solution_id=TEXT, degassed=BOOL)), dict(solution_id='edta_cell'), ['loading_syringe'],
    '溶液脱气（真空脱气后约2 h内有效；未脱气的样品在高于室温3 K以上运行时析出气泡）', 'agent', 'robot_skill')
cap('solution.dialyze', obj(dict(solution_id=SID, buffer=RECIPE, dialysate_id=SID, dialysate_volume_m3=number('m3', 1.0e-6, 5.0e-5)), optional=('dialysate_volume_m3',)),
    obj(dict(solution_id=TEXT, dialysate_id=TEXT, ph_nominal=number('1'))),
    dict(solution_id='protein', buffer=dict(buffer_id='hepes', concentration_mol_m3=20.0, ph=7.5, nacl_mol_m3=150.0), dialysate_id='dialysate'), ['loading_syringe'],
    '把样品对缓冲液透析（小分子与缓冲成分换成透析液，大分子保留），透析外液另存为溶液（用来配滴定剂以避免缓冲液不匹配）', 'agent', 'robot_skill')
cap('cell.load', obj(dict(solution_id=SID, volume_m3=number('m3', 5.0e-8, 5.0e-7), rinse=BOOL), optional=('rinse',)),
    obj(dict(solution_id=TEXT, loaded_m3=number('m3'), warnings=array(TEXT, 0, 8))), dict(solution_id='edta_cell', volume_m3=3.0e-7, rinse=True),
    ['cell_ports', 'cell_recess', 'loading_syringe'],
    '用装样针吸净样品池后（可先用同一溶液约300 µL润洗）装入样品（≥280 µL，厂家）；须移液器不在池中、池清洗工具已取出', 'agent', 'robot_skill')
cap('cell.empty', obj({}), obj(dict(removed_m3=number('m3'))), {}, ['cell_ports', 'loading_syringe'], '用装样针吸出样品池中的液体（池壁与池底残留约4 µL）', 'agent', 'robot_skill')
cap('reference.refill', obj(dict(solution_id=nullable(SID)), optional=('solution_id',)), obj(dict(volume_m3=number('m3'), solution=TEXT)), {},
    ['reference_cell_lid', 'cell_ports', 'loading_syringe'], '取下参比池盖，用玻璃注射器吸空参比池并注入约280 µL脱气蒸馏水（或指定溶液），盖回盖子（厂家建议每周一次）', 'agent', 'robot_skill')
cap('fpa.attach', obj({}), obj(dict(attached=BOOL)), {}, ['fpa', 'syringe_glass'], '把Fill Port Adapter从存放位装到注射器的灌注口（移液器不在样品池中）', 'agent', 'robot_skill')
cap('fpa.detach', obj({}), obj(dict(attached=BOOL)), {}, ['fpa', 'syringe_glass'], '取下Fill Port Adapter放回存放位', 'agent', 'robot_skill')
cap('pipette.move', obj(dict(location=enum(LOCATIONS))), obj(dict(location=TEXT, travel_s=number('s'))), dict(location='load'),
    ['pipette_carriage', 'pipette_arm', 'pipette_needle'],
    '把移液器升起、转到Cell/Rest/Load/Clean位置并放下（手动操作的机器人技能；进样品池前须取下FPA、取出池清洗工具）', 'agent', 'robot_skill')
cap('tube.place', obj(dict(solution_id=SID, volume_m3=number('m3', 1.0e-9, V_TUBE_MAX))), obj(dict(solution_id=TEXT, volume_m3=number('m3'))),
    dict(solution_id='cacl2_syr', volume_m3=6.0e-8), ['titration_loading_station'], '把装有滴定剂的PCR管（约60 µL，厂家）放进Load位置的管座（移液器不能在Load位置放下）',
    'agent', 'robot_skill')
cap('tube.remove', obj({}), obj(dict(solution_id=nullable(TEXT), volume_m3=number('m3'))), {}, ['titration_loading_station'], '取出管座中的PCR管', 'agent', 'robot_skill')
cap('syringe.plunger_down', obj({}), obj(dict(position_m=number('m'))), {}, ['plunger_rod', 'syringe_glass'],
    'Plunger Down：柱塞推到底（注射器里的液体从针尖排出：在PCR管中回到管里，在清洗站进废液，在样品池中进入样品池）')
cap('syringe.load', obj({}), OPERATION, {}, ['plunger_rod', 'syringe_glass', 'fpa', 'titration_loading_station', 'wash_body'],
    'Load：柱塞在下位、移液器在Load位置放下（针在PCR管中）、装好FPA、洗涤模块开着时，柱塞上行并由真空把滴定剂经针吸满注射器（约1 min，异步）')
cap('syringe.purge_refill', obj({}), OPERATION, {}, ['plunger_rod', 'syringe_glass'], 'Purge/Refill：柱塞推到底再回到上位以排出注射器里的气泡（针须在PCR管中；约20 s，异步）')
cap('cleaning_tool.insert', obj({}), obj(dict(inserted=BOOL)), {}, ['cell_cleaning_tool', 'cell_ports'], '把池清洗工具插入样品池口（移液器须不在池中）', 'agent', 'robot_skill')
cap('cleaning_tool.remove', obj({}), obj(dict(inserted=BOOL)), {}, ['cell_cleaning_tool', 'cell_ports'], '取出池清洗工具放回洗涤模块', 'agent', 'robot_skill')
cap('run.start', obj(dict(experiment_id=SID)), OPERATION, dict(experiment_id='ca_edta_1'), ['cell_body', 'pipette_head', 'plunger_rod'],
    'Start：按方法运行（热平衡并开始搅拌、初始延迟、逐针注射、记录DP），结果存为实验；须样品池与注射器已装、移液器放在样品池中、FPA取下（异步）')
cap('instrument.stop', obj({}), obj(dict(stopped=array(TEXT, 0, 4))), {}, ['cell_body'], '仪器控制栏的Stop：停止当前的运行、装样或清洗')
cap('clean.start', obj(dict(cell_method=enum(CELL_METHODS), syringe_method=enum(SYRINGE_METHODS))), OPERATION, dict(cell_method='wash', syringe_method='rinse'),
    ['wash_body', 'cell_cleaning_tool', 'wash_load_station', 'bottle_tubing', 'waste_bottle'],
    'Clean：池清洗（须插入池清洗工具；Wash/Rinse/Soak）与注射器清洗（须移液器在Clean位置并装FPA；Rinse/Wash，最后约8 min用甲醇干燥）；异步')
cap('wash.empty_waste', obj({}), obj(dict(emptied_m3=number('m3'))), {}, ['waste_bottle', 'waste_bottle_cap'], '拧开废液瓶灰色瓶盖倒空废液（系统须空闲）', 'agent', 'robot_skill')
cap('wash.empty_overflow', obj({}), obj(dict(emptied_m3=number('m3'))), {}, ['overflow_bottle', 'overflow_cap'], '倒空溢流瓶（溢流瓶满时洗涤模块停泵）', 'agent', 'robot_skill')
cap('wash.refill', obj(dict(bottle=enum(['detergent', 'methanol', 'water']))), obj(dict(bottle=TEXT, volume_m3=number('m3'))), dict(bottle='water'),
    ['detergent_bottle', 'methanol_bottle', 'water_bottle'], '加满洗涤剂瓶（20 % Contrad 70或14 % Decon 90）、甲醇瓶（≥99 %）或水瓶（系统须空闲）', 'agent', 'robot_skill')
cap('data.analyze', ANALYZE, ANALYZE_OUT, dict(experiment_id='ca_edta_1', model='one_set_of_sites', exclude_injections=[1], control='fitted_offset'), ['cell_body'],
    '分析软件：自动基线与积分、对照扣除（fitted offset或对照实验）、按模型拟合（One Set of Sites、Two Sets of Sites、Sequential Binding Sites、Competitive、Dissociation、'
    'Enzyme Kinetics多针/单针），给出N、KD、ΔH、ΔG、−TΔS、c值、残差与Bin评语', 'agent', 'query')
cap('data.read', obj(dict(experiment_id=SID, max_points=integer(10, 5000)), optional=('max_points',)),
    obj(dict(experiment_id=TEXT, time_s=array(number('s'), 0, 5000), dp_w=array(number('W'), 0, 5000), temperature_k=array(number('K'), 0, 5000),
             injections=array(obj(dict(index=INT, start_s=number('s'), volume_m3=number('m3'), duration_s=number('s'))), 0, MAX_INJECTIONS), completed=BOOL)),
    dict(experiment_id='ca_edta_1', max_points=500), ['cell_body'], '读实验的原始DP曲线（均匀抽点）、样品池温度与注射时刻', 'agent', 'query')
cap('experiments.list', obj({}), obj(dict(experiments=array(EXP_ROW, 0, MAX_EXPERIMENTS))), {}, ['cell_body'], '列出实验', 'agent', 'query')
cap('design.simulate', obj(dict(n=number('1', 0.01, 100.0), kd_mol_m3=number('mol/m3', 1e-9, 1e5), dh_j_mol=number('J/mol', -1e6, 1e6),
                                cell_concentration_mol_m3=number('mol/m3', 1e-6, 1e4), syringe_concentration_mol_m3=number('mol/m3', 1e-6, 1e5),
                                injection_volumes_m3=array(number('m3', 1e-11, V_SYR), 1, MAX_INJECTIONS))),
    obj(dict(heats_j=array(number('J'), 0, MAX_INJECTIONS), molar_ratio=array(number('1'), 0, MAX_INJECTIONS), c_value=number('1'), total_heat_j=number('J'),
             second_injection_heat_j=nullable(number('J')), guidance=array(TEXT, 0, 8))),
    dict(n=1.0, kd_mol_m3=5.73e-4, dh_j_mol=-16359.44, cell_concentration_mol_m3=0.1, syringe_concentration_mol_m3=1.0, injection_volumes_m3=[4e-10] + [2e-9] * 19),
    ['cell_body'], '实验设计（模拟器）：按一组位点模型与估计的N、KD、ΔH预测每针热量、c值，并对照厂家的热量建议（第二针≥1 µcal、理想>2.5 µcal，总热量≥16 µcal；c宜5–500）',
    'agent', 'query')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS_LOG), dropped=INT)), dict(since_s=0.0), ['cell_body'], '读事件日志',
    'agent', 'query')
cap('provenance.read', obj({}), obj(dict(libraries=array(obj(dict(name=TEXT, file=TEXT, entries=INT)), 0, 8), shelf_species=array(TEXT, 0, 200),
                                         interactions=array(obj(dict(interaction_id=TEXT, receptor=TEXT, ligand=TEXT, evidence=TEXT)), 0, 64))),
    {}, ['cell_body'], '读模板库来源、可用试剂（架上物质）与库中已知相互作用的名称（不含热力学真值）', 'agent', 'query')
cap('stock.provide', obj(dict(stock_id=SID, volume_m3=number('m3', 1.0e-8, 5.0e-5), buffer=nullable(RECIPE), components=array(SOLUTE, 0, 6),
                              label=array(SOLUTE, 0, 6), macromolecules=array(SPID, 0, 6), degassed=BOOL), optional=('label', 'macromolecules', 'degassed')),
    obj(dict(stock_id=TEXT, volume_m3=number('m3'))),
    dict(stock_id='protein_stock', volume_m3=2.0e-6, buffer=dict(buffer_id='tris', concentration_mol_m3=50.0, ph=8.0, nacl_mol_m3=100.0),
         components=[dict(species_id='carbonic_anhydrase_ii', concentration_mol_m3=0.19)], label=[dict(species_id='carbonic_anhydrase_ii', concentration_mol_m3=0.2)]),
    ['cell_body'], '评测者给一份储备液：真实浓度与缓冲液（agent只看到标签浓度）；可声明新物质为大分子（透析时保留）', 'evaluator', 'simulation')
cap('interaction.define', obj(dict(interaction_id=SID, model=enum(['sites', 'sequential', 'dimer', 'enzyme']), receptor=SPID, ligand=SPID, n=number('1', 0.01, 100.0),
                                   kd_mol_m3=number('mol/m3', 1e-12, 1e6), dh_j_mol=number('J/mol', -1e6, 1e6), dcp_j_k_mol=number('J/(K*mol)', -1e5, 1e5),
                                   temperature_k=number('K', *T_RANGE), n_h=number('1', -10.0, 10.0), kon_m3_mol_s=number('m3/(mol*s)', 1e-3, 1e8), site=SID,
                                   ks_mol_m3=array(number('mol/m3', 1e-12, 1e6), 2, 3), dhs_j_mol=array(number('J/mol', -1e6, 1e6), 2, 3),
                                   km_mol_m3=number('mol/m3', 1e-6, 1e5), kcat_s=number('1/s', 1e-6, 1e7)),
                              optional=('ligand', 'n', 'kd_mol_m3', 'dh_j_mol', 'dcp_j_k_mol', 'temperature_k', 'n_h', 'kon_m3_mol_s', 'site', 'ks_mol_m3', 'dhs_j_mol',
                                        'km_mol_m3', 'kcat_s')),
    obj(dict(interaction_id=TEXT)),
    dict(interaction_id='p_l', model='sites', receptor='protein_x', ligand='ligand_y', n=1.0, kd_mol_m3=1e-3, dh_j_mol=-40000.0, n_h=0.5), ['cell_body'],
    '评测者定义物质间相互作用的真值：位点结合（N、KD、ΔH、ΔCp、质子释放数、结合速率常数、位点标签：同一位点的不同配体相互竞争，同一配体的不同位点即两组位点）、顺序结合、'
    '二聚体解离或酶反应（KM、kcat、ΔH）', 'evaluator', 'simulation')
cap('cell.inspect', obj({}), obj(dict(volume_m3=number('m3'), species=SPECMAP, ph=nullable(number('1')), ionic_strength_mol_m3=number('mol/m3'),
                                      bound_mol_m3=SPECMAP, temperature_k=number('K'), bubble=BOOL, contamination_fraction=number('1'))),
    {}, ['cell_body'], '评测者读样品池有效体积内的真值：各物质总浓度、pH、离子强度、各相互作用的结合量、温度、气泡与上一份样品的残留比例', 'evaluator', 'query')
cap('syringe.inspect', obj({}), obj(dict(liquid_m3=number('m3'), bubble_m3=number('m3'), species=SPECMAP, residue=TEXT, tip_dilution_m3=number('m3'))),
    {}, ['syringe_glass'], '评测者读注射器真值：液体体积、气泡、各物质浓度、残留（水或甲醇）与针尖被池液稀释的体积', 'evaluator', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=LEDGER)), {}, ['cell_body'], '读守恒账目（电能、样品/滴定剂/清洗液/废液、参比池水、化学热、注射与运行次数）', 'evaluator', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT, 0, 16))), dict(fault='cell_bubble', active=True), ['cell_body'],
    '评测者故障注入：断电、洗涤泵失效、瓶滤芯堵塞、注射器损坏（装样失败）、搅拌电机失效、样品池气泡、参比池干', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 7 * 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=60.0), ['cell_body'], '推进宿主时钟（0.2 s物理网格）', 'evaluator',
    'simulation')
UNSUPPORTED = {
    'autosampler.run_queue': '自动进样（PEAQ-ITC Automated的384样品、370 µL装样）不在所选标准型配置内',
    'cell.auto_fill': '自动装池（Automated）不在所选标准型配置内；标准型须用装样针手动装样',
    'dsc.temperature_scan': '升温扫描（DSC）不是本仪器的工作流（等温滴定量热仪）',
}
for k, v in UNSUPPORTED.items():
    cap(k, obj({}), obj({}), {}, ['cell_body'], v)

# ---------------------------------------------------------------- analysis (equivalent of the MicroCal PEAQ-ITC Analysis software)
BASE_WIN = 10.0                              # s of data averaged for the pre / post injection baselines (assumption)


def _integrate(exp):
    """Automatic baseline and integration: linear baseline between the means of the 10 s before the injection and the 10 s before the next one."""
    t = np.asarray(exp['t'], float)
    dp = np.asarray(exp['dp'], float)
    injs = exp['injections']
    rows = []
    if len(t) < 3 or not injs:
        return rows
    starts = [i['start_s'] for i in injs]
    t_end = t[-1] + REC_DT
    for i, inj in enumerate(injs):
        s = starts[i]
        e = starts[i + 1] if i + 1 < len(injs) else t_end
        pre = (t >= s - BASE_WIN) & (t < s)
        post = (t >= e - BASE_WIN) & (t < e)
        late = (t >= e - 2 * BASE_WIN) & (t < e - BASE_WIN)
        m = (t >= s) & (t < e)
        if not pre.any() or not post.any() or not m.any():
            rows.append(dict(index=inj['index'], volume_m3=inj['volume_m3'], heat_j=0.0, amp=0.0, b0=None, b1=None, resid=0.0, s=s, e=e, valid=False))
            continue
        b0, b1 = float(dp[pre].mean()), float(dp[post].mean())
        base = b0 + (b1 - b0) * (t[m] - s) / max(e - s, 1e-9)
        dev = dp[m] - base
        amp = float(dev[np.argmax(np.abs(dev))])
        resid = abs(b1 - float(dp[late].mean())) if late.any() else 0.0
        rows.append(dict(index=inj['index'], volume_m3=inj['volume_m3'], heat_j=float(dev.sum() * REC_DT), amp=amp, b0=b0, b1=b1, resid=resid, s=s, e=e, valid=True))
    return rows


def _noise_estimate(exp, rows, worst=False):
    t = np.asarray(exp['t'], float)
    dp = np.asarray(exp['dp'], float)
    sd = []
    for r in rows:
        w = (t >= r['e'] - BASE_WIN) & (t < r['e'])
        if w.sum() >= 4:
            x = dp[w]
            sd.append(float(np.std(np.diff(x)) / math.sqrt(2)))
    if not sd:
        return 0.0
    return float(np.max(sd)) if worst else float(np.median(sd))


def _lm(fun, p0, free, y, max_iter=200):
    """Levenberg-Marquardt on the free parameters; returns (p, cov or None, ssr)."""
    p = np.array(p0, float)
    idx = [i for i, f in enumerate(free) if f]
    r = fun(p) - y
    cost = float(r @ r)
    lam = 1e-3
    J = None
    for _ in range(max_iter):
        if not idx:
            break
        J = np.zeros((len(y), len(idx)))
        for j, i in enumerate(idx):
            h = 1e-6 * max(1.0, abs(p[i]))
            pp = p.copy()
            pp[i] += h
            J[:, j] = (fun(pp) - y - r) / h
        A, g = J.T @ J, J.T @ r
        improved = False
        for _k in range(30):
            try:
                stp = -np.linalg.solve(A + lam * np.diag(np.diag(A) + 1e-300), g)
            except np.linalg.LinAlgError:
                lam *= 10
                continue
            pn = p.copy()
            pn[idx] += stp
            rn = fun(pn) - y
            cn = float(rn @ rn)
            if np.isfinite(cn) and cn < cost:
                rel = (cost - cn) / max(cost, 1e-300)
                p, r, cost = pn, rn, cn
                lam = max(lam / 10, 1e-12)
                improved = True
                break
            lam *= 10
        if not improved or rel < 1e-12:
            break
    cov = None
    if idx and J is not None:
        dof = max(1, len(y) - len(idx))
        try:
            cov = np.linalg.inv(J.T @ J) * cost / dof
        except np.linalg.LinAlgError:
            cov = None
    return p, cov, cost, idx


def _free_two(Mt, Xt, ns, ks):
    if Xt <= 0:
        return 0.0
    lo, hi = math.log(Xt) - 70.0, math.log(Xt)
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        L = math.exp(mid)
        if L + Mt * sum(n * k * L / (1 + k * L) for n, k in zip(ns, ks)) > Xt:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-12:
            break
    return math.exp(0.5 * (lo + hi))


def _competitive_q(Mt, At, Bt, n, ka, dha, kb, dhb, v0):
    S_t = n * Mt
    if S_t <= 0:
        return 0.0
    lo, hi = 0.0, S_t
    for _ in range(100):
        S = 0.5 * (lo + hi)
        tot = S * (1 + ka * At / (1 + ka * S) + kb * Bt / (1 + kb * S))
        if tot > S_t:
            hi = S
        else:
            lo = S
    S = 0.5 * (lo + hi)
    MA = S * ka * At / (1 + ka * S)
    MB = S * kb * Bt / (1 + kb * S)
    return v0 * 1000 * (dha * MA + dhb * MB)


def _apply_fix(fix, starts, free, mapping):
    """Fixed fit parameters (SI values: n, KD in mol/m3, dH / offset in J/mol) written into every start vector and removed from the free set."""
    for key, (i, kind) in mapping.items():
        if key in fix:
            v = fix[key]
            conv = v if kind == 'n' else (-math.log10(v / 1000.0) if kind == 'kd' else v / 4184.0)
            for st in starts:
                st[i] = conv
            free[i] = False


def analyze_experiment(exp, model, exclude=(1,), control='none', control_rows=None, ligand_in_cell=False, cell_m=None, syr_m=None, competitor=None, sites=2,
                       enzyme_dh=None, fix=None):
    """Integration, control subtraction and model fitting of one experiment (concentrations in mol/L internally; outputs SI)."""
    m = exp['method']
    T = m['temperature_k']
    M0 = (cell_m if cell_m is not None else m['cell_concentration_mol_m3']) / 1000.0
    X0 = (syr_m if syr_m is not None else m['syringe_concentration_mol_m3']) / 1000.0
    fix = dict(fix or {})
    rows = _integrate(exp)
    comments, warns = [], []
    sim_model = model in ('enzyme_kinetics_single',)
    n_inj = len(rows)
    heats = np.array([r['heat_j'] for r in rows]) if rows else np.zeros(0)
    if control == 'experiment' and control_rows is not None:
        for i in range(n_inj):
            if i < len(control_rows):
                heats[i] -= control_rows[i]['heat_j']
    excl = set(exclude) if exclude is not None else set()
    incl = [i for i, r in enumerate(rows) if r['valid'] and (r['index'] not in excl or (sim_model and n_inj == 1))]
    vols = [r['volume_m3'] for r in rows]
    sigma_dp = _noise_estimate(exp, rows)
    sig_q = max(sigma_dp * math.sqrt(150.0) * REC_DT, 1e-12)
    t = np.asarray(exp['t'], float)
    dp = np.asarray(exp['dp'], float)
    p_ref = exp['p_ref']
    if len(t):
        first = rows[0]['s'] if rows else t[-1]
        b_init = float(dp[t < min(30.0, max(first, REC_DT))].mean()) if (t < min(30.0, max(first, REC_DT))).any() else float(dp[0])
        b_end = float(dp[-int(BASE_WIN):].mean())
        if b_init < p_ref - W_UCAL(1.0):
            comments.append('Low Initial Baseline')
        if b_init > p_ref + W_UCAL(1.0):
            comments.append('High Initial Baseline')
        if abs(b_end - b_init) > W_UCAL(1.0) and model not in ('enzyme_kinetics_multiple', 'enzyme_kinetics_single'):
            comments.append('Check Baseline Drift')
        if (dp <= 0).any():
            comments.append('Data Below Minimum')
        if (dp >= ENG['dp_max_w']).any():
            comments.append('Data Exceeds Maximum')
        base = [r['b1'] for r in rows if r['valid']]
        steps = [abs(base[i + 1] - 2 * base[i] + base[i - 1]) for i in range(1, len(base) - 1)]
        stepped = model not in ('enzyme_kinetics_multiple', 'enzyme_kinetics_single') and steps and max(steps) > W_UCAL(0.03)
        if sigma_dp > 5 * ENG['noise_w'][exp['feedback']] or _noise_estimate(exp, rows, worst=True) > 10 * ENG['noise_w'][exp['feedback']] or stepped:
            comments.append('Possible Poor Load')                              # noisy or stepping baseline (bubbles in the cell)
    if M0 <= 0 or X0 <= 0:
        comments.append('Concentrations = 0.0')
    big = [r for r in rows if r['valid'] and abs(r['amp']) > 20 * max(sigma_dp, 1e-15)]
    tau_fb = ENG['response_time_s'][exp['feedback']]

    def tail_left(r):                                                            # signal still above the baseline at the window end ~ |slope| x tau,
        w = (t >= r['e'] - 2 * BASE_WIN) & (t < r['e'])                         # counted only when the slope is resolved (> 3 standard errors)
        if w.sum() < 5:
            return 0.0
        coef, cov = np.polyfit(t[w], dp[w], 1, cov=True)
        slope, se = abs(float(coef[0])), math.sqrt(max(float(cov[0, 0]), 0.0))
        return slope * tau_fb if slope > 3 * se else 0.0
    if big and sum(1 for r in big if r['resid'] > 0.05 * abs(r['amp']) or tail_left(r) > 0.02 * abs(r['amp'])) > 0.3 * len(big) \
            and model not in ('enzyme_kinetics_multiple', 'enzyme_kinetics_single'):
        comments.append('Injection Spacing Too Short')
    if sim_model and n_inj > 1:
        comments.append('Too Many Injections')
    if not sim_model and len(incl) < 5:
        comments.append('Too Few Injections')
    tot = float(sum(heats[i] for i in incl)) if incl else 0.0
    if incl and model not in ('enzyme_kinetics_multiple', 'enzyme_kinetics_single', 'dissociation'):
        last3 = [heats[i] / vols[i] for i in incl[-3:]]
        if abs(np.mean(last3)) > W_UCAL(0.25) / 1e-9 and abs(heats[incl[-1]]) > 0.25 * max(abs(heats[i]) for i in incl):
            comments.append('Possible Buffer Mismatch')
    out_rows = []
    params, derived = [], dict(ka_m3_mol=None, dg_j_mol=None, minus_tds_j_mol=None, ds_j_k_mol=None, c_value=None, chi2_reduced=None, temperature_k=T,
                               total_heat_j=tot)
    fit_heats = [None] * n_inj
    rate_col, s_col = [None] * n_inj, [None] * n_inj
    ok_to_fit = 'Concentrations = 0.0' not in comments and incl and not (sim_model and n_inj > 1) and (sim_model or len(incl) >= 3)
    conc = _cum_conc(M0, X0, vols, V0)
    inj_mol = [X0 * v * 1000 for v in vols]
    use_off = control == 'fitted_offset'

    def finish(names, units, scale, p, cov, idx, ssr, model_fn):
        fh = model_fn(p)
        for j, i in enumerate(incl):
            fit_heats[i] = float(fh[j])
        dof = max(1, len(incl) - len(idx))
        derived['chi2_reduced'] = float(ssr / dof / sig_q ** 2)
        err = {}
        if cov is not None:
            for j, i in enumerate(idx):
                err[i] = math.sqrt(max(cov[j, j], 0.0))
        for i, (nm, un) in enumerate(zip(names, units)):
            val, e = scale[i](p[i], err.get(i))
            params.append(dict(name=nm, value=float(val), error=None if e is None else float(e), unit=un, fixed=i not in idx))

    def kd_of(lk, e):
        kd = 10 ** (-lk)
        return kd * 1000.0, (kd * 1000.0 * math.log(10) * e) if e is not None else None
    ident = lambda v, e: (v, e)                                                       # noqa: E731
    kcal = lambda v, e: (v * 4184.0, e * 4184.0 if e is not None else None)          # noqa: E731
    y = np.array([heats[i] for i in incl]) / UCAL if incl else np.zeros(0)
    ndh = [heats[i] / inj_mol[i] if inj_mol[i] > 0 else 0.0 for i in range(n_inj)]
    dh0 = (ndh[incl[0]] if incl else -1e4) / 4184.0
    if ok_to_fit and model in ('one_set_of_sites', 'two_sets_of_sites', 'sequential_binding_sites', 'competitive'):
        if model == 'one_set_of_sites':
            def heat_fn(p):
                n, lk, dh, off = p
                n = max(n, 1e-3)
                if ligand_in_cell:
                    Q = [_one_set_q(Xt, Mt, n, 10 ** lk, dh * 4184.0, V0) for Mt, Xt in conc]
                else:
                    Q = [_one_set_q(Mt, Xt, n, 10 ** lk, dh * 4184.0, V0) for Mt, Xt in conc]
                dq = _delta_q(Q, vols, V0)
                return np.array([(dq[i] + (off * 4184.0 * inj_mol[i] if use_off else 0.0)) / UCAL for i in incl])
            names, units = ['n', 'kd', 'dh', 'offset'], ['1', 'mol/m3', 'J/mol', 'J/mol']
            starts = [[fix.get('n', 1.0), lk0, fix['dh'] / 4184.0 if 'dh' in fix else dh0 * 1.05, (fix.get('offset', 0.0)) / 4184.0] for lk0 in (4.0, 5.5, 7.0, 8.5)]
            if 'kd' in fix:
                for st in starts:
                    st[1] = -math.log10(fix['kd'] / 1000.0)
            free = ['n' not in fix, 'kd' not in fix, 'dh' not in fix, use_off and 'offset' not in fix]
            scale = [ident, kd_of, kcal, kcal]
        elif model == 'two_sets_of_sites':
            def heat_fn(p):
                n1, lk1, dh1, n2, lk2, dh2, off = p
                n1, n2 = max(n1, 1e-3), max(n2, 1e-3)
                Q = []
                for Mt, Xt in conc:
                    L = _free_two(Mt, Xt, [n1, n2], [10 ** lk1, 10 ** lk2])
                    th = [10 ** lk1 * L / (1 + 10 ** lk1 * L), 10 ** lk2 * L / (1 + 10 ** lk2 * L)]
                    Q.append(V0 * 1000 * Mt * (n1 * th[0] * dh1 + n2 * th[1] * dh2) * 4184.0)
                dq = _delta_q(Q, vols, V0)
                return np.array([(dq[i] + (off * 4184.0 * inj_mol[i] if use_off else 0.0)) / UCAL for i in incl])
            names, units = ['n1', 'kd1', 'dh1', 'n2', 'kd2', 'dh2', 'offset'], ['1', 'mol/m3', 'J/mol', '1', 'mol/m3', 'J/mol', 'J/mol']
            starts = [[0.5, a, dh0, 0.5, b, dh0 * 0.5, 0.0] for a, b in ((8.0, 5.0), (7.0, 4.5), (6.5, 5.5))]
            free = [True] * 6 + [use_off]
            _apply_fix(fix, starts, free, {'n': (0, 'n'), 'kd': (1, 'kd'), 'dh': (2, 'dh'), 'n2': (3, 'n'), 'kd2': (4, 'kd'), 'dh2': (5, 'dh'), 'offset': (6, 'dh')})
            scale = [ident, kd_of, kcal, ident, kd_of, kcal, kcal]
        elif model == 'sequential_binding_sites':
            ns = sites

            def heat_fn(p):
                lks, dhs, off = p[0:2 * ns:2], p[1:2 * ns:2], p[2 * ns]
                Q = []
                for Mt, Xt in conc:
                    _, fr = _sequential_bound(Xt, Mt, [10 ** x for x in lks])
                    cum, q = 0.0, 0.0
                    for i, f in enumerate(fr[1:], start=1):
                        cum += dhs[i - 1]
                        q += f * cum
                    Q.append(V0 * 1000 * Mt * q * 4184.0)
                dq = _delta_q(Q, vols, V0)
                return np.array([(dq[i] + (off * 4184.0 * inj_mol[i] if use_off else 0.0)) / UCAL for i in incl])
            names, units = [], []
            for i in range(ns):
                names += [f'kd{i + 1}', f'dh{i + 1}']
                units += ['mol/m3', 'J/mol']
            names.append('offset')
            units.append('J/mol')
            starts = [sum(([6.5 - i * d, dh0 / (i + 1)] for i in range(ns)), []) + [0.0] for d in (0.5, 1.0, 1.5)]
            free = [True] * (2 * ns) + [use_off]
            scale = [kd_of, kcal] * ns + [kcal]
        else:
            if competitor is None:
                raise ActionRejected('COMPETITOR_REQUIRED')
            kb = 1000.0 / competitor['kd_mol_m3']
            dhb = competitor['dh_j_mol']
            B0 = competitor['concentration_mol_m3'] / 1000.0

            def heat_fn(p):
                n, lk, dh, off = p
                n = max(n, 1e-3)
                Q = []
                cv = 0.0
                for (Mt, Xt), v in zip(conc, vols):
                    cv += v
                    r = cv / V0
                    Bt = B0 * (1 - r / 2) / (1 + r / 2)
                    Q.append(_competitive_q(Mt, Xt, Bt, n, 10 ** lk, dh * 4184.0, kb, dhb, V0))
                q00 = _competitive_q(M0, 0.0, B0, n, 10 ** lk, dh * 4184.0, kb, dhb, V0)
                dq = []
                prev = q00
                for i, q in enumerate(Q):
                    dq.append(q + vols[i] / V0 * (q + prev) / 2 - prev)
                    prev = q
                return np.array([(dq[i] + (off * 4184.0 * inj_mol[i] if use_off else 0.0)) / UCAL for i in incl])
            names, units = ['n', 'kd', 'dh', 'offset'], ['1', 'mol/m3', 'J/mol', 'J/mol']
            th_b = kb * B0 / (1 + kb * B0)
            dha0 = dh0 + dhb / 4184.0 * th_b                                 # apparent dH = dH_A - dH_B theta_B
            starts = [[1.0, a, dha0, 0.0] for a in (6.5, 7.5, 8.5, 9.5)]
            free = [True, True, True, use_off]
            _apply_fix(fix, starts, free, {'n': (0, 'n'), 'kd': (1, 'kd'), 'dh': (2, 'dh'), 'offset': (3, 'dh')})
            scale = [ident, kd_of, kcal, kcal]
        lk_idx = [i for i, nm in enumerate(names) if nm.startswith('kd')]
        raw_fn = heat_fn

        def heat_fn(p):                                                            # noqa: F811  (log K kept within 10^-3 .. 10^14 1/M)
            q = np.array(p, float)
            q[lk_idx] = np.clip(q[lk_idx], -3.0, 14.0)
            return raw_fn(q)
        best = None
        for st in starts:
            pr, cov, ssr, idx = _lm(heat_fn, st, free, y)
            if best is None or ssr < best[2]:
                best = (pr, cov, ssr, idx)
        pr, cov, ssr, idx = best
        pr = np.array(pr, float)
        pr[lk_idx] = np.clip(pr[lk_idx], -3.0, 14.0)
        finish(names, units, scale, pr, cov, idx, ssr * UCAL ** 2, lambda p: heat_fn(p) * UCAL)
        if model in ('one_set_of_sites', 'competitive'):
            n, lk, dh = pr[0], pr[1], pr[2] * 4184.0
            kd = 10 ** (-lk)
            dg = R_GAS * T * math.log(kd)
            derived.update(ka_m3_mol=float(10 ** lk / 1000.0), dg_j_mol=float(dg), minus_tds_j_mol=float(dg - dh), ds_j_k_mol=float((dh - dg) / T),
                           c_value=float(n * (X0 if ligand_in_cell else M0) / kd))
    elif ok_to_fit and model == 'dissociation':
        def dimer(c, kd):
            Mf = (-1 + math.sqrt(1 + 8 * c / kd)) * kd / 4 if c > 0 else 0.0
            return (c - Mf) / 2

        def heat_fn(p):
            lkd, dh, off = p
            kd = 10 ** lkd
            Q = [V0 * 1000 * dh * 4184.0 * (Xt / 2 - dimer(Xt, kd)) for _, Xt in conc]
            own = dh * 4184.0 * (X0 / 2 - dimer(X0, kd))
            dq = []
            prev = 0.0
            for i, q in enumerate(Q):
                dq.append(q + vols[i] / V0 * (q + prev) / 2 - prev - vols[i] * 1000 * own)
                prev = q
            return np.array([(dq[i] + (off * 4184.0 * inj_mol[i] if use_off else 0.0)) / UCAL for i in incl])
        best = None
        for st in ([-3.0, abs(dh0) * 2 + 1.0, 0.0], [-5.0, abs(dh0) * 2 + 1.0, 0.0], [-2.0, abs(dh0) + 1.0, 0.0]):
            pr, cov, ssr, idx = _lm(heat_fn, st, [True, True, use_off], y)
            if best is None or ssr < best[2]:
                best = (pr, cov, ssr, idx)
        pr, cov, ssr, idx = best
        finish(['kd_dimer', 'dh_dissociation', 'offset'], ['mol/m3', 'J/mol', 'J/mol'],
               [lambda v, e: (10 ** v * 1000.0, 10 ** v * 1000.0 * math.log(10) * e if e is not None else None), kcal, kcal], pr, cov, idx, ssr * UCAL ** 2,
               lambda p: heat_fn(p) * UCAL)
    elif ok_to_fit and model in ('enzyme_kinetics_multiple', 'enzyme_kinetics_single'):
        E0 = M0
        E_list = []
        if model == 'enzyme_kinetics_multiple':
            if enzyme_dh is None:
                raise ActionRejected('ENZYME_DH_REQUIRED')
            dh = enzyme_dh
            base = float(dp[t < rows[0]['s']].mean()) if (t < rows[0]['s']).any() else float(dp[0])
            S, R_ = [], []
            for i, r in enumerate(rows):
                w = (t >= r['s'] + 0.7 * (r['e'] - r['s'])) & (t < r['e'])
                lev = float(dp[w].mean()) if w.any() else base
                rate = (lev - base) / (dh * V0 * 1000)
                rate_col[i] = rate * 1000.0
                s_col[i] = conc[i][1] * 1000.0
                if i in incl:
                    S.append(conc[i][1])
                    R_.append(rate)
                    E_list.append(conc[i][0])
        else:
            r0 = rows[0]
            base = r0['b0']                                            # single injection: constant pre-injection baseline
            w = (t >= r0['s']) & (t < r0['e'])
            S0 = X0 * vols[0] / V0 * (1 - vols[0] / (2 * V0))
            mol = S0 * V0 * 1000
            qt = np.cumsum(dp[w] - base) * REC_DT
            Qtot = float(qt[-1]) if len(qt) else 0.0
            heats[0] = Qtot
            dh = (enzyme_dh if enzyme_dh is not None else Qtot / mol if mol > 0 else -1.0)
            v = (dp[w] - base) / (dh * V0 * 1000)
            Sv = S0 * (1 - qt / Qtot) if Qtot != 0 else np.full(len(qt), S0)    # substrate left from the cumulative heat (complete conversion)
            tail = dp[w][-int(BASE_WIN):] if w.sum() > BASE_WIN else dp[w]
            if len(tail) and abs(tail.mean() - base) > 0.05 * float(np.max(np.abs(dp[w] - base))):
                comments.append('Check Baseline')
            tau = ENG['response_time_s'][exp['feedback']]
            sel = (t[w] > r0['s'] + exp['injections'][0]['duration_s'] + 3 * tau + 5) & (Sv > 0.02 * S0) & (v > 0)
            S, R_ = list(Sv[sel]), list(v[sel])
        S, R_ = np.array(S), np.array(R_)
        Ev = np.array(E_list) if len(E_list) == len(S) else np.full(len(S), E0)      # enzyme diluted by the displaced volume
        if len(S) >= 3 and E0 > 0:
            yv = R_ / max(abs(R_).max(), 1e-300)
            sc = max(abs(R_).max(), 1e-300)

            def rate_fn(p):
                km, kcat = 10 ** p[0], p[1]
                return kcat * Ev * S / (km + S) / sc
            best = None
            vmax0 = R_.max() / E0 if len(R_) else 1.0
            for lkm in (math.log10(max(S.mean(), 1e-12)), math.log10(max(S.mean(), 1e-12)) - 1, math.log10(max(S.mean(), 1e-12)) + 1):
                pr, cov, ssr, idx = _lm(rate_fn, [lkm, vmax0 * 1.5], [True, True], yv)
                if best is None or ssr < best[2]:
                    best = (pr, cov, ssr, idx)
            pr, cov, ssr, idx = best
            e_ = {}
            if cov is not None:
                e_ = {i: math.sqrt(max(cov[j, j], 0.0)) for j, i in enumerate(idx)}
            km = 10 ** pr[0]
            params.append(dict(name='km', value=float(km * 1000.0), error=float(km * 1000.0 * math.log(10) * e_[0]) if 0 in e_ else None, unit='mol/m3', fixed=False))
            params.append(dict(name='kcat', value=float(pr[1]), error=float(e_[1] * sc / sc) if 1 in e_ else None, unit='1/s', fixed=False))
            params.append(dict(name='dh', value=float(dh), error=None, unit='J/mol', fixed=model == 'enzyme_kinetics_multiple' or enzyme_dh is not None))
            derived['chi2_reduced'] = float(ssr / max(1, len(S) - 2))
        else:
            comments.append('Check Baseline')
    rows_out = []
    for i, r in enumerate(rows):
        mr = None
        if M0 > 0:
            cm, cx = conc[i]
            mr = cx / cm if not ligand_in_cell else cm / cx if cx > 0 else None
        rows_out.append(dict(index=r['index'], volume_m3=float(r['volume_m3']), heat_j=float(heats[i]), ndh_j_mol=float(ndh[i]) if inj_mol[i] > 0 else None,
                             molar_ratio=float(mr) if mr is not None else None, fit_heat_j=fit_heats[i], excluded=i not in incl, rate_mol_m3_s=rate_col[i],
                             substrate_mol_m3=s_col[i]))
    if model == 'one_set_of_sites' and incl:
        q_first, q_last = abs(heats[incl[0]]), abs(heats[incl[-1]])
        binding = abs(tot) >= ENG_HEAT['total_min_j'] and (q_first - q_last) > max(3 * sig_q, W_UCAL(0.2), 0.2 * q_first)
        if not binding and abs(tot) < ENG_HEAT['total_min_j']:
            comments.append('Too Little Heat')
    checks = {'Low Initial Baseline', 'High Initial Baseline', 'Check Baseline Drift', 'Data Below Minimum', 'Data Exceeds Maximum', 'Possible Poor Load',
              'Injection Spacing Too Short', 'Too Many Injections', 'Too Few Injections', 'Concentrations = 0.0', 'Possible Buffer Mismatch', 'Check Baseline'}
    if any(c in checks for c in comments):
        bin_ = 'Check data'
    elif model == 'one_set_of_sites':
        bin_ = 'Binding' if (incl and binding) else 'No binding'
        comments.append(bin_ if bin_ == 'Binding' else 'No binding')
    else:
        bin_ = 'OK'
        comments.append('OK')
    c_ = derived.get('c_value')
    if c_ is not None and not 5 <= c_ <= 500:
        warns.append('C_VALUE_OUTSIDE_5_500')
    if incl and len(incl) > 1 and abs(heats[incl[0]]) < ENG_HEAT['second_injection_min_j']:
        warns.append('SECOND_INJECTION_HEAT_BELOW_1_UCAL')
    return dict(parameters=params, injections=rows_out, derived=derived, bin=bin_, bin_comments=list(dict.fromkeys(comments)), warnings=warns)


ENG_HEAT = DOC['heat_guidelines']


def design_simulate(n, kd_mol_m3, dh_j_mol, cell_m, syr_m, vols):
    M0, X0 = cell_m / 1000.0, syr_m / 1000.0
    conc = _cum_conc(M0, X0, vols, V0)
    Q = [_one_set_q(Mt, Xt, n, 1000.0 / kd_mol_m3, dh_j_mol, V0) for Mt, Xt in conc]
    dq = _delta_q(Q, vols, V0)
    mr = [cx / cm for cm, cx in conc]
    c = n * M0 / (kd_mol_m3 / 1000.0)
    g = []
    q2 = dq[1] if len(dq) > 1 else None
    if q2 is not None and abs(q2) < ENG_HEAT['second_injection_min_j']:
        g.append('第二针热量低于1 µcal（厂家最低建议）：提高浓度')
    elif q2 is not None and abs(q2) < ENG_HEAT['second_injection_ideal_j']:
        g.append('第二针热量在1–2.5 µcal之间（厂家理想值>2.5 µcal）')
    if abs(sum(dq[1:])) < ENG_HEAT['total_min_j']:
        g.append('总热量低于16 µcal（厂家建议）')
    if not 5 <= c <= 500:
        g.append('c值不在5–500（TN240816建议范围）')
    if mr and mr[-1] < 1.5 * n:
        g.append('末针摩尔比低于1.5倍N：可能达不到饱和')
    return dict(heats_j=[float(x) for x in dq], molar_ratio=[float(x) for x in mr], c_value=float(c), total_heat_j=float(sum(dq)),
                second_injection_heat_j=float(q2) if q2 is not None else None, guidance=g)


# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference)


_PN = lambda u: nullable(number(u))                                             # noqa: E731
RUN_STATES = ['idle', 'equilibrating', 'initial_delay', 'injecting', 'spacing', 'completed', 'stopped', 'failed']
slot('clock.time_s', number('s'), 's', 'derived', 'cell_body', '仿真时钟（0.2 s物理网格）')
slot('plant.configured', BOOL, '1', 'setpoint', 'cell_body', '已声明实验室条件')
slot('power.enabled', BOOL, '1', 'derived', 'power_switch', '量热池单元通电（开关开且供电正常）')
slot('wash.powered', BOOL, '1', 'derived', 'wash_power_switch', '洗涤模块通电')
slot('lamp.status', BOOL, '1', 'derived', 'status_light', '量热池单元前面板绿色指示灯（通电时亮；场景灯镜像）')
slot('lamp.wash_status', BOOL, '1', 'derived', 'wash_status_light', '洗涤模块前面板绿色指示灯（通电时亮；场景灯镜像）')
slot('temperature.setpoint_k', number('K'), 'K', 'setpoint', 'cell_body', '样品池目标温度')
slot('temperature.cell_k', _PN('K'), 'K', 'sensor', 'cell_body', '样品池温度读数（控制栏Temperature；未通电为空）')
slot('dp.power_w', _PN('W'), 'W', 'sensor', 'cell_body', '差分功率DP读数（样品池补偿加热功率；放热时下降；未通电为空）')
slot('dp.reference_power_w', number('W'), 'W', 'setpoint', 'cell_body', '参比功率设定（参比池补偿加热器的恒定功率）')
slot('dp.feedback', enum(FEEDBACK), '1', 'setpoint', 'cell_body', '反馈模式（High/Low/None）')
slot('stir.rate_hz', number('1/s'), '1/s', 'derived', 'pipette_head', '搅拌桨实际转速（1/s；只在运行时转，750 rpm = 12.5 s⁻¹）')
slot('method.current', nullable(obj(dict(temperature_k=number('K'), reference_power_w=number('W'), feedback=TEXT, stir_rate_hz=number('1/s'),
                                         initial_delay_s=number('s'), injections=INT, total_volume_m3=number('m3'), cell_concentration_mol_m3=number('mol/m3'),
                                         syringe_concentration_mol_m3=number('mol/m3')))), '1', 'setpoint', 'cell_body', '当前方法摘要（未设为空）')
slot('run.state', enum(RUN_STATES), '1', 'derived', 'cell_body', '运行状态')
slot('run.experiment_id', nullable(TEXT), '1', 'derived', 'cell_body', '当前/最近一次实验')
slot('run.injection_number', INT, '1', 'derived', 'cell_body', '当前注射序号（控制栏Injection；0为第一针之前）')
slot('run.remaining_time_s', _PN('s'), 's', 'derived', 'cell_body', '运行剩余时间（控制栏Remaining Time；热平衡阶段为空）')
slot('pipette.location', enum(LOCATIONS + ['moving']), '1', 'derived', 'pipette_arm', '移液器位置（Cell/Rest/Load/Clean；移动中为moving）')
slot('pipette.lift_m', number('m'), 'm', 'derived', 'pipette_carriage', '移液器升起高度（0放下，0.096 m升起；场景升降关节镜像）', reference='放下位置')
slot('pipette.swing_rad', number('rad'), 'rad', 'derived', 'pipette_arm', '移液器臂转角（Cell 0、Rest 1.057、Load 1.755、Clean 2.279 rad；场景转动关节镜像）', reference='Cell位置')
slot('pipette.seated_in_cell', BOOL, '1', 'derived', 'pipette_needle', '移液器放在样品池中（针与搅拌桨在池内）')
slot('syringe.plunger_position_m', number('m'), 'm', 'derived', 'plunger_rod', '柱塞杆下行距离（0为上位=满，0.06 m为下位=空；场景柱塞关节镜像）', reference='上位')
slot('syringe.state', enum(['empty', 'filling', 'loaded', 'purging', 'expelling']), '1', 'derived', 'syringe_glass', '注射器状态（按控制软件的操作记录）')
slot('syringe.available_volume_m3', number('m3'), 'm3', 'derived', 'syringe_glass', '按柱塞位置计的剩余可注射体积（装样后；不知道是否有气泡）')
slot('fpa.attached', BOOL, '1', 'derived', 'fpa', 'Fill Port Adapter装在注射器上')
slot('cell.solution_id', nullable(TEXT), '1', 'derived', 'cell_ports', '样品池中装的溶液（agent装入的记录）')
slot('cell.loaded_volume_m3', number('m3'), 'm3', 'derived', 'cell_ports', '最近一次装入样品池的体积')
slot('cell.cleaning_tool_inserted', BOOL, '1', 'derived', 'cell_cleaning_tool', '池清洗工具插在样品池中')
slot('reference.age_s', number('s'), 's', 'derived', 'reference_cell_lid', '参比池上次换水后的时间')
slot('loading_station.tube_solution_id', nullable(TEXT), '1', 'derived', 'titration_loading_station', 'Load位置管座中PCR管的溶液')
slot('loading_station.tube_volume_m3', number('m3'), 'm3', 'sensor', 'titration_loading_station', 'PCR管中剩余液体（目视）')
slot('wash.bottle_fraction', obj({b: number('1') for b in BOTTLES}), '1', 'sensor', 'wash_body', '各瓶液位（目视，0–1；废液瓶有刻度）')
slot('wash.pump_ok', BOOL, '1', 'derived', 'wash_body', '洗涤模块真空泵可用（溢流瓶满会停泵）')
slot('clean.state', enum(['idle', 'cleaning', 'completed', 'failed', 'stopped']), '1', 'derived', 'wash_body', '清洗状态')
slot('operations.running', array(TEXT, 0, 8), '1', 'derived', 'cell_body', '进行中的异步操作类型')
slot('operation.started_by_other_agent', BOOL, '1', 'derived', 'cell_body', '进行中的操作是否由另一个agent启动')
slot('warnings.active', array(TEXT, 0, 16), '1', 'derived', 'cell_body', '当前警告（AMBIENT_OUT_OF_RANGE、DP_OUT_OF_RANGE、WASTE_BOTTLE_FULL、BOTTLE_LOW_*等）')
slot('errors.active', array(TEXT, 0, 16), '1', 'derived', 'cell_body', '当前错误（WASH_PUMP_SHUTDOWN、NO_FLOW、SYRINGE_FILL_FAILURE等）')
slot('solutions.available', array(TEXT, 0, MAX_SOLUTIONS * 2), '1', 'derived', 'loading_syringe', '可取用的溶液与储备液')
slot('experiments.count', INT, '1', 'derived', 'cell_body', '实验数')
slot('truth.jacket_temperature_k', _PN('K'), 'K', 'process_truth', 'cell_body', '夹套温度真值', 'evaluator')
slot('truth.cell_content_temperature_k', _PN('K'), 'K', 'process_truth', 'cell_body', '样品池液体温度真值', 'evaluator')
slot('truth.reference_temperature_k', _PN('K'), 'K', 'process_truth', 'cell_body', '参比池液体温度真值', 'evaluator')
slot('truth.dp_noise_free_w', _PN('W'), 'W', 'process_truth', 'cell_body', '无噪声、未截断的DP真值', 'evaluator')
slot('truth.heat_rate_w', number('W'), 'W', 'process_truth', 'cell_body', '样品池有效体积内的放热速率（化学+机械空白热；放热为正）', 'evaluator')
slot('truth.cell_species_mol_m3', SPECMAP, 'mol/m3', 'process_truth', 'cell_body', '样品池有效体积内各物质总浓度真值（混合部分）', 'evaluator')
slot('truth.cell_ph', _PN('1'), '1', 'process_truth', 'cell_body', '样品池pH真值（无缓冲液时为空）', 'evaluator')
slot('truth.cell_bound_mol_m3', SPECMAP, 'mol/m3', 'process_truth', 'cell_body', '各相互作用的结合（或转化、二聚）浓度真值', 'evaluator')
slot('truth.unmixed_volume_m3', number('m3'), 'm3', 'process_truth', 'cell_body', '尚未混匀的注入液体积', 'evaluator')
slot('truth.syringe_liquid_m3', number('m3'), 'm3', 'process_truth', 'syringe_glass', '注射器中液体体积真值', 'evaluator')
slot('truth.syringe_bubble_m3', number('m3'), 'm3', 'process_truth', 'syringe_glass', '注射器中气泡体积真值', 'evaluator')
slot('truth.syringe_species_mol_m3', SPECMAP, 'mol/m3', 'process_truth', 'syringe_glass', '注射器液体中各物质浓度真值', 'evaluator')
slot('truth.syringe_residue', enum(['none', 'water', 'methanol']), '1', 'process_truth', 'syringe_glass', '注射器残留（清洗后未干燥为水，干燥中断为甲醇）', 'evaluator')
slot('truth.reference_volume_m3', number('m3'), 'm3', 'process_truth', 'cell_body', '参比池液体体积真值（蒸发）', 'evaluator')
slot('truth.cell_bubble', BOOL, '1', 'process_truth', 'cell_body', '样品池中有气泡（装样不足、注射空气或故障）', 'evaluator')
slot('truth.cell_contamination_fraction', number('1'), '1', 'process_truth', 'cell_body', '样品池中上一份液体（或清洗水）的残留体积分数', 'evaluator')
slot('truth.wash_volumes_m3', obj({b: number('m3') for b in BOTTLES}), 'm3', 'process_truth', 'wash_body', '各瓶液体体积真值', 'evaluator')
slot('truth.faults', array(TEXT, 0, 16), '1', 'process_truth', 'cell_body', '注入的故障', 'evaluator')


# ---------------------------------------------------------------- compositions
def _comp(V=0.0):
    """A liquid composition: volume (m3), solute moles, buffer moles, proton excess (mol)."""
    return dict(V=V, n={}, buf={}, th=0.0)


def _scaled(c, f):
    return dict(V=c['V'] * f, n={k: v * f for k, v in c['n'].items()}, buf={k: v * f for k, v in c['buf'].items()}, th=c['th'] * f)


def _add(a, b):
    out = dict(V=a['V'] + b['V'], n=dict(a['n']), buf=dict(a['buf']), th=a['th'] + b['th'])
    for k, v in b['n'].items():
        out['n'][k] = out['n'].get(k, 0.0) + v
    for k, v in b['buf'].items():
        out['buf'][k] = out['buf'].get(k, 0.0) + v
    return out


def _sub(a, b):
    """a - b for compositions (b a part of a); tiny negative rounding residues clipped to zero."""
    out = dict(V=max(0.0, a['V'] - b['V']), n=dict(a['n']), buf=dict(a['buf']), th=a['th'] - b['th'])
    for k, v in b['n'].items():
        out['n'][k] = max(0.0, out['n'].get(k, 0.0) - v)
    for k, v in b['buf'].items():
        out['buf'][k] = max(0.0, out['buf'].get(k, 0.0) - v)
    return out


def _conc(c):
    """Concentrations in mol/L."""
    L = c['V'] * 1000.0
    if L <= 0:
        return {}, {}, 0.0
    return {k: v / L for k, v in c['n'].items() if v > 0}, {k: v / L for k, v in c['buf'].items() if v > 0}, c['th'] / L


class MalvernMicrocalpeaqRuntime:
    def __init__(self, instance_id='peaq_itc_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.tick = 0
        self.plant = None
        self.seed = 0
        self.switch_on = False
        self.power = False
        self.wash_switch = False
        self.wash_power = False
        self.T_room = 295.15
        self.T_set = 298.15
        self.T_j = self.T_s = self.T_r = None
        self.method = None
        self.feedback = 'high'
        self.p_ref = W_UCAL(5.0)
        self.stir_hz = 0.0
        self.y = 0.0                               # DP response state (W of heat being compensated)
        self.drift = 0.0
        self.dp_true = None
        self.dp = None
        self.q_now = 0.0
        self.dp_clipped = False
        self.pip = dict(loc='cell', target=None, lift=0.0, swing=0.0, phase=None)
        self.plunger = 0.0
        self.plunger_target = None
        self.syr = dict(state='empty', liquid=_comp(), bubble=0.0, residue='none', tip_dilution=0.0, leak=0.0, fresh=False, E_density=0.0, methanol=0.0,
                        solution_id=None, air_m3=V_SYR)
        self.fpa = False
        self.tool_in = False
        self.cell = dict(solution_id='water', loaded=V_LOAD_MIN, mix=_comp(V0), E=0.0, eq=None, kin={}, residual=_comp(0.0), bubble=False, contamination=0.0,
                         degassed=True, T_eq=None, poor_load=False)
        self.pool = dict(c=_comp(0.0), E=0.0, methanol=0.0)
        self.ref = dict(V=ENG['reference_full_m3'], age=0.0, solution='water')
        self.tube = None                           # dict(solution_id, c (composition))
        self.wash = dict(detergent=0.0, methanol=0.0, water=0.0, waste=0.0, overflow=0.0, pump_ok=True)
        self.solutions = {}
        self.interactions = {}
        self.species = {k: dict(v) for k, v in SHELF.items()}
        self.experiments = {}
        self.run = None
        self.clean_state = 'idle'
        self.operations = {}
        self.warnings = {}
        self.errors = {}
        self.faults = set()
        self.led = {k: _Sum() for k in LEDGER_KEYS}
        self.events, self.events_dropped = [], 0
        self.revision = 0
        self.seq = dict(op=0, inj=0, prep=0)
        self._principals = {}
        self._requests = {}
        self._owners = {}
        self._caller = None
        self._steps_k = 0.0
        self._q_total = 0.0
        self._dp_ok_ticks = 0
        self._spike = 0.0
        self._plunger_speed = ENG['plunger_speed_m_s']
        self.T_meas = None
        self._load_library_interactions()

    def _load_library_interactions(self):
        for k, b in bindings().items():
            if b['model'] == 'one_set_of_sites':
                self.interactions[k] = dict(model='sites', receptor=b['receptor'], ligand=b['ligand'], n=b['n'], kd=b['kd_m'], dh=b['dh_j_mol'],
                                            dcp=b['dcp_j_k_mol'] or 0.0, T0=b['temperature_k'], n_h=0.0, kon=None, site='a', library=True, evidence=b['evidence'])
            elif b['model'] == 'enzyme_kinetics':
                self.interactions[k] = dict(model='enzyme', receptor=b['receptor'], ligand=b['ligand'], km=b['km_m'], kcat=b['kcat_s'], dh=b['dh_j_mol'],
                                            dcp=b['dcp_j_k_mol'] or 0.0, T0=b['temperature_k'], n_h=0.0, kon=None, site='a', library=True, evidence=b['evidence'])

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
        return MalvernMicrocalpeaqSession(self, name)

    def _event(self, kind, code, message, ref=None):
        if len(self.events) >= MAX_EVENTS_LOG:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.t, kind=kind, code=code, message_zh=message, _ref=ref))

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

    def _mains_ok(self):
        v = self.plant['mains_voltage_v']
        return MAINS[0] * 0.9 <= v <= MAINS[1] * 1.1 and 'power_loss' not in self.faults

    # ---------------------------------------------------------------- operations
    def _new_op(self, kind, **extra):
        self.seq['op'] += 1
        oid = f'op{self.seq["op"]}'
        self.operations[oid] = dict(op_id=oid, kind=kind, phase='running', t0=self.t, error=None, result={}, **extra)
        self._own(oid)
        return self.operations[oid]

    def _running_ops(self, *kinds):
        return [op for op in self.operations.values() if op['phase'] == 'running' and (not kinds or op['kind'] in kinds)]

    def _finish_op(self, op, phase, code=None):
        op['phase'] = phase
        op['error'] = code
        op['t1'] = self.t

    def _cancel_op(self, op):
        if op['kind'] == 'run.start':
            self._end_run('stopped', None)
        elif op['kind'] == 'clean.start':
            self._end_clean(op, 'stopped', None)
        elif op['kind'] in ('syringe.load', 'syringe.purge_refill'):
            self._end_syringe_op(op, 'cancelled', None)
        if op['phase'] == 'running':
            self._finish_op(op, 'cancelled')

    # ---------------------------------------------------------------- plant and power
    def _cmd_plant_configure(self, room_k, mains_voltage_v, noise_seed, room_drift_k_per_s=0.0, reference_age_s=0.0):
        if self.plant is not None and noise_seed != self.plant['noise_seed']:
            raise ActionRejected('SEED_FIXED')
        first = self.plant is None
        self.plant = dict(room_k=float(room_k), mains_voltage_v=float(mains_voltage_v), noise_seed=int(noise_seed), room_drift_k_per_s=float(room_drift_k_per_s),
                          reference_age_s=float(reference_age_s))
        self.seed = int(noise_seed)
        self.T_room = float(room_k)
        if first:
            self.T_j = self.T_s = self.T_r = float(room_k)
            cap_ = ENG['bottle_capacity_m3']
            self.wash.update(detergent=cap_['detergent'], methanol=cap_['methanol'], water=cap_['water'], waste=0.0, overflow=0.0, pump_ok=True)
            self.ref['age'] = float(reference_age_s)
            self.ref['V'] = max(0.0, ENG['reference_full_m3'] - ENG['reference_evaporation_m3_per_day'] * reference_age_s / 86400.0)
        self._update_power()
        self._event('power', 'PLANT', f'实验室条件：室温{room_k - 273.15:.1f} °C，供电{mains_voltage_v:.0f} V')
        return dict(plant=dict(self.plant))

    def _update_power(self):
        was, wwas = self.power, self.wash_power
        ok = self.plant is not None and self._mains_ok()
        self.power = bool(ok and self.switch_on)
        self.wash_power = bool(ok and self.wash_switch)
        if was and not self.power:
            self._power_lost('cell')
        if wwas and not self.wash_power:
            self._power_lost('wash')
        if self.power and not was:
            self._event('power', 'POWER_ON', '量热池单元通电')
        if self.wash_power and not wwas:
            self._event('wash', 'WASH_POWER_ON', '洗涤模块通电')

    def _power_lost(self, unit):
        for op in self._running_ops():
            if unit == 'cell' or op['kind'] in ('clean.start', 'syringe.load', 'syringe.purge_refill'):
                if op['kind'] == 'run.start':
                    self._end_run('failed', 'POWER_OFF_INTERRUPT')
                elif op['kind'] == 'clean.start':
                    self._end_clean(op, 'failed', 'POWER_OFF_INTERRUPT')
                else:
                    self._end_syringe_op(op, 'failed', 'POWER_OFF_INTERRUPT')
        if unit == 'cell':
            self.stir_hz = 0.0
            self.plunger_target = None
            self._event('power', 'POWER_OFF', '量热池单元断电')
        else:
            self._event('wash', 'WASH_POWER_OFF', '洗涤模块断电')

    def _cmd_power_set(self, enabled):
        self._need_plant()
        if enabled and not self._mains_ok():
            raise ActionRejected('MAINS_OUT_OF_RANGE')
        self.switch_on = bool(enabled)
        self._update_power()
        return dict(enabled=self.power)

    def _cmd_power_press_switch(self):
        return self._cmd_power_set(not self.switch_on)

    def _cmd_wash_power_set(self, enabled):
        self._need_plant()
        if enabled and not self._mains_ok():
            raise ActionRejected('MAINS_OUT_OF_RANGE')
        self.wash_switch = bool(enabled)
        self._update_power()
        return dict(enabled=self.wash_power)

    def _cmd_wash_press_switch(self):
        return self._cmd_wash_power_set(not self.wash_switch)

    # ---------------------------------------------------------------- method and temperature
    def _need_idle_run(self):
        if self.run is not None:
            raise ActionRejected('RUN_IN_PROGRESS')

    def _cmd_method_set(self, temperature_k, reference_power_w, feedback, stir_rate_hz, initial_delay_s, injections, cell_concentration_mol_m3,
                        syringe_concentration_mol_m3, comment=''):
        self._need_power()
        self._need_idle_run()
        tot = sum(i['volume_m3'] for i in injections)
        if tot > V_SYR - ENG['syringe_residual_m3'] + 1e-15:
            raise ActionRejected('METHOD_EXCEEDS_SYRINGE')
        if any(i['spacing_s'] < i['duration_s'] for i in injections):
            raise ActionRejected('INVALID_SPACING')
        self.method = dict(temperature_k=float(temperature_k), reference_power_w=float(reference_power_w), feedback=feedback, stir_rate_hz=float(stir_rate_hz),
                           initial_delay_s=float(initial_delay_s), injections=copy.deepcopy(injections), cell_concentration_mol_m3=float(cell_concentration_mol_m3),
                           syringe_concentration_mol_m3=float(syringe_concentration_mol_m3), comment=comment)
        self.T_set = float(temperature_k)
        self.p_ref = float(reference_power_w)
        self.feedback = feedback
        dur = initial_delay_s + sum(i['spacing_s'] for i in injections)
        self._event('method', 'METHOD_SET', f'方法：{temperature_k - 273.15:.2f} °C，{len(injections)}针，共{tot * 1e9:.2f} µL')
        return dict(injections=len(injections), total_volume_m3=float(tot), duration_s=float(dur))

    def _cmd_temperature_set(self, temperature_k):
        self._need_power()
        self._need_idle_run()
        self.T_set = float(temperature_k)
        if self.method is not None:
            self.method['temperature_k'] = float(temperature_k)
        self._event('temperature', 'SET', f'目标温度{temperature_k - 273.15:.2f} °C')
        return dict(setpoint_k=self.T_set)

    # ---------------------------------------------------------------- solutions
    def _source(self, sid):
        s = self.solutions.get(sid)
        if s is None:
            raise ActionRejected('UNKNOWN_SOLUTION')
        return s

    def _buffer_comp(self, recipe, V, exact=False, key=None):
        """Fresh buffer of a recipe (pH set with NaOH / HCl with a preparation error unless exact)."""
        c = _comp(V)
        conc = recipe['concentration_mol_m3'] / 1000.0
        nacl = recipe.get('nacl_mol_m3', 0.0) / 1000.0
        ph = recipe['ph']
        if not exact:
            self.seq['prep'] += 1
            ph += ENG['ph_preparation_error'] * _gauss(self.seed, _h(key or self.seq['prep']), 11)
        if conc > 0:
            th = proton_excess_for_ph({recipe['buffer_id']: conc}, ph, nacl, 298.15)
            c['buf'][recipe['buffer_id']] = conc * V * 1000
            c['th'] = th * V * 1000
        else:
            c['th'] = (10 ** -ph - 10 ** (ph - 14)) * V * 1000
        if nacl > 0:
            c['n']['nacl'] = nacl * V * 1000
        return c

    def _new_solution(self, sid, comp, label, ph_nominal, owner_tag, buffer_key, degassed=False, stock=False, extra=None):
        s = dict(solution_id=sid, c=comp, label=dict(label), ph_nominal=ph_nominal, buffer_key=buffer_key, degassed_t=self.t if degassed else None, stock=stock,
                 created_s=self.t)
        if extra:
            s.update(extra)
        self.solutions[sid] = s
        return s

    def _cmd_solution_prepare(self, solution_id, volume_m3, buffer=None, buffer_from=None, solutes=(), sources=()):
        self._need_plant()
        if solution_id in self.solutions or solution_id == WATER_ID:
            raise ActionRejected('ITEM_EXISTS')
        if sum(1 for s in self.solutions.values() if not s['stock']) >= MAX_SOLUTIONS:
            raise ActionRejected('TOO_MANY_ITEMS')
        if buffer is not None and buffer_from is not None:
            raise ActionRejected('INVALID_ARGUMENT')
        vs = sum(s['volume_m3'] for s in sources)
        if vs > volume_m3 + 1e-15:
            raise ActionRejected('VOLUME_OUT_OF_RANGE')
        comp = _comp(0.0)
        label = {}
        for s in sources:
            src = self._source(s['source_id'])
            if s['volume_m3'] > src['c']['V'] + 1e-15:
                raise ActionRejected('INSUFFICIENT_SOLUTION')
        for s in solutes:
            if s['species_id'] not in self.species or self.species[s['species_id']]['kind'] == 'buffer':
                raise ActionRejected('UNKNOWN_SPECIES')
        rest = volume_m3 - vs
        bkey = None
        ph_nom = None
        if buffer_from is not None:
            bs = self._source(buffer_from)
            base = bs['c']
            if base['V'] <= 0:
                raise ActionRejected('INSUFFICIENT_SOLUTION')
            dil = _comp(rest)
            f = rest / base['V']
            dil['buf'] = {k: v * f for k, v in base['buf'].items()}
            dil['th'] = base['th'] * f
            for k, v in base['n'].items():
                if self.species.get(k, {}).get('kind') == 'salt':
                    dil['n'][k] = v * f
            bkey, ph_nom = bs['buffer_key'], bs['ph_nominal']
        elif buffer is not None:
            dil = self._buffer_comp(buffer, rest, key=f'{solution_id}:buffer')
            bkey, ph_nom = f'{solution_id}:buffer', buffer['ph']
        else:
            dil = _comp(rest)
            bkey = 'water'
        for s in sources:
            src = self._source(s['source_id'])
            part = _scaled(src['c'], s['volume_m3'] / src['c']['V'])
            src['c'] = _scaled(src['c'], 1 - s['volume_m3'] / src['c']['V'])
            comp = _add(comp, part)
            for k, v in src['label'].items():
                label[k] = label.get(k, 0.0) + v * s['volume_m3'] / volume_m3
            if bkey == 'water' and len(sources) == 1 and not solutes:
                bkey, ph_nom = src['buffer_key'], src['ph_nominal']
        comp = _add(comp, dil)
        for s in solutes:
            err = 1 + ENG['weighing_error_rel'] * _gauss(self.seed, _h(f'{solution_id}:{s["species_id"]}'), 12)
            comp['n'][s['species_id']] = comp['n'].get(s['species_id'], 0.0) + s['concentration_mol_m3'] * err * volume_m3
            label[s['species_id']] = label.get(s['species_id'], 0.0) + s['concentration_mol_m3']
        comp['V'] = float(volume_m3)
        self._new_solution(solution_id, comp, label, ph_nom, self._caller, bkey)
        self._own('solution:' + solution_id)
        self._event('solution', 'PREPARED', f'配好溶液{solution_id}（{volume_m3 * 1e6:.3f} mL）', 'solution:' + solution_id)
        return dict(solution_id=solution_id, volume_m3=float(volume_m3), ph_nominal=ph_nom,
                    components=[dict(species_id=k, nominal_concentration_mol_m3=float(v)) for k, v in sorted(label.items())])

    def _cmd_solution_degas(self, solution_id):
        self._need_plant()
        s = self._source(solution_id)
        s['degassed_t'] = self.t
        return dict(solution_id=solution_id, degassed=True)

    def _cmd_solution_dialyze(self, solution_id, buffer, dialysate_id, dialysate_volume_m3=5.0e-5):
        self._need_plant()
        s = self._source(solution_id)
        if dialysate_id in self.solutions or dialysate_id == WATER_ID:
            raise ActionRejected('ITEM_EXISTS')
        if sum(1 for x in self.solutions.values() if not x['stock']) >= MAX_SOLUTIONS:
            raise ActionRejected('TOO_MANY_ITEMS')
        key = f'{dialysate_id}:buffer'
        V = s['c']['V']
        fresh = self._buffer_comp(buffer, V, key=key)
        new = _comp(V)
        new['buf'], new['th'] = dict(fresh['buf']), fresh['th']
        new['n'] = {k: v for k, v in fresh['n'].items()}
        for k, v in s['c']['n'].items():
            if self.species.get(k, {}).get('macromolecule'):
                new['n'][k] = new['n'].get(k, 0.0) + v
        s['c'] = new
        s['label'] = {k: v for k, v in s['label'].items() if self.species.get(k, {}).get('macromolecule')}
        s['buffer_key'], s['ph_nominal'] = key, buffer['ph']
        dial = _scaled(fresh, dialysate_volume_m3 / V) if V > 0 else self._buffer_comp(buffer, dialysate_volume_m3, key=key)
        self._new_solution(dialysate_id, dial, {}, buffer['ph'], self._caller, key)
        self._own('solution:' + dialysate_id)
        self._event('solution', 'DIALYZED', f'{solution_id}已对{buffer["buffer_id"]}透析，外液存为{dialysate_id}', 'solution:' + solution_id)
        return dict(solution_id=solution_id, dialysate_id=dialysate_id, ph_nominal=float(buffer['ph']))

    def _cmd_stock_provide(self, stock_id, volume_m3, buffer, components, label=None, macromolecules=(), degassed=False):
        self._need_plant()
        if stock_id in self.solutions or stock_id == WATER_ID:
            raise ActionRejected('ITEM_EXISTS')
        if sum(1 for s in self.solutions.values() if s['stock']) >= MAX_SOLUTIONS:
            raise ActionRejected('TOO_MANY_ITEMS')
        for m_ in macromolecules:
            self.species.setdefault(m_, dict(kind='solute', name=m_, ionic=0.0, macromolecule=True, source='evaluator'))
            self.species[m_]['macromolecule'] = True
        for c in components:
            if self.species.get(c['species_id'], {}).get('kind') == 'buffer':
                raise ActionRejected('UNKNOWN_SPECIES')
            self.species.setdefault(c['species_id'], dict(kind='solute', name=c['species_id'], ionic=0.0, macromolecule=False, source='evaluator'))
        comp = self._buffer_comp(buffer, volume_m3, exact=True) if buffer is not None else _comp(volume_m3)
        for c in components:
            comp['n'][c['species_id']] = comp['n'].get(c['species_id'], 0.0) + c['concentration_mol_m3'] * volume_m3
        lab = {c['species_id']: c['concentration_mol_m3'] for c in (label if label is not None else components)}
        self._new_solution(stock_id, comp, lab, buffer['ph'] if buffer else None, None, f'{stock_id}:buffer', degassed=degassed, stock=True)
        return dict(stock_id=stock_id, volume_m3=float(volume_m3))

    def _cmd_interaction_define(self, interaction_id, model, receptor, ligand=None, n=1.0, kd_mol_m3=None, dh_j_mol=0.0, dcp_j_k_mol=0.0, temperature_k=298.15,
                                n_h=0.0, kon_m3_mol_s=None, site='a', ks_mol_m3=None, dhs_j_mol=None, km_mol_m3=None, kcat_s=None):
        self._need_plant()
        if interaction_id in self.interactions:
            raise ActionRejected('ITEM_EXISTS')
        if len(self.interactions) >= 64:
            raise ActionRejected('TOO_MANY_ITEMS')
        need = dict(sites=(ligand is not None and kd_mol_m3 is not None), sequential=(ligand is not None and ks_mol_m3 is not None and dhs_j_mol is not None
                                                                                     and len(ks_mol_m3) == len(dhs_j_mol)),
                    dimer=kd_mol_m3 is not None, enzyme=(ligand is not None and km_mol_m3 is not None and kcat_s is not None))[model]
        if not need:
            raise ActionRejected('INVALID_INTERACTION')
        for sp in (receptor, ligand):
            if sp is not None:
                if self.species.get(sp, {}).get('kind') == 'buffer':
                    raise ActionRejected('INVALID_INTERACTION')
                self.species.setdefault(sp, dict(kind='solute', name=sp, ionic=0.0, macromolecule=False, source='evaluator'))
        self.interactions[interaction_id] = dict(model=model, receptor=receptor, ligand=ligand, n=float(n), kd=(kd_mol_m3 / 1000.0) if kd_mol_m3 else None,
                                                 dh=float(dh_j_mol), dcp=float(dcp_j_k_mol), T0=float(temperature_k), n_h=float(n_h),
                                                 kon=(kon_m3_mol_s * 1000.0) if kon_m3_mol_s else None, site=site,
                                                 ks=[k / 1000.0 for k in ks_mol_m3] if ks_mol_m3 else None, dhs=list(dhs_j_mol) if dhs_j_mol else None,
                                                 km=(km_mol_m3 / 1000.0) if km_mol_m3 else None, kcat=kcat_s, library=False, evidence='evaluator')
        return dict(interaction_id=interaction_id)

    # ---------------------------------------------------------------- chemistry (enthalpy content of a composition)
    def _salt_i(self, cn):
        return sum(c * self.species.get(k, {}).get('ionic', 0.0) for k, c in cn.items())

    def _energy(self, comp, T, kin=None, dt=0.0, logh0=None, apply_enzyme=False, pure_methanol=False):
        """Enthalpy content (J) of a composition at T relative to the unbound, fully deprotonated, infinitely dilute components: binding networks,
        sequential sites, dimers, enzyme products, virial dilution, dissolved methanol, buffer protonation and Debye-Hueckel excess enthalpy.
        kin: previous bound concentrations (mol/L) of kinetic interactions (relaxed over dt).  apply_enzyme converts substrate over dt (in place).
        Returns (E_J, info)."""
        L = comp['V'] * 1000.0
        if L <= 0:
            return 0.0, dict(bound={}, logh=None, ph=None, I=0.0, kin={}, kin_eq={})
        cn, cb, th = _conc(comp)
        E = 0.0
        bound, kin_new, kin_eq, h_rel = {}, {}, {}, 0.0
        present = {k for k, c in cn.items() if c > 1e-20}
        # enzyme kinetics (Michaelis-Menten, integrated over dt)
        if apply_enzyme and dt > 0:
            for iid, it in self.interactions.items():
                if it['model'] != 'enzyme' or it['receptor'] not in present or it['ligand'] not in present:
                    continue
                vmax = it['kcat'] * cn[it['receptor']]
                S0 = cn[it['ligand']]
                S = S0
                for _ in range(30):                                   # K_M ln(S0 / S) + (S0 - S) = vmax dt
                    g = it['km'] * math.log(S0 / S) + (S0 - S) - vmax * dt
                    dg = -it['km'] / S - 1.0
                    Sn = S - g / dg
                    S = min(S0, max(Sn, S0 * 1e-12))
                    if abs(g) < 1e-16:
                        break
                conv = (S0 - S) * L
                if conv > 0:
                    comp['n'][it['ligand']] -= conv
                    pk = 'product:' + iid
                    comp['n'][pk] = comp['n'].get(pk, 0.0) + conv
                    cn[it['ligand']] = S
                    cn[pk] = cn.get(pk, 0.0) + conv / L
        for k, c in cn.items():
            if k.startswith('product:'):
                it = self.interactions.get(k[8:])
                if it is not None:
                    E += c * (it['dh'] + it['dcp'] * (T - it['T0']))
                    bound[k[8:]] = c
        # site networks (competition among ligands for the same (receptor, site))
        classes, ligs = {}, {}
        for iid, it in self.interactions.items():
            if it['model'] != 'sites' or it['receptor'] not in present or it['ligand'] not in present:
                continue
            ka, dhT = _k_at(it['kd'], it['dh'], it['dcp'], it['T0'], T)
            key = (it['receptor'], it['site'])
            c = classes.setdefault(key, dict(sites=it['n'] * cn[it['receptor']], K={}, iids={}))
            c['K'][it['ligand']] = ka
            c['iids'][it['ligand']] = (iid, dhT, it)
            ligs[it['ligand']] = cn[it['ligand']]
        if classes:
            cl = list(classes.values())
            if len(ligs) == 1 and len(cl) == 1 and len(cl[0]['K']) == 1:
                lg = next(iter(ligs))
                S, Lt, kd = cl[0]['sites'], ligs[lg], 1.0 / cl[0]['K'][lg]
                b = S + Lt + kd
                x = 2 * S * Lt / (b + math.sqrt(max(b * b - 4 * S * Lt, 0.0)))
                free = {lg: Lt - x}
            elif len(ligs) == 1:
                lg = next(iter(ligs))
                Lt = ligs[lg]
                lo, hi = math.log(Lt) - 70.0, math.log(Lt)
                for _ in range(120):
                    mid = 0.5 * (lo + hi)
                    Lf = math.exp(mid)
                    tot = Lf + sum(c['sites'] * c['K'][lg] * Lf / (1 + c['K'][lg] * Lf) for c in cl)
                    if tot > Lt:
                        hi = mid
                    else:
                        lo = mid
                    if hi - lo < 1e-13:
                        break
                free = {lg: math.exp(0.5 * (lo + hi))}
            elif len(cl) == 1:
                free = _competing_free(ligs, cl[0])
            else:
                free = _solve_free(ligs, cl)
            for c in cl:
                den = 1 + sum(c['K'][l] * free.get(l, 0.0) for l in c['K'])
                for lg, (iid, dhT, it) in c['iids'].items():
                    eq = c['sites'] * c['K'][lg] * free.get(lg, 0.0) / den
                    b = eq
                    if it['kon'] is not None and kin is not None and iid in kin:
                        r_free = c['sites'] / den
                        kobs = it['kon'] * (free.get(lg, 0.0) + r_free) + it['kon'] / c['K'][lg]
                        b = eq + (kin[iid] - eq) * math.exp(-kobs * dt)
                        kin_new[iid] = b
                        kin_eq[iid] = eq
                    elif it['kon'] is not None:
                        kin_new[iid] = eq
                        kin_eq[iid] = eq
                    bound[iid] = b
                    E += b * dhT
                    h_rel += b * it['n_h']
        # sequential sites
        for iid, it in self.interactions.items():
            if it['model'] != 'sequential' or it['receptor'] not in present or it['ligand'] not in present:
                continue
            ks = [_k_at(kd, 0.0, 0.0, it['T0'], T)[0] for kd in it['ks']]
            Lf, fr = _sequential_bound(cn[it['ligand']], cn[it['receptor']], ks)
            cum = 0.0
            for i, f in enumerate(fr[1:], start=1):
                cum += it['dhs'][i - 1]
                E += cn[it['receptor']] * f * cum
            bound[iid] = cn[it['receptor']] * sum(i * f for i, f in enumerate(fr))
        # dimer dissociation (kd = dissociation constant of the dimer, dh = dissociation enthalpy)
        for iid, it in self.interactions.items():
            if it['model'] != 'dimer' or it['receptor'] not in present:
                continue
            ka, dha = _k_at(it['kd'], -it['dh'], -it['dcp'], it['T0'], T)      # association D <- 2 M
            kdT = 1.0 / ka
            Mt = cn[it['receptor']]
            M = (-1 + math.sqrt(1 + 8 * Mt / kdT)) * kdT / 4
            D = (Mt - M) / 2
            bound[iid] = D
            E += D * dha
        # virial dilution and dissolved methanol
        for k, c in cn.items():
            pr = _dilution_params(k, T)
            if pr is not None:
                E += pr[0] * c * c + pr[1] * c * c * c
            elif k == 'methanol' and not pure_methanol:
                E += c * METHANOL_DH
        # protons (buffers, water, protons released by binding) with the Debye-Hueckel excess enthalpy of all ions
        sp = _speciate(cb, th + h_rel, self._salt_i(cn), T, logh0)
        E += sp['enthalpy']
        return E * L, dict(bound=bound, logh=sp['logh'], ph=sp['ph'] if (cb or abs(th) > 1e-12) else None, I=sp['I'], kin=kin_new, kin_eq=kin_eq)

    # ---------------------------------------------------------------- cell, reference, adapters, pipette, tube
    def _pipette_away(self):
        """Pipette lowered at Rest / Load / Clean (not in the cell, not moving)."""
        return self.pip['phase'] is None and self.pip['loc'] != 'cell'

    def _seated(self):
        return self.pip['phase'] is None and self.pip['loc'] == 'cell' and self.pip['lift'] <= 1e-9

    def _need_not_busy(self):
        if self.run is not None:
            raise ActionRejected('RUN_IN_PROGRESS')
        if self._running_ops('clean.start', 'syringe.load', 'syringe.purge_refill'):
            raise ActionRejected('INSTRUMENT_BUSY')

    def _empty_cell(self, to_waste=False):
        c = self.cell
        if c['mix']['V'] > 0:
            vm = c['mix']['V'] + self.pool['c']['V']
            whole = _add(c['mix'], self.pool['c'])
            res = _scaled(whole, ENG['cell_residual_m3'] / vm) if vm > 0 else _comp(0.0)
            removed = max(0.0, c['loaded'] - ENG['cell_residual_m3'])
            if to_waste:
                self._to_waste(removed)
            c.update(mix=_comp(0.0), E=0.0, residual=res, solution_id=None, loaded=0.0, kin={}, eq=None, bubble=False, poor_load=False, air=0.0)
            self.pool = dict(c=_comp(0.0), E=0.0, methanol=0.0)
            return removed
        return 0.0

    def _cmd_cell_load(self, solution_id, volume_m3, rinse=False):
        self._need_plant()
        self._need_not_busy()
        if self.tool_in:
            raise ActionRejected('CLEANING_TOOL_INSERTED')
        if not self._pipette_away():
            raise ActionRejected('PIPETTE_IN_CELL')
        s = self._source(solution_id)
        need = volume_m3 + (ENG['rinse_volume_m3'] if rinse else 0.0)
        if s['c']['V'] + 1e-15 < need:
            raise ActionRejected('INSUFFICIENT_SOLUTION')
        self._empty_cell()
        res = self.cell['residual']
        frac_new = 1.0 - ENG['rinse_residual_factor'] if rinse else 0.0
        if rinse:
            s_c = s['c']
            portion = _scaled(s_c, ENG['cell_residual_m3'] * frac_new / s_c['V'])
            res = _add(_scaled(res, ENG['rinse_residual_factor']), portion)
            res['V'] = ENG['cell_residual_m3']
            s['c'] = _scaled(s_c, 1 - ENG['rinse_volume_m3'] / s_c['V'])
            self.led['sample_rinse_m3'].add(ENG['rinse_volume_m3'])
            self._to_waste(ENG['rinse_volume_m3'])
        s_c = s['c']
        fill = min(volume_m3, V0 - res['V'])
        mix = _add(res, _scaled(s_c, fill / s_c['V']))
        s['c'] = _scaled(s_c, 1 - volume_m3 / s_c['V'])
        warns = []
        bubble = False
        if volume_m3 < V0:
            bubble = True
            warns.append('UNDERFILLED')
        elif volume_m3 < V_LOAD_MIN:
            p = (V_LOAD_MIN - volume_m3) / 2.0e-8
            bubble = _uni(self.seed, _h(f'load:{solution_id}:{self.t:.1f}'), 21) < p
            warns.append('BELOW_MINIMUM_LOAD')
        degassed = s['degassed_t'] is not None and (self.t - s['degassed_t']) < ENG['degassed_hold_s']
        if not degassed:
            warns.append('NOT_DEGASSED')
        contamination = ENG['cell_residual_m3'] * (ENG['rinse_residual_factor'] if rinse else 1.0) / V0
        self.T_s = self.T_room
        E, info = self._energy(mix, self.T_s)
        self.cell.update(solution_id=solution_id, loaded=float(volume_m3), mix=mix, E=E, eq=info, kin=dict(info['kin']), residual=_comp(0.0), bubble=bubble,
                         contamination=contamination, degassed=degassed, T_eq=self.T_s, poor_load=bubble)
        self.led['sample_loaded_m3'].add(volume_m3)
        self._own('cell')
        self._event('cell', 'LOADED', f'样品池装入{solution_id} {volume_m3 * 1e9:.0f} µL' + (f'（{", ".join(warns)}）' if warns else ''), 'solution:' + solution_id)
        return dict(solution_id=solution_id, loaded_m3=float(volume_m3), warnings=warns)

    def _cmd_cell_empty(self):
        self._need_plant()
        self._need_not_busy()
        if self.tool_in:
            raise ActionRejected('CLEANING_TOOL_INSERTED')
        if not self._pipette_away():
            raise ActionRejected('PIPETTE_IN_CELL')
        removed = self._empty_cell(to_waste=False)
        self._event('cell', 'EMPTIED', f'吸出样品池液体{removed * 1e9:.0f} µL')
        return dict(removed_m3=float(removed))

    def _cmd_reference_refill(self, solution_id=None):
        self._need_plant()
        if self.run is not None:
            raise ActionRejected('RUN_IN_PROGRESS')
        if solution_id is not None:
            s = self._source(solution_id)
            if s['c']['V'] + 1e-15 < ENG['reference_full_m3'] + 2e-8:
                raise ActionRejected('INSUFFICIENT_SOLUTION')
            s['c'] = _scaled(s['c'], 1 - (ENG['reference_full_m3'] + 2e-8) / s['c']['V'])
        self.ref.update(V=ENG['reference_full_m3'], age=0.0, solution=solution_id or 'water')
        self.T_r = self.T_room
        self.faults.discard('reference_cell_empty')
        self.led['reference_water_m3'].add(ENG['reference_full_m3'])
        self._event('cell', 'REFERENCE_REFILLED', '参比池已换液')
        return dict(volume_m3=float(self.ref['V']), solution=self.ref['solution'])

    def _cmd_fpa_attach(self):
        self._need_plant()
        if self.run is not None:
            raise ActionRejected('RUN_IN_PROGRESS')
        if not self._pipette_away():
            raise ActionRejected('PIPETTE_IN_CELL')
        if self.fpa:
            raise ActionRejected('FPA_ATTACHED')
        self.fpa = True
        return dict(attached=True)

    def _cmd_fpa_detach(self):
        self._need_plant()
        if self._running_ops('clean.start', 'syringe.load', 'syringe.purge_refill'):
            raise ActionRejected('INSTRUMENT_BUSY')
        if not self._pipette_away():
            raise ActionRejected('PIPETTE_IN_CELL')
        if not self.fpa:
            raise ActionRejected('FPA_NOT_ATTACHED')
        self.fpa = False
        return dict(attached=False)

    def _cmd_pipette_move(self, location):
        self._need_plant()
        self._need_not_busy()
        if self.pip['phase'] is not None:
            raise ActionRejected('PIPETTE_MOVING')
        if location == 'cell':
            if self.fpa:
                raise ActionRejected('FPA_ATTACHED')
            if self.tool_in:
                raise ActionRejected('CLEANING_TOOL_INSERTED')
        if location == self.pip['loc'] and self.pip['lift'] <= 1e-9:
            return dict(location=location, travel_s=0.0)
        if self.plunger_target is not None:
            raise ActionRejected('SYRINGE_BUSY')
        travel = (LIFT_TRAVEL - self.pip['lift']) / ENG['lift_speed_m_s'] + abs(LOCATION_Q[location] - self.pip['swing']) / ENG['swing_speed_rad_s'] \
            + LIFT_TRAVEL / ENG['lift_speed_m_s']
        self.pip.update(target=location, phase='lift', loc_from=self.pip['loc'])
        self.pip['loc'] = 'moving'
        self._event('pipette', 'MOVE', f'移液器移到{location}')
        return dict(location=location, travel_s=float(travel))

    def _tip_in_tube(self):
        return self.pip['phase'] is None and self.pip['loc'] == 'load' and self.tube is not None

    def _cmd_tube_place(self, solution_id, volume_m3):
        self._need_plant()
        if self.tube is not None:
            raise ActionRejected('TUBE_PRESENT')
        if self.pip['loc'] in ('load', 'moving') and (self.pip['phase'] is None or self.pip.get('target') == 'load'):
            raise ActionRejected('PIPETTE_IN_STATION')
        s = self._source(solution_id)
        if s['c']['V'] + 1e-15 < volume_m3:
            raise ActionRejected('INSUFFICIENT_SOLUTION')
        part = _scaled(s['c'], volume_m3 / s['c']['V'])
        s['c'] = _scaled(s['c'], 1 - volume_m3 / s['c']['V'])
        self.tube = dict(solution_id=solution_id, c=part)
        self._own('tube')
        return dict(solution_id=solution_id, volume_m3=float(volume_m3))

    def _cmd_tube_remove(self):
        self._need_plant()
        if self.tube is None:
            raise ActionRejected('NO_TUBE')
        if self.pip['loc'] == 'load' or (self.pip['phase'] is not None and self.pip.get('target') == 'load'):
            raise ActionRejected('PIPETTE_IN_STATION')
        t = self.tube
        self.tube = None
        return dict(solution_id=t['solution_id'], volume_m3=float(t['c']['V']))

    def _cmd_cleaning_tool_insert(self):
        self._need_plant()
        self._need_not_busy()
        if not self._pipette_away():
            raise ActionRejected('PIPETTE_IN_CELL')
        if self.tool_in:
            raise ActionRejected('CLEANING_TOOL_INSERTED')
        self.tool_in = True
        return dict(inserted=True)

    def _cmd_cleaning_tool_remove(self):
        self._need_plant()
        if self._running_ops('clean.start'):
            raise ActionRejected('INSTRUMENT_BUSY')
        if not self.tool_in:
            raise ActionRejected('CLEANING_TOOL_NOT_INSERTED')
        self.tool_in = False
        return dict(inserted=False)

    # ---------------------------------------------------------------- syringe
    def _tip_destination(self):
        if self._seated():
            return 'cell'
        if self.pip['phase'] is None and self.pip['loc'] == 'load' and self.tube is not None:
            return 'tube'
        if self.pip['phase'] is None and self.pip['loc'] == 'clean':
            return 'waste'
        return 'spill'

    def _cmd_syringe_plunger_down(self):
        self._need_power()
        self._need_not_busy()
        if self.pip['phase'] is not None:
            raise ActionRejected('PIPETTE_MOVING')
        if self.plunger >= PLUNGER_STROKE - 1e-9:
            return dict(position_m=float(self.plunger))
        self.plunger_target = PLUNGER_STROKE
        self._plunger_speed = ENG['plunger_speed_m_s']
        self.syr['state'] = 'expelling'
        return dict(position_m=float(self.plunger))

    def _cmd_syringe_load(self):
        self._need_power()
        self._need_not_busy()
        if not self.wash_power:
            raise ActionRejected('WASH_MODULE_OFF')
        if not self.wash['pump_ok']:
            raise ActionRejected('WASH_PUMP_SHUTDOWN')
        if self.plunger < PLUNGER_STROKE - 1e-6 or self.plunger_target is not None:
            raise ActionRejected('PLUNGER_NOT_DOWN')
        if not (self.pip['phase'] is None and self.pip['loc'] == 'load'):
            raise ActionRejected('PIPETTE_NOT_AT_LOAD')
        if not self.fpa:
            raise ActionRejected('FPA_NOT_ATTACHED')
        if self.tube is None:
            raise ActionRejected('NO_TUBE')
        op = self._new_op('syringe.load', solution_id=self.tube['solution_id'], drawn=_comp(0.0), deficit=0.0, dur=ENG['syringe_load_s'])
        self.syr['state'] = 'filling'
        self.plunger_target = 0.0
        self._plunger_speed = PLUNGER_STROKE / ENG['syringe_load_s']
        self._event('syringe', 'LOAD', '注射器开始装滴定剂', op['op_id'])
        return dict(operation_id=op['op_id'])

    def _cmd_syringe_purge_refill(self):
        self._need_power()
        self._need_not_busy()
        if not self._tip_in_tube():
            raise ActionRejected('PIPETTE_NOT_AT_LOAD')
        if self.syr['state'] != 'loaded':
            raise ActionRejected('SYRINGE_NOT_LOADED')
        op = self._new_op('syringe.purge_refill', dur=ENG['purge_refill_s'])
        self.syr['state'] = 'purging'
        self.plunger_target = PLUNGER_STROKE
        self._plunger_speed = PLUNGER_STROKE / (ENG['purge_refill_s'] / 2)
        return dict(operation_id=op['op_id'])

    def _syringe_fill_from_tube(self, op, vol):
        """Draw vol of titrant from the PCR tube into the syringe (tube runs dry -> air)."""
        tb = self.tube
        take = min(vol, tb['c']['V']) if tb is not None else 0.0
        if take > 0:
            part = _scaled(tb['c'], take / tb['c']['V'])
            tb['c'] = _scaled(tb['c'], 1 - take / tb['c']['V'])
            op['drawn'] = _add(op['drawn'], part)
        op['deficit'] += vol - take

    def _end_syringe_op(self, op, phase, code):
        sy = self.syr
        if op['kind'] == 'syringe.load':
            film = sy.get('film') or _comp(0.0)
            want = V_SYR - film['V']
            drawn = op['drawn']
            if phase == 'succeeded':
                liquid_v = min(want, drawn['V'])
                if op['deficit'] > 0:                                   # the tube ran dry: air drawn through the needle leaves a bubble (assumption: 1 uL)
                    liquid_v = max(0.0, liquid_v - 1.0e-9)
                liq = _add(_scaled(drawn, liquid_v / drawn['V']) if drawn['V'] > 0 else _comp(0.0), film)
                to_waste = drawn['V'] - liquid_v
                self._to_waste(to_waste)
                self.led['titrant_to_waste_m3'].add(to_waste)
                self.led['titrant_loaded_m3'].add(liquid_v)
                sy.update(liquid=liq, bubble=max(0.0, V_SYR - liq['V']), state='loaded', fresh=True, leak=0.0, tip_dilution=0.0, solution_id=op['solution_id'],
                          film=None)
                if sy['residue'] == 'methanol':
                    sy['methanol'] = ENG['syringe_methanol_residue_m3'] * 791.8 / 0.03204
                sy['residue'] = 'none'
                sy['T_E'] = None
                self._own('syringe')
            else:
                self._to_waste(drawn['V'])
                sy.update(state='empty', liquid=film if film['V'] > 0 else _comp(0.0), bubble=0.0)
        elif op['kind'] == 'syringe.purge_refill':
            if phase != 'succeeded':
                sy['state'] = 'loaded' if sy['liquid']['V'] > 0 else 'empty'
        self.plunger_target = None
        if op['phase'] == 'running':
            self._finish_op(op, phase, code)
        if code == 'SYRINGE_FILL_FAILURE':
            self._error(code, '注射器装样失败（检查FPA、注射器与管路）')

    # ---------------------------------------------------------------- washing module
    def _to_waste(self, vol):
        if vol <= 0:
            return
        cap_ = ENG['bottle_capacity_m3']
        w = self.wash
        room = max(0.0, cap_['waste'] - w['waste'])
        to_w = min(vol, room)
        w['waste'] += to_w
        self.led['waste_added_m3'].add(to_w)
        rest = vol - to_w
        if rest > 0:
            w['overflow'] += rest
            self.led['overflow_added_m3'].add(rest)
            if w['overflow'] >= cap_['overflow'] and w['pump_ok']:
                w['overflow'] = min(w['overflow'], cap_['overflow'])
                w['pump_ok'] = False
                self._error('WASH_PUMP_SHUTDOWN', '废液瓶满，溢流瓶已满，真空泵停机')

    def _cmd_clean_start(self, cell_method, syringe_method):
        self._need_power()
        self._need_not_busy()
        if cell_method == 'none' and syringe_method == 'none':
            raise ActionRejected('NOTHING_TO_CLEAN')
        if not self.wash_power:
            raise ActionRejected('WASH_MODULE_OFF')
        if not self.wash['pump_ok']:
            raise ActionRejected('WASH_PUMP_SHUTDOWN')
        if cell_method != 'none' and not self.tool_in:
            raise ActionRejected('CLEANING_TOOL_NOT_INSERTED')
        if syringe_method != 'none':
            if not (self.pip['phase'] is None and self.pip['loc'] == 'clean'):
                raise ActionRejected('PIPETTE_NOT_AT_CLEAN')
            if not self.fpa:
                raise ActionRejected('FPA_NOT_ATTACHED')
        cm = ENG['cell_methods'].get(cell_method)
        sm = ENG['syringe_methods'].get(syringe_method)
        dur = max(cm['duration_s'] if cm else 0.0, sm['duration_s'] if sm else 0.0)
        rates = dict(detergent=0.0, methanol=0.0, water=0.0)
        if cm:
            rates['water'] += cm['water_m3'] / cm['duration_s']
            rates['detergent'] += cm['detergent_m3'] / cm['duration_s']
        srates = dict(detergent=0.0, methanol=0.0, water=0.0)
        if sm:
            wet = sm['duration_s'] - sm['drying_s']
            for b in srates:
                srates[b] = sm.get(f'{b}_m3', 0.0) / wet
        op = self._new_op('clean.start', cell_method=cell_method, syringe_method=syringe_method, dur=dur, rates=rates, srates=srates,
                          methanol_short=False, delivered=dict(detergent=0.0, methanol=0.0, water=0.0))
        self.errors.pop('NO_FLOW', None)
        if cm:
            self._empty_cell(to_waste=True)
            self.cell['solution_id'] = None
        if sm:
            liq = self.syr['liquid']
            self._to_waste(liq['V'])
            self.syr.update(state='empty', liquid=_comp(0.0), bubble=0.0, film=_scaled(liq, ENG['syringe_residual_m3'] / liq['V']) if liq['V'] > 0 else
                            self.syr.get('film'), fresh=False)
            self.plunger_target = 0.0
            self._plunger_speed = ENG['plunger_speed_m_s']
        self.clean_state = 'cleaning'
        self._event('clean', 'CLEAN', f'清洗开始：池{cell_method}、注射器{syringe_method}', op['op_id'])
        return dict(operation_id=op['op_id'])

    def _clean_tick(self, op):
        el = self.t - op['t0']
        cm = ENG['cell_methods'].get(op['cell_method'])
        sm = ENG['syringe_methods'].get(op['syringe_method'])
        if 'wash_pump_failure' in self.faults:
            self._end_clean(op, 'failed', 'NO_FLOW')
            return
        f_clog = ENG['clogged_filter_factor'] if 'bottle_filter_clogged' in self.faults else 1.0
        need = dict(detergent=0.0, methanol=0.0, water=0.0)
        if cm and el <= cm['duration_s']:
            for b in ('detergent', 'water'):
                need[b] += op['rates'][b] * DT * f_clog
        if sm and el <= sm['duration_s'] - sm['drying_s']:
            for b in need:
                need[b] += op['srates'][b] * DT * f_clog
        for b, v in need.items():
            if v <= 0:
                continue
            if self.wash[b] < v:
                if b == 'methanol' and sm:
                    op['methanol_short'] = True
                    self.wash[b] = 0.0
                    continue
                self._end_clean(op, 'failed', 'NO_FLOW')
                return
            self.wash[b] -= v
            op['delivered'][b] += v
            self.led[f'{b}_used_m3'].add(v)
            self._to_waste(v)
            if not self.wash['pump_ok']:
                self._end_clean(op, 'failed', 'WASH_OVERFLOW')
                return
        if el >= op['dur'] - 1e-9:
            self._end_clean(op, 'succeeded', None)

    def _end_clean(self, op, phase, code):
        cm = ENG['cell_methods'].get(op['cell_method'])
        sm = ENG['syringe_methods'].get(op['syringe_method'])
        el = self.t - op['t0']
        f_clog = ENG['clogged_filter_factor'] if 'bottle_filter_clogged' in self.faults else 1.0

        def scrub(c, m, frac):
            if c is None:
                return None
            out = _comp(c['V'])
            sf = m['small_factor'] ** (frac * f_clog)
            mf = m['macro_factor'] ** (frac * f_clog)
            for k, v in c['n'].items():
                out['n'][k] = v * (mf if self.species.get(k, {}).get('macromolecule') else sf)
            out['buf'] = {k: v * sf for k, v in c['buf'].items()}
            out['th'] = c['th'] * sf
            return out
        if cm:
            frac = min(1.0, el / cm['duration_s'])
            res = scrub(self.cell['residual'], cm, frac)
            res['V'] = ENG['cell_residual_m3']                       # wet with water after the rinse
            self.cell['residual'] = res
        if sm:
            wet = sm['duration_s'] - sm['drying_s']
            frac = min(1.0, el / wet)
            film = scrub(self.syr.get('film') or _comp(0.0), sm, frac)
            dried = el >= sm['duration_s'] - 1e-9 and not op['methanol_short']
            if dried:
                film['V'] = 0.0
                self.syr['residue'] = 'none'
            elif el > wet and not op['methanol_short']:
                film['V'] = 0.0
                self.syr['residue'] = 'methanol'
            else:
                film['V'] = ENG['syringe_water_residue_m3']
                self.syr['residue'] = 'water'
            self.syr['film'] = film
        if op['phase'] == 'running':
            self._finish_op(op, 'succeeded' if phase == 'succeeded' else ('cancelled' if phase == 'stopped' else 'failed'), code)
        self.clean_state = {'succeeded': 'completed', 'stopped': 'stopped'}.get(phase, 'failed')
        if phase == 'succeeded':
            self.led['cleanings_completed'].add(1.0)
            self._event('clean', 'CLEAN_DONE', '清洗完成')
        elif code == 'NO_FLOW':
            self._error('NO_FLOW', '清洗时无液流（检查瓶中液体、滤芯与管路）')

    def _cmd_wash_empty_waste(self):
        self._need_plant()
        if self._running_ops('clean.start', 'syringe.load'):
            raise ActionRejected('INSTRUMENT_BUSY')
        v = self.wash['waste']
        self.wash['waste'] = 0.0
        return dict(emptied_m3=float(v))

    def _cmd_wash_empty_overflow(self):
        self._need_plant()
        if self._running_ops('clean.start', 'syringe.load'):
            raise ActionRejected('INSTRUMENT_BUSY')
        v = self.wash['overflow']
        self.wash['overflow'] = 0.0
        if not self.wash['pump_ok']:
            self.wash['pump_ok'] = True
            self.errors.pop('WASH_PUMP_SHUTDOWN', None)
        return dict(emptied_m3=float(v))

    def _cmd_wash_refill(self, bottle):
        self._need_plant()
        if self._running_ops('clean.start', 'syringe.load'):
            raise ActionRejected('INSTRUMENT_BUSY')
        self.wash[bottle] = ENG['bottle_capacity_m3'][bottle]
        self.errors.pop('NO_FLOW', None)
        return dict(bottle=bottle, volume_m3=float(self.wash[bottle]))

    # ---------------------------------------------------------------- run
    def _cmd_run_start(self, experiment_id):
        self._need_power()
        if self.method is None:
            raise ActionRejected('NO_METHOD')
        self._need_not_busy()
        if experiment_id in self.experiments:
            raise ActionRejected('ITEM_EXISTS')
        if len(self.experiments) >= MAX_EXPERIMENTS:
            raise ActionRejected('TOO_MANY_ITEMS')
        if self.tool_in:
            raise ActionRejected('CLEANING_TOOL_INSERTED')
        if self.cell['mix']['V'] <= 0:
            raise ActionRejected('CELL_EMPTY')
        if not self._seated():
            raise ActionRejected('PIPETTE_NOT_IN_CELL')
        if self.fpa:
            raise ActionRejected('FPA_ATTACHED')
        if self.syr['state'] != 'loaded' or self.plunger_target is not None:
            raise ActionRejected('SYRINGE_NOT_LOADED')
        op = self._new_op('run.start', experiment_id=experiment_id)
        m = copy.deepcopy(self.method)
        self.experiments[experiment_id] = dict(experiment_id=experiment_id, state='running', method=m, started_s=self.t, finished_s=None, data_t0=None, t=[], dp=[],
                                               T=[], injections=[], cell_solution=self.cell['solution_id'], syringe_solution=self.syr['solution_id'], op_id=op['op_id'],
                                               truth=dict(volumes=[], heat_j=[]), clipped=0, p_ref=m['reference_power_w'], feedback=m['feedback'])
        self.run = dict(op_id=op['op_id'], exp_id=experiment_id, phase='equilibrating', t_phase=self.t, inj=-1, inj_t0=None, nom=0.0, act=0.0, done=0.0,
                        rec_tick0=None)
        self.T_set, self.p_ref, self.feedback = m['temperature_k'], m['reference_power_w'], m['feedback']
        self.stir_hz = 0.0 if 'stir_motor_failure' in self.faults else m['stir_rate_hz']
        self._own('experiment:' + experiment_id)
        self._event('run', 'START', f'实验{experiment_id}开始（热平衡）', 'experiment:' + experiment_id)
        return dict(operation_id=op['op_id'])

    def _end_run(self, state, code):
        r = self.run
        if r is None:
            return
        exp = self.experiments[r['exp_id']]
        if r.get('to_expel', 0.0) > 0:                                            # the compressed bubble keeps pushing after the plunger stops
            comp, E, meth, air, tip = self._syringe_dispense(r['to_expel'])
            air = min(air, self.syr['bubble'])
            self.syr['bubble'] -= air
            if self._seated():
                self._into_cell(comp, E, meth, air, tip)
            else:
                self.led['syringe_spilled_m3'].add(comp['V'])
            if exp['truth']['volumes']:
                exp['truth']['volumes'][-1] += comp['V']
            r['to_expel'] = 0.0
        exp['state'] = state
        exp['finished_s'] = self.t
        op = self.operations[r['op_id']]
        if op['phase'] == 'running':
            self._finish_op(op, {'completed': 'succeeded', 'stopped': 'cancelled'}.get(state, 'failed'), code)
        op['result'] = dict(experiment_id=r['exp_id'], injections=len(exp['injections']))
        self.run = None
        self.stir_hz = 0.0
        self.syr['fresh'] = False
        if state == 'completed':
            self.led['runs_completed'].add(1.0)
        self._event('run', state.upper(), f'实验{r["exp_id"]}：{state}' + (f'（{code}）' if code else ''), 'experiment:' + r['exp_id'])

    def _cmd_instrument_stop(self):
        self._need_power()
        stopped = []
        for op in list(self._running_ops()):
            self._cancel_op(op)
            stopped.append(op['kind'])
        if self.plunger_target is not None and self.syr['state'] == 'expelling':
            self.plunger_target = None
            self.syr['state'] = 'loaded' if self.syr['liquid']['V'] > 0 else 'empty'
            stopped.append('syringe.plunger_down')
        return dict(stopped=stopped)

    # ---------------------------------------------------------------- physics
    def step(self, dt_s):
        """Advance the host clock: physics on the fixed 0.2 s grid (k * 0.2 s); state between grid points is held, so any split of the same interval
        gives identical results."""
        if dt_s < 0:
            raise ActionRejected('INVALID_ARGUMENT')
        t_end = self.t + float(dt_s)
        if self.plant is None:
            self.t = t_end
            return self.t
        while (self.tick + 1) * DT <= t_end + 1e-9:
            self.tick += 1
            self.t = self.tick * DT
            self._physics_tick()
        self.t = t_end
        return self.t

    def _c_heat(self, V):
        return 4184.0 * 1000.0 * min(V0, max(V, 0.0))

    def _mix_tau(self):
        if self.stir_hz > 0.5 and self._seated():
            return ENG['mixing_time_s_at_750'] * 12.5 / self.stir_hz
        return ENG['unstirred_mixing_time_s']

    def _syr_e_density(self):
        """Enthalpy density (J/m3) of the syringe liquid at the cell temperature (the titrant reaches the cell temperature in the needle)."""
        sy = self.syr
        liq = sy['liquid']
        if liq['V'] <= 0:
            return 0.0
        if sy.get('T_E') is None or abs(self.T_s - sy['T_E']) > 1e-4:
            sy['E_density'] = self._energy(liq, self.T_s)[0] / liq['V']
            sy['T_E'] = self.T_s
        return sy['E_density']

    def _syringe_dispense(self, dv):
        """Dispense dv (m3) through the needle: first the cell solution that diffused into the tip, then the syringe liquid, then air.
        Returns (composition, enthalpy, methanol mol, air m3, tip part of the composition)."""
        sy = self.syr
        out, E, meth = _comp(0.0), 0.0, 0.0
        tip = _comp(0.0)
        if sy['tip_dilution'] > 0 and dv > 0:
            v = min(dv, sy['tip_dilution'])
            mx = self.cell['mix']
            if mx['V'] > 0:
                tip = _scaled(mx, v / mx['V'])
                out = _add(out, tip)
                E += self.cell['E'] * v / mx['V']
            sy['tip_dilution'] -= v
            dv -= v
        liq = sy['liquid']
        v = min(dv, liq['V'])
        if v > 0:
            f = v / liq['V']
            out = _add(out, _scaled(liq, f))
            E += self._syr_e_density() * v
            meth = sy['methanol'] * f
            sy['methanol'] -= meth
            sy['liquid'] = _scaled(liq, 1 - f)
        return out, E, meth, max(0.0, dv - v), tip

    def _into_cell(self, comp, E, meth, air, tip=None):
        """Liquid (and air) entering the active volume through the needle: added to the unmixed slug; the same volume of mixed content is displaced."""
        dv = comp['V'] + air
        if dv <= 0:
            return
        mx = self.cell['mix']
        if mx['V'] > 0:
            f = min(1.0, dv / mx['V'])
            self.cell['mix'] = _scaled(mx, 1 - f)
            self.cell['E'] *= (1 - f)
        self.pool['c'] = _add(self.pool['c'], comp)
        if tip is not None and tip['V'] > 0:
            self.pool['tip'] = _add(self.pool.get('tip') or _comp(0.0), tip)       # cell solution from the needle tip (not yet reacted with the titrant)
        self.pool['E'] += E
        self.pool['methanol'] += meth
        if air > 0:
            self.cell['air'] = self.cell.get('air', 0.0) + air                    # injected air accumulates under the cell lid
            if self.cell['air'] > 2.0e-10:
                self.cell['bubble'] = True
        self.led['titrant_injected_m3'].add(comp['V'])

    def _mix_tick(self):
        """Mix part of the unmixed slug into the active volume and re-equilibrate; returns the heat released (W).  Both sides of the balance are
        evaluated at the current cell temperature (the temperature-driven change of the stored enthalpy is sensible heat, not reaction heat);
        slow-binding states are kept when the temperature moves."""
        pool, cell = self.pool, self.cell
        pending = cell.get('pending', False)
        mx = cell['mix']
        T = self.T_s
        if pool['c']['V'] <= 0 and pool['methanol'] <= 0 and not pending:
            if mx['V'] > 0 and cell['T_eq'] is not None and abs(T - cell['T_eq']) > 1e-4:
                cell['E'], info = self._energy(mx, T, kin=dict(cell['kin']), dt=0.0, logh0=(cell['eq'] or {}).get('logh'))
                pend = any(abs(v - info['kin_eq'].get(k, v)) > 1e-12 * max(abs(info['kin_eq'].get(k, v)), 1e-12) for k, v in info['kin'].items())
                cell.update(eq=info, kin=dict(info['kin']), T_eq=T, pending=pend)  # fast equilibria re-solved (sensible heat); slow complexes relax later
            return 0.0
        if mx['V'] <= 0:
            cell['mix'] = _add(mx, pool['c'])
            self.pool = dict(c=_comp(0.0), E=0.0, methanol=0.0)
            return 0.0
        if cell['T_eq'] is None or abs(T - cell['T_eq']) > 1e-10:            # both sides of the balance at exactly the current temperature
            cell['E'], info = self._energy(mx, T, kin=dict(cell['kin']), dt=0.0, logh0=(cell['eq'] or {}).get('logh'))
            cell.update(kin=dict(info['kin']), T_eq=T)
        tip_tr = _comp(0.0)
        if pool['c']['V'] > 0 or pool['methanol'] > 0:
            f = 1.0 if pool['c']['V'] < 1e-16 else 1.0 - math.exp(-DT / self._mix_tau())
            tr = _scaled(pool['c'], f)
            if pool.get('tip') is not None and pool['tip']['V'] > 0:
                tip_tr = _scaled(pool['tip'], f)
            E_tr, meth_tr = pool['E'] * f, pool['methanol'] * f
            if f >= 1.0:
                self.pool = dict(c=_comp(0.0), E=0.0, methanol=0.0)
            else:
                pool['c'] = _scaled(pool['c'], 1 - f)
                if pool.get('tip') is not None:
                    pool['tip'] = _scaled(pool['tip'], 1 - f)
                pool['E'] *= 1 - f
                pool['methanol'] *= 1 - f
        else:
            tr, E_tr, meth_tr = _comp(0.0), 0.0, 0.0
        slug_kin = {}
        if tr['V'] > 0:                                                         # the transferred slug portion at the cell temperature: titrant and the cell
            E_tr, info_tr = self._energy(_sub(tr, tip_tr), T)                   # solution from the tip apart; slow complexes carried in keep their bound
            for k_, b_ in info_tr['kin'].items():                                 # amount (mol) in the kinetic state of the mixture
                slug_kin[k_] = slug_kin.get(k_, 0.0) + b_ * _sub(tr, tip_tr)['V'] * 1000.0
            if tip_tr['V'] > 0:
                E_tip, info_tip = self._energy(tip_tr, T)
                E_tr += E_tip
                for k_, b_ in info_tip['kin'].items():
                    slug_kin[k_] = slug_kin.get(k_, 0.0) + b_ * tip_tr['V'] * 1000.0
        new = _add(mx, tr)
        if meth_tr > 0:
            new['n']['methanol'] = new['n'].get('methanol', 0.0) + meth_tr
        Lold, Lnew = mx['V'] * 1000.0, new['V'] * 1000.0
        kin = {k: v * Lold / Lnew for k, v in cell['kin'].items()} if Lnew > 0 else {}
        for k_, mol in slug_kin.items():
            kin[k_] = kin.get(k_, 0.0) + mol / Lnew if Lnew > 0 else 0.0
        for k_, it in self.interactions.items():                                 # a slow complex that was absent starts from zero (it forms over time)
            if it['kon'] is not None and k_ not in kin and new['n'].get(it['receptor'], 0.0) > 0 and new['n'].get(it['ligand'], 0.0) > 0:
                kin[k_] = 0.0
        E_before = cell['E'] + E_tr
        E_after, info = self._energy(new, T, kin=kin, dt=DT, logh0=(cell['eq'] or {}).get('logh'), apply_enzyme=True)
        pend = any(abs(v - info['kin_eq'].get(k, v)) > 1e-12 * max(abs(info['kin_eq'].get(k, v)), 1e-12) for k, v in info['kin'].items())
        pend = pend or any(it['model'] == 'enzyme' and new['n'].get(it['ligand'], 0.0) > 1e-18 and new['n'].get(it['receptor'], 0.0) > 0
                           for it in self.interactions.values())
        cell.update(mix=new, E=E_after, eq=info, kin=dict(info['kin']), pending=pend, T_eq=T)
        q = -(E_after - E_before) / DT
        self.led['chemical_heat_j'].add(q * DT)
        return q

    def _start_injection(self):
        r = self.run
        exp = self.experiments[r['exp_id']]
        m = exp['method']
        r['inj'] += 1
        inj = m['injections'][r['inj']]
        eps = _gauss(self.seed, _h(f'{r["exp_id"]}:{r["inj"]}'), 41)
        act = inj['volume_m3'] * (1 + ENG['injection_precision_rel'] * eps)
        r.update(phase='injecting', t_phase=self.t, inj_t0=self.t, nom=inj['volume_m3'], act=act, done=0.0, dur=inj['duration_s'], spacing=inj['spacing_s'],
                 heat0=self.led['chemical_heat_j'].value())
        exp['injections'].append(dict(index=r['inj'] + 1, start_s=round(self.t - exp['data_t0'], 6), volume_m3=inj['volume_m3'], duration_s=inj['duration_s']))
        exp['truth']['volumes'].append(0.0)
        self.led['injections'].add(1.0)
        self._event('injection', 'INJECT', f'第{r["inj"] + 1}针 {inj["volume_m3"] * 1e9:.2f} µL', 'experiment:' + r['exp_id'])

    def _run_tick(self, q_th):
        r = self.run
        if r is None:
            return 0.0
        exp = self.experiments[r['exp_id']]
        m = exp['method']
        el = self.t - r['t_phase']
        flow = 0.0
        if r['phase'] == 'equilibrating':
            ok = (el >= ENG['equilibration_min_s'] and abs(self.T_j - self.T_set) < 1e-3 and abs(self.T_s - self.T_j) < 5e-4
                  and abs(q_th) < ENG['equilibration_heat_flow_w'] and self._dp_ok_ticks * DT >= 60.0)
            if ok:
                r.update(phase='initial_delay', t_phase=self.t, rec_tick0=self.tick)
                exp['data_t0'] = self.t
                self._event('run', 'EQUILIBRATED', '热平衡完成，开始初始延迟', 'experiment:' + r['exp_id'])
            elif el > ENG['equilibration_timeout_s']:
                self._end_run('failed', 'EQUILIBRATION_TIMEOUT')
            return 0.0
        if r['phase'] == 'initial_delay':
            if el >= m['initial_delay_s'] - 1e-9:
                self._start_injection()
            else:
                return 0.0
        if r['phase'] == 'spacing' and self.t - r['inj_t0'] >= r['spacing'] - 1e-9:     # spacing counts from the start of the injection
            exp['truth']['heat_j'].append(self.led['chemical_heat_j'].value() - r['heat0'])
            if r['inj'] + 1 < len(m['injections']):
                self._start_injection()
            else:
                self._end_run('completed', None)
                return 0.0
        if r['phase'] == 'injecting':
            dv_nom = min(r['nom'] / r['dur'] * DT, r['nom'] - r['done'])
            if dv_nom > 0:
                r['to_expel'] = r.get('to_expel', 0.0) + dv_nom * r['act'] / r['nom']     # actual stroke volume of this grid step
                r['done'] += dv_nom
                self.plunger = min(PLUNGER_STROKE, self.plunger + dv_nom / V_SYR * PLUNGER_STROKE)
                self.syr['fresh'] = False
            if r['done'] >= r['nom'] - 1e-18:
                r['phase'] = 'spacing'
        if r.get('to_expel', 0.0) > 0:
            sy = self.syr
            if sy['bubble'] > 0:                                                    # compressible air: delivery lags the plunger (tau ~ bubble size)
                tau_b = max(DT, ENG['bubble_lag_s_per_m3'] * sy['bubble'])
                v = r['to_expel'] * (1 - math.exp(-DT / tau_b))
                v = r['to_expel'] if v < 1e-16 else v
            else:
                v = r['to_expel']
            comp, E, meth, air, tip = self._syringe_dispense(v)                     # liquid first (air rises to the plunger), air last
            air = min(air, sy['bubble'])
            sy['bubble'] -= air
            r['to_expel'] = max(0.0, r['to_expel'] - comp['V'] - air) if (comp['V'] + air) > 0 else 0.0
            self._into_cell(comp, E, meth, air, tip)
            if air > 0 or (comp['V'] <= 0 and sy['liquid']['V'] <= 0):
                self.warnings['SYRINGE_EMPTY'] = '注射器液体已推完（注入空气或空推）'
            exp['truth']['volumes'][-1] += comp['V']
            flow = comp['V'] / DT
        return flow

    def _syringe_op_tick(self, op):
        el = self.t - op['t0']
        if op['kind'] == 'syringe.load':
            film = self.syr.get('film') or _comp(0.0)
            total = V_SYR - film['V'] + ENG['load_excess_m3']
            if not self._tip_in_tube() or not self.fpa:
                self._end_syringe_op(op, 'failed', 'SYRINGE_FILL_FAILURE')
                return
            if 'syringe_damaged' not in self.faults and self.wash['pump_ok'] and 'wash_pump_failure' not in self.faults:
                self._syringe_fill_from_tube(op, total * DT / op['dur'])
            if el >= op['dur'] - 1e-9:
                self.plunger = 0.0
                if 'syringe_damaged' in self.faults or 'wash_pump_failure' in self.faults or not self.wash['pump_ok']:
                    self._end_syringe_op(op, 'failed', 'SYRINGE_FILL_FAILURE')
                else:
                    self._end_syringe_op(op, 'succeeded', None)
        elif op['kind'] == 'syringe.purge_refill':
            half = op['dur'] / 2
            sy = self.syr
            if el < half - 1e-9:
                return
            if not op.get('expelled'):
                op['expelled'] = True
                liq = sy['liquid']
                if self.tube is not None and liq['V'] > 0:
                    self.tube['c'] = _add(self.tube['c'], liq)
                sy.update(liquid=_comp(0.0), bubble=0.0, tip_dilution=0.0)
                self.plunger_target = 0.0
            if el >= op['dur'] - 1e-9:
                want = V_SYR
                tb = self.tube['c'] if self.tube is not None else _comp(0.0)
                take = min(want, tb['V'])
                liq = _scaled(tb, take / tb['V']) if tb['V'] > 0 else _comp(0.0)
                if self.tube is not None and tb['V'] > 0:
                    self.tube['c'] = _scaled(tb, 1 - take / tb['V'])
                sy.update(liquid=liq, bubble=max(0.0, V_SYR - take), state='loaded' if take > 0 else 'empty', fresh=True)
                sy['T_E'] = None
                self.plunger = 0.0
                self._end_syringe_op(op, 'succeeded', None)

    def _plunger_tick(self):
        if self.plunger_target is None or not self.power:
            return
        sp = getattr(self, '_plunger_speed', ENG['plunger_speed_m_s'])
        old = self.plunger
        tgt = self.plunger_target
        new = min(tgt, old + sp * DT) if tgt > old else max(tgt, old - sp * DT)
        self.plunger = new
        sy = self.syr
        if new > old and sy['state'] == 'expelling':
            cap_new = (1 - new / PLUNGER_STROKE) * V_SYR
            content = sy['liquid']['V'] + sy['tip_dilution'] + sy['bubble']
            ex = content - max(cap_new, ENG['syringe_residual_m3'])
            if ex > 0:
                comp, E, meth, air, tip = self._syringe_dispense(ex)
                dest = self._tip_destination()
                if dest == 'cell':
                    self._into_cell(comp, E, meth, air, tip)
                elif dest == 'tube':
                    self.tube['c'] = _add(self.tube['c'], comp)
                elif dest == 'waste':
                    self._to_waste(comp['V'])
                else:
                    self.led['syringe_spilled_m3'].add(comp['V'])
                sy['bubble'] = max(0.0, sy['bubble'] - air)
        if abs(new - tgt) < 1e-12:
            self.plunger = tgt
            self.plunger_target = None
            if sy['state'] == 'expelling':
                liq = sy['liquid']
                sy.update(state='empty', film=liq if liq['V'] > 0 else sy.get('film'), liquid=_comp(0.0), bubble=0.0, tip_dilution=0.0, fresh=False)

    def _pipette_tick(self):
        p = self.pip
        if p['phase'] is None:
            return
        if p['phase'] == 'lift':
            p['lift'] = min(LIFT_TRAVEL, p['lift'] + ENG['lift_speed_m_s'] * DT)
            if p['lift'] >= LIFT_TRAVEL - 1e-12:
                p['lift'] = LIFT_TRAVEL
                p['phase'] = 'swing'
        elif p['phase'] == 'swing':
            q = LOCATION_Q[p['target']]
            d = q - p['swing']
            stepq = ENG['swing_speed_rad_s'] * DT
            p['swing'] = q if abs(d) <= stepq else p['swing'] + math.copysign(stepq, d)
            if abs(p['swing'] - q) < 1e-12:
                p['phase'] = 'lower'
        elif p['phase'] == 'lower':
            p['lift'] = max(0.0, p['lift'] - ENG['lift_speed_m_s'] * DT)
            if p['lift'] <= 1e-12:
                p['lift'] = 0.0
                p['phase'] = None
                p['loc'] = p['target']
                p['target'] = None
                self._event('pipette', 'ARRIVED', f'移液器到达{p["loc"]}')

    def _physics_tick(self):
        pl = self.plant
        k = self.tick
        self.T_room += pl['room_drift_k_per_s'] * DT
        evap = ENG['reference_evaporation_m3_per_day'] / 86400.0 * DT
        if self.ref['V'] > 0:
            e = min(evap, self.ref['V'])
            self.ref['V'] -= e
            self.led['reference_evaporated_m3'].add(e)
        if 'reference_cell_empty' in self.faults:
            self.ref['V'] = 0.0
        self.ref['age'] += DT
        if self.power:
            d = (self.T_set - self.T_j) * (1 - math.exp(-DT / ENG['jacket_tau_s']))
            lim = ENG['jacket_max_rate_k_s'] * DT
            self.T_j += max(-lim, min(lim, d))
        else:
            self.T_j += (self.T_room - self.T_j) * (1 - math.exp(-DT / 3600.0))
        Vs = self.cell['mix']['V'] + self.pool['c']['V']
        Cs, Cr = self._c_heat(Vs), self._c_heat(self.ref['V'])
        tc = ENG['cell_equilibration_tau_s']
        q_th = (Cs * (self.T_s - self.T_j) - Cr * (self.T_r - self.T_j)) / tc
        a = 1 - math.exp(-DT / tc)
        self.T_s += (self.T_j - self.T_s) * a
        self.T_r += (self.T_j - self.T_r) * a
        self._pipette_tick()
        self._plunger_tick()
        for op in self._running_ops('syringe.load', 'syringe.purge_refill'):
            self._syringe_op_tick(op)
        for op in self._running_ops('clean.start'):
            self._clean_tick(op)
        flow = self._run_tick(q_th) if self.run is not None else 0.0
        sy = self.syr
        if sy['fresh'] and self._seated() and sy['liquid']['V'] > 0:
            dl = (ENG['first_injection_leak_m3'] - sy['leak']) * (1 - math.exp(-DT / ENG['first_injection_leak_tau_s']))
            sy['leak'] += dl
            sy['_leak_pending'] = sy.get('_leak_pending', 0.0) + dl
            if k % 25 == 0 and sy['_leak_pending'] > 0:
                v = min(sy['_leak_pending'], sy['liquid']['V'])
                f = v / sy['liquid']['V']
                comp = _scaled(sy['liquid'], f)
                E = self._syr_e_density() * v
                meth = sy['methanol'] * f
                sy['methanol'] -= meth
                sy['liquid'] = _scaled(sy['liquid'], 1 - f)
                sy['tip_dilution'] += v
                sy['_leak_pending'] = 0.0
                self._into_cell(comp, E, meth, 0.0)
        q_chem = self._mix_tick()
        q_blank = -ENG['blank_heat_j_per_m3'] * flow
        q_stir = ENG['stirring_heat_w_at_750'] * (self.stir_hz / 12.5) ** ENG['stirring_heat_exponent'] if (self.stir_hz > 0 and self._seated()) else 0.0
        spike = getattr(self, '_spike', 0.0) * math.exp(-DT / 2.0)
        if self.run is not None and not self.cell['degassed'] and self.T_set - self.T_room > ENG['outgassing_threshold_k']:
            if _uni(self.seed, k, 31) < ENG['outgassing_rate_per_s'] * DT:
                spike += ENG['outgassing_spike_w'] * (0.5 + _uni(self.seed, k, 33))
        self._spike = spike
        self.q_now = q_chem + q_blank
        q_total = q_chem + q_blank + q_th + q_stir + spike
        self._q_total = q_total
        tau = ENG['response_time_s'][self.feedback]
        self.y += (q_total - self.y) * (1 - math.exp(-DT / tau))
        self._dp_ok_ticks = self._dp_ok_ticks + 1 if abs(q_total - self.y) < ENG['equilibration_heat_flow_w'] else 0   # DP response converged
        self.drift = self.drift * math.exp(-DT / 600.0) + ENG['baseline_drift_w_per_sqrt_s'] * math.sqrt(DT) * _gauss(self.seed, k, 2)
        bubble = self.cell['bubble'] or 'cell_bubble' in self.faults
        if bubble and _uni(self.seed, k, 34) < DT / 120.0:
            self._steps_k += W_UCAL(0.05) * (1 if _uni(self.seed, k, 35) < 0.5 else -1)
        ref_off = ENG['reference_offset_w_per_m3'] * max(0.0, ENG['reference_full_m3'] - self.ref['V'])
        if self.power:
            self.dp_true = self.p_ref - self.y + ref_off + self.drift + (self._steps_k if bubble else 0.0)
            noise = ENG['noise_w'][self.feedback] * (ENG['poor_load_noise_factor'] if bubble else 1.0) * _gauss(self.seed, k, 1)
            raw = self.dp_true + noise
            self.dp = min(max(raw, 0.0), ENG['dp_max_w'])
            self.dp_clipped = raw <= 0.0 or raw >= ENG['dp_max_w']
            self.T_meas = self.T_s + 4.0e-5 * _gauss(self.seed, k, 3)
        else:
            self.dp_true = self.dp = None
            self.dp_clipped = False
            self.T_meas = None
        r = self.run
        if r is not None and r['rec_tick0'] is not None and (k - r['rec_tick0']) % int(round(REC_DT / DT)) == 0:
            exp = self.experiments[r['exp_id']]
            exp['t'].append(round(self.t - exp['data_t0'], 6))
            exp['dp'].append(float(self.dp))
            exp['T'].append(float(self.T_meas))
            if self.dp_clipped:
                exp['clipped'] += 1
        self._warnings()
        e = 0.0
        if self.power:
            e += ENG['electrical_power_w']['cell_unit']
        if self.wash_power:
            e += ENG['electrical_power_w']['wash_module_pump' if self._running_ops('clean.start', 'syringe.load') else 'wash_module_idle']
        self.led['electrical_energy_j'].add(e * DT)

    def _warnings(self):
        w = {}
        if not AMBIENT[0] <= self.T_room <= AMBIENT[1]:
            w['AMBIENT_OUT_OF_RANGE'] = '室温超出厂家环境范围10–28 °C'
        if self.dp_clipped:
            w['DP_OUT_OF_RANGE'] = 'DP超出量程（截断）'
        cap_ = ENG['bottle_capacity_m3']
        if self.wash['waste'] >= 0.9 * cap_['waste']:
            w['WASTE_BOTTLE_FULL'] = '废液瓶将满'
        for b in ('detergent', 'methanol', 'water'):
            if self.wash[b] < 0.1 * cap_[b]:
                w[f'BOTTLE_LOW_{b.upper()}'] = f'{b}瓶液位低'
        if self.run is not None and 'SYRINGE_EMPTY' in self.warnings:
            w['SYRINGE_EMPTY'] = self.warnings['SYRINGE_EMPTY']
        for code in w:
            if code not in self.warnings:
                self._event('warning', code, w[code])
        self.warnings = w

    # ---------------------------------------------------------------- read
    def _species_map(self, comp):
        cn, cb, th = _conc(comp)
        out = {k: v * 1000.0 for k, v in cn.items() if v * 1000.0 > 1e-12}
        out.update({k: v * 1000.0 for k, v in cb.items() if v * 1000.0 > 1e-12})
        return out

    def read(self, visibility='agent'):
        on = self.power
        r = self.run
        exp = self.experiments[r['exp_id']] if r is not None else None
        rem = None
        if r is not None and r['phase'] != 'equilibrating':
            m = exp['method']
            if r['phase'] == 'initial_delay':
                rem = m['initial_delay_s'] - (self.t - r['t_phase']) + sum(i['spacing_s'] for i in m['injections'])
            else:
                rem = r['spacing'] - (self.t - r['inj_t0']) + sum(i['spacing_s'] for i in m['injections'][r['inj'] + 1:])
            rem = max(0.0, float(rem))
        m = self.method
        msum = None if m is None else dict(temperature_k=m['temperature_k'], reference_power_w=m['reference_power_w'], feedback=m['feedback'],
                                           stir_rate_hz=m['stir_rate_hz'], initial_delay_s=m['initial_delay_s'], injections=len(m['injections']),
                                           total_volume_m3=float(sum(i['volume_m3'] for i in m['injections'])),
                                           cell_concentration_mol_m3=m['cell_concentration_mol_m3'], syringe_concentration_mol_m3=m['syringe_concentration_mol_m3'])
        last = None
        if r is not None:
            last = r['exp_id']
        elif self.experiments:
            last = max(self.experiments.values(), key=lambda e: e['started_s'])['experiment_id']
        cap_ = ENG['bottle_capacity_m3']
        sy = self.syr
        avail = (1 - self.plunger / PLUNGER_STROKE) * V_SYR if sy['state'] == 'loaded' else 0.0
        info = self.cell.get('eq') or {}
        whole = _add(self.cell['mix'], self.pool['c']) if self.cell['mix']['V'] > 0 else self.cell['mix']
        liq = _add(sy['liquid'], _comp(sy['tip_dilution']))
        v = {
            'clock.time_s': self.t, 'plant.configured': self.plant is not None, 'power.enabled': on, 'wash.powered': self.wash_power,
            'lamp.status': on, 'lamp.wash_status': self.wash_power,
            'temperature.setpoint_k': float(self.T_set), 'temperature.cell_k': float(self.T_meas) if (on and getattr(self, 'T_meas', None) is not None) else None,
            'dp.power_w': float(self.dp) if (on and self.dp is not None) else None, 'dp.reference_power_w': float(self.p_ref), 'dp.feedback': self.feedback,
            'stir.rate_hz': float(self.stir_hz if self._seated() else 0.0), 'method.current': msum,
            'run.state': r['phase'] if r is not None else (exp['state'] if exp else (self.experiments[last]['state'] if last else 'idle')),
            'run.experiment_id': last, 'run.injection_number': int(r['inj'] + 1) if r is not None else (len(self.experiments[last]['injections']) if last else 0),
            'run.remaining_time_s': rem,
            'pipette.location': self.pip['loc'] if self.pip['phase'] is None else 'moving', 'pipette.lift_m': float(self.pip['lift']),
            'pipette.swing_rad': float(self.pip['swing']), 'pipette.seated_in_cell': self._seated(),
            'syringe.plunger_position_m': float(self.plunger), 'syringe.state': sy['state'], 'syringe.available_volume_m3': float(avail), 'fpa.attached': self.fpa,
            'cell.solution_id': self.cell['solution_id'], 'cell.loaded_volume_m3': float(self.cell['loaded']), 'cell.cleaning_tool_inserted': self.tool_in,
            'reference.age_s': float(self.ref['age']),
            'loading_station.tube_solution_id': self.tube['solution_id'] if self.tube else None,
            'loading_station.tube_volume_m3': float(self.tube['c']['V']) if self.tube else 0.0,
            'wash.bottle_fraction': {b: float(min(1.0, self.wash[b] / cap_[b])) for b in BOTTLES}, 'wash.pump_ok': bool(self.wash['pump_ok']),
            'clean.state': self.clean_state, 'operations.running': sorted({op['kind'] for op in self._running_ops()}), 'operation.started_by_other_agent': False,
            'warnings.active': sorted(self.warnings), 'errors.active': sorted(self.errors),
            'solutions.available': sorted(self.solutions), 'experiments.count': len(self.experiments),
            'truth.jacket_temperature_k': self.T_j, 'truth.cell_content_temperature_k': self.T_s, 'truth.reference_temperature_k': self.T_r,
            'truth.dp_noise_free_w': float(self.dp_true) if self.dp_true is not None else None, 'truth.heat_rate_w': float(self.q_now),
            'truth.cell_species_mol_m3': self._species_map(whole), 'truth.cell_ph': info.get('ph') if self.cell['mix']['V'] > 0 else None,
            'truth.cell_bound_mol_m3': {k: float(b * 1000.0) for k, b in info.get('bound', {}).items() if b * 1000.0 > 1e-12} if self.cell['mix']['V'] > 0 else {},
            'truth.unmixed_volume_m3': float(self.pool['c']['V']), 'truth.syringe_liquid_m3': float(liq['V']), 'truth.syringe_bubble_m3': float(sy['bubble']),
            'truth.syringe_species_mol_m3': self._species_map(sy['liquid']), 'truth.syringe_residue': sy['residue'],
            'truth.reference_volume_m3': float(self.ref['V']), 'truth.cell_bubble': bool(self.cell['bubble'] or 'cell_bubble' in self.faults),
            'truth.cell_contamination_fraction': float(self.cell['contamination']), 'truth.wash_volumes_m3': {b: float(self.wash[b]) for b in BOTTLES},
            'truth.faults': sorted(self.faults),
        }
        if visibility == 'agent':
            v = {k: x for k, x in v.items() if SLOTS[k]['visibility'] == 'agent'}
        return v

    # ---------------------------------------------------------------- data queries
    def _exp(self, eid):
        e = self.experiments.get(eid)
        if e is None:
            raise ActionRejected('UNKNOWN_EXPERIMENT')
        return e

    def _cmd_data_analyze(self, experiment_id, model, exclude_injections=(1,), control='none', control_experiment_id=None, ligand_in_cell=False,
                          cell_concentration_mol_m3=None, syringe_concentration_mol_m3=None, competitor=None, sites=2, enzyme_dh_j_mol=None, fix=None):
        exp = self._exp(experiment_id)
        if not exp['injections']:
            raise ActionRejected('NO_DATA')
        crow = None
        if control == 'experiment':
            if control_experiment_id is None:
                raise ActionRejected('INVALID_ARGUMENT')
            crow = _integrate(self._exp(control_experiment_id))
        if model == 'competitive' and competitor is None:
            raise ActionRejected('COMPETITOR_REQUIRED')
        if model == 'enzyme_kinetics_multiple' and enzyme_dh_j_mol is None:
            raise ActionRejected('ENZYME_DH_REQUIRED')
        if ligand_in_cell and model == 'competitive':
            raise ActionRejected('INVALID_ARGUMENT')
        if fix and (model not in ('one_set_of_sites', 'two_sets_of_sites', 'competitive') or
                    (model != 'two_sets_of_sites' and set(fix) & {'n2', 'kd2', 'dh2'}) or ('offset' in fix and control != 'fitted_offset')):
            raise ActionRejected('INVALID_ARGUMENT')                                # fix applies to One Set, Two Sets and Competitive only (offset: fitted offset)
        res = analyze_experiment(exp, model, exclude=exclude_injections, control=control, control_rows=crow, ligand_in_cell=ligand_in_cell,
                                 cell_m=cell_concentration_mol_m3, syr_m=syringe_concentration_mol_m3, competitor=competitor, sites=sites,
                                 enzyme_dh=enzyme_dh_j_mol, fix=fix)
        return dict(experiment_id=experiment_id, model=model, **res)

    def _cmd_data_read(self, experiment_id, max_points=1000):
        exp = self._exp(experiment_id)
        n = len(exp['t'])
        idx = np.unique(np.linspace(0, n - 1, min(n, max_points)).round().astype(int)) if n else np.zeros(0, int)
        return dict(experiment_id=experiment_id, time_s=[float(exp['t'][i]) for i in idx], dp_w=[float(exp['dp'][i]) for i in idx],
                    temperature_k=[float(exp['T'][i]) for i in idx],
                    injections=[dict(index=i['index'], start_s=float(i['start_s']), volume_m3=float(i['volume_m3']), duration_s=float(i['duration_s']))
                                for i in exp['injections']], completed=exp['state'] == 'completed')

    def _cmd_experiments_list(self):
        return dict(experiments=[dict(experiment_id=e['experiment_id'], state=e['state'], injections_done=len(e['injections']), injections_total=len(e['method']['injections']),
                                      started_s=float(e['started_s']), finished_s=e['finished_s'], cell_solution=e['cell_solution'], syringe_solution=e['syringe_solution'])
                                 for e in sorted(self.experiments.values(), key=lambda x: x['started_s'])])

    def _cmd_design_simulate(self, n, kd_mol_m3, dh_j_mol, cell_concentration_mol_m3, syringe_concentration_mol_m3, injection_volumes_m3):
        return design_simulate(n, kd_mol_m3, dh_j_mol, cell_concentration_mol_m3, syringe_concentration_mol_m3, injection_volumes_m3)

    def _cmd_events_read(self, since_s):
        return dict(events=[{k: v for k, v in e.items() if k != '_ref'} for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def _cmd_provenance_read(self):
        libs = [dict(name=k, file=v, entries=len(_lib(k).get('substances', {})) if k != 'instrument' else len(_lib(k)['documented'])) for k, v in LIBS.items()]
        return dict(libraries=libs, shelf_species=sorted(SHELF), interactions=[dict(interaction_id=k, receptor=v['receptor'], ligand=v['ligand'] or '', evidence=v['evidence'])
                                                                              for k, v in sorted(self.interactions.items()) if v['library']])

    # ---------------------------------------------------------------- evaluator queries, faults, clock
    def _cmd_cell_inspect(self):
        whole = _add(self.cell['mix'], self.pool['c']) if self.cell['mix']['V'] > 0 else self.cell['residual']
        info = self.cell.get('eq') or {}
        return dict(volume_m3=float(whole['V']), species=self._species_map(whole), ph=info.get('ph') if self.cell['mix']['V'] > 0 else None,
                    ionic_strength_mol_m3=float(info.get('I', 0.0) * 1000.0),
                    bound_mol_m3={k: float(b * 1000.0) for k, b in info.get('bound', {}).items()} if self.cell['mix']['V'] > 0 else {},
                    temperature_k=float(self.T_s if self.T_s is not None else self.T_room), bubble=bool(self.cell['bubble'] or 'cell_bubble' in self.faults),
                    contamination_fraction=float(self.cell['contamination']))

    def _cmd_syringe_inspect(self):
        sy = self.syr
        return dict(liquid_m3=float(sy['liquid']['V'] + sy['tip_dilution']), bubble_m3=float(sy['bubble']), species=self._species_map(sy['liquid']),
                    residue=sy['residue'], tip_dilution_m3=float(sy['tip_dilution']))

    def ledger(self):
        return {k: v.value() for k, v in self.led.items()}

    def _cmd_ledger_read(self):
        return dict(ledger=self.ledger())

    def _cmd_fault_inject(self, fault, active):
        self._need_plant()
        (self.faults.add if active else self.faults.discard)(fault)
        if fault == 'power_loss':
            self._update_power()
        if fault == 'stir_motor_failure' and active:
            self.stir_hz = 0.0
        if fault == 'cell_bubble' and not active:
            self.cell['bubble'] = False
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
        """Atomic rejection: deep copy of the mutable state except the recorded data arrays and the session tables (commands never change them)."""
        d = {}
        for k, v in self.__dict__.items():
            if k in self._NOCOPY:
                continue
            if k == 'experiments':
                d[k] = {eid: dict(e) for eid, e in v.items()}
            else:
                d[k] = copy.deepcopy(v)
        return d

    def _restore(self, snap):
        for k, v in snap.items():
            self.__dict__[k] = v


# ---------------------------------------------------------------- session (agent / evaluator isolation, idempotency)
ASYNC = {'syringe.load', 'syringe.purge_refill', 'run.start', 'clean.start'}
_DATA_QUERIES = {'data.analyze', 'data.read'}
_CONTROL_GUARDED = {k for k, v in CAPS.items() if v['visibility'] == 'agent' and v['kind'] in ('device_command', 'robot_skill') and k not in UNSUPPORTED
                    and k not in ('solution.prepare', 'solution.degas', 'solution.dialyze')}


class MalvernMicrocalpeaqSession:
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
        meta = copy.deepcopy(export_malvern_microcalpeaq_contract_metadata())
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
        return any(self._foreign(op['op_id']) for op in self._host._running_ops())

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
        for key in ('solution_id', 'buffer_from'):
            sid = arguments.get(key)
            if isinstance(sid, str) and self._foreign('solution:' + sid):
                raise ActionRejected('ACCESS_DENIED')
        if capability_id == 'solution.prepare' and any(self._foreign('solution:' + s['source_id']) for s in arguments.get('sources', [])):
            raise ActionRejected('ACCESS_DENIED')
        if capability_id in _DATA_QUERIES or capability_id == 'data.analyze':
            for key in ('experiment_id', 'control_experiment_id'):
                eid = arguments.get(key)
                if isinstance(eid, str) and self._foreign('experiment:' + eid):
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
                if capability_id == 'experiments.list':
                    result = dict(experiments=[e for e in result['experiments'] if not self._foreign('experiment:' + e['experiment_id'])])
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
        if op['kind'] == 'run.start':
            e = h.experiments.get(op['experiment_id'])
            res.update(experiment_id=op['experiment_id'], injections_done=len(e['injections']) if e else 0, run_state=e['state'] if e else None)
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
    'INVALID_ARGUMENT': '参数不符合输入模式或取值范围', 'REQUEST_CONFLICT': '同一request_id对应不同的调用', 'UNKNOWN_INSTANCE': '实例不存在',
    'UNKNOWN_CAPABILITY': '能力不存在', 'UNSUPPORTED': '本配置不支持该能力', 'ACCESS_DENIED': '无权访问（真值、另一个agent的溶液、实验或进行中的操作）',
    'PLANT_REQUIRED': '尚未声明实验室条件', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'MAINS_OUT_OF_RANGE': '供电不在100–240 V ±10 %（或断电）',
    'POWER_OFF': '量热池单元未通电', 'RUN_IN_PROGRESS': '实验运行中', 'INSTRUMENT_BUSY': '装样、排气泡或清洗进行中',
    'METHOD_EXCEEDS_SYRINGE': '注射总体积超过注射器可用体积（40 µL减去约1 µL死体积）', 'INVALID_SPACING': '注射间隔须不短于注射时长',
    'UNKNOWN_SOLUTION': '溶液或储备液不存在', 'ITEM_EXISTS': 'ID已存在', 'TOO_MANY_ITEMS': '数量已达上限', 'VOLUME_OUT_OF_RANGE': '分装体积之和超过终体积',
    'INSUFFICIENT_SOLUTION': '溶液体积不够', 'UNKNOWN_SPECIES': '试剂不在架上（或是缓冲剂：缓冲剂用buffer字段）', 'INVALID_INTERACTION': '相互作用参数不完整或物质无效',
    'CLEANING_TOOL_INSERTED': '池清洗工具插在样品池中', 'CLEANING_TOOL_NOT_INSERTED': '须先插入池清洗工具', 'PIPETTE_IN_CELL': '移液器在样品池中（须先移到Rest/Load/Clean）',
    'FPA_ATTACHED': 'Fill Port Adapter还装着（进样品池前须取下）', 'FPA_NOT_ATTACHED': '须先装Fill Port Adapter', 'PIPETTE_MOVING': '移液器正在移动',
    'SYRINGE_BUSY': '柱塞正在移动', 'TUBE_PRESENT': '管座中已有PCR管', 'PIPETTE_IN_STATION': '移液器在Load位置（针在管座里）', 'NO_TUBE': '管座中没有PCR管',
    'WASH_MODULE_OFF': '洗涤模块未通电（真空泵）', 'WASH_PUMP_SHUTDOWN': '洗涤模块真空泵已停（溢流瓶满：倒空溢流瓶）', 'PLUNGER_NOT_DOWN': '柱塞不在下位（先Plunger Down）',
    'PIPETTE_NOT_AT_LOAD': '移液器须放在Load位置（针在PCR管中）', 'SYRINGE_NOT_LOADED': '注射器没有装样', 'NOTHING_TO_CLEAN': '池与注射器清洗方法都为none',
    'PIPETTE_NOT_AT_CLEAN': '注射器清洗须把移液器放在Clean位置', 'NO_METHOD': '没有方法', 'CELL_EMPTY': '样品池是空的', 'PIPETTE_NOT_IN_CELL': '移液器须放在样品池中',
    'UNKNOWN_EXPERIMENT': '实验不存在', 'NO_DATA': '实验还没有注射数据', 'COMPETITOR_REQUIRED': '竞争模型须给出竞争剂的KD、ΔH与浓度',
    'ENZYME_DH_REQUIRED': '多针酶动力学须给出反应焓（先做单针实验）', 'UNKNOWN_OPERATION': '操作不存在', 'NOT_RUNNING': '操作不在进行中',
    'POWER_OFF_INTERRUPT': '断电中止', 'EQUILIBRATION_TIMEOUT': '热平衡超时（2 h）', 'SYRINGE_FILL_FAILURE': '注射器装样失败', 'NO_FLOW': '清洗无液流',
    'WASH_OVERFLOW': '溢流瓶满，真空泵停机',
}
_PW = ['PLANT_REQUIRED', 'POWER_OFF']
_BUSY = ['RUN_IN_PROGRESS', 'INSTRUMENT_BUSY']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED'], 'power.set': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'], 'power.press_switch': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'],
    'wash.power_set': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'], 'wash.press_switch': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'],
    'method.set': _PW + ['RUN_IN_PROGRESS', 'METHOD_EXCEEDS_SYRINGE', 'INVALID_SPACING'], 'temperature.set': _PW + ['RUN_IN_PROGRESS'],
    'solution.prepare': ['PLANT_REQUIRED', 'ITEM_EXISTS', 'TOO_MANY_ITEMS', 'VOLUME_OUT_OF_RANGE', 'UNKNOWN_SOLUTION', 'INSUFFICIENT_SOLUTION', 'UNKNOWN_SPECIES'],
    'solution.degas': ['PLANT_REQUIRED', 'UNKNOWN_SOLUTION'], 'solution.dialyze': ['PLANT_REQUIRED', 'UNKNOWN_SOLUTION', 'ITEM_EXISTS', 'TOO_MANY_ITEMS'],
    'cell.load': ['PLANT_REQUIRED'] + _BUSY + ['CLEANING_TOOL_INSERTED', 'PIPETTE_IN_CELL', 'UNKNOWN_SOLUTION', 'INSUFFICIENT_SOLUTION'],
    'cell.empty': ['PLANT_REQUIRED'] + _BUSY + ['CLEANING_TOOL_INSERTED', 'PIPETTE_IN_CELL'],
    'reference.refill': ['PLANT_REQUIRED', 'RUN_IN_PROGRESS', 'UNKNOWN_SOLUTION', 'INSUFFICIENT_SOLUTION'],
    'fpa.attach': ['PLANT_REQUIRED', 'RUN_IN_PROGRESS', 'PIPETTE_IN_CELL', 'FPA_ATTACHED'], 'fpa.detach': ['PLANT_REQUIRED', 'INSTRUMENT_BUSY', 'PIPETTE_IN_CELL', 'FPA_NOT_ATTACHED'],
    'pipette.move': ['PLANT_REQUIRED'] + _BUSY + ['PIPETTE_MOVING', 'FPA_ATTACHED', 'CLEANING_TOOL_INSERTED', 'SYRINGE_BUSY'],
    'tube.place': ['PLANT_REQUIRED', 'TUBE_PRESENT', 'PIPETTE_IN_STATION', 'UNKNOWN_SOLUTION', 'INSUFFICIENT_SOLUTION'],
    'tube.remove': ['PLANT_REQUIRED', 'NO_TUBE', 'PIPETTE_IN_STATION'],
    'syringe.plunger_down': _PW + _BUSY + ['PIPETTE_MOVING'],
    'syringe.load': _PW + _BUSY + ['WASH_MODULE_OFF', 'WASH_PUMP_SHUTDOWN', 'PLUNGER_NOT_DOWN', 'PIPETTE_NOT_AT_LOAD', 'FPA_NOT_ATTACHED', 'NO_TUBE'],
    'syringe.purge_refill': _PW + _BUSY + ['PIPETTE_NOT_AT_LOAD', 'SYRINGE_NOT_LOADED'],
    'cleaning_tool.insert': ['PLANT_REQUIRED'] + _BUSY + ['PIPETTE_IN_CELL', 'CLEANING_TOOL_INSERTED'],
    'cleaning_tool.remove': ['PLANT_REQUIRED', 'INSTRUMENT_BUSY', 'CLEANING_TOOL_NOT_INSERTED'],
    'run.start': _PW + ['NO_METHOD'] + _BUSY + ['ITEM_EXISTS', 'TOO_MANY_ITEMS', 'CLEANING_TOOL_INSERTED', 'CELL_EMPTY', 'PIPETTE_NOT_IN_CELL', 'FPA_ATTACHED',
                                                 'SYRINGE_NOT_LOADED'],
    'instrument.stop': _PW,
    'clean.start': _PW + _BUSY + ['NOTHING_TO_CLEAN', 'WASH_MODULE_OFF', 'WASH_PUMP_SHUTDOWN', 'CLEANING_TOOL_NOT_INSERTED', 'PIPETTE_NOT_AT_CLEAN', 'FPA_NOT_ATTACHED'],
    'wash.empty_waste': ['PLANT_REQUIRED', 'INSTRUMENT_BUSY'], 'wash.empty_overflow': ['PLANT_REQUIRED', 'INSTRUMENT_BUSY'], 'wash.refill': ['PLANT_REQUIRED', 'INSTRUMENT_BUSY'],
    'data.analyze': ['UNKNOWN_EXPERIMENT', 'NO_DATA', 'COMPETITOR_REQUIRED', 'ENZYME_DH_REQUIRED'], 'data.read': ['UNKNOWN_EXPERIMENT'], 'experiments.list': [],
    'design.simulate': [], 'events.read': [], 'provenance.read': [],
    'stock.provide': ['PLANT_REQUIRED', 'ITEM_EXISTS', 'TOO_MANY_ITEMS', 'UNKNOWN_SPECIES'],
    'interaction.define': ['PLANT_REQUIRED', 'ITEM_EXISTS', 'TOO_MANY_ITEMS', 'INVALID_INTERACTION'],
    'cell.inspect': [], 'syringe.inspect': [], 'ledger.read': [], 'fault.inject': ['PLANT_REQUIRED'], 'sim.step': [],
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
PRECONDITIONS = {
    'plant.configure': ('首次声明时设定噪声种子；之后再声明（室温、供电、漂移）须给同一种子', ['plant.configured']),
    'power.set': ('已声明实验室条件；开机时供电在100–240 V ±10 %', ['plant.configured']), 'power.press_switch': ('同power.set', ['plant.configured']),
    'wash.power_set': ('同power.set', ['plant.configured']), 'wash.press_switch': ('同power.set', ['plant.configured']),
    'method.set': ('通电；不在运行中；注射总体积不超过注射器可用体积；每针间隔不短于时长', ['power.enabled', 'run.state']),
    'temperature.set': ('通电；不在运行中', ['power.enabled', 'run.state']),
    'solution.prepare': ('已声明实验室条件；ID唯一、溶液数≤60；分装来源存在且体积足够；试剂在架上（缓冲剂用buffer）；buffer与buffer_from至多一个', ['solutions.available']),
    'solution.degas': ('溶液存在', ['solutions.available']), 'solution.dialyze': ('溶液存在；外液ID唯一', ['solutions.available']),
    'cell.load': ('不在运行、装样或清洗中；移液器在Rest/Load/Clean（不在池中）；池清洗工具已取出；溶液体积足够（含润洗300 µL）',
                  ['run.state', 'operations.running', 'pipette.location', 'cell.cleaning_tool_inserted', 'solutions.available']),
    'cell.empty': ('不在运行、装样或清洗中；移液器不在池中；池清洗工具已取出', ['run.state', 'operations.running', 'pipette.location', 'cell.cleaning_tool_inserted']),
    'reference.refill': ('不在运行中；指定溶液时体积≥300 µL', ['run.state']),
    'fpa.attach': ('不在运行中；移液器不在池中；FPA未装', ['run.state', 'pipette.location', 'fpa.attached']),
    'fpa.detach': ('不在装样或清洗中；移液器不在池中；FPA已装', ['operations.running', 'pipette.location', 'fpa.attached']),
    'pipette.move': ('不在运行、装样或清洗中；移液器不在移动；柱塞不在移动；进样品池须取下FPA并取出池清洗工具',
                     ['run.state', 'operations.running', 'pipette.location', 'fpa.attached', 'cell.cleaning_tool_inserted']),
    'tube.place': ('管座空；移液器不在Load位置；溶液体积足够', ['loading_station.tube_solution_id', 'pipette.location', 'solutions.available']),
    'tube.remove': ('管座有管；移液器不在Load位置', ['loading_station.tube_solution_id', 'pipette.location']),
    'syringe.plunger_down': ('通电；不在运行、装样或清洗中；移液器不在移动', ['power.enabled', 'run.state', 'operations.running', 'pipette.location']),
    'syringe.load': ('通电；洗涤模块通电且真空泵可用；不在运行、装样或清洗中；柱塞在下位；移液器放在Load位置；FPA已装；管座有PCR管',
                     ['power.enabled', 'wash.powered', 'wash.pump_ok', 'run.state', 'operations.running', 'syringe.plunger_position_m', 'pipette.location', 'fpa.attached',
                      'loading_station.tube_solution_id']),
    'syringe.purge_refill': ('通电；不在运行、装样或清洗中；移液器放在Load位置且管座有管；注射器已装样',
                             ['power.enabled', 'run.state', 'operations.running', 'pipette.location', 'loading_station.tube_solution_id', 'syringe.state']),
    'cleaning_tool.insert': ('不在运行、装样或清洗中；移液器不在池中；工具未插入', ['run.state', 'operations.running', 'pipette.location', 'cell.cleaning_tool_inserted']),
    'cleaning_tool.remove': ('不在清洗中；工具已插入', ['operations.running', 'cell.cleaning_tool_inserted']),
    'run.start': ('通电；有方法；不在运行、装样或清洗中；ID唯一、实验数≤40；池清洗工具已取出；样品池已装；移液器放在样品池中；FPA已取下；注射器已装样',
                  ['power.enabled', 'method.current', 'run.state', 'operations.running', 'experiments.count', 'cell.cleaning_tool_inserted', 'cell.solution_id',
                   'pipette.seated_in_cell', 'fpa.attached', 'syringe.state']),
    'instrument.stop': ('通电', ['power.enabled']),
    'clean.start': ('通电；洗涤模块通电且真空泵可用；不在运行、装样或清洗中；至少一个方法；池清洗须插入工具；注射器清洗须移液器在Clean位置并装FPA',
                    ['power.enabled', 'wash.powered', 'wash.pump_ok', 'run.state', 'operations.running', 'cell.cleaning_tool_inserted', 'pipette.location', 'fpa.attached']),
    'wash.empty_waste': ('不在装样或清洗中', ['operations.running']), 'wash.empty_overflow': ('不在装样或清洗中', ['operations.running']),
    'wash.refill': ('不在装样或清洗中', ['operations.running']),
    'data.analyze': ('实验存在且有注射数据；竞争模型须给竞争剂参数；多针酶动力学须给反应焓；fix只用于One Set、Two Sets与Competitive模型（n2/kd2/dh2只用于Two Sets，'
                     'offset只在control=fitted_offset时），否则INVALID_ARGUMENT', ['experiments.count']),
    'data.read': ('实验存在', ['experiments.count']),
    'stock.provide': ('已声明实验室条件；ID唯一、储备液数≤60；组分不是缓冲剂', ['solutions.available']),
    'interaction.define': ('已声明实验室条件；ID唯一、≤64个；所选模型的参数齐全', ['plant.configured']), 'fault.inject': ('已声明实验室条件', ['plant.configured']),
}
MODEL_NONE_ZH = {
    'plant.configure': '只声明宿主实验室条件', 'method.set': '方法只在过程层', 'temperature.set': '温度只在过程层', 'solution.prepare': '溶液没有场景几何（实验台抽象）',
    'solution.degas': '同左', 'solution.dialyze': '同左', 'cell.load': '样品池内部与液体没有场景几何（池口为接口点）', 'cell.empty': '同左',
    'reference.refill': '参比池盖为固定外观（换液是机器人抽象）', 'fpa.attach': 'FPA为固定外观（存放位与装上不分别建几何）', 'fpa.detach': '同左',
    'tube.place': 'PCR管没有场景几何（管座为接口点）', 'tube.remove': '同左', 'cleaning_tool.insert': '池清洗工具为固定外观', 'cleaning_tool.remove': '同左',
    'clean.start': '清洗液流只在过程层', 'wash.empty_waste': '瓶中液位只在过程层（瓶为固定外观）', 'wash.empty_overflow': '同左', 'wash.refill': '同左',
    'data.analyze': '只读', 'data.read': '只读', 'experiments.list': '只读', 'design.simulate': '只读计算', 'events.read': '只读', 'provenance.read': '只读',
    'stock.provide': '储备液没有场景几何', 'interaction.define': '只在过程层', 'cell.inspect': '只读真值（evaluator）', 'syringe.inspect': '只读真值（evaluator）',
    'ledger.read': '只读账目（evaluator）', 'fault.inject': '评估者故障注入', 'sim.step': '推进时钟'}
SCENE_MIRRORED = {'pipette.lift_m', 'pipette.swing_rad', 'syringe.plunger_position_m', 'lamp.status', 'lamp.wash_status'}
EXEC_TEXT = {
    'syringe.load': ('约60 s后注射器装满（操作succeeded，syringe.state=loaded）', '取消、断电或条件丢失（FPA取下、移液器离开Load位置）：操作failed，已吸入的液体进废液，注射器为空'),
    'syringe.purge_refill': ('约20 s后柱塞回到上位、注射器重新吸满（操作succeeded）', '取消或断电：柱塞停在当前位置，注射器内容可能已排回PCR管'),
    'run.start': ('全部注射完成并经过最后一针的间隔后实验completed（操作succeeded）', '取消/Stop：实验stopped（已记录的数据保留，操作cancelled）；断电：failed（POWER_OFF_INTERRUPT）；热平衡2 h未达标：failed（EQUILIBRATION_TIMEOUT）'),
    'clean.start': ('方法时长结束后clean.state=completed（操作succeeded）', '取消/Stop：stopped（注射器干燥中断时留下甲醇残留）；瓶空或泵故障：failed（NO_FLOW）；溢流瓶满：failed（WASH_OVERFLOW）'),
}
RESOURCES = {'power.set': ['mains'], 'power.press_switch': ['mains'], 'wash.power_set': ['mains'], 'wash.press_switch': ['mains'], 'solution.prepare': ['bench'],
             'solution.degas': ['bench'], 'solution.dialyze': ['bench'], 'cell.load': ['robot_arm', 'loading_syringe', 'sample_cell'], 'cell.empty': ['robot_arm', 'loading_syringe'],
             'reference.refill': ['robot_arm', 'reference_cell'], 'fpa.attach': ['robot_arm', 'fpa'], 'fpa.detach': ['robot_arm', 'fpa'], 'pipette.move': ['robot_arm', 'pipette', 'host_clock'],
             'tube.place': ['robot_arm', 'loading_station'], 'tube.remove': ['robot_arm', 'loading_station'], 'syringe.plunger_down': ['plunger_drive', 'host_clock'],
             'syringe.load': ['plunger_drive', 'wash_module', 'host_clock'], 'syringe.purge_refill': ['plunger_drive', 'host_clock'],
             'cleaning_tool.insert': ['robot_arm', 'sample_cell'], 'cleaning_tool.remove': ['robot_arm', 'sample_cell'],
             'run.start': ['calorimeter', 'plunger_drive', 'stirrer', 'host_clock'], 'clean.start': ['wash_module', 'host_clock'],
             'wash.empty_waste': ['robot_arm', 'waste_bottle'], 'wash.empty_overflow': ['robot_arm', 'overflow_bottle'], 'wash.refill': ['robot_arm', 'reagent_bottles'],
             'plant.configure': ['room', 'mains'], 'fault.inject': ['evaluator'], 'sim.step': ['host_clock'], 'data.analyze': ['analysis_software']}
OBSERVES = {'power.set': ['power.enabled', 'lamp.status'], 'power.press_switch': ['power.enabled', 'lamp.status'], 'wash.power_set': ['wash.powered', 'lamp.wash_status'],
            'wash.press_switch': ['wash.powered', 'lamp.wash_status'], 'method.set': ['method.current'], 'temperature.set': ['temperature.setpoint_k', 'temperature.cell_k'],
            'cell.load': ['cell.solution_id', 'cell.loaded_volume_m3'], 'pipette.move': ['pipette.location', 'pipette.lift_m', 'pipette.swing_rad'],
            'syringe.plunger_down': ['syringe.plunger_position_m', 'syringe.state'], 'syringe.load': ['syringe.state', 'syringe.plunger_position_m', 'operations.running'],
            'syringe.purge_refill': ['syringe.state', 'operations.running'], 'run.start': ['run.state', 'run.injection_number', 'dp.power_w', 'run.remaining_time_s'],
            'clean.start': ['clean.state', 'operations.running', 'wash.bottle_fraction'], 'fpa.attach': ['fpa.attached'], 'fpa.detach': ['fpa.attached'],
            'tube.place': ['loading_station.tube_solution_id'], 'cleaning_tool.insert': ['cell.cleaning_tool_inserted'], 'reference.refill': ['reference.age_s']}
_WB, _PR, _BR, _UM, _AM, _GS, _TN1, _TN2, _GB, _IU, _SIM = ('user_workbook', 'manufacturer_primary', 'manufacturer_brochure', 'manufacturer_user_manual',
                                                         'manufacturer_analysis_manual', 'manufacturer_getting_started', 'manufacturer_tn240816', 'manufacturer_tn161202',
                                                         'nist_goldberg_2002', 'iupac_2001', 'simulation_assumption')
CAP_SOURCES = {k: [_SIM] for k in CAPS}
CAP_SOURCES.update({k: [_UM, _BR] for k in ('power.set', 'power.press_switch', 'wash.power_set', 'wash.press_switch')})
CAP_SOURCES.update({'method.set': [_UM, _GS, _TN1, _BR], 'temperature.set': [_UM], 'solution.prepare': [_GS, _GB, _SIM], 'solution.degas': [_UM, _SIM],
                    'solution.dialyze': [_UM, _GB, _SIM], 'cell.load': [_GS, _UM, _BR, _SIM], 'cell.empty': [_GS, _SIM], 'reference.refill': [_UM, _GS],
                    'fpa.attach': [_GS, _UM], 'fpa.detach': [_GS, _UM], 'pipette.move': [_GS, _UM, _SIM], 'tube.place': [_GS], 'tube.remove': [_GS],
                    'syringe.plunger_down': [_UM], 'syringe.load': [_UM, _GS, _TN1, _SIM], 'syringe.purge_refill': [_UM, _SIM], 'cleaning_tool.insert': [_UM, _GS],
                    'cleaning_tool.remove': [_UM, _GS], 'run.start': [_UM, _BR, _TN1, _TN2, _GB, _IU, _SIM], 'instrument.stop': [_UM], 'clean.start': [_UM, _GS, _SIM],
                    'wash.empty_waste': [_UM], 'wash.empty_overflow': [_UM], 'wash.refill': [_UM], 'data.analyze': [_AM, _TN1, _SIM], 'data.read': [_AM],
                    'experiments.list': [_AM], 'design.simulate': [_TN1, _AM], 'provenance.read': [_GB, _IU, _TN1, _GS], 'stock.provide': [_GB, _SIM],
                    'interaction.define': [_SIM, _GB], 'fault.inject': [_SIM, _UM], 'plant.configure': [_BR, _SIM]})
CAP_SOURCES.update({k: [_BR, _WB] for k in UNSUPPORTED})
PARAM_ZH = {'room_k': '实验室室温', 'mains_voltage_v': '供电电压', 'noise_seed': '噪声随机数种子（只在首次声明时生效）', 'room_drift_k_per_s': '室温漂移速率',
            'reference_age_s': '参比池上次换水后的时间（蒸发）'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.titration_calorimetry_process.limitations。'
CAP_LIMITS = {
    'method.set': ['参比功率0–20 µcal/s（厂家建议3–10 µcal/s；输入上限为假设）、搅拌0–20 s⁻¹（0–1200 rpm，上限为假设）、每针0.01–40 µL；厂家没有给出软件的全部输入范围。'],
    'solution.prepare': ['称量误差σ=0.5 %、新配缓冲液pH误差σ=0.02（假设）；用buffer_from取同一缓冲液（透析外液）时缓冲成分完全一致（无不匹配热）；缓冲剂按NaOH/HCl滴定到pH（Debye–Hückel活度修正）。'],
    'cell.load': ['装样前用装样针吸净：池壁与池底残留约4 µL（假设），润洗把残留的旧液降到2 %（假设）；装样<200 µL时有效体积不满（气泡、噪声与台阶），200–280 µL时按欠量比例可能带入气泡；'
                  '样品在室温装入（随后与夹套热平衡）。'],
    'syringe.load': ['装样约60 s（假设）：柱塞从下位升到上位，真空经针从PCR管吸入40 µL减去残留膜，另有约15 µL经灌注口进废液（假设）；PCR管不足时注射器带入等体积气泡（厂家建议管中约60 µL，'
                     'TN240816写装满注射器需75 µL，两者不一致已记录）。'],
    'syringe.purge_refill': ['把注射器内容排回PCR管再吸满（约20 s，假设）；管中液体不足时仍留气泡。'],
    'run.start': ['热平衡判据：夹套偏离设定<1 mK、池液与夹套差<0.5 mK、热流<0.002 µcal/s且至少120 s（假设）；搅拌从热平衡开始；注射体积按柱塞行程，实际体积σ=0.3 %（厂家<1 %@2 µL）；'
                  '注射器有气泡时推注随气泡可压缩而滞后（时间常数每µL气泡0.5 s，假设），液体推完后注入空气（池中气泡、噪声与台阶）；第一针前针尖扩散约0.15 µL（时间常数600 s，假设）；'
                  '热平衡还要求DP响应已收敛（搅拌开始的阶跃）。'],
    'clean.start': ['方法时长与用量（池Rinse 5 min/30 mL水、Wash 10 min/3 mL洗涤剂+30 mL水、Soak 30 min；注射器Rinse 12 min、Wash 15 min，最后8 min甲醇干燥）除8 min干燥外均为假设；'
                    '残留按方法的去除系数下降（小分子/大分子分别给出，假设）；滤芯堵塞时输送量降到25 %。'],
    'data.analyze': ['自动基线：注射前10 s与下一针前10 s的均值连线（假设）；积分按1 s数据点；拟合用Levenberg–Marquardt（数值雅可比、多初值、log K限于10^−3–10^14 M⁻¹），'
                     '误差来自协方差×残差方差；fix对One Set、Two Sets与Competitive模型有效（n/kd/dh/offset与n2/kd2/dh2）；'
                     'One Set of Sites等模型按MicroCal（Wiseman）置换体积公式；Bin评语阈值：初始基线偏离参比功率>1 µcal/s（厂家）、总热量<16 µcal（TN240816），其余为假设。'],
    'design.simulate': ['只按一组位点模型；不含稀释热、缓冲液不匹配、针尖扩散与噪声。'],
    'pipette.move': ['升起/放下0.05 m/s、转动1 rad/s（假设，手动操作的机器人技能）。'],
}
LIMITATIONS = [
    '选定配置：MicroCal PEAQ-ITC标准型（手动装样）与洗涤模块；PEAQ-ITC Automated（384样品、370 µL装样、91 kg）与控制电脑不在配置内。',
    '量热为集总模型：夹套一阶控温、池液一阶热平衡、DP一阶响应（High 8 s为厂家值，Low/None为假设）；没有池体内部的温度场与热流计的电路模型。',
    '化学为有效体积内的均匀混合：注入液先进入未混合区，按搅拌决定的时间常数混入；置换出的液体离开有效体积不再计热；反应为质量作用平衡（给定kon时按一阶弛豫）。',
    '缓冲液质子化用Goldberg 2002的pK、ΔH、ΔCp（298.15 K）与扩展Debye–Hückel（B=1.6）；结合常数不随pH变化（按实验pH给出的表观值），质子释放只影响缓冲液的质子化热。',
    '稀释热只有丙醇与蔗糖（IUPAC）与电解质的Debye–Hückel过量焓；其它物质的稀释热未知（为0）；甲醇残留的混合焓−7.3 kJ/mol为文献量级的假设值。',
    '溶液、PCR管、装样针、FPA与池清洗工具没有场景几何；装样、换液、放管与移液器移动为宿主/机器人抽象，没有接触与液体仿真。',
    '数据分析是厂家软件功能的等价实现（不是原软件）：基线、积分与Bin判据为简化规则；实验数据只在运行层内存中。',
    '仿真热量只含注射体积精密度（0.3 %）与DP噪声/漂移的散布，没有真实数据的基线与积分散布，所以c>1000时拟合的KD误差会比实测数据小（不可当真）。',
    '稀释热与Debye–Hückel过量焓把摩尔浓度（mol/L）当作质量摩尔浓度（mol/kg）使用，只适用于稀溶液；温度改变时快速平衡（缓冲液质子化与瞬时结合）的移动计入溶液热容'
    '（不记反应热，也不进DP），给定kon的慢结合在温度改变后的弛豫按反应热记账（会出现在DP中）。',
]


def _sampling_text(k):
    if k in ('dp.power_w', 'temperature.cell_k', 'truth.dp_noise_free_w', 'truth.heat_rate_w'):
        return 'Value of the last 0.2 s host grid point (DP noise drawn per grid point; recorded data use one point per 1 s).'
    if k.startswith(('truth.', 'pipette.', 'syringe.', 'wash.', 'run.', 'loading_station.', 'reference.', 'clean.', 'operations', 'warnings', 'errors', 'stir.')) \
            or k == 'clock.time_s':
        return 'Integrated on the fixed 0.2 s host grid; between grid points the last grid value is held.'
    return 'Changes when a command is accepted; no other time dynamics.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if (k in _CONTROL_GUARDED or k in _DATA_QUERIES or k.startswith('solution.') or CAPS[k]['visibility'] != 'agent') else []
    return list(dict.fromkeys(CAP_ERRORS.get(k, []) + _COMMON_ERRORS + extra))


def _foreign_text(k):
    if k in _CONTROL_GUARDED:
        return '；另一个agent的异步操作（装样、排气泡、运行或清洗）进行中时agent调用被拒（ACCESS_DENIED；未通电时开机除外）；只能用自己配的溶液'
    if k in _DATA_QUERIES:
        return '；agent只能读自己的实验（ACCESS_DENIED）'
    if k.startswith('solution.'):
        return '；agent只能用自己配的溶液与评测者给的储备液（ACCESS_DENIED）'
    return ''


def _pre_slots(k):
    errs = CAP_ERRORS.get(k, [])
    ids = (['plant.configured'] if 'PLANT_REQUIRED' in errs else []) + (['power.enabled'] if 'POWER_OFF' in errs else []) + list(PRECONDITIONS[k][1])
    return list(dict.fromkeys(ids + (['operation.started_by_other_agent'] if k in _CONTROL_GUARDED else [])))


def _pre_text(k):
    errs, text = CAP_ERRORS.get(k, []), PRECONDITIONS[k][0]
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


_THERM = ['temperature.cell_k', 'truth.cell_content_temperature_k', 'truth.jacket_temperature_k', 'truth.reference_temperature_k']
_DP = ['dp.power_w', 'truth.dp_noise_free_w']
_CHEM = ['truth.cell_species_mol_m3', 'truth.cell_ph', 'truth.cell_bound_mol_m3', 'truth.unmixed_volume_m3', 'truth.heat_rate_w', 'truth.cell_bubble']
_SYR = ['syringe.plunger_position_m', 'syringe.available_volume_m3', 'syringe.state', 'truth.syringe_liquid_m3', 'truth.syringe_species_mol_m3', 'truth.syringe_bubble_m3',
        'truth.syringe_residue']
_PIP = ['pipette.location', 'pipette.lift_m', 'pipette.swing_rad', 'pipette.seated_in_cell']
_WASH = ['wash.bottle_fraction', 'truth.wash_volumes_m3', 'wash.pump_ok']
_OPS = ['operations.running', 'warnings.active', 'errors.active']
_RUN = ['run.state', 'run.injection_number', 'run.remaining_time_s', 'stir.rate_hz']
_PWR_EFFECT = ['power.enabled', 'wash.powered', 'lamp.status', 'lamp.wash_status', 'dp.power_w', 'temperature.cell_k', 'truth.dp_noise_free_w', 'operations.running',
               'run.state', 'run.remaining_time_s', 'stir.rate_hz', 'clean.state', 'syringe.state', 'syringe.available_volume_m3', 'errors.active', 'truth.syringe_liquid_m3',
               'truth.syringe_species_mol_m3', 'truth.syringe_bubble_m3', 'truth.syringe_residue', 'truth.wash_volumes_m3', 'wash.bottle_fraction']
_CELL_NOW = ['cell.solution_id', 'cell.loaded_volume_m3', 'truth.cell_species_mol_m3', 'truth.cell_ph', 'truth.cell_bound_mol_m3', 'truth.cell_bubble',
             'truth.unmixed_volume_m3']
_u = lambda *groups: sorted(set(x for g in groups for x in g))                    # noqa: E731
EFFECTS = {
    'plant.configure': _u(['plant.configured', 'truth.jacket_temperature_k', 'truth.cell_content_temperature_k', 'truth.reference_temperature_k',
                           'truth.reference_volume_m3', 'reference.age_s', 'wash.pump_ok'], _PWR_EFFECT),
    'power.set': _PWR_EFFECT, 'power.press_switch': _PWR_EFFECT,
    'wash.power_set': ['wash.powered', 'lamp.wash_status', 'operations.running', 'clean.state', 'syringe.state', 'syringe.available_volume_m3', 'errors.active',
                       'truth.syringe_liquid_m3', 'truth.syringe_species_mol_m3', 'truth.syringe_bubble_m3', 'truth.syringe_residue', 'truth.wash_volumes_m3',
                       'wash.bottle_fraction'],
    'method.set': ['method.current', 'temperature.setpoint_k', 'dp.reference_power_w', 'dp.feedback'], 'temperature.set': ['temperature.setpoint_k', 'method.current'],
    'solution.prepare': ['solutions.available'], 'solution.degas': [], 'solution.dialyze': ['solutions.available'], 'stock.provide': ['solutions.available'],
    'cell.load': _u(_CELL_NOW, ['truth.cell_contamination_fraction', 'truth.cell_content_temperature_k', 'truth.wash_volumes_m3', 'wash.bottle_fraction']),
    'cell.empty': _CELL_NOW,
    'reference.refill': ['reference.age_s', 'truth.reference_volume_m3', 'truth.reference_temperature_k', 'truth.faults'],
    'fpa.attach': ['fpa.attached'], 'fpa.detach': ['fpa.attached'], 'pipette.move': ['pipette.location', 'pipette.seated_in_cell'],
    'tube.place': ['loading_station.tube_solution_id', 'loading_station.tube_volume_m3'], 'tube.remove': ['loading_station.tube_solution_id', 'loading_station.tube_volume_m3'],
    'syringe.plunger_down': ['syringe.state', 'syringe.available_volume_m3'], 'syringe.load': ['syringe.state', 'syringe.available_volume_m3', 'operations.running'],
    'syringe.purge_refill': ['syringe.state', 'syringe.available_volume_m3', 'operations.running'],
    'cleaning_tool.insert': ['cell.cleaning_tool_inserted'], 'cleaning_tool.remove': ['cell.cleaning_tool_inserted'],
    'run.start': ['run.state', 'run.experiment_id', 'run.injection_number', 'run.remaining_time_s', 'experiments.count', 'operations.running', 'stir.rate_hz',
                  'temperature.setpoint_k', 'dp.reference_power_w', 'dp.feedback'],
    'instrument.stop': _u(['operations.running', 'run.state', 'run.remaining_time_s', 'stir.rate_hz', 'clean.state', 'errors.active'], _SYR, ['truth.wash_volumes_m3',
                                                                                                                                               'wash.bottle_fraction']),
    'clean.start': _u(['clean.state', 'operations.running', 'errors.active'], _CELL_NOW, _SYR, ['truth.wash_volumes_m3', 'wash.bottle_fraction']),
    'wash.empty_waste': ['truth.wash_volumes_m3', 'wash.bottle_fraction'],
    'wash.empty_overflow': ['truth.wash_volumes_m3', 'wash.bottle_fraction', 'wash.pump_ok', 'errors.active'],
    'wash.refill': ['truth.wash_volumes_m3', 'wash.bottle_fraction', 'errors.active'], 'interaction.define': [],
    'fault.inject': _u(['truth.faults', 'truth.cell_bubble', 'truth.reference_volume_m3'], _PWR_EFFECT), 'sim.step': ['clock.time_s'],
}
EFFECTS['wash.press_switch'] = EFFECTS['wash.power_set']
_ALL_PHYS = sorted(k for k in SLOTS if k not in ('plant.configured', 'method.current', 'dp.reference_power_w', 'dp.feedback', 'temperature.setpoint_k',
                                                 'solutions.available', 'fpa.attached', 'cell.cleaning_tool_inserted', 'loading_station.tube_solution_id',
                                                 'operation.started_by_other_agent', 'truth.faults', 'cell.solution_id', 'cell.loaded_volume_m3'))
# Later (on-step) effects per capability: the slots whose evolution differs from the same host without the call (checked by the with / without-call
# discovery in the tests).  Bench and bookkeeping calls that act only through a later call have none.
ON_STEP = {k: _ALL_PHYS for k in ('plant.configure', 'power.set', 'power.press_switch', 'fault.inject', 'sim.step')}
ON_STEP.update({
    'wash.power_set': _u(_OPS, _SYR, _WASH, ['loading_station.tube_volume_m3', 'clean.state']),
    'method.set': _u(_THERM, _DP, _CHEM), 'temperature.set': _u(_THERM, _DP, _CHEM),
    'cell.load': _u(_THERM, _DP, _CHEM, ['warnings.active']), 'cell.empty': _u(_THERM, _DP, _CHEM, ['warnings.active']),
    'reference.refill': _u(_THERM, _DP, ['warnings.active']),
    'pipette.move': _u(_PIP, _DP, _CHEM, _SYR), 'syringe.plunger_down': _u(_SYR, _DP, _CHEM, _WASH, ['loading_station.tube_volume_m3', 'warnings.active']),
    'syringe.load': _u(_SYR, _OPS, _WASH, ['loading_station.tube_volume_m3']), 'syringe.purge_refill': _u(_SYR, _OPS, ['loading_station.tube_volume_m3']),
    'run.start': _u(_RUN, _OPS, _SYR, _CHEM, _DP, _THERM), 'instrument.stop': _u(_RUN, _OPS, _SYR, _CHEM, _DP, _THERM, _WASH, ['clean.state', 'loading_station.tube_volume_m3']),
    'clean.start': _u(_OPS, ['clean.state'], _WASH, _SYR, _DP, _THERM, _CHEM),
    'wash.empty_waste': _u(_OPS, _WASH), 'wash.empty_overflow': _u(_OPS, _WASH), 'wash.refill': _u(_OPS, _WASH),
    'interaction.define': _u(_CHEM, _DP),
})
ON_STEP['wash.press_switch'] = ON_STEP['wash.power_set']
ON_STEP_ZH = {
    'plant.configure': '声明后所有过程按0.2 s网格演化（室温驱动夹套与池温；参比池蒸发）', 'fault.inject': '故障作用于随后的各物理过程（断电、泵、滤芯、注射器、搅拌、气泡、参比池）',
    'power.set': '通电后夹套控温、DP测量与柱塞驱动工作；断电后温度回落室温，进行中的运行、装样与清洗失败', 'power.press_switch': '同power.set',
    'wash.power_set': '洗涤模块断电时进行中的装样与清洗失败（真空泵）', 'wash.press_switch': '同wash.power_set',
    'method.set': '夹套按新的目标温度变温（池温与DP随之变化，池内平衡与pH按新温度重新求解）；新的参比功率与反馈改变DP基线、响应与噪声',
    'temperature.set': '夹套按新的目标温度变温（池温、DP随之变化，池内平衡与pH按新温度重新求解；慢结合随后弛豫）',
    'cell.load': '室温装入的样品与夹套热平衡（DP偏转、池温变化）；有注射器在池中时针尖扩散的滴定剂与样品反应', 'cell.empty': '池中热容变化使DP与池温的演化不同',
    'reference.refill': '室温新水与夹套热平衡（DP偏转），参比池液位恢复使基线回到参比功率', 'pipette.move': '移液器按升起—转动—放下运动；离开样品池后针尖扩散停止（池内化学与注射器液量随之不同）',
    'syringe.plunger_down': '柱塞约10 s推到底，排出的液体按针尖位置进入样品池（放热）、PCR管或废液', 'syringe.load': '约60 s内柱塞上行、从PCR管吸入滴定剂（多余的进废液）',
    'syringe.purge_refill': '约20 s内排回PCR管再吸满（气泡排出）', 'run.start': '热平衡、搅拌、初始延迟与逐针注射：柱塞下行、注入与混合、反应放热、DP峰与运行进度',
    'instrument.stop': '停止后不再注射与搅拌（或清洗、装样中止），之后的DP、池内化学、注射器与瓶液位的演化不同',
    'clean.start': '按方法时长消耗洗涤剂/甲醇/水、充入废液；注射器干燥；池被排空（热容变化影响DP与池温）', 'wash.empty_waste': '之后清洗时废液瓶不再溢流（停泵）',
    'wash.empty_overflow': '同左', 'wash.refill': '之后清洗不再因瓶空失败', 'interaction.define': '池中同时有两种物质时下一步按新的相互作用求平衡并放热', 'sim.step': '0.2 s网格积分',
}


def _params():
    P = {}

    def doc(name, val, unit, meaning, src):
        P[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)

    def asm(name, val, unit, meaning, unc, src=(_SIM,)):
        P[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=list(src), uncertainty=unc)
    doc('cell_volume_m3', V0, 'm3', '样品池有效体积200 µL（硬币形哈氏合金池）', [_BR, _WB])
    doc('sample_volume_m3', V_LOAD_MIN, 'm3', '样品装载量280 µL（区别于有效体积）', [_BR, _WB])
    doc('syringe_volume_m3', V_SYR, 'm3', '滴定注射器40 µL', [_BR, _WB])
    doc('injection_precision_rel', DOC['injection_precision_rel_at_2ul'], '1', '注射体积精密度<1 %（2 µL）', [_BR, _WB])
    doc('temperature_range_k', list(T_RANGE), 'K', '实验温度2–80 °C', [_BR, _WB, _UM])
    doc('temperature_stability_k', DOC['temperature_stability_k'], 'K', '温度稳定性±0.00012 °C', [_BR, _WB])
    doc('noise_high_w', DOC['noise_w'], 'W', '标称热功率噪声0.15 ncal/s', [_BR, _WB])
    doc('response_time_high_s', DOC['response_time_s'], 's', '响应时间常数8 s（达到峰高63 %的时间；不是平衡等待时间，也不是反应动力学常数）', [_BR, _WB])
    doc('ambient_temperature_k', list(AMBIENT), 'K', '工作环境10–28 °C', [_BR, _WB])
    doc('voltage_range_v', list(MAINS), 'V', '外部电源100–240 V', [_BR])
    asm('mains_tolerance_rel', 0.10, '1', '运行层接受的供电偏差（100–240 V的±10 %，超出时MAINS_OUT_OF_RANGE）', '假设（厂家只给100–240 V）')
    doc('mass_kg', DOC['mass_kg'], 'kg', '量热仪+洗涤站13.6 kg', [_BR, _WB])
    doc('feedback_modes', FEEDBACK, '1', '反馈High/Low/None（High最快，None最灵敏）', [_UM, _BR])
    doc('reference_power_guidance_w', DOC['reference_power_guidance_w'], 'W', '参比功率建议：大放热约10、未知约5、大吸热约0.5 µcal/s', [_UM])
    doc('reference_power_recommended_w', DOC['reference_power_w'], 'W', 'PEAQ-ITC推荐参比功率3–10 µcal/s', [_TN1])
    doc('baseline_within_w', DOC['baseline_within_w_of_reference'], 'W', '基线应在参比功率±1 µcal/s以内', [_UM])
    doc('stir_rate_recommended_hz', DOC['stir_rpm_recommended'] / 60.0, '1/s', '推荐搅拌750 rpm', [_TN1, _GS])
    doc('initial_delay_s', DOC['initial_delay_s'], 's', '初始延迟标准值60 s', [_UM, _GS])
    doc('injection_spacing_s', DOC['spacing_s'], 's', '注射间隔典型90–180 s', [_UM])
    doc('typical_method_volumes_m3', dict(first=4e-10, each=2e-9), 'm3', '19 Injections方法：第一针0.4 µL、其后每针2 µL', [_GS, _TN1])
    doc('typical_method_duration_s', 4.0, 's', '19 Injections方法：每针时长4 s', [_GS])
    doc('typical_method_spacing_s', 150.0, 's', '19 Injections方法：间隔150 s', [_GS])
    doc('pcr_tube_titrant_m3', DOC['ligand_tube_m3'], 'm3', 'PCR管中约60 µL滴定剂', [_GS])
    doc('loading_syringe_fill_m3', DOC['loading_syringe_fill_m3'], 'm3', '装样针吸约300 µL', [_GS])
    doc('heat_guidelines_j', dict(DOC['heat_guidelines']), 'J', '热量建议：第二针理想>2.5 µcal、最低1 µcal；总热量>16 µcal', [_TN1])
    doc('c_value_range', [5.0, 500.0], '1', 'c值宜5–500', [_TN1])
    asm('water_ionization_pk', 13.995, '1', '水的电离pKw（298.15 K，I = 0）', '标准文献值（Olofsson & Hepler 1975，Goldberg 2002引用为75OLO/HEP）；数值未在缓存文献中逐字出现',
        [_GB, _SIM])
    doc('water_ionization_dh_j_mol', 55810.0, 'J/mol', '水的电离焓55.81 kJ/mol（298.15 K；IUPAC中和热−55.81 kJ/mol的逆反应）', [_IU])
    doc('water_ionization_dcp_j_k_mol', -224.0, 'J/(K*mol)', '水的电离热容变化−224 J/(K·mol)（298.15 K）', [_IU])
    doc('debye_hueckel_b', DEBYE_B, 'kg^0.5/mol^0.5', '扩展Debye–Hückel参数B=1.6 kg^1/2 mol^−1/2', [_GB])
    UNITS = {'response_time_s': 's', 'noise_w': 'W', 'baseline_drift_w_per_sqrt_s': 'W/s^0.5', 'dp_max_w': 'W', 'stirring_heat_w_at_750': 'W',
             'stirring_heat_exponent': '1', 'mixing_time_s_at_750': 's', 'unstirred_mixing_time_s': 's', 'jacket_tau_s': 's', 'jacket_max_rate_k_s': 'K/s',
             'cell_equilibration_tau_s': 's', 'equilibration_min_s': 's', 'equilibration_heat_flow_w': 'W', 'equilibration_timeout_s': 's',
             'injection_precision_rel': '1', 'first_injection_leak_m3': 'm3', 'first_injection_leak_tau_s': 's', 'blank_heat_j_per_m3': 'J/m3',
             'reference_full_m3': 'm3', 'reference_offset_w_per_m3': 'W/m3', 'lift_speed_m_s': 'm/s', 'swing_speed_rad_s': 'rad/s', 'plunger_speed_m_s': 'm/s',
             'syringe_load_s': 's', 'purge_refill_s': 's', 'load_excess_m3': 'm3', 'bubble_lag_s_per_m3': 's/m3', 'cell_residual_m3': 'm3', 'syringe_residual_m3': 'm3', 'rinse_volume_m3': 'm3',
             'rinse_residual_factor': '1', 'syringe_water_residue_m3': 'm3', 'syringe_methanol_residue_m3': 'm3', 'methanol_mixing_dh_j_mol': 'J/mol',
             'bottle_capacity_m3': 'm3', 'clogged_filter_factor': '1', 'outgassing_threshold_k': 'K', 'outgassing_spike_w': 'W', 'outgassing_rate_per_s': '1/s',
             'poor_load_noise_factor': '1', 'electrical_power_w': 'W', 'weighing_error_rel': '1', 'ph_preparation_error': '1', 'degassed_hold_s': 's'}
    for k, unit in UNITS.items():
        asm(k, copy.deepcopy(ENG[k]), unit, ENG_ZH.get(k, k), '工程值（见instrument_model.json的engineering说明）')
    asm('reference_evaporation_m3_s', ENG['reference_evaporation_m3_per_day'] / 86400.0, 'm3/s', '参比池蒸发速率（每天约1.5 µL）', '工程值（厂家只建议每周换水）')
    for group, methods in (('cell', ENG['cell_methods']), ('syringe', ENG['syringe_methods'])):
        fields = [('duration_s', 's', '时长'), ('water_m3', 'm3', '水用量'), ('detergent_m3', 'm3', '洗涤剂用量'), ('small_factor', '1', '小分子残留去除系数'),
                  ('macro_factor', '1', '大分子残留去除系数')] + ([('drying_s', 's', '甲醇干燥时长（厂家约8 min）'), ('methanol_m3', 'm3', '甲醇用量')] if group == 'syringe' else [])
        for f, unit, zh in fields:
            asm(f'{group}_method_{f}', {m: v[f] for m, v in methods.items()}, unit, ('池' if group == 'cell' else '注射器') + '清洗方法的' + zh,
                '工程值（syringe drying约8 min为厂家）' if f == 'drying_s' else '工程值')
    asm('dh_correction_note', 'Goldberg 2002 eq. 13-14', '1', '缓冲液pK与ΔH的离子强度修正用扩展Debye–Hückel；A_m(T)=1.10708−1.54508e−3 T+5.95584e−6 T²（Clarke–Glew拟合，1.1758@298.15 K）',
        'A_m公式未在缓存文献中逐字给出（298.15 K值1.1758可核对）', [_GB, _SIM])
    return P


ENG_ZH = {
    'response_time_s': '各反馈模式的响应时间常数（High 8 s厂家，Low 20 s、None 60 s假设）', 'noise_w': '各反馈模式的DP噪声（High 0.15 ncal/s厂家，其余假设）',
    'baseline_drift_w_per_sqrt_s': '基线随机漂移强度（600 s均值回复）', 'dp_max_w': 'DP量程上限（100 µcal/s）', 'stirring_heat_w_at_750': '750 rpm搅拌摩擦热0.25 µcal/s',
    'stirring_heat_exponent': '搅拌热随转速的指数', 'mixing_time_s_at_750': '750 rpm混合时间常数（反比于转速）', 'unstirred_mixing_time_s': '不搅拌时的混合时间常数',
    'jacket_tau_s': '夹套控温时间常数', 'jacket_max_rate_k_s': '夹套最大变温速率（1 K/min）', 'cell_equilibration_tau_s': '池液与夹套的热平衡时间常数',
    'equilibration_min_s': '热平衡最短时间', 'equilibration_heat_flow_w': '热平衡判据：池液热流<0.002 µcal/s', 'equilibration_timeout_s': '热平衡超时',
    'injection_precision_rel': '注射体积相对标准差0.3 %（厂家<1 %）', 'first_injection_leak_m3': '第一针前针尖扩散的饱和体积', 'first_injection_leak_tau_s': '针尖扩散时间常数',
    'blank_heat_j_per_m3': '机械空白热（每µL约−0.02 µcal，放热）', 'reference_evaporation_m3_per_day': '参比池每天蒸发', 'reference_full_m3': '参比池装满体积',
    'reference_offset_w_per_m3': '参比池欠满使基线上移（每µL 0.02 µcal/s）', 'lift_speed_m_s': '移液器升降速度', 'swing_speed_rad_s': '移液器转动速度',
    'plunger_speed_m_s': '柱塞Plunger Down速度', 'syringe_load_s': '注射器装样时长', 'purge_refill_s': 'Purge/Refill时长', 'load_excess_m3': '装样时经灌注口进废液的滴定剂',
    'bubble_lag_s_per_m3': '注射器气泡使推注滞后的时间常数（每µL气泡0.5 s）',
    'cell_residual_m3': '吸净后样品池残留', 'syringe_residual_m3': '注射器排空后的残留膜', 'rinse_volume_m3': '润洗用量', 'rinse_residual_factor': '润洗后旧液残留比例',
    'syringe_water_residue_m3': '未干燥时注射器的水残留', 'syringe_methanol_residue_m3': '干燥中断时的甲醇残留', 'methanol_mixing_dh_j_mol': '甲醇溶入水的混合焓（假设）',
    'bottle_capacity_m3': '各瓶容量', 'cell_methods': '池清洗方法（时长、用量与去除系数）', 'syringe_methods': '注射器清洗方法（时长、干燥、用量与去除系数）',
    'clogged_filter_factor': '滤芯堵塞时的输送比例', 'outgassing_threshold_k': '未脱气样品析气的温差阈值', 'outgassing_spike_w': '析气尖峰幅度', 'outgassing_rate_per_s': '析气尖峰频率',
    'poor_load_noise_factor': '池中有气泡时噪声倍数', 'electrical_power_w': '电功率分配', 'weighing_error_rel': '称量相对误差', 'ph_preparation_error': '配缓冲液pH误差',
    'degassed_hold_s': '脱气有效时间',
}


SLOT_REFERENCE = {
    'clock.time_s': '仿真时钟（宿主时钟，0.2 s网格）；0为运行层创建时',
    'temperature.setpoint_k': '热力学温度（ITS-90），样品池目标温度', 'temperature.cell_k': '热力学温度（ITS-90），样品池液体温度传感器读数（σ约0.04 mK）',
    'truth.jacket_temperature_k': '热力学温度（ITS-90），夹套', 'truth.cell_content_temperature_k': '热力学温度（ITS-90），样品池液体',
    'truth.reference_temperature_k': '热力学温度（ITS-90），参比池液体',
    'dp.power_w': '差分功率：样品池补偿加热功率（参比池加恒定参比功率）；基线≈参比功率−搅拌摩擦热；放热使DP下降；量程0–100 µcal/s',
    'dp.reference_power_w': '参比池补偿加热器的恒定功率设定', 'truth.dp_noise_free_w': '同dp.power_w的定义，不含噪声、不截断',
    'truth.heat_rate_w': '有效体积200 µL内的放热速率（化学反应、混合与机械空白热；放热为正）',
    'stir.rate_hz': '搅拌桨转速（转/秒，750 rpm = 12.5 s⁻¹）',
    'truth.cell_species_mol_m3': '有效体积200 µL内（混合部分与未混合注入液合计）各物质总浓度，池温下',
    'truth.cell_bound_mol_m3': '有效体积混合部分内各相互作用的结合（或转化、二聚）浓度，池温下', 'truth.syringe_species_mol_m3': '注射器液体中各物质浓度',
    'truth.cell_ph': 'pH（活度标度，扩展Debye–Hückel），池温下', 'cell.loaded_volume_m3': '装入样品池的体积（室温下量取；有效体积200 µL之外的部分在池颈）',
    'syringe.available_volume_m3': '按柱塞位置计的剩余可注射体积（40 µL注射器）', 'truth.syringe_liquid_m3': '注射器中液体体积（含针尖内的池液）',
    'truth.syringe_bubble_m3': '注射器中空气体积（约1 atm）', 'truth.reference_volume_m3': '参比池液体体积（装满280 µL）',
    'truth.unmixed_volume_m3': '有效体积中尚未混匀的注入液体积', 'loading_station.tube_volume_m3': 'PCR管中液体体积（目视）',
    'wash.bottle_fraction': '各瓶液体体积/瓶容量（洗涤剂与甲醇0.25 L、水0.5 L、废液1 L、溢流0.1 L）', 'truth.wash_volumes_m3': '各瓶液体体积',
    'run.remaining_time_s': '按方法计的运行剩余时间（初始延迟起算）', 'reference.age_s': '参比池上次换液后的仿真时间',
    'pipette.lift_m': '移液器升起高度（0放下），关节参考姿态见geometry_manifest', 'pipette.swing_rad': '移液器臂转角（0为Cell位置）',
    'syringe.plunger_position_m': '柱塞杆下行距离（0为上位=满）',
}


def export_malvern_microcalpeaq_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=SLOT_REFERENCE.get(k, v['reference']), origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'MalvernMicrocalpeaqSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.malvern_microcalpeaq.MalvernMicrocalpeaqSession.invoke',
                                              invocation=f'session.invoke(instance_id="peaq_itc_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=_pre_text(k) + _foreign_text(k) + '；被拒时状态不变。',
                                                                              state_ids=_pre_slots(k),
                                                                              enforced_by='runtime', check='MalvernMicrocalpeaqRuntime.invoke / MalvernMicrocalpeaqSession._guard')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量') for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置或查询（温度、移液器、柱塞与化学在之后的step中演化）',
                                completion_state_ids=['operations.running'] if async_ else [], timeout_s=None,
                                cancellation='supported' if async_ else 'unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=EXEC_TEXT[k][1] if k in EXEC_TEXT else ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, e), side_effects='none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in dict.fromkeys(ON_STEP[k])
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    mechanism = dict(
        component_ids=['cell_body', 'cell_recess', 'cell_ports', 'reference_cell_lid', 'pipette_head', 'pipette_needle', 'plunger_rod', 'syringe_glass', 'pipette_carriage',
                       'pipette_arm', 'injector_tower', 'titration_loading_station', 'wash_load_station', 'fpa', 'wash_body', 'detergent_bottle', 'methanol_bottle', 'water_bottle',
                       'waste_bottle', 'overflow_bottle', 'cell_cleaning_tool', 'status_light', 'wash_status_light', 'power_switch', 'wash_power_switch'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='等温滴定量热：夹套控温与池液热平衡、功率补偿DP（参比功率、反馈响应、搅拌热、参比池液位、热容失配、漂移与噪声）、柱塞注射与针尖扩散、搅拌混合与连续置换、'
                   '有效体积内的结合网络（一组/两组位点、竞争、顺序位点、动力学、质子释放）、二聚体解离、酶反应、缓冲液质子化（Goldberg 2002 + Debye–Hückel）、稀释热与甲醇残留；'
                   '装样（装样针、FPA、PCR管、Plunger Down、Load、Purge/Refill）、洗涤模块（瓶、废液与溢流停泵、清洗方法与残留）与数据分析（积分、对照扣除、拟合、Bin评语）',
        equations=['dT_j/dt = clip((T_set - T_j)/tau_j, +-1 K/min); dT_s/dt = (T_j - T_s)/tau_c; q_th = [C_s (T_s - T_j) - C_r (T_r - T_j)] / tau_c',
                   'dy/dt = (q_chem + q_blank + q_th + q_stir + q_spike - y) / tau_fb; DP = P_ref - y + k_ref (V_ref,full - V_ref) + drift + noise, clipped to [0, DP_max]',
                   'q_stir = 0.25 ucal/s (f / 12.5 s^-1)^2; tau_mix = 2.5 s x 12.5 s^-1 / f; injection: V_act = V_nom (1 + 0.003 e), flow = V_act / duration',
                   'slug -> active volume: dV_mix = V_slug (1 - exp(-dt / tau_mix)); displaced volume leaves the 200 uL active volume (overfilled cell)',
                   'heat released q_chem = -[H(after) - H(mixed before) - H(slug part)] / dt, H = sum bound x dH(T) + protonation enthalpy + H_E(I) + virial + methanol + products',
                   'sites: theta = K L / (1 + sum K L), L from sum mass balances (competition on the same site); K(T) = K0 exp[-dH/R (1/T - 1/T0) + dCp/R (T0/T - 1 + ln T/T0)]',
                   'protons: th = [H+] - [OH-] + sum C nbar(h); ln Kc = ln K0 + A_m f(I) (z_H^2 + z_B^2 - z_HA^2), f = sqrt(I)/(1 + 1.6 sqrt(I)); H_E = R T^2 dA_m/dT (4/B^3)(ln(1+Bs) - Bs + B^2 s^2/2)',
                   'virial dilution: H_E = hxx m^2 + hxxx m^3 per kg water (propan-1-ol, sucrose: IUPAC 2001); enzyme: K_M ln(S0/S) + (S0 - S) = kcat E dt',
                   'analysis: Q_i = integral (DP - baseline) dt; one set of sites Q = n M V0 dH / 2 [1 + X/nM + 1/nKM - sqrt((1 + X/nM + 1/nKM)^2 - 4X/nM)], '
                   'dQ_i = Q_i + dV_i/V0 (Q_i + Q_(i-1))/2 - Q_(i-1); dG = R T ln KD, -T dS = dG - dH, c = n M / KD'],
        parameters=_params(), source_ids=[_SIM, _WB, _PR, _BR, _UM, _AM, _GS, _TN1, _TN2, _GB, _IU], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_peaq_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('malvern_microcalpeaq', caps, slots, {'titration_calorimetry_process': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if SLOT_REFERENCE.get(k) or v['reference']:
            slots[k]['reference_conditions'] = SLOT_REFERENCE.get(k, v['reference'])
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'titration_calorimetry_process': mechanism})

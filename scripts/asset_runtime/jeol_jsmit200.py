"""CHEM-151 JEOL JSM-IT200 LA (InTouchScope scanning electron microscope with W hairpin gun, TMP + RP vacuum, low vacuum 10-100 Pa,
SE (Everhart-Thornley) and BSE (BED) detectors and the integrated JED-2300 DrySD EDS): runtime (SI units).

Physics owned by the host clock (``step`` is the only entry that advances time; instrument cycle 1 s):
  * vacuum: one lumped volume (chamber cavity of the geometry + column), rotary pump (roughing and foreline, >= 100 L/min, JSM-IT300
    manual of the same family), turbo-molecular pump with spin-up, crossover and full speed, outgassing that decays after venting
    (air with humidity or dry N2), wet specimens, a door that is not closed (leak), vent valve; low vacuum by the RP through an orifice
    against a PI-controlled needle valve (10-100 Pa) with the gun kept in high vacuum behind the pressure-limiting aperture; Pirani,
    Penning and LV gauges; EVAC / VENT / LV sequences with the panel lamps (blinking while running); 10-min evacuation limit;
  * gun: W hairpin filament heated to saturation by the automatic filament adjustment (about 10 s), emission, filament life (Weibull,
    engineering) and burnout; accelerating voltage 0.5-30 kV, probe current 1 pA - 0.3 uA (catalogue);
  * optics: probe diameter from brightness, spherical and chromatic aberration and diffraction at the optimum aperture angle with
    effective constants fitted to the catalogue resolutions (outputs/.../optics_fit.json), focus and defocus blur, astigmatism,
    direct magnification x5-300,000 on the 128 mm print, LV gas scattering (unscattered fraction, skirt);
  * signals: BSE (Hunger-Kuechler eta(Z, E0), Castaing mass-fraction rule, Arnal tilt law) collected by the annular BED (solid angle
    from the working distance, energy-weighted, amplifier noise), SE (Lin-Joy universal curve of the total yield with material-class
    parameters, secant tilt law, SE3) collected by the E-T detector (high vacuum only), shot noise, negative charging of uncoated
    insulators in high vacuum with one surface potential per phase driven by I (1 - eta - delta) at the actual incidence towards the
    second crossover (no charging in LV or when coated), microstructure from a seeded Voronoi phase map; images report grey-level
    regions, not phase names;
  * EDS: thick-target characteristic generation (Bethe ionisation cross-section, Joy-Luo stopping power, Myklebust backscatter loss,
    Elam fluorescence yields and line intensities), Philibert-Duncumb-Heinrich absorption with Chantler attenuation, detector efficiency
    (polyimide window, Al coating, Si grid, Si dead layer, 0.45 mm Si), solid angle and take-off angle from the modelled detector
    position, Kramers continuum, Fano-limited resolution (127 eV at Mn Ka, catalogue <= 130 eV), Si escape peaks, paralysable dead
    time, Poisson counts, Duane-Hunt limit estimated from the spectrum; standardless quantification with a Green-Cosslett-type generation
    term and the full Philibert absorption at the nominal take-off angle (the model difference is the method bias) and automatic peak
    identification;
  * stage X / Y (motor) and Z / T / R (manual knobs), tilt about the holder top (the beam meets a tilted specimen h tan t from its
    centre), swept-path collision limits against the pole piece, draw-out door (opens only at atmospheric pressure), specimen loading on
    the 32 mm holder; charge, gas, X-ray and filament ledgers;
  * front panel (1.1.0; panel_ui.json beside the contract, run by the shared engine _panel_ui.py): the SEM Operation (InTouchScope) main window,
    photo data and Settings windows, the specimen exchange guide and the console VENT / EVAC / LV switches, MAIN POWER key, Z / T / R knobs and
    aperture knobs; the functions behind them (beam blanking, scan modes / rotation, manual stigmator, brightness / contrast, beam shift,
    wobbler and movable aperture, electron gun window, P.C. scale and EOS high current mode, recipes, stage memory / last move / click
    center / calibration, settings, files, movie, snapshot, ruler, users) are minimal models in _SemOperation; alignment, aperture and stage
    coordinate error parameters default to 0 (ideal).
  * 1.2.0 (sweep 2026-10-19): the ownership guard checks the command a gesture invokes, not the gesture (window changes and viewing stay free;
    a refused command shows the device message); hands-on actions (draw-out door, specimen loading, Z / T / R knobs, aperture knobs,
    filament exchange, the MAIN POWER key switch) are never refused for ownership, the device's own conditions still apply (switching off
    fails a running acquisition with POWER_OFF; the Z / T / R knobs are not refused during a scan, the scan sees the moved stage; the software
    shutdown stays owner-guarded); evaluator fault.inject / fault.clear for the
    messages of the JSM-IT300 manual 3.14 that need a hardware fault (stage Error IDs 0200 / 0240 / 0250 / 0260, evacuation 0331 / 0351,
    Filament failure): the documented message, the motors / EVAC / automatic LV pressure / observation refused, the documented remedy.
"""
from __future__ import annotations

import bisect
import copy
import functools
import hashlib
import json
import math
import os
import random
import tempfile
from pathlib import Path

import numpy as np

PROTOCOL_VERSION = '1.0.0'
SLUG = 'jeol_jsmit200'
ASSET_TYPE_ID = 'instrument.' + SLUG
CATALOG_ID = 'CHEM-151'
ROOT = Path(__file__).resolve().parents[2]
ASSET = ROOT / 'assets/expansion_batch04/t3_analytical' / SLUG
LIBRARY_PATH = ASSET / 'sem_eds_library.json'
LIBRARY_REL = str(LIBRARY_PATH.relative_to(ROOT))

E_CHARGE = 1.602176634e-19
N_AVOGADRO = 6.02214076e23
K_BOLTZMANN = 1.380649e-23
GAS_CONSTANT = 8.314462618
P_ATM = 101325.0
CYCLE_S = 1.0
CYCLE_NS = 1_000_000_000
# ---------------------------------------------------------------- documented (JEOL catalogue JSM-IT200 p.17-18 unless noted)
KV_RANGE_V = (500.0, 30000.0)
KV_FINE_MAX_V, KV_FINE_STEP_V, KV_COARSE_STEP_V = 3000.0, 100.0, 1000.0     # JSM-IT300 manual (same family): 100 V steps to 3 kV, 1 kV above
PROBE_CURRENT_RANGE_A = (1e-12, 3e-7)                                        # 1 pA to 0.3 uA (1 uA only with the MP-30060 option, not fitted)
MAG_RANGE = (5.0, 300000.0)                                                  # direct magnification on a 128 mm x 96 mm print
PRINT_WIDTH_M = 0.128
LV_RANGE_PA = (10.0, 100.0)
RESOLUTION_HV_M = {30000.0: 3.0e-9, 3000.0: 8.0e-9, 1000.0: 15.0e-9}
RESOLUTION_LV_M = 4.0e-9                                                     # LV, BED, 30 kV
WD_RANGE_M = (0.005, 0.048)                                                  # stage Z 5-48 mm
STAGE_X_RANGE_M = (-0.040, 0.040)                                            # X 80 mm
STAGE_Y_RANGE_M = (-0.020, 0.020)                                            # Y 40 mm
TILT_RANGE_RAD = (math.radians(-10.0), math.radians(90.0))
ROT_RANGE_RAD = (0.0, 2 * math.pi)
Z_LIFT_RANGE_M = (0.0, WD_RANGE_M[1] - WD_RANGE_M[0])
SPECIMEN_MAX_DIAMETER_M, SPECIMEN_MAX_HEIGHT_M = 0.150, 0.048
PIXEL_FORMATS = {'320x240': (320, 240), '640x480': (640, 480), '1280x960': (1280, 960), '2560x1920': (2560, 1920), '5120x3840': (5120, 3840)}
MAINS_NOMINAL_V, MAINS_TOL = 100.0, 0.10                                     # single-phase AC 100 V +-10 %, 1.5 kVA
ROOM_RANGE_K = (288.15, 300.15)                                              # 15-27 C
HUMIDITY_MAX = 0.60
EDS_AREA_M2 = 25e-6                                                          # DrySD 25 mm2
EDS_FWHM_MN_SPEC_EV = 130.0
EDS_Z_RANGE = (4, 92)                                                        # Be to U
EVAC_LIMIT_S = 600.0                                                         # JSM-IT300 manual: error if evacuation not complete within 10 min
RP_SPEED_M3_S = 100e-3 / 60.0                                                # JSM-IT300 manual: RP pumping speed >= 100 L/min
HOLDER_RADIUS_M, HOLDER_HEIGHT_M = 0.016, 0.012                              # Iowa State CIF: 32 mm multipurpose mount (height engineering)
# ---------------------------------------------------------------- engineering values (see LIMITATIONS and mechanisms)
V_SYSTEM_M3 = 0.0106                   # chamber cavity of the geometry (0.18 x 0.25 x 0.218 m less stage, ~9.1 L) + column (~1.5 L)
RP_EFF_M3_S = 0.50 * RP_SPEED_M3_S     # through the roughing hose, valve and chamber port (engineering)
RP_ULTIMATE_PA = 2.0                   # rotary-pump ultimate pressure at the chamber (back-streaming load S_rp P_u)
TMP_SPEED_M3_S = 0.050                 # 67 L/s class TMP behind its valve
TMP_SPINUP_S = 60.0
CROSSOVER_PA = 20.0
HV_READY_PA = 5.0e-3                   # gun operation allowed below this (W gun)
GUN_TRIP_PA = 3.0e-2                   # HT trips above this
VENT_READY_PA = 9.5e4
VENT_CONDUCTANCE_M3_S = 4.0e-4
DOOR_GAP_CONDUCTANCE_M3_S = 1.0e-3     # door not closed
LEAK_CLOSED_PA_M3_S = 1.0e-7
OUTGAS_AIR_PA_M3_S = 1.0e-4            # after venting with room air at 0 % RH; x (1 + 2 RH)
OUTGAS_N2_PA_M3_S = 3.0e-5             # after venting with dry nitrogen
OUTGAS_TC_S = 60.0                     # Q(t) = Q0 tc / (tc + t)
WET_OUTGAS_PA_M3_S = 2.0e-3            # wet (hydrated) specimen, decaying with its accumulated exposure to the pumped chamber (WET_TAU_S)
WET_TAU_S = 1800.0
LV_ORIFICE_M3_S = 2.0e-4               # RP through the LV orifice
LV_VALVE_MAX_M3_S = 2.0e-5             # needle-valve conductance fully open (to room pressure)
LV_KP, LV_TI_S = 0.02, 8.0
PLA_CONDUCTANCE_M3_S = 2.0e-6          # pressure-limiting aperture between chamber and column in LV
GUN_BASE_PA = 2.0e-4
SOFTWARE_BOOT_S = 60.0
SATURATION_S = 10.0                    # Iowa State CIF guide: about 10 s after "Observation" the image forms
EMISSION_SAT_A = 1.0e-4
FILAMENT_LIFE_H = 300.0                # W hairpin mean life at saturation (Iowa State guide: filaments changed "every few hundred hours"; engineering)
FILAMENT_LIFE_S = FILAMENT_LIFE_H * 3600.0
FILAMENT_WEIBULL_K = 3.0
STAGE_XY_SPEED_M_S = 0.003             # motor
STAGE_Z_SPEED_M_S = 0.002              # hand on the Z knob
STAGE_TILT_SPEED = math.radians(10.0)  # hand on the T knob, rad/s
STAGE_ROT_SPEED = math.radians(30.0)
DOOR_SPEED_M_S = 0.10
DOOR_TRAVEL_M = 0.200
POLE_CLEARANCE_M = 0.004               # specimen top must stay this far below the pole piece (BED hangs 3.5 mm below it)
BED_R_IN_M, BED_R_OUT_M, BED_DROP_M = 0.004, 0.013, 0.0035
EDS_TIP_R_M = 0.040 * math.cos(math.radians(35.0))       # detector position of the geometry: 40 mm from the WD-10 analysis point at 35 deg
EDS_TIP_DZ_M = 0.040 * math.sin(math.radians(35.0)) - 0.010   # tip height above the pole-piece face
ANALYTICAL_WD_M = 0.010
# optics (effective constants fitted to the catalogue resolutions by derive_optics_parameters.py)
BETA_R = 1.0e9 / 30000.0               # W reduced brightness A m^-2 sr^-1 V^-1
ENERGY_SPREAD_EV = 1.5
LENS_GAP_M = 0.002
C_S_COEF = 0.9315669301523783          # C_s = c_s (WD + gap)
C_C_COEF = 0.1320663205093771
K_RES = 0.6                            # quoted resolution = 0.6 d_p
BED_RES_FACTOR = 1.430569028448803     # LV 4.0 nm (IT300 manual: 30 kV, WD 5 mm, 10 Pa, BSE) / modelled 30 kV resolution at WD 5 mm
VIBRATION_M = 1.0e-9
# signals
SURFACE_CAPACITANCE_F = 1.0e-13        # capacitance of a charging insulating region to ground (engineering)
MIRROR_EV = 50.0                       # landing energy below which the charged surface reflects the beam (mirror)
DH_END_MARGIN_EV = 300.0               # the Duane-Hunt fit needs the continuum end inside the recorded 0-20.48 keV range
SE_COLLECTION = 0.45
SE3_FRACTION = 0.10
BED_DEAD_LAYER_EV = 500.0
BED_AMP_NOISE_PAIRS = 2000.0           # rms e-h pairs for a 1 us dwell (scales with dwell^-0.5)
GAS_SIGMA30_M2 = 1.0e-21               # total scattering cross-section of air at 30 kV (order of magnitude after Danilatos)
# EDS detector (engineering)
EDS_PROCESS_TIMES = {'T1': dict(tau_s=0.4e-6, noise_ev=95.0), 'T2': dict(tau_s=0.8e-6, noise_ev=75.0), 'T3': dict(tau_s=1.6e-6, noise_ev=60.0),
                     'T4': dict(tau_s=3.2e-6, noise_ev=49.3)}
FANO, PAIR_EV = 0.115, 3.64
EDS_CHANNEL_EV, EDS_CHANNELS = 10.0, 2048
WINDOW = dict(kapton_nm=300.0, al_nm=50.0, grid_open=0.77, dead_si_nm=30.0, si_um=450.0)
KRAMERS_K = 2.0e-9                     # continuum photons per electron per eV into 4 pi (x Zbar (E0-E)/E)
MAX_RECORDS, MAX_EVENTS = 200, 1000
ASSET_VERSION = '1.2.0'
# ---- SEM Operation (front panel 1.1.0).  The InTouchScope windows follow the JSM-IT300 instructions manual of the same family (chapters 3-5) and
# the Iowa State JSM-IT200 guide; values the documents do not give are assumptions (listed in panel_ui.json and the contract limitations).
PC_STD_A = (1e-12, 3e-8)                 # P.C. 1-99 in the standard mode: 1 pA - 30 nA on a log scale (scale not published; assumption)
PC_HCM_A = (1e-11, 3e-7)                 # EOS High Current Mode: P.C. 1-99 -> 10 pA - 0.3 uA (catalogue maximum 0.3 uA; assumption)
GUN_SAT_POS = 0.6                        # filament heating bar position of the saturation point (2nd peak) (assumption)
GUN_ALIGN_WIDTH = 0.25                   # gun shift / tilt misalignment (full scale 1) that lowers the beam current to 1/e (assumption)
GUN_AUTO_S = 30.0                        # AGC / AFS: 'after several dozen seconds' (manual 3-59); 30 s assumed
BIAS_COARSE_OCTAVE, BIAS_FINE_OCTAVE = 25.0, 250.0   # gun bias slider steps that halve the load current (coarse / fine; assumption)
OVERHEAT_WEAR = 20.0                     # filament wear x (1 + 20 (h / h_sat - 1)) right of the saturation point (manual 3-62 caution; assumption)
STIG_RANGE_M = 100e-9                    # stigmator range +-100 nm of equivalent blur (assumption)
BEAM_SHIFT_LIMIT_M = 10e-6               # beam shift deflection limit (manual 4-11: 'there is a limit'; assumption)
APERTURE_RANGE_M = 5e-4                  # movable objective aperture X / Y fine adjustment +-0.5 mm (assumption)
APERTURE_TOL_M = 4e-5                    # aperture offset from the beam axis that lowers the transmitted current to 1/e (assumption)
APERTURE_BLUR = 2e-3                     # extra blur per metre of aperture offset (off-axis aberrations; assumption)
WOBBLE_GAIN = 0.1                        # image swing of the wobbler per metre of aperture offset (assumption)
WOBBLE_DEFOCUS_M = 20e-6                 # objective focus swing while the wobbler runs (blurs a photo taken meanwhile; assumption)
RDC_FIELD = 0.25                         # RDC scans a reduced area: 1/4 of the field width (assumption)
CF_CHARGING = 0.3                        # CF scan (Slow or Photo): charging rate x 0.3 (assumption)
SLOW_LEAK_FACTOR, SLOW_LEAK_UNTIL_PA = 0.1, 5000.0   # slow-leak evacuation: roughing at 1/10 speed down to 5 kPa (assumption)
CLOCK_EPOCH = '2026-01-01T00:00:00'      # the SEM PC clock at simulation time 0 (file names, photo data date; assumption)
HOLDERS = ['32mm_multipurpose', '10mm_round', '3mm_pin_stub', '4x10mm_insert']   # Iowa State guide holder set; the modelled holder is the 32 mm mount
PHOTO_1FRAME = [('640x480', 10.0), ('640x480', 20.0), ('1280x960', 20.0), ('1280x960', 40.0), ('1280x960', 80.0), ('2560x1920', 40.0),
                ('2560x1920', 80.0), ('2560x1920', 160.0), ('5120x3840', 160.0), ('5120x3840', 320.0)]      # 10 combinations (manual 3-33; values assumed,
                                                                                                           # Iowa State: 2560 x 1920 at 40 s or 80 s)
PHOTO_INTEGRATION = [('640x480', 0.5), ('640x480', 1.0), ('1280x960', 1.0), ('1280x960', 2.0), ('2560x1920', 4.0)]   # 5 (pixels, frame time) pairs
SCAN_SPEEDS = {'RDC': [('320x240', 0.075), ('320x240', 0.25), ('320x240', 0.5)],
               'FAST': [('640x480', 0.15), ('640x480', 0.5), ('640x480', 1.0), ('1280x960', 0.5), ('1280x960', 1.0)],
               'SLOW': [('1280x960', 10.0), ('1280x960', 20.0), ('1280x960', 40.0), ('2560x1920', 40.0)]}
# live scan combinations (pixels, frame time) per mode: manual 3-32/33 (3 / 5 / 4 combinations) and specification table 8.2.5.b (frame times
# RDC 0.075/0.25/0.5 s at 320 x 240, Fast 0.15/0.5/1.0 s and 0.5/1.0 s, Slow 10/20/40/40 s); the text copy flattens the merged pixel cells of
# the table, so the pixel number of each frame time is assigned in column order (assumption)
HOLDER_OUTLINE_R_M = {'32mm_multipurpose': 0.016, '10mm_round': 0.0075, '3mm_pin_stub': 0.016, '4x10mm_insert': 0.016}
# outline radius of each holder the software knows (Iowa State guide: the pin stub and the 4-position inserts fit the 32 mm mount; the 10 mm
# round holder's outer diameter of 15 mm is an assumption); the software stage limit uses it with the entered specimen height
VIEW_MODES = ['standard', 'multiple', 'flexible', 'signal_mixing', 'full_screen']
# recipes (manual 3-11/12, table 3-1, garbled in the text copy): kV and P.C. per specimen category for coated (SE), uncoated at low kV (SE) and uncoated in
# low vacuum (BE, pressure); EDS raises the P.C.  None = '-' in the table.  Reconstruction, see LIMITATIONS.
RECIPES = {
    'general': dict(coated=(15000.0, 50.0), low_kv=(1000.0, 50.0), low_vacuum=(15000.0, 60.0, 50.0), eds_pc=60.0),
    'high_mag': dict(coated=(20000.0, 35.0), low_kv=None, low_vacuum=None, eds_pc=None),
    'inorganic': dict(coated=(15000.0, 50.0), low_kv=(1000.0, 50.0), low_vacuum=(15000.0, 60.0, 40.0), eds_pc=60.0),
    'metal_mineral': dict(coated=(15000.0, 60.0), low_kv=(1500.0, 50.0), low_vacuum=(15000.0, 60.0, 30.0), eds_pc=65.0),
    'organic': dict(coated=(10000.0, 60.0), low_kv=(1000.0, 50.0), low_vacuum=(10000.0, 60.0, 40.0), eds_pc=65.0),
    'biological': dict(coated=(10000.0, 60.0), low_kv=(1000.0, 50.0), low_vacuum=(10000.0, 60.0, 50.0), eds_pc=65.0),
}
MAX_FILES, MAX_MEMORY, MAX_MEASUREMENTS = 500, 100, 20
EDS_MAX_DEAD_TIME = 0.90             # the analysis refuses to start above 90 % dead time (reduce the probe current)
THUMB_W, THUMB_H = 32, 24


class ActionRejected(ValueError):
    pass


# ---------------------------------------------------------------- schema helpers
def number(unit='1', lo=None, hi=None):
    s = {'type': 'number', 'x-unit': unit}
    if lo is not None:
        s['minimum'] = lo
    if hi is not None:
        s['maximum'] = hi
    return s


def obj(properties):
    return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)


def nullable(s):
    return {'anyOf': [s, {'type': 'null'}]}


def array(s, minimum=0, maximum=4096):
    return dict(type='array', items=s, minItems=minimum, maxItems=maximum)


def enum(items):
    return {'type': 'string', 'enum': list(items)}


BOOL = {'type': 'boolean'}
TEXT = {'type': 'string'}
INT = {'type': 'integer', 'x-unit': '1'}


def integer(lo, hi):
    return {'type': 'integer', 'x-unit': '1', 'minimum': lo, 'maximum': hi}


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
            if not set(s.get('required', s['properties'])) <= set(v) <= set(s['properties']):   # optional arguments: only the panel.* commands
                raise ActionRejected('INVALID_ARGUMENT')
            for key, value in v.items():
                validate(s['properties'][key], value)
        elif 'additionalProperties' in s and isinstance(s['additionalProperties'], dict):
            for key, value in v.items():
                if not isinstance(key, str):
                    raise ActionRejected('INVALID_ARGUMENT')
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
    elif k == 'null' and v is not None:
        raise ActionRejected('INVALID_ARGUMENT')
    if 'enum' in s and v not in s['enum']:
        raise ActionRejected('INVALID_ARGUMENT')


# ---------------------------------------------------------------- template library (NIST SRD 126, xraydb Elam / Chantler, Hephaestus)
_LIB = None


def _lib():
    global _LIB
    if _LIB is None:
        raw = json.loads(LIBRARY_PATH.read_text())
        els = raw['elements']
        for e in els.values():
            if e.get('attenuation'):
                e['_lnE'] = np.log(np.asarray(e['attenuation']['energy_eV'], float))
                e['_lnMu'] = np.log(np.asarray(e['attenuation']['mu_rho_cm2_g'], float))
        _LIB = dict(meta={k: v for k, v in raw.items() if k not in ('elements', 'substances')}, elements=els,
                    by_z={e['Z']: s for s, e in els.items()}, substances={s['substance_id']: s for s in raw['substances']})
    return _LIB


def library():
    """Deep copy of the substance templates (by id) and element symbols of the SEM/EDS library."""
    lib = _lib()
    return dict(meta=copy.deepcopy(lib['meta']), substances=copy.deepcopy(lib['substances']), elements=sorted(lib['elements']))


def element(sym):
    return _lib()['elements'][sym]


def substance(sid):
    return _lib()['substances'][sid]


@functools.lru_cache(maxsize=200000)
def mu_rho(sym, e_ev):
    """Mass attenuation coefficient (cm2/g) of an element, log-log interpolation of the Chantler table (xraydb)."""
    e = element(sym)
    if '_lnE' not in e:
        return 0.0
    x = math.log(max(e_ev, 50.0))
    lnE, lnMu = e['_lnE'], e['_lnMu']
    i = int(np.searchsorted(lnE, x, side='right'))
    i = min(max(i, 1), len(lnE) - 1)
    x0, x1 = lnE[i - 1], lnE[i]
    if x1 <= x0:
        return float(math.exp(lnMu[i]))
    return float(math.exp(lnMu[i - 1] + (lnMu[i] - lnMu[i - 1]) * (x - x0) / (x1 - x0)))


def mu_rho_mix(comp, e_ev):
    return sum(w * mu_rho(s, e_ev) for s, w in comp.items())


def mean_z(comp):
    return sum(w * element(s)['Z'] for s, w in comp.items())


def mean_a(comp):
    return sum(w * element(s)['molar_mass_g_mol'] for s, w in comp.items())


def zoa(comp):
    return sum(w * element(s)['z_over_a'] for s, w in comp.items())


def mean_j_kev(comp):
    """Mass-and-Z/A weighted mean ionisation energy (keV) from the NIST SRD 126 I values."""
    num = sum(w * element(s)['z_over_a'] * math.log(element(s)['mean_excitation_eV']) for s, w in comp.items())
    return math.exp(num / zoa(comp)) / 1000.0


# ---------------------------------------------------------------- electron optics
def wavelength_m(v):
    return 1.22639e-9 / math.sqrt(v * (1.0 + 9.78476e-7 * v))


def probe(v, i_a, wd_m):
    """Probe diameter (m) and aperture semi-angle (rad) at the optimum angle (golden-section search on log alpha)."""
    cs, cc, beta = C_S_COEF * (wd_m + LENS_GAP_M), C_C_COEF * (wd_m + LENS_GAP_M), BETA_R * v
    lam = wavelength_m(v)

    def tot(la):
        a = 10.0 ** la
        return math.sqrt(4 * i_a / (math.pi ** 2 * beta * a * a) + (0.5 * cs * a ** 3) ** 2 + (cc * a * ENERGY_SPREAD_EV / v) ** 2 + (1.22 * lam / a) ** 2)
    lo, hi = -4.0, -1.3
    g = (math.sqrt(5) - 1) / 2
    x1, x2 = hi - g * (hi - lo), lo + g * (hi - lo)
    f1, f2 = tot(x1), tot(x2)
    for _ in range(80):
        if f1 < f2:
            hi, x2, f2 = x2, x1, f1
            x1 = hi - g * (hi - lo); f1 = tot(x1)
        else:
            lo, x1, f1 = x1, x2, f2
            x2 = lo + g * (hi - lo); f2 = tot(x2)
    la = (lo + hi) / 2
    return tot(la), 10.0 ** la


# ---------------------------------------------------------------- electron-specimen interaction
def eta_hk(z, e_kev):
    """Hunger & Kuechler (1979) backscatter coefficient of an element at normal incidence."""
    lz = math.log(z)
    m = 0.1382 - 0.9211 / math.sqrt(z)
    c = 0.1904 - 0.2236 * lz + 0.1292 * lz ** 2 - 0.01491 * lz ** 3
    return max(0.0, min(0.95, c * max(e_kev, 0.1) ** m))


def eta(comp, e_kev, theta=0.0):
    """Castaing mass-fraction rule on Hunger-Kuechler with the Arnal tilt law eta(theta) = (1 + cos theta)^-(9/sqrt Z) (ratio to theta = 0)."""
    e0 = sum(w * eta_hk(element(s)['Z'], e_kev) for s, w in comp.items())
    zb = max(mean_z(comp), 1.0)
    p = 9.0 / math.sqrt(zb)
    th = min(abs(theta), math.radians(85.0))
    return min(0.95, e0 * (2.0 ** p) / (1.0 + math.cos(th)) ** p)


def delta_lin_joy(dm, em_kev, e_kev):
    """Lin & Joy (2005) universal secondary-electron yield curve."""
    r = max(e_kev, 1e-3) / em_kev
    return dm * 1.28 * r ** -0.67 * (1.0 - math.exp(-1.614 * r ** 1.67))


def se_yield(sub, comp, e_kev, theta=0.0):
    """Total secondary-electron yield (SE1 + SE2): the Lin-Joy curve with the material's measured-type parameters already describes the
    total yield of the bulk material, so no separate SE2 term is added; secant law for the incidence angle."""
    se = sub['se_parameters']
    return delta_lin_joy(se['delta_m'], se['E_m_keV'], e_kev) / max(math.cos(min(abs(theta), math.radians(80.0))), 0.17)


def sigma_total(sub, e_kev, theta=0.0):
    comp = sub['composition_mass_fraction']
    return eta(comp, e_kev, theta) + se_yield(sub, comp, e_kev, theta)


_E2_CACHE = {}


def second_crossover_kev(sub, theta=0.0):
    """Landing energy (keV) above which the total yield eta + delta falls below 1 at incidence `theta` (negative charging of an
    insulator); None if the total yield never exceeds 1; 30 keV if it is still above 1 there.  Cached per substance and 0.01 rad
    (module cache, the library templates stay untouched)."""
    key = (sub['substance_id'], round(abs(theta), 2))
    if key in _E2_CACHE:
        return _E2_CACHE[key]
    th = key[1]
    em = sub['se_parameters']['E_m_keV']
    if sigma_total(sub, em, th) <= 1.0:
        e2 = None
    elif sigma_total(sub, 30.0, th) > 1.0:
        e2 = 30.0
    else:
        lo, hi = em, 30.0
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            lo, hi = (mid, hi) if sigma_total(sub, mid, th) > 1.0 else (lo, mid)
        e2 = 0.5 * (lo + hi)
    _E2_CACHE[key] = e2
    return e2


def ko_range_m(comp, rho, e_kev):
    """Kanaya-Okayama electron range (m)."""
    zb, ab = mean_z(comp), mean_a(comp)
    return 0.0276 * ab * e_kev ** 1.67 / (zb ** 0.889 * rho) * 1e-6


# ---------------------------------------------------------------- X-ray generation (true model)
SHELL_PARAMS = {'K': (2, 0.35), 'L1': (2, 0.25), 'L2': (2, 0.25), 'L3': (4, 0.25), 'M1': (2, 0.25), 'M2': (2, 0.25), 'M3': (4, 0.25), 'M4': (4, 0.25), 'M5': (6, 0.25)}
_GL_X, _GL_W = np.polynomial.legendre.leggauss(32)


def _stopping(e_kev, zoa_m, j_kev):
    """Joy & Luo (1989) modified Bethe stopping power, keV cm2/g (positive)."""
    return 78500.0 * zoa_m / e_kev * math.log(1.166 * (e_kev + 0.85 * j_kev) / j_kev)


def _myklebust_r(zb, u0, e0_kev):
    return max(0.3, min(1.0, 1.0 - 0.0081517 * zb + 3.613e-5 * zb * zb + 0.009583 * zb * math.exp(-u0) + 0.001141 * e0_kev))


def ionisations_per_electron(comp, sym, shell, e0_kev):
    """Thick-target ionisations of `shell` of element `sym` per incident electron (Bethe cross-section over the Joy-Luo slowing down)."""
    lev = element(sym)['levels'].get(shell)
    w = comp.get(sym, 0.0)
    if lev is None or w <= 0.0:
        return 0.0
    ec = lev['edge_eV'] / 1000.0
    if ec <= 0.0 or e0_kev <= ec * 1.001:
        return 0.0
    n_s, b_s = SHELL_PARAMS.get(shell, (2, 0.25))
    zoa_m, j = zoa(comp), mean_j_kev(comp)
    u0 = e0_kev / ec
    # substitute E = Ec U, U in [1, U0]
    us = 1.0 + (u0 - 1.0) * (_GL_X + 1.0) / 2.0
    tot = 0.0
    for u, wt in zip(us, _GL_W):
        q = 6.51e-20 * n_s * b_s * math.log(u) / (u * ec * ec)
        tot += wt * q / _stopping(ec * u, zoa_m, j) * ec
    tot *= (u0 - 1.0) / 2.0
    a = element(sym)['molar_mass_g_mol']
    return N_AVOGADRO * w / a * tot * _myklebust_r(mean_z(comp), u0, e0_kev)


def philibert_f(chi, e0_kev, ec_kev, a_bar, z_bar, full=True):
    if chi <= 0.0:
        return 1.0
    ec = min(ec_kev, e0_kev * 0.999)
    sigma = 4.5e5 / (e0_kev ** 1.65 - ec ** 1.65)
    h = 1.2 * a_bar / (z_bar * z_bar) if full else 0.0
    return 1.0 / ((1.0 + chi / sigma) * (1.0 + h / (1.0 + h) * chi / sigma))


# ---------------------------------------------------------------- EDS detector
KAPTON = {'C': 0.691127, 'H': 0.026363, 'N': 0.073270, 'O': 0.209235}   # C22H10N2O5 mass fractions (NIST SRD 126 Table 2 "Kapton Polyimide Film")


@functools.lru_cache(maxsize=10000)
def detector_efficiency(e_ev):
    """Transmission of the polyimide window + Al coating (x open fraction of the Si grid, which is opaque below ~8 keV), Si dead layer,
    times absorption in 0.45 mm Si."""
    if e_ev < 60.0:
        return 0.0
    t_win = math.exp(-mu_rho_mix(KAPTON, e_ev) * 1.42 * WINDOW['kapton_nm'] * 1e-7 - mu_rho('Al', e_ev) * 2.70 * WINDOW['al_nm'] * 1e-7)
    grid = WINDOW['grid_open'] + (1.0 - WINDOW['grid_open']) * math.exp(-mu_rho('Si', e_ev) * 2.33 * 0.038)    # 380 um Si grid bars
    dead = math.exp(-mu_rho('Si', e_ev) * 2.33 * WINDOW['dead_si_nm'] * 1e-7)
    absorb = 1.0 - math.exp(-mu_rho('Si', e_ev) * 2.33 * WINDOW['si_um'] * 1e-4)
    return t_win * grid * dead * absorb


def fwhm_ev(e_ev, noise_ev):
    return math.sqrt(noise_ev ** 2 + 5.5452 * FANO * PAIR_EV * max(e_ev, 0.0))


def duane_hunt_limit(counts, noise_ev=60.0, e0_ev=None):
    """Duane-Hunt limit (eV) estimated from a spectrum: near its end the continuum per channel follows N(E) = (a + b E) eff(E) / E
    (Kramers), i.e. it falls linearly to zero at E_DH = -a/b.  The fit window runs from 0.6 of the last occupied channel (or from two FWHM
    above the last significant characteristic peak there) to the end; Poisson maximum likelihood by iteratively reweighted least squares
    with 3-sigma clipping of residual peaks; never above the set beam energy.  None when the spectrum is truncated (E0 within 300 eV of
    the 20.48 keV end of the recorded range) or the fit window holds fewer than 1000 counts.  An uncharged specimen gives about E0, a charged insulator its
    landing energy (statistical error about 30-60 eV at 1 nA / 30 s, a few hundred eV for a few 1e4 counts)."""
    y = np.asarray(counts, float)
    nz = np.nonzero(y > 0)[0]
    if len(nz) < 20 or y.sum() < 2000 or (e0_ev is not None and e0_ev > EDS_CHANNELS * EDS_CHANNEL_EV - DH_END_MARGIN_EV):
        return None
    c_last = int(nz[-1])
    f, var = _tophat(y, noise_ev)
    sig = f / np.sqrt(var)
    lo = int(0.6 * c_last)
    pk = [c for c in range(max(lo, 1), c_last) if sig[c] >= 5.0 and f[c] >= f[c - 1] and f[c] > f[c + 1]]
    if pk:
        start = max(pk) + int(2.0 * fwhm_ev((max(pk) + 0.5) * EDS_CHANNEL_EV, noise_ev) / EDS_CHANNEL_EV)
        if c_last - start >= 0.08 * c_last:
            lo = start
    sel = np.arange(lo, c_last + 1)
    e = (sel + 0.5) * EDS_CHANNEL_EV
    g = np.array([max(detector_efficiency(x), 1e-3) for x in e]) / e
    yy = y[sel]
    if yy.sum() < 1000.0:
        return None
    A = np.vstack([g, g * e]).T
    mask = np.ones(len(sel), bool)
    mu = np.maximum(yy, 1.0)
    coef = None
    for _ in range(40):
        w = 1.0 / np.sqrt(np.maximum(mu, 0.5))
        c2, *_ = np.linalg.lstsq(A[mask] * w[mask, None], yy[mask] * w[mask], rcond=None)
        mu = np.maximum(A @ c2, 0.5)
        new = (yy - mu) / np.sqrt(mu) < 3.0
        done = coef is not None and np.allclose(c2, coef, rtol=1e-10, atol=0.0) and bool((new == mask).all())
        coef, mask = c2, new
        if done:
            break
    if coef[1] >= 0.0:
        return None
    e_dh = -coef[0] / coef[1]
    return float(round(min(e_dh, e0_ev) if e0_ev is not None else e_dh, 0))


def eds_geometry(wd_m, tilt_rad):
    """Take-off angle (rad) and solid angle (sr) of the SDD for a specimen point at working distance `wd_m`; the tilt towards the door is
    perpendicular to the detector azimuth, so sin(psi_eff) = sin(psi) cos(tilt)."""
    dz = EDS_TIP_DZ_M + wd_m
    d = math.hypot(EDS_TIP_R_M, dz)
    psi = math.atan2(dz, EDS_TIP_R_M)
    psi_eff = math.asin(max(1e-3, math.sin(psi) * math.cos(tilt_rad)))
    return psi_eff, EDS_AREA_M2 / (d * d)


_SI_KA_EV = 1739.98
_SI_K_EDGE_EV = 1838.9


def escape_fraction(e_ev):
    """Reed & Ware (1972) Si escape probability for a photon absorbed in the SDD."""
    if e_ev <= _SI_K_EDGE_EV:
        return 0.0
    si = element('Si')['levels']['K']
    r = si['jump_ratio']
    mu_i, mu_k = mu_rho('Si', e_ev), mu_rho('Si', _SI_KA_EV)
    return max(0.0, 0.5 * si['fluorescence_yield'] * (1.0 - 1.0 / r) * (1.0 - mu_k / mu_i * math.log(1.0 + mu_i / mu_k)))


def _gauss_channels(center_ev, fwhm, area, out):
    """Add a Gaussian of `area` counts integrated over the 10 eV channels."""
    s = fwhm / 2.3548
    lo = max(0, int((center_ev - 5 * s) / EDS_CHANNEL_EV))
    hi = min(EDS_CHANNELS, int((center_ev + 5 * s) / EDS_CHANNEL_EV) + 2)
    if hi <= lo:
        return
    edges = np.arange(lo, hi + 1) * EDS_CHANNEL_EV
    cdf = 0.5 * (1.0 + np.array([math.erf((x - center_ev) / (s * math.sqrt(2))) for x in edges]))
    out[lo:hi] += area * np.diff(cdf)


def _lines_of(sym, e0_ev, min_rel=0.0):
    """Lines of K, L and M vacancies above 100 eV whose vacancy level is excited (N-shell emission is not modelled)."""
    e = element(sym)
    return [ln for ln in e['lines'] if ln['energy_eV'] >= 100.0 and ln['relative_intensity'] > min_rel and ln['initial'] in SHELL_PARAMS
            and e['levels'][ln['initial']]['edge_eV'] < e0_ev]


def spectrum_rates(comp, e0_ev, psi_rad, omega_sr, noise_ev, coating=None):
    """Expected detected photons per incident electron per channel (numpy array, 2048 x 10 eV) and the generated / emitted photon totals.
    coating = (comp, mass thickness g/cm2) of a thin conductive coating on top (thin-film generation at E0, absorbs the substrate lines)."""
    e0 = e0_ev / 1000.0
    ab, zb = mean_a(comp), mean_z(comp)
    csc = 1.0 / math.sin(psi_rad)
    rates = np.zeros(EDS_CHANNELS)
    gen = emitted = 0.0

    def coat_t(e_ev):
        if not coating:
            return 1.0
        return math.exp(-mu_rho_mix(coating[0], e_ev) * coating[1] * csc)
    for sym in comp:
        if element(sym)['Z'] < EDS_Z_RANGE[0]:
            continue
        shells = {}
        for ln in _lines_of(sym, e0_ev):
            shells.setdefault(ln['initial'], []).append(ln)
        for shell, lines in shells.items():
            lev = element(sym)['levels'][shell]
            n_ion = ionisations_per_electron(comp, sym, shell, e0)
            if n_ion <= 0.0:
                continue
            for ln in lines:
                n = n_ion * lev['fluorescence_yield'] * ln['relative_intensity']
                gen += n
                f = philibert_f(mu_rho_mix(comp, ln['energy_eV']) * csc, e0, lev['edge_eV'] / 1000.0, ab, zb)
                em = n * f * coat_t(ln['energy_eV'])
                emitted += em
                det = em * omega_sr / (4 * math.pi) * detector_efficiency(ln['energy_eV'])
                fw = fwhm_ev(ln['energy_eV'], noise_ev)
                pe = escape_fraction(ln['energy_eV'])
                _gauss_channels(ln['energy_eV'], fw, det * (1.0 - pe), rates)
                if pe > 0.0:
                    _gauss_channels(ln['energy_eV'] - _SI_KA_EV, fwhm_ev(ln['energy_eV'] - _SI_KA_EV, noise_ev), det * pe, rates)
    if coating:
        ccomp, mt = coating
        for sym, w in ccomp.items():
            if element(sym)['Z'] < EDS_Z_RANGE[0]:
                continue
            for ln in _lines_of(sym, e0_ev):
                lev = element(sym)['levels'][ln['initial']]
                ec = lev['edge_eV'] / 1000.0
                n_s, b_s = SHELL_PARAMS.get(ln['initial'], (2, 0.25))
                u = e0 / ec
                q = 6.51e-20 * n_s * b_s * math.log(u) / (u * ec * ec)
                n = N_AVOGADRO * w * mt / element(sym)['molar_mass_g_mol'] * q * lev['fluorescence_yield'] * ln['relative_intensity'] * (1.0 + eta(comp, e0))
                gen += n
                emitted += n
                det = n * omega_sr / (4 * math.pi) * detector_efficiency(ln['energy_eV'])
                _gauss_channels(ln['energy_eV'], fwhm_ev(ln['energy_eV'], noise_ev), det, rates)
    # Kramers continuum with absorption (edges) and window
    centers = (np.arange(EDS_CHANNELS) + 0.5) * EDS_CHANNEL_EV
    for c, e in enumerate(centers):
        if e >= e0_ev or e < 60.0:
            continue
        n = KRAMERS_K * zb * (e0_ev - e) / e * EDS_CHANNEL_EV
        f = philibert_f(mu_rho_mix(comp, e) * csc, e0, e / 1000.0, ab, zb)
        rates[c] += n * f * coat_t(e) * omega_sr / (4 * math.pi) * detector_efficiency(e)
    return rates, gen, emitted


# ---------------------------------------------------------------- standardless quantification (simpler model = method bias)
def _family(shell):
    return shell[0]


def _analytical_family(sym, e0_ev):
    lev = element(sym)['levels']
    for fam, shells in (('K', ['K']), ('L', ['L3', 'L2', 'L1']), ('M', ['M5', 'M4'])):
        edge = min((lev[s]['edge_eV'] for s in shells if s in lev), default=None)
        lines = [ln for ln in _lines_of(sym, e0_ev) if _family(ln['initial']) == fam]
        if edge and e0_ev / edge >= 1.5 and lines and max(ln['energy_eV'] for ln in lines) < EDS_CHANNELS * EDS_CHANNEL_EV - 200 and \
                sum(ln['relative_intensity'] for ln in lines if ln['energy_eV'] > 150.0) > 0.3:
            return fam
    return None


def _quant_model(comp, sym, fam, e0_ev, psi_rad, noise_ev):
    """Green-Cosslett thick-target intensity per unit concentration for the line family (quantification model), and its channel shape."""
    e0 = e0_ev / 1000.0
    ab, zb = mean_a(comp), mean_z(comp)
    zoa_m, j = zoa(comp), mean_j_kev(comp)
    csc = 1.0 / math.sin(psi_rad)
    shape = np.zeros(EDS_CHANNELS)
    total = 0.0
    for ln in _lines_of(sym, e0_ev):
        if _family(ln['initial']) != fam:
            continue
        lev = element(sym)['levels'][ln['initial']]
        ec = lev['edge_eV'] / 1000.0
        u0 = e0 / ec
        n_s, b_s = SHELL_PARAMS.get(ln['initial'], (2, 0.25))
        gc = n_s * b_s * (u0 - 1.0) ** 1.67 / (zoa_m * math.log(1.166 * 0.5 * (e0 + ec) / j))
        r_q = 1.0 - 0.4 * eta(comp, e0) * (1.0 - 1.0 / u0)
        f = philibert_f(mu_rho_mix(comp, ln['energy_eV']) * csc, e0, ec, ab, zb)
        v = gc / element(sym)['molar_mass_g_mol'] * lev['fluorescence_yield'] * ln['relative_intensity'] * r_q * f * detector_efficiency(ln['energy_eV'])
        total += v
        pe = escape_fraction(ln['energy_eV'])
        _gauss_channels(ln['energy_eV'], fwhm_ev(ln['energy_eV'], noise_ev), v * (1.0 - pe), shape)
        if pe > 0.0:
            _gauss_channels(ln['energy_eV'] - _SI_KA_EV, fwhm_ev(ln['energy_eV'] - _SI_KA_EV, noise_ev), v * pe, shape)
    return total, shape


def _background_basis(e0_ev, comp, psi_rad=math.radians(35.0)):
    """Continuum basis for the fit: Kramers shape and a linear term, both through the detector efficiency and (when a composition
    estimate is given) the Philibert absorption of the estimate, so the absorption edges of the matrix appear in the model."""
    centers = (np.arange(EDS_CHANNELS) + 0.5) * EDS_CHANNEL_EV
    b1 = np.zeros(EDS_CHANNELS)
    b2 = np.zeros(EDS_CHANNELS)
    if comp:
        ab_, zb_, csc = mean_a(comp), mean_z(comp), 1.0 / math.sin(psi_rad)
    for c, e in enumerate(centers):
        if 150.0 <= e < e0_ev:
            eff = detector_efficiency(e)
            ab = philibert_f(mu_rho_mix(comp, e) * csc, e0_ev / 1000.0, e / 1000.0, ab_, zb_) if comp else 1.0
            b1[c] = (e0_ev - e) / e * eff * ab
            b2[c] = eff * ab * (e0_ev - e) / e0_ev
    return b1, b2


def _nnls_fit(A, y, w):
    """Weighted least squares with non-negativity by active-set elimination (small problems)."""
    active = list(range(A.shape[1]))
    coef = np.zeros(A.shape[1])
    for _ in range(A.shape[1] + 1):
        Aw = A[:, active] * w[:, None]
        sol, *_ = np.linalg.lstsq(Aw, y * w, rcond=None)
        neg = [a for a, s in zip(active, sol) if s < 0]
        if not neg:
            coef[:] = 0.0
            coef[active] = sol
            cov = np.linalg.pinv(Aw.T @ Aw)
            var = np.zeros(A.shape[1])
            var[active] = np.diag(cov)
            return coef, var
        worst = min(zip(sol, active))[1]
        active.remove(worst)
    return coef, np.zeros(A.shape[1])


WINDOW_ARTIFACT_EV = (200.0, 265.0)


def _line_weight(sym, ln):
    """Approximate emitted weight of a line for identification: intensity within its vacancy level x fluorescence yield x shell occupancy
    x detector efficiency."""
    lev = element(sym)['levels'][ln['initial']]
    return ln['relative_intensity'] * lev['fluorescence_yield'] * SHELL_PARAMS.get(ln['initial'], (2, 0.25))[0] * detector_efficiency(ln['energy_eV'])


def _tophat(y, noise_ev, var_y=None):
    """Top-hat digital filter (central lobe one FWHM wide, side lobes half as wide on each side): removes slowly varying continuum and
    gives the net peak height per channel with its variance (Poisson counts by default, or the given per-channel variance)."""
    n = len(y)
    v = np.asarray(y if var_y is None else var_y, float)
    csum = np.concatenate([[0.0], np.cumsum(y)])
    vsum = np.concatenate([[0.0], np.cumsum(v)])
    f = np.zeros(n)
    var = np.ones(n)
    for c in range(n):
        m = max(1, int(round(0.5 * fwhm_ev((c + 0.5) * EDS_CHANNEL_EV, noise_ev) / EDS_CHANNEL_EV)))
        a0, a1 = c - m, c + m + 1
        l0, r1 = a0 - m, a1 + m
        if l0 < 0 or r1 > n:
            continue
        nc, ns = a1 - a0, 2 * m
        f[c] = (csum[a1] - csum[a0]) / nc - ((csum[a0] - csum[l0]) + (csum[r1] - csum[a1])) / ns
        var[c] = (vsum[a1] - vsum[a0]) / nc ** 2 + ((vsum[a0] - vsum[l0]) + (vsum[r1] - vsum[a1])) / ns ** 2 + 1e-12
    return f, var


def _efficiency_array():
    return np.array([max(detector_efficiency((c + 0.5) * EDS_CHANNEL_EV), 0.02) for c in range(EDS_CHANNELS)])


def _family_lines(sym, fam, e0_ev):
    lines = [ln for ln in _lines_of(sym, e0_ev) if _family(ln['initial']) == fam and 150.0 <= ln['energy_eV'] <= min(0.98 * e0_ev, EDS_CHANNELS * EDS_CHANNEL_EV - 100)]
    if not lines:
        return [], None, 0.0
    edge = min(element(sym)['levels'][ln['initial']]['edge_eV'] for ln in lines)
    return lines, max(lines, key=lambda ln: _line_weight(sym, ln)), e0_ev / edge


def _strong_lines(sym, e0_ev):
    out = []
    for fam in ('K', 'L', 'M'):
        lines, main, u0 = _family_lines(sym, fam, e0_ev)
        if main is not None:
            out += [ln['energy_eV'] for ln in lines if _line_weight(sym, ln) >= 0.02 * _line_weight(sym, main)]
    return out


def _peaks(f, sig, y, noise_ev, lo_c, hi_c, min_sig=8.0, min_rel=0.03, artifacts=True):
    out = []
    for c in range(lo_c + 1, hi_c):
        if sig[c] >= min_sig and f[c] >= f[c - 1] and f[c] > f[c + 1] and f[c] >= min_rel * max(y[c], 1.0):
            k = max(1, int(round(fwhm_ev((c + 0.5) * EDS_CHANNEL_EV, noise_ev) / EDS_CHANNEL_EV)))
            if c - k < 0 or c + k >= len(f):
                continue
            left, right = f[c - k] / f[c], f[c + k] / f[c]
            e = (c + 0.5) * EDS_CHANNEL_EV
            if artifacts and WINDOW_ARTIFACT_EV[0] < e < WINDOW_ARTIFACT_EV[1]:
                continue                                                  # transmission maximum of the polyimide window below its C K edge
            # Gaussian-like: negative side lobes (a continuum absorption edge gives one lobe near +1 and the other near -1); an unresolved
            # multiplet (Au Ma + Mb) lifts one lobe a little; a resolved neighbour line lifts it more: accept a doublet when that side
            # holds another local maximum
            if -3.0 <= min(left, right) <= -0.1 and max(left, right) <= 0.3 and (min(left, right) <= -0.3 or max(left, right) <= -0.1):
                out.append((e, f[c]))
            elif -3.0 <= min(left, right) <= -0.3:
                side = range(c + 1, min(len(f) - 1, c + 2 * k + 1)) if right > left else range(max(1, c - 2 * k), c)
                if any(f[j] >= f[j - 1] and f[j] > f[j + 1] and f[j] >= 0.2 * f[c] for j in side):
                    out.append((e, f[c]))
            elif sig[c] >= 20.0 and abs(left - right) <= 0.35 and max(left, right) <= 0.3:
                out.append((e, f[c]))                                     # a strong, symmetric but broad multiplet (e.g. an L family at T1)
    return sorted(out, key=lambda p: -p[1])


def _family_template(sym, fam, e0_ev, noise_ev):
    """Efficiency-corrected channel pattern of one line family of the pure element (relative heights incl. its own absorption,
    e.g. Cr Lb above the L3 edge)."""
    tmpl = np.zeros(EDS_CHANNELS)
    lines, main, u0 = _family_lines(sym, fam, e0_ev)
    el = element(sym)
    csc = 1.0 / math.sin(math.radians(35.0))
    for ln in lines:
        f_abs = philibert_f(mu_rho(sym, ln['energy_eV']) * csc, e0_ev / 1000.0, el['levels'][ln['initial']]['edge_eV'] / 1000.0, el['molar_mass_g_mol'], el['Z'])
        _gauss_channels(ln['energy_eV'], fwhm_ev(ln['energy_eV'], noise_ev), _line_weight(sym, ln) * f_abs / max(detector_efficiency(ln['energy_eV']), 0.02), tmpl)
    return tmpl


def _local_chi2(z, vz, sym, fam, e_pk, e0_ev, noise_ev, others=()):
    """Chi-square of a local fit (family pattern + the families of the elements already identified + linear background) to the
    efficiency-corrected spectrum around a peak."""
    fw = fwhm_ev(e_pk, noise_ev)
    c0 = max(0, int((e_pk - 2.5 * fw) / EDS_CHANNEL_EV))
    c1 = min(len(z), int((e_pk + 2.5 * fw) / EDS_CHANNEL_EV) + 1)
    if c1 - c0 < 5:
        return 0.0
    tmpl = _family_template(sym, fam, e0_ev, noise_ev)
    extra = []
    for s2, f2 in others:
        t2 = _family_template(s2, f2, e0_ev, noise_ev)[c0:c1]
        if t2.max() > 1e-9 * max(tmpl.max(), 1e-30):
            extra.append(t2)
    e = (np.arange(c0, c1) + 0.5) * EDS_CHANNEL_EV
    A = np.vstack([tmpl[c0:c1], np.ones(c1 - c0), (e - e_pk) / fw] + extra).T
    w = 1.0 / np.sqrt(vz[c0:c1])
    sol, *_ = np.linalg.lstsq(A * w[:, None], z[c0:c1] * w, rcond=None)
    if sol[0] <= 0:
        return float(np.sum(((z[c0:c1] - A[:, 1:] @ np.linalg.lstsq(A[:, 1:] * w[:, None], z[c0:c1] * w, rcond=None)[0]) * w) ** 2))
    return float(np.sum(((z[c0:c1] - A @ sol) * w) ** 2))


def _fit_elements(y, elements, e0_ev, noise_ev, psi_rad=math.radians(35.0)):
    comp = {e: 1.0 / len(elements) for e in elements}
    cols = []
    for e in elements:
        for fam in ('K', 'L', 'M'):
            tot, shape = _quant_model(comp, e, fam, e0_ev, psi_rad, noise_ev)
            if tot > 0.0 and shape.sum() > 0.0:
                cols.append(shape / shape.sum())
    b1, b2 = _background_basis(e0_ev, comp, psi_rad)
    A = np.vstack(cols + [b1, b2]).T
    lo_c, hi_c = int(150 / EDS_CHANNEL_EV), min(EDS_CHANNELS, int(e0_ev / EDS_CHANNEL_EV))
    coef, _ = _nnls_fit(A[lo_c:hi_c], y[lo_c:hi_c], 1.0 / np.sqrt(y[lo_c:hi_c] + 1.0))
    return A @ coef


@functools.lru_cache(maxsize=4096)
def _family_main_intensity(sym, fam, e0_ev):
    """Emitted intensity per electron of the main line of a family for the pure element (ionisation x fluorescence yield x line
    intensity x self-absorption at 35 deg); used to bound the expected heights of an identified element's other line families."""
    lines, main, u0 = _family_lines(sym, fam, e0_ev)
    if main is None:
        return 0.0
    el = element(sym)
    lev = el['levels'][main['initial']]
    n = ionisations_per_electron({sym: 1.0}, sym, main['initial'], e0_ev / 1000.0)
    f_abs = philibert_f(mu_rho(sym, main['energy_eV']) / math.sin(math.radians(35.0)), e0_ev / 1000.0, lev['edge_eV'] / 1000.0, el['molar_mass_g_mol'], el['Z'])
    return n * lev['fluorescence_yield'] * main['relative_intensity'] * f_abs


# noble gases and elements without stable isotopes do not occur in SEM specimens: never proposed by the automatic identification
ID_EXCLUDED = {'He', 'Ne', 'Ar', 'Kr', 'Xe', 'Rn', 'Tc', 'Pm', 'Po', 'At', 'Fr', 'Ra', 'Ac', 'Pa',     # Ar (implanted by ion milling or sputtering) must be given explicitly
               'Be', 'B'}               # Be K 108 eV, B K 183 eV: inside the window-transmission structure below the C K edge (give them explicitly)
ID_MIN_BASE_SCORE = -2.0          # a candidate may miss at most one expected line family before the local shape is compared


def identify_elements(counts, e0_ev, noise_ev, max_elements=12, trace=None):
    """Automatic peak identification in up to three passes on the efficiency-corrected spectrum: Gaussian-like top-hat peaks (>= 8 sigma
    and >= 3 % of the local counts; doublets accepted) are taken strongest first by detected counts (the K and L lines of the heavier
    elements then explain their own low-energy L / M lines, which the efficiency correction inflates) and assigned to the element whose line family has its
    strongest line within half a FWHM and whose other strong lines (same family, expected heights from the line weights, and the main
    lines of its other excited families) are present and not already claimed; candidates that miss more than one expected family are
    dropped, the rest are ranked by that score and by the local shape of the family pattern (pure-element self-absorption included).
    Noble gases and unstable elements are never proposed.  A peak is explained by an identified element only if that element's expected
    line height there is at least half the peak; a continuum absorption edge of an identified element explains a peak no higher than
    about 0.6 of the local continuum.  After each pass all identified elements are fitted and the next pass searches the residual
    (e.g. S Ka hidden under Pb Ma).  Searches from 200 eV: B K (183 eV) and Be K (108 eV) lie in the window-transmission structure below the
    C K edge and are never proposed automatically (give them in elements=[...])."""
    y = np.asarray(counts, float)
    eff = _efficiency_array()
    z = y / eff
    vz = np.maximum(y, 1.0) / eff ** 2
    f_raw, var = _tophat(z, noise_ev, vz)
    sig_raw = f_raw / np.sqrt(var)
    lo_c, hi_c = int(200 / EDS_CHANNEL_EV), min(EDS_CHANNELS - 2, int(0.98 * e0_ev / EDS_CHANNEL_EV))

    def window(e_ev):
        c = int(e_ev / EDS_CHANNEL_EV)
        k = max(1, int(round(0.25 * fwhm_ev(e_ev, noise_ev) / EDS_CHANNEL_EV)))
        return slice(max(c - k, 0), min(c + k + 1, len(f_raw)))

    def height(arr, e_ev):
        return float(np.max(arr[window(e_ev)])) if 0 <= int(e_ev / EDS_CHANNEL_EV) < len(arr) else 0.0

    def is_claimed(e_ev, h_obs, claims):
        """Explained when an identified element's expected height there reaches half the peak, or the line heights of several identified
        elements together do (a blend, e.g. Cs and I M lines; each element counted once, absorption-edge artefacts never summed)."""
        fw_ = fwhm_ev(e_ev, noise_ev)
        near = [(ch, sym_, kind) for ce, ch, sym_, kind in claims if abs(e_ev - ce) <= 0.5 * fw_]
        if not near:
            return False
        if max(ch for ch, _, _ in near) >= 0.5 * h_obs:
            return True
        per_el = {}
        for ch, sym_, kind in near:
            if kind == 'line':
                per_el[sym_] = max(per_el.get(sym_, 0.0), ch)
        return sum(per_el.values()) >= 0.5 * h_obs

    def local_level(e_ev):
        c = int(e_ev / EDS_CHANNEL_EV)
        k = max(2, int(2 * fwhm_ev(e_ev, noise_ev) / EDS_CHANNEL_EV))
        seg = z[max(c - k, 0):max(c, 1)]
        return float(np.median(seg)) if len(seg) else 0.0

    found_fam = {}                                      # element -> (family it was identified by, its measured main-line height)

    def element_claims(sym, scale_of):
        """Positions and expected heights the element explains; the other families' heights are the measured ones but never more than
        twice the theoretical ratio to the identifying family (a neighbour's peak must not be taken as, e.g., the weak Ca Ll)."""
        out = []
        fam_id, h_id = found_fam.get(sym, (None, None))
        i_id = _family_main_intensity(sym, fam_id, e0_ev) if fam_id else 0.0
        for lev, v in element(sym)['levels'].items():               # absorption edges of an identified element shape the continuum
            if lev in ('K', 'L3', 'M5', 'M4') and 250.0 < v['edge_eV'] < 0.9 * e0_ev:
                fw_ = fwhm_ev(v['edge_eV'], noise_ev)
                h_edge = 0.3 * local_level(v['edge_eV'])
                for de in (0.25, 0.5, 0.75, 1.0):
                    out.append((v['edge_eV'] - de * fw_, h_edge, sym, 'edge'))
        for fam in ('K', 'L', 'M'):
            lines, main, u0 = _family_lines(sym, fam, e0_ev)
            if main is None or main['energy_eV'] < 250.0:
                continue
            h_main = scale_of(fam, main)
            if fam_id and fam != fam_id and i_id > 0.0:
                _, m_id, _ = _family_lines(sym, fam_id, e0_ev)
                ratio = _family_main_intensity(sym, fam, e0_ev) / i_id * fwhm_ev(m_id['energy_eV'], noise_ev) / fwhm_ev(main['energy_eV'], noise_ev)
                h_main = min(h_main, 2.0 * h_id * ratio)
            if h_main <= 0.0:
                continue
            wm, fm = _line_weight(sym, main), fwhm_ev(main['energy_eV'], noise_ev)
            for ln in lines:
                if ln['energy_eV'] < 250.0:
                    continue
                h = h_main * _line_weight(sym, ln) / wm * fm / fwhm_ev(ln['energy_eV'], noise_ev)
                out.append((ln['energy_eV'], h, sym, 'line'))
                if ln['energy_eV'] > _SI_K_EDGE_EV:
                    e_esc = ln['energy_eV'] - _SI_KA_EV
                    pe = escape_fraction(ln['energy_eV'])
                    out.append((e_esc, h * pe / (1.0 - pe) * fwhm_ev(ln['energy_eV'], noise_ev) / fwhm_ev(e_esc, noise_ev)
                                * detector_efficiency(ln['energy_eV']) / max(detector_efficiency(e_esc), 0.02), sym, 'escape'))
        return out
    elems = [s_ for s_, e in _lib()['elements'].items() if EDS_Z_RANGE[0] <= e['Z'] <= EDS_Z_RANGE[1] and e['lines'] and s_ not in ID_EXCLUDED]
    found = []
    resid = z
    f_model = np.zeros_like(z)
    for _ in range(3):
        first = resid is z
        f, _ = _tophat(resid, noise_ev, vz)
        peaks = _peaks(f, f / np.sqrt(var), z, noise_ev, lo_c, hi_c, 8.0 if first else 15.0, 0.03 if first else 0.08, artifacts=True)
        if not first:                                   # a hidden element must be a substantial part of the fitted signal at its position
            peaks = [(e, h) for e, h in peaks if h >= 0.15 * max(height(f_model, e), 0.0)]
        f_cnt, _ = _tophat(resid * eff, noise_ev)        # rank by detected counts: the efficiency correction inflates low-energy L / M lines
        peaks.sort(key=lambda p_: -float(f_cnt[min(int(p_[0] / EDS_CHANNEL_EV), len(f_cnt) - 1)]))
        claims = []
        for sym_ in found:
            fam_, _ = found_fam[sym_]
            _, m_, _ = _family_lines(sym_, fam_, e0_ev)
            found_fam[sym_] = (fam_, height(f_model, m_['energy_eV']))
            claims += element_claims(sym_, lambda fam, main: height(f_model, main['energy_eV']))
        raw_peaks = [e for e, _ in _peaks(f_raw, sig_raw, z, noise_ev, lo_c, hi_c, 5.0, 0.0, artifacts=False)]
        new = []
        for e_pk, h_pk in peaks:
            if len(found) + len(new) >= max_elements:
                break
            if is_claimed(e_pk, h_pk, claims):
                continue
            fw = fwhm_ev(e_pk, noise_ev)
            cands = []
            for sym in elems:
                if sym in found or sym in new:
                    continue
                for rank, fam in enumerate(('K', 'L', 'M')):
                    lines, main, u0 = _family_lines(sym, fam, e0_ev)
                    if main is None or u0 < 1.1 or abs(main['energy_eV'] - e_pk) > 0.5 * fw:
                        continue
                    wm = _line_weight(sym, main)
                    fm = fwhm_ev(main['energy_eV'], noise_ev)
                    expect = [(ln, h_pk * _line_weight(sym, ln) / wm * fm / fwhm_ev(ln['energy_eV'], noise_ev)) for ln in lines
                              if _line_weight(sym, ln) >= 0.25 * wm and abs(ln['energy_eV'] - main['energy_eV']) > 2.0 * fwhm_ev(ln['energy_eV'], noise_ev)]
                    weak = []
                    i_fam = _family_main_intensity(sym, fam, e0_ev)
                    for fam2 in ('K', 'L', 'M'):
                        if fam2 != fam:
                            _, m2, u2 = _family_lines(sym, fam2, e0_ev)
                            if m2 is not None and u2 >= 1.1 and m2['energy_eV'] > 250.0:
                                # expected only when its predicted height (pure-element ratio) stands 10 sigma above the local noise:
                                # the M lines of 4d elements next to their L lines are usually too weak to demand
                                c2 = min(int(m2['energy_eV'] / EDS_CHANNEL_EV), len(var) - 1)
                                pred = h_pk * _family_main_intensity(sym, fam2, e0_ev) / i_fam * fm / fwhm_ev(m2['energy_eV'], noise_ev) if i_fam > 0.0 else 0.0
                                (expect if (u2 >= 1.5 and pred >= 10.0 * math.sqrt(var[c2])) else weak).append((m2, None))

                    def ok(ln, h):
                        obs = height(f_raw, ln['energy_eV'])
                        if is_claimed(ln['energy_eV'], obs, claims):
                            return False
                        if h is None:                     # another family's main line: a detected peak must be there
                            return any(abs(e_ - ln['energy_eV']) <= 0.5 * fwhm_ev(ln['energy_eV'], noise_ev) for e_ in raw_peaks)
                        return obs >= 0.3 * h
                    hits = sum(1 for ln, h in expect if ok(ln, h))
                    bonus = sum(1 for ln, h in weak if ok(ln, h))
                    score = 2.0 * (hits + bonus) - 1.5 * (len(expect) - hits) - 0.5 * rank - abs(main['energy_eV'] - e_pk) / fw
                    # intensity consistency: the peak must not be far stronger than this family can be next to the element's other
                    # detected family (pure-element ratio, a factor 10 for matrix absorption): a weak K L family cannot explain a C K peak
                    for m2, _ in [x for x in expect + weak if x[1] is None]:
                        if not ok(m2, None):
                            continue
                        fam2 = _family(m2['initial'])
                        i2 = _family_main_intensity(sym, fam2, e0_ev)
                        h2 = height(f_raw, m2['energy_eV'])
                        if i2 > 0.0 and h2 > 0.0 and h_pk > 10.0 * (i_fam / i2) * (fwhm_ev(m2['energy_eV'], noise_ev) / fm) * h2:
                            score -= 4.0
                    if score < ID_MIN_BASE_SCORE:
                        continue
                    others = [(s2, f2) for s2 in found + new for f2 in ('K', 'L', 'M') if _family_lines(s2, f2, e0_ev)[1] is not None]
                    cands.append([score, sym, fam, main, _local_chi2(resid, vz, sym, fam, e_pk, e0_ev, noise_ev, others)])
            if not cands:
                continue
            c_best = min(c[4] for c in cands)            # local shape: the family pattern (e.g. Zr La+Lb doublet vs P Ka) must fit the peak
            n_w = max(10.0, 5.0 * fw / EDS_CHANNEL_EV)
            for c in cands:
                c[0] -= min(4.0, 2.0 * (c[4] - c_best) / max(c_best, n_w))
            best = tuple(max(cands, key=lambda c: c[0])[:4])
            _, sym, fam_m, main_m = best
            new.append(sym)
            found_fam[sym] = (fam_m, h_pk)
            if trace is not None:
                trace.append(dict(pass_first=first, peak_ev=e_pk, element=sym, score=best[0]))
            claims += element_claims(sym, lambda fam, main: h_pk if fam == fam_m else height(f_raw, main['energy_eV']))
        if not new:
            break
        found += new
        model = _fit_elements(y, found, e0_ev, noise_ev)
        resid = (y - model) / eff
        f_model, _ = _tophat(model / eff, noise_ev, vz)
    return found


def quantify(counts, e0_ev, psi_rad, noise_ev, elements):
    """Standardless ZAF: family areas by weighted least squares (Gaussian templates + continuum), then iterate C_i ~ area_i / P_i(C)."""
    y = np.asarray(counts, float)
    elements = [s for s in elements if s in _lib()['elements'] and element(s)['Z'] >= EDS_Z_RANGE[0]]
    fams = {s: _analytical_family(s, e0_ev) for s in elements}
    usable = [s for s in elements if fams[s]]
    if not usable:
        raise ActionRejected('QUANT_FAILED')
    comp = {s: 1.0 / len(usable) for s in usable}
    lo_c, hi_c = int(150 / EDS_CHANNEL_EV), min(EDS_CHANNELS, int(e0_ev / EDS_CHANNEL_EV))
    w = 1.0 / np.sqrt(y + 1.0)
    for _ in range(8):
        cols, names = [], []
        for s in usable:
            for fam in ('K', 'L', 'M'):
                tot, shape = _quant_model(comp, s, fam, e0_ev, psi_rad, noise_ev)
                if tot > 0.0 and shape.sum() > 0.0:
                    cols.append(shape / shape.sum()); names.append((s, fam))
        b1, b2 = _background_basis(e0_ev, comp, psi_rad)
        A = np.vstack(cols + [b1, b2]).T
        coef, var = _nnls_fit(A[lo_c:hi_c], y[lo_c:hi_c], w[lo_c:hi_c])
        areas = {nm: (c, math.sqrt(max(v, 0.0))) for nm, c, v in zip(names, coef, var)}
        new = {}
        for s in usable:
            a, _ = areas.get((s, fams[s]), (0.0, 0.0))
            tot, _ = _quant_model(comp, s, fams[s], e0_ev, psi_rad, noise_ev)
            new[s] = max(a, 0.0) / tot if tot > 0 else 0.0
        norm = sum(new.values())
        if norm <= 0:
            raise ActionRejected('QUANT_FAILED')
        new = {s: max(v / norm, 1e-6) for s, v in new.items()}
        if max(abs(new[s] - comp[s]) for s in usable) < 1e-6:
            comp = new
            break
        comp = new
    bgfit = A[:, -2:] @ coef[-2:]
    out = []
    atoms = {s: comp[s] / element(s)['molar_mass_g_mol'] for s in usable}
    at_norm = sum(atoms.values())
    for s in usable:
        a, sa = areas.get((s, fams[s]), (0.0, 0.0))
        lines = [ln for ln in _lines_of(s, e0_ev) if _family(ln['initial']) == fams[s]]
        main = max(lines, key=lambda ln: ln['relative_intensity'])
        fw = fwhm_ev(main['energy_eV'], noise_ev)
        c0, c1 = int((main['energy_eV'] - fw) / EDS_CHANNEL_EV), int((main['energy_eV'] + fw) / EDS_CHANNEL_EV) + 1
        bkg = float(np.sum(bgfit[max(c0, 0):max(c1, 1)]))
        rel = math.sqrt(a + 2.0 * bkg) / a if a > 0 else None
        mdl = 3.0 * math.sqrt(bkg) / a * comp[s] if a > 0 else None
        out.append(dict(element=s, line_family=fams[s], mass_fraction=comp[s], atom_fraction=atoms[s] / at_norm, mass_fraction_sigma=comp[s] * rel if rel else None,
                        net_counts=a, background_counts=bkg, detection_limit_mass_fraction=mdl))
    return out


# ---------------------------------------------------------------- microstructure (seeded Voronoi phase map)
def _hash01(*ints):
    h = 1469598103934665603
    for v in ints:
        h ^= (int(v) & 0xFFFFFFFFFFFFFFFF)
        h = (h * 1099511628211) & 0xFFFFFFFFFFFFFFFF
        h ^= h >> 29
    return ((h * 2685821657736338717) & 0xFFFFFFFFFFFFFFFF) / 2.0 ** 64


def phase_at(spec, u, v):
    """Index of the phase at specimen coordinates (u, v) (m): nearest jittered cell centre on a grid of the mean grain size."""
    g = spec['_grain']
    i0, j0 = math.floor(u / g), math.floor(v / g)
    best, bi = 1e30, 0
    for i in (i0 - 1, i0, i0 + 1):
        for j in (j0 - 1, j0, j0 + 1):
            cx = (i + _hash01(i, j, spec['_seed'], 1)) * g
            cy = (j + _hash01(i, j, spec['_seed'], 2)) * g
            d = (cx - u) ** 2 + (cy - v) ** 2
            if d < best:
                best, bi = d, (i, j)
    r = _hash01(bi[0], bi[1], spec['_seed'], 3)
    acc = 0.0
    for k, ph in enumerate(spec['phases']):
        acc += ph['area_fraction']
        if r < acc:
            return k
    return len(spec['phases']) - 1


def local_tilt(spec, u, v):
    """Local surface slope (rad) and its azimuth for rough or particle specimens (per grain; flat polished = 0)."""
    topo = spec['topography']
    if topo == 'flat_polished':
        return 0.0, 0.0
    g = spec['_grain']
    i, j = math.floor(u / g), math.floor(v / g)
    amp = math.radians(35.0 if topo == 'rough_fracture' else 25.0)
    return amp * _hash01(i, j, spec['_seed'], 4), 2 * math.pi * _hash01(i, j, spec['_seed'], 5)


# ---------------------------------------------------------------- capabilities
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=list(c) if isinstance(c, (list, tuple)) else [c], function_zh=meaning,
                   visibility=visibility, kind=kind, effects=[])


PLANT_EXAMPLE = dict(room_k=295.15, relative_humidity=0.45, mains_voltage_v=100.0, vent_gas='dry_nitrogen', noise_seed=1)
PLANT = obj(dict(room_k=number('K', 283.15, 308.15), relative_humidity=number('1', 0.0, 1.0), mains_voltage_v=number('V', 0.0, 140.0),
                 vent_gas=enum(['room_air', 'dry_nitrogen']), noise_seed=INT))
PHASE_ITEM = obj(dict(substance_id=TEXT, area_fraction=number('1', 0.0, 1.0), grain_size_m=number('m', 1e-8, 1e-2)))
SPECIMEN = obj(dict(specimen_id=dict(type='string', minLength=1, maxLength=40), phases=array(PHASE_ITEM, 1, 6), diameter_m=number('m', 1e-4, 0.3),
                    height_m=number('m', 1e-4, 0.1), topography=enum(['flat_polished', 'particles', 'rough_fracture']),
                    coating=enum(['none', 'carbon', 'gold', 'platinum']), coating_thickness_m=number('m', 0.0, 1e-7), wet=BOOL))
SPECIMEN_EXAMPLE = dict(specimen_id='fe_si_polished', phases=[dict(substance_id='iron', area_fraction=0.7, grain_size_m=2e-5),
                                                             dict(substance_id='silicon', area_fraction=0.3, grain_size_m=2e-5)],
                        diameter_m=0.012, height_m=0.005, topography='flat_polished', coating='none', coating_thickness_m=0.0, wet=False)
OPERATION = obj(dict(operation_id=TEXT))
REGION = obj(dict(region_id=TEXT, area_fraction_in_view=number('1', 0.0, 1.0), mean_grey=number('1', 0.0, 255.0), snr=number('1', 0.0), charging=BOOL))
IMAGE = obj(dict(image_id=TEXT, status=enum(['running', 'completed', 'failed', 'cancelled']), detector=TEXT, accelerating_voltage_v=number('V'),
                 probe_current_a=number('A'), magnification=number('1'), pixels=TEXT, dwell_s=number('s'), frame_time_s=number('s'),
                 field_of_view_m=array(number('m'), 2, 2), pixel_size_m=nullable(number('m')), working_distance_m=nullable(number('m')),
                 probe_diameter_m=nullable(number('m')), effective_resolution_m=nullable(number('m')), vacuum_mode=enum(['HV', 'LV']),
                 chamber_pressure_pa=nullable(number('Pa')), regions=array(REGION, 0, 8), contrast=nullable(number('1')), charging_artifacts=BOOL,
                 saturated_fraction=nullable(number('1')), thumbnail=array(array(integer(0, 255), THUMB_W, THUMB_W), 0, THUMB_H),
                 error_code=nullable(TEXT), started_s=number('s'), finished_s=nullable(number('s')), template_source=obj(dict(library=TEXT, substances=array(TEXT, 0, 8)))))
SPECTRUM = obj(dict(spectrum_id=TEXT, status=enum(['running', 'completed', 'failed', 'cancelled']), mode=enum(['point', 'area']),
                    accelerating_voltage_v=number('V'), duane_hunt_limit_ev=nullable(number('eV')), probe_current_a=number('A'), live_time_s=number('s'),
                    real_time_s=nullable(number('s')), process_time=TEXT, dead_time_fraction=nullable(number('1')), input_count_rate_cps=nullable(number('1/s')),
                    output_count_rate_cps=nullable(number('1/s')), takeoff_angle_rad=nullable(number('rad')), solid_angle_sr=nullable(number('sr')),
                    working_distance_m=nullable(number('m')), channel_width_ev=number('eV'), counts=array(integer(0, 2 ** 31 - 1), 0, EDS_CHANNELS),
                    total_counts=INT, fwhm_mn_ka_ev=number('eV'), error_code=nullable(TEXT), started_s=number('s'), finished_s=nullable(number('s')),
                    template_source=obj(dict(library=TEXT, substances=array(TEXT, 0, 8)))))
QUANT_ITEM = obj(dict(element=TEXT, line_family=enum(['K', 'L', 'M']), mass_fraction=number('1', 0.0, 1.0), atom_fraction=number('1', 0.0, 1.0),
                      mass_fraction_sigma=nullable(number('1')), net_counts=number('1'), background_counts=number('1'),
                      detection_limit_mass_fraction=nullable(number('1'))))
QUANT = obj(dict(spectrum_id=TEXT, method=TEXT, elements_identified=array(TEXT, 0, 16), results=array(QUANT_ITEM, 0, 16), total_before_normalisation=nullable(number('1'))))
EVENT = obj(dict(time_s=number('s'), kind=enum(['power', 'vacuum', 'gun', 'stage', 'door', 'specimen', 'acquisition', 'warning', 'error', 'maintenance']),
                 code=TEXT, message_zh=TEXT))
LEDGER_KEYS = ['beam_charge_c', 'backscattered_charge_c', 'secondary_charge_c', 'absorbed_charge_c', 'stored_charge_c', 'charge_residual_c',
               'beam_energy_j', 'gas_admitted_mol', 'gas_pumped_mol', 'gas_in_volume_mol', 'gas_residual_mol', 'xray_generated', 'xray_detected',
               'filament_on_time_s', 'electrons_on_specimen']
LEDGER = obj({k: number('C' if k.endswith('_c') else 'J' if k.endswith('_j') else 'mol' if k.endswith('_mol') else 's' if k.endswith('_s') else '1')
              for k in LEDGER_KEYS})

cap('plant.configure', PLANT, obj(dict(configured=BOOL)), PLANT_EXAMPLE, ['cabinet_body', 'rear_panel', 'vent_valve'],
    '宿主声明实验室条件：室温（安装要求15–27°C）、相对湿度（≤60%）、供电电压（单相AC 100V±10%，1.5kVA）、放气气体（室内空气或干燥氮气）与噪声随机数种子（只在首次声明时生效）',
    'evaluator', 'configuration')
cap('specimen.provide', SPECIMEN, obj(dict(specimen_id=TEXT, provided=BOOL)), SPECIMEN_EXAMPLE, 'specimen_holder',
    '宿主在实验台上放入一个样品：由模板库物质组成的相（面积分数、晶粒尺寸）、直径、高度、形貌（抛光平面/颗粒/粗糙断口）、导电镀膜（碳、金、铂及厚度）与是否含水；'
    '接受厂家上限（直径150mm、高48mm），但高于44mm的样品在Z最低时也离极靴不足4mm，推不进样品室', 'evaluator',
    'configuration')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), 'key_main_power',
    'MAIN POWER钥匙开关：转到START（开）启动主机与计算机，软件约60s加载后可操作；转到OFF（关）断电——应先在软件中关机（system.shutdown）；断电时电子枪高压立即关闭、泵停转，样品室在真空下缓慢漏气')
cap('power.key_toggle', obj({}), obj(dict(enabled=BOOL)), {}, 'key_main_power', 'MAIN POWER钥匙开关按压（Matterix弹簧键）：切换通电状态，语义同power.set')
cap('system.shutdown', obj({}), obj(dict(shutdown=BOOL)), {}, ['monitor', 'control_electronics'], '软件关机（采集进行中时被拒，须先结束或取消采集）：关闭电子枪高压与灯丝、保存记录；之后才应把钥匙转到OFF')
cap('error.acknowledge', obj({}), obj(dict(acknowledged=TEXT)), {}, 'monitor', '确认当前错误（抽气超时、灯丝烧断、高压跳闸等），仪器回到可操作状态')
cap('vacuum.evacuate', obj({}), obj(dict(state=TEXT)), {}, ['button_evac', 'rotary_pump', 'turbo_molecular_pump', 'vacuum_valve_block'],
    'EVAC开关：关闭放气阀，机械泵粗抽样品室至约20Pa后切换涡轮分子泵抽高真空（LV模式时抽到设定低真空）；抽气中EVAC灯闪烁、完成后常亮；10min未完成报错（JSM-IT300手册）')
cap('vacuum.vent', obj({}), obj(dict(state=TEXT)), {}, ['button_vent', 'vent_valve', 'turbo_molecular_pump'],
    'VENT开关：关闭电子枪高压，停止涡轮分子泵抽样品室，打开放气阀引入宿主声明的放气气体；放气中VENT灯闪烁、达到大气压后常亮；样品台X自动回到交换位；Y也回0，'
    '但倾斜时若Y回位会使样品升到极靴下4mm以内则Y不动（事件Y_RETURN_SKIPPED）')
cap('vacuum.toggle_lv', obj({}), obj(dict(mode=enum(['HV', 'LV']))), {}, ['button_lv', 'lv_valve'],
    'LV开关：在高真空（HV）与低真空（LV，10–100Pa，由针阀维持设定压力）之间切换；观察中先自动关闭电子枪高压（事件HT_OFF）；样品室在真空下时立即转入对应抽气；'
    'LV抽气完成后LV灯常亮；LV下只能用BED成像（SED自动切换到BED）')
cap('vacuum.set_lv_pressure', obj(dict(pressure_pa=number('Pa', LV_RANGE_PA[0], LV_RANGE_PA[1]))), obj(dict(pressure_pa=number('Pa'))), dict(pressure_pa=50.0),
    ['lv_valve', 'monitor'], '设定低真空样品室压力10–100Pa（厂家LV压力范围）；只写设定值，实际压力随step由针阀调节')
cap('chamber.draw_out', obj({}), obj(dict(target_m=number('m'))), {}, ['chamber_door', 'stage_base'],
    '拉出样品室门（抽拉式换样，样品台随门出舱）：须已放气到大气压；门在真空下被大气压压住拉不开', 'agent', 'robot_skill')
cap('chamber.draw_in', obj({}), obj(dict(target_m=number('m'))), {}, ['chamber_door', 'stage_base'],
    '推回并关好样品室门：样品（或样品座）最高点须在极靴下至少4mm（背散射探测器挂在极靴下3.5mm，即离BED至少0.5mm）；按Z、倾斜与Y从当前位置到目标的整个扫掠范围严格检查，'
    '否则拒绝以免碰撞（先用Z旋钮降低样品台）；推回后样品台视为在舱内，装卸样品被拒', 'agent', 'robot_skill')
cap('specimen.load', obj(dict(specimen_id=TEXT)), obj(dict(specimen_id=TEXT, loaded=BOOL)), dict(specimen_id='fe_si_polished'), ['specimen_holder', 'stage_rot'],
    '把实验台上的样品装到样品座上（门须完全拉出）；样品直径≤150mm、高度≤48mm（厂家）', 'agent', 'robot_skill')
cap('specimen.unload', obj({}), obj(dict(specimen_id=TEXT)), {}, ['specimen_holder', 'stage_rot'], '从样品座取下样品放回实验台（门须完全拉出）', 'agent', 'robot_skill')
cap('stage.move_xy', obj(dict(x_m=number('m', STAGE_X_RANGE_M[0], STAGE_X_RANGE_M[1]), y_m=number('m', STAGE_Y_RANGE_M[0], STAGE_Y_RANGE_M[1]))),
    obj(dict(x_m=number('m'), y_m=number('m'))), dict(x_m=0.002, y_m=-0.001), ['stage_x', 'stage_y'],
    '电机驱动样品台X（80mm）/Y（40mm）移到目标位置（目录称优中心台；约3mm/s）；Y沿倾斜后的摇篮移动，倾斜时样品随Y升降：扫掠范围内最高点会进入极靴下4mm以内且高于当前位置时拒绝'
    '（按真实样品判定，另按所选样品座与输入的样品高度做软件限位）；只写目标，位置随step推进；须门关闭')
cap('stage.set_z', obj(dict(z_lift_m=number('m', Z_LIFT_RANGE_M[0], Z_LIFT_RANGE_M[1]))), obj(dict(z_lift_m=number('m'))), dict(z_lift_m=0.038),
    ['knob_stage_z', 'stage_z'], 'Z旋钮（手动）：升降样品台，升降0对应样品座顶面WD 48mm，0.043m对应WD 5mm；门完全拉出（且未下令推回）时不检查碰撞；样品台在舱内时，'
    '按当前到目标的Z与正在进行的倾斜/Y运动的扫掠范围检查，最高点会进入极靴下4mm以内且高于当前位置则拒绝（降低总是允许）；只写目标，随step推进')
cap('stage.set_tilt', obj(dict(tilt_rad=number('rad', TILT_RANGE_RAD[0], TILT_RANGE_RAD[1]))), obj(dict(tilt_rad=number('rad'))), dict(tilt_rad=0.35),
    ['knob_stage_tilt', 'stage_tilt'], 'T旋钮（手动）：倾斜−10°～90°（绕过样品座顶面的X轴，正值使样品朝门倾斜）；倾斜时样品边缘升高：样品台在舱内时按倾斜区间（含边缘最高的中间角度）'
    '与Z/Y扫掠范围检查，最高点会进入极靴下4mm以内且高于当前位置则拒绝（门完全拉出时不检查）；电子束落在倾斜样品顶面上距中心h·tanθ处（超出样品则落到样品座）；只写目标，随step推进')
cap('stage.rotate', obj(dict(rotation_rad=number('rad', ROT_RANGE_RAD[0], ROT_RANGE_RAD[1]))), obj(dict(rotation_rad=number('rad'))), dict(rotation_rad=1.0),
    ['door_center_cap', 'stage_rot'], 'R（手动）：样品旋转0–360°；只写目标，随step推进')
cap('beam.set_voltage', obj(dict(voltage_v=number('V', KV_RANGE_V[0], KV_RANGE_V[1]))), obj(dict(voltage_v=number('V'))), dict(voltage_v=15000.0), ['electron_gun', 'hv_power_supply'],
    '加速电压0.5–30kV（厂家）；设定按同系列手册取整到0.1kV（≤3kV）或1kV（>3kV）；高压开着时立即生效，换电压后需重新聚焦/消像散')
cap('beam.set_probe_current', obj(dict(current_a=number('A', PROBE_CURRENT_RANGE_A[0], PROBE_CURRENT_RANGE_A[1]))), obj(dict(current_a=number('A'))), dict(current_a=1e-10),
    ['condenser_lens', 'objective_lens'], '探针电流1pA–0.3μA（厂家）：聚光镜与光阑决定束流，束流越大束斑越大；只写设定值')
cap('beam.observation_on', obj({}), obj(dict(ht_on=BOOL)), {}, ['electron_gun', 'hv_power_supply', 'gun_alignment'],
    '开始观察（Observation）：加高压、灯丝自动加热到饱和并自动对中（约10s后形成图像）；须通电、真空就绪（高真空或低真空，真空就绪即门已关闭）、灯丝未烧断')
cap('beam.observation_off', obj({}), obj(dict(ht_on=BOOL)), {}, ['electron_gun', 'hv_power_supply'], '停止观察：关闭高压与灯丝加热（进行中的采集以BEAM_OFF失败）')
cap('gun.replace_filament', obj({}), obj(dict(filament_serial=INT)), {}, ['electron_gun', 'gun_cap'],
    '更换钨灯丝（预对中灯丝）：须放气到大气压（放气时高压已自动关闭）；新灯丝寿命为工程值（Weibull分布，平均300h）', 'agent', 'robot_skill')
cap('scan.set_magnification', obj(dict(magnification=number('1', MAG_RANGE[0], MAG_RANGE[1]))), obj(dict(magnification=number('1'), field_width_m=number('m'))),
    dict(magnification=2000.0), ['scan_coil_section', 'monitor'], '直接放大倍数×5–300,000（以128×96mm打印为准，厂家）：视场宽=0.128m/倍数')
cap('optics.autofocus', obj({}), obj(dict(focus_wd_m=number('m'))), {}, ['objective_lens', 'monitor'],
    '自动聚焦（AF）：须有束流与可成像的样品；把物镜聚焦到束斑所在的表面（误差约为景深的1/8，1σ）；屏上WD显示聚焦值')
cap('optics.set_focus', obj(dict(focus_wd_m=number('m', 0.001, 0.060))), obj(dict(focus_wd_m=number('m'))), dict(focus_wd_m=0.010), ['objective_lens', 'monitor'],
    '手动聚焦：设定物镜聚焦的工作距离；与样品实际距离之差造成离焦模糊（2αΔz）')
cap('optics.auto_stigmate', obj({}), obj(dict(astigmatism_m=number('m'))), {}, ['scan_coil_section', 'monitor'], '自动消像散（AS）：须有束流；把像散模糊降到约1nm')
cap('optics.auto_contrast_brightness', obj({}), obj(dict(gain=number('1'), offset=number('1'))), {}, ['monitor', 'bse_detector', 'sed_detector'],
    '自动衬度/亮度（ACB）：按当前视场实时帧（640×480、10µs）的灰度直方图——信号范围加减2.5σ像素噪声——设定探测器增益与偏置，使图像灰度落在20–235；'
    '没有信号衬度的视场（如平整样品的TOPO像）被拉伸到噪声宽度，增益有限')
cap('detector.select', obj(dict(detector=enum(['SED', 'REF', 'BED_COMPO', 'BED_TOPO', 'BED_SHADOW']))), obj(dict(detector=TEXT)), dict(detector='BED_COMPO'),
    ['sed_detector', 'bse_detector'], '选择成像探测器：SED（E-T二次电子，只能高真空）、REF（SED只收背散射电子在样品室/极靴上转换的SE3，高真空）或BED的成分（COMPO）、形貌（TOPO）、阴影（Shadow）像')
cap('image.acquire', obj(dict(pixels=enum(list(PIXEL_FORMATS)), dwell_s=number('s', 1e-7, 1e-2))), OPERATION, dict(pixels='640x480', dwell_s=2e-5),
    ['monitor', 'sed_detector', 'bse_detector', 'scan_coil_section'],
    '采集一帧图像（异步）：像素320×240–5120×3840（厂家），帧时间=像素数×驻留时间；返回按灰度分割的区域（按平均灰度排序，不给出物质名；平均灰度之差小于噪声与相内形貌纹理所定阈值的相并为一区）、'
    '各区平均灰度与信噪比、衬度（TOPO差分信号无定义，为null）、有效分辨率、显示的WD、荷电标记与32×24缩略图；束流或真空条件在采集中改变则失败')
cap('image.result', obj(dict(image_id=TEXT)), IMAGE, dict(image_id='img-1'), 'monitor', '读取图像记录（本调用者可见）', 'agent', 'query')
cap('eds.acquire', obj(dict(mode=enum(['point', 'area']), live_time_s=number('s', 1.0, 600.0), process_time=enum(list(EDS_PROCESS_TIMES)))), OPERATION,
    dict(mode='point', live_time_s=30.0, process_time='T3'), ['eds_finger', 'eds_detector_housing', 'monitor'],
    'EDS采谱（异步）：点分析（束斑处，含X射线激发体积内的相混合）或面分析（当前视场）；活时间1–600s，处理时间T1–T4（越长分辨率越好、死时间越大）；预计死时间超过90%时拒绝'
    '（降低探针电流）；结果为0–20.48keV的2048道计数谱、由谱估计的Duane–Hunt限（荷电时低于加速电压），以及按显示WD与倾斜读数计算的名义出射角/立体角')
cap('eds.result', obj(dict(spectrum_id=TEXT)), SPECTRUM, dict(spectrum_id='eds-1'), 'monitor', '读取EDS谱记录（本调用者可见）', 'agent', 'query')
cap('eds.quantify', obj(dict(spectrum_id=TEXT, elements=array(TEXT, 0, 16))), QUANT, dict(spectrum_id='eds-1', elements=[]), 'monitor',
    '无标样ZAF定量（厂家：standardless ZAF）：elements为空时自动识峰（B、Be与惰性气体不自动识别，可在elements中给出）；最小二乘拟合峰面积与连续谱，按记录的名义出射角迭代ZAF并'
    '归一化；返回质量分数与原子分数（0–1）、计数误差与检出限；自动识峰在5kV、T1/T2或10nA高计数等条件下可能多报或漏报元素，应核对后在elements中给出', 'agent', 'analysis')
cap('records.list', obj({}), obj(dict(images=array(TEXT, 0, MAX_RECORDS), spectra=array(TEXT, 0, MAX_RECORDS))), {}, 'monitor', '本调用者的图像与谱记录列表', 'agent', 'query')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS), dropped=INT)), dict(since_s=0.0), 'monitor', '读取事件日志', 'agent', 'query')
cap('ledger.read', obj({}), LEDGER, {}, 'cabinet_body', '受限：电荷账（入射、背散射、二次电子、吸收、样品存储电荷）、束流能量、气体物料账（进入、抽走、体积内）、X射线产生/探测光子数、灯丝工作时间（s）',
    'evaluator', 'query')
cap('provenance.read', obj({}), obj(dict(library=TEXT, substances=array(TEXT, 0, 100))), {}, 'specimen_holder', '受限：本实例用过的模板库记录', 'evaluator', 'query')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=10.0), 'cabinet_body',
    '唯一宿主时间推进（纳秒整数时钟；1s仪器周期在全局时间网格上执行）', 'evaluator', 'simulation')
UNSUPPORTED = {
    'eds.map': 'EDS元素面分布（最高4096×3072）与线扫描未实现：样品虽有Voronoi相图，但未实现逐像素采谱与元素图的生成和记录格式',
    'stage.navigation_camera': '样品台导航系统SNS（光学相机拍样品座全景）是选配件，本配置未装',
    'chamber.scope': '样品室监视相机（Chamber Scope）是选配件，本配置未装',
    'image.montage': '拼图（Montage，目录列为标准功能）与3D重构（选配）未实现',
    'detector.lsed': '低真空二次电子探测器LSED是选配件，本配置未装（LV下用BED）',
    'stage.motor_zt': 'Z/T/R电机驱动（5轴电机台）是选配件；本配置X/Y电机，Z/T/R手动',
    'hardware.connect': '无真实JEOL主机、PC或EDS FIS通信',
    'robot.handle_specimen': '无机器人装样、拉门、拧旋钮的接触验证',
    'accuracy.certification': '无厂家分辨率/定量准确度的验收测量',
}
UNSUPPORTED_COMPONENTS = {'eds.map': ['eds_finger', 'monitor'], 'stage.navigation_camera': ['chamber_door'], 'chamber.scope': ['chamber_body'],
                          'image.montage': ['monitor'], 'detector.lsed': ['chamber_body'], 'stage.motor_zt': ['knob_stage_z', 'knob_stage_tilt'],
                          'hardware.connect': ['rear_panel'], 'robot.handle_specimen': ['specimen_holder', 'chamber_door'],
                          'accuracy.certification': ['objective_lens', 'eds_finger']}
for _k, _v in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, UNSUPPORTED_COMPONENTS[_k], _v)

# ---- SEM Operation commands (front panel 1.1.0): what the InTouchScope windows, icons and console knobs do beyond the commands above
def _text(lo=0, hi=80):
    return {'type': 'string', 'minLength': lo, 'maxLength': hi}


FRACTION = number('1', 0.0, 1.0)
XY_M = array(number('m'), 2, 2)
SETTINGS_FIELDS = {          # section: {field: (schema, default)} - the Settings windows (manual 5-4) and the photo data / label area
    'scan': dict(rdc=(integer(1, 3), 1), fast=(integer(1, 5), 2), slow=(integer(1, 4), 2), photo_mode=(enum(['1frame', 'integration']), '1frame'),
                 photo=(integer(1, 10), 6), integration=(integer(1, 5), 3), frames=(integer(1, 255), 8)),
    'auto': dict(acb_interlock=(BOOL, False), acb_brightness=(FRACTION, 0.5), acb_contrast=(FRACTION, 0.5)),
    'stage': dict(auto_focus_tracer=(BOOL, False), frame_travel=(BOOL, False), frame_travel_rate=(number('1', 0.1, 1.0), 0.5)),
    'eco': dict(enabled=(BOOL, False), minutes=(integer(5, 120), 30)),
    'vacuum': dict(slow_leak=(BOOL, False)),
    'eos': dict(high_current=(BOOL, False)),
    'image_save': dict(auto_save=(BOOL, False), folder=(_text(1), 'E:/DATA/IT200/user'), file_name=(_text(1, 40), 'Image'), number=(integer(0, 99999), 1),
                       format=(enum(['TIFF', 'JPEG', 'BMP']), 'TIFF'), insert_photo_data=(BOOL, True), snapshot_folder=(_text(1), 'E:/DATA/IT200/user/Snapshot')),
    'movie': dict(folder=(_text(1), 'E:/DATA/IT200/user/Movie'), fps=(integer(1, 30), 15)),
    'photo_data': dict(label=(_text(0, 30), ''), show_number=(BOOL, True), show_date=(BOOL, True), show_logo=(BOOL, False), micron_bar=(BOOL, True)),
    'view': dict(mode=(enum(VIEW_MODES), 'standard'), mix_ratio=(FRACTION, 0.5)),
    'environment': dict(language=(enum(['English', 'Japanese']), 'English'), theme=(enum(['light', 'dark']), 'light'), show_navigator=(BOOL, True),
                        fixed_icons_right=(BOOL, False)),
    'signal': dict(sed_filter=(BOOL, False), bed_gain=(enum(['standard', 'analysis']), 'standard'), shadow_level=(FRACTION, 0.5)),
    'tilt_correction': dict(tilt_mag_deg=(number('deg', 0.0, 90.0), 0.0), dynamic_focus_deg=(number('deg', 0.0, 90.0), 0.0)),
    'lut': dict(gamma=(number('1', 0.3, 3.0), 1.0), pseudo_color=(BOOL, False)),
}
SETTINGS_SCHEMAS = {s: obj({k: v[0] for k, v in f.items()}) for s, f in SETTINGS_FIELDS.items()}
SETTINGS_DEFAULTS = {s: {k: v[1] for k, v in f.items()} for s, f in SETTINGS_FIELDS.items()}
PRESET_WINDOWS = {'accv': (KV_RANGE_V, [1000.0, 5000.0, 10000.0, 15000.0, 20000.0]), 'pc': ((1.0, 99.0), [30.0, 40.0, 50.0, 60.0, 65.0]),
                  'mag': (MAG_RANGE, [50.0, 500.0, 1000.0, 5000.0, 10000.0]), 'wd': ((0.005, 0.048), [0.008, 0.010, 0.015, 0.020, 0.030]),
                  'pressure': (LV_RANGE_PA, [30.0, 50.0, 100.0])}      # five presets per window (pressure: three, manual 3-37); values assumed
GUN = obj(dict(heating=FRACTION, bias_coarse=number('1', 0.0, 100.0), bias_fine=number('1', 0.0, 100.0), shift_x=number('1', -1.0, 1.0),
               shift_y=number('1', -1.0, 1.0), tilt_x=number('1', -1.0, 1.0), tilt_y=number('1', -1.0, 1.0)))
GUN_STATE = obj(dict(GUN['properties'], mode=enum(['auto', 'manual'])))
ALIGN_ERR = obj(dict(gun_shift_x=number('1', -1.0, 1.0), gun_shift_y=number('1', -1.0, 1.0), gun_tilt_x=number('1', -1.0, 1.0), gun_tilt_y=number('1', -1.0, 1.0),
                     aperture_x_m=number('m', -APERTURE_RANGE_M, APERTURE_RANGE_M), aperture_y_m=number('m', -APERTURE_RANGE_M, APERTURE_RANGE_M),
                     stage_x_m=number('m', -1e-3, 1e-3), stage_y_m=number('m', -1e-3, 1e-3)))
UV = number('1', 0.0, 1.0)
cap('beam.blank', obj(dict(on=BOOL)), obj(dict(blanked=BOOL)), dict(on=True), ['electron_gun', 'monitor'],
    'Beam Blanking（Home图标，手册4-6）：偏转电子束使其不到达样品（防损伤）；再按一次恢复；消隐时图像全黑、不荷电、不计剂量')
cap('beam.set_pc', obj(dict(pc=number('1', 1.0, 99.0))), obj(dict(pc=number('1'), current_a=number('A'))), dict(pc=50.0), ['condenser_lens', 'monitor'],
    '照片数据P.C.窗口（手册3-24）：按P.C.值1–99设定探针电流（标准模式1pA–30nA、EOS大束流模式10pA–0.3µA的对数刻度；刻度为假设），等同于beam.set_probe_current')
cap('scan.set_mode', obj(dict(mode=enum(['RDC', 'FAST', 'SLOW', 'FREEZE']), charge_free=BOOL)), obj(dict(mode=TEXT, charge_free=BOOL)),
    dict(mode='SLOW', charge_free=False), ['scan_coil_section', 'monitor'],
    '扫描模式图标（手册3-31）：RDC（缩小扫描区域，剂量集中在1/4视场宽）、Fast、Slow、Freeze（冻结图像，此时电子束消隐——假设）；CF只在Slow与拍照时降低荷电速率')
cap('scan.set_rotation', obj(dict(rotation_rad=number('rad', -10.0, 10.0))), obj(dict(rotation_rad=number('rad'))), dict(rotation_rad=math.pi / 2),
    ['scan_coil_section', 'monitor'], '扫描旋转SRT（手册4-15）：转动电子束扫描方向使图像旋转（±0.1°、90°/180°/270°、复位0°）；角度取0–2π')
cap('scan.rotate_to_line', obj(dict(u1=UV, v1=UV, u2=UV, v2=UV, orientation=enum(['horizontal', 'vertical']))), obj(dict(rotation_rad=number('rad'))),
    dict(u1=0.2, v1=0.6, u2=0.8, v2=0.4, orientation='horizontal'), ['scan_coil_section', 'monitor'],
    '扫描水平/垂直校正（手册4-16）：在图像上指定一条线的起止点（图像分数坐标），扫描旋转使该线变为水平或垂直')
cap('optics.set_stigmator', obj(dict(x_m=number('m', -STIG_RANGE_M, STIG_RANGE_M), y_m=number('m', -STIG_RANGE_M, STIG_RANGE_M))), obj(dict(x_m=number('m'), y_m=number('m'))),
    dict(x_m=5e-9, y_m=-3e-9), ['objective_lens', 'monitor'],
    '手动消像散Stigma ±X/±Y（手册3-29）：消像散器X/Y（以等效模糊表示，±100nm）；剩余像散=固有像散矢量−消像散器设定；AS把它调到约1nm')
cap('optics.set_image_adjust', obj(dict(brightness=FRACTION, contrast=FRACTION, suppress=FRACTION)), obj(dict(brightness=number('1'), contrast=number('1'), suppress=number('1'))),
    dict(brightness=0.55, contrast=0.5, suppress=0.5), ['monitor', 'sed_detector', 'bse_detector'],
    '手动亮度/衬度（手册3-26）与BED Suppress粗调（3-27）：0.5为ACB设定处；灰度=127.5+(g−127.5)·2^(4(衬度−0.5))+255(亮度−0.5)，BED另加255(Suppress−0.5)；ACB后复位到0.5')
cap('optics.set_beam_shift', obj(dict(x_m=number('m', -1e-3, 1e-3), y_m=number('m', -1e-3, 1e-3))), obj(dict(x_m=number('m'), y_m=number('m'), at_limit=BOOL)),
    dict(x_m=1e-6, y_m=0.0), ['scan_coil_section', 'monitor'],
    '电子束位移Beam Shift（手册4-10/11）：在扫描坐标中移动视场；偏转限±10µm（假设），超出时停在限位并报at_limit（须复位后用样品台移动）')
cap('optics.wobbler', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['objective_lens', 'objective_aperture_holder'],
    'Wobbler（Home图标，手册3-64）：物镜电流摆动；可动光阑偏离光轴时图像上下左右摆动（幅度∝偏离），对中后不动；摆动时拍照会离焦模糊')
cap('aperture.set_position', obj(dict(x_m=number('m', -APERTURE_RANGE_M, APERTURE_RANGE_M), y_m=number('m', -APERTURE_RANGE_M, APERTURE_RANGE_M))),
    obj(dict(x_m=number('m'), y_m=number('m'))), dict(x_m=1e-5, y_m=0.0), ['objective_aperture_drive', 'objective_aperture_holder'],
    '可动物镜光阑X/Y微调旋钮（镜筒上，手册3-64；机械旋钮，不需软件）：光阑偏离光轴使透过电流按exp(−(d/40µm)²)下降、图像模糊增加并在Wobbler下摆动', 'agent', 'robot_skill')
cap('gun.auto_adjust', obj(dict(mode=enum(['AGC', 'AFS']))), obj(dict(mode=TEXT, done_s=number('s'))), dict(mode='AGC'), ['electron_gun', 'gun_alignment'],
    '电子枪窗口自动调整（手册3-59）：AGC=灯丝加热到饱和并自动对中（倾斜与平移），AFS=只调灯丝加热；约30s（手册“几十秒”）后完成；须观察中，LV模式不可用')
cap('gun.set', GUN, GUN_STATE, dict(heating=GUN_SAT_POS, bias_coarse=50.0, bias_fine=50.0, shift_x=0.0, shift_y=0.0, tilt_x=0.0, tilt_y=0.0),
    ['electron_gun', 'gun_alignment'],
    '电子枪窗口手动调整（手册3-60–63）：灯丝加热条（0.6为饱和点，左侧有假峰，右侧橙色区过流缩短寿命）、栅偏压粗/细（改变负载电流L.C.与束流）、对中Shift/Tilt X/Y；'
    '手动后观察开始时不再自动调整，直到再做AGC/AFS')
cap('gun.bias_memory', obj(dict(action=enum(['save', 'load']))), obj(dict(voltage_v=number('V'), bias_coarse=number('1'), bias_fine=number('1'))), dict(action='save'),
    ['electron_gun', 'monitor'], '电子枪窗口Save/Preset（手册3-60）：每个加速电压保存一组栅偏压粗/细值，Preset读回当前电压的保存值')
cap('alignment.configure_error', ALIGN_ERR, ALIGN_ERR, dict(gun_shift_x=0.2, gun_shift_y=0.0, gun_tilt_x=0.0, gun_tilt_y=-0.1, aperture_x_m=2e-5, aperture_y_m=0.0,
                                                           stage_x_m=0.0, stage_y_m=0.0), ['gun_alignment', 'objective_aperture_holder', 'stage_x', 'stage_y'],
    '受限：设定误差参数（默认0=理想）：电子枪对中偏差、可动光阑偏离光轴、样品台X/Y坐标偏差；AGC/手动对中、光阑旋钮与样品台校准（Calibration）按手册把它们找回', 'evaluator', 'configuration')
# messages of the JSM-IT300 manual 3.14 that need a hardware fault (1.2.0 sweep): evaluator fault.inject / fault.clear; 0261 needs the R motor and
# 0360 / 0380 the VMIU, both absent options
JSM_FAULTS = {'stage_driver_overheat': 'STAGE_DRIVER_OVERHEAT', 'stage_mechanical_limit': 'STAGE_MECHANICAL_LIMIT', 'stage_motor_failure': 'STAGE_MOTOR_FAILURE',
              'stage_lens_contact': 'STAGE_LENS_CONTACT', 'gun_chamber_open': 'GUN_CHAMBER_OPEN', 'lv_pressure_adjust': 'LV_PRESSURE_ADJUST_FAILED',
              'filament_failure': 'FILAMENT_FAILURE'}
STAGE_FAULTS = ('stage_driver_overheat', 'stage_mechanical_limit', 'stage_motor_failure', 'stage_lens_contact')
_FAULT_IN = obj(dict(code=enum(list(JSM_FAULTS))))
cap('fault.inject', _FAULT_IN, obj(dict(injected=array(TEXT, 0, 7))), dict(code='stage_motor_failure'), ['monitor', 'stage_x', 'stage_y'],
    '受限：注入手册3.14中需要硬件故障的信息：样品台电机驱动过热（0200）、超出机械限位（0240）、电机故障/位置读不出（0250）、样品台碰到物镜（0260）→ 电机停止、X/Y电机命令被拒；'
    '电子枪室未关（0331）→ EVAC被拒；自动压力调节失败（0351）→ LV自动压力调节停止；灯丝故障（晶须）→ 不能开始观察；有错误信息待确认', 'evaluator', 'simulation')
cap('fault.clear', _FAULT_IN, obj(dict(injected=array(TEXT, 0, 7))), dict(code='stage_motor_failure'), ['monitor', 'stage_x', 'stage_y'],
    '受限：手册措施已完成（服务修理、关好电子枪室、检查后无损伤）：消除故障原因；0250也可在放气后执行样品台初始化（stage.calibrate all）清除，灯丝故障也可换灯丝清除', 'evaluator', 'simulation')
RECIPE_IN = obj(dict(category=enum(list(RECIPES)), coated=BOOL, method=enum(['low_kv', 'low_vacuum']), eds=BOOL))
cap('recipe.execute', RECIPE_IN, obj(dict(voltage_v=number('V'), pc=number('1'), detector=TEXT, vacuum_mode=TEXT, lv_pressure_pa=nullable(number('Pa')))),
    dict(category='general', coated=True, method='low_kv', eds=False), ['monitor', 'electron_gun', 'bse_detector'],
    'Recipes窗口Execute（手册3-11/12，表3-1）：按样品类别与勾选项（镀膜/未镀膜低加速电压/未镀膜低真空、EDS）设定加速电压、P.C.、探测器与真空模式（LV时设压力）；'
    '表中“-”的组合被拒；表值为文本副本的重建（见限制）')
cap('stage.stop', obj({}), obj(dict(x_m=number('m'), y_m=number('m'))), {}, ['stage_x', 'stage_y'], '坐标窗口Stop（手册3-45）：X/Y电机停在当前位置')
cap('stage.last_move', obj({}), obj(dict(x_m=number('m'), y_m=number('m'))), {}, ['stage_x', 'stage_y'],
    'Last Move（手册3-47）：回到上一次移动前的坐标（再按一次回到移动后的坐标）')
cap('stage.click_center', obj(dict(u=number('1', -1.0, 2.0), v=number('1', -1.0, 2.0))), obj(dict(x_m=number('m'), y_m=number('m'))), dict(u=0.7, v=0.4),
    ['stage_x', 'stage_y', 'monitor'], 'Click Center与导航XY按钮（手册3-63、5-10）：把图像上（图像分数坐标u向右、v向下）的点移到视场中心——按样品台旋转与扫描旋转换算成X/Y电机移动')
cap('stage.memory', obj(dict(action=enum(['record', 'move', 'delete']), name=_text(0, 40))), obj(dict(name=TEXT, x_m=number('m'), y_m=number('m'))),
    dict(action='record', name='site_a'), ['stage_x', 'stage_y', 'monitor'],
    'Memory（手册3-47/48）：Record Current Position登记当前X/Y（名称空时用日期时间）、Move移到登记坐标、删除；最多100条')
cap('stage.calibrate', obj(dict(axes=enum(['all', 'x', 'y']))), obj(dict(axes=TEXT, correction_m=XY_M)), dict(axes='all'), ['stage_x', 'stage_y'],
    'Settings–Stage的Calibration（手册3-65）：放气并取下样品后执行，所选轴走到限位再回到交换位，校正坐标偏差（之后示值=真实位置）')
cap('specimen.set_info', obj(dict(name=_text(0, 40), holder=enum(HOLDERS), height_m=number('m', 0.0, SPECIMEN_MAX_HEIGHT_M))),
    obj(dict(name=TEXT, holder=TEXT, height_m=number('m'))), dict(name='Zn standard', holder='32mm_multipurpose', height_m=0.003), ['monitor', 'specimen_holder'],
    '样品信息（换样向导第2步与样品座/高度区，手册3-10、5-13）：样品名、样品座种类与凸出高度（1mm步进）；X/Y电机的软件限位按所选样品座外廓与输入高度计算'
    '（手册2-7：越过限位线即停并报错），导航区的样品座图画所选样品座；Z/T/R是手动轴，软件不能限位')
cap('settings.configure', obj(dict(section=enum(list(SETTINGS_FIELDS)), values={'anyOf': list(SETTINGS_SCHEMAS.values())})),
    obj(dict(section=TEXT, values={'type': 'object'})), dict(section='image_save', values=dict(SETTINGS_DEFAULTS['image_save'], auto_save=True)),
    ['monitor'], 'Settings图标窗口与照片数据/标签区（手册5-4、3-31–3-56）：扫描组合与拍照模式、ACB联动与目标、样品台（自动聚焦跟踪、帧移动比例）、Eco待机、慢抽气、EOS大束流、'
    '图像保存、录像、照片数据与标签、视图模式、语言/主题/布局、信号高级设置、倾斜校正、LUT；每节一次写入全部字段')
cap('presets.configure', obj(dict(window=enum(list(PRESET_WINDOWS)), index=integer(0, 4), value=number('1'))), obj(dict(window=TEXT, presets=array(number('1'), 3, 5))),
    dict(window='mag', index=2, value=2000.0), ['monitor'],
    '预设按钮（手册3-23–3-37）：把滑块选定的值拖放到预设按钮上改写该预设（加速电压、P.C.、倍数、WD各5个，压力3个）')
cap('ui.select', obj(dict(window=enum(['accv', 'pressure']), value=number('1'))), obj(dict(window=TEXT, value=number('1'))),
    dict(window='accv', value=10000.0), ['monitor'], '加速电压窗口（V）与压力窗口（Pa）的选定值（手册3-23、3-37：预设或滑块选定，Apply才生效）；超出该窗口范围时拒绝')
cap('vacuum.lv_auto', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=False), ['lv_valve', 'monitor'],
    '压力窗口Apply/Stop（手册3-37）：Stop停止自动调压（针阀停在当前开度，压力随之漂移），Apply重新开始按设定值自动调压')
cap('image.save', obj(dict(name=_text(1, 40))), obj(dict(file=TEXT)), dict(name='site_a'), ['monitor'],
    'Home–Save（手册3-53，自动保存关时）：把冻结图像或最近的照片按设定格式保存到图像目录')
cap('image.open', obj(dict(file=_text(0, 120))), obj(dict(file=nullable(TEXT))), dict(file='Image_0001.tif'), ['monitor'],
    'Home–Open（手册5-3）：在主屏上打开已保存的图像文件（空名关闭，回到实时图像）')
cap('image.copy', obj({}), obj(dict(source=TEXT)), {}, ['monitor'], 'Home–Copy（手册5-3）：把当前图像登记到剪贴板')
cap('image.anaglyph', obj(dict(left_file=_text(1, 120), right_file=_text(1, 120))), obj(dict(file=TEXT, tilt_difference_rad=number('rad'))),
    dict(left_file='Image_0001.tif', right_file='Image_0002.tif'), ['monitor'],
    'Home–3D Imaging（手册5-3）：由两张倾斜角不同（≥1°）的图像生成红/青立体图文件（3D测量软件为选配，不提供）')
cap('movie.record', obj(dict(on=BOOL)), obj(dict(recording=BOOL, file=nullable(TEXT))), dict(on=True), ['monitor'],
    'Home–Movie（手册3-56）：录制实时图像（640×480，按设定帧率）；全屏视图下不能录，录制中不能切换视图；停止时生成yyyymmddhhmmss_0000.avi文件')
cap('snapshot.take', obj(dict(file=_text(0, 120))), obj(dict(source=TEXT)), dict(file=''), ['monitor'],
    'Snap Shot的Snap图标与打开文件图标（手册3-55、5-12）：把主屏的实时或冻结图像（file为空）或已保存的图像文件连同观察条件贴到快照区')
cap('snapshot.reproduce', obj({k: BOOL for k in ('accv', 'pc', 'mag', 'wd', 'signal', 'stage')}), obj(dict(applied=array(TEXT, 0, 6))),
    dict(accv=True, pc=False, mag=True, wd=False, signal=False, stage=False), ['monitor'], '快照的条件再现图标（手册5-12）：勾选要再现的条件（加速电压、P.C.、倍数、WD、信号、样品台坐标）后Apply')
cap('snapshot.save', obj({}), obj(dict(file=TEXT)), {}, ['monitor'], '从快照保存图像（手册3-55）：保存到快照目录')
cap('measure.ruler', obj(dict(u1=UV, v1=UV, u2=UV, v2=UV)), obj(dict(distance_m=number('m'), angle_deg=number('deg'))), dict(u1=0.25, v1=0.5, u2=0.75, v2=0.5),
    ['monitor'], 'Home–Ruler（手册5-3）：在主屏图像上量两点距离与连线角度（按显示图像的视场宽度换算）；保留最近20条')
cap('eds.transfer_image', obj({}), obj(dict(source=TEXT)), {}, ['monitor', 'eds_finger'], 'Home–Transfer image（手册5-3）：把当前视场图像传到EDS分析窗口作参考图')
cap('user.manage', obj(dict(action=enum(['add', 'switch']), name=_text(1, 30))), obj(dict(user=TEXT, users=array(TEXT, 1, 50))), dict(action='add', name='guest'),
    ['monitor'], 'Settings–User（手册5-4）：登录/切换用户（新用户的数据目录E:/DATA/IT200/<用户>，Iowa State）')

# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference)


STATES = ['off', 'booting', 'ready', 'shutdown']
VAC_STATES = ['vented', 'venting', 'roughing', 'hv_pumping', 'hv_ready', 'lv_pumping', 'lv_ready', 'idle']
LAMP_MODES = ['off', 'blinking', 'on']
ABS = '绝对压力'
slot('clock.time_s', number('s'), 's', 'derived', 'cabinet_body', '宿主时钟；read不推进')
slot('plant.configured', BOOL, '1', 'derived', 'cabinet_body', '实验室条件已声明')
slot('power.enabled', BOOL, '1', 'derived', 'key_main_power', '整机通电（钥匙在ON/START）')
slot('system.state', enum(STATES), '1', 'derived', 'monitor', '软件状态：off/booting（约60s加载）/ready/shutdown（已软件关机，等待钥匙OFF）')
slot('system.error_code', nullable(TEXT), '1', 'derived', 'monitor', '待确认的错误代码（EVAC_TIMEOUT、FILAMENT_BURNOUT、HT_TRIP、MAINS_FAULT；1.2.0起还有评测者注入的'
     'STAGE_DRIVER_OVERHEAT、STAGE_MECHANICAL_LIMIT、STAGE_MOTOR_FAILURE、STAGE_LENS_CONTACT、LV_PRESSURE_ADJUST_FAILED、FILAMENT_FAILURE）；无为null')
slot('fault.injected', array(TEXT, 0, 7), '1', 'setpoint', 'monitor', '评测者注入且尚未消除的硬件故障原因（fault.inject，1.2.0）', 'evaluator')
slot('vacuum.mode', enum(['HV', 'LV']), '1', 'setpoint', 'button_lv', '真空模式：HV高真空或LV低真空')
slot('vacuum.state', enum(VAC_STATES), '1', 'derived', 'vacuum_valve_block',
     '真空系统状态：vented已放气/venting放气中/roughing粗抽/hv_pumping涡轮泵抽高真空/hv_ready高真空就绪/lv_pumping与lv_ready低真空/idle断电后停泵')
slot('vacuum.lv_setpoint_pa', number('Pa'), 'Pa', 'setpoint', 'lv_valve', '低真空样品室压力设定（10–100Pa）', reference=ABS)
slot('vacuum.chamber_pressure_indicated_pa', nullable(number('Pa')), 'Pa', 'sensor', 'vacuum_valve_block',
     '样品室皮拉尼真空计示值（有效范围约0.5Pa–大气压，低于0.5Pa显示0.5）；断电为null', reference=ABS)
slot('vacuum.gun_pressure_indicated_pa', nullable(number('Pa')), 'Pa', 'sensor', 'electron_gun', '电子枪/镜筒潘宁真空计示值（1e-6–1e-1Pa，高于0.1Pa显示0.1）；断电为null', reference=ABS)
slot('vacuum.lv_pressure_indicated_pa', nullable(number('Pa')), 'Pa', 'sensor', 'lv_valve', 'LV模式样品室压力示值（0.5%相对噪声，工程值；传感器类型厂家未公开）；非LV模式或断电为null（屏上Pa读数为空）',
     reference=ABS)
slot('vacuum.evacuation_elapsed_s', nullable(number('s')), 's', 'derived', 'vacuum_valve_block', '本次抽气已进行时间；未在抽气为null')
for _lamp, _zh in (('vent', 'VENT（放气中闪烁，大气压时常亮）'), ('evac', 'EVAC（抽气中闪烁，完成常亮）'), ('lv', 'LV（LV抽气中闪烁，LV就绪常亮）')):
    slot(f'panel.{_lamp}_lamp', BOOL, '1', 'derived', f'button_{_lamp}', f'{_zh}开关灯此刻是否点亮（闪烁按1Hz、半周期亮）；断电为false')
    slot(f'panel.{_lamp}_lamp_mode', enum(LAMP_MODES), '1', 'derived', f'button_{_lamp}', f'{_zh}开关灯模式off/blinking/on')
slot('chamber.door_state', enum(['closed', 'open', 'moving']), '1', 'derived', 'chamber_door', '样品室门状态（完全关闭/完全拉出/移动中）')
slot('specimen.loaded_id', nullable(TEXT), '1', 'derived', 'specimen_holder', '样品座上的样品ID；无为null')
slot('specimen.bench_ids', array(TEXT, 0, 20), '1', 'derived', 'specimen_holder', '实验台上可装的样品ID')
slot('stage.x_indicated_m', number('m'), 'm', 'sensor', 'stage_x', 'X电机编码器读数（0.1µm分辨率）')
slot('stage.y_indicated_m', number('m'), 'm', 'sensor', 'stage_y', 'Y电机编码器读数（0.1µm分辨率）')
slot('stage.z_indicated_m', number('m'), 'm', 'sensor', 'knob_stage_z', 'Z旋钮刻度读数（升降量，0.1mm分辨率）')
slot('stage.tilt_indicated_rad', number('rad'), 'rad', 'sensor', 'knob_stage_tilt', 'T旋钮刻度读数（0.5°分辨率）')
slot('stage.rotation_indicated_rad', number('rad'), 'rad', 'sensor', 'door_center_cap', 'R刻度读数（1°分辨率）')
slot('stage.moving', BOOL, '1', 'derived', 'stage_x', '样品台任一轴仍在向目标移动')
slot('beam.accelerating_voltage_v', number('V'), 'V', 'setpoint', 'electron_gun', '加速电压设定（屏上kV读数）')
slot('beam.probe_current_setpoint_a', number('A'), 'A', 'setpoint', 'condenser_lens', '探针电流设定')
slot('beam.ht_on', BOOL, '1', 'derived', 'hv_power_supply', '电子枪高压开着（观察中）')
slot('beam.filament_state', enum(['off', 'heating', 'saturated', 'burned_out']), '1', 'derived', 'electron_gun', '灯丝状态：off/heating（自动加热中）/saturated/burned_out')
slot('beam.emission_indicated_a', nullable(number('A')), 'A', 'sensor', 'hv_power_supply', '发射电流表读数；断电或高压关为null')
slot('scan.magnification', number('1'), '1', 'setpoint', 'scan_coil_section', '直接放大倍数（屏上×读数）')
slot('scan.field_width_m', number('m'), 'm', 'derived', 'scan_coil_section', '视场宽度=0.128m/倍数')
slot('optics.focus_wd_m', number('m'), 'm', 'setpoint', 'objective_lens', '物镜聚焦的工作距离（屏上WD读数；聚焦后接近样品实际WD）')
slot('optics.astigmatism_corrected', BOOL, '1', 'derived', 'scan_coil_section', '当前电压/灯丝下已做过自动消像散')
slot('optics.acb_done', BOOL, '1', 'derived', 'monitor', '当前条件下已做过自动衬度/亮度')
slot('detector.selected', enum(['SED', 'REF', 'BED_COMPO', 'BED_TOPO', 'BED_SHADOW']), '1', 'setpoint', 'bse_detector', '当前成像探测器')
slot('acquisition.id', nullable(TEXT), '1', 'derived', 'monitor', '进行中的采集（图像或EDS）ID；无或属于另一个agent时为null')
slot('acquisition.kind', nullable(enum(['image', 'eds'])), '1', 'derived', 'monitor', '进行中采集的种类；无为null')
slot('acquisition.progress', nullable(number('1', 0.0, 1.0)), '1', 'derived', 'monitor', '进行中采集的进度（0–1）；无为null')
slot('acquisition.started_by_other_agent', BOOL, '1', 'derived', 'monitor', '进行中的采集属于另一个agent（按调用者计算）')
slot('result.last_image_id', nullable(TEXT), '1', 'derived', 'monitor', '本调用者最近完成的图像ID')
slot('result.last_spectrum_id', nullable(TEXT), '1', 'derived', 'monitor', '本调用者最近完成的EDS谱ID')
# process truth (evaluator): scene-joint mirrors and hidden physics
slot('chamber.door_position_m', number('m', 0.0, DOOR_TRAVEL_M), 'm', 'process_truth', 'chamber_door', '门拉出量（0关闭，0.20m完全拉出；过程权威，镜像到门关节）', 'evaluator',
     '沿−Y（朝操作者），关闭为0。')
slot('stage.x_m', number('m', STAGE_X_RANGE_M[0], STAGE_X_RANGE_M[1]), 'm', 'process_truth', 'stage_x', '样品台X位置（过程权威，镜像到X关节）', 'evaluator', '沿+X，交换位为0。')
slot('stage.y_m', number('m', STAGE_Y_RANGE_M[0], STAGE_Y_RANGE_M[1]), 'm', 'process_truth', 'stage_y', '样品台Y位置（沿倾斜后的Y；镜像到Y关节）', 'evaluator', '沿倾斜摇篮的+Y，交换位为0。')
slot('stage.z_lift_m', number('m', Z_LIFT_RANGE_M[0], Z_LIFT_RANGE_M[1]), 'm', 'process_truth', 'stage_z', '样品台Z升降量（0=样品座顶面WD 48mm；镜像到Z关节与Z旋钮）', 'evaluator',
     '沿+Z，升降0时样品座顶面在极靴下48mm。')
slot('stage.tilt_rad', number('rad', TILT_RANGE_RAD[0], TILT_RANGE_RAD[1]), 'rad', 'process_truth', 'stage_tilt', '样品台倾斜角（镜像到倾斜关节与T旋钮）', 'evaluator',
     '绕+X；正值使样品表面法线转向门（−Y）。')
slot('stage.rotation_rad', number('rad', ROT_RANGE_RAD[0], ROT_RANGE_RAD[1]), 'rad', 'process_truth', 'stage_rot', '样品旋转角（镜像到旋转关节）', 'evaluator', '绕样品座法线，俯视逆时针为正。')
for _k, _u, _c, _m in [('truth.chamber_pressure_pa', 'Pa', 'chamber_body', '样品室真实绝对压力'), ('truth.gun_pressure_pa', 'Pa', 'electron_gun', '电子枪/镜筒真实绝对压力'),
                       ('truth.gas_load_pa_m3_s', 'Pa*m^3/s', 'chamber_body', '当前气体负荷（漏气+放气+含水样品+针阀进气）'),
                       ('truth.tmp_speed_fraction', '1', 'turbo_molecular_pump', '涡轮分子泵转速（额定的分数）'),
                       ('truth.filament_on_time_s', 's', 'electron_gun', '当前灯丝已工作时间（加热斜坡按发射线性积分）'),
                       ('truth.filament_life_s', 's', 'electron_gun', '当前灯丝的（随机，Weibull）寿命；平均300h=1.08×10⁶s'),
                       ('truth.emission_a', 'A', 'electron_gun', '真实发射电流'), ('truth.probe_current_a', 'A', 'objective_lens', '真实到达样品的探针电流'),
                       ('truth.probe_diameter_m', 'm', 'objective_lens', '当前条件下的探针直径（最优孔径角）'),
                       ('truth.working_distance_m', 'm', 'specimen_holder', '极靴到束斑处样品表面的真实工作距离'),
                       ('truth.defocus_m', 'm', 'objective_lens', '聚焦WD与真实WD之差'), ('truth.astigmatism_m', 'm', 'scan_coil_section', '像散造成的等效模糊'),
                       ('truth.specimen_charge_c', 'C', 'specimen_holder', '绝缘样品表面累积的电荷')]:
    slot(_k, number(_u), _u, 'process_truth', _c, _m, 'evaluator', ABS if _u == 'Pa' else None)
slot('truth.specimens', {'type': 'object', 'additionalProperties': {'type': 'object'}}, '1', 'process_truth', 'specimen_holder', '各样品的真实组成、形貌、镀膜与位置', 'evaluator')
# SEM Operation (front panel 1.1.0)
slot('beam.blanked', BOOL, '1', 'setpoint', 'electron_gun', 'Beam Blanking开着：电子束被偏转，不到达样品')
slot('beam.probe_current_pc', number('1'), '1', 'derived', 'monitor', '照片数据的P.C.值（标准模式1pA–30nA、大束流模式10pA–0.3µA对数刻度上的1–99；直接设定的电流可落在刻度外）')
slot('scan.mode', enum(['RDC', 'FAST', 'SLOW', 'FREEZE']), '1', 'setpoint', 'scan_coil_section', '实时扫描模式（Freeze时电子束消隐，拍照或EDS时恢复）')
slot('scan.charge_free', BOOL, '1', 'setpoint', 'scan_coil_section', 'CF图标开着（Slow与拍照时降低荷电速率）')
slot('scan.rotation_rad', number('rad', 0.0, 2 * math.pi), 'rad', 'setpoint', 'scan_coil_section', '扫描旋转SRT角度')
slot('optics.stigmator_m', XY_M, 'm', 'setpoint', 'objective_lens', '消像散器X/Y设定（等效模糊）')
slot('optics.image_adjust', obj(dict(brightness=number('1'), contrast=number('1'), suppress=number('1'))), '1', 'setpoint', 'monitor',
     '手动亮度、衬度与BED Suppress（0–1；0.5为ACB设定处）')
slot('optics.beam_shift_m', XY_M, 'm', 'setpoint', 'scan_coil_section', '电子束位移X/Y（扫描坐标，±10µm限位）')
slot('optics.wobbler_on', BOOL, '1', 'setpoint', 'objective_lens', 'Wobbler开着')
slot('aperture.position_m', XY_M, 'm', 'setpoint', 'objective_aperture_drive', '可动物镜光阑X/Y微调旋钮位置')
slot('gun.settings', GUN_STATE, '1', 'derived', 'gun_alignment', '电子枪窗口设定（AGC/AFS完成时由自动调整改写）：灯丝加热条（0.6为饱和点）、栅偏压粗/细、对中Shift/Tilt X/Y、auto（观察开始时自动调整）或manual')
slot('gun.auto_adjust', nullable(enum(['AGC', 'AFS'])), '1', 'derived', 'gun_alignment', '正在进行的自动调整（AGC/AFS，约30s）；无为null')
slot('operation.state', {'type': 'object'}, '1', 'derived', 'monitor',
     'SEM Operation软件的设置与数据：settings（各设置窗口）、presets、selected、files（保存的文件）、memory（坐标记忆）、snapshot、measurements、movie、specimen、users/user、viewer、'
     'clipboard、eds_image、gun_bias、last_move、calibrated_s')
slot('truth.alignment_error', ALIGN_ERR, '1', 'process_truth', 'gun_alignment', '误差参数：电子枪对中、可动光阑偏离光轴、样品台坐标偏差（默认0=理想）', 'evaluator')


class _Sum:
    """Neumaier compensated sum (ledger)."""
    __slots__ = ('s', 'c')

    def __init__(self):
        self.s, self.c = 0.0, 0.0

    def add(self, x):
        t = self.s + x
        self.c += (self.s - t) + x if abs(self.s) >= abs(x) else (x - t) + self.s
        self.s = t

    @property
    def value(self):
        return self.s + self.c


LEDGER_FLOWS = ['beam_charge_c', 'backscattered_charge_c', 'secondary_charge_c', 'absorbed_charge_c', 'beam_energy_j', 'gas_admitted_mol', 'gas_pumped_mol',
                'xray_generated', 'xray_detected', 'filament_on_time_s', 'electrons_on_specimen']
COATINGS = {'carbon': {'C': 1.0}, 'gold': {'Au': 1.0}, 'platinum': {'Pt': 1.0}}
COATING_RHO = {'carbon': 2.0, 'gold': 19.3, 'platinum': 21.45}
HOLDER_SUBSTANCE = 'aluminium'


def quantize_kv(v):
    step = KV_FINE_STEP_V if v <= KV_FINE_MAX_V + 1e-9 else KV_COARSE_STEP_V
    return round(round(v / step) * step, 6)


def _panel_engine():
    try:
        from . import _panel_ui as engine                                   # imported as asset_runtime.jeol_jsmit200 (tests, Matterix bridge)
        return engine
    except ImportError:                                                      # loaded from its file path
        import importlib.util
        import sys
        if '_chemsim_panel_ui' not in sys.modules:
            spec = importlib.util.spec_from_file_location('_chemsim_panel_ui', Path(__file__).with_name('_panel_ui.py'))
            sys.modules['_chemsim_panel_ui'] = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
        return sys.modules['_chemsim_panel_ui']


_panel_ui = _panel_engine()
if os.environ.get('JSMIT200_PANEL_BUILD'):                                   # spec builder: runtime constants without the spec being written
    PANEL_SPEC, _PANEL_DEFS = None, {}
else:
    PANEL_SPEC = _panel_ui.load_spec(ASSET / 'panel_ui.json', capabilities=set(CAPS))
    _PANEL_DEFS = _panel_ui.capability_definitions(PANEL_SPEC)               # panel.press / turn / switch / touch / capture / frame
PANEL_CAPS = {k: dict(input_schema=d['input_schema'], output_schema=d['output_schema'], example_arguments=d['example_arguments'],
                      component_ids=list(d['component_ids']), function_zh=d['function_zh'], visibility=d['visibility'], kind=d['kind'], effects=[])
              for k, d in _PANEL_DEFS.items()}                              # kept out of CAPS: CAPS stays the instrument's command set
ALL_CAPS = dict(CAPS, **PANEL_CAPS)
_ACB_INTERLOCK = {'beam.set_voltage', 'beam.set_probe_current', 'beam.set_pc', 'detector.select', 'recipe.execute'}


class _SemOperation:
    """SEM Operation (InTouchScope UI) functions beyond the basic commands: beam blanking, scan modes / rotation, manual stigmator, brightness /
    contrast, beam shift, wobbler and the movable aperture, the electron gun window (AGC / AFS, heating, bias, alignment), P.C. scale and EOS high
    current mode, recipes, stage memory / last move / click center / calibration, the Settings windows, files, movie, snapshot, ruler, users.
    Error parameters (gun alignment, aperture offset, stage coordinates) default to 0 (ideal) and are found back by the documented procedures."""

    def _init_operation(self):
        self.blanked = False
        self.scan_mode, self.charge_free, self.srt = 'FAST', False, 0.0
        self.stig = getattr(self, 'stig', [0.0, 0.0])
        self.image_adjust = dict(brightness=0.5, contrast=0.5, suppress=0.5)
        self.beam_shift = [0.0, 0.0]
        self.wobbler = False
        self.aperture = [0.0, 0.0]
        self.gun = dict(heating=GUN_SAT_POS, bias_coarse=50.0, bias_fine=50.0, shift_x=0.0, shift_y=0.0, tilt_x=0.0, tilt_y=0.0)
        self.gun_mode = 'auto'
        self.gun_auto = None
        self.align_err = dict(gun_shift_x=0.0, gun_shift_y=0.0, gun_tilt_x=0.0, gun_tilt_y=0.0, aperture_x_m=0.0, aperture_y_m=0.0, stage_x_m=0.0, stage_y_m=0.0)
        self.stage_cal = [0.0, 0.0]
        self.last_op_s = 0.0
        self.frozen = None
        self.file_seq = 0
        self._thumbs = {}
        self._live_cache = None
        self._slow_prev = None                   # Slow scan: the screen before the last condition change and when the repaint began
        self.sem_ui = dict(settings=copy.deepcopy(SETTINGS_DEFAULTS), presets={k: list(v[1]) for k, v in PRESET_WINDOWS.items()}, selected=dict(accv=None, pressure=None), lv_auto=True,
                           files=[], memory=[], snapshot=None, measurements=[], movie=None, specimen=dict(name='', holder=HOLDERS[0], height_m=0.0),
                           users=['user'], user='user', viewer=None, clipboard=None, eds_image=None, gun_bias={}, last_move=None, calibrated_s=None)

    # ---- residual astigmatism: a vector (intrinsic astigmatism minus the stigmator); the commands above set its magnitude
    @property
    def astig(self):
        return self._astig_r

    @astig.setter
    def astig(self, r):
        self._astig_r = float(r)
        if getattr(self, '_astig_dir', None) is None:
            self._astig_dir = (math.cos(0.6), math.sin(0.6))

    def _set_stig(self, x, y):
        rx = self._astig_r * self._astig_dir[0] + self.stig[0] - x
        ry = self._astig_r * self._astig_dir[1] + self.stig[1] - y
        self.stig = [float(x), float(y)]
        r = math.hypot(rx, ry)
        self._astig_r = r
        if r > 0.0:
            self._astig_dir = (rx / r, ry / r)

    # ---- beam current factors (exactly 1 at the defaults)
    def _beam_blanked(self):
        return self.blanked or (self.scan_mode == 'FREEZE' and self.acq is None)

    def _emission_factor(self):
        """Filament heating against the saturation point (a W hairpin shows a false peak at about half of it) and the gun bias (load current)."""
        g = self.gun
        x = g['heating'] / GUN_SAT_POS
        e = 1.0 if x >= 1.0 else max(x ** 3, 0.6 * math.exp(-((x - 0.5) / 0.08) ** 2))
        return e * 2.0 ** (-(g['bias_coarse'] - 50.0) / BIAS_COARSE_OCTAVE - (g['bias_fine'] - 50.0) / BIAS_FINE_OCTAVE)

    def _aperture_offset(self):
        return math.hypot(self.aperture[0] - self.align_err['aperture_x_m'], self.aperture[1] - self.align_err['aperture_y_m'])

    def _align_factor(self):
        g, e = self.gun, self.align_err
        d2 = sum((g[k] - e['gun_' + k]) ** 2 for k in ('shift_x', 'shift_y', 'tilt_x', 'tilt_y'))
        return math.exp(-d2 / GUN_ALIGN_WIDTH ** 2 - (self._aperture_offset() / APERTURE_TOL_M) ** 2)

    def _gun_factor(self):
        return 0.0 if self._beam_blanked() else self._emission_factor() * self._align_factor()

    def _wear_factor(self):
        x = self.gun['heating'] / GUN_SAT_POS
        return 1.0 if x <= 1.0 else 1.0 + OVERHEAT_WEAR * (x - 1.0)

    def _extra_blur(self, alpha):
        """Off-axis aperture blur and the focus swing of a running wobbler (0 at the defaults)."""
        b = APERTURE_BLUR * self._aperture_offset()
        w = 2.0 * alpha * WOBBLE_DEFOCUS_M if self.wobbler else 0.0
        return math.hypot(b, w)

    def _scan_angle(self):
        return -self.stage['rot'] if self.srt == 0.0 else -self.stage['rot'] + self.srt

    def _charging_factor(self):
        scanning_cf = self.scan_mode == 'SLOW' or (self.acq is not None and self.acq['kind'] == 'image')
        return CF_CHARGING if (self.charge_free and scanning_cf) else 1.0

    def _slow_leak_factor(self):
        return SLOW_LEAK_FACTOR if (self.sem_ui['settings']['vacuum']['slow_leak'] and self.p_ch > SLOW_LEAK_UNTIL_PA) else 1.0

    def _stage_error(self):
        return self.align_err['stage_x_m'] - self.stage_cal[0], self.align_err['stage_y_m'] - self.stage_cal[1]

    def _stage_ind(self, i):
        e = self._stage_error()[i]
        v = self.stage['xy'[i]]
        return v + e if e else v

    def _grey(self, g):
        """Grey level after the manual adjustments and the signal settings (identity at the defaults)."""
        a, sg = self.image_adjust, self.sem_ui['settings']['signal']
        bed = self.detector not in ('SED', 'REF')
        if a['brightness'] == 0.5 and a['contrast'] == 0.5 and (a['suppress'] == 0.5 or not bed) and not sg['sed_filter'] \
                and sg['bed_gain'] == 'standard' and (sg['shadow_level'] == 0.5 or self.detector != 'BED_SHADOW'):
            return g
        if bed and sg['bed_gain'] == 'analysis':                              # BED Advanced Gain 'Analysis': gain for strong probe currents
            g = 20.0 + (g - 20.0) * 0.25
        if self.detector == 'BED_SHADOW':
            g = 127.5 + (g - 127.5) * 2.0 ** ((sg['shadow_level'] - 0.5) * 2.0)
        if not bed and sg['sed_filter'] and g > 200.0:                        # SED Advanced Filter: edge-effect highlights compressed
            g = 200.0 + (g - 200.0) * 0.3
        g = 127.5 + (g - 127.5) * 2.0 ** ((a['contrast'] - 0.5) * 4.0) + 255.0 * (a['brightness'] - 0.5)
        if bed:
            g += 255.0 * (a['suppress'] - 0.5)
        return g

    def _pc_range(self):
        return PC_HCM_A if self.sem_ui['settings']['eos']['high_current'] else PC_STD_A

    def _pc_to_a(self, pc):
        lo, hi = self._pc_range()
        return min(max(lo * (hi / lo) ** ((float(pc) - 1.0) / 98.0), PROBE_CURRENT_RANGE_A[0]), PROBE_CURRENT_RANGE_A[1])

    def _a_to_pc(self, a):
        lo, hi = self._pc_range()
        return 1.0 + 98.0 * math.log(a / lo) / math.log(hi / lo)

    def _date(self, t=None):
        import datetime
        return datetime.datetime.fromisoformat(CLOCK_EPOCH) + datetime.timedelta(seconds=int(self.time_s if t is None else t))

    def _need_software(self):
        self._need_ready(allow_error=True)

    # ---- live image (what the main screen shows): the scan of the present field with the selected speed combination
    def _live_scan(self):
        """The live scan of the present mode (Settings - Scan, manual 3-32/33): pixel format, frame time and integrated frames (the Frame
        Integration Coefficient averages RDC / Fast frames; Slow shows single frames).  Freeze keeps the Fast entry (nothing is scanned)."""
        st = self.sem_ui['settings']['scan']
        mode = self.scan_mode if self.scan_mode in SCAN_SPEEDS else 'FAST'
        px, t = SCAN_SPEEDS[mode][st[mode.lower()] - 1]
        return dict(mode=mode, pixels=px, frame_time_s=t, frames=1 if mode == 'SLOW' else int(st['frames']))

    def _live_frame(self):
        """The live frame of the present state: the noise-free signal plus the shot / amplifier noise of one thumbnail pixel of the selected
        scan (full-resolution pixel noise at the dwell frame time / pixels, over sqrt(pixels per thumbnail pixel x integrated frames)); the
        noise pattern is drawn per instrument cycle from the plant noise seed (reproducible renders, the photo random stream untouched)."""
        if not (self.power and self.state == 'ready' and self._vac_ready() and self._beam_ready()):
            return None
        if self.scan_mode == 'FREEZE' and self.acq is None:
            return self.frozen
        key = (self.revision, self.time_ns)
        if self._live_cache is not None and self._live_cache[0] == key:
            return self._live_cache[1]
        if self._beam_blanked() or self._probe_current() <= 0.0:
            rows = [[0] * THUMB_W for _ in range(THUMB_H)]
        else:
            sc = self._live_scan()
            nx, ny = PIXEL_FORMATS[sc['pixels']]
            fr = self._frame_signals(dwell=sc['frame_time_s'] / (nx * ny), nx=nx, ny=ny, noise=False)
            acb = self.acb if (self.acb and self.acb['detector'] == self.detector) else dict(gain=255.0 / (0.3 * 1e-10 / E_CHARGE), offset=0.0)
            sigma = {r['key']: r['sigma'] for r in fr['regions']}
            k = 1.0 / math.sqrt(max(fr['n_avg'], 1.0) * sc['frames'])
            z = np.random.default_rng([int(self.plant['noise_seed']) if self.plant else 0, self.time_ns // CYCLE_NS, 151]).standard_normal((THUMB_H, THUMB_W))
            sig = fr['pixel_signal']
            rows = [[int(min(255, max(0, round(self._grey(acb['offset'] + acb['gain'] * (float(sig[j, i]) + sigma.get(fr['keys'][j, i], 0.0) * k * z[j, i]))))))
                     for i in range(THUMB_W)] for j in range(THUMB_H)]
        self._live_cache = (key, rows)
        return rows

    def _screen_live(self):
        """What the main screen shows of the live scan.  Fast / RDC frames (0.075-1 s) refresh within one instrument cycle.  A Slow frame
        takes 10-40 s: after a condition change the image is repainted from the top, so the rows below the scan line still show the screen
        of before the change until one frame time has passed (time-driven changes such as stage travel appear at once)."""
        live = self._live_frame()
        prev = self._slow_prev
        if live is None or prev is None or prev['rows'] is None or self.scan_mode != 'SLOW' or self.acq is not None:
            return live
        frac = (self.time_s - prev['t0']) / self._live_scan()['frame_time_s']
        if frac >= 1.0:
            return live
        n = max(0, int(frac * THUMB_H))
        return [list(r) for r in live[:n]] + [list(r) for r in prev['rows'][n:]]

    def _slow_hold(self):
        """Before a command in the Slow scan: keep what the screen shows now; the new state is painted over it from the top."""
        shown = self._screen_live()
        self._slow_prev = dict(rows=None if shown is None else [list(r) for r in shown], t0=self.time_s)

    def _y_div(self):
        """Screen-height divisor of the scan on the specimen: a tilted surface is foreshortened by cos(tilt) (the frame covers 1/cos(tilt)
        more specimen along Y); Tilt Mag correction (manual 4-14) shrinks the Y scan by cos(correction angle).  1 / cos(tilt) at 0."""
        k_tm = max(math.cos(math.radians(self.sem_ui['settings']['tilt_correction']['tilt_mag_deg'])), 0.05)
        return max(math.cos(self.stage['tilt']), 0.05) / k_tm

    def _focus_gradient(self):
        """Defocus per metre of projected scan deflection (screen x, screen y) on a tilted specimen: the surface rises by tan(tilt) along the
        tilt direction (screen y at scan rotation 0, turned with the scan rotation); Dynamic Focus (manual 4-13) ramps the focus by
        tan(correction angle) along the scan y.  (0, 0) on a flat stage without correction."""
        t = max(min(self.stage['tilt'], math.radians(89.0)), -math.radians(89.0))
        b = math.radians(min(self.sem_ui['settings']['tilt_correction']['dynamic_focus_deg'], 89.0))
        tt = math.tan(t)
        return tt * math.sin(self.srt), tt * math.cos(self.srt) - math.tan(b)

    def _shown_image(self):
        """What a Save / Copy / Snap / Ruler acts on: the frozen frame in Freeze, else the latest completed photo, else the live frame."""
        if self.scan_mode == 'FREEZE' and self.frozen is not None:
            return dict(source='freeze', thumbnail=self.frozen, field_width_m=PRINT_WIDTH_M / self.mag, image_id=None)
        last = max((r for r in self.images.values() if r['status'] == 'completed'), key=lambda r: r['finished_s'], default=None)
        if last is not None:
            return dict(source=last['image_id'], thumbnail=last['thumbnail'], field_width_m=last['field_of_view_m'][0], image_id=last['image_id'])
        live = self._screen_live()
        if live is not None:
            return dict(source='live', thumbnail=live, field_width_m=PRINT_WIDTH_M / self.mag, image_id=None)
        raise ActionRejected('NO_IMAGE')

    def _conditions(self):
        return dict(accv=self.kv, pc=round(self._a_to_pc(self.ip_set), 1), mag=self.mag, wd=self.focus_wd, signal=self.detector,
                    stage=[self.stage_target['x'] + self._stage_error()[0], self.stage_target['y'] + self._stage_error()[1]])

    def _add_file(self, folder, name, kind, thumbnail=None, image_id=None, **extra):
        files = self.sem_ui['files']
        if len(files) >= MAX_FILES:
            raise ActionRejected('FILES_FULL')
        self.file_seq += 1
        entry = dict(file=name, folder=folder, kind=kind, image_id=image_id, user=self.sem_ui['user'], time_s=self.time_s,
                     date=self._date().strftime('%Y-%m-%d %H:%M:%S'), **extra)
        files.append(entry)
        if thumbnail is not None:
            self._thumbs[name] = [list(r) for r in thumbnail]
        return entry

    def _photo_name(self, base):
        st = self.sem_ui['settings']['image_save']
        ext = {'TIFF': 'tif', 'JPEG': 'jpg', 'BMP': 'bmp'}[st['format']]
        name = f"{base}_{st['number']:04d}.{ext}"
        st['number'] = min(st['number'] + 1, 99999)
        return name

    def _photo_data(self):
        pd = self.sem_ui['settings']['photo_data']
        out = dict(accv_kv=self.kv / 1000.0, wd_mm=round(self.focus_wd * 1000.0, 1), pc=round(self._a_to_pc(self.ip_set), 1), mag=self.mag, signal=self.detector,
                   vacuum=self.vac_mode, label=pd['label'])
        if pd['show_date']:
            out['date'] = self._date().strftime('%Y-%m-%d %H:%M')
        return out

    def _auto_save(self, rec):
        """Photo completed: with Auto Save on, the image goes to the image folder (manual 3-31 / 3-50)."""
        st = self.sem_ui['settings']['image_save']
        rec['_stage'] = dict(tilt_rad=self.stage['tilt'], x_m=self.stage['x'], y_m=self.stage['y'])
        rec['_tilt_correction'] = dict(self.sem_ui['settings']['tilt_correction'])
        if st['auto_save'] and len(self.sem_ui['files']) < MAX_FILES:
            self._add_file(st['folder'], self._photo_name(st['file_name']), 'image', image_id=rec['image_id'], format=st['format'],
                           photo_data=self._photo_data() if st['insert_photo_data'] else None, lut=dict(self.sem_ui['settings']['lut']))

    def _acb_interlock(self):
        if (self.sem_ui['settings']['auto']['acb_interlock'] and self.state == 'ready' and self.error is None and self.acq is None and self._vac_ready()
                and self._beam_ready()):
            self._cmd_optics_auto_contrast_brightness()

    def _operation_tick(self, t):
        ga = self.gun_auto
        if ga is not None:
            if not self._beam_ready() or self.vac_mode == 'LV':
                self.gun_auto = None
                self._event('gun', 'GUN_AUTO_ABORTED', '电子束关闭或切换到LV：自动灯丝调整中止')
            elif t >= ga['t_end'] - 1e-9:
                self.gun['heating'] = GUN_SAT_POS
                if ga['mode'] == 'AGC':
                    for k in ('shift_x', 'shift_y', 'tilt_x', 'tilt_y'):
                        self.gun[k] = self.align_err['gun_' + k]
                self.gun_mode, self.gun_auto = 'auto', None
                self._event('gun', ga['mode'] + '_DONE', ('AGC完成：灯丝加热到饱和并自动对中' if ga['mode'] == 'AGC' else 'AFS完成：灯丝加热到饱和'))
        eco = self.sem_ui['settings']['eco']
        if eco['enabled'] and self.ht and self.acq is None and t - self.last_op_s >= eco['minutes'] * 60.0 - 1e-9:
            self.ht = False
            self._event('gun', 'ECO_STANDBY', f'Eco：{eco["minutes"]}min无操作，停止观察（高压关闭）')

    # ---- commands
    def _cmd_beam_blank(self, on):
        self._need_ready()
        self.blanked = bool(on)
        return dict(blanked=self.blanked)

    def _cmd_beam_set_pc(self, pc):
        self._cmd_beam_set_probe_current(self._pc_to_a(pc))
        return dict(pc=round(self._a_to_pc(self.ip_set), 1), current_a=self.ip_set)

    def _cmd_scan_set_mode(self, mode, charge_free):
        self._need_ready()
        self._need_idle()
        if mode == 'FREEZE' and self.scan_mode != 'FREEZE':
            self.frozen = self._screen_live()
        elif mode != 'FREEZE':
            self.frozen = None
        if mode != 'SLOW':
            self._slow_prev = None
        self.scan_mode, self.charge_free = mode, bool(charge_free)
        return dict(mode=self.scan_mode, charge_free=self.charge_free)

    def _cmd_scan_set_rotation(self, rotation_rad):
        self._need_ready()
        self._need_idle()
        self.srt = float(rotation_rad) % (2 * math.pi)
        return dict(rotation_rad=self.srt)

    def _cmd_scan_rotate_to_line(self, u1, v1, u2, v2, orientation):
        self._need_ready()
        self._need_idle()
        dx, dy = (u2 - u1) * 4.0, (v1 - v2) * 3.0                            # 4:3 frame, image y up
        if math.hypot(dx, dy) < 1e-6:
            raise ActionRejected('INVALID_ARGUMENT')
        a = math.atan2(dy, dx) - (math.pi / 2 if orientation == 'vertical' else 0.0)
        return self._cmd_scan_set_rotation((self.srt + a) % (2 * math.pi))

    def _cmd_optics_set_stigmator(self, x_m, y_m):
        self._need_ready()
        self._need_idle()
        self._set_stig(x_m, y_m)
        self.astig_ok = self.astig <= 2e-9
        return dict(x_m=self.stig[0], y_m=self.stig[1])

    def _cmd_optics_set_image_adjust(self, brightness, contrast, suppress):
        self._need_ready()
        self.image_adjust = dict(brightness=float(brightness), contrast=float(contrast), suppress=float(suppress))
        return dict(self.image_adjust)

    def _cmd_optics_set_beam_shift(self, x_m, y_m):
        self._need_ready()
        self._need_idle()
        lim = BEAM_SHIFT_LIMIT_M
        x, y = min(max(float(x_m), -lim), lim), min(max(float(y_m), -lim), lim)
        self.beam_shift = [x, y]
        return dict(x_m=x, y_m=y, at_limit=abs(x) >= lim - 1e-15 or abs(y) >= lim - 1e-15)

    def _cmd_optics_wobbler(self, on):
        self._need_ready()
        self.wobbler = bool(on)
        return dict(on=self.wobbler)

    def _cmd_aperture_set_position(self, x_m, y_m):
        self._need_plant()
        self.aperture = [float(x_m), float(y_m)]
        return dict(x_m=self.aperture[0], y_m=self.aperture[1])

    def _cmd_gun_auto_adjust(self, mode):
        self._need_ready()
        self._need_idle()
        self._need_beam()
        if self.vac_mode == 'LV':
            raise ActionRejected('LV_MODE')
        if self.gun_auto is not None:
            raise ActionRejected('GUN_AUTO_RUNNING')
        self.gun_auto = dict(mode=mode, t_end=self.time_s + GUN_AUTO_S)
        self._event('gun', mode + '_START', f'{mode}开始（约{GUN_AUTO_S:.0f}s；低于5kV时在5kV执行后恢复）')
        return dict(mode=mode, done_s=self.gun_auto['t_end'])

    def _cmd_gun_set(self, heating, bias_coarse, bias_fine, shift_x, shift_y, tilt_x, tilt_y):
        self._need_ready()
        if self.gun_auto is not None:
            raise ActionRejected('GUN_AUTO_RUNNING')
        hot = self.gun['heating'] > GUN_SAT_POS + 1e-9
        self.gun = dict(heating=float(heating), bias_coarse=float(bias_coarse), bias_fine=float(bias_fine), shift_x=float(shift_x), shift_y=float(shift_y),
                        tilt_x=float(tilt_x), tilt_y=float(tilt_y))
        self.gun_mode = 'manual'
        if self.gun['heating'] > GUN_SAT_POS + 1e-9 and not hot:
            self._event('warning', 'FILAMENT_OVERHEAT', '灯丝加热条在饱和点右侧（橙色区）：过流缩短灯丝寿命')
        return dict(self.gun, mode=self.gun_mode)

    def _cmd_gun_bias_memory(self, action):
        self._need_ready()
        key = f'{self.kv:.0f}'
        store = self.sem_ui['gun_bias']
        if action == 'save':
            store[key] = [self.gun['bias_coarse'], self.gun['bias_fine']]
        else:
            if key not in store:
                raise ActionRejected('NO_SAVED_BIAS')
            if self.gun_auto is not None:
                raise ActionRejected('GUN_AUTO_RUNNING')
            self.gun['bias_coarse'], self.gun['bias_fine'] = store[key]
            self.gun_mode = 'manual'
        return dict(voltage_v=self.kv, bias_coarse=self.gun['bias_coarse'], bias_fine=self.gun['bias_fine'])

    def _cmd_alignment_configure_error(self, **err):
        self._need_plant()
        self.align_err = {k: float(v) for k, v in err.items()}
        return dict(self.align_err)

    def _cmd_recipe_execute(self, category, coated, method, eds):
        self._need_ready()
        self._need_idle()
        r = RECIPES[category]
        col = 'coated' if coated else method
        cond = r[col]
        if cond is None or (eds and r['eds_pc'] is None):
            raise ActionRejected('RECIPE_NOT_AVAILABLE')
        lv = col == 'low_vacuum'
        if lv != (self.vac_mode == 'LV'):
            self._cmd_vacuum_toggle_lv()
        if lv:
            self._cmd_vacuum_set_lv_pressure(cond[2])
        self._cmd_beam_set_voltage(cond[0])
        self._cmd_beam_set_pc(r['eds_pc'] if eds else cond[1])
        self._cmd_detector_select('BED_COMPO' if lv else 'SED')
        self._event('gun', 'RECIPE', f'Recipe {category}（{col}{"+EDS" if eds else ""}）：{self.kv / 1000:.1f}kV、P.C.{self._a_to_pc(self.ip_set):.0f}、{self.detector}')
        return dict(voltage_v=self.kv, pc=round(self._a_to_pc(self.ip_set), 1), detector=self.detector, vacuum_mode=self.vac_mode,
                    lv_pressure_pa=self.lv_set if lv else None)

    def _cmd_stage_stop(self):
        self._need_power()
        self.stage_target['x'], self.stage_target['y'] = self.stage['x'], self.stage['y']
        ex, ey = self._stage_error()
        return dict(x_m=self.stage['x'] + ex, y_m=self.stage['y'] + ey)

    def _cmd_stage_last_move(self):
        last = self.sem_ui['last_move']
        if last is None:
            raise ActionRejected('NO_LAST_MOVE')
        return self._cmd_stage_move_xy(last[0], last[1])

    def _cmd_stage_click_center(self, u, v):
        fw = PRINT_WIDTH_M / self.mag
        xs, ys = (u - 0.5) * fw, (0.5 - v) * 0.75 * fw / self._y_div()
        th = self._scan_angle()
        du, dv = math.cos(th) * xs - math.sin(th) * ys, math.sin(th) * xs + math.cos(th) * ys
        r = self.stage['rot']
        ex, ey = self._stage_error()
        x = self.stage_target['x'] + ex - (math.cos(r) * du - math.sin(r) * dv)
        y = self.stage_target['y'] + ey - (math.sin(r) * du + math.cos(r) * dv)
        return self._cmd_stage_move_xy(min(max(x, STAGE_X_RANGE_M[0]), STAGE_X_RANGE_M[1]), min(max(y, STAGE_Y_RANGE_M[0]), STAGE_Y_RANGE_M[1]))

    def _cmd_stage_memory(self, action, name):
        self._need_software()
        mem = self.sem_ui['memory']
        if action == 'record':
            if len(mem) >= MAX_MEMORY:
                raise ActionRejected('MEMORY_FULL')
            ex, ey = self._stage_error()
            entry = dict(name=name or self._date().strftime('%Y%m%d_%H%M%S'), x_m=round(self.stage['x'] + ex, 7), y_m=round(self.stage['y'] + ey, 7))
            mem[:] = [m for m in mem if m['name'] != entry['name']] + [entry]
            return dict(entry)
        entry = next((m for m in mem if m['name'] == name), None)
        if entry is None:
            raise ActionRejected('UNKNOWN_MEMORY')
        if action == 'move':
            self._cmd_stage_move_xy(entry['x_m'], entry['y_m'])
        else:
            mem.remove(entry)
        return dict(entry)

    def _cmd_stage_calibrate(self, axes):
        self._need_ready()
        self._need_idle()
        if not self._at_atmosphere():
            raise ActionRejected('VENT_REQUIRED')
        if self.loaded is not None:
            raise ActionRejected('HOLDER_OCCUPIED')
        other = next((f for f in STAGE_FAULTS if f in self.faults and f != 'stage_motor_failure'), None)
        if other is not None:
            raise ActionRejected(JSM_FAULTS[other])                         # motors inoperable: contact JEOL / check for damage
        if axes == 'all' and 'stage_motor_failure' in self.faults:         # manual 3.14 (0250): vent, then the initialization of all axes
            self.faults.remove('stage_motor_failure')
        for i, ax in enumerate('xy'):
            if axes in ('all', ax):
                self.stage_cal[i] = self.align_err[f'stage_{ax}_m']
                self.stage_target[ax] = 0.0
        self.sem_ui['calibrated_s'] = self.time_s
        self._event('stage', 'STAGE_CALIBRATED', f'样品台校准（{axes}）：坐标偏差已校正，回到交换位')
        return dict(axes=axes, correction_m=list(self.stage_cal))

    def _cmd_specimen_set_info(self, name, holder, height_m):
        self._need_software()
        self.sem_ui['specimen'] = dict(name=name, holder=holder, height_m=round(float(height_m), 3))
        return dict(self.sem_ui['specimen'])

    def _cmd_settings_configure(self, section, values):
        self._need_software()
        validate(SETTINGS_SCHEMAS[section], values)
        st = self.sem_ui['settings']
        if section == 'view' and self.sem_ui['movie'] is not None and values['mode'] != st['view']['mode']:
            raise ActionRejected('MOVIE_RECORDING')
        if section == 'eos' and values['high_current'] != st['eos']['high_current']:
            self._need_idle()
            pc = self._a_to_pc(self.ip_set)
            st['eos'] = dict(values)
            self.ip_set = self._pc_to_a(min(max(pc, 1.0), 99.0))
            self.acb = None
        st[section] = copy.deepcopy(values)
        return dict(section=section, values=copy.deepcopy(values))

    def _cmd_presets_configure(self, window, index, value):
        self._need_software()
        (lo, hi), pre = PRESET_WINDOWS[window][0], self.sem_ui['presets'][window]
        if index >= len(pre) or not lo - 1e-12 <= value <= hi + 1e-12:
            raise ActionRejected('INVALID_ARGUMENT')
        pre[index] = quantize_kv(value) if window == 'accv' else float(value)
        return dict(window=window, presets=list(pre))

    def _cmd_ui_select(self, window, value):
        self._need_software()
        lo, hi = KV_RANGE_V if window == 'accv' else LV_RANGE_PA
        if not lo - 1e-9 <= value <= hi + 1e-9:
            raise ActionRejected('INVALID_ARGUMENT')
        self.sem_ui['selected'][window] = quantize_kv(value) if window == 'accv' else float(value)
        return dict(window=window, value=self.sem_ui['selected'][window])

    def _cmd_vacuum_lv_auto(self, on):
        self._need_power()
        if on and 'lv_pressure_adjust' in self.faults:
            raise ActionRejected('LV_PRESSURE_ADJUST_FAILED')               # Error ID 0351: use the manual adjustment buttons
        self.sem_ui['lv_auto'] = bool(on)
        return dict(on=self.sem_ui['lv_auto'])

    def _cmd_image_save(self, name):
        self._need_software()
        img = self._shown_image()
        st = self.sem_ui['settings']['image_save']
        ext = {'TIFF': 'tif', 'JPEG': 'jpg', 'BMP': 'bmp'}[st['format']]
        fname = name if '.' in name else f'{name}.{ext}'
        if any(f['file'] == fname for f in self.sem_ui['files']):
            raise ActionRejected('FILE_EXISTS')
        self._add_file(st['folder'], fname, 'image', thumbnail=img['thumbnail'] if img['image_id'] is None else None, image_id=img['image_id'],
                       format=st['format'], photo_data=self._photo_data() if st['insert_photo_data'] else None, lut=dict(self.sem_ui['settings']['lut']),
                       field_width_m=img['field_width_m'])
        return dict(file=fname)

    def _file(self, name):
        f = next((f for f in self.sem_ui['files'] if f['file'] == name), None)
        if f is None:
            raise ActionRejected('UNKNOWN_FILE')
        return f

    def _file_thumb(self, f):
        if f['file'] in self._thumbs:
            return self._thumbs[f['file']]
        rec = self.images.get(f.get('image_id'))
        return rec['thumbnail'] if rec else None

    def _cmd_image_open(self, file):
        self._need_software()
        if file:
            self._file(file)
        self.sem_ui['viewer'] = file or None
        return dict(file=self.sem_ui['viewer'])

    def _cmd_image_copy(self):
        self._need_software()
        if self.sem_ui['viewer']:
            src = self.sem_ui['viewer']
        else:
            src = self._shown_image()['source']
        self.sem_ui['clipboard'] = src
        return dict(source=src)

    def _cmd_image_anaglyph(self, left_file, right_file):
        self._need_software()
        fl, fr = self._file(left_file), self._file(right_file)
        rl, rr = self.images.get(fl.get('image_id')), self.images.get(fr.get('image_id'))
        if rl is None or rr is None or '_stage' not in rl or '_stage' not in rr:
            raise ActionRejected('TILT_PAIR_REQUIRED')
        dt = abs(rl['_stage']['tilt_rad'] - rr['_stage']['tilt_rad'])
        if dt < math.radians(1.0) - 1e-9:
            raise ActionRejected('TILT_PAIR_REQUIRED')
        thumb = [[[int(a), int(b), int(b)] for a, b in zip(ra, rb)] for ra, rb in zip(rl['thumbnail'], rr['thumbnail'])]
        n = sum(1 for f in self.sem_ui['files'] if f['kind'] == 'anaglyph') + 1
        name = f'Anaglyph_{n:04d}.jpg'
        self._add_file(self.sem_ui['settings']['image_save']['folder'], name, 'anaglyph', thumbnail=thumb, left=left_file, right=right_file)
        return dict(file=name, tilt_difference_rad=dt)

    def _cmd_movie_record(self, on):
        self._need_software()
        mv = self.sem_ui['movie']
        if on:
            if self.sem_ui['settings']['view']['mode'] == 'full_screen':
                raise ActionRejected('FULL_SCREEN')
            if self._live_frame() is None:
                raise ActionRejected('BEAM_NOT_READY')
            if mv is None:
                self.sem_ui['movie'] = dict(t0=self.time_s, fps=self.sem_ui['settings']['movie']['fps'], pixels='640x480')
                self._event('acquisition', 'MOVIE_START', '开始录像（640×480）')
            return dict(recording=True, file=None)
        if mv is None:
            raise ActionRejected('NOT_RECORDING')
        name = self._date(mv['t0']).strftime('%Y%m%d%H%M%S') + '_0000.avi'
        dur = self.time_s - mv['t0']
        self._add_file(self.sem_ui['settings']['movie']['folder'], name, 'movie', duration_s=dur, frames=int(round(dur * mv['fps'])), pixels=mv['pixels'])
        self.sem_ui['movie'] = None
        self._event('acquisition', 'MOVIE_SAVED', f'录像已保存：{name}（{dur:.0f}s）')
        return dict(recording=False, file=name)

    def _cmd_snapshot_take(self, file):
        self._need_software()
        if file:
            f = self._file(file)
            thumb, rec = self._file_thumb(f), self.images.get(f.get('image_id'))
            if thumb is None or isinstance(thumb[0][0], list):
                raise ActionRejected('NO_IMAGE')
            if f.get('conditions'):
                cond = copy.deepcopy(f['conditions'])
            elif rec is not None:
                ex, ey = self._stage_error()
                st = rec.get('_stage', dict(x_m=self.stage['x'], y_m=self.stage['y']))
                cond = dict(accv=rec['accelerating_voltage_v'], pc=round(self._a_to_pc(rec['probe_current_a']), 1), mag=rec['magnification'],
                            wd=rec['working_distance_m'] or self.focus_wd, signal=rec['detector'], stage=[st['x_m'] + ex, st['y_m'] + ey])
            else:
                cond = self._conditions()
            img = dict(source=file, thumbnail=thumb, field_width_m=f.get('field_width_m') or (rec['field_of_view_m'][0] if rec else PRINT_WIDTH_M / self.mag))
        else:
            img, cond = self._shown_image(), self._conditions()
        self.sem_ui['snapshot'] = dict(source=img['source'], time_s=self.time_s, conditions=cond, field_width_m=img['field_width_m'])
        self._thumbs['<snapshot>'] = [list(r) for r in img['thumbnail']]
        return dict(source=img['source'])

    def _cmd_snapshot_reproduce(self, **items):
        self._need_ready()
        self._need_idle()
        snap = self.sem_ui['snapshot']
        if snap is None:
            raise ActionRejected('NO_SNAPSHOT')
        if not any(items.values()):
            raise ActionRejected('INVALID_ARGUMENT')
        c, done = snap['conditions'], []
        for it in ('accv', 'pc', 'mag', 'wd', 'signal', 'stage'):
            if not items[it]:
                continue
            if it == 'accv':
                self._cmd_beam_set_voltage(c['accv'])
            elif it == 'pc':
                self._cmd_beam_set_pc(min(max(c['pc'], 1.0), 99.0))
            elif it == 'mag':
                self._cmd_scan_set_magnification(c['mag'])
            elif it == 'wd':
                self._cmd_optics_set_focus(c['wd'])
            elif it == 'signal':
                self._cmd_detector_select(c['signal'])
            else:
                self._cmd_stage_move_xy(c['stage'][0], c['stage'][1])
            done.append(it)
        return dict(applied=done)

    def _cmd_snapshot_save(self):
        self._need_software()
        snap = self.sem_ui['snapshot']
        if snap is None:
            raise ActionRejected('NO_SNAPSHOT')
        n = sum(1 for f in self.sem_ui['files'] if f['kind'] == 'snapshot') + 1
        name = f'Snap_{n:04d}.jpg'
        self._add_file(self.sem_ui['settings']['image_save']['snapshot_folder'], name, 'snapshot', thumbnail=self._thumbs['<snapshot>'],
                       conditions=copy.deepcopy(snap['conditions']), field_width_m=snap['field_width_m'])
        return dict(file=name)

    def _cmd_measure_ruler(self, u1, v1, u2, v2):
        self._need_software()
        view = self.sem_ui['viewer']
        if view:
            f = self._file(view)
            rec = self.images.get(f.get('image_id'))
            fw = f.get('field_width_m') or (rec['field_of_view_m'][0] if rec else None)
            if fw is None:
                raise ActionRejected('NO_IMAGE')
        else:
            if self._live_frame() is None and not (self.scan_mode == 'FREEZE' and self.frozen is not None):
                raise ActionRejected('NO_IMAGE')
            fw = PRINT_WIDTH_M / self.mag
        dx, dy = (u2 - u1) * fw, (v1 - v2) * 0.75 * fw
        out = dict(distance_m=math.hypot(dx, dy), angle_deg=math.degrees(math.atan2(dy, dx)))
        ms = self.sem_ui['measurements']
        ms.append(dict(out, time_s=self.time_s))
        del ms[:-MAX_MEASUREMENTS]
        return out

    def _cmd_eds_transfer_image(self):
        self._need_software()
        img = self._shown_image()
        self.sem_ui['eds_image'] = dict(source=img['source'], time_s=self.time_s, field_width_m=img['field_width_m'])
        self._thumbs['<eds>'] = [list(r) for r in img['thumbnail']]
        return dict(source=img['source'])

    def _cmd_user_manage(self, action, name):
        self._need_software()
        users = self.sem_ui['users']
        if action == 'add':
            if name in users:
                raise ActionRejected('USER_EXISTS')
            if len(users) >= 50:
                raise ActionRejected('INVALID_ARGUMENT')
            users.append(name)
        elif name not in users:
            raise ActionRejected('UNKNOWN_USER')
        self.sem_ui['user'] = name
        base = f'E:/DATA/IT200/{name}'
        st = self.sem_ui['settings']
        st['image_save'].update(folder=base, snapshot_folder=base + '/Snapshot')
        st['movie']['folder'] = base + '/Movie'
        return dict(user=name, users=list(users))

    # ---- values the runtime reports
    def _operation_values(self):
        ui = copy.deepcopy(self.sem_ui)
        if ui['movie'] is not None:
            ui['movie']['elapsed_s'] = self.time_s - ui['movie']['t0']
        return {'beam.blanked': self.blanked, 'beam.probe_current_pc': round(self._a_to_pc(self.ip_set), 1), 'scan.mode': self.scan_mode,
                'scan.charge_free': self.charge_free, 'scan.rotation_rad': self.srt, 'optics.stigmator_m': list(self.stig),
                'optics.image_adjust': dict(self.image_adjust), 'optics.beam_shift_m': list(self.beam_shift), 'optics.wobbler_on': self.wobbler,
                'aperture.position_m': list(self.aperture), 'gun.settings': dict(self.gun, mode=self.gun_mode),
                'gun.auto_adjust': self.gun_auto['mode'] if self.gun_auto else None, 'operation.state': ui, 'truth.alignment_error': dict(self.align_err)}

    # ---- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
    def panel_read(self):
        """What SEM Operation shows: the agent slots (no process truth) and the pictures it draws (live image, holder graphic, histogram)."""
        out = self.read('agent')
        live = self._screen_live() if self.power else None
        view = self.sem_ui['viewer']
        vf = next((f for f in self.sem_ui['files'] if f['file'] == view), None) if view else None
        lut = self.sem_ui['settings']['lut']
        main = self._file_thumb(vf) if vf else live
        out.update({
            'host.powered': self.power, 'host.live': live, 'host.main_image': self._lut_rows(main, lut), 'host.histogram': self._histogram(main),
            'host.line_profile': None if main is None else [[i, float(v)] for i, v in enumerate(main[len(main) // 2])],
            'host.holder_graphic': self._holder_graphic(), 'host.snapshot': self._thumbs.get('<snapshot>') if self.sem_ui['snapshot'] else None,
            'host.wobble_shift_m': WOBBLE_GAIN * self._aperture_offset() if (self.wobbler and self.power) else None,
            'host.date': self._date().strftime('%Y-%m-%d %H:%M'), 'host.field_width_um': PRINT_WIDTH_M / self.mag * 1e6,
            'host.progress': {'vented': 'WAIT', 'venting': 'VENT', 'roughing': 'PRE EVAC', 'hv_pumping': 'EVAC', 'lv_pumping': 'EVAC', 'hv_ready': 'READY',
                              'lv_ready': 'READY', 'idle': 'WAIT'}[self.vac_state],
            'host.example_file': '{}_{:04d}.{}'.format(self.sem_ui['settings']['image_save']['file_name'], self.sem_ui['settings']['image_save']['number'],
                                                        {'TIFF': 'tif', 'JPEG': 'jpg', 'BMP': 'bmp'}[self.sem_ui['settings']['image_save']['format']]),
            'host.version': f'SEM Operation (simulated) / asset {ASSET_VERSION}',
            'host.micron_bar': self._micron_bar(), 'host.last_photo': self._lut_rows(self._last_photo_rows(), lut), 'host.mix': self._mixed_frame() if (live is not None and not vf) else None,
            'host.eds_image': self._thumbs.get('<eds>') if self.sem_ui['eds_image'] else None, 'host.last_spectrum': self._last_spectrum(),
        })
        return out

    def _micron_bar(self):
        """Micron bar of the main screen (manual 5-8): the largest 1-2-5 length up to a fifth of the field width."""
        t = PRINT_WIDTH_M / self.mag / 5.0
        e = 10.0 ** math.floor(math.log10(t))
        v = max(k * e for k in (1.0, 2.0, 5.0) if k * e <= t * (1 + 1e-9))
        for unit, f in (('mm', 1e-3), ('um', 1e-6), ('nm', 1e-9)):
            if v >= f * (1 - 1e-9):
                return f'{v / f:g} {unit}'
        return f'{v / 1e-9:g} nm'

    def _last_photo_rows(self):
        last = max((r for r in self.images.values() if r['status'] == 'completed'), key=lambda r: r['finished_s'], default=None)
        return None if last is None else last['thumbnail']

    def _mixed_frame(self):
        """View mode Signal Mixing (manual 5-5): SED and BED-COMPO live frames, each stretched to 0-255, mixed by the ratio (HV only)."""
        if self.sem_ui['settings']['view']['mode'] != 'signal_mixing' or self.vac_mode == 'LV' or self._beam_blanked():
            return None
        det, frames = self.detector, []
        try:
            for d in ('SED', 'BED_COMPO'):
                self.detector = d
                sig = self._frame_signals(dwell=1e-5, nx=640, ny=480, noise=False)['pixel_signal']
                lo, hi = float(sig.min()), float(sig.max())
                frames.append((sig - lo) / (hi - lo) * 255.0 if hi > lo else sig * 0.0 + 128.0)
        finally:
            self.detector = det
        r = self.sem_ui['settings']['view']['mix_ratio']
        mix = r * frames[0] + (1.0 - r) * frames[1]
        return [[int(round(float(v))) for v in row] for row in mix]

    def _last_spectrum(self):
        done = [r for r in self.spectra.values() if r['status'] == 'completed']
        if not done:
            return None
        rec = max(done, key=lambda r: r['finished_s'])
        q = self.quants.get(rec['spectrum_id'])
        return dict(spectrum_id=rec['spectrum_id'], mode=rec['mode'], live_time_s=rec['live_time_s'], dead_time_fraction=rec['dead_time_fraction'],
                    quant=' '.join(f"{x['element']} {100 * x['mass_fraction']:.1f}" for x in q['results'][:6]) + ' wt%' if q else '')

    @staticmethod
    def _lut_rows(rows, lut):
        if rows is None:
            return None
        if lut['gamma'] == 1.0 and not lut['pseudo_color']:
            return rows

        def px(g):
            if isinstance(g, list):
                return g
            x = (g / 255.0) ** (1.0 / lut['gamma']) * 255.0
            if not lut['pseudo_color']:
                return int(round(x))
            return [int(round(min(255.0, 2 * x))), int(round(255 - abs(2 * x - 255))), int(round(max(0.0, 255 - 2 * x)))]
        return [[px(g) for g in r] for r in rows]

    @staticmethod
    def _histogram(rows):
        if not rows or isinstance(rows[0][0], list):
            return None
        h = [0] * 16
        for r in rows:
            for g in r:
                h[min(15, int(g) // 16)] += 1
        return [[i * 16 + 8, n] for i, n in enumerate(h)]

    def _holder_graphic(self):
        """Specimen holder graphic of the navigator (40 x 40, stage coordinates): holder, specimen and the field of view (bright)."""
        n, R = 40, HOLDER_RADIUS_M * 1.25
        r_holder = HOLDER_OUTLINE_R_M[self.sem_ui['specimen']['holder']]       # the graphic of the holder selected in the software
        spec = self._spec()
        ex, ey = self._stage_error()
        bx, by = -(self.stage['x'] + ex), -(self.stage['y'] + ey)                # beam point on the holder (displayed coordinates)
        rows = []
        for j in range(n):
            y = (0.5 - (j + 0.5) / n) * 2 * R
            row = []
            for i in range(n):
                x = ((i + 0.5) / n - 0.5) * 2 * R
                r = math.hypot(x, y)
                g = 40 if r > r_holder else 110
                if spec is not None and r <= spec['diameter_m'] / 2:
                    g = 170
                if abs(x - bx) <= R / n and abs(y - by) <= R / n:
                    g = 255
                row.append(g)
            rows.append(row)
        return rows

    def panel_invoke(self, capability_id, arguments):
        """A panel action calls the same command a direct call would (commands check before they change anything).  An agent's gesture may not
        invoke a guarded command during another agent's acquisition (ACCESS_DENIED, shown as the device message); navigation is not guarded."""
        try:
            if self._foreign_acquisition(capability_id):
                raise ActionRejected('ACCESS_DENIED')
            return self.invoke(capability_id, **arguments)
        except ActionRejected as exc:
            raise _panel_ui.PanelRejected(str(exc)) from None

    def panel_time(self):
        return self.time_s

    def _foreign_acquisition(self, capability_id):
        """The same rule as the session guard for direct calls: an agent caller may not change another agent's running acquisition."""
        c = self._caller
        if c is None or self._principals.get(c) != 'agent' or capability_id not in _CONTROL_GUARDED or self.acq is None:
            return False
        if capability_id in ('power.set', 'power.key_toggle') and not self.power:
            return False
        owner = self._owners.get(self.acq['record_id'])
        return owner is not None and owner != c and self._principals.get(owner) == 'agent'

    def _panel_entry(self, capability_id, arguments):
        """The panel.* commands (the session sends them here, not through the command invoke): validate, run, count the revision of a gesture."""
        validate(PANEL_CAPS[capability_id]['input_schema'], arguments)
        out = getattr(self, '_cmd_' + capability_id.replace('.', '_'))(**arguments)
        if PANEL_CAPS[capability_id]['kind'] == 'device_command':
            self.revision += 1
        return out

    def _panel(self, gesture, *args):
        try:
            return dict(accepted=bool(gesture(*args)))
        except _panel_ui.PanelError:
            raise ActionRejected('INVALID_ARGUMENT') from None

    def _cmd_panel_press(self, input_id, duration_s=0.1):
        return self._panel(self.panel.press, input_id, float(duration_s))

    def _cmd_panel_turn(self, input_id, detents):
        return self._panel(self.panel.turn, input_id, int(detents))

    def _cmd_panel_switch(self, input_id, position):
        return self._panel(self.panel.switch, input_id, position)

    def _cmd_panel_touch(self, display_id, x, y, duration_s=0.1, to_x=None, to_y=None):
        if (to_x is None) != (to_y is None):
            raise ActionRejected('INVALID_ARGUMENT')
        return self._panel(self.panel.touch, display_id, int(x), int(y), float(duration_s), None if to_x is None else (int(to_x), int(to_y)))

    def _cmd_panel_capture(self, display_id):
        """Close-up camera of a display: a PNG file named by its content (large data is returned as a file reference)."""
        try:
            data, w, h = self.panel.png_bytes(display_id)
        except _panel_ui.PanelError:
            raise ActionRejected('INVALID_ARGUMENT') from None
        sha = hashlib.sha256(data).hexdigest()
        folder = Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir()) / 'chemistry_sim_panel' / self.instance_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f'{display_id}_{sha[:16]}.png'
        if not path.exists():
            path.write_bytes(data)
        return dict(display_id=display_id, time_s=self.time_s, image=dict(path=str(path), format='png', width=w, height=h, sha256=sha))

    def _cmd_panel_frame(self):
        return dict(frame=self.panel.frame())


class JeolJsmit200Runtime(_SemOperation):
    """JSM-IT200 LA with the JED-2300 EDS: vacuum, gun, optics, signals, EDS, stage, door and specimen handling (SI units)."""

    def __init__(self, instance_id='jsmit200_01'):
        self.instance_id = instance_id
        self.time_ns = 0
        self._clock_remainder_ns = 0.0
        self.revision = 0
        self.plant = None
        self.rng = random.Random(0)
        self.power = False
        self.state = 'off'
        self.state_t0 = 0.0
        self.shutdown_done = True
        self.error = None
        # vacuum (delivered vented, door closed)
        self.vac_mode = 'HV'
        self.vac_state = 'vented'
        self.p_ch = P_ATM
        self.tmp_on = False
        self.tmp_speed = 0.0
        self.pump_t0 = None
        self.evac_t0 = None
        self.evac_timeout_raised = False
        self.lv_set = 50.0
        self.lv_valve = 0.0
        self.lv_int = 0.0
        self.lv_ok_s = 0.0
        self.outgas_q0 = OUTGAS_AIR_PA_M3_S
        self.readings = None
        # door and stage (reference pose)
        self.door = 0.0
        self.door_target = 0.0
        self.stage = dict(x=0.0, y=0.0, z=0.0, tilt=0.0, rot=0.0)
        self.stage_target = dict(self.stage)
        # gun and optics
        self.kv = 15000.0
        self.ip_set = 1e-10
        self.ht = False
        self.heat = 0.0
        self.fil_serial = 1
        self.fil_life_s = FILAMENT_LIFE_S
        self.fil_used_s = 0.0
        self.fil_burned = False
        self.mag = 100.0
        self.focus_wd = WD_RANGE_M[1]
        self.astig = 30e-9
        self.astig_ok = False
        self.acb = None
        self.detector = 'SED'
        self.v_surface = {}           # negative surface potential (V) of each charging phase of the loaded insulator (phase index -> V)
        # specimens, records, operations
        self.specimens = {}
        self.loaded = None
        self.images, self.spectra, self.quants = {}, {}, {}
        self.img_seq = self.eds_seq = self.op_seq = 0
        self.acq = None
        self.operations = {}
        self.events, self.events_dropped = [], 0
        self.used_substances = set()
        self.led = {k: _Sum() for k in LEDGER_FLOWS}
        self._n0 = None
        self._principals, self._requests, self._owners = {}, {}, {}
        self._caller = None                     # the principal whose session call is running (set by the session; panel gestures act as it)
        self.faults = []                        # evaluator-injected hardware faults (manual 3.14, 1.2.0)
        self._last = {}
        self._init_operation()
        self.panel_capture_dir = None
        self.panel = _panel_ui.PanelUI(PANEL_SPEC, self) if PANEL_SPEC else None

    # ---------------------------------------------------------------- basics
    @property
    def time_s(self):
        return self.time_ns / 1e9

    def register_principal(self, name, visibility):
        if visibility not in ('agent', 'evaluator') or name in self._principals:
            raise ActionRejected('INVALID_ARGUMENT')
        self._principals[name] = visibility

    def session(self, name):
        if name not in self._principals:
            raise ActionRejected('ACCESS_DENIED')
        return JeolJsmit200Session(self, name)

    def _event(self, kind, code, message, rid=None):
        if len(self.events) >= MAX_EVENTS:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.time_s, kind=kind, code=code, message_zh=message, _rid=rid))

    def _room_k(self):
        return self.plant['room_k'] if self.plant else 293.15

    def _gas_mol(self):
        return self.p_ch * V_SYSTEM_M3 / (GAS_CONSTANT * self._room_k())

    # ---------------------------------------------------------------- derived quantities
    def _door_state(self):
        if self.door <= 1e-9:
            return 'closed'
        if self.door >= DOOR_TRAVEL_M - 1e-9:
            return 'open'
        return 'moving'

    def _vac_ready(self):
        return self.vac_state in ('hv_ready', 'lv_ready')

    def _p_gun(self):
        if self.vac_mode == 'LV' and self.vac_state in ('lv_pumping', 'lv_ready'):
            return GUN_BASE_PA + self.p_ch * PLA_CONDUCTANCE_M3_S / (TMP_SPEED_M3_S * max(self.tmp_speed, 0.05))
        return self.p_ch

    def _beam_ready(self):
        return self.ht and self.heat >= 1.0 - 1e-9 and not self.fil_burned

    def _probe_current(self):
        """Current reaching the specimen: the set point, limited by the emission while the filament heats up."""
        if not self.ht or self.fil_burned:
            return 0.0
        return self.ip_set * min(1.0, self.heat) ** 2 * self._gun_factor()

    def _spec(self):
        return self.specimens.get(self.loaded) if self.loaded else None

    def _spec_height(self):
        s = self._spec()
        return s['height_m'] if s else 0.0

    def _spec_radius(self):
        s = self._spec()
        return s['diameter_m'] / 2 if s else HOLDER_RADIUS_M

    def _top_z_rel(self, z, tilt, y=0.0, hr=None):
        """Highest point of the specimen (or holder) above the WD-48 plane (m) for lift z, tilt and Y carriage offset: the Y carriage
        moves along the tilted cradle (vertical component y sin t), the top rim of a disc of height h and radius r reaches
        h cos t + r |sin t| above its centre.  hr = (h, r) overrides the true specimen (the software limit uses the entered values)."""
        h, r = hr if hr is not None else (self._spec_height(), max(self._spec_radius(), HOLDER_RADIUS_M))
        return z + y * math.sin(tilt) + h * math.cos(tilt) + r * abs(math.sin(tilt))

    def _software_hr(self):
        """The specimen the stage software knows (manual 2-7, 3-8/3-10): the selected holder's outline and the entered specimen height
        (the specimen is assumed not to stick out sideways)."""
        sp = self.sem_ui['specimen']
        return sp['height_m'], HOLDER_OUTLINE_R_M[sp['holder']]

    def _path_collision(self, z_to, tilt_to, y_to=None, strict=False, hr=None):
        """The Z and T hand motions and the Y motor run independently towards their targets: the highest rim point over the whole swept
        box between the present position and the targets (max over Z, the tilt interval incl. the rim maximum at atan(r/h), and both Y
        ends) must stay 4 mm below the pole piece.  A motion that does not lift the rim above its present height is always allowed (the
        remedy from a too-high pose, e.g. after a taller specimen was mounted), and with the door fully drawn out there is no pole piece
        above the stage; strict (drawing the door in) requires the whole box below the limit."""
        if not strict and self._stage_outside():
            return False
        y_to = self.stage['y'] if y_to is None else y_to
        lo, hi = sorted((self.stage['tilt'], tilt_to))
        h, r = hr if hr is not None else (self._spec_height(), max(self._spec_radius(), HOLDER_RADIUS_M))
        phi = math.atan2(r, max(h, 1e-12))
        tilts = [lo + (hi - lo) * i / 90.0 for i in range(91)] + [t for t in (phi, -phi, 0.0) if lo < t < hi]
        z_max = max(self.stage['z'], z_to)
        top = max(self._top_z_rel(z_max, t, y, (h, r)) for t in tilts for y in (self.stage['y'], y_to))
        limit = WD_RANGE_M[1] - POLE_CLEARANCE_M + 1e-12
        if strict:
            return top > limit
        now = self._top_z_rel(self.stage['z'], self.stage['tilt'], self.stage['y'], (h, r))
        return top > limit and top > now + 1e-12

    def _stage_outside(self):
        """The stage is out of the chamber only while the door is fully drawn out and not commanded back in."""
        return self.door >= DOOR_TRAVEL_M - 1e-9 and self.door_target >= DOOR_TRAVEL_M - 1e-9

    def _hit(self):
        """Where the vertical beam meets the specimen: (u, v, on, t_m).  The tilt axis (X) passes through the holder top on the column
        axis; the tilted top face of a specimen of height h is met h/cos t above that point, h tan t along the tilted surface from the
        face centre (shifted by the X / Y carriages and turned by R).  When that point lies outside the disc (or the stage is tilted
        so far that the top face is edge-on) the beam passes the specimen and lands on the holder plane (t = 0); the specimen side wall
        is not modelled."""
        x, y, r, th = self.stage['x'], self.stage['y'], self.stage['rot'], self.stage['tilt']
        spec = self._spec()
        h = spec['height_m'] if spec else 0.0
        c_t = math.cos(th)
        d, t = (h * math.tan(th), h / c_t) if c_t > 1e-6 else (math.inf, math.inf)
        u, v = -x, (d - y) if math.isfinite(d) else math.inf
        on = spec is not None and math.isfinite(v)
        if on:
            c, s = math.cos(-r), math.sin(-r)
            u, v = c * u - s * v, s * u + c * v
            on = math.hypot(u, v) <= spec['diameter_m'] / 2
        if not on:
            u, v = -x, -y
            c, s = math.cos(-r), math.sin(-r)
            u, v = c * u - s * v, s * u + c * v
            t = 0.0
        return u, v, on, t

    def _wd_true(self):
        """Distance from the pole-piece face to the point the beam lands on (top face of the specimen or the holder plane)."""
        return WD_RANGE_M[1] - self.stage['z'] - self._hit()[3]

    def _beam_point(self):
        """Beam position in specimen coordinates (m) and whether it lands on the specimen top face."""
        u, v, on, _ = self._hit()
        bx, by = self.beam_shift
        if bx or by:                                                         # beam shift: scan coordinates turned to the specimen
            th = self._scan_angle()
            cos_t = max(math.cos(self.stage['tilt']), 0.05)
            u, v = u + math.cos(th) * bx - math.sin(th) * by / cos_t, v + math.sin(th) * bx + math.cos(th) * by / cos_t
            spec = self._spec()
            on = spec is not None and math.hypot(u, v) <= spec['diameter_m'] / 2
        return u, v, on

    def _phase_sub(self, spec, u, v):
        if spec is None:
            return substance(HOLDER_SUBSTANCE), 0.0, 0.0, None
        k = phase_at(spec, u, v)
        th, az = local_tilt(spec, u, v)
        return substance(spec['phases'][k]['substance_id']), th, az, k

    def _charging_phase(self, sub, spec):
        if spec is None or spec['coating'] != 'none' or self.vac_mode == 'LV':
            return False
        return sub['electrical_class'] not in ('conductor', 'semiconductor')

    def _landing_ev(self, sub, spec, k=None):
        """Landing energy (eV) of the beam on phase `k`: E0 minus the negative surface potential that phase has built up (uncoated
        insulators in high vacuum; see _beam_step)."""
        if spec is None or k is None or not self._charging_phase(sub, spec):
            return self.kv
        return max(0.0, self.kv - self.v_surface.get(k, 0.0))

    def _v_equilibrium(self, sub, theta):
        """Surface potential (V) at which the total yield of the phase is 1 at this incidence (landing energy = E2(theta)); 0 when the
        yield stays above 1 up to E0."""
        e2 = second_crossover_kev(sub, theta)
        if e2 is None:
            return max(0.0, self.kv - 100.0)                   # total yield never above 1: charges until the beam is nearly stopped
        return max(0.0, self.kv - e2 * 1000.0)

    # ---------------------------------------------------------------- read
    def _lamp(self, which):
        if not self.power:
            return 'off'
        vs = self.vac_state
        if which == 'vent':
            return 'on' if vs == 'vented' else 'blinking' if vs == 'venting' else 'off'
        if which == 'evac':
            return 'on' if vs in ('hv_ready', 'lv_ready') else 'blinking' if vs in ('roughing', 'hv_pumping', 'lv_pumping') else 'off'
        return 'on' if vs == 'lv_ready' else 'blinking' if vs == 'lv_pumping' else 'off'

    def _lit(self, mode):
        return mode == 'on' or (mode == 'blinking' and int(math.floor(self.time_s * 2.0)) % 2 == 0)

    def _filament_state(self):
        if self.fil_burned:
            return 'burned_out'
        if not self.ht:
            return 'off'
        return 'saturated' if self.heat >= 1.0 - 1e-9 else 'heating'

    def read(self, visibility='agent'):
        m = self.readings if self.power else None
        acq = self.acq
        lamp = {w: self._lamp(w) for w in ('vent', 'evac', 'lv')}
        dp = probe(self.kv, max(self._probe_current(), 1e-13), max(self.focus_wd, 1e-3))
        values = {
            'clock.time_s': self.time_s, 'plant.configured': self.plant is not None, 'power.enabled': self.power, 'system.state': self.state,
            'system.error_code': self.error, 'fault.injected': list(self.faults), 'vacuum.mode': self.vac_mode, 'vacuum.state': self.vac_state, 'vacuum.lv_setpoint_pa': self.lv_set,
            'vacuum.chamber_pressure_indicated_pa': m['pirani'] if m else None, 'vacuum.gun_pressure_indicated_pa': m['penning'] if m else None,
            'vacuum.lv_pressure_indicated_pa': m['lv'] if m else None,
            'vacuum.evacuation_elapsed_s': (self.time_s - self.evac_t0) if self.evac_t0 is not None else None,
            'panel.vent_lamp': self._lit(lamp['vent']), 'panel.vent_lamp_mode': lamp['vent'], 'panel.evac_lamp': self._lit(lamp['evac']),
            'panel.evac_lamp_mode': lamp['evac'], 'panel.lv_lamp': self._lit(lamp['lv']), 'panel.lv_lamp_mode': lamp['lv'],
            'chamber.door_state': self._door_state(), 'specimen.loaded_id': self.loaded,
            'specimen.bench_ids': sorted(k for k, s in self.specimens.items() if s['location'] == 'bench'),
            'stage.x_indicated_m': round(self._stage_ind(0), 7), 'stage.y_indicated_m': round(self._stage_ind(1), 7),
            'stage.z_indicated_m': round(self.stage['z'], 4), 'stage.tilt_indicated_rad': round(self.stage['tilt'] / math.radians(0.5)) * math.radians(0.5),
            'stage.rotation_indicated_rad': round(self.stage['rot'] / math.radians(1.0)) * math.radians(1.0),
            'stage.moving': any(abs(self.stage[k] - self.stage_target[k]) > 1e-12 for k in self.stage),
            'beam.accelerating_voltage_v': self.kv, 'beam.probe_current_setpoint_a': self.ip_set, 'beam.ht_on': self.ht, 'beam.filament_state': self._filament_state(),
            'beam.emission_indicated_a': m['emission'] if m else None, 'scan.magnification': self.mag, 'scan.field_width_m': PRINT_WIDTH_M / self.mag,
            'optics.focus_wd_m': self.focus_wd, 'optics.astigmatism_corrected': self.astig_ok, 'optics.acb_done': self.acb is not None,
            'detector.selected': self.detector, 'acquisition.id': acq['record_id'] if acq else None, 'acquisition.kind': acq['kind'] if acq else None,
            'acquisition.progress': min(1.0, (self.time_s - acq['t0']) / acq['duration']) if acq else None, 'acquisition.started_by_other_agent': False,
            'result.last_image_id': None, 'result.last_spectrum_id': None,
            'chamber.door_position_m': self.door, 'stage.x_m': self.stage['x'], 'stage.y_m': self.stage['y'], 'stage.z_lift_m': self.stage['z'],
            'stage.tilt_rad': self.stage['tilt'], 'stage.rotation_rad': self.stage['rot'],
            'truth.chamber_pressure_pa': self.p_ch, 'truth.gun_pressure_pa': self._p_gun(), 'truth.gas_load_pa_m3_s': self._gas_load_now(),
            'truth.tmp_speed_fraction': self.tmp_speed, 'truth.filament_on_time_s': self.fil_used_s, 'truth.filament_life_s': self.fil_life_s,
            'truth.emission_a': EMISSION_SAT_A * self.heat * self._emission_factor() if (self.ht and not self.fil_burned) else 0.0, 'truth.probe_current_a': self._probe_current(),
            'truth.probe_diameter_m': dp[0], 'truth.working_distance_m': self._wd_true(), 'truth.defocus_m': self.focus_wd - self._wd_true(),
            'truth.astigmatism_m': self.astig, 'truth.specimen_charge_c': SURFACE_CAPACITANCE_F * sum(self.v_surface.values()),
            'truth.specimens': {k: dict(location=s['location'], phases=copy.deepcopy(s['phases']), diameter_m=s['diameter_m'], height_m=s['height_m'],
                                        topography=s['topography'], coating=s['coating'], coating_thickness_m=s['coating_thickness_m'], wet=s['wet'])
                                for k, s in self.specimens.items()},
        }
        values.update(self._operation_values())
        return {k: v for k, v in values.items() if SLOTS[k]['visibility'] == 'agent' or visibility == 'evaluator'}

    # ---------------------------------------------------------------- vacuum model
    def _outgas(self):
        if self.pump_t0 is None:
            return 0.0
        t = max(0.0, self.time_s - self.pump_t0)
        q = self.outgas_q0 * OUTGAS_TC_S / (OUTGAS_TC_S + t)
        if self._wet_exposed():
            q += WET_OUTGAS_PA_M3_S * math.exp(-self._spec()['_wet_exposed_s'] / WET_TAU_S)
        return q

    def _wet_exposed(self):
        """A wet specimen releases water (and dries) only while it is inside the closed chamber and the chamber is not being vented or at
        atmosphere: drawn out, the stage and specimen sit in room air; the water it has lost stays lost (venting does not re-wet it)."""
        s = self._spec()
        return s is not None and s['wet'] and self.door <= 1e-9 and self.pump_t0 is not None and self.vac_state not in ('vented', 'venting')

    def _vac_coeffs(self):
        """Linear gas balance dP/dt = (Q0 + K (P_atm - P) - S P) / V for the present valve / pump configuration."""
        q0 = LEAK_CLOSED_PA_M3_S + self._outgas()
        k = 0.0 if self.door <= 1e-9 else DOOR_GAP_CONDUCTANCE_M3_S
        vs = self.vac_state
        s = 0.0
        if vs == 'venting' or vs == 'vented':
            k += VENT_CONDUCTANCE_M3_S
        elif vs == 'roughing':
            s = RP_EFF_M3_S * self._slow_leak_factor()
            q0 += RP_EFF_M3_S * RP_ULTIMATE_PA
        elif vs in ('hv_pumping', 'hv_ready'):
            s = TMP_SPEED_M3_S * self.tmp_speed + (RP_EFF_M3_S if self.tmp_speed < 0.05 else 0.0)
        elif vs in ('lv_pumping', 'lv_ready'):
            s = LV_ORIFICE_M3_S
            k += LV_VALVE_MAX_M3_S * self.lv_valve
        return q0, k, s

    def _gas_load_now(self):
        q0, k, s = self._vac_coeffs()
        return q0 + k * (P_ATM - self.p_ch)

    def _vacuum_step(self, dt):
        q0, k, s = self._vac_coeffs()
        rt = GAS_CONSTANT * self._room_k()
        lam = (s + k) / V_SYSTEM_M3
        p0 = self.p_ch
        if lam > 0:
            pss = (q0 + k * P_ATM) / (s + k)
            e = math.exp(-lam * dt)
            p1 = pss + (p0 - pss) * e
            integ = pss * dt + (p0 - pss) * (1.0 - e) / lam
        else:
            p1 = p0 + q0 * dt / V_SYSTEM_M3
            integ = 0.5 * (p0 + p1) * dt
        self.led['gas_admitted_mol'].add((q0 * dt + k * (P_ATM * dt - integ)) / rt)
        self.led['gas_pumped_mol'].add(s * integ / rt)
        self.p_ch = max(p1, 1e-9)

    # ---------------------------------------------------------------- charge / beam bookkeeping
    def _discharge(self):
        """Release the stored surface charge (to ground through the holder when the specimen is handled, or to the gas ions in LV)."""
        stored = SURFACE_CAPACITANCE_F * sum(self.v_surface.values())
        if stored > 0.0:
            self.led['absorbed_charge_c'].add(stored)
        self.v_surface = {}

    def _beam_on_specimen(self):
        return self._beam_ready() and self.door <= 1e-9 and self._vac_ready()

    def _dose_points(self):
        """Where the electrons go: the stationary spot during an EDS point analysis, otherwise the scanned field of view (6 x 4 grid).
        Each entry: (substance, on the specimen, phase index, local slope, slope azimuth, weight)."""
        spec = self._spec()
        u0, v0, on0 = self._beam_point()
        acq = self.acq
        if acq is not None and acq['kind'] == 'eds' and acq.get('mode') == 'point':
            sub, th, az, k = self._phase_sub(spec if on0 else None, u0, v0)
            return [(sub, on0, k, th, az, 1.0)]
        fw = PRINT_WIDTH_M / self.mag * (RDC_FIELD if (self.scan_mode == 'RDC' and acq is None) else 1.0)
        th = self._scan_angle()
        c, s = math.cos(th), math.sin(th)
        cos_t = self._y_div()
        pts = []
        for i in range(6):
            for j in range(4):
                xs, ys = ((i + 0.5) / 6 - 0.5) * fw, ((j + 0.5) / 4 - 0.5) * 0.75 * fw / cos_t
                u, v = u0 + c * xs - s * ys, v0 + s * xs + c * ys
                on = spec is not None and math.hypot(u, v) <= spec['diameter_m'] / 2
                sub, th, az, k = self._phase_sub(spec if on else None, u, v)
                pts.append((sub, on, k, th, az, 1.0 / 24.0))
        return pts

    def _beam_step(self, dt):
        """Beam charge bookkeeping and charging over one cycle (20 sub-steps).  Each uncoated insulating phase of the loaded specimen
        carries its own negative surface potential V_k (capacitance SURFACE_CAPACITANCE_F); electrons land with E0 - V_k and the net
        deposited current I (1 - eta - delta) at the actual incidence (stage tilt with the local slope) charges V_k towards the
        potential at which the total yield is 1 (landing energy = E2 at that incidence); a phase whose total yield is above 1 at its
        landing energy discharges towards that equilibrium."""
        spec = self._spec()
        if not self._beam_on_specimen():
            if self.vac_mode == 'LV' or spec is None or spec['coating'] != 'none':
                self._discharge()
            return
        if self.vac_mode == 'LV' or spec is None or spec['coating'] != 'none':
            self._discharge()
        ip = self._probe_current()
        if ip <= 0.0:
            return
        points = self._dose_points()
        n_sub = 20
        h = dt / n_sub
        for _ in range(n_sub):
            for sub, on, k, th_l, az_l, w in points:
                comp = sub['composition_mass_fraction']
                theta = self._incidence(th_l, az_l)[0]
                charging = on and k is not None and self._charging_phase(sub, spec)
                el = self._landing_ev(sub, spec if on else None, k)
                if charging and el <= MIRROR_EV:
                    # the surface is charged beyond the beam energy (kV lowered after charging): the beam is reflected (mirror),
                    # nothing lands, the potential stays
                    q = ip * h * w
                    self.led['beam_charge_c'].add(q)
                    self.led['electrons_on_specimen'].add(q / E_CHARGE)
                    self.led['backscattered_charge_c'].add(q)
                    continue
                e_kev = el / 1000.0
                et = eta(comp, e_kev, theta)
                de = se_yield(sub, comp, e_kev, theta)
                q = ip * h * w
                self.led['beam_charge_c'].add(q)
                self.led['electrons_on_specimen'].add(q / E_CHARGE)
                self.led['beam_energy_j'].add(q * el)
                self.led['backscattered_charge_c'].add(q * et)
                self.led['secondary_charge_c'].add(q * de)
                deposited = q * (1.0 - et - de)                  # negative when more electrons leave than arrive
                stored = 0.0
                if charging:
                    v0 = self.v_surface.get(k, 0.0)
                    v_eq = self._v_equilibrium(sub, theta)
                    dv = deposited / SURFACE_CAPACITANCE_F * self._charging_factor()
                    # the potential only moves in the direction of the deposited charge and stops at the equilibrium
                    v1 = min(v0 + dv, max(v_eq, v0)) if dv >= 0.0 else max(v0 + dv, min(v_eq, v0), 0.0)
                    self.v_surface[k] = v1
                    stored = (v1 - v0) * SURFACE_CAPACITANCE_F
                self.led['absorbed_charge_c'].add(deposited - stored)

    # ---------------------------------------------------------------- commands
    def invoke(self, capability_id, **a):
        if capability_id not in CAPS:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        validate(CAPS[capability_id]['input_schema'], a)
        fn = getattr(self, '_cmd_' + capability_id.replace('.', '_'))
        if CAPS[capability_id]['kind'] not in ('query', 'analysis', 'simulation') and \
                (self.scan_mode == 'SLOW' or (capability_id == 'scan.set_mode' and a.get('mode') == 'SLOW')):
            self._slow_hold()                                                  # the Slow scan repaints over what the screen shows now
        out = fn(**a)
        if CAPS[capability_id]['kind'] in ('device_command', 'robot_skill'):
            self.last_op_s = self.time_s                                       # Eco standby counts from the last operation
        if capability_id in _ACB_INTERLOCK:
            self._acb_interlock()
        if CAPS[capability_id]['kind'] not in ('query', 'analysis', 'simulation'):   # sim.step counts its own revision when time advances
            self.revision += 1
        return out

    def _need_plant(self):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')

    def _need_power(self):
        self._need_plant()
        if not self.power:
            raise ActionRejected('POWER_OFF')

    def _need_ready(self, allow_error=False):
        self._need_power()
        if self.state != 'ready':
            raise ActionRejected('NOT_READY')
        if self.error is not None and not allow_error:
            raise ActionRejected('ERROR_PENDING')

    def _need_idle(self):
        if self.acq is not None:
            raise ActionRejected('ACQUISITION_RUNNING')

    def _need_beam(self):
        if not self._vac_ready():                               # a ready vacuum implies the draw-out door is closed
            raise ActionRejected('VACUUM_NOT_READY')
        if not self._beam_ready():
            raise ActionRejected('BEAM_NOT_READY')

    def _cmd_plant_configure(self, room_k, relative_humidity, mains_voltage_v, vent_gas, noise_seed):
        first = self.plant is None
        if not first:
            noise_seed = self.plant['noise_seed']
            self.p_ch *= room_k / self.plant['room_k']           # the enclosed gas follows the room temperature at constant volume
        self.plant = dict(room_k=room_k, relative_humidity=relative_humidity, mains_voltage_v=mains_voltage_v, vent_gas=vent_gas, noise_seed=noise_seed)
        if first:
            self.rng = random.Random(noise_seed)
            self.fil_life_s = self._draw_life()
            self.outgas_q0 = self._outgas_q0()
            self._n0 = self._gas_mol()
        if not ROOM_RANGE_K[0] <= room_k <= ROOM_RANGE_K[1]:
            self._event('warning', 'ROOM_OUT_OF_SPEC', '室温超出安装要求15–27°C')
        if relative_humidity > HUMIDITY_MAX:
            self._event('warning', 'HUMIDITY_OUT_OF_SPEC', '相对湿度超过安装要求60%（放气后水汽放气增加）')
        return dict(configured=True)

    def _draw_life(self):
        u = max(1e-12, 1.0 - self.rng.random())
        return FILAMENT_LIFE_S / math.gamma(1.0 + 1.0 / FILAMENT_WEIBULL_K) * (-math.log(u)) ** (1.0 / FILAMENT_WEIBULL_K)

    def _outgas_q0(self):
        if self.plant and self.plant['vent_gas'] == 'dry_nitrogen':
            return OUTGAS_N2_PA_M3_S
        rh = self.plant['relative_humidity'] if self.plant else 0.5
        return OUTGAS_AIR_PA_M3_S * (1.0 + 2.0 * rh)

    def _cmd_specimen_provide(self, specimen_id, phases, diameter_m, height_m, topography, coating, coating_thickness_m, wet):
        self._need_plant()
        if specimen_id in self.specimens or len(self.specimens) >= 20:
            raise ActionRejected('SPECIMEN_EXISTS')
        if any(p['substance_id'] not in _lib()['substances'] for p in phases):
            raise ActionRejected('UNKNOWN_SUBSTANCE')
        if abs(sum(p['area_fraction'] for p in phases) - 1.0) > 1e-6 or any(p['area_fraction'] <= 0.0 for p in phases):
            raise ActionRejected('AREA_FRACTIONS_INVALID')
        if (coating == 'none') != (coating_thickness_m == 0.0):
            raise ActionRejected('COATING_INVALID')
        if diameter_m > SPECIMEN_MAX_DIAMETER_M + 1e-12 or height_m > SPECIMEN_MAX_HEIGHT_M + 1e-12:
            raise ActionRejected('SPECIMEN_TOO_LARGE')
        grain = sum(p['area_fraction'] * p['grain_size_m'] for p in phases)
        seed = int(_hash01(sum(map(ord, specimen_id)) * 7919 + len(specimen_id), self.plant['noise_seed']) * 2 ** 31)
        self.specimens[specimen_id] = dict(phases=copy.deepcopy(phases), diameter_m=diameter_m, height_m=height_m, topography=topography, coating=coating,
                                           coating_thickness_m=coating_thickness_m, wet=wet, location='bench', _grain=grain, _seed=seed, _wet_exposed_s=0.0)
        self.used_substances.update(p['substance_id'] for p in phases)
        return dict(specimen_id=specimen_id, provided=True)

    def _cmd_power_set(self, enabled):
        self._need_plant()
        if enabled and not self.power:
            if abs(self.plant['mains_voltage_v'] - MAINS_NOMINAL_V) > MAINS_TOL * MAINS_NOMINAL_V + 1e-9:
                raise ActionRejected('MAINS_OUT_OF_RANGE')
            self.power = True
            self.state, self.state_t0 = 'booting', self.time_s
            self.shutdown_done = False
            self.error = None
            if self.vac_state == 'idle':                          # the chamber was left under vacuum: start-up pumping
                self._start_evac()
            self._event('power', 'POWER_ON', '钥匙转到START：主机上电，软件加载约60s')
        elif not enabled and self.power:
            self._power_off()
        return dict(enabled=self.power)

    def _cmd_power_key_toggle(self):
        return self._cmd_power_set(not self.power)

    def _power_off(self):
        if not self.shutdown_done:
            self._event('warning', 'UNSAFE_POWER_OFF', '未先软件关机就断电：电子枪高压与泵立即停止')
        self._stop_acq('failed', 'POWER_OFF')
        self.power = False
        self.state = 'off'
        self.ht = False
        self.tmp_on = False
        self.evac_t0 = None
        if not (self.vac_state == 'vented' and self.p_ch >= VENT_READY_PA):
            self.vac_state = 'idle'                               # valves close without power: a chamber under vacuum only leaks slowly
        self.readings = None
        self.gun_auto, self.frozen = None, None                              # auto adjustment and recording stop; SEM Operation keeps its settings
        self.sem_ui['movie'] = None
        self._event('power', 'POWER_OFF', '钥匙转到OFF：主机断电')

    def _cmd_system_shutdown(self):
        self._need_ready(allow_error=True)
        self._need_idle()
        self.ht = False
        self.state = 'shutdown'
        self.shutdown_done = True
        self._event('power', 'SHUTDOWN', '软件关机：高压关闭、记录已保存；可把钥匙转到OFF')
        return dict(shutdown=True)

    def _cmd_error_acknowledge(self):
        self._need_power()
        if self.error is None:
            raise ActionRejected('NO_ERROR')
        code, self.error = self.error, None
        return dict(acknowledged=code)

    def _start_evac(self):
        self.vac_state = 'roughing'
        self.tmp_on = True
        self.evac_t0 = self.time_s
        self.evac_timeout_raised = False
        if self.pump_t0 is None:
            self.pump_t0 = self.time_s

    def _vent_complete(self):
        """The chamber has reached atmospheric pressure through the vent valve: the only way to the 'vented' state."""
        self.vac_state = 'vented'
        self.pump_t0 = None
        self.outgas_q0 = self._outgas_q0()
        self._event('vacuum', 'VENT_COMPLETE', '样品室已放气到大气压')

    def _at_atmosphere(self):
        return self.vac_state == 'vented' and self.p_ch >= VENT_READY_PA

    def _cmd_vacuum_evacuate(self):
        self._need_ready()
        self._need_idle()
        if 'gun_chamber_open' in self.faults:
            raise ActionRejected('GUN_CHAMBER_OPEN')                        # Error ID 0331: close the gun chamber, then press EVAC
        if self.vac_state in ('vented', 'venting', 'idle'):
            self._start_evac()
            self._event('vacuum', 'EVAC', 'EVAC：放气阀关闭，机械泵粗抽，涡轮分子泵启动')
        return dict(state=self.vac_state)

    def _cmd_vacuum_vent(self):
        self._need_ready()
        self._need_idle()
        if self.ht:
            self.ht = False
            self._event('gun', 'HT_OFF', '放气前自动关闭电子枪高压')
        if self.vac_state not in ('vented', 'venting'):
            self.vac_state = 'venting'
            self.evac_t0 = None
            self.tmp_on = False
            gas = '干燥氮气' if self.plant['vent_gas'] == 'dry_nitrogen' else '室内空气'
            if any(f in self.faults for f in STAGE_FAULTS):                   # the X/Y motors are inoperable (manual 3.14): the stage stays
                self._event('vacuum', 'VENT', f'VENT：放气（{gas}）；样品台电机故障，X/Y不回交换位')
            elif self._path_collision(self.stage_target['z'], self.stage_target['tilt'], 0.0):
                # on a tilted stage the Y return would lift the specimen rim towards the pole piece: X returns, Y stays
                self.stage_target['x'] = 0.0
                self._event('vacuum', 'VENT', f'VENT：放气（{gas}），样品台X回到交换位')
                self._event('warning', 'Y_RETURN_SKIPPED', '样品台倾斜时Y回位会使样品升到极靴附近：Y未自动回位，请先降低Z或减小倾斜后用stage.move_xy回位')
            else:
                self.stage_target['x'] = self.stage_target['y'] = 0.0
                self._event('vacuum', 'VENT', f'VENT：放气（{gas}），样品台X/Y回到交换位')
        return dict(state=self.vac_state)

    def _cmd_vacuum_toggle_lv(self):
        self._need_ready()
        self._need_idle()
        if self.ht:                                             # the mode change runs with the beam off (the column pressure changes)
            self.ht = False
            self._event('gun', 'HT_OFF', '切换真空模式：先关闭电子枪高压，达到新的真空后再开始观察')
        self.vac_mode = 'LV' if self.vac_mode == 'HV' else 'HV'
        if self.vac_mode == 'LV':
            if self.detector in ('SED', 'REF'):
                self.detector = 'BED_COMPO'
                self._event('warning', 'DETECTOR_SWITCHED', 'LV模式不能用SED，已切换到BED（COMPO）')
            if self.vac_state in ('hv_pumping', 'hv_ready'):
                self.vac_state = 'lv_pumping'
                self.lv_ok_s = 0.0
                self.lv_int = 0.0
                self.evac_t0 = self.time_s
        else:
            if self.vac_state in ('lv_pumping', 'lv_ready'):
                self.vac_state = 'hv_pumping'
                self.lv_valve = 0.0
                self.evac_t0 = self.time_s
        self.acb = None
        self._event('vacuum', 'MODE_' + self.vac_mode, '真空模式切换到' + ('低真空' if self.vac_mode == 'LV' else '高真空'))
        return dict(mode=self.vac_mode)

    def _cmd_vacuum_set_lv_pressure(self, pressure_pa):
        self._need_power()
        self.lv_set = float(pressure_pa)
        return dict(pressure_pa=self.lv_set)

    def _cmd_chamber_draw_out(self):
        self._need_plant()
        self._need_idle()
        if not self._at_atmosphere():
            raise ActionRejected('CHAMBER_UNDER_VACUUM')
        self.door_target = DOOR_TRAVEL_M
        return dict(target_m=self.door_target)

    def _cmd_chamber_draw_in(self):
        self._need_plant()
        self._need_idle()
        if self._path_collision(self.stage_target['z'], self.stage_target['tilt'], self.stage_target['y'], strict=True):
            raise ActionRejected('STAGE_COLLISION_RISK')
        self.door_target = 0.0
        return dict(target_m=self.door_target)

    def _cmd_specimen_load(self, specimen_id):
        self._need_plant()
        if not self._stage_outside():                          # fully drawn out and not being pushed back in
            raise ActionRejected('DOOR_NOT_OPEN')
        if specimen_id not in self.specimens:
            raise ActionRejected('UNKNOWN_SPECIMEN')
        if self.specimens[specimen_id]['location'] != 'bench':
            raise ActionRejected('SPECIMEN_NOT_ON_BENCH')
        if self.loaded is not None:
            raise ActionRejected('HOLDER_OCCUPIED')
        self.specimens[specimen_id]['location'] = 'holder'
        self.loaded = specimen_id
        self._discharge()
        self.acb = None
        self._event('specimen', 'LOAD', f'样品{specimen_id}装到样品座')
        return dict(specimen_id=specimen_id, loaded=True)

    def _cmd_specimen_unload(self):
        self._need_plant()
        if not self._stage_outside():
            raise ActionRejected('DOOR_NOT_OPEN')
        if self.loaded is None:
            raise ActionRejected('NO_SPECIMEN')
        sid, self.loaded = self.loaded, None
        self.specimens[sid]['location'] = 'bench'
        self._discharge()
        self.acb = None
        self._event('specimen', 'UNLOAD', f'样品{sid}取下')
        return dict(specimen_id=sid)

    def _cmd_fault_inject(self, code):
        self._need_plant()
        if code not in self.faults:
            self.faults.append(code)
        if self.power:
            if code in STAGE_FAULTS:
                self.stage_target['x'], self.stage_target['y'] = self.stage['x'], self.stage['y']   # the motors stop
                self.error = JSM_FAULTS[code]
            elif code == 'lv_pressure_adjust' and self.vac_mode == 'LV':
                self.sem_ui['lv_auto'] = False
                self.error = JSM_FAULTS[code]
            elif code == 'filament_failure':
                self.error = JSM_FAULTS[code]
            self._event('error', JSM_FAULTS[code], f'评测者注入的硬件故障：{code}')
        return dict(injected=list(self.faults))

    def _cmd_fault_clear(self, code):
        self._need_plant()
        if code in self.faults:
            self.faults.remove(code)
        return dict(injected=list(self.faults))

    def _cmd_stage_move_xy(self, x_m, y_m):
        self._need_ready()
        self._need_idle()
        f = next((x for x in STAGE_FAULTS if x in self.faults), None)
        if f is not None:
            raise ActionRejected(JSM_FAULTS[f])                             # manual 3.14: the motors of all axes are inoperable
        if self.door > 1e-9:
            raise ActionRejected('DOOR_OPEN')
        ex, ey = self._stage_error()                                         # commanded in the (calibrated) coordinates of the display
        tx = min(max(float(x_m) - ex, STAGE_X_RANGE_M[0]), STAGE_X_RANGE_M[1]) if ex else float(x_m)
        ty = min(max(float(y_m) - ey, STAGE_Y_RANGE_M[0]), STAGE_Y_RANGE_M[1]) if ey else float(y_m)
        if self._path_collision(self.stage_target['z'], self.stage_target['tilt'], ty):
            raise ActionRejected('STAGE_COLLISION_RISK')
        if self._path_collision(self.stage_target['z'], self.stage_target['tilt'], ty, hr=self._software_hr()):
            raise ActionRejected('STAGE_COLLISION_RISK')                     # the motor stage software limit (selected holder, entered height)
        self.sem_ui['last_move'] = [self.stage_target['x'] + ex, self.stage_target['y'] + ey]
        self.stage_target['x'], self.stage_target['y'] = tx, ty
        return dict(x_m=tx + ex if ex else tx, y_m=ty + ey if ey else ty)

    def _cmd_stage_set_z(self, z_lift_m):
        self._need_plant()                                                   # a hand knob: turning it during a scan is not refused (the scan sees it)
        if self._path_collision(z_lift_m, self.stage_target['tilt'], self.stage_target['y']):
            raise ActionRejected('STAGE_COLLISION_RISK')
        if self.sem_ui['settings']['stage']['auto_focus_tracer']:            # Auto Focus Tracer: the focus follows the Z travel (set for its target)
            self.focus_wd = min(0.060, max(0.001, self.focus_wd - (float(z_lift_m) - self.stage_target['z'])))
        self.stage_target['z'] = float(z_lift_m)
        return dict(z_lift_m=self.stage_target['z'])

    def _cmd_stage_set_tilt(self, tilt_rad):
        self._need_plant()                                                   # hand knob, as Z
        if self._path_collision(self.stage_target['z'], tilt_rad, self.stage_target['y']):
            raise ActionRejected('STAGE_COLLISION_RISK')
        self.stage_target['tilt'] = float(tilt_rad)
        self.acb = None
        return dict(tilt_rad=self.stage_target['tilt'])

    def _cmd_stage_rotate(self, rotation_rad):
        self._need_plant()                                                   # hand knob, as Z
        self.stage_target['rot'] = float(rotation_rad)
        return dict(rotation_rad=self.stage_target['rot'])

    def _cmd_beam_set_voltage(self, voltage_v):
        self._need_ready()
        self._need_idle()
        v = quantize_kv(voltage_v)
        if v != self.kv:
            self.kv = v
            self.acb = None
            if self.ht:
                self.astig, self.astig_ok = self.rng.uniform(10e-9, 40e-9), False
        return dict(voltage_v=self.kv)

    def _cmd_beam_set_probe_current(self, current_a):
        self._need_ready()
        self._need_idle()
        self.ip_set = float(current_a)
        self.acb = None
        return dict(current_a=self.ip_set)

    def _cmd_beam_observation_on(self):
        self._need_ready()
        if 'filament_failure' in self.faults:
            raise ActionRejected('FILAMENT_FAILURE')                        # remove the filament, check for whiskers, replace it
        if not self._vac_ready():
            raise ActionRejected('VACUUM_NOT_READY')
        if self.fil_burned:
            raise ActionRejected('FILAMENT_BURNED_OUT')
        if not self.ht:
            self.ht = True
            if self.gun_mode == 'auto':                                         # fully automatic filament heating and gun alignment
                self.gun['heating'] = GUN_SAT_POS
                for k in ('shift_x', 'shift_y', 'tilt_x', 'tilt_y'):
                    self.gun[k] = self.align_err['gun_' + k]
            self._event('gun', 'HT_ON', f'开始观察：{self.kv / 1000:.1f}kV，灯丝自动加热到饱和并自动对中')
        return dict(ht_on=True)

    def _cmd_beam_observation_off(self):
        self._need_power()
        if self.ht:
            self.ht = False
            self._stop_acq('failed', 'BEAM_OFF')
            self._event('gun', 'HT_OFF', '停止观察：高压关闭')
        return dict(ht_on=False)

    def _cmd_gun_replace_filament(self):
        self._need_plant()
        if not self._at_atmosphere():
            raise ActionRejected('VENT_REQUIRED')
        self.fil_serial += 1
        self.fil_life_s = self._draw_life()
        self.fil_used_s = 0.0
        self.fil_burned = False
        self.heat = 0.0
        self.astig, self.astig_ok = self.rng.uniform(15e-9, 50e-9), False
        if 'filament_failure' in self.faults:
            self.faults.remove('filament_failure')                          # a new filament (no whiskers)
        if self.error in ('FILAMENT_BURNOUT', 'FILAMENT_FAILURE'):
            self.error = None
        self._event('maintenance', 'FILAMENT_REPLACED', f'更换钨灯丝（第{self.fil_serial}根）')
        return dict(filament_serial=self.fil_serial)

    def _cmd_scan_set_magnification(self, magnification):
        self._need_ready()
        self._need_idle()
        self.mag = float(magnification)
        return dict(magnification=self.mag, field_width_m=PRINT_WIDTH_M / self.mag)

    def _cmd_optics_autofocus(self):
        self._need_ready()
        self._need_idle()
        self._need_beam()
        wd = self._wd_true()
        d, a = probe(self.kv, max(self._probe_current(), 1e-13), max(wd, 1e-3))
        dof = max(d, PRINT_WIDTH_M / self.mag / 1000.0) / a
        self.focus_wd = min(0.060, max(0.001, wd + self.rng.gauss(0.0, dof / 8.0)))
        return dict(focus_wd_m=self.focus_wd)

    def _cmd_optics_set_focus(self, focus_wd_m):
        self._need_ready()
        self._need_idle()
        self.focus_wd = float(focus_wd_m)
        return dict(focus_wd_m=self.focus_wd)

    def _cmd_optics_auto_stigmate(self):
        self._need_ready()
        self._need_idle()
        self._need_beam()
        r, d = self.astig, self._astig_dir                                    # the stigmator takes up the residual astigmatism
        self.stig = [self.stig[0] + (r - 1e-9) * d[0], self.stig[1] + (r - 1e-9) * d[1]]
        self.astig, self.astig_ok = 1e-9, True
        return dict(astigmatism_m=self.astig)

    def _cmd_optics_auto_contrast_brightness(self):
        self._need_ready()
        self._need_idle()
        self._need_beam()
        # the grey range spans the histogram of the live frame (640 x 480, 10 us): the noise-free signal range widened by +-2.5 sigma of the
        # pixel noise (about the 0.6 % / 99.4 % tails), so a frame without signal contrast (flat specimen in TOPO) is stretched to its
        # noise, never to an unbounded gain
        fr = self._frame_signals(dwell=1e-5, nx=640, ny=480, noise=False)
        sig = fr['pixel_signal']
        sigma = {r['key']: r['sigma'] for r in fr['regions']}
        s_px = np.array([[sigma.get(fr['keys'][j, i], 0.0) for i in range(THUMB_W)] for j in range(THUMB_H)])
        lo, hi = float(np.min(sig - 2.5 * s_px)), float(np.max(sig + 2.5 * s_px))
        if hi - lo < 1e-9 * max(abs(hi), abs(lo), 1e-30):
            lo, hi = 0.0, 2.0 * max(abs(hi), 1e-30)
        span = hi - lo
        lo -= 0.05 * span; hi += 0.05 * span
        au = self.sem_ui['settings']['auto']                                 # Settings-Auto ACB target (0.5 / 0.5: grey 20-235)
        span = 215.0 * 2.0 ** ((au['acb_contrast'] - 0.5) * 2.0)
        gain = span / (hi - lo)
        self.acb = dict(gain=gain, offset=20.0 + 200.0 * (au['acb_brightness'] - 0.5) - gain * lo, detector=self.detector)
        self.image_adjust = dict(brightness=0.5, contrast=0.5, suppress=0.5)
        return dict(gain=gain, offset=self.acb['offset'])

    def _cmd_detector_select(self, detector):
        self._need_ready()
        self._need_idle()
        if detector in ('SED', 'REF') and self.vac_mode == 'LV':
            raise ActionRejected('DETECTOR_NOT_AVAILABLE')
        if detector != self.detector:
            self.detector = detector
            self.acb = None
        return dict(detector=self.detector)

    def _new_acq(self, kind, duration, **data):
        self.op_seq += 1
        if kind == 'image':
            self.img_seq += 1
            rid = f'img-{self.img_seq}'
        else:
            self.eds_seq += 1
            rid = f'eds-{self.eds_seq}'
        op = dict(operation_id=f'{kind}-op-{self.op_seq}', kind=kind, record_id=rid, phase='running', t0=self.time_s, duration=duration, **data)
        self.operations[op['operation_id']] = op
        self.acq = op
        return op

    def _cmd_image_acquire(self, pixels, dwell_s):
        self._need_ready()
        self._need_idle()
        self._need_beam()
        if len(self.images) >= MAX_RECORDS:
            raise ActionRejected('RECORDS_FULL')
        nx, ny = PIXEL_FORMATS[pixels]
        op = self._new_acq('image', nx * ny * dwell_s, pixels=pixels, dwell_s=dwell_s)
        self.images[op['record_id']] = dict(image_id=op['record_id'], status='running', detector=self.detector, accelerating_voltage_v=self.kv,
                                            probe_current_a=self.ip_set, magnification=self.mag, pixels=pixels, dwell_s=dwell_s, frame_time_s=op['duration'],
                                            started_s=self.time_s, finished_s=None, error_code=None, _op=op['operation_id'])
        self._event('acquisition', 'IMAGE_START', f'开始采集图像{op["record_id"]}（{pixels}，帧时间{op["duration"]:.1f}s）', op['record_id'])
        return dict(operation_id=op['operation_id'])

    def _check_record_owner(self, record_id):
        """Another agent's image or spectrum is refused to an agent caller (the session checks direct calls first; this covers the panel)."""
        owner, caller = self._owners.get(record_id), self._caller
        if caller is not None and self._principals.get(caller) == 'agent' and owner is not None and owner != caller \
                and self._principals.get(owner) == 'agent':
            raise ActionRejected('ACCESS_DENIED')

    def _cmd_image_result(self, image_id):
        rec = self.images.get(image_id)
        if rec is None:
            raise ActionRejected('UNKNOWN_RECORD')
        self._check_record_owner(image_id)
        return self._image_out(rec)

    def _cmd_eds_acquire(self, mode, live_time_s, process_time):
        self._need_ready()
        self._need_idle()
        self._need_beam()
        if len(self.spectra) >= MAX_RECORDS:
            raise ActionRejected('RECORDS_FULL')
        pre = self._eds_expected(mode, process_time)
        if pre['live_fraction'] < 1.0 - EDS_MAX_DEAD_TIME:
            raise ActionRejected('COUNT_RATE_TOO_HIGH')
        real = live_time_s / pre['live_fraction']
        op = self._new_acq('eds', real, mode=mode, live_time_s=live_time_s, process_time=process_time)
        self.spectra[op['record_id']] = dict(spectrum_id=op['record_id'], status='running', mode=mode, accelerating_voltage_v=self.kv, probe_current_a=self.ip_set,
                                             live_time_s=live_time_s, process_time=process_time, started_s=self.time_s, finished_s=None, error_code=None,
                                             _op=op['operation_id'])
        self._event('acquisition', 'EDS_START', f'开始EDS采谱{op["record_id"]}（{mode}，活时间{live_time_s:.0f}s，预计实时间{real:.1f}s）', op['record_id'])
        return dict(operation_id=op['operation_id'])

    def _cmd_eds_result(self, spectrum_id):
        rec = self.spectra.get(spectrum_id)
        if rec is None:
            raise ActionRejected('UNKNOWN_RECORD')
        self._check_record_owner(spectrum_id)
        return self._spectrum_out(rec)

    def _cmd_eds_quantify(self, spectrum_id, elements):
        rec = self.spectra.get(spectrum_id)
        if rec is None:
            raise ActionRejected('UNKNOWN_RECORD')
        self._check_record_owner(spectrum_id)
        if rec['status'] != 'completed':
            raise ActionRejected('NOT_COMPLETED')
        if any(e not in _lib()['elements'] or element(e)['Z'] < EDS_Z_RANGE[0] for e in elements):
            raise ActionRejected('UNKNOWN_ELEMENT')
        noise = EDS_PROCESS_TIMES[rec['process_time']]['noise_ev']
        counts = np.asarray(rec['counts'], float)
        found = list(dict.fromkeys(elements)) if elements else identify_elements(counts, rec['accelerating_voltage_v'], noise)
        if not found:
            raise ActionRejected('NO_PEAKS')
        res = quantify(counts, rec['accelerating_voltage_v'], rec['takeoff_angle_rad'], noise, found)
        out = dict(spectrum_id=spectrum_id, method='standardless ZAF (Green-Cosslett generation, Philibert absorption, family areas by weighted least squares, '
                                                   'normalised to 100 wt%)',
                   elements_identified=list(found), results=res, total_before_normalisation=None)
        self.quants[spectrum_id] = copy.deepcopy(out)
        return out

    def _cmd_records_list(self):
        return dict(images=sorted(self.images, key=lambda k: int(k.split('-')[1])), spectra=sorted(self.spectra, key=lambda k: int(k.split('-')[1])))

    def _cmd_events_read(self, since_s):
        return dict(events=[{k: v for k, v in e.items() if k != '_rid'} for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def _cmd_ledger_read(self):
        return self.ledger()

    def _cmd_provenance_read(self):
        return dict(library=LIBRARY_REL, substances=sorted(self.used_substances))

    def _cmd_sim_step(self, dt_s):
        self.step(dt_s)
        return dict(time_s=self.time_s)

    def ledger(self):
        led = {k: v.value for k, v in self.led.items()}
        stored = SURFACE_CAPACITANCE_F * sum(self.v_surface.values())
        led['stored_charge_c'] = stored
        led['charge_residual_c'] = led['beam_charge_c'] - led['backscattered_charge_c'] - led['secondary_charge_c'] - led['absorbed_charge_c'] - stored
        n = self._gas_mol()
        led['gas_in_volume_mol'] = n
        led['gas_residual_mol'] = (n - (self._n0 if self._n0 is not None else n)) - (led['gas_admitted_mol'] - led['gas_pumped_mol'])
        return led

    # ---------------------------------------------------------------- image formation
    def _incidence(self, th_l, az_l):
        """Incidence angle between the beam and the local surface normal (stage tilt about X combined with the local slope) and the
        x-slope seen by the two BED halves."""
        nx = math.sin(th_l) * math.cos(az_l)
        ny = math.sin(th_l) * math.sin(az_l)
        nz = math.cos(th_l)
        t = self.stage['tilt']
        nz2 = ny * math.sin(t) + nz * math.cos(t)
        return math.acos(max(-1.0, min(1.0, nz2))), nx

    def _yield(self, sub, spec, on, th_l, az_l, k=None):
        """Detector output per incident electron for one surface element (SED: collected electrons; BED: e-h pairs), the BSE count per
        electron (for noise) and the charging flag."""
        comp = sub['composition_mass_fraction']
        el = self._landing_ev(sub, spec if on else None, k if on else None)
        theta, slope_x = self._incidence(th_l, az_l)
        charging = on and self._charging_phase(sub, spec) and el < self.kv - 1e-6
        if charging and el <= MIRROR_EV:                       # mirror: the reflected beam returns up the axis; only SE3 from the chamber
            return (SE3_FRACTION, 0.0, 1.0, True) if self.detector in ('SED', 'REF') else (0.0, 0.0, 1.0, True)
        e_kev = el / 1000.0
        if self.detector == 'REF':                                             # SED grid rejects the specimen SE: only SE3 from the BSE
            return SE3_FRACTION * eta(comp, e_kev, theta), 0.0, 1.0, charging
        if self.detector == 'SED':
            y = SE_COLLECTION * se_yield(sub, comp, e_kev, theta) + SE3_FRACTION * eta(comp, e_kev, theta)
            if charging:
                y *= 1.0 + 3.0 * (1.0 - el / self.kv)
            return y, 0.0, 1.0, charging
        h = max(self._wd_true() - BED_DROP_M, 1e-4)
        f_col = math.sin(math.atan(BED_R_OUT_M / h)) ** 2 - math.sin(math.atan(BED_R_IN_M / h)) ** 2
        et = eta(comp, e_kev, theta)
        k_e = 0.6 + 0.25 * min(mean_z(comp), 80.0) / 80.0
        g = max(0.0, k_e * el - BED_DEAD_LAYER_EV) / PAIR_EV
        n_bse = et * f_col
        if self.detector == 'BED_COMPO':
            return n_bse * g, n_bse, g, charging
        if self.detector == 'BED_TOPO':
            return n_bse * g * slope_x, n_bse, g, charging
        return 0.5 * n_bse * g * (1.0 + slope_x), n_bse, g, charging

    def _frame_signals(self, dwell, nx, ny, noise=True):
        spec = self._spec()
        fw = PRINT_WIDTH_M / self.mag
        fh = 0.75 * fw
        u0, v0, _ = self._beam_point()
        th = self._scan_angle()
        c, s = math.cos(th), math.sin(th)
        cos_t = self._y_div()
        ip = self._probe_current()
        rate = ip / E_CHARGE
        # LV skirt: only the unscattered fraction forms the probe, the rest spreads over the field of view
        f_u = 1.0
        if self.vac_mode == 'LV':
            n_gas = self.p_ch / (K_BOLTZMANN * self._room_k())
            f_u = math.exp(-GAS_SIGMA30_M2 * (30000.0 / self.kv) ** 0.8 * n_gas * max(self._wd_true(), 1e-4))
        sig = np.zeros((THUMB_H, THUMB_W))
        keys = np.empty((THUMB_H, THUMB_W), dtype=object)
        pure = np.zeros((THUMB_H, THUMB_W), dtype=bool)           # all four sub-samples of the thumbnail pixel on the same phase
        stats = {}
        for j in range(THUMB_H):
            for i in range(THUMB_W):
                acc = 0.0
                first = None
                same = True
                for di, dj in ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)):
                    xs = ((i + di) / THUMB_W - 0.5) * fw
                    ys = (0.5 - (j + dj) / THUMB_H) * fh / cos_t
                    u, v = u0 + c * xs - s * ys, v0 + s * xs + c * ys
                    on = spec is not None and math.hypot(u, v) <= spec['diameter_m'] / 2
                    sub, th_l, az_l, k = self._phase_sub(spec if on else None, u, v)
                    key = k if on else 'holder'
                    y, n_bse, g, charging = self._yield(sub, spec, on, th_l, az_l, k)
                    acc += y / 4.0
                    st = stats.setdefault(key, dict(sub=sub, n=0, y=0.0, n_bse=0.0, g=g, charging=False))
                    st['n'] += 1; st['y'] += y; st['n_bse'] += n_bse; st['charging'] |= charging
                    keys[j, i] = key
                    if first is None:
                        first = key
                    elif key != first:
                        same = False
                pure[j, i] = same
                sig[j, i] = acc * rate
        mean_sig = float(sig.mean())
        sig = f_u * sig + (1.0 - f_u) * mean_sig
        # blur: probe (at the focus plane) with defocus, astigmatism and vibration against the thumbnail pixel
        wd_f = max(self.focus_wd, 1e-3)
        d_p, alpha = probe(self.kv, max(ip, 1e-13), wd_f)
        blur = math.sqrt(d_p ** 2 + (2.0 * alpha * abs(self.focus_wd - self._wd_true())) ** 2 + self.astig ** 2 + VIBRATION_M ** 2 + self._extra_blur(alpha) ** 2)
        k_bed = BED_RES_FACTOR if self.detector not in ('SED', 'REF') else 1.0
        px_full = fw / nx

        def gauss_blur(img, sigma_px):
            if sigma_px <= 0.3:
                return img
            r = int(min(12, math.ceil(3 * sigma_px)))
            ker = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma_px) ** 2)
            ker /= ker.sum()
            pad = np.pad(img, r, mode='edge')
            out = np.apply_along_axis(lambda m: np.convolve(m, ker, mode='valid'), 1, pad)
            return np.apply_along_axis(lambda m: np.convolve(m, ker, mode='valid'), 0, out)
        gx, gy = self._focus_gradient()
        if gx == 0.0 and gy == 0.0:
            sig = gauss_blur(sig, blur * k_bed / 2.3548 / (fw / THUMB_W))
        else:
            # tilted specimen: the defocus changes across the frame (surface height minus the Dynamic Focus ramp, along the projected scan
            # deflection); each thumbnail pixel takes the blur of its own defocus, the frame resolution the rms blur over the frame
            xs_c = ((np.arange(THUMB_W) + 0.5) / THUMB_W - 0.5) * fw
            ys_c = (0.5 - (np.arange(THUMB_H) + 0.5) / THUMB_H) * fh * max(math.cos(self.stage['tilt']), 0.05) / cos_t
            dz = (self.focus_wd - self._wd_true()) + gx * xs_c[None, :] + gy * ys_c[:, None]
            b2 = d_p ** 2 + (2.0 * alpha * dz) ** 2 + self.astig ** 2 + VIBRATION_M ** 2 + self._extra_blur(alpha) ** 2
            blur = math.sqrt(float(np.mean(b2)))
            level = np.round(np.sqrt(b2) * k_bed / 2.3548 / (fw / THUMB_W), 2)
            out = np.empty_like(sig)
            for lv in np.unique(level):
                m = level == lv
                out[m] = gauss_blur(sig, float(lv))[m]
            sig = out
        eff_res = max(K_RES * blur * k_bed, 2.0 * px_full)
        # per-region noise and signal-to-noise of one full-resolution pixel (Poisson + detector amplifier). The shot variance counts every
        # detected carrier: SED the collected electrons (DQE 0.8); BED the pairs of both halves, so the TOPO difference signal A - B carries
        # the noise of the sum A + B (a flat specimen still gives a noisy TOPO frame). In LV the scattered skirt (1 - f_u) adds the frame-mean
        # shot variance. sigma is in pixel_signal units (per second); SNR = signal / sigma, the TOPO signal being the sum signal.
        n_e = ip * dwell / E_CHARGE
        amp_var = 0.0 if self.detector in ('SED', 'REF') else (BED_AMP_NOISE_PAIRS * math.sqrt(1e-6 / dwell)) ** 2
        n_tot = 4.0 * THUMB_W * THUMB_H
        per = {}
        for key, st in stats.items():
            y = st['y'] / st['n']
            if self.detector in ('SED', 'REF'):
                shot, ref = n_e * max(y, 0.0) / 0.8, y
            else:
                nb = st['n_bse'] / st['n']
                shot, ref = n_e * nb * st['g'] ** 2 * 1.25, (nb * st['g'] if self.detector == 'BED_TOPO' else y)
            per[key] = dict(y=y, shot=shot, ref=ref, f=st['n'] / n_tot)
        shot_mean = sum(q['f'] * q['shot'] for q in per.values())
        ref_mean = sum(q['f'] * q['ref'] for q in per.values())
        regions = []
        for key, st in stats.items():
            q = per[key]
            var = f_u * q['shot'] + (1.0 - f_u) * shot_mean + amp_var
            ref = f_u * q['ref'] + (1.0 - f_u) * ref_mean
            snr = n_e * abs(ref) / math.sqrt(var) if var > 0 else 0.0
            # within-phase texture: robust spread (1.4826 x median absolute deviation) of the phase's noise-free thumbnail pixels after the
            # skirt and the blur (pure pixels, all four sub-samples on the phase; mixed pixels when the phase has fewer than 8 pure ones)
            m = (keys == key)
            vals = sig[m & pure] if int(np.count_nonzero(m & pure)) >= 8 else sig[m]
            tex = 1.4826 * float(np.median(np.abs(vals - np.median(vals)))) if vals.size >= 2 else 0.0
            regions.append(dict(key=key, substance_id=st['sub']['substance_id'], fraction=q['f'], y=q['y'], snr=snr, charging=st['charging'],
                                sigma=math.sqrt(var) / dwell, texture=tex))
        return dict(pixel_signal=sig, keys=keys, regions=regions, fov=(fw, fh), px=px_full, probe_d=d_p, eff_res=eff_res, f_u=f_u, n_avg=(nx * ny) / (THUMB_W * THUMB_H))

    def _image_complete(self, rec, op):
        nx, ny = PIXEL_FORMATS[op['pixels']]
        fr = self._frame_signals(op['dwell_s'], nx, ny)
        acb = self.acb if (self.acb and self.acb['detector'] == self.detector) else dict(gain=255.0 / (0.3 * 1e-10 / E_CHARGE), offset=0.0)
        sig = fr['pixel_signal']
        sigma_px = {r['key']: r['sigma'] for r in fr['regions']}
        grey = np.zeros_like(sig)
        for j in range(THUMB_H):
            for i in range(THUMB_W):
                noisy = sig[j, i] + self.rng.gauss(0.0, sigma_px.get(fr['keys'][j, i], 0.0) / math.sqrt(fr['n_avg']))
                grey[j, i] = self._grey(acb['offset'] + acb['gain'] * noisy)
        thumb = np.clip(np.rint(grey), 0, 255).astype(int)
        # grey-level segmentation of the frame: regions are what the image shows (ranked by mean grey), not named phases. Two phases join
        # one region (complete linkage over all members: no chaining) when their mean greys differ by less than
        # sqrt((3 s_noise)^2 + (2 s_texture)^2) of either: s_noise is the thumbnail-pixel noise (full-resolution pixel noise over
        # sqrt(n_avg)), s_texture the spread of the phase's own pixels from its surface slopes (rough / particle specimens; two
        # equal-width distributions closer than 2 sigma give a single-peaked histogram). Identical phases always merge. The phases behind
        # the regions, their templates and yields stay evaluator truth (truth.specimens, provenance.read)
        rate = self._probe_current() / E_CHARGE
        # in LV only the unscattered fraction f_u forms the image; the scattered skirt adds the frame mean everywhere (as in the thumbnail)
        f_u = fr['f_u']
        y_mean = sum(r['fraction'] * r['y'] for r in fr['regions']) / max(sum(r['fraction'] for r in fr['regions']), 1e-30)
        gain = abs(acb['gain'])
        rows = []
        for r in fr['regions']:
            y_s = f_u * r['y'] + (1.0 - f_u) * y_mean
            g_mean = float(np.clip(self._grey(acb['offset'] + acb['gain'] * y_s * rate), 0.0, 255.0))
            s_noise = gain * r['sigma'] / math.sqrt(max(fr['n_avg'], 1.0))
            s_tex = gain * r['texture']
            t = max(math.hypot(3.0 * s_noise, 2.0 * s_tex), 1.5)
            rows.append(dict(g=g_mean, t=t, f=r['fraction'], snr=r['snr'], y=y_s, charging=r['charging'], members=[]))
        rows.sort(key=lambda x: -x['g'])
        clusters = []
        for x in rows:
            c = clusters[-1] if clusters else None
            if c is not None and all(abs(x['g'] - m['g']) < max(x['t'], m['t']) for m in c['members']):
                w = c['f'] + x['f']
                for key in ('g', 'snr', 'y'):
                    c[key] = (c[key] * c['f'] + x[key] * x['f']) / w
                c['f'] = w
                c['charging'] = c['charging'] or x['charging']
                c['members'].append(dict(g=x['g'], t=x['t']))
            else:
                clusters.append(dict(x, members=[dict(g=x['g'], t=x['t'])]))
        clusters = sorted(clusters, key=lambda c: -c['f'])[:8]
        clusters.sort(key=lambda c: -c['g'])
        regions = [dict(region_id=f'region_{i + 1}', area_fraction_in_view=c['f'], mean_grey=c['g'], snr=c['snr'], charging=c['charging'])
                   for i, c in enumerate(clusters)]
        ys = [c['y'] for c in clusters]
        # contrast between the brightest and darkest region signals; not defined for the signed TOPO difference signal (None)
        if self.detector == 'BED_TOPO':
            contrast = None
        else:
            contrast = (max(ys) - min(ys)) / max(abs(max(ys)), 1e-30) if len(ys) > 1 else 0.0
        # resolution measured on the frame (edge sharpness): about 10 % uncertainty, two significant digits, never below two pixels
        eff = max(fr['eff_res'] * (1.0 + 0.1 * self.rng.gauss(0.0, 1.0)), 2.0 * fr['px'])
        rec.update(status='completed', finished_s=self.time_s, field_of_view_m=[fr['fov'][0], fr['fov'][1]], pixel_size_m=fr['px'], working_distance_m=self.focus_wd,
                   probe_diameter_m=None, effective_resolution_m=float(f'{eff:.2g}'), vacuum_mode=self.vac_mode,
                   chamber_pressure_pa=self.readings['pirani'] if self.readings else None, regions=regions, contrast=contrast,
                   charging_artifacts=any(r['charging'] for r in regions), saturated_fraction=float(np.mean((thumb <= 0) | (thumb >= 255))),
                   thumbnail=thumb.tolist(), template_source=dict(library=LIBRARY_REL, substances=[]))
        self._auto_save(rec)

    def _image_out(self, rec):
        out = {k: copy.deepcopy(v) for k, v in rec.items() if not k.startswith('_')}
        for k, v in dict(field_of_view_m=[PRINT_WIDTH_M / rec['magnification'], 0.75 * PRINT_WIDTH_M / rec['magnification']], pixel_size_m=None, working_distance_m=None,
                         probe_diameter_m=None, effective_resolution_m=None, vacuum_mode=self.vac_mode, chamber_pressure_pa=None, regions=[], contrast=None,
                         charging_artifacts=False, saturated_fraction=None, thumbnail=[], template_source=dict(library=LIBRARY_REL, substances=[])).items():
            out.setdefault(k, v)
        return out

    # ---------------------------------------------------------------- EDS
    def _eds_weights(self, mode):
        """Mixture of surface elements (phase key, substance, weight) that the electrons reach: point = X-ray excitation volume around the
        beam (half the Kanaya-Okayama range), area = the field of view; in LV the scattered skirt reaches the field of view."""
        spec = self._spec()
        u0, v0, on0 = self._beam_point()
        th = self._scan_angle()
        c, s = math.cos(th), math.sin(th)
        pts = []
        if mode == 'point':
            sub0 = self._phase_sub(spec if on0 else None, u0, v0)[0]
            r_x = 0.5 * ko_range_m(sub0['composition_mass_fraction'], sub0['density_g_cm3'], self.kv / 1000.0)
            pts = [(u0, v0, 1.0)] + [(u0 + rr * math.cos(a), v0 + rr * math.sin(a), 1.0) for rr in (0.5 * r_x, r_x) for a in np.linspace(0, 2 * math.pi, 6, endpoint=False)]
        fw = PRINT_WIDTH_M / self.mag
        area = []
        for i in range(12):
            for j in range(9):
                xs, ys = ((i + 0.5) / 12 - 0.5) * fw, ((j + 0.5) / 9 - 0.5) * 0.75 * fw / self._y_div()
                area.append((u0 + c * xs - s * ys, v0 + s * xs + c * ys, 1.0))
        f_u = 1.0
        if self.vac_mode == 'LV':
            n_gas = self.p_ch / (K_BOLTZMANN * self._room_k())
            f_u = math.exp(-GAS_SIGMA30_M2 * (30000.0 / self.kv) ** 0.8 * n_gas * max(self._wd_true(), 1e-4))
        mix = {}

        def add(points, weight):
            for u, v, _ in points:
                on = spec is not None and math.hypot(u, v) <= spec['diameter_m'] / 2
                sub, _, _, k = self._phase_sub(spec if on else None, u, v)
                key = k if on else 'holder'
                ent = mix.setdefault(key, dict(sub=sub, on=on, w=0.0))
                ent['w'] += weight / len(points)
        if mode == 'point':
            add(pts, f_u)
            if f_u < 1.0:
                add(area, 1.0 - f_u)
        else:
            add(area, 1.0)
        return mix

    def _eds_expected(self, mode, process_time):
        spec = self._spec()
        pt = EDS_PROCESS_TIMES[process_time]
        wd = max(self._wd_true(), 1e-4)
        psi, omega = eds_geometry(wd, self.stage['tilt'])
        rates = np.zeros(EDS_CHANNELS)
        gen = 0.0
        subs = set()
        coat = None
        if spec is not None and spec['coating'] != 'none':
            coat = (COATINGS[spec['coating']], COATING_RHO[spec['coating']] * spec['coating_thickness_m'] * 100.0)
        e_beam = self.kv
        for key, ent in self._eds_weights(mode).items():
            sub = ent['sub']
            el = max(self._landing_ev(sub, spec if ent['on'] else None, key if key != 'holder' else None), 1.0)
            if key != 'holder' and ent is not None and el < e_beam:
                e_beam = min(e_beam, el)
            r, g, _ = _spectrum_cached(sub['substance_id'], round(el, 3), round(psi, 6), round(omega, 9), pt['noise_ev'],
                                       (spec['coating'], coat[1]) if (coat and ent['on']) else None)
            rates += ent['w'] * r
            gen += ent['w'] * g
            subs.add(sub['substance_id'])
        ip = self._probe_current()
        icr = float(rates.sum() * ip / E_CHARGE)
        live = math.exp(-icr * pt['tau_s'])
        return dict(rates=rates, gen=gen, icr=icr, live_fraction=max(live, 1e-6), psi=psi, omega=omega, wd=wd, subs=sorted(subs), e_landing=e_beam)

    def _eds_complete(self, rec, op):
        ex = self._eds_expected(op['mode'], op['process_time'])
        ip = self._probe_current()
        lam = ex['rates'] * ip / E_CHARGE * op['live_time_s']
        nrng = np.random.default_rng(self.rng.getrandbits(63))
        counts = nrng.poisson(np.maximum(lam, 0.0)).astype(int)
        real = op['live_time_s'] / ex['live_fraction']
        self.led['xray_generated'].add(ex['gen'] * ip / E_CHARGE * real)
        self.led['xray_detected'].add(float(counts.sum()))
        noise = EDS_PROCESS_TIMES[op['process_time']]['noise_ev']
        # the analysis software knows the displayed WD (objective focus) and the indicated tilt, not the true geometry
        tilt_ind = round(self.stage['tilt'] / math.radians(0.5)) * math.radians(0.5)
        psi_n, omega_n = eds_geometry(max(self.focus_wd, 1e-4), tilt_ind)
        rec.update(status='completed', finished_s=self.time_s, duane_hunt_limit_ev=duane_hunt_limit(counts, noise, self.kv), real_time_s=real,
                   dead_time_fraction=1.0 - ex['live_fraction'], input_count_rate_cps=ex['icr'], output_count_rate_cps=ex['icr'] * ex['live_fraction'],
                   takeoff_angle_rad=psi_n, solid_angle_sr=omega_n, working_distance_m=self.focus_wd, channel_width_ev=EDS_CHANNEL_EV, counts=counts.tolist(),
                   total_counts=int(counts.sum()), fwhm_mn_ka_ev=fwhm_ev(5898.75, noise), template_source=dict(library=LIBRARY_REL, substances=[]))

    def _spectrum_out(self, rec):
        out = {k: copy.deepcopy(v) for k, v in rec.items() if not k.startswith('_')}
        noise = EDS_PROCESS_TIMES[rec['process_time']]['noise_ev']
        for k, v in dict(duane_hunt_limit_ev=None, real_time_s=None, dead_time_fraction=None, input_count_rate_cps=None, output_count_rate_cps=None,
                         takeoff_angle_rad=None, solid_angle_sr=None, working_distance_m=None, channel_width_ev=EDS_CHANNEL_EV, counts=[], total_counts=0,
                         fwhm_mn_ka_ev=fwhm_ev(5898.75, noise), template_source=dict(library=LIBRARY_REL, substances=[])).items():
            out.setdefault(k, v)
        return out

    # ---------------------------------------------------------------- acquisitions
    def _stop_acq(self, status, code):
        op = self.acq
        if op is None:
            return
        rec = (self.images if op['kind'] == 'image' else self.spectra)[op['record_id']]
        rec.update(status=status, finished_s=self.time_s, error_code=code)
        op['phase'], op['reason'] = status, code
        self.acq = None
        self._event('acquisition', 'ACQ_' + status.upper(), f'采集{op["record_id"]}{"被取消" if status == "cancelled" else "失败"}（{code}）', op['record_id'])

    def _cancel_op(self, op):
        self._stop_acq('cancelled', 'USER_CANCEL')

    def _check_acq(self):
        op = self.acq
        if op is None:
            return
        if not self._beam_ready():
            self._stop_acq('failed', 'BEAM_OFF')
        elif not self._vac_ready():
            self._stop_acq('failed', 'VACUUM_LOST')
        elif self.time_s >= op['t0'] + op['duration'] - 1e-9:
            rec = (self.images if op['kind'] == 'image' else self.spectra)[op['record_id']]
            (self._image_complete if op['kind'] == 'image' else self._eds_complete)(rec, op)
            op['phase'] = 'completed'
            self.acq = None
            self._event('acquisition', 'ACQ_COMPLETED', f'采集{op["record_id"]}完成', op['record_id'])

    # ---------------------------------------------------------------- clock
    def step(self, dt_s):
        validate(number('s', 0.0, 86400.0), dt_s)
        raw = dt_s * 1e9 + self._clock_remainder_ns
        ns = round(raw)
        self._clock_remainder_ns = raw - ns
        if ns <= 0 or self.plant is None:
            if ns > 0:
                self.time_ns += ns
                self.revision += 1
            return
        end = self.time_ns + ns
        while self.time_ns < end:
            nxt = min(end, (self.time_ns // CYCLE_NS + 1) * CYCLE_NS)
            self._integrate((nxt - self.time_ns) / 1e9)
            self.time_ns = nxt
            if nxt % CYCLE_NS == 0:
                self._tick()
        self.revision += 1

    def _move(self, dt):
        def toward(cur, tgt, speed):
            d = speed * dt
            return min(tgt, cur + d) if cur < tgt else max(tgt, cur - d)
        self.door = toward(self.door, self.door_target, DOOR_SPEED_M_S)
        if self.power:
            self.stage['x'] = toward(self.stage['x'], self.stage_target['x'], STAGE_XY_SPEED_M_S)
            self.stage['y'] = toward(self.stage['y'], self.stage_target['y'], STAGE_XY_SPEED_M_S)
        self.stage['z'] = toward(self.stage['z'], self.stage_target['z'], STAGE_Z_SPEED_M_S)
        self.stage['tilt'] = toward(self.stage['tilt'], self.stage_target['tilt'], STAGE_TILT_SPEED)
        self.stage['rot'] = toward(self.stage['rot'], self.stage_target['rot'], STAGE_ROT_SPEED)

    def _integrate(self, dt):
        """Continuous motions between the cycles (door and stage move smoothly at any host step; exact for any chopping)."""
        self._move(dt)

    def _physics(self, dt):
        """Vacuum, pumps, filament and beam charge over one instrument cycle with the coefficients at the cycle start (the result does not
        depend on how the host chops its steps)."""
        self._vacuum_step(dt)
        if self._wet_exposed():
            self._spec()['_wet_exposed_s'] += dt
        if self.tmp_on and self.power:
            self.tmp_speed = min(1.0, self.tmp_speed + dt / TMP_SPINUP_S)
        else:
            self.tmp_speed = max(0.0, self.tmp_speed - dt / 30.0)
        self._beam_step(dt)
        if self.ht and self.power and not self.fil_burned:
            h0 = self.heat
            h1 = min(1.0, h0 + dt / SATURATION_S)
            tau = min(dt, (1.0 - h0) * SATURATION_S)
            on_s = (h0 + h1) / 2.0 * tau + (dt - tau)                  # exact integral of the linear heating ramp
            self.heat = h1
            self.fil_used_s += on_s * self._wear_factor()
            self.led['filament_on_time_s'].add(on_s)
        else:
            self.heat = max(0.0, self.heat - dt / 5.0)

    def _tick(self):
        self._physics(CYCLE_S)
        if not self.power:
            self.readings = None
            return
        t = self.time_s
        if abs(self.plant['mains_voltage_v'] - MAINS_NOMINAL_V) > MAINS_TOL * MAINS_NOMINAL_V + 1e-9:
            self._power_off()
            self.error = 'MAINS_FAULT'
            self._event('error', 'MAINS_FAULT', '供电电压超出AC 100V±10%：主机断路')
            return
        if self.state == 'booting' and t - self.state_t0 >= SOFTWARE_BOOT_S - 1e-9:
            self.state = 'ready'
            self._event('power', 'SOFTWARE_READY', '软件已启动')
        vs = self.vac_state
        if vs == 'roughing' and self.p_ch < CROSSOVER_PA and self.tmp_speed >= 1.0 - 1e-9:
            self.vac_state = 'hv_pumping' if self.vac_mode == 'HV' else 'lv_pumping'
            self.lv_ok_s, self.lv_int = 0.0, 0.0
        elif vs == 'hv_pumping' and self._p_gun() < HV_READY_PA:
            self.vac_state = 'hv_ready'
            self._event('vacuum', 'EVAC_COMPLETE', f'高真空就绪（抽气{t - (self.evac_t0 or t):.0f}s）')
            self.evac_t0 = None
        elif vs == 'hv_ready' and self._p_gun() > 2.0 * HV_READY_PA:
            self.vac_state = 'hv_pumping'
            self.evac_t0 = t
        if self.vac_state in ('lv_pumping', 'lv_ready'):
            err = (self.lv_set - self.p_ch) / self.lv_set
            if self.sem_ui['lv_auto']:                                         # Stop in the Pressure window holds the needle valve where it is
                self.lv_int = min(1.0, max(0.0, self.lv_int + err * CYCLE_S / LV_TI_S * LV_KP * 5.0))
                self.lv_valve = min(1.0, max(0.0, LV_KP * err + self.lv_int))
            ok = abs(err) < 0.05
            self.lv_ok_s = self.lv_ok_s + CYCLE_S if ok else 0.0
            if self.vac_state == 'lv_pumping' and self.lv_ok_s >= 10.0 and self._p_gun() < HV_READY_PA:
                self.vac_state = 'lv_ready'
                self._event('vacuum', 'LV_READY', f'低真空就绪（{self.lv_set:.0f}Pa）')
                self.evac_t0 = None
            elif self.vac_state == 'lv_ready' and abs(err) > 0.2:
                self.vac_state = 'lv_pumping'
        if self.evac_t0 is not None and not self._vac_ready() and t - self.evac_t0 > EVAC_LIMIT_S and not self.evac_timeout_raised:
            self.error = 'EVAC_TIMEOUT'
            self.evac_timeout_raised = True
            self._event('error', 'EVAC_TIMEOUT', '抽气10min未完成（门未关严、漏气或含水样品）')
        if self.vac_state == 'venting' and self.p_ch >= VENT_READY_PA:
            self._vent_complete()
        if self.ht and self._p_gun() > GUN_TRIP_PA:
            self.ht = False
            self.error = 'HT_TRIP'
            self._event('error', 'HT_TRIP', '电子枪真空变差，高压跳闸')
        if self.ht and self.fil_used_s >= self.fil_life_s:
            self.fil_burned = True
            self.ht = False
            self.error = 'FILAMENT_BURNOUT'
            self._event('error', 'FILAMENT_BURNOUT', '钨灯丝烧断：放气后更换灯丝')
        self._operation_tick(t)
        r = self.rng
        lv_on = self.vac_mode == 'LV' and self.vac_state in ('lv_pumping', 'lv_ready')
        self.readings = dict(pirani=min(1.2e5, max(0.5, self.p_ch * (1.0 + r.gauss(0.0, 0.03)))),
                             penning=min(0.1, max(1e-6, self._p_gun() * (1.0 + r.gauss(0.0, 0.10)))),
                             lv=self.p_ch * (1.0 + r.gauss(0.0, 0.005)) if lv_on else None,
                             emission=EMISSION_SAT_A * self.heat * self._emission_factor() * (1.0 + r.gauss(0.0, 0.01)) if (self.ht and not self.fil_burned) else None)
        self._check_acq()


@functools.lru_cache(maxsize=512)
def _spectrum_cached(substance_id, e_landing_ev, psi, omega, noise_ev, coating_key):
    sub = substance(substance_id)
    coat = (COATINGS[coating_key[0]], coating_key[1]) if coating_key else None
    rates, gen, emitted = spectrum_rates(sub['composition_mass_fraction'], e_landing_ev, psi, omega, noise_ev, coat)
    rates.setflags(write=False)
    return rates, gen, emitted


# ---------------------------------------------------------------- host session: identity filter, request binding, ownership
_CONTROL_GUARDED = {'power.set', 'power.key_toggle', 'system.shutdown', 'error.acknowledge', 'vacuum.evacuate', 'vacuum.vent', 'vacuum.toggle_lv',
                    'vacuum.set_lv_pressure', 'chamber.draw_out', 'chamber.draw_in', 'specimen.load', 'specimen.unload', 'stage.move_xy', 'stage.set_z',
                    'stage.set_tilt', 'stage.rotate', 'beam.set_voltage', 'beam.set_probe_current', 'beam.observation_on', 'beam.observation_off',
                    'gun.replace_filament', 'scan.set_magnification', 'optics.autofocus', 'optics.set_focus', 'optics.auto_stigmate',
                    'optics.auto_contrast_brightness', 'detector.select', 'image.acquire', 'eds.acquire'}
_DATA_GUARDED = {'image.result', 'eds.result', 'eds.quantify'}
ASYNC = {'image.acquire', 'eds.acquire'}
# hands on the hardware (sweep 2026-10-19): physical actions are never refused for ownership; the device reacts by its own conditions
_PHYSICAL = {'chamber.draw_out', 'chamber.draw_in', 'specimen.load', 'specimen.unload', 'gun.replace_filament', 'aperture.set_position', 'stage.set_z',
             'stage.set_tilt', 'stage.rotate',
             'power.set', 'power.key_toggle'}   # the MAIN POWER switch cuts the power: a power loss for the run, never refused (lead 10-19)
_CONTROL_GUARDED -= _PHYSICAL


class JeolJsmit200Session:
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
        meta = copy.deepcopy(export_jeol_jsmit200_contract_metadata())
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
        return dict(protocol_version=PROTOCOL_VERSION, asset_type_id=ASSET_TYPE_ID, asset_version=ASSET_VERSION, capabilities=caps, state_slots=slots)

    def _foreign(self, object_id):
        owner = self._host._owners.get(object_id)
        return owner is not None and owner != self._principal and self._host._principals.get(owner) == 'agent'

    def _foreign_acq(self):
        acq = self._host.acq
        return acq is not None and self._foreign(acq['record_id'])

    def _own_last(self, table):
        done = [k for k, r in table.items() if r['status'] == 'completed' and self._host._owners.get(k) == self._principal]
        return max(done, key=lambda k: table[k]['finished_s'], default=None)

    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id)
        h = self._host
        values = h.read(self._role)
        foreign = self._role == 'agent' and self._foreign_acq()
        values['acquisition.started_by_other_agent'] = foreign
        if foreign:
            values['acquisition.id'] = None
        values['result.last_image_id'] = self._own_last(h.images)
        values['result.last_spectrum_id'] = self._own_last(h.spectra)
        ids = list(values) if state_ids is None else state_ids
        if not isinstance(ids, list) or any(not isinstance(k, str) for k in ids):
            raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in values for k in ids):
            raise ActionRejected('ACCESS_DENIED')
        return dict(instance_id=instance_id, status='succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                    observations={k: dict(value=copy.deepcopy(values[k]), unit=SLOTS[k]['unit'],
                                          quality='unavailable' if values[k] is None else 'estimated' if SLOTS[k]['origin'] in ('sensor', 'derived') else 'valid',
                                          sample_time_s=(h.time_ns // CYCLE_NS) * CYCLE_S if k in _SAMPLING_READINGS else h.time_s,
                                          sample_window_s=CYCLE_S if k in _SAMPLING_READINGS else None) for k in ids})

    def _guard(self, capability_id, arguments):
        if self._role != 'agent':
            return
        h = self._host
        if capability_id in _CONTROL_GUARDED and self._foreign_acq():       # panel gestures: the host checks the command they invoke (panel_invoke)
            if capability_id in ('power.set', 'power.key_toggle') and not h.power:
                return
            raise ActionRejected('ACCESS_DENIED')
        if capability_id in _DATA_GUARDED:
            rid = arguments.get('image_id') or arguments.get('spectrum_id')
            if (rid in h.images or rid in h.spectra) and self._foreign(rid):     # unknown ids are reported by the runtime (UNKNOWN_RECORD)
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
            if not isinstance(capability_id, str) or capability_id not in ALL_CAPS:
                raise ActionRejected('UNKNOWN_CAPABILITY')
            if self._role == 'agent' and ALL_CAPS[capability_id]['visibility'] != 'agent':
                raise ActionRejected('ACCESS_DENIED')
            if not isinstance(arguments, dict):
                raise ActionRejected('INVALID_ARGUMENT')
            validate(ALL_CAPS[capability_id]['input_schema'], arguments)
            self._guard(capability_id, arguments)
            ops_before = set(h.operations)
            h._caller = self._principal                                         # commands run by a panel gesture act as this caller
            try:
                result = h._panel_entry(capability_id, arguments) if capability_id in PANEL_CAPS else h.invoke(capability_id, **arguments)
            finally:
                h._caller = None
            if capability_id == 'records.list' and self._role == 'agent':
                result = dict(images=[r for r in result['images'] if not self._foreign(r)], spectra=[r for r in result['spectra'] if not self._foreign(r)])
            if capability_id == 'events.read' and self._role == 'agent':
                result = dict(events=[{k: v for k, v in e.items() if k != '_rid'} for e in h.events
                                      if e['time_s'] >= arguments['since_s'] and not self._foreign(e.get('_rid'))], dropped=h.events_dropped)
            operation = result.get('operation_id') if capability_id in ASYNC else None
            if operation:
                h._owners[operation] = self._principal
                h._owners[h.operations[operation]['record_id']] = self._principal
            elif capability_id in PANEL_CAPS:
                for op_id in set(h.operations) - ops_before:                   # a photo / analysis started by a panel gesture belongs to the caller
                    h._owners[op_id] = self._principal
                    h._owners[h.operations[op_id]['record_id']] = self._principal
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
        raise ActionRejected('ACCESS_DENIED')                       # set points are written through capabilities

    def poll(self, operation_id):
        h = self._host
        if not isinstance(operation_id, str):
            raise ActionRejected('INVALID_ARGUMENT')
        op = h.operations.get(operation_id)
        if op is None:
            raise ActionRejected('UNKNOWN_OPERATION')
        if h._owners.get(operation_id) != self._principal:
            raise ActionRejected('ACCESS_DENIED')
        rec = (h.images if op['kind'] == 'image' else h.spectra)[op['record_id']]
        return dict(operation_id=operation_id, status=op['phase'], result=h._image_out(rec) if op['kind'] == 'image' else h._spectrum_out(rec))

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
_LAMPS = ['panel.vent_lamp', 'panel.vent_lamp_mode', 'panel.evac_lamp', 'panel.evac_lamp_mode', 'panel.lv_lamp', 'panel.lv_lamp_mode']
_GAUGES = ['vacuum.chamber_pressure_indicated_pa', 'vacuum.gun_pressure_indicated_pa', 'vacuum.lv_pressure_indicated_pa']
_ACQ = ['acquisition.id', 'acquisition.kind', 'acquisition.progress']
_BEAM_TRUTH = ['truth.emission_a', 'truth.probe_current_a', 'truth.probe_diameter_m']
_POWER_EFFECTS = ['power.enabled', 'system.state', 'system.error_code', 'vacuum.state', 'vacuum.evacuation_elapsed_s', 'beam.ht_on', 'beam.filament_state',
                  'beam.emission_indicated_a', 'truth.gas_load_pa_m3_s', 'truth.gun_pressure_pa'] + _BEAM_TRUTH + _LAMPS + _GAUGES + _ACQ
EFFECTS = {
    'plant.configure': ['plant.configured', 'truth.filament_life_s', 'truth.gas_load_pa_m3_s', 'truth.chamber_pressure_pa', 'truth.gun_pressure_pa'],
    'specimen.provide': ['specimen.bench_ids', 'truth.specimens'],
    'power.set': _POWER_EFFECTS, 'power.key_toggle': _POWER_EFFECTS, 'system.shutdown': ['system.state', 'beam.ht_on', 'beam.filament_state'] + _BEAM_TRUTH,
    'error.acknowledge': ['system.error_code'],
    'vacuum.evacuate': ['vacuum.state', 'vacuum.evacuation_elapsed_s', 'truth.gas_load_pa_m3_s', 'truth.gun_pressure_pa'] + _LAMPS,
    'vacuum.vent': ['vacuum.state', 'vacuum.evacuation_elapsed_s', 'beam.ht_on', 'beam.filament_state', 'stage.moving', 'truth.gas_load_pa_m3_s',
                    'truth.gun_pressure_pa'] + _BEAM_TRUTH + _LAMPS,
    'vacuum.toggle_lv': ['vacuum.mode', 'vacuum.state', 'vacuum.evacuation_elapsed_s', 'detector.selected', 'optics.acb_done', 'beam.ht_on', 'beam.filament_state',
                         'truth.gun_pressure_pa', 'truth.gas_load_pa_m3_s'] + _BEAM_TRUTH + _LAMPS + _GAUGES,
    'vacuum.set_lv_pressure': ['vacuum.lv_setpoint_pa'], 'chamber.draw_out': ['chamber.door_state'], 'chamber.draw_in': ['chamber.door_state'],
    'specimen.load': ['specimen.loaded_id', 'specimen.bench_ids', 'optics.acb_done', 'truth.specimens', 'truth.specimen_charge_c', 'truth.working_distance_m',
                      'truth.defocus_m'],
    'specimen.unload': ['specimen.loaded_id', 'specimen.bench_ids', 'optics.acb_done', 'truth.specimens', 'truth.specimen_charge_c', 'truth.working_distance_m',
                        'truth.defocus_m'],
    'stage.move_xy': ['stage.moving'], 'stage.set_z': ['stage.moving'], 'stage.set_tilt': ['stage.moving', 'optics.acb_done'], 'stage.rotate': ['stage.moving'],
    'beam.set_voltage': ['beam.accelerating_voltage_v', 'optics.acb_done', 'optics.astigmatism_corrected', 'truth.astigmatism_m', 'truth.probe_diameter_m'],
    'beam.set_probe_current': ['beam.probe_current_setpoint_a', 'optics.acb_done', 'truth.probe_current_a', 'truth.probe_diameter_m'],
    'beam.observation_on': ['beam.ht_on', 'beam.filament_state'] + _BEAM_TRUTH,
    'beam.observation_off': ['beam.ht_on', 'beam.filament_state'] + _BEAM_TRUTH + _ACQ,
    'gun.replace_filament': ['beam.filament_state', 'optics.astigmatism_corrected', 'system.error_code', 'truth.filament_on_time_s', 'truth.filament_life_s',
                             'truth.astigmatism_m'],
    'scan.set_magnification': ['scan.magnification', 'scan.field_width_m'], 'optics.autofocus': ['optics.focus_wd_m', 'truth.defocus_m', 'truth.probe_diameter_m'],
    'optics.set_focus': ['optics.focus_wd_m', 'truth.defocus_m', 'truth.probe_diameter_m'], 'optics.auto_stigmate': ['optics.astigmatism_corrected', 'truth.astigmatism_m'],
    'optics.auto_contrast_brightness': ['optics.acb_done'], 'detector.select': ['detector.selected', 'optics.acb_done'],
    'image.acquire': list(_ACQ), 'image.result': [], 'eds.acquire': list(_ACQ), 'eds.result': [], 'eds.quantify': [], 'records.list': [], 'events.read': [],
    'ledger.read': [], 'provenance.read': [], 'sim.step': ['clock.time_s'],
}
for _k, _v in EFFECTS.items():
    CAPS[_k]['effects'] = list(_v)
_TIMED = {'power.set', 'power.key_toggle', 'vacuum.evacuate', 'vacuum.vent', 'vacuum.toggle_lv', 'vacuum.set_lv_pressure', 'chamber.draw_out', 'chamber.draw_in',
          'stage.move_xy', 'stage.set_z', 'stage.set_tilt', 'stage.rotate', 'beam.observation_on', 'image.acquire', 'eds.acquire', 'sim.step'}
# _TIMED: calls that start a time-driven process of their own.  ON_STEP: what each call changes later, when the host clock advances (on_step
# effects) - the process a timed call starts and the consequences of other calls on running processes (beam off, landing energy, mains trip);
# set points never change on a step
_AX = {'x': ['stage.x_m', 'stage.x_indicated_m'], 'y': ['stage.y_m', 'stage.y_indicated_m'], 'z': ['stage.z_lift_m', 'stage.z_indicated_m'],
       'tilt': ['stage.tilt_rad', 'stage.tilt_indicated_rad'], 'rot': ['stage.rotation_rad', 'stage.rotation_indicated_rad']}
_MOTION = ['stage.moving', 'truth.working_distance_m', 'truth.defocus_m']
_P = ['truth.chamber_pressure_pa', 'truth.gun_pressure_pa', 'truth.gas_load_pa_m3_s']
_VENT_LAMPS, _EVAC_LAMPS, _LV_LAMPS = _LAMPS[0:2], _LAMPS[2:4], _LAMPS[4:6]
_HT_OFF_STEP = ['truth.filament_on_time_s', 'beam.emission_indicated_a']       # a call that switches the beam off stops the filament clock
_CHARGE = ['truth.specimen_charge_c']                                           # where, how hard and how long the beam lands changes the charging
_POWER_STEP = ['system.state', 'system.error_code', 'vacuum.state', 'vacuum.evacuation_elapsed_s', 'truth.tmp_speed_fraction', 'truth.filament_on_time_s'] \
    + _P + _LAMPS + _GAUGES + _CHARGE + _AX['x'] + _AX['y'] + _MOTION
ON_STEP = {
    'plant.configure': ['power.enabled', 'beam.ht_on', 'beam.filament_state'] + _POWER_STEP + _HT_OFF_STEP + _BEAM_TRUTH + _ACQ,
    'power.set': _POWER_STEP, 'power.key_toggle': _POWER_STEP,
    'system.shutdown': _HT_OFF_STEP + _CHARGE,
    'vacuum.evacuate': ['vacuum.state', 'vacuum.evacuation_elapsed_s', 'system.error_code', 'truth.tmp_speed_fraction'] + _P + _LAMPS + _GAUGES,
    'vacuum.vent': ['vacuum.state', 'truth.tmp_speed_fraction'] + _P + _LAMPS + _GAUGES + _HT_OFF_STEP + _AX['x'] + _AX['y'] + _MOTION + _CHARGE,
    'vacuum.toggle_lv': ['vacuum.state', 'vacuum.evacuation_elapsed_s', 'system.error_code'] + _P + _EVAC_LAMPS + _LV_LAMPS + _GAUGES + _HT_OFF_STEP + _CHARGE,
    'vacuum.set_lv_pressure': ['vacuum.state'] + _P + _EVAC_LAMPS + _LV_LAMPS + _GAUGES + _ACQ,
    'chamber.draw_out': ['chamber.door_position_m', 'chamber.door_state', 'vacuum.chamber_pressure_indicated_pa'] + _P,
    'chamber.draw_in': ['chamber.door_position_m', 'chamber.door_state', 'vacuum.state', 'vacuum.evacuation_elapsed_s', 'system.error_code'] + _P + _EVAC_LAMPS
                       + _LV_LAMPS + _GAUGES,
    'stage.move_xy': _AX['x'] + _AX['y'] + _MOTION + _CHARGE, 'stage.set_z': _AX['z'] + _MOTION, 'stage.set_tilt': _AX['tilt'] + _MOTION + _CHARGE,
    'stage.rotate': _AX['rot'] + _MOTION + _CHARGE,
    'beam.set_voltage': _CHARGE, 'beam.set_probe_current': _CHARGE, 'scan.set_magnification': _CHARGE,
    'beam.observation_on': ['beam.ht_on', 'beam.filament_state', 'beam.emission_indicated_a', 'system.error_code', 'truth.filament_on_time_s'] + _BEAM_TRUTH + _CHARGE,
    'beam.observation_off': _HT_OFF_STEP + _CHARGE,
    'image.acquire': _ACQ + ['result.last_image_id', 'acquisition.started_by_other_agent'],
    'eds.acquire': _ACQ + ['result.last_spectrum_id', 'acquisition.started_by_other_agent'] + _CHARGE,
}
ON_STEP = {k: list(dict.fromkeys(v)) for k, v in ON_STEP.items()}
ON_STEP['sim.step'] = ['clock.time_s'] + sorted(set(sum(ON_STEP.values(), [])) | set(_POWER_EFFECTS) - {'clock.time_s'})
ON_STEP_ZH = {
    'plant.configure': '重新声明的条件在下一仪器周期起作用：供电超出AC 100V±10%时主机断路（高压、泵与X/Y电机停止，采集失败，灯丝不再计时）；室温、湿度与放气气体改变之后的抽气与放气过程',
    'power.set': '开机：约60s软件加载；样品室留在真空时自动启动抽气；断电后阀门关闭，样品室缓慢漏气，涡轮泵减速，束流停止（灯丝工作时间不再累计、样品不再荷电），X/Y电机停在断电时的位置（Z/T/R为手动轴）',
    'system.shutdown': '高压关闭后灯丝工作时间停止累计、发射读数在下一周期消失，绝缘样品不再荷电',
    'vacuum.evacuate': '粗抽、涡轮泵升速与高真空（或LV）抽气，含10min超时', 'vacuum.toggle_lv': '切换后按新模式抽气或针阀调压；高压已关，灯丝工作时间停止累计、发射读数在下一周期消失；LV下绝缘样品电荷经气体离子释放',
    'vacuum.set_lv_pressure': '针阀PI调到新压力：偏差超过20%时LV状态回到lv_pumping（EVAC/LV灯闪烁），运行中的采集以VACUUM_LOST失败；10–100Pa内电子枪真空保持在跳闸限以下',
    'vacuum.vent': '放气到大气压；高压已关，灯丝工作时间停止累计、发射读数在下一周期消失；X（与不抬高样品时的Y）回到换样位置，Y移动在倾斜时改变工作距离；'
                   '束流停止后样品不再荷电',
    'chamber.draw_out': '抽拉门按速度移动到拉出位置；门缝使样品室与室内空气相通（放气状态下压力停在大气压附近）',
    'chamber.draw_in': '抽拉门按速度移动到关闭位置；关严前门缝仍与室内空气相通；已按EVAC时关严后抽气才能完成（不再因门未关超时），含水样品关门后开始放气并逐渐变干',
    'stage.move_xy': 'X/Y电机按速度移动；倾斜时Y移动改变真实工作距离与离焦；束流开着时视场移到别的相，绝缘样品的荷电随之变化',
    'stage.set_z': 'Z手动轴按速度移动，改变真实工作距离与离焦',
    'stage.set_tilt': '倾斜按速度变化，改变束斑处的工作距离与离焦；入射角改变绝缘样品的荷电', 'stage.rotate': '旋转按速度变化，束斑落点随之移动，绝缘样品的荷电随之变化',
    'beam.set_voltage': '束流开着时，新的着陆能量改变绝缘样品的荷电过程', 'beam.set_probe_current': '束流开着时，新的探针电流改变绝缘样品的荷电速率',
    'scan.set_magnification': '束流开着时，扫描视场改变受照的相与电荷分布',
    'beam.observation_on': '灯丝加热到饱和、发射与探针电流上升、灯丝工作时间累计；束流照射使未镀膜绝缘样品荷电；灯丝烧断时高压关闭并记错误',
    'beam.observation_off': '高压关闭后灯丝工作时间停止累计、发射读数在下一周期消失，绝缘样品不再荷电',
    'image.acquire': '帧时间内进度推进，结束时生成图像记录', 'eds.acquire': 'EDS实时间内进度推进，结束时生成谱记录；点分析时束斑固定在一点照射',
    'sim.step': '统一宿主时钟推进全部过程：真空、泵、灯丝、电荷、门与样品台运动、采集，以及供电异常时的断路',
}
ON_STEP_ZH['power.key_toggle'] = ON_STEP_ZH['power.set']
# how each slot is sampled when read
_SAMPLING_CONTINUOUS = {'clock.time_s', 'vacuum.evacuation_elapsed_s', 'panel.vent_lamp', 'panel.evac_lamp', 'panel.lv_lamp', 'chamber.door_state',
                        'chamber.door_position_m', 'stage.moving', 'acquisition.progress', 'truth.working_distance_m', 'truth.defocus_m', 'truth.gas_load_pa_m3_s'} \
    | set(sum(_AX.values(), []))
_SAMPLING_READINGS = {'vacuum.chamber_pressure_indicated_pa': 'Pirani, 3 % relative noise', 'vacuum.gun_pressure_indicated_pa': 'Penning, 10 % relative noise',
                      'vacuum.lv_pressure_indicated_pa': 'LV pressure, 0.5 % relative noise', 'beam.emission_indicated_a': 'emission meter, 1 % relative noise'}
_SAMPLING_CYCLE = {'system.state', 'system.error_code', 'power.enabled', 'vacuum.state', 'panel.vent_lamp_mode', 'panel.evac_lamp_mode', 'panel.lv_lamp_mode',
                   'beam.ht_on', 'beam.filament_state', 'acquisition.id', 'acquisition.kind', 'acquisition.started_by_other_agent', 'result.last_image_id',
                   'result.last_spectrum_id', 'truth.chamber_pressure_pa', 'truth.gun_pressure_pa', 'truth.tmp_speed_fraction', 'truth.filament_on_time_s',
                   'truth.emission_a', 'truth.probe_current_a', 'truth.probe_diameter_m', 'truth.specimen_charge_c'}
_SAMPLING_COMMAND = set(SLOTS) - _SAMPLING_CONTINUOUS - set(_SAMPLING_READINGS) - _SAMPLING_CYCLE


def _sampling_text(k):
    if k in _SAMPLING_CONTINUOUS:
        return ('Instantaneous value at the read time: door and stage motions are integrated continuously between the 1 s instrument cycles (encoder readings '
                'rounded to their display resolution); lamp blinking, elapsed times, outgassing and acquisition progress follow the simulation clock.')
    if k in _SAMPLING_READINGS:
        return f'Reading sampled at the latest 1 s instrument cycle and held until the next one ({_SAMPLING_READINGS[k]}); null while unpowered or not applicable.'
    if k in _SAMPLING_CYCLE:
        return ('Process state advanced on the 1 s instrument cycle (vacuum, pumps, filament, charge, acquisition completion, faults); commands that set it take '
                'effect at once.')
    return 'Changes only when a command is accepted (set point, configuration or bookkeeping); no time dynamics.'
_READY = ['power.enabled', 'system.state', 'system.error_code', 'acquisition.id']
_BEAM = _READY + ['vacuum.state', 'beam.ht_on', 'beam.filament_state']
PRECONDITIONS = {
    'plant.configure': ('无；随机数种子只在首次声明时生效', []),
    'specimen.provide': ('已声明实验室条件；ID不重复（最多20个）；物质在模板库中；面积分数之和为1；镀膜与厚度一致（无镀膜厚度为0）；直径≤150mm、高度≤48mm（厂家）',
                         ['plant.configured', 'specimen.bench_ids']),
    'power.set': ('已声明实验室条件；开机时供电须在AC 100V±10%内（厂家安装要求）', ['plant.configured', 'power.enabled']),
    'power.key_toggle': ('已声明实验室条件；开机时供电须在AC 100V±10%内', ['plant.configured', 'power.enabled']),
    'system.shutdown': ('通电、软件已启动、无采集', ['power.enabled', 'system.state', 'acquisition.id']),
    'error.acknowledge': ('通电且有待确认错误', ['power.enabled', 'system.error_code']),
    'vacuum.evacuate': ('通电、软件已启动、无待确认错误与采集；已在抽气或就绪时不重复启动', _READY + ['vacuum.state']),
    'vacuum.vent': ('通电、软件已启动、无待确认错误与采集；高压开着时自动关闭', _READY + ['vacuum.state', 'beam.ht_on']),
    'vacuum.toggle_lv': ('通电、软件已启动、无待确认错误与采集（观察中会先关闭高压）', _READY + ['vacuum.mode', 'vacuum.state', 'detector.selected', 'beam.ht_on']),
    'vacuum.set_lv_pressure': ('通电', ['power.enabled']),
    'chamber.draw_out': ('样品室已放气到大气压（vacuum.state=vented）、无采集', ['vacuum.state', 'acquisition.id', 'chamber.door_state']),
    'chamber.draw_in': ('Z、倾斜与Y从当前到目标的扫掠范围内样品（或样品座）最高点都在极靴下4mm以外、无采集', ['stage.z_indicated_m', 'stage.tilt_indicated_rad', 'specimen.loaded_id',
                                                                                   'acquisition.id']),
    'specimen.load': ('门完全拉出且未下令推回；样品在实验台上；样品座空着', ['chamber.door_state', 'specimen.bench_ids', 'specimen.loaded_id']),
    'specimen.unload': ('门完全拉出且未下令推回；样品座上有样品', ['chamber.door_state', 'specimen.loaded_id']),
    'stage.move_xy': ('通电、软件已启动、无待确认错误与采集；门关闭；Y运动扫掠范围内样品最高点不进入极靴下4mm以内或不高于当前位置',
                      _READY + ['chamber.door_state', 'stage.y_indicated_m', 'stage.z_indicated_m', 'stage.tilt_indicated_rad', 'specimen.loaded_id']),
    'stage.set_z': ('门完全拉出且未下令推回，或Z从当前到目标（与正在进行的倾斜/Y运动）的扫掠范围内样品最高点不进入极靴下4mm以内或不高于当前位置（降低总是允许）',
                    ['stage.z_indicated_m', 'stage.tilt_indicated_rad', 'stage.y_indicated_m', 'specimen.loaded_id']),
    'stage.set_tilt': ('门完全拉出且未下令推回，或倾斜从当前到目标（含边缘最高的中间角度，与正在进行的Z/Y运动）的扫掠范围内样品最高点不进入极靴下4mm以内或不高于当前位置',
                       ['stage.z_indicated_m', 'stage.tilt_indicated_rad', 'stage.y_indicated_m', 'specimen.loaded_id']),
    'stage.rotate': ('已声明实验室条件（手动旋钮，采集中转动不被拒）', ['plant.configured']),
    'beam.set_voltage': ('通电、软件已启动、无待确认错误与采集', list(_READY)), 'beam.set_probe_current': ('通电、软件已启动、无待确认错误与采集', list(_READY)),
    'beam.observation_on': ('通电、软件已启动、无待确认错误；真空就绪（高真空或低真空；真空就绪即门已关闭）；灯丝未烧断', _READY + ['vacuum.state', 'beam.filament_state']),
    'beam.observation_off': ('通电', ['power.enabled']),
    'gun.replace_filament': ('样品室已放气到大气压（放气时高压已关闭）', ['vacuum.state', 'vacuum.chamber_pressure_indicated_pa']),
    'scan.set_magnification': ('通电、软件已启动、无待确认错误与采集', list(_READY)),
    'optics.autofocus': ('通电、软件已启动、无待确认错误与采集；真空就绪、灯丝饱和（观察中）', list(_BEAM)),
    'optics.set_focus': ('通电、软件已启动、无待确认错误与采集', list(_READY)),
    'optics.auto_stigmate': ('通电、软件已启动、无待确认错误与采集；真空就绪、灯丝饱和', list(_BEAM)),
    'optics.auto_contrast_brightness': ('通电、软件已启动、无待确认错误与采集；真空就绪、灯丝饱和', list(_BEAM)),
    'detector.select': ('通电、软件已启动、无待确认错误与采集；LV模式不能选SED', _READY + ['vacuum.mode']),
    'image.acquire': ('通电、软件已启动、无待确认错误与采集；真空就绪、灯丝饱和；记录未满200条', list(_BEAM)),
    'eds.acquire': ('通电、软件已启动、无待确认错误与采集；真空就绪、灯丝饱和；预计死时间不超过90%；记录未满200条', _BEAM + ['beam.probe_current_setpoint_a']),
    'image.result': ('记录存在且不属于另一个agent', []), 'eds.result': ('记录存在且不属于另一个agent', []),
    'eds.quantify': ('谱存在、已完成且不属于另一个agent；给定元素须在库中且Z≥4；自动识峰须找到显著峰', []),
    'records.list': ('无', []), 'events.read': ('无', []), 'ledger.read': ('无', []), 'provenance.read': ('无', []), 'sim.step': ('无', []),
}
ERROR_ZH = {
    'INVALID_ARGUMENT': '参数不符合输入结构、类型或范围', 'ACCESS_DENIED': '该角色无权调用，或当前采集/记录属于另一个agent', 'REQUEST_CONFLICT': '同一request_id已用于不同的请求',
    'UNKNOWN_INSTANCE': '实例ID不存在', 'UNSUPPORTED': '本配置不提供该功能（理由见function_zh）', 'UNKNOWN_CAPABILITY': '能力ID不存在', 'UNKNOWN_OPERATION': '操作ID不存在',
    'NOT_RUNNING': '操作不在进行中', 'PLANT_REQUIRED': '尚未声明实验室条件', 'POWER_OFF': '未通电（或断电中止了采集）', 'NOT_READY': '软件加载中或已软件关机',
    'ERROR_PENDING': '有待确认的错误（先确认）', 'NO_ERROR': '没有待确认的错误', 'MAINS_OUT_OF_RANGE': '供电电压超出AC 100V±10%（厂家安装要求）',
    'ACQUISITION_RUNNING': '有图像或EDS采集正在进行', 'VACUUM_NOT_READY': '真空未就绪（高真空或低真空）', 'DOOR_OPEN': '样品室门没有关好',
    'BEAM_NOT_READY': '电子束未就绪：未开始观察或灯丝仍在加热', 'CHAMBER_UNDER_VACUUM': '样品室在真空下，门被大气压压住，须先放气', 'DOOR_NOT_OPEN': '门未完全拉出（或已下令推回）',
    'STAGE_COLLISION_RISK': '运动会使样品最高点进入极靴下4mm以内（离下挂3.5mm的背散射探测器不足0.5mm）并高于当前位置（先降低Z或减小倾斜）', 'SPECIMEN_EXISTS': '样品ID已存在或实验台已有20个样品',
    'UNKNOWN_SUBSTANCE': '物质不在SEM/EDS模板库中', 'AREA_FRACTIONS_INVALID': '各相面积分数须为正且之和为1', 'COATING_INVALID': '镀膜种类与厚度不一致',
    'SPECIMEN_TOO_LARGE': '样品超过直径150mm或高度48mm（厂家）', 'UNKNOWN_SPECIMEN': '样品ID不存在', 'SPECIMEN_NOT_ON_BENCH': '样品不在实验台上',
    'HOLDER_OCCUPIED': '样品座上已有样品', 'NO_SPECIMEN': '样品座上没有样品', 'FILAMENT_BURNED_OUT': '灯丝已烧断（放气后更换）', 'VENT_REQUIRED': '须先放气到大气压',
    'HT_ON': '电子枪高压开着', 'DETECTOR_NOT_AVAILABLE': 'LV模式不能用SED（E-T探测器高压栅会放电），请用BED', 'RECORDS_FULL': '记录已满（200条）',
    'UNKNOWN_RECORD': '图像或谱记录不存在', 'NOT_COMPLETED': '谱尚未完成', 'UNKNOWN_ELEMENT': '元素不在库中或低于Be（EDS范围Be–U）', 'NO_PEAKS': '自动识峰没有找到显著峰',
    'QUANT_FAILED': '没有可定量的谱线族（过压比不足或超出能量范围）', 'USER_CANCEL': '采集被取消', 'BEAM_OFF': '采集中电子束关闭（停止观察、灯丝烧断或高压跳闸）',
    'VACUUM_LOST': '采集中真空失去就绪', 'EVAC_TIMEOUT': '抽气10min未完成（JSM-IT300手册同系列）', 'FILAMENT_BURNOUT': '钨灯丝烧断',
    'HT_TRIP': '电子枪真空变差，高压跳闸', 'MAINS_FAULT': '供电电压异常，主机断路',
    'COUNT_RATE_TOO_HIGH': 'EDS死时间会超过90%（输入计数率过高），请降低探针电流',
    'STAGE_DRIVER_OVERHEAT': '样品台电机驱动过热（Error ID 0200–0205）：各轴电机不能动作，关电源并联系JEOL',
    'STAGE_MECHANICAL_LIMIT': '超出样品台机械限位（Error ID 0240–0245）：关电源并联系JEOL',
    'STAGE_MOTOR_FAILURE': '电机故障、位置读不出（Error ID 0250–0255）：按VENT放气后执行样品台初始化（全部轴）',
    'STAGE_LENS_CONTACT': '检测到样品台碰到物镜（Error ID 0260）：放气到大气压并检查样品、物镜与探测器有无损伤',
    'GUN_CHAMBER_OPEN': '电子枪室未关（Error ID 0331）：关好电子枪室后再按EVAC',
    'LV_PRESSURE_ADJUST_FAILED': '自动压力调节失败（Error ID 0351）：用手动调节按钮或重做自动设定',
    'FILAMENT_FAILURE': '灯丝故障（L.C.≥150µA，晶须）：取下灯丝检查晶须，必要时更换',
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
_R = ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ERROR_PENDING', 'ACQUISITION_RUNNING']
_B = _R + ['VACUUM_NOT_READY', 'BEAM_NOT_READY']            # vacuum ready implies the door is closed
CAP_ERRORS = {
    'plant.configure': [], 'specimen.provide': ['PLANT_REQUIRED', 'SPECIMEN_EXISTS', 'UNKNOWN_SUBSTANCE', 'AREA_FRACTIONS_INVALID', 'COATING_INVALID', 'SPECIMEN_TOO_LARGE'],
    'power.set': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'], 'power.key_toggle': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'],
    'system.shutdown': ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ACQUISITION_RUNNING'], 'error.acknowledge': ['PLANT_REQUIRED', 'POWER_OFF', 'NO_ERROR'],
    'vacuum.evacuate': _R + ['GUN_CHAMBER_OPEN'], 'vacuum.vent': list(_R), 'vacuum.toggle_lv': list(_R), 'vacuum.set_lv_pressure': ['PLANT_REQUIRED', 'POWER_OFF'],
    'chamber.draw_out': ['PLANT_REQUIRED', 'ACQUISITION_RUNNING', 'CHAMBER_UNDER_VACUUM'], 'chamber.draw_in': ['PLANT_REQUIRED', 'ACQUISITION_RUNNING', 'STAGE_COLLISION_RISK'],
    'specimen.load': ['PLANT_REQUIRED', 'DOOR_NOT_OPEN', 'UNKNOWN_SPECIMEN', 'SPECIMEN_NOT_ON_BENCH', 'HOLDER_OCCUPIED'],
    'specimen.unload': ['PLANT_REQUIRED', 'DOOR_NOT_OPEN', 'NO_SPECIMEN'],
    'stage.move_xy': _R + ['DOOR_OPEN', 'STAGE_COLLISION_RISK', 'STAGE_DRIVER_OVERHEAT', 'STAGE_MECHANICAL_LIMIT', 'STAGE_MOTOR_FAILURE', 'STAGE_LENS_CONTACT'],
    'stage.set_z': ['PLANT_REQUIRED', 'STAGE_COLLISION_RISK'], 'stage.set_tilt': ['PLANT_REQUIRED', 'STAGE_COLLISION_RISK'],
    'stage.rotate': ['PLANT_REQUIRED'], 'beam.set_voltage': list(_R), 'beam.set_probe_current': list(_R),
    'beam.observation_on': ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY', 'ERROR_PENDING', 'VACUUM_NOT_READY', 'FILAMENT_BURNED_OUT', 'FILAMENT_FAILURE'],
    'beam.observation_off': ['PLANT_REQUIRED', 'POWER_OFF'], 'gun.replace_filament': ['PLANT_REQUIRED', 'VENT_REQUIRED'],
    'scan.set_magnification': list(_R), 'optics.autofocus': list(_B), 'optics.set_focus': list(_R),
    'optics.auto_stigmate': list(_B), 'optics.auto_contrast_brightness': list(_B),
    'detector.select': _R + ['DETECTOR_NOT_AVAILABLE'],
    'image.acquire': _B + ['RECORDS_FULL', 'USER_CANCEL', 'BEAM_OFF', 'VACUUM_LOST', 'POWER_OFF'],
    'eds.acquire': _B + ['RECORDS_FULL', 'COUNT_RATE_TOO_HIGH', 'USER_CANCEL', 'BEAM_OFF', 'VACUUM_LOST', 'POWER_OFF'],
    'image.result': ['UNKNOWN_RECORD'], 'eds.result': ['UNKNOWN_RECORD'], 'eds.quantify': ['UNKNOWN_RECORD', 'NOT_COMPLETED', 'UNKNOWN_ELEMENT', 'NO_PEAKS', 'QUANT_FAILED'],
}
_PARTIAL = {'USER_CANCEL', 'BEAM_OFF', 'VACUUM_LOST', 'POWER_OFF'}
CAP_ERROR_ZH = {('chamber.draw_in', 'STAGE_COLLISION_RISK'): '推回后（或推回途中Z/T/Y到达目标时）样品最高点会进入极靴下4mm以内：推门按整个扫掠范围严格判定（先降低Z或减小倾斜）',
                ('stage.move_xy', 'STAGE_COLLISION_RISK'): '移动会使样品最高点进入极靴下4mm以内并高于当前位置：按真实样品判定，或按所选样品座外廓与输入的样品高度（软件限位，'
                                                           '手册2-7）判定（先降低Z或减小倾斜，或改正样品座/高度输入）'}
MODEL_NONE_ZH = {
    'plant.configure': '只声明宿主实验室条件（室温、湿度、供电、放气气体、噪声种子），没有可见模型变化',
    'specimen.provide': '只在过程层实验台上增加样品；场景没有样品几何（样品位由接口点specimen_surface表示）',
    'system.shutdown': '软件关机只关闭高压与灯丝并记录状态；这些状态没有场景显示（kV/倍数/WD行显示设定值，Pa读数与开关灯随真空与供电），没有可见模型变化', 'error.acknowledge': '只清除错误状态，没有可见模型变化',
    'vacuum.set_lv_pressure': '只写LV压力设定；屏上Pa读数随step显示实际压力', 'specimen.load': '样品只进入过程层；场景没有样品几何',
    'specimen.unload': '样品只离开过程层样品座；场景没有样品几何', 'beam.set_probe_current': '只写探针电流设定，没有可见模型变化',
    'beam.observation_on': '高压与灯丝状态没有可见几何；图像与读数随step变化', 'beam.observation_off': '同上，没有可见几何变化',
    'gun.replace_filament': '过程层更换灯丝；场景中枪罩不打开（没有维护动画）', 'optics.auto_stigmate': '只改像散状态，没有可见模型变化',
    'optics.auto_contrast_brightness': '只改探测器增益/偏置，没有可见模型变化', 'detector.select': '只改成像探测器，没有可见模型变化',
    'image.acquire': '采集只在过程层生成图像记录；屏幕贴图不随图像内容变化', 'image.result': '只读记录', 'eds.acquire': '采谱只在过程层生成谱记录，没有可见模型变化',
    'eds.result': '只读记录', 'eds.quantify': '分析计算，没有可见模型变化', 'records.list': '只读列表', 'events.read': '只读事件', 'ledger.read': '只读账目（evaluator）',
    'provenance.read': '只读来源记录（evaluator）',
}
_GUARDED = _CONTROL_GUARDED | _DATA_GUARDED
RESOURCES = {'image.acquire': ['electron_beam', 'scan_generator', 'detectors', 'stage', 'host_clock'], 'eds.acquire': ['electron_beam', 'eds_detector', 'stage', 'host_clock'],
             'vacuum.evacuate': ['vacuum_system'], 'vacuum.vent': ['vacuum_system', 'electron_gun', 'stage'], 'vacuum.toggle_lv': ['vacuum_system', 'detectors'],
             'vacuum.set_lv_pressure': ['vacuum_system'], 'chamber.draw_out': ['chamber_door', 'stage'], 'chamber.draw_in': ['chamber_door', 'stage'],
             'specimen.load': ['specimen_holder'], 'specimen.unload': ['specimen_holder'], 'stage.move_xy': ['stage'], 'stage.set_z': ['stage'], 'stage.set_tilt': ['stage'],
             'stage.rotate': ['stage'], 'beam.set_voltage': ['electron_gun'], 'beam.set_probe_current': ['electron_gun'], 'beam.observation_on': ['electron_gun'],
             'beam.observation_off': ['electron_gun'], 'gun.replace_filament': ['electron_gun'], 'scan.set_magnification': ['scan_generator'],
             'optics.autofocus': ['electron_beam', 'objective_lens'], 'optics.set_focus': ['objective_lens'], 'optics.auto_stigmate': ['electron_beam', 'objective_lens'],
             'optics.auto_contrast_brightness': ['electron_beam', 'detectors'], 'detector.select': ['detectors'], 'power.set': ['mains', 'vacuum_system', 'electron_gun'],
             'power.key_toggle': ['mains', 'vacuum_system', 'electron_gun'], 'system.shutdown': ['electron_gun'], 'plant.configure': ['room', 'mains', 'vent_gas'],
             'specimen.provide': ['bench'], 'sim.step': ['host_clock'], 'eds.quantify': ['records'], 'image.result': ['records'], 'eds.result': ['records']}
_ROBOT = '实体操作为宿主/机器人技能的过程层抽象，没有接触或机器人验证；门与样品台关节由过程状态镜像，样品没有几何。'
_HAND_SCAN = ('Z/T/R是手动旋钮：采集中转动不被拒（真机上没有东西阻止手转，1.2.0清扫）；扫描继续，图像/谱按扫描结束时的样品台位置、工作距离与离焦计算'
              '（简化假设：真实图像在转动的行上出现错位或模糊，这里整幅按结束时的状态）。')
CAP_LIMITS = {
    'plant.configure': ['宿主声明的实验室条件；供电波动、振动、杂散磁场与室温漂移对图像的影响不建模（供电只按±10%判断开机与断路）。'],
    'specimen.provide': ['样品用相面积分数与晶粒尺寸的Voronoi相图表示；形貌只分抛光平面/颗粒/粗糙断口三类（每个晶粒一个随机倾斜）；含水只影响放气量：只在样品处于关门且未放气的样品室中时放出水汽，放气量按累计暴露时间指数衰减（τ=30min，工程值），门拉出或放气时不放气也不变干，放气后不会重新变湿。'],
    'power.set': ['软件加载60s为工程值；未软件关机就断电只记警告，记录保留。'],
    'power.key_toggle': ['MAIN POWER为三位钥匙开关，本资产在Matterix中按一次按压切换的弹簧键处理。'],
    'vacuum.evacuate': ['真空为单体积集总模型：机械泵有效抽速、涡轮泵抽速与起转时间、放气与漏率均为工程值；抽气约2min（就绪时间主要由粗抽与涡轮泵起转决定；干燥氮气放气后放气量更小，'
                        '达到的枪真空更好但就绪时间几乎不变），10min超时依同系列手册。'],
    'vacuum.vent': ['放气时间约1–2min（放气阀流导工程值）；X回交换位，Y在不抬高样品到极靴附近时回位（否则留在原处并记Y_RETURN_SKIPPED），Z/T/R为手动轴不自动回位。'],
    'vacuum.toggle_lv': ['LV：机械泵经孔板抽样品室、针阀PI控制进气，电子枪经压差光阑保持高真空；各流导为工程值；切换模式时自动关闭高压（工程选择），故电子枪真空不会越过跳闸限，'
                         'HT_TRIP联锁在公开操作序列中通常不触发。'],
    'chamber.draw_out': [_ROBOT, '门拉出行程0.20m为工程值；断电时阀门关闭，未到大气压的样品室不能开门。'],
    'chamber.draw_in': [_ROBOT, '碰撞判据：Z、倾斜与Y扫掠范围内样品（或样品座）顶部边缘离极靴至少4mm（离下挂3.5mm的BED至少0.5mm）；未建模样品侧面与EDS/SED探头的碰撞。'],
    'specimen.load': [_ROBOT], 'specimen.unload': [_ROBOT],
    'stage.move_xy': ['X/Y电机速度3mm/s为工程值；到位精度按编码器0.1µm显示，未建模背隙；Y沿倾斜摇篮移动并计入碰撞扫掠检查。'],
    'stage.set_z': ['Z为手动旋钮的单圈抽象（Matterix中旋钮转角与升降双向联动），手转速度2mm/s为工程值；碰撞按扫掠范围保守检查（Z与T运动独立推进）。', _HAND_SCAN],
    'stage.set_tilt': ['倾斜轴取过样品座顶面中心的X轴（样品顶面不在倾斜轴上，高度h的样品倾斜后束斑落在顶面距中心h·tanθ处，工作距离=48mm−升降−h/cosθ）；'
                       '束斑落出顶面时按落到样品座平面处理，未建模样品侧壁；倾斜方向朝门为工程选择；手转速度10°/s为工程值。', _HAND_SCAN],
    'stage.rotate': ['R为手动旋转，速度30°/s为工程值。', _HAND_SCAN],
    'beam.set_voltage': ['电压步进按同系列JSM-IT300手册（≤3kV 0.1kV、>3kV 1kV）；换电压后像散随机重置（工程）。'],
    'beam.set_probe_current': ['探针电流直接取设定值（聚光镜/光阑的具体档位未建模）；束斑按最优孔径角计算。'],
    'beam.observation_on': ['灯丝10s到饱和（设施手册约10s成像）；发射电流100µA与寿命均值300h（Weibull，设施手册“每几百小时”更换）为工程值；自动对中视为瞬时完成。'],
    'gun.replace_filament': [_ROBOT, '新灯丝寿命按Weibull(k=3，均值300h)抽样（工程值）。'],
    'scan.set_magnification': ['放大倍数按128mm打印宽度定义（厂家）；显示器上的“显示倍数”（×14–839,724）未单独建模。'],
    'optics.autofocus': ['自动聚焦误差为景深的1/8（1σ，工程值）；不模拟聚焦失败。'],
    'optics.auto_contrast_brightness': ['按当前视场实时帧的直方图（信号范围±2.5σ噪声）映射到灰度20–235；换条件后需重做，否则图像可能过暗或饱和；放大器增益上限未建模（由噪声宽度限定）。'],
    'image.acquire': ['图像只返回按灰度分割的区域统计与32×24缩略图（不给出物质名与每电子产额）；信号模型（SE/BSE产额、收集效率、噪声、荷电、LV散射）为文献经验式加工程参数，见mechanisms。',
                      '区域分割只比较各相平均灰度与阈值√((3σ噪声)²+(2σ纹理)²)，不做空间连通分析；粗糙/颗粒样品的相内形貌纹理会使成分相近的相并为一区；TOPO差分信号的衬度为null。'],
    'eds.acquire': ['谱为物理模型生成的泊松计数（Bethe电离截面、Joy–Luo阻止本领、Philibert吸收、Kramers连续谱、Fano分辨率、逃逸峰、死时间）；荧光校正、和峰与不完全电荷收集拖尾未建模；'
                    'Duane–Hunt限由谱尾端连续谱的泊松最大似然直线外推估计（1nA、30s约±30–60eV；计数只有几万时约±几百eV；不超过加速电压；加速电压超过约20.2keV（谱尾被0–20.48keV截断）'
                    '或尾端计数不足1000时为null）；记录的出射角/立体角按显示WD与倾斜读数计算，'
                    '与真实几何不一致时定量出现相应偏差。'],
    'eds.quantify': ['无标样ZAF：峰族面积加权最小二乘，生成项取Green–Cosslett型(U0−1)^1.67/[(Z/A)·ln(1.166(E0+Ec)/2J)]、背散射修正1−0.4η(1−1/U0)与完整Philibert吸收（含h项），'
                     '迭代并归一化；与生成模型（Bethe积分、Myklebust背散射）的差异即方法偏差（库中物质15/20kV给出全部元素时中位约5%相对：重元素二元体系约±3%；氧化物与硅酸盐中'
                     'O偏低10–40%、金属偏高至约15%（硅酸盐中的K约+25%）；B、N、Cl等轻元素与含H有机物可偏差20–35%）。自动识峰在参考条件（15/20kV、T3、1nA、60s）下对库中75种物质'
                     '只漏判碳基体中约7wt%的N；多报或漏报元素的谱约占：5kV约10%（重元素低能M线与N/O/F/Ti重叠、Ti L/O K、Pb M/S K）、T1约7%（宽多重峰）、T2与约10nA高计数约3–4%'
                     '（弱线拟合残差）；不提议惰性气体（含Ar）、不稳定元素、B与Be。'],
    'sim.step': ['唯一的宿主时间推进；逻辑、读数与采集在1s仪器周期上执行，真空、运动、灯丝与电荷连续推进。'],
}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.jsmit200_sem_eds.limitations。'


def _foreign_acq_text(k):
    if k not in _CONTROL_GUARDED:
        return ''
    return '；另一个agent的采集进行中时，agent调用被拒（ACCESS_DENIED' + ('；未通电时开机除外' if k in ('power.set', 'power.key_toggle') else '') + '）'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    access = ['ACCESS_DENIED'] if (CAPS[k]['visibility'] != 'agent' or k in _GUARDED) else []
    return list(dict.fromkeys(_COMMON_ERRORS + access + CAP_ERRORS.get(k, [])))


OBSERVES = {
    'plant.configure': ['plant.configured'], 'specimen.provide': ['specimen.bench_ids'], 'power.set': ['power.enabled', 'system.state'],
    'power.key_toggle': ['power.enabled', 'system.state'], 'system.shutdown': ['system.state'], 'error.acknowledge': ['system.error_code'],
    'vacuum.evacuate': ['vacuum.state', 'vacuum.chamber_pressure_indicated_pa', 'panel.evac_lamp_mode'],
    'vacuum.vent': ['vacuum.state', 'vacuum.chamber_pressure_indicated_pa', 'panel.vent_lamp_mode'],
    'vacuum.toggle_lv': ['vacuum.mode', 'vacuum.state', 'vacuum.lv_pressure_indicated_pa', 'panel.lv_lamp_mode'], 'vacuum.set_lv_pressure': ['vacuum.lv_setpoint_pa'],
    'chamber.draw_out': ['chamber.door_state'], 'chamber.draw_in': ['chamber.door_state'], 'specimen.load': ['specimen.loaded_id'], 'specimen.unload': ['specimen.loaded_id'],
    'stage.move_xy': ['stage.x_indicated_m', 'stage.y_indicated_m', 'stage.moving'], 'stage.set_z': ['stage.z_indicated_m', 'stage.moving'],
    'stage.set_tilt': ['stage.tilt_indicated_rad', 'stage.moving'], 'stage.rotate': ['stage.rotation_indicated_rad', 'stage.moving'],
    'beam.set_voltage': ['beam.accelerating_voltage_v'], 'beam.set_probe_current': ['beam.probe_current_setpoint_a'],
    'beam.observation_on': ['beam.ht_on', 'beam.filament_state', 'beam.emission_indicated_a'], 'beam.observation_off': ['beam.ht_on'],
    'gun.replace_filament': ['beam.filament_state'], 'scan.set_magnification': ['scan.magnification', 'scan.field_width_m'], 'optics.autofocus': ['optics.focus_wd_m'],
    'optics.set_focus': ['optics.focus_wd_m'], 'optics.auto_stigmate': ['optics.astigmatism_corrected'], 'optics.auto_contrast_brightness': ['optics.acb_done'],
    'detector.select': ['detector.selected'], 'image.acquire': ['acquisition.id', 'acquisition.progress', 'result.last_image_id'], 'image.result': ['result.last_image_id'],
    'eds.acquire': ['acquisition.id', 'acquisition.progress', 'result.last_spectrum_id'], 'eds.result': ['result.last_spectrum_id'], 'eds.quantify': [],
    'records.list': [], 'events.read': ['system.error_code'], 'ledger.read': [], 'provenance.read': [], 'sim.step': ['clock.time_s'],
}
_CAT, _PROD, _MAN, _IA, _PNNL, _WB = 'manufacturer_catalogue', 'manufacturer_product_page', 'related_manual_jsm_it300', 'facility_guide_iastate', 'facility_pnnl', 'user_workbook'
_LIBS, _FIT, _PHYS, _SIM = 'sem_eds_library', 'optics_fit', 'physics_literature', 'simulation_assumption'
_XRAY = ['nist_srd126_tab1', 'nist_srd126_tab2', 'nist_srd128', 'xraydb']          # the raw data behind the library's X-ray lines and attenuation
_AUDIT = 'template_validation'
CAP_SOURCES = {
    'plant.configure': [_CAT, _SIM], 'specimen.provide': [_CAT, _IA, _LIBS, 'xraydb_materials', _AUDIT, _SIM], 'power.set': [_MAN, _CAT], 'power.key_toggle': [_MAN], 'system.shutdown': [_MAN, _SIM],
    'error.acknowledge': [_MAN, _SIM], 'vacuum.evacuate': [_CAT, _MAN, _IA, _SIM], 'vacuum.vent': [_MAN, _IA, _SIM], 'vacuum.toggle_lv': [_CAT, _MAN, _SIM],
    'vacuum.set_lv_pressure': [_CAT], 'chamber.draw_out': [_CAT, _IA], 'chamber.draw_in': [_CAT, _IA], 'specimen.load': [_CAT, _IA], 'specimen.unload': [_CAT, _IA],
    'stage.move_xy': [_CAT], 'stage.set_z': [_CAT, _IA], 'stage.set_tilt': [_CAT], 'stage.rotate': [_CAT], 'beam.set_voltage': [_CAT, _MAN],
    'beam.set_probe_current': [_CAT, _FIT], 'beam.observation_on': [_CAT, _IA, _SIM], 'beam.observation_off': [_IA], 'gun.replace_filament': [_CAT, _IA, _SIM],
    'scan.set_magnification': [_CAT], 'optics.autofocus': [_CAT, _FIT, _SIM], 'optics.set_focus': [_CAT, _FIT], 'optics.auto_stigmate': [_CAT, _SIM],
    'optics.auto_contrast_brightness': [_CAT, _SIM], 'detector.select': [_CAT, _MAN, _PNNL], 'image.acquire': [_CAT, _WB, _LIBS, _FIT, _PHYS, _SIM],
    'image.result': [_CAT], 'eds.acquire': [_CAT, _PROD, _PNNL, _WB, _LIBS] + _XRAY + [_AUDIT, _PHYS, _SIM], 'eds.result': [_CAT],
    'eds.quantify': [_CAT, _LIBS] + _XRAY + [_AUDIT, _PHYS],
    'records.list': [_SIM], 'events.read': [_SIM], 'ledger.read': ['runtime_code'], 'provenance.read': [_LIBS, _AUDIT], 'sim.step': ['runtime_code'],
    'eds.map': [_CAT], 'stage.navigation_camera': [_CAT, _PROD], 'chamber.scope': [_CAT], 'image.montage': [_CAT], 'detector.lsed': [_CAT], 'stage.motor_zt': [_CAT],
    'hardware.connect': [_CAT], 'robot.handle_specimen': [_SIM], 'accuracy.certification': [_CAT, _WB]}
PARAM_ZH = {
    'room_k': '实验室室温（安装要求15–27°C）', 'relative_humidity': '相对湿度（安装要求≤60%；影响放气后水汽放气）', 'mains_voltage_v': '供电电压（单相AC 100V±10%）',
    'vent_gas': '放气气体（室内空气或干燥氮气）', 'noise_seed': '随机数种子（真空计噪声、灯丝寿命、自动聚焦误差、图像与EDS计数噪声）',
    'specimen_id': '样品ID', 'phases': '样品各相（模板库物质、面积分数、晶粒尺寸）', 'diameter_m': '样品直径', 'height_m': '样品高度',
    'topography': '样品形貌类别', 'coating': '导电镀膜种类', 'coating_thickness_m': '镀膜厚度', 'wet': '样品是否含水',
}
LIMITATIONS = [
    '选定JSM-IT200 LA（钨灯丝、TMP+RP全自动真空、LV 10–100Pa、SED+BED、集成JED-2300 DrySD 25mm² EDS、X/Y电机台）；SNS导航相机、Chamber Scope、LSED、5轴电机台、选配操作面板（Operation Panel）与3D为选配未装（面板操作经软件与控制台按键）；拼图（目录列为标准功能）、EDS面分布与线扫描未实现。',
    '真空为单体积集总模型（体积按几何样品室空腔与镜筒估计），机械泵有效抽速、涡轮泵抽速与起转、放气量（空气/氮气/含水）、漏率、放气阀与LV孔板/针阀流导均为工程值；气体物料账逐步精确闭合。',
    '束斑按亮度、球差、色差与衍射在最优孔径角下的平方和计算；有效Cs/Cc由derive_optics_parameters.py按厂家分辨率（30kV 3.0nm、3kV 8.0nm、1kV 15nm，LV 4.0nm）拟合，β、ΔE、测试束流与边缘分辨率系数为固定假设，拟合不唯一。',
    '背散射系数用Hunger–Küchler元素式与Castaing质量分数规则、Arnal倾斜律；二次电子用Lin–Joy通用曲线（其材料类别参数描述块体的总二次电子产额，不再另加SE2项）与正割倾斜律、'
    'SED收集效率0.45；BED按工作距离计算立体角、能量加权与放大器噪声；这些是经验式与工程值，不是JSM-IT200实测。',
    '未镀膜绝缘体在高真空下每个相有各自的负表面电位（电容1e-13F，工程值）：着陆能量=E0−V，净沉积电流I(1−η−δ)在实际入射角（样品台倾斜加局部坡度）下使V趋向总产额为1的平衡'
    '（着陆能量=该入射角的E2，倾斜越大E2越高、荷电越少）；电位超过束能时束被反射（镜面），降压后不再着陆；观察时剂量分布在扫描视场、EDS点分析时集中在束斑；LV下气体离子中和、'
    '导电镀膜导走电荷，不荷电。二次电子参数按材料类别给出，E2与常引值只在量级上一致（石英约3.8keV对约3keV、蓝宝石3.8对4.2、PVC 1.65对1.65、PTFE 1.5对1.85、Kapton 1.5对约0.4keV），'
    '个别聚合物的荷电阈值偏高。'
    'LV气体散射按空气截面数量级估计的未散射分数形成裙边背景。',
    'EDS：特征X射线按Bethe电离截面在Joy–Luo减速过程上积分、Myklebust背散射损失、Elam荧光产额与谱线强度、Philibert–Duncumb–Heinrich吸收（Chantler衰减系数）、探测器效率（聚酰亚胺窗、Al膜、Si网格、死层、0.45mm Si）、几何立体角与出射角（35°为工程假设）、Kramers连续谱、Fano分辨率（Mn Kα 127eV，厂家≤130eV）、Si逃逸峰、可瘫痪死时间；二次荧光、和峰、拖尾与L/M壳层Coster–Kronig跃迁未建模。',
    '无标样ZAF定量的生成项为Green–Cosslett型(U0−1)^1.67/[(Z/A)·ln(1.166(E0+Ec)/2J)]、背散射修正1−0.4η(1−1/U0)、吸收为完整Philibert式（含h项），峰族面积由高斯模板加连续谱加权最小二乘'
    '得到并按记录的名义出射角迭代归一化；与生成模型的差异是方法偏差（中位约5%相对；重元素二元体系约±3%，氧化物与硅酸盐中O偏低10–40%、金属偏高至约15%（硅酸盐中K约+25%），'
    '轻元素与有机物可达20–35%）。自动识峰按探测计数由强到弱贪心匹配（谱线族模式、其他谱线族按理论强度可检出时须有峰、纯元素理论强度比一致性与局部形状），不提议惰性气体（含Ar）、'
    '不稳定元素、B与Be；参考条件（15/20kV、T3、1nA）下只漏判碳基体中约7wt%的N，多报或漏报的谱在5kV约10%、T1约7%、T2与约10nA高计数约3–4%（重元素低能M线、Ti L/O K、'
    'Pb M/S K、宽多重峰与弱线残差）。',
    '样品微结构为种子化Voronoi相图（相面积分数与晶粒尺寸），形貌三类；图像只输出按灰度分割的区域统计与32×24缩略图（不给物质名），不输出全分辨率图像；点分析按Kanaya–Okayama射程一半的'
    '激发体积混合周围相；倾斜样品的束斑落在顶面距中心h·tanθ处，落出顶面按落到样品座处理，未建模样品侧壁。',
    '样品台X/Y电机与Z/T/R手动的速度、门拉出行程0.20m、碰撞间隙4mm、Z/T旋钮单圈抽象为工程值；换样流程（放气、拉门、装样、按样品高度设Z、推门、抽气、观察）按设施手册；实体操作没有接触或机器人验证。',
    'agent只看到示值、设定、图像/谱记录与事件；真实压力、气体负荷、灯丝寿命与已工作时间、探针电流与直径、真实工作距离、离焦、像散、样品电荷（着陆能量只能由谱的Duane–Hunt限估计）与样品真实'
    '组成只对evaluator可见；图像与谱记录中的WD为显示值、区域不带物质名、模板来源不列物质；记录按调用者隔离（evaluator或宿主创建的记录对所有agent可读，Matterix宿主以evaluator身份调用运行层）。',
    '没有真实硬件、JEOL固件、SEM Center/SMILE VIEW软件或厂家验收数据。',
]

# ---------------------------------------------------------------- SEM Operation commands (front panel 1.1.0): metadata
_OP = ['operation.state']
_I = ['truth.probe_current_a', 'truth.probe_diameter_m']
_PC = ['beam.probe_current_pc', 'beam.probe_current_setpoint_a', 'optics.acb_done'] + _I
_MOVE = ['stage.moving', 'operation.state']                                # the move target and the Last Move bookkeeping
_OFF_RESET = ['gun.auto_adjust', 'operation.state']                      # a power loss stops the auto adjustment and the movie recording
_OP_EFFECTS = {
    'beam.blank': ['beam.blanked'] + _I, 'beam.set_pc': list(_PC), 'scan.set_mode': ['scan.mode', 'scan.charge_free'] + _I,
    'scan.set_rotation': ['scan.rotation_rad'], 'scan.rotate_to_line': ['scan.rotation_rad'],
    'optics.set_stigmator': ['optics.stigmator_m', 'optics.astigmatism_corrected', 'truth.astigmatism_m'], 'optics.set_image_adjust': ['optics.image_adjust'],
    'optics.set_beam_shift': ['optics.beam_shift_m'], 'optics.wobbler': ['optics.wobbler_on'], 'aperture.set_position': ['aperture.position_m'] + _I,
    'gun.auto_adjust': ['gun.auto_adjust'], 'gun.set': ['gun.settings', 'truth.emission_a'] + _I,
    'gun.bias_memory': ['gun.settings', 'operation.state', 'truth.emission_a'] + _I,
    'alignment.configure_error': ['truth.alignment_error', 'stage.x_indicated_m', 'stage.y_indicated_m'] + _I,
    'recipe.execute': EFFECTS['vacuum.toggle_lv'] + EFFECTS['vacuum.set_lv_pressure'] + EFFECTS['beam.set_voltage'] + _PC + EFFECTS['detector.select']
                      + ['optics.image_adjust'],
    'fault.inject': ['fault.injected', 'system.error_code', 'stage.moving', 'operation.state'], 'fault.clear': ['fault.injected'],
    'stage.stop': ['stage.moving'], 'stage.last_move': list(_MOVE), 'stage.click_center': list(_MOVE), 'stage.memory': list(_MOVE),
    'stage.calibrate': ['stage.x_indicated_m', 'stage.y_indicated_m', 'fault.injected'] + _MOVE, 'specimen.set_info': _OP, 'settings.configure': _OP + _PC,
    'presets.configure': _OP, 'ui.select': _OP, 'image.save': _OP, 'image.open': _OP, 'image.copy': _OP, 'image.anaglyph': _OP, 'movie.record': _OP,
    'snapshot.take': _OP, 'snapshot.reproduce': EFFECTS['beam.set_voltage'] + _PC + EFFECTS['scan.set_magnification'] + EFFECTS['optics.set_focus']
                                               + EFFECTS['detector.select'] + _MOVE + ['optics.image_adjust'],
    'snapshot.save': _OP, 'measure.ruler': _OP, 'eds.transfer_image': _OP, 'user.manage': _OP, 'vacuum.lv_auto': _OP,
}
for _k, _v in _OP_EFFECTS.items():
    CAPS[_k]['effects'] = list(dict.fromkeys(_v))
    EFFECTS[_k] = CAPS[_k]['effects']
for _k, _extra in {'power.set': _OFF_RESET, 'power.key_toggle': _OFF_RESET, 'beam.observation_on': ['gun.settings'], 'stage.move_xy': _OP,
                   'gun.replace_filament': ['fault.injected'],
                   'optics.auto_stigmate': ['optics.stigmator_m'], 'optics.auto_contrast_brightness': ['optics.image_adjust'],
                   'image.acquire': _I, 'eds.acquire': _I, 'stage.set_z': ['optics.focus_wd_m', 'truth.defocus_m', 'truth.probe_diameter_m']}.items():
    CAPS[_k]['effects'] = list(dict.fromkeys(CAPS[_k]['effects'] + _extra))
for _k in _ACB_INTERLOCK - {'recipe.execute', 'beam.set_pc'}:              # ACB interlock (Settings-Auto) resets the manual brightness / contrast
    CAPS[_k]['effects'] = list(dict.fromkeys(CAPS[_k]['effects'] + ['optics.image_adjust']))
CAPS['beam.set_pc']['effects'].append('optics.image_adjust')
CAPS['beam.set_probe_current']['effects'] = list(dict.fromkeys(CAPS['beam.set_probe_current']['effects'] + ['beam.probe_current_pc']))
for _k in EFFECTS:
    EFFECTS[_k] = CAPS[_k]['effects']
_GUN_STEP = ['gun.settings', 'gun.auto_adjust', 'beam.emission_indicated_a', 'truth.emission_a', 'truth.filament_on_time_s'] + _I + _CHARGE
ON_STEP.update({
    'beam.blank': _CHARGE, 'beam.set_pc': _CHARGE, 'scan.set_mode': _CHARGE, 'scan.set_rotation': _CHARGE, 'scan.rotate_to_line': _CHARGE,
    'optics.set_beam_shift': _CHARGE, 'aperture.set_position': _CHARGE, 'gun.auto_adjust': _GUN_STEP,
    'gun.set': ['beam.emission_indicated_a', 'truth.filament_on_time_s'] + _CHARGE, 'gun.bias_memory': ['beam.emission_indicated_a'] + _CHARGE,
    'alignment.configure_error': _CHARGE, 'recipe.execute': ON_STEP['vacuum.toggle_lv'] + ON_STEP['vacuum.set_lv_pressure'] + _CHARGE,
    'stage.last_move': ON_STEP['stage.move_xy'], 'stage.click_center': ON_STEP['stage.move_xy'], 'stage.memory': ON_STEP['stage.move_xy'],
    'stage.calibrate': ON_STEP['stage.move_xy'], 'snapshot.reproduce': ON_STEP['stage.move_xy'] + _CHARGE,
    'vacuum.lv_auto': list(ON_STEP['vacuum.set_lv_pressure']),
    'settings.configure': ['beam.ht_on', 'beam.filament_state', 'beam.emission_indicated_a', 'truth.filament_on_time_s', 'vacuum.state', 'vacuum.evacuation_elapsed_s',
                           'system.error_code'] + _BEAM_TRUTH + _P + _GAUGES + _LAMPS + _CHARGE,
})
for _k, _extra in {'image.acquire': ['operation.state'] + _I, 'eds.acquire': _I,
                   'plant.configure': _OFF_RESET, 'beam.observation_on': _GUN_STEP, 'vacuum.vent': ['gun.auto_adjust'], 'vacuum.toggle_lv': ['gun.auto_adjust'],
                   'beam.observation_off': ['gun.auto_adjust'], 'system.shutdown': ['gun.auto_adjust']}.items():
    ON_STEP[_k] = ON_STEP[_k] + _extra
ON_STEP = {k: list(dict.fromkeys(v)) for k, v in ON_STEP.items()}
ON_STEP['sim.step'] = ['clock.time_s'] + sorted(set(sum((v for k, v in ON_STEP.items() if k != 'sim.step'), [])) | set(CAPS['power.set']['effects'])
                                                - {'clock.time_s'})
ON_STEP_ZH.update({
    'beam.blank': '消隐期间束流不到样品：绝缘样品不再荷电；取消消隐后恢复', 'beam.set_pc': ON_STEP_ZH['beam.set_probe_current'],
    'scan.set_mode': 'Freeze时电子束消隐（不荷电）；RDC把剂量集中在1/4视场宽，荷电更快；CF在Slow/拍照时降低荷电速率',
    'scan.set_rotation': '扫描方向改变受照区域的取向，荷电随之变化', 'scan.rotate_to_line': '同扫描旋转',
    'optics.set_beam_shift': '束斑落点随位移移动，绝缘样品的荷电随之变化', 'aperture.set_position': '透过电流随光阑偏离改变，荷电速率随之变化',
    'gun.auto_adjust': '约30s后完成：灯丝加热回到饱和点，AGC还把对中设到最佳；束流与发射读数随之变化；电子束关闭或切到LV时中止',
    'gun.set': '发射读数在下一周期更新；加热超过饱和点时灯丝寿命消耗加快', 'gun.bias_memory': '发射读数在下一周期更新',
    'alignment.configure_error': '束流随误差改变，荷电速率随之变化',
    'recipe.execute': '切换真空模式或LV压力后按新模式抽气与调压（同vacuum.toggle_lv、vacuum.set_lv_pressure）；新的着陆能量与束流改变荷电',
    'stage.last_move': ON_STEP_ZH['stage.move_xy'], 'stage.click_center': ON_STEP_ZH['stage.move_xy'], 'stage.memory': ON_STEP_ZH['stage.move_xy'],
    'stage.calibrate': 'X/Y回到交换位（按速度移动）', 'snapshot.reproduce': '再现的样品台坐标按速度移动；新的条件改变荷电',
    'vacuum.lv_auto': '自动调压停止时针阀不动，样品室压力向该开度的平衡漂移（可能离开LV就绪）；重新Apply后按设定值调压',
    'settings.configure': 'Eco开着时无操作达到设定分钟后停止观察（高压关闭）；慢抽气使粗抽到5kPa前按1/10速度进行；EOS大束流改变束流与荷电',
})
ON_STEP_ZH['image.acquire'] = ON_STEP_ZH['image.acquire'] + '；自动保存开着时完成后写入图像文件；Freeze时采集结束后电子束重新消隐'
_SAMPLING_CYCLE |= {'gun.settings', 'gun.auto_adjust'}
_SAMPLING_COMMAND -= _SAMPLING_CYCLE
_SW = ['power.enabled', 'system.state']
PRECONDITIONS.update({
    'beam.blank': ('通电、软件已启动、无待确认错误', _READY[:3]), 'beam.set_pc': PRECONDITIONS['beam.set_probe_current'],
    'scan.set_mode': ('通电、软件已启动、无待确认错误与采集', list(_READY)), 'scan.set_rotation': ('通电、软件已启动、无待确认错误与采集', list(_READY)),
    'scan.rotate_to_line': ('通电、软件已启动、无待确认错误与采集；两点不重合', list(_READY)),
    'optics.set_stigmator': ('通电、软件已启动、无待确认错误与采集', list(_READY)), 'optics.set_image_adjust': ('通电、软件已启动、无待确认错误', _READY[:3]),
    'optics.set_beam_shift': ('通电、软件已启动、无待确认错误与采集', list(_READY)), 'optics.wobbler': ('通电、软件已启动、无待确认错误', _READY[:3]),
    'aperture.set_position': ('已声明实验室条件（机械旋钮）', ['plant.configured']),
    'gun.auto_adjust': ('通电、软件已启动、无待确认错误与采集；观察中（真空就绪、灯丝饱和）；高真空模式；没有正在进行的自动调整', _BEAM + ['vacuum.mode', 'gun.auto_adjust']),
    'gun.set': ('通电、软件已启动、无待确认错误；没有正在进行的自动调整', _READY[:3] + ['gun.auto_adjust']),
    'gun.bias_memory': ('通电、软件已启动、无待确认错误；读回时须已为当前加速电压保存过', _READY[:3] + ['beam.accelerating_voltage_v', 'gun.auto_adjust']),
    'alignment.configure_error': ('已声明实验室条件', ['plant.configured']),
    'fault.inject': ('已声明实验室条件', ['plant.configured']), 'fault.clear': ('已声明实验室条件', ['plant.configured']),
    'recipe.execute': ('通电、软件已启动、无待确认错误与采集；所选类别有该列（表中“-”被拒）', _READY + ['vacuum.mode']),
    'stage.stop': ('通电', ['power.enabled']), 'stage.last_move': ('同stage.move_xy；之前有过一次移动', PRECONDITIONS['stage.move_xy'][1] + ['operation.state']),
    'stage.click_center': PRECONDITIONS['stage.move_xy'], 'stage.memory': ('软件已启动；移动时同stage.move_xy；名称须已登记', _SW + ['operation.state']),
    'stage.calibrate': ('通电、软件已启动、无待确认错误与采集；样品室已放气到大气压；样品已取下', _READY + ['vacuum.state', 'specimen.loaded_id']),
    'specimen.set_info': ('软件已启动', list(_SW)), 'vacuum.lv_auto': ('通电', ['power.enabled']), 'settings.configure': ('软件已启动；录像中不能切换视图；EOS切换时无采集', _SW + ['operation.state', 'acquisition.id']),
    'presets.configure': ('软件已启动；数值在该窗口的范围内', list(_SW)), 'ui.select': ('软件已启动', list(_SW)),
    'image.save': ('软件已启动；有冻结图像、已完成的照片或实时图像；文件名未被占用', _SW + ['scan.mode', 'result.last_image_id']),
    'image.open': ('软件已启动；文件存在', _SW + ['operation.state']), 'image.copy': ('软件已启动；有可复制的图像', list(_SW)),
    'image.anaglyph': ('软件已启动；两个文件都是倾斜角相差≥1°的照片', _SW + ['operation.state']),
    'movie.record': ('软件已启动；开始时有实时图像且不是全屏视图；停止时正在录像', _SW + ['beam.ht_on', 'operation.state']),
    'snapshot.take': ('软件已启动；有可贴的图像', list(_SW)), 'snapshot.reproduce': ('通电、软件已启动、无待确认错误与采集；有快照', _READY + ['operation.state']),
    'snapshot.save': ('软件已启动；有快照', _SW + ['operation.state']), 'measure.ruler': ('软件已启动；主屏有图像', list(_SW)),
    'eds.transfer_image': ('软件已启动；有图像', list(_SW)), 'user.manage': ('软件已启动；新增时名称未被占用、切换时用户已登记', _SW + ['operation.state']),
})
ERROR_ZH.update({
    'LV_MODE': 'LV模式不能执行（手册：AGC/AFS不能用于LV）', 'GUN_AUTO_RUNNING': '自动灯丝调整（AGC/AFS）正在进行', 'NO_SAVED_BIAS': '当前加速电压没有保存的栅偏压',
    'RECIPE_NOT_AVAILABLE': '该类别没有这一列条件（表中为“-”）', 'NO_LAST_MOVE': '还没有可返回的上一次移动', 'UNKNOWN_MEMORY': '坐标记忆中没有该名称',
    'MEMORY_FULL': '坐标记忆已满（100条）', 'NO_IMAGE': '没有可用的图像（先观察、冻结或拍照）', 'UNKNOWN_FILE': '文件不存在', 'FILE_EXISTS': '文件名已存在',
    'FILES_FULL': '文件列表已满（500个）', 'TILT_PAIR_REQUIRED': '须选两张倾斜角相差至少1°的照片', 'FULL_SCREEN': '全屏视图下不能录像', 'NOT_RECORDING': '没有在录像',
    'MOVIE_RECORDING': '录像中不能切换视图', 'NO_SNAPSHOT': '快照区没有图像', 'USER_EXISTS': '用户已存在', 'UNKNOWN_USER': '用户未登记',
})
_SWE = ['PLANT_REQUIRED', 'POWER_OFF', 'NOT_READY']
CAP_ERRORS.update({
    'beam.blank': _R[:4], 'beam.set_pc': list(_R), 'scan.set_mode': list(_R), 'scan.set_rotation': list(_R), 'scan.rotate_to_line': list(_R),
    'optics.set_stigmator': list(_R), 'optics.set_image_adjust': _R[:4], 'optics.set_beam_shift': list(_R), 'optics.wobbler': _R[:4],
    'aperture.set_position': ['PLANT_REQUIRED'], 'gun.auto_adjust': _B + ['LV_MODE', 'GUN_AUTO_RUNNING'], 'gun.set': _R[:4] + ['GUN_AUTO_RUNNING'],
    'gun.bias_memory': _R[:4] + ['NO_SAVED_BIAS', 'GUN_AUTO_RUNNING'], 'alignment.configure_error': ['PLANT_REQUIRED'],
    'recipe.execute': _R + ['RECIPE_NOT_AVAILABLE'], 'stage.stop': ['PLANT_REQUIRED', 'POWER_OFF'],
    'stage.last_move': CAP_ERRORS['stage.move_xy'] + ['NO_LAST_MOVE'], 'stage.click_center': list(CAP_ERRORS['stage.move_xy']),
    'stage.memory': _SWE + ['MEMORY_FULL', 'UNKNOWN_MEMORY'] + CAP_ERRORS['stage.move_xy'],
    'stage.calibrate': _R + ['VENT_REQUIRED', 'HOLDER_OCCUPIED', 'STAGE_DRIVER_OVERHEAT', 'STAGE_MECHANICAL_LIMIT', 'STAGE_LENS_CONTACT'],
    'specimen.set_info': list(_SWE), 'vacuum.lv_auto': ['PLANT_REQUIRED', 'POWER_OFF', 'LV_PRESSURE_ADJUST_FAILED'],
    'fault.inject': ['PLANT_REQUIRED'], 'fault.clear': ['PLANT_REQUIRED'], 'settings.configure': _SWE + ['MOVIE_RECORDING', 'ACQUISITION_RUNNING'], 'presets.configure': list(_SWE),
    'ui.select': list(_SWE), 'image.save': _SWE + ['NO_IMAGE', 'FILE_EXISTS', 'FILES_FULL'], 'image.open': _SWE + ['UNKNOWN_FILE'],
    'image.copy': _SWE + ['NO_IMAGE'], 'image.anaglyph': _SWE + ['UNKNOWN_FILE', 'TILT_PAIR_REQUIRED', 'FILES_FULL'],
    'movie.record': _SWE + ['FULL_SCREEN', 'BEAM_NOT_READY', 'NOT_RECORDING', 'FILES_FULL'], 'snapshot.take': _SWE + ['NO_IMAGE'],
    'snapshot.reproduce': _R + ['NO_SNAPSHOT', 'DETECTOR_NOT_AVAILABLE', 'DOOR_OPEN', 'STAGE_COLLISION_RISK'], 'snapshot.save': _SWE + ['NO_SNAPSHOT', 'FILES_FULL'],
    'measure.ruler': _SWE + ['NO_IMAGE', 'UNKNOWN_FILE'], 'eds.transfer_image': _SWE + ['NO_IMAGE'], 'user.manage': _SWE + ['USER_EXISTS', 'UNKNOWN_USER'],
})
for _k in _OP_EFFECTS:
    MODEL_NONE_ZH.setdefault(_k, '只改SEM Operation软件或电子光学的设定，没有可见模型变化（屏幕画面由panel.capture提供）')
MODEL_NONE_ZH['aperture.set_position'] = '光阑微调旋钮没有单独的场景关节；只改过程层光阑位置'
RESOURCES.update({'vacuum.lv_auto': ['vacuum_system'], 'beam.blank': ['electron_gun'], 'beam.set_pc': ['electron_gun'], 'scan.set_mode': ['scan_generator'], 'scan.set_rotation': ['scan_generator'],
                  'scan.rotate_to_line': ['scan_generator'], 'optics.set_stigmator': ['objective_lens'], 'optics.set_image_adjust': ['detectors'],
                  'optics.set_beam_shift': ['scan_generator'], 'optics.wobbler': ['objective_lens'], 'aperture.set_position': ['objective_aperture'],
                  'gun.auto_adjust': ['electron_gun', 'host_clock'], 'gun.set': ['electron_gun'], 'gun.bias_memory': ['electron_gun'],
                  'recipe.execute': ['electron_gun', 'vacuum_system', 'detectors'], 'stage.stop': ['stage'], 'stage.last_move': ['stage'], 'stage.click_center': ['stage'],
                  'stage.memory': ['stage'], 'stage.calibrate': ['stage'], 'snapshot.reproduce': ['electron_gun', 'scan_generator', 'detectors', 'stage'],
                  'movie.record': ['scan_generator', 'records'], 'image.save': ['records'], 'image.anaglyph': ['records'], 'snapshot.save': ['records']})
_ASSUMED = '刻度、步长、时间与系数为假设（JSM-IT300同系列手册与Iowa State指南未给出），见panel_ui.json的assumptions。'
CAP_LIMITS.update({
    'beam.blank': ['消隐瞬时生效；消隐偏转器与法拉第杯不单独建模。'],
    'beam.set_pc': ['P.C.刻度（标准1pA–30nA、大束流10pA–0.3µA的对数1–99）为假设：厂家只给出照片数据的P.C.值。'],
    'scan.set_mode': ['实时像按所选扫描组合（Settings–Scan的RDC/Fast/Slow组合，帧时间取手册规格表8.2.5.b）加散粒/放大器噪声：每个缩略图像素的噪声=全分辨率像素噪声/√(像素合并数×积分帧数)，'
                      '噪声图样按仪器周期由噪声种子决定；Slow帧（10–40s）在条件改变后从上往下重画一帧时间（按时间推进的变化如样品台移动立即显示）；'
                      'Freeze时消隐、RDC剂量集中在1/4视场宽、CF荷电速率×0.3为假设；扫描速度不改变荷电（视场平均剂量率与帧时间无关；未建模逐点充放电）。'],
    'scan.set_rotation': ['扫描旋转只旋转扫描方向；Tilt Mag与Dynamic Focus要求SRT为0（手册）由操作者保证。'],
    'scan.rotate_to_line': ['按4:3画面换算角度。'],
    'optics.set_stigmator': ['固有像散是一个矢量：换电压或换灯丝时大小随机重置、方向保持；消像散器范围±100nm为假设。'],
    'optics.set_image_adjust': ['亮度/衬度作用在探测器增益/偏置之后的灰度上（数字化映射，非真实放大器）；' + _ASSUMED],
    'optics.set_beam_shift': ['偏转限±10µm为假设；束斑位移按扫描坐标换算到样品坐标（未建模束倾斜）。'],
    'optics.wobbler': ['摆动幅度=0.1×光阑偏离、摆动时离焦20µm为假设；只给出摆动幅度读数，不生成动画帧。'],
    'aperture.set_position': ['光阑偏离使透过电流按exp(−(d/40µm)²)下降、模糊增加2e-3×d为假设；光阑孔径档位未建模。', _ROBOT],
    'gun.auto_adjust': ['AGC/AFS 30s为假设（手册“几十秒”）；低于5kV时在5kV执行后恢复，没有单独的电压变化。'],
    'gun.set': ['灯丝加热：饱和点在加热条0.6处，左侧约一半处有假峰，右侧过流使寿命消耗×(1+20(h/h饱和−1))；栅偏压粗/细每25/250格使负载电流减半；'
                '对中偏差按exp(−Σd²/0.25²)降低束流；全部为假设。'],
    'gun.bias_memory': ['每个加速电压一组保存值。'],
    'alignment.configure_error': ['误差参数默认0（理想）；真实偏差的统计分布不研究（后续“标定/仿真到真实差距”任务）。'],
    'fault.inject': ['只有手册3.14中能由仿真表现的信息：0261需要R电机、0360/0380需要VMIU（均为未装选配），仍不支持；信息文字照手册，Error ID为一组时取首个；'
                     '服务修理不建模（由评测者fault.clear表示修好）。'],
    'fault.clear': ['只消除原因；信息仍须确认（error.acknowledge）。'],
    'recipe.execute': ['表3-1在文本副本中错乱：各类别的kV/P.C./压力为重建值（镀膜15kV P.C.50、低加速1.0kV P.C.50、低真空BED P.C.60 30–50Pa，高倍20kV P.C.35，有机/生物10kV），'
                       '需对照原厂表核实。'],
    'stage.stop': ['只停X/Y电机（Z/T/R为手动轴）。'], 'stage.last_move': ['只记X/Y。'],
    'stage.click_center': ['按样品台旋转、扫描旋转与倾斜（Y方向1/cosθ）换算；坐标系为运行层的X/Y（视场向右移动时X减小）。'],
    'stage.memory': ['只记X/Y坐标（Z/T/R为手动轴）。'],
    'stage.calibrate': ['校准瞬时测出坐标偏差并回到交换位（真实约数十秒的走限位过程未逐步建模）。'],
    'specimen.set_info': ['软件限位只作用于X/Y电机移动（IT200目录：XY两轴电机台；Z/T/R手动轴由真实几何的碰撞检查拒绝）；软件按所选样品座外廓（32mm座与其插件16mm、10mm圆座7.5mm为假设）'
                          '与输入高度、假设样品不侧向伸出计算；物理样品座按32mm多用途座建模，输入偏小时由真实几何检查拒绝（未建模真实撞击损坏）。'],
    'settings.configure': ['各节字段按手册窗口说明整理；扫描速度组合按手册规格表8.2.5.b的帧时间（文本副本合并单元格，像素数按列顺序对应为假设）；拍照组合的像素/时间、Eco默认30min、'
                           '慢抽气1/10速度到5kPa、ACB目标映射为假设；语言：手册没有日文界面文字，选择Japanese时保存设定、在语言窗口与operation.state中显示，界面文字仍为英文（不自拟译文）；'
                           'Tilt Mag按cos(校正角)缩小Y扫描（倾斜样品图像纵横比复原），Dynamic Focus沿扫描Y按tan(校正角)改变聚焦，'
                           '两者作用于实时像、照片、剂量分布、EDS面分析与Click Center；SRT不为0时校正方向不随旋转（手册要求SRT为0）。'],
    'presets.configure': ['预设默认值为假设。'], 'ui.select': ['只记加速电压窗口的选定值，Apply才设定。'],
    'image.save': ['文件只记名称、目录、格式、照片数据与缩略图引用；不写真实文件。'], 'image.open': ['只在主屏显示文件的缩略图。'],
    'image.copy': ['剪贴板只记来源。'], 'image.anaglyph': ['立体图按两张32×24缩略图合成（红=左，青=右）。'],
    'movie.record': ['只记录时长、帧数与文件名（日期按模拟PC时钟，起点2026-01-01为假设），不保存帧。'],
    'snapshot.take': ['快照为32×24缩略图与条件。'], 'snapshot.reproduce': ['再现的条件经各自命令设定，被拒的那一项之前已设定的保留。'],
    'snapshot.save': ['同image.save。'], 'measure.ruler': ['只量两点距离与角度（平行线、直径等其他量测未提供）。'],
    'eds.transfer_image': ['只记参考图来源。'], 'user.manage': ['用户只改数据目录；没有密码与权限。'],
    'vacuum.lv_auto': ['Fine↑/↓按设定值±1Pa实现（手册文字中箭头方向与压力升降的对应前后不一，按“↑降压、↓升压”）。'],
})
CAP_LIMITS['beam.observation_on'] = CAP_LIMITS['beam.observation_on'] + ['电子枪为auto时开始观察瞬时完成加热与对中（对中到误差参数处）；手动调整后保持手动设定直到AGC/AFS。']
OBSERVES.update({
    'beam.blank': ['beam.blanked'], 'beam.set_pc': ['beam.probe_current_pc', 'beam.probe_current_setpoint_a'], 'scan.set_mode': ['scan.mode', 'scan.charge_free'],
    'scan.set_rotation': ['scan.rotation_rad'], 'scan.rotate_to_line': ['scan.rotation_rad'], 'optics.set_stigmator': ['optics.stigmator_m'],
    'optics.set_image_adjust': ['optics.image_adjust'], 'optics.set_beam_shift': ['optics.beam_shift_m'], 'optics.wobbler': ['optics.wobbler_on'],
    'aperture.set_position': ['aperture.position_m'], 'gun.auto_adjust': ['gun.auto_adjust', 'gun.settings'], 'gun.set': ['gun.settings', 'beam.emission_indicated_a'],
    'gun.bias_memory': ['gun.settings'], 'alignment.configure_error': [], 'fault.inject': ['fault.injected', 'system.error_code'], 'fault.clear': ['fault.injected'],
    'recipe.execute': ['beam.accelerating_voltage_v', 'beam.probe_current_pc', 'detector.selected', 'vacuum.mode'],
    'stage.stop': ['stage.moving'], 'stage.last_move': ['stage.x_indicated_m', 'stage.y_indicated_m'], 'stage.click_center': ['stage.x_indicated_m', 'stage.y_indicated_m'],
    'stage.memory': ['operation.state'], 'stage.calibrate': ['stage.x_indicated_m', 'stage.y_indicated_m'],
    **{k: ['operation.state'] for k in ('specimen.set_info', 'settings.configure', 'presets.configure', 'ui.select', 'image.save', 'image.open', 'image.copy',
                                        'image.anaglyph', 'movie.record', 'snapshot.take', 'snapshot.save', 'measure.ruler', 'eds.transfer_image', 'user.manage')},
    'snapshot.reproduce': ['beam.accelerating_voltage_v', 'scan.magnification', 'optics.focus_wd_m', 'detector.selected'],
    'vacuum.lv_auto': ['operation.state', 'vacuum.lv_pressure_indicated_pa'],
})
CAP_SOURCES.update({k: [_MAN, _SIM] for k in _OP_EFFECTS})
CAP_SOURCES.update({k: [_MAN, _IA, _SIM] for k in ('specimen.set_info', 'settings.configure', 'user.manage', 'image.save', 'stage.click_center', 'beam.set_pc')})
CAP_SOURCES['recipe.execute'] = [_MAN, _CAT, _SIM]
_CONTROL_GUARDED |= {'beam.blank', 'beam.set_pc', 'scan.set_mode', 'scan.set_rotation', 'scan.rotate_to_line', 'optics.set_stigmator', 'optics.set_image_adjust',
                     'optics.set_beam_shift', 'optics.wobbler', 'aperture.set_position', 'gun.auto_adjust', 'gun.set', 'gun.bias_memory', 'recipe.execute',
                     'stage.stop', 'stage.last_move', 'stage.click_center', 'stage.memory', 'stage.calibrate', 'settings.configure', 'snapshot.reproduce',
                     'vacuum.lv_auto'}
_CONTROL_GUARDED -= _PHYSICAL
_GUARDED = _CONTROL_GUARDED | _DATA_GUARDED
LIMITATIONS.append('前面板（1.1.0）：SEM Operation（InTouchScope）界面由panel_ui.json描述，按JSM-IT300同系列说明书（第3–5章）与Iowa State JSM-IT200指南重建（近似）；'
                   '消隐、扫描模式与旋转、手动消像散/亮度衬度/束位移、Wobbler与可动光阑、电子枪窗口（AGC/AFS、加热、栅偏压、对中）、P.C.刻度与EOS大束流、Recipes、'
                   '坐标记忆/Last Move/Click Center/校准、各设置窗口、文件、录像、快照、量尺、用户由本运行层的最小模型实现；对中、光阑与样品台坐标的误差参数默认0（理想），'
                   '真实误差与公差留给后续“标定/仿真到真实差距”任务；Report（需Microsoft Office）、PCD、LLC、AUX、SNS/Chamber Scope、5轴电机台功能（R/T/Z电机、优中心R/T、'
                   '高分辨率模式）、LSED与3D测量为未装的选配或外部软件，界面显示“未安装”。')


def export_jeol_jsmit200_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=v['reference'], origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'JeolJsmit200Session.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)),
                        source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.jeol_jsmit200.JeolJsmit200Session.invoke',
                                              invocation=f'session.invoke(instance_id="jsmit200_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=PRECONDITIONS[k][0] + _foreign_acq_text(k) + '；被拒时状态不变。',
                                                                              state_ids=list(PRECONDITIONS[k][1]) + (['acquisition.started_by_other_agent'] if k in _CONTROL_GUARDED else []),
                                                                              enforced_by='runtime', check='JeolJsmit200Runtime.invoke / JeolJsmit200Session._guard')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定、目标或状态标志；压力、位置、灯丝与采集进度只随step推进')
                                for s in v['effects']],
                 observes=[] if un else list(OBSERVES.get(k, [])),
                 model_effects=dict(mode='none', description_zh='不支持：不实现，没有模型变化' if un else MODEL_NONE_ZH.get(k, '状态绑定到场景显示/关节（见合同model_bindings）'), binding_ids=[]),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion='采集结束（完成、失败或取消）：帧时间或EDS实时间到达后记录状态确定' if async_ else '原子设定、配置或查询，实际物理状态由step推进',
                                completion_state_ids=['acquisition.id', 'acquisition.progress', 'result.last_image_id' if k == 'image.acquire' else 'result.last_spectrum_id']
                                if async_ else [],
                                timeout_s=None, cancellation='supported' if async_ else 'unsupported', retry='same_request_id', resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=('取消或失败（停止观察、灯丝烧断、高压跳闸、真空失去、断电）时记录以cancelled/failed结束，不产生图像/谱；'
                                                    '已照射的电子与电荷仍计入账目（样品已受照）。') if async_ else
                                                   ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=CAP_ERROR_ZH.get((k, e), ERROR_ZH[e]), side_effects='partial' if async_ and e in _PARTIAL else 'none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH[k]) for s in ON_STEP[k]
                                   if not (k == 'sim.step' and s in v['effects'])]      # a slot set at once may also evolve on the step
        caps[k] = c
    called = _panel_ui.spec_capabilities(PANEL_SPEC) if PANEL_SPEC else []
    imm = sorted({e for k in called for e in CAPS[k]['effects']})
    later = sorted({e for k in called for e in ON_STEP.get(k, [])})
    for k, v in PANEL_CAPS.items():
        gesture = v['kind'] == 'device_command'
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='implemented', implementation={layer: 'implemented' if layer in ('process', 'host') else 'absent' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.jeol_jsmit200.JeolJsmit200Session.invoke',
                              invocation=f'session.invoke(instance_id="jsmit200_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[dict(description_zh='按panel_ui.json的界面规则：断电时只有MAIN POWER钥匙与机械旋钮/门有效，软件加载中只显示启动画面；被调用的命令各自检查前提，被拒时显示软件的提示且状态不变',
                                     state_ids=['power.enabled', 'system.state', 'acquisition.started_by_other_agent'], enforced_by='runtime',
                                     check='JeolJsmit200Session._guard / JeolJsmit200Runtime._panel_entry（面板引擎_panel_ui.py）')] if gesture else [],
                 state_effects=([dict(state_id=x, timing='immediate', description_zh='手势调用的命令立即改变的状态') for x in imm] +
                                [dict(state_id=x, timing='on_step', description_zh='手势调用的命令在之后的step中推进的状态') for x in later]) if gesture else [],
                 observes=[], model_effects=dict(mode='none', description_zh='屏幕画面由panel.capture提供；3D屏幕纹理与按键/旋钮关节触发手势属后续Matterix步骤', binding_ids=[]),
                 execution=dict(mode='synchronous', time_domain='simulation', completion_criterion='手势瞬时完成（不推进仿真时钟）；被调用命令的过程由step推进',
                                completion_state_ids=[], timeout_s=None, cancellation='unsupported', retry='same_request_id', resources=['panel'],
                                partial_failure_zh='一个手势按界面规则可调用多个命令：已被接受的前序命令保留，被拒的命令不改变状态' if gesture else '只读'),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH[e], side_effects='none') for e in _COMMON_ERRORS + ['ACCESS_DENIED']],
                 verification=[], limitations=['面板层：panel_ui.json（JSM-IT300同系列说明书与Iowa State JSM-IT200指南页码证据）解释手势并调用本运行层的命令；'
                                              '同系列手册未写或文本副本错乱之处见panel_ui.json的assumptions（近似）'],
                 source_ids=[_MAN, _IA, _CAT])
        caps[k] = c
    params = {}
    for name, (val, unit, meaning, src) in {
            'kv_min_v': (KV_RANGE_V[0], 'V', '最低加速电压0.5kV', _CAT), 'kv_max_v': (KV_RANGE_V[1], 'V', '最高加速电压30kV', _CAT),
            'probe_current_min_a': (PROBE_CURRENT_RANGE_A[0], 'A', '最小探针电流1pA', _CAT), 'probe_current_max_a': (PROBE_CURRENT_RANGE_A[1], 'A', '最大探针电流0.3µA', _CAT),
            'mag_min': (MAG_RANGE[0], '1', '最小直接放大倍数×5', _CAT), 'mag_max': (MAG_RANGE[1], '1', '最大直接放大倍数×300,000', _CAT),
            'print_width_m': (PRINT_WIDTH_M, 'm', '放大倍数定义用打印宽度128mm', _CAT), 'lv_min_pa': (LV_RANGE_PA[0], 'Pa', 'LV最低压力10Pa', _CAT),
            'lv_max_pa': (LV_RANGE_PA[1], 'Pa', 'LV最高压力100Pa', _CAT), 'resolution_30kv_m': (RESOLUTION_HV_M[30000.0], 'm', '分辨率3.0nm（30kV，HV）', _CAT),
            'resolution_3kv_m': (RESOLUTION_HV_M[3000.0], 'm', '分辨率8.0nm（3kV）', _CAT), 'resolution_1kv_m': (RESOLUTION_HV_M[1000.0], 'm', '分辨率15nm（1kV）', _CAT),
            'resolution_lv_m': (RESOLUTION_LV_M, 'm', 'LV分辨率4.0nm（30kV，BED）', _CAT), 'wd_min_m': (WD_RANGE_M[0], 'm', 'Z（WD）下限5mm', _CAT),
            'wd_max_m': (WD_RANGE_M[1], 'm', 'Z（WD）上限48mm', _CAT), 'stage_x_travel_m': (0.080, 'm', 'X行程80mm', _CAT), 'stage_y_travel_m': (0.040, 'm', 'Y行程40mm', _CAT),
            'tilt_min_rad': (TILT_RANGE_RAD[0], 'rad', '倾斜下限−10°', _CAT), 'tilt_max_rad': (TILT_RANGE_RAD[1], 'rad', '倾斜上限90°', _CAT),
            'specimen_max_diameter_m': (SPECIMEN_MAX_DIAMETER_M, 'm', '样品最大直径150mm', _CAT), 'specimen_max_height_m': (SPECIMEN_MAX_HEIGHT_M, 'm', '样品最大高度48mm', _CAT),
            'eds_area_m2': (EDS_AREA_M2, 'm^2', 'DrySD有效面积25mm²', _CAT), 'eds_fwhm_mn_spec_ev': (EDS_FWHM_MN_SPEC_EV, 'eV', 'EDS分辨率≤130eV（目录未注明谱线，按惯例为Mn Kα）', _CAT),
            'mains_nominal_v': (MAINS_NOMINAL_V, 'V', '单相AC 100V', _CAT), 'evac_limit_s': (EVAC_LIMIT_S, 's', '抽气10min未完成报错', _MAN),
            'rp_speed_m3_s': (RP_SPEED_M3_S, 'm^3/s', '机械泵抽速≥100L/min', _MAN), 'holder_radius_m': (HOLDER_RADIUS_M, 'm', '32mm多用途样品座', _IA),
            'saturation_s': (SATURATION_S, 's', '开始观察约10s成像', _IA)}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=[src], uncertainty=None)
    for name, (val, unit, meaning) in {
            'c_s_coef': (C_S_COEF, '1', 'Cs=c_s(WD+2mm)，按分辨率拟合'), 'c_c_coef': (C_C_COEF, '1', 'Cc=c_c(WD+2mm)，按分辨率拟合'),
            'bed_resolution_factor': (BED_RES_FACTOR, '1', 'BED/LV分辨率系数（LV 4.0nm/同条件WD 5mm下模型分辨率）')}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=[_FIT, _CAT, _MAN],
                            uncertainty='按厂家分辨率拟合的等效常数（derive_optics_parameters.py）：拟合不唯一（β–Cs、ΔE–Cc互相替代），不是实测透镜数据')
    for name, (val, unit, meaning) in {
            'v_system_m3': (V_SYSTEM_M3, 'm^3', '抽气体积（几何样品室空腔+镜筒）'), 'rp_eff_m3_s': (RP_EFF_M3_S, 'm^3/s', '机械泵经管路的有效抽速'),
            'tmp_speed_m3_s': (TMP_SPEED_M3_S, 'm^3/s', '涡轮分子泵有效抽速'), 'tmp_spinup_s': (TMP_SPINUP_S, 's', '涡轮泵起转时间'),
            'crossover_pa': (CROSSOVER_PA, 'Pa', '粗抽切换压力'), 'hv_ready_pa': (HV_READY_PA, 'Pa', '高真空就绪压力'), 'gun_trip_pa': (GUN_TRIP_PA, 'Pa', '高压跳闸压力'),
            'outgas_air_pa_m3_s': (OUTGAS_AIR_PA_M3_S, 'Pa*m^3/s', '空气放气后放气量（×(1+2RH)）'), 'outgas_n2_pa_m3_s': (OUTGAS_N2_PA_M3_S, 'Pa*m^3/s', '氮气放气后放气量'),
            'wet_outgas_pa_m3_s': (WET_OUTGAS_PA_M3_S, 'Pa*m^3/s', '含水样品放气量'), 'vent_conductance_m3_s': (VENT_CONDUCTANCE_M3_S, 'm^3/s', '放气阀流导'),
            'lv_orifice_m3_s': (LV_ORIFICE_M3_S, 'm^3/s', 'LV孔板抽速'), 'pla_conductance_m3_s': (PLA_CONDUCTANCE_M3_S, 'm^3/s', '压差光阑流导'),
            'emission_sat_a': (EMISSION_SAT_A, 'A', '饱和发射电流'), 'filament_life_s': (FILAMENT_LIFE_S, 's', '钨灯丝平均寿命300h（=1.08×10⁶s）'),
            'beta_reduced': (BETA_R, 'A/(m^2 sr V)', '钨灯丝约化亮度'), 'energy_spread_ev': (ENERGY_SPREAD_EV, 'eV', '钨灯丝能量展宽'),
            'surface_capacitance_f': (SURFACE_CAPACITANCE_F, 'F', '绝缘相荷电区域对地电容'), 'se_collection': (SE_COLLECTION, '1', 'SED收集效率'), 'bed_dead_layer_ev': (BED_DEAD_LAYER_EV, 'eV', 'BED死层能量损失'),
            'gas_sigma30_m2': (GAS_SIGMA30_M2, 'm^2', '30kV空气散射截面'), 'eds_takeoff_rad': (math.radians(35.0), 'rad', 'EDS出射角35°（WD 10mm；工程假设）'),
            'kramers_k': (KRAMERS_K, '1/eV', 'Kramers常数'), 'eds_fwhm_mn_model_ev': (fwhm_ev(5898.75, EDS_PROCESS_TIMES['T4']['noise_ev']), 'eV', '模型Mn Kα分辨率（T4）'),
            'door_travel_m': (DOOR_TRAVEL_M, 'm', '门拉出行程'), 'pole_clearance_m': (POLE_CLEARANCE_M, 'm', '样品与极靴最小间隙')}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=[_SIM], uncertainty='工程值')
    mechanism = dict(
        component_ids=['electron_gun', 'hv_power_supply', 'condenser_lens', 'objective_lens', 'scan_coil_section', 'pole_piece', 'chamber_body', 'turbo_molecular_pump',
                       'rotary_pump', 'vacuum_valve_block', 'vent_valve', 'lv_valve', 'sed_detector', 'bse_detector', 'eds_finger', 'eds_detector_housing', 'stage_z',
                       'stage_tilt', 'stage_x', 'stage_y', 'stage_rot', 'specimen_holder', 'chamber_door'],
        state_ids=list(slots), meaning_zh='JSM-IT200 LA扫描电镜：真空（粗抽/涡轮泵/LV针阀/放气）、钨灯丝电子枪、探针光学、SE/BSE信号与噪声、荷电与LV散射、EDS谱生成与无标样ZAF定量、样品台与换样',
        implementation='lumped',
        equations=['V dP/dt = Q_leak + Q_outgas(t) + K (P_atm - P) - S P  (exact exponential step; K = door gap / vent / needle valve, S = RP, TMP or LV orifice)',
                   'd_p^2 = 4 I/(pi^2 beta a^2) + (C_s a^3/2)^2 + (C_c a dE/E0)^2 + (1.22 lambda/a)^2 at the optimum a ; C_s = c_s (WD + g) ; resolution = 0.6 d_p',
                   'eta = sum_i w_i C(Z_i) E^m(Z_i) (Hunger-Kuechler) x (2/(1+cos theta))^(9/sqrt Zbar) ; delta = 1.28 delta_m r^-0.67 (1 - exp(-1.614 r^1.67))/cos theta (total SE yield, r = E/E_m)',
                   'charging (uncoated insulator, HV, per phase k): E_land = E0 - V_k ; C dV_k/dt = I (1 - eta(E_land, theta) - delta(E_land, theta)) towards V_eq = E0 - E2(theta), sigma(E2) = 1',
                   'tilted specimen (height h, tilt t about X through the holder top): beam meets the top face h tan t from its centre, WD = 48 mm - z - h/cos t',
                   'BED: n_BSE = eta (sin^2 th_out - sin^2 th_in), gain = (k_E E - E_dl)/3.64 eV ; SED: y = 0.45 delta + 0.1 eta ; SNR from Poisson + amplifier noise',
                   'n_ion = N_A w/A int_{Ec}^{E0} Q(E)/(dE/drho s) dE x R ; Q = 6.51e-20 n_s b_s ln U/(U Ec^2) ; dE/drho s = 78500 (Z/A)/E ln(1.166 (E + 0.85 J)/J)',
                   'I_det = N_e n_ion omega p f(chi) eps(E) Omega/(4 pi) ; f = 1/((1 + chi/sigma)(1 + h chi/(sigma (1+h)))) ; N_cont = k Zbar (E0 - E)/E',
                   'FWHM^2 = noise^2 + 5.545 F eps E ; live = exp(-ICR tau) ; counts ~ Poisson ; Duane-Hunt: N(E) E / eps(E) ~ k (E_DH - E) near the end, extrapolated',
                   'quant: C_i = norm( A_i / P_i(C) ), P_i = omega p n_s b_s (U0-1)^1.67 / (A_i (Z/A) ln(1.166 (E0 + Ec)/(2 J))) (1 - 0.4 eta (1 - 1/U0)) f_Philibert(h) eps'],
        parameters=params, source_ids=[_SIM, _FIT, _LIBS, _CAT, _MAN, _IA, _PHYS], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_jsmit200_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export(SLUG, caps, slots, {'jsmit200_sem_eds': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE), 'specimen.provide': (SPECIMEN, SPECIMEN_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if v['reference']:
            slots[k]['reference_conditions'] = v['reference']
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'jsmit200_sem_eds': mechanism})

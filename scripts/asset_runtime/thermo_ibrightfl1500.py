"""Runtime of CHEM-165 instrument.thermo_ibrightfl1500 - Thermo Fisher Invitrogen iBright FL1500 gel and blot imaging system.

Process model (SI; host clock only - step(dt_s) is the only way time advances):
  - instrument: rear power switch, CCD cooling after power-up (ready when within 2 K of the set point), automatic drawer (motor, ~4 s) with the
    automatic rotating stage (+-10 deg; the stage squares a skewed sample when the drawer closes unless the camera settings are locked), mechanical
    zoom 1-2x (camera moves towards the stage, field of view 22.5 x 18.0 cm / zoom) and digital zoom 1-4x (crop), autofocus, binning 1x1 ... 8x8;
  - modes (user guide): Chemi Blots (no illumination, open emission; optional membrane channel; Smart Range HDR), Fluorescent Blots (up to four dyes on
    EX1/EM1 ... EX5/EM5 plus the membrane channel), Nucleic Acid Gels and Protein Gels (green 490-520 nm transilluminator + EM2 568-617 nm, or the
    visible channel: epi white LED with the ND filter, 400-700 nm), Universal (any of these);
  - image formation per channel: fluorescence photons per molecule = sigma(lambda) phi(lambda) over the excitation band (LED irradiance, FPbase
    excitation / absorption spectrum x the molar extinction coefficient, sigma = epsilon ln10 / N_A) x quantum yield x lens collection (NA^2 / 4 with
    the object-side NA of the 25 mm f/0.95 lens at the working distance of the zoom) x lens and filter transmission x the emission-band / CCD-QE
    weighted fraction of the FPbase emission spectrum; spectral crosstalk follows from the spectra of all dyes present; chemiluminescence = HRP x
    substrate photon rate with first-order substrate decay; visible = white light through the stained band (10^-A); background = substrate / gel
    autofluorescence and free stain, plus the out-of-band filter leakage;
  - camera: Poisson shot noise, dark current (doubling per 6.3 K, cooled), read noise per binned pixel, fixed gain (full well -> 65 535 ADU at 1x1),
    16-bit clipping (65 535 = saturated), binning sums charge, digital zoom crops; Smart Exposure: test exposure scaled to put the brightest pixel of
    the region at 80 % of full scale; Smart Range HDR (chemi): short + long exposure merged;
  - analysis (user guide p.28-29): lanes and bands found from the image; Volume, Density, Local Background Corrected Volume / Density (2-pixel
    surround), Migration Distance (Rf), Band Purity, molecular weight from a marker lane, standard-curve quantification with the linear range and
    saturation flags; the evaluator reads the sample truth (sample.inspect).
Generic schema / capability / session framework follows the CHEM-164 runtime.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.thermo_ibrightfl1500"
CATALOG_ID = "CHEM-165"
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch03/t4_measure/thermo_ibrightfl1500'
FLUOR_LIB_PATH = ASSET_DIR / 'fluorophore_library.json'
N_A = 6.02214076e23
H_PLANCK, C_LIGHT = 6.62607015e-34, 2.99792458e8

# ---------------------------------------------------------------- manufacturer values (workbook row 170, brochure Table 2/3, user guide)
ADC_MAX = 65535                              # 16-bit
MEGAPIXELS = 9.1
SENSOR_PX = (3388, 2712)                    # 9.19 MP at the 1.25 aspect of the 22.5 x 18.0 cm field (engineering choice of the array)
FOV_M = (0.225, 0.180)                      # field of view at zoom 1 (W x D)
FOCAL_M, F_NUMBER = 0.025, 0.95
MECH_ZOOM, DIG_ZOOM = (1.0, 2.0), (1.0, 4.0)
BINNINGS = [1, 2, 3, 4, 5, 6, 8]
STAGE_ROT_RAD = 10 * math.pi / 180
MAX_DYES = 4
FILTERS_NM = {                              # (excitation band, emission band) of the FL1500 channels (brochure Table 2 / user guide Table 4)
    'EX1': ((455, 485), (508, 557)), 'EX2': ((515, 545), (568, 617)), 'EX3': ((608, 632), (675, 720)), 'EX4': ((610, 660), (710, 730)),
    'EX5': ((745, 765), (800, 850)), 'GREEN_TRANS': ((490, 520), (568, 617)), 'VISIBLE': ((400, 700), (400, 700)), 'CHEMI': (None, (380, 700)),
}
MODES = ['chemi_blots', 'fluorescent_blots', 'nucleic_acid_gels', 'protein_gels', 'universal']
MODE_CHANNELS = {'chemi_blots': ['CHEMI', 'VISIBLE'], 'fluorescent_blots': ['EX1', 'EX2', 'EX3', 'EX4', 'EX5', 'VISIBLE'],
                 'nucleic_acid_gels': ['GREEN_TRANS', 'VISIBLE'], 'protein_gels': ['GREEN_TRANS', 'VISIBLE'],
                 'universal': ['EX1', 'EX2', 'EX3', 'EX4', 'EX5', 'GREEN_TRANS', 'VISIBLE', 'CHEMI']}
RATED_POWER_W = 410.0
AMBIENT_K = (288.15, 303.15)                 # 15-30 C

# ---------------------------------------------------------------- engineering values (not published)
PIXEL_PITCH_M = 3.69e-6                     # 3388 x 3.69 um = 12.5 mm sensor width
FULL_WELL_E = 18000.0
GAIN_E_ADU = FULL_WELL_E / ADC_MAX
READ_NOISE_E = 5.0
DARK_E_S_COOLED = 0.004                      # e-/px/s at the cooled set point
DARK_DOUBLING_K = 6.3
CCD_DELTA_K = 30.0                          # set point below the room
CCD_TAU_S = 90.0
DARK_REF_K = 295.15 - 30.0                  # the dark-current figure refers to the cooled CCD at the nominal 22 C room
CCD_HISTORY_S = 1260.0                      # CCD anchors kept for exposures still being integrated (2 x the longest exposure + overheads)
LENS_T, FILTER_T, FILTER_OD = 0.85, 0.90, 6.0
QE_TABLE = [(350e-9, 0.25), (400e-9, 0.45), (450e-9, 0.62), (500e-9, 0.72), (540e-9, 0.75), (600e-9, 0.68), (650e-9, 0.55), (700e-9, 0.42), (750e-9, 0.30),
            (800e-9, 0.18), (850e-9, 0.09), (900e-9, 0.04), (1000e-9, 0.005)]
LED_IRRADIANCE = {'EX1': 25.0, 'EX2': 20.0, 'EX3': 15.0, 'EX4': 15.0, 'EX5': 10.0, 'GREEN_TRANS': 40.0, 'VISIBLE': 2.0}   # W/m2 at the stage inside each band
DRAWER_S = 4.0
DRAWER_TRAVEL = 0.30                        # drawer open: the whole stage glass in front of the body (interface_definition)
GLASS_WD = (0.240, 0.200)                   # transilluminator glass on the turntable (covers the 22.5 x 18 cm field)
CAPTURE_OVERHEAD_S = 1.5                    # filter wheels, LED switching and readout per channel
TEST_EXPOSURE_S = 0.2
SMART_TARGET = 0.80
EXPOSURE_RANGE_S = (0.001, 600.0)
HRP_PHOTONS_S = {'supersignal_west_pico_plus': 40.0, 'supersignal_west_femto': 400.0, 'supersignal_west_dura': 120.0, 'generic_ecl': 8.0}
SUBSTRATE_TAU_S = {'supersignal_west_pico_plus': 6 * 3600.0, 'supersignal_west_femto': 8 * 3600.0, 'supersignal_west_dura': 24 * 3600.0, 'generic_ecl': 1 * 3600.0}
CHEMI_PEAK_M, CHEMI_FWHM_M = 425e-9, 70e-9  # luminol / HRP emission band (literature, approximate)
STAIN_A_M2_KG = {'coomassie_r250': 1.2e3, 'coomassie_g250_colloidal': 1.6e3, 'silver': 6.0e3, 'ponceau_s': 0.5e3, 'prestained_marker': 3.0e3}   # absorbance per kg/m2
DYE_PER_BP = 0.2                            # intercalator molecules per base pair when stained to saturation
BP_MASS_KG = 650.0 / N_A * 1e-3              # average mass of one base pair (650 g/mol)
BOUND_QY = {'ethidium_bromide': 0.15, 'sybr_safe': 0.50, 'sybr_gold': 0.60, 'sybr_green': 0.80}
PROTEIN_STAIN_DYE_PER_KG = {'sypro_ruby_protein_gel_stain': 2.0e22, 'sypro_orange_protein_gel_stain': 1.0e22}   # stain molecules per kg protein
BACKGROUND = {   # fluorescent background of the support, expressed as an equivalent dye surface density (mol/m2) per channel
    'nitrocellulose': {'EX1': 4e-10, 'EX2': 2.5e-10, 'EX3': 2e-11, 'EX4': 1.5e-11, 'EX5': 5e-12, 'GREEN_TRANS': 3e-10},
    'pvdf_lf': {'EX1': 1.5e-10, 'EX2': 1e-10, 'EX3': 1e-11, 'EX4': 8e-12, 'EX5': 3e-12, 'GREEN_TRANS': 1.2e-10},
    'polyacrylamide_gel': {'EX1': 1e-10, 'EX2': 6e-11, 'EX3': 8e-12, 'EX4': 6e-12, 'EX5': 2e-12, 'GREEN_TRANS': 2e-10},
    'agarose_gel': {'EX1': 1.2e-10, 'EX2': 8e-11, 'EX3': 1e-11, 'EX4': 8e-12, 'EX5': 3e-12, 'GREEN_TRANS': 2.5e-10},
}
CHEMI_BACKGROUND_PHOTONS_M2_S = 2.0e9       # substrate glow of a blot (engineering)
STRAY_FRACTION = 2e-8                       # excitation photons scattered by the sample that leak through the emission filter (per incident photon)
ANALYSIS_MAX_PX = 768                       # stored / analysed image grid: at most 768 px on the long side (blocks of m x m binned camera pixels)
GALLERY_MAX, SAMPLES_MAX = 30, 20
BIAS_ADU = 100.0                            # camera offset (subtracted by the analysis)
POWER_IDLE_W, POWER_COOL_W, POWER_LED_W = 90.0, 30.0, 20.0   # electrical draw: idle, CCD cooling at full load, LED during an exposure (rated 410 W)
CCD_OFF_TAU_S = 300.0                       # CCD warms back to the room when unpowered
READY_BAND_K = 2.0
REF_EPS_M2_MOL, REF_QY = 1.0e4, 0.5         # reference dye for the support background (100 000 M-1 cm-1, QY 0.5, fully in band)
GLASS_BG_FRACTION = 0.1                     # stage glass background relative to a membrane
REFLECT_SAMPLE, REFLECT_STAGE = 0.8, 0.3    # white-light reflectance of a membrane / gel and of the stage (visible channel)
EDGE_M = 0.3e-3                             # soft lane edges
SMUDGE_BG = 5.0                             # stage_smudge fault: background x5 and scatter x20
AUTOZOOM_MARGIN = 1.15
HDR_RATIO = 16.0
ROLL_M = 8e-3                               # rolling-ball (rolling minimum) window of the lane background: wider than a band's +-3 sigma
LANE_MARGIN_M = 0.6e-3                     # the lane rectangle extends 0.6 mm beyond the half-maximum edges (engineering)
LBC_RING_M = 2 * 0.225 / 3388               # local-background surround: 2 camera pixels at zoom 1, binning 1, fixed in object space (zoom invariant)
USER_FRAME_MIN_M = 2e-3                     # Edit analysis frame: smallest frame side (engineering; the guide gives none)
MAX_FRAMES = 4                              # the system identifies 1-4 analysis frames (user guide p.28)


class ActionRejected(ValueError):
    """A capability call refused with a declared error code (no state change)."""


# ---------------------------------------------------------------- schema helpers (as CHEM-164)
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


BOOL = {'type': 'boolean'}
FREEOBJ = {'type': 'object'}
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
            req = set(s.get('required', s['properties']))
            if not req <= set(v) or not set(v) <= set(s['properties']):
                raise ActionRejected('INVALID_ARGUMENT')
            for key, value in v.items():
                validate(s['properties'][key], value)
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


# ---------------------------------------------------------------- template library and optics
@functools.lru_cache(maxsize=1)
def fluor_lib():
    return json.loads(FLUOR_LIB_PATH.read_text())


@functools.lru_cache(maxsize=None)
def _grid():
    g = fluor_lib()['grid_m']
    return np.arange(g['start'], g['stop'] + 0.5 * g['step'], g['step'])


def qe(lam):
    t = np.array(QE_TABLE)
    return np.interp(lam, t[:, 0], t[:, 1], left=0.0, right=0.0)


def band_mask(band_nm):
    lam = _grid()
    return ((lam >= band_nm[0] * 1e-9) & (lam <= band_nm[1] * 1e-9)).astype(float)


def working_geometry(mech_zoom):
    """Object-side numerical aperture and object pixel size of the 25 mm f/0.95 lens at mechanical zoom z (field of view 22.5 x 18.0 cm / z)."""
    m = SENSOR_PX[0] * PIXEL_PITCH_M / (FOV_M[0] / mech_zoom)
    u = FOCAL_M * (1 + 1 / m)
    na = (FOCAL_M / F_NUMBER / 2) / u
    return dict(magnification=m, object_distance_m=u, na=na, collection=na * na / 4, object_pixel_m=PIXEL_PITCH_M / m)


@functools.lru_cache(maxsize=None)
def dye_response(dye_id, channel):
    """Detected photons per second per molecule of dye in a channel at zoom 1 (collection applied separately): the excitation-band photon flux x
    sigma(lambda) of the FPbase excitation / absorption spectrum x quantum yield x the emission-band / QE weighted fraction of the emission spectrum
    (x filter / lens transmission); out-of-band emission and excitation leak with the filter optical density."""
    e = fluor_lib()['substances'][dye_id]
    lam = _grid()
    ex_band, em_band = FILTERS_NM[channel]
    if ex_band is None:
        return 0.0
    ex = np.array(e['spectra'][e['excitation_spectrum_key']])
    em = np.array(e['spectra']['em'])
    eps = e['ext_coeff_m2_mol'] or 5000.0                                     # 50 000 M^-1 cm^-1 when FPbase gives none (assumption)
    qy = BOUND_QY.get(dye_id) or e['quantum_yield'] or 0.3
    sigma = eps * math.log(10) / N_A * ex                                    # m2 per molecule
    exm = band_mask(ex_band) * FILTER_T + (1 - band_mask(ex_band)) * 10 ** (-FILTER_OD)
    E = LED_IRRADIANCE[channel] / max(1e-12, (ex_band[1] - ex_band[0]) * 1e-9)   # W/m2 per m of wavelength inside the band
    phi = E * lam / (H_PLANCK * C_LIGHT) * exm                                # photons / (m2 s m)
    k_abs = float(np.trapezoid(sigma * phi, lam))
    emm = band_mask(em_band) * FILTER_T + (1 - band_mask(em_band)) * 10 ** (-FILTER_OD)
    frac = float(np.trapezoid(em * emm * qe(lam), lam) / max(np.trapezoid(em, lam), 1e-30))
    return k_abs * qy * LENS_T * frac


def chemi_fraction():
    lam = _grid()
    em = np.exp(-0.5 * ((lam - CHEMI_PEAK_M) / (CHEMI_FWHM_M / 2.355)) ** 2)
    band = band_mask(FILTERS_NM['CHEMI'][1])
    return float(np.trapezoid(em * band * qe(lam), lam) / np.trapezoid(em, lam)) * LENS_T


def visible_fraction():
    lam = _grid()
    band = band_mask(FILTERS_NM['VISIBLE'][1])
    return float(np.trapezoid(band * qe(lam), lam) / np.trapezoid(band, lam)) * LENS_T * FILTER_T


# ---------------------------------------------------------------- capabilities and state slots
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=c, function_zh=meaning, visibility=visibility, kind=kind)


SID = dict(type='string', minLength=1, maxLength=40, pattern='^[A-Za-z0-9_.-]+$')
LIBID = dict(type='string', minLength=1, maxLength=80)
CHANNELS = list(FILTERS_NM)
SUPPORTS = list(BACKGROUND)
VISIBLE_STAINS = list(STAIN_A_M2_KG)
PROTEIN_FLUOR_STAINS = list(PROTEIN_STAIN_DYE_PER_KG)
NUCLEIC_STAINS = list(BOUND_QY)
SUBSTRATES = list(HRP_PHOTONS_S)
PLANT_EXAMPLE = dict(room_k=295.15, mains_voltage_v=230.0, noise_seed=1)
PLANT = obj(dict(room_k=number('K', 283.15, 313.15), mains_voltage_v=number('V', 0.0, 264.0), noise_seed=integer(0, 2 ** 31 - 1)))
BAND = obj(dict(y_m=number('m', 0.0, 0.20), sigma_m=number('m', 1e-4, 5e-3), label=LIBID, amount_mol=nullable(number('mol', 0.0, 1e-6)),
                amount_kg=nullable(number('kg', 0.0, 1e-5)), molar_mass_kg_mol=nullable(number('kg/mol', 0.5, 1000.0))),
           optional=('amount_mol', 'amount_kg', 'molar_mass_kg_mol'))
LANE = obj(dict(x_m=number('m', 0.0, 0.24), width_m=number('m', 1e-3, 0.02), bands=array(BAND, 0, 40)))
SAMPLE = obj(dict(sample_id=SID, support=enum(SUPPORTS), width_m=number('m', 0.02, 0.24), height_m=number('m', 0.02, 0.20), lanes=array(LANE, 1, 26),
                  substrate=nullable(enum(SUBSTRATES)), free_stain_mol_m2=number('mol/m2', 0.0, 1e-6)), optional=('substrate', 'free_stain_mol_m2'))
SAMPLE_EXAMPLE = dict(sample_id='blot_1', support='pvdf_lf', width_m=0.086, height_m=0.068,
                      lanes=[dict(x_m=0.010 + 0.0075 * i, width_m=0.005, bands=[dict(y_m=0.030, sigma_m=0.0006, label='alexa_fluor_647', amount_mol=2e-14 * (i + 1))])
                             for i in range(3)], substrate=None, free_stain_mol_m2=0.0)
CHANNEL_SEL = obj(dict(channel=enum(CHANNELS), dye=nullable(LIBID)))
CAMERA = obj(dict(binning=integer(1, 8), zoom=number('1', 1.0, 8.0), auto_focus=BOOL, lock=BOOL, rotation_rad=nullable(number('rad', -STAGE_ROT_RAD, STAGE_ROT_RAD))),
             optional=('rotation_rad',))
OPERATION = obj(dict(operation_id=TEXT))
STATS = obj(dict(channel=TEXT, dye=nullable(TEXT), exposure_s=number('s'), min_adu=INT, max_adu=INT, mean_adu=number('1'), saturated_px=INT, saturated_fraction=number('1'),
                 preview=array(array(integer(0, ADC_MAX), 1, 64), 1, 64)))
IMAGE = obj(dict(image_id=TEXT, captured_s=number('s'), mode=enum(MODES), binning=INT, zoom=number('1'), hdr=BOOL, width_px=INT, height_px=INT, object_pixel_m=number('m'),
                 hdr_scale=nullable(number('1')), channels=array(STATS, 1, 6), warnings=array(TEXT, 0, 12), template_source=array(TEXT, 0, 8)))
BANDROW = obj(dict(band=INT, y_m=number('m'), rf=number('1'), volume=number('1'), density=number('1'), lbc_volume=number('1'), lbc_density=number('1'), area_px=INT,
                   band_purity_pct=number('1'), lane_purity_pct=number('1'), molar_mass_kg_mol=nullable(number('kg/mol')), saturated=BOOL, top_m=number('m'),
                   bottom_m=number('m')))
LANEROW = obj(dict(lane=INT, frame=INT, x_m=number('m'), width_m=number('m'), total_lane_volume=number('1'), background_volume=number('1'), adjusted_total_lane_volume=number('1'),
                   normalization_factor=nullable(number('1')), bands=array(BANDROW, 0, 60)))
ANALYSIS = obj(dict(image_id=TEXT, channel=TEXT, sensitivity=number('1'), inverted=BOOL, hdr_scale=nullable(number('1')), frame_m=array(number('m'), 4, 4),
                    frames_m=array(array(number('m'), 4, 4), 1, MAX_FRAMES), lanes=array(LANEROW, 0, 30), marker_lane=nullable(INT), warnings=array(TEXT, 0, 12)))
QUANT = obj(dict(image_id=TEXT, channel=TEXT, slope=number('1/kg'), intercept=number('1'), r2=number('1'), standards_used=INT, linear_range=array(number('kg'), 2, 2),
                 unknowns=array(obj(dict(lane=INT, band=INT, amount_kg=nullable(number('kg')), flag=TEXT)), 0, 200), warnings=array(TEXT, 0, 12)))
RECORD = obj(dict(record_id=TEXT, kind=enum(['smart_exposure', 'capture']), status=enum(['running', 'completed', 'failed', 'cancelled']), started_s=number('s'),
                  finished_s=nullable(number('s')), image_id=nullable(TEXT), recommended_exposure_s=obj({}, ()), warnings=array(TEXT, 0, 12)))
RECORD['properties']['recommended_exposure_s'] = dict(type='object', additionalProperties=number('s'), maxProperties=6)
EVENT = obj(dict(time_s=number('s'), kind=enum(['power', 'drawer', 'camera', 'capture', 'analysis', 'alarm', 'fault']), code=TEXT, message_zh=TEXT))
LEDGER_KEYS = ['electrical_energy_j', 'exposure_time_s', 'images', 'led_energy_j', 'photons_detected']
LEDGER = obj({k: number('J' if k.endswith('_j') else 's' if k.endswith('_s') else '1') for k in LEDGER_KEYS})

cap('plant.configure', PLANT, obj(dict(plant=PLANT)), PLANT_EXAMPLE, ['base_plate'], '宿主声明实验室条件：室温、电源电压、噪声种子', 'evaluator', 'simulation')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), ['switch_power', 'rear_panel'], '主电源开/关（开机后CCD制冷到设定点）')
cap('power.press_switch', obj({}), obj(dict(enabled=BOOL)), {}, ['switch_power'], '按一下后面板电源开关（翻转开/关）')
cap('mode.select', obj(dict(mode=enum(MODES))), obj(dict(mode=TEXT, channels=array(TEXT, 0, 6))), dict(mode='fluorescent_blots'), ['touch_screen'],
    '选择成像模式：化学发光印迹、荧光印迹、核酸胶、蛋白胶、通用（重设该模式的默认通道）')
cap('drawer.open', obj({}), OPERATION, {}, ['sample_drawer', 'touch_screen'], '触屏打开自动抽屉（约4 s；不要用手推）')
cap('drawer.close', obj({}), OPERATION, {}, ['sample_drawer', 'sample_stage', 'touch_screen'], '触屏关闭抽屉（约4 s）；关上后自动旋转样品台对正样品、自动变焦与对焦（未锁定时）')
cap('sample.provide', SAMPLE, obj(dict(sample_id=TEXT, location=TEXT)), SAMPLE_EXAMPLE, ['sample_stage'],
    '在实验台上准备一块胶或膜：支持物、尺寸、泳道与条带（每条带的标记：荧光染料、HRP、可见/荧光蛋白染料或核酸染料及其量；真值只对评测者可见）', 'agent', 'robot_skill')
cap('sample.place', obj(dict(sample_id=SID, skew_rad=number('rad', -0.35, 0.35))), obj(dict(sample_id=TEXT)), dict(sample_id='blot_1', skew_rad=0.05), ['sample_stage', 'stage_glass'],
    '把胶/膜放到打开抽屉的透射玻璃中心（可有摆放偏角）', 'agent', 'robot_skill')
cap('sample.remove', obj(dict(sample_id=SID)), obj(dict(sample_id=TEXT)), dict(sample_id='blot_1'), ['sample_stage'], '从打开的抽屉取下样品放回实验台', 'agent', 'robot_skill')
cap('channels.set', obj(dict(channels=array(CHANNEL_SEL, 1, 6))), obj(dict(channels=array(TEXT, 1, 6), warnings=array(TEXT, 0, 8))),
    dict(channels=[dict(channel='EX3', dye='alexa_fluor_647'), dict(channel='VISIBLE', dye=None)]), ['touch_screen'],
    '选择通道（荧光通道最多4种染料，另可加可见/膜通道）；EX3/EM3与EX4/EM4同用会告警（厂家：光谱重叠）')
cap('exposure.set', obj(dict(channel=enum(CHANNELS), exposure_s=number('s', *EXPOSURE_RANGE_S))), obj(dict(channel=TEXT, exposure_s=number('s'))), dict(channel='EX3', exposure_s=5.0),
    ['touch_screen', 'display_exposure_digits'], '手动设定某通道的曝光时间')
cap('camera.set', CAMERA, CAMERA, dict(binning=2, zoom=1.0, auto_focus=True, lock=False), ['touch_screen'],
    '相机设定：像素合并（1、2、3、4、5、6、8）、变焦1–8×（1–2×机械、其余数字）、自动对焦、锁定相机设置（锁定后抽屉开关不再自动旋转/变焦）')
cap('smart_exposure.run', obj(dict(roi=nullable(array(number('m', -0.12, 0.12), 4, 4))), optional=('roi',)), OPERATION, dict(roi=None), ['touch_screen', 'display_exposure_digits'],
    'Smart Exposure：拍一系列短曝光，给出每个通道的推荐曝光（最亮像素到满量程80 %；可限定感兴趣区[x0,y0,x1,y1]）；不是正式图像')
cap('capture.run', obj(dict(hdr=BOOL)), OPERATION, dict(hdr=False), ['touch_screen', 'sample_stage'], '按当前通道与曝光拍摄并存入图库（hdr：化学发光模式的Smart Range HDR）')
LANE_CENTRES = nullable(array(number('m', 0.0, 0.24), 1, 30))
FRAMES = nullable(array(obj(dict(rect_m=array(number('m', -0.12, 0.12), 4, 4), lane_centres_m=LANE_CENTRES), optional=('lane_centres_m',)), 1, MAX_FRAMES))
LANE_BANDS = nullable(array(obj(dict(lane=integer(1, 30), bands_m=array(array(number('m', 0.0, 0.24), 2, 2), 0, 60))), 1, 30))
ANALYSIS_EDITS = dict(lane_centres_m=LANE_CENTRES, frames_m=FRAMES, lane_bands_m=LANE_BANDS)
cap('image.analyze', obj(dict(image_id=TEXT, channel=enum(CHANNELS), sensitivity=number('1', 0.0, 1.0), marker_lane=nullable(integer(1, 30)),
                               marker_molar_mass_kg_mol=nullable(array(number('kg/mol', 0.5, 20000.0), 1, 20)), **ANALYSIS_EDITS),
                          optional=('marker_lane', 'marker_molar_mass_kg_mol') + tuple(ANALYSIS_EDITS)),
    ANALYSIS, dict(image_id='IMG0001', channel='EX3', sensitivity=0.5, marker_lane=None, marker_molar_mass_kg_mol=None), ['touch_screen'],
    '分析图像：自动找分析框、泳道与条带（或按给定泳道中心，即“调整泳道”；按给定的1–4个分析框，即“编辑分析框”；按给定泳道的条带上下边，即“调整条带”），'
    '输出体积、密度、局部背景校正体积/密度、迁移率Rf、条带/泳道纯度与条带上下边；给定分子量标准泳道时估算同一分析框内其它泳道的分子量'
    '（DNA梯度按片段摩尔质量=bp×0.650 kg/mol给出）', 'agent', 'analysis')
cap('image.quantify', obj(dict(image_id=TEXT, channel=enum(CHANNELS), standards=array(obj(dict(lane=integer(1, 30), band=integer(1, 60), amount_kg=number('kg', 0.0, 1e-5))), 2, 20),
                                sensitivity=number('1', 0.0, 1.0), **ANALYSIS_EDITS), optional=tuple(ANALYSIS_EDITS)), QUANT,
    dict(image_id='IMG0001', channel='EX3', standards=[dict(lane=1, band=1, amount_kg=1e-10), dict(lane=2, band=1, amount_kg=2e-10)], sensitivity=0.5), ['touch_screen'],
    '浓度标定：用已知量的标准条带拟合背景校正体积–质量直线，估算其它条带的量并标出线性范围外与饱和的条带', 'agent', 'analysis')
cap('image.export', obj(dict(image_id=TEXT, fmt=enum(['g2i', 'tiff', 'jpg', 'png']), analysis_16bit=BOOL)), obj(dict(image_id=TEXT, file=TEXT, bytes=INT)),
    dict(image_id='IMG0001', fmt='tiff', analysis_16bit=True), ['side_ports', 'touch_screen'], '导出图像（G2i、TIFF、JPG、PNG；分析用16位未调整图像）到U盘', 'agent', 'robot_skill')
cap('image.delete', obj(dict(image_id=TEXT)), obj(dict(image_id=TEXT, images_left=INT)), dict(image_id='IMG0001'), ['touch_screen'], '把图库中的一张图像移到回收站并删除（释放图库位置）')
cap('measurement.result', obj(dict(image_id=TEXT)), IMAGE, dict(image_id='IMG0001'), ['touch_screen'], '读一张图像的元数据与每通道统计（最小/最大/均值、饱和像素）和缩略图', 'agent', 'query')
cap('records.list', obj({}), obj(dict(records=array(TEXT, 0, 400), images=array(TEXT, 0, GALLERY_MAX))), {}, ['touch_screen'], '列出操作记录与图库图像', 'agent', 'query')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, 400), dropped=INT)), dict(since_s=0.0), ['base_plate'], '读事件日志', 'agent', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=LEDGER)), {}, ['base_plate'], '读守恒账目（电能、LED能量、曝光时间、图像数、探测光子数）', 'evaluator', 'query')
cap('sample.inspect', obj(dict(sample_id=SID)), obj(dict(sample_id=TEXT, support=TEXT, lanes=FREEOBJ, expected_signal=FREEOBJ)), dict(sample_id='blot_1'), ['sample_stage'],
    '评测者读样品真值：各泳道条带的标记、量与位置，以及各通道的期望信号（无噪声电子数）', 'evaluator', 'query')
cap('provenance.read', obj({}), obj(dict(libraries=array(obj(dict(path=TEXT, sha256=TEXT, count=INT)), 1, 1))), {}, ['base_plate'], '模板库路径与sha256', 'agent', 'query')
cap('fault.inject', obj(dict(fault=enum(['power_loss', 'led_failure', 'drawer_blocked', 'filter_wheel_jam', 'stage_smudge']), active=BOOL)), obj(dict(faults=array(TEXT, 0, 8))),
    dict(fault='stage_smudge', active=True), ['base_plate', 'sample_drawer'], '评测者故障注入：断电、LED失效、抽屉受阻、滤光轮卡住、透射玻璃脏污（背景升高）', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=1.0), ['base_plate'], '推进宿主时钟', 'evaluator', 'simulation')
UNSUPPORTED = {
    'colony.count': '菌落计数应用需琼脂平板样品模型，本资产未建',
    'e_gel_96.image': 'E-Gel 96应用未建模',
    'cloud.connect': 'Thermo Fisher Connect云与Wi-Fi连接未建模（Wi-Fi适配器为选购件）',
    'hardware.connect': '没有真实iBright与分析软件的硬件接口',
}
for _k, _why in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, ['base_plate'], _why, 'agent', 'device_command')
BASE_CAPS = list(CAPS)

# ---------------------------------------------------------------- touch-screen app model (user guide ch.2-5): accounts, settings, live-view light, routines, image
# view adjustments, gallery names, export destinations, custom dyes, band excision, service tools.  Every app control of panel_ui.json calls one of these.
SCREEN_NAME = dict(type='string', minLength=1, maxLength=24, pattern='^[A-Za-z0-9_. -]+$')
PIN = dict(type='string', pattern='^[0-9]{4}$')
NAMING_ASPECTS = ['Mode', 'Binning', 'Custom Field User', 'Exposure Time', 'Sequential Number']
FALSE_COLORS = ['red', 'green', 'blue', 'yellow', 'magenta', 'cyan', 'white', 'gray']
MODE_TEXT = {'chemi_blots': 'Chemi Blots', 'fluorescent_blots': 'Fluorescent Blots', 'nucleic_acid_gels': 'Nucleic Acid Gels', 'protein_gels': 'Protein Gels',
             'universal': 'Universal'}
LADDERS = {                                        # marker standards offered on Add markers (assumption: two common ladders), molar mass in kg/mol
    'Protein ladder 10-250 kDa': [250.0, 130.0, 100.0, 70.0, 55.0, 35.0, 25.0, 15.0, 10.0],
    'DNA ladder 100-15000 bp': [x * 0.650 for x in (15000, 10000, 8000, 7000, 6000, 5000, 4000, 3000, 2000, 1500, 1000, 850, 650, 500, 400, 300, 200, 100)]}
FALSE_RGB = {'red': (235, 50, 40), 'green': (40, 225, 70), 'blue': (70, 120, 255), 'yellow': (245, 225, 40), 'magenta': (235, 70, 235), 'cyan': (60, 225, 235),
             'white': (255, 255, 255), 'gray': (255, 255, 255)}
DEFAULT_FALSE_COLOR = {'EX1': 'green', 'EX2': 'yellow', 'EX3': 'red', 'EX4': 'magenta', 'EX5': 'white', 'GREEN_TRANS': 'gray', 'VISIBLE': 'gray', 'CHEMI': 'gray'}
TIME_ZONES = ['UTC-08:00', 'UTC-05:00', 'UTC+00:00', 'UTC+01:00', 'UTC+04:00', 'UTC+08:00', 'UTC+09:00']
APP_SETTINGS = {                                   # key: (schema, default, user guide page)
    'instrument_name': (dict(type='string', minLength=1, maxLength=24), 'iBright FL1500', 33), 'language': (enum(['English']), 'English', 33),
    'time_zone': (enum(TIME_ZONES), 'UTC+00:00', 33), 'date_format': (enum(['YYYY-MM-DD', 'DD/MM/YYYY', 'MM/DD/YYYY']), 'YYYY-MM-DD', 33),
    'time_format': (enum(['24-hour', '12-hour']), '24-hour', 33), 'sleep_mode': (BOOL, True, 33), 'security_mode': (BOOL, False, 33),
    'naming': (dict(array(enum(NAMING_ASPECTS), 0, 3), uniqueItems=True), [], 33), 'custom_field': (dict(type='string', maxLength=16), '', 33),
    'network_drive_location': (dict(type='string', maxLength=120), '', 34), 'network_drive_domain': (dict(type='string', maxLength=40), '', 34),
    'network_drive_user': (dict(type='string', maxLength=40), '', 34), 'network_drive_password': (dict(type='string', maxLength=40), '', 34),
    'auto_backup': (BOOL, False, 34), 'printer_ip': (dict(type='string', maxLength=40), '', 34), 'paper_size': (enum(['US Letter', 'A4']), 'US Letter', 34),
    'cloud_region': (enum(['Other', 'China']), 'Other', 34)}
VIEW_SETTINGS = {                                  # image display adjustments (user guide Tables 7-8): key: (schema, default)
    'contrast': (enum(['high', 'medium', 'low', 'raw', 'manual']), 'medium'), 'gamma': (number('1', 0.1, 5.0), 1.0), 'black': (integer(0, ADC_MAX), 0),
    'white': (integer(1, ADC_MAX), ADC_MAX), 'invert': (BOOL, False), 'saturation': (BOOL, False), 'channel': (nullable(enum(CHANNELS)), None),
    'hidden_layers': (array(enum(CHANNELS), 0, 6), []), 'view_zoom': (number('1', 1.0, 8.0), 1.0), 'layout': (enum(['2up', '1up']), '2up'),
    'color': (enum(['gray', 'color']), 'gray'), 'auto_enhance': (BOOL, False), 'membrane_overlay': (BOOL, False)}
EXPORT_TYPES = {'publication_tiff': ('tiff', False), 'publication_jpeg': ('jpg', False), 'publication_png': ('png', False), 'analysis_tiff': ('tiff', True),
                'analysis_g2i': ('g2i', True)}
DIAG_S, PIXEL_MAP_S = 600.0, 120.0
PIXEL_MAP_SNR = 5.0                                # Pixel Mapping flags a pixel whose excess dark charge over the 2 min dark frame exceeds 5 sigma of a normal pixel
SHIP_PREP_STATES = ['remove_turntable', 'power_off']
INSTRUMENT_EPOCH = '2026-10-19T08:00:00'          # wall-clock time of simulation time 0 (UTC)
_opt = lambda props: obj(props, optional=tuple(props))   # noqa: E731
cap('user.create', obj(dict(screen_name=SCREEN_NAME, pin=PIN, pin_confirm=PIN)), obj(dict(screen_name=TEXT, admin=BOOL)),
    dict(screen_name='lab_user', pin='1234', pin_confirm='1234'), ['touch_screen'], '创建仪器账户（屏名+4位PIN，确认PIN）并登录；第一个账户为管理员')
cap('user.sign_in', obj(dict(screen_name=SCREEN_NAME, pin=PIN)), obj(dict(screen_name=TEXT)), dict(screen_name='lab_user', pin='1234'), ['touch_screen'],
    '登录到用户账户（PIN被管理员删除后，本次输入的PIN成为新PIN）；图像存入该用户的图库')
cap('user.sign_out', obj({}), obj(dict(screen_name=nullable(TEXT))), {}, ['touch_screen'], '退出登录（之后为访客：图像存入公共访客图库）')
cap('user.manage', obj(dict(screen_name=SCREEN_NAME, action=enum(['make_admin', 'delete_pin', 'delete_account']))), obj(dict(screen_name=TEXT, action=TEXT)),
    dict(screen_name='lab_user', action='delete_pin'), ['touch_screen'], '管理员管理账户：标准用户改为管理员（不能改回）、删除PIN、删除账户（连同其图库图像）')
cap('settings.configure', _opt({k: v[0] for k, v in APP_SETTINGS.items()}), obj(dict(settings=FREEOBJ)), dict(sleep_mode=False), ['touch_screen'],
    '仪器设置：名称、语言、时区与日期/时间格式、休眠、安全模式（管理员）、文件命名规则、网络驱动器与打印机配置、云区域')
cap('settings.reset', obj(dict(kind=enum(['user_tips', 'factory_defaults']))), obj(dict(kind=TEXT)), dict(kind='user_tips'), ['touch_screen'],
    '重置访客的用户提示（同时清除访客图库）或恢复出厂设置（下次开机生效：清除账户与设置）')
cap('settings.backup', obj({}), obj(dict(file=TEXT)), {}, ['touch_screen', 'side_ports'], '备份系统设置到U盘（不含图像）')
cap('settings.restore', obj({}), obj(dict(settings=FREEOBJ)), {}, ['touch_screen', 'side_ports'], '从U盘上的备份恢复系统设置')
cap('live.set_trans_light', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), ['touch_screen'],
    '实时视图用绿光透射台照明（Use Trans Light；默认白光Epi-LED）')
cap('routine.run', obj(dict(kind=enum(['multi_exposure', 'signal_accumulation']), first_s=nullable(number('s', *EXPOSURE_RANGE_S)),
                            total_s=nullable(number('s', *EXPOSURE_RANGE_S)), count=nullable(integer(2, 10))), optional=('first_s', 'total_s', 'count')), OPERATION,
    dict(kind='multi_exposure'), ['touch_screen', 'sample_stage'],
    '拍摄程序：多重曝光（当前曝光的1/4、1/2、1、2、4倍各一张）或信号累积（单通道：首张时间、累积总时间与张数，第k张为前k帧之和）')
cap('image.view', dict(_opt(dict({k: v[0] for k, v in VIEW_SETTINGS.items()}, image_id=TEXT)), required=['image_id']), obj(dict(image_id=TEXT, view=FREEOBJ)),
    dict(image_id='IMG0001', contrast='high'), ['touch_screen'], '图像显示调整：自动对比（高/中/低/原始）或手动Gamma/白/黑、反相、饱和像素标红、通道与图层、缩放、2UP/1UP、灰度/彩色、Auto Enhance、膜叠加（只影响显示，不改数据）')
cap('image.rename', obj(dict(image_id=TEXT, name=dict(type='string', minLength=1, maxLength=64), comments=dict(type='string', maxLength=200))),
    obj(dict(image_id=TEXT, name=TEXT)), dict(image_id='IMG0001', name='blot_1', comments=''), ['touch_screen'], '导出页Edit：改文件名与注释')
cap('export.run', obj(dict(image_ids=array(TEXT, 1, GALLERY_MAX), destination=enum(['usb', 'network_drive', 'printer', 'connect']), file_type=enum(list(EXPORT_TYPES)),
                           separate_channels=BOOL)), obj(dict(files=array(TEXT, 1, 8 * GALLERY_MAX), bytes=INT)),
    dict(image_ids=['IMG0001'], destination='usb', file_type='analysis_tiff', separate_channels=False), ['touch_screen', 'side_ports'],
    '导出页：选目标（U盘/网络驱动器/网络打印机/Connect）与文件类型（出版用24位TIFF/JPEG/PNG可分通道导出；分析用16位TIFF/G2i），文件名按图库名称', 'agent', 'robot_skill')
cap('analysis.export', obj(dict(image_id=TEXT, channel=enum(CHANNELS), sensitivity=number('1', 0.0, 1.0), report_name=dict(type='string', minLength=1, maxLength=64),
                                marker_lane=nullable(integer(1, 30)), marker_molar_mass_kg_mol=nullable(array(number('kg/mol', 0.5, 20000.0), 1, 20)), **ANALYSIS_EDITS),
                           optional=('marker_lane', 'marker_molar_mass_kg_mol') + tuple(ANALYSIS_EDITS)), obj(dict(files=array(TEXT, 2, 2), bytes=INT, lanes=INT, bands=INT)),
    dict(image_id='IMG0001', channel='EX3', sensitivity=0.5, report_name='report_1'), ['touch_screen', 'side_ports'],
    '分析页Generate report + Export：把分析表（.csv）与报告（.pdf）导出到U盘', 'agent', 'robot_skill')
cap('dye.define', obj(dict(name=dict(type='string', minLength=1, maxLength=24, pattern='^[A-Za-z0-9_ -]+$'), excitation_nm=number('nm', 300.0, 900.0),
                           emission_nm=number('nm', 350.0, 950.0), false_color=enum(FALSE_COLORS))), obj(dict(dye=TEXT)),
    dict(name='my dye', excitation_nm=650.0, emission_nm=700.0, false_color='red'), ['touch_screen'], '自定义染料（名称、发射与激发波长、伪彩）；之后可在Choose a dye中选用')
cap('channels.false_color', obj(dict(channel=enum(CHANNELS), color=enum(FALSE_COLORS))), obj(dict(channel=TEXT, color=TEXT)), dict(channel='EX3', color='red'),
    ['touch_screen'], '改通道（染料）在合成图中的伪彩')
cap('band_excision.start', obj({}), obj(dict(operation_id=nullable(TEXT), on=BOOL)), {}, ['sample_drawer', 'stage_glass', 'touch_screen'], '切胶：打开抽屉并打开绿光透射台以便安全切胶（核酸胶模式的More options）')
cap('band_excision.stop', obj({}), obj(dict(on=BOOL)), {}, ['stage_glass', 'touch_screen'], '结束切胶：关闭透射台（关抽屉时也关闭）')
cap('diagnostics.run', obj({}), OPERATION, {}, ['touch_screen', 'sample_drawer'], '仪器自检（约10 min；测试中抽屉打开再关闭），结束后给出各硬件项的通过/失败报告')
cap('service.pixel_mapping', obj({}), OPERATION, {}, ['touch_screen'], 'Pixel Mapping：长时间暗场曝光找出偏离正常暗值的像元并存入校正（约2 min）；之后的拍摄把这些像元校正为正常暗值')
cap('service.ship_prep', obj(dict(action=enum(['start', 'next', 'cancel']))), obj(dict(operation_id=nullable(TEXT), state=nullable(enum(SHIP_PREP_STATES)))),
    dict(action='start'), ['touch_screen', 'sample_drawer', 'sample_stage'],
    'Ship Prep Mode（服务工具，运输准备）：start打开抽屉并提示取下转台；取下转台后next，提示关机（关机即结束该模式）；cancel退出该模式')
cap('turntable.set_installed', obj(dict(installed=BOOL)), obj(dict(installed=BOOL)), dict(installed=False), ['sample_stage', 'stage_glass'],
    '取下或装回样品转台（连同透射玻璃；抽屉须打开、台上无样品）；取下后不能放样品、不能旋转样品台，自检的Turntable项失败', 'agent', 'robot_skill')
cap('sensor.configure_error', obj(dict(hot_pixels=integer(0, 2000), hot_pixel_dark_e_s=number('1/s', 0.0, 1e4))),
    obj(dict(hot_pixels=INT, hot_pixel_dark_e_s=number('1/s'))), dict(hot_pixels=20, hot_pixel_dark_e_s=50.0), ['base_plate'],
    '评测者：CCD缺陷像元（热像元）的个数与每个的额外暗电流（制冷设定点时电子/s，随CCD温度同暗电流变化；位置由噪声种子决定）；默认0即理想CCD', 'evaluator', 'simulation')
APP_CAPS = [k for k in CAPS if k not in BASE_CAPS]

SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference)


slot('clock.time_s', number('s'), 's', 'derived', 'base_plate', '仿真时钟（连续；操作事件在其精确时刻发生）')
slot('plant.configured', BOOL, '1', 'setpoint', 'base_plate', '已声明实验室条件')
slot('power.enabled', BOOL, '1', 'setpoint', 'switch_power', '主电源')
slot('lamp.status_on', BOOL, '1', 'derived', 'status_light_bar', '正面蓝色灯条（通电亮）')
slot('camera.ready', BOOL, '1', 'derived', 'touch_screen', 'CCD已制冷到设定点附近（2 K以内），可拍摄')
slot('camera.temperature_k', number('K'), 'K', 'process_truth', 'touch_screen', 'CCD温度', 'evaluator')
slot('mode', nullable(enum(MODES)), '1', 'setpoint', 'touch_screen', '当前成像模式')
slot('channels.selected', array(TEXT, 0, 6), '1', 'setpoint', 'touch_screen', '已选通道（通道:染料）')
slot('camera.binning', INT, '1', 'setpoint', 'touch_screen', '像素合并')
slot('camera.zoom', number('1'), '1', 'setpoint', 'touch_screen', '总变焦倍数（1–2×机械，其余数字）')
slot('camera.locked', BOOL, '1', 'setpoint', 'touch_screen', '相机设置锁定')
slot('camera.field_of_view_m', array(number('m'), 2, 2), 'm', 'derived', 'touch_screen', '当前视场宽×深')
slot('drawer.y_m', number('m'), 'm', 'derived', 'sample_drawer', '抽屉位置（0关闭，0.30 m打开；场景抽屉关节镜像）', reference='资产坐标−Y方向的平移')
slot('drawer.state', enum(['closed', 'opening', 'open', 'closing', 'stopped']), '1', 'derived', 'sample_drawer', '抽屉状态（stopped：受阻或断电停在中途）')
slot('stage.angle_rad', number('rad'), 'rad', 'derived', 'sample_stage', '样品台转角（±10°；场景转台关节镜像）', reference='绕+Z，正为逆时针')
slot('sample.on_stage', nullable(TEXT), '1', 'derived', 'sample_stage', '台上的样品')
slot('sample.bench_ids', array(TEXT, 0, SAMPLES_MAX), '1', 'derived', 'sample_stage', '实验台上的样品')
slot('display.exposure_s', number('s'), 's', 'derived', 'display_exposure_digits', '屏幕仿真数字：当前（第一个）通道的曝光时间')
slot('operation.kind', nullable(TEXT), '1', 'derived', 'touch_screen', '进行中的操作（drawer/smart_exposure/capture）')
slot('operation.progress', number('1'), '1', 'derived', 'touch_screen', '操作进度0–1')
slot('operation.started_by_other_agent', BOOL, '1', 'derived', 'touch_screen', '进行中的操作属于另一个agent')
slot('result.last_image_id', nullable(TEXT), '1', 'derived', 'touch_screen', '最近拍摄的图像')
slot('truth.faults', array(TEXT, 0, 8), '1', 'process_truth', 'base_plate', '注入的故障', 'evaluator')
slot('truth.sample_skew_rad', nullable(number('rad')), 'rad', 'process_truth', 'sample_stage', '台上样品相对相机的剩余偏角（摆放偏角−转台转角）', 'evaluator')
BASE_SLOTS = list(SLOTS)
slot('app.user', nullable(TEXT), '1', 'setpoint', 'touch_screen', '已登录的用户（null为访客）')
slot('app.users', array(FREEOBJ, 0, 50), '1', 'setpoint', 'touch_screen', '仪器账户（屏名、管理员、创建时间、是否有PIN；不含PIN）')
slot('app.settings', FREEOBJ, '1', 'setpoint', 'touch_screen', '仪器设置（名称、语言、时区与格式、休眠、安全模式、文件命名、网络驱动器/打印机、云区域）')
slot('app.trans_light', BOOL, '1', 'setpoint', 'touch_screen', '实时视图用绿光透射台照明')
slot('gallery', array(FREEOBJ, 0, GALLERY_MAX), '1', 'derived', 'touch_screen', '图库：图像名称、注释、所属用户、拍摄时间、模式、HDR')
slot('image.views', FREEOBJ, '1', 'setpoint', 'touch_screen', '各图像的显示调整（只影响显示）')
slot('dyes.custom', array(FREEOBJ, 0, 40), '1', 'setpoint', 'touch_screen', '自定义染料（名称、激发/发射峰、伪彩）')
slot('channels.false_colors', FREEOBJ, '1', 'setpoint', 'touch_screen', '各通道在合成图中的伪彩')
slot('band_excision.on', BOOL, '1', 'derived', 'stage_glass', '切胶：绿光透射台开着（抽屉打开）')
slot('diagnostics.report', nullable(FREEOBJ), '1', 'derived', 'touch_screen', '最近一次仪器自检的报告（时间与各硬件项结果）')
slot('pixel_map.last_s', nullable(number('s')), 's', 'derived', 'touch_screen', '最近一次Pixel Mapping完成的时间')
slot('settings.backup_s', nullable(number('s')), 's', 'derived', 'side_ports', 'U盘上的设置备份的时间')
slot('settings.factory_reset_pending', BOOL, '1', 'derived', 'touch_screen', '已选恢复出厂设置，下次开机生效')
slot('pixel_map.corrected_px', INT, '1', 'derived', 'touch_screen', '最近一次Pixel Mapping找出并存入校正的异常像元数（之后的拍摄把它们校正为正常暗值）')
slot('service.ship_prep', nullable(enum(SHIP_PREP_STATES)), '1', 'derived', 'touch_screen', 'Ship Prep Mode的步骤（remove_turntable：抽屉已开、等取下转台；power_off：等关机；null：不在该模式）')
slot('stage.turntable_installed', BOOL, '1', 'derived', 'sample_stage', '样品转台（连同透射玻璃）装在抽屉里')
slot('truth.hot_pixels', FREEOBJ, '1', 'process_truth', 'base_plate', '评测者设置的CCD热像元：个数、额外暗电流与尚未被像元图校正的个数', 'evaluator')
MAX_RECORDS, MAX_EVENTS = 400, 400


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


# ---------------------------------------------------------------- numerics and label physics
def _upper_tail(z):
    """P(N(0, 1) > z), vectorised (Abramowitz-Stegun 7.1.26 erfc, |error| < 1.5e-7)."""
    x = np.asarray(z, dtype=float) / math.sqrt(2.0)
    a = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * a)
    y = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429)))) * np.exp(-a * a)
    return 0.5 * np.where(x >= 0, y, 2.0 - y)


def _erf(x):
    return 1.0 - 2.0 * _upper_tail(np.asarray(x, dtype=float) * math.sqrt(2.0))


def _soft_box_px(x, a, b, s, p):
    """Mean over a pixel of width p centred at x of the soft box 0.5 [erf((x - a) / (s sqrt2)) - erf((x - b) / (s sqrt2))] (exact: the antiderivative of
    erf(u) is u erf(u) + exp(-u^2) / sqrt(pi))."""
    k = s * math.sqrt(2.0)

    def G(d):
        u = d / k
        return k * (u * _erf(u) + np.exp(-u * u) / math.sqrt(math.pi))

    def F(z):
        return 0.5 * (G(z - a) - G(z - b))
    return (F(x + p / 2) - F(x - p / 2)) / p


def _box3(a):
    """3 x 3 mean (edges replicated)."""
    q = np.pad(a, 1, mode='edge')
    return sum(q[i:i + a.shape[0], j:j + a.shape[1]] for i in range(3) for j in range(3)) / 9.0


HRP = 'hrp'


def label_kind(label):
    if label == HRP:
        return 'enzyme'
    if label in STAIN_A_M2_KG:
        return 'absorber'
    if label in fluor_lib()['substances']:
        return 'fluorophore'
    return None


def molecules_per_kg(label):
    """Stain molecules per kg of stained material (SYPRO protein stains per kg protein, nucleic-acid stains per kg DNA)."""
    if label in PROTEIN_STAIN_DYE_PER_KG:
        return PROTEIN_STAIN_DYE_PER_KG[label]
    if label in BOUND_QY:
        return DYE_PER_BP / BP_MASS_KG
    return None


@functools.lru_cache(maxsize=None)
def ref_response(channel):
    """Detected photons per second per molecule (before the lens collection) of the reference background dye (epsilon 1e4 m2/mol, QY 0.5, spectra
    fully inside the bands) - converts the support backgrounds given as equivalent dye surface densities."""
    ex, em = FILTERS_NM[channel]
    if ex is None:
        return 0.0
    lam_ex = 0.5 * (ex[0] + ex[1]) * 1e-9
    lam_em = 0.5 * (em[0] + em[1]) * 1e-9
    flux = LED_IRRADIANCE[channel] / (H_PLANCK * C_LIGHT / lam_ex) * FILTER_T
    sigma = REF_EPS_M2_MOL * math.log(10) / N_A
    return sigma * flux * REF_QY * LENS_T * FILTER_T * float(qe(lam_em))


@functools.lru_cache(maxsize=None)
def stray_response(channel):
    """Excitation photons per second per m2 scattered by the sample that leak through the emission filter and are detected (before collection)."""
    ex, em = FILTERS_NM[channel]
    if ex is None or channel == 'VISIBLE':
        return 0.0
    lam_ex = 0.5 * (ex[0] + ex[1]) * 1e-9
    return LED_IRRADIANCE[channel] / (H_PLANCK * C_LIGHT / lam_ex) * STRAY_FRACTION * float(qe(lam_ex)) * LENS_T


@functools.lru_cache(maxsize=None)
def white_flux():
    """White epi-LED photons per second per m2 at the stage through the ND filter (400-700 nm, mean photon energy at 550 nm)."""
    return LED_IRRADIANCE['VISIBLE'] / (H_PLANCK * C_LIGHT / 550e-9)


@functools.lru_cache(maxsize=None)
def dye_rate(dye_id, channel, free=False):
    """Detected photons per second per dye molecule in a channel before the lens collection; nucleic-acid stains use the bound quantum yield in the
    band and 1/20 of it when free (the intercalation enhancement)."""
    if channel in ('VISIBLE', 'CHEMI'):
        return 0.0
    r = dye_response(dye_id, channel)
    if free and dye_id in BOUND_QY:
        r /= 20.0
    return r


# ---------------------------------------------------------------- runtime
class ThermoIbrightFl1500Runtime:
    """iBright FL1500: power, CCD cooling, drawer and rotating stage, camera (binning / zoom / lock), modes and channels, Smart Exposure, capture
    (fluorescence, chemiluminescence, visible, HDR), gallery, band analysis and quantification (SI units)."""

    def __init__(self, instance_id='imager_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.revision = 0
        self.plant = None
        self.power = False
        self.switch_on = False
        self.ccd = dict(t0=0.0, T0=None, powered=False, Ts=None)
        self.ccd_hist = []
        self.energy_anchor_j = 0.0
        self.mode = None
        self.channels = []                          # [(channel, dye or None)]
        self.exposure = {}                          # channel -> s
        self.camera = dict(binning=2, zoom=1.0, auto_focus=True, lock=False)
        self.drawer = dict(state='closed', y=0.0, t0=0.0, y0=0.0, target=0.0)
        self.stage_angle = 0.0
        self.samples = {}
        self.on_stage = None
        self.images = {}
        self.records = {}
        self.operations = {}
        self.op = None
        self.seq = dict(img=0, rec=0, op=0)
        self.faults = set()
        self.events, self.events_dropped = [], 0
        self.led = {k: _Sum() for k in LEDGER_KEYS}
        self._principals, self._requests, self._owners = {}, {}, {}
        self._caller = None
        self.cam_err = dict(hot_pixels=0, hot_pixel_dark_e_s=0.0, sites=[])  # evaluator CCD defects (sensor.configure_error); default: an ideal CCD
        self.turntable = True
        self._app_init()

    # ---------------------------------------------------------------- basics
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
        return ThermoIbrightFl1500Session(self, name)

    def _event(self, kind, code, message, rid=None):
        if len(self.events) >= MAX_EVENTS:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(dict(time_s=self.t, kind=kind, code=code, message_zh=message, _rid=rid))

    def _rng(self, tag):
        h = hashlib.sha256(f'{self.plant["noise_seed"] if self.plant else 0}:{tag}'.encode()).digest()
        return np.random.default_rng(int.from_bytes(h[:8], 'little'))

    def _room(self):
        return self.plant['room_k'] if self.plant else 295.15

    # ---------------------------------------------------------------- CCD and energy (analytic between events: exactly step-partition independent)
    def _ccd_target(self, powered):
        return self._room() - CCD_DELTA_K if powered else self._room()

    def _ccd_at(self, t):
        """CCD anchor in force at time t: the CCD history is piecewise analytic between power and room changes, so a past time (the start of an
        exposure being read out) uses its own anchor, never the current one extrapolated backwards."""
        for a in reversed(self.ccd_hist):
            if a['t0'] <= t + 1e-12:
                return a
        return self.ccd_hist[0]

    def ccd_temperature(self, t=None):
        t = self.t if t is None else t
        if not self.ccd_hist:
            return self._room()
        c = self._ccd_at(t)
        tau = CCD_TAU_S if c['powered'] else CCD_OFF_TAU_S
        return c['Ts'] + (c['T0'] - c['Ts']) * math.exp(-max(0.0, t - c['t0']) / tau)

    def _energy_since_anchor(self, t=None):
        t = self.t if t is None else t
        c = self.ccd
        if not c['powered'] or c['T0'] is None:
            return 0.0
        h = t - c['t0']
        cool = POWER_COOL_W * max(0.0, c['T0'] - c['Ts']) / CCD_DELTA_K * CCD_TAU_S * (1.0 - math.exp(-h / CCD_TAU_S))
        return POWER_IDLE_W * h + cool

    def _anchor(self, powered):
        """Re-anchor the analytic CCD / energy state at the current time (power or room change)."""
        T = self.ccd_temperature()
        self.energy_anchor_j += self._energy_since_anchor()
        self._new_anchor(T, powered)

    def _new_anchor(self, T, powered):
        """Start a new CCD piece (set point from the room of this moment); older pieces are kept while an exposure may still integrate over them."""
        self.ccd = dict(t0=self.t, T0=T, powered=powered, Ts=self._ccd_target(powered))
        old = [a for a in self.ccd_hist if a['t0'] < self.t - CCD_HISTORY_S]
        keep = [a for a in self.ccd_hist if a['t0'] >= self.t - CCD_HISTORY_S and a['t0'] < self.t]   # a piece starting now replaces any other piece starting now
        self.ccd_hist = old[-1:] + keep + [self.ccd]                         # pruned by time only (exact for any number of changes)

    def camera_ready(self):
        return self.power and self.ccd_temperature() <= self._ccd_target(True) + READY_BAND_K

    # ---------------------------------------------------------------- read
    def _drawer_y(self):
        d = self.drawer
        if d['state'] in ('opening', 'closing'):
            span = DRAWER_S * abs(d['target'] - d['y0']) / DRAWER_TRAVEL
            if span <= 0.0:                                                          # blocked drawer: the motor stalls in place
                return d['y0']
            frac = min(1.0, max(0.0, (self.t - d['t0']) / span))
            return d['y0'] + (d['target'] - d['y0']) * frac
        return d['y']

    def _geometry(self):
        zoom = self.camera['zoom']
        zm = min(zoom, MECH_ZOOM[1])
        zd = zoom / zm
        wg = working_geometry(zm)
        b = self.camera['binning']
        cam = (max(1, int(SENSOR_PX[0] / zd) // b), max(1, int(SENSOR_PX[1] / zd) // b))
        m = max(1, math.ceil(max(cam) / ANALYSIS_MAX_PX))
        grid = (max(1, cam[0] // m), max(1, cam[1] // m))
        obj = wg['object_pixel_m'] * b
        return dict(zoom=zoom, zm=zm, zd=zd, cam=cam, m=m, grid=grid, obj_px_m=obj, stored_px_m=obj * m, collection=wg['collection'], na=wg['na'],
                    fov=(cam[0] * obj, cam[1] * obj), object_distance_m=wg['object_distance_m'])

    def read(self, visibility='agent'):
        g = self._geometry()
        s = self.samples.get(self.on_stage) if self.on_stage else None
        values = {
            'clock.time_s': self.t, 'plant.configured': self.plant is not None, 'power.enabled': self.power, 'lamp.status_on': self.power,
            'camera.ready': self.camera_ready(), 'camera.temperature_k': self.ccd_temperature(), 'mode': self.mode,
            'channels.selected': [f'{c}:{d}' if d else c for c, d in self.channels], 'camera.binning': self.camera['binning'], 'camera.zoom': self.camera['zoom'],
            'camera.locked': self.camera['lock'], 'camera.field_of_view_m': [g['fov'][0], g['fov'][1]], 'drawer.y_m': self._drawer_y(), 'drawer.state': self.drawer['state'],
            'stage.angle_rad': self.stage_angle, 'sample.on_stage': self.on_stage, 'sample.bench_ids': sorted(k for k, v in self.samples.items() if v['location'] == 'bench'),
            'display.exposure_s': float(self.exposure.get(self.channels[0][0], 0.0)) if self.channels else 0.0,
            'operation.kind': self.op['kind'] if self.op else None, 'operation.progress': self._progress(), 'operation.started_by_other_agent': False,
            'result.last_image_id': max(self.images, default=None), 'truth.faults': sorted(self.faults),
            'truth.sample_skew_rad': (s['skew'] - self.stage_angle) if s else None,
            'stage.turntable_installed': self.turntable,
            'truth.hot_pixels': dict(hot_pixels=len(self.cam_err['sites']), hot_pixel_dark_e_s=self.cam_err['hot_pixel_dark_e_s'], uncorrected=len(self._hot_sites())),
        }
        values.update(self._app_read())
        return {k: v for k, v in values.items() if SLOTS[k]['visibility'] == 'agent' or visibility == 'evaluator'}

    def _progress(self):
        if not self.op:
            return 0.0
        o = self.op
        return float(min(1.0, max(0.0, (self.t - o['start']) / max(o['end'] - o['start'], 1e-9))))

    # ---------------------------------------------------------------- commands
    def invoke(self, capability_id, **a):
        if capability_id not in ALL_CAPS:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        validate(ALL_CAPS[capability_id]['input_schema'], a)
        props = ALL_CAPS[capability_id]['input_schema'].get('properties', {})
        a = {k: (int(v) if props.get(k, {}).get('type') == 'integer' and v is not None else v) for k, v in a.items()}
        if capability_id in PANEL_CAPS:                                       # panel gestures, capture and frame (panel_ui.json on _panel_ui.py)
            out = self._panel_cmd(capability_id, a)
        else:
            out = getattr(self, '_cmd_' + capability_id.replace('.', '_'))(**a)
        if ALL_CAPS[capability_id]['kind'] not in ('query', 'analysis', 'simulation'):
            self.revision += 1
        return out

    def _need_plant(self):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')

    def _need_power(self):
        self._need_plant()
        if not self.power:
            raise ActionRejected('POWER_OFF')

    def _need_idle(self):
        self._need_power()
        if self.op is not None:
            raise ActionRejected('BUSY')

    def _mains_ok(self):
        return 'power_loss' not in self.faults and 0.9 * 100.0 <= self.plant['mains_voltage_v'] <= 1.1 * 240.0

    def _cmd_plant_configure(self, room_k, mains_voltage_v, noise_seed):
        if self.plant is not None and noise_seed != self.plant['noise_seed']:
            raise ActionRejected('SEED_FIXED')
        first = self.plant is None
        if not first:                                                         # close the CCD piece of the old room, then start one for the new room
            T = self.ccd_temperature()
            self.energy_anchor_j += self._energy_since_anchor()
        self.plant = dict(room_k=room_k, mains_voltage_v=mains_voltage_v, noise_seed=noise_seed)
        self._new_anchor(room_k if first else T, False if first else self.ccd['powered'])
        if self.power and not self._mains_ok():
            self._power_off()
        elif self.switch_on and not self.power and self._mains_ok():
            self._power_up()
        return dict(plant=dict(self.plant))

    def _power_up(self):
        self._anchor(True)
        self.power = True
        self._event('power', 'POWER_ON', 'iBright通电：CCD开始制冷，触屏启动')
        self.power_epoch += 1
        if self.app['reset_pending']:                                         # Reset Factory Defaults applies at the next power-up
            self._factory_reset()

    def _power_off(self):
        if self.op is not None:
            self._fail_op('power_lost', '断电：操作中止')
        d = self.drawer
        if d['state'] in ('opening', 'closing'):                                  # the drawer motor stops where it is
            d.update(y=self._drawer_y(), state='stopped')
        self._anchor(False)
        self.power = False
        self._excision_off()
        self.app['ship_prep'] = None                                          # powering off ends Ship Prep Mode (its last step)
        self._event('power', 'POWER_OFF', 'iBright断电（屏幕与灯条熄灭）')

    def _set_power(self, enabled):
        self._need_plant()
        if enabled:
            if not self.power:
                if not self._mains_ok():
                    raise ActionRejected('MAINS_OUT_OF_RANGE')
                self.switch_on = True
                self._power_up()
        else:
            self.switch_on = False
            if self.power:
                self._power_off()
        return dict(enabled=self.power)

    def _cmd_power_set(self, enabled):
        return self._set_power(enabled)

    def _cmd_power_press_switch(self):
        return self._set_power(not self.switch_on)

    def _cmd_mode_select(self, mode):
        self._need_idle()
        self.mode = mode
        default = {'chemi_blots': [('CHEMI', None)], 'fluorescent_blots': [], 'nucleic_acid_gels': [('GREEN_TRANS', 'sybr_safe')], 'protein_gels': [('VISIBLE', None)],
                   'universal': []}[mode]
        self.channels = list(default)
        for c, _ in default:
            self.exposure.setdefault(c, 1.0 if c != 'VISIBLE' else 0.0015)
        return dict(mode=mode, channels=[f'{c}:{d}' if d else c for c, d in self.channels])

    def _cmd_channels_set(self, channels):
        self._need_idle()
        if self.mode is None:
            raise ActionRejected('MODE_REQUIRED')
        seen, out, warn = set(), [], []
        allowed = MODE_CHANNELS[self.mode]
        for c in channels:
            ch, dye = c['channel'], c['dye']
            if ch in seen:
                raise ActionRejected('DUPLICATE_CHANNEL')
            if ch not in allowed:
                raise ActionRejected('CHANNEL_NOT_IN_MODE')
            if dye is not None:
                custom = self.app['custom_dyes'].get(dye)
                if dye not in fluor_lib()['substances'] and custom is None:
                    raise ActionRejected('UNKNOWN_DYE')
                if ch in ('VISIBLE', 'CHEMI'):
                    raise ActionRejected('DYE_CHANNEL_MISMATCH')
                if custom is not None:                                            # a custom dye: its peaks must fall in the channel's bands (+-15 nm)
                    (ex0, ex1), (em0, em1) = FILTERS_NM[ch]
                    if not (ex0 - 15 <= custom['excitation_nm'] <= ex1 + 15 and em0 - 15 <= custom['emission_nm'] <= em1 + 15):
                        raise ActionRejected('DYE_CHANNEL_MISMATCH')
                else:
                    best = max(dye_rate(dye, k) for k in FILTERS_NM if k not in ('VISIBLE', 'CHEMI'))
                    if best <= 0 or dye_rate(dye, ch) < 0.05 * best:              # the dye is not detected in this filter set
                        raise ActionRejected('DYE_CHANNEL_MISMATCH')
            elif ch not in ('VISIBLE', 'CHEMI', 'GREEN_TRANS'):
                raise ActionRejected('DYE_REQUIRED')
            seen.add(ch)
            out.append((ch, dye))
        if sum(1 for ch, _ in out if ch not in ('VISIBLE', 'CHEMI')) > MAX_DYES:
            raise ActionRejected('TOO_MANY_DYES')
        if 'EX3' in seen and 'EX4' in seen:
            warn.append('ex3_ex4_crosstalk')
        self.channels = out
        for ch, _ in out:
            self.exposure.setdefault(ch, 1.0 if ch != 'VISIBLE' else 0.0015)
        return dict(channels=[f'{c}:{d}' if d else c for c, d in out], warnings=warn)

    def _cmd_exposure_set(self, channel, exposure_s):
        self._need_idle()
        if channel not in [c for c, _ in self.channels]:
            raise ActionRejected('CHANNEL_NOT_SELECTED')
        if self.camera['lock']:
            raise ActionRejected('SETTINGS_LOCKED')
        self.exposure[channel] = float(exposure_s)
        return dict(channel=channel, exposure_s=float(exposure_s))

    def _cmd_camera_set(self, binning, zoom, auto_focus, lock, rotation_rad=None):
        self._need_idle()
        if binning not in BINNINGS:
            raise ActionRejected('UNSUPPORTED_BINNING')
        cam = self.camera
        if cam['lock'] and lock and abs(zoom - cam['zoom']) > 1e-12:
            raise ActionRejected('SETTINGS_LOCKED')
        if rotation_rad is not None:
            if self.drawer['state'] != 'closed':
                raise ActionRejected('DRAWER_NOT_CLOSED')
            if not self.turntable:
                raise ActionRejected('TURNTABLE_REMOVED')
            self.stage_angle = float(rotation_rad)
        cam.update(binning=binning, zoom=float(zoom), auto_focus=bool(auto_focus), lock=bool(lock))
        return dict(binning=binning, zoom=float(zoom), auto_focus=bool(auto_focus), lock=bool(lock), rotation_rad=self.stage_angle)

    # ---------------------------------------------------------------- operations (one at a time; their events happen at exact times)
    def _start_op(self, kind, duration, segments=(), **payload):
        """segments: [(t_rel_end, name, data)] processed at their exact times before the end (a capture forms each channel at the end of its own
        exposure; Smart Exposure books each test exposure)."""
        self.seq['op'] += 1
        oid = f'op{self.seq["op"]:04d}'
        rid = None
        if kind in ('smart_exposure', 'capture'):
            if len(self.records) >= MAX_RECORDS:
                raise ActionRejected('RECORDS_FULL')
            self.seq['rec'] += 1
            rid = f'R{self.seq["rec"]:04d}'
            self.records[rid] = dict(record_id=rid, kind=kind, status='running', started_s=self.t, finished_s=None, image_id=None, recommended_exposure_s={}, warnings=[])
        self.operations[oid] = dict(op=oid, kind=kind, phase='running', record_id=rid, start=self.t, end=self.t + float(duration),
                                    pending=sorted([(self.t + float(tr), i, name, data) for i, (tr, name, data) in enumerate(segments)], key=lambda q: (q[0], q[1])),
                                    parts={}, **payload)
        self.op = self.operations[oid]
        if self._caller is not None:
            self._owners[oid] = self._caller
            if rid:
                self._owners[rid] = self._caller
        return oid

    def _finish_op(self, phase, status=None, warn=None):
        op = self.op
        op['phase'] = phase
        op['pending'] = []
        if op['record_id']:
            rec = self.records[op['record_id']]
            rec['status'] = status or {'completed': 'completed', 'cancelled': 'cancelled'}.get(phase, 'failed')
            rec['finished_s'] = self.t
            if warn and warn not in rec['warnings']:
                rec['warnings'].append(warn)
        self.op = None

    def _book_partial(self, op):
        """LED energy and exposure time of the exposure in progress when an operation is aborted (the charge is discarded, not read out)."""
        for t_end, _, name, data in op['pending']:
            if name in ('expose', 'test'):
                t0 = t_end - data['te']
                el = max(0.0, min(data['te'], self.t - t0))
                if el > 0:
                    self.led['exposure_time_s'].add(el)
                    if data['ch'] != 'CHEMI' and 'led_failure' not in self.faults:
                        self.led['led_energy_j'].add(POWER_LED_W * el)
                        self.energy_anchor_j += POWER_LED_W * el
                break

    def _fail_op(self, code, message):
        op = self.op
        if op['kind'].startswith('drawer'):
            d = self.drawer
            d.update(y=self._drawer_y(), state='stopped')
        else:
            self._book_partial(op)
        self._event('alarm', code.upper(), message, rid=op['record_id'])
        self._finish_op('failed', 'failed', code)

    def _cancel_op(self, op):
        if self.op is None or self.op is not op:
            raise ActionRejected('NOT_RUNNING')
        if op['kind'].startswith('drawer'):
            raise ActionRejected('CANCEL_UNSUPPORTED')
        d = self.drawer
        if op['kind'] == 'diagnostics' and d['state'] in ('opening', 'closing'):   # a cancelled self-test leaves the drawer where it is
            d.update(y=self._drawer_y(), state='stopped')
        self._book_partial(op)
        self._event('capture', 'CANCELLED', f'{op["kind"]}已取消', rid=op['record_id'])
        self._finish_op('cancelled', 'cancelled')

    def step(self, dt_s):
        """Advance the host clock; operation events (the end of each exposure, the completion) happen at their exact times inside the step (drawer
        position, CCD temperature and energy are analytic functions of time), so any split of the same interval gives the same result."""
        if dt_s < 0:
            raise ActionRejected('INVALID_ARGUMENT')
        t_end = self.t + float(dt_s)
        while self.op is not None:
            op = self.op
            nxt = op['pending'][0][0] if op['pending'] else op['end']
            if nxt > t_end + 1e-12:
                break
            self.t = max(self.t, nxt)
            try:
                if op['pending']:
                    _, _, name, data = op['pending'].pop(0)
                    self._segment(op, name, data)
                else:
                    self._complete(op)
            except ActionRejected:
                raise
            except Exception as exc:  # noqa: BLE001 - a numerical failure fails the operation (alarm) instead of wedging the instrument
                if self.op is op:
                    self._event('alarm', 'SIMULATION_ERROR', f'{op["kind"]}：仿真计算出错（{type(exc).__name__}），操作中止', rid=op['record_id'])
                    self._finish_op('failed', 'failed', 'simulation_error')
        self.t = t_end

    def _segment(self, op, name, data):
        if name == 'test':                                                        # a Smart Exposure test shot, formed at the end of its exposure
            self._smart_test(op, data)
        elif name == 'expose':                                                    # one channel (or HDR part) read out at the end of its exposure
            self._expose_part(op, data)
        elif name == 'diag_drawer':                                               # the self-test opens and closes the drawer
            self._diag_drawer(op, data)

    def _complete(self, op):
        kind = op['kind']
        if op.get('fail'):
            self._fail_op(*op['fail'])
            return
        if kind in ('drawer_open', 'drawer_close'):
            d = self.drawer
            d.update(y=d['target'], state='open' if kind == 'drawer_open' else 'closed')
            if kind == 'drawer_open':
                if not self.camera['lock']:
                    self.stage_angle = 0.0                                          # the stage returns square to the drawer
                self._event('drawer', 'OPEN', '抽屉已打开')
            else:
                self._auto_align()
                self._event('drawer', 'CLOSED', '抽屉已关闭：自动对正、变焦与对焦' if not self.camera['lock'] else '抽屉已关闭（相机设置已锁定：不自动旋转/变焦）')
            self._finish_op('completed')
        elif kind == 'smart_exposure':
            rec = self.records[op['record_id']]
            rec['recommended_exposure_s'] = dict(op['result'])
            for w in op['warnings']:
                if w not in rec['warnings']:
                    rec['warnings'].append(w)
            if not self.camera['lock']:
                self.exposure.update(op['result'])
            self._event('capture', 'SMART_EXPOSURE', '推荐曝光：' + '，'.join(f'{k} {v:.3g} s' for k, v in op['result'].items()), rid=op['record_id'])
            self._finish_op('completed')
        elif kind == 'capture' and op.get('routine'):
            self._form_routine(op)
        elif kind in ('diagnostics', 'pixel_mapping'):
            self._complete_service(op)
        elif kind == 'capture':
            img = self._form_image(op)
            rec = self.records[op['record_id']]
            rec['image_id'] = img['image_id']
            rec['warnings'] = list(img['warnings'])
            self._event('capture', 'CAPTURED', f'图像{img["image_id"]}已存入图库', rid=op['record_id'])
            self._finish_op('completed')

    def _auto_align(self):
        """Closing the drawer (camera settings not locked): the stage rotates to square the sample (within +-10 deg) and the optical zoom frames it
        (user guide: "automatically aligns, zooms, focuses")."""
        s = self.samples.get(self.on_stage) if self.on_stage else None
        if s is None or self.camera['lock']:
            return
        self.stage_angle = float(max(-STAGE_ROT_RAD, min(STAGE_ROT_RAD, s['skew'])))
        th = abs(s['skew'] - self.stage_angle)
        bw = s['W'] * math.cos(th) + s['H'] * math.sin(th)
        bh = s['W'] * math.sin(th) + s['H'] * math.cos(th)
        self.camera['zoom'] = float(max(MECH_ZOOM[0], min(MECH_ZOOM[1], FOV_M[0] / (AUTOZOOM_MARGIN * bw), FOV_M[1] / (AUTOZOOM_MARGIN * bh))))
        self.camera['auto_focus'] = True

    def _cmd_drawer_open(self):
        self._need_idle()
        d = self.drawer
        if d['state'] == 'open':
            raise ActionRejected('DRAWER_OPEN')
        y = self._drawer_y()
        dur = DRAWER_S * (DRAWER_TRAVEL - y) / DRAWER_TRAVEL
        if 'drawer_blocked' in self.faults:
            oid = self._start_op('drawer_open', DRAWER_S, fail=('drawer_blocked', '抽屉受阻：电机堵转，抽屉未移动'))
            d.update(state='opening', t0=self.t, y0=y, target=y)
            return dict(operation_id=oid)
        oid = self._start_op('drawer_open', dur)
        d.update(state='opening', t0=self.t, y0=y, target=DRAWER_TRAVEL)
        return dict(operation_id=oid)

    def _cmd_drawer_close(self):
        self._need_idle()
        d = self.drawer
        if d['state'] == 'closed':
            raise ActionRejected('DRAWER_CLOSED')
        y = self._drawer_y()
        if 'drawer_blocked' in self.faults:
            oid = self._start_op('drawer_close', DRAWER_S, fail=('drawer_blocked', '抽屉受阻：电机堵转，抽屉未移动'))
            d.update(state='closing', t0=self.t, y0=y, target=y)
            return dict(operation_id=oid)
        oid = self._start_op('drawer_close', DRAWER_S * y / DRAWER_TRAVEL)
        d.update(state='closing', t0=self.t, y0=y, target=0.0)
        self._excision_off()                                                  # closing the drawer ends band excision (transilluminator off)
        return dict(operation_id=oid)

    # ---------------------------------------------------------------- samples
    def _cmd_sample_provide(self, sample_id, support, width_m, height_m, lanes, substrate=None, free_stain_mol_m2=0.0):
        self._need_plant()
        if sample_id in self.samples:
            raise ActionRejected('ITEM_EXISTS')
        if len(self.samples) >= SAMPLES_MAX:
            raise ActionRejected('TOO_MANY_ITEMS')
        stain = None
        for lane in lanes:
            if lane['x_m'] - lane['width_m'] / 2 < -1e-12 or lane['x_m'] + lane['width_m'] / 2 > width_m + 1e-12:
                raise ActionRejected('LANE_OUTSIDE_SAMPLE')
            for b in lane['bands']:
                if b['y_m'] > height_m + 1e-12:
                    raise ActionRejected('BAND_OUTSIDE_SAMPLE')
                kind = label_kind(b['label'])
                if kind is None:
                    raise ActionRejected('UNKNOWN_LABEL')
                mol, kg = b.get('amount_mol'), b.get('amount_kg')
                if (mol is None) == (kg is None):
                    raise ActionRejected('AMOUNT_REQUIRED')
                if (kind == 'enzyme' and mol is None) or (kind == 'absorber' and kg is None) or (kind == 'fluorophore' and kg is not None and molecules_per_kg(b['label']) is None):
                    raise ActionRejected('AMOUNT_UNIT_MISMATCH')
                if kind == 'fluorophore' and molecules_per_kg(b['label']) is not None and stain is None:
                    stain = b['label']
        self.samples[sample_id] = dict(sample_id=sample_id, support=support, W=float(width_m), H=float(height_m), lanes=copy.deepcopy(lanes), substrate=substrate,
                                       free_stain=float(free_stain_mol_m2), stain=stain, location='bench', skew=0.0, t_provided=self.t)
        self._event('capture', 'SAMPLE_READY', f'样品{sample_id}（{support}，{len(lanes)}泳道）放在实验台上')
        return dict(sample_id=sample_id, location='bench')

    def _cmd_sample_place(self, sample_id, skew_rad):
        self._need_plant()
        if self.drawer['state'] != 'open' or self.op is not None:
            raise ActionRejected('DRAWER_NOT_OPEN')
        s = self.samples.get(sample_id)
        if s is None:
            raise ActionRejected('UNKNOWN_ITEM')
        if s['location'] != 'bench':
            raise ActionRejected('SAMPLE_NOT_ON_BENCH')
        if self.on_stage is not None:
            raise ActionRejected('STAGE_OCCUPIED')
        if not self.turntable:                                                # the glass the sample lies on sits in the turntable
            raise ActionRejected('TURNTABLE_REMOVED')
        th = abs(skew_rad)
        if s['W'] * math.cos(th) + s['H'] * math.sin(th) > GLASS_WD[0] or s['W'] * math.sin(th) + s['H'] * math.cos(th) > GLASS_WD[1]:
            raise ActionRejected('SAMPLE_TOO_LARGE')
        s.update(location='stage', skew=float(skew_rad))
        self.on_stage = sample_id
        return dict(sample_id=sample_id)

    def _cmd_sample_remove(self, sample_id):
        self._need_plant()
        if self.drawer['state'] != 'open' or self.op is not None:
            raise ActionRejected('DRAWER_NOT_OPEN')
        if self.on_stage != sample_id:
            raise ActionRejected('SAMPLE_NOT_ON_STAGE')
        self.samples[sample_id].update(location='bench', skew=0.0)
        self.on_stage = None
        return dict(sample_id=sample_id)

    # ---------------------------------------------------------------- image formation
    def _unit_rate(self, kind, label, channel, s):
        """Detected photons per second per molecule before the lens collection (chemiluminescence at the time the substrate was applied)."""
        if kind == 'fluorophore':
            return 0.0 if 'led_failure' in self.faults else dye_rate(label, channel)
        if kind == 'enzyme':
            return HRP_PHOTONS_S[s['substrate']] * chemi_fraction() if channel == 'CHEMI' and s['substrate'] else 0.0
        return 0.0

    def _fields(self, geo, channels):
        """Expected detected electrons per second per binned camera pixel on the stored grid for each channel (chemiluminescence at the substrate
        time), the sample mask and the grid coordinates.  Band and lane profiles are integrated over the stored pixel (no point sampling)."""
        nx, ny = geo['grid']
        p = geo['stored_px_m']
        x = (np.arange(nx) + 0.5 - nx / 2) * p
        y = (np.arange(ny) + 0.5 - ny / 2) * p
        X, Y = np.meshgrid(x, y)
        out = {ch: np.zeros((ny, nx)) for ch in channels}
        absorb = np.zeros((ny, nx))
        inside = np.zeros((ny, nx), dtype=bool)
        s = self.samples.get(self.on_stage) if self.on_stage else None
        smudge = 'stage_smudge' in self.faults
        if s is not None:
            th = s['skew'] - self.stage_angle
            c, sn = math.cos(th), math.sin(th)
            XS = s['W'] / 2 + X * c + Y * sn
            YS = s['H'] / 2 - X * sn + Y * c
            inside = (XS >= 0) & (XS <= s['W']) & (YS >= 0) & (YS <= s['H'])
            for lane in s['lanes']:
                xl, wl = lane['x_m'], lane['width_m']
                sel = inside & (np.abs(XS - xl) < wl / 2 + 4 * EDGE_M + p)
                if not sel.any():
                    continue
                xs, ys = XS[sel], YS[sel]
                box = _soft_box_px(xs, xl - wl / 2, xl + wl / 2, EDGE_M, p) / wl
                for b in lane['bands']:
                    sg = b['sigma_m'] * math.sqrt(2.0)
                    dens = box * 0.5 * (_erf((ys + p / 2 - b['y_m']) / sg) - _erf((ys - p / 2 - b['y_m']) / sg)) / p
                    kind = label_kind(b['label'])
                    if kind == 'absorber':
                        absorb[sel] += STAIN_A_M2_KG[b['label']] * b['amount_kg'] * dens
                        continue
                    n = b['amount_mol'] * N_A if b.get('amount_mol') is not None else b['amount_kg'] * molecules_per_kg(b['label'])
                    for ch in channels:
                        r = self._unit_rate(kind, b['label'], ch, s)
                        if r:
                            out[ch][sel] += n * r * dens
        A = geo['obj_px_m'] ** 2 * geo['collection']
        for ch in channels:
            if ch == 'VISIBLE':
                R = np.where(inside, REFLECT_SAMPLE, REFLECT_STAGE) * (0.9 if smudge else 1.0)
                lit = 0.0 if 'led_failure' in self.faults else 1.0
                out[ch] = white_flux() * visible_fraction() * R * 10.0 ** (-absorb) * lit * A
            elif ch == 'CHEMI':
                if s is not None and s['substrate']:
                    out[ch] += np.where(inside, CHEMI_BACKGROUND_PHOTONS_M2_S * chemi_fraction(), 0.0)
                out[ch] *= A
            else:
                lit = 0.0 if 'led_failure' in self.faults else 1.0
                bg_in = BACKGROUND[s['support']][ch] * N_A * ref_response(ch) if s is not None else 0.0
                if s is not None and s['stain'] and s['free_stain'] > 0:
                    bg_in += s['free_stain'] * N_A * dye_rate(s['stain'], ch, free=True)
                bg_out = GLASS_BG_FRACTION * BACKGROUND['pvdf_lf'][ch] * N_A * ref_response(ch)
                bg = np.where(inside, bg_in, bg_out) * (SMUDGE_BG if smudge else 1.0)
                stray = stray_response(ch) * np.where(inside, REFLECT_SAMPLE, REFLECT_STAGE) * (20.0 if smudge else 1.0)
                out[ch] = (out[ch] + (bg + stray) * lit) * A
        return out, inside, X, Y

    def _live_frame(self, geo):
        """Sample outline found in the white-light live view the instrument takes when the drawer closes (user guide p.13): the bounding box of the
        placed sample in image coordinates (m, frame [x0, y0, x1, y1]), clipped to the field of view; None without a sample."""
        s = self.samples.get(self.on_stage) if self.on_stage else None
        if s is None:
            return None
        th = s['skew'] - self.stage_angle
        c, sn = math.cos(th), math.sin(th)
        corners = [((u - s['W'] / 2) * c - (v - s['H'] / 2) * sn, (u - s['W'] / 2) * sn + (v - s['H'] / 2) * c) for u in (0, s['W']) for v in (0, s['H'])]
        xs, ys = [q[0] for q in corners], [q[1] for q in corners]
        return [min(xs), min(ys), max(xs), max(ys)]                          # may extend beyond the field of view (zoomed in)

    def _dark_electrons(self, t1, t2):
        """Dark charge per binned pixel collected in [t1, t2]: the dark rate (DARK_E_S_COOLED at the nominal cooled temperature, doubling every
        6.3 K) integrated through the piecewise CCD temperature history (trapezoid rule, 65 points per piece)."""
        if t2 <= t1 or not self.ccd_hist:
            return 0.0
        cuts = sorted({t1, t2} | {a['t0'] for a in self.ccd_hist if t1 < a['t0'] < t2})
        tot = 0.0
        for a, b in zip(cuts, cuts[1:]):
            c = self._ccd_at(0.5 * (a + b))
            tau = CCD_TAU_S if c['powered'] else CCD_OFF_TAU_S
            ts = np.linspace(a, b, 65)
            T = c['Ts'] + (c['T0'] - c['Ts']) * np.exp(-np.maximum(0.0, ts - c['t0']) / tau)
            rate = DARK_E_S_COOLED * np.exp2(np.minimum((T - DARK_REF_K) / DARK_DOUBLING_K, 40.0))
            tot += float(np.sum(0.5 * (rate[1:] + rate[:-1]) * np.diff(ts)))
        return tot * self.camera['binning'] ** 2

    def _hot_sites(self):
        """Hot pixels (sensor columns, rows) the stored pixel map does not correct."""
        mapped = {tuple(q) for q in self.app['pixel_map']}
        return [q for q in self.cam_err['sites'] if tuple(q) not in mapped]

    def _hot_electrons(self, geo, t1, t2):
        """Excess dark charge of the uncorrected hot pixels (evaluator sensor.configure_error; none by default, then 0): each adds its rate x the
        normal dark charge of a camera pixel / the normal dark rate (same temperature history) to the stored pixel it falls in (block mean of m x m
        binned pixels; the digital-zoom crop is centred on the sensor)."""
        sites = self._hot_sites() if self.cam_err['hot_pixel_dark_e_s'] > 0 else []
        if not sites:
            return 0.0
        b, m = self.camera['binning'], geo['m']
        q = self._dark_electrons(t1, t2) / b ** 2 * self.cam_err['hot_pixel_dark_e_s'] / DARK_E_S_COOLED
        nx, ny = geo['grid']
        off = [(SENSOR_PX[i] - geo['cam'][i] * b) // 2 for i in (0, 1)]
        add = np.zeros((ny, nx))
        for u, w in sites:
            ix, iy = (u - off[0]) // b // m, (w - off[1]) // b // m
            if u >= off[0] and w >= off[1] and ix < nx and iy < ny:
                add[iy, ix] += q / (m * m)
        return add

    def _signal(self, field, channel, t1, t2):
        """Electrons per binned pixel collected in [t1, t2] (chemiluminescence decays from the substrate time)."""
        if channel == 'CHEMI':
            s = self.samples.get(self.on_stage) if self.on_stage else None
            tau = SUBSTRATE_TAU_S.get(s['substrate'], 3600.0) if s is not None and s['substrate'] else 3600.0
            t0 = s['t_provided'] if s is not None else t1
            return field * tau * (math.exp(-(t1 - t0) / tau) - math.exp(-(t2 - t0) / tau))
        return field * (t2 - t1)

    def _expose(self, sig_e, t_exp, t_start, geo, rng):
        """16-bit image of the stored grid: each stored pixel is the mean of an m x m block of binned camera pixels (the Poisson / read noise of the
        block sum are exact); the saturated share of the block's pixels is estimated from the per-pixel noise (mask: any pixel of the block clipped)."""
        m = geo['m']
        e_mean = sig_e + self._dark_electrons(t_start, t_start + t_exp) + self._hot_electrons(geo, t_start, t_start + t_exp)
        tot = rng.poisson(np.minimum(m * m * e_mean, 1e15)).astype(float) + rng.normal(0.0, m * READ_NOISE_E, e_mean.shape)
        adu = tot / (m * m) / GAIN_E_ADU + BIAS_ADU
        sd = np.sqrt(e_mean + READ_NOISE_E ** 2) / GAIN_E_ADU
        sat = _upper_tail((ADC_MAX - (e_mean / GAIN_E_ADU + BIAS_ADU)) / sd)
        img = np.clip(np.round(adu), 0, ADC_MAX).astype(np.uint16)
        mask = (sat * m * m >= 0.5) | (img >= ADC_MAX)
        return img, float(np.sum(sat)) * m * m, float(np.sum(e_mean)) * m * m, mask

    def _plan(self, channels, hdr):
        """Exposure schedule of a capture: per channel the filter / LED overhead then the exposure (HDR: a short 1/16 exposure first)."""
        plan, t = [], 0.0
        for ch, dye in channels:
            te = self.exposure.get(ch, 1.0)
            t += CAPTURE_OVERHEAD_S
            if hdr and ch == 'CHEMI':
                plan.append((ch, dye, 'short', t, te / HDR_RATIO))
                t += te / HDR_RATIO
            plan.append((ch, dye, 'long', t, te))
            t += te
        return plan, t

    def _expose_part(self, op, d):
        """Read out one exposure of a capture at its end, with the illumination / fault state of that moment."""
        geo = op['geo']
        ch, part, te, t1 = d['ch'], d['part'], d['te'], d['t1']
        fields, inside, X, Y = self._fields(geo, [ch])
        rng = self._rng(f'image:{op["image_id"]}:{ch}:{part}')
        img, nsat, e_tot, mask = self._expose(self._signal(fields[ch], ch, t1, t1 + te), te, t1, geo, rng)
        op['parts'][(ch, part)] = (img, nsat, te, mask)
        self.led['exposure_time_s'].add(te)
        self.led['photons_detected'].add(e_tot)
        if ch != 'CHEMI' and 'led_failure' not in self.faults:
            self.led['led_energy_j'].add(POWER_LED_W * te)
            self.energy_anchor_j += POWER_LED_W * te

    def _form_image(self, op):
        geo = op['geo']
        iid = op['image_id']
        chans, warns = [], []
        scale = None
        for ch, dye in op['channels']:
            img, nsat, te, mask = op['parts'][(ch, 'long')]
            if (ch, 'short') in op['parts']:                                      # Smart Range HDR: the short exposure replaces saturated pixels
                simg, ssat, ste, smask = op['parts'][(ch, 'short')]
                L = img.astype(float) - BIAS_ADU
                S = (simg.astype(float) - BIAS_ADU) * (te / ste)
                M = np.where(mask, S, L)
                top = float(M.max())
                scale = min(1.0, (ADC_MAX - 1 - BIAS_ADU) / top) if top > 0 else 1.0      # linear, below full scale: no pixel is reported clipped
                img = np.clip(np.round(M * scale + BIAS_ADU), 0, ADC_MAX - 1).astype(np.uint16)
                nsat, mask = ssat, smask
            total_px = geo['cam'][0] * geo['cam'][1]
            chans.append(dict(channel=ch, dye=dye, exposure_s=float(te), img=img, sat=mask, saturated_px=int(round(nsat)), saturated_fraction=float(min(1.0, nsat / total_px))))
            if nsat >= 1:
                warns.append(f'saturated_pixels:{ch}')
        s = self.samples.get(self.on_stage) if self.on_stage else None
        if s is not None:
            if abs(s['skew'] - self.stage_angle) > math.radians(1.0):
                warns.append('sample_skewed')
            th = abs(s['skew'] - self.stage_angle)
            bw = s['W'] * math.cos(th) + s['H'] * math.sin(th)
            bh = s['W'] * math.sin(th) + s['H'] * math.cos(th)
            if bw > geo['fov'][0] + 1e-9 or bh > geo['fov'][1] + 1e-9:
                warns.append('sample_outside_field_of_view')
        if 'EX3' in [c for c, _ in op['channels']] and 'EX4' in [c for c, _ in op['channels']]:
            warns.append('ex3_ex4_crosstalk')
        img = dict(image_id=iid, captured_s=self.t, mode=op['mode'], binning=op['binning'], zoom=op['zoom'], hdr=op['hdr'], width_px=geo['cam'][0],
                   height_px=geo['cam'][1], object_pixel_m=geo['obj_px_m'], m=geo['m'], stored_px_m=geo['stored_px_m'], channels=chans, warnings=warns[:12], hdr_scale=scale,
                   live_frame_m=op['live_frame'], exported=[],
                   template_source=sorted({f'fluorophore_library.json#{dye}' for _, dye in op['channels'] if dye})[:8])   # the dyes the user chose, not the sample truth
        self.images[iid] = img
        self.app['names'][iid] = dict(name=self._auto_name(img, op), comments='', user=self.app['user'])   # the gallery of the signed-in user
        self.led['images'].add(1.0)
        if self._owner_of_op(op):
            self._owners[iid] = self._owner_of_op(op)
        return img

    def _owner_of_op(self, op):
        return self._owners.get(op['op'])

    def _need_capture_ready(self):
        self._need_idle()
        if self.mode is None:
            raise ActionRejected('MODE_REQUIRED')
        if not self.channels:
            raise ActionRejected('NO_CHANNELS')
        if self.drawer['state'] != 'closed':
            raise ActionRejected('DRAWER_NOT_CLOSED')
        if not self.camera_ready():
            raise ActionRejected('CAMERA_COOLING')

    def _cmd_smart_exposure_run(self, roi=None):
        """Smart Exposure: a series of short test exposures per channel (saturated -> 10x shorter, too dim -> 10x longer, at most four); each test
        image is formed at the end of its own exposure with the illumination / fault state of that moment and decides the next test; the brightest
        point of the region is taken from the 3 x 3-smoothed test image (not the single noisiest pixel) and the recommendation puts it at 80 % of
        full scale; not a real image."""
        self._need_capture_ready()
        if roi is not None and not (roi[0] < roi[2] and roi[1] < roi[3]):
            raise ActionRejected('INVALID_ROI')
        if 'filter_wheel_jam' in self.faults:
            return dict(operation_id=self._start_op('smart_exposure', CAPTURE_OVERHEAD_S, fail=('filter_wheel_error', '滤光轮卡住：Smart Exposure中止')))
        geo = self._geometry()
        _, _, X, Y = self._fields(geo, [self.channels[0][0]])
        mask = np.ones(X.shape, dtype=bool) if roi is None else ((X >= roi[0]) & (X <= roi[2]) & (Y >= roi[1]) & (Y <= roi[3]))
        if not mask.any():
            raise ActionRejected('INVALID_ROI')
        chans = list(self.channels)
        te0 = [self._smart_first_te(ch) for ch, _ in chans]
        est = sum(CAPTURE_OVERHEAD_S + te for te in te0)
        first = (CAPTURE_OVERHEAD_S + te0[0], 'test', dict(ch=chans[0][0], te=te0[0], k=0, t1=self.t + CAPTURE_OVERHEAD_S))
        oid = self._start_op('smart_exposure', est, segments=[first], result={}, warnings=[],
                             series=dict(channels=chans, idx=0, geo=geo, mask=mask, te0=te0, nseg=1))
        return dict(operation_id=oid)

    @staticmethod
    def _smart_first_te(ch):
        return {'VISIBLE': 0.002, 'CHEMI': 2.0}.get(ch, TEST_EXPOSURE_S)

    def _smart_test(self, op, d):
        """One Smart Exposure test shot, read out now (its end): book it, then schedule the next test or the next channel."""
        s = op['series']
        ch, te, k, t1 = d['ch'], d['te'], d['k'], d['t1']
        fields, _, _, _ = self._fields(s['geo'], [ch])
        rng = self._rng(f'smart:{op["op"]}:{ch}:{k}')
        img, nsat, e_tot, sat = self._expose(self._signal(fields[ch], ch, t1, t1 + te), te, t1, s['geo'], rng)
        self.led['exposure_time_s'].add(te)
        self.led['photons_detected'].add(e_tot)
        if ch != 'CHEMI' and 'led_failure' not in self.faults:
            self.led['led_energy_j'].add(POWER_LED_W * te)
            self.energy_anchor_j += POWER_LED_W * te
        top = float(_box3(img.astype(float))[s['mask']].max())
        clipped = bool(sat[s['mask']].any())
        nxt = te / 10.0 if clipped and k < 3 else min(20.0, te * 10.0) if (top - BIAS_ADU < 100.0 and k < 3 and te < 20.0) else None
        if nxt is not None:
            self._queue(op, nxt, 'test', dict(ch=ch, te=nxt, k=k + 1, t1=self.t))
        else:
            rate = max(top - BIAS_ADU, 1.0) / te
            if clipped:
                op['warnings'].append(f'saturated_at_shortest_test:{ch}')
            if top - BIAS_ADU < 100.0:
                op['warnings'].append(f'weak_signal:{ch}')
            op['result'][ch] = float(min(EXPOSURE_RANGE_S[1], max(EXPOSURE_RANGE_S[0], SMART_TARGET * (ADC_MAX - BIAS_ADU) / rate)))
            s['idx'] += 1
            if s['idx'] < len(s['channels']):
                ch2 = s['channels'][s['idx']][0]
                te2 = s['te0'][s['idx']]
                self._queue(op, CAPTURE_OVERHEAD_S + te2, 'test', dict(ch=ch2, te=te2, k=0, t1=self.t + CAPTURE_OVERHEAD_S))
        rest = sum(CAPTURE_OVERHEAD_S + te for te in s['te0'][s['idx'] + 1:])
        op['end'] = (max(q[0] for q in op['pending']) if op['pending'] else self.t) + (rest if op['pending'] else 0.0)
        op['warnings'] = op['warnings'][:12]

    def _queue(self, op, dt, name, data):
        s = op['series']
        s['nseg'] += 1
        op['pending'].append((self.t + dt, s['nseg'], name, data))
        op['pending'].sort(key=lambda q: (q[0], q[1]))

    def _cmd_capture_run(self, hdr):
        self._need_capture_ready()
        if hdr and (self.mode != 'chemi_blots' or 'CHEMI' not in [c for c, _ in self.channels]):
            raise ActionRejected('HDR_CHEMI_ONLY')
        if len(self.images) >= GALLERY_MAX:
            raise ActionRejected('GALLERY_FULL')
        plan, dur = self._plan(self.channels, hdr)
        if 'filter_wheel_jam' in self.faults:
            return dict(operation_id=self._start_op('capture', CAPTURE_OVERHEAD_S, fail=('filter_wheel_error', '滤光轮卡住：拍摄中止')))
        self.seq['img'] += 1
        iid = f'IMG{self.seq["img"]:04d}'
        geo = self._geometry()
        segs = [(dt + te, 'expose', dict(ch=ch, part=part, te=te, t1=self.t + dt)) for ch, dye, part, dt, te in plan]
        oid = self._start_op('capture', dur, segments=segs, plan=plan, channels=list(self.channels), mode=self.mode, hdr=bool(hdr), image_id=iid, geo=geo,
                             binning=self.camera['binning'], zoom=self.camera['zoom'], live_frame=self._live_frame(geo))
        return dict(operation_id=oid)

    def _cmd_image_delete(self, image_id):
        self._need_idle()                                                     # a touch-screen command: needs power and an idle instrument
        self._image(image_id)
        self._drop_image(image_id)
        self._event('analysis', 'DELETED', f'图像{image_id}移到回收站并删除', rid=image_id)
        return dict(image_id=image_id, images_left=len(self.images))

    # ---------------------------------------------------------------- gallery, analysis and queries
    def _image(self, image_id):
        img = self.images.get(image_id)
        if img is None:
            raise ActionRejected('UNKNOWN_IMAGE')
        return img

    def _channel(self, img, channel):
        for c in img['channels']:
            if c['channel'] == channel:
                return c
        raise ActionRejected('CHANNEL_NOT_IN_IMAGE')

    def _cmd_measurement_result(self, image_id):
        img = self._image(image_id)
        stats = []
        for c in img['channels']:
            a = c['img']
            ny, nx = a.shape
            by, bx = max(1, math.ceil(ny / 64)), max(1, math.ceil(nx / 64))
            py, px = ny // by, nx // bx
            prev = a[:py * by, :px * bx].astype(float).reshape(py, by, px, bx).mean(axis=(1, 3))
            stats.append(dict(channel=c['channel'], dye=c['dye'], exposure_s=c['exposure_s'], min_adu=int(a.min()), max_adu=int(a.max()), mean_adu=float(a.mean()),
                              saturated_px=c['saturated_px'], saturated_fraction=c['saturated_fraction'], preview=[[int(round(v)) for v in row] for row in prev]))
        return dict(image_id=image_id, captured_s=img['captured_s'], mode=img['mode'], binning=img['binning'], zoom=img['zoom'], hdr=img['hdr'], hdr_scale=img['hdr_scale'], width_px=img['width_px'],
                    height_px=img['height_px'], object_pixel_m=img['object_pixel_m'], channels=stats, warnings=list(img['warnings']), template_source=list(img['template_source']))

    def _cmd_image_analyze(self, image_id, channel, sensitivity, marker_lane=None, marker_molar_mass_kg_mol=None, lane_centres_m=None, frames_m=None, lane_bands_m=None):
        return analyze_image(self._image(image_id), self._channel(self._image(image_id), channel), sensitivity, marker_lane, marker_molar_mass_kg_mol, lane_centres_m,
                             frames_m, lane_bands_m)

    def _cmd_image_quantify(self, image_id, channel, standards, sensitivity, lane_centres_m=None, frames_m=None, lane_bands_m=None):
        res = self._cmd_image_analyze(image_id, channel, sensitivity, lane_centres_m=lane_centres_m, frames_m=frames_m, lane_bands_m=lane_bands_m)
        table = {(l['lane'], b['band']): b for l in res['lanes'] for b in l['bands']}
        pts = []
        for st in standards:
            b = table.get((st['lane'], st['band']))
            if b is None:
                raise ActionRejected('NO_SUCH_BAND')
            pts.append((st['amount_kg'], b['lbc_volume'], b['saturated']))
        if len({(st['lane'], st['band']) for st in standards}) != len(standards):
            raise ActionRejected('DUPLICATE_STANDARD')
        x = np.array([q[0] for q in pts])
        y = np.array([q[1] for q in pts])
        if np.ptp(x) <= 0:
            raise ActionRejected('STANDARDS_NOT_DISTINCT')
        sx = float(np.max(np.abs(x)))
        try:                                                                  # fit on scaled amounts; a degenerate or non-finite fit is refused, never raised
            with np.errstate(all='ignore'):
                s_, icpt = np.polyfit(x / sx, y, 1)
            slope = s_ / sx
        except (np.linalg.LinAlgError, ValueError, FloatingPointError, ZeroDivisionError):
            raise ActionRejected('ANALYSIS_FAILED')
        if not (math.isfinite(slope) and math.isfinite(icpt)):
            raise ActionRejected('ANALYSIS_FAILED')
        pred = slope * x + icpt
        ss = float(np.sum((y - y.mean()) ** 2))
        r2 = 1.0 - float(np.sum((y - pred) ** 2)) / ss if ss > 0 else 0.0
        warn = []
        if any(q[2] for q in pts):
            warn.append('saturated_standard')
        if r2 < 0.95:
            warn.append('poor_linearity')
        if slope <= 0:
            warn.append('nonpositive_slope')
        lo, hi = float(x.min()), float(x.max())
        used = {(st['lane'], st['band']) for st in standards}
        unknowns = []
        for (ln, bn), b in sorted(table.items()):
            if (ln, bn) in used:
                continue
            amt = (b['lbc_volume'] - icpt) / slope if slope > 0 else None
            flag = 'saturated' if b['saturated'] else 'no_calibration' if amt is None else 'below_range' if amt < lo else 'above_range' if amt > hi else 'ok'
            unknowns.append(dict(lane=ln, band=bn, amount_kg=None if amt is None else float(amt), flag=flag))
        return dict(image_id=image_id, channel=channel, slope=float(slope), intercept=float(icpt), r2=float(r2), standards_used=len(pts), linear_range=[lo, hi],
                    unknowns=unknowns[:200], warnings=warn + res['warnings'][:12 - len(warn)])

    def _cmd_image_export(self, image_id, fmt, analysis_16bit):
        self._need_power()
        img = self._image(image_id)
        if fmt == 'jpg' and analysis_16bit:
            raise ActionRejected('FORMAT_NOT_16BIT')
        nbytes = self._export_bytes(img, fmt, analysis_16bit)
        name = f'{image_id}.{fmt}' if not analysis_16bit else f'{image_id}_analysis16.{fmt}'
        img['exported'].append(name)
        self._event('analysis', 'EXPORTED', f'{name}导出到U盘（{nbytes / 1e6:.1f} MB）', rid=image_id)
        return dict(image_id=image_id, file=name, bytes=nbytes)

    @staticmethod
    def _export_bytes(img, fmt, analysis_16bit, channels=None):
        npx = sum(c['img'].size for c in img['channels'] if channels is None or c['channel'] in channels) * img['m'] ** 2
        cam = img['width_px'] * img['height_px']
        if analysis_16bit or fmt == 'g2i':                                    # unadjusted 16-bit image per channel for analysis
            return int({'g2i': 2 * npx + 65536, 'tiff': 2 * npx + 8192, 'png': int(1.2 * npx) + 4096}[fmt])
        return int({'tiff': 3 * cam + 8192, 'png': int(1.8 * cam) + 4096, 'jpg': int(0.3 * cam) + 4096}[fmt])   # publication: 24-bit colour composite

    def _cmd_records_list(self):
        return dict(records=sorted(self.records), images=sorted(self.images))

    def _cmd_events_read(self, since_s):
        return dict(events=[{k: v for k, v in e.items() if k != '_rid'} for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def ledger(self):
        led = {k: v.value() for k, v in self.led.items()}
        led['electrical_energy_j'] = self.energy_anchor_j + self._energy_since_anchor()
        return led

    def _cmd_ledger_read(self):
        return dict(ledger=self.ledger())

    def _cmd_sample_inspect(self, sample_id):
        s = self.samples.get(sample_id)
        if s is None:
            raise ActionRejected('UNKNOWN_ITEM')
        geo = self._geometry()
        A = geo['obj_px_m'] ** 2 * geo['collection']
        exp = {}
        for ch, _ in self.channels:
            per = {}
            for i, lane in enumerate(s['lanes'], 1):
                row = []
                for b in lane['bands']:
                    kind = label_kind(b['label'])
                    if kind == 'absorber':
                        row.append(dict(label=b['label'], absorbance_peak=STAIN_A_M2_KG[b['label']] * b['amount_kg'] / (lane['width_m'] * b['sigma_m'] * math.sqrt(2 * math.pi))))
                        continue
                    n = b['amount_mol'] * N_A if b.get('amount_mol') is not None else b['amount_kg'] * molecules_per_kg(b['label'])
                    r = self._unit_rate(kind, b['label'], ch, s)
                    row.append(dict(label=b['label'], electrons_per_s=n * r * geo['collection'], electrons_per_s_per_binned_px_peak=n * r * A / (lane['width_m'] * b['sigma_m'] * math.sqrt(2 * math.pi))))
                per[f'lane_{i}'] = row
            exp[ch] = per
        return dict(sample_id=sample_id, support=s['support'], lanes=dict(lanes=copy.deepcopy(s['lanes']), width_m=s['W'], height_m=s['H'], location=s['location'], skew_rad=s['skew'],
                                                                                substrate=s['substrate']), expected_signal=exp)

    def _cmd_provenance_read(self):
        return dict(libraries=[dict(path=str(FLUOR_LIB_PATH.relative_to(ROOT)), sha256=hashlib.sha256(FLUOR_LIB_PATH.read_bytes()).hexdigest(), count=len(fluor_lib()['substances']))])

    def _cmd_fault_inject(self, fault, active):
        self._need_plant()
        (self.faults.add if active else self.faults.discard)(fault)
        if fault == 'power_loss':
            if active and self.power:
                self._power_off()
            elif not active and self.switch_on and not self.power and self._mains_ok():
                self._power_up()
        self._event('fault', 'FAULT', f'{fault} {"on" if active else "off"}')
        return dict(faults=sorted(self.faults))

    def _cmd_sim_step(self, dt_s):
        self.step(dt_s)
        return dict(time_s=self.t)

    # ---------------------------------------------------------------- touch-screen app model (user guide ch.2-5); the panel layer (panel_ui.json) calls these
    def _app_init(self):
        self.app = dict(users={}, user=None, settings={k: copy.deepcopy(v[1]) for k, v in APP_SETTINGS.items()}, trans_light=False, names={}, views={},
                        custom_dyes={}, false_colors=dict(DEFAULT_FALSE_COLOR), excision=None, diag=None, pixel_map_s=None, backup=None, reset_pending=False,
                        pixel_map=[], ship_prep=None)
        self.power_epoch = 0
        self.panel = _Panel(PANEL_SPEC, self, PANEL_HOOKS) if PANEL_SPEC else None

    def _app_read(self):
        a = self.app
        return {'app.user': a['user'],
                'app.users': [dict(screen_name=k, admin=u['admin'], created_s=u['created_s'], has_pin=u['pin'] is not None) for k, u in sorted(a['users'].items())],
                'app.settings': copy.deepcopy(a['settings']), 'app.trans_light': a['trans_light'], 'gallery': [self._gallery_row(i) for i in sorted(self.images)],
                'image.views': {k: dict(v) for k, v in sorted(a['views'].items())}, 'dyes.custom': [dict(v, dye=k) for k, v in sorted(a['custom_dyes'].items())],
                'channels.false_colors': dict(a['false_colors']), 'band_excision.on': a['excision'] is not None, 'diagnostics.report': copy.deepcopy(a['diag']),
                'pixel_map.last_s': a['pixel_map_s'], 'settings.backup_s': a['backup']['time_s'] if a['backup'] else None,
                'settings.factory_reset_pending': a['reset_pending'], 'pixel_map.corrected_px': len(a['pixel_map']), 'service.ship_prep': a['ship_prep']}

    def _gallery_row(self, iid):
        img, n = self.images[iid], self.app['names'].get(iid, {})
        return dict(image_id=iid, name=n.get('name', iid), comments=n.get('comments', ''), user=n.get('user'), captured_s=img['captured_s'], mode=img['mode'],
                    hdr=img['hdr'], channels=[c['channel'] for c in img['channels']])

    def _wall(self, t):
        """Wall-clock time of simulation time t in the configured time zone (the clock starts at INSTRUMENT_EPOCH UTC)."""
        import datetime
        tz = self.app['settings']['time_zone']
        off = (1 if tz[3] == '+' else -1) * (int(tz[4:6]) * 60 + int(tz[7:9]))
        return datetime.datetime.fromisoformat(INSTRUMENT_EPOCH) + datetime.timedelta(seconds=t, minutes=off)

    def wall_text(self, t):
        st = self.app['settings']
        w = self._wall(t)
        d = w.strftime({'YYYY-MM-DD': '%Y-%m-%d', 'DD/MM/YYYY': '%d/%m/%Y', 'MM/DD/YYYY': '%m/%d/%Y'}[st['date_format']])
        return d + ' ' + (w.strftime('%H:%M') if st['time_format'] == '24-hour' else w.strftime('%I:%M %p'))

    def _auto_name(self, img, op):
        """File name by the naming convention (user guide p.33): date and time, then up to three chosen aspects in their order."""
        st = self.app['settings']
        parts = [self._wall(img['captured_s']).strftime('%Y%m%d_%H%M%S')]
        for asp in st['naming']:
            parts.append({'Mode': ''.join(x.capitalize() for x in img['mode'].split('_')), 'Binning': f'Bin{img["binning"]}x{img["binning"]}',
                          'Custom Field User': st['custom_field'] or (self.app['user'] or 'Guest'),
                          'Exposure Time': f'{img["channels"][0]["exposure_s"]:g}s' if img['channels'] else '',
                          'Sequential Number': img['image_id'][3:]}[asp])
        return '_'.join(x for x in parts if x)

    def _admin(self):
        u = self.app['users'].get(self.app['user'])
        if not (u and u['admin']):
            raise ActionRejected('ADMIN_REQUIRED')

    def _cmd_user_create(self, screen_name, pin, pin_confirm):
        self._need_power()
        if pin != pin_confirm:
            raise ActionRejected('PIN_MISMATCH')
        if screen_name in self.app['users']:
            raise ActionRejected('ITEM_EXISTS')
        if len(self.app['users']) >= 50:
            raise ActionRejected('TOO_MANY_ITEMS')
        admin = not self.app['users']                                         # the first profile on the instrument is its administrator (assumption)
        self.app['users'][screen_name] = dict(pin=pin, admin=admin, created_s=self.t)
        self.app['user'] = screen_name
        return dict(screen_name=screen_name, admin=admin)

    def _cmd_user_sign_in(self, screen_name, pin):
        self._need_power()
        u = self.app['users'].get(screen_name)
        if u is None:
            raise ActionRejected('UNKNOWN_USER')
        if u['pin'] is None:                                                  # PIN deleted by an admin: the user creates a new one at sign-in
            u['pin'] = pin
        elif pin != u['pin']:
            raise ActionRejected('WRONG_PIN')
        self.app['user'] = screen_name
        return dict(screen_name=screen_name)

    def _cmd_user_sign_out(self):
        self._need_power()
        old, self.app['user'] = self.app['user'], None
        return dict(screen_name=old)

    def _cmd_user_manage(self, screen_name, action):
        self._need_power()
        self._admin()
        u = self.app['users'].get(screen_name)
        if u is None:
            raise ActionRejected('UNKNOWN_USER')
        if action == 'make_admin':
            u['admin'] = True
        elif action == 'delete_pin':
            u['pin'] = None
        else:
            if screen_name == self.app['user']:
                raise ActionRejected('CANNOT_DELETE_SELF')
            self._need_idle()
            for iid in [i for i, n in self.app['names'].items() if n.get('user') == screen_name and i in self.images]:   # the user's gallery goes too
                self._drop_image(iid)
            del self.app['users'][screen_name]
        return dict(screen_name=screen_name, action=action)

    def _drop_image(self, iid):
        del self.images[iid]
        self.app['names'].pop(iid, None)
        self.app['views'].pop(iid, None)

    def _cmd_settings_configure(self, **a):
        self._need_power()
        if not a:
            raise ActionRejected('INVALID_ARGUMENT')
        if 'naming' in a and len(set(a['naming'])) != len(a['naming']):
            raise ActionRejected('INVALID_ARGUMENT')
        if 'security_mode' in a:                                              # Manage Users: administrators only
            self._admin()
        self.app['settings'].update(copy.deepcopy(a))
        return dict(settings=copy.deepcopy(self.app['settings']))

    def _cmd_settings_reset(self, kind):
        self._need_power()
        if kind == 'user_tips':                                               # for the guest; the user guide notes it erases the guest gallery
            if self.app['user'] is not None:
                raise ActionRejected('GUEST_ONLY')
            self._need_idle()
            for iid in [i for i in self.images if self.app['names'].get(i, {}).get('user') is None]:
                self._drop_image(iid)
        else:
            if any(u['admin'] for u in self.app['users'].values()):
                self._admin()
            self.app['reset_pending'] = True
        return dict(kind=kind)

    def _factory_reset(self):
        """Users and settings back to the factory state (images stay, moved to the guest gallery); custom dyes and false colours cleared."""
        self.app.update(users={}, user=None, settings={k: copy.deepcopy(v[1]) for k, v in APP_SETTINGS.items()}, custom_dyes={},
                        false_colors=dict(DEFAULT_FALSE_COLOR), views={}, reset_pending=False, trans_light=False)
        for n in self.app['names'].values():
            n['user'] = None
        self._event('power', 'FACTORY_RESET', '恢复出厂设置已生效（账户与设置已清除）')

    def _cmd_settings_backup(self):
        self._need_power()
        self.app['backup'] = dict(time_s=self.t, settings=copy.deepcopy(self.app['settings']))
        return dict(file='iBright_settings_backup.dat')

    def _cmd_settings_restore(self):
        self._need_power()
        if not self.app['backup']:
            raise ActionRejected('NO_BACKUP')
        self.app['settings'] = copy.deepcopy(self.app['backup']['settings'])
        return dict(settings=copy.deepcopy(self.app['settings']))

    def _cmd_live_set_trans_light(self, enabled):
        self._need_power()
        self.app['trans_light'] = bool(enabled)
        return dict(enabled=bool(enabled))

    def _cmd_routine_run(self, kind, first_s=None, total_s=None, count=None):
        """Routines (user guide Table 6): multi-exposure = five captures at 1/4, 1/2, 1, 2 and 4 x the current exposures (assumption: the presets);
        signal accumulation (one channel) = count consecutive frames of increasing cumulative time from first_s to total_s, image k is the sum of
        frames 1..k (read noise of every frame)."""
        self._need_capture_ready()
        if kind == 'multi_exposure':
            plan_t = [[min(EXPOSURE_RANGE_S[1], max(EXPOSURE_RANGE_S[0], self.exposure.get(ch, 1.0) * f)) for ch, _ in self.channels] for f in (0.25, 0.5, 1.0, 2.0, 4.0)]
        else:
            if len(self.channels) != 1:
                raise ActionRejected('SINGLE_CHANNEL_ONLY')
            if first_s is None or total_s is None or count is None or total_s <= first_s:
                raise ActionRejected('ROUTINE_PARAMETERS')
            cum = [first_s + (total_s - first_s) * k / (count - 1) for k in range(count)]
            plan_t = [[c - (cum[k - 1] if k else 0.0)] for k, c in enumerate(cum)]   # frame durations
        n = len(plan_t)
        if len(self.images) + n > GALLERY_MAX:
            raise ActionRejected('GALLERY_FULL')
        if 'filter_wheel_jam' in self.faults:
            return dict(operation_id=self._start_op('capture', CAPTURE_OVERHEAD_S, fail=('filter_wheel_error', '滤光轮卡住：拍摄程序中止')))
        ids = [f'IMG{self.seq["img"] + 1 + k:04d}' for k in range(n)]
        self.seq['img'] += n
        segs, t = [], 0.0
        for k, tes in enumerate(plan_t):
            for (ch, _), te in zip(self.channels, tes):
                t += CAPTURE_OVERHEAD_S
                segs.append((t + te, 'expose', dict(ch=ch, part=f'r{k}', te=te, t1=self.t + t)))
                t += te
        geo = self._geometry()
        oid = self._start_op('capture', t, segments=segs, channels=list(self.channels), mode=self.mode, hdr=False, image_id=ids[0], geo=geo,
                             binning=self.camera['binning'], zoom=self.camera['zoom'], live_frame=self._live_frame(geo), routine=dict(kind=kind, ids=ids, n=n))
        return dict(operation_id=oid)

    def _form_routine(self, op):
        r = op['routine']
        for k, iid in enumerate(r['ids']):
            if r['kind'] == 'multi_exposure':
                parts = {(ch, 'long'): op['parts'][(ch, f'r{k}')] for ch, _ in op['channels']}
            else:                                                                 # image k = frames 1..k summed (bias once, clipped at full scale)
                ch = op['channels'][0][0]
                frames = [op['parts'][(ch, f'r{j}')] for j in range(k + 1)]
                tot = sum(f[0].astype(float) - BIAS_ADU for f in frames) + BIAS_ADU
                mask = np.logical_or.reduce([f[3] for f in frames]) | (tot >= ADC_MAX)
                img = np.clip(np.round(tot), 0, ADC_MAX).astype(np.uint16)
                parts = {(ch, 'long'): (img, float(max(sum(f[1] for f in frames), np.sum(mask) * op['geo']['m'] ** 2)), float(sum(f[2] for f in frames)), mask)}
            self._form_image(dict(op, image_id=iid, parts=parts, hdr=False))
        rec = self.records[op['record_id']]
        rec['image_id'] = r['ids'][-1]
        self._event('capture', 'CAPTURED', f'拍摄程序{r["kind"]}：{r["n"]}张图像已存入图库（{r["ids"][0]}–{r["ids"][-1]}）', rid=op['record_id'])
        self._finish_op('completed')

    def _cmd_image_view(self, image_id, **a):
        self._need_power()
        img = self._image(image_id)
        chans = [c['channel'] for c in img['channels']]
        if a.get('channel') is not None and a['channel'] not in chans or any(x not in chans for x in a.get('hidden_layers', [])):
            raise ActionRejected('CHANNEL_NOT_IN_IMAGE')
        v = dict(self.app['views'].get(image_id, {}), **copy.deepcopy(a))
        if v.get('black', 0) >= v.get('white', ADC_MAX):
            raise ActionRejected('BAD_LEVELS')
        self.app['views'][image_id] = v
        return dict(image_id=image_id, view=dict(v))

    def view_of(self, image_id):
        return dict({k: copy.deepcopy(d) for k, (_, d) in VIEW_SETTINGS.items()}, **self.app['views'].get(image_id, {}))

    def _cmd_image_rename(self, image_id, name, comments):
        self._need_power()
        self._image(image_id)
        self.app['names'].setdefault(image_id, dict(name=image_id, comments='', user=None)).update(name=name, comments=comments)
        return dict(image_id=image_id, name=name)

    def _cmd_export_run(self, image_ids, destination, file_type, separate_channels):
        """Export (user guide p.25-26): only the USB drive in the side port is connected in the simulated configuration (network drive, network
        printer and Connect need a network)."""
        self._need_power()
        imgs = [self._image(i) for i in image_ids]
        if destination != 'usb':
            key = {'network_drive': 'network_drive_location', 'printer': 'printer_ip'}.get(destination)
            raise ActionRejected('NOT_CONFIGURED' if key and not self.app['settings'][key] else 'NO_NETWORK')
        fmt, a16 = EXPORT_TYPES[file_type]
        files, nbytes = [], 0
        for img in imgs:
            base = self.app['names'].get(img['image_id'], {}).get('name', img['image_id'])
            jobs = [(f'{base}.{fmt}', None)]
            if separate_channels and not a16:                                   # publication: the composite and every channel separately
                jobs += [(f'{base}_{c["channel"]}.{fmt}', [c['channel']]) for c in img['channels']]
            for name, chans in jobs:
                b = self._export_bytes(img, fmt, a16, chans)
                files.append(name)
                nbytes += b
                img['exported'].append(name)
            self._event('analysis', 'EXPORTED', f'{base}导出到U盘（{file_type}）', rid=img['image_id'])
        return dict(files=files, bytes=nbytes)

    def _cmd_analysis_export(self, image_id, channel, sensitivity, report_name, marker_lane=None, marker_molar_mass_kg_mol=None, lane_centres_m=None, frames_m=None,
                             lane_bands_m=None):
        self._need_power()
        res = self._cmd_image_analyze(image_id, channel, sensitivity, marker_lane, marker_molar_mass_kg_mol, lane_centres_m, frames_m, lane_bands_m)
        nb = sum(len(l['bands']) for l in res['lanes'])
        files = [f'{report_name}.csv', f'{report_name}.pdf']
        self._event('analysis', 'REPORT', f'{report_name}：分析表与报告导出到U盘', rid=image_id)
        return dict(files=files, bytes=400 + 160 * (nb + len(res['lanes'])) + 250000, lanes=len(res['lanes']), bands=nb)

    def _cmd_dye_define(self, name, excitation_nm, emission_nm, false_color):
        self._need_power()
        did = 'custom_' + name.strip().lower().replace(' ', '_').replace('-', '_')
        if did in self.app['custom_dyes'] or did in fluor_lib()['substances']:
            raise ActionRejected('ITEM_EXISTS')
        if len(self.app['custom_dyes']) >= 40:
            raise ActionRejected('TOO_MANY_ITEMS')
        self.app['custom_dyes'][did] = dict(name=name, excitation_nm=float(excitation_nm), emission_nm=float(emission_nm), false_color=false_color)
        return dict(dye=did)

    def _cmd_channels_false_color(self, channel, color):
        self._need_power()
        self.app['false_colors'][channel] = color
        return dict(channel=channel, color=color)

    def _excision_off(self):
        x = self.app['excision']
        if x is not None:                                                     # the green transilluminator's energy for the time it was on
            e = POWER_LED_W * max(0.0, self.t - x['start_s'])
            self.led['led_energy_j'].add(e)
            self.energy_anchor_j += e
            self.app['excision'] = None

    def _cmd_band_excision_start(self):
        self._need_idle()
        oid = None
        if self.drawer['state'] != 'open':
            oid = self._cmd_drawer_open()['operation_id']
        self.app['excision'] = dict(start_s=self.t)
        self._event('drawer', 'BAND_EXCISION', '切胶：抽屉打开，绿光透射台开')
        return dict(operation_id=oid, on=True)

    def _cmd_band_excision_stop(self):
        self._need_power()
        if self.app['excision'] is None:
            raise ActionRejected('NOT_RUNNING')
        self._excision_off()
        return dict(on=False)

    def _cmd_diagnostics_run(self):
        """Instrument diagnostic (user guide p.36): about 10 min; the drawer opens at 5 min and closes again; the report lists each hardware item."""
        self._need_idle()
        if self.drawer['state'] != 'closed':
            raise ActionRejected('DRAWER_NOT_CLOSED')
        segs = [(300.0, 'diag_drawer', dict(move='open')), (300.0 + DRAWER_S, 'diag_drawer', dict(move='opened')), (320.0, 'diag_drawer', dict(move='close')),
                (320.0 + DRAWER_S, 'diag_drawer', dict(move='closed'))]
        return dict(operation_id=self._start_op('diagnostics', DIAG_S, segments=segs, drawer_ok=True))

    def _diag_drawer(self, op, data):
        d, m = self.drawer, data['move']
        if 'drawer_blocked' in self.faults:
            op['drawer_ok'] = False
            return
        if m in ('open', 'close'):
            d.update(state='opening' if m == 'open' else 'closing', t0=self.t, y0=self._drawer_y(), target=DRAWER_TRAVEL if m == 'open' else 0.0)
        else:
            d.update(y=d['target'], state='open' if m == 'opened' else 'closed')

    def _cmd_service_pixel_mapping(self):
        self._need_idle()
        if self.drawer['state'] != 'closed':
            raise ActionRejected('DRAWER_NOT_CLOSED')
        return dict(operation_id=self._start_op('pixel_mapping', PIXEL_MAP_S))

    def _cmd_service_ship_prep(self, action):
        """Ship Prep Mode (user guide p.35 and "Prepare for shipping" p.38): Ship prep mode opens the drawer, the operator removes the turntable,
        Next, then the instrument is powered off (power-off ends the mode).  Cancel leaves the mode (assumption: the guide shows no cancel)."""
        self._need_power()
        st = self.app['ship_prep']
        if action == 'start':
            self._need_idle()
            if st is not None:
                raise ActionRejected('SHIP_PREP_ACTIVE')
            oid = self._cmd_drawer_open()['operation_id'] if self.drawer['state'] != 'open' else None
            self.app['ship_prep'] = 'remove_turntable'
            self._event('drawer', 'SHIP_PREP', 'Ship Prep Mode：抽屉打开，请取下转台')
            return dict(operation_id=oid, state=self.app['ship_prep'])
        if st is None:
            raise ActionRejected('NOT_RUNNING')
        if action == 'next':
            if st != 'remove_turntable':
                raise ActionRejected('NOT_RUNNING')
            if self.turntable:
                raise ActionRejected('TURNTABLE_PRESENT')
            self.app['ship_prep'] = 'power_off'
            self._event('power', 'SHIP_PREP', 'Ship Prep Mode：转台已取下，请关机')
        else:
            self.app['ship_prep'] = None
        return dict(operation_id=None, state=self.app['ship_prep'])

    def _cmd_turntable_set_installed(self, installed):
        """The operator lifts the turntable (with the transilluminator glass) out of the open drawer, or puts it back (the stage is then at 0)."""
        self._need_plant()
        if self.drawer['state'] != 'open' or self.op is not None:
            raise ActionRejected('DRAWER_NOT_OPEN')
        if not installed and self.on_stage is not None:
            raise ActionRejected('STAGE_OCCUPIED')
        self.turntable = bool(installed)
        self.stage_angle = 0.0
        self._event('drawer', 'TURNTABLE', '转台已装回' if installed else '转台已取下')
        return dict(installed=self.turntable)

    def _cmd_sensor_configure_error(self, hot_pixels, hot_pixel_dark_e_s):
        """Evaluator: hot pixels of the CCD at positions drawn from the noise seed (the stored pixel map is kept: Pixel Mapping finds new ones)."""
        self._need_plant()
        rng = self._rng(f'hot_pixels:{hot_pixels}')
        sites = {(int(rng.integers(0, SENSOR_PX[0])), int(rng.integers(0, SENSOR_PX[1]))) for _ in range(hot_pixels)}
        self.cam_err = dict(hot_pixels=hot_pixels, hot_pixel_dark_e_s=float(hot_pixel_dark_e_s), sites=[list(q) for q in sorted(sites)])
        return dict(hot_pixels=len(sites), hot_pixel_dark_e_s=float(hot_pixel_dark_e_s))

    def _complete_service(self, op):
        if op['kind'] == 'pixel_mapping':                                      # dark frames: every pixel that deviates from the normal dark value
            T = PIXEL_MAP_S                                                    # (excess charge > 5 sigma of a normal pixel) replaces the stored map
            noise = math.sqrt(DARK_E_S_COOLED * T + READ_NOISE_E ** 2)
            found = [list(q) for q in self.cam_err['sites']] if self.cam_err['hot_pixel_dark_e_s'] * T > PIXEL_MAP_SNR * noise else []
            self.app.update(pixel_map=found, pixel_map_s=self.t)
            self._event('camera', 'PIXEL_MAPPING', f'Pixel Mapping完成（{len(found)}个异常像元，已存入校正）')
        else:
            items = [('Camera', self.camera_ready()), ('Filter wheel', 'filter_wheel_jam' not in self.faults), ('Epi-LEDs', 'led_failure' not in self.faults),
                     ('Transilluminator', 'led_failure' not in self.faults), ('Drawer', op['drawer_ok']), ('Turntable', self.turntable), ('Power supply', self._mains_ok())]
            self.app['diag'] = dict(time_s=self.t, passed=all(ok for _, ok in items), items=[dict(item=k, status='Pass' if ok else 'Fail') for k, ok in items])
            self._event('alarm' if not self.app['diag']['passed'] else 'camera', 'DIAGNOSTICS', '仪器自检完成：' + ('全部通过' if self.app['diag']['passed'] else '有失败项'))
        self._finish_op('completed')

    # ---------------------------------------------------------------- front panel: host protocol of the engine (_panel_ui.py), viewport bitmaps and panel.* commands
    def panel_read(self):
        out = self.read('agent')
        u = self.app['users'].get(self.app['user'])
        out.update({'host.powered': self.power, 'host.power_epoch': self.power_epoch, 'host.serial': self.instance_id, 'host.signed_in': self.app['user'] is not None,
                    'host.admin': bool(u and u['admin']), 'host.op_kind': self.op['kind'] if self.op else None, 'host.busy': self.op is not None,
                    'host.channels': [dict(channel=c, dye=d, label=self._dye_name(d) if d else c, exposure_s=self.exposure.get(c, 1.0),
                                           color=self.app['false_colors'].get(c, 'gray')) for c, d in self.channels],
                    'host.ccd_c': self.ccd_temperature() - 273.15, 'host.now_text': self.wall_text(self.t),
                    'host.errors': [dict(when=self.wall_text(e['time_s']), user=self.app['user'] or 'Guest', text=f'{e["code"]}: {e["message_zh"]}')
                                    for e in self.events if e['kind'] in ('alarm', 'fault')][-40:],
                    'host.events': [dict(when=self.wall_text(e['time_s']), kind=e['kind'], text=e['code']) for e in self.events][-60:],
                    'host.captures': [dict(when=self.wall_text(r['started_s']), kind=r['kind'], image=self.app['names'].get(r['image_id'], {}).get('name', r['image_id'] or ''),
                                           status=r['status']) for r in self.records.values()][-60:],
                    'host.dyes': self._dye_list()})
        return out

    def _dye_name(self, d):
        c = self.app['custom_dyes'].get(d)
        return c['name'] if c else fluor_lib()['substances'].get(d, {}).get('name', d)

    def _dye_list(self):
        """Dyes the FL1500 can detect (best channel), library then custom; 'channel' is the filter set the app assigns."""
        out = [dict(x) for x in _library_dyes()]
        for k, v in self.app['custom_dyes'].items():
            ch = next((c for c in ('EX1', 'EX2', 'EX3', 'EX4', 'EX5') if FILTERS_NM[c][1][0] - 15 <= v['emission_nm'] <= FILTERS_NM[c][1][1] + 15
                       and FILTERS_NM[c][0][0] - 15 <= v['excitation_nm'] <= FILTERS_NM[c][0][1] + 15), None)
            if ch:
                out.append(dict(dye=k, name=v['name'], channel=ch, em_nm=round(v['emission_nm'])))
        return out

    def panel_gallery(self, v):
        """Gallery rows the screen lists (user guide p.24, 27): the signed-in user's gallery (guest: the guest gallery), with the guest gallery or
        all users' galleries (admin) when the filter asks; keyword / mode / exposure-mode filters; newest first or by name / mode / size."""
        user = self.app['user']
        admin = bool(self.app['users'].get(user, {}).get('admin'))
        rows = []
        for iid in self.images:
            r = self._gallery_row(iid)
            mine = r['user'] == user or (v.get('f_guest') and r['user'] is None) or (v.get('f_all') and admin)
            kw = (v.get('f_keyword') or '').lower()
            if not mine or (kw and kw not in (r['name'] + ' ' + r['comments']).lower()):
                continue
            if v.get('f_mode') and r['mode'] != v['f_mode'] or v.get('f_hdr') in ('normal', 'hdr') and r['hdr'] != (v['f_hdr'] == 'hdr'):
                continue
            img = self.images[iid]
            rows.append(dict(r, date=self.wall_text(r['captured_s']), mode_text=MODE_TEXT[r['mode']], size_kb=self._export_bytes(img, 'g2i', True) // 1024,
                             selected=iid in (v.get('sel') or []), exposure='HDR' if r['hdr'] else 'Normal'))
        key = v.get('sort') or 'date'
        rows.sort(key=lambda r: (-r['captured_s'],) if key == 'date' else (r['name'],) if key == 'name' else (r['mode'], -r['captured_s']) if key == 'mode'
                  else (-r['size_kb'],))
        return rows

    def analysis_args(self, v, ch, sens):
        """image.analyze arguments of the Analyze page: Adjust lanes (n lanes evenly spaced over each frame), Edit analysis frame (the frames)
        and Adjust bands (the lanes' bands)."""
        img = self.images.get(v.get('img'))
        n = int(v['a_lanes']) if v.get('a_lanes') else 0
        frames = v.get('a_frames') or None
        out = dict(lane_centres_m=None, frames_m=None, lane_bands_m=copy.deepcopy(v.get('a_bands')) or None)
        if frames:
            out['frames_m'] = [dict(rect_m=[float(x) for x in f], **({'lane_centres_m': [(f[2] - f[0]) * (i + 0.5) / n for i in range(n)]} if n else {}))
                               for f in frames]
        elif n and img is not None:                                          # evenly spaced over the found frame
            try:
                fr = analyze_image(img, self._channel(img, ch), sens)['frame_m']
            except ActionRejected:
                return out
            out['lane_centres_m'] = [(fr[2] - fr[0]) * (i + 0.5) / n for i in range(n)]
        return out

    def panel_analysis_result(self, v, ch):
        """The Analyze page's analysis (the same computation as image.analyze with the page's settings) and its frame geometry, cached."""
        iid = v.get('img')
        img = self.images.get(iid) if iid else None
        if img is None or ch is None:
            return dict(lanes=[]), []
        sens = round(float(v.get('a_sens', 0.5)), 3)
        ladder = LADDERS.get(v.get('a_ladder') or '') if v.get('a_marker') else None
        key = json.dumps([iid, ch, sens, v.get('a_marker'), v.get('a_ladder'), v.get('a_lanes'), v.get('a_frames'), v.get('a_bands')], sort_keys=True, default=str)
        cache = self.__dict__.setdefault('_acache', {})
        if key not in cache:
            geo = []
            try:
                res = analyze_image(img, self._channel(img, ch), sens, v.get('a_marker') if ladder else None, ladder, geo_out=geo, **self.analysis_args(v, ch, sens))
            except ActionRejected as exc:
                res, geo = dict(error=str(exc), lanes=[]), []
            cache[key] = (res, geo)
        return cache[key]

    def panel_analysis(self, v, field='rows'):
        """Analysis on the Analyze page (same computation as image.analyze) for the page's image, dye / channel, sensitivity, marker lane, lanes,
        frames and bands; 'rows' = the lane and band table in the chosen output, 'summary' = the line under the image."""
        iid = v.get('img')
        img = self.images.get(iid) if iid else None
        if img is None:
            return [] if field == 'rows' else ''
        ch = v.get('a_ch') if v.get('a_ch') in [c['channel'] for c in img['channels']] else img['channels'][0]['channel']
        res, _ = self.panel_analysis_result(v, ch)
        if field == 'summary':
            nb = sum(len(l['bands']) for l in res['lanes'])
            nf = len(res.get('frames_m') or [1])
            return res.get('error') or (f'{ch}: {len(res["lanes"])} lanes, {nb} bands (sensitivity {round(float(v.get("a_sens", 0.5)), 3):.1f})'
                                        + (f', {nf} frames' if nf > 1 else ''))
        out = v.get('a_out') or 'Volume'
        col = {'Volume': ('volume', '{:.4g}'), 'Weight': ('molar_mass_kg_mol', '{:.1f} kDa'), 'Density': ('density', '{:.4g}'), 'LBC Volume': ('lbc_volume', '{:.4g}'),
               'LBC Density': ('lbc_density', '{:.4g}'), 'Rf': ('rf', '{:.3f}'), 'Band Purity': ('band_purity_pct', '{:.1f} %')}[out]
        rows = []
        for l in res['lanes']:
            for b in l['bands']:
                x = b.get(col[0])
                rows.append(dict(lane=l['lane'], band=b['band'], value='--' if x is None else col[1].format(x),
                                 norm='--' if l.get('normalization_factor') is None else f'{l["normalization_factor"]:.3f}', sat='SAT' if b['saturated'] else ''))
        return rows

    def view_window(self, img):
        """Image rows / columns the screen shows (the display zoom shows the centre 1/z, as _view_gray): (row0, row1, col0, col1), ends exclusive."""
        ny, nx = img['channels'][0]['img'].shape
        z = self.view_of(img['image_id'])['view_zoom']
        if z > 1.0:
            cy, cx, hy, hx = ny // 2, nx // 2, max(1, int(ny / z / 2)), max(1, int(nx / z / 2))
            return cy - hy, cy + hy, cx - hx, cx + hx
        return 0, ny, 0, nx

    def analysis_overlay(self, bm, v, ch):
        """The Analyze image with the analysis drawn on it (user guide p.28: the system displays the analysis frames, lanes and bands): frames
        yellow, bands green; while editing, the selected frame / band cyan."""
        img = self.images.get(v.get('img'))
        res, geo = self.panel_analysis_result(v, ch)
        if not bm or img is None or not geo:
            return bm
        H, W = len(bm), len(bm[0])
        r_lo, r_hi, c_lo, c_hi = self.view_window(img)
        p = img['stored_px_m']

        def box(r0, r1, c0, c1, col):                                         # image rows / columns, ends inclusive
            y0, y1 = int(math.floor((r0 - r_lo) * H / (r_hi - r_lo))), int(math.floor((r1 + 1 - r_lo) * H / (r_hi - r_lo))) - 1
            x0, x1 = int(math.floor((c0 - c_lo) * W / (c_hi - c_lo))), int(math.floor((c1 + 1 - c_lo) * W / (c_hi - c_lo))) - 1
            y1, x1 = max(y0, y1), max(x0, x1)
            for x in range(max(x0, 0), min(x1, W - 1) + 1):
                for y in (y0, y1):
                    if 0 <= y < H:
                        bm[y][x] = list(col)
            for y in range(max(y0, 0), min(y1, H - 1) + 1):
                for x in (x0, x1):
                    if 0 <= x < W:
                        bm[y][x] = list(col)
        edit = v.get('a_edit')
        lanes = iter(res['lanes'])
        for fi, g in enumerate(geo):
            for a, b in g['lanes_px']:
                lane = next(lanes, None)
                if lane is None:
                    break
                for band in lane['bands']:
                    sel = edit == 'bands' and v.get('a_blane') == lane['lane'] and v.get('a_bsel') == band['band']
                    box(int(round(band['top_m'] / p + g['rf_top_row'])), int(round(band['bottom_m'] / p + g['rf_top_row'])) - 1, a, b,
                        (0, 220, 255) if sel else (60, 230, 90))
        for fi, g in enumerate(geo):
            box(g['rows'][0], g['rows'][1], g['cols'][0], g['cols'][1], (0, 220, 255) if edit == 'frames' and v.get('a_fsel') == fi else (255, 210, 0))
        return bm

    def panel_invoke(self, capability_id, arguments):
        """A panel action calls the same command a direct call would (each command checks before it changes anything); a gesture made through a
        session keeps that caller's access rules (Session._guard: another agent's images and running operation), as the direct call does."""
        try:
            if self._caller is not None and self._caller in self._principals:
                ThermoIbrightFl1500Session(self, self._caller)._guard(capability_id, arguments)
            return self.invoke(capability_id, **arguments)
        except ActionRejected as exc:
            raise _panel_ui.PanelRejected(str(exc)) from None

    def panel_time(self):
        return self.t

    def viewport(self, image_id, which='main', w=96, channel=None):
        """Bitmap the screen shows (rows of [r, g, b]): the live view (sample outline in the field of view) or a stored image with its display
        adjustments (contrast, gamma / black / white, invert, saturated pixels red, channel or false-colour composite, zoom, membrane overlay,
        auto enhance); which = 'main' (1UP / top of 2UP) or 'composite' (bottom of 2UP)."""
        if not self.power:
            return []
        img = self.images.get(image_id) if image_id else None
        h = int(round(w * FOV_M[1] / FOV_M[0]))
        if img is None:
            return self._live_bitmap(w, h)
        v = self.view_of(image_id)
        chans = {c['channel']: c for c in img['channels']}
        sel = channel if channel in chans else v['channel'] if v['channel'] in chans else img['channels'][0]['channel']
        comp = channel is None and (which == 'composite' or (v['layout'] == '1up' and v['color'] == 'color'))
        layers = [c for c in chans if c not in v['hidden_layers'] and (c != 'VISIBLE' or v['membrane_overlay'] or len(chans) == 1)] if comp else [sel]
        rgb = np.zeros((h, w, 3))
        for ch in layers:
            g, sat = self._view_gray(chans[ch], v, w, h)
            col = np.array(FALSE_RGB[self.app['false_colors'].get(ch, 'gray') if comp else 'gray'], dtype=float) / 255.0
            rgb = np.maximum(rgb, g[..., None] * col[None, None, :]) if comp else g[..., None] * np.ones(3)
            if v['saturation']:
                rgb[sat] = [1.0, 0.0, 0.0]
        out = np.clip(np.round(rgb * 255), 0, 255).astype(int)
        return [[list(map(int, px)) for px in row] for row in out]

    def _view_gray(self, c, v, w, h):
        a = c['img'].astype(float)
        z = v['view_zoom']
        if z > 1.0:                                                           # display zoom: the centre 1/z of the image
            ny, nx = a.shape
            cy, cx, hy, hx = ny // 2, nx // 2, max(1, int(ny / z / 2)), max(1, int(nx / z / 2))
            a, satm = a[cy - hy:cy + hy, cx - hx:cx + hx], c['sat'][cy - hy:cy + hy, cx - hx:cx + hx]
        else:
            satm = c['sat']
        ys, xs = np.linspace(0, a.shape[0], h + 1).astype(int), np.linspace(0, a.shape[1], w + 1).astype(int)
        g = np.array([[a[ys[i]:max(ys[i + 1], ys[i] + 1), xs[j]:max(xs[j + 1], xs[j] + 1)].mean() for j in range(w)] for i in range(h)])
        sat = np.array([[bool(satm[ys[i]:max(ys[i + 1], ys[i] + 1), xs[j]:max(xs[j + 1], xs[j] + 1)].any()) for j in range(w)] for i in range(h)])
        if v['auto_enhance']:                                                 # rolling-ball style background removal of the displayed image only
            g = g - np.array([_rolling_min(row, 6) for row in g]) + BIAS_ADU
        if v['contrast'] == 'raw':
            lo, hi, gam = 0.0, float(ADC_MAX), 1.0
        elif v['contrast'] == 'manual':
            lo, hi, gam = float(v['black']), float(v['white']), float(v['gamma'])
        else:                                                                 # auto contrast: percentile window (high 2-98 %, medium 0.5-99.5 %, low min-max; assumption)
            p = {'high': (2.0, 98.0), 'medium': (0.5, 99.5), 'low': (0.0, 100.0)}[v['contrast']]
            lo, hi = (float(x) for x in np.percentile(g, p))
            gam = 1.0
        n = np.clip((g - lo) / max(hi - lo, 1e-9), 0.0, 1.0) ** (1.0 / gam)
        if v['invert']:
            n = 1.0 - n
        return n, sat

    def _live_bitmap(self, w, h):
        """Live view: the field of view at the current zoom with the placed sample's outline (white Epi-LED; green with Use Trans Light)."""
        base = np.array([0, 120, 40] if self.app['trans_light'] else [40, 40, 40], dtype=int)
        out = np.tile(base, (h, w, 1))
        s = self.samples.get(self.on_stage) if self.on_stage else None
        if s is None or self.drawer['state'] != 'closed':
            return [[list(map(int, px)) for px in row] for row in out]
        fov = self._geometry()['fov']
        th = s['skew'] - self.stage_angle
        X = (np.arange(w) + 0.5) / w * fov[0] - fov[0] / 2
        Y = (np.arange(h) + 0.5) / h * fov[1] - fov[1] / 2
        xx, yy = np.meshgrid(X, Y)
        u = xx * math.cos(th) + yy * math.sin(th)
        q = -xx * math.sin(th) + yy * math.cos(th)
        inside = (np.abs(u) <= s['W'] / 2) & (np.abs(q) <= s['H'] / 2)
        out[inside] = [200, 200, 200] if not self.app['trans_light'] else [120, 220, 140]
        return [[list(map(int, px)) for px in row] for row in out]

    def _panel_cmd(self, k, a):
        p = self.panel
        try:
            if k == 'panel.press':
                return dict(accepted=bool(p.press(a['input_id'], a.get('duration_s', 0.1))))
            if k == 'panel.touch':
                return dict(accepted=bool(p.touch(a['display_id'], a['x'], a['y'], a.get('duration_s', 0.1),
                                                  (a['to_x'], a['to_y']) if 'to_x' in a and 'to_y' in a else None)))
            if k == 'panel.switch':
                return dict(accepted=bool(p.switch(a['input_id'], a['position'])))
            if k == 'panel.frame':
                return dict(frame=p.frame())
            data, w, h = p.png_bytes(a['display_id'])
        except _panel_ui.PanelError:
            raise ActionRejected('INVALID_ARGUMENT') from None
        import tempfile
        sha = hashlib.sha256(data).hexdigest()
        d = Path(getattr(self, 'panel_capture_dir', None) or Path(tempfile.gettempdir()) / 'chemistry_sim_panel' / self.instance_id)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f'{a["display_id"]}_{sha[:16]}.png'
        if not path.exists():
            path.write_bytes(data)
        return dict(display_id=a['display_id'], time_s=self.t, image=dict(path=str(path), format='png', width=w, height=h, sha256=sha))


# ---------------------------------------------------------------- image analysis (user guide p.28-29 definitions)
def _runs(mask, min_len, max_gap):
    """[start, end] index runs of True in a 1-D mask (gaps up to max_gap merged, runs shorter than min_len dropped)."""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    runs, a, b = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - b - 1 <= max_gap:
            b = i
        else:
            runs.append((a, b))
            a = b = i
    runs.append((a, b))
    return [(int(a), int(b)) for a, b in runs if b - a + 1 >= min_len]


def _robust_sd(v):
    v = np.asarray(v, dtype=float)
    if v.size < 3:
        return 0.0
    d = np.diff(v)
    return float(1.4826 * np.median(np.abs(d - np.median(d))) / math.sqrt(2.0))


def _rolling_min(v, w):
    """Rolling-minimum then rolling-mean baseline (a 1-D rolling-ball background along the lane)."""
    n = v.size
    w = max(1, int(w))
    pad = np.pad(v, w, mode='edge')
    mn = np.array([pad[i:i + 2 * w + 1].min() for i in range(n)])
    pad2 = np.pad(mn, w, mode='edge')
    c = np.cumsum(np.insert(pad2, 0, 0.0))
    return (c[2 * w + 1:] - c[:-(2 * w + 1)])[:n] / (2 * w + 1)


def _split_at_valleys(cp, a, b, min_sep):
    """Split the column run [a, b] at valleys deeper than half of the smaller neighbouring peak (adjacent lanes separated by a narrow gap)."""
    seg = cp[a:b + 1]
    if seg.size < 3:
        return [(a, b)]
    peaks = [i for i in range(1, seg.size - 1) if seg[i] >= seg[i - 1] and seg[i] >= seg[i + 1]] or [int(np.argmax(seg))]
    kept = []
    for i in sorted(peaks, key=lambda q: -seg[q]):                           # strongest peaks first, at least min_sep apart
        if all(abs(i - j) >= min_sep for j in kept):
            kept.append(i)
    kept.sort()
    cuts = []
    for l_, r_ in zip(kept, kept[1:]):
        v = l_ + int(np.argmin(seg[l_:r_ + 1]))
        if seg[v] < 0.5 * min(seg[l_], seg[r_]):
            cuts.append(v)
    runs, start = [], a
    for v in cuts:
        runs.append((start, a + v - 1))
        start = a + v + 1
    runs.append((start, b))
    return [(x, y) for x, y in runs if y >= x]


def analyze_image(img, chan, sensitivity, marker_lane=None, marker_mass=None, lane_centres_m=None, frames_m=None, lane_bands_m=None, geo_out=None):
    """Lanes and bands found from the 16-bit image (sensitivity 0-1 lowers the thresholds): Volume (sum of pixel intensities above the camera
    offset, in camera pixels), Density, Local Background Corrected Volume / Density (the surround 2 camera pixels wide), Rf (distance / gel length
    of the sample frame), Band Purity (percent of the lane's corrected band volumes), Lane Purity, lane total / rolling-ball background / adjusted
    volume and the normalization factor (lane 1 = 1), molar mass from a marker lane (log M interpolated in Rf).  Frame: the membrane / gel detected
    in the image, else in the image's visible channel, else the outline from the live view.  Lanes: column runs split at valleys, gaps at the
    regular lane pitch kept as empty lanes, or the lane centres given by the user (Adjust lanes).  Saturation from the readout mask (white clip
    in the inverted visible channel).  The visible channel is inverted (stained bands absorb).
    Edit analysis frame (user guide p.28, 30): frames_m = 1-4 frames [{rect_m: [x0, y0, x1, y1] in image coordinates (m, centred, y down; as
    frame_m), lane_centres_m (optional, m from that frame's left edge)}] replace the automatic frame; each frame is a gel of its own (Rf, lanes,
    marker within the frame) and the lanes are numbered over the frames in order.  Adjust bands (p.28): lane_bands_m = [{lane, bands_m: [[top,
    bottom], ...]}] gives exactly the bands of these lanes (m below the gel top, as y_m / top_m / bottom_m; neighbouring bands may share one
    image row, which then counts half for each); a band given by the user is reported even when it is not above its local background.
    geo_out (a list, panel only): receives per frame dict(rows, cols, rf_top_row, rf_rows) for drawing and editing on the screen."""
    raw = chan['img'].astype(float) - BIAS_ADU
    sat_mask = chan.get('sat')
    if sat_mask is None:
        sat_mask = chan['img'] >= ADC_MAX
    m, p = img['m'], img['stored_px_m']
    ny, nx = raw.shape
    warns = []
    min_frame_px = max(3, int(round(0.02 / p)))

    def frame_of(a):
        """Sample frame: the membrane / gel is brighter than the stage glass (autofluorescence, reflectance); None if not detectable."""
        noise = max(_robust_sd(a.ravel()[:: max(1, a.size // 20000)]), 0.5)     # at least the ADU quantisation
        fr = [0, ny - 1, 0, nx - 1]
        found = 0
        for axis, (i0, i1) in ((1, (0, 1)), (0, (2, 3))):
            prof = np.median(a, axis=axis)
            lo, hi = np.percentile(prof, 2), np.percentile(prof, 90)             # glass level vs sample level (the sample may cover < 40 % of the view)
            if hi - lo > max(6 * noise / math.sqrt(a.shape[axis]), 1.5):             # above the noise and the 1-ADU quantisation of the medians
                r = _runs(prof > 0.5 * (lo + hi), 2, 2)
                if r:
                    fr[i0], fr[i1] = max(r, key=lambda q: q[1] - q[0])
                    found += 1
        if not found or fr[1] - fr[0] + 1 < min_frame_px or fr[3] - fr[2] + 1 < min_frame_px:
            return None
        return fr
    if frames_m:                                                              # Edit analysis frame: the user's 1-4 frames (snapped to the image grid)
        frames = []
        min_user_px = max(3, int(round(USER_FRAME_MIN_M / p)))
        for f in frames_m:
            x0, y0, x1, y1 = f['rect_m']
            c0, c1 = int(round(x0 / p + nx / 2)), int(round(x1 / p + nx / 2)) - 1
            r0, r1 = int(round(y0 / p + ny / 2)), int(round(y1 / p + ny / 2)) - 1
            if c0 < 0 or r0 < 0 or c1 > nx - 1 or r1 > ny - 1:
                raise ActionRejected('FRAME_OUTSIDE_IMAGE')
            if c1 - c0 + 1 < min_user_px or r1 - r0 + 1 < min_user_px:
                raise ActionRejected('FRAME_TOO_SMALL')
            if any(r0 <= q[1] and q[0] <= r1 and c0 <= q[3] and q[2] <= c1 for q, _, _ in frames):
                raise ActionRejected('FRAMES_OVERLAP')
            frames.append(([r0, r1, c0, c1], (float(r0), float(r1 - r0 + 1)), f.get('lane_centres_m') or lane_centres_m))
        warns.append('frames_from_user')
    else:
        frame = frame_of(raw)
        if frame is None:
            vis = next((c for c in img['channels'] if c['channel'] == 'VISIBLE'), None)
            frame = frame_of(vis['img'].astype(float) - BIAS_ADU) if vis is not None else None
        rf_ref = None                                                         # (top row, length in rows) of the gel for Rf; defaults to the analysis frame
        if frame is None and img.get('live_frame_m'):
            x0, y0, x1, y1 = img['live_frame_m']
            c0, c1 = int(max(0, math.floor(x0 / p + nx / 2))), int(min(nx - 1, math.ceil(x1 / p + nx / 2) - 1))
            r0, r1 = int(max(0, math.floor(y0 / p + ny / 2))), int(min(ny - 1, math.ceil(y1 / p + ny / 2) - 1))
            frame = [r0, r1, c0, c1]
            rf_ref = (y0 / p + ny / 2, (y1 - y0) / p)                         # the whole sample, also when the zoomed view shows only part of it
            warns.append('frame_from_live_view')
        if frame is None:
            frame = [0, ny - 1, 0, nx - 1]
            warns.append('frame_is_whole_image')
        if rf_ref is None:
            rf_ref = (float(frame[0]), float(frame[1] - frame[0] + 1))
        frames = [(frame, rf_ref, lane_centres_m)]
    manual = {}
    for e in lane_bands_m or []:                                              # Adjust bands: the user's bands of these lanes
        if e['lane'] in manual:
            raise ActionRejected('INVALID_ARGUMENT')
        if any(not top < bot for top, bot in e['bands_m']):
            raise ActionRejected('BAND_OUTSIDE_FRAME')
        manual[e['lane']] = sorted((float(top), float(bot)) for top, bot in e['bands_m'])
    inverted = chan['channel'] == 'VISIBLE'
    if inverted:
        warns.append('visible_channel_inverted')
    k = 1.0 - float(sensitivity)
    ring = max(1, int(math.ceil(LBC_RING_M / p - 1e-9)))                        # the surround: 2 camera pixels at zoom 1 (fixed in object space)
    lanes, rects = [], []
    sat_any = False
    for fi, ((r0, r1, c0, c1), rf_ref, centres) in enumerate(frames, 1):
        sub = raw[r0:r1 + 1, c0:c1 + 1]
        smk = sat_mask[r0:r1 + 1, c0:c1 + 1]
        work = (np.percentile(sub, 99) - sub).clip(min=0.0) if inverted else sub
        if smk.mean() > 0.2:
            warns.append('image_largely_saturated')
        H, Wd = work.shape
        sm = np.apply_along_axis(lambda v: np.convolve(v, np.ones(5) / 5, mode='same'), 0, work) if H >= 5 else work
        cp = sm.max(axis=0) - np.median(sm)                                  # any band in the column (a lane may hold a single narrow band)
        cp = np.convolve(cp, np.ones(3) / 3, mode='same')
        sd_pix = max(_robust_sd(work.ravel()[:: max(1, work.size // 50000)]), 0.5)   # pixel noise from differences (robust to lanes covering most columns)
        thr = max(float(np.percentile(cp, 10)) + (6 + 10 * k) * sd_pix / math.sqrt(5.0), 1e-9)          # from the pixel noise, not from the brightest lane
        min_w = max(2, int(round(1e-3 / p)))
        runs = []
        for a_, b_ in _runs(cp > thr, min_w, max(0, int(round(0.4e-3 / p)))):
            runs.extend(_split_at_valleys(cp, a_, b_, max(2, int(round(1.5e-3 / p)))))
        runs = [r_ for r_ in runs if r_[1] - r_[0] + 1 >= min_w][:30]
        bgc = float(np.percentile(cp, 10))
        kw = max(3, 2 * int(round(0.2e-3 / p)) + 1)                             # 0.4 mm smoothing of the column profile
        cpw = np.convolve(cp, np.ones(kw) / kw, mode='same')
        ext = []
        for j, (a_, b_) in enumerate(runs):                                    # lane edges: half-maximum crossings (sub-pixel) + 0.6 mm on each side
            top = float(cpw[a_:b_ + 1].max())
            half = bgc + 0.5 * (top - bgc)
            left_stop = (runs[j - 1][1] + a_) // 2 if j > 0 else 0
            right_stop = (b_ + runs[j + 1][0]) // 2 if j + 1 < len(runs) else Wd - 1
            i0 = a_ + int(np.argmax(cpw[a_:b_ + 1]))
            l_, r_ = i0, i0
            while l_ > left_stop and cpw[l_ - 1] > half:
                l_ -= 1
            while r_ < right_stop and cpw[r_ + 1] > half:
                r_ += 1
            xl = l_ - ((cpw[l_] - half) / (cpw[l_] - cpw[l_ - 1]) if l_ > 0 and cpw[l_] > cpw[l_ - 1] else 0.0)
            xr = r_ + ((cpw[r_] - half) / (cpw[r_] - cpw[r_ + 1]) if r_ < Wd - 1 and cpw[r_] > cpw[r_ + 1] else 0.0)
            mg = LANE_MARGIN_M / p
            ext.append((max(left_stop, int(math.floor(xl - mg + 0.5))), min(right_stop, int(math.ceil(xr + mg - 0.5)))))
        runs = ext
        cen = [0.5 * (a_ + b_) for a_, b_ in runs]
        widths = [b_ - a_ + 1 for a_, b_ in runs]
        empty = set()
        if centres:                                                           # Adjust lanes: the user's lane centres (m from the frame's left edge)
            xs = sorted(float(x) for x in centres)
            if xs[0] < 0.0 or xs[-1] > Wd * p:
                raise ActionRejected('LANE_OUTSIDE_FRAME')
            if len(xs) > 1 and float(np.min(np.diff(xs))) < 1e-3:
                raise ActionRejected('LANES_OVERLAP')
            if xs != [float(x) for x in centres]:
                warns.append('lane_centres_sorted')
            cen = [x / p - 0.5 for x in xs]
            widths = widths or [max(2, int(round(5e-3 / p)))]
            warns.append('lanes_from_user')
        elif len(cen) >= 3:                                                   # keep empty lanes at the regular pitch
            d = np.diff(cen)
            pitch = float(d.min())                                            # every gap must be a whole number of pitches (else no inference)
            if not all(abs(dd / pitch - round(dd / pitch)) < 0.25 for dd in d):
                pitch = float('inf')
            full = [cen[0]]
            for c_, dd in zip(cen[1:], d):
                n_ = int(round(dd / pitch))
                if n_ >= 2 and abs(dd - n_ * pitch) < 0.25 * pitch:
                    for j in range(1, n_):
                        full.append(full[-1] + dd / n_)
                        empty.add(len(full) - 1)
                full.append(c_)
            if empty:
                warns.append('empty_lanes_kept')
            cen = full[:30]
        if not centres and len(cen) >= 3:                                     # a comb is centred on the gel: unequal margins hint at empty edge lanes
            pitch = float(np.min(np.diff(cen)))                               # (reported, not inserted: lanes may be loaded on one side only;
            if abs(cen[0] - ((Wd - 1) - cen[-1])) > 0.75 * pitch:             #  image.analyze lane_centres_m sets the lanes explicitly)
                warns.append('possible_empty_edge_lane')
        elif not centres and len(cen) == 2 and widths and cen[1] - cen[0] > 2.5 * float(np.median(widths)):
            warns.append('possible_empty_lanes')                              # the lane pitch cannot be told from two lanes
        if not cen:
            warns.append('no_lanes_found')
        else:
            W = int(np.median(widths)) if widths else 2
            if len(cen) > 1:
                W = min(W, int(min(np.diff(cen))) - 2)
            W = max(W, 2)
            if widths and max(widths) > 3 * np.median(widths):
                warns.append('lane_width_implausible')
        lanes_px = []
        for c_ in cen:                                                        # clipped to the frame, at least one column wide, never overlapping the previous lane
            a0 = int(round(c_ - W / 2))
            lo_c = min(max(0, a0), Wd - 1)
            if lanes_px:
                lo_c = max(lo_c, lanes_px[-1][1] + 1)
                if lo_c > Wd - 1:
                    raise ActionRejected('LANES_OVERLAP')
            lanes_px.append((lo_c, max(min(Wd - 1, a0 + W - 1), lo_c)))
        base_n = len(lanes)
        for li, (a, b) in enumerate(lanes_px, 1):
            gl = base_n + li                                                  # lane number over all frames
            w = b - a + 1
            core = slice(a + int(0.15 * w), b - int(0.15 * w) + 1)
            rp = work[:, core].sum(axis=1)
            base = _rolling_min(rp, max(2, int(round(ROLL_M / p))))
            r = np.convolve(rp - base, [0.25, 0.5, 0.25], mode='same')
            spans = []
            if gl in manual:                                                  # Adjust bands: the user's bands (rows whose centre lies inside [top, bottom])
                rows = []
                for top, bot in manual[gl]:
                    j0 = int(math.ceil(top / p + rf_ref[0] - r0 - 0.5 - 1e-9))
                    j1 = int(math.floor(bot / p + rf_ref[0] - r0 - 0.5 + 1e-9))
                    if j0 < 0 or j1 > H - 1 or j1 < j0:
                        raise ActionRejected('BAND_OUTSIDE_FRAME')
                    rows.append((j0, j1))
                for (_, a1), (b0, _) in zip(rows, rows[1:]):
                    if b0 < a1:                                               # neighbours may share one row (half each), not more
                        raise ActionRejected('BANDS_OVERLAP')
                for n, (j0, j1) in enumerate(rows):
                    spans.append((j0 + int(np.argmax(r[j0:j1 + 1])), j0, j1, n > 0 and rows[n - 1][1] == j0, n + 1 < len(rows) and rows[n + 1][0] == j1))
            else:
                sd_r = max(_robust_sd(rp), 1e-9)
                tb = (5 + 10 * k) * sd_r
                peaks = [i for i in range(1, H - 1) if r[i] >= r[i - 1] and r[i] > r[i + 1] and r[i] > tb] if (li - 1) not in empty or centres else []
                groups = []                                                    # neighbouring peaks without a valley below half the smaller peak = one band
                for i in peaks:
                    if groups:
                        j = groups[-1][-1]
                        v = j + int(np.argmin(r[j:i + 1]))
                        if r[v] >= 0.5 * min(r[i], r[j]):
                            groups[-1].append(i)
                            continue
                    groups.append([i])
                cuts = [g0[-1] + int(np.argmin(r[g0[-1]:g1[0] + 1])) for g0, g1 in zip(groups, groups[1:])]   # the valley between two resolved bands
                for n, g in enumerate(groups):
                    i = max(g, key=lambda q: r[q])
                    lb = cuts[n - 1] if n > 0 else 0                               # the valley row is shared by both bands (half each)
                    ub = cuts[n] if n < len(cuts) else H - 1
                    half = 0.5 * r[i]                                              # half-maximum width -> sigma (within the valleys)
                    lo_, hi_ = i, i
                    while lo_ > lb and r[lo_ - 1] > half:
                        lo_ -= 1
                    while hi_ < ub and r[hi_ + 1] > half:
                        hi_ += 1
                    sig = max(1.0, (hi_ - lo_ + 1) / 2.355)
                    lo_ = max(lb, g[0] - int(math.ceil(4 * sig)))                   # the band spans +-4 sigma of its half-maximum width, never past a valley
                    hi_ = min(ub, g[-1] + int(math.ceil(4 * sig)))
                    spans.append((i, lo_, hi_, n > 0 and lo_ == lb, n < len(cuts) and hi_ == ub))
            lane_img = work[:, a:b + 1]
            total = float(lane_img.sum()) * m * m
            basem = _rolling_min(lane_img.mean(axis=1), max(2, int(round(ROLL_M / p))))
            bgv = float(basem.sum()) * w * m * m
            bands = []
            for i, y0, y1, half_lo, half_hi in spans[:60]:
                wt = np.ones(y1 - y0 + 1)
                if half_lo:
                    wt[0] = 0.5
                if half_hi:
                    wt[-1] = 0.5
                blk = lane_img[y0:y1 + 1]
                area = float(wt.sum()) * blk.shape[1] * m * m
                vol = float(wt @ blk.sum(axis=1)) * m * m
                ya, yb = max(0, y0 - ring), min(H - 1, y1 + ring)
                xa, xb = max(0, a - ring), min(Wd - 1, b + ring)
                box = np.zeros((yb - ya + 1, xb - xa + 1), dtype=bool)
                box[:, :] = True
                box[y0 - ya:y1 - ya + 1, a - xa:b - xa + 1] = False                  # the band itself
                for (_, s0, s1, _, _) in spans:                                     # the lane rows of the other bands (not background)
                    if (s0, s1) != (y0, y1):
                        q0, q1 = max(ya, s0), min(yb, s1)
                        if q0 <= q1:
                            box[q0 - ya:q1 - ya + 1, a - xa:b - xa + 1] = False
                nring = int(box.sum())
                bgpx = float(work[ya:yb + 1, xa:xb + 1][box].sum()) / nring if nring > 0 else 0.0
                lbc = vol - bgpx * area
                if lbc <= 0.0 and gl not in manual:                                 # a noise peak not above its local background is not a band
                    continue
                sat = bool(smk[ya:yb + 1, xa:xb + 1].any())                         # a clipped pixel in the band or its surround (white clip in visible)
                sat_any |= sat
                yrow = r0 + i + 0.5 - rf_ref[0]                                     # rows below the gel top (wells)
                bands.append(dict(band=len(bands) + 1, y_m=yrow * p, rf=yrow / rf_ref[1], volume=vol, density=vol / area, lbc_volume=lbc, lbc_density=lbc / area,
                                  area_px=int(round(area)), band_purity_pct=0.0, lane_purity_pct=0.0, molar_mass_kg_mol=None, saturated=sat,
                                  top_m=(r0 + y0 - rf_ref[0]) * p, bottom_m=(r0 + y1 + 1 - rf_ref[0]) * p))
            tot_lbc = sum(max(0.0, x['lbc_volume']) for x in bands)
            adj = total - bgv
            for x in bands:
                x['band_purity_pct'] = 100.0 * max(0.0, x['lbc_volume']) / tot_lbc if tot_lbc > 0 else 0.0
                x['lane_purity_pct'] = 100.0 * max(0.0, x['lbc_volume']) / adj if adj > 0 else 0.0
            lanes.append(dict(lane=gl, frame=fi, x_m=(a + b + 1) / 2 * p, width_m=w * p, total_lane_volume=total, background_volume=bgv, adjusted_total_lane_volume=adj,
                              normalization_factor=None, bands=bands))
        fx, fy = -nx / 2 * p + c0 * p, -ny / 2 * p + r0 * p
        rects.append([fx, fy, fx + Wd * p, fy + H * p])
        if geo_out is not None:
            geo_out.append(dict(rows=(r0, r1), cols=(c0, c1), rf_top_row=rf_ref[0], rf_rows=rf_ref[1], lanes_px=[(c0 + a, c0 + b) for a, b in lanes_px]))
    if len(lanes) > 30:
        lanes = lanes[:30]
        warns.append('lanes_truncated')
    if manual and max(manual) > len(lanes):
        raise ActionRejected('NO_SUCH_LANE')
    if manual:
        warns.append('bands_from_user')
    if lanes and lanes[0]['adjusted_total_lane_volume'] > 0:
        ref = lanes[0]['adjusted_total_lane_volume']
        for l in lanes:
            l['normalization_factor'] = l['adjusted_total_lane_volume'] / ref
    if sat_any:
        warns.append('saturated_bands')
    if marker_lane is not None:
        if marker_lane > len(lanes) or not marker_mass:
            warns.append('marker_lane_unavailable')
        else:
            mframe = lanes[marker_lane - 1]['frame']                            # a marker lane serves the lanes of its own frame
            mb = sorted(lanes[marker_lane - 1]['bands'], key=lambda x: x['rf'])
            mm = sorted(marker_mass, reverse=True)
            n = min(len(mb), len(mm))
            if len(mb) != len(mm):
                warns.append('marker_band_count_mismatch')
            if n >= 2:
                rf = np.array([x['rf'] for x in mb[:n]])
                lm = np.log10(np.array(mm[:n]))
                for x, v in zip(mb[:n], mm[:n]):
                    x['molar_mass_kg_mol'] = float(v)
                for l in lanes:
                    if l['lane'] == marker_lane or l['frame'] != mframe:
                        continue
                    for x in l['bands']:
                        q = x['rf']
                        if q <= rf[0]:
                            v = lm[0] + (lm[1] - lm[0]) / (rf[1] - rf[0]) * (q - rf[0]) if rf[1] > rf[0] else lm[0]
                        elif q >= rf[-1]:
                            v = lm[-1] + (lm[-1] - lm[-2]) / (rf[-1] - rf[-2]) * (q - rf[-1]) if rf[-1] > rf[-2] else lm[-1]
                        else:
                            v = float(np.interp(q, rf, lm))
                        x['molar_mass_kg_mol'] = float(10 ** v)
            else:
                warns.append('marker_lane_unavailable')
    for l in lanes:                                                           # every reported number is finite
        vals = [l['x_m'], l['width_m'], l['total_lane_volume'], l['background_volume'], l['adjusted_total_lane_volume']] + \
               ([l['normalization_factor']] if l['normalization_factor'] is not None else []) + [v for b_ in l['bands'] for v in (b_['volume'], b_['lbc_volume'], b_['rf'])]
        if not all(math.isfinite(v) for v in vals):
            raise ActionRejected('ANALYSIS_FAILED')
    return dict(image_id=img['image_id'], channel=chan['channel'], sensitivity=float(sensitivity), inverted=inverted, hdr_scale=img.get('hdr_scale'), frame_m=rects[0],
                frames_m=rects, lanes=lanes, marker_lane=marker_lane, warnings=list(dict.fromkeys(warns))[:12])

ASYNC = {'drawer.open', 'drawer.close', 'smart_exposure.run', 'capture.run', 'routine.run', 'band_excision.start', 'diagnostics.run', 'service.pixel_mapping',
         'service.ship_prep'}
_DATA_GUARDED = {'measurement.result', 'image.analyze', 'image.quantify', 'image.export', 'image.delete', 'image.view', 'image.rename', 'analysis.export'}
# Caller rules (final sync 2026-10-19): during another agent's operation the device commands are refused; physical actions are not (the rear power
# switch cuts the power as a power loss does: the operation fails; bench / drawer handling of samples and the turntable meet the device's own
# interlocks), nor is viewing (image.view: display settings; another agent's images stay record-guarded below).
_PHYSICAL = {'power.set', 'power.press_switch', 'sample.provide', 'sample.place', 'sample.remove', 'turntable.set_installed'}
_CONTROL_GUARDED = {k for k, v in CAPS.items() if v['visibility'] == 'agent' and v['kind'] in ('device_command', 'robot_skill') and k not in UNSUPPORTED
                    and k not in _PHYSICAL and k != 'image.view'}


def _panel_engine():
    try:
        from . import _panel_ui as engine                                   # imported as asset_runtime.thermo_ibrightfl1500 (tests, Matterix bridge)
        return engine
    except ImportError:                                                      # loaded from its file path
        import importlib.util
        import sys
        if '_chemsim_panel_ui' not in sys.modules:
            spec = importlib.util.spec_from_file_location('_chemsim_panel_ui', Path(__file__).with_name('_panel_ui.py'))
            sys.modules['_chemsim_panel_ui'] = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
        return sys.modules['_chemsim_panel_ui']


# ---------------------------------------------------------------- front panel: the touch screen (panel_ui.json) on the shared engine; panel.* stay out of CAPS
ASSET_VERSION = '1.2.0'
_panel_ui = _panel_engine()
PANEL_SPEC = None if os.environ.get('IBRIGHT_PANEL_BUILD') else _panel_ui.load_spec(ASSET_DIR / 'panel_ui.json', capabilities=set(CAPS))
PANEL_CAPS = _panel_ui.capability_definitions(PANEL_SPEC) if PANEL_SPEC else {}
ALL_CAPS = dict(CAPS, **PANEL_CAPS)


@functools.lru_cache(maxsize=1)
def _library_dyes():
    """Library dyes the FL1500 detects, each on the filter set with the highest detected rate (the set the app assigns it to)."""
    out = []
    for k, v in fluor_lib()['substances'].items():
        if v.get('kind') != 'fluorophore':
            continue
        rates = {ch: dye_rate(k, ch) for ch in ('EX1', 'EX2', 'EX3', 'EX4', 'EX5')}
        best = max(rates, key=rates.get)
        if rates[best] > 0:
            out.append(dict(dye=k, name=v['name'], channel=best, em_nm=round(v['em_max_m'] * 1e9) if v.get('em_max_m') else None))
    return tuple(out)


# Panel hooks: small computations the declarative spec cannot express (list composition, nearest preset, coordinate conversion); every command
# a gesture causes is still an 'invoke' of the spec.  p is the PanelUI (p.host the runtime, p.ui['vars'] the screen's UI variables).
EXPOSURE_PRESETS = [0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0]
ZOOM_STEPS = [1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0]


def _v(p):
    return p.ui['vars']


def _cur_img(p, ctx=None, s=None):
    """The image on screen: the one chosen (gallery / view), else the capture made from this screen once it is in the gallery."""
    v, h = _v(p), p.host
    if v.get('img') in h.images:
        return v['img']
    last = max(h.images, default=None)
    return last if v.get('await') and last is not None and last != v.get('cap_from') else None


def _cur_ch(p, ctx=None, s=None):
    chans = [c for c, _ in p.host.channels]
    return _v(p).get('ch') if _v(p).get('ch') in chans else (chans[0] if chans else None)


def _nearest(seq, x):
    return min(range(len(seq)), key=lambda i: (abs(math.log(seq[i]) - math.log(max(x, 1e-9))), i))


def _exp_value(p, ctx, s):
    ch = _cur_ch(p)
    return None if ch is None else float(p.host.exposure.get(ch, 1.0))


def _exp_next(p, ctx, s):
    x = _exp_value(p, ctx, s) or 1.0
    i = _nearest(EXPOSURE_PRESETS, x)
    if (s['step'] > 0 and EXPOSURE_PRESETS[i] <= x) or (s['step'] < 0 and EXPOSURE_PRESETS[i] >= x):
        i += s['step']
    return EXPOSURE_PRESETS[min(max(i, 0), len(EXPOSURE_PRESETS) - 1)]


def _exp_index(p, ctx, s):
    x = _exp_value(p, ctx, s)
    return None if x is None else _nearest(EXPOSURE_PRESETS, x)


def _roi(p, ctx, s):
    """Region of Interest from a drag on the live view: image fractions to metres in the field of view (centred, y down)."""
    ev = ctx.get('ev') or {}
    fw, fh = p.host._geometry()['fov']
    xs = sorted(((ev.get('u', 0.5) - 0.5) * fw, (ev.get('u2', 0.5) - 0.5) * fw))
    ys = sorted(((ev.get('v', 0.5) - 0.5) * fh, (ev.get('v2', 0.5) - 0.5) * fh))
    if xs[1] - xs[0] < 1e-4 or ys[1] - ys[0] < 1e-4:
        return None
    return [round(min(max(q, -0.12), 0.12), 6) for q in (xs[0], ys[0], xs[1], ys[1])]


def _view(p, ctx, s):
    h, v = p.host, _v(p)
    iid = v.get('img') if s.get('which') == 'analysis' else _cur_img(p)
    if s.get('which') == 'analysis' and iid in h.images:
        ch = _analysis_channel(p, ctx, s)
        return h.analysis_overlay(h.viewport(iid, 'main', channel=ch), v, ch)
    return h.viewport(iid, s.get('which', 'main'))


def _view_setting(p, ctx, s):
    iid = _cur_img(p)
    return None if iid is None else p.host.view_of(iid).get(s['setting'])


def _multi_channel(p, ctx, s):
    iid = _cur_img(p)
    return iid is not None and len(p.host.images[iid]['channels']) > 1


def _img_name(p, ctx, s):
    iid = _cur_img(p) or _v(p).get('img')
    return p.host.app['names'].get(iid, {}).get('name', iid or '')


def _img_channels(p, ctx, s):
    iid = _cur_img(p) or _v(p).get('img')
    if iid not in p.host.images:
        return []
    view = p.host.view_of(iid)
    chans = [c['channel'] for c in p.host.images[iid]['channels']]
    shown = view['channel'] if view['channel'] in chans else chans[0]
    return [dict(channel=c, layer=c not in view['hidden_layers'], shown=c == shown, analysis=c == _analysis_channel(p, ctx, s)) for c in chans]


def _layers_toggle(p, ctx, s):
    ch = (ctx.get('row') or {}).get('channel')
    hidden = list(p.host.view_of(_cur_img(p))['hidden_layers'])
    return [x for x in hidden if x != ch] if ch in hidden else hidden + [ch]


def _view_zoom_next(p, ctx, s):
    z = p.host.view_of(_cur_img(p))['view_zoom']
    i = _nearest(ZOOM_STEPS, z)
    return ZOOM_STEPS[min(max(i + s['step'], 0), len(ZOOM_STEPS) - 1)]


def _zoom_next(p, ctx, s):
    i = _nearest(ZOOM_STEPS, p.host.camera['zoom'])
    return ZOOM_STEPS[min(max(i + s['step'], 0), len(ZOOM_STEPS) - 1)]


def _rot_next(p, ctx, s):
    return round(min(max(p.host.stage_angle + s['step'] * math.radians(1.0), -STAGE_ROT_RAD), STAGE_ROT_RAD), 9)


def _dye_sel_init(p, ctx, s):
    return [d if d else c for c, d in p.host.channels]


def _dye_sel_text(p, ctx, s):
    names = {x['dye']: x['name'] for x in p.host._dye_list()}
    return ', '.join(names.get(d, d) for d in _v(p).get('dye_sel') or []) or 'none'


def _dye_toggle(p, ctx, a):
    """Choose a dye: a tap adds the dye (at most four) or removes it again."""
    d = (ctx.get('row') or {}).get('dye')
    sel = list(_v(p).get('dye_sel') or [])
    if d in sel:
        sel.remove(d)
    elif sum(1 for x in sel if x not in CHANNELS) < MAX_DYES:
        sel.append(d)
    else:
        return False
    _v(p)['dye_sel'] = sel
    return True


def _chan_toggle(p, ctx, a):
    sel = list(_v(p).get('dye_sel') or [])
    _v(p)['dye_sel'] = [x for x in sel if x != a['channel']] if a['channel'] in sel else sel + [a['channel']]
    return True


def _dye_remove(p, ctx, a):
    """Remove Dye: the dye of the selected channel leaves the selection; channels.set applies it."""
    ch = _cur_ch(p)
    d = dict(p.host.channels).get(ch)
    sel = [x for x in (_v(p).get('dye_sel') or _dye_sel_init(p, ctx, a)) if x != (d or ch)]
    _v(p)['dye_sel'] = sel
    return p._invoke('channels.set', dict(channels=_channels_arg(p, ctx, a)), None, ctx) if sel else True


def _channels_arg(p, ctx, s):
    """channels.set argument from the chosen dyes: each dye on the filter set the app assigns it to; plain channels (CHEMI, VISIBLE, GREEN_TRANS) as is."""
    where = {x['dye']: x['channel'] for x in p.host._dye_list()}
    out = []
    for d in _v(p).get('dye_sel') or []:
        if d in CHANNELS:
            out.append(dict(channel=d, dye='sybr_safe' if d == 'GREEN_TRANS' and p.host.mode == 'nucleic_acid_gels' else None))
        elif d in where:
            out.append(dict(channel=where[d], dye=d))
    return out or [dict(channel=c, dye=dd) for c, dd in p.host.channels] or [dict(channel='VISIBLE', dye=None)]


def _false_color(p, ctx, s):
    return p.host.app['false_colors'].get(_cur_ch(p) or 'EX1')


def _gallery(p, ctx, s):
    return p.host.panel_gallery(_v(p))


def _toggle_sel(p, ctx, a):
    iid = (ctx.get('row') or {}).get('image_id')
    sel = list(_v(p).get('sel') or [])
    _v(p)['sel'] = [x for x in sel if x != iid] if iid in sel else sel + [iid]
    return True


def _select_all(p, ctx, a):
    _v(p)['sel'] = [r['image_id'] for r in p.host.panel_gallery(_v(p))]
    return True


def _delete_selected(p, ctx, a):
    ok = all([p._invoke('image.delete', dict(image_id=i), None, ctx) for i in list(_v(p).get('sel') or []) if i in p.host.images])
    _v(p)['sel'] = []
    return ok


def _thumb(p, ctx, s):
    rows = p.host.panel_gallery(_v(p))
    return p.host.viewport(rows[s['index']]['image_id'], 'composite', w=48) if s['index'] < len(rows) else []


def _thumb_label(p, ctx, s):
    rows = p.host.panel_gallery(_v(p))
    if s['index'] >= len(rows):
        return ''
    r = rows[s['index']]
    return ('[x] ' if r['selected'] else '[ ] ') + r['name']


def _thumb_toggle(p, ctx, a):
    rows = p.host.panel_gallery(_v(p))
    return a['index'] < len(rows) and _toggle_sel(p, dict(ctx, row=rows[a['index']]), a)


def _sel_first(p, ctx, s):
    sel = [i for i in _v(p).get('sel') or [] if i in p.host.images]
    return sel[0] if sel else None


def _export_names(p, ctx, s):
    return ', '.join(p.host.app['names'].get(i, {}).get('name', i) for i in _v(p).get('exp_ids') or [])


def _export_first(p, ctx, s):
    ids = _v(p).get('exp_ids') or []
    return ids[0] if ids else None


def _export_done(p, ctx, s):
    v = _v(p)
    return f'{len(v.get("exp_ids") or [])} image(s) exported to the USB drive.'


def _edit_name(p, ctx, s):
    iid = (ctx.get('buffer') or {}).get('image_id') or _export_first(p, ctx, s)
    return p.host.app['names'].get(iid, {}).get('name', iid or '')


def _edit_comments(p, ctx, s):
    iid = (ctx.get('buffer') or {}).get('image_id') or _export_first(p, ctx, s)
    return p.host.app['names'].get(iid, {}).get('comments', '')


def _analysis(p, ctx, s):
    return p.host.panel_analysis(dict(_v(p), a_ch=_analysis_channel(p, ctx, s)), s.get('part', 'rows'))


def _analysis_channel(p, ctx, s):
    iid = _v(p).get('img')
    chans = [c['channel'] for c in p.host.images[iid]['channels']] if iid in p.host.images else []
    return _v(p).get('a_ch') if _v(p).get('a_ch') in chans else (chans[0] if chans else None)


def _sens_next(p, ctx, s):
    return round(min(max(float(_v(p).get('a_sens', 0.5)) + 0.1 * s['step'], 0.0), 1.0), 1)


def _ladder(p, ctx, s):
    return LADDERS.get(_v(p).get('a_ladder') or '') if _v(p).get('a_marker') else None


def _naming_at(p, ctx, s):
    n = p.host.app['settings']['naming']
    return n[s['index']] if s['index'] < len(n) else None


def _naming_with(p, ctx, s):
    """The aspect list with this position set to the chosen value (None removes it; duplicates removed, order kept)."""
    n = list(p.host.app['settings']['naming']) + [None] * 3
    n[s['index']] = ctx.get('value')
    out = []
    for x in n[:3]:
        if x is not None and x not in out:
            out.append(x)
    return out


def _naming_example(p, ctx, s):
    h = p.host
    fake = dict(image_id='IMG0007', captured_s=h.t, mode=h.mode or 'chemi_blots', binning=h.camera['binning'],
                channels=[dict(exposure_s=h.exposure.get(h.channels[0][0], 1.0) if h.channels else 1.0)])
    return h._auto_name(fake, None)


def _pixel_map_text(p, ctx, s):
    t = p.host.app['pixel_map_s']
    return '' if t is None else f'{p.host.wall_text(t)}  ({len(p.host.app["pixel_map"])} pixels corrected)'


# ---- Edit analysis frame / Adjust bands on the Analyze image (user guide p.28, 30): taps select, drags draw; the page's vars feed image.analyze
def _an_res(p, ctx, s=None):
    return p.host.panel_analysis_result(_v(p), _analysis_channel(p, ctx, s or {}))


def _an_arg(p, ctx, s):
    """One image.analyze argument of the Analyze page (lane centres, frames or bands), e.g. for the report."""
    v = _v(p)
    ch = _analysis_channel(p, ctx, s)
    if v.get('img') not in p.host.images or ch is None:
        return None
    return p.host.analysis_args(v, ch, round(float(v.get('a_sens', 0.5)), 3))[s['key']]


def _analysis_error(p, ctx, s):
    return _an_res(p, ctx, s)[0].get('error')


def _rowcol(p, img, u, v):
    """Image row / column (fractional) of a point of the Analyze image given in fractions of the part shown (display zoom)."""
    r_lo, r_hi, c_lo, c_hi = p.host.view_window(img)
    return r_lo + v * (r_hi - r_lo), c_lo + u * (c_hi - c_lo)


def _drag(p, ctx):
    """The touch on the Analyze image: (image, start row / column, end row / column, a tap?)."""
    img = p.host.images.get(_v(p).get('img'))
    ev = ctx.get('ev') or {}
    ra, ca = _rowcol(p, img, ev.get('u', 0.0), ev.get('v', 0.0))
    rb, cb = _rowcol(p, img, ev.get('u2', ev.get('u', 0.0)), ev.get('v2', ev.get('v', 0.0)))
    return img, ra, ca, rb, cb, abs(ra - rb) < 1.0 and abs(ca - cb) < 1.0


def _is_tap(p, ctx, s):
    return _drag(p, ctx)[5]


def _frames_now(p, ctx, s=None):
    """The frames being edited: the user's, else the one found automatically (image coordinates, m)."""
    v = _v(p)
    if v.get('a_frames'):
        return [list(f) for f in v['a_frames']]
    return [list(f) for f in (_an_res(p, ctx, s)[0].get('frames_m') or [])]


def _frame_rows(p, ctx, s):
    sel = _v(p).get('a_fsel')
    return [dict(index=i, text=f'Frame {i + 1}', size=f'{(f[2] - f[0]) * 1e3:.1f} x {(f[3] - f[1]) * 1e3:.1f} mm', sel=i == sel)
            for i, f in enumerate(_frames_now(p, ctx, s))]


def _frames_full(p, ctx, s):
    return _v(p).get('a_fsel') is None and len(_frames_now(p, ctx, s)) >= MAX_FRAMES


def _frame_drag(p, ctx, a):
    """A tap selects the frame under the finger (or none); a drag draws a new frame, or moves / resizes the selected one to the dragged
    rectangle (snapped to the image grid)."""
    v = _v(p)
    img, ra, ca, rb, cb, tap = _drag(p, ctx)
    if img is None:
        return False
    ny, nx = img['channels'][0]['img'].shape
    pm = img['stored_px_m']
    frames = _frames_now(p, ctx, a)
    if tap:
        x, y = (ca - nx / 2) * pm, (ra - ny / 2) * pm
        v['a_fsel'] = next((i for i, f in enumerate(frames) if f[0] <= x <= f[2] and f[1] <= y <= f[3]), None)
        return True
    c0, c1 = sorted((int(round(ca)), int(round(cb))))
    r0, r1 = sorted((int(round(ra)), int(round(rb))))
    rect = [(c0 - nx / 2) * pm, (r0 - ny / 2) * pm, (c1 - nx / 2) * pm, (r1 - ny / 2) * pm]
    if v.get('a_fsel') is not None and v['a_fsel'] < len(frames):
        frames[v['a_fsel']] = rect
    else:
        frames.append(rect)
        v['a_fsel'] = len(frames) - 1
    v['a_frames'] = frames
    return True


def _frame_remove(p, ctx, a):
    v = _v(p)
    frames = _frames_now(p, ctx, a)
    del frames[v['a_fsel']]
    v.update(a_frames=frames, a_fsel=None)
    return True


def _frames_apply(p, ctx, a):
    """Apply: new frames renumber the lanes, so band edits made before are dropped."""
    v = _v(p)
    if v.get('a_frames') != v.get('a_frames_old'):
        v['a_bands'] = None
    return True


def _lane_rows(p, ctx, s):
    sel = _v(p).get('a_blane')
    return [dict(lane=l['lane'], text=f'Lane {l["lane"]}', bands=f'{len(l["bands"])} bands', sel=l['lane'] == sel) for l in _an_res(p, ctx, s)[0]['lanes']]


def _band_rows(p, ctx, s):
    v = _v(p)
    lane = next((l for l in _an_res(p, ctx, s)[0]['lanes'] if l['lane'] == v.get('a_blane')), None)
    return [] if lane is None else [dict(band=b['band'], text=f'Band {b["band"]}', span=f'{b["top_m"] * 1e3:.1f} - {b["bottom_m"] * 1e3:.1f} mm',
                                         sel=b['band'] == v.get('a_bsel')) for b in lane['bands']]


def _set_lane_bands(v, lane, spans):
    bands = [e for e in (v.get('a_bands') or []) if e['lane'] != lane]
    if spans is not None:
        bands.append(dict(lane=lane, bands_m=sorted([list(x) for x in spans])))
    v['a_bands'] = sorted(bands, key=lambda e: e['lane']) or None


def _band_drag(p, ctx, a):
    """A tap selects the lane and band under the finger; a drag over a lane adds a band spanning the dragged rows, or resizes the selected band
    of that lane (top / bottom snapped to image rows)."""
    v = _v(p)
    img, ra, ca, rb, cb, tap = _drag(p, ctx)
    res, geo = _an_res(p, ctx, a)
    if img is None or not geo:
        return False
    pm = img['stored_px_m']
    lanes = iter(res['lanes'])
    hit = None
    for g in geo:
        for a_, b_ in g['lanes_px']:
            lane = next(lanes, None)
            if lane is not None and a_ - 0.5 <= ca <= b_ + 1.5 and g['rows'][0] - 0.5 <= ra <= g['rows'][1] + 1.5:
                hit = (lane, g)
    if hit is None:
        return False
    lane, g = hit
    if tap:
        y = (ra - g['rf_top_row']) * pm
        v.update(a_blane=lane['lane'], a_bsel=next((b['band'] for b in lane['bands'] if b['top_m'] <= y <= b['bottom_m']), None))
        return True
    r0, r1 = sorted((int(round(ra)), int(round(rb))))
    if r1 <= r0:
        return False
    span = [(r0 - g['rf_top_row']) * pm, (r1 - g['rf_top_row']) * pm]
    spans = [[b['top_m'], b['bottom_m']] for b in lane['bands']]
    if v.get('a_blane') == lane['lane'] and v.get('a_bsel') is not None and v['a_bsel'] <= len(spans):
        spans[v['a_bsel'] - 1] = span
    else:
        spans.append(span)
    spans.sort()
    _set_lane_bands(v, lane['lane'], spans)
    v.update(a_blane=lane['lane'], a_bsel=spans.index(span) + 1)
    return True


def _band_remove(p, ctx, a):
    v = _v(p)
    lane = next((l for l in _an_res(p, ctx, a)[0]['lanes'] if l['lane'] == v.get('a_blane')), None)
    if lane is None or v.get('a_bsel') is None:
        return False
    _set_lane_bands(v, lane['lane'], [[b['top_m'], b['bottom_m']] for b in lane['bands'] if b['band'] != v['a_bsel']])
    v['a_bsel'] = None
    return True


def _band_auto(p, ctx, a):
    v = _v(p)
    if v.get('a_blane') is None:
        return False
    _set_lane_bands(v, v['a_blane'], None)
    v['a_bsel'] = None
    return True


PANEL_HOOKS = {k[1:] if k.startswith('_') else k: v for k, v in dict(
    view=_view, cur_img=_cur_img, cur_ch=_cur_ch, exp_value=_exp_value, exp_next=_exp_next, exp_index=_exp_index, roi=_roi, view_setting=_view_setting,
    multi_channel=_multi_channel, img_name=_img_name, img_channels=_img_channels, layers_toggle=_layers_toggle, view_zoom_next=_view_zoom_next,
    zoom_next=_zoom_next, rot_next=_rot_next, dye_sel_init=_dye_sel_init, dye_sel_text=_dye_sel_text, dye_toggle=_dye_toggle, chan_toggle=_chan_toggle,
    dye_remove=_dye_remove, channels_arg=_channels_arg, false_color=_false_color, gallery=_gallery, toggle_sel=_toggle_sel, select_all=_select_all,
    delete_selected=_delete_selected, thumb=_thumb, thumb_label=_thumb_label, thumb_toggle=_thumb_toggle, sel_first=_sel_first, export_names=_export_names, export_first=_export_first, export_done=_export_done,
    edit_name=_edit_name, edit_comments=_edit_comments, analysis=_analysis, analysis_channel=_analysis_channel, sens_next=_sens_next, ladder=_ladder,
    naming_at=_naming_at, naming_with=_naming_with, naming_example=_naming_example, pixel_map_text=_pixel_map_text, an_arg=_an_arg,
    analysis_error=_analysis_error, is_tap=_is_tap, frame_rows=_frame_rows, frames_full=_frames_full, frame_drag=_frame_drag, frame_remove=_frame_remove,
    frames_apply=_frames_apply, lane_rows=_lane_rows, band_rows=_band_rows, band_drag=_band_drag, band_remove=_band_remove, band_auto=_band_auto).items()}


class _Panel(_panel_ui.PanelUI):
    """The panel's UI state belongs to the instrument: two panels are equal when their UI states are (state snapshots compare runtimes)."""
    def __eq__(self, o):
        return isinstance(o, _panel_ui.PanelUI) and self.ui == o.ui
    __hash__ = None


class ThermoIbrightFl1500Session:
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
        meta = copy.deepcopy(export_thermo_ibrightfl1500_contract_metadata())
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

    def _foreign_op(self):
        h = self._host
        return h.op is not None and self._foreign(h.op['op'])

    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id)
        h = self._host
        values = h.read(self._role)
        values['operation.started_by_other_agent'] = self._role == 'agent' and self._foreign_op()
        if self._role == 'agent':
            values['result.last_image_id'] = max((k for k in h.images if not self._foreign(k)), default=None)
            values['gallery'] = [r for r in values['gallery'] if not self._foreign(r['image_id'])]
            values['image.views'] = {k: v for k, v in values['image.views'].items() if not self._foreign(k)}
        ids = list(values) if state_ids is None else state_ids
        if not isinstance(ids, list) or any(not isinstance(k, str) for k in ids):
            raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in values for k in ids):
            raise ActionRejected('ACCESS_DENIED')
        return dict(instance_id=instance_id, status='succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                    observations={k: dict(value=copy.deepcopy(values[k]), unit=SLOTS[k]['unit'],
                                          quality='unavailable' if values[k] is None else 'estimated' if SLOTS[k]['origin'] in ('sensor', 'derived') else 'valid',
                                          sample_time_s=h.time_s, sample_window_s=None) for k in ids})

    def _guard(self, capability_id, arguments):
        if self._role != 'agent':
            return
        h = self._host
        if capability_id in _CONTROL_GUARDED and self._foreign_op():
            if capability_id in ('power.set', 'power.press_switch') and not h.power:
                return
            raise ActionRejected('ACCESS_DENIED')
        if capability_id in _DATA_GUARDED and self._foreign(arguments.get('image_id')):
            raise ActionRejected('ACCESS_DENIED')
        if capability_id == 'export.run' and any(self._foreign(i) for i in arguments.get('image_ids') or []):
            raise ActionRejected('ACCESS_DENIED')
        if capability_id == 'image.delete' and arguments.get('image_id') in h.images and h._owners.get(arguments.get('image_id')) != self._principal:
            raise ActionRejected('ACCESS_DENIED')                            # agents delete only their own images (evaluator images are read-only)

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
            h._caller = self._principal
            try:
                result = h.invoke(capability_id, **arguments)
            finally:
                h._caller = None
            if self._role == 'agent':
                if capability_id == 'records.list':
                    result = dict(records=[r for r in result['records'] if not self._foreign(r)], images=[i for i in result['images'] if not self._foreign(i)])
                elif capability_id == 'events.read':
                    result = dict(events=[{k: v for k, v in e.items() if k != '_rid'} for e in h.events
                                          if e['time_s'] >= arguments['since_s'] and not self._foreign(e.get('_rid')) and e['kind'] != 'fault'], dropped=h.events_dropped)
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
        res = copy.deepcopy(h.records[op['record_id']]) if op['record_id'] else dict(drawer_state=h.drawer['state'], drawer_y_m=h._drawer_y())
        return dict(operation_id=operation_id, status='succeeded' if op['phase'] == 'completed' else op['phase'], result=res)

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


ERROR_ZH = {
    'INVALID_ARGUMENT': '参数不符合输入模式或取值范围', 'REQUEST_CONFLICT': '同一request_id对应不同的调用', 'UNKNOWN_INSTANCE': '实例不存在',
    'UNKNOWN_CAPABILITY': '能力不存在', 'UNSUPPORTED': '本配置不支持该能力', 'ACCESS_DENIED': '无权访问（真值槽、另一个agent的图像或操作）',
    'PLANT_REQUIRED': '尚未声明实验室条件', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'MAINS_OUT_OF_RANGE': '电源电压不在100–240 V ±10 %（或断电）',
    'POWER_OFF': '仪器未通电', 'BUSY': '仪器正在执行另一个操作（抽屉、Smart Exposure或拍摄）', 'MODE_REQUIRED': '尚未选择成像模式',
    'DUPLICATE_CHANNEL': '同一通道选了两次', 'CHANNEL_NOT_IN_MODE': '该通道不属于当前模式', 'UNKNOWN_DYE': '荧光染料库中没有该染料',
    'DYE_CHANNEL_MISMATCH': '该染料在此滤光组几乎检测不到（或可见/化学发光通道不设染料）', 'DYE_REQUIRED': '荧光通道需指定染料',
    'TOO_MANY_DYES': '最多同时4种荧光染料', 'CHANNEL_NOT_SELECTED': '该通道未选中', 'SETTINGS_LOCKED': '相机设置已锁定（曝光与变焦）',
    'UNSUPPORTED_BINNING': '像素合并只能是1、2、3、4、5、6或8', 'DRAWER_NOT_CLOSED': '抽屉未关闭', 'DRAWER_OPEN': '抽屉已打开',
    'DRAWER_CLOSED': '抽屉已关闭', 'DRAWER_NOT_OPEN': '抽屉未打开（或正在移动）', 'ITEM_EXISTS': 'ID已存在', 'TOO_MANY_ITEMS': '样品数量超过上限（20）',
    'LANE_OUTSIDE_SAMPLE': '泳道超出样品宽度', 'BAND_OUTSIDE_SAMPLE': '条带超出样品高度', 'UNKNOWN_LABEL': '未知的标记（荧光染料库、hrp或可见染色剂）',
    'AMOUNT_REQUIRED': '每个条带须且只能给amount_mol或amount_kg之一', 'AMOUNT_UNIT_MISMATCH': '标记与量的单位不符（染料/HRP用mol，可见染色与蛋白/核酸染料按kg）',
    'UNKNOWN_ITEM': '样品不存在', 'SAMPLE_NOT_ON_BENCH': '样品不在实验台上', 'STAGE_OCCUPIED': '样品台上已有样品', 'SAMPLE_TOO_LARGE': '样品（含摆放偏角）超出透射玻璃24×20 cm',
    'SAMPLE_NOT_ON_STAGE': '该样品不在样品台上', 'NO_CHANNELS': '未选通道', 'CAMERA_COOLING': 'CCD尚未制冷到设定点（开机后约4 min）',
    'INVALID_ROI': '感兴趣区无效或不在视场内', 'HDR_CHEMI_ONLY': 'Smart Range HDR只用于化学发光印迹模式的化学发光通道', 'GALLERY_FULL': '图库已满（30张，先用image.delete删除图像）',
    'RECORDS_FULL': '记录数已达上限', 'UNKNOWN_IMAGE': '图像不存在', 'CHANNEL_NOT_IN_IMAGE': '该图像没有这个通道', 'NO_SUCH_BAND': '分析结果中没有该泳道/条带',
    'DUPLICATE_STANDARD': '同一条带重复作为标准', 'STANDARDS_NOT_DISTINCT': '标准的量必须不全相同', 'FORMAT_NOT_16BIT': 'JPG为8位，不能作16位分析图导出',
    'ANALYSIS_FAILED': '分析得到非有限值（不输出结果）', 'LANE_OUTSIDE_FRAME': '给定的泳道中心在分析框之外（从分析框左边缘起算，m）', 'LANES_OVERLAP': '给定的泳道中心重复或间距小于1 mm',
    'UNKNOWN_OPERATION': '操作不存在', 'NOT_RUNNING': '操作已结束', 'CANCEL_UNSUPPORTED': '抽屉运动不能取消',
    'PIN_MISMATCH': '两次输入的PIN不一致', 'UNKNOWN_USER': '没有该屏名的账户', 'WRONG_PIN': 'PIN错误', 'ADMIN_REQUIRED': '需要管理员登录',
    'CANNOT_DELETE_SELF': '不能删除正在登录的账户', 'GUEST_ONLY': '只能由访客重置用户提示', 'NO_BACKUP': 'U盘上没有设置备份',
    'NOT_CONFIGURED': '未配置网络驱动器或打印机', 'NO_NETWORK': '没有网络连接（仿真配置无以太网/Wi-Fi）', 'SINGLE_CHANNEL_ONLY': '信号累积只用于单通道',
    'ROUTINE_PARAMETERS': '信号累积需给首张时间、累积总时间（大于首张）与张数', 'BAD_LEVELS': '黑电平须低于白电平',
    'FRAME_OUTSIDE_IMAGE': '分析框超出图像', 'FRAME_TOO_SMALL': '分析框边长小于2 mm', 'FRAMES_OVERLAP': '分析框互相重叠',
    'NO_SUCH_LANE': '给定条带的泳道不在分析结果中', 'BAND_OUTSIDE_FRAME': '给定条带的上下边无效或超出分析框（至少一行图像像素）',
    'BANDS_OVERLAP': '同一泳道的给定条带重叠（相邻条带最多共用一行）', 'SHIP_PREP_ACTIVE': '已在Ship Prep Mode中', 'TURNTABLE_PRESENT': '转台还在抽屉里（先取下转台再点Next）',
    'TURNTABLE_REMOVED': '转台已取下（运输准备）：不能放样品或旋转样品台',
}
_OPS = ['operation.kind', 'operation.progress']
_PWR = ['power.enabled', 'lamp.status_on', 'camera.ready', 'drawer.state', 'drawer.y_m'] + _OPS
_CAM = ['camera.binning', 'camera.zoom', 'camera.locked', 'camera.field_of_view_m']
EFFECTS = {
    'plant.configure': ['plant.configured', 'camera.temperature_k'] + _PWR,
    'power.set': _PWR, 'power.press_switch': _PWR,
    'mode.select': ['mode', 'channels.selected', 'display.exposure_s'],
    'drawer.open': ['drawer.state'] + _OPS, 'drawer.close': ['drawer.state'] + _OPS,
    'sample.provide': ['sample.bench_ids'], 'sample.place': ['sample.on_stage', 'sample.bench_ids', 'truth.sample_skew_rad'],
    'sample.remove': ['sample.on_stage', 'sample.bench_ids', 'truth.sample_skew_rad'],
    'channels.set': ['channels.selected', 'display.exposure_s'], 'exposure.set': ['display.exposure_s'],
    'camera.set': _CAM + ['stage.angle_rad', 'truth.sample_skew_rad'],
    'smart_exposure.run': _OPS, 'capture.run': _OPS,
    'image.export': [], 'image.delete': ['result.last_image_id'], 'fault.inject': ['truth.faults'] + _PWR, 'sim.step': ['clock.time_s'],
}
_TIME = ['camera.ready', 'camera.temperature_k', 'clock.time_s']
_MOVE = ['drawer.y_m', 'drawer.state', 'stage.angle_rad', 'camera.zoom', 'camera.field_of_view_m', 'truth.sample_skew_rad'] + _OPS
_ABORT = _MOVE + ['display.exposure_s', 'result.last_image_id']              # a power loss stops the drawer and aborts a pending Smart Exposure / capture
ON_STEP = {'power.set': _TIME + _ABORT, 'power.press_switch': _TIME + _ABORT, 'plant.configure': _TIME + _ABORT, 'fault.inject': _TIME + _ABORT,
           'drawer.open': _MOVE, 'drawer.close': _MOVE, 'smart_exposure.run': _OPS + ['display.exposure_s'], 'capture.run': _OPS + ['result.last_image_id'],
           'sim.step': _TIME + _MOVE + ['display.exposure_s', 'result.last_image_id']}
ON_STEP_ZH = {'power.set': '开机后CCD按约90 s时间常数制冷到室温−30 K（2 K以内就绪）；断电时抽屉停在原处、操作中止', 'power.press_switch': '同power.set',
              'plant.configure': '室温改变：CCD设定点随之改变（曝光中改变时暗电荷按分段温度历史积分）；电源电压超范围断电时抽屉停住、操作中止', 'fault.inject': '断电/抽屉受阻等使进行中的操作失败（不再产生图像或推荐曝光）',
              'drawer.open': '抽屉约4 s内移到0.30 m（受阻时堵转失败）；打开后（未锁定）样品台回正', 'drawer.close': '约4 s关闭；关上后（未锁定）样品台旋转对正样品并光学变焦框住样品',
              'smart_exposure.run': '一系列测试曝光结束后给出推荐曝光并写入各通道（未锁定时）', 'capture.run': '各通道依次曝光（滤光轮/LED切换约1.5 s）后图像存入图库',
              'sim.step': '时钟推进；操作在其精确结束时刻完成；CCD温度与电能为时间的解析函数'}
PRECONDITIONS = {
    'plant.configure': ('噪声种子只能在首次声明时设定', ['plant.configured']),
    'power.set': ('已声明实验室条件；开机时电源电压在90–264 V（100–240 V ±10 %）且未断电', ['plant.configured', 'power.enabled']),
    'power.press_switch': ('同power.set；按一下切换开关位置', ['plant.configured', 'power.enabled']),
    'mode.select': ('通电、无进行中的操作', ['power.enabled', 'operation.kind']),
    'drawer.open': ('通电、空闲、抽屉未打开', ['power.enabled', 'operation.kind', 'drawer.state']),
    'drawer.close': ('通电、空闲、抽屉未关闭', ['power.enabled', 'operation.kind', 'drawer.state']),
    'sample.provide': ('已声明实验室条件；标记在荧光染料库/hrp/可见染色剂之内，量的单位与标记相符，泳道与条带在样品尺寸内', ['sample.bench_ids']),
    'sample.place': ('抽屉已打开、台上无样品、样品在实验台上、转台（透射玻璃）装着、含偏角不超出透射玻璃', ['drawer.state', 'sample.on_stage', 'sample.bench_ids']),
    'sample.remove': ('抽屉已打开、该样品在台上', ['drawer.state', 'sample.on_stage']),
    'channels.set': ('通电、空闲、已选模式；通道属于该模式，荧光通道须指定在该滤光组可检测的染料，最多4种染料', ['power.enabled', 'operation.kind', 'mode']),
    'exposure.set': ('通电、空闲、该通道已选、相机设置未锁定', ['power.enabled', 'channels.selected', 'camera.locked']),
    'camera.set': ('通电、空闲；合并为1/2/3/4/5/6/8；锁定时不改变焦；手动旋转样品台需抽屉关闭且转台装着', ['power.enabled', 'operation.kind', 'camera.locked', 'drawer.state']),
    'smart_exposure.run': ('通电、空闲、已选模式与通道、抽屉关闭、CCD已就绪', ['power.enabled', 'operation.kind', 'mode', 'channels.selected', 'drawer.state', 'camera.ready']),
    'capture.run': ('同smart_exposure.run；HDR只在化学发光印迹模式；图库未满', ['power.enabled', 'operation.kind', 'mode', 'channels.selected', 'drawer.state', 'camera.ready']),
    'image.export': ('通电；图像存在；JPG不作16位分析图', ['power.enabled', 'result.last_image_id']),
    'image.delete': ('通电、空闲；图像存在且由调用者拍摄（agent不能删除评测者或其他agent的图像）', ['power.enabled', 'operation.kind', 'result.last_image_id']),
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
_EDIT_ERRORS = ['FRAME_OUTSIDE_IMAGE', 'FRAME_TOO_SMALL', 'FRAMES_OVERLAP', 'NO_SUCH_LANE', 'BAND_OUTSIDE_FRAME', 'BANDS_OVERLAP']   # Edit analysis frame / Adjust bands
_IDLE = ['PLANT_REQUIRED', 'POWER_OFF', 'BUSY']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED'], 'power.set': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'], 'power.press_switch': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'],
    'mode.select': _IDLE, 'drawer.open': _IDLE + ['DRAWER_OPEN', 'RECORDS_FULL'], 'drawer.close': _IDLE + ['DRAWER_CLOSED', 'RECORDS_FULL'],
    'sample.provide': ['PLANT_REQUIRED', 'ITEM_EXISTS', 'TOO_MANY_ITEMS', 'LANE_OUTSIDE_SAMPLE', 'BAND_OUTSIDE_SAMPLE', 'UNKNOWN_LABEL', 'AMOUNT_REQUIRED', 'AMOUNT_UNIT_MISMATCH'],
    'sample.place': ['PLANT_REQUIRED', 'DRAWER_NOT_OPEN', 'UNKNOWN_ITEM', 'SAMPLE_NOT_ON_BENCH', 'STAGE_OCCUPIED', 'SAMPLE_TOO_LARGE', 'TURNTABLE_REMOVED'],
    'sample.remove': ['PLANT_REQUIRED', 'DRAWER_NOT_OPEN', 'SAMPLE_NOT_ON_STAGE'],
    'channels.set': _IDLE + ['MODE_REQUIRED', 'DUPLICATE_CHANNEL', 'CHANNEL_NOT_IN_MODE', 'UNKNOWN_DYE', 'DYE_CHANNEL_MISMATCH', 'DYE_REQUIRED', 'TOO_MANY_DYES'],
    'exposure.set': _IDLE + ['CHANNEL_NOT_SELECTED', 'SETTINGS_LOCKED'], 'camera.set': _IDLE + ['UNSUPPORTED_BINNING', 'SETTINGS_LOCKED', 'DRAWER_NOT_CLOSED', 'TURNTABLE_REMOVED'],
    'smart_exposure.run': _IDLE + ['MODE_REQUIRED', 'NO_CHANNELS', 'DRAWER_NOT_CLOSED', 'CAMERA_COOLING', 'INVALID_ROI', 'RECORDS_FULL'],
    'capture.run': _IDLE + ['MODE_REQUIRED', 'NO_CHANNELS', 'DRAWER_NOT_CLOSED', 'CAMERA_COOLING', 'HDR_CHEMI_ONLY', 'GALLERY_FULL', 'RECORDS_FULL'],
    'image.analyze': ['UNKNOWN_IMAGE', 'CHANNEL_NOT_IN_IMAGE', 'LANE_OUTSIDE_FRAME', 'LANES_OVERLAP', 'ANALYSIS_FAILED'] + _EDIT_ERRORS,
    'image.quantify': ['UNKNOWN_IMAGE', 'CHANNEL_NOT_IN_IMAGE', 'LANE_OUTSIDE_FRAME', 'LANES_OVERLAP', 'ANALYSIS_FAILED', 'NO_SUCH_BAND', 'DUPLICATE_STANDARD', 'STANDARDS_NOT_DISTINCT']
    + _EDIT_ERRORS,
    'image.export': ['PLANT_REQUIRED', 'POWER_OFF', 'UNKNOWN_IMAGE', 'FORMAT_NOT_16BIT'], 'image.delete': _IDLE + ['UNKNOWN_IMAGE'], 'measurement.result': ['UNKNOWN_IMAGE'],
    'records.list': [], 'events.read': [],
    'ledger.read': [], 'sample.inspect': ['UNKNOWN_ITEM'], 'provenance.read': [], 'fault.inject': ['PLANT_REQUIRED'], 'sim.step': [],
}
MODEL_NONE_ZH = {
    'plant.configure': '只声明宿主实验室条件', 'mode.select': '模式只改变触屏界面（贴图不变）', 'sample.provide': '样品是外部物件（透射玻璃为接口面）',
    'sample.place': '同左', 'sample.remove': '同左', 'channels.set': '通道选择只在过程层', 'exposure.set': '曝光数字经宿主镜像到屏上仿真数字',
    'camera.set': '相机在机身内部，无外部几何', 'smart_exposure.run': '只在过程层', 'capture.run': '只在过程层（图像为过程数据）', 'image.analyze': '只读分析',
    'image.quantify': '只读分析', 'image.export': '导出为数据操作', 'image.delete': '图库数据操作', 'measurement.result': '只读结果', 'records.list': '只读列表', 'events.read': '只读事件',
    'ledger.read': '只读账目（evaluator）', 'sample.inspect': '只读真值（evaluator）', 'provenance.read': '只读来源记录', 'fault.inject': '评估者故障注入', 'sim.step': '推进时钟',
}
SCENE_MIRRORED = {'drawer.y_m', 'stage.angle_rad', 'lamp.status_on', 'display.exposure_s', 'power.enabled'}


def _model_effects(k, un):
    if un:
        return dict(mode='none', description_zh='不支持：不实现，没有模型变化', binding_ids=[])
    seen = sorted((set(EFFECTS.get(k, [])) | set(ON_STEP.get(k, []))) & SCENE_MIRRORED)
    if seen:
        return dict(mode='host_required', binding_ids=[],
                    description_zh='状态经宿主桥（Matterix）镜像到场景关节、灯条或屏上仿真数字（' + '、'.join(seen) + '；绑定见合同model_bindings），运行层本身不驱动模型'
                                   + ('；' + MODEL_NONE_ZH[k] if k in MODEL_NONE_ZH else ''))
    return dict(mode='none', description_zh=MODEL_NONE_ZH.get(k, '没有镜像到场景的状态变化'), binding_ids=[])


EXEC_TEXT = {
    'drawer.open': ('抽屉到达0.30 m（completed；受阻时failed）', '不能取消；断电时抽屉停在原处（stopped）'),
    'drawer.close': ('抽屉关到0并完成自动对正/变焦（completed；受阻时failed）', '不能取消；断电时抽屉停在原处（stopped）'),
    'smart_exposure.run': ('测试曝光序列结束：推荐曝光写入记录与各通道（completed）', '取消或断电：不改变曝光设置，记录为cancelled/failed'),
    'capture.run': ('所有通道曝光结束：图像存入图库，记录写image_id（completed）', '取消或断电：不产生图像，记录为cancelled/failed'),
}
RESOURCES = {'power.set': ['mains'], 'power.press_switch': ['mains'], 'mode.select': ['touch_screen'], 'drawer.open': ['drawer', 'host_clock'], 'drawer.close': ['drawer', 'stage', 'host_clock'],
             'sample.provide': ['bench'], 'sample.place': ['stage'], 'sample.remove': ['stage'], 'channels.set': ['touch_screen'], 'exposure.set': ['touch_screen'],
             'camera.set': ['camera'], 'smart_exposure.run': ['camera', 'illumination', 'host_clock'], 'capture.run': ['camera', 'illumination', 'host_clock'],
             'image.export': ['usb'], 'plant.configure': ['room', 'mains'], 'fault.inject': ['evaluator'], 'sim.step': ['host_clock']}
OBSERVES = {'power.set': ['power.enabled', 'lamp.status_on'], 'power.press_switch': ['power.enabled'], 'mode.select': ['mode', 'channels.selected'],
            'drawer.open': ['drawer.state', 'drawer.y_m'], 'drawer.close': ['drawer.state', 'drawer.y_m', 'stage.angle_rad', 'camera.zoom'],
            'sample.place': ['sample.on_stage'], 'channels.set': ['channels.selected'], 'exposure.set': ['display.exposure_s'], 'camera.set': _CAM,
            'smart_exposure.run': ['display.exposure_s', 'operation.kind'], 'capture.run': ['result.last_image_id', 'operation.kind']}
_WB, _BR, _UG, _FP, _SIM = 'user_workbook', 'manufacturer_primary', 'manufacturer_user_guide', 'fpbase', 'simulation_assumption'
CAP_SOURCES = {k: [_SIM] for k in CAPS}
CAP_SOURCES.update({
    'power.set': [_UG], 'power.press_switch': [_UG], 'mode.select': [_UG], 'drawer.open': [_UG, _BR], 'drawer.close': [_UG, _BR], 'sample.place': [_UG],
    'sample.remove': [_UG], 'sample.provide': [_FP, _SIM], 'channels.set': [_BR, _UG, _FP], 'exposure.set': [_UG], 'camera.set': [_BR, _UG, _WB],
    'smart_exposure.run': [_UG, _BR, _SIM], 'capture.run': [_BR, _UG, _FP, _WB, _SIM], 'image.analyze': [_UG, _SIM], 'image.quantify': [_UG, _SIM],
    'image.export': [_BR], 'image.delete': [_UG], 'measurement.result': [_UG, _BR], 'provenance.read': [_FP]})
CAP_SOURCES.update({k: [_UG] for k in UNSUPPORTED})
PARAM_ZH = {'room_k': '实验室室温（CCD设定点跟随室温）', 'mains_voltage_v': '供电电压', 'noise_seed': '噪声随机数种子（只在首次声明时生效）'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.ibright_imaging.limitations。'
CAP_LIMITS = {
    'capture.run': ['CCD像元数、像元尺寸、满阱、读出噪声、暗电流、量子效率与LED辐照度厂家未公开：9.1 MP按3388×2712、满阱18 000 e、读噪5 e、QE为行间CCD典型曲线、'
                    '各LED在滤光带内的辐照度为工程值；信号与背景的绝对量级是工程估计，通道间的相对量（光谱串扰、染料亮度）由FPbase光谱与消光系数/量子产率计算。',
                    '图像以长边最多768像素存储与分析：合并1–3×时每个存储像素为m×m个合并像元的均值（块和的泊松与读出噪声精确，块内饱和像元按单像元噪声估计），'
                    '最大值与饱和计数因此略平滑；镜头PSF、景深与光漂白未建模；化学发光按底物一阶衰减积分，底物时间取样品准备时刻。',
                    'Smart Range HDR按短曝光（长曝光的1/16）替换长曝光饱和像素后线性压缩到16位；压缩比hdr_scale在measurement.result与image.analyze中给出'
                    '（体积除以hdr_scale换回长曝光的ADU，便于与其它图像比较）。',
                    '暗电荷按CCD温度历史积分（开关机或室温改变处分段；暗电流以名义室温22 °C时的制冷温度265.15 K为基准，每6.3 K翻倍）。'],
    'smart_exposure.run': ['测试曝光的起始值（可见0.002 s、化学发光2 s、荧光0.2 s）与序列规则（饱和则缩短10倍、太弱则延长10倍、最多4次）为工程值；推荐值使感兴趣区最亮像素到满量程80 %；'
                           '每张测试图在它自己的曝光结束时按当时的照明/故障状态形成并决定下一次测试。'],
    'image.analyze': ['泳道与条带用列/行剖面阈值自动检测（阈值按像素噪声，灵敏度越高阈值越低）；样品框由膜/胶与台面玻璃的背景差检测，检测不到时用实时视图的样品外框，再不行用整幅图；'
                      '泳道矩形取列剖面半高边缘外扩0.6 mm；同一泳道内相邻峰之间的谷底低于较小峰一半时分为两条带并在谷底分开，否则视为一条带（肩峰）；条带跨度为半高宽换算σ的±4σ且不越过谷底；'
                      '局部背景为条带矩形外一圈，宽度为0.13 mm（变焦1×、合并1时的2个相机像素）向上取整到存储像素（至少1个：变焦1×合并2时约0.4 mm），与数字变焦无关；其它条带所在的行不计入；滚球背景用8 mm滚动最小值近似；'
                      '相邻两条带之间的谷底行各计一半（间距约3.5σ时两条带的拆分误差随像素网格相位可达约6 %，两者之和正确）；背景校正体积≤0的峰不报告为条带；灵敏度接近1.0时仍可能把个别噪声峰当成条带；'
                      '手动泳道中心须在分析框内且间距≥1 mm（输入无序时按从左到右排序并提示）；两侧留边不对称时只提示possible_empty_edge_lane，不插入空泳道；'
                      '自动只识别一个分析框；编辑分析框（frames_m）给1–4个不重叠的框（边长≥2 mm，按图像网格取整），每个框按一块胶计算Rf与泳道，分子量标准泳道只用于同一框内；'
                      '调整条带（lane_bands_m）给定泳道的条带上下边（按行中心落在其中的图像行取整，相邻条带可共用一行、各计一半），用户给定的条带即使不高于局部背景也报告；'
                      '可见通道自动反相。算法为工程实现，不是厂家软件的复制。'],
    'image.quantify': ['标准曲线为背景校正体积对质量的最小二乘直线；线性范围取标准的最小–最大量；饱和条带标记saturated。'],
    'sample.provide': ['条带与泳道由上游电泳/转印给出；荧光染料的光谱、消光系数与量子产率来自FPbase（无数据时取ε 5×10⁴ M⁻¹cm⁻¹、QY 0.3的假设值），'
                       '核酸染料按结合态量子产率（游离态为其1/20），SYPRO蛋白染料与核酸染料的每千克分子数为工程值；可见染色剂的吸光系数为工程值；HRP按底物的光子产率（工程值）。'],
    'drawer.close': ['关抽屉时（未锁定）按样品偏角旋转样品台（±10°内）并把光学变焦设为使样品外接框占视场的1/1.15（1–2×）；对焦视为自动完成。'],
}
LIMITATIONS = [
    '选定配置：iBright FL1500（荧光+化学发光+可见），右侧USB口插着U盘；菌落计数与E-Gel 96应用（无琼脂平板/E-Gel 96样品模型）、云（Connect）与网络（以太网/Wi-Fi）不建模。',
    '样品在场景中没有几何；放取样品是宿主/机器人抽象，没有接触验证；触摸屏为贴图，屏上曝光数字为仿真UI提示。',
    '相机与照明的工程参数未公开（见capture.run限制）；图像为过程数据，分析按用户指南的输出定义实现。',
    '没有真实iBright与分析软件的硬件接口。',
]

# ---------------------------------------------------------------- app commands (touch-screen model): effects, preconditions, errors and texts
_PW = ['PLANT_REQUIRED', 'POWER_OFF']
_GAL = ['gallery', 'image.views', 'result.last_image_id']
_RESET = ['app.users', 'app.user', 'app.settings', 'app.trans_light', 'dyes.custom', 'channels.false_colors', 'image.views', 'gallery',
          'settings.factory_reset_pending', 'band_excision.on']             # power-up after Reset Factory Defaults; power-down ends band excision
EFFECTS.update({
    'user.create': ['app.users', 'app.user'], 'user.sign_in': ['app.user', 'app.users'], 'user.sign_out': ['app.user'], 'user.manage': ['app.users'] + _GAL,
    'settings.configure': ['app.settings'], 'settings.reset': ['settings.factory_reset_pending'] + _GAL, 'settings.backup': ['settings.backup_s'],
    'settings.restore': ['app.settings'], 'live.set_trans_light': ['app.trans_light'], 'routine.run': list(_OPS), 'image.view': ['image.views'],
    'image.rename': ['gallery'], 'export.run': [], 'analysis.export': [], 'dye.define': ['dyes.custom'], 'channels.false_color': ['channels.false_colors'],
    'band_excision.start': ['band_excision.on', 'drawer.state'] + _OPS, 'band_excision.stop': ['band_excision.on'], 'diagnostics.run': list(_OPS),
    'service.pixel_mapping': list(_OPS), 'service.ship_prep': ['service.ship_prep', 'drawer.state'] + _OPS,
    'turntable.set_installed': ['stage.turntable_installed', 'stage.angle_rad'], 'sensor.configure_error': ['truth.hot_pixels']})
for _k in ('power.set', 'power.press_switch', 'plant.configure', 'fault.inject'):
    EFFECTS[_k] = EFFECTS[_k] + [x for x in _RESET + ['service.ship_prep'] if x not in EFFECTS[_k]]   # power-off ends Ship Prep Mode
EFFECTS['drawer.close'] = EFFECTS['drawer.close'] + ['band_excision.on']
EFFECTS['image.delete'] = EFFECTS['image.delete'] + ['gallery', 'image.views']
ON_STEP.update({'routine.run': _OPS + ['result.last_image_id', 'gallery'], 'band_excision.start': list(_MOVE), 'diagnostics.run': _MOVE + ['diagnostics.report'],
                'service.pixel_mapping': _OPS + ['pixel_map.last_s', 'pixel_map.corrected_px', 'truth.hot_pixels'], 'service.ship_prep': list(_MOVE)})
ON_STEP['capture.run'] = ON_STEP['capture.run'] + ['gallery']
ON_STEP['sensor.configure_error'] = _OPS + ['display.exposure_s']                # hot pixels set during a Smart Exposure change its recommendation
ON_STEP_ZH['sensor.configure_error'] = '进行中的Smart Exposure / 拍摄的暗电荷含新的热像元（推荐曝光与图像随之改变）'
ON_STEP.update({k: ['gallery'] for k in ('user.create', 'user.sign_in', 'user.sign_out', 'user.manage', 'settings.configure', 'settings.restore')})   # owner / name of a pending image
ON_STEP['sim.step'] = ON_STEP['sim.step'] + ['gallery', 'diagnostics.report', 'pixel_map.last_s', 'pixel_map.corrected_px', 'truth.hot_pixels']
for _k in ('power.set', 'power.press_switch', 'plant.configure', 'fault.inject'):
    ON_STEP[_k] = ON_STEP[_k] + ['gallery']
ON_STEP_ZH.update({'routine.run': '各帧依次曝光，结束时多张图像存入图库', 'band_excision.start': '抽屉约4 s打开', 'diagnostics.run': '约10 min：第5 min抽屉打开再关闭，结束时写自检报告',
                   'service.pixel_mapping': '约2 min暗场曝光后存入异常像元图与完成时间', 'service.ship_prep': '抽屉约4 s打开'})
PRECONDITIONS.update({
    'user.create': ('通电；两次PIN一致、屏名未被使用；第一个账户为管理员', ['power.enabled', 'app.users']),
    'user.sign_in': ('通电；账户存在、PIN正确（PIN被删除时本次PIN成为新PIN）', ['power.enabled', 'app.users']),
    'user.sign_out': ('通电', ['power.enabled']), 'user.manage': ('通电、管理员登录；删除账户需空闲且不能删除自己', ['power.enabled', 'app.user', 'app.users']),
    'settings.configure': ('通电；改安全模式需管理员登录', ['power.enabled', 'app.user']),
    'settings.reset': ('通电；重置用户提示只由访客（清除访客图库，需空闲）；有管理员时恢复出厂需管理员', ['power.enabled', 'app.user']),
    'settings.backup': ('通电（U盘在侧面USB口）', ['power.enabled']), 'settings.restore': ('通电，U盘上有备份', ['power.enabled', 'settings.backup_s']),
    'live.set_trans_light': ('通电', ['power.enabled']),
    'routine.run': ('同capture.run；信号累积只用于单通道并需首张时间、累积总时间与张数；图库能容下全部图像', ['power.enabled', 'operation.kind', 'mode', 'channels.selected', 'drawer.state', 'camera.ready']),
    'image.view': ('通电；图像存在，通道/图层属于该图像，黑电平低于白电平', ['power.enabled', 'gallery']), 'image.rename': ('通电；图像存在', ['power.enabled', 'gallery']),
    'export.run': ('通电；图像存在；只有U盘目标可用（网络驱动器/打印机未配置或无网络，Connect无网络）', ['power.enabled', 'gallery', 'app.settings']),
    'analysis.export': ('通电；图像与通道存在', ['power.enabled', 'gallery']), 'dye.define': ('通电；名称未被使用', ['power.enabled', 'dyes.custom']),
    'channels.false_color': ('通电', ['power.enabled']), 'band_excision.start': ('通电、空闲（抽屉未开时先打开抽屉）', ['power.enabled', 'operation.kind', 'drawer.state']),
    'band_excision.stop': ('切胶进行中', ['band_excision.on']), 'diagnostics.run': ('通电、空闲、抽屉关闭', ['power.enabled', 'operation.kind', 'drawer.state']),
    'service.pixel_mapping': ('通电、空闲、抽屉关闭', ['power.enabled', 'operation.kind', 'drawer.state']),
    'service.ship_prep': ('通电；start需空闲且不在该模式（抽屉未开时先打开抽屉）；next需在等取下转台的步骤且转台已取下；cancel需在该模式中',
                          ['power.enabled', 'operation.kind', 'service.ship_prep', 'stage.turntable_installed']),
    'turntable.set_installed': ('已声明实验室条件；抽屉已打开且不在移动；取下时台上无样品', ['drawer.state', 'sample.on_stage']),
    'sensor.configure_error': ('已声明实验室条件', ['plant.configured'])})
CAP_ERRORS.update({
    'user.create': _PW + ['PIN_MISMATCH', 'ITEM_EXISTS', 'TOO_MANY_ITEMS'], 'user.sign_in': _PW + ['UNKNOWN_USER', 'WRONG_PIN'], 'user.sign_out': list(_PW),
    'user.manage': _PW + ['ADMIN_REQUIRED', 'UNKNOWN_USER', 'CANNOT_DELETE_SELF', 'BUSY'], 'settings.configure': _PW + ['ADMIN_REQUIRED'],
    'settings.reset': _PW + ['GUEST_ONLY', 'BUSY', 'ADMIN_REQUIRED'], 'settings.backup': list(_PW), 'settings.restore': _PW + ['NO_BACKUP'],
    'live.set_trans_light': list(_PW),
    'routine.run': _IDLE + ['MODE_REQUIRED', 'NO_CHANNELS', 'DRAWER_NOT_CLOSED', 'CAMERA_COOLING', 'SINGLE_CHANNEL_ONLY', 'ROUTINE_PARAMETERS', 'GALLERY_FULL', 'RECORDS_FULL'],
    'image.view': _PW + ['UNKNOWN_IMAGE', 'CHANNEL_NOT_IN_IMAGE', 'BAD_LEVELS'], 'image.rename': _PW + ['UNKNOWN_IMAGE'],
    'export.run': _PW + ['UNKNOWN_IMAGE', 'NOT_CONFIGURED', 'NO_NETWORK'],
    'analysis.export': _PW + ['UNKNOWN_IMAGE', 'CHANNEL_NOT_IN_IMAGE', 'LANE_OUTSIDE_FRAME', 'LANES_OVERLAP', 'ANALYSIS_FAILED'] + _EDIT_ERRORS,
    'dye.define': _PW + ['ITEM_EXISTS', 'TOO_MANY_ITEMS'], 'channels.false_color': list(_PW), 'band_excision.start': _IDLE + ['DRAWER_OPEN'],
    'band_excision.stop': _PW + ['NOT_RUNNING'], 'diagnostics.run': _IDLE + ['DRAWER_NOT_CLOSED'], 'service.pixel_mapping': _IDLE + ['DRAWER_NOT_CLOSED'],
    'service.ship_prep': _IDLE + ['SHIP_PREP_ACTIVE', 'NOT_RUNNING', 'TURNTABLE_PRESENT'], 'turntable.set_installed': ['PLANT_REQUIRED', 'DRAWER_NOT_OPEN', 'STAGE_OCCUPIED'],
    'sensor.configure_error': ['PLANT_REQUIRED']})
MODEL_NONE_ZH.update({k: '触屏App数据操作，只在过程层' for k in APP_CAPS})
MODEL_NONE_ZH.update({'turntable.set_installed': '转台取下只在过程层：场景中的转台不消失（属于后续Matterix步骤）', 'sensor.configure_error': '评测者设置CCD缺陷，只在过程层'})
EXEC_TEXT.update({
    'routine.run': ('全部帧曝光结束：多张图像存入图库，记录写最后一张的image_id（completed）', '取消或断电：不产生图像，记录为cancelled/failed'),
    'band_excision.start': ('抽屉到达0.30 m，透射台开（completed）', '不能取消；断电时抽屉停在原处、透射台关'),
    'diagnostics.run': ('约10 min后写自检报告（completed）', '取消或断电：不写报告；抽屉若在移动则停在原处'),
    'service.pixel_mapping': ('约2 min后存入异常像元图并记录完成时间（completed）', '取消或断电：不改变上次结果'),
    'service.ship_prep': ('start：抽屉到达0.30 m（completed），之后等取下转台；next/cancel即时', '不能取消抽屉运动；断电时抽屉停在原处、模式结束')})
COMPLETION = {'band_excision.start': ['drawer.state', 'band_excision.on'], 'diagnostics.run': ['operation.kind', 'diagnostics.report'],
              'service.pixel_mapping': ['operation.kind', 'pixel_map.last_s', 'pixel_map.corrected_px'], 'service.ship_prep': ['drawer.state', 'service.ship_prep']}
RESOURCES.update({'routine.run': ['camera', 'illumination', 'host_clock'], 'band_excision.start': ['drawer', 'illumination'], 'diagnostics.run': ['drawer', 'camera', 'host_clock'],
                  'service.pixel_mapping': ['camera', 'host_clock'], 'service.ship_prep': ['drawer', 'touch_screen'], 'turntable.set_installed': ['stage'],
                  'sensor.configure_error': ['evaluator'], 'export.run': ['usb'], 'analysis.export': ['usb'], 'settings.backup': ['usb'], 'settings.restore': ['usb']})
CAP_SOURCES.update({k: [_UG] for k in APP_CAPS})
CAP_SOURCES['sensor.configure_error'] = [_SIM]
CAP_LIMITS.update({
    'routine.run': ['多重曝光的5个预设时间取当前曝光的1/4、1/2、1、2、4倍（手册未列预设值）；信号累积的第k张为前k帧之和（每帧读出噪声与偏置只计一次偏置），每帧之间有1.5 s滤光/读出开销。'],
    'image.view': ['显示调整只改变屏幕图像（panel.capture），不改图像数据与分析；自动对比按百分位（高2–98 %、中0.5–99.5 %、低最小–最大）为假设；Auto Enhance为显示用滚动最小背景扣除。'],
    'export.run': ['仿真配置只有U盘（侧面USB口）可写；文件名用图库名称（命名规则在拍摄时生成，可在Edit中改）；出版用格式可同时导出合成图与各通道。'],
    'diagnostics.run': ['自检项与时序（第5 min开关抽屉）为工程假设；各项结果由注入故障与CCD就绪决定。'],
    'service.pixel_mapping': ['约2 min暗场曝光（工程值）找出额外暗电荷超过正常像元5σ的像元（评测者sensor.configure_error设置的热像元；默认没有，图为空），存入像元图；'
                              '之后的拍摄把图中的像元校正为正常暗值，新出现的热像元要再做一次Pixel Mapping。'],
    'service.ship_prep': ['按用户指南“Prepare for shipping”：抽屉打开→取下转台→Next→关机（关机结束该模式）；Cancel退出为假设（指南未写）；'
                          '“取下转台后才能关机”按步骤提示实现（后部电源开关是机械开关，运行层不阻止关机）。'],
    'turntable.set_installed': ['转台连同透射玻璃取下或装回是操作者（机器人）动作，只在过程层；取下后不能放样品、不能手动旋转样品台，仪器自检的Turntable项为Fail；场景中的转台不消失。'],
    'sensor.configure_error': ['热像元位置由噪声种子决定（在3388×2712像元阵列上均匀分布），每个的额外暗电流按制冷设定点给出并随CCD温度同暗电流一样变化；默认0即理想CCD。'],
    'user.create': ['第一个创建的账户为管理员（手册未写首个管理员如何产生）；创建后即登录。'],
    'settings.reset': ['恢复出厂设置在下次开机时清除账户、设置、自定义染料与伪彩（图像保留并归入访客图库，手册未写图像去向）；重置用户提示按手册清除访客图库。'],
    'dye.define': ['自定义染料只用于通道选择与伪彩：激发/发射峰须落在通道的激发与发射带内（±15 nm）；成像信号仍来自样品标记（荧光染料库）。']})
LIMITATIONS.append('前面板（panel_ui.json）：触屏App的登录、模式与染料选择、实时视图、曝光、Smart Exposure、拍摄、更多选项、图库、导出、分析与设置页都调用本运行层命令；'
                   '菌落计数、E-Gel 96、Connect、网络搜索、软件更新与SAE在屏上只显示不可用的消息。')

# panel gestures change what the commands they call change (panel_ui.json); capture and frame are queries
_PANEL_CALLS = _panel_ui.spec_capabilities(PANEL_SPEC) if PANEL_SPEC else []
for _k in PANEL_CAPS:
    if _k not in ('panel.capture', 'panel.frame'):
        EFFECTS[_k] = list(dict.fromkeys(e for c in _PANEL_CALLS for e in EFFECTS.get(c, [])))
        ON_STEP[_k] = list(dict.fromkeys(e for c in _PANEL_CALLS for e in ON_STEP.get(c, [])))
        ON_STEP_ZH[_k] = '被调用的命令在step中的演化（同各命令）'
        EXEC_TEXT[_k] = ('手势瞬时完成（不推进仿真时钟）：界面状态与被调用命令的效果立即生效；结果只说明是否被仪器接受，屏幕须用panel.capture查看', '被拒时界面显示仪器消息，状态不变')
    CAP_SOURCES[_k] = [_UG]
    MODEL_NONE_ZH[_k] = '面板层按panel_ui.json解释手势并调用本运行层命令；屏幕纹理与3D联动属于后续Matterix步骤'
    CAP_ERRORS[_k] = []
    CAP_LIMITS[_k] = ['面板层（panel_ui.json，用户指南页码证据）；手势不推进仿真时钟；手册未写之处的选择见panel_ui.json的assumptions。']


def _sampling_text(k):
    if k in ('clock.time_s', 'camera.temperature_k', 'camera.ready', 'drawer.y_m', 'operation.progress'):
        return 'Analytic function of the host clock (drawer motion, CCD cooling); operations complete at their exact end time inside step().'
    return 'Changes when a command is accepted or an operation completes; no other time dynamics.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if (k in _CONTROL_GUARDED or k in _DATA_GUARDED or ALL_CAPS[k]['visibility'] != 'agent') else []
    return list(dict.fromkeys(CAP_ERRORS.get(k, []) + _COMMON_ERRORS + extra))


def _foreign_text(k):
    return '；另一个agent的操作进行中时，agent调用被拒（ACCESS_DENIED；未通电时开机除外）' if k in _CONTROL_GUARDED else ''


def export_thermo_ibrightfl1500_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=v['reference'], origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'ThermoIbrightFl1500Session.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in ALL_CAPS.items():
        un = k in UNSUPPORTED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.thermo_ibrightfl1500.ThermoIbrightFl1500Session.invoke',
                                              invocation=f'session.invoke(instance_id="imager_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=PRECONDITIONS[k][0] + _foreign_text(k) + '；被拒时状态不变。',
                                                                              state_ids=list(dict.fromkeys(list(PRECONDITIONS[k][1]) + (['operation.started_by_other_agent'] if k in _CONTROL_GUARDED else []))),
                                                                              enforced_by='runtime', check='ThermoIbrightFl1500Runtime.invoke / ThermoIbrightFl1500Session._guard')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量') for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置、分析或查询',
                                completion_state_ids=COMPLETION.get(k, ['drawer.state', 'operation.kind'] if k.startswith('drawer') else ['operation.kind', 'result.last_image_id']) if async_ else [],
                                timeout_s=None, cancellation=('unsupported' if k.startswith('drawer') or k in ('band_excision.start', 'service.ship_prep') else 'supported') if async_ else 'unsupported', retry='same_request_id',
                                resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=EXEC_TEXT[k][1] if k in EXEC_TEXT else ('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, e), side_effects='none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in dict.fromkeys(ON_STEP[k])
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    params = {}
    for name, (val, unit, meaning, src) in {
            'field_of_view_m': (list(FOV_M), 'm', '视场22.5×18.0 cm（变焦1×）', [_WB, _BR]), 'focal_length_m': (FOCAL_M, 'm', '镜头焦距25 mm', [_WB, _BR]), 'f_number': (F_NUMBER, '1', '镜头光圈f/0.95', [_WB, _BR]),
            'adc_max': (ADC_MAX, '1', '16位CCD（65 535灰阶）', [_WB, _BR]), 'pixel_count': (MEGAPIXELS * 1e6, '1', '像素数9.1×10⁶（9.1 MP）', [_WB, _BR]),
            'zoom': (dict(mechanical=list(MECH_ZOOM), digital=list(DIG_ZOOM)), '1', '1–8×（1–2×机械、1–4×数字）', [_BR, _UG]), 'binnings': (BINNINGS, '1', '1×1…8×8', [_BR]),
            'stage_rotation_rad': (STAGE_ROT_RAD, 'rad', '样品台自动旋转±10°', [_BR, _UG]), 'max_dyes': (MAX_DYES, '1', '最多同时4种荧光染料', [_WB, _BR]),
            'filters_m': ({k: [[x * 1e-9 for x in v[0]] if v[0] else None, [x * 1e-9 for x in v[1]]] for k, v in FILTERS_NM.items()}, 'm', '激发/发射波段（产品册表2、用户指南表4；数值为说明文字中的nm）', [_BR, _UG]),
            'rated_power_w': (RATED_POWER_W, 'W', '额定功率410 W', [_UG])}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)
    for name, (val, unit, meaning) in {
            'sensor_px': (list(SENSOR_PX), '1', '像元阵列3388×2712（9.19 MP）'), 'pixel_pitch_m': (PIXEL_PITCH_M, 'm', '像元尺寸'), 'full_well_e': (FULL_WELL_E, '1', '满阱电子数'),
            'read_noise_e': (READ_NOISE_E, '1', '读出噪声（电子）'), 'dark_e_s_cooled': (DARK_E_S_COOLED, '1/s', '制冷时暗电流（电子/像元/s）'),
            'dark_doubling_k': (DARK_DOUBLING_K, 'K', '暗电流倍增温差'), 'ccd_delta_k': (CCD_DELTA_K, 'K', 'CCD设定点低于室温'), 'ccd_tau_s': (CCD_TAU_S, 's', 'CCD制冷时间常数'),
            'transmission': (dict(lens=LENS_T, filter=FILTER_T, filter_od=FILTER_OD), '1', '镜头与滤光片透过率、带外光密度（无量纲）'),
            'qe_wavelengths_m': ([a for a, _ in QE_TABLE], 'm', 'CCD量子效率曲线的波长点'), 'qe_values': ([b for _, b in QE_TABLE], '1', 'CCD量子效率（与波长点对应）'), 'led_irradiance_w_m2': (dict(LED_IRRADIANCE), 'W/m2', '各LED在滤光带内的样品面辐照度'),
            'drawer_s': (DRAWER_S, 's', '抽屉全行程时间'), 'capture_overhead_s': (CAPTURE_OVERHEAD_S, 's', '每通道滤光轮/LED切换与读出'),
            'smart_target': (SMART_TARGET, '1', 'Smart Exposure目标（最亮像素/满量程）'), 'hrp_photons_s': (dict(HRP_PHOTONS_S), '1/s', '每个HRP在各底物下的光子产率'),
            'substrate_tau_s': (dict(SUBSTRATE_TAU_S), 's', '底物光输出衰减时间常数'), 'stain_a_m2_kg': (dict(STAIN_A_M2_KG), 'm2/kg', '可见染色剂的吸光系数'),
            'background_mol_m2': ({k: dict(v) for k, v in BACKGROUND.items()}, 'mol/m2', '膜/胶自发荧光（参考染料等效面密度）'),
            'stray_fraction': (STRAY_FRACTION, '1', '散射激发光漏过发射滤光片的比例'), 'bias_adu': (BIAS_ADU, '1', '相机偏置'),
            'electrical_w': (dict(idle=POWER_IDLE_W, cooling=POWER_COOL_W, led=POWER_LED_W), 'W', '电功率：待机、CCD制冷满载、曝光时LED'),
            'analysis_max_px': (ANALYSIS_MAX_PX, '1', '存储/分析图像的长边像素上限')}.items():
        params[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=[_SIM], uncertainty='工程值（厂家未公开）')
    mechanism = dict(
        component_ids=['base_plate', 'body_shell', 'touch_screen', 'display_exposure_digits', 'status_light_bar', 'sample_drawer', 'sample_stage', 'stage_glass', 'side_ports',
                       'rear_panel', 'rear_connectors', 'switch_power'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='CCD成像：荧光（FPbase光谱×滤光×QE×集光）、化学发光（HRP×底物衰减）、可见（白光反射×10^−A）；泊松/读出/暗电流噪声与16位饱和；Smart Exposure、HDR；按用户指南定义的条带分析与定量',
        equations=['fluorescence: e/s per molecule = integral(sigma(l) phi_ex(l) T_ex(l)) QY LENS_T integral(em(l) T_em(l) QE(l)) / integral(em) x NA^2 / 4; sigma = eps ln10 / N_A',
                   'NA = (f / N / 2) / u, u = f (1 + 1 / M), M = sensor width / (FOV / mechanical zoom)',
                   'chemiluminescence: e = N_HRP k_s tau (exp(-(t1 - t0) / tau) - exp(-(t2 - t0) / tau)) x chemi band QE x collection',
                   'visible: e/s = Phi_white R 10^(-A) x collection x QE; A = sum a_stain m / area',
                   'camera: ADU = (Poisson(e) + N(0, sigma_read)) / g + offset, clipped at 65 535; dark = b^2 integral d0 2^((T_ccd(t) - 265.15 K) / 6.3 K) dt over the exposure',
                   'CCD: T(t) = T_set + (T0 - T_set) exp(-t / 90 s), T_set = T_room - 30 K; ready when T <= T_set + 2 K',
                   'Smart Exposure: t_rec = 0.8 (65 535 - offset) / (max ADU rate in the ROI)'],
        parameters=params, source_ids=[_SIM, _WB, _BR, _UG, _FP], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_ibright_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('thermo_ibrightfl1500', caps, slots, {'ibright_imaging': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if v['reference']:
            slots[k]['reference_conditions'] = v['reference']
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'ibright_imaging': mechanism})

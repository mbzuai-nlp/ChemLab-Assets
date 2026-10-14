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
                   band_purity_pct=number('1'), lane_purity_pct=number('1'), molar_mass_kg_mol=nullable(number('kg/mol')), saturated=BOOL))
LANEROW = obj(dict(lane=INT, x_m=number('m'), width_m=number('m'), total_lane_volume=number('1'), background_volume=number('1'), adjusted_total_lane_volume=number('1'),
                   normalization_factor=nullable(number('1')), bands=array(BANDROW, 0, 60)))
ANALYSIS = obj(dict(image_id=TEXT, channel=TEXT, sensitivity=number('1'), inverted=BOOL, hdr_scale=nullable(number('1')), frame_m=array(number('m'), 4, 4), lanes=array(LANEROW, 0, 30),
                    marker_lane=nullable(INT), warnings=array(TEXT, 0, 12)))
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
cap('image.analyze', obj(dict(image_id=TEXT, channel=enum(CHANNELS), sensitivity=number('1', 0.0, 1.0), marker_lane=nullable(integer(1, 30)),
                               marker_molar_mass_kg_mol=nullable(array(number('kg/mol', 0.5, 20000.0), 1, 20)), lane_centres_m=LANE_CENTRES),
                          optional=('marker_lane', 'marker_molar_mass_kg_mol', 'lane_centres_m')),
    ANALYSIS, dict(image_id='IMG0001', channel='EX3', sensitivity=0.5, marker_lane=None, marker_molar_mass_kg_mol=None), ['touch_screen'],
    '分析图像：自动找泳道与条带（或按给定泳道中心，即“调整泳道”），输出体积、密度、局部背景校正体积/密度、迁移率Rf、条带/泳道纯度；给定分子量标准泳道时估算分子量'
    '（DNA梯度按片段摩尔质量=bp×0.650 kg/mol给出）', 'agent', 'analysis')
cap('image.quantify', obj(dict(image_id=TEXT, channel=enum(CHANNELS), standards=array(obj(dict(lane=integer(1, 30), band=integer(1, 60), amount_kg=number('kg', 0.0, 1e-5))), 2, 20),
                                sensitivity=number('1', 0.0, 1.0), lane_centres_m=LANE_CENTRES), optional=('lane_centres_m',)), QUANT,
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
    'band_excision.start': '切胶模式（开抽屉并开绿光透射台）未建模',
    'e_gel_96.image': 'E-Gel 96应用未建模',
    'cloud.connect': 'Thermo Fisher Connect云与Wi-Fi连接未建模（Wi-Fi适配器为选购件）',
    'diagnostics.run': '仪器自检（约10 min）未建模',
    'hardware.connect': '没有真实iBright与分析软件的硬件接口',
}
for _k, _why in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, ['base_plate'], _why, 'agent', 'device_command')

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
        }
        return {k: v for k, v in values.items() if SLOTS[k]['visibility'] == 'agent' or visibility == 'evaluator'}

    def _progress(self):
        if not self.op:
            return 0.0
        o = self.op
        return float(min(1.0, max(0.0, (self.t - o['start']) / max(o['end'] - o['start'], 1e-9))))

    # ---------------------------------------------------------------- commands
    def invoke(self, capability_id, **a):
        if capability_id not in CAPS:
            raise ActionRejected('UNKNOWN_CAPABILITY')
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        validate(CAPS[capability_id]['input_schema'], a)
        props = CAPS[capability_id]['input_schema'].get('properties', {})
        a = {k: (int(v) if props.get(k, {}).get('type') == 'integer' and v is not None else v) for k, v in a.items()}
        out = getattr(self, '_cmd_' + capability_id.replace('.', '_'))(**a)
        if CAPS[capability_id]['kind'] not in ('query', 'analysis', 'simulation'):
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

    def _power_off(self):
        if self.op is not None:
            self._fail_op('power_lost', '断电：操作中止')
        d = self.drawer
        if d['state'] in ('opening', 'closing'):                                  # the drawer motor stops where it is
            d.update(y=self._drawer_y(), state='stopped')
        self._anchor(False)
        self.power = False
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
                if dye not in fluor_lib()['substances']:
                    raise ActionRejected('UNKNOWN_DYE')
                if ch in ('VISIBLE', 'CHEMI'):
                    raise ActionRejected('DYE_CHANNEL_MISMATCH')
                best = max(dye_rate(dye, k) for k in FILTERS_NM if k not in ('VISIBLE', 'CHEMI'))
                if best <= 0 or dye_rate(dye, ch) < 0.05 * best:                  # the dye is not detected in this filter set
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
        e_mean = sig_e + self._dark_electrons(t_start, t_start + t_exp)
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
        del self.images[image_id]
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

    def _cmd_image_analyze(self, image_id, channel, sensitivity, marker_lane=None, marker_molar_mass_kg_mol=None, lane_centres_m=None):
        return analyze_image(self._image(image_id), self._channel(self._image(image_id), channel), sensitivity, marker_lane, marker_molar_mass_kg_mol, lane_centres_m)

    def _cmd_image_quantify(self, image_id, channel, standards, sensitivity, lane_centres_m=None):
        res = self._cmd_image_analyze(image_id, channel, sensitivity, lane_centres_m=lane_centres_m)
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
        npx = sum(c['img'].size for c in img['channels']) * img['m'] ** 2
        cam = img['width_px'] * img['height_px']
        if analysis_16bit or fmt == 'g2i':                                    # unadjusted 16-bit image per channel for analysis
            nbytes = int({'g2i': 2 * npx + 65536, 'tiff': 2 * npx + 8192, 'png': int(1.2 * npx) + 4096}[fmt])
        else:                                                                 # publication export: 24-bit colour-adjusted composite (user guide)
            nbytes = int({'tiff': 3 * cam + 8192, 'png': int(1.8 * cam) + 4096, 'jpg': int(0.3 * cam) + 4096}[fmt])
        name = f'{image_id}.{fmt}' if not analysis_16bit else f'{image_id}_analysis16.{fmt}'
        img['exported'].append(name)
        self._event('analysis', 'EXPORTED', f'{name}导出到U盘（{nbytes / 1e6:.1f} MB）', rid=image_id)
        return dict(image_id=image_id, file=name, bytes=nbytes)

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


def analyze_image(img, chan, sensitivity, marker_lane=None, marker_mass=None, lane_centres_m=None):
    """Lanes and bands found from the 16-bit image (sensitivity 0-1 lowers the thresholds): Volume (sum of pixel intensities above the camera
    offset, in camera pixels), Density, Local Background Corrected Volume / Density (the surround 2 camera pixels wide), Rf (distance / gel length
    of the sample frame), Band Purity (percent of the lane's corrected band volumes), Lane Purity, lane total / rolling-ball background / adjusted
    volume and the normalization factor (lane 1 = 1), molar mass from a marker lane (log M interpolated in Rf).  Frame: the membrane / gel detected
    in the image, else in the image's visible channel, else the outline from the live view.  Lanes: column runs split at valleys, gaps at the
    regular lane pitch kept as empty lanes, or the lane centres given by the user (Adjust lanes).  Saturation from the readout mask (white clip
    in the inverted visible channel).  The visible channel is inverted (stained bands absorb)."""
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
    frame = frame_of(raw)
    if frame is None:
        vis = next((c for c in img['channels'] if c['channel'] == 'VISIBLE'), None)
        frame = frame_of(vis['img'].astype(float) - BIAS_ADU) if vis is not None else None
    rf_ref = None                                                             # (top row, length in rows) of the gel for Rf; defaults to the analysis frame
    if frame is None and img.get('live_frame_m'):
        x0, y0, x1, y1 = img['live_frame_m']
        c0, c1 = int(max(0, math.floor(x0 / p + nx / 2))), int(min(nx - 1, math.ceil(x1 / p + nx / 2) - 1))
        r0, r1 = int(max(0, math.floor(y0 / p + ny / 2))), int(min(ny - 1, math.ceil(y1 / p + ny / 2) - 1))
        frame = [r0, r1, c0, c1]
        rf_ref = (y0 / p + ny / 2, (y1 - y0) / p)                             # the whole sample, also when the zoomed view shows only part of it
        warns.append('frame_from_live_view')
    if frame is None:
        frame = [0, ny - 1, 0, nx - 1]
        warns.append('frame_is_whole_image')
    r0, r1, c0, c1 = frame
    if rf_ref is None:
        rf_ref = (float(r0), float(r1 - r0 + 1))
    inverted = chan['channel'] == 'VISIBLE'
    sub = raw[r0:r1 + 1, c0:c1 + 1]
    smk = sat_mask[r0:r1 + 1, c0:c1 + 1]
    work = (np.percentile(sub, 99) - sub).clip(min=0.0) if inverted else sub
    if inverted:
        warns.append('visible_channel_inverted')
    if smk.mean() > 0.2:
        warns.append('image_largely_saturated')
    k = 1.0 - float(sensitivity)
    H, Wd = work.shape
    sm = np.apply_along_axis(lambda v: np.convolve(v, np.ones(5) / 5, mode='same'), 0, work) if H >= 5 else work
    cp = sm.max(axis=0) - np.median(sm)                                      # any band in the column (a lane may hold a single narrow band)
    cp = np.convolve(cp, np.ones(3) / 3, mode='same')
    sd_pix = max(_robust_sd(work.ravel()[:: max(1, work.size // 50000)]), 0.5)   # pixel noise from differences (robust to lanes covering most columns)
    thr = max(float(np.percentile(cp, 10)) + (6 + 10 * k) * sd_pix / math.sqrt(5.0), 1e-9)          # from the pixel noise, not from the brightest lane
    min_w = max(2, int(round(1e-3 / p)))
    runs = []
    for a_, b_ in _runs(cp > thr, min_w, max(0, int(round(0.4e-3 / p)))):
        runs.extend(_split_at_valleys(cp, a_, b_, max(2, int(round(1.5e-3 / p)))))
    runs = [r_ for r_ in runs if r_[1] - r_[0] + 1 >= min_w][:30]
    bgc = float(np.percentile(cp, 10))
    kw = max(3, 2 * int(round(0.2e-3 / p)) + 1)                                 # 0.4 mm smoothing of the column profile
    cpw = np.convolve(cp, np.ones(kw) / kw, mode='same')
    ext = []
    for j, (a_, b_) in enumerate(runs):                                        # lane edges: half-maximum crossings (sub-pixel) + 0.6 mm on each side
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
    if lane_centres_m:                                                        # Adjust lanes: the user's lane centres (m from the frame's left edge)
        xs = sorted(float(x) for x in lane_centres_m)
        if xs[0] < 0.0 or xs[-1] > Wd * p:
            raise ActionRejected('LANE_OUTSIDE_FRAME')
        if len(xs) > 1 and float(np.min(np.diff(xs))) < 1e-3:
            raise ActionRejected('LANES_OVERLAP')
        if xs != [float(x) for x in lane_centres_m]:
            warns.append('lane_centres_sorted')
        cen = [x / p - 0.5 for x in xs]
        widths = widths or [max(2, int(round(5e-3 / p)))]
        warns.append('lanes_from_user')
    elif len(cen) >= 3:                                                       # keep empty lanes at the regular pitch
        d = np.diff(cen)
        pitch = float(d.min())                                                # every gap must be a whole number of pitches (else no inference)
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
    if not lane_centres_m and len(cen) >= 3:                                  # a comb is centred on the gel: unequal margins hint at empty edge lanes
        pitch = float(np.min(np.diff(cen)))                                   # (reported, not inserted: lanes may be loaded on one side only;
        if abs(cen[0] - ((Wd - 1) - cen[-1])) > 0.75 * pitch:                 #  image.analyze lane_centres_m sets the lanes explicitly)
            warns.append('possible_empty_edge_lane')
    elif not lane_centres_m and len(cen) == 2 and widths and cen[1] - cen[0] > 2.5 * float(np.median(widths)):
        warns.append('possible_empty_lanes')                                  # the lane pitch cannot be told from two lanes
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
    for c_ in cen:                                                            # clipped to the frame, at least one column wide, never overlapping the previous lane
        a0 = int(round(c_ - W / 2))
        lo_c = min(max(0, a0), Wd - 1)
        if lanes_px:
            lo_c = max(lo_c, lanes_px[-1][1] + 1)
            if lo_c > Wd - 1:
                raise ActionRejected('LANES_OVERLAP')
        lanes_px.append((lo_c, max(min(Wd - 1, a0 + W - 1), lo_c)))
    ring = max(1, int(math.ceil(LBC_RING_M / p - 1e-9)))                        # the surround: 2 camera pixels at zoom 1 (fixed in object space)
    lanes = []
    sat_any = False
    for li, (a, b) in enumerate(lanes_px, 1):
        w = b - a + 1
        core = slice(a + int(0.15 * w), b - int(0.15 * w) + 1)
        rp = work[:, core].sum(axis=1)
        base = _rolling_min(rp, max(2, int(round(ROLL_M / p))))
        r = np.convolve(rp - base, [0.25, 0.5, 0.25], mode='same')
        sd_r = max(_robust_sd(rp), 1e-9)
        tb = (5 + 10 * k) * sd_r
        peaks = [i for i in range(1, H - 1) if r[i] >= r[i - 1] and r[i] > r[i + 1] and r[i] > tb] if (li - 1) not in empty or lane_centres_m else []
        groups = []                                                            # neighbouring peaks without a valley below half the smaller peak = one band
        for i in peaks:
            if groups:
                j = groups[-1][-1]
                v = j + int(np.argmin(r[j:i + 1]))
                if r[v] >= 0.5 * min(r[i], r[j]):
                    groups[-1].append(i)
                    continue
            groups.append([i])
        cuts = [g0[-1] + int(np.argmin(r[g0[-1]:g1[0] + 1])) for g0, g1 in zip(groups, groups[1:])]   # the valley between two resolved bands
        spans = []
        for n, g in enumerate(groups):
            i = max(g, key=lambda q: r[q])
            lb = cuts[n - 1] if n > 0 else 0                                   # the valley row is shared by both bands (half each)
            ub = cuts[n] if n < len(cuts) else H - 1
            half = 0.5 * r[i]                                                  # half-maximum width -> sigma (within the valleys)
            lo_, hi_ = i, i
            while lo_ > lb and r[lo_ - 1] > half:
                lo_ -= 1
            while hi_ < ub and r[hi_ + 1] > half:
                hi_ += 1
            sig = max(1.0, (hi_ - lo_ + 1) / 2.355)
            lo_ = max(lb, g[0] - int(math.ceil(4 * sig)))                       # the band spans +-4 sigma of its half-maximum width, never past a valley
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
            if lbc <= 0.0:                                                      # a noise peak not above its local background is not a band
                continue
            sat = bool(smk[ya:yb + 1, xa:xb + 1].any())                         # a clipped pixel in the band or its surround (white clip in visible)
            sat_any |= sat
            yrow = r0 + i + 0.5 - rf_ref[0]                                     # rows below the gel top (wells)
            bands.append(dict(band=len(bands) + 1, y_m=yrow * p, rf=yrow / rf_ref[1], volume=vol, density=vol / area, lbc_volume=lbc, lbc_density=lbc / area, area_px=int(round(area)),
                              band_purity_pct=0.0, lane_purity_pct=0.0, molar_mass_kg_mol=None, saturated=sat))
        tot_lbc = sum(max(0.0, x['lbc_volume']) for x in bands)
        adj = total - bgv
        for x in bands:
            x['band_purity_pct'] = 100.0 * max(0.0, x['lbc_volume']) / tot_lbc if tot_lbc > 0 else 0.0
            x['lane_purity_pct'] = 100.0 * max(0.0, x['lbc_volume']) / adj if adj > 0 else 0.0
        lanes.append(dict(lane=li, x_m=(a + b + 1) / 2 * p, width_m=w * p, total_lane_volume=total, background_volume=bgv, adjusted_total_lane_volume=adj,
                          normalization_factor=None, bands=bands))
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
                    if l['lane'] == marker_lane:
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
    x0, y0 = -nx / 2 * p + c0 * p, -ny / 2 * p + r0 * p
    return dict(image_id=img['image_id'], channel=chan['channel'], sensitivity=float(sensitivity), inverted=inverted, hdr_scale=img.get('hdr_scale'), frame_m=[x0, y0, x0 + Wd * p, y0 + H * p],
                lanes=lanes, marker_lane=marker_lane, warnings=list(dict.fromkeys(warns))[:12])

ASYNC = {'drawer.open', 'drawer.close', 'smart_exposure.run', 'capture.run'}
_DATA_GUARDED = {'measurement.result', 'image.analyze', 'image.quantify', 'image.export', 'image.delete'}
_CONTROL_GUARDED = {k for k, v in CAPS.items() if v['visibility'] == 'agent' and v['kind'] in ('device_command', 'robot_skill') and k not in UNSUPPORTED}


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
        return dict(protocol_version=PROTOCOL_VERSION, asset_type_id=ASSET_TYPE_ID, asset_version='1.0.0', capabilities=caps, state_slots=slots)

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
    'sample.place': ('抽屉已打开、台上无样品、样品在实验台上、含偏角不超出透射玻璃', ['drawer.state', 'sample.on_stage', 'sample.bench_ids']),
    'sample.remove': ('抽屉已打开、该样品在台上', ['drawer.state', 'sample.on_stage']),
    'channels.set': ('通电、空闲、已选模式；通道属于该模式，荧光通道须指定在该滤光组可检测的染料，最多4种染料', ['power.enabled', 'operation.kind', 'mode']),
    'exposure.set': ('通电、空闲、该通道已选、相机设置未锁定', ['power.enabled', 'channels.selected', 'camera.locked']),
    'camera.set': ('通电、空闲；合并为1/2/3/4/5/6/8；锁定时不改变焦；手动旋转样品台需抽屉关闭', ['power.enabled', 'operation.kind', 'camera.locked', 'drawer.state']),
    'smart_exposure.run': ('通电、空闲、已选模式与通道、抽屉关闭、CCD已就绪', ['power.enabled', 'operation.kind', 'mode', 'channels.selected', 'drawer.state', 'camera.ready']),
    'capture.run': ('同smart_exposure.run；HDR只在化学发光印迹模式；图库未满', ['power.enabled', 'operation.kind', 'mode', 'channels.selected', 'drawer.state', 'camera.ready']),
    'image.export': ('通电；图像存在；JPG不作16位分析图', ['power.enabled', 'result.last_image_id']),
    'image.delete': ('通电、空闲；图像存在且由调用者拍摄（agent不能删除评测者或其他agent的图像）', ['power.enabled', 'operation.kind', 'result.last_image_id']),
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
_IDLE = ['PLANT_REQUIRED', 'POWER_OFF', 'BUSY']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED'], 'power.set': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'], 'power.press_switch': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'],
    'mode.select': _IDLE, 'drawer.open': _IDLE + ['DRAWER_OPEN', 'RECORDS_FULL'], 'drawer.close': _IDLE + ['DRAWER_CLOSED', 'RECORDS_FULL'],
    'sample.provide': ['PLANT_REQUIRED', 'ITEM_EXISTS', 'TOO_MANY_ITEMS', 'LANE_OUTSIDE_SAMPLE', 'BAND_OUTSIDE_SAMPLE', 'UNKNOWN_LABEL', 'AMOUNT_REQUIRED', 'AMOUNT_UNIT_MISMATCH'],
    'sample.place': ['PLANT_REQUIRED', 'DRAWER_NOT_OPEN', 'UNKNOWN_ITEM', 'SAMPLE_NOT_ON_BENCH', 'STAGE_OCCUPIED', 'SAMPLE_TOO_LARGE'],
    'sample.remove': ['PLANT_REQUIRED', 'DRAWER_NOT_OPEN', 'SAMPLE_NOT_ON_STAGE'],
    'channels.set': _IDLE + ['MODE_REQUIRED', 'DUPLICATE_CHANNEL', 'CHANNEL_NOT_IN_MODE', 'UNKNOWN_DYE', 'DYE_CHANNEL_MISMATCH', 'DYE_REQUIRED', 'TOO_MANY_DYES'],
    'exposure.set': _IDLE + ['CHANNEL_NOT_SELECTED', 'SETTINGS_LOCKED'], 'camera.set': _IDLE + ['UNSUPPORTED_BINNING', 'SETTINGS_LOCKED', 'DRAWER_NOT_CLOSED'],
    'smart_exposure.run': _IDLE + ['MODE_REQUIRED', 'NO_CHANNELS', 'DRAWER_NOT_CLOSED', 'CAMERA_COOLING', 'INVALID_ROI', 'RECORDS_FULL'],
    'capture.run': _IDLE + ['MODE_REQUIRED', 'NO_CHANNELS', 'DRAWER_NOT_CLOSED', 'CAMERA_COOLING', 'HDR_CHEMI_ONLY', 'GALLERY_FULL', 'RECORDS_FULL'],
    'image.analyze': ['UNKNOWN_IMAGE', 'CHANNEL_NOT_IN_IMAGE', 'LANE_OUTSIDE_FRAME', 'LANES_OVERLAP', 'ANALYSIS_FAILED'],
    'image.quantify': ['UNKNOWN_IMAGE', 'CHANNEL_NOT_IN_IMAGE', 'LANE_OUTSIDE_FRAME', 'LANES_OVERLAP', 'ANALYSIS_FAILED', 'NO_SUCH_BAND', 'DUPLICATE_STANDARD', 'STANDARDS_NOT_DISTINCT'],
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
                      '只识别一个分析框（厂家1–4个）；可见通道自动反相。算法为工程实现，不是厂家软件的复制。'],
    'image.quantify': ['标准曲线为背景校正体积对质量的最小二乘直线；线性范围取标准的最小–最大量；饱和条带标记saturated。'],
    'sample.provide': ['条带与泳道由上游电泳/转印给出；荧光染料的光谱、消光系数与量子产率来自FPbase（无数据时取ε 5×10⁴ M⁻¹cm⁻¹、QY 0.3的假设值），'
                       '核酸染料按结合态量子产率（游离态为其1/20），SYPRO蛋白染料与核酸染料的每千克分子数为工程值；可见染色剂的吸光系数为工程值；HRP按底物的光子产率（工程值）。'],
    'drawer.close': ['关抽屉时（未锁定）按样品偏角旋转样品台（±10°内）并把光学变焦设为使样品外接框占视场的1/1.15（1–2×）；对焦视为自动完成。'],
}
LIMITATIONS = [
    '选定配置：iBright FL1500（荧光+化学发光+可见）；菌落计数、E-Gel 96、切胶模式、云与Wi-Fi、仪器自检与多重曝光/信号累积程序不建模。',
    '样品在场景中没有几何；放取样品是宿主/机器人抽象，没有接触验证；触摸屏为贴图，屏上曝光数字为仿真UI提示。',
    '相机与照明的工程参数未公开（见capture.run限制）；图像为过程数据，分析按用户指南的输出定义实现。',
    '没有真实iBright与分析软件的硬件接口。',
]


def _sampling_text(k):
    if k in ('clock.time_s', 'camera.temperature_k', 'camera.ready', 'drawer.y_m', 'operation.progress'):
        return 'Analytic function of the host clock (drawer motion, CCD cooling); operations complete at their exact end time inside step().'
    return 'Changes when a command is accepted or an operation completes; no other time dynamics.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if (k in _CONTROL_GUARDED or k in _DATA_GUARDED or CAPS[k]['visibility'] != 'agent') else []
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
    for k, v in CAPS.items():
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
                                completion_state_ids=(['drawer.state', 'operation.kind'] if k.startswith('drawer') else ['operation.kind', 'result.last_image_id']) if async_ else [],
                                timeout_s=None, cancellation=('unsupported' if k.startswith('drawer') else 'supported') if async_ else 'unsupported', retry='same_request_id',
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

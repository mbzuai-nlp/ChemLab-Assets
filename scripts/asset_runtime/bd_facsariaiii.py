"""CHEM-168 BD FACSAria III cell sorter (2018 specification; 4 lasers 405 / 488 / 561 / 633 nm, 10 fluorescence detectors, ACDU, AMO and
sort-collection recirculator options): process runtime.

Device (Technical Specifications 23-11585-02 (2018); User's Guide 333608 (2003); facility guides): fluidics cart (10-L sheath and waste, 5-L
cleaning reservoirs, 80-100 psi air) feeding a pressurised plenum; Fluidics Startup / Shutdown (shutdown ~3 min with the closed-loop nozzle) and
cleaning modes; Sort Setup presets (70 um at 70 / 35 psi, 85, 100, 130 um) setting sheath pressure 5-75 psi, drop drive 1-100,000 Hz, amplitude
1-80 V, plate voltage 0-6000 V, side-stream charges, laser delays and area scaling; stream button; breakoff camera (Drop 1 / Gap, Drop 1 jumps by
the drop spacing ~57 px), Sweet Spot (amplitude servo, pauses sorting when the Gap is >2 px off, on a clog turns the stream and plates off, closes
the aspirator drawer and unloads the tube); side streams (far left, left, right, far right), test sort, BD Accudrop (diode laser, lower camera,
optical filter; automatic drop delay 10-140 drops); masks in 1/32-drop (yield, purity, phase; Purity, 4-Way Purity, Yield, Single Cell, Initial,
Fine Tune modes); sample injection chamber with Load / Unload, agitation and 4 / 20 / 37 / 42 C control (~45 min), chamber light; flow rate 1-11
(~10-120 ul/min); lasers need 30 min warm-up, the flow cell access door blocks them; FSC photodiode and SSC / fluorescence PMTs, 262,144 channels,
H / A / W, thresholds on any parameter (OR / AND), max 70,000 events/s with 12 compensation pairs and 8 parameters; Emergency stop button (lit while
a tube loads); AMO (16 / 69 chamber volumes per minute); sort collection devices 2-way 12 x 75 / 15 mL, 4-way 12 x 75 / 1.5 mL, ACDU plates.

Process model (fixed 0.1 s grid, numpy per particle): jet speed Cd sqrt(2 dP / rho), Rayleigh capillary growth of the drop-drive perturbation
(breakoff length, Drop 1 / Gap on a strobe-locked camera, drop delay = plenum-to-nozzle transit + breakoff drops), surface tension and density of
water (IAPWS 2014, Tanaka 2001) with sheath warm-up; Poisson particle arrivals from tube populations (particle library: Mie FSC / SSC tables;
fluorochrome library: FPbase spectra, photon budget calibrated to the documented 85 MESF-FITC / 29 MESF-PE), hydrodynamic core position in the
65-um beam, laser warm-up and noise, photoelectron statistics, PMT gain (V/500)^7, electronic noise, laser-delay capture, area scaling, pulse
pile-up within the window extension, the processing limit and electronic aborts; compensation, gates and statistics; drop assignment with the set
vs. true drop delay, BD mask logic, conflicts, side-stream landing and capture, sub-threshold particles riding in sorted drops; sorted fractions
become new samples (post-sort purity check)."""
import copy
import json
import math
import zlib
from pathlib import Path

import numpy as np

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.bd_facsariaiii"
CATALOG_ID = "CHEM-168"
ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / 'assets/expansion_batch04/t4_measure/bd_facsariaiii'
LIBS = {'fluorochromes': 'fluorochrome_library.json', 'particles': 'particle_library.json', 'fluidics': 'fluidics_model.json', 'optics': 'optics_model.json'}


class ActionRejected(ValueError):
    """A capability call refused with a declared error code (no state change)."""


# ---------------------------------------------------------------- libraries
_LIBC = {}


def _lib(name):
    if name not in _LIBC:
        _LIBC[name] = json.loads((ASSET_DIR / LIBS[name]).read_text())
    return _LIBC[name]


def fluorochromes():
    return _lib('fluorochromes')['substances']


def particles():
    return _lib('particles')['substances']


def fluidics():
    return _lib('fluidics')


def optics():
    return _lib('optics')


# ---------------------------------------------------------------- constants (SI)
DT = 0.1                                   # physics grid (s)
PSI = 6894.757
ADC_MAX = 262143.0                         # 262,144 channels (documented)
LASERS = ['violet', 'blue', 'yellow_green', 'red']
FL_DETS = ['V450_50', 'V510_50', 'B530_30', 'B695_40', 'YG582_15', 'YG610_20', 'YG710_50', 'YG780_60', 'R660_20', 'R780_60']
DETS = ['FSC', 'SSC'] + FL_DETS
DET_LASER = {'FSC': 'blue', 'SSC': 'blue', 'V450_50': 'violet', 'V510_50': 'violet', 'B530_30': 'blue', 'B695_40': 'blue', 'YG582_15': 'yellow_green',
             'YG610_20': 'yellow_green', 'YG710_50': 'yellow_green', 'YG780_60': 'yellow_green', 'R660_20': 'red', 'R780_60': 'red'}
PARAMS = [f'{d}-{x}' for d in DETS for x in ('A', 'H', 'W')] + ['Time']
PIDX = {p: i for i, p in enumerate(PARAMS)}
NPAR = len(PARAMS)
CHANNEL_AREA = 160e-6 * 250e-6             # cuvette flow channel (assumption, library)
CORE_SPEED_RATIO = 2.0                     # centre-line / mean speed in the rectangular channel (engineering)
BEAM_H, BEAM_W = 9e-6, 65e-6               # documented beam height (along the flow) and width (1/e2 full widths)
PULSE_K = 0.8                              # particle-size share of the pulse width (engineering)
K_CH = 1.8                                 # ADC channels per photoelectron at 500 V (engineering; FITC dim-bead noise ~ electronic noise at ~550 V)
PMT_EXP, PMT_REF_V = 7.0, 500.0            # library: gain ~ (V / 500 V)^7 (assumption)
FSC_EXP = 3.0                              # FSC photodiode amplifier gain ~ (V / 500 V)^3 (assumption)
FSC_NOISE_CH = 10.0                        # FSC stray-light / photodiode noise (assumption)
WIDTH_SCALE = 65536.0                      # W = A / H x 65,536 (assumption: FACSDiva convention)
LASER_NOISE = 0.006                        # rms laser intensity noise of a warm laser (assumption)
WARMUP_S = 1800.0                          # documented: wait 30 minutes for the lasers to warm up
WARMUP_TAU_S = 480.0                       # warm-up time constant of the power / noise excess (assumption)
LASER_POWER_W = {'violet': 60.0, 'blue': 60.0, 'yellow_green': 100.0, 'red': 80.0}   # wall-plug power of the laser modules (assumption)
BASE_POWER_W = 700.0                       # electronics, pumps, cameras (assumption; documented maximum 1,500 W)
SAMPLE_TC_POWER_W = 100.0
RATE_REF = 70000.0                         # documented: max 70,000 events/s with 12 compensation pairs and 8 parameters
LOAD_TRAVEL = 0.065                        # loading port rise (interface definition)
PORT_SPEED = LOAD_TRAVEL / 3.0             # port moves in ~3 s (engineering)
DRAWER_TRAVEL = 0.080
DRAWER_SPEED = DRAWER_TRAVEL / 1.0
ESTOP_TRAVEL = 0.006
GAUGE_FULL_SCALE_PSI = 160.0
GAUGE_SWEEP = 270.0 * math.pi / 180
CART_AIR_PA = 88.0 * PSI                   # documented ~88 psi typical (80-100 psi while running)
CART_AIR_TAU_S = (20.0, 60.0)              # rise / decay time constants (engineering)
SHEATH_TAU_S = 2.0                         # plenum pressure response (engineering)
STARTUP_S, SHUTDOWN_S = 480.0, 180.0       # Fluidics Startup (assumption ~8 min) / Shutdown (documented ~3 min)
CLEAN_S = {'clean_flow_cell': 360.0, 'prepare_aseptic_sort': 2100.0}    # documented 5-10 min / 10-40 min ranges; values assumptions
PRIME_SHEATH_M3 = 4.0e-4                   # sheath used to prime the plenum and lines (assumption)
SAMPLE_CHAMBER_TAU_S = 900.0               # ~45 min to reach the setpoint (documented) = 3 tau
TUBE_TAU_S = 300.0                         # sample tube follows the chamber (engineering)
COLLECTION_TAU_S = 600.0                   # recirculator cooling of the collection tubes (assumption)
SHEATH_WARM_K, SHEATH_WARM_TAU_S = 1.5, 1200.0   # stream / pump heating of the sheath at the nozzle (assumption; "wait 30 min for the stream to stabilise")
TRANSDUCER_TC = -0.02                      # 1/K drift of the drop-drive coupling with sheath temperature (assumption)
ATTENUATION = 0.25                         # drop-drive attenuation factor (documented control; value assumption)
CAMERA_PX_M = None                         # set in _cal(): drop spacing of the 70 um High setup = 57 px (documented)
REF_SHEATH_K = 295.15
NB_REF_70 = 20.0                           # drops between the nozzle and the breakoff at the 70 um High defaults (assumption)
INSTALL_SD = (0.15, 0.03)                  # instrument-to-instrument spread of the drive coupling and the laser-to-nozzle volume (assumption: the drop
                                           # delay of a given instrument differs from the factory default by ~+-0.7 drops and must be set with Accudrop)
DROP1_DEFAULT, GAP_DEFAULT = 330.0, 6.0    # documented defaults
DROP1_RANGE = (100.0, 600.0)
GAP_TOL_PAUSE, DROP1_TOL = 2.0, 10.0       # documented: >2 px Gap pauses sorting; up to 10 px Drop 1 difference acceptable
SS_COARSE_V_S, SS_FINE_V_S = 2.0, 0.2      # Sweet Spot amplitude slew (engineering)
DD_JITTER = 0.02                           # breakoff jitter (drops rms) with a stable stream (engineering)
SIDE_SIGMA_M = (3.5e-4, 5.0e-4)            # near / far side-stream spread at the collection (engineering)
ASPIRATOR_R = 1.5e-3                       # centre stream must hit the waste aspirator (engineering)
BEAD_STOCK_PER_M3 = 1.0e12                 # bead suspensions 1e6 / mL (assumption)
MAX_EVENTS_LOG = 1000
MAX_RECORDS, MAX_RECORD_EVENTS, MAX_STORED_EVENTS = 40, 100000, 400000
LIVE_BUFFER = 20000
MAX_TUBES, MAX_SAMPLES, MAX_GATES, MAX_LAYOUTS = 40, 40, 64, 16
AGITATION = {'off': 0.0, '100_rpm': 100.0 / 60.0, '200_rpm': 200.0 / 60.0, '300_rpm': 300.0 / 60.0}   # software speeds (1/s; Off / 100 / 200 / 300 rpm: assumption)
AGITATION_EFFECT = 0.6                     # share of the Stokes settling that 300 rpm prevents (assumption; facility: "agitation even at 300 RPM is
                                           # pretty ineffective", pause and mix samples that settle)
AMO_EXCHANGE_PER_S = {'normal': 16.0 / 60.0, 'rapid': 69.0 / 60.0}   # documented: 16 / 69 sort-chamber volumes per minute
SAMPLE_SETPOINTS_K = [277.15, 293.15, 310.15, 315.15]   # documented 4, 20, 37, 42 C
COLLECTION_RANGE_K = (277.15, 315.15)      # recirculator option: 4-42 C (assumption)
ROOM_RANGE_K = (290.65, 300.65)            # documented operating range 17.5-27.5 C
FAULTS = ['power_loss', 'clog_full', 'clog_partial', 'flow_cell_bubble', 'laser_failure_violet', 'laser_failure_blue', 'laser_failure_yellow_green',
          'laser_failure_red', 'breakoff_drift', 'cart_air_leak']
TUBE_TYPES = {'12x75': dict(capacity_m3=5.0e-6, dead_m3=5.0e-8, radius_m=5.5e-3), '15ml': dict(capacity_m3=1.5e-5, dead_m3=2.0e-7, radius_m=7.4e-3),
              '1.5ml': dict(capacity_m3=1.5e-6, dead_m3=3.0e-8, radius_m=4.5e-3)}   # dead volume / radius: engineering
PROBE_HEIGHT_M = 2.0e-3
PRECISION = {'purity': (32, 32, 0, False), '4way_purity': (0, 32, 0, False), 'yield': (32, 0, 0, False), 'single_cell': (0, 32, 16, True),
             'initial': (32, 0, 0, False), 'fine_tune': (0, 0, 0, False)}     # yield, purity, phase masks (1/32 drop), single-cell flag
STREAMS = ['far_left', 'left', 'right', 'far_right']
STREAM_SIGN = {'far_left': -1, 'left': -1, 'right': 1, 'far_right': 1}
DEVICES = {   # tube-holder geometry: nominal stream landing x (m) for each location (assumption; fitted to the default side-stream charges), capture radius
    '2way_12x75': dict(kind='tubes', tube='12x75', locations={'left': ('left', -0.014), 'right': ('right', 0.014)}, capture_r=4.4e-3),
    '2way_15ml': dict(kind='tubes', tube='15ml', locations={'left': ('left', -0.018), 'right': ('right', 0.018)}, capture_r=5.9e-3),
    '4way_12x75': dict(kind='tubes', tube='12x75', locations={'far_left': ('far_left', -0.0315), 'left': ('left', -0.014), 'right': ('right', 0.014),
                                                              'far_right': ('far_right', 0.0315)}, capture_r=4.4e-3),
    '4way_1.5ml': dict(kind='tubes', tube='1.5ml', locations={'far_left': ('far_left', -0.022), 'left': ('left', -0.0095), 'right': ('right', 0.0095),
                                                              'far_right': ('far_right', 0.022)}, capture_r=3.6e-3),
    'plate_96': dict(kind='plate', rows='ABCDEFGH', cols=12, well_m3=3.0e-7, capture_r=3.4e-3, move_s=0.25, stream='left', x=-0.014),
    'plate_384': dict(kind='plate', rows='ABCDEFGHIJKLMNOP', cols=24, well_m3=8.0e-8, capture_r=1.6e-3, move_s=0.20, stream='left', x=-0.014),
    # ACDU 6- / 24- / 48-well plates (documented formats; SBS well diameters 34.8 / 15.6 / 11.0 mm and volumes; stage move times: assumption)
    'plate_6': dict(kind='plate', rows='AB', cols=3, well_m3=1.68e-5, capture_r=1.5e-2, move_s=0.60, stream='left', x=-0.014),
    'plate_24': dict(kind='plate', rows='ABCD', cols=6, well_m3=3.4e-6, capture_r=6.8e-3, move_s=0.40, stream='left', x=-0.014),
    'plate_48': dict(kind='plate', rows='ABCDEF', cols=8, well_m3=1.6e-6, capture_r=4.8e-3, move_s=0.30, stream='left', x=-0.014),
}
NOZZLES = ['70', '85', '100', '130', 'closed_loop']
DOORS = {'optics': (0.0, 100 * math.pi / 180), 'flow_cell': (0.0, 70 * math.pi / 180), 'side': (0.0, 100 * math.pi / 180), 'sort_chamber': (0.0, 100 * math.pi / 180)}
DOOR_CLOSED_RAD = 2.0 * math.pi / 180       # a door counts as closed below 2 deg (engineering)
SPILL_MIN = 1.0e-3                          # calculated spillover below 0.1 % is set to 0 (not significant; assumption)
DOOR_OPEN_RAD = 60.0 * math.pi / 180        # access needs >= 60 deg (engineering)
PRESET_ATTEN = {'70_high': False, '70_medium': True, '85': False, '100': True, '130': True, '70_custom': False}   # documented Table 3-1 (High / Custom off,
                                                                                                                # Medium / Low on); 85 / 130 assumption
PRESET_PLATE_V = {'70_high': 6000.0, '70_medium': 5000.0, '100': 5000.0, '85': 5000.0, '130': 5000.0, '70_custom': 0.0}   # 85 / 130 assumption
PRESET_ASF = {'70_high': dict(blue=1.40, red=1.00, violet=0.80), '70_medium': dict(blue=1.20, red=0.75, violet=0.35), '100': dict(blue=1.40, red=1.00, violet=0.50),
              '70_custom': dict(blue=0.75, red=0.50, violet=0.25)}
NONSORT_PRESETS = ('70_custom',)            # Table 3-1 Custom: 70 um non-sorting mode for high sensitivity (20 psi, 30 kHz, plates off)
LASER_SPOT_ORDER = {'blue': 0.0, 'red': -1.0, 'violet': 1.0, 'yellow_green': 2.0}   # spot offsets in units of the documented +-36 us at High (YG: assumption)
ROLES_DNA = {'dna_viability', 'viability'}


def _phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _erf_v(x):
    """Vectorised erf (Abramowitz & Stegun 7.1.26, |error| < 1.5e-7)."""
    s = np.sign(x)
    x = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * x)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * np.exp(-x * x)
    return s * y


def _phi_v(x):
    return 0.5 * (1.0 + _erf_v(np.asarray(x, float) / math.sqrt(2.0)))


def _bessel_ratio(x):
    """I1(x) / I0(x) by series (x < 3)."""
    i0 = i1 = 0.0
    term0, term1 = 1.0, x / 2.0
    for k in range(30):
        i0 += term0
        i1 += term1
        term0 *= (x / 2.0) ** 2 / ((k + 1) ** 2)
        term1 *= (x / 2.0) ** 2 / ((k + 1) * (k + 2))
    return i1 / i0


def water_surface_tension(T):
    """IAPWS 2014 release on the surface tension of ordinary water (N/m)."""
    tau = 1.0 - T / 647.096
    return 0.2358 * tau ** 1.256 * (1.0 - 0.625 * tau)


def water_density(T):
    """Tanaka et al. (2001) density of SMOW at 0.1 MPa (kg/m3), T in K."""
    t = T - 273.15
    return 999.974950 * (1.0 - (t - 3.983035) ** 2 * (t + 301.797) / (522528.9 * (t + 69.34881)))


def water_viscosity(T):
    """Dynamic viscosity of water (Pa s), Vogel form fitted to IAPWS 2008 values 273-373 K (engineering fit, +-1 %)."""
    return 2.414e-5 * 10 ** (247.8 / (T - 140.0))


def jet_speed(p_pa, T, cd):
    return cd * math.sqrt(2.0 * max(p_pa, 0.0) / water_density(T))


LAMBDA_OVER_D = 4.4                        # 85 / 130 um: drop wavelength / nozzle diameter of the other presets (assumption)


def growth_rate(a, lam, T):
    """Rayleigh inviscid growth rate (1/s) of a perturbation of wavelength lam on a jet of radius a; 0 if the wavelength is stable (ka >= 1)."""
    x = 2.0 * math.pi * a / lam
    if x >= 1.0 or x <= 0.0:
        return 0.0
    return math.sqrt(water_surface_tension(T) / (water_density(T) * a ** 3) * x * (1.0 - x * x) * _bessel_ratio(x))


# ---------------------------------------------------------------- calibration of the fluidics presets (from the library defaults)
_CAL = {}


def _cal():
    if _CAL:
        return _CAL
    fl = fluidics()
    pre = fl['presets']
    T = REF_SHEATH_K
    hi = pre['70_high']
    cd_hi = fl['discharge_coefficient']['value']
    # discharge coefficient of each preset: Cd = 0.9 at High (library assumption); the documented laser delays (Table 3-1: 36 / 54 / 40 us) fix the
    # cuvette flow of Medium and Low relative to High (delay ~ 1 / flow); 85 / 130 um: drop wavelength = 4.4 nozzle diameters (assumption)
    q_hi = _q_sheath(hi['nozzle_m'], jet_speed(hi['sheath_pressure_pa'], T, cd_hi))
    cds = {}
    for name, p in pre.items():
        ideal = jet_speed(p['sheath_pressure_pa'], T, 1.0)
        if name in fl['laser_delay_us']:
            q = q_hi * fl['laser_delay_us']['70_high'] / fl['laser_delay_us'][name]
            cds[name] = q / (math.pi / 4 * p['nozzle_m'] ** 2 * ideal)
        else:
            cds[name] = LAMBDA_OVER_D * p['nozzle_m'] * p['drop_drive_frequency_hz'] / ideal
    v_hi = jet_speed(hi['sheath_pressure_pa'], T, cds['70_high'])
    lam_hi = v_hi / hi['drop_drive_frequency_hz']
    px = lam_hi / 57.0                                                             # documented: Drop 1 jumps by ~57 px (drop spacing)
    L_ref = NB_REF_70 * lam_hi
    cam_top = L_ref - (DROP1_DEFAULT - GAP_DEFAULT - _drop_r(hi['nozzle_m'] / 2, lam_hi) / px) * px
    out = dict(px=px, L_ref=L_ref, cam_top=cam_top, presets={})
    v_core_hi = 2.0 * _q_sheath(hi['nozzle_m'], v_hi) / CHANNEL_AREA
    spot = {L: LASER_SPOT_ORDER[L] * fl['laser_delay_us']['70_high'] * 1e-6 * v_core_hi for L in LASERS}
    out['spot_m'] = spot
    for name, p in pre.items():
        a = p['nozzle_m'] / 2
        f = p['drop_drive_frequency_hz']
        v = jet_speed(p['sheath_pressure_pa'], T, cds[name])
        lam = v / f
        om = growth_rate(a, lam, T)
        nb = L_ref / lam
        a_eff = p['amplitude_v'] * (ATTENUATION if PRESET_ATTEN[name] else 1.0)
        G = a / (a_eff * math.exp(nb * om / f))
        q = _q_sheath(p['nozzle_m'], v)
        vdead = max(1e-12, (p['drop_delay_drops'] - nb) * q / f)
        if name in NONSORT_PRESETS:                                                  # non-sorting defaults (10 V, 10 drops) are not breakoff settings: the
            G, vdead = out['presets']['70_high']['G'], out['presets']['70_high']['vdead']   # 70 um nozzle keeps its coupling and flow-cell volume
        r_px = _drop_r(a, lam) / px
        B_ref = (L_ref - cam_top) / px
        p0 = B_ref + r_px + GAP_DEFAULT                                             # strobe-locked drop positions: p0 + j x spacing
        out['presets'][name] = dict(nozzle_um=str(int(round(p['nozzle_m'] * 1e6))), a=a, G=G, vdead=vdead, p0=p0, pressure_pa=p['sheath_pressure_pa'],
                                    frequency_hz=f, amplitude_v=p['amplitude_v'], drop_delay=p['drop_delay_drops'], v=v, d_drop=2 * _drop_r(a, lam), cd=cds[name],
                                    lambda_over_d=lam / p['nozzle_m'], nb_ref=nb)
    # side-stream deflection constant x = K x charge fraction x V_plate, from the documented default charges landing on the 4-way 12 x 75 tubes
    dd = fl['deflection']['side_stream_voltage_pct']
    for name in out['presets']:
        if name in dd and name not in NONSORT_PRESETS:
            near = dd[name][1] / 100.0
            out['presets'][name]['K_defl'] = 0.014 / (near * PRESET_PLATE_V[name])
            out['presets'][name]['side_frac'] = [float(x) / 100.0 for x in dd[name]]
    ref = out['presets']['100']
    for name in ('85', '130'):
        p = out['presets'][name]
        p['K_defl'] = ref['K_defl'] * (ref['v'] / p['v']) ** 2 * (ref['d_drop'] / p['d_drop']) ** 2      # q/m ~ 1/d^2, deflection ~ t^2 (engineering)
        near = 0.014 / (p['K_defl'] * PRESET_PLATE_V[name])
        p['side_frac'] = [round(min(1.0, near * 2.25), 3), round(min(1.0, near), 3), round(min(1.0, near), 3), round(min(1.0, near * 2.25), 3)]
    hi_ = out['presets']['70_high']
    for name in NONSORT_PRESETS:                                                     # same nozzle as High: deflection constant scaled like 85 / 130
        p = out['presets'][name]
        p['K_defl'] = hi_['K_defl'] * (hi_['v'] / p['v']) ** 2 * (hi_['d_drop'] / p['d_drop']) ** 2
        p['side_frac'] = [float(x) / 100.0 for x in dd[name]]
    _CAL.update(out)
    return _CAL


def _drop_r(a, lam):
    return 0.5 * (6.0 * a * a * lam) ** (1.0 / 3.0)


def _q_sheath(d_noz, v):
    return math.pi / 4.0 * d_noz ** 2 * v


# ---------------------------------------------------------------- photon budget (from the optics / fluorochrome libraries)
_PB = {}


def _photon():
    """Per-dye photoelectrons per molecule at the reference transit (70 psi), saturation k tau, per-detector backgrounds; FSC / SSC scales."""
    if _PB:
        return _PB
    op = optics()
    fl = fluorochromes()
    dets = op['detectors']
    cone, T, t_ref = op['collection_solid_angle_fraction'], op['optical_transmission'], op['reference_transit_s']
    flux = {dets[d]['laser']: dets[d]['photon_flux_m2_s'] for d in FL_DETS}
    dyes = sorted(fl)
    q = np.zeros((len(dyes), len(FL_DETS)))
    ktau = np.zeros((len(dyes), len(FL_DETS)))
    for i, k in enumerate(dyes):
        d = fl[k]
        for j, det in enumerate(FL_DETS):
            L = dets[det]['laser']
            lex = d['laser_excitation'][L]
            if lex <= 0:
                continue
            kex = 3.82e-25 * d['ext_coeff_m1_cm1'] * lex * flux[L]
            kt = kex * d['lifetime_s']
            q[i, j] = d['quantum_yield'] * t_ref * kex / (1.0 + kt) * cone * T * d['detector_response'][det] / lex
            ktau[i, j] = kt
    bg = np.array([dets[d]['background_pe'] for d in FL_DETS])
    pl = particles()
    ref = pl['human_t_cell_cd4']
    sc = ref['scatter']
    lg = np.log(sc['diameter_um'])
    s_f = float(np.exp(np.interp(math.log(ref['diameter_um']), lg, np.log(sc['fsc_cross_section_m2']))))
    s_s = float(np.exp(np.interp(math.log(ref['diameter_um']), lg, np.log(sc['ssc_cross_section_m2'])))) * ref['granularity_factor']
    ph_f = s_f * flux['blue'] * t_ref
    ph_s = s_s * flux['blue'] * t_ref
    k_fsc = 60000.0 / (ph_f * (250.0 / 500.0) ** FSC_EXP)                          # lymphocyte FSC-A 60,000 at FSC 250 (engineering calibration)
    t_ssc = 15000.0 / (ph_s * (300.0 / PMT_REF_V) ** PMT_EXP * K_CH)                # lymphocyte SSC-A 15,000 at SSC 300 V (ND + optics; engineering)
    _PB.update(dyes=dyes, didx={k: i for i, k in enumerate(dyes)}, q=q, ktau=ktau, bg=bg, flux=flux, t_ref=t_ref, k_fsc=k_fsc, t_ssc=t_ssc,
               v_ref=op['reference_cuvette_speed_m_s'], fitc=dyes.index('fluorescein_fitc'))
    return _PB


BEAD_DYES = ['fluorescein_fitc', 'pe_r_pe_r_phycoerythrin', 'apc_allophycocyanin', 'pacific_blue', 'bd_horizon_v500', 'percp_cy5_5', 'pe_cf594', 'pe_cy5_5', 'pe_cy7',
             'apc_cy7']                   # hard-dyed bead blend bright in every detector, equal MESF of each dye (assumption)


# ---------------------------------------------------------------- schema helpers (as CHEM-167)
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


SID = dict(type='string', minLength=1, maxLength=40, pattern='^[A-Za-z0-9_.:-]+$')
LIBID = dict(type='string', minLength=1, maxLength=80)
PRESETS = ['70_high', '70_medium', '85', '100', '130', '70_custom']
PARAM_E = enum(PARAMS)
DETMAP = lambda s: dict(type='object', propertyNames=enum(DETS), additionalProperties=s, minProperties=1, maxProperties=len(DETS))      # noqa: E731
LASERMAP = lambda s: dict(type='object', propertyNames=enum(LASERS), additionalProperties=s, minProperties=1, maxProperties=4)         # noqa: E731
STREAMMAP = lambda s: dict(type='object', propertyNames=enum(STREAMS), additionalProperties=s, minProperties=1, maxProperties=4)       # noqa: E731
PLANT_EXAMPLE = dict(room_k=295.15, mains_voltage_v=230.0, noise_seed=1)
PLANT = obj(dict(room_k=number('K', 283.15, 308.15), mains_voltage_v=number('V', 0.0, 264.0), noise_seed=integer(0, 2 ** 31 - 1),
                 room_drift_k_per_s=number('K/s', -1e-3, 1e-3)), optional=('room_drift_k_per_s',))
STAIN = obj(dict(dye=LIBID, molecules=number('1', 0.0, 1.0e10), cv=number('1', 0.0, 2.0), target=enum(['all', 'live', 'dead'])), optional=('cv', 'target'))
POP = obj(dict(population_id=SID, particle_id=LIBID, concentration_per_m3=number('1/m3', 0.0, 1.0e16), diameter_m=number('m', 2.0e-7, 3.0e-5),
               diameter_cv=number('1', 0.0, 0.6), dead_fraction=number('1', 0.0, 1.0), doublet_fraction=number('1', 0.0, 0.5), stains=array(STAIN, 0, 12)),
          optional=('diameter_m', 'diameter_cv', 'dead_fraction', 'doublet_fraction', 'stains'))
SAMPLE_EXAMPLE = dict(sample_id='pbmc_cd4_fitc', volume_m3=2.0e-6, populations=[
    dict(population_id='cd4_t', particle_id='human_t_cell_cd4', concentration_per_m3=4.0e12, dead_fraction=0.05,
         stains=[dict(dye='fluorescein_fitc', molecules=45000.0, cv=0.35), dict(dye='propidium_iodide', molecules=1.0e9, cv=0.2, target='dead')]),
    dict(population_id='b_cells', particle_id='human_b_cell', concentration_per_m3=1.5e12, stains=[dict(dye='fluorescein_fitc', molecules=300.0, cv=0.5)])])
SOURCE = obj(dict(sample_id=SID, bead_id=LIBID, volume_m3=number('m3', 1.0e-10, 1.5e-5), stains=array(STAIN, 0, 4)), optional=('sample_id', 'bead_id', 'stains'))
TUBE_EXAMPLE = dict(tube_id='t1', tube_type='12x75', sources=[dict(sample_id='pbmc_cd4_fitc', volume_m3=5.0e-7)], buffer_volume_m3=5.0e-7)
OPERATION = obj(dict(operation_id=TEXT))
EVENT = obj(dict(time_s=number('s'), kind=enum(['power', 'laser', 'fluidics', 'stream', 'sample', 'acquisition', 'record', 'sort', 'deflection', 'safety', 'cart',
                                               'warning', 'error', 'fault', 'truth', 'data', 'cst']), code=TEXT, message_zh=TEXT))
GATE = obj(dict(gate_id=SID, parent=nullable(SID), kind=enum(['rectangle', 'polygon', 'interval']), x=PARAM_E, y=nullable(PARAM_E),
                x_range=nullable(array(number('1'), 2, 2)), y_range=nullable(array(number('1'), 2, 2)), vertices=nullable(array(array(number('1'), 2, 2), 3, 64))),
           optional=('parent', 'y', 'x_range', 'y_range', 'vertices'))
STAT_ROW = obj(dict(gate=TEXT, events=INT, pct_parent=nullable(number('1')), pct_total=nullable(number('1')),
                    parameters=array(obj(dict(parameter=TEXT, mean=nullable(number('1')), median=nullable(number('1')), cv=nullable(number('1')),
                                              rcv=nullable(number('1')))), 0, NPAR)))
MASKS = obj(dict(yield_mask=integer(0, 32), purity_mask=integer(0, 32), phase_mask=integer(0, 32), single_cell=BOOL))
LAYOUT = obj(dict(layout_id=SID, device=enum(DEVICES), precision_mode=enum(list(PRECISION) + ['custom']), masks=MASKS,
                  targets=dict(type='object', propertyNames=enum(STREAMS + ['plate']), additionalProperties=SID, minProperties=1, maxProperties=4),
                  target_events=dict(type='object', propertyNames=enum(STREAMS + ['plate']), additionalProperties=integer(1, 10 ** 9), maxProperties=4),
                  events_per_well=integer(1, 100000), wells=array(TEXT, 1, 384)),
             optional=('masks', 'target_events', 'events_per_well', 'wells'))
LAYOUT_EXAMPLE = dict(layout_id='two_way', device='2way_12x75', precision_mode='purity', targets={'left': 'cd4_pos', 'right': 'cd4_neg'},
                      target_events={'left': 10000, 'right': 10000})
LEDGER_KEYS = ['electrical_energy_j', 'sheath_used_m3', 'waste_added_m3', 'sample_used_m3', 'cleaning_fluid_used_m3', 'particles_drawn', 'particles_detected',
               'events_processed', 'electronic_aborts', 'coincident_particles', 'drops_formed', 'drops_deflected', 'particles_sorted', 'particles_to_waste',
               'particles_lost_off_target', 'particles_in_collection', 'particles_spilled_from_collection', 'particles_leaked_into_collection',
               'collection_added_m3', 'collection_spilled_m3', 'aerosol_particles_escaped', 'laser_on_time_s']
LEDGER = obj({k: number('J' if k.endswith('_j') else 'm3' if k.endswith('_m3') else 's' if k.endswith('_s') else '1') for k in LEDGER_KEYS})
POP_TRUTH = obj(dict(population_id=TEXT, particle_id=TEXT, particles=number('1'), concentration_per_m3=number('1/m3')))

cap('plant.configure', PLANT, obj(dict(plant=obj(dict(room_k=number('K'), mains_voltage_v=number('V'), noise_seed=INT, room_drift_k_per_s=number('K/s'))))),
    PLANT_EXAMPLE, ['fluidics_body', 'cart_body'], '宿主声明实验室条件：室温（厂家运行范围17.5–27.5 °C）、供电电压、室温漂移与噪声种子（只在首次声明时生效）；液流车鞘液罐满、废液罐空',
    'evaluator', 'simulation')
cap('power.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), ['power_button', 'power_panel'], '开机/关机（主电源；液流车由主机供电）')
cap('power.press_switch', obj({}), obj(dict(enabled=BOOL)), {}, ['power_button'], '按一下左侧电源面板的主电源按钮（切换开/关）')
cap('lasers.set', obj(dict(laser=enum(LASERS), enabled=BOOL)), obj(dict(laser=TEXT, enabled=BOOL, warm=BOOL)), dict(laser='blue', enabled=True), ['optics_body'],
    '开/关某台激光器（405/488/561/633 nm）；开机后需预热30 min（厂家）')
cap('door.set', obj(dict(door=enum(DOORS), angle_rad=number('rad', 0.0, 100 * math.pi / 180))), obj(dict(door=TEXT, angle_rad=number('rad'))),
    dict(door='sort_chamber', angle_rad=1.6), ['optics_door', 'flow_cell_door', 'side_door', 'sort_chamber_door'],
    '用手开/关光学门、比色池检修门（打开时快门挡住激光）、侧门或分选收集室门（角度0为关闭）', 'agent', 'robot_skill')
cap('nozzle.install', obj(dict(nozzle=enum(NOZZLES))), obj(dict(nozzle=TEXT)), dict(nozzle='70'), ['flow_cell_door', 'fluidics_body'],
    '更换喷嘴（70/85/100/130 µm，或关机用的闭环喷嘴）：须液流关闭、比色池检修门打开', 'agent', 'robot_skill')
cap('fluidics.startup', obj({}), OPERATION, {}, ['cart_body', 'sheath_tank', 'waste_tank'],
    'Fluidics Startup：检查鞘液与废液液位，用鞘液替换管路与压力缓冲罐中的去离子水（约8 min，异步）')
cap('fluidics.shutdown', obj({}), OPERATION, {}, ['cart_body', 'cleaning_reservoirs'],
    'Fluidics Shutdown：用去离子水替换鞘液并清洗比色池（约3 min，异步；须装闭环喷嘴、液流关闭）')
cap('cleaning.run', obj(dict(mode=enum(list(CLEAN_S)))), OPERATION, dict(mode='clean_flow_cell'), ['cart_body', 'cleaning_reservoirs'],
    '清洗模式：Clean Flow Cell或Prepare for Aseptic Sort（异步）')
cap('sort_setup.select', obj(dict(preset=enum(PRESETS))), obj(dict(preset=TEXT, sheath_pressure_pa=number('Pa'), frequency_hz=number('Hz'), amplitude_v=number('V'),
                                                                   drop_delay_drops=number('1'), plate_voltage_v=number('V'))),
    dict(preset='70_high'), ['fluidics_body'], '选择Sort Setup（70 µm High 70 psi / Medium 35 psi、85、100 µm Low、130 µm，或表3-1的Custom：70 µm 20 psi非分选高灵敏度模式，'
    '偏转板0 V）：载入鞘液压力、液滴驱动频率与振幅、衰减、液滴延迟、偏转板电压、侧流电荷、激光延迟、面积系数与窗口延长的默认值（须液流关闭、Sweet Spot关闭）')
cap('stream.set', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['fluidics_body', 'stream_window'], 'Breakoff窗口的Stream按钮：开/关液流（须Fluidics Startup完成、装好分选喷嘴、急停未按下）')
cap('breakoff.set', obj(dict(amplitude_v=number('V', 1.0, 80.0), frequency_hz=number('Hz', 1.0, 100000.0), drop1_target_px=number('1', 100.0, 600.0),
                             gap_target_px=number('1', 1.0, 30.0), attenuation=BOOL), optional=('amplitude_v', 'frequency_hz', 'drop1_target_px', 'gap_target_px', 'attenuation')),
    obj(dict(amplitude_v=number('V'), frequency_hz=number('Hz'), drop1_target_px=number('1'), gap_target_px=number('1'), attenuation=BOOL)),
    dict(drop1_target_px=330.0), ['fluidics_body'], '设定液滴驱动振幅、频率（Sweet Spot开启时不可改）、Drop 1与Gap目标、衰减（低于30 psi时建议打开）')
cap('sweet_spot.set', obj(dict(enabled=BOOL)), obj(dict(enabled=BOOL)), dict(enabled=True), ['fluidics_body'],
    'Sweet Spot：自动调振幅保持Drop 1与Gap；Gap偏差>2 px时暂停分选；检测到堵塞时关液流与偏转板、关抽吸抽屉、卸下样品管')
cap('sheath.set_pressure', obj(dict(pressure_pa=number('Pa', 5 * PSI, 75 * PSI))), obj(dict(pressure_pa=number('Pa'))), dict(pressure_pa=70 * PSI), ['fluidics_body'],
    '自定义鞘液压力5–75 psi（Sweet Spot开启时不可用；厂家建议用Sort Setup改压力）')
cap('deflection.set', obj(dict(voltage_on=BOOL, plate_voltage_v=number('V', 0.0, 6000.0), side_stream_fraction=STREAMMAP(number('1', 0.0, 1.0)),
                               voltage_center_fraction=number('1', -1.0, 1.0), test_sort=BOOL, optical_filter_in=BOOL),
                          optional=('voltage_on', 'plate_voltage_v', 'side_stream_fraction', 'voltage_center_fraction', 'test_sort', 'optical_filter_in')),
    obj(dict(voltage_on=BOOL, plate_voltage_v=number('V'), side_stream_fraction=obj({s: number('1') for s in STREAMS}), voltage_center_fraction=number('1'),
             test_sort=BOOL, optical_filter_in=BOOL)), dict(voltage_on=True), ['deflection_plates', 'deflection_lamp', 'sort_block'],
    'Side Stream窗口：偏转板电压开关与大小（0–6000 V）、四个侧流的充电比例（0–1，FACSDiva显示的百分比/100）、电压中心（−1–1）、测试分选、Accudrop光学滤片')
cap('drop_delay.set', obj(dict(drops=number('1', 10.0, 140.0))), obj(dict(drops=number('1'))), dict(drops=44.0), ['sort_block'], '设定液滴延迟（10–140滴）')
cap('drawer.set', obj(dict(open=BOOL)), obj(dict(open=BOOL)), dict(open=True), ['aspirator_drawer'], 'Sort Layout里的抽吸抽屉按钮：打开/关闭（默认关闭盖住收集管）')
cap('amo.set', obj(dict(enabled=BOOL, mode=enum(['normal', 'rapid'])), optional=('mode',)), obj(dict(enabled=BOOL, mode=TEXT, chamber_exchange_rate_per_s=number('1/s'))),
    dict(enabled=True, mode='normal'), ['sort_chamber', 'sort_chamber_door'],
    '气溶胶管理选件：抽吸分选收集室（厂家：正常16、快速69个腔体积/min，即0.267/1.15 s⁻¹；须关好分选收集室门）')
cap('sample.agitation', obj(dict(speed=enum(list(AGITATION)))), obj(dict(speed=TEXT, rate_hz=number('1/s'))), dict(speed='300_rpm'), ['sample_chamber'],
    '进样搅拌：选软件档位Off/100/200/300 rpm（转速以1/s发布：300 rpm = 5 s⁻¹）；搅拌只减缓沉降（设施指南：300 rpm也不太有效，易沉降的样品需定期暂停混匀）')
cap('sample.temperature', obj(dict(setpoint_k=nullable(dict(type='number', enum=list(SAMPLE_SETPOINTS_K), **{'x-unit': 'K'})))),
    obj(dict(setpoint_k=nullable(number('K')))), dict(setpoint_k=277.15), ['sample_chamber'],
    '进样室温控：设定点只能是软件提供的277.15、293.15、310.15、315.15 K（4、20、37、42 °C），null为关（约45 min到温，须关闭进样室）')
cap('collection.temperature', obj(dict(setpoint_k=nullable(number('K', *COLLECTION_RANGE_K)))), obj(dict(setpoint_k=nullable(number('K')))), dict(setpoint_k=277.15),
    ['collection_holder'], '分选收集冷却/加热（循环水选件）设定或关')
cap('sample.chamber_light', obj({}), obj(dict(on=BOOL)), {}, ['chamber_light_button', 'chamber_light'], '按进样室照明按钮（开/关照明，看样品管余量）')
cap('tube.prepare', obj(dict(tube_id=SID, tube_type=enum(TUBE_TYPES), sources=array(SOURCE, 1, 6), buffer_volume_m3=number('m3', 0.0, 1.5e-5)),
                        optional=('buffer_volume_m3',)),
    obj(dict(tube_id=TEXT, volume_m3=number('m3'), particles=number('1'))), TUBE_EXAMPLE, ['sample_chamber'],
    '在实验台上配样品管：从样品取一份、或加库中的微球（可染单一荧光做补偿对照），加缓冲液稀释', 'agent', 'robot_skill')
cap('tube.mix', obj(dict(tube_id=SID)), obj(dict(tube_id=TEXT)), dict(tube_id='t1'), ['sample_chamber'],
    '在实验台上轻弹/涡旋混匀样品管，使沉降的颗粒重新悬浮（管须在实验台上；设施指南：易沉降的样品定期暂停、卸管混匀）', 'agent', 'robot_skill')
cap('tube.install', obj(dict(tube_id=SID)), obj(dict(tube_id=TEXT)), dict(tube_id='t1'), ['loading_port'], '把样品管放到下降的进样口管座上', 'agent', 'robot_skill')
cap('tube.remove', obj({}), obj(dict(tube_id=TEXT)), {}, ['loading_port'], '从下降的进样口取下样品管放回实验台', 'agent', 'robot_skill')
cap('sample.load', obj({}), obj(dict(tube_id=TEXT)), {}, ['loading_port', 'sample_chamber', 'estop_light'],
    'Load：进样口升起把管封入进样室（约3 s，急停灯亮），加压、开始搅拌与采集（须液流开启）')
cap('sample.unload', obj({}), obj(dict(tube_id=TEXT)), {}, ['loading_port', 'sample_chamber'],
    'Unload：停止采集与搅拌，降下进样口；进行中的分选与记录随之结束（failed，SAMPLE_UNLOADED；厂家：换管后重新开始分选时计数归零）')
cap('acquisition.set', obj(dict(acquiring=BOOL)), obj(dict(acquiring=BOOL)), dict(acquiring=False), ['sample_chamber'],
    'Acquire按钮：已装载时暂停/继续进样（暂停时进样室保持关闭）')
cap('acquisition.flow_rate', obj(dict(setting=number('1', 1.0, 11.0))), obj(dict(setting=number('1'), sample_flow_m3_s=number('m3/s'))), dict(setting=1.0),
    ['sample_chamber'], '样品流速档1.0–11.0（约10–120 µl/min）')
cap('cytometer.settings', obj(dict(voltages=DETMAP(number('V', 0.0, 1000.0)), thresholds=array(obj(dict(parameter=enum(DETS), value=number('1', 0.0, ADC_MAX))), 1, 4),
                                   threshold_logic=enum(['or', 'and']), laser_delays_s=LASERMAP(number('s', -2.0e-4, 2.0e-4)), area_scaling=LASERMAP(number('1', 0.1, 5.0)),
                                   window_extension_s=number('s', 0.0, 1.0e-5), detectors_enabled=array(enum(DETS), 2, len(DETS))),
                              optional=('voltages', 'thresholds', 'threshold_logic', 'laser_delays_s', 'area_scaling', 'window_extension_s', 'detectors_enabled')),
    obj(dict(max_event_rate_eps=number('1/s'))), dict(voltages={'B530_30': 520.0}), ['optics_body'],
    '仪器设置：各检测器电压（PMT/FSC 0–1000 V）、阈值参数与数值（可多参数OR/AND）、激光延迟、面积系数、窗口延长、启用的检测器')
cap('compensation.set', obj(dict(enabled=BOOL, spillover=nullable(DETMAP(DETMAP(number('1', 0.0, 10.0))))), optional=('spillover',)),
    obj(dict(enabled=BOOL, pairs=INT)), dict(enabled=True), ['optics_body'], '补偿：开关；给溢出矩阵（荧光检测器→各检测器的溢出比例，对角为1）')
cap('compensation.calculate', obj(dict(unstained_record=SID, controls=dict(type='object', propertyNames=enum(FL_DETS), additionalProperties=SID, minProperties=1,
                                                                         maxProperties=len(FL_DETS)))),
    obj(dict(spillover=dict(type='object', additionalProperties=dict(type='object', additionalProperties=number('1'))), pairs=INT)),
    dict(unstained_record='unstained', controls={'B530_30': 'fitc_ctrl'}), ['optics_body'], '由未染色与单染对照记录计算溢出矩阵（阳性群与阴性群中位数差之比）并启用补偿')
cap('gate.define', GATE, obj(dict(gate_id=TEXT)), dict(gate_id='cells', kind='rectangle', x='FSC-A', y='SSC-A', x_range=[20000.0, 200000.0], y_range=[0.0, 100000.0]),
    ['optics_body'], '定义门：矩形、多边形或一维区间，可指定父门（层级）', 'agent', 'device_command')
cap('gate.delete', obj(dict(gate_id=SID)), obj(dict(gate_id=TEXT)), dict(gate_id='cells'), ['optics_body'], '删除门（有子门或被分选布局引用时不能删）')
cap('acquisition.record', obj(dict(record_id=SID, events=integer(1, MAX_RECORD_EVENTS), storage_gate=nullable(SID), stopping_gate=nullable(SID),
                                   max_time_s=nullable(number('s', 1.0, 86400.0))), optional=('storage_gate', 'stopping_gate', 'max_time_s')),
    OPERATION, dict(record_id='unstained', events=10000), ['optics_body'], '记录数据：保存经处理的事件直到达到事件数（可设停止门、存储门、时间上限）；异步')
cap('collection.install', obj(dict(device=enum(DEVICES), prefill_m3=number('m3', 0.0, 1.0e-5)), optional=('prefill_m3',)), obj(dict(device=TEXT, locations=array(TEXT, 1, 384))),
    dict(device='2way_12x75', prefill_m3=5.0e-7), ['collection_holder', 'sort_chamber'],
    '在分选收集室装收集装置（2/4路管架，或ACDU的6/24/48/96/384孔板；可预加收集液）；须打开分选收集室门',
    'agent', 'robot_skill')
cap('collection.remove', obj({}), obj(dict(samples=array(TEXT, 0, 384))), {}, ['collection_holder'],
    '取出收集装置：管架里每管的分选产物成为新样品（可再配管上机检查纯度）；须打开分选收集室门、不在分选', 'agent', 'robot_skill')
cap('sort.layout', LAYOUT, obj(dict(layout_id=TEXT, masks=MASKS)), LAYOUT_EXAMPLE, ['sort_block'],
    'Sort Layout：收集装置、精度模式（Purity、4-Way Purity、Yield、Single Cell、Initial、Fine Tune或自定义掩码）、每个位置的分选门与目标事件数（板：每孔事件数）')
cap('sort.start', obj(dict(layout_id=SID)), OPERATION, dict(layout_id='two_way'), ['sort_block', 'deflection_plates', 'aspirator_drawer'],
    '开始分选（须采集中、偏转板开、收集装置与布局一致；抽屉自动打开）；异步，到目标数后完成；卸下样品管即结束（换管后重新开始，计数从0起）')
cap('sort.pause', obj({}), obj(dict(state=TEXT)), {}, ['sort_block'], '暂停分选（计数停止）')
cap('sort.resume', obj({}), obj(dict(state=TEXT)), {}, ['sort_block'], '继续分选')
cap('sort.auto_drop_delay', obj({}), OPERATION, {}, ['sort_block', 'deflection_plates'],
    'Accudrop自动液滴延迟：用Accudrop微球先按Initial模式粗扫7点、再按Fine Tune细扫13点（每点5 s，共约100 s，异步），按下摄像头看到的左侧流比例取最优延迟')
cap('cst.performance_check', obj({}), OPERATION, {}, ['optics_body'], 'CS&T性能检查：用CS&T微球测各检测器rCV，并设定激光延迟与面积系数（异步，约5 min）')
cap('safety.emergency_stop', obj({}), obj(dict(latched=BOOL)), {}, ['emergency_stop'], '按急停按钮：卸下样品管、停ACDU、关液流与偏转板电压、关抽吸抽屉（不关激光与主电源），按钮锁住')
cap('safety.reset_emergency_stop', obj({}), obj(dict(latched=BOOL)), {}, ['emergency_stop'], '顺时针旋转复位急停按钮')
cap('warnings.dismiss', obj({}), obj(dict(dismissed=array(TEXT, 0, 8))), {}, ['optics_body'], '确认液位警告（鞘液低/废液满；15 min未确认会自动关液流）')
cap('cart.refill_sheath', obj(dict(volume_m3=number('m3', 0.0, 0.010))), obj(dict(sheath_m3=number('m3'))), dict(volume_m3=0.009), ['sheath_tank'],
    '给10 L鞘液罐加鞘液（须液流关闭、罐已泄压）', 'agent', 'robot_skill')
cap('cart.empty_waste', obj(dict(bleach_m3=number('m3', 0.0, 0.002)), optional=('bleach_m3',)), obj(dict(waste_m3=number('m3'))), dict(bleach_m3=0.001), ['waste_tank'],
    '倒空10 L废液罐并加入约1 L漂白剂（须液流关闭）', 'agent', 'robot_skill')
cap('cart.refill_cleaning', obj({}), obj(dict(cleaning_m3=obj({k: number('m3') for k in ('facsclean', 'facsrinse', 'di_water', 'ethanol')}))), {}, ['cleaning_reservoirs'],
    '把5 L清洗液瓶（FACSClean、FACSRinse、去离子水、乙醇）加满', 'agent', 'robot_skill')
COMP_CHOICE = enum(['recorded', 'current'])
cap('statistics.read', obj(dict(source=SID, gates=nullable(array(SID, 1, MAX_GATES)), parameters=nullable(array(PARAM_E, 1, 12)), compensation=COMP_CHOICE),
                           optional=('gates', 'parameters', 'compensation')),
    obj(dict(source=TEXT, total_events=INT, compensated=BOOL, rows=array(STAT_ROW, 0, MAX_GATES + 1))), dict(source='live', parameters=['FSC-A']), ['optics_body'],
    '统计：live（最近2万个事件，按当前补偿）或某个记录（默认按记录时保存的补偿矩阵；compensation=current时套用当前补偿）；各门事件数、占父门与总数百分比、'
    '参数均值/中位数/CV/rCV', 'agent', 'query')
cap('record.read', obj(dict(record_id=SID, parameters=array(PARAM_E, 1, 12), max_events=integer(1, 5000), compensation=COMP_CHOICE), optional=('compensation',)),
    obj(dict(record_id=TEXT, parameters=array(TEXT, 1, 12), events=array(array(number('1'), 1, 12), 0, 5000), total_events=INT, compensated=BOOL)),
    dict(record_id='unstained', parameters=['FSC-A', 'SSC-A'], max_events=500), ['optics_body'],
    '读记录中的事件（均匀抽样；荧光参数默认按记录时保存的补偿，compensation=current时套用当前补偿）', 'agent', 'query')
cap('records.list', obj({}), obj(dict(records=array(obj(dict(record_id=TEXT, events=INT, tube_id=nullable(TEXT), started_s=number('s'), finished_s=nullable(number('s')),
                                                               compensated=BOOL)), 0, MAX_RECORDS))), {}, ['optics_body'], '列出记录', 'agent', 'query')
cap('sort.side_streams', obj({}), obj(dict(streams=array(obj(dict(stream=TEXT, x_m=nullable(number('m')), spread_m=nullable(number('m')),
                                                                    target=nullable(TEXT), offset_from_target_m=nullable(number('m')))), 0, 5),
                                            centre_in_aspirator=BOOL)), {}, ['sort_block'],
    '看Side Stream窗口：测试分选或分选时各侧流落点、展宽与相对收集管中心的偏差（侧流没形成时为空）', 'agent', 'query')
cap('data.export', obj(dict(record_id=SID, kind=enum(['fcs_csv']), compensation=COMP_CHOICE), optional=('compensation',)),
    obj(dict(file=TEXT, bytes=INT, rows=INT, compensated=BOOL)), dict(record_id='unstained', kind='fcs_csv'), ['optics_body'],
    '把记录导出为FCS参数表（CSV文本；默认按记录时的补偿；写文件并记事件，不改状态槽）', 'agent', 'device_command')
cap('events.read', obj(dict(since_s=number('s', 0.0))), obj(dict(events=array(EVENT, 0, MAX_EVENTS_LOG), dropped=INT)), dict(since_s=0.0), ['optics_body'], '读事件日志',
    'agent', 'query')
cap('provenance.read', obj({}), obj(dict(libraries=array(obj(dict(name=TEXT, file=TEXT, entries=INT)), 0, 8),
                                         detectors=array(obj(dict(detector=TEXT, laser=TEXT, bandpass_m=array(number('m'), 2, 2), intended=TEXT)), 0, 16))),
    {}, ['optics_body'], '读模板库与检测器配置的来源', 'agent', 'query')
cap('sample.provide', obj(dict(sample_id=SID, volume_m3=number('m3', 1.0e-9, 5.0e-5), populations=array(POP, 1, 16))), obj(dict(sample_id=TEXT, particles=number('1'))),
    SAMPLE_EXAMPLE, ['sample_chamber'], '评测者提供一份细胞/颗粒悬液：各群体的颗粒库类型、浓度、可选粒径、死细胞比例、团聚（双联体）比例与染色（每颗粒荧光分子数、CV、只染活/死细胞）',
    'evaluator', 'simulation')
cap('tube.inspect', obj(dict(tube_id=SID)), obj(dict(tube_id=TEXT, volume_m3=number('m3'), temperature_k=number('K'), populations=array(POP_TRUTH, 0, 64),
                                               settled_depth_m=dict(type='object', additionalProperties=number('m')), liquid_height_m=number('m'))),
    dict(tube_id='t1'), ['sample_chamber'], '评测者读样品管真值：体积、温度、各群体剩余颗粒数与浓度、各群体沉降前锋深度与液面高度（前锋过了取样针就取不到）', 'evaluator', 'query')
cap('collection.inspect', obj({}), obj(dict(device=nullable(TEXT), locations=array(obj(dict(location=TEXT, volume_m3=number('m3'), temperature_k=number('K'),
                                                                                             populations=array(POP_TRUTH, 0, 64))), 0, 384))),
    {}, ['collection_holder'], '评测者读收集装置真值：每个位置的液体体积、温度、各群体颗粒数（纯度与得率的真值）', 'evaluator', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=LEDGER)), {}, ['fluidics_body'], '读守恒账目（电能、鞘液/废液/样品/清洗液、颗粒去向、液滴、气溶胶、激光开机时间）', 'evaluator', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT, 0, 16))), dict(fault='clog_full', active=True), ['fluidics_body'],
    '评测者故障注入：断电、喷嘴全堵/部分堵、比色池气泡、某台激光失效、断点漂移、液流车漏气', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 7 * 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=60.0), ['fluidics_body'], '推进宿主时钟（0.1 s物理网格）', 'evaluator',
    'simulation')
UNSUPPORTED = {
    'laser.uv_355nm': '355 nm紫外激光器不在所选配置内',
    'sort.index_sort': '索引分选（每孔记录单细胞参数）未建模',
    'acdu.slide_sort': 'ACDU玻片分选与自定义收集装置未建模',
    'optics.change_filters': '更换滤光片与二向色镜（用户可换）未建模，配置固定',
    'facsdiva.user_accounts': 'BD FACSDiva用户账户、实验模板与工作站文件系统未建模',
    'cytometer.sheath_pressure_custom_device': '自定义Sort Setup设备文件未建模（自定义压力用sheath.set_pressure）',
}
for k, v in UNSUPPORTED.items():
    cap(k, obj({}), obj({}), {}, ['optics_body'], v)

# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent', reference=None):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility, reference=reference)


_PN = lambda u: nullable(number(u))                                             # noqa: E731
_LB = obj({L: BOOL for L in LASERS})
_LN = obj({L: number('W') for L in LASERS})
slot('clock.time_s', number('s'), 's', 'derived', 'fluidics_body', '仿真时钟（0.1 s物理网格）')
slot('plant.configured', BOOL, '1', 'setpoint', 'fluidics_body', '已声明实验室条件')
slot('power.enabled', BOOL, '1', 'derived', 'power_button', '整机通电（主电源开且市电正常）')
slot('lasers.on', _LB, '1', 'setpoint', 'optics_body', '各激光器开关')
slot('lasers.warm', _LB, '1', 'derived', 'optics_body', '各激光器已预热30 min（厂家）')
slot('lasers.power_w', _LN, 'W', 'sensor', 'optics_body', '各激光器输出功率读数（预热中偏低；失效为0）')
slot('lasers.shutter_closed', BOOL, '1', 'derived', 'flow_cell_door', '比色池检修门打开时快门挡住激光')
for d in DOORS:
    slot(f'door.{d}_angle_rad', number('rad'), 'rad', 'setpoint', f'{d}_door', f'{d}门开角（0关闭；场景门关节镜像）', reference='关闭位置')
slot('nozzle.installed', nullable(enum(NOZZLES)), '1', 'setpoint', 'fluidics_body', '装着的喷嘴（closed_loop为关机用闭环喷嘴）')
slot('fluidics.state', enum(['shutdown', 'starting', 'ready', 'shutting_down', 'cleaning']), '1', 'derived', 'cart_body', '液流系统状态')
slot('sort_setup.preset', enum(PRESETS + ['custom']), '1', 'setpoint', 'fluidics_body', '当前Sort Setup（改了鞘液压力为custom）')
slot('sheath.pressure_setpoint_pa', number('Pa'), 'Pa', 'setpoint', 'fluidics_body', '鞘液压力设定')
slot('sheath.pressure_pa', number('Pa'), 'Pa', 'sensor', 'fluidics_body', '鞘液压力读数（液流关闭时为0）')
slot('stream.on', BOOL, '1', 'setpoint', 'fluidics_body', '液流开关')
slot('breakoff.amplitude_v', number('V'), 'V', 'setpoint', 'fluidics_body', '液滴驱动振幅（Sweet Spot开启时自动调节）')
slot('breakoff.frequency_hz', number('Hz'), 'Hz', 'setpoint', 'fluidics_body', '液滴驱动频率')
slot('breakoff.attenuation', BOOL, '1', 'setpoint', 'fluidics_body', '液滴驱动衰减')
slot('breakoff.drop1_target_px', number('1'), '1', 'setpoint', 'fluidics_body', 'Drop 1目标（像素）')
slot('breakoff.gap_target_px', number('1'), '1', 'setpoint', 'fluidics_body', 'Gap目标（像素，默认6）')
slot('breakoff.drop1_px', _PN('1'), '1', 'sensor', 'fluidics_body', '上摄像头测得的Drop 1（图像顶部到第一个断开液滴重心的像素；液流关闭或不成滴时为空）')
slot('breakoff.gap_px', _PN('1'), '1', 'sensor', 'fluidics_body', '上摄像头测得的Gap（液柱断点到第一个液滴的像素）')
slot('breakoff.drop_spacing_px', _PN('1'), '1', 'sensor', 'fluidics_body', '图像中的液滴间距（像素；Drop 1按此跳变）')
slot('sweet_spot.on', BOOL, '1', 'setpoint', 'fluidics_body', 'Sweet Spot开关')
slot('sweet_spot.paused', BOOL, '1', 'derived', 'fluidics_body', '断点超出范围（Gap偏差>2 px或Drop 1偏差>10 px），分选暂停（不给液滴充电）')
slot('deflection.voltage_on', BOOL, '1', 'setpoint', 'deflection_plates', '偏转板电压开关')
slot('deflection.plate_voltage_v', number('V'), 'V', 'setpoint', 'deflection_plates', '偏转板电压（0–6000 V）')
slot('deflection.side_stream_fraction', obj({s: number('1') for s in STREAMS}), '1', 'setpoint', 'deflection_plates', '四个侧流的充电比例（0–1，FACSDiva显示的百分比/100）')
slot('deflection.voltage_center_fraction', number('1'), '1', 'setpoint', 'deflection_plates', '电压中心（−1–1，整体左右移动侧流）')
slot('deflection.test_sort', BOOL, '1', 'setpoint', 'deflection_plates', '测试分选（产生测试侧流）')
slot('deflection.optical_filter_in', BOOL, '1', 'setpoint', 'sort_block', 'Accudrop光学滤片在下摄像头前')
slot('drop_delay.drops', number('1'), '1', 'setpoint', 'sort_block', '液滴延迟设定（滴）')
slot('sort.drawer_position_m', number('m'), 'm', 'derived', 'aspirator_drawer', '抽吸抽屉位置（0关闭盖住收集管，0.08 m打开；场景抽屉关节镜像）', reference='关闭位置')
slot('sort.drawer_open', BOOL, '1', 'setpoint', 'aspirator_drawer', '抽屉打开指令')
slot('amo.on', BOOL, '1', 'setpoint', 'sort_chamber', '气溶胶管理选件开关')
slot('amo.mode', enum(['normal', 'rapid']), '1', 'setpoint', 'sort_chamber', 'AMO抽吸模式')
slot('sample.agitation_rate_hz', number('1/s'), '1/s', 'setpoint', 'sample_chamber', '进样搅拌转速（1/s；0为关，300 rpm为5 s⁻¹）')
slot('sample.temperature_setpoint_k', _PN('K'), 'K', 'setpoint', 'sample_chamber', '进样室温控设定（空为关）')
slot('sample.chamber_temperature_k', number('K'), 'K', 'sensor', 'sample_chamber', '进样室温度读数')
slot('sample.chamber_light', BOOL, '1', 'setpoint', 'chamber_light', '进样室照明')
slot('sample.port_position_m', number('m'), 'm', 'derived', 'loading_port', '进样口升起高度（0下降，0.065 m封入进样室；场景进样口关节镜像）', reference='下降位置')
slot('sample.port_state', enum(['down', 'loading', 'up', 'unloading']), '1', 'derived', 'loading_port', '进样口状态')
slot('sample.tube_id', nullable(TEXT), '1', 'derived', 'loading_port', '进样口上的样品管')
slot('sample.flow_rate_setting', number('1'), '1', 'setpoint', 'sample_chamber', '流速档（1–11）')
slot('sample.acquiring', BOOL, '1', 'derived', 'sample_chamber', '正在进样采集（管已封入、加压、采集未暂停）')
slot('collection.device', nullable(enum(DEVICES)), '1', 'derived', 'collection_holder', '装着的收集装置')
slot('collection.temperature_setpoint_k', _PN('K'), 'K', 'setpoint', 'collection_holder', '收集冷却设定（空为关）')
slot('collection.temperature_k', number('K'), 'K', 'sensor', 'collection_holder', '收集管温度读数（循环水）')
slot('tubes.on_bench', array(TEXT, 0, MAX_TUBES), '1', 'derived', 'sample_chamber', '实验台上的样品管')
slot('samples.available', array(TEXT, 0, MAX_SAMPLES), '1', 'derived', 'sample_chamber', '可取用的样品（评测者提供的样品与取出的分选产物）')
slot('acquisition.threshold_rate_eps', number('1/s'), '1/s', 'sensor', 'optics_body', '过阈值事件率（最近1 s）')
slot('acquisition.processed_rate_eps', number('1/s'), '1/s', 'sensor', 'optics_body', '处理的事件率（最近1 s）')
slot('acquisition.abort_rate_eps', number('1/s'), '1/s', 'sensor', 'optics_body', '电子中止率（超过处理能力被丢弃，最近1 s）')
slot('acquisition.events_processed', INT, '1', 'derived', 'optics_body', '本次装载以来处理的事件数')
slot('cytometer.voltages', obj({d: number('V') for d in DETS}), 'V', 'setpoint', 'optics_body', '各检测器电压')
slot('cytometer.thresholds', array(obj(dict(parameter=TEXT, value=number('1'))), 1, 4), '1', 'setpoint', 'optics_body', '阈值（参数与高度通道值）')
slot('cytometer.threshold_logic', enum(['or', 'and']), '1', 'setpoint', 'optics_body', '多阈值逻辑')
slot('cytometer.laser_delays_s', obj({L: number('s') for L in LASERS}), 's', 'setpoint', 'optics_body', '各激光相对488 nm的延迟设定')
slot('cytometer.area_scaling', obj({L: number('1') for L in LASERS}), '1', 'setpoint', 'optics_body', '各激光的面积系数设定')
slot('cytometer.window_extension_s', number('s'), 's', 'setpoint', 'optics_body', '窗口延长')
slot('cytometer.detectors_enabled', array(enum(DETS), 2, len(DETS)), '1', 'setpoint', 'optics_body', '启用的检测器（每个给A/H/W）')
slot('cytometer.max_event_rate_eps', number('1/s'), '1/s', 'derived', 'optics_body',
     '处理能力：min(70,000/s, 70,000/s×14/(启用检测器数+补偿对数/2))（厂家：最高70,000/s，8参数12对补偿；参数更多时的下降为假设）')
slot('compensation.enabled', BOOL, '1', 'setpoint', 'optics_body', '补偿开关')
slot('compensation.pairs', INT, '1', 'derived', 'optics_body', '补偿矩阵非零非对角项数')
slot('gates.defined', array(TEXT, 0, MAX_GATES), '1', 'derived', 'optics_body', '已定义的门')
slot('records.count', INT, '1', 'derived', 'optics_body', '记录数')
slot('sort.layouts', array(TEXT, 0, MAX_LAYOUTS), '1', 'derived', 'sort_block', '分选布局')
slot('sort.state', enum(['idle', 'sorting', 'paused', 'completed', 'stopped', 'error']), '1', 'derived', 'sort_block', '分选状态')
slot('sort.layout_id', nullable(TEXT), '1', 'derived', 'sort_block', '当前/最近一次分选布局')
slot('sort.counts', dict(type='object', additionalProperties=INT), '1', 'derived', 'sort_block', '各位置已分选事件计数（分选决定数）')
slot('sort.conflicts', INT, '1', 'derived', 'sort_block', '冲突（掩码或位置冲突而放弃的目标事件）计数')
slot('sort.efficiency', _PN('1'), '1', 'derived', 'sort_block', '分选效率（已分选/（已分选+冲突））')
slot('sort.sort_rate_eps', number('1/s'), '1/s', 'derived', 'sort_block', '分选率（最近1 s）')
slot('sort.elapsed_s', number('s'), 's', 'derived', 'sort_block', '分选已用时间')
slot('sort.well', nullable(TEXT), '1', 'derived', 'collection_holder', '板分选当前孔')
slot('accudrop.left_stream_fraction', _PN('1'), '1', 'sensor', 'sort_block', '下摄像头（滤片在位）看到的Accudrop微球进入左侧流的比例（最近1 s；不在测量条件时为空）')
slot('cst.status', enum(['not_run', 'passed', 'failed']), '1', 'derived', 'optics_body', 'CS&T性能检查结果')
slot('cart.sheath_level_fraction', number('1'), '1', 'sensor', 'sheath_tank', '鞘液液位指示（0–1）')
slot('cart.waste_level_fraction', number('1'), '1', 'sensor', 'waste_tank', '废液液位指示（0–1）')
slot('cart.cleaning_ok', BOOL, '1', 'sensor', 'cleaning_reservoirs', '清洗液瓶指示（都高于20 %）')
slot('cart.air_pressure_pa', number('Pa'), 'Pa', 'sensor', 'gauge', '液流车气压表读数')
slot('cart.gauge_angle_rad', number('rad'), 'rad', 'derived', 'gauge_needle', '气压表指针转角（0–160 psi对应0–270°；场景指针关节镜像）', reference='0 psi')
slot('safety.estop_latched', BOOL, '1', 'derived', 'emergency_stop', '急停按钮按下锁住')
slot('safety.estop_position_m', number('m'), 'm', 'derived', 'emergency_stop', '急停按钮按入深度（0或0.006 m；场景按钮关节镜像）', reference='弹出位置')
slot('lamp.deflection_plates', BOOL, '1', 'derived', 'deflection_lamp', '偏转板高压警示灯（电压开时亮）')
slot('lamp.chamber_light', BOOL, '1', 'derived', 'chamber_light', '进样室照明灯')
slot('lamp.estop_light', BOOL, '1', 'derived', 'estop_light', '急停按钮灯（通电时：样品管装载/卸载移动中亮；急停按下锁住时亮，顺时针复位后熄灭，厂家）')
slot('operations.running', array(TEXT, 0, 8), '1', 'derived', 'optics_body', '进行中的异步操作类型')
slot('operation.started_by_other_agent', BOOL, '1', 'derived', 'optics_body', '进行中的操作是否由另一个agent启动')
slot('warnings.active', array(TEXT, 0, 16), '1', 'derived', 'optics_body', '当前警告（SHEATH_LOW、WASTE_FULL、SAMPLE_DRY、LASERS_WARMING等）')
slot('errors.active', array(TEXT, 0, 16), '1', 'derived', 'optics_body', '当前错误')
slot('truth.sheath_temperature_k', number('K'), 'K', 'process_truth', 'fluidics_body', '喷嘴处鞘液温度真值', 'evaluator')
slot('truth.jet_speed_m_s', number('m/s'), 'm/s', 'process_truth', 'fluidics_body', '喷射速度真值', 'evaluator')
slot('truth.breakoff_length_m', _PN('m'), 'm', 'process_truth', 'fluidics_body', '喷嘴到断点距离真值', 'evaluator')
slot('truth.drop_delay_drops', _PN('1'), '1', 'process_truth', 'sort_block', '真实液滴延迟（滴）', 'evaluator')
slot('truth.laser_delays_s', obj({L: number('s') for L in LASERS}), 's', 'process_truth', 'optics_body', '真实激光延迟', 'evaluator')
slot('truth.area_scaling', obj({L: number('1') for L in LASERS}), '1', 'process_truth', 'optics_body', '使小颗粒面积等于高度的真实面积系数', 'evaluator')
slot('truth.core_radius_m', number('m'), 'm', 'process_truth', 'fluidics_body', '样品芯流半径真值', 'evaluator')
slot('truth.side_stream_x_m', obj({s: _PN('m') for s in STREAMS}), 'm', 'process_truth', 'sort_block', '各侧流在收集高度的落点真值', 'evaluator')
slot('truth.cart_sheath_m3', number('m3'), 'm3', 'process_truth', 'sheath_tank', '鞘液罐液量真值', 'evaluator')
slot('truth.cart_waste_m3', number('m3'), 'm3', 'process_truth', 'waste_tank', '废液罐液量真值', 'evaluator')
slot('truth.sample_temperature_k', _PN('K'), 'K', 'process_truth', 'sample_chamber', '进样口上样品管温度真值', 'evaluator')
slot('truth.faults', array(TEXT, 0, 16), '1', 'process_truth', 'fluidics_body', '注入的故障', 'evaluator')


# ---------------------------------------------------------------- numerics
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


def _h(text):
    return zlib.crc32(str(text).encode()) & 0x7FFFFFFF


def _rng(seed, *keys):
    return np.random.Generator(np.random.PCG64(np.random.SeedSequence([int(seed)] + [int(k) & 0xFFFFFFFF for k in keys])))


def _pmt_gain(v):
    return (max(v, 0.0) / PMT_REF_V) ** PMT_EXP if v > 0 else 0.0


def _fsc_gain(v):
    return (max(v, 0.0) / PMT_REF_V) ** FSC_EXP if v > 0 else 0.0


def _deflected_fraction(eps, y_mask, sigma):
    """Fraction of particles that end up in a charged drop when the set drop delay is off by eps drops (true - set), with the yield mask
    y_mask (1/32 drop) and a breakoff jitter sigma (drops): phase uniform over the assigned drop, jitter by Gauss-Hermite quadrature."""
    y = y_mask / 64.0
    ph = (np.arange(64) + 0.5) / 64.0
    xs, ws = np.polynomial.hermite_e.hermegauss(9)
    out = 0.0
    for x, w in zip(xs, ws):
        off = np.floor(ph + eps + sigma * x)
        hit = (off == 0) | ((off == -1) & (ph < y)) | ((off == 1) & (ph >= 1.0 - y))
        out += w * float(hit.mean())
    return out / math.sqrt(2.0 * math.pi)


def _in_poly(x, y, verts):
    inside = np.zeros(len(x), bool)
    n = len(verts)
    for i in range(n):
        x1, y1 = verts[i]
        x2, y2 = verts[(i + 1) % n]
        cond = ((y1 > y) != (y2 > y))
        with np.errstate(divide='ignore', invalid='ignore'):
            xc = (x2 - x1) * (y - y1) / ((y2 - y1) if y2 != y1 else 1e-300) + x1
        inside ^= cond & (x < xc)
    return inside


class BdFacsariaiiiRuntime:
    def __init__(self, instance_id='facsaria_iii_01'):
        cal = _cal()
        _photon()
        pre = cal['presets']['70_high']
        self.instance_id = instance_id
        self.t = 0.0
        self.tick = 0
        self.plant = None
        self.switch_on = False
        self.power = False
        self.lasers = {L: dict(on=False, t_on=None) for L in LASERS}
        self.doors = {d: 0.0 for d in DOORS}
        self.nozzle = '70'
        self.fluidics_state = 'shutdown'
        self.preset = '70_high'
        self.custom = False
        self.sheath_set = pre['pressure_pa']
        self.sheath_p = 0.0
        self.stream = False
        self.stream_t_on = None
        self.bo = dict(amplitude_v=pre['amplitude_v'], frequency_hz=pre['frequency_hz'], drop1_target_px=DROP1_DEFAULT, gap_target_px=GAP_DEFAULT,
                       attenuation=PRESET_ATTEN['70_high'])
        self.sweet = False
        self.ss_paused = False
        self.defl = dict(voltage_on=False, plate_voltage_v=PRESET_PLATE_V['70_high'], side_stream_fraction=dict(zip(STREAMS, pre['side_frac'])),
                         voltage_center_fraction=0.0,
                         test_sort=False, optical_filter_in=False)
        self.dd_set = pre['drop_delay']
        self.drawer_open = False
        self.drawer_pos = 0.0
        self.amo = dict(on=False, mode='normal')
        self.agitation = 'off'
        self.sample_sp = None
        self.T_chamber = None
        self.light = False
        self.col_sp = None
        self.T_col = None
        self.T_sheath = None
        self.T_room = None
        self.samples = {}
        self.popspec = {}
        self.tubes = {}
        self.port = dict(tube=None, pos=0.0, state='down', t_up=None)
        self.acq_on = False
        self.flow_setting = 1.0
        self.events_processed = 0
        fl = fluidics()
        self.settings = dict(voltages={d: (250.0 if d == 'FSC' else 300.0 if d == 'SSC' else 500.0) for d in DETS},
                             thresholds=[dict(parameter='FSC', value=5000.0)], threshold_logic='or',
                             laser_delays_s={L: LASER_SPOT_ORDER[L] * fl['laser_delay_us']['70_high'] * 1e-6 for L in LASERS},
                             area_scaling=self._asf_default('70_high'), window_extension_s=fl['window_extension_us'] * 1e-6, detectors_enabled=list(DETS))
        self.comp_on = False
        self.S = np.eye(len(FL_DETS))
        self.gates = {}
        self.records = {}
        self.live = np.zeros((LIVE_BUFFER, NPAR), np.float32)
        self.live_n = 0
        self.live_pos = 0
        self.hist = []                             # per-tick (detected, processed, aborted, sorted, accudrop_left, accudrop_centre)
        self.collection = None
        self.layouts = {}
        self.sortrun = None
        self.operations = {}
        self.cart = dict(sheath=0.0, waste=0.0, cleaning=dict(facsclean=0.0, facsrinse=0.0, di_water=0.0, ethanol=0.0), air=0.0)
        self.estop = False
        self.level_warn = dict(shown_at=None, next_show=0.0)
        self.warnings = {}
        self.errors = {}
        self.faults = set()
        self.fault_t = {}
        self.led = {k: _Sum() for k in LEDGER_KEYS}
        self.cst_status = 'not_run'
        self.cst_result = None
        self.events, self.events_dropped = [], 0
        self.exports = {}
        self.revision = 0
        self.seq = dict(op=0, col=0)
        self._principals = {}
        self._requests = {}
        self._owners = {}
        self._caller = None
        self._stored = 0
        self._inst = {}
        self._col_volume = 0.0

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
        return BdFacsariaiiiSession(self, name)

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

    # ---------------------------------------------------------------- derived fluidics quantities
    @staticmethod
    def _asf_default(preset):
        base = PRESET_ASF.get(preset, PRESET_ASF['100'])
        return dict(violet=base['violet'], blue=base['blue'], yellow_green=base['blue'], red=base['red'])

    def _nozzle_a(self):
        return int(self.nozzle) * 0.5e-6 if self.nozzle not in (None, 'closed_loop') else None

    def _jet(self):
        """Jet speed, flow, cuvette speeds at the actual sheath pressure."""
        a = self._nozzle_a()
        T = self.T_sheath if self.T_sheath is not None else REF_SHEATH_K
        v = jet_speed(self.sheath_p, T, self._cd()) if (a is not None and self.stream) else 0.0
        q = math.pi * a * a * v if a is not None else 0.0
        v_cuv = q / CHANNEL_AREA
        return dict(a=a, v=v, q=q, v_cuv=v_cuv, v_core=CORE_SPEED_RATIO * v_cuv, T=T)

    def _breakoff(self):
        """Breakoff state of the running stream: None when no stream or no regular drop formation."""
        if not (self.power and self.stream) or self.sheath_p < 0.5 * self.sheath_set:
            return None
        j = self._jet()
        if j['a'] is None or j['v'] <= 0:
            return None
        cal = _cal()
        f = self.bo['frequency_hz']
        lam = j['v'] / f
        om = growth_rate(j['a'], lam, j['T'])
        if om <= 0:
            return None
        pc = self._ncal()
        dT = j['T'] - REF_SHEATH_K
        G = pc['G'] * (1.0 + TRANSDUCER_TC * dT)
        a_eff = self.bo['amplitude_v'] * (ATTENUATION if self.bo['attenuation'] else 1.0)
        if 'clog_full' in self.faults:
            return None
        if 'clog_partial' in self.faults:
            a_eff *= 1.6                                                            # disturbed orifice: larger effective perturbation (engineering)
        nb = (f / om) * math.log(max(j['a'] / (G * a_eff), 1.0000001))
        L_b = nb * lam
        px = cal['px']
        B = (L_b - cal['cam_top']) / px
        S = lam / px
        r = _drop_r(j['a'], lam) / px
        p0 = pc['p0']
        # first strobe-locked drop position whose top is below the jet end
        jmin = math.floor((B + r - p0) / S) + 1
        d1 = p0 + jmin * S
        gap = d1 - r - B
        if 'clog_partial' in self.faults:
            gap += 3.0
        if 'flow_cell_bubble' in self.faults:
            gap += 4.0 * math.sin(self.t * 1.7)
        visible = (0.0 <= B <= 700.0 - r) and DROP1_RANGE[0] - 60 <= d1 <= 700.0
        q = j['q']
        dd = f * pc['vdead'] / max(q, 1e-15) + nb                                  # laser-to-nozzle transit + drops to the breakoff
        overdrive = (not self.bo['attenuation']) and self.sheath_p < 30 * PSI and self.bo['amplitude_v'] > 10.0
        return dict(lam=lam, nb=nb, L_b=L_b, B=B, S=S, r=r, drop1=d1 if visible else None, gap=gap if visible else None, dd_true=dd, om=om, f=f,
                    v=j['v'], q=q, a=j['a'], overdrive=overdrive, drop_m3=math.pi * j['a'] ** 2 * lam)

    def _cd(self):
        """Discharge coefficient of the installed nozzle at the present pressure: linear in pressure between the presets of that nozzle size."""
        pts = sorted((c['pressure_pa'], c['cd']) for c in _cal()['presets'].values() if c['nozzle_um'] == self.nozzle)
        if not pts:
            return fluidics()['discharge_coefficient']['value']
        p = self.sheath_set if self.sheath_p <= 0 else self.sheath_p
        return float(np.interp(p, [x for x, _ in pts], [y for _, y in pts]))

    def _ncal(self):
        """Calibration of the installed nozzle: the preset of that nozzle size whose drop-drive frequency is closest to the present one (transducer
        coupling, strobe offset, laser-to-nozzle volume, deflection constant)."""
        cal = _cal()['presets']
        cands = [c for c in cal.values() if c['nozzle_um'] == self.nozzle]
        if not cands:
            return None
        c = dict(min(cands, key=lambda c: abs(c['frequency_hz'] - self.bo['frequency_hz'])))
        zg, zv = self._inst.get(self.nozzle, (0.0, 0.0))
        c['G'] = c['G'] * (1.0 + INSTALL_SD[0] * zg)                                  # this instrument / nozzle: coupling and dead volume differ from the defaults
        c['vdead'] = c['vdead'] * (1.0 + INSTALL_SD[1] * zv)
        return c

    def _true_laser_delays(self):
        j = self._jet()
        if j['v_core'] <= 0:
            return {L: 0.0 for L in LASERS}
        return {L: _cal()['spot_m'][L] / j['v_core'] for L in LASERS}

    def _true_asf(self):
        """Area scaling that makes the area of a small particle equal its height: documented preset values scaled with the cuvette speed."""
        j = self._jet()
        base_name = self.preset if self.preset in PRESET_ASF else '100'
        base = self._asf_default(base_name)
        pc = _cal()['presets'][base_name]
        v_ref = CORE_SPEED_RATIO * _q_sheath(int(pc['nozzle_um']) * 1e-6, pc['v']) / CHANNEL_AREA
        k = (j['v_core'] / v_ref) if j['v_core'] > 0 else 1.0
        return {L: base[L] * k for L in LASERS}

    def _stream_x(self, stream, bo=None):
        """Landing position (m) and spread of a side stream at the collection height."""
        bo = bo if bo is not None else self._breakoff()
        if bo is None or not self.defl['voltage_on'] or self.defl['plate_voltage_v'] <= 0:
            return None, None
        pc = self._ncal()
        K = pc['K_defl'] * (pc['v'] / bo['v']) ** 2 if bo['v'] > 0 else pc['K_defl']
        V = self.defl['plate_voltage_v']
        x = STREAM_SIGN[stream] * K * self.defl['side_stream_fraction'][stream] * V + K * V * self.defl['voltage_center_fraction'] * 0.1
        sig = SIDE_SIGMA_M[0 if stream in ('left', 'right') else 1]
        if bo['gap'] is not None:
            sig += 1.5e-4 * max(0.0, abs(bo['gap'] - self.bo['gap_target_px']) - 1.0)
        if bo['overdrive']:
            sig += 1.0e-3
        if 'clog_partial' in self.faults:
            x += 2.5e-3
            sig += 2.0e-3
        if 'flow_cell_bubble' in self.faults:
            sig += 1.0e-3
        return x, sig

    def _centre_x(self):
        if 'clog_partial' in self.faults:
            return 0.012, 3.0e-3
        return 0.0, 2.0e-4

    def _rate_limit(self):
        en = self.settings['detectors_enabled']
        pairs = self._pairs()
        return min(RATE_REF, RATE_REF * (8 + 0.5 * 12) / (len(en) + 0.5 * pairs))        # documented maximum 70,000 /s; reduction above 8 parameters
                                                                                     # and 12 pairs: assumption

    def _pairs(self):
        if not self.comp_on:
            return 0
        off = self.S - np.diag(np.diag(self.S))
        return int(np.count_nonzero(np.abs(off) > 1e-9))

    def _laser_power(self, L):
        """Relative output (0-1) and extra noise of a laser (warm-up, failure)."""
        s = self.lasers[L]
        if not (self.power and s['on']) or f'laser_failure_{L}' in self.faults:
            return 0.0, 0.0
        age = self.t - s['t_on']
        return 1.0 - 0.15 * math.exp(-age / WARMUP_TAU_S), 0.04 * math.exp(-age / WARMUP_TAU_S)

    def _laser_warm(self, L):
        s = self.lasers[L]
        return bool(self.power and s['on'] and f'laser_failure_{L}' not in self.faults and self.t - s['t_on'] >= WARMUP_S - 1e-9)

    def _shutter(self):
        return self.doors['flow_cell'] > DOOR_CLOSED_RAD

    # ---------------------------------------------------------------- plant, power
    def _need_plant(self):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')

    def _need_power(self):
        self._need_plant()
        if not self.power:
            raise ActionRejected('POWER_OFF')

    def _mains_ok(self):
        v = self.plant['mains_voltage_v']
        return 'power_loss' not in self.faults and 90.0 <= v <= 253.0          # 100/115/230 VAC +-10 %

    def _cmd_plant_configure(self, room_k, mains_voltage_v, noise_seed, room_drift_k_per_s=0.0):
        if self.plant is not None and noise_seed != self.plant['noise_seed']:
            raise ActionRejected('SEED_FIXED')
        first = self.plant is None
        self.plant = dict(room_k=room_k, mains_voltage_v=mains_voltage_v, noise_seed=noise_seed, room_drift_k_per_s=room_drift_k_per_s)
        if first:
            rng = _rng(noise_seed, 41)
            self._inst = {nz: (float(rng.standard_normal()), float(rng.standard_normal())) for nz in NOZZLES if nz != 'closed_loop'}
            self.T_room = room_k
            self.T_chamber = room_k
            self.T_col = room_k
            self.T_sheath = room_k
            self.cart.update(sheath=fluidics()['reservoirs']['sheath_m3'], waste=0.0,
                             cleaning={k: fluidics()['reservoirs']['cleaning_m3'] for k in self.cart['cleaning']})
        else:
            self.T_room = room_k
        if self.power and not self._mains_ok():
            self._power_off('mains')
        elif self.switch_on and not self.power and self._mains_ok():
            self._power_up()
        return dict(plant=dict(self.plant))

    def _power_up(self):
        self.power = True
        self._event('power', 'POWER_ON', '主机通电；液流车泵启动，激光器需手动开启并预热')

    def _power_off(self, cause):
        self.power = False
        for L in LASERS:
            self.lasers[L].update(on=False, t_on=None)
        self._stream_stop('POWER_OFF')
        self.defl['voltage_on'] = False
        self.defl['test_sort'] = False
        self.acq_on = False
        self.sweet = False
        for op in list(self.operations.values()):
            if op['phase'] == 'running':
                self._fail_op(op, 'POWER_OFF')
        if self.fluidics_state in ('starting', 'shutting_down', 'cleaning'):
            self.fluidics_state = 'shutdown'
        self._event('power', 'POWER_OFF', '主机断电（' + ('市电中断' if cause == 'mains' else '电源按钮') + '）：激光、液流、偏转板与采集停止')

    def _set_power(self, enabled):
        self._need_plant()
        if enabled and not self._mains_ok():
            raise ActionRejected('MAINS_OUT_OF_RANGE')
        self.switch_on = enabled
        if enabled and not self.power:
            self._power_up()
        elif not enabled and self.power:
            self._power_off('switch')
        return dict(enabled=self.power)

    def _cmd_power_set(self, enabled):
        return self._set_power(enabled)

    def _cmd_power_press_switch(self):
        return self._set_power(not self.switch_on)

    def _cmd_lasers_set(self, laser, enabled):
        self._need_power()
        s = self.lasers[laser]
        if enabled and not s['on']:
            s.update(on=True, t_on=self.t)
            self._event('laser', 'LASER_ON', f'{laser}激光器开启（预热30 min）')
        elif not enabled and s['on']:
            s.update(on=False, t_on=None)
            self._event('laser', 'LASER_OFF', f'{laser}激光器关闭')
        return dict(laser=laser, enabled=s['on'], warm=self._laser_warm(laser))

    # ---------------------------------------------------------------- doors, nozzle
    def _cmd_door_set(self, door, angle_rad):
        self._need_plant()
        if angle_rad > DOORS[door][1] + 1e-12:
            raise ActionRejected('INVALID_ARGUMENT')
        if door == 'sort_chamber' and angle_rad > DOOR_CLOSED_RAD and self.amo['on']:
            self._event('safety', 'AMO_DOOR_OPEN', '分选收集室门打开：AMO抽吸失去密封')
        self.doors[door] = float(angle_rad)
        return dict(door=door, angle_rad=self.doors[door])

    def _cmd_nozzle_install(self, nozzle):
        self._need_plant()
        if self.stream:
            raise ActionRejected('STREAM_ON')
        if self.doors['flow_cell'] < DOOR_OPEN_RAD - 1e-9:
            raise ActionRejected('DOOR_CLOSED')
        self.nozzle = nozzle
        self.faults.discard('clog_full')                                            # a new / sonicated nozzle clears a clog
        self.faults.discard('clog_partial')
        self.errors.pop('CLOG', None)
        self._event('stream', 'NOZZLE_INSTALLED', f'装上{nozzle}喷嘴')
        return dict(nozzle=nozzle)

    # ---------------------------------------------------------------- fluidics procedures
    def _new_op(self, kind, duration, **extra):
        self.seq['op'] += 1
        op_id = f'op_{self.seq["op"]}'
        op = dict(op_id=op_id, kind=kind, phase='running', started_s=self.t, ends_s=self.t + duration if duration is not None else None, error=None, result={}, **extra)
        self.operations[op_id] = op
        self._own(op_id)
        return op

    def _running_ops(self, *kinds):
        return [op for op in self.operations.values() if op['phase'] == 'running' and (not kinds or op['kind'] in kinds)]

    def _fail_op(self, op, code):
        op['phase'] = 'failed'
        op['error'] = code
        op['finished_s'] = self.t
        if op['kind'] == 'sort.start' and self.sortrun is not None and self.sortrun['op_id'] == op['op_id']:
            self.sortrun['state'] = 'stopped' if code == 'SAMPLE_UNLOADED' else 'error'
            self.sortrun['error'] = code
            self.drawer_open = False
            op['result'] = dict(counts=dict(self.sortrun['counts']), conflicts=self.sortrun['conflicts'])
        if op['kind'] == 'acquisition.record':
            r = self.records[op['record_id']]
            r['finished_s'] = self.t
            op['result'] = dict(record_id=op['record_id'], events=int(r['n']))
        self._event('error' if code not in ('CANCELLED',) else 'sort', 'OPERATION_FAILED', f'{op["kind"]}中止：{ERROR_ZH.get(code, code)}', op['op_id'])

    def _cancel_op(self, op):
        op['phase'] = 'cancelled'
        op['finished_s'] = self.t
        if op['kind'] == 'acquisition.record':
            r = self.records[op['record_id']]
            r['finished_s'] = self.t
            op['result'] = dict(record_id=op['record_id'], events=int(r['n']))
        if op['kind'] == 'sort.start' and self.sortrun is not None and self.sortrun['op_id'] == op['op_id']:
            self.sortrun['state'] = 'stopped'
            self.drawer_open = False
        if op['kind'] in ('fluidics.startup', 'fluidics.shutdown', 'cleaning.run'):
            self.fluidics_state = 'shutdown' if op['kind'] != 'cleaning.run' else 'ready'
        self._event('sort' if op['kind'] == 'sort.start' else 'fluidics', 'OPERATION_CANCELLED', f'{op["kind"]}已取消', op['op_id'])

    def _need_fluidics_idle(self):
        if self._running_ops('fluidics.startup', 'fluidics.shutdown', 'cleaning.run'):
            raise ActionRejected('FLUIDICS_BUSY')

    def _cmd_fluidics_startup(self):
        self._need_power()
        self._need_fluidics_idle()
        if self.fluidics_state != 'shutdown':
            raise ActionRejected('FLUIDICS_ALREADY_STARTED')
        if self.nozzle in (None, 'closed_loop'):
            raise ActionRejected('SORT_NOZZLE_REQUIRED')
        if self.cart['sheath'] < PRIME_SHEATH_M3 + 1e-4:
            raise ActionRejected('SHEATH_LOW')
        if self.cart['waste'] > 0.9 * fluidics()['reservoirs']['waste_m3']:
            raise ActionRejected('WASTE_FULL')
        op = self._new_op('fluidics.startup', STARTUP_S)
        self.fluidics_state = 'starting'
        self._event('fluidics', 'STARTUP', 'Fluidics Startup开始：检查液位，用鞘液替换管路与压力缓冲罐中的水', op['op_id'])
        return dict(operation_id=op['op_id'])

    def _cmd_fluidics_shutdown(self):
        self._need_power()
        self._need_fluidics_idle()
        if self.fluidics_state != 'ready':
            raise ActionRejected('FLUIDICS_NOT_READY')
        if self.stream:
            raise ActionRejected('STREAM_ON')
        if self.nozzle != 'closed_loop':
            raise ActionRejected('CLOSED_LOOP_NOZZLE_REQUIRED')
        if self.cart['cleaning']['di_water'] < 3.0e-4 or self.cart['cleaning']['facsclean'] < 5.0e-5:
            raise ActionRejected('CLEANING_FLUID_LOW')
        op = self._new_op('fluidics.shutdown', SHUTDOWN_S)
        self.fluidics_state = 'shutting_down'
        self._event('fluidics', 'SHUTDOWN', 'Fluidics Shutdown开始：去离子水替换鞘液，清洗比色池（约3 min）', op['op_id'])
        return dict(operation_id=op['op_id'])

    def _cmd_cleaning_run(self, mode):
        self._need_power()
        self._need_fluidics_idle()
        if self.fluidics_state != 'ready':
            raise ActionRejected('FLUIDICS_NOT_READY')
        if self.stream:
            raise ActionRejected('STREAM_ON')
        if mode == 'prepare_aseptic_sort' and self.nozzle != 'closed_loop':
            raise ActionRejected('CLOSED_LOOP_NOZZLE_REQUIRED')
        need = dict(facsclean=1.0e-4, facsrinse=1.0e-4, di_water=2.0e-4, ethanol=(1.0e-3 if mode == 'prepare_aseptic_sort' else 0.0))
        if any(self.cart['cleaning'][k] < v for k, v in need.items()):
            raise ActionRejected('CLEANING_FLUID_LOW')
        op = self._new_op('cleaning.run', CLEAN_S[mode], mode=mode, need=need)
        self.fluidics_state = 'cleaning'
        self._event('fluidics', 'CLEANING', f'清洗模式{mode}开始', op['op_id'])
        return dict(operation_id=op['op_id'])

    def _finish_fluidics_op(self, op):
        res = fluidics()['reservoirs']
        if op['kind'] == 'fluidics.startup':
            self._move_fluid('sheath', PRIME_SHEATH_M3)
            self.fluidics_state = 'ready'
            self._event('fluidics', 'STARTUP_DONE', 'Fluidics Startup完成：可以开液流', op['op_id'])
        elif op['kind'] == 'fluidics.shutdown':
            self._move_fluid('di_water', 3.0e-4)
            self._move_fluid('facsclean', 5.0e-5)
            self.fluidics_state = 'shutdown'
            self._event('fluidics', 'SHUTDOWN_DONE', 'Fluidics Shutdown完成：可以关机', op['op_id'])
        else:
            for k, v in op['need'].items():
                self._move_fluid(k, v)
            self.fluidics_state = 'ready'
            self._event('fluidics', 'CLEANING_DONE', f'清洗模式{op["mode"]}完成', op['op_id'])
        op['phase'] = 'succeeded'
        op['finished_s'] = self.t
        op['result'] = dict(fluidics_state=self.fluidics_state)
        del res

    def _move_fluid(self, source, vol):
        """Take vol (m3) from a cart container to the waste (sheath / cleaning fluids)."""
        if source == 'sheath':
            take = min(vol, self.cart['sheath'])
            self.cart['sheath'] -= take
            self.led['sheath_used_m3'].add(take)
        else:
            take = min(vol, self.cart['cleaning'][source])
            self.cart['cleaning'][source] -= take
            self.led['cleaning_fluid_used_m3'].add(take)
        self._to_waste(take)

    def _to_waste(self, vol):
        self.cart['waste'] += vol
        self.led['waste_added_m3'].add(vol)

    # ---------------------------------------------------------------- sort setup, stream, breakoff
    def _cmd_sort_setup_select(self, preset):
        self._need_power()
        if self.stream:
            raise ActionRejected('STREAM_ON')
        if self.sweet:
            raise ActionRejected('SWEET_SPOT_ON')
        if self._running_ops('sort.start', 'sort.auto_drop_delay', 'cst.performance_check'):
            raise ActionRejected('SORT_IN_PROGRESS')
        pc = _cal()['presets'][preset]
        self.preset = preset
        self.custom = False
        self.sheath_set = pc['pressure_pa']
        self.bo.update(amplitude_v=pc['amplitude_v'], frequency_hz=pc['frequency_hz'], attenuation=PRESET_ATTEN[preset])
        self.dd_set = pc['drop_delay']
        self.defl.update(plate_voltage_v=PRESET_PLATE_V[preset], side_stream_fraction=dict(zip(STREAMS, pc['side_frac'])))
        fl = fluidics()
        self.settings['window_extension_s'] = fl.get('window_extension_us_by_preset', {}).get(preset, fl['window_extension_us']) * 1e-6
        dl = fl['laser_delay_us'].get(preset)
        if dl is None:                                                              # spot spacing / core speed (85, 130 um: no documented value)
            hi = _cal()['presets']['70_high']
            dl = fl['laser_delay_us']['70_high'] * _q_sheath(70e-6, hi['v']) / _q_sheath(int(pc['nozzle_um']) * 1e-6, pc['v'])
        self.settings['laser_delays_s'] = {L: LASER_SPOT_ORDER[L] * dl * 1e-6 for L in LASERS}
        self.settings['area_scaling'] = self._asf_default(preset)
        self._event('stream', 'SORT_SETUP', f'Sort Setup：{preset}（鞘液{pc["pressure_pa"] / PSI:.0f} psi、{pc["frequency_hz"] / 1000:.1f} kHz）')
        return dict(preset=preset, sheath_pressure_pa=self.sheath_set, frequency_hz=self.bo['frequency_hz'], amplitude_v=self.bo['amplitude_v'],
                    drop_delay_drops=self.dd_set, plate_voltage_v=self.defl['plate_voltage_v'])

    def _cmd_sheath_set_pressure(self, pressure_pa):
        self._need_power()
        if self.sweet:
            raise ActionRejected('SWEET_SPOT_ON')
        if self._running_ops('sort.start', 'sort.auto_drop_delay'):
            raise ActionRejected('SORT_IN_PROGRESS')
        if not self.custom:
            self._custom_base = self.preset
        self.custom = True
        self.sheath_set = float(pressure_pa)
        self._event('stream', 'SHEATH_PRESSURE', f'鞘液压力设为{pressure_pa / PSI:.1f} psi（自定义；激光延迟、面积系数、液滴延迟需重新确定）')
        return dict(pressure_pa=self.sheath_set)

    def _cmd_stream_set(self, on):
        self._need_power()
        if on:
            if self.stream:
                return dict(on=True)
            if self.estop:
                raise ActionRejected('EMERGENCY_STOP_LATCHED')
            if self.fluidics_state != 'ready':
                raise ActionRejected('FLUIDICS_NOT_READY')
            if self.nozzle in (None, 'closed_loop'):
                raise ActionRejected('SORT_NOZZLE_REQUIRED')
            if self.cart['sheath'] <= 1e-5:
                raise ActionRejected('SHEATH_EMPTY')
            self.stream = True
            self.stream_t_on = self.t
            self.level_warn.update(shown_at=None, next_show=self.t)                    # a fresh warning period starts with the stream (User's Guide p.83)
            if 'flow_cell_bubble' in self.faults and self.fault_t.get('flow_cell_bubble_off', -1) >= self.fault_t.get('flow_cell_bubble', 0):
                self.faults.discard('flow_cell_bubble')
                self._event('stream', 'BUBBLE_CLEARED', '关开液流后比色池气泡排出')
            self._event('stream', 'STREAM_ON', '液流开启')
        else:
            if not self.stream:
                return dict(on=False)
            self._stream_stop('USER')
        return dict(on=self.stream)

    def _stream_stop(self, cause):
        if not self.stream:
            return
        self.stream = False
        self.stream_t_on = None
        self.sweet = False
        self.ss_paused = False
        if 'flow_cell_bubble' in self.faults:
            self.fault_t['flow_cell_bubble_off'] = self.t
        for op in self._running_ops('sort.start', 'sort.auto_drop_delay'):
            self._fail_op(op, 'STREAM_OFF')
        self._event('stream', 'STREAM_OFF', '液流关闭' + ('' if cause == 'USER' else f'（{cause}）'))

    def _cmd_breakoff_set(self, **kw):
        self._need_power()
        if not kw:
            raise ActionRejected('INVALID_ARGUMENT')
        if self.sweet and ('amplitude_v' in kw or 'frequency_hz' in kw):
            raise ActionRejected('SWEET_SPOT_ON')
        if 'frequency_hz' in kw and self._running_ops('sort.start', 'sort.auto_drop_delay'):
            raise ActionRejected('SORT_IN_PROGRESS')
        self.bo.update({k: (bool(v) if k == 'attenuation' else float(v)) for k, v in kw.items()})
        return dict(self.bo)

    def _cmd_sweet_spot_set(self, enabled):
        self._need_power()
        if enabled:
            if not self.stream:
                raise ActionRejected('STREAM_OFF')
            bo = self._breakoff()
            if bo is None or bo['drop1'] is None:
                raise ActionRejected('NO_BREAKOFF')
            self.sweet = True
            self._event('stream', 'SWEET_SPOT_ON', 'Sweet Spot开启：自动调节振幅保持Drop 1与Gap')
        else:
            self.sweet = False
            self.ss_paused = False
            self._event('stream', 'SWEET_SPOT_OFF', 'Sweet Spot关闭')
        return dict(enabled=self.sweet)

    def _sweet_spot_tick(self, bo):
        """Sweet Spot servo on the amplitude (documented: coarse to Drop 1, then fine to the Gap); returns the pause flag."""
        if bo is None or bo['drop1'] is None:
            return True
        cal = _cal()
        S, r = bo['S'], bo['r']
        pc = self._ncal()
        p0 = pc['p0']
        jt = round((self.bo['drop1_target_px'] - p0) / S)
        p_star = p0 + jt * S
        B_star = p_star - r - self.bo['gap_target_px']
        nb_star = (B_star * cal['px'] + cal['cam_top']) / bo['lam']
        j = self._jet()
        G = pc['G'] * (1.0 + TRANSDUCER_TC * (j['T'] - REF_SHEATH_K))
        atten = ATTENUATION if self.bo['attenuation'] else 1.0
        a_star = j['a'] / (G * atten * math.exp(nb_star * bo['om'] / bo['f']))
        coarse = abs(bo['drop1'] - self.bo['drop1_target_px']) > DROP1_TOL
        step = (SS_COARSE_V_S if coarse else SS_FINE_V_S) * DT
        if abs(bo['gap'] - self.bo['gap_target_px']) > 1.0 or coarse:
            da = max(-step, min(step, a_star - self.bo['amplitude_v']))
            self.bo['amplitude_v'] = float(min(80.0, max(1.0, self.bo['amplitude_v'] + da)))
        return abs(bo['gap'] - self.bo['gap_target_px']) > GAP_TOL_PAUSE or abs(bo['drop1'] - self.bo['drop1_target_px']) > DROP1_TOL

    def _clog_protect(self):
        """Documented Sweet Spot clog response: stream and plates off, aspirator drawer closed, tube unloaded."""
        self._event('error', 'CLOG_DETECTED', 'Sweet Spot检测到堵塞：关液流与偏转板、关闭抽吸抽屉、卸下样品管')
        self._error('CLOG', '喷嘴堵塞（须取下喷嘴超声清洗）')
        self.defl['voltage_on'] = False
        self.drawer_open = False
        self._unload('CLOG')
        self._stream_stop('CLOG')

    # ---------------------------------------------------------------- deflection, drawer, AMO, safety
    def _cmd_deflection_set(self, **kw):
        self._need_power()
        if not kw:
            raise ActionRejected('INVALID_ARGUMENT')
        if kw.get('voltage_on') and self.estop:
            raise ActionRejected('EMERGENCY_STOP_LATCHED')
        if kw.get('test_sort') and not (kw.get('voltage_on', self.defl['voltage_on'])):
            raise ActionRejected('PLATES_OFF')
        for k, v in kw.items():
            if k == 'side_stream_fraction':
                self.defl['side_stream_fraction'].update({s: float(x) for s, x in v.items()})
            else:
                self.defl[k] = v if isinstance(v, bool) else float(v)
        if not self.defl['voltage_on']:
            self.defl['test_sort'] = False
        self._event('deflection', 'DEFLECTION', '偏转设置：电压' + ('开' if self.defl['voltage_on'] else '关') + f'，{self.defl["plate_voltage_v"]:.0f} V')
        out = copy.deepcopy(self.defl)
        return out

    def _cmd_drop_delay_set(self, drops):
        self._need_power()
        if self._running_ops('sort.auto_drop_delay'):
            raise ActionRejected('SORT_IN_PROGRESS')
        self.dd_set = float(drops)
        return dict(drops=self.dd_set)

    def _cmd_drawer_set(self, open):
        self._need_power()
        if open and self.estop:
            raise ActionRejected('EMERGENCY_STOP_LATCHED')
        self.drawer_open = bool(open)
        return dict(open=self.drawer_open)

    def _cmd_amo_set(self, enabled, mode=None):
        self._need_power()
        if enabled and self.doors['sort_chamber'] > DOOR_CLOSED_RAD:
            raise ActionRejected('DOOR_OPEN')
        self.amo['on'] = enabled
        if mode is not None:
            self.amo['mode'] = mode
        return dict(enabled=self.amo['on'], mode=self.amo['mode'], chamber_exchange_rate_per_s=AMO_EXCHANGE_PER_S[self.amo['mode']] if self.amo['on'] else 0.0)

    def _cmd_safety_emergency_stop(self):
        self._need_plant()
        self.estop = True
        self._unload('EMERGENCY_STOP')
        if self.sortrun is not None and self.sortrun['state'] in ('sorting', 'paused'):
            for op in self._running_ops('sort.start'):
                self._fail_op(op, 'EMERGENCY_STOP')
        for op in self._running_ops('sort.auto_drop_delay', 'cst.performance_check', 'acquisition.record'):
            self._fail_op(op, 'EMERGENCY_STOP')
        self._stream_stop('EMERGENCY_STOP')
        self.defl['voltage_on'] = False
        self.defl['test_sort'] = False
        self.drawer_open = False
        who = {'agent': 'agent', 'evaluator': '评测者'}.get(self._principals.get(self._caller), '宿主')         # role, not the host-specific session name
        self._event('safety', 'EMERGENCY_STOP', f'急停（{who}按下）：卸下样品管、ACDU停止、关液流与偏转板电压、关闭抽吸抽屉（激光与主电源不受影响）；'
                                                '按钮锁住、灯亮，顺时针旋转复位')
        return dict(latched=True)

    def _cmd_safety_reset_emergency_stop(self):
        self._need_plant()
        self.estop = False
        self._event('safety', 'EMERGENCY_STOP_RESET', '急停按钮已复位')
        return dict(latched=False)

    def _cmd_warnings_dismiss(self):
        self._need_power()
        out = []
        if self.level_warn['shown_at'] is not None:
            self.level_warn.update(shown_at=None, next_show=self.t + 300.0)
            out = [c for c in ('SHEATH_LOW', 'WASTE_FULL') if c in self.warnings]
        return dict(dismissed=out)

    # ---------------------------------------------------------------- cart
    def _cmd_cart_refill_sheath(self, volume_m3):
        self._need_plant()
        if self.stream:
            raise ActionRejected('STREAM_ON')
        cap_ = fluidics()['reservoirs']['sheath_m3']
        if self.cart['sheath'] + volume_m3 > cap_ + 1e-9:
            raise ActionRejected('CONTAINER_OVERFLOW')
        self.cart['sheath'] += volume_m3
        if self.cart['sheath'] > 1e-5:
            self.errors.pop('SHEATH_EMPTY', None)
        self._event('cart', 'SHEATH_REFILL', f'鞘液罐加入{volume_m3 * 1000:.2f} L')
        return dict(sheath_m3=self.cart['sheath'])

    def _cmd_cart_empty_waste(self, bleach_m3=0.0):
        self._need_plant()
        if self.stream:
            raise ActionRejected('STREAM_ON')
        self.cart['waste'] = float(bleach_m3)
        self.errors.pop('WASTE_OVERFLOW', None)
        self._event('cart', 'WASTE_EMPTIED', '废液罐已倒空' + (f'并加入{bleach_m3 * 1000:.1f} L漂白剂' if bleach_m3 else ''))
        return dict(waste_m3=self.cart['waste'])

    def _cmd_cart_refill_cleaning(self):
        self._need_plant()
        for k in self.cart['cleaning']:
            self.cart['cleaning'][k] = fluidics()['reservoirs']['cleaning_m3']
        return dict(cleaning_m3=dict(self.cart['cleaning']))

    # ---------------------------------------------------------------- samples and tubes
    def _register_pop(self, key, spec):
        self.popspec[key] = spec
        return key

    def _cmd_sample_provide(self, sample_id, volume_m3, populations):
        self._need_plant()
        if sample_id in self.samples:
            raise ActionRejected('ITEM_EXISTS')
        if len(self.samples) >= MAX_SAMPLES:
            raise ActionRejected('TOO_MANY_ITEMS')
        pl, fl = particles(), fluorochromes()
        ids = [p['population_id'] for p in populations]
        if len(set(ids)) != len(ids):
            raise ActionRejected('DUPLICATE_POPULATION')
        counts = {}
        specs = {}
        for p in populations:
            if p['particle_id'] not in pl:
                raise ActionRejected('UNKNOWN_PARTICLE')
            for st in p.get('stains', []):
                if st['dye'] not in fl:
                    raise ActionRejected('UNKNOWN_DYE')
            lib = pl[p['particle_id']]
            key = f'{sample_id}/{p["population_id"]}'
            specs[key] = dict(key=key, population_id=p['population_id'], particle_id=p['particle_id'], kind=lib['kind'],
                              diameter_m=p.get('diameter_m', lib['diameter_um'] * 1e-6), diameter_cv=p.get('diameter_cv', lib['diameter_cv']),
                              dead_fraction=p.get('dead_fraction', 0.0), doublet_fraction=p.get('doublet_fraction', 0.0),
                              stains=[dict(dye=s['dye'], molecules=s['molecules'], cv=s.get('cv', 0.3), target=s.get('target', 'all')) for s in p.get('stains', [])],
                              peaks=None, accudrop=False, density_kg_m3=1070.0 if lib['kind'] == 'cell' else 1050.0)
            counts[key] = float(round(p['concentration_per_m3'] * volume_m3))
        for k, v in specs.items():
            self._register_pop(k, v)
        self.samples[sample_id] = dict(volume_m3=float(volume_m3), counts=counts, origin='evaluator')
        self._event('sample', 'SAMPLE_PROVIDED', f'样品{sample_id}（{len(populations)}个群体）', sample_id)
        return dict(sample_id=sample_id, particles=float(sum(counts.values())))

    def _bead_spec(self, bead_id, stains):
        lib = particles()[bead_id]
        tag = _h(json.dumps(stains, sort_keys=True)) if stains else 0
        key = f'bead:{bead_id}:{tag}'
        if key not in self.popspec:
            peaks = lib.get('peaks_mesf')
            self._register_pop(key, dict(key=key, population_id=bead_id if not stains else f'{bead_id}+stain', particle_id=bead_id, kind='bead',
                                         diameter_m=lib['diameter_um'] * 1e-6, diameter_cv=lib['diameter_cv'], dead_fraction=0.0, doublet_fraction=0.01,
                                         stains=[dict(dye=s['dye'], molecules=s['molecules'], cv=s.get('cv', 0.05), target='all') for s in stains],
                                         peaks=list(peaks) if peaks else None, accudrop=bead_id == 'accudrop_bead', density_kg_m3=1050.0))
        return key

    def _cmd_tube_prepare(self, tube_id, tube_type, sources, buffer_volume_m3=0.0):
        self._need_plant()
        if tube_id in self.tubes:
            raise ActionRejected('ITEM_EXISTS')
        if len(self.tubes) >= MAX_TUBES:
            raise ActionRejected('TOO_MANY_ITEMS')
        pl, fl = particles(), fluorochromes()
        vol = float(buffer_volume_m3)
        take = {}
        for src in sources:
            if ('sample_id' in src) == ('bead_id' in src):
                raise ActionRejected('INVALID_SOURCE')
            if 'sample_id' in src:
                if src.get('stains'):
                    raise ActionRejected('INVALID_SOURCE')
                sm = self.samples.get(src['sample_id'])
                if sm is None or (self._caller is not None and self._foreign_sample(src['sample_id'])):
                    raise ActionRejected('UNKNOWN_SAMPLE')
                if src['volume_m3'] > sm['volume_m3'] + 1e-15 - take.get(src['sample_id'], 0.0):
                    raise ActionRejected('INSUFFICIENT_SAMPLE')
                take[src['sample_id']] = take.get(src['sample_id'], 0.0) + src['volume_m3']
            else:
                if src['bead_id'] not in pl or pl[src['bead_id']]['kind'] != 'bead':
                    raise ActionRejected('UNKNOWN_PARTICLE')
                for st in src.get('stains', []):
                    if st['dye'] not in fl:
                        raise ActionRejected('UNKNOWN_DYE')
            vol += src['volume_m3']
        if vol > TUBE_TYPES[tube_type]['capacity_m3'] + 1e-12:
            raise ActionRejected('VOLUME_OUT_OF_RANGE')
        counts = {}
        rng = _rng(self.plant['noise_seed'], 11, _h(tube_id), self.tick)
        for src in sources:
            if 'sample_id' in src:
                sm = self.samples[src['sample_id']]
                frac = src['volume_m3'] / sm['volume_m3']
                for k, n in sm['counts'].items():
                    m = float(rng.binomial(int(n), min(1.0, frac))) if n > 0 else 0.0
                    sm['counts'][k] = n - m
                    counts[k] = counts.get(k, 0.0) + m
                sm['volume_m3'] -= src['volume_m3']
            else:
                key = self._bead_spec(src['bead_id'], src.get('stains', []))
                counts[key] = counts.get(key, 0.0) + float(rng.poisson(BEAD_STOCK_PER_M3 * src['volume_m3']))
        self.tubes[tube_id] = dict(type=tube_type, volume_m3=vol, counts=counts, location='bench', T=self.T_room, settle={k: 0.0 for k in counts})
        self._own('tube:' + tube_id)
        self._event('sample', 'TUBE_PREPARED', f'配好样品管{tube_id}（{vol * 1e6:.2f} ml）', tube_id)
        return dict(tube_id=tube_id, volume_m3=vol, particles=float(sum(counts.values())))

    def _cmd_tube_mix(self, tube_id):
        self._need_plant()
        tb = self.tubes.get(tube_id)
        if tb is None:
            raise ActionRejected('UNKNOWN_ITEM')
        if tb['location'] != 'bench':
            raise ActionRejected('TUBE_NOT_ON_BENCH')
        for k in tb['settle']:
            tb['settle'][k] = 0.0
        self._event('sample', 'TUBE_MIXED', f'混匀样品管{tube_id}', tube_id)
        return dict(tube_id=tube_id)

    def _foreign_sample(self, sample_id):
        owner = self._owners.get('sample:' + sample_id)
        return owner is not None and owner != self._caller and self._principals.get(owner) == 'agent'

    def _cmd_tube_install(self, tube_id):
        self._need_plant()
        tb = self.tubes.get(tube_id)
        if tb is None:
            raise ActionRejected('UNKNOWN_ITEM')
        if tb['location'] != 'bench':
            raise ActionRejected('TUBE_NOT_ON_BENCH')
        if self.port['tube'] is not None:
            raise ActionRejected('PORT_OCCUPIED')
        if self.port['state'] != 'down':
            raise ActionRejected('PORT_NOT_DOWN')
        tb['location'] = 'port'
        self.port['tube'] = tube_id
        return dict(tube_id=tube_id)

    def _cmd_tube_remove(self):
        self._need_plant()
        if self.port['tube'] is None:
            raise ActionRejected('NO_TUBE')
        if self.port['state'] != 'down':
            raise ActionRejected('PORT_NOT_DOWN')
        tid = self.port['tube']
        self.tubes[tid]['location'] = 'bench'
        self.port['tube'] = None
        return dict(tube_id=tid)

    def _cmd_sample_load(self):
        self._need_power()
        if self.estop:
            raise ActionRejected('EMERGENCY_STOP_LATCHED')
        if not self.stream:
            raise ActionRejected('STREAM_OFF')
        if self.port['tube'] is None:
            raise ActionRejected('NO_TUBE')
        if self.port['state'] != 'down':
            raise ActionRejected('PORT_NOT_DOWN')
        self.port['state'] = 'loading'
        self.acq_on = True
        self.events_processed = 0
        self.live_n = 0                                                                 # a new tube starts a new data display
        self.live_pos = 0
        self.hist = []
        self._event('sample', 'LOAD', f'装载样品管{self.port["tube"]}', self.port['tube'])
        return dict(tube_id=self.port['tube'])

    def _unload(self, cause):
        if self.port['state'] in ('loading', 'up'):
            self.port['state'] = 'unloading'
            self.port['t_up'] = None
            self.acq_on = False
            code = 'SAMPLE_UNLOADED' if cause == 'USER' else cause                  # sorting / recording end with the tube (counts restart with a new sort)
            for op in self._running_ops('sort.start', 'acquisition.record'):
                self._fail_op(op, code)
            self._event('sample', 'UNLOAD', '卸下样品管' + ('' if cause == 'USER' else f'（{cause}）'), self.port['tube'])

    def _cmd_sample_unload(self):
        self._need_power()
        if self.port['state'] not in ('loading', 'up'):
            raise ActionRejected('NOT_LOADED')
        self._unload('USER')
        return dict(tube_id=self.port['tube'])

    def _cmd_acquisition_set(self, acquiring):
        self._need_power()
        if self.port['state'] not in ('loading', 'up'):
            raise ActionRejected('NOT_LOADED')
        self.acq_on = bool(acquiring)
        return dict(acquiring=self.acq_on)

    def _flow_m3_s(self):
        lo, hi = fluidics()['sample_flow_rate_ul_min']
        s0, s1 = fluidics()['sample_flow_setting']
        return (lo + (self.flow_setting - s0) * (hi - lo) / (s1 - s0)) * 1e-9 / 60.0

    def _cmd_acquisition_flow_rate(self, setting):
        self._need_power()
        self.flow_setting = float(setting)
        return dict(setting=self.flow_setting, sample_flow_m3_s=self._flow_m3_s())

    def _cmd_sample_agitation(self, speed):
        self._need_power()
        self.agitation = speed
        return dict(speed=speed, rate_hz=AGITATION[speed])

    def _cmd_sample_temperature(self, setpoint_k):
        self._need_power()
        self.sample_sp = None if setpoint_k is None else float(setpoint_k)
        return dict(setpoint_k=self.sample_sp)

    def _cmd_collection_temperature(self, setpoint_k):
        self._need_power()
        self.col_sp = None if setpoint_k is None else float(setpoint_k)
        return dict(setpoint_k=self.col_sp)

    def _cmd_sample_chamber_light(self):
        self._need_power()
        self.light = not self.light
        return dict(on=self.light)

    # ---------------------------------------------------------------- cytometer settings, compensation, gates
    def _cmd_cytometer_settings(self, **kw):
        self._need_power()
        if not kw:
            raise ActionRejected('INVALID_ARGUMENT')
        if self._running_ops('sort.start', 'acquisition.record', 'cst.performance_check', 'sort.auto_drop_delay') and \
                ('detectors_enabled' in kw or 'thresholds' in kw or 'threshold_logic' in kw):
            raise ActionRejected('ACQUISITION_IN_PROGRESS')
        en = kw.get('detectors_enabled', self.settings['detectors_enabled'])
        if len(set(en)) != len(en) or 'FSC' not in en or 'SSC' not in en:
            raise ActionRejected('INVALID_PARAMETERS')
        thr = kw.get('thresholds', self.settings['thresholds'])
        if any(t['parameter'] not in en for t in thr) or len({t['parameter'] for t in thr}) != len(thr):
            raise ActionRejected('INVALID_THRESHOLD')
        for k, v in kw.items():
            if k in ('voltages', 'laser_delays_s', 'area_scaling'):
                self.settings[k].update({a: float(b) for a, b in v.items()})
            elif k == 'thresholds':
                self.settings[k] = [dict(parameter=t['parameter'], value=float(t['value'])) for t in v]
            elif k == 'detectors_enabled':
                self.settings[k] = [d for d in DETS if d in v]
            else:
                self.settings[k] = float(v) if k == 'window_extension_s' else v
        return dict(max_event_rate_eps=self._rate_limit())

    def _cmd_compensation_set(self, enabled, spillover=None):
        self._need_power()
        if spillover is not None:
            S = np.eye(len(FL_DETS))
            for i, d in enumerate(FL_DETS):
                row = spillover.get(d)
                if row is None:
                    continue
                if d in row and abs(row[d] - 1.0) > 1e-9:
                    raise ActionRejected('INVALID_SPILLOVER')
                for e, v in row.items():
                    if e not in FL_DETS:
                        raise ActionRejected('INVALID_SPILLOVER')
                    S[i, FL_DETS.index(e)] = v
            if abs(np.linalg.det(S)) < 1e-6:
                raise ActionRejected('INVALID_SPILLOVER')
            self.S = S
        self.comp_on = bool(enabled)
        return dict(enabled=self.comp_on, pairs=self._pairs())

    def _record_of(self, rid):
        r = self.records.get(rid)
        if r is None:
            raise ActionRejected('UNKNOWN_RECORD')
        return r

    def _cmd_compensation_calculate(self, unstained_record, controls):
        self._need_power()
        un = self._record_of(unstained_record)
        if un['n'] < 100:
            raise ActionRejected('TOO_FEW_EVENTS')
        U = un['data'][:un['n']]
        S = np.eye(len(FL_DETS))
        for det, rid in controls.items():
            r = self._record_of(rid)
            if r['n'] < 100:
                raise ActionRejected('TOO_FEW_EVENTS')
            X = r['data'][:r['n']]
            i = FL_DETS.index(det)
            prim = PIDX[f'{det}-A']
            cut = np.percentile(U[:, prim], 99.5)
            pos = X[X[:, prim] > cut]
            if len(pos) < 50:
                raise ActionRejected('NO_POSITIVE_POPULATION')
            dp = float(np.median(pos[:, prim]) - np.median(U[:, prim]))
            if dp <= 0:
                raise ActionRejected('NO_POSITIVE_POPULATION')
            for j, e in enumerate(FL_DETS):
                if j == i:
                    continue
                c = PIDX[f'{e}-A']
                v_ = max(0.0, float(np.median(pos[:, c]) - np.median(U[:, c])) / dp)
                S[i, j] = v_ if v_ >= SPILL_MIN else 0.0                        # below 0.1 %: not significant, no compensation pair
        self.S = S
        self.comp_on = True
        self._event('data', 'COMPENSATION', f'由{len(controls)}个单染对照计算补偿矩阵')
        return dict(spillover={FL_DETS[i]: {FL_DETS[j]: float(S[i, j]) for j in range(len(FL_DETS)) if S[i, j] != 0.0} for i in range(len(FL_DETS))},
                    pairs=self._pairs())

    def _cmd_gate_define(self, gate_id, kind, x, parent=None, y=None, x_range=None, y_range=None, vertices=None):
        self._need_plant()
        if gate_id in self.gates or gate_id == 'live':
            raise ActionRejected('ITEM_EXISTS')
        if len(self.gates) >= MAX_GATES:
            raise ActionRejected('TOO_MANY_ITEMS')
        if parent is not None and parent not in self.gates:
            raise ActionRejected('UNKNOWN_GATE')
        if kind == 'interval':
            ok = x_range is not None and y is None and vertices is None and x_range[0] < x_range[1]
        elif kind == 'rectangle':
            ok = y is not None and x_range is not None and y_range is not None and vertices is None and x_range[0] < x_range[1] and y_range[0] < y_range[1]
        else:
            ok = y is not None and vertices is not None and x_range is None and y_range is None
        if not ok:
            raise ActionRejected('INVALID_GATE')
        self.gates[gate_id] = dict(kind=kind, x=x, y=y, parent=parent, x_range=x_range, y_range=y_range, vertices=vertices)
        self._own('gate:' + gate_id)
        return dict(gate_id=gate_id)

    def _cmd_gate_delete(self, gate_id):
        self._need_plant()
        if gate_id not in self.gates:
            raise ActionRejected('UNKNOWN_GATE')
        if any(g['parent'] == gate_id for g in self.gates.values()):
            raise ActionRejected('GATE_IN_USE')
        if any(gate_id in lay['targets'].values() for lay in self.layouts.values()):
            raise ActionRejected('GATE_IN_USE')
        if any(op['phase'] == 'running' and gate_id in (op.get('storage_gate'), op.get('stopping_gate')) for op in self.operations.values()):
            raise ActionRejected('GATE_IN_USE')
        del self.gates[gate_id]
        return dict(gate_id=gate_id)

    def _compensate(self, X, S=None, on=None):
        """Compensated copy of an event matrix (A and H of the fluorescence detectors)."""
        S = self.S if S is None else S
        on = self.comp_on if on is None else on
        if not on or len(X) == 0:
            return X
        Y = X.astype(np.float64, copy=True)
        Si = np.linalg.inv(S)
        for suf in ('A', 'H'):
            cols = [PIDX[f'{d}-{suf}'] for d in FL_DETS]
            Y[:, cols] = Y[:, cols] @ Si
        return Y

    def _gate_mask(self, gid, X, cache):
        if gid in cache:
            return cache[gid]
        g = self.gates[gid]
        x = X[:, PIDX[g['x']]]
        if g['kind'] == 'interval':
            m = (x >= g['x_range'][0]) & (x <= g['x_range'][1])
        elif g['kind'] == 'rectangle':
            yv = X[:, PIDX[g['y']]]
            m = (x >= g['x_range'][0]) & (x <= g['x_range'][1]) & (yv >= g['y_range'][0]) & (yv <= g['y_range'][1])
        else:
            m = _in_poly(x, X[:, PIDX[g['y']]], g['vertices'])
        if g['parent'] is not None:
            m = m & self._gate_mask(g['parent'], X, cache)
        cache[gid] = m
        return m

    def _cmd_statistics_read(self, source, gates=None, parameters=None, compensation='recorded'):
        self._need_plant()
        if source == 'live':
            n = self.live_n
            X = np.roll(self.live, -self.live_pos, axis=0)[-n:] if n == LIVE_BUFFER else self.live[:n]
            S, on = self.S, self.comp_on
        else:
            r = self._record_of(source)
            X = r['data'][:r['n']]
            S, on = (r['S'], r['comp_on']) if compensation == 'recorded' else (self.S, self.comp_on)
        gates = list(gates) if gates is not None else sorted(self.gates)
        if any(g not in self.gates for g in gates):
            raise ActionRejected('UNKNOWN_GATE')
        Xc = self._compensate(X, S, on)
        cache = {}
        rows = []
        tot = len(Xc)
        params = parameters or []

        def prow(sel):
            out = []
            for p in params:
                v = Xc[sel, PIDX[p]] if len(Xc) else np.zeros(0)
                if len(v) == 0:
                    out.append(dict(parameter=p, mean=None, median=None, cv=None, rcv=None))
                    continue
                mean, med = float(np.mean(v)), float(np.median(v))
                q1, q3 = np.percentile(v, [25, 75])
                out.append(dict(parameter=p, mean=mean, median=med, cv=float(np.std(v) / mean) if mean > 0 else None,
                                rcv=float(0.7413 * (q3 - q1) / med) if med > 0 else None))
            return out
        rows.append(dict(gate='All Events', events=int(tot), pct_parent=None, pct_total=100.0 if tot else None, parameters=prow(np.ones(tot, bool))))
        for gid in gates:
            m = self._gate_mask(gid, Xc, cache)
            par = self.gates[gid]['parent']
            pn = int(np.count_nonzero(self._gate_mask(par, Xc, cache))) if par else tot
            n = int(np.count_nonzero(m))
            rows.append(dict(gate=gid, events=n, pct_parent=100.0 * n / pn if pn else None, pct_total=100.0 * n / tot if tot else None, parameters=prow(m)))
        return dict(source=source, total_events=int(tot), compensated=bool(on), rows=rows)

    def _cmd_record_read(self, record_id, parameters, max_events, compensation='recorded'):
        r = self._record_of(record_id)
        S, on = (r['S'], r['comp_on']) if compensation == 'recorded' else (self.S, self.comp_on)
        X = self._compensate(r['data'][:r['n']], S, on)
        idx = np.linspace(0, len(X) - 1, min(max_events, len(X))).astype(int) if len(X) else np.zeros(0, int)
        cols = [PIDX[p] for p in parameters]
        return dict(record_id=record_id, parameters=list(parameters), events=[[float(v) for v in X[i, cols]] for i in idx], total_events=int(r['n']),
                    compensated=bool(on))

    def _cmd_records_list(self):
        return dict(records=[dict(record_id=k, events=int(r['n']), tube_id=r['tube_id'], started_s=r['started_s'], finished_s=r['finished_s'], compensated=bool(r['comp_on']))
                             for k, r in self.records.items()])

    def _cmd_data_export(self, record_id, kind, compensation='recorded'):
        r = self._record_of(record_id)
        S, on = (r['S'], r['comp_on']) if compensation == 'recorded' else (self.S, self.comp_on)
        X = self._compensate(r['data'][:r['n']], S, on)
        en = [p for p in PARAMS if p == 'Time' or p.split('-')[0] in r['detectors']]
        cols = [PIDX[p] for p in en]
        lines = [','.join(en)] + [','.join(f'{v:.6g}' for v in row) for row in X[:, cols]]
        text = '\n'.join(lines) + '\n'
        name = f'{record_id}.csv'
        self.exports[name] = text
        self._event('data', 'EXPORT', f'导出{name}（{len(X)}个事件）', record_id)
        return dict(file=name, bytes=len(text.encode()), rows=int(len(X)), compensated=bool(on))

    def _cmd_acquisition_record(self, record_id, events, storage_gate=None, stopping_gate=None, max_time_s=None):
        self._need_power()
        if record_id in self.records or any(op.get('record_id') == record_id for op in self._running_ops('acquisition.record')):
            raise ActionRejected('ITEM_EXISTS')
        if len(self.records) >= MAX_RECORDS:
            raise ActionRejected('TOO_MANY_ITEMS')
        if self._stored + events > MAX_STORED_EVENTS:
            raise ActionRejected('STORAGE_FULL')
        for g in (storage_gate, stopping_gate):
            if g is not None and g not in self.gates:
                raise ActionRejected('UNKNOWN_GATE')
        if self._running_ops('acquisition.record'):
            raise ActionRejected('RECORDING_IN_PROGRESS')
        if self.port['state'] not in ('loading', 'up') or not self.acq_on:
            raise ActionRejected('NOT_ACQUIRING')
        op = self._new_op('acquisition.record', max_time_s, record_id=record_id, target=int(events), storage_gate=storage_gate, stopping_gate=stopping_gate)
        self.records[record_id] = dict(data=np.zeros((min(events, MAX_RECORD_EVENTS), NPAR), np.float32), n=0, stop_count=0, tube_id=self.port['tube'],
                                       started_s=self.t, finished_s=None, S=self.S.copy(), comp_on=self.comp_on, detectors=list(self.settings['detectors_enabled']),
                                       labels=np.zeros(min(events, MAX_RECORD_EVENTS), np.int32), op_id=op['op_id'])
        self._stored += events
        self._own('record:' + record_id)
        self._event('record', 'RECORD_START', f'开始记录{record_id}（{events}个事件）', record_id)
        return dict(operation_id=op['op_id'])

    def _finish_record(self, op, ok=True, code=None):
        r = self.records[op['record_id']]
        r['finished_s'] = self.t
        op['finished_s'] = self.t
        if ok:
            op['phase'] = 'succeeded'
        else:
            op['phase'] = 'failed'
            op['error'] = code
        op['result'] = dict(record_id=op['record_id'], events=int(r['n']))
        self._event('record', 'RECORD_DONE' if ok else 'RECORD_STOPPED', f'记录{op["record_id"]}结束（{r["n"]}个事件）', op['record_id'])

    # ---------------------------------------------------------------- collection devices, sort layouts
    def _cmd_collection_install(self, device, prefill_m3=0.0):
        self._need_plant()
        if self.doors['sort_chamber'] < DOOR_OPEN_RAD - 1e-9:
            raise ActionRejected('DOOR_CLOSED')
        if self.collection is not None:
            raise ActionRejected('DEVICE_INSTALLED')
        dv = DEVICES[device]
        if dv['kind'] == 'tubes':
            locs = list(dv['locations'])
            cap_ = TUBE_TYPES[dv['tube']]['capacity_m3']
        else:
            locs = [f'{r}{c}' for r in dv['rows'] for c in range(1, dv['cols'] + 1)]
            cap_ = dv['well_m3']
        if prefill_m3 > cap_:
            raise ActionRejected('VOLUME_OUT_OF_RANGE')
        self.collection = dict(device=device, capacity_m3=cap_, locs={k: dict(volume_m3=float(prefill_m3), counts={}) for k in locs})
        self._own('collection')
        return dict(device=device, locations=locs)

    def _cmd_collection_remove(self):
        self._need_plant()
        if self.collection is None:
            raise ActionRejected('NO_DEVICE')
        if self.doors['sort_chamber'] < DOOR_OPEN_RAD - 1e-9:
            raise ActionRejected('DOOR_CLOSED')
        if self._running_ops('sort.start'):
            raise ActionRejected('SORT_IN_PROGRESS')
        made = []
        self.seq['col'] += 1
        dv = DEVICES[self.collection['device']]
        if dv['kind'] == 'tubes':
            for loc, c in self.collection['locs'].items():
                if c['volume_m3'] <= 0:
                    continue
                sid = f'sorted_{self.seq["col"]}_{loc}'
                self.samples[sid] = dict(volume_m3=c['volume_m3'], counts=dict(c['counts']), origin='sorted')
                self._own('sample:' + sid)
                made.append(sid)
        self.collection = None
        self._event('sort', 'COLLECTION_REMOVED', '取出收集装置' + (f'：{", ".join(made)}' if made else ''))
        return dict(samples=made)

    def _cmd_sort_layout(self, layout_id, device, precision_mode, targets, masks=None, target_events=None, events_per_well=None, wells=None):
        self._need_plant()
        if len(self.layouts) >= MAX_LAYOUTS and layout_id not in self.layouts:
            raise ActionRejected('TOO_MANY_ITEMS')
        if any(op.get('layout_id') == layout_id for op in self._running_ops('sort.start')):
            raise ActionRejected('SORT_IN_PROGRESS')
        dv = DEVICES[device]
        if dv['kind'] == 'tubes':
            if not set(targets) <= set(dv['locations']) or events_per_well is not None or wells is not None:
                raise ActionRejected('INVALID_LAYOUT')
            if target_events is not None and not set(target_events) <= set(targets):
                raise ActionRejected('INVALID_LAYOUT')
        else:
            if set(targets) != {'plate'} or events_per_well is None:
                raise ActionRejected('INVALID_LAYOUT')
            allw = [f'{r}{c}' for r in dv['rows'] for c in range(1, dv['cols'] + 1)]
            if wells is not None and (not set(wells) <= set(allw) or len(set(wells)) != len(wells)):
                raise ActionRejected('INVALID_LAYOUT')
        if any(g not in self.gates for g in targets.values()):
            raise ActionRejected('UNKNOWN_GATE')
        if precision_mode == 'custom':
            if masks is None:
                raise ActionRejected('INVALID_LAYOUT')
            m = dict(masks)
        else:
            if masks is not None:
                raise ActionRejected('INVALID_LAYOUT')
            y, p_, ph, sc = PRECISION[precision_mode]
            m = dict(yield_mask=y, purity_mask=p_, phase_mask=ph, single_cell=sc)
        if m['phase_mask'] > 0 or m['single_cell']:
            m['yield_mask'] = 0                                                         # documented: yield and phase masks exclusive; single cell disables yield
        self.layouts[layout_id] = dict(device=device, precision_mode=precision_mode, masks=m, targets=dict(targets), target_events=dict(target_events or {}),
                                       events_per_well=events_per_well, wells=list(wells) if wells else None)
        self._own('layout:' + layout_id)
        return dict(layout_id=layout_id, masks=m)

    # ---------------------------------------------------------------- sorting control
    def _cmd_sort_start(self, layout_id):
        self._need_power()
        lay = self.layouts.get(layout_id)
        if lay is None:
            raise ActionRejected('UNKNOWN_LAYOUT')
        if self._running_ops('sort.start', 'sort.auto_drop_delay', 'cst.performance_check'):
            raise ActionRejected('SORT_IN_PROGRESS')
        if self.collection is None or self.collection['device'] != lay['device']:
            raise ActionRejected('DEVICE_MISMATCH')
        if not self.stream:
            raise ActionRejected('STREAM_OFF')
        if not self.defl['voltage_on']:
            raise ActionRejected('PLATES_OFF')
        if self.port['state'] not in ('loading', 'up') or not self.acq_on:
            raise ActionRejected('NOT_ACQUIRING')
        if DEVICES[lay['device']]['kind'] == 'plate' and self.doors['sort_chamber'] > DOOR_CLOSED_RAD:
            raise ActionRejected('DOOR_OPEN')
        op = self._new_op('sort.start', None, layout_id=layout_id)
        dv = DEVICES[lay['device']]
        wells = None
        if dv['kind'] == 'plate':
            wells = lay['wells'] or [f'{r}{c}' for r in dv['rows'] for c in range(1, dv['cols'] + 1)]
        self.sortrun = dict(op_id=op['op_id'], layout_id=layout_id, layout=copy.deepcopy(lay), state='sorting', started_s=self.t, elapsed_s=0.0,
                            counts={k: 0 for k in (lay['targets'] if dv['kind'] == 'tubes' else ['plate'])}, conflicts=0, wells=wells, well_i=0, well_n=0,
                            conflicts_by_location={k: 0 for k in (lay['targets'] if dv['kind'] == 'tubes' else ['plate'])},
                            move_until=-1.0, error=None, sorted_hist=[])
        self.drawer_open = True                                                         # the drawer opens when sorting begins (documented)
        self._event('sort', 'SORT_START', f'开始分选（{layout_id}，{lay["precision_mode"]}）', op['op_id'])
        return dict(operation_id=op['op_id'])

    def _active_sort(self):
        sr = self.sortrun
        if sr is None or not self._running_ops('sort.start'):
            raise ActionRejected('NOT_SORTING')
        return sr

    def _cmd_sort_pause(self):
        self._need_power()
        sr = self._active_sort()
        if sr['state'] != 'sorting':
            raise ActionRejected('NOT_SORTING')
        sr['state'] = 'paused'
        return dict(state='paused')

    def _cmd_sort_resume(self):
        self._need_power()
        sr = self._active_sort()
        if sr['state'] != 'paused':
            raise ActionRejected('NOT_PAUSED')
        sr['state'] = 'sorting'
        return dict(state='sorting')

    def _cmd_sort_auto_drop_delay(self):
        self._need_power()
        if self._running_ops('sort.start', 'sort.auto_drop_delay', 'cst.performance_check'):
            raise ActionRejected('SORT_IN_PROGRESS')
        if not self.stream:
            raise ActionRejected('STREAM_OFF')
        if not self.sweet:
            raise ActionRejected('SWEET_SPOT_OFF')
        if not self.defl['voltage_on']:
            raise ActionRejected('PLATES_OFF')
        if not self._tube_has(lambda s: s['accudrop']):
            raise ActionRejected('ACCUDROP_BEADS_REQUIRED')
        if self.port['state'] != 'up' or not self.acq_on:
            raise ActionRejected('NOT_ACQUIRING')                               # the run scores beads from its first 5 s
        base = round(self.dd_set)
        steps = [('initial', float(base + k)) for k in range(-3, 4)]
        op = self._new_op('sort.auto_drop_delay', None, steps=steps, step_i=0, step_t=self.t, scores=[], fine=False, base=base)
        self.defl['optical_filter_in'] = True
        self._event('sort', 'AUTO_DROP_DELAY', 'Accudrop自动液滴延迟开始（粗扫7点、细扫13点，每点5 s）', op['op_id'])
        return dict(operation_id=op['op_id'])

    def _cmd_cst_performance_check(self):
        self._need_power()
        if self._running_ops('sort.start', 'sort.auto_drop_delay', 'cst.performance_check'):
            raise ActionRejected('SORT_IN_PROGRESS')
        if not self._tube_has(lambda s: s['particle_id'].startswith('cst_bead')):
            raise ActionRejected('CST_BEADS_REQUIRED')
        if self.port['state'] != 'up' or not self.acq_on:
            raise ActionRejected('NOT_ACQUIRING')
        op = self._new_op('cst.performance_check', 300.0)
        self._event('cst', 'CST_START', 'CS&T性能检查开始（约5 min）', op['op_id'])
        return dict(operation_id=op['op_id'])

    def _tube_has(self, pred):
        tid = self.port['tube']
        if tid is None:
            return False
        tb = self.tubes[tid]
        return any(n > 0 and pred(self.popspec[k]) for k, n in tb['counts'].items())

    def _finish_cst(self, op):
        ok = True
        rows = []
        j = self._jet()
        true_d = self._true_laser_delays()
        true_a = self._true_asf()
        rng = _rng(self.plant['noise_seed'], 21, self.tick)
        for L in LASERS:
            P, extra = self._laser_power(L)
            if P <= 0:
                ok = False
                continue
            self.settings['laser_delays_s'][L] = true_d[L] + (0.0 if L == 'blue' else float(rng.normal(0.0, 0.2e-6)))
            self.settings['area_scaling'][L] = true_a[L] * (1.0 + float(rng.normal(0.0, 0.01)))
        pb = _photon()
        t_ratio = (BEAM_H / j['v_cuv']) / pb['t_ref'] if j['v_cuv'] > 0 else 0.0
        R = math.sqrt(self._flow_m3_s() / max(j['v_core'], 1e-9) / math.pi) if j['v_core'] > 0 else 0.0
        pos_rcv = R * R / (2.0 * (BEAM_W / 2) ** 2)
        bright = 2.0e5
        for i, det in enumerate(FL_DETS):
            L = DET_LASER[det]
            P, extra = self._laser_power(L)
            pe = bright * sum(pb['q'][pb['didx'][d], i] for d in BEAD_DYES) * t_ratio * P
            if pe <= 0:
                rows.append(dict(detector=det, rcv=None, passed=False))
                ok = False
                continue
            rcv = math.sqrt(0.02 ** 2 + 1.0 / pe + (LASER_NOISE + extra) ** 2 + pos_rcv ** 2)
            passed = rcv < 0.06 and self._laser_warm(L)
            ok = ok and passed
            rows.append(dict(detector=det, rcv=rcv, passed=passed))
        self.cst_status = 'passed' if ok else 'failed'
        self.cst_result = rows
        op['phase'] = 'succeeded'
        op['finished_s'] = self.t
        op['result'] = dict(status=self.cst_status, detectors=rows)
        self._event('cst', 'CST_DONE', f'CS&T性能检查{"通过" if ok else "未通过"}；已设定激光延迟与面积系数', op['op_id'])

    def _auto_dd_tick(self, op, bo, stats):
        """Accudrop automatic drop delay: every scan point lasts 5 s; its score is the left-stream share of the Accudrop beads that the lower camera (filter in)
        saw during those 5 s."""
        if not (self.stream and self.defl['voltage_on'] and self.acq_on and self.port['state'] == 'up' and bo is not None):
            self._fail_op(op, 'CONDITIONS_LOST')
            return
        cam = op.setdefault('cam', [0, 0])
        cam[0] += int(stats['al'])
        cam[1] += int(stats['ac'])
        if self.t - op['step_t'] < 5.0 - 1e-9:
            return
        mode, dd = op['steps'][op['step_i']]
        n = cam[0] + cam[1]
        op['scores'].append((dd, float(cam[0]) / n if n > 0 else 0.0))
        op['cam'] = [0, 0]
        op['step_i'] += 1
        op['step_t'] = self.t
        if op['step_i'] >= len(op['steps']):
            if not op['fine']:
                best = max(op['scores'], key=lambda x: x[1])[0]
                op['steps'] = [('fine_tune', round(best + 0.1 * k, 2)) for k in range(-6, 7)]
                op['step_i'] = 0
                op['scores_coarse'] = op['scores']
                op['scores'] = []
                op['fine'] = True
            else:
                sc = sorted(op['scores'], key=lambda x: -x[1])[:3]
                wsum = sum(s for _, s in sc)
                best = sum(d * s for d, s in sc) / wsum if wsum > 0 else sc[0][0]
                self.dd_set = float(min(140.0, max(10.0, round(best, 2))))
                op['phase'] = 'succeeded'
                op['finished_s'] = self.t
                op['result'] = dict(drop_delay_drops=self.dd_set, left_stream_fraction=max(s for _, s in op['scores']),
                                    coarse=[dict(drops=d, fraction=s) for d, s in op['scores_coarse']], fine=[dict(drops=d, fraction=s) for d, s in op['scores']])
                self._event('sort', 'AUTO_DROP_DELAY_DONE', f'自动液滴延迟完成：{self.dd_set:.2f}滴', op['op_id'])

    def _dd_sigma(self, bo):
        s = DD_JITTER
        if 'flow_cell_bubble' in self.faults:
            s += 0.2
        if bo is not None and bo['gap'] is not None:
            s += 0.01 * max(0.0, abs(bo['gap'] - self.bo['gap_target_px']) - 1.0)
        return s

    # ---------------------------------------------------------------- particle / event engine (one 0.1 s tick)
    def _tube_draw(self, tb, q_s, rng):
        """Units drawn from the loaded tube in this tick: dict of arrays (pop key index, multiplicity, t)."""
        vol = tb['volume_m3']
        dead = TUBE_TYPES[tb['type']]['dead_m3']
        avail = max(0.0, vol - dead)
        dv = min(q_s * DT, avail)
        if dv <= 0:
            return None, 0.0
        keys = sorted(tb['counts'])
        h = vol / (math.pi * TUBE_TYPES[tb['type']]['radius_m'] ** 2)
        out = []
        for ki, k in enumerate(keys):
            n_av = tb['counts'][k]
            if n_av <= 0:
                continue
            sp = self.popspec[k]
            susp = 1.0 if tb['settle'].get(k, 0.0) < max(h - PROBE_HEIGHT_M, 0.0) else 0.0
            lam = n_av / vol * dv * susp
            df = sp['doublet_fraction']
            nu = int(rng.poisson(lam / (1.0 + df))) if lam > 0 else 0
            if nu == 0:
                continue
            m = 1 + (rng.random(nu) < df).astype(np.int64)
            cum = np.cumsum(m)
            keep = cum <= n_av
            m = m[keep]
            if len(m) == 0:
                continue
            tb['counts'][k] = n_av - float(m.sum())
            out.append((k, m))
        tb['volume_m3'] = vol - dv
        self.led['sample_used_m3'].add(dv)
        return out, dv

    def _signals(self, draws, rng, j, t0):
        """Per-unit detector values (A_true before area scaling, H, A, W per detector), arrival times, labels."""
        pb = _photon()
        pl = particles()
        fl = fluorochromes()
        t_ratio = (BEAM_H / j['v_cuv']) / pb['t_ref']
        v_core = j['v_core']
        tau_s = BEAM_H / v_core
        # arrays per unit
        keys, mult, D1, D2, lab = [], [], [], [], []
        dye_list = ['fluorescein_fitc']
        for k, m in draws:
            sp = self.popspec[k]
            for st in sp['stains']:
                if st['dye'] not in dye_list:
                    dye_list.append(st['dye'])
            if sp['peaks']:
                for d in BEAD_DYES:
                    if d not in dye_list:
                        dye_list.append(d)
        di = {d: i for i, d in enumerate(dye_list)}
        n_tot = sum(len(m) for _, m in draws)
        M = np.zeros((n_tot, len(dye_list)))
        sf = np.zeros(n_tot)
        ss = np.zeros(n_tot)
        Deff = np.zeros(n_tot)
        accu = np.zeros(n_tot, bool)
        cells = np.zeros(n_tot)
        lab = np.zeros(n_tot, np.int32)
        pos0 = 0
        key_ids = []
        for kk, (k, m) in enumerate(draws):
            sp = self.popspec[k]
            n = len(m)
            sl = slice(pos0, pos0 + n)
            two = m == 2
            cv = sp['diameter_cv']
            d1 = sp['diameter_m'] * np.exp(cv * rng.standard_normal(n))
            d2 = sp['diameter_m'] * np.exp(cv * rng.standard_normal(n)) * two
            lib = pl[sp['particle_id']]
            tab = lib['scatter']
            lg = np.log(tab['diameter_um'])
            lf, ls = np.log(tab['fsc_cross_section_m2']), np.log(tab['ssc_cross_section_m2'])
            def xs(d, table):
                return np.where(d > 0, np.exp(np.interp(np.log(np.maximum(d, 1e-9) * 1e6), lg, table)), 0.0)
            gran = lib['granularity_factor']
            sf[sl] = xs(d1, lf) + xs(d2, lf)
            ss[sl] = (xs(d1, ls) + xs(d2, ls)) * gran
            Deff[sl] = d1 + d2
            dead1 = rng.random(n) < sp['dead_fraction']
            dead2 = (rng.random(n) < sp['dead_fraction']) & two
            for st in sp['stains']:
                sig = math.sqrt(math.log(1.0 + st['cv'] ** 2))
                for dead, w in ((dead1, np.ones(n)), (dead2, two.astype(float))):
                    app = np.ones(n) if st['target'] == 'all' else (dead if st['target'] == 'dead' else ~dead).astype(float)
                    M[sl, di[st['dye']]] += st['molecules'] * np.exp(sig * rng.standard_normal(n) - sig * sig / 2) * app * w
            if sp['peaks']:
                lev = np.array(sp['peaks'])[rng.integers(0, len(sp['peaks']), n)]
                for d in BEAD_DYES:
                    M[sl, di[d]] += lev * np.exp(0.03 * rng.standard_normal(n)) * (1 + two)
            af = lib.get('autofluorescence_fitc_mesf', 0.0)
            if af:
                M[sl, di['fluorescein_fitc']] += af * np.exp(0.3 * rng.standard_normal(n) - 0.045) * (1 + two)
            accu[sl] = sp['accudrop']
            cells[sl] = m
            lab[sl] = kk
            key_ids.append(k)
            pos0 += n
        t = t0 + rng.random(n_tot) * DT
        if n_tot > 1:                                                               # excluded volume: two units cannot pass closer than the sum of
            o = np.argsort(t, kind='stable')                                         # their half lengths along the core (hard-core Poisson arrivals)
            gap = 0.5 * (Deff[o][:-1] + Deff[o][1:]) / max(v_core, 1e-9)
            G = np.concatenate([[0.0], np.cumsum(gap)])
            t[o] = G + np.maximum.accumulate(t[o] - G)
        # illumination: core position in the beam, laser power / noise, delays
        R = math.sqrt(self._flow_m3_s() / v_core / math.pi)
        yv = R * np.sqrt(rng.random(n_tot)) * np.cos(2 * math.pi * rng.random(n_tot))
        pos = np.exp(-2.0 * yv ** 2 / (BEAM_W / 2) ** 2)
        if 'flow_cell_bubble' in self.faults:
            pos *= np.clip(1.0 + 0.06 * rng.standard_normal(n_tot), 0.2, 2.0)
        shut = 0.0 if self._shutter() else 1.0
        Prel, noise = {}, {}
        for L in LASERS:
            P, extra = self._laser_power(L)
            Prel[L] = P * shut
            noise[L] = np.clip(1.0 + (LASER_NOISE + extra) * rng.standard_normal(n_tot), 0.0, None)
        tau_p = np.sqrt(tau_s ** 2 + (PULSE_K * Deff / v_core) ** 2)
        true_d = self._true_laser_delays()
        true_a = self._true_asf()
        ext = self.settings['window_extension_s']
        cap = {}
        for L in LASERS:
            dlt = abs(self.settings['laser_delays_s'][L] - true_d[L])
            hw = tau_p / 2.0 + ext
            sg = tau_p / 4.0
            cap[L] = (_phi_v((hw - dlt) / sg) - _phi_v((-hw - dlt) / sg)) if dlt > 1e-12 else np.ones(n_tot)
        V = self.settings['voltages']
        en = 8.0
        out = np.zeros((n_tot, NPAR))
        Atrue = np.zeros((n_tot, len(DETS)))
        # fluorescence
        idxd = [pb['didx'][d] for d in dye_list]
        for jd, det in enumerate(FL_DETS):
            L = DET_LASER[det]
            P = Prel[L]
            if P <= 0:
                pe = np.zeros(n_tot)
                bg = 0.0
            else:
                kt = pb['ktau'][idxd, jd]
                qe = pb['q'][idxd, jd] * t_ratio * P * (1.0 + kt) / (1.0 + kt * P)
                pe = (M @ qe) * pos * noise[L]
                bg = pb['bg'][jd] * t_ratio * P
            pe_m = rng.poisson(pe + bg).astype(float) - bg
            Atrue[:, 2 + jd] = pe_m * _pmt_gain(V[det]) * K_CH * cap[L]
        # scatter (488 nm)
        P = Prel['blue']
        ph_f = sf * pb['flux']['blue'] * (BEAM_H / j['v_cuv']) * P * pos * noise['blue']
        Atrue[:, 0] = ph_f * pb['k_fsc'] * _fsc_gain(V['FSC'])
        pe_s = ss * pb['flux']['blue'] * (BEAM_H / j['v_cuv']) * P * pos * noise['blue'] * pb['t_ssc']
        pe_s = pe_s + np.sqrt(np.maximum(pe_s, 0.0)) * rng.standard_normal(n_tot)
        Atrue[:, 1] = pe_s * _pmt_gain(V['SSC']) * K_CH
        for jd, det in enumerate(DETS):
            L = DET_LASER[det]
            e = math.sqrt(en ** 2 + FSC_NOISE_CH ** 2) if det == 'FSC' else en
            H = Atrue[:, jd] * tau_s / tau_p + e * rng.standard_normal(n_tot)
            A = Atrue[:, jd] * self.settings['area_scaling'][L] / max(true_a[L], 1e-9) + e * rng.standard_normal(n_tot)
            H = np.clip(H, -2000.0, ADC_MAX)
            A = np.clip(A, -2000.0, ADC_MAX)
            out[:, 3 * jd] = A
            out[:, 3 * jd + 1] = H
            out[:, 3 * jd + 2] = np.clip(WIDTH_SCALE * A / np.maximum(H, 1.0), 0.0, ADC_MAX)
        out[:, -1] = t
        order = np.argsort(t, kind='stable')
        return dict(vals=out[order], t=t[order], lab=lab[order], cells=cells[order], accu=accu[order], tau=tau_p[order], keys=key_ids)

    def _threshold(self, vals):
        en = self.settings['detectors_enabled']
        masks = []
        for th in self.settings['thresholds']:
            if th['parameter'] not in en:
                continue
            masks.append(vals[:, PIDX[f'{th["parameter"]}-H']] > th['value'])
        if not masks:
            return np.zeros(len(vals), bool)
        m = masks[0]
        for x in masks[1:]:
            m = (m | x) if self.settings['threshold_logic'] == 'or' else (m & x)
        return m

    def _merge(self, sig, det_idx):
        """Pulse pile-up: detected units closer than the previous pulse + window extension form one event (summed areas)."""
        t = sig['t'][det_idx]
        tau = sig['tau'][det_idx]
        vals = sig['vals'][det_idx]
        if len(t) == 0:
            return vals, t, det_idx, np.zeros(0, int)
        ext = self.settings['window_extension_s']
        new = np.ones(len(t), bool)
        new[1:] = (t[1:] - t[:-1]) >= (tau[:-1] + ext)
        gid = np.cumsum(new) - 1
        starts = np.nonzero(new)[0]
        ev = vals[starts].copy()
        sizes = np.bincount(gid)
        for g in np.nonzero(sizes > 1)[0]:
            mem = np.nonzero(gid == g)[0]
            dtm = t[mem] - t[mem[0]]
            for jd in range(len(DETS)):
                A = vals[mem, 3 * jd]
                H = vals[mem, 3 * jd + 1]
                k = int(np.argmax(H))
                over = np.clip(1.0 - np.abs(dtm - dtm[k]) / np.maximum(tau[mem], 1e-12), 0.0, 1.0)
                Hm = H[k] + float(np.sum(np.where(np.arange(len(mem)) == k, 0.0, H * over)))
                Am = float(np.sum(A))
                ev[g, 3 * jd] = min(Am, ADC_MAX)
                ev[g, 3 * jd + 1] = min(Hm, ADC_MAX)
                ev[g, 3 * jd + 2] = min(WIDTH_SCALE * Am / max(Hm, 1.0), ADC_MAX)
        self.led['coincident_particles'].add(float(len(t) - len(starts)))
        return ev, t[starts], det_idx[starts], gid

    def _classify(self, vals):
        """Location index of each processed event for the running sort (first matching target in layout order), -1 for non-targets."""
        sr = self.sortrun
        locs = self._sort_locs()
        cls = np.full(len(vals), -1, np.int64)
        if len(vals) == 0:
            return cls
        Xc = self._compensate(vals)
        cache = {}
        for li, loc in enumerate(locs):
            g = sr['layout']['targets'][loc]
            m = self._gate_mask(g, Xc, cache) & (cls < 0)
            cls[m] = li
        return cls

    def _sort_locs(self):
        lay = self.sortrun['layout']
        if DEVICES[lay['device']]['kind'] == 'plate':
            return ['plate']
        return [s for s in STREAMS if s in lay['targets']]

    def _sort_tick(self, sig, ev_t, ev_first, unit_event, ev_cls, bo, rng):
        """Drop decisions (BD masks), conflicts, quotas and the fate of every particle drawn in this tick."""
        sr = self.sortrun
        lay = sr['layout']
        dv = DEVICES[lay['device']]
        locs = self._sort_locs()
        nloc = len(locs)
        f = bo['f']
        mk = lay['masks']
        Y, P, Ph, single = mk['yield_mask'], mk['purity_mask'], mk['phase_mask'], mk['single_cell']
        xs = ev_t * f + self.dd_set
        ks = np.floor(xs).astype(np.int64)
        ph = np.floor((xs - ks) * 32.0) / 32.0
        ut = sig['t']
        kt_u = np.floor(ut * f + bo['dd_true'] + self._dd_sigma(bo) * rng.standard_normal(len(ut))).astype(np.int64)
        allk = np.concatenate([ks, kt_u]) if len(ks) else kt_u
        k0 = int(allk.min()) - 2 if len(allk) else 0
        size = int(allk.max()) - k0 + 3 if len(allk) else 3
        idx = ks - k0
        charged = np.full(size, -1, np.int64)
        drop_well = np.full(size, -1, np.int64)                                          # plate: well that the charged drop is aimed at
        sorted_now = np.zeros(nloc, np.int64)
        conflicts = 0
        drawer = self.drawer_pos >= DRAWER_TRAVEL - 1e-6
        active = sr['state'] == 'sorting' and not self.ss_paused and self.defl['voltage_on'] and self.t >= sr['move_until'] and drawer
        if active and len(ks) and dv['kind'] == 'tubes':
            for l_, loc in enumerate(locs):                                              # a location that reached its target count no longer sorts
                tgt = lay['target_events'].get(loc)
                if tgt is not None and sr['counts'][loc] >= tgt:
                    ev_cls = np.where(ev_cls == l_, -1, ev_cls)
        if active and len(ks):
            cnt_all = np.bincount(idx, minlength=size)
            cnt_l = [np.bincount(idx[ev_cls == L], minlength=size) for L in range(nloc)]

            def edges(m, sel):
                lead = np.bincount(idx[sel & (ph < m / 64.0)], minlength=size)
                trail = np.bincount(idx[sel & (ph >= 1.0 - m / 64.0)], minlength=size)
                return lead, trail
            tg = np.nonzero(ev_cls >= 0)[0]
            L = ev_cls[tg]
            k = idx[tg]
            phi = ph[tg]
            ok = np.ones(len(tg), bool)
            ext_lo = (phi < Y / 64.0) if Y > 0 else np.zeros(len(tg), bool)          # yield mask: also the leading / trailing drop
            ext_hi = (phi >= 1.0 - Y / 64.0) if Y > 0 else np.zeros(len(tg), bool)
            k_lo = k - ext_lo.astype(np.int64)
            k_hi = k + ext_hi.astype(np.int64)
            if P > 0:
                # purity mask: the deflection envelope (1 or 2 drops) must be free of non-target particles, and none may lie within P/2 (1/32 drop)
                # outside its edges
                la, ta = edges(P, np.ones(len(ks), bool))
                for l_ in range(nloc):
                    sel = L == l_
                    if not sel.any():
                        continue
                    ll, tl = edges(P, ev_cls == l_)
                    other = np.concatenate([[0], np.cumsum(cnt_all - cnt_l[l_])])
                    lo, hi = k_lo[sel], k_hi[sel]
                    inside = (other[hi + 1] - other[lo]) > 0
                    bad = inside | ((ta[lo - 1] - tl[lo - 1]) > 0) | ((la[hi + 1] - ll[hi + 1]) > 0)
                    ok[sel] &= ~bad
            if Ph > 0:
                la, ta = edges(Ph, np.ones(len(ks), bool))
                ok &= ~((phi < Ph / 64.0) | (phi >= 1.0 - Ph / 64.0) | (ta[k - 1] > 0) | (la[k + 1] > 0))
            if single:
                for l_ in range(nloc):
                    sel = L == l_
                    ok[sel] &= cnt_l[l_][k[sel]] < 2
            conflicts += int(np.count_nonzero(~ok))
            for l_, loc in enumerate(locs):
                sr['conflicts_by_location'][loc] = sr['conflicts_by_location'].get(loc, 0) + int(np.count_nonzero(~ok & (L == l_)))
            acc = tg[ok]
            if dv['kind'] == 'tubes':
                keep = np.zeros(len(acc), bool)
                for l_, loc in enumerate(locs):
                    sel = np.nonzero(ev_cls[acc] == l_)[0]
                    tgt = lay['target_events'].get(loc)
                    room = len(sel) if tgt is None else max(0, tgt - sr['counts'][loc])
                    keep[sel[:room]] = True
                acc = acc[keep]
                req_k, req_l = [idx[acc]], [ev_cls[acc]]
                if Y > 0:
                    lo = acc[ph[acc] < Y / 64.0]
                    hi = acc[ph[acc] >= 1.0 - Y / 64.0]
                    req_k += [idx[lo] - 1, idx[hi] + 1]
                    req_l += [ev_cls[lo], ev_cls[hi]]
                rk = np.concatenate(req_k)
                rl = np.concatenate(req_l)
                first = np.full(size, -1, np.int64)
                multi = np.zeros(size, bool)
                for kk_, ll_ in zip(rk, rl):
                    if first[kk_] == -1:
                        first[kk_] = ll_
                    elif first[kk_] != ll_:
                        multi[kk_] = True
                charged = np.where(multi, -1, first)
                main_ok = charged[idx[acc]] == ev_cls[acc]
                conflicts += int(np.count_nonzero(~main_ok))
                for l_, loc in enumerate(locs):
                    sr['conflicts_by_location'][loc] += int(np.count_nonzero(~main_ok & (ev_cls[acc] == l_)))
                for l_, loc in enumerate(locs):
                    n = int(np.count_nonzero(main_ok & (ev_cls[acc] == l_)))
                    sr['counts'][loc] += n
                    sorted_now[l_] += n
            else:
                ppw = lay['events_per_well']
                for e in acc:
                    if sr['well_i'] >= len(sr['wells']):
                        break
                    if ev_t[e] < sr['move_until']:
                        continue
                    charged[idx[e]] = 0
                    drop_well[idx[e]] = sr['well_i']
                    if Y > 0:                                                    # yield mask: the leading / trailing drop goes to the same well
                        for kk_ in ((idx[e] - 1) if ph[e] < Y / 64.0 else -1, (idx[e] + 1) if ph[e] >= 1.0 - Y / 64.0 else -1):
                            if 0 <= kk_ < size and charged[kk_] < 0:
                                charged[kk_] = 0
                                drop_well[kk_] = sr['well_i']
                    sr['well_n'] += 1
                    sr['counts']['plate'] += 1
                    sorted_now[0] += 1
                    if sr['well_n'] >= ppw:
                        sr['well_i'] += 1
                        sr['well_n'] = 0
                        sr['move_until'] = float(ev_t[e]) + dv['move_s']
        # fate of the particles
        valid = (kt_u >= k0) & (kt_u < k0 + size)
        dest = np.full(len(ut), -1, np.int64)
        dest[valid] = charged[kt_u[valid] - k0]
        cells = sig['cells']
        coll = self.collection
        pcap = np.zeros(nloc)
        for l_, loc in enumerate(locs):
            stream = dv['locations'][loc][0] if dv['kind'] == 'tubes' else dv['stream']
            x, sg = self._stream_x(stream, bo)
            if x is None:
                continue
            xt = dv['locations'][loc][1] if dv['kind'] == 'tubes' else dv['x']
            pcap[l_] = _phi((xt + dv['capture_r'] - x) / sg) - _phi((xt - dv['capture_r'] - x) / sg)
        u = rng.random(len(ut))
        n_drops = np.bincount(charged[charged >= 0], minlength=nloc) if (charged >= 0).any() else np.zeros(nloc, np.int64)
        self.led['drops_deflected'].add(float(n_drops.sum()))
        to_col = np.zeros(len(ut), bool)
        for l_, loc in enumerate(locs):
            sel = dest == l_
            got = sel & (u < pcap[l_]) & drawer
            to_col |= got
            self.led['particles_lost_off_target'].add(float(cells[sel & ~got].sum()))
            if coll is None or not drawer:
                continue
            for li in np.nonzero(got)[0]:
                tl = loc if dv['kind'] == 'tubes' else sr['wells'][drop_well[kt_u[li] - k0]]
                c = coll['locs'][tl]
                key = sig['keys'][sig['lab'][li]]
                c['counts'][key] = c['counts'].get(key, 0.0) + float(cells[li])
            if dv['kind'] == 'tubes':
                coll['locs'][loc]['volume_m3'] += float(n_drops[l_]) * bo['drop_m3'] * pcap[l_]
            else:
                for kk_ in np.nonzero(drop_well >= 0)[0]:
                    coll['locs'][sr['wells'][drop_well[kk_]]]['volume_m3'] += bo['drop_m3'] * pcap[l_]
        self.led['particles_sorted'].add(float(cells[to_col].sum()))
        vol_col = float(sum(n_drops[l_] * bo['drop_m3'] * pcap[l_] for l_ in range(nloc))) if drawer else 0.0
        if coll is not None:
            self._spill(coll, _rng(self.plant['noise_seed'], 37, self.tick))
        centre = dest < 0
        if coll is not None and drawer and 'clog_partial' in self.faults and dv['kind'] == 'tubes':
            xc, sc = self._centre_x()
            best = min(locs, key=lambda q: abs(dv['locations'][q][1] - xc))
            xt = dv['locations'][best][1]
            pc = _phi((xt + dv['capture_r'] - xc) / sc) - _phi((xt - dv['capture_r'] - xc) / sc)
            leak = centre & (rng.random(len(ut)) < pc)
            c = coll['locs'][best]
            for li in np.nonzero(leak)[0]:
                key = sig['keys'][sig['lab'][li]]
                c['counts'][key] = c['counts'].get(key, 0.0) + float(cells[li])
            c['volume_m3'] += pc * bo['q'] * DT
            vol_col += pc * bo['q'] * DT
            self.led['particles_leaked_into_collection'].add(float(cells[leak].sum()))
            centre &= ~leak
        self.led['particles_to_waste'].add(float(cells[centre].sum()))
        accu = sig['accu']
        left_i = locs.index('left') if 'left' in locs else (0 if dv['kind'] == 'plate' else None)
        n_left = int(np.count_nonzero(accu & (dest == left_i))) if left_i is not None else 0
        n_cen = int(np.count_nonzero(accu & (dest < 0)))
        return sorted_now, conflicts, n_left, n_cen, vol_col

    def _spill(self, coll, rng):
        """A collection tube / well that overfills spills the excess liquid; every particle leaves with the spilled share (binomial draw).  Truth only: the
        instrument has no level sensor, so the overflow is an evaluator event (once per location)."""
        for loc, c in coll['locs'].items():
            if c['volume_m3'] > coll['capacity_m3']:
                frac = 1.0 - coll['capacity_m3'] / c['volume_m3']
                lost = 0
                for k_ in sorted(c['counts']):
                    n0 = int(round(c['counts'][k_]))
                    m = int(rng.binomial(n0, frac)) if n0 > 0 else 0
                    c['counts'][k_] = float(n0 - m)
                    lost += m
                self.led['particles_spilled_from_collection'].add(float(lost))
                self.led['collection_spilled_m3'].add(c['volume_m3'] - coll['capacity_m3'])
                c['volume_m3'] = coll['capacity_m3']
                if not c.get('overflow_reported'):
                    c['overflow_reported'] = True
                    self._event('truth', 'COLLECTION_OVERFLOW', f'收集位置{loc}溢出（真值；仪器没有液位传感）')

    def _acquire_tick(self, bo, j):
        """Sample flow, particle signals, thresholds, pile-up, aborts, live data, records, sorting, Accudrop camera."""
        stats = dict(det=0, proc=0, abort=0, sorted=0, al=0, ac=0)
        tid = self.port['tube']
        if tid is None or self.port['state'] != 'up' or not self.acq_on or not self.stream or 'clog_full' in self.faults or self.sheath_p < 0.5 * self.sheath_set:
            return stats, 0.0
        tb = self.tubes[tid]
        q_s = self._flow_m3_s() * (1.0 - math.exp(-(self.t - self.port['t_up']) / 1.0))
        rng = _rng(self.plant['noise_seed'], 1, self.tick, _h(tid))
        draws, dv = self._tube_draw(tb, q_s, rng)
        if draws is None:
            return stats, 0.0
        if not draws:
            sig = None
        else:
            sig = self._signals(draws, rng, j, self.t - DT)
        if sig is None:
            if self.sortrun is not None and self.sortrun['state'] == 'sorting':
                self.sortrun['elapsed_s'] += DT
            return stats, dv
        cells = sig['cells']
        self.led['particles_drawn'].add(float(cells.sum()))
        det = self._threshold(sig['vals'])
        det_idx = np.nonzero(det)[0]
        self.led['particles_detected'].add(float(cells[det_idx].sum()))
        ev_vals, ev_t, ev_first, gid = self._merge(sig, det_idx)
        unit_event = np.full(len(cells), -1, np.int64)
        unit_event[det_idx] = gid
        n_ev = len(ev_t)
        cap_n = int(self._rate_limit() * DT)
        aborted = np.zeros(n_ev, bool)
        if n_ev > cap_n:
            aborted[rng.choice(n_ev, n_ev - cap_n, replace=False)] = True
        proc = ~aborted
        stats.update(det=n_ev, proc=int(proc.sum()), abort=int(aborted.sum()))
        self.led['events_processed'].add(float(proc.sum()))
        self.led['electronic_aborts'].add(float(aborted.sum()))
        vals = ev_vals[proc].astype(np.float32)
        en = set(self.settings['detectors_enabled'])
        for jd, d in enumerate(DETS):
            if d not in en:
                vals[:, 3 * jd:3 * jd + 3] = 0.0
        self.events_processed += len(vals)
        self._live_append(vals)
        labs = sig['lab'][ev_first[proc]] if len(ev_first) else np.zeros(0, np.int32)
        for op in self._running_ops('acquisition.record'):
            self._record_append(op, vals, labs, sig['keys'])
        sr = self.sortrun
        if sr is not None and sr['state'] in ('sorting', 'paused') and self._running_ops('sort.start') and bo is not None:
            ev_cls = np.full(n_ev, -2, np.int64)
            ev_cls[proc] = self._classify(vals.astype(np.float64))
            s_now, confl, al, ac, vol_col = self._sort_tick(sig, ev_t, ev_first, unit_event, ev_cls, bo, rng)
            sr['conflicts'] += confl
            stats.update(sorted=int(s_now.sum()), al=al, ac=ac)
            if sr['state'] == 'sorting':
                sr['elapsed_s'] += DT
            dv += 0.0
            self._col_volume = vol_col
        else:
            self.led['particles_to_waste'].add(float(cells.sum()))
            if self.defl['optical_filter_in']:
                na = int(np.count_nonzero(sig['accu']))
                nl = 0
                run = self._running_ops('sort.auto_drop_delay')
                if run and na and self.defl['voltage_on'] and self.defl['plate_voltage_v'] > 0 and bo is not None:   # the automatic run deflects the
                    mode, dd = run[0]['steps'][run[0]['step_i']]                                                 # bead drops of its scan point
                    p = _deflected_fraction(bo['dd_true'] - dd, PRECISION[mode][0], self._dd_sigma(bo))
                    nl = int(_rng(self.plant['noise_seed'], 29, self.tick).binomial(na, min(1.0, max(0.0, p))))
                stats.update(al=nl, ac=na - nl)
        return stats, dv

    def _live_append(self, vals):
        n = len(vals)
        if n == 0:
            return
        if n >= LIVE_BUFFER:
            self.live[:] = vals[-LIVE_BUFFER:]
            self.live_pos = 0
            self.live_n = LIVE_BUFFER
            return
        end = self.live_pos + n
        if end <= LIVE_BUFFER:
            self.live[self.live_pos:end] = vals
        else:
            k = LIVE_BUFFER - self.live_pos
            self.live[self.live_pos:] = vals[:k]
            self.live[:n - k] = vals[k:]
        self.live_pos = end % LIVE_BUFFER
        self.live_n = min(LIVE_BUFFER, self.live_n + n)

    def _record_append(self, op, vals, labs, keys):
        r = self.records[op['record_id']]
        if len(vals) == 0:
            return
        X = vals.astype(np.float64)
        Xc = self._compensate(X, r['S'], r['comp_on'])
        cache = {}
        keep = self._gate_mask(op['storage_gate'], Xc, cache) if op['storage_gate'] else np.ones(len(X), bool)
        stop_m = self._gate_mask(op['stopping_gate'], Xc, cache) if op['stopping_gate'] else np.ones(len(X), bool)
        cnt = np.cumsum(stop_m & keep)
        need = op['target'] - r['stop_count']
        if cnt.size and cnt[-1] >= need:
            last = int(np.searchsorted(cnt, need)) + 1
        else:
            last = len(X)
        sel = np.nonzero(keep[:last])[0]
        room = len(r['data']) - r['n']
        sel = sel[:room]
        r['data'][r['n']:r['n'] + len(sel)] = vals[sel]
        r['labels'][r['n']:r['n'] + len(sel)] = [_h(keys[x]) for x in labs[sel]] if len(sel) else []
        r['n'] += len(sel)
        r['stop_count'] += int(np.count_nonzero(stop_m[:last][keep[:last]][:len(sel)])) if len(sel) else 0
        if r['stop_count'] >= op['target'] or r['n'] >= len(r['data']):
            self._finish_record(op)

    # ---------------------------------------------------------------- physics tick
    def step(self, dt_s):
        """Advance the host clock: physics on the fixed 0.1 s grid (k * 0.1 s); state between grid points is held, so any split of the same interval
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

    def _physics_tick(self):
        pl = self.plant
        self.T_room += pl['room_drift_k_per_s'] * DT
        warm = SHEATH_WARM_K if self.stream else 0.0
        if 'breakoff_drift' in self.faults:
            warm += 2.0 * (1.0 - math.exp(-(self.t - self.fault_t.get('breakoff_drift', self.t)) / 600.0))
        self.T_sheath += (self.T_room + warm - self.T_sheath) * DT / SHEATH_WARM_TAU_S
        closed = self.port['state'] == 'up'
        sp = self.sample_sp if (self.power and self.sample_sp is not None) else None
        tgt = sp if sp is not None else self.T_room
        if sp is not None and not closed:
            tgt = self.T_room + 0.4 * (sp - self.T_room)                            # open chamber cannot hold the setpoint (engineering)
        self.T_chamber += (tgt - self.T_chamber) * DT / (SAMPLE_CHAMBER_TAU_S if closed else 2 * SAMPLE_CHAMBER_TAU_S)
        ct = self.col_sp if (self.power and self.col_sp is not None) else self.T_room
        self.T_col += (ct - self.T_col) * DT / COLLECTION_TAU_S
        for tid, tb in self.tubes.items():
            tt = self.T_chamber if (tb['location'] == 'port' and closed) else self.T_room
            tb['T'] += (tt - tb['T']) * DT / TUBE_TAU_S
            r_ag = AGITATION[self.agitation] / AGITATION['300_rpm'] if (tb['location'] == 'port' and closed and self.power) else 0.0
            for k in tb['settle']:                                                 # Stokes settling front; agitation only slows it (facility guide)
                sp_ = self.popspec[k]
                drho = sp_['density_kg_m3'] - water_density(tb['T'])
                v_s = max(0.0, 2.0 / 9.0 * drho * 9.80665 * (sp_['diameter_m'] / 2) ** 2 / water_viscosity(tb['T']))
                tb['settle'][k] += v_s * (1.0 - AGITATION_EFFECT * r_ag) * DT
        # power, energy
        if self.power:
            pw = BASE_POWER_W + sum(LASER_POWER_W[L] for L in LASERS if self.lasers[L]['on']) + (SAMPLE_TC_POWER_W if self.sample_sp is not None else 0.0)
            self.led['electrical_energy_j'].add(pw * DT)
            self.led['laser_on_time_s'].add(DT * sum(1 for L in LASERS if self.lasers[L]['on']))
        air_max = (40.0 * PSI if 'cart_air_leak' in self.faults else CART_AIR_PA) if self.power else 0.0
        tau = CART_AIR_TAU_S[0] if air_max > self.cart['air'] else CART_AIR_TAU_S[1]
        self.cart['air'] += (air_max - self.cart['air']) * DT / tau
        p_tgt = min(self.sheath_set, max(0.0, self.cart['air'] - 5.0 * PSI)) if (self.stream and self.power) else 0.0
        self.sheath_p += (p_tgt - self.sheath_p) * min(1.0, DT / SHEATH_TAU_S)
        if not self.stream and self.sheath_p < 1.0:
            self.sheath_p = 0.0
        # motions
        if self.port['state'] == 'loading':
            self.port['pos'] = min(LOAD_TRAVEL, self.port['pos'] + PORT_SPEED * DT)
            if self.port['pos'] >= LOAD_TRAVEL - 1e-12:
                self.port.update(state='up', t_up=self.t)
        elif self.port['state'] == 'unloading':
            self.port['pos'] = max(0.0, self.port['pos'] - PORT_SPEED * DT)
            if self.port['pos'] <= 1e-12:
                self.port.update(state='down', pos=0.0)
        dt_ = DRAWER_TRAVEL if (self.drawer_open and self.power) else 0.0
        if self.drawer_pos != dt_:
            step_ = DRAWER_SPEED * DT
            self.drawer_pos = min(dt_, self.drawer_pos + step_) if dt_ > self.drawer_pos else max(dt_, self.drawer_pos - step_)
        # fluidics procedures
        for op in self._running_ops('fluidics.startup', 'fluidics.shutdown', 'cleaning.run'):
            if self.t >= op['ends_s'] - 1e-9:
                self._finish_fluidics_op(op)
        # breakoff, Sweet Spot, clog
        bo = self._breakoff()
        if self.stream and 'clog_full' in self.faults and self.sweet:
            self._clog_protect()
            bo = None
        if self.sweet:
            self.ss_paused = self._sweet_spot_tick(bo)
            bo = self._breakoff()
        else:
            self.ss_paused = False
        j = self._jet()
        # fluid balance of the running stream
        stats, dv = dict(det=0, proc=0, abort=0, sorted=0, al=0, ac=0), 0.0
        self._col_volume = 0.0
        if self.stream and self.power and j['q'] > 0:
            q_sheath = j['q']
            take = min(self.cart['sheath'], q_sheath * DT)
            self.cart['sheath'] -= take
            self.led['sheath_used_m3'].add(take)
            self.led['drops_formed'].add(self.bo['frequency_hz'] * DT if bo is not None else 0.0)
            stats, dv = self._acquire_tick(bo, j)
            if 'clog_full' in self.faults and not self.sweet and self.collection is not None and self.drawer_pos >= DRAWER_TRAVEL - 1e-6:
                spray = 0.005 * take                                                    # unprotected clog: the spraying stream wets the open tubes
                for c in self.collection['locs'].values():
                    c['volume_m3'] += spray / max(1, len(self.collection['locs']))
                self._col_volume += spray
                self._spill(self.collection, _rng(self.plant['noise_seed'], 38, self.tick))
            self.led['collection_added_m3'].add(self._col_volume)
            self._to_waste(max(0.0, take + dv - self._col_volume))
            if self.cart['sheath'] <= 1e-9:
                self._error('SHEATH_EMPTY', '鞘液罐空：液流停止')
                self._stream_stop('SHEATH_EMPTY')
        if self.cart['waste'] > fluidics()['reservoirs']['waste_m3']:
            self._error('WASTE_OVERFLOW', '废液罐溢出')
        self._warnings(bo)
        # asynchronous operations
        for op in self._running_ops('sort.auto_drop_delay'):
            self._auto_dd_tick(op, bo, stats)
        for op in self._running_ops('cst.performance_check'):
            if not (self.acq_on and self.port['state'] == 'up' and self.stream):
                self._fail_op(op, 'CONDITIONS_LOST')
            elif self.t >= op['ends_s'] - 1e-9:
                self._finish_cst(op)
        for op in self._running_ops('acquisition.record'):
            if op['ends_s'] is not None and self.t >= op['ends_s'] - 1e-9:
                self._finish_record(op)
        sr = self.sortrun
        if sr is not None:
            for op in self._running_ops('sort.start'):
                if op['op_id'] != sr['op_id']:
                    continue
                lay = sr['layout']
                if DEVICES[lay['device']]['kind'] == 'tubes':
                    done = bool(lay['target_events']) and all(sr['counts'][k] >= n for k, n in lay['target_events'].items())
                else:
                    done = sr['well_i'] >= len(sr['wells'])
                if done:
                    sr['state'] = 'completed'
                    op['phase'] = 'succeeded'
                    op['finished_s'] = self.t
                    op['result'] = dict(counts=dict(sr['counts']), conflicts=sr['conflicts'])
                    self.drawer_open = False
                    self._event('sort', 'SORT_COMPLETE', '分选完成：' + '，'.join(f'{k} {v}' for k, v in sr['counts'].items()), op['op_id'])
        # aerosol
        if self.stream and self.power:
            gen = 500.0 if 'clog_full' in self.faults or 'clog_partial' in self.faults else (50.0 if (sr is not None and sr['state'] == 'sorting') else 5.0)
            esc = 0.05 if self.doors['sort_chamber'] > DOOR_CLOSED_RAD else (1e-5 if self.amo['on'] and self.amo['mode'] == 'normal' else 1e-6 if self.amo['on'] else 1e-3)
            n = _rng(pl['noise_seed'], 31, self.tick).poisson(gen * DT * esc)
            self.led['aerosol_particles_escaped'].add(float(n))
        self.hist.append((stats['det'], stats['proc'], stats['abort'], stats['sorted'], stats['al'], stats['ac']))
        if len(self.hist) > 10:
            self.hist.pop(0)
        if self.collection is not None:
            tot = sum(sum(c['counts'].values()) for c in self.collection['locs'].values())
            self.led['particles_in_collection'].s = float(tot)
            self.led['particles_in_collection'].c = 0.0

    def _warnings(self, bo):
        w = {}
        res = fluidics()['reservoirs']
        low = self.cart['sheath'] < 0.1 * res['sheath_m3']
        full = self.cart['waste'] > 0.9 * res['waste_m3']
        if self.power and low:
            w['SHEATH_LOW'] = '鞘液罐液位低'
        if self.power and full:
            w['WASTE_FULL'] = '废液罐将满'
        if self.stream and (low or full):
            lw = self.level_warn
            if lw['shown_at'] is None and self.t >= lw['next_show']:
                lw['shown_at'] = self.t
                self._event('warning', 'LEVEL_WARNING', '液位警告：鞘液低或废液满（15 min未确认将自动关液流）')
            if lw['shown_at'] is not None and self.t - lw['shown_at'] >= 900.0 - 1e-9:
                self._stream_stop('LEVEL_WARNING_TIMEOUT')
                lw['shown_at'] = None
        elif not (low or full):
            self.level_warn.update(shown_at=None, next_show=0.0)
        if any(self.lasers[L]['on'] and not self._laser_warm(L) and f'laser_failure_{L}' not in self.faults for L in LASERS) and self.power:
            w['LASERS_WARMING'] = '激光器预热中（30 min）'
        for L in LASERS:
            if self.power and self.lasers[L]['on'] and f'laser_failure_{L}' in self.faults:
                self._error(f'LASER_FAILURE_{L.upper()}', f'{L}激光器无输出')
            elif f'LASER_FAILURE_{L.upper()}' in self.errors and f'laser_failure_{L}' not in self.faults:
                del self.errors[f'LASER_FAILURE_{L.upper()}']
        tid = self.port['tube']
        if tid is not None and self.port['state'] == 'up' and self.acq_on:
            tb = self.tubes[tid]
            if tb['volume_m3'] <= TUBE_TYPES[tb['type']]['dead_m3'] + 1e-15:
                w['SAMPLE_DRY'] = '样品管已空（只剩死体积）'
        if self.stream and self.stream_t_on is not None and self.t - self.stream_t_on > 10.0 and self.sheath_p < 0.9 * self.sheath_set:
            w['SHEATH_PRESSURE_LOW'] = '鞘液压力达不到设定（检查液流车气压）'
        if self.stream and self.stream_t_on is not None and self.t - self.stream_t_on > 5.0 and (bo is None or bo['drop1'] is None):
            w['STREAM_UNSTABLE'] = '断点图像异常（不成滴或断点不在图像内）'
        if self.sortrun is not None and self.sortrun['state'] == 'sorting' and self.flow_setting > fluidics()['sort_flow_rate_max_setting']:
            w['FLOW_RATE_HIGH_FOR_SORT'] = '分选时流速档高于8'
        if self.T_room is not None and not (ROOM_RANGE_K[0] <= self.T_room <= ROOM_RANGE_K[1]):
            w['ROOM_TEMPERATURE_OUT_OF_RANGE'] = '室温超出17.5–27.5 °C运行范围'
        for code, msg in w.items():
            if code not in self.warnings:
                self._event('warning', code, msg)
        self.warnings = w

    # ---------------------------------------------------------------- state read
    def read(self, visibility='agent'):
        on = self.power
        bo = self._breakoff() if on else None
        j = self._jet()
        sr = self.sortrun
        h10 = self.hist[-10:]
        rate = lambda i: float(sum(h[i] for h in h10) / (len(h10) * DT)) if h10 else 0.0       # noqa: E731
        accu = None
        if on and self.defl['optical_filter_in'] and h10:
            nl, nc = sum(h[4] for h in h10), sum(h[5] for h in h10)
            accu = (nl / (nl + nc)) if (nl + nc) > 0 else None
        res = fluidics()['reservoirs']
        sx = {}
        for s in STREAMS:
            x, _ = self._stream_x(s, bo) if (on and bo is not None) else (None, None)
            sx[s] = x
        ops = sorted({op['kind'] for op in self._running_ops()})
        eff = None
        if sr is not None:
            tot = sum(sr['counts'].values()) + sr['conflicts']
            eff = (sum(sr['counts'].values()) / tot) if tot > 0 else None
        P = {L: self._laser_power(L)[0] * optics()['lasers'][L]['min_power_w'] * 1.15 for L in LASERS}
        v = {
            'clock.time_s': self.t, 'plant.configured': self.plant is not None, 'power.enabled': on,
            'lasers.on': {L: bool(on and self.lasers[L]['on']) for L in LASERS}, 'lasers.warm': {L: self._laser_warm(L) for L in LASERS},
            'lasers.power_w': {L: float(P[L]) for L in LASERS}, 'lasers.shutter_closed': self._shutter(),
            **{f'door.{d}_angle_rad': float(self.doors[d]) for d in DOORS},
            'nozzle.installed': self.nozzle, 'fluidics.state': self.fluidics_state, 'sort_setup.preset': 'custom' if self.custom else self.preset,
            'sheath.pressure_setpoint_pa': float(self.sheath_set), 'sheath.pressure_pa': float(self.sheath_p) if on else 0.0, 'stream.on': bool(self.stream),
            'breakoff.amplitude_v': float(self.bo['amplitude_v']), 'breakoff.frequency_hz': float(self.bo['frequency_hz']), 'breakoff.attenuation': bool(self.bo['attenuation']),
            'breakoff.drop1_target_px': float(self.bo['drop1_target_px']), 'breakoff.gap_target_px': float(self.bo['gap_target_px']),
            'breakoff.drop1_px': (round(bo['drop1'], 1) if (bo is not None and bo['drop1'] is not None) else None),
            'breakoff.gap_px': (round(bo['gap'], 1) if (bo is not None and bo['gap'] is not None) else None),
            'breakoff.drop_spacing_px': (round(bo['S'], 1) if bo is not None else None),
            'sweet_spot.on': bool(self.sweet), 'sweet_spot.paused': bool(self.ss_paused),
            'deflection.voltage_on': bool(on and self.defl['voltage_on']), 'deflection.plate_voltage_v': float(self.defl['plate_voltage_v']),
            'deflection.side_stream_fraction': {s: float(self.defl['side_stream_fraction'][s]) for s in STREAMS},
            'deflection.voltage_center_fraction': float(self.defl['voltage_center_fraction']),
            'deflection.test_sort': bool(on and self.defl['test_sort']), 'deflection.optical_filter_in': bool(self.defl['optical_filter_in']),
            'drop_delay.drops': float(self.dd_set), 'sort.drawer_position_m': float(self.drawer_pos), 'sort.drawer_open': bool(self.drawer_open),
            'amo.on': bool(on and self.amo['on']), 'amo.mode': self.amo['mode'],
            'sample.agitation_rate_hz': float(AGITATION[self.agitation]), 'sample.temperature_setpoint_k': self.sample_sp,
            'sample.chamber_temperature_k': float(self.T_chamber if self.T_chamber is not None else 295.15), 'sample.chamber_light': bool(on and self.light),
            'sample.port_position_m': float(self.port['pos']), 'sample.port_state': self.port['state'], 'sample.tube_id': self.port['tube'],
            'sample.flow_rate_setting': float(self.flow_setting), 'sample.acquiring': bool(on and self.acq_on and self.port['state'] == 'up' and self.stream),
            'collection.device': self.collection['device'] if self.collection else None, 'collection.temperature_setpoint_k': self.col_sp,
            'collection.temperature_k': float(self.T_col if self.T_col is not None else 295.15),
            'tubes.on_bench': sorted(k for k, t in self.tubes.items() if t['location'] == 'bench'), 'samples.available': sorted(self.samples),
            'acquisition.threshold_rate_eps': rate(0), 'acquisition.processed_rate_eps': rate(1), 'acquisition.abort_rate_eps': rate(2),
            'acquisition.events_processed': int(self.events_processed),
            'cytometer.voltages': {d: float(self.settings['voltages'][d]) for d in DETS}, 'cytometer.thresholds': copy.deepcopy(self.settings['thresholds']),
            'cytometer.threshold_logic': self.settings['threshold_logic'], 'cytometer.laser_delays_s': dict(self.settings['laser_delays_s']),
            'cytometer.area_scaling': dict(self.settings['area_scaling']), 'cytometer.window_extension_s': float(self.settings['window_extension_s']),
            'cytometer.detectors_enabled': list(self.settings['detectors_enabled']), 'cytometer.max_event_rate_eps': float(self._rate_limit()),
            'compensation.enabled': bool(self.comp_on), 'compensation.pairs': self._pairs(), 'gates.defined': sorted(self.gates), 'records.count': len(self.records),
            'sort.layouts': sorted(self.layouts), 'sort.state': sr['state'] if sr is not None else 'idle', 'sort.layout_id': sr['layout_id'] if sr is not None else None,
            'sort.counts': dict(sr['counts']) if sr is not None else {}, 'sort.conflicts': int(sr['conflicts']) if sr is not None else 0, 'sort.efficiency': eff,
            'sort.sort_rate_eps': rate(3), 'sort.elapsed_s': float(sr['elapsed_s']) if sr is not None else 0.0,
            'sort.well': (sr['wells'][sr['well_i']] if (sr is not None and sr['wells'] and sr['well_i'] < len(sr['wells'])) else None),
            'accudrop.left_stream_fraction': accu, 'cst.status': self.cst_status,
            'cart.sheath_level_fraction': float(self.cart['sheath'] / res['sheath_m3']), 'cart.waste_level_fraction': float(min(1.0, self.cart['waste'] / res['waste_m3'])),
            'cart.cleaning_ok': all(x >= 0.2 * res['cleaning_m3'] for x in self.cart['cleaning'].values()),
            'cart.air_pressure_pa': float(self.cart['air']), 'cart.gauge_angle_rad': float(min(GAUGE_SWEEP, self.cart['air'] / PSI / GAUGE_FULL_SCALE_PSI * GAUGE_SWEEP)),
            'safety.estop_latched': bool(self.estop), 'safety.estop_position_m': ESTOP_TRAVEL if self.estop else 0.0,
            'lamp.deflection_plates': bool(on and self.defl['voltage_on']), 'lamp.chamber_light': bool(on and self.light),
            'lamp.estop_light': bool(on and (self.estop or self.port['state'] in ('loading', 'unloading'))),
            'operations.running': ops, 'operation.started_by_other_agent': False, 'warnings.active': sorted(self.warnings), 'errors.active': sorted(self.errors),
            'truth.sheath_temperature_k': float(self.T_sheath if self.T_sheath is not None else 295.15), 'truth.jet_speed_m_s': float(j['v']),
            'truth.breakoff_length_m': (float(bo['L_b']) if bo is not None else None), 'truth.drop_delay_drops': (float(bo['dd_true']) if bo is not None else None),
            'truth.laser_delays_s': self._true_laser_delays(), 'truth.area_scaling': self._true_asf(),
            'truth.core_radius_m': float(math.sqrt(self._flow_m3_s() / j['v_core'] / math.pi)) if j['v_core'] > 0 else 0.0,
            'truth.side_stream_x_m': sx, 'truth.cart_sheath_m3': float(self.cart['sheath']), 'truth.cart_waste_m3': float(self.cart['waste']),
            'truth.sample_temperature_k': (float(self.tubes[self.port['tube']]['T']) if self.port['tube'] else None), 'truth.faults': sorted(self.faults),
        }
        if visibility == 'agent':
            v = {k: x for k, x in v.items() if SLOTS[k]['visibility'] == 'agent'}
        return v

    def _cmd_sort_side_streams(self):
        self._need_power()
        bo = self._breakoff()
        streams = []
        if bo is not None and self.defl['voltage_on'] and (self.defl['test_sort'] or (self.sortrun is not None and self.sortrun['state'] == 'sorting')):
            dv = DEVICES[self.collection['device']] if self.collection else None
            for s in STREAMS:
                x, sg = self._stream_x(s, bo)
                tgt = None
                off = None
                if dv is not None:
                    if dv['kind'] == 'tubes':
                        for loc, (st, xt) in dv['locations'].items():
                            if st == s:
                                tgt, off = loc, x - xt
                    elif s == dv['stream']:
                        tgt, off = 'plate', x - dv['x']
                streams.append(dict(stream=s, x_m=x, spread_m=sg, target=tgt, offset_from_target_m=off))
        xc, _ = self._centre_x()
        if bo is not None:
            streams.append(dict(stream='centre', x_m=xc, spread_m=2.0e-4 if 'clog_partial' not in self.faults else 3.0e-3, target='waste_aspirator', offset_from_target_m=xc))
        return dict(streams=streams, centre_in_aspirator=bool(bo is not None and abs(xc) < ASPIRATOR_R))

    # ---------------------------------------------------------------- evaluator queries, faults, clock
    def _pop_truth(self, counts, volume):
        out = []
        for k in sorted(counts):
            sp = self.popspec[k]
            out.append(dict(population_id=k, particle_id=sp['particle_id'], particles=float(counts[k]),
                            concentration_per_m3=float(counts[k] / volume) if volume > 0 else 0.0))
        return out

    def _cmd_tube_inspect(self, tube_id):
        tb = self.tubes.get(tube_id)
        if tb is None:
            raise ActionRejected('UNKNOWN_ITEM')
        h = tb['volume_m3'] / (math.pi * TUBE_TYPES[tb['type']]['radius_m'] ** 2)
        return dict(tube_id=tube_id, volume_m3=float(tb['volume_m3']), temperature_k=float(tb['T']), populations=self._pop_truth(tb['counts'], tb['volume_m3']),
                    settled_depth_m={k: float(v) for k, v in sorted(tb['settle'].items())}, liquid_height_m=float(h))

    def _cmd_collection_inspect(self):
        c = self.collection
        if c is None:
            return dict(device=None, locations=[])
        return dict(device=c['device'], locations=[dict(location=k, volume_m3=float(x['volume_m3']), temperature_k=float(self.T_col),
                                                        populations=self._pop_truth(x['counts'], x['volume_m3'])) for k, x in c['locs'].items()
                                                   if x['volume_m3'] > 0 or x['counts']])

    def _cmd_events_read(self, since_s):
        return dict(events=[{k: v for k, v in e.items() if k != '_ref'} for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def _cmd_provenance_read(self):
        op = optics()
        return dict(libraries=[dict(name=k, file=v, entries=len(_lib(k).get('substances', _lib(k).get('presets', _lib(k).get('detectors', {}))))) for k, v in LIBS.items()],
                    detectors=[dict(detector=d, laser=op['detectors'][d]['laser'], bandpass_m=[(op['detectors'][d]['bandpass_nm'][0] - op['detectors'][d]['bandpass_nm'][1] / 2) * 1e-9,
                                                                                             (op['detectors'][d]['bandpass_nm'][0] + op['detectors'][d]['bandpass_nm'][1] / 2) * 1e-9],
                                    intended=op['detectors'][d]['intended']) for d in FL_DETS])

    def ledger(self):
        return {k: v.value() for k, v in self.led.items()}

    def _cmd_ledger_read(self):
        return dict(ledger=self.ledger())

    def _cmd_fault_inject(self, fault, active):
        self._need_plant()
        (self.faults.add if active else self.faults.discard)(fault)
        if active:
            self.fault_t[fault] = self.t
        if fault == 'power_loss':
            if active and self.power:
                self._power_off('mains')
            elif not active and self.switch_on and not self.power and self._mains_ok():
                self._power_up()
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
        if CAPS[capability_id]['kind'] not in ('query',) and capability_id != 'sim.step':
            self.revision += 1
        return out

    _NOCOPY = ('live', '_principals', '_requests')

    def _snapshot(self):
        """Atomic rejection: deep copy of the mutable state except the large live buffer and the session tables (commands that fail never touch them)."""
        d = {}
        for k, v in self.__dict__.items():
            if k in self._NOCOPY:
                continue
            if k == 'records':
                d[k] = {rid: dict(r) for rid, r in v.items()}
            else:
                d[k] = copy.deepcopy(v)
        return d

    def _restore(self, snap):
        for k, v in snap.items():
            self.__dict__[k] = v


# ---------------------------------------------------------------- session (agent / evaluator isolation, idempotency)
ASYNC = {'fluidics.startup', 'fluidics.shutdown', 'cleaning.run', 'acquisition.record', 'sort.start', 'sort.auto_drop_delay', 'cst.performance_check'}
_RECORD_QUERIES = {'statistics.read', 'record.read', 'data.export'}
_CONTROL_GUARDED = {k for k, v in CAPS.items() if v['visibility'] == 'agent' and v['kind'] in ('device_command', 'robot_skill') and k not in UNSUPPORTED
                    and k not in ('data.export', 'gate.define', 'safety.emergency_stop', 'safety.reset_emergency_stop')}   # the e-stop is a hardware button


class BdFacsariaiiiSession:
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
        meta = copy.deepcopy(export_bd_facsariaiii_contract_metadata())
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
        return any(self._foreign(op['op_id']) for op in h._running_ops())

    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id)
        h = self._host
        values = h.read(self._role)
        values['operation.started_by_other_agent'] = self._role == 'agent' and self._foreign_op()
        if self._role == 'agent':
            values['tubes.on_bench'] = [x for x in values['tubes.on_bench'] if not self._foreign('tube:' + x)]
            values['samples.available'] = [x for x in values['samples.available'] if not self._foreign('sample:' + x)]
        ids = list(values) if state_ids is None else state_ids
        if not isinstance(ids, list) or any(not isinstance(k, str) for k in ids):
            raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in values for k in ids):
            raise ActionRejected('ACCESS_DENIED')
        n_h = min(10, len(h.hist))                                                   # counted rates: the last (up to) 10 grid ticks
        win = [round((h.tick - n_h) * DT, 9), round(h.tick * DT, 9)] if n_h else None
        return dict(instance_id=instance_id, status='succeeded', state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s},
                    observations={k: dict(value=copy.deepcopy(values[k]), unit=SLOTS[k]['unit'],
                                          quality='unavailable' if values[k] is None else 'estimated' if SLOTS[k]['origin'] in ('sensor', 'derived') else 'valid',
                                          sample_time_s=h.time_s,
                                          sample_window_s=win if (k.startswith(('acquisition.', 'accudrop.')) and k != 'acquisition.events_processed')
                                          or k == 'sort.sort_rate_eps' else None)
                                  for k in ids})

    def _guard(self, capability_id, arguments):
        if self._role != 'agent':
            return
        h = self._host
        if capability_id in _CONTROL_GUARDED and self._foreign_op():
            if not h.power and ((capability_id == 'power.set' and arguments.get('enabled') is True) or (capability_id == 'power.press_switch' and not h.switch_on)):
                return
            raise ActionRejected('ACCESS_DENIED')
        if capability_id in ('tube.install', 'tube.mix') and self._foreign('tube:' + arguments['tube_id']):
            raise ActionRejected('ACCESS_DENIED')
        if capability_id in ('tube.remove', 'sample.load', 'sample.unload', 'acquisition.set') and h.port['tube'] is not None and self._foreign('tube:' + h.port['tube']):
            raise ActionRejected('ACCESS_DENIED')
        if capability_id == 'tube.prepare' and any('sample_id' in s and self._foreign('sample:' + s['sample_id']) for s in arguments['sources']):
            raise ActionRejected('ACCESS_DENIED')
        if capability_id in _RECORD_QUERIES:
            rid = arguments.get('record_id', arguments.get('source'))
            if rid not in (None, 'live') and self._foreign('record:' + rid):
                raise ActionRejected('ACCESS_DENIED')
        if capability_id == 'compensation.calculate' and any(self._foreign('record:' + r) for r in [arguments['unstained_record']] + list(arguments['controls'].values())):
            raise ActionRejected('ACCESS_DENIED')
        if capability_id in ('sort.start', 'sort.layout') and self._foreign('layout:' + arguments['layout_id']):
            raise ActionRejected('ACCESS_DENIED')
        if capability_id == 'gate.delete' and self._foreign('gate:' + arguments['gate_id']):
            raise ActionRejected('ACCESS_DENIED')
        if capability_id == 'collection.remove' and h.collection is not None and self._foreign('collection'):
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
            if capability_id == 'sample.provide':
                h._owners['sample:' + arguments['sample_id']] = self._principal
            if self._role == 'agent':
                if capability_id == 'records.list':
                    result = dict(records=[r for r in result['records'] if not self._foreign('record:' + r['record_id'])])
                elif capability_id == 'events.read':
                    result = dict(events=[{k: v for k, v in e.items() if k != '_ref'} for e in h.events
                                          if e['time_s'] >= arguments['since_s'] and not any(self._foreign(p + str(e.get('_ref'))) for p in ('', 'tube:', 'record:', 'sample:'))
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
        if op['kind'] == 'sort.start' and h.sortrun is not None and h.sortrun['op_id'] == operation_id:
            res.update(state=h.sortrun['state'], counts=dict(h.sortrun['counts']), conflicts=int(h.sortrun['conflicts']),
                       conflicts_by_location=dict(h.sortrun['conflicts_by_location']))
        if op['kind'] == 'acquisition.record':
            res.update(record_id=op['record_id'], events=int(h.records[op['record_id']]['n']))
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
    'UNKNOWN_CAPABILITY': '能力不存在', 'UNSUPPORTED': '本配置不支持该能力', 'ACCESS_DENIED': '无权访问（真值、另一个agent的样品管、记录、布局、门或进行中的操作）',
    'PLANT_REQUIRED': '尚未声明实验室条件', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'MAINS_OUT_OF_RANGE': '市电不在100/115/230 VAC ±10 %（或断电）',
    'POWER_OFF': '仪器未通电', 'STREAM_ON': '液流开着（须先关液流）', 'STREAM_OFF': '液流没有开', 'DOOR_CLOSED': '须先打开对应的门', 'DOOR_OPEN': '须先关好分选收集室门',
    'FLUIDICS_BUSY': '液流程序进行中', 'FLUIDICS_ALREADY_STARTED': '液流系统已启动', 'FLUIDICS_NOT_READY': '液流系统未完成Fluidics Startup（或正在关机/清洗）',
    'SORT_NOZZLE_REQUIRED': '须装分选喷嘴（70/85/100/130 µm）', 'CLOSED_LOOP_NOZZLE_REQUIRED': '须装闭环喷嘴', 'SHEATH_LOW': '鞘液不足以启动',
    'WASTE_FULL': '废液罐将满', 'CLEANING_FLUID_LOW': '清洗液不足', 'SWEET_SPOT_ON': 'Sweet Spot开启时不能改振幅、频率、鞘液压力或Sort Setup',
    'SWEET_SPOT_OFF': '须先开启Sweet Spot', 'SORT_IN_PROGRESS': '分选、自动液滴延迟或CS&T进行中', 'EMERGENCY_STOP_LATCHED': '急停按钮未复位',
    'SHEATH_EMPTY': '鞘液罐空', 'NO_BREAKOFF': '没有稳定的断点图像（不成滴或断点不在图像内）', 'PLATES_OFF': '偏转板电压没开',
    'CONTAINER_OVERFLOW': '加入量超过容器容量', 'ITEM_EXISTS': 'ID已存在', 'TOO_MANY_ITEMS': '数量已达上限', 'DUPLICATE_POPULATION': '群体ID重复',
    'UNKNOWN_PARTICLE': '颗粒类型不在颗粒库中（或不是微球）', 'UNKNOWN_DYE': '荧光染料不在荧光库中', 'INVALID_SOURCE': '每个来源须给样品或微球之一；只有微球可加染色',
    'UNKNOWN_SAMPLE': '样品不存在', 'INSUFFICIENT_SAMPLE': '样品体积不够', 'VOLUME_OUT_OF_RANGE': '体积超过管或孔的容量', 'UNKNOWN_ITEM': '样品管不存在',
    'TUBE_NOT_ON_BENCH': '样品管不在实验台上', 'PORT_OCCUPIED': '进样口上已有样品管', 'PORT_NOT_DOWN': '进样口没有降下', 'NO_TUBE': '进样口上没有样品管',
    'NOT_LOADED': '样品管没有装载', 'ACQUISITION_IN_PROGRESS': '记录、分选或检查进行中不能改阈值与参数', 'INVALID_PARAMETERS': '启用的检测器须含FSC与SSC且不重复',
    'INVALID_THRESHOLD': '阈值参数须是启用的检测器且不重复', 'INVALID_SPILLOVER': '溢出矩阵无效（对角须为1、矩阵须可逆、检测器须为荧光检测器）',
    'UNKNOWN_RECORD': '记录不存在', 'TOO_FEW_EVENTS': '记录事件太少（<100）', 'NO_POSITIVE_POPULATION': '单染对照没有明显的阳性群',
    'UNKNOWN_GATE': '门不存在', 'INVALID_GATE': '门的参数不完整或范围无效', 'GATE_IN_USE': '门有子门、被分选布局或进行中的记录引用',
    'STORAGE_FULL': '记录存储已满', 'RECORDING_IN_PROGRESS': '已有记录进行中', 'NOT_ACQUIRING': '没有在采集（须装载样品管并采集）',
    'DEVICE_INSTALLED': '已装收集装置', 'NO_DEVICE': '没有收集装置', 'INVALID_LAYOUT': '布局与收集装置不一致（位置、目标数或孔）', 'UNKNOWN_LAYOUT': '布局不存在',
    'DEVICE_MISMATCH': '装着的收集装置与布局不一致', 'NOT_SORTING': '没有进行中的分选', 'NOT_PAUSED': '分选没有暂停',
    'ACCUDROP_BEADS_REQUIRED': '须装载并采集Accudrop微球', 'CST_BEADS_REQUIRED': '须装载并采集CS&T微球', 'UNKNOWN_OPERATION': '操作不存在',
    'NOT_RUNNING': '操作不在进行中', 'POWER_OFF_INTERRUPT': '断电中止', 'STREAM_OFF_INTERRUPT': '液流关闭中止', 'EMERGENCY_STOP': '急停', 'CONDITIONS_LOST': '运行条件丢失（液流、装载或偏转）',
    'CLOG': '喷嘴堵塞', 'WASTE_OVERFLOW': '废液罐溢出', 'SAMPLE_UNLOADED': '样品管已卸下（进行中的分选与记录结束）',
    **{f'LASER_FAILURE_{L.upper()}': f'{L}激光器无输出' for L in LASERS},
}
_OPS = ['operations.running', 'warnings.active', 'errors.active']
_LAMPS = ['lamp.deflection_plates', 'lamp.chamber_light', 'lamp.estop_light']
_BO = ['breakoff.drop1_px', 'breakoff.gap_px', 'breakoff.drop_spacing_px', 'breakoff.amplitude_v', 'sweet_spot.paused', 'truth.breakoff_length_m', 'truth.drop_delay_drops',
       'truth.jet_speed_m_s', 'truth.laser_delays_s', 'truth.area_scaling', 'truth.core_radius_m', 'truth.side_stream_x_m']
_STREAM = ['stream.on', 'sheath.pressure_pa', 'sweet_spot.on'] + _BO
_ACQ = ['acquisition.threshold_rate_eps', 'acquisition.processed_rate_eps', 'acquisition.abort_rate_eps', 'acquisition.events_processed', 'accudrop.left_stream_fraction']
_SORT = ['sort.state', 'sort.layout_id', 'sort.counts', 'sort.conflicts', 'sort.efficiency', 'sort.sort_rate_eps', 'sort.elapsed_s', 'sort.well', 'sort.drawer_open',
         'sort.drawer_position_m']
_SAMPLE = ['sample.port_position_m', 'sample.port_state', 'sample.tube_id', 'sample.acquiring', 'tubes.on_bench', 'truth.sample_temperature_k']
_THERM = ['sample.chamber_temperature_k', 'collection.temperature_k', 'truth.sheath_temperature_k', 'truth.sample_temperature_k']
_CART = ['cart.sheath_level_fraction', 'cart.waste_level_fraction', 'cart.cleaning_ok', 'cart.air_pressure_pa', 'cart.gauge_angle_rad', 'truth.cart_sheath_m3',
         'truth.cart_waste_m3']
_PWR = ['power.enabled', 'lasers.on', 'lasers.warm', 'lasers.power_w', 'deflection.voltage_on', 'deflection.test_sort', 'sample.chamber_light', 'amo.on', 'fluidics.state',
        'sample.acquiring', 'sheath.pressure_pa', 'sort.drawer_open'] + _STREAM + _LAMPS + _OPS + _SORT + _ACQ + ['sample.port_state', 'sample.port_position_m']
_ALL_PHYS = sorted(set(_THERM + _CART + _STREAM + _ACQ + _SORT + _SAMPLE + _LAMPS + _OPS + ['lasers.warm', 'lasers.power_w', 'fluidics.state', 'cst.status',
                                                                                         'deflection.optical_filter_in', 'drop_delay.drops', 'cytometer.laser_delays_s',
                                                                                         'cytometer.area_scaling', 'records.count', 'deflection.voltage_on',
                                                                                         'deflection.test_sort', 'collection.device', 'samples.available']))
EFFECTS = {
    'plant.configure': ['plant.configured'] + _THERM + _CART + _PWR, 'power.set': _PWR, 'power.press_switch': _PWR,
    'lasers.set': ['lasers.on', 'lasers.warm', 'lasers.power_w', 'warnings.active', 'errors.active'],
    'door.set': [f'door.{d}_angle_rad' for d in DOORS] + ['lasers.shutter_closed'],
    'nozzle.install': ['nozzle.installed', 'errors.active', 'truth.faults'],
    'fluidics.startup': ['fluidics.state', 'operations.running'], 'fluidics.shutdown': ['fluidics.state', 'operations.running'],
    'cleaning.run': ['fluidics.state', 'operations.running'],
    'sort_setup.select': ['sort_setup.preset', 'sheath.pressure_setpoint_pa', 'breakoff.amplitude_v', 'breakoff.frequency_hz', 'breakoff.attenuation', 'drop_delay.drops',
                          'deflection.plate_voltage_v', 'deflection.side_stream_fraction', 'cytometer.laser_delays_s', 'cytometer.area_scaling',
                          'cytometer.window_extension_s', 'truth.area_scaling'],
    'stream.set': _STREAM + _SORT + _OPS + ['sample.acquiring', 'truth.faults'],
    'breakoff.set': ['breakoff.frequency_hz', 'breakoff.drop1_target_px', 'breakoff.gap_target_px', 'breakoff.attenuation'] + _BO,
    'sweet_spot.set': ['sweet_spot.on', 'sweet_spot.paused'],
    'sheath.set_pressure': ['sheath.pressure_setpoint_pa', 'sort_setup.preset', 'truth.jet_speed_m_s', 'truth.laser_delays_s', 'truth.area_scaling', 'truth.core_radius_m'] + _BO,
    'deflection.set': ['deflection.voltage_on', 'deflection.plate_voltage_v', 'deflection.side_stream_fraction', 'deflection.voltage_center_fraction', 'deflection.test_sort',
                       'deflection.optical_filter_in', 'lamp.deflection_plates', 'truth.side_stream_x_m', 'accudrop.left_stream_fraction'],
    'drop_delay.set': ['drop_delay.drops'], 'drawer.set': ['sort.drawer_open'], 'amo.set': ['amo.on', 'amo.mode'],
    'sample.agitation': ['sample.agitation_rate_hz'], 'sample.temperature': ['sample.temperature_setpoint_k'], 'collection.temperature': ['collection.temperature_setpoint_k'],
    'sample.chamber_light': ['sample.chamber_light', 'lamp.chamber_light'], 'tube.prepare': ['tubes.on_bench'], 'tube.mix': [],
    'tube.install': ['tubes.on_bench', 'sample.tube_id', 'truth.sample_temperature_k'], 'tube.remove': ['tubes.on_bench', 'sample.tube_id', 'truth.sample_temperature_k'],
    'sample.load': ['sample.port_state', 'sample.acquiring', 'acquisition.events_processed', 'lamp.estop_light'],
    'sample.unload': ['sample.port_state', 'sample.acquiring', 'lamp.estop_light', 'sort.state', 'sort.drawer_open', 'operations.running'],
    'acquisition.set': ['sample.acquiring'],
    'acquisition.flow_rate': ['sample.flow_rate_setting', 'truth.core_radius_m'],
    'cytometer.settings': ['cytometer.voltages', 'cytometer.thresholds', 'cytometer.threshold_logic', 'cytometer.laser_delays_s', 'cytometer.area_scaling',
                           'cytometer.window_extension_s', 'cytometer.detectors_enabled', 'cytometer.max_event_rate_eps'],
    'compensation.set': ['compensation.enabled', 'compensation.pairs', 'cytometer.max_event_rate_eps'],
    'compensation.calculate': ['compensation.enabled', 'compensation.pairs', 'cytometer.max_event_rate_eps'],
    'gate.define': ['gates.defined'], 'gate.delete': ['gates.defined'], 'acquisition.record': ['records.count', 'operations.running'],
    'collection.install': ['collection.device'], 'collection.remove': ['collection.device', 'samples.available'], 'sort.layout': ['sort.layouts'],
    'sort.start': _SORT + ['operations.running'], 'sort.pause': ['sort.state'], 'sort.resume': ['sort.state'],
    'sort.auto_drop_delay': ['operations.running', 'deflection.optical_filter_in', 'accudrop.left_stream_fraction'], 'cst.performance_check': ['operations.running'],
    'safety.emergency_stop': ['safety.estop_latched', 'safety.estop_position_m', 'sample.port_state', 'sample.acquiring', 'lamp.estop_light', 'lamp.deflection_plates',
                              'deflection.voltage_on', 'deflection.test_sort', 'accudrop.left_stream_fraction'] + _SORT + _STREAM + _OPS,
    'safety.reset_emergency_stop': ['safety.estop_latched', 'safety.estop_position_m', 'lamp.estop_light'], 'warnings.dismiss': [],
    'cart.refill_sheath': ['cart.sheath_level_fraction', 'truth.cart_sheath_m3', 'errors.active'],
    'cart.empty_waste': ['cart.waste_level_fraction', 'truth.cart_waste_m3', 'errors.active'],
    'cart.refill_cleaning': ['cart.cleaning_ok'], 'data.export': [], 'sample.provide': ['samples.available'],
    'fault.inject': ['truth.faults'] + _PWR + ['errors.active'], 'sim.step': ['clock.time_s'],
}
# Later (on-step) effects per capability: the slots whose evolution differs from the same host without the call (checked by the with / without-call
# discovery in the tests).  Pure bookkeeping and configuration calls that act only through a later call have none.
_AIR = ['cart.air_pressure_pa', 'cart.gauge_angle_rad']
_LEVELS = ['cart.sheath_level_fraction', 'cart.waste_level_fraction', 'truth.cart_sheath_m3', 'truth.cart_waste_m3', 'cart.cleaning_ok']
_WASTE = ['cart.waste_level_fraction', 'truth.cart_waste_m3']
_PORTMOVE = ['sample.port_position_m', 'sample.port_state', 'sample.acquiring', 'lamp.estop_light']
_PROTECT = ['deflection.voltage_on', 'deflection.test_sort', 'lamp.deflection_plates', 'sort.drawer_open', 'sort.drawer_position_m'] + _PORTMOVE   # clog protection
_DATA = _ACQ + _SORT + _OPS + _WASTE            # event rates, sort decisions and counts, operations that end on counts or conditions, and the
                                                # waste level (deflected drops are collected instead of reaching the waste)
_STREAM_ALL = _STREAM + ['truth.sheath_temperature_k'] + _LEVELS + _DATA + _PROTECT
_u = lambda *groups: sorted(set(x for g in groups for x in g))                    # noqa: E731
ON_STEP = {k: _ALL_PHYS for k in ('plant.configure', 'power.set', 'power.press_switch', 'fault.inject', 'sim.step')}
ON_STEP.update({
    'lasers.set': _u(['lasers.warm', 'lasers.power_w', 'cst.status', 'cytometer.laser_delays_s', 'cytometer.area_scaling'], _DATA),
    'door.set': _u(_DATA),
    'fluidics.startup': _u(['fluidics.state'], _LEVELS, _OPS), 'fluidics.shutdown': _u(['fluidics.state'], _LEVELS, _OPS),
    'cleaning.run': _u(['fluidics.state'], _LEVELS, _OPS),
    'stream.set': _u(_STREAM_ALL), 'sweet_spot.set': _u(_STREAM_ALL), 'warnings.dismiss': _u(_STREAM_ALL),
    'safety.emergency_stop': _u(_STREAM_ALL, ['sample.chamber_temperature_k', 'truth.sample_temperature_k']),
    'breakoff.set': _u(_BO, _DATA), 'sheath.set_pressure': _u(_STREAM, _LEVELS, _DATA),
    'deflection.set': _u(_DATA, _WASTE), 'drop_delay.set': _u(_DATA), 'drawer.set': _u(['sort.drawer_position_m'], _DATA, _WASTE),
    'sample.agitation': _u(_DATA), 'sample.temperature': _u(['sample.chamber_temperature_k', 'truth.sample_temperature_k'], _DATA),
    'collection.temperature': ['collection.temperature_k'],
    'sample.load': _u(_PORTMOVE, ['sample.chamber_temperature_k', 'truth.sample_temperature_k'], _DATA, _WASTE),
    'sample.unload': _u(_PORTMOVE, ['sample.chamber_temperature_k', 'truth.sample_temperature_k'], _DATA, _WASTE),
    'acquisition.set': _u(_DATA, _WASTE), 'acquisition.flow_rate': _u(_DATA, _WASTE),
    'cytometer.settings': _u(_DATA), 'compensation.set': _u(_DATA), 'compensation.calculate': _u(_DATA),
    'acquisition.record': ['operations.running'],
    'sort.start': _u(_DATA, _WASTE, ['sort.drawer_position_m']), 'sort.pause': _u(_DATA, _WASTE), 'sort.resume': _u(_DATA, _WASTE),
    'sort.auto_drop_delay': ['accudrop.left_stream_fraction', 'drop_delay.drops', 'operations.running'],
    'cst.performance_check': _u(['cst.status', 'cytometer.laser_delays_s', 'cytometer.area_scaling'], _DATA),
    'cart.refill_sheath': ['warnings.active'], 'cart.empty_waste': ['warnings.active'],
})
ON_STEP_ZH = {
    'plant.configure': '声明后所有过程按0.1 s网格演化（室温驱动各温度、鞘液温度与断点）', 'fault.inject': '故障作用于随后的各物理过程（断电、堵塞、气泡、激光、断点漂移、漏气）',
    'power.set': '通电后液流车气压升到约88 psi；激光开启后预热30 min；断电后温度、气压回落', 'power.press_switch': '同power.set',
    'lasers.set': '激光功率按预热时间常数上升；信号、事件率、分类与分选、记录进度与CS&T结果随之变化',
    'door.set': '比色池检修门打开时快门挡光：事件率、记录与分选随之变化（其它门只影响气溶胶账目）',
    'fluidics.startup': '约8 min后液流系统就绪，灌注鞘液进废液', 'fluidics.shutdown': '约3 min后完成关机清洗（去离子水与FACSClean进废液）',
    'cleaning.run': '清洗程序按时长完成，清洗液进废液', 'stream.set': '鞘液压力约2 s建立，喷嘴处鞘液约20 min升温1.5 K（断点漂移），鞘液消耗、废液增加；采集、分选与堵塞保护随液流',
    'breakoff.set': '断点（Drop 1、Gap、真实液滴延迟）随新的振幅、频率与目标演化（Sweet Spot开启时自动调振幅）；分选结果随之变化',
    'sweet_spot.set': '每0.1 s按Drop 1/Gap调节振幅；超差时暂停分选；喷嘴全堵时执行保护动作（关液流与偏转板、关抽屉、卸管）',
    'sheath.set_pressure': '鞘液压力约2 s到新设定：射流速度、断点、液滴延迟、芯流与激光延迟真值、鞘液消耗、事件率与分选随之变化',
    'deflection.set': '分选液滴的偏转与收集（计数、冲突、落点）、收集液量（少进废液）与Accudrop左侧流比例随之变化',
    'drop_delay.set': '分选液滴与颗粒实际所在液滴的对应（纯度、计数、冲突）与Accudrop比例随之变化',
    'drawer.set': '抽屉约1 s开合；关闭时不分选（偏转液滴落在抽屉上）', 'sample.agitation': '沉降前锋推进变慢（不会再悬浮），颗粒取不到的时刻推迟，事件率与分选随之变化',
    'sample.temperature': '进样室约45 min到温（须关闭），样品管跟随；温度改变沉降速度', 'collection.temperature': '收集管约10 min时间常数冷却/加热',
    'sample.load': '进样口约3 s升起，进样室加压后开始采集（事件率、记录、分选），样品进废液；管温跟随进样室', 'sample.unload': '进样口约3 s降下，事件率归零',
    'acquisition.set': '暂停/继续进样：事件率、记录、分选与样品消耗随之变化', 'acquisition.flow_rate': '样品流量改变：事件率、芯流宽度、分选与样品消耗随之变化',
    'cytometer.settings': '阈值、电压、延迟与启用参数改变事件率、处理与中止、门分类、记录进度与分选', 'compensation.set': '补偿对数改变处理能力（中止）与分选门分类',
    'compensation.calculate': '同compensation.set', 'acquisition.record': '按事件数（或停止门计数、时间上限）完成',
    'sort.start': '分选按掩码逐滴决定，抽屉约1 s打开，到目标数完成；收集液量少进废液', 'sort.pause': '暂停期间不分选、计数不变', 'sort.resume': '继续逐滴分选',
    'sort.auto_drop_delay': '粗扫7点与细扫13点每点5 s（约100 s），下摄像头看到的左侧流比例随扫描点变化，结束时设定液滴延迟',
    'cst.performance_check': '约5 min后给出结果并设定激光延迟与面积系数（之后的信号随之变化）',
    'safety.emergency_stop': '进样口约3 s降下、抽屉约1 s关闭、鞘液压力与断点消失、喷嘴处鞘液回落到室温、事件率归零',
    'warnings.dismiss': '确认后5 min内不再提示、不会在15 min后自动关液流（液流相关的演化因而不同）',
    'cart.refill_sheath': '鞘液低警告在下一个网格解除', 'cart.empty_waste': '废液满警告在下一个网格解除', 'sim.step': '0.1 s网格积分',
}
PRECONDITIONS = {
    'power.set': ('已声明实验室条件；开机时市电在100/115/230 VAC ±10 %', ['plant.configured']), 'power.press_switch': ('同power.set', ['plant.configured']),
    'lasers.set': ('通电', ['power.enabled']), 'door.set': ('已声明实验室条件；角度在门的范围内', ['plant.configured']),
    'nozzle.install': ('液流关闭；比色池检修门打开≥60°', ['stream.on', 'door.flow_cell_angle_rad']),
    'fluidics.startup': ('通电；液流系统处于关机状态且无液流程序在进行；装分选喷嘴；鞘液≥0.5 L；废液≤90 %', ['power.enabled', 'fluidics.state', 'nozzle.installed',
                                                                                                    'cart.sheath_level_fraction', 'cart.waste_level_fraction']),
    'fluidics.shutdown': ('通电；液流系统就绪；液流关闭；装闭环喷嘴；去离子水与FACSClean足够', ['power.enabled', 'fluidics.state', 'stream.on', 'nozzle.installed', 'cart.cleaning_ok']),
    'cleaning.run': ('通电；液流系统就绪；液流关闭；无菌分选准备须装闭环喷嘴；清洗液足够', ['power.enabled', 'fluidics.state', 'stream.on', 'nozzle.installed', 'cart.cleaning_ok']),
    'sort_setup.select': ('通电；液流关闭；Sweet Spot关闭；不在分选、自动液滴延迟或CS&T中', ['power.enabled', 'stream.on', 'sweet_spot.on', 'operations.running']),
    'sheath.set_pressure': ('通电；Sweet Spot关闭；不在分选中', ['power.enabled', 'sweet_spot.on', 'operations.running']),
    'stream.set': ('通电；开液流时：急停未按下、液流系统就绪、装分选喷嘴、鞘液不空', ['power.enabled', 'safety.estop_latched', 'fluidics.state', 'nozzle.installed',
                                                                                'cart.sheath_level_fraction']),
    'breakoff.set': ('通电；至少一个字段；Sweet Spot开启时不能改振幅与频率；分选中不能改频率', ['power.enabled', 'sweet_spot.on', 'operations.running']),
    'sweet_spot.set': ('通电；开启时液流开着且断点图像有效', ['power.enabled', 'stream.on', 'breakoff.drop1_px']),
    'deflection.set': ('通电；至少一个字段；急停未按下才能开电压；测试分选须电压开', ['power.enabled', 'safety.estop_latched', 'deflection.voltage_on']),
    'drop_delay.set': ('通电；不在自动液滴延迟中', ['power.enabled', 'operations.running']), 'drawer.set': ('通电；打开时急停未按下', ['power.enabled', 'safety.estop_latched']),
    'amo.set': ('通电；开启时分选收集室门关闭', ['power.enabled', 'door.sort_chamber_angle_rad']),
    'sample.agitation': ('通电', ['power.enabled']), 'sample.temperature': ('通电', ['power.enabled']), 'collection.temperature': ('通电', ['power.enabled']),
    'sample.chamber_light': ('通电', ['power.enabled']),
    'tube.prepare': ('已声明实验室条件；ID唯一、台上≤40管；每个来源为样品（体积足够）或库中微球（可加染色）；总体积不超过管容量', ['tubes.on_bench', 'samples.available']),
    'tube.install': ('样品管在实验台；进样口降下且空', ['tubes.on_bench', 'sample.port_state', 'sample.tube_id']),
    'tube.remove': ('进样口降下且有管', ['sample.port_state', 'sample.tube_id']),
    'sample.load': ('通电；急停未按下；液流开着；进样口降下且有管', ['power.enabled', 'safety.estop_latched', 'stream.on', 'sample.port_state', 'sample.tube_id']),
    'sample.unload': ('通电；已装载或正在装载', ['power.enabled', 'sample.port_state']), 'acquisition.set': ('通电；已装载或正在装载', ['power.enabled', 'sample.port_state']),
    'acquisition.flow_rate': ('通电', ['power.enabled']),
    'cytometer.settings': ('通电；至少一个字段；启用检测器含FSC与SSC；阈值参数已启用且不重复；记录、分选或检查中不能改阈值与启用检测器',
                           ['power.enabled', 'cytometer.detectors_enabled', 'operations.running']),
    'compensation.set': ('通电；溢出矩阵对角为1且可逆', ['power.enabled']),
    'compensation.calculate': ('通电；记录存在且≥100个事件；对照有阳性群（计算出的溢出<0.1 %按0处理，不计补偿对）', ['power.enabled', 'records.count']),
    'gate.define': ('已声明实验室条件；ID唯一；父门存在；字段与类型一致', ['gates.defined']), 'gate.delete': ('门存在；没有子门、未被布局或进行中的记录引用', ['gates.defined']),
    'acquisition.record': ('通电；正在采集；ID唯一；无其它记录进行中；存储未满；门存在', ['power.enabled', 'sample.acquiring', 'records.count', 'operations.running']),
    'collection.install': ('分选收集室门打开≥60°；没有装置；预加液不超过容量', ['door.sort_chamber_angle_rad', 'collection.device']),
    'collection.remove': ('分选收集室门打开≥60°；有装置；不在分选', ['door.sort_chamber_angle_rad', 'collection.device', 'operations.running']),
    'sort.layout': ('位置与装置一致；门存在；板须给每孔事件数；该布局不在分选中', ['gates.defined', 'sort.layouts', 'operations.running']),
    'sort.start': ('通电；布局存在；装置与布局一致；液流开；偏转板电压开；正在采集；板分选须关分选收集室门；无其它分选类操作', ['power.enabled', 'sort.layouts', 'collection.device',
                                                                                                    'stream.on', 'deflection.voltage_on', 'sample.acquiring',
                                                                                                    'door.sort_chamber_angle_rad', 'operations.running']),
    'sort.pause': ('通电；分选进行中', ['power.enabled', 'sort.state']), 'sort.resume': ('通电；分选已暂停', ['power.enabled', 'sort.state']),
    'sort.auto_drop_delay': ('通电；液流开；Sweet Spot开；偏转板电压开；进样口上的管含Accudrop微球且已升起、正在采集；无其它分选类操作',
                             ['power.enabled', 'stream.on', 'sweet_spot.on', 'deflection.voltage_on', 'sample.tube_id', 'sample.port_state', 'sample.acquiring',
                              'operations.running']),
    'cst.performance_check': ('通电；进样口上的管含CS&T微球且已升起、正在采集；无其它分选类操作', ['power.enabled', 'sample.tube_id', 'sample.port_state', 'sample.acquiring',
                                                                                   'operations.running']),
    'safety.emergency_stop': ('已声明实验室条件', ['plant.configured']), 'safety.reset_emergency_stop': ('已声明实验室条件', ['plant.configured']),
    'warnings.dismiss': ('通电', ['power.enabled']), 'cart.refill_sheath': ('液流关闭；加入后不超过10 L', ['stream.on', 'cart.sheath_level_fraction']),
    'cart.empty_waste': ('液流关闭', ['stream.on']), 'cart.refill_cleaning': ('已声明实验室条件', ['plant.configured']),
    'statistics.read': ('记录存在（或live）；门存在', ['records.count', 'gates.defined']), 'record.read': ('记录存在', ['records.count']),
    'data.export': ('记录存在', ['records.count']), 'sort.side_streams': ('通电', ['power.enabled']),
    'tube.mix': ('已声明实验室条件；样品管在实验台上（先卸管、取下）', ['plant.configured', 'tubes.on_bench']),
    'sample.provide': ('已声明实验室条件；样品ID唯一、样品数≤40；群体ID不重复；颗粒与染料在库中', ['plant.configured', 'samples.available']),
    'fault.inject': ('已声明实验室条件', ['plant.configured']),
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE']
_PW = ['PLANT_REQUIRED', 'POWER_OFF']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED'], 'power.set': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'], 'power.press_switch': ['PLANT_REQUIRED', 'MAINS_OUT_OF_RANGE'],
    'lasers.set': _PW, 'door.set': ['PLANT_REQUIRED'], 'nozzle.install': ['PLANT_REQUIRED', 'STREAM_ON', 'DOOR_CLOSED'],
    'fluidics.startup': _PW + ['FLUIDICS_BUSY', 'FLUIDICS_ALREADY_STARTED', 'SORT_NOZZLE_REQUIRED', 'SHEATH_LOW', 'WASTE_FULL'],
    'fluidics.shutdown': _PW + ['FLUIDICS_BUSY', 'FLUIDICS_NOT_READY', 'STREAM_ON', 'CLOSED_LOOP_NOZZLE_REQUIRED', 'CLEANING_FLUID_LOW'],
    'cleaning.run': _PW + ['FLUIDICS_BUSY', 'FLUIDICS_NOT_READY', 'STREAM_ON', 'CLOSED_LOOP_NOZZLE_REQUIRED', 'CLEANING_FLUID_LOW'],
    'sort_setup.select': _PW + ['STREAM_ON', 'SWEET_SPOT_ON', 'SORT_IN_PROGRESS'], 'sheath.set_pressure': _PW + ['SWEET_SPOT_ON', 'SORT_IN_PROGRESS'],
    'stream.set': _PW + ['EMERGENCY_STOP_LATCHED', 'FLUIDICS_NOT_READY', 'SORT_NOZZLE_REQUIRED', 'SHEATH_EMPTY'],
    'breakoff.set': _PW + ['SWEET_SPOT_ON', 'SORT_IN_PROGRESS'], 'sweet_spot.set': _PW + ['STREAM_OFF', 'NO_BREAKOFF'],
    'deflection.set': _PW + ['EMERGENCY_STOP_LATCHED', 'PLATES_OFF'], 'drop_delay.set': _PW + ['SORT_IN_PROGRESS'], 'drawer.set': _PW + ['EMERGENCY_STOP_LATCHED'],
    'amo.set': _PW + ['DOOR_OPEN'], 'sample.agitation': _PW, 'sample.temperature': _PW, 'collection.temperature': _PW, 'sample.chamber_light': _PW,
    'tube.prepare': ['PLANT_REQUIRED', 'ITEM_EXISTS', 'TOO_MANY_ITEMS', 'INVALID_SOURCE', 'UNKNOWN_SAMPLE', 'INSUFFICIENT_SAMPLE', 'UNKNOWN_PARTICLE', 'UNKNOWN_DYE',
                     'VOLUME_OUT_OF_RANGE'],
    'tube.mix': ['PLANT_REQUIRED', 'UNKNOWN_ITEM', 'TUBE_NOT_ON_BENCH'],
    'tube.install': ['PLANT_REQUIRED', 'UNKNOWN_ITEM', 'TUBE_NOT_ON_BENCH', 'PORT_OCCUPIED', 'PORT_NOT_DOWN'], 'tube.remove': ['PLANT_REQUIRED', 'NO_TUBE', 'PORT_NOT_DOWN'],
    'sample.load': _PW + ['EMERGENCY_STOP_LATCHED', 'STREAM_OFF', 'NO_TUBE', 'PORT_NOT_DOWN'], 'sample.unload': _PW + ['NOT_LOADED'], 'acquisition.set': _PW + ['NOT_LOADED'],
    'acquisition.flow_rate': _PW, 'cytometer.settings': _PW + ['ACQUISITION_IN_PROGRESS', 'INVALID_PARAMETERS', 'INVALID_THRESHOLD'],
    'compensation.set': _PW + ['INVALID_SPILLOVER'], 'compensation.calculate': _PW + ['UNKNOWN_RECORD', 'TOO_FEW_EVENTS', 'NO_POSITIVE_POPULATION'],
    'gate.define': ['PLANT_REQUIRED', 'ITEM_EXISTS', 'TOO_MANY_ITEMS', 'UNKNOWN_GATE', 'INVALID_GATE'], 'gate.delete': ['PLANT_REQUIRED', 'UNKNOWN_GATE', 'GATE_IN_USE'],
    'acquisition.record': _PW + ['ITEM_EXISTS', 'TOO_MANY_ITEMS', 'STORAGE_FULL', 'UNKNOWN_GATE', 'RECORDING_IN_PROGRESS', 'NOT_ACQUIRING'],
    'collection.install': ['PLANT_REQUIRED', 'DOOR_CLOSED', 'DEVICE_INSTALLED', 'VOLUME_OUT_OF_RANGE'],
    'collection.remove': ['PLANT_REQUIRED', 'NO_DEVICE', 'DOOR_CLOSED', 'SORT_IN_PROGRESS'],
    'sort.layout': ['PLANT_REQUIRED', 'TOO_MANY_ITEMS', 'SORT_IN_PROGRESS', 'INVALID_LAYOUT', 'UNKNOWN_GATE'],
    'sort.start': _PW + ['UNKNOWN_LAYOUT', 'SORT_IN_PROGRESS', 'DEVICE_MISMATCH', 'STREAM_OFF', 'PLATES_OFF', 'NOT_ACQUIRING', 'DOOR_OPEN'],
    'sort.pause': _PW + ['NOT_SORTING'], 'sort.resume': _PW + ['NOT_SORTING', 'NOT_PAUSED'],
    'sort.auto_drop_delay': _PW + ['SORT_IN_PROGRESS', 'STREAM_OFF', 'SWEET_SPOT_OFF', 'PLATES_OFF', 'ACCUDROP_BEADS_REQUIRED', 'NOT_ACQUIRING'],
    'cst.performance_check': _PW + ['SORT_IN_PROGRESS', 'CST_BEADS_REQUIRED', 'NOT_ACQUIRING'], 'safety.emergency_stop': ['PLANT_REQUIRED'], 'safety.reset_emergency_stop': ['PLANT_REQUIRED'],
    'warnings.dismiss': _PW, 'cart.refill_sheath': ['PLANT_REQUIRED', 'STREAM_ON', 'CONTAINER_OVERFLOW'], 'cart.empty_waste': ['PLANT_REQUIRED', 'STREAM_ON'],
    'cart.refill_cleaning': ['PLANT_REQUIRED'], 'statistics.read': ['PLANT_REQUIRED', 'UNKNOWN_RECORD', 'UNKNOWN_GATE'], 'record.read': ['UNKNOWN_RECORD'],
    'records.list': [], 'sort.side_streams': _PW, 'data.export': ['UNKNOWN_RECORD'], 'events.read': [], 'provenance.read': [],
    'sample.provide': ['PLANT_REQUIRED', 'ITEM_EXISTS', 'TOO_MANY_ITEMS', 'DUPLICATE_POPULATION', 'UNKNOWN_PARTICLE', 'UNKNOWN_DYE'], 'tube.inspect': ['UNKNOWN_ITEM'],
    'collection.inspect': [], 'ledger.read': [], 'fault.inject': ['PLANT_REQUIRED'], 'sim.step': [],
}
MODEL_NONE_ZH = {
    'plant.configure': '只声明宿主实验室条件', 'fluidics.startup': '液流程序只在过程层', 'fluidics.shutdown': '同左', 'cleaning.run': '同左',
    'sort_setup.select': '设定只在过程层', 'breakoff.set': '断点只在过程层（上摄像头图像为数值）', 'sweet_spot.set': '只在过程层', 'sheath.set_pressure': '只在过程层',
    'drop_delay.set': '只在过程层', 'amo.set': 'AMO抽吸只在过程层', 'sample.agitation': '只在过程层', 'sample.temperature': '只在过程层', 'collection.temperature': '只在过程层',
    'tube.prepare': '样品管没有场景几何（进样口上表面为接口面）', 'tube.mix': '样品管没有场景几何', 'tube.install': '同左', 'tube.remove': '同左', 'acquisition.set': '只在过程层', 'acquisition.flow_rate': '只在过程层',
    'cytometer.settings': '只在过程层', 'compensation.set': '只在过程层', 'compensation.calculate': '只在过程层', 'gate.define': '只在过程层', 'gate.delete': '只在过程层',
    'acquisition.record': '数据只在过程层', 'collection.install': '收集装置没有单独的场景几何（4路管架为固定外观）', 'collection.remove': '同左', 'sort.layout': '只在过程层',
    'sort.pause': '只在过程层', 'sort.resume': '只在过程层', 'sort.auto_drop_delay': '只在过程层', 'cst.performance_check': '只在过程层', 'warnings.dismiss': '只在过程层',
    'cart.refill_sheath': '液位只在过程层（场景罐体为固定外观）', 'cart.empty_waste': '同左', 'cart.refill_cleaning': '同左', 'statistics.read': '只读', 'record.read': '只读',
    'records.list': '只读', 'sort.side_streams': '只读', 'data.export': '数据操作', 'events.read': '只读', 'provenance.read': '只读', 'sample.provide': '样品没有场景几何',
    'tube.inspect': '只读真值（evaluator）', 'collection.inspect': '只读真值（evaluator）', 'ledger.read': '只读账目（evaluator）', 'fault.inject': '评估者故障注入', 'sim.step': '推进时钟'}
SCENE_MIRRORED = {f'door.{d}_angle_rad' for d in DOORS} | {'sample.port_position_m', 'sort.drawer_position_m', 'cart.gauge_angle_rad', 'safety.estop_position_m',
                                                            'lamp.deflection_plates', 'lamp.chamber_light', 'lamp.estop_light', 'power.enabled'}


def _model_effects(k, un):
    if un:
        return dict(mode='none', description_zh='不支持：不实现，没有模型变化', binding_ids=[])
    seen = sorted((set(EFFECTS.get(k, [])) | set(ON_STEP.get(k, []))) & SCENE_MIRRORED)
    if seen:
        return dict(mode='host_required', binding_ids=[],
                    description_zh='状态经宿主桥（Matterix）镜像到场景关节或指示灯（' + '、'.join(seen) + '；绑定见合同model_bindings），运行层本身不驱动模型'
                                   + ('；' + MODEL_NONE_ZH[k] if k in MODEL_NONE_ZH else ''))
    return dict(mode='none', description_zh=MODEL_NONE_ZH.get(k, '没有镜像到场景的状态变化'), binding_ids=[])


EXEC_TEXT = {
    'fluidics.startup': ('约8 min后fluidics.state为ready（操作succeeded）', '取消或断电：液流系统回到shutdown（需重新启动）；已用的鞘液不退回'),
    'fluidics.shutdown': ('约3 min后fluidics.state为shutdown（操作succeeded）', '取消或断电：回到shutdown状态（管路中可能残留鞘液，需重做）'),
    'cleaning.run': ('清洗时长结束后fluidics.state回到ready（操作succeeded）', '取消：回到ready；断电：回到shutdown'),
    'acquisition.record': ('记录到目标事件数（停止门计数）或时间上限后操作succeeded',
                           '取消：已记录的事件保留（操作cancelled）；卸下样品管（SAMPLE_UNLOADED）、急停、堵塞保护或断电：操作failed，已记录事件保留'),
    'sort.start': ('所有位置达到目标事件数（板：所有孔）后sort.state为completed（操作succeeded）；没有目标数的位置连续分选直到取消',
                   '取消（cancel）：sort.state为stopped（操作cancelled），抽屉关闭；卸下样品管：sort.state为stopped（操作failed，SAMPLE_UNLOADED；重新开始分选计数从0起）；'
                   '液流关闭、急停、断电或堵塞保护：sort.state为error（操作failed），已收集的颗粒保留'),
    'sort.auto_drop_delay': ('粗扫7点与细扫13点（每点5 s，共约100 s）结束后drop_delay.drops设为最优值（操作succeeded）', '取消或条件丢失（液流、偏转板、装载）：液滴延迟不变'),
    'cst.performance_check': ('约5 min后cst.status为passed或failed并设定激光延迟与面积系数（操作succeeded）', '取消或条件丢失：结果不变'),
}
RESOURCES = {'power.set': ['mains'], 'power.press_switch': ['mains'], 'lasers.set': ['lasers'], 'door.set': ['robot_arm'], 'nozzle.install': ['robot_arm', 'nozzle'],
             'fluidics.startup': ['fluidics_cart', 'plenum', 'host_clock'], 'fluidics.shutdown': ['fluidics_cart', 'plenum', 'host_clock'],
             'cleaning.run': ['fluidics_cart', 'host_clock'], 'stream.set': ['fluidics_cart', 'nozzle'], 'sample.load': ['loading_port', 'host_clock'],
             'sample.unload': ['loading_port', 'host_clock'], 'tube.prepare': ['bench'], 'tube.mix': ['robot_arm', 'bench'], 'tube.install': ['robot_arm', 'loading_port'], 'tube.remove': ['robot_arm', 'loading_port'],
             'acquisition.record': ['data_system', 'host_clock'], 'sort.start': ['sort_block', 'deflection_plates', 'aspirator_drawer', 'host_clock'],
             'sort.auto_drop_delay': ['sort_block', 'accudrop_camera', 'host_clock'], 'cst.performance_check': ['optics', 'host_clock'],
             'collection.install': ['robot_arm', 'sort_chamber'], 'collection.remove': ['robot_arm', 'sort_chamber'], 'cart.refill_sheath': ['robot_arm', 'sheath_tank'],
             'cart.empty_waste': ['robot_arm', 'waste_tank'], 'cart.refill_cleaning': ['robot_arm', 'cleaning_reservoirs'], 'plant.configure': ['room', 'mains'],
             'fault.inject': ['evaluator'], 'sim.step': ['host_clock'], 'data.export': ['workstation_disk']}
OBSERVES = {'power.set': ['power.enabled'], 'power.press_switch': ['power.enabled'], 'lasers.set': ['lasers.on', 'lasers.warm'], 'door.set': [f'door.{d}_angle_rad' for d in DOORS],
            'fluidics.startup': ['fluidics.state'], 'fluidics.shutdown': ['fluidics.state'], 'cleaning.run': ['fluidics.state'], 'stream.set': ['stream.on', 'sheath.pressure_pa'],
            'breakoff.set': ['breakoff.drop1_px', 'breakoff.gap_px'], 'sweet_spot.set': ['sweet_spot.on', 'breakoff.amplitude_v', 'sweet_spot.paused'],
            'deflection.set': ['deflection.voltage_on', 'lamp.deflection_plates'], 'sample.load': ['sample.port_state', 'sample.acquiring', 'acquisition.threshold_rate_eps'],
            'sample.unload': ['sample.port_state'], 'acquisition.record': ['records.count', 'operations.running'],
            'sort.start': ['sort.state', 'sort.counts', 'sort.conflicts', 'sort.efficiency', 'sort.sort_rate_eps'], 'sort.auto_drop_delay': ['drop_delay.drops', 'operations.running'],
            'cst.performance_check': ['cst.status'], 'safety.emergency_stop': ['safety.estop_latched', 'stream.on'], 'drawer.set': ['sort.drawer_position_m'],
            'cart.refill_sheath': ['cart.sheath_level_fraction'], 'cart.empty_waste': ['cart.waste_level_fraction']}
_WB, _TS, _UG, _BRO, _QR, _MSU, _PAV, _UCH, _FP, _RI, _SIM, _H2O = ('user_workbook', 'manufacturer_primary', 'manufacturer_users_guide', 'manufacturer_brochure',
                                                                  'manufacturer_quick_reference', 'facility_msu', 'facility_pavir', 'facility_uchicago', 'fpbase',
                                                                  'refractiveindex_info', 'simulation_assumption', 'water_properties_literature')
CAP_SOURCES = {k: [_SIM] for k in CAPS}
CAP_SOURCES.update({k: [_UG] for k in ('power.set', 'power.press_switch', 'door.set', 'nozzle.install', 'stream.set', 'drop_delay.set', 'drawer.set', 'sample.chamber_light',
                                        'tube.install', 'tube.remove', 'sample.unload', 'acquisition.set', 'warnings.dismiss', 'safety.reset_emergency_stop',
                                        'gate.define', 'gate.delete', 'records.list', 'events.read', 'sort.pause', 'sort.resume')})
CAP_SOURCES.update({'lasers.set': [_UG, _TS], 'fluidics.startup': [_UG, _TS, _SIM], 'fluidics.shutdown': [_UG, _TS], 'cleaning.run': [_TS, _UG, _SIM],
                    'sort_setup.select': [_UG, _TS, _MSU, _SIM], 'breakoff.set': [_UG, _TS, _SIM, _H2O], 'sweet_spot.set': [_UG, _UCH, _SIM],
                    'sheath.set_pressure': [_UG, _TS], 'deflection.set': [_UG, _SIM], 'amo.set': [_TS, _SIM], 'sample.agitation': [_TS, _PAV, _SIM],
                    'tube.mix': [_PAV, _SIM],
                    'sample.temperature': [_TS, _UG], 'collection.temperature': [_TS, _SIM], 'tube.prepare': [_TS, _FP, _RI, _SIM], 'sample.load': [_UG],
                    'acquisition.flow_rate': [_UG, _SIM], 'cytometer.settings': [_TS, _UG, _SIM], 'compensation.set': [_TS, _SIM], 'compensation.calculate': [_SIM],
                    'acquisition.record': [_TS, _SIM, _FP, _RI], 'collection.install': [_TS, _UG, _SIM], 'collection.remove': [_SIM],
                    'sort.layout': [_UG, _PAV, _TS], 'sort.start': [_UG, _TS, _UCH, _SIM], 'sort.auto_drop_delay': [_TS, _UG, _SIM], 'cst.performance_check': [_SIM, _TS],
                    'safety.emergency_stop': [_UG, _UCH], 'cart.refill_sheath': [_TS, _UG], 'cart.empty_waste': [_UG, _TS], 'cart.refill_cleaning': [_TS],
                    'statistics.read': [_SIM], 'record.read': [_SIM], 'sort.side_streams': [_UG, _SIM], 'data.export': [_TS, _SIM], 'provenance.read': [_FP, _RI, _TS],
                    'sample.provide': [_FP, _RI, _SIM], 'tube.inspect': [_SIM], 'collection.inspect': [_SIM], 'ledger.read': [_SIM], 'fault.inject': [_SIM, _UG, _UCH],
                    'plant.configure': [_TS, _SIM]})
CAP_SOURCES.update({k: [_TS, _UG] for k in UNSUPPORTED})
PARAM_ZH = {'room_k': '实验室室温', 'mains_voltage_v': '供电电压', 'noise_seed': '噪声随机数种子（只在首次声明时生效）', 'room_drift_k_per_s': '室温漂移速率'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.flow_sorting_process.limitations。'
CAP_LIMITS = {
    'sort_setup.select': ['预设按用户指南表3-1（70 µm High 70 psi/90 kHz/60 V/44滴、Medium 35 psi/60 kHz/50 V（衰减开）/45滴、100 µm Low 20 psi/30 kHz/20 V（衰减开）/29滴、'
                          'Custom 70 µm 20 psi/30 kHz/10 V/10滴（非分选高灵敏度模式：偏转板0 V、侧流0、激光延迟±82 µs、面积系数0.75/0.50/0.25、窗口延长4 µs），'
                          '偏转板电压、侧流电荷、激光延迟、面积系数、窗口延长2 µs）与技术规格（70 psi/90 kHz）；85 µm（45 psi/53 kHz，MSU）的振幅、液滴延迟、偏转与激光延迟，'
                          '以及130 µm（10 psi/16 kHz）全部为假设；Custom的振幅与液滴延迟不是断点设定（喷嘴耦合与流道体积沿用70 µm）；第2–4滴电荷校正与相位不建模。'],
    'breakoff.set': ['断点：Rayleigh毛细不稳定（无粘性色散式，水的表面张力IAPWS 2014、密度Tanaka 2001）× 驱动扰动 = 振幅×耦合系数；耦合系数按各预设默认值把断点放在同一图像位置'
                     '（20滴@70 µm High，假设）反推——70 µm High与Medium（衰减0.25，假设）得到同一耦合系数（2.53e-8 m/V）；耦合随鞘液温度−2 %/K（假设）；Drop 1按频闪锁定的液滴位置跳变'
                     '（液滴间距57 px@High，厂家），Gap为液柱末端到第一滴的距离。'],
    'sweet_spot.set': ['伺服速率粗调2 V/s、细调0.2 V/s（工程值）；Gap偏差>1 px时调节、>2 px或Drop 1偏差>10 px时暂停分选（厂家）；喷嘴全堵时执行厂家描述的保护动作，部分堵塞不触发停止'
                       '（设施指南），只因Gap偏差暂停分选。'],
    'stream.set': ['射流速度=Cd√(2ΔP/ρ)；Cd在70 psi时取0.9（库中假设），其它预设按表3-1激光延迟反推（Medium 0.849、100 µm 0.743；液滴波长/喷嘴直径因此为4.1–4.4，接近Rayleigh最优4.5），'
                   '85/130 µm按波长=4.4倍直径（假设）；液流开启后喷嘴处鞘液约20 min升温1.5 K（假设，对应“等30 min液流稳定”）。'],
    'deflection.set': ['侧流落点=K×充电比例×偏转板电压（+电压中心×0.1），K按默认电荷把近侧流放到4路12×75管架±14 mm、远侧流±31.5 mm（管架几何为假设），随射流速度平方反比；'
                       '展宽（近0.35 mm、远0.5 mm）与Gap偏差、过驱、部分堵塞的增宽为工程值。'],
    'sample.load': ['进样口约3 s升起（工程值）；样品流量按流速档线性10–120 µl/min（厂家），加压后1 s时间常数建立；死体积12×75管50 µl（假设）。'],
    'acquisition.flow_rate': ['芯流半径=√(Q样品/(π v芯))，芯流在65 µm宽光束中的位置使信号rCV≈R²/(2w²)：10 µl/min约0.5 %，120 µl/min约5 %（几何推导）。'],
    'cytometer.settings': ['PMT增益∝(V/500 V)^7、FSC增益∝(V/500 V)^3、每光电子1.8通道@500 V、电子噪声8通道、FSC杂散噪声10通道为工程值；'
                           '处理能力=min(70,000/s, 70,000/s×14/(启用检测器数+补偿对数/2))（厂家最高70,000/s（8参数12对补偿）；参数更多时的下降为假设）；'
                           '超过时随机丢弃（电子中止）；W=A/H×65,536（假设）。'],
    'acquisition.record': ['信号：每颗粒光电子=Σ分子数×（消光系数×激光处激发×光通量×曝光时间×量子产率×饱和×NA 1.2收集×光学透过0.35×带通内发射×PMT量子效率），背景按厂家85 MESF-FITC/29 MESF-PE灵敏度拟合；'
                           '散射按Mie表（FSC 1–14°、SSC NA锥）；光电子泊松统计、激光噪声0.6 %、预热、芯流位置、激光延迟捕获、面积系数与脉冲堆叠（窗口延长内的颗粒合成一个事件）；'
                           '颗粒到达为硬核泊松过程（相邻两颗粒至少相隔两者半长之和/芯流速度，排除体积），所以重合的两个细胞形成更宽的脉冲（W变大）。'],
    'sort.start': ['逐滴决定：事件按设定液滴延迟分到液滴（1/32滴相位），按厂家掩码定义（Yield/Purity/Phase、Single Cell）判断，不同位置抢同一液滴为冲突；颗粒实际所在液滴按真实液滴延迟'
                   '（激光到喷嘴的输运+断点滴数）加断点抖动0.02滴；阈值以下的颗粒看不见但随液滴走（可能混入）；侧流落点偏离收集管口时颗粒损失；抽屉未打开时偏转液滴落到抽屉。'],
    'sort.auto_drop_delay': ['自动程序（技术规格“全自动液滴延迟”）：Initial模式粗扫当前延迟±3滴（1滴步长），Fine Tune模式细扫±0.6滴（0.1步长），每点5 s（共约100 s）；'
                             '每点的得分是下摄像头（滤片在位）5 s内看到的Accudrop微球中进入左侧流的比例（偏转份额按掩码与抖动的解析式，逐颗二项抽取）；取前三点加权平均'
                             '（程序细节为假设）。'],
    'sample.agitation': ['搅拌档位Off/100/200/300 rpm为假设（厂家：软件可调）；300 rpm时只阻止60 %的Stokes沉降、不会让已沉降的颗粒再悬浮（假设；设施指南：'
                         '300 rpm搅拌也不太有效）；沉降前锋越过取样针（离管底2 mm）后该群体取不到。'],
    'tube.mix': ['混匀使所有群体的沉降前锋归零（完全再悬浮）；管须在实验台上（先卸管、取下）；没有剪切对细胞的影响。'],
    'sample.unload': ['卸管结束进行中的分选（sort.state=stopped，操作failed：SAMPLE_UNLOADED）与记录；重新开始分选时计数从0起（厂家用户指南第151页）。'],
    'collection.install': ['ACDU板：6/24/48孔（SBS孔径34.8/15.6/11.0 mm、孔容16.8/3.4/1.6 ml）、96与384孔；捕获半径与台移动时间为假设；收集管或孔溢出时按溢出份额'
                           '逐颗二项抽取损失颗粒（真值；仪器没有液位传感）。'],
    'statistics.read': ['记录的统计默认按记录时保存的补偿矩阵（与records.list的compensated一致），compensation=current时套用当前实验补偿；live按当前补偿。'],
    'record.read': ['默认按记录时保存的补偿矩阵，compensation=current时套用当前实验补偿；返回compensated。'],
    'data.export': ['默认按记录时保存的补偿矩阵，compensation=current时套用当前实验补偿；只导出CSV文本（不写真实FCS二进制）。'],
    'cst.performance_check': ['CS&T简化：亮微球（2e5 MESF，四种染料等量的混合，假设）的rCV按光电子、微球本身2 %、激光噪声与芯流位置计算，<6 %且激光已预热为通过（阈值为假设）；'
                              '激光延迟设为真值±0.2 µs、面积系数±1 %。'],
    'amo.set': ['AMO只影响气溶胶逃逸账目（正常模式逃逸率1e-5、快速1e-6、关闭1e-3、开门0.05，堵塞时气溶胶产生率×10，为工程值；厂家：应激条件下<3个Glo Germ颗粒）。'],
    'sample.temperature': ['进样室一阶到温，时间常数15 min（厂家：约45 min到温）；进样室打开时只能维持40 %的温差（假设）；样品管5 min时间常数跟随。'],
}
LIMITATIONS = [
    '选定配置：BD FACSAria III（2018规格），405/488/561/633 nm四激光、10个荧光检测器（V450/50、V510/50、B530/30、B695/40、YG582/15、YG610/20、YG710/50、YG780/60、R660/20、R780/60）'
    '+FSC/SSC，ACDU、AMO与收集冷却选件；355 nm紫外激光、索引分选、玻片与自定义装置不在配置内。',
    '液流为集总模型：射流速度、流量、比色池芯流速度、Rayleigh断点与液滴延迟按0.1 s网格计算；没有液滴形成的流体力学仿真、卫星滴与液滴充电的电场计算。',
    '光学为每颗粒的光子预算（库中FPbase光谱、厂家带通、PMT量子效率曲线与透过率为工程值），绝对灵敏度按厂家MESF标定；Mie散射为均匀球；细胞抗原密度、染色强度、自发荧光与粒径分布由样品给出。',
    '分选逻辑按厂家对掩码的文字描述实现（1/32滴）；同一tick（0.1 s）边界两侧的相邻液滴不互相检查（每tick约9000滴中1滴）；第2–4滴电荷校正与相位不建模；'
    '表3-1 Custom为非分选模式（偏转板0 V），其振幅与液滴延迟不是断点设定。',
    '颗粒到达为硬核泊松过程（排除体积），颗粒间没有流体力学相互作用；样品管沉降为各群体的Stokes沉降前锋（无浓度梯度、无团聚动力学），搅拌只部分有效。',
    '样品管、收集管、板与液滴没有场景几何；配管、装管、装收集装置、开关门与加液为宿主/机器人抽象，没有接触验证；工作站软件界面（图、门的绘制）以能力接口表示。',
    '气溶胶、无菌、细胞活力与温度对细胞的影响只以账目或温度读数表示；没有真实BD FACSAria III的硬件接口。',
]


def _sampling_text(k):
    if k == 'acquisition.events_processed':
        return 'Running total of the processed events since the tube was loaded (updated on the fixed 0.1 s host grid).'
    if k.startswith(('acquisition.', 'accudrop.')) or k == 'sort.sort_rate_eps':
        return 'Counted over the last 1 s of the fixed 0.1 s host grid.'
    if k.startswith(('sensor', 'truth.', 'sheath.', 'breakoff.', 'cart.', 'sample.', 'sort.', 'lamp.', 'lasers.', 'collection.temperature', 'sweet_spot', 'operations',
                     'warnings', 'errors', 'fluidics', 'safety', 'cst')) or k == 'clock.time_s':
        return 'Integrated on the fixed 0.1 s host grid; between grid points the last grid value is held.'
    return 'Changes when a command is accepted; no other time dynamics.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if (k in _CONTROL_GUARDED or k in _RECORD_QUERIES or k in ('compensation.calculate', 'tube.prepare') or CAPS[k]['visibility'] != 'agent') else []
    return list(dict.fromkeys(CAP_ERRORS.get(k, []) + _COMMON_ERRORS + extra))


def _foreign_text(k):
    if k in _CONTROL_GUARDED:
        return '；另一个agent的异步操作（液流程序、记录、分选、自动液滴延迟、CS&T）进行中时，agent调用被拒（ACCESS_DENIED；未通电时开机除外）；只能操作自己配的样品管、布局与门'
    if k in _RECORD_QUERIES or k == 'compensation.calculate':
        return '；agent只能读自己的记录（ACCESS_DENIED）'
    return ''


def _params():
    fl, op = fluidics(), optics()
    cal = _cal()
    pb = _photon()
    P = {}

    def doc(name, val, unit, meaning, src):
        P[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)

    def asm(name, val, unit, meaning, unc, src=(_SIM,)):
        P[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=list(src), uncertainty=unc)
    doc('sheath_pressure_range_pa', [5 * PSI, 75 * PSI], 'Pa', '鞘液压力5–75 psi', [_TS, _WB])
    doc('drop_drive_frequency_range_hz', [1.0, 100000.0], 'Hz', '液滴驱动频率1–100,000 Hz（2003版指南为1–102 kHz）', [_TS, _WB])
    doc('amplitude_range_v', [1.0, 80.0], 'V', '液滴驱动振幅1–80 V', [_UG])
    doc('drop_delay_range_drops', [10.0, 140.0], '1', '液滴延迟10–140滴', [_UG])
    doc('drop1_target_range_px', list(DROP1_RANGE), '1', 'Drop 1目标100–600像素；默认330', [_UG])
    doc('gap_default_px', GAP_DEFAULT, '1', 'Gap默认6像素', [_UG])
    doc('sweet_spot_tolerances_px', dict(gap_adjust=1.0, gap_pause=GAP_TOL_PAUSE, drop1=DROP1_TOL), '1', 'Gap偏差>1 px调节、>2 px暂停分选；Drop 1允许10 px偏差', [_UG])
    doc('drop_spacing_high_px', 57.0, '1', 'Drop 1按液滴间距约57像素跳变', [_UG])
    doc('plate_voltage_range_v', [0.0, 6000.0], 'V', '偏转板电压0–6000 V（开时两板间约12,000 V电位差）', [_UG])
    doc('window_extension_s', fl['window_extension_us'] * 1e-6, 's', '窗口延长2 µs（表3-1）', [_UG])
    doc('sample_flow_m3_s', [fl['sample_flow_rate_ul_min'][0] * 1e-9 / 60, fl['sample_flow_rate_ul_min'][1] * 1e-9 / 60], 'm3/s', '流速档1–11约10–120 µl/min', [_UG])
    doc('laser_warmup_s', WARMUP_S, 's', '激光开启后预热30 min', [_UG])
    doc('sample_temperature_setpoints_k', SAMPLE_SETPOINTS_K, 'K', '进样温控4、20、37、42 °C', [_TS, _WB])
    doc('sample_chamber_settle_s', 2700.0, 's', '进样室约45 min到温（须保持关闭）', [_UG])
    doc('reservoir_volumes_m3', dict(sheath=0.010, waste=0.010, cleaning=0.005), 'm3', '10 L鞘液与废液罐、5 L清洗液瓶', [_TS])
    doc('cart_air_pressure_pa', [80 * PSI, 100 * PSI], 'Pa', '运行时液流车气压80–100 psi（约88 psi）', [_UG])
    doc('max_event_rate_eps', RATE_REF, '1/s', '12对补偿、8参数时最高70,000事件/s', [_TS, _WB])
    doc('adc_channels', 262144, '1', '262,144通道（10 MHz ADC）', [_TS])
    doc('data_parameters', dict(total=20, fluorescence=18, scatter=2), '1', '最多20参数（18荧光+2散射）', [_TS, _WB])
    doc('beam_size_m', [BEAM_H, BEAM_W], 'm', '光斑高9 ±3 µm、宽65 ±7 µm', [_TS])
    doc('numerical_aperture', op['numerical_aperture'], '1', '凝胶耦合物镜NA 1.2', [_TS])
    doc('laser_min_power_w', {L: op['lasers'][L]['min_power_w'] for L in LASERS}, 'W', '激光头输出：405 >50 mW、488 >20 mW、561 >50 mW、633 >18 mW', [_TS])
    doc('fluorescence_sensitivity_mesf', dict(fitc=85.0, pe=29.0), '1', '70 psi、90 kHz、SPHERO RCP-30-5A：85 MESF-FITC、29 MESF-PE', [_TS])
    doc('cv_spec', dict(pi_cen=0.03, hoechst_cen=0.035, doublet_singlet_ratio=[1.95, 2.05]), '1', 'PI-CEN <3 %、Hoechst-CEN <3.5 %，双联/单体比1.95–2.05', [_TS])
    doc('sort_performance_spec', dict(purity_min=0.98, yield_of_poisson_min=0.80, ways=4), '1',
        '4路分选：纯度>98 %、得率>泊松预期的80 %（条件见sort_performance_threshold_rate、sort_performance_sheath_pressure_pa、sort_performance_frequency_hz）', [_TS])
    doc('sort_performance_threshold_rate', 25000.0, '1/s', '性能规格的平均阈值率25,000事件/s', [_TS])
    doc('sort_performance_sheath_pressure_pa', 70 * PSI, 'Pa', '性能规格的鞘液压力70 psi', [_TS])
    doc('sort_performance_frequency_hz', 90000.0, 'Hz', '性能规格的液滴驱动频率90 kHz', [_TS])
    doc('shutdown_s', SHUTDOWN_S, 's', 'Fluidics Shutdown约3 min', [_UG])
    doc('amo_exchange_rate_per_s', dict(AMO_EXCHANGE_PER_S), '1/s', 'AMO抽换收集室体积的速率：每分钟16次（正常）/69次（快速）', [_TS])
    doc('operating_temperature_k', list(ROOM_RANGE_K), 'K', '运行环境17.5–27.5 °C', [_TS])
    doc('max_power_w', 1500.0, 'W', '最大功率1,500 W（100/115/230 VAC、50/60 Hz）', [_TS])
    doc('precision_modes', {k: dict(yield_mask=v[0], purity_mask=v[1], phase_mask=v[2], single_cell=v[3]) for k, v in PRECISION.items()}, '1',
        '精度模式掩码（1/32滴）：Purity 32/32/0、Yield 32/0/0、Single Cell 0/32/16（单细胞）、Initial 32/0/0、Fine Tune 0/0/0、4-Way Purity 0/32/0', [_UG, _PAV])
    doc('level_warning_timing_s', dict(repeat=300.0, auto_stream_off=900.0), 's', '鞘液低/废液满警告每5 min重复，15 min未确认自动关液流', [_UG])
    doc('nozzles_m', [70e-6, 85e-6, 100e-6, 130e-6], 'm', '喷嘴70、85、100、130 µm', [_TS, _WB])
    doc('sort_devices', sorted(DEVICES), '1', '2路12×75/15 mL、4路1.5 mL/12×75、ACDU板6、24、48、96、384孔', [_TS])
    pres = fl['presets']
    tab = ('70_high', '70_medium', '100', '70_custom')                                # User's Guide Table 3-1 columns High / Medium / Low / Custom
    q_hi = _q_sheath(70e-6, cal['presets']['70_high']['v'])
    dl_asm = {n: fl['laser_delay_us']['70_high'] * 1e-6 * q_hi / _q_sheath(int(cal['presets'][n]['nozzle_um']) * 1e-6, cal['presets'][n]['v']) for n in ('85', '130')}
    doc('sort_setup_nozzle_m', {n: pres[n]['nozzle_m'] for n in pres}, 'm', 'Sort Setup预设的喷嘴（表3-1：High、Medium与Custom 70 µm、Low 100 µm；85与130 µm为本模型的预设名）',
        [_UG, _TS])
    doc('sort_setup_sheath_pressure_pa', {**{n: pres[n]['sheath_pressure_pa'] for n in tab}, '85': pres['85']['sheath_pressure_pa']}, 'Pa',
        '鞘液压力：表3-1 High 70、Medium 35、Low 20、Custom 20 psi；85 µm 45 psi（MSU设施文件）', [_UG, _TS, _MSU])
    doc('sort_setup_frequency_hz', {**{n: pres[n]['drop_drive_frequency_hz'] for n in tab}, '85': pres['85']['drop_drive_frequency_hz']}, 'Hz',
        '液滴驱动频率：表3-1 High 90、Medium 60、Low 30、Custom 30 kHz；85 µm 53 kHz（MSU：51–55 kHz，取中值）', [_UG, _TS, _MSU])
    doc('sort_setup_amplitude_v', {n: pres[n]['amplitude_v'] for n in tab}, 'V', '振幅：表3-1 60、50、20、10 V', [_UG])
    doc('sort_setup_attenuation', {n: PRESET_ATTEN[n] for n in tab}, '1', '衰减：表3-1 High关、Medium开、Low开、Custom关', [_UG])
    doc('sort_setup_drop_delay_drops', {n: pres[n]['drop_delay_drops'] for n in tab}, '1', '液滴延迟：表3-1 44、45、29、10滴（Custom非分选）', [_UG])
    doc('sort_setup_plate_voltage_v', {n: PRESET_PLATE_V[n] for n in tab}, 'V', '偏转板电压：表3-1 6000、5000、5000、0 V', [_UG])
    doc('sort_setup_side_stream_fraction', {n: cal['presets'][n]['side_frac'] for n in tab}, '1', '远左/左/右/远右充电比例：表3-1百分比/100', [_UG])
    doc('sort_setup_laser_delay_s', {n: fl['laser_delay_us'][n] * 1e-6 for n in tab}, 's', '激光延迟（红为负、紫为正）：表3-1 36、54、40、82 µs', [_UG])
    doc('sort_setup_area_scaling', {n: dict(fl['area_scaling'][n]) for n in tab}, '1', '面积系数（蓝/红/紫）：表3-1', [_UG])
    doc('sort_setup_window_extension_s', {n: fl['window_extension_us_by_preset'][n] * 1e-6 for n in tab}, 's', '窗口延长：表3-1 2、2、2、4 µs', [_UG])
    asm('sort_setup_assumed_amplitude_v', {n: pres[n]['amplitude_v'] for n in ('85', '130')}, 'V', '85与130 µm预设的振幅', '假设（BD默认值未取得；按断点位置缩放）',
        [_SIM, _MSU])
    asm('sort_setup_assumed_drop_delay_drops', {n: pres[n]['drop_delay_drops'] for n in ('85', '130')}, '1', '85与130 µm预设的液滴延迟', '假设（按Accudrop确定）', [_SIM])
    asm('sort_setup_assumed_attenuation', {n: PRESET_ATTEN[n] for n in ('85', '130')}, '1', '85与130 µm预设的衰减', '假设', [_SIM])
    asm('sort_setup_assumed_plate_voltage_v', {n: PRESET_PLATE_V[n] for n in ('85', '130')}, 'V', '85与130 µm预设的偏转板电压', '假设', [_SIM])
    asm('sort_setup_assumed_side_stream_fraction', {n: cal['presets'][n]['side_frac'] for n in ('85', '130')}, '1', '85与130 µm预设的侧流充电比例',
        '假设：按偏转常数把近侧流放到±14 mm、远侧流2.25倍', [_SIM])
    asm('sort_setup_assumed_laser_delay_s', dl_asm, 's', '85与130 µm预设的激光延迟', '假设：按比色池流量从High的36 µs缩放', [_SIM, _UG])
    asm('sort_setup_assumed_area_scaling', {n: dict(PRESET_ASF['100']) for n in ('85', '130')}, '1', '85与130 µm预设的面积系数', '假设：沿用100 µm Low的值', [_SIM, _UG])
    asm('sort_setup_130_sheath_pressure_pa', pres['130']['sheath_pressure_pa'], 'Pa', '130 µm预设的鞘液压力10 psi', '假设（按100 µm缩放；BD默认值未取得）', [_SIM])
    asm('sort_setup_130_frequency_hz', pres['130']['drop_drive_frequency_hz'], 'Hz', '130 µm预设的液滴驱动频率16 kHz', '假设（液滴波长约4.4倍喷嘴直径）', [_SIM])
    asm('discharge_coefficients', {k: round(v['cd'], 4) for k, v in cal['presets'].items()}, '1', '各预设喷嘴流量系数（射流速度=Cd√(2ΔP/ρ)）',
        '拟合值：High取0.9（库中假设），Medium与Low由表3-1激光延迟36/54/40 µs反推；85/130 µm按液滴波长=4.4倍直径', [_SIM, _UG])
    asm('transducer_coupling_m_per_v', {k: v['G'] for k, v in cal['presets'].items()}, 'm/V', '液滴驱动振幅到射流扰动的耦合系数',
        '拟合值：各预设默认振幅把断点放在20滴@70 µm High的同一图像位置（Rayleigh增长）；70 µm High与Medium一致（2.53e-8 m/V）', [_SIM, _UG])
    asm('laser_to_nozzle_volume_m3', {k: v['vdead'] for k, v in cal['presets'].items()}, 'm3', '激光交点到喷嘴出口的鞘液体积（液滴延迟的输运部分）',
        '拟合值：各预设默认液滴延迟（表3-1、假设值）减去断点滴数', [_SIM, _UG])
    asm('deflection_constant_m_per_v', {k: v['K_defl'] for k, v in cal['presets'].items()}, 'm/V', '侧流落点/（充电比例×偏转板电压）',
        '拟合值：默认电荷落在管架位置（管架几何为假设）；85/130 µm按速度与液滴直径缩放', [_SIM, _UG])
    asm('camera_pixel_m', cal['px'], 'm', '断点摄像头像素尺寸', '拟合值：High液滴间距=57像素（厂家）', [_SIM, _UG])
    asm('breakoff_drops_high', NB_REF_70, '1', '70 µm High默认时喷嘴到断点的液滴数', '假设（约6 mm）')
    asm('attenuation_factor', ATTENUATION, '1', '衰减开时驱动振幅系数', '假设；使High与Medium默认值得到同一耦合系数')
    asm('transducer_temperature_coefficient_per_k', TRANSDUCER_TC, '1/K', '耦合系数随鞘液温度的变化（断点漂移）', '假设：每K约7 px Drop 1漂移')
    asm('sheath_warmup_delta_k', SHEATH_WARM_K, 'K', '液流开启后喷嘴处鞘液的升温', '假设（“等30 min液流稳定”）')
    asm('sheath_warmup_tau_s', SHEATH_WARM_TAU_S, 's', '喷嘴处鞘液升温的时间常数', '假设（“等30 min液流稳定”）')
    asm('cuvette_channel_area_m2', CHANNEL_AREA, 'm2', '比色池流道截面（160×250 µm）', '库中假设')
    asm('core_speed_ratio', CORE_SPEED_RATIO, '1', '芯流速度/平均速度', '工程值（矩形管道中心线速度约2倍平均）')
    asm('spot_spacing_m', cal['spot_m'], 'm', '各激光光斑相对488 nm的距离（激光延迟=距离/芯流速度）', '拟合值：High的±36 µs；561 nm光斑在405 nm之后（假设）', [_SIM, _UG])
    asm('pulse_size_factor', PULSE_K, '1', '脉宽中颗粒直径的份额', '工程值')
    asm('channels_per_photoelectron_500v', K_CH, '1', '500 V时每光电子的ADC通道数', '工程值（暗珠噪声≈电子噪声时电压约550 V）')
    asm('pmt_gain_exponent', PMT_EXP, '1', 'PMT增益∝(V/500 V)^7', '库中假设')
    asm('fsc_gain_exponent', FSC_EXP, '1', 'FSC增益∝(V/500 V)^3', '假设')
    asm('electronic_noise_ch', 8.0, '1', '电子噪声（通道rms）', '库中假设')
    asm('fsc_noise_ch', FSC_NOISE_CH, '1', 'FSC杂散光噪声', '假设')
    asm('width_scale', WIDTH_SCALE, '1', 'W=A/H×65,536', '假设（FACSDiva约定）')
    asm('laser_noise_rms', LASER_NOISE, '1', '预热后激光强度噪声', '工程值')
    asm('laser_warmup_tau_s', WARMUP_TAU_S, 's', '预热过程的功率与噪声时间常数（开机时功率85 %、噪声+4 %）', '假设')
    asm('electrical_power_w', dict(base=BASE_POWER_W, lasers=LASER_POWER_W, sample_temperature_control=SAMPLE_TC_POWER_W), 'W', '整机功耗分配', '假设（厂家最大1,500 W）')
    asm('scatter_calibration', dict(fsc_ch_per_photon_500v=pb['k_fsc'], ssc_transmission=pb['t_ssc']), '1', 'FSC与SSC标度', '工程值：淋巴细胞FSC-A 60,000@250、SSC-A 15,000@300 V')
    asm('sweet_spot_slew_v_s', [SS_COARSE_V_S, SS_FINE_V_S], 'V/s', 'Sweet Spot粗调/细调振幅速率', '工程值')
    asm('breakoff_jitter_drops', DD_JITTER, '1', '稳定液流的断点抖动', '工程值')
    asm('side_stream_spread_m', list(SIDE_SIGMA_M), 'm', '近/远侧流在收集高度的展宽', '工程值')
    asm('device_geometry', {k: dict(capture_r=v['capture_r'], **({'locations': {a: b[1] for a, b in v['locations'].items()}} if v['kind'] == 'tubes' else {'x': v['x']}))
                            for k, v in DEVICES.items()}, 'm', '收集装置管口位置与捕获半径', '假设（管架几何按默认电荷反推）')
    asm('tube_capacity_m3', {k: v['capacity_m3'] for k, v in TUBE_TYPES.items()}, 'm3', '样品管容量', '工程值')
    asm('tube_dead_volume_m3', {k: v['dead_m3'] for k, v in TUBE_TYPES.items()}, 'm3', '样品管死体积（取样针取不到）', '工程值')
    asm('tube_inner_radius_m', {k: v['radius_m'] for k, v in TUBE_TYPES.items()}, 'm', '样品管内半径（液面高度）', '工程值')
    asm('bead_stock_per_m3', BEAD_STOCK_PER_M3, '1/m3', '微球储液浓度', '假设（1e6/ml）')
    asm('fluidics_durations_s', dict(startup=STARTUP_S, **CLEAN_S), 's', 'Fluidics Startup与清洗模式时长', '假设（指南：清洗5–10 min、无菌准备10–40 min）')
    asm('prime_sheath_m3', PRIME_SHEATH_M3, 'm3', '启动时灌注用的鞘液量', '假设')
    asm('temperature_time_constants_s', dict(chamber=SAMPLE_CHAMBER_TAU_S, tube=TUBE_TAU_S, collection=COLLECTION_TAU_S), 's', '进样室、样品管与收集管的温度时间常数',
        '进样室按45 min到温；其余为工程值')
    asm('agitation_rates_hz', dict(AGITATION), '1/s', '搅拌档位Off/100/200/300 rpm的转速', '假设（厂家只说软件可调；PAVIR设施指南提到300 rpm）', [_SIM, _TS, _PAV])
    asm('agitation_settling_reduction', AGITATION_EFFECT, '1', '300 rpm搅拌阻止的Stokes沉降份额（其它档按转速比例）', '假设（设施指南：300 rpm也不太有效）', [_SIM, _PAV])
    asm('collection_temperature_range_k', list(COLLECTION_RANGE_K), 'K', '收集冷却/加热范围', '假设（循环水选件）')
    asm('aerosol_generation_rate_per_s', dict(idle=5.0, sorting=50.0, clog=500.0), '1/s', '气溶胶液滴产生率（液流、分选、堵塞）', '工程值（厂家：AMO下应激条件<3个Glo Germ颗粒）')
    asm('aerosol_escape_fraction', dict(door_open=0.05, closed=1e-3, amo_normal=1e-5, amo_rapid=1e-6), '1', '气溶胶逃出分选收集室的份额', '工程值')
    asm('stream_settling', dict(probe_height_m=PROBE_HEIGHT_M), 'm', '不搅拌时颗粒按Stokes沉降，沉降前锋过探针后取不到', '工程值')
    return P


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


def export_bd_facsariaiii_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=v['reference'], origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'BdFacsariaiiiSession.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        async_ = k in ASYNC
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.bd_facsariaiii.BdFacsariaiiiSession.invoke',
                                              invocation=f'session.invoke(instance_id="facsaria_iii_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=_pre_text(k) + _foreign_text(k) + '；被拒时状态不变。',
                                                                              state_ids=_pre_slots(k),
                                                                              enforced_by='runtime', check='BdFacsariaiiiRuntime.invoke / BdFacsariaiiiSession._guard')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量') for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='asynchronous' if async_ else 'synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置或查询（进样口、抽屉、温度与液流在之后的step中演化）',
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
        component_ids=['fluidics_body', 'optics_body', 'sort_block', 'deflection_plates', 'aspirator_drawer', 'sample_chamber', 'loading_port', 'flow_cell_door',
                       'sort_chamber_door', 'cart_body', 'sheath_tank', 'waste_tank', 'gauge_needle', 'emergency_stop', 'deflection_lamp', 'estop_light', 'chamber_light'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='流式细胞分析与分选：液流车与压力缓冲罐、射流与Rayleigh断点（Drop 1/Gap、Sweet Spot、液滴延迟）、四激光光子预算（FPbase光谱、PMT、噪声、芯流位置、激光延迟、面积系数）、'
                   'Mie散射、阈值、脉冲堆叠与电子中止、补偿、门与统计、BD掩码逐滴分选、侧流落点与收集、Accudrop、CS&T、进样与收集温控、急停与堵塞保护、液位与气溶胶账目',
        equations=['v_jet = Cd sqrt(2 dP / rho(T)); Q = pi a^2 v_jet; v_core = 2 Q / A_channel; lambda = v_jet / f',
                   'omega^2 = sigma(T) / (rho a^3) x (1 - x^2) I1(x) / I0(x), x = 2 pi a / lambda (Rayleigh); N_breakoff = (f / omega) ln(a / (G (1 + beta dT) A_eff))',
                   'drop delay = f V_dead / Q + N_breakoff; Drop 1 = first strobe-locked drop p0 + j S below the jet end; Gap = Drop 1 - r_drop - breakoff',
                   'pe = sum_dye N_mol x QY x (t_ex) x k_ex / (1 + k_ex tau) x cone(NA 1.2) x 0.35 x f_band x QE, k_ex = 3.82e-25 eps(lambda_L) Phi_L; background fitted to 85 / 29 MESF',
                   'pos = exp(-2 y^2 / w^2), y uniform over the core radius sqrt(Q_s / (pi v_core)); A = pe x (V / 500)^7 x 1.8 x capture(laser delay) x ASF_set / ASF_true',
                   'H = A tau_small / sqrt(tau_small^2 + (0.8 D / v_core)^2); W = 65,536 A / H; events within (tau + window extension) merge; '
                   'processed rate <= min(70,000, 70,000 x 14 / (n_det + n_pairs / 2))',
                   'sort: drop k = floor(t f + DD_set), phase in 1/32; yield / purity / phase masks and single cell per the User\'s Guide; particle drop = floor(t f + DD_true + jitter)',
                   'side stream x = K q V_plate (q = charge fraction); capture = Phi((x_t + r - x) / s) - Phi((x_t - r - x) / s)',
                   'arrivals: hard-core Poisson, t_(i+1) - t_i >= (L_i + L_(i+1)) / (2 v_core); settling front dz/dt = v_Stokes (1 - 0.6 r_ag), r_ag = agitation rate / 300 rpm; tube.mix resets the front'],
        parameters=_params(), source_ids=[_SIM, _WB, _TS, _UG, _BRO, _MSU, _PAV, _UCH, _FP, _RI, _H2O], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_aria_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('bd_facsariaiii', caps, slots, {'flow_sorting_process': mechanism}, inputs={'plant.configure': (PLANT, PLANT_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if v['reference']:
            slots[k]['reference_conditions'] = v['reference']
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'flow_sorting_process': mechanism})

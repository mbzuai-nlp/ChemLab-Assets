"""CHEM-095 Wyatt Technology Eclipse AF4 (asymmetric-flow field-flow fractionation controller + analytical channel): process runtime.

Device (brochure W8100E, p.2-8): single-pump flow controller for AF4 with center-downstream injection; analytical channels short (153 mm effective
length) and long (246 mm e.l.), aqueous or organic versions; spacers 250 / 350 / 490 um; pre-cut membranes PES 1, 5, 10, 30 kDa and RC 10, 30 kDa;
channel pressure max. 30 bar with software overpressure protection; separation range ~1 nm - 10 um depending on sample and membrane; 1-100 ug
injections; Autoinject / Analog In / Pump Control / Detector Zero / Start Pump / Pump Interlock I/O; temperature-controlled separations (4-90 C)
need a temperature regulation chamber (not part of this configuration).  The HPLC pump, autosampler and the UV / MALS / dRI detectors are external
modules declared by the host (plant.configure); this runtime computes what they see.

Process model (fixed 0.1 s grid; SI units; skipped exactly while no flow runs and nothing is in the channel or on its way to the detectors):
  * Channel: trapezoidal spacer cut-out of effective length L and breadths b0 (inlet) -> bL (outlet), thickness w_eff = f_w x nominal spacer
    thickness (membrane swelling / compression; the operator only knows the nominal value until calibrate); area A(x), void volume V0 = w A_T.
  * Flows (single pump): elution: channel flow Q(x) = V_out + V_c (1 - A(x)/A_T), mean velocity v(x) = Q / (b(x) w); focus: inlet and focus flows
    meet at the focus line z_f and leave as cross flow, the detectors get the pump by-pass flow.  Cross-flow programme per step: constant,
    linear or exponential decay.  Channel pressure p = V_c / (L_p A_m) + R_out V_out (membrane permeability L_p, detector-line resistance R_out)
    with a 2 s first-order lag; above 30 bar the software stops the flows and aborts the run.
  * Analytes (template library, assets/reactions/wyatt_eclipseaf4_fractionation_library): R_h -> D = k T / (6 pi eta(T) R_h) (carrier viscosity
    from a Vogel / Andrade correlation); a size distribution is represented by 7 Gauss-Hermite pseudo-components in ln R_h.  Retention parameter
    lambda = D / (u0 w), u0 = V_c / A_T; retention ratio with the steric term (Giddings):  R = 6 a(1-a) + 6 lambda (1-2a) [coth((1-2a)/(2 lambda))
    - 2 lambda/(1-2a)], a = R_h / w;  zone velocity R v(x);  non-equilibrium plate height H = chi(lambda) w^2 v / D, chi = min(24 lambda^3 (1 - 8
    lambda + 60 lambda^2), 2/105);  variance grows by H dx;  focusing band 1.5 mm; relaxation 1 - exp(-t_relax / tau), tau = (w / u0)(1 + 2 lambda);
    the unrelaxed part elutes with R = 1 (void peak); injected mass above 100 ug broadens the band (overloading, engineering factor).
  * Membrane: sieving coefficient S = 1 / (1 + (R_h / R_c)^10), R_c from the nominal cut-off (globular-protein radius 0.0668 nm x M^(1/3) retained
    to 99 %); permeation rate k = S (u0 / w) / (lambda (1 - exp(-1/lambda))) acting on the mass still in the channel (focus and elution).
  * Detectors (after a 60 uL transfer volume): UV 280 nm absorbance (1 cm cell) = sum eps c l; dRI = sum (dn/dc) c; MALS 90 deg Rayleigh ratio
    R90 = sum K* c M (dn/dc)^2 P90, K* = 4 pi^2 n0^2 (dn/dc)^2 / (lambda0^4 N_A), lambda0 = 658 nm, P90 = 1 / (1 + q^2 Rg^2 / 3); seeded noise.
  * Analysis: peaks.analyze finds the peaks of the concentration detector and converts retention times back to R_h by integrating the same zone
    kinematics with the operator's channel thickness (nominal spacer, or the value from channel.calibrate_thickness with a standard).
  * Ledger: injected = in channel + eluted + permeated + flushed (aborts); sample loop: loaded = injected + left in the loop."""
from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path

PROTOCOL_VERSION = "1.0.0"
ASSET_TYPE_ID = "instrument.wyatt_eclipseaf4"
CATALOG_ID = "CHEM-095"
ROOT = Path(__file__).resolve().parents[2]
LIBRARY_REL = 'assets/reactions/wyatt_eclipseaf4_fractionation_library/templates.json'
DT = 0.1
REC_EVERY = 5                         # fractogram row every 0.5 s
KB = 1.380649e-23
NA = 6.02214076e23
C0 = 273.15
MAX_EVENTS_LOG = 3000
MAX_ROWS = 60000
_M64 = (1 << 64) - 1
CONVECTIVE_SPREAD = 0.35            # max. band standard deviation per metre travelled (engineering)

# ---------------------------------------------------------------- documented values (brochure W8100E p.8 / p.2 / p.4)
DOC = dict(
    channel_pressure_max_pa=3.0e6, spacers_m=(250e-6, 350e-6, 490e-6), effective_length_m={'short': 0.153, 'long': 0.246},
    membranes=('PES_1kDa', 'PES_5kDa', 'PES_10kDa', 'PES_30kDa', 'RC_10kDa', 'RC_30kDa'), separation_range_m=(1e-9, 10e-6),
    analytical_load_kg=(1e-9, 100e-9), control_unit_dimensions_m=(0.64, 0.36, 0.25), weight_kg=20.0, solvent_ph=(2.0, 12.0),
)
# ---------------------------------------------------------------- engineering values (no manufacturer number; every one is an assumption)
ENG = dict(
    breadth_inlet_m={'short': 0.0215, 'long': 0.0215}, breadth_outlet_m={'short': 0.0095, 'long': 0.0060},
    focus_position_m={'short': 0.025, 'long': 0.035}, thickness_factor=0.86,
    membrane_permeability_m_pa_s={'PES_1kDa': 0.6e-11, 'PES_5kDa': 2.0e-11, 'PES_10kDa': 5.0e-11, 'PES_30kDa': 1.0e-10, 'RC_10kDa': 3.0e-11, 'RC_30kDa': 8.0e-11},
    cutoff_kda={'PES_1kDa': 1.0, 'PES_5kDa': 5.0, 'PES_10kDa': 10.0, 'PES_30kDa': 30.0, 'RC_10kDa': 10.0, 'RC_30kDa': 30.0},
    sieving_exponent=10.0, globular_radius_nm_per_da13=0.0668, detector_line_resistance_pa_s_m3=1.2e13, pressure_tau_s=2.0,
    focus_band_sigma_m=0.0015, overload_mass_kg=100e-9, transfer_volume_m3=60e-9, pump_max_flow_m3_s=10e-6 / 60.0, loop_max_volume_m3=500e-9,
    flow_meter_noise_rel=0.002, pressure_noise_pa=2000.0, uv_noise_au=2e-5, dri_noise_riu=2e-9, ls_noise_per_m=1e-6,
    laser_wavelength_m=658e-9, uv_path_m=0.01, rg_over_rh={'polymer': 1.5, 'protein': 0.775, 'protein_fragment': 0.775, 'nanoparticle': 0.775, 'virus': 0.775},
    gauss_hermite_nodes=7, overload_broadening=0.5,
)
CARRIERS = {   # viscosity correlations: water (Vogel, eta = A 10^(B/(T-C))); organic: Andrade around 25 C (engineering activation energies)
    'aqueous_buffer': dict(kind='aqueous', vogel=(2.414e-5, 247.8, 140.0), n0=1.330, meaning_zh='水相缓冲液（如10 mM NaNO3/PBS，按水的黏度）'),
    'thf': dict(kind='organic', eta25=0.456e-3, ea=7.0e3, n0=1.407, meaning_zh='四氢呋喃'),
    'toluene': dict(kind='organic', eta25=0.560e-3, ea=9.0e3, n0=1.497, meaning_zh='甲苯'),
}
MODES = ('elution', 'focus', 'focus_inject')
PROFILES = ('constant', 'linear', 'exponential')
DETECTORS = ('uv', 'mals', 'dri')
GH7 = ([-2.6519613568, -1.6735516288, -0.8162878829, 0.0, 0.8162878829, 1.6735516288, 2.6519613568],
       [0.0009717812, 0.0545155828, 0.4256072526, 0.8102646176, 0.4256072526, 0.0545155828, 0.0009717812])


class ActionRejected(ValueError):
    """A capability call refused with a declared error code (no state change)."""


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


def _phi(z):
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


_LIBCACHE = {}


def library():
    if 'doc' not in _LIBCACHE:
        _LIBCACHE['doc'] = json.loads((ROOT / LIBRARY_REL).read_text(encoding='utf-8'))
        _LIBCACHE['by_id'] = {r['substance_id']: r for r in _LIBCACHE['doc']['templates']}
        import hashlib
        _LIBCACHE['sha256'] = hashlib.sha256((ROOT / LIBRARY_REL).read_bytes()).hexdigest()
    return _LIBCACHE


def template(sid):
    return library()['by_id'].get(sid)


TEMPLATE_IDS = sorted(r['substance_id'] for r in library()['doc']['templates']) if (ROOT / LIBRARY_REL).exists() else []


def carrier_viscosity(carrier, t_k):
    c = CARRIERS[carrier]
    if 'vogel' in c:
        a, b, cc = c['vogel']
        return a * 10.0 ** (b / (t_k - cc))
    return c['eta25'] * math.exp(c['ea'] / 8.314462618 * (1.0 / t_k - 1.0 / 298.15))


def retention_ratio(lam, alpha=0.0):
    """Giddings normal + steric retention ratio; alpha = particle radius / channel thickness (< 0.5)."""
    alpha = min(max(alpha, 0.0), 0.49)
    if lam <= 0.0:
        return 6.0 * alpha * (1.0 - alpha)
    g = 1.0 - 2.0 * alpha
    xa = g / (2.0 * lam)
    if xa > 40.0:
        coth = 1.0
    elif xa < 1e-8:
        return 1.0
    else:
        coth = 1.0 / math.tanh(xa)
    r = 6.0 * alpha * (1.0 - alpha) + 6.0 * lam * g * (coth - 2.0 * lam / g)
    return min(max(r, 1e-9), 1.0)


def chi(lam):
    if lam >= 0.1:
        return 2.0 / 105.0
    return min(24.0 * lam ** 3 * (1.0 - 8.0 * lam + 60.0 * lam * lam), 2.0 / 105.0)


class Geometry:
    """Trapezoidal channel (x from the inlet edge along the flow)."""

    def __init__(self, kind, w):
        self.kind, self.w = kind, w
        self.L = DOC['effective_length_m'][kind]
        self.b0, self.bL = ENG['breadth_inlet_m'][kind], ENG['breadth_outlet_m'][kind]
        self.zf = ENG['focus_position_m'][kind]
        self.AT = 0.5 * (self.b0 + self.bL) * self.L
        self.V0 = self.w * self.AT

    def b(self, x):
        x = min(max(x, 0.0), self.L)
        return self.b0 + (self.bL - self.b0) * x / self.L

    def A(self, x):
        x = min(max(x, 0.0), self.L)
        return self.b0 * x + 0.5 * (self.bL - self.b0) * x * x / self.L

    def v(self, x, vout, vc):
        """Mean channel velocity at x during elution (beyond the outlet: the outlet value)."""
        q = vout + vc * (1.0 - self.A(x) / self.AT)
        return q / (self.b(x) * self.w)

    def void_time(self, vout, vc):
        if vc <= 0.0:
            return self.w * (self.AT - self.A(self.zf)) / max(vout, 1e-15)
        return self.V0 / vc * math.log((vout + vc * (1.0 - self.A(self.zf) / self.AT)) / vout)


def cutoff_radius_m(membrane):
    r90 = ENG['globular_radius_nm_per_da13'] * (ENG['cutoff_kda'][membrane] * 1000.0) ** (1.0 / 3.0) * 1e-9
    return r90 / 99.0 ** (1.0 / ENG['sieving_exponent'])                # S(r_MWCO) = 0.01: 99 % retention at the nominal cut-off


def sieving(rh, membrane):
    return 1.0 / (1.0 + (rh / cutoff_radius_m(membrane)) ** ENG['sieving_exponent'])


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
TEXT = {'type': 'string', 'maxLength': 120}
SAMPLE_ID = {'type': 'string', 'minLength': 1, 'maxLength': 32, 'pattern': r'[A-Za-z0-9_\-]+'}
FREE_OBJ = dict(type='object')


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
    if k == 'null':
        if v is not None:
            raise ActionRejected('INVALID_ARGUMENT')
    elif k in ('number', 'integer'):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or (k == 'integer' and not float(v).is_integer()):
            raise ActionRejected('INVALID_ARGUMENT')
        if not s.get('minimum', -math.inf) <= v <= s.get('maximum', math.inf):
            raise ActionRejected('INVALID_ARGUMENT')
        if 'enum' in s and not any(abs(v - e) <= 1e-12 * max(abs(e), 1e-30) for e in s['enum']):
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
        if 'pattern' in s and not re.fullmatch(s['pattern'], v):
            raise ActionRejected('INVALID_ARGUMENT')
        if 'enum' in s and v not in s['enum']:
            raise ActionRejected('INVALID_ARGUMENT')


# ---------------------------------------------------------------- capabilities
CAPS = {}


def cap(k, s, o, e, c, meaning, visibility='agent', kind='device_command'):
    CAPS[k] = dict(input_schema=s, output_schema=o, example_arguments=e, component_ids=c, function_zh=meaning, visibility=visibility, kind=kind)


FLOW = number('m3/s', 0.0, ENG['pump_max_flow_m3_s'])
PLANT_EXAMPLE = dict(ambient_k=296.15, pressure_pa=101325.0, mains_v=230.0, noise_seed=1)
PLANT = obj(dict(ambient_k=number('K', C0 + 15.0, C0 + 35.0), pressure_pa=number('Pa', 70000.0, 110000.0), mains_v=number('V', 100.0, 253.0),
                 noise_seed=integer(0, 2 ** 31 - 1), version=enum(['aqueous', 'organic']), channel=enum(['long', 'short']), carrier=enum(sorted(CARRIERS)),
                 detectors=array(enum(DETECTORS), 0, 3), carrier_ph=number('1', 0.0, 14.0)),
            optional=('version', 'channel', 'carrier', 'detectors', 'carrier_ph'))
STEP = obj(dict(mode=enum(MODES), duration_s=number('s', 1.0, 6 * 3600.0), cross_flow_start_m3_s=FLOW, cross_flow_end_m3_s=FLOW, profile=enum(PROFILES),
                half_life_s=number('s', 1.0, 6 * 3600.0)), optional=('cross_flow_end_m3_s', 'profile', 'half_life_s'))
METHOD = obj(dict(detector_flow_m3_s=number('m3/s', 0.05e-6 / 60.0, 5e-6 / 60.0), focus_flow_m3_s=FLOW, inject_flow_m3_s=number('m3/s', 0.05e-6 / 60.0, 1e-6 / 60.0),
                  steps=array(STEP, 1, 20)), optional=('focus_flow_m3_s', 'inject_flow_m3_s'))
METHOD_EXAMPLE = dict(detector_flow_m3_s=1.0e-6 / 60.0, focus_flow_m3_s=2.0e-6 / 60.0, inject_flow_m3_s=0.2e-6 / 60.0,
                      steps=[dict(mode='elution', duration_s=60.0, cross_flow_start_m3_s=2.0e-6 / 60.0),
                             dict(mode='focus', duration_s=60.0, cross_flow_start_m3_s=2.0e-6 / 60.0),
                             dict(mode='focus_inject', duration_s=120.0, cross_flow_start_m3_s=2.0e-6 / 60.0),
                             dict(mode='focus', duration_s=120.0, cross_flow_start_m3_s=2.0e-6 / 60.0),
                             dict(mode='elution', duration_s=1500.0, cross_flow_start_m3_s=2.0e-6 / 60.0, cross_flow_end_m3_s=0.1e-6 / 60.0, profile='linear'),
                             dict(mode='elution', duration_s=300.0, cross_flow_start_m3_s=0.0)])
COMPONENT = obj(dict(template_id=enum(TEMPLATE_IDS) if TEMPLATE_IDS else TEXT, concentration_kg_m3=number('kg/m3', 0.0, 50.0)))
LOAD = obj(dict(sample_id=SAMPLE_ID, volume_m3=number('m3', 1e-9, ENG['loop_max_volume_m3']), components=array(COMPONENT, 1, 8), provided_id=SAMPLE_ID),
           optional=('components', 'provided_id'))
LOAD_EXAMPLE = dict(sample_id='bsa_1', volume_m3=20e-9, components=[dict(template_id='bovine_serum_albumin', concentration_kg_m3=2.0)])
PROVIDE = obj(dict(provided_id=SAMPLE_ID, components=array(COMPONENT, 1, 8), medium=enum(['aqueous', 'organic'])), optional=('medium',))
MEMBRANE_IN = obj(dict(membrane=enum(DOC['membranes'])))
SPACER_IN = obj(dict(thickness_m={'type': 'number', 'x-unit': 'm', 'enum': list(DOC['spacers_m'])}))
FAULTS = ['mains_outage', 'pump_failure', 'outlet_blockage', 'membrane_rupture', 'detector_fault']
PEAK = obj(dict(index=integer(0, 99), apex_time_s=number('s'), start_time_s=number('s'), end_time_s=number('s'), area=number('1'), height=number('1'),
                rh_from_retention_m=nullable(number('m')), molar_mass_kg_mol=nullable(number('kg/mol')), regime=enum(['normal', 'void', 'steric_possible']),
                thickness_used_m=number('m')))
cap('plant.configure', PLANT, obj(dict(configured=BOOL)), PLANT_EXAMPLE, ['housing'],
    '声明环境与配置：室温、大气压、电源、噪声种子（之后不可改）、标准水相或有机相版本、长/短分析通道、载液、外接检测器（UV/MALS/dRI）与载液pH', 'evaluator', 'configuration')
cap('power.set', obj(dict(on=BOOL)), obj(dict(on=BOOL)), dict(on=True), ['power_switch'], '后面板电源开关：开（LCD点亮、控制器可用）或关')
cap('power.toggle', obj({}), obj(dict(on=BOOL)), {}, ['power_switch'], '按一次电源翘板开关：切换开/关')
cap('channel.set_lift', obj(dict(lift_m=number('m', 0.0, 0.04))), obj(dict(lift_m=number('m'), open=BOOL)), dict(lift_m=0.04), ['channel_top'],
    '抬起或压回通道顶板（0闭合可加压；>1 mm为打开，可换膜和垫片；须无流动）', 'agent', 'robot_skill')
cap('membrane.install', MEMBRANE_IN, obj(dict(membrane=enum(DOC['membranes']))), dict(membrane='RC_10kDa'), ['membrane'],
    '在烧结板上铺一张预裁超滤膜（PES 1/5/10/30 kDa或RC 10/30 kDa；须顶板打开且无膜）', 'agent', 'robot_skill')
cap('membrane.remove', obj({}), obj(dict(membrane=nullable(enum(DOC['membranes'])), used_runs=integer(0, 10 ** 6))), {}, ['membrane'],
    '取下旧膜（须顶板打开）', 'agent', 'robot_skill')
cap('spacer.install', SPACER_IN, obj(dict(thickness_m=number('m'))), dict(thickness_m=350e-6), ['spacer'],
    '放入Mylar垫片（250/350/490 µm，决定通道厚度；须顶板打开且无垫片）', 'agent', 'robot_skill')
cap('spacer.remove', obj({}), obj(dict(thickness_m=nullable(number('m')))), {}, ['spacer'], '取出垫片（须顶板打开）', 'agent', 'robot_skill')
cap('method.set', METHOD, obj(dict(method=FREE_OBJ)), METHOD_EXAMPLE, ['lcd_screen'],
    '设定方法：检测器流量、聚焦流量、进样流量与步骤表（洗脱/聚焦/聚焦+进样；横流起止值与恒定/线性/指数程序）；运行中不可改', 'agent', 'configuration')
cap('sample.load', LOAD, obj(dict(sample=FREE_OBJ)), LOAD_EXAMPLE, ['injection_port'],
    '在进样接口装入样品（自动进样器定量环或注射器）：给出组分（模板库物质与质量浓度）或评测者提供的盲样编号，以及进样体积（≤500 µL）', 'agent', 'robot_skill')
cap('sample.unload', obj({}), obj(dict(sample=nullable(FREE_OBJ))), {}, ['injection_port'], '清空进样环中尚未注入的样品（运行中不可）', 'agent', 'robot_skill')
cap('sample.provide', PROVIDE, obj(dict(provided_id=SAMPLE_ID)), dict(provided_id='unknown_1', components=[dict(template_id='ovalbumin', concentration_kg_m3=1.0)]),
    ['injection_port'], '评测者：准备一个组成对代理隐藏的盲样（代理用sample.load的provided_id装入）', 'evaluator', 'simulation')
cap('run.start', obj({}), obj(dict(run_id=TEXT, expected_duration_s=number('s'))), {}, ['lcd_screen', 'channel_top'],
    '开始运行方法（须开机、顶板闭合、已装膜与垫片、方法已设、有聚焦+进样步时须已装样）；之后由宿主时钟推进', 'agent', 'device_command')
cap('run.abort', obj({}), obj(dict(aborted=BOOL)), {}, ['lcd_screen'], '中止运行：泵停，通道内样品不再洗脱（记为冲洗损失）', 'agent', 'device_command')
cap('fractogram.read', obj(dict(since_s=number('s', 0.0), max_rows=integer(1, MAX_ROWS)), optional=('since_s', 'max_rows')),
    obj(dict(run_id=nullable(TEXT), columns=array(TEXT, 0, 16), rows=array(array(nullable(number('1')), 0, 16), 0, MAX_ROWS), template_source=nullable(FREE_OBJ))),
    dict(since_s=0.0), ['lcd_screen'], '读分离图：时间、横流、检测器流量、通道压力与UV(280 nm, AU)、dRI(RIU)、MALS 90°瑞利比(1/m)；未接的检测器为null', 'agent', 'query')
cap('peaks.analyze', obj(dict(detector=enum(['dri', 'uv']), dn_dc_m3_kg=number('m3/kg', 1e-6, 1e-3), min_height=number('1', 0.0)),
                         optional=('detector', 'dn_dc_m3_kg', 'min_height')),
    obj(dict(run_id=nullable(TEXT), peaks=array(PEAK, 0, 100), thickness_used_m=number('m'), calibrated=BOOL)), dict(detector='dri', dn_dc_m3_kg=0.185e-3),
    ['lcd_screen'], '峰分析：在浓度检测器上找峰，按保留时间和方法横流程序反算流体力学半径（用名义垫片厚度或已标定厚度）；给出dn/dc时由MALS/dRI算摩尔质量', 'agent', 'query')
cap('channel.calibrate_thickness', obj(dict(template_id=enum(TEMPLATE_IDS) if TEMPLATE_IDS else TEXT, retention_time_s=number('s', 1.0, 6 * 3600.0))),
    obj(dict(thickness_m=number('m'), nominal_m=number('m'))), dict(template_id='bovine_serum_albumin', retention_time_s=600.0), ['spacer'],
    '用已知R_h的标准物（模板库）在上一方法下的峰顶时间（peaks.analyze的apex_time_s，含传输延迟）反算有效通道厚度，供之后peaks.analyze使用（换垫片后作废）', 'agent', 'configuration')
cap('events.read', obj(dict(since_s=number('s', 0.0)), optional=('since_s',)), obj(dict(events=array(FREE_OBJ), dropped=integer(0, 10 ** 9))), dict(since_s=0.0),
    ['lcd_screen'], '事件与报警记录', 'agent', 'query')
cap('truth.inspect', obj({}), obj(dict(process=FREE_OBJ, faults=array(TEXT, 0, 16))), {}, ['channel_base'], '评测者：真实通道厚度、各谱带位置/方差/质量、λ与R', 'evaluator', 'query')
cap('ledger.read', obj({}), obj(dict(ledger=FREE_OBJ)), {}, ['channel_base'], '评测者：样品质量守恒账', 'evaluator', 'query')
cap('fault.inject', obj(dict(fault=enum(FAULTS), active=BOOL)), obj(dict(faults=array(TEXT, 0, 16))), dict(fault='outlet_blockage', active=True), ['housing'],
    '评测者：注入/清除故障（停电、泵故障、出口堵塞、膜破裂、检测器故障）', 'evaluator', 'simulation')
cap('sim.step', obj(dict(dt_s=number('s', 0.0, 2 * 86400.0))), obj(dict(time_s=number('s'))), dict(dt_s=10.0), ['housing'], '推进宿主时钟', 'evaluator', 'simulation')
UNSUPPORTED = {
    'temperature.set': '温控分离（4–90 °C）需要温控室选件，本配置没有（通道在室温）。',
    'injection.tip': 'tip-injection只有Eclipse DualTec（Eclipse AF4为中心下游进样）。',
    'hf5.run': '中空纤维HF5只有Eclipse DualTec。',
    'channel.switch': 'SEC/AF4/HF5自动切换只有Eclipse DualTec。',
    'metal_free.set': '无金属流路只有Eclipse DualTec。',
    'semiprep.run': '半制备通道（Eclipse AF4可选）未安装在本配置中。',
    'frit_inlet.run': 'frit-inlet通道（Eclipse AF4可选）未安装在本配置中。',
    'fraction.collect': '馏分收集需要HPLC馏分收集器，本资产没有。',
}
for _k, _v in UNSUPPORTED.items():
    cap(_k, obj({}), obj({}), {}, ['housing'], _v)

# ---------------------------------------------------------------- state slots
SLOTS = {}


def slot(k, schema, unit, origin, component, meaning, visibility='agent'):
    SLOTS[k] = dict(schema=schema, unit=unit, origin=origin, component=component, meaning=meaning, visibility=visibility)


slot('clock.time_s', number('s'), 's', 'derived', 'housing', '仿真时钟（宿主时钟）')
slot('plant.configured', BOOL, '1', 'setpoint', 'housing', '环境与配置是否已声明')
slot('config.summary', FREE_OBJ, '1', 'setpoint', 'housing', '版本（水相/有机相）、通道（长/短）、载液、外接检测器')
slot('power.on', BOOL, '1', 'setpoint', 'power_switch', '电源开')
slot('channel.top_lift_m', number('m', 0.0, 0.04), 'm', 'setpoint', 'channel_top', '通道顶板抬起高度（0闭合；顶板关节=该值）')
slot('channel.open', BOOL, '1', 'derived', 'channel_top', '顶板打开（抬起>1 mm）')
slot('membrane.installed', nullable(enum(DOC['membranes'])), '1', 'setpoint', 'membrane', '已装的膜（类型与截留）')
slot('spacer.thickness_m', nullable(number('m')), 'm', 'setpoint', 'spacer', '已装垫片的名义厚度')
slot('method.summary', nullable(FREE_OBJ), '1', 'setpoint', 'lcd_screen', '当前方法（流量与步骤表）')
slot('sample.loaded', nullable(FREE_OBJ), '1', 'setpoint', 'injection_port', '进样环中的样品：编号、体积、已注入体积（盲样不显示组成）')
slot('run.state', enum(['idle', 'running', 'completed', 'aborted']), '1', 'derived', 'lcd_screen', '运行状态')
slot('run.progress', FREE_OBJ, '1', 'derived', 'lcd_screen', '运行编号、当前步序号与模式、步内与总已用时间')
slot('flow.cross_set_m3_s', number('m3/s'), 'm3/s', 'derived', 'lcd_screen', '当前横流设定（方法程序给出）')
slot('flow.cross_measured_m3_s', nullable(number('m3/s')), 'm3/s', 'sensor', 'port_crossflow', '横流流量计读数')
slot('flow.detector_m3_s', number('m3/s'), 'm3/s', 'derived', 'port_detector_out', '去检测器的流量（洗脱时为通道出口流量）')
slot('pressure.channel_pa', nullable(number('Pa')), 'Pa', 'sensor', 'channel_base', '通道压力传感器读数（表压）')
slot('detector.uv_au', nullable(number('1')), '1', 'sensor', 'port_detector_out', '外接UV检测器280 nm吸光度（AU，1 cm）')
slot('detector.dri_riu', nullable(number('1')), '1', 'sensor', 'port_detector_out', '外接示差折光检测器（RIU）')
slot('detector.ls90_per_m', nullable(number('1/m')), '1/m', 'sensor', 'port_detector_out', '外接MALS 90°瑞利比')
slot('calibration.thickness_m', nullable(number('m')), 'm', 'setpoint', 'spacer', '标定得到的有效通道厚度（无则用名义值）')
slot('display.text', TEXT, '1', 'derived', 'lcd_screen', 'LCD状态行：Ready/Focus/Inject/Elution/Done/Abort/OVERPRES/OPEN（关机为空）')
slot('display.cross_flow_ml_min', nullable(number('1')), '1', 'derived', 'display_digits', 'LCD横流读数（mL/min，2位小数；关机为空）')
slot('alarm.state', nullable(enum(['overpressure', 'pump_failure', 'no_mains'])), '1', 'derived', 'lcd_screen', '报警')
slot('truth.process', FREE_OBJ, '1', 'process_truth', 'channel_base', '真实有效厚度、各谱带位置/方差/通道内质量/λ/R、膜渗透损失', 'evaluator')
slot('truth.faults', array(TEXT, 0, 16), '1', 'process_truth', 'housing', '已注入的故障', 'evaluator')
SCENE_MIRRORED = {'channel.top_lift_m', 'display.cross_flow_ml_min'}


class _Zone:
    __slots__ = ('species', 'rh', 'weight', 'm', 'x', 'var', 'F', 'relax_s', 'void', 'done', 'eluted', 'lost', 'injected', 'travel', 'blind')

    def __init__(self, species, rh, weight):
        self.species, self.rh, self.weight = species, rh, weight
        self.m = 0.0
        self.x = 0.0
        self.var = 0.0
        self.F = 0.0
        self.relax_s = 0.0
        self.void = False
        self.done = False
        self.eluted = self.lost = self.injected = 0.0
        self.travel = 0.0
        self.blind = False


# ---------------------------------------------------------------- runtime
class WyattEclipseaf4Runtime:
    def __init__(self, instance_id='wyatt_eclipseaf4_01'):
        self.instance_id = instance_id
        self.t = 0.0
        self.t_grid = 0.0
        self.tick = 0
        self.plant = None
        self.seed = 0
        self.on = False
        self.lift = 0.0
        self.membrane = 'RC_10kDa'                                          # reference pose: channel assembled with a 10 kDa RC membrane
        self.membrane_runs = 0
        self.spacer = 350e-6
        self.w_cal = None
        self.w_cal_for = None
        self.method = None
        self.loaded = None                                                  # {sample_id, volume, injected_volume, components {tid: c}, blind}
        self.provided = {}
        self.run = None                                                     # {id, step, step_t, total_t, state, rows, ...}
        self.run_counter = 0
        self.last_run = None
        self.zones = []
        self.p = 0.0
        self.vc = 0.0
        self.vout = 0.0
        self.pending = []                                                   # detector rows on their way through the transfer volume
        self.det = dict(uv=None, dri=None, ls=None)
        self.alarm = None
        self.faults = set()
        self.events, self.events_dropped = [], 0
        self.revision = 0
        self.led = dict(loaded_kg=0.0, injected_kg=0.0, eluted_kg=0.0, permeated_kg=0.0, flushed_kg=0.0, unloaded_kg=0.0)
        self._principals, self._requests, self._caller = {}, {}, None

    # ---- sessions
    @property
    def time_s(self):
        return self.t

    def register_principal(self, name, visibility):
        if visibility not in ('agent', 'evaluator', 'debug'):
            raise ActionRejected('INVALID_ARGUMENT')
        self._principals[name] = visibility

    def session(self, name):
        if name not in self._principals:
            raise ActionRejected('ACCESS_DENIED')
        return WyattEclipseaf4Session(self, name)

    def _event(self, kind, code, message):
        self.events.append(dict(time_s=round(self.t, 6), kind=kind, code=code, message_zh=message))
        if len(self.events) > MAX_EVENTS_LOG:
            self.events_dropped += len(self.events) - MAX_EVENTS_LOG
            del self.events[:-MAX_EVENTS_LOG]

    # ---- helpers
    def _need_plant(self):
        if self.plant is None:
            raise ActionRejected('PLANT_REQUIRED')

    def _mains(self):
        return self.plant is not None and 'mains_outage' not in self.faults

    def _need_on(self):
        self._need_plant()
        if not self._mains():
            raise ActionRejected('NO_MAINS')
        if not self.on:
            raise ActionRejected('POWER_OFF')

    def _open(self):
        return self.lift > 1e-3

    def _running(self):
        return self.run is not None and self.run['state'] == 'running'

    def _need_idle(self):
        if self._running():
            raise ActionRejected('RUN_ACTIVE')

    def _need_open(self):
        if not self._open():
            raise ActionRejected('CHANNEL_CLOSED')

    def _w_for(self, r):
        """Thickness the analysis of run r uses: the calibrated value when it was made for that run's spacer and channel, else the nominal spacer."""
        if r is None:
            return self.spacer or 350e-6
        c = r['conditions']
        if self.w_cal is not None and self.w_cal_for == (c['spacer_m'], c['channel']):
            return self.w_cal
        return c['spacer_m']

    def _w_true(self):
        return ENG['thickness_factor'] * self.spacer

    def _geom(self, w=None):
        return Geometry(self.plant['channel'], self._w_true() if w is None else w)

    def _temperature(self):
        return self.plant['ambient_k']

    def _eta(self):
        return carrier_viscosity(self.plant['carrier'], self._temperature())

    def _diff(self, rh):
        return KB * self._temperature() / (6.0 * math.pi * self._eta() * rh)

    # ---- capabilities
    def invoke(self, capability_id, **args):
        if capability_id in UNSUPPORTED:
            raise ActionRejected('UNSUPPORTED')
        fn = getattr(self, '_cmd_' + capability_id.replace('.', '_'))
        out = fn(**args)
        if capability_id not in ('events.read', 'truth.inspect', 'ledger.read', 'fractogram.read', 'peaks.analyze'):
            self.revision += 1
        return out

    def _cmd_plant_configure(self, ambient_k, pressure_pa, mains_v, noise_seed, version=None, channel=None, carrier=None, detectors=None, carrier_ph=None):
        if self.plant is not None and noise_seed != self.seed:
            raise ActionRejected('SEED_FIXED')
        first = self.plant is None
        version = version or (self.plant or {}).get('version', 'aqueous')
        channel = channel or (self.plant or {}).get('channel', 'long')
        carrier = carrier or (self.plant or {}).get('carrier', 'aqueous_buffer' if version == 'aqueous' else 'thf')
        detectors = sorted(set(detectors if detectors is not None else (self.plant or {}).get('detectors', list(DETECTORS))))
        ph = carrier_ph if carrier_ph is not None else (self.plant or {}).get('carrier_ph', 7.0)
        if CARRIERS[carrier]['kind'] != version:
            raise ActionRejected('SOLVENT_INCOMPATIBLE')                     # organic solvents need the organic version (vapour sensor, Kalrez)
        if version == 'aqueous' and not DOC['solvent_ph'][0] <= ph <= DOC['solvent_ph'][1]:
            raise ActionRejected('SOLVENT_INCOMPATIBLE')                     # standard version: pH 2-12
        if version == 'organic' and self.membrane and self.membrane.startswith('PES'):
            raise ActionRejected('MEMBRANE_INCOMPATIBLE')
        if not first and self._running():
            raise ActionRejected('RUN_ACTIVE')
        if not first and channel != self.plant['channel'] and self.zones:
            raise ActionRejected('RUN_ACTIVE')
        self.plant = dict(ambient_k=float(ambient_k), pressure_pa=float(pressure_pa), mains_v=float(mains_v), version=version, channel=channel, carrier=carrier,
                          detectors=detectors, carrier_ph=float(ph))
        self.seed = int(noise_seed)
        self._event('system', 'PLANT_CONFIGURED', f'配置：{version}版、{channel}通道、载液{carrier}、检测器{",".join(detectors) or "无"}')
        return dict(configured=True)

    def _cmd_power_set(self, on):
        self._need_plant()
        if not self._mains():
            raise ActionRejected('NO_MAINS')
        if on != self.on:
            if not on and self._running():
                self._abort('关机：运行中止')
            self.on = on
            self._event('operation', 'POWER_ON' if on else 'POWER_OFF', '开机' if on else '关机')
        return dict(on=self.on)

    def _cmd_power_toggle(self):
        return self._cmd_power_set(not self.on)

    def _cmd_channel_set_lift(self, lift_m):
        self._need_plant()
        if lift_m > 1e-3 and (self._running() or self.vc > 0.0 or self.vout > 0.0 or self.p > 1e4):
            raise ActionRejected('FLOW_ACTIVE')                               # never open a pressurised channel
        was = self._open()
        self.lift = float(lift_m)
        if was != self._open():
            self._event('operation', 'CHANNEL_OPEN' if self._open() else 'CHANNEL_CLOSED', '通道顶板抬起（可换膜/垫片）' if self._open() else '通道顶板压回')
            if self._open() and self.zones:
                left = sum(z.m for z in self.zones)
                self.led['flushed_kg'] += left
                for z in self.zones:
                    z.m = 0.0
                self.zones = []
                self._event('truth', 'SAMPLE_DISCARDED', f'打开通道：通道内残留样品{left * 1e9:.3f} µg被冲掉（评测者可见）')
        return dict(lift_m=self.lift, open=self._open())

    def _cmd_membrane_install(self, membrane):
        self._need_plant()
        self._need_open()
        if self.membrane is not None:
            raise ActionRejected('MEMBRANE_PRESENT')
        if self.plant['version'] == 'organic' and membrane.startswith('PES'):
            raise ActionRejected('MEMBRANE_INCOMPATIBLE')
        self.membrane = membrane
        self.membrane_runs = 0
        self._event('operation', 'MEMBRANE_INSTALLED', f'装膜{membrane}')
        return dict(membrane=membrane)

    def _cmd_membrane_remove(self):
        self._need_plant()
        self._need_open()
        m, n = self.membrane, self.membrane_runs
        self.membrane = None
        self.membrane_runs = 0
        if m:
            self._event('operation', 'MEMBRANE_REMOVED', f'取下膜{m}（用过{n}次）')
        return dict(membrane=m, used_runs=n)

    def _cmd_spacer_install(self, thickness_m):
        self._need_plant()
        self._need_open()
        if self.spacer is not None:
            raise ActionRejected('SPACER_PRESENT')
        self.spacer = float(thickness_m)
        self.w_cal = None
        self._event('operation', 'SPACER_INSTALLED', f'装垫片{thickness_m * 1e6:.0f} µm（厚度标定作废）')
        return dict(thickness_m=self.spacer)

    def _cmd_spacer_remove(self):
        self._need_plant()
        self._need_open()
        s = self.spacer
        self.spacer = None
        self.w_cal = None
        return dict(thickness_m=s)

    def _cmd_method_set(self, detector_flow_m3_s, steps, focus_flow_m3_s=None, inject_flow_m3_s=None):
        self._need_on()
        self._need_idle()
        n_inj = sum(1 for s in steps if s['mode'] == 'focus_inject')
        if n_inj > 1:
            raise ActionRejected('INVALID_METHOD')
        for i, s in enumerate(steps):
            if s['mode'] == 'elution' and i > 0 and any(p['mode'] == 'focus_inject' for p in steps[:i]) and \
                    any(p['mode'] in ('focus', 'focus_inject') for p in steps[i + 1:]):
                raise ActionRejected('INVALID_METHOD')                        # once elution of the injected sample started, no re-focusing
            for key in ('cross_flow_start_m3_s', 'cross_flow_end_m3_s'):
                v = s.get(key)
                if v is not None and v + detector_flow_m3_s > ENG['pump_max_flow_m3_s'] + 1e-15:
                    raise ActionRejected('FLOW_LIMIT')
        ff = focus_flow_m3_s if focus_flow_m3_s is not None else max((s['cross_flow_start_m3_s'] for s in steps), default=0.0)
        if any(s['mode'] != 'elution' for s in steps) and ff <= 0.0:
            raise ActionRejected('INVALID_METHOD')
        if ff + detector_flow_m3_s > ENG['pump_max_flow_m3_s'] + 1e-15:
            raise ActionRejected('FLOW_LIMIT')                                # focusing: the pump feeds the focus flow plus the detector by-pass
        self.method = dict(detector_flow_m3_s=float(detector_flow_m3_s), focus_flow_m3_s=float(ff),
                           inject_flow_m3_s=float(inject_flow_m3_s if inject_flow_m3_s is not None else 0.2e-6 / 60.0),
                           steps=[dict(mode=s['mode'], duration_s=float(s['duration_s']), cross_flow_start_m3_s=float(s['cross_flow_start_m3_s']),
                                       cross_flow_end_m3_s=float(s.get('cross_flow_end_m3_s', s['cross_flow_start_m3_s'])), profile=s.get('profile', 'constant'),
                                       half_life_s=float(s.get('half_life_s', s['duration_s'] / 4.0))) for s in steps])
        self._event('operation', 'METHOD_SET', f'方法已设：{len(steps)}步，总时长{sum(s["duration_s"] for s in steps) / 60.0:.1f} min')
        return dict(method=copy.deepcopy(self.method))

    def _components(self, components):
        out = {}
        for c in components:
            tid = c['template_id']
            if template(tid) is None:
                raise ActionRejected('UNKNOWN_TEMPLATE')
            out[tid] = out.get(tid, 0.0) + float(c['concentration_kg_m3'])
        return out

    def _cmd_sample_provide(self, provided_id, components, medium=None):
        self._need_plant()
        comps = self._components(components)
        med = medium or ('organic' if any(template(t)['conditions']['medium'] != 'aqueous' for t in comps) else 'aqueous')
        self.provided[provided_id] = dict(components=comps, medium=med)
        self._event('truth', 'SAMPLE_PROVIDED', f'评测者提供盲样{provided_id}')
        return dict(provided_id=provided_id)

    def _cmd_sample_load(self, sample_id, volume_m3, components=None, provided_id=None):
        self._need_plant()
        self._need_idle()
        if (components is None) == (provided_id is None):
            raise ActionRejected('INVALID_ARGUMENT')
        if self.loaded is not None and self.loaded['volume_m3'] - self.loaded['injected_m3'] > 1e-15:
            raise ActionRejected('SAMPLE_PRESENT')
        if provided_id is not None:
            if provided_id not in self.provided:
                raise ActionRejected('UNKNOWN_SAMPLE')
            comps, blind, med = dict(self.provided[provided_id]['components']), True, self.provided[provided_id]['medium']
        else:
            comps, blind = self._components(components), False
            med = 'organic' if any(template(t)['conditions']['medium'] != 'aqueous' for t in comps) else 'aqueous'
        if med != self.plant['version']:
            raise ActionRejected('SOLVENT_INCOMPATIBLE')
        self.loaded = dict(sample_id=sample_id, volume_m3=float(volume_m3), injected_m3=0.0, components=comps, blind=blind, medium=med)
        m = sum(comps.values()) * volume_m3
        self.led['loaded_kg'] += m
        self._event('operation', 'SAMPLE_LOADED', f'装样{sample_id}：{volume_m3 * 1e9:.1f} µL')
        return dict(sample=self._sample_public())

    def _sample_public(self):
        s = self.loaded
        if s is None:
            return None
        out = dict(sample_id=s['sample_id'], volume_m3=s['volume_m3'], injected_m3=s['injected_m3'], blind=s['blind'])
        if not s['blind']:
            out['components'] = [dict(template_id=k, concentration_kg_m3=v) for k, v in sorted(s['components'].items())]
        return out

    def _cmd_sample_unload(self):
        self._need_plant()
        self._need_idle()
        s = self.loaded
        if s is not None:
            left = sum(s['components'].values()) * (s['volume_m3'] - s['injected_m3'])
            self.led['unloaded_kg'] += left
        pub = self._sample_public()
        self.loaded = None
        return dict(sample=pub)

    def _cmd_run_start(self):
        self._need_on()
        self._need_idle()
        if self.method is None:
            raise ActionRejected('NO_METHOD')
        if self._open():
            raise ActionRejected('CHANNEL_OPEN')
        if self.membrane is None or self.spacer is None:
            raise ActionRejected('CHANNEL_NOT_ASSEMBLED')
        if 'pump_failure' in self.faults:
            raise ActionRejected('PUMP_FAILURE')
        has_inj = any(s['mode'] == 'focus_inject' for s in self.method['steps'])
        if has_inj and (self.loaded is None or self.loaded['volume_m3'] - self.loaded['injected_m3'] <= 1e-15):
            raise ActionRejected('NO_SAMPLE')
        self.run_counter += 1
        rid = f'run_{self.run_counter:03d}'
        self.zones = [z for z in self.zones if not z.done and z.m > 0.0]     # leftovers of an earlier run stay in the channel (carry-over)
        carry = sorted({z.species for z in self.zones})
        self.run = dict(run_id=rid, step=0, step_t=0.0, total_t=0.0, state='running', rows=[], method=copy.deepcopy(self.method),
                        sample=self._sample_public(), thickness_nominal_m=self.spacer, membrane=self.membrane, started_s=self.t,
                        injected_kg={}, carry_over=carry, blind=bool(self.loaded and self.loaded['blind']) or any(z.blind for z in self.zones), k=0,
                        conditions=dict(channel=self.plant['channel'], spacer_m=self.spacer, carrier=self.plant['carrier'], temperature_k=self._temperature(),
                                        detectors=list(self.plant['detectors'])))
        self.membrane_runs += 1
        self.alarm = None
        self._event('operation', 'RUN_START', f'开始运行{rid}')
        return dict(run_id=rid, expected_duration_s=sum(s['duration_s'] for s in self.method['steps']))

    def _abort(self, why):
        if not self._running():
            return
        left = sum(z.m for z in self.zones)
        self.led['flushed_kg'] += left
        for z in self.zones:
            z.m = 0.0
            z.done = True
        self.zones = []
        self.run['state'] = 'aborted'
        self.vc = self.vout = 0.0
        self.last_run = self.run
        self._event('alarm' if 'OVERPRES' in why or '故障' in why else 'operation', 'RUN_ABORTED', why)

    def _cmd_run_abort(self):
        self._need_plant()
        was = self._running()
        self._abort('运行被中止')
        return dict(aborted=was)

    def _cmd_fractogram_read(self, since_s=0.0, max_rows=MAX_ROWS):
        r = self.run if self.run is not None else self.last_run
        cols = ['time_s', 'cross_flow_m3_s', 'detector_flow_m3_s', 'pressure_pa', 'uv_au', 'dri_riu', 'ls90_per_m']
        if r is None:
            return dict(run_id=None, columns=cols, rows=[], template_source=None)
        rows = [list(x) for x in r['rows'] if x[0] >= since_s][:max_rows]
        return dict(run_id=r['run_id'], columns=cols, rows=rows, template_source=self._provenance(r))

    def _provenance(self, r):
        lib = library()
        blind = bool(r.get('blind'))
        tids = sorted(set(r.get('injected_kg', {})) | set(r.get('carry_over', [])))
        recs = [lib['by_id'][t] for t in tids]
        return dict(library=LIBRARY_REL, sha256=lib['sha256'], records=None if blind else tids, carry_over=None if blind else list(r.get('carry_over', [])),
                    evidence=None if blind else (sorted({x['evidence'] for x in recs}) if recs else []),
                    note_zh='分离图由模板库的流体力学半径经AF4理论计算（检测器响应为类别假设），不是这台仪器的实测分离图'
                            + ('；盲样：组分与证据对代理隐藏' if blind else ''))

    # ---- analysis
    def _predict_tr(self, method, D, w, rh=0.0, channel=None):
        """Elution time (s from run start) of a zone with diffusion coefficient D under the method's set-point programme, channel thickness w:
        material is present from the start when the method has no injection step (carry-over / blank), else from the injection on; every focus
        step holds it at the focus line (refocusing), elution steps move it."""
        g = Geometry(channel or self.plant['channel'], w)
        x, t, started, present = g.zf, 0.0, False, not any(s['mode'] == 'focus_inject' for s in method['steps'])
        h = 0.5
        for s in method['steps']:
            n = max(int(math.ceil(s['duration_s'] / h)), 1)
            hh = s['duration_s'] / n
            for i in range(n):
                tau = (i + 0.5) * hh
                if s['mode'] == 'focus_inject':
                    present = True
                if s['mode'] != 'elution':
                    x = g.zf
                    t += hh
                    continue
                if not present:
                    t += hh
                    continue
                started = True
                vc = self._program(s, tau)
                vout = method['detector_flow_m3_s']
                lam = D * g.AT / (vc * w) if vc > 0 else math.inf
                R = retention_ratio(lam, rh / w) if math.isfinite(lam) else 1.0
                x += R * g.v(x, vout, vc) * hh
                t += hh
                if x >= g.L:
                    return t
        return None if not started else math.inf

    def _rh_from_tr(self, method, tr, w, cond):
        """Invert t_r(R_h) in the normal mode (bisection on ln R_h between 0.3 nm and 500 nm) with the run's channel, carrier and temperature."""
        kT6pe = KB * cond['temperature_k'] / (6.0 * math.pi * carrier_viscosity(cond['carrier'], cond['temperature_k']))
        ch = cond['channel']
        lo, hi = math.log(0.3e-9), math.log(500e-9)
        t_lo, t_hi = self._predict_tr(method, kT6pe / math.exp(lo), w, 0.0, ch), self._predict_tr(method, kT6pe / math.exp(hi), w, 0.0, ch)
        if t_lo is None or t_hi is None:
            return None, 'void'
        if tr < t_lo:
            return None, 'void'
        if math.isfinite(t_hi) and tr > t_hi:
            return None, 'steric_possible'
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            tm = self._predict_tr(method, kT6pe / math.exp(mid), w, 0.0, ch)
            if tm is None or tm > tr:
                hi = mid
            else:
                lo = mid
        return math.exp(0.5 * (lo + hi)), 'normal'

    def _cmd_peaks_analyze(self, detector='dri', dn_dc_m3_kg=None, min_height=None):
        self._need_plant()
        r = self.run if self.run is not None else self.last_run
        w_used = self._w_for(r)
        if r is None or not r['rows']:
            return dict(run_id=None if r is None else r['run_id'], peaks=[], thickness_used_m=w_used, calibrated=False)
        col = {'uv': 4, 'dri': 5}[detector]
        if detector not in r['conditions']['detectors']:
            raise ActionRejected('NO_DETECTOR')
        rows = r['rows']
        tt = [x[0] for x in rows]
        ys = [x[col] or 0.0 for x in rows]
        n = len(ys)
        k = 6                                                                # 6.5 s moving average (rows every 0.5 s)
        cs = [0.0]
        for y in ys:
            cs.append(cs[-1] + y)
        sm = [(cs[min(n, i + k + 1)] - cs[max(0, i - k)]) / (min(n, i + k + 1) - max(0, i - k)) for i in range(n)]
        noise = ENG['uv_noise_au'] if detector == 'uv' else ENG['dri_noise_riu']
        thr = max(min_height or 0.0, 8.0 * noise)
        maxima = [i for i in range(1, n - 1) if sm[i] > thr and sm[i] >= sm[i - 1] and sm[i] > sm[i + 1]]
        keep = []
        for i in maxima:                                                     # topographic prominence >= 25 % of the peak and >= 8 noise sigma
            hgt = sm[i]
            lmin, j = hgt, i
            while j > 0 and sm[j - 1] <= hgt:
                j -= 1
                lmin = min(lmin, sm[j])
            rmin, j = hgt, i
            while j < n - 1 and sm[j + 1] <= hgt:
                j += 1
                rmin = min(rmin, sm[j])
            prom = hgt - max(lmin, rmin)
            if prom >= max(0.25 * hgt, thr):
                keep.append(i)
        peaks = []
        for idx, i in enumerate(keep[:100]):
            lo_b = keep[idx - 1] if idx > 0 else 0
            hi_b = keep[idx + 1] if idx + 1 < len(keep) else n - 1
            a = i
            while a > lo_b and sm[a - 1] < sm[a] and sm[a - 1] > 0.05 * sm[i]:
                a -= 1
            if a > lo_b and sm[a - 1] >= sm[a]:                              # valley to the previous peak
                pass
            b = i
            while b < hi_b and sm[b + 1] < sm[b] and sm[b + 1] > 0.05 * sm[i]:
                b += 1
            area = sum(0.5 * (ys[j] + ys[j + 1]) * (tt[j + 1] - tt[j]) for j in range(a, b))
            t_apex = tt[i]
            t_rel = t_apex - r['started_s'] - ENG['transfer_volume_m3'] / r['method']['detector_flow_m3_s']
            rh, regime = self._rh_from_tr(r['method'], t_rel, w_used, r['conditions'])
            mm = None
            if dn_dc_m3_kg and 'mals' in r['conditions']['detectors'] and 'dri' in r['conditions']['detectors'] and rows[i][5] is not None:
                n0 = CARRIERS[r['conditions']['carrier']]['n0']
                kstar = 4 * math.pi ** 2 * n0 ** 2 * dn_dc_m3_kg ** 2 / (ENG['laser_wavelength_m'] ** 4 * NA)
                dri_sm = sum(rows[j][5] or 0.0 for j in range(max(0, i - k), min(n, i + k + 1))) / (min(n, i + k + 1) - max(0, i - k))
                ls = sum(rows[j][6] or 0.0 for j in range(max(0, i - k), min(n, i + k + 1))) / (min(n, i + k + 1) - max(0, i - k))
                c = dri_sm / dn_dc_m3_kg
                mm = ls / (kstar * c) if c > 0 else None
            peaks.append(dict(index=len(peaks), apex_time_s=t_apex - r['started_s'], start_time_s=tt[a] - r['started_s'], end_time_s=tt[b] - r['started_s'],
                              area=area, height=sm[i], rh_from_retention_m=rh, molar_mass_kg_mol=mm, regime=regime, thickness_used_m=w_used))
        return dict(run_id=r['run_id'], peaks=peaks, thickness_used_m=w_used, calibrated=self._w_for(r) != r['conditions']['spacer_m'])

    def _cmd_channel_calibrate_thickness(self, template_id, retention_time_s):
        self._need_plant()
        r = self.run if self.run is not None else self.last_run
        if r is None:
            raise ActionRejected('NO_RUN')
        if self.spacer is None:
            raise ActionRejected('CHANNEL_NOT_ASSEMBLED')
        cond = r['conditions']
        if cond['spacer_m'] != self.spacer or cond['channel'] != self.plant['channel']:
            raise ActionRejected('RUN_CONDITIONS_CHANGED')                  # the last run was made with another spacer / channel
        rec = template(template_id)
        if rec is None:
            raise ActionRejected('UNKNOWN_TEMPLATE')
        tk = cond['temperature_k']
        D = KB * tk / (6.0 * math.pi * carrier_viscosity(cond['carrier'], tk) * rec['values']['hydrodynamic_radius_m'])
        retention_time_s = retention_time_s - ENG['transfer_volume_m3'] / r['method']['detector_flow_m3_s']   # apex_time_s includes the transfer delay
        lo, hi = 0.4 * self.spacer, 1.1 * self.spacer
        tl, th = self._predict_tr(r['method'], D, lo, 0.0, cond['channel']), self._predict_tr(r['method'], D, hi, 0.0, cond['channel'])
        if tl is None or th is None or not (min(tl, th) <= retention_time_s <= max(tl, th)):
            raise ActionRejected('CALIBRATION_OUT_OF_RANGE')
        for _ in range(40):                                                  # t_r grows with w (lambda ~ 1/w, R ~ 6 lambda)
            mid = 0.5 * (lo + hi)
            tm = self._predict_tr(r['method'], D, mid, 0.0, cond['channel'])
            if tm > retention_time_s:
                hi = mid
            else:
                lo = mid
        self.w_cal = 0.5 * (lo + hi)
        self.w_cal_for = (self.spacer, self.plant['channel'])
        self._event('operation', 'THICKNESS_CALIBRATED', f'标定有效通道厚度{self.w_cal * 1e6:.1f} µm（名义{self.spacer * 1e6:.0f} µm）')
        return dict(thickness_m=self.w_cal, nominal_m=self.spacer)

    def _cmd_events_read(self, since_s=0.0):
        return dict(events=[e for e in self.events if e['time_s'] >= since_s], dropped=self.events_dropped)

    def _cmd_truth_inspect(self):
        self._need_plant()
        r = self.read('evaluator')
        return dict(process=r['truth.process'], faults=r['truth.faults'])

    def _cmd_ledger_read(self):
        return dict(ledger=self._ledger())

    def _cmd_fault_inject(self, fault, active):
        self._need_plant()
        (self.faults.add if active else self.faults.discard)(fault)
        if active and fault in ('mains_outage', 'pump_failure') and self._running():
            self._abort('电源故障：运行中止' if fault == 'mains_outage' else '泵故障：运行中止')
            self.alarm = 'no_mains' if fault == 'mains_outage' else 'pump_failure'
        if fault == 'mains_outage' and active:
            self.on = False
        if not active and self.alarm == {'mains_outage': 'no_mains', 'pump_failure': 'pump_failure'}.get(fault):
            self.alarm = None                                                 # the condition is gone: the alarm clears
        self._event('fault', 'FAULT_' + fault.upper(), f'故障{fault}' + ('：注入' if active else '：清除'))
        return dict(faults=sorted(self.faults))

    def _cmd_sim_step(self, dt_s):
        return dict(time_s=self.step(dt_s))

    # ---- physics
    def _program(self, s, tau):
        a, b = s['cross_flow_start_m3_s'], s['cross_flow_end_m3_s']
        if s['profile'] == 'linear':
            return a + (b - a) * min(tau / s['duration_s'], 1.0)
        if s['profile'] == 'exponential':
            return b + (a - b) * math.exp(-math.log(2.0) * tau / s['half_life_s'])
        return a

    def _quiet(self):
        return (self.plant is None) or (not self._running() and not self.pending and self.p == 0.0 and self.vc == 0.0 and self.vout == 0.0)

    def step(self, dt_s):
        if dt_s < 0:
            raise ActionRejected('INVALID_ARGUMENT')
        t_end = round(self.t + float(dt_s), 9)
        while self.t_grid + DT <= t_end + 1e-9:
            if self._quiet():
                n = int(math.floor((t_end - self.t_grid) / DT + 1e-6))
                self.tick += n
                self.t_grid = round(self.tick * DT, 9)
                self.t = self.t_grid
                break
            self.t = self.t_grid
            self._tick(DT)
            self.tick += 1
            self.t_grid = round(self.tick * DT, 9)
            self.t = self.t_grid
        self.t = t_end
        return self.t

    def _lam(self, z, u0, w):
        D = self._diff(z.rh)
        return (D / (u0 * w) if u0 > 0 else math.inf), D

    def _inject(self, h, w):
        s = self.loaded
        if s is None or s['volume_m3'] - s['injected_m3'] <= 1e-15:
            return
        dv = min(self.method_run['inject_flow_m3_s'] * h, s['volume_m3'] - s['injected_m3'])
        s['injected_m3'] += dv
        g = self._geom()
        for tid, c in s['components'].items():
            rec = template(tid)
            dm = c * dv
            if dm <= 0.0:
                continue
            self.run['injected_kg'][tid] = self.run['injected_kg'].get(tid, 0.0) + dm
            self.led['injected_kg'] += dm
            zs = [z for z in self.zones if z.species == tid and not z.void]
            if not zs:
                rh0 = rec['values']['hydrodynamic_radius_m']
                sg = rec['values'].get('size_dispersion_ln') or 0.0
                xs, ws = GH7
                norm = sum(ws)
                for xi, wi in zip(xs, ws):
                    z = _Zone(tid, rh0 * math.exp(math.sqrt(2.0) * sg * xi), wi / norm)
                    z.x, z.var = g.zf, ENG['focus_band_sigma_m'] ** 2
                    self.zones.append(z)
                zs = [z for z in self.zones if z.species == tid and not z.void]
            for z in zs:
                z.m += dm * z.weight
                z.injected += dm * z.weight
                z.blind = z.blind or s['blind']
                z.relax_s = 0.0                                              # fresh material: relaxation restarts (engineering: per zone)

    @property
    def method_run(self):
        return self.run['method']

    def _tick(self, h):
        mains = self._mains()
        running = self._running() and mains and self.on
        w = self._w_true() if self.spacer else None
        mode, vc_set, vdet = None, 0.0, 0.0
        if running:
            m = self.method_run
            st = m['steps'][self.run['step']]
            mode = st['mode']
            vc_set = self._program(st, self.run['step_t'])
            vdet = m['detector_flow_m3_s']
            if mode != 'elution':
                vc_set = m['focus_flow_m3_s']
            if 'pump_failure' in self.faults:
                vc_set = vdet = 0.0
        self.vc, self.vout = vc_set, vdet
        # ---- pressure
        p_target = 0.0
        if running and self.membrane:
            lp = ENG['membrane_permeability_m_pa_s'][self.membrane] * (1000.0 if 'membrane_rupture' in self.faults else 1.0)
            g = self._geom()
            r_out = ENG['detector_line_resistance_pa_s_m3'] * (20.0 if 'outlet_blockage' in self.faults else 1.0)
            p_target = max(self.vc / (lp * g.AT), r_out * self.vout)        # the outlet restrictor / cross-flow controller equalises both paths
        self.p += (p_target - self.p) * (1.0 - math.exp(-h / ENG['pressure_tau_s']))
        if not running and self.p < 1.0:
            self.p = 0.0
        if running and self.p > DOC['channel_pressure_max_pa']:
            self.alarm = 'overpressure'
            self._abort('OVERPRES：通道压力超过30 bar，软件停止流动并中止运行')
            running = False
            self.vc = self.vout = 0.0
        # ---- zones
        rows_c = {}
        if running and w and self.zones is not None:
            g = self._geom()
            u0 = self.vc / g.AT if self.vc > 0 else 0.0
            focus = mode in ('focus', 'focus_inject')
            if mode == 'focus_inject':
                self._inject(h, w)
            loaded_mass = sum(z.injected for z in self.zones)
            ovl = 1.0 + ENG['overload_broadening'] * max(0.0, loaded_mass / ENG['overload_mass_kg'] - 1.0)
            for z in self.zones:
                if z.done or z.m <= 0.0 and z.F > 0.0:
                    continue
                lam, D = self._lam(z, u0, w)
                # membrane permeation (focus and elution)
                if self.membrane and u0 > 0.0 and z.m > 0.0:
                    S = 1.0 if 'membrane_rupture' in self.faults else sieving(z.rh, self.membrane)
                    lam_e = min(lam, 1e6)
                    k = S * (u0 / w) / (lam_e * (1.0 - math.exp(-1.0 / lam_e)))
                    dm = z.m * (1.0 - math.exp(-k * h))
                    z.m -= dm
                    z.lost += dm
                    self.led['permeated_kg'] += dm
                if focus:
                    if z.void or z.F > 0.0 or z.travel > 0.0:                  # carry-over / earlier elution: refocused at the focus line
                        z.void, z.F, z.travel, z.relax_s = False, 0.0, 0.0, 0.0
                    z.x = g.zf
                    z.var = (ENG['focus_band_sigma_m'] * ovl) ** 2
                    z.relax_s += h
                    continue
                # elution
                if z.void:
                    R = 1.0
                    lam_v = math.inf
                else:
                    R = retention_ratio(lam, z.rh / w) if math.isfinite(lam) else 1.0
                    lam_v = lam
                vbar = g.v(min(z.x, g.L), self.vout, self.vc)
                u = R * vbar
                dx = u * h
                Hp = chi(lam_v) * w * w * vbar / D if math.isfinite(lam_v) else (2.0 / 105.0) * w * w * vbar / D
                z.x += dx
                z.travel += dx
                z.var = min(z.var + Hp * dx, (CONVECTIVE_SPREAD * z.travel) ** 2 + ENG['focus_band_sigma_m'] ** 2)   # non-equilibrium cap: a
                #   band cannot spread faster than the velocity range of the parabolic profile (Taylor-Aris needs t >> w^2/D)
                Fn = _phi((z.x - g.L) / math.sqrt(z.var)) if z.var > 0 else (1.0 if z.x >= g.L else 0.0)
                if Fn > z.F:
                    frac = (Fn - z.F) / max(1.0 - z.F, 1e-15)
                    de = z.m * min(frac, 1.0)
                    z.m -= de
                    z.eluted += de
                    self.led['eluted_kg'] += de
                    rows_c[z.species] = rows_c.get(z.species, 0.0) + de
                    z.F = Fn
                if z.x > g.L + 8.0 * math.sqrt(max(z.var, 1e-12)) or (z.F > 1.0 - 1e-12):
                    if z.m > 0.0:
                        self.led['eluted_kg'] += z.m
                        z.eluted += z.m
                        rows_c[z.species] = rows_c.get(z.species, 0.0) + z.m
                        z.m = 0.0
                    z.done = True
            self.zones = [z for z in self.zones if not z.done]
        # ---- detectors (outlet concentration -> after the transfer volume)
        if running:
            self.run['k'] += 1
            sig = self._signals(rows_c, h) if self.vout > 0 else dict(uv=0.0, dri=0.0, ls=0.0)
            delay = ENG['transfer_volume_m3'] / self.vout if self.vout > 0 else 0.0
            self.pending.append((self.t + h + delay, sig, self.vc, self.vout, self.p, self.run['k'] % REC_EVERY == 0))
        arrived = [x for x in self.pending if x[0] <= self.t + h + 1e-9]
        self.pending = [x for x in self.pending if x[0] > self.t + h + 1e-9]
        for (ta, sig, vc, vo, p, rec) in arrived:
            self.det = self._noisy(sig, ta)
            r = self.run
            if rec and r is not None and len(r['rows']) < MAX_ROWS:
                r['rows'].append((round(ta, 6), vc, vo, self._p_meas(p, ta), self.det['uv'], self.det['dri'], self.det['ls']))
        if not running and not self.pending:
            self.det = dict(uv=None, dri=None, ls=None) if not (self.on and mains) else self._noisy(dict(uv=0.0, dri=0.0, ls=0.0), self.t + h)
        # ---- programme clock
        if running:
            self.run['step_t'] += h
            self.run['total_t'] += h
            st = self.method_run['steps'][self.run['step']]
            if self.run['step_t'] >= st['duration_s'] - 1e-9:
                prev = st['mode']
                self.run['step'] += 1
                self.run['step_t'] = 0.0
                nxt = self.method_run['steps'][self.run['step']]['mode'] if self.run['step'] < len(self.method_run['steps']) else None
                if prev == 'focus_inject' and self.loaded is not None and self.loaded['volume_m3'] - self.loaded['injected_m3'] > 1e-15:
                    self._event('warning', 'INJECTION_INCOMPLETE', f'进样步结束时进样环中还剩{(self.loaded["volume_m3"] - self.loaded["injected_m3"]) * 1e9:.1f} µL（进样时间×进样流量不足）')
                if prev in ('focus', 'focus_inject') and nxt == 'elution':
                    self._release_focus()
                if self.run['step'] >= len(self.method_run['steps']):
                    self._finish_run()

    def _release_focus(self):
        """Focus -> elution: the relaxed part of each zone starts at the focus line, the unrelaxed part travels as a void zone (R = 1)."""
        g = self._geom()
        w = self._w_true()
        vc = self.method_run['focus_flow_m3_s']
        u0 = vc / g.AT if vc > 0 else 0.0
        new = []
        for z in self.zones:
            if z.void or z.m <= 0.0:
                continue
            lam, _ = self._lam(z, u0, w) if u0 > 0 else (math.inf, None)
            tau = (w / u0) * (1.0 + 2.0 * min(lam, 1e3)) if u0 > 0 else math.inf
            f = 1.0 - math.exp(-z.relax_s / tau) if math.isfinite(tau) else 0.0
            if f < 1.0 - 1e-9:
                v = _Zone(z.species, z.rh, z.weight)
                v.void, v.x, v.var, v.blind = True, z.x, z.var, z.blind
                v.m = z.m * (1.0 - f)
                v.injected = 0.0
                z.m -= v.m
                new.append(v)
        self.zones.extend(new)
        if new and sum(v.m for v in new) > 0.05 * max(sum(z.m for z in self.zones), 1e-30):
            self._event('truth', 'INCOMPLETE_RELAXATION', '聚焦/弛豫时间不足：部分样品未弛豫，将在空隙峰附近流出（评测者可见）')

    def _finish_run(self):
        self.run['state'] = 'completed'
        left = sum(z.m for z in self.zones)
        self.vc = self.vout = 0.0
        self.last_run = self.run
        self._event('operation', 'RUN_DONE', f'运行{self.run["run_id"]}结束')
        if left > 1e-15:
            self._event('truth', 'MATERIAL_LEFT_IN_CHANNEL', f'运行{self.run["run_id"]}结束时通道内仍有{left * 1e9:.3f} µg未洗脱（评测者可见）')

    def _signals(self, rows_c, h):
        uv = dri = ls = 0.0
        n0 = CARRIERS[self.plant['carrier']]['n0']
        lam0 = ENG['laser_wavelength_m']
        q = 4.0 * math.pi * n0 * math.sin(math.radians(45.0)) / lam0
        for tid, dm in rows_c.items():
            rec = template(tid)
            c = dm / (self.vout * h)
            resp = rec['detector_response']
            uv += resp['uv280_m2_kg'] * c * ENG['uv_path_m']
            dri += resp['dn_dc_m3_kg'] * c
            mm = rec['values'].get('molar_mass_kg_mol')
            if mm and resp['dn_dc_m3_kg'] > 0:
                kstar = 4 * math.pi ** 2 * n0 ** 2 * resp['dn_dc_m3_kg'] ** 2 / (lam0 ** 4 * NA)
                rg = ENG['rg_over_rh'].get(rec['category'], 0.775) * rec['values']['hydrodynamic_radius_m']
                ls += kstar * c * mm / (1.0 + q * q * rg * rg / 3.0)
        return dict(uv=uv, dri=dri, ls=ls)

    def _noisy(self, sig, ta):
        k = int(round(ta / DT))
        dets = self.plant['detectors'] if self.plant else []
        bad = 'detector_fault' in self.faults
        return dict(uv=(sig['uv'] + ENG['uv_noise_au'] * _gauss(self.seed, k, 1)) if 'uv' in dets and not bad else None,
                    dri=(sig['dri'] + ENG['dri_noise_riu'] * _gauss(self.seed, k, 2)) if 'dri' in dets and not bad else None,
                    ls=(sig['ls'] + ENG['ls_noise_per_m'] * _gauss(self.seed, k, 3)) if 'mals' in dets and not bad else None)

    def _p_meas(self, p, ta):
        return max(p + ENG['pressure_noise_pa'] * _gauss(self.seed, int(round(ta / DT)), 4), 0.0) if self._mains() and self.on else None

    # ---- read
    def _display(self):
        if not (self._mains() and self.on and self.plant is not None):
            return '', None
        if self.alarm == 'overpressure':
            txt = 'OVERPRES'
        elif self._open():
            txt = 'OPEN'
        elif self._running():
            mode = self.method_run['steps'][self.run['step']]['mode']
            txt = {'elution': 'Elution', 'focus': 'Focus', 'focus_inject': 'Inject'}[mode]
        elif self.last_run is not None and self.last_run['state'] == 'completed':
            txt = 'Done'
        elif self.last_run is not None and self.last_run['state'] == 'aborted':
            txt = 'Abort'
        else:
            txt = 'Ready'
        return txt, round(self.vc * 6e7, 2)

    def read(self, visibility='agent'):
        pc = self.plant is not None
        txt, vx = self._display()
        on = pc and self._mains() and self.on
        r = self.run
        prog = dict(run_id=r['run_id'], step=r['step'], steps=len(r['method']['steps']),
                    mode=r['method']['steps'][min(r['step'], len(r['method']['steps']) - 1)]['mode'], step_elapsed_s=r['step_t'], elapsed_s=r['total_t'],
                    state=r['state']) if r is not None else dict(run_id=None, state='idle')
        out = {'clock.time_s': self.t, 'plant.configured': pc,
               'config.summary': dict(version=self.plant['version'], channel=self.plant['channel'], carrier=self.plant['carrier'], detectors=list(self.plant['detectors']))
               if pc else {},
               'power.on': self.on, 'channel.top_lift_m': self.lift, 'channel.open': self._open(), 'membrane.installed': self.membrane,
               'spacer.thickness_m': self.spacer, 'method.summary': copy.deepcopy(self.method), 'sample.loaded': self._sample_public(),
               'run.state': r['state'] if r is not None else 'idle', 'run.progress': prog, 'flow.cross_set_m3_s': self.vc,
               'flow.cross_measured_m3_s': (self.vc * (1.0 + ENG['flow_meter_noise_rel'] * _gauss(self.seed, self.tick, 5)) if on else None),
               'flow.detector_m3_s': self.vout, 'pressure.channel_pa': self._p_meas(self.p, self.t) if pc else None,
               'detector.uv_au': self.det['uv'] if on else None, 'detector.dri_riu': self.det['dri'] if on else None,
               'detector.ls90_per_m': self.det['ls'] if on else None,
               'calibration.thickness_m': self.w_cal, 'display.text': txt, 'display.cross_flow_ml_min': vx,
               'alarm.state': (self.alarm if self.alarm else ('no_mains' if pc and not self._mains() else None))}
        if visibility != 'agent':
            g = self._geom() if pc and self.spacer else None
            out['truth.process'] = dict(
                thickness_true_m=self._w_true() if self.spacer else None, viscosity_pa_s=self._eta() if pc else None,
                void_volume_m3=g.V0 if g else None, pressure_true_pa=self.p,
                zones=[dict(species=z.species, rh_m=z.rh, weight=z.weight, x_m=z.x, sigma_m=math.sqrt(max(z.var, 0.0)), mass_in_channel_kg=z.m,
                            eluted_kg=z.eluted, permeated_kg=z.lost, void=z.void, relax_s=z.relax_s) for z in self.zones],
                sample_composition=({k: v for k, v in self.loaded['components'].items()} if self.loaded else None),
                provided_samples={k: dict(v) for k, v in self.provided.items()}) if pc else {}
            out['truth.faults'] = sorted(self.faults)
        return out

    def _ledger(self):
        L = dict(self.led)
        L['in_channel_kg'] = sum(z.m for z in self.zones)
        s = self.loaded
        L['in_loop_kg'] = sum(s['components'].values()) * (s['volume_m3'] - s['injected_m3']) if s else 0.0
        L['injected_balance_kg'] = L['injected_kg'] - L['eluted_kg'] - L['permeated_kg'] - L['flushed_kg'] - L['in_channel_kg']
        L['loop_balance_kg'] = L['loaded_kg'] - L['injected_kg'] - L['unloaded_kg'] - L['in_loop_kg']
        return L


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o


# ---------------------------------------------------------------- session (agent / evaluator isolation, idempotency)
class WyattEclipseaf4Session:
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
        meta = copy.deepcopy(export_wyatt_eclipseaf4_contract_metadata())
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

    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id)
        h = self._host
        values = _jsonable(h.read(self._role))
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
            h._caller = self._principal
            snapshot = copy.deepcopy({k: v for k, v in h.__dict__.items() if k not in ('_principals', '_requests', '_caller')})
            try:
                result = _jsonable(h.invoke(capability_id, **arguments))
            except ActionRejected:
                h.__dict__.update(snapshot)                                   # rejected: nothing changed
                raise
            except Exception:                                                 # noqa: BLE001  (any internal error: roll back, report)
                h.__dict__.update(snapshot)
                raise ActionRejected('INTERNAL_ERROR')
            finally:
                h._caller = None
            if self._role == 'agent' and capability_id == 'events.read':
                result = dict(events=[e for e in result['events'] if e['kind'] not in ('fault', 'truth')], dropped=result['dropped'])
            response = dict(protocol_version=PROTOCOL_VERSION, instance_id=instance_id, request_id=request_id, operation_id=None, status='succeeded',
                            state_revision=h.revision, clock={'domain': 'simulation', 'time_s': h.time_s}, result=copy.deepcopy(result), observations={}, error=None)
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
        raise ActionRejected('UNKNOWN_OPERATION')

    def cancel(self, operation_id, request_id):
        raise ActionRejected('UNKNOWN_OPERATION')


# ---------------------------------------------------------------- contract metadata
ERROR_ZH = {
    'INVALID_ARGUMENT': '参数不符合输入模式或取值范围', 'REQUEST_CONFLICT': '同一request_id对应不同的调用', 'UNKNOWN_INSTANCE': '实例不存在',
    'UNKNOWN_CAPABILITY': '能力不存在', 'UNSUPPORTED': '本型号或本配置没有该功能', 'ACCESS_DENIED': '无权访问（真值或评测者能力）',
    'UNKNOWN_OPERATION': '会话不支持该操作（本运行层的能力都是同步的，运行由宿主时钟推进）', 'INTERNAL_ERROR': '运行层内部错误（已回滚）',
    'PLANT_REQUIRED': '尚未声明环境与配置', 'SEED_FIXED': '噪声种子只能在首次声明时设定', 'SOLVENT_INCOMPATIBLE': '溶剂与版本不匹配（标准版只用水相/醇、pH 2–12；有机溶剂需有机版）',
    'MEMBRANE_INCOMPATIBLE': '有机相版本不能用PES膜', 'NO_MAINS': '停电', 'POWER_OFF': '控制单元未开机', 'RUN_ACTIVE': '运行中不能这样做',
    'FLOW_ACTIVE': '通道有流动或压力，不能打开顶板', 'CHANNEL_CLOSED': '顶板闭合（先抬起顶板）', 'CHANNEL_OPEN': '顶板未压回', 'MEMBRANE_PRESENT': '已有膜（先取下）',
    'SPACER_PRESENT': '已有垫片（先取出）', 'CHANNEL_NOT_ASSEMBLED': '通道缺膜或垫片', 'INVALID_METHOD': '方法不合法（最多一个聚焦+进样步；开始洗脱后不能再聚焦；聚焦需要聚焦流量）',
    'FLOW_LIMIT': '横流+检测器流量超过泵的最大流量', 'UNKNOWN_TEMPLATE': '模板库中没有该物质', 'UNKNOWN_SAMPLE': '没有这个盲样编号', 'SAMPLE_PRESENT': '进样环中还有未注入的样品',
    'NO_METHOD': '尚未设定方法', 'PUMP_FAILURE': '泵故障', 'NO_SAMPLE': '方法含聚焦+进样步但未装样', 'NO_DETECTOR': '没有接该检测器', 'NO_RUN': '还没有运行记录',
    'CALIBRATION_OUT_OF_RANGE': '该保留时间无法用合理通道厚度（名义值的40–110 %）解释',
    'RUN_CONDITIONS_CHANGED': '上一次运行用的是另一个垫片或通道（换件后先用标准物重新运行）',
}
_P = ['PLANT_REQUIRED']
_ON = _P + ['NO_MAINS', 'POWER_OFF']
CAP_ERRORS = {
    'plant.configure': ['SEED_FIXED', 'SOLVENT_INCOMPATIBLE', 'MEMBRANE_INCOMPATIBLE', 'RUN_ACTIVE'], 'power.set': _P + ['NO_MAINS'], 'power.toggle': _P + ['NO_MAINS'],
    'channel.set_lift': _P + ['FLOW_ACTIVE'], 'membrane.install': _P + ['CHANNEL_CLOSED', 'MEMBRANE_PRESENT', 'MEMBRANE_INCOMPATIBLE'],
    'membrane.remove': _P + ['CHANNEL_CLOSED'], 'spacer.install': _P + ['CHANNEL_CLOSED', 'SPACER_PRESENT'], 'spacer.remove': _P + ['CHANNEL_CLOSED'],
    'method.set': _ON + ['RUN_ACTIVE', 'INVALID_METHOD', 'FLOW_LIMIT'],
    'sample.load': _P + ['RUN_ACTIVE', 'SAMPLE_PRESENT', 'UNKNOWN_TEMPLATE', 'UNKNOWN_SAMPLE', 'SOLVENT_INCOMPATIBLE'],
    'sample.unload': _P + ['RUN_ACTIVE'], 'sample.provide': _P + ['UNKNOWN_TEMPLATE'],
    'run.start': _ON + ['RUN_ACTIVE', 'NO_METHOD', 'CHANNEL_OPEN', 'CHANNEL_NOT_ASSEMBLED', 'PUMP_FAILURE', 'NO_SAMPLE'], 'run.abort': _P,
    'fractogram.read': [], 'peaks.analyze': _P + ['NO_DETECTOR'], 'channel.calibrate_thickness': _P + ['NO_RUN', 'CHANNEL_NOT_ASSEMBLED', 'RUN_CONDITIONS_CHANGED', 'UNKNOWN_TEMPLATE', 'CALIBRATION_OUT_OF_RANGE'],
    'events.read': [], 'truth.inspect': _P, 'ledger.read': [], 'fault.inject': _P, 'sim.step': [],
}
_COMMON_ERRORS = ['INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE', 'INTERNAL_ERROR']
PRECONDITIONS = {
    'plant.configure': ('首次声明时设定噪声种子（之后不可改）；载液与版本匹配（标准版水相pH 2–12，有机溶剂需有机版且不用PES膜）；运行中不可改', ['plant.configured', 'membrane.installed', 'run.state']),
    'power.set': ('有电', ['power.on']), 'power.toggle': ('有电', ['power.on']),
    'channel.set_lift': ('打开时须无流动且通道泄压（运行中拒绝）；打开会冲掉通道内残留样品', ['channel.top_lift_m', 'run.state', 'pressure.channel_pa']),
    'membrane.install': ('顶板打开且没有膜；有机版不能用PES', ['channel.open', 'membrane.installed']), 'membrane.remove': ('顶板打开', ['channel.open']),
    'spacer.install': ('顶板打开且没有垫片', ['channel.open', 'spacer.thickness_m']), 'spacer.remove': ('顶板打开', ['channel.open']),
    'method.set': ('开机且不在运行中；最多一个聚焦+进样步，洗脱开始后不再聚焦；流量不超过泵上限', ['power.on', 'run.state']),
    'sample.load': ('不在运行中；进样环为空；样品介质与版本一致（水相/有机相）', ['run.state', 'sample.loaded']),
    'sample.unload': ('不在运行中', ['run.state', 'sample.loaded']), 'sample.provide': ('无', ['plant.configured']),
    'run.start': ('开机、不在运行中、已设方法、顶板闭合、已装膜与垫片、泵正常；方法含聚焦+进样步时须已装样', ['power.on', 'run.state', 'method.summary', 'channel.open',
                                                                                            'membrane.installed', 'spacer.thickness_m', 'sample.loaded']),
    'run.abort': ('无（不在运行中时为空操作）', ['run.state']), 'peaks.analyze': ('有运行记录；所选检测器在该次运行时已接；分析用该次运行的通道、垫片、载液与温度（标定厚度只在同一垫片/通道时使用）', ['run.state']),
    'channel.calibrate_thickness': ('有运行记录与垫片，且上一次运行用的是当前垫片与通道；保留时间可用名义厚度40–110 %解释', ['run.state', 'spacer.thickness_m']),
    'truth.inspect': ('无', ['plant.configured']), 'fault.inject': ('无', ['plant.configured']),
}
_SENS = ['flow.cross_set_m3_s', 'flow.cross_measured_m3_s', 'flow.detector_m3_s', 'pressure.channel_pa', 'detector.uv_au', 'detector.dri_riu', 'detector.ls90_per_m']
_DISP = ['display.text', 'display.cross_flow_ml_min', 'alarm.state']
_EVOLVE = _SENS + _DISP + ['run.state', 'run.progress', 'sample.loaded', 'truth.process']
EFFECTS = {
    'plant.configure': ['plant.configured', 'config.summary', 'truth.process', 'pressure.channel_pa'] + _DISP,
    'power.set': ['power.on', 'run.state', 'run.progress', 'truth.process'] + _DISP + _SENS,
    'power.toggle': ['power.on', 'run.state', 'run.progress', 'truth.process'] + _DISP + _SENS, 'channel.set_lift': ['channel.top_lift_m', 'channel.open', 'display.text', 'truth.process'],
    'membrane.install': ['membrane.installed'], 'membrane.remove': ['membrane.installed'], 'spacer.install': ['spacer.thickness_m', 'calibration.thickness_m', 'truth.process'],
    'spacer.remove': ['spacer.thickness_m', 'calibration.thickness_m', 'truth.process'], 'method.set': ['method.summary'], 'sample.load': ['sample.loaded', 'truth.process'],
    'sample.unload': ['sample.loaded', 'truth.process'], 'sample.provide': ['truth.process'], 'run.start': ['run.state', 'run.progress', 'display.text', 'alarm.state'],
    'run.abort': ['run.state', 'run.progress', 'truth.process'] + _DISP + _SENS, 'channel.calibrate_thickness': ['calibration.thickness_m'],
    'fault.inject': ['truth.faults', 'power.on', 'run.state', 'run.progress', 'truth.process'] + _DISP + _SENS, 'sim.step': ['clock.time_s'],
}
_STEPPED = ('run.start', 'power.set', 'power.toggle', 'fault.inject', 'run.abort')
ON_STEP = {k: list(_EVOLVE) for k in _STEPPED}
ON_STEP['sim.step'] = _EVOLVE + ['clock.time_s']
ON_STEP_ZH = {'sim.step': '按0.1 s网格积分：方法程序（横流、聚焦、进样）、通道压力、各谱带的迁移/展宽/膜渗透、出口浓度与检测器信号（经60 µL传输体积延迟）；'
                          '无流动且通道与传输管路中没有样品时整段跳过'}
EXEC_TEXT = {'run.start': ('运行在宿主时钟推进中完成（run.state=completed；中止为aborted）；本调用只启动', ['run.state'])}
RESOURCES = {k: ['host_clock'] for k in CAPS}
for _k in ('channel.set_lift', 'membrane.install', 'membrane.remove', 'spacer.install', 'spacer.remove', 'sample.load', 'sample.unload'):
    RESOURCES[_k] = ['host_clock', 'robot_or_operator']
OBSERVES = {'run.start': ['run.state', 'detector.dri_riu', 'detector.uv_au'], 'power.set': ['display.text'], 'channel.set_lift': ['channel.open'],
            'method.set': ['method.summary']}
_WB, _BR, _PP, _THEO, _LIB, _SIM = ('user_workbook', 'manufacturer_brochure', 'manufacturer_product_page', 'af4_theory_literature', 'af4_template_library',
                                    'simulation_assumption')
CAP_SOURCES = {k: [_BR, _SIM] for k in CAPS}
CAP_SOURCES.update({'plant.configure': [_BR, _WB, _SIM], 'sample.load': [_LIB, _BR, _SIM], 'sample.provide': [_LIB, _SIM], 'run.start': [_BR, _THEO, _LIB, _SIM],
                    'fractogram.read': [_THEO, _LIB, _SIM], 'peaks.analyze': [_THEO, _BR, _SIM], 'channel.calibrate_thickness': [_THEO, _LIB, _SIM],
                    'membrane.install': [_BR, _PP, _SIM], 'spacer.install': [_BR, _WB, _SIM], 'channel.set_lift': [_PP, _SIM], 'truth.inspect': [_SIM],
                    'ledger.read': [_SIM], 'fault.inject': [_BR, _SIM], 'sim.step': [_SIM], 'events.read': [_SIM]})
PARAM_ZH = {'ambient_k': '室温', 'pressure_pa': '大气压', 'mains_v': '电源电压', 'noise_seed': '噪声种子', 'version': '控制单元版本（水相/有机相）',
            'channel': '分析通道（长246 mm/短153 mm有效长）', 'carrier': '载液', 'detectors': '外接检测器', 'carrier_ph': '载液pH', 'on': '开或关',
            'lift_m': '顶板抬起高度', 'membrane': '膜类型', 'thickness_m': '垫片名义厚度', 'detector_flow_m3_s': '检测器流量', 'focus_flow_m3_s': '聚焦流量',
            'inject_flow_m3_s': '进样流量', 'steps': '方法步骤表', 'sample_id': '样品编号', 'volume_m3': '进样体积', 'components': '样品组分',
            'provided_id': '盲样编号', 'medium': '样品介质', 'since_s': '起始时间', 'max_rows': '最多行数', 'detector': '浓度检测器', 'dn_dc_m3_kg': '比折光指数增量',
            'min_height': '最小峰高', 'template_id': '标准物（模板库）', 'retention_time_s': '标准物实测保留时间', 'fault': '故障名', 'active': '注入或清除', 'dt_s': '推进时间'}
_DEFAULT_LIMIT = '过程层实现；机器人与硬件层未实现；整体模型边界见mechanisms.af4_fractionation_process.limitations。'
CAP_LIMITS = {
    'sample.load': ['样品组分只能来自模板库（每个组分一个流体力学半径与分布宽度）；不建模聚集、样品与膜的吸附/相互作用、离子强度对R_h的影响；进样体积按定量环/注射器处理，自动进样器本身不在本资产内。'],
    'run.start': ['运行由宿主时钟推进：Wahlund–Giddings空隙时间、含立体项的保留比R(λ)、非平衡板高、聚焦带宽、弛豫与膜渗透为模型；流量为方法设定值（泵与流量控制器的动态、流量计噪声只体现在读数）；'
                  '不建模过载引起的保留漂移（只按工程因子展宽）、二次弛豫、壁吸附与膜老化。'],
    'peaks.analyze': ['峰识别为平滑后的局部极大值与5 %边界；R_h由同一AF4模型在名义或标定厚度下反算（正常模式，立体模式只给出提示）；摩尔质量用峰顶MALS/dRI比值（P(θ)≈1），不是ASTRA的逐片Zimm拟合。'],
    'channel.calibrate_thickness': ['按Wyatt常用做法用已知R_h的标准物（如BSA）反算有效厚度；模型中真实厚度=名义×0.86（工程值），标定只修正分析，不改变物理。'],
    'fractogram.read': ['UV/dRI/MALS为外接检测器（本资产不含其几何）；响应系数为类别级假设（模板库detector_response）；噪声为种子化高斯白噪声，无基线漂移。'],
    'channel.set_lift': ['真机顶板由螺栓固定、拆装需工具；场景中以竖直滑移关节表示；打开会冲掉通道内残留样品（计入冲洗损失）。'],
}
LIMITATIONS = [
    '所选配置：Wyatt Eclipse AF4（W8100E一代控制单元）+长分析通道（246 mm e.l.，水相版）；运行层也支持短通道（153 mm）与有机相版本，但场景几何只画长通道。泵、自动进样器与UV/MALS/dRI检测器为外部模块（运行层计算其读数，几何不含）。',
    '通道几何：有效长度按厂家；梯形入口/出口宽、聚焦位置、有效厚度=名义垫片×0.86为工程值（真实值需用标准物标定）；膜渗透率、截留曲线（R_c由球蛋白尺寸换算，名义截留处截留99 %）、检测管路阻力为工程值。',
    'AF4理论：保留比R(λ)含立体项（Giddings），λ=D/(u0 w)；D由模板库R_h按Stokes–Einstein与载液黏度（水用Vogel式，THF/甲苯用Andrade近似）计算；非平衡板高χ(λ)用小λ展开并封顶于2/105；'
    '聚焦带宽1.5 mm、弛豫时间(w/u0)(1+2λ)、过载展宽为工程值；不建模二次弛豫、壁相互作用、溶质—膜吸附与离子强度效应。',
    '压力：通道压力=横流/(L_p A)+检测管路阻力×出口流量，2 s一阶滞后；超过30 bar（厂家）时软件停止流动并中止运行；泵与流量控制器的瞬态未建模。',
    '温度：通道在室温；温控室（4–90 °C）为选件，本配置不支持（temperature.set为unsupported）。',
    '检测器：UV 280 nm（1 cm）、dRI、MALS 90°（658 nm）的响应按模板库类别系数（假设）；颗粒与蛋白的Rg/Rh取0.775，线团取1.5；金属纳米粒dRI与MALS响应记0（未建模等离激元散射）。',
    '分析：peaks.analyze用同一模型在名义或已标定厚度下把保留时间换成R_h——与真实厚度不同时有系统偏差（这是真实做法需要厚度标定的原因）；分子量为峰顶比值近似。',
    'DualTec专属功能（tip-injection、HF5、通道切换、无金属流路）、半制备与frit-inlet通道（未安装）、馏分收集为unsupported；I/O信号、以太网与软件插件不建模。',
    '场景：控制单元（机箱、前面板与LCD、侧面8个接头、后面板）、通道支架、通道底座/膜/垫片/顶板（竖直滑移关节）与毛细管有几何；顶板关节由运行层channel.top_lift_m驱动；电源开关为弹簧按键；样品与液体没有几何。',
]


def _sampling_text(k):
    if k == 'clock.time_s':
        return 'Host clock.'
    if SLOTS[k]['origin'] in ('sensor', 'derived', 'process_truth'):
        return 'Updated on the 0.1 s grid of the host steps (detector values arrive after the 60 uL transfer volume).'
    return 'Changes when a command is accepted.'


def _errors_of(k):
    if k in UNSUPPORTED:
        return ['UNSUPPORTED'] + _COMMON_ERRORS
    extra = ['ACCESS_DENIED'] if CAPS[k]['visibility'] != 'agent' else []
    return list(dict.fromkeys(CAP_ERRORS.get(k, []) + _COMMON_ERRORS + extra))


def _pre_text(k):
    errs, text = CAP_ERRORS.get(k, []), PRECONDITIONS[k][0]
    if 'PLANT_REQUIRED' in errs and '声明' not in text:
        text = '已声明环境（否则PLANT_REQUIRED）；' + text
    return text


def _pre_slots(k):
    errs = CAP_ERRORS.get(k, [])
    ids = (['plant.configured'] if 'PLANT_REQUIRED' in errs else []) + list(PRECONDITIONS[k][1])
    return list(dict.fromkeys(ids))


def _model_effects(k, un):
    if un:
        return dict(mode='none', description_zh='不支持：无模型变化', binding_ids=[])
    seen = sorted((set(EFFECTS.get(k, [])) | set(ON_STEP.get(k, []))) & SCENE_MIRRORED)
    if seen:
        return dict(mode='host_required', binding_ids=[], description_zh='状态经宿主桥（Matterix）镜像到场景（' + '、'.join(seen) + '；绑定见合同model_bindings），运行层本身不驱动模型')
    return dict(mode='none', description_zh='过程或设定变化，没有对应的场景关节、读数或指示灯', binding_ids=[])


def _params():
    P = {}

    def doc(name, val, unit, meaning, src):
        P[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='documented', source_ids=list(src), uncertainty=None)

    def asm(name, val, unit, meaning, unc, src=(_SIM,)):
        P[name] = dict(value=val, unit=unit, meaning_zh=meaning, evidence='assumption', source_ids=list(src), uncertainty=unc)
    doc('channel_pressure_max_pa', DOC['channel_pressure_max_pa'], 'Pa', '通道最高压力30 bar，软件超压保护（原文：Maximum 30 bar, with software overpressure protection）', (_BR, _WB))
    doc('spacer_thicknesses_m', list(DOC['spacers_m']), 'm', '垫片厚度250、350、490 µm', (_BR, _WB))
    doc('effective_length_m', dict(DOC['effective_length_m']), 'm', '分析通道有效长：短153 mm、长246 mm', (_BR, _WB))
    doc('membranes', list(DOC['membranes']), '1', '预裁膜：PES 1/5/10/30 kDa；RC 10/30 kDa', (_BR,))
    doc('separation_range_m', list(DOC['separation_range_m']), 'm', '分离范围约1 nm至10 µm（依样品与膜）', (_BR, _WB))
    doc('analytical_load_kg', list(DOC['analytical_load_kg']), 'kg', '分析型通道1–100 µg进样', (_BR,))
    doc('control_unit_dimensions_m', list(DOC['control_unit_dimensions_m']), 'm', '控制单元64 (L)×36 (W)×25 (H) cm', (_BR, _WB))
    doc('control_unit_mass_kg', DOC['weight_kg'], 'kg', '20 kg（规格表Weight）', (_BR,))
    doc('solvent_ph_range', list(DOC['solvent_ph']), '1', '标准版耐腐蚀pH 2–12', (_BR,))
    notes = dict(breadth_inlet_m='梯形流道入口宽（工程值）', breadth_outlet_m='梯形流道出口宽（工程值）', focus_position_m='聚焦线（进样口）距入口距离（工程值）',
                 thickness_factor='有效厚度/名义垫片厚度（膜溶胀与压缩，工程值；需标准物标定）', membrane_permeability_m_pa_s='膜+烧结板水力渗透率L_p（工程值）',
                 cutoff_kda='膜名义截留分子量', sieving_exponent='筛分曲线陡度n（工程值；浓差极化使膜壁浓度为平均的1/λ倍，需陡峭截留才与蛋白回收率相符）', globular_radius_nm_per_da13='球蛋白R_h≈0.0668 nm×M^(1/3)（由RNase A等标准蛋白换算，工程值）；名义截留分子量处截留99 %（S=0.01）',
                 detector_line_resistance_pa_s_m3='检测器管路与流通池阻力（工程值）', pressure_tau_s='压力响应时间常数（工程值）', focus_band_sigma_m='聚焦带标准差（工程值）',
                 overload_mass_kg='过载阈值（取分析型进样上限100 µg）', transfer_volume_m3='通道出口到检测器的传输体积（工程值）', pump_max_flow_m3_s='外接泵最大流量10 mL/min（工程值）',
                 loop_max_volume_m3='最大进样体积500 µL（工程值）', flow_meter_noise_rel='横流流量计相对噪声（工程值）', pressure_noise_pa='压力传感器噪声（工程值）',
                 uv_noise_au='UV噪声', dri_noise_riu='dRI噪声', ls_noise_per_m='MALS 90°噪声', laser_wavelength_m='MALS激光波长658 nm（DAWN常用，工程取值）',
                 uv_path_m='UV光程1 cm', rg_over_rh='Rg/Rh形状比（线团1.5，致密0.775）', gauss_hermite_nodes='尺寸分布离散为7个Gauss–Hermite伪组分', overload_broadening='过载展宽系数（工程值）')
    for k, v in ENG.items():
        asm(k, v, {'breadth_inlet_m': 'm', 'breadth_outlet_m': 'm', 'focus_position_m': 'm', 'membrane_permeability_m_pa_s': 'm/(Pa s)', 'cutoff_kda': 'kDa',
                   'detector_line_resistance_pa_s_m3': 'Pa s/m3', 'pressure_tau_s': 's', 'focus_band_sigma_m': 'm', 'overload_mass_kg': 'kg', 'transfer_volume_m3': 'm3',
                   'pump_max_flow_m3_s': 'm3/s', 'loop_max_volume_m3': 'm3', 'pressure_noise_pa': 'Pa', 'ls_noise_per_m': '1/m', 'laser_wavelength_m': 'm',
                   'uv_path_m': 'm', 'globular_radius_nm_per_da13': 'nm'}.get(k, '1'), notes.get(k, k), '工程值，不是实测')
    asm('carriers', {k: {kk: vv for kk, vv in v.items() if kk != 'meaning_zh'} for k, v in CARRIERS.items()}, '1',
        '载液黏度关系：水用Vogel式η=A·10^(B/(T−C))（常用文献系数），THF/甲苯用25 °C黏度+Andrade活化能（工程值）；n0为折射率', '文献常数与工程近似')
    lib = library()
    asm('template_library', dict(file=LIBRARY_REL, sha256=lib['sha256'], counts=lib['doc']['counts']), '1',
        '分析物模板：流体力学半径、摩尔质量与分布宽度（NIST证书、Armstrong 2004、Erickson 2009、Wyatt手册）；检测器响应为类别假设；每条记录带证据与许可标记',
        '实测文献/证书数据（逐条许可标记）', (_LIB,))
    asm('boltzmann_j_k', KB, 'J/K', '玻尔兹曼常数', '物理常数')
    return P


SLOT_REFERENCE = {'pressure.channel_pa': '通道入口侧压力，相对大气压（表压）', 'flow.cross_measured_m3_s': '载液在通道温度下的实际体积流量',
                  'detector.uv_au': '280 nm、1 cm光程吸光度', 'detector.dri_riu': '相对载液的折射率差', 'detector.ls90_per_m': '658 nm、90°超额瑞利比'}


def export_wyatt_eclipseaf4_contract_metadata():
    slots, caps = {}, {}
    for k, v in SLOTS.items():
        slots[k] = dict(meaning_zh=v['meaning'], component_ids=[v['component']], value_schema=copy.deepcopy(v['schema']), unit=v['unit'],
                        reference_conditions=SLOT_REFERENCE.get(k), origin=v['origin'], visibility=v['visibility'], access='read_only',
                        read_binding=dict(backend='host', artifact_id='runtime', accessor=f'WyattEclipseaf4Session.read(state_ids=[{k!r}])'), write_via=None,
                        sampling=dict(mode='instant', time_domain='simulation', window_description=_sampling_text(k)), source_ids=['simulation_assumption'])
    for k, v in CAPS.items():
        un = k in UNSUPPORTED
        c = {key: copy.deepcopy(v[key]) for key in ['input_schema', 'output_schema', 'component_ids', 'function_zh', 'kind', 'visibility', 'example_arguments']}
        c.update(availability='unsupported' if un else 'implemented',
                 implementation={layer: 'absent' if un or layer in ('scene', 'robot', 'hardware') else 'implemented' for layer in ['process', 'scene', 'robot', 'hardware', 'host']},
                 binding=None if un else dict(backend='host', artifact_id='runtime', entrypoint='asset_runtime.wyatt_eclipseaf4.WyattEclipseaf4Session.invoke',
                                              invocation=f'session.invoke(instance_id="wyatt_eclipseaf4_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
                 preconditions=[] if un or k not in PRECONDITIONS else [dict(description_zh=_pre_text(k) + '；被拒时状态不变。', state_ids=_pre_slots(k),
                                                                              enforced_by='runtime', check='WyattEclipseaf4Runtime.invoke')],
                 state_effects=[dict(state_id=s, timing='on_step' if k == 'sim.step' else 'immediate', description_zh='设定或立即改变的量') for s in dict.fromkeys(EFFECTS.get(k, []))],
                 observes=[] if un else list(OBSERVES.get(k, [])), model_effects=_model_effects(k, un),
                 execution=dict(mode='synchronous', time_domain='simulation',
                                completion_criterion=EXEC_TEXT[k][0] if k in EXEC_TEXT else '原子设定、配置或查询（流动与样品在之后的step中演化）',
                                completion_state_ids=list(EXEC_TEXT[k][1]) if k in EXEC_TEXT else [], timeout_s=None, cancellation='unsupported', retry='same_request_id',
                                resources=list(RESOURCES.get(k, [])),
                                partial_failure_zh=('不支持：不执行，状态不变' if un else '同步原子操作：成功时一次写入全部效果，被拒时状态不变')),
                 errors=[dict(code=e, meaning_zh=ERROR_ZH.get(e, e), side_effects='none') for e in _errors_of(k)],
                 verification=[], limitations=[UNSUPPORTED[k]] if un else list(CAP_LIMITS.get(k, [_DEFAULT_LIMIT])), source_ids=list(CAP_SOURCES[k]))
        if k in ON_STEP:
            c['state_effects'] += [dict(state_id=s, timing='on_step', description_zh=ON_STEP_ZH.get(k, '随step演化')) for s in dict.fromkeys(ON_STEP[k])
                                   if not (k == 'sim.step' and s in EFFECTS.get(k, []))]
        caps[k] = c
    mechanism = dict(
        component_ids=['housing', 'channel_base', 'channel_top', 'membrane', 'spacer', 'injection_port', 'port_crossflow', 'port_detector_out', 'lcd_screen'],
        state_ids=list(slots), implementation='lumped',
        meaning_zh='非对称流场流分离：单泵供液，Eclipse把流量分成横流（穿过膜与烧结板）与出口流；样品在聚焦线聚焦弛豫，横流把分析物压向膜、扩散把它们托起，'
                   '平衡层厚度λw随扩散系数增大，在抛物线流速剖面中小粒子迁移快、先流出；出口流经检测器给出分离图',
        equations=['V0 = w A_T, A_T = (b0 + bL) L / 2;  Q(x) = V_out + V_c (1 - A(x)/A_T);  v(x) = Q(x) / (b(x) w)',
                   't0 = (V0 / V_c) ln[(V_out + V_c (1 - A(z_f)/A_T)) / V_out]  (Wahlund-Giddings void time)',
                   'D = k T / (6 pi eta(T) R_h);  lambda = D A_T / (V_c w);  R = 6a(1-a) + 6 lambda (1-2a)[coth((1-2a)/(2 lambda)) - 2 lambda/(1-2a)], a = R_h / w',
                   'dx/dt = R v(x);  d sigma^2 = chi(lambda) w^2 v / D dx, chi = min(24 lambda^3 (1 - 8 lambda + 60 lambda^2), 2/105)',
                   'S = 1 / (1 + (R_h / R_c)^10);  dm/dt = -S (u0 / w) m / (lambda (1 - e^(-1/lambda)))',
                   'p = V_c / (L_p A_T) + R_out V_out (tau 2 s);  p > 30 bar -> flows stop, run aborted',
                   'A280 = sum eps c l;  dRI = sum (dn/dc) c;  R90 = sum K* c M (dn/dc)^2 / (1 + q^2 Rg^2 / 3), K* = 4 pi^2 n0^2 / (lambda0^4 N_A)'],
        parameters=_params(), source_ids=[_SIM, _WB, _BR, _PP, _THEO, _LIB], limitations=list(LIMITATIONS))
    import importlib.util
    spec = importlib.util.spec_from_file_location('_wyatt_eclipseaf4_local_annotations', ROOT / 'scripts/asset_runtime/_annotations.py')
    ann = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ann)
    ann.KEY_ZH.update({k: v['meaning'] for k, v in SLOTS.items()})
    ann.PARAM_ZH.update(PARAM_ZH)
    ann.annotate_export('wyatt_eclipseaf4', caps, slots, {'af4_fractionation_process': mechanism},
                        inputs={'plant.configure': (PLANT, PLANT_EXAMPLE), 'method.set': (METHOD, METHOD_EXAMPLE), 'sample.load': (LOAD, LOAD_EXAMPLE)})
    for k, v in SLOTS.items():
        slots[k]['meaning_zh'] = v['meaning']
        if SLOT_REFERENCE.get(k):
            slots[k]['reference_conditions'] = SLOT_REFERENCE[k]
    return dict(asset_type_id=ASSET_TYPE_ID, catalog_id=CATALOG_ID, capabilities=caps, state_slots=slots, mechanisms={'af4_fractionation_process': mechanism})

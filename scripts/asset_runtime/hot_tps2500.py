"""CHEM-136 TPS2500S: explicit-clock Gaussian-plane thermal transport proxy.

This is an energy-normalized Gaussian plane heater in an infinite homogeneous
medium, not the manufacturer's finite spiral TPS D(tau), calibration or accuracy.
Only a host-created Session is public; Runtime exposes privileged simulator state.
"""
from __future__ import annotations
import copy
import importlib.util
import json
import math
from pathlib import Path
import numpy as np
from scipy.optimize import minimize_scalar

PROTOCOL_VERSION = '1.0.0'
SLUG = 'hot_tps2500'
ASSET_TYPE_ID = 'instrument.' + SLUG
CATALOG_ID = 'CHEM-136'
ROOT = Path(__file__).resolve().parents[2]
ASSET = ROOT / 'assets/expansion_batch04/t1_thermal' / SLUG
LIBRARY_PATH = ASSET / 'thermal_transport_library.json'

class ActionRejected(ValueError):
    """Atomic rejection before any state mutation."""

def number(unit='1', lo=None, hi=None):
    out = {'type': 'number', 'x-unit': unit}
    if lo is not None: out['minimum'] = lo
    if hi is not None: out['maximum'] = hi
    return out

def obj(fields):
    return dict(type='object', properties=fields, required=list(fields), additionalProperties=False)

def nullable(schema): return {'anyOf': [schema, {'type': 'null'}]}
def array(schema, minimum=0, maximum=20001): return dict(type='array', items=schema, minItems=minimum, maxItems=maximum)
BOOL = {'type': 'boolean'}
TEXT = {'type': 'string'}
PHASE = dict(type='string', enum=['idle', 'running', 'succeeded', 'cancelled', 'failed'])

def validate(schema, value):
    if 'anyOf' in schema:
        for option in schema['anyOf']:
            try:
                validate(option, value)
                return
            except ActionRejected: pass
        raise ActionRejected('INVALID_ARGUMENT')
    kind = schema.get('type')
    if kind in ('number', 'integer'):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value): raise ActionRejected('INVALID_ARGUMENT')
        if kind == 'integer' and type(value) is not int: raise ActionRejected('INVALID_ARGUMENT')
        if not schema.get('minimum', -math.inf) <= value <= schema.get('maximum', math.inf): raise ActionRejected('INVALID_ARGUMENT')
    elif kind == 'object':
        if not isinstance(value, dict) or set(value) != set(schema['properties']): raise ActionRejected('INVALID_ARGUMENT')
        for key, item in value.items(): validate(schema['properties'][key], item)
    elif kind == 'array':
        if not isinstance(value, list) or not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', math.inf): raise ActionRejected('INVALID_ARGUMENT')
        for item in value: validate(schema['items'], item)
    elif kind == 'boolean' and type(value) is not bool: raise ActionRejected('INVALID_ARGUMENT')
    elif kind == 'string' and not isinstance(value, str): raise ActionRejected('INVALID_ARGUMENT')
    elif kind == 'null' and value is not None: raise ActionRejected('INVALID_ARGUMENT')
    if 'enum' in schema and value not in schema['enum']: raise ActionRejected('INVALID_ARGUMENT')

def library():
    data = json.loads(LIBRARY_PATH.read_text(encoding='utf-8'))
    return {row['id']: row for row in data['templates']}

def gaussian_plane_rise(time_s, power_w, conductivity_w_m_k, diffusivity_m2_s, radius_m):
    """Center T for q(r,z)=P exp(-r²/a²)/(pi*a²)*delta(z).

    Integrating the 3-D heat kernel over radius/time yields atan. The heater
    normalization integrates to P, half of which enters each identical half-space.
    """
    t = np.maximum(np.asarray(time_s, dtype=float), 0.)
    return power_w / (2 * math.pi ** 1.5 * conductivity_w_m_k * radius_m) * np.arctan(2 * np.sqrt(diffusivity_m2_s * t) / radius_m)

def fit_gaussian_plane(time_s, temperature_rise_k, power_w, radius_m, minimum_diffusivity_m2_s, maximum_diffusivity_m2_s, accept_gaussian_plane_approximation):
    """Fit only supplied observations, with k from amplitude and alpha from shape."""
    if not accept_gaussian_plane_approximation: raise ActionRejected('ASSUMPTION_NOT_ACCEPTED')
    t = np.asarray(time_s, float); y = np.asarray(temperature_rise_k, float)
    if t.shape != y.shape or t.ndim != 1 or len(t) < 12 or np.any(np.diff(t) <= 0) or t[0] < 0 or not np.all(np.isfinite(t)) or not np.all(np.isfinite(y)) or np.ptp(y) < 1e-9 or power_w <= 0 or radius_m <= 0 or minimum_diffusivity_m2_s >= maximum_diffusivity_m2_s:
        raise ActionRejected('ANALYSIS_DATA')
    lo, hi = math.log(minimum_diffusivity_m2_s), math.log(maximum_diffusivity_m2_s)
    def evaluate(logalpha):
        curve = gaussian_plane_rise(t, power_w, 1., math.exp(logalpha), radius_m)
        design = np.column_stack([curve, np.ones(len(t))])
        coeff = np.linalg.lstsq(design, y, rcond=None)[0]
        residual = y - design @ coeff
        error = float(np.mean(residual ** 2)) if coeff[0] > 0 else 1e100
        return error, coeff, residual
    grid = np.linspace(lo, hi, 41); costs = [evaluate(x)[0] for x in grid]; best = int(np.argmin(costs))
    bounds = (grid[max(0, best - 1)], grid[min(len(grid) - 1, best + 1)])
    fit = minimize_scalar(lambda x: evaluate(x)[0], method='bounded', bounds=bounds, options={'xatol': 1e-12})
    error, coeff, residual = evaluate(float(fit.x))
    if not fit.success or error >= 1e99 or min(fit.x - lo, hi - fit.x) < 1e-5: raise ActionRejected('ANALYSIS_UNRESOLVED')
    alpha = math.exp(float(fit.x)); kappa = 1 / float(coeff[0]); tau_lo = 2 * math.sqrt(alpha * max(float(t[0]), 1e-15)) / radius_m; tau_hi = 2 * math.sqrt(alpha * float(t[-1])) / radius_m
    # Early sqrt(t) alone identifies k/sqrt(alpha), not k and alpha separately.
    if tau_hi < .3 or tau_lo > 5 or not .005 <= kappa <= 500: raise ActionRejected('ANALYSIS_UNRESOLVED')
    return dict(conductivity_w_m_k=kappa, diffusivity_m2_s=alpha, volumetric_heat_capacity_j_m3_k=kappa / alpha, fit_rms_k=math.sqrt(max(0., error)), offset_k=float(coeff[1]), points_used=len(t), evidence='derived_from_observations_gaussian_plane_approximation')

PLANT_EXAMPLE = dict(ambient_k=296.15, sensor_gain=1., sensor_offset_k=0., sensor_resolution_k=1e-7, maximum_rise_k=10., sensor_maximum_k=400., clamp_speed_m_s=.02, accept_engineering=True)
PLANT = obj({'ambient_k': number('K', 288.15, 303.15), 'sensor_gain': number('1', .1, 10), 'sensor_offset_k': number('K', -10, 10), 'sensor_resolution_k': number('K', 1e-10, .1), 'maximum_rise_k': number('K', .001, 100), 'sensor_maximum_k': number('K', 283.15, 1000), 'clamp_speed_m_s': number('m/s', .001, .1), 'accept_engineering': BOOL})
PROBE_EXAMPLE = dict(radius_m=.0064, connected=True, accept_gaussian_plane_approximation=True)
PROBE = obj({'radius_m': number('m', .0005, .03), 'connected': BOOL, 'accept_gaussian_plane_approximation': BOOL})
SAMPLE_EXAMPLE = dict(reference_id='stainless_steel_316l_hotdisk', half_thickness_m=.02, diameter_m=.06, density_kg_m3=8000., diffusivity_override_m2_s=None, accept_property_completion=False, accept_isotropic_approximation=True)
SAMPLE = obj({'reference_id': TEXT, 'half_thickness_m': number('m', .0005, 1), 'diameter_m': number('m', .002, 2), 'density_kg_m3': number('kg/m^3', 1, 50000), 'diffusivity_override_m2_s': nullable(number('m^2/s', 1e-8, 4e-4)), 'accept_property_completion': BOOL, 'accept_isotropic_approximation': BOOL})
METHOD_EXAMPLE = dict(power_w=.5, duration_s=1.5, sample_interval_s=.025, temperature_k=296.15, geometry_model='isotropic_bulk', accept_gaussian_plane_approximation=True)
METHOD = obj({'power_w': number('W', 1e-6, 20), 'duration_s': number('s', 1., 2560.), 'sample_interval_s': number('s', .0001, 2560.), 'temperature_k': number('K', 288.15, 303.15), 'geometry_model': {'type': 'string', 'enum': ['isotropic_bulk']}, 'accept_gaussian_plane_approximation': BOOL})
CALIBRATION_EXAMPLE = dict(gain_correction=1., offset_correction_k=0.)
CALIBRATION = obj({'gain_correction': number('1', .1, 10), 'offset_correction_k': number('K', -10, 10)})
FIT = obj({'time_s': array(number('s', 0), 12), 'temperature_rise_k': array(number('K'), 12), 'power_w': number('W', 1e-6, 20), 'radius_m': number('m', .0005, .03), 'minimum_diffusivity_m2_s': number('m^2/s', 1e-8, 4e-4), 'maximum_diffusivity_m2_s': number('m^2/s', 1e-8, 4e-4), 'accept_gaussian_plane_approximation': BOOL})
FIT_OUTPUT = obj({'conductivity_w_m_k': number('W/(m*K)', .005, 500), 'diffusivity_m2_s': number('m^2/s', 1e-8, 4e-4), 'volumetric_heat_capacity_j_m3_k': number('J/(m^3*K)', 0), 'fit_rms_k': number('K', 0), 'offset_k': number('K'), 'points_used': {'type': 'integer', 'minimum': 12, 'maximum': 20001, 'x-unit': '1'}, 'evidence': TEXT})
ROW = obj({'time_s': number('s', 0), 'temperature_rise_k': nullable(number('K')), 'temperature_k': nullable(number('K')), 'power_w': number('W', 0)})
RESULT = obj({'operation_id': nullable(TEXT), 'phase': PHASE, 'elapsed_s': number('s', 0), 'rows': array(ROW), 'fit': nullable(FIT_OUTPUT), 'template_source': nullable(TEXT), 'evidence': TEXT, 'error_code': nullable(TEXT)})
CAPS = {}; SLOTS = {}
def cap(key, schema, out, components, description, kind='device_command', visibility='agent', example=None):
    CAPS[key] = dict(input_schema=schema, output_schema=out, component_ids=components, function_zh=description, kind=kind, visibility=visibility, example_arguments=copy.deepcopy(example or {}), effects=[])
def slot(key, schema, unit, description, origin='derived', visibility='agent', component='controller'):
    SLOTS[key] = dict(schema=schema, unit=unit, meaning=description, origin=origin, visibility=visibility, component=component, write=None)

cap('plant.configure', PLANT, obj({'configured': BOOL}), ['controller'], '宿主显式配置室温边界及工程观测误差，不模拟外置温控炉', 'configuration', 'evaluator', PLANT_EXAMPLE)
cap('probe.configure', PROBE, obj({'configured': BOOL}), ['sensor_disc', 'probe_connector'], '安装探头并显式接受高斯平面热源半径近似', 'configuration', 'evaluator', PROBE_EXAMPLE)
cap('sample.load', SAMPLE, obj({'loaded': BOOL}), ['sample_lower', 'sample_upper'], '宿主装入同材质两半样品；缺失alpha须显式补充并标假设', 'configuration', 'evaluator', SAMPLE_EXAMPLE)
cap('sample.remove', obj({}), obj({'loaded': BOOL}), ['sample_lower', 'sample_upper'], '宿主移出样品，携走质量与残余热量', 'configuration', 'evaluator')
cap('power.set', obj({'enabled': BOOL}), obj({'enabled': BOOL}), ['power_switch'], '电子主机上电；断电取消当前测量并保留已注入热量', example={'enabled': True})
cap('fixture.set_clamp', obj({'closed': BOOL}), obj({'target_m': number('m', 0, .035)}), ['clamp_head', 'sample_holder'], '宿主手动移动夹头代理：只写目标，step推进；不声称仪器主动执行或接触力', kind='simulation', visibility='evaluator', example={'closed': True})
cap('fixture.set_hood', obj({'opened': BOOL}), obj({'target_m': number('m', 0, .23)}), ['hood'], '宿主手动移动保护罩代理；实际位移由step推进，不是主机主动机构', 'simulation', 'evaluator', {'opened': False})
cap('method.configure', METHOD, obj({'configured': BOOL}), ['controller'], '配置功率、采集时间和模型；设定不推进加热，温度必须等于外部室温边界', 'configuration', example=METHOD_EXAMPLE)
cap('sensor.calibrate', CALIBRATION, obj({'configured': BOOL}), ['controller', 'sensor_disc'], '输入独立观测增益/零点修正，不从隐藏真值自动校准', 'configuration', example=CALIBRATION_EXAMPLE)
cap('acquisition.start', obj({}), obj({'operation_id': TEXT}), ['controller', 'sensor_disc', 'sample_lower', 'sample_upper'], '开始异步恒功率温升采样；宿主step完成后从观测拟合k与alpha')
cap('acquisition.stop', obj({}), obj({'phase': PHASE, 'elapsed_s': number('s', 0)}), ['controller', 'sensor_disc'], '取消并冻结已采集温升；保留样品残热，停止继续注入功率')
cap('acquisition.result', obj({}), RESULT, ['controller'], '公开读取本次冻结观测、拟合量及通用模板库引用；完整选中来源须经evaluator provenance.read', 'query')
cap('provenance.read', obj({}), obj({'operation_id': nullable(TEXT), 'reference_id': nullable(TEXT), 'template_source': nullable(TEXT), 'evidence': TEXT}), ['controller'], '仅evaluator读取最近测量选中模板ID和完整来源；独立于公开结果的身份无关过滤', 'query', 'evaluator')
_fit_t = np.linspace(0, 5., 41).tolist()
FIT_EXAMPLE = dict(time_s=_fit_t, temperature_rise_k=gaussian_plane_rise(_fit_t, .5, 13.69, 3.705e-6, .0064).tolist(), power_w=.5, radius_m=.0064, minimum_diffusivity_m2_s=1e-8, maximum_diffusivity_m2_s=4e-4, accept_gaussian_plane_approximation=True)
cap('analysis.fit', FIT, FIT_OUTPUT, ['controller'], '独立外部温升数据拟合k、alpha及体积热容k/alpha；不读取样品真值', 'analysis', example=FIT_EXAMPLE)
cap('sim.step', obj({'dt_s': number('s', 0, 86400)}), obj({'time_s': number('s', 0)}), ['controller'], '宿主统一时钟与有限夹头运动、解析热响应、内部采样', 'simulation', 'evaluator', {'dt_s': .1})
LEDGER_UNITS = dict(sample_loaded_kg='kg', sample_present_kg='kg', sample_removed_kg='kg', mass_error_kg='kg', heater_injected_j='J', sample_stored_j='J', sample_removed_heat_j='J', energy_error_j='J')
cap('ledger.read', obj({}), obj({k: number(u) for k, u in LEDGER_UNITS.items()}), ['controller'], '核对两半样品质量与注入热量/残热/带出热量守恒', 'query', 'evaluator')
UNSUPPORTED = ['thermal.set_temperature', 'analysis.tps_oem', 'sample.anisotropy', 'sample.thin_film', 'heat_capacity.mass_specific', 'accuracy.certification', 'noise.empirical', 'robot.load', 'hardware.connect']
for key in UNSUPPORTED: cap(key, obj({}), obj({}), ['controller'], '未实现：' + key)
for key, comp in [('plant.configured','controller'),('probe.connected','probe_connector'),('sample.present','sample_lower'),('power.enabled','power_switch'),('fixture.closed','clamp_head'),('fixture.moving','clamp_head')]: slot(key, BOOL, '1', '配置或可观测场景状态', component=comp)
slot('fixture.position_m', number('m', 0, .035), 'm', '夹头工程位移；0为闭合、0.035为取样位置，不表示接触力', 'scene', component='clamp_head')
slot('fixture.target_m', number('m', 0, .035), 'm', '夹头目标高度，实际位置只随step演化', 'setpoint', component='clamp_head')
slot('hood.position_m', number('m', 0, .23), 'm', '保护罩宿主手动移动代理，0落下、0.23m提起；不表示驱动电机', 'scene', component='hood')
slot('hood.target_m', number('m', 0, .23), 'm', '保护罩宿主移动目标，实际位置仅随step推进', 'setpoint', component='hood')
slot('power.angle_rad', number('rad', 0, .28), 'rad', '电源开关工程姿态，28/100弧度为造型假设', 'scene', component='power_switch')
for key, unit, desc in [('method.power_w','W','恒功率设定；不是主机额定耗电'),('method.duration_s','s','采集时间设定，厂商1至2560秒'),('method.temperature_k','K','外部样品温度登记，当前仅室温边界')]: slot(key, number(unit), unit, desc, 'setpoint')
slot('clock.time_s', number('s', 0), 's', '唯一宿主仿真时钟')
slot('acquisition.phase', PHASE, '1', '当前异步测量状态')
slot('acquisition.elapsed_s', number('s', 0), 's', '本次实际受热采集时间')
slot('acquisition.points', {'type':'integer','minimum':0,'maximum':20001,'x-unit':'1'}, '1', '实际内部采样点数')
slot('measurement.indicated_k', nullable(number('K')), 'K', '由工程增益、偏置、分辨率、外部校准产生的可见读数；断电/超模型域/饱和为null', 'sensor', component='sensor_disc')
for key, unit, desc in [('truth.sensor_k','K','解析中心温度，仅有效近场时间内'),('truth.conductivity_w_m_k','W/(m*K)','参考库输入k，不是拟合输出'),('truth.diffusivity_m2_s','m^2/s','参考库或显式补充alpha'),('truth.sample_mass_kg','kg','两半样品质量账本'),('truth.stored_heat_j','J','理想封闭样品累计注入热量；不假装无限域中心温度是均温')]: slot(key, nullable(number(unit)), unit, desc, 'process_truth', 'evaluator')
slot('model.fault', nullable(TEXT), '1', '模型域或观测故障代码')

class HotTps2500Runtime:
    def __init__(self, instance_id='tps2500_01'):
        self.instance_id = instance_id; self.plant = None; self.probe = None; self.sample = None; self.reference = None
        self.power = False; self.clamp = .035; self.clamp_target = .035; self.hood = .23; self.hood_target = .23; self.method = copy.deepcopy(METHOD_EXAMPLE); self.calibration = copy.deepcopy(CALIBRATION_EXAMPLE)
        self.time_ns = 0; self._clock_remainder_ns = 0.; self.revision = 0; self.phase = 'idle'; self.operation_id = None; self.elapsed = 0.; self.rows = []; self.fit = None; self.error = None; self.model_fault = None
        self._operation_start_ns = 0; self._next_sample = 0; self._operation_counter = 0; self._pulses = []; self._source = None; self._operation_reference_id = None; self._evidence = 'no_measurement'; self._mass_loaded = 0.; self._mass_removed = 0.; self._heat_input = 0.; self._heat_removed = 0.; self._heat_stored = 0.
        self._principals = {}; self._requests = {}; self._owners = {}; self._cancel_operations = {}; self.records = {}
    @property
    def time_s(self): return self.time_ns / 1e9
    def register_principal(self, principal_id, visibility='agent'):
        if not isinstance(principal_id, str) or not principal_id or principal_id in self._principals or visibility not in ('agent', 'debug', 'evaluator'): raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id] = visibility
    def session(self, principal_id):
        if principal_id not in self._principals: raise ActionRejected('ACCESS_DENIED')
        return HotTps2500Session(self, principal_id)
    def describe(self): return export_hot_tps2500_contract_metadata()
    def _moving(self): return abs(self.clamp - self.clamp_target) > 1e-12
    def _mass(self): return self.sample['density_kg_m3'] * math.pi * (self.sample['diameter_m'] / 2) ** 2 * 2 * self.sample['half_thickness_m'] if self.sample else 0.
    def _domain_time(self):
        if not self.sample or not self.probe: return 0.
        clearance = min(self.sample['half_thickness_m'], self.sample['diameter_m'] / 2 - 3 * self.probe['radius_m'])
        return max(clearance, 0.) ** 2 / (16 * self.sample['alpha'])
    def _rise(self, now_ns):
        if not self.sample or not self.probe: return None
        total = 0.
        for pulse in self._pulses:
            age = (now_ns - pulse['start_ns']) / 1e9
            if age > self._domain_time() + 1e-9: return None
            total += float(gaussian_plane_rise(age, pulse['power_w'], self.sample['kappa'], self.sample['alpha'], self.probe['radius_m']))
            if pulse['end_ns'] is not None:
                total -= float(gaussian_plane_rise((now_ns - pulse['end_ns']) / 1e9, pulse['power_w'], self.sample['kappa'], self.sample['alpha'], self.probe['radius_m']))
        return total
    def _observation(self, now_ns):
        rise = self._rise(now_ns)
        if not self.power or not self.plant or not self.probe or not self.probe['connected'] or rise is None: return None, None
        p, c = self.plant, self.calibration
        signal = (rise * p['sensor_gain'] + p['sensor_offset_k'] - c['offset_correction_k']) * c['gain_correction']
        if p['ambient_k'] + signal > p['sensor_maximum_k']: return None, None
        q = p['sensor_resolution_k']; signal = float(round(signal / q) * q)
        return p['ambient_k'] + signal, signal
    def read(self, role='evaluator'):
        if role not in ('agent', 'debug', 'evaluator'): raise ActionRejected('ACCESS_DENIED')
        indicated, _ = self._observation(self.time_ns); rise = self._rise(self.time_ns)
        values = {'plant.configured': self.plant is not None, 'probe.connected': self.probe is not None and self.probe['connected'], 'sample.present': self.sample is not None, 'power.enabled': self.power, 'fixture.closed': self.clamp <= 1e-12 and self.clamp_target == 0., 'fixture.moving': self._moving(), 'fixture.position_m': self.clamp, 'fixture.target_m': self.clamp_target, 'hood.position_m': self.hood, 'hood.target_m': self.hood_target, 'power.angle_rad': .28 if self.power else 0., 'method.power_w': self.method['power_w'], 'method.duration_s': self.method['duration_s'], 'method.temperature_k': self.method['temperature_k'], 'clock.time_s': self.time_s, 'acquisition.phase': self.phase, 'acquisition.elapsed_s': self.elapsed, 'acquisition.points': len(self.rows), 'measurement.indicated_k': indicated, 'truth.sensor_k': self.plant['ambient_k'] + rise if self.plant and rise is not None else None, 'truth.conductivity_w_m_k': self.sample['kappa'] if self.sample else None, 'truth.diffusivity_m2_s': self.sample['alpha'] if self.sample else None, 'truth.sample_mass_kg': self._mass() if self.sample else None, 'truth.stored_heat_j': self._heat_stored, 'model.fault': self.model_fault}
        return {key: value for key, value in values.items() if role != 'agent' or SLOTS[key]['visibility'] == 'agent'}
    def _ledger(self):
        return dict(sample_loaded_kg=self._mass_loaded, sample_present_kg=self._mass(), sample_removed_kg=self._mass_removed, mass_error_kg=self._mass_loaded-self._mass()-self._mass_removed, heater_injected_j=self._heat_input, sample_stored_j=self._heat_stored, sample_removed_heat_j=self._heat_removed, energy_error_j=self._heat_input-self._heat_stored-self._heat_removed)
    def _result(self): return dict(operation_id=self.operation_id, phase=self.phase, elapsed_s=self.elapsed, rows=copy.deepcopy(self.rows), fit=copy.deepcopy(self.fit), template_source=self._source, evidence=self._evidence, error_code=self.error)
    def _finish(self, phase, code=None):
        if self._pulses and self._pulses[-1]['end_ns'] is None: self._pulses[-1]['end_ns'] = self.time_ns
        self.phase = phase; self.error = code
        if self.operation_id: self.records[self.operation_id] = self._result()
    def invoke(self, capability, **arguments):
        if capability not in CAPS: raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability]['input_schema'], arguments)
        if capability in UNSUPPORTED: raise ActionRejected('UNSUPPORTED')
        a = arguments
        if capability == 'analysis.fit': return fit_gaussian_plane(**a)
        if capability == 'acquisition.result': return self._result()
        if capability == 'provenance.read': return dict(operation_id=self.operation_id, reference_id=self._operation_reference_id, template_source=self._source, evidence=self._evidence)
        if capability == 'ledger.read': return self._ledger()
        if capability == 'sim.step': self.step(a['dt_s']); return {'time_s': self.time_s}
        if self.phase == 'running' and capability not in ('acquisition.stop','power.set'): raise ActionRejected('BUSY')
        if capability == 'plant.configure':
            if self.plant is not None or self.sample or not a['accept_engineering']: raise ActionRejected('PRECONDITION_FAILED')
            if a['sensor_maximum_k'] <= a['ambient_k']: raise ActionRejected('INVALID_ARGUMENT')
            self.plant = copy.deepcopy(a); self.method['temperature_k'] = a['ambient_k']; result = {'configured': True}
        elif capability == 'probe.configure':
            if self.sample or not a['accept_gaussian_plane_approximation']: raise ActionRejected('PRECONDITION_FAILED')
            self.probe = copy.deepcopy(a); result = {'configured': True}
        elif capability == 'sample.load':
            if not self.plant or not self.probe or self.sample or self.hood < .23-1e-12 or self.hood_target != .23 or self.clamp < .035-1e-12 or self._moving() or not a['accept_isotropic_approximation']: raise ActionRejected('PRECONDITION_FAILED')
            references = library(); record = references.get(a['reference_id'])
            if record is None: raise ActionRejected('UNKNOWN_REFERENCE')
            kappa = record.get('thermal_conductivity_W_m_K'); alpha = record.get('thermal_diffusivity_m2_s'); reference_t = record.get('reference_temperature_K')
            if not isinstance(reference_t, (int,float)) or abs(reference_t - self.plant['ambient_k']) > .05: raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
            completed = alpha is None
            if completed:
                if a['diffusivity_override_m2_s'] is None or not a['accept_property_completion']: raise ActionRejected('MISSING_REFERENCE_PROPERTY')
                alpha = a['diffusivity_override_m2_s']
            elif a['diffusivity_override_m2_s'] is not None: raise ActionRejected('REFERENCE_OVERRIDE_FORBIDDEN')
            if isinstance(kappa, bool) or not isinstance(kappa, (int,float)) or not math.isfinite(kappa) or not .005 <= kappa <= 500 or not math.isfinite(alpha) or not 1e-8 <= alpha <= 4e-4: raise ActionRejected('REFERENCE_RANGE')
            if a['diameter_m'] / 2 <= 3 * self.probe['radius_m']: raise ActionRejected('SAMPLE_GEOMETRY')
            new = copy.deepcopy(a); new.update(kappa=kappa, alpha=alpha, cv=kappa/alpha, completed=completed)
            self.sample = new; self.reference = copy.deepcopy(record); self._mass_loaded += self._mass(); self._pulses = []; self.model_fault = None; result = {'loaded': True}
        elif capability == 'sample.remove':
            if not self.sample or self.hood < .23-1e-12 or self.hood_target != .23 or self._moving() or self.clamp < .035-1e-12: raise ActionRejected('PRECONDITION_FAILED')
            self._mass_removed += self._mass(); self._heat_removed += self._heat_stored; self._heat_stored = 0.; self.sample = None; self.reference = None; self._pulses = []; self.model_fault = None; result = {'loaded': False}
        elif capability == 'power.set':
            if not a['enabled'] and self.phase == 'running': self._finish('cancelled', 'POWER_OFF')
            self.power = a['enabled']; result = {'enabled': self.power}
        elif capability == 'fixture.set_clamp':
            if not self.plant or self.hood < .23-1e-12 or self.hood_target != .23 or a['closed'] and (not self.sample or not self.probe): raise ActionRejected('PRECONDITION_FAILED')
            self.clamp_target = 0. if a['closed'] else .035; result = {'target_m': self.clamp_target}
        elif capability == 'fixture.set_hood':
            if not self.plant: raise ActionRejected('PRECONDITION_FAILED')
            if not a['opened'] and (self.clamp > 1e-12 or self._moving()): raise ActionRejected('PRECONDITION_FAILED')
            self.hood_target = .23 if a['opened'] else 0.; result = {'target_m': self.hood_target}
        elif capability == 'method.configure':
            if not self.plant or not a['accept_gaussian_plane_approximation']: raise ActionRejected('PRECONDITION_FAILED')
            if abs(a['temperature_k'] - self.plant['ambient_k']) > 1e-9: raise ActionRejected('EXTERNAL_TEMPERATURE_BOUNDARY')
            points = a['duration_s'] / a['sample_interval_s']
            if points < 12 or points > 20000 or abs(points - round(points)) > 1e-8: raise ActionRejected('SAMPLE_GRID')
            if any(abs(a[key]*1e9-round(a[key]*1e9)) > 1e-4 for key in ('duration_s','sample_interval_s')): raise ActionRejected('SAMPLE_GRID')
            self.method = copy.deepcopy(a); result = {'configured': True}
        elif capability == 'sensor.calibrate': self.calibration = copy.deepcopy(a); result = {'configured': True}
        elif capability == 'acquisition.start':
            if not self.power or not self.plant or not self.sample or not self.probe or not self.probe['connected'] or self.hood > 1e-12 or self.hood_target != 0. or self.clamp > 1e-12 or self._moving(): raise ActionRejected('PRECONDITION_FAILED')
            if self._pulses: raise ActionRejected('RELOAD_REQUIRED')
            if self.method['duration_s'] > self._domain_time(): raise ActionRejected('FINITE_BOUNDARY_DOMAIN')
            predicted = float(gaussian_plane_rise(self.method['duration_s'], self.method['power_w'], self.sample['kappa'], self.sample['alpha'], self.probe['radius_m']))
            if predicted > self.plant['maximum_rise_k']: raise ActionRejected('SMALL_RISE_DOMAIN')
            self._operation_counter += 1; self.operation_id = f'{self.instance_id}:measurement:{self._operation_counter}'; self._operation_start_ns = self.time_ns; self._next_sample = 0; self.phase = 'running'; self.elapsed = 0.; self.rows = []; self.fit = None; self.error = None; self.model_fault = None
            self._pulses.append(dict(start_ns=self.time_ns, end_ns=None, power_w=self.method['power_w']))
            self._operation_reference_id = self.sample['reference_id']
            source = self.reference.get('source'); self._source = source if isinstance(source,str) else json.dumps(source, ensure_ascii=False, sort_keys=True)
            self._evidence = 'derived_assumption_host_completed_alpha' if self.sample['completed'] else 'real_property_template_gaussian_plane_approximation'
            result = {'operation_id': self.operation_id}
        elif capability == 'acquisition.stop':
            if self.phase != 'running': raise ActionRejected('NOT_RUNNING')
            self._finish('cancelled', 'USER_CANCEL'); result = {'phase': self.phase, 'elapsed_s': self.elapsed}
        else: raise ActionRejected('UNKNOWN_CAPABILITY')
        self.revision += 1
        return copy.deepcopy(result)
    def step(self, dt_s):
        validate(number('s', 0, 86400), dt_s)
        # Preserve sub-nanosecond host increments rather than rounding each
        # 1/60-s frame independently; sum(integer_ns)+remainder tracks host time.
        total_ns = math.fsum((self._clock_remainder_ns, dt_s * 1e9))
        ns = round(total_ns)
        self._clock_remainder_ns = total_ns - ns
        if ns == 0: return
        end_ns = self.time_ns + ns
        if self.plant:
            distance = self.plant['clamp_speed_m_s'] * (ns / 1e9)
            self.clamp += max(-distance, min(distance, self.clamp_target-self.clamp))
            self.hood += max(-distance, min(distance, self.hood_target-self.hood))
        if self.phase == 'running':
            stop_ns = self._operation_start_ns + round(self.method['duration_s'] * 1e9); active_end = min(end_ns, stop_ns); active_ns = active_end-self.time_ns
            self._heat_input += self.method['power_w'] * active_ns / 1e9; self._heat_stored += self.method['power_w'] * active_ns / 1e9
            interval = round(self.method['sample_interval_s'] * 1e9)
            while self._operation_start_ns + self._next_sample * interval <= active_end:
                at = self._operation_start_ns + self._next_sample * interval; temp, rise = self._observation(at)
                self.rows.append(dict(time_s=(at-self._operation_start_ns)/1e9, temperature_rise_k=rise, temperature_k=temp, power_w=self.method['power_w']))
                self._next_sample += 1
            self.time_ns = active_end; self.elapsed = (active_end-self._operation_start_ns)/1e9
            if active_end == stop_ns:
                if any(row['temperature_rise_k'] is None for row in self.rows): self._finish('failed','SENSOR_RANGE')
                else:
                    try:
                        self.fit = fit_gaussian_plane([row['time_s'] for row in self.rows], [row['temperature_rise_k'] for row in self.rows], self.method['power_w'], self.probe['radius_m'], 1e-8, 4e-4, True)
                        self._finish('succeeded')
                    except ActionRejected as exc: self._finish('failed',str(exc))
        self.time_ns = end_ns
        if self._pulses and self._rise(end_ns) is None: self.model_fault = 'POSTPULSE_FINITE_BOUNDARY_DOMAIN'
        self.revision += 1

class HotTps2500Session:
    def __init__(self, host, principal_id): self._host = host; self._principal = principal_id
    @property
    def _role(self): return self._host._principals[self._principal]
    def _target(self, instance_id):
        if instance_id != self._host.instance_id: raise ActionRejected('UNKNOWN_INSTANCE')
    def describe(self, *, instance_id=None, asset_type_id=None):
        if (instance_id is None) == (asset_type_id is None): raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None: self._target(instance_id)
        if asset_type_id is not None and asset_type_id != ASSET_TYPE_ID: raise ActionRejected('UNKNOWN_INSTANCE')
        meta = export_hot_tps2500_contract_metadata()
        return dict(protocol_version=PROTOCOL_VERSION, asset_type_id=ASSET_TYPE_ID, asset_version='1.0.0', capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'}, state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id); h=self._host; values=h.read(self._role); ids=list(values) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids): raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in values for k in ids): raise ActionRejected('ACCESS_DENIED')
        return dict(instance_id=instance_id, status='succeeded', state_revision=h.revision, clock={'domain':'simulation','time_s':h.time_s}, observations={k:dict(value=copy.deepcopy(values[k]),unit=SLOTS[k]['unit'],quality='unavailable' if values[k] is None else 'estimated' if SLOTS[k]['origin'] in ('sensor','derived') else 'valid',sample_time_s=h.time_s,sample_window_s=None) for k in ids})
    def _filter_result(self, result):
        result = copy.deepcopy(result)
        # A bridge may dispatch agent-visible capabilities through a privileged
        # session. Public acquisition payloads therefore use the SAME redaction
        # for every role. Full provenance has its own evaluator-only capability.
        if 'rows' in result and 'template_source' in result:
            result['template_source'] = str(LIBRARY_PATH.relative_to(ROOT)) + '; selected record provenance available only through evaluator provenance.read' if result['template_source'] is not None else None
            result['evidence'] = 'observed_temperature_trace_gaussian_plane_approximation; selected template details require evaluator provenance.read' if result['operation_id'] is not None else 'no_measurement'
        return result
    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        h=self._host
        try:
            self._target(instance_id)
            if not isinstance(capability_id,str) or capability_id not in CAPS: raise ActionRejected('UNKNOWN_CAPABILITY')
            if self._role=='agent' and CAPS[capability_id]['visibility']!='agent': raise ActionRejected('ACCESS_DENIED')
            if not isinstance(request_id,str) or not request_id: raise ActionRejected('INVALID_ARGUMENT')
            validate(CAPS[capability_id]['input_schema'],arguments); key=(self._principal,request_id); digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
            if key in h._requests:
                old,response=h._requests[key]
                if old!=digest: raise ActionRejected('REQUEST_CONFLICT')
                return copy.deepcopy(response)
            if capability_id in ('acquisition.stop','acquisition.result') and self._role=='agent' and h.operation_id and h._owners.get(h.operation_id)!=self._principal: raise ActionRejected('ACCESS_DENIED')
            if capability_id=='power.set' and not arguments['enabled'] and h.phase=='running' and self._role=='agent' and h._owners.get(h.operation_id)!=self._principal: raise ActionRejected('ACCESS_DENIED')
            result=self._filter_result(h.invoke(capability_id,**arguments)); operation=result.get('operation_id') if capability_id=='acquisition.start' else None
            if operation: h._owners[operation]=self._principal
            response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=operation,status='accepted' if operation else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None)
            h._requests[key]=(digest,copy.deepcopy(response)); return response
        except ActionRejected as exc:
            return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,status='unsupported' if str(exc)=='UNSUPPORTED' else 'rejected',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))
    def write(self, *, instance_id, state_id, value, request_id):
        self._target(instance_id); entry=SLOTS.get(state_id)
        if not entry or not entry['write']: raise ActionRejected('ACCESS_DENIED')
        capability,argument=entry['write']; return self.invoke(instance_id=instance_id,capability_id=capability,arguments={argument:value},request_id=request_id)
    def poll(self, operation_id):
        h=self._host
        if h._owners.get(operation_id)!=self._principal: raise ActionRejected('ACCESS_DENIED')
        result=h._result() if operation_id==h.operation_id and h.phase=='running' else h.records.get(operation_id)
        if result is None: raise ActionRejected('UNKNOWN_OPERATION')
        return dict(operation_id=operation_id,status=result['phase'],result=self._filter_result(result))
    def cancel(self, operation_id, request_id):
        self.poll(operation_id); h=self._host
        if not isinstance(request_id,str) or not request_id: raise ActionRejected('INVALID_ARGUMENT')
        key=(self._principal,request_id)
        if key in h._cancel_operations:
            if h._cancel_operations[key]!=operation_id: raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(h._requests[key][1])
        if key in h._requests: raise ActionRejected('REQUEST_CONFLICT')
        if operation_id!=h.operation_id or h.phase!='running': raise ActionRejected('NOT_RUNNING')
        result=self.invoke(instance_id=h.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        if result['status']=='succeeded': h._cancel_operations[key]=operation_id
        return result

EFFECTS = {
 'plant.configure':['plant.configured','method.temperature_k'], 'probe.configure':['probe.connected'],
 'sample.load':['sample.present','truth.sensor_k','truth.conductivity_w_m_k','truth.diffusivity_m2_s','truth.sample_mass_kg','measurement.indicated_k','model.fault'],
 'sample.remove':['sample.present','truth.sensor_k','truth.conductivity_w_m_k','truth.diffusivity_m2_s','truth.sample_mass_kg','truth.stored_heat_j','measurement.indicated_k','model.fault'],
 'power.set':['power.enabled','power.angle_rad','measurement.indicated_k','acquisition.phase'],
 'fixture.set_clamp':['fixture.target_m','fixture.closed','fixture.moving'],
 'fixture.set_hood':['hood.target_m'],
 'method.configure':['method.power_w','method.duration_s','method.temperature_k'],
 'sensor.calibrate':['measurement.indicated_k'],
 'acquisition.start':['acquisition.phase','acquisition.elapsed_s','acquisition.points','model.fault'],
 'acquisition.stop':['acquisition.phase'], 'sim.step':list(SLOTS)}
for key,effects in EFFECTS.items(): CAPS[key]['effects']=effects
ERRORS = sorted(set(__import__('re').findall(r"ActionRejected\('([A-Z_0-9]+)'",Path(__file__).read_text())) | {'POWER_OFF','USER_CANCEL','SENSOR_RANGE'})

def export_hot_tps2500_contract_metadata():
    slots={}; caps={}
    for key, value in SLOTS.items():
        slots[key]=dict(meaning_zh=value['meaning'],component_ids=[value['component']],value_schema=copy.deepcopy(value['schema']),unit=value['unit'],reference_conditions='SI; isotropic twin bulk sample, room temperature matched to public reference within0.05K; thermal readings are engineering estimates.',origin=value['origin'],visibility=value['visibility'],access='read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'HotTps2500Session.read(state_ids=[{key!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description='Host nanosecond clock; reads do not advance time. Internal acquisition samples retain their own operation-relative times.'),source_ids=['simulation_assumption'])
    limitations=[
      '高斯平面热源解析近似，不是厂家镍螺旋TPS D(tau)、ISO方法实现或优于5%准确度认证；功率/探头半径/机械速度/观测误差范围为工程假设。',
      '仅厂家电子主机15至30摄氏度室温范围、两个同材质同尺寸各向同性体样；0.5mm厚和2mm宽是厂家最小规格，但本模型另外要求3r加扩散距离的尺寸边界，不能据最小尺寸保证任意探头可用。',
      '样品参考温度与室温边界必须相差不超过0.05K；不外推公开材料物性，不模拟外置炉、低温附件、相变、对流、粉末接触网络、液体或真实夹持力。',
      'k从真实材料模板，alpha缺失时只能由宿主显式提供并标假设；Cv采用k/alpha闭合，原文独立Cp/rho/alpha可能有圆整冲突；不输出未经独立密度证据支持的质量比热。',
      '加热场仅在4sqrt(alpha*t)<=min(半样厚度,半径-3探头半径)的近场窗口有效；窗口外中心温度读数为null；有限样品热量账本仍保存全部注入能量。',
      '每次装样只运行一个脉冲；取消/完成后可解析冷却但不能把有残热样品重新当作均匀基温；须移出样品并由宿主重新初始化，带出的质量/热量被记账。',
      '显示经显式增益、零点、量化、校准与饱和判定；没有真实噪声分布或电桥时间响应标定；拟合仅用公开观测，不读取隐藏k/alpha。',
      '公开acquisition.result/poll在所有身份下都隐藏选中记录来源，防止bridge以evaluator代调用时泄漏；仅evaluator provenance.read返回完整来源。',
      '会话身份由宿主注册，过滤API与操作归属；同进程Python对象、模板文件及共享可读工作区不构成OS安全隔离。',
      '厂家两小时暖机与长期温漂未建模；假定宿主在ready任务环境配置本机，power.set不表示真实开机立即满足准确度。',
      '机器人、真实硬件、接触动力学、校准实验和厂家误差验证not_run；场景绑定状态由独立验证记录补充。']
    for key,value in CAPS.items():
        un=key in UNSUPPORTED; async_=key=='acquisition.start'
        caps[key]={k:copy.deepcopy(value[k]) for k in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']}
        caps[key].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ('scene','robot','hardware') else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.hot_tps2500.HotTps2500Session.invoke',invocation=f'session.invoke(instance_id="tps2500_01",capability_id={key!r},arguments={value["example_arguments"]!r},request_id="unique")'),preconditions=[] if un or value['kind'] in ('query','analysis') else [dict(description_zh='主机、探头、装样、夹持、来源温度、采样网格及近场/小温升前提由运行入口原子检查。',state_ids=['power.enabled','probe.connected','sample.present','fixture.closed','acquisition.phase'],enforced_by='runtime',check='HotTps2500Runtime.invoke validates all applicable guards before committing state')],state_effects=[dict(state_id=s,timing='immediate',description_zh='命令的实际即时状态变化') for s in value['effects']],observes=[],model_effects=dict(mode='none',description_zh='本运行模块仅输出数值状态；关节及面板映射由Matterix独立验证登记。',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='恒功率温升采集完成并从可见数据拟合k/alpha/Cv' if async_ else '实际输入通过原子校验并写入；热量与位置演化须等待宿主step',completion_state_ids=['acquisition.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','probe','sample_fixture'],partial_failure_zh='取消或断电冻结已采集记录，保留注入热量及样品质量；不回滚物理状态；未采样区间不会伪造数据。'),errors=[dict(code=e,meaning_zh=e,side_effects='partial' if async_ and e in ('POWER_OFF','USER_CANCEL','SENSOR_RANGE','ANALYSIS_UNRESOLVED') else 'none') for e in ERRORS],verification=[],limitations=limitations,source_ids=['manufacturer_primary','simulation_assumption'])
        if key in ('acquisition.start','fixture.set_clamp','fixture.set_hood'):
            caps[key]['state_effects'] += [dict(state_id=s,timing='on_step',description_zh='宿主step推进实际夹头位置或热响应、内部采样') for s in SLOTS if s not in value['effects'] and (s.startswith(('fixture.','hood.','truth.','measurement.','acquisition.')) or s in ('clock.time_s','model.fault'))]
    mechanism=dict(component_ids=['controller','sensor_disc','sample_lower','sample_upper','clamp_head'],state_ids=list(slots),meaning_zh='能量归一化高斯平面热源、定时观测和k/alpha独立拟合',implementation='lumped',equations=[
      'q(r,z)=P/(pi*a^2)*exp(-r^2/a^2)*delta(z); integrated source=P; each identical half-space receives P/2.',
      'DeltaT_center(t)=P/(2*pi^(3/2)*k*a)*atan(2*sqrt(alpha*t)/a), from convolution of infinite 3D heat kernel. Finite heating pulse uses difference of two step responses.',
      'Thermal penetration guard:4sqrt(alpha*t)<=min(half_thickness, sample_radius-3a). Gaussian radial tail beyond3a is exp(-9); this is an engineering near-field limit, not exact finite-boundary solution.',
      'Injected energy=sum(P*active_dt); sample_stored+sample_removed_heat=injected. Two-sample mass=rho*pi*R^2*(2*half_thickness). Removal exports current mass and accumulated sensible energy.',
      'Observed DeltaT=quantize((sensor_gain*true_DeltaT+sensor_offset-calibration_offset)*calibration_gain); saturation yields null. No random empirical noise is claimed.',
      'For external observed y(t), log(alpha) minimizes residual after linear least squares y=A*g(t,alpha,k=1)+offset; k=1/A and Cv=k/alpha. No hidden sample parameters accessed by inverse analysis.',
      'step(dt) accepts finite nonnegative floating host intervals and carries fractional-nanosecond residuals across frames; cumulative rounding avoids per-frame drift at60Hz. It alone advances integer-nanosecond host time; internal acquisition-grid samples are evaluated analytically and independent of host-step partition; power terminates exactly at duration.'],parameters={},source_ids=['simulation_assumption'],limitations=limitations)
    spec=importlib.util.spec_from_file_location('_tps_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py'); annotations=importlib.util.module_from_spec(spec); spec.loader.exec_module(annotations)
    annotations.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()})
    annotations.annotate_export(SLUG,caps,slots,{'gaussian_plane_transport':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'probe.configure':(PROBE,PROBE_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sensor.calibrate':(CALIBRATION,CALIBRATION_EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'gaussian_plane_transport':mechanism})

"""CHEM140 Alpha-T RevC with current converter: observed phasor spectroscopy.

Finite dwell,50ohm generator loading,external OSL inverse calibration and explicit
thermal budgets. Static dielectric references never become claimed measured spectra.
"""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
import numpy as np
from scipy.integrate import solve_ivp
PROTOCOL_VERSION='1.0.0';SLUG='novocontrol_alphat';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-140'
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch04/t1_thermal'/SLUG;LIBRARY_PATH=ASSET/'dielectric_library.json'

class ActionRejected(ValueError):pass

def number(unit='1',lo=None,hi=None):
    s={'type':'number','x-unit':unit}
    if lo is not None:s['minimum']=lo
    if hi is not None:s['maximum']=hi
    return s

def obj(properties):return dict(type='object',properties=properties,required=list(properties),additionalProperties=False)
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
def array(s,minimum=0,maximum=200001):return dict(type='array',items=s,minItems=minimum,maxItems=maximum)
def enum(items):return {'type':'string','enum':items}
BOOL={'type':'boolean'};TEXT={'type':'string'};PHASE=enum(['idle','running','succeeded','cancelled','failed'])

def validate(s,v):
    if 'anyOf' in s:
        for o in s['anyOf']:
            try:validate(o,v);return
            except ActionRejected:pass
        raise ActionRejected('INVALID_ARGUMENT')
    k=s.get('type')
    if k in ('number','integer'):
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or k=='integer' and type(v) is not int or not s.get('minimum',-math.inf)<=v<=s.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
    elif k=='object':
        if not isinstance(v,dict) or set(v)!=set(s['properties']):raise ActionRejected('INVALID_ARGUMENT')
        for key,value in v.items():validate(s['properties'][key],value)
    elif k=='array':
        if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
        for x in v:validate(s['items'],x)
    elif k=='boolean' and type(v) is not bool:raise ActionRejected('INVALID_ARGUMENT')
    elif k=='string' and not isinstance(v,str):raise ActionRejected('INVALID_ARGUMENT')
    elif k=='null' and v is not None:raise ActionRejected('INVALID_ARGUMENT')
    if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')

from scipy.linalg import expm
EPS0=8.8541878128e-12
SOURCE_RESISTANCE_OHM=50.
CONVERTER_LIMIT_A=.04
MIN_POINT_S=1/10.5

def library():return {x['id']:x for x in json.loads(LIBRARY_PATH.read_text())['templates']}
def cx(unit):return obj({'real':number(unit),'imag':number(unit)})
def pack(z):return dict(real=float(complex(z).real),imag=float(complex(z).imag))
def unpack(v):return complex(v['real'],v['imag'])
def vacuum_capacitance(diameter_m,thickness_m):return EPS0*math.pi*(diameter_m/2)**2/thickness_m

def dielectric_response(epsilon_static,epsilon_infinity,relaxation_time_s,dc_conductivity_s_m,frequency_hz):
    w=2*math.pi*frequency_hz;return epsilon_infinity+(epsilon_static-epsilon_infinity)/(1+1j*w*relaxation_time_s)-1j*dc_conductivity_s_m/(w*EPS0)

def infer_dielectric(frequency_hz,voltage_rms,current_rms,diameter_m,thickness_m):
    voltage=unpack(voltage_rms);current=unpack(current_rms)
    if abs(voltage)<1e-30 or abs(current)<1e-30:raise ActionRejected('ANALYSIS_ZERO_SIGNAL')
    z=voltage/current
    if not math.isfinite(z.real) or not math.isfinite(z.imag) or abs(z)<1e-300:raise ActionRejected('ANALYSIS_DATA')
    y=1/z;w=2*math.pi*frequency_hz;c0=vacuum_capacitance(diameter_m,thickness_m);eps=y/(1j*w*c0)
    if eps.real<=0 or -eps.imag < -1e-9*max(abs(eps),1.):raise ActionRejected('NONPASSIVE_OR_NONCAPACITIVE_RESULT')
    loss=max(0.,-eps.imag)
    return dict(impedance_ohm=pack(z),epsilon_real=float(eps.real),epsilon_loss=float(loss),tan_delta=float(loss/eps.real),parallel_capacitance_f=float(c0*eps.real),conductivity_real_s_m=float(w*EPS0*loss),conductivity_imag_s_m=float(w*EPS0*eps.real),evidence='inverse_of_supplied_RMS_complex_voltage_and_current; epsilon=Y/(j*omega*C0); sigma_real_includes_dielectric_loss_not_only_dc_conduction')

def solve_osl(frequency_hz,open_measured_ohm,short_measured_ohm,load_measured_ohm,load_resistance_ohm):
    zo,zs,zl=map(unpack,[open_measured_ohm,short_measured_ohm,load_measured_ohm]);scale=max(abs(zo),abs(zs),abs(zl),1e-30)
    if abs(zo)<1e-30 or abs(zl-zs)<1e-12*max(abs(zl),abs(zs),1e-30) or abs(zo-zs)<1e-12*scale:raise ActionRejected('CALIBRATION_DEGENERATE')
    a=load_resistance_ohm*(1-zl/zo)/(zl-zs);b=-a*zs;c=-1/zo
    if not all(math.isfinite(v) for z in (a,b,c) for v in (z.real,z.imag)):raise ActionRejected('CALIBRATION_DEGENERATE')
    return dict(frequency_hz=frequency_hz,a=pack(a),b_ohm=pack(b),c_s=pack(c),evidence='three_standard_Mobius_inverse_from_external_open_short_load_measurements; no_hidden_fixture_or_gain_access')

def correct_impedance(z,calibration):
    denominator=unpack(calibration['c_s'])*z+1
    if abs(denominator)<1e-14:raise ActionRejected('CALIBRATION_SINGULAR')
    return (unpack(calibration['a'])*z+unpack(calibration['b_ohm']))/denominator

PLANT_EXAMPLE=dict(ambient_k=293.2,fixture_series_resistance_ohm=.2,fixture_series_inductance_h=1e-7,fixture_parallel_capacitance_f=2e-12,fixture_parallel_conductance_s=1e-10,voltage_gain_magnitude=1.,voltage_gain_phase_rad=0.,current_gain_magnitude=1.,current_gain_phase_rad=0.,voltage_resolution_v=1e-10,current_resolution_a=1e-18,detector_tau_s=.002,temperature_resolution_k=1e-4,clamp_speed_m_s=.025,accept_engineering=True)
PLANT=obj({'ambient_k':number('K',283.15,313.15),'fixture_series_resistance_ohm':number('ohm',0,1e6),'fixture_series_inductance_h':number('H',0,1),'fixture_parallel_capacitance_f':number('F',1e-18,1e-3),'fixture_parallel_conductance_s':number('S',0,1),'voltage_gain_magnitude':number('1',.1,10),'voltage_gain_phase_rad':number('rad',-math.pi,math.pi),'current_gain_magnitude':number('1',.1,10),'current_gain_phase_rad':number('rad',-math.pi,math.pi),'voltage_resolution_v':number('V',1e-15,.1),'current_resolution_a':number('A',1e-24,.001),'detector_tau_s':number('s',1e-6,100),'temperature_resolution_k':number('K',1e-6,1),'clamp_speed_m_s':number('m/s',.0001,.1),'accept_engineering':BOOL})
GEOMETRY_EXAMPLE=dict(diameter_m=.02,thickness_m=.001)
GEOMETRY=obj({'diameter_m':number('m',.001,.1),'thickness_m':number('m',1e-6,.02)})
THERMAL_EXAMPLE=dict(minimum_temperature_k=293.15,maximum_temperature_k=303.15,bath_capacity_j_k=5.,bath_ambient_conductance_w_k=.1,sample_bath_conductance_w_k=.5,open_sample_ambient_conductance_w_k=.05,controller_gain_w_k=5.,heating_limit_w=30.,cooling_limit_w=30.,accept_external_engineering_system=True)
THERMAL=obj({'minimum_temperature_k':number('K',100,1000),'maximum_temperature_k':number('K',100,1000),'bath_capacity_j_k':number('J/K',.001,1e7),'bath_ambient_conductance_w_k':number('W/K',0,1000),'sample_bath_conductance_w_k':number('W/K',1e-6,1000),'open_sample_ambient_conductance_w_k':number('W/K',0,100),'controller_gain_w_k':number('W/K',1e-6,1e5),'heating_limit_w':number('W',.001,1e5),'cooling_limit_w':number('W',.001,1e5),'accept_external_engineering_system':BOOL})
SAMPLE_EXAMPLE=dict(reference_id='water_crc_7732_18_5',epsilon_infinity=4.9,relaxation_time_s=1e-11,dc_conductivity_s_m=5e-6,density_kg_m3=1000.,specific_heat_capacity_j_kg_k=4180.,initial_temperature_k=293.2,temperature_tolerance_k=.5,accept_spectrum_assumption=True,accept_thermal_assumption=True)
SAMPLE=obj({'reference_id':TEXT,'epsilon_infinity':number('1',1,1e5),'relaxation_time_s':number('s',1e-15,1e12),'dc_conductivity_s_m':number('S/m',0,1e8),'density_kg_m3':number('kg/m^3',1,30000),'specific_heat_capacity_j_kg_k':number('J/kg/K',1,10000),'initial_temperature_k':number('K',100,1000),'temperature_tolerance_k':number('K',.0001,2.),'accept_spectrum_assumption':BOOL,'accept_thermal_assumption':BOOL})
METHOD_EXAMPLE=dict(frequencies_hz=[10.,100.,1000.,10000.,100000.],source_voltage_rms_v=.3,cycles_per_point=1,settling_s=.01,require_calibration=False)
METHOD=obj({'frequencies_hz':array(number('Hz',3e-6,4e7),1,2001),'source_voltage_rms_v':number('V',0,3),'cycles_per_point':dict(type='integer',minimum=1,maximum=1000,**{'x-unit':'1'}),'settling_s':number('s',0,10000),'require_calibration':BOOL})
MODE=enum(['sample','open','short','load'])
OSL_INPUT=obj({'frequency_hz':number('Hz',3e-6,4e7),'open_measured_ohm':cx('ohm'),'short_measured_ohm':cx('ohm'),'load_measured_ohm':cx('ohm'),'load_resistance_ohm':number('ohm',.01,1e12)})
OSL_RESULT=obj({'frequency_hz':number('Hz',3e-6,4e7),'a':cx('1'),'b_ohm':cx('ohm'),'c_s':cx('S'),'evidence':TEXT})
OSL_EXAMPLE=dict(frequency_hz=1000.,open_measured_ohm=pack(100-1e6j),short_measured_ohm=pack(.2+.0006j),load_measured_ohm=pack(1000.199+.999j),load_resistance_ohm=1000.)
ANALYSIS_INPUT=obj({'frequency_hz':number('Hz',3e-6,4e7),'voltage_rms':cx('V'),'current_rms':cx('A'),'diameter_m':number('m',.001,.1),'thickness_m':number('m',1e-6,.02)})
ANALYSIS_EXAMPLE=dict(frequency_hz=1000.,voltage_rms=pack(1.),current_rms=pack(1e-8+1e-6j),diameter_m=.02,thickness_m=.001)
INFERENCE=obj({'impedance_ohm':cx('ohm'),'epsilon_real':number('1',0),'epsilon_loss':number('1',0),'tan_delta':number('1',0),'parallel_capacitance_f':number('F',0),'conductivity_real_s_m':number('S/m',0),'conductivity_imag_s_m':number('S/m',0),'evidence':TEXT})
ROW=obj({'time_s':number('s',0),'host_time_s':number('s',0),'frequency_hz':number('Hz'),'point_duration_s':number('s',0),'load_mode':MODE,'source_voltage_rms_v':number('V'),'voltage_rms':nullable(cx('V')),'current_rms':nullable(cx('A')),'raw_impedance_ohm':nullable(cx('ohm')),'impedance_ohm':nullable(cx('ohm')),'epsilon_real':nullable(number('1')),'epsilon_loss':nullable(number('1')),'tan_delta':nullable(number('1')),'parallel_capacitance_f':nullable(number('F')),'conductivity_real_s_m':nullable(number('S/m')),'conductivity_imag_s_m':nullable(number('S/m')),'sample_temperature_k':nullable(number('K')),'calibration_applied':BOOL,'quality':enum(['estimated','unavailable']),'error_code':nullable(TEXT)})
RESULT=obj({'operation_id':nullable(TEXT),'phase':PHASE,'elapsed_s':number('s',0),'rows':array(ROW,0,2001),'template_source':nullable(TEXT),'evidence':TEXT,'error_code':nullable(TEXT)})
CAPS={};SLOTS={}
def cap(k,i,o,c,z,kind='device_command',visibility='agent',example=None):CAPS[k]=dict(input_schema=i,output_schema=o,component_ids=c,function_zh=z,kind=kind,visibility=visibility,example_arguments=copy.deepcopy(example or {}),effects=[])
def slot(k,s,u,z,origin='derived',visibility='agent',component='controller'):SLOTS[k]=dict(schema=s,unit=u,meaning=z,origin=origin,visibility=visibility,component=component,write=None)
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller','current_converter'],'宿主显式寄生/相位增益/探测响应参数，厂家50Ω源电阻固定','configuration','evaluator',PLANT_EXAMPLE)
cap('geometry.configure',GEOMETRY,obj({'configured':BOOL}),['electrode_upper','electrode_lower','sample_disc'],'工程平板夹具几何与C0；装样后尺寸锁定守恒','configuration','evaluator',GEOMETRY_EXAMPLE)
cap('thermal.configure',THERMAL,obj({'configured':BOOL}),['thermal_port','electrode_lower'],'明确选择外部工程集中热边界，不假造OEM温控附件额定温区','configuration','evaluator',THERMAL_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_disc'],'真实静态介电参考加显式Debye/直流导电/热物性假设，不冒充实测宽频谱','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.remove',obj({}),obj({'loaded':BOOL}),['sample_disc'],'移出整片/定容液体样品及当前显热，不回滚已耗散热','configuration','evaluator')
cap('sample.set_clamp',obj({'closed':BOOL}),obj({'target_m':number('m',0,.025)}),['electrode_upper'],'宿主手动上电极有限运动代理，开放时转为外界热交换','simulation','evaluator',{'closed':True})
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_switch'],'关闭源电压/控制并取消采集，样品显热继续交换',example={'enabled':True})
cap('thermal.set_temperature',obj({'temperature_k':number('K',100,1000)}),obj({'target_k':number('K')}),['thermal_port'],'只写已配置外部热边界范围内目标，实际温度仅step推进',example={'temperature_k':293.2})
cap('thermal.disable',obj({}),obj({'enabled':BOOL}),['thermal_port'],'停止外部主动温控，热交换与介电耗散仍按实际供电继续')
cap('fixture.select_load',obj({'mode':MODE,'load_resistance_ohm':number('ohm',.01,1e12)}),obj({'mode':MODE}),['electrode_upper','current_converter'],'宿主连接样品或外部open/short/已知纯电阻参考；非已验证自动校准切换硬件','simulation','evaluator',{'mode':'sample','load_resistance_ohm':1000.})
cap('method.configure',METHOD,obj({'configured':BOOL}),['controller'],'每点实际等待cycles/f和无OptionF的10.5点/s下限，幅度按频界检查','configuration',example=METHOD_EXAMPLE)
cap('calibration.solve',OSL_INPUT,OSL_RESULT,['controller'],'只用外部open/short/load复阻抗观测解三项Möbius校正','analysis',example=OSL_EXAMPLE)
cap('calibration.apply',obj({'records':array(OSL_RESULT,1,2001)}),obj({'configured':BOOL}),['controller'],'应用外部逐频点校准系数；不外推/插值到未校准频率','configuration',example={'records':[solve_osl(**OSL_EXAMPLE)]})
cap('calibration.clear',obj({}),obj({'configured':BOOL}),['controller'],'清除校准记录并明确返回未校准状态','configuration')
cap('acquisition.start',obj({}),obj({'operation_id':TEXT,'expected_duration_s':number('s',0)}),['controller','current_converter'],'异步逐频率实际驻留并采集RMS复电压电流')
cap('acquisition.stop',obj({}),obj({'phase':PHASE,'elapsed_s':number('s',0)}),['controller'],'停止源激励并冻结已完成频点，部分点不伪装完成')
cap('acquisition.result',obj({}),RESULT,['controller'],'公开复VI观测和反演介电/损耗/阻抗；任意会话均隐藏选中物质来源','query')
cap('analysis.dielectric',ANALYSIS_INPUT,INFERENCE,['controller'],'仅由外部复VI及平板几何反演，σ实部包括介电损耗与直流导电','analysis',example=ANALYSIS_EXAMPLE)
cap('provenance.read',obj({}),obj({'reference_id':nullable(TEXT),'template_source':nullable(TEXT),'evidence':TEXT}),['controller'],'仅宿主读取当前样品真实静态参考及显式谱假设来源','query','evaluator')
LEDGER_UNITS=dict(mass_loaded_kg='kg',mass_present_kg='kg',mass_removed_kg='kg',mass_error_kg='kg',volume_loaded_m3='m^3',volume_present_m3='m^3',volume_removed_m3='m^3',volume_error_m3='m^3',generator_active_input_j='J',source_resistor_heat_j='J',fixture_parasitic_heat_j='J',reference_load_heat_j='J',sample_dielectric_heat_j='J',electrical_error_j='J',controller_input_j='J',ambient_loss_j='J',loaded_sensible_j='J',removed_sensible_j='J',thermal_stored_j='J',thermal_energy_error_j='J')
cap('ledger.read',obj({}),obj({k:number(u) for k,u in LEDGER_UNITS.items()}),['controller'],'周期平均源/50Ω/寄生/参考/样品耗散及热/质量/体积守恒账','query','evaluator')
cap('sim.step',obj({'dt_s':number('s',0,86400)}),obj({'time_s':number('s',0)}),['controller'],'单一宿主时钟支持分数纳秒余数，超长低频点必须实际累计推进','simulation','evaluator',{'dt_s':.1})
UNSUPPORTED=['hardware.connect','robot.load','measurement.dc_bias_option_b','measurement.fast_option_f','measurement.high_voltage','measurement.oem_thermal_attachment','noise.empirical','accuracy.certification','measurement.field_transients']
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'未实现：'+k)
for k,c in [('plant.configured','controller'),('geometry.configured','electrode_lower'),('thermal.configured','thermal_port'),('sample.present','sample_disc'),('power.enabled','power_switch'),('thermal.enabled','thermal_port'),('clamp.moving','electrode_upper'),('calibration.configured','controller')]:slot(k,BOOL,'1','实际配置或运行状态',component=c)
for suffix in ['position_m','target_m']:slot('clamp.'+suffix,number('m',0,.025),'m','上电极开口相对测量位置位移；0闭合，.025开放','setpoint' if suffix=='target_m' else 'scene',component='electrode_upper')
slot('power.angle_rad',number('rad',0,.28),'rad','电源开关工程姿态','scene',component='power_switch')
slot('thermal.target_k',number('K'),'K','外部工程热边界温度目标，不代表OEM温控能力','setpoint',component='thermal_port')
slot('thermal.indicated_k',nullable(number('K')),'K','样品与电极集中节点量化温度API观测，非厂家精度认证','sensor',component='sample_disc')
slot('excitation.frequency_hz',nullable(number('Hz')),'Hz','当前真实驻留频点，未激励时null','derived',component='controller')
slot('excitation.source_voltage_rms_v',number('V'),'V','当前50Ω前源RMS幅度，停止时0，不等于样品两端幅度','derived',component='controller')
slot('fixture.load_mode',MODE,'1','宿主当前接入sample/open/short/load参考','setpoint',component='current_converter')
slot('measured.voltage_rms_v',nullable(cx('V')),'V','含显式增益/相位/响应/量化的复RMS电压观测','sensor',component='current_converter')
slot('measured.current_rms_a',nullable(cx('A')),'A','含显式增益/相位/响应/量化的复RMS电流；超过40mA时不可用','sensor',component='current_converter')
slot('clock.time_s',number('s',0),'s','唯一宿主时钟')
slot('acquisition.phase',PHASE,'1','异步频扫状态')
slot('acquisition.elapsed_s',number('s',0),'s','本次实际源激励时长')
slot('acquisition.expected_duration_s',number('s',0),'s','逐点频率周期及通信速率下限计算的所需总时长')
slot('acquisition.points',{'type':'integer','minimum':0,'maximum':2001,'x-unit':'1'},'1','实际完成的频点数，不含尚未完成点')
for k,u in [('truth.bath_k','K'),('truth.sample_k','K'),('truth.sample_mass_kg','kg'),('truth.dc_conductivity_s_m','S/m')]:slot(k,nullable(number(u)),u,'仅宿主真实热/样品参数状态','process_truth','evaluator')

class NovocontrolAlphatRuntime:
    def __init__(self,instance_id='alphat_01'):
        self.instance_id=instance_id;self.plant=None;self.geometry=None;self.thermal=None;self.sample=None;self.record=None;self.power=False;self.thermal_enabled=False;self.target=293.2;self.clamp=.025;self.clamp_target=.025;self._clamp_start=.025;self._clamp_start_ns=0;self._clamp_arrival_ns=0;self.x=np.zeros(4);self.detector=np.zeros(2,dtype=complex);self.time_ns=0;self._clock_remainder_ns=0.;self.revision=0;self.mode='sample';self.load_resistance=1000.;self.method=copy.deepcopy(METHOD_EXAMPLE);self.calibration={};self.phase='idle';self.elapsed=0.;self.rows=[];self.error=None;self.operation_id=None;self._counter=0;self._start_ns=0;self._point_index=0;self._point_start_ns=0;self._point_end_ns=0;self.expected_duration=0.;self.records={};self._principals={};self._requests={};self._owners={};self._cancel_operations={};self._source=None;self._reference_id=None;self._evidence='no_sample';self._operation_source=None;self._operation_evidence='no_measurement';self.mass_loaded=self.mass_removed=self.volume_loaded=self.volume_removed=self.loaded_sensible=self.removed_sensible=0.;self.electrical_energy=np.zeros(5)
    @property
    def time_s(self):return self.time_ns/1e9
    def register_principal(self,principal_id,visibility='agent'):
        if not isinstance(principal_id,str) or not principal_id or principal_id in self._principals or visibility not in ('agent','debug','evaluator'):raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id):
        if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return NovocontrolAlphatSession(self,principal_id)
    def describe(self):return export_novocontrol_alphat_contract_metadata()
    def _moving(self):return self.time_ns<self._clamp_arrival_ns
    def _closed(self):return self.clamp<=1e-12 and self.clamp_target==0.
    def _volume(self):return math.pi*(self.geometry['diameter_m']/2)**2*self.geometry['thickness_m'] if self.sample else 0.
    def _mass(self):return self._volume()*self.sample['density_kg_m3'] if self.sample else 0.
    def _capacities(self):return np.array([self.thermal['bath_capacity_j_k'],.1+(self._mass()*self.sample['specific_heat_capacity_j_kg_k'] if self.sample else 0.)])
    def _temperature(self,i):return self.plant['ambient_k']+float(self.x[i]) if self.plant and self.thermal else None
    def _frequency(self):return self.method['frequencies_hz'][self._point_index] if self.phase=='running' else None
    def _point_duration_ns(self,f):return math.ceil((self.method['settling_s']+max(self.method['cycles_per_point']/f,MIN_POINT_S))*1e9)
    def _circuit(self,f=None,vrms=None,mode=None):
        f=self._frequency() if f is None else f;vrms=self.method['source_voltage_rms_v'] if vrms is None else vrms;mode=self.mode if mode is None else mode
        if f is None or not self.plant:return dict(voltage=0j,current=0j,source_voltage=0.,power=np.zeros(5),sample_admittance=0j)
        p=self.plant;w=2*math.pi*f;zp=complex(p['fixture_series_resistance_ohm'],w*p['fixture_series_inductance_h']);yp=complex(p['fixture_parallel_conductance_s'],w*p['fixture_parallel_capacitance_f']);ys=0j
        if mode=='sample' and self.sample:
            s=self.sample;eps=dielectric_response(s['epsilon_static'],s['epsilon_infinity'],s['relaxation_time_s'],s['dc_conductivity_s_m'],f);ys=1j*w*vacuum_capacitance(**self.geometry)*eps
        elif mode=='load':ys=1/self.load_resistance
        parallel_z=0j if mode=='short' else 1/(ys+yp);fixture_z=zp+parallel_z;current=complex(vrms)/(SOURCE_RESISTANCE_OHM+fixture_z);voltage=current*fixture_z;parallel_voltage=current*parallel_z;sample_power=abs(parallel_voltage)**2*ys.real if mode=='sample' else 0.;reference_power=abs(parallel_voltage)**2*ys.real if mode=='load' else 0.;parasitic=abs(current)**2*zp.real+abs(parallel_voltage)**2*yp.real;source_heat=abs(current)**2*SOURCE_RESISTANCE_OHM;input_power=float((complex(vrms)*current.conjugate()).real);return dict(voltage=voltage,current=current,source_voltage=vrms,power=np.array([input_power,source_heat,parasitic,reference_power,sample_power]),sample_admittance=ys)
    def _measurement_error(self,circuit):
        if abs(circuit['current'])>CONVERTER_LIMIT_A*(1+1e-12):return 'CURRENT_CONVERTER_OVERFLOW'
        if self.mode=='sample' and self.sample and abs(self._temperature(1)-self.sample['reference_k'])>self.sample['temperature_tolerance_k']:return 'REFERENCE_TEMPERATURE_RANGE'
        return None
    def _observed(self):
        if not self.power or not self.plant or self.phase!='running':return None,None
        circuit=self._circuit()
        if self._measurement_error(circuit):return None,None
        p=self.plant;quant=lambda z,q:complex(round(z.real/q)*q,round(z.imag/q)*q);return pack(quant(self.detector[0],p['voltage_resolution_v'])),pack(quant(self.detector[1],p['current_resolution_a']))
    def read(self,role='evaluator'):
        if role not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
        voltage,current=self._observed();d={'plant.configured':self.plant is not None,'geometry.configured':self.geometry is not None,'thermal.configured':self.thermal is not None,'sample.present':self.sample is not None,'power.enabled':self.power,'thermal.enabled':self.thermal_enabled,'clamp.moving':self._moving(),'calibration.configured':bool(self.calibration),'clamp.position_m':self.clamp,'clamp.target_m':self.clamp_target,'power.angle_rad':.28 if self.power else 0.,'thermal.target_k':self.target,'thermal.indicated_k':float(round(self._temperature(1)/self.plant['temperature_resolution_k'])*self.plant['temperature_resolution_k']) if self.power and self.thermal else None,'excitation.frequency_hz':self._frequency(),'excitation.source_voltage_rms_v':self.method['source_voltage_rms_v'] if self.phase=='running' and self.power else 0.,'fixture.load_mode':self.mode,'measured.voltage_rms_v':voltage,'measured.current_rms_a':current,'clock.time_s':self.time_s,'acquisition.phase':self.phase,'acquisition.elapsed_s':self.elapsed,'acquisition.expected_duration_s':self.expected_duration,'acquisition.points':len(self.rows),'truth.bath_k':self._temperature(0),'truth.sample_k':self._temperature(1),'truth.sample_mass_kg':self._mass() if self.sample else None,'truth.dc_conductivity_s_m':self.sample['dc_conductivity_s_m'] if self.sample else None}
        return {k:v for k,v in d.items() if role!='agent' or SLOTS[k]['visibility']=='agent'}
    def _propagate(self,ns):
        if not ns:return
        dt=ns/1e9;circuit=self._circuit() if self.power and self.phase=='running' else dict(voltage=0j,current=0j,power=np.zeros(5));self.electrical_energy+=circuit['power']*dt
        if self.plant:
            p=self.plant;gv=p['voltage_gain_magnitude']*complex(math.cos(p['voltage_gain_phase_rad']),math.sin(p['voltage_gain_phase_rad']));gi=p['current_gain_magnitude']*complex(math.cos(p['current_gain_phase_rad']),math.sin(p['current_gain_phase_rad']));target=np.array([circuit['voltage']*gv,circuit['current']*gi]);self.detector=target+(self.detector-target)*math.exp(-dt/p['detector_tau_s'])
        if not self.thermal:return
        t=self.thermal;C=self._capacities();gb=t['sample_bath_conductance_w_k'] if self._closed() else 0.;gs=0. if self._closed() else t['open_sample_ambient_conductance_w_k'];ga=t['bath_ambient_conductance_w_k'];K=t['controller_gain_w_k'] if self.power and self.thermal_enabled else 0.;target=self.target-self.plant['ambient_k'];P=float(circuit['power'][4]);low=min(float(min(self.x[:2])),0.,target);high=max(float(max(self.x[:2])),0.,target)+P*dt/min(C);saturated=K and ((K+ga)*target-K*low>t['heating_limit_w'] or (K+ga)*target-K*high < -t['cooling_limit_w'])
        if saturated:
            def rhs(_time,y):
                b,s=y[:2];raw=(K+ga)*target-K*b;control=min(t['heating_limit_w'],max(-t['cooling_limit_w'],raw));ambient=ga*b+gs*s;return [(control-gb*(b-s)-ga*b)/C[0],(gb*(b-s)-gs*s+P)/C[1],control,ambient]
            sol=solve_ivp(rhs,(0.,dt),self.x,method='Radau',rtol=1e-10,atol=[1e-11,1e-11,1e-10,1e-10])
            if not sol.success:raise RuntimeError('THERMAL_INTEGRATION_FAILED')
            self.x=sol.y[:,-1]
        else:
            A=np.zeros((5,5));A[0,0]=-(gb+ga+K)/C[0];A[0,1]=gb/C[0];A[0,4]=(K+ga)*target/C[0] if K else 0.;A[1,0]=gb/C[1];A[1,1]=-(gb+gs)/C[1];A[1,4]=P/C[1];A[2,0]=-K;A[2,4]=(K+ga)*target if K else 0.;A[3,0]=ga;A[3,1]=gs;self.x=(expm(A*dt)@np.r_[self.x,1.])[:4]
    def _ledger(self):
        generator,source,parasitic,reference,sample=self.electrical_energy;stored=float(self._capacities()@self.x[:2]) if self.thermal else 0.;control,ambient=self.x[2:];return dict(mass_loaded_kg=self.mass_loaded,mass_present_kg=self._mass(),mass_removed_kg=self.mass_removed,mass_error_kg=self.mass_loaded-self._mass()-self.mass_removed,volume_loaded_m3=self.volume_loaded,volume_present_m3=self._volume(),volume_removed_m3=self.volume_removed,volume_error_m3=self.volume_loaded-self._volume()-self.volume_removed,generator_active_input_j=float(generator),source_resistor_heat_j=float(source),fixture_parasitic_heat_j=float(parasitic),reference_load_heat_j=float(reference),sample_dielectric_heat_j=float(sample),electrical_error_j=float(generator-source-parasitic-reference-sample),controller_input_j=float(control),ambient_loss_j=float(ambient),loaded_sensible_j=self.loaded_sensible,removed_sensible_j=self.removed_sensible,thermal_stored_j=stored,thermal_energy_error_j=float(control+sample-ambient+self.loaded_sensible-self.removed_sensible-stored))
    def _result(self):return dict(operation_id=self.operation_id,phase=self.phase,elapsed_s=self.elapsed,rows=copy.deepcopy(self.rows),template_source=self._operation_source,evidence=self._operation_evidence,error_code=self.error)
    def _finish(self,phase,error=None):
        self.phase=phase;self.error=error
        if self.operation_id:self.records[self.operation_id]=self._result()
    def _record(self):
        frequency=self._frequency();circuit=self._circuit();error=self._measurement_error(circuit);voltage,current=self._observed();raw=None;corrected=None;inferred=None;calibration=self.calibration.get(frequency) if self.mode=='sample' else None
        if not error:
            try:
                if voltage is None or current is None or abs(unpack(current))<1e-30:raise ActionRejected('ANALYSIS_ZERO_SIGNAL')
                raw=unpack(voltage)/unpack(current);corrected=correct_impedance(raw,calibration) if calibration else raw
                if self.mode=='sample':
                    inferred=infer_dielectric(frequency,pack(corrected*unpack(current)),current,**self.geometry)
                    if not .01<=abs(corrected)<=2e14 or not 1e-15<=inferred['parallel_capacitance_f']<=10.:raise ActionRejected('MEASUREMENT_RANGE')
            except ActionRejected as exc:error=str(exc)
        row=dict(time_s=self.elapsed,host_time_s=self.time_s,frequency_hz=frequency,point_duration_s=(self.time_ns-self._point_start_ns)/1e9,load_mode=self.mode,source_voltage_rms_v=self.method['source_voltage_rms_v'],voltage_rms=voltage,current_rms=current,raw_impedance_ohm=pack(raw) if raw is not None else None,impedance_ohm=pack(corrected) if corrected is not None else None,**{k:inferred[k] if inferred and not error else None for k in ['epsilon_real','epsilon_loss','tan_delta','parallel_capacitance_f','conductivity_real_s_m','conductivity_imag_s_m']},sample_temperature_k=self.read()['thermal.indicated_k'],calibration_applied=calibration is not None,quality='unavailable' if error else 'estimated',error_code=error);self.rows.append(row)
        if error:self._finish('failed',error);return
        self._point_index+=1
        if self._point_index>=len(self.method['frequencies_hz']):self._finish('succeeded')
        else:self._point_start_ns=self.time_ns;self._point_end_ns=self.time_ns+self._point_duration_ns(self._frequency())
    def invoke(self,capability,**a):
        if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability]['input_schema'],a)
        if capability in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
        if capability=='analysis.dielectric':return infer_dielectric(**a)
        if capability=='calibration.solve':return solve_osl(**a)
        if capability=='acquisition.result':return self._result()
        if capability=='provenance.read':return dict(reference_id=self._reference_id,template_source=self._source,evidence=self._evidence)
        if capability=='ledger.read':return self._ledger()
        if capability=='sim.step':self.step(a['dt_s']);return {'time_s':self.time_s}
        if self.phase=='running' and capability not in ('acquisition.stop','power.set'):raise ActionRejected('BUSY')
        if capability=='plant.configure':
            if self.plant or not a['accept_engineering']:raise ActionRejected('PRECONDITION_FAILED')
            self.plant=copy.deepcopy(a);self.target=a['ambient_k'];out={'configured':True}
        elif capability=='geometry.configure':
            if self.sample:raise ActionRejected('PRECONDITION_FAILED')
            self.geometry=copy.deepcopy(a);out={'configured':True}
        elif capability=='thermal.configure':
            if not self.plant or self.thermal or not a['accept_external_engineering_system'] or not a['minimum_temperature_k']<=self.plant['ambient_k']<=a['maximum_temperature_k'] or a['minimum_temperature_k']>=a['maximum_temperature_k']:raise ActionRejected('PRECONDITION_FAILED')
            self.thermal=copy.deepcopy(a);out={'configured':True}
        elif capability=='sample.load':
            if not self.plant or not self.geometry or not self.thermal or self.sample or self._moving() or self.clamp<.024 or not a['accept_spectrum_assumption'] or not a['accept_thermal_assumption']:raise ActionRejected('PRECONDITION_FAILED')
            record=library().get(a['reference_id'])
            if record is None:raise ActionRejected('UNKNOWN_REFERENCE')
            epsilon=record.get('relative_permittivity_static');temperature=record.get('reference_temperature_K')
            if not isinstance(epsilon,(int,float)) or not math.isfinite(epsilon) or epsilon<a['epsilon_infinity']:raise ActionRejected('REFERENCE_DATA')
            if not isinstance(temperature,(int,float)) or not math.isfinite(temperature):raise ActionRejected('REFERENCE_TEMPERATURE_UNKNOWN')
            if abs(a['initial_temperature_k']-temperature)>a['temperature_tolerance_k']:raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
            if not self.thermal['minimum_temperature_k']<=a['initial_temperature_k']<=self.thermal['maximum_temperature_k']:raise ActionRejected('THERMAL_ENVELOPE')
            old=self._capacities()[1];self.sample=copy.deepcopy(a);self.sample.update(epsilon_static=epsilon,reference_k=temperature);self.record=copy.deepcopy(record);new=self._capacities()[1];delta=a['initial_temperature_k']-self.plant['ambient_k'];self.x[1]=(old*self.x[1]+(new-old)*delta)/new;self.loaded_sensible+=(new-old)*delta;self.mass_loaded+=self._mass();self.volume_loaded+=self._volume();self._reference_id=a['reference_id'];self._source=json.dumps(record['source'],ensure_ascii=False,sort_keys=True);self._evidence='real_static_permittivity_reference; synthetic_Debye_spectrum_and_dc_conductivity_explicit_host_assumptions';out={'loaded':True}
        elif capability=='sample.remove':
            if not self.sample or self._moving() or self.clamp<.024:raise ActionRejected('PRECONDITION_FAILED')
            self.mass_removed+=self._mass();self.volume_removed+=self._volume();self.removed_sensible+=self._mass()*self.sample['specific_heat_capacity_j_kg_k']*float(self.x[1]);self.sample=self.record=None;self._source=self._reference_id=None;self._evidence='no_sample';out={'loaded':False}
        elif capability=='sample.set_clamp':
            if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
            self.clamp_target=0. if a['closed'] else .025;self._clamp_start=self.clamp;self._clamp_start_ns=self.time_ns;self._clamp_arrival_ns=self.time_ns+round(abs(self.clamp_target-self.clamp)/self.plant['clamp_speed_m_s']*1e9);out={'target_m':self.clamp_target}
        elif capability=='power.set':
            if not a['enabled'] and self.phase=='running':self._finish('cancelled','POWER_OFF')
            self.power=a['enabled']
            if not self.power:self.thermal_enabled=False
            out={'enabled':self.power}
        elif capability=='thermal.set_temperature':
            if not self.power or not self.thermal or not self.thermal['minimum_temperature_k']<=a['temperature_k']<=self.thermal['maximum_temperature_k']:raise ActionRejected('THERMAL_ENVELOPE')
            self.target=a['temperature_k'];self.thermal_enabled=True;out={'target_k':self.target}
        elif capability=='thermal.disable':self.thermal_enabled=False;out={'enabled':False}
        elif capability=='fixture.select_load':self.mode=a['mode'];self.load_resistance=a['load_resistance_ohm'];out={'mode':self.mode}
        elif capability=='method.configure':
            frequencies=a['frequencies_hz']
            if len(set(frequencies))!=len(frequencies) or any(f>=1e7 and a['source_voltage_rms_v']>1. for f in frequencies):raise ActionRejected('FREQUENCY_VOLTAGE_ENVELOPE')
            self.method=copy.deepcopy(a);out={'configured':True}
        elif capability=='calibration.apply':
            if len({x['frequency_hz'] for x in a['records']})!=len(a['records']) or any(abs(unpack(x['a'])-unpack(x['b_ohm'])*unpack(x['c_s']))<1e-20 for x in a['records']):raise ActionRejected('CALIBRATION_DEGENERATE')
            self.calibration={x['frequency_hz']:copy.deepcopy(x) for x in a['records']};out={'configured':True}
        elif capability=='calibration.clear':self.calibration={};out={'configured':False}
        elif capability=='acquisition.start':
            if not self.power or not self.plant or not self.geometry or not self.thermal or not self._closed() or self._moving() or self.mode=='sample' and not self.sample:raise ActionRejected('PRECONDITION_FAILED')
            if self.method['source_voltage_rms_v']==0:raise ActionRejected('ZERO_EXCITATION')
            if self.mode=='sample' and abs(self._temperature(1)-self.sample['reference_k'])>self.sample['temperature_tolerance_k']:raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
            if self.mode=='sample' and self.method['require_calibration'] and any(f not in self.calibration for f in self.method['frequencies_hz']):raise ActionRejected('CALIBRATION_MISSING')
            self._counter+=1;self.operation_id=f'{self.instance_id}:acquisition:{self._counter}';self._operation_source=self._source if self.mode=='sample' else None;self._operation_evidence=self._evidence if self.mode=='sample' else 'actual_simulated_external_reference_observation';self.phase='running';self.elapsed=0.;self.rows=[];self.error=None;self._start_ns=self.time_ns;self._point_index=0;self._point_start_ns=self.time_ns;self._point_end_ns=self.time_ns+self._point_duration_ns(self._frequency());self.expected_duration=sum(self._point_duration_ns(f) for f in self.method['frequencies_hz'])/1e9;out={'operation_id':self.operation_id,'expected_duration_s':self.expected_duration}
        elif capability=='acquisition.stop':
            if self.phase!='running':raise ActionRejected('NOT_RUNNING')
            self._finish('cancelled','USER_CANCEL');out={'phase':self.phase,'elapsed_s':self.elapsed}
        else:raise ActionRejected('UNKNOWN_CAPABILITY')
        self.revision+=1;return copy.deepcopy(out)
    def step(self,dt_s):
        validate(number('s',0,86400),dt_s);total=math.fsum((self._clock_remainder_ns,dt_s*1e9));ns=round(total);self._clock_remainder_ns=total-ns
        if not ns:return
        end=self.time_ns+ns
        while self.time_ns<end:
            boundary=end
            if self._moving():boundary=min(boundary,self._clamp_arrival_ns)
            if self.phase=='running':boundary=min(boundary,self._point_end_ns)
            self._propagate(boundary-self.time_ns);self.time_ns=boundary
            if self.plant:self.clamp=self.clamp_target if self.time_ns>=self._clamp_arrival_ns else self._clamp_start+math.copysign(self.plant['clamp_speed_m_s']*(self.time_ns-self._clamp_start_ns)/1e9,self.clamp_target-self._clamp_start)
            if self.phase=='running':
                self.elapsed=(self.time_ns-self._start_ns)/1e9
                if self.time_ns==self._point_end_ns:self._record()
        self.revision+=1

class NovocontrolAlphatSession:
    def __init__(self, host, principal_id): self._host = host; self._principal = principal_id
    @property
    def _role(self): return self._host._principals[self._principal]
    def _target(self, instance_id):
        if instance_id != self._host.instance_id: raise ActionRejected('UNKNOWN_INSTANCE')
    def describe(self, *, instance_id=None, asset_type_id=None):
        if (instance_id is None) == (asset_type_id is None): raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None: self._target(instance_id)
        if asset_type_id is not None and asset_type_id != ASSET_TYPE_ID: raise ActionRejected('UNKNOWN_INSTANCE')
        meta = export_novocontrol_alphat_contract_metadata()
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
            result['evidence'] = 'observed_complex_VI_with_explicit_synthetic_spectrum_and_fixture_assumptions; selected template details require evaluator provenance.read' if result['operation_id'] is not None else 'no_measurement'
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

EFFECTS={
 'plant.configure':['plant.configured','thermal.target_k'],
 'geometry.configure':['geometry.configured'],
 'thermal.configure':['thermal.configured','truth.bath_k','truth.sample_k','thermal.indicated_k'],
 'sample.load':['sample.present','truth.sample_mass_kg','truth.dc_conductivity_s_m','truth.sample_k','thermal.indicated_k'],
 'sample.remove':['sample.present','truth.sample_mass_kg','truth.dc_conductivity_s_m'],
 'sample.set_clamp':['clamp.target_m','clamp.moving'],
 'power.set':['power.enabled','power.angle_rad','thermal.enabled','thermal.indicated_k','excitation.frequency_hz','excitation.source_voltage_rms_v','measured.voltage_rms_v','measured.current_rms_a','acquisition.phase'],
 'thermal.set_temperature':['thermal.target_k','thermal.enabled'],'thermal.disable':['thermal.enabled'],
 'fixture.select_load':['fixture.load_mode'],
 'calibration.apply':['calibration.configured'],'calibration.clear':['calibration.configured'],
 'acquisition.start':['acquisition.phase','acquisition.elapsed_s','acquisition.expected_duration_s','acquisition.points','excitation.frequency_hz','excitation.source_voltage_rms_v','measured.voltage_rms_v','measured.current_rms_a'],
 'acquisition.stop':['acquisition.phase','excitation.frequency_hz','excitation.source_voltage_rms_v','measured.voltage_rms_v','measured.current_rms_a'],
 'sim.step':list(SLOTS)}
for k,v in EFFECTS.items():CAPS[k]['effects']=v
ERRORS=sorted(set(__import__('re').findall(r"ActionRejected\('([A-Z_0-9]+)'",Path(__file__).read_text()))|{'POWER_OFF','USER_CANCEL','CURRENT_CONVERTER_OVERFLOW','REFERENCE_TEMPERATURE_RANGE'})

def export_novocontrol_alphat_contract_metadata():
    slots={};caps={}
    for k,v in SLOTS.items():slots[k]=dict(meaning_zh=v['meaning'],component_ids=[v['component']],value_schema=copy.deepcopy(v['schema']),unit=v['unit'],reference_conditions='SI; RMS phasors with exp(+j*omega*t),epsilon*=epsilon_real-j*epsilon_loss,Y=j*omega*C0*epsilon. Alpha-T RevC,current converter40mA;50ohmsource.',origin=v['origin'],visibility=v['visibility'],access='read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'NovocontrolAlphatSession.read(state_ids=[{k!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description='Host clock only; displayed phasor is an explicitly assumed first-order demodulator endpoint estimate after actual frequency dwell, not an instantaneous AC waveform sample.'),source_ids=['simulation_assumption'])
    limits=[
      'Alpha-T RevC含电流转换接口；范围3µHz至40MHz。10MHz以下0至3Vrms，原表below/above边界未明，恰好10MHz本模型保守限制1Vrms并与高频同处理。源输出阻抗固定50Ω。',
      '未选OptionF时每点等待settling+max(cycles/f,1/10.5)s；3µHz一周期至少333333.333s，不能瞬时出谱。600点/s、OptionB直流偏置和高压选件unsupported。',
      '电流转换器物理电流超过40mA即观测不可用、完成当前点时采集失败切断源；不把测量量程伪装成源电流限流。样品端RMS电压由50Ω分压与寄生网络决定，并非始终等于源幅度。',
      '模板提供真实文献编纂静态介电常数/温度，curves为空。Debye高频极限、松弛τ、σdc均由宿主显式假设，生成曲线为synthetic，不冒充67种真实宽频实测谱。',
      '默认water静态ε80.1@293.2K；ε∞4.9、τ1e-11s、σdc5e-6S/m、ρ1000kg/m³和Cp4180J/kg/K仅工程水近似，库内未知字段仍null。改变τ用于教学弛豫峰不能写成物质实验结果。',
      'Debye关系epsilon*=epsilon_inf+(epsilon_s-epsilon_inf)/(1+j*omega*tau)-j*sigma_dc/(omega*eps0)；C0=eps0*A/d。复电导定义sigma*=j*omega*eps0*epsilon，实部包含介电损耗与直流导电；不能与宿主sigma_dc混称。',
      '工程平板D20mm厚1mm的绝缘液池/固体垫片实例，电极下方为generator高电位、上方为current-converter虚地。geometry.configure影响过程几何，静态3D示例不自动缩放。装样后禁止改几何以免质量体积生灭。',
      '本批工程夹具用串联R/L、并联C/G寄生网络；已知BDS1200上限10MHz、ZGS只适配Alpha-A，不把两者拼成Alpha-T原厂40MHz夹具。Analyzer BNC端范围不能自动等同样品端精度。',
      '外部open/short/已知纯电阻load经实际相同网络和两个复增益通道测量，solve由三个外部观测解Möbius逆，再apply逐频点记录；不读隐藏寄生/增益，不跨未校准频率插值。fixture.select_load只是宿主接线代理，不声称自动校准机械。',
      '所有电压电流为RMS复相量，P=Re(V*conj(I))不含1/2。样品耗散进入样品热节点，50Ω源/寄生/参考负载耗散导出外部环境，分别留账。',
      '采用稳态周期平均有功功率及工程一阶解调估计，未求解交流逐周期波形、电磁起停暂态或电场/极化储能。能量验证限于周期平均有功与热账，不宣称瞬态总电磁能量认证。',
      '明确选择宿主外部工程有限热容边界；温区、功率、热导由thermal.configure输入，未指定Novotherm/Quatro等原厂温控系统。温度超静态参考容差时测量失败，但现有显热仍继续交换。',
      '样品温度在宿主参考容差内时epsilon_s/epsilon_inf/tau/sigma_dc作为常系数近似，不宣称温度谱插值。样品热节点包含固定0.1J/K夹具/电极工程热容，非OEM参数。',
      '整片/定容样品几何确定体积，密度/Cp为宿主显式假设，载入/移出显热完整记账。开夹时样品脱离热台转与环境换热，未求解深度接触、压力或溶剂挥发。',
      '厂家误差强依赖频率/阻抗/校准和环境，不能把条件精度套全域；这里的增益/相位/分辨率/τ是输入假设，真实噪声和准确度认证not_run。',
      '主机有蓝色双行字符LCD，几何只保留静态外观/身份文字；动态LCD未实现，READOUTS为空不等于没有显示屏。',
      '所有session公开result/poll统一脱敏，包括内部evaluator桥接；选中静态参考来源只在evaluator provenance.read。共享Python与磁盘不等于OS级真值隔离。',
      '真实硬件、机器人装样、OEM夹具/温控校准、直流偏置、高压、安全联锁与电磁暂态not_run。']
    for k,v in CAPS.items():
        un=k in UNSUPPORTED;async_=k=='acquisition.start';caps[k]={key:copy.deepcopy(v[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']}
        caps[k].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ('scene','robot','hardware') else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.novocontrol_alphat.NovocontrolAlphatSession.invoke',invocation=f'session.invoke(instance_id="alphat_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),preconditions=[] if un or v['kind'] in ('query','analysis') else [dict(description_zh='明确夹具/热边界、夹持、静态参考温域、频率/电压/校准网格由运行入口原子检查。',state_ids=['power.enabled','sample.present','clamp.position_m','thermal.configured','acquisition.phase'],enforced_by='runtime',check='NovocontrolAlphatRuntime.invoke guards before commit')],state_effects=[dict(state_id=s,timing='immediate',description_zh='命令实际即时状态变化') for s in v['effects']],observes=[],model_effects=dict(mode='none',description_zh='只输出可映射状态，实际模型求值由独立场景报告登记。',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='每个频点累计指定真实周期和通信时间后采样，所有频点完成或实际范围错误终止' if async_ else '原子命令；电热/运动/频扫驻留仅宿主step推进',completion_state_ids=['acquisition.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','parallel_plate_fixture','source','current_converter'],partial_failure_zh='取消/断电切断源并冻结已完成频点，未完成点不输出；已积耗散热和显热不回滚。范围错误完成当前频点时记不可用并停止。'),errors=[dict(code=e,meaning_zh=e,side_effects='partial' if async_ and e in ('POWER_OFF','USER_CANCEL','CURRENT_CONVERTER_OVERFLOW','MEASUREMENT_RANGE') else 'none') for e in ERRORS],verification=[],limitations=limits,source_ids=['manufacturer_primary','simulation_assumption','template_library'])
        if k in ('thermal.set_temperature','thermal.disable','sample.set_clamp','acquisition.start','power.set'):
            caps[k]['state_effects'] += [dict(state_id=s,timing='on_step',description_zh='宿主推进热过程、夹持或实际频扫') for s in SLOTS if s not in v['effects'] and s not in ('plant.configured','geometry.configured','thermal.configured','sample.present','power.enabled')]
    mechanism=dict(component_ids=['controller','current_converter','electrode_upper','electrode_lower','sample_disc','thermal_port'],state_ids=list(slots),meaning_zh='带50Ω源和夹具寄生的复介电相量网络，有限扫频时间、外部OSL校准、有限热容与有功热守恒',implementation='lumped',equations=[
      'epsilon*=epsilon_inf+(epsilon_s-epsilon_inf)/(1+j*omega*tau)-j*sigma_dc/(omega*eps0); Ysample=j*omega*C0*epsilon*,C0=eps0*pi*(D/2)^2/d. Static epsilon_s is source data; all dynamic parameters are explicit assumptions.',
      'Yparallel=Ysample+Gparasitic+j*omega*Cparasitic; Zfixture=Rseries+j*omega*Lseries+1/Yparallel; I=Vs/(50ohm+Zfixture),Vfixture=I*Zfixture. External open/short/load changes the sample admittance branch only.',
      'Measured RMS channels follow explicit complex gains and first-order demodulator response, with independently declared engineering quantization. Physical converter current>40mA returns unavailable, not clipped current.',
      'OSL inverse Zcorrected=(a*Zmeasured+b)/(c*Zmeasured+1), c=-1/Zopen, a=Rload*(1-Zload/Zopen)/(Zload-Zshort),b=-a*Zshort; coefficients are computed solely from supplied external observed standards.',
      'Z=V/I; epsilon=1/(Z*j*omega*C0); tan_delta=epsilon_loss/epsilon_real; sigma_real=omega*eps0*epsilon_loss and sigma_imag=omega*eps0*epsilon_real. Sample observation limits enforce C1e-15..10F and |Z|.01..2e14ohm.',
      'Each point dwell=settling+max(cycles/f,1/10.5)s, rounded upward to nanosecond. A fractional-nanosecond residual retains normal60Hz host clock accuracy. Filter and circuit clocks never advance on read/invoke configuration.',
      'Pgenerator=Re(Vs*conj(I)); Psource=|I|²*50; Pseries=|I|²*Rseries; Pshunt=|Vparallel|²*Gparasitic; Psample=|Vparallel|²*Re(Ysample). Pgenerator=Psource+Pseries+Pshunt+Psample+Preference with RMS convention.',
      'Cb*dTb=Pcontroller-Gb*(Tb-Ts)-Ga*(Tb-Ta); Cs*dTs=Gb*(Tb-Ts)-Gopen*(Ts-Ta)+Psample. Closed fixture:Gb>0,Gopen=0;open:Gb=0,Gopen>0. External controller has explicit heating/cooling limits.',
      'Matrix exponential propagates linear thermal/energy states; Radau solves the same equations if power saturation is possible. Complex demodulator state has exact exp(-dt/tau) propagation. Sample loading capacity-weights incoming sensible heat; removing exports actual sensible heat.',
      'Electrical ledger is steady cycle-averaged active energy, excluding unmodeled turn-on/off electromagnetic stored energy and transients.'],parameters={},source_ids=['simulation_assumption','template_library'],limitations=limits)
    spec=importlib.util.spec_from_file_location('_alpha_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()});ann.annotate_export(SLUG,caps,slots,{'dielectric_spectroscopy':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'geometry.configure':(GEOMETRY,GEOMETRY_EXAMPLE),'thermal.configure':(THERMAL,THERMAL_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
    for key,value in SLOTS.items():slots[key]['meaning_zh']=value['meaning']
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'dielectric_spectroscopy':mechanism})

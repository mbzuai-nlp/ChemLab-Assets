"""CHEM137 MCR503: PP25 flow/oscillation with explicit time and honest material evidence.

Newtonian viscosity is read from public material references. HB yield/exponent and
Maxwell relaxation are explicit engineering assumptions, never measured template
moduli. The parallel-plate integrals, Maxwell transient energy, torque observations,
and inverse analyses are separate. No motor/contact/fluid CFD or OEM DSP is claimed.
"""
from __future__ import annotations
import copy, importlib.util, json, math
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares
PROTOCOL_VERSION='1.0.0';SLUG='antonpaar_mcr503';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-137'
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch04/t1_thermal'/SLUG;LIBRARY_PATH=ASSET/'rheology_library.json'
TORQUE_ROTATION_MIN=1e-9;TORQUE_OSCILLATION_MIN=.15e-9;TORQUE_MAX=.255;TORQUE_RESOLUTION=.05e-9

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

def library():return {x['id']:x for x in json.loads(LIBRARY_PATH.read_text(encoding='utf-8'))['templates']}
def pp_torque(rate_s_inv,radius_m,viscosity_pa_s):return math.pi/2*radius_m**3*viscosity_pa_s*rate_s_inv

def hb_torque(rate_s_inv,radius_m,yield_stress_pa,consistency_pa_sn,flow_index):
    rate=np.asarray(rate_s_inv,float)
    return 2*math.pi*radius_m**3*(yield_stress_pa/3+consistency_pa_sn*rate**flow_index/(flow_index+3))

def fit_flow(shear_rates_s_inv,torques_nm,radius_m,accept_no_slip_hb_model):
    if not accept_no_slip_hb_model:raise ActionRejected('ASSUMPTION_NOT_ACCEPTED')
    rate=np.asarray(shear_rates_s_inv,float);torque=np.asarray(torques_nm,float)
    if rate.shape!=torque.shape or len(rate)<6 or np.any(rate<=0) or np.any(torque<=0) or np.any(np.diff(rate)<=0) or rate[-1]/rate[0]<10:raise ActionRejected('ANALYSIS_DATA')
    scale=max(float(np.max(torque)),1e-20);stress=torque/(2*math.pi*radius_m**3)
    fit=least_squares(lambda p:(hb_torque(rate,radius_m,p[0],math.exp(p[1]),p[2])-torque)/scale,[max(0.,float(np.min(stress))*.1),math.log(max(float(stress[-1]/rate[-1]),1e-12)),1.],bounds=([0.,math.log(1e-12),.1],[1e8,math.log(1e8),2.]),xtol=1e-12,ftol=1e-12,gtol=1e-12,max_nfev=3000)
    if not fit.success or np.linalg.matrix_rank(fit.jac)<3:raise ActionRejected('ANALYSIS_UNRESOLVED')
    prediction=hb_torque(rate,radius_m,fit.x[0],math.exp(fit.x[1]),fit.x[2]);rms=float(np.sqrt(np.mean((prediction-torque)**2)))
    return dict(yield_stress_pa=float(fit.x[0]),consistency_pa_sn=float(math.exp(fit.x[1])),flow_index=float(fit.x[2]),torque_fit_rms_nm=rms,evidence='derived_from_external_torque_flow_curve_under_hb_no_slip_assumption')

def fit_oscillation(time_s,torques_nm,radius_m,strain_amplitude,frequency_hz,discard_time_s):
    t=np.asarray(time_s,float)
    if len(t)!=len(torques_nm) or len(t)<20 or np.any(np.diff(t)<=0) or strain_amplitude<=0 or frequency_hz<=0:raise ActionRejected('ANALYSIS_DATA')
    valid=np.asarray([x is not None for x in torques_nm])&(t>=discard_time_s)
    if sum(valid)<20:raise ActionRejected('TORQUE_BELOW_RANGE')
    t=t[valid];torque=np.asarray([0. if x is None else x for x in torques_nm])[valid]
    if t[-1]-t[0]<2/frequency_hz-1e-9:raise ActionRejected('ANALYSIS_WINDOW')
    phase=2*math.pi*frequency_hz*t;X=np.column_stack([strain_amplitude*np.sin(phase),strain_amplitude*np.cos(phase),np.ones(len(t))]);stress=2*torque/(math.pi*radius_m**3)
    if np.linalg.matrix_rank(X)<3:raise ActionRejected('ANALYSIS_UNRESOLVED')
    coef=np.linalg.lstsq(X,stress,rcond=None)[0];rms=float(np.sqrt(np.mean((stress-X@coef)**2)))
    return dict(storage_modulus_pa=float(coef[0]),loss_modulus_pa=float(coef[1]),stress_fit_rms_pa=rms,stress_offset_pa=float(coef[2]),points_used=int(len(t)),evidence='derived_fourier_regression_of_observed_torque; stationary_linear_tail_assumption_not_oem_dsp')

PLANT_EXAMPLE=dict(ambient_k=298.15,plate_capacity_j_k=100.,ambient_conductance_w_k=.5,controller_gain_w_k=10.,heater_max_w=100.,cooler_max_w=50.,lift_speed_m_s=.02,torque_gain=1.,torque_offset_nm=0.,temperature_resolution_k=.0001,accept_engineering=True)
PLANT=obj({'ambient_k':number('K',288.15,303.15),'plate_capacity_j_k':number('J/K',1,1e5),'ambient_conductance_w_k':number('W/K',0,100),'controller_gain_w_k':number('W/K',.01,1000),'heater_max_w':number('W',.01,1000),'cooler_max_w':number('W',0,1000),'lift_speed_m_s':number('m/s',.001,.1),'torque_gain':number('1',.1,10),'torque_offset_nm':number('N*m',-.1,.1),'temperature_resolution_k':number('K',1e-7,.1),'accept_engineering':BOOL})
GEOMETRY_EXAMPLE=dict(radius_m=.0125,accept_parallel_plate_no_slip=True)
GEOMETRY=obj({'radius_m':number('m',.001,.05),'accept_parallel_plate_no_slip':BOOL})
SAMPLE_EXAMPLE=dict(reference_id='water',volume_m3=math.pi*.0125**2*.001,density_kg_m3=997.,heat_capacity_j_kg_k=4180.,constitutive_model='newtonian',relaxation_time_s=.05,yield_stress_pa=0.,flow_index=1.,reference_shear_rate_s_inv=1.,accept_engineering_rheology=True)
SAMPLE=obj({'reference_id':TEXT,'volume_m3':number('m^3',1e-12,.001),'density_kg_m3':number('kg/m^3',1,30000),'heat_capacity_j_kg_k':number('J/(kg*K)',1,10000),'constitutive_model':enum(['newtonian','maxwell','herschel_bulkley']),'relaxation_time_s':number('s',1e-5,1000),'yield_stress_pa':number('Pa',0,1e8),'flow_index':number('1',.1,2.),'reference_shear_rate_s_inv':number('1/s',1e-6,1e6),'accept_engineering_rheology':BOOL})
METHOD_EXAMPLE=dict(mode='rotation',shear_rate_s_inv=100.,strain_amplitude=.1,frequency_hz=5.,duration_s=2.,sample_interval_s=.01,discard_time_s=1.,temperature_tolerance_k=.05)
METHOD=obj({'mode':enum(['rotation','oscillation']),'shear_rate_s_inv':number('1/s',1e-6,1e6),'strain_amplitude':number('1',1e-6,1.),'frequency_hz':number('Hz',.001,250),'duration_s':number('s',.001,3600),'sample_interval_s':number('s',1e-6,3600),'discard_time_s':number('s',0,3600),'temperature_tolerance_k':number('K',.001,.1)})
CALIBRATION_EXAMPLE=dict(torque_gain_correction=1.,torque_zero_nm=0.)
CALIBRATION=obj({'torque_gain_correction':number('1',.1,10),'torque_zero_nm':number('N*m',-.1,.1)})
OSC_INPUT=obj({'time_s':array(number('s',0),20),'torques_nm':array(nullable(number('N*m')),20),'radius_m':number('m',.001,.05),'strain_amplitude':number('1',1e-6,1.),'frequency_hz':number('Hz',.001,250),'discard_time_s':number('s',0)})
OSC_OUTPUT=obj({'storage_modulus_pa':number('Pa'),'loss_modulus_pa':number('Pa'),'stress_fit_rms_pa':number('Pa',0),'stress_offset_pa':number('Pa'),'points_used':{'type':'integer','minimum':20,'maximum':200001,'x-unit':'1'},'evidence':TEXT})
FLOW_INPUT=obj({'shear_rates_s_inv':array(number('1/s',1e-6),6,1000),'torques_nm':array(number('N*m',1e-15),6,1000),'radius_m':number('m',.001,.05),'accept_no_slip_hb_model':BOOL})
FLOW_OUTPUT=obj({'yield_stress_pa':number('Pa',0),'consistency_pa_sn':number('Pa*s^n',0),'flow_index':number('1',.1,2),'torque_fit_rms_nm':number('N*m',0),'evidence':TEXT})
ROW=obj({'time_s':number('s',0),'strain':number('1'),'shear_rate_s_inv':number('1/s'),'torque_nm':nullable(number('N*m')),'temperature_k':number('K'),'apparent_viscosity_pa_s':nullable(number('Pa*s'))})
RESULT=obj({'operation_id':nullable(TEXT),'phase':PHASE,'elapsed_s':number('s',0),'rows':array(ROW),'mean_apparent_viscosity_pa_s':nullable(number('Pa*s')),'oscillation_fit':nullable(OSC_OUTPUT),'template_source':nullable(TEXT),'evidence':TEXT,'error_code':nullable(TEXT)})
CAPS={};SLOTS={}
def cap(k,i,o,c,z,kind='device_command',visibility='agent',example=None):CAPS[k]=dict(input_schema=i,output_schema=o,component_ids=c,function_zh=z,kind=kind,visibility=visibility,example_arguments=copy.deepcopy(example or {}),effects=[])
def slot(k,s,u,z,origin='derived',visibility='agent',component='controller'):SLOTS[k]=dict(schema=s,unit=u,meaning=z,origin=origin,visibility=visibility,component=component,write=None)
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller','sample_plate'],'宿主配置温控热容/功率/环境与扭矩观测工程参数','configuration','evaluator',PLANT_EXAMPLE)
cap('geometry.configure',GEOMETRY,obj({'configured':BOOL}),['rotor','sample_plate'],'宿主登记平行板半径与无滑移假设；不自动变更固定PP25场景','configuration','evaluator',GEOMETRY_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_plate'],'宿主按真实黏度参考装样；Newtonian/Maxwell/HB本构及未知rho/Cp均须显式接受假设','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.remove',obj({}),obj({'loaded':BOOL}),['sample_plate'],'宿主卸样；带出质量、显热与残余弹性能','configuration','evaluator')
cap('geometry.set_gap',obj({'gap_m':number('m',.0001,.08)}),obj({'target_gap_m':number('m',.0001,.08)}),['head_lift'],'只写间隙目标，真实位置由宿主step有限速推进；未求解挤压流动',example={'gap_m':.001})
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_switch'],'上电或断电；断电取消采集但保留残余弹性和热状态',example={'enabled':True})
cap('thermal.set_temperature',obj({'temperature_k':number('K',273.15,373.15)}),obj({'target_k':number('K',273.15,373.15)}),['sample_plate','controller'],'只设温度目标；工程Peltier集中热容模型由step推进',example={'temperature_k':298.15})
cap('thermal.disable',obj({}),obj({'enabled':BOOL}),['sample_plate'],'关闭温控，保持实际热状态并自然散热')
cap('method.configure',METHOD,obj({'configured':BOOL}),['controller','rotor'],'配置恒剪切或单频振荡、内部采样和尾窗；真实物理不随配置推进','configuration',example=METHOD_EXAMPLE)
cap('sensor.calibrate',CALIBRATION,obj({'configured':BOOL}),['controller'],'写入独立来源的扭矩增益/零点校正，不偷读隐藏本构真值','configuration',example=CALIBRATION_EXAMPLE)
cap('acquisition.start',obj({}),obj({'operation_id':TEXT}),['controller','rotor','sample_plate'],'启动异步旋转或振荡；后续由step产生角位移、应力响应及观测')
cap('acquisition.stop',obj({}),obj({'phase':PHASE,'elapsed_s':number('s',0)}),['controller','rotor'],'停转并冻结采样；Maxwell残余应力随后松弛并释放弹性能')
cap('acquisition.result',obj({}),RESULT,['controller'],'公开返回冻结曲线与由实际力矩导出的黏度/G，所有session统一隐藏选中参考身份','query')
cap('provenance.read',obj({}),obj({'operation_id':nullable(TEXT),'reference_id':nullable(TEXT),'template_source':nullable(TEXT),'evidence':TEXT}),['controller'],'仅evaluator读取选中参考和本构假设来源','query','evaluator')
_ftime=np.geomspace(.1,100.,10)
FLOW_EXAMPLE=dict(shear_rates_s_inv=_ftime.tolist(),torques_nm=hb_torque(_ftime,.0125,.2,.3,.7).tolist(),radius_m=.0125,accept_no_slip_hb_model=True)
cap('analysis.flow_curve',FLOW_INPUT,FLOW_OUTPUT,['controller'],'只使用外部旋转流动曲线逆拟合HB屈服/K/n；单点测量不返回假屈服值','analysis',example=FLOW_EXAMPLE)
_ot=np.linspace(0,2.,201);_gamma=.1;_freq=5.;_sig=_gamma*(2*np.sin(2*math.pi*_freq*_ot)+3*np.cos(2*math.pi*_freq*_ot))
OSC_EXAMPLE=dict(time_s=_ot.tolist(),torques_nm=(math.pi/2*.0125**3*_sig).tolist(),radius_m=.0125,strain_amplitude=_gamma,frequency_hz=_freq,discard_time_s=1.)
cap('analysis.oscillation',OSC_INPUT,OSC_OUTPUT,['controller'],'实际外部力矩尾窗正交回归Gprime/Gdoubleprime，不读样品真值','analysis',example=OSC_EXAMPLE)
cap('sim.step',obj({'dt_s':number('s',0,86400)}),obj({'time_s':number('s',0)}),['controller'],'宿主唯一时钟，携带不足纳秒余数；内部采样与场景帧率分离','simulation','evaluator',{'dt_s':.1})
LEDGER_UNITS=dict(mass_loaded_kg='kg',mass_present_kg='kg',mass_removed_kg='kg',mass_error_kg='kg',volume_loaded_m3='m^3',volume_present_m3='m^3',volume_removed_m3='m^3',volume_error_m3='m^3',mechanical_work_j='J',viscous_heat_to_thermostat_j='J',elastic_stored_j='J',elastic_removed_j='J',mechanical_error_j='J',controller_input_j='J',ambient_loss_j='J',sample_loaded_heat_j='J',sample_removed_heat_j='J',thermal_stored_j='J',thermal_error_j='J')
cap('ledger.read',obj({}),obj({k:number(u) for k,u in LEDGER_UNITS.items()}),['controller'],'核对质量/体积、本构机械功-弹性-耗散、温控热量三个账本','query','evaluator')
UNSUPPORTED=['geometry.cone_plate','geometry.cup_cylinder','measurement.normal_force','measurement.wall_slip','measurement.thixotropy_empirical','measurement.automatic_sweep','accuracy.certification','noise.empirical','robot.load','hardware.connect']
for key in UNSUPPORTED:cap(key,obj({}),obj({}),['controller'],'未实现：'+key)
for key,component in [('plant.configured','controller'),('geometry.configured','rotor'),('sample.present','sample_plate'),('power.enabled','power_switch'),('thermal.enabled','sample_plate'),('geometry.moving','head_lift')]:slot(key,BOOL,'1','配置或设备可观测状态',component=component)
slot('geometry.gap_m',number('m',0,.08),'m','测量板实际间隙；机械参考0，测量方法另要求至少0.0001m','scene',component='head_lift')
slot('geometry.target_gap_m',number('m',.0001,.08),'m','间隙设定，实际lift只随step推进','setpoint',component='head_lift')
slot('rotor.angle_rad',number('rad'),'rad','转子连续累计角，真实多圈无wrap；场景频率受帧率限制','scene',component='rotor')
slot('rotor.orientation_rad',number('rad',-math.pi,math.pi),'rad','累计实转角的atan2(sin,cos)等价朝向，仅供场景姿态设置；跨π存在表示跳变，不用于速度差分或替代累计角','derived',component='rotor')
slot('rotor.speed_rad_s',number('rad/s'),'rad/s','理想运动控制实际角速度，无电机惯量模型','derived',component='rotor')
slot('power.angle_rad',number('rad',0,.28),'rad','电源开关造型姿态代理','scene',component='power_switch')
slot('thermal.target_k',number('K',273.15,373.15),'K','温控目标，成功不表示到温','setpoint',component='sample_plate')
slot('thermal.indicated_k',nullable(number('K')),'K','集中温控温度量化观测，不是厂家精度证明','sensor',component='sample_plate')
slot('torque.indicated_nm',nullable(number('N*m')),'N*m','经过显式增益零点校准及0.05nNm量化；低于模式下限或超量程为null','sensor',component='rotor')
slot('clock.time_s',number('s',0),'s','唯一宿主仿真时钟')
slot('acquisition.phase',PHASE,'1','异步采集状态')
slot('acquisition.elapsed_s',number('s',0),'s','本次实际采集时间')
slot('acquisition.points',{'type':'integer','minimum':0,'maximum':200001,'x-unit':'1'},'1','实际内部网格采样数')
for key,unit,meaning in [('truth.temperature_k','K','集中样品/温控台过程温度'),('truth.viscosity_pa_s','Pa*s','选中参考黏度，不是测量输出'),('truth.stress_pa','Pa','边缘本构应力'),('truth.torque_nm','N*m','平行板积分真实扭矩'),('truth.mass_kg','kg','样品质量'),('truth.elastic_j','J','Maxwell储存弹性能')]:slot(key,nullable(number(unit)),unit,meaning,'process_truth','evaluator')
slot('model.fault',nullable(TEXT),'1','本构、温度或量程故障')

class AntonpaarMcr503Runtime:
    def __init__(self,instance_id='mcr503_01'):
        self.instance_id=instance_id;self.plant=None;self.geometry=None;self.sample=None;self.reference=None;self.power=False;self.thermal_enabled=False;self.temperature=298.15;self.target=298.15;self.gap=.08;self.target_gap=.08;self.angle=0.;self.speed=0.;self.stress=0.;self.torque=0.;self.elastic=0.
        self.time_ns=0;self._clock_remainder_ns=0.;self.revision=0;self.method=copy.deepcopy(METHOD_EXAMPLE);self.calibration=copy.deepcopy(CALIBRATION_EXAMPLE);self.phase='idle';self.elapsed=0.;self.rows=[];self.mean_viscosity=None;self.osc_fit=None;self.error=None;self.model_fault=None;self.operation_id=None;self._counter=0;self._start_ns=0;self._next_sample=0;self._start_angle=0.;self._initial_stress=0.;self._source=None;self._reference_id=None;self._evidence='no_measurement';self.records={};self._principals={};self._requests={};self._owners={};self._cancel_operations={}
        self.mass_loaded=self.mass_removed=self.volume_loaded=self.volume_removed=self.mechanical_work=self.viscous_heat=self.elastic_removed=self.controller_input=self.ambient_loss=self.loaded_heat=self.removed_heat=0.
    @property
    def time_s(self):return self.time_ns/1e9
    def register_principal(self,principal_id,visibility='agent'):
        if not isinstance(principal_id,str) or not principal_id or principal_id in self._principals or visibility not in ('agent','debug','evaluator'):raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id):
        if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return AntonpaarMcr503Session(self,principal_id)
    def describe(self):return export_antonpaar_mcr503_contract_metadata()
    def _moving(self):return abs(self.gap-self.target_gap)>1e-12
    def _mass(self):return self.sample['density_kg_m3']*self.sample['volume_m3'] if self.sample else 0.
    def _capacity(self):return self.plant['plate_capacity_j_k']+(self._mass()*self.sample['heat_capacity_j_kg_k'] if self.sample else 0.)
    def _indicated_torque(self,torque,mode=None):
        if not self.power:return None
        value=(self.plant['torque_gain']*torque+self.plant['torque_offset_nm']-self.calibration['torque_zero_nm'])*self.calibration['torque_gain_correction'] if self.plant else 0.
        minimum=TORQUE_OSCILLATION_MIN if (mode or self.method['mode'])=='oscillation' else TORQUE_ROTATION_MIN
        if abs(value)<minimum or abs(value)>TORQUE_MAX:return None
        return float(round(value/TORQUE_RESOLUTION)*TORQUE_RESOLUTION)
    def _indicated_temperature(self):return round(self.temperature/self.plant['temperature_resolution_k'])*self.plant['temperature_resolution_k'] if self.power and self.plant else None
    def read(self,role='evaluator'):
        if role not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
        values={'plant.configured':self.plant is not None,'geometry.configured':self.geometry is not None,'sample.present':self.sample is not None,'power.enabled':self.power,'thermal.enabled':self.thermal_enabled,'geometry.moving':self._moving(),'geometry.gap_m':self.gap,'geometry.target_gap_m':self.target_gap,'rotor.angle_rad':self.angle,'rotor.orientation_rad':math.atan2(math.sin(self.angle),math.cos(self.angle)),'rotor.speed_rad_s':self.speed,'power.angle_rad':.28 if self.power else 0.,'thermal.target_k':self.target,'thermal.indicated_k':self._indicated_temperature(),'torque.indicated_nm':self._indicated_torque(self.torque),'clock.time_s':self.time_s,'acquisition.phase':self.phase,'acquisition.elapsed_s':self.elapsed,'acquisition.points':len(self.rows),'truth.temperature_k':self.temperature if self.plant else None,'truth.viscosity_pa_s':self.sample['eta'] if self.sample else None,'truth.stress_pa':self.stress if self.sample else None,'truth.torque_nm':self.torque if self.sample else None,'truth.mass_kg':self._mass() if self.sample else None,'truth.elastic_j':self.elastic,'model.fault':self.model_fault}
        return {k:v for k,v in values.items() if role!='agent' or SLOTS[k]['visibility']=='agent'}
    def _thermal(self,dt):
        if not self.plant:return
        p=self.plant;C=self._capacity();G=p['ambient_conductance_w_k'];remaining=dt
        while remaining>1e-12:
            T=self.temperature;K=p['controller_gain_w_k'] if self.power and self.thermal_enabled else 0.;feed=G*(self.target-p['ambient_k']) if K else 0.;low=self.target+(feed-p['heater_max_w'])/K if K else -math.inf;high=self.target+(feed+p['cooler_max_w'])/K if K else math.inf
            if K and T<low-1e-10:a=G;b=p['heater_max_w']+G*p['ambient_k'];P=p['heater_max_w'];boundary=low
            elif K and T>high+1e-10:a=G;b=-p['cooler_max_w']+G*p['ambient_k'];P=-p['cooler_max_w'];boundary=high
            else:a=G+K;b=(G+K)*self.target if K else G*p['ambient_k'];P=None if K else 0.;boundary=None
            if a==0:
                velocity=b/C;duration=remaining
                if boundary is not None and velocity*(boundary-T)>0:duration=min(duration,(boundary-T)/velocity)
                Tnew=T+velocity*duration;integral=T*duration+.5*velocity*duration**2
            else:
                equilibrium=b/a;duration=remaining
                candidates=[boundary] if boundary is not None else [low,high] if K else []
                for bound in candidates:
                    if math.isfinite(bound) and (bound-T)*(equilibrium-T)>0 and abs(bound-T)<abs(equilibrium-T) and abs(bound-T)>1e-10:
                        crossing=-C/a*math.log((bound-equilibrium)/(T-equilibrium))
                        if crossing>1e-12:duration=min(duration,crossing)
                decay=math.exp(-a*duration/C);Tnew=equilibrium+(T-equilibrium)*decay;integral=equilibrium*duration+(T-equilibrium)*C/a*(-math.expm1(-a*duration/C))
            input_energy=K*(self.target*duration-integral)+feed*duration if P is None else P*duration;loss=G*(integral-p['ambient_k']*duration)
            self.controller_input+=input_energy;self.ambient_loss+=loss;self.temperature=Tnew;remaining-=duration
    def _mechanical(self,t):
        """Analytic stress/angle/work from operation start, using only host model state."""
        s=self.sample;m=self.method;R=self.geometry['radius_m'];h=self.gap;V=math.pi*R*R*h;eta=s['eta'];kind=s['constitutive_model'];tau0=self._initial_stress
        if m['mode']=='rotation':
            rate=m['shear_rate_s_inv'];strain=rate*t;speed=rate*h/R;angle=self._start_angle+strain*h/R
            if kind=='herschel_bulkley':
                K=eta*s['reference_shear_rate_s_inv']**(1-s['flow_index']);stress=s['yield_stress_pa']+K*rate**s['flow_index'];torque=float(hb_torque(rate,R,s['yield_stress_pa'],K,s['flow_index']));work=torque*speed*t;elastic=0.
            elif kind=='maxwell':
                lam=s['relaxation_time_s'];G=eta/lam;target=eta*rate;e=math.exp(-t/lam);stress=target+(tau0-target)*e;torque=math.pi/2*R**3*stress;work=V/2*rate*(target*t+(tau0-target)*lam*(1-e));elastic=V*stress*stress/(4*G)
            else:stress=eta*rate;torque=pp_torque(rate,R,eta);work=torque*speed*t;elastic=0.
        else:
            w=2*math.pi*m['frequency_hz'];gamma=m['strain_amplitude'];strain=gamma*math.sin(w*t);rate=gamma*w*math.cos(w*t);speed=rate*h/R;angle=self._start_angle+strain*h/R
            if kind=='maxwell':
                lam=s['relaxation_time_s'];G=eta/lam;u=w*lam;A=G*gamma*u*u/(1+u*u);B=G*gamma*u/(1+u*u);D=tau0-B;e=math.exp(-t/lam);stress=A*math.sin(w*t)+B*math.cos(w*t)+D*e
                integral_exp=(e*(-math.cos(w*t)/lam+w*math.sin(w*t))+1/lam)/(1/lam**2+w*w)
                work=V/2*gamma*w*(A*math.sin(w*t)**2/(2*w)+B*(t/2+math.sin(2*w*t)/(4*w))+D*integral_exp);elastic=V*stress*stress/(4*G)
            else:stress=eta*rate;work=V/2*eta*gamma**2*w*w*(t/2+math.sin(2*w*t)/(4*w));elastic=0.
            torque=math.pi/2*R**3*stress
        return dict(strain=strain,rate=rate,stress=stress,torque=torque,angle=angle,speed=speed,work=work,elastic=elastic)
    def _relax(self,dt):
        if self.sample and self.sample['constitutive_model']=='maxwell':
            old=self.elastic;decay=math.exp(-dt/self.sample['relaxation_time_s']);self.stress*=decay;self.torque*=decay;self.elastic*=decay**2;self.viscous_heat+=old-self.elastic
        else:self.stress=0.;self.torque=0.;self.elastic=0.
        self.speed=0.
    def _result(self):return dict(operation_id=self.operation_id,phase=self.phase,elapsed_s=self.elapsed,rows=copy.deepcopy(self.rows),mean_apparent_viscosity_pa_s=self.mean_viscosity,oscillation_fit=copy.deepcopy(self.osc_fit),template_source=self._source,evidence=self._evidence,error_code=self.error)
    def _finish(self,phase,error=None):
        self.phase=phase;self.error=error;self.speed=0.
        if not self.sample or self.sample['constitutive_model']!='maxwell':self.stress=self.torque=0.
        if self.operation_id:self.records[self.operation_id]=self._result()
    def _ledger(self):
        volume=self.sample['volume_m3'] if self.sample else 0.;heat=self._capacity()*(self.temperature-self.plant['ambient_k']) if self.plant else 0.
        return dict(mass_loaded_kg=self.mass_loaded,mass_present_kg=self._mass(),mass_removed_kg=self.mass_removed,mass_error_kg=self.mass_loaded-self._mass()-self.mass_removed,volume_loaded_m3=self.volume_loaded,volume_present_m3=volume,volume_removed_m3=self.volume_removed,volume_error_m3=self.volume_loaded-volume-self.volume_removed,mechanical_work_j=self.mechanical_work,viscous_heat_to_thermostat_j=self.viscous_heat,elastic_stored_j=self.elastic,elastic_removed_j=self.elastic_removed,mechanical_error_j=self.mechanical_work-self.viscous_heat-self.elastic-self.elastic_removed,controller_input_j=self.controller_input,ambient_loss_j=self.ambient_loss,sample_loaded_heat_j=self.loaded_heat,sample_removed_heat_j=self.removed_heat,thermal_stored_j=heat,thermal_error_j=self.controller_input+self.loaded_heat-self.ambient_loss-self.removed_heat-heat)
    def invoke(self,capability,**a):
        if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability]['input_schema'],a)
        if capability in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
        if capability=='analysis.flow_curve':return fit_flow(**a)
        if capability=='analysis.oscillation':return fit_oscillation(**a)
        if capability=='acquisition.result':return self._result()
        if capability=='provenance.read':return dict(operation_id=self.operation_id,reference_id=self._reference_id,template_source=self._source,evidence=self._evidence)
        if capability=='ledger.read':return self._ledger()
        if capability=='sim.step':self.step(a['dt_s']);return {'time_s':self.time_s}
        if self.phase=='running' and capability not in ('acquisition.stop','power.set'):raise ActionRejected('BUSY')
        if capability=='plant.configure':
            if self.plant or not a['accept_engineering']:raise ActionRejected('PRECONDITION_FAILED')
            self.plant=copy.deepcopy(a);self.temperature=self.target=a['ambient_k'];out={'configured':True}
        elif capability=='geometry.configure':
            if self.sample or not a['accept_parallel_plate_no_slip']:raise ActionRejected('PRECONDITION_FAILED')
            self.geometry=copy.deepcopy(a);out={'configured':True}
        elif capability=='sample.load':
            if not self.plant or not self.geometry or self.sample or self._moving() or self.gap<.079999:raise ActionRejected('PRECONDITION_FAILED')
            record=library().get(a['reference_id'])
            if record is None:raise ActionRejected('UNKNOWN_REFERENCE')
            eta=record.get('viscosity_Pa_s');T=record.get('reference_temperature_K')
            if not isinstance(eta,(int,float)) or isinstance(eta,bool) or not math.isfinite(eta) or eta<=0 or not isinstance(T,(int,float)) or not math.isfinite(T):raise ActionRejected('REFERENCE_DATA')
            if not a['accept_engineering_rheology']:raise ActionRejected('ASSUMPTION_NOT_ACCEPTED')
            fill_gap=a['volume_m3']/(math.pi*self.geometry['radius_m']**2)
            if not .0001<=fill_gap<=.005:raise ActionRejected('SAMPLE_VOLUME')
            self.sample=copy.deepcopy(a);self.sample.update(eta=eta,reference_k=T);self.reference=copy.deepcopy(record);self.mass_loaded+=self._mass();self.volume_loaded+=a['volume_m3'];self.loaded_heat+=self._mass()*a['heat_capacity_j_kg_k']*(self.temperature-self.plant['ambient_k']);self.stress=self.torque=self.elastic=0.;out={'loaded':True}
        elif capability=='sample.remove':
            if not self.sample or self._moving() or self.gap<.079999:raise ActionRejected('PRECONDITION_FAILED')
            self.mass_removed+=self._mass();self.volume_removed+=self.sample['volume_m3'];self.removed_heat+=self._mass()*self.sample['heat_capacity_j_kg_k']*(self.temperature-self.plant['ambient_k']);self.elastic_removed+=self.elastic;self.sample=self.reference=None;self.stress=self.torque=self.elastic=0.;out={'loaded':False}
        elif capability=='geometry.set_gap':
            if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
            if self.sample and a['gap_m']<self.sample['volume_m3']/(math.pi*self.geometry['radius_m']**2)-1e-10:raise ActionRejected('SQUEEZE_FLOW_UNSUPPORTED')
            self.target_gap=a['gap_m'];out={'target_gap_m':self.target_gap}
        elif capability=='power.set':
            if not a['enabled'] and self.phase=='running':self._finish('cancelled','POWER_OFF')
            self.power=a['enabled']
            if not self.power:self.thermal_enabled=False
            out={'enabled':self.power}
        elif capability=='thermal.set_temperature':
            if not self.plant or not self.power:raise ActionRejected('PRECONDITION_FAILED')
            self.target=a['temperature_k'];self.thermal_enabled=True;out={'target_k':self.target}
        elif capability=='thermal.disable':self.thermal_enabled=False;out={'enabled':False}
        elif capability=='method.configure':
            if a['discard_time_s']>=a['duration_s']:raise ActionRejected('ANALYSIS_WINDOW')
            points=a['duration_s']/a['sample_interval_s']
            if points<20 or points>200000 or abs(points-round(points))>1e-8 or any(abs(a[k]*1e9-round(a[k]*1e9))>1e-4 for k in ('duration_s','sample_interval_s')):raise ActionRejected('SAMPLE_GRID')
            if a['mode']=='oscillation' and (a['sample_interval_s']>1/(20*a['frequency_hz'])+1e-12 or a['duration_s']-a['discard_time_s']<2/a['frequency_hz']):raise ActionRejected('OSCILLATION_SAMPLING')
            self.method=copy.deepcopy(a);out={'configured':True}
        elif capability=='sensor.calibrate':self.calibration=copy.deepcopy(a);out={'configured':True}
        elif capability=='acquisition.start':
            if not self.power or not self.plant or not self.geometry or not self.sample or self._moving():raise ActionRejected('PRECONDITION_FAILED')
            if abs(self.gap-self.sample['volume_m3']/(math.pi*self.geometry['radius_m']**2))>1e-9:raise ActionRejected('GAP_FILL_MISMATCH')
            if abs(self.temperature-self.sample['reference_k'])>self.method['temperature_tolerance_k'] or abs(self.target-self.sample['reference_k'])>self.method['temperature_tolerance_k']:raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
            if self.method['mode']=='oscillation' and self.sample['constitutive_model']=='herschel_bulkley':raise ActionRejected('HB_OSCILLATION_UNSUPPORTED')
            angular_speed=(self.method['shear_rate_s_inv'] if self.method['mode']=='rotation' else self.method['strain_amplitude']*2*math.pi*self.method['frequency_hz'])*self.gap/self.geometry['radius_m']
            if angular_speed>314.:raise ActionRejected('ANGULAR_SPEED_RANGE')
            if self.method['mode']=='rotation':maximum=float(hb_torque(self.method['shear_rate_s_inv'],self.geometry['radius_m'],self.sample['yield_stress_pa'],self.sample['eta']*self.sample['reference_shear_rate_s_inv']**(1-self.sample['flow_index']),self.sample['flow_index'])) if self.sample['constitutive_model']=='herschel_bulkley' else abs(pp_torque(self.method['shear_rate_s_inv'],self.geometry['radius_m'],self.sample['eta']))
            else:maximum=abs(pp_torque(self.method['strain_amplitude']*2*math.pi*self.method['frequency_hz'],self.geometry['radius_m'],self.sample['eta']))+abs(self.torque)
            if maximum>TORQUE_MAX:raise ActionRejected('TORQUE_OVERLOAD')
            self._counter+=1;self.operation_id=f'{self.instance_id}:measurement:{self._counter}';self._start_ns=self.time_ns;self._next_sample=0;self._start_angle=self.angle;self._initial_stress=self.stress;self.phase='running';self.elapsed=0.;self.rows=[];self.mean_viscosity=None;self.osc_fit=None;self.error=None;self.model_fault=None;self._reference_id=self.sample['reference_id'];source=self.reference.get('source');self._source=source if isinstance(source,str) else json.dumps(source,ensure_ascii=False,sort_keys=True);self._evidence='real_viscosity_reference; '+('newtonian_assumption' if self.sample['constitutive_model']=='newtonian' else 'explicit_engineering_'+self.sample['constitutive_model']);out={'operation_id':self.operation_id}
        elif capability=='acquisition.stop':
            if self.phase!='running':raise ActionRejected('NOT_RUNNING')
            self._finish('cancelled','USER_CANCEL');out={'phase':self.phase,'elapsed_s':self.elapsed}
        else:raise ActionRejected('UNKNOWN_CAPABILITY')
        self.revision+=1;return copy.deepcopy(out)
    def step(self,dt_s):
        validate(number('s',0,86400),dt_s);total_ns=math.fsum((self._clock_remainder_ns,dt_s*1e9));ns=round(total_ns);self._clock_remainder_ns=total_ns-ns
        if ns==0:return
        end_ns=self.time_ns+ns;dt=ns/1e9
        if self.plant:
            distance=self.plant['lift_speed_m_s']*dt;remaining_gap=self.target_gap-self.gap
            self.gap=self.target_gap if abs(remaining_gap)<=distance else self.gap+math.copysign(distance,remaining_gap)
        if self.phase=='running':
            stop_ns=self._start_ns+round(self.method['duration_s']*1e9);active_end=min(end_ns,stop_ns);previous_t=(self.time_ns-self._start_ns)/1e9;fault=None
            interval=round(self.method['sample_interval_s']*1e9);thermal_time=self.time_ns
            while self._start_ns+self._next_sample*interval<=active_end:
                stamp=self._start_ns+self._next_sample*interval;self._thermal((stamp-thermal_time)/1e9);thermal_time=stamp
                if abs(self.temperature-self.sample['reference_k'])>self.method['temperature_tolerance_k']:
                    active_end=stamp;fault='REFERENCE_TEMPERATURE_RANGE';break
                sample=self._mechanical((stamp-self._start_ns)/1e9);torque=self._indicated_torque(sample['torque']);viscosity=2*torque/(math.pi*self.geometry['radius_m']**3*sample['rate']) if torque is not None and self.method['mode']=='rotation' and sample['rate'] else None
                self.rows.append(dict(time_s=(stamp-self._start_ns)/1e9,strain=sample['strain'],shear_rate_s_inv=sample['rate'],torque_nm=torque,temperature_k=self._indicated_temperature(),apparent_viscosity_pa_s=viscosity));self._next_sample+=1
            self._thermal((active_end-thermal_time)/1e9);t=(active_end-self._start_ns)/1e9;before=self._mechanical(previous_t);after=self._mechanical(t);work=after['work']-before['work'];released=work-(after['elastic']-self.elastic);self.mechanical_work+=work;self.viscous_heat+=released;self.stress=after['stress'];self.torque=after['torque'];self.elastic=after['elastic'];self.angle=after['angle'];self.speed=after['speed'];self.time_ns=active_end;self.elapsed=t
            if fault or abs(self.temperature-self.sample['reference_k'])>self.method['temperature_tolerance_k']:
                self.model_fault='REFERENCE_TEMPERATURE_RANGE';self._finish('failed',self.model_fault)
            elif active_end==stop_ns:
                try:
                    if self.method['mode']=='oscillation':self.osc_fit=fit_oscillation([r['time_s'] for r in self.rows],[r['torque_nm'] for r in self.rows],self.geometry['radius_m'],self.method['strain_amplitude'],self.method['frequency_hz'],self.method['discard_time_s'])
                    else:
                        tail=[r['apparent_viscosity_pa_s'] for r in self.rows if r['time_s']>=self.method['discard_time_s'] and r['apparent_viscosity_pa_s'] is not None]
                        if not tail:raise ActionRejected('TORQUE_BELOW_RANGE')
                        self.mean_viscosity=sum(tail)/len(tail)
                    self._finish('succeeded')
                except ActionRejected as exc:self._finish('failed',str(exc))
            remainder=(end_ns-active_end)/1e9
            if remainder:self._thermal(remainder);self._relax(remainder)
        else:self._thermal(dt);self._relax(dt)
        self.time_ns=end_ns;self.revision+=1

class AntonpaarMcr503Session:
    def __init__(self, host, principal_id): self._host = host; self._principal = principal_id
    @property
    def _role(self): return self._host._principals[self._principal]
    def _target(self, instance_id):
        if instance_id != self._host.instance_id: raise ActionRejected('UNKNOWN_INSTANCE')
    def describe(self, *, instance_id=None, asset_type_id=None):
        if (instance_id is None) == (asset_type_id is None): raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None: self._target(instance_id)
        if asset_type_id is not None and asset_type_id != ASSET_TYPE_ID: raise ActionRejected('UNKNOWN_INSTANCE')
        meta = export_antonpaar_mcr503_contract_metadata()
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
            result['evidence'] = 'observed_torque_trace_parallel_plate_model; selected template details require evaluator provenance.read' if result['operation_id'] is not None else 'no_measurement'
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
 'plant.configure':['plant.configured','truth.temperature_k','thermal.target_k'],
 'geometry.configure':['geometry.configured'],
 'sample.load':['sample.present','truth.viscosity_pa_s','truth.stress_pa','truth.torque_nm','truth.mass_kg','truth.elastic_j','torque.indicated_nm'],
 'sample.remove':['sample.present','truth.viscosity_pa_s','truth.stress_pa','truth.torque_nm','truth.mass_kg','truth.elastic_j','torque.indicated_nm'],
 'geometry.set_gap':['geometry.target_gap_m','geometry.moving'],
 'power.set':['power.enabled','power.angle_rad','thermal.enabled','thermal.indicated_k','torque.indicated_nm','acquisition.phase','rotor.speed_rad_s','truth.stress_pa','truth.torque_nm'],
 'thermal.set_temperature':['thermal.target_k','thermal.enabled'],
 'thermal.disable':['thermal.enabled'],
 'method.configure':['torque.indicated_nm'],
 'sensor.calibrate':['torque.indicated_nm'],
 'acquisition.start':['acquisition.phase','acquisition.elapsed_s','acquisition.points','model.fault'],
 'acquisition.stop':['acquisition.phase','rotor.speed_rad_s','truth.stress_pa','truth.torque_nm','torque.indicated_nm'],
 'sim.step':list(SLOTS)}
for k,v in EFFECTS.items():CAPS[k]['effects']=v
ERRORS=sorted(set(__import__('re').findall(r"ActionRejected\('([A-Z_0-9]+)'",Path(__file__).read_text()))|{'POWER_OFF','USER_CANCEL'})

def export_antonpaar_mcr503_contract_metadata():
    slots={};caps={}
    for k,v in SLOTS.items():
        slots[k]=dict(meaning_zh=v['meaning'],component_ids=[v['component']],value_schema=copy.deepcopy(v['schema']),unit=v['unit'],reference_conditions='SI; PP25 reference geometry. Public viscosity measured at stated reference temperature. Torque resolution50pNm; minimum rotation1nNm/oscillation0.15nNm; max0.255Nm.',origin=v['origin'],visibility=v['visibility'],access='read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'AntonpaarMcr503Session.read(state_ids=[{k!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description='Unique nanosecond host clock with fractional-nanosecond carry; internal acquisition grid independent from rendered scene frame rate.'),source_ids=['simulation_assumption'])
    limits=[
      '默认固定PP25平行板；其它半径可用于数值分析但场景不会自动缩放。几何半径、精确间隙和无滑移假设必须与试验配置一致；锥板、同轴筒、壁滑、法向力、惯性和边缘破裂未实现。',
      'Newtonian eta来自真实物性参考单点；模板不等于流变仪实测曲线。Maxwell lambda、HB屈服应力/流动指数/参考剪切率由宿主显式给定并标assumption；G=eta/lambda不冒充真实模量文献数据。',
      '旋转输出2M/(piR^3*gamma_edge)为平行板表观黏度；非Newtonian材料不能当作已做Weissenberg–Rabinowitsch修正。屈服值只由外部多剪切速率力矩数据的HB拟合返回。',
      '振荡Gprime/Gdoubleprime由公开观测尾窗回归，至少20点每周期、尾窗至少两周期；瞬态未充分衰减时拟合会偏移并保留RMS，未实现厂家DSP或非线性LAOS分析。',
      '默认水样PP25/gap1mm/边缘100s^-1的gap Reynolds约8.9，不能忽略真实二次流对准确度的影响；当前无惯性本构数值演示不证明该工况真实流变测量准确。',
      '声称的最小扭矩和分辨率仅作为量程/量化规则；没有真实仪器误差分布、噪声谱、溯源校准或准确度认证。低于量程为null，不能把零交叉的缺失数据伪造为精确零。',
      '温控是宿主显式C/K/G/功率的集中参数Peltier教学模型，没有附件型号保证；273.15至373.15K是本模型范围。温度设定仅写目标，测量只在真实参考温度容差内有效，不将常数eta外推到其它温度。',
      '本构耗散由理想等温补偿器即时传给温控浴并独立记账；没有将局部黏性发热耦合成温度梯度。温控台与样品按单一热容节点求控制/散热，未知rho/Cp由宿主显式假设。',
      '样品体积只在load/remove转移；测量须满足V=piR²gap，间隙调小到填充高度以下拒绝，不默默挤出或生灭物质。头升降是理想位置执行，不模拟装卸挤压流动/毛细力。',
      'Maxwell机械功分成可恢复弹性能和耗散；取消停转后真实应力仍按lambda松弛，已发热不回滚；卸样携走剩余弹性能与显热。',
      '标准503最大角速度314rad/s同时限制旋转与振荡峰值角速度；rotor.angle_rad为连续累计实转角，多圈不wrap；rotor.orientation_rad只提供atan2(sin(angle),cos(angle))的等价场景朝向，不能用于差分速度；250Hz是数值方法上限，不声称60Hz场景逐帧解析高频运动；实际转子关节/面板需独立场景验证。',
      '全部session公开acquisition.result/poll都只返回通用模板库来源，以免bridge以evaluator代理调用时泄漏选中材料；完整来源仅evaluator provenance.read。共享进程和文件不提供OS级安全隔离。',
      '自动扫频/扫剪切工作流、实测触变恢复、夹具真实接触、机器人和硬件not_run；单点和单频可重复测量，外部流动曲线/振荡分析接口已实现。']
    for k,v in CAPS.items():
        un=k in UNSUPPORTED;async_=k=='acquisition.start';caps[k]={key:copy.deepcopy(v[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']}
        caps[k].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ('scene','robot','hardware') else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.antonpaar_mcr503.AntonpaarMcr503Session.invoke',invocation=f'session.invoke(instance_id="mcr503_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),preconditions=[] if un or v['kind'] in ('analysis','query') else [dict(description_zh='供电、装样、真实填充间隙、温度参考、扭矩量程和采样前提由运行入口原子检查。',state_ids=['power.enabled','sample.present','geometry.moving','acquisition.phase','thermal.indicated_k'],enforced_by='runtime',check='AntonpaarMcr503Runtime.invoke validates relevant guards before committing state')],state_effects=[dict(state_id=s,timing='immediate',description_zh='实际命令即时变化') for s in v['effects']],observes=[],model_effects=dict(mode='none',description_zh='本模块输出可绑定数值；连续旋转、升降和面板的实际场景验证由独立报告登记。',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='完成实际内部力矩采样及观测尾窗分析' if async_ else '原子输入被接受；设定类动作须等待宿主step物理演化',completion_state_ids=['acquisition.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','parallel_plate','sample','torque_sensor'],partial_failure_zh='取消冻结已采集数据并停转；保留材料应力/弹性、已耗散热与温度，随后松弛继续，卸样显式转移质量/体积及热量。'),errors=[dict(code=e,meaning_zh=e,side_effects='partial' if async_ and e in ('POWER_OFF','USER_CANCEL','REFERENCE_TEMPERATURE_RANGE','TORQUE_BELOW_RANGE') else 'none') for e in ERRORS],verification=[],limitations=limits,source_ids=['manufacturer_primary','simulation_assumption'])
        if k in ('acquisition.start','geometry.set_gap','thermal.set_temperature','thermal.disable','acquisition.stop'):
            caps[k]['state_effects'] += [dict(state_id=s,timing='on_step',description_zh='宿主step后的有限运动/传热/本构/内部采样') for s in SLOTS if s not in v['effects'] and s not in ('plant.configured','geometry.configured','sample.present','power.enabled')]
    mechanism=dict(component_ids=['controller','sample_plate','head_lift','rotor'],state_ids=list(slots),meaning_zh='平行板剪切积分、线性Maxwell瞬态与热/机械/物质账本，输出来自独立观测分析',implementation='lumped',equations=[
      'gamma_dot(r)=Omega*r/h. Newtonian M=pi*eta*Omega*R^4/(2h)=pi*eta*gamma_dot_edge*R^3/2.',
      'HB tau=tau_y+K*gamma_dot^n; M=2piR^3[tau_y/3+K*gamma_dot_edge^n/(n+3)]. K=eta_reference*gamma_reference^(1-n), and tau_y/n/gamma_reference are explicit model assumptions.',
      'Maxwell d(tau)/dt=G*gamma_dot-tau/lambda, G=eta_reference/lambda. Constant-rate exact tau=eta*rate+(tau_initial-eta*rate)exp(-t/lambda).',
      'Sinusoidal strain gamma0*sin(omega*t) produces tau=G*gamma0[(omega*lambda)^2*sin/(1+(omega*lambda)^2)+(omega*lambda)*cos/(1+(omega*lambda)^2)]+transient; no steady-state assumption in forward response.',
      'PP Maxwell elastic U=V*tau_edge²/(4G), work W=integral(M*Omega dt), dissipation D=W-dU; exact sin/cos/exponential antiderivatives. At stop gamma_dot=0, tau decays and U loss becomes heat to ideal thermostat.',
      'C_total=C_plate+sample_mass*Cp. C*dT/dt=clip(Kp*(Ttarget-T)+Gambient*(Ttarget-Tambient),-Pcool,Pheat)-Gambient*(T-Tambient), exact exponential within each saturation regime; crossings split analytically; controller and ambient integrals balance stored heat.',
      'No constitutive self-heating temperature field: rheological dissipation delivered directly to an ideal isothermal compensator, separately metered; thermal node balance concerns explicit control, ambient, and loaded/removed sensible heat.',
      'Torque observation=(gain*M+offset-calibration_zero)*calibration_gain, quantized at50pNm, null below1nNm(rotation)/0.15nNm(oscillation) or above0.255Nm. No empirical random noise claimed.',
      'Gprime/Gdoubleprime extracted by ordinary least-squares observed stress on gamma0*sin and gamma0*cos plus offset in explicit tail window; external HB inverse uses only supplied torque/rate observations.',
      'Mass=rho*volume, volume is immutable while loaded; V=piR²gap checked before acquisition. Removal carries mass, volume, sensible heat and remaining elastic energy to separate ledgers.',
      'Host step accepts finite nonnegative float seconds with fractional-ns carry; internal grid supports20+samples/cycle through250Hz independent of scene rendering. Physical accumulated rotor angle is not wrapped; separate orientation=atan2(sin(angle),cos(angle)) is a scene pose representation only, never differentiated for angular velocity.'],parameters={},source_ids=['simulation_assumption','template_library'],limitations=limits)
    spec=importlib.util.spec_from_file_location('_mcr_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()});ann.annotate_export(SLUG,caps,slots,{'parallel_plate_rheology':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'geometry.configure':(GEOMETRY,GEOMETRY_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sensor.calibrate':(CALIBRATION,CALIBRATION_EXAMPLE)})
    # Shared annotation defaults include generic sensor dynamics and a vessel
    # label; this asset has instantaneous quantization and a plate/sample node.
    slots['thermal.indicated_k']['meaning_zh']='平行板样品与温控台集中节点的即时量化温度观测；无传感器一阶滞后模型，未按厂家温度精度标定；断电为null。'
    slots['torque.indicated_nm']['meaning_zh']='积分力矩经显式增益、零点校准及0.05nNm即时量化得到；低于模式下限或超量程为null。没有传感器时间响应模型或真实噪声标定。'
    slots['truth.temperature_k']['meaning_zh']='PP25样品与温控台合并热容节点的过程温度；仅evaluator/debug可见，用于能量平衡，不是厂家实测读数。'
    slots['thermal.target_k']['meaning_zh']='温控目标设定，范围273.15至373.15K；由plant.configure初始化，并由thermal.set_temperature修改；设定只写目标，实际温度仅随宿主step演化。'
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'parallel_plate_rheology':mechanism})

"""CHEM138 TAM IV20mL: differential calorimetry with conserved sealed dissolution.

Three finite thermal nodes + one heat-flux sensor + explicitly assumed first-order
extent are integrated together. Stopping acquisition never stops sealed chemistry.
Public enthalpies are reaction data, not fifty invented TAM kinetic experiments.
"""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
import numpy as np
from scipy.linalg import expm
from scipy.optimize import minimize_scalar
from scipy.integrate import solve_ivp
PROTOCOL_VERSION='1.0.0';SLUG='ta_tamiv';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-138'
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch04/t1_thermal'/SLUG;LIBRARY_PATH=ASSET/'microcalorimetry_library.json'

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

def first_order_observation(time_s,rate_s_inv,thermal_tau_s,sensor_tau_s):
    t=np.asarray(time_s,float);k=rate_s_inv;a=1/thermal_tau_s;b=1/sensor_tau_s
    rates=np.array([k,a,b])
    if min(abs(k-a),abs(k-b),abs(a-b))<1e-5*max(rates):
        A=np.array([[-k,0,0],[k*a,-a,0],[0,b,-b]],float)
        return np.array([(expm(A*max(float(v),0))@np.array([1.,0.,0.]))[2] if v>=0 else 0. for v in t])
    out=np.zeros_like(t);valid=t>=0
    for i,r in enumerate(rates):out[valid]+=k*a*b*np.exp(-r*t[valid])/np.prod(np.delete(rates,i)-r)
    return out

def fit_kinetics(time_s,heat_flow_w,trigger_time_s,thermal_tau_s,sensor_tau_s,minimum_rate_s_inv,maximum_rate_s_inv,accept_fixed_bath_first_order):
    if not accept_fixed_bath_first_order:raise ActionRejected('ASSUMPTION_NOT_ACCEPTED')
    t=np.asarray(time_s,float);y=np.asarray(heat_flow_w,float)
    if t.shape!=y.shape or len(t)<20 or np.any(np.diff(t)<=0) or np.ptp(y)<1e-14 or minimum_rate_s_inv>=maximum_rate_s_inv:raise ActionRejected('ANALYSIS_DATA')
    t=t-trigger_time_s;scale=np.ptp(y);centered=(t-np.mean(t))/max(np.ptp(t),1e-12)
    def evaluate(logk):
        kernel=first_order_observation(t,math.exp(logk),thermal_tau_s,sensor_tau_s);X=np.column_stack([kernel,np.ones(len(t)),centered]);coef=np.linalg.lstsq(X,y,rcond=None)[0];residual=y-X@coef;return float(np.mean((residual/scale)**2)),coef,residual
    lo=math.log(minimum_rate_s_inv);hi=math.log(maximum_rate_s_inv);grid=np.linspace(lo,hi,31);best=int(np.argmin([evaluate(x)[0] for x in grid]));f=minimize_scalar(lambda z:evaluate(z)[0],bounds=(grid[max(best-1,0)],grid[min(best+1,len(grid)-1)]),method='bounded',options={'xatol':1e-11});cost,coef,residual=evaluate(float(f.x))
    if not f.success or min(f.x-lo,hi-f.x)<1e-5 or t[-1]*math.exp(f.x)<1.:raise ActionRejected('ANALYSIS_UNRESOLVED')
    return dict(rate_s_inv=math.exp(float(f.x)),total_heat_j=float(coef[0]),baseline_at_window_center_w=float(coef[1]),baseline_drift_w_s=float(coef[2]/np.ptp(t)),fit_rms_w=float(np.sqrt(np.mean(residual**2))),evidence='external_observed_heat_flow_fit; fixed_bath_first_order_and_known_two_response_times_are_assumptions')

def baseline_fit(time_s,heat_flow_w):
    t=np.asarray(time_s,float);y=np.asarray(heat_flow_w,float)
    if t.shape!=y.shape or len(t)<3 or np.any(np.diff(t)<=0):raise ActionRejected('ANALYSIS_DATA')
    X=np.column_stack([np.ones(len(t)),t]);c=np.linalg.lstsq(X,y,rcond=None)[0]
    return dict(intercept_w=float(c[0]),drift_w_s=float(c[1]),residual_rms_w=float(np.sqrt(np.mean((y-X@c)**2))),evidence='fit_of_supplied_blank_observations_no_hidden_offset_access')

PLANT_EXAMPLE=dict(ambient_k=298.15,bath_capacity_j_k=1000.,cell_capacity_j_k=.05,sample_conductance_w_k=.2,reference_conductance_w_k=.2,retracted_conductance_w_k=.005,ambient_conductance_w_k=1.,controller_conductance_w_k=5.,controller_heating_limit_w=800.,controller_cooling_limit_w=800.,sensor_tau_s=.2,sensor_gain=1.,baseline_offset_w=5e-7,baseline_drift_w_s=1e-12,heatflow_resolution_w=1e-10,heatflow_limit_w=.1,lift_speed_m_s=.48,accept_engineering=True)
PLANT=obj({'ambient_k':number('K',288.15,303.15),'bath_capacity_j_k':number('J/K',1,1e8),'cell_capacity_j_k':number('J/K',1e-4,1000),'sample_conductance_w_k':number('W/K',1e-6,100),'reference_conductance_w_k':number('W/K',1e-6,100),'retracted_conductance_w_k':number('W/K',0,100),'ambient_conductance_w_k':number('W/K',0,1000),'controller_conductance_w_k':number('W/K',1e-5,10000),'controller_heating_limit_w':number('W',.01,10000),'controller_cooling_limit_w':number('W',.01,10000),'sensor_tau_s':number('s',1e-4,1e5),'sensor_gain':number('1',.1,10),'baseline_offset_w':number('W',-.1,.1),'baseline_drift_w_s':number('W/s',-1e-3,1e-3),'heatflow_resolution_w':number('W',1e-12,.001),'heatflow_limit_w':number('W',1e-6,10),'lift_speed_m_s':number('m/s',.001,1.),'accept_engineering':BOOL})
SAMPLE_EXAMPLE=dict(reference_id='halite_dissolution',amount_mol=1e-6,carrier_water_kg=.0001,volume_m3=1e-7,added_heat_capacity_j_k=.418,initial_temperature_k=298.15,first_order_rate_s_inv=.5,accept_ideal_solution_and_kinetics=True)
SAMPLE=obj({'reference_id':TEXT,'amount_mol':number('mol',1e-12,1),'carrier_water_kg':number('kg',1e-9,.1),'volume_m3':number('m^3',1e-10,20e-6),'added_heat_capacity_j_k':number('J/K',1e-4,1000),'initial_temperature_k':number('K',277.15,423.15),'first_order_rate_s_inv':number('1/s',1e-8,1000),'accept_ideal_solution_and_kinetics':BOOL})
REFERENCE_EXAMPLE=dict(mass_kg=.0001,volume_m3=1e-7,added_heat_capacity_j_k=.418,initial_temperature_k=298.15,accept_inert_reference=True)
REFERENCE=obj({'mass_kg':number('kg',1e-9,.1),'volume_m3':number('m^3',1e-10,20e-6),'added_heat_capacity_j_k':number('J/K',1e-4,1000),'initial_temperature_k':number('K',277.15,423.15),'accept_inert_reference':BOOL})
METHOD_EXAMPLE=dict(duration_s=12.,sample_interval_s=.05,equilibrium_tolerance_k=.1)
METHOD=obj({'duration_s':number('s',.001,86400),'sample_interval_s':number('s',1e-4,86400),'equilibrium_tolerance_k':number('K',1e-5,.5)})
CALIBRATION_EXAMPLE=dict(gain_correction=1.,baseline_intercept_w=0.,baseline_drift_w_s=0.)
CALIBRATION=obj({'gain_correction':number('1',.1,10),'baseline_intercept_w':number('W',-.1,.1),'baseline_drift_w_s':number('W/s',-1e-3,1e-3)})
ROW=obj({'time_s':number('s',0),'host_time_s':number('s',0),'heat_flow_w':nullable(number('W')),'cumulative_heat_j':nullable(number('J')),'bath_temperature_k':nullable(number('K'))})
RESULT=obj({'operation_id':nullable(TEXT),'phase':PHASE,'elapsed_s':number('s',0),'rows':array(ROW),'recorded_heat_j':nullable(number('J')),'template_source':nullable(TEXT),'evidence':TEXT,'error_code':nullable(TEXT)})
KINETICS_INPUT=obj({'time_s':array(number('s'),20,2001),'heat_flow_w':array(number('W'),20,2001),'trigger_time_s':number('s'),'thermal_tau_s':number('s',1e-4,1e5),'sensor_tau_s':number('s',1e-4,1e5),'minimum_rate_s_inv':number('1/s',1e-8,1000),'maximum_rate_s_inv':number('1/s',1e-8,1000),'accept_fixed_bath_first_order':BOOL})
KINETICS_OUTPUT=obj({'rate_s_inv':number('1/s',0),'total_heat_j':number('J'),'baseline_at_window_center_w':number('W'),'baseline_drift_w_s':number('W/s'),'fit_rms_w':number('W',0),'evidence':TEXT})
CAPS={};SLOTS={}
def cap(k,i,o,c,z,kind='device_command',visibility='agent',example=None):CAPS[k]=dict(input_schema=i,output_schema=o,component_ids=c,function_zh=z,kind=kind,visibility=visibility,example_arguments=copy.deepcopy(example or {}),effects=[])
def slot(k,s,u,z,origin='derived',visibility='agent',component='controller'):SLOTS[k]=dict(schema=s,unit=u,meaning=z,origin=origin,visibility=visibility,component=component,write=None)
cap('plant.configure',PLANT,obj({'configured':BOOL}),['bath','controller','calorimeter_sensor'],'宿主显式热容量/热导/传感τ/基线与运动假设，不拿厂家限值当随机噪声分布','configuration','evaluator',PLANT_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_ampoule'],'装入固相及纯水载体；真实溶解焓与显式一阶/理想活度假设分开','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.remove',obj({}),obj({'loaded':BOOL}),['sample_ampoule'],'导出所有剩余固相、溶液物种、载体及显热；不回滚已反应量','configuration','evaluator')
cap('reference.load',REFERENCE,obj({'loaded':BOOL}),['reference_ampoule'],'宿主初始化固定参比通道惰性空白及热容量','configuration','evaluator',REFERENCE_EXAMPLE)
cap('reference.remove',obj({}),obj({'loaded':BOOL}),['reference_ampoule'],'宿主卸载固定参比，显式导出质量和显热','configuration','evaluator')
cap('sample.set_lift',obj({'lift_m':number('m',0,.48)}),obj({'target_lift_m':number('m',0,.48)}),['sample_lift'],'宿主手动取放杆有限运动代理，不声称主机主动换样机器人','simulation','evaluator',{'lift_m':0.})
cap('sample.trigger',obj({}),obj({'triggered':BOOL}),['sample_ampoule'],'宿主外部触发密封安瓿溶解；采样停止/断电不暂停已发生化学反应','simulation','evaluator')
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_switch'],'电子控制供电；断电停止采样及电热控制，反应/热传导继续',example={'enabled':True})
cap('thermal.set_temperature',obj({'temperature_k':number('K',277.15,423.15)}),obj({'target_k':number('K',277.15,423.15)}),['bath'],'只写恒温目标，实际三节点温度随唯一宿主step推进',example={'temperature_k':298.15})
cap('thermal.disable',obj({}),obj({'enabled':BOOL}),['bath'],'停止主动浴控制，仍传热并反应')
cap('calibration.set_power',obj({'power_w':number('W',0,.1)}),obj({'power_w':number('W',0,.1)}),['sample_ampoule'],'向样品节点真实注入设定电热，能量按Pdt计入账本；功率上限工程假设',example={'power_w':.0001})
cap('sensor.calibrate',CALIBRATION,obj({'configured':BOOL}),['calorimeter_sensor'],'写入由外部实际空白/电热观测推断的增益与基线，不偷读隐藏参数','configuration',example=CALIBRATION_EXAMPLE)
cap('method.configure',METHOD,obj({'configured':BOOL}),['controller'],'配置采样网格和初始平衡容差','configuration',example=METHOD_EXAMPLE)
cap('acquisition.start',obj({}),obj({'operation_id':TEXT}),['controller','calorimeter_sensor'],'开始异步采样，不自动触发反应')
cap('acquisition.stop',obj({}),obj({'phase':PHASE,'elapsed_s':number('s',0)}),['controller'],'只停止采样，冻结观测积分；密封样品继续反应')
cap('acquisition.result',obj({}),RESULT,['controller'],'公开热流曲线及观测梯形积分，所有会话统一隐藏选中来源','query')
cap('provenance.read',obj({}),obj({'reference_id':nullable(TEXT),'template_source':nullable(TEXT),'evidence':TEXT}),['controller'],'仅evaluator读取当前装入样品的热化学记录与工程动力学溯源；已完成曲线保留其开始时来源快照','query','evaluator')
cap('analysis.baseline',obj({'time_s':array(number('s'),3,2001),'heat_flow_w':array(number('W'),3,2001)}),obj({'intercept_w':number('W'),'drift_w_s':number('W/s'),'residual_rms_w':number('W',0),'evidence':TEXT}),['controller'],'对实际空白观测拟合截距和线性漂移','analysis',example={'time_s':[0.,1.,2.],'heat_flow_w':[1e-7,1.1e-7,1.2e-7]})
cap('analysis.electrical_calibration',obj({'known_power_w':number('W',1e-12,.1),'plateau_observed_w':number('W'),'baseline_observed_w':number('W')}),obj({'gain_correction':number('1',0),'evidence':TEXT}),['controller'],'独立已知电热和实际稳定平台差推导增益校正，不访问真值','analysis',example={'known_power_w':1e-4,'plateau_observed_w':1.21e-4,'baseline_observed_w':1e-6})
_tt=np.linspace(0,20.,101);KINETICS_EXAMPLE=dict(time_s=_tt.tolist(),heat_flow_w=(-.0037*first_order_observation(_tt,.5,1.,.2)).tolist(),trigger_time_s=0.,thermal_tau_s=1.,sensor_tau_s=.2,minimum_rate_s_inv=.01,maximum_rate_s_inv=10.,accept_fixed_bath_first_order=True)
cap('analysis.kinetics',KINETICS_INPUT,KINETICS_OUTPUT,['controller'],'仅以实际外部热流拟合一阶速率和总热；响应时间与固定浴是明确输入假设','analysis',example=KINETICS_EXAMPLE)
cap('sim.step',obj({'dt_s':number('s',0,86400)}),obj({'time_s':number('s',0)}),['controller'],'宿主时钟带不足纳秒余数，统一反应/热/传感/运动/内部采样','simulation','evaluator',{'dt_s':.1})
LEDGER_UNITS=dict(sample_mass_loaded_kg='kg',sample_mass_present_kg='kg',sample_mass_removed_kg='kg',sample_mass_error_kg='kg',reference_mass_loaded_kg='kg',reference_mass_present_kg='kg',reference_mass_removed_kg='kg',reference_mass_error_kg='kg',reactant_loaded_mol='mol',reactant_remaining_mol='mol',reactant_removed_mol='mol',converted_present_mol='mol',converted_removed_mol='mol',reaction_amount_error_mol='mol',controller_input_j='J',electrical_input_j='J',reaction_heat_j='J',ambient_loss_j='J',loaded_sensible_j='J',removed_sensible_j='J',thermal_stored_j='J',energy_error_j='J')
cap('ledger.read',obj({}),obj({k:number(u) for k,u in LEDGER_UNITS.items()}),['controller'],'量热/反应/电热/环境全能量及固液物种与参比质量账本','query','evaluator')
UNSUPPORTED=['hardware.mixing_trigger','reaction.arrest','noise.empirical','accuracy.certification','measurement.flow_cell','measurement.micro_solution_accessory','measurement.nonideal_activities','robot.load','hardware.connect']
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'未实现：'+k)
for k,c in [('plant.configured','controller'),('sample.present','sample_ampoule'),('reference.present','reference_ampoule'),('sample.triggered','sample_ampoule'),('power.enabled','power_switch'),('thermal.enabled','bath'),('sample.moving','sample_lift')]:slot(k,BOOL,'1','实际配置或过程状态',component=c)
for k in ['sample.lift_m','sample.target_lift_m']:slot(k,number('m',0,.48),'m','样品杆上提位移；0插入/.48取样；目标不代表实际位置','setpoint' if 'target' in k else 'scene',component='sample_lift')
slot('power.angle_rad',number('rad',0,.28),'rad','电源开关工程姿态代理','scene',component='power_switch')
slot('bath.target_k',number('K',277.15,423.15),'K','恒温浴目标值，只写目标不推进热过程','setpoint',component='bath')
slot('bath.indicated_k',nullable(number('K')),'K','浴温即时量化API观测，本机没有假造数值屏','sensor',component='bath')
slot('calorimeter.indicated_w',nullable(number('W')),'W','样品减参比热流的一阶传感响应，经显式基线/量化/校准；正值为样品向浴放热','sensor',component='calorimeter_sensor')
slot('calibration.power_w',number('W',0,.1),'W','校准电阻样品侧电热设定，只有供电时随step注入','setpoint',component='sample_ampoule')
slot('clock.time_s',number('s',0),'s','唯一宿主时钟')
slot('acquisition.phase',PHASE,'1','异步采样状态')
slot('acquisition.elapsed_s',number('s',0),'s','本次真实采样时长')
slot('acquisition.points',{'type':'integer','minimum':0,'maximum':200001,'x-unit':'1'},'1','实际内部采样数')
for k,u in [('truth.bath_k','K'),('truth.sample_k','K'),('truth.reference_k','K'),('truth.sensor_w','W'),('truth.unreacted_mol','mol'),('truth.converted_mol','mol'),('truth.sample_mass_kg','kg')]:slot(k,nullable(number(u)),u,'真实过程状态；仅宿主evaluator/debug用于物理和守恒','process_truth','evaluator')

class TaTamivRuntime:
    def __init__(self,instance_id='tamiv_01'):
        self.instance_id=instance_id;self.plant=None;self.sample=None;self.reference=None;self.record=None;self.power=False;self.thermal_enabled=False;self.target=298.15;self.lift=.48;self.target_lift=.48;self._lift_start=.48;self._lift_start_ns=0;self._lift_arrival_ns=0;self.cal_power=0.;self.calibration=copy.deepcopy(CALIBRATION_EXAMPLE);self.method=copy.deepcopy(METHOD_EXAMPLE);self.x=np.zeros(5);self.time_ns=0;self._clock_remainder_ns=0.;self.revision=0;self.phase='idle';self.elapsed=0.;self.rows=[];self.recorded_heat=0.;self.error=None;self.operation_id=None;self._counter=0;self._start_ns=0;self._next_sample=0;self.records={};self._principals={};self._requests={};self._owners={};self._cancel_operations={};self._matrix_cache={};self._source=None;self._reference_id=None;self._evidence='no_sample';self._operation_source=None;self._operation_evidence='no_measurement'
        self.mass_loaded=self.mass_removed=self.ref_mass_loaded=self.ref_mass_removed=self.amount_loaded=self.amount_removed=self.converted_removed=self.controller_input=self.electrical_input=self.reaction_heat=self.ambient_loss=self.loaded_sensible=self.removed_sensible=0.
    @property
    def time_s(self):return self.time_ns/1e9
    def register_principal(self,principal_id,visibility='agent'):
        if not isinstance(principal_id,str) or not principal_id or principal_id in self._principals or visibility not in ('agent','debug','evaluator'):raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id):
        if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return TaTamivSession(self,principal_id)
    def describe(self):return export_ta_tamiv_contract_metadata()
    def _moving(self):return self.time_ns<self._lift_arrival_ns
    def _capacities(self):return np.array([self.plant['bath_capacity_j_k'],self.plant['cell_capacity_j_k']+(self.sample['added_heat_capacity_j_k'] if self.sample else 0.),self.plant['cell_capacity_j_k']+(self.reference['added_heat_capacity_j_k'] if self.reference else 0.)])
    def _mass(self):
        if not self.sample:return 0.
        s=self.sample;converted=s['converted'];solid=(s['amount_mol']-converted)*s['molar_mass'];products=sum(p['stoichiometry']*converted*p['molar_mass_kg_mol'] for p in s['products'])
        return s['carrier_water_kg']+solid+products
    def _temperature(self,index):return self.plant['ambient_k']+float(self.x[index]) if self.plant else None
    def _observed(self):
        if not self.power or not self.plant:return None
        if self.sample and self.sample['triggered'] and abs(self._temperature(1)-self.sample['reference_k'])>.1:return None
        p=self.plant;c=self.calibration;value=(p['sensor_gain']*self.x[3]+p['baseline_offset_w']+p['baseline_drift_w_s']*self.time_s-c['baseline_intercept_w']-c['baseline_drift_w_s']*self.time_s)*c['gain_correction']
        if abs(value)>p['heatflow_limit_w']:return None
        return float(round(value/p['heatflow_resolution_w'])*p['heatflow_resolution_w'])
    def read(self,role='evaluator'):
        if role not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
        d={'plant.configured':self.plant is not None,'sample.present':self.sample is not None,'reference.present':self.reference is not None,'sample.triggered':bool(self.sample and self.sample['triggered']),'power.enabled':self.power,'thermal.enabled':self.thermal_enabled,'sample.moving':self._moving(),'sample.lift_m':self.lift,'sample.target_lift_m':self.target_lift,'power.angle_rad':.28 if self.power else 0.,'bath.target_k':self.target,'bath.indicated_k':round(self._temperature(0),6) if self.power and self.plant else None,'calorimeter.indicated_w':self._observed(),'calibration.power_w':self.cal_power,'clock.time_s':self.time_s,'acquisition.phase':self.phase,'acquisition.elapsed_s':self.elapsed,'acquisition.points':len(self.rows),'truth.bath_k':self._temperature(0),'truth.sample_k':self._temperature(1),'truth.reference_k':self._temperature(2),'truth.sensor_w':float(self.x[3]),'truth.unreacted_mol':self.sample['amount_mol']-self.sample['converted'] if self.sample else None,'truth.converted_mol':self.sample['converted'] if self.sample else None,'truth.sample_mass_kg':self._mass() if self.sample else None}
        return {k:v for k,v in d.items() if role!='agent' or SLOTS[k]['visibility']=='agent'}
    def _propagate(self,ns):
        if not self.plant or ns==0:return
        p=self.plant;C=self._capacities();inserted=self.lift<=1e-12 and self.target_lift==0.;Gs=p['sample_conductance_w_k'] if inserted else 0.;GaS=0. if inserted else p['retracted_conductance_w_k'];Gr=p['reference_conductance_w_k'];Ga=p['ambient_conductance_w_k'];K=p['controller_conductance_w_k'] if self.power and self.thermal_enabled else 0.;k=self.sample['first_order_rate_s_inv'] if self.sample and self.sample['triggered'] else 0.;enthalpy=self.sample['enthalpy'] if self.sample else 0.;P=self.cal_power if self.power else 0.;target_delta=self.target-p['ambient_k'];dt=ns/1e9
        key=(tuple(C),Gs,GaS,Gr,Ga,K,k,enthalpy,P,target_delta,p['sensor_tau_s'],ns)
        E=self._matrix_cache.get(key)
        if E is None:
            A=np.zeros((11,11));A[0,0]=-(Gs+Gr+Ga+K)/C[0];A[0,1]=Gs/C[0];A[0,2]=Gr/C[0];A[0,5]=((K+Ga)*target_delta if K else 0.)/C[0]
            A[1,0]=Gs/C[1];A[1,1]=-(Gs+GaS)/C[1];A[1,4]=-enthalpy*k/C[1];A[1,5]=P/C[1]
            A[2,0]=Gr/C[2];A[2,2]=-Gr/C[2]
            A[3,0]=(-Gs+Gr)/p['sensor_tau_s'];A[3,1]=Gs/p['sensor_tau_s'];A[3,2]=-Gr/p['sensor_tau_s'];A[3,3]=-1/p['sensor_tau_s'];A[4,4]=-k;A[6:11,:5]=np.eye(5)
            E=expm(A*dt)
            if len(self._matrix_cache)>256:self._matrix_cache.clear()
            self._matrix_cache[key]=E
        before_n=float(self.x[4]);initial=self.x.copy();y=E@np.r_[initial,1.,np.zeros(5)];control_energy=None
        # A maximum-principle envelope decides whether bounded-power integration
        # is needed. False positives change only the numerical solver, not physics.
        thermal_excursion=abs(enthalpy)*before_n/min(C)+P*dt/min(C)
        low=min(float(np.min(initial[:3])),0.,target_delta)-thermal_excursion
        high=max(float(np.max(initial[:3])),0.,target_delta)+thermal_excursion
        if K and ((K+Ga)*target_delta-K*low>p['controller_heating_limit_w'] or (K+Ga)*target_delta-K*high < -p['controller_cooling_limit_w']):
            def rhs(_t,u):
                b,ss,rr,z,n=u[:5];raw=(K+Ga)*target_delta-K*b;pc=min(p['controller_heating_limit_w'],max(-p['controller_cooling_limit_w'],raw))
                dx=np.array([(Gs*(ss-b)+Gr*(rr-b)-Ga*b+pc)/C[0],(-Gs*(ss-b)-GaS*ss+P-enthalpy*k*n)/C[1],Gr*(b-rr)/C[2],(Gs*(ss-b)-Gr*(rr-b)-z)/p['sensor_tau_s'],-k*n])
                return np.r_[dx,u[:5],pc]
            solution=solve_ivp(rhs,(0.,dt),np.r_[initial,np.zeros(6)],method='Radau',rtol=1e-10,atol=np.array([1e-12,1e-12,1e-12,1e-15,1e-20,1e-11,1e-11,1e-11,1e-14,1e-19,1e-10]))
            if not solution.success:raise RuntimeError('THERMAL_INTEGRATION_FAILED')
            final=solution.y[:,-1];self.x=final[:5];integral=final[5:10];control_energy=float(final[10])
        else:self.x=y[:5];integral=y[6:11]
        converted=before_n-float(self.x[4])
        if self.sample:self.sample['converted']+=converted
        self.reaction_heat-=enthalpy*converted;self.electrical_input+=P*dt;self.controller_input+=control_energy if control_energy is not None else ((K+Ga)*target_delta*dt-K*integral[0]) if K else 0.;self.ambient_loss+=Ga*integral[0]+GaS*integral[1]
    def _ledger(self):
        stored=float(self._capacities()@self.x[:3]) if self.plant else 0.;remaining=self.sample['amount_mol']-self.sample['converted'] if self.sample else 0.;converted=self.sample['converted'] if self.sample else 0.;refmass=self.reference['mass_kg'] if self.reference else 0.
        return dict(sample_mass_loaded_kg=self.mass_loaded,sample_mass_present_kg=self._mass(),sample_mass_removed_kg=self.mass_removed,sample_mass_error_kg=self.mass_loaded-self._mass()-self.mass_removed,reference_mass_loaded_kg=self.ref_mass_loaded,reference_mass_present_kg=refmass,reference_mass_removed_kg=self.ref_mass_removed,reference_mass_error_kg=self.ref_mass_loaded-refmass-self.ref_mass_removed,reactant_loaded_mol=self.amount_loaded,reactant_remaining_mol=remaining,reactant_removed_mol=self.amount_removed,converted_present_mol=converted,converted_removed_mol=self.converted_removed,reaction_amount_error_mol=self.amount_loaded-remaining-self.amount_removed-converted-self.converted_removed,controller_input_j=self.controller_input,electrical_input_j=self.electrical_input,reaction_heat_j=self.reaction_heat,ambient_loss_j=self.ambient_loss,loaded_sensible_j=self.loaded_sensible,removed_sensible_j=self.removed_sensible,thermal_stored_j=stored,energy_error_j=self.controller_input+self.electrical_input+self.reaction_heat+self.loaded_sensible-self.ambient_loss-self.removed_sensible-stored)
    def species_ledger(self):
        if not self.sample:return {}
        return dict(solid_mol=self.sample['amount_mol']-self.sample['converted'],carrier_water_kg=self.sample['carrier_water_kg'],products={p['id']:dict(amount_mol=p['stoichiometry']*self.sample['converted'],mass_kg=p['stoichiometry']*self.sample['converted']*p['molar_mass_kg_mol']) for p in self.sample['products']})
    def _result(self):return dict(operation_id=self.operation_id,phase=self.phase,elapsed_s=self.elapsed,rows=copy.deepcopy(self.rows),recorded_heat_j=self.recorded_heat,template_source=self._operation_source,evidence=self._operation_evidence,error_code=self.error)
    def _finish(self,phase,error=None):
        self.phase=phase;self.error=error
        if self.operation_id:self.records[self.operation_id]=self._result()
    def _record(self):
        t=(self.time_ns-self._start_ns)/1e9;value=self._observed()
        if value is None:self.recorded_heat=None
        if self.rows:
            last=self.rows[-1]
            if value is None or last['heat_flow_w'] is None or self.recorded_heat is None:self.recorded_heat=None
            else:self.recorded_heat+=(value+last['heat_flow_w'])/2*(t-last['time_s'])
        self.rows.append(dict(time_s=t,host_time_s=self.time_s,heat_flow_w=value,cumulative_heat_j=self.recorded_heat,bath_temperature_k=round(self._temperature(0),6) if self.power else None));self._next_sample+=1
        if self.sample and self.sample['triggered'] and abs(self._temperature(1)-self.sample['reference_k'])>.1:self._finish('failed','REFERENCE_TEMPERATURE_RANGE')
    def invoke(self,capability,**a):
        if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability]['input_schema'],a)
        if capability in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
        if capability=='analysis.baseline':return baseline_fit(**a)
        if capability=='analysis.kinetics':return fit_kinetics(**a)
        if capability=='analysis.electrical_calibration':
            delta=a['plateau_observed_w']-a['baseline_observed_w']
            if delta<=0:raise ActionRejected('ANALYSIS_DATA')
            return dict(gain_correction=a['known_power_w']/delta,evidence='known_electrical_power_divided_by_observed_plateau_minus_blank')
        if capability=='acquisition.result':return self._result()
        if capability=='provenance.read':return dict(reference_id=self._reference_id,template_source=self._source,evidence=self._evidence)
        if capability=='ledger.read':return self._ledger()
        if capability=='sim.step':self.step(a['dt_s']);return {'time_s':self.time_s}
        if self.phase=='running' and capability not in ('acquisition.stop','sample.trigger','calibration.set_power','power.set'):raise ActionRejected('BUSY')
        if capability=='plant.configure':
            if self.plant or not a['accept_engineering']:raise ActionRejected('PRECONDITION_FAILED')
            self.plant=copy.deepcopy(a);self.target=a['ambient_k'];out={'configured':True}
        elif capability=='sample.load':
            if not self.plant or self.sample or self._moving() or self.lift<.46 or not a['accept_ideal_solution_and_kinetics']:raise ActionRejected('PRECONDITION_FAILED')
            record=library().get(a['reference_id'])
            if record is None:raise ActionRejected('UNKNOWN_REFERENCE')
            enthalpy=record['enthalpy_J_mol'];molar_mass=record['molar_mass_kg_mol'];products=record['products'];temperature=record['reference_temperature_K']
            if not math.isfinite(enthalpy) or molar_mass<=0 or not products or abs(sum(p['stoichiometry']*p['molar_mass_kg_mol'] for p in products)-molar_mass)>molar_mass*1e-6:raise ActionRejected('REFERENCE_MASS_BALANCE')
            if abs(a['initial_temperature_k']-temperature)>.1:raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
            C=self._capacities()[1];added=a['added_heat_capacity_j_k'];delta=a['initial_temperature_k']-self.plant['ambient_k'];self.x[1]=(C*self.x[1]+added*delta)/(C+added);self.loaded_sensible+=added*delta
            self.sample=copy.deepcopy(a);self.sample.update(enthalpy=enthalpy,molar_mass=molar_mass,products=copy.deepcopy(products),reference_k=temperature,logK=record['dissolution_log10_K'],triggered=False,converted=0.);self.record=copy.deepcopy(record);self.mass_loaded+=self._mass();self.amount_loaded+=a['amount_mol'];self.x[4]=0.;self._reference_id=a['reference_id'];source=record.get('source');self._source=source if isinstance(source,str) else json.dumps(source,ensure_ascii=False,sort_keys=True);self._evidence='compiled_real_dissolution_enthalpy; ideal_solution_and_first_order_kinetics_explicit_assumptions';out={'loaded':True}
        elif capability=='sample.remove':
            if not self.sample or self._moving() or self.lift<.46 or self.cal_power>0:raise ActionRejected('PRECONDITION_FAILED')
            self.mass_removed+=self._mass();self.amount_removed+=self.sample['amount_mol']-self.sample['converted'];self.converted_removed+=self.sample['converted'];self.removed_sensible+=self.sample['added_heat_capacity_j_k']*self.x[1];self.sample=self.record=None;self.x[4]=0.;self._source=self._reference_id=None;self._evidence='no_sample';out={'loaded':False}
        elif capability=='reference.load':
            if not self.plant or self.reference or not a['accept_inert_reference']:raise ActionRejected('PRECONDITION_FAILED')
            C=self._capacities()[2];added=a['added_heat_capacity_j_k'];delta=a['initial_temperature_k']-self.plant['ambient_k'];self.x[2]=(C*self.x[2]+added*delta)/(C+added);self.loaded_sensible+=added*delta;self.reference=copy.deepcopy(a);self.ref_mass_loaded+=a['mass_kg'];out={'loaded':True}
        elif capability=='reference.remove':
            if not self.reference or self.power:raise ActionRejected('PRECONDITION_FAILED')
            self.ref_mass_removed+=self.reference['mass_kg'];self.removed_sensible+=self.reference['added_heat_capacity_j_k']*self.x[2];self.reference=None;out={'loaded':False}
        elif capability=='sample.set_lift':
            if not self.plant or self.cal_power>0:raise ActionRejected('PRECONDITION_FAILED')
            self.target_lift=a['lift_m'];self._lift_start=self.lift;self._lift_start_ns=self.time_ns;self._lift_arrival_ns=self.time_ns+round(abs(self.lift-self.target_lift)/self.plant['lift_speed_m_s']*1e9);out={'target_lift_m':self.target_lift}
        elif capability=='sample.trigger':
            if not self.sample or self.sample['triggered'] or self._moving() or self.lift>1e-12:raise ActionRejected('PRECONDITION_FAILED')
            if abs(self._temperature(1)-self.sample['reference_k'])>.1:raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
            ionic=[p for p in self.sample['products'] if p.get('phase')!='liquid_water'];nu=sum(p['stoichiometry'] for p in ionic)
            if nu<=0:raise ActionRejected('REFERENCE_DATA')
            log_extent=math.log(self.sample['carrier_water_kg'])+(math.log(10)*self.sample['logK']-sum(p['stoichiometry']*math.log(p['stoichiometry']) for p in ionic))/nu;limit=math.exp(min(log_extent,700));self.x[4]=min(self.sample['amount_mol'],limit);self.sample['triggered']=True;self.sample['convertible_mol']=float(self.x[4]);out={'triggered':True}
        elif capability=='power.set':
            if not a['enabled'] and self.phase=='running':self._finish('cancelled','POWER_OFF')
            self.power=a['enabled']
            if not self.power:self.thermal_enabled=False;self.cal_power=0.
            out={'enabled':self.power}
        elif capability=='thermal.set_temperature':
            if not self.power or not self.plant:raise ActionRejected('PRECONDITION_FAILED')
            self.target=a['temperature_k'];self.thermal_enabled=True;out={'target_k':self.target}
        elif capability=='thermal.disable':self.thermal_enabled=False;out={'enabled':False}
        elif capability=='calibration.set_power':
            if not self.power or not self.sample or self._moving() or self.lift>1e-12:raise ActionRejected('PRECONDITION_FAILED')
            self.cal_power=a['power_w'];out={'power_w':self.cal_power}
        elif capability=='sensor.calibrate':self.calibration=copy.deepcopy(a);out={'configured':True}
        elif capability=='method.configure':
            points=a['duration_s']/a['sample_interval_s']
            if points<2 or points>200000 or abs(points-round(points))>1e-8 or any(abs(a[k]*1e9-round(a[k]*1e9))>1e-4 for k in ('duration_s','sample_interval_s')):raise ActionRejected('SAMPLE_GRID')
            self.method=copy.deepcopy(a);out={'configured':True}
        elif capability=='acquisition.start':
            if not self.power or not self.plant or not self.sample or not self.reference or self._moving() or self.lift>1e-12:raise ActionRejected('PRECONDITION_FAILED')
            if self.sample['triggered'] and abs(self._temperature(1)-self.sample['reference_k'])>.1:raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
            if max(abs(self._temperature(i)-self.target) for i in range(3))>self.method['equilibrium_tolerance_k']:raise ActionRejected('NOT_EQUILIBRATED')
            self._operation_source=self._source;self._operation_evidence=self._evidence;self._counter+=1;self.operation_id=f'{self.instance_id}:acquisition:{self._counter}';self._start_ns=self.time_ns;self._next_sample=0;self.phase='running';self.elapsed=0.;self.rows=[];self.recorded_heat=0.;self.error=None;out={'operation_id':self.operation_id}
        elif capability=='acquisition.stop':
            if self.phase!='running':raise ActionRejected('NOT_RUNNING')
            self._finish('cancelled','USER_CANCEL');out={'phase':self.phase,'elapsed_s':self.elapsed}
        else:raise ActionRejected('UNKNOWN_CAPABILITY')
        self.revision+=1;return copy.deepcopy(out)
    def step(self,dt_s):
        validate(number('s',0,86400),dt_s);total=math.fsum((self._clock_remainder_ns,dt_s*1e9));ns=round(total);self._clock_remainder_ns=total-ns
        if ns==0:return
        end=self.time_ns+ns
        while self.time_ns<end:
            if self.phase=='running' and self.time_ns==self._start_ns+self._next_sample*round(self.method['sample_interval_s']*1e9):self._record()
            stop=self._start_ns+round(self.method['duration_s']*1e9)
            if self.phase=='running' and self.time_ns==stop:self.elapsed=self.method['duration_s'];self._finish('succeeded' if self.recorded_heat is not None else 'failed',None if self.recorded_heat is not None else 'SENSOR_RANGE')
            boundary=end
            if self._moving():boundary=min(boundary,self._lift_arrival_ns)
            if self.phase=='running':boundary=min(boundary,self._start_ns+self._next_sample*round(self.method['sample_interval_s']*1e9),stop)
            self._propagate(boundary-self.time_ns);self.time_ns=boundary
            if self.plant:
                distance=self.plant['lift_speed_m_s']*(self.time_ns-self._lift_start_ns)/1e9;self.lift=self.target_lift if self.time_ns>=self._lift_arrival_ns else self._lift_start+math.copysign(distance,self.target_lift-self._lift_start)
            if self.phase=='running':self.elapsed=(self.time_ns-self._start_ns)/1e9
        if self.phase=='running' and self.time_ns==self._start_ns+self._next_sample*round(self.method['sample_interval_s']*1e9):self._record()
        if self.phase=='running' and self.time_ns==self._start_ns+round(self.method['duration_s']*1e9):self._finish('succeeded' if self.recorded_heat is not None else 'failed',None if self.recorded_heat is not None else 'SENSOR_RANGE')
        self.revision+=1

class TaTamivSession:
    def __init__(self, host, principal_id): self._host = host; self._principal = principal_id
    @property
    def _role(self): return self._host._principals[self._principal]
    def _target(self, instance_id):
        if instance_id != self._host.instance_id: raise ActionRejected('UNKNOWN_INSTANCE')
    def describe(self, *, instance_id=None, asset_type_id=None):
        if (instance_id is None) == (asset_type_id is None): raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None: self._target(instance_id)
        if asset_type_id is not None and asset_type_id != ASSET_TYPE_ID: raise ActionRejected('UNKNOWN_INSTANCE')
        meta = export_ta_tamiv_contract_metadata()
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
            result['evidence'] = 'observed_differential_heat_flow_with_explicit_kinetics; selected template details require evaluator provenance.read' if result['operation_id'] is not None else 'no_measurement'
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
 'plant.configure':['plant.configured','bath.target_k','truth.bath_k','truth.sample_k','truth.reference_k'],
 'sample.load':['sample.present','sample.triggered','truth.sample_k','truth.unreacted_mol','truth.converted_mol','truth.sample_mass_kg'],
 'sample.remove':['sample.present','sample.triggered','truth.unreacted_mol','truth.converted_mol','truth.sample_mass_kg'],
 'reference.load':['reference.present','truth.reference_k'],'reference.remove':['reference.present'],
 'sample.set_lift':['sample.target_lift_m','sample.moving'],'sample.trigger':['sample.triggered'],
 'power.set':['power.enabled','power.angle_rad','thermal.enabled','calibration.power_w','bath.indicated_k','calorimeter.indicated_w','acquisition.phase'],
 'thermal.set_temperature':['bath.target_k','thermal.enabled'],'thermal.disable':['thermal.enabled'],
 'calibration.set_power':['calibration.power_w'],'sensor.calibrate':['calorimeter.indicated_w'],
 'acquisition.start':['acquisition.phase','acquisition.elapsed_s','acquisition.points'],'acquisition.stop':['acquisition.phase'],
 'sim.step':list(SLOTS)}
for k,v in EFFECTS.items():CAPS[k]['effects']=v
ERRORS=sorted(set(__import__('re').findall(r"ActionRejected\('([A-Z_0-9]+)'",Path(__file__).read_text()))|{'POWER_OFF','USER_CANCEL','SENSOR_RANGE'})

def export_ta_tamiv_contract_metadata():
    slots={};caps={}
    for k,v in SLOTS.items():slots[k]=dict(meaning_zh=v['meaning'],component_ids=[v['component']],value_schema=copy.deepcopy(v['schema']),unit=v['unit'],reference_conditions='SI; static20mL twin-channel microcalorimeter, target277.15..423.15K; standard dissolution enthalpies at298.15K. Positive differential heat flow means sample heat released toward bath.',origin=v['origin'],visibility=v['visibility'],access='read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'TaTamivSession.read(state_ids=[{k!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description='One host nanosecond clock with fractional residual; internal acquisition samples have independent explicit timestamps. Reads never advance time.'),source_ids=['simulation_assumption'])
    limits=[
      '仅一个静态20mL样品/参比模块；外观不含独立数值显示屏，bath/heatflow是API观测。没有增加micro-solution混合或破碎硬件。sample.trigger只是受信宿主初始化反应事件，不是仪器自驱动操作。',
      '真实模板为USGS编纂的298.15K标准溶解反应焓和logK，不是TAM实测时间曲线。一阶速率/理想活度/纯水载体为显式假设；已触发过程继续用固定工程系数，但超参考温度±0.1K时公开热流不可用、采集失败，不把标准单温点参数冒充全温区真实物性；无Arrhenius反馈。',
      '厂家静态20mL噪声/漂移/2%精度仅文献指标，不当作已验证随机噪声分布；Micro Solution附件的漂移规格与本配置不同，未建模附件不能继承该指标。',
      '默认仅100微升水载体，addedC=.418J/K按0.0001kg×4180J/kg/K工程水比热近似；cell/bath热容、接触热导与传感τ均明确工程参数，快速演示不代表OEM平衡时间、空安瓿热质量或温度精度。',
      '样品与参比各是有限热容节点，浴为第三节点；上提/移动时样品与浴热导为0、改与外界交换，插入0位后切回。未求解随插入深度变化的分布热接触、流体对流或机械接触。',
      '反应trigger后即使停止采样、断电或样品上提仍继续反应/传热。sample.remove导出剩余固相、产品及载体和显热，终止的只是本资产对已移出物料的追踪；不宣称把化学反应回滚。',
      '同组成溶解产品质量来自模板化学式和化学计量，晶水计入产品质量但理想溶解上限采用初始载体水量；体积变化、活度系数、酸碱平衡和副反应未求解。',
      '电校准P真正进入样品热节点；停止记录不自动切断校准电功率，需calibration.set_power(0)或断电。显式基线/漂移校正只写系数，来源应为实际空白和电热平台观测。',
      '记录累计热量是已采集校正热流的梯形积分；并不等于即时真实反应热，遗漏残余传热或停止采样后继续反应时二者必然不同。缺测后累计值为null，不把缺失区段当成0。',
      '外部kinetics分析使用固定浴与已知单节点热τ/传感τ的双一阶卷积近似，仅拟合外部观测并明确假设；不使用隐藏反应速率或标准焓直接冒充拟合值。',
      '浴控制为显式工程K+热/冷功率上限的双向调温代理，默认K5W/K、热冷上限各800W均是假设而非OEM加热额定值；整机1400W电气值不冒充部件功率。线性域矩阵解析，可能饱和时用Radau解含clip的同一守恒方程。4至150°C是目标范围，不证明真实升温时间或长期稳定度。',
      '所有session公开acquisition.result/poll统一隐藏选中记录，即使bridge内部用evaluator代理；完整来源只走evaluator provenance.read。共享Python/可读文件不提供OS级安全隔离。',
      '真实接触、机器人、硬件驱动、混合触发机械、实测基线/噪声/校准实验和厂家性能认证not_run。']
    for k,v in CAPS.items():
        un=k in UNSUPPORTED;async_=k=='acquisition.start';caps[k]={key:copy.deepcopy(v[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']}
        caps[k].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ('scene','robot','hardware') else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.ta_tamiv.TaTamivSession.invoke',invocation=f'session.invoke(instance_id="tamiv_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),preconditions=[] if un or v['kind'] in ('query','analysis') else [dict(description_zh='配置、安瓿/参比、插入姿态、化学计量与参考温度、采样网格由运行入口原子检查。',state_ids=['power.enabled','sample.present','reference.present','sample.moving','sample.lift_m','acquisition.phase'],enforced_by='runtime',check='TaTamivRuntime.invoke checks applicable guards before committing state')],state_effects=[dict(state_id=s,timing='immediate',description_zh='命令实际即时变化') for s in v['effects']],observes=[],model_effects=dict(mode='none',description_zh='这里只输出可映射状态；样品杆与电源开关实际场景求值由独立验证登记。',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='实际内部采样窗口结束，返回冻结观测与梯形积分' if async_ else '原子命令提交；热过程、反应与运动仅由宿主step推进',completion_state_ids=['acquisition.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','sealed_sample','reference','calorimeter_sensor'],partial_failure_zh='取消或断电停止采样且保留已采集曲线；密封反应与残余热继续，断电同时切断电校准/主动温控；显热、产品、剩余化学库存均不回滚。'),errors=[dict(code=e,meaning_zh=e,side_effects='partial' if async_ and e in ('POWER_OFF','USER_CANCEL','SENSOR_RANGE') else 'none') for e in ERRORS],verification=[],limitations=limits,source_ids=['manufacturer_primary','simulation_assumption'])
        if k in ('sample.trigger','sample.set_lift','thermal.set_temperature','thermal.disable','calibration.set_power','power.set','acquisition.start'):
            caps[k]['state_effects'] += [dict(state_id=s,timing='on_step',description_zh='宿主时钟推进三节点热、传感器、反应或内部采样') for s in SLOTS if s not in v['effects'] and s not in ('plant.configured','sample.present','reference.present','power.enabled')]
    mechanism=dict(component_ids=['bath','sample_ampoule','reference_ampoule','calorimeter_sensor','sample_lift'],state_ids=list(slots),meaning_zh='有限热容三节点、差分热流传感、一阶溶解和守恒账本的联立解析传播',implementation='lumped',equations=[
      'Cb*dTb/dt=Gs*(Ts-Tb)+Gr*(Tr-Tb)-Ga*(Tb-Ta)+Pcontrol; Cs*dTs/dt=-Gs*(Ts-Tb)-Gair*(Ts-Ta)+Pcal-DeltaH*k*n; Cr*dTr/dt=-Gr*(Tr-Tb).',
      'Pcontrol=clip(K*(Ttarget-Tb)+Ga*(Ttarget-Ta),-engineering_cooling_limit,+engineering_heating_limit) if enabled. Pcal applied only with power; neither limit is an OEM heater rating.',
      'Inserted atactual lift0 with target0:Gs=sample_conductance,Gair=0; moving/retracted:Gs=0,Gair=retracted_conductance. Motion arrival is an exact event boundary.',
      'Differential flux phi=Gs*(Ts-Tb)-Gr*(Tr-Tb); tau_sensor*dz/dt=phi-z. Raw observation=gain*z+offset+drift*t; explicit calibration subtracts fitted intercept/drift and multiplies gain correction, then engineering quantization/range checks.',
      'Ideal saturation extent limit = initial_water_kg*exp[(ln(10)*logK-sum(nu*ln(nu)))/sum(nu)] for aqueous products with a_water=1. Trigger sets convertible pool min(initial_solid,limit); dn/dt=-k*n, dxi/dt=k*n.',
      'Reaction source=-DeltaH*dxi/dt, so positive dissolution enthalpy absorbs heat. Solid=(n0-xi), each product=nu_i*xi; product molecular masses and crystal water preserve reactant mass.',
      'An augmented matrix exponential propagates the linear domain and time integrals. A conservative maximum-principle temperature envelope selects Radau integration of the same coupled equations with clipped controller power when saturation is possible; integrated control/ambient/reaction terms balance stored energy.',
      'Loading adds sensible energy C_added*(T_load-Ta) and merges it with the cell node by capacity weighting; unloading exports C_added*(T_cell-Ta). No hidden bath reset or thermal energy creation.',
      'Acquisition start/stop change recording only; first-order sample reaction continues outside recording and without electrical power. Trapezoid observation integral is distinct from true integrated reaction heat.',
      'External kinetics inference fits known double first-order heat/sensor convolution with rate + amplitude + baseline/drift from supplied observations; no private source/k fields consulted.'],parameters={},source_ids=['simulation_assumption','template_library'],limitations=limits)
    spec=importlib.util.spec_from_file_location('_tam_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()});ann.annotate_export(SLUG,caps,slots,{'differential_microcalorimetry':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'reference.load':(REFERENCE,REFERENCE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sensor.calibrate':(CALIBRATION,CALIBRATION_EXAMPLE)})
    # Keep local physical meanings; shared generic annotations must not invent a
    # first-order bath thermometer or a sample-bottle label for the thermal bath.
    for key,value in SLOTS.items():slots[key]['meaning_zh']=value['meaning']
    slots['bath.target_k']['meaning_zh']='浴温目标277.15至423.15K，由plant.configure初始化、thermal.set_temperature修改；命令不直接改变实际浴温。'
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'differential_microcalorimetry':mechanism})

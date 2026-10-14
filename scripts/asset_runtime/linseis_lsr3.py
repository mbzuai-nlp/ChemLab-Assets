"""CHEM139 LSR-3 IR800: conserved finite thermal nodes and actual polarity samples.

Published transport curves drive voltage/current observations; inverse results use
those observations, never copied reference values. Heat capacities and controller
parameters are explicit assumptions, not OEM power or performance certification.
"""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
import numpy as np
from scipy.integrate import solve_ivp
PROTOCOL_VERSION='1.0.0';SLUG='linseis_lsr3';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-139'
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch04/t1_thermal'/SLUG;LIBRARY_PATH=ASSET/'thermoelectric_library.json'

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

def library():return {x['id']:x for x in json.loads(LIBRARY_PATH.read_text())['templates']}
def curve_xy(record,key):
    value={'seebeck':'value_V_K','resistivity':'value_ohm_m','thermal_conductivity':'value_W_m_K'}[key]
    rows=record['curves'][key];return np.array([x['temperature_K'] for x in rows]),np.array([x[value] for x in rows])
def transport(record,hot,cold,clamp=False):
    st,sv=curve_xy(record,'seebeck');rt,rv=curve_xy(record,'resistivity');lo=max(st[0],rt[0]);hi=min(st[-1],rt[-1]);valid=lo<=min(hot,cold) and max(hot,cold)<=hi
    if not valid and not clamp:return None
    # Integral of the piecewise-linear signed Seebeck curve between probe nodes.
    a=min(hot,cold);b=max(hot,cold);points=np.r_[a,st[(st>a)&(st<b)],b];values=np.interp(points,st,sv);emf=float(np.trapezoid(values,points))*(1 if hot>=cold else -1);mean=(hot+cold)/2;rho=float(np.interp(mean,rt,rv));return dict(seebeck_v_k=emf/(hot-cold) if hot!=cold else float(np.interp(mean,st,sv)),resistivity_ohm_m=rho,emf_v=emf,valid=valid,domain_k=[float(lo),float(hi)])

def infer_transport(zero_voltage_v,positive_voltage_v,negative_voltage_v,positive_current_a,negative_current_a,hot_temperature_k,cold_temperature_k,width_m,depth_m,probe_spacing_m,geometry_factor,contact_resistance_correction_ohm):
    delta=hot_temperature_k-cold_temperature_k
    if abs(delta)<1e-6 or positive_current_a<=0 or negative_current_a>=0:raise ActionRejected('ANALYSIS_DATA')
    resistance=(positive_voltage_v-negative_voltage_v)/(positive_current_a-negative_current_a)-contact_resistance_correction_ohm
    if resistance<=0:raise ActionRejected('ANALYSIS_DATA')
    rho=resistance*width_m*depth_m/(probe_spacing_m*geometry_factor);s=zero_voltage_v/delta
    return dict(seebeck_v_k=float(round(s/1e-8)*1e-8),conductivity_s_m=1/rho,resistivity_ohm_m=rho,four_probe_resistance_ohm=resistance,evidence='external_zero_current_and_actual_polarity_voltage_current_samples; frozen_gradient_during_pair_and_declared_geometry_contact_correction_assumptions')

PLANT_EXAMPLE=dict(ambient_k=298.15,furnace_capacity_j_k=100.,furnace_loss_w_k=.05,sample_furnace_conductance_w_k=1.,sample_span_conductance_w_k=.1,retracted_conductance_w_k=.02,controller_gain_w_k=200.,heater_limit_w=1000.,cooling_limit_w=1000.,gradient_heater_limit_w=30.,current_contact_resistance_ohm=.2,voltage_contact_leak_fraction=.001,current_compliance_v=10.,voltage_gain=1.,voltage_offset_v=0.,voltage_resolution_v=1e-9,temperature_resolution_k=1e-4,hot_sensor_bias_k=0.,cold_sensor_bias_k=0.,furnace_speed_m_s=.1,clamp_speed_m_s=.004,accept_engineering=True)
PLANT=obj({'ambient_k':number('K',288.15,303.15),'furnace_capacity_j_k':number('J/K',.01,1e7),'furnace_loss_w_k':number('W/K',0,1000),'sample_furnace_conductance_w_k':number('W/K',1e-6,100),'sample_span_conductance_w_k':number('W/K',0,100),'retracted_conductance_w_k':number('W/K',0,100),'controller_gain_w_k':number('W/K',.001,1e5),'heater_limit_w':number('W',.01,1e5),'cooling_limit_w':number('W',.01,1e5),'gradient_heater_limit_w':number('W',.001,1000),'current_contact_resistance_ohm':number('ohm',0,1e6),'voltage_contact_leak_fraction':number('1',0,1),'current_compliance_v':number('V',.001,1000),'voltage_gain':number('1',.1,10),'voltage_offset_v':number('V',-.1,.1),'voltage_resolution_v':number('V',1e-12,.01),'temperature_resolution_k':number('K',1e-6,1),'hot_sensor_bias_k':number('K',-20,20),'cold_sensor_bias_k':number('K',-20,20),'furnace_speed_m_s':number('m/s',.001,1),'clamp_speed_m_s':number('m/s',.0001,.1),'accept_engineering':BOOL})
GEOMETRY_EXAMPLE=dict(width_m=.003,depth_m=.003,length_m=.02,probe_spacing_m=.008)
GEOMETRY=obj({'width_m':number('m',.002,.005),'depth_m':number('m',.002,.005),'length_m':number('m',.004,.023),'probe_spacing_m':dict(type='number',enum=[.004,.006,.008],**{'x-unit':'m'})})
SAMPLE_EXAMPLE=dict(reference_id='tematdb_00009',density_kg_m3=7700.,specific_heat_capacity_j_kg_k=200.,initial_temperature_k=298.15,accept_engineering_density_heat_capacity=True,accept_directional_scalar_proxy=True)
SAMPLE=obj({'reference_id':TEXT,'density_kg_m3':number('kg/m^3',1,30000),'specific_heat_capacity_j_kg_k':number('J/kg/K',1,10000),'initial_temperature_k':number('K',288.15,1073.15),'accept_engineering_density_heat_capacity':BOOL,'accept_directional_scalar_proxy':BOOL})
METHOD_EXAMPLE=dict(duration_s=3.,phase_interval_s=.05,current_a=.01,minimum_delta_k=.1,equilibrium_tolerance_k=.05,geometry_factor=1.,contact_resistance_correction_ohm=.0002)
METHOD=obj({'duration_s':number('s',.003,86400),'phase_interval_s':number('s',.001,60),'current_a':number('A',1e-9,.16),'minimum_delta_k':number('K',.001,20),'equilibrium_tolerance_k':number('K',.0001,20),'geometry_factor':number('1',.1,10),'contact_resistance_correction_ohm':number('ohm',0,1e6)})
CALIBRATION_EXAMPLE=dict(voltage_gain_correction=1.,voltage_offset_v=0.,hot_offset_k=0.,cold_offset_k=0.)
CALIBRATION=obj({'voltage_gain_correction':number('1',.1,10),'voltage_offset_v':number('V',-.1,.1),'hot_offset_k':number('K',-20,20),'cold_offset_k':number('K',-20,20)})
SEGMENT=obj({'temperature_k':number('K',288.15,1073.15),'ramp_rate_k_min':number('K/min',.01,100),'hold_s':number('s',0,86400)})
PROGRAM_EXAMPLE={'segments':[dict(temperature_k=306.15,ramp_rate_k_min=100.,hold_s=.3),dict(temperature_k=305.,ramp_rate_k_min=100.,hold_s=.3)]}
ANALYSIS_INPUT=obj({'zero_voltage_v':number('V'),'positive_voltage_v':number('V'),'negative_voltage_v':number('V'),'positive_current_a':number('A',1e-12,.16),'negative_current_a':number('A',-.16,-1e-12),'hot_temperature_k':number('K'),'cold_temperature_k':number('K'),'width_m':number('m',.002,.005),'depth_m':number('m',.002,.005),'probe_spacing_m':number('m',.004,.008),'geometry_factor':number('1',.1,10),'contact_resistance_correction_ohm':number('ohm',0,1e6)})
ANALYSIS_EXAMPLE=dict(zero_voltage_v=2e-4,positive_voltage_v=3e-4,negative_voltage_v=1e-4,positive_current_a=.01,negative_current_a=-.01,hot_temperature_k=305.5,cold_temperature_k=304.5,width_m=.003,depth_m=.003,probe_spacing_m=.008,geometry_factor=1.,contact_resistance_correction_ohm=0.)
INFERENCE=obj({'seebeck_v_k':number('V/K'),'conductivity_s_m':number('S/m',0),'resistivity_ohm_m':number('ohm*m',0),'four_probe_resistance_ohm':number('ohm',0),'evidence':TEXT})
ROW=obj({'time_s':number('s',0),'host_time_s':number('s',0),'polarity':enum(['zero','positive','negative']),'current_a':nullable(number('A')),'voltage_v':nullable(number('V')),'hot_temperature_k':nullable(number('K')),'cold_temperature_k':nullable(number('K')),'mean_temperature_k':nullable(number('K')),'delta_temperature_k':nullable(number('K'))})
POINT=obj({'time_s':number('s',0),'temperature_k':number('K'),'delta_temperature_k':number('K'),'seebeck_v_k':nullable(number('V/K')),'conductivity_s_m':nullable(number('S/m')),'resistivity_ohm_m':nullable(number('ohm*m')),'four_probe_resistance_ohm':nullable(number('ohm')),'quality':enum(['estimated','unavailable']),'error_code':nullable(TEXT)})
RESULT=obj({'operation_id':nullable(TEXT),'phase':PHASE,'elapsed_s':number('s',0),'rows':array(ROW),'transport_points':array(POINT),'template_source':nullable(TEXT),'evidence':TEXT,'error_code':nullable(TEXT)})
CAPS={};SLOTS={}
def cap(k,i,o,c,z,kind='device_command',visibility='agent',example=None):CAPS[k]=dict(input_schema=i,output_schema=o,component_ids=c,function_zh=z,kind=kind,visibility=visibility,example_arguments=copy.deepcopy(example or {}),effects=[])
def slot(k,s,u,z,origin='derived',visibility='agent',component='controller'):SLOTS[k]=dict(schema=s,unit=u,meaning=z,origin=origin,visibility=visibility,component=component,write=None)
cap('plant.configure',PLANT,obj({'configured':BOOL}),['furnace','controller'],'宿主热容/热导/控制功率/接触与传感参数；全部未知量明确工程假设','configuration','evaluator',PLANT_EXAMPLE)
cap('geometry.configure',GEOMETRY,obj({'configured':BOOL}),['sample_rod','voltage_probe_hot','voltage_probe_cold'],'棒截面2至5mm、长≤23mm，探针间距4/6/8mm；装样后几何锁定','configuration','evaluator',GEOMETRY_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_rod'],'装入实验输运曲线对应棒样，密度和比热由宿主明确假设','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.remove',obj({}),obj({'loaded':BOOL}),['sample_rod'],'卸出整根样品及显热；热账与物料账不回滚','configuration','evaluator')
cap('furnace.set_open',obj({'opened':BOOL}),obj({'target_m':number('m',0,.18)}),['furnace'],'宿主水平退炉有限运动代理，开炉时样品只与环境换热','simulation','evaluator',{'opened':False})
cap('sample.set_clamp',obj({'closed':BOOL}),obj({'target_m':number('m',0,.008)}),['sample_electrode_cold'],'宿主上电极升降夹持代理，开放才允许装卸样','simulation','evaluator',{'closed':True})
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_switch'],'电源关闭后停电流/主动热源，剩余显热继续传导',example={'enabled':True})
cap('thermal.set_temperature',obj({'temperature_k':number('K',288.15,1073.15),'ramp_rate_k_min':number('K/min',.01,100)}),obj({'target_k':number('K'),'ramp_rate_k_min':number('K/min')}),['furnace'],'只写平均温度目标与限定斜坡，step推进实际炉体和双端温度',example={'temperature_k':305.,'ramp_rate_k_min':100.})
cap('thermal.set_gradient',obj({'delta_k':number('K',0,20)}),obj({'target_delta_k':number('K',0,20)}),['sample_electrode_hot'],'仅下电极副加热建立正温差，上电极反转选件未实现',example={'delta_k':1.})
cap('thermal.disable',obj({}),obj({'enabled':BOOL}),['furnace','sample_electrode_hot'],'停止主动炉温及梯度加热，温度继续被动变化')
cap('program.configure',obj({'segments':array(SEGMENT,1,30)}),obj({'configured':BOOL}),['controller'],'分段温度/斜坡/保温程序','configuration',example=PROGRAM_EXAMPLE)
cap('program.start',obj({}),obj({'running':BOOL}),['controller'],'按当前程序设定温度启动分段斜坡/保温；不自动启动电测')
cap('program.stop',obj({}),obj({'running':BOOL}),['controller'],'冻结当前温程设定，保持温控，已发生热变化不回滚')
cap('method.configure',METHOD,obj({'configured':BOOL}),['controller'],'配置0/+I/-I三相采样、电流≤160mA、几何及接触校正','configuration',example=METHOD_EXAMPLE)
cap('sensor.calibrate',CALIBRATION,obj({'configured':BOOL}),['voltage_probe_hot','voltage_probe_cold'],'应用外部标准给出的电压/温度校正；不读取隐藏偏差','configuration',example=CALIBRATION_EXAMPLE)
cap('acquisition.start',obj({}),obj({'operation_id':TEXT}),['controller','voltage_probe_hot','voltage_probe_cold'],'异步逐相采集实际电压、电流和端温，随后反演S/σ/ρ')
cap('acquisition.stop',obj({}),obj({'phase':PHASE,'elapsed_s':number('s',0)}),['controller'],'停止电流采集，保留已采样三相数据；炉温和显热继续')
cap('acquisition.result',obj({}),RESULT,['controller'],'公开实际三相数据与输运推断；任意会话角色均隐藏所选材料来源','query')
cap('analysis.transport',ANALYSIS_INPUT,INFERENCE,['controller'],'仅由外部零流/反转电压电流、温差及几何计算输运量','analysis',example=ANALYSIS_EXAMPLE)
cap('provenance.read',obj({}),obj({'reference_id':nullable(TEXT),'template_source':nullable(TEXT),'evidence':TEXT}),['controller'],'仅宿主读取当前样品实验文献来源','query','evaluator')
LEDGER_UNITS=dict(mass_loaded_kg='kg',mass_present_kg='kg',mass_removed_kg='kg',mass_error_kg='kg',volume_loaded_m3='m^3',volume_present_m3='m^3',volume_removed_m3='m^3',volume_error_m3='m^3',controller_input_j='J',gradient_input_j='J',joule_sample_j='J',joule_contact_export_j='J',peltier_input_j='J',electrical_source_input_j='J',ambient_loss_j='J',loaded_sensible_j='J',removed_sensible_j='J',thermal_stored_j='J',energy_error_j='J')
cap('ledger.read',obj({}),obj({k:number(u) for k,u in LEDGER_UNITS.items()}),['controller'],'整棒质量/体积与炉/样品/焦耳/帕尔贴/接触外流热账','query','evaluator')
cap('sim.step',obj({'dt_s':number('s',0,86400)}),obj({'time_s':number('s',0)}),['controller'],'单一宿主时钟推动热/运动/温程/真实三相采样','simulation','evaluator',{'dt_s':.1})
UNSUPPORTED=['hardware.connect','robot.load','measurement.thin_film_fixture','measurement.infrared_1500c','measurement.gradient_reversal_option','measurement.current_220ma_option','noise.empirical','accuracy.certification','measurement.zt']
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'未实现：'+k)
for k,c in [('plant.configured','controller'),('geometry.configured','sample_rod'),('sample.present','sample_rod'),('power.enabled','power_switch'),('thermal.enabled','furnace'),('furnace.moving','furnace'),('clamp.moving','sample_electrode_cold'),('program.running','controller')]:slot(k,BOOL,'1','当前配置或过程状态',component=c)
for base,travel,component in [('furnace',.18,'furnace'),('clamp',.008,'sample_electrode_cold')]:
    for suffix in ['position_m','target_m']:slot(base+'.'+suffix,number('m',0,travel),'m','水平退炉或上电极开放位移；0为测量位；目标不代表实际位姿','setpoint' if suffix=='target_m' else 'scene',component=component)
slot('furnace.indicated_k',nullable(number('K')),'K','炉体温度量化代理；原显示窗语义未确认，场景仅显式模拟UI覆盖','sensor',component='furnace')
slot('power.angle_rad',number('rad',0,.28),'rad','电源开关工程姿态','scene',component='power_switch')
for k,z in [('thermal.target_k','最终平均温度目标'),('thermal.programmed_k','由限定斜坡随step推进的平均温度程序设定')]:slot(k,number('K'),'K',z,'setpoint',component='furnace')
slot('thermal.target_delta_k',number('K',0,20),'K','下电极正温差目标，非实际温差','setpoint',component='sample_electrode_hot')
for k,z in [('thermal.hot_k','热端探针量化观测'),('thermal.cold_k','冷端探针量化观测'),('thermal.mean_k','两探针温度观测均值'),('thermal.delta_k','热端减冷端探针观测温差')]:slot(k,nullable(number('K')),'K',z+'，工程偏差与校正；不是厂家精度认证','sensor',component='sample_rod')
slot('electrical.current_a',nullable(number('A')),'A','当前相源电流观测；显式顺从电压约束可降低实际电流','sensor',component='sample_electrode_hot')
slot('electrical.voltage_v',nullable(number('V')),'V','四探针S积分电压+电阻压降+接触泄漏，经工程传感校正量化','sensor',component='voltage_probe_hot')
slot('clock.time_s',number('s',0),'s','唯一宿主时钟')
slot('acquisition.phase',PHASE,'1','异步三相采集状态')
slot('acquisition.elapsed_s',number('s',0),'s','本次实际采样时长')
slot('acquisition.points',{'type':'integer','minimum':0,'maximum':200001,'x-unit':'1'},'1','实际采集的相数据行数')
slot('acquisition.polarity',enum(['zero','positive','negative']),'1','当前电流相，停止测量则为零电流')
for k,u in [('truth.furnace_k','K'),('truth.hot_k','K'),('truth.cold_k','K'),('truth.sample_mass_kg','kg')]:slot(k,nullable(number(u)),u,'宿主真实热/物料状态，非代理可见实测值','process_truth','evaluator')

class LinseisLsr3Runtime:
    def __init__(self,instance_id='lsr3_01'):
        self.instance_id=instance_id;self.plant=None;self.geometry=None;self.sample=None;self.record=None;self.power=False;self.thermal_enabled=False;self.target=298.15;self.programmed=298.15;self.ramp_rate=100.;self.gradient=0.;self.x=np.zeros(9);self.axes={key:dict(position=travel,target=travel,start=travel,start_ns=0,arrival_ns=0) for key,travel in [('furnace',.18),('clamp',.008)]};self.time_ns=0;self._clock_remainder_ns=0.;self.revision=0;self.method=copy.deepcopy(METHOD_EXAMPLE);self.calibration=copy.deepcopy(CALIBRATION_EXAMPLE);self.program=[];self.program_running=False;self.program_index=0;self._hold_end_ns=None;self.phase='idle';self.elapsed=0.;self.rows=[];self.points=[];self._phase_index=0;self.error=None;self.operation_id=None;self._counter=0;self._start_ns=0;self.records={};self._principals={};self._requests={};self._owners={};self._cancel_operations={};self._source=None;self._reference_id=None;self._evidence='no_sample';self._operation_source=None;self._operation_evidence='no_measurement';self.mass_loaded=self.mass_removed=self.volume_loaded=self.volume_removed=self.loaded_sensible=self.removed_sensible=0.
    @property
    def time_s(self):return self.time_ns/1e9
    def register_principal(self,principal_id,visibility='agent'):
        if not isinstance(principal_id,str) or not principal_id or principal_id in self._principals or visibility not in ('agent','debug','evaluator'):raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id):
        if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return LinseisLsr3Session(self,principal_id)
    def describe(self):return export_linseis_lsr3_contract_metadata()
    def _moving(self,key):return self.time_ns<self.axes[key]['arrival_ns']
    def _closed(self,key):return self.axes[key]['position']<=1e-12 and self.axes[key]['target']==0.
    def _accessible(self):return self.axes['furnace']['position']>=.17 and self.axes['clamp']['position']>=.007 and not any(self._moving(k) for k in self.axes)
    def _volume(self):return self.geometry['width_m']*self.geometry['depth_m']*self.geometry['length_m'] if self.sample else 0.
    def _mass(self):return self._volume()*self.sample['density_kg_m3'] if self.sample else 0.
    def _capacities(self):
        extra=self._mass()*self.sample['specific_heat_capacity_j_kg_k']/2 if self.sample else 0.;return np.array([self.plant['furnace_capacity_j_k'],.1+extra,.1+extra])
    def _temperature(self,i):return self.plant['ambient_k']+float(self.x[i]) if self.plant else None
    def _polarity(self):return ['zero','positive','negative'][self._phase_index%3] if self.phase=='running' else 'zero'
    def _electrical(self,hot,cold):
        if not self.sample or not self.power or not self._closed('furnace') or not self._closed('clamp'):return dict(current=0.,voltage=0.,bulk_r=0.,contact_r=0.,emf=0.,s=0.,valid=False)
        v=transport(self.record,hot,cold,clamp=True);g=self.geometry;p=self.plant;area=g['width_m']*g['depth_m'];bulk=v['resistivity_ohm_m']*g['length_m']/area;contact=p['current_contact_resistance_ohm'];requested=self.method['current_a']*({'zero':0.,'positive':1.,'negative':-1.}[self._polarity()]);lower=(-p['current_compliance_v']-v['emf_v'])/(bulk+contact);upper=(p['current_compliance_v']-v['emf_v'])/(bulk+contact);current=min(upper,max(lower,requested)) if requested else 0.;span=v['resistivity_ohm_m']*g['probe_spacing_m']/area*self.method['geometry_factor'];voltage=v['emf_v']+current*(span+contact*p['voltage_contact_leak_fraction']);return dict(current=current,voltage=voltage,bulk_r=bulk,contact_r=contact,emf=v['emf_v'],s=v['seebeck_v_k'],valid=v['valid'])
    def _observed(self):
        if not self.power or not self.plant:return dict(hot=None,cold=None,mean=None,delta=None,current=None,voltage=None)
        p=self.plant;c=self.calibration;resolution=p['temperature_resolution_k'];hot=float(round((self._temperature(1)+p['hot_sensor_bias_k']-c['hot_offset_k'])/resolution)*resolution);cold=float(round((self._temperature(2)+p['cold_sensor_bias_k']-c['cold_offset_k'])/resolution)*resolution);v=self._electrical(self._temperature(1),self._temperature(2));voltage=(v['voltage']*p['voltage_gain']+p['voltage_offset_v']-c['voltage_offset_v'])*c['voltage_gain_correction'];voltage=float(round(voltage/p['voltage_resolution_v'])*p['voltage_resolution_v']) if v['valid'] else None;return dict(hot=hot,cold=cold,mean=(hot+cold)/2,delta=hot-cold,current=v['current'] if v['valid'] else None,voltage=voltage)
    def read(self,role='evaluator'):
        if role not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
        o=self._observed();d={'plant.configured':self.plant is not None,'geometry.configured':self.geometry is not None,'sample.present':self.sample is not None,'power.enabled':self.power,'thermal.enabled':self.thermal_enabled,'furnace.moving':self._moving('furnace'),'clamp.moving':self._moving('clamp'),'program.running':self.program_running,'power.angle_rad':.28 if self.power else 0.,'furnace.indicated_k':round(self._temperature(0),4) if self.power and self.plant else None,'thermal.target_k':self.target,'thermal.programmed_k':self.programmed,'thermal.target_delta_k':self.gradient,'thermal.hot_k':o['hot'],'thermal.cold_k':o['cold'],'thermal.mean_k':o['mean'],'thermal.delta_k':o['delta'],'electrical.current_a':o['current'],'electrical.voltage_v':o['voltage'],'clock.time_s':self.time_s,'acquisition.phase':self.phase,'acquisition.elapsed_s':self.elapsed,'acquisition.points':len(self.rows),'acquisition.polarity':self._polarity(),'truth.furnace_k':self._temperature(0),'truth.hot_k':self._temperature(1),'truth.cold_k':self._temperature(2),'truth.sample_mass_kg':self._mass() if self.sample else None}
        for key,axis in self.axes.items():d[key+'.position_m']=axis['position'];d[key+'.target_m']=axis['target']
        return {k:v for k,v in d.items() if role!='agent' or SLOTS[k]['visibility']=='agent'}
    def _slope(self):return math.copysign(self.ramp_rate/60,self.target-self.programmed) if self.power and self.thermal_enabled and abs(self.target-self.programmed)>1e-12 else 0.
    def _propagate(self,ns):
        if not self.plant or not ns:return
        dt=ns/1e9;p=self.plant;C=self._capacities();gf=p['sample_furnace_conductance_w_k'] if self._closed('furnace') else 0.;ga_sample=0. if self._closed('furnace') else p['retracted_conductance_w_k'];gi=p['sample_span_conductance_w_k'];ga=p['furnace_loss_w_k'];on=self.power and self.thermal_enabled and self._closed('furnace');gradient_power=min(p['gradient_heater_limit_w'],(p['sample_furnace_conductance_w_k']+2*gi)*self.gradient) if on else 0.;slope=self._slope();start_program=self.programmed;gradient_shift=(p['sample_furnace_conductance_w_k']+2*gi)*self.gradient/(2*p['sample_furnace_conductance_w_k']) if on else 0.;ambient=p['ambient_k']
        def rhs(t,y):
            f,h,c=y[:3];program=start_program+slope*t;command=max(ambient,program-gradient_shift)-ambient;command_slope=slope if program-gradient_shift>ambient else 0.;loss=ga*f+gf*(2*f-h-c);raw=C[0]*command_slope+p['controller_gain_w_k']*(command-f)+loss;control=min(p['heater_limit_w'],max(-p['cooling_limit_w'],raw)) if on else 0.;v=self._electrical(ambient+h,ambient+c);I=v['current'];joule=I*I*v['bulk_r'];contact=I*I*v['contact_r'];ph=I*v['s']*(ambient+h);pc=-I*v['s']*(ambient+c);out=ga*f+ga_sample*(h+c);d=np.array([(control-ga*f-gf*(2*f-h-c))/C[0],(gf*(f-h)-gi*(h-c)-ga_sample*h+gradient_power+joule/2+ph)/C[1],(gf*(f-c)+gi*(h-c)-ga_sample*c+joule/2+pc)/C[2]])
            return np.r_[d,control,gradient_power,joule,contact,ph+pc,out]
        sol=solve_ivp(rhs,(0.,dt),self.x,method='DOP853',rtol=2e-10,atol=np.r_[[1e-11]*3,[1e-12]*6])
        if not sol.success:raise RuntimeError('THERMAL_INTEGRATION_FAILED')
        self.x=sol.y[:,-1];self.programmed=start_program+slope*dt
        if abs(self.programmed-self.target)<1e-7:self.programmed=self.target
    def _ledger(self):
        stored=float(self._capacities()@self.x[:3]) if self.plant else 0.;control,gradient,joule,contact,peltier,ambient=self.x[3:];electric=joule+contact+peltier
        return dict(mass_loaded_kg=self.mass_loaded,mass_present_kg=self._mass(),mass_removed_kg=self.mass_removed,mass_error_kg=self.mass_loaded-self._mass()-self.mass_removed,volume_loaded_m3=self.volume_loaded,volume_present_m3=self._volume(),volume_removed_m3=self.volume_removed,volume_error_m3=self.volume_loaded-self._volume()-self.volume_removed,controller_input_j=float(control),gradient_input_j=float(gradient),joule_sample_j=float(joule),joule_contact_export_j=float(contact),peltier_input_j=float(peltier),electrical_source_input_j=float(electric),ambient_loss_j=float(ambient),loaded_sensible_j=self.loaded_sensible,removed_sensible_j=self.removed_sensible,thermal_stored_j=stored,energy_error_j=float(control+gradient+electric-contact-ambient+self.loaded_sensible-self.removed_sensible-stored))
    def _result(self):return dict(operation_id=self.operation_id,phase=self.phase,elapsed_s=self.elapsed,rows=copy.deepcopy(self.rows),transport_points=copy.deepcopy(self.points),template_source=self._operation_source,evidence=self._operation_evidence,error_code=self.error)
    def _finish(self,phase,error=None):
        self.phase=phase;self.error=error
        if self.operation_id:self.records[self.operation_id]=self._result()
    def _record(self):
        t=(self.time_ns-self._start_ns)/1e9;o=self._observed();self.rows.append(dict(time_s=t,host_time_s=self.time_s,polarity=self._polarity(),current_a=o['current'],voltage_v=o['voltage'],hot_temperature_k=o['hot'],cold_temperature_k=o['cold'],mean_temperature_k=o['mean'],delta_temperature_k=o['delta']));self._phase_index+=1
        if o['voltage'] is None:self._finish('failed','REFERENCE_TEMPERATURE_RANGE');return
        if self._phase_index%3==0:
            z,p,n=self.rows[-3:];error=None;fit=None
            try:
                if abs(z['delta_temperature_k'])<self.method['minimum_delta_k']:raise ActionRejected('DELTA_TOO_SMALL')
                g=self.geometry;fit=infer_transport(z['voltage_v'],p['voltage_v'],n['voltage_v'],p['current_a'],n['current_a'],z['hot_temperature_k'],z['cold_temperature_k'],g['width_m'],g['depth_m'],g['probe_spacing_m'],self.method['geometry_factor'],self.method['contact_resistance_correction_ohm'])
                if not 1e-6<=abs(fit['seebeck_v_k'])<=.25 or not 1<=fit['conductivity_s_m']<=2e7:raise ActionRejected('RESULT_RANGE')
            except ActionRejected as exc:error=str(exc)
            self.points.append(dict(time_s=t,temperature_k=z['mean_temperature_k'],delta_temperature_k=z['delta_temperature_k'],**{k:fit[k] if fit and not error else None for k in ['seebeck_v_k','conductivity_s_m','resistivity_ohm_m','four_probe_resistance_ohm']},quality='unavailable' if error else 'estimated',error_code=error))
    def _start_segment(self):
        s=self.program[self.program_index];self.target=s['temperature_k'];self.ramp_rate=s['ramp_rate_k_min'];self._hold_end_ns=None
    def _program_event(self):
        # Consume zero-duration holds without losing a segment to a coarse host step.
        while self.program_running and abs(self.programmed-self.target)<=1e-10:
            if self._hold_end_ns is None:self._hold_end_ns=self.time_ns+round(self.program[self.program_index]['hold_s']*1e9)
            if self.time_ns<self._hold_end_ns:return
            self.program_index+=1
            if self.program_index>=len(self.program):self.program_running=False;self._hold_end_ns=None
            else:self._start_segment()
    def invoke(self,capability,**a):
        if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability]['input_schema'],a)
        if capability in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
        if capability=='analysis.transport':return infer_transport(**a)
        if capability=='acquisition.result':return self._result()
        if capability=='provenance.read':return dict(reference_id=self._reference_id,template_source=self._source,evidence=self._evidence)
        if capability=='ledger.read':return self._ledger()
        if capability=='sim.step':self.step(a['dt_s']);return {'time_s':self.time_s}
        if self.phase=='running' and capability not in ('acquisition.stop','power.set','program.stop'):raise ActionRejected('BUSY')
        if capability=='plant.configure':
            if self.plant or not a['accept_engineering']:raise ActionRejected('PRECONDITION_FAILED')
            self.plant=copy.deepcopy(a);self.target=self.programmed=a['ambient_k'];out={'configured':True}
        elif capability=='geometry.configure':
            if self.sample or a['probe_spacing_m']>=a['length_m']:raise ActionRejected('PRECONDITION_FAILED')
            self.geometry=copy.deepcopy(a);out={'configured':True}
        elif capability=='sample.load':
            if not self.plant or not self.geometry or self.sample or not self._accessible() or not a['accept_engineering_density_heat_capacity'] or not a['accept_directional_scalar_proxy']:raise ActionRejected('PRECONDITION_FAILED')
            record=library().get(a['reference_id'])
            if record is None:raise ActionRejected('UNKNOWN_REFERENCE')
            try:
                for key in ('seebeck','resistivity'):
                    tt,vv=curve_xy(record,key)
                    if len(tt)<2 or not np.all(np.isfinite(tt)) or not np.all(np.isfinite(vv)) or not np.all(np.diff(tt)>0) or key=='resistivity' and not np.all(vv>0):raise ValueError()
                if max(curve_xy(record,k)[0][0] for k in ('seebeck','resistivity'))>=min(curve_xy(record,k)[0][-1] for k in ('seebeck','resistivity')):raise ValueError()
            except (ValueError,KeyError,TypeError):raise ActionRejected('REFERENCE_DATA')
            old=self._capacities();self.sample=copy.deepcopy(a);self.record=copy.deepcopy(record);new=self._capacities();delta=a['initial_temperature_k']-self.plant['ambient_k'];self.x[1:3]=(old[1:3]*self.x[1:3]+(new-old)[1:3]*delta)/new[1:3];self.loaded_sensible+=float(sum((new-old)[1:3])*delta);self.mass_loaded+=self._mass();self.volume_loaded+=self._volume();self._reference_id=a['reference_id'];self._source=json.dumps(record['source'],ensure_ascii=False,sort_keys=True);self._evidence='experimental_literature_digitized_directional_transport_curves; explicit_engineering_heat_capacity_contact_and_controller';out={'loaded':True}
        elif capability=='sample.remove':
            if not self.sample or not self._accessible():raise ActionRejected('PRECONDITION_FAILED')
            self.mass_removed+=self._mass();self.volume_removed+=self._volume();self.removed_sensible+=self._mass()*self.sample['specific_heat_capacity_j_kg_k']*float((self.x[1]+self.x[2])/2);self.sample=self.record=None;self._source=self._reference_id=None;self._evidence='no_sample';out={'loaded':False}
        elif capability in ('furnace.set_open','sample.set_clamp'):
            if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
            key='furnace' if capability=='furnace.set_open' else 'clamp';target=(.18 if a['opened'] else 0.) if key=='furnace' else (0. if a['closed'] else .008)
            if key=='furnace' and target==0. and not self._closed('clamp'):raise ActionRejected('PRECONDITION_FAILED')
            if key=='clamp' and target>0 and self.axes['furnace']['position']<.17:raise ActionRejected('PRECONDITION_FAILED')
            axis=self.axes[key];axis.update(target=target,start=axis['position'],start_ns=self.time_ns,arrival_ns=self.time_ns+round(abs(target-axis['position'])/self.plant[key+'_speed_m_s']*1e9));out={'target_m':target}
        elif capability=='power.set':
            if not a['enabled'] and self.phase=='running':self._finish('cancelled','POWER_OFF')
            self.power=a['enabled']
            if not self.power:self.thermal_enabled=False;self.program_running=False;self.target=self.programmed
            out={'enabled':self.power}
        elif capability=='thermal.set_temperature':
            if not self.power or not self.plant or self.program_running or a['temperature_k']<self.plant['ambient_k']:raise ActionRejected('PRECONDITION_FAILED')
            self.target=a['temperature_k'];self.ramp_rate=a['ramp_rate_k_min'];self.thermal_enabled=True;out={'target_k':self.target,'ramp_rate_k_min':self.ramp_rate}
        elif capability=='thermal.set_gradient':
            if not self.plant or not self.power or (self.plant['sample_furnace_conductance_w_k']+2*self.plant['sample_span_conductance_w_k'])*a['delta_k']>self.plant['gradient_heater_limit_w']:raise ActionRejected('PRECONDITION_FAILED')
            self.gradient=a['delta_k'];out={'target_delta_k':self.gradient}
        elif capability=='thermal.disable':self.thermal_enabled=False;self.program_running=False;self.target=self.programmed;out={'enabled':False}
        elif capability=='program.configure':
            if not self.plant or self.program_running or any(s['temperature_k']<self.plant['ambient_k'] for s in a['segments']):raise ActionRejected('PRECONDITION_FAILED')
            self.program=copy.deepcopy(a['segments']);out={'configured':True}
        elif capability=='program.start':
            if not self.power or not self.plant or not self.program or self.program_running:raise ActionRejected('PRECONDITION_FAILED')
            self.program_running=True;self.program_index=0;self.thermal_enabled=True;self._start_segment();out={'running':True}
        elif capability=='program.stop':
            if not self.program_running:raise ActionRejected('NOT_RUNNING')
            self.program_running=False;self.target=self.programmed;self._hold_end_ns=None;out={'running':False}
        elif capability=='method.configure':
            intervals=a['duration_s']/a['phase_interval_s']
            if intervals<3 or intervals>200000 or abs(intervals/3-round(intervals/3))>1e-8 or any(abs(a[k]*1e9-round(a[k]*1e9))>1e-4 for k in ('duration_s','phase_interval_s')):raise ActionRejected('SAMPLE_GRID')
            self.method=copy.deepcopy(a);out={'configured':True}
        elif capability=='sensor.calibrate':self.calibration=copy.deepcopy(a);out={'configured':True}
        elif capability=='acquisition.start':
            if not self.power or not self.plant or not self.sample or not all(self._closed(k) for k in self.axes) or any(self._moving(k) for k in self.axes):raise ActionRejected('PRECONDITION_FAILED')
            o=self._observed()
            if o['voltage'] is None:raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
            if abs(o['delta'])<self.method['minimum_delta_k']:raise ActionRejected('DELTA_TOO_SMALL')
            if abs(o['mean']-self.programmed)>self.method['equilibrium_tolerance_k']:raise ActionRejected('NOT_EQUILIBRATED')
            self._counter+=1;self.operation_id=f'{self.instance_id}:acquisition:{self._counter}';self._operation_source=self._source;self._operation_evidence=self._evidence;self._start_ns=self.time_ns;self._phase_index=0;self.phase='running';self.elapsed=0.;self.rows=[];self.points=[];self.error=None;out={'operation_id':self.operation_id}
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
            self._program_event();boundary=end
            for key in self.axes:
                if self._moving(key):boundary=min(boundary,self.axes[key]['arrival_ns'])
            slope=self._slope()
            if slope:boundary=min(boundary,self.time_ns+max(1,round(abs(self.target-self.programmed)/abs(slope)*1e9)))
            if self.program_running and self._hold_end_ns is not None:boundary=min(boundary,self._hold_end_ns)
            if self.phase=='running':boundary=min(boundary,self._start_ns+(self._phase_index+1)*round(self.method['phase_interval_s']*1e9))
            self._propagate(boundary-self.time_ns);self.time_ns=boundary
            if self.plant:
                for key,axis in self.axes.items():axis['position']=axis['target'] if self.time_ns>=axis['arrival_ns'] else axis['start']+math.copysign(self.plant[key+'_speed_m_s']*(self.time_ns-axis['start_ns'])/1e9,axis['target']-axis['start'])
            if self.phase=='running':
                self.elapsed=(self.time_ns-self._start_ns)/1e9
                if self.time_ns==self._start_ns+(self._phase_index+1)*round(self.method['phase_interval_s']*1e9):self._record()
                if self.phase=='running' and self.elapsed>=self.method['duration_s']:
                    bad=next((p['error_code'] for p in self.points if p['quality']=='unavailable'),None);self._finish('failed' if bad else 'succeeded',bad)
            self._program_event()
        self.revision+=1

class LinseisLsr3Session:
    def __init__(self, host, principal_id): self._host = host; self._principal = principal_id
    @property
    def _role(self): return self._host._principals[self._principal]
    def _target(self, instance_id):
        if instance_id != self._host.instance_id: raise ActionRejected('UNKNOWN_INSTANCE')
    def describe(self, *, instance_id=None, asset_type_id=None):
        if (instance_id is None) == (asset_type_id is None): raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None: self._target(instance_id)
        if asset_type_id is not None and asset_type_id != ASSET_TYPE_ID: raise ActionRejected('UNKNOWN_INSTANCE')
        meta = export_linseis_lsr3_contract_metadata()
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
            result['evidence'] = 'observed_three_phase_transport_with_explicit_contact_geometry_and_thermal_assumptions; selected template details require evaluator provenance.read' if result['operation_id'] is not None else 'no_measurement'
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
            if capability_id in ('acquisition.stop','acquisition.result','program.stop') and self._role=='agent' and h.operation_id and h._owners.get(h.operation_id)!=self._principal: raise ActionRejected('ACCESS_DENIED')
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
 'plant.configure':['plant.configured','thermal.target_k','thermal.programmed_k','truth.furnace_k','truth.hot_k','truth.cold_k'],
 'geometry.configure':['geometry.configured'],
 'sample.load':['sample.present','truth.sample_mass_kg','truth.hot_k','truth.cold_k','thermal.hot_k','thermal.cold_k','thermal.mean_k','thermal.delta_k','electrical.current_a','electrical.voltage_v'],
 'sample.remove':['sample.present','truth.sample_mass_kg','electrical.current_a','electrical.voltage_v'],
 'furnace.set_open':['furnace.target_m','furnace.moving','electrical.current_a','electrical.voltage_v'],
 'sample.set_clamp':['clamp.target_m','clamp.moving','electrical.current_a','electrical.voltage_v'],
 'power.set':['power.enabled','power.angle_rad','thermal.enabled','thermal.target_k','program.running','furnace.indicated_k','thermal.hot_k','thermal.cold_k','thermal.mean_k','thermal.delta_k','electrical.current_a','electrical.voltage_v','acquisition.phase','acquisition.polarity'],
 'thermal.set_temperature':['thermal.target_k','thermal.enabled'],
 'thermal.set_gradient':['thermal.target_delta_k'],
 'thermal.disable':['thermal.enabled','program.running','thermal.target_k'],
 'program.start':['program.running','thermal.enabled','thermal.target_k'],
 'program.stop':['program.running','thermal.target_k'],
 'method.configure':['electrical.current_a','electrical.voltage_v'],
 'sensor.calibrate':['thermal.hot_k','thermal.cold_k','thermal.mean_k','thermal.delta_k','electrical.voltage_v'],
 'acquisition.start':['acquisition.phase','acquisition.elapsed_s','acquisition.points','acquisition.polarity','electrical.current_a','electrical.voltage_v'],
 'acquisition.stop':['acquisition.phase','acquisition.polarity','electrical.current_a','electrical.voltage_v'],
 'sim.step':list(SLOTS)}
for k,v in EFFECTS.items():CAPS[k]['effects']=v
ERRORS=sorted(set(__import__('re').findall(r"ActionRejected\('([A-Z_0-9]+)'",Path(__file__).read_text()))|{'POWER_OFF','USER_CANCEL'})

def export_linseis_lsr3_contract_metadata():
    slots={};caps={}
    for k,v in SLOTS.items():slots[k]=dict(meaning_zh=v['meaning'],component_ids=[v['component']],value_schema=copy.deepcopy(v['schema']),unit=v['unit'],reference_conditions='SI; LSR-3 IR800 only; ambient..1073.15K; signed Seebeck, sigma1..2e7S/m; specimen3x3x20mm and 8mmprobe span example.',origin=v['origin'],visibility=v['visibility'],access='read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'LinseisLsr3Session.read(state_ids=[{k!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description='Single host clock with fractional nanosecond carry; three-phase voltage rows have independent exact timestamps; reads do not advance time.'),source_ids=['simulation_assumption'])
    limits=[
      '只实现红外炉室温至800°C配置，温程0.01至100K/min；1500°C炉、薄膜夹具、220mA电流源和上电极反向梯度选件均unsupported。下电极副加热仅产生正目标温差。',
      '实验文献数字化S(T)/rho(T)曲线按各自原始温点线性插值；只在共同温区且两个探针温度均落域时报告测量，绝不把越界值当真实外推。源曲线不是原机原始数据文件。',
      '默认HD-Sb1.7的in-plane测试方向来自对应论文；sample.load必须显式接受标量代表原记录方向，不声称全体材料各向同性。记录合金配比、掺杂与温区保留在模板。',
      '密度、比热、炉热容量、热导、控制器功率/顺从电压、接触电阻、传感偏置均为显式工程输入；供电230/208V16A不当作部件加热功率。电极节点各0.1J/K为声明工程假设。',
      '有限炉体+两探针样品集中节点保留质量、显热、环境散热、梯度热源、焦耳及帕尔贴功；导热采用宿主有效热导而非声称空间温场求解。载入/移出样品显热进入独立账本。',
      '实际棒长决定体积和全部棒电阻，4/6/8mm探针间距决定四探针压降；几何修正因子仅代表显式用户校正。几何模型默认为3x3x20mm、8mm探针，其他配置不宣称3D自动缩放。',
      '零流/+I/-I各保持一个真实相间隔，电压电流为该相结束时采样；R=(Vplus-Vminus)/(Iplus-Iminus)-RcontactCorrection，S=Vzero/(Th-Tc)。三相间梯度漂移会产生实际偏差，未直接复制参考真值。',
      '四探针理想消除电流接触电阻；显式有限电压接触泄漏进入读数并允许外部校正，接触I²R作为外部端点热流导出，不暗算进入样品。样品焦耳热按全棒电阻分到两节点。',
      '两半棒各自等温集中近似：同侧探针与端电极共用温度节点，探针外杆段仅参与rho*L/A电阻及总热容量，未解探针外温差/热电势；不声称真实棒内线性空间温场。',
      '常系数跨探针帕尔贴近似用Seff=integral(SdT)/(Th-Tc)，Qhot=I*Seff*Th,Qcold=-I*Seff*Tc，净值等于电源热电功I*integral(SdT)；未解Thomson热或变系数分布温场。',
      '热电源即使材料参考域暂不可观测也以端点夹持插值的工程系数维持数值能量，下一采样立即失败并切断电流，不声称域外输运测量；被动显热仍继续。',
      '控制为带有限功率的斜坡前馈+误差反馈，默认足以实现100K/min炉体升温而样品有有限滞后；不是OEM升温性能认证。降温亦为显式工程散热/双向调温代理，不证实厂家主动制冷配置。',
      'Seebeck推断按任务厂家10nV/K输出量化；厂家±7%和温度±1.5K不是模拟噪声分布或实测认证。文献电阻率resolution原写nOhm维度不清，不虚构10nOhm*m分辨率。',
      '两显示窗原功能未取得确定证据；场景映射炉温和均温仅是明确模拟UI覆盖。水平退炉和上电极夹持为宿主手动操作代理，非机器人或厂家自动驱动验证。',
      '所有会话公开result/poll统一脱敏，即使bridge内部evaluator转发；当前选中记录来源仅evaluator provenance.read。共享Python内存与磁盘不构成OS安全隔离。',
      '真实接触/冷却水/气氛控制/真空/机械机器人/厂家硬件、实测精度噪声验证not_run；气氛化学反应、热膨胀及ZT计算未实现。']
    for k,v in CAPS.items():
        un=k in UNSUPPORTED;async_=k=='acquisition.start';caps[k]={key:copy.deepcopy(v[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']}
        caps[k].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ('scene','robot','hardware') else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.linseis_lsr3.LinseisLsr3Session.invoke',invocation=f'session.invoke(instance_id="lsr3_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),preconditions=[] if un or v['kind'] in ('query','analysis') else [dict(description_zh='配置、退炉/夹持、真实模板重叠温区和相采样网格由入口原子检查。',state_ids=['power.enabled','sample.present','furnace.position_m','clamp.position_m','acquisition.phase'],enforced_by='runtime',check='LinseisLsr3Runtime.invoke guards before state commit')],state_effects=[dict(state_id=s,timing='immediate',description_zh='调用实际即时变化') for s in v['effects']],observes=[],model_effects=dict(mode='none',description_zh='过程输出可映射状态，实际模型求值由单独场景验证登记。',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='真实三相采样窗口结束并冻结电压、电流、温度与输运推断' if async_ else '原子命令提交；热/程序/关节/采样仅step推进',completion_state_ids=['acquisition.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','specimen','current_source','voltage_probes'],partial_failure_zh='取消/断电停止电测并保留已观测数据；显热和温控按状态继续；功率、物料与能量不回滚。'),errors=[dict(code=e,meaning_zh=e,side_effects='partial' if async_ and e in ('POWER_OFF','USER_CANCEL') else 'none') for e in ERRORS],verification=[],limitations=limits,source_ids=['manufacturer_primary','simulation_assumption','template_library'])
        if k in ('thermal.set_temperature','thermal.set_gradient','thermal.disable','program.start','program.stop','furnace.set_open','sample.set_clamp','acquisition.start','power.set'):
            caps[k]['state_effects'] += [dict(state_id=s,timing='on_step',description_zh='宿主推进温程、热学、电测或机械位置') for s in SLOTS if s not in v['effects'] and s not in ('plant.configured','geometry.configured','sample.present','power.enabled')]
    mechanism=dict(component_ids=['furnace','sample_rod','sample_electrode_hot','sample_electrode_cold','voltage_probe_hot','voltage_probe_cold'],state_ids=list(slots),meaning_zh='有限热容三节点、真实温程和电流反转电测；从公开实验输运曲线生成观测再外部反演',implementation='lumped',equations=[
      'Vte=integral(Tcold..Thot) S(T)dT, rho=rho((Thot+Tcold)/2) by piecewise-linear interpolation; Rbulk=rho*L/A; Rspan=rho*lprobe/A*geometry_factor.',
      'V4=Vte+I*(Rspan+Rcontact*voltage_leak_fraction). Actual current is bounded by current-source compliance across full bar plus contacts. Zero, positive and negative phases evolve in host time.',
      'Rmeasured=(Vplus-Vminus)/(Iplus-Iminus)-declared_contact_correction; rho=Rmeasured*A/(lprobe*geometry_factor); sigma=1/rho; S=Vzero/(Thot-Tcold), quantized10nV/K. Only actual observed inputs enter inverse.',
      'Cf*dTf=Pcontrol-Ga*(Tf-Ta)-Gf*(2Tf-Th-Tc); Ch*dTh=Gf*(Tf-Th)-Gi*(Th-Tc)-Gair*(Th-Ta)+Pgradient+I^2Rbulk/2+I*Seff*Th; Cc*dTc=Gf*(Tf-Tc)+Gi*(Th-Tc)-Gair*(Tc-Ta)+I^2Rbulk/2-I*Seff*Tc.',
      'Pgradient=(Gf_nominal+2Gi)*positive_gradient_target subject to explicit engineering limit. Furnace target=Tmean_program-Pgradient/(2Gf_nominal), lower bounded by ambient. Open furnace switches sample coupling to ambient.',
      'Pcontrol=clip(Cf*program_slope+K*(Tcommand-Tf)+instantaneous_furnace_heat_losses,-cooling_limit,heater_limit). Piecewise slope0.01..100K/min and holds are event boundaries; finite C and exact ledgers prevent thermal teleportation.',
      'Electrical source energy=int[I^2*(Rbulk+Rcontact)+I*Vte]dt; contact heat exports int[I^2Rcontact]dt. The remaining source power equals sample Joule plus net Peltier heat.',
      'Loading capacity-weights incoming sample temperature and accounts Csample*(Tload-Ta); removing exports actual sample sensible energy while electrode0.1J/K nodes retain their temperature.',
      'DOP853 integrates the coupled conserved node and energy equations between ramp/motion/sampling event boundaries; fractional nanosecond carry supports60Hz host steps.'],parameters={},source_ids=['simulation_assumption','template_library'],limitations=limits)
    spec=importlib.util.spec_from_file_location('_lsr_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()});ann.annotate_export(SLUG,caps,slots,{'thermoelectric_transport':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'geometry.configure':(GEOMETRY,GEOMETRY_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sensor.calibrate':(CALIBRATION,CALIBRATION_EXAMPLE)})
    for key,value in SLOTS.items():slots[key]['meaning_zh']=value['meaning']
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'thermoelectric_transport':mechanism})

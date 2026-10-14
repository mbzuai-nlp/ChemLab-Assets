"""CHEM-126 conservative dispersion transport, finite-layer two-flux optics and timed height scans."""
from pathlib import Path
import copy,json,math,importlib.util
import numpy as np
PROTOCOL_VERSION='1.0.0';SLUG='microtrac_turbiscanlab';CATALOG_ID='CHEM-126';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch03/t4_measure'/SLUG
DT=.1;G=9.80665;KB=1.380649e-23;WAVELENGTH=880e-9;SCAN_STEP=20e-6;MAX_HEIGHT=.055
class ActionRejected(ValueError):pass
def number(unit='1',lo=None,hi=None):
 s={'type':'number','x-unit':unit}
 if lo is not None:s['minimum']=lo
 if hi is not None:s['maximum']=hi
 return s
def integer(lo=0,hi=1000000):return dict(type='integer',minimum=lo,maximum=hi,**{'x-unit':'1'})
def obj(properties):return dict(type='object',properties=properties,required=list(properties),additionalProperties=False)
def array(items,minimum=0,maximum=10000):return dict(type='array',items=items,minItems=minimum,maxItems=maximum)
def enum(items):return dict(type='string',enum=list(items))
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
BOOL={'type':'boolean'};TEXT={'type':'string'};PHASE=enum(['idle','running','succeeded','failed','cancelled'])
def validate(s,v):
 if 'anyOf' in s:
  for option in s['anyOf']:
   try:validate(option,v);return
   except ActionRejected:pass
  raise ActionRejected('INVALID_ARGUMENT')
 kind=s.get('type')
 if kind in ('number','integer'):
  if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or kind=='integer' and type(v) is not int or not s.get('minimum',-math.inf)<=v<=s.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
 elif kind=='boolean' and type(v) is not bool:raise ActionRejected('INVALID_ARGUMENT')
 elif kind=='string' and not isinstance(v,str):raise ActionRejected('INVALID_ARGUMENT')
 elif kind=='null' and v is not None:raise ActionRejected('INVALID_ARGUMENT')
 elif kind=='object':
  if not isinstance(v,dict) or set(v)!=set(s['properties']):raise ActionRejected('INVALID_ARGUMENT')
  for key,value in v.items():validate(s['properties'][key],value)
 elif kind=='array':
  if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
  for value in v:validate(s['items'],value)
 if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')


def library():
 p=ASSET/'turbiscan_library.json';return json.loads(p.read_text()) if p.exists() else {'substances':{}}

def two_flux(absorption_m_inv,scattering_m_inv,path_m):
 """Finite homogeneous slab, unit incident forward flux and no back illumination.
 R is hemispherical diffuse proxy, NOT the135degree detector reading.
 """
 k=np.asarray(absorption_m_inv,dtype=float);s=np.asarray(scattering_m_inv,dtype=float);b=np.sqrt(k*(k+2*s));x=b*path_m;small=b==0.;safe=np.where(small,1.,b);decay=np.exp(-x);one_minus=-np.expm1(-2*x);den=safe*(1+decay*decay)+(k+s)*one_minus
 t=np.where(small,1/(1+s*path_m),2*safe*decay/den);r=np.where(small,s*path_m/(1+s*path_m),s*one_minus/den)
 return r,t

PLANT_EXAMPLE=dict(accept_engineering=True,ambient_k=298.15,thermal_capacity_j_k=100.,heater_max_w=100.,heater_gain_w_k=100.,heat_loss_w_k=1.,temperature_sensor_tau_s=.2,temperature_gain=1.,temperature_offset_k=0.,scan_speed_m_s=.01,return_speed_m_s=.02,safe_access_temperature_k=313.15,transport_cells=48)
PLANT=obj({'accept_engineering':BOOL,'ambient_k':number('K',278.15,333.15),'thermal_capacity_j_k':number('J/K',.1,1e6),'heater_max_w':number('W',.01,1000),'heater_gain_w_k':number('W/K',.01,1e5),'heat_loss_w_k':number('W/K',0,1000),'temperature_sensor_tau_s':number('s',.001,100),'temperature_gain':number('1',.9,1.1),'temperature_offset_k':number('K',-10,10),'scan_speed_m_s':number('m/s',1e-5,.1),'return_speed_m_s':number('m/s',1e-5,.1),'safe_access_temperature_k':number('K',278.15,333.15),'transport_cells':integer(8,128)})
SAMPLE_EXAMPLE=dict(reference_id='lentil_soybean_hipe_untreated',volume_m3=20e-6,height_m=20e-6/(math.pi*(.0251/2)**2),particle_diameter_m=50e-6,volume_fraction=.75,particle_density_kg_m3=920.,liquid_density_kg_m3=1000.,dynamic_viscosity_pa_s=.01,maximum_packing_fraction=.95,hindered_exponent=4.65,extra_diffusion_m2_s=0.,aggregation_rate_s=.005,particle_absorption_m_inv=10.,carrier_absorption_m_inv=0.,scattering_m_inv=100.,optical_path_m=.025,accept_engineering=True,accept_unknown_conditions=True,accept_template_condition_transfer=False,accept_concentrated_stokes_proxy=True)
SAMPLE=obj({'reference_id':TEXT,'volume_m3':number('m^3',2e-6,20e-6),'height_m':number('m',.002,MAX_HEIGHT),'particle_diameter_m':number('m',1e-8,1e-3),'volume_fraction':number('1',1e-6,.95),'particle_density_kg_m3':number('kg/m^3',100,20000),'liquid_density_kg_m3':number('kg/m^3',100,3000),'dynamic_viscosity_pa_s':number('Pa*s',1e-4,100),'maximum_packing_fraction':number('1',.05,.95),'hindered_exponent':number('1',1,8),'extra_diffusion_m2_s':number('m^2/s',0,1e-6),'aggregation_rate_s':number('1/s',0,10),'particle_absorption_m_inv':number('1/m',0,1e6),'carrier_absorption_m_inv':number('1/m',0,1e6),'scattering_m_inv':number('1/m',0,1e6),'optical_path_m':number('m',.001,.05),'accept_engineering':BOOL,'accept_unknown_conditions':BOOL,'accept_template_condition_transfer':BOOL,'accept_concentrated_stokes_proxy':BOOL})
METHOD_EXAMPLE=dict(scan_start_m=.002,scan_end_m=.0394,scan_interval_s=8.,duration_s=30.,temperature_tolerance_k=.25,tsi_channel='backscatter',front_channel='backscatter',front_threshold_fraction=.05)
METHOD=obj({'scan_start_m':number('m',0,MAX_HEIGHT),'scan_end_m':number('m',0,MAX_HEIGHT),'scan_interval_s':number('s',.1,86400),'duration_s':number('s',.2,86400),'temperature_tolerance_k':number('K',.01,5),'tsi_channel':enum(['backscatter','transmission','auto_baseline']),'front_channel':enum(['backscatter','transmission']),'front_threshold_fraction':number('1',1e-6,.99)})
VECTOR=array(number('1'),1,10000);NULL_VECTOR=nullable(VECTOR)
PROFILE=obj({'scan_index':integer(),'start_time_s':number('s'),'end_time_s':number('s'),'height_m':array(number('m'),1,10000),'point_time_s':array(number('s'),1,10000),'transmission_fraction':NULL_VECTOR,'backscatter_fraction':NULL_VECTOR,'tsi':nullable(number('1')),'front_height_m':nullable(number('m')),'front_time_s':nullable(number('s')),'saturated_points':integer()})
RESULT=obj({'operation_id':nullable(TEXT),'phase':enum(['idle','running','succeeded','failed','cancelled']),'elapsed_s':number('s'),'profiles':array(PROFILE,0,10000),'partial_profile':nullable(PROFILE),'tsi':nullable(number('1')),'migration_velocity_m_s':nullable(number('m/s')),'analysis_reasons':array(TEXT,0,100),'reference_id':nullable(TEXT),'template_source':nullable(TEXT),'evidence':TEXT,'error_code':nullable(TEXT)})
LEDGER=obj({k:number(u) for k,u in [('particle_loaded_kg','kg'),('particle_present_kg','kg'),('particle_recovered_kg','kg'),('particle_error_kg','kg'),('liquid_loaded_kg','kg'),('liquid_present_kg','kg'),('liquid_recovered_kg','kg'),('liquid_error_kg','kg'),('sample_volume_m3','m^3'),('particle_volume_error_m3','m^3'),('heater_input_j','J'),('ambient_heat_loss_j','J'),('stored_thermal_energy_j','J'),('thermal_error_j','J')]})
CAPS={};SLOTS={}
def cap(k,i,o,c,z,kind='device_command',visibility='agent',example=None,effects=None):CAPS[k]=dict(input_schema=i,output_schema=o,component_ids=c,function_zh=z,kind=kind,visibility=visibility,example_arguments=example or {},effects=effects or [])
def slot(k,s,u,z,origin='derived',visibility='agent',component='controller',write=None):SLOTS[k]=dict(schema=s,unit=u,meaning=z,origin=origin,visibility=visibility,component=component,write=write)
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller','thermal_block'],'显式接受热学/扫描速度/输运网格工程参数','configuration','evaluator',PLANT_EXAMPLE,['plant.configured','truth.temperature_k','thermal.target_k'])
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_cell'],'取出开盖下装入有限乳液/悬浮液；初始光学数据与未知动力学分开','configuration','evaluator',SAMPLE_EXAMPLE,['sample.present','truth.particle_mass_kg','truth.liquid_mass_kg','truth.volume_m3','truth.particle_diameter_m','truth.minimum_volume_fraction','truth.maximum_volume_fraction','truth.sample_age_s'])
cap('sample.remove',obj({}),obj({'removed':BOOL}),['sample_cell'],'冷却、取出并开盖后回收有限两相样品','configuration','evaluator',effects=['sample.present','truth.particle_mass_kg','truth.liquid_mass_kg','truth.volume_m3','truth.particle_diameter_m','truth.minimum_volume_fraction','truth.maximum_volume_fraction','truth.sample_age_s'])
for k,arg,sc,c,z,example,effects in [('power.set','enabled',BOOL,'controller','电源',True,['power.enabled','power.angle_rad','thermal.indicated_k']),('lid.set','opened',BOOL,'lid','手动样品管盖',False,['lid.open','lid.lift_m']),('sample.set_position','distance_m',number('m',0,.105),'sample_cell','整管人工插拔，非电动进样',0.,['sample.lift_m']),('service.set','distance_m',number('m',0,.06),'service_cover','断电维护盖',.06,['service.slide_m']),('scan.move','position_m',number('m',0,MAX_HEIGHT),'scan_head','设置扫描头目标；实际位置由宿主时间推进',0.,['head.target_m']),('thermal.set_temperature','temperature_k',number('K',278.15,333.15),'thermal_block','仅设置加热目标，不提供低于环境的制冷',298.15,['thermal.target_k'])]:cap(k,obj({arg:sc}),obj({arg:sc}),[c],z,example={arg:example},effects=effects)
CALIBRATION=obj({'temperature_zero_k':number('K',-10,10),'transmission_gain':number('1',.01,100),'transmission_zero_fraction':number('1',-1,1),'backscatter_gain':number('1',.01,100),'backscatter_zero_fraction':number('1',-1,1)})
cap('sensor.calibrate',CALIBRATION,CALIBRATION,['transmission_detector','backscatter_detector','thermal_block'],'外部独立零点和增益；不免费读取样品真值','configuration',example=dict(temperature_zero_k=0.,transmission_gain=1.,transmission_zero_fraction=0.,backscatter_gain=1.,backscatter_zero_fraction=0.),effects=['thermal.indicated_k'])
cap('method.configure',METHOD,METHOD,['controller','scan_head'],'20µm真实采样网格、逐点时间、扫描周期和TSI约定','configuration',example=METHOD_EXAMPLE)
cap('acquisition.start',obj({}),obj({'operation_id':TEXT}),['controller','scan_head','led_880nm','transmission_detector','backscatter_detector'],'异步扫描高度与时间；过程在等待和扫描期间继续演化',effects=['acquisition.phase','acquisition.elapsed_s','acquisition.scan_index','acquisition.points','head.target_m'])
cap('acquisition.stop',obj({}),obj({'phase':enum(['cancelled'])}),['controller','scan_head'],'取消冻结完整/部分扫描，保留浓度迁移和温度',effects=['acquisition.phase','head.target_m'])
cap('acquisition.result',obj({}),RESULT,['controller'],'纯读来源/方法/部分扫描冻结结果','query')
cap('reference.query',obj({'reference_id':TEXT}),obj({'reference_json':TEXT,'template_source':TEXT,'evidence':TEXT}),['controller'],'查询原始高度/时间光学参考；单TSI不能反造扫描','query',example={'reference_id':SAMPLE_EXAMPLE['reference_id']})
TSI_INPUT=obj({'transmission_fraction':nullable(array(VECTOR,2,10000)),'backscatter_fraction':nullable(array(VECTOR,2,10000)),'channel':enum(['backscatter','transmission','auto_baseline'])})
cap('analysis.tsi',TSI_INPUT,obj({'tsi':number('1'),'tsi_series':array(number('1'),2,10000),'channels':array(enum(['transmission','backscatter']),1,10000),'convention':TEXT}),['controller'],'公开厂家公式：相邻扫描绝差累计/真实高度点数，fraction转百分数','analysis',example={'transmission_fraction':[[.001,.1],[.001,.2]],'backscatter_fraction':[[.5,.1],[.55,.1]],'channel':'auto_baseline'})
OPTICS_INPUT=obj({'absorption_m_inv':number('1/m',0,1e6),'scattering_m_inv':number('1/m',0,1e6),'path_m':number('m',1e-6,.1)})
cap('analysis.two_flux',OPTICS_INPUT,obj({'hemispherical_reflectance':number('1',0,1),'transmittance':number('1',0,1),'absorptance':number('1',0,1)}),['controller'],'有限层KM工程双通量，不等同135°BS探测器','analysis',example={'absorption_m_inv':10.,'scattering_m_inv':100.,'path_m':.025})
cap('ledger.read',obj({}),LEDGER,['controller'],'宿主有限固/液质量与热量账本','query','evaluator')
cap('sim.step',obj({'dt_s':number('s',0,3600)}),obj({'time_s':number('s')}),['controller'],'唯一宿主时间，扫描事件处内部细分','simulation','evaluator',{'dt_s':.1})
UNSUPPORTED=['particle_size.inverse_mie','particle_size.distribution','shelf_life.predict','noise.empirical','accuracy.certification','robot.sample_handling','hardware.connect','bar_code.hardware_scan']
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'未实现：'+k)
for k,z,c in [('plant.configured','已接受显式工程参数','controller'),('sample.present','有限乳液/悬浮液已装入','sample_cell'),('power.enabled','供电','controller'),('lid.open','样品管盖开启','lid')]:slot(k,BOOL,'1',z,component=c)
for k,hi,z,c in [('power.angle_rad',.28,'电源开关工程姿态','power_switch'),('lid.lift_m',.030,'管盖手动抬升','lid'),('sample.lift_m',.105,'整管人工插拔','sample_cell'),('service.slide_m',.06,'维护盖位移','service_cover'),('head.position_m',MAX_HEIGHT,'扫描头真实高度，相对样品内部底面','scan_head')]:
 u='rad' if k.endswith('rad') else 'm';slot(k,number(u,0,hi),u,z,'scene',component=c)
slot('head.target_m',number('m',0,MAX_HEIGHT),'m','扫描头目标高度','setpoint',component='scan_head');slot('thermal.target_k',number('K',278.15,333.15),'K','环境至60°C加热目标','setpoint',component='thermal_block');slot('thermal.indicated_k',nullable(number('K')),'K','显式一阶温度观测与外部校准','derived',component='thermal_block')
for k,u,z in [('truth.temperature_k','K','有限热容温度'),('truth.particle_mass_kg','kg','封闭容器分散相质量'),('truth.liquid_mass_kg','kg','封闭容器连续相质量'),('truth.volume_m3','m^3','有限总体积'),('truth.particle_diameter_m','m','显式聚集模型的体积等效直径，非反演结果'),('truth.minimum_volume_fraction','1','守恒输运网格的最小体积分数'),('truth.maximum_volume_fraction','1','守恒输运网格的最大体积分数'),('truth.sample_age_s','s','装样后的真实演化年龄')]:slot(k,number(u,0),u,z,'process_truth','evaluator')
slot('clock.time_s',number('s',0),'s','宿主时间');slot('acquisition.phase',enum(['idle','running','succeeded','failed','cancelled']),'1','扫描终态');slot('acquisition.elapsed_s',number('s',0),'s','本次扫描真实经过时间');slot('acquisition.scan_index',integer(),'1','已完成扫描数');slot('acquisition.points',integer(),'1','已采集完整和部分扫描点数')
PHYSICS_EFFECTS=['clock.time_s','truth.temperature_k','truth.particle_diameter_m','truth.minimum_volume_fraction','truth.maximum_volume_fraction','truth.sample_age_s','truth.particle_mass_kg','truth.liquid_mass_kg','thermal.indicated_k','head.target_m','head.position_m','acquisition.phase','acquisition.elapsed_s','acquisition.scan_index','acquisition.points']
CAPS['sim.step']['effects']=PHYSICS_EFFECTS

def tsi_analysis(a):
 t=a['transmission_fraction'];b=a['backscatter_fraction'];present=[x for x in [t,b] if x is not None]
 if not present or len(present[0])<2:raise ActionRejected('INSUFFICIENT_SCANS')
 n=len(present[0]);nh=len(present[0][0])
 if not nh or any(len(p)!=n or any(len(row)!=nh or any(not math.isfinite(v) for v in row) for row in p) for p in present):raise ActionRejected('INVALID_PROFILE_SHAPE')
 channel=a['channel']
 if channel=='transmission' and t is None or channel=='backscatter' and b is None:raise ActionRejected('CHANNEL_UNAVAILABLE')
 if channel=='auto_baseline' and (t is None or b is None):raise ActionRejected('CHANNEL_UNAVAILABLE')
 use_b=np.array([channel=='backscatter']*nh) if channel!='auto_baseline' else np.array(t[0])<.002
 selected=np.where(use_b,np.array(b) if b is not None else np.zeros((n,nh)),np.array(t) if t is not None else np.zeros((n,nh)));delta=np.mean(np.abs(np.diff(selected,axis=0)),axis=1)*100;series=np.r_[0.,np.cumsum(delta)]
 return dict(tsi=float(series[-1]),tsi_series=list(map(float,series)),channels=['backscatter' if x else 'transmission' for x in use_b],convention='OEM cumulative adjacent-scan absolute difference / actual included height-point count. Fraction intensities converted to percentage points. auto_baseline selects BS where first-scan T<0.002; freezes that mask for all scans. Endpoint count and fixed initial mask are declared engineering choices; no proprietary filtering or index clipping.')

class MicrotracTurbiscanlabRuntime:
 def __init__(self,instance_id='turbiscanlab_01'):
  self.instance_id=instance_id;self.plant=None;self.sample=None;self.reference=None;self.optical_grid=[];self.baseline_t=None;self.baseline_b=None;self.power=False;self.lid_open=False;self.sample_lift=0.;self.service=0.;self.head=0.;self.head_target=0.;self.temperature=298.15;self.initial_temperature=298.15;self.target_temperature=298.15;self.temperature_sensor=298.15;self.calibration=dict(CAPS['sensor.calibrate']['example_arguments']);self.phi=[];self.centers=[];self.age=0.;self.diameter=0.;self.particle_loaded=0.;self.particle_recovered=0.;self.liquid_loaded=0.;self.liquid_recovered=0.;self.initial_particle_volume=0.;self.heater_energy=0.;self.ambient_loss=0.;self.process_fault=None;self.method=copy.deepcopy(METHOD_EXAMPLE);self.phase='idle';self.elapsed=0.;self.profiles=[];self.partial=None;self.tsi=None;self.velocity=None;self.reasons=[];self.error=None;self.operation_id=None;self.source=None;self.result_reference=None;self.result_evidence=None;self.operation_method=None;self.operation_sample=None;self._counter=0;self.operation_start=0.;self.first_scan_offset=0.;self.planned_scans=0;self.scan_active=False;self._event_time=None;self._event_kind=None;self.scan_grid=[];self._point_index=0;self.records={};self.time_s=0.;self._ticks=0;self._remainder=0.;self.revision=0;self._requests={};self._owners={};self._principals={'agent':'agent','evaluator':'evaluator'}
 def register_principal(self,principal_id,visibility='agent'):
  if not isinstance(principal_id,str) or not principal_id or principal_id in self._principals or visibility not in ('agent','debug','evaluator'):raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='agent'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return MicrotracTurbiscanlabSession(self,principal_id)
 def describe(self):return export_microtrac_turbiscanlab_contract_metadata()
 def _masses(self):
  if not self.sample:return 0.,0.
  s=self.sample;pv=s['volume_m3']*sum(self.phi)/len(self.phi);return pv*s['particle_density_kg_m3'],(s['volume_m3']-pv)*s['liquid_density_kg_m3']
 def _indicated(self):return self.temperature_sensor*(self.plant['temperature_gain'] if self.plant else 1.)+(self.plant['temperature_offset_k'] if self.plant else 0.)-self.calibration['temperature_zero_k']
 def _heating(self):return self.power and self.plant is not None and self.temperature<self.target_temperature-1e-10
 def read(self,role='evaluator'):
  pm,lm=self._masses();d={'plant.configured':self.plant is not None,'sample.present':self.sample is not None,'power.enabled':self.power,'lid.open':self.lid_open,'power.angle_rad':.28 if self.power else 0.,'lid.lift_m':.030 if self.lid_open else 0.,'sample.lift_m':self.sample_lift,'service.slide_m':self.service,'head.position_m':self.head,'head.target_m':self.head_target,'thermal.target_k':self.target_temperature,'thermal.indicated_k':self._indicated() if self.power else None,'truth.temperature_k':self.temperature,'truth.particle_mass_kg':pm,'truth.liquid_mass_kg':lm,'truth.volume_m3':self.sample['volume_m3'] if self.sample else 0.,'truth.particle_diameter_m':self.diameter,'truth.minimum_volume_fraction':min(self.phi) if self.phi else 0.,'truth.maximum_volume_fraction':max(self.phi) if self.phi else 0.,'truth.sample_age_s':self.age,'clock.time_s':self.time_s,'acquisition.phase':self.phase,'acquisition.elapsed_s':self.elapsed,'acquisition.scan_index':len(self.profiles),'acquisition.points':sum(len(x['height_m']) for x in self.profiles)+(len(self.partial['height_m']) if self.partial else 0),'lamp.power':self.power,'lamp.heating':self._heating(),'lamp.scanning':self.scan_active}
  return {k:copy.deepcopy(v) for k,v in d.items() if role!='agent' or SLOTS[k]['visibility']=='agent'}
 def _ledger(self):
  pm,lm=self._masses();pv=pm/self.sample['particle_density_kg_m3'] if self.sample else 0.;stored=self.plant['thermal_capacity_j_k']*(self.temperature-self.initial_temperature) if self.plant else 0.
  return dict(particle_loaded_kg=self.particle_loaded,particle_present_kg=pm,particle_recovered_kg=self.particle_recovered,particle_error_kg=self.particle_loaded-pm-self.particle_recovered,liquid_loaded_kg=self.liquid_loaded,liquid_present_kg=lm,liquid_recovered_kg=self.liquid_recovered,liquid_error_kg=self.liquid_loaded-lm-self.liquid_recovered,sample_volume_m3=self.sample['volume_m3'] if self.sample else 0.,particle_volume_error_m3=pv-self.initial_particle_volume if self.sample else 0.,heater_input_j=self.heater_energy,ambient_heat_loss_j=self.ambient_loss,stored_thermal_energy_j=stored,thermal_error_j=self.heater_energy-self.ambient_loss-stored)
 def _result(self):
  if self.phase!='running' and self.operation_id in self.records:return copy.deepcopy(self.records[self.operation_id])
  return dict(operation_id=self.operation_id,phase=self.phase,elapsed_s=self.elapsed,profiles=copy.deepcopy(self.profiles),partial_profile=copy.deepcopy(self.partial),tsi=self.tsi,migration_velocity_m_s=self.velocity,analysis_reasons=list(self.reasons),reference_id=self.result_reference,template_source=self.source,evidence=self.result_evidence or 'No acquired profiles. Optical model and true sample state are separate.',error_code=self.error)
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _cool(self):
  if not self.plant:raise ActionRejected('NOT_READY')
  if self.temperature>self.plant['safe_access_temperature_k']:raise ActionRejected('HOT_SAMPLE')
 def _access(self):
  self._cool()
  if self.sample_lift<.10-1e-12 or not self.lid_open:raise ActionRejected('SAMPLE_ACCESS')
 def _ready(self):
  if not self.plant or not self.power or self.service>1e-9:raise ActionRejected('NOT_READY')
  if not self.sample:raise ActionRejected('NO_SAMPLE')
  if self.sample_lift>1e-9 or self.lid_open:raise ActionRejected('SAMPLE_ACCESS')
  if self.process_fault:raise ActionRejected(self.process_fault)
 def _source_profiles(self,record):
  v=record.get('values',{});z=v.get('height_m');times=v.get('time_s');t=v.get('transmission_fraction');b=v.get('backscatter_fraction')
  if not isinstance(z,list) or len(z)<2 or any(not isinstance(x,(int,float)) or not math.isfinite(x) or x<0 for x in z) or any(v<=u for u,v in zip(z,z[1:])) or not isinstance(times,list) or not times or any(not math.isfinite(x) or x<0 for x in times) or any(v<=u for u,v in zip(times,times[1:])):raise ActionRejected('INVALID_REFERENCE')
  if t is None and b is None:raise ActionRejected('INVALID_REFERENCE')
  for matrix in [t,b]:
   if matrix is not None and (len(matrix)!=len(times) or any(len(row)!=len(z) or any(not math.isfinite(x) or x<0 or x>1 for x in row) for row in matrix)):raise ActionRejected('INVALID_REFERENCE')
  return z,t,b
 def _check_method(self,m):
  if not self.sample:raise ActionRejected('NO_SAMPLE')
  lo=m['scan_start_m'];hi=m['scan_end_m'];z,_,_=self._source_profiles(self.reference)
  if hi<=lo or lo<max(z[0],.002)-1e-12 or hi>min(z[-1],self.sample['height_m']-.001)+1e-12:raise ActionRejected('SCAN_RANGE')
  n=(hi-lo)/SCAN_STEP
  if abs(n-round(n))>1e-7:raise ActionRejected('SCAN_GRID_ALIGNMENT')
  scan=(hi-lo)/self.plant['scan_speed_m_s'];back=(hi-lo)/self.plant['return_speed_m_s'];lead=abs(self.head-lo)/self.plant['return_speed_m_s']
  if m['scan_interval_s']<scan+back-1e-10:raise ActionRejected('SCAN_INTERVAL_TOO_SHORT')
  if m['duration_s']<lead+scan+m['scan_interval_s']-1e-10:raise ActionRejected('INSUFFICIENT_SCANS')
  if self.reference['values'].get('transmission_fraction') is None and m['tsi_channel']=='auto_baseline' and not self.sample['accept_template_condition_transfer']:raise ActionRejected('UNMEASURED_TRANSMISSION_SELECTOR')
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[k]['input_schema'],a)
  if k=='sim.step':self.step(a['dt_s']);return {'time_s':self.time_s}
  before=copy.deepcopy(self.__dict__)
  try:
   r=self._invoke(k,a);validate(CAPS[k]['output_schema'],r)
   if CAPS[k]['kind'] not in ('query','analysis'):self.revision+=1
   return r
  except Exception:self.__dict__.clear();self.__dict__.update(before);raise
 def _invoke(self,k,a):
  if k=='acquisition.result':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='analysis.tsi':return tsi_analysis(a)
  if k=='analysis.two_flux':
   r,t=two_flux(**a);return dict(hemispherical_reflectance=float(r),transmittance=float(t),absorptance=max(0.,float(1-r-t)))
  if k=='reference.query':
   rec=library()['substances'].get(a['reference_id'])
   if rec is None:raise ActionRejected('INVALID_REFERENCE')
   return dict(reference_json=json.dumps(rec,sort_keys=True),template_source=json.dumps(rec['source'],sort_keys=True),evidence=json.dumps(rec['evidence'],sort_keys=True))
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self._finish('cancelled');return {'phase':'cancelled'}
  self._idle()
  if k=='plant.configure':
   if self.plant is not None:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   if a['safe_access_temperature_k']<a['ambient_k']:raise ActionRejected('INVALID_THERMAL_CONFIGURATION')
   self.plant=copy.deepcopy(a);self.temperature=self.temperature_sensor=self.initial_temperature=self.target_temperature=a['ambient_k'];return {'configured':True}
  if k=='sample.load':
   self._access()
   if self.sample:raise ActionRejected('SAMPLE_PRESENT')
   if not a['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   if not math.isclose(a['volume_m3'],math.pi*(.0251/2)**2*a['height_m'],rel_tol=1e-7,abs_tol=1e-15):raise ActionRejected('VESSEL_VOLUME_HEIGHT')
   if a['volume_fraction']>a['maximum_packing_fraction']:raise ActionRejected('PACKING_RANGE')
   if a['volume_fraction']>.1 and not a['accept_concentrated_stokes_proxy']:raise ActionRejected('CONCENTRATED_MODEL_NOT_ACCEPTED')
   velocity=abs(a['particle_density_kg_m3']-a['liquid_density_kg_m3'])*G*a['particle_diameter_m']**2/(18*a['dynamic_viscosity_pa_s']);reynolds=a['liquid_density_kg_m3']*velocity*a['particle_diameter_m']/a['dynamic_viscosity_pa_s']
   if reynolds>.1:raise ActionRejected('STOKES_REGIME')
   rec=library()['substances'].get(a['reference_id'])
   if rec is None or rec.get('runtime_usable') is not True:raise ActionRejected('INVALID_REFERENCE')
   z,t,b=self._source_profiles(rec);c=rec.get('conditions',{})
   if c.get('wavelength_m') is not None and abs(c['wavelength_m']-WAVELENGTH)>1e-12:raise ActionRejected('REFERENCE_WAVELENGTH')
   pairs=[('wavelength_m',WAVELENGTH),('temperature_k',self.temperature),('sample_volume_m3',a['volume_m3']),('cell_path_m',a['optical_path_m']),('sample_height_m',a['height_m']),('volume_fraction',a['volume_fraction'])]
   if any(c.get(key) is None for key,_ in pairs) and not a['accept_unknown_conditions']:raise ActionRejected('UNKNOWN_REFERENCE_CONDITIONS')
   if any(c.get(key) is not None and not math.isclose(c[key],value,rel_tol=1e-6,abs_tol=1e-10) for key,value in pairs) and not a['accept_template_condition_transfer']:raise ActionRejected('REFERENCE_CONDITIONS')
   r0,t0=two_flux(a['particle_absorption_m_inv']+a['carrier_absorption_m_inv'],a['scattering_m_inv'],a['optical_path_m'])
   if b is not None and r0<1e-12 or t is not None and t0<1e-100:raise ActionRejected('OPTICAL_ANCHOR_ZERO')
   self.sample=copy.deepcopy(a);self.reference=copy.deepcopy(rec);self.optical_grid=copy.deepcopy(z);self.baseline_t=copy.deepcopy(t[0]) if t is not None else None;self.baseline_b=copy.deepcopy(b[0]) if b is not None else None;self.phi=[a['volume_fraction']]*self.plant['transport_cells'];dz=a['height_m']/len(self.phi);self.centers=[(i+.5)*dz for i in range(len(self.phi))];self.age=0.;self.diameter=a['particle_diameter_m'];self.initial_particle_volume=a['volume_m3']*a['volume_fraction'];pm,lm=self._masses();self.particle_loaded+=pm;self.liquid_loaded+=lm;self.process_fault=None;return {'loaded':True}
  if k=='sample.remove':
   self._access()
   if not self.sample:raise ActionRejected('NO_SAMPLE')
   pm,lm=self._masses();self.particle_recovered+=pm;self.liquid_recovered+=lm;self.sample=None;self.reference=None;self.phi=[];self.centers=[];self.age=0.;self.diameter=0.;self.initial_particle_volume=0.;self.process_fault=None;return {'removed':True}
  if k=='power.set':
   if a['enabled'] and (not self.plant or self.service>1e-9):raise ActionRejected('NOT_READY')
   self.power=a['enabled'];return a
  if k=='lid.set':
   if a['opened']:
    self._cool()
    if self.sample_lift<.10-1e-12:raise ActionRejected('SAMPLE_ACCESS')
   self.lid_open=a['opened'];return a
  if k=='sample.set_position':
   self._cool()
   if self.lid_open:raise ActionRejected('LID_OPEN')
   self.sample_lift=a['distance_m'];return a
  if k=='service.set':
   if self.power:raise ActionRejected('POWER_INTERLOCK')
   self.service=a['distance_m'];return a
  if k=='thermal.set_temperature':
   if not self.plant:raise ActionRejected('NOT_READY')
   if a['temperature_k']<self.plant['ambient_k']:raise ActionRejected('NO_ACTIVE_COOLING')
   if self.sample and self.reference.get('conditions',{}).get('temperature_k') is not None and abs(a['temperature_k']-self.reference['conditions']['temperature_k'])>self.method['temperature_tolerance_k'] and not self.sample['accept_template_condition_transfer']:raise ActionRejected('REFERENCE_TEMPERATURE')
   self.target_temperature=a['temperature_k'];return a
  if k=='scan.move':self._ready();self.head_target=a['position_m'];return a
  if k=='sensor.calibrate':self.calibration=copy.deepcopy(a);return a
  if k=='method.configure':self._check_method(a);self.method=copy.deepcopy(a);return a
  if k=='acquisition.start':
   self._ready();self._check_method(self.method)
   reference_temperature=self.reference.get('conditions',{}).get('temperature_k')
   if reference_temperature is not None and abs(self.temperature-reference_temperature)>self.method['temperature_tolerance_k'] and not self.sample['accept_template_condition_transfer']:raise ActionRejected('REFERENCE_TEMPERATURE')
   if abs(self._indicated()-self.target_temperature)>self.method['temperature_tolerance_k']:raise ActionRejected('TEMPERATURE_NOT_READY')
   self._counter+=1;self.operation_id=self.instance_id+':'+str(self._counter);self.operation_start=self.time_s;self.phase='running';self.elapsed=0.;self.profiles=[];self.partial=None;self.tsi=None;self.velocity=None;self.reasons=[];self.error=None;self.source=json.dumps(self.reference['source'],sort_keys=True);self.result_reference=self.sample['reference_id'];self.operation_method=copy.deepcopy(self.method);self.operation_sample=copy.deepcopy(self.sample);v=self.reference['values'];self.result_evidence='True source initial height profile anchors only the available optical channel(s): T='+('measured baseline' if v.get('transmission_fraction') is not None else 'unmeasured; engineering KM output')+', BS='+('measured baseline' if v.get('backscatter_fraction') is not None else 'unmeasured; engineering KM output')+'. Subsequent transport/aggregation/heat and optical K/S are explicitly assumed, not original time-series replay or a validated acceleration of source aging. KM hemispherical R/T are separate from135degree detector gain/offset.20um acquisition may interpolate a coarser measured height grid; no added experimental resolution. Original matrices unchanged; source wavelength/temperature/volume are unknown unless documented, and accepting unknown conditions does not verify them. HIPE recipe75% oil has unknown basis; any supplied volume_fraction is an explicit engineering interpretation. Detector saturation is explicitly counted. TSI fixed-baseline channel selection and included endpoint count are declared choices; no inverse Mie/PSD/shelf-life or empirical noise claim.'
   m=self.method;count=round((m['scan_end_m']-m['scan_start_m'])/SCAN_STEP)+1;self.scan_grid=[m['scan_start_m']+i*SCAN_STEP for i in range(count)];self.first_scan_offset=abs(self.head-m['scan_start_m'])/self.plant['return_speed_m_s'];scan_duration=(m['scan_end_m']-m['scan_start_m'])/self.plant['scan_speed_m_s'];self.planned_scans=math.floor((m['duration_s']-self.first_scan_offset-scan_duration+1e-10)/m['scan_interval_s'])+1;self.head_target=m['scan_start_m'];self._event_time=self.time_s+self.first_scan_offset;self._event_kind='scan_start';self.scan_active=False;return {'operation_id':self.operation_id}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _heat(self,dt):
  p=self.plant;c=p['thermal_capacity_j_k'];loss=p['heat_loss_w_k'];gain=p['heater_gain_w_k'];ambient=p['ambient_k'];remaining=dt
  while remaining>1e-14:
   start=self.temperature;duration=remaining
   if not self.power or start>=self.target_temperature:
    rate=loss;equilibrium=ambient;constant=0.
   elif gain*(self.target_temperature-start)>p['heater_max_w']*(1+1e-12):
    rate=loss;equilibrium=ambient+p['heater_max_w']/loss if loss else 0.;constant=p['heater_max_w'];boundary=self.target_temperature-p['heater_max_w']/gain
    if loss and equilibrium>boundary and start<boundary:duration=min(duration,-c/loss*math.log((boundary-equilibrium)/(start-equilibrium)))
    elif not loss and start<boundary:duration=min(duration,c*(boundary-start)/constant)
   else:rate=loss+gain;equilibrium=(loss*ambient+gain*self.target_temperature)/rate;constant=None
   if rate:
    alpha=-math.expm1(-rate*duration/c);end=start+(equilibrium-start)*alpha;integral=(equilibrium-ambient)*duration+(start-equilibrium)*c/rate*alpha
   else:end=start+(constant or 0.)*duration/c;integral=(start-ambient)*duration+(constant or 0.)*duration**2/(2*c)
   qloss=loss*integral;qheat=c*(end-start)+qloss;self.temperature=end;self.ambient_loss+=qloss;self.heater_energy+=qheat;remaining-=duration
   if duration<1e-12:remaining=0.
  self.temperature_sensor=self.temperature+(self.temperature_sensor-self.temperature)*math.exp(-dt/p['temperature_sensor_tau_s'])
 def _transport(self,dt):
  if not self.sample:return
  s=self.sample;self.age+=dt
  if self.process_fault:return
  diameter=s['particle_diameter_m']*(1+s['aggregation_rate_s']*self.age)**(1/3)
  if diameter>1e-3:self.process_fault='PARTICLE_SIZE_MODEL_RANGE';return
  self.diameter=diameter;v0=(s['liquid_density_kg_m3']-s['particle_density_kg_m3'])*G*diameter**2/(18*s['dynamic_viscosity_pa_s']);reynolds=s['liquid_density_kg_m3']*abs(v0)*diameter/s['dynamic_viscosity_pa_s']
  if reynolds>.1:self.process_fault='STOKES_REGIME';return
  dz=s['height_m']/len(self.phi);diffusion=KB*self.temperature/(3*math.pi*s['dynamic_viscosity_pa_s']*diameter)+s['extra_diffusion_m2_s'];steps=max(1,math.ceil(dt*(abs(v0)/dz+2*diffusion/dz**2)/.4));delta=dt/steps;x=np.asarray(self.phi);packing=s['maximum_packing_fraction']
  for _ in range(steps):
   left=x[:-1];right=x[1:];up=left if v0>=0 else right;velocity=v0*np.maximum(0.,1-up)**s['hindered_exponent'];flux=(velocity*up-diffusion*(right-left)/dz)*delta/dz
   outgoing=np.r_[np.maximum(flux,0),0.]+np.r_[0.,np.maximum(-flux,0)];incoming=np.r_[np.maximum(-flux,0),0.]+np.r_[0.,np.maximum(flux,0)];donor=np.minimum(1.,np.divide(x,outgoing,out=np.ones_like(x),where=outgoing>0));recipient=np.minimum(1.,np.divide(np.maximum(0.,packing-x),incoming,out=np.ones_like(x),where=incoming>0));scale=np.where(flux>=0,np.minimum(donor[:-1],recipient[1:]),np.minimum(donor[1:],recipient[:-1]));flux*=scale;x+=np.r_[0.,flux]-np.r_[flux,0.]
  self.phi=list(map(float,x))
 def _advance(self,dt):
  if dt<=0:return
  if self.plant:
   self._heat(dt);self._transport(dt)
   if self.power:
    speed=self.plant['scan_speed_m_s'] if self.scan_active else self.plant['return_speed_m_s'];self.head+=max(-speed*dt,min(speed*dt,self.head_target-self.head))
 def _optical_point(self,z):
  s=self.sample;fraction=float(np.interp(z,self.centers,self.phi));ratio=fraction/s['volume_fraction'];k=s['carrier_absorption_m_inv']+s['particle_absorption_m_inv']*ratio;scattering=s['scattering_m_inv']*ratio*s['particle_diameter_m']/self.diameter;r,t=two_flux(k,scattering,s['optical_path_m']);r0,t0=two_flux(s['particle_absorption_m_inv']+s['carrier_absorption_m_inv'],s['scattering_m_inv'],s['optical_path_m']);grid=self.optical_grid;ts=self.baseline_t;bs=self.baseline_b;raw_t=float(t)*(float(np.interp(z,grid,ts))/float(t0) if ts is not None else 1.);raw_b=float(r)*(float(np.interp(z,grid,bs))/float(r0) if bs is not None else 1.);c=self.calibration;raw_t=raw_t*c['transmission_gain']-c['transmission_zero_fraction'];raw_b=raw_b*c['backscatter_gain']-c['backscatter_zero_fraction'];saturated=int(raw_t<0 or raw_t>1 or raw_b<0 or raw_b>1)
  return min(1.,max(0.,raw_t)),min(1.,max(0.,raw_b)),saturated
 def _front(self,profile):
  if not self.profiles:return None
  key=self.method['front_channel']+'_fraction';baseline=np.array(self.profiles[0][key]);current=np.array(profile[key]);delta=abs(current-baseline)-self.method['front_threshold_fraction'];z=profile['height_m'];cross=[]
  for i in range(len(z)-1):
   if delta[i]==0:cross.append(z[i])
   elif delta[i]*delta[i+1]<0:cross.append(z[i]+(z[i+1]-z[i])*(-delta[i])/(delta[i+1]-delta[i]))
  if len(cross)>1 and 'multiple_front_crossings_first_used' not in self.reasons:self.reasons.append('multiple_front_crossings_first_used')
  return float(cross[0]) if cross else None
 def _event(self):
  if self._event_kind=='scan_start':
   self.scan_active=True;self.head=self.scan_grid[0];self.partial=dict(scan_index=len(self.profiles),start_time_s=self.time_s,end_time_s=self.time_s,height_m=[],point_time_s=[],transmission_fraction=[],backscatter_fraction=[],tsi=None,front_height_m=None,front_time_s=None,saturated_points=0);self._point_index=0
  z=self.scan_grid[self._point_index];self.head=z;t,b,sat=self._optical_point(z);p=self.partial;p['height_m'].append(z);p['point_time_s'].append(self.time_s);p['transmission_fraction'].append(t);p['backscatter_fraction'].append(b);p['saturated_points']+=sat;p['end_time_s']=self.time_s
  if self._point_index+1<len(self.scan_grid):self._point_index+=1;self.head_target=self.scan_grid[self._point_index];self._event_time=self.time_s+SCAN_STEP/self.plant['scan_speed_m_s'];self._event_kind='point';return
  p['front_height_m']=self._front(p);p['front_time_s']=float(np.interp(p['front_height_m'],p['height_m'],p['point_time_s'])) if p['front_height_m'] is not None else None;self.profiles.append(p);self.partial=None;self.scan_active=False
  if len(self.profiles)>1:self.tsi=tsi_analysis(dict(transmission_fraction=[x['transmission_fraction'] for x in self.profiles],backscatter_fraction=[x['backscatter_fraction'] for x in self.profiles],channel=self.method['tsi_channel']))['tsi']
  else:self.tsi=0.
  p['tsi']=self.tsi;fronts=[x for x in self.profiles if x['front_height_m'] is not None]
  if len(fronts)>=2:
   times=np.array([x['front_time_s'] for x in fronts]);heights=np.array([x['front_height_m'] for x in fronts]);center=times-times.mean();self.velocity=float(np.dot(center,heights-heights.mean())/np.dot(center,center))
  if len(self.profiles)>=self.planned_scans:
   if self.velocity is None:self.reasons.append('migration_front_not_bracketed_in_two_scans')
   self._finish('succeeded');return
  self.head_target=self.method['scan_start_m'];self._event_time=self.operation_start+self.first_scan_offset+len(self.profiles)*self.method['scan_interval_s'];self._event_kind='scan_start'
 def _finish(self,phase,error=None):
  self.phase=phase;self.error=error;self.elapsed=self.time_s-self.operation_start;self.scan_active=False;self.head_target=self.head;self._event_time=None;self._event_kind=None
  if self.operation_id:self.records[self.operation_id]=self._result()
 def _tick(self,start,end):
  self.time_s=start
  while self.time_s<end-1e-12:
   next_time=min(end,self._event_time) if self.phase=='running' and self._event_time is not None else end;delta=max(0.,next_time-self.time_s);self._advance(delta);self.time_s=next_time
   if self.phase=='running' and self.process_fault:self._finish('failed',self.process_fault)
   if self.phase=='running':
    self.elapsed=self.time_s-self.operation_start
    if self._event_time is not None and self.time_s>=self._event_time-1e-11:self._event()
  self.time_s=end
 def step(self,dt_s):
  validate(number('s',0,3600),dt_s);total=self._remainder+dt_s;n=int((total+1e-12)/DT);self._remainder=total-n*DT
  for _ in range(n):self._ticks+=1;self._tick((self._ticks-1)*DT,self._ticks*DT)
  self.revision+=n

for key,z in [('lamp.power','工程灯映射：供电'),('lamp.heating','工程灯映射：加热命令活动'),('lamp.scanning','工程灯映射：逐点扫描活动')]:slot(key,BOOL,'1',z,component='controller')
CAPS['power.set']['effects']+=['lamp.power','lamp.heating'];CAPS['thermal.set_temperature']['effects']+=['lamp.heating'];CAPS['acquisition.stop']['effects']+=['lamp.scanning'];PHYSICS_EFFECTS+=['lamp.heating','lamp.scanning']

class MicrotracTurbiscanlabSession:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,instance_id):
  if instance_id!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  meta=export_microtrac_turbiscanlab_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
 def read(self,*,instance_id,state_ids=None):
  self._target(instance_id);h=self._host;values=h.read(self._role);ids=list(values) if state_ids is None else state_ids
  if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
  if any(k not in values for k in ids):raise ActionRejected('ACCESS_DENIED')
  return dict(instance_id=instance_id,status='succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=values[k],unit=SLOTS[k]['unit'],quality='unavailable' if values[k] is None else 'estimated' if SLOTS[k]['origin']=='derived' else 'valid',sample_time_s=h.time_s,sample_window_s=None) for k in ids})
 def invoke(self,*,instance_id,capability_id,arguments,request_id):
  h=self._host
  try:
   self._target(instance_id)
   if not isinstance(capability_id,str) or capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
   if self._role=='agent' and CAPS[capability_id]['visibility']!='agent':raise ActionRejected('ACCESS_DENIED')
   if not isinstance(request_id,str) or not request_id:raise ActionRejected('INVALID_ARGUMENT')
   validate(CAPS[capability_id]['input_schema'],arguments);key=(self._principal,request_id);digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
   if key in h._requests:
    old,result=h._requests[key]
    if old!=digest:raise ActionRejected('REQUEST_CONFLICT')
    return copy.deepcopy(result)
   if capability_id in ('acquisition.stop','acquisition.result') and self._role=='agent' and h.operation_id is not None and h._owners.get(h.operation_id)!=self._principal:raise ActionRejected('ACCESS_DENIED')
   result=h.invoke(capability_id,**arguments);operation=result.get('operation_id') if capability_id=='acquisition.start' else None
   if operation:h._owners[operation]=self._principal
   response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=operation,status='accepted' if operation else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None);h._requests[key]=(digest,copy.deepcopy(response));return response
  except ActionRejected as exc:return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,status='unsupported' if str(exc)=='UNSUPPORTED' else 'rejected',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))
 def write(self,*,instance_id,state_id,value,request_id):
  self._target(instance_id);entry=SLOTS.get(state_id)
  if not entry or not entry['write']:raise ActionRejected('ACCESS_DENIED')
  capability,argument=entry['write'];return self.invoke(instance_id=instance_id,capability_id=capability,arguments={argument:value},request_id=request_id)
 def poll(self,operation_id):
  h=self._host
  if h._owners.get(operation_id)!=self._principal:raise ActionRejected('ACCESS_DENIED')
  result=h._result() if operation_id==h.operation_id and h.phase=='running' else h.records.get(operation_id)
  if result is None:raise ActionRejected('UNKNOWN_OPERATION')
  return dict(operation_id=operation_id,status=result['phase'],result=copy.deepcopy(result))
 def cancel(self,operation_id,request_id):
  self.poll(operation_id)
  if operation_id!=self._host.operation_id:raise ActionRejected('NOT_RUNNING')
  return self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)


def export_microtrac_turbiscanlab_contract_metadata():
 slots={};caps={}
 for k,v in SLOTS.items():slots[k]=dict(meaning_zh=v['meaning'],component_ids=[v['component']],value_schema=copy.deepcopy(v['schema']),unit=v['unit'],reference_conditions='Heights measured upward from internal sample bottom, world z=.151m. Fraction readings are normalized detector signals; KM hemispherical R/T are distinct. Temperature in kelvin. Sample v/v and model coefficients are accepted engineering inputs.',origin=v['origin'],visibility=v['visibility'],access='read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'MicrotracTurbiscanlabSession.read(state_ids=[{k!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description='Host0.1s ticks, internally split at20um point acquisition events; optical profiles carry per-point simulation timestamps. No random empirical noise claim.'),source_ids=['simulation_assumption'])
 errors=['UNKNOWN_CAPABILITY','UNKNOWN_INSTANCE','UNKNOWN_OPERATION','INVALID_ARGUMENT','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','BUSY','NOT_RUNNING','NOT_READY','INITIALIZATION_ONLY','MODEL_NOT_ACCEPTED','INVALID_THERMAL_CONFIGURATION','NO_SAMPLE','SAMPLE_PRESENT','SAMPLE_ACCESS','LID_OPEN','HOT_SAMPLE','POWER_INTERLOCK','NO_ACTIVE_COOLING','TEMPERATURE_NOT_READY','VESSEL_VOLUME_HEIGHT','PACKING_RANGE','CONCENTRATED_MODEL_NOT_ACCEPTED','STOKES_REGIME','PARTICLE_SIZE_MODEL_RANGE','INVALID_REFERENCE','REFERENCE_WAVELENGTH','UNKNOWN_REFERENCE_CONDITIONS','REFERENCE_CONDITIONS','REFERENCE_TEMPERATURE','OPTICAL_ANCHOR_ZERO','SCAN_RANGE','SCAN_GRID_ALIGNMENT','SCAN_INTERVAL_TOO_SHORT','INSUFFICIENT_SCANS','UNMEASURED_TRANSMISSION_SELECTOR','INVALID_PROFILE_SHAPE','CHANNEL_UNAVAILABLE']
 for k,c in CAPS.items():
  un=k in UNSUPPORTED;async_=k=='acquisition.start';caps[k]={key:copy.deepcopy(c[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']};caps[k].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ['scene','robot','hardware'] else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.microtrac_turbiscanlab.MicrotracTurbiscanlabSession.invoke',invocation=f'session.invoke(instance_id="turbiscanlab_01",capability_id={k!r},arguments={c["example_arguments"]!r},request_id="unique")'),preconditions=[] if c['kind'] in ['query','analysis'] or un else [dict(description_zh='实际权限、温区、样品插拔/盖、有限两相与参考范围、扫描时间前提；拒绝原子',state_ids=['power.enabled','plant.configured','sample.present','sample.lift_m','lid.open','acquisition.phase'],enforced_by='runtime',check='atomic invoke and registered Session')],state_effects=[dict(state_id=x,timing='immediate',description_zh='实际命令状态变化') for x in c['effects']],observes=[],model_effects=dict(mode='none',description_zh='管盖/整管/扫描头/维护盖/电源联动另行验证；此处不证明接触或真实光路',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='按计划逐高度逐点采集至少两条完整扫描；失败与取消保留已采集点' if async_ else '命令返回但不推进物理时钟',completion_state_ids=['acquisition.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','sample','scan_head','thermal_block'],partial_failure_zh='热量和重力迁移不回滚；取消停止扫描头并冻结已采集结果，样品仍按宿主时间演化'),errors=[dict(code=x,meaning_zh=x,side_effects='partial' if async_ and x in ['STOKES_REGIME','PARTICLE_SIZE_MODEL_RANGE'] else 'none') for x in errors],verification=[],limitations=['真实初始光学高度profile锚定与未知物性/动力学分开','KM半球漫反射不是原厂135°探测效率或Mie反演','TSI来自实际扫描的累计变化；不是独立随机分数'],source_ids=['manufacturer_specs','simulation_assumption'])
  if async_ or k in ['thermal.set_temperature','scan.move','power.set']:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='有限两相守恒迁移、热量与逐点扫描推进') for x in PHYSICS_EFFECTS if x!='clock.time_s']
 mechanism=dict(component_ids=['sample_cell','scan_head','led_880nm','transmission_detector','backscatter_detector','thermal_block','controller'],state_ids=list(slots),meaning_zh='有限封闭乳液/悬浮液守恒输运、两通量工程光学和真实时序扫描',implementation='lumped',equations=[
  'Signed Stokes velocity v0=(rho_liquid-rho_particle)*g*d^2/(18*dynamic_viscosity); upward positive. Explicit hindering v=v0*(1-phi)^n; initial Reynolds<=0.1; concentrated use requires independent assumption acceptance.',
  'D=kB*T/(3*pi*dynamic_viscosity*d)+explicit_extra_diffusion. No-flux finite-volume transport of phi with upwind advection and centered diffusion. Donor availability and receiving packing-capacity flux limiters preserve both phases; no post-update mass-dropping clipping.',
  'Closed sample volume constant; particle mass=rho_p*V*mean(phi); carrier mass=rho_l*V*(1-mean(phi)). Agglomerate size d=d0*(1+k*age)^(1/3) is explicit assumed kinetics and does not create particle mass.',
  'Effective thermal node C*dT/dt=clamp(Kh*(target-T),0,Pmax)-G*(T-Tambient); exact exponential segments through saturation boundary. Heater energy-ambient loss=stored thermal energy. No cooling below ambient; no evaporation or sample-insertion enthalpy claim.',
  'Finite-slab two-flux equations dI+/dx=-(K+S)I+ + S I-; dI-/dx=-S I+ +(K+S)I- with I+(0)=1,I-(L)=0. b=sqrt(K*(K+2S)), T=b/[b*cosh(bL)+(K+S)*sinh(bL)], R=S*sinh(bL)/[b*cosh(bL)+(K+S)*sinh(bL)]. Stable exp(-bL) form; K=0 limit T=1/(1+SL),R=SL/(1+SL).',
  'K=Kcarrier+Kparticle*phi/phi0; S=S0*(phi/phi0)*(d0/d), explicit optical closure. Initial source T and/or BS profile calibrates separate detector gain versus intrinsic T or R. Missing channels use explicitly engineering output and remain null in original reference. No forced measured T+BS energy sum.',
  'Programmed duration is the maximum acquisition window, containing only complete scans; operation completes after the last whole scan that fits.Acquisition excludes the bottom2mm and top1mm as explicit standard-tube engineering edge margins; full0..55mm manual head travel remains available.20um scan grid with exact event timing dz/scan_speed, finite return travel and distinct scan starts; interval shorter than scan+return rejected. Original40um profile interpolation does not create additional experimental resolution.',
  'TSI_n=100/Nh * sum_i sum_z |signal_i(z)-signal_(i-1)(z)| for fraction signals. auto_baseline freezes first-scan T<0.002 mask: BS there, T elsewhere. Nh is actual included point count; baseline mask and endpoint count are explicitly adopted conventions.',
  'Migration front = first observed crossing of |signal-current minus baseline| through explicit threshold; absence is null. OLS of at least two front positions against interpolated actual per-point front acquisition times yields signed velocity; no perfect-truth front extraction.'
 ],parameters={},source_ids=['manufacturer_specs','manufacturer_tsi','manufacturer_sedimentation','manufacturer_creaming','kubelka_munk_hecht1976','stokes_einstein1905','simulation_assumption'],limitations=['Supplied viscosity is dynamic Pa*s; OEM application-note kinematic wording is dimensionally inconsistent with its Stokes expression.','Original HIPE75% oil recipe basis unspecified; suppliedphi and phase densities/diameter/viscosity are engineering inputs, not measured formulation truth.','Source40um and15day-sampled BS matrices remain original. Only firstprofile is a detector reference anchor; simulated30s dynamics is not source aging or a validated accelerated stability test.','10nm–1mm and0.0001–95%v/v are manufacturer applicability ranges, not guaranteed PSD inversion, Stokes validity or achieved accuracy.','V=pi*(25.1mm/2)^2*fill_height is enforced for the represented standard tube.2–4mL accessory is not represented; low standard-tube filling is an engineering configuration, not verified small-adapter performance.','20um is acquisition spacing, not particle-size accuracy; one sample,880nm and ambient..333.15K implemented.','KM assumes an effective homogeneous diffuse slab at each height; no exact Mie, angular radiative transfer, dependent scattering, non-Newtonian HIPE rheology or independently calibrated detector geometry.','Temperature sensor is a first-order numerical response using segment-end node temperature, not an exact coupled sensor solution.','Thermal capacity is an explicit effective node; container insertion/extraction enthalpy, convection and evaporation are not resolved.','Three physical green lamps receive declared engineering power/heating/scanning mappings because OEM icon semantics are unknown.'])
 spec=importlib.util.spec_from_file_location('_turbiscan_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()});ann.annotate_export(SLUG,caps,slots,{'dispersion_scanning':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'dispersion_scanning':mechanism})

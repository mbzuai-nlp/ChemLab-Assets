"""CHEM-123 low-mass DVS: measured equilibrium references, explicit finite-process assumptions."""
from pathlib import Path
import copy,json,math,importlib.util
import numpy as np
PROTOCOL_VERSION='1.0.0';SLUG='surface_dvsresolution';CATALOG_ID='CHEM-123';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch03/t4_measure'/SLUG
R=8.31446261815324;PREF=101325.;TREF=273.15;NREF=PREF/(R*TREF);MW=.0180153;MN=.0280134;DT=.1
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
 p=ASSET/'dvs_isotherm_library.json';return json.loads(p.read_text()) if p.exists() else {'substances':{}}
def saturation_pressure(temperature_k):
 if not 273.16<=temperature_k<=647.096:raise ActionRejected('SATURATION_RANGE')
 tau=1-temperature_k/647.096
 return 22.064e6*math.exp(647.096/temperature_k*sum(a*tau**b for a,b in zip([-7.85951783,1.84408259,-11.7866497,22.6807411,-15.9618719,1.80122502],[1,1.5,3,3.5,4,7.5])))
def curve(record,branch,reversible=False):
 v=record['values'];prefix='desorption_' if branch=='desorption' and 'desorption_relative_humidity' in v else ''
 if branch=='desorption' and not prefix and not reversible:raise ActionRejected('DESORPTION_REFERENCE_REQUIRED')
 x=v.get(prefix+'relative_humidity');y=v.get(prefix+'uptake_kg_kg')
 if not isinstance(x,list) or not isinstance(y,list) or len(x)!=len(y) or len(x)<2 or any(not math.isfinite(a) or not 0<=a<=1 for a in x) or any(not math.isfinite(a) or a<0 for a in y) or any(b<=a for a,b in zip(x,x[1:])):raise ActionRejected('INVALID_REFERENCE')
 return x,y
def interpolate(record,rh,branch,reversible=False):
 x,y=curve(record,branch,reversible)
 if rh<x[0]-1e-12 or rh>x[-1]+1e-12:raise ActionRejected('REFERENCE_RH_RANGE')
 return float(np.interp(rh,x,y))
def slope(times,values):
 t=np.asarray(times,dtype=float);v=np.asarray(values,dtype=float);x=t-t.mean();den=float(x@x)
 if den<=0:raise ActionRejected('INVALID_TIME_SERIES')
 return float(x@(v-v.mean())/den)
PLANT_EXAMPLE=dict(accept_engineering=True,ambient_k=298.15,pressure_pa=101325.,chamber_volume_m3=1e-6,thermal_capacity_j_k=10.,controller_w_k=30.,heater_limit_w=100.,cooler_limit_w=100.,heat_loss_w_k=.1,gas_cp_j_mol_k=29.,water_latent_j_kg=2.44e6,temperature_tau_s=.3,humidity_tau_s=.3,balance_tau_s=.3,balance_gain=1.,balance_offset_kg=0.,pan_mass_kg=5e-5,max_flow_ref_m3_s=1e-5,reference_temperature_tolerance_k=.2)
PLANT=obj({'accept_engineering':BOOL,'ambient_k':number('K',278.15,333.15),'pressure_pa':number('Pa',80000,120000),'chamber_volume_m3':number('m^3',1e-8,1e-3),'thermal_capacity_j_k':number('J/K',1,10000),'controller_w_k':number('W/K',.1,1000),'heater_limit_w':number('W',1,10000),'cooler_limit_w':number('W',1,10000),'heat_loss_w_k':number('W/K',0,100),'gas_cp_j_mol_k':number('J/(mol*K)',1,100),'water_latent_j_kg':number('J/kg',0,3e6),'temperature_tau_s':number('s',.01,100),'humidity_tau_s':number('s',.01,100),'balance_tau_s':number('s',.01,100),'balance_gain':number('1',.1,10),'balance_offset_kg':number('kg',-1e-4,1e-4),'pan_mass_kg':number('kg',0,5e-4),'max_flow_ref_m3_s':number('m^3/s',1e-8,1e-4),'reference_temperature_tolerance_k':number('K',.01,.5)})
SAMPLE_EXAMPLE=dict(reference_id='bam_p109_water_nitrogen_298p15k',form='powder',dry_mass_kg=1e-6,initial_water_kg=0.,accept_engineering_kinetics=True,accept_reversible_reference=False,sorption_tau_s=.5,desorption_tau_s=1.,sorption_heat_j_kg=1e5)
SAMPLE=obj({'reference_id':TEXT,'form':enum(['powder','film']),'dry_mass_kg':number('kg',1e-9,1e-3),'initial_water_kg':number('kg',0,1e-3),'accept_engineering_kinetics':BOOL,'accept_reversible_reference':BOOL,'sorption_tau_s':number('s',.001,1e7),'desorption_tau_s':number('s',.001,1e7),'sorption_heat_j_kg':number('J/kg',0,3e6)})
STOCK_EXAMPLE=dict(solvent='water',mass_kg=.001)
STOCK=obj({'solvent':TEXT,'mass_kg':number('kg',1e-15,1.)})
GAS_EXAMPLE=dict(amount_mol=.01);GAS=obj({'amount_mol':number('mol',1e-12,1000)})
METHOD_EXAMPLE=dict(temperature_k=298.15,relative_humidity_steps=[.1,.4],cycles=1,minimum_dwell_s=2.,maximum_dwell_s=10.,window_s=2.,dm_dt_kg_s=1e-10,sample_interval_s=.2)
METHOD=obj({'temperature_k':number('K',278.15,333.15),'relative_humidity_steps':array(number('1',0,.98),1,100),'cycles':integer(1,100),'minimum_dwell_s':number('s',.1,86400),'maximum_dwell_s':number('s',.1,86400),'window_s':number('s',.2,3600),'dm_dt_kg_s':number('kg/s',0,1e-5),'sample_interval_s':number('s',.1,60)})
ROW=obj({'time_s':number('s'),'temperature_k':number('K'),'relative_humidity':number('1'),'mass_kg':number('kg'),'target_relative_humidity':number('1'),'cycle':integer(),'step':integer(),'dm_dt_kg_s':nullable(number('kg/s'))})
POINT=obj({'cycle':integer(),'step':integer(),'target_relative_humidity':number('1'),'observed_relative_humidity':number('1'),'mass_kg':number('kg'),'dwell_s':number('s'),'dm_dt_kg_s':nullable(number('kg/s')),'equilibrated':BOOL,'termination':enum(['equilibrium','maximum_dwell'])})
RESULT=obj({'operation_id':nullable(TEXT),'phase':PHASE,'elapsed_s':number('s'),'rows':array(ROW,0,1000000),'points':array(POINT,0,10000),'all_equilibrated':BOOL,'template_source':nullable(TEXT),'reference_id':nullable(TEXT),'evidence':TEXT,'error_code':nullable(TEXT)})
LEDGER=obj({k:number(u) for k,u in [('water_loaded_kg','kg'),('water_present_kg','kg'),('water_exhaust_waste_kg','kg'),('water_error_kg','kg'),('drygas_loaded_mol','mol'),('drygas_present_mol','mol'),('drygas_exhaust_waste_mol','mol'),('drygas_error_mol','mol'),('mass_error_kg','kg'),('heat_input_j','J'),('heat_out_j','J'),('phase_and_sorption_heat_j','J'),('stored_heat_j','J'),('heat_error_j','J')]})
CAPS={};SLOTS={}
def cap(k,i,o,c,z,kind='device_command',visibility='agent',example=None,effects=None):CAPS[k]=dict(input_schema=i,output_schema=o,component_ids=c,function_zh=z,kind=kind,visibility=visibility,example_arguments=example or {},effects=effects or [])
def slot(k,s,u,z,origin='derived',visibility='agent',component='controller',write=None):SLOTS[k]=dict(schema=s,unit=u,meaning=z,origin=origin,visibility=visibility,component=component,write=write)
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller','balance','chamber'],'显式输入有效热学、传感器、容量与气体参考态系数','configuration','evaluator',PLANT_EXAMPLE,['plant.configured','thermal.target_k','truth.temperature_k','truth.chamber_dry_mol'])
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_pan'],'降下样品仓后装粉末/薄膜；实测等温线驱动平衡，传质参数显式假设','configuration','evaluator',SAMPLE_EXAMPLE,['sample.present','truth.sample_water_kg','truth.sample_dry_kg'])
cap('sample.remove',obj({}),obj({'removed':BOOL}),['sample_pan'],'关闭气流并降仓取样，剩余含水量入回收账本','configuration','evaluator',effects=['sample.present','truth.sample_water_kg','truth.sample_dry_kg','truth.equilibrium_water_kg'])
cap('stock.load',STOCK,obj({'loaded':BOOL}),['reservoir'],'宿主在瓶盖开启时装入有限水源，有机溶剂配置未实现','configuration','evaluator',STOCK_EXAMPLE,['truth.reservoir_water_kg'])
cap('stock.unload',obj({}),obj({'unloaded':BOOL}),['reservoir'],'回收剩余液体入账本','configuration','evaluator',effects=['truth.reservoir_water_kg'])
cap('gas.load',GAS,obj({'loaded':BOOL}),['gas_manifold'],'宿主登记有限干氮气库存','configuration','evaluator',GAS_EXAMPLE,['truth.drygas_mol'])
cap('gas.unload',obj({}),obj({'unloaded':BOOL}),['gas_manifold'],'关闭流路后回收剩余氮气库存','configuration','evaluator',effects=['truth.drygas_mol'])
for k,arg,schema,c,z,ex,effects in [('power.set','enabled',BOOL,'power_switch','电源',True,['power.enabled','power.angle_rad','lamp.power','lamp.running','lamp.fault','thermal.indicated_k','humidity.indicated','mass.indicated_kg','gas.enabled']),('door.set','opened',BOOL,'door','外门开闭人工代理',True,['door.angle_rad']),('chamber.set_position','distance_m',number('m',0,.135),'chamber','左样品仓人工向下脱开',.135,['chamber.lower_m']),('reference.set_position','distance_m',number('m',0,.135),'reference_chamber','右参考仓维护姿态；无双样流程',.135,['reference.lower_m']),('reservoir.open','distance_m',number('m',0,.045),'reservoir_cap','水瓶盖取下位移',.045,['reservoir.cap_m']),('service.set','distance_m',number('m',0,.10),'service_cover','断电服务盖取下',.10,['service.slide_m'])]:cap(k,obj({arg:schema}),obj({arg:schema}),[c],z,example={arg:ex},effects=effects)
cap('thermal.set_temperature',obj({'temperature_k':number('K',278.15,358.15)}),obj({'temperature_k':number('K')}),['chamber'],'5–85°C干态温控；>60°C不提供湿度运行',example={'temperature_k':298.15},effects=['thermal.target_k'])
cap('humidity.set',obj({'relative_humidity':number('1',0,.98)}),obj({'relative_humidity':number('1')}),['gas_manifold','humidity_sensor'],'5–60°C范围水相对湿度目标，不直接改变真RH',example={'relative_humidity':.4},effects=['humidity.target','acquisition.branch'])
cap('vapor.set_pressure',obj({'pressure_pa':number('Pa',0,20000)}),obj({'relative_humidity':number('1')}),['gas_manifold'],'水蒸气分压按目标温度IAPWS饱和压换算RH',example={'pressure_pa':1000.},effects=['humidity.target','acquisition.branch'])
cap('gas.set_flow',obj({'reference_m3_s':number('m^3/s',0,1e-4)}),obj({'reference_m3_s':number('m^3/s')}),['gas_manifold'],'氮气水蒸气混合物参考总流量，范围由显式工程配置限定',example={'reference_m3_s':100e-6/60},effects=['flow.target_ref_m3_s'])
cap('gas.open',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['gas_manifold'],'关闭仓体和水瓶后开关有限气路',example={'enabled':True},effects=['gas.enabled'])
cap('balance.tare',obj({}),obj({'tared':BOOL}),['balance'],'以当前观测值去皮；不声明原厂精度认证',effects=['mass.indicated_kg','balance.tared'])
CAL=obj({'reference_masses_kg':array(number('kg',0,1e-3),2,2),'observed_masses_kg':array(number('kg',-1e-3,1e-3),2,2)})
cap('balance.calibrate',CAL,obj({'gain':number('1'),'offset_kg':number('kg')}),['balance'],'外部标准砝码及独立观测的两点标定','configuration',example=dict(reference_masses_kg=[0.,1e-4],observed_masses_kg=[0.,1e-4]),effects=['balance.calibrated','balance.tared','mass.indicated_kg'])
cap('method.configure',METHOD,METHOD,['controller','chamber','humidity_sensor'],'RH阶跃、循环、最小最大驻留及滑窗dm/dt，不将最大驻留称为平衡','configuration',example=METHOD_EXAMPLE)
cap('acquisition.start',obj({}),obj({'operation_id':TEXT}),['controller','balance','gas_manifold'],'按实测等温线支持范围执行有限水吸脱附',effects=['acquisition.phase','acquisition.elapsed_s','acquisition.step_elapsed_s','acquisition.points','acquisition.equilibria','acquisition.cycle','acquisition.step','acquisition.branch','acquisition.dm_dt_kg_s','humidity.target','thermal.target_k','lamp.running','lamp.fault'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['controller'],'取消冻结观测但保留转移质量和热量；关闭流路',effects=['acquisition.phase','gas.enabled','lamp.running'])
cap('acquisition.result',obj({}),RESULT,['balance'],'纯查询当前或历史冻结的质量时间与等温线端点','query')
cap('reference.query',obj({'reference_id':TEXT}),obj({'reference_json':TEXT,'template_source':TEXT,'evidence':TEXT}),['controller'],'查询原温度和干基实测水等温线','query',example={'reference_id':SAMPLE_EXAMPLE['reference_id']})
cap('analysis.diffusion',obj({'times_s':array(number('s',0),4,100000),'masses_kg':array(number('kg'),4,100000),'initial_mass_kg':number('kg'),'equilibrium_mass_kg':number('kg'),'half_thickness_m':number('m',1e-9,.1),'accept_slab_approximation':BOOL}),obj({'diffusivity_m2_s':number('m^2/s',0),'slope_s':number('1/s'),'intercept':number('1'),'residual':number('1'),'points_used':integer()}),['controller'],'独立外部数据的平板Fick长时扩散拟合；不读取材料真值','analysis',example={'times_s':[1.,2.,3.,4.],'masses_kg':[.6e-6,.7e-6,.8e-6,.9e-6],'initial_mass_kg':0.,'equilibrium_mass_kg':1e-6,'half_thickness_m':1e-4,'accept_slab_approximation':True})
HYS=obj({'adsorption_rh':array(number('1',0,1),2,10000),'adsorption_uptake_kg_kg':array(number('1',0),2,10000),'desorption_rh':array(number('1',0,1),2,10000),'desorption_uptake_kg_kg':array(number('1',0),2,10000)})
cap('analysis.hysteresis',HYS,obj({'signed_area':number('1'),'minimum_rh':number('1'),'maximum_rh':number('1')}),['controller'],'外部两支路共同RH区间上的滞后积分','analysis',example={'adsorption_rh':[.1,.5,.9],'adsorption_uptake_kg_kg':[.01,.1,.2],'desorption_rh':[.1,.5,.9],'desorption_uptake_kg_kg':[.02,.13,.22]})
cap('ledger.read',obj({}),LEDGER,['controller'],'宿主水/干气/总质量及有效热量账本','query','evaluator')
cap('sim.step',obj({'dt_s':number('s',0,3600)}),obj({'time_s':number('s')}),['controller'],'唯一宿主物理时钟','simulation','evaluator',{'dt_s':.1})
UNSUPPORTED=['solvent.organic','humidity.high_temperature','noise.empirical','stability.certification_24h','calibration.oem_certificate','robot.manipulate','hardware.connect','sample.reference_channel_measure']
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'缺少配置或实测验证：'+k)
for k,z,c in [('plant.configured','工程过程参数就绪','controller'),('sample.present','粉末或薄膜样品在位','sample_pan'),('power.enabled','供电','power_switch'),('gas.enabled','气路开启','gas_manifold'),('balance.tared','已调用观测去皮','balance'),('balance.calibrated','已输入独立标准标定','balance'),('lamp.power','工程供电灯代理','lamp_power'),('lamp.running','工程运行灯代理','lamp_running'),('lamp.fault','工程故障灯代理','lamp_fault')]:slot(k,BOOL,'1',z,component=c)
for k,u,hi,z,c,w in [('door.angle_rad','rad',1.75,'外门开放角度','door',None),('chamber.lower_m','m',.135,'样品仓向下脱开位移','chamber',('chamber.set_position','distance_m')),('reference.lower_m','m',.135,'参考仓维护位移','reference_chamber',('reference.set_position','distance_m')),('reservoir.cap_m','m',.045,'水瓶盖取下位移','reservoir_cap',('reservoir.open','distance_m')),('service.slide_m','m',.10,'服务后盖取下位移','service_cover',('service.set','distance_m')),('power.angle_rad','rad',.28,'电源开关代理角','power_switch',None)]:slot(k,number(u,0,hi),u,z,'scene',component=c,write=w)
for k,u,z,c in [('thermal.target_k','K','控制温度目标','chamber'),('humidity.target','1','水相对湿度目标','humidity_sensor'),('flow.target_ref_m3_s','m^3/s','参考温压下总气流目标','gas_manifold')]:slot(k,number(u),u,z,'setpoint',component=c)
for k,u,z,c in [('thermal.indicated_k','K','一阶温度观测；未验证±0.05K稳定性','temperature_sensor'),('humidity.indicated','1','一阶湿度观测；不等于过程真RH','humidity_sensor'),('mass.indicated_kg','kg','一阶天平及1e-11kg量化观测，不添加虚假实测噪声','balance')]:slot(k,nullable(number(u)),u,z,component=c)
for k,u,z in [('truth.temperature_k','K','有效热节点真温'),('truth.relative_humidity','1','有限气相水的分压/饱和压比'),('truth.drygas_mol','mol','有限干氮气库存'),('truth.reservoir_water_kg','kg','水瓶液体库存'),('truth.chamber_dry_mol','mol','仓内氮气'),('truth.chamber_water_kg','kg','仓内水蒸气'),('truth.sample_water_kg','kg','样品吸附水'),('truth.sample_dry_kg','kg','样品干基质量')]:slot(k,number(u,0),u,z,'process_truth','evaluator')
SLOTS['truth.relative_humidity']['schema']=nullable(number('1',0))
slot('truth.equilibrium_water_kg',nullable(number('kg',0)),'kg','当前原温度/RH/支路支持下的实测平衡含水量；域外null','process_truth','evaluator')
slot('clock.time_s',number('s',0),'s','宿主时间');slot('acquisition.phase',PHASE,'1','运行阶段');slot('acquisition.elapsed_s',number('s',0),'s','当前操作时间');slot('acquisition.step_elapsed_s',number('s',0),'s','当前RH驻留时间');slot('acquisition.dm_dt_kg_s',nullable(number('kg/s')),'kg/s','当前完整滑窗观测质量斜率');slot('acquisition.branch',enum(['adsorption','desorption']),'1','目标变化所选支路')
for k,z in [('acquisition.points','质量观测点数'),('acquisition.equilibria','已结束RH步数'),('acquisition.cycle','当前循环零基索引'),('acquisition.step','当前RH零基索引')]:slot(k,integer(),'1',z)
PHYSICS_EFFECTS=['clock.time_s','thermal.indicated_k','humidity.indicated','mass.indicated_kg','truth.temperature_k','truth.relative_humidity','truth.drygas_mol','truth.reservoir_water_kg','truth.chamber_dry_mol','truth.chamber_water_kg','truth.sample_water_kg','truth.equilibrium_water_kg','acquisition.phase','acquisition.elapsed_s','acquisition.step_elapsed_s','acquisition.dm_dt_kg_s','acquisition.points','acquisition.equilibria','acquisition.cycle','acquisition.step','acquisition.branch','humidity.target','gas.enabled','lamp.running','lamp.fault']
CAPS['sim.step']['effects']=PHYSICS_EFFECTS
CAPS['gas.set_flow']['input_schema']['properties']['reference_m3_s']['x-reference-conditions']='Simulation total gas volume at273.15K and101325Pa; flow range explicitly supplied engineering input'
class SurfaceDvsresolutionRuntime:
 def __init__(self,instance_id='dvsresolution_01'):
  self.instance_id=instance_id;self.plant=None;self.sample=None;self.reference=None;self.method=copy.deepcopy(METHOD_EXAMPLE);self.power=False;self.door=True;self.chamber=.135;self.reference_lower=0.;self.cap=.045;self.service=0.;self.gas_enabled=False;self.temperature=298.15;self.target=298.15;self.temp_sensor=298.15;self.rh_target=0.;self.rh_sensor=0.;self.flow_target=100e-6/60;self.dry_stock=0.;self.liquid_stock=0.;self.cell_dry=0.;self.cell_water=0.;self.water=0.;self.dry_loaded=0.;self.dry_exhaust=0.;self.water_loaded=0.;self.water_exhaust=0.;self.solid_loaded=0.;self.solid_waste=0.;self.heat_in=0.;self.heat_out=0.;self.exchange_heat=0.;self.raw_mass=0.;self.mass_sensor=0.;self.gain=1.;self.offset=0.;self.tare=0.;self.change_reference=0.;self.tared=False;self.calibrated=False;self.eq_water=None;self.phase='idle';self.branch='adsorption';self.elapsed=0.;self.step_elapsed=0.;self.cycle=0;self.step_index=0;self.rows=[];self.points=[];self.window=[];self.dm_dt=None;self.error=None;self.source=None;self.result_reference=None;self.time_s=0.;self._ticks=0;self._remainder=0.;self.revision=0;self.operation_id=None;self._counter=0;self.records={};self._requests={};self._owners={};self._principals={'agent':'agent','evaluator':'evaluator'}
 def register_principal(self,principal_id,visibility='agent'):
  if not isinstance(principal_id,str) or not principal_id or principal_id in self._principals or visibility not in ('agent','debug','evaluator'):raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='agent'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return SurfaceDvsresolutionSession(self,principal_id)
 def describe(self):return export_surface_dvsresolution_contract_metadata()
 def _rh(self):
  if not self.plant:return 0.
  if not 273.16<=self.temperature<=647.096:return None
  return self.cell_water/MW*R*self.temperature/(self.plant['chamber_volume_m3']*saturation_pressure(self.temperature))
 def _mass(self):return (self.plant['pan_mass_kg'] if self.plant else 0.)+(self.sample['dry_mass_kg'] if self.sample else 0.)+self.water
 def _indicated_mass(self):return round((self.gain*self.mass_sensor+self.offset-self.tare)/1e-11)*1e-11
 def read(self,role='evaluator'):
  d={'plant.configured':self.plant is not None,'sample.present':self.sample is not None,'power.enabled':self.power,'power.angle_rad':.28 if self.power else 0.,'gas.enabled':self.gas_enabled,'balance.tared':self.tared,'balance.calibrated':self.calibrated,'lamp.power':self.power,'lamp.running':self.power and self.phase=='running','lamp.fault':self.power and self.phase=='failed','door.angle_rad':1.75 if self.door else 0.,'chamber.lower_m':self.chamber,'reference.lower_m':self.reference_lower,'reservoir.cap_m':self.cap,'service.slide_m':self.service,'thermal.target_k':self.target,'humidity.target':self.rh_target,'flow.target_ref_m3_s':self.flow_target,'thermal.indicated_k':self.temp_sensor if self.power else None,'humidity.indicated':self.rh_sensor if self.power else None,'mass.indicated_kg':self._indicated_mass() if self.power else None,'truth.temperature_k':self.temperature,'truth.relative_humidity':self._rh(),'truth.drygas_mol':self.dry_stock,'truth.reservoir_water_kg':self.liquid_stock,'truth.chamber_dry_mol':self.cell_dry,'truth.chamber_water_kg':self.cell_water,'truth.sample_water_kg':self.water,'truth.sample_dry_kg':self.sample['dry_mass_kg'] if self.sample else 0.,'truth.equilibrium_water_kg':self.eq_water,'clock.time_s':self.time_s,'acquisition.phase':self.phase,'acquisition.elapsed_s':self.elapsed,'acquisition.step_elapsed_s':self.step_elapsed,'acquisition.dm_dt_kg_s':self.dm_dt,'acquisition.branch':self.branch,'acquisition.points':len(self.rows),'acquisition.equilibria':len(self.points),'acquisition.cycle':self.cycle,'acquisition.step':self.step_index}
  return {k:copy.deepcopy(v) for k,v in d.items() if role!='agent' or SLOTS[k]['visibility']=='agent'}
 def _ledger(self):
  water_present=self.liquid_stock+self.cell_water+self.water;dry_present=self.dry_stock+self.cell_dry;we=self.water_loaded-water_present-self.water_exhaust;de=self.dry_loaded-dry_present-self.dry_exhaust;se=self.solid_loaded-(self.sample['dry_mass_kg'] if self.sample else 0.)-self.solid_waste;stored=self.plant['thermal_capacity_j_k']*(self.temperature-self.plant['ambient_k']) if self.plant else 0.
  return dict(water_loaded_kg=self.water_loaded,water_present_kg=water_present,water_exhaust_waste_kg=self.water_exhaust,water_error_kg=we,drygas_loaded_mol=self.dry_loaded,drygas_present_mol=dry_present,drygas_exhaust_waste_mol=self.dry_exhaust,drygas_error_mol=de,mass_error_kg=we+de*MN+se,heat_input_j=self.heat_in,heat_out_j=self.heat_out,phase_and_sorption_heat_j=self.exchange_heat,stored_heat_j=stored,heat_error_j=self.heat_in-self.heat_out+self.exchange_heat-stored)
 def _result(self):
  if self.phase!='running' and self.operation_id in self.records:return copy.deepcopy(self.records[self.operation_id])
  return dict(operation_id=self.operation_id,phase=self.phase,elapsed_s=self.elapsed,rows=copy.deepcopy(self.rows),points=copy.deepcopy(self.points),all_equilibrated=bool(self.points) and all(p['equilibrated'] for p in self.points),template_source=self.source,reference_id=self.result_reference,evidence='Experimental source isotherm at original temperature/dry basis; finite transport, local temperature tolerance, thermal coefficients and ideal filtered/quantized balance are explicit engineering assumptions; reversible desorption only if explicitly accepted; no empirical noise or long-term stability claim.',error_code=self.error)
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _ready(self):
  if not self.plant or not self.power or self.service>1e-9:raise ActionRejected('NOT_READY')
 def _sealed(self):
  if self.door or max(self.chamber,self.reference_lower,self.cap)>1e-9:raise ActionRejected('CHAMBER_OPEN')
 def _access(self):
  if not self.door or self.chamber<.12 or self.gas_enabled or self.temperature>313.15:raise ActionRejected('SAMPLE_ACCESS')
 def _humidity(self,rh,temperature):
  if rh>0 and temperature>333.15:raise ActionRejected('UNSUPPORTED_HIGH_TEMPERATURE_HUMIDITY')
  if not 0<=rh<=.98 or rh*saturation_pressure(temperature)>=self.plant['pressure_pa']:raise ActionRejected('HUMIDITY_RANGE')
 def _method_check(self,method):
  if not self.sample:raise ActionRejected('NO_SAMPLE')
  if method['minimum_dwell_s']>method['maximum_dwell_s'] or method['window_s']>method['maximum_dwell_s'] or method['sample_interval_s']>method['window_s']/2:raise ActionRejected('INVALID_DWELL')
  known=self.reference.get('conditions',{}).get('temperature_k')
  if known is None or abs(known-method['temperature_k'])>1e-6:raise ActionRejected('REFERENCE_TEMPERATURE')
  previous=self.rh_target
  for _ in range(method['cycles']):
   for rh in method['relative_humidity_steps']:
    self._humidity(rh,method['temperature_k']);branch='desorption' if rh<previous-1e-12 else 'adsorption';interpolate(self.reference,rh,branch,self.sample['accept_reversible_reference']);previous=rh
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[k]['input_schema'],a)
  if k=='sim.step':self.step(a['dt_s']);return {'time_s':self.time_s}
  before=copy.deepcopy(self.__dict__)
  try:
   result=self._invoke(k,a);validate(CAPS[k]['output_schema'],result)
   if CAPS[k]['kind'] not in ('query','analysis'):self.revision+=1
   return result
  except Exception:self.__dict__.clear();self.__dict__.update(before);raise
 def _invoke(self,k,a):
  if k=='acquisition.result':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='reference.query':
   record=library()['substances'].get(a['reference_id'])
   if record is None:raise ActionRejected('INVALID_REFERENCE')
   return dict(reference_json=json.dumps(record,sort_keys=True),template_source=json.dumps(record['source'],sort_keys=True),evidence=json.dumps(record['evidence'],sort_keys=True))
  if k=='analysis.diffusion':
   t=np.asarray(a['times_s']);m=np.asarray(a['masses_kg']);change=a['equilibrium_mass_kg']-a['initial_mass_kg']
   if not a['accept_slab_approximation']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   if len(t)!=len(m) or change==0 or np.any(np.diff(t)<=0):raise ActionRejected('INVALID_TIME_SERIES')
   fraction=(m-a['initial_mass_kg'])/change;take=(fraction>=.5)&(fraction<=.95)
   if np.count_nonzero(take)<3:raise ActionRejected('INSUFFICIENT_DIFFUSION_WINDOW')
   x=t[take];y=np.log1p(-fraction[take]);rate=slope(x,y);intercept=float(y.mean()-rate*x.mean())
   if rate>=0:raise ActionRejected('INVALID_DIFFUSION_RESPONSE')
   return dict(diffusivity_m2_s=-rate*4*a['half_thickness_m']**2/math.pi**2,slope_s=rate,intercept=intercept,residual=float(np.sqrt(np.mean((y-(rate*x+intercept))**2))),points_used=len(x))
  if k=='analysis.hysteresis':
   x=a['adsorption_rh'];y=a['adsorption_uptake_kg_kg'];xx=a['desorption_rh'];yy=a['desorption_uptake_kg_kg']
   if len(x)!=len(y) or len(xx)!=len(yy) or any(b<=c for c,b in zip(x,x[1:])) or any(b<=c for c,b in zip(xx,xx[1:])):raise ActionRejected('INVALID_TIME_SERIES')
   lo=max(x[0],xx[0]);hi=min(x[-1],xx[-1])
   if lo>=hi:raise ActionRejected('NO_OVERLAP')
   grid=sorted({lo,hi}|{t for t in x+xx if lo<t<hi});delta=np.interp(grid,xx,yy)-np.interp(grid,x,y);area=sum((grid[i+1]-grid[i])*(delta[i+1]+delta[i])/2 for i in range(len(grid)-1));return dict(signed_area=float(area),minimum_rh=lo,maximum_rh=hi)
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self.gas_enabled=False;self._finish('cancelled');return {'phase':self.phase}
  self._idle()
  if k=='plant.configure':
   if self.plant is not None:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   self.plant=copy.deepcopy(a);self.temperature=self.temp_sensor=self.target=a['ambient_k'];self.cell_dry=a['pressure_pa']*a['chamber_volume_m3']/(R*a['ambient_k']);self.dry_loaded=self.cell_dry;self.raw_mass=self.mass_sensor=a['pan_mass_kg']*a['balance_gain']+a['balance_offset_kg'];self.tare=self.mass_sensor;self.change_reference=a['pan_mass_kg'];return {'configured':True}
  if k=='power.set':
   if a['enabled'] and (not self.plant or self.service>1e-9):raise ActionRejected('NOT_READY')
   self.power=a['enabled']
   if not self.power:self.gas_enabled=False
   return a
  if k=='service.set':
   if self.power:raise ActionRejected('POWER_INTERLOCK')
   self.service=a['distance_m'];return a
  if k=='door.set':
   if self.gas_enabled:raise ActionRejected('GAS_ACCESS')
   if not a['opened'] and max(self.chamber,self.reference_lower)>1e-9:raise ActionRejected('CHAMBER_POSITION')
   self.door=a['opened'];return a
  if k in ('chamber.set_position','reference.set_position'):
   if not self.door or self.gas_enabled or self.temperature>313.15:raise ActionRejected('SAMPLE_ACCESS')
   if k=='chamber.set_position':self.chamber=a['distance_m']
   else:self.reference_lower=a['distance_m']
   return a
  if k=='reservoir.open':
   if self.gas_enabled:raise ActionRejected('GAS_ACCESS')
   self.cap=a['distance_m'];return a
  if k=='sample.load':
   self._access()
   if not self.plant or self.sample:raise ActionRejected('SAMPLE_PRESENT')
   if not a['accept_engineering_kinetics']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   reference=library()['substances'].get(a['reference_id'])
   if reference is None:raise ActionRejected('INVALID_REFERENCE')
   if reference.get('runtime_usable') is not True:raise ActionRejected('REFERENCE_NOT_RUNTIME_USABLE')
   curve(reference,'adsorption')
   if a['dry_mass_kg']+a['initial_water_kg']+self.plant['pan_mass_kg']>1e-3:raise ActionRejected('BALANCE_CAPACITY')
   self.sample=copy.deepcopy(a);self.reference=copy.deepcopy(reference);self.water=a['initial_water_kg'];self.water_loaded+=self.water;self.solid_loaded+=a['dry_mass_kg'];return {'loaded':True}
  if k=='sample.remove':
   self._access()
   if not self.sample:raise ActionRejected('NO_SAMPLE')
   self.water_exhaust+=self.water;self.solid_waste+=self.sample['dry_mass_kg'];self.sample=None;self.reference=None;self.water=0.;self.eq_water=None;return {'removed':True}
  if k in ('stock.load','stock.unload'):
   if not self.plant or self.gas_enabled or self.cap<.03:raise ActionRejected('RESERVOIR_ACCESS')
   if k=='stock.unload':self.water_exhaust+=self.liquid_stock;self.liquid_stock=0.;return {'unloaded':True}
   if a['solvent']!='water':raise ActionRejected('UNSUPPORTED_SOLVENT')
   if self.liquid_stock>0:raise ActionRejected('STOCK_PRESENT')
   self.liquid_stock=a['mass_kg'];self.water_loaded+=self.liquid_stock;return {'loaded':True}
  if k in ('gas.load','gas.unload'):
   if not self.plant or self.gas_enabled:raise ActionRejected('GAS_ACCESS')
   if k=='gas.unload':self.dry_exhaust+=self.dry_stock;self.dry_stock=0.;return {'unloaded':True}
   if self.dry_stock>0:raise ActionRejected('STOCK_PRESENT')
   self.dry_stock=a['amount_mol'];self.dry_loaded+=self.dry_stock;return {'loaded':True}
  if k=='thermal.set_temperature':
   if not self.plant:raise ActionRejected('NOT_READY')
   self._humidity(self.rh_target,a['temperature_k']);self.target=a['temperature_k'];return a
  if k in ('humidity.set','vapor.set_pressure'):
   if not self.plant:raise ActionRejected('NOT_READY')
   rh=a['relative_humidity'] if k=='humidity.set' else a['pressure_pa']/saturation_pressure(self.target);self._humidity(rh,self.target);self.branch='desorption' if rh<self.rh_target-1e-12 else 'adsorption';self.rh_target=rh;return {'relative_humidity':rh}
  if k=='gas.set_flow':
   if not self.plant or a['reference_m3_s']>self.plant['max_flow_ref_m3_s']:raise ActionRejected('FLOW_RANGE')
   self.flow_target=a['reference_m3_s'];return a
  if k=='gas.open':
   if a['enabled']:
    self._ready();self._sealed();self._humidity(self.rh_target,self.target)
    if self.flow_target<=0 or self.dry_stock<=0 or self.rh_target>0 and self.liquid_stock<=0:raise ActionRejected('FINITE_INVENTORY')
   self.gas_enabled=a['enabled'];return a
  if k=='balance.tare':
   self._ready();self.tare=self.gain*self.mass_sensor+self.offset;self.change_reference=self._mass();self.tared=True;return {'tared':True}
  if k=='balance.calibrate':
   x=a['observed_masses_kg'];y=a['reference_masses_kg']
   if x[1]<=x[0] or y[1]<=y[0]:raise ActionRejected('INVALID_CALIBRATION')
   self.gain=(y[1]-y[0])/(x[1]-x[0]);self.offset=y[0]-self.gain*x[0];self.tared=False;self.calibrated=True;return dict(gain=self.gain,offset_kg=self.offset)
  if k=='method.configure':self._method_check(a);self.method=copy.deepcopy(a);return a
  if k=='acquisition.start':
   self._ready();self._sealed();self._method_check(self.method)
   if not self.gas_enabled or self.dry_stock<=0 or any(x>0 for x in self.method['relative_humidity_steps']) and self.liquid_stock<=0:raise ActionRejected('FINITE_INVENTORY')
   self._counter+=1;self.operation_id=self.instance_id+':'+str(self._counter);self.phase='running';self.elapsed=0.;self.step_elapsed=0.;self.cycle=0;self.step_index=0;self.rows=[];self.points=[];self.window=[];self.dm_dt=None;self.error=None;self.target=self.method['temperature_k'];self.source=json.dumps(self.reference['source'],sort_keys=True);self.result_reference=self.sample['reference_id'];self._set_rh_step();return {'operation_id':self.operation_id}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _set_rh_step(self):
  rh=self.method['relative_humidity_steps'][self.step_index];self.branch='desorption' if rh<self.rh_target-1e-12 else 'adsorption';self.rh_target=rh;self.step_elapsed=0.;self.window=[];self.dm_dt=None
 def _finish(self,phase,error=None):
  self.phase=phase;self.error=error
  if self.operation_id:self.records[self.operation_id]=self._result()
 def _observe(self):self.rows.append(dict(time_s=self.time_s,temperature_k=self.temp_sensor,relative_humidity=self.rh_sensor,mass_kg=self._indicated_mass(),target_relative_humidity=self.rh_target,cycle=self.cycle,step=self.step_index,dm_dt_kg_s=self.dm_dt))
 def _vent(self):
  total=self.cell_dry+self.cell_water/MW;maximum=self.plant['pressure_pa']*self.plant['chamber_volume_m3']/(R*self.temperature);vent=max(0.,total-maximum)
  if vent:
   fraction=vent/total;dn=self.cell_dry*fraction;dw=self.cell_water*fraction;self.cell_dry-=dn;self.cell_water-=dw;self.dry_exhaust+=dn;self.water_exhaust+=dw
 def _tick(self):
  if not self.plant:return
  p=self.plant;failure=None;feed_mol=0.;evaporated=0.;exchanged=0.;self.eq_water=None
  if self.gas_enabled and self.power:
   if not 278.15<=self.temperature<=358.15:failure='THERMAL_LIMIT';self.gas_enabled=False
   else:
    y=self.rh_target*saturation_pressure(self.temperature)/p['pressure_pa'];desired=self.flow_target*NREF*DT;dn=desired*(1-y);dw=desired*y*MW;water_fraction=self.liquid_stock/dw if dw else 1.;dry_fraction=self.dry_stock/dn if dn else 1.;energy_fraction=p['thermal_capacity_j_k']*max(0.,self.temperature-273.16)/(p['water_latent_j_kg']*dw) if dw and p['water_latent_j_kg'] else 1.;factor=min(1.,dry_fraction,water_fraction,energy_fraction);dn*=factor;dw*=factor;self.dry_stock=max(0.,self.dry_stock-dn);self.liquid_stock=max(0.,self.liquid_stock-dw);self.cell_dry+=dn;self.cell_water+=dw;feed_mol=dn+dw/MW;evaporated=dw
    if factor<1.:failure='WATER_EXHAUSTED' if water_fraction<=min(dry_fraction,energy_fraction) else 'DRYGAS_EXHAUSTED' if dry_fraction<=energy_fraction else 'THERMAL_FLOW_LIMIT';self.gas_enabled=False
  self._vent()
  if self.sample and abs(self.temperature-self.reference['conditions']['temperature_k'])<=p['reference_temperature_tolerance_k']:
   try:self.eq_water=self.sample['dry_mass_kg']*interpolate(self.reference,self._rh(),self.branch,self.sample['accept_reversible_reference'])
   except ActionRejected as error:
    if str(error) not in ('REFERENCE_RH_RANGE','DESORPTION_REFERENCE_REQUIRED'):raise
   if self.eq_water is not None:
    # Solve vapor/sample water together; fixed-dt explicit targets oscillate for stiff kinetics.
    x,y=curve(self.reference,self.branch,self.sample['accept_reversible_reference']);total_water=self.water+self.cell_water;old=self.water;rh_per_kg=R*self.temperature/(MW*p['chamber_volume_m3']*saturation_pressure(self.temperature));tau=self.sample['sorption_tau_s'] if self.eq_water>=old else self.sample['desorption_tau_s'];alpha=-math.expm1(-DT/tau);energy_available=max(0.,p['thermal_capacity_j_k']*(self.temperature-273.16)-p['water_latent_j_kg']*evaporated);desorption_limit=energy_available/self.sample['sorption_heat_j_kg'] if self.sample['sorption_heat_j_kg'] else math.inf
    lo=max(0.,total_water-x[-1]/rh_per_kg,old-desorption_limit);hi=min(total_water,total_water-x[0]/rh_per_kg)
    def residual(water):return water-(1-alpha)*old-alpha*self.sample['dry_mass_kg']*float(np.interp(rh_per_kg*(total_water-water),x,y))
    if residual(lo)>=0:new=lo
    elif residual(hi)<=0:new=hi
    else:
     for _ in range(50):
      mid=(lo+hi)/2
      if residual(mid)>0:hi=mid
      else:lo=mid
     new=(lo+hi)/2
    exchanged=new-old;self.water=new;self.cell_water=total_water-new;self.eq_water=self.sample['dry_mass_kg']*float(np.interp(self._rh(),x,y))
  # Bounded implicit proportional thermal controller; latent and sorption heat are explicit inputs.
  capacity=p['thermal_capacity_j_k'];gain=p['controller_w_k'] if self.power else 0.;loss_gain=p['heat_loss_w_k']+feed_mol*p['gas_cp_j_mol_k']/DT;ambient=p['ambient_k'];heat=-p['water_latent_j_kg']*evaporated+(self.sample['sorption_heat_j_kg']*exchanged if self.sample else 0.);candidate=(capacity*self.temperature+DT*(gain*self.target+loss_gain*ambient)+heat)/(capacity+DT*(gain+loss_gain));requested=gain*(self.target-candidate);heater=max(-p['cooler_limit_w'],min(p['heater_limit_w'],requested))
  if heater!=requested:candidate=(capacity*self.temperature+DT*(heater+loss_gain*ambient)+heat)/(capacity+DT*loss_gain)
  self.heat_in+=heater*DT;self.heat_out+=loss_gain*(candidate-ambient)*DT;self.exchange_heat+=heat;self.temperature=candidate
  if not 273.16<=self.temperature<=363.15:failure='THERMAL_LIMIT'
  if self.temperature<=0:raise ActionRejected('THERMAL_MODEL_RANGE')
  # Physical water beyond saturation condenses to the accounted exhaust trap; no supersaturated RH readout.
  bounded_temperature=min(647.096,max(273.16,self.temperature));maximum_water=saturation_pressure(bounded_temperature)*p['chamber_volume_m3']/(R*self.temperature)*MW;condensed=max(0.,self.cell_water-maximum_water)
  if condensed:self.cell_water-=condensed;self.water_exhaust+=condensed;latent=p['water_latent_j_kg']*condensed;self.exchange_heat+=latent;self.temperature+=latent/capacity
  if not 273.16<=self.temperature<=363.15:failure='THERMAL_LIMIT'
  self._vent()
  self.temp_sensor=self.temperature+(self.temp_sensor-self.temperature)*math.exp(-DT/p['temperature_tau_s']);rh=self._rh();self.rh_sensor=rh+(self.rh_sensor-rh)*math.exp(-DT/p['humidity_tau_s']) if rh is not None else self.rh_sensor;self.raw_mass=self._mass()*p['balance_gain']+p['balance_offset_kg'];self.mass_sensor=self.raw_mass+(self.mass_sensor-self.raw_mass)*math.exp(-DT/p['balance_tau_s'])
  if self._mass()>1e-3+1e-15:failure='BALANCE_OVERLOAD'
  if abs(self._mass()-self.change_reference)>1.5e-4+1e-15:failure='BALANCE_CHANGE_RANGE'
  if self.phase!='running':return
  self.elapsed+=DT;self.step_elapsed+=DT;self.window.append((self.time_s,self._indicated_mass()));cut=self.time_s-self.method['window_s'];self.window=[p for p in self.window if p[0]>=cut-DT/2];self.dm_dt=slope([p[0] for p in self.window],[p[1] for p in self.window]) if len(self.window)>2 and self.window[-1][0]-self.window[0][0]>=self.method['window_s']-1e-9 else None
  every=max(1,round(self.method['sample_interval_s']/DT))
  if round(self.elapsed/DT)%every==0 or failure:self._observe()
  if failure:self.gas_enabled=False;self._finish('failed',failure);return
  equilibrated=self.step_elapsed>=self.method['minimum_dwell_s']-1e-9 and self.dm_dt is not None and abs(self.dm_dt)<=self.method['dm_dt_kg_s'] and abs(self.rh_sensor-self.rh_target)<=.005 and abs(self.temp_sensor-self.target)<=.1 and self.eq_water is not None
  expired=self.step_elapsed>=self.method['maximum_dwell_s']-1e-9
  if equilibrated or expired:
   self.points.append(dict(cycle=self.cycle,step=self.step_index,target_relative_humidity=self.rh_target,observed_relative_humidity=self.rh_sensor,mass_kg=self._indicated_mass(),dwell_s=self.step_elapsed,dm_dt_kg_s=self.dm_dt,equilibrated=bool(equilibrated),termination='equilibrium' if equilibrated else 'maximum_dwell'))
   if self.step_index+1<len(self.method['relative_humidity_steps']):self.step_index+=1;self._set_rh_step()
   elif self.cycle+1<self.method['cycles']:self.cycle+=1;self.step_index=0;self._set_rh_step()
   else:
    if not self.rows or self.rows[-1]['time_s']!=self.time_s:self._observe()
    self._finish('succeeded')
 def step(self,dt_s):
  validate(number('s',0,3600),dt_s);total=self._remainder+dt_s;n=int((total+1e-12)/DT);self._remainder=total-n*DT
  for _ in range(n):self._ticks+=1;self.time_s=self._ticks*DT;self._tick()
  self.revision+=n
class SurfaceDvsresolutionSession:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,instance_id):
  if instance_id!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  meta=export_surface_dvsresolution_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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
def export_surface_dvsresolution_contract_metadata():
 slots={};caps={}
 for k,v in SLOTS.items():slots[k]=dict(meaning_zh=v['meaning'],component_ids=[v['component']],value_schema=copy.deepcopy(v['schema']),unit=v['unit'],reference_conditions='SI; total gas volume reference273.15K/101325Pa assumed; water RH=pv/IAPWS92_psat(T); uptake drybasis kg/kg; dm/dt kg/s',origin=v['origin'],visibility=v['visibility'],access='read_write' if v['write'] else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'SurfaceDvsresolutionSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=v['write'][0],argument=v['write'][1],fixed_arguments={}) if v['write'] else None,sampling=dict(mode='instant',time_domain='simulation',window_description='Fixed0.1s host ticks; mass resolution1e-11kg with explicitly assumed balance lag and noempiricalnoise; full sliding-window dm/dt'),source_ids=['simulation_assumption'])
 errors=['UNKNOWN_CAPABILITY','UNKNOWN_INSTANCE','UNKNOWN_OPERATION','INVALID_ARGUMENT','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','BUSY','NOT_READY','NOT_RUNNING','INITIALIZATION_ONLY','MODEL_NOT_ACCEPTED','SAMPLE_ACCESS','SAMPLE_PRESENT','NO_SAMPLE','CHAMBER_OPEN','CHAMBER_POSITION','INVALID_REFERENCE','REFERENCE_NOT_RUNTIME_USABLE','REFERENCE_RH_RANGE','REFERENCE_TEMPERATURE','DESORPTION_REFERENCE_REQUIRED','RESERVOIR_ACCESS','UNSUPPORTED_SOLVENT','UNSUPPORTED_HIGH_TEMPERATURE_HUMIDITY','HUMIDITY_RANGE','FINITE_INVENTORY','STOCK_PRESENT','FLOW_RANGE','GAS_ACCESS','POWER_INTERLOCK','INVALID_CALIBRATION','INVALID_DWELL','INVALID_TIME_SERIES','INSUFFICIENT_DIFFUSION_WINDOW','INVALID_DIFFUSION_RESPONSE','NO_OVERLAP','WATER_EXHAUSTED','DRYGAS_EXHAUSTED','THERMAL_LIMIT','THERMAL_FLOW_LIMIT','BALANCE_OVERLOAD','BALANCE_CAPACITY','BALANCE_CHANGE_RANGE']
 for k,c in CAPS.items():
  un=k in UNSUPPORTED;async_=k=='acquisition.start';caps[k]={key:copy.deepcopy(c[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']};caps[k].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ['scene','robot','hardware'] else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.surface_dvsresolution.SurfaceDvsresolutionSession.invoke',invocation=f'session.invoke(instance_id="dvsresolution_01",capability_id={k!r},arguments={c["example_arguments"]!r},request_id="unique")'),preconditions=[] if c['kind'] in ['query','analysis'] or un else [dict(description_zh='实际参数、权限、库存、炉温、水气路及访问检查；命令拒绝原子，非原厂安全认证',state_ids=['power.enabled','plant.configured','acquisition.phase'],enforced_by='runtime',check='atomic invoke and registered Session')],state_effects=[dict(state_id=x,timing='immediate',description_zh='实际命令变化') for x in c['effects']],observes=[],model_effects=dict(mode='none',description_zh='场景映射需独立验证',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='所有RH驻留步和循环完成或明确失败/取消；max_dwell不标平衡' if async_ else '命令完成但不推进物理时钟',completion_state_ids=['acquisition.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','chamber','drygas','water','sample','balance'],partial_failure_zh='保留已消耗/排出气体、表面变化、热量与记录；终态结果冻结，后续真实热弛豫仍由宿主推进'),errors=[dict(code=x,meaning_zh=x,side_effects='partial' if async_ and x in ['WATER_EXHAUSTED','DRYGAS_EXHAUSTED','THERMAL_LIMIT','THERMAL_FLOW_LIMIT','BALANCE_OVERLOAD','BALANCE_CHANGE_RANGE'] else 'none') for x in errors],verification=[],limitations=['原厂噪声/故障/误差/压力控制数据未知','传质/热量/传感器系数是显式工程输入；等温线为公开实测参考'],source_ids=['manufacturer_specs','simulation_assumption'])
  if async_ or k in ['gas.open','thermal.set_temperature','humidity.set','vapor.set_pressure','power.set']:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='宿主步进热学、有限气流、反应与观测') for x in PHYSICS_EFFECTS if x!='clock.time_s']
 mech=dict(component_ids=['controller','balance','sample_pan','chamber','gas_manifold','reservoir','temperature_sensor','humidity_sensor'],state_ids=list(slots),meaning_zh='有限水/氮气混合、真实等温线驱动吸脱附、质量观测和平衡终止',implementation='lumped',equations=['IAPWS SR1-86(1992) water psat:ln(p/22.064MPa)=(647.096K/T)*sum(ai*tau^ei);RH=pwater/psat;gasreference273.15K101325Pa is simulation convention','Source equilibrium water=dry_mass*piecewise_linear(measured_drybasis_uptake,RH) only within original RH and targettemperature; noextrapolation orcross-temperature materialresponse','Coupled implicit vapor/sample transfer: m_new=(1-alpha)*m_old+alpha*meq(RH_new),alpha=1-exp(-dt/tau); solve within measured RH support, finite vapor/samplewater and available thermal energy; tau is explicit engineering input','Finite drygas and liquid inventories supply requested molar mixture; depleted source proportionally clips both streams then terminates; excess chamber moles vent at referencepressure','Water conservation:loaded=reservoir+chambervapor+samplewater+exhaust/waste;drygas and drysample accounted separately','Bounded implicit thermalcontroller:C*dT=heater-loss-feedgas_sensible-latent_vaporization+sorption_heat+condensation_heat;phaseheats effective supplied coefficients; condensate goes to accounted trap','Filtered balance mass plus calibration/tare, quantized1e-11kg; gross load<=1e-3kg, changefromtarebaseline<=1.5e-4kg; noempiricalnoisestability claim','dm/dt=least-squares slope of complete sliding window of observedquantized mass;equilibrium requires minimumdwell,RH/Tsettling and slopecriterion;maximumdwell records nonequilibrium outcome','Cycles reuse explicit RHsteps and reset each window;completed/cancelled/failed result snapshots survive subsequentphysical relaxation','External slab diffusion long-time ln(1-F)=intercept-pi^2*D*t/(4*halflength^2);only0.5<=F<=0.95; independentexternalinputs,not automatedmaterialtruth','External hysteresis integrates desorptionminusadsorption on measured commonRHrange only'],parameters={},source_ids=['manufacturer_specs','simulation_assumption','iapws_saturation'],limitations=['No certified±0.05K6h or<=3e-10kgRMS24h result;simulation duration is actualhostseconds only','Water RH0–0.98 only at278.15–333.15K;drytemperaturecontrol278.15–358.15K;organicvapors/high-temperatureRH explicitly unsupported','Referencecurve fields retain original temperature/material/measurementbasis; unavailable desorption requires explicit reversible-equilibrium assumption and is not measuredhysteresis','Localtemperaturetolerance around source curve is evaluatoracceptedengineering approximation; targettemperature cannot change the materialcurve','Outside measured RH or localtemperature support equilibriumwater=null and exchange is paused ratherthan extrapolated; dwell cannot be called equilibrated','Waterphase/thermal model is lumped, notfullgasline condensation, reservoir bath or dual-reference-pan driftcancellation','Sampleaccess313.15K and thermalfloors/trips are simulationguards,notOEMsafetycertification','Shared low-level Runtime object is a trustedhost interface; registered Session filters agenttruth and operationowner; nofilesystem/processsandboxisolation claim','No robot/hardware, organic-solvent module, certifiednoise/fault, panunhooktrajectory or active second-reference-channel implementation'])
 spec=importlib.util.spec_from_file_location('_dvs_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()});ann.annotate_export(SLUG,caps,slots,{'dynamic_vapor_sorption':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'dynamic_vapor_sorption':mech})

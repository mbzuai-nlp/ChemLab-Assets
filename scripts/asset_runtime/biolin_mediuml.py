"""CHEM-127 finite Langmuir film, empirical isotherms and Wilhelmy/LB engineering process."""
from pathlib import Path
import copy,json,math,importlib.util
import numpy as np
PROTOCOL_VERSION='1.0.0';SLUG='biolin_mediuml';CATALOG_ID='CHEM-127';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch03/t4_measure'/SLUG
DT=.1;NA=6.02214076e23;G=9.80665;LENGTH=.364;WIDTH=.075;AREA_MAX=.0273;AREA_MIN=AREA_MAX/10.8
BARRIER_MAX=(LENGTH-AREA_MIN/WIDTH)/2;BARRIER_MIN_SPEED=.0001/60;BARRIER_MAX_SPEED=.270/60
DIPPER_MIN_SPEED=.0001/60;DIPPER_MAX_SPEED=.108/60;DIPPER_MAX=.125;SURFACE_POSITION=.063
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
 p=ASSET/'langmuir_library.json';return json.loads(p.read_text()) if p.exists() else {'substances':{}}

EOS_FORM='softplus_condensation_eos_v1'
def is_scalar_model(reference):
 """Scalar-only literature record whose runtime EOS is a derived engineering model, not a measured array."""
 return isinstance(reference,dict) and reference.get('runtime_model') is not None and not reference.get('values',{}).get('molecular_area_m2')

def model_parameters(reference):
 """Validated (A0,s,w,A_min,A_max) in SI for a softplus_condensation_eos_v1 record."""
 m=reference.get('runtime_model') or {}
 try:
  p=m['parameters_si'];d=reference['runtime_domain'];v=[float(p['limiting_area_m2']),float(p['slope_n_m_per_m2']),float(p['transition_width_m2']),float(d['minimum_molecular_area_m2']),float(d['maximum_molecular_area_m2'])]
 except (KeyError,TypeError,ValueError):raise ActionRejected('INVALID_REFERENCE')
 if not reference.get('runtime_usable') or m.get('form')!=EOS_FORM or not all(math.isfinite(x) and x>0 for x in v) or not v[3]<v[0]<v[4]:raise ActionRejected('INVALID_REFERENCE')
 return tuple(v)

def model_eos(a0,s,w,x):
 """pi=s*w*ln(1+exp((A0-A)/w)); Cs^-1=-A dpi/dA=A*s*logistic((A0-A)/w). SI units."""
 z=(a0-x)/w;soft=max(z,0.)+math.log1p(math.exp(-abs(z)));logi=1/(1+math.exp(-z)) if z>=0 else math.exp(z)/(1+math.exp(z))
 return s*w*soft,x*s*logi

def curve(reference):
 if is_scalar_model(reference):
  a0,s,w,lo,hi=model_parameters(reference);a=np.linspace(lo,hi,400);return a,np.array([model_eos(a0,s,w,x)[0] for x in a])
 try:
  a=np.array(reference['values']['molecular_area_m2'],float);p=np.array(reference['values']['surface_pressure_n_m'],float)
 except (KeyError,ValueError,TypeError):raise ActionRejected('INVALID_REFERENCE')
 if not reference.get('runtime_usable') or len(a)<3 or a.shape!=p.shape or not np.all(np.isfinite(a)) or not np.all(np.isfinite(p)) or np.any(a<=0):raise ActionRejected('INVALID_REFERENCE')
 if np.all(np.diff(a)<0):a=a[::-1];p=p[::-1]
 elif not np.all(np.diff(a)>0):raise ActionRejected('INVALID_REFERENCE')
 return a,p

def eos(reference,molecular_area_m2,allow_dilute=False):
 """Measured arrays: unsmoothed piecewise-linear source compression branch (derivative may be negative/noisy).
 Scalar-only records: derived softplus EOS within the published lift-off/collapse window."""
 if is_scalar_model(reference):
  a0,s,w,lo,hi=model_parameters(reference);x=molecular_area_m2
  if not math.isfinite(x) or x<lo*(1-1e-12):raise ActionRejected('EOS_AREA_RANGE')
  if x>hi*(1+1e-12):
   if allow_dilute:return 0.,0.
   raise ActionRejected('EOS_AREA_RANGE')
  return model_eos(a0,s,w,x)
 a,p=curve(reference);x=molecular_area_m2
 if not math.isfinite(x) or x<max(a[0],reference.get('runtime_domain',{}).get('minimum_molecular_area_m2',a[0]))*(1-1e-12):raise ActionRejected('EOS_AREA_RANGE')
 if x>a[-1]*(1+1e-12):
  if allow_dilute:return 0.,0.
  raise ActionRejected('EOS_AREA_RANGE')
 x=min(max(x,a[0]),a[-1]);i=min(len(a)-2,max(0,int(np.searchsorted(a,x,side='right')-1)));slope=(p[i+1]-p[i])/(a[i+1]-a[i]);return float(p[i]+(x-a[i])*slope),float(-x*slope)

PLANT_EXAMPLE=dict(accept_engineering=True,temperature_k=294.15,pressure_sensor_tau_s=.2,force_gain=1.,force_offset_n=0.,force_drift_n_s=0.,plate_width_m=.01952,plate_thickness_m=.0001,plate_mass_kg=.00005,plate_immersion_m=.0015,plate_contact_cosine=1.,controller_gain_m3_n_s=.2,leak_rate_s=0.,solvent_evaporation_rate_s=2.,solvent_density_kg_m3=1480.,solvent_latent_j_kg=250000.,isothermal_bath=True,safe_access_temperature_k=313.15)
PLANT=obj({'accept_engineering':BOOL,'temperature_k':number('K',278.15,333.15),'pressure_sensor_tau_s':number('s',.001,100),'force_gain':number('1',.01,100),'force_offset_n':number('N',-.01,.01),'force_drift_n_s':number('N/s',-1e-4,1e-4),'plate_width_m':number('m',.001,.02),'plate_thickness_m':number('m',1e-5,.002),'plate_mass_kg':number('kg',1e-7,.001),'plate_immersion_m':number('m',0,.003),'plate_contact_cosine':number('1',.01,1),'controller_gain_m3_n_s':number('m^3/(N*s)',1e-5,10),'leak_rate_s':number('1/s',0,100),'solvent_evaporation_rate_s':number('1/s',.001,100),'solvent_density_kg_m3':number('kg/m^3',100,3000),'solvent_latent_j_kg':number('J/kg',0,3e6),'isothermal_bath':BOOL,'safe_access_temperature_k':number('K',278.15,333.15)})
SUBPHASE_EXAMPLE=dict(volume_m3=176e-6,density_kg_m3=997.,surface_tension_n_m=.071972205,identity='PBS',ph=7.4,buffer_concentration_mol_m3=10.,accept_engineering=True)
SUBPHASE=obj({'volume_m3':number('m^3',176e-6,176e-6),'density_kg_m3':number('kg/m^3',100,3000),'surface_tension_n_m':number('N/m',.005,.3),'identity':TEXT,'ph':number('1',0,14),'buffer_concentration_mol_m3':number('mol/m^3',0,1000),'accept_engineering':BOOL})
SAMPLE_EXAMPLE=dict(reference_id='statins2020_dmpc_control',volume_m3=100e-9,concentration_mol_m3=1.,molar_mass_kg_mol=.677933,accept_engineering=True,accept_unknown_conditions=True,accept_condition_transfer=True,accept_quasistatic_branch_reuse=True,allow_dilute_extension=False)
SAMPLE=obj({'reference_id':TEXT,'volume_m3':number('m^3',1e-15,1e-5),'concentration_mol_m3':number('mol/m^3',1e-6,1000),'molar_mass_kg_mol':number('kg/mol',.001,1e6),'accept_engineering':BOOL,'accept_unknown_conditions':BOOL,'accept_condition_transfer':BOOL,'accept_quasistatic_branch_reuse':BOOL,'allow_dilute_extension':BOOL})
SOLUTION_EXAMPLE=SAMPLE_EXAMPLE
SUBSTRATE_EXAMPLE=dict(width_m=.052,height_m=.056,thickness_m=.003,faces=2,wetting='hydrophilic',deposition_mode='first_favorable',transfer_efficiency=.9,accept_engineering=True)
SUBSTRATE=obj({'width_m':number('m',.001,.052),'height_m':number('m',.001,.056),'thickness_m':number('m',.0001,.003),'faces':integer(1,2),'wetting':enum(['hydrophilic','hydrophobic']),'deposition_mode':enum(['first_favorable','y_type','upstroke_only','downstroke_only']),'transfer_efficiency':number('1',0,1),'accept_engineering':BOOL})
METHOD_EXAMPLE=dict(mode='compression',target_area_m2=.016,barrier_speed_m_s=.0045,target_pressure_n_m=.020,pressure_tolerance_n_m=.0005,duration_s=40.,sample_interval_s=.1,dipper_positions_m=[.073,.053],dipping_speed_m_s=.0018,maximum_solvent_fraction=.001,blank_leak_area_rate_m2_s=0.)
METHOD=obj({'mode':enum(['compression','expansion','hold','deposition']),'target_area_m2':number('m^2',AREA_MIN,AREA_MAX),'barrier_speed_m_s':number('m/s',BARRIER_MIN_SPEED,BARRIER_MAX_SPEED),'target_pressure_n_m':number('N/m',0,.3),'pressure_tolerance_n_m':number('N/m',1e-7,.01),'duration_s':number('s',.1,3600),'sample_interval_s':number('s',.1,60),'dipper_positions_m':array(number('m',0,DIPPER_MAX),1,20),'dipping_speed_m_s':number('m/s',DIPPER_MIN_SPEED,DIPPER_MAX_SPEED),'maximum_solvent_fraction':number('1',0,.01),'blank_leak_area_rate_m2_s':number('m^2/s',0,.001)})
CALIBRATION=obj({'force_gain_correction':number('1',.001,1000),'force_zero_n':number('N',-.01,.01),'assumed_contact_cosine':number('1',.01,1)})
ROW=obj({'time_s':number('s'),'area_m2':number('m^2'),'barrier_inward_m':number('m'),'dipper_position_m':number('m'),'surface_pressure_n_m':nullable(number('N/m')),'balance_force_n':nullable(number('N')),'apparent_modulus_n_m':nullable(number('N/m'))})
STROKE=obj({'index':integer(),'direction':enum(['down','up']),'start_time_s':number('s'),'end_time_s':number('s'),'start_area_m2':number('m^2'),'end_area_m2':number('m^2'),'swept_area_m2':number('m^2',0),'transfer_ratio':nullable(number('1')),'blank_corrected_transfer_ratio':nullable(number('1')),'completed':BOOL})
RESULT=obj({'operation_id':nullable(TEXT),'phase':PHASE,'elapsed_s':number('s'),'rows':array(ROW,0,40000),'strokes':array(STROKE,0,20),'completed_strokes':integer(),'equivalent_layers_from_area':number('1'),'reference_id':nullable(TEXT),'template_source':nullable(TEXT),'method_json':nullable(TEXT),'evidence':TEXT,'error_code':nullable(TEXT)})
LEDGER=obj({k:number(u) for k,u in [('solute_loaded_mol','mol'),('solute_stock_mol','mol'),('surface_mol','mol'),('subphase_leaked_mol','mol'),('substrate_deposited_mol','mol'),('solute_recovered_mol','mol'),('solute_error_mol','mol'),('solute_loaded_kg','kg'),('solute_present_kg','kg'),('solute_recovered_kg','kg'),('solute_error_kg','kg'),('solvent_loaded_m3','m^3'),('solvent_stock_m3','m^3'),('surface_solvent_m3','m^3'),('solvent_evaporated_m3','m^3'),('solvent_recovered_m3','m^3'),('solvent_error_m3','m^3'),('subphase_loaded_kg','kg'),('subphase_present_kg','kg'),('subphase_recovered_kg','kg'),('subphase_error_kg','kg'),('bath_input_j','J'),('evaporation_latent_j','J'),('thermal_error_j','J'),('interfacial_work_j','J')]})
CAPS={};SLOTS={}
def cap(k,i,o,c,z,kind='device_command',visibility='agent',example=None,effects=None):CAPS[k]=dict(input_schema=i,output_schema=o,component_ids=c,function_zh=z,kind=kind,visibility=visibility,example_arguments=example or {},effects=effects or [])
def slot(k,s,u,z,origin='derived',visibility='agent',component='controller'):SLOTS[k]=dict(schema=s,unit=u,meaning=z,origin=origin,visibility=visibility,component=component,write=None)
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller','balance'],'接受单挡板速度、显式恒温浴/有限库存/观测参数','configuration','evaluator',PLANT_EXAMPLE)
for k,sc,ex,c in [('subphase',SUBPHASE,SUBPHASE_EXAMPLE,'trough'),('solution',SAMPLE,SAMPLE_EXAMPLE,'trough'),('substrate',SUBSTRATE,SUBSTRATE_EXAMPLE,'substrate')]:
 cap(k+'.load',sc,obj({'loaded':BOOL}),[c],'装入有限'+k,'configuration','evaluator',ex);cap(k+'.remove',obj({}),obj({'removed':BOOL}),[c],'回收有限'+k,'configuration','evaluator')
cap('film.spread',obj({'volume_m3':number('m^3',1e-15,1e-5)}),obj({'spread_volume_m3':number('m^3')}),['trough'],'有限铺展：溶质入表面、溶剂随后由时间推进蒸发',example={'volume_m3':50e-9})
cap('film.clean',obj({}),obj({'cleaned':BOOL}),['trough'],'人工回收表面膜和残余铺展溶剂','configuration','evaluator')
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['controller','power_switch'],'供电；断电停止运动目标',example={'enabled':True})
cap('barrier.move',obj({'area_m2':number('m^2',AREA_MIN,AREA_MAX),'speed_m_s':number('m/s',BARRIER_MIN_SPEED,BARRIER_MAX_SPEED)}),obj({'target_area_m2':number('m^2')}),['barrier_left','barrier_right'],'设置对称挡板面积目标；实际单挡板速度在宿主时间演化',example={'area_m2':.020,'speed_m_s':.0045})
cap('dipper.move',obj({'position_m':number('m',0,DIPPER_MAX),'speed_m_s':number('m/s',DIPPER_MIN_SPEED,DIPPER_MAX_SPEED)}),obj({'target_position_m':number('m')}),['dipper','substrate'],'浸提目标；实际接触扫面积才产生有限转移',example={'position_m':.030,'speed_m_s':.0018})
cap('pressure.control',obj({'enabled':BOOL,'target_n_m':number('N/m',0,.3),'speed_m_s':number('m/s',BARRIER_MIN_SPEED,BARRIER_MAX_SPEED)}),obj({'enabled':BOOL,'target_n_m':number('N/m')}),['balance','barrier_left','barrier_right'],'Wilhelmy读数驱动挡板闭环，非直接写真值',example={'enabled':True,'target_n_m':.020,'speed_m_s':.0045})
cap('balance.tare',obj({}),obj({'tared':BOOL}),['balance','wilhelmy_plate'],'清洁亚相、固定浸深下记录力基线')
cap('balance.calibrate',CALIBRATION,CALIBRATION,['balance'],'外部独立力校准/假定润湿余弦，不读取真值校准','configuration',example={'force_gain_correction':1.,'force_zero_n':0.,'assumed_contact_cosine':1.})
cap('method.configure',METHOD,METHOD,['controller'],'有限曲线扫描或逐层浸提方法','configuration',example=METHOD_EXAMPLE)
cap('acquisition.start',obj({}),obj({'operation_id':TEXT}),['controller','barrier_left','barrier_right','dipper','balance'],'启动异步等温线/恒压浸提，宿主step唯一推进')
cap('acquisition.stop',obj({}),obj({'phase':enum(['cancelled'])}),['controller','barrier_left','barrier_right','dipper'],'取消保留实际膜/溶剂/半层，冻结历史')
cap('acquisition.result',obj({}),RESULT,['controller'],'纯读历史来源、实测代理曲线和真实扫面积转移比','query')
cap('reference.query',obj({'reference_id':TEXT}),obj({'reference_json':TEXT,'template_source':TEXT,'evidence':TEXT}),['controller'],'真实等温线原始数组/来源，不制造材料','query',example={'reference_id':SAMPLE_EXAMPLE['reference_id']})
cap('analysis.isotherm',obj({'area_m2':array(number('m^2',1e-25),3,10000),'surface_pressure_n_m':array(number('N/m'),3,10000),'constant_molecule_number':BOOL}),obj({'modulus_n_m':array(number('N/m'),3,10000),'interpretation':TEXT}),['controller'],'外部数组有限差分：固定n的压缩模量或有转移/泄漏的表观路径斜率','analysis',example={'area_m2':[.01,.02,.03],'surface_pressure_n_m':[.03,.02,.01],'constant_molecule_number':True})
cap('ledger.read',obj({}),LEDGER,['controller'],'有限表面/亚相/基底/溶液/溶剂与恒温浴账本','query','evaluator')
cap('sim.step',obj({'dt_s':number('s',0,3600)}),obj({'time_s':number('s')}),['controller'],'唯一宿主时钟固定0.1秒分割','simulation','evaluator',{'dt_s':.1})
UNSUPPORTED=['film.molecular_dynamics','film.collapse_prediction','hardware.connect','robot.spreading','noise.empirical','accuracy.certification','thermal.hardware_pid','alternative_multitrough','surface_potential.measure']
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'未实现：'+k)
cap('probe.move',obj({'distance_m':number('m',0,.04)}),obj({'distance_m':number('m',0,.04)}),['balance','wilhelmy_plate'],'人工抬升微天平探头；离液面则不提供表面压观测',example={'distance_m':.04})
cap('clamp.set',obj({'locked':BOOL}),obj({'locked':BOOL}),['substrate_clamp','clamp_jaw'],'上端静止时手动锁紧/松开基底夹',example={'locked':True})
for k,z,c in [('plant.configured','显式工程模型已接受','controller'),('power.enabled','供电','controller'),('subphase.present','亚相已加载','trough'),('solution.present','有限铺展溶液已加载','trough'),('substrate.present','基底已装夹','substrate'),('clamp.locked','手动夹具锁紧','substrate_clamp'),('balance.tared','固定湿润深度的清亚相力基线','balance'),('pressure.control_enabled','表面压闭环已启用','controller')]:slot(k,BOOL,'1',z,component=c)
for k,hi,z,c in [('barrier.inward_m',BARRIER_MAX,'每块对称挡板向内行程','barrier_left'),('dipper.position_m',DIPPER_MAX,'基底底端向下行程，.063m穿水面','dipper'),('probe.lift_m',.04,'人工微天平抬升','balance'),('clamp.release_m',.003,'手动夹松开位移','clamp_jaw'),('power.angle_rad',.28,'电源工程姿态','power_switch')]:
 u='rad' if k.endswith('rad') else 'm';slot(k,number(u,0,hi),u,z,'scene',component=c)
for k,s,u,z,c in [('barrier.target_area_m2',number('m^2',AREA_MIN,AREA_MAX),'m^2','目标面积，不是实际面积','barrier_left'),('dipper.target_m',number('m',0,DIPPER_MAX),'m','浸提目标','dipper'),('pressure.target_n_m',number('N/m',0,.3),'N/m','表面压力设定','controller')]:slot(k,s,u,z,'setpoint',component=c)
slot('area.indicated_m2',number('m^2',AREA_MIN,AREA_MAX),'m^2','由实际挡板位置计算的理想编码器面积，未注入实测噪声',component='controller')
slot('pressure.indicated_n_m',nullable(number('N/m')),'N/m','清亚相tare减Wilhelmy实际力，受润湿/浮力/零漂影响',component='balance');slot('balance.force_n',nullable(number('N')),'N','滤波与外部校准后的Wilhelmy力',component='balance');slot('temperature.indicated_k',nullable(number('K')),'K','恒温浴边界温度，无独立温控精度认证',component='trough')
for k,u,z in [('truth.surface_mol','mol','有限表面分子'),('truth.leaked_mol','mol','表面泄漏入亚相'),('truth.deposited_mol','mol','基底沉积真实分子'),('truth.solution_mol','mol','铺展库存真实分子'),('truth.surface_solvent_m3','m^3','未挥发表面溶剂'),('truth.temperature_k','K','显式恒温浴温度')]:slot(k,number(u,0),u,z,'process_truth','evaluator')
for k,u,z in [('truth.surface_pressure_n_m','N/m','实测等温线准静态代理π'),('truth.eos_modulus_n_m','N/m','固定n/T/branch源曲线局部导数，原始噪声可负')]:slot(k,nullable(number(u)),u,z,'process_truth','evaluator')
slot('clock.time_s',number('s',0),'s','唯一宿主物理时间');slot('acquisition.phase',PHASE,'1','异步状态');slot('acquisition.elapsed_s',number('s',0),'s','当前采集真实时间');slot('acquisition.points',integer(),'1','已采集曲线点');slot('acquisition.completed_strokes',integer(),'1','完整运动行程数，非保证完整单层')
PHYSICS_EFFECTS=list(SLOTS)

class BiolinMediumlRuntime:
 def __init__(self,instance_id='mediuml_01'):
  self.instance_id=instance_id;self.plant=None;self.subphase=None;self.solution=None;self.substrate=None;self.reference=None;self.power=False;self.clamp_locked=False;self.probe_lift=0.;self.area=AREA_MAX;self.area_target=AREA_MAX;self.barrier_speed=BARRIER_MAX_SPEED;self.dipper=0.;self.dipper_target=0.;self.dipper_speed=DIPPER_MAX_SPEED;self.control=False;self.pressure_target=.02;self.tared=False;self.tare_force=0.;self.sensor_force=0.;self.calibration=copy.deepcopy(CAPS['balance.calibrate']['example_arguments']);self.surface_mol=0.;self.stock_volume=0.;self.leaked_mol=0.;self.deposited_mol=0.;self.surface_solvent=0.;self.spread_solvent=0.;self.solute_loaded=0.;self.solute_recovered=0.;self.solute_loaded_mass=0.;self.solute_recovered_mass=0.;self.solvent_loaded=0.;self.solvent_evaporated=0.;self.solvent_recovered=0.;self.subphase_loaded=0.;self.subphase_recovered=0.;self.bath_energy=0.;self.latent_energy=0.;self.interfacial_work=0.;self.surface_reference=None;self.material_molar_mass=0.;self.first_favorable_transferred=False;self.process_fault=None;self.method=copy.deepcopy(METHOD_EXAMPLE);self.phase='idle';self.elapsed=0.;self.rows=[];self.strokes=[];self.current_stroke=None;self.stroke_index=0;self.operation_start=0.;self.operation_id=None;self.result_reference=None;self.source=None;self.result_evidence=None;self.operation_method=None;self.operation_substrate_area=0.;self.error=None;self.records={};self._counter=0;self._ticks=0;self._remainder=0.;self.time_s=0.;self.revision=0;self._requests={};self._owners={};self._principals={'agent':'agent','evaluator':'evaluator'}
 def register_principal(self,principal_id,visibility='agent'):
  if not isinstance(principal_id,str) or not principal_id or principal_id in self._principals or visibility not in ('agent','debug','evaluator'):raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='agent'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return BiolinMediumlSession(self,principal_id)
 def describe(self):return export_biolin_mediuml_contract_metadata()
 def _stock_mol(self):return self.stock_volume*self.solution['concentration_mol_m3'] if self.solution else 0.
 def _eos(self,area=None,n=None):
  n=self.surface_mol if n is None else n;area=self.area if area is None else area
  if n<=0:return 0.,0.
  if not self.surface_reference:raise ActionRejected('INVALID_REFERENCE')
  p,mod=eos(self.surface_reference,area/(n*NA),self.solution['allow_dilute_extension'] if self.solution else False)
  if not self.subphase or self.subphase['surface_tension_n_m']-p<=0:raise ActionRejected('NONPOSITIVE_SURFACE_TENSION')
  return p,mod
 def _pressure_truth(self):
  try:return self._eos()
  except ActionRejected:return None,None
 def _force(self):
  if not self.plant:return 0.
  p=self.plant;force=p['plate_mass_kg']*G;depth=max(0.,p['plate_immersion_m']+self._liquid_rise()-self.probe_lift)
  if self.subphase and depth>0:
   pressure,_=self._eos();gamma=self.subphase['surface_tension_n_m']-pressure;force+=2*(p['plate_width_m']+p['plate_thickness_m'])*gamma*p['plate_contact_cosine']-self.subphase['density_kg_m3']*G*p['plate_width_m']*p['plate_thickness_m']*depth
  if abs(force)>G*.001+1e-15:raise ActionRejected('BALANCE_OVERLOAD')
  return force*p['force_gain']+p['force_offset_n']+p['force_drift_n_s']*self.time_s
 def _balance(self):return (self.sensor_force-self.calibration['force_zero_n'])/self.calibration['force_gain_correction']
 def _observed(self):
  if not self.power or not self.tared or not self.subphase or self.probe_lift>=self.plant['plate_immersion_m']+self._liquid_rise() or self._pressure_truth()[0] is None:return None
  return (self.tare_force-self._balance())/(2*(self.plant['plate_width_m']+self.plant['plate_thickness_m'])*self.calibration['assumed_contact_cosine'])
 def read(self,role='evaluator'):
  pressure,mod=self._pressure_truth();d={'plant.configured':self.plant is not None,'power.enabled':self.power,'subphase.present':self.subphase is not None,'solution.present':self.solution is not None,'substrate.present':self.substrate is not None,'clamp.locked':self.clamp_locked,'balance.tared':self.tared,'pressure.control_enabled':self.control,'barrier.inward_m':max(0.,min(BARRIER_MAX,(LENGTH-self.area/WIDTH)/2)),'dipper.position_m':self.dipper,'probe.lift_m':self.probe_lift,'clamp.release_m':0. if self.clamp_locked else .003,'power.angle_rad':.28 if self.power else 0.,'barrier.target_area_m2':self.area_target,'dipper.target_m':self.dipper_target,'pressure.target_n_m':self.pressure_target,'area.indicated_m2':self.area,'subphase.level_delta_m':self._liquid_rise(),'panel.area_text':f'{self.area*1e4:.1f}' if self.power else '', 'panel.pressure_text':f'{self._observed()*1e3:.1f}' if self._observed() is not None else '','pressure.indicated_n_m':self._observed(),'balance.force_n':self._balance() if self.power else None,'temperature.indicated_k':self.plant['temperature_k'] if self.plant and self.power else None,'truth.surface_mol':self.surface_mol,'truth.leaked_mol':self.leaked_mol,'truth.deposited_mol':self.deposited_mol,'truth.solution_mol':self._stock_mol(),'truth.surface_solvent_m3':self.surface_solvent,'truth.temperature_k':self.plant['temperature_k'] if self.plant else 294.15,'truth.surface_pressure_n_m':pressure,'truth.eos_modulus_n_m':mod,'clock.time_s':self.time_s,'acquisition.phase':self.phase,'acquisition.elapsed_s':self.elapsed,'acquisition.points':len(self.rows),'acquisition.completed_strokes':sum(s['completed'] for s in self.strokes)}
  return copy.deepcopy({k:v for k,v in d.items() if role in ('evaluator','debug') or SLOTS[k]['visibility']=='agent'})
 def _ledger(self):
  stock=self._stock_mol();present=stock+self.surface_mol+self.leaked_mol+self.deposited_mol;mass=present*self.material_molar_mass;sub=self.subphase['volume_m3']*self.subphase['density_kg_m3'] if self.subphase else 0.
  return dict(solute_loaded_mol=self.solute_loaded,solute_stock_mol=stock,surface_mol=self.surface_mol,subphase_leaked_mol=self.leaked_mol,substrate_deposited_mol=self.deposited_mol,solute_recovered_mol=self.solute_recovered,solute_error_mol=self.solute_loaded-present-self.solute_recovered,solute_loaded_kg=self.solute_loaded_mass,solute_present_kg=mass,solute_recovered_kg=self.solute_recovered_mass,solute_error_kg=self.solute_loaded_mass-mass-self.solute_recovered_mass,solvent_loaded_m3=self.solvent_loaded,solvent_stock_m3=self.stock_volume,surface_solvent_m3=self.surface_solvent,solvent_evaporated_m3=self.solvent_evaporated,solvent_recovered_m3=self.solvent_recovered,solvent_error_m3=self.solvent_loaded-self.stock_volume-self.surface_solvent-self.solvent_evaporated-self.solvent_recovered,subphase_loaded_kg=self.subphase_loaded,subphase_present_kg=sub,subphase_recovered_kg=self.subphase_recovered,subphase_error_kg=self.subphase_loaded-sub-self.subphase_recovered,bath_input_j=self.bath_energy,evaporation_latent_j=self.latent_energy,thermal_error_j=self.bath_energy-self.latent_energy,interfacial_work_j=self.interfacial_work)
 def _result(self):
  if self.phase!='running' and self.operation_id in self.records:return copy.deepcopy(self.records[self.operation_id])
  strokes=copy.deepcopy(self.strokes)
  if self.current_stroke is not None:strokes.append(self._stroke_result(False))
  return dict(operation_id=self.operation_id,phase=self.phase,elapsed_s=self.elapsed,rows=copy.deepcopy(self.rows),strokes=strokes,completed_strokes=sum(s['completed'] for s in strokes),equivalent_layers_from_area=sum((s['transfer_ratio'] or 0.)*s['swept_area_m2'] for s in strokes)/self.operation_substrate_area if self.operation_substrate_area>0 else 0.,reference_id=self.result_reference,template_source=self.source,method_json=self.operation_method,evidence=self.result_evidence or 'No operation. Physics and hardware accuracy remain unvalidated.',error_code=self.error)
 def invoke(self,capability,**arguments):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability]['input_schema'],arguments)
  if capability=='sim.step':self.step(arguments['dt_s']);return {'time_s':self.time_s}
  before=copy.deepcopy(self.__dict__)
  try:
   result=self._invoke(capability,arguments);validate(CAPS[capability]['output_schema'],result)
   if CAPS[capability]['kind'] not in ('query','analysis'):self.revision+=1
   return copy.deepcopy(result)
  except Exception:self.__dict__.clear();self.__dict__.update(before);raise
 def _stopped(self):
  if self.phase=='running':raise ActionRejected('BUSY')
  if abs(self.area_target-self.area)>1e-12 or abs(self.dipper_target-self.dipper)>1e-12 or self.control:raise ActionRejected('MOTION_ACTIVE')
 def _access(self):
  self._stopped()
  if self.plant and self.plant['temperature_k']>self.plant['safe_access_temperature_k']:raise ActionRejected('HOT_SUBPHASE')
 def _dry_ready(self):
  if self.surface_solvent>self.spread_solvent*self.method['maximum_solvent_fraction']:raise ActionRejected('SOLVENT_NOT_EVAPORATED')
 def _powered(self):
  if not self.power or not self.plant:raise ActionRejected('NOT_READY')
 def _check_conditions(self,rec,a):
  conditions=rec.get('conditions',{});t=conditions.get('temperature_k');sub=conditions.get('subphase') or conditions.get('subphase_identity');unknown=t is None or sub is None
  if unknown and not a['accept_unknown_conditions']:raise ActionRejected('UNKNOWN_REFERENCE_CONDITIONS')
  if t is not None and abs(t-self.plant['temperature_k'])>(conditions.get('temperature_uncertainty_k') or 0.)+1e-9 and not a['accept_condition_transfer']:raise ActionRejected('REFERENCE_CONDITIONS')
  if sub is not None and str(sub).casefold()!=self.subphase['identity'].casefold() and not a['accept_condition_transfer']:raise ActionRejected('REFERENCE_CONDITIONS')
  for source_key,local_key in [('subphase_ph','ph'),('buffer_concentration_mol_m3','buffer_concentration_mol_m3')]:
   value=conditions.get(source_key)
   if value is not None and abs(value-self.subphase[local_key])>1e-12 and not a['accept_condition_transfer']:raise ActionRejected('REFERENCE_CONDITIONS')
 def _invoke(self,k,a):
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  if k=='acquisition.result':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='reference.query':
   ref=library()['substances'].get(a['reference_id'])
   if ref is None:raise ActionRejected('INVALID_REFERENCE')
   return dict(reference_json=json.dumps(ref,ensure_ascii=False),template_source=json.dumps(ref.get('source'),ensure_ascii=False),evidence=json.dumps(ref.get('evidence'),ensure_ascii=False))
  if k=='analysis.isotherm':
   x=np.array(a['area_m2']);y=np.array(a['surface_pressure_n_m'])
   if len(x)!=len(y) or not (np.all(np.diff(x)>0) or np.all(np.diff(x)<0)):raise ActionRejected('INVALID_ARRAYS')
   return dict(modulus_n_m=list(map(float,-x*np.gradient(y,x))),interpretation='constant-number isotherm compression modulus' if a['constant_molecule_number'] else 'apparent path modulus; leakage/deposition changes molecule number')
  if k=='plant.configure':
   if self.plant or self.time_s or self.subphase or self.solution:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_engineering'] or not a['isothermal_bath']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   self.plant=copy.deepcopy(a);self.sensor_force=self._force();return {'configured':True}
  if k=='power.set':
   if a['enabled'] and not self.plant:raise ActionRejected('NOT_READY')
   if not a['enabled'] and self.phase=='running':raise ActionRejected('BUSY')
   self.power=a['enabled']
   if not self.power:self.control=False;self.area_target=self.area;self.dipper_target=self.dipper
   return a
  if k=='clamp.set':
   self._access()
   if self.dipper>1e-12:raise ActionRejected('SUBSTRATE_ACCESS')
   self.clamp_locked=a['locked'];return a
  if k=='probe.move':
   self._access();self.probe_lift=a['distance_m'];self.tared=False;self.sensor_force=self._force();return a
  if k=='subphase.load':
   self._access()
   if not self.plant or self.subphase or self.surface_mol or self.solution or self.substrate:raise ActionRejected('SUBPHASE_ACCESS')
   if not a['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   self.subphase=copy.deepcopy(a);self.subphase_loaded+=a['volume_m3']*a['density_kg_m3'];self.tared=False;self.sensor_force=self._force();return {'loaded':True}
  if k=='subphase.remove':
   self._access()
   if not self.subphase or self.surface_mol or self.surface_solvent or self.substrate or self.solution or self.probe_lift<.01:raise ActionRejected('SUBPHASE_ACCESS')
   self.subphase_recovered+=self.subphase['volume_m3']*self.subphase['density_kg_m3'];self.solute_recovered+=self.leaked_mol;self.solute_recovered_mass+=self.leaked_mol*self.material_molar_mass;self.leaked_mol=0.;self.subphase=None;self.tared=False;self.sensor_force=self._force();return {'removed':True}
  if k=='solution.load':
   self._access()
   if not self.plant or not self.subphase or self.solution or self.surface_mol or self.deposited_mol or self.leaked_mol:raise ActionRejected('SOLUTION_ACCESS')
   if not a['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   rec=copy.deepcopy(library()['substances'].get(a['reference_id'],{}));curve(rec);self._check_conditions(rec,a);self.reference=rec;self.solution=copy.deepcopy(a);self.stock_volume=a['volume_m3'];n=self._stock_mol();self.solute_loaded+=n;self.solute_loaded_mass+=n*a['molar_mass_kg_mol'];self.material_molar_mass=a['molar_mass_kg_mol'];self.solvent_loaded+=self.stock_volume;return {'loaded':True}
  if k=='solution.remove':
   self._access()
   if not self.solution or self.surface_mol or self.surface_solvent:raise ActionRejected('SOLUTION_ACCESS')
   self.solute_recovered+=self._stock_mol();self.solute_recovered_mass+=self._stock_mol()*self.material_molar_mass;self.solvent_recovered+=self.stock_volume;self.stock_volume=0.;self.solution=None;return {'removed':True}
  if k=='film.spread':
   self._access()
   if not self.solution or not self.subphase:raise ActionRejected('NO_SOLUTION')
   if a['volume_m3']>self.stock_volume:raise ActionRejected('INSUFFICIENT_SOLUTION')
   self.surface_reference=copy.deepcopy(self.reference);n=a['volume_m3']*self.solution['concentration_mol_m3'];self._eos(n=self.surface_mol+n);self.surface_mol+=n;self.stock_volume-=a['volume_m3'];self.surface_solvent+=a['volume_m3'];self.spread_solvent+=a['volume_m3'];self.process_fault=None;return {'spread_volume_m3':a['volume_m3']}
  if k=='film.clean':
   self._access();self.solute_recovered+=self.surface_mol;self.solute_recovered_mass+=self.surface_mol*self.material_molar_mass;self.solvent_recovered+=self.surface_solvent;self.surface_mol=0.;self.surface_solvent=0.;self.spread_solvent=0.;self.surface_reference=None;self.process_fault=None;return {'cleaned':True}
  if k=='substrate.load':
   self._access()
   if self.substrate or self.clamp_locked or self.dipper>1e-12:raise ActionRejected('SUBSTRATE_ACCESS')
   if not a['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   self.substrate=copy.deepcopy(a);self.first_favorable_transferred=False;return {'loaded':True}
  if k=='substrate.remove':
   self._access()
   if not self.substrate or self.clamp_locked or self.dipper>1e-12:raise ActionRejected('SUBSTRATE_ACCESS')
   self.solute_recovered+=self.deposited_mol;self.solute_recovered_mass+=self.deposited_mol*self.material_molar_mass;self.deposited_mol=0.;self.substrate=None;return {'removed':True}
  if k=='balance.calibrate':
   self._stopped();self.calibration=copy.deepcopy(a);self.tared=False;return a
  if k=='balance.tare':
   self._stopped();self._powered()
   if not self.subphase or self.surface_mol or self.surface_solvent or self.leaked_mol or self.probe_lift!=0.:raise ActionRejected('CLEAN_BASELINE_REQUIRED')
   self.tare_force=self._balance();self.tared=True;return {'tared':True}
  if k=='barrier.move':
   self._stopped();self._powered();self._dry_ready();self._eos(area=a['area_m2'])
   if self.surface_mol and a['area_m2']>self.area and not self.solution['accept_quasistatic_branch_reuse']:raise ActionRejected('BRANCH_REUSE_NOT_ACCEPTED')
   self.area_target=a['area_m2'];self.barrier_speed=a['speed_m_s'];return {'target_area_m2':self.area_target}
  if k=='dipper.move':
   self._stopped();self._powered();self._dry_ready();self._check_dipper(a['position_m']);self.dipper_target=a['position_m'];self.dipper_speed=a['speed_m_s'];return {'target_position_m':self.dipper_target}
  if k=='pressure.control':
   if self.phase=='running':raise ActionRejected('BUSY')
   if a['enabled']:self._ready();self._check_pressure(a['target_n_m'])
   self.control=a['enabled'];self.pressure_target=a['target_n_m'];self.barrier_speed=a['speed_m_s'];self.area_target=self.area;return {'enabled':self.control,'target_n_m':self.pressure_target}
  if k=='method.configure':
   self._stopped();self._check_method(a);self.method=copy.deepcopy(a);return a
  if k=='acquisition.start':
   self._stopped();self._ready();self._check_method(self.method);self._check_pressure(self.method['target_pressure_n_m'])
   if self.method['mode'] in ('expansion','deposition','hold') and not self.solution['accept_quasistatic_branch_reuse']:raise ActionRejected('BRANCH_REUSE_NOT_ACCEPTED')
   self._counter+=1;self.operation_id=self.instance_id+':'+str(self._counter);self.phase='running';self.elapsed=0.;self.rows=[];self.strokes=[];self.current_stroke=None;self.stroke_index=0;self.error=None;self.operation_start=self.time_s;self.result_reference=self.solution['reference_id'];self.source=json.dumps(self.surface_reference.get('source'),ensure_ascii=False);self.operation_method=json.dumps(self.method,sort_keys=True);self.operation_substrate_area=self.substrate['width_m']*self.substrate['height_m']*self.substrate['faces'] if self.substrate else 0.;self.result_evidence=self._evidence();self.barrier_speed=self.method['barrier_speed_m_s'];self.dipper_speed=self.method['dipping_speed_m_s'];self.pressure_target=self.method['target_pressure_n_m'];self.control=self.method['mode'] in ('hold','deposition')
   if self.method['mode'] in ('compression','expansion'):self.area_target=self.method['target_area_m2']
   if self.method['mode']=='deposition':self._begin_stroke()
   self._record();return {'operation_id':self.operation_id}
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self._finish('cancelled');return {'phase':'cancelled'}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _check_dipper(self,position):
  if not self.substrate or not self.clamp_locked:raise ActionRejected('SUBSTRATE_ACCESS')
  if position>SURFACE_POSITION and not self.subphase:raise ActionRejected('NO_SUBPHASE')
  if self.surface_mol and not self.solution['accept_quasistatic_branch_reuse']:raise ActionRejected('BRANCH_REUSE_NOT_ACCEPTED')
 def _check_pressure(self,value):
  if not self.subphase or value>=self.subphase['surface_tension_n_m']:raise ActionRejected('NONPOSITIVE_SURFACE_TENSION')
 def _ready(self):
  self._powered();self._dry_ready()
  if not self.subphase or self.surface_mol<=0 or not self.solution or not self.tared or self.probe_lift!=0.:raise ActionRejected('NOT_READY')
  self._eos()
 def _check_method(self,m):
  if abs(m['sample_interval_s']/DT-round(m['sample_interval_s']/DT))>1e-8:raise ActionRejected('SAMPLING_GRID')
  if m['mode']=='compression' and m['target_area_m2']>self.area+1e-12 or m['mode']=='expansion' and m['target_area_m2']<self.area-1e-12:raise ActionRejected('METHOD_DIRECTION')
  if self.surface_mol and m['mode'] in ('compression','expansion'):self._eos(area=m['target_area_m2'])
  if m['mode']=='deposition':
   for x in m['dipper_positions_m']:self._check_dipper(x)
   if self._observed() is not None and abs(self._observed()-m['target_pressure_n_m'])>m['pressure_tolerance_n_m']:raise ActionRejected('PRESSURE_NOT_READY')
 def _evidence(self):
  head=('Derived engineering EOS softplus_condensation_eos_v1 parameterised by measured-literature characteristic scalars (lift-off area, collapse pressure and two published slope constraints); not a measured array: no LE/LC plateau, kink, hysteresis or post-collapse behaviour. ' if is_scalar_model(self.surface_reference) else 'Measured source compression arrays, unsmoothed/no clipping; conservative published or measured-branch collapse domain. ')
  return (head+'Concentration, quasi-static branch reuse, dilute extension, Wilhelmy wetting, leakage, evaporation and transfer kinetics are explicit engineering inputs. Fast solvent evaporation is not experimental validation. Observed pressure uses clean tare minus force divided by perimeter and assumed contact cosine; includes weight, buoyancy and wetting. Ideal isothermal bath supplies solvent latent heat; no OEM thermal PID/subphase evaporation claim. Surface work reported separately, not resolved molecular heat. Transfer ratio uses actual barrier decrease/actual swept area; optional correction uses independent supplied blank rate, never hidden leakage. Partial strokes are not complete films. Default geometry52x56x3mm; different numerical dimensions require separate geometry. Free liquid rises over full273cm2 waterplane; meniscus freeboard0.5mm is engineering, no overflow hardware claim. Source='+json.dumps(self.surface_reference.get('evidence'))+'; Instance-specific inventory, leakage, true wetting, transfer efficiency and sensor error parameters are evaluator-only, not exposed here.')
 def _liquid_rise(self,position=None):
  if not self.substrate or not self.subphase:return 0.
  z=self.dipper if position is None else position;cross=self.substrate['width_m']*self.substrate['thickness_m'];d=max(0.,z-SURFACE_POSITION);rise=cross*d/(AREA_MAX-cross);return min(rise,cross*self.substrate['height_m']/AREA_MAX)
 def _wet_length(self,position):
  if not self.substrate:return 0.
  return min(self.substrate['height_m'],max(0.,position-SURFACE_POSITION+self._liquid_rise(position)))
 def _begin_stroke(self):
  target=self.method['dipper_positions_m'][self.stroke_index];direction='down' if target>=self.dipper else 'up';self.dipper_target=target;self.current_stroke=dict(index=self.stroke_index,direction=direction,start_time_s=self.time_s,start_area_m2=self.area,swept_area_m2=0.)
 def _stroke_result(self,completed):
  s=self.current_stroke;area=s['swept_area_m2'];elapsed=self.time_s-s['start_time_s'];decrease=s['start_area_m2']-self.area;return dict(index=s['index'],direction=s['direction'],start_time_s=s['start_time_s'],end_time_s=self.time_s,start_area_m2=s['start_area_m2'],end_area_m2=self.area,swept_area_m2=area,transfer_ratio=decrease/area if area>0 else None,blank_corrected_transfer_ratio=(decrease-self.method['blank_leak_area_rate_m2_s']*elapsed)/area if area>0 else None,completed=completed)
 def _record(self):
  if self.rows and abs(self.rows[-1]['time_s']-self.time_s)<=1e-12:return
  pressure=self._observed();row=dict(time_s=self.time_s,area_m2=self.area,barrier_inward_m=max(0.,min(BARRIER_MAX,(LENGTH-self.area/WIDTH)/2)),dipper_position_m=self.dipper,surface_pressure_n_m=pressure,balance_force_n=self._balance() if self.power else None,apparent_modulus_n_m=None)
  if self.rows:
   last=self.rows[-1];delta=self.area-last['area_m2']
   if abs(delta)>1e-15 and pressure is not None and last['surface_pressure_n_m'] is not None:row['apparent_modulus_n_m']=-self.area*(pressure-last['surface_pressure_n_m'])/delta
  if not self.rows or abs(self.rows[-1]['time_s']-self.time_s)>1e-12:self.rows.append(row)
  else:self.rows[-1]=row
 def _finish(self,phase,error=None):
  self.elapsed=self.time_s-self.operation_start;self._record()
  if self.current_stroke is not None:self.strokes.append(self._stroke_result(False));self.current_stroke=None
  self.phase=phase;self.error=error;self.control=False;self.area_target=self.area;self.dipper_target=self.dipper
  if self.operation_id:self.records[self.operation_id]=self._result()
 def _transfer(self,old_z,new_z):
  if not self.substrate:return
  oldwet=self._wet_length(old_z);newwet=self._wet_length(new_z);swept=abs(newwet-oldwet)*self.substrate['width_m']*self.substrate['faces']
  if self.current_stroke is not None:self.current_stroke['swept_area_m2']+=swept
  if swept<=0 or self.surface_mol<=0:return
  sub=self.substrate;down=new_z>old_z;favorable=not down if sub['wetting']=='hydrophilic' else down;mode=sub['deposition_mode'];eligible=mode=='y_type' or mode=='upstroke_only' and not down or mode=='downstroke_only' and down or mode=='first_favorable' and favorable and not self.first_favorable_transferred
  if not eligible:return
  transferred=self.surface_mol*(-math.expm1(-sub['transfer_efficiency']*swept/self.area));self.surface_mol-=transferred;self.deposited_mol+=transferred
  if mode=='first_favorable' and ((not down and newwet<=0) or (down and newwet>=sub['height_m'])):self.first_favorable_transferred=True
 def _tick(self):
  self.time_s=self._ticks*DT;dt=DT
  if self.plant:
   evaporated=self.surface_solvent*(-math.expm1(-self.plant['solvent_evaporation_rate_s']*dt));self.surface_solvent-=evaporated;self.solvent_evaporated+=evaporated;latent=evaporated*self.plant['solvent_density_kg_m3']*self.plant['solvent_latent_j_kg'];self.latent_energy+=latent;self.bath_energy+=latent
   leaked=self.surface_mol*(-math.expm1(-self.plant['leak_rate_s']*dt));self.surface_mol-=leaked;self.leaked_mol+=leaked
  if self.power:
   oldarea=self.area;oldp=self._pressure_truth()[0];next_area=self.area
   if self.control:
    indicated=self._observed()
    if indicated is not None:
     error=self.pressure_target-indicated
     if abs(error)>self.method['pressure_tolerance_n_m']:
      v=min(self.barrier_speed,max(BARRIER_MIN_SPEED,abs(self.plant['controller_gain_m3_n_s']*error/(2*WIDTH))));next_area=max(AREA_MIN,min(AREA_MAX,self.area-math.copysign(2*WIDTH*v*dt,error)))
   else:next_area=self.area+max(-2*WIDTH*self.barrier_speed*dt,min(2*WIDTH*self.barrier_speed*dt,self.area_target-self.area))
   try:self._eos(area=next_area);self.area=next_area
   except ActionRejected as exc:self.process_fault=str(exc);self.control=False;self.area_target=self.area
   if oldp is not None:self.interfacial_work+=oldp*(oldarea-self.area)
   oldz=self.dipper;self.dipper+=max(-self.dipper_speed*dt,min(self.dipper_speed*dt,self.dipper_target-self.dipper));self._transfer(oldz,self.dipper)
  try:
   raw=self._force()
   if self.plant:self.sensor_force+=(-math.expm1(-dt/self.plant['pressure_sensor_tau_s']))*(raw-self.sensor_force)
  except ActionRejected as exc:self.process_fault=str(exc);self.control=False;self.area_target=self.area;self.dipper_target=self.dipper
  if self.phase=='running':
   self.elapsed=self.time_s-self.operation_start
   if (self._ticks-round(self.operation_start/DT))%round(self.method['sample_interval_s']/DT)==0:self._record()
   if self.process_fault:self._finish('failed',self.process_fault);return
   m=self.method
   if m['mode'] in ('compression','expansion') and abs(self.area-self.area_target)<1e-12:self._finish('succeeded');return
   if m['mode']=='deposition' and abs(self.dipper-self.dipper_target)<1e-12:
    self.strokes.append(self._stroke_result(True));self.current_stroke=None;self.stroke_index+=1
    if self.stroke_index>=len(m['dipper_positions_m']):self._finish('succeeded');return
    self._begin_stroke()
   if self.elapsed>=m['duration_s']-1e-9:self._finish('succeeded' if m['mode']=='hold' else 'failed',None if m['mode']=='hold' else 'METHOD_TIMEOUT')
 def step(self,dt_s):
  validate(number('s',0,3600),dt_s);total=self._remainder+dt_s;n=int((total+1e-12)/DT);self._remainder=total-n*DT
  for _ in range(n):self._ticks+=1;self._tick()
  self.revision+=n

slot('subphase.level_delta_m',number('m',0,.0005),'m','全槽水面排液升幅；0.5mm工程弯月面余量，非厂家溢流认证','scene',component='subphase')

class BiolinMediumlSession:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,instance_id):
  if instance_id!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  meta=export_biolin_mediuml_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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
slot('panel.area_text',TEXT,'1','面积LCD字符串，cm2，一位小数；断电空白',component='controller')
slot('panel.pressure_text',TEXT,'1','表面压LCD字符串，mN/m，一位小数；无读数或断电空白',component='controller')
EFFECTS={
 'plant.configure':['plant.configured','truth.temperature_k'],
 'subphase.load':['subphase.present','balance.tared','balance.force_n','pressure.indicated_n_m','subphase.level_delta_m'],
 'subphase.remove':['subphase.present','balance.tared','balance.force_n','pressure.indicated_n_m','truth.leaked_mol','subphase.level_delta_m'],
 'solution.load':['solution.present','truth.solution_mol'],
 'solution.remove':['solution.present','truth.solution_mol'],
 'film.spread':['truth.solution_mol','truth.surface_mol','truth.surface_solvent_m3','truth.surface_pressure_n_m','truth.eos_modulus_n_m','pressure.indicated_n_m'],
 'film.clean':['truth.surface_mol','truth.surface_solvent_m3','truth.surface_pressure_n_m','truth.eos_modulus_n_m','pressure.indicated_n_m'],
 'substrate.load':['substrate.present'],
 'substrate.remove':['substrate.present','truth.deposited_mol'],
 'power.set':['power.enabled','power.angle_rad','balance.force_n','temperature.indicated_k','pressure.indicated_n_m','pressure.control_enabled','barrier.target_area_m2','dipper.target_m'],
 'clamp.set':['clamp.locked','clamp.release_m'],
 'probe.move':['probe.lift_m','balance.tared','pressure.indicated_n_m','balance.force_n'],
 'balance.calibrate':['balance.tared','pressure.indicated_n_m','balance.force_n'],
 'balance.tare':['balance.tared','pressure.indicated_n_m'],
 'barrier.move':['barrier.target_area_m2'],
 'dipper.move':['dipper.target_m'],
 'pressure.control':['pressure.control_enabled','pressure.target_n_m','barrier.target_area_m2'],
 'acquisition.start':['acquisition.phase','acquisition.elapsed_s','acquisition.points','acquisition.completed_strokes','barrier.target_area_m2','dipper.target_m','pressure.target_n_m','pressure.control_enabled'],
 'acquisition.stop':['acquisition.phase','acquisition.elapsed_s','acquisition.points','pressure.control_enabled','barrier.target_area_m2','dipper.target_m'],
 'sim.step':list(SLOTS),
}
for key,effects in EFFECTS.items():
 if 'pressure.indicated_n_m' in effects:effects.append('panel.pressure_text')
 if 'area.indicated_m2' in effects or key=='power.set':effects.append('panel.area_text')
 CAPS[key]['effects']=list(dict.fromkeys(effects))
PHYSICS_EFFECTS=['panel.area_text','panel.pressure_text','clock.time_s','barrier.inward_m','dipper.position_m','barrier.target_area_m2','dipper.target_m','area.indicated_m2','subphase.level_delta_m','balance.force_n','pressure.indicated_n_m','pressure.control_enabled','truth.surface_mol','truth.leaked_mol','truth.deposited_mol','truth.surface_solvent_m3','truth.surface_pressure_n_m','truth.eos_modulus_n_m','acquisition.phase','acquisition.elapsed_s','acquisition.points','acquisition.completed_strokes']

def export_biolin_mediuml_contract_metadata():
 slots={};caps={}
 for k,v in SLOTS.items():slots[k]=dict(meaning_zh=v['meaning'],component_ids=[v['component']],value_schema=copy.deepcopy(v['schema']),unit=v['unit'],reference_conditions='SI; temperature fixed by explicitly accepted ideal bath. Barrier speed is per single symmetric barrier. Dipperpositive downward, substrate bottom crosses unperturbed surface at.063m. Readings are engineering sensor proxies.',origin=v['origin'],visibility=v['visibility'],access='read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'BiolinMediumlSession.read(state_ids=[{k!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description='Host0.1s fixed ticks; queries never advance. No random measured-noise or accuracy certification.'),source_ids=['simulation_assumption'])
 errors=['UNKNOWN_CAPABILITY','UNKNOWN_INSTANCE','UNKNOWN_OPERATION','INVALID_ARGUMENT','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','BUSY','MOTION_ACTIVE','NOT_RUNNING','NOT_READY','INITIALIZATION_ONLY','MODEL_NOT_ACCEPTED','SUBPHASE_ACCESS','SOLUTION_ACCESS','SUBSTRATE_ACCESS','HOT_SUBPHASE','NO_SUBPHASE','NO_SOLUTION','INSUFFICIENT_SOLUTION','INVALID_REFERENCE','EOS_AREA_RANGE','UNKNOWN_REFERENCE_CONDITIONS','REFERENCE_CONDITIONS','NONPOSITIVE_SURFACE_TENSION','SOLVENT_NOT_EVAPORATED','CLEAN_BASELINE_REQUIRED','BRANCH_REUSE_NOT_ACCEPTED','PRESSURE_NOT_READY','METHOD_DIRECTION','SAMPLING_GRID','INVALID_ARRAYS','METHOD_TIMEOUT','BALANCE_OVERLOAD']
 for k,c in CAPS.items():
  un=k in UNSUPPORTED;async_=k=='acquisition.start';caps[k]={key:copy.deepcopy(c[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']};caps[k].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ['scene','robot','hardware'] else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.biolin_mediuml.BiolinMediumlSession.invoke',invocation=f'session.invoke(instance_id="mediuml_01",capability_id={k!r},arguments={c["example_arguments"]!r},request_id="unique")'),preconditions=[] if c['kind'] in ['query','analysis'] or un else [dict(description_zh='有限库存、清亚相tare、实际静止/夹具/源曲线域/蒸发和润湿前提；拒绝原子',state_ids=['power.enabled','plant.configured','subphase.present','balance.tared','clamp.locked','acquisition.phase'],enforced_by='runtime',check='atomic runtime invoke and registered Session')],state_effects=[dict(state_id=x,timing='immediate',description_zh='实际命令改变的状态') for x in c['effects']],observes=[],model_effects=dict(mode='none',description_zh='双挡板/基底浸提/探头/夹具/液位/电源关节另行场景验证；不证明真实接触力',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='到达实际面积/完成规定浸提行程，或有限时间窗终态' if async_ else '命令返回不推进时钟',completion_state_ids=['acquisition.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','trough','barriers','dipper','balance'],partial_failure_zh='取消保留有限表面分子、泄漏、挥发和半层沉积；冻结历史结果，停止运动目标'),errors=[dict(code=x,meaning_zh=x,side_effects='partial' if async_ and x in ['EOS_AREA_RANGE','NONPOSITIVE_SURFACE_TENSION','METHOD_TIMEOUT'] else 'none') for x in errors],verification=[],limitations=['真实实测等温线与未知准静态/转移动力学分开','原始负压与压力噪声未clip/平滑','OEM范围不代表达成准确度或水支持300mN/m'],source_ids=['manufacturer_specs','simulation_assumption'])
  if k in ['acquisition.start','film.spread','barrier.move','dipper.move','pressure.control','power.set']:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='宿主时间推进有限膜/溶剂、Wilhelmy观测、运动和采集') for x in PHYSICS_EFFECTS if x!='clock.time_s']
 mechanism=dict(component_ids=['trough','barrier_left','barrier_right','balance','wilhelmy_plate','dipper','substrate'],state_ids=list(slots),meaning_zh='真实等温线驱动有限单分子膜与Wilhelmy观测和LB面积转移',implementation='lumped',equations=[
 'A=W*(L-2*q), L=.364m,W=.075m,Amax=.0273m2,Amin=Amax/10.8; speed is explicitly per barrier, not manufacturer-confirmed total-gap convention.',
 'a=A/(n_surface*NA), Pi=piecewise-linear published compression isotherm(a) for measured-array records (source noise and negative readings preserved); for scalar-only literature records Pi=s*w*ln(1+exp((A0-a)/w)) with A0,s,w fixed at build time from published lift-off area, collapse pressure and two slope constraints (derived engineering EOS). Cs^-1=-a*dPi/da at fixed n,T,branch; observed -A*dPi_indicated/dA is apparent modulus if n changes.',
 'F=mg+P*(gamma0-Pi)*cos(theta_true)-rho*g*w*t*immersion; force readout has explicit gain/offset/drift and exponential sensor response. Pi_read=(F_clean_tare-F_corrected)/(P*cos(theta_assumed)). No direct truth-pressure output to agent.',
 'Finite solution spread transfers c*V mol to surface and V solvent to surface reservoir. Solvent evaporation dV/dt=-k_evap*V; latent=evaporatedV*rho_solvent*L, ideal bath supplies exactly same heat. No subphase evaporation or original10min preparation validation.',
 'Surface leakage dn/dt=-k_leak*n transfers finite molecules into subphase. Actual substrate swept area=facing_count*width*abs(delta immersed_length). Eligible wetting direction removes n*(1-exp(-eta*dAs/A)) mol; exactly same molecules accumulate on substrate. eta is explicit unmeasured engineering input, never returned as measured transfer ratio.',
 'Observed TR=(A_before-A_after)/As_actual; independently blank-corrected TR=(deltaA-blank_area_rate*elapsed)/As_actual. Hidden leakage never enters observed correction. Completed stroke count differs from equivalent_layers=sum(TR*actual_swept_area)/(full substrate face area) and true molecular coverage. Partial strokes are weighted by the actually swept fraction.',
 'Constant water inventory: delta_height=B*max(z-.063,0)/(.0273-B), capped at B*H/.0273 where B=substrate width*thickness. Full waterplane, not barrier area. Explicit0.5mm meniscus/freeboard allows maximum.32mm rise; no undocumented overflow or replenishment.',
 'Feedback uses only indicated pressure: dq/dt=clamp(G*(target-Pi_read)/(2W), permitted single-barrier speed), with declared deadband. Source/gamma domain checked before area changes; failures preserve prior physical effects. Fixed0.1s host partition.'
 ],parameters={},source_ids=['manufacturer_specs','manufacturer_langmuir_theory','manufacturer_wilhelmy_theory','statins2020_paper','expansion2026_raw','expansion2026_literature_scalars','simulation_assumption'],limitations=['Main library only runtime_usable references: measured compression arrays, or published characteristic scalars driving the derived softplus_condensation_eos_v1 (engineering curve, no LE/LC plateau or hysteresis). Crossinstrument/unknown condition reuse requires explicit engineering acceptance; no automatic thermal extrapolation.','Postcollapse molecular areas excluded by published collapse area/pressure or a measured-branch modulus-drop window, not simulated collapse dynamics.','Compression curve reused for expansion/constant-pressure transfer only with explicit reversible/quasistatic assumption; no invented hysteresis.','Finite subphase and dissolved leaked molecules are tracked; no solvent dissolution, monolayer domains or multilayer morphology dynamics.','Wilhelmy default platinum perimeter39.24mm; chosen width19.52mm andthickness.10mm are geometric engineering partition of that perimeter.','Isothermal external bath and supplied gamma/density are engineering inputs; buffer does not inherit purewater IAPWS gamma as verified truth.','Free-surface footprint loss by thin vertical substrate is neglected in the molecular-area approximation; displaced liquid level and buoyancy are represented.','Sensor noise spectrum, OEM0.1uN/m resolution and absolute accuracy not experimentally validated.','3D movement/contact/robot/hardware execution not established by runtime unit tests.'])
 spec=importlib.util.spec_from_file_location('_mediuml_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()});ann.annotate_export(SLUG,caps,slots,{'langmuir_film':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'subphase.load':(SUBPHASE,SUBPHASE_EXAMPLE),'solution.load':(SAMPLE,SAMPLE_EXAMPLE),'substrate.load':(SUBSTRATE,SUBSTRATE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'langmuir_film':mechanism})

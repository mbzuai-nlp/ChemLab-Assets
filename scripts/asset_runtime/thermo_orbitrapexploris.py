"""CHEM-088: finite H-ESI/IRM loading and conditional Orbitrap profile observations."""
from __future__ import annotations
import ast,copy,csv,hashlib,importlib.util,io,json,math
from pathlib import Path
import numpy as np
from scipy.signal import find_peaks
ROOT=Path(__file__).resolve().parents[2];SLUG='thermo_orbitrapexploris';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-088';PROTOCOL_VERSION=ASSET_VERSION='1.0.0'
TEMPLATE_REL='assets/expansion_batch04/t3_analytical/thermo_orbitrapexploris/output_templates/library.json'
U_KG=1.66053906892e-27;E_C=1.602176634e-19;NA=6.02214076e23
RESOLUTIONS=[15000,22500,30000,45000,60000,90000,120000];RATE_CEILINGS={15000:22.,30000:12.,60000:7.,120000:3.}
MAX_RECORDS=128;MAX_CURVES=32;MAX_PROFILE=200000;MAX_PEAKS=2000
class ActionRejected(ValueError):pass
BOOL={'type':'boolean'};TEXT={'type':'string'}
def number(u,lo=None,hi=None):
 s={'type':'number','x-unit':u}
 if lo is not None:s['minimum']=lo
 if hi is not None:s['maximum']=hi
 return s
def integer(lo,hi):return dict(number('1',lo,hi),type='integer')
def enum(v):return {'type':'string','enum':v}
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
def arr(s,lo=0,hi=20000):return dict(type='array',items=s,minItems=lo,maxItems=hi)
def obj(p):return dict(type='object',properties=p,required=list(p),additionalProperties=False)
def validate(s,v):
 if 'anyOf' in s:
  if v is None:return
  return validate(s['anyOf'][0],v)
 t=s.get('type')
 if t in ['number','integer']:
  if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or (t=='integer' and type(v) is not int):raise ActionRejected('INVALID_ARGUMENT')
  if not s.get('minimum',-math.inf)<=v<=s.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='boolean' and type(v) is not bool:raise ActionRejected('INVALID_ARGUMENT')
 elif t=='string' and not isinstance(v,str):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='object':
  if not isinstance(v,dict) or set(v)!=set(s['required']):raise ActionRejected('INVALID_ARGUMENT')
  for k,x in v.items():validate(s['properties'][k],x)
 elif t=='array':
  if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
  for x in v:validate(s['items'],x)
 if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')

PLANT=obj(dict(seed=integer(0,2**32-1),ambient_temperature_k=number('K',291.15,300.15),heat_capacity_j_k=number('J/K',.01,1e4),heat_loss_w_k=number('W/K',.001,1000),heater_power_w=number('W',.01,10000),maintenance_temperature_k=number('K',291.15,323.15),source_speed_m_s=number('m/s',.001,1),latch_speed_rad_s=number('s^-1',.01,20),lid_speed_rad_s=number('s^-1',.01,20),needle=enum(['low_flow','high_flow']),source_nitrogen_mol=number('mol',0,1000),collision_nitrogen_mol=number('mol',0,1000),source_gas_mol_s=number('mol/s',1e-9,1),collision_gas_mol_s=number('mol/s',1e-10,.1),ionization_efficiency=number('1',1e-12,.01),transfer_efficiency=number('1',.001,1),source_retention_fraction=number('1',0,.5),signal_per_charge=number('1',1e-12,1),baseline_relative=number('1',0,10),noise_relative=number('1',0,1),maximum_signal_relative=number('1',.001,1e6),mass_scale_fraction=number('1',-.00002,.00002),mass_offset_relative=number('1',-.001,.001),mass_drift_fraction_s=number('s^-1',-1e-7,1e-7),transient_15000_s=number('s',.025,1),transfer_s=number('s',.001,1),settling_s=number('s',.001,1),cleaning_s=number('s',.1,100),cleaning_rate_s=number('s^-1',.001,100),peak_threshold_fraction=number('1',.00001,.1),calibration_validity_s=number('s',1,86400)))
PLANT_EXAMPLE=dict(seed=880,ambient_temperature_k=298.15,heat_capacity_j_k=.2,heat_loss_w_k=.5,heater_power_w=200.,maintenance_temperature_k=298.65,source_speed_m_s=.3,latch_speed_rad_s=5.,lid_speed_rad_s=5.,needle='high_flow',source_nitrogen_mol=2.,collision_nitrogen_mol=1.,source_gas_mol_s=.001,collision_gas_mol_s=.00001,ionization_efficiency=1e-5,transfer_efficiency=.7,source_retention_fraction=.02,signal_per_charge=1e-6,baseline_relative=1e-5,noise_relative=1e-6,maximum_signal_relative=100.,mass_scale_fraction=8e-6,mass_offset_relative=.0003,mass_drift_fraction_s=1e-10,transient_15000_s=.03,transfer_s=.004,settling_s=.004,cleaning_s=.4,cleaning_rate_s=5.,peak_threshold_fraction=.001,calibration_validity_s=86400.)
METHOD=obj(dict(mode=enum(['ms1','ms2']),polarity=enum(['positive','negative']),mass_min=number('1',40,3000),mass_max=number('1',40,3000),resolution={'type':'integer','enum':RESOLUTIONS,'x-unit':'1'},precursor_mz=number('1',40,2500),isolation_width_relative=number('1',.4,5),collision_type=enum(['nce','absolute']),normalized_collision_energy=number('1',.01,2),collision_potential_v=number('V',1,200),agc_normalized=number('1',.00001,30),maximum_injection_s=number('s',.001,1),scans=integer(1,20),spray_voltage_v=number('V',-5500,6000),transfer_temperature_k=number('K',273.15,673.15),vaporizer_temperature_k=nullable(number('K',273.15,823.15)),flow_m3_s=number('m^3/s',1e-9/60,1e-6/60),sheath_gas_arbitrary=number('1',0,80),auxiliary_gas_arbitrary=number('1',0,25),sweep_gas_arbitrary=number('1',0,20),reference_condition_policy=enum(['matching_energy','acknowledged_fixed_pattern'])))
METHOD_EXAMPLE=dict(mode='ms1',polarity='positive',mass_min=40.,mass_max=500.,resolution=30000,precursor_mz=122.06,isolation_width_relative=1.,collision_type='nce',normalized_collision_energy=.35,collision_potential_v=25.,agc_normalized=.1,maximum_injection_s=.02,scans=1,spray_voltage_v=3500.,transfer_temperature_k=323.15,vaporizer_temperature_k=None,flow_m3_s=1e-9,sheath_gas_arbitrary=10.,auxiliary_gas_arbitrary=2.,sweep_gas_arbitrary=0.,reference_condition_policy='matching_energy')
COMPONENT=obj({'template_id':TEXT,'amount_mol':number('mol',1e-15,1e-3)})
SAMPLE=obj(dict(label=TEXT,kind=enum(['sample','standard','blank']),volume_m3=number('m^3',1e-9,1e-4),components=arr(COMPONENT,0,12),accept_transferred_reference=BOOL))
SAMPLE_EXAMPLE=dict(label='host_prepared_benzamide_solution',kind='sample',volume_m3=1e-7,components=[{'template_id':'sm808001','amount_mol':1e-8}],accept_transferred_reference=True)
BLANK_EXAMPLE=dict(label='host_solvent_blank',kind='blank',volume_m3=1e-7,components=[],accept_transferred_reference=True)
PEAK=obj(dict(mass_to_charge_relative=number('1'),mass_to_charge_kg_c=number('kg/C'),raw_mass_to_charge_relative=number('1'),intensity_relative=number('1',0),area_relative=number('1',0),fwhm_relative=number('1',0),mass_calibrated=BOOL))
SCAN=obj(dict(index=integer(0,19),start_s=number('s'),fill_start_s=number('s'),fill_end_s=number('s'),transient_start_s=number('s'),end_s=number('s'),complete=BOOL,agc_target_charges=number('1',0),agc_fill_charges=number('1',0),injection_limited=BOOL,mode=TEXT,polarity=TEXT,resolution_at_200=number('1'),profile_mass_to_charge_relative=arr(number('1'),0,MAX_PROFILE),profile_mass_to_charge_kg_c=arr(number('kg/C'),0,MAX_PROFILE),profile_intensity_relative=arr(number('1'),0,MAX_PROFILE),centroids=arr(PEAK,0,MAX_PEAKS),tic_relative=number('1',0),saturated=BOOL))
RESULT=obj(dict(record_id=TEXT,kind=TEXT,status=TEXT,complete=BOOL,scans=arr(SCAN,0,20),acquisition_signature=TEXT,mass_calibration_id=nullable(TEXT),method_json=TEXT,provenance_json=TEXT,quality=arr(TEXT,0,30)))
LEDGER=obj(dict(species_json=TEXT,registered_mol=number('mol'),reservoir_mol=number('mol'),source_residue_mol=number('mol'),trapped_parent_equivalent_mol=number('mol'),waste_mol=number('mol'),returned_mol=number('mol'),residual_mol=number('mol'),mass_residual_kg=number('kg'),registered_volume_m3=number('m^3'),reservoir_volume_m3=number('m^3'),cleaning_solvent_remaining_m3=number('m^3'),waste_volume_m3=number('m^3'),returned_volume_m3=number('m^3'),volume_residual_m3=number('m^3'),source_nitrogen_remaining_mol=number('mol'),source_nitrogen_used_mol=number('mol'),source_nitrogen_residual_mol=number('mol'),collision_nitrogen_remaining_mol=number('mol'),collision_nitrogen_used_mol=number('mol'),collision_nitrogen_residual_mol=number('mol'),injected_charge_c=number('C'),stored_charge_c=number('C'),neutralized_charge_c=number('C'),charge_residual_c=number('C'),heating_energy_j=number('J'),thermal_stored_j=number('J'),thermal_lost_j=number('J'),energy_residual_j=number('J')))
CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',example=None):CAPS[k]=(i,o,c,z,kind,vis,example or {})
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body','source_heater'],'宿主气体/响应/有限动力学初始化','configuration','evaluator',PLANT_EXAMPLE)
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['body'],'电子供电；断电保存部分过程',example={'enabled':True})
cap('source.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['spray_emitter','source_heater'],'HESI电压/气体/加热，有限升温供样',example={'enabled':True})
cap('source.fluid',obj({'connected':BOOL}),obj({'connected':BOOL}),['sample_inlet'],'宿主外液路连接状态；不是LC驱动','configuration','evaluator',{'connected':True})
cap('source.latch',obj({'side':enum(['left','right']),'unlocked':BOOL}),obj({'target_rad':number('rad',0,math.pi/2)}),['source_latch_left','source_latch_right'],'冷态双锁杆独立有限动作','device_command','evaluator',{'side':'left','unlocked':False})
cap('source.withdraw',obj({'withdrawn':BOOL}),obj({'target_m':number('m',0,.18)}),['source_housing'],'外液路断开/冷却/双解锁后的宿主托持直撤','device_command','evaluator',{'withdrawn':False})
cap('accessory_lid.set',obj({'opened':BOOL}),obj({'target_rad':number('rad',0,1.45)}),['accessory_lid'],'配件柜盖有限开合，不是真空门','device_command','evaluator',{'opened':False})
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_inlet'],'宿主有限配制溶液，真值受限','device_command','evaluator',SAMPLE_EXAMPLE)
cap('sample.unload',obj({'recipient_id':TEXT}),obj({'returned_volume_m3':number('m^3',0)}),['sample_inlet'],'仅交接瓶中剩余溶液','device_command','evaluator',{'recipient_id':'host_recovery'})
cap('method.configure',METHOD,obj({'configured':BOOL}),['quadrupole','ion_routing_multipole','orbitrap_analyzer'],'质量范围/真实分辨率档/AGC/固定HCD类型','configuration',example=METHOD_EXAMPLE)
cap('scan.start',obj({}),obj({'operation_id':TEXT}),['spray_emitter','charge_detector','ion_routing_multipole','c_trap','orbitrap_analyzer'],'有限IRM充填/C-trap转移/Orbitrap观察窗')
cap('measurement.stop',obj({}),obj({'phase':TEXT}),['orbitrap_analyzer'],'取消保留耗样与部分观测，余离子入中和仓')
cap('maintenance.clean',obj({'solvent_volume_m3':number('m^3',1e-8,1e-4)}),obj({'operation_id':TEXT}),['source_housing','spray_emitter'],'冷断电撤源后的宿主有限清洁代理','device_command','evaluator',{'solvent_volume_m3':1e-6})
cap('process.measure',obj({}),obj({'phase':TEXT,'stage':TEXT,'elapsed_s':number('s'),'progress':number('1',0,1),'tic_relative':nullable(number('1'))}),['display_panel'],'真实阶段和最近观测纯读','query')
cap('result.read',obj({}),RESULT,['display_panel'],'实际观察谱和校正标志，不返回样品模板真值','query')
cap('spectrum.read',obj({}),obj({'scans':arr(SCAN,0,20),'evidence':TEXT}),['orbitrap_analyzer'],'公开参考网格上的模拟profile及识峰','query')
cap('result.export',obj({'format':enum(['json','csv'])}),obj({'format':TEXT,'content':TEXT}),['display_panel'],'kg/C与原m/z、相对强度、实际窗口导出','query',example={'format':'json'})
cap('blank.read',obj({}),obj({'valid':BOOL,'blank_json':TEXT}),['display_panel'],'当前签名的实际观测空白','query')
cap('calibration.fit',obj({'curve_id':TEXT,'record_ids':arr(TEXT,3,12)}),obj({'curve_id':TEXT,'slope':number('1'),'intercept_relative':number('1'),'rms_fraction':number('1',0)}),['display_panel'],'三次以上实际标准观测的质量轴拟合，非浓度标线','analysis',example={'curve_id':'mass_positive','record_ids':['record_2','record_3','record_4']})
cap('calibration.select',obj({'curve_id':TEXT}),obj({'selected':TEXT}),['display_panel'],'选择同极性/响应/时效质量轴标定',example={'curve_id':'mass_positive'})
cap('calibration.read',obj({'curve_id':TEXT}),obj({'curve_json':TEXT}),['display_panel'],'实际质量观测与拟合残差','query',example={'curve_id':'mass_positive'})
cap('formula.search',obj({'record_id':TEXT,'scan_index':integer(0,19),'peak_index':integer(0,MAX_PEAKS-1),'tolerance_fraction':number('1',1e-8,.001)}),obj({'observed_mass_to_charge_relative':number('1'),'candidates_json':TEXT,'interpretation':TEXT}),['display_panel'],'从已观测校准质量搜索公开公式，不确证结构','analysis',example={'record_id':'record_5','scan_index':0,'peak_index':0,'tolerance_fraction':5e-6})
cap('records.list',obj({}),obj({'record_ids':arr(TEXT,0,MAX_RECORDS),'curve_ids':arr(TEXT,0,MAX_CURVES)}),['display_panel'],'实例独立工程缓存','query')
cap('records.read',obj({'record_id':TEXT}),RESULT,['display_panel'],'冻结方法和观察历史','query',example={'record_id':'record_1'})
cap('records.remove',obj({'record_id':TEXT}),obj({'removed':BOOL}),['display_panel'],'删缓存记录，操作收据仍受所有权限制',example={'record_id':'record_1'})
cap('template.read',obj({'substance_id':TEXT}),obj({'template_json':TEXT}),['display_panel'],'真实ESI/HCD和派生MS1条件查询','query',example={'substance_id':'sm808001'})
cap('product.read',obj({}),LEDGER,['sample_inlet','source_heater','ion_routing_multipole'],'受限物种当量/体积/气体/电荷/热账本','query','evaluator')
UNSUPPORTED={'hardware.connect','software.gui','lc.gradient','source.apci','source.faims','scan.dda','scan.dia','calibration.easy_ic','maintenance.transfer_tube','signal.raw_fft'}
for k in sorted(UNSUPPORTED):cap(k,obj({}),obj({}),['body'],'未实现或未安装：'+k)
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,origin='derived',vis='agent',parts=('display_panel',),write=None):SLOTS[k]=(s,u,origin,vis,list(parts),write);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','宿主已初始化');slot('power.enabled',BOOL,'1','电子供电','setpoint',parts=('body',),write=('power.set','enabled'))
for k,p,z,hi,u in [('accessory_lid.angle_rad','accessory_lid','配件柜盖角度',1.45,'rad'),('source.withdrawal_m','source_housing','源罩宿主托持退离',.18,'m'),('source_latch.left_angle_rad','source_latch_left','左锁杆角度',math.pi/2,'rad'),('source_latch.right_angle_rad','source_latch_right','右锁杆角度',math.pi/2,'rad')]:slot(k,number(u,0,hi),u,z,'scene',parts=(p,))
slot('source.enabled',BOOL,'1','HESI启用','setpoint',parts=('spray_emitter',),write=('source.set','enabled'));slot('source.fluid_connected',BOOL,'1','宿主外液路连接','setpoint',parts=('sample_inlet',));slot('source.actual_temperature_k',number('K',273.15,1000),'K','转移管等效热节点温度，工程热容',parts=('source_heater',));slot('source.spray_voltage_v',number('V',-5500,6000),'V','实际HESI电势，禁用0',parts=('spray_emitter',));slot('source.gas_enabled',BOOL,'1','HESI气体开关，非完整standby背景气流',parts=('spray_emitter',));slot('instrument.ready',BOOL,'1','供电/锁紧/气体/温度允许扫描');slot('method.mode',TEXT,'1','公开MS1/MS2模式');slot('method.polarity',TEXT,'1','公开极性');slot('resolution.actual_at_200',number('1',15000,120000),'1','名义R200，不是性能标定',parts=('orbitrap_analyzer',))
slot('source.vaporizer_actual_temperature_k',number('K',273.15,1000),'K','HESI vaporizer独立等效热节点，null目标表示关闭','derived',parts=('source_heater',))
slot('scan.phase',TEXT,'1','过程终态');slot('scan.stage',TEXT,'1','IRM填充/转移/瞬态/冷清洁阶段');slot('scan.elapsed_s',number('s',0),'s','操作实际时间');slot('scan.progress',number('1',0,1),'1','有限过程进度');slot('mass.current_ratio',nullable(number('1')),'1','最新观测基峰m/z，相对于u/e');slot('mass.current_kg_c',nullable(number('kg/C')),'kg/C','最新观测基峰质量电荷比');slot('detector.tic_relative',nullable(number('1',0)),'1','观察峰面积相对和，非真实计数',parts=('orbitrap_analyzer',));slot('detector.last_sample_time_s',nullable(number('s')),'s','实际观察末时刻');slot('agc.fill_charges',number('1',0),'1','宿主IRM充填电荷数代理',parts=('charge_detector',));slot('blank.valid',BOOL,'1','同方法实际空白可用');slot('calibration.ready',BOOL,'1','质量轴标定条件与时效有效');slot('storage.result_count',integer(0,MAX_RECORDS),'1','128记录工程容量');slot('storage.curve_count',integer(0,MAX_CURVES),'1','32标线工程容量');slot('safety.warning',TEXT,'1','有限安全状态，不暴露组成');slot('truth.ledger',LEDGER,'1','受限物种/体积/气体/电荷/热账本','process_truth','evaluator',('sample_inlet','source_heater','ion_routing_multipole'))
for k in ['power','status','scan']:
 slot('led.'+k+'_on',BOOL,'1','真实'+k+'灯点亮fallback，不表示完整色彩',parts=('body',));slot('led.'+k+'_rgb',arr(number('1',0,1),3,3),'1','真实'+k+'灯定性颜色，不是标定亮度',parts=('body',))
def _library():
 p=ROOT/TEMPLATE_REL
 return json.loads(p.read_text()) if p.exists() else {'compounds':{}}
def _move(x,t,v,dt):return x+max(-v*dt,min(v*dt,t-x))

class ThermoOrbitrapexplorisRuntime:
 def __init__(self,instance_id='orbitrap_01',library=None):
  self.instance_id=instance_id;self.library=copy.deepcopy(library) if library is not None else _library();self.templates=self.library.get('compounds',{});self._library_hash=hashlib.sha256(json.dumps(self.library,sort_keys=True,allow_nan=False).encode()).hexdigest();self.plant=None;self.method=copy.deepcopy(METHOD_EXAMPLE);self.time_s=0.;self.revision=0;self.powered=False;self.enabled=False;self.fluid=True;self.temperature=298.15;self.vaporizer_temperature=298.15;self.q={'accessory_lid.angle_rad':0.,'source.withdrawal_m':0.,'source_latch.left_angle_rad':0.,'source_latch.right_angle_rad':0.};self.targets=self.q.copy();self.warning='';self.phase='idle';self.stage='idle';self.elapsed_s=0.;self.reservoir={};self.residue={};self.trapped={};self.waste={};self.returned={};self.registered={};self.volume=0.;self.registered_volume=0.;self.waste_volume=0.;self.returned_volume=0.;self.source_gas=0.;self.collision_gas=0.;self.source_gas_used=0.;self.collision_gas_used=0.;self.charge_in=0.;self.charge_neutral=0.;self.heating=0.;self.heat_lost=0.;self.kind='blank';self.standard=None;self._records={};self._curves={};self._blanks={};self._standards={};self._selected=None;self._operation_counter=0;self._record_counter=0;self.operation_id=None;self._operation_records={};self._owners={};self._requests={};self._principals={'__public_agent__':'agent'};self._grid_cache={};self.scans=[];self._last_scan=None;self._result_cache=None;self._stage_elapsed=0.;self._scan_start=0.;self._fill_start=0.;self._fill_end=0.;self._transient_start=0.;self._run_method=None;self._run_key='';self._run_curve=None;self._run_kind='';self._run_standard=None;self._run_quality=[];self._target_charges=0.;self._fill_charges=0.;self._injection_limited=False;self._run_grid=None;self._clean_volume=0.;self._clean_used=0.;self._clean_remaining=0.;self._operation_kind=None
 def register_principal(self,principal_id,visibility):
  if not isinstance(principal_id,str) or not principal_id or visibility not in ['agent','debug','evaluator']:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals and self._principals[principal_id]!=visibility:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return ThermoOrbitrapexplorisSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _reference(self,i):
  r=self.templates.get(i)
  if not r:raise ActionRejected('UNKNOWN_TEMPLATE')
  if not r.get('physical_input_eligible') or not r.get('derived_ms1') or not r.get('ms2'):raise ActionRejected('REFERENCE_QUERY_ONLY')
  if r.get('precursor_charge') not in [-1,1] or r.get('ionization')!='ESI':raise ActionRejected('UNSUPPORTED_REFERENCE')
  return r
 def _seated(self):return self.q['source.withdrawal_m']==0 and self.targets['source.withdrawal_m']==0 and all(self.q['source_latch.'+s+'_angle_rad']==0 and self.targets['source_latch.'+s+'_angle_rad']==0 for s in ['left','right'])
 def _ready(self):return bool(self.plant and self.powered and self.enabled and self.fluid and self._seated() and self.source_gas>0 and self.collision_gas>0 and abs(self.temperature-max(self.plant['ambient_temperature_k'],self.method['transfer_temperature_k']))<2. and (self.method['vaporizer_temperature_k'] is None or abs(self.vaporizer_temperature-max(self.plant['ambient_temperature_k'],self.method['vaporizer_temperature_k']))<2.))
 def _cold(self):return bool(self.plant and max(self.temperature,self.vaporizer_temperature)<=self.plant['maintenance_temperature_k'])
 def _signature(self,method=None):return hashlib.sha256(json.dumps({'method':method or self.method,'response':self.plant,'library_sha':self._library_hash},sort_keys=True,allow_nan=False).encode()).hexdigest()
 def _mass_key(self,polarity=None):
  response={k:self.plant[k] for k in ['mass_scale_fraction','mass_offset_relative','mass_drift_fraction_s']} if self.plant else {}
  return hashlib.sha256(json.dumps({'polarity':polarity or self.method['polarity'],'response':response,'library_sha':self._library_hash},sort_keys=True).encode()).hexdigest()
 def _curve(self):
  c=self._curves.get(self._selected)
  return c if c and c['mass_signature']==self._mass_key() and self.time_s-c['time_s']<=self.plant['calibration_validity_s'] else None
 def _blank(self):return self._blanks.get(self._signature()) if self.plant else None
 def _duration(self):
  if not self.plant:return 0.
  if self._operation_kind=='clean':return self.plant['cleaning_s']
  m=self._run_method or self.method
  return m['scans']*(m['maximum_injection_s']+self.plant['transfer_s']+self._transient(m)+self.plant['settling_s'])
 def _transient(self,m):return max(self.plant['transient_15000_s']*m['resolution']/15000,1/RATE_CEILINGS[m['resolution']] if m['resolution'] in RATE_CEILINGS else 0.)
 def _progress(self):return 1. if self.phase=='succeeded' else min(1.,self.elapsed_s/max(self._duration(),1e-30))
 def _charge(self):return sum(n*abs(self.templates[i]['precursor_charge'])*NA for i,n in self.trapped.items())
 def _leds(self):
  off=[0.,0.,0.];green=[0.,1.,0.];white=[1.,1.,1.];yellow=[1.,.65,0.]
  status=off if not self.powered else yellow if self.warning else white if self.enabled and not self._ready() else green
  scan=off if not self.powered or not self.enabled and self.phase!='running' else white if self.enabled and not self._ready() else ([0.,.2,1.] if self.method['mode']=='ms1' else [.6,0.,1.]) if self.phase=='running' else status
  return {'led.power_rgb':green if self.powered else off,'led.status_rgb':status,'led.scan_rgb':scan,'led.power_on':self.powered,'led.status_on':any(status),'led.scan_on':any(scan)}
 def _ledger(self):
  rows={};massres=0.
  for i in sorted(self.registered):
   v=dict(registered_mol=self.registered[i],reservoir_mol=self.reservoir.get(i,0.),source_residue_mol=self.residue.get(i,0.),trapped_parent_equivalent_mol=self.trapped.get(i,0.),waste_mol=self.waste.get(i,0.),returned_mol=self.returned.get(i,0.));v['residual_mol']=v['registered_mol']-sum(v[k] for k in v if k!='registered_mol');v['molar_mass_kg_mol']=self.templates[i]['molar_mass_kg_mol'];massres+=v['residual_mol']*v['molar_mass_kg_mol'];rows[i]=v
  sums={k:sum(x[k] for x in rows.values()) for k in ['registered_mol','reservoir_mol','source_residue_mol','trapped_parent_equivalent_mol','waste_mol','returned_mol','residual_mol']};stored=(self.temperature+self.vaporizer_temperature-2*self.plant['ambient_temperature_k'])*self.plant['heat_capacity_j_k'] if self.plant else 0.;q=self._charge()*E_C
  return dict(species_json=json.dumps(rows,sort_keys=True),**sums,mass_residual_kg=massres,registered_volume_m3=self.registered_volume,reservoir_volume_m3=self.volume,cleaning_solvent_remaining_m3=self._clean_remaining,waste_volume_m3=self.waste_volume,returned_volume_m3=self.returned_volume,volume_residual_m3=self.registered_volume-self.volume-self._clean_remaining-self.waste_volume-self.returned_volume,source_nitrogen_remaining_mol=self.source_gas,source_nitrogen_used_mol=self.source_gas_used,source_nitrogen_residual_mol=(self.plant['source_nitrogen_mol'] if self.plant else 0)-self.source_gas-self.source_gas_used,collision_nitrogen_remaining_mol=self.collision_gas,collision_nitrogen_used_mol=self.collision_gas_used,collision_nitrogen_residual_mol=(self.plant['collision_nitrogen_mol'] if self.plant else 0)-self.collision_gas-self.collision_gas_used,injected_charge_c=self.charge_in,stored_charge_c=q,neutralized_charge_c=self.charge_neutral,charge_residual_c=self.charge_in-q-self.charge_neutral,heating_energy_j=self.heating,thermal_stored_j=stored,thermal_lost_j=self.heat_lost,energy_residual_j=self.heating-stored-self.heat_lost)
 def read(self,visibility='agent'):
  if visibility not in ['agent','debug','evaluator']:raise ActionRejected('ACCESS_DENIED')
  last=self._last_scan;peak=max(last['centroids'],key=lambda p:p['intensity_relative']) if last and last['centroids'] else None
  v=dict(self.q);v.update({'plant.configured':self.plant is not None,'power.enabled':self.powered,'source.enabled':self.enabled,'source.fluid_connected':self.fluid,'source.actual_temperature_k':self.temperature,'source.vaporizer_actual_temperature_k':self.vaporizer_temperature,'source.spray_voltage_v':self.method['spray_voltage_v'] if self.enabled and self.powered and self._seated() else 0.,'source.gas_enabled':bool(self.enabled and self.source_gas>0),'instrument.ready':self._ready(),'method.mode':self.method['mode'],'method.polarity':self.method['polarity'],'resolution.actual_at_200':self.method['resolution'],'scan.phase':self.phase,'scan.stage':self.stage,'scan.elapsed_s':self.elapsed_s,'scan.progress':self._progress(),'mass.current_ratio':peak['mass_to_charge_relative'] if peak else None,'mass.current_kg_c':peak['mass_to_charge_kg_c'] if peak else None,'detector.tic_relative':last['tic_relative'] if last else None,'detector.last_sample_time_s':last['end_s'] if last else None,'agc.fill_charges':self._charge(),'blank.valid':self._blank() is not None,'calibration.ready':self._curve() is not None,'storage.result_count':len(self._records),'storage.curve_count':len(self._curves),'safety.warning':self.warning,**self._leds()})
  if visibility!='agent':v['truth.ledger']=self._ledger()
  return v
 def invoke(self,capability,**arguments):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],arguments)
  if capability in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  result=self._invoke(capability,arguments);validate(CAPS[capability][1],result)
  if CAPS[capability][4] not in ['query','analysis'] or capability=='calibration.fit':self.revision+=1
  return copy.deepcopy(result)
 def _invoke(self,k,a):
  if k=='template.read':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_TEMPLATE')
   return {'template_json':json.dumps(self.templates[a['substance_id']],sort_keys=True,ensure_ascii=False,allow_nan=False)}
  if k=='plant.configure':
   if self.plant is not None or self.time_s!=0:raise ActionRejected('INITIALIZATION_ONLY')
   if a['maintenance_temperature_k']<a['ambient_temperature_k']:raise ActionRejected('INVALID_ARGUMENT')
   self.plant=copy.deepcopy(a);self.temperature=a['ambient_temperature_k'];self.vaporizer_temperature=self.temperature;self.source_gas=a['source_nitrogen_mol'];self.collision_gas=a['collision_nitrogen_mol'];self.rng=np.random.default_rng(a['seed']);return {'configured':True}
  if k=='product.read':return self._ledger()
  if k=='process.measure':return dict(phase=self.phase,stage=self.stage,elapsed_s=self.elapsed_s,progress=self._progress(),tic_relative=self._last_scan['tic_relative'] if self._last_scan else None)
  if k=='records.list':return {'record_ids':list(self._records),'curve_ids':list(self._curves)}
  if k in ['records.read','records.remove']:
   if a['record_id'] not in self._records:raise ActionRejected('UNKNOWN_RECORD')
   if k=='records.read':return self._records[a['record_id']]
   self._idle();del self._records[a['record_id']];return {'removed':True}
  if k=='result.read':
   if self._result_cache is None:raise ActionRejected('NO_RESULT')
   return self._result_cache
  if k=='spectrum.read':return {'scans':self.scans,'evidence':'synthetic image-current profile surrogate; measured reference MS2 and derived MS1 are distinct; not OEM electronics or Fourier transient'}
  if k=='result.export':
   if self._result_cache is None:raise ActionRejected('NO_RESULT')
   if a['format']=='json':return {'format':'json','content':json.dumps(self._result_cache,sort_keys=True,allow_nan=False)}
   stream=io.StringIO();w=csv.writer(stream);w.writerow(['scan','start_s','end_s','mass_to_charge_relative','mass_to_charge_kg_c','intensity_relative'])
   for s in self._result_cache['scans']:
    for x,y,z in zip(s['profile_mass_to_charge_relative'],s['profile_mass_to_charge_kg_c'],s['profile_intensity_relative']):w.writerow([s['index'],s['transient_start_s'],s['end_s'],x,y,z])
   return {'format':'csv','content':stream.getvalue()}
  if k=='blank.read':return {'valid':self._blank() is not None,'blank_json':json.dumps(self._blank(),sort_keys=True,allow_nan=False)}
  if k=='calibration.read':
   if a['curve_id'] not in self._curves:raise ActionRejected('UNKNOWN_CURVE')
   return {'curve_json':json.dumps(self._curves[a['curve_id']],sort_keys=True,allow_nan=False)}
  if k=='formula.search':return self._formula(a)
  if k=='measurement.stop':
   if self.phase=='running':self._stop('cancelled')
   return {'phase':self.phase}
  if k=='power.set':
   if self.plant is None:raise ActionRejected('PLANT_REQUIRED')
   if not a['enabled'] and self.phase=='running':self._stop('cancelled')
   self.powered=a['enabled']
   if not self.powered:self.enabled=False
   return {'enabled':self.powered}
  if self.plant is None:raise ActionRejected('PLANT_REQUIRED')
  if k=='source.set':
   if a['enabled'] and (not self.powered or not self._seated() or not self.fluid):raise ActionRejected('SOURCE_INTERLOCK')
   if a['enabled'] and min(self.source_gas,self.collision_gas)<=0:raise ActionRejected('GAS_REQUIRED')
   if not a['enabled'] and self.phase=='running':self._stop('cancelled')
   self.enabled=a['enabled'];self.warning='';return {'enabled':self.enabled}
  self._idle()
  if k=='source.fluid':
   if self.enabled:raise ActionRejected('SOURCE_ACTIVE')
   self.fluid=a['connected'];return {'connected':self.fluid}
  if k=='source.latch':
   if self.enabled or not self._cold():raise ActionRejected('COLD_SOURCE_REQUIRED')
   if self.fluid:raise ActionRejected('FLUID_DISCONNECT_REQUIRED')
   if self.q['source.withdrawal_m']!=0 or self.targets['source.withdrawal_m']!=0:raise ActionRejected('SOURCE_NOT_SEATED')
   q='source_latch.'+a['side']+'_angle_rad';t=math.pi/2 if a['unlocked'] else 0.;self.targets[q]=t;return {'target_rad':t}
  if k=='source.withdraw':
   if self.enabled or not self._cold():raise ActionRejected('COLD_SOURCE_REQUIRED')
   if self.fluid:raise ActionRejected('FLUID_DISCONNECT_REQUIRED')
   if any(abs(self.q['source_latch.'+s+'_angle_rad']-math.pi/2)>1e-9 or self.targets['source_latch.'+s+'_angle_rad']!=math.pi/2 for s in ['left','right']):raise ActionRejected('BOTH_LATCHES_REQUIRED')
   self.targets['source.withdrawal_m']=.18 if a['withdrawn'] else 0.;self.enabled=False;return {'target_m':self.targets['source.withdrawal_m']}
  if k=='accessory_lid.set':self.targets['accessory_lid.angle_rad']=1.45 if a['opened'] else 0.;return {'target_rad':self.targets['accessory_lid.angle_rad']}
  if k=='method.configure':
   if a['mass_min']>=a['mass_max'] or a['mass_max']>15*a['mass_min']:raise ActionRejected('MASS_RANGE_RATIO')
   if a['mode']=='ms1' and a['agc_normalized']>10:raise ActionRejected('AGC_RANGE')
   if a['polarity']=='positive' and a['spray_voltage_v']<0 or a['polarity']=='negative' and a['spray_voltage_v']>0:raise ActionRejected('POLARITY_VOLTAGE')
   minwidth=next(v for end,v in [(400,.4),(700,.7),(1000,1.),(1500,1.5),(2000,2.),(2500,3.)] if a['precursor_mz']<=end)
   if a['mode']=='ms2' and a['isolation_width_relative']<minwidth:raise ActionRejected('ISOLATION_WIDTH')
   flow=a['flow_m3_s']*60/1e-9
   if self.plant['needle']=='low_flow' and not 1-1e-9<=flow<=10+1e-9 or self.plant['needle']=='high_flow' and not 10-1e-9<=flow<=1000+1e-9:raise ActionRejected('NEEDLE_FLOW_RANGE')
   if a['vaporizer_temperature_k'] is not None and a['vaporizer_temperature_k']>373.15 and a['auxiliary_gas_arbitrary']<5:raise ActionRejected('AUXILIARY_GAS_REQUIRED')
   self.method=copy.deepcopy(a);return {'configured':True}
  if k=='sample.load':
   if self.enabled:raise ActionRejected('SOURCE_ACTIVE')
   if not a['accept_transferred_reference']:raise ActionRejected('SOURCE_TRANSFER_ACCEPTANCE_REQUIRED')
   ids=[x['template_id'] for x in a['components']]
   if len(set(ids))!=len(ids):raise ActionRejected('DUPLICATE_COMPONENT')
   for i in ids:self._reference(i)
   if a['kind']=='blank' and ids or a['kind']!='blank' and not ids:raise ActionRejected('INVALID_SAMPLE_KIND')
   if a['kind']=='standard' and (len(ids)!=1 or not self.templates[ids[0]].get('mass_reference_eligible')):raise ActionRejected('MASS_REFERENCE_REQUIRED')
   self._return_reservoir();self.reservoir={x['template_id']:x['amount_mol'] for x in a['components']};self.volume=a['volume_m3'];self.registered_volume+=self.volume
   for i,n in self.reservoir.items():self.registered[i]=self.registered.get(i,0.)+n
   self.kind=a['kind'];self.standard=ids[0] if a['kind']=='standard' else None;return {'loaded':True}
  if k=='sample.unload':
   if self.enabled:raise ActionRejected('SOURCE_ACTIVE')
   if not a['recipient_id']:raise ActionRejected('INVALID_ARGUMENT')
   v=self.volume;self._return_reservoir();return {'returned_volume_m3':v}
  if k=='scan.start':return self._start()
  if k=='calibration.fit':return self._fit(a)
  if k=='calibration.select':
   c=self._curves.get(a['curve_id'])
   if not c:raise ActionRejected('UNKNOWN_CURVE')
   if c['mass_signature']!=self._mass_key() or self.time_s-c['time_s']>self.plant['calibration_validity_s']:raise ActionRejected('CALIBRATION_SIGNATURE')
   self._selected=a['curve_id'];return {'selected':self._selected}
  if k=='maintenance.clean':
   if self.powered or not self._cold() or self.q['source.withdrawal_m']!=.18 or self.targets['source.withdrawal_m']!=.18 or self.fluid:raise ActionRejected('COLD_REMOVED_SOURCE_REQUIRED')
   self._new_operation('clean');self.stage='cleaning';self._clean_volume=a['solvent_volume_m3'];self._clean_used=0.;self.returned_volume+=self._clean_remaining;self._clean_remaining=self._clean_volume;self.registered_volume+=self._clean_volume;return {'operation_id':self.operation_id}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _return_reservoir(self):
  for i,n in self.reservoir.items():self.returned[i]=self.returned.get(i,0.)+n
  self.reservoir={};self.returned_volume+=self.volume;self.volume=0.
 def _new_operation(self,kind):
  self._operation_counter+=1;self.operation_id='operation_'+str(self._operation_counter);self._operation_kind=kind;self.phase='running';self.elapsed_s=0.;self._stage_elapsed=0.;self.warning=''
 def _transmission(self,i,m):
  r=self.templates[i]
  if r['polarity']!=m['polarity']:return 0.
  x=np.asarray(r['derived_ms1']['mass_to_charge_relative']);prob=np.asarray(r['derived_ms1']['probability'])
  if m['mode']=='ms2':
   # Only the source monoisotopic-precursor spectrum is modeled; other isotope-specific fragmentation is unknown.
   mono=r['derived_precursor_mz']
   return float(prob[int(np.argmin(abs(x-mono)))]) if abs(mono-m['precursor_mz'])<=m['isolation_width_relative']/2 else 0.
  lo=min(m['mass_min'],2500)
  return float(np.sum(prob[(x>=lo)&(x<=m['mass_max'])]))
 def _eligible(self,i,m):return self._transmission(i,m)>0
 def _pattern(self,r,m):
  if r['polarity']!=m['polarity']:return np.array([]),np.array([])
  if m['mode']=='ms2':
   if abs(r['derived_precursor_mz']-m['precursor_mz'])>m['isolation_width_relative']/2:return np.array([]),np.array([])
   ce=r['collision_energy'];match=ce.get('single_energy_verified') is True and m['collision_type']=='nce' and abs(ce.get('fraction',-1)-m['normalized_collision_energy'])<1e-12
   if not match and m['reference_condition_policy']!='acknowledged_fixed_pattern':return np.array([]),np.array([])
   s=r['ms2']
  else:s=r['derived_ms1']
  x=np.asarray(s['mass_to_charge_relative'],float);relative=np.asarray(s['relative_intensity'],float);use=(x>=m['mass_min'])&(x<=m['mass_max'])&(relative>=1e-4)
  y=relative if m['mode']=='ms2' else np.asarray(s['probability'],float)/max(self._transmission(r['substance_id'],m),1e-30)
  return x[use],y[use]
 def _sigma(self,x,m):return np.asarray(x)**1.5/(m['resolution']*math.sqrt(200)*2*math.sqrt(2*math.log(2)))
 def _grid(self,m):
  key=json.dumps(m,sort_keys=True)
  if key in self._grid_cache:return self._grid_cache[key]
  # Entire public library determines sampling positions, never the hidden mixture.
  centers=[]
  for r in self.templates.values():
   x,_=self._pattern(r,m);centers.extend(x.tolist())
  parts=[np.linspace(m['mass_min'],m['mass_max'],257)]
  for x in sorted(set(centers)):
   sigma=float(self._sigma(x,m));span=6*sigma+.001+abs(x)*.00005;n=min(501,max(25,int(math.ceil(2*span/(sigma/3)))+1));parts.append(np.linspace(max(m['mass_min'],x-span),min(m['mass_max'],x+span),n))
  grid=np.unique(np.concatenate(parts))
  if len(grid)>MAX_PROFILE:raise ActionRejected('PROFILE_CAPACITY')
  self._grid_cache[key]=grid;return grid
 def _start(self):
  if not self._ready():raise ActionRejected('INSTRUMENT_NOT_READY')
  if self.volume<=0:raise ActionRejected('SAMPLE_REQUIRED')
  if len(self._records)>=MAX_RECORDS:raise ActionRejected('RESULT_CAPACITY')
  if self.method['mode']=='ms2' and self.method['reference_condition_policy']=='matching_energy' and not any(len(self._pattern(r,self.method)[0]) for r in self.templates.values()):raise ActionRejected('REFERENCE_CONDITION_REQUIRED')
  grid=self._grid(self.method);self._new_operation('scan');self.stage='filling';self.scans=[];self._last_scan=None;self._result_cache=None;self._run_method=copy.deepcopy(self.method);self._run_key=self._signature();self._run_curve=copy.deepcopy(self._curve());self._run_kind=self.kind;self._run_standard=self.standard;self._run_grid=grid;self._run_quality=[];self._begin_scan();return {'operation_id':self.operation_id}
 def _begin_scan(self):
  self.stage='filling';self._stage_elapsed=0.;self._scan_start=self.time_s;self._fill_start=self.time_s;self._fill_end=self.time_s;self._transient_start=self.time_s;self._target_charges=(1e6 if self._run_method['mode']=='ms1' else 1e5)*self._run_method['agc_normalized'];self._fill_charges=0.;self._injection_limited=False
 def _ion_rate(self):
  if self.volume<=0:return 0.
  p=self.plant;m=self._run_method
  return sum(n*self._transmission(i,m) for i,n in self.reservoir.items())/self.volume*m['flow_m3_s']*(1-p['source_retention_fraction'])*p['ionization_efficiency']*p['transfer_efficiency']*NA
 def _dump(self):
  self.charge_neutral+=self._charge()*E_C
  for i,n in self.trapped.items():self.waste[i]=self.waste.get(i,0.)+n
  self.trapped={}
 def _physical(self,dt):
  p=self.plant
  for k in self.q:
   speed=p['source_speed_m_s'] if k=='source.withdrawal_m' else p['lid_speed_rad_s'] if k=='accessory_lid.angle_rad' else p['latch_speed_rad_s'];self.q[k]=_move(self.q[k],self.targets[k],speed,dt)
  for attr,setting in [('temperature',self.method['transfer_temperature_k']),('vaporizer_temperature',self.method['vaporizer_temperature_k'])]:
   target=max(p['ambient_temperature_k'],setting) if self.powered and self.enabled and setting is not None else p['ambient_temperature_k'];h=p['heat_loss_w_k'];c=p['heat_capacity_j_k'];power=min(p['heater_power_w'],max(0.,h*(target-p['ambient_temperature_k']))) if self.enabled and self.powered else 0.;before=getattr(self,attr);equilibrium=p['ambient_temperature_k']+power/h;after=equilibrium+(before-equilibrium)*math.exp(-h*dt/c);setattr(self,attr,after);de=power*dt;stored=(after-before)*c;self.heating+=de;self.heat_lost+=de-stored
  if self.enabled and self.powered:
   take=min(self.source_gas,p['source_gas_mol_s']*dt);self.source_gas-=take;self.source_gas_used+=take;take=min(self.collision_gas,p['collision_gas_mol_s']*dt);self.collision_gas-=take;self.collision_gas_used+=take
  if self.phase=='running' and self._operation_kind=='scan' and self.enabled and self.volume>0:
   v=min(self.volume,self._run_method['flow_m3_s']*dt);fraction=v/self.volume;self.volume-=v;self.waste_volume+=v
   for i in list(self.reservoir):
    n=self.reservoir[i]*fraction;self.reservoir[i]-=n;retained=n*p['source_retention_fraction'];self.residue[i]=self.residue.get(i,0.)+retained;trapped=(n-retained)*p['ionization_efficiency']*p['transfer_efficiency']*self._transmission(i,self._run_method) if self.stage=='filling' else 0.;self.trapped[i]=self.trapped.get(i,0.)+trapped;self.charge_in+=trapped*NA*E_C;self.waste[i]=self.waste.get(i,0.)+n-retained-trapped
  if self.phase=='running' and self._operation_kind=='clean':
   fraction=-math.expm1(-p['cleaning_rate_s']*dt)
   for i in self.residue:
    n=self.residue[i]*fraction;self.residue[i]-=n;self.waste[i]=self.waste.get(i,0.)+n
   v=min(self._clean_volume-self._clean_used,self._clean_volume*dt/p['cleaning_s']);self._clean_used+=v;self._clean_remaining-=v;self.waste_volume+=v
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(float,int)) or not math.isfinite(dt_s) or dt_s<=0:raise ActionRejected('INVALID_STEP')
  if self.plant is None:self.time_s+=dt_s;self.revision+=1;return self.read('evaluator')
  remaining=float(dt_s)
  while remaining>1e-12:
   limit=remaining
   if self.phase=='running':
    if self._operation_kind=='clean':limit=min(limit,self.plant['cleaning_s']-self.elapsed_s)
    elif self.stage=='filling':
     limit=min(limit,self._run_method['maximum_injection_s']-self._stage_elapsed);rate=self._ion_rate()
     if rate>0:limit=min(limit,max(0.,self._target_charges-self._charge())/rate)
    elif self.stage=='transfer':limit=min(limit,self.plant['transfer_s']-self._stage_elapsed)
    elif self.stage=='transient':limit=min(limit,self._transient(self._run_method)-self._stage_elapsed)
    elif self.stage=='settling':limit=min(limit,self.plant['settling_s']-self._stage_elapsed)
    if self._operation_kind=='scan':limit=min(limit,self.volume/self._run_method['flow_m3_s'])
   if self.enabled:limit=min(limit,self.source_gas/self.plant['source_gas_mol_s'],self.collision_gas/self.plant['collision_gas_mol_s'])
   if limit>1e-12:
    self._physical(limit);self.time_s+=limit;remaining-=limit
    if self.phase=='running':self.elapsed_s+=limit;self._stage_elapsed+=limit
   if self.enabled and min(self.source_gas,self.collision_gas)<1e-14:
    self.warning='GAS_EXHAUSTED';self.enabled=False
    if self.phase=='running':self._stop('failed')
   elif self.phase=='running' and self._operation_kind=='scan' and self.volume<1e-18:self.warning='SAMPLE_EXHAUSTED';self._stop('failed')
   elif self.phase=='running':
    if self._operation_kind=='clean' and self.elapsed_s>=self.plant['cleaning_s']-1e-10:self._stop('succeeded')
    elif self.stage=='filling' and (self._charge()>=self._target_charges-1e-7 or self._stage_elapsed>=self._run_method['maximum_injection_s']-1e-12):self._fill_charges=self._charge();self._injection_limited=self._fill_charges<self._target_charges*(1-1e-8);self._fill_end=self.time_s;self.stage='transfer';self._stage_elapsed=0.
    elif self.stage=='transfer' and self._stage_elapsed>=self.plant['transfer_s']-1e-12:self.stage='transient';self._stage_elapsed=0.;self._transient_start=self.time_s
    elif self.stage=='transient' and self._stage_elapsed>=self._transient(self._run_method)-1e-12:self._observe(True);self._dump();self.stage='settling';self._stage_elapsed=0.
    elif self.stage=='settling' and self._stage_elapsed>=self.plant['settling_s']-1e-12:
     if len(self.scans)>=self._run_method['scans']:self._stop('succeeded')
     else:self._begin_scan()
   elif limit<=1e-12:raise RuntimeError('nonprogressing finite integrator')
  self.revision+=1;return self.read('evaluator')

 def _observe(self,complete):
  m=self._run_method;grid=self._run_grid;p=self.plant;signal=np.full(len(grid),p['baseline_relative']);mid=(self._transient_start+self.time_s)/2;scale=1+p['mass_scale_fraction']+p['mass_drift_fraction_s']*mid
  for i,n in self.trapped.items():
   if n<=0:continue
   xs,ys=self._pattern(self.templates[i],m)
   for x,y in zip(xs,ys):
    center=x*scale+p['mass_offset_relative'];sigma=float(self._sigma(x,m));l=np.searchsorted(grid,center-8*sigma);h=np.searchsorted(grid,center+8*sigma)
    signal[l:h]+=n*NA*p['signal_per_charge']*float(y)*np.exp(-.5*((grid[l:h]-center)/sigma)**2)
  observed_duration=max(0.,self.time_s-self._transient_start);noise=p['noise_relative']*math.sqrt(self._transient(m)/max(observed_duration,1e-12));signal+=self.rng.normal(0,noise,len(signal));saturated=bool(np.any(signal>p['maximum_signal_relative']));signal=np.minimum(signal,p['maximum_signal_relative'])
  blank=self._blanks.get(self._run_key);baseline=np.full(len(grid),p['baseline_relative'])
  if blank and blank['scans']:
   bs=blank['scans'][-1];bx=np.asarray(bs['profile_mass_to_charge_relative']);by=np.asarray(bs['profile_intensity_relative']);bc=self._curves.get(blank['mass_calibration_id'])
   if bc:bx=(bx-bc['intercept_relative'])/bc['slope']
   baseline=np.interp(grid,bx,by)
  corrected=signal-baseline;threshold=max(6*noise,float(np.max(corrected))*p['peak_threshold_fraction'],1e-12);indices,_=find_peaks(corrected,height=threshold,prominence=threshold);rawpeaks=[]
  for idx in indices:
   sigma=float(self._sigma(grid[idx],m));l=idx;r=idx
   while l>0 and grid[idx]-grid[l-1]<3*sigma and grid[l]-grid[l-1]<sigma*2:l-=1
   while r+1<len(grid) and grid[r+1]-grid[idx]<3*sigma and grid[r+1]-grid[r]<sigma*2:r+=1
   if r-l<3:continue
   xx=grid[l:r+1];yy=np.maximum(corrected[l:r+1],0);area=float(np.trapz(yy,xx))
   if area<=0:continue
   center=float(np.trapz(xx*yy,xx)/area)
   # Densely overlapping public-reference grids do not create extra observations.
   if rawpeaks and abs(center-rawpeaks[-1][0])<sigma:continue
   half=float(corrected[idx])/2;left=idx;right=idx
   while left>l and corrected[left]>half:left-=1
   while right<r and corrected[right]>half:right+=1
   width=max(0.,float(grid[right]-grid[left]));rawpeaks.append((center,float(max(corrected[idx],0)),area,width))
  if len(rawpeaks)>MAX_PEAKS:rawpeaks=sorted(sorted(rawpeaks,key=lambda x:x[1],reverse=True)[:MAX_PEAKS])
  curve=self._run_curve;peaks=[]
  for x,height,area,width in rawpeaks:
   calibrated=bool(curve and curve['minimum_observed_mz']<=x<=curve['maximum_observed_mz']);value=curve['slope']*x+curve['intercept_relative'] if curve else x
   peaks.append(dict(mass_to_charge_relative=value,mass_to_charge_kg_c=value*U_KG/E_C,raw_mass_to_charge_relative=x,intensity_relative=height,area_relative=area,fwhm_relative=width*(curve['slope'] if curve else 1.),mass_calibrated=calibrated))
  axis=grid*curve['slope']+curve['intercept_relative'] if curve else grid
  scan=dict(index=len(self.scans),start_s=self._scan_start,fill_start_s=self._fill_start,fill_end_s=self._fill_end,transient_start_s=self._transient_start,end_s=self.time_s,complete=complete,agc_target_charges=self._target_charges,agc_fill_charges=self._fill_charges,injection_limited=self._injection_limited,mode=m['mode'],polarity=m['polarity'],resolution_at_200=m['resolution'],profile_mass_to_charge_relative=axis.tolist(),profile_mass_to_charge_kg_c=(axis*U_KG/E_C).tolist(),profile_intensity_relative=signal.tolist(),centroids=peaks,tic_relative=sum(x['area_relative'] for x in peaks),saturated=saturated)
  validate(SCAN,scan);self.scans.append(scan);self._last_scan=scan
 def _operation_snapshot(self):
  return dict(operation_id=self.operation_id,status='running' if self.phase=='running' else self.phase,progress=self._progress(),result=copy.deepcopy(self._result_cache) if self._operation_kind=='scan' and self.phase!='running' else {'phase':self.phase,'stage':self.stage,'elapsed_s':self.elapsed_s},clock={'domain':'simulation','time_s':self.time_s},error=None if self.phase!='failed' else {'code':self.warning,'message':self.warning,'retryable':False,'side_effects':'retained'})
 def _stop(self,status):
  if self._operation_kind=='scan':
   if self.stage=='transient' and self._stage_elapsed>1e-12:self._observe(False)
   self._dump();self.phase=status;self.stage='idle';self._finish_result(status)
  else:self.phase=status;self.stage='idle'
  self._operation_records[self.operation_id]=self._operation_snapshot()
 def _finish_result(self,status):
  self._record_counter+=1;rid='record_'+str(self._record_counter);quality=['REFERENCE_TAIL_BELOW_1E_MINUS4_NOT_PROFILED','CONDITIONAL_SYNTHETIC_RESPONSE','PROFILE_SAMPLING_LIMITED_TO_PUBLIC_REFERENCE_WINDOWS','UNKNOWN_ADDUCTS_AND_UNLISTED_COMPOUNDS_UNMODELED']
  if not self._run_curve:quality.append('UNCALIBRATED_MASS_AXIS')
  if self._run_kind!='blank' and self._run_key not in self._blanks:quality.append('UNMATCHED_BLANK_HOST_BASELINE_ONLY')
  if self._run_method['mode']=='ms2':quality.append('SOURCE_INSTRUMENT_RESOLUTION_AND_COLLISION_CONFLICT_TRANSFER' if self._run_method['reference_condition_policy']=='acknowledged_fixed_pattern' else 'UNVERIFIED_COLLISION_RESPONSE_UNMODELED_NOT_ABSENCE')
  else:quality.append('MS1_THEORETICAL_NATURAL_ISOTOPE_REFERENCE')
  if status!='succeeded':quality.append('INCOMPLETE_ACQUISITION')
  if any(s['saturated'] for s in self.scans):quality.append('SATURATED')
  if self.warning:quality.append(self.warning)
  if self._run_curve and any(not p['mass_calibrated'] for s in self.scans for p in s['centroids']):quality.append('MASS_AXIS_EXTRAPOLATION_NOT_VALIDATED')
  provenance=dict(evidence='synthetic',source_library_sha256=self._library_hash,mass_to_charge_sign_convention='positive m/abs(q) spectral-axis magnitude; polarity separately records charge sign',isotope_transmission='MS1 admitted fraction sums all derived probabilities in collection range, including heavy-isotope-only windows; conditional admitted-ion distribution preserves per-isotope contributions. MS2 only source monoisotopic precursor fragmentation is modeled.',source_mode='real processed ESI/HCD centroid references or derived natural-isotope MS1, not actual Exploris120 detector data',profile_grid='257 coarse public mass points plus dense windows around the entire eligible public library, independent of hidden composition; not uniform hardware profile or raw Fourier data',profile_reference_relative_intensity_floor=1e-4,profile_window='6 Gaussian sigma +0.001 nominal m/z+50ppm around public reference positions; no sample-dependent grid',response='host finite parent-equivalent mol to IRM charges; reference relative peak heights times trapped charges times host signal_per_charge; unknown absolute ESI/fragment yield is not measured',mass_axis='host affine drift followed by actual observed-standard affine correction; no3ppm hardware accuracy claim',resolution_equation='R(m)=R200/sqrt(m/200); Gaussian FWHM=m/R(m), explicit broadened-profile approximation, not recovered source resolution',timing='sequential fill -> transfer -> transient -> settling; fixture transient scales withR and obeys published maximum-rate ceilings where supplied; no claim of OEM transient durations or parallel filling',collision_energy='NCE fraction and absolute potentialV are separate; no conversion NCE/eV. All current MassBank spectra need ACK for35NCE versus stepped20/35/50 conflict and source35000/QExactivePlus',agc='100percent=1e6 nominal chargesFullMS or1e5MS2; host ionization/transfer efficiencies and relative signal are independent assumptions',formula_inference='observed centroid+public formula/adduct dictionary only; shared formula/isomers remain candidates',unknown_matrix_effects=True,upstream='finite host gated infusion, no built-inLC, retention time or gradient execution',calibration_source='host prepared formula exact-mass references, not certified mass solutions or source-observed MS1',trapped_inventory='parent-equivalent conservation through HCD; fragment stoichiometry/neutral losses not claimed')
  result=dict(record_id=rid,kind=self._run_kind,status=status,complete=status=='succeeded',scans=copy.deepcopy(self.scans),acquisition_signature=self._run_key,mass_calibration_id=self._run_curve['curve_id'] if self._run_curve else None,method_json=json.dumps(self._run_method,sort_keys=True),provenance_json=json.dumps(provenance,sort_keys=True),quality=quality);validate(RESULT,result);self._result_cache=result;self._records[rid]=copy.deepcopy(result)
  if self._run_kind=='blank' and status=='succeeded':self._blanks[self._run_key]=copy.deepcopy(result)
  if self._run_kind=='standard':self._standards[rid]={'template_id':self._run_standard,'mass_signature':self._mass_key(self._run_method['polarity'])}
 def _fit(self,a):
  if not a['curve_id']:raise ActionRejected('INVALID_ARGUMENT')
  if len(set(a['record_ids']))!=len(a['record_ids']):raise ActionRejected('DUPLICATE_RECORD')
  if a['curve_id'] not in self._curves and len(self._curves)>=MAX_CURVES:raise ActionRejected('CURVE_CAPACITY')
  x=[];y=[];references=[]
  for rid in a['record_ids']:
   r=self._records.get(rid);ref=self._standards.get(rid)
   if not r or not ref or not r['complete']:raise ActionRejected('ACTUAL_STANDARD_RECORD_REQUIRED')
   method=json.loads(r['method_json'])
   if ref['mass_signature']!=self._mass_key() or method['mode']!='ms1' or method['polarity']!=self.method['polarity']:raise ActionRejected('CALIBRATION_SIGNATURE')
   if r['acquisition_signature'] not in self._blanks:raise ActionRejected('BLANK_REQUIRED')
   if 'SATURATED' in r['quality']:raise ActionRejected('STANDARD_QUALITY')
   target=self.templates[ref['template_id']]['derived_precursor_mz'];values=[]
   for scan in r['scans']:
    peaks=scan['centroids'];matches=[p for p in peaks if abs(p['raw_mass_to_charge_relative']-target)<.002+target*.00005]
    if len(matches)!=1:raise ActionRejected('STANDARD_PEAK_AMBIGUOUS')
    values.append(matches[0]['raw_mass_to_charge_relative'])
   if not values:raise ActionRejected('STANDARD_PEAK_REQUIRED')
   x.append(float(np.mean(values)));y.append(float(target));references.append(ref['template_id'])
  if len(set(y))<3 or max(y)-min(y)<10:raise ActionRejected('REFERENCE_MASS_SPAN')
  xx=np.asarray(x);yy=np.asarray(y);coef=np.linalg.lstsq(np.column_stack([xx,np.ones(len(xx))]),yy,rcond=None)[0];slope,intercept=map(float,coef);res=yy-(slope*xx+intercept);rms=float(np.sqrt(np.mean((res/yy)**2)))
  if not .99<slope<1.01 or rms>1e-5:raise ActionRejected('MASS_CALIBRATION_FIT_INVALID')
  curve=dict(curve_id=a['curve_id'],mass_signature=self._mass_key(),time_s=self.time_s,polarity=self.method['polarity'],slope=slope,intercept_relative=intercept,rms_fraction=rms,minimum_observed_mz=min(x),maximum_observed_mz=max(x),observed_mz=x,reference_mz=y,reference_ids=references,record_ids=a['record_ids'],source='actual synthetic MS1 profile centroids fitted to public derived formula reference masses; not sourcecertified calibrant or instrument accuracy',validity_s=self.plant['calibration_validity_s']);self._curves[a['curve_id']]=curve;return {k:curve[k] for k in ['curve_id','slope','intercept_relative','rms_fraction']}
 def _formula(self,a):
  r=self._records.get(a['record_id'])
  if not r:raise ActionRejected('UNKNOWN_RECORD')
  if a['scan_index']>=len(r['scans']):raise ActionRejected('INVALID_SCAN_INDEX')
  scan=r['scans'][a['scan_index']]
  if a['peak_index']>=len(scan['centroids']):raise ActionRejected('INVALID_PEAK_INDEX')
  p=scan['centroids'][a['peak_index']]
  if not p['mass_calibrated'] or not scan['complete'] or scan['saturated'] or 'UNMATCHED_BLANK_HOST_BASELINE_ONLY' in r['quality']:raise ActionRejected('CALIBRATED_OBSERVATION_REQUIRED')
  observed=p['mass_to_charge_relative'];candidates=[]
  for i,ref in sorted(self.templates.items()):
   if ref['polarity']!=scan['polarity']:continue
   expected=ref['derived_precursor_mz'];error=(observed-expected)/expected
   if abs(error)<=a['tolerance_fraction']:candidates.append(dict(template_id=i,formula=ref['formula'],adduct=ref['precursor_type'],charge=ref['precursor_charge'],reference_mz=expected,mass_error_fraction=error,identity_confirmed=False))
  return dict(observed_mass_to_charge_relative=observed,candidates_json=json.dumps(candidates,sort_keys=True),interpretation='Public observed accurate-mass formula/adduct candidates only. No hidden sample identity is consulted; same formula isomers and unknown candidates remain unresolved. Fragment matches do not establish the precursor structure.')
class ThermoOrbitrapexplorisSession:
    """Only the trusted host assigns roles; returned data obey the same boundary."""
    def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
    @property
    def _role(self):return self._host._principals[self._principal]
    def _target(self,i):
        if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
    def describe(self,*,instance_id=None,asset_type_id=None):
        if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None:self._target(instance_id)
        if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
        m=export_thermo_orbitrapexploris_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        def timing(key):
            if key in ['mass.current_ratio','mass.current_kg_c','detector.tic_relative','detector.last_sample_time_s'] and h._last_scan:return h._last_scan['end_s'],[h._last_scan['transient_start_s'],h._last_scan['end_s']]
            return h.time_s,None
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='unavailable' if v[k] is None else 'estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=timing(k)[0],sample_window_s=timing(k)[1]) for k in ids})
    def invoke(self,*,instance_id,capability_id,arguments,request_id):
        h=self._host
        try:
            self._target(instance_id)
            if not isinstance(capability_id,str) or capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
            if self._role=='agent' and CAPS[capability_id][5]!='agent':raise ActionRejected('ACCESS_DENIED')
            if not isinstance(request_id,str) or not request_id:raise ActionRejected('INVALID_ARGUMENT')
            validate(CAPS[capability_id][0],arguments);key=(self._principal,instance_id,request_id);digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
            if key in h._requests:
                old,result=h._requests[key]
                if old!=digest:raise ActionRejected('REQUEST_CONFLICT')
                return copy.deepcopy(result)
            result=h.invoke(capability_id,**arguments);op=result.get('operation_id')
            if op:h._owners[op]=self._principal
            response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=op,status='accepted' if op else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None);h._requests[key]=(digest,copy.deepcopy(response));return response
        except ActionRejected as exc:return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,status='unsupported' if str(exc)=='UNSUPPORTED' else 'rejected',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))
    def write(self,*,instance_id,state_id,value,request_id):
        self._target(instance_id)
        if state_id not in SLOTS or not SLOTS[state_id][5]:raise ActionRejected('ACCESS_DENIED')
        cap,arg=SLOTS[state_id][5];return self.invoke(instance_id=instance_id,capability_id=cap,arguments={arg:value},request_id=request_id)
    def poll(self,operation_id):
        h=self._host
        if h._owners.get(operation_id)!=self._principal:raise ActionRejected('ACCESS_DENIED')
        if operation_id in h._operation_records:return copy.deepcopy(h._operation_records[operation_id])
        if operation_id!=h.operation_id:raise ActionRejected('ACCESS_DENIED')
        return copy.deepcopy(h._operation_snapshot())
    def cancel(self,operation_id,request_id):
        snapshot=self.poll(operation_id);h=self._host
        if not isinstance(request_id,str) or not request_id:raise ActionRejected('INVALID_ARGUMENT')
        key=(self._principal,h.instance_id,request_id);digest=json.dumps(['measurement.cancel',operation_id])
        if key in h._requests:
            old,response=h._requests[key]
            if old!=digest:raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(response)
        result=h.invoke('measurement.stop') if snapshot['status']=='running' else {'phase':snapshot['status']}
        response=dict(protocol_version=PROTOCOL_VERSION,instance_id=h.instance_id,request_id=request_id,operation_id=operation_id,status='succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None)
        h._requests[key]=(digest,copy.deepcopy(response));return response

LIMITATIONS=[
'Configuration is Orbitrap Exploris120 with OptaMaxNG H-ESI and S-lens, AABG, segmentedquadrupole, C-trap, charge detector, nitrogen-filledIRM and Orbitrap analyzer. IRM fills/cools before return toC-trap and Orbitrap injection. No moving quadrupole or analyzer scanning mechanism, linear ion trap, built-inLC, APCI/FAIMS option, fullDDA/DIA or hardware API is claimed.',
'FullMS range40–3000 obeys lastmass<=15*firstmass. For firstmass>2500, source says ioncollection2500–lastmass while displayed spectrum starts atrequestedmass. MS2 precursor40–2500, segmentedminimumwidth.4/.7/1/1.5/2/3 over documented bands, maximumwidth5; the broad specification .4–2000 describes a differentisolation context. Native dimensionless m/z refers to u/e; kg/C=mz*CODATA2022u/e, not neutralmasskg. Library supports single positive/negative adduct ions only.',
'Software supports15000/22500/30000/45000/60000/90000/120000. R(m)=R200/sqrt(m/200). Gaussian FWHM=m/R is a host profile approximation; it cannot recover unresolved information from source35000 processed centroids. Source maximum22/12/7/3Hz at15/30/60/120k are ceilings under sourceconditions. Numerical sequentialtransient/fill/transfer/settling values are explicit hostfixtures, not OEM timing or parallel AGC acquisition. Midresolution timing has no independentlyreported rate anchor.',
'AGC100percent means nominal1e6 chargesFullMS or1e5MS2; percentage input converted to fraction, rangesFull1e-5–10 andMS2to30. Finite parentmoles, host ionization/transfer efficiencies and elapsedfill produce stored charge; target or maxinjectiontime ends filling. Imagecurrent-relative amplitude is not an actual measured ioncount/current or concentration. Unknown charge-state/adduct equilibria and matrix-dependentESI response are not inferred.',
'Fixed HCD NCE1–200percent and absolutepotential1–200V are distinct. NCE has no invented eV conversion. CurrentCASMI references report35NCE while citedpaper reportsstepped20/35/50. Sourceinstrument QExactivePlus and35000resolution differ. MS2 requires explicitlyselected acknowledged_fixed_pattern and trusted accept_transferred_reference for the fixed conditioned shape; defaultmatching_energy never implies this conflict is resolved. Under strict mode unsupported sourceenergy responses are unmodeled and absence is not established. Exactfragmentation/neutral-lossstoichiometry/collisiongasdynamics are not claimed.',
'Real MS2 arrays are processed/recalibrated MassBank ESI/HCD centroid references, not raw detector electronics. MS1 natural-isotope distributions are derived from publicformulas/NIST isotope data, not measured sourceMS1. The generator keeps originalsourcebase normalization and samples a coarse publicbackground plus dense windows around all eligible publiclibrary peaks above1e-4relative. Grid depends only on publicmethod/library, never hiddencomposition. It is not a complete unknown-species highresolution search or nativeFT transient. Noise, drift, peakwidth and gain are explicit hostparameters.',
'Actual generatedprofile values are backgroundsubtracted against matching measuredblank whenavailable, then independent localpeakfinding/moments give observedcentroids. Profilepeakfinding never returns hiddenexpected masses as observations. >=3 actual single-reference MS1 scans with matching blanks fit affine massaxis using public formula exactmass assignments, not CRMsolutions. Calibration signature includes polarity/modeldrift/reference library, not scanrange/mode, allowing separate localreferencewindows. Source24h<3ppmRMS is not noise or guaranteedsimulationaccuracy. Calibration validity is a hostcheck capped24h; observedfit residual is modelconsistency only. Outsideobservedreferenceinterpolation domain massaxis correction is explicitlyunvalidated; formula search requires an in-range calibrated completeunsaturated observation.',
'Formula lookup uses only observedcentroid, publicformula/adductdictionary and tolerance. It does not inspect hiddenmixture. Candidates are not confirmedcompounds or isomerstructures; spectra fromunmodeled molecules/adducts cannot be ruledout. No sourceintensity-to-concentration calibration is invented.',
'Finite upstreamsolution infusion is a hostgate openonlyduringacquisition, not built-inLC/pump control. Carrier/vaporizer sourceN2 andIRMcollisionN2 are separate finite molstocks with hostreferenceconditions/flowassumptions; sourcearbitrary gas settings are not physicallycalibrated m3/s. Fullstandbybackgroundgas, pumpdown, vacuuminterlocks and gaugeelectronics are not implemented. Low/highflowneedles enforce source1–10/10–1000uL/min domains. Transfer andvaporizer temperatures have distinct finite equivalentheatnodes; heaterpower, capacity andloss are hostparameters.',
'Neutral-species parent-equivalent moles close reservoir+sourcefilm+IRM+discard+hostreturned. HCDfragmentstoichiometry is not asserted. Chargeledger covers acceptedIRMcharges, storedions andeventualneutralization, not totalESIelectricalcurrent. Solution andcleaningsolvent volumes are separate finiteinventories. Cancel retains consumption, depositedfilm, partialtransient observation andiondump. Cleaning is a coldremoved-source hostfilm-removal surrogate withfinite solvent; no realOEMchemicalcleaning recipe or recoveryclaim.',
'Four physicalstates are accessorycabinetlid, two independent90degree sourcelatches andhostsupported straightwithdrawal. Coordinates/travel/speed andthermalthreshold(defaultambient+0.5K,298.65K) are engineeringconditions, not an OEMmotor or timedthermalmodel. Manufacturer approximately30mincooling guidance for sourcehousing andatleast60min fortransfer-tuberemoval are differenttasks; this model supports sourcehousingonly usingexplicit actualtemperature proxy, not acceleratedproof ofOEMwait. Fluiddisconnect, coldstate, sourcestop anddualunlock are enforced. Removal disablesAPIvoltage/gas andstandby in thismodel; fullhardwareinterlockchain notvalidated.',
'Three genuineLEDregions Power/Status/Scan have publicRGB andboolfallback. Greenpower/status, blueMS1,purpleMS2,yellowmodelwarning,whiteengineeringwarmup indication follow documentedqualitative palette. ActualOEManimation/blinktiming andfullerrorseverityclassification are not reconstructed. Sixnumericregions are simulation-only; no originalintegralLCD. LED/material/geometry/nativebridge effects require separate actualrootvisualreports.',
'All finite motion/heat/transport/scanwindows advance only instep; acceptedhostbatchtransfers/configuration are atomic commands. Read/describe/query are pure. Role/owner/same-request controls and128results/32curves are process-localengineeringfeatures, not OSisolation or documentedOEMstorage. Realhardware, nativefirmware, hardwareaccuracy, LCmethods andfullFFT remain not_run.'
]
def export_thermo_orbitrapexploris_contract_metadata():
 caps={};slots={};motion=['accessory_lid.angle_rad','source.withdrawal_m','source_latch.left_angle_rad','source_latch.right_angle_rad'];process=['scan.phase','scan.stage','scan.elapsed_s','scan.progress'];observed=['mass.current_ratio','mass.current_kg_c','detector.tic_relative','detector.last_sample_time_s'];thermal=['source.actual_temperature_k','source.vaporizer_actual_temperature_k','truth.ledger'];leds=[k for k in SLOTS if k.startswith('led.')];ready=['instrument.ready','blank.valid','calibration.ready'];end=[*process,*observed,'storage.result_count','safety.warning',*ready,*leds]
 immediate={'plant.configure':['plant.configured',*thermal,*ready,*leds],'power.set':['power.enabled','source.enabled','source.spray_voltage_v','source.gas_enabled',*end,'truth.ledger'],'source.set':['source.enabled','source.spray_voltage_v','source.gas_enabled',*end,'truth.ledger'],'source.fluid':['source.fluid_connected','instrument.ready'],'source.latch':['instrument.ready'],'source.withdraw':['source.enabled','source.spray_voltage_v','source.gas_enabled','instrument.ready'],'accessory_lid.set':[],'sample.load':['truth.ledger'],'sample.unload':['truth.ledger'],'method.configure':['method.mode','method.polarity','resolution.actual_at_200','source.spray_voltage_v',*ready,*leds],'scan.start':[*process,*observed,'safety.warning',*leds],'measurement.stop':[*end,'truth.ledger','agc.fill_charges'],'maintenance.clean':[*process,'truth.ledger',*leds],'calibration.fit':['storage.curve_count','calibration.ready'],'calibration.select':['calibration.ready'],'records.remove':['storage.result_count']}
 onstep={'power.set':[*thermal,*leds],'source.set':[*thermal,*ready,*leds,'source.gas_enabled','source.spray_voltage_v','source.enabled',*end],'source.latch':['source_latch.left_angle_rad','source_latch.right_angle_rad',*ready],'source.withdraw':['source.withdrawal_m',*ready],'accessory_lid.set':['accessory_lid.angle_rad'],'scan.start':[*process,*observed,*thermal,*leds,'agc.fill_charges','storage.result_count',*ready,'safety.warning','source.enabled','source.gas_enabled','source.spray_voltage_v'],'maintenance.clean':[*process,'truth.ledger',*leds]}
 for k,(s,u,origin,vis,parts,write) in SLOTS.items():
  mode='interval_mean' if k=='detector.tic_relative' else 'instant'
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=parts,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=origin,visibility=vis,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'ThermoOrbitrapexplorisSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode=mode,time_domain='simulation',window_description='Actual complete or partialcancelOrbitrap surrogate transient; querytime is not sampletime.' if mode!='instant' else None),source_ids=['simulation_assumption'])
 errors=sorted({n.args[0].value for n in ast.walk(ast.parse(Path(__file__).read_text())) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='ActionRejected' and n.args and isinstance(n.args[0],ast.Constant) and isinstance(n.args[0].value,str)})
 observes={'process.measure':[*process,'detector.tic_relative'],'result.read':observed,'spectrum.read':observed,'blank.read':['blank.valid'],'records.list':['storage.result_count','storage.curve_count'],'product.read':['truth.ledger']}
 for k,(inp,out,parts,label,kind,vis,example) in CAPS.items():
  unsupported=k in UNSUPPORTED;async_=k in ['scan.start','maintenance.clean'];effects=[dict(state_id=s,description_zh=SLOT_ZH[s],timing=t) for t,mapping in [('immediate',immediate),('on_step',onstep)] for s in dict.fromkeys(mapping.get(k,[]))]
  caps[k]=dict(function_zh=label,kind=kind,visibility=vis,component_ids=parts,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation={layer:('implemented' if layer in ['process','host'] and not unsupported else 'absent') for layer in ['process','scene','robot','hardware','host']},binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.thermo_orbitrapexploris.ThermoOrbitrapexplorisSession.invoke',invocation=f'session.invoke(instance_id="orbitrap_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if kind=='query' or unsupported else [dict(description_zh='有限样品/源冷态与锁杆/供气/公开方法/真实质量观察标定/源冲突接受由入口校验',state_ids=['power.enabled','instrument.ready','scan.phase'],enforced_by='runtime',check='Runtime._invoke/_start/_fit/_formula and authenticated Session')],state_effects=[] if unsupported or kind=='query' else effects,observes=[] if unsupported else observes.get(k,[]),model_effects=dict(mode='none',description_zh='独立真实scene绑定后才能声称几何/LED联动，质谱不是机械运动',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='有限IRM充填及实际瞬态/扫描数完成或冷清洁时长完成；取消保留实际耗样' if async_ else '原子命令返回，有限过程只由step推进',completion_state_ids=['scan.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','source_housing','ion_routing_multipole','orbitrap_analyzer','finite_upstream_solution'],partial_failure_zh='已耗溶液/源滞留/离子卸载及部分观察保留，不返还初始真值'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in errors],example_arguments=copy.deepcopy(example),verification=[],limitations=LIMITATIONS,source_ids=['manufacturer_manual','simulation_assumption'])
 mech=dict(component_ids=['spray_emitter','charge_detector','quadrupole','ion_routing_multipole','c_trap','orbitrap_analyzer'],state_ids=list(slots),meaning_zh='有限溶液ESI/IRM填充与条件化Orbitrap实际观察profile、质量轴拟合和公开公式候选',implementation='lumped',equations=['n_input=n_reservoir+n_sourcefilm+n_IRMparentEquivalent+n_waste+n_returned; finite infusion transfers volume*concentration.', 'q_target=e*AGC_fraction*(1e6FullMS or1e5MS2); dN_IRM=flow*concentration*hostESIeta*hosttransfereta*NA*dt; maxITorAGClimit.', 'R(m)=R200/sqrt(m/200); GaussianFWHM=m/R(m); sourcecentroid weights generate conditional relativeprofile, not rawFourierdata.', 'Observedprofile peakfinding and localmoments precede fit(mz_reference=slope*mz_observed+intercept); formula candidates use observedcorrectedmass only.', 'm/z_kg_C=m/z_relative*CODATA2022u/e; NCEdimensionlessfraction is not collisionpotentialV.', 'Two finite thermalnodes obey C*dT/dt=P-h*(T-Tambient); electricalheat=stored+lost. AcceptedIRMcharge=stored+neutralized.'],parameters={},source_ids=['manufacturer_manual','simulation_assumption'],limitations=LIMITATIONS)
 spec=importlib.util.spec_from_file_location('_orbitrap_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'esi_irm_orbitrap':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE)})
 return dict(capabilities=caps,state_slots=slots,mechanisms={'esi_irm_orbitrap':mech})

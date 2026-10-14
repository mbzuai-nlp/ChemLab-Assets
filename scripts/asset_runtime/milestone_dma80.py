"""CHEM-086 DMA-80 evo Double Beam: conditional Hg mass transport and observed dual-beam AAS."""
from __future__ import annotations
import ast,copy,csv,hashlib,importlib.util,io,json,math
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2];SLUG='milestone_dma80';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-086';PROTOCOL_VERSION=ASSET_VERSION='1.0.0'
TEMPLATE_REL='assets/expansion_batch04/t3_analytical/milestone_dma80/output_templates/library.json'
R_GAS=8.31446261815324;M_O2=.031998;WAVELENGTH_M=253.65e-9;MAX_RESULTS=512;MAX_CURVES=32;MAX_WINDOWS=10000;SLOT_COUNT=40
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
PLANT=obj(dict(seed=integer(0,2**32-1),ambient_temperature_k=number('K',278.15,308.15),heating_rate_k_s=number('K/s',1,10000),cooling_rate_k_s=number('K/s',1,10000),thermal_capacity_j_k=number('J/K',.1,1e5),heat_loss_w_k=number('W/K',0,100),rotation_speed_rad_s=number('rad/s',.01,30),injection_speed_m_s=number('m/s',.001,3),cold_threshold_k=number('K',308.15,373.15),oxygen_supply_mol=number('mol',0,1000),oxygen_flow_mol_s=number('mol/s',1e-8,.1),oxygen_demand_mol_kg=number('mol/kg',0,1000),dry_mass_loss_fraction=number('1',0,.99),residue_fraction=number('1',0,1),mercury_recovery=number('1',.01,1),transport_tau_s=number('s',.005,30),release_tau_s=number('s',.005,30),gold_capacity_kg=number('kg',1e-12,1e-5),cell_volume_m3=number('m^3',1e-9,1e-3),cell_pressure_pa=number('Pa',10000,2e5),absorption_coefficient_m2_kg=number('m^2/kg',1,1e9),optical_path_m=number('m',.001,1),lamp_relative=number('1',.01,100),common_drift_fraction=number('1',0,.8),common_drift_period_s=number('s',.1,1000),sample_beam_ratio=number('1',.1,10),reference_dark_relative=number('1',0,.1),sample_dark_relative=number('1',0,.1),beam_noise_density_sqrt_s=number('s^0.5',0,.01),baseline_absorbance=number('1',-.05,.05),max_absorbance=number('1',.1,6),optional_collected_trap_installed=BOOL))
PLANT_EXAMPLE=dict(seed=860,ambient_temperature_k=298.15,heating_rate_k_s=4000.,cooling_rate_k_s=4000.,thermal_capacity_j_k=5.,heat_loss_w_k=.1,rotation_speed_rad_s=8.,injection_speed_m_s=1.,cold_threshold_k=323.15,oxygen_supply_mol=10.,oxygen_flow_mol_s=.0001,oxygen_demand_mol_kg=.1,dry_mass_loss_fraction=.1,residue_fraction=.05,mercury_recovery=.99,transport_tau_s=.02,release_tau_s=.04,gold_capacity_kg=1e-7,cell_volume_m3=2e-7,cell_pressure_pa=101325.,absorption_coefficient_m2_kg=1000.,optical_path_m=.1,lamp_relative=1.,common_drift_fraction=.1,common_drift_period_s=4.,sample_beam_ratio=.9,reference_dark_relative=.0001,sample_dark_relative=.0002,beam_noise_density_sqrt_s=0.,baseline_absorbance=.0001,max_absorbance=2.,optional_collected_trap_installed=False)
METHOD=obj(dict(drying_temperature_k=number('K',298.15,573.15),drying_s=number('s',.05,3600),decomposition_temperature_k=number('K',573.15,1273.15),decomposition_s=number('s',.05,3600),catalyst_temperature_k=number('K',573.15,1123.15),purge_s=number('s',.05,600),release_temperature_k=number('K',773.15,1223.15),release_s=number('s',.05,60),record_s=number('s',.1,600),window_s=number('s',.01,10),range_min_kg=number('kg',1e-14,1.5e-9),range_max_kg=number('kg',1e-14,1.5e-9)))
METHOD_EXAMPLE=dict(drying_temperature_k=473.15,drying_s=.12,decomposition_temperature_k=923.15,decomposition_s=1.,catalyst_temperature_k=838.15,purge_s=.2,release_temperature_k=1173.15,release_s=.5,record_s=.8,window_s=.05,range_min_kg=1e-14,range_max_kg=1.5e-9)
SAMPLE=obj(dict(slot_index=integer(0,39),template_id=TEXT,kind=enum(['sample','standard','blank']),input_form=enum(['solid','liquid','collected_gas_sorbent']),boat_material=enum(['quartz','metal','collected_trap']),mass_kg=number('kg',1e-9,.0015),volume_m3=number('m^3',0,1.5e-6),reference_fraction=number('1',1e-8,1),dry_mass_fraction=nullable(number('1',1e-6,1)),host_mercury_mass_kg=nullable(number('kg',0,.0015)),accept_source_condition_mismatch=BOOL))
SAMPLE_EXAMPLE=dict(slot_index=0,template_id='nist_2448_certified',kind='sample',input_form='solid',boat_material='metal',mass_kg=.0001,volume_m3=0.,reference_fraction=1.,dry_mass_fraction=None,host_mercury_mass_kg=None,accept_source_condition_mismatch=True)
STANDARD_EXAMPLE=SAMPLE_EXAMPLE|dict(template_id='nist_1641f_certified',kind='standard',input_form='liquid',boat_material='quartz',mass_kg=.0001,volume_m3=1e-7)
BLANK_EXAMPLE=SAMPLE_EXAMPLE|dict(kind='blank',template_id='',host_mercury_mass_kg=None)
POINT=obj(dict(start_s=number('s',0),end_s=number('s',0),absorbance=nullable(number('1')),sample_relative=number('1'),reference_relative=number('1'),transmission_ratio=nullable(number('1',0)),saturated=BOOL))
RESULT=obj(dict(record_id=TEXT,slot_index=integer(0,39),kind=TEXT,status=TEXT,complete=BOOL,windows=arr(POINT,0,MAX_WINDOWS),integrated_absorbance_s=nullable(number('s')),blank_corrected_integral_s=nullable(number('s')),mercury_mass_kg=nullable(number('kg')),mercury_mass_fraction_kg_kg=nullable(number('1')),mercury_concentration_kg_m3=nullable(number('kg/m^3')),uncertainty_kg=nullable(number('kg',0)),sample_mass_kg=number('kg'),sample_volume_m3=number('m^3'),acquisition_signature=TEXT,acquisition_method_json=TEXT,evidence=TEXT,template_source=TEXT,quality=arr(TEXT,0,20)))
LEDGER=obj(dict(sample_registered_kg=number('kg',0),sample_remaining_kg=number('kg',0),sample_returned_kg=number('kg',0),residue_kg=number('kg',0),residue_removed_kg=number('kg',0),reaction_products_kg=number('kg',0),oxygen_reacted_kg=number('kg',0),mass_residual_kg=number('kg'),mercury_ledger_json=TEXT,volume_ledger_json=TEXT,gas_ledger_json=TEXT,electrical_energy_j=number('J',0),thermal_stored_j=number('J'),thermal_lost_j=number('J',0),energy_residual_j=number('J')))
CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',example=None):CAPS[k]=(i,o,c,z,kind,vis,example or {})
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body'],'宿主显式初始化热/光响应、气体和选件','configuration','evaluator',PLANT_EXAMPLE)
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['decomposition_furnace','catalyst','amalgamator'],'电源目标；断电取消并保留实际消耗',example={'enabled':True})
cap('gas.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['carrier_gas_inlet'],'载氧开关；中断不恢复已消耗样品',example={'enabled':True})
cap('cover.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['main_cover'],'可信宿主移动主维护罩；冷态断电且样盘罩归位','device_command','evaluator',{'opened':False})
cap('dust_cover.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['dust_cover'],'例行装样独立透明罩；空闲且输舟归位的宿主动作','device_command','evaluator',{'opened':False})
cap('sample.load',SAMPLE,obj({'loaded_slot':integer(0,39)}),['carousel'],'四十舟位独立装载，受限组成只由宿主提供','device_command','evaluator',SAMPLE_EXAMPLE)
cap('sample.unload',obj({'slot_index':integer(0,39),'recipient_id':TEXT}),obj({'unburned_mass_kg':number('kg',0),'residue_mass_kg':number('kg',0)}),['carousel'],'移出剩余样品/残渣，不假还原燃烧物','device_command','evaluator',{'slot_index':0,'recipient_id':'host'})
cap('sample.inventory',obj({}),obj({'slots':arr(obj({'slot_index':integer(0,39),'occupied':BOOL,'processed':BOOL}),40,40)}),['carousel'],'仅公开舟位占用与已处理标志，不泄漏Hg真值','query')
cap('carousel.select',obj({'slot_index':integer(0,39)}),obj({'selected_slot':integer(0,39)}),['carousel'],'送舟归位后有限选位；40个真独立槽',example={'slot_index':0})
cap('method.configure',METHOD,obj({'configured':BOOL}),['decomposition_furnace','catalyst','amalgamator','absorption_cell'],'干燥/分解/捕集释放/记录方法与用户量程窗口','configuration',example=METHOD_EXAMPLE)
cap('measurement.start',obj({'slot_index':integer(0,39)}),obj({'operation_id':TEXT}),['carousel','transfer_carriage','decomposition_furnace','amalgamator','absorption_cell'],'单舟完整热分解金捕集双光束吸收流程',example={'slot_index':0})
cap('sequence.start',obj({'slot_indices':arr(integer(0,39),1,40)}),obj({'operation_id':TEXT}),['carousel','transfer_carriage','decomposition_furnace','amalgamator','absorption_cell'],'依序处理独立40舟位，空位/重复/已消耗拒绝',example={'slot_indices':[0]})
cap('measurement.stop',obj({}),obj({'phase':TEXT}),['transfer_carriage','decomposition_furnace','amalgamator'],'取消并保存当前部分光学窗口与物料残留')
cap('process.measure',obj({}),obj({'phase':TEXT,'stage':TEXT,'elapsed_s':number('s',0),'progress':number('1',0,1),'absorbance':nullable(number('1')),'integrated_absorbance_s':nullable(number('s')),'mercury_mass_kg':nullable(number('kg'))}),['display_panel'],'纯读过程与公开观测推断','query')
cap('result.read',obj({}),RESULT,['display_panel'],'当前/最近已保存观测及其冻结方法','query')
cap('sequence.read',obj({}),obj({'phase':TEXT,'completed_records':arr(RESULT,0,40)}),['display_panel'],'本次序列已有结果；历史operation另受owner保护','query')
cap('signal.read',obj({}),obj({'windows':arr(POINT,0,MAX_WINDOWS),'integrated_absorbance_s':nullable(number('s')),'wavelength_m':number('m'),'evidence':TEXT}),['sample_photodiode','reference_photodiode'],'实际窗口双光束相对光强与吸光度，不当W或原始OEM ADC','query')
cap('result.export',obj({'format':enum(['json','csv'])}),obj({'format':TEXT,'content':TEXT}),['display_panel'],'返回SI kg/s/1的JSON与CSV内容','query',example={'format':'json'})
cap('blank.read',obj({}),obj({'valid':BOOL,'blank_json':TEXT}),['display_panel'],'方法及响应签名匹配的实际模拟空舟观测','query')
cap('calibration.fit',obj({'curve_id':TEXT,'record_ids':arr(TEXT,3,40)}),obj({'curve_id':TEXT,'slope_s_kg':number('s/kg'),'r_squared':number('1')}),['display_panel'],'仅已保存实际标准峰面积与来源参考量拟合','analysis',example={'curve_id':'observed','record_ids':['record_2','record_3','record_4']})
cap('calibration.select',obj({'curve_id':TEXT}),obj({'selected':TEXT}),['display_panel'],'选取同方法/响应签名的曲线',example={'curve_id':'observed'})
cap('calibration.read',obj({'curve_id':TEXT}),obj({'curve_json':TEXT}),['display_panel'],'纯读实际标准观测和拟合','query',example={'curve_id':'observed'})
cap('records.list',obj({}),obj({'record_ids':arr(TEXT,0,MAX_RESULTS),'curve_ids':arr(TEXT,0,MAX_CURVES)}),['display_panel'],'本实例独立缓存目录','query')
cap('records.read',obj({'record_id':TEXT}),RESULT,['display_panel'],'历史记录含原方法及采样时间','query',example={'record_id':'record_1'})
cap('records.remove',obj({'record_id':TEXT}),obj({'removed':BOOL}),['display_panel'],'删除结果缓存，不删除受保护操作终态',example={'record_id':'record_1'})
cap('template.read',obj({'substance_id':TEXT}),obj({'template_json':TEXT}),['display_panel'],'真实/名义/理论/缺量记录分开原样查询','query',example={'substance_id':'nist_2448_certified'})
cap('product.read',obj({}),LEDGER,['decomposition_furnace','catalyst','amalgamator','absorption_cell'],'受限Hg/样品/氧气/体积/热账本','query','evaluator')
cap('product.drain',obj({'recipient_id':TEXT}),obj({'removed_mercury_kg':number('kg',0)}),['catalyst','amalgamator','absorption_cell'],'冷断电宿主清理实际残留到废物，不归还原样','device_command','evaluator',{'recipient_id':'hazardous_waste'})
UNSUPPORTED={'sample.on_the_fly','sample.preprocess','gas.collect','speciation.identify','hardware.connect','software.gui'}
for k in sorted(UNSUPPORTED):cap(k,obj({}),obj({}),['body'],'当前未实现或未安装：'+k)
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,origin='derived',vis='agent',parts=('display_panel',),write=None):SLOTS[k]=(s,u,origin,vis,list(parts),write);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','宿主已配置');slot('power.enabled',BOOL,'1','电源目标','setpoint',parts=('decomposition_furnace',),write=('power.set','enabled'));slot('gas.enabled',BOOL,'1','载氧目标','setpoint',parts=('carrier_gas_inlet',),write=('gas.set','enabled'))
slot('cover.angle_rad',number('rad',0,1.35),'rad','主维护罩宿主工程角','scene',parts=('main_cover',));slot('dust_cover.angle_rad',number('rad',0,1.65),'rad','独立样盘防尘罩宿主工程角','scene',parts=('dust_cover',));slot('carousel.angle_rad',number('rad',0,2*math.pi),'rad','四十位转盘有限工程角','scene',parts=('carousel',));slot('injection.position_m',number('m',0,.230),'m','有限送舟工程行程','scene',parts=('transfer_carriage',));slot('transfer.lift_m',number('m',0,.050),'m','取舟有限抬升，完整落回才可转盘','scene',parts=('transfer_lift',));slot('carousel.selected_slot',integer(0,39),'1','目标舟位0至39','setpoint',parts=('carousel',),write=('carousel.select','slot_index'));slot('sample.count',integer(0,40),'1','包括已处理舟位占用数量')
for k,z in [('temperature.actual_k','炉温理想热反馈'),('temperature.target_k','当前步骤炉温目标'),('catalyst.actual_k','催化炉理想温度'),('amalgamator.actual_k','金捕集器理想温度'),('cell.actual_k','吸收池理想温度')]:slot(k,number('K',278.15,1273.15),'K',z+'；不是实机温控精度',parts=('decomposition_furnace','catalyst','amalgamator','absorption_cell'))
slot('instrument.ready',BOOL,'1','电源/气体/罩/热台就绪');slot('measurement.phase',TEXT,'1','操作终态或running');slot('measurement.stage',TEXT,'1','选位/送舟/干燥/分解/捕集释放/返舟步骤');slot('measurement.elapsed_s',number('s',0),'s','当前样品真实模拟已用时间');slot('measurement.progress',number('1',0,1),'1','单样步骤进度');slot('sequence.progress',number('1',0,1),'1','序列已完成及当前样品步骤进度');slot('sequence.completed_count',integer(0,40),'1','本序列已完成记录数');slot('sequence.current_slot',nullable(integer(0,39)),'1','当前处理舟位或null')
for k,z in [('detector.absorbance','最近真实窗口双光束净吸光度'),('detector.sample_relative','样品光束窗口平均相对光强'),('detector.reference_relative','参考光束窗口平均相对光强'),('detector.transmission_ratio','扣宿主暗信号及已知光束比后的透射比')]:slot(k,nullable(number('1')),'1',z+'；synthetic且非W',parts=('sample_photodiode','reference_photodiode'))
slot('detector.integrated_absorbance_s',nullable(number('s')),'s','实际窗口吸光度累积面积',parts=('absorption_cell',));slot('detector.last_sample_time_s',nullable(number('s',0)),'s','最近光学窗口终点')
slot('result.mercury_mass_kg',nullable(number('kg')),'kg','空白扣除及实际标准峰面积反演Hg质量；未校准/量程外null');slot('result.mass_fraction_kg_kg',nullable(number('1')),'1','观测Hg质量除宿主公开实际称样量');slot('result.concentration_kg_m3',nullable(number('kg/m^3')),'kg/m^3','液样观测Hg质量除实际体积，固体不输出假浓度');slot('blank.valid',BOOL,'1','匹配当前方法的已完成空白');slot('calibration.ready',BOOL,'1','已选择相容标线');slot('storage.result_count',integer(0,MAX_RESULTS),'1','512实例结果工程缓存非OEM容量');slot('storage.curve_count',integer(0,MAX_CURVES),'1','32条实例标线工程缓存');slot('safety.warning',TEXT,'1','公开错误不含隐藏组成');slot('truth.ledger',LEDGER,'1','受限物料与Hg各仓守恒','process_truth','evaluator',('decomposition_furnace','amalgamator'))

def _library():
 p=ROOT/TEMPLATE_REL
 return json.loads(p.read_text()) if p.exists() else {'compounds':{}}

def _amount(r,a):
 if r.get('mercury_mass_kg') is not None:return float(r['mercury_mass_kg'])*a['reference_fraction']
 if r.get('mercury_concentration_kg_m3') is not None:
  if a['volume_m3']<=0:raise ActionRejected('SOURCE_QUANTITY_BASIS')
  return float(r['mercury_concentration_kg_m3'])*a['volume_m3']*a['reference_fraction']
 if r.get('mercury_mass_fraction_kg_kg') is not None:
  factor=1.
  if r.get('conditions',{}).get('mass_basis')=='dry_mass':
   if a['dry_mass_fraction'] is None:raise ActionRejected('DRY_MASS_FRACTION_REQUIRED')
   factor=a['dry_mass_fraction']
  return float(r['mercury_mass_fraction_kg_kg'])*a['mass_kg']*a['reference_fraction']*factor
 raise ActionRejected('REFERENCE_QUERY_ONLY')

class MilestoneDma80Runtime:
 def __init__(self,instance_id='dma80_01',library=None):
  self.instance_id=instance_id;self.library=copy.deepcopy(library) if library is not None else _library();self.templates=self.library.get('compounds',{});self.plant=None;self.method=copy.deepcopy(METHOD_EXAMPLE);self.time_s=0.;self.revision=0;self.powered=False;self.gas=False;self.cover=0.;self.dust_cover=0.;self.carousel=0.;self.selected_slot=0;self.lift=0.;self.injection=0.;self.samples={};self.phase='idle';self.stage='idle';self.elapsed_s=0.;self.stage_elapsed=0.;self.warning='';self.temps={k:298.15 for k in ['furnace','catalyst','gold','cell']};self.target=298.15;self.queue=[];self.queue_index=0;self.current_slot=None;self.completed=[];self.windows=[];self._last_point=None;self._pending_s=0.;self._pending_r=0.;self._pending_dt=0.;self._pending_end=0.;self.area=0.;self._result_cache=None;self._run_method=None;self._run_key='';self._run_blank=None;self._run_curve=None;self._run_kind='';self._records={};self._curves={};self._blanks={};self._standards={};self._selected_curve=None;self._record_counter=0;self._operation_counter=0;self.operation_id=None;self._operation_records={};self._owners={};self._requests={};self._principals={'__public_agent__':'agent'}
  self.registered=0.;self.returned=0.;self.residue_removed=0.;self.products=0.;self.hg_registered=0.;self.hg_returned=0.;self.hg_transport=0.;self.hg_gold=0.;self.hg_cell=0.;self.hg_exhaust=0.;self.hg_unrecovered=0.;self.hg_removed=0.;self.vol_registered=0.;self.vol_consumed=0.;self.vol_returned=0.;self.oxygen_remaining=0.;self.oxygen_exhaust=0.;self.oxygen_reacted=0.;self.electrical=0.;self.thermal_lost=0.
 def register_principal(self,principal_id,visibility):
  if not isinstance(principal_id,str) or not principal_id or visibility not in ['agent','debug','evaluator']:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals and self._principals[principal_id]!=visibility:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return MilestoneDma80Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _home(self):return self.injection<1e-9 and self.lift<1e-9
 def _cold(self):return not self.powered and max(self.temps.values())<=self.plant['cold_threshold_k']
 def _key(self):
  if self.plant is None:return ''
  excluded={'seed','oxygen_supply_mol','rotation_speed_rad_s','injection_speed_m_s','cold_threshold_k','optional_collected_trap_installed'}
  return hashlib.sha256(json.dumps({'method':self.method,'response':{k:v for k,v in self.plant.items() if k not in excluded},'library_sha':self._library_hash,'wavelength_m':WAVELENGTH_M},sort_keys=True,allow_nan=False).encode()).hexdigest()
 def _ready(self):
  return bool(self.plant and self.powered and self.gas and self.oxygen_remaining>0 and self.cover==0 and self.dust_cover==0 and self.temps['catalyst']>=self.method['catalyst_temperature_k']-1e-6 and abs(self.temps['cell']-393.15)<1e-6)
 def _blank(self):return self._blanks.get(self._key())
 def _curve(self):
  c=self._curves.get(self._selected_curve)
  return c if c and c['acquisition_signature']==self._key() else None
 def _progress(self):
  if self.phase=='succeeded':return 1.
  if self.phase!='running':return 0. if self.elapsed_s==0 else self._saved_progress
  stages=['selecting','lifting','inserting','drying_ramp','drying','decomposition_ramp','decomposing','purging','release_ramp','recording','returning','lowering']
  i=stages.index(self.stage);return min(.999,(i+min(1.,self.stage_elapsed/max(.01,self._stage_duration())))/len(stages))
 def read(self,visibility='agent'):
  if visibility not in ['agent','debug','evaluator']:raise ActionRejected('ACCESS_DENIED')
  last=self._last_point;result=self._result();progress=self._progress();seq=(self.queue_index+progress)/len(self.queue) if self.queue else 0.
  v={'plant.configured':self.plant is not None,'power.enabled':self.powered,'gas.enabled':self.gas,'cover.angle_rad':self.cover,'dust_cover.angle_rad':self.dust_cover,'carousel.angle_rad':self.carousel,'transfer.lift_m':self.lift,'injection.position_m':self.injection,'carousel.selected_slot':self.selected_slot,'sample.count':len(self.samples),'temperature.actual_k':self.temps['furnace'],'temperature.target_k':self.target,'catalyst.actual_k':self.temps['catalyst'],'amalgamator.actual_k':self.temps['gold'],'cell.actual_k':self.temps['cell'],'instrument.ready':self._ready(),'measurement.phase':self.phase,'measurement.stage':self.stage,'measurement.elapsed_s':self.elapsed_s,'measurement.progress':progress,'sequence.progress':1. if self.phase=='succeeded' else min(1.,seq),'sequence.completed_count':len(self.completed),'sequence.current_slot':self.current_slot,'detector.absorbance':last['absorbance'] if last else None,'detector.sample_relative':last['sample_relative'] if last else None,'detector.reference_relative':last['reference_relative'] if last else None,'detector.transmission_ratio':last['transmission_ratio'] if last else None,'detector.integrated_absorbance_s':self.area if self.windows else None,'detector.last_sample_time_s':last['end_s'] if last else None,'result.mercury_mass_kg':result['mercury_mass_kg'],'result.mass_fraction_kg_kg':result['mercury_mass_fraction_kg_kg'],'result.concentration_kg_m3':result['mercury_concentration_kg_m3'],'blank.valid':self._blank() is not None,'calibration.ready':self._curve() is not None,'storage.result_count':len(self._records),'storage.curve_count':len(self._curves),'safety.warning':self.warning}
  if visibility!='agent':v['truth.ledger']=self._ledger()
  return v
 def _ledger(self):
  unburned=sum(s['remaining'] for s in self.samples.values());residue=sum(s['residue'] for s in self.samples.values());held=sum(s['hg']*(1-s['burn_fraction']) for s in self.samples.values());vol=sum(s['cfg']['volume_m3']*(1-s['consumed_fraction']) for s in self.samples.values());reacted=self.oxygen_reacted*M_O2;p=self.plant
  hg=dict(registered_kg=self.hg_registered,held_kg=held,returned_kg=self.hg_returned,transport_kg=self.hg_transport,gold_kg=self.hg_gold,optical_cell_kg=self.hg_cell,exhaust_kg=self.hg_exhaust,unrecovered_kg=self.hg_unrecovered,removed_kg=self.hg_removed,residual_kg=self.hg_registered-held-self.hg_returned-self.hg_transport-self.hg_gold-self.hg_cell-self.hg_exhaust-self.hg_unrecovered-self.hg_removed)
  gases=dict(oxygen_initial_mol=p['oxygen_supply_mol'] if p else 0.,remaining_mol=self.oxygen_remaining,exhaust_mol=self.oxygen_exhaust,reacted_mol=self.oxygen_reacted,residual_mol=(p['oxygen_supply_mol'] if p else 0.)-self.oxygen_remaining-self.oxygen_exhaust-self.oxygen_reacted)
  volume=dict(registered_m3=self.vol_registered,held_m3=vol,consumed_m3=self.vol_consumed,returned_m3=self.vol_returned,residual_m3=self.vol_registered-vol-self.vol_consumed-self.vol_returned)
  stored=sum(t-p['ambient_temperature_k'] for t in self.temps.values())*p['thermal_capacity_j_k'] if p else 0.
  return dict(sample_registered_kg=self.registered,sample_remaining_kg=unburned,sample_returned_kg=self.returned,residue_kg=residue,residue_removed_kg=self.residue_removed,reaction_products_kg=self.products,oxygen_reacted_kg=reacted,mass_residual_kg=self.registered+reacted-unburned-self.returned-residue-self.residue_removed-self.products,mercury_ledger_json=json.dumps(hg,sort_keys=True),volume_ledger_json=json.dumps(volume,sort_keys=True),gas_ledger_json=json.dumps(gases,sort_keys=True),electrical_energy_j=self.electrical,thermal_stored_j=stored,thermal_lost_j=self.thermal_lost,energy_residual_j=self.electrical-stored-self.thermal_lost)
 def invoke(self,capability,**arguments):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],arguments)
  if capability in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  result=self._invoke(capability,arguments);validate(CAPS[capability][1],result)
  if CAPS[capability][4]!='query':self.revision+=1
  return copy.deepcopy(result)
 def _invoke(self,k,a):
  if k=='plant.configure':
   if self.plant is not None or self.time_s!=0:raise ActionRejected('INITIALIZATION_ONLY')
   self.plant=copy.deepcopy(a);self.rng=np.random.default_rng(a['seed']);self.temps={x:a['ambient_temperature_k'] for x in self.temps};self.target=a['ambient_temperature_k'];self.oxygen_remaining=a['oxygen_supply_mol'];self._library_hash=hashlib.sha256(json.dumps(self.library,sort_keys=True,allow_nan=False).encode()).hexdigest();return {'configured':True}
  if k=='template.read':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_TEMPLATE')
   return {'template_json':json.dumps(self.templates[a['substance_id']],ensure_ascii=False,sort_keys=True,allow_nan=False)}
  if k=='sample.inventory':return {'slots':[dict(slot_index=i,occupied=i in self.samples,processed=self.samples[i]['processed'] if i in self.samples else False) for i in range(40)]}
  if k=='process.measure':return dict(phase=self.phase,stage=self.stage,elapsed_s=self.elapsed_s,progress=self._progress(),absorbance=self._last_point['absorbance'] if self._last_point else None,integrated_absorbance_s=self.area if self.windows else None,mercury_mass_kg=self._result()['mercury_mass_kg'])
  if k=='result.read':return self._result()
  if k=='sequence.read':return dict(phase=self.phase,completed_records=copy.deepcopy(self.completed))
  if k=='signal.read':return dict(windows=copy.deepcopy(self.windows),integrated_absorbance_s=self.area if self.windows else None,wavelength_m=WAVELENGTH_M,evidence='synthetic dual-beam window averages; no original electronic OEM arrays')
  if k=='product.read':return self._ledger()
  if k=='result.export':
   if a['format']=='json':content=json.dumps(self._result(),ensure_ascii=False,allow_nan=False)
   else:
    stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=list(POINT['properties']));writer.writeheader();writer.writerows(self.windows);content=stream.getvalue()
   return {'format':a['format'],'content':content}
  if k=='blank.read':return {'valid':self._blank() is not None,'blank_json':json.dumps(self._blank(),allow_nan=False)}
  if k=='records.list':return dict(record_ids=list(self._records),curve_ids=list(self._curves))
  if k=='records.read':
   if a['record_id'] not in self._records:raise ActionRejected('UNKNOWN_RECORD')
   return self._records[a['record_id']]
  if k=='calibration.read':
   if a['curve_id'] not in self._curves:raise ActionRejected('UNKNOWN_CURVE')
   return {'curve_json':json.dumps(self._curves[a['curve_id']],allow_nan=False)}
  if self.plant is None:raise ActionRejected('PLANT_NOT_CONFIGURED')
  if k=='power.set':
   if a['enabled'] and self.cover!=0:raise ActionRejected('COVER_OPEN')
   if not a['enabled'] and self.phase=='running':self._stop('cancelled','power removed')
   self.powered=a['enabled'];return {'enabled':self.powered}
  if k=='gas.set':
   if a['enabled'] and self.oxygen_remaining<=0:raise ActionRejected('OXYGEN_UNAVAILABLE')
   if not a['enabled'] and self.phase=='running':self._stop('cancelled','carrier removed')
   self.gas=a['enabled'];return {'enabled':self.gas}
  if k=='measurement.stop':
   if self.phase=='running':self._stop('cancelled','host stop')
   return {'phase':self.phase}
  if k in ['measurement.start','sequence.start']:return self._start([a['slot_index']] if k=='measurement.start' else a['slot_indices'])
  self._idle()
  if k=='cover.set':
   if not self._home():raise ActionRejected('TRANSFER_NOT_HOME')
   if not self._cold():raise ActionRejected('COLD_MAINTENANCE_REQUIRED')
   if self.dust_cover!=0:raise ActionRejected('DUST_COVER_OPEN')
   self.cover=1.35 if a['opened'] else 0.;return {'opened':a['opened']}
  if k=='dust_cover.set':
   if not self._home() or self.cover!=0:raise ActionRejected('SAMPLE_ACCESS')
   if abs(self.carousel-self.selected_slot*2*math.pi/40)>1e-9:raise ActionRejected('CAROUSEL_MOVING')
   self.dust_cover=1.65 if a['opened'] else 0.;return {'opened':a['opened']}
  if k=='sample.load':
   i=a['slot_index']
   if self.dust_cover==0 or self.cover!=0 or not self._home():raise ActionRejected('SAMPLE_ACCESS')
   if i in self.samples:raise ActionRejected('SLOT_OCCUPIED')
   if a['input_form']=='liquid' and (a['volume_m3']<=0 or a['boat_material']!='quartz'):raise ActionRejected('LIQUID_QUARTZ_BOAT_REQUIRED')
   if a['input_form']!='liquid' and a['volume_m3']!=0:raise ActionRejected('VOLUME_IS_LIQUID_ONLY')
   if a['input_form']=='collected_gas_sorbent' and (not self.plant['optional_collected_trap_installed'] or a['boat_material']!='collected_trap'):raise ActionRejected('COLLECTED_TRAP_OPTION_REQUIRED')
   if a['input_form']!='collected_gas_sorbent' and a['boat_material']=='collected_trap':raise ActionRejected('BOAT_FORM_MISMATCH')
   if a['kind']=='blank':
    if a['template_id'] or a['host_mercury_mass_kg'] is not None:raise ActionRejected('BLANK_CONTENT')
    hg=0.;reference=''
   elif a['template_id']:
    if a['template_id'] not in self.templates:raise ActionRejected('UNKNOWN_TEMPLATE')
    r=self.templates[a['template_id']]
    if not r.get('physical_input_eligible',False):raise ActionRejected('REFERENCE_QUERY_ONLY')
    if r.get('input_form')!=a['input_form']:raise ActionRejected('SOURCE_FORM_MISMATCH')
    if a['host_mercury_mass_kg'] is not None:raise ActionRejected('AMBIGUOUS_MERCURY_INPUT')
    if not a['accept_source_condition_mismatch']:raise ActionRejected('SOURCE_CONDITION_ACK_REQUIRED')
    if a['kind']=='standard' and not r.get('calibration_eligible',False):raise ActionRejected('REFERENCE_NOT_CALIBRATION')
    hg=_amount(r,a);reference=a['template_id']
   else:
    if a['kind']=='standard':raise ActionRejected('SOURCE_STANDARD_REQUIRED')
    if a['host_mercury_mass_kg'] is None:raise ActionRejected('MERCURY_INPUT_REQUIRED')
    hg=a['host_mercury_mass_kg'];reference=''
   if not math.isfinite(hg) or hg<0 or hg>a['mass_kg']:raise ActionRejected('MERCURY_MASS_EXCEEDS_SAMPLE')
   self.samples[i]=dict(cfg=copy.deepcopy(a),hg=hg,reference_id=reference,remaining=a['mass_kg'],dry_fraction=0.,burn_fraction=0.,consumed_fraction=0.,residue=0.,processed=False);self.registered+=a['mass_kg'];self.hg_registered+=hg;self.vol_registered+=a['volume_m3'];return {'loaded_slot':i}
  if k=='sample.unload':
   if self.dust_cover==0 or self.cover!=0 or not self._home():raise ActionRejected('SAMPLE_ACCESS')
   if not a['recipient_id'] or a['slot_index'] not in self.samples:raise ActionRejected('UNKNOWN_SAMPLE')
   s=self.samples.pop(a['slot_index']);self.returned+=s['remaining'];self.hg_returned+=s['hg']*(1-s['burn_fraction']);self.vol_returned+=s['cfg']['volume_m3']*(1-s['consumed_fraction']);self.residue_removed+=s['residue'];return dict(unburned_mass_kg=s['remaining'],residue_mass_kg=s['residue'])
  if k=='carousel.select':
   if not self._home():raise ActionRejected('TRANSFER_NOT_HOME')
   if not self.powered or self.cover!=0 or self.dust_cover!=0:raise ActionRejected('CAROUSEL_INTERLOCK')
   self.selected_slot=a['slot_index'];return {'selected_slot':self.selected_slot}
  if k=='method.configure':
   if a['decomposition_temperature_k']<a['drying_temperature_k'] or a['record_s']<a['release_s'] or a['window_s']>a['record_s'] or math.ceil(a['record_s']/a['window_s'])>MAX_WINDOWS or a['range_min_kg']>=a['range_max_kg']:raise ActionRejected('INVALID_METHOD')
   self.method=copy.deepcopy(a);return {'configured':True}
  if k=='calibration.fit':return self._fit(a)
  if k=='calibration.select':
   if a['curve_id'] not in self._curves:raise ActionRejected('UNKNOWN_CURVE')
   if self._curves[a['curve_id']]['acquisition_signature']!=self._key():raise ActionRejected('CALIBRATION_SIGNATURE_MISMATCH')
   self._selected_curve=a['curve_id'];return {'selected':a['curve_id']}
  if k=='records.remove':
   if a['record_id'] not in self._records:raise ActionRejected('UNKNOWN_RECORD')
   del self._records[a['record_id']];return {'removed':True}
  if k=='product.drain':
   if not self._cold() or self.gas or not self._home():raise ActionRejected('COLD_MAINTENANCE_REQUIRED')
   if not a['recipient_id']:raise ActionRejected('INVALID_ARGUMENT')
   amount=self.hg_transport+self.hg_gold+self.hg_cell;self.hg_removed+=amount;self.hg_transport=self.hg_gold=self.hg_cell=0.;return {'removed_mercury_kg':amount}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _start(self,indices):
  self._idle()
  if not self._ready() or not self._home():raise ActionRejected('NOT_READY')
  if len(set(indices))!=len(indices):raise ActionRejected('DUPLICATE_SLOT')
  if any(i not in self.samples or self.samples[i]['processed'] for i in indices):raise ActionRejected('SAMPLE_MISSING_OR_PROCESSED')
  if self.oxygen_remaining<=0:raise ActionRejected('OXYGEN_UNAVAILABLE')
  if len(self._records)+len(indices)>MAX_RESULTS:raise ActionRejected('RESULT_CAPACITY')
  # All preconditions before any sequencing mutation. A leading blank may prepare following samples.
  blank_available=self._blank() is not None
  for i in indices:
   if self.samples[i]['cfg']['kind']=='blank':blank_available=True
   elif not blank_available:raise ActionRejected('MATCHED_BLANK_REQUIRED')
  self.queue=list(indices);self.queue_index=0;self.completed=[];self.phase='running';self.warning='';self._operation_counter+=1;self.operation_id=f'{self.instance_id}_operation_{self._operation_counter}';self._begin_assay(indices[0]);return {'operation_id':self.operation_id}
 def _begin_assay(self,i):
  self.current_slot=i;self.selected_slot=i;self.elapsed_s=0.;self.windows=[];self._last_point=None;self.area=0.;self._pending_s=self._pending_r=self._pending_dt=0.;self._pending_end=self.time_s;self._result_cache=None;self._run_method=copy.deepcopy(self.method);self._run_key=self._key();self._run_blank=copy.deepcopy(self._blank());self._run_curve=copy.deepcopy(self._curve());self._run_kind=self.samples[i]['cfg']['kind'];self._record_saved=False;self._saved_progress=0.;self._set_stage('selecting')
 def _set_stage(self,stage):self.stage=stage;self.stage_elapsed=0.
 def _stage_duration(self):
  m=self._run_method or self.method
  return {'drying':m['drying_s'],'decomposing':m['decomposition_s'],'purging':m['purge_s'],'recording':m['record_s']}.get(self.stage,1.)
 def _result(self):
  if self._result_cache:return copy.deepcopy(self._result_cache)
  s=self.samples.get(self.current_slot);cfg=s['cfg'] if s else SAMPLE_EXAMPLE
  return dict(record_id='',slot_index=self.current_slot if self.current_slot is not None else 0,kind=self._run_kind,status=self.phase,complete=False,windows=copy.deepcopy(self.windows),integrated_absorbance_s=self.area if self.windows else None,blank_corrected_integral_s=None,mercury_mass_kg=None,mercury_mass_fraction_kg_kg=None,mercury_concentration_kg_m3=None,uncertainty_kg=None,sample_mass_kg=cfg['mass_kg'],sample_volume_m3=cfg['volume_m3'],acquisition_signature=self._run_key,acquisition_method_json=json.dumps(self._run_method,sort_keys=True),evidence='synthetic dual-beam current-window optical observations; source scalar Hg references are not raw curves',template_source=TEMPLATE_REL,quality=['no complete observed result'])
 def _window(self):
  if self._pending_dt<=1e-12:return
  p=self.plant;dt=self._pending_dt;sample=self._pending_s/dt;ref=self._pending_r/dt
  if p['beam_noise_density_sqrt_s']:
   sd=p['beam_noise_density_sqrt_s']/math.sqrt(dt);sample+=float(self.rng.normal(0,sd));ref+=float(self.rng.normal(0,sd))
  ns=sample-p['sample_dark_relative'];nr=ref-p['reference_dark_relative'];ratio=ns/(nr*p['sample_beam_ratio']) if ns>0 and nr>0 else None;A=-math.log10(ratio) if ratio is not None and math.isfinite(ratio) and ratio>0 else None;saturated=A is None or abs(A)>p['max_absorbance'];self._last_point=dict(start_s=self._pending_end-dt,end_s=self._pending_end,absorbance=A,sample_relative=sample,reference_relative=ref,transmission_ratio=ratio,saturated=saturated);self.windows.append(self._last_point)
  if A is not None:self.area+=A*dt
  self._pending_s=self._pending_r=self._pending_dt=0.
 def _save(self,status):
  if self._record_saved:return
  self._window();r=self._result();s=self.samples[self.current_slot];net=self.area-self._run_blank['integrated_absorbance_s'] if self._run_blank and self.windows else None;quality=[];mass=sd=None
  if status!='succeeded':quality.append('incomplete operation; partial observations and irreversible consumption retained')
  elif not self.windows or any(w['saturated'] for w in self.windows):quality.append('zero/low optical intensity or saturation; no quantitative result')
  elif self._run_curve and net is not None:
   c=self._run_curve;x=(net-c['intercept_s'])/c['slope_s_kg']
   if x<0:quality.append('negative blank-corrected quantity, not clipped to zero')
   elif not c['min_kg']<=x<=c['max_kg']:quality.append('outside actual observed calibration interpolation range')
   elif not self._run_method['range_min_kg']<=x<=self._run_method['range_max_kg']:quality.append('outside selected documented mass-range subset; LOD is not noise sigma')
   else:mass=x;sd=c['residual_sd_s']/c['slope_s_kg']
  else:quality.append('no matching selected observed calibration')
  # A visible final-window excess is a limited end-point check, not an oracle about hidden mercury inventory.
  if status=='succeeded' and self.windows and self._run_blank:
   last=self.windows[-1];b=self._run_blank['windows'][-1]['absorbance'];value=last['absorbance'];threshold=max(1e-5,10*self.plant['beam_noise_density_sqrt_s']/math.sqrt(last['end_s']-last['start_s']))
   if value is not None and b is not None and abs(value-b)>threshold:mass=sd=None;quality.append('observable absorption tail unresolved; recorded window complete but quantitative recovery not established')
  self._record_counter+=1;r.update(record_id=f'record_{self._record_counter}',status=status,complete=status=='succeeded',windows=copy.deepcopy(self.windows),integrated_absorbance_s=self.area if self.windows else None,blank_corrected_integral_s=net,mercury_mass_kg=mass,mercury_mass_fraction_kg_kg=mass/s['cfg']['mass_kg'] if mass is not None else None,mercury_concentration_kg_m3=mass/s['cfg']['volume_m3'] if mass is not None and s['cfg']['input_form']=='liquid' else None,uncertainty_kg=sd,quality=quality or ['conditioned total Hg from observed dual-beam peak; no molecular speciation']);self._result_cache=r;self._records[r['record_id']]=copy.deepcopy(r);self.completed.append(copy.deepcopy(r));self._record_saved=True;s['processed']=True
  if status=='succeeded' and self._run_kind=='blank' and self.windows and not any(w['saturated'] for w in self.windows):self._blanks[self._run_key]=copy.deepcopy(r)
  if status=='succeeded' and self._run_kind=='standard':self._standards[r['record_id']]=dict(reference_id=s['reference_id'],reference_mass_kg=s['hg'])
 def _stop(self,status,warning):
  self._saved_progress=self._progress();self.warning=warning;self._save(status);self.phase=status;self._operation_records[self.operation_id]=self._operation_snapshot()
 def _operation_snapshot(self):return dict(operation_id=self.operation_id,status=self.phase,result=copy.deepcopy(self.completed),current_result=self._result(),clock={'domain':'simulation','time_s':self.time_s},state_revision=self.revision)
 def _fit(self,a):
  if not a['curve_id'] or a['curve_id'] in self._curves:raise ActionRejected('CURVE_EXISTS')
  if len(self._curves)>=MAX_CURVES:raise ActionRejected('CURVE_CAPACITY')
  if len(set(a['record_ids']))!=len(a['record_ids']):raise ActionRejected('DUPLICATE_STANDARD')
  rows=[]
  for rid in a['record_ids']:
   if rid not in self._records or rid not in self._standards:raise ActionRejected('ACTUAL_STANDARD_REQUIRED')
   r=self._records[rid]
   if r['acquisition_signature']!=self._key():raise ActionRejected('CALIBRATION_SIGNATURE_MISMATCH')
   if r['status']!='succeeded' or r['blank_corrected_integral_s'] is None or any(w['saturated'] for w in r['windows']) or any('tail unresolved' in q for q in r['quality']):raise ActionRejected('INVALID_STANDARD_OBSERVATION')
   rows.append(self._standards[rid]|dict(record_id=rid,integrated_absorbance_s=r['blank_corrected_integral_s']))
  x=np.asarray([r['reference_mass_kg'] for r in rows]);y=np.asarray([r['integrated_absorbance_s'] for r in rows]);scale=float(np.max(x))
  if scale<=0 or len(set(x))<3 or np.ptp(x)<scale*1e-9:raise ActionRejected('CALIBRATION_RANGE')
  coef=np.linalg.lstsq(np.column_stack([x/scale,np.ones(len(x))]),y,rcond=None)[0];slope=float(coef[0]/scale);intercept=float(coef[1]);res=y-(slope*x+intercept);ss=float(np.sum((y-y.mean())**2));r2=1-float(res@res)/ss if ss>1e-30 else -1.
  if not all(math.isfinite(v) for v in [slope,intercept,r2]) or slope<=0 or r2<.995:raise ActionRejected('CALIBRATION_FIT_INVALID')
  self._curves[a['curve_id']]=dict(acquisition_signature=self._key(),observations=rows,slope_s_kg=slope,intercept_s=intercept,r_squared=r2,min_kg=float(x.min()),max_kg=float(x.max()),residual_sd_s=float(np.sqrt(res@res/max(1,len(x)-2))),meaning='Declared linear calibration subset from saved synthetic observations and real source reference masses; not OEM full S-curve calibration');return dict(curve_id=a['curve_id'],slope_s_kg=slope,r_squared=r2)
 @staticmethod
 def _approach(x,target,speed,dt):return x+max(-speed*dt,min(speed*dt,target-x))
 def _physical(self,dt):
  p=self.plant
  if not p:return
  m=self._run_method if self.phase=='running' else self.method;ambient=p['ambient_temperature_k'];active=self.phase=='running'
  target=ambient
  if active:
   if self.stage in ['drying_ramp','drying']:target=m['drying_temperature_k']
   elif self.stage in ['decomposition_ramp','decomposing','purging']:target=m['decomposition_temperature_k']
  self.target=target if self.powered else ambient
  goals=dict(furnace=self.target,catalyst=m['catalyst_temperature_k'] if self.powered else ambient,gold=m['release_temperature_k'] if self.powered and active and self.stage in ['release_ramp','recording'] else ambient,cell=393.15 if self.powered else ambient)
  for k,t in goals.items():
   old=self.temps[k];new=self._approach(old,t,p['heating_rate_k_s'] if t>old else p['cooling_rate_k_s'],dt);dE=(new-old)*p['thermal_capacity_j_k'];loss=p['heat_loss_w_k']*max(0.,(old+new)/2-ambient)*dt;self.temps[k]=new
   if dE>=0:self.electrical+=dE+loss;self.thermal_lost+=loss
   else:self.thermal_lost-=dE
  delivered=0.
  if self.gas:
   delivered=min(self.oxygen_remaining,p['oxygen_flow_mol_s']*dt);self.oxygen_remaining-=delivered;self.oxygen_exhaust+=delivered
   if delivered<p['oxygen_flow_mol_s']*dt-1e-16 and active:self._stop('failed','oxygen supply depleted');return
  if not active:
   if delivered>0:
    self._transport_mercury(delivered/p['oxygen_flow_mol_s']);self._flush_cell(dt,delivered)
   if self.powered:
    self.injection=self._approach(self.injection,0.,p['injection_speed_m_s'],dt)
    if self.injection<=1e-9:self.lift=self._approach(self.lift,0.,p['injection_speed_m_s'],dt)
    if self._home():self.carousel=self._approach(self.carousel,self.selected_slot*2*math.pi/40,p['rotation_speed_rad_s'],dt)
   return
  self.elapsed_s+=dt;self.stage_elapsed+=dt;stage_at_entry=self.stage;s=self.samples[self.current_slot]
  if self.stage=='selecting':
   self.carousel=self._approach(self.carousel,self.selected_slot*2*math.pi/40,p['rotation_speed_rad_s'],dt)
   if abs(self.carousel-self.selected_slot*2*math.pi/40)<1e-10:self._set_stage('lifting')
  elif self.stage=='lifting':
   self.lift=self._approach(self.lift,.050,p['injection_speed_m_s'],dt)
   if self.lift>=.050-1e-10:self._set_stage('inserting')
  elif self.stage=='inserting':
   self.injection=self._approach(self.injection,.230,p['injection_speed_m_s'],dt)
   if self.injection>=.230-1e-10:self._set_stage('drying_ramp')
  elif self.stage=='drying_ramp':
   if abs(self.temps['furnace']-m['drying_temperature_k'])<1e-6:self._set_stage('drying')
  elif self.stage=='drying':
   f=min(1.,self.stage_elapsed/m['drying_s']);dm=(s['cfg']['mass_kg']-s['hg'])*p['dry_mass_loss_fraction']*(f-s['dry_fraction']);s['remaining']-=dm;self.products+=dm;s['dry_fraction']=f;newconsumed=(1-s['hg']/s['cfg']['mass_kg'])*p['dry_mass_loss_fraction']*f;self.vol_consumed+=s['cfg']['volume_m3']*(newconsumed-s['consumed_fraction']);s['consumed_fraction']=newconsumed
   if f>=1-1e-10:self._set_stage('decomposition_ramp')
  elif self.stage=='decomposition_ramp':
   if abs(self.temps['furnace']-m['decomposition_temperature_k'])<1e-6:self._set_stage('decomposing')
  elif self.stage=='decomposing':
   f=min(1.,self.stage_elapsed/m['decomposition_s']);df=f-s['burn_fraction'];dm=(s['cfg']['mass_kg']-(s['cfg']['mass_kg']-s['hg'])*p['dry_mass_loss_fraction'])*df;reaction=dm*p['oxygen_demand_mol_kg']
   if reaction>delivered+1e-16:self._stop('failed','declared matrix oxygen demand exceeds actual supplied carrier');return
   s['remaining']=(s['cfg']['mass_kg']-(s['cfg']['mass_kg']-s['hg'])*p['dry_mass_loss_fraction'])*(1-f);s['residue']+=dm*p['residue_fraction'];self.products+=dm*(1-p['residue_fraction'])+reaction*M_O2;self.oxygen_reacted+=reaction;self.oxygen_exhaust-=reaction;hg=s['hg']*df;self.hg_transport+=hg*p['mercury_recovery'];self.hg_unrecovered+=hg*(1-p['mercury_recovery']);s['burn_fraction']=f;dry=(1-s['hg']/s['cfg']['mass_kg'])*p['dry_mass_loss_fraction'];newconsumed=dry+(1-dry)*f;self.vol_consumed+=s['cfg']['volume_m3']*(newconsumed-s['consumed_fraction']);s['consumed_fraction']=newconsumed
   if f>=1-1e-10:self._set_stage('purging')
  elif self.stage=='purging':
   if self.stage_elapsed>=m['purge_s']-1e-10:self._set_stage('release_ramp')
  elif self.stage=='release_ramp':
   if abs(self.temps['gold']-m['release_temperature_k'])<1e-6:self._set_stage('recording')
  elif self.stage=='returning':
   self.injection=self._approach(self.injection,0.,p['injection_speed_m_s'],dt)
   if self.injection<=1e-10:self._set_stage('lowering')
  elif self.stage=='lowering':
   self.lift=self._approach(self.lift,0.,p['injection_speed_m_s'],dt)
   if self.lift<=1e-10:
    self.queue_index+=1
    if self.queue_index==len(self.queue):self.phase='succeeded';self.stage='complete';self._saved_progress=1.;self._operation_records[self.operation_id]=self._operation_snapshot()
    else:self._begin_assay(self.queue[self.queue_index])
  # Catalyst/transport separates mobile Hg from gold-retained Hg. Uncaptured capacity overflow is never hidden success.
  self._transport_mercury(dt)
  if stage_at_entry=='recording':
   if self.stage_elapsed<=m['release_s']+1e-10:
    released=self.hg_gold*(-math.expm1(-dt/p['release_tau_s']));self.hg_gold-=released;self.hg_cell+=released
   mean_mass=self._flush_cell(dt)
   absorbance=p['baseline_absorbance']+p['absorption_coefficient_m2_kg']*p['optical_path_m']*mean_mass/p['cell_volume_m3'];lamp=p['lamp_relative']*(1+p['common_drift_fraction']*math.sin(2*math.pi*self.time_s/p['common_drift_period_s']));ref=lamp+p['reference_dark_relative'];sample=lamp*p['sample_beam_ratio']*10**(-min(absorbance,300.))+p['sample_dark_relative'];self._pending_r+=ref*dt;self._pending_s+=sample*dt;self._pending_dt+=dt;self._pending_end=self.time_s
   if self._pending_dt>=m['window_s']-1e-10:self._window()
   if self.stage_elapsed>=m['record_s']-1e-10:self._save('succeeded');self._set_stage('returning')
  else:self._flush_cell(dt)
 def _transport_mercury(self,dt):
  p=self.plant;transported=self.hg_transport*(-math.expm1(-dt/p['transport_tau_s']));self.hg_transport-=transported;captured=min(transported,max(0.,p['gold_capacity_kg']-self.hg_gold));self.hg_gold+=captured;self.hg_unrecovered+=transported-captured
 def _flush_cell(self,dt,delivered_mol=None):
  p=self.plant;flow=(p['oxygen_flow_mol_s'] if delivered_mol is None else delivered_mol/dt)*R_GAS*self.temps['cell']/p['cell_pressure_pa'];tau=p['cell_volume_m3']/flow;departed=self.hg_cell*(-math.expm1(-dt/tau));self.hg_cell-=departed;self.hg_exhaust+=departed;return departed*tau/dt
 def step(self,dt_s):
  validate(number('s',1e-9,3600),dt_s);remaining=float(dt_s)
  while remaining>1e-12:
   dt=min(.01,remaining)
   if self.phase=='running' and self.stage in ['drying','decomposing','purging','recording']:
    dt=min(dt,max(1e-12,self._stage_duration()-self.stage_elapsed))
    if self.stage=='recording':
     dt=min(dt,max(1e-12,self._run_method['window_s']-self._pending_dt))
     if self.stage_elapsed<self._run_method['release_s']-1e-12:dt=min(dt,self._run_method['release_s']-self.stage_elapsed)
   self.time_s+=dt;self._physical(dt);remaining-=dt
  self.revision+=1
  return self.read('evaluator')

class MilestoneDma80Session:
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
        m=export_milestone_dma80_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        def timing(key):
            if key in ['detector.absorbance','detector.sample_relative','detector.reference_relative','detector.transmission_ratio'] and h._last_point:return h._last_point['end_s'],[h._last_point['start_s'],h._last_point['end_s']]
            if key.startswith('result.') or key=='detector.integrated_absorbance_s':
                if h.windows:return h.windows[-1]['end_s'],[h.windows[0]['start_s'],h.windows[-1]['end_s']]
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
'DMA-80 evo Double Beam is one low-pressure mercury lamp with sample/reference beams180 degrees apart and two UV photodiodes at253.65nm; it is not the three-detector Tricell. A=-log10(corrected sample/reference ratio). Intensities are relative, not watts or recovered OEM ADC values.',
'Only total elemental mercury mass is determined. No methylmercury/inorganic mercury or other molecular speciation. Real reference scalar masses/fractions may drive conditioned sample input but never become instrument readouts without actual synthetic optical observations and observed calibration.',
'LOD0.001ng=1e-15kg is a source specification, not noise sigma. Documented0.01–1500ng=1e-14..1.5e-9kg is the available analytical range; user range_min/max only select a subset, not unverified factory cell switches. Precision<=1% at5ng is not a complete response/noise model.',
'Reported uncertainty_kg is only standard-fit residual SD divided by observed sensitivity, not propagated certificate/aliquot uncertainty or a complete uncertainty budget. Three or more actual saved source-standard peak integrals fit a declared linear subrange. This is not the OEM full calibration recommendation of at least10points, nonlinear/S-curve behavior or Tricell cell0/1/2 calibration. Negative, missing calibration, poor optical signals, tail excess or out-of-range results remain null with diagnostics.',
'Forty separate occupied sample positions and sequential assays are modeled. The one empty moving boat is a host-loaded transfer fixture proxy with a safe reference clearance above the carousel. No dynamic attachment/real gripping between slot and fixture, forty-sample visibility, robot handling or exact pneumatic kinematics is claimed.',
'Routine loading uses the independent transparent dust cover with the main maintenance cover closed. The main cover requires cold power-off access and the dust cover closed. Dust-cover operations require idle sequencing, stationary carousel and a home transfer fixture; measuring and indexing require it closed. These conservative model guards do not establish OEM hardware interlocks or implement on-the-fly loading/preprocessing of ten queued samples.',
'Gas means an already collected Hg sorbent trap installed by a trusted host with the declared external trap option. Direct gas flow sampling, sampled air volume and conversion to air mercury concentration are not implemented. Liquids require a declared quartz boat and explicit positive host volume; Hg mass-fraction references do not imply a liquid density.',
'Temperature configuration bounds and the short example durations/ramp rates are explicit numerical-fixture assumptions, not undocumented factory hardware limits. Dry-mass source references require an explicit host dry_mass_fraction; actual weighed mass and liquid volume remain independent. Source minimum sample sizes/homogeneity and reference conditions are not automatically validated by this model. Drying removes a declared fraction of non-Hg matrix; thermal decomposition releases declared Hg into a finite transport/catalyst compartment, gold retains Hg up to a host capacity, thermal release sends Hg into a well-mixed optical cell and carrier exhaust. Matrix oxygen demand/recovery, temperature rates, capacities, residence times and absorption coefficients are explicit host assumptions, not factory kinetics or molecular reaction validation.',
'Gas uses explicit oxygen mol/s and finite inventory. Actual optical-cell ideal-gas residence derives from its declared volume,absolute pressure and temperature; source ca100mL/min and4bar lack reference T/P or pressure basis, so they are not silently converted into these host quantities. Optional air carrier is not implemented.',
'Each completed window averages two physical-model beam intensities before logarithmic conversion; intensity noise is sampled only on a completed/partial-cancel window. Both beams share lamp drift, with declared dark/background factors. No detector counts, original time arrays or absolute radiometry claimed. Saturation/low light protect undefined logarithms.',
'Public final-window excess against matched blank is a limited engineering tail QC, not OEM endpoint software or an oracle about hidden Hg. Cancellation retains burned matrix, Hg in catalyst/gold/cell, consumed carrier, partial observed windows and all previous sequence results; mobile/cell Hg continues carrier-driven transport while gas remains on, even outside acquisitions; cleanup moves residue to waste without recreating samples.',
'Gross sample plus reacted oxygen balances remaining/returned sample,residue and explicitly uncharacterized reaction products. Hg has a separate elemental balance across held/returned,transport,gold,optical cell,exhaust,unrecovered and disposed compartments. These are different ledgers, not a claim to identify all products or avoid real Hg contamination.',
'Body dimensions.800W*.420D*.300H m have source support; conflicting56kg and84kg masses remain unknown/null, as explicitly required. Local CAD,part masses,thermal parameters,friction and seal dimensions are unknown. Six numeric regions are a simulation-only UI; original separate6.5inch control terminal/GUI is not reproduced by these readings.',
'Only host step advances all temperature,transport,motion and observation windows. Session returns and errors enforce process-local roles, request deduplication and owner-bound historical terminal snapshots. This does not prove OS/filesystem separation, restart persistence, OEM hardware/native protocol or Isaac/Blender/robot integration; those require independent reports.'
]
SAMPLE['properties']['dry_mass_fraction']['description']='Required for a dry-mass source reference. Gross host weighing multiplied by this independently declared dry fraction; no implied Hg-preserving in-instrument drying correction.'
SAMPLE['properties']['mass_kg']['description']='Actual gross sample weighing, including separately quantified moisture; no automatic density or CRM minimum-aliquot certification.'
SAMPLE['properties']['volume_m3']['description']='Actual host-declared liquid volume only; required positive for liquid, zero for solid/trap. No density inferred from a mass-fraction CRM.'
PLANT['properties']['cell_pressure_pa']['description']='Explicit absolute optical-cell pressure assumption; not manufacturer unspecified4bar supply.'

def export_milestone_dma80_contract_metadata():
 caps={};slots={};run=['measurement.phase','measurement.stage','measurement.elapsed_s','measurement.progress','sequence.progress','sequence.current_slot','sequence.completed_count'];motion=['carousel.angle_rad','transfer.lift_m','injection.position_m'];thermal=['temperature.actual_k','temperature.target_k','catalyst.actual_k','amalgamator.actual_k','cell.actual_k'];obs=['detector.absorbance','detector.sample_relative','detector.reference_relative','detector.transmission_ratio','detector.integrated_absorbance_s','detector.last_sample_time_s'];res=['result.mercury_mass_kg','result.mass_fraction_kg_kg','result.concentration_kg_m3'];ready=['instrument.ready','blank.valid','calibration.ready'];end=[*run,*obs,*res,'storage.result_count','truth.ledger','safety.warning']
 immediate={'plant.configure':['plant.configured',*thermal,'truth.ledger'],'power.set':['power.enabled',*end,'instrument.ready'],'gas.set':['gas.enabled',*end,'instrument.ready'],'cover.set':['cover.angle_rad','instrument.ready'],'dust_cover.set':['dust_cover.angle_rad','instrument.ready'],'sample.load':['sample.count','truth.ledger'],'sample.unload':['sample.count','truth.ledger'],'carousel.select':['carousel.selected_slot'],'method.configure':ready,'measurement.start':[*run,*obs,*res,'carousel.selected_slot','safety.warning'],'sequence.start':[*run,*obs,*res,'carousel.selected_slot','safety.warning'],'measurement.stop':end,'calibration.fit':['storage.curve_count'],'calibration.select':['calibration.ready'],'records.remove':['storage.result_count'],'product.drain':['truth.ledger']}
 onstep={'power.set':[*thermal,*motion,'truth.ledger','instrument.ready'],'gas.set':['truth.ledger',*end,'instrument.ready'],'method.configure':[*thermal,'truth.ledger','instrument.ready'],'carousel.select':['carousel.angle_rad'],'measurement.start':[*run,*thermal,*motion,*obs,*res,'storage.result_count','truth.ledger','blank.valid','carousel.selected_slot','safety.warning'],'sequence.start':[*run,*thermal,*motion,*obs,*res,'storage.result_count','truth.ledger','blank.valid','carousel.selected_slot','safety.warning'],'measurement.stop':['transfer.lift_m','injection.position_m',*thermal,'truth.ledger']}
 observes={'plant.configure':['plant.configured'],'power.set':['power.enabled'],'gas.set':['gas.enabled'],'cover.set':['cover.angle_rad'],'dust_cover.set':['dust_cover.angle_rad'],'sample.load':['sample.count'],'sample.inventory':['sample.count'],'carousel.select':['carousel.selected_slot'],'measurement.start':['measurement.phase'],'sequence.start':['measurement.phase'],'measurement.stop':['measurement.phase'],'process.measure':['measurement.phase','measurement.stage','measurement.elapsed_s','measurement.progress','detector.absorbance','detector.integrated_absorbance_s',res[0]],'result.read':[*res,'detector.integrated_absorbance_s'],'sequence.read':['measurement.phase','sequence.completed_count'],'signal.read':obs,'blank.read':['blank.valid'],'records.list':['storage.result_count','storage.curve_count'],'product.read':['truth.ledger']}
 for k,(s,u,origin,vis,parts,write) in SLOTS.items():
  mode='integral' if k=='detector.integrated_absorbance_s' else 'interval_mean' if k in obs[:4] else 'instant'
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=parts,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=origin,visibility=vis,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'MilestoneDma80Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode=mode,time_domain='simulation',window_description='Actual completed window or cumulative recorded optical interval; query clock never substitutes for sample time.' if mode!='instant' else None),source_ids=['simulation_assumption'])
 errors=sorted({n.args[0].value for n in ast.walk(ast.parse(Path(__file__).read_text())) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='ActionRejected' and n.args and isinstance(n.args[0],ast.Constant) and isinstance(n.args[0].value,str)})
 for k,(inp,out,parts,label,kind,vis,example) in CAPS.items():
  unsupported=k in UNSUPPORTED;async_=k in ['measurement.start','sequence.start'];effects=[dict(state_id=s,description_zh=SLOT_ZH[s],timing=t) for t,mapping in [('immediate',immediate),('on_step',onstep)] for s in dict.fromkeys(mapping.get(k,[]))]
  caps[k]=dict(function_zh=label,kind=kind,visibility=vis,component_ids=parts,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation={layer:('implemented' if layer in ['process','host'] and not unsupported else 'absent') for layer in ['process','scene','robot','hardware','host']},binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.milestone_dma80.MilestoneDma80Session.invoke',invocation=f'session.invoke(instance_id="dma80_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if kind=='query' or unsupported else [dict(description_zh='真实入口校验独立舟位/样品形式/冷态接近/热与气条件/实际已保存标线签名，量程判断取观测不读隐藏Hg',state_ids=['power.enabled','instrument.ready','measurement.phase'],enforced_by='runtime',check='Runtime._invoke/_start/_fit and authenticated Session')],state_effects=[] if unsupported or kind=='query' else effects,observes=[] if unsupported else observes.get(k,[]),model_effects=dict(mode='none',description_zh='需独立几何及实际场景绑定验证，查询没有模型运动',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='全部所选舟完成实际窗口采集与夹具归位；取消保留当前及之前记录' if async_ else '原子返回；运动和物理仅由step推进',completion_state_ids=['measurement.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','sample_positions','transfer','decomposition_furnace','gold_trap','double_beam_detector','oxygen'],partial_failure_zh='保留消耗物料、Hg各仓及完整/部分吸光窗口；不会恢复已燃烧样品'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in errors],example_arguments=copy.deepcopy(example),verification=[],limitations=LIMITATIONS,source_ids=['manufacturer_primary','simulation_assumption'])
 mech=dict(component_ids=['carousel','transfer_lift','transfer_carriage','decomposition_furnace','catalyst','amalgamator','absorption_cell','sample_photodiode','reference_photodiode'],state_ids=list(slots),meaning_zh='热分解/催化输运/金捕集热释放与双光束原子吸收的有限仓模型',implementation='lumped',equations=['Registered Hg = held+returned+transport+gold+cell+exhaust+unrecovered+disposed (kg).','Gold capture/release and transport use explicit host first-order time constants and finite gold capacity.','Q_cell=n_dot_O2*R*T_cell/P_cell; tau=V_cell/Q_cell, explicit actual cell conditions.','A=-log10((I_sample-dark_sample)/(I_reference-dark_reference)/declared_beam_ratio), using actual window-averaged relative intensities.','Observed area=sum(A_window*window_duration); matched blank subtraction, actual source-standard OLS, interpolation only.','Registered gross sample + reacted oxygen = remaining+returned+residue+removed_residue+uncharacterized reaction products.','Electrical energy = stored thermal energy + rejected heat; no invented combustion enthalpy.'],parameters={},source_ids=['manufacturer_primary','simulation_assumption'],limitations=LIMITATIONS)
 spec=importlib.util.spec_from_file_location('_dma80_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'thermal_amalgamation_aas':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE)})
 return dict(capabilities=caps,state_slots=slots,mechanisms={'thermal_amalgamation_aas':mech})

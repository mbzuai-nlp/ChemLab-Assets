"""CHEM-089: finite graphite fusion, CuO conversion, NDIR/TC observations and observed calibration.
All kinetics, gains, thermal properties and noise below are explicit host assumptions.
"""
from __future__ import annotations
import ast,copy,csv,hashlib,importlib.util,io,json,math
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2];SLUG='leco_onh836';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-089';PROTOCOL_VERSION=ASSET_VERSION='1.0.0'
TEMPLATE_REL='assets/expansion_batch04/t3_analytical/leco_onh836/output_templates/library.json'
ELEMENTS=['O','N','H'];M={'O':.015999,'N':.014007,'H':.001008,'C':.012011};RGAS=8.31446261815324
LIMITS={'He':{'O':[5e-11,5e-5],'N':[5e-11,3e-5],'H':[1e-10,2.5e-6]},'Ar':{'O':[5e-11,5e-5],'N':[2e-10,3e-5],'H':[1e-10,2.5e-6]}}
MAX_RECORDS=256;MAX_CURVES=64
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
def ed(s):return obj({e:copy.deepcopy(s) for e in ELEMENTS})
def zeros():return {e:0. for e in ELEMENTS}
def nulls():return {e:None for e in ELEMENTS}
PLANT=obj(dict(seed=integer(0,2**32-1),ambient_temperature_k=number('K',288.15,308.15),thermal_capacity_j_k=number('J/K',.05,1e5),cooling_w_k=number('W/K',.01,1e4),maintenance_temperature_k=number('K',288.15,353.15),maximum_temperature_k=number('K',1000,6000),motion_speed_m_s=number('m/s',.001,2),helium_inventory_mol=number('mol',0,1000),argon_inventory_mol=number('mol',0,1000),gas_reference_pressure_pa=number('Pa',1e4,3e5),gas_reference_temperature_k=number('K',200,400),cuo_oxygen_inventory_mol=number('mol',0,10),co2_scrubber_capacity_mol=number('mol',0,10),water_scrubber_capacity_mol=number('mol',0,10),dfc_maximum_mol_s=number('mol/s',0,.1),fusion_release_rate_s=ed(number('s^-1',.01,1000)),outgas_rate_s=number('s^-1',.01,1000),transport_tau_s=number('s',.001,10),oxygen_to_co_fraction=number('1',0,1),response_gain_s_kg=ed(number('s/kg',1,1e12)),argon_response_fraction=ed(number('1',.01,100)),baseline_relative=ed(number('1',-1,1)),noise_density_sqrt_s=ed(number('s^0.5',0,.1)),maximum_relative=number('1',.01,1e6),coolant_ready=BOOL,pneumatic_ready=BOOL))
PLANT_EXAMPLE=dict(seed=890,ambient_temperature_k=298.15,thermal_capacity_j_k=.3,cooling_w_k=2.,maintenance_temperature_k=303.15,maximum_temperature_k=5000.,motion_speed_m_s=.8,helium_inventory_mol=2.,argon_inventory_mol=2.,gas_reference_pressure_pa=101325.,gas_reference_temperature_k=298.15,cuo_oxygen_inventory_mol=.1,co2_scrubber_capacity_mol=.1,water_scrubber_capacity_mol=.1,dfc_maximum_mol_s=.01,fusion_release_rate_s={'O':24.,'N':20.,'H':28.},outgas_rate_s=30.,transport_tau_s=.03,oxygen_to_co_fraction=.8,response_gain_s_kg={'O':1e6,'N':5e6,'H':8e6},argon_response_fraction={'O':1.,'N':.55,'H':1.},baseline_relative={'O':.001,'N':.002,'H':.001},noise_density_sqrt_s={'O':1e-6,'N':1e-6,'H':1e-6},maximum_relative=1000.,coolant_ready=True,pneumatic_ready=True)
METHOD=obj(dict(carrier=enum(['He','Ar']),selected_elements=arr(enum(ELEMENTS),1,3),carrier_flow_m3_s=number('m^3/s',1e-8,1e-4),outgas_power_w=number('W',0,7500),outgas_s=number('s',.01,600),purge_s=number('s',.01,600),baseline_s=number('s',.01,600),fusion_power_w=number('W',1,7500),fusion_s=number('s',.02,600),integration_s=number('s',.01,600),window_s=number('s',.005,10),endpoint_relative=number('1',1e-9,10)))
METHOD_EXAMPLE=dict(carrier='He',selected_elements=ELEMENTS.copy(),carrier_flow_m3_s=490e-6/60,outgas_power_w=6000.,outgas_s=.16,purge_s=.12,baseline_s=.08,fusion_power_w=4800.,fusion_s=.4,integration_s=.3,window_s=.02,endpoint_relative=.001)
CRUCIBLE=obj(dict(graphite_mass_kg=number('kg',1e-5,.02),blank_element_masses_kg=ed(number('kg',0,1e-6))))
CRUCIBLE_EXAMPLE=dict(graphite_mass_kg=.002,blank_element_masses_kg={'O':1e-9,'N':2e-10,'H':1e-10})
SAMPLE=obj(dict(template_id=TEXT,kind=enum(['sample','standard','blank']),mass_kg=number('kg',0,.001),declared_elements=arr(enum(ELEMENTS),0,3),mass_basis=enum(['as_received','dry_mass','source_conditioned']),dry_mass_fraction=nullable(number('1',1e-6,1)),accept_source_condition_mismatch=BOOL))
SAMPLE_EXAMPLE=dict(template_id='leco_503558_reference',kind='sample',mass_kg=.00012,declared_elements=ELEMENTS.copy(),mass_basis='source_conditioned',dry_mass_fraction=None,accept_source_condition_mismatch=True)
BLANK_EXAMPLE=dict(template_id='',kind='blank',mass_kg=0.,declared_elements=[],mass_basis='as_received',dry_mass_fraction=None,accept_source_condition_mismatch=True)
WINDOW=obj(dict(start_s=number('s'),end_s=number('s'),stage=TEXT,complete=BOOL,raw_relative=ed(number('1')),corrected_relative=ed(number('1')),gas_channels_relative=obj({k:number('1') for k in ['co','co2_low','co2_high','h2o','n2']})))
RESULT=obj(dict(record_id=TEXT,status=TEXT,kind=TEXT,complete=BOOL,carrier=TEXT,sample_mass_kg=number('kg'),selected_elements=arr(enum(ELEMENTS),1,3),windows=arr(WINDOW,0,300000),baseline_relative=ed(number('1')),integrals_s=ed(number('s')),blank_integrals_s=nullable(ed(number('s'))),net_integrals_s=nullable(ed(number('s'))),element_mass_kg=ed(nullable(number('kg'))),element_mass_fractions=ed(nullable(number('1'))),element_quality=ed(arr(TEXT,0,20)),acquisition_signature=TEXT,calibration_id=nullable(TEXT),standard_reference=nullable(obj(dict(template_id=TEXT,mass_kg=number('kg'),element_mass_kg=ed(nullable(number('kg')))))),provenance_json=TEXT))
CURVE=obj(dict(curve_id=TEXT,acquisition_signature=TEXT,carrier=TEXT,elements=arr(enum(ELEMENTS),1,3),parameters=ed(nullable(obj(dict(slope_s_kg=number('s/kg'),intercept_s=number('s'),r_squared=number('1'),mass_domain_kg=arr(number('kg'),2,2),response_domain_s=arr(number('s'),2,2))))),record_ids=arr(TEXT,3,100),created_s=number('s')))
LEDGER=obj(dict(elements_json=TEXT,species_json=TEXT,gas_inventory_json=TEXT,unknown_matrix_registered_kg=number('kg'),unknown_matrix_remaining_kg=number('kg'),unknown_matrix_returned_kg=number('kg'),unknown_matrix_disposed_kg=number('kg'),unknown_matrix_residual_kg=number('kg'),carbon_registered_kg=number('kg'),carbon_remaining_kg=number('kg'),carbon_gas_kg=number('kg'),carbon_disposed_kg=number('kg'),carbon_residual_kg=number('kg'),cuo_oxygen_initial_mol=number('mol'),cuo_oxygen_remaining_mol=number('mol'),cuo_oxygen_used_mol=number('mol'),cuo_oxygen_residual_mol=number('mol'),co2_captured_mol=number('mol'),water_captured_mol=number('mol'),co2_bypass_mol=number('mol'),water_bypass_mol=number('mol'),unconverted_co_mol=number('mol'),unconverted_h2_mol=number('mol'),dfc_supplied_mol=number('mol'),dfc_deficit_mol=number('mol'),electrical_energy_j=number('J'),thermal_stored_j=number('J'),coolant_heat_j=number('J'),energy_residual_j=number('J')))
CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',example=None):CAPS[k]=(i,o,c,z,kind,vis,example or {})
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body'],'宿主响应/有限反应库存初始化','configuration','evaluator',PLANT_EXAMPLE)
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','impulse_furnace'],'逻辑供电；断电保留耗样',example={'enabled':True})
cap('gas.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['carrier_gas_inlet'],'He或Ar供气开关，载气类型由方法定',example={'enabled':True})
cap('coolant.set',obj({'ready':BOOL}),obj({'ready':BOOL}),['impulse_furnace'],'宿主液冷可用性；失效中断熔融','configuration','evaluator',{'ready':True})
cap('cover.set',obj({'opened':BOOL}),obj({'target_m':number('m',0,.160)}),['load_head_cover'],'加载头罩有限维护升降','device_command','evaluator',{'opened':False})
cap('loading.set',obj({'loading':BOOL}),obj({'target_m':number('m',0,.045)}),['loading_slide'],'装样漏斗/观察窗有限切换',example={'loading':True})
cap('furnace.open',obj({'opened':BOOL}),obj({'target_m':number('m',0,.1)}),['lower_electrode'],'冷态下电极下降置换坩埚',example={'opened':True})
cap('sample_gate.set',obj({'opened':BOOL}),obj({'target_m':number('m',0,.04)}),['drop_gate'],'无样冷态下滑闸有限维护动作','device_command','evaluator',{'opened':False})
cap('crucible.install',CRUCIBLE,obj({'installed':BOOL}),['graphite_crucible','lower_electrode'],'宿主置新石墨坩埚与背景库存','device_command','evaluator',CRUCIBLE_EXAMPLE)
cap('crucible.remove',obj({'recipient_id':TEXT}),obj({'disposed':BOOL}),['graphite_crucible'],'取出使用坩埚，残余归废物而非还原原样','device_command','evaluator',{'recipient_id':'host_crucible_waste'})
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_inlet','loading_slide'],'称重无机固体留在加载头，组成受限','device_command','evaluator',SAMPLE_EXAMPLE)
cap('sample.unload',obj({'recipient_id':TEXT}),obj({'returned_mass_kg':number('kg',0)}),['sample_inlet'],'仅允许未落入炉的原样返还','device_command','evaluator',{'recipient_id':'host_sample_return'})
cap('method.configure',METHOD,obj({'configured':BOOL}),['impulse_furnace','oxygen_detector','hydrogen_detector','nitrogen_detector'],'功率/载气/实际积分窗/三元素分析','configuration',example=METHOD_EXAMPLE)
cap('measurement.start',obj({}),obj({'operation_id':TEXT}),['impulse_furnace','drop_gate','graphite_crucible','cuo_furnace','oxygen_detector','hydrogen_detector','nitrogen_detector'],'单次同坩埚有限脱气、落样、熔融与观测')
cap('measurement.stop',obj({}),obj({'phase':TEXT}),['impulse_furnace','drop_gate'],'中断保留已耗样、石墨、试剂及部分窗口')
cap('process.measure',obj({}),obj({'phase':TEXT,'stage':TEXT,'elapsed_s':number('s'),'progress':number('1'),'total_relative':nullable(number('1'))}),['display_panel'],'最近过程与真实窗口纯读','query')
cap('result.read',obj({}),RESULT,['display_panel'],'实际模拟观测积分和标线推断，不返回样品真值','query')
cap('trace.read',obj({}),obj({'windows':arr(WINDOW,0,300000),'evidence':TEXT}),['oxygen_detector','hydrogen_detector','nitrogen_detector'],'原窗口O/N/H及CO/CO2/H2O/N2相对信号','query')
cap('blank.read',obj({}),obj({'valid':BOOL,'record_id':nullable(TEXT),'integrals_s':nullable(ed(number('s')))}),['display_panel'],'同方法真实坩埚空白','query')
cap('calibration.fit',obj({'record_ids':arr(TEXT,3,100),'elements':arr(enum(ELEMENTS),1,3)}),CURVE,['display_panel'],'实际保存标准观察面积拟合，不直接返回真实质量','analysis',example={'record_ids':['standard_1','standard_2','standard_3'],'elements':ELEMENTS.copy()})
cap('calibration.select',obj({'curve_id':TEXT}),obj({'selected':TEXT}),['display_panel'],'选择当前载气及响应匹配的标线',example={'curve_id':'curve_1'})
cap('calibration.read',obj({}),obj({'curve':nullable(CURVE)}),['display_panel'],'标线观测域和拟合参数','query')
cap('records.list',obj({}),obj({'record_ids':arr(TEXT,0,MAX_RECORDS),'curve_ids':arr(TEXT,0,MAX_CURVES)}),['display_panel'],'进程内记录目录；非OEM容量','query')
cap('records.read',obj({'record_id':TEXT}),RESULT,['display_panel'],'保存的公开实际观测','query',example={'record_id':'record_1'})
cap('records.remove',obj({'record_id':TEXT}),obj({'removed':TEXT}),['display_panel'],'删除结果，受引用的空白/标准拒绝',example={'record_id':'record_1'})
cap('result.export',obj({'format':enum(['json','csv'])}),obj({'media_type':TEXT,'data':TEXT,'evidence':TEXT}),['display_panel'],'完整观测窗口和量值CSV/JSON纯读','query',example={'format':'csv'})
cap('template.read',obj({'template_id':TEXT}),obj({'template_id':TEXT,'record_json':TEXT,'evidence':TEXT}),['display_panel'],'来源原条目查询，不推未知组分','query',example={'template_id':'leco_503558_reference'})
cap('product.read',obj({}),LEDGER,['impulse_furnace','graphite_crucible','cuo_furnace'],'受限物种和资源账本纯读','query','evaluator')
UNSUPPORTED={'hardware.connect','software.gui','sample.autosampler','maintenance.autocleaner','calibration.gas_dose','speciation.identify','flux.automatic_addition'}
for k in sorted(UNSUPPORTED):cap(k,obj({}),obj({}),['body'],'明确未实现的选件/硬件/物种能力')
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,origin='derived',vis='agent',parts=('display_panel',),write=None):SLOTS[k]=(s,u,origin,vis,list(parts),write);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','宿主有限响应模型已配置','setpoint',parts=('body',))
slot('power.enabled',BOOL,'1','逻辑供电','setpoint',parts=('power_inlet',),write=('power.set','enabled'))
slot('gas.enabled',BOOL,'1','有限载气供给','setpoint',parts=('carrier_gas_inlet',),write=('gas.set','enabled'))
slot('coolant.ready',BOOL,'1','宿主液冷可用；非实测流量','setpoint',parts=('impulse_furnace',))
for k,p,hi in [('cover.lift_m','load_head_cover',.160),('loading.position_m','loading_slide',.045),('sample_gate.position_m','drop_gate',.04),('furnace.opening_m','lower_electrode',.1)]:slot(k,number('m',0,hi),'m','独立部件工程直线坐标，方向见URDF','scene',parts=(p,))
slot('furnace.actual_power_w',number('W',0,7500),'W','实际脉冲电功率工程控制值，非整机功耗',parts=('impulse_furnace',))
slot('furnace.temperature_k',number('K',0),'K','集总炉温代理，真实峰温未知',parts=('impulse_furnace',))
slot('crucible.installed',BOOL,'1','石墨坩埚存在，不证明机器人装配','setpoint',parts=('graphite_crucible',))
slot('sample.loaded',BOOL,'1','加载头或坩埚中有当前样品','derived',parts=('sample_inlet',))
slot('sample.dropped',BOOL,'1','实际已释放入坩埚，不可假还样',parts=('drop_gate',))
slot('sample.mass_kg',number('kg',0,.001),'kg','公开称样质量，1g为本模型范围上界而非厂家最大容量','setpoint',parts=('sample_inlet',))
slot('method.carrier',enum(['He','Ar']),'1','当前He或Ar载气方法，单次无切换','setpoint',parts=('carrier_gas_inlet',))
slot('instrument.ready',BOOL,'1','模型供气/液冷/气动/机构/耗材允许启动')
for k,z,s,u in [('phase','生命周期',TEXT,'1'),('stage','有限脱气/落样/熔融/积分阶段',TEXT,'1'),('elapsed_s','仿真世界耗时',number('s',0),'s'),('progress','有限过程进度',number('1',0,1),'1')]:slot('measurement.'+k,s,u,z)
for e in ELEMENTS:
 slot('result.'+e.lower()+'_fraction',nullable(number('1')),'1',e+'公开推断质量分数kg/kg，未标定/量程外null')
 slot('result.'+e.lower()+'_mass_kg',nullable(number('kg')),'kg',e+'从实际积分标线推断绝对质量')
slot('detector.total_relative',nullable(number('1')),'1','最近实际采样窗三通道绝对相对信号和，不是总元素量')
slot('detector.last_sample_time_s',nullable(number('s')),'s','最近窗口末时刻')
slot('blank.valid',BOOL,'1','当前方法匹配空白已实际采集')
slot('calibration.ready',BOOL,'1','当前载气/方法/响应签名匹配标线')
slot('storage.result_count',integer(0,MAX_RECORDS),'1','进程内工程记录容量')
slot('storage.curve_count',integer(0,MAX_CURVES),'1','进程内工程标线容量')
slot('safety.warning',TEXT,'1','公开过程异常，不泄露源组成')
slot('truth.ledger',LEDGER,'1','受限O/N/H/石墨/CuO/载气/热账本','process_truth','evaluator',('impulse_furnace',))

slot('sample.declared_elements',arr(enum(ELEMENTS),0,3),'1','宿主公开报告范围；未报告不是零','setpoint',parts=('sample_inlet',))
slot('led.furnace_on',BOOL,'1','炉工作区真实照明的工程开关映射，非OEM固件协议',parts=('impulse_furnace',))

PLANT['properties']['gas_reference_pressure_pa']['description']='Explicit absolute gas reference pressure; not the OEM22psi incoming supply whose gauge/absolute basis is unspecified.'
PLANT['properties']['gas_reference_temperature_k']['description']='Explicit reference temperature for the declared gasvolume flow, not an inferred OEM STP value.'
METHOD['properties']['carrier_flow_m3_s']['description']='Carrier volume rate at host declared gas_reference_pressure_pa andgas_reference_temperature_k; OEM490cc/min reference T/P unspecified.'
CRUCIBLE['properties']['graphite_mass_kg']['description']='Finite carbon-equivalent graphite inventory; backgroundO/N/H added separately, not a falsely measured gross crucible composition.'
SAMPLE['properties']['declared_elements']['description']='Explicit publicly reportable source element coverage. Missing elements remainunknown, notzero; mustmatch declarednon-null sourcefields.'
SAMPLE['properties']['mass_kg']['description']='Gross host weighing; runtime0–1g is an engineering scope. Manufacturer1g is nominal, not a claimed physicalmaximum.'

def _library():return json.loads((ROOT/TEMPLATE_REL).read_text())
def _move(x,t,v,d):return x+max(-v*d,min(v*d,t-x))
def _chain(r,q,k,l,d):
 a=math.exp(-k*d);b=math.exp(-l*d);rn=r*a;qn=q*b+(r*k*d*b if abs(l-k)<1e-10 else r*k*(a-b)/(l-k));return rn,qn,max(0.,r+q-rn-qn)
class LecoOnh836Runtime:
 def __init__(self,instance_id='onh_01',library=None):
  self.instance_id=instance_id;self.library=copy.deepcopy(library) if library is not None else _library();self.templates=self.library.get('compounds',{});self._library_hash=hashlib.sha256(json.dumps(self.library,sort_keys=True,allow_nan=False).encode()).hexdigest();self.plant=None;self.method=copy.deepcopy(METHOD_EXAMPLE);self.time_s=0.;self.revision=0;self.powered=False;self.gas_enabled=False;self.coolant=False;self.temperature=298.15;self.actual_power=0.;self.q={k:0. for k in ['cover.lift_m','loading.position_m','sample_gate.position_m','furnace.opening_m']};self.targets=self.q.copy();self.phase='idle';self.stage='idle';self.elapsed_s=0.;self.warning='';self.crucible=False;self.sample=None;self.dropped=False;self.registered=zeros();self.held=zeros();self.remaining=zeros();self.background=zeros();self.transport=zeros();self.detected=zeros();self.returned=zeros();self.disposed=zeros();self.matrix_registered=self.matrix_remaining=self.matrix_returned=self.matrix_disposed=0.;self.carbon_registered=self.carbon=self.carbon_gas=self.carbon_disposed=0.;self.cuo=self.cuo_used=0.;self.co2_captured=self.water_captured=self.co2_bypass=self.water_bypass=self.unconverted_co=self.unconverted_h2=0.;self.dfc_supplied=self.dfc_deficit=0.;self.gas={'He':0.,'Ar':0.};self.gas_used={'He':0.,'Ar':0.};self.gases_produced={k:0. for k in ['CO_pre','CO2_pre','H2_pre','N2','CO2_post','H2O_post']};self.heating=self.cooling_heat=0.;self._records={};self._curves={};self._blanks={};self._selected=None;self._operation_counter=self._record_counter=0;self.operation_id=None;self._operation_records={};self._owners={};self._requests={};self._principals={'__public_agent__':'agent'};self.windows=[];self._last_window=None;self._result=None;self._stage_elapsed=0.;self._pending_dt=0.;self._pending=zeros();self._pending_gases={k:0. for k in ['co','co2_low','co2_high','h2o','n2']};self._window_start=0.;self._baseline=zeros();self._baseline_integral=zeros();self._baseline_dt=0.;self._integrals=zeros();self._quality=[];self._run_key='';self._run_kind='blank';self._run_curve=None;self._run_standard=None;self._run_mass=0.;self._current_crucible_used=False
 def register_principal(self,principal_id,visibility):
  if not isinstance(principal_id,str) or not principal_id or visibility not in ['agent','debug','evaluator']:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals and self._principals[principal_id]!=visibility:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return LecoOnh836Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _cold(self):return bool(self.plant and self.temperature<=self.plant['maintenance_temperature_k'])
 def _settled(self):return all(abs(self.q[k]-v)<1e-12 for k,v in self.targets.items())
 def _closed(self):return self._settled() and all(v==0 for v in self.q.values())
 def _ready(self):return bool(self.plant and self.powered and self.gas_enabled and self.coolant and self.plant['pneumatic_ready'] and self._closed() and self.crucible and not self._current_crucible_used and self.sample is not None and self.gas[self.method['carrier']]>0)
 def _signature(self):
  keys=['fusion_release_rate_s','outgas_rate_s','transport_tau_s','oxygen_to_co_fraction','response_gain_s_kg','argon_response_fraction','baseline_relative','noise_density_sqrt_s','maximum_relative','gas_reference_pressure_pa','gas_reference_temperature_k','dfc_maximum_mol_s']
  return hashlib.sha256(json.dumps({'method':self.method,'response':{k:self.plant[k] for k in keys} if self.plant else {},'library':self._library_hash},sort_keys=True).encode()).hexdigest()
 def _blank(self):return self._blanks.get(self._signature()) if self.plant else None
 def _curve(self):
  c=self._curves.get(self._selected);return c if c and c['acquisition_signature']==self._signature() else None
 def _duration(self):return sum(self.method[k] for k in ['outgas_s','purge_s','baseline_s','fusion_s','integration_s'])+.08/self.plant['motion_speed_m_s'] if self.plant else 1.
 def _progress(self):return 1. if self.phase=='succeeded' else min(1.,self.elapsed_s/self._duration())
 def _operation_snapshot(self):return dict(operation_id=self.operation_id,status=self.phase,progress=self._progress(),elapsed_s=self.elapsed_s,result=copy.deepcopy(self._result) if self.phase!='running' else None)
 def _archive_operation(self):
  if self.operation_id:self._operation_records[self.operation_id]=self._operation_snapshot()
 def debug_snapshot(self):
  d=copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in ['library','templates','_rng']});d['_rng_state']=copy.deepcopy(self._rng.bit_generator.state) if hasattr(self,'_rng') else None;return d
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[k][0],a)
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  snap=self.debug_snapshot()
  try:
   r=self._invoke(k,copy.deepcopy(a));validate(CAPS[k][1],r)
   if CAPS[k][4] not in ['query','analysis']:self.revision+=1;self._archive_operation()
   return copy.deepcopy(r)
  except Exception:
   lib=self.library;rs=snap.pop('_rng_state');self.__dict__.clear();self.__dict__.update(snap);self.library=lib;self.templates=lib.get('compounds',{})
   if rs is not None:self._rng=np.random.default_rng();self._rng.bit_generator.state=rs
   raise
 def _reference(self,i):
  r=self.templates.get(i)
  if r is None:raise ActionRejected('UNKNOWN_TEMPLATE')
  if not r.get('physical_input_eligible') or r.get('input_form')!='solid':raise ActionRejected('REFERENCE_QUERY_ONLY')
  return r
 def _invoke(self,k,a):
  if k=='plant.configure':
   if self.time_s!=0 or self.plant is not None:raise ActionRejected('INITIALIZATION_ONLY')
   if a['maintenance_temperature_k']<a['ambient_temperature_k']:raise ActionRejected('THERMAL_CONFIGURATION')
   self.plant=a;self.temperature=a['ambient_temperature_k'];self.coolant=a['coolant_ready'];self.gas={'He':a['helium_inventory_mol'],'Ar':a['argon_inventory_mol']};self.cuo=a['cuo_oxygen_inventory_mol'];self._rng=np.random.default_rng(a['seed']);return {'configured':True}
  if k=='power.set':
   if not self.plant:raise ActionRejected('NOT_CONFIGURED')
   if not a['enabled'] and self.phase=='running':self._finish('cancelled','POWER_LOSS')
   self.powered=a['enabled']
   if not self.powered:self.actual_power=0.
   return {'enabled':self.powered}
  if k=='gas.set':
   if not self.plant:raise ActionRejected('NOT_CONFIGURED')
   if a['enabled'] and self.gas[self.method['carrier']]<=0:raise ActionRejected('CARRIER_EMPTY')
   if not a['enabled'] and self.phase=='running':self._finish('cancelled','CARRIER_STOP')
   self.gas_enabled=a['enabled'];return {'enabled':self.gas_enabled}
  if k=='coolant.set':
   if not self.plant:raise ActionRejected('NOT_CONFIGURED')
   if not a['ready'] and self.phase=='running':self._finish('failed','COOLANT_LOSS')
   self.coolant=a['ready'];return {'ready':self.coolant}
  if k in ['cover.set','loading.set','furnace.open','sample_gate.set']:
   self._idle()
   if not self.plant or not self.plant['pneumatic_ready']:raise ActionRejected('PNEUMATIC_REQUIRED')
   state,target={'cover.set':('cover.lift_m',.160 if a.get('opened') else 0.),'loading.set':('loading.position_m',.045 if a.get('loading') else 0.),'furnace.open':('furnace.opening_m',.1 if a.get('opened') else 0.),'sample_gate.set':('sample_gate.position_m',.04 if a.get('opened') else 0.)}[k]
   if k!='loading.set' and not self._cold():raise ActionRejected('HOT_MAINTENANCE')
   if k=='cover.set' and self.powered:raise ActionRejected('POWER_OFF_REQUIRED')
   if k=='sample_gate.set' and a['opened'] and self.sample is not None:raise ActionRejected('SAMPLE_RETAINED')
   if k=='furnace.open' and self.sample is not None and not self.dropped:raise ActionRejected('SAMPLE_RETAINED')
   if k=='loading.set' and (self.q['furnace.opening_m']!=0 or self.targets['furnace.opening_m']!=0 or self.q['cover.lift_m']!=0 or self.targets['cover.lift_m']!=0):raise ActionRejected('FURNACE_CLOSED_REQUIRED')
   self.targets[state]=target;return {'target_m':target}
  if k=='crucible.install':
   self._idle()
   if not self._cold() or not self._settled() or self.q['furnace.opening_m']!=.1:raise ActionRejected('FURNACE_OPEN_COLD_REQUIRED')
   if self.crucible:raise ActionRejected('CRUCIBLE_PRESENT')
   if sum(a['blank_element_masses_kg'].values())>a['graphite_mass_kg']:raise ActionRejected('INVALID_CRUCIBLE')
   self.crucible=True;self._current_crucible_used=False;self.carbon=a['graphite_mass_kg'];self.carbon_registered+=self.carbon;self.background=copy.deepcopy(a['blank_element_masses_kg'])
   for e in ELEMENTS:self.registered[e]+=self.background[e]
   return {'installed':True}
  if k=='crucible.remove':
   self._idle()
   if not self.crucible:raise ActionRejected('NO_CRUCIBLE')
   if not self._cold() or self.q['furnace.opening_m']!=.1 or not self._settled():raise ActionRejected('FURNACE_OPEN_COLD_REQUIRED')
   if self.sample is not None and not self.dropped:raise ActionRejected('SAMPLE_RETAINED')
   for e in ELEMENTS:self.disposed[e]+=self.remaining[e]+self.background[e];self.remaining[e]=self.background[e]=0.
   self.carbon_disposed+=self.carbon;self.carbon=0.;self.matrix_disposed+=self.matrix_remaining;self.matrix_remaining=0.;self.crucible=False;self.sample=None;self.dropped=False;return {'disposed':True}
  if k=='sample.load':
   self._idle()
   if not self.crucible or not self._settled() or self.q['loading.position_m']!=.045 or self.q['sample_gate.position_m']!=0 or self.q['furnace.opening_m']!=0:raise ActionRejected('LOAD_POSITION_REQUIRED')
   if self.sample is not None or self._current_crucible_used:raise ActionRejected('SAMPLE_PRESENT')
   if not a['accept_source_condition_mismatch']:raise ActionRejected('SOURCE_CONDITIONS_REQUIRED')
   if len(set(a['declared_elements']))!=len(a['declared_elements']):raise ActionRejected('DUPLICATE_ELEMENT')
   if a['kind']=='blank':
    if a['template_id'] or a['mass_kg']!=0 or a['declared_elements']:raise ActionRejected('INVALID_BLANK')
    vals=nulls();amount=zeros()
   else:
    if a['mass_kg']<=0:raise ActionRejected('NONZERO_SAMPLE_REQUIRED')
    r=self._reference(a['template_id']);vals=r['element_mass_fractions'];basis=r.get('conditions',{}).get('mass_basis','unknown');dry=a['dry_mass_fraction']
    if basis in ['dry_mass','dry'] and dry is None:raise ActionRejected('DRY_FRACTION_REQUIRED')
    source_mass=a['mass_kg']*(dry if basis in ['dry_mass','dry'] else 1.)
    if r.get('conditions',{}).get('source_whole_pin_required_by_host') and abs(a['mass_kg']-r['conditions']['source_sample_mass_kg'])>1e-12:raise ActionRejected('WHOLE_SOURCE_PIN_REQUIRED')
    minimum=r.get('conditions',{}).get('minimum_source_sample_mass_kg')
    if minimum is not None and source_mass<minimum-1e-15:raise ActionRejected('REFERENCE_MINIMUM_ALIQUOT')
    if any(v is not None and (not math.isfinite(v) or not 0<=v<=1) for v in vals.values()) or sum(v or 0 for v in vals.values())>1:raise ActionRejected('INVALID_REFERENCE')
    if set(a['declared_elements'])!={e for e in ELEMENTS if vals.get(e) is not None}:raise ActionRejected('DECLARED_ELEMENTS_MISMATCH')
    amount={e:source_mass*(vals.get(e) or 0.) for e in ELEMENTS}
    if a['kind']=='standard' and not set(self.method['selected_elements'])<=set(r.get('calibration_eligible_elements',[])):raise ActionRejected('REFERENCE_NOT_CALIBRATION_ELIGIBLE')
   self.sample=dict(a,values=vals,amount=amount);self.held=amount.copy();self.dropped=False
   for e in ELEMENTS:self.registered[e]+=amount[e]
   matrix=a['mass_kg']-sum(amount.values());self.matrix_registered+=matrix;self.matrix_remaining+=matrix;return {'loaded':True}
  if k=='sample.unload':
   self._idle()
   if self.sample is None:raise ActionRejected('NO_SAMPLE')
   if self.dropped:raise ActionRejected('SAMPLE_ALREADY_FUSED')
   if not self._settled() or self.q['loading.position_m']!=.045:raise ActionRejected('LOAD_POSITION_REQUIRED')
   mass=self.sample['mass_kg']
   for e in ELEMENTS:self.returned[e]+=self.held[e];self.held[e]=0.
   self.matrix_returned+=self.matrix_remaining;self.matrix_remaining=0.;self.sample=None;return {'returned_mass_kg':mass}
  if k=='method.configure':
   self._idle()
   if not self.plant:raise ActionRejected('NOT_CONFIGURED')
   if len(set(a['selected_elements']))!=len(a['selected_elements']):raise ActionRejected('DUPLICATE_ELEMENT')
   if a['window_s']>min(a['baseline_s'],a['fusion_s'],a['integration_s']):raise ActionRejected('WINDOW_TOO_LONG')
   if sum(math.ceil(a[k]/a['window_s']) for k in ['outgas_s','purge_s','baseline_s','fusion_s','integration_s'])+math.ceil(.08/self.plant['motion_speed_m_s']/a['window_s'])+10>300000:raise ActionRejected('WINDOW_CAPACITY')
   if self.gas_enabled and a['carrier']!=self.method['carrier']:raise ActionRejected('CARRIER_CHANGE_REQUIRES_OFF')
   if a['carrier']!=self.method['carrier'] and sum(self.transport.values())>1e-13:raise ActionRejected('PATH_NOT_FLUSHED')
   self.method=a;return {'configured':True}
  if k=='measurement.start':return self._start()
  if k=='measurement.stop':
   if self.phase=='running':self._finish('cancelled','USER_CANCELLED')
   return {'phase':self.phase}
  if k=='process.measure':return dict(phase=self.phase,stage=self.stage,elapsed_s=self.elapsed_s,progress=self._progress(),total_relative=self._total())
  if k=='result.read':
   if self._result is None:raise ActionRejected('NO_RESULT')
   return self._result
  if k=='trace.read':return dict(windows=self.windows,evidence='synthetic finite-window NDIR/TC surrogate; not OEM electronic release trace')
  if k=='blank.read':
   b=self._blank();return dict(valid=b is not None,record_id=b['record_id'] if b else None,integrals_s=b['integrals_s'] if b else None)
  if k=='calibration.fit':return self._fit(a)
  if k=='calibration.select':
   self._idle();c=self._curves.get(a['curve_id'])
   if c is None:raise ActionRejected('UNKNOWN_CURVE')
   if c['acquisition_signature']!=self._signature():raise ActionRejected('CALIBRATION_MISMATCH')
   self._selected=a['curve_id'];return {'selected':self._selected}
  if k=='calibration.read':return {'curve':self._curve()}
  if k=='records.list':return dict(record_ids=list(self._records),curve_ids=list(self._curves))
  if k=='records.read':
   if a['record_id'] not in self._records:raise ActionRejected('UNKNOWN_RECORD')
   return self._records[a['record_id']]
  if k=='records.remove':
   self._idle();rid=a['record_id']
   if rid not in self._records:raise ActionRejected('UNKNOWN_RECORD')
   if any(rid in q['record_ids'] for q in self._curves.values()) or any(b['record_id']==rid for b in self._blanks.values()):raise ActionRejected('RECORD_IN_USE')
   del self._records[rid];return {'removed':rid}
  if k=='result.export':
   if self._result is None:raise ActionRejected('NO_RESULT')
   if a['format']=='json':return dict(media_type='application/json',data=json.dumps(self._result,sort_keys=True),evidence='synthetic observations; quantities fitted from actual saved standard observations')
   f=io.StringIO();w=csv.writer(f);w.writerow(['start_s','end_s','stage','complete',*[e+'_raw_relative' for e in ELEMENTS],*[e+'_corrected_relative' for e in ELEMENTS]])
   for x in self._result['windows']:w.writerow([x['start_s'],x['end_s'],x['stage'],x['complete'],*[x['raw_relative'][e] for e in ELEMENTS],*[x['corrected_relative'][e] for e in ELEMENTS]])
   return dict(media_type='text/csv',data=f.getvalue(),evidence='actual complete or partial windows, units in headers; source reference values are not detector counts')
  if k=='template.read':
   if a['template_id'] not in self.templates:raise ActionRejected('UNKNOWN_TEMPLATE')
   return dict(template_id=a['template_id'],record_json=json.dumps(self.templates[a['template_id']],sort_keys=True),evidence='source literature/certificate values; no real release trace invented')
  if k=='product.read':return self._ledger()
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _start(self):
  self._idle()
  if not self._ready():raise ActionRejected('INSTRUMENT_NOT_READY')
  if len(self._records)>=MAX_RECORDS:raise ActionRejected('RESULT_STORE_FULL')
  if self.sample['kind']=='standard' and not set(self.method['selected_elements'])<=set(self.templates[self.sample['template_id']].get('calibration_eligible_elements',[])):raise ActionRejected('REFERENCE_NOT_CALIBRATION_ELIGIBLE')
  if sum(self.transport.values())>1e-13:raise ActionRejected('PATH_NOT_FLUSHED')
  if self.cuo<=0 or self.co2_captured>=self.plant['co2_scrubber_capacity_mol'] or self.water_captured>=self.plant['water_scrubber_capacity_mol']:raise ActionRejected('REAGENT_EXHAUSTED')
  self._operation_counter+=1;self.operation_id=self.instance_id+':op:'+str(self._operation_counter);self.phase='running';self.stage='outgas';self.elapsed_s=0.;self._stage_elapsed=0.;self.windows=[];self._last_window=None;self._result=None;self._integrals=zeros();self._baseline=zeros();self._baseline_integral=zeros();self._baseline_dt=0.;self._pending=zeros();self._pending_dt=0.;self._pending_gases={k:0. for k in self._pending_gases};self._window_start=self.time_s;self._quality=[];self.warning='';self._run_key=self._signature();self._run_kind=self.sample['kind'];self._run_mass=self.sample['mass_kg'];self._run_declared=self.sample['declared_elements'];self._run_curve=copy.deepcopy(self._curve());self._run_blank=copy.deepcopy(self._blank());self._run_standard=None
  if self._run_kind=='standard':self._run_standard=dict(template_id=self.sample['template_id'],mass_kg=self.sample['mass_kg'],element_mass_kg={e:self.sample['amount'][e] if e in self._run_declared else None for e in ELEMENTS})
  self._current_crucible_used=True;return {'operation_id':self.operation_id}
 def _stage_duration(self):
  if self.stage in ['gate_open','gate_close']:return .04/self.plant['motion_speed_m_s']
  return self.method[{'outgas':'outgas_s','purge':'purge_s','baseline':'baseline_s','fusion':'fusion_s','integration':'integration_s'}[self.stage]]
 def _next_stage(self):
  self._flush_window(True)
  order=['outgas','purge','baseline','gate_open','gate_close','fusion','integration'];i=order.index(self.stage)
  if self.stage=='baseline':self._baseline={e:self._baseline_integral[e]/max(self._baseline_dt,1e-30) for e in ELEMENTS}
  if self.stage=='gate_open':
   self.dropped=True
   for e in ELEMENTS:self.remaining[e]+=self.held[e];self.held[e]=0.
  if self.stage=='integration':self._finish('succeeded');return
  self.stage=order[i+1];self._stage_elapsed=0.;self._window_start=self.time_s
  if self.stage=='gate_open':self.targets['sample_gate.position_m']=.04
  if self.stage=='gate_close':self.targets['sample_gate.position_m']=0.
 def _physical(self,d):
  p=self.plant;active=self.phase=='running';power=self.method['outgas_power_w'] if active and self.stage=='outgas' else self.method['fusion_power_w'] if active and self.stage=='fusion' else 0.;self.actual_power=power
  h=p['cooling_w_k']*(1. if self.coolant else .02);c=p['thermal_capacity_j_k'];old=self.temperature;eq=p['ambient_temperature_k']+power/h;self.temperature=eq+(old-eq)*math.exp(-h*d/c);self.heating+=power*d;self.cooling_heat+=power*d-c*(self.temperature-old)
  flowing=self.powered and self.gas_enabled and self.gas[self.method['carrier']]>0
  if not flowing:return zeros(),{k:0. for k in self._pending_gases},None
  base=self.method['carrier_flow_m3_s']*p['gas_reference_pressure_pa']/(RGAS*p['gas_reference_temperature_k'])*d;carrier=self.method['carrier'];base=min(base,self.gas[carrier]);self.gas[carrier]-=base;self.gas_used[carrier]+=base
  out=zeros();release=zeros();old_transport=self.transport.copy();unreleased=self.background if active and self.stage=='outgas' else self.remaining;release_active=active and self.stage in ['fusion','outgas']
  for e in ELEMENTS:
   k=(p['outgas_rate_s']*self.method['outgas_power_w']/6000. if self.stage=='outgas' else p['fusion_release_rate_s'][e]*self.method['fusion_power_w']/4800.) if release_active else 0.;r=unreleased[e] if release_active else 0.;rn,qn,escaped=_chain(r,self.transport[e],k,1/p['transport_tau_s'],d);release[e]=r-rn
   if release_active:unreleased[e]=rn
   self.transport[e]=qn;out[e]=escaped
  f=p['oxygen_to_co_fraction'];carbon_need=release['O']/M['O']*(f+(1-f)/2)*M['C'];fault=None
  if carbon_need>self.carbon+1e-20:
   # Keep unreleased sample O, rather than create carbon atoms. Other emitted species persist.
   fraction=self.carbon/carbon_need;excess=release['O']*(1-fraction);unreleased['O']+=excess;total=self.transport['O']+out['O'];reduce=min(excess,max(0.,total-old_transport['O']));ratio=max(0.,(total-reduce)/max(total,1e-30));self.transport['O']*=ratio;out['O']*=ratio;carbon_need=self.carbon;fault='GRAPHITE_EXHAUSTED'
  self.carbon-=carbon_need;self.carbon_gas+=carbon_need
  for e in ELEMENTS:self.detected[e]+=out[e]
  co=out['O']/M['O']*f;co2=out['O']/M['O']*(1-f)/2;h2=out['H']/M['H']/2;n2=out['N']/M['N']/2;need=co+h2;conversion=min(1.,self.cuo/max(need,1e-30));used=need*conversion;self.cuo-=used;self.cuo_used+=used;postco2=co2+co*conversion;water=h2*conversion;self.unconverted_co+=co*(1-conversion);self.unconverted_h2+=h2*(1-conversion)
  if conversion<1.-1e-12:fault='CUO_EXHAUSTED'
  capturedco2=min(postco2,max(0.,p['co2_scrubber_capacity_mol']-self.co2_captured));capturedwater=min(water,max(0.,p['water_scrubber_capacity_mol']-self.water_captured));self.co2_captured+=capturedco2;self.water_captured+=capturedwater;self.co2_bypass+=postco2-capturedco2;self.water_bypass+=water-capturedwater
  if postco2-capturedco2+water-capturedwater>1e-15:fault='SCRUBBER_BREAKTHROUGH'
  needed=capturedco2+capturedwater;dfc=min(needed,p['dfc_maximum_mol_s']*d,self.gas[carrier]);self.gas[carrier]-=dfc;self.gas_used[carrier]+=dfc;self.dfc_supplied+=dfc;self.dfc_deficit+=needed-dfc
  if needed-dfc>1e-15:fault='DFC_FLOW_DEFICIT'
  for k,v in {'CO_pre':co,'CO2_pre':co2,'H2_pre':h2,'N2':n2,'CO2_post':postco2,'H2O_post':water}.items():self.gases_produced[k]+=v
  gains={e:p['response_gain_s_kg'][e]*(p['argon_response_fraction'][e] if carrier=='Ar' else 1.) for e in ELEMENTS}
  # Public engineering gas-channel gain convention permits O reconstruction without hidden composition:
  # O integral = postCO2 - preCO = gO*MO*(2*nCO2post - nCOpre).
  ch=dict(co=co*M['O']*gains['O'],co2_low=2*postco2*M['O']*gains['O'],co2_high=2*co2*M['O']*gains['O'],h2o=2*water*M['H']*gains['H'],n2=2*n2*M['N']*gains['N'])
  signal={'O':ch['co2_low']-ch['co'],'H':ch['h2o'],'N':ch['n2']}
  return signal,ch,fault
 def _flush_window(self,complete):
  d=self._pending_dt
  if d<=1e-14:return
  raw={e:self.plant['baseline_relative'][e]+self._pending[e]/d+float(self._rng.normal(0,self.plant['noise_density_sqrt_s'][e]/math.sqrt(d))) for e in ELEMENTS}
  if any(abs(v)>self.plant['maximum_relative'] for v in raw.values()):
   self._quality.append('detector_saturated');raw={e:max(-self.plant['maximum_relative'],min(self.plant['maximum_relative'],v)) for e,v in raw.items()}
  corrected={e:raw[e]-self._baseline[e] for e in ELEMENTS};row=dict(start_s=self._window_start,end_s=self.time_s,stage=self.stage,complete=complete,raw_relative=raw,corrected_relative=corrected,gas_channels_relative={k:v/d for k,v in self._pending_gases.items()});self.windows.append(row);self._last_window=row
  if self.stage=='baseline':
   self._baseline_dt+=d
   for e in ELEMENTS:self._baseline_integral[e]+=raw[e]*d
  if self.stage in ['fusion','integration']:
   for e in ELEMENTS:self._integrals[e]+=corrected[e]*d
  self._pending_dt=0.;self._pending=zeros();self._pending_gases={k:0. for k in self._pending_gases};self._window_start=self.time_s
 def _finish(self,status,reason=None):
  if self.phase!='running':return
  self._flush_window(status=='succeeded');self.phase=status;self.actual_power=0.
  if status!='succeeded':self.targets['sample_gate.position_m']=self.q['sample_gate.position_m']
  if reason:self.warning=reason;self._quality.append(reason)
  self._record_counter+=1;rid='record_'+str(self._record_counter);b=self._run_blank;net={e:self._integrals[e]-b['integrals_s'][e] for e in ELEMENTS} if b else None;mass=nulls();fractions=nulls();quality={e:[] for e in ELEMENTS};curve=self._run_curve
  tail=[w for w in self.windows if w['stage']=='integration'];endpoint={e:abs(tail[-1]['corrected_relative'][e]) if tail else math.inf for e in ELEMENTS}
  if b:
   for e in ELEMENTS:endpoint[e]=abs((tail[-1]['corrected_relative'][e] if tail else math.inf)-b['endpoint_relative'][e])
  for e in ELEMENTS:
   if e not in self.method['selected_elements']:quality[e].append('not_selected')
   if e not in self._run_declared and self._run_kind!='blank':quality[e].append('unreported_element_unknown_not_zero')
   if status!='succeeded':quality[e].append('incomplete')
   quality[e]+=list(dict.fromkeys(self._quality))
   if endpoint[e]>self.method['endpoint_relative']:quality[e].append('release_endpoint_not_reached')
   if self._run_kind=='blank':quality[e].append('blank_not_sample')
   if net is None:quality[e].append('matching_blank_unavailable')
   if not curve or curve['parameters'][e] is None:quality[e].append('not_calibrated')
   if not quality[e]:
    z=curve['parameters'][e];estimate=(net[e]-z['intercept_s'])/z['slope_s_kg'];lo,hi=LIMITS[self.method['carrier']][e]
    if estimate<0:quality[e].append('negative_inferred_mass')
    elif not lo<=estimate<=hi:quality[e].append('outside_instrument_range')
    elif not z['mass_domain_kg'][0]<=estimate<=z['mass_domain_kg'][1]:quality[e].append('outside_calibration_domain')
    elif self._run_mass<=0:quality[e].append('zero_sample_mass')
    elif estimate>self._run_mass:quality[e].append('inferred_mass_exceeds_sample')
    else:mass[e]=estimate;fractions[e]=estimate/self._run_mass
  r=dict(record_id=rid,status=status,kind=self._run_kind,complete=status=='succeeded',carrier=self.method['carrier'],sample_mass_kg=self._run_mass,selected_elements=self.method['selected_elements'].copy(),windows=copy.deepcopy(self.windows),baseline_relative=self._baseline.copy(),integrals_s=self._integrals.copy(),blank_integrals_s=b['integrals_s'].copy() if b else None,net_integrals_s=net,element_mass_kg=mass,element_mass_fractions=fractions,element_quality=quality,acquisition_signature=self._run_key,calibration_id=curve['curve_id'] if curve else None,standard_reference=copy.deepcopy(self._run_standard),provenance_json=json.dumps(dict(evidence='synthetic finite release/NDIR/TC observations; source materials independent',basis='elemental O/N/H mass, not oxide mass',fraction_denominator='gross host sample mass; a dry source uses explicit dry_mass_fraction for source amount, not silent denominator conversion',library_sha256=self._library_hash,gas_response='Five engineering channels: O=co2_low-co, H=h2o,N=n2; co2_high is pre-CuO CO2 signal (2*gO*MO*nCO2pre); no OEM optical gain claim',blank_record_id=b['record_id'] if b else None,unreported_elements='Unmeasured remainder composition unknown; no modeled contribution from unspecified elements and no absence claim',method=self.method,source_condition_transfer_acknowledged=self.sample['accept_source_condition_mismatch'],source_times='OEM85–130s analysis/180–210s cycle are sourceconditions, this explicit numerical fixture is not a performance reproduction'),sort_keys=True))
  self._records[rid]=r;self._result=r
  if status=='succeeded' and self._run_kind=='blank' and not self._quality:self._blanks[self._run_key]=dict(record_id=rid,integrals_s=self._integrals.copy(),endpoint_relative={e:tail[-1]['corrected_relative'][e] if tail else 0. for e in ELEMENTS})
  self._archive_operation()
 def _fit(self,a):
  self._idle()
  if len(self._curves)>=MAX_CURVES:raise ActionRejected('CURVE_STORE_FULL')
  if len(set(a['record_ids']))!=len(a['record_ids']) or len(set(a['elements']))!=len(a['elements']):raise ActionRejected('DUPLICATE_OBSERVATION')
  if not set(a['elements'])<=set(self.method['selected_elements']):raise ActionRejected('ELEMENT_NOT_SELECTED')
  records=[]
  for rid in a['record_ids']:
   r=self._records.get(rid)
   if r is None:raise ActionRejected('UNKNOWN_RECORD')
   if not r['complete'] or r['kind']!='standard' or r['standard_reference'] is None or r['net_integrals_s'] is None:raise ActionRejected('INVALID_STANDARD_OBSERVATION')
   if r['acquisition_signature']!=self._signature():raise ActionRejected('CALIBRATION_MISMATCH')
   records.append(r)
  params=nulls()
  for e in a['elements']:
   if any(r['standard_reference']['element_mass_kg'][e] is None for r in records):raise ActionRejected('REFERENCE_ELEMENT_UNREPORTED')
   if any(any(q not in ['not_calibrated','outside_calibration_domain'] for q in r['element_quality'][e]) for r in records):raise ActionRejected('STANDARD_QUALITY_INVALID')
   x=np.array([r['standard_reference']['element_mass_kg'][e] for r in records]);y=np.array([r['net_integrals_s'][e] for r in records]);lo,hi=LIMITS[self.method['carrier']][e]
   if len(set(x))<3 or min(x)<lo or max(x)>hi:raise ActionRejected('STANDARD_RANGE_INVALID')
   scale=max(x);coef=np.linalg.lstsq(np.column_stack((x/scale,np.ones(len(x)))),y,rcond=None)[0];slope=float(coef[0]/scale);intercept=float(coef[1]);pred=slope*x+intercept;ss=float(np.sum((y-y.mean())**2));r2=1-float(np.sum((y-pred)**2))/max(ss,1e-30)
   if slope<=0 or r2<.98:raise ActionRejected('CALIBRATION_FIT_INVALID')
   params[e]=dict(slope_s_kg=slope,intercept_s=intercept,r_squared=r2,mass_domain_kg=[float(min(x)),float(max(x))],response_domain_s=[float(min(y)),float(max(y))])
  cid='curve_'+str(len(self._curves)+1);c=dict(curve_id=cid,acquisition_signature=self._signature(),carrier=self.method['carrier'],elements=a['elements'],parameters=params,record_ids=a['record_ids'],created_s=self.time_s);self._curves[cid]=c;self.revision+=1;return c
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<0:raise ActionRejected('INVALID_TIMESTEP')
  if dt_s==0:return
  end=self.time_s+dt_s
  while self.time_s<end-1e-12:
   if not self.plant:self.time_s=end;break
   d=min(end-self.time_s,.02)
   if self.phase=='running':
    if not (self.powered and self.gas_enabled and self.coolant and self.gas[self.method['carrier']]>1e-16):self._finish('failed','UTILITY_LOSS');continue
    d=min(d,self._stage_duration()-self._stage_elapsed,self.method['window_s']-self._pending_dt)
    flow=self.method['carrier_flow_m3_s']*self.plant['gas_reference_pressure_pa']/(RGAS*self.plant['gas_reference_temperature_k']);d=min(d,self.gas[self.method['carrier']]/flow)
   if d<=1e-12:
    if self.phase=='running':
     if self._stage_elapsed>=self._stage_duration()-1e-10:self._next_stage()
     elif self._pending_dt>=self.method['window_s']-1e-10:self._flush_window(True)
     else:self._finish('failed','UTILITY_LOSS')
     continue
    d=end-self.time_s
   for key in self.q:self.q[key]=_move(self.q[key],self.targets[key],self.plant['motion_speed_m_s'],d)
   signal,ch,fault=self._physical(d);self.time_s+=d
   if self.phase=='running':
    self.elapsed_s+=d;self._stage_elapsed+=d;self._pending_dt+=d
    for e in ELEMENTS:self._pending[e]+=signal[e]
    for key in ch:self._pending_gases[key]+=ch[key]
    if self.temperature>self.plant['maximum_temperature_k']:fault='OVERTEMPERATURE'
    if fault:self._finish('failed',fault)
    elif self._stage_elapsed>=self._stage_duration()-1e-10:self._next_stage()
    elif self._pending_dt>=self.method['window_s']-1e-10:self._flush_window(True)
  self.time_s=end;self.revision+=1;self._archive_operation()
 def _total(self):return sum(abs(v) for v in self._last_window['raw_relative'].values()) if self._last_window else None
 def _ledger(self):
  elements={e:dict(registered_kg=self.registered[e],held_kg=self.held[e],unreleased_kg=self.remaining[e]+self.background[e],transport_kg=self.transport[e],detected_terminal_kg=self.detected[e],returned_kg=self.returned[e],disposed_kg=self.disposed[e],residual_kg=self.registered[e]-self.held[e]-self.remaining[e]-self.background[e]-self.transport[e]-self.detected[e]-self.returned[e]-self.disposed[e]) for e in ELEMENTS};p=self.plant or PLANT_EXAMPLE;gas={e:dict(initial_mol=p['helium_inventory_mol' if e=='He' else 'argon_inventory_mol'] if self.plant else 0.,remaining_mol=self.gas[e],exhausted_mol=self.gas_used[e],residual_mol=(p['helium_inventory_mol' if e=='He' else 'argon_inventory_mol'] if self.plant else 0.)-self.gas[e]-self.gas_used[e]) for e in ['He','Ar']};cuoin=p['cuo_oxygen_inventory_mol'] if self.plant else 0.;stored=p['thermal_capacity_j_k']*(self.temperature-p['ambient_temperature_k']) if self.plant else 0.
  return dict(elements_json=json.dumps(elements,sort_keys=True),species_json=json.dumps(dict(cumulative_outlet_mol=self.gases_produced,sample_oxygen_atoms_mol=self.detected['O']/M['O'],reagent_oxygen_atoms_mol=self.cuo_used,oxygen_atoms_residual_mol=self.detected['O']/M['O']+self.cuo_used-2*self.gases_produced['CO2_post']-self.gases_produced['H2O_post']-self.unconverted_co,hydrogen_atoms_residual_mol=self.detected['H']/M['H']-2*self.gases_produced['H2O_post']-2*self.unconverted_h2,nitrogen_atoms_residual_mol=self.detected['N']/M['N']-2*self.gases_produced['N2'],cu_produced_mol=self.cuo_used),sort_keys=True),gas_inventory_json=json.dumps(gas,sort_keys=True),unknown_matrix_registered_kg=self.matrix_registered,unknown_matrix_remaining_kg=self.matrix_remaining,unknown_matrix_returned_kg=self.matrix_returned,unknown_matrix_disposed_kg=self.matrix_disposed,unknown_matrix_residual_kg=self.matrix_registered-self.matrix_remaining-self.matrix_returned-self.matrix_disposed,carbon_registered_kg=self.carbon_registered,carbon_remaining_kg=self.carbon,carbon_gas_kg=self.carbon_gas,carbon_disposed_kg=self.carbon_disposed,carbon_residual_kg=self.carbon_registered-self.carbon-self.carbon_gas-self.carbon_disposed,cuo_oxygen_initial_mol=cuoin,cuo_oxygen_remaining_mol=self.cuo,cuo_oxygen_used_mol=self.cuo_used,cuo_oxygen_residual_mol=cuoin-self.cuo-self.cuo_used,co2_captured_mol=self.co2_captured,water_captured_mol=self.water_captured,co2_bypass_mol=self.co2_bypass,water_bypass_mol=self.water_bypass,unconverted_co_mol=self.unconverted_co,unconverted_h2_mol=self.unconverted_h2,dfc_supplied_mol=self.dfc_supplied,dfc_deficit_mol=self.dfc_deficit,electrical_energy_j=self.heating,thermal_stored_j=stored,coolant_heat_j=self.cooling_heat,energy_residual_j=self.heating-stored-self.cooling_heat)
 def read(self,visibility='agent'):
  if visibility not in ['agent','debug','evaluator']:raise ActionRejected('ACCESS_DENIED')
  r=self._result;v={'plant.configured':self.plant is not None,'power.enabled':self.powered,'gas.enabled':self.gas_enabled,'coolant.ready':self.coolant,**self.q,'furnace.actual_power_w':self.actual_power,'furnace.temperature_k':self.temperature,'crucible.installed':self.crucible,'sample.loaded':self.sample is not None,'sample.dropped':self.dropped,'sample.mass_kg':self.sample['mass_kg'] if self.sample else 0.,'sample.declared_elements':self.sample['declared_elements'] if self.sample else [],'method.carrier':self.method['carrier'],'instrument.ready':self._ready(),'measurement.phase':self.phase,'measurement.stage':self.stage,'measurement.elapsed_s':self.elapsed_s,'measurement.progress':self._progress(),'detector.total_relative':self._total(),'detector.last_sample_time_s':self._last_window['end_s'] if self._last_window else None,'blank.valid':self._blank() is not None,'calibration.ready':self._curve() is not None,'storage.result_count':len(self._records),'storage.curve_count':len(self._curves),'safety.warning':self.warning,'led.furnace_on':self.powered}
  for e in ELEMENTS:v['result.'+e.lower()+'_fraction']=r['element_mass_fractions'][e] if r else None;v['result.'+e.lower()+'_mass_kg']=r['element_mass_kg'][e] if r else None
  if visibility!='agent':v['truth.ledger']=self._ledger()
  return copy.deepcopy(v)

class LecoOnh836Session:
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
        m=export_leco_onh836_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        def timing(key):
            if key in ['detector.total_relative','detector.last_sample_time_s'] and h._last_window:return h._last_window['end_s'],[h._last_window['start_s'],h._last_window['end_s']]
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
'ONH836-C analyzer without touch-screen/PC geometry. One inert-gas graphite-crucible fusion measures O/N/H simultaneously. Helium and argon are separate methods, no carrier changeover during a run. 1g is manufacturer nominal sample mass; this implementation restricts gross mass to0–1g as an explicit engineering scope, not a claimed maximumhardwarecapacity.',
'O absolute range5e-11–5e-5kg; N He5e-11–3e-5kg versusAr2e-10–3e-5kg; H1e-10–2.5e-6kg. OEM lowerlimit is2blankdeviations andprecision1blankdeviation or statedRSD; these aggregate figures are not the synthetic noise sigma. All range checks apply inferredresults, never hidden sample mass content.',
'Power0–7500W is impulsefurnace electricalpower, not totalinstrumentheat. Source490cc/min referenceT/P and22psi pressurebasis unspecified; host supplies explicit gasreference pressure/temperature for m3/s→mol/s. Source85–130s analysis and180–210scycle dependcarrier/analyte. Numerical shortdurations, thermalcapacity/cooling/conversionrates/gain/noise are declared hostfixtures, not acceleratedproof ofsourceperformance.',
'Graphite consumes carbon as sampleO becomes CO/CO2; the host fixes CO branchfraction. CuO consumes separate reagentoxygen converting CO→CO2 and H2→H2O andproducesCu. SampleO does not include reagentO. Gaschannels are a linear response convention: co=gO*MO*nCO,co2low=2*gO*MO*nCO2post,co2high=2*gO*MO*nCO2pre; CO/highCO2 are beforeCuO andlowCO2/H2O afterCuO. O response=co2low-co. This uses the combinedCO/CO2 information without counting reagentoxygen as sampleoxygen. Hresponse=gH*2MH*nH2O, Nresponse=gN*2MN*nN2. These are relative surrogate outputs, not OEM IRabsorbance/current electronics or measured gainratio.',
'Finite analyte/blank release and common transport use a two-compartment linear exact exponential overconstantpower stages. Outgasrate scaleswithP/6000W andfusionratewithP/4800W;0Woutgas causes no blankrelease. These powerproxies are not an Arrhenius/thermalmelting model, even though thermalenergy separately closes. CuOreaction anddownstreamNDIR/scrubbingaftertransport are zeroadditionalresidencetime engineeringapproximations. O/N/H elementalbooks, carboninput/out/disposed, CuOoxygen/copper, capturedCO2/H2O, carrierHe/Ar andDFCsupply are explicit. Unknownmatrix remainsunspecifiedresidue; no oxide/mineralphases orunreportedO/N/H contributions are synthesized.',
'CO2/H2O sorbent capacities are finite molar-equivalent capacities, not an invented reagentmass. Named source magnesiumperchlorate is Mg(ClO4)2; schematic MgClO4 is not used as a chemicalformula. Sodiumhydroxide supportchemistry/copperturningdetails andreal scrubbing breakthrough kinetics are not solved. DFCadds actual samecarrier moles matching removedCO2/H2O up to explicit hostcapacity; deficit flags invalidanalysis, no OEM feedback dynamics claimed.',
'Gross weighing and publicdeclared_elements represent hostinputs. Records withunreported values retain null andunknownremainder; declaredunreported channels returnunavailable regardless syntheticbackground. Source drymass requires explicit dryfraction; source minimumaliquot is respected where known. Existing metals/ceramics are source references, not molecularspeciation. Ni fluxbasket andadditionalgraphite orsource matrices are conditiontransfers, not automaticallyinstalledhardware or demonstratedcompletefusion.',
'Every assay collects actual preanalysisbaseline windows, then dropgate andfusion/tailwindows. Savedmatchingcrucibleblank subtracts actualintegratedbackground. >=3distinctmass observedstandardrecords fitresponsearea versus publicreferenceelementmass using leastsquares. No directtruth-to-result, no arbitraryexternal observations trustedasactualrecords. Calibrations bindcarrier/method/response/library; interpolationonly, endpoint/saturation/reagent/incomplete/negative/rangefail produce null. Certificate/source uncertainty is retained inlibrary but not a validatedmeasurement uncertainty budget.',
'Four finite mechanisms are loadheadmaintenancecover+Z.160m,loading slide+Y.045m,lowerreleasegate+Y.040m,andlower electrode−Z.100m. Cover travel 0.160 m is an engineering estimate: photo-reconstructed cover top is approximately 0.840 m, exposed rear filter tubes set the 0.915 m nominal overall height, and opened cover reaches the source 1.000 m envelope. The OEM specifies overall heights, not cover stroke. Local travels, speeds and cold threshold are engineering proxies. Fixedupper electrode is not lowered. Routine loading usesloadingfunnel withmaintenancecoverclosed. No20-positionautoloader/autocleaner/realparticletrajectory orrobotpickup is implemented. Crucibleinstalled bool supports separatevisualbinding, not actual roboticinsertion.',
'Gas/coolantloss andlogicalpowerstop haltongoingfusion andretainconsumption andpartialwindows. Remainingtransport mayflush under continuedcarrier, unreleasedmaterial staysincrucible, anddiscardmovesit todisposedbooks. Only predrop heldsample mayreturn as originalmaterial. Coldunloadingtemperature is explicit numericalthreshold, not OEM electronicinterlockcertification.',
'All physics andfinite motion advance solely viahoststep; purequery/describe do notconsume oradvance. Atomiccommandrejection restores randomgenerator andstate. Authenticatedper-instance Sessionsupports filteredagent/evaluator views, requestdedup/conflict andownedpoll/cancel. 256records/64curves are engineeringin-memorylimits, not OEMstorage; no cross-processpersistence/OSisolation orhardwareprotocol claim.',
'Six internalSIM readouts are O/N/H inferredfraction, furnacepowerW, totalrelativesignal andprogress, not originalLCD. Furnaceworkareaillumination has a publicboolmapped tologicalpower as an explicitengineeringlightingconvention. Actualfourjoints/cruciblevisibility/LED/nativeintegration require separate geometryandMatterixreports. Originalelectronics, release-timearrays, driftcalibration, gasdosecalibration,fullGUI, autoloader/autocleaner, hardware/HIL/robot remain not_run.'
]
def export_leco_onh836_contract_metadata():
 caps={};slots={};process=['measurement.phase','measurement.stage','measurement.elapsed_s','measurement.progress'];observed=['detector.total_relative','detector.last_sample_time_s']+[f'result.{e.lower()}_{s}' for e in ELEMENTS for s in ['fraction','mass_kg']];ready=['instrument.ready','blank.valid','calibration.ready'];thermal=['furnace.temperature_k','furnace.actual_power_w','truth.ledger'];sample=['sample.loaded','sample.dropped','sample.mass_kg','sample.declared_elements'];end=[*process,*observed,'storage.result_count','safety.warning',*ready]
 immediate={'plant.configure':['plant.configured','coolant.ready',*thermal,*ready],'power.set':['power.enabled','led.furnace_on',*thermal,*end],'gas.set':['gas.enabled',*end,*thermal],'coolant.set':['coolant.ready',*end,*thermal],'cover.set':['instrument.ready'],'loading.set':['instrument.ready'],'furnace.open':['instrument.ready'],'sample_gate.set':['instrument.ready'],'crucible.install':['crucible.installed','truth.ledger',*ready],'crucible.remove':['crucible.installed','truth.ledger',*sample,*ready],'sample.load':[*sample,'truth.ledger',*ready],'sample.unload':[*sample,'truth.ledger',*ready],'method.configure':['method.carrier',*ready],'measurement.start':[*process,*observed,'safety.warning',*ready],'measurement.stop':[*end,*thermal],'calibration.fit':['storage.curve_count'],'calibration.select':['calibration.ready'],'records.remove':['storage.result_count']}
 onstep={'power.set':[*thermal,*ready],'gas.set':['truth.ledger',*ready],'coolant.set':[*thermal,*ready],'cover.set':['cover.lift_m','instrument.ready'],'loading.set':['loading.position_m','instrument.ready'],'furnace.open':['furnace.opening_m','instrument.ready'],'sample_gate.set':['sample_gate.position_m','instrument.ready'],'measurement.start':[*end,*thermal,'sample_gate.position_m','sample.dropped']}
 for k,(s,u,origin,vis,parts,write) in SLOTS.items():
  mode='interval_mean' if k=='detector.total_relative' else 'instant';slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=parts,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=origin,visibility=vis,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'LecoOnh836Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode=mode,time_domain='simulation',window_description='Actual complete or partial acquisition interval; no read-time resampling.' if mode!='instant' else None),source_ids=['simulation_assumption'])
 errors=sorted({n.args[0].value for n in ast.walk(ast.parse(Path(__file__).read_text())) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='ActionRejected' and n.args and isinstance(n.args[0],ast.Constant) and isinstance(n.args[0].value,str)})
 observes={'process.measure':[*process,'detector.total_relative'],'result.read':observed,'trace.read':['detector.total_relative'],'blank.read':['blank.valid'],'calibration.read':['calibration.ready'],'records.list':['storage.result_count','storage.curve_count'],'product.read':['truth.ledger']}
 for k,(inp,out,parts,label,kind,vis,example) in CAPS.items():
  unsupported=k in UNSUPPORTED;async_=k=='measurement.start';effects=[dict(state_id=s,description_zh=SLOT_ZH[s],timing=t) for t,mapping in [('immediate',immediate),('on_step',onstep)] for s in dict.fromkeys(mapping.get(k,[]))]
  caps[k]=dict(function_zh=label,kind=kind,visibility=vis,component_ids=parts,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation={layer:('implemented' if layer in ['process','host'] and not unsupported else 'absent') for layer in ['process','scene','robot','hardware','host']},binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.leco_onh836.LecoOnh836Session.invoke',invocation=f'session.invoke(instance_id="onh_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if kind=='query' or unsupported else [dict(description_zh='实际运行检查有限石墨/载气/CuO/样品、机构/液冷前提及观测标线相容性',state_ids=['power.enabled','instrument.ready','measurement.phase'],enforced_by='runtime',check='Runtime._invoke/_start/_fit and authenticated Session')],state_effects=[] if unsupported or kind=='query' else effects,observes=[] if unsupported else observes.get(k,[]),model_effects=dict(mode='none',description_zh='本模块仅过程；机械/显示/灯及坩埚显隐联动须单独实际scene证据',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='有限脱气/吹扫/采样落闸/熔融与实际积分窗完成；量值需端点与观测标线通过' if async_ else '原子命令返回，物理唯一hoststep推进',completion_state_ids=['measurement.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','graphite_crucible','impulse_furnace','cuo_reagent','carrier_gas','NDIR_TC'],partial_failure_zh='已释放物质、石墨/CuO及气体消耗保留；部分采样窗保存，未释放残样归废物而不假返还'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in errors],example_arguments=copy.deepcopy(example),verification=[],limitations=LIMITATIONS,source_ids=['manufacturer_specification','simulation_assumption'])
 mech=dict(component_ids=['impulse_furnace','graphite_crucible','cuo_furnace','oxygen_detector','hydrogen_detector','nitrogen_detector'],state_ids=list(slots),meaning_zh='有限惰气熔融和三元素质量守恒，CuO气体转化、NDIR/TC真实模拟窗口与实际标准拟合',implementation='lumped',equations=['dr/dt=-kr; dq/dt=kr-q/tau; exact two-compartment stage propagation; cumulative detected += prior r+q-current r-q.', 'O+C→CO or2O+C→CO2; CO+CuO→CO2+Cu; H2+CuO→H2O+Cu; N→N2. ReagentO ledger separate fromsampleO.', 'O response=co2low-co;H=h2o;N=n2, where detector gain conventions are explicit engineeringmodel. Integrals=sum(windowmean*actualwindowduration).', 'netarea=observedarea-matchingactualblankarea; fitarea=slope*knownstandardmass+intercept from>=3 actualstandardrecords; unknownmass=(observednetarea-intercept)/slope, never read sampletruth.', 'n_gas=Qreference*Preference/(R*Treference)*dt; DFCmakeup=removedCO2+H2O constrained byfinitecarrierandmaximumflow.', 'Cthermal*dT/dt=P-h*(T-Tambient); integrated electricalenergy=storedthermal+coolantheat. All unreleased/transport/detected/disposed sampleelements close independently.'],parameters={},source_ids=['manufacturer_specification','simulation_assumption'],limitations=LIMITATIONS)
 spec=importlib.util.spec_from_file_location('_onh_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'inert_fusion_ndir_tc':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'crucible.install':(CRUCIBLE,CRUCIBLE_EXAMPLE)})
 for key,label in {'gas_reference_pressure_pa':'宿主明确绝压气体参考压力，不是未注明表压/绝压的原厂供气压力','gas_reference_temperature_k':'宿主体积流量参考温度，不推定原厂STP','carrier_flow_m3_s':'载气在宿主声明参考温度及绝压下的体积流量；原厂490cc/min参考T/P未知','thermal_capacity_j_k':'炉体集总热容代理；不包含已验证的试样熔化潜热'}.items():
  mech['parameters'][key]['meaning_zh']=label+'；当前值为显式宿主示例，非厂家校准。'
 return dict(capabilities=caps,state_slots=slots,mechanisms={'inert_fusion_ndir_tc':mech})

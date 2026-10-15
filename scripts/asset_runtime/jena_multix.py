"""multi X2500: conditioned combustion/microcoulometry, not molecular identification or OEM metrology."""
from __future__ import annotations
import ast,copy,csv,hashlib,importlib.util,io,json,math
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2];SLUG='jena_multix';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-085';PROTOCOL_VERSION='1.0.0';ASSET_VERSION='1.1.0'
TEMPLATE_REL='assets/expansion_batch04/t3_analytical/jena_multix/output_templates/library.json'
FARADAY=96485.33212331001;M_CL=.03545;M_O2=.031998;MAX_RESULTS=256;MAX_CURVES=32;MAX_WINDOWS=10000
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
PLANT=obj(dict(seed=integer(0,2**32-1),optional_boat_drive_installed=BOOL,optional_pox_installed=BOOL,ambient_temperature_k=number('K',278.15,308.15),heating_rate_k_s=number('K/s',.1,10000),cooling_rate_k_s=number('K/s',.1,10000),thermal_capacity_j_k=number('J/K',1,1e5),heat_loss_w_k=number('W/K',0,100),cold_threshold_k=number('K',308.15,373.15),rotation_speed_rad_s=number('rad/s',.01,20),feed_speed_m_s=number('m/s',.001,2),oxygen_supply_mol=number('mol',0,1000),argon_supply_mol=number('mol',0,1000),oxygen_flow_mol_s=number('mol/s',1e-8,.1),argon_flow_mol_s=number('mol/s',1e-8,.1),oxygen_demand_mol_kg=number('mol/kg',0,1000),solid_residue_fraction=number('1',0,1),combustion_recovery=number('1',.01,1),transport_tau_s=number('s',.01,10),cell_tau_s=number('s',.01,10),silver_initial_mol=number('mol',0,1),detector_gain=number('1',.01,100),baseline_current_a=number('A',-1e-3,1e-3),current_noise_density_a_sqrt_s=number('A*s^0.5',0,1e-3),maximum_current_a=number('A',1e-6,1),cell_temperature_k=number('K',278.15,308.15)))
PLANT_EXAMPLE=dict(seed=850,optional_boat_drive_installed=False,optional_pox_installed=False,ambient_temperature_k=298.15,heating_rate_k_s=2000.,cooling_rate_k_s=2000.,thermal_capacity_j_k=10.,heat_loss_w_k=.2,cold_threshold_k=323.15,rotation_speed_rad_s=3.,feed_speed_m_s=1.,oxygen_supply_mol=10.,argon_supply_mol=10.,oxygen_flow_mol_s=.001,argon_flow_mol_s=.001,oxygen_demand_mol_kg=2.,solid_residue_fraction=.05,combustion_recovery=.98,transport_tau_s=.08,cell_tau_s=.06,silver_initial_mol=.001,detector_gain=1.03,baseline_current_a=2e-7,current_noise_density_a_sqrt_s=0.,maximum_current_a=.1,cell_temperature_k=298.15)
METHOD=obj(dict(mode=enum(['AOX','EOX','POX','TX']),temperature_k=number('K',973.15,1373.15),feeding_s=number('s',.1,30),combustion_s=number('s',.1,120),integration_s=number('s',.5,600),window_s=number('s',.01,10)))
METHOD_EXAMPLE=dict(mode='AOX',temperature_k=1223.15,feeding_s=.4,combustion_s=.6,integration_s=12.,window_s=.2)
HALOGEN=obj(dict(element=enum(['Cl','Br','I','F']),amount_mol=number('mol',0,.001)))
SAMPLE=obj(dict(template_id=TEXT,kind=enum(['sample','standard','blank']),input_form=enum(['loaded_carbon','extract','solid','liquid','purged_gas']),mass_kg=number('kg',1e-9,1e-4),volume_m3=number('m^3',0,1e-7),reference_fraction=number('1',1e-6,1),accept_prepared_input=BOOL,accept_source_condition_mismatch=BOOL,halogen_moles=arr(HALOGEN,0,4)))
SAMPLE_EXAMPLE=dict(template_id='aox_linear_100_r1_c1',kind='sample',input_form='loaded_carbon',mass_kg=5e-5,volume_m3=0.,reference_fraction=.2,accept_prepared_input=True,accept_source_condition_mismatch=True,halogen_moles=[])
STANDARD_EXAMPLE=SAMPLE_EXAMPLE|dict(kind='standard');BLANK_EXAMPLE=SAMPLE_EXAMPLE|dict(kind='blank',template_id='')
POINT=obj(dict(start_s=number('s',0),end_s=number('s',0),current_a=number('A'),observed_charge_c=number('C'),saturated=BOOL))
RESULT=obj(dict(phase=TEXT,complete=BOOL,record_id=TEXT,mode=TEXT,direction=TEXT,windows=arr(POINT,0,MAX_WINDOWS),observed_charge_c=number('C'),blank_corrected_charge_c=nullable(number('C')),chlorine_equivalent_mass_kg=nullable(number('kg')),faraday_equivalent_mass_kg=nullable(number('kg')),uncertainty_kg=nullable(number('kg',0)),quality=arr(TEXT,0,30),acquisition_signature=TEXT,acquisition_method_json=TEXT,evidence=TEXT,template_source=TEXT))
LEDGER=obj(dict(sample_registered_kg=number('kg',0),sample_unburned_kg=number('kg',0),sample_returned_kg=number('kg',0),solid_residue_kg=number('kg',0),residue_removed_kg=number('kg',0),reaction_products_kg=number('kg',0),oxygen_reacted_kg=number('kg',0),mass_residual_kg=number('kg'),equivalent_ledger_json=TEXT,volume_ledger_json=TEXT,species_ledger_json=TEXT,gas_ledger_json=TEXT,charge_generated_c=number('C',0),silver_initial_mol=number('mol',0),silver_remaining_mol=number('mol',0),silver_precipitate_mol=number('mol',0),silver_removed_precipitate_mol=number('mol',0),silver_residual_mol=number('mol'),electrical_energy_j=number('J',0),thermal_stored_j=number('J'),thermal_lost_j=number('J',0),energy_residual_j=number('J')))
CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',example=None):CAPS[k]=(i,o,c,z,kind,vis,example or {})
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body'],'宿主初始化响应/气体/银及显式选件','configuration','evaluator',PLANT_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_inlet'],'装载已预处理输入；真值由宿主管理','device_command','evaluator',SAMPLE_EXAMPLE)
cap('sample.unload',obj({'recipient_id':TEXT}),obj({'unburned_mass_kg':number('kg',0),'residue_mass_kg':number('kg',0)}),['sample_inlet'],'只移出未燃烧样与明确残渣，不归还已消耗样','device_command','evaluator',{'recipient_id':'host'})
cap('cover.set',obj({'door':enum(['main_left','main_right','chlorine']),'opened':BOOL}),obj({'opened':BOOL}),['base_door_left','base_door_right','cl_door'],'可信宿主维护门；热态与活动过程拒绝','device_command','evaluator',{'door':'main_left','opened':False})
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['combustion_furnace','coulometer_cell'],'上电/断电；断电取消并保留不可逆消耗',example={'enabled':True})
cap('gas.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['gas_box'],'开关载气；运行中停气取消并保留残留',example={'enabled':True})
cap('direction.set',obj({'direction':enum(['vertical','horizontal'])}),obj({'direction':TEXT}),['furnace_tilt'],'冷态断电停气的单炉有限倾转；水平需ABD选件','device_command','evaluator',{'direction':'vertical'})
cap('method.configure',METHOD,obj({'configured':BOOL}),['combustion_furnace','coulometer_cell'],'AOX/EOX/POX/TX对应已预处理入口与积分窗口','configuration',example=METHOD_EXAMPLE)
cap('temperature.set',obj({'temperature_k':number('K',298.15,1373.15)}),obj({'target_k':number('K')}),['combustion_furnace'],'设置炉温目标，step有限推进',example={'temperature_k':1223.15})
cap('cell.prepare',obj({'cell_type':enum(['sensitive','high_sensitive','high_concentration'])}),obj({'prepared':BOOL}),['coulometer_cell','silver_anode'],'宿主装妥电解液/电极的声明，不冒充OEM终点标定','device_command','evaluator',{'cell_type':'sensitive'})
cap('measurement.start',obj({}),obj({'operation_id':TEXT}),['sample_inlet','combustion_furnace','coulometer_cell'],'启动消耗型燃烧/微库仑积分')
cap('measurement.stop',obj({}),obj({'phase':TEXT}),['combustion_furnace','coulometer_cell'],'取消保留完整和最后部分积分窗口与已消耗物料')
cap('process.measure',obj({}),obj(dict(phase=TEXT,elapsed_s=number('s',0),progress=number('1',0,1),current_a=nullable(number('A')),charge_c=number('C'),chlorine_equivalent_mass_kg=nullable(number('kg')))),['display_panel'],'纯读当前过程及观测推断','query')
cap('result.read',obj({}),RESULT,['display_panel'],'读取冻结方法、观测窗口与Cl当量推断','query')
cap('signal.read',obj({}),obj(dict(windows=arr(POINT,0,MAX_WINDOWS),observed_charge_c=number('C'),sample_window_s=nullable(arr(number('s',0),2,2)),evidence=TEXT)),['coulometer_cell'],'观测电流与积分电荷，不返回隐藏卤素真值','query')
cap('result.export',obj({'format':enum(['json','csv'])}),obj({'format':TEXT,'content':TEXT}),['display_panel'],'导出SI C/A/s/kg曲线字符串','query',example={'format':'json'})
cap('blank.read',obj({}),obj({'valid':BOOL,'blank_json':TEXT}),['display_panel'],'匹配完整积分方法的真实运行空白电荷','query')
cap('calibration.fit',obj({'curve_id':TEXT,'record_ids':arr(TEXT,3,30)}),obj({'curve_id':TEXT,'r_squared':number('1'),'slope_c_kg':number('C/kg')}),['display_panel'],'真实来源标准已保存观测电荷拟合；不得给任意伪造q','analysis',example={'curve_id':'observed','record_ids':['record_2','record_3','record_4']})
cap('calibration.select',obj({'curve_id':TEXT}),obj({'selected':TEXT}),['display_panel'],'选择方法/方向/响应一致的观测标线',example={'curve_id':'observed'})
cap('calibration.read',obj({'curve_id':TEXT}),obj({'curve_json':TEXT}),['display_panel'],'纯读源参考量与观测拟合','query',example={'curve_id':'observed'})
cap('records.list',obj({}),obj({'record_ids':arr(TEXT,0,MAX_RESULTS),'curve_ids':arr(TEXT,0,MAX_CURVES)}),['display_panel'],'实例独立结果和标线目录','query')
cap('records.read',obj({'record_id':TEXT}),RESULT,['display_panel'],'冻结历史结果查询','query',example={'record_id':'record_1'})
cap('records.remove',obj({'record_id':TEXT}),obj({'removed':BOOL}),['display_panel'],'删除缓存但保留受保护异步历史',example={'record_id':'record_1'})
cap('template.read',obj({'substance_id':TEXT}),obj({'template_json':TEXT}),['display_panel'],'真实记录/回收率/计量参考只读，理论与实测分开','query',example={'substance_id':SAMPLE_EXAMPLE['template_id']})
cap('product.read',obj({}),LEDGER,['combustion_furnace','coulometer_cell'],'受限物种、当量、气体、银、电荷与能量账','query','evaluator')
cap('product.drain',obj({'recipient_id':TEXT}),obj({'drained_equivalents_mol':number('mol',0)}),['coulometer_cell'],'宿主移出气路/滴定池残留和沉淀，不恢复银或样品','device_command','evaluator',{'recipient_id':'chemical_waste'})
UNSUPPORTED={'aox.prepare','eox.extract','pox.purge','toc.measure','icprep.speciate','autosampler.sequence','hardware.connect'}
for k in sorted(UNSUPPORTED):cap(k,obj({}),obj({}),['body'],'未安装/未实现前处理、自动序列或硬件：'+k)
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,origin='derived',vis='agent',components=('display_panel',),write=None):SLOTS[k]=(s,u,origin,vis,list(components),write);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','显式宿主参数配置完成');slot('power.enabled',BOOL,'1','电源目标','setpoint',components=('combustion_furnace',),write=('power.set','enabled'));slot('gas.enabled',BOOL,'1','载气目标，停气不回滚样品','setpoint',components=('gas_box',),write=('gas.set','enabled'))
slot('sample.loaded',BOOL,'1','装载物或已燃烧样品载体仍在位',components=('sample_inlet',))
for door,part in [('main_left','base_door_left'),('main_right','base_door_right'),('chlorine','cl_door')]:slot('cover.'+door+'_angle_rad',number('rad',0,1.6),'rad','独立维护门宿主瞬时工程角','scene',components=(part,))
slot('furnace.direction',enum(['vertical','horizontal']),'1','单炉目标方向，换向需断电停气冷却','setpoint',components=('furnace_tilt',));slot('furnace.angle_rad',number('rad',0,math.pi/2),'rad','单炉有限倾转工程角，不是两炉','scene',components=('furnace_tilt',));slot('injection.position_m',number('m',0,.285),'m','已安装ABD的有限送舟工程行程，非厂家实测','scene',components=('abd_carriage',))
slot('abd_cover.angle_rad',number('rad',0,1.4),'rad','ABD进样口有限盖行程，非厂家实测','scene',components=('abd_lid',));slot('abd.powered',BOOL,'1','ABD附件电源','setpoint',components=('abd_carriage',),write=('abd.power','enabled'));slot('abd.stopped',BOOL,'1','ABD紧急停止锁存，须关再开复位',components=('abd_carriage',));slot('abd.cooldown_remaining_s',number('s',0,200),'s','液体舟再用前至少200s冷却，不能用模拟缩短厂建议',components=('abd_carriage',));slot('cell.type',enum(['sensitive','high_sensitive','high_concentration']),'1','宿主实际选择池型，三量程不合并',components=('coulometer_cell',));slot('cell.maximum_current_a',number('A',.0001,.01),'A','手册池对应发生极电流上限',components=('silver_anode',));slot('cell.electrolyte_volume_m3',number('m^3',2e-5,1.2e-4),'m^3','所选池标称电解液体积',components=('coulometer_cell',))
slot('temperature.target_k',number('K',278.15,1373.15),'K','炉温目标','setpoint',components=('combustion_furnace',),write=('temperature.set','temperature_k'));slot('temperature.actual_k',number('K',278.15,1373.15),'K','明确理想热反馈代理，非OEM温度标定',components=('combustion_furnace',));slot('cell.temperature_k',number('K',278.15,308.15),'K','宿主恒温池声明，不是实测冷却性能',components=('coulometer_cell',));slot('cell.prepared',BOOL,'1','宿主已装电解液和电极，未模拟完整终点调整',components=('coulometer_cell',));slot('instrument.ready',BOOL,'1','温度/气体/方向/电解池准备就绪')
slot('method.mode',enum(['AOX','EOX','POX','TX']),'1','检测已预处理输入对应的操作性参数','setpoint');slot('measurement.phase',TEXT,'1','本次流程状态');slot('measurement.elapsed_s',number('s',0),'s','本次积分时长');slot('measurement.progress',number('1',0,1),'1','已过积分时长比例');slot('detector.current_a',nullable(number('A')),'A','最近实际时间窗平均观测电流，含宿主噪声',components=('coulometer_cell',));slot('detector.charge_c',number('C'),'C','观测电流各窗口累计电荷',components=('coulometer_cell',));slot('detector.last_sample_time_s',nullable(number('s',0)),'s','最近电流窗口终点');slot('result.chlorine_equivalent_mass_kg',nullable(number('kg')),'kg','观测空白及实际来源标线反演Cl当量；不是各元素质量');slot('result.faraday_equivalent_mass_kg',nullable(number('kg')),'kg','匹配空白净电荷的理想Faraday当量，仅作未校准诊断，不是盲样报告值');slot('blank.valid',BOOL,'1','完整方法签名匹配空白');slot('calibration.ready',BOOL,'1','已选择相容标线');slot('storage.result_count',integer(0,MAX_RESULTS),'1','实例内256结果代理容量非OEM');slot('storage.curve_count',integer(0,MAX_CURVES),'1','实例内32曲线代理容量非OEM');slot('safety.warning',TEXT,'1','公开运行错误，不包含真值');slot('truth.ledger',LEDGER,'1','受权限过滤的物料/气体/当量/银/电荷/热仓','process_truth','evaluator',('combustion_furnace','coulometer_cell'))

cap('abd.power',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['abd_carriage'],'ABD独立电源；停止锁存需确实关再开',example={'enabled':True})
cap('abd.stop',obj({}),obj({'stopped':BOOL}),['abd_carriage'],'立即停止ABD并锁存，活动样品过程取消保留物料')
cap('sample_port.toggle',obj({}),obj({'opened':BOOL}),['abd_lid'],'PORT按钮切换进样口，采集中锁定')
CELLS={'sensitive':dict(min_kg=1e-9,max_kg=1e-7,current_a=.001,volume_m3=2e-5),'high_sensitive':dict(min_kg=1e-11,max_kg=1e-8,current_a=.0001,volume_m3=6.5e-5),'high_concentration':dict(min_kg=1e-8,max_kg=1e-6,current_a=.01,volume_m3=1.2e-4)}
cap('tube.configure',obj({'tube_type':enum(['open','multi_purpose'])}),obj({'tube_type':TEXT}),['combustion_tube'],'冷态宿主声明已换装对应燃烧管和管线；几何换件未验证','configuration','evaluator',{'tube_type':'open'})
slot('tube.type',enum(['open','multi_purpose']),'1','宿主已装燃烧管；换向本身不自动换管','setpoint',components=('combustion_tube',))
def _library():
 p=ROOT/TEMPLATE_REL
 return json.loads(p.read_text()) if p.exists() else {'compounds':{}}
def _source_amount(r,a):
 for prefix in ['chlorine_equivalent','chloride_equivalent']:
  v=r.get(prefix+'_mass_kg')
  if v is not None:return float(v)*a['reference_fraction']
  v=r.get(prefix+'_concentration_kg_m3')
  if v is not None:
   if a['volume_m3']<=0:raise ActionRejected('SOURCE_QUANTITY_BASIS')
   return float(v)*a['volume_m3']*a['reference_fraction']
  v=r.get(prefix+'_mass_fraction_kg_kg')
  if v is not None:return float(v)*a['mass_kg']*a['reference_fraction']
 if r.get('basis')=='chlorine_equivalent':
  if r.get('concentration_kg_m3') is not None:
   if a['volume_m3']<=0:raise ActionRejected('SOURCE_QUANTITY_BASIS')
   return float(r['concentration_kg_m3'])*a['volume_m3']*a['reference_fraction']
  if r.get('mass_fraction_kg_kg') is not None:return float(r['mass_fraction_kg_kg'])*a['mass_kg']*a['reference_fraction']
 raise ActionRejected('REFERENCE_QUERY_ONLY')
RECOVERY_KIND='published_compound_recovery';RECOVERY_FIELDS=('chlorine_equivalent_mass_kg','concentration_kg_m3')
def _recovery_check(r,mode):
 # Published compound recovery (2026-10-14 library expansion): delivered Cl-equivalent = basis value x source aliquot x
 # derived stoichiometric/halide Cl-equivalent factor x published recovery. Recomputed here so a record can only be
 # loaded in its own mode and only with an internally consistent, finite derivation.
 if mode not in (r.get('supported_modes') or []):raise ActionRejected('RECOVERY_MODE_MISMATCH')
 d=r.get('derivation')
 if not isinstance(d,dict) or d.get('target_field') not in RECOVERY_FIELDS:raise ActionRejected('REFERENCE_QUERY_ONLY')
 parts=[d.get(k) for k in ('basis_value','aliquot','cl_equivalent_factor','applied_recovery')]
 if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 for v in parts) or not 0<parts[3]<=2:raise ActionRejected('REFERENCE_QUERY_ONLY')
 q=math.prod(parts);stored=r.get(d['target_field'])
 if not isinstance(stored,(int,float)) or not math.isclose(q,stored,rel_tol=1e-9,abs_tol=1e-30) or any(r.get(k) is not None for k in ('chlorine_equivalent_mass_kg','concentration_kg_m3','mass_fraction_kg_kg') if k!=d['target_field']):raise ActionRejected('RECOVERY_DERIVATION_MISMATCH')
 return q
class JenaMultixRuntime:
 def __init__(self,instance_id='multix_01',library=None):
  self.instance_id=instance_id;self.library=copy.deepcopy(library) if library is not None else _library();self.templates=self.library.get('compounds',{});self.plant=None;self.method=copy.deepcopy(METHOD_EXAMPLE);self.sample=None;self.time_s=0.;self.revision=0;self.powered=False;self.gas=False;self.doors={k:0. for k in ['main_left','main_right','chlorine']};self.direction='vertical';self.angle=0.;self.injection=0.;self.port_open=False;self.port_angle=0.;self.abd_powered=False;self.abd_stopped=False;self._abd_off_seen=False;self.cooldown=0.;self.target=1223.15;self.temperature=298.15;self.tube_type='open';self.cell_type='sensitive';self.cell_prepared=False;self.phase='idle';self.elapsed_s=0.;self.warning='';self.windows=[];self.charge=0.;self._pending_charge=0.;self._pending_dt=0.;self._pending_saturated=False;self._pending_end=0.;self._last_point=None;self._run_method=None;self._run_key='';self._run_blank=None;self._run_curve=None;self._run_kind='';self._run_direction='vertical';self._result_cache=None;self._record_id='';self._record_counter=0;self._selected='';self._records={};self._curves={};self._blanks={};self._standard_refs={};self.operation_id='';self._op_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._operation_records={};self._rng=np.random.default_rng(0)
  self.volume_registered=self.volume_returned=self.volume_consumed=0.;self.registered_mass=self.returned_mass=self.residue=self.removed_residue=self.products_mass=self.oxygen_reacted=0.;self.equivalent_registered=self.equivalent_returned=self.released=self.transport=self.cell_pending=self.detected=self.uncaptured=self.drained=0.;self._species_registered={};self._species_returned={};self._species_released={};self.oxygen_remaining=self.argon_remaining=self.oxygen_initial=self.argon_initial=self.oxygen_exhaust=self.argon_exhaust=0.;self.silver_initial=self.silver_remaining=self.silver_precipitate=self.silver_removed=self.generated_charge=0.;self.electrical=self.thermal_lost=0.
 def register_principal(self,principal_id,visibility='agent'):
  if not isinstance(principal_id,str) or not principal_id or visibility not in ['agent','debug','evaluator']:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('PRINCIPAL_EXISTS')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return JenaMultixSession(self,principal_id)
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _cold(self):return self.temperature<=(self.plant or PLANT_EXAMPLE)['cold_threshold_k']
 def _reference(self,k):
  r=self.templates.get(k)
  if r is None:raise ActionRejected('UNKNOWN_REFERENCE')
  return r
 def _key(self):
  response={k:v for k,v in (self.plant or {}).items() if k not in ['seed','oxygen_supply_mol','argon_supply_mol','silver_initial_mol','rotation_speed_rad_s','feed_speed_m_s','heating_rate_k_s','cooling_rate_k_s','cold_threshold_k','thermal_capacity_j_k','heat_loss_w_k']}
  return hashlib.sha256(json.dumps(dict(method=self.method,direction=self.direction,cell=self.cell_type,input_form=self.sample['configuration']['input_form'] if self.sample else {'AOX':'loaded_carbon','EOX':'extract','POX':'purged_gas','TX':'solid'}[self.method['mode']],response=response),sort_keys=True).encode()).hexdigest()
 def _blank_valid(self):return self._key() in self._blanks
 def _calibration_ready(self):return self._selected in self._curves and self._curves[self._selected]['key']==self._key()
 def _needs_argon(self):return self.method['mode']!='AOX' or self.direction=='horizontal'
 def _ready(self):
  if self.tube_type!=('open' if self.direction=='vertical' and self.method['mode'] in ['AOX','POX'] else 'multi_purpose'):return False
  if not self.plant or not self.powered or not self.gas or not self.cell_prepared or any(self.doors.values()):return False
  if abs(self.temperature-self.target)>.1 or self.target<1223.15:return False
  if abs(self.angle-(0. if self.direction=='vertical' else math.pi/2))>1e-9:return False
  if self.oxygen_remaining<=0 or self._needs_argon() and self.argon_remaining<=0:return False
  if self.direction=='horizontal' and (not self.abd_powered or self.abd_stopped or self.port_open or self.port_angle>1e-8 or self.cooldown>0):return False
  return True
 def _ledger(self):
  unburned=self.sample['mass']*(1-self.sample['burned']) if self.sample else 0.;held=self.sample['equiv']*(1-self.sample['burned']) if self.sample else 0.
  eq=dict(registered_mol=self.equivalent_registered,unburned_mol=held,returned_mol=self.equivalent_returned,transport_mol=self.transport,cell_pending_mol=self.cell_pending,precipitated_mol=self.detected,unrecovered_mol=self.uncaptured,drained_mol=self.drained,residual_mol=self.equivalent_registered-held-self.equivalent_returned-self.transport-self.cell_pending-self.detected-self.uncaptured-self.drained,meaning='Operational halide equivalents, not an elemental Cl mass identification. Unknown Cl/Br/I proportions remain unknown.')
  species={k:dict(registered_mol=v,unburned_mol=self.sample['species'].get(k,0.)*(1-self.sample['burned']) if self.sample else 0.,returned_mol=self._species_returned.get(k,0.),released_or_residue_mol=self._species_released.get(k,0.)) for k,v in self._species_registered.items()}
  for d in species.values():d['residual_mol']=d['registered_mol']-d['unburned_mol']-d['returned_mol']-d['released_or_residue_mol']
  gas=dict(oxygen_initial_mol=self.oxygen_initial,oxygen_remaining_mol=self.oxygen_remaining,oxygen_exhaust_mol=self.oxygen_exhaust,oxygen_reacted_mol=self.oxygen_reacted,oxygen_residual_mol=self.oxygen_initial-self.oxygen_remaining-self.oxygen_exhaust-self.oxygen_reacted,argon_initial_mol=self.argon_initial,argon_remaining_mol=self.argon_remaining,argon_exhaust_mol=self.argon_exhaust,argon_residual_mol=self.argon_initial-self.argon_remaining-self.argon_exhaust)
  stored=(self.temperature-(self.plant or PLANT_EXAMPLE)['ambient_temperature_k'])*(self.plant or PLANT_EXAMPLE)['thermal_capacity_j_k']
  return dict(sample_registered_kg=self.registered_mass,sample_unburned_kg=unburned,sample_returned_kg=self.returned_mass,solid_residue_kg=self.residue,residue_removed_kg=self.removed_residue,reaction_products_kg=self.products_mass,oxygen_reacted_kg=self.oxygen_reacted*M_O2,mass_residual_kg=self.registered_mass+self.oxygen_reacted*M_O2-unburned-self.returned_mass-self.residue-self.removed_residue-self.products_mass,equivalent_ledger_json=json.dumps(eq,sort_keys=True),volume_ledger_json=json.dumps(dict(registered_m3=self.volume_registered,unburned_m3=self.sample['configuration']['volume_m3']*(1-self.sample['burned']) if self.sample else 0.,returned_m3=self.volume_returned,consumed_original_liquid_m3=self.volume_consumed,residual_m3=self.volume_registered-self.volume_returned-self.volume_consumed-(self.sample['configuration']['volume_m3']*(1-self.sample['burned']) if self.sample else 0.),meaning='Original liquid volume inventory only; not combustion gas volume or invented density.'),sort_keys=True),species_ledger_json=json.dumps(species,sort_keys=True),gas_ledger_json=json.dumps(gas,sort_keys=True),charge_generated_c=self.generated_charge,silver_initial_mol=self.silver_initial,silver_remaining_mol=self.silver_remaining,silver_precipitate_mol=self.silver_precipitate,silver_removed_precipitate_mol=self.silver_removed,silver_residual_mol=self.silver_initial-self.silver_remaining-self.silver_precipitate-self.silver_removed,electrical_energy_j=self.electrical,thermal_stored_j=stored,thermal_lost_j=self.thermal_lost,energy_residual_j=self.electrical-stored-self.thermal_lost)
 def _result(self):
  if self._result_cache is not None:return copy.deepcopy(self._result_cache)
  return dict(phase=self.phase,complete=False,record_id=self._record_id,mode=(self._run_method or self.method)['mode'],direction=self._run_direction,windows=copy.deepcopy(self.windows),observed_charge_c=self.charge,blank_corrected_charge_c=None,chlorine_equivalent_mass_kg=None,faraday_equivalent_mass_kg=None,uncertainty_kg=None,quality=['no completed calibrated observation'],acquisition_signature=self._run_key,acquisition_method_json=json.dumps(self._run_method,sort_keys=True),evidence='synthetic electrical observations from declared combustion/transport/cell response; source composition/assigned values are not measured raw signals',template_source=TEMPLATE_REL)
 def read(self,visibility='agent'):
  if visibility not in ['agent','debug','evaluator']:raise ActionRejected('ACCESS_DENIED')
  result=self._result();v={'plant.configured':self.plant is not None,'power.enabled':self.powered,'gas.enabled':self.gas,'sample.loaded':self.sample is not None,**{'cover.'+k+'_angle_rad':v for k,v in self.doors.items()},'furnace.direction':self.direction,'furnace.angle_rad':self.angle,'injection.position_m':self.injection,'abd_cover.angle_rad':self.port_angle,'abd.powered':self.abd_powered,'abd.stopped':self.abd_stopped,'abd.cooldown_remaining_s':self.cooldown,'temperature.target_k':self.target,'temperature.actual_k':self.temperature,'cell.temperature_k':(self.plant or PLANT_EXAMPLE)['cell_temperature_k'],'cell.type':self.cell_type,'tube.type':self.tube_type,'cell.maximum_current_a':CELLS[self.cell_type]['current_a'],'cell.electrolyte_volume_m3':CELLS[self.cell_type]['volume_m3'],'cell.prepared':self.cell_prepared,'instrument.ready':self._ready(),'method.mode':self.method['mode'],'measurement.phase':self.phase,'measurement.elapsed_s':self.elapsed_s,'measurement.progress':min(1.,self.elapsed_s/(self._run_method or self.method)['integration_s']),'detector.current_a':self._last_point['current_a'] if self._last_point else None,'detector.charge_c':self.charge,'detector.last_sample_time_s':self._last_point['end_s'] if self._last_point else None,'result.chlorine_equivalent_mass_kg':result['chlorine_equivalent_mass_kg'],'result.faraday_equivalent_mass_kg':result['faraday_equivalent_mass_kg'],'blank.valid':self._blank_valid(),'calibration.ready':self._calibration_ready(),'storage.result_count':len(self._records),'storage.curve_count':len(self._curves),'safety.warning':self.warning}
  if visibility!='agent':v['truth.ledger']=self._ledger()
  return copy.deepcopy(v)
 def describe(self):return self.session().describe(asset_type_id=ASSET_TYPE_ID)
 def invoke(self,capability,**arguments):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if capability in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[capability][0],arguments)
  result=self._invoke(capability,arguments);validate(CAPS[capability][1],result)
  if CAPS[capability][4]!='query':self.revision+=1
  return copy.deepcopy(result)
 def _invoke(self,k,a):
  if k=='plant.configure':
   if self.time_s or self.plant is not None or self.sample:raise ActionRejected('INITIALIZATION_ONLY')
   self.plant=copy.deepcopy(a);self.temperature=a['ambient_temperature_k'];self.oxygen_initial=self.oxygen_remaining=a['oxygen_supply_mol'];self.argon_initial=self.argon_remaining=a['argon_supply_mol'];self.silver_initial=self.silver_remaining=a['silver_initial_mol'];self._rng=np.random.default_rng(a['seed']);return {'configured':True}
  if k=='sample.load':
   self._idle()
   if self.sample is not None:raise ActionRejected('SAMPLE_PRESENT')
   if self.plant is None:raise ActionRejected('NOT_CONFIGURED')
   if not a['accept_prepared_input'] or not a['accept_source_condition_mismatch']:raise ActionRejected('PREPARATION_ACK_REQUIRED')
   forms={'AOX':['loaded_carbon'],'EOX':['extract'],'POX':['purged_gas'],'TX':['solid','liquid']}
   if a['input_form'] not in forms[self.method['mode']]:raise ActionRejected('INPUT_FORM_MISMATCH')
   if a['input_form'] not in ['extract','liquid'] and a['volume_m3']!=0:raise ActionRejected('LIQUID_VOLUME_ONLY')
   if a['input_form'] in ['extract','liquid'] and a['volume_m3']<=0:raise ActionRejected('INVALID_VOLUME')
   if self.direction=='horizontal' and (not self.port_open or abs(self.port_angle-1.4)>1e-8 or self.injection>1e-9):raise ActionRejected('SAMPLE_PORT_CLOSED')
   if self.direction=='vertical' and any(self.doors.values()):raise ActionRejected('MAINTENANCE_DOOR_OPEN')
   if self.transport+self.cell_pending>1e-15:raise ActionRejected('RESIDUAL_SAMPLE')
   species={};mass=0.;r=None
   if a['halogen_moles']:
    if a['template_id'] or a['kind']=='standard':raise ActionRejected('REFERENCE_REQUIRED_FOR_STANDARD')
    if len({v['element'] for v in a['halogen_moles']})!=len(a['halogen_moles']):raise ActionRejected('INVALID_ARGUMENT')
    species={v['element']:v['amount_mol'] for v in a['halogen_moles']};mass=sum(v for k,v in species.items() if k!='F')*M_CL
    if sum(species.get(e,0.)*M for e,M in [('Cl',.03545),('Br',.079904),('I',.12690447),('F',.0189984)])>a['mass_kg']:raise ActionRejected('SPECIES_EXCEED_SAMPLE_MASS')
   elif a['kind']!='blank':
    r=self._reference(a['template_id'])
    if r.get('record_kind')==RECOVERY_KIND:_recovery_check(r,self.method['mode'])
    if r.get('input_form') is not None and r['input_form']!=a['input_form'] or r.get('supported_modes') is not None and self.method['mode'] not in r['supported_modes']:raise ActionRejected('SOURCE_STAGE_MISMATCH')
    mass=_source_amount(r,a)
    if r.get('physical_input_eligible') is False or r.get('physical_loading_eligible') is False:raise ActionRejected('REFERENCE_QUERY_ONLY')
    if a['kind']=='standard' and not r.get('calibration_eligible',False):raise ActionRejected('REFERENCE_NOT_CALIBRATION_ELIGIBLE')
   if mass>a['mass_kg']:raise ActionRejected('EQUIVALENT_EXCEEDS_SAMPLE_MASS')
   if not math.isfinite(mass) or mass<0:raise ActionRejected('REFERENCE_QUERY_ONLY')
   if a['kind']=='blank' and (a['template_id'] or species):raise ActionRejected('BLANK_INPUT_INVALID')
   self.sample=dict(configuration=copy.deepcopy(a),mass=a['mass_kg'],equiv=mass/M_CL,species=species,burned=0.,kind=a['kind'],reference_id=a['template_id']);self.registered_mass+=a['mass_kg'];self.volume_registered+=a['volume_m3'];self.equivalent_registered+=mass/M_CL
   for e,n in species.items():self._species_registered[e]=self._species_registered.get(e,0.)+n
   return {'loaded':True}
  if k=='sample.unload':
   self._idle()
   if self.sample is None:raise ActionRejected('NO_SAMPLE')
   if self.direction=='horizontal' and (not self.port_open or self.injection>1e-9):raise ActionRejected('SAMPLE_PORT_CLOSED')
   self.volume_returned+=self.sample['configuration']['volume_m3']*(1-self.sample['burned']);mass=self.sample['mass']*(1-self.sample['burned']);eq=self.sample['equiv']*(1-self.sample['burned']);self.returned_mass+=mass;self.equivalent_returned+=eq
   for e,n in self.sample['species'].items():self._species_returned[e]=self._species_returned.get(e,0.)+n*(1-self.sample['burned'])
   residue=self.residue;self.removed_residue+=residue;self.residue=0.;self.sample=None;return dict(unburned_mass_kg=mass,residue_mass_kg=residue)
  if k=='cover.set':
   self._idle()
   if self.powered or self.gas or not self._cold():raise ActionRejected('COLD_ISOLATED_MAINTENANCE_REQUIRED')
   self.doors[a['door']]=1.6 if a['opened'] else 0.;return {'opened':a['opened']}
  if k=='direction.set':
   self._idle()
   if self.plant is None:raise ActionRejected('NOT_CONFIGURED')
   if self.powered or self.gas or not self._cold() or self.sample or self.injection>1e-9:raise ActionRejected('COLD_ISOLATED_MAINTENANCE_REQUIRED')
   if not self.doors['main_left'] or not self.doors['main_right']:raise ActionRejected('MAINTENANCE_DOOR_CLOSED')
   if a['direction']=='horizontal' and not self.plant['optional_boat_drive_installed']:raise ActionRejected('OPTION_NOT_INSTALLED')
   self.direction=a['direction'];return {'direction':self.direction}
  if k=='power.set':
   if a['enabled'] and (self.plant is None or any(self.doors.values())):raise ActionRejected('MAINTENANCE_DOOR_OPEN')
   if not a['enabled'] and self.phase=='running':self._finish('cancelled','power off')
   self.powered=a['enabled'];return {'enabled':self.powered}
  if k=='gas.set':
   if self.plant is None:raise ActionRejected('NOT_CONFIGURED')
   if a['enabled'] and self.oxygen_remaining<=0:raise ActionRejected('OXYGEN_DEPLETED')
   if a['enabled'] and self._needs_argon() and self.argon_remaining<=0:raise ActionRejected('ARGON_DEPLETED')
   if not a['enabled'] and self.phase=='running':self._finish('cancelled','carrier gas off')
   self.gas=a['enabled'];return {'enabled':self.gas}
  if k=='method.configure':
   self._idle()
   if self.plant is None:raise ActionRejected('NOT_CONFIGURED')
   if a['integration_s']<a['feeding_s']+a['combustion_s'] or a['window_s']>a['integration_s'] or math.ceil(a['integration_s']/a['window_s'])>MAX_WINDOWS:raise ActionRejected('INVALID_TIME_WINDOW')
   if a['mode']=='POX' and not self.plant['optional_pox_installed']:raise ActionRejected('OPTION_NOT_INSTALLED')
   if a['mode']=='POX' and self.direction!='vertical' or a['mode']=='TX' and self.direction!='horizontal':raise ActionRejected('DIRECTION_MODE_MISMATCH')
   if a['mode'] in ['AOX','POX'] and self.direction=='vertical' and self.cell_type=='high_sensitive':raise ActionRejected('CELL_TUBE_MISMATCH')
   self.method=copy.deepcopy(a);self.target=a['temperature_k'];return {'configured':True}
  if k=='temperature.set':
   self._idle();self.target=a['temperature_k'];self.method['temperature_k']=a['temperature_k'];return {'target_k':self.target}
  if k=='tube.configure':
   self._idle()
   if self.powered or self.gas or not self._cold() or not self.doors['main_left'] or not self.doors['main_right']:raise ActionRejected('COLD_ISOLATED_MAINTENANCE_REQUIRED')
   self.tube_type=a['tube_type'];return {'tube_type':self.tube_type}
  if k=='cell.prepare':
   self._idle()
   if self.plant is None:raise ActionRejected('NOT_CONFIGURED')
   if self.powered or self.gas or not self.doors['chlorine']:raise ActionRejected('CELL_MAINTENANCE_REQUIRED')
   if self.transport+self.cell_pending>1e-15:raise ActionRejected('RESIDUAL_SAMPLE')
   if a['cell_type']=='high_sensitive' and self.direction=='vertical' and self.method['mode'] in ['AOX','POX']:raise ActionRejected('CELL_TUBE_MISMATCH')
   self.cell_type=a['cell_type'];self.cell_prepared=True;return {'prepared':True}
  if k=='abd.power':
   if self.plant is None or not self.plant['optional_boat_drive_installed']:raise ActionRejected('OPTION_NOT_INSTALLED')
   if not a['enabled']:
    if self.phase=='running' and self.direction=='horizontal':self._finish('cancelled','ABD power off')
    self._abd_off_seen=True
   elif self._abd_off_seen:self.abd_stopped=False;self._abd_off_seen=False
   self.abd_powered=a['enabled'];return {'enabled':self.abd_powered}
  if k=='abd.stop':
   if self.plant is None or not self.plant['optional_boat_drive_installed']:raise ActionRejected('OPTION_NOT_INSTALLED')
   if self.phase=='running' and self.direction=='horizontal':self._finish('cancelled','ABD emergency stop')
   self.abd_stopped=True;self._abd_off_seen=False;return {'stopped':True}
  if k=='sample_port.toggle':
   self._idle()
   if self.plant is None or not self.plant['optional_boat_drive_installed']:raise ActionRejected('OPTION_NOT_INSTALLED')
   if not self.abd_powered or self.abd_stopped or self.injection>1e-9 or self.cooldown>0:raise ActionRejected('ABD_NOT_READY')
   self.port_open=not self.port_open;return {'opened':self.port_open}
  if k=='measurement.start':return self._start()
  if k=='measurement.stop':
   if self.phase=='running':self._finish('cancelled','software stop')
   return {'phase':self.phase}
  if k=='process.measure':
   v=self.read();return dict(phase=self.phase,elapsed_s=self.elapsed_s,progress=v['measurement.progress'],current_a=v['detector.current_a'],charge_c=self.charge,chlorine_equivalent_mass_kg=v['result.chlorine_equivalent_mass_kg'])
  if k=='result.read':return self._result()
  if k=='signal.read':return dict(windows=copy.deepcopy(self.windows),observed_charge_c=self.charge,sample_window_s=[self.windows[0]['start_s'],self.windows[-1]['end_s']] if self.windows else None,evidence='synthetic current observations; preserved actual integration windows')
  if k=='result.export':
   if a['format']=='json':return dict(format='json',content=json.dumps(self._result(),sort_keys=True,allow_nan=False))
   f=io.StringIO();w=csv.writer(f);w.writerow(['start_s','end_s','current_a','observed_charge_c','saturated']);w.writerows([[r[k] for k in ['start_s','end_s','current_a','observed_charge_c','saturated']] for r in self.windows]);return dict(format='csv',content=f.getvalue())
  if k=='blank.read':return dict(valid=self._blank_valid(),blank_json=json.dumps(self._blanks.get(self._key()),sort_keys=True))
  if k=='calibration.fit':return self._fit(a)
  if k in ['calibration.select','calibration.read']:
   if a['curve_id'] not in self._curves:raise ActionRejected('UNKNOWN_CURVE')
   if k=='calibration.read':return {'curve_json':json.dumps(self._curves[a['curve_id']],sort_keys=True)}
   self._idle()
   if self._curves[a['curve_id']]['key']!=self._key():raise ActionRejected('CALIBRATION_SIGNATURE_MISMATCH')
   self._selected=a['curve_id'];return {'selected':self._selected}
  if k=='records.list':return dict(record_ids=list(self._records),curve_ids=list(self._curves))
  if k in ['records.read','records.remove']:
   if a['record_id'] not in self._records:raise ActionRejected('UNKNOWN_RECORD')
   if k=='records.read':return self._records[a['record_id']]
   self._idle();del self._records[a['record_id']];return {'removed':True}
  if k=='template.read':
   for collection in ['compounds','references','species']:
    if a['substance_id'] in self.library.get(collection,{}):return {'template_json':json.dumps(self.library[collection][a['substance_id']],sort_keys=True)}
   raise ActionRejected('UNKNOWN_REFERENCE')
  if k=='product.read':return self._ledger()
  if k=='product.drain':
   self._idle();amount=self.transport+self.cell_pending;self.drained+=amount;self.transport=self.cell_pending=0.;self.silver_removed+=self.silver_precipitate;self.silver_precipitate=0.;return {'drained_equivalents_mol':amount}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _start(self):
  self._idle()
  if not self._ready():raise ActionRejected('INSTRUMENT_NOT_READY')
  if self.sample is None:raise ActionRejected('NO_SAMPLE')
  if self.direction=='horizontal' and self.method['feeding_s']<.285/self.plant['feed_speed_m_s']:raise ActionRejected('FEED_TIME_TOO_SHORT')
  if self.sample['burned']>0:raise ActionRejected('SAMPLE_ALREADY_CONSUMED')
  if self.sample['configuration']['input_form'] not in {'AOX':['loaded_carbon'],'EOX':['extract'],'POX':['purged_gas'],'TX':['solid','liquid']}[self.method['mode']]:raise ActionRejected('INPUT_FORM_MISMATCH')
  if len(self._records)>=MAX_RESULTS:raise ActionRejected('RESULT_CAPACITY')
  if self.sample['kind']!='blank' and not self._blank_valid():raise ActionRejected('BLANK_REQUIRED')
  if self.silver_remaining<=0:raise ActionRejected('SILVER_DEPLETED')
  if self.transport+self.cell_pending>1e-15:raise ActionRejected('RESIDUAL_SAMPLE')
  self._run_method=copy.deepcopy(self.method);self._run_key=self._key();self._run_direction=self.direction;self._run_blank=copy.deepcopy(self._blanks.get(self._run_key));self._run_curve=copy.deepcopy(self._curves[self._selected]) if self._calibration_ready() else None;self._run_kind=self.sample['kind'];self._op_count+=1;self.operation_id=f'{self.instance_id}:op:{self._op_count}';self.phase='running';self.elapsed_s=0.;self.windows=[];self.charge=0.;self._pending_charge=self._pending_dt=0.;self._last_point=None;self._pending_saturated=False;self._result_cache=None;self._record_id='';self.warning='';return {'operation_id':self.operation_id}
 def _window(self):
  if self._pending_dt<=1e-14:return
  dt=self._pending_dt;noise=float(self._rng.normal(0,self.plant['current_noise_density_a_sqrt_s']*math.sqrt(dt))) if self.plant['current_noise_density_a_sqrt_s'] else 0.;q=self._pending_charge+noise;end=self._pending_end;start=end-dt
  p=dict(start_s=start,end_s=end,current_a=q/dt,observed_charge_c=q,saturated=self._pending_saturated);self.windows.append(p);self._last_point=copy.deepcopy(p);self.charge+=q;self._pending_dt=self._pending_charge=0.;self._pending_saturated=False
 def _finish(self,status,reason=''):
  self._window();self.phase=status;self.warning=reason;result=self._result();result['phase']=status;result['complete']=status=='succeeded';quality=[];net=self.charge-self._run_blank['observed_charge_c'] if self._run_blank and status=='succeeded' else None;mass=None;sd=None;far=net/FARADAY*M_CL if net is not None else None
  endpoint_unresolved=False
  if status=='succeeded' and self._run_blank and self.windows:
   last=self.windows[-1];blank_rate=self._run_blank['observed_charge_c']/self._run_method['integration_s'];threshold=max(.001*CELLS[self.cell_type]['current_a']*self.plant['detector_gain'],5*self.plant['current_noise_density_a_sqrt_s']/math.sqrt(last['end_s']-last['start_s']));endpoint_unresolved=abs(last['current_a']-blank_rate)>threshold
  if status!='succeeded':quality.append('incomplete operation; irreversible combustion and partial observed charge retained')
  elif endpoint_unresolved:quality.append('endpoint unresolved by public final-window current; finite integration is complete but titration and quantitative mass are incomplete')
  elif any(w['saturated'] for w in self.windows):quality.append('detector saturation; calibrated quantity undefined')
  elif self._run_curve and net is not None:
   c=self._run_curve;candidate=(net-c['intercept_c'])/c['slope_c_kg']
   if candidate<0:quality.append('negative observed blank-subtracted result; not clipped to zero')
   elif candidate<c['min_kg'] or candidate>c['max_kg']:quality.append('outside observed calibration interpolation range')
   elif not CELLS[self.cell_type]['min_kg']<=candidate<=CELLS[self.cell_type]['max_kg']:quality.append('outside documented selected-cell range')
   else:mass=candidate;sd=c['residual_sd_c']/c['slope_c_kg']
  else:quality.append('no selected matching observed calibration; Faraday value is diagnostic only')
  self._record_counter+=1;self._record_id=f'record_{self._record_counter}';result.update(record_id=self._record_id,windows=copy.deepcopy(self.windows),observed_charge_c=self.charge,blank_corrected_charge_c=net,chlorine_equivalent_mass_kg=mass,faraday_equivalent_mass_kg=far,uncertainty_kg=sd,quality=quality or ['estimated conditional Cl-equivalent; no elemental speciation'])
  self._result_cache=copy.deepcopy(result);self._records[self._record_id]=copy.deepcopy(result)
  if status=='succeeded' and self._run_kind=='blank':self._blanks[self._run_key]=copy.deepcopy(result)
  if status=='succeeded' and self._run_kind=='standard':self._standard_refs[self._record_id]=dict(reference_id=self.sample['reference_id'],reference_mass_kg=self.sample['equiv']*M_CL,reference_fraction=self.sample['configuration']['reference_fraction'])
  if self._run_direction=='horizontal' and self.sample and self.sample['configuration']['input_form'] in ['liquid','extract'] and self.sample['burned']>0:self.cooldown=200.
  self._operation_records[self.operation_id]=self._operation_snapshot()
 def _operation_snapshot(self):return dict(operation_id=self.operation_id,status=self.phase,result=self._result(),clock={'domain':'simulation','time_s':self.time_s},state_revision=self.revision)
 def _fit(self,a):
  self._idle()
  if not a['curve_id'] or a['curve_id'] in self._curves:raise ActionRejected('CURVE_EXISTS')
  if len(self._curves)>=MAX_CURVES:raise ActionRejected('CURVE_CAPACITY')
  if len(set(a['record_ids']))!=len(a['record_ids']):raise ActionRejected('DUPLICATE_STANDARD')
  rows=[]
  for rid in a['record_ids']:
   if rid not in self._records or rid not in self._standard_refs:raise ActionRejected('ACTUAL_STANDARD_REQUIRED')
   r=self._records[rid];ref=self._standard_refs[rid]
   if r['acquisition_signature']!=self._key():raise ActionRejected('CALIBRATION_SIGNATURE_MISMATCH')
   if r['blank_corrected_charge_c'] is None or any(w['saturated'] for w in r['windows']) or any('endpoint unresolved' in x for x in r['quality']):raise ActionRejected('INVALID_STANDARD_OBSERVATION')
   rows.append(ref|dict(record_id=rid,observed_charge_c=r['blank_corrected_charge_c']))
  x=np.asarray([r['reference_mass_kg'] for r in rows]);y=np.asarray([r['observed_charge_c'] for r in rows]);scale=float(np.max(x))
  if scale<=0 or len(set(x))<3 or np.ptp(x)<scale*1e-9:raise ActionRejected('CALIBRATION_RANGE')
  coef=np.linalg.lstsq(np.column_stack([x/scale,np.ones(len(x))]),y,rcond=None)[0];slope=float(coef[0]/scale);intercept=float(coef[1]);res=y-(slope*x+intercept);ss=float(np.sum((y-y.mean())**2));r2=1-float(res@res)/ss if ss>1e-30 else -1.
  if not all(math.isfinite(v) for v in [slope,intercept,r2]) or slope<=0 or r2<.98:raise ActionRejected('CALIBRATION_FIT_INVALID')
  c=dict(key=self._key(),observations=rows,slope_c_kg=slope,intercept_c=intercept,r_squared=r2,residual_sd_c=float(np.sqrt(res@res/max(1,len(x)-2))),min_kg=float(x.min()),max_kg=float(x.max()),meaning='Actual saved synthetic charge observations against public source Cl-equivalent references; no real apparatus accuracy claim');self._curves[a['curve_id']]=c;return dict(curve_id=a['curve_id'],r_squared=r2,slope_c_kg=slope)
 @staticmethod
 def _approach(x,target,speed,dt):return x+max(-speed*dt,min(speed*dt,target-x))
 def _physical(self,dt):
  p=self.plant
  if not p:return
  self.angle=self._approach(self.angle,0. if self.direction=='vertical' else math.pi/2,p['rotation_speed_rad_s'],dt)
  self.port_angle=self._approach(self.port_angle,1.4 if self.port_open else 0.,p['rotation_speed_rad_s'],dt) if self.abd_powered and not self.abd_stopped else self.port_angle
  if self.injection<=1e-9:self.cooldown=max(0.,self.cooldown-dt)
  old=self.temperature;target=self.target if self.powered else p['ambient_temperature_k'];self.temperature=self._approach(old,target,p['heating_rate_k_s'] if target>old else p['cooling_rate_k_s'],dt);dE=(self.temperature-old)*p['thermal_capacity_j_k'];loss=p['heat_loss_w_k']*max(0.,(self.temperature+old)/2-p['ambient_temperature_k'])*dt
  if dE>=0:self.electrical+=dE+loss;self.thermal_lost+=loss
  else:self.thermal_lost-=dE
  if self.direction=='horizontal' and self.abd_powered and not self.abd_stopped:
   goal=.285 if self.phase=='running' and self.elapsed_s<self._run_method['feeding_s']+self._run_method['combustion_s'] else 0.;self.injection=self._approach(self.injection,goal,p['feed_speed_m_s'],dt)
  if self.gas:
   O=min(self.oxygen_remaining,p['oxygen_flow_mol_s']*dt);A=min(self.argon_remaining,p['argon_flow_mol_s']*dt) if self._needs_argon() else 0.;self.oxygen_remaining-=O;self.argon_remaining-=A;self.oxygen_exhaust+=O;self.argon_exhaust+=A
   if (O<p['oxygen_flow_mol_s']*dt-1e-15 or self._needs_argon() and A<p['argon_flow_mol_s']*dt-1e-15) and self.phase=='running':self._finish('failed','carrier supply depleted')
  if self.phase!='running':return
  if self.direction=='horizontal' and self.abd_stopped:return
  m=self._run_method;t1=self.elapsed_s+dt;f0=self.sample['burned'];fraction=max(0.,min(1.,(t1-m['feeding_s'])/m['combustion_s']));burned=max(0.,fraction-f0);dm=self.sample['mass']*burned;reaction=dm*p['oxygen_demand_mol_kg']
  if reaction>O+1e-15:self._finish('failed','insufficient carrier oxygen for declared matrix demand');return
  self.volume_consumed+=self.sample['configuration']['volume_m3']*burned;self.sample['burned']=fraction;self.oxygen_exhaust-=reaction;self.oxygen_reacted+=reaction;self.residue+=dm*p['solid_residue_fraction'];self.products_mass+=dm*(1-p['solid_residue_fraction'])+reaction*M_O2
  released=self.sample['equiv']*burned;self.released+=released;self.transport+=released*p['combustion_recovery'];self.uncaptured+=released*(1-p['combustion_recovery'])
  for e,n in self.sample['species'].items():self._species_released[e]=self._species_released.get(e,0.)+n*burned
  transported=self.transport*(-math.expm1(-dt/p['transport_tau_s']));self.transport-=transported;self.cell_pending+=transported
  want=self.cell_pending*(-math.expm1(-dt/p['cell_tau_s']));captured=min(want,self.silver_remaining,CELLS[self.cell_type]['current_a']*dt/FARADAY);self.cell_pending-=captured;self.silver_remaining-=captured;self.silver_precipitate+=captured;self.detected+=captured;Q=captured*FARADAY;self.generated_charge+=Q
  observed=p['detector_gain']*Q+p['baseline_current_a']*dt;saturated=abs(observed/dt)>p['maximum_current_a'];observed=max(-p['maximum_current_a']*dt,min(p['maximum_current_a']*dt,observed));self._pending_charge+=observed;self._pending_dt+=dt;self._pending_saturated|=saturated;self._pending_end=self.time_s;self.elapsed_s=t1
  if self._pending_dt>=m['window_s']-1e-10:self._window()
  if self.silver_remaining<=1e-18 and self.cell_pending>1e-15:self._finish('failed','silver anode exhausted');return
  if self.elapsed_s>=m['integration_s']-1e-9:self.elapsed_s=m['integration_s'];self._finish('succeeded')
 def step(self,dt_s):
  validate(number('s',1e-9,3600),dt_s);remaining=float(dt_s)
  while remaining>1e-12:
   dt=min(.01,remaining)
   if self.phase=='running':dt=min(dt,self._run_method['integration_s']-self.elapsed_s,self._run_method['window_s']-self._pending_dt)
   if dt<1e-12:dt=min(.01,remaining)
   self.time_s+=dt;self._physical(dt);remaining-=dt
  self.revision+=1;return self.read('evaluator')

class JenaMultixSession:
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
        m=export_jena_multix_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        def timing(key):
            if key=='detector.current_a' and h._last_point:return h._last_point['end_s'],[h._last_point['start_s'],h._last_point['end_s']]
            if key.startswith('result.') or key=='detector.charge_c':
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
'Source microcoulometry detects Cl/Br/I collectively as chloride equivalents, HF is not determined. Charge to chloride-equivalent conversion uses F and representative molar mass Cl; no elemental discrimination or molecular identification. Unknown source halogen proportions stay unknown.',
'2025 technical range10ng–1000ug combines different cells: sensitive1–100ug/1mA/20mL, high_sensitive.01–10ug/100uA/65mL, high_concentration10–1000ug/10mA/120mL. Generator current is bounded by the selected documented cell. Detection limit is not noise sigma; >5ug direct-cell RSD<2% is not a whole-process calibration.',
'AOX receives prepared loaded carbon; EOX a prepared extract; POX only a declared installed external purge module delivering prepared gas, without simulating extraction or purge recovery. TX uses horizontal multipurpose tube and declared ABD. TOC NDIR, ICprep halogen speciation, AOX adsorption/extraction and full autosampling are unsupported.',
'Temperature, finite feed/tilt, carrier flows, bulk combustion recovery, transport/cell kinetics, charge response/noise and matrix oxygen demand are explicit host assumptions. Default fast scenario does not reproduce manufacturer480–600s analyses. Source concentration/assigned mass drives synthetic physics; blind result is derived from actual observed charge and blank/reference measurements, never copied from hidden input.',
'End-point QC uses actual final-window current versus matched blank mean with explicit engineering threshold: max(0.001*selected-cell generator limit*host gain,5*host noise standard error). It is a limited observable tail check, not OEM electrode endpoint software or proof of zero undetected residue.',
'Same amount of monovalent halide precipitates one mole silver per mole equivalent, Q=F*nAg. Measured current is synthetic gain/baseline/noise applied to physical generator charge; it need not equal physical charge or certify current metrology. Unmeasured carrier/matrix composition is not silently assigned O, C or water.',
'Gross consumed sample plus declared reacted oxygen enters explicitly uncharacterized reaction products and residue; independent halogen-equivalent and optional supplied Cl/Br/I/F mole ledgers track remaining/released/removed material. No detailed CO2/H2O/sulfur chemistry, acid-drying chemistry, actual dose, acid volume or electrolyte activity model.',
'Cold maintenance requires power off/gas off/cooling per manual; exact numerical cold threshold is engineering. Three doors are independently host repositioned. Tilt alone does not replace tubes or gas/acid plumbing: tube.configure requires explicit cold installation. Default sensitive-cell geometry is a proxy; changing cell configuration is not physical glassware replacement or validated CAD.',
'ABD optional installed hardware has finite carriage and flap proxies, PORT locked during measurement, emergency STOP latched until ABD off/on; no flame sensor option or automatic optimization. Horizontal liquid/EOX boats have the documented minimum200s cooldown after combustion, counted only once the boat is back in the cooled sluice; fixture does not shorten it. Step is sole clock. Robot, OEM electronics and real force/temperature response remain not_run.',
'Cancellation preserves all generated/observed partial charge and irreversible burned material, plus unburned sample/gas/cell residual. sample.unload returns only unburned material and explicitly separated residue; product.drain disposes remaining equivalents without regenerating silver. No implicit sample restoration.',
'Public calibration references must be actual saved complete standard observations in this instance, with exact acquisition signature and three distinct source-reference doses. Linear calibration is conditional and interpolation-only; negative and uncalibrated values remain null with diagnostics, not forced zero. Reference aliquot fractions require explicit preparation assumption.',
'Process-local256 result/32curve/10000window capacity is engineering, not documented OEM persistence. Owner-bound asynchronous terminal snapshots remain queryable after new measurement and result deletion. Agent/evaluator return filtering is not OS/container/filesystem isolation.',
'Body nominal.810W*.550D*.460H m/about30kg plus installedABD.520W*.500D*.210H m10kg are distinct documented items; horizontal system spaceabout1.4m wide. Real local CAD, masses, gas connections and chemistry/hardware/robot validation remain separate.',
'Published compound-recovery templates (EPA SW-846 9020B/9021/9023, Ding et al. 2022 Se Pu, ES&T 2023 SI, Thermo AN-72333, multi X SP_0041) load as prepared inputs: delivered Cl-equivalent = source basis x source aliquot x derived stoichiometric/halide Cl-equivalent factor x published recovery, recomputed and mode-checked at load (RECOVERY_MODE_MISMATCH, RECOVERY_DERIVATION_MISMATCH). Most recoveries were measured on other TOX/AOX/CIC instruments; the host combustion_recovery still applies on top, so outputs are not replays of the published recoveries.'
]
SAMPLE['properties']['volume_m3']['description']='实际液体原始体积，仅extract/liquid使用；其他输入必须0，不是POX气量或AOX原水体积。'
def export_jena_multix_contract_metadata():
 caps={};slots={};run=['measurement.phase','measurement.elapsed_s','measurement.progress'];obs=['detector.current_a','detector.charge_c','detector.last_sample_time_s'];res=['result.chlorine_equivalent_mass_kg','result.faraday_equivalent_mass_kg'];ready=['instrument.ready','blank.valid','calibration.ready'];end=['measurement.phase','safety.warning','storage.result_count',*obs,*res,'truth.ledger'];doors=['cover.'+k+'_angle_rad' for k in ['main_left','main_right','chlorine']]
 immediate={'plant.configure':['plant.configured','temperature.actual_k','cell.temperature_k','truth.ledger'],'sample.load':['sample.loaded','truth.ledger','blank.valid','calibration.ready'],'sample.unload':['sample.loaded','truth.ledger','blank.valid','calibration.ready'],'cover.set':doors+['instrument.ready'],'power.set':['power.enabled',*end,'instrument.ready'],'gas.set':['gas.enabled',*end,'instrument.ready'],'direction.set':['furnace.direction',*ready],'tube.configure':['tube.type','instrument.ready'],'method.configure':['method.mode','temperature.target_k',*ready],'temperature.set':['temperature.target_k',*ready],'cell.prepare':['cell.type','cell.maximum_current_a','cell.electrolyte_volume_m3','cell.prepared',*ready],'abd.power':['abd.powered','abd.stopped',*end,'instrument.ready'],'abd.stop':['abd.stopped',*end,'instrument.ready'],'sample_port.toggle':['instrument.ready'],'measurement.start':[*run,*obs,*res,'safety.warning'],'measurement.stop':end+['abd.cooldown_remaining_s'],'calibration.fit':['storage.curve_count'],'calibration.select':['calibration.ready'],'records.remove':['storage.result_count'],'product.drain':['truth.ledger']}
 onstep={'direction.set':['furnace.angle_rad','instrument.ready'],'power.set':['temperature.actual_k','truth.ledger','instrument.ready'],'temperature.set':['temperature.actual_k','truth.ledger','instrument.ready'],'method.configure':['temperature.actual_k','truth.ledger','instrument.ready'],'gas.set':['truth.ledger','instrument.ready','safety.warning'],'sample_port.toggle':['abd_cover.angle_rad','instrument.ready'],'abd.power':['injection.position_m','abd_cover.angle_rad','instrument.ready'],'measurement.start':[*run,*obs,*res,'injection.position_m','truth.ledger','blank.valid','storage.result_count','abd.cooldown_remaining_s','safety.warning'],'measurement.stop':['injection.position_m','abd.cooldown_remaining_s','instrument.ready']}
 observes={'plant.configure':['plant.configured'],'sample.load':['sample.loaded'],'cover.set':doors,'power.set':['power.enabled'],'gas.set':['gas.enabled'],'direction.set':['furnace.direction'],'tube.configure':['tube.type'],'temperature.set':['temperature.target_k'],'cell.prepare':['cell.prepared'],'abd.power':['abd.powered'],'abd.stop':['abd.stopped'],'measurement.start':['measurement.phase'],'measurement.stop':['measurement.phase'],'process.measure':[*run,*obs[:2],res[0]],'result.read':['measurement.phase',*res],'signal.read':['detector.charge_c'],'blank.read':['blank.valid'],'product.read':['truth.ledger'],'records.list':['storage.result_count','storage.curve_count']}
 for k,(s,u,origin,vis,components,write) in SLOTS.items():
  mode='integral' if k=='detector.charge_c' else 'interval_mean' if k=='detector.current_a' else 'instant'
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=components,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=origin,visibility=vis,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'JenaMultixSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode=mode,time_domain='simulation',window_description='实际已观测电流窗口/累计积分区间；不以当前查询时间冒充采样时刻' if mode!='instant' else None),source_ids=['simulation_assumption'])
 errors=sorted({n.args[0].value for n in ast.walk(ast.parse(Path(__file__).read_text())) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='ActionRejected' and n.args and isinstance(n.args[0],ast.Constant) and isinstance(n.args[0].value,str)})
 for k,(inp,out,components,label,kind,vis,example) in CAPS.items():
  unsupported=k in UNSUPPORTED;async_=k=='measurement.start';effects=[dict(state_id=s,description_zh=SLOT_ZH[s],timing=t) for t,mapping in [('immediate',immediate),('on_step',onstep)] for s in dict.fromkeys(mapping.get(k,[]))]
  caps[k]=dict(function_zh=label,kind=kind,visibility=vis,component_ids=components,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation={layer:('implemented' if layer in ['process','host'] and not unsupported else 'absent') for layer in ['process','scene','robot','hardware','host']},binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.jena_multix.JenaMultixSession.invoke',invocation=f'session.invoke(instance_id="multix_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if kind=='query' or unsupported else [dict(description_zh='运行入口检查真实权限、模式/方向/管型/已安装附件、炉温、对应进样口与观测记录签名；时钟由宿主推进',state_ids=['power.enabled','instrument.ready','measurement.phase'],enforced_by='runtime',check='Runtime._invoke/_start/_fit and authenticated Session')],state_effects=[] if unsupported or kind=='query' else effects,observes=[] if unsupported else observes.get(k,[]),model_effects=dict(mode='none',description_zh='真实场景绑定由独立几何验证补充，查询不产生运动',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='完整方法积分窗口完成；发生极受所选池电流限制，未滴定残留不会偷偷还样' if async_ else '原子返回；有限动作/热过程只在step中推进',completion_state_ids=['measurement.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','prepared_sample','combustion_furnace','coulometer_cell','silver','carrier_gas'],partial_failure_zh='保留部分观测电荷、燃烧消耗和气路/池残留；样品原样不可恢复'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in errors],example_arguments=copy.deepcopy(example),verification=[],limitations=LIMITATIONS,source_ids=['manufacturer_primary','manufacturer_manual','manufacturer_abd','simulation_assumption'])
 mech=dict(component_ids=['sample_inlet','combustion_furnace','combustion_tube','gas_box','coulometer_cell','silver_anode'],state_ids=list(slots),meaning_zh='已预处理输入经有限燃烧和输运进入银库仑池；公开观测空白/标准反演Cl当量',implementation='lumped',equations=['n_equivalent=sum(nCl,nBr,nI); fluorine is excluded, proportions unknown for operational AOX source records.','Ag+ + X- -> AgX; physical Q=F*nAg, generator current capped at documented cell100uA/1mA/10mA.','Measured dQ=gain*physical_dQ+baseline*dt+explicit host noise; observations integrate actual window lengths.','Net observed charge=slope*reference_Cl_equivalent_mass+intercept; actual saved standards, >=3 distinct masses, interpolation only.','Registered sample + reacted oxygen = unburned + returned + residue + removed residue + uncharacterized reaction products.','Equivalent registered=unburned+returned+gas+cell+precipitated+unrecovered+drained.','Electrical input=thermal stored+thermal rejected; no invented matrix heat release.'],parameters={},source_ids=['manufacturer_primary','manufacturer_manual','simulation_assumption'],limitations=LIMITATIONS)
 spec=importlib.util.spec_from_file_location('_multix_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'combustion_microcoulometry':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE)})
 return dict(capabilities=caps,state_slots=slots,mechanisms={'combustion_microcoulometry':mech})

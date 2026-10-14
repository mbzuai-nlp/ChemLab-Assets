"""CHEM-087 RGA100 Faraday: finite vacuum inventory and observed EI ion-current scans."""
from __future__ import annotations
import ast,copy,csv,hashlib,importlib.util,io,json,math
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2];SLUG='stanford_rga100';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-087';PROTOCOL_VERSION=ASSET_VERSION='1.0.0'
TEMPLATE_REL='assets/expansion_batch04/t3_analytical/stanford_rga100/output_templates/library.json'
R_GAS=8.31446261815324;TORR_PA=101325./760.;U_KG=1.66053906892e-27;E_C=1.602176634e-19
OPERATING_MAX_PA=1e-4*TORR_PA;LINEAR_MAX_PA=1e-5*TORR_PA;FC_MIN_PA=5e-11*TORR_PA;N2_REFERENCE_A_PA=2e-4/TORR_PA
NF_ANALOG_S_U=[2.,1.,.4,.2,.126,.045,.030,.015];NF_SINGLE_S=[2.2,1.1,.44,.22,.139,.050,.033,.0165]
NF_REFERENCE_SD_A=[7e-15,1e-14,1.5e-14,2e-14,4e-14,1.2e-13,2.5e-13,5e-13]
MAX_RECORDS=256;MAX_CURVES=64;MAX_WINDOWS=30000
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
COMPONENT=obj({'species_id':TEXT,'amount_mol':number('mol',0,1)})
GAIN=obj({'species_id':TEXT,'sensitivity_a_pa':number('A/Pa',1e-12,1)})
PLANT=obj(dict(seed=integer(0,2**32-1),chamber_volume_m3=number('m^3',1e-6,1),temperature_k=number('K',273.15,343.15),pump_speed_m3_s=number('m^3/s',0,1),valve_speed_m_s=number('m/s',.001,1),ecu_speed_m_s=number('m/s',.001,1),cold_threshold_k=number('K',298.15,343.15),thermal_capacity_j_k=number('J/K',.01,1e4),thermal_loss_w_k=number('W/K',0,100),desorption_rate_mol_j=number('mol/J',0,1e-4),adsorbed_gases=arr(COMPONENT,0,100),default_sensitivity_a_pa=number('A/Pa',1e-12,1),species_sensitivities=arr(GAIN,0,100),peak_width_tenth_u=number('1',.1,2),mass_offset_u=number('1',-.25,.25),dark_current_a=number('A',-1e-10,1e-10),noise_scale=number('1',0,100),maximum_current_a=number('A',1e-12,1e-3),prelude_s=number('s',.001,10),stability_fraction=number('1',0,.2),fit_relative_residual_max=number('1',.000001,.5)))
PLANT_EXAMPLE=dict(seed=870,chamber_volume_m3=.001,temperature_k=298.15,pump_speed_m3_s=.005,valve_speed_m_s=.2,ecu_speed_m_s=.2,cold_threshold_k=323.15,thermal_capacity_j_k=10.,thermal_loss_w_k=.2,desorption_rate_mol_j=1e-14,adsorbed_gases=[{'species_id':'water','amount_mol':1e-12}],default_sensitivity_a_pa=N2_REFERENCE_A_PA,species_sensitivities=[{'species_id':'nitrogen','sensitivity_a_pa':N2_REFERENCE_A_PA},{'species_id':'argon','sensitivity_a_pa':N2_REFERENCE_A_PA*.8},{'species_id':'carbon_dioxide','sensitivity_a_pa':N2_REFERENCE_A_PA*1.2}],peak_width_tenth_u=1.,mass_offset_u=0.,dark_current_a=2e-15,noise_scale=0.,maximum_current_a=1e-7,prelude_s=.01,stability_fraction=.01,fit_relative_residual_max=.02)
METHOD=obj(dict(mode=enum(['analog','histogram','mid']),mass_min=integer(1,100),mass_max=integer(1,100),points_per_u=integer(10,25),mid_masses=arr(integer(1,100),1,100),cycles=integer(1,20),noise_floor=integer(0,7),electron_energy_v=number('V',25,105),ion_energy_v=enum([8,12]),focus_potential_v=number('V',-150,0),emission_current_a=number('A',0,.0035),candidates=arr(TEXT,0,12),accept_unknown_ei_conditions=BOOL))
METHOD_EXAMPLE=dict(mode='analog',mass_min=12,mass_max=46,points_per_u=10,mid_masses=[14,18,28,40,44],cycles=1,noise_floor=7,electron_energy_v=70.,ion_energy_v=12,focus_potential_v=-100.,emission_current_a=.001,candidates=['nitrogen','argon','carbon_dioxide'],accept_unknown_ei_conditions=True)
# Voltage enumeration remains numeric; SI quantities must not be string enumerations.
METHOD['properties']['ion_energy_v']={'type':'number','enum':[8,12],'x-unit':'V'}
SAMPLE=obj(dict(label=TEXT,kind=enum(['sample','standard','blank']),components=arr(COMPONENT,0,100),standard_species_id=nullable(TEXT),accept_unknown_ei_conditions=BOOL))
def gas_for_pressure(species,pressure_pa,plant=None):
 p=plant or PLANT_EXAMPLE
 return {'species_id':species,'amount_mol':pressure_pa*p['chamber_volume_m3']/(R_GAS*p['temperature_k'])}
SAMPLE_EXAMPLE=dict(label='host_prepared_three_gas_mixture',kind='sample',components=[gas_for_pressure('nitrogen',2e-4),gas_for_pressure('argon',1e-4),gas_for_pressure('carbon_dioxide',1.5e-4)],standard_species_id=None,accept_unknown_ei_conditions=True)
STANDARD_EXAMPLE=dict(label='host_prepared_N2_pressure_standard',kind='standard',components=[gas_for_pressure('nitrogen',2e-4)],standard_species_id='nitrogen',accept_unknown_ei_conditions=True)
BLANK_EXAMPLE=dict(label='host_prepared_evacuated_blank',kind='blank',components=[],standard_species_id=None,accept_unknown_ei_conditions=True)
POINT=obj(dict(start_s=number('s',0),end_s=number('s',0),nominal_mass_to_charge=number('1'),mass_to_charge_kg_c=number('kg/C'),current_a=number('A'),raw_current_a=number('A'),saturated=BOOL,group_index=integer(0,MAX_WINDOWS),cycle=integer(0,19)))
PEAK=obj(dict(nominal_mass_to_charge=number('1'),mass_to_charge_kg_c=number('kg/C'),current_a=number('A'),start_s=number('s'),end_s=number('s'),cycle=integer(0,19)))
SPECIES_RESULT=obj(dict(species_id=TEXT,partial_pressure_pa=nullable(number('Pa')),mass_concentration_kg_m3=nullable(number('kg/m^3')),fitted_partial_pressure_pa=nullable(number('Pa')),quality=arr(TEXT,0,15)))
RESULT=obj(dict(record_id=TEXT,kind=TEXT,status=TEXT,complete=BOOL,points=arr(POINT,0,MAX_WINDOWS),reported_peaks=arr(PEAK,0,MAX_WINDOWS),species=arr(SPECIES_RESULT,0,12),total_partial_pressure_pa=nullable(number('Pa')),relative_residual=nullable(number('1')),matrix_rank=integer(0,12),condition_number=nullable(number('1')),acquisition_signature=TEXT,method_json=TEXT,provenance_json=TEXT,quality=arr(TEXT,0,30)))
LEDGER=obj(dict(species_json=TEXT,registered_mol=number('mol'),chamber_mol=number('mol'),adsorbed_remaining_mol=number('mol'),pumped_mol=number('mol'),returned_mol=number('mol'),residual_mol=number('mol'),mass_residual_kg=number('kg'),electrical_energy_j=number('J'),thermal_stored_j=number('J'),thermal_lost_j=number('J'),energy_residual_j=number('J')))
CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',example=None):CAPS[k]=(i,o,c,z,kind,vis,example or {})
cap('plant.configure',PLANT,obj({'configured':BOOL}),['host_chamber'],'宿主真空腔、有限气体和响应初始化','configuration','evaluator',PLANT_EXAMPLE)
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['body'],'电源；断电保留部分观察并关闭发射',example={'enabled':True})
cap('ecu.set',obj({'detached':BOOL}),obj({'target_m':number('m',0,.08)}),['ecu_carriage'],'冷断电宿主托持ECU有限退离；不是OEM电动拔插','device_command','evaluator',{'detached':False})
cap('vacuum.valve',obj({'opened':BOOL}),obj({'target_m':number('m',0,.04)}),['host_gate'],'宿主泵支路隔离阀有限升程；非RGA本机机构','device_command','evaluator',{'opened':False})
cap('vacuum.pump',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['vacuum_port'],'宿主外泵抽速开关；物种有限抽走而非直接设压力','device_command','evaluator',{'enabled':False})
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['host_chamber'],'宿主密闭容器批次气体交换；真值仅限宿主','device_command','evaluator',SAMPLE_EXAMPLE)
cap('sample.unload',obj({'recipient_id':TEXT}),obj({'transferred_mol':number('mol',0)}),['host_chamber'],'停灯密闭宿主容器交接剩余气体；不恢复已泵出气体','device_command','evaluator',{'recipient_id':'host_recovery'})
cap('host.filament_fault',obj({'faulted':BOOL}),obj({'faulted':BOOL}),['filament'],'冷断电宿主声明灯丝完整性，不推断寿命或实际损坏','configuration','evaluator',{'faulted':False})
cap('method.configure',METHOD,obj({'configured':BOOL}),['ionizer','quadrupole','faraday_cup'],'EI电势、NF、模拟/整数寻峰/MID及公开候选组分','configuration',example=METHOD_EXAMPLE)
cap('filament.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['filament'],'低于操作总压上限才开灯；关灯保留扫描暗电流',example={'enabled':True})
cap('detector.zero',obj({}),obj({'zero_current_a':number('A')}),['faraday_cup'],'仪器内部无输入电流电子零点，不等于气体空白')
cap('scan.start',obj({}),obj({'operation_id':TEXT}),['ionizer','quadrupole','faraday_cup'],'真实NF窗口、七点寻峰和有限次数扫描')
cap('measurement.stop',obj({}),obj({'phase':TEXT}),['faraday_cup','filament'],'停止扫描或脱气；保存部分积分窗口及真实退气残留')
cap('degas.start',obj({'duration_min':integer(1,20)}),obj({'operation_id':TEXT}),['ionizer','filament'],'400V电子激励；1至20mA用60s爬升，有限吸附气体解吸',example={'duration_min':1})
cap('process.measure',obj({}),obj({'phase':TEXT,'stage':TEXT,'elapsed_s':number('s'),'progress':number('1',0,1),'current_a':nullable(number('A')),'mass_ratio':nullable(number('1'))}),['display_panel'],'纯读真实最近窗口与过程','query')
cap('result.read',obj({}),RESULT,['display_panel'],'公开候选矩阵以观察电流反演分压；歧义/不足/越域为null','query')
cap('spectrum.read',obj({}),obj({'points':arr(POINT,0,MAX_WINDOWS),'reported_peaks':arr(PEAK,0,MAX_WINDOWS),'evidence':TEXT}),['faraday_cup'],'模拟原始电流窗口与寻峰值，绝不冒充厂家电子原数组','query')
cap('result.export',obj({'format':enum(['json','csv'])}),obj({'format':TEXT,'content':TEXT}),['display_panel'],'导出A、s、Pa、kg/C及原名义m/z','query',example={'format':'json'})
cap('blank.read',obj({}),obj({'valid':BOOL,'blank_json':TEXT}),['display_panel'],'已实际采集且相同公开方法/响应的气体空白','query')
cap('calibration.fit',obj({'curve_id':TEXT,'species_id':TEXT,'record_ids':arr(TEXT,3,20)}),obj({'curve_id':TEXT,'sensitivity_a_pa':number('A/Pa'),'r_squared':number('1')}),['display_panel'],'从至少三次实际标准扫描及宿主已知压力拟合响应','analysis',example={'curve_id':'n2','species_id':'nitrogen','record_ids':['record_2','record_3','record_4']})
cap('calibration.select',obj({'curve_ids':arr(TEXT,1,12)}),obj({'selected':arr(TEXT,1,12)}),['display_panel'],'选同签名、独立物种标线供公开混合矩阵','device_command',example={'curve_ids':['n2']})
cap('calibration.read',obj({'curve_id':TEXT}),obj({'curve_json':TEXT}),['display_panel'],'纯读真实观察、拟合和插值域','query',example={'curve_id':'n2'})
cap('records.list',obj({}),obj({'record_ids':arr(TEXT,0,MAX_RECORDS),'curve_ids':arr(TEXT,0,MAX_CURVES)}),['display_panel'],'实例独立有限工程缓存','query')
cap('records.read',obj({'record_id':TEXT}),RESULT,['display_panel'],'冻结窗口/方法/结果历史，不受之后方法覆写','query',example={'record_id':'record_1'})
cap('records.remove',obj({'record_id':TEXT}),obj({'removed':BOOL}),['display_panel'],'删除结果，已终结操作收据仍受owner保护',example={'record_id':'record_1'})
cap('template.read',obj({'substance_id':TEXT}),obj({'template_json':TEXT}),['display_panel'],'原始EI全峰含>100、来源条件未知与基峰归一分开','query',example={'substance_id':'nitrogen'})
cap('product.read',obj({}),LEDGER,['host_chamber','ionizer'],'受限摩尔/质量/退气/热账本','query','evaluator')
UNSUPPORTED={'detector.electron_multiplier','hardware.connect','software.gui','scan.native_binary','analysis.total_pressure_gauge','vacuum.bake','scan.continuous'}
for k in sorted(UNSUPPORTED):cap(k,obj({}),obj({}),['body'],'当前未安装或未实现：'+k)
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,origin='derived',vis='agent',parts=('display_panel',),write=None):SLOTS[k]=(s,u,origin,vis,list(parts),write);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','宿主已初始化');slot('power.enabled',BOOL,'1','ECU供电','setpoint',parts=('body',),write=('power.set','enabled'))
slot('ecu.withdrawal_m',number('m',0,.08),'m','可信宿主托持ECU工程有限退离','scene',parts=('ecu_carriage',));slot('ecu.target_m',number('m',0,.08),'m','宿主ECU退离目标','setpoint',parts=('ecu_carriage',))
slot('host.valve_lift_m',number('m',0,.04),'m','外部真空隔离阀工程有限升程','scene',parts=('host_gate',));slot('host.valve_target_m',number('m',0,.04),'m','外部真空阀目标','setpoint',parts=('host_gate',));slot('host.pump_enabled',BOOL,'1','外部理想泵开关','setpoint',parts=('vacuum_port',))
slot('vacuum.pressure_pa',number('Pa',0),'Pa','宿主理想独立总压反馈；不是RGA已识别真实总分压',parts=('host_chamber',));slot('ionizer.temperature_k',number('K',273.15,10000),'K','显式工程热容量下的离子源温度代理',parts=('ionizer',))
slot('filament.enabled',BOOL,'1','正常扫描发射开关','setpoint',parts=('filament',),write=('filament.set','enabled'));slot('filament.emission_current_a',number('A',0,.02),'A','扫描设定/脱气爬升电子发射电流；不是离子电流',parts=('filament',));slot('ionizer.electron_energy_v',number('V',0,400),'V','电子加速电势：扫描25–105V，脱气400V',parts=('ionizer',));slot('degas.power_w',number('W',0,8.0001),'W','脱气电子束功率V×A；不是整机功率',parts=('filament',))
slot('instrument.ready',BOOL,'1','供电/ECU/压力允许扫描；暗电流扫描不必开灯');slot('method.mode',TEXT,'1','analog/histogram/mid公开选择');slot('method.noise_floor',integer(0,7),'1','NF0–7，时间表有据噪声仍为宿主模型')
slot('scan.phase',TEXT,'1','闲置/运行/完成/取消/保护终态');slot('scan.stage',TEXT,'1','电子零点准备/窗口扫描/脱气步骤');slot('scan.elapsed_s',number('s',0),'s','实际累计模拟时间');slot('scan.progress',number('1',0,1),'1','有限扫描或脱气进度');slot('mass.current_ratio',nullable(number('1')),'1','最近实际窗口名义m/z，单位参照u/e非kg',parts=('quadrupole',));slot('mass.current_kg_c',nullable(number('kg/C')),'kg/C','名义m/z乘固定CODATA2022 u/e',parts=('quadrupole',));slot('detector.current_a',nullable(number('A')),'A','扣电子零点后的实际Faraday窗口平均电流',parts=('faraday_cup',));slot('detector.last_sample_time_s',nullable(number('s')),'s','最近真实电流窗口终点',parts=('faraday_cup',));slot('detector.zero_current_a',number('A'),'A','内部电子零点，不是气体背景',parts=('faraday_cup',));slot('result.total_partial_pressure_pa',nullable(number('Pa')),'Pa','已校准公开候选分压之和；不是本机无筛选总压模式');slot('blank.valid',BOOL,'1','本方法/响应匹配的真实观察空白');slot('calibration.ready',BOOL,'1','公开候选皆有相容响应标线');slot('storage.result_count',integer(0,MAX_RECORDS),'1','256条工程结果容量非OEM宣称');slot('storage.curve_count',integer(0,MAX_CURVES),'1','64条工程标线容量非OEM宣称');slot('safety.warning',TEXT,'1','公开保护原因不暴露隐藏气体身份');slot('truth.ledger',LEDGER,'1','有限气体/退气/质量/热账本','process_truth','evaluator',('host_chamber','ionizer'))

def _library():
 p=ROOT/TEMPLATE_REL
 return json.loads(p.read_text()) if p.exists() else {'compounds':{}}
def _move(x,target,speed,dt):return x+max(-speed*dt,min(speed*dt,target-x))
PLANT['properties'].update(supply_voltage_v=number('V',0,30),filament_intact=BOOL,matrix_condition_max=number('1',1,1e8));PLANT['required']=list(PLANT['properties']);PLANT_EXAMPLE.update(supply_voltage_v=24.,filament_intact=True,matrix_condition_max=1000.)
for k,z in [('power','有效22–26V供电'),('filament','普通发射或脱气发射'),('degas','脱气过程中'),('em_active','标准Faraday配置无EM，恒false'),('error','本模型覆盖的有限故障，非完整OEM STATUS位'),('overpressure','开灯或脱气时实际总压保护已触发'),('filament_fault','宿主明确声明灯丝不完整，非Thoria老化识别')]:slot('led.'+k,BOOL,'1',z,parts=('body',))
slot('led.rs232_active',nullable(BOOL),'1','原生TX/RX未接入为null，不用扫描busy代替','derived',parts=('body',))

class StanfordRga100Runtime:
 def __init__(self,instance_id='rga100_01',library=None):
  self.instance_id=instance_id;self.library=copy.deepcopy(library) if library is not None else _library();self.templates=self.library.get('compounds',{});self.plant=None;self.method=copy.deepcopy(METHOD_EXAMPLE);self.time_s=0.;self.revision=0;self.powered=False;self.filament=False;self.ecu=0.;self.ecu_target=0.;self.valve=0.;self.valve_target=0.;self.pump=False;self.temperature=298.15;self.phase='idle';self.stage='idle';self.elapsed_s=0.;self.warning='';self.overpressure=False;self.emission=0.;self.energy_v=0.;self.degas_power=0.;self.zero=0.;self.gases={};self.adsorbed={};self.registered={};self.pumped={};self.returned={};self.sample_kind='blank';self.standard=None;self.electrical=0.;self.thermal_lost=0.;self.points=[];self.peaks=[];self._last_point=None;self._result_cache=None;self._records={};self._curves={};self._blanks={};self._standard_records={};self._selected=[];self._operation_counter=0;self._record_counter=0;self.operation_id=None;self._operation_records={};self._owners={};self._requests={};self._principals={'__public_agent__':'agent'};self._basis_cache={};self._pending=0.;self._pending_dt=0.;self._point_index=0;self._grid=[];self._run_key='';self._run_method=None;self._run_blank=None;self._run_curves={};self._quality=[];self._run_pressure_min=0.;self._run_pressure_max=0.;self._degas_duration=0.;self._degas_restore=False;self._run_emission=False;self._scan_duration=0.
 def register_principal(self,principal_id,visibility):
  if not isinstance(principal_id,str) or not principal_id or visibility not in ['agent','debug','evaluator']:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals and self._principals[principal_id]!=visibility:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return StanfordRga100Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _pressure(self):return sum(self.gases.values())*R_GAS*self.plant['temperature_k']/self.plant['chamber_volume_m3'] if self.plant else 0.
 def _ready(self):return bool(self.plant and self.powered and self.ecu<1e-12 and self.ecu_target==0 and self._pressure()<OPERATING_MAX_PA)
 def _key(self):
  if self.plant is None:return ''
  response={k:self.plant[k] for k in ['default_sensitivity_a_pa','species_sensitivities','peak_width_tenth_u','mass_offset_u','dark_current_a','noise_scale','maximum_current_a','temperature_k']}
  return hashlib.sha256(json.dumps({'method':self.method,'response':response,'library_sha':self._library_hash},sort_keys=True,allow_nan=False).encode()).hexdigest()
 def _blank(self):return self._blanks.get(self._key())
 def _curves_now(self):return {self._curves[i]['species_id']:self._curves[i] for i in self._selected if i in self._curves and self._curves[i]['acquisition_signature']==self._key()}
 def _progress(self):
  if self.phase=='succeeded':return 1.
  d=self._degas_duration if self.stage=='degassing' else self._scan_duration
  return min(1.,self.elapsed_s/d) if d else 0.
 def _ei(self,species):
  r=self.templates.get(species)
  if not r:raise ActionRejected('UNKNOWN_TEMPLATE')
  ei=r.get('ei')
  if not ei or r.get('physical_input_eligible') is False:raise ActionRejected('REFERENCE_QUERY_ONLY')
  x=np.asarray(ei['mass_to_charge_relative'],float);y=np.asarray(ei['relative_intensity'],float)
  if len(x)!=len(y) or not len(x) or not np.isfinite(x).all() or not np.isfinite(y).all() or np.any(y<0):raise ActionRejected('INVALID_REFERENCE')
  return x,y
 def _molar(self,species):
  r=self.templates.get(species,{});m=r.get('molar_mass_kg_mol')
  if not isinstance(m,(int,float)) or not math.isfinite(m) or m<=0:raise ActionRejected('MOLAR_MASS_UNKNOWN')
  return float(m)
 def _shape(self,species,x):
  # Use full-source normalization; cropping to this instrument never scales remaining peaks up.
  ref,intensity=self._ei(species);mask=(ref>=1)&(ref<=100);ref=ref[mask];intensity=intensity[mask]
  if not len(ref):return np.zeros(len(x))
  sigma=self.plant['peak_width_tenth_u']/(2*math.sqrt(2*math.log(10)))
  return np.exp(-.5*((np.asarray(x)[:,None]-ref[None,:]-self.plant['mass_offset_u'])/sigma)**2)@intensity
 def _gain(self,species):return next((x['sensitivity_a_pa'] for x in self.plant['species_sensitivities'] if x['species_id']==species),self.plant['default_sensitivity_a_pa'])
 def _ledger(self):
  ids=set(self.registered)|set(self.gases)|set(self.adsorbed)|set(self.pumped)|set(self.returned);rows={};massres=0.
  for s in sorted(ids):
   v=dict(registered_mol=self.registered.get(s,0.),chamber_mol=self.gases.get(s,0.),adsorbed_mol=self.adsorbed.get(s,0.),pumped_mol=self.pumped.get(s,0.),returned_mol=self.returned.get(s,0.));v['residual_mol']=v['registered_mol']-v['chamber_mol']-v['adsorbed_mol']-v['pumped_mol']-v['returned_mol'];v['molar_mass_kg_mol']=self._molar(s);massres+=v['residual_mol']*v['molar_mass_kg_mol'];rows[s]=v
  registered=sum(self.registered.values());chamber=sum(self.gases.values());ad=sum(self.adsorbed.values());pumped=sum(self.pumped.values());returned=sum(self.returned.values());stored=(self.temperature-self.plant['temperature_k'])*self.plant['thermal_capacity_j_k'] if self.plant else 0.
  return dict(species_json=json.dumps(rows,sort_keys=True),registered_mol=registered,chamber_mol=chamber,adsorbed_remaining_mol=ad,pumped_mol=pumped,returned_mol=returned,residual_mol=registered-chamber-ad-pumped-returned,mass_residual_kg=massres,electrical_energy_j=self.electrical,thermal_stored_j=stored,thermal_lost_j=self.thermal_lost,energy_residual_j=self.electrical-stored-self.thermal_lost)
 def read(self,visibility='agent'):
  if visibility not in ['agent','debug','evaluator']:raise ActionRejected('ACCESS_DENIED')
  pt=self._last_point;r=self._result_cache or {'total_partial_pressure_pa':None};p=self.plant
  v={'plant.configured':p is not None,'power.enabled':self.powered,'ecu.withdrawal_m':self.ecu,'ecu.target_m':self.ecu_target,'host.valve_lift_m':self.valve,'host.valve_target_m':self.valve_target,'host.pump_enabled':self.pump,'vacuum.pressure_pa':self._pressure(),'ionizer.temperature_k':self.temperature,'filament.enabled':self.filament,'filament.emission_current_a':self.emission,'ionizer.electron_energy_v':self.energy_v,'degas.power_w':self.degas_power,'instrument.ready':self._ready(),'method.mode':self.method['mode'],'method.noise_floor':self.method['noise_floor'],'scan.phase':self.phase,'scan.stage':self.stage,'scan.elapsed_s':self.elapsed_s,'scan.progress':self._progress(),'mass.current_ratio':pt['nominal_mass_to_charge'] if pt else None,'mass.current_kg_c':pt['mass_to_charge_kg_c'] if pt else None,'detector.current_a':pt['current_a'] if pt else None,'detector.last_sample_time_s':pt['end_s'] if pt else None,'detector.zero_current_a':self.zero,'result.total_partial_pressure_pa':r['total_partial_pressure_pa'],'blank.valid':self._blank() is not None,'calibration.ready':bool(self.method['candidates']) and set(self.method['candidates'])<=set(self._curves_now()),'storage.result_count':len(self._records),'storage.curve_count':len(self._curves),'safety.warning':self.warning,'led.power':bool(self.powered and p and 22<=p['supply_voltage_v']<=26),'led.filament':self.emission>0,'led.degas':self.phase=='running' and self.stage=='degassing','led.em_active':False,'led.rs232_active':None,'led.error':bool(self.powered and (self.overpressure or (p and not p['filament_intact']))),'led.overpressure':bool(self.powered and self.overpressure),'led.filament_fault':bool(self.powered and p and not p['filament_intact'])}
  if visibility!='agent':v['truth.ledger']=self._ledger()
  return v
 def invoke(self,capability,**arguments):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],arguments)
  if capability in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  result=self._invoke(capability,arguments);validate(CAPS[capability][1],result)
  if CAPS[capability][4]!='query':self.revision+=1
  return copy.deepcopy(result)
 def _invoke(self,k,a):
  if k=='template.read':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_TEMPLATE')
   return {'template_json':json.dumps(self.templates[a['substance_id']],ensure_ascii=False,sort_keys=True,allow_nan=False)}
  if k=='plant.configure':
   if self.plant is not None or self.time_s!=0:raise ActionRejected('INITIALIZATION_ONLY')
   if a['cold_threshold_k']<a['temperature_k']:raise ActionRejected('INVALID_ARGUMENT')
   for seq in [a['adsorbed_gases'],a['species_sensitivities']]:
    if len({x['species_id'] for x in seq})!=len(seq):raise ActionRejected('DUPLICATE_SPECIES')
    for x in seq:self._ei(x['species_id']);self._molar(x['species_id'])
   self.plant=copy.deepcopy(a);self.rng=np.random.default_rng(a['seed']);self.temperature=a['temperature_k'];self.adsorbed={x['species_id']:x['amount_mol'] for x in a['adsorbed_gases']};self.registered=copy.deepcopy(self.adsorbed);self._library_hash=hashlib.sha256(json.dumps(self.library,sort_keys=True,allow_nan=False).encode()).hexdigest();return {'configured':True}
  if k=='process.measure':return dict(phase=self.phase,stage=self.stage,elapsed_s=self.elapsed_s,progress=self._progress(),current_a=self._last_point['current_a'] if self._last_point else None,mass_ratio=self._last_point['nominal_mass_to_charge'] if self._last_point else None)
  if k=='product.read':return self._ledger()
  if k=='result.read':return self._result()
  if k=='spectrum.read':return dict(points=copy.deepcopy(self.points),reported_peaks=copy.deepcopy(self.peaks),evidence='synthetic Faraday current interval means; miniscan subpoints are model telemetry, not the OEM transmitted peak-only data')
  if k=='result.export':
   if a['format']=='json':content=json.dumps(self._result(),ensure_ascii=False,allow_nan=False)
   else:
    f=io.StringIO();w=csv.DictWriter(f,fieldnames=list(POINT['properties']));w.writeheader();w.writerows(self.points);content=f.getvalue()
   return {'format':a['format'],'content':content}
  if k=='blank.read':return {'valid':self._blank() is not None,'blank_json':json.dumps(self._blank(),allow_nan=False)}
  if k=='records.list':return dict(record_ids=list(self._records),curve_ids=list(self._curves))
  if k=='records.read':
   if a['record_id'] not in self._records:raise ActionRejected('UNKNOWN_RECORD')
   return copy.deepcopy(self._records[a['record_id']])
  if k=='calibration.read':
   if a['curve_id'] not in self._curves:raise ActionRejected('UNKNOWN_CURVE')
   return {'curve_json':json.dumps(self._curves[a['curve_id']],allow_nan=False)}
  if not self.plant:raise ActionRejected('NOT_CONFIGURED')
  if k=='power.set':
   if a['enabled'] and (self.ecu>1e-12 or self.ecu_target>0):raise ActionRejected('ECU_DETACHED')
   if a['enabled'] and not 22<=self.plant['supply_voltage_v']<=26:raise ActionRejected('SUPPLY_VOLTAGE')
   if not a['enabled']:
    if self.phase=='running':self._stop('cancelled','power_off')
    self.filament=False;self.emission=0.;self.energy_v=0.
   self.powered=a['enabled'];return {'enabled':self.powered}
  if k=='vacuum.valve':self.valve_target=.04 if a['opened'] else 0.;return {'target_m':self.valve_target}
  if k=='vacuum.pump':self.pump=a['enabled'];return {'enabled':self.pump}
  if k=='filament.set':
   if self.phase=='running' and self.stage=='degassing':raise ActionRejected('BUSY')
   if a['enabled']:
    if not self._ready():raise ActionRejected('VACUUM_OR_POWER')
    if not self.plant['filament_intact']:raise ActionRejected('FILAMENT_FAULT')
    if self.method['emission_current_a']<=0:raise ActionRejected('ZERO_EMISSION')
   if self.phase=='running' and a['enabled']!=self.filament:self._quality.append('illumination_changed_during_scan')
   self.filament=a['enabled']
   if self.filament and self.overpressure:self.overpressure=False;self.warning=''
   return {'enabled':self.filament}
  if k=='measurement.stop':
   if self.phase=='running':self._stop('cancelled','operator_cancelled')
   return {'phase':self.phase}
  self._idle()
  if k=='host.filament_fault':
   if self.powered or self.filament:raise ActionRejected('COLD_POWER_OFF_REQUIRED')
   self.plant['filament_intact']=not a['faulted'];self.warning='host_declared_filament_fault' if a['faulted'] else '';return {'faulted':a['faulted']}
  if k=='ecu.set':
   if self.powered or self.filament or self.temperature>self.plant['cold_threshold_k']:raise ActionRejected('COLD_POWER_OFF_REQUIRED')
   self.ecu_target=.08 if a['detached'] else 0.;return {'target_m':self.ecu_target}
  if k in ['sample.load','sample.unload']:
   if self.filament or self.valve>1e-12 or self.valve_target>0:raise ActionRejected('SEALED_FILAMENT_OFF_REQUIRED')
   if k=='sample.load':
    if not a['accept_unknown_ei_conditions']:raise ActionRejected('SOURCE_CONDITION_ACK_REQUIRED')
    ids=[x['species_id'] for x in a['components']]
    if len(ids)!=len(set(ids)):raise ActionRejected('DUPLICATE_SPECIES')
    for s in ids:self._ei(s);self._molar(s)
    if a['kind']=='blank' and any(x['amount_mol']>0 for x in a['components']):raise ActionRejected('NONZERO_BLANK')
    if a['kind']=='standard' and (len(ids)!=1 or ids[0]!=a['standard_species_id'] or a['components'][0]['amount_mol']<=0):raise ActionRejected('PURE_STANDARD_REQUIRED')
    if a['kind']!='standard' and a['standard_species_id'] is not None:raise ActionRejected('INVALID_ARGUMENT')
   elif not a['recipient_id']:raise ActionRejected('RECIPIENT_REQUIRED')
   transferred=sum(self.gases.values())
   for s,n in self.gases.items():self.returned[s]=self.returned.get(s,0.)+n
   self.gases={};self.standard=None
   if k=='sample.unload':self.sample_kind='blank';return {'transferred_mol':transferred}
   self.gases={x['species_id']:x['amount_mol'] for x in a['components']};self.sample_kind=a['kind']
   for s,n in self.gases.items():self.registered[s]=self.registered.get(s,0.)+n
   if a['kind']=='standard':self.standard=dict(species_id=a['standard_species_id'],assigned_pressure_pa=self._pressure(),evidence='host-prepared pressure from explicitly supplied mol,V,T; not a source-certified pressure standard')
   return {'loaded':True}
  if k=='method.configure':
   if a['mass_max']<a['mass_min'] or len(set(a['mid_masses']))!=len(a['mid_masses']) or len(set(a['candidates']))!=len(a['candidates']):raise ActionRejected('INVALID_SCAN_GRID')
   if not a['accept_unknown_ei_conditions']:raise ActionRejected('SOURCE_CONDITION_ACK_REQUIRED')
   for s in a['candidates']:self._ei(s);self._molar(s)
   if len(self._make_grid(a))>MAX_WINDOWS:raise ActionRejected('WINDOW_CAPACITY')
   self.method=copy.deepcopy(a);return {'configured':True}
  if k=='detector.zero':
   if not self._ready():raise ActionRejected('VACUUM_OR_POWER')
   self.zero=self.plant['dark_current_a'];return {'zero_current_a':self.zero}
  if k=='scan.start':return self._start_scan()
  if k=='degas.start':
   if not self._ready():raise ActionRejected('VACUUM_OR_POWER')
   if not self.plant['filament_intact']:raise ActionRejected('FILAMENT_FAULT')
   self._degas_restore=self.filament;self._degas_duration=60.*a['duration_min'];self.phase='running';self.stage='degassing';self.elapsed_s=0.;self.warning='';self.overpressure=False;self._operation_counter+=1;self.operation_id=f'{self.instance_id}_operation_{self._operation_counter}';return {'operation_id':self.operation_id}
  if k=='calibration.fit':return self._fit(a)
  if k=='calibration.select':
   curves=[]
   for i in a['curve_ids']:
    if i not in self._curves:raise ActionRejected('UNKNOWN_CURVE')
    if self._curves[i]['acquisition_signature']!=self._key():raise ActionRejected('CALIBRATION_SIGNATURE')
    curves.append(self._curves[i])
   if len({c['species_id'] for c in curves})!=len(curves):raise ActionRejected('DUPLICATE_SPECIES')
   self._selected=list(a['curve_ids']);return {'selected':self._selected}
  if k=='records.remove':
   if a['record_id'] not in self._records:raise ActionRejected('UNKNOWN_RECORD')
   del self._records[a['record_id']];return {'removed':True}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _make_grid(self,m):
  grid=[];group=0
  for cycle in range(m['cycles']):
   if m['mode']=='analog':
    for j in range((m['mass_max']-m['mass_min'])*m['points_per_u']+1):grid.append((m['mass_min']+j/m['points_per_u'],NF_ANALOG_S_U[m['noise_floor']]/m['points_per_u'],group,cycle));group+=1
   else:
    masses=range(m['mass_min'],m['mass_max']+1) if m['mode']=='histogram' else m['mid_masses']
    for mass in masses:
     for offset in [-.3,-.2,-.1,0.,.1,.2,.3]:grid.append((mass+offset,NF_SINGLE_S[m['noise_floor']]/7,group,cycle))
     group+=1
  return grid
 def _start_scan(self):
  if not self._ready():raise ActionRejected('VACUUM_OR_POWER')
  if len(self._records)>=MAX_RECORDS:raise ActionRejected('RECORD_CAPACITY')
  grid=self._make_grid(self.method)
  if len(grid)>MAX_WINDOWS:raise ActionRejected('WINDOW_CAPACITY')
  # All physical gases contribute; candidates only determine the public inference matrix.
  basis={s:self._shape(s,[g[0] for g in grid])*self._gain(s) for s in set(self.gases)|set(self.adsorbed)}
  self.points=[];self.peaks=[];self._last_point=None;self._result_cache=None;self._pending=self._pending_dt=0.;self._point_index=0;self._grid=grid;self._basis_cache=basis;self._run_key=self._key();self._run_method=copy.deepcopy(self.method);self._run_blank=copy.deepcopy(self._blank());self._run_curves=copy.deepcopy(self._curves_now());self._run_emission=self.filament;self._quality=[];self._run_kind=self.sample_kind;self._run_standard=copy.deepcopy(self.standard);
  if self.standard and not math.isclose(self._pressure(),self.standard['assigned_pressure_pa'],rel_tol=self.plant['stability_fraction'],abs_tol=1e-20):self._quality.append('standard_preparation_pressure_changed')
  self._run_pressure_min=self._run_pressure_max=self._pressure();self._scan_duration=self.plant['prelude_s']+sum(g[1] for g in grid);self.elapsed_s=0.;self._prelude_remaining=self.plant['prelude_s'];self.phase='running';self.stage='preparing';self.warning='';self.overpressure=False;self._operation_counter+=1;self.operation_id=f'{self.instance_id}_operation_{self._operation_counter}';self._scan_start=self.time_s
  if self.method['mode'] in ['analog','histogram']:self.zero=self.plant['dark_current_a']
  return {'operation_id':self.operation_id}
 def _physical(self,dt):
  p=self.plant;oldvalve=self.valve;self.valve=_move(self.valve,self.valve_target,p['valve_speed_m_s'],dt);self.ecu=_move(self.ecu,self.ecu_target,p['ecu_speed_m_s'],dt)
  dg=self.phase=='running' and self.stage=='degassing';self.emission=(.001+.019*min(1.,(self.elapsed_s+dt/2)/60.)) if dg else self.method['emission_current_a'] if self.powered and self.filament else 0.;self.energy_v=400. if dg else self.method['electron_energy_v'] if self.emission>0 else 0.;self.degas_power=self.emission*self.energy_v if dg else 0.;power=self.emission*self.energy_v
  # Effective ionizer heat is an explicit electron-beam-only proxy, not whole ECU or thermionic heater power.
  energy=power*dt;cap=self.plant['thermal_capacity_j_k'];loss=self.plant['thermal_loss_w_k'];old=self.temperature
  if loss>0:
   equilibrium=p['temperature_k']+power/loss;self.temperature=equilibrium+(old-equilibrium)*math.exp(-loss*dt/cap)
  else:self.temperature=old+energy/cap
  self.electrical+=energy;self.thermal_lost+=energy-cap*(self.temperature-old)
  k=p['pump_speed_m3_s']/p['chamber_volume_m3']*(oldvalve+self.valve)/.08 if self.pump else 0.;mean={};ids=set(self.gases)|set(self.adsorbed)
  for s in ids:
   released=min(self.adsorbed.get(s,0.),p['desorption_rate_mol_j']*energy) if dg else 0.;self.adsorbed[s]=self.adsorbed.get(s,0.)-released;n=self.gases.get(s,0.);r=released/dt
   if k>0:
    q=-math.expm1(-k*dt);end=n*(1-q)+r*q/k;integral=n*q/k+r/k*(dt-q/k)
   else:end=n+released;integral=(n+end)*dt/2
   removed=max(0.,n+released-end);self.gases[s]=max(0.,end);self.pumped[s]=self.pumped.get(s,0.)+removed;mean[s]=integral/dt*R_GAS*p['temperature_k']/p['chamber_volume_m3']
  if (self.filament or dg) and self._pressure()>=OPERATING_MAX_PA:
   self.overpressure=True;self.filament=False;self.emission=0.;self.energy_v=0.;self.degas_power=0.
   if self.phase=='running':self._stop('failed','overpressure')
   self.warning='overpressure'
  return mean
 def _complete_point(self,partial=False):
  if self._pending_dt<=0:return
  g=self._grid[self._point_index];duration=self._pending_dt;noise=self.rng.normal(0.,NF_REFERENCE_SD_A[self._run_method['noise_floor']]*self.plant['noise_scale']) if self.plant['noise_scale'] else 0.;raw=self._pending/duration+self.plant['dark_current_a']+noise;clipped=max(-self.plant['maximum_current_a'],min(self.plant['maximum_current_a'],raw));end=self.time_s;point=dict(start_s=end-duration,end_s=end,nominal_mass_to_charge=g[0],mass_to_charge_kg_c=g[0]*U_KG/E_C,current_a=float(clipped-self.zero),raw_current_a=float(clipped),saturated=bool(clipped!=raw),group_index=g[2],cycle=g[3]);self.points.append(point);self._last_point=point;self._pending=0.;self._pending_dt=0.;self._point_index+=1
  if point['saturated'] and 'detector_saturated' not in self._quality:self._quality.append('detector_saturated')
  if self._run_method['mode']!='analog' and (self._point_index==len(self._grid) or self._grid[self._point_index][2]!=g[2]):
   part=[x for x in self.points[-7:] if x['group_index']==g[2]]
   if len(part)==7 and not partial:self.peaks.append(dict(nominal_mass_to_charge=round(g[0]),mass_to_charge_kg_c=round(g[0])*U_KG/E_C,current_a=max(x['current_a'] for x in part),start_s=part[0]['start_s'],end_s=part[-1]['end_s'],cycle=g[3]))
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<=0:raise ActionRejected('INVALID_TIMESTEP')
  remaining=float(dt_s)
  while remaining>1e-12:
   h=min(remaining,.005)
   running=self.phase=='running';stage=self.stage
   if running and stage=='preparing':h=min(h,self._prelude_remaining)
   elif running and stage=='scanning':h=min(h,self._grid[self._point_index][1]-self._pending_dt)
   elif running and stage=='degassing':h=min(h,self._degas_duration-self.elapsed_s)
   if not self.plant:self.time_s+=remaining;remaining=0.;break
   mean=self._physical(h);self.time_s+=h;remaining-=h
   if running and self.phase=='running':
    self.elapsed_s+=h
    if stage=='preparing':
     self._prelude_remaining-=h
     if self._prelude_remaining<1e-12:self.stage='scanning'
    elif stage=='scanning':
     self._run_pressure_min=min(self._run_pressure_min,self._pressure());self._run_pressure_max=max(self._run_pressure_max,self._pressure());factor=(self.method['emission_current_a']/.001) if self.filament else 0.;rate=sum(mean[s]*self._basis_cache[s][self._point_index] for s in mean if s in self._basis_cache)*factor;self._pending+=rate*h;self._pending_dt+=h
     if self._grid[self._point_index][1]-self._pending_dt<1e-12:self._complete_point()
     if self._point_index==len(self._grid):self._finish_scan('succeeded')
    elif stage=='degassing' and self.elapsed_s>=self._degas_duration-1e-10:self._stop('succeeded','')
  self.revision+=1;return self.read('evaluator')
 def _operation_snapshot(self):
  result=dict(degas_elapsed_s=self.elapsed_s,duration_s=self._degas_duration,normal_emission_restored=self.filament) if self.stage=='degassing' else self._result()
  return dict(operation_id=self.operation_id,status=self.phase,stage=self.stage,progress=self._progress(),result=result,error=None if self.phase!='failed' else self.warning)
 def _stop(self,status,reason):
  if self.stage=='degassing':
   self.phase=status;self.filament=bool(self._degas_restore and status!='failed' and self.powered);self.emission=self.method['emission_current_a'] if self.filament else 0.;self.energy_v=self.method['electron_energy_v'] if self.filament else 0.;self.degas_power=0.;self.warning=reason;self._operation_records[self.operation_id]=self._operation_snapshot()
  else:
   self._complete_point(partial=True)
   if reason:self._quality.append(reason)
   self.warning=reason;self._finish_scan(status)
 def _empty_result(self):return dict(record_id='',kind=self.sample_kind,status=self.phase,complete=False,points=[],reported_peaks=[],species=[],total_partial_pressure_pa=None,relative_residual=None,matrix_rank=0,condition_number=None,acquisition_signature='',method_json='{}',provenance_json='{}',quality=['no_completed_scan'])
 def _result(self):return copy.deepcopy(self._result_cache) if self._result_cache else self._empty_result()|dict(points=copy.deepcopy(self.points),reported_peaks=copy.deepcopy(self.peaks),status=self.phase)
 def _observation_space(self,record,species):
  mode=json.loads(record['method_json'])['mode'];points=record['points'];raw=np.column_stack([self._shape(q,[x['nominal_mass_to_charge'] for x in points]) for q in species])
  if mode=='analog':return np.array([x['current_a'] for x in points]),raw,[[i] for i in range(len(points))],raw
  groups=[]
  for peak in record['reported_peaks']:
   idx=[i for i,x in enumerate(points) if x['cycle']==peak['cycle'] and peak['start_s']-1e-10<=x['start_s'] and x['end_s']<=peak['end_s']+1e-10]
   if len(idx)!=7:raise ActionRejected('INVALID_REPORTED_PEAK_WINDOW')
   groups.append(idx)
  # Only native maximum outputs are observations. Seven hidden subcurrents never add fit rows.
  values=np.array([x['current_a'] for x in record['reported_peaks']]);max_profiles=np.array([raw[idx].max(axis=0) for idx in groups])
  return values,max_profiles,groups,raw
 def _inverse_observations(self,record,blank,candidates,curves):
  observed,profile,groups,raw=self._observation_space(record,candidates);bvals,_,_,_=self._observation_space(blank,candidates);y=observed-bvals;slopes=np.array([curves[q]['sensitivity_a_pa'] for q in candidates]);intercepts=np.array([curves[q]['intercept_a'] for q in candidates]);basis=profile*slopes;rank=0;condition=None
  def solve(matrix,adjusted):
   norm=np.linalg.norm(matrix,axis=0)
   if np.any(norm<=1e-30):return None,0,None
   scaled=matrix/norm;rank=int(np.linalg.matrix_rank(scaled));cond=float(np.linalg.cond(scaled));cond=cond if math.isfinite(cond) else None
   if rank<len(candidates) or cond is None or cond>self.plant['matrix_condition_max']:return None,rank,cond
   return np.linalg.lstsq(scaled,adjusted,rcond=None)[0]/norm,rank,cond
  inferred,rank,condition=solve(basis,y-profile@intercepts)
  if inferred is None:return None,rank,condition,None,'ambiguous_candidate_matrix'
  mode=json.loads(record['method_json'])['mode'];stable=False
  if mode=='analog':prediction=basis@inferred+profile@intercepts;stable=True
  else:
   # Public piecewise-linear max-of-summed-candidate response, never sum-of-pure-gas maxima.
   for _ in range(30):
    coefficients=slopes*inferred+intercepts;active=[idx[int(np.argmax(raw[idx]@coefficients))] for idx in groups];active_profile=raw[active];next_values,rank,condition=solve(active_profile*slopes,y-active_profile@intercepts)
    if next_values is None:return None,rank,condition,None,'ambiguous_candidate_matrix'
    new_coeff=slopes*next_values+intercepts;next_active=[idx[int(np.argmax(raw[idx]@new_coeff))] for idx in groups];inferred=next_values
    if next_active==active:stable=True;break
   prediction=np.array([np.max(raw[idx]@(slopes*inferred+intercepts)) for idx in groups])
  if not stable:return None,rank,condition,None,'peak_lock_solution_unstable'
  relative=float(np.linalg.norm(y-prediction)/max(np.linalg.norm(y),1e-30));return inferred,rank,condition,relative,None
 def _finish_scan(self,status):
  self.phase=status;self._record_counter+=1;rid=f'record_{self._record_counter}';quality=list(dict.fromkeys(self._quality));complete=status=='succeeded';candidates=self._run_method['candidates'];rows=[dict(species_id=s,partial_pressure_pa=None,mass_concentration_kg_m3=None,fitted_partial_pressure_pa=None,quality=[]) for s in candidates];rank=0;cond=residual=total=None
  if not complete:quality.append('incomplete_scan')
  if not self._run_emission:quality.append('filament_off_dark_scan')
  if self._run_pressure_max>LINEAR_MAX_PA:quality.append('above_Faraday_linear_partial_pressure_domain')
  if self._run_pressure_max-self._run_pressure_min>self.plant['stability_fraction']*max(self._run_pressure_max,FC_MIN_PA):quality.append('host_pressure_changed_during_scan')
  provenance=dict(evidence='synthetic_EI_Faraday_observations',reference_shapes='full-source base-peak normalization; source electron energies unknown and explicitly accepted',mass_to_charge_conversion=dict(atomic_mass_constant_kg=U_KG,elementary_charge_c=E_C,reference='fixed CODATA2022; nominal m/z already divides charge, not necessarily singly charged atoms'),pressure_feedback='independent ideal host gauge, not native RGA total-current gauge',noise_model='NF average source SD multiplied by explicit host scale; no individual device calibration or LOD sigma claim',miniscan_subpoints='synthetic internal telemetry; native firmware sends seven-point maximum only',ionization_response='fixed declared EI shape and host sensitivity at this method; no measured energy/focus cross-section function',unmodeled_species='unselected candidates may remain unresolved; low residual is not a completeness proof',host_pressure_min_pa=self._run_pressure_min,host_pressure_max_pa=self._run_pressure_max)
  if complete and self._run_kind=='blank' and not quality:self._blanks[self._run_key]=dict(record_id=rid,points=copy.deepcopy(self.points),reported_peaks=copy.deepcopy(self.peaks),method_json=json.dumps(self._run_method),acquisition_signature=self._run_key)
  if self._run_kind=='standard' and self._run_standard:self._standard_records[rid]=copy.deepcopy(self._run_standard)
  if self._run_kind=='sample':
   blank=self._run_blank
   if not blank:quality.append('blank_missing')
   if not candidates:quality.append('candidates_not_declared')
   if any(s not in self._run_curves for s in candidates):quality.append('calibration_missing_or_signature')
   if not quality:
    temporary=dict(points=self.points,reported_peaks=self.peaks,method_json=json.dumps(self._run_method));inferred,rank,cond,residual,inverse_error=self._inverse_observations(temporary,blank,candidates,self._run_curves)
    if inverse_error:quality.append(inverse_error)
    if residual is not None and residual>self.plant['fit_relative_residual_max']:quality.append('unexplained_signal_residual')
    if inferred is not None:
     for row,s,value in zip(rows,candidates,inferred):
      value=float(value);row['fitted_partial_pressure_pa']=value;c=self._run_curves[s];qc=[]
      if value<0:qc.append('negative_unconstrained_solution')
      if value<max(c['min_pressure_pa'],FC_MIN_PA) or value>min(c['max_pressure_pa'],LINEAR_MAX_PA):qc.append('outside_observed_calibration_or_Faraday_range')
      row['quality']=qc
      if not qc and not quality:row['partial_pressure_pa']=value;row['mass_concentration_kg_m3']=value*self._molar(s)/(R_GAS*self.plant['temperature_k'])
     if all(r['partial_pressure_pa'] is not None for r in rows):total=sum(r['partial_pressure_pa'] for r in rows)
  provenance['analysis_observation_space']='native analog windows' if self._run_method['mode']=='analog' else 'native reported peak maxima only; seven subcurrents are not independent analysis observations'
  provenance['analysis_observation_count']=len(self.points) if self._run_method['mode']=='analog' else len(self.peaks)
  result=dict(record_id=rid,kind=self._run_kind,status=status,complete=complete,points=copy.deepcopy(self.points),reported_peaks=copy.deepcopy(self.peaks),species=rows,total_partial_pressure_pa=total,relative_residual=residual,matrix_rank=rank,condition_number=cond,acquisition_signature=self._run_key,method_json=json.dumps(self._run_method,sort_keys=True),provenance_json=json.dumps(provenance,sort_keys=True),quality=list(dict.fromkeys(quality)));validate(RESULT,result);self._result_cache=result;self._records[rid]=copy.deepcopy(result);self._operation_records[self.operation_id]=self._operation_snapshot()
 def _fit(self,a):
  if not a['curve_id']:raise ActionRejected('INVALID_ARGUMENT')
  if a['curve_id'] not in self._curves and len(self._curves)>=MAX_CURVES:raise ActionRejected('CURVE_CAPACITY')
  if len(set(a['record_ids']))!=len(a['record_ids']):raise ActionRejected('DUPLICATE_RECORD')
  blank=self._blank()
  if not blank:raise ActionRejected('BLANK_REQUIRED')
  species=a['species_id'];self._ei(species);press=[];amps=[];ids=[];projections=[]
  for rid in a['record_ids']:
   r=self._records.get(rid);standard=self._standard_records.get(rid)
   if not r or not standard:raise ActionRejected('ACTUAL_STANDARD_RECORD_REQUIRED')
   if r['acquisition_signature']!=self._key():raise ActionRejected('CALIBRATION_SIGNATURE')
   if standard['species_id']!=species or r['quality'] or not r['complete']:raise ActionRejected('STANDARD_QUALITY')
   obs,basis,_,_=self._observation_space(r,[species]);bobs,_,_,_=self._observation_space(blank,[species]);y=obs-bobs;profile=basis[:,0];den=float(profile@profile)
   if den<=1e-20:raise ActionRejected('NO_OBSERVABLE_PEAK')
   amp=float(profile@y/den);relative=float(np.linalg.norm(y-profile*amp)/max(np.linalg.norm(y),1e-30))
   if relative>self.plant['fit_relative_residual_max']:raise ActionRejected('STANDARD_SPECTRAL_MISMATCH')
   press.append(standard['assigned_pressure_pa']);amps.append(amp);ids.append(rid);projections.append(relative)
  if len(set(press))<3 or min(press)<=0 or max(press)>LINEAR_MAX_PA:raise ActionRejected('CALIBRATION_PRESSURE_RANGE')
  x=np.array(press);y=np.array(amps);scale=max(x);coef=np.linalg.lstsq(np.column_stack([x/scale,np.ones(len(x))]),y,rcond=None)[0];slope=float(coef[0]/scale);intercept=float(coef[1]);pred=slope*x+intercept;ss=float(np.sum((y-y.mean())**2));r2=1-float(np.sum((y-pred)**2))/ss if ss>0 else 0.
  if slope<=0 or r2<.995:raise ActionRejected('CALIBRATION_FIT_INVALID')
  curve=dict(curve_id=a['curve_id'],species_id=species,sensitivity_a_pa=slope,intercept_a=intercept,r_squared=r2,min_pressure_pa=min(press),max_pressure_pa=max(press),record_ids=ids,pressures_pa=press,observed_amplitudes_a=amps,spectral_relative_residuals=projections,acquisition_signature=self._key(),pressure_reference='explicit host prepared amounts/V/T, no source-certified pressure assignment',evidence='actual synthetic current observations fitted; EI source shape is real reference, absolute sensitivity is conditioned');self._curves[a['curve_id']]=curve;return {k:curve[k] for k in ['curve_id','sensitivity_a_pa','r_squared']}

class StanfordRga100Session:
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
        m=export_stanford_rga100_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        def timing(key):
            if key in ['mass.current_ratio','mass.current_kg_c','detector.current_a','detector.last_sample_time_s'] and h._last_point:return h._last_point['end_s'],[h._last_point['start_s'],h._last_point['end_s']]
            if key.startswith('result.') and h.points:return h.points[-1]['end_s'],[h.points[0]['start_s'],h.points[-1]['end_s']]
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
'RGA100 standard Faraday cup, nominal m/z1–100 relative to u/e. Result separately converts m/z to kg/C with cached CODATA2022 u=1.66053906892e-27kg and exact e=1.602176634e-19C. A fragment at14 can be a different ion or a multiply charged ion; the model never interprets nominal m/z as the mass of a neutral molecule. Electron multiplier hardware, native binary transport and native unfiltered total-ion-current pressure mode are not implemented.',
'Full reference EI spectra retain source base-peak normalization and all original peaks. Contributions outside1–100 are excluded without renormalizing. Original electron energy, source geometry, isotope fragmentation and actual instrument-specific transmission are not established for these records. Host acceptance of unknown conditions is mandatory. A fixed reference shape with explicit host sensitivity and a Gaussian quadrupole profile is a conditioned numerical model, not calibrated fragmentation at any25–105V electron energy or focus setting. Same source species can share unresolved nominal masses.',
'FCN2 sensitivity2e-4A/Torr at70V electrons/12V ions/1mA emission/1u width at10percent height is a source anchor only. Different species gains, focus/energy dependencies and absolute transfer are unknown host assumptions. The source better-than0.5u resolution specification has different conditions. FCminimum5e-11Torr is not noise sigma. Quantification is restricted to actual observed standard interpolation and the source1e-5Torr linear domain. The1e-4Torr operating threshold is a distinct protection limit.',
'NF0–7 durations use the source table: analogseconds/u divided by10–25points/u; inclusive endpoints add the final actual window. Histogram/MID perform7 actual subwindows at±0.3u and return the maximum for each completed group. Equal subdivision of total single-mass time is an engineering assumption; firmware settling allocation is unknown. Exported miniscan subpoints are additional synthetic model telemetry, not actual native RS232 output. Histogram/MID calibration and inference use only the reported maximum per group. A public max-of-summed-candidate forward model and its active-peak Jacobian handle mixture nonlinearity; seven internal currents do not increase observable rank. Analog uses its actual reported windows. Finite cycles only; continuous scans remain unsupported.',
'Noise uses source average reference baseline SD at eachNF multiplied by an explicit host scale; source averages do not calibrate a particular unit. A single random draw is applied only to a completed or cancelled partial window. Applying a scaled table SD to individual miniscan subwindows is an explicit synthetic noise assumption, not the original instrument internal-noise allocation. All source current responses and time arrays are synthetic; no OEM electronic raw data exist here. The high-level typed protocol intentionally permits pure queries during scans, unlike native firmware commands that interrupt streaming. No claim of native timing, binary ADC quantization,675Hz electronics protection, RS232 throughput or real instrument validation.',
'An internal zero is the explicit host electronic dark offset under no-input electronics; it is not a gas blank. Analog/histogram autozero, while MID uses the retained zero. Separate actual gas-blank scans and at least3 distinct observed pure-gas pressure-standard scans fit eachA/Pa sensitivity. Standard pressures are host-prepared from declared mol,V,T, not measured or certified pressures from the EI library. Blank/signature, source library and public response settings match. Candidate species are public and independent of hidden composition. Rank/conditioning with an explicit host condition-number ceiling (default1000; conservative numerical identifiability rather than a factory specification), residual, negative/unobserved range, detector saturation or drifting pressure cause honest null results.',
'Total inferred partial pressure sums only declared, calibrated candidates when all pass QC; it does not prove unknown-gas completeness. Mass concentration is inferred partial pressure times source molar mass divided by hostR*T. Source molar masses and EI intensities are references, not concentration measurements. Underlying unconstrained least-squares values are explicit diagnostics even when scientific output is null, with no silent negative clipping.',
'Host chamber is an explicit ideal-gas vessel, not a built-in RGA pump. Independent host pressure feedback is ideal and must not be presented as native RGA identified total pressure. Trusted batch exchange moves prior residual gas to a named conceptual host-return inventory and registers new finite mol amounts; no RGA gas-sampling inlet is invented. External gate and pump cause finite species transport. Degas transfers finite adsorbed species into the chamber. Chambers,pumped gas,returned gas and adsorbed inventories close per-species mol and mass ledgers. The Faraday diagnostic is treated as non-perturbing at these low sampled ion currents: ion extraction/neutralization and fragment-specific consumption are not modeled or claimed as a charge-to-parent-gas balance. No chemical conversion, atmospheric sampling, reactor reaction kinetics or uncontrolled gas leak identification is claimed.',
'Degas has actualsource400V and1–20mA emission over60s, then constant20mA to1–20minutes. Its8W electron-beam power is not whole-system input power or an independent arbitrary1–10W setting. Finite desorption mol/J, thermal capacity and heat rejection are host assumptions. Cancellation retains released gases, pumping and heat and restores pre-degas normal emission only if safe. The scene demonstrates early degassing and cancellation instead of mislabeling an accelerated complete manufacturer cycle.',
'Normal emission0–3.5mA and ion energy8/12V are separate from the400V/20mA degas exception. Overpressure at1e-4Torr stops emission and running acquisitions, preserving partial observations and inventories. Opening host valves and finite pumping are external operations. Cold power-off ECU withdrawal is host-supported engineering motion, not an OEM motor. Cold323.15K example is an engineering threshold; source ECU operating70C and probe bake300C withoutECU are distinct, and bake is not implemented.',
'Eight visible source LEDs have limited modeled semantics. Power requires explicit24V supply within22–26V. Filament also lights during degas, EM is false for standardFC. Overpressure and declared-host filament integrity are modeled, not full filament aging/Thoria diagnostics. Error covers this model only. RS232activity is null because high-level API traffic does not establish native TX/RX. No scan-busy impersonation of theRS232LED. External six numeric regions are simulation-only and hidden in product views, not an original LCD.',
'All physical motion, gas transport, heat and acquisition windows advance only in step. Parameter and host-transfer actions are atomic at the command boundary; pure read/describe do not advance time. Session role checks, same-request deduplication and owner-bound operation receipts are process-local guarantees, not OS isolation, persistence or physical robot/hardware evidence. Scene,geometry,light-material and native bridge evidence must come from separate actual reports.'
]

def export_stanford_rga100_contract_metadata():
 caps={};slots={};motion=['ecu.withdrawal_m','host.valve_lift_m'];process=['scan.phase','scan.stage','scan.elapsed_s','scan.progress'];observed=['mass.current_ratio','mass.current_kg_c','detector.current_a','detector.last_sample_time_s'];physical=['vacuum.pressure_pa','ionizer.temperature_k','filament.emission_current_a','ionizer.electron_energy_v','degas.power_w','truth.ledger'];leds=[k for k in SLOTS if k.startswith('led.')];end=[*process,*observed,'result.total_partial_pressure_pa','storage.result_count','safety.warning','blank.valid',*leds];ready=['instrument.ready','calibration.ready','blank.valid']
 immediate={'host.filament_fault':['safety.warning',*leds],'plant.configure':['plant.configured',*physical,*ready,*leds],'power.set':['power.enabled','filament.enabled',*end,*physical,'instrument.ready'],'ecu.set':['ecu.target_m','instrument.ready'],'vacuum.valve':['host.valve_target_m'],'vacuum.pump':['host.pump_enabled'],'sample.load':['vacuum.pressure_pa','truth.ledger','instrument.ready'],'sample.unload':['vacuum.pressure_pa','truth.ledger','instrument.ready'],'method.configure':['method.mode','method.noise_floor',*ready],'filament.set':['filament.enabled','safety.warning',*leds],'detector.zero':['detector.zero_current_a'],'scan.start':[*process,*observed,'detector.zero_current_a','result.total_partial_pressure_pa','safety.warning',*leds],'degas.start':[*process,'safety.warning',*leds],'measurement.stop':[*end,*physical,'filament.enabled'],'calibration.fit':['storage.curve_count'],'calibration.select':['calibration.ready'],'records.remove':['storage.result_count']}
 onstep={'ecu.set':['ecu.withdrawal_m','instrument.ready'],'vacuum.valve':['host.valve_lift_m',*physical,*end,'filament.enabled','instrument.ready'],'vacuum.pump':[*physical,*end,'filament.enabled','instrument.ready'],'power.set':[*physical,*leds],'filament.set':[*physical,*leds],'scan.start':[*process,*observed,*physical,*leds,'result.total_partial_pressure_pa','blank.valid','storage.result_count','safety.warning','filament.enabled'],'degas.start':[*process,*physical,*leds,'filament.enabled','safety.warning'],'measurement.stop':[*physical,*leds]}
 observes={'plant.configure':['plant.configured'],'power.set':['power.enabled'],'ecu.set':['ecu.target_m'],'vacuum.valve':['host.valve_target_m'],'vacuum.pump':['host.pump_enabled'],'filament.set':['filament.enabled'],'detector.zero':['detector.zero_current_a'],'scan.start':['scan.phase'],'degas.start':['scan.phase'],'measurement.stop':['scan.phase'],'process.measure':[*process,'detector.current_a','mass.current_ratio'],'result.read':['result.total_partial_pressure_pa'],'spectrum.read':observed,'blank.read':['blank.valid'],'records.list':['storage.result_count','storage.curve_count'],'product.read':['truth.ledger']}
 for k,(s,u,origin,vis,parts,write) in SLOTS.items():
  mode='interval_mean' if k=='detector.current_a' else 'instant'
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=parts,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=origin,visibility=vis,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'StanfordRga100Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode=mode,time_domain='simulation',window_description='Actual completed or partial-cancel Faraday integration window; query clock is not sample time.' if mode!='instant' else None),source_ids=['simulation_assumption'])
 errors=sorted({n.args[0].value for n in ast.walk(ast.parse(Path(__file__).read_text())) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='ActionRejected' and n.args and isinstance(n.args[0],ast.Constant) and isinstance(n.args[0].value,str)})
 for k,(inp,out,parts,label,kind,vis,example) in CAPS.items():
  unsupported=k in UNSUPPORTED;async_=k in ['scan.start','degas.start'];effects=[dict(state_id=s,description_zh=SLOT_ZH[s],timing=t) for t,mapping in [('immediate',immediate),('on_step',onstep)] for s in dict.fromkeys(mapping.get(k,[]))]
  caps[k]=dict(function_zh=label,kind=kind,visibility=vis,component_ids=parts,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation={layer:('implemented' if layer in ['process','host'] and not unsupported else 'absent') for layer in ['process','scene','robot','hardware','host']},binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.stanford_rga100.StanfordRga100Session.invoke',invocation=f'session.invoke(instance_id="rga100_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if kind=='query' or unsupported else [dict(description_zh='由入口校验独立真空宿主、ECU/灯丝/电压、公开谱形条件、实际观察标线/候选矩阵与有限缓存',state_ids=['power.enabled','instrument.ready','scan.phase'],enforced_by='runtime',check='Runtime._invoke/_start_scan/_fit and authenticated Session')],state_effects=[] if unsupported or kind=='query' else effects,observes=[] if unsupported else observes.get(k,[]),model_effects=dict(mode='none',description_zh='需要独立实际几何与场景绑定；离子筛选与电流不是机械转动',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='全部真实NF窗口完成，或源定义完整脱气时长；取消保留部分观测和气体/热' if async_ else '原子返回；有限运动和过程仅由step推进',completion_state_ids=['scan.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','host_vacuum_chamber','faraday_detector','ionizer','filament','quadrupole'],partial_failure_zh='保留已泵出及退气库存、热和实际部分电流窗口；不恢复原气体'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in errors],example_arguments=copy.deepcopy(example),verification=[],limitations=LIMITATIONS,source_ids=['manufacturer_manual','simulation_assumption'])
 mech=dict(component_ids=['host_chamber','host_gate','ionizer','filament','quadrupole','faraday_cup'],state_ids=list(slots),meaning_zh='有限宿主真空库存、EI参考峰混合和真实Faraday窗口/观察标线分压反演',implementation='lumped',equations=['P=sum(n_i)*R*T/V; dn_i/dt=r_desorb_i-S_eff*n_i/V; registered=chamber+adsorbed+pumped+returned.','I(m,t)=emission/1mA * sum(P_i(t)*host_gain_i*sum(reference_peak_j*Gaussian(m-mz_j))); fixed explicit width at10percent.','Each actual current window integrates I(t)dt/window_dt; each completed seven-point miniscan reports max(I_k).','Pressure-standard amplitudes come from native analog windows or reported histogram/MID maxima and matched observed blank; ≥3 distinct host-prepared pressures fit sensitivityA/Pa.','Public candidate basis SVD/rank and observed least-squares residual precede calibrated partial-pressure interpolation; no hidden-composition decision.','m/z_SI=(nominal m/z)*CODATA2022 atomic_mass_constant/elementary_charge, kg/C.','Degas E=400V, emission1–20mA over60s, beamP=E*I; finite desorption stock. Electrical energy=thermal stored+rejected heat.'],parameters={},source_ids=['manufacturer_manual','simulation_assumption'],limitations=LIMITATIONS)
 spec=importlib.util.spec_from_file_location('_rga100_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'vacuum_ei_faraday':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE)})
 return dict(capabilities=caps,state_slots=slots,mechanisms={'vacuum_ei_faraday':mech})

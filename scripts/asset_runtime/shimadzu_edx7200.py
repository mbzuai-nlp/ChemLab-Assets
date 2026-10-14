"""EDX-7200: nondestructive conditional XRF spectrum, observed calibration and optional atmosphere."""
from __future__ import annotations
import ast,copy,csv,hashlib,importlib.util,io,json,math
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2];SLUG='shimadzu_edx7200';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-084';PROTOCOL_VERSION=ASSET_VERSION='1.0.0'
TEMPLATE_REL='assets/expansion_batch04/t3_analytical/shimadzu_edx7200/output_templates/library.json'
E_CHARGE=1.602176634e-19;R=8.31446261815324;MAX_RESULTS=256;MAX_CURVES=32;MAX_WINDOWS=2000
FILTERS=['OPEN','F1','F2','F3','F4','F5'];APERTURES=[.001,.003,.005,.010]
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
PLANT=obj(dict(seed=integer(0,2**32-1),optional_vacuum_installed=BOOL,optional_helium_installed=BOOL,ambient_pressure_pa=number('Pa',8e4,12e4),ambient_temperature_k=number('K',278.15,308.15),chamber_volume_m3=number('m^3',1e-4,.01),vacuum_target_pa=number('Pa',10,10000),vacuum_tau_s=number('s',.05,100),vent_tau_s=number('s',.05,100),helium_supply_mol=number('mol',0,1000),helium_flow_m3_s=number('m^3/s',0,.01),voltage_ramp_v_s=number('V/s',1,1e7),current_ramp_a_s=number('A/s',1e-8,1),selector_speed_m_s=number('m/s',.001,1),detector_target_k=number('K',220,280),detector_tau_s=number('s',.05,100),line_rate_s_inv=number('s^-1',1,1e8),background_rate_s_inv=number('s^-1',0,1e6),air_optical_depth_at_1kev=number('1',0,100),filter_transmissions=arr(number('1',.001,1),6,6),fwhm_j=number('J',20*E_CHARGE,1000*E_CHARGE),dead_time_s=number('s',0,1e-4),max_count_rate_s_inv=number('s^-1',1e3,1e9),poisson_counts=BOOL,xray_energy_fraction=number('1',1e-6,.5),sample_absorbed_fraction=number('1',0,1)))
PLANT_EXAMPLE=dict(seed=840,optional_vacuum_installed=False,optional_helium_installed=False,ambient_pressure_pa=101325.,ambient_temperature_k=298.15,chamber_volume_m3=.005,vacuum_target_pa=1000.,vacuum_tau_s=.2,vent_tau_s=.15,helium_supply_mol=20.,helium_flow_m3_s=.01,voltage_ramp_v_s=1e5,current_ramp_a_s=.001,selector_speed_m_s=.1,detector_target_k=253.15,detector_tau_s=.2,line_rate_s_inv=2e6,background_rate_s_inv=100.,air_optical_depth_at_1kev=2.,filter_transmissions=[1.,.85,.7,.55,.4,.25],fwhm_j=150.*E_CHARGE,dead_time_s=1e-8,max_count_rate_s_inv=1e7,poisson_counts=False,xray_energy_fraction=.005,sample_absorbed_fraction=.4)
METHOD=obj(dict(voltage_v=number('V',4000,50000),current_a=number('A',1e-6,1e-3),selected_lines=arr(TEXT,1,12),energy_max_j=number('J',5000*E_CHARGE,50000*E_CHARGE),bins=integer(256,4096),integration_s=number('s',.02,200),window_s=number('s',.01,10),repeats=integer(1,20),deadtime_correction=BOOL))
METHOD_EXAMPLE=dict(voltage_v=50000.,current_a=.0001,selected_lines=['co_ka1','cu_ka1','ni_ka1'],energy_max_j=50000.*E_CHARGE,bins=2048,integration_s=.5,window_s=.1,repeats=1,deadtime_correction=True)
SAMPLE=obj(dict(template_id=TEXT,kind=enum(['sample','standard','blank']),mass_kg=number('kg',1e-6,5),dimensions_m=arr(number('m',1e-5,.3),3,3),phase=enum(['solid','powder','liquid']),sealed_cup=BOOL,reference_fraction=number('1',.000001,1),matrix_factor=number('1',.001,100),accept_conditional_response=BOOL,accept_geometric_clearance_assumption=BOOL))
SAMPLE_EXAMPLE=dict(template_id='blackmass_ec_a',kind='sample',mass_kg=.01,dimensions_m=[.02,.02,.01],phase='powder',sealed_cup=True,reference_fraction=1.,matrix_factor=1.,accept_conditional_response=True,accept_geometric_clearance_assumption=True)
STANDARD_EXAMPLE=SAMPLE_EXAMPLE|dict(template_id='blackmass_icp_a',kind='standard')
BLANK_EXAMPLE=SAMPLE_EXAMPLE|dict(template_id='',kind='blank')
WINDOW=obj(dict(start_s=number('s',0),end_s=number('s',0),counts=arr(integer(0,2**53),256,4096),count_rate_s_inv=number('s^-1',0),live_time_s=number('s',0),saturated=BOOL))
LINE_OBS=obj(dict(line_id=TEXT,net_rate_s_inv=nullable(number('s^-1')),roi_low_j=number('J',0),roi_high_j=number('J',0)))
FRACTION=obj(dict(line_id=TEXT,element=TEXT,mass_fraction=nullable(number('1')),standard_deviation=nullable(number('1',0)),quality=TEXT))
ASSAY=obj(dict(response_profile_json=TEXT,windows=arr(WINDOW,0,MAX_WINDOWS),line_observations=arr(LINE_OBS,0,12),counts=arr(integer(0,2**53),256,4096),elapsed_s=number('s',0),complete=BOOL))
RESULT=obj(dict(phase=TEXT,complete=BOOL,energy_edges_j=arr(number('J',0),257,4097),assays=arr(ASSAY,0,20),active_assay=nullable(ASSAY),mass_fractions=arr(FRACTION,0,12),qc=arr(TEXT,0,100),acquisition_method_json=TEXT,acquisition_signature=TEXT,evidence=TEXT,template_source=TEXT))
CAL_OBSERVATION=obj(dict(acquisition_signature=TEXT,reference_id=TEXT,reference_fraction=number('1',.000001,1),line_observations=arr(LINE_OBS,1,12)))
LEDGER=obj(dict(sample_registered_kg=number('kg',0),sample_loaded_kg=number('kg',0),sample_returned_kg=number('kg',0),sample_mass_residual_kg=number('kg'),element_ledger_json=TEXT,gas_ledger_json=TEXT,gas_residual_mol=number('mol'),helium_supply_remaining_mol=number('mol',0),tube_electrical_energy_j=number('J',0),xray_energy_j=number('J',0),sample_absorbed_energy_j=number('J',0),escaped_energy_j=number('J',0),tube_loss_energy_j=number('J',0),energy_residual_j=number('J')))
CAPS={}
def cap(k,inp,out,components,label,kind='device_command',visibility='agent',example=None):CAPS[k]=(inp,out,components,label,kind,visibility,example or {})
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body'],'可信宿主初始化数值响应和显式气氛选件','configuration','evaluator',PLANT_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_mount'],'装载无损样品；已知组成仅限宿主','device_command','evaluator',SAMPLE_EXAMPLE)
cap('sample.unload',obj({'recipient_id':TEXT}),obj({'returned_mass_kg':number('kg',0)}),['sample_mount'],'把未消耗样品完整返回宿主','device_command','evaluator',{'recipient_id':'host'})
cap('cover.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['sample_lid'],'可信宿主开盖立即停止辐射和采集；真空需先回填','device_command','evaluator',{'opened':False})
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['xray_tube','sdd_detector'],'设定电源；管压流及SDD冷却仅step推进',example={'enabled':True})
cap('method.configure',METHOD,obj({'configured':BOOL}),['xray_tube','sdd_detector'],'设置管压流、能量轴和完整积分窗；谱线必须满足壳层吸收边',example=METHOD_EXAMPLE)
cap('filter.select',obj({'filter_id':enum(FILTERS)}),obj({'selected':TEXT}),['filter_slide'],'OPEN+五未知材料滤位；有限选位，不捏造原厂透射曲线',example={'filter_id':'OPEN'})
cap('collimator.select',obj({'diameter_m':number('m',.001,.010)}),obj({'selected_diameter_m':number('m')}),['collimator_slide'],'选择1/3/5/10mm样品照射斑；准直片实体孔径未知，有限工程选位',example={'diameter_m':.010})
cap('atmosphere.set',obj({'mode':enum(['air','vacuum','helium'])}),obj({'mode':TEXT}),['sample_chamber'],'空气/显式安装选件的有限抽气、He置换、回填；真空液体拒绝',example={'mode':'air'})
cap('measurement.start',obj({}),obj({'operation_id':TEXT}),['xray_tube','sdd_detector','sample_mount'],'闭盖后非破坏能谱采集；只完整时间窗生成合成整数计数')
cap('measurement.stop',obj({}),obj({'phase':TEXT}),['xray_tube','sdd_detector'],'停止辐射，保留已完整采样窗口及全部样品')
cap('process.measure',obj({}),obj(dict(phase=TEXT,elapsed_s=number('s',0),progress=number('1',0,1),count_rate_s_inv=nullable(number('s^-1',0)),mass_fractions=arr(FRACTION,0,12))),['display_panel'],'纯读过程与实际观测推断','query')
cap('result.read',obj({}),RESULT,['display_panel'],'纯读当前结果，保留当时方法与谱数组','query')
cap('spectrum.read',obj({}),obj(dict(energy_edges_j=arr(number('J',0),257,4097),counts=arr(integer(0,2**53),256,4096),sample_window_s=nullable(arr(number('s',0),2,2)),evidence=TEXT)),['sdd_detector'],'同时能量通道的累计计数谱，非顺序质量扫描','query')
cap('spectrum.cursor',obj({'energy_j':number('J',0,50000*E_CHARGE)}),obj({'energy_j':number('J')}),['display_panel'],'设置能谱游标，不选择光子或改采集',example={'energy_j':8000*E_CHARGE})
cap('result.export',obj({'format':enum(['json','csv'])}),obj({'format':TEXT,'content':TEXT}),['display_panel'],'导出真实运行数组字符串，包含J和计数单位','query',example={'format':'json'})
cap('blank.read',obj({}),obj({'valid':BOOL,'blank_json':TEXT}),['display_panel'],'匹配响应方法的实际空白谱','query')
cap('calibration.fit',obj({'curve_id':TEXT,'observations':arr(CAL_OBSERVATION,3,20),'accept_same_matrix_response':BOOL}),obj({'curve_id':TEXT,'line_count':integer(1,12),'minimum_r_squared':number('1')}),['display_panel'],'真实标准参考+实际公开谱形分解净率拟合；同基体响应为显式接受前提','analysis',example={'curve_id':'observed','observations':[],'accept_same_matrix_response':True})
cap('calibration.select',obj({'curve_id':TEXT}),obj({'selected':TEXT}),['display_panel'],'选择全响应签名相容标线',example={'curve_id':'observed'})
cap('calibration.read',obj({'curve_id':TEXT}),obj({'curve_json':TEXT}),['display_panel'],'读取原始标准观测与拟合','query',example={'curve_id':'observed'})
cap('records.list',obj({}),obj({'record_ids':arr(TEXT,0,MAX_RESULTS),'curve_ids':arr(TEXT,0,MAX_CURVES)}),['display_panel'],'实例独立结果/标线目录','query')
cap('records.read',obj({'record_id':TEXT}),RESULT,['display_panel'],'读取冻结历史谱和方法','query',example={'record_id':'record_1'})
cap('records.remove',obj({'record_id':TEXT}),obj({'removed':BOOL}),['display_panel'],'删除指定缓存结果；异步历史仍受权限保护',example={'record_id':'record_1'})
cap('template.read',obj({'substance_id':TEXT}),obj({'template_json':TEXT}),['display_panel'],'纯读真实线/边与组成参考，未知响应分开','query',example={'substance_id':'cu_ka1'})
cap('product.read',obj({}),LEDGER,['sample_mount'],'仅可信宿主读取无损质量、气体和能量代理账本','query','evaluator')
UNSUPPORTED={'carousel.sequence','microcollimator.select','fundamental_parameters.quantify','hardware.connect'}
for k in sorted(UNSUPPORTED):cap(k,obj({}),obj({}),['body'],'未安装/未实现：'+k)
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,origin='derived',visibility='agent',components=('display_panel',),write=None):SLOTS[k]=(s,u,origin,visibility,list(components),write);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','宿主模型已配置');slot('sample.loaded',BOOL,'1','样品已装载',components=('sample_mount',));slot('power.enabled',BOOL,'1','电源目标','setpoint',components=('xray_tube',),write=('power.set','enabled'))
slot('cover.opened',BOOL,'1','样品盖已开启','scene',components=('sample_lid',));slot('cover.angle_rad',number('rad',0,1.48),'rad','宿主立即重定位的盖工程转角','scene',components=('sample_lid',))
slot('filter.id',enum(FILTERS),'1','当前目标滤位，材料未公开','setpoint',components=('filter_slide',),write=('filter.select','filter_id'));slot('filter.position_m',number('m',0,.060),'m','六滤位直条工程位移，仅step有限移动','scene',components=('filter_slide',))
slot('collimator.diameter_m',number('m',.001,.01),'m','样品照射斑直径目标；非实体准直片实测孔径','setpoint',components=('collimator_slide',),write=('collimator.select','diameter_m'));slot('collimator.position_m',number('m',0,.045),'m','四照射斑选位直条的工程位移','scene',components=('collimator_slide',))
for k,u,lo,hi,z,c in [('tube.actual_voltage_v','V',0,50000,'实际管压的有限宿主代理','xray_tube'),('tube.actual_current_a','A',0,.001,'实际管流的有限宿主代理','xray_tube'),('detector.temperature_k','K',220,310,'SDD电子冷却热惯性代理，非厂家温度标定','sdd_detector')]:slot(k,number(u,lo,hi),u,z,components=(c,))
slot('xray.emitting',BOOL,'1','采集中辐射使能，开盖即关；不是X射线剂量安全认证',components=('xray_tube',));slot('atmosphere.mode',enum(['air','vacuum','helium']),'1','气氛目标，He/真空必须显式安装','setpoint',components=('sample_chamber',),write=('atmosphere.set','mode'));slot('atmosphere.pressure_pa',number('Pa',0,120000),'Pa','腔体绝压宿主理想气体模型',components=('sample_chamber',));slot('atmosphere.helium_fraction',number('1',0,1),'1','腔内He摩尔分数代理',components=('sample_chamber',));slot('atmosphere.ready',BOOL,'1','达到方法的气氛阈值',components=('sample_chamber',));slot('instrument.ready',BOOL,'1','管压流、SDD和光路/气氛已满足采样要求')
slot('measurement.phase',TEXT,'1','当前测量执行状态');slot('measurement.elapsed_s',number('s',0),'s','当前测量累计时间');slot('measurement.progress',number('1',0,1),'1','完成积分时间占比');slot('measurement.completed_repeats',integer(0,20),'1','完整重复次数');slot('detector.count_rate_s_inv',nullable(number('s^-1',0)),'s^-1','最近完整同时谱窗口的总计数率',components=('sdd_detector',));slot('detector.last_sample_time_s',nullable(number('s',0)),'s','最近完整谱窗口终点',components=('sdd_detector',));slot('spectrum.current_energy_j',number('J',0,50000*E_CHARGE),'J','用户谱图游标，不代表顺序采样能量','setpoint',write=('spectrum.cursor','energy_j'));slot('result.mass_fraction',nullable(number('1')),'1','第一选线实际观测标线推断质量分数，未定义null');slot('result.mass_fractions',arr(FRACTION,0,12),'1','各选线推断质量分数与质量说明');slot('blank.valid',BOOL,'1','当前方法响应签名下空白可用');slot('calibration.ready',BOOL,'1','选定标线签名相容');slot('storage.result_count',integer(0,MAX_RESULTS),'1','过程内结果数，非OEM容量');slot('storage.curve_count',integer(0,MAX_CURVES),'1','过程内标线数，非OEM容量');slot('safety.warning',TEXT,'1','公开条件失败提示，无样品真值');slot('truth.ledger',LEDGER,'1','受权限保护的真实模型质量/气体/能量仓','process_truth','evaluator',('sample_mount',))

def _library():
 p=ROOT/TEMPLATE_REL
 return json.loads(p.read_text()) if p.exists() else {'lines':{},'compounds':{}}
def _values(record):
 values={k:float(v) for k,v in record.get('element_mass_fractions',{}).items() if v is not None}
 if not values or any(not math.isfinite(v) or v<0 or v>1 for v in values.values()) or sum(values.values())>1+1e-8:raise ActionRejected('REFERENCE_COMPOSITION_UNAVAILABLE')
 return values
class ShimadzuEdx7200Runtime:
 def __init__(self,instance_id='edx_01',library=None):
  self.instance_id=instance_id;self.library=copy.deepcopy(library) if library is not None else _library();self.lines=self.library.get('lines',{});self._line_library_sha=hashlib.sha256(json.dumps(self.lines,sort_keys=True).encode()).hexdigest();self.templates=self.library.get('compounds',{});self.plant=self.method=self.sample=None;self.time_s=0.;self.revision=0;self.powered=False;self.opened=False;self.filter_id='OPEN';self.filter_position=0.;self.diameter=.010;self.collimator_position=.045;self.voltage=self.current=0.;self.temperature=298.15;self.atmosphere='air';self.air_mol=self.he_mol=0.;self.initial_gas_mol=self.air_in_mol=self.he_in_mol=self.gas_out_mol=0.;self.air_out_mol=self.he_out_mol=0.;self.he_remaining=0.;self.registered_mass=self.returned_mass=0.;self.element_registered={};self.element_returned={};self.electrical_energy=self.xray_energy=self.absorbed_energy=self.escaped_energy=self.tube_loss=0.;self.phase='idle';self.elapsed_s=0.;self.warning='';self.cursor_j=8000*E_CHARGE;self.assays=[];self._active=None;self._concentrations=[];self._qc=[];self._edges=np.linspace(0,50000*E_CHARGE,2049);self._run_method=None;self._run_key=None;self._run_curve=None;self._run_blank=None;self._run_kind='';self._pending_dt=0.;self._last_point=None;self._spectrum_start=None;self._spectrum_end=None;self._rate=np.zeros(2048);self._pending_mu=np.zeros(2048);self._pending_coeff=None;self._assay_coeff=None;self._profile_history=[];self._correction=1.;self._saturated=False;self._blanks={};self._curves={};self._records={};self._selected='';self.operation_id='';self._op_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._operation_records={};self._rng=np.random.default_rng(0)
 def register_principal(self,principal_id,visibility='agent'):
  if not isinstance(principal_id,str) or not principal_id or visibility not in ['agent','debug','evaluator']:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('PRINCIPAL_EXISTS')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return ShimadzuEdx7200Session(self,principal_id)
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _reference(self,k):
  r=self.templates.get(k)
  if r is None:raise ActionRejected('UNKNOWN_REFERENCE')
  
  if r.get('physical_loading_eligible') is False:raise ActionRejected('REFERENCE_QUERY_ONLY')
  _values(r);return r
 def _line(self,k):
  q=self.lines.get(k)
  if q is None:raise ActionRejected('UNKNOWN_LINE')
  if not isinstance(q.get('energy_j'),(int,float)) or not isinstance(q.get('edge_energy_j'),(int,float)) or not 0<q['energy_j']<q['edge_energy_j']:raise ActionRejected('LINE_EDGE_UNAVAILABLE')
  if not 11<=q.get('atomic_number',0)<=92:raise ActionRejected('ELEMENT_RANGE')
  return q
 def _key(self):
  response={k:self.plant[k] for k in ['line_rate_s_inv','background_rate_s_inv','air_optical_depth_at_1kev','filter_transmissions','fwhm_j','dead_time_s','max_count_rate_s_inv','poisson_counts','ambient_pressure_pa','ambient_temperature_k']} if self.plant else None;response_sha=hashlib.sha256(json.dumps(response,sort_keys=True).encode()).hexdigest()
  return json.dumps(dict(response_sha256=response_sha,line_library_sha256=self._line_library_sha,method={k:v for k,v in (self.method or {}).items() if k not in ['repeats','integration_s','window_s']},filter=self.filter_id,diameter_m=self.diameter,atmosphere=self.atmosphere),sort_keys=True)
 def _blank_valid(self):return self._key() in self._blanks
 def _calibration_ready(self):return self._selected in self._curves and self._curves[self._selected]['key']==self._key()
 def _pressure(self):return (self.air_mol+self.he_mol)*R*self.plant['ambient_temperature_k']/self.plant['chamber_volume_m3'] if self.plant else 101325.
 def _he_fraction(self):return self.he_mol/max(self.air_mol+self.he_mol,1e-300)
 def _atmosphere_ready(self):
  if not self.plant:return False
  p=self._pressure();ambient=self.plant['ambient_pressure_pa']
  return p<=1.01*self.plant['vacuum_target_pa'] if self.atmosphere=='vacuum' else abs(p/ambient-1)<.005 and (self._he_fraction()>=.99 if self.atmosphere=='helium' else self._he_fraction()<.001)
 def _ready(self):
  if not self.plant or not self.method or not self.powered or self.opened:return False
  return abs(self.voltage-self.method['voltage_v'])<.1 and abs(self.current-self.method['current_a'])<1e-9 and self.temperature<=self.plant['detector_target_k']+1 and abs(self.filter_position-FILTERS.index(self.filter_id)*.012)<1e-8 and abs(self.collimator_position-APERTURES.index(self.diameter)*.015)<1e-8 and self._atmosphere_ready()
 def _ledger(self):
  loaded=self.sample['mass'] if self.sample else 0.;e={}
  for k,v in self.element_registered.items():
   held=self.sample['mass']*self.sample['values'].get(k,0.) if self.sample else 0.;ret=self.element_returned.get(k,0.);e[k]=dict(registered_kg=v,loaded_kg=held,returned_kg=ret,residual_kg=v-held-ret)
  total=self.air_mol+self.he_mol;gas=dict(initial_air_mol=self.initial_gas_mol,air_entered_mol=self.air_in_mol,helium_entered_mol=self.he_in_mol,chamber_air_mol=self.air_mol,chamber_helium_mol=self.he_mol,air_exhausted_mol=self.air_out_mol,helium_exhausted_mol=self.he_out_mol,air_residual_mol=self.initial_gas_mol+self.air_in_mol-self.air_mol-self.air_out_mol,helium_residual_mol=self.he_in_mol-self.he_mol-self.he_out_mol)
  return dict(sample_registered_kg=self.registered_mass,sample_loaded_kg=loaded,sample_returned_kg=self.returned_mass,sample_mass_residual_kg=self.registered_mass-loaded-self.returned_mass,element_ledger_json=json.dumps(e,sort_keys=True),gas_ledger_json=json.dumps(gas,sort_keys=True),gas_residual_mol=self.initial_gas_mol+self.air_in_mol+self.he_in_mol-total-self.gas_out_mol,helium_supply_remaining_mol=self.he_remaining,tube_electrical_energy_j=self.electrical_energy,xray_energy_j=self.xray_energy,sample_absorbed_energy_j=self.absorbed_energy,escaped_energy_j=self.escaped_energy,tube_loss_energy_j=self.tube_loss,energy_residual_j=self.electrical_energy-self.tube_loss-self.absorbed_energy-self.escaped_energy)
 def _result(self):return dict(phase=self.phase,complete=self.phase=='succeeded',energy_edges_j=self._edges.tolist(),assays=copy.deepcopy(self.assays),active_assay=copy.deepcopy(self._active),mass_fractions=copy.deepcopy(self._concentrations),qc=list(dict.fromkeys(self._qc)),acquisition_method_json=json.dumps(self._run_method,sort_keys=True),acquisition_signature=self._run_key or '',evidence='synthetic XRF photon-channel counts from true reference line energies and conditioned source composition; not original measured XRF counts',template_source=TEMPLATE_REL)
 def read(self,visibility='agent'):
  progress=self.elapsed_s/max(1e-9,(self._run_method or {}).get('integration_s',1)*(self._run_method or {}).get('repeats',1))
  v={'plant.configured':self.plant is not None,'sample.loaded':self.sample is not None,'power.enabled':self.powered,'cover.opened':self.opened,'cover.angle_rad':1.48 if self.opened else 0.,'filter.id':self.filter_id,'filter.position_m':self.filter_position,'collimator.diameter_m':self.diameter,'collimator.position_m':self.collimator_position,'tube.actual_voltage_v':self.voltage,'tube.actual_current_a':self.current,'detector.temperature_k':self.temperature,'xray.emitting':self.phase=='running' and self.powered and not self.opened,'atmosphere.mode':self.atmosphere,'atmosphere.pressure_pa':self._pressure(),'atmosphere.helium_fraction':self._he_fraction(),'atmosphere.ready':self._atmosphere_ready(),'instrument.ready':self._ready(),'measurement.phase':self.phase,'measurement.elapsed_s':self.elapsed_s,'measurement.progress':min(1.,max(0.,progress)),'measurement.completed_repeats':len(self.assays),'detector.count_rate_s_inv':self._last_point['count_rate_s_inv'] if self._last_point else None,'detector.last_sample_time_s':self._last_point['end_s'] if self._last_point else None,'spectrum.current_energy_j':self.cursor_j,'result.mass_fraction':self._concentrations[0]['mass_fraction'] if self._concentrations else None,'result.mass_fractions':copy.deepcopy(self._concentrations),'blank.valid':self._blank_valid(),'calibration.ready':self._calibration_ready(),'storage.result_count':len(self._records),'storage.curve_count':len(self._curves),'safety.warning':self.warning}
  if visibility in ['debug','evaluator']:v['truth.ledger']=self._ledger()
  return v
 def invoke(self,capability_id,**a):
  if capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if capability_id in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[capability_id][0],a);result=self._invoke(capability_id,a);validate(CAPS[capability_id][1],result)
  if CAPS[capability_id][4]!='query':self.revision+=1;self._archive_operation()
  return result
 def _invoke(self,k,a):
  if k=='plant.configure':
   if self.plant is not None or self.time_s!=0:raise ActionRejected('INITIALIZATION_ONLY')
   if a['optional_helium_installed'] and (a['helium_flow_m3_s']<=0 or a['helium_supply_mol']<=0):raise ActionRejected('HELIUM_SERVICE_REQUIRED')
   self.plant=copy.deepcopy(a);self.temperature=a['ambient_temperature_k'];self.air_mol=self.initial_gas_mol=a['ambient_pressure_pa']*a['chamber_volume_m3']/(R*a['ambient_temperature_k']);self.he_remaining=a['helium_supply_mol'];self._rng=np.random.default_rng(a['seed']);return {'configured':True}
  if k=='sample.load':
   self._idle()
   if self.sample:raise ActionRejected('SAMPLE_ALREADY_LOADED')
   if not self.opened:raise ActionRejected('OPEN_COVER_REQUIRED')
   if not a['accept_geometric_clearance_assumption']:raise ActionRejected('GEOMETRIC_CLEARANCE_ACK_REQUIRED')
   if not a['accept_conditional_response']:raise ActionRejected('CONDITIONAL_RESPONSE_ACK_REQUIRED')
   if any(x>y for x,y in zip(a['dimensions_m'],[.300,.275,.100])):raise ActionRejected('SAMPLE_ENVELOPE')
   if a['phase']=='liquid' and (not a['sealed_cup'] or self.atmosphere=='vacuum'):raise ActionRejected('LIQUID_CONFIGURATION')
   r=self._reference(a['template_id']) if a['kind']!='blank' else None;values={e:v*a['reference_fraction'] for e,v in _values(r).items()} if r else {}
   sample=dict(mass=a['mass_kg'],values=values,phase=a['phase'],kind=a['kind'],reference_id=a['template_id'],matrix=a['matrix_factor'],reference_fraction=a['reference_fraction'],conditions=copy.deepcopy((r or {}).get('conditions',{})))
   self.sample=sample;self.registered_mass+=sample['mass']
   for e,v in values.items():self.element_registered[e]=self.element_registered.get(e,0.)+sample['mass']*v
   return {'loaded':True}
  if k=='sample.unload':
   self._idle()
   if not self.opened:raise ActionRejected('OPEN_COVER_REQUIRED')
   if not self.sample:raise ActionRejected('NO_SAMPLE')
   mass=self.sample['mass'];self.returned_mass+=mass
   for e,v in self.sample['values'].items():self.element_returned[e]=self.element_returned.get(e,0.)+mass*v
   self.sample=None;return {'returned_mass_kg':mass}
  if k=='cover.set':
   if a['opened'] and self.plant and self._pressure()<.99*self.plant['ambient_pressure_pa']:raise ActionRejected('VENT_BEFORE_OPENING')
   if a['opened']:
    self._stop('COVER_OPENED')
    if self.plant:
     ambient_n=self.plant['ambient_pressure_pa']*self.plant['chamber_volume_m3']/(R*self.plant['ambient_temperature_k']);entering=max(0.,ambient_n-self.air_mol);self.he_out_mol+=self.he_mol;self.gas_out_mol+=self.he_mol;self.air_in_mol+=entering;self.air_mol=ambient_n;self.he_mol=0.;self.atmosphere='air'
   self.opened=a['opened'];return {'opened':self.opened}
  if k=='power.set':
   if a['enabled'] and not self.plant:raise ActionRejected('PLANT_REQUIRED')
   if not a['enabled']:self._stop('POWER_OFF')
   self.powered=a['enabled'];return {'enabled':self.powered}
  if k=='method.configure':
   self._idle()
   if len(set(a['selected_lines']))!=len(a['selected_lines']) or a['window_s']>a['integration_s'] or math.ceil(a['integration_s']/a['window_s'])*a['repeats']>MAX_WINDOWS:raise ActionRejected('INVALID_METHOD')
   for key in a['selected_lines']:
    line=self._line(key)
    if a['voltage_v']*E_CHARGE<=line['edge_energy_j']:raise ActionRejected('LINE_NOT_EXCITABLE')
    if not 0<line['energy_j']<a['energy_max_j']:raise ActionRejected('LINE_OUTSIDE_SPECTRUM')
   self.method=copy.deepcopy(a);return {'configured':True}
  if k=='filter.select':self._idle();self.filter_id=a['filter_id'];return {'selected':self.filter_id}
  if k=='collimator.select':
   self._idle()
   if a['diameter_m'] not in APERTURES:raise ActionRejected('UNSUPPORTED_APERTURE')
   self.diameter=a['diameter_m'];return {'selected_diameter_m':self.diameter}
  if k=='atmosphere.set':
   self._idle()
   if not self.plant:raise ActionRejected('PLANT_REQUIRED')
   mode=a['mode']
   if mode!='air' and not self.plant['optional_'+('vacuum' if mode=='vacuum' else 'helium')+'_installed']:raise ActionRejected('OPTION_NOT_INSTALLED')
   if mode!='air' and self.opened:raise ActionRejected('CLOSE_COVER_REQUIRED')
   if mode=='vacuum' and self.sample and self.sample['phase']=='liquid':raise ActionRejected('LIQUID_VACUUM_UNSUPPORTED')
   if mode=='helium' and (self.he_remaining<=0 or self.plant['helium_flow_m3_s']<=0):raise ActionRejected('HELIUM_EXHAUSTED')
   self.atmosphere=mode;return {'mode':mode}
  if k=='measurement.start':return self._start()
  if k=='measurement.stop':self._stop('CANCELLED');return {'phase':self.phase}
  if k=='process.measure':
   s=self.read();return dict(phase=self.phase,elapsed_s=self.elapsed_s,progress=s['measurement.progress'],count_rate_s_inv=s['detector.count_rate_s_inv'],mass_fractions=copy.deepcopy(self._concentrations))
  if k=='result.read':return self._result()
  if k=='spectrum.read':
   assay=self._active or (self.assays[-1] if self.assays else None)
   return dict(energy_edges_j=self._edges.tolist(),counts=copy.deepcopy(assay['counts']) if assay else [0]*(len(self._edges)-1),sample_window_s=[self._spectrum_start,self._spectrum_end] if self._spectrum_start is not None else None,evidence='simultaneous synthetic SDD energy-channel histogram; unknown element contributions unmodeled, not proof of absence; detector broadening/filter/air/matrix assumptions, no raw OEM spectrum')
  if k=='spectrum.cursor':self.cursor_j=a['energy_j'];return {'energy_j':self.cursor_j}
  if k=='result.export':
   if a['format']=='json':return dict(format='json',content=json.dumps(self._result(),sort_keys=True,allow_nan=False))
   stream=io.StringIO();w=csv.writer(stream);w.writerow(['repeat','window_start_s','window_end_s','energy_low_j','energy_high_j','counts'])
   for i,assay in enumerate(self.assays+([self._active] if self._active else [])):
    for win in assay['windows']:
     for j,c in enumerate(win['counts']):w.writerow([i,win['start_s'],win['end_s'],self._edges[j],self._edges[j+1],c])
   return dict(format='csv',content=stream.getvalue())
  if k=='blank.read':return dict(valid=self._blank_valid(),blank_json=json.dumps(self._blanks.get(self._key())))
  if k=='calibration.fit':return self._fit(a)
  if k=='calibration.select':
   if a['curve_id'] not in self._curves:raise ActionRejected('UNKNOWN_CALIBRATION')
   if self._curves[a['curve_id']]['key']!=self._key():raise ActionRejected('CALIBRATION_MISMATCH')
   self._selected=a['curve_id'];return {'selected':self._selected}
  if k=='calibration.read':
   if a['curve_id'] not in self._curves:raise ActionRejected('UNKNOWN_CALIBRATION')
   return {'curve_json':json.dumps(self._curves[a['curve_id']],sort_keys=True)}
  if k=='records.list':return dict(record_ids=list(self._records),curve_ids=list(self._curves))
  if k=='records.read':
   if a['record_id'] not in self._records:raise ActionRejected('UNKNOWN_RECORD')
   return copy.deepcopy(self._records[a['record_id']])
  if k=='records.remove':
   if a['record_id'] not in self._records:raise ActionRejected('UNKNOWN_RECORD')
   del self._records[a['record_id']];return {'removed':True}
  if k=='template.read':
   value=self.templates.get(a['substance_id'],self.lines.get(a['substance_id'],self.library.get('edges',{}).get(a['substance_id'])))
   if value is None:raise ActionRejected('UNKNOWN_REFERENCE')
   return {'template_json':json.dumps(value,sort_keys=True)}
  if k=='product.read':return self._ledger()
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _fit(self,a):
  self._idle()
  if not self.method or not self._blank_valid():raise ActionRejected('MATCHED_BLANK_REQUIRED')
  if not a['accept_same_matrix_response']:raise ActionRejected('MATRIX_RESPONSE_ACK_REQUIRED')
  if a['curve_id'] not in self._curves and len(self._curves)>=MAX_CURVES:raise ActionRejected('CURVE_CAPACITY')
  fits={}
  for line_id in self.method['selected_lines']:
   line=self._line(line_id);x=[];y=[]
   for obs in a['observations']:
    if obs['acquisition_signature']!=self._key():raise ActionRejected('OBSERVATION_SIGNATURE_MISMATCH')
    record=self._reference(obs['reference_id']);eligible=record.get('calibration_eligible_elements',[])
    if line['element'] not in eligible:raise ActionRejected('REFERENCE_NOT_CALIBRATION_ELIGIBLE')
    vals=_values(record)
    if line['element'] not in vals:raise ActionRejected('UNREPORTED_ELEMENT')
    matches=[r for r in obs['line_observations'] if r['line_id']==line_id]
    if len(matches)!=1 or matches[0]['net_rate_s_inv'] is None:raise ActionRejected('INVALID_STANDARD_OBSERVATION')
    half=2*self.plant['fwhm_j']/2.354820045;expected=(max(0.,line['energy_j']-half),min(self.method['energy_max_j'],line['energy_j']+half))
    if any(abs(matches[0][name]-v)>1e-24 for name,v in zip(['roi_low_j','roi_high_j'],expected)):raise ActionRejected('OBSERVATION_ROI_MISMATCH')
    x.append(vals[line['element']]*obs['reference_fraction']);y.append(matches[0]['net_rate_s_inv'])
   xx=np.asarray(x);yy=np.asarray(y);scale=max(abs(xx));span=np.ptp(xx)
   if len(set(x))<3 or scale<=0 or span<=scale*1e-10:raise ActionRejected('DEGENERATE_CALIBRATION')
   co=np.linalg.lstsq(np.column_stack([xx/scale,np.ones(len(x))]),yy,rcond=None)[0];slope=float(co[0]/scale);intercept=float(co[1]);pred=slope*xx+intercept;ss=float(np.sum((yy-yy.mean())**2));r2=1-float(np.sum((pred-yy)**2))/ss if ss else 0.
   if not all(math.isfinite(z) for z in [slope,intercept,r2]) or slope<=0 or r2<.95:raise ActionRejected('CALIBRATION_FIT_INVALID')
   fits[line_id]=dict(slope_s_inv=slope,intercept_s_inv=intercept,min_fraction=float(xx.min()),max_fraction=float(xx.max()),r_squared=r2)
  curve=dict(key=self._key(),fits=fits,observations=copy.deepcopy(a['observations']),evidence='reference compositions with supplied actual observed ROI rates; same matrix/geometry condition asserted, not OEM FP or CRM certification')
  self._curves[a['curve_id']]=curve;return dict(curve_id=a['curve_id'],line_count=len(fits),minimum_r_squared=min(v['r_squared'] for v in fits.values()))
 def _prepare_response(self):
  self._response_cache=None
  p=self.plant;m=self.method;self._edges=np.linspace(0,m['energy_max_j'],m['bins']+1);sigma=p['fwhm_j']/2.354820045;shapes=[];elements=[];energies=[];coeff=[];keys=[]
  for key,q in self.lines.items():
   if q.get('spectrum_eligible') is False or q.get('source_reference_only'):continue
   try:q=self._line(key)
   except ActionRejected:continue
   if q['edge_energy_j']>=m['voltage_v']*E_CHARGE or q['energy_j']>=m['energy_max_j']:continue
   cdf=np.fromiter((.5*(1+math.erf((float(v)-q['energy_j'])/(sigma*math.sqrt(2)))) for v in self._edges),dtype=float,count=len(self._edges));shapes.append(np.diff(cdf));keys.append(key);elements.append(q['element']);energies.append(q['energy_j']);coeff.append((1-q['edge_energy_j']/(m['voltage_v']*E_CHARGE))**2)
  self._line_ids=keys;self._shapes=np.asarray(shapes);self._line_elements=elements;self._line_energies=np.asarray(energies);self._base_coeff=np.asarray(coeff);self._candidate_elements=sorted(set(elements));self._element_projection=np.asarray([[1. if e==k else 0. for k in self._candidate_elements] for e in elements]);self._normalization=self._base_coeff@self._element_projection
 def _response(self):
  p=self.plant;m=self.method;gas=(self.air_mol+.05*self.he_mol)/max(self.initial_gas_mol,1e-30)
  if self._response_cache is not None and self._response_cache[0]==gas:return self._response_cache[1]
  coeff=self._base_coeff*np.exp(-p['air_optical_depth_at_1kev']*gas/(self._line_energies/(1000*E_CHARGE))**3);strength=p['line_rate_s_inv']*self.sample['matrix']*(self.diameter/.01)**2*p['filter_transmissions'][FILTERS.index(self.filter_id)]*(m['current_a']/.0001);fractions=np.asarray([self.sample['values'].get(e,0.) for e in self._line_elements]);rate=np.full(m['bins'],p['background_rate_s_inv']/m['bins'])+(coeff*fractions*strength)@self._shapes;factor=1/(1+rate.sum()*p['dead_time_s']);observed=rate*factor;saturated=observed.sum()>p['max_count_rate_s_inv'] or 1-factor>=.95
  if observed.sum()>p['max_count_rate_s_inv']:observed*=p['max_count_rate_s_inv']/observed.sum()
  result=(observed,coeff,bool(saturated));self._response_cache=(gas,result);return result
 def _start(self):
  self._idle()
  if not self.sample:raise ActionRejected('NO_SAMPLE')
  if not self._ready():raise ActionRejected('INSTRUMENT_NOT_READY')
  if len(self._records)>=MAX_RESULTS:raise ActionRejected('RESULT_CAPACITY')
  if self.sample['kind']!='blank' and not self._blank_valid():raise ActionRejected('MATCHED_BLANK_REQUIRED')
  if self.sample['phase']=='liquid' and self.atmosphere=='vacuum':raise ActionRejected('LIQUID_VACUUM_UNSUPPORTED')
  self._prepare_response();self._pending_mu=np.zeros(self.method['bins']);self._pending_coeff=np.zeros(len(self._base_coeff));self._assay_coeff=np.zeros(len(self._base_coeff));self._profile_history=[];self._saturated=False;
  self._run_method=copy.deepcopy(self.method);self._run_key=self._key();self._run_kind=self.sample['kind'];self._run_curve=copy.deepcopy(self._curves.get(self._selected)) if self._calibration_ready() else None;self._run_blank=copy.deepcopy(self._blanks.get(self._key()));self.phase='running';self.elapsed_s=0.;self.assays=[];self._concentrations=[];self._qc=[];self._active=self._new_assay();self._pending_dt=0.;self._last_point=None;self._spectrum_start=self._spectrum_end=None;self.warning='';self._op_count+=1;self.operation_id=f'{self.instance_id}:operation:{self._op_count}';return {'operation_id':self.operation_id}
 def _new_assay(self):return dict(response_profile_json='',windows=[],line_observations=[],counts=[0]*(len(self._edges)-1),elapsed_s=0.,complete=False)
 def _finish_window(self):
  dt=self._pending_dt;mu=self._pending_mu;counts=self._rng.poisson(mu) if self.plant['poisson_counts'] else np.rint(mu).astype(np.int64);rate=float(counts.sum()/dt);saturated=self._saturated or rate>self.plant['max_count_rate_s_inv'] or rate*self.plant['dead_time_s']>=.95
  win=dict(start_s=self.time_s-dt,end_s=self.time_s,counts=counts.tolist(),count_rate_s_inv=rate,live_time_s=dt*max(0.,1-rate*self.plant['dead_time_s']),saturated=bool(saturated));self._active['windows'].append(win);self._active['counts']=(np.asarray(self._active['counts'],dtype=np.int64)+counts).tolist();self._last_point=win;self._spectrum_start=self._active['windows'][0]['start_s'];self._spectrum_end=self.time_s;self._pending_dt=0.;self._assay_coeff+=self._pending_coeff;self._pending_coeff=np.zeros(len(self._base_coeff));self._pending_mu=np.zeros(len(self._edges)-1)
 def _line_rates(self,assay,profile):
  p=self.plant;counts=np.zeros(len(self._edges)-1);total=0.;invalid=False
  for w in assay['windows']:
   factor=1-w['count_rate_s_inv']*p['dead_time_s'];invalid|=w['saturated'] or factor<=.05
   counts+=np.asarray(w['counts'])/(factor if self._run_method['deadtime_correction'] and factor>.05 else 1.);total+=w['end_s']-w['start_s']
  rates=counts/max(total,1e-30)
  if self._run_kind!='blank':rates-=np.asarray(self._run_blank['rates_s_inv'])
  basis=(self._shapes.T@(self._element_projection*(profile/max(total,1e-30))[:,None]))/self._normalization[None,:];amplitudes,_,rank,singular=np.linalg.lstsq(basis,rates,rcond=1e-8);invalid|=rank<len(self._candidate_elements)
  rows=[];centers=.5*(self._edges[:-1]+self._edges[1:]);half=2*self.plant['fwhm_j']/2.354820045
  for key in self._run_method['selected_lines']:
   q=self._line(key);low=max(0.,q['energy_j']-half);high=min(self._edges[-1],q['energy_j']+half);mask=(centers>=low)&(centers<=high);value=float(amplitudes[self._candidate_elements.index(q['element'])]) if q['element'] in self._candidate_elements else 0.
   rows.append(dict(line_id=key,net_rate_s_inv=None if invalid else value,roi_low_j=low,roi_high_j=high))
  return rows,rates.tolist(),invalid
 def _finish_assay(self):
  rows,rates,invalid=self._line_rates(self._active,self._assay_coeff);self._active['response_profile_json']=json.dumps(dict(line_ids=self._line_ids,mean_coefficients=(self._assay_coeff/self._active['elapsed_s']).tolist(),candidate_elements=self._candidate_elements,normalization=self._normalization.tolist(),fwhm_j=self.plant['fwhm_j'],dead_time_s=self.plant['dead_time_s'],least_squares_rcond=1e-8,meaning='Public reference line/edge profile with observed-window gas attenuation integral; no source fractions or line strengths'),sort_keys=True);self._active['line_observations']=rows;self._active['complete']=True;self.assays.append(self._active);self._profile_history.append(self._assay_coeff.copy());self._active=None
  if invalid:self._qc.append('detector saturation; quantitative response unavailable')
  if len(self.assays)<self._run_method['repeats']:self._active=self._new_assay();self._assay_coeff=np.zeros(len(self._base_coeff));return
  if self._run_kind=='blank':
   if invalid:self._qc.append('blank invalid due saturation')
   else:
    # Correct each real repeat separately before averaging its histogram rate.
    corrected=[self._line_rates(a,coeff)[1] for a,coeff in zip(self.assays,self._profile_history)];self._blanks[self._run_key]=dict(rates_s_inv=np.mean(corrected,axis=0).tolist(),evidence='observed synthetic blank energy histogram',method=copy.deepcopy(self._run_method))
  if self._run_kind=='sample':
   for key in self._run_method['selected_lines']:
    fit=(self._run_curve or {}).get('fits',{}).get(key);q=self._line(key);ys=[next(r['net_rate_s_inv'] for r in a['line_observations'] if r['line_id']==key) for a in self.assays];fraction=sd=None;quality='uncalibrated: count spectrum available, mass fraction unavailable' if fit is None else 'unavailable saturated response'
    if fit is not None and all(v is not None for v in ys):
     xs=np.asarray([(v-fit['intercept_s_inv'])/fit['slope_s_inv'] for v in ys]);tol=1e-12
     if np.any(xs<fit['min_fraction']-tol) or np.any(xs>fit['max_fraction']+tol):quality='outside calibration interpolation domain; no clipping'
     elif np.any(xs<0) or np.any(xs>1):quality='inferred mass fraction outside physical range; no clipping'
     else:fraction=float(xs.mean());sd=float(xs.std(ddof=1)) if len(xs)>1 else None;quality='conditional XRF mass fraction from observed public-catalog profile decomposition and same-matrix calibration; line overlap/unknown matrix remain limitations'
    self._concentrations.append(dict(line_id=key,element=q['element'],mass_fraction=fraction,standard_deviation=sd,quality=quality))
  self.phase='succeeded';self._records['record_'+str(self._op_count)]=self._result()
 def _stop(self,reason):
  if self.phase=='running':self.phase='cancelled';self.warning=reason;self._pending_dt=0.;self._concentrations=[];self._qc.append('partial spectrum: only complete time windows published; specimen mass unchanged')
 def _physical(self,dt):
  if not self.plant:return
  p=self.plant
  def towards(x,target,rate):return x+min(abs(target-x),rate*dt)*(1 if target>x else -1)
  self.voltage=towards(self.voltage,self.method['voltage_v'] if self.powered and self.method else 0.,p['voltage_ramp_v_s']);self.current=towards(self.current,self.method['current_a'] if self.powered and self.method else 0.,p['current_ramp_a_s']);target=p['detector_target_k'] if self.powered else p['ambient_temperature_k'];self.temperature=target+(self.temperature-target)*math.exp(-dt/p['detector_tau_s']);self.filter_position=towards(self.filter_position,FILTERS.index(self.filter_id)*.012,p['selector_speed_m_s']);self.collimator_position=towards(self.collimator_position,APERTURES.index(self.diameter)*.015,p['selector_speed_m_s'])
  ambient_n=p['ambient_pressure_pa']*p['chamber_volume_m3']/(R*p['ambient_temperature_k']);total=self.air_mol+self.he_mol
  if not self.opened:
   if self.atmosphere=='vacuum':
    target_n=p['vacuum_target_pa']*p['chamber_volume_m3']/(R*p['ambient_temperature_k']);next_n=target_n+(total-target_n)*math.exp(-dt/p['vacuum_tau_s']);removed=max(0.,total-next_n);ratio=removed/max(total,1e-30);self.air_out_mol+=self.air_mol*ratio;self.he_out_mol+=self.he_mol*ratio;self.air_mol*=1-ratio;self.he_mol*=1-ratio;self.gas_out_mol+=removed
   elif total<ambient_n*(1-1e-12):
    added=(ambient_n-total)*(-math.expm1(-dt/p['vent_tau_s']));self.air_mol+=added;self.air_in_mol+=added
   elif self.atmosphere=='helium' and self._he_fraction()<.999:
    entering=min(self.he_remaining,p['helium_flow_m3_s']*p['ambient_pressure_pa']/(R*p['ambient_temperature_k'])*dt);old_air=self.air_mol;self.air_mol*=math.exp(-entering/max(total,1e-30));self.he_mol=total-self.air_mol;self.he_remaining-=entering;self.he_in_mol+=entering;self.gas_out_mol+=entering;air_out=old_air-self.air_mol;self.air_out_mol+=air_out;self.he_out_mol+=entering-air_out
    if self.he_remaining<=1e-14 and not self._atmosphere_ready():self._stop('HELIUM_EXHAUSTED');self.warning='HELIUM_EXHAUSTED'
   elif self.atmosphere=='air' and self.he_mol>0:
    entering=ambient_n*dt/p['vent_tau_s'];old_he=self.he_mol;self.he_mol*=math.exp(-entering/max(total,1e-30));self.air_mol=total-self.he_mol;self.air_in_mol+=entering;self.gas_out_mol+=entering;he_out=old_he-self.he_mol;self.he_out_mol+=he_out;self.air_out_mol+=entering-he_out
  if self.phase=='running':
   energy=self.voltage*self.current*dt;photons=energy*p['xray_energy_fraction'];absorbed=photons*p['sample_absorbed_fraction'];self.electrical_energy+=energy;self.xray_energy+=photons;self.absorbed_energy+=absorbed;self.escaped_energy+=photons-absorbed;self.tube_loss+=energy-photons
 def step(self,dt_s):
  validate(number('s',0,1000),dt_s)
  if dt_s==0:return self.read('evaluator')
  end=self.time_s+dt_s
  while self.time_s<end-1e-12:
   dt=min(end-self.time_s,.02)
   if self.phase=='running':dt=min(dt,self._run_method['window_s']-self._pending_dt,self._run_method['integration_s']-self._active['elapsed_s'])
   if self.phase=='running':
    rates,coeff,saturated=self._response();self._pending_mu+=rates*dt;self._pending_coeff+=coeff*dt;self._saturated|=saturated
   self._physical(dt);self.time_s+=dt
   if self.phase=='running':
    self.elapsed_s+=dt;self._active['elapsed_s']+=dt;self._pending_dt+=dt
    if self._pending_dt>=self._run_method['window_s']-1e-10 or self._active['elapsed_s']>=self._run_method['integration_s']-1e-10:self._finish_window()
    if self._active['elapsed_s']>=self._run_method['integration_s']-1e-10:self._finish_assay()
  self.time_s=end;self.revision+=1;self._archive_operation();return self.read('evaluator')
 def _operation_snapshot(self):return dict(protocol_version=PROTOCOL_VERSION,instance_id=self.instance_id,operation_id=self.operation_id,status=self.phase,state_revision=self.revision,clock={'domain':'simulation','time_s':self.time_s},result=self._result(),observations=self.session().read(instance_id=self.instance_id)['observations'],error=dict(code=self.warning,side_effects='partial') if self.phase=='failed' else None)
 def _archive_operation(self):
  if self.operation_id and self.phase in ['succeeded','cancelled','failed'] and self.operation_id not in self._operation_records:self._operation_records[self.operation_id]=self._operation_snapshot()

class ShimadzuEdx7200Session:
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
        m=export_shimadzu_edx7200_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        def timing(key):
            if key in ['detector.count_rate_s_inv','detector.last_sample_time_s'] and h._last_point:return h._last_point['end_s'],[h._last_point['start_s'],h._last_point['end_s']] if key!='detector.last_sample_time_s' else None
            if key.startswith('result.'):
                windows=[w for a in h.assays for w in a['windows']]
                if windows:return windows[-1]['end_s'],[windows[0]['start_s'],windows[-1]['end_s']]
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
'Rh X-ray tube4–50kV,1–1000uA and element Na–U are documented; SI input V/A, photon energy J. Shell edge rather than emission energy sets excitation threshold. Current spectrum uses reference K/L line centers, synthetic equal host line yields/Gaussian response; no measured original spectrum, atomic yield derivation or OEM FP algorithm.',
'Five filter materials and transmission curves unknown; OPEN/F1–F5 host scalar attenuation is not a material model or hard source-energy cutoff.1/3/5/10mm are irradiation spot diameters, not documented slide aperture bores. Filter/collimator linear slides and cover geometry have source support; local pitch/travel and motor rates engineering assumptions.',
'Source composition values and prepared/assigned references are kept apart from generated integer counts. Unknown unreported element contribution is unmodeled, not evidence of absence. Public-catalog spectrum least-squares decomposition separates modeled overlapping K/L profiles and compensates assumed atmosphere attenuation; candidate elements never depend on hidden composition. No automatic chemical identification, matrix absorption/enhancement correction, isotope discrimination or certified XRF accuracy.',
'Solid/powder/sealed liquid mass and modeled element masses are preserved; unknown mass remainder is not presumed oxygen. Host reference_fraction describes explicit homogeneous admixture, not an OEM solid dilution function. Mass does not scale thick homogeneous response; surface, film, thickness and matrix factor are uncalibrated host assumptions. Same response conditions must be accepted for calibration.',
'Air standard. Vacuum/He require explicit host-installed options and positive facilities, default absent; no external pump geometry or electronic installation verification. Liquid vacuum rejected. Finite ideal fixed-volume pump/vent/He displacement uses absolute host reference pressure and temperature; true leak, air contamination, pump curves and He spectroscopy not calibrated.',
'SDD electronically cooled source; target temperature, thermal response, voltage/current ramps, Poisson counts, dead time, saturation, width, yields and noise are host assumptions. Global nonparalyzable deadtime model and simultaneous energy bins; no real detector electronics, pulse pileup, escape peaks or filter-material atomic calculations.',
'Only step advances physical time/RNG. Complete simultaneous spectrum windows are published; stop/cover/power retain complete windows and all specimen mass. Partial-window irradiation energy is kept but incomplete counts not reported. Owner-protected operation snapshots survive later measurements and result deletion; process-local256results/32curves/2000windows are engineering limits, not OEM persistence.',
'Tube electrical energy splits into assumed wall loss/X-ray/sample absorption/escape proxies. Photon counts are synthetic detector observations including electronic background; no claim they close a photon-by-photon radiation balance or establish dose/shielding safety. Cover instant trusted-host repositioning; low-pressure opening refused until vented.',
'Body460W590D360Hmm approximately45kg, specimen≤5kg and rectangular300W275Dapproximately100Hmm chamber envelope exclude corner geometry. Collision fit, sample support, real radiation shielding, hardware/HIL, robotic contact and OS-level truth-file isolation not verified. Optional12position changer/.3mm spot and complete OEM FP/GUI absent.'
]
def export_shimadzu_edx7200_contract_metadata():
 caps={};slots={};run=['measurement.phase','measurement.elapsed_s','measurement.progress','measurement.completed_repeats'];det=['detector.count_rate_s_inv','detector.last_sample_time_s'];res=['result.mass_fraction','result.mass_fractions'];heat=['tube.actual_voltage_v','tube.actual_current_a','detector.temperature_k'];atm=['atmosphere.pressure_pa','atmosphere.helium_fraction','atmosphere.ready','instrument.ready','truth.ledger'];cancel=['measurement.phase','xray.emitting','safety.warning',*res]
 immediate={'plant.configure':['plant.configured','detector.temperature_k',*atm],'sample.load':['sample.loaded','truth.ledger'],'sample.unload':['sample.loaded','truth.ledger'],'cover.set':['cover.opened','cover.angle_rad','atmosphere.pressure_pa','atmosphere.mode','atmosphere.helium_fraction','truth.ledger','blank.valid','calibration.ready','instrument.ready',*cancel],'power.set':['power.enabled','instrument.ready',*cancel],'method.configure':['blank.valid','calibration.ready','instrument.ready'],'filter.select':['filter.id','blank.valid','calibration.ready','instrument.ready'],'collimator.select':['collimator.diameter_m','blank.valid','calibration.ready','instrument.ready'],'atmosphere.set':['atmosphere.mode','atmosphere.ready','instrument.ready','blank.valid','calibration.ready'],'measurement.start':[*run,*det,*res,'xray.emitting','safety.warning'],'measurement.stop':cancel,'spectrum.cursor':['spectrum.current_energy_j'],'calibration.fit':['storage.curve_count'],'calibration.select':['calibration.ready'],'records.remove':['storage.result_count']}
 onstep={'power.set':[*heat,'instrument.ready'],'method.configure':[*heat,'instrument.ready'],'filter.select':['filter.position_m','instrument.ready'],'collimator.select':['collimator.position_m','instrument.ready'],'atmosphere.set':[*atm,'safety.warning'],'measurement.start':[*run,*det,*res,'xray.emitting','truth.ledger','blank.valid','storage.result_count']}
 observes={'plant.configure':['plant.configured'],'sample.load':['sample.loaded'],'cover.set':['cover.opened'],'power.set':['power.enabled'],'filter.select':['filter.id'],'collimator.select':['collimator.diameter_m'],'atmosphere.set':['atmosphere.mode'],'measurement.start':['measurement.phase'],'measurement.stop':['measurement.phase'],'process.measure':[*run[:3],'detector.count_rate_s_inv','result.mass_fractions'],'result.read':['measurement.phase','result.mass_fractions'],'result.export':['measurement.phase','result.mass_fractions'],'spectrum.cursor':['spectrum.current_energy_j'],'blank.read':['blank.valid'],'records.list':['storage.result_count','storage.curve_count'],'product.read':['truth.ledger']}
 for k,(s,u,origin,vis,components,write) in SLOTS.items():
  interval=k.startswith('detector.count') or k.startswith('result.');reference='宿主plant绝对压力，气体实际温度plant.ambient_temperature_k；非厂家参考态' if k=='atmosphere.pressure_pa' else None
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=components,value_schema=copy.deepcopy(s),unit=u,reference_conditions=reference,origin=origin,visibility=vis,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'ShimadzuEdx7200Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode='interval_mean' if interval else 'instant',time_domain='simulation',window_description='最近完整同时能量谱积分窗口；推断结果为完整重复与冻结方法' if interval else None),source_ids=['simulation_assumption'])
 errors=sorted({n.args[0].value for n in ast.walk(ast.parse(Path(__file__).read_text())) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='ActionRejected' and n.args and isinstance(n.args[0],ast.Constant) and isinstance(n.args[0].value,str)})
 for k,(inp,out,components,label,kind,vis,example) in CAPS.items():
  unsupported=k in UNSUPPORTED;effects=[dict(state_id=s,description_zh=SLOT_ZH[s],timing=t) for t,mapping in [('immediate',immediate),('on_step',onstep)] for s in dict.fromkeys(mapping.get(k,[]))];async_=k=='measurement.start'
  ex=copy.deepcopy(example)
  if k=='calibration.fit':ex['observations']=[dict(acquisition_signature='actual method signature',reference_id='blackmass_icp_'+i,reference_fraction=1.,line_observations=[dict(line_id='cu_ka1',net_rate_s_inv=1000.,roi_low_j=7800*E_CHARGE,roi_high_j=8300*E_CHARGE)]) for i in ['f','a','g']]
  caps[k]=dict(function_zh=label,kind=kind,visibility=vis,component_ids=components,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.shimadzu_edx7200.ShimadzuEdx7200Session.invoke',invocation=f'session.invoke(instance_id="edx_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if kind=='query' or unsupported else [dict(description_zh='运行入口检查身份、样品/盖/压力/气氛选件/管压流/SDD/响应签名；所有操作只由宿主时钟推进',state_ids=['power.enabled','cover.opened','instrument.ready','measurement.phase'],enforced_by='runtime',check='Runtime._invoke, _start and Session principal policy')],state_effects=[] if unsupported or kind=='query' else effects,observes=[] if unsupported else observes.get(k,[]),model_effects=dict(mode='none',description_zh='场景效果另需真实几何绑定与验证；查询不动作',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='全部重复的积分窗口完成；无损保留样品' if async_ else '原子返回，有限动作step推进',completion_state_ids=['measurement.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','prepared_specimen','Rh_Xray_tube','SDD','optional_atmosphere','result_store'],partial_failure_zh='保留全部样品与完整窗口及已发生气体/能量效果，不伪回滚'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in errors],example_arguments=ex,verification=[],limitations=LIMITATIONS,source_ids=['manufacturer_primary','simulation_assumption'])
 mech=dict(component_ids=['sample_mount','sample_chamber','xray_tube','filter_slide','collimator_slide','sdd_detector'],state_ids=list(slots),meaning_zh='XRF无损固液样品与真实壳层线/边参考；合成同时能量谱，经观测空白/标准反演质量分数',implementation='lumped',equations=['Emax=e*V; fluorescence only when Eedge<Emax, not merely Eline<Emax.','Rate_i=host_yield*mass_fraction*matrix*current*spot_area*filter_transmission*(1-Eedge/Emax)^2*exp(-host_air_depth/E_keV^3); Gaussian bin integration.','Nonparalyzable common live fraction=1/(1+sum(rate)*tau); complete-window integer counts, inverse net rate from observed counts and matched blank through public-library spectral decomposition.','Observed decomposed profile response=slope*reference_mass_fraction+intercept; >=3 actual reference concentrations, interpolation only.','Specimen registered_mass=loaded_mass+returned_mass for each modeled element and total; irradiation is nonconsumptive.','Ideal gas n=P*V/(R*T); fixed-volume exponential pump/backfill and well-mixed He replacement. Input+initial=chamber+exhaust.','Tube electrical energy=wall loss+sample absorbed Xray energy+escaped Xray energy under explicit host fractions.'],parameters={},source_ids=['manufacturer_primary','simulation_assumption'],limitations=LIMITATIONS)
 # Load an isolated annotation module: never mutate the shared module dictionaries.
 spec=importlib.util.spec_from_file_location('_edx7200_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'xray_fluorescence':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 return dict(capabilities=caps,state_slots=slots,mechanisms={'xray_fluorescence':mech})

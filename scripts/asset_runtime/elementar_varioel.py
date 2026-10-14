"""vario EL cube: reported elemental references, finite combustion/APT and observed calibration."""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
SLUG='elementar_varioel';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-079';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/expansion_batch04/t3_analytical/elementar_varioel/output_templates/library.json'
BOOL={'type':'boolean'};TEXT={'type':'string'}
class ActionRejected(ValueError):pass

def number(unit,low=None,high=None):
 s={'type':'number','x-unit':unit}
 if low is not None:s['minimum']=low
 if high is not None:s['maximum']=high
 return s

def obj(p):return dict(type='object',properties=p,required=list(p),additionalProperties=False)
def arr(s):return dict(type='array',items=s)
def integer(low,high):return dict(number('1',low,high),type='integer')
def enum(values):return dict(type='string',enum=values)
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
def vector(s,low=2,high=10000):return dict(arr(s),minItems=low,maxItems=high)
def validate(s,v):
 if 'anyOf' in s:
  if v is None and any(q.get('type')=='null' for q in s['anyOf']):return
  return validate(next(q for q in s['anyOf'] if q.get('type')!='null'),v)
 t=s.get('type')
 if t in ('number','integer'):
  if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):raise ActionRejected('INVALID_ARGUMENT')
  if t=='integer' and type(v) is not int:raise ActionRejected('INVALID_ARGUMENT')
  if not s.get('minimum',-math.inf)<=v<=s.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='null' and v is not None:raise ActionRejected('INVALID_ARGUMENT')
 elif t=='boolean' and type(v) is not bool:raise ActionRejected('INVALID_ARGUMENT')
 elif t=='string' and not isinstance(v,str):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='object':
  if not isinstance(v,dict) or not set(s['required'])<=v.keys() or not v.keys()<=s['properties'].keys():raise ActionRejected('INVALID_ARGUMENT')
  for k,x in v.items():validate(s['properties'][k],x)
 elif t=='array':
  if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
  for x in v:validate(s['items'],x)
 if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')

ELEMENTS=['C','H','N','S'];ORDER=['N','C','H','S'];MODES={'CHNS':ELEMENTS,'CHN':['C','H','N'],'CNS':['C','N','S'],'CN':['C','N']}
ATOMIC=np.array([.012011,.001008,.014007,.03206]);O2_KG_MOL=.031998
O2_PER_ELEMENT_KG=np.array([1/.012011,1/(4*.001008),0.,1/.03206])
ELEMENT_LIMITS=np.array([40e-6,3e-6,15e-6,6e-6])
PHASE=enum(['idle','running','succeeded','cancelled','failed'])
def element_obj(s):return obj({k:copy.deepcopy(s) for k in ELEMENTS})
FRACTIONS=element_obj(nullable(number('1',0,1)));AREAS=element_obj(nullable(number('s')))
PLANT=obj({'ambient_k':number('K',273.15,323.15),'furnace_capacity_j_k':number('J/K',1,1e5),'furnace_conductance_w_k':number('W/K',.001,1e5),'combustion_rate_s_inv':number('1/s',.001,1000),'reduction_time_s':number('s',.001,3600),'release_tau_s':element_obj(number('s',.001,1000)),'tcd_gain_s_kg':element_obj(number('s/kg',1,1e12)),'tcd_baseline':number('1',-10,10),'tcd_noise_sd':number('1',0,.1),'detector_period_s':number('s',.001,1),'carousel_speed_rad_s':number('rad/s',.01,20),'sample_valve_speed_rad_s':number('rad/s',.01,100),'oxygen_inventory_mol':number('mol',0,1000),'carrier_ready':BOOL,'carrier':enum(['He','Ar']),'heated_line_ready':BOOL,'seed':integer(0,2147483647),'accept_lumped_model':BOOL})
SAMPLE=obj({'slot_index':integer(0,79),'sample_id':TEXT,'kind':enum(['blank','standard','sample']),'sample_class':enum(['organic','inorganic']),'template_id':TEXT,'mass_kg':number('kg',0,1.5e-3),'mass_basis':TEXT,'assumed_unreported_fractions':FRACTIONS,'accept_assumed_missing_elements':BOOL,'accept_unknown_conditions':BOOL})
FURNACE=obj({'combustion_k':number('K',273.15,1473.15),'reduction_k':number('K',273.15,1473.15)})
METHOD=obj({'mode':enum(list(MODES)),'combustion_s':number('s',.001,3600),'oxygen_dose_mol':number('mol',1e-9,.1),'oxygen_admission_s':number('s',.001,60),'baseline_threshold':number('1',1e-8,.1),'baseline_samples':integer(1,100),'minimum_release_s':number('s',.001,1000),'maximum_release_s':number('s',.01,3600),'temperature_tolerance_k':number('K',.01,20)})
PLANT_EXAMPLE=dict(ambient_k=298.15,furnace_capacity_j_k=20.,furnace_conductance_w_k=100.,combustion_rate_s_inv=120.,reduction_time_s=.02,release_tau_s={e:.01 for e in ELEMENTS},tcd_gain_s_kg={'C':1e5,'H':8e5,'N':1.2e5,'S':2e5},tcd_baseline=.01,tcd_noise_sd=1e-6,detector_period_s=.005,carousel_speed_rad_s=10.,sample_valve_speed_rad_s=100.,oxygen_inventory_mol=.02,carrier_ready=True,carrier='He',heated_line_ready=True,seed=79,accept_lumped_model=True)
FURNACE_EXAMPLE=dict(combustion_k=1373.15,reduction_k=1123.15)
METHOD_EXAMPLE=dict(mode='CHNS',combustion_s=.1,oxygen_dose_mol=.0005,oxygen_admission_s=.02,baseline_threshold=1e-5,baseline_samples=2,minimum_release_s=.025,maximum_release_s=1.,temperature_tolerance_k=2.)
SAMPLE_EXAMPLE=dict(slot_index=0,sample_id='external_unknown_capsule',kind='sample',sample_class='organic',template_id='oem_sulfanilic_acid',mass_kg=2e-6,mass_basis='as_reported_unspecified_dry_basis',assumed_unreported_fractions={e:None for e in ELEMENTS},accept_assumed_missing_elements=False,accept_unknown_conditions=True)
BLANK_EXAMPLE=dict(SAMPLE_EXAMPLE,sample_id='external_blank_capsule',kind='blank',template_id='',mass_kg=0.)
STANDARD_EXAMPLE=dict(SAMPLE_EXAMPLE,sample_id='external_reference_capsule',kind='standard')
QUEUE_EXAMPLE={'slot_indices':[0]}
CAL_INPUT=obj({'element':enum(ELEMENTS),'observations':vector(obj({'reference_id':TEXT,'sample_mass_kg':number('kg',1e-9,1.5e-3),'peak_area_s':number('s')}),3,100),'accept_same_method_basis':BOOL})
CAL_OUTPUT=obj({'element':TEXT,'slope_s_kg':number('s/kg',0),'intercept_s':number('s'),'r_squared':number('1'),'mass_range_kg':arr(number('kg',0)),'evidence':TEXT})
CALIBRATION_EXAMPLE=dict(element='C',observations=[dict(reference_id='oem_sulfanilic_acid',sample_mass_kg=m,peak_area_s=m*.4168*1e5) for m in [1e-6,2e-6,3e-6]],accept_same_method_basis=True)
POINT=obj({'element':TEXT,'time_s':number('s',0),'sample_window_s':arr(number('s',0)),'signal':number('1'),'corrected_signal':number('1')})
ASSAY=obj({'slot_index':integer(0,79),'kind':TEXT,'status':TEXT,'mass_kg':number('kg',0),'areas_s':AREAS,'fractions':FRACTIONS,'fraction_quality':element_obj(TEXT),'source_fraction_evidence':element_obj(TEXT),'chromatogram':arr(POINT),'settings_json':TEXT,'calibration_json':TEXT,'limitations':arr(TEXT)})
RESULT=obj({'template_source':TEXT,'phase':PHASE,'stage':TEXT,'complete':BOOL,'completed_assays':arr(ASSAY),'active_assay':nullable(ASSAY),'evidence':TEXT})
MEASURE=obj({'template_source':TEXT,'phase':PHASE,'stage':TEXT,'elapsed_s':number('s',0),'progress':number('1',0,1),'current_slot':nullable(integer(0,79)),'completed_samples':integer(0,80),'active_element':TEXT,'tcd_signal':nullable(number('1')),'fractions':FRACTIONS,'warning':TEXT})
COMPARTMENTS=obj({'registered':element_obj(number('kg',0)),'tray':element_obj(number('kg',0)),'unconverted':element_obj(number('kg',0)),'trapped':element_obj(number('kg',0)),'tails':element_obj(number('kg',0)),'retained_residue':element_obj(number('kg',0)),'unselected_exhaust':element_obj(number('kg',0)),'exhaust':element_obj(number('kg',0)),'removed':element_obj(number('kg',0)),'residual':element_obj(number('kg'))})
LEDGER=obj({'tracked_elements_kg':COMPARTMENTS,'unmeasured_registered_kg':number('kg',0),'unmeasured_tray_kg':number('kg',0),'unmeasured_reactor_kg':number('kg',0),'unmeasured_retained_kg':number('kg',0),'unmeasured_removed_kg':number('kg',0),'unmeasured_residual_kg':number('kg'),'oxygen_initial_mol':number('mol',0),'oxygen_supply_mol':number('mol',0),'oxygen_chamber_mol':number('mol',0),'oxygen_consumed_mol':number('mol',0),'oxygen_vented_mol':number('mol',0),'oxygen_residual_mol':number('mol'),'furnace_heat_j':number('J'),'furnace_stored_j':number('J'),'thermal_residual_j':number('J'),'active_template_id':nullable(TEXT),'evidence':TEXT})
TEMPLATE=obj({'substance_id':TEXT,'name':TEXT,'template_json':TEXT})
CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None):CAPS[k]=[i,o,c,z,kind,vis,{} if ex is None else ex,[]]
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body','combustion_furnace','reduction_furnace','tcd_detector'],'宿主一次声明热学/反应/柱释放/等效TCD系数和气源，均非厂家校准','configuration','evaluator',PLANT_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['autosampler_carousel'],'顶部开放80位盘装已称量封装样；真实Found值与缺项假设分开；无自动推断分子式','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.unload',obj({'slot_index':integer(0,79),'recipient_id':TEXT}),obj({'unloaded':BOOL,'mass_kg':number('kg',0)}),['autosampler_carousel'],'未进炉完整封装样取回；取消后仅冷态断电开维护门伸炉时回收剩余样源物料，不含已排出或外加氧','device_command','evaluator',{'slot_index':0,'recipient_id':'external_rack'})
cap('cover.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['front_door'],'左铰维护门；顶部装样无需开门，开维护门中止并停加热','device_command','evaluator',{'opened':False})
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','combustion_furnace','reduction_furnace'],'逻辑供电，断电中止反应并保留已消耗物料',ex={'enabled':True})
cap('furnace.configure',FURNACE,obj({'configured':BOOL}),['combustion_furnace','reduction_furnace'],'炉温目标最高1473.15K；后续step有限热学推进，锡闪燃1800°C不当炉温','configuration','agent',FURNACE_EXAMPLE)
cap('furnace.extend',obj({'extension_m':number('m',0,.12)}),obj({'extension_m':number('m',0,.12)}),['furnace_carriage'],'冷态断电维护滑炉，工程行程，不模拟真实维护机器人','device_command','evaluator',{'extension_m':.12})
cap('method.configure',METHOD,obj({'configured':BOOL}),['combustion_furnace','oxygen_valve','co2_column','water_column','so2_column'],'CHNS/CHN/CNS/CN、氧脉冲与基线返回判据；并行GC峰模型不适用于APT','configuration','agent',METHOD_EXAMPLE)
cap('sequence.configure',obj({'slot_indices':vector(integer(0,79),1,80)}),obj({'configured':BOOL}),['autosampler_carousel'],'声明已装槽位顺序；同一封装样只消费一次','configuration','agent',QUEUE_EXAMPLE)
cap('sequence.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['autosampler_carousel','ball_valve','oxygen_valve','combustion_furnace','reduction_furnace','co2_column','water_column','so2_column','tcd_detector'],'有限进样/氧脉冲/燃烧还原后N2直接检测，CO2/H2O/SO2按前峰返回基线顺序释放')
cap('sequence.stop',obj({}),obj({'phase':PHASE}),['ball_valve','oxygen_valve'],'中止保留已消耗/未转换/滞留/排出仓与已观测峰；不复活样品')
cap('process.measure',obj({}),MEASURE,['host_screen'],'只读当前TCD/进度及已用观察标定换算的质量分数','query')
cap('result.read',obj({}),RESULT,['host_screen'],'保留各样品原方法/标定与峰面积；未标定或缺失元素返回null，未知余量不算O','query')
cap('chromatogram.read',obj({}),obj({'points':arr(POINT),'evidence':TEXT}),['host_screen'],'实际采样得到的APT顺序等效TCD信号，不是实测厂家色谱','query')
cap('result.export',obj({'format':enum(['json','csv'])}),obj({'format':TEXT,'content':TEXT}),['host_screen'],'纯返回JSON/CSV内容，保留证据/缺失标志，不写文件','query','agent',{'format':'json'})
cap('blank.read',obj({}),obj({'valid':BOOL,'areas_s':AREAS,'settings_json':TEXT}),['host_screen'],'读取完整当前方法空白面积；部分空白不可用于扣除','query')
cap('calibration.fit',CAL_INPUT,CAL_OUTPUT,['host_screen'],'使用真实来源已报告元素含量及调用者标准峰面积拟合；示例面积是演示假设，非实机标线','configuration','agent',CALIBRATION_EXAMPLE)
cap('calibration.read',obj({}),obj({'calibration_json':TEXT,'ready':BOOL}),['host_screen'],'公开拟合标线与范围，不读取未知样品组成','query')
cap('template.read',obj({'substance_id':TEXT}),TEMPLATE,['host_screen'],'公开来源Found/实测含量，缺项null，理论值仅独立derived reference','query','agent',{'substance_id':'oem_sulfanilic_acid'})
cap('product.read',obj({}),LEDGER,['combustion_furnace','co2_column','water_column','so2_column'],'私有已报告/显式假设元素与氧/热学仓守恒；余量组成和相态未定','query','evaluator')
cap('oxygen_mode.measure',obj({}),obj({}),['tcd_detector'],'O元素测定选件未实现，不用100%-CHNS假装测O')
cap('chlorine.measure',obj({}),obj({}),['tcd_detector'],'Cl测定选件未实现')
cap('tic.measure',obj({}),obj({}),['tcd_detector'],'TIC酸化分离选件未实现')
UNSUPPORTED={'oxygen_mode.measure','chlorine.measure','tic.measure'}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='host_screen',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','宿主已配置反应与检测代理','setpoint',c='body')
slot('power.enabled',BOOL,'1','逻辑供电','setpoint',c='power_inlet',w=('power.set','enabled'))
slot('cover.opened',BOOL,'1','维护门开启；不是顶部进样盖','scene',c='front_door')
slot('cover.angle_rad',number('rad',0,1.75),'rad','维护门向前工程转角','scene',c='front_door')
slot('furnace.extension_m',number('m',0,.12),'m','维护炉架向前工程行程','scene',c='furnace_carriage')
slot('temperature.target_k',number('K',273.15,1473.15),'K','燃烧炉目标温度','setpoint',c='combustion_furnace')
slot('temperature.actual_k',number('K',273.15,1473.15),'K','燃烧炉集总温度估计','derived',c='combustion_furnace')
slot('reduction.target_k',number('K',273.15,1473.15),'K','还原炉目标温度','setpoint',c='reduction_furnace')
slot('reduction.actual_k',number('K',273.15,1473.15),'K','还原炉集总温度估计','derived',c='reduction_furnace')
slot('furnace.ready',BOOL,'1','双炉在方法声明容差内','derived',c='combustion_furnace')
slot('autosampler.angle_rad',number('rad',0,2*math.pi),'rad','80位盘有限工程转角','scene',c='autosampler_carousel')
slot('sample_valve.angle_rad',number('rad',0,math.pi/2),'rad','球阀工程角，π/2通料','scene',c='ball_valve')
slot('oxygen_valve.angle_rad',number('rad',0,math.pi/2),'rad','氧脉冲工程阀开度','scene',c='oxygen_valve')
slot('sequence.phase',PHASE,'1','自动序列生命周期')
slot('sequence.stage',TEXT,'1','有限进样/氧脉冲/燃烧/还原/APT阶段')
slot('sequence.progress',number('1',0,1),'1','已完整测完样本占声明序列比例')
slot('sequence.elapsed_s',number('s',0),'s','当前序列实际世界耗时')
slot('sequence.current_slot',nullable(integer(0,79)),'1','当前公开托盘槽号')
slot('sequence.completed_samples',integer(0,80),'1','已完成样本数')
slot('detector.tcd_signal',nullable(number('1')),'1','最近等效TCD观测，相对信号而非未知实机伏特')
slot('detector.active_element',TEXT,'1','当前APT归属元素；N直接，随后C/H/S')
slot('detector.last_sample_time_s',nullable(number('s',0)),'s','最近TCD积分采样世界时刻')
for e in ELEMENTS:slot('result.'+e.lower()+'_fraction',nullable(number('1',0,1)),'1',e+'质量分数；未标定/缺项/外推无效为null')
slot('blank.valid',BOOL,'1','当前方法完整空白可用')
slot('calibration.ready',BOOL,'1','所选模式各元素有当前方法真实参考标线')
slot('method.mode',TEXT,'1','CHNS/CHN/CNS/CN测量模式','setpoint')
slot('safety.warning',TEXT,'1','公开中止与状态提示，不含样品身份')
slot('truth.ledger',LEDGER,'1','私有跟踪元素/未测余量/氧/热量仓账本','process_truth','evaluator','combustion_furnace')


def _library():return json.loads((ROOT/TEMPLATE_REL).read_text())
def _ed(values):return {e:float(v) for e,v in zip(ELEMENTS,values)}
def _nulls():return {e:None for e in ELEMENTS}

class ElementarVarioelRuntime:
 def __init__(self,instance_id='varioel_01',library=None):
  self.instance_id=instance_id;self.library=_library() if library is None else copy.deepcopy(library);self.templates=self.library['compounds']
  self.time_s=0.;self.revision=0;self.plant=None;self.method=None;self.powered=False;self.opened=False;self.extension=0.;self.temperature=np.array([298.15,298.15]);self.targets=self.temperature.copy();self.initial_temperature=self.temperature.copy();self.furnace_heat=0.
  self.carousel=0.;self.ball=0.;self.oxygen_angle=0.;self.phase='idle';self.stage='idle';self.elapsed_s=0.;self.warning='';self.operation_id='';self._op_count=0;self.queue=[];self.samples={};self._queue=[];self._index=0;self._current=None;self._active=None;self.completed_assays=[];self._stage_elapsed=0.;self._run_method=None;self._run_calibration={};self._run_key=None;self._blank=None;self._calibration={}
  self._registered=np.zeros(4);self._removed=np.zeros(4);self._exhaust=np.zeros(4);self._unselected=np.zeros(4);self._tails=np.zeros(4);self._retained=np.zeros(4);self._u_registered=0.;self._u_removed=0.;self._u_retained=0.;self._oxygen_initial=0.;self._oxygen_supply=0.;self._oxygen_chamber=0.;self._oxygen_consumed=0.;self._oxygen_vented=0.
  self._signal=None;self._last_point=None;self._next_detector=None;self._detector_start=None;self._pending_area=0.;self._baseline_samples=[];self._baseline=0.;self._baseline_count=0;self._release_index=0;self._release_order=[];self._active_element=''
  self._rng=np.random.default_rng(0);self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._operation_records={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return ElementarVarioelSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in ['library','templates','_rng']})|{'_rng_state':copy.deepcopy(self._rng.bit_generator.state)}
 def _key(self):return json.dumps(dict(method=self.method,targets=self.targets.tolist()),sort_keys=True)
 def _blank_valid(self):return bool(self._blank and self._blank['key']==self._key())
 def _calibration_ready(self):return bool(self.method and all(e in self._calibration and self._calibration[e]['key']==self._key() for e in MODES[self.method['mode']]))
 def _ready(self):return bool(self.plant and self.method and self.powered and not self.opened and self.extension==0 and np.max(np.abs(self.temperature-self.targets))<=self.method['temperature_tolerance_k'])
 def _ledger(self):
  tray=sum((q['elements'] for q in self.samples.values()),start=np.zeros(4));unc=self._current['unconverted'] if self._current else np.zeros(4);trapped=self._current['trapped'] if self._current else np.zeros(4)
  utray=sum(q['remainder'] for q in self.samples.values());ureactor=self._current['remainder'] if self._current else 0.;res=self._registered-tray-unc-trapped-self._tails-self._retained-self._exhaust-self._unselected-self._removed
  stored=float(np.sum(self.temperature-self.initial_temperature)*(self.plant['furnace_capacity_j_k'] if self.plant else 0.))
  return dict(tracked_elements_kg=dict(registered=_ed(self._registered),tray=_ed(tray),unconverted=_ed(unc),trapped=_ed(trapped),tails=_ed(self._tails),retained_residue=_ed(self._retained),unselected_exhaust=_ed(self._unselected),exhaust=_ed(self._exhaust),removed=_ed(self._removed),residual=_ed(res)),unmeasured_registered_kg=self._u_registered,unmeasured_tray_kg=utray,unmeasured_reactor_kg=ureactor,unmeasured_retained_kg=self._u_retained,unmeasured_removed_kg=self._u_removed,unmeasured_residual_kg=self._u_registered-utray-ureactor-self._u_retained-self._u_removed,oxygen_initial_mol=self._oxygen_initial,oxygen_supply_mol=self._oxygen_supply,oxygen_chamber_mol=self._oxygen_chamber,oxygen_consumed_mol=self._oxygen_consumed,oxygen_vented_mol=self._oxygen_vented,oxygen_residual_mol=self._oxygen_initial-self._oxygen_supply-self._oxygen_chamber-self._oxygen_consumed-self._oxygen_vented,furnace_heat_j=self.furnace_heat,furnace_stored_j=stored,thermal_residual_j=self.furnace_heat-stored,active_template_id=self._current['input']['template_id'] if self._current else None,evidence='tracked reported/explicitly assumed element masses; unreported remainder identity and phase unresolved; model O2 is external reagent only; finite tails/residue are not discarded; net two-lump heat is not electrical power or complete apparatus calorimetry')
 def _fractions(self):return copy.deepcopy(self.completed_assays[-1]['fractions']) if self.completed_assays else _nulls()
 def _result(self):return dict(template_source=TEMPLATE_REL,phase=self.phase,stage=self.stage,complete=self.phase=='succeeded',completed_assays=copy.deepcopy(self.completed_assays),active_assay=copy.deepcopy(self._active),evidence='real reported source compositions drive explicitly synthetic combustion/APT/TCD traces; concentrations inferred only from caller-fitted measured-reference calibration, never directly from hidden sample fractions')
 def _measure(self):return dict(template_source=TEMPLATE_REL,phase=self.phase,stage=self.stage,elapsed_s=self.elapsed_s,progress=len(self.completed_assays)/len(self._queue) if self._queue else 0.,current_slot=self._queue[self._index] if self._queue and self._index<len(self._queue) else None,completed_samples=len(self.completed_assays),active_element=self._active_element,tcd_signal=self._signal,fractions=self._fractions(),warning=self.warning)
 def read(self,visibility='agent'):
  if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
  m=self._measure();v={'plant.configured':self.plant is not None,'power.enabled':self.powered,'cover.opened':self.opened,'cover.angle_rad':1.75 if self.opened else 0.,'furnace.extension_m':self.extension,'temperature.target_k':float(self.targets[0]),'temperature.actual_k':float(self.temperature[0]),'reduction.target_k':float(self.targets[1]),'reduction.actual_k':float(self.temperature[1]),'furnace.ready':self._ready(),'autosampler.angle_rad':self.carousel,'sample_valve.angle_rad':self.ball,'oxygen_valve.angle_rad':self.oxygen_angle,'sequence.phase':self.phase,'sequence.stage':self.stage,'sequence.progress':m['progress'],'sequence.elapsed_s':self.elapsed_s,'sequence.current_slot':m['current_slot'],'sequence.completed_samples':len(self.completed_assays),'detector.tcd_signal':self._signal,'detector.active_element':self._active_element,'detector.last_sample_time_s':self._last_point['time_s'] if self._last_point else None,'blank.valid':self._blank_valid(),'calibration.ready':self._calibration_ready(),'method.mode':self.method['mode'] if self.method else '', 'safety.warning':self.warning,'truth.ledger':self._ledger()}
  v.update({'result.'+e.lower()+'_fraction':m['fractions'][e] for e in ELEMENTS})
  if visibility=='agent':v.pop('truth.ledger')
  return copy.deepcopy(v)
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
   library=self.library;rs=snap.pop('_rng_state');self.__dict__.clear();self.__dict__.update(snap);self.library=library;self.templates=library['compounds'];self._rng=np.random.default_rng();self._rng.bit_generator.state=rs;raise
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _reference(self,key):
  if key not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
  q=self.templates[key];f=q.get('element_mass_fractions')
  if not isinstance(f,dict) or not any(f.get(e) is not None for e in ELEMENTS):raise ActionRejected('INVALID_REFERENCE')
  validate(FRACTIONS,{e:f.get(e) for e in ELEMENTS})
  if sum(v for v in f.values() if v is not None)>1:raise ActionRejected('SOURCE_COMPOSITION_RANGE')
  return q
 def _invoke(self,k,a):
  if k=='plant.configure':
   if self.plant is not None or self.samples:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_lumped_model']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   self.plant=a;self.temperature[:]=a['ambient_k'];self.targets[:]=a['ambient_k'];self.initial_temperature=self.temperature.copy();self._oxygen_initial=self._oxygen_supply=a['oxygen_inventory_mol'];self._rng=np.random.default_rng(a['seed']);return {'configured':True}
  if k=='sample.load':
   self._idle()
   if not self.plant:raise ActionRejected('PLANT_REQUIRED')
   i=a['slot_index']
   if i in self.samples or (self._current and self._current['input']['slot_index']==i):raise ActionRejected('SLOT_OCCUPIED')
   if not a['sample_id'] or not a['mass_basis']:raise ActionRejected('INVALID_ARGUMENT')
   mass=a['mass_kg'];known=np.ones(4,dtype=bool);fractions=np.zeros(4);ev={e:'blank_no_sample' for e in ELEMENTS}
   if a['kind']=='blank':
    if mass!=0 or a['template_id'] or any(v is not None for v in a['assumed_unreported_fractions'].values()):raise ActionRejected('BLANK_COMPOSITION')
   else:
    if mass<=0 or (a['sample_class']=='organic' and mass>20e-6):raise ActionRejected('SAMPLE_MASS_LIMIT')
    q=self._reference(a['template_id'])
    if q.get('basis')!=a['mass_basis']:raise ActionRejected('SOURCE_BASIS_MISMATCH')
    if not a['accept_unknown_conditions']:raise ActionRejected('UNKNOWN_CONDITIONS_ACK_REQUIRED')
    for j,e in enumerate(ELEMENTS):
     v=q['element_mass_fractions'].get(e);assumed=a['assumed_unreported_fractions'][e]
     if v is not None:
      if assumed is not None:raise ActionRejected('REPORTED_VALUE_OVERRIDE')
      fractions[j]=v;ev[e]=q.get('evidence','reported_measurement')
     elif assumed is not None:
      if not a['accept_assumed_missing_elements']:raise ActionRejected('MISSING_ELEMENT_ACK_REQUIRED')
      fractions[j]=assumed;ev[e]='explicit_host_assumption_not_source_measurement'
     else:known[j]=False;ev[e]='unreported_unknown_not_zero'
    if fractions.sum()>1:raise ActionRejected('SOURCE_COMPOSITION_RANGE')
    remainder=max(0.,1.-float(fractions.sum()));upper=np.where(known,fractions,remainder)*mass
    if np.any(upper>ELEMENT_LIMITS*(1+1e-12)):raise ActionRejected('ELEMENT_MASS_LIMIT')
   elements=mass*fractions;remainder=max(0.,mass-float(elements.sum()));self.samples[i]=dict(input=a,elements=elements,known=known,evidence=ev,remainder=remainder)
   self._registered+=elements;self._u_registered+=remainder;return {'loaded':True}
  if k=='sample.unload':
   self._idle()
   if not a['recipient_id']:raise ActionRejected('RECIPIENT_REQUIRED')
   i=a['slot_index']
   if i in self.samples:
    q=self.samples.pop(i);self._removed+=q['elements'];self._u_removed+=q['remainder'];return dict(unloaded=True,mass_kg=q['input']['mass_kg'])
   if self._current and self._current['input']['slot_index']==i:
    if self.powered or not self.opened or self.extension<=0 or np.max(self.temperature)>323.15:raise ActionRejected('COLD_SERVICE_REQUIRED')
    q=self._current;mass=q['unconverted']+q['trapped'];self._removed+=mass;self._u_removed+=q['remainder'];amount=float(mass.sum())+q['remainder'];self._oxygen_vented+=self._oxygen_chamber;self._oxygen_chamber=0.;self._current=None;self._active=None;return dict(unloaded=True,mass_kg=amount)
   raise ActionRejected('EMPTY_SLOT')
  if k=='cover.set':
   if a['opened'] and self.phase=='running':self._stop('MAINTENANCE_DOOR_OPEN')
   if not a['opened'] and self.extension>0:raise ActionRejected('RETRACT_FURNACE_REQUIRED')
   self.opened=a['opened'];return dict(opened=self.opened)
  if k=='power.set':
   if a['enabled'] and (not self.plant or self.opened or self.extension>0):raise ActionRejected('POWER_INTERLOCK')
   if not a['enabled'] and self.phase=='running':self._stop('POWER_OFF')
   self.powered=a['enabled'];return dict(enabled=self.powered)
  if k=='furnace.configure':
   self._idle()
   if not self.plant:raise ActionRejected('PLANT_REQUIRED')
   self.targets=np.array([a['combustion_k'],a['reduction_k']]);return {'configured':True}
  if k=='furnace.extend':
   self._idle()
   if self.powered or not self.opened or np.max(self.temperature)>323.15:raise ActionRejected('COLD_SERVICE_REQUIRED')
   self.extension=a['extension_m'];return dict(extension_m=self.extension)
  if k=='method.configure':
   self._idle()
   if a['maximum_release_s']<a['minimum_release_s']:raise ActionRejected('RELEASE_TIME_RANGE')
   if self.plant and a['maximum_release_s']<a['baseline_samples']*self.plant['detector_period_s']:raise ActionRejected('RELEASE_TIME_RANGE')
   self.method=a;return {'configured':True}
  if k=='sequence.configure':
   self._idle()
   if len(set(a['slot_indices']))!=len(a['slot_indices']):raise ActionRejected('DUPLICATE_SLOT')
   self.queue=a['slot_indices'];return {'configured':True}
  if k=='sequence.start':return self._start()
  if k=='sequence.stop':
   if self.phase=='running':self._stop('CANCELLED')
   return {'phase':self.phase}
  if k=='process.measure':return self._measure()
  if k=='result.read':return self._result()
  if k=='chromatogram.read':return dict(points=[p for q in self.completed_assays+([self._active] if self._active else []) for p in copy.deepcopy(q['chromatogram'])],evidence='observed synthetic equivalent TCD finite windows; N,C,H,S sequential APT gates, residual earlier tails explicitly included')
  if k=='result.export':
   r=self._result()
   if a['format']=='json':content=json.dumps(r,sort_keys=True,allow_nan=False)
   else:
    import csv,io
    f=io.StringIO();f.write('# metadata_json='+json.dumps({z:v for z,v in r.items() if z not in ['completed_assays','active_assay']},sort_keys=True)+'\n');w=csv.writer(f);w.writerow(['slot_index','kind','status','mass_kg','element','peak_area_s','mass_fraction','quality','settings_json','calibration_json'])
    for q in r['completed_assays']+([r['active_assay']] if r['active_assay'] else []):
     for e in ELEMENTS:w.writerow([q['slot_index'],q['kind'],q['status'],q['mass_kg'],e,q['areas_s'][e],q['fractions'][e],q['fraction_quality'][e],q['settings_json'],q['calibration_json']])
    content=f.getvalue()
   return dict(format=a['format'],content=content)
  if k=='blank.read':return dict(valid=self._blank_valid(),areas_s=copy.deepcopy(self._blank['areas']) if self._blank else _nulls(),settings_json=self._blank['key'] if self._blank else 'null')
  if k=='calibration.fit':return self._fit(a)
  if k=='calibration.read':return dict(calibration_json=json.dumps(self._calibration,sort_keys=True,allow_nan=False),ready=self._calibration_ready())
  if k=='template.read':
   q=self._reference(a['substance_id']);return dict(substance_id=a['substance_id'],name=q['name'],template_json=json.dumps(q,sort_keys=True,allow_nan=False))
  if k=='product.read':return self._ledger()
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _fit(self,a):
  self._idle()
  if not self.method or not self._blank_valid():raise ActionRejected('MATCHED_BLANK_REQUIRED')
  if not a['accept_same_method_basis']:raise ActionRejected('CALIBRATION_ACK_REQUIRED')
  e=a['element'];x=[];y=[];bases=set();sources=set()
  if e not in MODES[self.method['mode']]:raise ActionRejected('ELEMENT_NOT_IN_MODE')
  for p in a['observations']:
   q=self._reference(p['reference_id']);v=q['element_mass_fractions'].get(e)
   if v is None or v<=0 or not q.get('evidence','').startswith('measured'):raise ActionRejected('MEASURED_REFERENCE_REQUIRED')
   mass=p['sample_mass_kg']*v
   if mass>ELEMENT_LIMITS[ELEMENTS.index(e)]:raise ActionRejected('ELEMENT_MASS_LIMIT')
   x.append(mass);y.append(p['peak_area_s']);bases.add(q.get('basis'));sources.add(q.get('evidence'))
  if len(bases)!=1:raise ActionRejected('SOURCE_BASIS_MISMATCH')
  x=np.array(x,dtype=np.longdouble);y=np.array(y,dtype=np.longdouble);xm=x.mean();ym=y.mean();dx=x-xm;dy=y-ym;den=np.sum(dx*dx)
  if den<=0:raise ActionRejected('DEGENERATE_CALIBRATION')
  slope=np.sum(dx*dy)/den;intercept=ym-slope*xm;ss=np.sum(dy*dy);r2=1-np.sum((y-(slope*x+intercept))**2)/ss if ss>0 else np.longdouble(0)
  if slope<=0 or not all(np.isfinite(z) and abs(z)<=np.finfo(float).max for z in [slope,intercept,r2]):raise ActionRejected('CALIBRATION_RANGE')
  result=dict(element=e,slope_s_kg=float(slope),intercept_s=float(intercept),r_squared=float(r2),mass_range_kg=[float(min(x)),float(max(x))],evidence='caller supplied observed standard areas and real reported reference composition; not certified CRM or OEM calibration; same method and basis explicitly acknowledged')
  self._calibration[e]=dict(result,key=self._key(),basis=next(iter(bases)),source_evidence=sorted(sources));return result
 def _start(self):
  self._idle()
  if self._current is not None:raise ActionRejected('RECOVER_CANCELLED_RESIDUE_REQUIRED')
  if not self._ready():raise ActionRejected('FURNACE_NOT_READY')
  if self.targets[0]<873.15 or self.targets[1]<673.15:raise ActionRejected('REACTION_TEMPERATURE_LIMIT')
  if len(self.queue)*len(MODES[self.method['mode']])*math.ceil(self.method['maximum_release_s']/self.plant['detector_period_s'])>1000000:raise ActionRejected('EVENT_RESOURCE_LIMIT')
  if not self.plant['carrier_ready'] or not self.plant['heated_line_ready']:raise ActionRejected('GAS_PATH_NOT_READY')
  if not self.queue or any(i not in self.samples for i in self.queue):raise ActionRejected('EMPTY_SLOT')
  if self._oxygen_supply+1e-15<len(self.queue)*self.method['oxygen_dose_mol']:raise ActionRejected('OXYGEN_SUPPLY_LIMIT')
  blank=self._blank_valid();selected=[ELEMENTS.index(e) for e in MODES[self.method['mode']]]
  for i in self.queue:
   q=self.samples[i]
   if not all(q['known'][j] for j in selected):raise ActionRejected('UNREPORTED_MODE_ELEMENT')
   if q['input']['kind']=='blank':blank=True
   elif not blank:raise ActionRejected('MATCHED_BLANK_REQUIRED')
  self._queue=list(self.queue);self._index=0;self.completed_assays=[];self._active=None;self._run_method=copy.deepcopy(self.method);self._run_calibration=copy.deepcopy(self._calibration);self._run_key=self._key();self.phase='running';self.elapsed_s=0.;self.warning='';self._signal=None;self._last_point=None;self._active_element='';self._op_count+=1;self.operation_id=f'{self.instance_id}:sequence:{self._op_count}';self._begin_sample();return dict(phase=self.phase,operation_id=self.operation_id)
 def _begin_sample(self):
  q=self.samples[self._queue[self._index]];a=q['input'];selected=MODES[self._run_method['mode']]
  self._active=dict(slot_index=a['slot_index'],kind=a['kind'],status='running',mass_kg=a['mass_kg'],areas_s={e:0. if e in selected else None for e in ELEMENTS},fractions=_nulls(),fraction_quality={e:'not_completed' if e in selected else 'not_measured_in_mode' for e in ELEMENTS},source_fraction_evidence=copy.deepcopy(q['evidence']),chromatogram=[],settings_json=self._run_key,calibration_json=json.dumps(self._run_calibration,sort_keys=True),limitations=copy.deepcopy(LIMITATIONS))
  self._baseline_samples=[];self._active_element='';self._enter('carousel')
 def _enter(self,stage):
  self.stage=stage;self._stage_elapsed=0.;self._next_detector=None;self._pending_area=0.
  if stage in ['baseline','release']:
   self._detector_start=self.time_s;self._next_detector=self.time_s+self.plant['detector_period_s']
   if stage=='release':self._active_element=self._release_order[self._release_index];self._baseline_count=0
 def _stop(self,why,failed=False):
  self.phase='failed' if failed else 'cancelled';self.warning=why;self.stage='idle';self.ball=0.;self.oxygen_angle=0.;self._next_detector=None
  if self._active:self._active['status']=self.phase
  # Active oxygen and sample remain explicitly quarantined. No unobserved terminal peak is fabricated.
 def _thermal(self,dt):
  if not self.plant:return
  eq=self.targets if self.powered and not self.opened and self.extension==0 else np.full(2,self.plant['ambient_k']);a=-math.expm1(-self.plant['furnace_conductance_w_k']/self.plant['furnace_capacity_j_k']*dt);delta=(eq-self.temperature)*a;self.temperature+=delta;self.furnace_heat+=float(delta.sum())*self.plant['furnace_capacity_j_k']
 def _physical(self,dt):
  self._thermal(dt)
  if not self.plant:return
  # Previously completed APT gates leave finite tails; these remain mass, and can contaminate later observed gates.
  if self.powered and self.plant['carrier_ready'] and self.plant['heated_line_ready']:
   released=self._tails*(-np.expm1(-dt/np.array([self.plant['release_tau_s'][e] for e in ELEMENTS])));self._tails-=released;self._exhaust+=released
   if self.phase=='running' and self.stage in ['baseline','release']:self._pending_area+=float(sum(released[j]*self.plant['tcd_gain_s_kg'][e] for j,e in enumerate(ELEMENTS)))
  if self.phase!='running':return
  self.elapsed_s+=dt;self._stage_elapsed+=dt
  if self.stage=='carousel':
   target=self._queue[self._index]*2*math.pi/80;dist=target-self.carousel;self.carousel+=math.copysign(min(abs(dist),self.plant['carousel_speed_rad_s']*dt),dist)
  elif self.stage=='ball_open':self.ball=min(math.pi/2,self.ball+self.plant['sample_valve_speed_rad_s']*dt)
  elif self.stage=='ball_close':self.ball=max(0.,self.ball-self.plant['sample_valve_speed_rad_s']*dt)
  elif self.stage=='oxygen':
   flow=self._run_method['oxygen_dose_mol']/self._run_method['oxygen_admission_s'];dose=min(self._oxygen_supply,flow*dt);self._oxygen_supply-=dose;self._oxygen_chamber+=dose;self.oxygen_angle=math.pi/2
  elif self.stage=='combustion':
   q=self._current;converted=q['unconverted']*(-math.expm1(-self.plant['combustion_rate_s_inv']*dt));need=float(converted@O2_PER_ELEMENT_KG)
   if need>self._oxygen_chamber and need>0:converted*=self._oxygen_chamber/need;need=self._oxygen_chamber
   q['unconverted']-=converted;self._oxygen_chamber=max(0.,self._oxygen_chamber-need);self._oxygen_consumed+=need
   for j,e in enumerate(ELEMENTS):
    if e in MODES[self._run_method['mode']]:q['trapped'][j]+=converted[j]
    else:self._unselected[j]+=converted[j]
  elif self.stage=='release':
   j=ELEMENTS.index(self._active_element);mass=self._current['trapped'][j]*(-math.expm1(-dt/self.plant['release_tau_s'][self._active_element]));self._current['trapped'][j]-=mass;self._exhaust[j]+=mass;self._pending_area+=mass*self.plant['tcd_gain_s_kg'][self._active_element]
 def _due(self):
  p=self.plant;m=self._run_method;s=self.stage
  if s=='carousel':return abs(self._queue[self._index]*2*math.pi/80-self.carousel)/p['carousel_speed_rad_s']
  if s=='ball_open':return (math.pi/2-self.ball)/p['sample_valve_speed_rad_s']
  if s=='ball_close':return self.ball/p['sample_valve_speed_rad_s']
  if s in ['baseline','release']:
   time=max(0.,self._next_detector-self.time_s)
   return min(time,max(0.,m['maximum_release_s']-self._stage_elapsed)) if s=='release' else time
  return max(0.,{'oxygen':m['oxygen_admission_s'],'combustion':m['combustion_s'],'reduction':p['reduction_time_s']}[s]-self._stage_elapsed)
 def _event(self):
  if self.stage=='carousel':self._enter('baseline')
  elif self.stage=='baseline':
   self._sample_detector(baseline=True)
   if len(self._baseline_samples)>=max(3,self._run_method['baseline_samples']):
    self._baseline=float(np.mean(self._baseline_samples)) if self._active['kind']=='blank' else self._blank['baseline'];self._enter('ball_open')
  elif self.stage=='ball_open':
   q=self.samples.pop(self._queue[self._index]);self._current=dict(q,unconverted=q['elements'].copy(),trapped=np.zeros(4));self._enter('ball_close')
  elif self.stage=='ball_close':self.oxygen_angle=math.pi/2;self._enter('oxygen')
  elif self.stage=='oxygen':self.oxygen_angle=0.;self._enter('combustion')
  elif self.stage=='combustion':self._enter('reduction')
  elif self.stage=='reduction':
   self._release_order=[e for e in ORDER if e in MODES[self._run_method['mode']]];self._release_index=0;self._enter('release')
  elif self.stage=='release':
   if self._next_detector<=self.time_s+1e-11:self._sample_detector()
   if self._stage_elapsed+1e-12>=self._run_method['minimum_release_s'] and self._baseline_count>=self._run_method['baseline_samples']:
    j=ELEMENTS.index(self._active_element);self._tails[j]+=self._current['trapped'][j];self._current['trapped'][j]=0.;self._release_index+=1
    if self._release_index<len(self._release_order):self._enter('release')
    else:self._finish_sample()
   elif self._stage_elapsed+1e-12>=self._run_method['maximum_release_s']:self._stop('APT_BASELINE_TIMEOUT',failed=True)
 def _sample_detector(self,baseline=False):
  duration=self.time_s-self._detector_start
  if duration<=0:raise RuntimeError('nonpositive detector window')
  signal=self.plant['tcd_baseline']+self._pending_area/duration+float(self._rng.normal(0,self.plant['tcd_noise_sd']));corrected=signal-(self._baseline if not baseline else 0.)
  point=dict(element='baseline' if baseline else self._active_element,time_s=self.time_s,sample_window_s=[self._detector_start,self.time_s],signal=signal,corrected_signal=corrected);self._active['chromatogram'].append(point);self._last_point=point;self._signal=signal
  if baseline:self._baseline_samples.append(signal)
  else:
   self._active['areas_s'][self._active_element]+=corrected*duration
   self._baseline_count=self._baseline_count+1 if abs(corrected)<=self._run_method['baseline_threshold'] else 0
  self._pending_area=0.;self._detector_start=self.time_s;self._next_detector=self.time_s+self.plant['detector_period_s']
 def _finish_sample(self):
  q=self._active;inp=self._current['input'];q['status']='succeeded'
  if q['kind']=='blank':self._blank=dict(key=self._run_key,baseline=self._baseline,areas=copy.deepcopy(q['areas_s']))
  for e in MODES[self._run_method['mode']]:
   cal=self._run_calibration.get(e);quality='blank_no_sample' if q['kind']=='blank' else 'calibration_unavailable'
   if q['kind']!='blank' and cal and cal['key']==self._run_key:
    if cal['basis']!=inp['mass_basis']:quality='calibration_basis_mismatch'
    else:
     mass=(np.longdouble(q['areas_s'][e])-cal['intercept_s'])/cal['slope_s_kg'];fraction=mass/q['mass_kg'];bounds=cal['mass_range_kg'];tol=1e-9*max(bounds)
     if mass<bounds[0]-tol or mass>bounds[1]+tol:quality='outside_calibrated_element_mass_range'
     elif not np.isfinite(fraction) or fraction<0 or fraction>1:quality='nonphysical_observed_fraction'
     else:q['fractions'][e]=float(fraction);quality='estimated_from_observed_calibration'
   q['fraction_quality'][e]=quality
  self.completed_assays.append(copy.deepcopy(q));self._retained+=self._current['unconverted'];self._u_retained+=self._current['remainder'];self._oxygen_vented+=self._oxygen_chamber;self._oxygen_chamber=0.;self._current=None;self._active=None;self._index+=1;self._active_element='';self.ball=0.;self.oxygen_angle=0.
  if self._index<len(self._queue):self._begin_sample()
  else:self.phase='succeeded';self.stage='idle';self._next_detector=None
 def step(self,dt_s):
  validate(number('s',0,86400),dt_s)
  if dt_s==0:return self.read()
  target=self.time_s+dt_s;events=0
  while self.time_s<target-1e-12 or self.phase=='running' and self._due()<=1e-12:
   if self.phase!='running':self._physical(target-self.time_s);self.time_s=target;break
   remaining=max(0.,target-self.time_s);due=max(0.,self._due());delta=min(remaining,due)
   if delta>0:self._physical(delta);self.time_s+=delta
   if due<=delta+1e-12:self._event();events+=1
   else:break
   if events>2000000:raise RuntimeError('event resource limit')
   if self.time_s>=target-1e-12 and (self.phase!='running' or self._due()>1e-12):break
  if self.time_s<target:
   self._physical(target-self.time_s);self.time_s=target
  self.revision+=1;self._archive_operation();return self.read()
 def _operation_snapshot(self):return dict(protocol_version=PROTOCOL_VERSION,instance_id=self.instance_id,operation_id=self.operation_id,status=self.phase,state_revision=self.revision,clock={'domain':'simulation','time_s':self.time_s},result=self._result(),observations=self.session().read(instance_id=self.instance_id)['observations'],error=dict(code=self.warning,message=self.warning) if self.phase=='failed' else None)
 def _archive_operation(self):
  if self.operation_id and self.phase in ['succeeded','cancelled','failed'] and self.operation_id not in self._operation_records:self._operation_records[self.operation_id]=self._operation_snapshot()

class ElementarVarioelSession:
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
        m=export_elementar_varioel_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.1',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        def timing(key):
            if key in ['detector.tcd_signal','detector.last_sample_time_s'] and h._last_point:return h._last_point['time_s'],h._last_point['sample_window_s'] if key=='detector.tcd_signal' else None
            if key.startswith('result.') and h.completed_assays:
                e=key.split('.')[1][0].upper();points=[p for p in h.completed_assays[-1]['chromatogram'] if p['element']==e]
                if points:return points[-1]['time_s'],[points[0]['sample_window_s'][0],points[-1]['time_s']]
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
        key=(self._principal,h.instance_id,request_id);digest=json.dumps(['sequence.cancel',operation_id])
        if key in h._requests:
            old,response=h._requests[key]
            if old!=digest:raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(response)
        result=h.invoke('sequence.stop') if snapshot['status']=='running' else {'phase':snapshot['status']}
        response=dict(protocol_version=PROTOCOL_VERSION,instance_id=h.instance_id,request_id=request_id,operation_id=operation_id,status='succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None)
        h._requests[key]=(digest,copy.deepcopy(response));return response

LIMITATIONS=[
'Documented vario EL cube body480x550x570mm; body mass unknown. Standard80-position open carousel; optional60/120 positions unsupported. Front door and detachable maintenance furnace carriage are engineering scene proxies, not a robot maintenance mechanism.',
'CHNS/CHN/CNS/CN supported. Current organic20mg, inorganic1.5g and absoluteC40mg/H3mg/N15mg/S6mg limits enforced; for unreported elements the entire unreported remainder is a conservative capacity upper bound, not an actual element estimate.',
'Furnace target max1473.15K(1200C). Tin capsule local flash1800C is not a furnace setpoint. Two first-order furnace lumps, accelerated example heat capacity/conductance, finite carousel/ball-valve speed and oxygen pulse are host assumptions, not OEM timing or thermal calibration. Model reaction requires combustion target>=873.15K and reduction target>=673.15K as explicit conservative engineering cutoffs, not manufacturer limits; no Arrhenius temperature dependence or full conversion guarantee.',
'Reported source mass fractions are real Found/manufacturer observations. Molecular formulas/calculated composition never populate measured fractions. Source basis and unknown source conditions remain explicit. Measured references are not certified reference materials; manufacturer unspecified +/- values are not standard deviations.',
'CHN with missing S returns S=null; trackedS=0 means no reported/assumed S inventory, never actual zero S. All unreported remainder retains unknown identity and phase, never oxygen, purity or ash inferred by subtraction.',
'Finite lumped conversion dM/dt=-kM limited by explicitly external reagent O2. Nominal tracked C/H/S need respectively1,1/4,1 molO2 per atom mol to formCO2/H2O/SO2; N maps nominally toN2. Unknown sample oxygen and remainder chemistry are not inferred. No whole-gas mass reconstruction or oxide chemistry is claimed.',
'Reduction is an explicitly assumed ideal gas conversion/transport delay, not copper capacity, NOx reaction kinetics or oxygen-removal cartridge chemistry. Heated lines/carrier readiness are host conditions; actual condensation, flow, leak detection and gas safety are not modeled.',
'APT orderN2->CO2->H2O->SO2, omitted channels removed from sequence. N2 uses direct transport buffer, not an adsorption column. Three adsorbent-specific gas buffers use host exponential releases. Each next gate waits for observed prior TCD signal to remain within the baseline threshold; no fixed overlapping Gaussian GC peaks.',
'Finite tail inventory continues to decay to the observed detector and exhaust, including contamination below the chosen baseline criterion. Residual unconverted elements and unreported remainder are retained in explicit residue accounts. Mode-excluded tracked products have a separate unselected-exhaust account.',
'Equivalent TCD relative signal is baseline + sum(gain_s_per_kg * released_mass/detector_window_s) + host Gaussian per-window noise. It is not calibrated volts or a measured chromatogram. Noise is independent of source precision claim<0.1percentage point (fraction0.001). Full noise/time-step/physical-parameter convergence is not established.',
'Complete blank captures observed pre-injection baseline at matched method and furnace targets; sample areas integrate signal minus that observed baseline, avoiding wrong subtraction of fixed areas across different gate durations. Background drift, detector nonlinearity and unknown carrier cross-sensitivity are not modeled.',
'Calibration uses at least3 caller-supplied standard areas with real reported reference fractions, stable affine fit versus absolute element mass, positive finite slope, matching method/furnace/basis and bounded interpolation. No sample-identity look-up in analysis; uncalibrated, extrapolated, nonphysical or unselected fractions are null, not clipped.',
'Each actual weighed capsule is consumed once at ball-valve transfer. Cancellation retains partial observations and quarantined reactor mass. Cold power-off, open-door, extended-furnace service may recover remaining sample-origin tracked/unreported mass; already exhausted material is never returned, and returned mass excludes externally supplied oxygen.',
'Cancellation freezes reaction/release except previously detached transport tails; passive furnace cooling continues. Self-sustaining hot combustion and emergency shutdown chemistry are not modeled. Therefore logical interlocks are not hardware safety certification.',
'Net heat ledger sums two lump changes C*dT, with ambient losses/heater input represented only through net transfer; it is not total electrical energy, oxygen cylinder enthalpy or full thermochemical conservation.',
'Pure reads/exports neither advance time nor draw noise. Historical terminal operations retain complete assay arrays, original method/calibration and exact detector windows under original principal ownership. Permissions and request replay are process-local, not persistence or OS security.',
'O/Cl/TIC options, certified CRM/factory precision validation, real TCD calibration/peak traces, actual pneumatic/adsorption/NOx chemistry, robot contact and hardware/HIL remain not_run.'
]

def export_elementar_varioel_contract_metadata():
 caps={};slots={};fractions=['result.'+e.lower()+'_fraction' for e in ELEMENTS];sequence=['sequence.phase','sequence.stage','sequence.progress','sequence.elapsed_s','sequence.current_slot','sequence.completed_samples'];detector=['detector.tcd_signal','detector.active_element','detector.last_sample_time_s'];thermal=['temperature.actual_k','reduction.actual_k','furnace.ready','truth.ledger'];valves=['sample_valve.angle_rad','oxygen_valve.angle_rad']
 immediate={'plant.configure':['plant.configured','temperature.actual_k','reduction.actual_k','temperature.target_k','reduction.target_k','truth.ledger'],'sample.load':['truth.ledger'],'sample.unload':['truth.ledger'],'cover.set':['cover.opened','cover.angle_rad','furnace.ready','sequence.phase','sequence.stage','safety.warning',*valves],'power.set':['power.enabled','furnace.ready','sequence.phase','sequence.stage','safety.warning',*valves],'furnace.configure':['temperature.target_k','reduction.target_k','furnace.ready','blank.valid','calibration.ready'],'furnace.extend':['furnace.extension_m','furnace.ready'],'method.configure':['method.mode','blank.valid','calibration.ready','furnace.ready'],'sequence.start':[*sequence,*detector,*fractions,'safety.warning'],'sequence.stop':['sequence.phase','sequence.stage',*valves,'safety.warning'],'calibration.fit':['calibration.ready']}
 onstep={'power.set':thermal,'cover.set':thermal,'furnace.configure':thermal,'sequence.start':[*sequence,*detector,*fractions,*valves,'autosampler.angle_rad','blank.valid','safety.warning',*thermal]}
 completion={'sequence.start':[*sequence,*fractions,*valves,'blank.valid','truth.ledger']}
 observes={'plant.configure':['plant.configured'],'cover.set':['cover.opened'],'power.set':['power.enabled'],'furnace.extend':['furnace.extension_m'],'sequence.start':['sequence.phase'],'sequence.stop':['sequence.phase'],'process.measure':[*sequence,*detector,*fractions,'safety.warning'],'result.read':['sequence.phase','sequence.stage',*fractions],'result.export':['sequence.phase','sequence.stage',*fractions],'chromatogram.read':detector,'blank.read':['blank.valid'],'calibration.read':['calibration.ready'],'product.read':['truth.ledger']}
 for k,(s,u,o,v,c,w) in SLOTS.items():
  mean=k=='detector.tcd_signal' or k in fractions
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'ElementarVarioelSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='interval_mean' if mean else 'instant',time_domain='simulation',window_description='TCD为最近完整检测积分窗；元素分数为对应完整APT峰面积经标线换算，时间窗覆盖该元素所有检测窗，非逐瞬时浓度平均' if mean else None),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','INVALID_REFERENCE','SOURCE_COMPOSITION_RANGE','INITIALIZATION_ONLY','EVIDENCE_ACK_REQUIRED','PLANT_REQUIRED','BUSY','SLOT_OCCUPIED','BLANK_COMPOSITION','SAMPLE_MASS_LIMIT','SOURCE_BASIS_MISMATCH','UNKNOWN_CONDITIONS_ACK_REQUIRED','REPORTED_VALUE_OVERRIDE','MISSING_ELEMENT_ACK_REQUIRED','ELEMENT_MASS_LIMIT','RECIPIENT_REQUIRED','COLD_SERVICE_REQUIRED','EMPTY_SLOT','RETRACT_FURNACE_REQUIRED','POWER_INTERLOCK','RELEASE_TIME_RANGE','DUPLICATE_SLOT','MATCHED_BLANK_REQUIRED','CALIBRATION_ACK_REQUIRED','ELEMENT_NOT_IN_MODE','MEASURED_REFERENCE_REQUIRED','DEGENERATE_CALIBRATION','CALIBRATION_RANGE','RECOVER_CANCELLED_RESIDUE_REQUIRED','FURNACE_NOT_READY','REACTION_TEMPERATURE_LIMIT','EVENT_RESOURCE_LIMIT','GAS_PATH_NOT_READY','OXYGEN_SUPPLY_LIMIT','UNREPORTED_MODE_ELEMENT']
 for k,(inp,out,c,z,kind,vis,ex,_) in CAPS.items():
  async_=k=='sequence.start';query=kind=='query';unsupported=k in UNSUPPORTED
  effects=[dict(state_id=state,timing=timing,description_zh={'immediate':'经权限与物料/温度检查的设定、装卸、逻辑互锁、初始化或取消；不推进时间','on_step':'有限炉温/机械运动、燃烧与APT检测窗推进；采样事件才产生噪声','on_completion':'全部声明样品或单个样品完成后保存完整峰和质量分数；完整空白此时可用'}[timing]) for timing,mapping in [('immediate',immediate),('on_step',onstep),('on_completion',completion)] for state in mapping.get(k,[])]
  caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.elementar_varioel.ElementarVarioelSession.invoke',invocation=f'session.invoke(instance_id="varioel_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if query or unsupported else [dict(description_zh='宿主权限、真实参考/缺项证据、称样和绝对元素容量、冷维护与双炉、气路、匹配空白及一次消费槽位均原子检查',state_ids=['power.enabled','cover.opened','furnace.ready','blank.valid','sequence.phase'],enforced_by='runtime',check='Runtime._invoke / host-assigned Session principal')],state_effects=[] if query or unsupported else effects,observes=[] if unsupported else observes.get(k,[]),model_effects=dict(mode='none',description_zh='过程状态与独立场景绑定另验；查询无动作',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='顺序进样、有限反应和按基线返回的所选N/C/H/S峰均完成；超时明确failed并保留残料与数据' if async_ else '返回原子事务，不推进时钟',completion_state_ids=['sequence.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','consumable_capsules','finite_oxygen_inventory','apt_columns','equivalent_tcd','request_cache'],partial_failure_zh='取消或APT基线超时保留完成和部分峰、样源/氧仓及原方法；未完成元素无伪结果；历史按原主体保护'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=LIMITATIONS,source_ids=['manufacturer_primary','simulation_assumption'])
 mech=dict(component_ids=['autosampler_carousel','ball_valve','oxygen_valve','combustion_furnace','reduction_furnace','co2_column','water_column','so2_column','tcd_detector'],state_ids=list(slots),meaning_zh='一次消费样品的CHNS元素仓、有限热学/燃烧还原和基线返回顺序APT检测及真实参考观察标定',implementation='lumped',equations=['T(t+dt)=T_eq+(T(t)-T_eq)*exp(-H*dt/C); Q_net=sum(C*dT).','dM_unconverted/dt=-k*M_unconverted, conversion limited by supplied external O2; unreported sample remainder never inferred as oxygen.','Known/assumed element registered=tray+unconverted+trapped+tails+retained_residue+detected_exhaust+unselected_exhaust+removed.','DirectN2, then CO2/H2O/SO2: dM_buffer/dt=-M_buffer/tau; next release waits consecutive observed baseline windows.','TCD=baseline+sum(gain_s_kg*released_mass_kg/window_s)+host_noise; area_s=sum((TCD-observed_blank_baseline)*window_s).','Fit area_s=slope_s_kg*reference_sample_mass_kg*reported_fraction+intercept_s; fraction=(observed_area-intercept)/(slope*weighed_sample_mass).','Source missing elements return null unless explicitly assumed; unknown remainder is an unresolved mass compartment.'],parameters={},source_ids=['manufacturer_primary','simulation_assumption'],limitations=LIMITATIONS)
 spec=importlib.util.spec_from_file_location('_varioel_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'combustion_apt_chns':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'；非原厂校准。'
 mech['parameters']={k.lower():v for k,v in mech['parameters'].items()};return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'combustion_apt_chns':mech})

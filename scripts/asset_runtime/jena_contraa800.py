"""contrAA 800 D: liquid flame/graphite-furnace elemental absorption, explicitly assumed dynamics."""
from __future__ import annotations
import copy, importlib.util, json, math
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
SLUG='jena_contraa800'; ASSET_TYPE_ID='instrument.'+SLUG; CATALOG_ID='CHEM-081'; PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/expansion_batch03/t3_analytical/jena_contraa800/output_templates/library.json'
BOOL={'type':'boolean'}; TEXT={'type':'string'}
class ActionRejected(ValueError): pass

def number(unit,low=None,high=None):
 s={'type':'number','x-unit':unit}
 if low is not None:s['minimum']=low
 if high is not None:s['maximum']=high
 return s
def obj(p):return dict(type='object',properties=p,required=list(p),additionalProperties=False)
def arr(s):return dict(type='array',items=s)
def integer(lo,hi):return dict(number('1',lo,hi),type='integer')
def enum(v):return dict(type='string',enum=v)
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
def vector(s,lo=1,hi=10000):return dict(arr(s),minItems=lo,maxItems=hi)
def validate(s,v):
 if 'anyOf' in s:
  if v is None and any(x.get('type')=='null' for x in s['anyOf']):return
  return validate(next(x for x in s['anyOf'] if x.get('type')!='null'),v)
 t=s.get('type')
 if t in ('number','integer'):
  if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):raise ActionRejected('INVALID_ARGUMENT')
  if t=='integer' and type(v) is not int:raise ActionRejected('INVALID_ARGUMENT')
  if not s.get('minimum',-math.inf)<=v<=s.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='boolean' and type(v) is not bool:raise ActionRejected('INVALID_ARGUMENT')
 elif t=='string' and not isinstance(v,str):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='null' and v is not None:raise ActionRejected('INVALID_ARGUMENT')
 elif t=='object':
  if not isinstance(v,dict) or not set(s['required'])<=v.keys() or not v.keys()<=s['properties'].keys():raise ActionRejected('INVALID_ARGUMENT')
  for k,x in v.items():validate(s['properties'][k],x)
 elif t=='array':
  if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
  for x in v:validate(s['items'],x)
 if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')

RESULT_CAPACITY=256; CURVE_CAPACITY=32; MAX_TRACE_POINTS=100000
PHASE=enum(['idle','running','succeeded','cancelled','failed'])
PLANT=obj({'ambient_k':number('K',278.15,313.15),'furnace_capacity_j_k':number('J/K',.01,1e4),'cooling_rate_k_s':number('K/s',.01,3000),'liquid_rate_m3_s':number('m^3/s',1e-10,1e-5),'motion_speed_m_s':number('m/s',.001,1),'rotation_speed_rad_s':number('rad/s',.01,100),'flame_ignition_s':number('s',.01,30),'nebulization_fraction':number('1',0,1),'atomization_fraction':number('1',0,1),'atomization_rate_s_inv':number('1/s',.001,1000),'pyrolysis_loss_rate_s_inv':number('1/s',0,100),'drying_rate_s_inv':number('1/s',.01,1000),'beam_residence_s':number('s',.001,10),'absorption_gain_kg_inv':number('1/kg',1,1e15),'line_sigma_m':number('m',1e-13,1e-9),'detector_period_s':number('s',.001,1),'detector_noise_sd':number('1',0,.1),'detector_baseline_absorbance':number('1',-1,1),'detector_transmission_floor':number('1',1e-12,.1),'fuel_inventory_mol':number('mol',0,1000),'oxidant_inventory_mol':number('mol',0,10000),'argon_inventory_mol':number('mol',0,10000),'fuel_flow_mol_s':number('mol/s',1e-8,.1),'oxidant_flow_mol_s':number('mol/s',1e-8,1),'argon_flow_mol_s':number('mol/s',1e-8,.1),'coolant_ready':BOOL,'exhaust_ready':BOOL,'burner_recognized':BOOL,'siphon_ready':BOOL,'seed':integer(0,2147483647),'accept_assumed_response':BOOL})
PLANT_EXAMPLE=dict(ambient_k=298.15,furnace_capacity_j_k=2.,cooling_rate_k_s=2000.,liquid_rate_m3_s=2e-7,motion_speed_m_s=.4,rotation_speed_rad_s=12.,flame_ignition_s=.05,nebulization_fraction=.05,atomization_fraction=.95,atomization_rate_s_inv=80.,pyrolysis_loss_rate_s_inv=0.,drying_rate_s_inv=50.,beam_residence_s=.02,absorption_gain_kg_inv=1e10,line_sigma_m=1.5e-12,detector_period_s=.005,detector_noise_sd=1e-6,detector_baseline_absorbance=.002,detector_transmission_floor=1e-7,fuel_inventory_mol=.1,oxidant_inventory_mol=1.,argon_inventory_mol=.1,fuel_flow_mol_s=.001,oxidant_flow_mol_s=.01,argon_flow_mol_s=.001,coolant_ready=True,exhaust_ready=True,burner_recognized=True,siphon_ready=True,seed=81,accept_assumed_response=True)
SAMPLE=obj({'sample_id':TEXT,'kind':enum(['blank','standard','sample']),'template_id':TEXT,'volume_m3':number('m^3',1e-9,.1),'vial_index':integer(0,107),'dilution_factor':number('1',1,1e6),'background_absorbance':number('1',0,10),'background_slope':number('1',-1,1),'accept_unknown_conditions':BOOL,'accept_assumed_matrix':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='external_solution',kind='sample',template_id='aj_wastewater_inlet_cu',volume_m3=1e-5,vial_index=0,dilution_factor=1.,background_absorbance=.02,background_slope=.005,accept_unknown_conditions=True,accept_assumed_matrix=True)
BLANK_EXAMPLE=dict(SAMPLE_EXAMPLE,sample_id='external_element_free_blank',kind='blank',template_id='',background_absorbance=0.,background_slope=0.)
STANDARD_EXAMPLE=dict(SAMPLE_EXAMPLE,sample_id='external_reference',kind='standard',template_id='aj_wastewater_std3_cu')
PROGRAM_STEP=obj({'stage':enum(['dry','pyrolysis','atomize','clean']),'temperature_k':number('K',278.15,3273.15),'ramp_k_s':number('K/s',.01,3000),'hold_s':number('s',.005,120),'argon_on':BOOL})
FURNACE_PROGRAM_EXAMPLE=[dict(stage=n,temperature_k=t,ramp_k_s=3000.,hold_s=h,argon_on=n!='atomize') for n,t,h in [('dry',393.15,.08),('pyrolysis',673.15,.04),('atomize',2273.15,.15),('clean',2473.15,.04)]]
METHOD=obj({'mode':enum(['flame','furnace']),'element':TEXT,'line_id':TEXT,'wavelength_m':number('m',185e-9,900e-9),'background':enum(['off','linear_reference']),'injection_volume_m3':number('m^3',1e-9,1e-4),'repeats':integer(1,10),'baseline_s':number('s',.005,10),'flush_s':number('s',.01,30),'furnace_program':vector(PROGRAM_STEP,4,4)})
METHOD_EXAMPLE=dict(mode='flame',element='Cu',line_id='cu_324754',wavelength_m=324.754e-9,background='linear_reference',injection_volume_m3=1e-7,repeats=1,baseline_s=.02,flush_s=.2,furnace_program=copy.deepcopy(FURNACE_PROGRAM_EXAMPLE))
FURNACE_METHOD_EXAMPLE=dict(METHOD_EXAMPLE,mode='furnace',injection_volume_m3=20e-9)
BURNER=obj({'height_m':number('m',.004,.016),'angle_rad':number('rad',0,math.pi/2)})
BURNER_EXAMPLE={'height_m':.010,'angle_rad':0.}
POINT=obj({'time_s':number('s',0),'sample_window_s':arr(number('s',0)),'absorbance':number('1'),'background_absorbance':number('1'),'corrected_absorbance':number('1'),'acquiring':BOOL,'saturated':BOOL})
ASSAY=obj({'repeat_index':integer(0,9),'phase':TEXT,'mode':TEXT,'element':TEXT,'line_id':TEXT,'injection_volume_m3':number('m^3',0),'integrated_absorbance_s':number('s'),'concentration_kg_m3':nullable(number('kg/m^3')),'quality':TEXT,'points':arr(POINT),'settings_json':TEXT,'calibration_json':TEXT})
RESULT=obj({'phase':PHASE,'stage':TEXT,'complete':BOOL,'mode':TEXT,'element':TEXT,'line_id':TEXT,'concentration_kg_m3':nullable(number('kg/m^3')),'sd_kg_m3':nullable(number('kg/m^3')),'cv':nullable(number('1')),'assays':arr(ASSAY),'active_assay':nullable(ASSAY),'qc':arr(TEXT),'evidence':TEXT,'template_source':TEXT})
MEASURE=obj({'phase':PHASE,'stage':TEXT,'elapsed_s':number('s',0),'progress':number('1',0,1),'absorbance':nullable(number('1')),'background_absorbance':nullable(number('1')),'concentration_kg_m3':nullable(number('kg/m^3')),'warning':TEXT})
SPECTRUM=obj({'wavelength_m':arr(number('m')),'normalized_transmission':arr(number('1',0)),'apparent_absorbance':arr(number('1')),'sample_window_s':nullable(arr(number('s',0))),'evidence':TEXT})
CAL_INPUT=obj({'curve_id':TEXT,'observations':vector(obj({'reference_id':TEXT,'dilution_factor':number('1',1,1e6),'injection_volume_m3':number('m^3',1e-9,1e-4),'integrated_absorbance_s':number('s')}),3,100),'accept_same_response_conditions':BOOL})
CAL_OUTPUT=obj({'curve_id':TEXT,'slope_s_kg':number('s/kg',0),'intercept_s':number('s'),'r_squared':number('1'),'element_mass_range_kg':arr(number('kg',0)),'evidence':TEXT})
CALIBRATION_EXAMPLE={'curve_id':'illustrative_cu','observations':[dict(reference_id=STANDARD_EXAMPLE['template_id'],dilution_factor=d,injection_volume_m3=1e-7,integrated_absorbance_s=a) for d,a in [(10.,.0001),(2.,.0005),(1.,.001)]],'accept_same_response_conditions':True}
WAREHOUSES=['registered','sample','pipette','tube','beam','deposit','exhaust','waste','removed','residual']
LEDGER=obj({'element_mass_kg':obj({k:number('kg') for k in WAREHOUSES}),'liquid_volume_m3':obj({k:number('m^3') for k in ['registered','sample','pipette','tube','waste','evaporated_liquid_equivalent','removed','residual']}),'fuel_mol':obj({k:number('mol') for k in ['initial','remaining','consumed','residual']}),'oxidant_mol':obj({k:number('mol') for k in ['initial','remaining','consumed','residual']}),'argon_mol':obj({k:number('mol') for k in ['initial','remaining','consumed','residual']}),'net_furnace_heat_j':number('J'),'stored_furnace_heat_j':number('J'),'thermal_residual_j':number('J'),'active_template_id':nullable(TEXT),'evidence':TEXT})
TEMPLATE=obj({'substance_id':TEXT,'template_json':TEXT})
CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None):CAPS[k]=[i,o,c,z,kind,vis,{} if ex is None else ex,[]]
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body','graphite_furnace','ccd_detector'],'可信宿主声明响应/热学/雾化/背景与燃气库存；厂家分辨率与重现性不充当噪声','configuration','evaluator',PLANT_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_inlet'],'装入有真实元素液体浓度的外部样品；未知基体响应显式接受','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.unload',obj({'recipient_id':TEXT}),obj({'unloaded':BOOL,'volume_m3':number('m^3',0)}),['sample_inlet'],'剩余溶液交回宿主，已消耗元素不复原','device_command','evaluator',{'recipient_id':'external_bottle'})
cap('fluidics.clean',obj({}),obj({'removed_mass_kg':number('kg',0),'removed_volume_m3':number('m^3',0)}),['spray_chamber','graphite_tube'],'冷态外部清理残液/沉积到废弃仓；非自动AS-GF完整清洗','device_command','evaluator')
cap('cover.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['sample_door'],'安全玻璃沿+Z滑开：火焰测量必须关闭，AS-GF炉进样必须打开；热态/运行时不切换','device_command','evaluator',{'opened':False})
cap('furnace.open',obj({'opened':BOOL}),obj({'opened':BOOL}),['furnace_jaw'],'冷态炉头开合装管维护，非固体进样附件','device_command','evaluator',{'opened':False})
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','button_power','xe_lamp'],'逻辑供电；断电终止光学采集与熄火，保留已耗样本',ex={'enabled':True})
cap('method.configure',METHOD,obj({'configured':BOOL}),['atomizer_swivel','echelle_grating','graphite_furnace'],'快顺序元素线/火焰或炉、独立背景代理和四步有限温程；非同时多元素','configuration','agent',METHOD_EXAMPLE)
cap('burner.configure',BURNER,obj({'configured':BOOL}),['burner_lift','burner_head'],'厂家4–16mm高度、0–90°旋转目标，step有限移动；不称喷雾流场标定','configuration','agent',BURNER_EXAMPLE)
cap('flame.ignite',obj({}),obj({'igniting':BOOL}),['burner_head','gas_manifold'],'气源/排风/虹吸/炉切换/关门互锁后有限点火；空气乙炔当前配置')
cap('flame.extinguish',obj({}),obj({'lit':BOOL}),['burner_head','gas_manifold'],'熄火并取消火焰测量；不模拟实机紧急吹扫细节')
cap('measurement.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['atomizer_swivel','sampler_tray','sampler_arm','sampler_probe','burner_head','graphite_tube','ccd_detector'],'实际有限取样与原子化，21点局部合成CCD观测及面积标定，不直接返回源浓度')
cap('measurement.stop',obj({}),obj({'phase':PHASE}),['sampler_probe','graphite_furnace'],'终止采集，保留已采点和元素/体积仓；炉冷却与残气继续由step推进')
cap('process.measure',obj({}),MEASURE,['display_panel'],'只读进度与最新观测','query')
cap('result.read',obj({}),RESULT,['display_panel'],'完整原方法/标线/每重复观察峰和浓度QC','query')
cap('spectrum.read',obj({}),SPECTRUM,['ccd_detector'],'最近完整采样窗21点工程局部光谱，不等于200像素厂原始CCD','query')
cap('result.export',obj({'format':enum(['json','csv'])}),obj({'format':TEXT,'content':TEXT}),['display_panel'],'纯JSON或CSV返回；不写文件或调用真实ASpect CS','query','agent',{'format':'json'})
cap('blank.read',obj({}),obj({'valid':BOOL,'blank_json':TEXT}),['display_panel'],'完整空白获得当前响应签名基线；取消空白不启用','query')
cap('calibration.fit',CAL_INPUT,CAL_OUTPUT,['display_panel'],'三条以上真实参考液体浓度与实测模拟面积拟合，按元素/线/模式/温程分开','configuration','agent',CALIBRATION_EXAMPLE)
cap('calibration.select',obj({'curve_id':TEXT}),obj({'selected':BOOL}),['display_panel'],'选择已有本方法响应签名标线，禁止跨元素/原子化模式错用','configuration','agent',{'curve_id':'illustrative_cu'})
cap('calibration.read',obj({'curve_id':TEXT}),obj({'curve_json':TEXT}),['display_panel'],'纯读取标线与参考来源','query','agent',{'curve_id':'illustrative_cu'})
cap('records.list',obj({}),obj({'capacity':integer(RESULT_CAPACITY,RESULT_CAPACITY),'count':integer(0,RESULT_CAPACITY),'record_ids':arr(TEXT),'curve_count':integer(0,CURVE_CAPACITY)}),['display_panel'],'实例256结果/32标线为工程内存边界，不宣称厂家容量','query')
cap('records.read',obj({'record_id':TEXT}),RESULT,['display_panel'],'按ID读历史完整峰和冻结方法','query','agent',{'record_id':'contraa_01:result:1'})
cap('records.remove',obj({'record_id':TEXT}),obj({'removed':BOOL}),['display_panel'],'显式释放结果容量，受保护异步历史独立保留','configuration','agent',{'record_id':'contraa_01:result:1'})
cap('template.read',obj({'substance_id':TEXT}),TEMPLATE,['display_panel'],'查询实测/赋值浓度或纯原子谱线，保留介质和缺测字段','query','agent',{'substance_id':SAMPLE_EXAMPLE['template_id']})
cap('product.read',obj({}),LEDGER,['graphite_tube','spray_chamber'],'私有元素仓、液体当量与燃料/氧化剂/氩气及净热量账本','query','evaluator')
UNSUPPORTED={'solid.measure','hydride.measure','emission.measure','background.lsbc','autosampler.sequence'}
for k,z in [('solid.measure','未安装solid AA固体进样附件'),('hydride.measure','未安装氢化物/冷蒸气附件'),('emission.measure','厂家D支持发射但本次运行物理只实现吸收'),('background.lsbc','原厂IBC/LSBC未复现，仅支持声明的线性参考背景代理'),('autosampler.sequence','AS-GF液体进样有限动作已建，全108位队列/自动稀释加标程序未实现')]:cap(k,obj({}),obj({}),['body'],z)
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='display_panel',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','宿主响应已配置','setpoint',c='body');slot('sample.loaded',BOOL,'1','外部溶液已接入','scene',c='sample_inlet');slot('power.enabled',BOOL,'1','逻辑电源','setpoint',c='power_inlet',w=('power.set','enabled'))
slot('cover.opened',BOOL,'1','原子化舱门','scene',c='sample_door');slot('cover.lift_m',number('m',0,.245),'m','厂家安全玻璃向上滑动；0–0.245m为工程行程，非实测行程','scene',c='sample_door')
slot('furnace.opened',BOOL,'1','冷维护炉头已开','scene',c='furnace_jaw');slot('furnace.angle_rad',number('rad',0,math.pi/2),'rad','炉头冷态维护工程转角','scene',c='furnace_jaw')
slot('atomizer.angle_rad',number('rad',0,math.pi/2),'rad','D双原子化旋臂角度，炉0火焰pi/2','scene',c='atomizer_swivel');slot('burner.height_m',number('m',.004,.016),'m','厂家燃烧器高度4–16mm','scene',c='burner_head');slot('burner.angle_rad',number('rad',0,math.pi/2),'rad','厂家燃烧器转角0–90°','scene',c='burner_head')
slot('sampler.angle_rad',number('rad',0,2*math.pi),'rad','AS-GF液体托盘工程当前选位','scene',c='sampler_tray');slot('sampler_arm.angle_rad',number('rad',0,math.pi),'rad','AS-GF臂从当前样瓶到注入口工程角','scene',c='sampler_arm');slot('injector.position_m',number('m',0,.10),'m','AS-GF有限针头浸入/提出工程行程','scene',c='sampler_probe')
slot('furnace.target_k',number('K',278.15,3273.15),'K','温程当前目标','setpoint',c='graphite_furnace');slot('furnace.actual_k',number('K',278.15,3273.15),'K','限速炉温工程估计非厂家传感器','derived',c='graphite_furnace')
slot('flame.lit',BOOL,'1','通过互锁点燃的火焰逻辑','scene',c='burner_head');slot('flame.igniting',BOOL,'1','点火有限延迟','derived',c='burner_head')
slot('method.mode',TEXT,'1','当前方法原子化模式','setpoint',c='atomizer_swivel');slot('method.element',TEXT,'1','选择元素','setpoint',c='echelle_grating');slot('method.wavelength_m',nullable(number('m',185e-9,900e-9)),'m','选定波长，线介质按源保留','setpoint',c='echelle_grating')
for k,s,u,z in [('measurement.phase',PHASE,'1','运行终态'),('measurement.stage',TEXT,'1','有限动作/温程阶段'),('measurement.elapsed_s',number('s',0),'s','本次测量推进时间'),('measurement.progress',number('1',0,1),'1','已完成重复与当前阶段进度'),('measurement.completed_repeats',integer(0,10),'1','完成重复数'),('detector.absorbance',nullable(number('1')),'1','最新窗中心表观吸光度'),('detector.background_absorbance',nullable(number('1')),'1','同期两端像素线性背景估计'),('detector.corrected_absorbance',nullable(number('1')),'1','扣除选定背景后的观察信号'),('detector.last_sample_time_s',nullable(number('s',0)),'s','最近完整检测窗截止时间'),('result.concentration_kg_m3',nullable(number('kg/m^3')),'kg/m^3','原观察面积与标线换算元素浓度'),('result.integrated_absorbance_s',nullable(number('s')),'s','完整采集窗基线扣除吸光度时间积分'),('blank.valid',BOOL,'1','完整匹配空白有效'),('calibration.ready',BOOL,'1','匹配元素/线/模式标线可用'),('storage.result_count',integer(0,RESULT_CAPACITY),'1','已保存完整结果数'),('storage.curve_count',integer(0,CURVE_CAPACITY),'1','已保存标线数'),('safety.warning',TEXT,'1','互锁/质量标记')]:slot(k,s,u,z)
slot('truth.ledger',LEDGER,'1','真实元素/液体/气源账本只对宿主公开','process_truth','evaluator','graphite_tube')

def _library():return json.loads((ROOT/TEMPLATE_REL).read_text()) if (ROOT/TEMPLATE_REL).exists() else {'compounds':{},'lines':{}}
cap('lamp_door.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['lamp_door'],'断电冷态开灯室维护；不假装带灯更换控制器','device_command','evaluator',{'opened':False})
slot('lamp_door.opened',BOOL,'1','灯室维护门开启','scene',c='lamp_door');slot('lamp_door.angle_rad',number('rad',0,1.4),'rad','灯室门宿主维护角','scene',c='lamp_door')
slot('burner.position_m',number('m',0,.012),'m','燃烧器相对最低4mm高度的升降行程','scene',c='burner_lift')

def sampler_pick_angles(index):
 validate(integer(0,107),index)
 offset=0
 for count,radius in [(28,.058),(32,.080),(40,.102),(8,.030)]:
  if index<offset+count:
   initial=2*math.pi*(index-offset)/count
   arm=math.acos((.130**2+.1125**2-radius**2)/(2*.130*.1125))
   beta=math.atan2(.1125-.130*math.cos(arm),.130*math.sin(arm))
   return arm,(beta-initial)%(2*math.pi)
  offset+=count
 raise ActionRejected('INVALID_ARGUMENT')

class JenaContraa800Runtime:
 def __init__(self,instance_id='contraa_01',library=None):
  self._tracked_element=None;self.instance_id=instance_id;self.library=_library() if library is None else copy.deepcopy(library);self.templates=self.library.get('compounds',{});self.lines=self.library.get('lines',{});self.time_s=0.;self.revision=0;self.plant=None;self.method=None;self.sample=None;self.powered=False;self.opened=False;self.lamp_opened=False;self.furnace_opened=False;self.temperature=298.15;self.target=298.15;self.initial_temperature=298.15;self.heat=0.;self.atomizer=0.;self.burner_height=.004;self.burner_angle=0.;self.burner_target_height=.004;self.burner_target_angle=0.;self.sampler=0.;self.sampler_arm=math.pi/2;self.injector=0.;self._parking=False;self.flame=False;self.igniting=False;self.ignition_elapsed=0.
  for k in WAREHOUSES:
   if k not in ['sample','residual']:setattr(self,'_'+k,0.)
  for k in ['registered','pipette','tube','waste','evaporated','removed']:setattr(self,'_'+k+'_v',0.)
  self._gases={k:dict(initial=0.,remaining=0.,consumed=0.) for k in ['fuel','oxidant','argon']}
  self.phase='idle';self.stage='idle';self.elapsed_s=0.;self.warning='';self.operation_id='';self._op_count=0;self._record_id=None;self._records={};self._curves={};self._selected='';self._blanks={};self._run_method=None;self._run_key=None;self._run_curve=None;self._run_kind='';self._run_blank=0.;self._plan=[];self._plan_index=0;self._stage_elapsed=0.;self._active=None;self.assays=[];self._concentration=None;self._sd=None;self._cv=None;self._qc=[];self._last_point=None;self._spectrum=dict(wavelength_m=[],normalized_transmission=[],apparent_absorbance=[],sample_window_s=None,evidence='not_sampled');self._pending_area=0.;self._pending_dt=0.;self._sample_start=0.;self._next_detector=None;self._baseline_points=[];self._result_mode='';self._result_element='';self._result_line=''
  self._rng=np.random.default_rng(0);self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._operation_records={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return JenaContraa800Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in ['library','templates','lines','_rng']})|{'_rng_state':copy.deepcopy(self._rng.bit_generator.state)}
 def _checkpoint(self):
  frozen=['library','templates','lines','_records','_curves','_operation_records','_requests','_rng'];s=copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in frozen})
  for k in frozen:s[k]=dict(getattr(self,k)) if k in ['_records','_curves','_operation_records','_requests'] else getattr(self,k)
  return {k:s[k] for k in self.__dict__},copy.deepcopy(self._rng.bit_generator.state)
 def _reference(self,key,physical=True):
  r=self.templates.get(key,self.lines.get(key))
  if r is None:raise ActionRejected('UNKNOWN_REFERENCE')
  if physical:
   value=r.get('concentration_kg_m3')
   if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:raise ActionRejected('LIQUID_CONCENTRATION_REQUIRED')
   if value>1000:raise ActionRejected('LIQUID_MODEL_DOMAIN')
   if not isinstance(r.get('element'),str):raise ActionRejected('INVALID_REFERENCE')
  return r
 def _key(self):
  # Integrated flame response depends on aspiration duration; furnace response on mass/program, but keep volume in signature to avoid unjustified transfer.
  settings={k:v for k,v in (self.method or {}).items() if k!='repeats'}
  if self.method and self.method['mode']=='flame':settings.update(burner_height_m=self.burner_target_height,burner_angle_rad=self.burner_target_angle)
  return json.dumps(settings,sort_keys=True)
 def _blank_valid(self):return self._key() in self._blanks
 def _calibration_ready(self):return self._selected in self._curves and self._curves[self._selected]['key']==self._key()
 def _position_target(self):return math.pi/2 if self.method and self.method['mode']=='flame' else 0.
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _cold(self):return self.temperature<=353.15 and not self.flame and not self.igniting
 def _ledger(self):
  m={k:getattr(self,'_'+k) for k in WAREHOUSES if k not in ['sample','residual']};m['sample']=self.sample['concentration']*self.sample['volume'] if self.sample else 0.;m['residual']=m['registered']-math.fsum(v for k,v in m.items() if k!='registered')
  v={k:getattr(self,'_'+k+'_v') for k in ['registered','pipette','tube','waste','removed']};v['sample']=self.sample['volume'] if self.sample else 0.;v['evaporated_liquid_equivalent']=self._evaporated_v;v['residual']=v['registered']-math.fsum(x for k,x in v.items() if k!='registered')
  gases={k:dict(g,residual=g['initial']-g['remaining']-g['consumed']) for k,g in self._gases.items()};stored=(self.temperature-self.initial_temperature)*(self.plant['furnace_capacity_j_k'] if self.plant else 0.)
  return dict(element_mass_kg=m,liquid_volume_m3=v,**{k+'_mol':x for k,x in gases.items()},net_furnace_heat_j=self.heat,stored_furnace_heat_j=stored,thermal_residual_j=self.heat-stored,active_template_id=self.sample['template_id'] if self.sample else None,evidence='Host-known selected-element mass only; all unmeasured matrix untracked. Liquid-equivalent evaporation is not gas volume; gas inventories not full reaction stoichiometry; C*dT net heat proxy.')
 def _result(self):return dict(phase=self.phase,stage=self.stage,complete=self.phase=='succeeded',mode=self._result_mode,element=self._result_element,line_id=self._result_line,concentration_kg_m3=self._concentration,sd_kg_m3=self._sd,cv=self._cv,assays=copy.deepcopy(self.assays),active_assay=copy.deepcopy(self._active),qc=list(self._qc),evidence='synthetic_observed_absorption_from_real_reference_concentration_and_atomic_line; not manufacturer measured trace',template_source=TEMPLATE_REL)
 def read(self,visibility='agent'):
  last=self._last_point;progress=(len(self.assays)+(self._plan_index/max(1,len(self._plan))))/max(1,(self._run_method or {}).get('repeats',1)) if self.phase=='running' else 1. if self.phase=='succeeded' else 0.
  v={'plant.configured':self.plant is not None,'sample.loaded':self.sample is not None,'power.enabled':self.powered,'cover.opened':self.opened,'cover.lift_m':.245 if self.opened else 0.,'lamp_door.opened':self.lamp_opened,'lamp_door.angle_rad':1.4 if self.lamp_opened else 0.,'furnace.opened':self.furnace_opened,'furnace.angle_rad':math.pi/2 if self.furnace_opened else 0.,'atomizer.angle_rad':self.atomizer,'burner.height_m':self.burner_height,'burner.position_m':self.burner_height-.004,'burner.angle_rad':self.burner_angle,'sampler.parking':self._parking,'sampler.angle_rad':self.sampler,'sampler_arm.angle_rad':self.sampler_arm,'injector.position_m':self.injector,'furnace.target_k':self.target,'furnace.actual_k':self.temperature,'flame.lit':self.flame,'flame.igniting':self.igniting,'method.mode':self.method['mode'] if self.method else '', 'method.element':self.method['element'] if self.method else '', 'method.wavelength_m':self.method['wavelength_m'] if self.method else None,'measurement.phase':self.phase,'measurement.stage':self.stage,'measurement.elapsed_s':self.elapsed_s,'measurement.progress':min(1.,max(0.,progress)),'measurement.completed_repeats':len(self.assays),'detector.absorbance':last['absorbance'] if last else None,'detector.background_absorbance':last['background_absorbance'] if last else None,'detector.corrected_absorbance':last['corrected_absorbance'] if last else None,'detector.last_sample_time_s':last['time_s'] if last else None,'result.concentration_kg_m3':self._concentration,'result.integrated_absorbance_s':float(np.mean([a['integrated_absorbance_s'] for a in self.assays])) if self.assays else None,'blank.valid':self._blank_valid(),'calibration.ready':self._calibration_ready(),'storage.result_count':len(self._records),'storage.curve_count':len(self._curves),'safety.warning':self.warning}
  if visibility!='agent':v['truth.ledger']=self._ledger()
  return v
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if capability in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[capability][0],a)
  if CAPS[capability][4]=='query':return copy.deepcopy(self._invoke(capability,a))
  before,rng=self._checkpoint()
  try:r=self._invoke(capability,a);validate(CAPS[capability][1],r)
  except Exception:
   self.__dict__.clear();self.__dict__.update(before);self._rng.bit_generator.state=rng;raise
  self.revision+=1;self._archive_operation();return copy.deepcopy(r)
 def _invoke(self,k,a):
  if k=='plant.configure':
   if self.plant is not None or self.time_s:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_assumed_response']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   self.plant=copy.deepcopy(a);self.temperature=self.target=self.initial_temperature=a['ambient_k'];self._rng=np.random.default_rng(a['seed'])
   for gas,g in self._gases.items():g.update(initial=a[gas+'_inventory_mol'],remaining=a[gas+'_inventory_mol'],consumed=0.)
   return {'configured':True}
  if k=='sample.load':
   self._idle()
   if not self.plant:raise ActionRejected('PLANT_REQUIRED')
   if self.sample is not None:raise ActionRejected('UNLOAD_REQUIRED')
   if not a['accept_unknown_conditions'] or not a['accept_assumed_matrix']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   if a['kind']=='blank':
    if a['template_id'] or a['background_absorbance'] or a['background_slope']:raise ActionRejected('BLANK_COMPOSITION')
    element=None;c=0.
   else:r=self._reference(a['template_id']);element=r['element'];c=r['concentration_kg_m3']/a['dilution_factor']
   if element and self._tracked_element and element!=self._tracked_element:raise ActionRejected('INSTANCE_ELEMENT_FIXED')
   if element:self._tracked_element=element
   self.sample=dict(template_id=a['template_id'],id=a['sample_id'],kind=a['kind'],volume=a['volume_m3'],concentration=c,element=element,vial_index=a['vial_index'],background=a['background_absorbance'],slope=a['background_slope'],dilution=a['dilution_factor']);self._registered+=c*a['volume_m3'];self._registered_v+=a['volume_m3'];return {'loaded':True}
  if k=='sample.unload':
   self._idle()
   if not self.sample:raise ActionRejected('SAMPLE_REQUIRED')
   if not a['recipient_id']:raise ActionRejected('RECIPIENT_REQUIRED')
   v=self.sample['volume'];self._removed+=v*self.sample['concentration'];self._removed_v+=v;self.sample=None;return dict(unloaded=True,volume_m3=v)
  if k=='fluidics.clean':
   self._idle()
   if self.powered or not self._cold():raise ActionRejected('COLD_POWER_OFF_REQUIRED')
   m=self._pipette+self._tube+self._deposit;v=self._pipette_v+self._tube_v;self._waste+=m;self._waste_v+=v;self._pipette=self._tube=self._deposit=0.;self._pipette_v=self._tube_v=0.;self.injector=0.;return dict(removed_mass_kg=m,removed_volume_m3=v)
  if k in ['cover.set','lamp_door.set','furnace.open']:
   if k=='cover.set' and (self.injector>1e-12 or abs(self.sampler_arm-math.pi/2)>1e-12):raise ActionRejected('SAMPLER_PARK_REQUIRED')
   if a['opened'] and (self.phase=='running' or not self._cold() or k!='cover.set' and self.powered):raise ActionRejected('MAINTENANCE_INTERLOCK')
   setattr(self,{'cover.set':'opened','lamp_door.set':'lamp_opened','furnace.open':'furnace_opened'}[k],a['opened']);return {'opened':a['opened']}
  if k=='power.set':
   if a['enabled'] and (not self.plant or self.lamp_opened or self.furnace_opened or self.opened and (not self.method or self.method['mode']=='flame')):raise ActionRejected('POWER_INTERLOCK')
   self.powered=a['enabled']
   if not self.powered:self.flame=self.igniting=False;self._stop('POWER_LOSS');self.target=self.plant['ambient_k'] if self.plant else 298.15
   return {'enabled':self.powered}
  if k=='method.configure':
   self._idle()
   if not self.plant:raise ActionRejected('PLANT_REQUIRED')
   if self.flame or self.igniting or not self._cold() or self.injector>1e-12 or abs(self.sampler_arm-math.pi/2)>1e-12:raise ActionRejected('MODE_CHANGE_INTERLOCK')
   if a['mode']=='furnace' and a['injection_volume_m3']>50e-9:raise ActionRejected('STANDARD_TUBE_VOLUME_LIMIT')
   if a['line_id'] not in self.lines:raise ActionRejected('UNKNOWN_LINE')
   line=self.lines[a['line_id']]
   if line['element']!=a['element']:raise ActionRejected('ELEMENT_LINE_MISMATCH')
   if self._tracked_element and a['element']!=self._tracked_element:raise ActionRejected('INSTANCE_ELEMENT_FIXED')
   if not 185e-9<=line['wavelength_m']<=900e-9:raise ActionRejected('LINE_OUT_OF_RANGE')
   if abs(a['wavelength_m']-line['wavelength_m'])>10*self.plant['line_sigma_m']:raise ActionRejected('LINE_WINDOW_MISMATCH')
   if [p['stage'] for p in a['furnace_program']]!=['dry','pyrolysis','atomize','clean']:raise ActionRejected('FURNACE_PROGRAM_ORDER')
   if any(p['temperature_k']<self.plant['ambient_k'] for p in a['furnace_program']):raise ActionRejected('FURNACE_PROGRAM_RANGE')
   if any(abs((p['temperature_k']-273.15)-round(p['temperature_k']-273.15))>1e-8 for p in a['furnace_program']):raise ActionRejected('FURNACE_TEMPERATURE_INCREMENT')
   if a['furnace_program'][2]['temperature_k']<=a['furnace_program'][1]['temperature_k']:raise ActionRejected('ATOMIZATION_TEMPERATURE')
   if a['baseline_s']<self.plant['detector_period_s'] or a['flush_s']<self.plant['detector_period_s']:raise ActionRejected('SAMPLING_WINDOW_RANGE')
   self.method=copy.deepcopy(a);return {'configured':True}
  if k=='burner.configure':
   self._idle()
   if self.flame or self.igniting:raise ActionRejected('FLAME_OFF_REQUIRED')
   self.burner_target_height=a['height_m'];self.burner_target_angle=a['angle_rad'];return {'configured':True}
  if k=='flame.ignite':
   self._idle()
   if not self.plant or not self.method or self.method['mode']!='flame' or not self.powered or self.opened or self.lamp_opened or self.furnace_opened or abs(self.atomizer-math.pi/2)>1e-9:raise ActionRejected('IGNITION_INTERLOCK')
   if not all(self.plant[x] for x in ['coolant_ready','exhaust_ready','burner_recognized','siphon_ready']):raise ActionRejected('IGNITION_INTERLOCK')
   if any(self._gases[x]['remaining']<=0 for x in ['fuel','oxidant']):raise ActionRejected('GAS_REQUIRED')
   if self.flame or self.igniting:raise ActionRejected('ALREADY_IGNITED')
   self.igniting=True;self.ignition_elapsed=0.;return {'igniting':True}
  if k=='flame.extinguish':self.flame=self.igniting=False;self._stop('FLAME_EXTINGUISHED');return {'lit':False}
  if k=='sampler.park':
   self._idle()
   if not self.plant or not self._cold():raise ActionRejected('COLD_MAINTENANCE_REQUIRED')
   self._parking=True;return {'parking':True}
  if k=='measurement.start':return self._start()
  if k=='measurement.stop':self._stop('CANCELLED');return {'phase':self.phase}
  if k=='process.measure':
   v=self.read();return dict(phase=self.phase,stage=self.stage,elapsed_s=self.elapsed_s,progress=v['measurement.progress'],absorbance=v['detector.absorbance'],background_absorbance=v['detector.background_absorbance'],concentration_kg_m3=self._concentration,warning=self.warning)
  if k=='result.read':return self._result()
  if k=='spectrum.read':return self._spectrum
  if k=='result.export':
   if a['format']=='json':content=json.dumps(self._result(),ensure_ascii=False,allow_nan=False)
   else:
    import csv,io
    out=io.StringIO();w=csv.writer(out);w.writerow(['repeat','time_s','window_start_s','window_end_s','absorbance','background_absorbance','corrected_absorbance','acquiring','saturated','integrated_absorbance_s','concentration_kg_m3','settings_json','calibration_json'])
    for assay in self.assays+([self._active] if self._active else []):
     for p in assay['points']:w.writerow([assay['repeat_index'],p['time_s'],*p['sample_window_s'],p['absorbance'],p['background_absorbance'],p['corrected_absorbance'],p['acquiring'],p['saturated'],assay['integrated_absorbance_s'],assay['concentration_kg_m3'],assay['settings_json'],assay['calibration_json']])
    content=out.getvalue()
   return dict(format=a['format'],content=content)
  if k=='blank.read':return dict(valid=self._blank_valid(),blank_json=json.dumps(self._blanks.get(self._key()),allow_nan=False))
  if k=='calibration.fit':return self._fit(a)
  if k=='calibration.select':
   self._idle()
   if a['curve_id'] and (a['curve_id'] not in self._curves or self._curves[a['curve_id']]['key']!=self._key()):raise ActionRejected('CURVE_METHOD_MISMATCH')
   self._selected=a['curve_id'];return {'selected':bool(self._selected)}
  if k=='calibration.read':
   if a['curve_id'] not in self._curves:raise ActionRejected('UNKNOWN_CURVE')
   return {'curve_json':json.dumps(self._curves[a['curve_id']],allow_nan=False)}
  if k=='records.list':return dict(capacity=RESULT_CAPACITY,count=len(self._records),record_ids=list(self._records),curve_count=len(self._curves))
  if k=='records.read':
   if a['record_id'] not in self._records:raise ActionRejected('UNKNOWN_RECORD')
   return self._records[a['record_id']]
  if k=='records.remove':
   if a['record_id'] not in self._records:raise ActionRejected('UNKNOWN_RECORD')
   del self._records[a['record_id']];return {'removed':True}
  if k=='template.read':return dict(substance_id=a['substance_id'],template_json=json.dumps(self._reference(a['substance_id'],False),ensure_ascii=False,allow_nan=False))
  if k=='product.read':return self._ledger()
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _fit(self,a):
  self._idle()
  if not self.method or not self._blank_valid():raise ActionRejected('MATCHED_BLANK_REQUIRED')
  if not a['accept_same_response_conditions']:raise ActionRejected('CALIBRATION_ACK_REQUIRED')
  if not a['curve_id'] or a['curve_id'] in self._curves:raise ActionRejected('CURVE_ID_EXISTS')
  if len(self._curves)>=CURVE_CAPACITY:raise ActionRejected('CURVE_STORE_FULL')
  x=[];y=[];sources=[]
  for r in a['observations']:
   source=self._reference(r['reference_id'])
   if source['element']!=self.method['element']:raise ActionRejected('ELEMENT_LINE_MISMATCH')
   if r['injection_volume_m3']!=self.method['injection_volume_m3']:raise ActionRejected('CALIBRATION_VOLUME_MISMATCH')
   x.append(source['concentration_kg_m3']/r['dilution_factor']*r['injection_volume_m3']);y.append(r['integrated_absorbance_s']);sources.append(copy.deepcopy(source))
  x=np.asarray(x);y=np.asarray(y);scale=float(np.max(np.abs(x)))
  if scale<=0 or np.ptp(x/scale)<1e-10 or len(np.unique(x))<3:raise ActionRejected('DEGENERATE_CALIBRATION')
  z=x/scale;zc=z-z.mean();yc=y-y.mean();slope_scaled=float(zc@yc/(zc@zc));slope=slope_scaled/scale;intercept=float(y.mean()-slope_scaled*z.mean());prediction=slope_scaled*z+intercept;ss=float(yc@yc);r2=1-float((prediction-y)@(prediction-y))/ss if ss>0 else 0.
  if not all(math.isfinite(v) for v in [slope,intercept,r2]) or slope<=0 or r2<.95:raise ActionRejected('CALIBRATION_FIT_INVALID')
  result=dict(curve_id=a['curve_id'],slope_s_kg=slope,intercept_s=intercept,r_squared=r2,element_mass_range_kg=[float(x.min()),float(x.max())],evidence='real_reported_or_assigned_reference_concentrations_with_user_supplied_observed_synthetic_absorbance_areas; not OEM calibration')
  self._curves[a['curve_id']]=dict(result,key=self._key(),method=copy.deepcopy(self.method),observations=copy.deepcopy(a['observations']),sources=sources);return result
 def _start(self):
  self._idle()
  if not self.plant or not self.method or not self.sample:raise ActionRejected('PLANT_METHOD_SAMPLE_REQUIRED')
  if not self.powered or self.lamp_opened or self.furnace_opened or not self.plant['coolant_ready'] or not self.plant['exhaust_ready']:raise ActionRejected('MEASUREMENT_INTERLOCK')
  if self.method['mode']=='flame' and self.opened or self.method['mode']=='furnace' and not self.opened:raise ActionRejected('SAMPLE_DOOR_MODE_INTERLOCK')
  if abs(self.atomizer-self._position_target())>1e-9:raise ActionRejected('ATOMIZER_NOT_POSITIONED')
  if self.method['mode']=='flame' and not self.flame:raise ActionRejected('FLAME_REQUIRED')
  if self.method['mode']=='furnace' and self._gases['argon']['remaining']<=0:raise ActionRejected('ARGON_REQUIRED')
  if self.sample['element'] is not None and self.sample['element']!=self.method['element']:raise ActionRejected('ELEMENT_LINE_MISMATCH')
  if self._pipette_v>1e-15 or self._tube_v>1e-12 or self._tube>1e-16:raise ActionRejected('RESIDUE_CLEAN_REQUIRED')
  if self.sample['volume']+1e-15<self.method['injection_volume_m3']*self.method['repeats']:raise ActionRejected('SAMPLE_VOLUME_LIMIT')
  if len(self._records)>=RESULT_CAPACITY:raise ActionRejected('RESULT_STORE_FULL')
  if self.sample['kind']!='blank' and not self._blank_valid():raise ActionRejected('MATCHED_BLANK_REQUIRED')
  if self.sample['kind']=='sample' and not self._calibration_ready():raise ActionRejected('MATCHED_CALIBRATION_REQUIRED')
  estimated=self.method['repeats']*(self.method['baseline_s']+self.method['flush_s']+self.method['injection_volume_m3']/self.plant['liquid_rate_m3_s']*2+2*(.052+.077)/self.plant['motion_speed_m_s']+2*math.pi/self.plant['rotation_speed_rad_s']+sum(3000/p['ramp_k_s']+p['hold_s'] for p in self.method['furnace_program']))
  if estimated/self.plant['detector_period_s']>MAX_TRACE_POINTS:raise ActionRejected('TRACE_CAPACITY')
  self._archive_operation();self._op_count+=1;self.operation_id=f'{self.instance_id}:operation:{self._op_count}';self.phase='running';self.warning='';self.elapsed_s=0.;self._run_method=copy.deepcopy(self.method);self._run_key=self._key();self._run_curve=copy.deepcopy(self._curves.get(self._selected)) if self._calibration_ready() else None;self._run_kind=self.sample['kind'];self._run_blank=self._blanks.get(self._run_key,{}).get('baseline',0.);self.assays=[];self._concentration=self._sd=self._cv=None;self._qc=[];self._last_point=None;self._result_mode=self.method['mode'];self._result_element=self.method['element'];self._result_line=self.method['line_id'];self._begin_assay();return dict(phase=self.phase,operation_id=self.operation_id)
 def _begin_assay(self):
  m=self._run_method;p=self.plant;plan=[]
  def add(name,duration,**kw):plan.append(dict(stage=name,duration=max(0.,duration),**kw))
  if m['mode']=='furnace':
   pick_arm,pick_tray=sampler_pick_angles(self.sample['vial_index']);add('cool',max(0.,self.temperature-p['ambient_k'])/p['cooling_rate_k_s']);add('sampler_select',abs(pick_tray-self.sampler)/p['rotation_speed_rad_s'],target=pick_tray);add('arm_sample',abs(self.sampler_arm-pick_arm)/p['rotation_speed_rad_s'],target=pick_arm);add('probe_down',(.052-self.injector)/p['motion_speed_m_s'],target=.052);add('aspirate',m['injection_volume_m3']/p['liquid_rate_m3_s']);add('probe_up',.052/p['motion_speed_m_s'],target=0.);add('arm_furnace',abs(math.pi-pick_arm)/p['rotation_speed_rad_s'],target=math.pi);add('probe_inject',.077/p['motion_speed_m_s'],target=.077);add('dispense',m['injection_volume_m3']/p['liquid_rate_m3_s']);add('probe_retract',.077/p['motion_speed_m_s'],target=0.)
  add('baseline',m['baseline_s'])
  if m['mode']=='flame':add('flame_aspirate',m['injection_volume_m3']/p['liquid_rate_m3_s'],acquire=True)
  else:
   previous=p['ambient_k']
   for step in m['furnace_program']:
    add('heat_'+step['stage'],abs(step['temperature_k']-previous)/step['ramp_k_s'],temperature=step['temperature_k'],rate=step['ramp_k_s'],argon=step['argon_on']);add(step['stage'],step['hold_s'],temperature=step['temperature_k'],rate=step['ramp_k_s'],argon=step['argon_on'],acquire=step['stage']=='atomize');previous=step['temperature_k']
  if m['mode']=='furnace':add('arm_park',(math.pi/2)/p['rotation_speed_rad_s'],target=math.pi/2)
  add('flush',m['flush_s'],acquire=True,argon=m['mode']=='furnace')
  self._plan=plan;self._plan_index=0;self._stage_elapsed=0.;self.stage=plan[0]['stage'];self._active=dict(repeat_index=len(self.assays),phase='running',mode=m['mode'],element=m['element'],line_id=m['line_id'],injection_volume_m3=m['injection_volume_m3'],integrated_absorbance_s=0.,concentration_kg_m3=None,quality='unavailable',points=[],settings_json=json.dumps(m,sort_keys=True),calibration_json=json.dumps(self._run_curve,sort_keys=True));self._pending_area=self._pending_dt=0.;self._sample_start=self.time_s;self._next_detector=self.time_s+p['detector_period_s'];self._baseline_points=[]
 def _stop(self,reason):
  if self.phase!='running':return
  self.phase='cancelled' if reason in ['CANCELLED','POWER_LOSS','FLAME_EXTINGUISHED'] else 'failed';self.warning=reason;self.stage=reason.lower();self.target=self.plant['ambient_k'];self._concentration=self._sd=self._cv=None
  if self._active:self._active['phase']=self.phase;self._active['quality']='partial_no_concentration';self._active['concentration_kg_m3']=None
 def _finish_assay(self):
  a=self._active;a['phase']='succeeded';area=a['integrated_absorbance_s'];q=[]
  if self._run_kind=='blank':a['quality']='blank_reference'
  elif any(p['saturated'] for p in a['points'] if p['acquiring']):a['quality']='saturated';q.append('SATURATED')
  elif self._run_curve:
   curve=self._run_curve;mass=(area-curve['intercept_s'])/curve['slope_s_kg'];low,high=curve['element_mass_range_kg'];tol=max(abs(low),abs(high))*1e-6
   if not math.isfinite(mass) or mass<low-tol or mass>high+tol:a['quality']='outside_calibration';q.append('OUTSIDE_CALIBRATION')
   else:a['concentration_kg_m3']=mass/a['injection_volume_m3'];a['quality']='estimated'
  else:a['quality']='standard_uncalibrated'
  self.assays.append(a);self._active=None;self._qc.extend(q)
  if len(self.assays)<self._run_method['repeats']:self._begin_assay();return
  if self._run_kind=='blank':
   baseline=[p['corrected_absorbance'] for a in self.assays for p in a['points'] if not p['acquiring'] and p['sample_window_s'][1]>p['sample_window_s'][0]]
   self._blanks[self._run_key]=dict(baseline=float(np.mean(baseline)) if baseline else 0.,evidence='completed synthetic detector blank, not OEM calibration')
  values=[a['concentration_kg_m3'] for a in self.assays]
  if values and all(v is not None for v in values):self._concentration=float(np.mean(values));self._sd=float(np.std(values,ddof=1)) if len(values)>1 else None;self._cv=self._sd/self._concentration if self._sd is not None and self._concentration>0 else None
  self.phase='succeeded';self.stage='complete';self.target=self.plant['ambient_k'];self._record_id=f'{self.instance_id}:result:{self._op_count}';self._records[self._record_id]=self._result()
 def _detector(self):
  if self._pending_dt<=1e-14:return
  m=self._run_method;line=self.lines[m['line_id']];p=self.plant;axes=m['wavelength_m']+np.linspace(-6.,6.,21)*p['line_sigma_m'];shape=np.exp(-.5*((axes-line['wavelength_m'])/p['line_sigma_m'])**2);A=self._pending_area/self._pending_dt*p['absorption_gain_kg_inv'];background=self.sample['background']+self.sample['slope']*np.linspace(-1.,1.,21);true_abs=A*shape+background+p['detector_baseline_absorbance'];raw=np.power(10.,-np.clip(true_abs,-10,320));saturated=bool(np.any(raw<p['detector_transmission_floor']));observed=-np.log10(np.maximum(raw,p['detector_transmission_floor']))+self._rng.normal(0,p['detector_noise_sd'],21);transmission=np.power(10.,-observed);center=float(observed[10]);bg=float((observed[0]+observed[-1])/2) if m['background']=='linear_reference' else 0.;corrected=center-bg;entry=self._plan[self._plan_index];acquire=bool(entry.get('acquire'));point=dict(time_s=self.time_s,sample_window_s=[self._sample_start,self.time_s],absorbance=center,background_absorbance=bg,corrected_absorbance=corrected,acquiring=acquire,saturated=saturated)
  self._last_point=point;self._active['points'].append(point);self._spectrum=dict(wavelength_m=axes.tolist(),normalized_transmission=transmission.tolist(),apparent_absorbance=observed.tolist(),sample_window_s=point['sample_window_s'],evidence='synthetic apparent normalized transmission/absorbance; not photon power or OEM200-pixel raw CCD; source line medium='+str(line.get('wavelength_medium','unknown')))
  if acquire:self._active['integrated_absorbance_s']+=(corrected-self._run_blank)*self._pending_dt
  self._pending_area=self._pending_dt=0.;self._sample_start=self.time_s;self._next_detector=self.time_s+p['detector_period_s']
 def _physical(self,dt):
  if not self.plant:return
  p=self.plant;active=self.phase=='running';entry=self._plan[self._plan_index] if active else {};stage=entry.get('stage','idle')
  def move(value,target,speed):return value+math.copysign(min(abs(target-value),speed*dt),target-value) if target!=value else value
  if self._parking:
   if self.injector>1e-12:self.injector=move(self.injector,0.,p['motion_speed_m_s'])
   else:self.sampler_arm=move(self.sampler_arm,math.pi/2,p['rotation_speed_rad_s'])
   if self.injector<=1e-12 and abs(self.sampler_arm-math.pi/2)<=1e-12:self._parking=False
  if self.powered and not self.flame and not self.igniting:
   if not self.method or self.method['mode']=='furnace' or abs(self.sampler_arm-math.pi/2)<=1e-12:self.atomizer=move(self.atomizer,self._position_target(),p['rotation_speed_rad_s'])
  self.burner_height=move(self.burner_height,self.burner_target_height,p['motion_speed_m_s']);self.burner_angle=move(self.burner_angle,self.burner_target_angle,p['rotation_speed_rad_s'])
  oldT=self.temperature;self.target=entry.get('temperature',p['ambient_k']);rate=entry.get('rate',p['cooling_rate_k_s']);self.temperature=move(self.temperature,self.target,rate);self.heat+=(self.temperature-oldT)*p['furnace_capacity_j_k']
  if active and stage=='sampler_select':self.sampler=move(self.sampler,entry['target'],p['rotation_speed_rad_s'])
  if active and stage in ['arm_sample','arm_furnace','arm_park']:self.sampler_arm=move(self.sampler_arm,entry['target'],p['rotation_speed_rad_s'])
  if active and stage in ['probe_down','probe_up','probe_inject','probe_retract']:self.injector=move(self.injector,entry['target'],p['motion_speed_m_s'])
  gas_modes=[]
  if self.flame or self.igniting:gas_modes+=['fuel','oxidant']
  if active and entry.get('argon'):gas_modes+=['argon']
  for name in gas_modes:
   g=self._gases[name];amount=min(g['remaining'],p[name+'_flow_mol_s']*dt);g['remaining']-=amount;g['consumed']+=amount
   if amount+1e-15<p[name+'_flow_mol_s']*dt:
    if name in ['fuel','oxidant']:self.flame=self.igniting=False
    self._stop('GAS_EXHAUSTED');active=False
  if self.igniting:self.ignition_elapsed+=dt
  if self.igniting and self.ignition_elapsed>=p['flame_ignition_s']-1e-12:self.igniting=False;self.flame=True
  feed=0.;k=0.;fraction=p['atomization_fraction'];B0=self._beam;X0=self._tube
  if active and stage in ['aspirate','flame_aspirate']:
   volume=min(self.sample['volume'],p['liquid_rate_m3_s']*dt);mass=volume*self.sample['concentration'];self.sample['volume']-=volume
   if stage=='aspirate':self._pipette_v+=volume;self._pipette+=mass
   else:
    feed=mass*p['nebulization_fraction']*fraction/dt;self._waste+=mass*(1-p['nebulization_fraction']);self._deposit+=mass*p['nebulization_fraction']*(1-fraction);self._waste_v+=volume*(1-p['nebulization_fraction']);self._evaporated_v+=volume*p['nebulization_fraction']
  if active and stage=='dispense':
   volume=min(self._pipette_v,p['liquid_rate_m3_s']*dt);mass=self._pipette*volume/self._pipette_v if self._pipette_v else 0.;self._pipette_v-=volume;self._pipette-=mass;self._tube_v+=volume;self._tube+=mass;X0=self._tube
  if active and stage in ['dry','pyrolysis','atomize','clean']:
   v=self._tube_v*(-math.expm1(-p['drying_rate_s_inv']*dt));self._tube_v-=v;self._evaporated_v+=v
  if active and stage=='pyrolysis':
   mass=self._tube*(-math.expm1(-p['pyrolysis_loss_rate_s_inv']*dt));self._tube-=mass;self._exhaust+=mass;X0=self._tube
  if active and stage in ['atomize','clean']:k=p['atomization_rate_s_inv'];fraction=p['atomization_fraction'] if stage=='atomize' else 1.
  if active and stage=='clean':
   old=self._deposit;released_deposit=old*(-math.expm1(-p['atomization_rate_s_inv']*dt));self._deposit-=released_deposit;self._exhaust+=released_deposit
  b=1/p['beam_residence_s'];eb=math.exp(-b*dt);ek=math.exp(-k*dt);released=X0*(1-ek);self._tube=X0*ek
  if k:
   kernel=dt*eb if abs(b-k)<1e-10*max(b,k) else (ek-eb)/(b-k)
   Bnew=B0*eb+fraction*k*X0*kernel+feed*(-math.expm1(-b*dt))/b
   self._deposit+=(1-fraction)*released
  else:Bnew=B0*eb+feed*(-math.expm1(-b*dt))/b
  incoming=fraction*released+feed*dt;exhaust=max(0.,incoming+B0-Bnew);self._beam=max(0.,Bnew);self._exhaust+=exhaust
  if active:self.elapsed_s+=dt;self._stage_elapsed+=dt;self._pending_area+=exhaust/b;self._pending_dt+=dt
 def _due(self):return min(max(0.,self._plan[self._plan_index]['duration']-self._stage_elapsed),max(0.,self._next_detector-self.time_s))
 def _event(self):
  end=self._stage_elapsed>=self._plan[self._plan_index]['duration']-1e-12
  if self._next_detector<=self.time_s+1e-12 or end:self._detector()
  if end:
   finished=self.stage
   # Evaporated volume is liquid-equivalent bookkeeping; finite drying may leave liquid and invalidate injection preparation.
   if finished=='dry' and self._tube_v>1e-12:self._qc.append('INCOMPLETE_DRYING')
   if finished=='clean' and self._tube_v>1e-12:self._qc.append('RESIDUAL_LIQUID')
   self._plan_index+=1;self._stage_elapsed=0.
   if self._plan_index>=len(self._plan):self._finish_assay()
   else:self.stage=self._plan[self._plan_index]['stage']
 def step(self,dt_s):
  validate(number('s',0,86400),dt_s)
  if dt_s==0:return self.read()
  target=self.time_s+dt_s;events=0
  while self.time_s<target-1e-12:
   if self.phase!='running':
    delta=target-self.time_s
    if self.igniting:delta=min(delta,max(1e-12,self.plant['flame_ignition_s']-self.ignition_elapsed))
    if self._parking:
     due=self.injector/self.plant['motion_speed_m_s'] if self.injector>1e-12 else abs(self.sampler_arm-math.pi/2)/self.plant['rotation_speed_rad_s']
     if due>1e-12:delta=min(delta,due)
    self._physical(delta);self.time_s+=delta;continue
   due=self._due();delta=min(target-self.time_s,due)
   if delta>0:self._physical(delta);self.time_s+=delta
   if self.phase=='running' and due<=delta+1e-12:self._event();events+=1
   if events>MAX_TRACE_POINTS*4:raise RuntimeError('event budget exceeded')
  if self.time_s<target:self._physical(target-self.time_s);self.time_s=target
  self.revision+=1;self._archive_operation();return self.read()
 def _operation_snapshot(self):return dict(protocol_version=PROTOCOL_VERSION,instance_id=self.instance_id,operation_id=self.operation_id,status=self.phase,state_revision=self.revision,clock={'domain':'simulation','time_s':self.time_s},result=self._result(),observations=self.session().read(instance_id=self.instance_id)['observations'],error=dict(code=self.warning,message=self.warning) if self.phase=='failed' else None)
 def _archive_operation(self):
  if self.operation_id and self.phase in ['succeeded','failed','cancelled'] and self.operation_id not in self._operation_records:self._operation_records[self.operation_id]=self._operation_snapshot()

class JenaContraa800Session:
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
        m=export_jena_contraa800_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.1',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        def timing(key):
            if key.startswith('detector.') and h._last_point:return h._last_point['time_s'],h._last_point['sample_window_s'] if key!='detector.last_sample_time_s' else None
            if key.startswith('result.'):
                points=[p for a in h.assays for p in a['points'] if p['acquiring']]
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
        key=(self._principal,h.instance_id,request_id);digest=json.dumps(['measurement.cancel',operation_id])
        if key in h._requests:
            old,response=h._requests[key]
            if old!=digest:raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(response)
        result=h.invoke('measurement.stop') if snapshot['status']=='running' else {'phase':snapshot['status']}
        response=dict(protocol_version=PROTOCOL_VERSION,instance_id=h.instance_id,request_id=request_id,operation_id=operation_id,status='succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None)
        h._requests[key]=(digest,copy.deepcopy(response));return response

LIMITATIONS=[
'contrAA800D includes flame and graphite furnace with motorized swivel changeover and AS-GF liquid sampler. SolidAA/hydride hardware optional and absent; emission mode documented but unsupported here. No built-in LCD: six readouts are a simulation-only external overlay, not ASpectCS GUI.',
'Manufacturer body780x775x625mm and170kg, AS-GF250x380x550mm and7.2kg separate. Combined177.2kg is derived assembly mass, not the body mass. Component mass/inertia and joint speeds remain engineering proxies.',
'185–900nm coverage;0.002nm resolution documented at200nm only,0.4pm wavelength reproducibility not Gaussian sigma. Host Gaussian line width and event noise are assumed independently; no uniform OEMresolution/precision claim across all wavelengths.',
'Real atomic line wavelengths retain source air/vacuum medium; no silent medium conversion. Chosen line/element checked.21-point local synthetic detector spectrum is not the200-pixel OEMCCD readout; simultaneous linear-reference background is an explicit simplified surrogate, not OEMIBC/LSBC.',
'Only documented measured/prepared liquid elemental concentrations drive physical samples. Concentration0–1000kg/m3 is an explicit numerical liquid-model domain, not an OEM analytical range. Solid mass fractions without documented digestion mass/volume cannot become solution concentrations. Censored/line-only records remain query references. Atomic lines from60elements do not mean60 measured sample concentrations.',
'crm_* liquid records come from the shared CRM element library: NIST1640a/1643f certificate-printed mass concentrations, NIST3100-series certified mass fractions as a declared gravimetric working standard (0.1g to100mL, c=w*m/V) and NIST/USGS solid CRM values as a declared total digest (0.25g to50mL, complete recovery assumed). Preparations are declared library inputs, not performed or measured; certificate values are not contrAA measurements and AAS responses stay synthetic. Ru/Ir lines have no CRM concentration.',
'Selected-element mass moves through sample, pipette, graphite tube, beam, deposit, waste/exhaust and removed warehouses. Unmeasured matrix and full chemical products are not inferred. Flame aerosol fraction/atomization and graphite pyrolysis loss and release are host coefficients, not measured recovery.',
'Flame uses air/acetylene engineering flow/inventory and finite ignition. Graphite program dry/pyrolysis/atomize/clean is ramp limited to3000K/s and3273.15K; standard tube50uL, PIN40uL alternative not installed. AS-GF minimum1uL and108positions documented; actual cannula stroke/arm geometry approximated.',
'Graphite acquisition samples hold atomization and flush windows; baseline and prior stages retained. Full sample drying/ashing chemistry, matrix modifiers, adsorption memory and radiative heat exchange unvalidated. Net furnace C*dT is not total electrical/chemical energy.',
'Observed normalized transmission is converted to apparent absorbance with finite floor/saturation and host event noise. Linear background uses two simultaneous side pixels. Integrated corrected absorbance is calibrated by >=3 supplied standard observations with real liquid reference concentrations. Samples never return source concentration directly.',
'Calibration is specific to element,line,mode,volume,background, burner position and furnace program. Burner height/rotation affect geometry and invalidate response calibration; no unmeasured flame-height response or angular path correction is invented. Only interpolation yields concentrations; saturated or outside-range results stay null. Negative noise integrals and all replicates retained; no zero clipping or silent outlier deletion.',
'Selected-element ledger is fixed to the first nonblank sample element per instance; create another instance for another element. Fast-sequential multi-element queue is not implemented. Only current sample vessel is host-loaded; AS-GF positions are explicit geometry. Full multi-vial scheduling, automatic dilution/spikes/modifiers/preconcentration and OEMGUI menus unsupported. User dilution is an externally prepared sample assumption, not performed by this model.',
'Cancellation retains partial peaks, withdrawn liquid and element residues; cold external clean transfers residues to waste rather than returning them to the donor. Fuel/oxidant/argon inventory use is not full stoichiometry; actual pressure basis, seals, flame geometry and emergency flushing unmodeled.',
'Queries do not advance clock or RNG. Historical operation result snapshots are owner protected and retained after record removal/method changes. Process-local256result/32curve capacities are engineering bounds, not manufacturer storage promises. No reboot persistence or OS/filesystem truth isolation.',
'Factory sensitivity anchors Cu324nm flame100mm burner andPb283nm graphite20uL are limited documented benchmarks, not generalized gains. Full noise/parameter convergence, detection limits, matrix recovery, actual calibration, fault probabilities, OEMhardware/HIL and robot reverse contact remain not_run.'
]
cap('sampler.park',obj({}),obj({'parking':BOOL}),['sampler_arm','sampler_probe'],'冷态宿主归位：先有限抬针再转到pi/2安全停泊角，门扫掠必须已归位','device_command','evaluator')
slot('sampler.parking',BOOL,'1','正在抬针并回安全停泊位','scene',c='sampler_arm')

def export_jena_contraa800_contract_metadata():
 caps={};slots={};run=['measurement.phase','measurement.stage','measurement.elapsed_s','measurement.progress','measurement.completed_repeats'];detector=['detector.absorbance','detector.background_absorbance','detector.corrected_absorbance','detector.last_sample_time_s'];results=['result.concentration_kg_m3','result.integrated_absorbance_s'];motion=['atomizer.angle_rad','sampler.angle_rad','sampler_arm.angle_rad','injector.position_m'];thermal=['furnace.target_k','furnace.actual_k','truth.ledger']
 immediate={'burner.configure':['blank.valid','calibration.ready'],'sampler.park':['sampler.parking'],'plant.configure':['plant.configured',*thermal],'sample.load':['sample.loaded','truth.ledger'],'sample.unload':['sample.loaded','truth.ledger'],'fluidics.clean':['injector.position_m','truth.ledger'],'cover.set':['cover.opened','cover.lift_m'],'lamp_door.set':['lamp_door.opened','lamp_door.angle_rad'],'furnace.open':['furnace.opened','furnace.angle_rad'],'power.set':['power.enabled','flame.lit','flame.igniting','measurement.phase','measurement.stage','safety.warning','furnace.target_k'],'method.configure':['method.mode','method.element','method.wavelength_m','blank.valid','calibration.ready'],'flame.ignite':['flame.igniting'],'flame.extinguish':['flame.lit','flame.igniting','measurement.phase','measurement.stage','furnace.target_k','safety.warning'],'measurement.start':[*run,*detector,*results,'safety.warning'],'measurement.stop':['measurement.phase','measurement.stage','furnace.target_k','safety.warning',*results],'calibration.fit':['storage.curve_count'],'calibration.select':['calibration.ready'],'records.remove':['storage.result_count']}
 onstep={'sampler.park':['sampler.parking','injector.position_m','sampler_arm.angle_rad'],'power.set':thermal,'method.configure':['atomizer.angle_rad'],'burner.configure':['burner.height_m','burner.position_m','burner.angle_rad'],'flame.ignite':['flame.lit','flame.igniting','truth.ledger'],'measurement.start':[*run,*detector,*results,*motion,*thermal,'blank.valid','storage.result_count','safety.warning'],'measurement.stop':thermal}
 complete={'measurement.start':[*results,'measurement.phase','measurement.stage','measurement.progress','measurement.completed_repeats','blank.valid','storage.result_count','truth.ledger']}
 observes={'sampler.park':['sampler.parking'],'plant.configure':['plant.configured'],'sample.load':['sample.loaded'],'sample.unload':['sample.loaded'],'cover.set':['cover.opened'],'lamp_door.set':['lamp_door.opened'],'furnace.open':['furnace.opened'],'power.set':['power.enabled'],'flame.ignite':['flame.igniting'],'flame.extinguish':['flame.lit'],'measurement.start':['measurement.phase'],'measurement.stop':['measurement.phase'],'process.measure':run[:4]+detector[:2]+results[:1]+['safety.warning'],'result.read':run[:2]+results[:1],'result.export':run[:2]+results[:1],'records.list':['storage.result_count','storage.curve_count'],'product.read':['truth.ledger'],'blank.read':['blank.valid']}
 for k,(schema,unit,origin,vis,components,write) in SLOTS.items():
  window=k.startswith('detector.') or k.startswith('result.');slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,origin=origin,visibility=vis,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'JenaContraa800Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode='interval_mean' if window else 'instant',time_domain='simulation',window_description='最新完整光学采样窗，浓度/面积对应完整采集窗口与冻结方法；非实机光电强度' if window else None),source_ids=['simulation_assumption'])
 import ast
 # Inspect actual exception calls; quoted source text is never an error code.
 literal_errors={node.exc.args[0].value for node in ast.walk(ast.parse(Path(__file__).read_text())) if isinstance(node,ast.Raise) and isinstance(node.exc,ast.Call) and isinstance(node.exc.func,ast.Name) and node.exc.func.id=='ActionRejected' and node.exc.args and isinstance(node.exc.args[0],ast.Constant) and isinstance(node.exc.args[0].value,str)}
 errors=sorted({'INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT'}|literal_errors)
 for k,(inp,out,components,label,kind,vis,example,_) in CAPS.items():
  unsupported=k in UNSUPPORTED;query=kind=='query';async_=k=='measurement.start';effects=[dict(state_id=state,timing=timing,description_zh={'immediate':'原子设定/维护/启动状态，不推进物理时钟','on_step':'宿主唯一时钟推进有限动作/光学采样与元素/热量账本','on_completion':'完整采集与观察标定结果写入，取消不伪完成'}[timing]) for timing,table in [('immediate',immediate),('on_step',onstep),('on_completion',complete)] for state in table.get(k,[])]
  caps[k]=dict(function_zh=label,kind=kind,visibility=vis,component_ids=components,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.jena_contraa800.JenaContraa800Session.invoke',invocation=f'session.invoke(instance_id="contraa_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if query or unsupported else [dict(description_zh='可信宿主配置/样品与浓度源、模式/谱线/气源/门/冷炉与匹配观察空白/标线按调用检查',state_ids=['power.enabled','cover.opened','furnace.opened','flame.lit','measurement.phase'],enforced_by='runtime',check='Runtime._invoke/Session principal ACL')],state_effects=[] if query or unsupported else effects,observes=[] if unsupported else observes.get(k,[]),model_effects=dict(mode='none',description_zh='模型联动另由真实几何/场景验证绑定；纯查询不动作',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='所有独立取样重复的有限雾化或炉程序和完整光学采样完成' if async_ else '原子返回，不隐式推进时间',completion_state_ids=['measurement.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','elemental_liquid_sample','fuel_oxidant_argon','atomizer','CCD_surrogate','result_store_256','curve_store_32'],partial_failure_zh='取消/失气/断电保留已转移样本、残料和已采峰；观测结果及部分作用不得回滚'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in errors]+([dict(code='GAS_EXHAUSTED',meaning_zh='气源耗尽导致停止，保留物料及已采数据',side_effects='partial')] if async_ else []),example_arguments=copy.deepcopy(example),verification=[],limitations=LIMITATIONS,source_ids=['manufacturer_primary','simulation_assumption'])
 mech=dict(component_ids=['sample_inlet','spray_chamber','burner_head','graphite_tube','graphite_furnace','atomizer_swivel','sampler_tray','sampler_arm','sampler_probe','xe_lamp','echelle_grating','ccd_detector'],state_ids=list(slots),meaning_zh='元素质量守恒的火焰/石墨炉原子化及连续光源局部吸收观测，真实参考浓度与合成响应分开',implementation='lumped',equations=['Flame liquid withdrawal gives element mass c*V; aerosol fraction and atomization yield feed beam, rejected aerosol/deposit separately retained.','Graphite thermal program has explicit ramps and holds; during atomization dX/dt=-kX, dB/dt=eta*k*X-B/tau; exact analytical transfer preserves selected-element mass.','A(t,lambda)=K*B(t)*Gaussian(lambda-lambda_line)+background; T=10^(-A), apparent A=-log10(max(T,floor))+assumed event noise.','Linear reference background is the mean of simultaneous flank pixels; integrated area=sum((Acorrected-observed_blank)*sample_window).','Fit integrated_area=slope*reference_concentration*injection_volume/dilution+intercept using centered scaled least squares; interpolation only.','Registered element=sum(sample,pipette,tube,beam,deposit,exhaust,waste,removed); liquid-equivalent evaporation tracked separately from gas volume.','Fuel/oxidant/argon initial=remaining+consumed; no invented whole reaction stoichiometry. Net furnace heat=C*(T-Tinitial).'],parameters={},source_ids=['manufacturer_primary','simulation_assumption'],limitations=LIMITATIONS)
 spec=importlib.util.spec_from_file_location('_contraa_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'atomic_absorption':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'；非厂家完整标定。'
 mech['parameters']={k.lower():v for k,v in mech['parameters'].items()};return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'atomic_absorption':mech})

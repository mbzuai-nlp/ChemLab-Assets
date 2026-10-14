"""SX20 standard stopped-flow; real optical references and explicit synthetic reaction states."""
from __future__ import annotations
import copy,hashlib,importlib.util,json,math
from pathlib import Path
import numpy as np
from scipy.optimize import curve_fit
from functools import lru_cache
ROOT=Path(__file__).resolve().parents[2];SLUG='applied_sx20';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-074';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/applied_sx20_kinetics_library/templates.json'
BOOL={'type':'boolean'};TEXT={'type':'string'}
class ActionRejected(ValueError):pass
def number(unit,low=None,high=None):
 s={'type':'number','x-unit':unit}
 if low is not None:s['minimum']=low
 if high is not None:s['maximum']=high
 return s
def obj(p):return dict(type='object',properties=p,required=list(p),additionalProperties=False)
def arr(s,n=None):
 x=dict(type='array',items=s)
 if n is not None:x.update(minItems=n,maxItems=n)
 return x
def integer(low,high):return dict(number('1',low,high),type='integer')
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
  if not isinstance(v,dict) or not set(s['required'])<=v.keys():raise ActionRejected('INVALID_ARGUMENT')
  for k,x in v.items():
   if k not in s['properties']:raise ActionRejected('INVALID_ARGUMENT')
   validate(s['properties'][k],x)
 elif t=='array':
  if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
  for x in v:validate(s['items'],x)
 if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')



PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
PLANT=obj({'rapid_cell_installed':BOOL,'drive_b_capacity_m3':{'type':'number','enum':[2.5e-6,1e-6,.5e-6,.25e-6,.1e-6],'x-unit':'m^3'},'flow_m3_s':number('m^3/s',1e-8,1e-4),'return_flow_m3_s':number('m^3/s',1e-8,1e-4),'fluid_density_kg_m3':number('kg/m^3',500,2000),'fluor_gain_v':number('V',.001,1000),'abs_noise_sd':number('1',0,1),'fluor_noise_sd_v':number('V',0,1),'seed':integer(0,2147483647),'accept_surrogate_model':BOOL})
SAMPLE=obj({'sample_id':TEXT,'template_id':TEXT,'solvent':TEXT,'stock_concentration_mol_m3':number('mol/m^3',1e-8,10),'k1_s_inv':number('1/s',.001,1e5),'k2_s_inv':number('1/s',.001,1e5),'intermediate_response':number('1',0,10),'product_response':number('1',0,10),'accept_hypothetical_kinetics':BOOL})
METHOD=obj({'mode':dict(type='string',enum=['absorbance','fluorescence']),'mix_ratio_a_b':number('1',1,25),'stop_volume_m3':number('m^3',1e-9,2.5e-6),'delay_s':number('s',0,1000),'wavelength_m':number('m',190e-9,850e-9),'path_m':{'type':'number','enum':[.01,.002,.005,.001],'x-unit':'m'},'duration_s':number('s',1e-4,1000),'points':integer(6,4000),'timebase':dict(type='string',enum=['uniform','log']),'filter_low_m':number('m',300e-9,650e-9),'filter_high_m':number('m',300e-9,650e-9)})
PLANT_EXAMPLE=dict(rapid_cell_installed=False,drive_b_capacity_m3=2.5e-6,flow_m3_s=2e-6,return_flow_m3_s=2e-6,fluid_density_kg_m3=1000.,fluor_gain_v=10.,abs_noise_sd=0.,fluor_noise_sd_v=0.,seed=74,accept_surrogate_model=True)
SAMPLE_EXAMPLE=dict(sample_id='fluorescein_demo',template_id='pcad_010',solvent='ethanol',stock_concentration_mol_m3=.002,k1_s_inv=40.,k2_s_inv=8.,intermediate_response=2.,product_response=.15,accept_hypothetical_kinetics=True)
METHOD_EXAMPLE=dict(mode='absorbance',mix_ratio_a_b=1.,stop_volume_m3=140e-9,delay_s=0.,wavelength_m=482.5e-9,path_m=.01,duration_s=.5,points=101,timebase='uniform',filter_low_m=505e-9,filter_high_m=535e-9)
FLUOR_METHOD=dict(METHOD_EXAMPLE,mode='fluorescence',wavelength_m=425e-9,path_m=.002)
POINT=obj({'trigger_time_s':number('s',0),'reaction_age_s':number('s',0),'sample_time_s':number('s',0),'signal':number('1'),'absorbance':nullable(number('1')),'fluorescence_v':nullable(number('V')),'unit':dict(type='string',enum=['1','V'])})
MEASURE=obj({'phase':PHASE,'stage':TEXT,'elapsed_s':number('s',0),'completed_points':integer(0,4000),'signal':nullable(number('1')),'unit':TEXT,'warning':TEXT})
RESULT=obj({'phase':PHASE,'complete':BOOL,'points':arr(POINT),'settings_json':TEXT,'template_source':TEXT,'evidence':TEXT,'limitations':arr(TEXT)})
LEDGER=obj({'registered_volume_m3':number('m^3',0),'stock_a_volume_m3':number('m^3',0),'stock_b_volume_m3':number('m^3',0),'cell_volume_m3':number('m^3',0),'stop_volume_m3':number('m^3',0),'waste_volume_m3':number('m^3',0),'volume_residual_m3':number('m^3'),'registered_moiety_mol':number('mol',0),'stock_moiety_mol':number('mol',0),'cell_moiety_mol':number('mol',0),'stop_moiety_mol':number('mol',0),'waste_moiety_mol':number('mol',0),'moiety_residual_mol':number('mol'),'modeled_mass_residual_kg':number('kg'),'optical_state_fractions':arr(number('1',0,1),3),'template_id':nullable(TEXT),'evidence':TEXT})
OBS=obj({'time_s':dict(arr(number('s',0)),minItems=6,maxItems=4000),'signal':dict(arr(number('1')),minItems=6,maxItems=4000)})
TEMPLATE=obj({'substance_id':TEXT,'name':TEXT,'source':TEXT,'conditions_json':TEXT,'abs_wavelength_m':arr(number('m')),'epsilon_m2_mol':arr(number('m^2/mol')),'em_wavelength_m':arr(number('m')),'emission_relative':arr(number('1')),'quantum_yield':number('1',0,1),'kinetic_constants_s_inv':{'type':'null'},'evidence':TEXT})
CAPS={}
def cap(k,inp,out,c,z,kind='device_command',vis='agent',ex=None):CAPS[k]=[inp,out,c,z,kind,vis,{} if ex is None else ex,[]]
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body','drive_a','drive_b','detector'],'宿主一次声明标准/快速池、物理注射器容量和未标定流速/密度/噪声','configuration','evaluator',PLANT_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['drive_a','drive_b','cell'],'仅首次外部装液；A含真实光学参考，B为无吸收触发溶剂代理；两步动力学为宿主假设','configuration','evaluator',SAMPLE_EXAMPLE)
cap('bath.cover',obj({'opened':BOOL}),obj({'opened':BOOL}),['bath_cover'],'外部开启恒温浴罩；绕X85°是工程铰链代理，非厂家铰链证据','device_command','evaluator',{'opened':False})
cap('valves.set',obj({'drive':BOOL}),obj({'drive':BOOL}),['valve_a','valve_b'],'外部手动90°Load/Drive，非自动转阀','device_command','evaluator',{'drive':True})
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','controller'],'供电逻辑；断电立即取消但不回滚液量',ex={'enabled':True})
cap('gas.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['gas_inlet','drive_ram'],'宿主确认外部气源连接；不等于实际压力传感/闭环','device_command','evaluator',{'enabled':True})
cap('method.configure',METHOD,obj({'configured':BOOL}),['monochromator','detector','cell'],'混合比必须等于实装针筒容量比；换探测通道/光程须断电；所选2012手册单通道配置','configuration','agent',METHOD_EXAMPLE)
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['drive_yoke','stop_plunger','stop_valve','cell','detector'],'有限驱动、自动排空停流针筒、停流触发与毫秒死时间；不补造不可见早期样点')
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['drive_ram','stop_plunger'],'停止并保留已消耗试剂/已观测样点，无液量回滚')
cap('fluid.flush',obj({}),obj({'flushed':BOOL}),['cell','stop_plunger','waste_port'],'宿主已完成外部排液/新缓冲液装池；不是自动清洗机器人','device_command','evaluator')
cap('process.measure',obj({}),MEASURE,['host_screen'],'最近真实生成观测，无数据null','query')
cap('result.read',obj({}),RESULT,['host_screen'],'真实静态谱驱动的显式模拟时间曲线，来源身份不泄漏','query')
cap('kinetics.fit',OBS,obj({'k_s_inv':number('1/s',0),'k_std_s_inv':nullable(number('1/s',0)),'amplitude':number('1'),'offset':number('1'),'rmse':number('1',0),'evidence':TEXT}),['host_screen'],'仅用调用者时间/信号拟合单指数；不能将两步动力学单指数k认作机理真值','analysis','agent',{'time_s':[0.,.1,.2,.3,.4,.5],'signal':[1.,.818730753,.670320046,.548811636,.449328964,.367879441]})
cap('calibration.zero',obj({'observed_blank':number('1'),'mode':dict(type='string',enum=['absorbance','fluorescence'])}),obj({'correction':number('1')}),['detector','fluor_detector'],'按mode仅修正对应吸光度或荧光电压空白；返回数码兼容旧接口，物理单位见两个修正状态槽；非原厂校准',ex={'observed_blank':.01,'mode':'absorbance'})
cap('template.read',obj({'substance_id':TEXT}),TEMPLATE,['host_screen'],'公开真实参考谱；不含任何虚构真实速率','query','agent',{'substance_id':'pcad_010'})
cap('product.read',obj({}),LEDGER,['cell','waste_port'],'私有体积/共同密度质量/染团守恒与光学态，非化学产物鉴定','query','evaluator')
cap('sequential.start',obj({}),obj({}),['drive_ram'],'SQ1四驱动针筒/双ram/老化液路未建；明确不支持，不将两步反应偷换为顺序混合')
cap('dual_channel.start',obj({}),obj({}),['detector','fluor_detector'],'DD1同时双通道选件未实现')
cap('temperature.set',obj({'temperature_k':number('K')}),obj({}),['bath'],'温控来自外部循环器，未建立温度依赖反应速率/自动恒温','device_command','agent',{'temperature_k':298.15})
UNSUPPORTED={'sequential.start','dual_channel.start','temperature.set'}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='host_screen',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','宿主配置已声明','setpoint',c='body')
slot('plant.rapid_cell_installed',BOOL,'1','快速池选件明确安装','setpoint',c='cell')
slot('sample.loaded',BOOL,'1','试剂已外部装载','scene',c='cell')
slot('power.enabled',BOOL,'1','软件供电','setpoint',c='controller',w=('power.set','enabled'))
slot('gas.enabled',BOOL,'1','外部气源逻辑可用','setpoint',c='gas_inlet')
slot('cover.opened',BOOL,'1','水浴盖开闭代理','scene',c='bath_cover')
slot('cover.angle_rad',number('rad',0,math.radians(85)),'rad','水浴盖工程铰链角','scene',c='bath_cover')
slot('valves.drive',BOOL,'1','宿主手动阀已位于Drive','scene',c='valve_a')
slot('valves.angle_rad',number('rad',0,math.pi/2),'rad','两只手动阀同位90°代理','scene',c='valve_a')
slot('drive.stroke_m',number('m',0,.08),'m','共同驱动柱塞累计位移，80mm为工程映射行程','scene',c='drive_yoke')
slot('stop.stroke_m',number('m',0,.08),'m','停流针筒柱塞位移，80mm工程映射','scene',c='stop_plunger')
slot('stop.valve_angle_rad',number('rad',0,math.pi),'rad','自动Drive/Empty阀180°','scene',c='stop_valve')
slot('fluid.needs_flush',BOOL,'1','再次采集前需外部完整清洗','derived',c='cell')
slot('scan.phase',PHASE,'1','操作生命周期')
slot('scan.stage',dict(type='string',enum=['idle','returning','driving','acquiring']),'1','实际排空/驱动/触发后采样阶段')
slot('scan.elapsed_s',number('s',0),'s','本次过程经过时间')
slot('scan.completed_points',integer(0,4000),'1','已实际观测样点数')
slot('scan.signal',nullable(number('1')),'1','最新显示数值（无量纲数码）；物理量分别在absorbance/fluorescence_v槽')
slot('scan.absorbance',nullable(number('1')),'1','最近观测的吸光度；荧光观测时null')
slot('scan.fluorescence_v',nullable(number('V')),'V','最近观测的宿主荧光电压代理；吸收观测时null')
slot('scan.unit',TEXT,'1','吸收无量纲或荧光宿主电压V')
slot('scan.last_sample_time_s',nullable(number('s')),'s','最新实际观测时刻')
slot('method.wavelength_m',nullable(number('m')),'m','检测/激发单色器波长','setpoint',c='monochromator')
slot('method.mix_ratio',nullable(number('1')),'1','物理针筒容量混合比','setpoint',c='drive_yoke')
slot('method.stop_volume_m3',nullable(number('m^3')),'m^3','一次驱动停止体积','setpoint',c='stop_plunger')
slot('cell.dead_time_s',nullable(number('s')),'s','标准约1ms/快速约0.5ms','setpoint',c='cell')
slot('safety.warning',TEXT,'1','无私有身份告警')
slot('truth.ledger',LEDGER,'1','体积/染团与模板身份仅评估器','process_truth','evaluator','cell')
slot('calibration.absorbance_correction',number('1'),'1','调用者空白的累计模拟吸光度修正，非原厂校准','setpoint',c='detector')
slot('calibration.fluorescence_correction_v',number('V'),'V','调用者空白的累计模拟荧光电压修正，非原厂校准','setpoint',c='fluor_detector')
@lru_cache(maxsize=1)
def _templates():return {t['substance_id']:t for t in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']}

class AppliedSx20Runtime:
 def __init__(self,instance_id='sx20_01'):
  self.instance_id=instance_id;self.templates=_templates();self.time_s=0.;self.revision=0;self.plant=None;self.method=None;self.sample=None
  self.powered=False;self.gas=False;self.opened=True;self.valves=False;self.needs_flush=False;self.phase='idle';self.stage='idle';self.elapsed_s=0.;self.warning='';self.points=[]
  self.operation_id='';self._op_count=0;self._stop_time=None;self._acquire_grid=[];self._drive_volume=0.;self._registered_v=0.;self._registered_n=0.
  self._va=0.;self._vb=0.;self._vc=0.;self._vs=0.;self._vw=0.;self._nc=0.;self._ns=0.;self._nw=0.;self._correction={'absorbance':0.,'fluorescence':0.}
  self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._operation_records={};self._result_method=None;self._rng=np.random.default_rng(0)
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return AppliedSx20Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def debug_snapshot(self):
  return copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in ['templates','_rng']})|{'_rng_state':copy.deepcopy(self._rng.bit_generator.state)}
 @property
 def cell_volume(self):return 5e-9 if self.plant and self.plant['rapid_cell_installed'] else 20e-9
 @property
 def stop_capacity(self):return 1e-6 if self.plant and self.plant['rapid_cell_installed'] else 2.5e-6
 @property
 def dead_time(self):return .0005 if self.plant and self.plant['rapid_cell_installed'] else .001
 @staticmethod
 def fractions(t,k1,k2):
  a=math.exp(-k1*t)
  if abs(k1-k2)<=1e-7*max(k1,k2):b=k1*t*math.exp(-k1*t)
  else:
   # Stable for large ages and nearly equal rates without exp(+large).
   b=k1*(math.exp(-k2*t)-a)/(k1-k2)
  b=max(0.,min(1.-a,b));return [a,b,max(0.,1.-a-b)]
 def _ledger(self):
  p=self.plant;s=self.sample;stock_n=self._va*s['stock_concentration_mol_m3'] if s else 0.
  rv=self._registered_v-(self._va+self._vb+self._vc+self._vs+self._vw);rn=self._registered_n-(stock_n+self._nc+self._ns+self._nw)
  age=0. if self._stop_time is None else self.dead_time+max(0.,self.time_s-self._stop_time)
  f=self.fractions(age,s['k1_s_inv'],s['k2_s_inv']) if s else [1.,0.,0.]
  return dict(registered_volume_m3=self._registered_v,stock_a_volume_m3=self._va,stock_b_volume_m3=self._vb,cell_volume_m3=self._vc,stop_volume_m3=self._vs,waste_volume_m3=self._vw,volume_residual_m3=rv,registered_moiety_mol=self._registered_n,stock_moiety_mol=stock_n,cell_moiety_mol=self._nc,stop_moiety_mol=self._ns,waste_moiety_mol=self._nw,moiety_residual_mol=rn,modeled_mass_residual_kg=rv*p['fluid_density_kg_m3'] if p else 0.,optical_state_fractions=f,template_id=s['template_id'] if s else None,evidence='equal-density incompressible volume and common chromophore-moiety bookkeeping; A/I/P are hypothetical optical states, not identified chemical products; no waste speciation')
 def read(self,visibility='agent'):
  if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
  m=self.method;last=self.points[-1] if self.points else None
  out={'plant.configured':bool(self.plant),'plant.rapid_cell_installed':bool(self.plant and self.plant['rapid_cell_installed']),'sample.loaded':bool(self.sample),'power.enabled':self.powered,'gas.enabled':self.gas,'cover.opened':self.opened,'cover.angle_rad':math.radians(85) if self.opened else 0.,'valves.drive':self.valves,'valves.angle_rad':math.pi/2 if self.valves else 0.,'drive.stroke_m':.08*(1.-self._va/2.5e-6) if self.sample else 0.,'stop.stroke_m':.08*self._vs/self.stop_capacity,'stop.valve_angle_rad':math.pi if self.stage=='returning' else 0.,'fluid.needs_flush':self.needs_flush,'scan.phase':self.phase,'scan.stage':self.stage,'scan.elapsed_s':self.elapsed_s,'scan.completed_points':len(self.points),'scan.signal':last['signal'] if last else None,'scan.absorbance':last['absorbance'] if last else None,'scan.fluorescence_v':last['fluorescence_v'] if last else None,'scan.unit':last['unit'] if last else ('1' if m['mode']=='absorbance' else 'V') if m else 'unavailable','scan.last_sample_time_s':last['sample_time_s'] if last else None,'method.wavelength_m':m['wavelength_m'] if m else None,'method.mix_ratio':m['mix_ratio_a_b'] if m else None,'method.stop_volume_m3':m['stop_volume_m3'] if m else None,'cell.dead_time_s':self.dead_time if self.plant else None,'safety.warning':self.warning}
  out.update({'calibration.absorbance_correction':self._correction['absorbance'],'calibration.fluorescence_correction_v':self._correction['fluorescence']})
  if visibility!='agent':out['truth.ledger']=self._ledger()
  return copy.deepcopy(out)
 def invoke(self,capability_id,**arguments):
  if capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability_id][0],arguments)
  if capability_id in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  if CAPS[capability_id][4] in ['query','analysis']:
   out=self._invoke(capability_id,arguments);validate(CAPS[capability_id][1],out);return copy.deepcopy(out)
  snapshot={k:copy.deepcopy(v) for k,v in self.__dict__.items() if k!='templates'}
  try:
   out=self._invoke(capability_id,arguments);validate(CAPS[capability_id][1],out);self._archive_operation();self.revision+=1;return copy.deepcopy(out)
  except Exception:
   t=self.templates;self.__dict__.clear();self.__dict__.update(snapshot);self.templates=t;raise
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _archive_operation(self):
  if self.operation_id and self.phase in ('succeeded','cancelled','failed') and self.operation_id not in self._operation_records:
   self._operation_records[self.operation_id]=dict(operation_id=self.operation_id,status=self.phase,result=copy.deepcopy(self._invoke('process.measure',{})))
 def _stop(self,phase='cancelled',warning=''):
  if self.phase=='running':self.phase=phase
  self.stage='idle';self.warning=warning
 def _invoke(self,k,a):
  if k=='plant.configure':
   if self.plant or self.powered or self.sample:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_surrogate_model']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   self.plant=copy.deepcopy(a);self._rng=np.random.default_rng(a['seed']);return {'configured':True}
  if k=='sample.load':
   self._idle()
   if not self.plant:raise ActionRejected('PLANT_REQUIRED')
   if self.sample or self.powered or self.gas or not self.opened or self.valves:raise ActionRejected('EXTERNAL_LOAD_REQUIRED')
   if not a['accept_hypothetical_kinetics']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   if not a['sample_id'] or a['template_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   if a['solvent']!=self.templates[a['template_id']]['conditions']['solvent']:raise ActionRejected('SOLVENT_MISMATCH')
   self.sample=copy.deepcopy(a);self._va=2.5e-6;self._vb=self.plant['drive_b_capacity_m3'];self._vc=self.cell_volume
   self._registered_v=self._va+self._vb+self._vc;self._registered_n=self._va*a['stock_concentration_mol_m3'];return {'loaded':True}
  if k=='bath.cover':
   if a['opened']:self._stop(warning='COVER_OPEN');self.gas=False
   self.opened=a['opened'];return {'opened':self.opened}
  if k=='valves.set':
   self._idle()
   if self.gas:raise ActionRejected('ISOLATE_GAS')
   self.valves=a['drive'];return {'drive':self.valves}
  if k=='power.set':
   if a['enabled'] and not self.plant:raise ActionRejected('PLANT_REQUIRED')
   self.powered=a['enabled']
   if not self.powered:self._stop(warning='POWER_OFF')
   return {'enabled':self.powered}
  if k=='gas.set':
   if a['enabled'] and (not self.plant or self.opened or not self.valves):raise ActionRejected('FLUID_NOT_READY')
   self.gas=a['enabled']
   if not self.gas:self._stop(warning='GAS_OFF')
   return {'enabled':self.gas}
  if k=='method.configure':
   self._idle()
   if not self.plant:raise ActionRejected('PLANT_REQUIRED')
   if self.powered and (self.method is None or a['mode']!=self.method['mode'] or a['path_m']!=self.method['path_m']):raise ActionRejected('POWER_OFF_FOR_OPTICS')
   if not math.isclose(a['mix_ratio_a_b'],2.5e-6/self.plant['drive_b_capacity_m3'],rel_tol=1e-12):raise ActionRejected('PHYSICAL_RATIO_MISMATCH')
   paths=[.005,.001] if self.plant['rapid_cell_installed'] else [.01,.002]
   if a['path_m'] not in paths:raise ActionRejected('CELL_PATH_MISMATCH')
   if a['mode']=='fluorescence' and a['path_m']!=(.005 if self.plant['rapid_cell_installed'] else .002):raise ActionRejected('FLUOR_EXCITATION_PATH')
   if a['stop_volume_m3']<7*self.cell_volume-1e-18 or a['stop_volume_m3']>self.stop_capacity:raise ActionRejected('STOP_VOLUME_RANGE')
   if a['filter_low_m']>=a['filter_high_m']:raise ActionRejected('FILTER_RANGE')
   self.method=copy.deepcopy(a);return {'configured':True}
  if k=='acquisition.start':
   self._idle()
   if not self.sample or not self.method:raise ActionRejected('SAMPLE_METHOD_REQUIRED')
   if not self.powered or not self.gas or self.opened or not self.valves:raise ActionRejected('FLUID_NOT_READY')
   if self.needs_flush:raise ActionRejected('EXTERNAL_FLUSH_REQUIRED')
   t=self.templates[self.sample['template_id']];m=self.method;v=m['stop_volume_m3'];f=m['mix_ratio_a_b']/(1+m['mix_ratio_a_b'])
   if v*f>self._va+1e-18 or v*(1-f)>self._vb+1e-18:raise ActionRejected('INSUFFICIENT_REAGENT')
   if not t['abs_wavelength_m'][0]<=m['wavelength_m']<=t['abs_wavelength_m'][-1]:raise ActionRejected('SOURCE_COVERAGE')
   if m['mode']=='fluorescence':
    if abs(m['wavelength_m']-t['conditions']['reference_excitation_m'])>1e-15:raise ActionRejected('EXCITATION_REFERENCE_REQUIRED')
    if m['filter_low_m']<t['em_wavelength_m'][0] or m['filter_high_m']>t['em_wavelength_m'][-1]:raise ActionRejected('SOURCE_COVERAGE')
   self._archive_operation();self._result_method=copy.deepcopy(self.method)
   self.phase='running';self.stage='returning' if self._vs>1e-18 else 'driving';self.elapsed_s=0.;self.points=[];self._drive_volume=0.;self._stop_time=None;self.needs_flush=True;self.warning=''
   self._op_count+=1;self.operation_id=self.instance_id+'_op_'+str(self._op_count)
   self._acquire_grid=(np.linspace(0,m['duration_s'],m['points']) if m['timebase']=='uniform' else np.r_[0.,np.geomspace(m['duration_s']/(m['points']-1)**2,m['duration_s'],m['points']-1)]).tolist()
   return dict(phase=self.phase,operation_id=self.operation_id)
  if k=='acquisition.stop':self._stop(warning='CANCELLED');return {'phase':self.phase}
  if k=='fluid.flush':
   self._idle()
   if not self.sample or self.gas or self.valves or not self.opened:raise ActionRejected('EXTERNAL_FLUSH_REQUIRED')
   self._vw+=self._vc;self._nw+=self._nc;self._vc=self.cell_volume;self._nc=0.;self._registered_v+=self.cell_volume
   self.needs_flush=False;self._stop_time=None;return {'flushed':True}
  if k=='process.measure':
   s=self.read();return dict(phase=self.phase,stage=self.stage,elapsed_s=self.elapsed_s,completed_points=len(self.points),signal=s['scan.signal'],unit=s['scan.unit'],warning=self.warning)
  if k=='result.read':return dict(phase=self.phase,complete=self.phase=='succeeded',points=self.points,settings_json=json.dumps(self._result_method if self._result_method is not None else self.method,sort_keys=True),template_source=TEMPLATE_REL,evidence='simulated stopped-flow trace from measured static optical spectrum and explicit hypothetical two-step kinetics',limitations=LIMITATIONS)
  if k=='template.read':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   t=self.templates[a['substance_id']]
   return dict(substance_id=t['substance_id'],name=t['name'],source=t['source'],conditions_json=json.dumps(t['conditions'],sort_keys=True),abs_wavelength_m=t['abs_wavelength_m'],epsilon_m2_mol=t['epsilon_m2_mol'],em_wavelength_m=t['em_wavelength_m'],emission_relative=t['emission_relative'],quantum_yield=t['quantum_yield'],kinetic_constants_s_inv=None,evidence=t['evidence'])
  if k=='product.read':return self._ledger()
  if k=='calibration.zero':
   self._idle()
   if not self.method or a['mode']!=self.method['mode']:raise ActionRejected('CALIBRATION_MODE')
   self._correction[a['mode']]-=a['observed_blank'];return {'correction':self._correction[a['mode']]}
  if k=='kinetics.fit':
   x=np.asarray(a['time_s'],dtype=float);y=np.asarray(a['signal'],dtype=float)
   if len(x)!=len(y) or np.any(x[1:]<=x[:-1]):raise ActionRejected('ANALYSIS_INPUT')
   # Fit dimensionless time and centered signal. Absolute signal thresholds,
   # unscaled time bounds, and squaring the physical residual break valid fits.
   span=x[-1]-x[0];scale=float(np.max(np.abs(y)))
   if scale==0:raise ActionRejected('ANALYSIS_INPUT')
   yn=y/scale;spread=float(np.ptp(yn));anchor=yn[-1]
   if spread==0:raise ActionRejected('ANALYSIS_INPUT')
   xs=(x-x[0])/span;ys=(yn-anchor)/spread
   def f(t,amplitude,k,offset):return offset+amplitude*np.exp(-k*t)
   try:
    with np.errstate(over='raise',invalid='raise',divide='raise'):
     pars,cov=curve_fit(f,xs,ys,p0=[ys[0],1.,0.],bounds=([-np.inf,0.,-np.inf],[np.inf,np.inf,np.inf]),maxfev=20000)
     if not np.isfinite(pars).all() or not np.isfinite(cov).all():raise ValueError()
     residual=f(xs,*pars)-ys;largest=float(np.max(np.abs(residual)))
     normalized_rmse=largest*math.sqrt(float(np.mean((residual/largest)**2))) if largest else 0.
     wide_scale=np.longdouble(spread)*np.longdouble(scale)
     out=dict(k_s_inv=float(np.longdouble(pars[1])/span),k_std_s_inv=float(np.longdouble(math.sqrt(max(cov[1,1],0.)))/span),amplitude=float(np.longdouble(pars[0])*wide_scale),offset=float((np.longdouble(pars[2])*spread+anchor)*scale),rmse=float(np.longdouble(normalized_rmse)*wide_scale))
     if not all(math.isfinite(v) for v in out.values()):raise ValueError()
   except (RuntimeError,ValueError,ArithmeticError):raise ActionRejected('FIT_UNIDENTIFIABLE') from None
   return dict(**out,evidence='single exponential least squares of caller observations, time zero at first supplied point; scale-normalized fit with conditional covariance, not mechanism identification; two-step traces can be poor fits')
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _flow(self,dv):
  m=self.method;ca=self.sample['stock_concentration_mol_m3'];f=m['mix_ratio_a_b']/(1+m['mix_ratio_a_b'])
  # Limit actual transfers by both physical stocks so rounding at exhaustion
  # cannot create negative volume or fabricate fluid/chromophore supply.
  dv=min(dv,self._va/f,self._vb/(1-f));da=min(self._va,dv*f);db=min(self._vb,dv*(1-f));dv=da+db
  if dv<=0:return
  self._va-=da;self._vb-=db;incoming=ca*da;cin=incoming/dv
  old=self._nc;new=min(old+incoming,max(0.,old+(cin*self._vc-old)*(-math.expm1(-dv/self._vc))))
  self._nc=new;self._ns+=old+incoming-new;self._vs+=dv;self._drive_volume+=dv
 def _signal(self,age):
  t=self.templates[self.sample['template_id']];s=self.sample;m=self.method
  eps=max(0.,float(np.interp(m['wavelength_m'],t['abs_wavelength_m'],t['epsilon_m2_mol'])))
  f=self.fractions(age,s['k1_s_inv'],s['k2_s_inv']);response=f[0]+s['intermediate_response']*f[1]+s['product_response']*f[2]
  concentration=self._nc/self._vc;absorbance=eps*concentration*m['path_m']
  if m['mode']=='absorbance':return absorbance*response+self._correction[m['mode']]
  x=np.asarray(t['em_wavelength_m']);y=np.maximum(t['emission_relative'],0.);lo=m['filter_low_m'];hi=m['filter_high_m'];xx=np.r_[lo,x[(x>lo)&(x<hi)],hi]
  fraction=float(np.trapezoid(np.interp(xx,x,y),xx)/np.trapezoid(y,x))
  return self.plant['fluor_gain_v']*(-math.expm1(-math.log(10)*absorbance))*t['quantum_yield']*fraction*response+self._correction[m['mode']]
 def _observe(self,t):
  relative=self._acquire_grid[len(self.points)]+self.method['delay_s'];age=self.dead_time+relative
  sigma=self.plant['abs_noise_sd'] if self.method['mode']=='absorbance' else self.plant['fluor_noise_sd_v']
  signal=self._signal(age)+(float(self._rng.normal(0,sigma)) if sigma>0 else 0.)
  self.points.append(dict(trigger_time_s=relative,reaction_age_s=age,sample_time_s=t,signal=signal,absorbance=signal if self.method['mode']=='absorbance' else None,fluorescence_v=signal if self.method['mode']=='fluorescence' else None,unit='1' if self.method['mode']=='absorbance' else 'V'))
  if len(self.points)==len(self._acquire_grid):self._stop('succeeded')
 def step(self,dt_s):
  validate(number('s',0,3600),dt_s)
  if dt_s==0:return self.read()
  left=dt_s;cursor=self.time_s
  while left>1e-12 and self.phase=='running':
   if self.stage=='returning':
    duration=self._vs/self.plant['return_flow_m3_s'];h=min(left,duration);dv=self._vs if h>=duration else min(self._vs,h*self.plant['return_flow_m3_s']);dn=self._ns if h>=duration else self._ns*dv/self._vs if self._vs else 0.
    self._vs-=dv;self._ns-=dn;self._vw+=dv;self._nw+=dn
    if h>=duration:self._vs=0.;self._ns=0.;self.stage='driving'
   elif self.stage=='driving':
    duration=(self.method['stop_volume_m3']-self._drive_volume)/self.plant['flow_m3_s'];h=min(left,max(0.,duration));dv=min(self.method['stop_volume_m3']-self._drive_volume,h*self.plant['flow_m3_s']);self._flow(max(0.,dv))
    if h>=duration:
     self._stop_time=cursor+h;self.stage='acquiring'
     if self.method['delay_s']==0:self._observe(self._stop_time)
   elif self.stage=='acquiring':
    next_time=self._stop_time+self.method['delay_s']+self._acquire_grid[len(self.points)];h=min(left,max(0.,next_time-cursor))
    if cursor+h>=next_time-1e-12:self._observe(next_time)
   else:raise RuntimeError('invalid stage')
   self.elapsed_s+=h;cursor+=h;left-=h
  self.time_s+=dt_s;self.revision+=1;self._archive_operation();return self.read()
class AppliedSx20Session:
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
        m=export_applied_sx20_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.1',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        measured=['scan.signal','scan.absorbance','scan.fluorescence_v','scan.last_sample_time_s']
        last=h.points[-1] if h.points else None
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='unavailable' if v[k] is None else 'estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=last['sample_time_s'] if last and k in measured else h.time_s,sample_window_s=None) for k in ids})
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('process.measure'))
    def cancel(self,operation_id,request_id):
        snapshot=self.poll(operation_id);h=self._host
        if not isinstance(request_id,str) or not request_id:raise ActionRejected('INVALID_ARGUMENT')
        key=(self._principal,h.instance_id,request_id);digest=json.dumps(['acquisition.cancel',operation_id])
        if key in h._requests:
            old,response=h._requests[key]
            if old!=digest:raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(response)
        result=h.invoke('acquisition.stop') if snapshot['status']=='running' else {'phase':snapshot['status']}
        response=dict(protocol_version=PROTOCOL_VERSION,instance_id=h.instance_id,request_id=request_id,operation_id=operation_id,status='succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None)
        h._requests[key]=(digest,copy.deepcopy(response));return response



LIMITATIONS=[
'Manufacturer body dimensions and mass unknown; minimum installation area is not a body bounding box. All geometry sizes are explicit task-layout proxies.',
'Standard20uL/10and2mm/approximately1ms; rapid5uL/5and1mm/approximately0.5ms requires explicit option. Manual also states0.45ms, conflict retained.',
'Absorption R928185–900nm; implemented conservative monochromator190–850nm and source overlap. Fluorescence R6095300–650nm with integrated optical filter, no emission scan.',
'Selected 2012 manual single-channel configuration; not a claim about current SX-series standard equipment. Concurrent abs/fluorescence DD1 and sequential mixing SQ1 four drive syringes/two rams/aging lines are unsupported in this asset.',
'63 real PhotochemCAD static optical records, not63 real reaction time curves. Rates/intermediate/product optical factors are explicit hypothetical host parameters.',
'A→I→P conserves one chromophore moiety; these are optical states, not chemically identified species. No predicted chemical stoichiometry, pH, temperature or solvent-change effects.',
'Independent published AA/DCPI rate56.5±0.6m³/(mol*s) retained as reference only; abstract lacks full conditions, never applied automatically.',
'Volume is incompressible, common host density; cell washout is a CSTR proxy, not true turbulent T-mixer CFD. >=7 cell volumes is model guard, not universal factory limit.',
'Finite drive/return rates and80mm visual strokes are engineering assumptions. Gas pressure and pneumatic transients are not simulated or calibrated.',
'Dead time is an unobserved chemical-age offset at trigger, not a second world-clock increment. Delay contributes to age; first observation cannot precede trigger.',
'Cell kinetic age is idealized uniform at stop; pre-stop residence-age distribution/reaction and bubble/scattering/dispersion artifacts are not modeled.',
'Source solvent/reference excitation and actual spectral coverage enforced. Signed source data retained; response clips negative baselines; no source-width deconvolution.',
'Fluorescence rectangular-filter integration and host voltage gain are uncalibrated; source quantum yield retained. Intermediate/product multipliers are hypothetical response factors.',
'Sampling is ideal instantaneous uniform/log timebase, no real ADC bandwidth, detector convolution or factory count limits;6–4000points and duration limits are host resources.',
'Noise is optional explicit Gaussian assumption, not OEM noise. Caller blank correction is not factory calibration; legacy observed_blank/correction numbers represent absorbance or volts according to mode, with explicit unit-specific correction slots.',
'Single-exponential fit uses caller observations with normalized time/signal and t=0 at the first supplied observation; finite output and covariance required. Fitting a two-step trace does not identify mechanism; covariance is not measurement uncertainty. Legacy signal/amplitude/offset numeric aliases follow caller units.',
'External load/valve/cover/flush are trusted-host events, not robotic or autonomous physical manipulations. Flushing retains stop-syringe contents for next automatic empty.',
'External bath circulation and temperature kinetics unsupported. Component mass, friction, true inertia and failure probabilities unknown; URDF0.1kg proxies disclosed.',
'Bath cover X85deg hinge is an engineering proxy; real cover hinging not verified. Separate simulation telemetry board is not OEM LCD.',
'Result points retain the acquisition-time method after reconfiguration until the next start. Owned terminal poll summaries and request dedup persist only in memory, not full historical traces. Session roles are not OS/filesystem isolation; no native async Matterix envelope, real robot, safety certification or hardware/HIL.'
]
def export_applied_sx20_contract_metadata():
 caps={};slots={}
 observations=['scan.completed_points','scan.signal','scan.absorbance','scan.fluorescence_v','scan.unit','scan.last_sample_time_s']
 stop=['scan.phase','scan.stage','stop.valve_angle_rad','safety.warning']
 corrections=['calibration.absorbance_correction','calibration.fluorescence_correction_v']
 immediate={
  'plant.configure':['plant.configured','plant.rapid_cell_installed','cell.dead_time_s'],
  'sample.load':['sample.loaded','truth.ledger'],
  'bath.cover':['cover.opened','cover.angle_rad','gas.enabled',*stop],
  'valves.set':['valves.drive','valves.angle_rad'],
  'power.set':['power.enabled',*stop],'gas.set':['gas.enabled',*stop],
  'method.configure':['method.wavelength_m','method.mix_ratio','method.stop_volume_m3','scan.unit'],
  'acquisition.start':['scan.phase','scan.stage','scan.elapsed_s',*observations,'stop.valve_angle_rad','fluid.needs_flush','safety.warning','truth.ledger'],
  'acquisition.stop':stop,'fluid.flush':['fluid.needs_flush','truth.ledger'],
  'calibration.zero':corrections,
 }
 on_step={'acquisition.start':['drive.stroke_m','stop.stroke_m','stop.valve_angle_rad','scan.phase','scan.stage','scan.elapsed_s',*observations,'truth.ledger']}
 on_completion={'acquisition.start':['scan.phase','scan.stage']}
 observed={
  'plant.configure':['plant.configured'],'sample.load':['sample.loaded'],
  'bath.cover':['cover.opened'],'valves.set':['valves.drive'],'power.set':['power.enabled'],'gas.set':['gas.enabled'],
  'acquisition.start':['scan.phase'],'acquisition.stop':['scan.phase'],
  'process.measure':['scan.phase','scan.stage','scan.elapsed_s','scan.completed_points','scan.signal','scan.unit','safety.warning'],
  'result.read':['scan.phase',*observations],'calibration.zero':corrections,'product.read':['truth.ledger'],
 }
 def effects_for(key):
  descriptions={'immediate':'经检查的外部事件、配置、操作初始化或取消；不推进时间，也不回滚已转移液量','on_step':'后续宿主step实际排空/驱动、触发和采样；仅移交现存液体与染团','on_completion':'最后调度样点完成后设置succeeded和idle，保留实际观测与液体账本'}
  return [dict(state_id=state,timing=timing,description_zh='仅mode对应的模拟空白修正改变；另一路保持不变，非原厂校准' if key=='calibration.zero' else descriptions[timing]) for timing,mapping in [('immediate',immediate),('on_step',on_step),('on_completion',on_completion)] for state in mapping.get(key,[])]
 for k,(s,u,o,v,c,w) in SLOTS.items():
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'AppliedSx20Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','INITIALIZATION_ONLY','EVIDENCE_ACK_REQUIRED','BUSY','PLANT_REQUIRED','EXTERNAL_LOAD_REQUIRED','ISOLATE_GAS','FLUID_NOT_READY','POWER_OFF_FOR_OPTICS','PHYSICAL_RATIO_MISMATCH','CELL_PATH_MISMATCH','FLUOR_EXCITATION_PATH','STOP_VOLUME_RANGE','FILTER_RANGE','SAMPLE_METHOD_REQUIRED','EXTERNAL_FLUSH_REQUIRED','INSUFFICIENT_REAGENT','SOURCE_COVERAGE','EXCITATION_REFERENCE_REQUIRED','SOLVENT_MISMATCH','ANALYSIS_INPUT','FIT_UNIDENTIFIABLE','CALIBRATION_MODE']
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  asynchronous=k=='acquisition.start';query=kind in ['query','analysis'];unsupported=k in UNSUPPORTED
  caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.applied_sx20.AppliedSx20Session.invoke',invocation=f'session.invoke(instance_id="sx20_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),
   preconditions=[] if query or unsupported else [dict(description_zh='宿主配置、外部装液/阀位、供电气源、单通道、池选件、物理混合比、清洗和实测波段；原子拒绝',state_ids=['power.enabled','gas.enabled','cover.opened','valves.drive','fluid.needs_flush','scan.phase'],enforced_by='runtime',check='Runtime._invoke and trusted-principal Session')],
   state_effects=[] if query or unsupported else effects_for(k),
   observes=observed.get(k,[]),model_effects=dict(mode='none',description_zh='明确未实现' if unsupported else '待独立场景验证',binding_ids=[]),
   execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',completion_criterion='驱动至停止体积，经历延迟并完成所有真实调度样点' if asynchronous else '事务返回，不额外推进世界时钟',completion_state_ids=['scan.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',retry='same_request_id',resources=['host_clock','fluid_circuit','request_cache'],partial_failure_zh='取消、开盖、断电或断气保留实际液量/染团和已取得观测，必须外部清洗才允许下一次；旧曲线绑定采集时method，后续启动才清空；终态poll公开摘要受原身份保护，取消历史终态不干扰新采集；历史与去重限进程内'),
   errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=LIMITATIONS,source_ids=['manufacturer_primary','simulation_assumption'])
 mech=dict(component_ids=['drive_a','drive_b','drive_yoke','drive_ram','valve_a','valve_b','cell','stop_plunger','stop_valve','detector','fluor_detector','monochromator','waste_port'],state_ids=list(slots),meaning_zh='标准两液停流、有限体积和死时间、真实静态光学谱与显式两步光学态速率；不把SQ1顺序混合偷换为两步机理',implementation='lumped',equations=[
 'fA=capacityA/(capacityA+capacityB); mixed input c=cA*fA; both plungers share fractional stroke.',
 'Cell washout n_new=c*V+(n_old-c*V)*exp(-dV/V), evaluated with expm1; stop gains n_old+n_in-n_new. Both actual liquid transfers are stock-bounded and n_in=c_stock*dV_A; no negative reagent at floating-point exhaustion.',
 'V_registered=V_A+V_B+V_cell+V_stop+V_waste; common-density mass residual=rho*volume residual. This is not complete chemical species or energy conservation.',
 'Automatic return moves stop contents to waste; external flush moves cell to waste and adds one cell volume of fresh blank, retaining stop contents.',
 'A=exp(-k1*t); I=k1*(exp(-k2*t)-exp(-k1*t))/(k1-k2), equal-rate limit=k1*t*exp(-k1*t); P=1-A-I.',
 'Reaction age=nominal cell dead time+delay+scheduled time after delay; step sole simulation clock. No observable points before trigger.',
 'Absorbance=epsilon(lambda)*c_cell*path*(A+rI*I+rP*P). epsilon[M^-1cm^-1]*0.1 => m^2/mol, c in mol/m^3, path in m.',
 'Fluor voltage=host_gain*(1-10^(-epsilon*c_cell*excitation_path))*QY*integral(filter_band,source_em)/integral(source_em)*(A+rI*I+rP*P). Ideal rectangular filter and response factors uncalibrated.',
 'Source emission negative baselines clipped for integration; original signed arrays preserved. Standard fluorescence path2mm, rapid5mm per manual.',
 'Noise independently Normal(0,host_sigma); observed blank correction is additive and supplied by caller, not hidden ground truth.',
 'Single-exponential fit: y=offset+amplitude*exp(-k*t), nonnegative k; fit only supplied observations and report residual.',
 'Standard drive2.5mL+2.5mL; asymmetric installed B1/.5/.25/.1mL yields ratios2.5/5/10/25; no software-only continuous ratio change.'
 ],parameters={},source_ids=['manufacturer_primary','photochemcad','simulation_assumption'],limitations=LIMITATIONS)
 spec=importlib.util.spec_from_file_location('_sx20_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
 ann.annotate_export(SLUG,caps,slots,{'stopped_flow_kinetics':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'；非原厂标定。'
 mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'stopped_flow_kinetics':mech})


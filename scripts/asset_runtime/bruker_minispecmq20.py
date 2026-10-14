"""mq20 TD-NMR: finite-pulse Bloch propagation, measured relaxivities and explicit synthetic calibration."""
from __future__ import annotations
import copy,hashlib,importlib.util,json,math
from pathlib import Path
import numpy as np
from scipy.linalg import expm
from scipy.optimize import minimize_scalar
ROOT=Path(__file__).resolve().parents[2];SLUG='bruker_minispecmq20';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-066';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/bruker_minispecmq20_relaxometry_library/templates.json'
RESOLUTION=20e-9;FIXED_HZ=20e6
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
PLANT=obj({'vt_installed':BOOL,'fixed_temperature_k':number('K',308.15,318.15),'initial_temperature_k':number('K',268.15,338.15),'thermal_capacity_j_k':number('J/K',.01,1e6),'thermal_ua_w_k':number('W/K',.001,1e6),'rf_nutation_rad_s_sqrt_w':number('rad/(s*W^0.5)',1,1e7),'receiver_dead_s':number('s',0,.01),'noise_sd':number('1',0,.5),'receiver_gain':number('1',.001,10),'offset_hz':number('Hz',0,100000),'rf_budget_j':number('J',0,1e9),'seed':integer(0,2147483647),'accept_uncalibrated_model':BOOL})
PLANT_EXAMPLE=dict(vt_installed=True,fixed_temperature_k=310.15,initial_temperature_k=310.15,thermal_capacity_j_k=2.,thermal_ua_w_k=2.,rf_nutation_rad_s_sqrt_w=math.pi/(2*50e-6*10),receiver_dead_s=10e-6,noise_sd=.0002,receiver_gain=.8,offset_hz=0.,rf_budget_j=1000.,seed=66,accept_uncalibrated_model=True)
FAST_PLANT=dict(PLANT_EXAMPLE,noise_sd=0.)
SOLUTION=obj({'sample_id':TEXT,'template_id':TEXT,'metal_concentration_mol_m3':number('mol/m^3',0,100),'medium_id':TEXT,'pH':nullable(number('1',0,14)),'volume_m3':number('m^3',1e-9,5e-6),'tube_od_m':number('m',.001,.018),'baseline_r1_s_inv':number('s^-1',.0001,1e5),'baseline_r2_s_inv':number('s^-1',.0001,1e5),'missing_r2_m3_mol_s':nullable(number('m^3/(mol*s)',0,1e6)),'accept_synthetic_baselines':BOOL,'accept_incomplete_source_conditions':BOOL,'accept_unvalidated_concentration':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='sample_01',template_id='patent_picolinate_p04218_pc2a1pa_sym',metal_concentration_mol_m3=1.,medium_id='water',pH=None,volume_m3=5e-7,tube_od_m=.01,baseline_r1_s_inv=.3,baseline_r2_s_inv=.5,missing_r2_m3_mol_s=None,accept_synthetic_baselines=True,accept_incomplete_source_conditions=True,accept_unvalidated_concentration=False)
METHOD=obj({'sequence':dict(type='string',enum=['inversion_recovery','cpmg']),'rf_power_w':number('W',.001,300),'pulse90_s':number('s',20e-9,.001),'pulse180_s':number('s',20e-9,.002),'recycle_s':number('s',0,100),'delays_s':arr(number('s',20e-9,100)),'echo_spacing_s':number('s',20e-9,1),'points':integer(4,32768),'averages':integer(1,16)})
METHOD_EXAMPLE=dict(sequence='inversion_recovery',rf_power_w=100.,pulse90_s=50e-6,pulse180_s=100e-6,recycle_s=2.,delays_s=[.001,.01,.05,.1,.2,.4,.8,1.2],echo_spacing_s=.005,points=8,averages=1)
CPMG_METHOD=dict(METHOD_EXAMPLE,sequence='cpmg',points=80)
POINT=obj({'delay_s':number('s',0),'signal':number('1',-1,1),'sample_time_s':number('s',0),'average':integer(1,16),'index':integer(0,32767)})
MEASURE=obj({'phase':PHASE,'elapsed_s':number('s',0),'completed_points':integer(0,524288),'signal':nullable(number('1')),'temperature_k':number('K'),'warning':TEXT})
RESULT=obj({'phase':PHASE,'complete':BOOL,'sequence':TEXT,'points':arr(POINT),'template_source':TEXT,'r1_evidence':TEXT,'r2_evidence':TEXT,'waveform_evidence':TEXT,'limitations':arr(TEXT)})
FIT=obj({'quantity':dict(type='string',enum=['T1','T2']),'time_s':number('s',0),'rate_s_inv':number('s^-1',0),'amplitude':number('1'),'offset':number('1'),'rmse':number('1',0),'r_squared':number('1'),'points':integer(4,32768),'method':TEXT,'evidence':TEXT})
LEDGER=obj({'registered_volume_m3':number('m^3',0),'attached_volume_m3':number('m^3',0),'external_volume_m3':number('m^3',0),'volume_residual_m3':number('m^3'),'rf_initial_j':number('J',0),'rf_remaining_j':number('J',0),'rf_delivered_j':number('J',0),'rf_residual_j':number('J'),'thermal_bath_j':number('J'),'thermal_stored_j':number('J'),'thermal_residual_j':number('J'),'template_id':TEXT,'r1_s_inv':nullable(number('s^-1')),'r2_s_inv':nullable(number('s^-1')),'magnetization':arr(arr(number('1'),3),3),'evidence':TEXT})
CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},[])
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body','vt_probe'],'宿主冻结探头配置与明确热/线圈/接收假设','configuration','evaluator',PLANT_EXAMPLE)
cap('solution.register',SOLUTION,obj({'registered':BOOL}),['probe_bore'],'注册预装核磁管；不是移液','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.load',obj({'sample_id':TEXT}),obj({'loaded':BOOL,'attachment_event':TEXT}),['probe_bore','sample_tube'],'开盖逻辑挂接样品管，不声称机器人动作','device_command','evaluator',{'sample_id':'sample_01'})
cap('sample.unload',obj({'recipient_id':TEXT}),obj({'unloaded':BOOL,'attachment_event':TEXT}),['probe_bore','sample_tube'],'完整管卸载，液体不消耗','device_command','evaluator',{'recipient_id':'external_rack'})
cap('method.configure',METHOD,obj({'configured':BOOL}),['pulse_programmer','host_screen'],'IR或CPMG，20ns时基/功率/点数/累积','configuration','agent',METHOD_EXAMPLE)
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet'],'控制电子学与热代理；永久磁场不会随断电消失',ex={'enabled':True})
cap('lid.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['sample_lid'],'样品口盖45mm工程升降；开盖取消采集，软件守卫非原厂认证',ex={'opened':False})
cap('thermal.set_temperature',obj({'temperature_k':number('K',268.15,338.15)}),obj({'target_k':number('K')}),['vt_probe'],'只有已安装VT可设−5..65°C；step推进一阶热模型','configuration','agent',{'temperature_k':310.15})
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['rf_coil','receiver','pulse_programmer','host_screen'],'真实时间事件调度有限时长Bloch RF/恢复/回波，非调用即返回理想谱')
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['receiver','host_screen'],'关RF保留已采集点及能量；不伪造完整数据')
cap('process.measure',obj({}),MEASURE,['host_screen'],'只读末次采样和当前模型温度','query')
cap('signal.read',obj({}),RESULT,['host_screen'],'已采集数字信号和分量证据','query')
cap('relaxation.fit',obj({}),FIT,['host_screen'],'只用观测曲线拟合T1/T2和倒数弛豫率，不读取隐藏T','analysis')
cap('template.read',obj({'substance_id':TEXT}),obj({'substance_id':TEXT,'r1_m3_mol_s':number('m^3/(mol*s)'), 'r2_m3_mol_s':nullable(number('m^3/(mol*s)')),'conditions_json':TEXT,'source':TEXT,'evidence':TEXT}),['host_screen'],'公共参考r1/r2与条件，不泄露当前样品身份','query','agent',{'substance_id':SAMPLE_EXAMPLE['template_id']})
cap('product.read',obj({}),LEDGER,['probe_bore','rf_coil','vt_probe'],'私有物料/RF能量/热量与Bloch真值','query','evaluator')
cap('magnet.set_frequency',obj({'frequency_hz':number('Hz',2e6,65e6)}),obj({}),['permanent_magnet'],'固定mq20不能把电子学2–65MHz当可调磁体','configuration','agent',{'frequency_hz':60e6})
cap('diffusion.measure',obj({}),obj({}),['probe_bore'],'未安装/验证PGU梯度；明确UNSUPPORTED','analysis')
UNSUPPORTED={'magnet.set_frequency','diffusion.measure'}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='host_screen',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','已显式初始化','setpoint',c='body')
slot('method.configured',BOOL,'1','有效方法','setpoint')
slot('power.enabled',BOOL,'1','电子学供电','setpoint',c='power_inlet',w=('power.set','enabled'))
slot('magnet.field_t',number('T'),'T','永久磁体约0.47T，即使断电仍存在','setpoint',c='permanent_magnet')
slot('magnet.frequency_hz',number('Hz'),'Hz','名义质子20MHz，不从约0.47T反算伪精确值','setpoint',c='permanent_magnet')
slot('lid.opened',BOOL,'1','盖已开启','scene',c='sample_lid',w=('lid.set','opened'))
slot('lid.travel_m',number('m',0,.045),'m','盖向上45mm工程代理','scene',c='sample_lid')
slot('sample.loaded',BOOL,'1','预装管逻辑挂接','scene',c='sample_tube')
slot('probe.vt_installed',BOOL,'1','VT探头宿主固定配置','setpoint',c='vt_probe')
slot('thermal.target_k',number('K',268.15,338.15),'K','目标温度，不是瞬间实际温度','setpoint',c='vt_probe',w=('thermal.set_temperature','temperature_k'))
slot('thermal.temperature_k',number('K'),'K','一阶集总热状态；未校准传感器','derived',c='vt_probe')
slot('thermal.ready',BOOL,'1','距目标≤0.1K工程门限','derived',c='vt_probe')
slot('rf.power_w',number('W',0,300),'W','当前有限时长脉冲RF设定功率，非吸收热','derived',c='rf_coil')
slot('scan.phase',PHASE,'1','异步操作状态')
slot('scan.elapsed_s',number('s',0),'s','操作经过时间')
slot('scan.completed_points',integer(0,524288),'1','已采样点含累积')
slot('scan.signal',nullable(number('1')),'1','12bit有符号归一化接收数字信号')
slot('fit.t1_s',nullable(number('s')),'s','纯查询拟合最近完整IR；无数据或失败null')
slot('fit.t2_s',nullable(number('s')),'s','纯查询拟合最近完整CPMG；无数据或失败null')
slot('safety.warning',TEXT,'1','已过滤告警')
slot('truth.ledger',LEDGER,'1','宿主私有溶液/能量/Bloch真值','process_truth','evaluator','probe_bore')
for k in CAPS:
 if CAPS[k][4] not in ['query','analysis'] and k not in UNSUPPORTED:CAPS[k][7].extend(SLOTS)
class BrukerMinispecMq20Runtime:
 def __init__(self,instance_id='mq20_01'):
  self.instance_id=instance_id;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.time_s=0.;self.revision=0;self.plant=None;self.method=None;self.samples={};self.active=None
  self.powered=False;self.opened=True;self.temperature=310.15;self.initial_temperature=310.15;self.target=310.15;self.thermal_bath=0.;self.rf_delivered=0.;self.rf=0.;self.axis='x';self.phase='idle';self.elapsed_s=0.;self.points=[];self.warning='';self.operation_id='';self._op_count=0;self._events=[];self._index=0;self._start=0.
  self.mag=np.tile([0.,0.,1.],(3,1));self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._rng=np.random.default_rng(0)
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return BrukerMinispecMq20Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def debug_snapshot(self):
  v=copy.deepcopy({k:x for k,x in self.__dict__.items() if k not in ['templates','_rng','mag']});v['mag']=self.mag.tolist();v['_rng_state']=copy.deepcopy(self._rng.bit_generator.state);return v
 def _sample(self):return self.samples.get(self.active)
 def _rates(self):
  s=self._sample()
  if not s:return None,None
  r=self.templates[s['template_id']];r2=r['r2_m3_mol_s'] if r['r2_m3_mol_s'] is not None else s['missing_r2_m3_mol_s'];c=s['metal_concentration_mol_m3']
  return s['baseline_r1_s_inv']+r['r1_m3_mol_s']*c,s['baseline_r2_s_inv']+r2*c
 def _ledger(self):
  vol=sum(s['volume_m3'] for s in self.samples.values());inside=self._sample()['volume_m3'] if self.active else 0.;budget=self.plant['rf_budget_j'] if self.plant else 0.;stored=(self.plant['thermal_capacity_j_k']*(self.temperature-self.initial_temperature)) if self.plant else 0.;r1,r2=self._rates()
  return dict(registered_volume_m3=vol,attached_volume_m3=inside,external_volume_m3=vol-inside,volume_residual_m3=vol-inside-(vol-inside),rf_initial_j=budget,rf_remaining_j=max(0.,budget-self.rf_delivered),rf_delivered_j=self.rf_delivered,rf_residual_j=budget-max(0.,budget-self.rf_delivered)-self.rf_delivered,thermal_bath_j=self.thermal_bath,thermal_stored_j=stored,thermal_residual_j=self.thermal_bath-stored,template_id=self._sample()['template_id'] if self.active else '',r1_s_inv=r1,r2_s_inv=r2,magnetization=self.mag.tolist(),evidence='prefilled volume conserved; RF P*dt budget not sample deposited heat; thermal bath C*dT bookkeeping with no RF-to-heat claim')
 def _fit_or_none(self):
  if self.phase!='succeeded':return None
  try:return self._fit()
  except ActionRejected:return None
 def read(self,visibility='agent'):
  if visibility not in ['agent','debug','evaluator']:raise ActionRejected('ACCESS_DENIED')
  f=self._fit_or_none();p=self.points[-1] if self.points else {}
  v={'plant.configured':bool(self.plant),'method.configured':bool(self.method),'power.enabled':self.powered,'magnet.field_t':.47,'magnet.frequency_hz':FIXED_HZ,'lid.opened':self.opened,'lid.travel_m':.045 if self.opened else 0.,'sample.loaded':bool(self.active),'probe.vt_installed':bool(self.plant and self.plant['vt_installed']),'thermal.target_k':self.target,'thermal.temperature_k':self.temperature,'thermal.ready':abs(self.temperature-self.target)<=.1,'rf.power_w':self.rf,'scan.phase':self.phase,'scan.elapsed_s':self.elapsed_s,'scan.completed_points':len(self.points),'scan.signal':p.get('signal'),'fit.t1_s':f['time_s'] if f and f['quantity']=='T1' else None,'fit.t2_s':f['time_s'] if f and f['quantity']=='T2' else None,'safety.warning':self.warning,'truth.ledger':self._ledger()}
  return copy.deepcopy({k:x for k,x in v.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _invalidate(self):self.phase='idle';self.points=[];self.elapsed_s=0.;self.warning=''
 def _stop(self,phase,warning):
  if self.phase=='running':self.phase=phase
  self.rf=0.;self.warning=warning
 def invoke(self,capability_id,**arguments):
  if capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability_id][0],arguments)
  if capability_id in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  old=copy.deepcopy(self.__dict__)
  try:
   result=self._invoke(capability_id,copy.deepcopy(arguments))
   if CAPS[capability_id][4] not in ['query','analysis']:self.revision+=1
   return copy.deepcopy(result)
  except Exception:self.__dict__.clear();self.__dict__.update(old);raise
 def _invoke(self,k,a):
  if k=='plant.configure':
   if self.plant or self.powered:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_uncalibrated_model']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   if abs(a['receiver_dead_s']/RESOLUTION-round(a['receiver_dead_s']/RESOLUTION))>1e-5:raise ActionRejected('TIMING_GRID')
   self.plant=a;self.temperature=a['initial_temperature_k'];self.initial_temperature=self.temperature;self.target=a['fixed_temperature_k'];self._rng=np.random.default_rng(a['seed']);return {'configured':True}
  if k=='solution.register':
   if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
   if not a['sample_id'] or a['sample_id'] in self.samples:raise ActionRejected('REQUEST_CONFLICT')
   if not a['accept_synthetic_baselines'] or not a['accept_incomplete_source_conditions']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   row=self.templates.get(a['template_id'])
   if row is None:raise ActionRejected('UNKNOWN_REFERENCE')
   cond=row['conditions']
   if a['medium_id']!=cond['medium_id'] or (cond['pH'] is not None and (a['pH'] is None or abs(a['pH']-cond['pH'])>1e-8)):raise ActionRejected('TEMPLATE_CONDITION_MISMATCH')
   rg=cond['valid_concentration_range_mol_m3'];c=a['metal_concentration_mol_m3']
   if rg and not rg[0]-1e-10<=c<=rg[1]+1e-10:raise ActionRejected('CONCENTRATION_RANGE')
   if not rg and not a['accept_unvalidated_concentration']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   if row['r2_m3_mol_s'] is None and a['missing_r2_m3_mol_s'] is None:raise ActionRejected('MISSING_R2')
   if row['r2_m3_mol_s'] is not None and a['missing_r2_m3_mol_s'] is not None:raise ActionRejected('MEASURED_R2_OVERRIDE')
   r2=row['r2_m3_mol_s'] if row['r2_m3_mol_s'] is not None else a['missing_r2_m3_mol_s']
   if 2*(a['baseline_r2_s_inv']+r2*c)<a['baseline_r1_s_inv']+row['r1_m3_mol_s']*c-1e-12:raise ActionRejected('BLOCH_RATE_CONSISTENCY')
   if a['tube_od_m']>.010+1e-12 or a['volume_m3']>math.pi*(a['tube_od_m']/2)**2*.12:raise ActionRejected('TUBE_ENVELOPE_PROXY')
   self.samples[a['sample_id']]=a;return {'registered':True}
  if k=='sample.load':
   if not self.opened or self.active or self.phase=='running':raise ActionRejected('SAMPLE_ACCESS_REQUIRED')
   if a['sample_id'] not in self.samples:raise ActionRejected('UNKNOWN_SAMPLE')
   self.active=a['sample_id'];self.mag[:]=[0,0,1];self._invalidate();return dict(loaded=True,attachment_event='host_prefilled_tube_attached_equilibrated_spin_assumption')
  if k=='sample.unload':
   if not self.opened or not self.active or self.phase=='running' or not a['recipient_id']:raise ActionRejected('SAMPLE_ACCESS_REQUIRED')
   self.active=None;self._invalidate();return dict(unloaded=True,attachment_event='host_entire_tube_to_external')
  if k=='power.set':
   self.powered=a['enabled']
   if not self.powered:self._stop('cancelled','POWER_OFF')
   return a
  if k=='lid.set':
   self.opened=a['opened']
   if self.opened:self._stop('cancelled','LID_OPEN')
   return a
  if k=='thermal.set_temperature':
   if not self.plant or not self.plant['vt_installed']:raise ActionRejected('UNSUPPORTED')
   if self.phase=='running':raise ActionRejected('BUSY')
   self.target=a['temperature_k'];self._invalidate();return {'target_k':self.target}
  if k=='method.configure':
   if self.phase=='running':raise ActionRejected('BUSY')
   timing=[a['pulse90_s'],a['pulse180_s'],a['recycle_s'],a['echo_spacing_s']]+a['delays_s']
   if any(abs(x/RESOLUTION-round(x/RESOLUTION))>1e-5 for x in timing):raise ActionRejected('TIMING_GRID')
   if a['sequence']=='inversion_recovery' and (len(a['delays_s'])!=a['points'] or any(y<=x for x,y in zip(a['delays_s'],a['delays_s'][1:]))):raise ActionRejected('DELAY_GRID')
   if a['sequence']=='cpmg' and abs((a['echo_spacing_s']-a['pulse180_s'])/(2*RESOLUTION)-round((a['echo_spacing_s']-a['pulse180_s'])/(2*RESOLUTION)))>1e-5:raise ActionRejected('TIMING_GRID')
   if a['sequence']=='cpmg' and a['echo_spacing_s']<=a['pulse180_s']+2*(self.plant['receiver_dead_s'] if self.plant else .01):raise ActionRejected('ECHO_OVERLAP')
   self.method=a;self._invalidate();return {'configured':True}
  if k=='acquisition.start':
   if self.phase=='running':raise ActionRejected('BUSY')
   if not self.plant or not self.method or not self.powered or self.opened or not self.active:raise ActionRejected('PRECONDITION_FAILED')
   temp=self.templates[self._sample()['template_id']]['conditions']['temperature_k']
   if abs(self.temperature-temp)>.1 or abs(self.target-temp)>.1:raise ActionRejected('TEMPLATE_TEMPERATURE_MISMATCH')
   self._schedule();self.phase='running';self.points=[];self.elapsed_s=0.;self.warning='';self._start=self.time_s;self._index=0;self._op_count+=1;self.operation_id=self.instance_id+':'+str(self._op_count);return dict(phase=self.phase,operation_id=self.operation_id)
  if k=='acquisition.stop':self._stop('cancelled','CANCELLED');return {'phase':self.phase}
  if k=='process.measure':return dict(phase=self.phase,elapsed_s=self.elapsed_s,completed_points=len(self.points),signal=self.points[-1]['signal'] if self.points else None,temperature_k=self.temperature,warning=self.warning)
  if k=='signal.read':
   row=self.templates[self._sample()['template_id']] if self.active else None
   return dict(phase=self.phase,complete=self.phase=='succeeded',sequence=self.method['sequence'] if self.method else '',points=self.points,template_source=row['source'] if row else '',r1_evidence='measured_literature',r2_evidence='measured_literature' if row and row['r2_m3_mol_s'] is not None else 'synthetic_explicit_host',waveform_evidence='simulated_Bloch_from_literature_rates_and_synthetic_baselines',limitations=['Not raw measured decay','No calibrated RF/receiver/noise/thermal model','No chemical shifts, gradients or diffusion','Concentration, medium and temperature conditions restricted'])
  if k=='relaxation.fit':return self._fit()
  if k=='product.read':return self._ledger()
  if k=='template.read':
   r=self.templates.get(a['substance_id'])
   if r is None:raise ActionRejected('UNKNOWN_REFERENCE')
   return dict(substance_id=r['substance_id'],r1_m3_mol_s=r['r1_m3_mol_s'],r2_m3_mol_s=r['r2_m3_mol_s'],conditions_json=json.dumps(r['conditions']),source=r['source'],evidence=r['evidence'])
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _schedule(self):
  """Offsets are elapsed seconds. RF has finite duration, not an instantaneous fake result."""
  m=self.method;ev=[];t=0.
  def pulse(width,axis):
   nonlocal t
   ev.append((t,'on',axis));t+=width;ev.append((t,'off',None))
  for avg in range(m['averages']):
   if m['sequence']=='inversion_recovery':
    for i,delay in enumerate(m['delays_s']):
     t+=m['recycle_s'];pulse(m['pulse180_s'],'x');t+=delay;pulse(m['pulse90_s'],'x');t+=self.plant['receiver_dead_s'];ev.append((t,'sample',(avg+1,i,delay)))
   else:
    t+=m['recycle_s'];pulse(m['pulse90_s'],'x')
    for i in range(m['points']):
     t+=(m['echo_spacing_s']-m['pulse180_s'])/2;pulse(m['pulse180_s'],'y');t+=(m['echo_spacing_s']-m['pulse180_s'])/2;ev.append((t,'sample',(avg+1,i,(i+1)*m['echo_spacing_s'])))
  ev.append((t,'done',None));self._events=ev
 def _advance(self,dt):
  if dt<=0:return
  if self.plant:
   old=self.temperature
   if self.powered:self.temperature=self.target+(old-self.target)*math.exp(-self.plant['thermal_ua_w_k']*dt/self.plant['thermal_capacity_j_k'])
   self.thermal_bath+=self.plant['thermal_capacity_j_k']*(self.temperature-old)
  if self.active and self.plant:
   r1,r2=self._rates();omega=self.plant['rf_nutation_rad_s_sqrt_w']*math.sqrt(self.rf)
   for i,df in enumerate([-self.plant['offset_hz'],0.,self.plant['offset_hz']]):
    wx=omega if self.axis=='x' else 0.;wy=omega if self.axis=='y' else 0.;wz=2*math.pi*df
    if not self.rf:
     x,y,z=self.mag[i];q=wz*dt;e=math.exp(-r2*dt);self.mag[i]=[e*(x*math.cos(q)-y*math.sin(q)),e*(x*math.sin(q)+y*math.cos(q)),1+(z-1)*math.exp(-r1*dt)]
    else:
     # Affine Bloch equation exactly propagated for this finite rectangular RF pulse.
     matrix=np.array([[-r2,-wz,wy,0.],[wz,-r2,-wx,0.],[-wy,wx,-r1,r1],[0,0,0,0.]])
     self.mag[i]=(expm(matrix*dt)@np.r_[self.mag[i],1.])[:3]
   self.rf_delivered+=self.rf*dt
  if self.phase=='running':self.elapsed_s+=dt
  self.time_s+=dt
 def step(self,dt_s):
  validate(number('s',0,1e6),dt_s)
  if dt_s==0:return
  end=self.time_s+dt_s
  while self.time_s<end-1e-13 or (self.phase=='running' and self._index<len(self._events) and self._start+self._events[self._index][0]<=self.time_s+1e-12):
   due=self._start+self._events[self._index][0] if self.phase=='running' else math.inf
   budget_time=self.time_s+(self.plant['rf_budget_j']-self.rf_delivered)/self.rf if self.rf else math.inf
   stop=min(end,due,budget_time);self._advance(max(0.,stop-self.time_s))
   if self.rf and self.rf_delivered>=self.plant['rf_budget_j']-1e-12:
    self.rf_delivered=min(self.rf_delivered,self.plant['rf_budget_j']);self._stop('failed','RF_BUDGET_EXHAUSTED');continue
   if self.phase=='running' and due<=self.time_s+1e-12:
    _,kind,data=self._events[self._index];self._index+=1
    if kind=='on':self.rf=self.method['rf_power_w'];self.axis=data
    elif kind=='off':self.rf=0.
    elif kind=='done':self._stop('succeeded','')
    else:
     sig=-float(self.mag[:,1].mean())*self.plant['receiver_gain']+float(self._rng.normal(0,self.plant['noise_sd']))
     if abs(sig)>1:self._stop('failed','RECEIVER_OVERFLOW');continue
     # 12-bit bipolar normalized ADC: exactly4096 codes, endpoints [-1,1].
     sig=2*round((sig+1)*4095/2)/4095-1;avg,idx,delay=data
     self.points.append(dict(delay_s=delay,signal=sig,sample_time_s=self.time_s,average=avg,index=idx))
   if self.time_s>=end-1e-13 and not(self.phase=='running' and self._start+self._events[self._index][0]<=self.time_s+1e-12):break
  self.revision+=1
 def _fit(self):
  if self.phase!='succeeded' or not self.method:raise ActionRejected('COMPLETE_SCAN_REQUIRED')
  m=self.method;n=m['points'];x=np.array([next(p['delay_s'] for p in self.points if p['index']==i) for i in range(n)]);y=np.array([np.mean([p['signal'] for p in self.points if p['index']==i]) for i in range(n)])
  if np.ptp(y)<.001:raise ActionRejected('INSUFFICIENT_DYNAMIC_RANGE')
  ir=m['sequence']=='inversion_recovery'
  def solve(log_r):
   e=np.exp(-np.exp(log_r)*x);X=np.column_stack([np.ones(n),e]) if ir else e[:,None];coef=np.linalg.lstsq(X,y,rcond=None)[0];res=y-X@coef;return float(res@res),coef
  fit=minimize_scalar(lambda z:solve(z)[0],bounds=(-8.,16.),method='bounded',options={'xatol':1e-10});loss,b=solve(fit.x);rate=math.exp(fit.x)
  if not fit.success or abs(fit.x+8)<1e-4 or abs(fit.x-16)<1e-4:raise ActionRejected('FIT_FAILED')
  return dict(quantity='T1' if ir else 'T2',time_s=1/rate,rate_s_inv=rate,amplitude=float(b[-1]),offset=float(b[0]) if ir else 0.,rmse=math.sqrt(loss/n),r_squared=1-loss/float(np.sum((y-y.mean())**2)),points=n,method='observed signed IR A+Bexp(-R*t) or CPMG Aexp(-R*t); bounded log-rate least-squares, no template/hidden rate access',evidence='inferred_from_simulated_observations')
class BrukerMinispecMq20Session:
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
        m=export_bruker_minispecmq20_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='unavailable' if v[k] is None else 'estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=h.time_s,sample_window_s=None) for k in ids})
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
        if operation_id!=h.operation_id or h._owners.get(operation_id)!=self._principal:raise ActionRejected('ACCESS_DENIED')
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('process.measure'))
    def cancel(self,operation_id,request_id):
        self.poll(operation_id);return self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)

def export_bruker_minispecmq20_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'BrukerMinispecMq20Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  asynchronous=k=='acquisition.start';query=kind in ['query','analysis'];unsupported=k in UNSUPPORTED
  caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.bruker_minispecmq20.BrukerMinispecMq20Session.invoke',invocation=f'session.invoke(instance_id="mq20_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if query or unsupported else [dict(description_zh='宿主配置/电源/样品管/温度与模板条件/权限与时基',state_ids=['power.enabled','lid.opened','thermal.ready','sample.loaded','scan.phase'],enforced_by='runtime',check='atomic Runtime.invoke and trusted-principal Session')],
  state_effects=[] if query or unsupported else [dict(state_id=x,timing='immediate',description_zh='受检设定/宿主挂接/取消保留') for x in effects]+[dict(state_id=x,timing='on_step',description_zh='Bloch/热/RF能量及采样点仅宿主step推进') for x in SLOTS if SLOTS[x][2] in ['derived','process_truth']],
  observes=[],model_effects=dict(mode='none',description_zh='未安装附件无主动变化' if unsupported else '需独立实际求值场景验证',binding_ids=[]),
  execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',completion_criterion='全部延迟或回波点与累积完成，RF关闭' if asynchronous else '事务完成；读和分析不推进时间，未装VT不支持温度设定',completion_state_ids=['scan.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',retry='same_request_id',resources=['host_clock','rf_coil','probe_bore','request_cache'],partial_failure_zh='取消/开盖/断电保留点和已发送RF能量，不伪造完整曲线；请求去重仅进程内。'),
  errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNSUPPORTED','PRECONDITION_FAILED','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','INITIALIZATION_ONLY','EVIDENCE_ACK_REQUIRED','TEMPLATE_CONDITION_MISMATCH','CONCENTRATION_RANGE','MISSING_R2','MEASURED_R2_OVERRIDE','BLOCH_RATE_CONSISTENCY','TUBE_ENVELOPE_PROXY','SAMPLE_ACCESS_REQUIRED','UNKNOWN_SAMPLE','BUSY','TIMING_GRID','DELAY_GRID','ECHO_OVERLAP','TEMPLATE_TEMPERATURE_MISMATCH','COMPLETE_SCAN_REQUIRED','INSUFFICIENT_DYNAMIC_RANGE','FIT_FAILED']],
  example_arguments=copy.deepcopy(ex),verification=[],limitations=['教学Bloch模型；真实r1/r2不等于真实衰减/线圈/热/接收校准；无PGU及MRI/高分辨化学位移输出'],source_ids=['manufacturer_primary','simulation_assumption'])
 mech=dict(component_ids=['permanent_magnet','rf_coil','receiver','pulse_programmer','probe_bore','vt_probe','host_screen'],state_ids=list(slots),meaning_zh='固定20MHz的三等权等时自旋Bloch仿真；参考实测r1/r2；有限时长RF和事件采样',implementation='lumped',equations=['R1=host_R1_baseline + literature_r1*c_metal; R2 likewise or explicit missing-r2 synthetic input. 1mM=1mol/m3. No B0/temperature/concentration interpolation. Reject 2R2<R1 to preserve Bloch-ball positivity.','dM/dt=omega cross M - diag(R2,R2,R1)*M + (0,0,R1); rotating-frame offsets[-df,0,+df]. Exact affine matrix exponential during rectangular RF; analytical free precession/recovery between pulses.','omega1=host_nutation_gain*sqrt(P); finite pulse timing20ns grid. CPMG90x and180y; IR180x-delay-90x. No reset to equilibrium per point/average; recovery takes scheduled time. Initial tube load at equilibrium is an explicit assumption.','signal=-gain*mean(My)+host Gaussian noise;4096 bipolar ADC codes; overflow fails without false clipping. Missing r2 remains synthetic, not relabeled measured.','Fit observed IR A+Bexp(-R*t), CPMG Aexp(-R*t); variable projection log-rate bounded minimization. T=1/R. Incomplete/flat curves rejected. No hidden template identity/rate accessed.','C*dT/dt=UA*(target-T) with bath energy=C*deltaT; no active target without VT. RF ledger sumsP*dt separately, not equal to sample deposited heat.','step sole clock; pure query/read/fit. Host prefilled tube volume ledger conserved; RF exhaustion event cancels exactly.'],parameters={},source_ids=['manufacturer_primary','simulation_assumption'],limitations=['55 source-scoped materials, full cross-paper chemical structure deduplication not_run; original decay traces not supplied.','VT one-pole dynamics, RF coil calibration, ADC transfer range and noise are assumptions. No diffusion gradient hardware, chemical shift spectra or MRI images.','No real sample transfer/robot contact, physical tube collision/attachment, real hardware driver or OEM software menus.','Three static offsets not spatial B0/B1 field map, no multi-exponential distributions/exchange/aggregation dynamics; incomplete-recycle fits may be biased.','Actual mass per component, dimensions per enclosure, inertia and friction unknown.'])
 spec=importlib.util.spec_from_file_location('_mq20_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
 ann.annotate_export(SLUG,caps,slots,{'td_nmr':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'solution.register':(SOLUTION,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']
 mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'td_nmr':mech})


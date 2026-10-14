"""HELIOS pump-on/off pairs, finite optical delay motion, explicit synthetic response and caller-only fits."""
from __future__ import annotations
import copy,hashlib,importlib.util,json,math
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares,nnls
from scipy.special import log_ndtr
from scipy.ndimage import gaussian_filter1d
from functools import lru_cache
ROOT=Path(__file__).resolve().parents[2];SLUG='ultrafast_helios';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-076';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/ultrafast_helios_ta_library/templates.json'
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
def enum(x):return dict(type='string',enum=x)
def vector(s,lo,hi):return dict(arr(s),minItems=lo,maxItems=hi)
PLANT=obj({'laser_wavelength_m':number('m',250e-9,800e-9),'laser_average_power_W':number('W',1e-9,.01),'laser_repetition_hz':number('1/s',100,10000),'pump_fwhm_s':number('s',10e-15,1e-12),'probe_fwhm_s':number('s',10e-15,1e-12),'timing_jitter_fwhm_s':number('s',0,1e-12),'pump_radius_m':number('m',10e-6,.001),'delay_zero_offset_s':number('s',0,8e-9),'delay_speed_s_s':number('1',10.0001e-9,100e-9),'delay_acceleration_s_s2':number('1/s',260e-9,1000e-9),'xy_installed':BOOL,'reference_installed':BOOL,'manual_800nm_filter_ready':BOOL,'probe_photoelectrons':number('1',100,1e8),'pulse_relative_noise':number('1',0,.2),'adc_max_photoelectrons':integer(100,1000000000),'seed':integer(0,2147483647),'accept_surrogate_model':BOOL})
SAMPLE=obj({'sample_id':TEXT,'template_id':TEXT,'solvent':TEXT,'concentration_mol_m3':number('mol/m^3',1e-6,1),'path_length_m':number('m',.0001,.01),'volume_m3':number('m^3',1e-9,4e-6),'lifetimes_s':vector(number('s',10e-15,1e-6),1,2),'kinetic_weights':vector(number('1',0,1),1,2),'esa_center_m':number('m',280e-9,1000e-9),'esa_fwhm_m':number('m',1e-9,300e-9),'esa_relative_strength':number('1',0,3),'accept_assumed_kinetics':BOOL})
METHOD=obj({'pump_wavelength_m':number('m',250e-9,800e-9),'pump_average_power_W':number('W',1e-9,.01),'probe_segment':enum(['Ti_420_820']),'wavelength_start_m':number('m',420e-9,820e-9),'wavelength_stop_m':number('m',420e-9,820e-9),'wavelength_step_m':number('m',.1e-9,10e-9),'delay_sequence_s':vector(number('s',-8e-9,8e-9),1,256),'averages':integer(1,1000),'scans':integer(1,8),'reference_enabled':BOOL,'sample_pattern_xy_m':vector(arr(number('m',-.006,.006),2),1,256),'sample_speed_m_s':number('m/s',1e-5,.002)})
PLANT_EXAMPLE=dict(laser_wavelength_m=530e-9,laser_average_power_W=50e-6,laser_repetition_hz=1000.,pump_fwhm_s=35e-15,probe_fwhm_s=50e-15,timing_jitter_fwhm_s=14e-15,pump_radius_m=200e-6,delay_zero_offset_s=.2e-9,delay_speed_s_s=12e-9,delay_acceleration_s_s2=270e-9,xy_installed=True,reference_installed=True,manual_800nm_filter_ready=False,probe_photoelectrons=1e7,pulse_relative_noise=.03,adc_max_photoelectrons=100000000,seed=76,accept_surrogate_model=True)
SAMPLE_EXAMPLE=dict(sample_id='rhodamine_b_demo',template_id='pcad_009',solvent='ethanol',concentration_mol_m3=.001,path_length_m=.002,volume_m3=1e-6,lifetimes_s=[150e-12],kinetic_weights=[1.],esa_center_m=600e-9,esa_fwhm_m=60e-9,esa_relative_strength=.5,accept_assumed_kinetics=True)
METHOD_EXAMPLE=dict(pump_wavelength_m=530e-9,pump_average_power_W=50e-6,probe_segment='Ti_420_820',wavelength_start_m=480e-9,wavelength_stop_m=660e-9,wavelength_step_m=2e-9,delay_sequence_s=[-2e-12,0.,20e-12,100e-12,500e-12,1e-9],averages=20,scans=1,reference_enabled=True,sample_pattern_xy_m=[[0.,0.],[.001,-.001]],sample_speed_m_s=.002)
POINT=obj({'scan_index':integer(0,7),'delay_index':integer(0,255),'requested_delay_s':number('s'),'actual_delay_s':number('s'),'pairs':integer(0,1000),'valid_pairs':arr(integer(0,1000)),'delta_absorbance':arr(nullable(number('1'))),'complete':BOOL,'last_sample_time_s':nullable(number('s',0))})
RESULT=obj({'template_source':TEXT,'phase':PHASE,'complete':BOOL,'wavelength_m':arr(number('m')),'points':arr(POINT),'active_point':nullable(POINT),'settings_json':TEXT,'evidence':TEXT,'limitations':arr(TEXT)})
MEASURE=obj({'template_source':TEXT,'phase':PHASE,'stage':TEXT,'elapsed_s':number('s',0),'pairs':integer(0,100000),'completed_points':integer(0,2048),'delta_absorbance_center':nullable(number('1')),'warning':TEXT})
LEDGER=obj({'sample_id':nullable(TEXT),'template_id':nullable(TEXT),'registered_volume_m3':number('m^3',0),'current_volume_m3':number('m^3',0),'removed_volume_m3':number('m^3',0),'volume_residual_m3':number('m^3'),'pump_pulses':integer(0,1000000000000),'probe_pulses':integer(0,2000000000000),'completed_pairs':integer(0,1000000000000),'pump_exposure_J':number('J',0),'energy_residual_J':number('J'),'assumed_irf_fwhm_s':nullable(number('s')),'last_excited_fraction':nullable(number('1')),'evidence':TEXT})
RAW=obj({'off_sample':arr(integer(0,1000000000)),'on_sample':arr(integer(0,1000000000)),'off_reference':arr(integer(0,1000000000)),'on_reference':arr(integer(0,1000000000)),'reference_enabled':BOOL,'evidence':TEXT})
FITIN=obj({'delay_s':vector(number('s',-8e-9,8e-9),12,5000),'delta_absorbance':vector(number('1',-10,10),12,5000),'irf_fwhm_s':number('s',1e-15,100e-12),'time_zero_s':number('s',-8e-9,8e-9),'initial_lifetimes_s':vector(number('s',5e-15,1e-6),1,2)})
FITOUT=obj({'lifetimes_s':arr(number('s',0)),'amplitudes':arr(number('1')),'baseline':number('1'),'relative_rmse':number('1',0),'condition_number':number('1',0),'evidence':TEXT})
TEMPLATE=obj({'substance_id':TEXT,'name':TEXT,'source':TEXT,'evidence':TEXT,'template_json':TEXT})
CAPS={}
def cap(k,inp,out,c,z,kind='device_command',vis='agent',ex=None):CAPS[k]=[inp,out,c,z,kind,vis,{} if ex is None else ex,[]]
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body','pump_port','probe_seed_port','delay_carriage','sample_spectrometer'],'外部激光/检测器/选件一次声明，显式接受非原厂噪声IRF与布局假设','configuration','evaluator',PLANT_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_holder','cuvette'],'宿主装入完整样品；真实基态ε加显式合成ESA/动力学；拒绝跨溶剂','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.cover',obj({'opened':BOOL}),obj({'opened':BOOL}),['sample_cover'],'外部盖提升150mm；开盖取消并保留已取得数据','device_command','evaluator',{'opened':False})
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','timing_board'],'逻辑供电；断电取消且保留暴露',ex={'enabled':True})
cap('laser.arm',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['pump_port','sync_port'],'宿主确认外部激光授权；未模拟安全认证','device_command','evaluator',{'enabled':True})
cap('method.configure',METHOD,obj({'configured':BOOL}),['delay_carriage','sample_x','sample_y','sample_spectrometer'],'泵浦功率/波长必须与外部已安装源匹配；探测段、延迟序列、平均和移动路径','configuration','agent',METHOD_EXAMPLE)
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['delay_carriage','sample_x','sample_y','chopper','sample_spectrometer','reference_spectrometer'],'逐延迟有限加减速移动、开关双脉冲探测、逐对ΔA平均，扫描形成数据立方')
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['timing_board','pump_port'],'取消保存完整点和部分点及实际曝光，不回滚')
cap('process.measure',obj({}),MEASURE,['host_screen'],'最近采集统计；光学延迟不是采集耗时','query')
cap('result.read',obj({}),RESULT,['host_screen'],'公开ΔA立方的扫描/延迟/波长轴，不暴露隐藏物质身份','query')
cap('raw.read',obj({}),RAW,['sample_spectrometer','reference_spectrometer'],'最近完整模拟开关双谱；非原厂实测光子','query')
cap('kinetics.fit',FITIN,FITOUT,['host_screen'],'只用调用者时间/观测ΔA/IRF做有符号单或双指数卷积拟合；无隐藏τ','analysis')
cap('template.read',obj({'substance_id':TEXT}),TEMPLATE,['host_screen'],'公开63种真实基态谱及1种真实TA矩阵，来源与缺失条件保持显式','query','agent',{'substance_id':'ru_dppz_acn'})
cap('product.read',obj({}),LEDGER,['sample_holder','timing_board'],'私有样品体积及脉冲/泵浦暴露账本，无虚构化学产物','query','evaluator')
cap('fast_scan.start',obj({}),obj({}),['delay_carriage'],'连续FastScan及位置触发采集未实现，明确unsupported')
cap('alignment.start',obj({}),obj({}),['delay_carriage'],'实际光束对准/漂移反馈未实现，不能自动等待后报告成功')
cap('stirrer.set',obj({'speed_rad_s':number('rad/s',0,100)}),obj({}),['stirrer'],'磁力搅拌流体动力学未实现',ex={'speed_rad_s':10.})
UNSUPPORTED={'fast_scan.start','alignment.start','stirrer.set'}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='host_screen',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','已明确外部安装及假设','setpoint',c='body')
slot('sample.loaded',BOOL,'1','样品外部装入','scene',c='cuvette')
slot('power.enabled',BOOL,'1','逻辑供电','setpoint',c='timing_board',w=('power.set','enabled'))
slot('laser.armed',BOOL,'1','宿主激光授权','setpoint',c='pump_port')
slot('laser.emitting',BOOL,'1','仅采集阶段发出开关脉冲','derived',c='pump_port')
slot('cover.opened',BOOL,'1','外部盖状态','scene',c='sample_cover')
slot('cover.lift_m',number('m',0,.15),'m','工程盖提升位移','scene',c='sample_cover')
slot('delay.optical_s',number('s',0,8e-9),'s','从行程零端开始的光学延迟','scene',c='delay_carriage')
slot('delay.relative_s',number('s',-8e-9,8e-9),'s','相对时间零点泵浦探测延迟，不是世界时间','derived',c='delay_carriage')
slot('delay.position_m',number('m',0,.30),'m','0.30m↔8ns工程可视化映射，非厂家多程行程','scene',c='delay_carriage')
slot('sample.x_m',number('m',-.006,.006),'m','选件X位移，嵌套Y台','scene',c='sample_x')
slot('sample.y_m',number('m',-.006,.006),'m','选件Y位移','scene',c='sample_y')
slot('method.pump_power_w',nullable(number('W')),'W','未斩波外部脉冲列平均功率','setpoint',c='pump_port')
slot('method.pump_wavelength_m',nullable(number('m')),'m','外部泵浦波长','setpoint',c='pump_port')
slot('method.spectral_fwhm_m',number('m'),'m','200µm狭缝4nm条件谱宽，非采样步长','setpoint',c='slit')
slot('scan.phase',PHASE,'1','异步采集生命周期')
slot('scan.stage',enum(['idle','moving','counting']),'1','移动或脉冲采集阶段')
slot('scan.elapsed_s',number('s',0),'s','操作世界耗时')
slot('scan.pairs',integer(0,100000),'1','当前扫描已完成的开关对数')
slot('scan.completed_points',integer(0,2048),'1','完成的扫描延迟点数')
slot('scan.delta_absorbance_center',nullable(number('1')),'1','中心波长最近观测差分吸收')
slot('scan.last_sample_time_s',nullable(number('s',0)),'s','最近完整开关对世界时刻')
slot('scan.pump_exposure_j',number('J',0),'J','依据外部设定功率与实际泵浦脉冲计数的暴露估计')
slot('safety.warning',TEXT,'1','公开警告，不包含样品身份')
slot('truth.ledger',LEDGER,'1','仅评估器样品与暴露守恒','process_truth','evaluator','sample_holder')
@lru_cache(maxsize=1)
def _templates():return {t['substance_id']:t for t in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']}
def gaussian_decay(t,tau,fwhm,t0=0.):
 sig=fwhm/math.sqrt(8*math.log(2));x=np.asarray(t)-t0
 return np.exp(np.clip(.5*(sig/tau)**2-x/tau+log_ndtr(x/sig-sig/tau),-745,0))
def delay_profile(distance,v,a):
 ramp=min(v/a,math.sqrt(abs(distance)/a));flat=max(0.,abs(distance)/(a*ramp)-ramp) if ramp else 0.
 return ramp,flat,2*ramp+flat
def moved(t,distance,v,a):
 ramp,flat,total=delay_profile(distance,v,a);t=max(0.,min(t,total))
 d=.5*a*t*t if t<=ramp else .5*a*ramp*ramp+a*ramp*(t-ramp) if t<=ramp+flat else abs(distance)-.5*a*(total-t)**2
 return math.copysign(min(abs(distance),max(0.,d)),distance)


class UltrafastHeliosRuntime:
 def __init__(self,instance_id='helios_01'):
  self.instance_id=instance_id;self.templates=_templates();self.time_s=0.;self.revision=0;self.plant=None;self.method=None;self.sample=None;self._run_method=None
  self.powered=False;self.armed=False;self.opened=True;self.optical_delay=0.;self.xy=np.zeros(2);self.phase='idle';self.stage='idle';self.elapsed_s=0.;self.warning=''
  self.operation_id='';self._op_count=0;self.points=[];self.active=None;self.wavelength=np.array([]);self._queue=[];self._next=None;self._rng=np.random.default_rng(0)
  self._registered_v=0.;self._removed_v=0.;self._pump_pulses=0;self._probe_pulses=0;self._pairs=0;self._energy=0.;self._run_pairs=0;self._last_sample_time=None;self._excited=None
  self._off=None;self._off_time=None;self._raw=dict(off_sample=[],on_sample=[],off_reference=[],on_reference=[],reference_enabled=False,evidence='no complete observed pair')
  self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._operation_records={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return UltrafastHeliosSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def debug_snapshot(self):
  return copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in ['templates','_rng']})|{'_rng_state':copy.deepcopy(self._rng.bit_generator.state)}
 @property
 def irf_fwhm(self):
  return None if self.plant is None else math.sqrt(sum(self.plant[k]**2 for k in ['pump_fwhm_s','probe_fwhm_s','timing_jitter_fwhm_s']))
 def _ledger(self):
  v=self.sample['volume_m3'] if self.sample else 0.
  ep=self.plant['laser_average_power_W']/self.plant['laser_repetition_hz'] if self.plant else 0.
  return dict(sample_id=self.sample['sample_id'] if self.sample else None,template_id=self.sample['template_id'] if self.sample else None,registered_volume_m3=self._registered_v,current_volume_m3=v,removed_volume_m3=self._removed_v,volume_residual_m3=self._registered_v-self._removed_v-v,pump_pulses=self._pump_pulses,probe_pulses=self._probe_pulses,completed_pairs=self._pairs,pump_exposure_J=self._energy,energy_residual_J=self._energy-self._pump_pulses*ep,assumed_irf_fwhm_s=self.irf_fwhm,last_excited_fraction=self._excited,evidence='synthetic optical excitation; actual sample identity private; no chemical products or heating model')
 def _point_result(self,p):
  if p is None:return None
  out={k:copy.deepcopy(p[k]) for k in ['scan_index','delay_index','requested_delay_s','actual_delay_s','pairs','complete','last_sample_time_s']}
  out['valid_pairs']=p['valid_pairs'].tolist();out['delta_absorbance']=[float(v/n) if n else None for v,n in zip(p['sums'],p['valid_pairs'])]
  return out
 def _measure(self):
  p=self.active or (self.points[-1] if self.points else None);s=self._point_result(p);d=s['delta_absorbance'][len(self.wavelength)//2] if s else None
  return dict(template_source='https://omlc.org/spectra/PhotochemCAD/',phase=self.phase,stage=self.stage,elapsed_s=self.elapsed_s,pairs=self._run_pairs,completed_points=len(self.points),delta_absorbance_center=d,warning=self.warning)
 def read(self,visibility='agent'):
  if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
  m=self._measure();p=self.plant
  v={'plant.configured':p is not None,'sample.loaded':self.sample is not None,'power.enabled':self.powered,'laser.armed':self.armed,'laser.emitting':self.phase=='running' and self.stage=='counting','cover.opened':self.opened,'cover.lift_m':.15 if self.opened else 0.,'delay.optical_s':self.optical_delay,'delay.relative_s':self.optical_delay-(p['delay_zero_offset_s'] if p else 0.),'delay.position_m':self.optical_delay/8e-9*.30,'sample.x_m':float(self.xy[0]),'sample.y_m':float(self.xy[1]),'method.pump_power_w':self.method['pump_average_power_W'] if self.method else None,'method.pump_wavelength_m':self.method['pump_wavelength_m'] if self.method else None,'method.spectral_fwhm_m':4e-9,'scan.phase':self.phase,'scan.stage':self.stage,'scan.elapsed_s':self.elapsed_s,'scan.pairs':self._run_pairs,'scan.completed_points':len(self.points),'scan.delta_absorbance_center':m['delta_absorbance_center'],'scan.last_sample_time_s':self._last_sample_time,'scan.pump_exposure_j':self._energy,'safety.warning':self.warning,'truth.ledger':self._ledger()}
  if visibility=='agent':v.pop('truth.ledger')
  return copy.deepcopy(v)
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[k][0],a)
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  snap=self.debug_snapshot()
  try:
   result=self._invoke(k,copy.deepcopy(a));validate(CAPS[k][1],result)
   if CAPS[k][4] not in ['query','analysis']:self._archive_operation();self.revision+=1
   return copy.deepcopy(result)
  except Exception:
   ts=self.templates;rs=snap.pop('_rng_state');self.__dict__.clear();self.__dict__.update(snap);self.templates=ts;self._rng=np.random.default_rng();self._rng.bit_generator.state=rs
   raise
 def _invoke(self,k,a):
  if k=='plant.configure':
   if self.plant is not None or self.phase!='idle':raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_surrogate_model']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   self.plant=a;self._rng=np.random.default_rng(a['seed']);return {'configured':True}
  if k=='sample.load':
   if self.phase=='running':raise ActionRejected('BUSY')
   if not self.opened:raise ActionRejected('EXTERNAL_LOAD_REQUIRED')
   if not self.plant:raise ActionRejected('PLANT_REQUIRED')
   if not a['accept_assumed_kinetics']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   t=self.templates.get(a['template_id'])
   if not t or t['kind']!='ground_state':raise ActionRejected('GROUND_STATE_TEMPLATE_REQUIRED')
   if a['solvent']!=t['conditions']['solvent']:raise ActionRejected('SOURCE_CONDITIONS_MISMATCH')
   if len(a['lifetimes_s'])!=len(a['kinetic_weights']) or not math.isclose(sum(a['kinetic_weights']),1.,abs_tol=1e-12):raise ActionRejected('SAMPLE_COMPOSITION')
   if max(a['lifetimes_s'])*10>=1/self.plant['laser_repetition_hz']:raise ActionRejected('INTERPULSE_ACCUMULATION_UNSUPPORTED')
   self._removed_v+=self.sample['volume_m3'] if self.sample else 0.;self._registered_v+=a['volume_m3'];self.sample=a
   return {'loaded':True}
  if k=='sample.cover':
   self.opened=a['opened']
   if self.opened:self._stop('cancelled','cover_open')
   return {'opened':self.opened}
  if k=='power.set':
   self.powered=a['enabled']
   if not self.powered:self._stop('cancelled','power_off')
   return {'enabled':self.powered}
  if k=='laser.arm':
   if not self.plant:raise ActionRejected('PLANT_REQUIRED')
   self.armed=a['enabled']
   if not self.armed:self._stop('cancelled','laser_disarmed')
   return {'enabled':self.armed}
  if k=='method.configure':
   if self.phase=='running':raise ActionRejected('BUSY')
   if not self.plant:raise ActionRejected('PLANT_REQUIRED')
   p=self.plant
   if not math.isclose(a['pump_average_power_W'],p['laser_average_power_W'],rel_tol=1e-12) or not math.isclose(a['pump_wavelength_m'],p['laser_wavelength_m'],rel_tol=1e-12):raise ActionRejected('EXTERNAL_LASER_MISMATCH')
   if a['reference_enabled'] and not p['reference_installed']:raise ActionRejected('OPTION_NOT_INSTALLED')
   if any(abs(v)>0 for xy in a['sample_pattern_xy_m'] for v in xy) and not p['xy_installed']:raise ActionRejected('OPTION_NOT_INSTALLED')
   if not a['wavelength_stop_m']>a['wavelength_start_m']:raise ActionRejected('SPECTRAL_GRID')
   n=1+round((a['wavelength_stop_m']-a['wavelength_start_m'])/a['wavelength_step_m'])
   if not 3<=n<=512 or not math.isclose(a['wavelength_start_m']+(n-1)*a['wavelength_step_m'],a['wavelength_stop_m'],rel_tol=0,abs_tol=1e-15):raise ActionRejected('SPECTRAL_GRID')
   if a['wavelength_stop_m']>=790e-9 and not p['manual_800nm_filter_ready']:raise ActionRejected('MANUAL_FILTER_REQUIRED')
   if any(t+p['delay_zero_offset_s']<0 or t+p['delay_zero_offset_s']>8e-9 for t in a['delay_sequence_s']):raise ActionRejected('DELAY_WINDOW')
   if len(a['delay_sequence_s'])*a['averages']*a['scans']>100000:raise ActionRejected('EVENT_RESOURCE_LIMIT')
   self.method=a;return {'configured':True}
  if k=='acquisition.start':
   if self.phase=='running':raise ActionRejected('BUSY')
   if not self.plant or not self.method or not self.sample:raise ActionRejected('SAMPLE_METHOD_REQUIRED')
   if not self.powered or not self.armed or self.opened:raise ActionRejected('OPTICS_NOT_READY')
   self._archive_operation();self._prepare_response()
   self.points=[];self.active=None;self._run_method=copy.deepcopy(self.method);self._queue=[(sc,i,t) for sc in range(self.method['scans']) for i,t in enumerate(self.method['delay_sequence_s'])]
   self._op_count+=1;self.operation_id=self.instance_id+':'+str(self._op_count);self.phase='running';self.elapsed_s=0.;self._started=self.time_s;self.warning='synthetic_TA_response';self._run_pairs=0;self._last_sample_time=None;self._off=None
   self._raw=dict(off_sample=[],on_sample=[],off_reference=[],on_reference=[],reference_enabled=self.method['reference_enabled'],evidence='no complete observed pair in current run')
   self._begin_point()
   return dict(phase=self.phase,operation_id=self.operation_id)
  if k=='acquisition.stop':self._stop('cancelled','requested');return {'phase':self.phase}
  if k=='process.measure':return self._measure()
  if k=='result.read':return dict(template_source='https://omlc.org/spectra/PhotochemCAD/',phase=self.phase,complete=self.phase=='succeeded',wavelength_m=self.wavelength.tolist(),points=[self._point_result(x) for x in self.points],active_point=self._point_result(self.active),settings_json=json.dumps(self._run_method or {},sort_keys=True),evidence='synthetic_transient_response_using_measured_ground_state_absorption',limitations=LIMITATIONS)
  if k=='raw.read':return self._raw
  if k=='product.read':return self._ledger()
  if k=='template.read':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   t=self.templates[a['substance_id']];return dict(substance_id=t['substance_id'],name=t['name'],source=t['source'],evidence=t['evidence'],template_json=json.dumps(t,ensure_ascii=False))
  if k=='kinetics.fit':return fit_kinetics(a)
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _prepare_response(self):
  p=self.plant;s=self.sample;m=self.method;t=self.templates[s['template_id']];x=np.asarray(t['abs_wavelength_m']);eps=np.maximum(0.,t['epsilon_m2_mol'])
  if min(m['wavelength_start_m'],m['pump_wavelength_m'])<x[0] or max(m['wavelength_stop_m'],m['pump_wavelength_m'])>x[-1]:raise ActionRejected('SOURCE_WAVELENGTH_RANGE')
  self.wavelength=np.linspace(m['wavelength_start_m'],m['wavelength_stop_m'],1+round((m['wavelength_stop_m']-m['wavelength_start_m'])/m['wavelength_step_m']))
  a0=eps*s['concentration_mol_m3']*s['path_length_m'];ep=p['laser_average_power_W']/p['laser_repetition_hz'];photon=6.62607015e-34*299792458/p['laser_wavelength_m']
  cross=math.log(10)*float(np.interp(p['laser_wavelength_m'],x,eps))/6.02214076e23
  self._excited=-math.expm1(-cross*ep/photon/(math.pi*p['pump_radius_m']**2))
  if self._excited>.2:raise ActionRejected('WEAK_EXCITATION_LIMIT')
  if max(a0)>4:raise ActionRejected('ABSORBANCE_RANGE')
  esa=s['esa_relative_strength']*float(max(a0))*np.exp(-4*math.log(2)*((x-s['esa_center_m'])/s['esa_fwhm_m'])**2)
  width=t['conditions'].get('source_absorption_bandwidth_m') or 1e-9;extra=math.sqrt(max(0.,(4e-9)**2-width**2))/math.sqrt(8*math.log(2));grid=float(np.median(np.diff(x)))
  self._a0=np.interp(self.wavelength,x,gaussian_filter1d(a0,extra/grid,mode='nearest'))
  self._spectral=np.interp(self.wavelength,x,gaussian_filter1d(esa-a0,extra/grid,mode='nearest'))*self._excited
 def _begin_point(self):
  sc,i,t=self._queue.pop(0);p=self.plant;m=self._run_method
  self._target_optical=min(math.floor(8e-9/2.8e-15),max(0,round((t+p['delay_zero_offset_s'])/2.8e-15)))*2.8e-15
  self._origin_optical=self.optical_delay;self._origin_xy=self.xy.copy();self._target_xy=np.array(m['sample_pattern_xy_m'][i%len(m['sample_pattern_xy_m'])])
  self._move_start=self.time_s;self._move_duration=max(delay_profile(self._target_optical-self.optical_delay,p['delay_speed_s_s'],p['delay_acceleration_s_s2'])[2],float(np.linalg.norm(self._target_xy-self.xy))/m['sample_speed_m_s'])
  self._next=self.time_s+self._move_duration;self.stage='moving'
  self.active=dict(scan_index=sc,delay_index=i,requested_delay_s=t,actual_delay_s=self._target_optical-p['delay_zero_offset_s'],pairs=0,valid_pairs=np.zeros(len(self.wavelength),dtype=int),sums=np.zeros(len(self.wavelength)),complete=False,first_sample_time_s=None,last_sample_time_s=None)
 def _move(self,t):
  p=self.plant;m=self._run_method;dt=t-self._move_start
  self.optical_delay=self._origin_optical+moved(dt,self._target_optical-self._origin_optical,p['delay_speed_s_s'],p['delay_acceleration_s_s2'])
  dist=float(np.linalg.norm(self._target_xy-self._origin_xy));f=min(1.,dt*m['sample_speed_m_s']/dist) if dist else 1.;self.xy=self._origin_xy+max(0.,f)*(self._target_xy-self._origin_xy)
 def _shot(self,on):
  p=self.plant;f=self._rng.lognormal(-.5*p['pulse_relative_noise']**2,p['pulse_relative_noise']);tau=self.active['actual_delay_s']
  kin=sum(w*float(gaussian_decay(tau,t,self.irf_fwhm)) for w,t in zip(self.sample['kinetic_weights'],self.sample['lifetimes_s']))
  delta=self._spectral*kin if on else np.zeros(len(self.wavelength))
  counts=self._rng.poisson(p['probe_photoelectrons']*f*10**(-self._a0-delta))
  ref=self._rng.poisson(p['probe_photoelectrons']*f,size=len(self.wavelength)) if self._run_method['reference_enabled'] else np.array([],dtype=int)
  self._probe_pulses+=1
  if on:self._pump_pulses+=1;self._energy+=p['laser_average_power_W']/p['laser_repetition_hz']
  return np.minimum(counts,p['adc_max_photoelectrons']),np.minimum(ref,p['adc_max_photoelectrons'])
 def _pair(self,on):
  off=self._off;limit=self.plant['adc_max_photoelectrons'];valid=(off[0]>0)&(on[0]>0)&(off[0]<limit)&(on[0]<limit);d=np.zeros(len(self.wavelength))
  if self._run_method['reference_enabled']:valid&=(off[1]>0)&(on[1]>0)&(off[1]<limit)&(on[1]<limit)
  d[valid]=np.log10(off[0][valid]/on[0][valid])
  if self._run_method['reference_enabled']:d[valid]+=np.log10(on[1][valid]/off[1][valid])
  if self.active['first_sample_time_s'] is None:self.active['first_sample_time_s']=self._off_time
  self.active['sums'][valid]+=d[valid];self.active['valid_pairs'][valid]+=1;self.active['pairs']+=1;self.active['last_sample_time_s']=self.time_s;self._last_sample_time=self.time_s;self._run_pairs+=1;self._pairs+=1
  self._raw=dict(off_sample=off[0].tolist(),on_sample=on[0].tolist(),off_reference=off[1].tolist(),on_reference=on[1].tolist(),reference_enabled=self._run_method['reference_enabled'],evidence='synthetic shot noise and host common-mode fluctuation; raw clipped ADC-equivalent photoelectrons')
  if not np.all(valid):self.warning='invalid_or_saturated_pixels_retained_as_null'
  self._off=None
 def _operation_snapshot(self):
  ids=['scan.delta_absorbance_center','scan.last_sample_time_s']
  return dict(operation_id=self.operation_id,status=self.phase,result=self._measure(),observations=self.session().read(instance_id=self.instance_id,state_ids=ids)['observations'])
 def _archive_operation(self):
  if self.operation_id and self.phase in ('succeeded','cancelled','failed') and self.operation_id not in self._operation_records:
   self._operation_records[self.operation_id]=copy.deepcopy(self._operation_snapshot())
 def _stop(self,phase,reason):
  if self.phase!='running':return
  self.phase=phase;self.stage='idle';self._next=None
  if reason or phase!='succeeded':self.warning=reason
 def step(self,dt):return self.step_clock(dt)
 def step_clock(self,dt):
  validate(number('s',0,100000),dt)
  if not dt:return self.read()
  end=self.time_s+dt
  while self.phase=='running' and self._next<=end:
   t=self._next
   if self.stage=='moving':
    self._move(t);self.time_s=t;self.stage='counting';self._next=t+1/self.plant['laser_repetition_hz'];self._off=None
   else:
    self.time_s=t
    if self._off is None:self._off=self._shot(False);self._off_time=t;self._next=t+1/self.plant['laser_repetition_hz']
    else:
     self._pair(self._shot(True))
     if self.active['pairs']>=self._run_method['averages']:
      self.active['complete']=True;self.points.append(self.active);self.active=None
      if self._queue:self._begin_point()
      else:self.elapsed_s=t-self._started;self._stop('succeeded','')
     else:self._next=t+1/self.plant['laser_repetition_hz']
  if self.phase=='running':
   if self.stage=='moving':self._move(end)
   self.elapsed_s=end-self._started
  self.time_s=end
  if dt:self.revision+=1
  self._archive_operation();return self.read()
def fit_kinetics(a):
 t=np.asarray(a['delay_s']);y=np.asarray(a['delta_absorbance']);initial=np.sort(a['initial_lifetimes_s'])
 if len(t)!=len(y) or np.any(t[1:]<=t[:-1]) or np.ptp(y)==0 or len(t)<4*len(initial)+4:raise ActionRejected('ANALYSIS_INPUT')
 span=t[-1]-t[0]
 if span<=a['irf_fwhm_s']*3:raise ActionRejected('FIT_UNIDENTIFIABLE')
 # Normalize observations before least squares; an absolute amplitude cutoff
 # discards valid scaled curves and unscaled residuals hide tiny responses.
 scale=float(np.max(np.abs(y)));ys=y/scale
 def solve(z):
  taus=np.exp(z);b=np.column_stack([gaussian_decay(t,tau,a['irf_fwhm_s'],a['time_zero_s']) for tau in taus]+[np.ones(len(t))]);coef=np.linalg.lstsq(b,ys,rcond=None)[0];return b,coef
 def resid(z):
  b,c=solve(z);return b@c-ys
 low=math.log(5e-15);high=math.log(min(1e-6,10*span));start=np.clip(np.log(initial),low+1e-8,high-1e-8)
 try:
  fit=least_squares(resid,start,bounds=(low,high),max_nfev=500,xtol=1e-12,ftol=1e-12,gtol=1e-12);taus=np.exp(fit.x);b,c=solve(fit.x);cond=float(np.linalg.cond(b))
  # A fitted exponential whose amplitude is at numerical roundoff cannot
  # identify a lifetime, even when the unweighted basis matrix is conditioned.
  roundoff=np.finfo(float).eps*max(b.shape)*max(1.,cond)*max(1.,float(np.max(np.abs(c))))
  if not fit.success or not math.isfinite(cond) or cond>1e8 or not np.isfinite(fit.jac).all() or np.linalg.matrix_rank(fit.jac)<len(taus) or np.any(np.abs(c[:-1])<=roundoff) or (len(taus)==2 and max(taus)/min(taus)<1.15):raise ValueError()
  order=np.argsort(taus);amplitudes=c[:-1][order]*scale;baseline=float(c[-1]*scale);rmse=float(np.sqrt(np.mean((b@c-ys)**2)))
  if not np.isfinite(amplitudes).all() or not math.isfinite(baseline) or not math.isfinite(rmse):raise ValueError()
 except (ValueError,RuntimeError,ArithmeticError,np.linalg.LinAlgError):raise ActionRejected('FIT_UNIDENTIFIABLE') from None
 return dict(lifetimes_s=taus[order].tolist(),amplitudes=amplitudes.tolist(),baseline=baseline,relative_rmse=rmse,condition_number=cond,evidence='derived only from caller observations and caller-specified Gaussian IRF, with normalized signal and numerical sensitivity guards; no true template lifetime read; not full global mechanism/uncertainty')

class UltrafastHeliosSession:
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
        m=export_ultrafast_helios_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.1',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        last=h._last_sample_time;p=h.active if h.active is not None else h.points[-1] if h.points else None
        def timing(key):
            if key=='scan.delta_absorbance_center' and p is not None and p['last_sample_time_s'] is not None:return p['last_sample_time_s'],[p['first_sample_time_s'],p['last_sample_time_s']]
            if key=='scan.last_sample_time_s' and last is not None:return last,None
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
        key=(self._principal,h.instance_id,request_id);digest=json.dumps(['acquisition.cancel',operation_id])
        if key in h._requests:
            old,response=h._requests[key]
            if old!=digest:raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(response)
        result=h.invoke('acquisition.stop') if snapshot['status']=='running' else {'phase':snapshot['status']}
        response=dict(protocol_version=PROTOCOL_VERSION,instance_id=h.instance_id,request_id=request_id,operation_id=operation_id,status='succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None)
        h._requests[key]=(digest,copy.deepcopy(response));return response





LIMITATIONS=[
'Body dimensions and mass are unknown. Geometry is an independent task-layout proxy, never scaled from photographs;0.1kg URDF and10kg rigid mass are numerical only.',
'8ns is an optical delay span;2.8fs is minimum step;14fs is bidirectional repeatability; neither is system IRF.0.30m travel is explicit scene mapping, NOT inferred multipass stroke.',
'Implemented probe segment Ti420–820nm only;200um slit4nm conditional resolution vs2nm intrinsic. Near800nm requires host manual-filter acknowledgment; other UV/NIR/SWIR/EOS options absent.',
'External laser wavelength/power/repetition/pulse widths/beam radius must be explicit. Power is unchopped pulse-train mean, so alternating on pulses deliver half the continuous train energy during acquisition.',
'Gaussian IRF width is quadrature of host pump/probe/jitter; actual spectral chirp, coherent artifacts, alignment drift and optical field propagation unmodeled.',
'63 real ground-state absorption spectra do not provide real excited-state dynamics. GSB and Gaussian ESA with one/two exponentials are explicitly synthetic, not fluorescence lifetimes relabeled as TA.',
'One real Ru-dppz/ACN matrix is queryable with original source noise/chirp and unknown conditions; not falsely exposed as50 materials or measured on this modeled HELIOS.49 additional real TA responses remain missing.',
'Uniform weak-excitation cross-section/fluence model, fraction<=0.2 guard, no pump attenuation integration, ESA saturation, stimulated emission, photochemistry, bleaching, thermal damage or interpulse accumulation.',
'Finite delay trapezoidal/triangular motion and stop-and-go sample XY pattern, max2mm/s in12mm-square range; continuous FastScan and real auto-alignment unsupported.',
'4nm effective smoothing adds variance to source documented ground-state bandwidth; ESA smoothing and Gaussian shape synthetic. Pixel step and point count are resource settings, not factory detector resolution.',
'Poisson equivalent photoelectrons and shared lognormal pulse drift are host assumptions; no true detector gain/read noise/dark/current/calibration/spike rejection. Invalid pixels remain null, never a fabricated zero.',
'Each paired log ratio is computed before averaging; optional reference suppresses common pulse fluctuations. Averaging intensities first is a separate OEM option not implemented.',
'Sample intact volume conserved and pump pulses/incident energy counted; exposure ledger is not thermal/chemical energy closure. Pump interactions occur only on pump-on events.',
'Caller-only mono/bi Gaussian reconvolution uses normalized signed least squares; constant observations, roundoff-level component amplitudes and rank-deficient lifetime sensitivity reject. These finite numerical guards do not establish global mechanism, fit uncertainty, chirp correction, measured IRF or full identifiability.',
'Host loading, manual removable lid and laser authorization are trusted external actions, not robot contact or certified real laser interlocks. Chopper geometry has no validated高速遮挡.',
'Simulation-only six-value telemetry is not OEM panel software. No OEM driver, hardware/HIL, robot dynamics, real collision/contact, native async adapter or reverse joint coupling.',
'Averaged delta-absorbance observations carry the current point window from first off-probe through latest completed on-probe; unavailable new points never inherit the previous point timestamp. Owned terminal poll summaries/windows persist through later operations, without full historical cubes; historical cancellation does not stop new acquisition. These in-memory permissions/idempotency are not OS security or restart persistence.'
]
CAPS['kinetics.fit'][6]=dict(delay_s=np.linspace(-1e-12,1e-9,80).tolist(),delta_absorbance=(.003*gaussian_decay(np.linspace(-1e-12,1e-9,80),150e-12,80e-15)+.0001).tolist(),irf_fwhm_s=80e-15,time_zero_s=0.,initial_lifetimes_s=[100e-12])
def export_ultrafast_helios_contract_metadata():
 caps={};slots={}
 measured=['scan.pairs','scan.completed_points','scan.delta_absorbance_center','scan.last_sample_time_s']
 stop=['scan.phase','scan.stage','laser.emitting','safety.warning']
 movement=['delay.optical_s','delay.relative_s','delay.position_m','sample.x_m','sample.y_m']
 immediate={
  'plant.configure':['plant.configured','delay.relative_s','truth.ledger'],
  'sample.load':['sample.loaded','truth.ledger'],
  'sample.cover':['cover.opened','cover.lift_m',*stop],'power.set':['power.enabled',*stop],'laser.arm':['laser.armed',*stop],
  'method.configure':['method.pump_power_w','method.pump_wavelength_m'],
  'acquisition.start':['scan.phase','scan.stage','scan.elapsed_s',*measured,'safety.warning','truth.ledger'],
  'acquisition.stop':stop,
 }
 on_step={'acquisition.start':[*movement,'laser.emitting','scan.phase','scan.stage','scan.elapsed_s',*measured,'scan.pump_exposure_j','truth.ledger','safety.warning']}
 on_completion={'acquisition.start':['scan.phase','scan.stage','laser.emitting']}
 observed={
  'plant.configure':['plant.configured'],'sample.load':['sample.loaded'],'sample.cover':['cover.opened'],
  'power.set':['power.enabled'],'laser.arm':['laser.armed'],'acquisition.start':['scan.phase'],'acquisition.stop':['scan.phase'],
  'process.measure':['scan.phase','scan.stage','scan.elapsed_s','scan.pairs','scan.completed_points','scan.delta_absorbance_center','safety.warning'],
  'result.read':['scan.phase',*measured],'product.read':['truth.ledger'],
 }
 def effects_for(key):
  descriptions={'immediate':'经检查的配置、外部事件、扫描初始化或取消；不移动延迟台、样品或补算曝光','on_step':'后续宿主step有限移动并触发开关脉冲；有效对求平均，实际泵浦事件增加曝光','on_completion':'全部扫描/延迟点的指定完整开关对完成后设置终态并停止发光'}
  return [dict(state_id=state,timing=timing,description_zh=descriptions[timing]) for timing,mapping in [('immediate',immediate),('on_step',on_step),('on_completion',on_completion)] for state in mapping.get(key,[])]
 for k,(s,u,o,v,c,w) in SLOTS.items():
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'UltrafastHeliosSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='interval_mean' if k=='scan.delta_absorbance_center' else 'instant',time_domain='simulation',window_description='当前点从首次off探测脉冲到最近完整on探测脉冲的采集窗口；仅有效开关对的log比值算术平均，非连续时间平均' if k=='scan.delta_absorbance_center' else None),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','INITIALIZATION_ONLY','EVIDENCE_ACK_REQUIRED','BUSY','PLANT_REQUIRED','EXTERNAL_LOAD_REQUIRED','GROUND_STATE_TEMPLATE_REQUIRED','SOURCE_CONDITIONS_MISMATCH','SAMPLE_COMPOSITION','INTERPULSE_ACCUMULATION_UNSUPPORTED','EXTERNAL_LASER_MISMATCH','OPTION_NOT_INSTALLED','SPECTRAL_GRID','MANUAL_FILTER_REQUIRED','DELAY_WINDOW','EVENT_RESOURCE_LIMIT','SAMPLE_METHOD_REQUIRED','OPTICS_NOT_READY','SOURCE_WAVELENGTH_RANGE','WEAK_EXCITATION_LIMIT','ABSORBANCE_RANGE','ANALYSIS_INPUT','FIT_UNIDENTIFIABLE']
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  asynchronous=k=='acquisition.start';query=kind in ['query','analysis'];unsupported=k in UNSUPPORTED
  caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.ultrafast_helios.UltrafastHeliosSession.invoke',invocation=f'session.invoke(instance_id="helios_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),
  preconditions=[] if query or unsupported else [dict(description_zh='显式外部安装/样品来源条件/激光授权/闭盖/供电/延迟窗；不满足原子拒绝',state_ids=['power.enabled','laser.armed','cover.opened','scan.phase'],enforced_by='runtime',check='Runtime._invoke and trusted-principal Session')],
  state_effects=[] if query or unsupported else effects_for(k),
  observes=observed.get(k,[]),model_effects=dict(mode='none',description_zh='明确未实现' if unsupported else '场景层另验',binding_ids=[]),
  execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',completion_criterion='有限移动后按扫描/延迟逐点获取指定开关双脉冲对数' if asynchronous else '返回事务，不推进时钟',completion_state_ids=['scan.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',retry='same_request_id',resources=['host_clock','optical_delay_stage','paired_probe_detector','request_cache'],partial_failure_zh='取消、开盖、断电保留完整/部分点、完整原始对与实际泵浦暴露；未完成off脉冲不伪造ΔA。历史终态公开摘要和平均窗口受原身份保护，后续采集后仍可查询；取消历史终态不停止新采集，历史不含完整旧立方；方法快照和幂等限进程内'),
  errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=LIMITATIONS,source_ids=['manufacturer_primary','manufacturer_formula','simulation_assumption'])
 mech=dict(component_ids=['pump_port','chopper','delay_carriage','sample_x','sample_y','cuvette','sample_spectrometer','reference_spectrometer'],state_ids=list(slots),meaning_zh='有限光学延迟移动、交替泵浦双谱、ΔA数据立方与调用者动力学拟合；非荧光寿命',implementation='lumped',equations=[
 'IRF_FWHM^2=pump_FWHM^2+probe_FWHM^2+timing_jitter_FWHM^2; Gaussian assumption, not2.8fs system response.',
 'Optical target=round((requested_delay+host_zero_offset)/2.8fs)*2.8fs bounded to8ns; scene translation=optical_delay/8ns*0.30m proxy.',
 'Delay motion triangular/trapezoidal with explicit optical-speed/acceleration; XY Euclidean speed<=2mm/s over±6mm, stop-and-go between points.',
 'A0(lambda)=epsilon_m2_mol(lambda)*concentration_mol_m3*path_m; sigma_abs=ln(10)*epsilon_pump/N_A.',
 'Energy_per_pump_pulse=unchopped_laser_power/repetition; excited_fraction=1-exp(-sigma_abs*energy/(hc/lambda*pi*radius^2)).',
 'DeltaA(lambda,t)=fraction*(assumed_ESA(lambda)-A0(lambda))*sum(weights*GaussianIRF_convolved_causal_exp(t,tau)).',
 'Single probe DeltaA_pair=log10(Ioff_sample/Ion_sample); dual reference addslog10(Ion_ref/Ioff_ref). Average valid paired logarithms, not logarithm of summed intensities.',
 'I_on/off sampled with explicit Poisson photoelectrons and common per-shot lognormal intensity drift; zero/saturated pixels excluded with per-pixel valid counts and nullable results.',
 'Registered_volume=current_volume+removed_volume; pump_energy=pump_on_pulses*power/repetition; no sample consumption or full energy-chemistry claim.',
 'Caller-only signed variable-projection least squares fits1/2 Gaussian-convolved causal exponentials plus baseline; no hidden material parameters read.'
 ],parameters={},source_ids=['manufacturer_primary','manufacturer_delay','manufacturer_formula','ground_state_dataset','simulation_assumption'],limitations=LIMITATIONS)
 spec=importlib.util.spec_from_file_location('_helios_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
 ann.annotate_export(SLUG,caps,slots,{'pump_probe_absorption':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'；非原厂校准。'
 mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'pump_probe_absorption':mech})

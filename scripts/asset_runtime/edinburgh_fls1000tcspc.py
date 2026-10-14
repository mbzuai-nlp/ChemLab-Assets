"""TCSPC event counting, caller-only reconvolution and explicitly synthetic response shapes."""
from __future__ import annotations
import copy,hashlib,importlib.util,json,math
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares,nnls
from scipy.special import ndtr
from functools import lru_cache
ROOT=Path(__file__).resolve().parents[2];SLUG='edinburgh_fls1000tcspc';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-075';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/edinburgh_fls1000tcspc_lifetime_library/templates.json'
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
def enum(values):return dict(type='string',enum=values)
SOURCES={'EPL-405':(405e-9,75e-12),'EPL-475':(475e-9,90e-12),'EPL-635':(635e-9,90e-12),'EPLED-250':(250e-9,950e-12)}
FREQUENCIES=[20e6,10e6,5e6,2e6,1e6,500e3,200e3,100e3,50e3,20e3]
PLANT=obj({'source_model':enum(list(SOURCES)),'dark_rate_s_inv':number('1/s',0,1e4),'electronics_fwhm_s':number('s',0,1e-9),'mono_dispersion_fwhm_s':number('s',0,1e-9),'irf_center_s':number('s',1e-9,1e-6),'wavelength_speed_m_s':number('m/s',1e-9,1e-3),'slit_speed_m_s':number('m/s',1e-5,.1),'bandwidth_per_slit':number('1',1e-8,1e-4),'seed':integer(0,2147483647),'accept_surrogate_model':BOOL})
COMPONENT=obj({'template_id':TEXT,'photon_weight':number('1',1e-6,1)})
SAMPLE=obj({'sample_id':TEXT,'kind':enum(['fluorescent','scatterer']),'components':dict(arr(COMPONENT),minItems=0,maxItems=2),'volume_m3':number('m^3',0,4e-6),'mean_photons_per_pulse_at_peak':number('1',1e-8,.04),'emission_sigma_m':number('m',1e-9,100e-9),'accept_assumed_response':BOOL})
METHOD=obj({'excitation_wavelength_m':number('m',250e-9,980e-9),'repetition_hz':dict(number('1/s'),enum=FREQUENCIES),'time_window_s':number('s',2.5e-9,50e-6),'bins':dict(integer(512,8192),enum=[512,1024,2048,4096,8192]),'emission_wavelength_m':number('m',185e-9,900e-9),'slit_width_m':number('m',1e-5,.011),'stop_kind':enum(['time','total_counts','peak_counts']),'target_count':integer(1,1000000),'max_acquisition_s':number('s',1e-5,1000),'fit_model':enum(['mono','bi'])})
PLANT_EXAMPLE=dict(source_model='EPL-405',dark_rate_s_inv=20.,electronics_fwhm_s=20e-12,mono_dispersion_fwhm_s=30e-12,irf_center_s=2e-9,wavelength_speed_m_s=500e-9,slit_speed_m_s=.01,bandwidth_per_slit=4e-6,seed=75,accept_surrogate_model=True)
SAMPLE_EXAMPLE=dict(sample_id='reference_488',kind='fluorescent',components=[dict(template_id='life_row_003',photon_weight=1.)],volume_m3=1e-6,mean_photons_per_pulse_at_peak=.005,emission_sigma_m=20e-9,accept_assumed_response=True)
IRF_EXAMPLE=dict(SAMPLE_EXAMPLE,sample_id='scatter_reference',kind='scatterer',components=[],volume_m3=0.)
METHOD_EXAMPLE=dict(excitation_wavelength_m=405e-9,repetition_hz=20e6,time_window_s=50e-9,bins=1024,emission_wavelength_m=519e-9,slit_width_m=.001,stop_kind='total_counts',target_count=5000,max_acquisition_s=.1,fit_model='mono')
HIST=obj({'emission_wavelength_m':number('m'),'edges_s':dict(arr(number('s',0)),minItems=513,maxItems=8193),'counts':dict(arr(integer(0,2000000)),minItems=512,maxItems=8192),'pulses':integer(0,20000000001),'duration_s':number('s',0),'start_time_s':number('s',0),'last_sample_time_s':nullable(number('s',0)),'complete':BOOL,'stop_reason':TEXT,'response_kind':enum(['decay','IRF']),'period_s':number('s',0)})
RESULT=obj({'phase':PHASE,'complete':BOOL,'histograms':arr(HIST),'active_histogram':nullable(HIST),'settings_json':TEXT,'evidence':TEXT,'limitations':arr(TEXT)})
MEASURE=obj({'phase':PHASE,'stage':TEXT,'elapsed_s':number('s',0),'total_counts':integer(0,2000000),'peak_counts':integer(0,2000000),'mean_microtime_s':nullable(number('s',0)),'completed_wavelengths':integer(0,32),'warning':TEXT})
LEDGER=obj({'sample_id':nullable(TEXT),'template_ids':arr(TEXT),'registered_volume_m3':number('m^3',0),'current_volume_m3':number('m^3',0),'removed_volume_m3':number('m^3',0),'volume_residual_m3':number('m^3'),'emitted_pulses':integer(0,1000000000000000),'detected_first_photons':integer(0,1000000000000000),'histogram_photons':integer(0,1000000000000000),'out_of_window_photons':integer(0,1000000000000000),'count_residual':integer(-1,1),'source_fwhm_s':nullable(number('s')),'detector_fwhm_s':number('s'),'combined_irf_fwhm_s':nullable(number('s')),'evidence':TEXT})
TEMPLATE=obj({'substance_id':TEXT,'name':TEXT,'source':TEXT,'conditions_json':TEXT,'lifetime_s':number('s'),'emission_peak_m':number('m'),'measured_histogram':{'type':'null'},'evidence':TEXT})
FITIN=obj({'histogram':HIST,'irf_histogram':HIST,'initial_lifetimes_s':dict(arr(number('s',5e-12,50e-6)),minItems=1,maxItems=2)})
FITOUT=obj({'lifetimes_s':arr(number('s',0)),'photon_amplitudes_per_pulse':arr(number('1',0)),'background_per_pulse':number('1',0),'relative_rmse':number('1',0),'condition_number':number('1',0),'warning':TEXT,'evidence':TEXT})
DASIN=obj({'histograms':dict(arr(HIST),minItems=2,maxItems=32),'irf_histogram':HIST,'fixed_lifetimes_s':dict(arr(number('s',5e-12,50e-6)),minItems=1,maxItems=2)})
DASOUT=obj({'wavelength_m':arr(number('m')),'fixed_lifetimes_s':arr(number('s')),'photon_amplitudes_per_pulse':arr(arr(number('1',0))),'background_per_pulse':arr(number('1',0)),'relative_rmse':arr(number('1',0)),'evidence':TEXT})
CAPS={}
def cap(k,inp,out,c,z,kind='device_command',vis='agent',ex=None):CAPS[k]=[inp,out,c,z,kind,vis,{} if ex is None else ex,[]]
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body','pulsed_source','pmt','timing_board'],'宿主一次安装离散光源和PMT900，声明IRF形状/展宽/速率假设','configuration','evaluator',PLANT_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_holder','cuvette'],'宿主外部更换完整光学样品/散射参考；溶液体积账本，不冒充机器人','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.cover',obj({'opened':BOOL}),obj({'opened':BOOL}),['sample_cover'],'外部竖直移走盖150mm工程代理；开盖立即终止发光/保留光子','device_command','evaluator',{'opened':False})
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','timing_board'],'供电逻辑；断电取消但不回滚光子',ex={'enabled':True})
cap('source.arm',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['pulsed_source','sync_port'],'宿主确认激光授权/外部联锁；不等于实际激光安全认证','device_command','evaluator',{'enabled':True})
cap('method.configure',METHOD,obj({'configured':BOOL}),['em_grating','slit_left','slit_right','timing_board'],'离散源波长/重复频率、时间窗、计数终止、拟合模型；单色器与狭缝有限运动','configuration','agent',METHOD_EXAMPLE)
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['pulsed_source','em_grating','slit_left','slit_right','pmt','timing_board'],'周期脉冲首光子统计，有限时间采集衰减直方图')
cap('irf.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['pulsed_source','em_grating','slit_left','slit_right','pmt','timing_board'],'显式装载散射参考后实际模拟采集IRF，不直接导出隐藏的高斯真值')
cap('scan.start',obj({'wavelengths_m':dict(arr(number('m',185e-9,900e-9)),minItems=2,maxItems=32)}),obj({'phase':PHASE,'operation_id':TEXT}),['em_grating','slit_left','slit_right','pmt'],'顺序波长扫描：移动、计数、下一波长；取消保留已取得直方图',ex={'wavelengths_m':[510e-9,530e-9]})
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['pulsed_source','timing_board'],'终止保留部分直方图、发射脉冲与样品，不把读取推进成采集')
cap('process.measure',obj({}),MEASURE,['host_screen'],'最近采集统计，平均到达时刻不是寿命','query')
cap('result.read',obj({}),RESULT,['host_screen'],'公开观测光子直方图，无隐藏模板身份/真实寿命','query')
cap('lifetime.fit',FITIN,FITOUT,['host_screen'],'仅用调用者观测IRF/直方图作单或双指数周期重卷积；限制完整周期窗','analysis')
cap('das.fit',DASIN,DASOUT,['host_screen'],'调用者给定寿命的非负重卷积分解；幅度谱不是浓度或完整全局寿命优化','analysis')
cap('template.read',obj({'substance_id':TEXT}),TEMPLATE,['host_screen'],'公开67种真实文献寿命标量，不伪造原始光子','query','agent',{'substance_id':'life_row_003'})
cap('product.read',obj({}),LEDGER,['sample_holder','pmt'],'私有样品/光子/体积账本；无化学消耗与真实光功率推断','query','evaluator')
cap('reverse_mode.set',obj({'enabled':BOOL}),obj({}),['timing_board'],'真实逆向TAC电子死时间/丢脉冲未实现，明确unsupported',ex={'enabled':True})
cap('mcs.start',obj({}),obj({}),['timing_board'],'MCS微秒至秒模式不是TCSPC，未实现')
UNSUPPORTED={'reverse_mode.set','mcs.start'}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='host_screen',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','宿主安装与假设已声明','setpoint',c='body')
slot('sample.loaded',BOOL,'1','完整光学样品已外部装入','scene',c='cuvette')
slot('power.enabled',BOOL,'1','软件供电','setpoint',c='timing_board',w=('power.set','enabled'))
slot('source.armed',BOOL,'1','宿主激光授权','setpoint',c='pulsed_source')
slot('source.emitting',BOOL,'1','仅计数阶段实际脉冲发光','derived',c='pulsed_source')
slot('cover.opened',BOOL,'1','外部盖状态','scene',c='sample_cover')
slot('cover.lift_m',number('m',0,.15),'m','工程盖提升150mm代理','scene',c='sample_cover')
slot('mono.wavelength_m',number('m',185e-9,900e-9),'m','单色器实际有限运动波长','scene',c='em_grating')
slot('mono.angle_rad',number('rad',-.8,.8),'rad','波长到光栅Z角工程线性映射','scene',c='em_grating')
slot('slit.width_m',number('m',0,.011),'m','实际入口两叶总狭缝宽度','scene',c='slit_left')
slot('slit.left_m',number('m',0,.0055),'m','左狭缝半宽位移，关节轴负X','scene',c='slit_left')
slot('slit.right_m',number('m',0,.0055),'m','右狭缝半宽位移，关节轴正X','scene',c='slit_right')
slot('method.excitation_m',nullable(number('m')),'m','安装离散源波长','setpoint',c='pulsed_source')
slot('method.repetition_hz',nullable(number('1/s')),'1/s','源预设重复频率','setpoint',c='timing_board')
slot('method.time_window_s',nullable(number('s')),'s','观测窗不是可测寿命范围','setpoint',c='timing_board')
slot('method.bin_width_s',nullable(number('s')),'s','数字时间bin不等于IRF或寿命分辨率','setpoint',c='timing_board')
slot('scan.phase',PHASE,'1','操作生命周期')
slot('scan.stage',enum(['idle','moving','counting']),'1','实际光路移动/计数阶段')
slot('scan.elapsed_s',number('s',0),'s','本次操作经过时间，含移动')
slot('scan.total_counts',integer(0,2000000),'1','当前或最近直方图观测光子总数')
slot('scan.peak_counts',integer(0,2000000),'1','当前或最近直方图最高bin计数')
slot('scan.mean_microtime_s',nullable(number('s',0)),'s','观测平均微时间，不冒充拟合寿命')
slot('scan.completed_wavelengths',integer(0,32),'1','完成波长数')
slot('scan.last_sample_time_s',nullable(number('s',0)),'s','最近观测光子世界时刻')
slot('safety.warning',TEXT,'1','公开状态/不含样品身份')
slot('truth.ledger',LEDGER,'1','仅评估器样品与光子守恒','process_truth','evaluator','sample_holder')
@lru_cache(maxsize=1)
def _templates():return {t['substance_id']:t for t in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']}
def exp_bins(n,dt,tau):
 x=np.arange(n)*dt
 return np.exp(-x/tau)*(-np.expm1(-dt/tau))/(-np.expm1(-n*dt/tau))
def wrapped_irf(n,dt,fwhm,center):
 edges=np.arange(n+1)*dt;sigma=fwhm/math.sqrt(8*math.log(2));period=n*dt
 out=sum(np.diff(ndtr((edges-center+k*period)/sigma)) for k in range(-3,4))
 return out/out.sum()
def convolve(irf,decay):
 out=np.maximum(0.,np.fft.irfft(np.fft.rfft(irf)*np.fft.rfft(decay),len(irf)))
 return out/out.sum()
def first_photon(mu):
 return np.exp(-np.r_[0.,np.cumsum(mu)[:-1]])*(-np.expm1(-mu))
def hazard(hist):
 y=np.asarray(hist['counts'],float);n=hist['pulses'];edges=np.asarray(hist['edges_s']);period=hist['period_s'];dt=period/len(y)
 if not hist['complete'] or n<=y.sum() or y.sum()<100 or len(edges)!=len(y)+1 or len(y)<512 or period<=0 or dt<=0:raise ActionRejected('ANALYSIS_INPUT')
 # Check the dimensionless grid so absolute tolerances cannot admit a zero,
 # collapsed, or nonuniform time axis at small scales.
 if edges[0]!=0 or np.any(edges[1:]<=edges[:-1]) or not math.isclose(edges[-1],period,rel_tol=1e-9):raise ActionRejected('FULL_PERIOD_REQUIRED')
 if not np.allclose(edges/period,np.arange(len(y)+1)/len(y),rtol=1e-9,atol=1e-12):raise ActionRejected('FULL_PERIOD_REQUIRED')
 risk=n-np.r_[0.,np.cumsum(y)[:-1]]
 return -np.log1p(-y/risk),dt
def analysis_basis(hist,irfh,taus):
 mu,dt=hazard(hist);irf,di=hazard(irfh)
 if irfh['response_kind']!='IRF' or hist['response_kind']!='decay' or len(mu)!=len(irf) or not math.isclose(dt,di,rel_tol=1e-9):raise ActionRejected('IRF_GRID_MISMATCH')
 # Uniform dark hazard estimated only from caller-supplied IRF lower decile.
 bg=float(np.quantile(irf,.1));irf=np.maximum(0.,irf-bg)
 if irf.sum()<=0:raise ActionRejected('ANALYSIS_INPUT')
 irf/=irf.sum()
 return mu,irf,dt

class EdinburghFls1000tcspcRuntime:
 def __init__(self,instance_id='fls1000_01'):
  self.instance_id=instance_id;self.templates=_templates();self.time_s=0.;self.revision=0;self.plant=None;self.method=None;self.sample=None;self._run_method=None
  self.powered=False;self.armed=False;self.opened=True;self.wavelength=500e-9;self.slit=.0002;self.phase='idle';self.stage='idle';self.elapsed_s=0.;self.warning=''
  self.operation_id='';self._op_count=0;self.histograms=[];self.active=None;self._queue=[];self._target_wave=self.wavelength
  self._rng=np.random.default_rng(0);self._next=None;self._pulse=0;self._event_cdf=None;self._p_any=0.;self._grid_n=0
  self._registered_v=0.;self._removed_v=0.;self._emitted=0;self._detected=0;self._recorded=0;self._discarded=0
  self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._operation_records={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return EdinburghFls1000tcspcSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def debug_snapshot(self):
  return copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in ['templates','_rng']})|{'_rng_state':copy.deepcopy(self._rng.bit_generator.state)}
 @property
 def irf_fwhm(self):
  return math.sqrt(SOURCES[self.plant['source_model']][1]**2+(600e-12)**2+self.plant['electronics_fwhm_s']**2+self.plant['mono_dispersion_fwhm_s']**2) if self.plant else None
 def _ledger(self):
  current=self.sample['volume_m3'] if self.sample else 0.
  return dict(sample_id=self.sample['sample_id'] if self.sample else None,template_ids=[x['template_id'] for x in self.sample['components']] if self.sample else [],registered_volume_m3=self._registered_v,current_volume_m3=current,removed_volume_m3=self._removed_v,volume_residual_m3=self._registered_v-current-self._removed_v,emitted_pulses=self._emitted,detected_first_photons=self._detected,histogram_photons=self._recorded,out_of_window_photons=self._discarded,count_residual=self._detected-self._recorded-self._discarded,source_fwhm_s=SOURCES[self.plant['source_model']][1] if self.plant else None,detector_fwhm_s=600e-12,combined_irf_fwhm_s=self.irf_fwhm,evidence='intact sample-volume and first-photon ledger only; no concentration, photobleaching, chemical conversion or emitted-energy claim; pulse widths conditional documented, Gaussian quadrature shape synthetic')
 def _recent(self):return self.active if self.active is not None else self.histograms[-1] if self.histograms else None
 def read(self,visibility='agent'):
  if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
  h=self._recent();m=self.method;n=sum(h['counts']) if h else 0
  mean=float(np.dot(h['counts'],(np.asarray(h['edges_s'][:-1])+np.asarray(h['edges_s'][1:]))/2)/n) if n else None
  out={'plant.configured':bool(self.plant),'sample.loaded':bool(self.sample),'power.enabled':self.powered,'source.armed':self.armed,'source.emitting':self.phase=='running' and self.stage=='counting','cover.opened':self.opened,'cover.lift_m':.15 if self.opened else 0.,'mono.wavelength_m':self.wavelength,'mono.angle_rad':(self.wavelength-500e-9)/500e-9,'slit.width_m':self.slit,'slit.left_m':self.slit/2,'slit.right_m':self.slit/2,'method.excitation_m':m['excitation_wavelength_m'] if m else None,'method.repetition_hz':m['repetition_hz'] if m else None,'method.time_window_s':m['time_window_s'] if m else None,'method.bin_width_s':m['time_window_s']/m['bins'] if m else None,'scan.phase':self.phase,'scan.stage':self.stage,'scan.elapsed_s':self.elapsed_s,'scan.total_counts':n,'scan.peak_counts':max(h['counts']) if h else 0,'scan.mean_microtime_s':mean,'scan.completed_wavelengths':len(self.histograms),'scan.last_sample_time_s':h['last_sample_time_s'] if h else None,'safety.warning':self.warning}
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
 def _operation_snapshot(self):
  ids=['scan.total_counts','scan.peak_counts','scan.mean_microtime_s','scan.last_sample_time_s']
  return dict(operation_id=self.operation_id,status=self.phase,result=self._invoke('process.measure',{}),observations=self.session().read(instance_id=self.instance_id,state_ids=ids)['observations'])
 def _archive_operation(self):
  if self.operation_id and self.phase in ('succeeded','cancelled','failed') and self.operation_id not in self._operation_records:
   self._operation_records[self.operation_id]=copy.deepcopy(self._operation_snapshot())
 def _stop(self,phase='cancelled',warning=''):
  if self.phase=='running':
   self.phase=phase
   if self.active is not None:self.active['stop_reason']=warning or phase
  self.stage='idle';self.warning=warning;self._next=None
 def _invoke(self,k,a):
  if k=='plant.configure':
   if self.plant or self.powered or self.sample:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_surrogate_model']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   self.plant=copy.deepcopy(a);self._rng=np.random.default_rng(a['seed']);return {'configured':True}
  if k=='sample.load':
   self._idle()
   if not self.plant:raise ActionRejected('PLANT_REQUIRED')
   if self.powered or self.armed or not self.opened:raise ActionRejected('EXTERNAL_LOAD_REQUIRED')
   if not a['sample_id'] or not a['accept_assumed_response']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   if a['kind']=='fluorescent':
    if not a['components'] or a['volume_m3']<=0 or not math.isclose(sum(x['photon_weight'] for x in a['components']),1.,abs_tol=1e-12):raise ActionRejected('SAMPLE_COMPOSITION')
    if len({x['template_id'] for x in a['components']})!=len(a['components']):raise ActionRejected('SAMPLE_COMPOSITION')
    if any(x['template_id'] not in self.templates for x in a['components']):raise ActionRejected('UNKNOWN_REFERENCE')
   elif a['components']:raise ActionRejected('SAMPLE_COMPOSITION')
   self._removed_v+=self.sample['volume_m3'] if self.sample else 0.;self._registered_v+=a['volume_m3'];self.sample=copy.deepcopy(a);return {'loaded':True}
  if k=='sample.cover':
   if a['opened']:self._stop(warning='COVER_OPEN');self.armed=False
   self.opened=a['opened'];return {'opened':self.opened}
  if k=='power.set':
   if a['enabled'] and not self.plant:raise ActionRejected('PLANT_REQUIRED')
   self.powered=a['enabled']
   if not self.powered:self._stop(warning='POWER_OFF');self.armed=False
   return {'enabled':self.powered}
  if k=='source.arm':
   if a['enabled'] and (not self.plant or not self.powered or self.opened):raise ActionRejected('OPTICS_NOT_READY')
   self.armed=a['enabled']
   if not self.armed:self._stop(warning='SOURCE_DISARMED')
   return {'enabled':self.armed}
  if k=='method.configure':
   self._idle()
   if not self.plant:raise ActionRejected('PLANT_REQUIRED')
   if abs(a['excitation_wavelength_m']-SOURCES[self.plant['source_model']][0])>1e-15:raise ActionRejected('INSTALLED_SOURCE_MISMATCH')
   if abs(a['emission_wavelength_m']/1e-11-round(a['emission_wavelength_m']/1e-11))>1e-6:raise ActionRejected('WAVELENGTH_STEP')
   period=1/a['repetition_hz'];dt=a['time_window_s']/a['bins'];n=period/dt
   if a['time_window_s']>period*(1+1e-12) or abs(n-round(n))>1e-5 or n>65536:raise ActionRejected('PERIOD_GRID_RESOURCE')
   if self.plant['irf_center_s']>=period or self.irf_fwhm>=period/4:raise ActionRejected('IRF_PERIOD_RANGE')
   if a['max_acquisition_s']*a['repetition_hz']*.05>2000000:raise ActionRejected('EVENT_RESOURCE_LIMIT')
   self.method=copy.deepcopy(a);return {'configured':True}
  if k in ['acquisition.start','irf.start','scan.start']:
   self._idle()
   if not self.sample or not self.method:raise ActionRejected('SAMPLE_METHOD_REQUIRED')
   if not self.powered or not self.armed or self.opened:raise ActionRejected('OPTICS_NOT_READY')
   want_irf=k=='irf.start'
   if (self.sample['kind']=='scatterer')!=want_irf:raise ActionRejected('REFERENCE_KIND')
   if want_irf and abs(self.method['emission_wavelength_m']-self.method['excitation_wavelength_m'])>1e-15:raise ActionRejected('SCATTER_WAVELENGTH')
   waves=a['wavelengths_m'] if k=='scan.start' else [self.method['emission_wavelength_m']]
   if any(abs(w/1e-11-round(w/1e-11))>1e-6 for w in waves) or len(set(waves))!=len(waves):raise ActionRejected('WAVELENGTH_STEP')
   # All queued optical count rates checked before any state mutation.
   for w in waves:
    mu=self._mean_bins(w)
    if float(first_photon(mu).sum())>.05:raise ActionRejected('PILEUP_LIMIT')
   self._archive_operation();self._run_method=copy.deepcopy(self.method);self.phase='running';self.stage='moving';self.elapsed_s=0.;self.warning='';self.histograms=[];self.active=None;self._queue=list(waves);self._target_wave=self._queue.pop(0)
   self._op_count+=1;self.operation_id=self.instance_id+'_op_'+str(self._op_count);return dict(phase=self.phase,operation_id=self.operation_id)
  if k=='acquisition.stop':self._stop(warning='CANCELLED');return {'phase':self.phase}
  if k=='process.measure':
   s=self.read();return dict(phase=self.phase,stage=self.stage,elapsed_s=self.elapsed_s,total_counts=s['scan.total_counts'],peak_counts=s['scan.peak_counts'],mean_microtime_s=s['scan.mean_microtime_s'],completed_wavelengths=len(self.histograms),warning=self.warning)
  if k=='result.read':return dict(phase=self.phase,complete=self.phase=='succeeded',histograms=self.histograms,active_histogram=self.active,settings_json=json.dumps(self._run_method,sort_keys=True),evidence='SIMULATED photon event histogram from real reported lifetime scalar and explicit assumed exponential/spectral/IRF shapes; no original FLS1000 photons',limitations=LIMITATIONS)
  if k=='template.read':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   t=self.templates[a['substance_id']]
   return dict(substance_id=t['substance_id'],name=t['name'],source=t['source'],conditions_json=json.dumps(t['conditions'],sort_keys=True),lifetime_s=t['lifetime_s'],emission_peak_m=t['emission_peak_m'],measured_histogram=None,evidence=t['evidence'])
  if k=='product.read':return self._ledger()
  if k=='lifetime.fit':
   hs=a['histogram'];irfh=a['irf_histogram'];tau0=np.array(a['initial_lifetimes_s']);mu,irf,dt=analysis_basis(hs,irfh,tau0);n=len(mu);scale=max(mu.sum(),1e-12)
   if self.method and len(tau0)!=(1 if self.method['fit_model']=='mono' else 2):raise ActionRejected('FIT_MODEL_MISMATCH')
   def basis(taus):return np.column_stack([convolve(irf,exp_bins(n,dt,t)) for t in taus]+[np.ones(n)/n])
   weights=1/np.sqrt(np.maximum(hs['counts'],1))
   def solve(logtau):
    B=basis(np.exp(logtau));co,_=nnls(B*weights[:,None],mu*weights);return co,B
   def residual(logtau):
    co,B=solve(logtau);return (B@co-mu)*weights/scale
   try:
    with np.errstate(over='raise',invalid='raise',divide='raise'):
     fit=least_squares(residual,np.log(tau0),bounds=(math.log(5e-12),math.log(50e-6)),diff_step=1e-4,gtol=1e-12,ftol=1e-11,xtol=1e-11,max_nfev=200)
     tau=np.exp(fit.x);co,B=solve(fit.x);cond=float(np.linalg.cond(B))
     # The amplitude basis condition alone cannot establish sensitivity to tau.
     # A zero/rank-deficient lifetime Jacobian must not return the initial guess.
     if not fit.success or not math.isfinite(cond) or cond>1e6 or not np.isfinite(fit.jac).all() or np.linalg.matrix_rank(fit.jac)<len(tau) or (len(tau)>1 and max(tau)/min(tau)<1.15) or min(co[:-1])<scale*1e-4:raise ValueError()
     idx=np.argsort(tau);rmse=float(np.linalg.norm(B@co-mu)/np.linalg.norm(mu))
     if not np.isfinite(co).all() or not math.isfinite(rmse):raise ValueError()
   except (ValueError,RuntimeError,ArithmeticError,np.linalg.LinAlgError):raise ActionRejected('FIT_UNIDENTIFIABLE') from None
   return dict(lifetimes_s=tau[idx].tolist(),photon_amplitudes_per_pulse=co[:-1][idx].tolist(),background_per_pulse=float(co[-1]),relative_rmse=rmse,condition_number=cond,warning='Finite-photon IRF and first-bin discretization; no uncertainty/5ps guarantee; IRF transfer across wavelength assumes identical response.',evidence='caller-only first-photon hazard inversion, weighted nonnegative periodic mono/bi reconvolution; no template truth used')
  if k=='das.fit':
   taus=a['fixed_lifetimes_s']
   if len(set(taus))!=len(taus):raise ActionRejected('FIT_UNIDENTIFIABLE')
   waves=[];amps=[];bgs=[];errs=[]
   for h in a['histograms']:
    mu,irf,dt=analysis_basis(h,a['irf_histogram'],taus);n=len(mu);B=np.column_stack([convolve(irf,exp_bins(n,dt,t)) for t in taus]+[np.ones(n)/n])
    if np.linalg.cond(B)>1e6:raise ActionRejected('FIT_UNIDENTIFIABLE')
    co,_=nnls(B,mu);waves.append(h['emission_wavelength_m']);amps.append(co[:-1].tolist());bgs.append(float(co[-1]));errs.append(float(np.linalg.norm(B@co-mu)/np.linalg.norm(mu)))
   return dict(wavelength_m=waves,fixed_lifetimes_s=taus,photon_amplitudes_per_pulse=amps,background_per_pulse=bgs,relative_rmse=errs,evidence='caller-fixed-lifetime nonnegative decay-associated photon-amplitude spectra; not fitted global lifetimes or concentrations; synthetic spectral envelope')
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _mean_bins(self,wave):
  m=self.method;p=self.plant;s=self.sample;dt=m['time_window_s']/m['bins'];n=round(1/m['repetition_hz']/dt)
  irf=wrapped_irf(n,dt,self.irf_fwhm,p['irf_center_s']);shape=np.zeros(n)
  if s['kind']=='scatterer':shape=irf
  else:
   bw=m['slit_width_m']*p['bandwidth_per_slit'];sig=s['emission_sigma_m']
   for c in s['components']:
    t=self.templates[c['template_id']];x=wave-t['emission_peak_m']
    # Average Gaussian envelope over ideal rectangular spectral band.
    response=sig*math.sqrt(2*math.pi)/bw*(ndtr((x+bw/2)/sig)-ndtr((x-bw/2)/sig))
    shape+=c['photon_weight']*max(0.,response)*convolve(irf,exp_bins(n,dt,t['lifetime_s']))
  return s['mean_photons_per_pulse_at_peak']*shape+p['dark_rate_s_inv']/m['repetition_hz']/n
 def _new_hist(self,when):
  m=self.method;dt=m['time_window_s']/m['bins'];prob=first_photon(self._mean_bins(self.wavelength));self._p_any=float(prob.sum());self._event_cdf=np.cumsum(prob)/self._p_any if self._p_any else np.ones(len(prob));self._event_cdf[-1]=1.;self._grid_n=len(prob);self._pulse=0
  self.active=dict(emission_wavelength_m=self.wavelength,edges_s=(np.arange(m['bins']+1)*dt).tolist(),counts=[0]*m['bins'],pulses=0,duration_s=0.,start_time_s=when,last_sample_time_s=None,complete=False,stop_reason='',response_kind='IRF' if self.sample['kind']=='scatterer' else 'decay',period_s=1/m['repetition_hz'])
  self.stage='counting';self._schedule()
 def _schedule(self):
  if not self._p_any:self._next=None;return
  self._pulse+=int(self._rng.geometric(self._p_any));b=min(self._grid_n-1,int(np.searchsorted(self._event_cdf,self._rng.random(),side='right')))
  dt=self.method['time_window_s']/self.method['bins'];self._next=(self.active['start_time_s']+(self._pulse-1)/self.method['repetition_hz']+(b+.5)*dt,b)
 def _duration(self,when):
  h=self.active;h['duration_s']=max(0.,when-h['start_time_s'])
  pulses=int(math.ceil(h['duration_s']*self.method['repetition_hz']-1e-8));self._emitted+=pulses-h['pulses'];h['pulses']=pulses
 def _finish_point(self,when,success,reason):
  self._duration(when);h=self.active;h['complete']=success;h['stop_reason']=reason
  if not success:self._stop('failed',reason);return
  self.histograms.append(h);self.active=None;self._next=None
  if self._queue:self._target_wave=self._queue.pop(0);self.stage='moving'
  else:self.phase='succeeded';self.stage='idle'
 @staticmethod
 def _toward(v,target,amount):return v+max(-amount,min(amount,target-v))
 def step(self,dt_s):
  validate(number('s',0,3600),dt_s)
  if not dt_s:return self.read()
  left=dt_s;cursor=self.time_s
  while left>1e-13 and self.phase=='running':
   if self.stage=='moving':
    duration=max(abs(self._target_wave-self.wavelength)/self.plant['wavelength_speed_m_s'],abs(self.method['slit_width_m']-self.slit)/self.plant['slit_speed_m_s']);h=min(left,duration)
    self.wavelength=self._toward(self.wavelength,self._target_wave,h*self.plant['wavelength_speed_m_s']);self.slit=self._toward(self.slit,self.method['slit_width_m'],h*self.plant['slit_speed_m_s'])
    if h>=duration-1e-13:self.wavelength=self._target_wave;self.slit=self.method['slit_width_m'];self._new_hist(cursor+h)
   else:
    deadline=self.active['start_time_s']+self.method['max_acquisition_s'];limit=min(cursor+left,deadline);h=max(0.,limit-cursor);finished=False
    while self._next is not None and self._next[0]<=limit:
     when,b=self._next;self._detected+=1
     if b<len(self.active['counts']):
      self.active['counts'][b]+=1;self._recorded+=1;self.active['last_sample_time_s']=when
      kind=self.method['stop_kind'];reached=(kind=='total_counts' and sum(self.active['counts'])>=self.method['target_count']) or (kind=='peak_counts' and self.active['counts'][b]>=self.method['target_count'])
      if reached:self._finish_point(when,True,kind);h=max(0.,when-cursor);finished=True;break
     else:self._discarded+=1
     self._schedule()
    if not finished:
     self._duration(limit)
     if limit>=deadline-1e-13:self._finish_point(deadline,self.method['stop_kind']=='time','time' if self.method['stop_kind']=='time' else 'COUNT_TIMEOUT')
   self.elapsed_s+=h;cursor+=h;left-=h
  self.time_s+=dt_s;self.revision+=1;self._archive_operation();return self.read()
class EdinburghFls1000tcspcSession:
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
        m=export_edinburgh_fls1000tcspc_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.1',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        accumulated=['scan.total_counts','scan.peak_counts','scan.mean_microtime_s'];last=h._recent()
        end=last['start_time_s']+last['duration_s'] if last else h.time_s
        def timing(key):
            if last and key in accumulated:return end,[last['start_time_s'],end]
            if last and key=='scan.last_sample_time_s':return last['last_sample_time_s'] if last['last_sample_time_s'] is not None else end,None
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
'FLS1000 selected data-sheet: lifetime5ps–50us conditional on source/detector, NOT5ps IRF or raw bin. Current web10us is retained as a conflict, not substituted.',
'Manufacturer whole-body dimensions/mass remain null. Functional task-layout, lid150mm lift, grating angle/speed and slit drive motion are engineering proxies; not photo-scaled CAD.',
'Installed PMT900 has documented600ps response,185–900nm; source variants EPL405/475/635 and EPLED250 require one explicit host installation. The full250–980nm family is not continuously tunable or fully implemented.',
'EPL2020 pulse widths use per-source maxima at10MHz; applying the same Gaussian to other repetition rates is an uncalibrated assumption. EPLED250950ps uses current source table, not60ps.',
'Gaussian quadrature combines source, PMT, host electronics and monochromator widths. No measured IRF, factory resolution or universal lifetime sensitivity is claimed.',
'Only one first detected photon per pulse is simulated. Exact first-event pileup hazard included, modeled detection fraction capped5%; real TAC dead time, reverse mode, MCS and afterpulsing unsupported.',
'TCC2 TCSPC documented channels512/1024/2048/4096/8192 and time window2.5ns–50us;500-series channels belong to MCS, which is unsupported. Full periodic response includes wraparound; latent grids<=65536 remain a host resource limit. Digital bin width is not IRF or lifetime resolution.',
'Photon events are binned at bin centers; circular discrete convolution introduces a bin-scale approximation. No continuous sub-bin timing or factory jitter validation.',
'67 real reported lifetime scalars and emission peaks from author-curated dataset; most sample conditions unknown, averages may not be monoexponential. No measured photon histogram or measured emission spectrum is provided.',
'Gaussian emission envelope, component photon weights, peak photon brightness and single-exponential shape are explicit assumptions, not concentrations, spectra or excitation cross-sections inferred from lifetime.',
'Ideal rectangular spectral band with host dispersion coefficient;0.01nm selected-grating step and325mm focal length are documented, not a universal spectral resolution/calibration.',
'Finite wavelength/slit travel occurs only during acquisition sequence; rates are host assumptions. Sample loading/removing/lid/laser authorization are external trusted-host operations, not robots.',
'Intact sample volume is preserved; no photobleaching, chemistry, photothermal heating, leakage, gas or full energy/mass conservation model. Emitted pulse count is not emitted photon energy.',
'Time/total-count/peak-count stops are event scheduled; cancellation retains partial data and exposure. Last partial pulse causes at most one-pulse difference in exposure normalization.',
'Caller-only fitting requires complete full-period histograms and observed IRF; mono/bi model count must match selected method when configured. No hidden sample lifetime used.',
'Fit uses hazard inversion and nonnegative variable projection; invalid time grids and numerically rank-deficient lifetime sensitivity reject. Finite-count IRF errors, model mismatch, parameter uncertainty and full identifiability audit remain unsolved; bi lifetimes close within15% rejected as model guard.',
'Fixed-lifetime DAS reports photon amplitudes vs wavelength, not concentrations or fully optimized global lifetimes. IRF transfer across wavelength assumes identical response and is not calibrated.',
'Current detector/source editions conflict with selected2020 tables; source evidence preserves all values. Dataset CF and ATTO390/465 ambiguities excluded rather than silently corrected.',
'URDF component0.1kg/box inertia and Matterix10kg rigid mass are numerical proxies; actual mass, inertia, friction, optics alignment, failure rates unknown.',
'Six-value external simulation telemetry panel is not an OEM body LCD or complete Fluoracle GUI. No actual driver/hardware/HIL, robot contact, laser/electrical certification or reverse joint coupling.',
'Accumulated count/mean observations carry the actual acquisition window even with zero photons; scan.last_sample_time_s remains the last recorded photon time. Owned terminal poll summaries with observation windows survive later operations in memory, without full historical histograms; cancelling an old terminal does not stop a new acquisition. Session permissions/idempotency are not OS isolation or restart persistence; native async envelope and all-library checks outside this TID are not validated.'
]
def demo_hist(tau=None,wavelength=519e-9):
 n=512;dt=50e-9/n;irf=wrapped_irf(n,dt,610e-12,2e-9)
 shape=irf if tau is None else convolve(irf,exp_bins(n,dt,tau))
 y=np.rint(10000000*first_photon(.01*shape+1e-6/n)).astype(int)
 return dict(emission_wavelength_m=wavelength,edges_s=(np.arange(n+1)*dt).tolist(),counts=y.tolist(),pulses=10000000,duration_s=.5,start_time_s=0.,last_sample_time_s=.5,complete=True,stop_reason='time',response_kind='IRF' if tau is None else 'decay',period_s=50e-9)
CAPS['lifetime.fit'][6]=dict(histogram=demo_hist(4e-9),irf_histogram=demo_hist(),initial_lifetimes_s=[3e-9])
CAPS['das.fit'][6]=dict(histograms=[demo_hist(4e-9,510e-9),demo_hist(4e-9,530e-9)],irf_histogram=demo_hist(),fixed_lifetimes_s=[4e-9])
def export_edinburgh_fls1000tcspc_contract_metadata():
 caps={};slots={}
 statistics=['scan.total_counts','scan.peak_counts','scan.mean_microtime_s','scan.completed_wavelengths','scan.last_sample_time_s']
 optical=['mono.wavelength_m','mono.angle_rad','slit.width_m','slit.left_m','slit.right_m']
 stop=['scan.phase','scan.stage','source.emitting','safety.warning']
 starts=['acquisition.start','irf.start','scan.start']
 immediate={
  'plant.configure':['plant.configured','truth.ledger'],'sample.load':['sample.loaded','truth.ledger'],
  'sample.cover':['cover.opened','cover.lift_m','source.armed',*stop],
  'power.set':['power.enabled','source.armed',*stop],'source.arm':['source.armed',*stop],
  'method.configure':['method.excitation_m','method.repetition_hz','method.time_window_s','method.bin_width_s'],
  'acquisition.stop':stop,
 }
 for key in starts:immediate[key]=['scan.phase','scan.stage','scan.elapsed_s',*statistics,'safety.warning']
 on_step={key:[*optical,'source.emitting','scan.phase','scan.stage','scan.elapsed_s',*statistics,'truth.ledger','safety.warning'] for key in starts}
 on_completion={key:['scan.phase','scan.stage','source.emitting','safety.warning'] for key in starts}
 observed={
  'plant.configure':['plant.configured'],'sample.load':['sample.loaded'],'sample.cover':['cover.opened'],
  'power.set':['power.enabled'],'source.arm':['source.armed'],'acquisition.stop':['scan.phase'],
  'process.measure':['scan.phase','scan.stage','scan.elapsed_s','scan.total_counts','scan.peak_counts','scan.mean_microtime_s','scan.completed_wavelengths','safety.warning'],
  'result.read':['scan.phase',*statistics],'product.read':['truth.ledger'],
 }
 for key in starts:observed[key]=['scan.phase']
 def effects_for(key):
  descriptions={'immediate':'经检查的配置、外部事件、计数器初始化或取消；不推进光栅、狭缝或光子采集','on_step':'后续宿主step唯一推进光栅/狭缝与首光子事件；仅计数阶段发射脉冲','on_completion':'时间/计数条件或超时设置终态并停止发光，保留已取得直方图'}
  return [dict(state_id=state,timing=timing,description_zh=descriptions[timing]) for timing,mapping in [('immediate',immediate),('on_step',on_step),('on_completion',on_completion)] for state in mapping.get(key,[])]
 for k,(s,u,o,v,c,w) in SLOTS.items():
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'EdinburghFls1000tcspcSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='integral' if k in ['scan.total_counts','scan.peak_counts'] else 'interval_mean' if k=='scan.mean_microtime_s' else 'instant',time_domain='simulation',window_description='当前或最近直方图从开始到实际采集结束/取消的窗口；均值为窗口内光子的微时间均值，不是寿命或时间加权平均' if k in ['scan.total_counts','scan.peak_counts','scan.mean_microtime_s'] else None),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','INITIALIZATION_ONLY','EVIDENCE_ACK_REQUIRED','BUSY','PLANT_REQUIRED','EXTERNAL_LOAD_REQUIRED','SAMPLE_COMPOSITION','OPTICS_NOT_READY','INSTALLED_SOURCE_MISMATCH','WAVELENGTH_STEP','PERIOD_GRID_RESOURCE','IRF_PERIOD_RANGE','EVENT_RESOURCE_LIMIT','SAMPLE_METHOD_REQUIRED','REFERENCE_KIND','SCATTER_WAVELENGTH','PILEUP_LIMIT','ANALYSIS_INPUT','FULL_PERIOD_REQUIRED','IRF_GRID_MISMATCH','FIT_MODEL_MISMATCH','FIT_UNIDENTIFIABLE']
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  asynchronous=k in ['acquisition.start','irf.start','scan.start'];query=kind in ['query','analysis'];unsupported=k in UNSUPPORTED
  caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.edinburgh_fls1000tcspc.EdinburghFls1000tcspcSession.invoke',invocation=f'session.invoke(instance_id="fls1000_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),
  preconditions=[] if query or unsupported else [dict(description_zh='外部装样/闭盖/源授权与供电、离散源、周期网格、计数率；失败原子拒绝',state_ids=['power.enabled','source.armed','cover.opened','scan.phase'],enforced_by='runtime',check='Runtime._invoke and trusted-principal Session')],
  state_effects=[] if query or unsupported else effects_for(k),
  observes=observed.get(k,[]),model_effects=dict(mode='none',description_zh='明确未实现' if unsupported else '待独立场景验证',binding_ids=[]),
  execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',completion_criterion='移动完成后达到时间/总计数/峰计数终止；扫描逐点完成' if asynchronous else '返回事务，不推进世界时钟',completion_state_ids=['scan.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',retry='same_request_id',resources=['host_clock','single_photon_detector','request_cache'],partial_failure_zh='取消、开盖、断电、源撤销保留实际光子与样品体积/累计暴露；计数超时failed且保留数据。历史终态摘要及采集窗口受原身份保护，后续操作后仍可查询；取消历史终态不干扰新采集。方法快照与历史限进程内，不保存完整历史直方图'),
  errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=LIMITATIONS,source_ids=['manufacturer_primary','manufacturer_tcc2','simulation_assumption'])
 mech=dict(component_ids=['pulsed_source','sample_holder','em_grating','slit_left','slit_right','pmt','timing_board'],state_ids=list(slots),meaning_zh='周期激发、IRF卷积、首光子pileup、有限计数与调用者重卷积；不把5ps寿命下限当IRF',implementation='lumped',equations=[
 'IRF_FWHM^2=source_FWHM^2+detector_FWHM^2+electronics_FWHM^2+mono_dispersion_FWHM^2; Gaussian shape is an explicit assumption.',
 'd_i=exp(-i*dt/tau)*(1-exp(-dt/tau))/(1-exp(-T/tau)); discrete circular convolution with wrapped IRF retains periodic tails.',
 'mu_i=sum(weight_j*host_brightness*assumed_spectral_band_j*conv(IRF,d_j)_i)+dark_rate/(frequency*N).',
 'p_first_i=exp(-sum_{k<i}mu_k)*(1-exp(-mu_i)); one detected first photon maximum per pulse; no electronic deadtime claim.',
 'Occupied-pulse gaps ~ Geometric(sum(p_first)); microtime bin ~ Categorical(p_first/sum). RNG sequence independent of host step partition.',
 'Histogram + discarded_out_of_window = detected_first_photons <= emitted_pulses; sample_registered=sample_current+sample_removed.',
 'Caller-only hazard muhat_i=-log(1-count_i/(pulses-cumulative_previous_counts)); IRF observed hazard normalized after lower-decile flat-background estimate.',
 'Mono/bi lifetime variable-projection NNLS of periodic reconvolution bases; fixed-lifetime DAS uses NNLS per observed wavelength, no true sample values.',
 'Finite movement rates reach wavelength target and both slit leaves before light emits;0.01nm grid applies to selected grating, bandwidth is host-calibrated proxy.'
 ],parameters={},source_ids=['manufacturer_primary','manufacturer_irf','manufacturer_tcspc','manufacturer_tcc2','lifetime_dataset','simulation_assumption'],limitations=LIMITATIONS)
 spec=importlib.util.spec_from_file_location('_fls_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
 ann.annotate_export(SLUG,caps,slots,{'tcspc_photon_counting':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'；非原厂标定。'
 mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'tcspc_photon_counting':mech})


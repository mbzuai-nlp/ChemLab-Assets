"""CPL-300: real PL shapes, explicit CPL detector-event proxy, literature-measured g_lum emitters and faithful reference queries."""
from __future__ import annotations
import copy, importlib.util, json, math
from pathlib import Path
import numpy as np
from scipy.ndimage import gaussian_filter1d
ROOT=Path(__file__).resolve().parents[2]
SLUG='jasco_cpl300';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-078';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/expansion_batch04/t3_analytical/jasco_cpl300/output_templates/library.json'
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

PHASE=enum(['idle','running','succeeded','cancelled','failed'])
MAX_SCAN_SPEED=10000e-9/60
PLANT=obj({'extended_pmt_installed':BOOL,'lamp_warmup_s':number('s',.001,3600),'mono_reposition_speed_m_s':number('m/s',1e-9,MAX_SCAN_SPEED),'counts_per_relative_unit_s':number('1/s',1,1e9),'dark_counts_s':number('1/s',0,1e6),'blank_pl_relative':number('1',0,1),'blank_glum':number('1',-2,2),'minimum_net_counts':integer(1,100000000),'poisson_noise':BOOL,'seed':integer(0,2147483647),'accept_uncalibrated_model':BOOL,'accept_kasha_model':BOOL})
SAMPLE=obj({'sample_id':TEXT,'kind':enum(['blank','synthetic_cpl','measured_cpl']),'template_id':TEXT,'solvent':TEXT,'volume_m3':number('m^3',1e-9,3.5e-6),'concentration_mol_m3':number('mol/m^3',0,1000),'peak_pl_relative':number('1',0,1000),'synthetic_glum':number('1',-2,2),'accept_unknown_conditions':BOOL,'accept_synthetic_cpl':BOOL})
METHOD=obj({'scan_axis':enum(['emission','excitation']),'scan_mode':enum(['step','continuous']),'excitation_m':number('m',250e-9,850e-9),'emission_m':number('m',250e-9,1100e-9),'start_m':number('m',250e-9,1100e-9),'stop_m':number('m',250e-9,1100e-9),'interval_m':number('m',.025e-9,100e-9),'response_s':number('s',.0001,30),'scan_speed_m_s':number('m/s',.001e-9,MAX_SCAN_SPEED),'ex_bandwidth_m':number('m',.025e-9,30e-9),'em_bandwidth_m':number('m',.025e-9,30e-9),'detector_band':enum(['standard','extended'])})
PLANT_EXAMPLE=dict(extended_pmt_installed=False,lamp_warmup_s=.1,mono_reposition_speed_m_s=MAX_SCAN_SPEED,counts_per_relative_unit_s=1e7,dark_counts_s=10.,blank_pl_relative=.005,blank_glum=0.,minimum_net_counts=25,poisson_noise=True,seed=78,accept_uncalibrated_model=True,accept_kasha_model=True)
SAMPLE_EXAMPLE=dict(sample_id='external_cpl_cell',kind='synthetic_cpl',template_id='pcad_032',solvent='basic ethanol',volume_m3=2e-6,concentration_mol_m3=.0002,peak_pl_relative=1.,synthetic_glum=.04,accept_unknown_conditions=True,accept_synthetic_cpl=True)
BLANK_EXAMPLE=dict(SAMPLE_EXAMPLE,sample_id='external_blank_cell',kind='blank',template_id='',concentration_mol_m3=0.,peak_pl_relative=0.,synthetic_glum=0.,accept_synthetic_cpl=False)
METHOD_EXAMPLE=dict(scan_axis='emission',scan_mode='step',excitation_m=400e-9,emission_m=520e-9,start_m=500e-9,stop_m=540e-9,interval_m=10e-9,response_s=.05,scan_speed_m_s=MAX_SCAN_SPEED,ex_bandwidth_m=5e-9,em_bandwidth_m=5e-9,detector_band='standard')
GQUALITY=enum(['valid','unavailable','low_net_counts','nonpositive_pl','outside_physical_range'])
POINT=obj({'requested_wavelength_m':number('m'),'excitation_m':number('m'),'emission_m':number('m'),'wavelength_window_m':arr(number('m')),'left_relative':number('1'),'right_relative':number('1'),'pl':number('1'),'cpl':number('1'),'glum':nullable(number('1',-2,2)),'glum_quality':GQUALITY,'raw_left_counts':number('1',0),'raw_right_counts':number('1',0),'net_counts':number('1'),'integration_s':number('s',0),'sample_time_s':number('s',0),'sample_window_s':arr(number('s',0))})
MEASURE=obj({'template_source':TEXT,'phase':PHASE,'stage':TEXT,'kind':TEXT,'elapsed_s':number('s',0),'progress':number('1',0,1),'completed_points':integer(0,10000),'pl':nullable(number('1')),'cpl':nullable(number('1')),'glum':nullable(number('1',-2,2)),'glum_quality':GQUALITY,'warning':TEXT})
PROVENANCE=obj({'pl_evidence':TEXT,'cpl_evidence':TEXT,'glum_evidence':TEXT,'intensity_unit':enum(['relative_unit']),'source_temperature_k':nullable(number('K')),'unknown_conditions_accepted':BOOL,'limitations':arr(TEXT)})
RESULT=obj({'template_source':TEXT,'phase':PHASE,'stage':TEXT,'kind':TEXT,'complete':BOOL,'points':arr(POINT),'settings_json':TEXT,'provenance':PROVENANCE})
RAW=obj({'left_counts':nullable(number('1',0)),'right_counts':nullable(number('1',0)),'integration_s':nullable(number('s',0)),'sample_window_s':nullable(arr(number('s',0))),'evidence':TEXT})
LEDGER=obj({'sample_id':nullable(TEXT),'template_id':nullable(TEXT),'registered_volume_m3':number('m^3',0),'current_volume_m3':number('m^3',0),'removed_volume_m3':number('m^3',0),'volume_residual_m3':number('m^3'),'registered_solute_mol':number('mol',0),'current_solute_mol':number('mol',0),'removed_solute_mol':number('mol',0),'solute_residual_mol':number('mol'),'left_signal_events':number('1',0),'right_signal_events':number('1',0),'left_background_events':number('1',0),'right_background_events':number('1',0),'left_dark_events':number('1',0),'right_dark_events':number('1',0),'detected_events':number('1',0),'detector_partition_residual':number('1'),'completed_gate_s':number('s',0),'evidence':TEXT})
TEMPLATE=obj({'substance_id':TEXT,'name':TEXT,'template_json':TEXT})
REFERENCE=obj({'reference_id':TEXT,'reference_json':TEXT})
GLUM_INPUT=obj({'left_relative':number('1'),'right_relative':number('1'),'net_counts':number('1'),'minimum_net_counts':number('1',1,1e8)})
GLUM_OUTPUT=obj({'pl':number('1'),'cpl':number('1'),'glum':nullable(number('1',-2,2)),'quality':GQUALITY})
CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None):CAPS[k]=[i,o,c,z,kind,vis,{} if ex is None else ex,[]]
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body','pmt_detector'],'宿主一次设置选件、暖机、等效计数/噪声，显式接受未校准代理','configuration','evaluator',PLANT_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_cell'],'可信宿主装入完整液池；synthetic_cpl=真实PL形状+显式合成glum；measured_cpl=文献实测g_lum发光体（template_id=记录:对映体，溶剂/激发须与源一致），不从吸收CD猜CPL','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.unload',obj({'recipient_id':TEXT}),obj({'unloaded':BOOL,'volume_m3':number('m^3',0)}),['sample_cell'],'完整池外部卸下，保留体积与溶质账本','device_command','evaluator',{'recipient_id':'external_rack'})
cap('cover.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['sample_cover'],'外部盖开闭；开盖取消采集并共同关闭两快门','device_command','evaluator',{'opened':False})
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','xe_lamp'],'逻辑供电；关机关闭灯与两快门并使空白失效',ex={'enabled':True})
cap('lamp.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['xe_lamp'],'150W仅灯额定电功率；暖机时间为明确宿主假设',ex={'enabled':True})
cap('shutter.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['excitation_shutter','emission_shutter'],'两侧共同快门代理；未实现独立实机快门响应',ex={'opened':True})
cap('method.configure',METHOD,obj({'configured':BOOL}),['excitation_prism_carrier','emission_prism_carrier','pmt_detector'],'设置激发/发射、连续/步进、积分DIT和检测器波段；带宽为工程核参数非0.025nm波长分辨率','configuration','agent',METHOD_EXAMPLE)
cap('blank.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['excitation_prism_carrier','emission_prism_carrier','excitation_shutter','emission_shutter','pmt_detector'],'相同扫描设置完整采集空白；部分空白不可使用')
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['excitation_prism_carrier','emission_prism_carrier','excitation_shutter','emission_shutter','pmt_detector'],'有限移动与积分门取得左右等效计数，空白扣除后给PL、CPL及受保护glum')
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['excitation_shutter','emission_shutter'],'停止并保留已完成积分门；未完成门不补造观测')
cap('process.measure',obj({}),MEASURE,['host_screen'],'最近实际观测点的PL/CPL/glum和进度；低计数或非物理比值glum为null','query')
cap('result.read',obj({}),RESULT,['host_screen'],'带时间窗与原采集方法的完整点序列；强度为相对单位，不是W','query')
cap('raw.read',obj({}),RAW,['pmt_detector'],'最近完整门左右等效检测计数，非真实OEM ADC/光子计数模式','query')
cap('result.export',obj({'format':enum(['json','csv'])}),obj({'format':TEXT,'content':TEXT}),['host_screen'],'纯返回带证据JSON/CSV内容，不写文件','query','agent',{'format':'json'})
cap('blank.read',obj({}),obj({'valid':BOOL,'result':nullable(RESULT)}),['host_screen'],'读取完整空白及当前方法/溶剂匹配状态','query')
cap('template.read',obj({'substance_id':TEXT}),TEMPLATE,['host_screen'],'调用者指定的公开PL/吸收源谱；保留未知条件与原始signed基线','query','agent',{'substance_id':'pcad_032'})
cap('reference.read',obj({'reference_id':TEXT}),REFERENCE,['host_screen'],'独立公开真实CPL/glum参考原样查询（SFEG配置与57种文献g_lum发光体记录）；未知CPL强度标度不与其他PL拼合','query','agent',{'reference_id':'sfeg_blue_l'})
cap('glum.compute',GLUM_INPUT,GLUM_OUTPUT,['host_screen'],'仅用调用者IL/IR计算总PL、差CPL、2ΔI/总PL；不按厂家半和DC误差一倍','analysis','agent',{'left_relative':.51,'right_relative':.49,'net_counts':1000.,'minimum_net_counts':25.})
cap('product.read',obj({}),LEDGER,['sample_cell','pmt_detector'],'私有完整池物料及完成积分门内等效检测事件分割账本，不声称全光能守恒','query','evaluator')
cap('auto_dit.start',obj({}),obj({}),['pmt_detector'],'厂家Auto DIT自适应准则/置信度未知，明确未实现')
cap('detector.calibrate',obj({}),obj({}),['pmt_detector'],'真实CPL/PL增益、偏振与PMT标定未实现')
UNSUPPORTED={'auto_dit.start','detector.calibrate'}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='host_screen',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','宿主已声明选件与代理参数','setpoint',c='body')
slot('sample.loaded',BOOL,'1','完整样品池已外部装入','scene',c='sample_cell')
slot('power.enabled',BOOL,'1','逻辑供电','setpoint',c='power_inlet',w=('power.set','enabled'))
slot('cover.opened',BOOL,'1','外部盖开启','scene',c='sample_cover')
slot('cover.lift_m',number('m',0,.15),'m','外部盖工程提升量','scene',c='sample_cover')
slot('lamp.enabled',BOOL,'1','灯逻辑使能','setpoint',c='xe_lamp',w=('lamp.set','enabled'))
slot('lamp.ready',BOOL,'1','达到宿主暖机时长，非实机灯稳定校验','derived',c='xe_lamp')
slot('shutter.opened',BOOL,'1','激发与发射两快门共同状态','scene',c='excitation_shutter',w=('shutter.set','opened'))
slot('shutter.angle_rad',number('rad',0,math.pi/2),'rad','两快门共同0/π/2工程角','scene',c='excitation_shutter')
slot('optics.excitation_m',number('m',250e-9,850e-9),'m','激发棱镜单色器实际工程波长；独立Ex范围未校准','scene',c='excitation_prism_carrier')
slot('optics.emission_m',number('m',250e-9,1100e-9),'m','发射棱镜单色器实际工程波长','scene',c='emission_prism_carrier')
slot('excitation.angle_rad',number('rad',-.35,.35),'rad','250–850nm到±0.35rad的工程棱镜转角映射','scene',c='excitation_prism_carrier')
slot('emission.angle_rad',number('rad',-.35,.35),'rad','250–1100nm到±0.35rad工程棱镜转角映射','scene',c='emission_prism_carrier')
slot('detector.band',enum(['standard','extended']),'1','当前检测器波段；extended需选件','setpoint',c='pmt_detector')
slot('method.response_s',nullable(number('s')),'s','DIT积分门宽，不是已测硬件时间常数','setpoint',c='pmt_detector')
slot('method.scan_mode',TEXT,'1','当前配置连续或步进方法','setpoint')
slot('blank.valid',BOOL,'1','完整空白仍与当前扫描方法和溶剂相同')
slot('scan.phase',PHASE,'1','当前操作生命周期')
slot('scan.stage',TEXT,'1','当前移动/积分阶段')
slot('scan.elapsed_s',number('s',0),'s','含有限单色器移动的操作实际时间')
slot('scan.progress',number('1',0,1),'1','完成观测点占方法网格总点数')
slot('scan.completed_points',integer(0,10000),'1','完整积分门观测点数')
slot('scan.pl',nullable(number('1')),'1','最近空白校正总PL=IL+IR，相对单位')
slot('scan.cpl',nullable(number('1')),'1','最近空白校正CPL=IL−IR，相对单位')
slot('scan.glum',nullable(number('1',-2,2)),'1','2(IL−IR)/(IL+IR)，低计数/非物理比值为空')
slot('scan.glum_quality',GQUALITY,'1','glum有效性与缺失原因')
slot('scan.last_sample_time_s',nullable(number('s',0)),'s','最近完成积分门世界时刻')
slot('safety.warning',TEXT,'1','公开中止信息，不含隐藏样品身份')
slot('truth.ledger',LEDGER,'1','私有样品物料及完成门等效检测计数账本','process_truth','evaluator','sample_cell')

def _library():return json.loads((ROOT/TEMPLATE_REL).read_text())
def _provenance():return dict(pl_evidence='unavailable',cpl_evidence='unavailable',glum_evidence='unavailable',intensity_unit='relative_unit',source_temperature_k=None,unknown_conditions_accepted=False,limitations=[])
def _grid(m):
 if m['stop_m']<=m['start_m']:raise ActionRejected('SPECTRAL_GRID')
 n=int(math.ceil((m['stop_m']-m['start_m'])/m['interval_m']))+1
 if n>10000:raise ActionRejected('EVENT_RESOURCE_LIMIT')
 x=m['start_m']+np.arange(n)*m['interval_m'];x=x[x<m['stop_m']-1e-15*m['stop_m']];return np.r_[x,m['stop_m']]
def _smoothed(record,key,ordinate,bandwidth):
 p=record[key];x=np.asarray(p['wavelength_m'],float);y=np.asarray(p[ordinate],float)
 if x.ndim!=1 or x.shape!=y.shape or len(x)<2 or not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)) or np.any(np.diff(x)<=0):raise ActionRejected('INVALID_REFERENCE')
 scale=float(np.max(y))
 if scale<=0:raise ActionRejected('ZERO_REFERENCE')
 y=np.maximum(y/scale,0.);source=p.get('source_bandwidth_m')
 if source is not None and bandwidth<source-1e-16:raise ActionRejected('SOURCE_BANDWIDTH_LIMIT')
 extra=math.sqrt(max(0.,bandwidth**2-(source or 0.)**2))
 if extra:
  d=min(float(np.min(np.diff(x))),bandwidth/8);n=int(math.ceil((x[-1]-x[0])/d))+1
  if n>200000:raise ActionRejected('EVENT_RESOURCE_LIMIT')
  gx=np.linspace(x[0],x[-1],n);y=gaussian_filter1d(np.interp(gx,x,y),extra/(math.sqrt(8*math.log(2))*(gx[1]-gx[0])),mode='nearest');x=gx
 return x,y

def _average_curve(x,y,lo,hi):
 if lo<x[0]-1e-16 or hi>x[-1]+1e-16:raise ActionRejected('SOURCE_WAVELENGTH_RANGE')
 if hi<=lo:return float(np.interp(lo,x,y))
 axis=np.r_[lo,x[(x>lo)&(x<hi)],hi]
 values=np.interp(axis,x,y)
 return float(np.sum(.5*(values[1:]+values[:-1])*np.diff(axis))/(hi-lo))

def _glum(left,right,net_counts,minimum):
 pl=float(np.longdouble(left)+np.longdouble(right));cpl=float(np.longdouble(left)-np.longdouble(right))
 if not math.isfinite(pl) or not math.isfinite(cpl):raise ActionRejected('ANALYSIS_RANGE')
 quality='valid';g=None
 if pl<=0:quality='nonpositive_pl'
 elif net_counts<minimum:quality='low_net_counts'
 elif left<0 or right<0:quality='outside_physical_range'
 else:
  scale=max(left,right);a=left/scale;b=right/scale;g=max(-2.,min(2.,2*(a-b)/(a+b)))
 return dict(pl=pl,cpl=cpl,glum=g,quality=quality)

def _kernel(x,y,bandwidth):
 # Gaussian of the full requested FWHM (literature curve bandwidth unknown); no normalisation or clipping.
 d=min(float(np.min(np.diff(x))),bandwidth/8);n=int(math.ceil((x[-1]-x[0])/d))+1
 if n>200000:raise ActionRejected('EVENT_RESOURCE_LIMIT')
 gx=np.linspace(x[0],x[-1],n);return gx,gaussian_filter1d(np.interp(gx,x,y),bandwidth/(math.sqrt(8*math.log(2))*(gx[1]-gx[0])),mode='nearest')

def _measured_selection(measured,a):
 rid,sep,lab=a['template_id'].partition(':');r=measured.get(rid)
 if not sep or r is None or lab not in r.get('runtime_enantiomers',[]):raise ActionRejected('UNKNOWN_REFERENCE')
 if r.get('curves'):conds=[(0,r['conditions'][0])]
 else:conds=[(i,c) for i,c in enumerate(r['conditions']) if lab in c.get('runtime_enantiomers',[])]
 match=[(i,c) for i,c in conds if c['solvent'] is not None and c['solvent']==a['solvent']]
 if not match:
  match=[(i,c) for i,c in conds if c['solvent'] is None]
  if not match:raise ActionRejected('SOURCE_CONDITIONS_MISMATCH')
  if not a['accept_unknown_conditions']:raise ActionRejected('UNKNOWN_CONDITIONS_ACK_REQUIRED')
 i,c=match[0]
 if any(c.get(q) is None for q in ['excitation_m','temperature_k','concentration_mol_m3']) and not a['accept_unknown_conditions']:raise ActionRejected('UNKNOWN_CONDITIONS_ACK_REQUIRED')
 return dict(record_id=rid,enantiomer=lab,condition_index=i,solvent_from_source=c['solvent'] is not None)

class JascoCpl300Runtime:
 def __init__(self,instance_id='cpl300_01',library=None):
  self.instance_id=instance_id;self.library=_library() if library is None else copy.deepcopy(library);self.templates=self.library['compounds'];self.references=self.library.get('cpl_references',{});self.measured=self.library.get('cpl_measured',{});self._msel=None
  self.time_s=0.;self.revision=0;self.plant=None;self.method=None;self.sample=None;self.powered=False;self.lamp=False;self.lamp_age=0.;self.opened=True;self.shutter=False
  self.excitation=250e-9;self.emission=250e-9;self.phase='idle';self.stage='idle';self.kind='';self.elapsed_s=0.;self.warning='';self.operation_id='';self._op_count=0;self._run_method=None;self._run_provenance=_provenance();self._points=[];self._grid=np.array([]);self._segments=[];self._segment=None;self._segment_elapsed=0.;self._due=None;self._blank=None
  self._registered_v=0.;self._removed_v=0.;self._registered_n=0.;self._removed_n=0.;self._events=np.zeros(6);self._detected=0.;self._gate_s=0.;self._last_sample_time=None
  self._rng=np.random.default_rng(0);self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._operation_records={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return JascoCpl300Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in ['library','templates','references','measured','_rng']})|{'_rng_state':copy.deepcopy(self._rng.bit_generator.state)}
 def _ready(self):return bool(self.plant and self.powered and self.lamp and self.lamp_age+1e-12>=self.plant['lamp_warmup_s'])
 def _fingerprint(self):return dict(method=self.method,solvent=self.sample['solvent']) if self.method and self.sample else None
 def _blank_valid(self):return bool(self.powered and self.lamp and self._blank and self._blank['fingerprint']==self._fingerprint())
 def _ledger(self):
  v=self.sample['volume_m3'] if self.sample else 0.;n=v*self.sample['concentration_mol_m3'] if self.sample else 0.
  result=dict(sample_id=self.sample['sample_id'] if self.sample else None,template_id=self.sample['template_id'] if self.sample else None,registered_volume_m3=self._registered_v,current_volume_m3=v,removed_volume_m3=self._removed_v,volume_residual_m3=self._registered_v-self._removed_v-v,registered_solute_mol=self._registered_n,current_solute_mol=n,removed_solute_mol=self._removed_n,solute_residual_mol=self._registered_n-self._removed_n-n,detected_events=self._detected,detector_partition_residual=self._detected-float(np.sum(self._events)),completed_gate_s=self._gate_s,evidence='equivalent detector events in completed integration gates only; intact-cell inventory; not optical power, incident/emitted photon closure, idle-open-shutter exposure or chemistry')
  for key,value in zip(['left_signal_events','right_signal_events','left_background_events','right_background_events','left_dark_events','right_dark_events'],self._events):result[key]=float(value)
  return result
 def _measure(self):
  p=self._points[-1] if self._points else None
  return dict(template_source=TEMPLATE_REL,phase=self.phase,stage=self.stage,kind=self.kind,elapsed_s=self.elapsed_s,progress=len(self._points)/len(self._grid) if len(self._grid) else 0.,completed_points=len(self._points),pl=p['pl'] if p else None,cpl=p['cpl'] if p else None,glum=p['glum'] if p else None,glum_quality=p['glum_quality'] if p else 'unavailable',warning=self.warning)
 def _result(self):return dict(template_source=TEMPLATE_REL,phase=self.phase,stage=self.stage,kind=self.kind,complete=self.phase=='succeeded',points=copy.deepcopy(self._points),settings_json=json.dumps(self._run_method,sort_keys=True),provenance=copy.deepcopy(self._run_provenance))
 def _raw(self):
  p=self._points[-1] if self._points else None
  return dict(left_counts=p['raw_left_counts'] if p else None,right_counts=p['raw_right_counts'] if p else None,integration_s=p['integration_s'] if p else None,sample_window_s=p['sample_window_s'] if p else None,evidence='synthetic equivalent detector events; float expectations when poisson_noise=false, not OEM raw PMT/ADC data')
 def read(self,visibility='agent'):
  if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
  m=self._measure();method=self.method
  values={'plant.configured':self.plant is not None,'sample.loaded':self.sample is not None,'power.enabled':self.powered,'cover.opened':self.opened,'cover.lift_m':.15 if self.opened else 0.,'lamp.enabled':self.lamp,'lamp.ready':self._ready(),'shutter.opened':self.shutter,'shutter.angle_rad':math.pi/2 if self.shutter else 0.,'optics.excitation_m':self.excitation,'optics.emission_m':self.emission,'excitation.angle_rad':max(-.35,min(.35,-.35+.7*(self.excitation-250e-9)/600e-9)),'emission.angle_rad':max(-.35,min(.35,-.35+.7*(self.emission-250e-9)/850e-9)),'detector.band':method['detector_band'] if method else 'standard','method.response_s':method['response_s'] if method else None,'method.scan_mode':method['scan_mode'] if method else '', 'blank.valid':self._blank_valid(),'scan.phase':self.phase,'scan.stage':self.stage,'scan.elapsed_s':self.elapsed_s,'scan.progress':m['progress'],'scan.completed_points':len(self._points),'scan.pl':m['pl'],'scan.cpl':m['cpl'],'scan.glum':m['glum'],'scan.glum_quality':m['glum_quality'],'scan.last_sample_time_s':self._last_sample_time,'safety.warning':self.warning,'truth.ledger':self._ledger()}
  if visibility=='agent':values.pop('truth.ledger')
  return copy.deepcopy(values)
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[k][0],a)
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  snap=self.debug_snapshot()
  try:
   result=self._invoke(k,copy.deepcopy(a));validate(CAPS[k][1],result)
   if CAPS[k][4] not in ['query','analysis']:self.revision+=1;self._archive_operation()
   return copy.deepcopy(result)
  except Exception:
   library=self.library;rs=snap.pop('_rng_state');self.__dict__.clear();self.__dict__.update(snap);self.library=library;self.templates=library['compounds'];self.references=library.get('cpl_references',{});self.measured=library.get('cpl_measured',{});self._rng=np.random.default_rng();self._rng.bit_generator.state=rs;raise
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _manual(self):
  self._idle()
  if not self.opened:raise ActionRejected('COVER_MUST_BE_OPEN')
 def _validate_method(self,m):
  grid=_grid(m)
  if m['scan_axis']=='excitation' and m['stop_m']>850e-9:raise ActionRejected('EXCITATION_WINDOW')
  emission=[m['start_m'],m['stop_m']] if m['scan_axis']=='emission' else [m['emission_m']]
  ex_max=m['stop_m'] if m['scan_axis']=='excitation' else m['excitation_m']
  if min(emission)<=ex_max:raise ActionRejected('STOKES_MODEL_REQUIRED')
  if m['detector_band']=='extended':
   if self.plant is None or not self.plant['extended_pmt_installed']:raise ActionRejected('OPTION_NOT_INSTALLED')
   low,high=400e-9,1100e-9
  else:low,high=250e-9,850e-9
  if min(emission)<low or max(emission)>high:raise ActionRejected('DETECTOR_WINDOW')
  if m['scan_mode']=='continuous' and m['response_s']>float(np.min(np.diff(grid)))/m['scan_speed_m_s']+1e-12:raise ActionRejected('RESPONSE_OVERLAPS_INTERVAL')
  total=len(grid)*m['response_s']+(m['stop_m']-m['start_m'])/(m['scan_speed_m_s'] if m['scan_mode']=='continuous' else self.plant['mono_reposition_speed_m_s'] if self.plant else MAX_SCAN_SPEED)
  if total>86400:raise ActionRejected('EVENT_RESOURCE_LIMIT')
  return grid
 def _invoke(self,k,a):
  if k=='plant.configure':
   if self.plant is not None or self.time_s or self.sample:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_uncalibrated_model']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   self.plant=a;self._rng=np.random.default_rng(a['seed']);return {'configured':True}
  if k=='sample.load':
   self._manual()
   if self.plant is None:raise ActionRejected('PLANT_REQUIRED')
   if self.sample:raise ActionRejected('UNLOAD_REQUIRED')
   if not a['sample_id'] or not a['solvent']:raise ActionRejected('INVALID_ARGUMENT')
   self._msel=None
   if a['kind']=='blank':
    if a['template_id'] or a['concentration_mol_m3'] or a['peak_pl_relative'] or a['synthetic_glum']:raise ActionRejected('SAMPLE_COMPOSITION')
   elif a['kind']=='measured_cpl':
    if a['synthetic_glum'] or a['accept_synthetic_cpl']:raise ActionRejected('SAMPLE_COMPOSITION')
    self._msel=_measured_selection(self.measured,a)
   else:
    r=self.templates.get(a['template_id'])
    if r is None or not r.get('pl') or not r.get('absorption'):raise ActionRejected('UNKNOWN_REFERENCE')
    if not a['accept_synthetic_cpl']:raise ActionRejected('SYNTHETIC_CPL_ACK_REQUIRED')
    c=r.get('conditions',{})
    if c.get('solvent') is not None and a['solvent']!=c['solvent']:raise ActionRejected('SOURCE_CONDITIONS_MISMATCH')
    if any(c.get(q) is None for q in ['temperature_k','concentration_mol_m3']) and not a['accept_unknown_conditions']:raise ActionRejected('UNKNOWN_CONDITIONS_ACK_REQUIRED')
   self.sample=a;self._registered_v+=a['volume_m3'];self._registered_n+=a['volume_m3']*a['concentration_mol_m3'];return {'loaded':True}
  if k=='sample.unload':
   self._manual()
   if not a['recipient_id']:raise ActionRejected('INVALID_ARGUMENT')
   if not self.sample:raise ActionRejected('SAMPLE_REQUIRED')
   v=self.sample['volume_m3'];self._removed_v+=v;self._removed_n+=v*self.sample['concentration_mol_m3'];self.sample=None;self._msel=None;return {'unloaded':True,'volume_m3':v}
  if k=='cover.set':
   self.opened=a['opened']
   if self.opened:self._stop('cover_open');self.shutter=False
   return a
  if k=='power.set':
   self.powered=a['enabled']
   if not self.powered:self._stop('power_off');self.lamp=False;self.lamp_age=0.;self.shutter=False;self._blank=None
   return a
  if k=='lamp.set':
   if a['enabled'] and (not self.powered or self.plant is None):raise ActionRejected('POWER_PLANT_REQUIRED')
   if self.lamp!=a['enabled']:self.lamp_age=0.
   self.lamp=a['enabled']
   if not self.lamp:self._stop('lamp_off');self.shutter=False;self._blank=None
   return a
  if k=='shutter.set':
   if a['opened']:
    self._idle()
    if not self._ready() or self.opened or self.sample is None:raise ActionRejected('OPTICS_NOT_READY')
   else:self._stop('shutter_closed')
   self.shutter=a['opened'];return a
  if k=='method.configure':self._idle();self._validate_method(a);self.method=a;return {'configured':True}
  if k in ['blank.start','acquisition.start']:return self._start('blank' if k=='blank.start' else 'sample')
  if k=='acquisition.stop':self._stop('cancelled_by_caller');return {'phase':self.phase}
  if k=='process.measure':return self._measure()
  if k=='result.read':return self._result()
  if k=='raw.read':return self._raw()
  if k=='product.read':return self._ledger()
  if k=='blank.read':return dict(valid=self._blank_valid(),result=copy.deepcopy(self._blank['result']) if self._blank else None)
  if k=='template.read':
   r=self.templates.get(a['substance_id'])
   if r is None:raise ActionRejected('UNKNOWN_REFERENCE')
   return dict(substance_id=a['substance_id'],name=r['name'],template_json=json.dumps(r,ensure_ascii=False,allow_nan=False))
  if k=='reference.read':
   r=self.references.get(a['reference_id']) or self.measured.get(a['reference_id'])
   if r is None:raise ActionRejected('UNKNOWN_REFERENCE')
   return dict(reference_id=a['reference_id'],reference_json=json.dumps(r,ensure_ascii=False,allow_nan=False))
  if k=='glum.compute':return _glum(a['left_relative'],a['right_relative'],a['net_counts'],a['minimum_net_counts'])
  if k=='result.export':
   result=self._result()
   if a['format']=='json':content=json.dumps(result,ensure_ascii=False,allow_nan=False)
   else:
    fields=['requested_wavelength_m','excitation_m','emission_m','left_relative','right_relative','pl','cpl','glum','glum_quality','raw_left_counts','raw_right_counts','net_counts','integration_s','sample_time_s']
    content='# '+json.dumps({k:v for k,v in result.items() if k!='points'},ensure_ascii=False,allow_nan=False)+'\n'+','.join(fields)+'\n';content+=''.join(','.join('' if p[k] is None else str(p[k]) for k in fields)+'\n' for p in result['points'])
   return dict(format=a['format'],content=content)
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _prepare_response(self):
  provenance=_provenance()
  if self.kind=='blank':provenance.update(pl_evidence='synthetic_background_and_dark',cpl_evidence='synthetic',glum_evidence='synthetic',limitations=['Host equivalent blank/dark event rates, not a measured solvent CPL response.']);return provenance
  if self.sample['kind']=='measured_cpl':return self._prepare_measured(provenance)
  r=self.templates[self.sample['template_id']];m=self.method
  if not self.plant['accept_kasha_model']:raise ActionRejected('KASHA_ACK_REQUIRED')
  self._ex_curve=_smoothed(r,'absorption','epsilon_m2_mol',m['ex_bandwidth_m']);self._em_curve=_smoothed(r,'pl','emission_relative',m['em_bandwidth_m'])
  ex_range=[m['start_m'],m['stop_m']] if m['scan_axis']=='excitation' else [m['excitation_m']]*2
  em_range=[m['start_m'],m['stop_m']] if m['scan_axis']=='emission' else [m['emission_m']]*2
  _average_curve(*self._ex_curve,*ex_range);_average_curve(*self._em_curve,*em_range)
  provenance.update(pl_evidence='measured_PL_shape_with_synthetic_amplitude_and_excitation_factor',cpl_evidence='synthetic',glum_evidence='synthetic_host_glum',source_temperature_k=r.get('conditions',{}).get('temperature_k'),unknown_conditions_accepted=self.sample['accept_unknown_conditions'],limitations=['Real PL and absorption shapes retain source broadening; negative source baselines remain in queries and are clipped only in this nonnegative forward-rate surrogate.','Host peak amplitude and normalized-absorption Kasha excitation factor are uncalibrated; no inferred molar PL response, sample watts, true excitation action spectrum or quantum-yield calibration.','CPL and glum are explicit host assumptions; independent real CPL references have display-angle units and cannot recover joint calibrated IL/IR or total PL.','Source temperature/concentration unknowns remain unknown. Method changes do not modify this acquisition snapshot.'])
  return provenance
 def _gate_windows(self,grid):
  m=self.method;span=m['response_s']*m['scan_speed_m_s'] if m['scan_mode']=='continuous' else 0.
  return [(float(w),float(w)) if i==0 or not span else (float(w)-span,float(w)) if w>=grid[i-1] else (float(w),float(w)+span) for i,w in enumerate(grid)]
 def _prepare_measured(self,provenance):
  m=self.method;sel=self._msel;r=self.measured[sel['record_id']];c=r['conditions'][sel['condition_index']]
  if m['scan_axis']!='emission':raise ActionRejected('REFERENCE_AXIS_UNSUPPORTED')
  ex=c.get('excitation_m')
  if ex is not None and abs(m['excitation_m']-ex)>max(m['ex_bandwidth_m'],1e-9)+1e-15:raise ActionRejected('SOURCE_CONDITIONS_MISMATCH')
  windows=self._gate_windows(_grid(m));self._mcurve=None;self._mpoints=None;evid=set()
  if r.get('curves'):
   cv=r['curves'][sel['enantiomer']];x=np.asarray(cv['wavelength_m'],float);pl=np.asarray(cv['pl_relative'],float);g=np.asarray(cv['glum'],float)
   lo,hi=cv['usable_window_m']
   if any(a<lo-1e-15 or b>hi+1e-15 for a,b in windows):raise ActionRejected('REFERENCE_WAVELENGTH_RANGE')
   gx,pls=_kernel(x,pl,m['em_bandwidth_m']);_,cps=_kernel(x,pl*g,m['em_bandwidth_m']);self._mcurve=(gx,pls/float(np.max(pls)),cps/float(np.max(pls)));evid.add(cv['evidence'])
   pl_e='measured_literature_PL_shape_(source_data_curve)_with_host_amplitude'
  else:
   pts=[(q['validity_window_m'][0],q['validity_window_m'][1],q['glum'],q['evidence']) for q in c['enantiomers'][sel['enantiomer']]]
   for a,b in windows:
    hit=[q for q in pts if q[0]-1e-15<=a and b<=q[1]+1e-15]
    if not hit:raise ActionRejected('REFERENCE_WAVELENGTH_RANGE')
    if m['em_bandwidth_m']>min(q[1]-q[0] for q in hit)+1e-15:raise ActionRejected('SOURCE_BANDWIDTH_LIMIT')
    evid|={q[3] for q in hit}
   self._mpoints=pts;pl_e='host_amplitude_flat_inside_literature_glum_validity_window'
  provenance.update(pl_evidence=pl_e,cpl_evidence='literature_glum_times_host_PL',glum_evidence='measured_literature' if evid=={'measured_literature'} else 'measured_literature_with_derived_sign' if 'derived' in evid else 'measured_literature',source_temperature_k=c.get('temperature_k'),unknown_conditions_accepted=self.sample['accept_unknown_conditions'],limitations=[
   f"Literature g_lum target: {r['record_id']} {r['name']} ({sel['enantiomer']}), doi:{r['source']['doi']}, licence {r['source']['licence'] or r['source']['licence_flag']}.",
   'g_lum is the cited measurement (not a CPL-300 measurement of this sample); IL/IR are host Poisson events built so that 2(IL-IR)/(IL+IR) equals the literature value inside its validity window.',
   'Scalar records carry no PL band shape: PL amplitude is the host value and is held flat only inside the reported validity window; scans outside it are rejected.' if self._mpoints else 'Camphorquinone PL and g_lum shapes are the original source-data curves (Fluo/glum), convolved with the requested emission bandwidth; source bandwidth unknown.',
   'Excitation factor is 1 (no absorption spectrum for these emitters); no molar brightness, quantum yield, concentration or temperature response is inferred.'])
  return provenance
 def _measured_signal(self,lo,hi):
  lo,hi=min(lo,hi),max(lo,hi);peak=self.sample['peak_pl_relative']
  if self._mcurve is not None:
   x,pl,cp=self._mcurve;s=_average_curve(x,pl,lo,hi)
   return peak*max(s,0.),(max(-2.,min(2.,_average_curve(x,cp,lo,hi)/s)) if s>0 else 0.)
  hit=[q for q in self._mpoints if q[0]-1e-15<=lo and hi<=q[1]+1e-15]
  return peak,hit[0][2]
 def _start(self,kind):
  self._idle()
  if self.plant is None:raise ActionRejected('PLANT_REQUIRED')
  if self.sample is None or self.method is None:raise ActionRejected('SAMPLE_METHOD_REQUIRED')
  if not self._ready() or self.opened:raise ActionRejected('OPTICS_NOT_READY')
  if (kind=='blank')!=(self.sample['kind']=='blank'):raise ActionRejected('WRONG_SAMPLE_KIND')
  if kind=='sample' and not self._blank_valid():raise ActionRejected('MATCHED_BLANK_REQUIRED')
  grid=self._validate_method(self.method);self.kind=kind;provenance=self._prepare_response();m=self.method
  segments=[];current=np.array([self.excitation,self.emission]);axis=1 if m['scan_axis']=='emission' else 0
  def position(w):return np.array([m['excitation_m'],w]) if axis==1 else np.array([w,m['emission_m']])
  def segment(stage,target,duration,index):
   nonlocal current
   if duration>0:segments.append(dict(stage=stage,start=current.tolist(),end=target.tolist(),duration=duration,index=index))
   current=target.copy()
  first=position(grid[0]);segment('moving',first,float(np.max(np.abs(first-current)))/self.plant['mono_reposition_speed_m_s'],0)
  for i,w in enumerate(grid):
   target=position(w)
   if i and m['scan_mode']=='continuous':segment('continuous',target,float(abs(w-grid[i-1]))/m['scan_speed_m_s'],i)
   else:
    if i:segment('moving',target,float(np.max(np.abs(target-current)))/self.plant['mono_reposition_speed_m_s'],i)
    segment('integrating',target,m['response_s'],i)
  if math.fsum(x['duration'] for x in segments)>86400:raise ActionRejected('EVENT_RESOURCE_LIMIT')
  self._run_method=copy.deepcopy(m);self._run_provenance=provenance;self._run_fingerprint=copy.deepcopy(self._fingerprint());self._grid=grid;self._points=[];self.elapsed_s=0.;self.warning='';self.phase='running';self._last_sample_time=None;self._segments=segments;self._op_count+=1;self.operation_id=self.instance_id+':op:'+str(self._op_count);self._background=copy.deepcopy(self._blank['result']['points']) if kind=='sample' else None
  if kind=='blank':self._blank=None
  self._next_segment();return dict(phase=self.phase,operation_id=self.operation_id)
 def _next_segment(self):
  if self._segments:
   self._segment=self._segments.pop(0);self._segment_elapsed=0.;self.stage=self._segment['stage'];self._due=self.time_s+self._segment['duration'];self.shutter=self.stage!='moving'
  else:
   self.phase='succeeded';self.stage='idle';self.shutter=False;self._segment=None;self._due=None
   if self.kind=='blank':self._blank=dict(fingerprint=copy.deepcopy(self._run_fingerprint),result=self._result())
 def _stop(self,why):
  if self.phase=='running':self.phase='cancelled';self.stage='idle';self.warning=why;self.shutter=False;self._due=None;self._segment=None;self._segments=[]
 def _advance(self,dt):
  if self.powered and self.lamp:self.lamp_age+=dt
  if self.phase=='running' and self._segment:
   self._segment_elapsed=min(self._segment['duration'],self._segment_elapsed+dt);f=self._segment_elapsed/self._segment['duration'];pos=(1-f)*np.array(self._segment['start'])+f*np.array(self._segment['end']);self.excitation,self.emission=map(float,pos);self.elapsed_s+=dt
  self.time_s+=dt
 def _gate(self):
  s=self._segment;m=self._run_method;p=self.plant;dt=m['response_s'];end=np.array(s['end']);begin=end.copy()
  if s['stage']=='continuous':begin=end+(np.array(s['start'])-end)*(dt/s['duration'])
  mid=(begin+end)/2;axis=1 if m['scan_axis']=='emission' else 0;window=[float(begin[axis]),float(end[axis])]
  signal=0.;g=0.
  if self.kind=='sample' and self.sample['kind']=='measured_cpl':signal,g=self._measured_signal(float(begin[1]),float(end[1]))
  elif self.kind=='sample':signal=self.sample['peak_pl_relative']*_average_curve(*self._ex_curve,float(begin[0]),float(end[0]))*_average_curve(*self._em_curve,float(begin[1]),float(end[1]));g=self.sample['synthetic_glum']
  r=p['counts_per_relative_unit_s']
  expected=np.array([signal*r*dt*(.5+.25*g),signal*r*dt*(.5-.25*g),p['blank_pl_relative']*r*dt*(.5+.25*p['blank_glum']),p['blank_pl_relative']*r*dt*(.5-.25*p['blank_glum']),p['dark_counts_s']*dt,p['dark_counts_s']*dt])
  if not np.all(np.isfinite(expected)) or np.max(expected)>1e15:raise ActionRejected('EVENT_RATE_LIMIT')
  counts=self._rng.poisson(expected).astype(float) if p['poisson_noise'] else expected
  left=float(np.sum(counts[[0,2,4]]));right=float(np.sum(counts[[1,3,5]]));self._events+=counts;self._detected+=left+right;self._gate_s+=dt
  background=self._background[s['index']] if self._background is not None else None
  net_left=left-(background['raw_left_counts']/background['integration_s']*dt if background else 0.);net_right=right-(background['raw_right_counts']/background['integration_s']*dt if background else 0.)
  il=net_left/(r*dt);ir=net_right/(r*dt);result=_glum(il,ir,net_left+net_right,p['minimum_net_counts']);point=dict(requested_wavelength_m=float(self._grid[s['index']]),excitation_m=float(mid[0]),emission_m=float(mid[1]),wavelength_window_m=window,left_relative=il,right_relative=ir,pl=result['pl'],cpl=result['cpl'],glum=result['glum'],glum_quality=result['quality'],raw_left_counts=left,raw_right_counts=right,net_counts=net_left+net_right,integration_s=dt,sample_time_s=self.time_s,sample_window_s=[self.time_s-dt,self.time_s]);validate(POINT,point);self._points.append(point);self._last_sample_time=self.time_s
 def step(self,dt_s):
  validate(number('s',0,86400),dt_s)
  if not dt_s:return
  target=self.time_s+dt_s
  if not math.isfinite(target):raise ActionRejected('INVALID_ARGUMENT')
  while self.phase=='running' and self._due<=target+4*math.ulp(target):
   event=self._due;target=max(target,event);self._advance(max(0.,event-self.time_s));self.time_s=event;self.excitation,self.emission=self._segment['end']
   if self.stage!='moving':self._gate()
   self._next_segment()
  self._advance(max(0.,target-self.time_s));self.time_s=target;self.revision+=1;self._archive_operation()
 def _operation_snapshot(self):return dict(protocol_version=PROTOCOL_VERSION,instance_id=self.instance_id,operation_id=self.operation_id,status=self.phase,state_revision=self.revision,clock={'domain':'simulation','time_s':self.time_s},result=self._result(),observations=self.session().read(instance_id=self.instance_id)['observations'],error=None)
 def _archive_operation(self):
  if self.operation_id and self.phase in ['succeeded','cancelled','failed'] and self.operation_id not in self._operation_records:self._operation_records[self.operation_id]=copy.deepcopy(self._operation_snapshot())


class JascoCpl300Session:
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
        m=export_jasco_cpl300_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.1',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        def timing(key):
            if key in ['scan.pl','scan.cpl','scan.glum'] and h._points:return h._points[-1]['sample_time_s'],h._points[-1]['sample_window_s']
            if key=='scan.last_sample_time_s' and h._last_sample_time is not None:return h._last_sample_time,None
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
'Documented body2000x700x1000mm and205kg uses the selected Japanese specification;2018 catalog180kg is a recorded source conflict. Internal geometry, prism carrier±0.35rad and cover/shutter strokes are engineering proxies.',
'Excitation and emission each use double-prism monochromators and a shutter. Two physical shutters share one process state here; no independent OEM timing or optical ray tracing. Ex250–850nm is a conservative engineering bound, not an independently documented calibration range.',
'Standard detection250–850nm; extended PMT400–1100nm requires trusted installation. Xe150W is nominal lamp electrical power, never sample irradiance or optical flux.',
'0.025nm wavelength resolution is not spectral bandpass. Explicit Gaussian bandwidths only add variance to documented source bandwidth and cannot recover finer detail. Unknown source width remains unknown; nearest continuation is a smoothing boundary assumption, not exported source extrapolation.',
'DIT0.1ms–30s is modeled as a rectangular integration gate, not a calibrated hardware response time constant. Scan speed<=10000nm/min; Auto DIT adaptation is unsupported.',
'Step scans move finite distance, then integrate at fixed wavelength. Continuous scans keep finite velocity and report a trailing DIT window ending at each grid target; non-overlapping gates require DIT<=the shortest spatial interval/scan speed. First continuous point is stationary for one DIT; effective wavelength is the gate midpoint, not an OEM timestamp convention.',
'Real normalized PL and absorption shapes come from PhotochemCAD; PL is not calibrated watts. Peak amplitude and normalized-absorption excitation factor are explicit assumptions. Negative source baseline points are preserved in queries but clipped only to form nonnegative simulated event rates.',
'Excitation dependence uses an explicitly accepted Kasha-like absorption-shape proxy, not a measured excitation action spectrum. No absorption/quantum-yield based absolute flux, inner-filter effect, saturation, anti-Stokes emission, reabsorption or radiative-energy closure.',
'Every PhotochemCAD PL template lacks measured CPL/glum. For kind=synthetic_cpl the host glum defines IL=PL*(1/2+g/4), IR=PL*(1/2-g/4), with|g|<=2. It is not inferred from absorption, ECD, VCD, molecular identity or configuration.',
'kind=measured_cpl loads one of 57 literature g_lum emitters (signed per enantiomer, solvent/excitation-matched). Camphorquinone replays real source-data PL and g_lum curves; the other 56 are scalar g_lum values valid only inside their reported wavelength/transition windows with host-flat PL amplitude. Values were measured on the cited (mostly non-JASCO) instruments; 39 sign points are derived from explicit mirror/ECD-sign statements and transition-window wavelengths for some Eu/Sm records are typical manifold regions.',
'Six real CPL/glum reference configurations belong to one WQD/liquid-crystal material system, not six new substances. Raw CPL display mdeg and its radians conversion are not DeltaI; unknown DC/normalization prevents reconstruction of joint PL/IL/IR. References remain query-only and retain source conditions.',
'Public total PL=IL+IR, CPL=IL−IR, glum=2*CPL/PL. Manufacturer IA denotes approximately half the sum; treating IA as total PL would introduce a factor-of-two error. Source formula layout conflicts are recorded separately.',
'Independent signal/background/dark equivalent Poisson detector events are a host assumption, not actual analog PMT/lock-in/ADC physics or factory noise calibration. poisson_noise=false returns deterministic expected events. Stray-light<=0.001% is a conditional specification, not used as a universal background or detection floor.',
'Observed blank is acquired with identical method and solvent. After subtraction a channel or total can be negative; values are preserved and glum is null for low net counts, nonpositive total or negative corrected channels. Ratios are not silently clipped to fabricate physical evidence.',
'Completed integration gates only contribute detector-event partition and gate-time ledgers; partial cancelled gates have no fabricated final readout. This is not continuous open-shutter exposure, incident/emitted photon conservation or total instrument energy closure.',
'Complete prefilled-cell volume and solute inventory are conserved through host loading/unloading. Concentration is inventory only; unknown source concentration/temperature remain null and require explicit acknowledgment. No chemical products, bleaching or sample consumption.',
'Pure reads/exports never sample noise or advance time. Results and owned terminal polls retain complete spectra, original method and sample windows across later runs; cancellation of old terminal operations never stops a new one. Permissions/cache are process-local, not restart persistence or OS security.',
'External manual cover/sample authority, ideal shutter logic and lamp warmup are not certified hardware safety. Real PMT gain/polarization calibration, OEM noise/covariance, Auto DIT, external DC analog measurements, robot contact, hardware/HIL and absolute-configuration assignment remain not_run.'
]
def export_jasco_cpl300_contract_metadata():
 caps={};slots={};observed_points=['scan.completed_points','scan.progress','scan.pl','scan.cpl','scan.glum','scan.glum_quality','scan.last_sample_time_s'];stop=['scan.phase','scan.stage','shutter.opened','shutter.angle_rad','safety.warning'];movement=['optics.excitation_m','optics.emission_m','excitation.angle_rad','emission.angle_rad']
 immediate={
 'plant.configure':['plant.configured'],'sample.load':['sample.loaded','blank.valid','truth.ledger'],'sample.unload':['sample.loaded','blank.valid','truth.ledger'],
 'cover.set':['cover.opened','cover.lift_m',*stop],'power.set':['power.enabled','lamp.enabled','lamp.ready','blank.valid',*stop],
 'lamp.set':['lamp.enabled','lamp.ready','blank.valid',*stop],'shutter.set':stop,
 'method.configure':['detector.band','method.response_s','method.scan_mode','blank.valid'],
 'blank.start':['scan.phase','scan.stage','scan.elapsed_s',*observed_points,'shutter.opened','shutter.angle_rad','blank.valid','safety.warning'],
 'acquisition.start':['scan.phase','scan.stage','scan.elapsed_s',*observed_points,'shutter.opened','shutter.angle_rad','safety.warning'],
 'acquisition.stop':stop}
 onstep={'lamp.set':['lamp.ready'],'power.set':['lamp.ready']}
 for key in ['blank.start','acquisition.start']:onstep[key]=['scan.phase','scan.stage','scan.elapsed_s',*observed_points,*movement,'shutter.opened','shutter.angle_rad','truth.ledger']
 completion={'blank.start':['scan.phase','scan.stage','blank.valid','shutter.opened','shutter.angle_rad'],'acquisition.start':['scan.phase','scan.stage','shutter.opened','shutter.angle_rad']}
 observes={'plant.configure':['plant.configured'],'sample.load':['sample.loaded'],'sample.unload':['sample.loaded'],'cover.set':['cover.opened'],'power.set':['power.enabled'],'lamp.set':['lamp.enabled'],'shutter.set':['shutter.opened'],'blank.start':['scan.phase'],'acquisition.start':['scan.phase'],'acquisition.stop':['scan.phase'],'process.measure':['scan.phase','scan.stage','scan.elapsed_s',*observed_points,'safety.warning'],'result.read':['scan.phase','scan.stage',*observed_points],'result.export':['scan.phase','scan.stage',*observed_points],'raw.read':['scan.last_sample_time_s'],'blank.read':['blank.valid'],'product.read':['truth.ledger']}
 for k,(s,u,o,v,c,w) in SLOTS.items():
  mean=k in ['scan.pl','scan.cpl','scan.glum'];slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'JascoCpl300Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='interval_mean' if mean else 'instant',time_domain='simulation',window_description='最近完整DIT积分门；连续扫描给出门内平均波长；glum为同门扣空白左右积分量之比，非逐瞬时比值平均' if mean else None),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','INVALID_REFERENCE','ZERO_REFERENCE','INITIALIZATION_ONLY','EVIDENCE_ACK_REQUIRED','SYNTHETIC_CPL_ACK_REQUIRED','UNKNOWN_CONDITIONS_ACK_REQUIRED','KASHA_ACK_REQUIRED','SOURCE_CONDITIONS_MISMATCH','SOURCE_BANDWIDTH_LIMIT','SOURCE_WAVELENGTH_RANGE','BUSY','PLANT_REQUIRED','SAMPLE_REQUIRED','SAMPLE_METHOD_REQUIRED','COVER_MUST_BE_OPEN','UNLOAD_REQUIRED','SAMPLE_COMPOSITION','POWER_PLANT_REQUIRED','OPTICS_NOT_READY','OPTION_NOT_INSTALLED','DETECTOR_WINDOW','EXCITATION_WINDOW','STOKES_MODEL_REQUIRED','SPECTRAL_GRID','RESPONSE_OVERLAPS_INTERVAL','EVENT_RESOURCE_LIMIT','EVENT_RATE_LIMIT','WRONG_SAMPLE_KIND','MATCHED_BLANK_REQUIRED','ANALYSIS_RANGE','REFERENCE_WAVELENGTH_RANGE','REFERENCE_AXIS_UNSUPPORTED']
 for k,(inp,out,c,z,kind,vis,ex,_) in CAPS.items():
  async_=k in ['blank.start','acquisition.start'];query=kind in ['query','analysis'];unsupported=k in UNSUPPORTED
  effects=[dict(state_id=state,timing=timing,description_zh={'immediate':'经检查的设定、外部事件、扫描初始化或安全取消；不推进时间、不补造计数','on_step':'宿主step完成暖机、有限单色器运动或DIT门检测事件；只完成门贡献账本和观测','on_completion':'指定所有扫描点完成后停机并共同关闭快门；完整空白此时才可用'}[timing]) for timing,mapping in [('immediate',immediate),('on_step',onstep),('on_completion',completion)] for state in mapping.get(k,[])]
  caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.jasco_cpl300.JascoCpl300Session.invoke',invocation=f'session.invoke(instance_id="cpl300_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if query or unsupported else [dict(description_zh='运行层检查宿主权限、灯/盖/样品、来源条件、检测波段、积分设置及相同方法空白，失败原子拒绝',state_ids=['power.enabled','lamp.ready','cover.opened','blank.valid','scan.phase'],enforced_by='runtime',check='Runtime._invoke and host-assigned principal Session')],state_effects=[] if query or unsupported else effects,observes=[] if unsupported else observes.get(k,[]),model_effects=dict(mode='none',description_zh='独立场景绑定另验；查询无动作',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='有限双单色器运动后所有方法网格点完整积分门结束' if async_ else '返回事务，不推进时钟',completion_state_ids=['scan.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','dual_monochromators','gated_equivalent_detector','request_cache'],partial_failure_zh='取消/开盖/关灯保留完成点与其门内账本，未完成门不伪造结果；完整历史结果/时间窗/方法按原身份保护，取消旧操作不影响新操作'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=LIMITATIONS,source_ids=['manufacturer_primary','manufacturer_formula','simulation_assumption'])
 mech=dict(component_ids=['xe_lamp','excitation_prism_carrier','emission_prism_carrier','excitation_shutter','emission_shutter','sample_cell','pmt_detector'],state_ids=list(slots),meaning_zh='双棱镜有限扫描、明确积分门的左右发光等效检测、PL/CPL/glum与独立真实CPL参考查询',implementation='lumped',equations=[
 'PL=I_L+I_R; CPL=I_L-I_R; g_lum=2*CPL/PL. Manufacturer I_A≈(I_L+I_R)/2 is not total PL.',
 'Assumed signal PL(lambda_ex,lambda_em)=host_peak*normalized_positive_absorption(lambda_ex)*normalized_positive_emission(lambda_em); no absolute molar fluorescence efficiency inferred.',
 'Signal means mu_L/R=PL*host_counts_per_relative_unit_s*DIT*(1/2±host_glum/4), with |host_glum|<=2; independent background/dark equivalent events add before observation.',
 'measured_cpl: PL=host_peak*[1 inside scalar validity window | gate-average of bandwidth-convolved source PL curve]; g=literature scalar in window | gate-average(conv(PL*g))/gate-average(conv(PL)); no excitation factor.',
 'Observed I_L/R=(sample_gate_events-blank_event_rate*DIT)/(host_counts_per_relative_unit_s*DIT). PL/CPL preserve signed background-corrected estimates.',
 'g_lum is null if total<=0, net_counts<host_minimum, or either corrected channel<0; otherwise the stable normalized ratio lies in[-2,2].',
 'Step-mode prisms move at explicit finite speed then hold for DIT. Continuous mode integrates the trailing DIT wavelength interval by piecewise-linear source-knot quadrature, reports its midpoint, and requires non-overlapping gates.',
 'Additional Gaussian bandwidth=sqrt(max(0,target_bandwidth^2-source_bandwidth^2)); source baseline/conditions preserved in queries.',
 'Registered_volume=current_volume+removed_volume; registered_solute=current_volume*concentration+removed_solute; no consumption.',
 'Completed-gate detected_events=sum(left/right signal+background+dark equivalent events); no optical-energy or continuous-exposure interpretation.'
 ],parameters={},source_ids=['manufacturer_primary','manufacturer_formula','pl_dataset','cpl_dataset','cpl_literature','simulation_assumption'],limitations=LIMITATIONS)
 spec=importlib.util.spec_from_file_location('_cpl300_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
 ann.annotate_export(SLUG,caps,slots,{'circularly_polarized_luminescence':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'；非原厂校准。'
 mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'circularly_polarized_luminescence':mech})

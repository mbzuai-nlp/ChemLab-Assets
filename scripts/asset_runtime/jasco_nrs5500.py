"""JASCO NRS-5500: measured Raman reference shapes, explicitly uncalibrated response and spatial phantom."""
from __future__ import annotations
import copy,hashlib,importlib.util,json,math
from pathlib import Path
import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks
from functools import lru_cache
ROOT=Path(__file__).resolve().parents[2];SLUG='jasco_nrs5500';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-073';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/jasco_nrs5500_raman_library/templates.json'
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
POS=arr(number('m',-.05,.05),3)
PLANT=obj({'autostage_installed':BOOL,'stage_speed_m_s':number('m/s',1e-6,.1),'psf_sigma_xy_m':number('m',1e-7,.001),'focus_sigma_z_m':number('m',1e-7,.001),'reference_rate_s_inv':number('1/s',1,1e7),'reference_power_w':number('W',1e-6,.05),'additional_fwhm_m_inv':number('1/m',0,2000),'axis_bias_m_inv':number('1/m',-1000,1000),'dark_rate_s_inv':number('1/s',0,10000),'read_noise_counts':number('1',0,1000),'shot_noise':BOOL,'seed':integer(0,2147483647),'accept_uncalibrated_model':BOOL})
REGION=obj({'template_id':TEXT,'x_min_m':number('m',-.05,.05),'x_max_m':number('m',-.05,.05),'y_min_m':number('m',-.05,.05),'y_max_m':number('m',-.05,.05),'response_scale':number('1',.001,100)})
REGIONS=dict(arr(REGION),minItems=1,maxItems=16)
SAMPLE=obj({'sample_id':TEXT,'mass_kg':number('kg',1e-9,1),'thickness_m':number('m',1e-6,.08),'focus_z_m':number('m',-.015,.015),'regions':REGIONS,'accept_synthetic_spatial_phantom':BOOL})
METHOD=obj({'laser_wavelength_m':{'type':'number','enum':[532e-9],'x-unit':'m'},'power_w':number('W',1e-6,.05),'grating_lines_m_inv':{'type':'number','enum':[1800000],'x-unit':'1/m'},'integration_s':number('s',.001,100),'start_m_inv':number('1/m',5000,800000),'stop_m_inv':number('1/m',5000,800000)})
PLANT_EXAMPLE=dict(autostage_installed=False,stage_speed_m_s=.001,psf_sigma_xy_m=2e-6,focus_sigma_z_m=3e-6,reference_rate_s_inv=10000.,reference_power_w=.01,additional_fwhm_m_inv=0.,axis_bias_m_inv=0.,dark_rate_s_inv=0.,read_noise_counts=0.,shot_noise=False,seed=73,accept_uncalibrated_model=True)
AUTO_PLANT=dict(PLANT_EXAMPLE,autostage_installed=True)
FAST_PLANT=dict(AUTO_PLANT)
SAMPLE_EXAMPLE=dict(sample_id='mineral_01',mass_kg=.001,thickness_m=.002,focus_z_m=0.,regions=[dict(template_id='rruff_r040031',x_min_m=-.005,x_max_m=.005,y_min_m=-.005,y_max_m=.005,response_scale=1.)],accept_synthetic_spatial_phantom=True)
MAP_SAMPLE=dict(SAMPLE_EXAMPLE,sample_id='two_phase',regions=[dict(SAMPLE_EXAMPLE['regions'][0],x_max_m=0.),dict(SAMPLE_EXAMPLE['regions'][0],template_id='rruff_r040070',x_min_m=0.)])
METHOD_EXAMPLE=dict(laser_wavelength_m=532e-9,power_w=.01,grating_lines_m_inv=1800000,integration_s=.02,start_m_inv=25000.,stop_m_inv=120000.)
MAPPING_EXAMPLE=dict(positions_m=[[-20e-6,0.,0.],[0.,0.,0.],[20e-6,0.,0.]])
SPECTRUM=obj({'position_m':POS,'raman_shift_m_inv':arr(number('1/m')),'counts':arr(number('1')),'count_rate_s_inv':arr(number('1/s')),'sample_time_s':number('s',0),'integration_s':number('s',0),'exposures':integer(1,8),'evidence':TEXT})
WINDOW=obj({**SPECTRUM['properties'],'point_index':integer(0,255),'pixel_start':integer(0,8000)})
MEASURE=obj({'phase':PHASE,'stage':TEXT,'completed_points':integer(0,256),'completed_windows':integer(0,2048),'elapsed_s':number('s',0),'maximum_rate_s_inv':nullable(number('1/s')),'warning':TEXT})
RESULT=obj({'phase':PHASE,'kind':TEXT,'complete':BOOL,'spectra':arr(SPECTRUM),'windows':arr(WINDOW),'settings_json':TEXT,'template_source':TEXT,'evidence':TEXT,'limitations':arr(TEXT)})
LEDGER=obj({'registered_mass_kg':number('kg',0),'attached_mass_kg':number('kg',0),'external_mass_kg':number('kg',0),'mass_residual_kg':number('kg'),'laser_on_s':number('s',0),'incident_energy_j':number('J',0),'active_sample_id':nullable(TEXT),'active_template_ids':arr(TEXT),'evidence':TEXT})
OBS=obj({'raman_shift_m_inv':dict(arr(number('1/m')),minItems=3,maxItems=8000),'count_rate_s_inv':dict(arr(number('1/s')),minItems=3,maxItems=8000)})
TEMPLATE=obj({'substance_id':TEXT,'name':TEXT,'raman_shift_m_inv':arr(number('1/m')),'normalized_shape':arr(number('1',0,1)),'source_json':TEXT,'evidence':TEXT})
CAPS={}
def cap(k,inp,out,c,z,kind='device_command',vis='agent',ex=None):CAPS[k]=[inp,out,c,z,kind,vis,{} if ex is None else ex,[]]
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body','detector'],'宿主一次性显式声明可选XYZ自动台/未标定传递、PSF、附加线宽和噪声；默认手动台','configuration','evaluator',PLANT_EXAMPLE)
cap('sample.register',SAMPLE,obj({'registered':BOOL}),['sample_holder'],'登记固体矿物样本质量/高度与独立合成空间分布，不是实测地图','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.load',obj({'sample_id':TEXT}),obj({'loaded':BOOL,'attachment_event':TEXT}),['sample_holder','sample_marker'],'开门逻辑装样；无机器人接触','device_command','evaluator',{'sample_id':'mineral_01'})
cap('sample.unload',obj({'recipient_id':TEXT}),obj({'unloaded':BOOL,'attachment_event':TEXT}),['sample_holder','sample_marker'],'开门完整卸样，质量不消耗','device_command','evaluator',{'recipient_id':'external_rack'})
cap('method.configure',METHOD,obj({'configured':BOOL}),['laser_532','grating','detector'],'标准532nm/50mW上限/1800gr/mm；1cm^-1/pixel采样，不是FWHM；积分上下限为宿主限制','configuration','agent',METHOD_EXAMPLE)
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_face','power_inlet'],'软件供电；断电取消并失效轴校准',ex={'enabled':True})
cap('door.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['sample_door'],'电动门逻辑状态与工程旋转代理；开门取消和撤销激光许可',ex={'opened':False})
cap('laser.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['laser_532','shutter'],'关门激光许可；只在积分阶段发光，移动与空闲闭快门',ex={'enabled':True})
cap('stage.manual_position',obj({'position_m':POS}),obj({'position_m':POS}),['stage_x','stage_y','stage_z'],'宿主已完成外部手动调节；不是自主执行器，开门无激光','device_command','evaluator',{'position_m':[.001,-.001,0.]})
cap('stage.move',obj({'position_m':POS}),obj({'phase':PHASE,'operation_id':TEXT}),['stage_x','stage_y','stage_z'],'仅宿主声明安装自动台后可用；由step有限速度推进',ex={'position_m':[20e-6,0.,0.]})
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['objective','notch_filter','grating','detector'],'当前位置采谱；CCD每窗最多1024pixel，宽带顺序拼窗')
cap('mapping.start',obj({'positions_m':dict(arr(POS),minItems=1,maxItems=256)}),obj({'phase':PHASE,'operation_id':TEXT}),['stage_x','stage_y','stage_z','detector'],'仅选配自动台；逐点移动、稳定后顺序完整积分，不在运动中虚构观测',ex=MAPPING_EXAMPLE)
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['shutter'],'取消台移动或采集，保留已完成窗/点与实际能量')
cap('process.measure',obj({}),MEASURE,['host_screen'],'只读已完成观测摘要，无观测时null','query')
cap('result.read',obj({}),RESULT,['host_screen'],'返回带实测模板出处的模拟计数谱和位置；不泄漏当前私有模板身份','query')
cap('spectrum.peaks',obj({**OBS['properties'],'minimum_prominence_s_inv':number('1/s',0)}),obj({'peak_shift_m_inv':arr(number('1/m')),'peak_rate_s_inv':arr(number('1/s')),'evidence':TEXT}),['host_screen'],'调用者观测谱的局部峰位置，不作隐含物质识别','analysis','agent',{'raman_shift_m_inv':[40000.,40100.,40200.],'count_rate_s_inv':[0.,2.,0.],'minimum_prominence_s_inv':.5})
cap('map.band',obj({'spectra':dict(arr(SPECTRUM),minItems=1,maxItems=256),'low_m_inv':number('1/m'),'high_m_inv':number('1/m')}),obj({'positions_m':arr(POS),'band_integrals_s_inv_m_inv':arr(number('1/(s*m)')),'evidence':TEXT}),['host_screen'],'调用者观测谱线性插值边界再梯形积分；强度图不是浓度/相含量图','analysis','agent',{'spectra':[dict(position_m=[0.,0.,0.],raman_shift_m_inv=[40000.,40100.,40200.],counts=[0.,2.,0.],count_rate_s_inv=[0.,2.,0.],sample_time_s=1.,integration_s=1.,exposures=1,evidence='caller observation')],'low_m_inv':40000.,'high_m_inv':40200.})
cap('calibration.axis',obj({'observed_peak_m_inv':number('1/m',5000,800000),'certified_peak_m_inv':number('1/m',5000,800000),'accept_external_reference':BOOL}),obj({'axis_correction_m_inv':number('1/m')}),['neon_lamp','detector'],'调用者观测峰和外部参考构成单点偏移；非原厂Ne标定或多项式校准',ex={'observed_peak_m_inv':46600.,'certified_peak_m_inv':46400.,'accept_external_reference':True})
cap('template.read',obj({'substance_id':TEXT}),TEMPLATE,['host_screen'],'公开RRUFF参考谱；当前样品身份仅evaluator可见','query','agent',{'substance_id':'rruff_r040031'})
cap('product.read',obj({}),LEDGER,['sample_holder'],'私有样品账本及累计入射能；无吸收/温升守恒主张','query','evaluator')
cap('liquid.validated_measure',obj({}),obj({}),['sample_holder'],'无液体实测模板/容器背景和相容性验证，拒绝伪成功','analysis')
cap('stress.quantify',obj({}),obj({}),['detector'],'无独立应力标定，不能以峰移直接冒充应力','analysis')
cap('autofocus.run',obj({}),obj({}),['objective'],'未实现原厂自动聚焦/SPRIntS/PCA，宿主显式焦面','device_command')
UNSUPPORTED={'liquid.validated_measure','stress.quantify','autofocus.run'}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='host_screen',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','宿主参数已声明','setpoint',c='body')
slot('plant.autostage_installed',BOOL,'1','可选XYZ自动台已明确声明','setpoint',c='stage_x')
slot('method.configured',BOOL,'1','532nm方法已配置','setpoint')
slot('power.enabled',BOOL,'1','软件供电','setpoint',c='power_face',w=('power.set','enabled'))
slot('door.opened',BOOL,'1','电动门开闭逻辑','scene',c='sample_door',w=('door.set','opened'))
slot('door.angle_rad',number('rad',0,math.radians(110)),'rad','绕Z工程旋转代理；非原厂门轨迹','scene',c='sample_door')
slot('laser.enabled',BOOL,'1','激光采集许可','setpoint',c='laser_532',w=('laser.set','enabled'))
slot('laser.emitting',BOOL,'1','积分门控正在发光','derived',c='shutter')
slot('sample.loaded',BOOL,'1','样品逻辑挂接','scene',c='sample_marker')
for i,ax in enumerate('xyz'):slot('stage.'+ax+'_m',number('m',-.05,.05),'m','实际载物台'+ax+'位移，过程唯一权威','scene',c='stage_'+ax)
slot('stage.target_m',POS,'m','载物台目标；样品光斑坐标=-台XY','setpoint',c='stage_z')
slot('scan.phase',PHASE,'1','移动/采谱生命周期')
slot('scan.stage',dict(type='string',enum=['idle','moving','integrating']),'1','有限台移动或完整CCD窗积分')
slot('scan.elapsed_s',number('s',0),'s','本次操作耗时')
slot('scan.completed_points',integer(0,256),'1','已完成全波段谱数量')
slot('scan.completed_windows',integer(0,2048),'1','已完成CCD窗数量')
slot('scan.maximum_rate_s_inv',nullable(number('1/s')),'1/s','最近完整光谱的最大观测计数率')
slot('scan.last_sample_time_s',nullable(number('s')),'s','最近完整光谱的最后窗观测时间')
slot('method.power_w',nullable(number('W')),'W','声明样品处功率，未验证实机传输损耗','setpoint',c='laser_532')
slot('method.integration_s',nullable(number('s')),'s','每CCD窗积分时间','setpoint',c='detector')
slot('method.wavelength_m',nullable(number('m')),'m','标准激光532nm','setpoint',c='laser_532')
slot('calibration.axis_correction_m_inv',number('1/m'),'1/m','调用者观测导出的单点偏移','setpoint',c='neon_lamp')
slot('safety.warning',TEXT,'1','不含私有物质身份的告警')
slot('truth.ledger',LEDGER,'1','质量/入射能/样品与模板身份私有账本','process_truth','evaluator','sample_holder')
@lru_cache(maxsize=1)
def _templates():return {r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']}

class JascoNrs5500Runtime:
 def __init__(self,instance_id='nrs5500_01'):
  self.instance_id=instance_id;self.templates=_templates();self.time_s=0.;self.revision=0;self.plant=None;self.method=None;self.samples={};self.active=None
  self.powered=False;self.opened=True;self.laser=False;self.position=np.zeros(3);self.target=np.zeros(3);self.axis_correction=0.
  self.phase='idle';self.stage='idle';self.kind='';self.elapsed_s=0.;self.points=[];self.windows=[];self.warning='';self.operation_id='';self._op_count=0
  self._positions=[];self._point_index=0;self._window_index=0;self._grid=np.zeros(0);self._remaining=0.;self._partial=[];self._laser_on_s=0.;self._incident_energy_j=0.
  self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._operation_records={};self._result_method=None;self._rng=np.random.default_rng(0)
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return JascoNrs5500Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def debug_snapshot(self):
  return copy.deepcopy({k:(v.tolist() if isinstance(v,np.ndarray) else v) for k,v in self.__dict__.items() if k not in ['templates','_rng']})|{'_rng_state':copy.deepcopy(self._rng.bit_generator.state)}
 def _sample(self):return self.samples.get(self.active)
 def _ledger(self):
  mass=sum(s['mass_kg'] for s in self.samples.values());s=self._sample();inside=s['mass_kg'] if s else 0.
  return dict(registered_mass_kg=mass,attached_mass_kg=inside,external_mass_kg=mass-inside,mass_residual_kg=mass-inside-(mass-inside),laser_on_s=self._laser_on_s,incident_energy_j=self._incident_energy_j,active_sample_id=self.active,active_template_ids=[r['template_id'] for r in s['regions']] if s else [],evidence='intact logical solid sample; incident energy integral only, not absorbed heat or complete electrical balance')
 def read(self,visibility='agent'):
  if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
  m=self.method;p=self.points[-1] if self.points else None
  v={'plant.configured':bool(self.plant),'plant.autostage_installed':bool(self.plant and self.plant['autostage_installed']),'method.configured':bool(m),'power.enabled':self.powered,'door.opened':self.opened,'door.angle_rad':math.radians(110) if self.opened else 0.,'laser.enabled':self.laser,'laser.emitting':self.phase=='running' and self.stage=='integrating','sample.loaded':bool(self.active),'stage.target_m':self.target.tolist(),'scan.phase':self.phase,'scan.stage':self.stage,'scan.elapsed_s':self.elapsed_s,'scan.completed_points':len(self.points),'scan.completed_windows':len(self.windows),'scan.maximum_rate_s_inv':max(p['count_rate_s_inv']) if p else None,'scan.last_sample_time_s':p['sample_time_s'] if p else None,'method.power_w':m['power_w'] if m else None,'method.integration_s':m['integration_s'] if m else None,'method.wavelength_m':m['laser_wavelength_m'] if m else None,'calibration.axis_correction_m_inv':self.axis_correction,'safety.warning':self.warning}
  v.update({'stage.'+ax+'_m':float(self.position[i]) for i,ax in enumerate('xyz')})
  if visibility!='agent':v['truth.ledger']=self._ledger()
  return copy.deepcopy(v)
 def invoke(self,capability_id,**arguments):
  if capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability_id][0],arguments)
  if capability_id in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  if CAPS[capability_id][4] in ['query','analysis']:return copy.deepcopy(self._invoke(capability_id,arguments))
  snapshot={k:copy.deepcopy(v) for k,v in self.__dict__.items() if k not in ['templates']}
  try:
   out=self._invoke(capability_id,arguments);validate(CAPS[capability_id][1],out);self.revision+=1;return copy.deepcopy(out)
  except Exception:
   templates=self.templates;self.__dict__.clear();self.__dict__.update(snapshot);self.templates=templates;raise
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _check_position(self,p):
  if not self.plant:raise ActionRejected('PLANT_REQUIRED')
  bounds=np.array([.05,.035,.015]) if self.plant['autostage_installed'] else np.array([.0375,.025,.015])
  if np.any(np.abs(p)>bounds+1e-14):raise ActionRejected('STAGE_RANGE')
 def _archive_operation(self):
  if self.operation_id and self.phase in ('succeeded','cancelled','failed') and self.operation_id not in self._operation_records:
   self._operation_records[self.operation_id]=dict(operation_id=self.operation_id,status=self.phase,result=copy.deepcopy(self._invoke('process.measure',{})))
 def _stop(self,phase='cancelled',warning=''):
  if self.phase=='running':self.phase=phase
  self.stage='idle';self.warning=warning;self.target=self.position.copy();self._archive_operation()
 def _optics(self):
  if not self.plant or not self.method or not self.active:raise ActionRejected('SAMPLE_METHOD_REQUIRED')
  if not self.powered or self.opened or not self.laser:raise ActionRejected('OPTICS_NOT_READY')
 def _prepare_spectrum(self):
  m=self.method
  n=int(round((m['stop_m_inv']-m['start_m_inv'])/100))+1
  self._grid=m['start_m_inv']+100*np.arange(n)
  for r in self._sample()['regions']:
   t=self.templates[r['template_id']];lo,hi=t['coverage_m_inv']
   if self._grid[0]<lo or self._grid[-1]>hi:raise ActionRejected('SOURCE_COVERAGE')
 def _new_operation(self,kind,positions):
  self._archive_operation();self._result_method=copy.deepcopy(self.method)
  self.kind=kind;self.phase='running';self.stage='moving';self.elapsed_s=0.;self.points=[];self.windows=[];self._partial=[];self._point_index=0;self._window_index=0;self._positions=copy.deepcopy(positions);self.target=np.array(positions[0],dtype=float)
  self.warning='';self._op_count+=1;self.operation_id=self.instance_id+'_op_'+str(self._op_count)
  return dict(phase=self.phase,operation_id=self.operation_id)
 @staticmethod
 def _observation(a):
  x=np.array(a['raman_shift_m_inv']);y=np.array(a['count_rate_s_inv'])
  if x.shape!=y.shape or len(x)<3 or not np.isfinite(x).all() or not np.isfinite(y).all() or np.any(x[1:]<=x[:-1]):raise ActionRejected('ANALYSIS_INPUT')
  return x,y
 def _invoke(self,k,a):
  if k=='plant.configure':
   if self.plant or self.powered or self.active:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_uncalibrated_model']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   self.plant=copy.deepcopy(a);self._rng=np.random.default_rng(a['seed']);return {'configured':True}
  if k=='sample.register':
   self._idle()
   if not self.plant:raise ActionRejected('PLANT_REQUIRED')
   if not a['accept_synthetic_spatial_phantom']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   if not a['sample_id'] or a['sample_id'] in self.samples:raise ActionRejected('REQUEST_CONFLICT')
   for i,r in enumerate(a['regions']):
    if r['template_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
    if r['x_min_m']>=r['x_max_m'] or r['y_min_m']>=r['y_max_m']:raise ActionRejected('INVALID_REGION')
    for b in a['regions'][:i]:
     if max(r['x_min_m'],b['x_min_m'])<min(r['x_max_m'],b['x_max_m']) and max(r['y_min_m'],b['y_min_m'])<min(r['y_max_m'],b['y_max_m']):raise ActionRejected('OVERLAPPING_REGIONS')
   self.samples[a['sample_id']]=copy.deepcopy(a);return {'registered':True}
  if k in ['sample.load','sample.unload']:
   self._idle()
   if not self.opened or self.laser:raise ActionRejected('SAMPLE_ACCESS_REQUIRED')
   if k=='sample.load':
    if self.active or a['sample_id'] not in self.samples:raise ActionRejected('UNKNOWN_REFERENCE')
    self.active=a['sample_id'];return dict(loaded=True,attachment_event='host_intact_solid_attached')
   if not self.active or not a['recipient_id']:raise ActionRejected('SAMPLE_ACCESS_REQUIRED')
   self.active=None;return dict(unloaded=True,attachment_event='host_intact_solid_detached')
  if k=='method.configure':
   self._idle()
   span=(a['stop_m_inv']-a['start_m_inv'])/100
   if span<2 or abs(span-round(span))>1e-7:raise ActionRejected('PIXEL_GRID')
   self.method=copy.deepcopy(a);return {'configured':True}
  if k=='power.set':
   if a['enabled'] and not self.plant:raise ActionRejected('PLANT_REQUIRED')
   self.powered=a['enabled']
   if not self.powered:self._stop(warning='POWER_OFF');self.laser=False;self.axis_correction=0.
   return {'enabled':self.powered}
  if k=='door.set':
   self.opened=a['opened']
   if self.opened:self._stop(warning='DOOR_OPEN');self.laser=False
   return {'opened':self.opened}
  if k=='laser.set':
   if a['enabled'] and (not self.powered or self.opened or not self.plant):raise ActionRejected('OPTICS_NOT_READY')
   self.laser=a['enabled']
   if not self.laser and self.kind!='move':self._stop(warning='LASER_DISABLED')
   return {'enabled':self.laser}
  if k=='stage.manual_position':
   self._idle();self._check_position(a['position_m'])
   if not self.opened or self.laser:raise ActionRejected('SAMPLE_ACCESS_REQUIRED')
   self.position=np.array(a['position_m'],float);self.target=self.position.copy();return {'position_m':self.position.tolist()}
  if k=='stage.move':
   self._idle();self._check_position(a['position_m'])
   if not self.plant['autostage_installed']:raise ActionRejected('AUTOSTAGE_NOT_INSTALLED')
   if not self.powered:raise ActionRejected('OPTICS_NOT_READY')
   return self._new_operation('move',[a['position_m']])
  if k in ['acquisition.start','mapping.start']:
   self._idle();self._optics()
   positions=a['positions_m'] if k=='mapping.start' else [self.position.tolist()]
   if k=='mapping.start' and not self.plant['autostage_installed']:raise ActionRejected('AUTOSTAGE_NOT_INSTALLED')
   for p in positions:self._check_position(p)
   self._prepare_spectrum()
   return self._new_operation('mapping' if k=='mapping.start' else 'point',positions)
  if k=='acquisition.stop':self._stop(warning='CANCELLED');return {'phase':self.phase}
  if k=='process.measure':
   s=self.read();return dict(phase=self.phase,stage=self.stage,completed_points=len(self.points),completed_windows=len(self.windows),elapsed_s=self.elapsed_s,maximum_rate_s_inv=s['scan.maximum_rate_s_inv'],warning=self.warning)
  if k=='result.read':
   return dict(phase=self.phase,kind=self.kind,complete=self.phase=='succeeded' and self.kind!='move',spectra=self.points,windows=self.windows,settings_json=json.dumps(self._result_method if self._result_method is not None else self.method,sort_keys=True),template_source=TEMPLATE_REL,evidence='simulated_instrument_response_from_measured_RRUFF_532nm_shapes',limitations=['absolute response, added linewidth, focus and spatial distribution are host assumptions','source widths retained; dispersion is not FWHM','CCD window stitching sequential; no interpolation beyond source coverage','map intensity is not concentration or phase mass fraction','raw CCD simulation is not measured NRS5500 output'])
  if k=='spectrum.peaks':
   x,y=self._observation(a);idx,_=find_peaks(y,prominence=a['minimum_prominence_s_inv'])
   return dict(peak_shift_m_inv=x[idx].tolist(),peak_rate_s_inv=y[idx].tolist(),evidence='local maxima of caller observations; no hidden identity lookup or factory resolution claim')
  if k=='map.band':
   lo=a['low_m_inv'];hi=a['high_m_inv'];values=[];positions=[]
   if lo>=hi:raise ActionRejected('ANALYSIS_INPUT')
   for spectrum in a['spectra']:
    x,y=self._observation(spectrum)
    if lo<x[0] or hi>x[-1]:raise ActionRejected('ANALYSIS_INPUT')
    # Float64 endpoint sums may overflow even when the final band integral
    # is representable. Interpolate and integrate in extended precision first.
    try:
     with np.errstate(over='raise',invalid='raise',divide='raise'):
      wide_x=x.astype(np.longdouble);wide_y=y.astype(np.longdouble)
      def edge(bound):
       j=int(np.searchsorted(wide_x,bound))
       if j==0:return wide_y[0]
       if j==len(wide_x):return wide_y[-1]
       fraction=(np.longdouble(bound)-wide_x[j-1])/(wide_x[j]-wide_x[j-1])
       return wide_y[j-1]*(1-fraction)+wide_y[j]*fraction
      inside=(x>lo)&(x<hi);xx=np.r_[np.longdouble(lo),wide_x[inside],np.longdouble(hi)];yy=np.r_[edge(lo),wide_y[inside],edge(hi)]
      integral=float(np.trapezoid(yy,xx))
      if not math.isfinite(integral):raise ArithmeticError
    except (ArithmeticError,ValueError):raise ActionRejected('ANALYSIS_INPUT') from None
    values.append(integral);positions.append(spectrum['position_m'])
   return dict(positions_m=positions,band_integrals_s_inv_m_inv=values,evidence='caller measured-rate band integral; synthetic spatial phantom, not calibrated concentration')
  if k=='calibration.axis':
   self._idle()
   if not a['accept_external_reference']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   if abs(a['observed_peak_m_inv']-a['certified_peak_m_inv'])>2000:raise ActionRejected('CALIBRATION_RANGE')
   # observed axis already contains previous correction: increment, do not replace.
   self.axis_correction+=a['certified_peak_m_inv']-a['observed_peak_m_inv']
   if abs(self.axis_correction)>2000:raise ActionRejected('CALIBRATION_RANGE')
   return {'axis_correction_m_inv':self.axis_correction}
  if k=='template.read':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   t=self.templates[a['substance_id']]
   return dict(substance_id=t['substance_id'],name=t['name'],raman_shift_m_inv=t['raman_shift_m_inv'],normalized_shape=t['normalized_shape'],source_json=json.dumps(t['source'],ensure_ascii=False),evidence=t['evidence'])
  if k=='product.read':return self._ledger()
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _rate(self):
  p=self.plant;s=self._sample();gx=-self.position[0];gy=-self.position[1];sigma=p['psf_sigma_xy_m'];shape=np.zeros(len(self._grid))
  for r in s['regions']:
   wx=.5*(math.erf((r['x_max_m']-gx)/(math.sqrt(2)*sigma))-math.erf((r['x_min_m']-gx)/(math.sqrt(2)*sigma)))
   wy=.5*(math.erf((r['y_max_m']-gy)/(math.sqrt(2)*sigma))-math.erf((r['y_min_m']-gy)/(math.sqrt(2)*sigma)))
   t=self.templates[r['template_id']];shape+=wx*wy*r['response_scale']*np.interp(self._grid,t['raman_shift_m_inv'],t['normalized_shape'])
  if p['additional_fwhm_m_inv']>0:shape=gaussian_filter1d(shape,p['additional_fwhm_m_inv']/(100*math.sqrt(8*math.log(2))),mode='nearest',truncate=4.)
  focus=math.exp(-.5*((self.position[2]-s['focus_z_m'])/p['focus_sigma_z_m'])**2)
  return shape*focus*p['reference_rate_s_inv']*self.method['power_w']/p['reference_power_w']
 def _complete_window(self,when):
  start=self._window_index*1024;end=min(start+1024,len(self._grid));dt=self.method['integration_s'];p=self.plant
  mu=(self._rate()[start:end]+p['dark_rate_s_inv'])*dt
  counts=self._rng.poisson(mu).astype(float) if p['shot_noise'] else mu
  if p['read_noise_counts']>0:counts=counts+self._rng.normal(0,p['read_noise_counts'],len(mu))
  row=dict(position_m=self.position.tolist(),raman_shift_m_inv=(self._grid[start:end]+p['axis_bias_m_inv']+self.axis_correction).tolist(),counts=counts.tolist(),count_rate_s_inv=(counts/dt).tolist(),sample_time_s=when,integration_s=dt,exposures=1,evidence='simulated CCD window from measured reference shape',point_index=self._point_index,pixel_start=start)
  self.windows.append(row);self._partial.append(row);self._window_index+=1
  if end<len(self._grid):self._remaining=dt;return
  spectrum={k:copy.deepcopy(row[k]) for k in SPECTRUM['properties']}
  for k in ['raman_shift_m_inv','counts','count_rate_s_inv']:spectrum[k]=sum([w[k] for w in self._partial],[])
  spectrum.update(exposures=len(self._partial),integration_s=dt,evidence='simulated spectrum; sequential CCD windows, each integration_s; last window sample_time_s')
  self.points.append(spectrum);self._partial=[];self._window_index=0;self._point_index+=1
  if self._point_index==len(self._positions):self._stop('succeeded')
  else:self.target=np.array(self._positions[self._point_index],float);self.stage='moving'
 def step(self,dt_s):
  validate(number('s',0,3600),dt_s)
  if dt_s==0:return self.read()
  left=dt_s;cursor=self.time_s
  while left>1e-12 and self.phase=='running':
   if self.stage=='moving':
    delta=self.target-self.position;distance=float(np.max(np.abs(delta)));duration=distance/self.plant['stage_speed_m_s']
    if duration<=1e-12:
     self.position=self.target.copy()
     if self.kind=='move':self._stop('succeeded');continue
     self.stage='integrating';self._remaining=self.method['integration_s'];continue
    h=min(left,duration);self.position+=delta*(h/duration);self.elapsed_s+=h;cursor+=h;left-=h
    if h>=duration-1e-12:
     self.position=self.target.copy()
     if self.kind=='move':self._stop('succeeded')
     else:self.stage='integrating';self._remaining=self.method['integration_s']
   elif self.stage=='integrating':
    h=min(left,self._remaining);self._remaining-=h;self.elapsed_s+=h;cursor+=h;left-=h
    self._laser_on_s+=h;self._incident_energy_j+=self.method['power_w']*h
    if self._remaining<=1e-12:self._complete_window(cursor)
   else:raise RuntimeError('invalid active stage')
  self.time_s+=dt_s;self.revision+=1;return self.read()

class JascoNrs5500Session:
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
        m=export_jasco_nrs5500_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.1',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        measured=['scan.maximum_rate_s_inv','scan.last_sample_time_s']
        last=h.points[-1] if h.points else None
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='unavailable' if v[k] is None else 'estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=last['sample_time_s'] if last and k in measured else h.time_s,sample_window_s=[last['sample_time_s']-last['integration_s']*last['exposures'],last['sample_time_s']] if last and k in measured else None) for k in ids})
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
'Manufacturer standard 532nm50mW,50–8000cm^-1,1800gr/mm,1024pixelCCD,1cm^-1/pixel dispersion; dispersion is not peak FWHM/noise.',
'71 distinct real RRUFF532nm solid mineral spectra; raw/processed source values retained, normalized shape explicitly derived; no measured NRS5500 traces.',
'Only intersection of every declared phase source coverage can be scanned; no synthetic zero fill/extrapolation to full instrument range.',
'Absolute response, sample power transfer, PSF, focus, added linewidth, axis bias/dark/read/shot noise and stage speed are host assumptions, not manufacturer calibration.',
'Standard XYZ stage is manual75/50/30mm; evaluator manual reposition is external action, not self actuation. Automated move/map requires explicitly installed optional100/70/30mm XYZ stage.',
'Optional factory0.04µmXY/0.1µmZ step is a specification, not validated accuracy; continuous simulated travel, no claimed controller quantization.',
'Rectangular spatial phase regions and Gaussian PSF are synthetic phantoms, not measured maps; band intensity does not imply concentration or mass fraction.',
'CCD windows <=1024 samples; wide bands sequentially stitched with each integration time and operation-time method snapshot; no exact spectrograph wavelength-dependent dispersion model. Band integrals include supplied background; unrepresentable outputs reject ANALYSIS_INPUT.',
'One-point axis correction is caller observed/certified reference arithmetic, not factory Ne calibration; no real certificate, sensitivity correction, autofocus/PCA/stress calibration.',
'No validated compatible liquid/container response, thermal damage/photochemistry/fluorescence/cosmic ray model, polarized orientation, source temperature dependence or failure probabilities.',
'Logical intact sample/mass ledger and incident energy only; no real robot loading, material contact, absorption heat balance or laser Class1 certification.',
'Photo-derived exterior; door rotation and internal optical coordinates engineering proxies; part mass/friction/inertia unknown; URDF numerical mass is not measured.',
'Host telemetry board simulation_only, not a nonexistent OEM body display; no full original software GUI.',
'Session filtering/in-process dedup are not OS filesystem isolation/restart persistence; no native async Matterix envelope, hardware/HIL.']
def export_jasco_nrs5500_contract_metadata():
 caps={};slots={}
 reset=['scan.phase','scan.stage','scan.elapsed_s','scan.completed_points','scan.completed_windows','scan.maximum_rate_s_inv','scan.last_sample_time_s','safety.warning']
 stop=['scan.phase','scan.stage','stage.target_m','laser.emitting','safety.warning']
 xyz=['stage.x_m','stage.y_m','stage.z_m']
 immediate={
  'plant.configure':['plant.configured','plant.autostage_installed'],
  'sample.register':['truth.ledger'],'sample.load':['sample.loaded','truth.ledger'],'sample.unload':['sample.loaded','truth.ledger'],
  'method.configure':['method.configured','method.power_w','method.integration_s','method.wavelength_m'],
  'power.set':['power.enabled','laser.enabled','calibration.axis_correction_m_inv',*stop],
  'door.set':['door.opened','door.angle_rad','laser.enabled',*stop],
  'laser.set':['laser.enabled',*stop],
  'stage.manual_position':[*xyz,'stage.target_m'],
  'stage.move':['stage.target_m',*reset],
  'acquisition.start':['stage.target_m',*reset],
  'mapping.start':['stage.target_m',*reset],
  'acquisition.stop':stop,
  'calibration.axis':['calibration.axis_correction_m_inv'],
 }
 scan_step=['scan.phase','scan.stage','scan.elapsed_s','scan.completed_points','scan.completed_windows','scan.maximum_rate_s_inv','scan.last_sample_time_s','laser.emitting','truth.ledger']
 on_step={'stage.move':[*xyz,'scan.phase','scan.stage','scan.elapsed_s'],'acquisition.start':scan_step,'mapping.start':[*xyz,'stage.target_m',*scan_step]}
 on_completion={'stage.move':['scan.phase','scan.stage'],'acquisition.start':['scan.phase','scan.stage','laser.emitting'],'mapping.start':['scan.phase','scan.stage','laser.emitting']}
 observed={
  'plant.configure':['plant.configured'],'method.configure':['method.configured'],'sample.load':['sample.loaded'],'sample.unload':['sample.loaded'],
  'power.set':['power.enabled'],'door.set':['door.opened'],'laser.set':['laser.enabled'],'stage.manual_position':xyz,
  'stage.move':['scan.phase'],'acquisition.start':['scan.phase'],'mapping.start':['scan.phase'],'acquisition.stop':['scan.phase'],
  'calibration.axis':['calibration.axis_correction_m_inv'],
  'process.measure':['scan.phase','scan.stage','scan.elapsed_s','scan.completed_points','scan.completed_windows','scan.maximum_rate_s_inv','safety.warning'],
  'result.read':['scan.phase','scan.completed_points','scan.completed_windows','scan.maximum_rate_s_inv','scan.last_sample_time_s'],
  'product.read':['truth.ledger'],
 }
 def effects_for(key):
  descriptions={'immediate':'本次通过校验的配置、门控、外部手动事件、取消或目标设置；不推进仿真时间','on_step':'后续宿主step推进已安装自动台或完整CCD曝光；移动时无激光入射能增加','on_completion':'目标就位或全部CCD窗/映射点结束时设置终态；采集结束关闭积分快门'}
  return [dict(state_id=state,timing=timing,description_zh=descriptions[timing]) for timing,mapping in [('immediate',immediate),('on_step',on_step),('on_completion',on_completion)] for state in mapping.get(key,[])]
 for k,(s,u,o,v,c,w) in SLOTS.items():
  measured=k in ['scan.maximum_rate_s_inv','scan.last_sample_time_s']
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'JascoNrs5500Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='interval_mean' if measured else 'instant',time_domain='simulation',window_description='最近完整谱的顺序CCD曝光窗口；sample_time为最后窗结束，不是读取时间' if measured else None),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNSUPPORTED','PRECONDITION_FAILED','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','INITIALIZATION_ONLY','SAMPLE_ACCESS_REQUIRED','SAMPLE_METHOD_REQUIRED','EVIDENCE_ACK_REQUIRED','BUSY','PLANT_REQUIRED','STAGE_RANGE','SOURCE_COVERAGE','ANALYSIS_INPUT','INVALID_REGION','OVERLAPPING_REGIONS','PIXEL_GRID','OPTICS_NOT_READY','AUTOSTAGE_NOT_INSTALLED','CALIBRATION_RANGE']
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  asynchronous=k in ['acquisition.start','mapping.start','stage.move'];query=kind in ['query','analysis'];unsupported=k in UNSUPPORTED
  caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.jasco_nrs5500.JascoNrs5500Session.invoke',invocation=f'session.invoke(instance_id="nrs5500_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),
   preconditions=[] if query or unsupported else [dict(description_zh='逐能力检查宿主配置、样品、供电关门、激光许可、实测覆盖、自动台选件和忙碌状态；详见Runtime._invoke',state_ids=['power.enabled','door.opened','laser.enabled','plant.autostage_installed','scan.phase'],enforced_by='runtime',check='atomic Runtime.invoke and trusted-principal Session')],
   state_effects=[] if query or unsupported else effects_for(k),
   observes=observed.get(k,[]),model_effects=dict(mode='none',description_zh='不支持无变化' if unsupported else '待独立场景验证',binding_ids=[]),
   execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',completion_criterion='载物台就位且全部顺序CCD窗与点完成；移动操作仅目标就位' if asynchronous else '事务返回；查询/分析不推进时间',completion_state_ids=['scan.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',retry='same_request_id',resources=['host_clock','sample_holder','request_cache'],partial_failure_zh='开门、断电、取消保留已完成窗/点与入射能；不完整窗不伪造数据。终态poll摘要受原身份保护且后续操作后仍可查询，取消历史终态不停止新操作；结果保留采集时方法快照；历史与去重限进程内'),
   errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=LIMITATIONS,source_ids=['manufacturer_primary','manufacturer_us','simulation_assumption'])
 mech=dict(component_ids=['laser_532','shutter','objective','sample_holder','notch_filter','grating','detector','stage_x','stage_y','stage_z','host_screen'],state_ids=list(slots),meaning_zh='RRUFF实测谱形驱动，标准/选配台严格区分，显式非标定PSF与计数、1024像素分窗、逐点映射和仅观测分析',implementation='lumped',equations=[
 'Raman shift[1/m]=source[cm^-1]*100; Stokes wavelength=1/(1/532e-9-shift); grid spacing100m^-1=1cm^-1/pixel, not FWHM.',
 'source shape=max(processed,0)/max(max(processed,0)); raw/processed originals preserved; no inverse deconvolution or extrapolation.',
 'sample illuminated XY=-stage XY; Gaussian PSF rectangle weight=product of0.5*(erf((max-coordinate)/(sqrt(2)*sigma))-erf((min-coordinate)/(sqrt(2)*sigma))).',
 'signal=sum(weight*host_region_scale*interp(shape)); optional additional Gaussian convolution sigma=host_FWHM/sqrt(8ln2); source linewidth remains embedded.',
 'focus=exp(-0.5*((stageZ-host_focusZ)/host_sigmaZ)^2); rate=signal*focus*host_reference_rate*declared_power/host_reference_power.',
 'CCD expected counts=(rate+host_dark)*integration; optional Poisson plus independent Normal(0,host_read_noise). Count rate=observed_counts/integration.',
 'Every CCD exposure<=1024pixels; wideband split into disjoint sequential windows, all completed windows retained on cancellation; source bounds checked for all phases.',
 'XYZ move direction-linear, max axis speed=host_speed; movement finishes before shutter integration. step sole clock; manual positioning host external event.',
 'Axis_reported=grid+host_bias+caller_calibration; calibration += caller_certified-caller_observed, no hidden bias lookup.',
 'Peak detection uses caller observations without baseline removal or hidden identity; band map uses linear edge interpolation and guarded extended-precision trapezoidal integration, unit1/(s*m); signed background remains included, not chemical concentration.',
 'Sum registered solid mass=attached+external; incident energy=sum(power*actual_integrated_time), no absorbed heat claim.'],
 parameters={},source_ids=['manufacturer_primary','manufacturer_us','rruff_primary','simulation_assumption'],limitations=LIMITATIONS)
 spec=importlib.util.spec_from_file_location('_nrs5500_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
 ann.annotate_export(SLUG,caps,slots,{'raman_spectroscopy':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.register':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'，非实机标定。'
 mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'raman_spectroscopy':mech})


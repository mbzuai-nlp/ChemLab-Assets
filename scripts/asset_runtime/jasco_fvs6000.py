"""Conditioned FTIR/VCD replay, explicit synthetic IR shapes, and host-owned sample handling."""
from __future__ import annotations
import copy, importlib.util, json, math
from functools import lru_cache
from pathlib import Path
import numpy as np
from scipy.ndimage import gaussian_filter1d
ROOT=Path(__file__).resolve().parents[2]
SLUG='jasco_fvs6000';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-077';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/expansion_batch04/t3_analytical/jasco_fvs6000/output_templates/library.json'
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
PLANT=obj({'detector_ready':BOOL,'purge_ready':BOOL,'water_ready':BOOL,'temperature_installed':BOOL,'three_position_installed':BOOL,'ambient_temperature_k':number('K',268.15,363.15),'thermal_capacity_j_k':number('J/K',1,1000),'thermal_conductance_w_k':number('W/K',.01,100),'filter_speed_rad_s':number('rad/s',.01,10),'stage_speed_m_s':number('m/s',.001,.1),'frame_duration_s':number('s',.01,10),'ir_noise_sd_1s':number('1',0,.001),'vcd_noise_sd_1s':number('1',0,.001),'blank_ir_absorbance':number('1',0,2),'blank_vcd_bias':number('1',-.01,.01),'seed':integer(0,2147483647),'accept_surrogate_model':BOOL})
SAMPLE=obj({'sample_id':TEXT,'kind':enum(['blank','measured_reference','synthetic_ir']),'template_id':TEXT,'solvent':TEXT,'is_aqueous':BOOL,'volume_m3':number('m^3',1e-9,10e-6),'concentration_kg_m3':number('kg/m^3',0,1000),'ir_peak_absorbance':number('1',0,2),'synthetic_vcd_ratio':number('1',-.01,.01),'accept_inferred_units':BOOL,'accept_unknown_conditions':BOOL,'accept_surrogate':BOOL})
PATH=obj({'path_length_m':number('m',25e-6,100e-6),'window_material':enum(['BaF2','CaF2'])})
METHOD=obj({'wavenumber_start_m_inv':number('1/m',85000,320000),'wavenumber_stop_m_inv':number('1/m',85000,320000),'spacing_m_inv':number('1/m',25,1600),'resolution_m_inv':number('1/m',50,1600),'accumulation_s':number('s',.01,86400)})
PLANT_EXAMPLE=dict(detector_ready=True,purge_ready=True,water_ready=True,temperature_installed=True,three_position_installed=True,ambient_temperature_k=298.15,thermal_capacity_j_k=20.,thermal_conductance_w_k=5.,filter_speed_rad_s=math.pi,stage_speed_m_s=.05,frame_duration_s=.25,ir_noise_sd_1s=1e-5,vcd_noise_sd_1s=1e-6,blank_ir_absorbance=.05,blank_vcd_bias=2e-6,seed=77,accept_surrogate_model=True)
PATH_EXAMPLE=dict(path_length_m=100e-6,window_material='BaF2')
METHOD_EXAMPLE=dict(wavenumber_start_m_inv=150000.,wavenumber_stop_m_inv=180000.,spacing_m_inv=400.,resolution_m_inv=800.,accumulation_s=1.)
SAMPLE_EXAMPLE=dict(sample_id='external_reference_cell',kind='measured_reference',template_id='peptide_tk082_ss',solvent='DMSO-d6',is_aqueous=False,volume_m3=50e-9,concentration_kg_m3=25.,ir_peak_absorbance=0.,synthetic_vcd_ratio=0.,accept_inferred_units=True,accept_unknown_conditions=True,accept_surrogate=False)
BLANK_EXAMPLE=dict(SAMPLE_EXAMPLE,sample_id='external_blank_cell',kind='blank',template_id='',concentration_kg_m3=0.,accept_inferred_units=False,accept_unknown_conditions=False)
MEASURE=obj({'template_source':TEXT,'phase':PHASE,'kind':TEXT,'elapsed_s':number('s',0),'progress':number('1',0,1),'frames':integer(0,1000000),'ir_center':nullable(number('1')),'vcd_center':nullable(number('1')),'last_sample_time_s':nullable(number('s',0)),'warning':TEXT})
PROVENANCE=obj({'response_kind':TEXT,'ir_evidence':TEXT,'vcd_evidence':TEXT,'unit_evidence':TEXT,'original_unit_explicit':nullable(TEXT),'inferred_units_accepted':BOOL,'source_temperature_k':nullable(number('K')),'unknown_conditions_accepted':BOOL,'source_resolution_m_inv':nullable(number('1/m')),'limitations':arr(TEXT)})
RESULT=obj({'template_source':TEXT,'phase':PHASE,'kind':TEXT,'complete':BOOL,'wavenumber_m_inv':arr(number('1/m')),'ir_absorbance':arr(number('1')),'vcd_delta_absorbance':arr(number('1')),'transmittance_left':arr(number('1',0)),'transmittance_right':arr(number('1',0)),'frames':integer(0,1000000),'sample_window_s':nullable(arr(number('s',0))),'settings_json':TEXT,'provenance':PROVENANCE})
LEDGER=obj({'sample_id':nullable(TEXT),'template_id':nullable(TEXT),'registered_volume_m3':number('m^3',0),'current_volume_m3':number('m^3',0),'removed_volume_m3':number('m^3',0),'volume_residual_m3':number('m^3'),'registered_solute_kg':number('kg',0),'current_solute_kg':number('kg',0),'removed_solute_kg':number('kg',0),'solute_residual_kg':number('kg'),'controlled_heat_j':number('J'),'ambient_heat_j':number('J'),'stored_heat_j':number('J'),'heat_residual_j':number('J'),'evidence':TEXT})
TEMPLATE=obj({'substance_id':TEXT,'name':TEXT,'evidence':TEXT,'template_json':TEXT})
COMPARE_IN=obj({'observed':vector(number('1')),'reference':vector(number('1'))})
COMPARE_OUT=obj({'signed_cosine':number('1',-1,1),'nonnegative_scale':number('1',0),'relative_rmse':number('1',0),'configuration_assignment':enum(['not_assigned']),'evidence':TEXT})
CAPS={}
def cap(k,inp,out,c,z,kind='device_command',vis='agent',ex=None):CAPS[k]=[inp,out,c,z,kind,vis,{} if ex is None else ex,[]]
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body','mct_detector','purge_optics','coolant_in'],'宿主一次确认安装/冷却/吹扫与明确噪声及热学代理；不是实机传感反馈','configuration','evaluator',PLANT_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_cell'],'可信宿主装入一只中心液池；实测条件与单位推断需明确接受；无自动身份识别','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.unload',obj({'recipient_id':TEXT}),obj({'unloaded':BOOL,'volume_m3':number('m^3',0)}),['sample_cell'],'可信宿主取出完整样品至外部接收器，保留体积与溶质账本','device_command','evaluator',{'recipient_id':'external_rack'})
cap('cover.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['sample_cover'],'外部盖提升；开盖取消当前采集，保留已完成帧','device_command','evaluator',{'opened':False})
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','ir_source'],'逻辑供电；断电取消采集并使空白失效',ex={'enabled':True})
cap('path.configure',PATH,obj({'configured':BOOL}),['sample_cell'],'手动选择25–100µm液池光程和窗口材质；不是电动改变液池','configuration','agent',PATH_EXAMPLE)
cap('filter.select',obj({'selection':enum(['low','high'])}),obj({'selection':TEXT}),['filter_wheel'],'六位置轮仅安装两滤段；0/π为工程选位映射，随后step有限转动',ex={'selection':'low'})
cap('stage.select',obj({'position':integer(-1,1)}),obj({'target_position_m':number('m',-.035,.035)}),['sample_stage'],'三位置选件有限平移；仅中心槽装池，侧槽为空，不模拟三份样品',ex={'position':0})
cap('temperature.set',obj({'target_k':number('K',268.15,363.15)}),obj({'target_k':number('K')}),['sample_cell','coolant_in'],'TCH选件设定；随后step按显式C/H一阶热模型推进',ex={'target_k':298.15})
cap('method.configure',METHOD,obj({'configured':BOOL}),['interferometer_mirror','filter_wheel','pem'],'设置波数网格/分辨率和全谱积累时间；保留旧谱自己的方法快照','configuration','agent',METHOD_EXAMPLE)
cap('blank.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['interferometer_mirror','pem','mct_detector'],'闭盖/冷却/吹扫/窗口就绪后采集空白；只有完成全程才生成可用背景')
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['interferometer_mirror','pem','sample_cell','mct_detector'],'对匹配空白进行全谱帧积分；ΔA=AL−AR，返回实测参考或明确合成谱')
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['interferometer_mirror'],'停止采集，保留已完成帧及真实已用时间')
cap('process.measure',obj({}),MEASURE,['host_screen'],'最近采集中心值/帧数/进度；查询不推进时间','query')
cap('result.read',obj({}),RESULT,['host_screen'],'当前操作的IR/VCD及左右归一化透射估计，保留源证据与方法快照，不输出隐藏身份','query')
cap('result.export',obj({'format':enum(['json','csv'])}),obj({'format':TEXT,'content':TEXT}),['host_screen'],'只返回JSON或CSV内容，不写文件；含源证据和单位推断说明','query','agent',{'format':'json'})
cap('blank.read',obj({}),obj({'valid':BOOL,'result':nullable(RESULT)}),['host_screen'],'读取匹配状态及最近完成空白；无额外采样','query')
cap('template.read',obj({'substance_id':TEXT}),TEMPLATE,['host_screen'],'按调用者指定ID读取公开真实源谱及完整单位/条件证据，VCD缺失保留null','query','agent',{'substance_id':'peptide_tk082_ss'})
cap('reference.compare',COMPARE_IN,COMPARE_OUT,['host_screen'],'仅比较调用者提供的同网格数组；保留VCD正负号，不从隐藏身份赋予构型','analysis','agent',{'observed':[1.,-1.,.5],'reference':[1.,-1.,.5]})
cap('product.read',obj({}),LEDGER,['sample_cell'],'评估器体积/溶质与热学代理能量账本；无样品消耗或光化学产物','query','evaluator')
cap('configuration.assign',obj({}),obj({}),['host_screen'],'绝对构型自动赋值未实现，需独立计算/参照与专家判断')
cap('interferogram.export',obj({}),obj({}),['interferometer_mirror','pem'],'真实干涉图、PEM锁相校准和傅里叶重建未实现')
UNSUPPORTED={'configuration.assign','interferogram.export'}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='host_screen',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','宿主已确认外接条件和代理参数','setpoint',c='body')
slot('sample.loaded',BOOL,'1','中心液池已外部装入','scene',c='sample_cell')
slot('power.enabled',BOOL,'1','逻辑供电','setpoint',c='ir_source',w=('power.set','enabled'))
slot('cover.opened',BOOL,'1','外部盖已打开','scene',c='sample_cover')
slot('cover.lift_m',number('m',0,.12),'m','工程盖提升位移','scene',c='sample_cover')
slot('sample.position_m',number('m',-.035,.035),'m','三位置选件当前工程位移','scene',c='sample_stage')
slot('sample.target_position_m',number('m',-.035,.035),'m','选件目标位移','setpoint',c='sample_stage')
slot('filter.selection',enum(['low','high']),'1','两已安装滤段目标选择','setpoint',c='filter_wheel')
slot('filter.angle_rad',number('rad',0,math.pi),'rad','滤轮工程实际转角','scene',c='filter_wheel')
slot('filter.target_angle_rad',number('rad',0,math.pi),'rad','两已安装标准滤段0/π目标角','setpoint',c='filter_wheel')
slot('path.length_m',number('m',25e-6,100e-6),'m','宿主选择的液池光程','setpoint',c='sample_cell')
slot('path.window_material',enum(['BaF2','CaF2']),'1','液池窗口材质','setpoint',c='sample_cell')
slot('temperature.target_k',number('K',268.15,363.15),'K','显式温控或环境目标','setpoint',c='sample_cell',w=('temperature.set','target_k'))
slot('temperature.actual_k',number('K',268.15,363.15),'K','一阶集总温度估计，非原厂传感器','derived',c='sample_cell')
for k,c,z in [('detector','mct_detector','宿主确认检测器冷却'),('purge','purge_optics','宿主确认三路吹扫'),('water','coolant_in','宿主确认冷却水')]:slot(k+'.ready',BOOL,'1',z,'setpoint',c=c)
slot('blank.valid',BOOL,'1','已完成空白且方法/光程/溶剂/目标温度仍匹配')
slot('method.resolution_m_inv',nullable(number('1/m')),'1/m','选定分辨率，非采样间隔','setpoint',c='interferometer_mirror')
slot('method.accumulation_s',nullable(number('s')),'s','全谱积累时间','setpoint',c='mct_detector')
slot('method.center_wavenumber_m_inv',nullable(number('1/m')),'1/m','当前方法两端中点波数','setpoint')
slot('scan.phase',PHASE,'1','当前操作生命周期')
slot('scan.kind',TEXT,'1','空白或样品操作类型')
slot('scan.elapsed_s',number('s',0),'s','当前操作实际已用时间')
slot('scan.progress',number('1',0,1),'1','已完成全谱帧积分时间占比')
slot('scan.frames',integer(0,1000000),'1','已完成全谱积分帧数')
slot('scan.ir_center',nullable(number('1')),'1','当前操作中心波数IR观测均值')
slot('scan.vcd_center',nullable(number('1')),'1','当前操作中心波数ΔA观测均值，符号AL−AR')
slot('scan.last_sample_time_s',nullable(number('s',0)),'s','最近完成全谱帧的世界时刻')
slot('interferometer.position_m',number('m',0,.01),'m','积累期间有限往复工程镜位，不是厂家OPD/行程','scene',c='interferometer_mirror')
slot('safety.warning',TEXT,'1','当前采集中止或条件提示，无样品身份')
slot('truth.ledger',LEDGER,'1','私有样品/体积/溶质/热量账本','process_truth','evaluator','sample_cell')

@lru_cache(maxsize=1)
def _templates():return json.loads((ROOT/TEMPLATE_REL).read_text())['compounds']
def _empty_provenance():return dict(response_kind='unavailable',ir_evidence='unavailable',vcd_evidence='unavailable',unit_evidence='unavailable',original_unit_explicit=None,inferred_units_accepted=False,source_temperature_k=None,unknown_conditions_accepted=False,source_resolution_m_inv=None,limitations=[])

def _grid(method):
 a=method['wavenumber_start_m_inv'];b=method['wavenumber_stop_m_inv'];d=method['spacing_m_inv']
 if b<=a:raise ActionRejected('SPECTRAL_GRID')
 n=int(math.ceil((b-a)/d))+1
 if n>10000:raise ActionRejected('EVENT_RESOURCE_LIMIT')
 x=a+np.arange(n)*d;x=x[x<b];return np.r_[x,b]

def _curve(record,key,ordinate,grid,resolution):
 p=record[key];x=np.asarray(p['wavenumber_m_inv'],float);y=np.asarray(p[ordinate],float)
 if x.ndim!=1 or len(x)<2 or x.shape!=y.shape or not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)) or np.any(np.diff(x)<=0):raise ActionRejected('INVALID_REFERENCE')
 if grid[0]<x[0] or grid[-1]>x[-1]:raise ActionRejected('SOURCE_WAVENUMBER_RANGE')
 src=p.get('source_resolution_m_inv')
 if src is not None and resolution+1e-10<src:raise ActionRejected('SOURCE_RESOLUTION_LIMIT')
 # Add only missing Gaussian variance; no deconvolution or finer source detail is claimed.
 extra=math.sqrt(max(0.,resolution**2-(src or 0.)**2))
 if extra:
  d=min(float(np.min(np.diff(x))),resolution/8.);n=int(math.ceil((x[-1]-x[0])/d))+1
  if n>200000:raise ActionRejected('EVENT_RESOURCE_LIMIT')
  gx=np.linspace(x[0],x[-1],n);gy=np.interp(gx,x,y)
  gy=gaussian_filter1d(gy,extra/(math.sqrt(8*math.log(2))*(gx[1]-gx[0])),mode='nearest');return np.interp(grid,gx,gy)
 return np.interp(grid,x,y)

class JascoFvs6000Runtime:
 def __init__(self,instance_id='fvs6000_01',templates=None):
  self.instance_id=instance_id;self.templates=_templates() if templates is None else copy.deepcopy(templates)
  self.time_s=0.;self.revision=0;self.plant=None;self.method=None;self.sample=None;self.path=copy.deepcopy(PATH_EXAMPLE)
  self.powered=False;self.opened=True;self.filter_selection='low';self.filter_angle=0.;self.filter_target=0.;self.stage_position=0.;self.stage_target=0.
  self.temperature=298.15;self.temperature_target=298.15;self._initial_temperature=298.15;self._controlled_heat=0.;self._ambient_heat=0.
  self.phase='idle';self.kind='';self.elapsed_s=0.;self.warning='';self.operation_id='';self._op_count=0;self._run_method=None;self._run_path=None;self._run_provenance=_empty_provenance()
  self._axis=np.array([]);self._mean_ir=np.array([]);self._mean_vcd=np.array([]);self._expected_ir=np.array([]);self._expected_vcd=np.array([]);self._background_ir=np.array([]);self._background_vcd=np.array([])
  self._frames=0;self._sampled_s=0.;self._last_sample_time=None;self._started=0.;self._next_frame=None;self._blank=None;self._mirror_phase=0.
  self._registered_v=0.;self._removed_v=0.;self._registered_mass=0.;self._removed_mass=0.;self._rng=np.random.default_rng(0)
  self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._operation_records={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return JascoFvs6000Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in ['templates','_rng']})|{'_rng_state':copy.deepcopy(self._rng.bit_generator.state)}
 def _ledger(self):
  v=self.sample['volume_m3'] if self.sample else 0.;m=v*self.sample['concentration_kg_m3'] if self.sample else 0.
  stored=(self.plant['thermal_capacity_j_k'] if self.plant else 0.)*(self.temperature-self._initial_temperature)
  return dict(sample_id=self.sample['sample_id'] if self.sample else None,template_id=self.sample['template_id'] if self.sample else None,registered_volume_m3=self._registered_v,current_volume_m3=v,removed_volume_m3=self._removed_v,volume_residual_m3=self._registered_v-self._removed_v-v,registered_solute_kg=self._registered_mass,current_solute_kg=m,removed_solute_kg=self._removed_mass,solute_residual_kg=self._registered_mass-self._removed_mass-m,controlled_heat_j=self._controlled_heat,ambient_heat_j=self._ambient_heat,stored_heat_j=stored,heat_residual_j=self._controlled_heat+self._ambient_heat-stored,evidence='closed intact-cell volume and solute; lumped host C/H thermal balance only; no optical power/photochemistry ledger')
 def _fingerprint(self):
  if not self.method or not self.sample:return None
  return dict(method={k:v for k,v in self.method.items() if k!='accumulation_s'},path=self.path,filter=self.filter_selection,solvent=self.sample['solvent'],is_aqueous=self.sample['is_aqueous'],target_temperature_k=self.temperature_target)
 def _blank_valid(self):return self.powered and self._blank is not None and self._blank['fingerprint']==self._fingerprint()
 def _measure(self):
  i=len(self._axis)//2
  return dict(template_source=TEMPLATE_REL,phase=self.phase,kind=self.kind,elapsed_s=self.elapsed_s,progress=self._sampled_s/self._run_method['accumulation_s'] if self._run_method else 0.,frames=self._frames,ir_center=float(np.interp((self._axis[0]+self._axis[-1])/2,self._axis,self._mean_ir)) if self._frames else None,vcd_center=float(np.interp((self._axis[0]+self._axis[-1])/2,self._axis,self._mean_vcd)) if self._frames else None,last_sample_time_s=self._last_sample_time,warning=self.warning)
 def _result(self):
  a=self._mean_ir if self._frames else np.array([]);d=self._mean_vcd if self._frames else np.array([])
  # Apparent normalized transmittance of the blank-corrected mean; not raw detector counts.
  return dict(template_source=TEMPLATE_REL,phase=self.phase,kind=self.kind,complete=self.phase=='succeeded',wavenumber_m_inv=self._axis.tolist(),ir_absorbance=a.tolist(),vcd_delta_absorbance=d.tolist(),transmittance_left=np.power(10.,-(a+d/2.)).tolist(),transmittance_right=np.power(10.,-(a-d/2.)).tolist(),frames=self._frames,sample_window_s=[self._started,self._last_sample_time] if self._frames else None,settings_json=json.dumps({'method':self._run_method,'path':self._run_path},sort_keys=True),provenance=copy.deepcopy(self._run_provenance))
 def read(self,visibility='agent'):
  if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
  p=self.plant;m=self.method;r=self._measure()
  v={'plant.configured':p is not None,'sample.loaded':self.sample is not None,'power.enabled':self.powered,'cover.opened':self.opened,'cover.lift_m':.12 if self.opened else 0.,'sample.position_m':self.stage_position,'sample.target_position_m':self.stage_target,'filter.selection':self.filter_selection,'filter.angle_rad':self.filter_angle,'filter.target_angle_rad':self.filter_target,'path.length_m':self.path['path_length_m'],'path.window_material':self.path['window_material'],'temperature.target_k':self.temperature_target,'temperature.actual_k':self.temperature,'detector.ready':p['detector_ready'] if p else False,'purge.ready':p['purge_ready'] if p else False,'water.ready':p['water_ready'] if p else False,'blank.valid':bool(self._blank_valid()),'method.resolution_m_inv':m['resolution_m_inv'] if m else None,'method.accumulation_s':m['accumulation_s'] if m else None,'method.center_wavenumber_m_inv':(m['wavenumber_start_m_inv']+m['wavenumber_stop_m_inv'])/2 if m else None,'scan.phase':self.phase,'scan.kind':self.kind,'scan.elapsed_s':self.elapsed_s,'scan.progress':r['progress'],'scan.frames':self._frames,'scan.ir_center':r['ir_center'],'scan.vcd_center':r['vcd_center'],'scan.last_sample_time_s':self._last_sample_time,'interferometer.position_m':.01*(1-abs(2*self._mirror_phase-1)),'safety.warning':self.warning,'truth.ledger':self._ledger()}
  if visibility=='agent':v.pop('truth.ledger')
  return copy.deepcopy(v)
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
   ts=self.templates;rs=snap.pop('_rng_state');self.__dict__.clear();self.__dict__.update(snap);self.templates=ts;self._rng=np.random.default_rng();self._rng.bit_generator.state=rs
   raise
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _manual(self):
  self._idle()
  if not self.opened:raise ActionRejected('COVER_MUST_BE_OPEN')
  if abs(self.stage_position)>1e-12 or abs(self.stage_target)>1e-12:raise ActionRejected('CENTER_POSITION_REQUIRED')
 def _conditions(self,sample):
  if sample['is_aqueous'] and self.path['window_material']=='BaF2':raise ActionRejected('BAF2_WATER_INCOMPATIBLE')
  if sample['kind']=='blank':
   if sample['template_id'] or sample['concentration_kg_m3']!=0:raise ActionRejected('SAMPLE_COMPOSITION')
   return
  r=self.templates.get(sample['template_id'])
  if r is None or not r.get('ir'):raise ActionRejected('UNKNOWN_REFERENCE')
  if sample['kind']=='synthetic_ir':
   if not sample['accept_surrogate']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   if 'absorbance_normalized' not in r['ir']:raise ActionRejected('NORMALIZED_IR_REQUIRED')
   return
  if r.get('vcd') is None or 'absorbance' not in r['ir']:raise ActionRejected('MEASURED_VCD_REQUIRED')
  if any(r[q].get('requires_accept_inferred_units',False) for q in ['ir','vcd']) and not sample['accept_inferred_units']:raise ActionRejected('INFERRED_UNITS_ACK_REQUIRED')
  c=r.get('conditions',{})
  for k in ['solvent','concentration_kg_m3','is_aqueous']:
   if c.get(k) is not None and sample[k]!=c[k]:raise ActionRejected('SOURCE_CONDITIONS_MISMATCH')
  for k in ['path_length_m','window_material']:
   if c.get(k) is not None and self.path[k]!=c[k]:raise ActionRejected('SOURCE_CONDITIONS_MISMATCH')
  if c.get('temperature_k') is None and not sample['accept_unknown_conditions']:raise ActionRejected('UNKNOWN_CONDITIONS_ACK_REQUIRED')
  if c.get('temperature_k') is not None and abs(self.temperature-c['temperature_k'])>.1:raise ActionRejected('SOURCE_CONDITIONS_MISMATCH')
 def _response(self,kind,axis):
  p=self.plant;s=self.sample;prov=_empty_provenance()
  a=np.zeros(len(axis));d=np.zeros(len(axis))
  if kind=='blank':prov.update(response_kind='synthetic_blank',ir_evidence='synthetic',vcd_evidence='synthetic',unit_evidence='host_declared_absorbance',limitations=['Uniform host blank/bias proxy; not a measured solvent spectrum.'])
  else:
   r=self.templates[s['template_id']];res=self.method['resolution_m_inv'];c=r.get('conditions',{})
   if s['kind']=='measured_reference':
    a=_curve(r,'ir','absorbance',axis,res);d=_curve(r,'vcd','delta_absorbance',axis,res)
    prov.update(response_kind='measured_reference_replay',ir_evidence=r['ir']['evidence'],vcd_evidence=r['vcd']['evidence'],unit_evidence=r['vcd'].get('unit_evidence','explicit'),original_unit_explicit=r['vcd'].get('original_unit_explicit'),inferred_units_accepted=s['accept_inferred_units'],source_temperature_k=c.get('temperature_k'),unknown_conditions_accepted=s['accept_unknown_conditions'],source_resolution_m_inv=r['vcd'].get('source_resolution_m_inv'),limitations=['Reference from another instrument and source accumulation; no calibrated FVS-6000 response or improved source SNR.','Unit and unknown temperature acknowledgments are explicit; no amplitude multiplier inferred.'])
   else:
    a=_curve(r,'ir','absorbance_normalized',axis,res)*s['ir_peak_absorbance']*self.path['path_length_m']/100e-6;d=s['synthetic_vcd_ratio']*a
    prov.update(response_kind='synthetic_ir_shape',ir_evidence='real_normalized_shape_with_synthetic_amplitude',vcd_evidence='synthetic',unit_evidence='host_assumed_amplitude',unknown_conditions_accepted=s['accept_unknown_conditions'],source_resolution_m_inv=r['ir'].get('source_resolution_m_inv'),limitations=['Normalized source IR is a shape only, not molar absorptivity or solution concentration response.','DeltaA = explicitly assumed constant ratio times A; it is not measured VCD, enantiomer identification or a prediction of chirality.','Source phase/solvent/temperature need not match; this explicitly accepted surrogate is not a conditioned real response.'])
  prov['limitations'].append('Left/right transmittance arrays are synthetic apparent values reconstructed from the observed blank-corrected mean; not measured raw polarized intensities, detector counts or recovered interferograms.')
  a=a+p['blank_ir_absorbance'];d=d+p['blank_vcd_bias']
  if not np.all(np.isfinite(a)) or not np.all(np.isfinite(d)) or np.max(np.abs(a)+np.abs(d)/2)>8:raise ActionRejected('ABSORBANCE_RANGE')
  return a,d,prov
 def _invoke(self,k,a):
  if k=='plant.configure':
   if self.plant is not None or self.time_s or self.sample:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_surrogate_model']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   self.plant=a;self._rng=np.random.default_rng(a['seed']);self.temperature=self.temperature_target=self._initial_temperature=a['ambient_temperature_k'];return {'configured':True}
  if k=='sample.load':
   self._manual()
   if self.plant is None:raise ActionRejected('PLANT_REQUIRED')
   if self.sample is not None:raise ActionRejected('UNLOAD_REQUIRED')
   if not a['sample_id'] or not a['solvent']:raise ActionRejected('INVALID_ARGUMENT')
   self._conditions(a);self.sample=a;self._registered_v+=a['volume_m3'];self._registered_mass+=a['volume_m3']*a['concentration_kg_m3'];return {'loaded':True}
  if k=='sample.unload':
   self._manual()
   if not a['recipient_id']:raise ActionRejected('INVALID_ARGUMENT')
   if self.sample is None:raise ActionRejected('SAMPLE_REQUIRED')
   v=self.sample['volume_m3'];self._removed_v+=v;self._removed_mass+=v*self.sample['concentration_kg_m3'];self.sample=None;return {'unloaded':True,'volume_m3':v}
  if k=='cover.set':
   self.opened=a['opened']
   if self.opened:self._stop('cover_open')
   return a
  if k=='power.set':
   self.powered=a['enabled']
   if not self.powered:self._stop('power_off');self._blank=None
   return a
  if k=='path.configure':
   self._manual();old=self.path;self.path=a
   if self.sample:self._conditions(self.sample)
   return {'configured':True}
  if k=='filter.select':
   self._idle();self.filter_selection=a['selection'];self.filter_target=0. if a['selection']=='low' else math.pi;return a
  if k=='stage.select':
   self._idle()
   if self.plant is None or not self.plant['three_position_installed']:raise ActionRejected('OPTION_NOT_INSTALLED')
   self.stage_target=a['position']*.035;return {'target_position_m':self.stage_target}
  if k=='temperature.set':
   self._idle()
   if self.plant is None or not self.plant['temperature_installed']:raise ActionRejected('OPTION_NOT_INSTALLED')
   if not self.plant['water_ready']:raise ActionRejected('COOLING_WATER_REQUIRED')
   self.temperature_target=a['target_k'];return a
  if k=='method.configure':
   self._idle();_grid(a)
   if self.plant and math.ceil(a['accumulation_s']/self.plant['frame_duration_s'])>1000000:raise ActionRejected('EVENT_RESOURCE_LIMIT')
   self.method=a;return {'configured':True}
  if k in ['blank.start','acquisition.start']:return self._start('blank' if k=='blank.start' else 'sample')
  if k=='acquisition.stop':self._stop('cancelled_by_caller');return {'phase':self.phase}
  if k=='process.measure':return self._measure()
  if k=='result.read':return self._result()
  if k=='blank.read':return {'valid':bool(self._blank_valid()),'result':copy.deepcopy(self._blank['result']) if self._blank else None}
  if k=='product.read':return self._ledger()
  if k=='result.export':
   result=self._result()
   if a['format']=='json':content=json.dumps(result,ensure_ascii=False,allow_nan=False)
   else:
    content='# '+json.dumps({k:v for k,v in result.items() if k not in ['wavenumber_m_inv','ir_absorbance','vcd_delta_absorbance','transmittance_left','transmittance_right']},ensure_ascii=False,allow_nan=False)+'\nwavenumber_m_inv,ir_absorbance,vcd_delta_absorbance,transmittance_left,transmittance_right\n'
    content+=''.join(','.join(format(v,'.17g') for v in row)+'\n' for row in zip(result['wavenumber_m_inv'],result['ir_absorbance'],result['vcd_delta_absorbance'],result['transmittance_left'],result['transmittance_right']))
   return {'format':a['format'],'content':content}
  if k=='template.read':
   r=self.templates.get(a['substance_id'])
   if r is None or not r.get('ir'):raise ActionRejected('UNKNOWN_REFERENCE')
   return {'substance_id':a['substance_id'],'name':r['name'],'evidence':r.get('evidence',r['ir'].get('evidence','unknown')),'template_json':json.dumps(r,ensure_ascii=False,allow_nan=False)}
  if k=='reference.compare':
   y=np.array(a['observed']);r=np.array(a['reference'])
   if y.shape!=r.shape or not np.any(y) or not np.any(r):raise ActionRejected('ANALYSIS_INPUT')
   sy=float(np.max(np.abs(y)));sr=float(np.max(np.abs(r)));yn=y/sy;rn=r/sr;dot=float(yn@rn);rr=float(rn@rn);yy=float(yn@yn)
   scale=float(np.longdouble(max(0.,dot/rr))*np.longdouble(sy)/np.longdouble(sr));err=float(np.linalg.norm(yn-max(0.,dot/rr)*rn)/math.sqrt(yy))
   if not math.isfinite(scale):raise ActionRejected('ANALYSIS_RANGE')
   return dict(signed_cosine=max(-1.,min(1.,dot/math.sqrt(rr*yy))),nonnegative_scale=scale,relative_rmse=err,configuration_assignment='not_assigned',evidence='caller-supplied arrays only; no hidden identity or automatic absolute-configuration inference')
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _start(self,kind):
  self._idle()
  if self.plant is None:raise ActionRejected('PLANT_REQUIRED')
  if self.sample is None or self.method is None:raise ActionRejected('SAMPLE_METHOD_REQUIRED')
  if not self.powered or self.opened or not all(self.plant[q] for q in ['detector_ready','purge_ready']):raise ActionRejected('OPTICS_NOT_READY')
  if self.plant['temperature_installed'] and not self.plant['water_ready']:raise ActionRejected('COOLING_WATER_REQUIRED')
  if abs(self.temperature-self.temperature_target)>.1:raise ActionRejected('TEMPERATURE_NOT_SETTLED')
  if abs(self.stage_position)>1e-12 or abs(self.stage_target)>1e-12:raise ActionRejected('CENTER_POSITION_REQUIRED')
  if abs(self.filter_angle-self.filter_target)>1e-12:raise ActionRejected('FILTER_NOT_SETTLED')
  axis=_grid(self.method);low,high=(85000.,200000.) if self.filter_selection=='low' else (200000.,320000.)
  if axis[0]<low or axis[-1]>high:raise ActionRejected('FILTER_WINDOW')
  if self.path['window_material']=='CaF2' and axis[0]<110000:raise ActionRejected('CAF2_WINDOW')
  self._conditions(self.sample)
  if (kind=='blank')!=(self.sample['kind']=='blank'):raise ActionRejected('WRONG_SAMPLE_KIND')
  if kind=='sample' and not self._blank_valid():raise ActionRejected('MATCHED_BLANK_REQUIRED')
  duration=self.method['accumulation_s'];dt=self.plant['frame_duration_s']
  if math.ceil(duration/dt)>1000000 or len(axis)*math.ceil(duration/dt)>100000000:raise ActionRejected('EVENT_RESOURCE_LIMIT')
  expected_ir,expected_vcd,provenance=self._response(kind,axis)
  self.kind=kind;self.phase='running';self.warning='';self.elapsed_s=0.;self._run_method=copy.deepcopy(self.method);self._run_path=copy.deepcopy(self.path);self._run_provenance=provenance;self._run_fingerprint=copy.deepcopy(self._fingerprint())
  self._axis=axis;self._expected_ir=expected_ir;self._expected_vcd=expected_vcd;self._mean_ir=np.zeros(len(axis));self._mean_vcd=np.zeros(len(axis));self._frames=0;self._sampled_s=0.;self._last_sample_time=None;self._started=self.time_s
  self._background_ir=np.array(self._blank['result']['ir_absorbance']) if kind=='sample' else np.zeros(len(axis));self._background_vcd=np.array(self._blank['result']['vcd_delta_absorbance']) if kind=='sample' else np.zeros(len(axis))
  self._next_frame=self.time_s+min(dt,duration);self._op_count+=1;self.operation_id=self.instance_id+':op:'+str(self._op_count)
  if kind=='blank':self._blank=None
  return {'phase':self.phase,'operation_id':self.operation_id}
 def _stop(self,why):
  if self.phase=='running':self.phase='cancelled';self.warning=why;self._next_frame=None
 def _advance(self,dt):
  if not dt:return
  p=self.plant
  if p:
   def approach(x,target,speed):return target if abs(target-x)<=speed*dt else x+math.copysign(speed*dt,target-x)
   if self.powered:
    self.filter_angle=approach(self.filter_angle,self.filter_target,p['filter_speed_rad_s']);self.stage_position=approach(self.stage_position,self.stage_target,p['stage_speed_m_s'])
   controlled=self.powered and p['temperature_installed'] and p['water_ready'];eq=self.temperature_target if controlled else p['ambient_temperature_k']
   change=(eq-self.temperature)*(-math.expm1(-p['thermal_conductance_w_k']*dt/p['thermal_capacity_j_k']));self.temperature+=change
   heat=p['thermal_capacity_j_k']*change
   if controlled:self._controlled_heat+=heat
   else:self._ambient_heat+=heat
   if self.phase=='running':self._mirror_phase=(self._mirror_phase+dt/p['frame_duration_s'])%1.
  if self.phase=='running':self.elapsed_s=min(self._run_method['accumulation_s'],self.elapsed_s+dt)
  self.time_s+=dt
 def _frame(self):
  duration=self._run_method['accumulation_s'];sampled=min(duration,(self._frames+1)*self.plant['frame_duration_s'])
  if abs(duration-sampled)<=4*math.ulp(duration):sampled=duration
  dt=sampled-self._sampled_s
  raw_a=self._expected_ir+self._rng.normal(0,self.plant['ir_noise_sd_1s']/math.sqrt(dt),len(self._axis));raw_d=self._expected_vcd+self._rng.normal(0,self.plant['vcd_noise_sd_1s']/math.sqrt(dt),len(self._axis))
  weight=dt/sampled;self._mean_ir+=weight*(raw_a-self._background_ir-self._mean_ir);self._mean_vcd+=weight*(raw_d-self._background_vcd-self._mean_vcd)
  self._sampled_s=sampled;self._frames+=1;self._last_sample_time=self.time_s
  if sampled>=duration:
   self.phase='succeeded';self.elapsed_s=duration;self._next_frame=None
   if self.kind=='blank':self._blank={'fingerprint':copy.deepcopy(self._run_fingerprint),'result':self._result()}
  else:self._next_frame=self._started+min(duration,(self._frames+1)*self.plant['frame_duration_s'])
 def step(self,dt_s):
  validate(number('s',0,86400),dt_s)
  if not dt_s:return
  target=self.time_s+dt_s
  if not math.isfinite(target):raise ActionRejected('INVALID_ARGUMENT')
  while self.phase=='running' and self._next_frame is not None and self._next_frame<=target+4*math.ulp(target):
   event=self._next_frame;target=max(target,event);self._advance(max(0.,event-self.time_s));self.time_s=event;self._frame()
  self._advance(max(0.,target-self.time_s));self.time_s=target;self.revision+=1;self._archive_operation()
 def _operation_snapshot(self):
  observation=self.session().read(instance_id=self.instance_id)
  return dict(protocol_version=PROTOCOL_VERSION,instance_id=self.instance_id,operation_id=self.operation_id,status=self.phase,state_revision=self.revision,clock={'domain':'simulation','time_s':self.time_s},result=self._result(),observations=observation['observations'],error=None)
 def _archive_operation(self):
  if self.operation_id and self.phase in ['succeeded','cancelled','failed'] and self.operation_id not in self._operation_records:self._operation_records[self.operation_id]=copy.deepcopy(self._operation_snapshot())


class JascoFvs6000Session:
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
        m=export_jasco_fvs6000_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.1',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        def timing(key):
            if key in ['scan.ir_center','scan.vcd_center'] and h._last_sample_time is not None:return h._last_sample_time,[h._started,h._last_sample_time]
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
'590x670x313mm and65kg describe the documented body only. Internal dimensions/strokes,0/π filter positions and external telemetry are explicit engineering geometry, not OEM CAD or GUI.',
'Standard MCT-V range85000–320000m^-1 uses two separately selected segments85000–200000 and200000–320000m^-1. Six-position wheel has only two standard installed filters; four unused slots are not extra optical ranges.',
'Resolution50–1600m^-1 is distinct from grid spacing. Additional Gaussian variance broadens documented source resolution; no deconvolution. Unknown source resolution stays unknown, smoothing is a host proxy; finite source edges use nearest continuation only inside smoothing, exported axis never extrapolates.',
'Only four public measured VCD peptides; remaining real IR shapes do not supply real VCD. Missing46 real VCD substances remain partial. No source spectra were measured on this simulated FVS-6000.',
'PMA50 output supports an explicit inferred IR/DeltaA unit interpretation for headerless peptide files. Physical replay requires trusted sample accept_inferred_units; original unit evidence and unknown measured temperature remain visible. No arbitrary multiplier or invented298.15K source temperature.',
'Measured replay requires original known solvent, concentration, path and window and source resolution limit; unknown temperature needs explicit acceptance. It is not a validated temperature-dependent or concentration-dependent FVS response.',
'Normalized NIST IR only supplies shape. Explicit host amplitude and DeltaA/A constant are synthetic; source gas/solution/solid phase is not reinterpreted as a measured solution response. No inferred molar absorptivity or absolute configuration.',
'Host blank IR/bias and independent Gaussian white noise are assumptions. Noise_sd_1s/sqrt(frame duration) and duration-weighted frame means model only added noise; fixed noise in measured templates cannot be averaged away. OEM<8e-6 DeltaAbs applies only400m^-1 and1200s; unknown statistic/distribution is not used as a universal noise calibration.',
'Whole-spectrum integration frames represent already demodulated FTIR/VCD. Apparent transmittance=10^(-(A±DeltaA/2)) of observed blank-corrected means; these are not raw detector photons and may exceed1 for negative apparent absorption. Interferograms, PEM lock-in, polarization imperfections and Fourier reconstruction are unsupported.',
'Liquid path25–100µm; BaF2 with aqueous samples rejects, CaF2 below110000m^-1 rejects. No solvent optical constants, saturation calibration, absorption heating, precipitation, evaporation or sample consumption is modeled.',
'TCH option268.15–363.15K uses explicit host C/H and ideal thermal equilibrium, not measured OEM settling. Energy closure applies only this thermal lump. Optical power, PEM dissipation and full instrument energy balance remain not_run.',
'Three-position stage is an optional finite motion proxy; this configuration loads one center cell and leaves side holders empty. Acquisition requires center position; no automatic three-sample material transfer.',
'All clocks advance only in step. Cover handling is trusted external scene authority; sample load/unload conserves intact liquid volume and solute. Detector/purge/water readiness is a host declaration, not measured hardware or certified safety.',
'Completed frame data survive cancellation. Partial blank never becomes a usable background. Completed blank matches grid/resolution/path/filter/solvent/temperature target; its accumulation time may differ explicitly from sample. Power off clears blank.',
'Current results keep acquisition method/path snapshots. Owned terminal poll keeps complete public spectrum and averaged sample windows across later operations; cancellation of historical terminal operations cannot stop a new one. Data caches/roles are process-local, not restart persistence or operating-system security.',
'Reference comparison uses only caller-supplied same-grid signed arrays, returns cosine/nonnegative scale/residual with configuration_assignment=not_assigned. No automatic alignment, uncertainty, stereoisomer matching or hidden identity read.',
'Robot contact, inverse joint authority, OEM drivers, hardware/HIL, factory noise/IRF calibration, real cooling/purge/LN2 behavior and absolute-configuration validation remain not_run.'
]
def export_jasco_fvs6000_contract_metadata():
 caps={};slots={}
 measured=['scan.frames','scan.ir_center','scan.vcd_center','scan.last_sample_time_s','scan.progress']
 stopped=['scan.phase','safety.warning']
 immediate={
  'plant.configure':['plant.configured','temperature.target_k','temperature.actual_k','detector.ready','purge.ready','water.ready','truth.ledger'],
  'sample.load':['sample.loaded','blank.valid','truth.ledger'],'sample.unload':['sample.loaded','blank.valid','truth.ledger'],
  'cover.set':['cover.opened','cover.lift_m',*stopped],'power.set':['power.enabled','blank.valid',*stopped],
  'path.configure':['path.length_m','path.window_material','blank.valid'],'filter.select':['filter.selection','filter.target_angle_rad','blank.valid'],
  'stage.select':['sample.target_position_m'],'temperature.set':['temperature.target_k','blank.valid'],
  'method.configure':['method.resolution_m_inv','method.accumulation_s','method.center_wavenumber_m_inv','blank.valid'],
  'blank.start':['scan.phase','scan.kind','scan.elapsed_s',*measured,'safety.warning','blank.valid'],
  'acquisition.start':['scan.phase','scan.kind','scan.elapsed_s',*measured,'safety.warning'],
  'acquisition.stop':stopped,
 }
 on_step={'filter.select':['filter.angle_rad'],'stage.select':['sample.position_m'],'temperature.set':['temperature.actual_k','truth.ledger'],'power.set':['temperature.actual_k','truth.ledger','filter.angle_rad','sample.position_m']}
 for k in ['blank.start','acquisition.start']:on_step[k]=['scan.phase','scan.elapsed_s',*measured,'interferometer.position_m','temperature.actual_k','truth.ledger']
 completion={'blank.start':['scan.phase','blank.valid'],'acquisition.start':['scan.phase']}
 observed={'plant.configure':['plant.configured'],'sample.load':['sample.loaded'],'sample.unload':['sample.loaded'],'cover.set':['cover.opened'],'power.set':['power.enabled'],'filter.select':['filter.selection'],'stage.select':['sample.target_position_m'],'temperature.set':['temperature.target_k'],'blank.start':['scan.phase'],'acquisition.start':['scan.phase'],'acquisition.stop':['scan.phase'],'process.measure':['scan.phase','scan.kind','scan.elapsed_s',*measured,'safety.warning'],'result.read':['scan.phase','scan.kind',*measured],'result.export':['scan.phase','scan.kind',*measured],'blank.read':['blank.valid'],'product.read':['truth.ledger']}
 for k,(s,u,o,v,c,w) in SLOTS.items():
  mean=k in ['scan.ir_center','scan.vcd_center']
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'JascoFvs6000Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='interval_mean' if mean else 'instant',time_domain='simulation',window_description='当前全谱积累从开始到最近完成帧的时间窗口，按帧持续时间加权；取消不补造未完成帧' if mean else None),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','INVALID_REFERENCE','INITIALIZATION_ONLY','EVIDENCE_ACK_REQUIRED','INFERRED_UNITS_ACK_REQUIRED','UNKNOWN_CONDITIONS_ACK_REQUIRED','BUSY','PLANT_REQUIRED','SAMPLE_REQUIRED','SAMPLE_METHOD_REQUIRED','COVER_MUST_BE_OPEN','CENTER_POSITION_REQUIRED','UNLOAD_REQUIRED','SAMPLE_COMPOSITION','BAF2_WATER_INCOMPATIBLE','CAF2_WINDOW','SOURCE_CONDITIONS_MISMATCH','NORMALIZED_IR_REQUIRED','MEASURED_VCD_REQUIRED','SPECTRAL_GRID','SOURCE_WAVENUMBER_RANGE','SOURCE_RESOLUTION_LIMIT','EVENT_RESOURCE_LIMIT','OPTION_NOT_INSTALLED','COOLING_WATER_REQUIRED','OPTICS_NOT_READY','TEMPERATURE_NOT_SETTLED','FILTER_NOT_SETTLED','FILTER_WINDOW','WRONG_SAMPLE_KIND','MATCHED_BLANK_REQUIRED','ABSORBANCE_RANGE','ANALYSIS_INPUT','ANALYSIS_RANGE']
 for k,(inp,out,c,z,kind,vis,ex,_) in CAPS.items():
  async_=k in ['blank.start','acquisition.start'];query=kind in ['query','analysis'];unsupported=k in UNSUPPORTED
  effects=[dict(state_id=state,timing=timing,description_zh={'immediate':'经验证的配置、外部装卸或采集初始化/取消；不补采帧，不推进时间','on_step':'仅后续宿主step推进有限运动、热学代理或完成全谱积分帧','on_completion':'全部指定积累完成后置终态，空白此时才可使用'}[timing]) for timing,mapping in [('immediate',immediate),('on_step',on_step),('on_completion',completion)] for state in mapping.get(k,[])]
  caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.jasco_fvs6000.JascoFvs6000Session.invoke',invocation=f'session.invoke(instance_id="fvs6000_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if query or unsupported else [dict(description_zh='检查宿主权限、来源条件、已匹配空白、窗口、闭盖、供电/冷却/吹扫和选件位置；不满足原子拒绝',state_ids=['power.enabled','cover.opened','blank.valid','scan.phase'],enforced_by='runtime',check='Runtime._invoke and host-assigned principal Session')],state_effects=[] if query or unsupported else effects,observes=[] if unsupported else observed.get(k,[]),model_effects=dict(mode='none',description_zh='场景层单独验证；查询无动作',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='指定积累时间内全部全谱积分帧完成' if async_ else '返回事务，不推进时钟',completion_state_ids=['scan.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','source_templates','request_cache','whole_spectrum_frames'],partial_failure_zh='开盖/断电/取消保留已完成帧；部分空白不使用；历史终态完整公开谱及时间窗归原调用者所有，后续方法和新采集不覆盖；原子拒绝不留副作用'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=LIMITATIONS,source_ids=['manufacturer_primary','manufacturer_formula','simulation_assumption'])
 mech=dict(component_ids=['ir_source','michelson_beamsplitter','interferometer_mirror','filter_wheel','polarizer','pem','sample_cell','mct_detector'],state_ids=list(slots),meaning_zh='条件化全谱IR/VCD积累、空白扣除、有限工程机构与显式热学代理；不是直接对隐藏构型返回标签',implementation='lumped',equations=[
 'DeltaA = A_L - A_R; A=(A_L+A_R)/2; apparent T_L/R=10^(-(A±DeltaA/2)).',
 'Measured reference uses original IR and VCD axes only within their intersection, and preserves source resolution, conditions and inferred-unit evidence; no concentration scaling.',
 'Synthetic IR A=normalized_IR*host_peak_absorbance*(path/100um); synthetic DeltaA=host_ratio*A; concentration is inventory only, not inferred molar absorptivity.',
 'Additional spectral Gaussian FWHM=sqrt(max(0,method_resolution^2-source_resolution^2)); unknown source width is not recovered.',
 'Frame noise_sd = host_noise_sd_1s/sqrt(frame_duration_s); observed full-spectrum mean = sum(frame_duration*frame_observation)/sum(frame_duration). Sample result subtracts the completed matching observed blank.',
 'Temperature_next=equilibrium+(temperature-equilibrium)*exp(-H*dt/C); Q=C*(T_next-T); thermal ledger Q_controlled+Q_ambient=C*(T-T_initial).',
 'Registered_volume=current_volume+removed_volume; registered_solute=current_volume*concentration+removed_solute; no sample consumption.',
 'Filter and optional stage move at explicit finite host speeds; mirror triangular scene phase advances only during acquisition, not OEM travel inferred from resolution.',
 'Caller reference signed cosine=(y dot r)/(|y||r|), nonnegative scale=max(0,y dot r/(r dot r)); normalized coordinates guard overflow, configuration remains not_assigned.'
 ],parameters={},source_ids=['manufacturer_primary','manufacturer_formula','vcd_dataset','ir_dataset','simulation_assumption'],limitations=LIMITATIONS)
 spec=importlib.util.spec_from_file_location('_fvs6000_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
 ann.annotate_export(SLUG,caps,slots,{'ftir_vcd_measurement':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'path.configure':(PATH,PATH_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'；非原厂校准。'
 mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'ftir_vcd_measurement':mech})

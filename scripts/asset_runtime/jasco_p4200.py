"""JASCO P-4200: measured reference specific rotations, explicitly idealized polarimetry."""
from __future__ import annotations
import copy,hashlib,importlib.util,json,math
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2];SLUG='jasco_p4200';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-070';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/jasco_p4200_rotation_library/templates.json'
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
PLANT=obj({'ambient_k':number('K',288.15,313.15),'heat_capacity_j_k':number('J/K',1,1e5),'thermal_ua_w_k':number('W/K',.001,100),'control_gain_w_k':number('W/K',.01,1000),'peltier_limit_w':number('W',.1,1000),'instrument_gain':number('1',.5,1.5),'zero_offset_rad':number('rad',-.05,.05),'detector_dark':number('1',0,1),'noise_density_sqrt_s':number('s^0.5',0,.01),'seed':integer(0,2147483647),'accept_uncalibrated_model':BOOL})
SAMPLE=obj({'sample_id':TEXT,'kind':dict(type='string',enum=['blank','sample','quartz']),'template_id':TEXT,'solvent':TEXT,'volume_m3':number('m^3',0,2e-6),'concentration_kg_m3':number('kg/m^3',0,1000),'path_m':{'type':'number','enum':[.1],'x-unit':'m'},'reference_temperature_k':number('K',288.15,313.15),'transmittance':number('1',.01,1),'quartz_angle_rad':nullable(number('rad',-1.3962634016,1.3962634016)),'accept_missing_source_concentration':BOOL,'accept_ideal_quartz':BOOL})
METHOD=obj({'lamp':{'type':'string','enum':['Na']},'wavelength_m':{'type':'number','enum':[589e-9],'x-unit':'m'},'integration_s':number('s',1,100),'repeats':integer(1,100),'declared_concentration_kg_m3':number('kg/m^3',.001,1000),'declared_path_m':number('m',.001,.1)})
PLANT_EXAMPLE=dict(ambient_k=293.15,heat_capacity_j_k=40.,thermal_ua_w_k=.5,control_gain_w_k=20.,peltier_limit_w=12.,instrument_gain=1.02,zero_offset_rad=.001,detector_dark=.01,noise_density_sqrt_s=0.,seed=70,accept_uncalibrated_model=True)
SAMPLE_EXAMPLE=dict(sample_id='sample_01',kind='sample',template_id='pubchem_5988',solvent='water',volume_m3=1e-6,concentration_kg_m3=100.,path_m=.1,reference_temperature_k=293.15,transmittance=.8,quartz_angle_rad=None,accept_missing_source_concentration=True,accept_ideal_quartz=False)
BLANK_EXAMPLE=dict(SAMPLE_EXAMPLE,sample_id='blank_01',kind='blank',template_id='',concentration_kg_m3=0.)
QUARTZ_EXAMPLE=dict(SAMPLE_EXAMPLE,sample_id='quartz_01',kind='quartz',template_id='',solvent='',volume_m3=0.,concentration_kg_m3=0.,quartz_angle_rad=math.radians(10),accept_ideal_quartz=True)
METHOD_EXAMPLE=dict(lamp='Na',wavelength_m=589e-9,integration_s=1.,repeats=3,declared_concentration_kg_m3=100.,declared_path_m=.1)
FAST_PLANT=copy.deepcopy(PLANT_EXAMPLE)
POINT=obj({'raw_rotation_rad':number('rad'),'rotation_rad':nullable(number('rad')),'photometric_channels':arr(number('1'),4),'sample_time_s':number('s',0),'integration_s':number('s',1,100),'elapsed_s':number('s',0),'temperature_k':number('K'),'detector_sigma_rad':number('rad',0)})
MEASURE=obj({'phase':PHASE,'rotation_rad':nullable(number('rad')),'raw_rotation_rad':nullable(number('rad')),'elapsed_s':number('s',0),'completed_points':integer(0,100),'warning':TEXT})
RESULT=obj({'phase':PHASE,'kind':TEXT,'complete':BOOL,'points':arr(POINT),'settings_json':TEXT,'template_source':TEXT,'warning':TEXT,'evidence':TEXT,'limitations':arr(TEXT)})
SPECIFIC=obj({'mean_rotation_rad':number('rad'),'specific_rotation_rad_m2_kg':number('rad*m^2/kg'),'repeat_sd_rad':nullable(number('rad')),'count':integer(1,100),'evidence':TEXT})
CONCENTRATION=obj({'concentration_kg_m3':number('kg/m^3',0),'evidence':TEXT})
LEDGER=obj({**{k:number(u) for k,u in [('registered_volume_m3','m^3'),('attached_volume_m3','m^3'),('external_volume_m3','m^3'),('volume_residual_m3','m^3'),('registered_solute_kg','kg'),('attached_solute_kg','kg'),('external_solute_kg','kg'),('mass_residual_kg','kg'),('thermal_delta_j','J'),('controller_energy_j','J'),('ambient_loss_j','J'),('thermal_residual_j','J')]},'active_template_id':TEXT,'evidence':TEXT})
TEMPLATE=obj({'substance_id':TEXT,'name':TEXT,'specific_rotation_rad_m2_kg':number('rad*m^2/kg'),'conditions_json':TEXT,'source_json':TEXT,'evidence':TEXT})
CAPS={}
def cap(k,inp,out,c,z,kind='device_command',vis='agent',ex=None):CAPS[k]=[inp,out,c,z,kind,vis,{} if ex is None else ex,[]]
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body','detector'],'宿主一次性声明传递增益/零偏/噪声/热参数；均非原厂辨识','configuration','evaluator',PLANT_EXAMPLE)
cap('sample.register',SAMPLE,obj({'registered':BOOL}),['sample_holder'],'登记已装池溶液或显式理想石英参考板；真实文献条件须匹配','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.load',obj({'sample_id':TEXT}),obj({'loaded':BOOL,'attachment_event':TEXT}),['sample_holder','quartz_cell'],'开盖逻辑挂接完整预装池或标准板；非机器人','device_command','evaluator',{'sample_id':'sample_01'})
cap('sample.unload',obj({'recipient_id':TEXT}),obj({'unloaded':BOOL,'attachment_event':TEXT}),['sample_holder','quartz_cell'],'开盖完整卸载；体积和溶质质量不消耗','device_command','evaluator',{'recipient_id':'external_rack'})
cap('method.configure',METHOD,obj({'configured':BOOL}),['sodium_lamp','analyzer','detector'],'标准Na589nm；积分1–100s，重复1–100为宿主限制；声明c/l仅用于分析','configuration','agent',METHOD_EXAMPLE)
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_face','power_inlet'],'逻辑供电，断电取消/熄灯/失效校准；无按钮接触',ex={'enabled':True})
cap('lid.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['sample_lid'],'工程90°掀盖；开盖终止采集并关闭灯门控',ex={'opened':False})
cap('lamp.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['sodium_lamp'],'标准Na灯逻辑光路门控；无灯预热/寿命模型',ex={'enabled':True})
cap('thermal.set_temperature',obj({'temperature_k':number('K',288.15,313.15)}),obj({'temperature_k':number('K')}),['peltier','sample_holder'],'标准温控15–40°C集总模型；设置不瞬时改变温度',ex={'temperature_k':293.15})
cap('acquisition.start',obj({'kind':dict(type='string',enum=['blank','sample','quartz'])}),obj({'phase':PHASE,'operation_id':TEXT}),['quartz_cell','analyzer','detector'],'先分析器有限速率就位，再完整积分；样品须匹配空白和石英校准',ex={'kind':'sample'})
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['sodium_lamp'],'取消保留已完成点与姿态/时间，不接受不完整校准')
cap('process.measure',obj({}),MEASURE,['host_screen'],'只读最后已完成积分点；没有测量时null','query')
cap('result.read',obj({}),RESULT,['host_screen'],'参考比旋光度驱动的模拟角度时序；不是原始测量文件','query')
cap('calibration.quartz',obj({'certified_angle_rad':number('rad',-1.3962634016,1.3962634016),'accept_simulated_standard':BOOL}),obj({'calibrated':BOOL,'gain_estimate':number('1',.1,10)}),['analyzer','detector'],'完成石英观测减空白后与调用者标准角比较；不读隐藏增益/真值',ex={'certified_angle_rad':math.radians(10),'accept_simulated_standard':True})
cap('quantify.specific',obj({'rotation_rad':arr(number('rad')),'declared_concentration_kg_m3':number('kg/m^3',.001,1000),'declared_path_m':number('m',.001,.1)}),SPECIFIC,['host_screen'],'只用观测平均角/声明c/l求比旋光度，重复SD非总不确定度','analysis','agent',{'rotation_rad':[math.radians(6.637)]*3,'declared_concentration_kg_m3':100.,'declared_path_m':.1})
cap('quantify.concentration',obj({'rotation_rad':number('rad'),'reference_specific_rotation_rad_m2_kg':number('rad*m^2/kg'),'declared_path_m':number('m',.001,.1),'same_conditions_confirmed':BOOL}),CONCENTRATION,['host_screen'],'观测角/调用者参考比旋光度及光程反算；条件匹配调用者确认','analysis','agent',{'rotation_rad':math.radians(6.637),'reference_specific_rotation_rad_m2_kg':66.37*math.pi/18000,'declared_path_m':.1,'same_conditions_confirmed':True})
cap('template.read',obj({'substance_id':TEXT}),TEMPLATE,['host_screen'],'公开文献参数，当前样品选择仅evaluator可见','query','agent',{'substance_id':'pubchem_5988'})
cap('product.read',obj({}),LEDGER,['sample_holder'],'预装池体积/溶质质量和独立温控能量私有账本','query','evaluator')
cap('ord.scan',obj({}),obj({}),['sodium_lamp'],'其他灯源/手动滤片选件未装，ORD光谱未实现','analysis')
cap('optical_purity.absolute',obj({}),obj({}),['detector'],'混合物/变旋/对映体绝对纯度未验证，不把角度比无条件称ee','analysis')
UNSUPPORTED={'ord.scan','optical_purity.absolute'}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='host_screen',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','显式宿主参数已配置','setpoint',c='body')
slot('method.configured',BOOL,'1','D线积分方法已设置','setpoint')
slot('power.enabled',BOOL,'1','软件供电','setpoint',c='power_face',w=('power.set','enabled'))
slot('lid.opened',BOOL,'1','样品盖开闭','scene',c='sample_lid',w=('lid.set','opened'))
slot('lid.angle_rad',number('rad',0,math.pi/2),'rad','工程盖角，轴-X','scene',c='sample_lid')
slot('lamp.enabled',BOOL,'1','软件Na灯光路门控','setpoint',c='sodium_lamp',w=('lamp.set','enabled'))
slot('sample.loaded',BOOL,'1','预装池逻辑挂接','scene',c='quartz_cell')
slot('thermal.setpoint_k',nullable(number('K',288.15,313.15)),'K','标准Peltier温度目标','setpoint',c='peltier',w=('thermal.set_temperature','temperature_k'))
slot('thermal.temperature_k',number('K'),'K','样品夹持器等温集总估计','derived',c='sample_holder')
slot('blank.ready',BOOL,'1','同光路/T空白就绪，液体另检查溶剂/光程')
slot('calibration.ready',BOOL,'1','观测石英比例校准就绪')
slot('calibration.gain',nullable(number('1')),'1','观测估计增益，不是隐藏增益')
slot('scan.phase',PHASE,'1','采集生命周期')
slot('scan.stage',dict(type='string',enum=['idle','slewing','integrating']),'1','有限转速/积分阶段')
slot('scan.elapsed_s',number('s',0),'s','本次采集时间，含分析器就位')
slot('scan.completed_points',integer(0,100),'1','完整积分次数')
slot('scan.rotation_rad',nullable(number('rad')),'rad','校准后最后观测角，空白/石英未校准时null')
slot('scan.raw_rotation_rad',nullable(number('rad')),'rad','四相光度反算原始角，非私有真角')
slot('optics.analyzer_rad',number('rad',-math.pi/2,math.pi/2),'rad','由理想光度相位驱动的分析器姿态代理','scene',c='analyzer')
slot('method.wavelength_m',nullable(number('m')),'m','标准Na标称589nm','setpoint',c='sodium_lamp')
slot('method.integration_s',nullable(number('s')),'s','每次积分时间','setpoint',c='detector')
slot('safety.warning',TEXT,'1','不含私有样品身份的告警')
slot('truth.ledger',LEDGER,'1','预装池物料/真实模板身份与热量账本','process_truth','evaluator','sample_holder')
class JascoP4200Runtime:
 def __init__(self,instance_id='p4200_01'):
  self.instance_id=instance_id;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.time_s=0.;self.revision=0;self.plant=None;self.method=None;self.samples={};self.active=None
  self.powered=False;self.opened=True;self.lamp=False;self.temperature_k=293.15;self.setpoint_k=None;self.controller_energy_j=0.;self.ambient_loss_j=0.
  self.blank=None;self.calibration=None;self.phase='idle';self.stage='idle';self.kind='';self.elapsed_s=0.;self.points=[];self.warning='';self.operation_id='';self._op_count=0
  self.analyzer_rad=0.;self._target_angle=0.;self._remaining=0.;self._signals=np.zeros(4);self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._operation_records={};self._rng=np.random.default_rng(0)
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return JascoP4200Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def debug_snapshot(self):
  v=copy.deepcopy({k:x for k,x in self.__dict__.items() if k not in ['templates','_rng','_signals']});v['_rng_state']=copy.deepcopy(self._rng.bit_generator.state);v['_signals']=self._signals.tolist();return v
 def _sample(self):return self.samples.get(self.active)
 def _ledger(self):
  s=self._sample();vol=sum(v['volume_m3'] for v in self.samples.values());inside=s['volume_m3'] if s else 0.;mass=sum(v['volume_m3']*v['concentration_kg_m3'] for v in self.samples.values());insidemass=s['volume_m3']*s['concentration_kg_m3'] if s else 0.;delta=self.plant['heat_capacity_j_k']*(self.temperature_k-self.plant['ambient_k']) if self.plant else 0.
  return dict(registered_volume_m3=vol,attached_volume_m3=inside,external_volume_m3=vol-inside,volume_residual_m3=vol-inside-(vol-inside),registered_solute_kg=mass,attached_solute_kg=insidemass,external_solute_kg=mass-insidemass,mass_residual_kg=mass-insidemass-(mass-insidemass),thermal_delta_j=delta,controller_energy_j=self.controller_energy_j,ambient_loss_j=self.ambient_loss_j,thermal_residual_j=delta-self.controller_energy_j+self.ambient_loss_j,active_template_id=s['template_id'] if s else '',evidence='intact prefilled cell relocation, no reaction/evaporation; ideal quartz has no declared solution; lumped thermal energy only, not full lamp/electrical energy')
 def _optical_key(self):return (self.method['lamp'],self.method['wavelength_m'],self.setpoint_k) if self.method else None
 def _blank_ok(self):
  if not self.blank or not self.active or self.blank['key']!=self._optical_key():return False
  s=self._sample()
  return s['kind']=='quartz' or (s['solvent']==self.blank['solvent'] and s['path_m']==self.blank['path_m'] and s['reference_temperature_k']==self.blank['reference_temperature_k'])
 def _cal_ok(self):return bool(self.calibration and self.calibration['key']==self._optical_key())
 def read(self,visibility='agent'):
  if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
  p=self.points[-1] if self.points else {};m=self.method
  v={'plant.configured':bool(self.plant),'method.configured':bool(m),'power.enabled':self.powered,'lid.opened':self.opened,'lid.angle_rad':math.pi/2 if self.opened else 0.,'lamp.enabled':self.lamp,'sample.loaded':bool(self.active),'thermal.setpoint_k':self.setpoint_k,'thermal.temperature_k':self.temperature_k,'blank.ready':self._blank_ok(),'calibration.ready':self._cal_ok(),'calibration.gain':self.calibration['gain'] if self._cal_ok() else None,'scan.phase':self.phase,'scan.stage':self.stage,'scan.elapsed_s':self.elapsed_s,'scan.completed_points':len(self.points),'scan.rotation_rad':p.get('rotation_rad'),'scan.raw_rotation_rad':p.get('raw_rotation_rad'),'optics.analyzer_rad':self.analyzer_rad,'method.wavelength_m':m['wavelength_m'] if m else None,'method.integration_s':m['integration_s'] if m else None,'safety.warning':self.warning,'truth.ledger':self._ledger()}
  return copy.deepcopy({k:x for k,x in v.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _operation_snapshot(self):
  angles=self.session().read(instance_id=self.instance_id,state_ids=['scan.rotation_rad','scan.raw_rotation_rad'])['observations']
  return dict(operation_id=self.operation_id,status=self.phase,result=copy.deepcopy(self._invoke('process.measure',{})),observations=angles)
 def _archive_operation(self):
  if self.operation_id and self.phase in ('succeeded','cancelled','failed') and self.operation_id not in self._operation_records:
   self._operation_records[self.operation_id]=self._operation_snapshot()
 def _clear_result(self):
  self._archive_operation();self.points=[];self.phase='idle';self.stage='idle';self.kind='';self.elapsed_s=0.;self.warning=''
 def _invalidate(self):self.blank=None;self.calibration=None;self._clear_result()
 def _stop(self,phase,warning):
  if self.phase=='running':self.phase=phase
  self.stage='idle';self.lamp=False;self.warning=warning;self._archive_operation()
 @staticmethod
 def demodulate(signals):
  a=np.asarray(signals,dtype=float)
  if a.shape!=(4,) or not np.all(np.isfinite(a)):raise ActionRejected('INVALID_ARGUMENT')
  u=float(a[1]-a[3]);q=float(a[0]-a[2])
  if math.hypot(u,q)<1e-10:raise ActionRejected('LOW_POLARIZED_SIGNAL')
  return .5*math.atan2(u,q)
 def _signals_for_sample(self):
  s=self._sample();p=self.plant
  theta=0. if s['kind']=='blank' else s['quartz_angle_rad'] if s['kind']=='quartz' else self.templates[s['template_id']]['specific_rotation_rad_m2_kg']*s['concentration_kg_m3']*s['path_m']
  raw=p['instrument_gain']*theta+p['zero_offset_rad']
  # +/-90° equivalent under Malus; refuse the ambiguous endpoint and all out-of-branch angles.
  if abs(theta)>=math.pi/2-1e-8 or abs(raw)>=math.pi/2-1e-8:raise ActionRejected('ANGLE_BRANCH_RANGE')
  psi=np.arange(4)*math.pi/4
  signals=s['transmittance']*np.cos(psi-raw)**2+p['detector_dark']
  return signals,self.demodulate(signals)
 def invoke(self,capability_id,**arguments):
  if capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability_id][0],arguments)
  if capability_id in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  if CAPS[capability_id][4] in ['query','analysis']:return copy.deepcopy(self._invoke(capability_id,copy.deepcopy(arguments)))
  old={k:copy.deepcopy(v) for k,v in self.__dict__.items() if k!='templates'};templates=self.templates
  try:result=self._invoke(capability_id,copy.deepcopy(arguments));self.revision+=1;return copy.deepcopy(result)
  except Exception:self.__dict__.clear();self.__dict__.update(old);self.templates=templates;raise
 def _invoke(self,k,a):
  if k=='template.read':
   r=self.templates.get(a['substance_id'])
   if r is None:raise ActionRejected('UNKNOWN_REFERENCE')
   return {**{q:r[q] for q in ['substance_id','name','specific_rotation_rad_m2_kg','evidence']},'conditions_json':json.dumps(r['conditions'],ensure_ascii=False),'source_json':json.dumps(r['source'],ensure_ascii=False)}
  if k=='product.read':return self._ledger()
  if k=='process.measure':
   p=self.points[-1] if self.points else {};return dict(phase=self.phase,rotation_rad=p.get('rotation_rad'),raw_rotation_rad=p.get('raw_rotation_rad'),elapsed_s=self.elapsed_s,completed_points=len(self.points),warning=self.warning)
  if k=='result.read':return dict(phase=self.phase,kind=self.kind,complete=self.phase=='succeeded',points=self.points,settings_json=json.dumps(self.method),template_source=TEMPLATE_REL+'; current identity restricted to evaluator',warning=self.warning,evidence='reference measured specific rotation parameter; simulated ideal Malus quadrature/servo/integration; blank and quartz simulated, no factory certification',limitations=['Not a measured P4200 trace or proprietary algorithm','Na D at source solvent/temperature and reported concentration only; missing concentration explicitly acknowledged','No noise/thermal/accuracy calibration, no ORD or mutarotation/mixture model','Returned detector_sigma excludes blank/quartz/reference uncertainty; not a total measurement uncertainty','Analysis only from caller observations and declared c/l, no hidden concentration or automatic ee'])
  if k=='quantify.specific':
   values=a['rotation_rad']
   if not 1<=len(values)<=100 or any(abs(v)>=math.pi/2 for v in values):raise ActionRejected('ANALYSIS_INPUT')
   mean=float(np.mean(values));return dict(mean_rotation_rad=mean,specific_rotation_rad_m2_kg=mean/(a['declared_concentration_kg_m3']*a['declared_path_m']),repeat_sd_rad=float(np.std(values,ddof=1)) if len(values)>1 else None,count=len(values),evidence='observed angles and caller-declared concentration/path only; repeat SD excludes systematic/reference uncertainty; correct solvent/T/wavelength required')
  if k=='quantify.concentration':
   alpha=a['reference_specific_rotation_rad_m2_kg']
   if not a['same_conditions_confirmed'] or abs(alpha)<1e-12:raise ActionRejected('REFERENCE_CONDITIONS_MISMATCH')
   concentration=a['rotation_rad']/(alpha*a['declared_path_m'])
   if concentration<0 or abs(a['rotation_rad'])>=math.pi/2:raise ActionRejected('ANALYSIS_INPUT')
   return dict(concentration_kg_m3=concentration,evidence='caller-observed angle / caller reference alpha and path; source solvent/T/wavelength/concentration-dependence conditions caller confirmed; no hidden sample fields')
  if k=='plant.configure':
   if self.plant or self.powered or not a['accept_uncalibrated_model']:raise ActionRejected('INITIALIZATION_ONLY')
   self.plant=a;self.temperature_k=a['ambient_k'];self.setpoint_k=a['ambient_k'];self._rng=np.random.default_rng(a['seed']);return {'configured':True}
  if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
  if self.phase=='running' and k in ['sample.register','sample.load','sample.unload','method.configure','thermal.set_temperature','calibration.quartz']:raise ActionRejected('BUSY')
  if k=='sample.register':
   if a['sample_id'] in self.samples or not a['sample_id']:raise ActionRejected('REQUEST_CONFLICT')
   if a['kind']=='quartz':
    if not a['accept_ideal_quartz'] or a['quartz_angle_rad'] is None or abs(a['quartz_angle_rad'])<math.radians(.1) or a['volume_m3'] or a['concentration_kg_m3'] or a['solvent'] or a['template_id']:raise ActionRejected('QUARTZ_ASSUMPTION_REQUIRED')
   else:
    if a['quartz_angle_rad'] is not None or a['volume_m3']<1e-6 or not a['solvent']:raise ActionRejected('CELL_CONFIGURATION')
    if a['kind']=='blank':
     if a['template_id'] or a['concentration_kg_m3']:raise ActionRejected('INVALID_ARGUMENT')
    else:
     if a['template_id'] not in self.templates or a['concentration_kg_m3']<=0:raise ActionRejected('UNKNOWN_REFERENCE')
     r=self.templates[a['template_id']]['conditions'];c=r['concentration_kg_m3']
     if a['solvent']!=r['solvent']:raise ActionRejected('SOURCE_SOLVENT_MISMATCH')
     if abs(a['reference_temperature_k']-r['temperature_k'])>1e-8:raise ActionRejected('SOURCE_TEMPERATURE_MISMATCH')
     if c is None and not a['accept_missing_source_concentration']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
     if c is not None and abs(c-a['concentration_kg_m3'])>max(1e-8,c*1e-6):raise ActionRejected('SOURCE_CONCENTRATION_MISMATCH')
   self.samples[a['sample_id']]=a;return {'registered':True}
  if k=='sample.load':
   if not self.opened or self.active or a['sample_id'] not in self.samples:raise ActionRejected('SAMPLE_ACCESS_REQUIRED')
   self.active=a['sample_id'];self._clear_result();return dict(loaded=True,attachment_event='host_attach_prefilled_cell_or_quartz')
  if k=='sample.unload':
   if not self.opened or not self.active or not a['recipient_id']:raise ActionRejected('SAMPLE_ACCESS_REQUIRED')
   self.active=None;return dict(unloaded=True,attachment_event='host_detach_intact_cell_or_quartz')
  if k=='method.configure':self.method=a;self._invalidate();return {'configured':True}
  if k=='power.set':
   self.powered=a['enabled']
   if not self.powered:self._stop('cancelled','POWER_OFF');self.blank=None;self.calibration=None
   return a
  if k=='lid.set':
   if a['opened']:self._stop('cancelled','LID_OPEN')
   self.opened=a['opened'];return a
  if k=='lamp.set':
   if a['enabled']:
    if not self.powered or self.opened or not self.method:raise ActionRejected('OPTICS_NOT_READY')
   elif self.phase=='running':self._stop('cancelled','LAMP_OFF')
   self.lamp=a['enabled'];return a
  if k=='thermal.set_temperature':self.setpoint_k=a['temperature_k'];self._invalidate();return a
  if k=='acquisition.stop':self._stop('cancelled','CANCELLED');return {'phase':self.phase}
  if k=='calibration.quartz':
   if self.phase!='succeeded' or self.kind!='quartz' or not self._blank_ok() or not a['accept_simulated_standard'] or abs(a['certified_angle_rad'])<math.radians(.1):raise ActionRejected('QUARTZ_CALIBRATION_REQUIRED')
   estimate=(float(np.mean([p['raw_rotation_rad'] for p in self.points]))-self.blank['zero'])/a['certified_angle_rad']
   if not .1<=estimate<=10:raise ActionRejected('INVALID_CALIBRATION')
   self.calibration=dict(key=self._optical_key(),gain=estimate);return dict(calibrated=True,gain_estimate=estimate)
  if k=='acquisition.start':
   if not self.powered or self.opened or not self.lamp:raise ActionRejected('OPTICS_NOT_READY')
   if self.phase=='running':raise ActionRejected('BUSY')
   if not self.method or not self.active:raise ActionRejected('SAMPLE_METHOD_REQUIRED')
   s=self._sample()
   if s['kind']!=a['kind']:raise ActionRejected('SAMPLE_KIND_MISMATCH')
   if abs(self.temperature_k-s['reference_temperature_k'])>.2 or abs(self.setpoint_k-s['reference_temperature_k'])>1e-8:raise ActionRejected('REFERENCE_TEMPERATURE_MISMATCH')
   if a['kind'] in ['quartz','sample'] and not self._blank_ok():raise ActionRejected('BLANK_REQUIRED')
   if a['kind']=='sample' and not self._cal_ok():raise ActionRejected('QUARTZ_CALIBRATION_REQUIRED')
   signals,target=self._signals_for_sample();self._archive_operation()
   if a['kind']=='blank':self.blank=None;self.calibration=None
   self._signals=signals;self._target_angle=target;self._remaining=abs(target-self.analyzer_rad)/math.radians(6)
   self.stage='slewing' if self._remaining>1e-12 else 'integrating'
   if self.stage=='integrating':self._remaining=self.method['integration_s']
   self.points=[];self.elapsed_s=0.;self.kind=a['kind'];self.phase='running';self.warning=''
   self._op_count+=1;self.operation_id=self.instance_id+':'+str(self._op_count);return dict(phase=self.phase,operation_id=self.operation_id)
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _thermal(self,dt):
  p=self.plant
  if not p:return
  C=p['heat_capacity_j_k'];U=p['thermal_ua_w_k'];K=p['control_gain_w_k'];limit=p['peltier_limit_w'];ambient=p['ambient_k'];remaining=dt
  for _ in range(8):
   if remaining<=1e-13:break
   t=self.temperature_k;control=bool(self.powered);feed=U*(self.setpoint_k-ambient) if control else 0.;demand=K*(self.setpoint_k-t)+feed if control else 0.
   if not control:aa=U*ambient/C;bb=U/C;boundary=None
   elif demand>limit+1e-10:aa=(limit+U*ambient)/C;bb=U/C;boundary=self.setpoint_k+(feed-limit)/K
   elif demand<-limit-1e-10:aa=(-limit+U*ambient)/C;bb=U/C;boundary=self.setpoint_k+(feed+limit)/K
   else:aa=(K+U)*self.setpoint_k/C;bb=(K+U)/C;boundary=None
   equilibrium=aa/bb;span=remaining
   if boundary is not None and abs(t-equilibrium)>1e-15:
    ratio=(boundary-equilibrium)/(t-equilibrium)
    if 0<ratio<1:
     hit=-math.log(ratio)/bb
     if hit>1e-13:span=min(span,hit)
   new=equilibrium+(t-equilibrium)*math.exp(-bb*span);integral=(equilibrium-ambient)*span+(t-equilibrium)*(-math.expm1(-bb*span))/bb
   loss=U*integral;self.ambient_loss_j+=loss;self.controller_energy_j+=C*(new-t)+loss;self.temperature_k=new;remaining-=span
  if remaining>1e-10:raise RuntimeError('thermal branch convergence')
 def _record(self):
  m=self.method;p=self.plant;s=self._sample();sigma=p['noise_density_sqrt_s']/math.sqrt(m['integration_s'])
  signals=self._signals+self._rng.normal(0.,sigma,4)
  try:raw=self.demodulate(signals)
  except ActionRejected:self._stop('failed','LOW_POLARIZED_SIGNAL');return
  # atan2 returns a principal angle: noise can wrap +90 degrees to -90.
  # The settled analyzer pose provides a continuous measured-phase reference.
  if abs(raw-self.analyzer_rad)>=math.pi/2 or abs(raw)>=math.pi/2-1e-8:
   self._stop('failed','ANGLE_BRANCH_RANGE');return
  calibrated=(raw-self.blank['zero'])/self.calibration['gain'] if self._blank_ok() and self._cal_ok() else None
  if calibrated is not None and abs(calibrated)>=math.pi/2:self._stop('failed','ANGLE_BRANCH_RANGE');return
  self.points.append(dict(raw_rotation_rad=raw,rotation_rad=calibrated,photometric_channels=signals.tolist(),sample_time_s=self.time_s,integration_s=m['integration_s'],elapsed_s=self.elapsed_s,temperature_k=self.temperature_k,detector_sigma_rad=math.sqrt(2)*sigma/(2*s['transmittance'])))
  if len(self.points)==m['repeats']:
   self.phase='succeeded';self.stage='idle';self.lamp=False
   if self.kind=='blank':self.blank=dict(key=self._optical_key(),solvent=s['solvent'],path_m=s['path_m'],reference_temperature_k=s['reference_temperature_k'],zero=float(np.mean([x['raw_rotation_rad'] for x in self.points])))
   self._archive_operation()
  else:self._remaining=m['integration_s']
 def step(self,dt_s):
  validate(number('s',0,86400),dt_s)
  if dt_s==0:return
  remaining=dt_s
  while remaining>1e-12:
   dt=min(remaining,max(0.,self._remaining)) if self.phase=='running' else remaining
   if dt>1e-13:
    self._thermal(dt)
    if self.phase=='running':
     if self.stage=='slewing':
      delta=self._target_angle-self.analyzer_rad;self.analyzer_rad+=math.copysign(min(abs(delta),math.radians(6)*dt),delta)
     self.elapsed_s+=dt;self._remaining=max(0.,self._remaining-dt)
    self.time_s+=dt;remaining-=dt
   if self.phase=='running' and abs(self.temperature_k-self._sample()['reference_temperature_k'])>.2:self._stop('failed','REFERENCE_TEMPERATURE_MISMATCH')
   if self.phase=='running' and self._remaining<=1e-12:
    if self.stage=='slewing':self.analyzer_rad=self._target_angle;self.stage='integrating';self._remaining=self.method['integration_s']
    else:self._record()
   if dt<=1e-13 and self.phase!='running':self._thermal(remaining);self.time_s+=remaining;remaining=0.
  self.revision+=1
class JascoP4200Session:
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
        m=export_jasco_p4200_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.1',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='unavailable' if v[k] is None else 'estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=h.points[-1]['sample_time_s'] if k.startswith('scan.') and k in ['scan.rotation_rad','scan.raw_rotation_rad'] and h.points else h.time_s,sample_window_s=[h.points[-1]['sample_time_s']-h.points[-1]['integration_s'],h.points[-1]['sample_time_s']] if k in ['scan.rotation_rad','scan.raw_rotation_rad'] and h.points else None) for k in ids})
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
        return h._operation_snapshot()
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


def export_jasco_p4200_contract_metadata():
 caps={};slots={}
 cleared=['scan.phase','scan.stage','scan.elapsed_s','scan.completed_points','scan.rotation_rad','scan.raw_rotation_rad','safety.warning']
 invalidated=['blank.ready','calibration.ready','calibration.gain',*cleared]
 interrupted=['scan.phase','scan.stage','lamp.enabled','safety.warning']
 immediate={
  'plant.configure':['plant.configured','thermal.setpoint_k','thermal.temperature_k'],
  'sample.register':['truth.ledger'],
  'sample.load':['sample.loaded','blank.ready','truth.ledger',*cleared],
  'sample.unload':['sample.loaded','blank.ready','truth.ledger'],
  'method.configure':['method.configured','method.wavelength_m','method.integration_s',*invalidated],
  'power.set':['power.enabled','blank.ready','calibration.ready','calibration.gain',*interrupted],
  'lid.set':['lid.opened','lid.angle_rad',*interrupted],
  'lamp.set':interrupted,
  'thermal.set_temperature':['thermal.setpoint_k',*invalidated],
  'acquisition.start':invalidated,
  'acquisition.stop':interrupted,
  'calibration.quartz':['calibration.ready','calibration.gain'],
 }
 on_step={
  'power.set':['thermal.temperature_k','truth.ledger'],
  'thermal.set_temperature':['thermal.temperature_k','truth.ledger'],
  'acquisition.start':['scan.phase','scan.stage','scan.elapsed_s','scan.completed_points','scan.rotation_rad','scan.raw_rotation_rad','optics.analyzer_rad','safety.warning','lamp.enabled'],
 }
 on_completion={'acquisition.start':['scan.phase','scan.stage','blank.ready','lamp.enabled']}
 observed={
  'plant.configure':['plant.configured'],'method.configure':['method.configured'],
  'sample.load':['sample.loaded'],'sample.unload':['sample.loaded'],
  'power.set':['power.enabled'],'lid.set':['lid.opened'],'lamp.set':['lamp.enabled'],
  'thermal.set_temperature':['thermal.setpoint_k'],'acquisition.start':['scan.phase'],'acquisition.stop':['scan.phase'],
  'calibration.quartz':['calibration.ready','calibration.gain','scan.raw_rotation_rad'],
  'process.measure':['scan.phase','scan.rotation_rad','scan.raw_rotation_rad','scan.elapsed_s','scan.completed_points','safety.warning'],
  'result.read':['scan.phase','scan.rotation_rad','scan.raw_rotation_rad','scan.elapsed_s','scan.completed_points','method.wavelength_m','method.integration_s','safety.warning'],
  'product.read':['truth.ledger'],
 }
 def effects_for(key):
  descriptions={'immediate':'本次通过校验的控制/配置改变或清除该状态；不推进仿真时间','on_step':'后续宿主step推进温控、分析器就位或积分；设定返回不表示过程完成','on_completion':'全部积分完成后设置终态并关闭灯门控；仅blank完整采集提交有效空白'}
  return [dict(state_id=state,timing=timing,description_zh=descriptions[timing]) for timing,mapping in [('immediate',immediate),('on_step',on_step),('on_completion',on_completion)] for state in mapping.get(key,[])]
 for k,(s,u,o,v,c,w) in SLOTS.items():
  measured=k in ['scan.rotation_rad','scan.raw_rotation_rad']
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'JascoP4200Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='interval_mean' if measured else 'instant',time_domain='simulation',window_description='分析器就位后的[t_point-integration,t_point]光度积分相位估计，不是整个step窗口' if measured else None),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNSUPPORTED','PRECONDITION_FAILED','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','INITIALIZATION_ONLY','SAMPLE_ACCESS_REQUIRED','SAMPLE_METHOD_REQUIRED','EVIDENCE_ACK_REQUIRED','SOURCE_SOLVENT_MISMATCH','SOURCE_TEMPERATURE_MISMATCH','SOURCE_CONCENTRATION_MISMATCH','QUARTZ_ASSUMPTION_REQUIRED','CELL_CONFIGURATION','OPTICS_NOT_READY','BLANK_REQUIRED','REFERENCE_TEMPERATURE_MISMATCH','SAMPLE_KIND_MISMATCH','BUSY','ANALYSIS_INPUT','REFERENCE_CONDITIONS_MISMATCH','QUARTZ_CALIBRATION_REQUIRED','INVALID_CALIBRATION','ANGLE_BRANCH_RANGE','LOW_POLARIZED_SIGNAL']
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  asynchronous=k=='acquisition.start';query=kind in ['query','analysis'];unsupported=k in UNSUPPORTED
  caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.jasco_p4200.JascoP4200Session.invoke',invocation=f'session.invoke(instance_id="p4200_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if query or unsupported else [dict(description_zh='受检宿主配置、供电关盖、D线/来源条件、完整匹配空白和石英观测校准；每能力仅检查适用前提，详见Runtime._invoke',state_ids=['power.enabled','lid.opened','lamp.enabled','blank.ready','calibration.ready','scan.phase'],enforced_by='runtime',check='atomic Runtime.invoke and trusted-principal Session')],
  state_effects=[] if query or unsupported else effects_for(k),
  observes=observed.get(k,[]),model_effects=dict(mode='none',description_zh='不支持无变化' if unsupported else '待独立场景验证',binding_ids=[]),
  execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',completion_criterion='分析器就位后全部积分完成并关闭逻辑光路门控' if asynchronous else '事务返回；只读/分析不推进时间',completion_state_ids=['scan.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',retry='same_request_id',resources=['host_clock','sample_holder','request_cache'],partial_failure_zh='开盖/断电/熄灯/取消/温度越界或噪声角度分支跳变保留部分点与姿态/热量；不完整空白/石英不接受校准。终态poll快照受原身份保护，后续采集后仍可查询；取消历史终态不停止新采集；历史与去重仅进程内'),
  errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=['文献参数驱动理想Malus/Stokes/分析器代理，非原厂检测机构或实机标定；ORD/绝对光学纯度不支持'],source_ids=['manufacturer_primary','simulation_assumption'])
 mech=dict(component_ids=['sodium_lamp','polarizer','quartz_cell','analyzer','detector','peltier','sample_holder','host_screen'],state_ids=list(slots),meaning_zh='67种真实参考比旋光度、显式传感增益/零偏/噪声、四相光度相位、有限速率与积分、标准温控和观测校准',implementation='lumped',
 equations=['alpha_SI [rad*m^2/kg]=alpha_source [deg*mL/(g*dm)]*pi/18000; theta_true=alpha_SI*c_kg_m3*l_m',
 'Reference constraints: sodium D (nominal589nm vs source589.3nm), exact source solvent and reported concentration; missing concentration host-acknowledged; T_source +/-0.2K only',
 'theta_sensor=host_gain*theta_true+host_zero; I(psi)=transmittance*cos(psi-theta_sensor)^2+dark; psi=0,pi/4,pi/2,3pi/4',
 'theta_observed=0.5*atan2(I45-I135,I0-I90); reject abs(theta_true) or abs(theta_sensor)>=pi/2-1e-8; endpoint branch ambiguity explicitly not supported; noisy demodulated phase differing from settled analyzer by >=pi/2 fails ANGLE_BRANCH_RANGE',
 'Analyzer teaching joint slews at6deg/s to noiseless photometric phase before integrating; no claim this is OEM servo/optical train',
 'Each channel receives Normal(0,host_noise_density/sqrt(integration_s)); 1..100s integration, 1..100 repeats host ceiling',
 'Blank zero=mean(raw_blank_angles); gain_est=(mean(raw_quartz_angles)-zero)/caller_certified_angle; calibrated=(raw-zero)/gain_est',
 'Detector sigma estimate=sqrt(2)*noise_sigma/(2*transmittance); excludes blank/quartz/reference uncertainties, not total uncertainty',
 'Specific rotation=mean(caller_observed_angles)/(declared_c*declared_path); concentration=caller_angle/(caller_reference_alpha*declared_path); no hidden truth',
 'Standard Peltier C*dT/dt=clip(K*(set-T)+UA*(set-Tambient),-Pmax,+Pmax)-UA*(T-Tambient); piecewise exact affine integration',
 'C*deltaT=controller_energy-ambient_loss; holder/sample isothermal lumped proxy, not calibrated temperature accuracy or full electrical energy',
 'step sole simulation clock; whole prefilled cell volume/solute conserved; no mutation/reaction/evaporation; quartz logical standard explicit synthetic'],
 parameters={},source_ids=['manufacturer_primary','manufacturer_spec','manufacturer_us','manufacturer_accessories','pubchem_primary','pubchem_sucrose','simulation_assumption'],
 limitations=['Factory550W350D210Hmm/24.5kg; standard15–40°C holder control +/-0.2°C and monitoring +/-0.1°C are specifications, not validated model errors.',
 '67 independently reviewed stereochemical identities with measured reference specific rotations; no measured raw optical signals or P4200 traces.',
 'No ORD/extrapolation, concentration dependence calibration, mutarotation/pH/mixtures or unconditional ee; invalid source derivative identities excluded.',
 'Na589nm only; other lamps/wavelengths/automatic changer absent; P4200 must not inherit P4300 standard configuration.',
 'Malus/Stokes signal and analyzer finite rate are explicit idealized model, not manufacturer demodulation or servo design; unresolved +/-90deg branch endpoints and noisy principal-angle wraps relative to settled analyzer pose are rejected with partial data retained.',
 'Gain/offset/noise/transmittance/thermal parameters are host assumptions; source display increment/accuracy/repeatability are not noise distributions.',
 'Quartz standard is host-synthetic with caller-declared angle, no ISO17025 certificate; calibration tested algorithmically only.',
 'Known reference concentration matched; missing concentration acknowledged; traditional percent->g/100mL convention retained with original source wording.',
 'Prefilled-cell logical attachment, fixed geometry no fill/robot/contact; 2mL logical ceiling is host assumption, not manufacturer capacity.',
 'Temperature holder/sample lumped; no validated probe delay, heat transfer geometry, long-term drift or failure probabilities.',
 'Six simplified screen readouts are simulation_only, no full native GUI/CFR/ALCOA+/LIMS/hardware driver.',
 'Session access and in-process request dedup are not OS file isolation or restart idempotency; no full native Matterix async envelope or hardware/HIL.'])
 spec=importlib.util.spec_from_file_location('_p4200_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
 ann.annotate_export(SLUG,caps,slots,{'polarimetry':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.register':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'，非实机标定。'
 mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'polarimetry':mech})

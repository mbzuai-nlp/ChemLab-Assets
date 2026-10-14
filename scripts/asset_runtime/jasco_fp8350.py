"""FP-8350: measured paired PhotochemCAD spectra and explicit uncalibrated instrument model."""
from __future__ import annotations
import copy,hashlib,importlib.util,json,math
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2];SLUG='jasco_fp8350';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-067';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/jasco_fp8350_fluorescence_library/templates.json'
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
PLANT=obj({'ambient_k':number('K',288.15,303.15),'lamp_warmup_s':number('s',.01,3600),'incident_photons_s_inv':number('s^-1',1,1e15),'collection_fraction':number('1',1e-9,1),'reference_counts_s_inv':number('s^-1',1,1e12),'dark_counts_s_inv':number('s^-1',0,1e8),'noise_multiplier':number('1',0,100),'seed':integer(0,2147483647),'temperature_accessory':BOOL,'heat_capacity_j_k':number('J/K',1,1e5),'thermal_ua_w_k':number('W/K',.01,100),'control_gain_w_k':number('W/K',.01,100),'peltier_limit_w':number('W',.01,1000),'accept_uncalibrated_model':BOOL,'accept_kasha_approximation':BOOL})
PLANT_EXAMPLE=dict(ambient_k=298.15,lamp_warmup_s=2.,incident_photons_s_inv=1e9,collection_fraction=.01,reference_counts_s_inv=1e4,dark_counts_s_inv=20.,noise_multiplier=1.,seed=67,temperature_accessory=False,heat_capacity_j_k=20.,thermal_ua_w_k=.2,control_gain_w_k=4.,peltier_limit_w=15.,accept_uncalibrated_model=True,accept_kasha_approximation=True)
FAST_PLANT=dict(PLANT_EXAMPLE,noise_multiplier=0.)
SOLUTION=obj({'sample_id':TEXT,'kind':dict(type='string',enum=['blank','sample']),'template_id':TEXT,'concentration_mol_m3':number('mol/m^3',0,1),'volume_m3':number('m^3',1e-8,3.5e-6),'solvent_id':TEXT,'solvent_background_s_inv':number('s^-1',0,1e6),'reference_temperature_k':number('K',288.15,303.15),'accept_unknown_temperature':BOOL,'accept_source_mirror':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='sample_01',kind='sample',template_id='pcad_083',concentration_mol_m3=.0002,volume_m3=2e-6,solvent_id='ethanol',solvent_background_s_inv=40.,reference_temperature_k=298.15,accept_unknown_temperature=True,accept_source_mirror=True)
BLANK_EXAMPLE=dict(SAMPLE_EXAMPLE,sample_id='blank_01',kind='blank',template_id='',concentration_mol_m3=0.)
BAND=dict(type='number',enum=[1e-9,2.5e-9,5e-9,10e-9,20e-9],**{'x-unit':'m'})
METHOD=obj({'mode':dict(type='string',enum=['emission','excitation','time']),'excitation_m':number('m',200e-9,750e-9),'emission_m':number('m',200e-9,750e-9),'start_m':number('m',200e-9,750e-9),'stop_m':number('m',200e-9,750e-9),'interval_m':number('m',.1e-9,20e-9),'excitation_bandwidth_m':BAND,'emission_bandwidth_m':BAND,'integration_s':number('s',.01,8),'interval_s':number('s',.01,60),'duration_s':number('s',.01,3600),'gain':number('1',.01,100)})
METHOD_EXAMPLE=dict(mode='emission',excitation_m=480e-9,emission_m=555e-9,start_m=530e-9,stop_m=600e-9,interval_m=5e-9,excitation_bandwidth_m=5e-9,emission_bandwidth_m=5e-9,integration_s=.04,interval_s=.1,duration_s=1.,gain=1.)
POINT=obj({'excitation_m':number('m'),'emission_m':number('m'),'intensity':number('1'),'raw_intensity':number('1'),'sample_time_s':number('s',0),'integration_s':number('s',0),'elapsed_s':number('s',0),'temperature_k':number('K'),'estimated_sigma':number('1',0)})
MEASURE=obj({'phase':PHASE,'excitation_m':nullable(number('m')),'emission_m':nullable(number('m')),'intensity':nullable(number('1')),'elapsed_s':number('s',0),'completed_points':integer(0,4096),'warning':TEXT})
RESULT=obj({'phase':PHASE,'kind':TEXT,'mode':TEXT,'complete':BOOL,'points':arr(POINT),'warning':TEXT,'template_source':TEXT,'evidence':TEXT,'limitations':arr(TEXT)})
LEDGER=obj({'solution_registered_m3':number('m^3',0),'solution_attached_m3':number('m^3',0),'solution_external_m3':number('m^3',0),'volume_residual_m3':number('m^3'),'registered_solute_mol':number('mol',0),'attached_solute_mol':number('mol',0),'external_solute_mol':number('mol',0),'solute_residual_mol':number('mol'),'incident_photons':number('1',0),'absorbed_photons':number('1',0),'transmitted_photons':number('1',0),'emitted_photons':number('1',0),'nonradiative_events':number('1',0),'detected_photons':number('1',0),'undetected_photons':number('1',0),'excitation_residual':number('1'),'excited_event_residual':number('1'),'collection_residual':number('1'),'thermal_delta_j':number('J'),'controller_energy_j':number('J'),'ambient_loss_j':number('J'),'thermal_residual_j':number('J'),'active_template_id':TEXT,'evidence':TEXT})
TEMPLATE=obj({'substance_id':TEXT,'name':TEXT,'abs_wavelength_m':arr(number('m')),'epsilon_m2_mol':arr(number('m^2/mol')),'em_wavelength_m':arr(number('m')),'emission_relative':arr(number('1')),'quantum_yield':number('1',0,1),'source':TEXT,'evidence':TEXT,'conditions_json':TEXT})
CAL=obj({'slope_m3_mol':number('m^3/mol'),'intercept':number('1'),'r_squared':number('1'),'estimated_concentration_mol_m3':nullable(number('mol/m^3')),'within_calibration_range':BOOL,'evidence':TEXT})
CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},[])
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body'],'一次性宿主指定光子通量/收集率/暖机/噪声及温控代理','configuration','evaluator',PLANT_EXAMPLE)
cap('solution.register',SOLUTION,obj({'registered':BOOL}),['cell_holder'],'宿主登记预装10mm方池、模板溶剂与浓度；未知温度须确认','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.load',obj({'sample_id':TEXT}),obj({'loaded':BOOL,'attachment_event':TEXT}),['cell_holder','sample_cell'],'开盖由宿主挂接完整池，固定网格不假装机器人动作','device_command','evaluator',{'sample_id':'sample_01'})
cap('sample.unload',obj({'recipient_id':TEXT}),obj({'unloaded':BOOL,'attachment_event':TEXT}),['cell_holder','sample_cell'],'卸下完整预装池，不损失液体','device_command','evaluator',{'recipient_id':'external_rack'})
cap('method.configure',METHOD,obj({'configured':BOOL}),['ex_monochromator','em_monochromator','host_screen'],'激发/发射/时间三模式与离散带宽、积分、相对增益；更改使空白失效','configuration','agent',METHOD_EXAMPLE)
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet'],'逻辑供电，关机取消并关闭灯/快门',ex={'enabled':True})
cap('lid.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['sample_lid'],'开盖取消并关快门，保留已采集点',ex={'opened':False})
cap('lamp.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['xe_lamp'],'150W氙灯额定；实际光子通量为独立宿主假设',ex={'enabled':True})
cap('shutter.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['shutter'],'软件光学互锁，不是硬件防护认证',ex={'opened':True})
cap('thermal.set_temperature',obj({'temperature_k':number('K',278.15,343.15)}),obj({'temperature_k':number('K')}),['cell_holder'],'仅宿主声明EHC113安装后；5..70°C为设置范围非全范围性能保证',ex={'temperature_k':298.15})
cap('acquisition.start',obj({'kind':dict(type='string',enum=['blank','sample'])}),obj({'phase':PHASE,'operation_id':TEXT}),['sample_cell','pmt','shutter','host_screen'],'同方法空白/样品异步采集，模板吸收与发射独立配对',ex={'kind':'sample'})
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['shutter','host_screen'],'取消保留部分点和曝光历史')
cap('process.measure',obj({}),MEASURE,['host_screen'],'纯读最后实际采集点，无数据null','query')
cap('spectrum.read',obj({}),RESULT,['host_screen'],'返回真实曲线模板经显式仿真变换的谱/时间序列','query')
cap('quantify.fit',obj({'concentrations_mol_m3':arr(number('mol/m^3',0,1)),'intensities':arr(number('1')),'unknown_intensity':number('1'),'accept_same_method':BOOL}),CAL,['host_screen'],'只拟合调用者的实测/模拟标准点，未知值外推拒绝，不读隐藏浓度','analysis','agent',{'concentrations_mol_m3':[0.,.0001,.0002],'intensities':[0.,1.,2.],'unknown_intensity':1.5,'accept_same_method':True})
cap('template.read',obj({'substance_id':TEXT}),TEMPLATE,['host_screen'],'公开参考曲线，不透露当前样品身份','query','agent',{'substance_id':'pcad_083'})
cap('product.read',obj({}),LEDGER,['cell_holder'],'私有完整池物料、仅采集期间的激发事件与温控能量账本；不累计空闲手动开快门曝光','query','evaluator')
cap('optics.install_900nm',obj({}),obj({}),['pmt'],'900nm选件未建模/未安装，明确UNSUPPORTED','configuration')
cap('phosphorescence.measure',obj({}),obj({}),['pmt'],'不以稳态荧光曲线冒充寿命/磷光测量','analysis')
UNSUPPORTED={'optics.install_900nm','phosphorescence.measure'}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='host_screen',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','显式宿主配置完成','setpoint',c='body')
slot('method.configured',BOOL,'1','激发/发射/时间方法配置','setpoint')
slot('power.enabled',BOOL,'1','逻辑供电','setpoint',c='power_inlet',w=('power.set','enabled'))
slot('lid.opened',BOOL,'1','样品室盖开启','scene',c='sample_lid',w=('lid.set','opened'))
slot('lid.angle_rad',number('rad',0,math.pi/2),'rad','盖绕-X开启，90°为工程行程','scene',c='sample_lid')
slot('lamp.enabled',BOOL,'1','氙灯逻辑使能','setpoint',c='xe_lamp',w=('lamp.set','enabled'))
slot('lamp.ready',BOOL,'1','达到宿主假设暖机时长','derived',c='xe_lamp')
slot('shutter.opened',BOOL,'1','光学快门开启','scene',c='shutter',w=('shutter.set','opened'))
slot('shutter.travel_m',number('m',0,.018),'m','快门工程平移18mm','scene',c='shutter')
slot('sample.loaded',BOOL,'1','宿主挂接完整预装池','scene',c='sample_cell')
slot('thermal.installed',BOOL,'1','EHC113选件由可信宿主声明安装','setpoint',c='cell_holder')
slot('thermal.setpoint_k',nullable(number('K',278.15,343.15)),'K','选件温度设置，不代表实机达标','setpoint',c='cell_holder',w=('thermal.set_temperature','temperature_k'))
slot('thermal.temperature_k',number('K'),'K','集中热容数值温度，非实测传感器','derived',c='cell_holder')
slot('thermal.within_documented_control',BOOL,'1','仅环境25°C且目标10–60°C对应厂家控制范围','derived',c='cell_holder')
slot('blank.ready',BOOL,'1','相同方法/溶剂/温度有效空白')
slot('scan.phase',PHASE,'1','异步采集生命周期')
slot('scan.mode',TEXT,'1','激发/发射谱或时间序列')
slot('scan.elapsed_s',number('s',0),'s','本次采集已推进时间')
slot('scan.completed_points',integer(0,4096),'1','实际完成点数')
slot('scan.excitation_m',nullable(number('m')),'m','实际采集激发波长')
slot('scan.emission_m',nullable(number('m')),'m','实际采集发射波长')
slot('scan.intensity',nullable(number('1')),'1','相对荧光强度，非原厂实机光度标定')
slot('method.gain',nullable(number('1',.01,100)),'1','相对增益设置，非原厂PMT电压标定','setpoint')
slot('safety.warning',TEXT,'1','不泄露样品身份的告警')
slot('truth.ledger',LEDGER,'1','样品身份、物料、仅采集期间光子及热量私有账本；不代表整段开机曝光','process_truth','evaluator','cell_holder')
class JascoFp8350Runtime:
 def __init__(self,instance_id='fp8350_01'):
  self.instance_id=instance_id;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.time_s=0.;self.revision=0;self.plant=None;self.method=None;self.samples={};self.active=None
  self.powered=False;self.opened=True;self.lamp=False;self.lamp_age=0.;self.shutter=False;self.temperature_k=298.15;self.setpoint_k=None;self.controller_energy_j=0.;self.ambient_loss_j=0.
  self.blank=None;self.phase='idle';self.kind='';self.elapsed_s=0.;self.points=[];self.warning='';self.operation_id='';self._op_count=0;self._schedule=[];self._due=0.;self._index=0
  self._photons={k:0. for k in ['incident','absorbed','transmitted','emitted','nonradiative','detected','undetected']};self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._operation_records={};self._rng=np.random.default_rng(0)
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return JascoFp8350Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def debug_snapshot(self):
  v=copy.deepcopy({k:x for k,x in self.__dict__.items() if k not in ['templates','_rng']});v['_rng_state']=copy.deepcopy(self._rng.bit_generator.state);return v
 def _sample(self):return self.samples.get(self.active)
 def _ledger(self):
  v=sum(s['volume_m3'] for s in self.samples.values());a=self._sample()['volume_m3'] if self.active else 0.;n=sum(s['volume_m3']*s['concentration_mol_m3'] for s in self.samples.values());na=a*self._sample()['concentration_mol_m3'] if self.active else 0.;p=self._photons;d=(self.plant['heat_capacity_j_k']*(self.temperature_k-self.plant['ambient_k'])) if self.plant else 0.
  return dict(solution_registered_m3=v,solution_attached_m3=a,solution_external_m3=v-a,volume_residual_m3=v-a-(v-a),registered_solute_mol=n,attached_solute_mol=na,external_solute_mol=n-na,solute_residual_mol=n-na-(n-na),incident_photons=p['incident'],absorbed_photons=p['absorbed'],transmitted_photons=p['transmitted'],emitted_photons=p['emitted'],nonradiative_events=p['nonradiative'],detected_photons=p['detected'],undetected_photons=p['undetected'],excitation_residual=p['incident']-p['absorbed']-p['transmitted'],excited_event_residual=p['absorbed']-p['emitted']-p['nonradiative'],collection_residual=p['emitted']-p['detected']-p['undetected'],thermal_delta_j=d,controller_energy_j=self.controller_energy_j,ambient_loss_j=self.ambient_loss_j,thermal_residual_j=d-self.controller_energy_j+self.ambient_loss_j,active_template_id=self._sample()['template_id'] if self.active else '',evidence='complete-cell location/solute conserved; no bleaching/reaction; acquisition-only excitation-event ledger excludes idle/manual-shutter exposure; independently lumped thermal ledger, not full optical energy balance')
 def _fingerprint(self):
  s=self._sample();return json.dumps([self.method,s['solvent_id'],s['solvent_background_s_inv'],s['reference_temperature_k'],self.setpoint_k],sort_keys=True)
 def _blank_ok(self):return bool(self.blank and self.active and self.blank['fingerprint']==self._fingerprint())
 def read(self,visibility='agent'):
  if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
  p=self.points[-1] if self.points else {};v={'plant.configured':bool(self.plant),'method.configured':bool(self.method),'power.enabled':self.powered,'lid.opened':self.opened,'lid.angle_rad':math.pi/2 if self.opened else 0.,'lamp.enabled':self.lamp,'lamp.ready':bool(self.lamp and self.plant and self.lamp_age>=self.plant['lamp_warmup_s']),'shutter.opened':self.shutter,'shutter.travel_m':.018 if self.shutter else 0.,'sample.loaded':bool(self.active),'thermal.installed':bool(self.plant and self.plant['temperature_accessory']),'thermal.setpoint_k':self.setpoint_k,'thermal.temperature_k':self.temperature_k,'thermal.within_documented_control':bool(self.plant and self.plant['temperature_accessory'] and abs(self.plant['ambient_k']-298.15)<1e-9 and 283.15<=self.setpoint_k<=333.15),'blank.ready':self._blank_ok(),'scan.phase':self.phase,'scan.mode':self.method['mode'] if self.method else '', 'scan.elapsed_s':self.elapsed_s,'scan.completed_points':len(self.points),'scan.excitation_m':p.get('excitation_m'),'scan.emission_m':p.get('emission_m'),'scan.intensity':p.get('intensity'),'method.gain':self.method['gain'] if self.method else None,'safety.warning':self.warning,'truth.ledger':self._ledger()}
  return copy.deepcopy({k:x for k,x in v.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _archive_operation(self):
  if self.operation_id and self.phase in ('succeeded','cancelled','failed') and self.operation_id not in self._operation_records:
   self._operation_records[self.operation_id]=dict(operation_id=self.operation_id,status=self.phase,result=copy.deepcopy(self._invoke('process.measure',{})))
 def _invalidate(self):
  self._archive_operation();self.blank=None;self.points=[];self.phase='idle';self.kind='';self.elapsed_s=0.;self.warning=''
 def _stop(self,phase,warning):
  if self.phase=='running':self.phase=phase
  self.shutter=False;self.warning=warning;self._archive_operation()
 def _ready(self):
  if not self.plant or not self.powered or self.opened or not self.lamp or self.lamp_age<self.plant['lamp_warmup_s']:raise ActionRejected('OPTICS_NOT_READY')
 def _grid(self):
  m=self.method
  if m['mode']=='time':return [(m['excitation_m'],m['emission_m'])]*(int(round(m['duration_s']/m['interval_s']))+1)
  grid=np.linspace(m['start_m'],m['stop_m'],int(round(abs(m['stop_m']-m['start_m'])/m['interval_m']))+1)
  return [(float(w),m['emission_m']) if m['mode']=='excitation' else (m['excitation_m'],float(w)) for w in grid]
 def _rates(self,ex,em):
  s=self._sample();m=self.method;p=self.plant
  if s['kind']=='blank':return dict(incident=p['incident_photons_s_inv'],absorbed=0.,emitted=0.,detected=0.)
  r=self.templates[s['template_id']];sx=m['excitation_bandwidth_m']/2.354820045;se=m['emission_bandwidth_m']/2.354820045
  x=np.linspace(ex-3*sx,ex+3*sx,61);y=np.linspace(em-3*se,em+3*se,61)
  if x[0]<r['abs_wavelength_m'][0]-1e-16 or x[-1]>r['abs_wavelength_m'][-1]+1e-16 or y[0]<r['em_wavelength_m'][0]-1e-16 or y[-1]>r['em_wavelength_m'][-1]+1e-16:raise ActionRejected('TEMPLATE_RANGE')
  a=np.interp(x,r['abs_wavelength_m'],np.maximum(r['epsilon_m2_mol'],0))*s['concentration_mol_m3']*.01
  # Dilute validity is conservative across both excitation and fluorescence support.
  max_eps=max(r['epsilon_m2_mol']);amax=max_eps*s['concentration_mol_m3']*.01
  if amax>.1+1e-12:raise ActionRejected('DILUTE_MODEL_LIMIT')
  wx=np.exp(-.5*((x-ex)/sx)**2);wx/=wx.sum();absorbed=p['incident_photons_s_inv']*float(np.dot(wx,-np.expm1(-math.log(10)*a)))
  raw=np.maximum(r['emission_relative'],0);area=np.trapezoid(raw,r['em_wavelength_m']);density=np.interp(y,r['em_wavelength_m'],raw)/area
  fraction=float(np.trapezoid(density*np.exp(-.5*((y-em)/se)**2),y))
  emitted=absorbed*r['quantum_yield'];detected=emitted*p['collection_fraction']*min(1.,max(0.,fraction))
  return dict(incident=p['incident_photons_s_inv'],absorbed=absorbed,emitted=emitted,detected=detected)
 def invoke(self,capability_id,**arguments):
  if capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability_id][0],arguments)
  if capability_id in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  if CAPS[capability_id][4] in ['query','analysis']:return copy.deepcopy(self._invoke(capability_id,copy.deepcopy(arguments)))
  old={k:copy.deepcopy(v) for k,v in self.__dict__.items() if k!='templates'};templates=self.templates
  try:
   result=self._invoke(capability_id,copy.deepcopy(arguments))
   if CAPS[capability_id][4] not in ['query','analysis']:self.revision+=1
   return copy.deepcopy(result)
  except Exception:self.__dict__.clear();self.__dict__.update(old);self.templates=templates;raise
 def _invoke(self,k,a):
  if k=='template.read':
   r=self.templates.get(a['substance_id'])
   if r is None:raise ActionRejected('UNKNOWN_REFERENCE')
   return {**{q:r[q] for q in ['substance_id','name','abs_wavelength_m','epsilon_m2_mol','em_wavelength_m','emission_relative','quantum_yield','source','evidence']},'conditions_json':json.dumps(r['conditions'],ensure_ascii=False)}
  if k=='product.read':return self._ledger()
  if k=='process.measure':
   v=self.read();return dict(phase=self.phase,excitation_m=v['scan.excitation_m'],emission_m=v['scan.emission_m'],intensity=v['scan.intensity'],elapsed_s=self.elapsed_s,completed_points=len(self.points),warning=self.warning)
  if k=='spectrum.read':return dict(phase=self.phase,kind=self.kind,mode=self.method['mode'] if self.method else '',complete=self.phase=='succeeded',points=self.points,warning=self.warning,template_source='PhotochemCAD 2.1a measured spectra via Scott Prahl OMLC mirror; active identity withheld',evidence='measured_literature paired absorption/emission and quantum yield; simulated scan/blank/gain/noise/temperature response',limitations=['Absorption used as Kasha excitation approximation, not independently measured excitation spectrum','Reference emission already includes source instrument bandwidth; no deconvolution or improved resolution claim','Uncalibrated Gaussian bandpass, uniform illumination, collection, gain and noise; not OEM S/N validation','Only source solvent and host-acknowledged unknown source temperature; no quenching, mixture, bleaching, lifetime or calibrated temperature dependence'])
  if k=='quantify.fit':
   x=np.asarray(a['concentrations_mol_m3']);y=np.asarray(a['intensities'])
   if not a['accept_same_method'] or len(x)!=len(y) or not 3<=len(x)<=1000 or len(set(x.tolist()))<3:raise ActionRejected('CALIBRATION_INPUT')
   # Scale before centering: finite caller inputs can still underflow/overflow OLS.
   # Reject coefficients that cannot be represented in the declared SI output.
   try:
    with np.errstate(all='raise'):
     xmin=float(x.min());span=float(x.max()-xmin);yscale=float(np.max(np.abs(y))) or 1.
     xn=(x-xmin)/span;yn=y/yscale;xc=xn-xn.mean();yc=yn-yn.mean()
     scaled_slope=float(np.dot(xc,yc)/np.dot(xc,xc));scaled_intercept=float(yn.mean()-scaled_slope*xn.mean())
     slope=scaled_slope*yscale/span;intercept=(scaled_intercept-scaled_slope*xmin/span)*yscale
     fit=scaled_slope*xn+scaled_intercept;total=float(np.dot(yc,yc));res=float(np.dot(yn-fit,yn-fit));r2=1-res/total if total>0. else 0.
     estimate=xmin+float((a['unknown_intensity']/yscale-scaled_intercept)/scaled_slope)*span if abs(slope)>1e-15 else None
     if not all(math.isfinite(v) for v in [slope,intercept,r2]) or (estimate is not None and not math.isfinite(estimate)):raise ArithmeticError
   except (ArithmeticError,ValueError):raise ActionRejected('CALIBRATION_INPUT') from None
   inside=estimate is not None and float(x.min())<=estimate<=float(x.max())
   return dict(slope_m3_mol=float(slope),intercept=float(intercept),r_squared=float(r2),estimated_concentration_mol_m3=estimate if inside else None,within_calibration_range=inside,evidence='ordinary least squares of caller-supplied standard observations; no hidden sample identity/concentration; method equivalence caller assertion, not real calibration')
  if k=='plant.configure':
   if self.plant or self.powered or not a['accept_uncalibrated_model'] or not a['accept_kasha_approximation']:raise ActionRejected('INITIALIZATION_ONLY')
   self.plant=a;self.temperature_k=a['ambient_k'];self.setpoint_k=a['ambient_k'] if a['temperature_accessory'] else None;self._rng=np.random.default_rng(a['seed']);return {'configured':True}
  if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
  if self.phase=='running' and k in ['solution.register','sample.load','sample.unload','method.configure','thermal.set_temperature']:raise ActionRejected('BUSY')
  if k=='solution.register':
   if a['sample_id'] in self.samples or not a['sample_id']:raise ActionRejected('REQUEST_CONFLICT')
   if not a['accept_unknown_temperature'] or not a['accept_source_mirror']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   if a['kind']=='blank':
    if a['template_id'] or a['concentration_mol_m3']!=0:raise ActionRejected('INVALID_ARGUMENT')
   else:
    if a['template_id'] not in self.templates or a['concentration_mol_m3']<=0:raise ActionRejected('UNKNOWN_REFERENCE')
    if a['solvent_id']!=self.templates[a['template_id']]['conditions']['solvent']:raise ActionRejected('SOURCE_SOLVENT_MISMATCH')
   for old in self.samples.values():
    if old['solvent_id']==a['solvent_id'] and old['solvent_background_s_inv']!=a['solvent_background_s_inv']:raise ActionRejected('SOLVENT_DEFINITION_CONFLICT')
   self.samples[a['sample_id']]=a;return {'registered':True}
  if k=='sample.load':
   if not self.opened or self.active or a['sample_id'] not in self.samples:raise ActionRejected('SAMPLE_ACCESS_REQUIRED')
   self.active=a['sample_id'];return dict(loaded=True,attachment_event='host_attach_prefilled_cell')
  if k=='sample.unload':
   if not self.opened or not self.active or not a['recipient_id']:raise ActionRejected('SAMPLE_ACCESS_REQUIRED')
   self.active=None;return dict(unloaded=True,attachment_event='host_detach_intact_prefilled_cell')
  if k=='method.configure':
   if a['mode']=='time':
    n=a['duration_s']/a['interval_s']
    if a['interval_s']<a['integration_s']:raise ActionRejected('INTEGRATION_OVERLAP')
   else:n=abs(a['stop_m']-a['start_m'])/a['interval_m']
   if not 1<=round(n)<=4095 or abs(n-round(n))>1e-5:raise ActionRejected('GRID_INTERVAL_MISMATCH')
   self.method=a
   for ex,em in self._grid():
    sx=a['excitation_bandwidth_m']/2.354820045;se=a['emission_bandwidth_m']/2.354820045
    if min(ex-3*sx,em-3*se)<200e-9-1e-16 or max(ex+3*sx,em+3*se)>750e-9+1e-16:raise ActionRejected('STANDARD_DETECTOR_RANGE')
    if em-3*se<=ex+3*sx:raise ActionRejected('SCATTER_OR_ANTISTOKES_UNSUPPORTED')
   self._invalidate();return {'configured':True}
  if k=='power.set':
   self.powered=a['enabled']
   if not self.powered:self._stop('cancelled','POWER_OFF');self.lamp=False;self.lamp_age=0.;self.blank=None
   return a
  if k=='lid.set':
   if a['opened']:self._stop('cancelled','LID_OPEN')
   self.opened=a['opened'];return a
  if k=='lamp.set':
   if a['enabled'] and not self.powered:raise ActionRejected('OPTICS_NOT_READY')
   if self.lamp!=a['enabled']:self.lamp_age=0.;self.blank=None
   self.lamp=a['enabled']
   if not self.lamp:self._stop('cancelled','LAMP_OFF')
   return a
  if k=='shutter.set':
   if a['opened']:self._ready()
   elif self.phase=='running':self._stop('cancelled','SHUTTER_CLOSED')
   self.shutter=a['opened'];return a
  if k=='thermal.set_temperature':
   if not self.plant['temperature_accessory']:raise ActionRejected('ACCESSORY_NOT_INSTALLED')
   self.setpoint_k=a['temperature_k'];self._invalidate();return a
  if k=='acquisition.stop':self._stop('cancelled','CANCELLED');return {'phase':self.phase}
  if k=='acquisition.start':
   if self.phase=='running':raise ActionRejected('BUSY')
   self._ready()
   if not self.active or not self.method:raise ActionRejected('SAMPLE_METHOD_REQUIRED')
   s=self._sample()
   if s['kind']!=a['kind']:raise ActionRejected('SAMPLE_KIND_MISMATCH')
   if abs(self.temperature_k-s['reference_temperature_k'])>.25:raise ActionRejected('REFERENCE_TEMPERATURE_MISMATCH')
   if a['kind']=='sample' and not self._blank_ok():raise ActionRejected('BLANK_REQUIRED')
   self._archive_operation();self._schedule=[]
   for i,(ex,em) in enumerate(self._grid()):
    rates=self._rates(ex,em);duration=self.method['integration_s'] if i==0 or self.method['mode']!='time' else self.method['interval_s']
    self._schedule.append(dict(ex=ex,em=em,rates=rates,duration=duration))
   self.phase='running';self.kind=a['kind'];self.elapsed_s=0.;self.points=[];self.warning='';self.shutter=True;self._index=0;self._due=self._schedule[0]['duration'];self._op_count+=1;self.operation_id=self.instance_id+':scan:'+str(self._op_count)
   return dict(phase=self.phase,operation_id=self.operation_id)
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _thermal(self,dt):
  p=self.plant
  if not p:return
  C=p['heat_capacity_j_k'];U=p['thermal_ua_w_k'];K=p['control_gain_w_k'];limit=p['peltier_limit_w'];ambient=p['ambient_k'];remaining=dt
  for _ in range(8):
   if remaining<=1e-13:break
   t=self.temperature_k;control=bool(self.powered and p['temperature_accessory']);feed=U*(self.setpoint_k-ambient) if control else 0.;demand=K*(self.setpoint_k-t)+feed if control else 0.
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
  e=self._schedule[self._index];m=self.method;p=self.plant;s=self._sample();dit=m['integration_s'];rate=e['rates']['detected']+p['dark_counts_s_inv']+s['solvent_background_s_inv'];scale=m['gain']/p['reference_counts_s_inv'];sigma=p['noise_multiplier']*math.sqrt(max(0.,rate)/dit)*scale
  raw=rate*scale+float(self._rng.normal(0.,sigma));blank=self.blank['points'][self._index] if self.kind=='sample' else None
  value=raw-(blank['raw_intensity'] if blank else 0.);combined=math.hypot(sigma,blank['estimated_sigma']) if blank else sigma
  self.points.append(dict(excitation_m=e['ex'],emission_m=e['em'],intensity=value,raw_intensity=raw,sample_time_s=self.time_s,integration_s=dit,elapsed_s=self.elapsed_s,temperature_k=self.temperature_k,estimated_sigma=combined))
  self._index+=1
  if self._index==len(self._schedule):
   self.phase='succeeded';self.shutter=False
   if self.kind=='blank':self.blank=dict(fingerprint=self._fingerprint(),points=copy.deepcopy(self.points))
   self._archive_operation()
  else:self._due+=self._schedule[self._index]['duration']
 def step(self,dt_s):
  validate(number('s',0,86400),dt_s)
  if dt_s==0:return
  remaining=dt_s
  while remaining>1e-12:
   dt=min(remaining,max(0.,self._due-self.elapsed_s)) if self.phase=='running' else remaining
   if dt>1e-13:
    self._thermal(dt)
    if self.lamp:self.lamp_age+=dt
    if self.phase=='running':
     rates=self._schedule[self._index]['rates'];p=self._photons
     for k in ['incident','absorbed','emitted','detected']:p[k]+=rates[k]*dt
     p['transmitted']+=(rates['incident']-rates['absorbed'])*dt;p['nonradiative']+=(rates['absorbed']-rates['emitted'])*dt;p['undetected']+=(rates['emitted']-rates['detected'])*dt
     self.elapsed_s+=dt
    self.time_s+=dt;remaining-=dt
   if self.phase=='running' and self.active and abs(self.temperature_k-self._sample()['reference_temperature_k'])>.25:self._stop('failed','REFERENCE_TEMPERATURE_MISMATCH')
   if self.phase=='running' and self.elapsed_s+1e-12>=self._due:self._record()
   if dt<=1e-13 and self.phase!='running':self._thermal(remaining);self.time_s+=remaining;remaining=0.
  self.revision+=1
class JascoFp8350Session:
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
        m=export_jasco_fp8350_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.1',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='unavailable' if v[k] is None else 'estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=h.points[-1]['sample_time_s'] if k.startswith('scan.') and k in ['scan.intensity','scan.excitation_m','scan.emission_m'] and h.points else h.time_s,sample_window_s=[h.points[-1]['sample_time_s']-h.points[-1]['integration_s'],h.points[-1]['sample_time_s']] if k=='scan.intensity' and h.points else None) for k in ids})
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


def export_jasco_fp8350_contract_metadata():
 caps={};slots={}
 # A command declares only state it can change; subsequent host stepping is
 # separate from a completed setting transaction.
 reset=['blank.ready','scan.phase','scan.elapsed_s','scan.completed_points','scan.excitation_m','scan.emission_m','scan.intensity','safety.warning']
 interrupt=['scan.phase','shutter.opened','shutter.travel_m','safety.warning']
 immediate={
  'plant.configure':['plant.configured','thermal.installed','thermal.setpoint_k','thermal.temperature_k','thermal.within_documented_control'],
  'solution.register':['truth.ledger'],
  'sample.load':['sample.loaded','blank.ready','truth.ledger'],
  'sample.unload':['sample.loaded','blank.ready','truth.ledger'],
  'method.configure':['method.configured','method.gain','scan.mode',*reset],
  'power.set':['power.enabled','lamp.enabled','lamp.ready','blank.ready',*interrupt],
  'lid.set':['lid.opened','lid.angle_rad',*interrupt],
  'lamp.set':['lamp.enabled','lamp.ready','blank.ready',*interrupt],
  'shutter.set':interrupt,
  'thermal.set_temperature':['thermal.setpoint_k','thermal.within_documented_control',*reset],
  'acquisition.start':['scan.phase','scan.elapsed_s','scan.completed_points','scan.excitation_m','scan.emission_m','scan.intensity','safety.warning','shutter.opened','shutter.travel_m'],
  'acquisition.stop':interrupt,
 }
 on_step={
  'power.set':['thermal.temperature_k','truth.ledger'],
  'lamp.set':['lamp.ready'],
  'thermal.set_temperature':['thermal.temperature_k','truth.ledger'],
  'acquisition.start':['scan.phase','scan.elapsed_s','scan.completed_points','scan.excitation_m','scan.emission_m','scan.intensity','safety.warning','truth.ledger','shutter.opened','shutter.travel_m'],
 }
 on_completion={'acquisition.start':['scan.phase','blank.ready','shutter.opened','shutter.travel_m']}
 observed={
  'plant.configure':['plant.configured'],'method.configure':['method.configured'],
  'sample.load':['sample.loaded'],'sample.unload':['sample.loaded'],
  'power.set':['power.enabled'],'lid.set':['lid.opened'],'lamp.set':['lamp.enabled'],'shutter.set':['shutter.opened'],
  'thermal.set_temperature':['thermal.setpoint_k'],'acquisition.start':['scan.phase'],'acquisition.stop':['scan.phase'],
  'process.measure':['scan.phase','scan.excitation_m','scan.emission_m','scan.intensity','scan.elapsed_s','scan.completed_points','safety.warning'],
  'spectrum.read':['scan.phase','scan.mode','scan.excitation_m','scan.emission_m','scan.intensity','scan.elapsed_s','scan.completed_points','safety.warning'],
  'product.read':['truth.ledger'],
 }
 def effects_for(key):
  descriptions={'immediate':'本次通过校验的控制/配置改变或清除该状态；无仿真时间推进','on_step':'由后续宿主step推进相应过程；完成设定不表示过程已完成','on_completion':'采集完整结束时提交有效空白（仅blank采集）、终态并关闭快门'}
  return [dict(state_id=state,timing=timing,description_zh=descriptions[timing]) for timing,mapping in [('immediate',immediate),('on_step',on_step),('on_completion',on_completion)] for state in mapping.get(key,[])]
 for k,(s,u,o,v,c,w) in SLOTS.items():
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'JascoFp8350Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='interval_mean' if k=='scan.intensity' else 'instant',time_domain='simulation',window_description='采集积分窗口[t_point-integration_s,t_point]；样品减同方法已完成空白' if k=='scan.intensity' else None),source_ids=['simulation_assumption'])
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  asynchronous=k=='acquisition.start';query=kind in ['query','analysis'];unsupported=k in UNSUPPORTED
  caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.jasco_fp8350.JascoFp8350Session.invoke',invocation=f'session.invoke(instance_id="fp8350_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if query or unsupported else [dict(description_zh='可信宿主配置、供电/灯暖机/关盖、源溶剂与温度、稀溶液范围、同方法空白；温控须安装选件',state_ids=['power.enabled','lamp.ready','lid.opened','thermal.installed','blank.ready','scan.phase'],enforced_by='runtime',check='atomic Runtime.invoke and trusted-principal Session')],
  state_effects=[] if query or unsupported else effects_for(k),
  observes=observed.get(k,[]),model_effects=dict(mode='none',description_zh='不支持的选项无变化' if unsupported else '待独立场景求值验证',binding_ids=[]),
  execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',completion_criterion='光谱全部波长点或时间序列全部采样点完成并关闭快门' if asynchronous else '事务返回，纯读分析不推进时间',completion_state_ids=['scan.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',retry='same_request_id',resources=['host_clock','cell_holder','request_cache'],partial_failure_zh='开盖/断电/停灯/取消或温度不匹配保留部分点及采集期间曝光预算；空白仅完整完成后有效。终态poll快照受原调用身份保护并在后续采集后保留；取消终态操作不停止新采集；历史和请求去重仅进程内。'),
  errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNSUPPORTED','PRECONDITION_FAILED','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','INITIALIZATION_ONLY','SAMPLE_ACCESS_REQUIRED','SAMPLE_METHOD_REQUIRED','EVIDENCE_ACK_REQUIRED','SOURCE_SOLVENT_MISMATCH','SOLVENT_DEFINITION_CONFLICT','GRID_INTERVAL_MISMATCH','INTEGRATION_OVERLAP','STANDARD_DETECTOR_RANGE','SCATTER_OR_ANTISTOKES_UNSUPPORTED','OPTICS_NOT_READY','BLANK_REQUIRED','TEMPLATE_RANGE','DILUTE_MODEL_LIMIT','REFERENCE_TEMPERATURE_MISMATCH','SAMPLE_KIND_MISMATCH','ACCESSORY_NOT_INSTALLED','BUSY','CALIBRATION_INPUT']],
  example_arguments=copy.deepcopy(ex),verification=[],limitations=['测量结果是公开曲线与教学仪器模型的组合，不是FP8350实机复现或S/N认证；EHC113条件化代理；900nm与磷光不支持'],source_ids=['manufacturer_primary','simulation_assumption'])
 mech=dict(component_ids=['xe_lamp','ex_monochromator','sample_cell','cell_holder','em_monochromator','pmt','shutter','host_screen'],state_ids=list(slots),meaning_zh='配对吸收/发射实测模板、空白/三种采集、稀溶液激发事件与选件热容模型',implementation='lumped',
 equations=['epsilon_SI[m2/mol]=0.1*epsilon_source[M^-1 cm^-1]; lambda_m=1e-9*lambda_nm; negative measured baselines retained in library, clipped only for nonnegative forward rate',
 'A(lambda)=epsilon_SI(lambda)*concentration[mol/m3]*0.01m; conservatively max(A) over complete absorption template<=0.1',
 'Absorbed=incident_photon_rate*Gaussian-weighted integral(1-10^(-A)); emission=absorbed*source_quantum_yield',
 'Measured emission density=max(raw,0)/trapezoid(max(raw,0),lambda_m); detected=emission*host_collection_fraction*integral(density*unit-peak Gaussian emission bandpass)',
 'Gaussian sigma=FWHM/2.354820045; 61 nodes over +/-3sigma; whole support inside measured template and standard200..750nm; emission band separated from excitation band; no extrapolation',
 'RFU_raw=gain/reference_counts*(detected+host_dark+host_solvent_background)+Normal(0,host_multiplier*gain/reference_counts*sqrt(total_rate/integration_s)); RFU=raw-matching_blank; negative blank-corrected observations retained',
 'Emission profile independent of excitation (Kasha approximation); no measured excitation action spectrum, inner-filter correction, quenching, bleaching, chemical reaction or lifetime',
 'Time mode first point at DIT, next at DIT+i*interval; spectral mode stationary points each DIT, instantaneous wavelength retuning assumption; source scan-speed/slew dynamics not implemented',
 'C*dT/dt=clip(K*(set-T)+U*(set-Tambient),-Pmax,+Pmax)-U*(T-Tambient); exact piecewise affine thermal integration; controller off without installed accessory or power',
 'Thermal C*deltaT=controller_energy-ambient_loss; photon excitation/collection counts integrate only while acquisition runs (including between time-mode samples), excluding idle/manual-shutter exposure; no optical-to-heat coupling claim',
 'Quantitation scaled ordinary least squares y=slope*c+intercept on caller standards; unrepresentable SI coefficients reject CALIBRATION_INPUT; no hidden concentration; extrapolation returns null estimated concentration',
 'step sole clock; query/read/fit/poll pure; whole prefilled-cell volume and solute location conserved; no real liquid transfer or reaction'],
 parameters={},source_ids=['manufacturer_primary','manufacturer_accessories','photochem_primary','simulation_assumption'],
 limitations=['FP8350 standard200–750nm/150W/36kg and520×545×270mm;900nm is not installed, JapanS/N220P-P880RMS only under unspecified manufacturer conditions.','PhotochemCAD/OMLC are non-FP8350 measurements with their own bandwidth; source temperature often unknown and requires explicit host acknowledgment; only documented solvent.','Temperature control5–70°C setting range differs from10–60°C control range at25°C ambient; Peltier/C/UA/controller/temperature response uncalibrated.','Gain, incident photons, collection, dark/background, noise, Gaussian slit and retuning are host assumptions; not OEM sensitivity/RMS/P-P validation.','Photon-number ledger covers acquisition only, not idle/manual-shutter exposure or total lamp-on history; it is separate from the lumped thermal ledger; no full spectral energy transfer, photobleaching, quantum yield measurement or oxygen chemistry.','10mm prefilled rectangular cuvette only, logical host attach; geometry fixed marker, no robot insertion or fluid mechanics.','Public source names deduplicated across solvents; source structure images/metadata are not a complete canonical structure database.','Independent telemetry board is simulation_only, not native iRM/SpectraManager GUI; no USB driver, hardware/HIL or deployment file isolation.'])
 spec=importlib.util.spec_from_file_location('_fp8350_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
 ann.annotate_export(SLUG,caps,slots,{'fluorometry':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'solution.register':(SOLUTION,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'，不代表实机标定。'
 mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'fluorometry':mech})


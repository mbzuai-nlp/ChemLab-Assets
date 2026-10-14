"""CHEM-100 GC-IMS: finite gas/headspace transfer, PAL axis motion and literature peak descriptors.
Trusted Python host only. Sessions filter identity/truth; process/file isolation is external.
"""
from pathlib import Path
import copy,math,json,importlib.util
PROTOCOL_VERSION='1.0.0';SLUG='gas_flavourspec25gc';CATALOG_ID='CHEM-100';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];TEMPLATE_REL='assets/expansion_batch04/t3_analytical/'+SLUG+'/ims_library.json'
class ActionRejected(ValueError):pass
def number(unit='1',lo=None,hi=None):
 d={'type':'number','x-unit':unit}
 if lo is not None:d['minimum']=lo
 if hi is not None:d['maximum']=hi
 return d
def obj(p):return dict(type='object',properties=p,required=list(p),additionalProperties=False)
def arr(v,n=None):
 d=dict(type='array',items=v)
 if n is not None:d.update(minItems=n,maxItems=n)
 return d
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
def enum(v):return {'type':'string','enum':v}
TEXT={'type':'string'};BOOL={'type':'boolean'};ISO=enum(['2H','13C','15N','18O','34S']);PHASE=enum(['idle','running','succeeded','cancelled','failed'])
def validate(s,v):
 if 'anyOf' in s:
  for q in s['anyOf']:
   try:validate(q,v);return
   except ActionRejected:pass
  raise ActionRejected('INVALID_ARGUMENT')
 t=s.get('type')
 if t in ('number','integer'):
  if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or (t=='integer' and type(v) is not int) or not s.get('minimum',-math.inf)<=v<=s.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='boolean' and type(v) is not bool:raise ActionRejected('INVALID_ARGUMENT')
 elif t=='string' and not isinstance(v,str):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='null' and v is not None:raise ActionRejected('INVALID_ARGUMENT')
 elif t=='object':
  if not isinstance(v,dict) or not set(s.get('required',[]))<=v.keys() or set(v)-set(s['properties']):raise ActionRejected('INVALID_ARGUMENT')
  for k,x in v.items():validate(s['properties'][k],x)
 elif t=='array':
  if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
  for x in v:validate(s['items'],x)
 if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')
import numpy as np
R=8.31446261815324
PLANT=obj({'ambient_k':number('K',278.15,308.15),'gas_supply_mol':number('mol',1e-9,1e4),'gas_reference_k':number('K',200,400),'gas_reference_pa':number('Pa',1e4,1e6),'thermal_tau_s':number('s',.01,1e5),'travel_s':number('s',.1,120),'stroke_s':number('s',.1,120),'gain_s_per_mol':number('s/mol',1e-9,1e15),'gc_sigma_s':number('s',.1,100),'drift_sigma_ratio':number('1',.001,.2),'accept_synthetic_response':BOOL})
PLANT_EXAMPLE=dict(ambient_k=298.15,gas_supply_mol=10.,gas_reference_k=293.15,gas_reference_pa=101325.,thermal_tau_s=2.,travel_s=1.,stroke_s=1.,gain_s_per_mol=1e9,gc_sigma_s=2.,drift_sigma_ratio=.015,accept_synthetic_response=True)
METHOD=obj({'gc_k':number('K',278.15,373.15),'ims_k':number('K',278.15,373.15),'injector_k':number('K',278.15,473.15),'incubator_k':number('K',278.15,473.15),'incubation_s':number('s',0,7200),'injection_m3':number('m^3',1e-9,1e-6),'column_length_m':number('m',.1,60),'gas':enum(['nitrogen','synthetic_air']),'carrier_scale':number('1',.01,10),'drift_flow_m3_s':number('m^3/s',1e-9,1e-4),'retention_scale':number('1',.05,20),'drift_ratio_scale':number('1',.1,10),'run_s':number('s',1,7200),'accept_method_transfer':BOOL})
METHOD_EXAMPLE=dict(gc_k=333.15,ims_k=318.15,injector_k=358.15,incubator_k=353.15,incubation_s=10.,injection_m3=500e-9,column_length_m=15.,gas='nitrogen',carrier_scale=1.,drift_flow_m3_s=150e-6/60,retention_scale=1.,drift_ratio_scale=1.,run_s=180.,accept_method_transfer=True)
SOLUTE=obj({'substance_id':TEXT,'mole_fraction':number('1',0,1)})
SAMPLE=obj({'sample_id':TEXT,'vial_index':dict(number('1',0,59),type='integer'),'vial_m3':enum(['10mL','20mL']),'headspace_m3':number('m^3',1e-9,20e-6),'pressure_pa':number('Pa',1e4,3e5),'temperature_k':number('K',278.15,473.15),'solutes':dict(arr(SOLUTE),minItems=1,maxItems=77)})
SAMPLE_EXAMPLE=dict(sample_id='headspace_01',vial_index=0,vial_m3='20mL',headspace_m3=15e-6,pressure_pa=101325.,temperature_k=353.15,solutes=[dict(substance_id='cas_64_17_5',mole_fraction=1e-5),dict(substance_id='cas_67_64_1',mole_fraction=2e-5)])
DRIFT_AXIS=np.linspace(.6,2.5,96).tolist();SIGNAL_ARRAY=arr(number('1',0),96)
POINT=obj({'time_s':number('s',0),'window_s':number('s',0),'signal':SIGNAL_ARRAY})
RESULT=obj({'phase':PHASE,'workflow':TEXT,'complete':BOOL,'points':arr(POINT),'drift_ratio_axis':arr(number('1'),96),'elapsed_s':number('s',0),'template_source':TEXT,'evidence':TEXT,'warnings':arr(TEXT)})
ROI=obj({'start_s':number('s',0),'stop_s':number('s',0),'drift_min_ratio':number('1',.6,2.5),'drift_max_ratio':number('1',.6,2.5)})
ROI_EXAMPLE=dict(start_s=0.,stop_s=180.,drift_min_ratio=.6,drift_max_ratio=2.5)
CAL_POINT=obj({'concentration_mol_m3':number('mol/m^3',0),'area_s':number('s',0)})
CAL=obj({'roi':ROI,'standards':dict(arr(CAL_POINT),minItems=2,maxItems=100),'label':TEXT})
CAL_EXAMPLE=dict(roi=ROI_EXAMPLE,standards=[dict(concentration_mol_m3=0.,area_s=0.),dict(concentration_mol_m3=.1,area_s=50.)],label='external example calibration; synthetic')
LEDGER=obj({'gas_remaining_mol':number('mol',0),'gas_consumed_mol':number('mol',0),'sample_remaining_mol':number('mol',0),'syringe_mol':number('mol',0),'column_mol':number('mol',0),'exhaust_mol':number('mol',0),'total_residual_mol':number('mol'),'analyte_residual_mol':number('mol'),'sample_id':TEXT})
CAPS={};UNSUPPORTED={'hardware.connect','ion_chemistry.solve'}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},effects or [])
cap('plant.configure',PLANT,obj({'configured':BOOL}),['housing','ims_tube'],'初始化有限气源和热/运动/峰展宽/离子信号假设','configuration','evaluator',PLANT_EXAMPLE,['plant.configured'])
cap('method.configure',METHOD,obj({'configured':BOOL}),['gc_oven','ims_tube','incubator','syringe_body'],'设GC/IMS≤100C、针≤200C、≤60m柱及显式方法迁移','configuration','agent',METHOD_EXAMPLE,['method.configured','gc.target_k','ims.target_k','injector.target_k','incubator.target_k','calibration.ready'])
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['vial_rack'],'宿主放置10或20mL瓶，给有限已平衡顶空气体组成','configuration','evaluator',SAMPLE_EXAMPLE,['sample.loaded','sample.identity','sample.vial_index'])
cap('sample.unload',obj({}),obj({'returned_mol':number('mol',0)}),['vial_rack'],'只归还原瓶内余气，不回滚注射器与柱内物质','device_command','evaluator',effects=['sample.loaded','sample.identity'])
for k,part,z in [('power.set','power_inlet','逻辑供电；断电取消并停止供气目标'),('gas.set','gas_inlet','有限气源启停目标；step唯一消费气体')]:cap(k,obj({'enabled':BOOL}),obj({'enabled':BOOL}),[part],z,ex={'enabled':True},effects=['power.enabled','gas.enabled','acquisition.phase'] if k=='power.set' else ['gas.enabled'])
cap('cover.set',obj({'angle_rad':number('rad',0,1.45)}),obj({'angle_rad':number('rad',0,1.45)}),['service_cover'],'主机检修盖工程关节；忙碌/供气时拒绝开启',ex={'angle_rad':0.},effects=['cover.angle_rad'])
AXES=obj({'x_m':number('m',-.47,.40),'y_m':number('m',0,.39),'z_m':number('m',0,.18)})
cap('gantry.move',AXES,AXES,['gantry_x','gantry_y','syringe_z'],'空闲宿主设PAL工程轴位置；非机器人接触操纵',ex={'x_m':0.,'y_m':0.,'z_m':0.},effects=['gantry.x_m','gantry.y_m','gantry.z_m'])
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['incubator','syringe_body','gc_oven','ims_tube'],'孵育、PAL移动、有限吸样/注入、二维采集；异步且可取消',effects=['acquisition.phase','workflow.phase','acquisition.elapsed_s'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['syringe_body','gc_oven','ims_tube'],'停止程序和供气目标，原瓶/针筒/柱/排气保留各自物质',effects=['acquisition.phase','gas.enabled'])
cap('syringe.purge',obj({}),obj({'purged_mol':number('mol',0)}),['syringe_body','exhaust_port'],'将残留针筒气体明确排到排气账本','device_command','evaluator')
cap('process.measure',obj({}),RESULT,['ims_tube','display_digits'],'读已采集二维窗口及无量纲RIP漂移轴','query')
cap('signal.integrate',ROI,obj({'area_s':number('s',0)}),['ims_tube'],'从观测二维窗口积分选区；不读取物种标签','analysis','agent',ROI_EXAMPLE)
cap('calibration.fit',CAL,obj({'slope_s_m3_mol':number('s*m^3/mol',0),'intercept_s':number('s'),'r_squared':number('1',0,1)}),['ims_tube'],'用外部标准浓度/观测积分拟合标线，限定当前方法与ROI','analysis','agent',CAL_EXAMPLE,['calibration.ready'])
cap('concentration.analyze',obj({}),obj({'concentration_mol_m3':nullable(number('mol/m^3',0)),'label':TEXT,'quality':TEXT,'evidence':TEXT}),['ims_tube'],'观测ROI积分与标线浓度；完成/范围检查，绝不读取隐藏样品真值','analysis')
cap('fingerprint.compare',obj({'reference':arr(number('1',0),1920)}),obj({'cosine_similarity':nullable(number('1',0,1)),'features':arr(number('1',0),1920),'evidence':TEXT}),['ims_tube'],'20保留时间格×96漂移格的观测指纹与外部参考比较','analysis',ex={'reference':[0.]*1920})
cap('template.read',obj({'substance_id':TEXT}),obj({'substance_id':TEXT,'retention_times_s':arr(number('s',0)),'drift_ratios':arr(number('1',0)),'template_source':TEXT,'evidence':TEXT}),['ims_tube'],'公开实验峰位置模板；源方法与25型号迁移未验证','query',ex={'substance_id':'cas_64_17_5'})
cap('ledger.read',obj({}),LEDGER,['vial_rack','syringe_body','gc_oven','gas_inlet','exhaust_port'],'私有有限摩尔量转移与守恒账本','query','evaluator')
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['housing'],'真实硬件或完整离子簇反应未实现')
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='display_digits',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','宿主气源与条件模型已配置','setpoint',c='housing');slot('method.configured',BOOL,'1','有效方法已配置','setpoint')
for sid,capid,comp in [('power.enabled','power.set','power_inlet'),('gas.enabled','gas.set','gas_inlet')]:slot(sid,BOOL,'1','运行目标；物理仅宿主step推进','setpoint',c=comp,w=(capid,'enabled'))
for name,comp,hi in [('gc','gc_oven',373.15),('ims','ims_tube',373.15),('injector','syringe_body',473.15),('incubator','incubator',473.15)]:
 slot(name+'.target_k',nullable(number('K',278.15,hi)),'K','模块温度目标；孵育温度上界沿用宿主工程限制，非独立PAL额定证据','setpoint',c=comp)
 slot(name+'.temperature_k',nullable(number('K')),'K','单节点实际温度真值','process_truth','evaluator',comp)
 slot(name+'.indicated_k',nullable(number('K')),'K','理想化温度观测，无实测噪声')
slot('cover.angle_rad',number('rad',0,1.45),'rad','服务盖绕+X开合角；零闭合','scene',c='service_cover',w=('cover.set','angle_rad'))
for name,lo,hi,comp in [('x',-.47,.40,'gantry_x'),('y',0,.39,'gantry_y'),('z',0,.18,'syringe_z')]:slot('gantry.'+name+'_m',number('m',lo,hi),'m','PAL工程轴q；X沿+X/Y沿-Y/Z沿-Z；任务中step按阶段移动','scene',c=comp)
slot('sample.loaded',BOOL,'1','有限顶空瓶已放置',c='vial_rack');slot('sample.identity',TEXT,'1','瓶身份真值','process_truth','evaluator','vial_rack');slot('sample.vial_index',nullable(dict(number('1',0,59),type='integer')),'1','60位瓶架公开位置',c='vial_rack')
slot('acquisition.phase',PHASE,'1','采集任务阶段');slot('workflow.phase',TEXT,'1','孵育/轴运动/吸样/注入/分离子阶段');slot('acquisition.elapsed_s',number('s',0),'s','进样后的GC采集时间')
slot('detector.total_signal',nullable(number('1',0)),'1','最近GC时间窗口内96个IMS漂移通道的信号和；响应增益显式假设')
slot('gas.remaining_mol',number('mol',0),'mol','有限载气和漂移气共享供给的剩余摩尔量','process_truth','evaluator','gas_inlet');slot('calibration.ready',BOOL,'1','当前方法和ROI存在外部标线')

def source_carrier_volume(t):
 t=max(0.,t);v=2*min(t,120.)
 if t>120:
  z=min(t-120,1080.);v+=2*z+(.5*98/1080)*z*z
 if t>1200:v+=100*(t-1200)
 return v*1e-6/60

def source_carrier_flow(t):return (2. if t<=120 else 2+98*(t-120)/1080 if t<1200 else 100)*1e-6/60

def vial_target(index):return [-.46+(index%10)*.030,.05-(-.31+(index//10)*.033),0.]

class GasFlavourspec25gcRuntime:
 def __init__(self,instance_id='gcims_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.gas=False;self.cover=0.;self.q=[0.,0.,0.];self.plant=None;self.method=None;self.temperature={k:None for k in ['gc','ims','injector','incubator']};self.supply=0.;self.consumed=0.;self.sample=None;self.sample_loaded_n=0.;self.sample_returned_n=0.;self.syringe=None;self.sample_exhaust_n=0.;self.bands=[];self.phase='idle';self.workflow='idle';self.stage_elapsed=0.;self.stage_start_q=[0.,0.,0.];self.elapsed=0.;self.gc_volume=0.;self.points=[];self.signal=None;self.calibration=None;self.serial=0;self.operation_id=None;self.records={};self.templates=json.loads((ROOT/TEMPLATE_REL).read_text())['substances'];self._principals={};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ['agent','evaluator','debug'] or principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return GasFlavourspec25gcSession(self,principal_id)
 def describe(self):return export_gas_flavourspec25gc_contract_metadata()
 def read(self,visibility='agent'):
  m=self.method or {};d={'plant.configured':self.plant is not None,'method.configured':self.method is not None,'power.enabled':self.powered,'gas.enabled':self.gas,'cover.angle_rad':self.cover,'sample.loaded':self.sample is not None,'sample.identity':self.sample['sample_id'] if self.sample else '','sample.vial_index':self.sample['vial_index'] if self.sample else None,'acquisition.phase':self.phase,'workflow.phase':self.workflow,'acquisition.elapsed_s':self.elapsed,'detector.total_signal':self.signal,'gas.remaining_mol':self.supply,'calibration.ready':self.calibration is not None}
  for name in self.temperature:d.update({name+'.target_k':m.get(name+'_k'),name+'.temperature_k':self.temperature[name],name+'.indicated_k':self.temperature[name]})
  for i,name in enumerate(['x','y','z']):d['gantry.'+name+'_m']=self.q[i]
  return copy.deepcopy({k:v for k,v in d.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _ledger(self):
  sn=self.sample['remaining_n'] if self.sample else 0.;sy=self.syringe['n'] if self.syringe else 0.;col=sum(b['n']-b['eluted'] for b in self.bands);inj=sum(b['n'] for b in self.bands);out=sum(b['eluted'] for b in self.bands);init=self.plant['gas_supply_mol'] if self.plant else 0.;exhaust=self.consumed+self.sample_exhaust_n
  return dict(gas_remaining_mol=self.supply,gas_consumed_mol=self.consumed,sample_remaining_mol=sn,syringe_mol=sy,column_mol=col,exhaust_mol=exhaust,total_residual_mol=init+self.sample_loaded_n-self.supply-sn-sy-col-exhaust-self.sample_returned_n,analyte_residual_mol=inj-out-col,sample_id=self.sample['sample_id'] if self.sample else '')
 def _result(self):return dict(phase=self.phase,workflow=self.workflow,complete=self.phase=='succeeded',points=copy.deepcopy(self.points),drift_ratio_axis=DRIFT_AXIS[:],elapsed_s=self.elapsed,template_source='Ma et al. 10.1093/jaoacint/qsac108 Table1; identities withheld',evidence='measured_literature positions; synthetic reconstructed 2D peak widths, gas transfer and detector response',warnings=['Source FlavourSpec is not FlavourSpec25 calibration','Drift axis is dimensionless t/t_RIP, NOT milliseconds or absolute mobility','Known finite equilibrated headspace supplied by host; no matrix partition/ion cluster chemistry','Gain/peak shapes/reference gas state/method transfer explicitly assumed'])
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[k][0],a);before=copy.deepcopy(self.__dict__)
  try:
   result=self._invoke(k,a);validate(CAPS[k][1],result)
   if CAPS[k][4] not in ['query','analysis'] or k=='calibration.fit':self.revision+=1
   return result
  except Exception:self.__dict__.clear();self.__dict__.update(before);raise
 def _area(self,roi):
  if roi['stop_s']<=roi['start_s'] or roi['drift_max_ratio']<=roi['drift_min_ratio'] or roi['stop_s']>self.elapsed+1e-8:raise ActionRejected('INVALID_WINDOW')
  selected=[i for i,x in enumerate(DRIFT_AXIS) if roi['drift_min_ratio']<=x<=roi['drift_max_ratio']]
  return sum(sum(p['signal'][i] for i in selected)*max(0.,min(p['time_s'],roi['stop_s'])-max(p['time_s']-p['window_s'],roi['start_s'])) for p in self.points)
 def _invoke(self,k,a):
  if k=='process.measure':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='template.read':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   t=self.templates[a['substance_id']];return dict(substance_id=a['substance_id'],retention_times_s=[x['retention_time_s'] for x in t['channels']],drift_ratios=[x['drift_ratio'] for x in t['channels']],template_source=t['source']['url']+' # Table1 '+t['cas'],evidence='measured_literature; source method conditions retained')
  if k=='signal.integrate':return {'area_s':self._area(a)}
  if k=='fingerprint.compare':
   f=np.zeros((20,96));span=self.method['run_s'] if self.method else 1.
   for p in self.points:
    lo=p['time_s']-p['window_s'];hi=p['time_s']
    for j in range(20):f[j]+=np.asarray(p['signal'])*max(0.,min(hi,(j+1)*span/20)-max(lo,j*span/20))
   f=f.ravel();ref=np.asarray(a['reference']);den=float(np.linalg.norm(f)*np.linalg.norm(ref));score=float(np.dot(f,ref)/den) if den else None;return dict(cosine_similarity=min(1.,max(0.,score)) if score is not None else None,features=f.tolist(),evidence='integrated observed 20x96 bins; similarity alone is not chemical identification')
  if k=='calibration.fit':
   if not self.method or self.phase=='running':raise ActionRejected('PRECONDITION_FAILED')
   roi=a['roi']
   if not 0<=roi['start_s']<roi['stop_s']<=self.method['run_s'] or roi['drift_min_ratio']>=roi['drift_max_ratio']:raise ActionRejected('INVALID_WINDOW')
   x=np.asarray([v['concentration_mol_m3'] for v in a['standards']]);y=np.asarray([v['area_s'] for v in a['standards']]);sx=float(np.sum((x-x.mean())**2));sy=float(np.sum((y-y.mean())**2))
   if sx==0 or sy==0:raise ActionRejected('INVALID_CALIBRATION')
   slope=float(np.sum((x-x.mean())*(y-y.mean()))/sx);intercept=float(y.mean()-slope*x.mean());r2=max(0.,1-float(np.sum((y-intercept-slope*x)**2))/sy)
   if slope<=0 or r2<.95:raise ActionRejected('INVALID_CALIBRATION')
   self.calibration=dict(roi=copy.deepcopy(roi),slope=slope,intercept=intercept,lo=float(min(x)),hi=float(max(x)),label=a['label']);return dict(slope_s_m3_mol=slope,intercept_s=intercept,r_squared=r2)
  if k=='concentration.analyze':
   c=self.calibration;v=None;quality='incomplete'
   if self.phase=='succeeded':
    if not c:quality='uncalibrated'
    else:
     estimate=(self._area(c['roi'])-c['intercept'])/c['slope'];quality='estimated' if c['lo']<=estimate<=c['hi'] else 'outside_calibration';v=estimate if quality=='estimated' else None
   return dict(concentration_mol_m3=v,label=c['label'] if c else '',quality=quality,evidence='observed ROI integral and supplied external calibration; no sample composition access')
  if k=='plant.configure':
   if self.plant or self.powered or not a['accept_synthetic_response']:raise ActionRejected('INITIALIZATION_ONLY')
   self.plant=copy.deepcopy(a);self.supply=a['gas_supply_mol'];self.temperature={k:a['ambient_k'] for k in self.temperature};return {'configured':True}
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self.phase='cancelled';self.gas=False;self.records[self.operation_id]=self._result();return {'phase':self.phase}
  if k=='power.set':
   self.powered=a['enabled']
   if not self.powered:
    self.gas=False
    if self.phase=='running':self.phase='cancelled';self.records[self.operation_id]=self._result()
   return a
  if self.phase=='running':raise ActionRejected('BUSY')
  if k=='method.configure':
   if not self.plant or self.gas or not a['accept_method_transfer']:raise ActionRejected('PRECONDITION_FAILED')
   if min(a[k+'_k'] for k in self.temperature)<self.plant['ambient_k']:raise ActionRejected('TEMPERATURE_RANGE')
   self.method=copy.deepcopy(a);self.calibration=None;return {'configured':True}
  if k=='gas.set':
   if a['enabled'] and (not self.powered or not self.method or self.cover>1e-6 or self.supply<=0):raise ActionRejected('NOT_READY')
   self.gas=a['enabled'];return a
  if k=='cover.set':
   if self.gas:raise ActionRejected('BUSY')
   self.cover=a['angle_rad'];return a
  if k=='gantry.move':self.q=[a['x_m'],a['y_m'],a['z_m']];return a
  if k=='sample.load':
   if self.sample or not self.plant or a['headspace_m3']>(10e-6 if a['vial_m3']=='10mL' else 20e-6) or sum(s['mole_fraction'] for s in a['solutes'])>1 or len({s['substance_id'] for s in a['solutes']})!=len(a['solutes']):raise ActionRejected('SAMPLE_INVALID')
   if any(s['substance_id'] not in self.templates for s in a['solutes']):raise ActionRejected('UNKNOWN_REFERENCE')
   self.sample=copy.deepcopy(a);n=a['pressure_pa']*a['headspace_m3']/(R*a['temperature_k']);self.sample['remaining_n']=n;self.sample['species']={s['substance_id']:n*s['mole_fraction'] for s in a['solutes']};self.sample_loaded_n+=n;return {'loaded':True}
  if k=='sample.unload':
   if not self.sample:raise ActionRejected('NO_SAMPLE')
   n=self.sample['remaining_n'];self.sample_returned_n+=n;self.sample=None;return {'returned_mol':n}
  if k=='syringe.purge':
   n=self.syringe['n'] if self.syringe else 0.;self.sample_exhaust_n+=n;self.syringe=None;return {'purged_mol':n}
  if k=='acquisition.start':
   if not self.sample or not self.method or not self.gas or self.cover>1e-6 or self.syringe or self._ledger()['column_mol']>1e-15:raise ActionRejected('NOT_READY')
   if max(abs(self.temperature[k]-self.method[k+'_k']) for k in ['gc','ims','injector','incubator'])>1. or self.method['injection_m3']>self.sample['headspace_m3']:raise ActionRejected('NOT_READY')
   self.serial+=1;self.operation_id=f'{self.instance_id}:op:{self.serial}';self.phase='running';self.workflow='incubating';self.stage_elapsed=0.;self.stage_start_q=self.q[:];self.elapsed=0.;self.gc_volume=0.;self.points=[];self.signal=None;return dict(phase=self.phase,operation_id=self.operation_id)
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _stages(self):
  v=vial_target(self.sample['vial_index']);down=[v[0],v[1],.145];inlet=[.10,.04,0.];dip=[.10,.04,.065];p=self.plant
  return [('incubating',self.method['incubation_s'],self.q),('above_vial',p['travel_s'],v),('lower_vial',p['stroke_s'],down),('aspirating',p['stroke_s'],down),('retract_vial',p['stroke_s'],v),('above_inlet',p['travel_s'],inlet),('lower_inlet',p['stroke_s'],dip),('injecting',p['stroke_s'],dip),('retract_inlet',p['stroke_s'],inlet)]
 def _transition(self):
  stages=self._stages();idx=[s[0] for s in stages].index(self.workflow)
  if self.workflow=='aspirating':
   fraction=self.method['injection_m3']/self.sample['headspace_m3'];n=self.sample['remaining_n']*fraction;species={k:v*fraction for k,v in self.sample['species'].items()};self.sample['remaining_n']-=n
   for k,v in species.items():self.sample['species'][k]-=v
   self.syringe=dict(n=n,species=species)
  if self.workflow=='injecting':
   sy=self.syringe
   for sid,n in sy['species'].items():
    channels=self.templates[sid]['channels'];weights=[x['signal_volume_arb_units'][0] for x in channels];total=sum(weights)
    for ch,w in zip(channels,weights):
     mu=ch['drift_ratio']*self.method['drift_ratio_scale'];sigma=self.plant['drift_sigma_ratio'];dx=DRIFT_AXIS[1]-DRIFT_AXIS[0];cdf=lambda x:.5*(1+math.erf((x-mu)/(sigma*math.sqrt(2))));probs=[cdf(x+dx/2)-cdf(x-dx/2) for x in DRIFT_AXIS];self.bands.append(dict(n=n*w/total,eluted=0.,volume=0.,mean=source_carrier_volume(ch['retention_time_s'])*self.method['retention_scale'],sigma=max(1e-12,source_carrier_flow(ch['retention_time_s'])*self.plant['gc_sigma_s']),prob=probs))
   self.sample_exhaust_n+=sy['n']-sum(sy['species'].values());self.syringe=None
  self.workflow=stages[idx+1][0] if idx+1<len(stages) else 'separating';self.stage_elapsed=0.;self.stage_start_q=self.q[:]
 def _gas_need(self,duration,collecting):
  m=self.method;carrier=m['carrier_scale']*(source_carrier_volume(self.elapsed+duration)-source_carrier_volume(self.elapsed)) if collecting else m['carrier_scale']*source_carrier_flow(0)*duration;total=carrier+m['drift_flow_m3_s']*duration;n=total*self.plant['gas_reference_pa']/(R*self.plant['gas_reference_k']);return n,carrier
 def _advance(self,duration,collecting):
  n,v=self._gas_need(duration,collecting);actual=duration
  if n>self.supply:
   lo,hi=0.,duration
   for _ in range(55):mid=(lo+hi)/2;need,_=self._gas_need(mid,collecting);lo,hi=(mid,hi) if need<=self.supply else (lo,mid)
   actual=lo;n,v=self._gas_need(actual,collecting)
  self.supply=max(0.,self.supply-n);self.consumed+=n;signal=np.zeros(96)
  for b in self.bands:
   b['volume']+=v;cdf=lambda x:.5*(1+math.erf((x-b['mean'])/(b['sigma']*math.sqrt(2))));f0=cdf(0);new=b['n']*max(0.,min(1.,(cdf(b['volume'])-f0)/(1-f0)));dn=max(0.,new-b['eluted']);b['eluted']=new;self.sample_exhaust_n+=dn
   if collecting and actual>0:signal+=self.plant['gain_s_per_mol']*dn/actual*np.asarray(b['prob'])
  if collecting and actual>0:self.elapsed+=actual;self.gc_volume+=v;self.signal=float(signal.sum());self.points.append(dict(time_s=self.elapsed,window_s=actual,signal=signal.tolist()))
  if actual<duration-1e-9:
   self.gas=False;self.phase='failed' if self.phase=='running' else self.phase
   if self.operation_id:self.records[self.operation_id]=self._result()
  return actual
 def step(self,dt_s):
  validate(number('s',0,10000),dt_s)
  if dt_s==0:return self.read()
  dt=float(dt_s);self.time_s+=dt;self.revision+=1
  if self.plant:
   for k,t in self.temperature.items():target=self.method[k+'_k'] if self.method and self.powered else self.plant['ambient_k'];self.temperature[k]=target+(t-target)*math.exp(-dt/self.plant['thermal_tau_s'])
  remain=dt
  while remain>1e-10 and self.powered and self.gas and self.method:
   if self.phase!='running':self._advance(remain,False);break
   if self.workflow=='separating':
    duration=min(remain,max(0.,self.method['run_s']-self.elapsed));used=self._advance(duration,True);remain-=used
    if self.phase=='running' and self.elapsed>=self.method['run_s']-1e-8:self.phase='succeeded';self.workflow='complete';self.gas=False;self.records[self.operation_id]=self._result()
    if used<=0:break
   else:
    name,total,target=next(s for s in self._stages() if s[0]==self.workflow);duration=min(remain,max(0.,total-self.stage_elapsed));collect=self.workflow=='retract_inlet';duration=min(duration,max(0.,self.method['run_s']-self.elapsed)) if collect else duration;used=self._advance(duration,collect);remain-=used;self.stage_elapsed+=used
    fraction=1. if total==0 else min(1.,self.stage_elapsed/total);self.q=[a+(b-a)*fraction for a,b in zip(self.stage_start_q,target)]
    if self.phase!='running':break
    if collect and self.elapsed>=self.method['run_s']-1e-8:self.phase='succeeded';self.workflow='complete';self.gas=False;self.records[self.operation_id]=self._result();break
    if self.stage_elapsed>=total-1e-9:self._transition()
    elif used<=0:break
  return self.read()
class GasFlavourspec25gcSession:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,i):
  if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  m=export_gas_flavourspec25gc_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
 def read(self,*,instance_id,state_ids=None):
  self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
  if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
  if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
  return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='unavailable' if v[k] is None else 'estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=h.time_s,sample_window_s=[h.points[-1]['time_s']-h.points[-1]['window_s'],h.points[-1]['time_s']] if k=='detector.total_signal' and h.points else None) for k in ids})
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
  record=h.records.get(operation_id)
  if operation_id==h.operation_id:record=h._result()
  if record is None:raise ActionRejected('UNKNOWN_OPERATION')
  return dict(operation_id=operation_id,status=record['phase'],result=copy.deepcopy(record))
 def cancel(self,operation_id,request_id):
  self.poll(operation_id)
  if operation_id!=self._host.operation_id:raise ActionRejected('NOT_RUNNING')
  return self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)



def export_gas_flavourspec25gc_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():
  ref='气体绝对压力' if u=='Pa' else '以plant气体参考温度/绝对压力定义的气体体积流量' if u=='m^3/s' else None
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=ref,origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'GasFlavourspec25gcSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  unsupported=k in UNSUPPORTED;asyn=k=='acquisition.start'
  caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.gas_flavourspec25gc.GasFlavourspec25gcSession.invoke',invocation=f'session.invoke(instance_id="gcims_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if kind in ['query','analysis'] or unsupported else [dict(description_zh='类型/身份、方法限值、闭门、温度准备、有限体积及忙碌检查；不冒充原厂安全互锁',state_ids=['power.enabled','gas.enabled','method.configured','cover.angle_rad','acquisition.phase'],enforced_by='runtime',check='atomic Runtime.invoke plus trusted-principal Session')],state_effects=[dict(state_id=x,timing='immediate',description_zh='受检事务目标/逻辑状态变化') for x in effects],observes=[],model_effects=dict(mode='none',description_zh='等待独立实际场景求值；数值过程不自动证明模型联动',binding_ids=[]),execution=dict(mode='asynchronous' if asyn else 'synchronous',time_domain='simulation',completion_criterion='采集实际时间达到run_s；残留柱物质仍记账' if asyn else '受检事务返回；read/describe/poll不推进时钟',completion_state_ids=['acquisition.phase'] if asyn else [],timeout_s=None,cancellation='supported' if asyn else 'unsupported',retry='same_request_id',resources=['host_clock','finite_headspace','finite_gas_supply','gas_exhaust','request_cache'],partial_failure_zh='已进样/已流出物质保留；取消停止气体目标，不逆向回滚；去重仅当前宿主进程'),errors=[dict(code=x,meaning_zh=x,side_effects='none') for x in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','PRECONDITION_FAILED','INITIALIZATION_ONLY','BUSY','COLUMN_RANGE','SAMPLE_COMPATIBILITY','PRESSURE_LIMIT','INVALID_CALIBRATION','SAMPLE_METHOD_REQUIRED','PREPARATION_REQUIRED','DUPLICATE_SOLUTE','UNKNOWN_REFERENCE','COLUMN_POLARITY','NO_SAMPLE','NOT_READY','SAMPLE_OR_CARRYOVER','WASTE_FULL','NOT_RUNNING','INVALID_WINDOW']],example_arguments=copy.deepcopy(ex),verification=[],limitations=['教学过程；GC/IMS模板方法迁移、峰宽、离子增益与实机未校准'],source_ids=['manufacturer_primary','simulation_assumption'])
  if asyn:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='唯一由宿主step推进') for x in ['acquisition.elapsed_s','detector.total_signal','gantry.x_m','gantry.y_m','gantry.z_m','acquisition.phase']]
 mech=dict(component_ids=['vial_rack','incubator','syringe_body','gc_oven','ims_tube','gas_inlet','exhaust_port'],state_ids=list(slots),meaning_zh='有限顶空气体与载气的GC-IMS二维峰位重建；机械轴由宿主step推进',implementation='lumped',equations=['n=P*V/(R*T); finite equilibrated headspace supplied explicitly, not inferred from food matrix','Aspirated amount = remaining vial gas * injection_volume/headspace_volume; vial -> syringe -> column/exhaust; cancellation preserves each compartment','N2 required = (carrier_reference_volume+drift_reference_volume)*P_reference/(R*T_reference); gas exhaustion truncates time','Source carrier program 2mL/min first120s, linear to100mL/min at1200s, then100mL/min; integral defines synthetic retention-volume coordinate','Eluted analyte amount = injected amount * positive-truncated Gaussian CDF(carrier_volume); signal=sum(gain*dn/dt * IMS-bin Gaussian probability)','IMS axis is measured t_d/t_RIP dimensionless; widths/gain/method transfer assumed; source QH1 monomer/dimer relative volumes used only as explicit matrix-dependent assumed channel weights','ROI area from observed signal windows; external standards area=slope*concentration+intercept; no hidden composition read by analyzer','Fingerprint cosine similarity uses observed integrated20x96 bins; no direct identity inference','Tnew=Tset+(Told-Tset)*exp(-dt/tau); single-node thermal surrogate; PAL stages use explicit engineering travel durations'],parameters={},source_ids=['manufacturer_primary','gc_ims_literature','simulation_assumption'],limitations=['77 distinct CAS with80 empirical peaks from one food-matrix study on earlier FlavourSpec; no raw electronic 2D response arrays.','GC/IMS max100C and syringe max200C from FlavourSpec25; incubator bound and travel/thermal parameters are engineering assumptions.','Host supplies an already-equilibrated finite headspace; no matrix partition kinetics, pressure regulation or actual vial opening.','Source drift coordinates are dimensionless RIP ratios; absolute drift seconds, mobility and reduced mobility are unknown.','Signal widths/gain, source monomer/dimer weights and method transfer are explicit synthetic assumptions; no ion reaction chemistry/noise/saturation model.','Concentrations require caller-supplied calibration; source food peak volumes are not pure standards.','Mass and geometry motions verified only within the implemented surrogate; robot manipulation/hardware not_run.','Trusted-host Session filtering is not filesystem or process isolation.'])
 spec=importlib.util.spec_from_file_location('_ims_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'gc_ims':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'。'
 slots['detector.total_signal']['sampling']=dict(mode='interval_mean',time_domain='simulation',window_description='最近实际GC时间窗口内96个漂移通道信号和；停止后保持')
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'gc_ims':mech})

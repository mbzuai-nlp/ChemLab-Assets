"""CHEM-104 SECM: finite effective redox and charge ledgers, time-driven XYZ and observed-current kinetics.
Trusted Python host only. Sessions filter identity/truth; process/file isolation is external.
"""
from pathlib import Path
import copy,math,json,importlib.util
PROTOCOL_VERSION='1.0.0';SLUG='ch_chi920d';CATALOG_ID='CHEM-104';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];TEMPLATE_REL='assets/expansion_batch04/t4_measure/'+SLUG+'/diffusion_library.json'
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
F=96485.33212;R=8.31446261815324;T=298.15
PLANT=obj({'tip_radius_m':number('m',1e-8,1e-4),'travel_speed_m_s':number('m/s',1e-8,.02),'current_range_a':number('A',1e-11,.25),'temperature_k':number('K',298.15,298.15),'fine_enabled':BOOL,'accept_feedback_surrogate':BOOL})
PLANT_EXAMPLE=dict(tip_radius_m=5e-6,travel_speed_m_s=.002,current_range_a=1e-6,temperature_k=298.15,fine_enabled=True,accept_feedback_surrogate=True)
XYZ=obj({'x_m':number('m',-.025,.025),'y_m':number('m',-.025,.025),'z_m':number('m',0,.05)})
METHOD=obj({'mode':enum(['constant_height','approach']),'path':dict(arr(XYZ),minItems=1,maxItems=2048),'dwell_s':number('s',.001,600),'tip_potential_v':number('V',-10,10),'substrate_potential_v':number('V',-10,10),'reference_electrode':TEXT,'min_gap_m':number('m',1e-9,.01)})
METHOD_EXAMPLE=dict(mode='constant_height',path=[dict(x_m=(-2+i if j%2==0 else 2-i)*20e-6,y_m=(-2+j)*20e-6,z_m=5e-6) for j in range(5) for i in range(5)],dwell_s=.3,tip_potential_v=-.2,substrate_potential_v=.2,reference_electrode='explicit synthetic reference; E0=0',min_gap_m=5e-7)
SAMPLE=obj({'sample_id':TEXT,'substance_id':TEXT,'volume_m3':number('m^3',1e-12,1e-3),'concentration_mol_m3':number('mol/m^3',1e-9,1e5),'oxidized_fraction':number('1',0,1),'electrons':dict(number('1',1,10),type='integer'),'formal_potential_v':number('V',-10,10),'surface_z_m':number('m',0,.005),'footprint_radius_m':number('m',1e-5,.025),'base_rate_m_s':number('m/s',0,1),'patch_rate_m_s':number('m/s',0,1),'patch_x_m':number('m',-.025,.025),'patch_y_m':number('m',-.025,.025),'patch_sigma_m':number('m',1e-8,.025),'accept_effective_redox':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='synthetic_surface',substance_id='o2_neutral',volume_m3=1e-6,concentration_mol_m3=1.,oxidized_fraction=1.,electrons=4,formal_potential_v=0.,surface_z_m=0.,footprint_radius_m=.01,base_rate_m_s=1e-6,patch_rate_m_s=1e-3,patch_x_m=0.,patch_y_m=0.,patch_sigma_m=20e-6,accept_effective_redox=True)
POINT=obj({'time_s':number('s',0),'window_s':number('s',0),'x_m':number('m'),'y_m':number('m'),'z_m':number('m'),'tip_current_a':number('A'),'substrate_current_a':number('A')})
RESULT=obj({'phase':PHASE,'complete':BOOL,'points':arr(POINT),'elapsed_s':number('s',0),'template_source':TEXT,'evidence':TEXT,'warnings':arr(TEXT)})
LEDGER=obj({'oxidized_mol':number('mol',0),'reduced_mol':number('mol',0),'loaded_mol':number('mol',0),'returned_mol':number('mol',0),'tip_charge_c':number('C',0),'substrate_charge_c':number('C',None,0),'total_residual_mol':number('mol'),'charge_residual_c':number('C'),'sample_id':TEXT})
KIN=obj({'diffusion_m2_s':number('m^2/s',1e-12,1e-7),'tip_radius_m':number('m',1e-8,1e-4),'substrate_plane_z_m':number('m',0,.005),'substrate_activation':number('1',1e-6,1)})
KIN_EXAMPLE=dict(diffusion_m2_s=2.35e-09,tip_radius_m=5e-6,substrate_plane_z_m=0.,substrate_activation=1/(1+math.exp(-4*F*.2/(R*T))))
CAPS={};UNSUPPORTED={'feedback.constant_current','impedance.acquire','voltammetry.full_suite','hardware.connect'}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},effects or [])
cap('plant.configure',PLANT,obj({'configured':BOOL}),['probe_tip','positioner'],'显式设置针径、速度、量程和反馈工程代理','configuration','evaluator',PLANT_EXAMPLE,['plant.configured'])
cap('method.configure',METHOD,obj({'configured':BOOL}),['positioner','bipotentiostat'],'设置固定高度栅格/接近路径及双通道电位参考','configuration','agent',METHOD_EXAMPLE,['method.configured','tip.target_v','substrate.target_v'])
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_cell','substrate'],'宿主放置有限O/R有效介体与私有基底反应图','configuration','evaluator',SAMPLE_EXAMPLE,['sample.loaded','sample.identity'])
cap('sample.unload',obj({}),obj({'returned_mol':number('mol',0)}),['sample_cell'],'抬起探针后归还池内剩余O/R，不回滚已流电荷','device_command','evaluator',effects=['sample.loaded','sample.identity'])
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['bipotentiostat','motor_controller'],'逻辑供电；断电取消采集和移动目标',ex={'enabled':True},effects=['power.enabled','acquisition.phase'])
cap('stage.move',XYZ,XYZ,['stage_x','stage_y','stage_z'],'设置三轴量化目标；只有step推进实际位置',ex={'x_m':0.,'y_m':0.,'z_m':.005},effects=['stage.target_x_m','stage.target_y_m','stage.target_z_m'])
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['probe_tip','substrate','positioner'],'按路径移动、停留和采样；有限氧化还原/电荷过程',effects=['acquisition.phase','acquisition.elapsed_s','acquisition.completed_points'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['probe_tip','positioner'],'停止目标与电流；保留位置、部分曲线及已反应物质/电荷',effects=['acquisition.phase','positioner.moving'])
cap('process.measure',obj({}),RESULT,['probe_tip','substrate'],'读取观测双通道电流图/接近曲线；不返回私有反应速率图','query')
cap('kinetics.estimate',KIN,obj({'rate_m_s':arr(nullable(number('m/s',0))),'quality':TEXT,'evidence':TEXT}),['probe_tip','substrate'],'从两个观测电流之比反演工程反馈模型的局部有效反应速率','analysis','agent',KIN_EXAMPLE)
cap('template.read',obj({'substance_id':TEXT}),obj({'substance_id':TEXT,'diffusion_m2_s':number('m^2/s',0),'temperature_k':number('K'),'template_source':TEXT,'evidence':TEXT}),['probe_tip'],'公开水相扩散系数；不保证物种电活性','query',ex={'substance_id':'o2_neutral'})
cap('ledger.read',obj({}),LEDGER,['sample_cell','probe_tip','substrate'],'有限O/R摩尔数与双通道电荷守恒账本','query','evaluator')
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['bipotentiostat'],'当前子集未实现，不返回假结果')
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='bipotentiostat',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','工程过程参数已配置','setpoint');slot('method.configured',BOOL,'1','扫描方法已配置','setpoint');slot('power.enabled',BOOL,'1','逻辑电源目标','setpoint',w=('power.set','enabled'))
for name,lo,hi in [('x',-.025,.025),('y',-.025,.025),('z',0,.05)]:
 slot('stage.'+name+'_m',number('m',lo,hi),'m','组合轴实际位置，+X右/+Y后/+Z远离基底；相对台面内样品零面','scene',c='stage_'+name)
 slot('stage.target_'+name+'_m',number('m',lo,hi),'m','量化后的轴目标；未立即到位','setpoint',c='stage_'+name)
 slot('stage.coarse_'+name+'_m',number('m'),'m','8nm量化粗轴分量','scene',c='stage_'+name)
 slot('stage.fine_'+name+'_m',number('m'),'m','1.6nm细轴组合残差；不是50mm单独压电行程','scene',c='stage_'+name)
slot('positioner.moving',BOOL,'1','轴尚未到目标',c='positioner');slot('positioner.resolution_m',number('m'),'m','配置的组合命令分辨率；不等同定位精度','setpoint',c='positioner')
for n,c in [('tip','probe_tip'),('substrate','substrate')]:
 slot(n+'.target_v',nullable(number('V',-10,10)),'V','相对方法指定参考电极的设定电位','setpoint',c=c)
 slot(n+'.true_current_a',nullable(number('A')),'A','最近推进区间的模型电流真值','process_truth','evaluator',c)
 slot(n+'.current_a',nullable(number('A')),'A','理想量化的最近区间平均电流观测，未标定噪声',c=c)
slot('sample.loaded',BOOL,'1','有限电解液池已装入',c='sample_cell');slot('sample.identity',TEXT,'1','样品身份','process_truth','evaluator','sample_cell');slot('acquisition.phase',PHASE,'1','扫描异步任务状态');slot('acquisition.elapsed_s',number('s',0),'s','包含移动与停留的采集经过时间');slot('acquisition.completed_points',dict(number('1',0),type='integer'),'1','已完成采样点数');slot('acquisition.failure',TEXT,'1','无私有真值的失败代码')
def feedback(L):return L/(L+1.),1.+math.pi/(4.*L)
def logistic(x):return 1/(1+math.exp(-max(-700.,min(700.,x))))
class ChChi920dRuntime:
 def __init__(self,instance_id='secm_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.plant=None;self.method=None;self.sample=None;self.q=[0.,0.,.005];self.target=self.q[:];self.phase='idle';self.elapsed=0.;self.points=[];self.point_index=0;self.dwell=0.;self.dwell_tip=0.;self.dwell_sub=0.;self.oxidized=0.;self.reduced=0.;self.loaded=0.;self.returned=0.;self.tip_charge=0.;self.sub_charge=0.;self.electron_mol=0.;self.currents=[None,None];self.indicated=[None,None];self.failure='';self.sample_window=None;self.serial=0;self.operation_id=None;self.records={};self.templates=json.loads((ROOT/TEMPLATE_REL).read_text())['substances'];self._principals={};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ['agent','evaluator','debug'] or principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return ChChi920dSession(self,principal_id)
 def describe(self):return export_ch_chi920d_contract_metadata()
 def read(self,visibility='agent'):
  m=self.method or {};d={'plant.configured':self.plant is not None,'method.configured':self.method is not None,'power.enabled':self.powered,'sample.loaded':self.sample is not None,'sample.identity':self.sample['sample_id'] if self.sample else '','positioner.moving':math.dist(self.q,self.target)>1e-12,'positioner.resolution_m':1.6e-9 if self.plant and self.plant['fine_enabled'] else 8e-9,'acquisition.phase':self.phase,'acquisition.elapsed_s':self.elapsed,'acquisition.completed_points':len(self.points),'acquisition.failure':self.failure}
  for i,n in enumerate(['x','y','z']):coarse=round(self.q[i]/8e-9)*8e-9;d.update({'stage.'+n+'_m':self.q[i],'stage.target_'+n+'_m':self.target[i],'stage.coarse_'+n+'_m':coarse,'stage.fine_'+n+'_m':self.q[i]-coarse})
  for i,n in enumerate(['tip','substrate']):d.update({n+'.target_v':m.get(n+'_potential_v'),n+'.true_current_a':self.currents[i],n+'.current_a':self.indicated[i]})
  return copy.deepcopy({k:v for k,v in d.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _ledger(self):return dict(oxidized_mol=self.oxidized,reduced_mol=self.reduced,loaded_mol=self.loaded,returned_mol=self.returned,tip_charge_c=self.tip_charge,substrate_charge_c=self.sub_charge,total_residual_mol=self.loaded-self.returned-self.oxidized-self.reduced,charge_residual_c=self.tip_charge+self.sub_charge-F*self.electron_mol,sample_id=self.sample['sample_id'] if self.sample else '')
 def _result(self):return dict(phase=self.phase,complete=self.phase=='succeeded',points=copy.deepcopy(self.points),elapsed_s=self.elapsed,template_source='USGS PHREEQC298.15K diffusivity; identity withheld',evidence='derived public diffusion; synthetic quasi-steady feedback and effective redox response',warnings=['Dimensions and mass unknown; geometry is illustrative','59 diffusion records do not imply59 validated redox mediators','Engineering feedback functions, not a calibrated SECM boundary-value solver','No experimental current/noise/kinetic calibration; external reference electrode required'])
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[k][0],a);before=copy.deepcopy(self.__dict__)
  try:
   result=self._invoke(k,a);validate(CAPS[k][1],result)
   if CAPS[k][4] not in ['query','analysis']:self.revision+=1
   return result
  except Exception:self.__dict__.clear();self.__dict__.update(before);raise
 def _quantize(self,q):
  res=1.6e-9 if self.plant['fine_enabled'] else 8e-9;return [max(lo,min(hi,round(v/res)*res)) for v,lo,hi in zip(q,[-.025,-.025,0.],[.025,.025,.05])]
 def _invoke(self,k,a):
  if k=='process.measure':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='template.read':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   t=self.templates[a['substance_id']];return dict(substance_id=a['substance_id'],diffusion_m2_s=t['diffusion_m2_s'],temperature_k=298.15,template_source=t['source']['url']+' line '+str(t['source']['line']),evidence='derived_database; no electroactivity claim')
  if k=='kinetics.estimate':
   result=[]
   for p in self.points:
    L=(p['z_m']-a['substrate_plane_z_m'])/a['tip_radius_m'];it=p['tip_current_a'];isub=p['substrate_current_a'];val=None
    if L>0 and it>0 and 0<=-isub/it<1:
     negative,positive=feedback(L);r=-isub/it;rho=r*negative/((1-r)*(positive-negative))
     if 0<=rho<a['substrate_activation']:val=a['diffusion_m2_s']/a['tip_radius_m']*rho/(a['substrate_activation']-rho)
    result.append(val)
   return dict(rate_m_s=result,quality='estimated_surrogate' if result and all(v is not None for v in result) else 'unidentifiable',evidence='only recorded dual-current ratio, supplied geometry/D/activation and engineering inverse; no private k-map reads')
  if k=='plant.configure':
   if self.plant or self.sample or self.powered or not a['accept_feedback_surrogate']:raise ActionRejected('INITIALIZATION_ONLY')
   self.plant=copy.deepcopy(a);return {'configured':True}
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self.phase='cancelled';self.target=self.q[:];self.records[self.operation_id]=self._result();return {'phase':self.phase}
  if k=='power.set':
   self.powered=a['enabled']
   if not self.powered:
    self.target=self.q[:]
    if self.phase=='running':self.phase='cancelled';self.records[self.operation_id]=self._result()
   return a
  if self.phase=='running':raise ActionRejected('BUSY')
  if k=='method.configure':
   if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
   xyz=[[p[n+'_m'] for n in ['x','y','z']] for p in a['path']]
   if a['mode']=='constant_height' and max(q[2] for q in xyz)-min(q[2] for q in xyz)>1e-12:raise ActionRejected('PATH_MODE')
   if a['mode']=='approach' and (any(q[:2]!=xyz[0][:2] for q in xyz) or any(b[2]>c[2] for c,b in zip(xyz,xyz[1:]))):raise ActionRejected('PATH_MODE')
   self.method=copy.deepcopy(a);self.method['path']=[dict(zip(['x_m','y_m','z_m'],self._quantize(q))) for q in xyz];return {'configured':True}
  if k=='stage.move':
   if not self.plant or not self.powered:raise ActionRejected('NOT_READY')
   q=self._quantize([a[n+'_m'] for n in ['x','y','z']])
   if self.sample and q[2]-self.sample['surface_z_m']<max(1e-9,.1*self.plant['tip_radius_m']):raise ActionRejected('COLLISION_GUARD')
   self.target=q;return dict(zip(['x_m','y_m','z_m'],q))
  if k=='sample.load':
   if not self.plant or self.sample or self.q[2]-a['surface_z_m']<.001 or not a['accept_effective_redox']:raise ActionRejected('SAMPLE_INVALID')
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   self.sample=copy.deepcopy(a);n=a['volume_m3']*a['concentration_mol_m3'];self.oxidized=n*a['oxidized_fraction'];self.reduced=n-self.oxidized;self.loaded+=n;return {'loaded':True}
  if k=='sample.unload':
   if not self.sample or self.q[2]-self.sample['surface_z_m']<.001:raise ActionRejected('RETRACT_REQUIRED')
   n=self.oxidized+self.reduced;self.returned+=n;self.oxidized=0.;self.reduced=0.;self.sample=None;return {'returned_mol':n}
  if k=='acquisition.start':
   if not self.plant or not self.method or not self.sample or not self.powered or math.dist(self.q,self.target)>1e-12:raise ActionRejected('NOT_READY')
   D=self.templates[self.sample['substance_id']]['diffusion_m2_s']
   if self.method['dwell_s']<5*self.plant['tip_radius_m']**2/D:raise ActionRejected('STEADY_STATE_DWELL')
   if any(p['z_m']-self.sample['surface_z_m']<max(self.method['min_gap_m'],.1*self.plant['tip_radius_m']) or math.hypot(p['x_m'],p['y_m'])>self.sample['footprint_radius_m'] for p in self.method['path']):raise ActionRejected('COLLISION_GUARD')
   self.serial+=1;self.operation_id=f'{self.instance_id}:op:{self.serial}';self.phase='running';self.elapsed=0.;self.points=[];self.point_index=0;self.dwell=0.;self.dwell_tip=0.;self.dwell_sub=0.;self.failure='';self.target=[self.method['path'][0][n+'_m'] for n in ['x','y','z']];return dict(phase=self.phase,operation_id=self.operation_id)
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _reaction(self,dt):
  p=self.plant;s=self.sample;m=self.method;D=self.templates[s['substance_id']]['diffusion_m2_s'];radius=p['tip_radius_m'];n=s['electrons'];L=(self.q[2]-s['surface_z_m'])/radius;neg,pos=feedback(L);k=s['base_rate_m_s']+s['patch_rate_m_s']*math.exp(-((self.q[0]-s['patch_x_m'])**2+(self.q[1]-s['patch_y_m'])**2)/(2*s['patch_sigma_m']**2));activation=logistic(n*F*(m['substrate_potential_v']-s['formal_potential_v'])/(R*T));rho=k*radius/D/(1+k*radius/D)*activation;direct=4*D*radius*neg*logistic(n*F*(s['formal_potential_v']-m['tip_potential_v'])/(R*T))/s['volume_m3'];ratio=rho*(pos-neg)/neg;initial=direct*self.oxidized*n*F
  if initial*(1+2*ratio)>.25 or initial*(1+ratio)>p['current_range_a']:
   self.failure='CURRENT_LIMIT';self.phase='failed';self.target=self.q[:];return
  converted=self.oxidized*(-math.expm1(-direct*dt));self.oxidized-=converted;self.reduced+=converted;cycle=converted*ratio;qt=n*F*(converted+cycle);qs=-n*F*cycle;self.tip_charge+=qt;self.sub_charge+=qs;self.electron_mol+=n*converted;self.dwell_tip+=qt;self.dwell_sub+=qs;self.currents=[qt/dt,qs/dt];quant=max(.3e-15,p['current_range_a']*.000015);self.indicated=[round(i/quant)*quant for i in self.currents]
 def step(self,dt_s):
  validate(number('s',0,10000),dt_s)
  if dt_s==0:return self.read()
  dt=float(dt_s);self.time_s+=dt;self.revision+=1;remain=dt
  while remain>1e-12 and self.powered and self.plant:
   distance=math.dist(self.q,self.target)
   if distance>1e-12:
    used=min(remain,distance/self.plant['travel_speed_m_s']);fraction=min(1.,used*self.plant['travel_speed_m_s']/distance);self.q=self.target[:] if fraction>=1-1e-12 else [a+(b-a)*fraction for a,b in zip(self.q,self.target)];remain-=used
    if self.phase=='running':self.elapsed+=used
    if used<=0:break
   elif self.phase=='running':
    used=min(remain,max(0.,self.method['dwell_s']-self.dwell))
    if used>0:
     self._reaction(used)
     if self.phase!='failed':self.sample_window=[self.time_s-remain,self.time_s-remain+used]
     remain-=used;self.elapsed+=used;self.dwell+=used
    if self.phase=='failed':self.records[self.operation_id]=self._result();break
    if self.dwell>=self.method['dwell_s']-1e-10:
     quant=max(.3e-15,self.plant['current_range_a']*.000015);self.points.append(dict(time_s=self.elapsed,window_s=self.dwell,x_m=self.q[0],y_m=self.q[1],z_m=self.q[2],tip_current_a=round((self.dwell_tip/self.dwell)/quant)*quant,substrate_current_a=round((self.dwell_sub/self.dwell)/quant)*quant));self.point_index+=1;self.dwell=0.;self.dwell_tip=0.;self.dwell_sub=0.
     if self.point_index==len(self.method['path']):self.phase='succeeded';self.records[self.operation_id]=self._result();break
     self.target=[self.method['path'][self.point_index][n+'_m'] for n in ['x','y','z']]
   else:break
  return self.read()
class ChChi920dSession:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,i):
  if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  m=export_ch_chi920d_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
 def read(self,*,instance_id,state_ids=None):
  self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
  if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
  if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
  current=lambda k:k in ['tip.current_a','substrate.current_a','tip.true_current_a','substrate.true_current_a']
  return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='unavailable' if v[k] is None else 'stale' if current(k) and h.phase!='running' else 'estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=h.sample_window[-1] if current(k) and h.sample_window else h.time_s,sample_window_s=copy.deepcopy(h.sample_window) if current(k) else None) for k in ids})
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




def export_ch_chi920d_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():
  ref='相对method.reference_electrode；不是默认SHE' if u=='V' else None
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=ref,origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'ChChi920dSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  unsupported=k in UNSUPPORTED;asyn=k=='acquisition.start'
  caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.ch_chi920d.ChChi920dSession.invoke',invocation=f'session.invoke(instance_id="secm_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if kind in ['query','analysis'] or unsupported else [dict(description_zh='类型/身份、双通道电压限值、路径/最低间隙/稳态停留与忙碌检查；非原厂安全互锁',state_ids=['power.enabled','method.configured','sample.loaded','positioner.moving','acquisition.phase'],enforced_by='runtime',check='atomic Runtime.invoke plus trusted-principal Session')],state_effects=[dict(state_id=x,timing='immediate',description_zh='受检事务目标/逻辑状态变化') for x in effects],observes=[],model_effects=dict(mode='none',description_zh='等待独立实际场景求值；数值过程不自动证明模型联动',binding_ids=[]),execution=dict(mode='asynchronous' if asyn else 'synchronous',time_domain='simulation',completion_criterion='路径所有停留点采集完成；部分电荷和O/R反应保留' if asyn else '受检事务返回；read/describe/poll不推进时钟',completion_state_ids=['acquisition.phase'] if asyn else [],timeout_s=None,cancellation='supported' if asyn else 'unsupported',retry='same_request_id',resources=['host_clock','finite_redox_cell','positioner','request_cache'],partial_failure_zh='已移动位置/已反应物质/电荷保留，取消不回滚；仅进程内请求去重'),errors=[dict(code=x,meaning_zh=x,side_effects='none') for x in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNKNOWN_OPERATION','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','PRECONDITION_FAILED','INITIALIZATION_ONLY','BUSY','UNKNOWN_REFERENCE','NOT_READY','NOT_RUNNING','PATH_MODE','SAMPLE_INVALID','COLLISION_GUARD','RETRACT_REQUIRED','STEADY_STATE_DWELL']],example_arguments=copy.deepcopy(ex),verification=[],limitations=['教学过程；电极化学、反馈曲线、精度/噪声和真实动力学未校准'],source_ids=['manufacturer_primary','simulation_assumption'])
  if asyn:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='唯一由宿主step推进') for x in ['acquisition.elapsed_s','tip.current_a','substrate.current_a','stage.x_m','stage.y_m','stage.z_m','acquisition.phase']]
 mech=dict(component_ids=['probe_tip','substrate','sample_cell','positioner','bipotentiostat'],state_ids=list(slots),meaning_zh='有限有效O/R介体与准稳态SECM反馈工程代理；电荷/物质守恒，图像从观测电流生成',implementation='lumped',equations=['Far-field microdisk transport current i_inf=4*n*F*D*c*a; D from public USGS data at298.15K','Engineering feedback: f_neg=L/(L+1); f_pos=1+pi/(4L), L=gap/a; not exact SECM boundary-value theory','rho=(k*a/D)/(1+k*a/D)*sigmoid(nF(E_sub-E0)/(RT)); direct reduction activation=sigmoid(nF(E0-E_tip)/(RT))','dN=O*(1-exp(-4*D*a*f_neg*activation*dt/V)); Onew=O-dN; Rnew=R+dN','Ncycle=dN*rho*(f_pos-f_neg)/f_neg; Qtip=nF(dN+Ncycle), Qsub=-nF*Ncycle; total charge=nF*dN','Current true values integrate finite reaction, then quantize at max(0.3fA,range*0.0015%); ideal ADC, not calibrated accuracy','If abs(Itip)+abs(Isub)>250mA or tip exceeds configured range: fail before reaction; no350mA peak capability claimed','k inversion uses observed r=-Isub/Itip, rho=r*fneg/((1-r)*(fpos-fneg)), k=(D/a)*rho/(activation-rho)','50mm combined axes;8nm coarse and1.6nm residual command representation, explicit finite travel speed; no implicit query time advance'],parameters={},source_ids=['manufacturer_primary','usgs_diffusion','simulation_assumption'],limitations=['59 real diffusion parameters, no measured SECM curve library; not59 electroactive mediators.','n/formal potential/current-reference and spatial k-map explicitly supplied as effective assumptions.','Only298.15K ideal-dilute chemistry; no migration/convection/adsorption/double layer/noise/full3D diffusion.','Coarse/fine command granularity not a claim of measured positional accuracy or a50mm piezo stroke.','Two-current sign convention reduction tip positive, regeneration substrate negative; not inferred hardware cable sign.','Detector values quantized; kinetics inverse can be unidentifiable at rounded-zero current or physical bounds.','Geometry envelope and mass unknown; all enclosure dimensions are illustrative engineering design.','Robot, hardware, constant-current PID, impedance and full electrochemical-suite modes unsupported/not_run.'])
 spec=importlib.util.spec_from_file_location('_secm_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'secm_feedback':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'。'
 for key in ['tip.current_a','substrate.current_a','tip.true_current_a','substrate.true_current_a']:slots[key]['sampling']=dict(mode='interval_mean',time_domain='simulation',window_description='最近真正反应推进区间；Session保留绝对采样时间窗，停止后stale')
 caps['stage.move']['state_effects'] += [dict(state_id='stage.'+n+'_m',timing='on_step',description_zh='step按有限速度推进实际三轴') for n in ['x','y','z']]
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'secm_feedback':mech})

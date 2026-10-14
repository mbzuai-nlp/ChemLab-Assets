"""CHEM-105 finite EQCM educational process; trusted host API, no experimental calibration."""
from pathlib import Path
import copy,math,json,importlib.util
PROTOCOL_VERSION='1.0.0';SLUG='ch_chi440c';CATALOG_ID='CHEM-105';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];TEMPLATE_REL='assets/expansion_batch04/t4_measure/'+SLUG+'/qcm_material_library.json'
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
CRYSTAL=obj({'fundamental_hz':number('Hz',1e6,2e7),'area_m2':number('m^2',1e-7,1e-3),'quartz_density_kg_m3':number('kg/m^3',2000,3000),'shear_modulus_pa':number('Pa',1e10,1e11),'frequency_quantum_hz':number('Hz',.001,.099),'max_relative_shift':number('1',1e-6,.01),'accept_rigid_thinfilm':BOOL})
CRYSTAL_EXAMPLE=dict(fundamental_hz=7995000.,area_m2=1.96e-5,quartz_density_kg_m3=2684.,shear_modulus_pa=2.947e10,frequency_quantum_hz=.05,max_relative_shift=.01,accept_rigid_thinfilm=True)
SEGMENT=obj({'duration_s':number('s',.001,1000),'potential_v':number('V',-10,10),'current_a':number('A',-.25,.25)})
METHOD=obj({'mode':enum(['potential','current','qcm_only']),'steps':dict(arr(SEGMENT),minItems=1,maxItems=320),'sample_interval_s':number('s',.001,10),'reference_electrode':TEXT})
METHOD_EXAMPLE=dict(mode='current',steps=[dict(duration_s=2.,potential_v=0.,current_a=1e-5),dict(duration_s=1.,potential_v=0.,current_a=-1e-5)],sample_interval_s=.1,reference_electrode='explicit synthetic E0 reference')
SAMPLE=obj({'sample_id':TEXT,'substance_id':TEXT,'process':enum(['electrodeposition','adsorption']),'volume_m3':number('m^3',1e-9,1e-5),'concentration_mol_m3':number('mol/m^3',1e-9,1e4),'formal_potential_v':number('V',-10,10),'exchange_current_a':number('A',1e-12,.1),'series_resistance_ohm':number('ohm',0,1e9),'site_capacity_mol':number('mol',1e-15,1e-3),'adsorption_on_m3_mol_s':number('m^3/(mol*s)',0,1e6),'desorption_off_s':number('1/s',0,1e6),'accept_kinetics_surrogate':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='copper_bath',substance_id='copper',process='electrodeposition',volume_m3=1e-6,concentration_mol_m3=1.,formal_potential_v=0.,exchange_current_a=1e-5,series_resistance_ohm=10.,site_capacity_mol=1e-8,adsorption_on_m3_mol_s=0.,desorption_off_s=0.,accept_kinetics_surrogate=True)
ADSORPTION_EXAMPLE=dict(SAMPLE_EXAMPLE,sample_id='ethanol_adsorption_surrogate',substance_id='ethanol',process='adsorption',site_capacity_mol=1e-9,adsorption_on_m3_mol_s=.5,desorption_off_s=.1)
POINT=obj({'time_s':number('s',0),'window_s':number('s',0),'potential_v':number('V',-10,10),'current_a':number('A',-.25,.25),'frequency_hz':nullable(number('Hz',0)),'delta_frequency_hz':nullable(number('Hz')),'inferred_delta_mass_kg':nullable(number('kg'))})
RESULT=obj({'phase':PHASE,'complete':BOOL,'elapsed_s':number('s',0),'points':arr(POINT),'template_source':TEXT,'evidence':TEXT,'warnings':arr(TEXT)})
LEDGER=obj({'loaded_mass_kg':number('kg',0),'solution_mass_kg':number('kg',0),'film_mass_kg':number('kg',0),'returned_mass_kg':number('kg',0),'waste_mass_kg':number('kg',0),'mass_residual_kg':number('kg'),'charge_c':number('C'),'electron_mol':number('mol'),'charge_residual_c':number('C'),'sample_id':TEXT})
CAPS={};UNSUPPORTED={'voltammetry.full_suite','qcmd.acquire','resistance.acquire','hardware.connect'}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},effects or [])
cap('crystal.configure',CRYSTAL,obj({'configured':BOOL,'sensitivity_hz_kg':number('Hz/kg',0)}),['quartz_crystal','oscillator'],'显式晶体与薄膜适用性参数；初始化时设置','configuration','evaluator',CRYSTAL_EXAMPLE,['crystal.configured'])
cap('method.configure',METHOD,obj({'configured':BOOL}),['controller'],'电位/电流台阶或仅QCM；采样≥1ms','configuration','agent',METHOD_EXAMPLE,['method.configured'])
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_cell','gold_electrode'],'宿主装入有限浴液和显式动力学参数','configuration','evaluator',SAMPLE_EXAMPLE,['sample.loaded','sample.identity'])
cap('sample.unload',obj({}),obj({'returned_mass_kg':number('kg',0)}),['sample_cell'],'开盖后归还剩余浴液；晶体膜保留','device_command','evaluator',effects=['sample.loaded','sample.identity'])
cap('crystal.clean',obj({}),obj({'waste_mass_kg':number('kg',0)}),['quartz_crystal','gold_electrode'],'开盖且排液后将膜转入废物账本；不凭空抹除质量','device_command','evaluator')
cap('lid.set',obj({'lift_m':number('m',0,.05)}),obj({'lift_m':number('m',0,.05)}),['cell_lid'],'人工释放螺钉后的盖抬升姿态；非电动开盖',ex={'lift_m':.05},effects=['lid.lift_m'])
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['controller','oscillator'],'逻辑电源与停止；不模拟高频晶体变形',ex={'enabled':True},effects=['power.enabled','acquisition.phase'])
cap('baseline.zero',obj({}),obj({'baseline_hz':number('Hz',0)}),['quartz_crystal','oscillator'],'仅捕获已有频率观测作为零基线，不改变膜质量',effects=['frequency.baseline_hz'])
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['controller','oscillator','gold_electrode'],'异步有限沉积/溶解/吸附与同步频率电流采集',effects=['acquisition.phase','acquisition.elapsed_s'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['controller'],'停止；保留材料、电荷和已采数据',effects=['acquisition.phase'])
cap('process.measure',obj({}),RESULT,['controller','oscillator'],'读取电流、电位、频移及由观测频移推断质量','query')
cap('template.read',obj({'substance_id':TEXT}),obj({'substance_id':TEXT,'molar_mass_kg_mol':number('kg/mol',0),'template_source':TEXT,'evidence':TEXT}),['gold_electrode'],'查询公开摩尔质量；无真实QCM响应曲线','query',ex={'substance_id':'copper'})
cap('ledger.read',obj({}),LEDGER,['sample_cell','gold_electrode'],'宿主验证有限物质与电荷守恒','query','evaluator')
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'当前子集未实现')
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='controller',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
for k in ['crystal','method']:slot(k+'.configured',BOOL,'1',k+'参数已配置','setpoint')
slot('power.enabled',BOOL,'1','逻辑电源','setpoint',w=('power.set','enabled'));slot('lid.lift_m',number('m',0,.05),'m','盖实际抬升；人工拆装抽象','scene',c='cell_lid',w=('lid.set','lift_m'))
slot('sample.loaded',BOOL,'1','有限浴液已装入');slot('sample.identity',TEXT,'1','样品身份','process_truth','evaluator','sample_cell');slot('film.true_mass_kg',number('kg',0),'kg','有限薄膜真实质量','process_truth','evaluator','gold_electrode')
for k,u,z in [('frequency.true_hz','Hz','过程真频率'),('frequency.hz','Hz','理想计数量化后的频率'),('frequency.delta_hz','Hz','相对观测基线频移'),('mass.inferred_delta_kg','kg','从观测频移换算的质量变化'),('current.a','A','区间平均电流'),('potential.v','V','相对明确参比的区间电位')]:slot(k,nullable(number(u)),u,z,'process_truth' if k=='frequency.true_hz' else 'derived','evaluator' if k=='frequency.true_hz' else 'agent',c='oscillator' if k.startswith('frequency') else 'controller')
slot('frequency.baseline_hz',nullable(number('Hz',0)),'Hz','观测零点，不是过程真质量归零','setpoint',c='oscillator');slot('frequency.quality',enum(['unavailable','estimated','invalid']),'1','薄膜条件与理想频率质量状态',c='oscillator');slot('acquisition.phase',PHASE,'1','采集任务状态');slot('acquisition.elapsed_s',number('s',0),'s','已处理采样时长');slot('acquisition.failure',TEXT,'1','无私有身份的失败码');slot('acquisition.completed_points',dict(number('1',0),type='integer'),'1','有效记录数')
def adsorb_exact(x,total,sites,ka,kd,volume,dt):
 a=ka/volume
 if a==0:return x*math.exp(-kd*dt)
 b=a*(total+sites)+kd;c=a*total*sites;d=math.sqrt(max(0.,b*b-4*a*c))
 if d<=1e-12*max(b,1e-300):r=b/(2*a);return r-(r-x)/(1+a*(r-x)*dt)
 r1=2*c/(b+d);r2=(b+d)/(2*a);w=(x-r1)/(x-r2)*math.exp(-d*dt);return max(0.,min(total,sites,(r1-w*r2)/(1-w)))
class ChChi440cRuntime:
 def __init__(self,instance_id='eqcm_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.crystal=None;self.method=None;self.sample=None;self.lift=0.;self.phase='idle';self.elapsed=0.;self.points=[];self.solution=0.;self.film=0.;self.mw=0.;self.film_species='';self.loaded_mass=0.;self.returned_mass=0.;self.waste_mass=0.;self.charge=0.;self.electron_mol=0.;self.frequency=None;self.true_frequency=None;self.baseline=None;self.current=None;self.potential=None;self.quality='unavailable';self.failure='';self.sample_window=None;self.serial=0;self.operation_id=None;self.records={};self.pending=0.;self.ticks=0;self._principals={};self._requests={};self._owners={};self.templates=json.loads((ROOT/TEMPLATE_REL).read_text())['substances']
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ['agent','evaluator','debug'] or principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return ChChi440cSession(self,principal_id)
 def describe(self):return export_ch_chi440c_contract_metadata()
 def sensitivity(self):
  c=self.crystal;return 2*c['fundamental_hz']**2/(c['area_m2']*math.sqrt(c['quartz_density_kg_m3']*c['shear_modulus_pa']))
 def delta(self):return self.frequency-self.baseline if self.frequency is not None and self.baseline is not None else None
 def inferred(self):return -self.delta()/self.sensitivity() if self.delta() is not None else None
 def read(self,visibility='agent'):
  d={'crystal.configured':self.crystal is not None,'method.configured':self.method is not None,'power.enabled':self.powered,'lid.lift_m':self.lift,'sample.loaded':self.sample is not None,'sample.identity':self.sample['sample_id'] if self.sample else '','film.true_mass_kg':self.film*self.mw,'frequency.true_hz':self.true_frequency,'frequency.hz':self.frequency,'frequency.delta_hz':self.delta(),'mass.inferred_delta_kg':self.inferred(),'current.a':self.current,'potential.v':self.potential,'frequency.baseline_hz':self.baseline,'frequency.quality':self.quality,'acquisition.phase':self.phase,'acquisition.elapsed_s':self.elapsed,'acquisition.failure':self.failure,'acquisition.completed_points':len(self.points)}
  return copy.deepcopy({k:v for k,v in d.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _ledger(self):return dict(loaded_mass_kg=self.loaded_mass,solution_mass_kg=self.solution*self.mw,film_mass_kg=self.film*self.mw,returned_mass_kg=self.returned_mass,waste_mass_kg=self.waste_mass,mass_residual_kg=self.loaded_mass-self.returned_mass-self.waste_mass-(self.solution+self.film)*self.mw,charge_c=self.charge,electron_mol=self.electron_mol,charge_residual_c=self.charge-F*self.electron_mol,sample_id=self.sample['sample_id'] if self.sample else '')
 def _result(self):return dict(phase=self.phase,complete=self.phase=='succeeded',elapsed_s=self.elapsed,points=copy.deepcopy(self.points),template_source='PubChem or NIST molar mass; sample identity withheld',evidence='public molar mass + documented Sauerbrey + synthetic finite kinetics; not measured QCM response',warnings=['Rigid thinfilm only; no liquid/viscoelastic dissipation correction','Frequency quantization is ideal; noise and calibration not measured','BV parameters/reference and adsorption affinity explicitly supplied assumptions','QCM sample limit1kHz; no claim of1MHz QCM acquisition'])
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[k][0],a);before=copy.deepcopy(self.__dict__)
  try:
   r=self._invoke(k,a);validate(CAPS[k][1],r)
   if CAPS[k][4] not in ['query','analysis']:self.revision+=1
   return r
  except Exception:self.__dict__.clear();self.__dict__.update(before);raise
 def _observe(self):
  self.true_frequency=self.crystal['fundamental_hz']-self.sensitivity()*self.film*self.mw
  if abs(self.true_frequency-self.crystal['fundamental_hz'])>self.crystal['max_relative_shift']*self.crystal['fundamental_hz']:self.frequency=None;self.quality='invalid';return False
  q=self.crystal['frequency_quantum_hz'];self.frequency=round(self.true_frequency/q)*q;self.quality='estimated';return True
 def _invoke(self,k,a):
  if k=='process.measure':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='template.read':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   t=self.templates[a['substance_id']];return dict(substance_id=a['substance_id'],molar_mass_kg_mol=t['molar_mass_kg_mol'],template_source=t['source']['url'],evidence=t['evidence'])
  if k=='crystal.configure':
   if self.crystal or self.powered or self.sample or not a['accept_rigid_thinfilm']:raise ActionRejected('INITIALIZATION_ONLY')
   self.crystal=copy.deepcopy(a);return dict(configured=True,sensitivity_hz_kg=self.sensitivity())
  if k=='power.set':
   self.powered=a['enabled']
   if not self.powered and self.phase=='running':self.phase='cancelled';self.records[self.operation_id]=self._result()
   return a
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self.phase='cancelled';self.records[self.operation_id]=self._result();return {'phase':self.phase}
  if self.phase=='running':raise ActionRejected('BUSY')
  if k=='lid.set':self.lift=a['lift_m'];return a
  if k=='method.configure':
   n=0
   for s in a['steps']:
    if (a['mode']!='potential' and s['potential_v']!=0.) or (a['mode']!='current' and s['current_a']!=0.):raise ActionRejected('UNUSED_CONTROL_NONZERO')
    ticks=s['duration_s']/a['sample_interval_s']
    if abs(ticks-round(ticks))>1e-8 or round(ticks)<1:raise ActionRejected('SAMPLE_GRID')
    n+=round(ticks)
   if n>20000:raise ActionRejected('POINT_LIMIT')
   self.method=copy.deepcopy(a);return {'configured':True}
  if k=='sample.load':
   if not self.crystal or self.sample or self.film>0 or self.lift<.03 or not a['accept_kinetics_surrogate']:raise ActionRejected('LOAD_PRECONDITION')
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   if a['process']=='electrodeposition' and a['substance_id']!='copper':raise ActionRejected('CHEMISTRY_UNSUPPORTED')
   self.sample=copy.deepcopy(a);self.solution=a['volume_m3']*a['concentration_mol_m3'];self.mw=self.templates[a['substance_id']]['molar_mass_kg_mol'];self.film_species=a['substance_id'];self.loaded_mass+=self.solution*self.mw;return {'loaded':True}
  if k=='sample.unload':
   if not self.sample or self.lift<.03:raise ActionRejected('LID_OPEN_REQUIRED')
   m=self.solution*self.mw;self.returned_mass+=m;self.solution=0.;self.sample=None;return {'returned_mass_kg':m}
  if k=='crystal.clean':
   if self.sample or self.lift<.03:raise ActionRejected('DRAIN_OPEN_REQUIRED')
   m=self.film*self.mw;self.waste_mass+=m;self.film=0.;self.mw=0.;self.film_species='';return {'waste_mass_kg':m}
  if k=='baseline.zero':
   if self.frequency is None or self.quality!='estimated' or not self.powered:raise ActionRejected('NO_VALID_OBSERVATION')
   self.baseline=self.frequency;return {'baseline_hz':self.baseline}
  if k=='acquisition.start':
   if not self.crystal or not self.method or not self.sample or not self.powered or self.lift>1e-12:raise ActionRejected('NOT_READY')
   if (self.sample['process']=='adsorption')!=(self.method['mode']=='qcm_only'):raise ActionRejected('METHOD_PROCESS_MISMATCH')
   self._observe()
   if self.quality=='invalid':raise ActionRejected('THINFILM_DOMAIN')
   if self.baseline is None:self.baseline=self.frequency
   self.serial+=1;self.operation_id=f'{self.instance_id}:op:{self.serial}';self.phase='running';self.elapsed=0.;self.points=[];self.pending=0.;self.ticks=0;self.start_time=self.time_s;self.failure='';return dict(phase=self.phase,operation_id=self.operation_id)
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _tick(self,dt):
  s=self.sample;m=self.method;offset=self.ticks;seg=None
  for v in m['steps']:
   count=round(v['duration_s']/dt)
   if offset<count:seg=v;break
   offset-=count
  if seg is None:self.phase='succeeded';return
  potential=0.;current=0.
  if s['process']=='adsorption':
   total=self.solution+self.film;new=adsorb_exact(self.film,total,s['site_capacity_mol'],s['adsorption_on_m3_mol_s'],s['desorption_off_s'],s['volume_m3'],dt);self.solution=total-new;self.film=new
  else:
   equilibrium=s['formal_potential_v']+R*T/(2*F)*math.log(max(1e-30,self.solution/s['volume_m3']))
   def voltage(i):return equilibrium-R*T/F*math.asinh(i/(2*s['exchange_current_a']))-i*s['series_resistance_ohm']
   if m['mode']=='current':
    current=seg['current_a'];potential=voltage(current)
    if abs(potential)>10.:self.failure='POTENTIAL_LIMIT';self.phase='failed';return
   else:
    potential=seg['potential_v']
    if not voltage(.25)<=potential<=voltage(-.25):self.failure='CURRENT_LIMIT';self.phase='failed';return
    lo=-.25;hi=.25
    for _ in range(80):
     mid=(lo+hi)/2
     if voltage(mid)>potential:lo=mid
     else:hi=mid
    current=(lo+hi)/2
   dn=current*dt/(2*F);actual=max(-self.film,min(self.solution,dn));self.solution-=actual;self.film+=actual;self.charge+=2*F*actual;self.electron_mol+=2*actual;current=2*F*actual/dt
   if abs(actual-dn)>1e-24:self.failure='FINITE_MATERIAL_EXHAUSTED';self.phase='failed'
  self.ticks+=1;self.elapsed=self.ticks*dt;self.current=current;self.potential=potential;self.sample_window=[self.start_time+self.elapsed-dt,self.start_time+self.elapsed]
  if not self._observe():self.failure='THINFILM_DOMAIN';self.phase='failed'
  self.points.append(dict(time_s=self.elapsed,window_s=dt,potential_v=potential,current_a=current,frequency_hz=self.frequency,delta_frequency_hz=self.delta(),inferred_delta_mass_kg=self.inferred()))
  if self.phase=='running' and self.ticks>=sum(round(v['duration_s']/dt) for v in m['steps']):self.phase='succeeded'
 def step(self,dt):
  validate(number('s',0,86400),dt)
  if dt==0:return
  if self.phase=='running':
   self.pending+=dt;interval=self.method['sample_interval_s']
   while self.pending+1e-12>=interval and self.phase=='running':self._tick(interval);self.pending=max(0.,self.pending-interval)
   if self.phase!='running':self.records[self.operation_id]=self._result()
  elif self.powered and self.crystal and self.phase=='idle':
   self._observe();self.sample_window=[self.time_s,self.time_s+dt]
   if self.baseline is None:self.baseline=self.frequency
  self.time_s+=dt;self.revision+=1

class ChChi440cSession:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,i):
  if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  m=export_ch_chi440c_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
 def read(self,*,instance_id,state_ids=None):
  self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
  if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
  if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
  current=lambda k:k in ['frequency.hz','frequency.true_hz','frequency.delta_hz','mass.inferred_delta_kg','current.a','potential.v']
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




def export_ch_chi440c_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions='相对方法显式参比；E0由宿主给定' if u=='V' else None,origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'ChChi440cSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNKNOWN_OPERATION','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','INITIALIZATION_ONLY','BUSY','UNKNOWN_REFERENCE','NOT_READY','NOT_RUNNING','UNUSED_CONTROL_NONZERO','SAMPLE_GRID','POINT_LIMIT','LOAD_PRECONDITION','CHEMISTRY_UNSUPPORTED','LID_OPEN_REQUIRED','DRAIN_OPEN_REQUIRED','NO_VALID_OBSERVATION','THINFILM_DOMAIN','METHOD_PROCESS_MISMATCH']
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  unsupported=k in UNSUPPORTED;asyn=k=='acquisition.start';caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.ch_chi440c.ChChi440cSession.invoke',invocation=f'session.invoke(instance_id="eqcm_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if kind=='query' or unsupported else [dict(description_zh='类型/身份、晶体薄膜条件、QCM采样≤1kHz、±10V/250mA及盖/有限浴液检查；非原厂互锁',state_ids=['power.enabled','method.configured','sample.loaded','lid.lift_m','acquisition.phase'],enforced_by='runtime',check='atomic Runtime.invoke and trusted Session')],state_effects=[dict(state_id=x,timing='immediate',description_zh='受检事务改变逻辑状态') for x in effects],observes=[],model_effects=dict(mode='none',description_zh='等待独立场景验证',binding_ids=[]),execution=dict(mode='asynchronous' if asyn else 'synchronous',time_domain='simulation',completion_criterion='固定采样格上的全部程序台阶完成' if asyn else '受检事务返回；查询不推进时钟',completion_state_ids=['acquisition.phase'] if asyn else [],timeout_s=None,cancellation='supported' if asyn else 'unsupported',retry='same_request_id',resources=['host_clock','finite_bath','quartz_film','request_cache'],partial_failure_zh='保留已发生质量转移、电荷和部分曲线；请求去重只在当前宿主内'),errors=[dict(code=x,meaning_zh=x,side_effects='none') for x in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=['教学过程，真实电化学响应、噪声、温漂及频率校准未验证'],source_ids=['manufacturer_primary','simulation_assumption'])
  if asyn:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='仅step在采样格推进有限过程和观测') for x in ['frequency.hz','frequency.delta_hz','mass.inferred_delta_kg','current.a','potential.v','acquisition.elapsed_s','acquisition.phase']]
 mech=dict(component_ids=['controller','oscillator','quartz_crystal','gold_electrode','sample_cell'],state_ids=list(slots),meaning_zh='有限单组分EQCM过程；由观测频移推断质量；真实物性与工程动力学分开',implementation='lumped',equations=['S=2*f0^2/(A*sqrt(rho*mu)); f=f0-S*mfilm; m_inferred=-(f_observed-f_baseline)/S','CuII/Cu only: dN=I*dt/(2F), clipped by finite dissolved/film amounts; Q=2F*dN','Eeq=E0+RT/(2F)*ln(c/1mol m^-3); E=Eeq-RT/F*asinh(I/(2i0))-I*Rseries; quasi-static symmetric Butler-Volmer surrogate','Adsorption: dx/dt=kon*(Ntotal-x)*(Nsites-x)/V-koff*x; analytic finite-bath Riccati integration','Fixed QCM ticks>=1ms; every segment aligns to sample grid; ≤20000 points implementation limit','Frequency ideal quantization0.05Hz(default); sensor quality invalid outside explicit thinfilm relative-shift bound','Mass ledger: loaded=solution+film+returned+waste; charge ledger Q=F*electron_mol; cleaning transfersfilm to waste'],parameters={},source_ids=['manufacturer_primary','pubchem','copper_nist','simulation_assumption'],limitations=['96 true molar masses, zero measured QCM response arrays;50 experimental curves unmet.','Mass inference is only from observed frequency and supplied crystal sensitivity, no hidden true-film mass reads.','Cu n2 chemistry and reference/i0/resistance supplied; no diffusion layer/double-layer/passivation/side reactions.','Adsorption affinity/site capacity are explicit hypotheses, not validated for all materials.','No viscoelastic/liquid correction, QCM-D or motional resistance, temperature/noise/drift calibration.','Continuous electrical limits±10V,±250mA;350mA peak, general CV and pulse suites unsupported.','Measured thickness/film distribution/crystal stress, screw/contact physics, robot and hardware not_run.'])
 spec=importlib.util.spec_from_file_location('_qcm_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'eqcm_mass':mech},inputs={'crystal.configure':(CRYSTAL,CRYSTAL_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'。'
 for key in ['current.a','potential.v']:slots[key]['sampling']=dict(mode='interval_mean',time_domain='simulation',window_description='最近QCM采样区间平均；Session记录绝对时间窗，停止后stale')
 for key in ['frequency.hz','frequency.true_hz','frequency.delta_hz','mass.inferred_delta_kg']:slots[key]['sampling']=dict(mode='instant',time_domain='simulation',window_description='最近采样区间末端观测；Session保留时间窗，不声称区间均值')
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'eqcm_mass':mech})

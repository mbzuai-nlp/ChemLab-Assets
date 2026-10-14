"""CHEM-111 Frontier manual pyrolysis, finite thermal and species transport."""
from pathlib import Path
import copy,math,json,importlib.util
PROTOCOL_VERSION='1.0.0';SLUG='frontier_egapy';CATALOG_ID='CHEM-111';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch03/t3_analytical'/SLUG
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
TEXT={'type':'string'};BOOL={'type':'boolean'};PHASE=enum(['idle','running','succeeded','cancelled','failed'])
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
R=8.314462618;DT=.1
PLANT=obj({'ambient_k':number('K',283.15,313.15),'gas_reference_k':number('K',273.15,313.15),'gas_reference_pa':number('Pa',90000,120000),'carrier_supply_mol':number('mol',.001,100),'cool_supply_mol':number('mol',0,100),'furnace_capacity_j_k':number('J/K',1,1000),'interface_capacity_j_k':number('J/K',1,1000),'cup_capacity_j_k':number('J/K',.001,1),'gas_volume_m3':number('m^3',1e-8,1e-4),'condensation_fraction':number('1',0,1),'delivery_fraction':number('1',0,1),'accept_engineering_model':BOOL})
PLANT_EXAMPLE=dict(ambient_k=293.15,gas_reference_k=293.15,gas_reference_pa=101325.,carrier_supply_mol=1.,cool_supply_mol=10.,furnace_capacity_j_k=20.,interface_capacity_j_k=8.,cup_capacity_j_k=.03,gas_volume_m3=1e-6,condensation_fraction=.05,delivery_fraction=.98,accept_engineering_model=True)
METHOD=obj({'mode':enum(['single','double','ega']),'td_start_k':number('K',293.15,1323.15),'td_end_k':number('K',293.15,1323.15),'pyrolysis_k':number('K',293.15,1323.15),'ramp_k_s':number('K/s',1/60,10),'hold_s':number('s',0,59994),'pyrolysis_s':number('s',0,59994),'interface_k':number('K',313.15,723.15),'interface_auto':BOOL,'carrier_flow_m3_s':number('m^3/s',1e-8,1e-5),'cooling':BOOL,'cool_pressure_pa':number('Pa',500000,500000)})
METHOD_EXAMPLE=dict(mode='single',td_start_k=323.15,td_end_k=573.15,pyrolysis_k=1073.15,ramp_k_s=5.,hold_s=6.,pyrolysis_s=6.,interface_k=623.15,interface_auto=False,carrier_flow_m3_s=2e-8,cooling=True,cool_pressure_pa=500000.)
YIELD=obj({'substance_id':TEXT,'mass_fraction':number('1',0,1)})
CHANNEL=obj({'channel_id':TEXT,'mass_kg':number('kg',1e-12,1e-4),'a_s_inv':number('1/s',1e-10,1e20),'ea_j_mol':number('J/mol',0,5e5),'char_fraction':number('1',0,1),'products':dict(arr(YIELD),minItems=1,maxItems=50)})
SAMPLE=obj({'sample_id':TEXT,'polymer_id':TEXT,'cup_ul':enum(['50','80']),'density_kg_m3':number('kg/m^3',10,20000),'channels':dict(arr(CHANNEL),minItems=1,maxItems=10),'accept_assumed_kinetics':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='private_PS',polymer_id='PS',cup_ul='50',density_kg_m3=1050.,channels=[dict(channel_id='PS',mass_kg=1e-7,a_s_inv=1e7,ea_j_mol=8e4,char_fraction=.1,products=[dict(substance_id='nist_pps_73',mass_fraction=.9)])],accept_assumed_kinetics=True)
TRACE=obj({'time_s':number('s',0),'furnace_k':number('K',0),'interface_k':number('K',0),'stage':TEXT,'gc_trigger':BOOL})
RESULT=obj({'phase':PHASE,'stage':TEXT,'elapsed_s':number('s',0),'thermal_trace':arr(TRACE),'quality':TEXT,'template_source':TEXT,'warnings':arr(TEXT)})
ROW=obj({'substance_id':TEXT,'generated_kg':number('kg',0),'chamber_kg':number('kg',0),'wall_kg':number('kg',0),'output_kg':number('kg',0),'external_kg':number('kg',0),'residual_kg':number('kg')})
LEDGER=obj({'loaded_kg':number('kg',0),'remaining_kg':number('kg',0),'char_kg':number('kg',0),'returned_kg':number('kg',0),'mass_residual_kg':number('kg'),'species':arr(ROW),'carrier_remaining_mol':number('mol',0),'carrier_used_mol':number('mol',0),'carrier_residual_mol':number('mol'),'cool_remaining_mol':number('mol',0),'cool_used_mol':number('mol',0),'cool_residual_mol':number('mol'),'heater_energy_j':number('J',0),'stored_energy_j':number('J'),'environment_energy_j':number('J'),'energy_residual_j':number('J')})
CAPS={};UNSUPPORTED={'detector.gc_ms','hardware.connect','sampler.automatic','heartcut.start','fault.probability_model'}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},effects or [])
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller','furnace'],'初始化有限气源、热容和传输工程参数','configuration','evaluator',PLANT_EXAMPLE,['plant.configured'])
cap('method.configure',METHOD,obj({'configured':BOOL}),['controller','interface'],'单击/双击/EGA温程与接口设定；原机时长6s、温程1C/min网格','configuration','agent',METHOD_EXAMPLE,['method.configured'])
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['controller','power_switch'],'逻辑电源，断电后热惯性和化学反应仍按步进计算',ex={'enabled':True},effects=['power.enabled','power.angle_rad'])
cap('gas.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['carrier_port'],'外部载气逻辑开关，显式有限气源',ex={'enabled':True},effects=['gas.enabled'])
cap('gc.ready',obj({'ready':BOOL}),obj({'ready':BOOL}),['gc_remote_port'],'外部GC宿主提供就绪信号',vis='evaluator',ex={'ready':True},effects=['gc.ready'])
cap('guard.set',obj({'lift_m':number('m',0,.18)}),obj({'lift_m':number('m',0,.18)}),['protective_guard'],'冷机维护罩手动提升，炉温大于100C拒绝',ex={'lift_m':.18},effects=['guard.lift_m'])
cap('sampler.set',obj({'down_m':number('m',0,.08)}),obj({'down_m':number('m',0,.08)}),['sampler'],'操作人员上提/压下采样杆，不冒充自动采样器',ex={'down_m':.08},effects=['sampler.down_m'])
cap('cup.drop',obj({}),obj({'drop_m':number('m',0,.06)}),['cup_drop'],'人工落杯确认；仅允许热炉就绪的单击或双击第二段',effects=['cup.drop_m'])
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_cup'],'有限质量装杯；动力学和质量产率须显式接受工程假设','configuration','evaluator',SAMPLE_EXAMPLE,['sample.loaded'])
cap('sample.unload',obj({}),obj({'returned_kg':number('kg',0)}),['sample_cup'],'冷杯上提后退回剩余原料和炭，不销毁气路残留',vis='evaluator',effects=['sample.loaded','cup.drop_m'])
cap('acquisition.start',obj({}),obj({'operation_id':TEXT,'phase':PHASE}),['controller','furnace'],'开始异步热程序；等待实际人工动作与外部GC ready',effects=['acquisition.phase','acquisition.stage'])
cap('acquisition.confirm',obj({}),obj({'stage':TEXT}),['controller','sampler'],'人工动作完成后的流程确认',effects=['acquisition.stage'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['controller'],'取消加热程序并保留所有物料、温度与待传输产物',effects=['acquisition.phase'])
cap('process.measure',obj({}),RESULT,['controller'],'读取理想炉温/接口温度及GC触发时间记录，不返回伪造GC峰','query')
cap('products.collect',obj({}),obj({'species':arr(obj({'substance_id':TEXT,'mass_kg':number('kg',0)})),'evidence':TEXT}),['gc_out_port'],'可信宿主领取到达外部GC的有限产物质量包',vis='evaluator')
cap('ledger.read',obj({}),LEDGER,['furnace','sample_cup','interface'],'总质量、产物、气源和热能账本','query','evaluator')
cap('template.reference',obj({'substance_id':TEXT}),obj({'name':TEXT,'ri':number('1'),'ri_reliable_domain':BOOL,'peaks':arr(arr(number('1'),2)),'evidence':TEXT,'template_source':TEXT}),['controller'],'查询独立的NIST公开EI谱/RI；不以样品私有成分冒充观测','query',ex={'substance_id':'nist_pps_73'})
for k in sorted(UNSUPPORTED):cap(k,obj({}),obj({}),['controller'],'当前子集未实现')
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='controller',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
for k in ['plant','method']:slot(k+'.configured',BOOL,'1',k+'配置状态','setpoint')
slot('power.enabled',BOOL,'1','电源开关','setpoint',w=('power.set','enabled'));slot('power.angle_rad',number('rad',0,.2),'rad','电源跷板工程角度','scene',c='power_switch');slot('gas.enabled',BOOL,'1','外部载气开关','setpoint',w=('gas.set','enabled'));slot('gc.ready',BOOL,'1','外部GC提供的就绪信号','sensor')
for k,c,hi in [('guard.lift_m','protective_guard',.18),('sampler.down_m','sampler',.08),('cup.drop_m','cup_drop',.06)]:slot(k,number('m',0,hi),'m','人工机构实际位置','scene',c=c)
for k,z in [('furnace.temperature_k','理想炉温传感器'),('interface.temperature_k','理想接口温度传感器')]:slot(k,number('K',0),'K',z,'sensor')
slot('cup.temperature_k',number('K',0),'K','未观测的杯温','process_truth','evaluator',c='sample_cup');slot('furnace.target_k',number('K',0),'K','当前温程目标','setpoint');slot('sample.loaded',BOOL,'1','样品是否已装入');slot('sample.identity',TEXT,'1','私有样品身份','process_truth','evaluator');slot('acquisition.phase',PHASE,'1','异步状态');slot('acquisition.stage',TEXT,'1','人工/加热阶段');slot('acquisition.elapsed_s',number('s',0),'s','本轮推进时长');slot('acquisition.failure',TEXT,'1','终止故障原因');slot('gc.trigger_count',dict(number('1',0),type='integer'),'1','已发GC启动触发数');slot('solid.remaining_kg',number('kg',0),'kg','杯内原料及炭的真值','process_truth','evaluator');slot('products.pending_kg',number('kg',0),'kg','气室残余产物真值','process_truth','evaluator')
def add(pool,k,x):pool[k]=pool.get(k,0.)+x
class FrontierEgapyRuntime:
 def __init__(self,instance_id='egapy_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.pending=0.;self.plant=None;self.method=None;self.powered=False;self.gas=False;self.gc_ready=False;self.guard=0.;self.sampler=0.;self.drop=0.;self.sample=None;self.channels=[];self.loaded=0.;self.char=0.;self.returned=0.;self.generated={};self.chamber={};self.wall={};self.output={};self.external={};self.carrier=0.;self.cool=0.;self.carrier_used=0.;self.cool_used=0.;self.heat=0.;self.env_heat=0.;self.T=[293.15]*3;self.target=293.15;self.phase='idle';self.stage='idle';self.elapsed=0.;self.stage_time=0.;self.trace=[];self.triggers=0;self.failure='';self.serial=0;self.operation_id=None;self.records={};self._principals={};self._owners={};self._requests={};self.library=json.loads((ASSET/'pyrolyzate_library.json').read_text())
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ['agent','evaluator','debug'] or principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return FrontierEgapySession(self,principal_id)
 def describe(self):return export_frontier_egapy_contract_metadata()
 def read(self,visibility='agent'):
  d={'plant.configured':bool(self.plant),'method.configured':bool(self.method),'power.enabled':self.powered,'power.angle_rad':.2 if self.powered else 0.,'gas.enabled':self.gas,'gc.ready':self.gc_ready,'guard.lift_m':self.guard,'sampler.down_m':self.sampler,'cup.drop_m':self.drop,'furnace.temperature_k':self.T[0],'interface.temperature_k':self.T[1],'cup.temperature_k':self.T[2],'furnace.target_k':self.target,'sample.loaded':bool(self.sample),'sample.identity':self.sample['sample_id'] if self.sample else '', 'acquisition.phase':self.phase,'acquisition.stage':self.stage,'acquisition.elapsed_s':self.elapsed,'acquisition.failure':self.failure,'gc.trigger_count':self.triggers,'solid.remaining_kg':sum(x['remaining'] for x in self.channels)+self.char,'products.pending_kg':sum(self.chamber.values())};return copy.deepcopy({k:v for k,v in d.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _result(self):return dict(phase=self.phase,stage=self.stage,elapsed_s=self.elapsed,thermal_trace=copy.deepcopy(self.trace),quality='engineering_process_ideal_sensors',template_source='NIST June2025 measured EI/RI library; query template.reference independently',warnings=['No internal GC/MS detector; finite products are trusted-host handoff only','Arrhenius/yields/capacities and heat transfer are explicit assumptions; no chemical elementary mechanism','Ideal temperature sensors; documented +/-0.1C stability is not experimentally validated','Manual cup insertion/retraction/drop and external GC ready required'])
 def _ledger(self):
  rows=[]
  for k,n in self.generated.items():
   f={name+'_kg':getattr(self,name).get(k,0.) for name in ['chamber','wall','output','external']};rows.append(dict(substance_id=k,generated_kg=n,residual_kg=n-sum(f.values()),**f))
  remaining=sum(x['remaining'] for x in self.channels);stored=sum(c*(t-self.plant['ambient_k']) for c,t in zip(self._capacities(),self.T)) if self.plant else 0.
  return dict(loaded_kg=self.loaded,remaining_kg=remaining,char_kg=self.char,returned_kg=self.returned,mass_residual_kg=self.loaded-remaining-self.char-self.returned-sum(self.generated.values()),species=rows,carrier_remaining_mol=self.carrier,carrier_used_mol=self.carrier_used,carrier_residual_mol=(self.plant['carrier_supply_mol'] if self.plant else 0.)-self.carrier-self.carrier_used,cool_remaining_mol=self.cool,cool_used_mol=self.cool_used,cool_residual_mol=(self.plant['cool_supply_mol'] if self.plant else 0.)-self.cool-self.cool_used,heater_energy_j=self.heat,stored_energy_j=stored,environment_energy_j=self.env_heat,energy_residual_j=self.heat-stored-self.env_heat)
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[k][0],a);before=copy.deepcopy({x:v for x,v in self.__dict__.items() if x!='library'})
  try:
   v=self._invoke(k,a);validate(CAPS[k][1],v)
   if CAPS[k][4] not in ['query','analysis']:self.revision+=1
   return v
  except Exception:
   lib=self.library;self.__dict__.clear();self.__dict__.update(before);self.library=lib;raise
 def _invoke(self,k,a):
  if k=='process.measure':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='template.reference':
   if a['substance_id'] not in self.library['substances']:raise ActionRejected('UNKNOWN_REFERENCE')
   r=self.library['substances'][a['substance_id']];return dict(name=r['name'],ri=r['retention_index'],ri_reliable_domain=r['ri_reliable_domain'],peaks=copy.deepcopy(r['peaks']),evidence=r['evidence'],template_source=r['source']['url']+' MSPEC#'+r['source']['record_id'])
  if k=='products.collect':
   packet=[dict(substance_id=x,mass_kg=v) for x,v in self.output.items() if v>0]
   for x in packet:add(self.external,x['substance_id'],x['mass_kg'])
   self.output={};return dict(species=packet,evidence='finite pyrolyzate handoff to external GC; not detector signal')
  if k=='plant.configure':
   if self.plant or self.powered or not a['accept_engineering_model']:raise ActionRejected('INITIALIZATION_ONLY')
   self.plant=copy.deepcopy(a);self.carrier=a['carrier_supply_mol'];self.cool=a['cool_supply_mol'];self.T=[a['ambient_k']]*3;self.target=a['ambient_k'];return {'configured':True}
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self.phase='cancelled';self.records[self.operation_id]=self._result();return {'phase':self.phase}
  if k=='power.set':
   if not a['enabled'] and self.phase=='running':self._invoke('acquisition.stop',{})
   self.powered=a['enabled'];return a
  if k=='gas.set':
   if not a['enabled'] and self.phase=='running':raise ActionRejected('BUSY')
   self.gas=a['enabled'];return a
  if k=='gc.ready':self.gc_ready=a['ready'];return a
  if k=='sampler.set':
   if self.drop>0 or (self.phase=='running' and self.stage not in ['wait_insert','wait_retract']):raise ActionRejected('MANUAL_INTERLOCK')
   self.sampler=a['down_m'];return a
  if k=='cup.drop':
   if self.phase!='running' or self.stage!='wait_drop' or self.sampler>1e-8 or not self.gc_ready:raise ActionRejected('MANUAL_INTERLOCK')
   self.drop=.06;return {'drop_m':self.drop}
  if k=='acquisition.confirm':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   if self.stage=='wait_insert' and self.sampler>=.08-1e-8 and self.gc_ready:self.stage='td';self.triggers+=1;self.stage_time=0.;self.gc_ready=False;self.trace.append(dict(time_s=self.time_s,furnace_k=self.T[0],interface_k=self.T[1],stage=self.stage,gc_trigger=True))
   elif self.stage=='wait_retract' and self.sampler<1e-8:self.stage='cool' if self.method['mode']=='ega' else 'heat_py';self.stage_time=0.
   elif self.stage=='wait_drop' and self.drop>=.06-1e-8 and self.gc_ready:self.stage='py';self.triggers+=1;self.stage_time=0.;self.gc_ready=False;self.trace.append(dict(time_s=self.time_s,furnace_k=self.T[0],interface_k=self.T[1],stage=self.stage,gc_trigger=True))
   else:raise ActionRejected('MANUAL_INTERLOCK')
   return {'stage':self.stage}
  if self.phase=='running':raise ActionRejected('BUSY')
  if k=='guard.set':
   if self.T[0]>373.15 or self.sampler>0 or self.drop>0:raise ActionRejected('HOT_INTERLOCK')
   self.guard=a['lift_m'];return a
  if k=='method.configure':
   if not self.plant:raise ActionRejected('NOT_READY')
   if min(a[x] for x in ['td_start_k','td_end_k','pyrolysis_k'])<self.plant['ambient_k']+10 or a['td_end_k']<a['td_start_k']:raise ActionRejected('INVALID_ARGUMENT')
   if any(abs(a[x]/6-round(a[x]/6))>1e-8 for x in ['hold_s','pyrolysis_s']) or abs(a['ramp_k_s']*60-round(a['ramp_k_s']*60))>1e-8 or any(abs(a[x]-273.15-round(a[x]-273.15))>1e-8 for x in ['td_start_k','td_end_k','pyrolysis_k','interface_k']):raise ActionRejected('INVALID_ARGUMENT')
   self.method=copy.deepcopy(a);return {'configured':True}
  if k=='sample.load':
   if not self.plant or self.sample or self.sampler or self.drop or self.T[2]>313.15 or not a['accept_assumed_kinetics']:raise ActionRejected('LOAD_PRECONDITION')
   if a['polymer_id'] not in self.library['polymers']:raise ActionRejected('UNKNOWN_REFERENCE')
   if len({x['channel_id'] for x in a['channels']})!=len(a['channels']):raise ActionRejected('INVALID_ARGUMENT')
   for ch in a['channels']:
    if abs(ch['char_fraction']+sum(x['mass_fraction'] for x in ch['products'])-1)>1e-10 or len({x['substance_id'] for x in ch['products']})!=len(ch['products']):raise ActionRejected('INVALID_ARGUMENT')
    if any(x['substance_id'] not in self.library['polymers'][a['polymer_id']] for x in ch['products']):raise ActionRejected('UNKNOWN_REFERENCE')
   mass=sum(x['mass_kg'] for x in a['channels'])
   if mass/a['density_kg_m3']>float(a['cup_ul'])*1e-9:raise ActionRejected('CUP_CAPACITY')
   self.sample=copy.deepcopy(a);self.channels=[dict(x,remaining=x['mass_kg']) for x in a['channels']];self.loaded+=mass;return {'loaded':True}
  if k=='sample.unload':
   if not self.sample or self.T[2]>313.15 or self.sampler:raise ActionRejected('HOT_INTERLOCK')
   n=sum(x['remaining'] for x in self.channels)+self.char;self.returned+=n;self.channels=[];self.char=0.;self.sample=None;self.drop=0.;return {'returned_kg':n}
  if k=='acquisition.start':
   if not self.powered or not self.plant or not self.method or not self.sample or self.guard or self.drop or self.sampler or not self.gas or self.carrier<=0:raise ActionRejected('NOT_READY')
   self.phase='running';self.stage='heat_py' if self.method['mode']=='single' else 'heat_td';self.stage_time=0.;self.elapsed=0.;self.trace=[];self.failure='';self.serial+=1;self.operation_id=f'{self.instance_id}:op:{self.serial}';return dict(operation_id=self.operation_id,phase=self.phase)
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _capacities(self):return [self.plant[x] for x in ['furnace_capacity_j_k','interface_capacity_j_k','cup_capacity_j_k']]
 def _exchange(self,i,j,g):
  c=self._capacities();q=(self.T[i]-self.T[j])*(-math.expm1(-g*(1/c[i]+1/c[j])*DT))/(1/c[i]+1/c[j]);self.T[i]-=q/c[i];self.T[j]+=q/c[j]
 def _tick(self):
  if not self.plant:return
  p=self.plant;m=self.method;amb=p['ambient_k'];active=self.powered and self.phase=='running';self.elapsed+=DT if self.phase=='running' else 0.;self.stage_time+=DT if self.phase=='running' else 0.
  if active:
   if self.stage in ['heat_py','wait_drop','py']:desired=m['pyrolysis_k']
   elif self.stage in ['heat_td','wait_insert']:desired=m['td_start_k']
   elif self.stage=='td':desired=min(m['td_end_k'],m['td_start_k']+m['ramp_k_s']*self.stage_time)
   elif self.stage=='wait_retract':desired=m['td_end_k']
   else:desired=amb
   speed=m['ramp_k_s'] if self.stage=='td' else 10.;self.target+=max(-speed*DT,min(speed*DT,desired-self.target))
  else:self.target=amb
  cooling=bool(m and m['cooling'] and self.stage=='cool' and self.powered)
  nfactor=p['gas_reference_pa']/R/p['gas_reference_k'];q=m['carrier_flow_m3_s'] if m and self.gas else 0.;need=q*nfactor*DT
  if need>self.carrier+1e-15:
   q=self.carrier/nfactor/DT;need=self.carrier;self.gas=False
   if self.phase=='running':self.phase='failed';self.failure='CARRIER_DEPLETED'
  self.carrier=max(0.,self.carrier-need);self.carrier_used+=need
  if cooling:
   needcool=7e-3/60*nfactor*DT
   if needcool>self.cool+1e-15:cooling=False;self.phase='failed';self.failure='COOLING_GAS_DEPLETED'
   else:self.cool-=needcool;self.cool_used+=needcool
  c=self._capacities();ti=min(m['interface_k'],self.T[0]+100.) if m and m['interface_auto'] else m['interface_k'] if m else amb;ti=max(313.15,ti) if active else amb
  for i,(g,maximum,tgt) in enumerate([(.12+(.7 if cooling else 0.),300.,self.target),(.04,100.,ti),(.003,0.,amb)]):
   old=self.T[i];power=max(0.,min(maximum,g*(old-amb)+c[i]*(tgt-old)/DT)) if active and self.stage!='cool' else 0.;new=amb+power/g+(old-amb-power/g)*math.exp(-g*DT/c[i]);self.T[i]=new;self.heat+=power*DT;self.env_heat+=power*DT-c[i]*(new-old)
  self._exchange(0,1,.012)
  if self.sample and (self.sampler>=.079 or self.drop>=.059):self._exchange(0,2,.08)
  for ch in self.channels:
   rate=ch['a_s_inv']*math.exp(-ch['ea_j_mol']/R/self.T[2]);released=ch['remaining']*(-math.expm1(-rate*DT));ch['remaining']-=released;self.char+=released*ch['char_fraction']
   for item in ch['products']:
    sid=item['substance_id'];n=released*item['mass_fraction'];add(self.generated,sid,n);add(self.wall,sid,n*p['condensation_fraction']);add(self.chamber,sid,n*(1-p['condensation_fraction']))
  if q:
   for sid,n in list(self.chamber.items()):
    out=n*(-math.expm1(-q/p['gas_volume_m3']*DT));self.chamber[sid]-=out;add(self.output,sid,out*p['delivery_fraction']);add(self.wall,sid,out*(1-p['delivery_fraction']))
  if self.phase=='running':
   if self.T[0]>1373.15 or self.T[1]>773.15:self.phase='failed';self.failure='OVERTEMPERATURE'
   if self.stage=='heat_py' and abs(self.T[0]-m['pyrolysis_k'])<2.:self.stage='wait_drop';self.stage_time=0.
   elif self.stage=='heat_td' and abs(self.T[0]-m['td_start_k'])<2.:self.stage='wait_insert';self.stage_time=0.
   elif self.stage=='td' and self.stage_time>=(m['td_end_k']-m['td_start_k'])/m['ramp_k_s']+m['hold_s']:self.stage='wait_retract';self.stage_time=0.
   elif self.stage=='py' and self.stage_time+1e-8>=m['pyrolysis_s']:self.stage='cool';self.stage_time=0.
   elif self.stage=='cool' and self.T[0]<=323.15:self.phase='succeeded'
  if int((self.time_s+1e-8)/DT)%10==0:self.trace.append(dict(time_s=self.time_s,furnace_k=self.T[0],interface_k=self.T[1],stage=self.stage,gc_trigger=False))
  if self.operation_id:self.records[self.operation_id]=self._result() if self.phase!='running' else dict(phase=self.phase)
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<0 or dt_s>7200:raise ActionRejected('INVALID_ARGUMENT')
  self.pending+=dt_s
  while self.pending+1e-12>=DT:self.pending=max(0.,self.pending-DT);self.time_s+=DT;self._tick();self.revision+=1

class FrontierEgapySession:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,i):
  if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  m=export_frontier_egapy_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
 def read(self,*,instance_id,state_ids=None):
  self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
  if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
  if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
  return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='unavailable' if v[k] is None else 'estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=h.time_s,sample_window_s=None) for k in ids})

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





def export_frontier_egapy_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions='absolute K; gas flow at explicit host reference P/T; manual displacement positive down, protective guard positive up',origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'FrontierEgapySession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description='fixed0.1s energy-conserving split thermal/reaction/flow integration; ideal temperature sensors'),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNKNOWN_OPERATION','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','INITIALIZATION_ONLY','BUSY','UNKNOWN_REFERENCE','NOT_READY','NOT_RUNNING','MANUAL_INTERLOCK','HOT_INTERLOCK','LOAD_PRECONDITION','CUP_CAPACITY']
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  un=k in UNSUPPORTED;asyn=k=='acquisition.start';caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if un else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if un else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if un else 'implemented'),binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.frontier_egapy.FrontierEgapySession.invoke',invocation=f'session.invoke(instance_id="egapy_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if kind=='query' or un else [dict(description_zh='热安全、手动采样阶段、GC ready、有限库存与配置域；拒绝原子回退',state_ids=['power.enabled','method.configured','sample.loaded','guard.lift_m','sampler.down_m','cup.drop_m','acquisition.phase'],enforced_by='runtime',check='Runtime.invoke atomic rollback and authenticated Session')],state_effects=[dict(state_id=x,timing='immediate',description_zh='受检事务改变状态') for x in effects],observes=[],model_effects=dict(mode='none',description_zh='待独立场景验证',binding_ids=[]),execution=dict(mode='asynchronous' if asyn else 'synchronous',time_domain='simulation',completion_criterion='人工动作/GC就绪后执行热程序并冷却至50C' if asyn else '受检事务返回；只step推进物理',completion_state_ids=['acquisition.phase'] if asyn else [],timeout_s=None,cancellation='supported' if asyn else 'unsupported',retry='same_request_id',resources=['host_clock','finite_sample','carrier','cooling_gas','thermal_energy','request_cache'],partial_failure_zh='保留实际温度、剩余原料/炭、产物残留；取消/断电后仍有被动传热与热反应；载气独立开关'),errors=[dict(code=x,meaning_zh=x,side_effects='none') for x in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=['实测动力学/产率/传热/传感器噪声未知；无内置GC检测器'],source_ids=['manufacturer_manual','simulation_assumption'])
  if asyn:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='宿主推进热量、反应和有限气路库存') for x in ['furnace.temperature_k','interface.temperature_k','cup.temperature_k','acquisition.stage','acquisition.elapsed_s','acquisition.phase']]
 mech=dict(component_ids=['controller','furnace','sample_cup','interface','gc_out_port'],state_ids=list(slots),meaning_zh='手动单击/双击/温程裂解、有限样品热反应、能量与载气守恒及GC物料接口',implementation='lumped',equations=['T_next=Ta+P/G+(T-Ta-P/G)*exp(-G*dt/C), P_f<=300W,P_itf<=100W','Qij=(Ti-Tj)*(1-exp(-Gij*(1/Ci+1/Cj)*dt))/(1/Ci+1/Cj); each pair exchange conserves energy','heater energy = stored sensible energy + signed environment heat; reaction enthalpy explicitly0 (no chemical energy partition)','dm=remaining*(1-exp(-A*exp(-Ea/R/Tcup)*dt)); provided char and product mass fractions sum1','generated=chamber+condensate/wall+GCoutput+external; loaded=unreacted+char+returned+generated','well-mixed chamber release=n*(1-exp(-Q*dt/V)); finite gas n=PQdt/RT at explicit reference conditions','furnace ramp<=10K/s; ITFauto=min(upper_limit,Tf+100K); cool7L/min500kPa, assumed Gcool','manual TD insert -> run -> retract -> heatPY -> manualdrop+GCready -> pyrolysis -> cool; no auto-actuator assertion'],parameters={},source_ids=['manufacturer_manual','manufacturer_specs','nist_pps','simulation_assumption'],limitations=['164 real pyrolyzate EI/RI spectra map to11 parent polymers, not50 measured parent-polymer yields/thermal-release curves.','Original NIST library uses Gerstel pyrolyzer and cryotrapping; transfer to Frontier not calibrated.','EI relative intensity is not a reaction mass yield; A,Ea and yields require explicit host assumptions.','Total mass conserved; elemental reaction stoichiometry and reaction enthalpy not modeled.','Ideal thermometry; manufacturer +/-0.1C stability and cooling performance not experimentally validated.','Temperature guards/manual moves are process-authoritative; contact, seals, optical metrology and robot handling not_run.','No actual GC/MS, optional heartcut/cryotrap/autosampler or hardware controller.'])
 spec=importlib.util.spec_from_file_location('_egapy_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'pyrolysis':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'。'
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'pyrolysis':mech})

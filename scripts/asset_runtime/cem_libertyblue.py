"""Liberty Blue 2.0: conditional washed SPPS, not a validated synthesis recipe."""
from __future__ import annotations
import copy,json,math
from asset_runtime import _annotations as _ann
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT,PHASE
from asset_runtime.antonpaar_abbematadvanced import validate
ROOT=Path(__file__).resolve().parents[2];ASSET_TYPE_ID='instrument.cem_libertyblue';CATALOG_ID='CHEM-022';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/cem_libertyblue_peptide_library/templates.json';AA='ACDEFGHIKLMNPQRSTVWY'
def arr(s,n=None):
 d={'type':'array','items':s}
 if n is not None:d.update(minItems=n,maxItems=n)
 return d
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
def integer(lo,hi):return {'type':'integer','minimum':lo,'maximum':hi,'x-unit':'1'}
FRAC=number('1',0,1);TEMP=number('K',273.15,423.15);LIFT=number('m',0,.04);COVER=number('m',0,.08);V=number('m^3',0);MOL=number('mol',0);MASS=number('kg',0)
STOCK=obj({'identity':TEXT,'volume_m3':V,'density_kg_m3':number('kg/m^3',1,3000),'concentration_mol_m3':number('mol/m^3',0,10000),'retained_increment_kg_mol':number('kg/mol',0,2)})
PLANT=obj({'aa_stocks':arr(STOCK,27),'external_stocks':arr(STOCK,4),'queue_module':{'type':'string','enum':['none','HT4','HT12']},'reactor_capacity_m3':number('m^3',1e-6,.001),'hold_up_m3':number('m^3',1e-9,1e-5),'fmoc_increment_kg_mol':number('kg/mol',.001,1),'base_molar_mass_kg_mol':number('kg/mol',.001,1),'nitrogen_connected':BOOL,'recipe_reviewed':BOOL,'thermal_tau_s':number('s',.01,10000),'initial_temperature_k':TEMP})
KINETICS=obj({'coupling_k_s_inv':arr(number('1/s',0,100),20),'deprotect_k_s_inv':number('1/s',0,100),'reference_temperature_k':TEMP,'activation_energy_j_mol':number('J/mol',0,120000),'base_reference_mol_m3':number('mol/m^3',1e-6,10000),'activation_reference_mol_m3':number('mol/m^3',1e-6,10000),'base_penalty_m3_mol':number('m^3/mol',0,100),'carryover_penalty_m3_mol':number('m^3/mol',0,100),'base_evaporation_s_inv':number('1/s',0,10),'calibration_evidence':TEXT})
STEP=obj({'temperature_k':TEMP,'deprotect_s':number('s',.01,3600),'couple_s':number('s',.01,3600),'wash_cycles':integer(1,10),'sequence_factor':number('1',0,10)})
JOB=obj({'resin_id':TEXT,'sequence_ntoc':TEXT,'scale_mol':number('mol',5e-6,5e-3),'resin_mass_kg':number('kg',1e-6,1),'loading_mol_kg':number('mol/kg',.001,10),'preloaded_c_terminal':TEXT,'steps':arr(STEP)})
QUEUE=obj({'jobs':arr(JOB),'fill_flow_m3_s':number('m^3/s',1e-9,1e-5),'drain_flow_m3_s':number('m^3/s',1e-9,1e-5),'aa_dose_m3':number('m^3',1e-8,1e-4),'base_dose_m3':number('m^3',1e-8,1e-4),'activator_dose_m3':number('m^3',1e-8,1e-4),'wash_dose_m3':number('m^3',1e-8,1e-4),'wash_mix_s':number('s',.01,3600)})
def stock(identity,concentration=100.,increment=.1):return dict(identity=identity,volume_m3=1e-4,density_kg_m3=900.,concentration_mol_m3=concentration,retained_increment_kg_mol=increment)
EXAMPLE=dict(aa_stocks=[stock(a) for a in AA]+[stock('',0.,0.) for _ in range(7)],external_stocks=[stock('activator_1'),stock('activator_2'),stock('base'),stock('wash',0.,0.)],queue_module='HT12',reactor_capacity_m3=50e-6,hold_up_m3=.1e-6,fmoc_increment_kg_mol=.22224,base_molar_mass_kg_mol=.07112,nitrogen_connected=True,recipe_reviewed=True,thermal_tau_s=2.,initial_temperature_k=298.15)
KINETIC_EXAMPLE=dict(coupling_k_s_inv=[.6]*20,deprotect_k_s_inv=1.,reference_temperature_k=298.15,activation_energy_j_mol=0.,base_reference_mol_m3=50.,activation_reference_mol_m3=10.,base_penalty_m3_mol=.05,carryover_penalty_m3_mol=.01,base_evaporation_s_inv=.01,calibration_evidence='synthetic_host_parameters; no AFPS-to-CEM calibration')
STEP_EXAMPLE=dict(temperature_k=298.15,deprotect_s=3.,couple_s=3.,wash_cycles=2,sequence_factor=1.)
def job_example(sequence='GAC',resin_id='resin_01'):
 return dict(resin_id=resin_id,sequence_ntoc=sequence,scale_mol=1e-5,resin_mass_kg=2e-5,loading_mol_kg=.5,preloaded_c_terminal=sequence[-1],steps=[copy.deepcopy(STEP_EXAMPLE) for _ in sequence])
QUEUE_EXAMPLE=dict(jobs=[job_example()],fill_flow_m3_s=1e-6,drain_flow_m3_s=2e-6,aa_dose_m3=1e-6,base_dose_m3=1e-6,activator_dose_m3=.5e-6,wash_dose_m3=2e-6,wash_mix_s=.2)
POINT=obj({'time_s':number('s',0),'job_index':integer(0,11),'residue_count':integer(0,500),'stage':TEXT,'temperature_k':TEMP,'waste_volume_m3':V,'phase':PHASE})
RESULT=obj({'phase':PHASE,'fault':TEXT,'elapsed_s':number('s',0),'job_index':integer(0,11),'residue_count':integer(0,500),'stage':TEXT,'temperature_k':nullable(TEMP),'waste_volume_m3':V,'true_purity_fraction':{'type':'null'},'isolated_yield_fraction':{'type':'null'},'evidence':TEXT})
PRODUCT=obj({'resin_id':TEXT,'sequence_ntoc':TEXT,'bound_resin_mass_kg':MASS,'site_mol':MOL,'free_site_mol':MOL,'fmoc_protected_site_mol':MOL,'conditional_correct_free_mol':MOL,'conditional_correct_protected_mol':MOL,'conditional_full_length_fraction':nullable(FRAC),'true_purity_fraction':{'type':'null'},'isolated_yield_fraction':{'type':'null'},'ready_for_cleavage':BOOL,'evidence':TEXT})
FEATURE=obj({'substance_id':TEXT,'sequence_ntoc':TEXT,'row_index':integer(0,500),'residue_count':integer(1,500),'first_area':number('1'),'first_height':number('1'),'first_width':number('1'),'first_diff':number('1'),'width_minus_height':number('1'),'template_source':TEXT,'source_url':TEXT,'raw_sha256':TEXT,'evidence':TEXT})
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet'],'供电；断电保留阶段和库存','device_command','agent',{'enabled':True},['power.enabled','cycle.phase','temperature.indicated_k']),
 'cover.set_lift':(obj({'lift_m':COVER}),obj({'lift_m':COVER}),['reactor_cover'],'非厂家行程的虚拟反应罩升降，运行中禁止','device_command','agent',{'lift_m':.08},['cover.lift_m']),
 'clamp.set_lift':(obj({'lift_m':LIFT}),obj({'lift_m':LIFT}),['reactor_clamp'],'虚拟反应器夹升降，运行中禁止','device_command','agent',{'lift_m':.04},['clamp.lift_m']),
 'plant.configure':(PLANT,obj({'configured':BOOL}),['aa_rack','reagent_rack','reactor','nitrogen_inlet'],'宿主配置27AA+4外试剂、单反应器和逻辑队列模块','configuration','evaluator',EXAMPLE,['plant.configured','queue.capacity']),
 'kinetics.configure':(KINETICS,obj({'configured':BOOL}),['reactor','heater'],'宿主显式速率、序列/残留与温度模型；没有真实标定','configuration','evaluator',KINETIC_EXAMPLE,['kinetics.configured']),
 'queue.configure':(QUEUE,obj({'queued':integer(1,12),'attachment_events':arr(TEXT)}),['ht12_rack','reactor','aa_rack'],'一次配置预载C端树脂和逐残基方法；N到C输入、C到N执行','configuration','evaluator',QUEUE_EXAMPLE,['queue.size','queue.configured']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['reactor','pump','heater'],'闭罩夹紧供电后启动或续作，队列始终单反应器串行','device_command','agent',{},['cycle.phase','cycle.fault']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['pump','heater'],'取消冻结本模型反应和输送；不是实机紧急停机化学保证','device_command','agent',{},['cycle.phase']),
 'synthesis.measure':(obj({}),RESULT,['display_digits'],'过程读数；不从归一化UV或完成时间伪造真实纯度','query','agent',{},[]),
 'history.read':(obj({}),obj({'records':arr(POINT),'truncated':BOOL,'evidence':TEXT}),['external_pc'],'读取阶段转换合成日志，不推进时间','query','agent',{},[]),
 'product.read':(obj({'job_index':integer(0,11)}),PRODUCT,['reactor'],'宿主条件性正确链期望量；不鉴定删除肽/真实纯度','query','evaluator',{'job_index':0},[]),
 'reference.lookup':(obj({'substance_id':TEXT,'row_index':integer(0,500)}),FEATURE,['external_pc'],'查询真实AFPS归一化特征，不假称CEM传感器曲线或偶联收率','query','agent',{'substance_id':'afps_prefix_fcf5275ec9bb','row_index':0},[]),
 'resin.unload':(obj({'job_index':integer(0,11),'resin_id':TEXT}),obj({'unloaded':BOOL,'attachment_event':TEXT}),['ht12_rack','reactor'],'完成队列后宿主移除树脂与质量，事件不创建机器人约束','configuration','evaluator',{'job_index':0,'resin_id':'resin_01'},['truth.removed_mass_kg']),
}
SLOTS={}
def slot(k,s,u,o='derived',v='agent',c='display_digits',w=None):SLOTS[k]=(s,u,o,v,[c],w)
slot('power.enabled',BOOL,'1','setpoint',c='power_inlet',w=('power.set','enabled'));slot('cover.lift_m',COVER,'m','scene',c='reactor_cover',w=('cover.set_lift','lift_m'));slot('clamp.lift_m',LIFT,'m','scene',c='reactor_clamp',w=('clamp.set_lift','lift_m'))
for k,s,u in [('plant.configured',BOOL,'1'),('kinetics.configured',BOOL,'1'),('queue.configured',BOOL,'1'),('queue.capacity',integer(1,12),'1'),('queue.size',integer(0,12),'1'),('queue.active_index',integer(0,11),'1'),('cycle.phase',PHASE,'1'),('cycle.fault',TEXT,'1'),('cycle.elapsed_s',number('s',0),'s'),('cycle.stage',TEXT,'1'),('cycle.residue_count',integer(0,500),'1'),('temperature.target_k',TEMP,'K'),('temperature.indicated_k',nullable(TEMP),'K'),('waste.volume_m3',V,'m^3'),('reactor.volume_m3',V,'m^3')]:slot(k,s,u)
for k in ['input','retained','waste','vent','removed']:slot('truth.'+k+'_mass_kg',MASS,'kg','process_truth','evaluator','reactor')
for k in ['initial_aa','remaining_aa','bound_aa','waste_aa','removed_aa']:slot('truth.'+k+'_mol',arr(MOL,20),'mol','process_truth','evaluator','reactor')
for k in ['free_sites','protected_sites','correct_free','correct_protected','residual_base']:slot('truth.'+k+'_mol',MOL,'mol','process_truth','evaluator','reactor')
slot('truth.stock_volumes_m3',arr(V,31),'m^3','process_truth','evaluator','aa_rack')
class CemLibertyblueRuntime:
 def __init__(self,instance_id='libertyblue_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.cover=0.;self.lift=0.;self.plant=None;self.kinetics=None;self.queue=None;self.phase='idle';self.fault='';self.elapsed_s=0.;self.temp_k=298.15;self.target_k=298.15;self.operation_id=None;self._operation_count=0;self.stocks=[];self.resins=[];self.plan=[];self.cursor=0;self.stage_progress=0.;self.entered=False;self.job_index=0;self.residue_count=0;self.pool=dict(volume=0.,mass=0.,aa=[0.]*20,base=0.,act=[0.,0.]);self.waste=dict(volume=0.,mass=0.,aa=[0.]*20);self.vent_mass=0.;self.removed_mass=0.;self.removed_aa=[0.]*20;self.input_mass=0.;self.initial_aa=[0.]*20;self.history=[];self.history_truncated=False;self.templates={t['substance_id']:t for t in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return CemLibertyblueSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _inventory(self):return dict(input=self.input_mass,retained=sum(s['volume_m3']*s['density_kg_m3'] for s in self.stocks)+self.pool['mass']+sum(r['mass'] for r in self.resins if not r['removed']),waste=self.waste['mass'],vent=self.vent_mass,removed=self.removed_mass)
 def _aa(self):
  remaining=list(self.pool['aa']);bound=[0.]*20
  for s in self.stocks[:27]:
   if s['identity'] in AA and s['identity']:remaining[AA.index(s['identity'])]+=s['volume_m3']*s['concentration_mol_m3']
  for r in self.resins:
   if not r['removed']:bound=[x+y for x,y in zip(bound,r['bound'])]
  return remaining,bound
 def _values(self):
  r=self.resins[self.job_index] if self.resins else dict(free=0.,protected=0.,correct_free=0.,correct_protected=0.);rem,bound=self._aa();v={'power.enabled':self.powered,'cover.lift_m':self.cover,'clamp.lift_m':self.lift,'plant.configured':self.plant is not None,'kinetics.configured':self.kinetics is not None,'queue.configured':self.queue is not None,'queue.capacity':{'none':1,'HT4':4,'HT12':12}[self.plant['queue_module']] if self.plant else 1,'queue.size':len(self.resins),'queue.active_index':self.job_index,'cycle.phase':self.phase,'cycle.fault':self.fault,'cycle.elapsed_s':self.elapsed_s,'cycle.stage':self.plan[self.cursor]['kind'] if self.cursor<len(self.plan) else ('complete' if self.plan else 'idle'),'cycle.residue_count':self.residue_count,'temperature.target_k':self.target_k,'temperature.indicated_k':round(self.temp_k-273.15,1)+273.15 if self.powered else None,'waste.volume_m3':self.waste['volume'],'reactor.volume_m3':self.pool['volume'],'truth.initial_aa_mol':self.initial_aa,'truth.remaining_aa_mol':rem,'truth.bound_aa_mol':bound,'truth.waste_aa_mol':self.waste['aa'],'truth.removed_aa_mol':self.removed_aa,'truth.free_sites_mol':r['free'],'truth.protected_sites_mol':r['protected'],'truth.correct_free_mol':r['correct_free'],'truth.correct_protected_mol':r['correct_protected'],'truth.residual_base_mol':self.pool['base'],'truth.stock_volumes_m3':[s['volume_m3'] for s in self.stocks] if self.stocks else [0.]*31}
  v.update({'truth.'+k+'_mass_kg':x for k,x in self._inventory().items()});return v
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('evaluator','debug')})
 def debug_snapshot(self):return dict(state=self.read('evaluator'),resins=copy.deepcopy(self.resins),pool=copy.deepcopy(self.pool),cursor=self.cursor,stage_progress=self.stage_progress,history=copy.deepcopy(self.history))
 def _measure(self):
  v=self._values();return dict(phase=self.phase,fault=self.fault,elapsed_s=self.elapsed_s,job_index=self.job_index,residue_count=self.residue_count,stage=v['cycle.stage'],temperature_k=v['temperature.indicated_k'],waste_volume_m3=self.waste['volume'],true_purity_fraction=None,isolated_yield_fraction=None,evidence='conditional_host_parameter_model; not calibrated; not real CEM logs')
 def _compile(self,q):
  p=[]
  for ji,j in enumerate(q['jobs']):
   seq=j['sequence_ntoc'][::-1]
   for ri,s in enumerate(j['steps']):
    base=dict(job=ji,residue=ri+1,temperature=s['temperature_k'],method=s)
    def add(kind,**args):p.append(dict(base,kind=kind,**args))
    def wash():
     for _ in range(s['wash_cycles']):add('fill',source=30,volume=q['wash_dose_m3']);add('wash',duration=q['wash_mix_s']);add('drain')
    add('fill',source=29,volume=q['base_dose_m3']);add('deprotect',duration=s['deprotect_s']);add('drain');wash()
    if ri<len(seq)-1:
     aa=seq[ri+1];idx=next(i for i,x in enumerate(self.stocks[:27]) if x['identity']==aa);add('fill',source=idx,volume=q['aa_dose_m3']);add('fill',source=27,volume=q['activator_dose_m3']);add('fill',source=28,volume=q['activator_dose_m3']);add('couple',duration=s['couple_s'],aa=AA.index(aa),source=idx);add('drain');wash()
   p.append(dict(job=ji,residue=len(seq),temperature=j['steps'][-1]['temperature_k'],kind='finish',duration=0.))
  return p
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if 'job_index' in a and (type(a['job_index']) is not int or not 0<=a['job_index']<len(self.resins)):raise ActionRejected('INVALID_ARGUMENT')
  if 'row_index' in a and type(a['row_index']) is not int:raise ActionRejected('INVALID_ARGUMENT')
  if self.phase=='running' and CAPS[capability][4]!='query' and capability not in ('power.set','acquisition.stop'):raise ActionRejected('BUSY')
  if capability=='power.set':
   self.powered=a['enabled']
   if not self.powered and self.phase=='running':self.phase='cancelled'
   out=dict(a)
  elif capability=='cover.set_lift':self.cover=a['lift_m'];out=dict(a)
  elif capability=='clamp.set_lift':self.lift=a['lift_m'];out=dict(a)
  elif capability=='plant.configure':
   stocks=a['aa_stocks']+a['external_stocks'];ids=[s['identity'] for s in a['aa_stocks'] if s['identity']]
   if self.plant or self.cover<.079 or self.lift<.039 or not a['recipe_reviewed'] or not a['nitrogen_connected'] or a['hold_up_m3']>=a['reactor_capacity_m3'] or any(x not in AA or len(x)!=1 for x in ids) or len(ids)!=len(set(ids)) or [s['identity'] for s in a['external_stocks']]!=['activator_1','activator_2','base','wash']:raise ActionRejected('PRECONDITION_FAILED')
   for i,s in enumerate(stocks):
    mm=s['retained_increment_kg_mol']+a['fmoc_increment_kg_mol'] if i<27 else (a['base_molar_mass_kg_mol'] if i==29 else s['retained_increment_kg_mol'])
    if s['concentration_mol_m3']*mm>s['density_kg_m3'] or (i<27 and not s['identity'] and s['concentration_mol_m3']!=0) or (i==30 and s['concentration_mol_m3']!=0):raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);self.stocks=copy.deepcopy(stocks);self.temp_k=a['initial_temperature_k'];self.target_k=self.temp_k;self.input_mass=sum(s['volume_m3']*s['density_kg_m3'] for s in stocks);self.initial_aa=self._aa()[0];out={'configured':True}
  elif capability=='kinetics.configure':
   if self.kinetics or self.operation_id or not self.plant:raise ActionRejected('PRECONDITION_FAILED')
   self.kinetics=copy.deepcopy(a);out={'configured':True}
  elif capability=='queue.configure':
   if not self.plant or self.queue or self.operation_id or self.cover<.079 or self.lift<.039:raise ActionRejected('PRECONDITION_FAILED')
   cap={'none':1,'HT4':4,'HT12':12}[self.plant['queue_module']];jobs=a['jobs'];ids=[j['resin_id'] for j in jobs]
   if not 1<=len(jobs)<=cap or len(set(ids))!=len(ids) or any(not x for x in ids):raise ActionRejected('INVALID_ARGUMENT')
   available={s['identity'] for s in self.stocks[:27] if s['concentration_mol_m3']>0}
   for j in jobs:
    seq=j['sequence_ntoc'];n=j['resin_mass_kg']*j['loading_mol_kg']
    if not 1<=len(seq)<=500 or any(x not in AA for x in seq) or any(x not in available for x in seq[:-1]) or j['preloaded_c_terminal']!=seq[-1] or len(j['steps'])!=len(seq) or abs(n-j['scale_mol'])>max(1e-13,j['scale_mol']*1e-8) or j['resin_mass_kg']<n*self.plant['fmoc_increment_kg_mol']:raise ActionRejected('PRECONDITION_FAILED')
    for s in j['steps']:
     if type(s['wash_cycles']) is not int or not 1<=s['wash_cycles']<=10:raise ActionRejected('INVALID_ARGUMENT')
   if self.plant['hold_up_m3']+max(a['base_dose_m3'],a['wash_dose_m3'],a['aa_dose_m3']+2*a['activator_dose_m3'])>self.plant['reactor_capacity_m3']:raise ActionRejected('PRECONDITION_FAILED')
   plan=self._compile(a);self.queue=copy.deepcopy(a);self.plan=plan
   for j in jobs:
    n=j['scale_mol'];self.resins.append(dict(id=j['resin_id'],sequence=j['sequence_ntoc'],site=n,mass=j['resin_mass_kg'],free=0.,protected=n,correct_free=0.,correct_protected=n,bound=[0.]*20,done=False,removed=False));self.input_mass+=j['resin_mass_kg']
   out=dict(queued=len(jobs),attachment_events=['host_attach:'+j['resin_id'] for j in jobs])
  elif capability=='acquisition.start':
   if not self.powered or self.cover>1e-8 or self.lift>1e-8 or not self.plant or not self.kinetics or not self.queue or self.cursor>=len(self.plan) or self.phase=='failed':raise ActionRejected('PRECONDITION_FAILED')
   self.phase='running';self.fault='';self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('PRECONDITION_FAILED')
   self.phase='cancelled';out={'phase':self.phase}
  elif capability=='synthesis.measure':return self._measure()
  elif capability=='history.read':return dict(records=copy.deepcopy(self.history),truncated=self.history_truncated,evidence='conditional_simulation_stage_events; not measured CEM logs')
  elif capability=='product.read':
   r=self.resins[a['job_index']];return dict(resin_id=r['id'],sequence_ntoc=r['sequence'],bound_resin_mass_kg=0. if r['removed'] else r['mass'],site_mol=r['site'],free_site_mol=r['free'],fmoc_protected_site_mol=r['protected'],conditional_correct_free_mol=r['correct_free'],conditional_correct_protected_mol=r['correct_protected'],conditional_full_length_fraction=r['correct_free']/r['site'] if r['done'] else None,true_purity_fraction=None,isolated_yield_fraction=None,ready_for_cleavage=r['done'] and not r['removed'],evidence='conditional expected correct-chain population; not identified purity; side-chain deprotection/cleavage absent')
  elif capability=='reference.lookup':
   t=self.templates.get(a['substance_id']);idx=a['row_index']
   if not t or not 0<=idx<len(t['values']):raise ActionRejected('TEMPLATE_RANGE_UNAVAILABLE')
   f=t['values'][idx];return dict(substance_id=t['substance_id'],sequence_ntoc=t['sequence_ntoc'],row_index=idx,residue_count=f['residue_count'],**{k:f['values'][k] for k in ['first_area','first_height','first_width','first_diff']},width_minus_height=f['width_minus_height'],template_source=TEMPLATE_REL,source_url=t['source'],raw_sha256=t['sha256'],evidence=t['evidence'])
  elif capability=='resin.unload':
   r=self.resins[a['job_index']]
   if self.phase!='succeeded' or not r['done'] or r['removed'] or r['id']!=a['resin_id'] or self.cover<.079 or self.lift<.039:raise ActionRejected('PRECONDITION_FAILED')
   r['removed']=True;self.removed_mass+=r['mass'];self.removed_aa=[x+y for x,y in zip(self.removed_aa,r['bound'])];out=dict(unloaded=True,attachment_event='host_release:'+r['id'])
  self.revision+=1;return copy.deepcopy(out)
 def _record(self,e):
  p=dict(time_s=self.time_s,job_index=e['job'],residue_count=e['residue'],stage=e['kind'],temperature_k=self.temp_k,waste_volume_m3=self.waste['volume'],phase=self.phase)
  if len(self.history)<10000:self.history.append(p)
  else:self.history_truncated=True
 def _fill(self,source,v):
  s=self.stocks[source];p=self.pool;n=v*s['concentration_mol_m3'];s['volume_m3']=max(0.,s['volume_m3']-v);p['volume']+=v;p['mass']+=v*s['density_kg_m3']
  if source<27:p['aa'][AA.index(s['identity'])]+=n
  elif source==29:p['base']+=n
  elif source<29:p['act'][source-27]+=n
 def _drain(self,v):
  p=self.pool;f=min(1.,v/p['volume']);self.waste['volume']+=v;self.waste['mass']+=p['mass']*f;p['mass']*=1-f
  for i in range(20):self.waste['aa'][i]+=p['aa'][i]*f;p['aa'][i]*=1-f
  p['base']*=1-f;p['act']=[x*(1-f) for x in p['act']];p['volume']=max(0.,p['volume']-v)
 def _react(self,e,dt):
  p=self.pool;r=self.resins[e['job']];k=self.kinetics;v=max(p['volume'],1e-20);thermal=math.exp(-k['activation_energy_j_mol']/8.314462618*(1/self.temp_k-1/k['reference_temperature_k']));fmoc=self.plant['fmoc_increment_kg_mol']
  if e['kind']=='deprotect':
   f=-math.expm1(-k['deprotect_k_s_inv']*thermal*min(1.,p['base']/v/k['base_reference_mol_m3'])*dt);x=r['protected']*f;cx=r['correct_protected']*f;r['protected']-=x;r['free']+=x;r['correct_protected']-=cx;r['correct_free']+=cx;r['mass']-=x*fmoc;p['mass']+=x*fmoc
  elif e['kind']=='couple':
   i=e['aa'];old=r['free'];act=min(1.,min(p['act'])/v/k['activation_reference_mol_m3']);carry=sum(n for ii,n in enumerate(p['aa']) if ii!=i)/v;rate=k['coupling_k_s_inv'][i]*thermal*e['method']['sequence_factor']*act*math.exp(-k['base_penalty_m3_mol']*p['base']/v-k['carryover_penalty_m3_mol']*carry);mm=self.stocks[e['source']]['retained_increment_kg_mol']+fmoc;x=min(old*-math.expm1(-rate*dt),p['aa'][i],p['mass']/mm);cx=x*r['correct_free']/old if old>0 else 0.;r['free']-=x;r['protected']+=x;r['correct_free']-=cx;r['correct_protected']+=cx;r['bound'][i]+=x;p['aa'][i]-=x;r['mass']+=x*mm;p['mass']-=x*mm
  if e['kind'] in ('deprotect','couple','wash'):
   x=min(p['base']*-math.expm1(-k['base_evaporation_s_inv']*dt),p['mass']/self.plant['base_molar_mass_kg_mol']);dm=x*self.plant['base_molar_mass_kg_mol'];p['base']-=x;p['mass']-=dm;self.vent_mass+=dm
 def step(self,dt):
  if isinstance(dt,bool) or not isinstance(dt,(int,float)) or not math.isfinite(dt) or dt<=0:raise ValueError('finite positive dt required')
  left=float(dt)
  while left>1e-12:
   if self.phase!='running':self.time_s+=left;left=0.;break
   e=self.plan[self.cursor];self.job_index=e['job'];self.residue_count=e['residue'];self.target_k=e['temperature'];r=self.resins[e['job']]
   if not self.entered:
    self.entered=True
    if e['kind']=='couple':r['correct_protected']=0.
    self._record(e)
   kind=e['kind'];remaining=e.get('duration',0.)-self.stage_progress
   if kind=='fill':remaining=(e['volume']-self.stage_progress)/self.queue['fill_flow_m3_s']
   elif kind=='drain':remaining=max(0.,self.pool['volume']-self.plant['hold_up_m3'])/self.queue['drain_flow_m3_s']
   if remaining<=1e-10:
    if kind=='couple':r['correct_free']=0.
    if kind=='finish':r['done']=True
    self.cursor+=1;self.entered=False;self.stage_progress=0.
    if self.cursor==len(self.plan):self.phase='succeeded'
    continue
   h=min(left,.1,remaining)
   if kind=='fill':
    v=min(e['volume']-self.stage_progress,h*self.queue['fill_flow_m3_s']);s=self.stocks[e['source']]
    if v>s['volume_m3']+1e-18:self.phase='failed';self.fault='stock_empty:'+str(e['source']);continue
    self._fill(e['source'],v);self.stage_progress+=v
   elif kind=='drain':self._drain(min(h*self.queue['drain_flow_m3_s'],self.pool['volume']-self.plant['hold_up_m3']))
   else:self._react(e,h);self.stage_progress+=h
   self.temp_k=self.target_k+(self.temp_k-self.target_k)*math.exp(-h/self.plant['thermal_tau_s']);self.time_s+=h;self.elapsed_s+=h;left-=h
  self.revision+=1;return self.read()
class CemLibertyblueSession:
    """Host registers principals; public calls have no visibility override."""
    def __init__(self,host,principal_id): self._host=host; self._principal=principal_id
    @property
    def _role(self): return self._host._principals[self._principal]
    def _target(self,i):
        if i!=self._host.instance_id: raise ActionRejected('UNKNOWN_INSTANCE')

    def describe(self,*,instance_id=None,asset_type_id=None):
        if (instance_id is None)==(asset_type_id is None): raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None: self._target(instance_id)
        if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID: raise ActionRejected('UNKNOWN_INSTANCE')
        meta=export_cem_libertyblue_contract_metadata()
        return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',
          capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},
          state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})

    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id); h=self._host; vals=h.read(self._role); ids=list(vals) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids): raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in vals for k in ids): raise ActionRejected('ACCESS_DENIED')
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},
          observations={k:dict(value=copy.deepcopy(vals[k]),unit=SLOTS[k][1],quality='unavailable' if vals[k] is None else ('estimated' if SLOTS[k][2]=='derived' else 'valid'),sample_time_s=h.time_s,sample_window_s=None) for k in ids})

    def invoke(self,*,instance_id,capability_id,arguments,request_id):
        try:
            return self._invoke(instance_id=instance_id,capability_id=capability_id,arguments=arguments,request_id=request_id)
        except ActionRejected as exc:
            return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,
                status='rejected',state_revision=self._host.revision,clock={'domain':'simulation','time_s':self._host.time_s},
                result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))

    def _invoke(self,*,instance_id,capability_id,arguments,request_id):
        self._target(instance_id); h=self._host
        if not isinstance(capability_id,str) or capability_id not in CAPS: raise ActionRejected('UNKNOWN_CAPABILITY')
        if self._role=='agent' and CAPS[capability_id][5]!='agent': raise ActionRejected('ACCESS_DENIED')
        if not isinstance(request_id,str) or not request_id: raise ActionRejected('INVALID_ARGUMENT')
        validate(CAPS[capability_id][0],arguments)
        key=(self._principal,instance_id,request_id); digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
        if key in h._requests:
            old,result=h._requests[key]
            if old!=digest: raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(result)
        result=h.invoke(capability_id,**arguments); op=result.get('operation_id')
        if op: h._owners[op]=self._principal
        response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=op,
          status='accepted' if op else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None)
        h._requests[key]=(digest,copy.deepcopy(response)); return response

    def write(self,*,instance_id,state_id,value,request_id):
        self._target(instance_id)
        if state_id not in SLOTS or not SLOTS[state_id][5]: raise ActionRejected('ACCESS_DENIED')
        cap,arg=SLOTS[state_id][5]
        return self.invoke(instance_id=instance_id,capability_id=cap,arguments={arg:value},request_id=request_id)

    def poll(self,operation_id):
        h=self._host
        if operation_id!=h.operation_id or h._owners.get(operation_id)!=self._principal: raise ActionRejected('ACCESS_DENIED')
        return dict(operation_id=operation_id,status=h.phase,result=h._measure())

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])

def export_cem_libertyblue_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'CemLibertyblueSession.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key=='acquisition.start'; pre=[]
        conditions={
         'acquisition.start':('供电/闭罩/夹紧，已配置树脂队列、试剂和显式条件速率；非failed',['power.enabled','cover.lift_m','clamp.lift_m','queue.configured','kinetics.configured'],'powered and closed and configured and resumable'),
         'plant.configure':('首次打开罩和夹具，27AA+4固定外试剂身份、密度/有效浓度检查',['plant.configured','cover.lift_m','clamp.lift_m'],'not configured and open and reviewed and nitrogen_connected'),
         'queue.configure':('首次队列，预载C端树脂负载匹配5e-6至5e-3mol，逐步方法数等于序列长度',['queue.configured','cover.lift_m','clamp.lift_m'],'not configured and open and within_module_capacity and valid_loading'),
         'kinetics.configure':('已配置装置，首次运行前一次输入条件性参数',['plant.configured','kinetics.configured'],'plant_configured and not kinetics_configured and not started'),
         'resin.unload':('全队列完成且罩夹打开、身份匹配，不能重复移除',['cycle.phase','cover.lift_m','clamp.lift_m'],'succeeded and open and matching_identity and not removed')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.cem_libertyblue.CemLibertyblueSession.invoke',invocation=f'session.invoke(instance_id="libertyblue_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；逐步执行全部队列；取消保留反应阶段和物料库存' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电不回滚已经输送/收集的试剂、树脂、废液、顶空排出与位点库存。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    evolving=[k for k in slots if k.startswith('truth.') or k.startswith('cycle.')]+['temperature.target_k','temperature.indicated_k','waste.volume_m3','reactor.volume_m3','queue.active_index']
    for key in ['acquisition.start','plant.configure','queue.configure','resin.unload']:
        caps[key]['state_effects'] += [dict(state_id=k,timing='on_step' if key=='acquisition.start' else 'immediate',description_zh='显式物料与阶段变化；真实收率未标定') for k in evolving if k not in [e['state_id'] for e in caps[key]['state_effects']]]
    caps['reference.lookup']['errors'].append(dict(code='TEMPLATE_RANGE_UNAVAILABLE',meaning_zh='物质或原记录行超界',side_effects='none'))
    for key in ['cover.set_lift','clamp.set_lift']:
        caps[key]['preconditions'].append(dict(description_zh='运行中禁止机械开合；无实机冷却/泄压安全保证',state_ids=['cycle.phase'],enforced_by='runtime',check='not running'))
    mechanism=dict(component_ids=['reactor','pump','heater','aa_rack','reagent_rack','waste_port','nitrogen_inlet'],state_ids=evolving,meaning_zh='预载树脂常规洗涤SPPS期望量模型，逐阶段有限供液和残留，非完整UE-SPPS',implementation='lumped',equations=['site_mol=resin_mass_kg*loading_mol_kg; free+protected=site_mol','deprotect_extent=P*(1-exp(-k_deprotect*thermal*min(1,Cbase/Cbase_ref)*dt)); bound_Fmoc mass -> liquid','coupling_k=k_AA*sequence_factor*thermal*activation_factor*exp(-base_penalty*Cbase-carryover_penalty*CotherAA)','couple_extent=min(F*(1-exp(-k*dt)),available_AA,liquid_mass/(retained_increment+Fmoc_increment))','correct_extent=couple_extent*correct_free/free_before; failed-prefix populations leave correct tracking but never leave mass/site inventory','fill_volume=Q*dt; drain mixed pool to explicit hold_up; washes dilute all remaining tags','effective_base_evaporation=base*(1-exp(-host_k_evap*dt)); corresponding base mass -> vent; solute volume change neglected','input_mass=stock_mass+pool_mass+retained_resin_mass+waste_mass+vent_mass+removed_resin_mass','initial_supplied_AA=remaining_stock_and_pool_AA+bound_added_residues+waste_AA+removed_added_residues','T=Ttarget+(Told-Ttarget)*exp(-dt/tau); only during running; no energy balance'],parameters={},source_ids=['manufacturer_primary','manufacturer_japanese','afps_templates','simulation_assumption'],limitations=['Kinetics, sequence factors, activation/base tags and headspace loss are explicit host assumptions; AFPS peak features never set CEM yield.','Conventional washes required; no complete UE-SPPS quench/volatile-base/condensation chemistry.','Preloaded C-terminal Fmoc resin only; no first anchoring, cleavage, side-chain deprotection, purification, mass spectra or real purity.','AA inventory counts only supplied added residues; preloaded residue is included in initial resin mass, not a supplied AA stock.','Effective activator and base tags are not full reaction stoichiometry; liquid mass retains unresolved byproducts.','Wrong-residue carryover suppresses the conditional rate; actual insertions/deletion sequence distributions are not identified.','Reaction only evolves in corresponding active holds and stops on cancel/poweroff; not a real chemical stop guarantee.','Single shared liquid pool across sequential jobs; host resin exchange is an event, no fluidic transfer/robot constraint simulation.','No manufacturer-calibrated vessel capacity, temperatures, power field, pressure, gas dynamics or absolute dimensions/mass.','Correct-chain expected fraction is conditional, not measured crude purity or isolated yield.'])
    _ann.annotate_export('cem_libertyblue',caps,slots,{'conditional_washed_spps':mechanism},inputs={'plant.configure':(PLANT,EXAMPLE),'kinetics.configure':(KINETICS,KINETIC_EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'conditional_washed_spps':mechanism})

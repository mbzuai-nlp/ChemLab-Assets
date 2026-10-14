"""ÄKTA oligosynt: bounded, uncalibrated flow-through DNA synthesis model."""
from __future__ import annotations
import copy,json,math
from asset_runtime import _annotations as _ann
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT,PHASE
from asset_runtime.antonpaar_abbematadvanced import validate
ROOT=Path(__file__).resolve().parents[2];ASSET_TYPE_ID='instrument.cytiva_ktaoligosynt';CATALOG_ID='CHEM-023';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/cytiva_ktaoligosynt_oligo_library/templates.json';BASES='ACGT';TAGS=['A','C','G','T','acid','activator','cap_a','cap_b','oxidant','dmtr']
AMIDITE_PORTS=list('ACGTQXYZacguqxyz');REAGENT_PORTS=['A_Thiolation','A_CapA','A_Extra1','A_Detrit','A_ACNA7','A_Oxidation','B_DEA','B_Activator','B_ACNB3','B_CapB','B_Extra2','B_Detrit','B_ACNB7','B_Oxidation']
def arr(s,n=None):
 d={'type':'array','items':s}
 if n is not None:d.update(minItems=n,maxItems=n)
 return d
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
def integer(a,b):return {'type':'integer','minimum':a,'maximum':b,'x-unit':'1'}
V=number('m^3',0);MASS=number('kg',0);MOL=number('mol',0);FRAC=number('1',0,1);LIFT=number('m',0,.04);PRESS=number('Pa',0);FLOW=number('m^3/s',.1e-6/60,150e-6/60)
STOCK=obj({'port':TEXT,'tag':TEXT,'volume_m3':V,'density_kg_m3':number('kg/m^3',1,3000),'concentration_mol_m3':number('mol/m^3',0,10000),'retained_increment_kg_mol':number('kg/mol',0,2)})
PLANT=obj({'stocks':arr(STOCK,30),'mix_volume_m3':number('m^3',1e-8,1e-4),'return_volume_m3':number('m^3',1e-8,1e-4),'prime_density_kg_m3':number('kg/m^3',1,3000),'viscosity_pa_s':number('Pa*s',.00035,.001),'line_resistance_pa_s_m3':number('Pa*s/m^3',0,1e15),'outlet_resistance_pa_s_m3':number('Pa*s/m^3',0,1e15),'dmtr_increment_kg_mol':number('kg/mol',.001,1),'cap_increment_kg_mol':number('kg/mol',.001,1),'oxygen_increment_kg_mol':number('kg/mol',.001,1),'high_flow_kit':BOOL,'inert_gas_connected':BOOL,'gas_supply_gauge_pa':number('Pa',5e5,1e6),'recipe_reviewed':BOOL,'temperature_k':number('K',277.15,308.15)})
KINETICS=obj({'coupling_k_s_inv':arr(number('1/s',0,100),4),'detritylation_k_s_inv':number('1/s',0,100),'oxidation_k_s_inv':number('1/s',0,100),'capping_k_s_inv':number('1/s',0,100),'reference_concentration_mol_m3':number('mol/m^3',1e-6,10000),'acid_penalty_m3_mol':number('m^3/mol',0,100),'carryover_penalty_m3_mol':number('m^3/mol',0,100),'wavelengths_m':arr(number('m',190e-9,700e-9),3),'epsilon_m2_mol':arr(arr(number('m^2/mol',0,1e6),3),10),'molar_conductivity_s_m2_mol':arr(number('S*m^2/mol',0,100),10),'baseline_conductivity_s_m':number('S/m',0,100),'calibration_evidence':TEXT})
STEP=obj({'detritylate_s':number('s',.01,3600),'wash_s':number('s',.01,3600),'couple_s':number('s',.01,3600),'recirculate_s':number('s',0,3600),'oxidize_s':number('s',.01,3600),'cap_s':number('s',.01,3600),'sequence_factor':number('1',0,10)})
JOB=obj({'column_position':integer(1,5),'support_id':TEXT,'sequence_5to3':TEXT,'preloaded_3prime':TEXT,'scale_mol':number('mol',1e-5,.012),'support_mass_kg':number('kg',1e-6,10),'loading_mol_kg':number('mol/kg',.001,10),'bed_volume_m3':number('m^3',1e-8,.01),'bed_length_m':number('m',.001,1),'diameter_m':number('m',.001,.3),'porosity':number('1',.1,.9),'particle_diameter_m':number('m',1e-6,.01),'steps':arr(STEP),'final_detritylation':BOOL})
METHOD=obj({'jobs':arr(JOB),'pump_a_flow_m3_s':FLOW,'pump_b_flow_m3_s':FLOW,'waste_position':integer(1,11)})
def example_stock(i):
 port=(AMIDITE_PORTS+REAGENT_PORTS)[i];tag=(BASES[i] if i<4 else '') if i<16 else {17:'cap_a',19:'acid',21:'oxidant',23:'activator',25:'cap_b',27:'acid',29:'oxidant'}.get(i,'')
 return dict(port=port,tag=tag,volume_m3=1e-4,density_kg_m3=900.,concentration_mol_m3=100. if tag else 0.,retained_increment_kg_mol=.3 if i<4 else 0.)
EXAMPLE=dict(stocks=[example_stock(i) for i in range(30)],mix_volume_m3=1e-6,return_volume_m3=.2e-6,prime_density_kg_m3=900.,viscosity_pa_s=.0006,line_resistance_pa_s_m3=1e10,outlet_resistance_pa_s_m3=1e10,dmtr_increment_kg_mol=.30237,cap_increment_kg_mol=.04204,oxygen_increment_kg_mol=.016,high_flow_kit=False,inert_gas_connected=True,gas_supply_gauge_pa=6e5,recipe_reviewed=True,temperature_k=298.15)
KINETIC_EXAMPLE=dict(coupling_k_s_inv=[.8]*4,detritylation_k_s_inv=1.,oxidation_k_s_inv=1.,capping_k_s_inv=1.,reference_concentration_mol_m3=50.,acid_penalty_m3_mol=.05,carryover_penalty_m3_mol=.01,wavelengths_m=[260e-9,280e-9,498e-9],epsilon_m2_mol=[[10.,5.,0.] for _ in range(9)]+[[0.,0.,100.]],molar_conductivity_s_m2_mol=[.001]*9+[0.],baseline_conductivity_s_m=.001,calibration_evidence='synthetic_host_coefficients; NOT manufacturer calibration or universal DMT epsilon')
STEP_EXAMPLE=dict(detritylate_s=3.,wash_s=6.,couple_s=3.,recirculate_s=1.,oxidize_s=3.,cap_s=3.,sequence_factor=1.)
def job_example(sequence='AC',position=1):
 vol=6.3e-6;length=.08
 return dict(column_position=position,support_id='support_'+str(position),sequence_5to3=sequence,preloaded_3prime=sequence[-1],scale_mol=1e-5,support_mass_kg=2e-5,loading_mol_kg=.5,bed_volume_m3=vol,bed_length_m=length,diameter_m=math.sqrt(4*vol/math.pi/length),porosity=.4,particle_diameter_m=150e-6,steps=[copy.deepcopy(STEP_EXAMPLE) for _ in sequence],final_detritylation=True)
METHOD_EXAMPLE=dict(jobs=[job_example()],pump_a_flow_m3_s=.5e-6,pump_b_flow_m3_s=.5e-6,waste_position=1)
POINT=obj({'time_s':number('s',0),'column_position':integer(0,5),'stage':TEXT,'uv_au':arr(nullable(number('1',-6,6)),3),'conductivity_s_m':nullable(number('S/m',.001,99.999)),'system_pressure_gauge_pa':PRESS,'waste_volume_m3':V})
RESULT=obj({'phase':PHASE,'fault':TEXT,'elapsed_s':number('s',0),'column_position':integer(0,5),'stage':TEXT,'uv_au':arr(nullable(number('1',-6,6)),3),'conductivity_s_m':nullable(number('S/m',.001,99.999)),'system_pressure_gauge_pa':PRESS,'uv_pressure_gauge_pa':PRESS,'isolated_yield_fraction':{'type':'null'},'true_purity_fraction':{'type':'null'},'evidence':TEXT})
PRODUCT=obj({'support_id':TEXT,'sequence_5to3':TEXT,'retained_support_mass_kg':MASS,'site_mol':MOL,'free_site_mol':MOL,'protected_site_mol':MOL,'capped_site_mol':MOL,'unoxidized_link_mol':MOL,'conditional_full_length_fraction':nullable(FRAC),'done':BOOL,'removed':BOOL,'isolated_yield_fraction':{'type':'null'},'true_purity_fraction':{'type':'null'},'evidence':TEXT})
REFERENCE=obj({'substance_id':TEXT,'sequence_5to3':TEXT,'kind':TEXT,'row_index':integer(0,4),'temperature_k':nullable(number('K',273.15,400)),'normalized_retention_s':nullable(number('s',0)),'wavelength_m':nullable(number('m',0)),'epsilon_m2_mol':nullable(number('m^2/mol',0)),'template_source':TEXT,'source_url':TEXT,'raw_sha256':TEXT,'evidence':TEXT})
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet'],'供电；断电仅冻结条件模型','device_command','agent',{'enabled':True},['power.enabled','cycle.phase']),
 'clamp.set_lift':(obj({'lift_m':LIFT}),obj({'lift_m':LIFT}),['column_clamp'],'虚拟柱夹+Z0–40mm，厂家行程未知','device_command','agent',{'lift_m':.04},['clamp.lift_m']),
 'plant.configure':(PLANT,obj({'configured':BOOL}),['pump_a','pump_b','amidite_valve_1','solvent_valve_a','gas_box'],'首次宿主30物理入口、死体积、密度/阻力配置','configuration','evaluator',EXAMPLE,['plant.configured']),
 'kinetics.configure':(KINETICS,obj({'configured':BOOL}),['column','uv_monitor','conductivity_monitor'],'条件速率及3波长光学、电导系数；未标定','configuration','evaluator',KINETIC_EXAMPLE,['kinetics.configured']),
 'method.configure':(METHOD,obj({'queued':integer(1,5),'attachment_events':arr(TEXT)}),['column_valve','column','pump_a','pump_b'],'5柱位单柱顺序DNA方法；5到3输入反向合成','configuration','evaluator',METHOD_EXAMPLE,['method.configured','method.size']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['pump_a','pump_b','column_valve','recirculation_valve'],'启动/续作单柱逐步合成','device_command','agent',{},['cycle.phase','cycle.fault']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['pump_a','pump_b'],'取消冻结流动与本模型化学，不是实机化学停反应保证','device_command','agent',{},['cycle.phase']),
 'synthesis.measure':(obj({}),RESULT,['host_display'],'未标定在线UV/电导与表压，不返回真纯度','query','agent',{},[]),
 'history.read':(obj({}),obj({'records':arr(POINT),'truncated':BOOL,'evidence':TEXT}),['host_display'],'每阶段和1秒在线采样日志；读取不推进','query','agent',{},[]),
 'product.read':(obj({'column_position':integer(1,5)}),PRODUCT,['column'],'条件性正确链期望量；非真实纯度或收率','query','evaluator',{'column_position':1},[]),
 'reference.lookup':(obj({'substance_id':TEXT,'row_index':integer(0,4)}),REFERENCE,['host_display'],'80真实核酸后分析参考，非本机在线合成标定','query','agent',{'substance_id':'pde_r11a','row_index':0},[]),
 'support.unload':(obj({'column_position':integer(1,5),'support_id':TEXT}),obj({'unloaded':BOOL,'attachment_event':TEXT}),['column_clamp','column'],'全部完成后宿主释放载体；不创建抓取约束','configuration','evaluator',{'column_position':1,'support_id':'support_1'},['truth.removed_mass_kg']),
}
SLOTS={}
def slot(k,s,u,o='derived',v='agent',c='host_display',w=None):SLOTS[k]=(s,u,o,v,[c],w)
slot('power.enabled',BOOL,'1','setpoint',c='power_inlet',w=('power.set','enabled'));slot('clamp.lift_m',LIFT,'m','scene',c='column_clamp',w=('clamp.set_lift','lift_m'))
for k,s,u in [('plant.configured',BOOL,'1'),('kinetics.configured',BOOL,'1'),('method.configured',BOOL,'1'),('method.size',integer(0,5),'1'),('cycle.phase',PHASE,'1'),('cycle.fault',TEXT,'1'),('cycle.elapsed_s',number('s',0),'s'),('cycle.stage',TEXT,'1'),('column.position',integer(0,5),'1'),('column.valve_angle_rad',number('rad',0,2.5),'rad'),('recirculation.enabled',BOOL,'1'),('recirculation.valve_angle_rad',number('rad',0,math.pi/2),'rad'),('waste.volume_m3',V,'m^3'),('system.pressure_gauge_pa',PRESS,'Pa'),('uv.pressure_gauge_pa',PRESS,'Pa'),('conductivity.indicated_s_m',nullable(number('S/m',.001,99.999)),'S/m')]:slot(k,s,u)
for i in range(3):slot('uv.channel_'+str(i)+'_au',nullable(number('1',-6,6)),'1')
for k in ['input','retained','waste','removed']:slot('truth.'+k+'_mass_kg',MASS,'kg','process_truth','evaluator','column')
for k in ['initial','remaining','bound','waste','removed']:slot('truth.'+k+'_monomer_mol',arr(MOL,4),'mol','process_truth','evaluator','column')
slot('truth.stock_volumes_m3',arr(V,30),'m^3','process_truth','evaluator','amidite_valve_1')
class CytivaKtaoligosyntRuntime:
 def __init__(self,instance_id='oligosynt_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.lift=0.;self.plant=None;self.kinetics=None;self.method=None;self.phase='idle';self.fault='';self.elapsed_s=0.;self.operation_id=None;self._operation_count=0;self.stocks=[];self.columns={};self.plan=[];self.cursor=0;self.progress=0.;self.entered=False;self.position=0;self.recirculating=False;self.pools={};self.wastes=[self._pool(0,0) for _ in range(11)];self.input_mass=0.;self.removed_mass=0.;self.initial_monomer=[0.]*4;self.removed_monomer=[0.]*4;self.pressure=0.;self.uv_pressure=0.;self.history=[];self.history_truncated=False;self.next_sample=0.;self.templates={t['substance_id']:t for t in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 @staticmethod
 def _pool(v,rho):return dict(volume=v,mass=v*rho,tags=[0.]*10)
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return CytivaKtaoligosyntSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _inventory(self):
  return dict(input=self.input_mass,retained=sum(s['volume_m3']*s['density_kg_m3'] for s in self.stocks)+sum(p['mass'] for p in self.pools.values())+sum(r['mass'] for r in self.columns.values() if not r['removed']),waste=sum(w['mass'] for w in self.wastes),removed=self.removed_mass)
 def _monomer(self):
  remaining=[sum(p['tags'][i] for p in self.pools.values()) for i in range(4)];bound=[sum(r['bound'][i] for r in self.columns.values() if not r['removed']) for i in range(4)];waste=[sum(w['tags'][i] for w in self.wastes) for i in range(4)]
  for s in self.stocks:
   if s['tag'] in BASES and s['tag']:remaining[BASES.index(s['tag'])]+=s['volume_m3']*s['concentration_mol_m3']
  return remaining,bound,waste
 def _signals(self):
  if not self.powered or not self.kinetics or not self.pools:return [None]*3,None
  p=self.pools['uv'];k=self.kinetics;uv=[round(min(6.,max(-6.,.002*sum(n/p['volume']*eps[j] for n,eps in zip(p['tags'],k['epsilon_m2_mol'])))),6) for j in range(3)];p=self.pools['conductivity'];c=k['baseline_conductivity_s_m']+sum(n/p['volume']*x for n,x in zip(p['tags'],k['molar_conductivity_s_m2_mol']))
  return uv,c if .001<=c<=99.999 else None
 def _values(self):
  uv,cond=self._signals();rem,bound,waste=self._monomer();v={'power.enabled':self.powered,'clamp.lift_m':self.lift,'plant.configured':self.plant is not None,'kinetics.configured':self.kinetics is not None,'method.configured':self.method is not None,'method.size':len(self.columns),'cycle.phase':self.phase,'cycle.fault':self.fault,'cycle.elapsed_s':self.elapsed_s,'cycle.stage':self.plan[self.cursor]['kind'] if self.cursor<len(self.plan) else ('complete' if self.plan else 'idle'),'column.position':self.position,'column.valve_angle_rad':self.position*.5,'recirculation.enabled':self.recirculating,'recirculation.valve_angle_rad':math.pi/2 if self.recirculating else 0.,'waste.volume_m3':sum(w['volume'] for w in self.wastes),'system.pressure_gauge_pa':self.pressure,'uv.pressure_gauge_pa':self.uv_pressure,'conductivity.indicated_s_m':cond,'truth.initial_monomer_mol':self.initial_monomer,'truth.remaining_monomer_mol':rem,'truth.bound_monomer_mol':bound,'truth.waste_monomer_mol':waste,'truth.removed_monomer_mol':self.removed_monomer,'truth.stock_volumes_m3':[s['volume_m3'] for s in self.stocks] if self.stocks else [0.]*30}
  v.update({'uv.channel_'+str(i)+'_au':x for i,x in enumerate(uv)});v.update({'truth.'+k+'_mass_kg':x for k,x in self._inventory().items()});return v
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('evaluator','debug')})
 def debug_snapshot(self):return dict(state=self.read('evaluator'),columns=copy.deepcopy(self.columns),pools=copy.deepcopy(self.pools),wastes=copy.deepcopy(self.wastes),cursor=self.cursor,progress=self.progress)
 def _measure(self):
  uv,c=self._signals();return dict(phase=self.phase,fault=self.fault,elapsed_s=self.elapsed_s,column_position=self.position,stage=self._values()['cycle.stage'],uv_au=uv,conductivity_s_m=c,system_pressure_gauge_pa=self.pressure,uv_pressure_gauge_pa=self.uv_pressure,isolated_yield_fraction=None,true_purity_fraction=None,evidence='synthetic_host_parameter_online_model; NOT calibrated Cytiva data')
 def _compile(self,a):
  plan=[];qa=a['pump_a_flow_m3_s'];qb=a['pump_b_flow_m3_s']
  for job in a['jobs']:
   seq=job['sequence_5to3'][::-1]
   def add(kind,t,feeds,ri,s,recirc=False):plan.append(dict(kind=kind,duration=t,feeds=feeds,position=job['column_position'],residue=ri,method=s,recirc=recirc))
   for ri,s in enumerate(job['steps'][:-1]):
    idx=BASES.index(seq[ri+1]);add('detritylate',s['detritylate_s'],[(19,qa)],ri,s);add('wash',s['wash_s'],[(20,qa),(24,qb)],ri,s);add('couple',s['couple_s'],[(idx,qa),(23,qb)],ri,s)
    if s['recirculate_s']>0:add('recirculate',s['recirculate_s'],[],ri,s,True)
    add('oxidize',s['oxidize_s'],[(21,qa)],ri,s);add('cap',s['cap_s'],[(17,qa),(25,qb)],ri,s);add('wash',s['wash_s'],[(20,qa),(24,qb)],ri,s)
   s=job['steps'][-1]
   if job['final_detritylation']:add('detritylate',s['detritylate_s'],[(19,qa)],len(seq)-1,s);add('wash',s['wash_s'],[(20,qa),(24,qb)],len(seq)-1,s)
   add('finish',0.,[],len(seq)-1,s)
  return plan
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if 'column_position' in a and (type(a['column_position']) is not int or a['column_position'] not in self.columns):raise ActionRejected('INVALID_ARGUMENT')
  if 'row_index' in a and type(a['row_index']) is not int:raise ActionRejected('INVALID_ARGUMENT')
  if self.phase=='running' and CAPS[capability][4]!='query' and capability not in ('power.set','acquisition.stop'):raise ActionRejected('BUSY')
  if capability=='power.set':
   self.powered=a['enabled']
   if not self.powered and self.phase=='running':self.phase='cancelled'
   out=dict(a)
  elif capability=='clamp.set_lift':self.lift=a['lift_m'];out=dict(a)
  elif capability=='plant.configure':
   if self.plant or self.lift<.039 or not a['recipe_reviewed'] or not a['inert_gas_connected']:raise ActionRejected('PRECONDITION_FAILED')
   for i,s in enumerate(a['stocks']):
    expected=example_stock(i)
    if s['port']!=expected['port'] or s['tag']!=expected['tag'] or (not s['tag'] and s['concentration_mol_m3']!=0) or (i<4 and s['retained_increment_kg_mol']<=0):raise ActionRejected('PRECONDITION_FAILED')
    mm=s['retained_increment_kg_mol']+a['dmtr_increment_kg_mol'] if i<4 else max(a['cap_increment_kg_mol'],a['oxygen_increment_kg_mol'])
    if s['concentration_mol_m3']*mm>s['density_kg_m3']:raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);self.stocks=copy.deepcopy(a['stocks']);self.pools={k:self._pool(v,a['prime_density_kg_m3']) for k,v in [('mix',a['mix_volume_m3']),('return',a['return_volume_m3']),('conductivity',22e-9),('uv',11e-9)]};self.input_mass=sum(s['volume_m3']*s['density_kg_m3'] for s in self.stocks)+sum(p['mass'] for p in self.pools.values());self.initial_monomer=self._monomer()[0];out={'configured':True}
  elif capability=='kinetics.configure':
   if not self.plant or self.kinetics or self.operation_id or any(abs(w/1e-9-round(w/1e-9))>1e-6 for w in a['wavelengths_m']) or len(set(a['wavelengths_m']))!=3:raise ActionRejected('PRECONDITION_FAILED')
   self.kinetics=copy.deepcopy(a);out={'configured':True}
  elif capability=='method.configure':
   if not self.plant or self.method or self.lift<.039:raise ActionRejected('PRECONDITION_FAILED')
   jobs=a['jobs'];positions=[j['column_position'] for j in jobs];ids=[j['support_id'] for j in jobs]
   if not 1<=len(jobs)<=5 or len(set(positions))!=len(jobs) or len(set(ids))!=len(ids) or any(not x for x in ids) or type(a['waste_position']) is not int:raise ActionRejected('INVALID_ARGUMENT')
   if a['pump_a_flow_m3_s']>70e-6/60 and not self.plant['high_flow_kit']:raise ActionRejected('PRECONDITION_FAILED')
   for j in jobs:
    seq=j['sequence_5to3'];n=j['scale_mol'];geo=math.pi*j['diameter_m']**2/4*j['bed_length_m']
    if type(j['column_position']) is not int or not 1<=j['column_position']<=5 or not 1<=len(seq)<=500 or any(x not in BASES for x in seq) or j['preloaded_3prime']!=seq[-1] or len(j['steps'])!=len(seq) or abs(j['support_mass_kg']*j['loading_mol_kg']-n)>max(1e-13,n*1e-8) or abs(geo-j['bed_volume_m3'])>geo*1e-6 or j['support_mass_kg']<n*self.plant['dmtr_increment_kg_mol']:raise ActionRejected('PRECONDITION_FAILED')
   plan=self._compile(a);self.method=copy.deepcopy(a);self.plan=plan
   for j in jobs:
    n=j['scale_mol'];pos=j['column_position'];self.columns[pos]=dict(job=copy.deepcopy(j),mass=j['support_mass_kg'],site=n,free=0.,protected=n,capped=0.,correct_free=0.,correct_protected=n,pending=0.,unoxidized=0.,bound=[0.]*4,done=False,removed=False);self.pools['column_'+str(pos)]=self._pool(j['bed_volume_m3']*j['porosity'],self.plant['prime_density_kg_m3']);self.input_mass+=j['support_mass_kg']+self.pools['column_'+str(pos)]['mass']
   out=dict(queued=len(jobs),attachment_events=['host_attach:'+j['support_id'] for j in jobs])
  elif capability=='acquisition.start':
   if not self.powered or self.lift>1e-8 or not self.plant or not self.kinetics or not self.method or self.cursor>=len(self.plan) or self.phase=='failed':raise ActionRejected('PRECONDITION_FAILED')
   self.phase='running';self.fault='';self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('PRECONDITION_FAILED')
   self.phase='cancelled';out={'phase':self.phase}
  elif capability=='synthesis.measure':return self._measure()
  elif capability=='history.read':return dict(records=copy.deepcopy(self.history),truncated=self.history_truncated,evidence='conditional simulation; 1s/transition samples; NOT UNICORN measured logs')
  elif capability=='product.read':
   r=self.columns[a['column_position']];j=r['job'];correct=r['correct_free'] if j['final_detritylation'] else r['correct_protected'];return dict(support_id=j['support_id'],sequence_5to3=j['sequence_5to3'],retained_support_mass_kg=0. if r['removed'] else r['mass'],site_mol=r['site'],free_site_mol=r['free'],protected_site_mol=r['protected'],capped_site_mol=r['capped'],unoxidized_link_mol=r['unoxidized'],conditional_full_length_fraction=correct/r['site'] if r['done'] else None,done=r['done'],removed=r['removed'],isolated_yield_fraction=None,true_purity_fraction=None,evidence='conditional expected correct-chain population; no actual sequence identification or calibrated yield')
  elif capability=='reference.lookup':
   t=self.templates.get(a['substance_id']);idx=a['row_index']
   if not t or not 0<=idx<len(t['values']):raise ActionRejected('TEMPLATE_RANGE_UNAVAILABLE')
   f=t['values'][idx];return dict(substance_id=t['substance_id'],sequence_5to3=t['sequence_5to3'],kind=t['kind'],row_index=idx,temperature_k=f.get('temperature_k'),normalized_retention_s=f.get('normalized_retention_s'),wavelength_m=f.get('wavelength_m'),epsilon_m2_mol=f.get('epsilon_m2_mol'),template_source=TEMPLATE_REL,source_url=t['source'],raw_sha256=t['sha256'],evidence=t['evidence'])
  elif capability=='support.unload':
   r=self.columns[a['column_position']]
   if self.phase!='succeeded' or not r['done'] or r['removed'] or self.lift<.039 or r['job']['support_id']!=a['support_id']:raise ActionRejected('PRECONDITION_FAILED')
   r['removed']=True;self.removed_mass+=r['mass'];self.removed_monomer=[x+y for x,y in zip(self.removed_monomer,r['bound'])];out=dict(unloaded=True,attachment_event='host_release:'+a['support_id'])
  self.revision+=1;return copy.deepcopy(out)
 def _record(self):
  uv,c=self._signals();p=dict(time_s=self.time_s,column_position=self.position,stage=self._values()['cycle.stage'],uv_au=uv,conductivity_s_m=c,system_pressure_gauge_pa=self.pressure,waste_volume_m3=sum(w['volume'] for w in self.wastes))
  if len(self.history)<10000:self.history.append(p)
  else:self.history_truncated=True
 def _hydraulics(self,e,q):
  j=self.columns[e['position']]['job'];p=self.plant;eps=j['porosity'];v=q/(math.pi*j['diameter_m']**2/4);d=j['particle_diameter_m'];rho=p['prime_density_kg_m3'];drop=j['bed_length_m']*(150*p['viscosity_pa_s']*(1-eps)**2/(eps**3*d*d)*v+1.75*rho*(1-eps)/(eps**3*d)*v*v);post=0. if e['recirc'] else p['outlet_resistance_pa_s_m3']*q;return post+drop+p['line_resistance_pa_s_m3']*q,post
 def _transport(self,e,q,h):
  keys=['mix','column_'+str(e['position']),'conductivity','uv','return'];ps=[self.pools[k] for k in keys];old=[dict(mass=p['mass']*q*h/p['volume'],tags=[n*q*h/p['volume'] for n in p['tags']]) for p in ps]
  if e['recirc']:incoming=old[-1]
  else:
   incoming=dict(mass=0.,tags=[0.]*10)
   for i,f in e['feeds']:
    s=self.stocks[i];vol=f*h;s['volume_m3']=max(0.,s['volume_m3']-vol);incoming['mass']+=vol*s['density_kg_m3']
    if s['tag']:incoming['tags'][TAGS.index(s['tag'])]+=vol*s['concentration_mol_m3']
   w=self.wastes[self.method['waste_position']-1];w['volume']+=q*h;w['mass']+=old[-1]['mass'];w['tags']=[x+y for x,y in zip(w['tags'],old[-1]['tags'])]
  for p,out,inc in zip(ps,old,[incoming]+old[:-1]):p['mass']+=inc['mass']-out['mass'];p['tags']=[max(0.,x+y-z) for x,y,z in zip(p['tags'],inc['tags'],out['tags'])]
 def _react(self,e,h):
  r=self.columns[e['position']];p=self.pools['column_'+str(e['position'])];tags=p['tags'];v=p['volume'];k=self.kinetics;ref=k['reference_concentration_mol_m3'];dmtr=self.plant['dmtr_increment_kg_mol'];kind=e['kind']
  def extent(pop,rate,tag):return pop*-math.expm1(-rate*min(1.,tags[tag]/v/ref)*h)
  if kind=='detritylate':
   old=r['protected'];x=extent(old,k['detritylation_k_s_inv'],4);cx=x*r['correct_protected']/old if old else 0.;r['protected']-=x;r['free']+=x;r['correct_protected']-=cx;r['correct_free']+=cx;r['mass']-=x*dmtr;p['mass']+=x*dmtr;tags[9]+=x
  elif kind in ('couple','recirculate'):
   i=BASES.index(r['job']['sequence_5to3'][::-1][e['residue']+1]);old=r['free'];rate=k['coupling_k_s_inv'][i]*e['method']['sequence_factor']*min(1.,tags[5]/v/ref)*math.exp(-k['acid_penalty_m3_mol']*tags[4]/v-k['carryover_penalty_m3_mol']*sum(tags[j] for j in range(4) if j!=i)/v);mm=self.stocks[i]['retained_increment_kg_mol']+dmtr;x=min(old*-math.expm1(-rate*h),tags[i],p['mass']/mm);cx=x*r['correct_free']/old if old else 0.;r['free']-=x;r['protected']+=x;r['correct_free']-=cx;r['pending']+=cx;r['unoxidized']+=x;r['bound'][i]+=x;tags[i]-=x;r['mass']+=x*mm;p['mass']-=x*mm
  elif kind=='oxidize':
   old=r['unoxidized'];mm=self.plant['oxygen_increment_kg_mol'];x=min(extent(old,k['oxidation_k_s_inv'],8),tags[8],p['mass']/mm);cx=x*r['pending']/old if old else 0.;r['unoxidized']-=x;r['pending']-=cx;r['correct_protected']+=cx;tags[8]-=x;r['mass']+=x*mm;p['mass']-=x*mm
  elif kind=='cap':
   old=r['free'];mm=self.plant['cap_increment_kg_mol'];rate=k['capping_k_s_inv']*min(1.,tags[7]/v/ref);x=min(extent(old,rate,6),tags[6],tags[7],p['mass']/mm);cx=x*r['correct_free']/old if old else 0.;r['free']-=x;r['capped']+=x;r['correct_free']-=cx;tags[6]-=x;tags[7]-=x;r['mass']+=x*mm;p['mass']-=x*mm
 def step(self,dt):
  if isinstance(dt,bool) or not isinstance(dt,(int,float)) or not math.isfinite(dt) or dt<=0:raise ValueError('finite positive dt required')
  left=float(dt)
  while left>1e-12:
   if self.phase!='running':self.time_s+=left;break
   e=self.plan[self.cursor];r=self.columns[e['position']];self.position=e['position'];self.recirculating=e['recirc']
   if not self.entered:
    self.entered=True
    if e['kind']=='couple':r['correct_protected']=0.;r['pending']=0.
    if e['kind']=='cap':r['pending']=0.
    self._record()
   remaining=e['duration']-self.progress
   if remaining<=1e-10:
    if e['kind']=='oxidize':r['correct_free']=0.
    if e['kind']=='finish':r['done']=True
    self.cursor+=1;self.progress=0.;self.entered=False
    if self.cursor==len(self.plan):self.phase='succeeded';self.recirculating=False;self.pressure=0.;self.uv_pressure=0.
    continue
   q=self.method['pump_b_flow_m3_s'] if e['recirc'] else sum(f for _,f in e['feeds']);pressure,uvp=self._hydraulics(e,q)
   if pressure>2.5e6 or uvp>2e6:self.phase='failed';self.fault='pressure_limit';continue
   h=min(left,.05,remaining,.2*min(self.pools[k]['volume'] for k in ['mix','column_'+str(e['position']),'conductivity','uv','return'])/q)
   empty=False
   for i,f in e['feeds']:
    if self.stocks[i]['volume_m3']<=1e-18:empty=True;break
    h=min(h,self.stocks[i]['volume_m3']/f)
   if empty:self.phase='failed';self.fault='stock_empty';continue
   self.pressure=pressure;self.uv_pressure=uvp;self._transport(e,q,h);self._react(e,h);self.progress+=h;self.time_s+=h;self.elapsed_s+=h;left-=h
   if self.elapsed_s>=self.next_sample:self._record();self.next_sample=self.elapsed_s+1.
  self.revision+=1;return self.read()
class CytivaKtaoligosyntSession:
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
        meta=export_cytiva_ktaoligosynt_contract_metadata()
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

def export_cytiva_ktaoligosynt_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'CytivaKtaoligosyntSession.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key=='acquisition.start'; pre=[]
        conditions={
         'acquisition.start':('供电、柱夹闭合、方法和条件速率已配置，未失败且仍有步骤',['power.enabled','clamp.lift_m','method.configured','kinetics.configured'],'powered and closed and configured and resumable'),
         'plant.configure':('首次开夹；30入口固定身份、密度浓度合理、干燥惰性气体条件经宿主审查',['plant.configured','clamp.lift_m'],'not configured and open and reviewed and gas_connected'),
         'method.configure':('首次，最多5个不重复柱位/载体，负载和几何一致；仅预载常规DNA',['method.configured','clamp.lift_m'],'not configured and open and valid_DNA_loading_geometry_ports'),
         'kinetics.configure':('装置已配置、首次运行前；3个不重复1nm网格波长',['plant.configured','kinetics.configured'],'plant_configured and not started and valid_wavelengths'),
         'support.unload':('全部完成后开夹，载体身份一致且未释放',['cycle.phase','clamp.lift_m'],'all_done and open and matching_identity and not removed')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.cytiva_ktaoligosynt.CytivaKtaoligosyntSession.invoke',invocation=f'session.invoke(instance_id="oligosynt_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；五柱位逐个执行DNA方法；取消保留反应阶段和物料库存' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电不回滚已经输送/收集的试剂、载体、管内残液、废液和未氧化链节库存。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    evolving=[k for k in slots if k.startswith(('truth.','cycle.','column.','recirculation.','uv.'))]+['system.pressure_gauge_pa','conductivity.indicated_s_m','waste.volume_m3']
    for key in ['acquisition.start','plant.configure','method.configure','support.unload']:
        caps[key]['state_effects'] += [dict(state_id=k,timing='on_step' if key=='acquisition.start' else 'immediate',description_zh='保守流动及条件化学；非实机标定') for k in evolving if k not in [e['state_id'] for e in caps[key]['state_effects']]]
    caps['clamp.set_lift']['preconditions'].append(dict(description_zh='运行中禁止开夹；不代表实机泄压保证',state_ids=['cycle.phase'],enforced_by='runtime',check='not running'))
    caps['reference.lookup']['errors'].append(dict(code='TEMPLATE_RANGE_UNAVAILABLE',meaning_zh='物质或原始记录索引不可用',side_effects='none'))
    mechanism=dict(component_ids=['pump_a','pump_b','column','column_valve','recirculation_valve','uv_monitor','conductivity_monitor','waste_valve'],state_ids=evolving,meaning_zh='流经单混合柱的预载DNA条件模型，五柱位顺序执行',implementation='lumped',equations=['flow-through mass/tags: dInventory=upstream_outflow-own_outflow; old-state conservative transport, CFL<=0.2','normal feed -> mix -> selected column -> conductivity22uL -> UV11uL -> return -> selected waste','recirculation: return -> pumpB -> mix; external feed=0 and waste=0','free+DMTr_protected+capped=initial_sites; sites=support_mass*loading','couple_extent=min(F*(1-exp(-k_i*sequence_factor*activator_factor*exp(-acid_penalty*Cacid-carryover_penalty*Cother)*dt)),available_monomer,available_liquid_mass/mm)','detritylate: bound_DMTr mass -> liquid; couple/oxidize/cap: explicitly retained mass from liquid -> support','unoxidized_links accumulate with coupling and deplete with oxidation; correct-chain pending expectation is separate and may be discarded but physical link inventory is never reset','input_mass=stocks+all_pools+retained_supports+waste+removed_supports','initial_supplied_monomer=remaining+bound_added_residues+waste+removed_added_residues','Ergun bed dP=L*(150*mu*(1-eps)^2/(eps^3*d^2)*v+1.75*rho*(1-eps)/(eps^3*d)*v^2); system gauge P=bed dP+host line R*Q+outlet gauge P','UV=A=0.002m*sum(host_epsilon_m2_mol*C_mol_m3); conductivity=host_baseline+sum(host_lambda*C)'],parameters={},source_ids=['manufacturer_primary','simulation_assumption'],limitations=['Only preloaded 3prime-DMTr ordinary DNA, no RNA, modified monomers, thiolation, DEA, first anchoring, cleavage or deprotection/purification.','5 independent loaded supports, only one selected at a time; static unselected column liquid retains inventory.','One mixed cell per bed, downflow only; no spatial distribution/upflow/bypass/manual valve operation and no individual 30 inlet deadlegs.','Kinetic coefficients, sequence factors, chemical concentrations, transfer increments and online optical/conductivity coefficients are explicit host assumptions, not manufacturer-calibrated parameters.','Correct-chain expected fraction is not purity, isolated yield or an identified sequence distribution; residual/wrong monomers inhibit only, not explicit wrong-base incorporation.','Activator/acid/cap tags are effective concentrations, not a complete elementary reaction network; unresolved byproduct mass remains in liquid.','Incompressible fixed liquid volumes; reaction solute transfer does not change liquid volume; isothermal ambient model has no thermal/energy balance.','Gas supply connection/range checks only, no gas dynamics, water/CO2 ingress, bubble transport, pump ripple, leaks, emergency safety or real depressurization.','Cancel/poweroff freezes reaction and transport in this model; real chemistry need not stop.','System pressure25bar gauge and UV20bar gauge checked separately; host line resistance and Ergun are not measured column calibration.','72 HPLC retention templates and8 free-oligo extinction coefficients are real post-synthesis reference data, NOT50 actual Cytiva online curves or calibrated step efficiencies.','Host attach/release event does not create a robot constraint; actual motion strokes remain unknown.'])
    _ann.annotate_export('cytiva_ktaoligosynt',caps,slots,{'conditional_flow_dna':mechanism},inputs={'plant.configure':(PLANT,EXAMPLE),'kinetics.configure':(KINETICS,KINETIC_EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'conditional_flow_dna':mechanism})

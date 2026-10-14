"""Passive Vivaflow200: external hydraulic boundaries, conservative UF/DF with explicit assumptions."""
from __future__ import annotations
import copy,json,math
from asset_runtime import _annotations as _ann
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.antonpaar_abbematadvanced import validate
ROOT=Path(__file__).resolve().parents[2];ASSET_TYPE_ID='instrument.sartorius_vivaflow200';CATALOG_ID='CHEM-030';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/sartorius_vivaflow200_filtration_library/templates.json';PERFORMANCE_REL='assets/reactions/sartorius_vivaflow200_filtration_library/manufacturer_performance.json';AREA=.02;MINVOL=20e-6;R=8.31446261815324
PHASE={'type':'string','enum':['idle','running','succeeded','cancelled','failed']}
def arr(x):return {'type':'array','items':x}
def nullable(x):return {'anyOf':[x,{'type':'null'}]}
MATERIAL={'type':'string','enum':['PES','RC']};CASSETTE=obj({'material':MATERIAL,'mwco_kg_mol':dict(number('kg/mol',2,100),enum=[2.,3.,5.,10.,30.,50.,100.]),'legacy_2023_configuration_confirmed':BOOL})
CASSETTE_EXAMPLE=dict(material='PES',mwco_kg_mol=10.,legacy_2023_configuration_confirmed=True)
PLANT=obj({'external_peristaltic_pump_connected':BOOL,'ports_and_reservoirs_connected':BOOL,'membrane_flushed_wet_confirmed':BOOL,'compatible_aqueous_sample_reviewed':BOOL,'temperature_k':number('K',277.15,333.15),'buffer_volume_m3':number('m^3',0,.05),'buffer_osmolarity_mol_m3':number('mol/m^3',0,5000),'permeate_capacity_m3':number('m^3',1e-6,.06),'circuit_holdup_m3':number('m^3',5.3e-6,19e-6),'pes_permeability_m_pa_s':number('m/(Pa*s)',1e-13,1e-8),'rc_permeability_m_pa_s':number('m/(Pa*s)',1e-13,1e-8),'channel_resistance_pa_s_m3':number('Pa*s/m^3',1e8,1e11),'permeate_backpressure_pa':number('Pa',0,5e4),'film_transfer_m_s':number('m/s',1e-6,.001),'sieving_exponent':number('1',1,12),'fouling_coefficient_m2_kg':number('m^2/kg',0,1e5),'adsorption_rate_s_inv':number('1/s',0,.01),'adsorption_capacity_kg':number('kg',0,.001),'max_concentration_kg_m3':number('kg/m^3',1,500),'coefficient_evidence':TEXT})
EXAMPLE=dict(external_peristaltic_pump_connected=True,ports_and_reservoirs_connected=True,membrane_flushed_wet_confirmed=True,compatible_aqueous_sample_reviewed=True,temperature_k=293.15,buffer_volume_m3=.005,buffer_osmolarity_mol_m3=0.,permeate_capacity_m3=.01,circuit_holdup_m3=10e-6,pes_permeability_m_pa_s=8e-11,rc_permeability_m_pa_s=5e-11,channel_resistance_pa_s_m3=1e10,permeate_backpressure_pa=0.,film_transfer_m_s=5e-5,sieving_exponent=4.,fouling_coefficient_m2_kg=30.,adsorption_rate_s_inv=1e-5,adsorption_capacity_kg=1e-5,max_concentration_kg_m3=150.,coefficient_evidence='synthetic_host_permeability_sieving_film_fouling_adsorption_not_manufacturer_calibration')
METHOD=obj({'mode':{'type':'string','enum':['concentrate','diafilter']},'circulation_m3_s':number('m^3/s',200e-6/60,400e-6/60),'tmp_pa':number('Pa',1e3,4e5),'target_retentate_m3':number('m^3',MINVOL,.0025),'target_diavolumes':number('1',.1,10),'timeout_s':number('s',1,21600)})
METHOD_EXAMPLE=dict(mode='concentrate',circulation_m3_s=300e-6/60,tmp_pa=275000.,target_retentate_m3=.0001,target_diavolumes=5.,timeout_s=10000.)
SOLUTE=obj({'substance_id':TEXT,'mass_kg':number('kg',1e-12,.05)})
SAMPLE=obj({'sample_id':TEXT,'volume_m3':number('m^3',.0005,.0025),'solutes':arr(SOLUTE),'freely_permeating_osmolarity_mol_m3':number('mol/m^3',0,5000),'unmodified_chain_monomer_model_confirmed':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='bsa_feed',volume_m3=.001,solutes=[dict(substance_id='uniprot_p02769',mass_kg=.001)],freely_permeating_osmolarity_mol_m3=300.,unmodified_chain_monomer_model_confirmed=True)
RESULT=obj({'phase':PHASE,'stage':TEXT,'fault':TEXT,'retentate_m3':number('m^3',0),'permeate_m3':number('m^3',0),'permeate_flow_m3_s':number('m^3/s',0),'feed_pressure_gauge_pa':number('Pa',0),'retentate_pressure_gauge_pa':number('Pa',0),'tmp_pa':number('Pa',0),'diavolumes':number('1',0),'reference_performance_ids':arr(TEXT),'evidence':TEXT,'template_source':TEXT})
POINT=obj({'time_s':number('s',0),'retentate_m3':number('m^3',0),'permeate_m3':number('m^3',0),'permeate_flow_m3_s':number('m^3/s',0),'tmp_pa':number('Pa',0),'diavolumes':number('1',0)})
PRODUCT=obj({'substance_ids':arr(TEXT),'retentate_masses_kg':arr(number('kg',0)),'permeate_masses_kg':arr(number('kg',0)),'adsorbed_masses_kg':arr(number('kg',0)),'retentate_m3':number('m^3',0),'permeate_m3':number('m^3',0),'retentate_includes_circuit_holdup':BOOL,'measured_recovered_product_kg':{'type':'null'},'evidence':TEXT,'template_source':TEXT})
REFERENCE=obj({'substance_id':TEXT,'unmodified_chain_molar_mass_kg_mol':number('kg/mol',0),'measured_sieving':{'type':'null'},'source':TEXT,'evidence':TEXT,'template_source':TEXT,'manufacturer_condition_ids':arr(TEXT)})
CAPS={
 'cassette.configure':(CASSETTE,obj({'configured':BOOL}),['membrane'],'宿主选择未使用单盒耗材SKU，非原位换膜','configuration','evaluator',CASSETTE_EXAMPLE,['cassette.configured','membrane.material','membrane.mwco_kg_mol']),
 'plant.configure':(PLANT,obj({'configured':BOOL}),['feed_port','retentate_port','permeate_port'],'外部泵/容器、湿膜与有限库存/模型系数','configuration','evaluator',EXAMPLE,['plant.configured']),
 'method.configure':(METHOD,obj({'configured':BOOL}),['feed_port','restrictor'],'外部循环/TMP边界和浓缩或恒体积换液终点；盒体无主动控制器','configuration','agent',METHOD_EXAMPLE,['method.configured','method.mode']),
 'sample.configure':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['feed_port'],'0.5–2.5L初始体系，含管路持液；蛋白链形态宿主确认','configuration','evaluator',SAMPLE_EXAMPLE,['sample.configured']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['feed_port','membrane','pressure_indicator'],'宿主外泵开始或取消后续作，非模块电源按钮','device_command','agent',{},['cycle.phase','cycle.stage']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['feed_port'],'宿主停外泵；准稳态无流动压，保留全部库存/吸附/污染','device_command','agent',{},['cycle.phase','cycle.stage']),
 'filtration.measure':(obj({}),RESULT,['pressure_indicator','permeate_port'],'宿主体积/流量读数与机械截留端压力；未标定传感器','query','agent',{},[]),
 'product.read':(obj({}),PRODUCT,['retentate_port','permeate_port'],'截留液/透过液条件组成及膜吸附真值；不等于回收后成品','query','evaluator',{},[]),
 'history.read':(obj({}),obj({'records':arr(POINT),'truncated':BOOL,'evidence':TEXT}),['permeate_port'],'每10秒浓缩或换液过程曲线','query','agent',{},[]),
 'reference.lookup':(obj({'substance_id':TEXT}),REFERENCE,['membrane'],'60真实蛋白序列链与对应厂家典型条件索引；不是60实测截留','query','agent',{'substance_id':'uniprot_p02769'},[])}
SLOTS={}
def slot(k,s,u,o='derived',v='agent',c='pressure_indicator',w=None):SLOTS[k]=(s,u,o,v,[c],w)
for k,s,u in [('cassette.configured',BOOL,'1'),('plant.configured',BOOL,'1'),('method.configured',BOOL,'1'),('sample.configured',BOOL,'1'),('membrane.material',nullable(MATERIAL),'1'),('membrane.mwco_kg_mol',nullable(number('kg/mol',2,100)),'kg/mol'),('method.mode',TEXT,'1'),('cycle.phase',PHASE,'1'),('cycle.stage',TEXT,'1'),('cycle.fault',TEXT,'1'),('cycle.elapsed_s',number('s',0),'s'),('volume.retentate_m3',number('m^3',0),'m^3'),('volume.permeate_m3',number('m^3',0),'m^3'),('flow.circulation_m3_s',number('m^3/s',0),'m^3/s'),('flow.permeate_m3_s',number('m^3/s',0),'m^3/s'),('pressure.feed_gauge_pa',number('Pa',0),'Pa'),('pressure.retentate_gauge_pa',number('Pa',0),'Pa'),('pressure.tmp_pa',number('Pa',0),'Pa'),('indicator.lift_m',number('m',0,.025),'m'),('process.diavolumes',number('1',0),'1')]:slot(k,s,u)
for k in ['initial','retentate','permeate','adsorbed']:slot('truth.'+k+'_protein_kg',number('kg',0),'kg','process_truth','evaluator','membrane')
for k in ['initial','retentate','permeate','buffer']:slot('truth.'+k+'_liquid_m3',number('m^3',0),'m^3','process_truth','evaluator','membrane')
for k in ['initial','retentate','permeate','buffer']:slot('truth.'+k+'_osmoles_mol',number('mol',0),'mol','process_truth','evaluator','membrane')
slot('truth.fouling_resistance_ratio',number('1',0),'1','process_truth','evaluator','membrane')
class SartoriusVivaflow200Runtime:
 def __init__(self,instance_id='vivaflow200_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.cassette=None;self.plant=None;self.method=None;self.sample=None;self.phase='idle';self.stage='standby';self.fault='';self.elapsed_s=0.;self.operation_id=None;self._operation_count=0;self.volume=0.;self.permeate=0.;self.buffer=0.;self.buffer_used=0.;self.mass=[];self.permeate_mass=[];self.adsorbed=[];self.mw=[];self.sieve=[];self.rf=0.;self.osmoles=0.;self.permeate_osmoles=0.;self.flux=0.;self.stalled=0.;self.history=[];self.next_record=0.;self.templates={x['substance_id']:x for x in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.performance=json.loads((ROOT/PERFORMANCE_REL).read_text())['records'];self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return SartoriusVivaflow200Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _pressures(self):
  if self.phase!='running' or not self.method:return 0.,0.,0.
  drop=self.plant['channel_resistance_pa_s_m3']*self.method['circulation_m3_s'];tmp=self.method['tmp_pa'];pperm=self.plant['permeate_backpressure_pa'];return pperm+tmp+drop/2,pperm+tmp-drop/2,tmp
 def _values(self):
  pf,pr,tmp=self._pressures();v={'cassette.configured':self.cassette is not None,'plant.configured':self.plant is not None,'method.configured':self.method is not None,'sample.configured':self.sample is not None,'membrane.material':self.cassette['material'] if self.cassette else None,'membrane.mwco_kg_mol':self.cassette['mwco_kg_mol'] if self.cassette else None,'method.mode':self.method['mode'] if self.method else '', 'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.fault':self.fault,'cycle.elapsed_s':self.elapsed_s,'volume.retentate_m3':self.volume,'volume.permeate_m3':self.permeate,'flow.circulation_m3_s':self.method['circulation_m3_s'] if self.phase=='running' else 0.,'flow.permeate_m3_s':self.flux*AREA if self.phase=='running' else 0.,'pressure.feed_gauge_pa':pf,'pressure.retentate_gauge_pa':pr,'pressure.tmp_pa':tmp,'indicator.lift_m':min(.025,pr/3.5e5*.025),'process.diavolumes':self.buffer_used/self.sample['volume_m3'] if self.sample else 0.,'truth.fouling_resistance_ratio':self.rf}
  for k,x in dict(initial=sum(x['mass_kg'] for x in self.sample['solutes']) if self.sample else 0.,retentate=sum(self.mass),permeate=sum(self.permeate_mass),adsorbed=sum(self.adsorbed)).items():v['truth.'+k+'_protein_kg']=x
  for k,x in dict(initial=(self.sample['volume_m3'] if self.sample else 0.)+(self.plant['buffer_volume_m3'] if self.plant else 0.),retentate=self.volume,permeate=self.permeate,buffer=self.buffer).items():v['truth.'+k+'_liquid_m3']=x
  for k,x in dict(initial=(self.sample['volume_m3']*self.sample['freely_permeating_osmolarity_mol_m3'] if self.sample else 0.)+(self.plant['buffer_volume_m3']*self.plant['buffer_osmolarity_mol_m3'] if self.plant else 0.),retentate=self.osmoles,permeate=self.permeate_osmoles,buffer=self.buffer*self.plant['buffer_osmolarity_mol_m3'] if self.plant else 0.).items():v['truth.'+k+'_osmoles_mol']=x
  return v
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('evaluator','debug')})
 def debug_snapshot(self):return copy.deepcopy(dict(state=self.read('evaluator'),masses=self.mass,adsorbed=self.adsorbed,sieving=self.sieve))
 def _refs(self,sid):
  category={'uniprot_p02769':'bsa','uniprot_p00698':'lysozyme'}.get(sid)
  return [r['reference_id'] for r in self.performance if r['sample_class']==category]
 def _measure(self):
  pf,pr,tmp=self._pressures();refs=[]
  if self.sample:
   for x in self.sample['solutes']:refs.extend(self._refs(x['substance_id']))
  return dict(phase=self.phase,stage=self.stage,fault=self.fault,retentate_m3=self.volume,permeate_m3=self.permeate,permeate_flow_m3_s=self.flux*AREA if self.phase=='running' else 0.,feed_pressure_gauge_pa=pf,retentate_pressure_gauge_pa=pr,tmp_pa=tmp,diavolumes=self.buffer_used/self.sample['volume_m3'] if self.sample else 0.,reference_performance_ids=refs,evidence='derived_chain_mass_with_synthetic_sieving_Darcy_film_fouling;reference_condition_ids_are_not_current_measured_performance',template_source=TEMPLATE_REL)
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if self.phase=='running' and CAPS[capability][4]!='query' and capability!='acquisition.stop':raise ActionRejected('BUSY')
  if capability=='cassette.configure':
   allowed=[3.,5.,10.,30.,50.,100.] if a['material']=='PES' else [2.,5.,10.,30.,100.]
   if self.cassette or not a['legacy_2023_configuration_confirmed'] or a['mwco_kg_mol'] not in allowed:raise ActionRejected('PRECONDITION_FAILED')
   self.cassette=copy.deepcopy(a);out={'configured':True}
  elif capability=='plant.configure':
   if self.plant or not all(a[k] for k in ['external_peristaltic_pump_connected','ports_and_reservoirs_connected','membrane_flushed_wet_confirmed','compatible_aqueous_sample_reviewed']) or not a['coefficient_evidence']:raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);self.buffer=a['buffer_volume_m3'];out={'configured':True}
  elif capability=='method.configure':
   if self.sample or not self.plant:raise ActionRejected('PRECONDITION_FAILED')
   drop=self.plant['channel_resistance_pa_s_m3']*a['circulation_m3_s'];pf=a['tmp_pa']+self.plant['permeate_backpressure_pa']+drop/2;pr=pf-drop
   if pf>4e5 or pr>3.5e5 or pr<0:raise ActionRejected('PRESSURE_ENVELOPE')
   self.method=copy.deepcopy(a);out={'configured':True}
  elif capability=='sample.configure':
   if self.sample or not self.plant or not self.cassette or not self.method or not a['sample_id'] or not a['unmodified_chain_monomer_model_confirmed'] or not 1<=len(a['solutes'])<=8 or len({x['substance_id'] for x in a['solutes']})!=len(a['solutes']) or self.method['target_retentate_m3']>=a['volume_m3'] or sum(x['mass_kg'] for x in a['solutes'])/a['volume_m3']>self.plant['max_concentration_kg_m3']:raise ActionRejected('PRECONDITION_FAILED')
   if any(x['substance_id'] not in self.templates for x in a['solutes']):raise ActionRejected('TEMPLATE_RANGE_UNAVAILABLE')
   self.sample=copy.deepcopy(a);self.volume=a['volume_m3'];self.mass=[x['mass_kg'] for x in a['solutes']];self.permeate_mass=[0.]*len(self.mass);self.adsorbed=list(self.permeate_mass);self.mw=[self.templates[x['substance_id']]['values']['unmodified_chain_molar_mass_kg_mol'] for x in a['solutes']];self.osmoles=a['volume_m3']*a['freely_permeating_osmolarity_mol_m3'];self.sieve=[1/(1+9*(m/self.cassette['mwco_kg_mol'])**self.plant['sieving_exponent']) for m in self.mw];out=dict(configured=True,attachment_event='host_attach:'+a['sample_id'])
  elif capability=='acquisition.start':
   if not self.sample or self.phase not in ('idle','cancelled'):raise ActionRejected('PRECONDITION_FAILED')
   self.phase='running';self.stage='concentrating' if self.method['mode']=='concentrate' else 'diafiltering';self._operation_count+=1;self.operation_id=f'{self.instance_id}:op{self._operation_count}';out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('PRECONDITION_FAILED')
   self.phase='cancelled';self.stage='paused';out={'phase':self.phase}
  elif capability=='filtration.measure':out=self._measure()
  elif capability=='product.read':out=dict(substance_ids=[x['substance_id'] for x in self.sample['solutes']] if self.sample else [],retentate_masses_kg=list(self.mass),permeate_masses_kg=list(self.permeate_mass),adsorbed_masses_kg=list(self.adsorbed),retentate_m3=self.volume,permeate_m3=self.permeate,retentate_includes_circuit_holdup=True,measured_recovered_product_kg=None,evidence='conditional_mass_balance_before_retention_recovery_or_rinse;adsorption_not_measured',template_source=TEMPLATE_REL)
  elif capability=='history.read':out=dict(records=copy.deepcopy(self.history),truncated=False,evidence='synthetic_model_process_curve')
  elif capability=='reference.lookup':
   t=self.templates.get(a['substance_id'])
   if t is None:raise ActionRejected('TEMPLATE_RANGE_UNAVAILABLE')
   out=dict(substance_id=a['substance_id'],unmodified_chain_molar_mass_kg_mol=t['values']['unmodified_chain_molar_mass_kg_mol'],measured_sieving=None,source=t['source'],evidence=t['evidence'],template_source=TEMPLATE_REL,manufacturer_condition_ids=self._refs(a['substance_id']))
  else:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][1],out)
  if CAPS[capability][4]!='query':self.revision+=1
  return out
 def _flux(self):
  lp=self.plant['pes_permeability_m_pa_s'] if self.cassette['material']=='PES' else self.plant['rc_permeability_m_pa_s'];lp/=1+self.rf;tmp=self.method['tmp_pa'];c=[m/self.volume for m in self.mass];km=self.plant['film_transfer_m_s']*(self.method['circulation_m3_s']/(300e-6/60))**(1/3)
  def film(j):
   exp=math.exp(min(30.,j/km));so=[s*exp/(1-s+s*exp) for s in self.sieve];cm=[x*exp/(1-s+s*exp) for x,s in zip(c,self.sieve)];pi=R*self.plant['temperature_k']*sum(x*(1-s)/mw for x,s,mw in zip(cm,self.sieve,self.mw));return so,pi
  lo=0.;hi=min(lp*tmp,.95*self.method['circulation_m3_s']/AREA)
  for _ in range(35):
   j=(lo+hi)/2;so,pi=film(j)
   if j>lp*max(0.,tmp-pi):hi=j
   else:lo=j
  j=(lo+hi)/2;return j,film(j)[0]
 def _fail(self,msg):self.phase='failed';self.stage='stopped';self.fault=msg
 def step(self,dt_s):
  if not isinstance(dt_s,(int,float)) or isinstance(dt_s,bool) or not math.isfinite(dt_s) or dt_s<0:raise ActionRejected('INVALID_ARGUMENT')
  if dt_s==0:return
  remaining=float(dt_s)
  while remaining>1e-12:
   dt=min(1.,remaining)
   if self.phase=='running':
    if self.elapsed_s>=self.method['timeout_s'] or self.elapsed_s>=21600:self._fail('TIMEOUT_OR_TUBE_6H')
    elif sum(self.mass)/self.volume>self.plant['max_concentration_kg_m3']:self._fail('HOST_CONCENTRATION_DOMAIN')
    else:
     j,so=self._flux();q=j*AREA;self.flux=j;self.stalled=self.stalled+dt if q<1e-14 else 0.
     if self.stalled>=30:self._fail('OSMOTIC_OR_FLOW_STALL')
     else:
      dt=min(dt,.01*self.volume/max(q,1e-30),max(1e-9,self.method['timeout_s']-self.elapsed_s));dv=q*dt
      target_remaining=self.volume-self.method['target_retentate_m3'] if self.method['mode']=='concentrate' else self.method['target_diavolumes']*self.sample['volume_m3']-self.buffer_used
      dv=min(dv,max(0.,target_remaining))
      if self.permeate+dv>self.plant['permeate_capacity_m3']+1e-15:self._fail('PERMEATE_VESSEL_FULL')
      elif self.method['mode']=='diafilter' and dv>self.buffer+1e-15:self._fail('BUFFER_EMPTY')
      else:
       oldv=self.volume;before=list(self.mass)
       if self.method['mode']=='concentrate':
        self.volume=max(self.method['target_retentate_m3'],self.volume-dv);self.mass=[m*(self.volume/oldv)**s for m,s in zip(self.mass,so)];outosm=self.osmoles*dv/oldv;self.osmoles-=outosm
       else:
        self.mass=[m*math.exp(-s*dv/oldv) for m,s in zip(self.mass,so)];cin=self.plant['buffer_osmolarity_mol_m3'];oldosm=self.osmoles;self.osmoles=cin*oldv+(oldosm-cin*oldv)*math.exp(-dv/oldv);outosm=oldosm+cin*dv-self.osmoles;self.buffer=max(0.,self.buffer-dv);self.buffer_used+=dv
       self.permeate_mass=[p+x-y for p,x,y in zip(self.permeate_mass,before,self.mass)];self.permeate_osmoles+=max(0.,outosm);self.permeate+=dv
       available=max(0.,self.plant['adsorption_capacity_kg']-sum(self.adsorbed));wanted=[m*(1-math.exp(-self.plant['adsorption_rate_s_inv']*dt)) for m in self.mass];scale=min(1.,available/max(sum(wanted),1e-30));ads=[x*scale for x in wanted];self.mass=[m-a for m,a in zip(self.mass,ads)];self.adsorbed=[m+a for m,a in zip(self.adsorbed,ads)]
       self.rf+=self.plant['fouling_coefficient_m2_kg']*sum((1-s)*m/oldv for s,m in zip(so,before))*dv/AREA
       if dv>=target_remaining-1e-14:self.phase='succeeded';self.stage='filtration_endpoint'
    self.elapsed_s+=dt
   self.time_s+=dt;remaining-=dt
   if self.time_s>=self.next_record:
    self.history.append(dict(time_s=self.time_s,retentate_m3=self.volume,permeate_m3=self.permeate,permeate_flow_m3_s=self.flux*AREA if self.phase=='running' else 0.,tmp_pa=self.method['tmp_pa'] if self.phase=='running' else 0.,diavolumes=self.buffer_used/self.sample['volume_m3'] if self.sample else 0.));self.next_record=self.time_s+10.
  self.revision+=1
class SartoriusVivaflow200Session:
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
        meta=export_sartorius_vivaflow200_contract_metadata()
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

def export_sartorius_vivaflow200_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'SartoriusVivaflow200Session.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key=='acquisition.start'; pre=[]
        if key=='acquisition.start':pre=[dict(description_zh='已选单盒SKU，外泵/容器/湿膜已确认，方法和样品已配置；首次或取消续作',state_ids=['cassette.configured','plant.configured','method.configured','sample.configured','cycle.phase'],enforced_by='runtime',check='configured and connected and sample_loaded and resumable')]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.sartorius_vivaflow200.SartoriusVivaflow200Session.invoke',invocation=f'session.invoke(instance_id="vivaflow200_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded表示达到浓缩体积或恒体积换液终点；未执行厂家终点缓速循环/回收/清洗' if async_ else '不推进时钟；只更新宿主边界或查询',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改物料；取消停止外部边界流、保留透过/吸附/污染。准稳态模型无弹性残压，停止后的吸附继续演化未实现；非真实安全认证。去重仅进程内'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    evolving=list(slots)
    for key in ['acquisition.start','acquisition.stop','plant.configure','sample.configure']:
        caps[key]['state_effects'] += [dict(state_id=k,timing='on_step' if key=='acquisition.start' else 'immediate',description_zh='准稳态压力边界、有限溶质/液体/自由透过渗透质账本') for k in evolving if k not in [x['state_id'] for x in caps[key]['state_effects']]]
    for key in ['sample.configure','reference.lookup']:caps[key]['errors'].append(dict(code='TEMPLATE_RANGE_UNAVAILABLE',meaning_zh='未收录该蛋白链参考',side_effects='none'))
    caps['method.configure']['errors'].append(dict(code='PRESSURE_ENVELOPE',meaning_zh='进料<=4bar，截留0..3.5bar；TMP不是单个端口表压',side_effects='none'))
    mechanism=dict(component_ids=['feed_port','membrane','retentate_port','permeate_port','restrictor','pressure_indicator'],state_ids=evolving,meaning_zh='单盒UF或恒体积DF，有限库存与未标定膜输运',implementation='lumped',equations=['one cassette area0.02m^2;feed500..2500mL,totalretentate>=20mL including circuit holdup','Pfeed=Pperm+TMP+Rchannel*Qcirculation/2;Pret=Pperm+TMP-Rchannel*Qcirculation/2;feed<=4bar,retentate<=3.5bar','Sm=1/(1+9*(unmodified_chain_MW/MWCO)^host_exponent);not an experimentally calibrated sieving curve','km=host_km*(Q/300mLmin)^(1/3);E=exp(J/km);So=Sm*E/(1-Sm+Sm*E);Cm=Cb*E/(1-Sm+Sm*E)','osmotic_dP=R*T*sum(Cm_i*(1-Sm_i)/chain_molar_mass_i);freely permeating buffer osmoles cancel at equal membrane concentrations','J=Lp_material/(1+Rf)*max(TMP-osmotic_dP(J),0);35-step bisection withJ<=0.95*Qcirculation/area','concentration exact frozenSo step:Mnew=Mold*(Vnew/Vold)^So;buffer osmoles leave atbulkconcentration','constant-volume DF:Mnew=Mold*exp(-So*dV/V);buffer_n_new=Cin*V+(nold-Cin*V)*exp(-dV/V)','adsorption desired=M*(1-exp(-host_kads*dt)),scaled to remaining finite binding capacity;mass moved toadsorbed ledger','Rf increases with host_coefficient*sum((1-So)*C)*dV/area;phenomenological resistance,not untracked deposited mass','initial_protein=retentate+permeate+adsorbed;initial_liquid=retentate+permeate+buffer;initial_free_osmoles=same three stocks','pressure_indicator_lift=retentate_gauge/350kPa*25mm;uncalibrated visual proxy only'],parameters={},source_ids=['manufacturer_manual','manufacturer_legacy','protein_database','simulation_assumption'],limitations=['60curated UniProt protein sequences/chain annotations yield calculated unmodified chain molar masses,NOT60measured sieving curves;PTMs,disulfide mass corrections,oligomers and hydrodynamic shape unknown.','16actual manufacturer performance conditions represent4sample classes,not16distinct proteins. Lysozyme species not specified;gamma-globulins mixture andyeast0.2um are references only,not separate modeled pure proteins.','Measured average flux and direct/flush recovery are preserved as referenceconditions,not used as instantaneous membrane permeability or intrinsic sieving coefficients.','One2023acrylic4–60C cassette configuration selected;newdatasheetPMP4–40C conflict retained. Host must confirm selected version;notapprovalfornewhardware.','Membrane material/MWCO fixed beforeloading;noonline replacement,PES0.2um microfiltration,two-cassette parallel orcleaning reuse.','All hydraulic,film,sieving,fouling,adsorption andconcentration-domain parameters are host assumptions;no measured permeability/retention curve calibration.','Ideal van’tHoff unmodified monomers only;charge,pH,Donnan,nonideality,PTM,aggregation,shear damage,temperature-viscosity dependence andphase changes absent.','Pressure is imposed external quasi-steadyincompressible boundary;no elastic residual pressure,gas,pulsation,leaks,permeate siphon or real shutdown safety.','Modeonly concentration ORconstant-volumeDF,not complete stagedDFrecipe. Endpoints stopfiltration;factory20–40mL/min1–2minfinish,retentaterecovery,25–50mLrinse,CIP,storageandsterilitynotimplemented.','Retentatevolume includes host circuit holdup>=5.3mL;reported product notall recoverable bottle contents.2016and2023nonrecoverable<1mL;exact recoveryunknown.','External pump and3reservoirs exist onlyas hostconnections/finite inventories,notnewphysicalassets. Sharedvacuum/bath explicitlynotconnected;existing independentperistalticassetnotfound.','Cassettepassive,noelectricalpowerorLCD. Digitalreadouts notapplicable;suppliedmechanicalpressureindicatorhasassumedlinear25mmstroke,notrealcalibration.','Localcomponentmass/inertia/frictionarenotweighed:URDF0.871kg=datasheet~0.84kgcassette(engineeringsplit)+~0.031kgsuppliedindicator(engineeringestimate);inertia/frictionunknown,geometryflowlanesnotCFD.','Cancelpreservesmaterial/adsorption/fouling;poststopadsorption/diffusionnotadvanced. Onefeedbatchperinstance;6hmaximumcumulativeruntime.','Hostidentity/asyncverifiedseparatelyfromMatterixouterack;nodeploymentfileisolation,robotconstraintsorhardwareverification.'])
    _ann.annotate_export('sartorius_vivaflow200',caps,slots,{'tangential_filtration':mechanism},inputs={'plant.configure':(PLANT,EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'tangential_filtration':mechanism})

"""S-300 Basic water-only drying: conservative cohorts, explicitly uncalibrated."""
from __future__ import annotations
import copy,json,math
from asset_runtime import _annotations as _ann
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.antonpaar_abbematadvanced import validate
ROOT=Path(__file__).resolve().parents[2];ASSET_TYPE_ID='instrument.buchi_minispray';CATALOG_ID='CHEM-026';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/buchi_minispray_drying_library/templates.json'
PHASE={'type':'string','enum':['idle','running','succeeded','cancelled','failed']}
def arr(s):return {'type':'array','items':s}
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
MASS=number('kg',0);FRAC=number('1',0,1);TEMP=number('K',200,700);FLOW=number('m^3/s',.1e-6/60,30e-6/60)
PLANT=obj({'ambient_k':number('K',278.15,313.15),'gas_reference_k':number('K',273.15,313.15),'gas_reference_pa':number('Pa',80000,120000),'gas_density_kg_m3':number('kg/m^3',.5,2),'gas_cp_j_kg_k':number('J/kg/K',700,1500),'heater_power_w':number('W',1,2300),'auxiliary_power_w':number('W',1,2300),'thermal_tau_s':number('s',.5,600),'chamber_volume_m3':number('m^3',.0001,.03),'water_density_kg_m3':number('kg/m^3',900,1100),'latent_j_kg':number('J/kg',2e6,2.6e6),'max_humidity_gain_kg_kg':number('kg/kg',.001,.5),'d2_rate_m2_s':number('m^2/s',1e-13,1e-7),'wall_rate_s_inv':number('1/s',0,10),'cyclone_d50_m':number('m',1e-7,1e-4),'filter_efficiency':FRAC,'filter_resistance_pa_kg':number('Pa/kg',0,1e10),'gas_supply_pa':number('Pa',6.5e5,7e5),'gas_pressure_basis':{'type':'string','enum':['manufacturer_unspecified']},'gas_connected':BOOL,'recipe_reviewed':BOOL,'coefficient_evidence':TEXT})
EXAMPLE=dict(ambient_k=298.15,gas_reference_k=298.15,gas_reference_pa=101325.,gas_density_kg_m3=1.184,gas_cp_j_kg_k=1005.,heater_power_w=2000.,auxiliary_power_w=300.,thermal_tau_s=3.,chamber_volume_m3=.008,water_density_kg_m3=997.,latent_j_kg=2.3e6,max_humidity_gain_kg_kg=.1,d2_rate_m2_s=3e-9,wall_rate_s_inv=.04,cyclone_d50_m=4e-6,filter_efficiency=.95,filter_resistance_pa_kg=1e7,gas_supply_pa=6.7e5,gas_pressure_basis='manufacturer_unspecified',gas_connected=True,recipe_reviewed=True,coefficient_evidence='synthetic_host_parameters_not_S300_calibration')
SAMPLE=obj({'sample_id':TEXT,'substance_id':TEXT,'solvent':{'type':'string','enum':['water']},'volume_m3':number('m^3',1e-9,.01),'solid_mass_fraction':number('1',.001,.5),'solid_density_kg_m3':number('kg/m^3',200,5000),'damage_rate_ref_s_inv':number('1/s',0,100),'damage_reference_k':TEMP,'damage_activation_j_mol':number('J/mol',0,2e5),'sticky_temperature_k':TEMP,'homogeneous_feed_reviewed':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='feed_1',substance_id='d_lactose',solvent='water',volume_m3=50e-6,solid_mass_fraction=.1,solid_density_kg_m3=1500.,damage_rate_ref_s_inv=.001,damage_reference_k=350.,damage_activation_j_mol=40000.,sticky_temperature_k=360.,homogeneous_feed_reviewed=True)
METHOD=obj({'inlet_target_k':number('K',303.15,493.15),'feed_m3_s':FLOW,'drying_gas_m3_s':number('m^3/s',1e-4,35/3600),'spray_gas_m3_s':number('m^3/s',80e-3/3600,1800e-3/3600),'nozzle':{'type':'string','enum':['two_fluid']},'needle_diameter_m':{'type':'number','enum':[.0005,.0007,.0014,.002],'x-unit':'m'},'atom_d_ref_m':number('m',1e-6,200e-6),'atom_reference_feed_m3_s':FLOW,'atom_reference_gas_m3_s':number('m^3/s',80e-3/3600,1800e-3/3600)})
METHOD_EXAMPLE=dict(inlet_target_k=423.15,feed_m3_s=5e-6/60,drying_gas_m3_s=25/3600,spray_gas_m3_s=600e-3/3600,nozzle='two_fluid',needle_diameter_m=.0007,atom_d_ref_m=30e-6,atom_reference_feed_m3_s=5e-6/60,atom_reference_gas_m3_s=600e-3/3600)
POINT=obj({'time_s':number('s',0),'inlet_k':TEMP,'outlet_k':TEMP,'feed_m3_s':number('m^3/s',0),'evaporation_kg_s':number('kg/s',0),'filter_delta_pa':number('Pa',0)})
RESULT=obj({'phase':PHASE,'fault':TEXT,'inlet_k':nullable(TEMP),'outlet_k':nullable(TEMP),'product_gas_temperature_k':{'type':'null'},'drying_gas_m3_s':number('m^3/s',0),'evaporation_kg_s':number('kg/s',0),'filter_delta_pa':number('Pa',0),'filter_maintenance_due':BOOL,'evidence':TEXT})
PRODUCT=obj({'sample_id':TEXT,'dry_solids_kg':MASS,'water_kg':MASS,'yield_dry_solid_fraction':nullable(FRAC),'active_fraction':nullable(FRAC),'water_mass_fraction':nullable(FRAC),'mass_weighted_particle_diameter_m':nullable(number('m',0)),'wet_reject_kg':MASS,'wall_mass_kg':MASS,'filter_mass_kg':MASS,'exhaust_particle_mass_kg':MASS,'evidence':TEXT,'template_source':TEXT})
REFERENCE=obj({'substance_id':TEXT,'glass_former':BOOL,'stable_on_storage':nullable(BOOL),'glass_transition_k':TEMP,'melting_k':TEMP,'tg_evidence':TEXT,'conditions':TEXT,'source':TEXT,'template_source':TEXT,'s300_yield_fraction':{'type':'null'},'s300_particle_diameter_m':{'type':'null'},'evidence':TEXT})
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet'],'供电；断电停泵停气但库存不消失','device_command','agent',{'enabled':True},['power.enabled','cycle.phase']),
 'clamp.set_lift':(obj({'lift_m':number('m',0,.03)}),obj({'lift_m':number('m',0,.03)}),['cylinder_clamp'],'教学夹具行程，冷却停气后才允许拆装','device_command','agent',{'lift_m':.03},['clamp.lift_m']),
 'vessel.set_lower':(obj({'lower_m':number('m',0,.05)}),obj({'lower_m':number('m',0,.05)}),['collection_vessel'],'教学集粉瓶拆卸行程','device_command','agent',{'lower_m':.05},['vessel.lower_m']),
 'plant.configure':(PLANT,obj({'configured':BOOL}),['heater','aspirator','outlet_filter'],'宿主首次物理系数与气源审查','configuration','evaluator',EXAMPLE,['plant.configured']),
 'sample.configure':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['feed_port'],'宿主水系单固体样品，一实例一批次','configuration','evaluator',SAMPLE_EXAMPLE,['sample.configured']),
 'method.configure':(METHOD,obj({'configured':BOOL}),['feed_pump','nozzle','heater'],'Basic两流体方法；雾化粒径系数为宿主假设','configuration','evaluator',METHOD_EXAMPLE,['method.configured']),
 'drying.prepare':(obj({}),obj({'preparing':BOOL}),['heater','aspirator','nozzle'],'启动气流和加热，必须step达到温度','device_command','agent',{},['airflow.enabled','heater.enabled']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['feed_pump','spray_cylinder','cyclone'],'启动/续作，料液耗尽后排空飞行颗粒才完成','device_command','agent',{},['cycle.phase']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['feed_pump'],'取消进料，不冻结热过程或飞行颗粒','device_command','agent',{},['cycle.phase']),
 'drying.shutdown':(obj({}),obj({'cooling':BOOL}),['heater','aspirator'],'停加热保留抽气冷却，不自动清洗','device_command','agent',{},['heater.enabled','cycle.phase']),
 'airflow.stop':(obj({}),obj({'stopped':BOOL}),['aspirator'],'冷却且无飞行颗粒才能停止抽气','device_command','agent',{},['airflow.enabled']),
 'drying.measure':(obj({}),RESULT,['display_panel'],'当前气温流量压差；不把可选产品气温当颗粒温度','query','agent',{},[]),
 'product.read':(obj({}),PRODUCT,['collection_vessel'],'未标定产率/湿度/粒径/活性真值；非实测','query','evaluator',{},[]),
 'history.read':(obj({}),obj({'records':arr(POINT),'truncated':BOOL,'evidence':TEXT}),['display_panel'],'每秒过程记录，读取不推进','query','agent',{},[]),
 'reference.lookup':(obj({'substance_id':TEXT}),REFERENCE,['display_panel'],'50物质原条件参考，不外推S300粒度产率','query','agent',{'substance_id':'d_lactose'},[]),
 'product.unload':(obj({'sample_id':TEXT}),obj({'unloaded':BOOL,'attachment_event':TEXT}),['collection_vessel'],'冷却、停气、降瓶后宿主取出产品','configuration','evaluator',{'sample_id':'feed_1'},['truth.removed_mass_kg'])}
SLOTS={}
def slot(k,s,u,o='derived',v='agent',c='display_panel',w=None):SLOTS[k]=(s,u,o,v,[c],w)
slot('power.enabled',BOOL,'1','setpoint',c='power_inlet',w=('power.set','enabled'));slot('clamp.lift_m',number('m',0,.03),'m','scene',c='cylinder_clamp',w=('clamp.set_lift','lift_m'));slot('vessel.lower_m',number('m',0,.05),'m','scene',c='collection_vessel',w=('vessel.set_lower','lower_m'))
for k,s,u in [('plant.configured',BOOL,'1'),('sample.configured',BOOL,'1'),('method.configured',BOOL,'1'),('cycle.phase',PHASE,'1'),('cycle.fault',TEXT,'1'),('cycle.elapsed_s',number('s',0),'s'),('airflow.enabled',BOOL,'1'),('heater.enabled',BOOL,'1'),('thermal.inlet_k',nullable(TEMP),'K'),('thermal.outlet_k',nullable(TEMP),'K'),('flow.drying_m3_s',number('m^3/s',0),'m^3/s'),('flow.feed_m3_s',number('m^3/s',0),'m^3/s'),('evaporation.kg_s',number('kg/s',0),'kg/s'),('filter.delta_pa',number('Pa',0),'Pa')]:slot(k,s,u)
for k in ['input','retained','vapor','removed','product','wall','filter','exhaust','wet_reject']:slot('truth.'+k+'_mass_kg',MASS,'kg','process_truth','evaluator','spray_cylinder')
class BuchiMinisprayRuntime:
 def __init__(self,instance_id='minispray_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.lift=0.;self.lower=0.;self.plant=None;self.sample=None;self.method=None;self.phase='idle';self.fault='';self.elapsed_s=0.;self.operation_id=None;self._operation_count=0;self.airflow=False;self.heating=False;self.inlet=298.15;self.outlet=298.15;self.stock={'s':0.,'w':0.,'a':0.};self.bins=[];self.pools={k:dict(s=0.,w=0.,a=0.) for k in ['product','wall','filter','exhaust','wet_reject','removed']};self.input_mass=0.;self.initial_solid=0.;self.vapor=0.;self.evap_rate=0.;self.dp=0.;self.diameter_sum=0.;self.unloaded=False;self.history=[];self.history_truncated=False;self.next_sample=0.;self.templates={t['substance_id']:t for t in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return BuchiMinispraySession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 @staticmethod
 def mass(p):return p['s']+p['w']
 def _inventory(self):
  v={k:self.mass(p) for k,p in self.pools.items()};return dict(input=self.input_mass,vapor=self.vapor,retained=self.mass(self.stock)+sum(self.mass(b) for b in self.bins)+sum(v[k] for k in ('product','wall','filter','wet_reject')),**{k:v[k] for k in v})
 def _values(self):
  v={'power.enabled':self.powered,'clamp.lift_m':self.lift,'vessel.lower_m':self.lower,'plant.configured':self.plant is not None,'sample.configured':self.sample is not None,'method.configured':self.method is not None,'cycle.phase':self.phase,'cycle.fault':self.fault,'cycle.elapsed_s':self.elapsed_s,'airflow.enabled':self.airflow,'heater.enabled':self.heating,'thermal.inlet_k':self.inlet if self.powered else None,'thermal.outlet_k':self.outlet if self.powered else None,'flow.drying_m3_s':self.method['drying_gas_m3_s'] if self.airflow else 0.,'flow.feed_m3_s':self.method['feed_m3_s'] if self.phase=='running' and self.mass(self.stock)>1e-18 else 0.,'evaporation.kg_s':self.evap_rate,'filter.delta_pa':self.dp};v.update({'truth.'+k+'_mass_kg':x for k,x in self._inventory().items()});return v
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('evaluator','debug')})
 def debug_snapshot(self):return copy.deepcopy(dict(state=self.read('evaluator'),stock=self.stock,bins=self.bins,pools=self.pools))
 def _measure(self):return dict(phase=self.phase,fault=self.fault,inlet_k=self.inlet if self.powered else None,outlet_k=self.outlet if self.powered else None,product_gas_temperature_k=None,drying_gas_m3_s=self.method['drying_gas_m3_s'] if self.airflow else 0.,evaporation_kg_s=self.evap_rate,filter_delta_pa=self.dp,filter_maintenance_due=self.dp>2000.,evidence='synthetic_host_drying_model; gas temperatures, no optional product sensor')
 def _product(self):
  p=self.pools['removed'] if self.unloaded else self.pools['product'];m=self.mass(p);return dict(sample_id=self.sample['sample_id'] if self.sample else '',dry_solids_kg=p['s'],water_kg=p['w'],yield_dry_solid_fraction=p['s']/self.initial_solid if self.initial_solid else None,active_fraction=p['a']/p['s'] if p['s'] else None,water_mass_fraction=p['w']/m if m else None,mass_weighted_particle_diameter_m=self.diameter_sum/p['s'] if p['s'] else None,wet_reject_kg=self.mass(self.pools['wet_reject']),wall_mass_kg=self.mass(self.pools['wall']),filter_mass_kg=self.mass(self.pools['filter']),exhaust_particle_mass_kg=self.mass(self.pools['exhaust']),evidence='synthetic_conservative_cohort_model; NOT S300 measured yield or morphology',template_source=TEMPLATE_REL)
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if self.phase=='running' and CAPS[capability][4]!='query' and capability not in ('power.set','acquisition.stop','drying.shutdown'):raise ActionRejected('BUSY')
  if capability=='power.set':
   self.powered=a['enabled']
   if not self.powered:
    self.airflow=False;self.heating=False
    if self.phase=='running':self.phase='cancelled'
   out=a
  elif capability in ('clamp.set_lift','vessel.set_lower'):
   ta=self.plant['ambient_k'] if self.plant else 298.15
   if self.airflow or self.heating or self.bins or self.inlet>ta+5:raise ActionRejected('PRECONDITION_FAILED')
   if capability=='clamp.set_lift':self.lift=a['lift_m']
   else:self.lower=a['lower_m']
   out=a
  elif capability=='plant.configure':
   if self.plant or self.sample or not a['recipe_reviewed'] or not a['gas_connected'] or a['heater_power_w']+a['auxiliary_power_w']>2300 or not a['coefficient_evidence']:raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);self.inlet=self.outlet=a['ambient_k'];out={'configured':True}
  elif capability=='sample.configure':
   if not self.plant or self.sample or self.phase!='idle' or not a['sample_id'] or a['substance_id'] not in self.templates or not a['homogeneous_feed_reviewed']:raise ActionRejected('PRECONDITION_FAILED')
   f=a['solid_mass_fraction'];rho=1/(f/a['solid_density_kg_m3']+(1-f)/self.plant['water_density_kg_m3']);m=rho*a['volume_m3'];self.stock=dict(s=m*f,w=m*(1-f),a=m*f);self.input_mass=m;self.initial_solid=m*f;self.sample=copy.deepcopy(a);out=dict(configured=True,attachment_event='host_attach:'+a['sample_id'])
  elif capability=='method.configure':
   if not self.plant or self.airflow or self.bins or self._operation_count:raise ActionRejected('PRECONDITION_FAILED')
   self.method=copy.deepcopy(a);out={'configured':True}
  elif capability=='drying.prepare':
   if not self.powered or not self.plant or not self.method or self.lift>1e-9 or self.lower>1e-9:raise ActionRejected('PRECONDITION_FAILED')
   self.airflow=True;self.heating=True;out={'preparing':True}
  elif capability=='acquisition.start':
   if not self.powered or not self.heating or not self.airflow or not self.sample or self.unloaded or self.lift>1e-9 or self.lower>1e-9 or self.phase=='failed' or abs(self.inlet-self.method['inlet_target_k'])>3 or (self.mass(self.stock)<1e-18 and not self.bins):raise ActionRejected('PRECONDITION_FAILED')
   self.phase='running';self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('PRECONDITION_FAILED')
   self.phase='cancelled';out=dict(phase=self.phase)
  elif capability=='drying.shutdown':
   self.heating=False
   if self.phase=='running':self.phase='cancelled'
   out=dict(cooling=self.airflow)
  elif capability=='airflow.stop':
   if self.heating or self.bins or (self.plant and self.inlet>self.plant['ambient_k']+5):raise ActionRejected('PRECONDITION_FAILED')
   self.airflow=False;out={'stopped':True}
  elif capability=='drying.measure':return self._measure()
  elif capability=='product.read':return self._product()
  elif capability=='history.read':return dict(records=copy.deepcopy(self.history),truncated=self.history_truncated,evidence='synthetic model 1s snapshots; not manufacturer recorded trace')
  elif capability=='reference.lookup':
   t=self.templates.get(a['substance_id'])
   if not t:raise ActionRejected('TEMPLATE_RANGE_UNAVAILABLE')
   v=t['values'];return dict(substance_id=t['substance_id'],glass_former=v['glass_former'],stable_on_storage=v['stable_on_storage'],glass_transition_k=v['glass_transition_k'],melting_k=v['melting_k'],tg_evidence=t['quantity_evidence']['glass_transition_k'],conditions=json.dumps(t['conditions'],sort_keys=True),source=t['source'],template_source=TEMPLATE_REL,s300_yield_fraction=None,s300_particle_diameter_m=None,evidence=t['evidence'])
  elif capability=='product.unload':
   if not self.sample or a['sample_id']!=self.sample['sample_id'] or self.unloaded or self.phase not in ('succeeded','cancelled') or self.airflow or self.heating or self.bins or self.lower<.049 or self.inlet>self.plant['ambient_k']+5:raise ActionRejected('PRECONDITION_FAILED')
   self.pools['removed']=self.pools['product'];self.pools['product']=dict(s=0.,w=0.,a=0.);self.unloaded=True;out=dict(unloaded=True,attachment_event='host_release:'+a['sample_id'])
  self.revision+=1;return copy.deepcopy(out)
 def _diameter(self,b):return (6*(b['w']/self.plant['water_density_kg_m3']+b['s']/self.sample['solid_density_kg_m3'])/(math.pi*b['n']))**(1/3) if b['n'] else 0.
 def _transfer(self,b,key,f):
  p=self.pools[key];f=min(1.,max(0.,f));d=self._diameter(b)
  if key=='product':self.diameter_sum+=b['s']*f*d
  for k in ['s','w','a']:x=b[k]*f;p[k]+=x;b[k]-=x
  b['n']*=1-f
 def _tick(self,h):
  p=self.plant;ta=p['ambient_k'];m=self.method;gas=m['drying_gas_m3_s']+m['spray_gas_m3_s'] if self.airflow else 0.;mdot=gas*p['gas_density_kg_m3'];cp=p['gas_cp_j_kg_k'];target=min(m['inlet_target_k'],ta+p['heater_power_w']/max(mdot*cp,1e-12)) if self.heating and self.airflow else ta
  self.inlet+=(target-self.inlet)*-math.expm1(-h/p['thermal_tau_s']);self.evap_rate=0.
  if self.phase=='running' and self.mass(self.stock)>1e-18:
   rho=1/(self.sample['solid_mass_fraction']/self.sample['solid_density_kg_m3']+(1-self.sample['solid_mass_fraction'])/p['water_density_kg_m3']);fraction=min(1.,m['feed_m3_s']*rho*h/self.mass(self.stock));feed={k:self.stock[k]*fraction for k in ['s','w','a']}
   for k in feed:self.stock[k]-=feed[k]
   dref=m['atom_d_ref_m']*(m['feed_m3_s']/m['atom_reference_feed_m3_s'])**.25*(m['atom_reference_gas_m3_s']/m['spray_gas_m3_s'])**.35*(m['needle_diameter_m']/.0007)**.5
   for ds,f in [(.65,.25),(1.,.5),(1.6,.25)]:
    b={k:feed[k]*f for k in feed};b.update(age=0.,n=6*(b['w']/p['water_density_kg_m3']+b['s']/self.sample['solid_density_kg_m3'])/(math.pi*(dref*ds)**3));self.bins.append(b)
  proposals=[]
  for b in self.bins:
   d=self._diameter(b);ddry=(6*b['s']/self.sample['solid_density_kg_m3']/math.pi/b['n'])**(1/3) if b['n'] else 0.;dnew=math.sqrt(max(ddry**2,d*d-p['d2_rate_m2_s']*max(0.,self.inlet-ta)/50*h)) if self.airflow else d
   wnew=max(0.,(dnew**3-ddry**3)*math.pi*b['n']/6*p['water_density_kg_m3']);proposals.append(min(b['w'],max(0.,b['w']-wnew)))
  requested=sum(proposals);energy=mdot*cp*max(0.,self.inlet-ta)*h;limit=min(energy/p['latent_j_kg'],mdot*p['max_humidity_gain_kg_kg']*h);scale=min(1.,limit/requested) if requested else 0.;evap=requested*scale;self.vapor+=evap;self.evap_rate=evap/h;self.outlet=self.inlet-evap*p['latent_j_kg']/max(h*mdot*cp,1e-12) if self.airflow else self.inlet
  keep=[];qactual=gas*self.inlet/p['gas_reference_k'];residence=p['chamber_volume_m3']/qactual if qactual else math.inf
  for b,e in zip(self.bins,proposals):
   b['w']-=e*scale;wet=b['w']/max(self.mass(b),1e-30);tp=ta+(self.outlet-ta)*(.3 if wet>.02 else 1.);s=self.sample;rate=s['damage_rate_ref_s_inv']*math.exp(max(-60.,min(60.,s['damage_activation_j_mol']/8.314462618*(1/s['damage_reference_k']-1/tp))));b['a']*=math.exp(-rate*h)
   if self.airflow:
    b['age']+=h;wall=p['wall_rate_s_inv']*(1+4*(wet>.15)+2*(tp>s['sticky_temperature_k']));self._transfer(b,'wall',-math.expm1(-wall*h))
    if b['age']>=residence:
     if wet>.15:self._transfer(b,'wet_reject',1.)
     else:
      d=self._diameter(b);cut=p['cyclone_d50_m']/math.sqrt(m['drying_gas_m3_s']/(25/3600));eta=d*d/(d*d+cut*cut);self._transfer(b,'product',eta);self._transfer(b,'filter',p['filter_efficiency']);self._transfer(b,'exhaust',1.)
     continue
   if self.mass(b)>1e-30:keep.append(b)
  self.bins=keep;self.dp=p['filter_resistance_pa_kg']*self.mass(self.pools['filter'])*(m['drying_gas_m3_s']/(25/3600)) if self.airflow else 0.
  if self.phase=='running':
   self.elapsed_s+=h
   if self.mass(self.stock)<=1e-18 and not self.bins:self.phase='succeeded'
  self.time_s+=h
  if self.time_s+1e-12>=self.next_sample:
   point=dict(time_s=self.time_s,inlet_k=self.inlet,outlet_k=self.outlet,feed_m3_s=self._values()['flow.feed_m3_s'],evaporation_kg_s=self.evap_rate,filter_delta_pa=self.dp)
   if len(self.history)<10000:self.history.append(point)
   else:self.history_truncated=True
   self.next_sample=self.time_s+1.
 def step(self,dt):
  if isinstance(dt,bool) or not isinstance(dt,(int,float)) or not math.isfinite(dt) or not 0<dt<=3600:raise ValueError('finite dt in(0,3600] required')
  left=float(dt)
  while left>1e-12:
   h=min(left,.05)
   if self.plant and self.method:self._tick(h)
   else:self.time_s+=h
   left-=h
  self.revision+=1;return self.read()
class BuchiMinispraySession:
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
        meta=export_buchi_minispray_contract_metadata()
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

def export_buchi_minispray_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'BuchiMinispraySession.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key=='acquisition.start'; pre=[]
        conditions={
         'acquisition.start':('供电气流加热开启，夹/瓶闭合，入口到设定±3K，未卸样且有剩余库存',['power.enabled','airflow.enabled','heater.enabled','clamp.lift_m','vessel.lower_m','sample.configured'],'powered and warm and assembled and resumable'),
         'plant.configure':('首次、气源和配方审查通过，热+辅机预算<=2300W',['plant.configured'],'not configured and reviewed and power_budget_valid'),
         'sample.configure':('首次水系单物质、宿主确认均匀供液',['sample.configured','plant.configured'],'plant_configured and not sample and water_only'),
         'method.configure':('供气未开且运行尚未开始；Basic两流体',['method.configured','airflow.enabled'],'not flowing and not started'),
         'product.unload':('批次完成或取消、已冷却停气排空、瓶降到位、样品身份正确',['cycle.phase','airflow.enabled','heater.enabled','vessel.lower_m'],'done_or_cancelled and cool and drained and matching_identity')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.buchi_minispray.BuchiMinispraySession.invoke',invocation=f'session.invoke(instance_id="minispray_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；进料耗尽且所有飞行颗粒排出；取消停止进料但继续残余干燥和冷却' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电不回滚水/固体；气流开启时剩余液滴继续演化，断电环境冷却但无主动蒸发。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    evolving=[k for k in slots if k.startswith(('truth.','cycle.','thermal.','flow.','evaporation.','filter.'))]
    for key in ['acquisition.start','plant.configure','sample.configure','product.unload']:
        caps[key]['state_effects'] += [dict(state_id=k,timing='on_step' if key=='acquisition.start' else 'immediate',description_zh='保守液滴水/固体账本；未标定') for k in evolving if k not in [e['state_id'] for e in caps[key]['state_effects']]]
    for key in ['clamp.set_lift','vessel.set_lower']:
        caps[key]['preconditions'].append(dict(description_zh='无气流、无加热、无飞行液滴且入口近环境温度',state_ids=['airflow.enabled','heater.enabled','thermal.inlet_k'],enforced_by='runtime',check='cool and off and drained'))
    caps['reference.lookup']['errors'].append(dict(code='TEMPLATE_RANGE_UNAVAILABLE',meaning_zh='物质没有公开原条件参考',side_effects='none'))
    mechanism=dict(component_ids=['heater','aspirator','feed_pump','nozzle','spray_cylinder','cyclone','outlet_filter','collection_vessel','separation_flask'],state_ids=evolving,meaning_zh='Basic水系开式抽吸三粒径液滴分组，显式宿主系数',implementation='lumped',equations=['rho_feed=1/(wsolid/rho_solid+(1-wsolid)/rho_water)','d0=dref*(Qfeed/Qref)^0.25*(Qgasref/Qgas)^0.35*(needle/0.7mm)^0.5; diameter factors[0.65,1,1.6], mass weights[0.25,0.5,0.25]','Tin_target=min(setpoint,ambient+heater_power/(gas_massflow*cp)); exponential approach with host tau','d_new^2=max(d_dry^2,d^2-K*(Tin-ambient)/50*dt)','evaporated=min(d2_proposals,gas_sensible_energy/latent,gas_massflow*host_humidity_gain*dt)','Tout=Tin-evaporation*latent/(gas_massflow*cp*dt)','active_solid*=exp(-k_ref*exp(E/R*(1/Tref-1/Tparticle))*dt); active/inactive are labels, total solid unchanged','wall_fraction=1-exp(-k_wall*(1+4*(water_fraction>0.15)+2*(Tp>Tsticky))*dt)','age>=Vchamber/Qactual: wet_fraction>0.15 -> separation flask; otherwise cyclone eta=d^2/(d^2+d50_effective^2), residual -> filter -> exhaust','input_feed=stock+flying+wall+wet_reject+product+filter+vapor+exhaust_particles+removed','filter_pressure_increase=host_resistance*filter_mass*Qdry/Qref'],parameters={},source_ids=['manufacturer_manual','simulation_assumption'],limitations=['One water-only batch per instance,homogeneous single solid solution/suspension,not solubility/emulsion prediction.','No organic/inert loop,no ultrasonic/three-fluid nozzle,no second pump,no optional product gas-temperature sensor.','Atomization,D2 evaporation,humidity ceiling,wall deposition,cyclone d50,filter resistance and Arrhenius parameters are host assumptions,not calibrated S300.','Gas volume reference temperature/pressure is explicit host metadata; manual volume reference conditions unknown.','Gas thermal system is lumped; heat/gas mixing and sensible feed enthalpy are approximated, no complete energy balance or certified heater efficiency.','Wet particle temperature=ambient+0.3*(outlet-ambient),dry=outlet: engineering approximation,not actual particle-core sensor.','Age-based residence,3size cohorts,no CFD,porosity/hollowness/agglomeration or full particle-size distribution.','Wall/product/filter/wet-reject deposits keep their captured masses and activity; postdeposition evaporation/degradation is not modeled.','Cancel stops feed only; remaining cohorts continue while airflow is on. Poweroff has no active vaporization/transport; environmental cooling and in-flight damage still evolve.','Cyclone dry-solid yield is captured/initial supplied solid,not guaranteed manufacturer up-to70percent yield.','Advertised1–60um family range not forced on synthetic output; standard two-fluid product comparison1–25um distinct.','50 reference GFA/thermal rows are B290 ethanol/acetone inert-loop outcomes,not measured S300 water yields/size; only original-condition lookup is supported.','Host attach/release events do not create robot constraints; no real compressor dynamics,explosion protection,gas certification,cleaning/flush chemistry or hardware communication.'])
    _ann.annotate_export('buchi_minispray',caps,slots,{'water_spray_drying':mechanism},inputs={'plant.configure':(PLANT,EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'water_spray_drying':mechanism})

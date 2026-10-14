"""E916 six-cell conservative extraction teaching model, NOT hardware control."""
from __future__ import annotations
import copy,json,math
from asset_runtime import _annotations as _ann
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.antonpaar_abbematadvanced import validate
ROOT=Path(__file__).resolve().parents[2];ASSET_TYPE_ID='instrument.buchi_speedextractore';CATALOG_ID='CHEM-028';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/buchi_speedextractore_extraction_library/templates.json';LIB=ROOT/Path(TEMPLATE_REL).parent
PHASE={'type':'string','enum':['idle','running','succeeded','cancelled','failed']}
def arr(s):return {'type':'array','items':s}
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
MASS=number('kg',0);VOL=number('m^3',0);PRESS=number('Pa',0);TEMP=number('K',273.15,473.15);FRAC=number('1',0,1)
SOLVENTS=['acetone','ethanol','methanol','n_hexane'];INT=lambda lo,hi:{'type':'integer','minimum':lo,'maximum':hi,'x-unit':'1'}
PLANT=obj({'ambient_k':number('K',288.15,303.15),'ambient_pa':number('Pa',80000,110000),'reservoir_m3':arr(number('m^3',0,.02)),'nitrogen_gauge_pa':number('Pa',6e5,1e6),'nitrogen_mol':number('mol',.001,1000),'nitrogen_mol_s_per_cell':number('mol/s',1e-6,.01),'exhaust_connected':BOOL,'dry_matrix_reviewed':BOOL,'thermal_tau_s':number('s',.5,300),'pressure_tau_s':number('s',.1,30),'discharge_m3_s_per_cell':number('m^3/s',1e-9,1e-5),'residual_holdup_fraction':number('1',0,.2),'vial_capacity_m3':number('m^3',60e-6,240e-6),'coefficient_evidence':TEXT})
EXAMPLE=dict(ambient_k=295.15,ambient_pa=101325.,reservoir_m3=[.001]*4,nitrogen_gauge_pa=7e5,nitrogen_mol=10.,nitrogen_mol_s_per_cell=.001,exhaust_connected=True,dry_matrix_reviewed=True,thermal_tau_s=10.,pressure_tau_s=1.,discharge_m3_s_per_cell=2e-6,residual_holdup_fraction=.02,vial_capacity_m3=240e-6,coefficient_evidence='synthetic_host_heat_hydraulic_transfer_and_holdup; NOT factory calibration')
METHOD=obj({'temperature_k':number('K',303.15,473.15),'pressure_gauge_pa':number('Pa',5e6,15e6),'cycles':INT(1,10),'hold_s':number('s',0,3600),'discharge_s':number('s',0,3600),'pump_total_m3_s':number('m^3/s',1e-6/60,50e-6/60),'solvent_fractions':arr(FRAC),'solvent_flush_s':number('s',0,540),'nitrogen_flush_s':number('s',60,1800)})
METHOD_EXAMPLE=dict(temperature_k=373.15,pressure_gauge_pa=10e6,cycles=2,hold_s=30.,discharge_s=30.,pump_total_m3_s=50e-6/60,solvent_fractions=[.5,.5,0.,0.],solvent_flush_s=0.,nitrogen_flush_s=60.)
SAMPLE=obj({'position':INT(0,5),'sample_id':TEXT,'substance_id':TEXT,'cell_volume_m3':{'type':'number','enum':[10e-6,20e-6,40e-6],'x-unit':'m^3'},'matrix_mass_kg':number('kg',1e-9,.1),'matrix_solid_volume_m3':number('m^3',1e-10,39e-6),'target_mass_kg':number('kg',1e-15,.001),'kd_m3_kg_by_solvent':arr(number('m^3/kg',1e-8,1e3)),'mass_transfer_s_inv':number('1/s',1e-6,10),'activation_energy_j_mol':number('J/mol',0,1e5),'adsorption_energy_j_mol':number('J/mol',0,1e5),'solubility_kg_m3':number('kg/m^3',1e-9,1000),'degradation_s_inv':number('1/s',0,1),'gripper_confirmed':BOOL,'parameters_evidence':TEXT})
SAMPLE_EXAMPLE=dict(position=0,sample_id='soil_1',substance_id='phenol',cell_volume_m3=20e-6,matrix_mass_kg=.005,matrix_solid_volume_m3=5e-6,target_mass_kg=1e-6,kd_m3_kg_by_solvent=[.0001,.0002,.00015,.01],mass_transfer_s_inv=.02,activation_energy_j_mol=10000.,adsorption_energy_j_mol=5000.,solubility_kg_m3=10.,degradation_s_inv=0.,gripper_confirmed=True,parameters_evidence='synthetic_host_soil_partition_transfer; NOT measured E916 recovery')
RESULT=obj({'phase':PHASE,'stage':TEXT,'fault':TEXT,'temperature_k':nullable(TEMP),'max_pressure_gauge_pa':nullable(PRESS),'cycle':INT(0,10),'stage_elapsed_s':number('s',0),'evidence':TEXT,'template_source':TEXT})
ITEM=obj({'position':INT(0,5),'sample_id':TEXT,'substance_id':TEXT,'extract_target_kg':MASS,'residual_target_kg':MASS,'degraded_target_kg':MASS,'matrix_mass_kg':MASS,'extract_solvent_m3':VOL,'residual_solvent_m3':VOL,'modeled_absolute_recovery_fraction':FRAC,'measured_e916_recovery_fraction':{'type':'null'},'reference_ase_soxtec_ratio':number('1',0),'reference_instrument':TEXT})
PRODUCT=obj({'items':arr(ITEM),'evidence':TEXT,'template_source':TEXT})
REFERENCE=obj({'substance_id':TEXT,'name':TEXT,'ase_soxtec_ratio':number('1',0),'measured_e916_recovery_fraction':{'type':'null'},'source':TEXT,'conditions':TEXT,'evidence':TEXT,'template_source':TEXT})
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet'],'电源；断电关闭主动阀，不消除残压','device_command','agent',{'enabled':True},['power.enabled','cycle.phase']),
 'shield.set_lift':(obj({'lift_m':number('m',0,.18)}),obj({'lift_m':number('m',0,.18)}),['shield'],'上推屏；只在非运行、脱压、两升降架降下时移动','device_command','agent',{'lift_m':.18},['shield.lift_m']),
 'drawer.set_extension':(obj({'extension_m':number('m',0,.16)}),obj({'extension_m':number('m',0,.16)}),['heater_drawer'],'上推屏后向前抽加热块','device_command','agent',{'extension_m':.16},['drawer.extension_m']),
 'plant.configure':(PLANT,obj({'configured':BOOL}),['pump','mixer','nitrogen_inlet','exhaust'],'有限四溶剂/氮气、宿主工程系数；仅首次','configuration','evaluator',EXAMPLE,['plant.configured']),
 'method.configure':(METHOD,obj({'configured':BOOL}),['panel','pump','heater_lift'],'温压循环时间、单泵总流量、四溶剂体积分数、氮吹','configuration','agent',METHOD_EXAMPLE,['method.configured','thermal.target_k']),
 'thermal.preheat':(obj({}),obj({'enabled':BOOL}),['heater_lift'],'装池前预热；step推进，不能装冷池后密封加热','device_command','agent',{},['thermal.preheat_enabled']),
 'sample.configure':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['heater_lift','cell_0','cell_1','cell_2','cell_3','cell_4','cell_5'],'宿主装入干燥/已审查固体；六位置必须同容量','configuration','evaluator',SAMPLE_EXAMPLE,['sample.configured']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['pump','media_valve','outlet_valve','heater_lift','collection_rack'],'密封、气密代理、充液、分级升压、保持、排液、最终冲洗/氮吹、解封','device_command','agent',{},['cycle.phase','cycle.stage']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['media_valve','pump'],'暂停/取消主动动作；保留残压/库存，热和传质继续','device_command','agent',{},['cycle.phase']),
 'acquisition.abort':(obj({}),obj({'phase':PHASE}),['media_valve','outlet_valve'],'宿主有序终止：排液、氮吹、解封；不是第三次急停固件','device_command','agent',{},['cycle.phase','cycle.stage']),
 'extraction.measure':(obj({}),RESULT,['display_digits','pressure_sensor_0'],'理想读数及阶段；不是校准仪表','query','agent',{},[]),
 'product.read':(obj({}),PRODUCT,['collection_rack','heater_lift'],'宿主条件回收率与真实跨仪器参考比值分开','query','evaluator',{},[]),
 'reference.lookup':(obj({'substance_id':TEXT}),REFERENCE,['display_digits'],'54真实ASE/Soxtec比较数据，不能当本机绝对回收率','query','agent',{'substance_id':'phenol'},[]),
 'sample.unload':(obj({'position':INT(0,5),'sample_id':TEXT,'gripper_confirmed':BOOL}),obj({'unloaded':BOOL,'attachment_event':TEXT}),['heater_lift','collection_rack'],'完整或有序终止后，开屏抽块，用夹具取池','configuration','evaluator',{'position':0,'sample_id':'soil_1','gripper_confirmed':True},['truth.removed_target_kg'])}
SLOTS={}
def slot(k,s,u,o='derived',v='agent',c='display_digits',w=None):SLOTS[k]=(s,u,o,v,[c],w)
slot('power.enabled',BOOL,'1','setpoint',c='power_inlet',w=('power.set','enabled'));slot('shield.lift_m',number('m',0,.18),'m','scene',c='shield',w=('shield.set_lift','lift_m'));slot('drawer.extension_m',number('m',0,.16),'m','scene',c='heater_drawer',w=('drawer.set_extension','extension_m'))
for k,c in [('heater.lift_m','heater_lift'),('rack.lift_m','collection_rack')]:slot(k,number('m',0,.035),'m','scene',c=c)
for k,s,u in [('plant.configured',BOOL,'1'),('sample.configured',BOOL,'1'),('method.configured',BOOL,'1'),('thermal.preheat_enabled',BOOL,'1'),('thermal.target_k',nullable(TEMP),'K'),('thermal.indicated_k',nullable(TEMP),'K'),('pressure.max_gauge_pa',nullable(PRESS),'Pa'),('cycle.phase',PHASE,'1'),('cycle.stage',TEXT,'1'),('cycle.fault',TEXT,'1'),('cycle.index',INT(0,10),'1'),('cycle.elapsed_s',number('s',0),'s'),('cycle.stage_elapsed_s',number('s',0),'s')]:slot(k,s,u)
for k,u in [('initial_target_kg','kg'),('sorbed_target_kg','kg'),('dissolved_target_kg','kg'),('extract_target_kg','kg'),('degraded_target_kg','kg'),('removed_target_kg','kg'),('initial_solvent_m3','m^3'),('reservoir_solvent_m3','m^3'),('cell_solvent_m3','m^3'),('extract_solvent_m3','m^3'),('waste_solvent_m3','m^3'),('removed_solvent_m3','m^3'),('initial_nitrogen_mol','mol'),('remaining_nitrogen_mol','mol'),('exhaust_nitrogen_mol','mol')]:slot('truth.'+k,number(u,0),u,'process_truth','evaluator','heater_lift')
class BuchiSpeedextractoreRuntime:
 def __init__(self,instance_id='speedextractor_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.shield=0.;self.drawer=0.;self.heater=0.;self.rack=0.;self.plant=None;self.method=None;self.cells={};self.phase='idle';self.stage='standby';self.fault='';self.elapsed_s=0.;self.stage_elapsed=0.;self.cycle=0;self.operation_id=None;self._operation_count=0;self.preheat=False;self.temperature=295.15;self.reservoir=[0.]*4;self.waste=[0.]*4;self.nitrogen=0.;self.nitrogen_out=0.;self.aborting=False;self.history=[];self.next_sample=0.;self.templates={x['substance_id']:x for x in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.solvents={x['substance_id']:x for x in json.loads((LIB/'solvents.json').read_text())['solvents']};self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return BuchiSpeedextractoreSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _maxp(self):return max((c['pressure'] for c in self.cells.values()),default=0.)
 def _inventory(self):
  active=[c for c in self.cells.values() if not c['removed']];removed=[c for c in self.cells.values() if c['removed']]
  return dict(initial_target_kg=sum(c['spec']['target_mass_kg'] for c in self.cells.values()),sorbed_target_kg=sum(c['sorbed'] for c in active),dissolved_target_kg=sum(c['dissolved'] for c in active),extract_target_kg=sum(c['extract'] for c in active),degraded_target_kg=sum(c['degraded'] for c in active),removed_target_kg=sum(c['sorbed']+c['dissolved']+c['extract']+c['degraded'] for c in removed),initial_solvent_m3=sum(self.plant['reservoir_m3']) if self.plant else 0.,reservoir_solvent_m3=sum(self.reservoir),cell_solvent_m3=sum(sum(c['liquid']) for c in active),extract_solvent_m3=sum(sum(c['vial']) for c in active),waste_solvent_m3=sum(self.waste),removed_solvent_m3=sum(sum(c['liquid'])+sum(c['vial']) for c in removed),initial_nitrogen_mol=self.plant['nitrogen_mol'] if self.plant else 0.,remaining_nitrogen_mol=self.nitrogen,exhaust_nitrogen_mol=self.nitrogen_out)
 def _values(self):
  v={'power.enabled':self.powered,'shield.lift_m':self.shield,'drawer.extension_m':self.drawer,'heater.lift_m':self.heater,'rack.lift_m':self.rack,'plant.configured':self.plant is not None,'sample.configured':bool(self.cells),'method.configured':self.method is not None,'thermal.preheat_enabled':self.preheat,'thermal.target_k':self.method['temperature_k'] if self.method else None,'thermal.indicated_k':self.temperature if self.powered else None,'pressure.max_gauge_pa':self._maxp() if self.powered else None,'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.fault':self.fault,'cycle.index':self.cycle,'cycle.elapsed_s':self.elapsed_s,'cycle.stage_elapsed_s':self.stage_elapsed};v.update({'truth.'+k:x for k,x in self._inventory().items()});return v
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _measure(self):return dict(phase=self.phase,stage=self.stage,fault=self.fault,temperature_k=self.temperature if self.powered else None,max_pressure_gauge_pa=self._maxp() if self.powered else None,cycle=self.cycle,stage_elapsed_s=self.stage_elapsed,evidence='synthetic_host_lumped_partition_and_control; NOT validated recovery or safety',template_source=TEMPLATE_REL)
 def _product(self):
  items=[]
  for i,c in self.cells.items():
   s=c['spec'];ref=self.templates[s['substance_id']];items.append(dict(position=i,sample_id=s['sample_id'],substance_id=s['substance_id'],extract_target_kg=c['extract'],residual_target_kg=c['sorbed']+c['dissolved'],degraded_target_kg=c['degraded'],matrix_mass_kg=s['matrix_mass_kg'],extract_solvent_m3=sum(c['vial']),residual_solvent_m3=sum(c['liquid']),modeled_absolute_recovery_fraction=min(1.,c['extract']/s['target_mass_kg']),measured_e916_recovery_fraction=None,reference_ase_soxtec_ratio=ref['values']['ase_to_soxtec_recovery_ratio'],reference_instrument=ref['conditions']['instrument']))
  return dict(items=items,evidence='synthetic_absolute_yield_from_host_Kd_transfer; measured_literature_relative_ASE200_to_Soxtec_is_NOT_E916_yield',template_source=TEMPLATE_REL)
 def saturation_pressure(self,temperature,volume_fractions):
  total=0.;parts=[]
  for sid,f in zip(SOLVENTS,volume_fractions):
   s=self.solvents[sid];points=s['vapor_pressure_reference_points'];t=temperature
   if not points[0]['temperature_k']<=t<=points[-1]['temperature_k']:raise ActionRejected('TEMPLATE_RANGE_UNAVAILABLE')
   j=min(len(points)-2,int((t-points[0]['temperature_k'])/5));lo,hi=points[j:j+2];weight=(t-lo['temperature_k'])/(hi['temperature_k']-lo['temperature_k']);ps=math.exp((1-weight)*math.log(lo['pressure_pa'])+weight*math.log(hi['pressure_pa']));moles=f*s['density_298_15_kg_m3']/s['molar_mass_kg_mol'];parts.append((moles,ps));total+=moles
  return sum(n*p for n,p in parts)/total if total else 0.
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  for k in ('position','cycles'):
   if k in a and (isinstance(a[k],bool) or not isinstance(a[k],int) or not (1<=a[k]<=10 if k=='cycles' else 0<=a[k]<=5)):raise ActionRejected('INVALID_ARGUMENT')
  if self.phase=='running' and CAPS[capability][4]!='query' and capability not in ('power.set','acquisition.stop','acquisition.abort'):raise ActionRejected('BUSY')
  if capability=='power.set':
   self.powered=a['enabled']
   if not self.powered and self.phase=='running':self.phase='cancelled'
   out=a
  elif capability in ('shield.set_lift','drawer.set_extension'):
   if self._maxp()>1e5 or self.heater>1e-9 or self.rack>1e-9:raise ActionRejected('PRECONDITION_FAILED')
   if capability=='shield.set_lift':
    if a['lift_m']<.179 and self.drawer>1e-9:raise ActionRejected('PRECONDITION_FAILED')
    self.shield=a['lift_m']
   else:
    if self.shield<.179:raise ActionRejected('PRECONDITION_FAILED')
    self.drawer=a['extension_m']
   out=a
  elif capability=='plant.configure':
   if self.plant or not a['exhaust_connected'] or not a['dry_matrix_reviewed'] or len(a['reservoir_m3'])!=4 or not a['coefficient_evidence']:raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);self.reservoir=list(a['reservoir_m3']);self.nitrogen=a['nitrogen_mol'];self.temperature=a['ambient_k'];out={'configured':True}
  elif capability=='method.configure':
   f=a['solvent_fractions']
   if self._operation_count or self.cells or len(f)!=4 or abs(sum(f)-1)>1e-9:raise ActionRejected('PRECONDITION_FAILED')
   if a['pressure_gauge_pa']/3+101325<1.1*self.saturation_pressure(a['temperature_k'],f):raise ActionRejected('LIQUID_PHASE_GUARD')
   self.method=copy.deepcopy(a);out={'configured':True}
  elif capability=='thermal.preheat':
   if not self.powered or not self.plant or not self.method or self.cells or self.heater>0:raise ActionRejected('PRECONDITION_FAILED')
   self.preheat=True;out={'enabled':True}
  elif capability=='sample.configure':
   if not self.plant or not self.method or not self.preheat or abs(self.temperature-self.method['temperature_k'])>1 or self.shield<.179 or self.drawer<.159 or not a['gripper_confirmed'] or self._operation_count or a['position'] in self.cells or not a['sample_id'] or any(c['spec']['sample_id']==a['sample_id'] for c in self.cells.values()) or a['matrix_solid_volume_m3']>=a['cell_volume_m3']*.9 or a['target_mass_kg']>a['matrix_mass_kg']*.01 or len(a['kd_m3_kg_by_solvent'])!=4 or not a['parameters_evidence']:raise ActionRejected('PRECONDITION_FAILED')
   if a['substance_id'] not in self.templates:raise ActionRejected('TEMPLATE_RANGE_UNAVAILABLE')
   if any(c['spec']['cell_volume_m3']!=a['cell_volume_m3'] for c in self.cells.values()):raise ActionRejected('PRECONDITION_FAILED')
   self.cells[a['position']]=dict(spec=copy.deepcopy(a),pressure=0.,liquid=[0.]*4,vial=[0.]*4,sorbed=a['target_mass_kg'],dissolved=0.,extract=0.,degraded=0.,removed=False);out=dict(configured=True,attachment_event='host_attach:'+a['sample_id'])
  elif capability=='acquisition.start':
   if not self.powered or not self.plant or not self.method or not self.cells or any(c['removed'] for c in self.cells.values()) or self.shield>1e-9 or self.drawer>1e-9 or self.phase not in ('idle','cancelled') or self.stage=='complete' or abs(self.temperature-self.method['temperature_k'])>1:raise ActionRejected('PRECONDITION_FAILED')
   if not self._operation_count:self._next('sealing');self.cycle=1
   self.phase='running';self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('PRECONDITION_FAILED')
   self.phase='cancelled';out=dict(phase=self.phase)
  elif capability=='acquisition.abort':
   if not self.powered or not self._operation_count or self.phase not in ('running','cancelled') or self.stage=='complete':raise ActionRejected('PRECONDITION_FAILED')
   self.aborting=True;self.phase='running';self._next('discharge');out=dict(phase=self.phase)
  elif capability=='extraction.measure':return self._measure()
  elif capability=='product.read':return self._product()
  elif capability=='reference.lookup':
   q=self.templates.get(a['substance_id'])
   if q is None:raise ActionRejected('TEMPLATE_RANGE_UNAVAILABLE')
   return dict(substance_id=q['substance_id'],name=q['name'],ase_soxtec_ratio=q['values']['ase_to_soxtec_recovery_ratio'],measured_e916_recovery_fraction=None,source=q['source'],conditions=json.dumps(q['conditions'],ensure_ascii=False),evidence=q['evidence'],template_source=TEMPLATE_REL)
  elif capability=='sample.unload':
   c=self.cells.get(a['position'])
   if not c or c['spec']['sample_id']!=a['sample_id'] or c['removed'] or self.stage!='complete' or self._maxp()>1e5 or self.shield<.179 or self.drawer<.159 or not a['gripper_confirmed']:raise ActionRejected('PRECONDITION_FAILED')
   c['removed']=True;out=dict(unloaded=True,attachment_event='host_release:'+a['sample_id'])
  self.revision+=1;return copy.deepcopy(out)
 def _next(self,stage):
  self.stage=stage;self.stage_elapsed=0.;self.history.append(dict(time_s=self.time_s,stage=stage,cycle=self.cycle))
 def _fail(self,reason):self.phase='failed';self.fault=reason
 def _feed(self,c,vol):
  f=self.method['solvent_fractions'];v=min([vol]+[self.reservoir[i]/x for i,x in enumerate(f) if x>0])
  for i,x in enumerate(f):self.reservoir[i]-=v*x;c['liquid'][i]+=v*x
  if v+1e-15<vol:self._fail('solvent_supply_empty')
 def _drain(self,c,vol,holdup=0.):
  v=sum(c['liquid']);x=min(max(0.,v-holdup),vol);space=self.plant['vial_capacity_m3']-sum(c['vial'])
  if x>space+1e-15:self._fail('collection_vial_full');x=max(0.,space)
  frac=x/v if v else 0.;m=c['dissolved']*frac;c['dissolved']-=m;c['extract']+=m
  for i in range(4):x=c['liquid'][i]*frac;c['liquid'][i]-=x;c['vial'][i]+=x
 def _transfer(self,c,h):
  if c['removed']:return
  s=c['spec'];v=sum(c['liquid']);t=self.temperature;total=c['sorbed']+c['dissolved']
  if v>1e-15:
   kd=math.exp(sum(c['liquid'][i]/v*math.log(s['kd_m3_kg_by_solvent'][i]) for i in range(4)))*math.exp(s['adsorption_energy_j_mol']/8.314462618*(1/t-1/298.15));eq=min(total*v/(v+kd*s['matrix_mass_kg']),s['solubility_kg_m3']*v);k=s['mass_transfer_s_inv']*math.exp(s['activation_energy_j_mol']/8.314462618*(1/298.15-1/t));delta=(eq-c['dissolved'])*-math.expm1(-k*h);delta=max(-c['dissolved'],min(c['sorbed'],delta));c['sorbed']-=delta;c['dissolved']+=delta
  d=-math.expm1(-s['degradation_s_inv']*h)
  for key in ('sorbed','dissolved'):x=c[key]*d;c[key]-=x;c['degraded']+=x
 def _gas(self,h):
  requested=self.plant['nitrogen_mol_s_per_cell']*len(self.cells)*h;x=min(self.nitrogen,requested);self.nitrogen-=x;self.nitrogen_out+=x
  if x+1e-12<requested:self._fail('nitrogen_supply_empty')
 def _tick(self,h):
  p=self.plant;m=self.method;target=m['temperature_k'] if self.powered and self.preheat and m else p['ambient_k'];self.temperature+=(target-self.temperature)*-math.expm1(-h/p['thermal_tau_s'])
  running=self.powered and self.phase=='running'
  if running:
   self.elapsed_s+=h;self.stage_elapsed+=h
   if self.stage=='sealing':
    self.heater=min(.035,self.heater+.01*h);self.rack=min(.035,self.rack+.01*h)
    if self.heater>=.035 and self.rack>=.035:self._next('tightness')
   elif self.stage=='tightness':
    self._gas(h)
    if self.stage_elapsed>=10 and self.phase=='running':self._next('fill')
   elif self.stage=='fill':
    for c in self.cells.values():self._feed(c,min(m['pump_total_m3_s']/6*h,max(0.,c['spec']['cell_volume_m3']-c['spec']['matrix_solid_volume_m3']-sum(c['liquid']))))
    if self.phase=='running' and all(abs(sum(c['liquid'])-(c['spec']['cell_volume_m3']-c['spec']['matrix_solid_volume_m3']))<1e-12 for c in self.cells.values()):self._next('heatup_1')
   elif self.stage.startswith('heatup_') or self.stage=='hold':
    fraction={'heatup_1':1/3,'heatup_2':2/3,'heatup_3':1.,'hold':1.}[self.stage]
    for c in self.cells.values():c['pressure']+=(m['pressure_gauge_pa']*fraction-c['pressure'])*-math.expm1(-h/p['pressure_tau_s'])
    dwell=10*next(iter(self.cells.values()))['spec']['cell_volume_m3']/20e-6
    if self.stage.startswith('heatup_') and self.stage_elapsed>=dwell and all(abs(c['pressure']-m['pressure_gauge_pa']*fraction)<1e4 for c in self.cells.values()) and abs(self.temperature-m['temperature_k'])<=1:self._next({'heatup_1':'heatup_2','heatup_2':'heatup_3','heatup_3':'hold'}[self.stage])
    elif self.stage=='hold' and self.stage_elapsed>=m['hold_s']:self._next('discharge')
   elif self.stage=='discharge':
    for c in self.cells.values():
     c['pressure']*=math.exp(-h/p['pressure_tau_s']);free=c['spec']['cell_volume_m3']-c['spec']['matrix_solid_volume_m3'];self._drain(c,p['discharge_m3_s_per_cell']*h,free*p['residual_holdup_fraction'])
    drained=all(sum(c['liquid'])<=(c['spec']['cell_volume_m3']-c['spec']['matrix_solid_volume_m3'])*p['residual_holdup_fraction']+1e-12 for c in self.cells.values())
    if self.phase=='running' and drained and self._maxp()<=1e5 and self.stage_elapsed>=m['discharge_s']:
     if self.cycle<m['cycles'] and not self.aborting:self.cycle+=1;self._next('fill')
     else:self._next('solvent_flush' if m['solvent_flush_s']>0 and not self.aborting else 'nitrogen_flush')
   elif self.stage=='solvent_flush':
    for c in self.cells.values():
     v=m['pump_total_m3_s']/6*h;self._feed(c,v);self._drain(c,v)
    if self.stage_elapsed>=m['solvent_flush_s'] and self.phase=='running':self._next('nitrogen_flush')
   elif self.stage=='nitrogen_flush':
    self._gas(h)
    for c in self.cells.values():self._drain(c,p['discharge_m3_s_per_cell']*h);c['pressure']*=math.exp(-h/p['pressure_tau_s'])
    if self.stage_elapsed>=m['nitrogen_flush_s'] and self.phase=='running' and all(sum(c['liquid'])<1e-15 for c in self.cells.values()) and self._maxp()<=1e5:self._next('release')
   elif self.stage=='release':
    self.heater=max(0.,self.heater-.01*h);self.rack=max(0.,self.rack-.01*h)
    if self.heater<=1e-12 and self.rack<=1e-12:self.heater=self.rack=0.;self._next('complete');self.phase='cancelled' if self.aborting else 'succeeded'
  for c in self.cells.values():self._transfer(c,h)
  self.time_s+=h
 def step(self,dt):
  if isinstance(dt,bool) or not isinstance(dt,(int,float)) or not math.isfinite(dt) or not 0<dt<=3600:raise ActionRejected('INVALID_ARGUMENT')
  n=math.ceil(dt/.25);h=dt/n
  for _ in range(n):
   if self.plant:self._tick(h)
   else:self.time_s+=h
  self.revision+=1;return self.read()

class BuchiSpeedextractoreSession:
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
        meta=export_buchi_speedextractore_contract_metadata()
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

def export_buchi_speedextractore_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'BuchiSpeedextractoreSession.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key=='acquisition.start'; pre=[]
        conditions={
         'acquisition.start':('供电、预热达温、已装同容量池，屏/滑轨闭合，首次或暂停续作',['power.enabled','sample.configured','thermal.indicated_k','shield.lift_m','drawer.extension_m','cycle.phase'],'powered and loaded and preheated and closed and resumable'),
         'sample.configure':('装池前预热，开屏抽块，夹具确认，首次且同容量，目标量<=基质1%',['thermal.preheat_enabled','thermal.indicated_k','shield.lift_m','drawer.extension_m'],'preheated_before_loading and gripper and equal_cell_capacity'),
         'method.configure':('装样/运行前，四比例合计1，整数周期，分级升压保守液相检查',['sample.configured','cycle.phase'],'before_loading and ratios_sum_one and liquid_guard'),
         'sample.unload':('完整或有序终止后，残压<=1bar、开屏抽块，夹具和身份确认',['cycle.stage','pressure.max_gauge_pa','shield.lift_m','drawer.extension_m'],'complete and depressurized and open and matching_identity')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.buchi_speedextractore.BuchiSpeedextractoreSession.invoke',invocation=f'session.invoke(instance_id="speedextractor_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded且完成排液/氮吹/降架；暂停保留库存和残压' if async_ else '设定或查询不推进时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝无修改；暂停关闭主动阀但不冻结热/传质/降解，有限供应或满瓶失败保留物料；不是实机安全认证。去重仅进程内有效'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    evolving=[k for k in slots if k not in ('power.enabled','shield.lift_m','drawer.extension_m')]
    for key in ['acquisition.start','plant.configure','sample.configure','sample.unload','acquisition.abort','thermal.preheat']:
        caps[key]['state_effects'] += [dict(state_id=k,timing='on_step' if key in ('acquisition.start','acquisition.abort','thermal.preheat') else 'immediate',description_zh='六位置守恒传质与阶段过程') for k in evolving if k not in [e['state_id'] for e in caps[key]['state_effects']]]
    for key in ['shield.set_lift','drawer.set_extension']:
        caps[key]['preconditions'].append(dict(description_zh='非运行、表压<=1bar、热块和瓶架降下；抽块另需屏完全上推',state_ids=['cycle.phase','pressure.max_gauge_pa','heater.lift_m','rack.lift_m','shield.lift_m'],enforced_by='runtime',check='not_running and pressure_low and lifts_down and clearance'))
    for key in ['reference.lookup','sample.configure']:caps[key]['errors'].append(dict(code='TEMPLATE_RANGE_UNAVAILABLE',meaning_zh='参考物质或纯溶剂公式范围不可用',side_effects='none'))
    caps['method.configure']['errors'].append(dict(code='LIQUID_PHASE_GUARD',meaning_zh='保守液相模型范围：目标表压1/3+大气压需高于理想蒸气压1.1倍',side_effects='none'))
    mechanism=dict(component_ids=['pump','mixer','heater_lift','media_valve','outlet_valve','collection_rack'],state_ids=evolving,meaning_zh='同容量六池有限溶剂与目标物守恒、条件分配与传质',implementation='lumped',equations=['four stock liquid volume ledgers: initial=reservoir+cell+vial+waste+removed; reference density fixed, thermal expansion neglected','target_initial=sorbed+dissolved+extract+degraded+removed, independently per cell','one total pump1..50mL/min divided by6; inactive positions reduce effective method flow, exact factory algorithm unknown','ideal Raoult vapor pressure: x_i=(volume_fraction*rho_i/M_i)/sum; p=sum(x_i*log_interpolated_35point_CoolProp7.2.0_EOS_i(T))','q_solid=Kd*C_liquid; M_liquid_eq=min(M_total*V/(V+Kd*matrix_mass),solubility*V)','mixture Kd=geometric_volume_mean(Kd_i)*exp(Eads/R*(1/T-1/298.15)); k=k_ref*exp(Ea/R*(1/298.15-1/T))','delta_M=(M_liquid_eq-M_liquid)*(1-exp(-k*dt)); clamp to available material','host degradation first order conservatively moves sorbed/dissolved target to degraded ledger; extract cooled proxy no degradation','preheat first; seal3.5s, nitrogen tightness proxy10s, finite fill, staged pressure1/3,2/3,full; intrinsic dwell proxy10s*(cell_volume/20mL)','hold and discharge each0..3600s;1..10cycles; discharge NO nitrogen, final optional solvent0..540s and mandatory N2 60..1800s','finite host nitrogen molar stock flows only tightness/final nitrogen, never creates liquid; nitrogen push moves residual liquid to collection not evaporation','pressure hydraulic first-order target and discharge decay, not mass-derived compressible EOS; pressure<=1bar before automatic release','pause closes active flows but thermal/partition/degradation continue; ordered abort drains/purges/releases then cancelled complete'],parameters={},source_ids=['manufacturer_manual','simulation_assumption'],limitations=['54measured AN317 ratios compare Dionex ASE200 with Soxtec and may exceed1; NOT E916 absolute recoveries,partition coefficients or transfer constants. No measured E916 calibration available.','54names have unresolved CAS; two ambiguous source names excluded. Original DCM/acetone reference recipe is not the four-solvent runtime subset.','Host supplies Kd,transfer,activation,solubility,degradation,thermal lag,pressure lag,discharge and holdup. These are synthetic assumptions,not inferred from recovery ratios.','Four-port E916 selected,10/20/40mL equal-capacity runtime groups; current geometry only20mL proxy and separately purchased240mL bottles. Mixed capacities and changing vials between cycles absent.','140pure-solvent EOS nodes and136heldout midpoint log-interpolation tests; ideal mole-fraction Raoult only; nonideal mixing,boiling,gas dissolution,thermal expansion and complete energy balance not implemented. Fill/discharge transient two-phase behavior absent; model does not certify real safety.','Pressure lag is independent of fluid compressibility; per-cell pressure sensors ideal,not manufacturer accuracy/noise calibration. Initial nitrogen test checks host connections,not actual leak or five-minute liquid LEAK procedure.','Six cells equal nominal division total/6 is an explicit flow assumption; factory active-position flow algorithm not independently known. Standalone service FLUSH50mL/min differs and is not implemented.','All product-directed liquid captured in selected bottle until finite capacity; waste ports are visible but selectable waste/service routing absent. N2 transports residual liquid,not solvent evaporation or aerosol entrainment.','No cell cleaning,wet-sample drying,polymer precipitation,clogging,needle puncture,thread torque,real pressure seal contact,robot pickup or hardware USB/control. One batch per instance.','Pause/resume and host ordered abort implemented; exact one/two/three STOP button timing and third-press immediate assembly freezing not implemented. No automatic recovery from failed supply/vial/pressure safety guards.','Cold loading forbidden; hot unload requires host gripper confirmation only,not a simulated thermal-contact safety guarantee. All safety interlocks are teaching subset,not certified.','Four joint strokes and all local component dimensions/masses unknown estimates. 90kg/98kg and datasheet dimension conflicts retained; actual total mass null.','Agent truth filtering and nativeSession async are tested separately from Matterix outer ack; no deployment isolation or robot constraint.'])
    _ann.annotate_export('buchi_speedextractore',caps,slots,{'pressurized_extraction':mechanism},inputs={'plant.configure':(PLANT,EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'pressurized_extraction':mechanism})

"""Monowave 400: finite-inventory Antoine pressure and explicitly synthetic thermal dynamics.

No reaction kinetics, product yield, electromagnetic field or manufacturer control firmware.
Trusted host establishes sample, gas boundary and mechanical attachment events.
Library v1.1: 72 cold-loadable pure liquids (NIST Antoine, NIST SRD 69 refits, composite low-T fits); real NIST liquid Cp
is used when the host passes heat_capacity_j_kg_k=None; `template.read` exposes the public reference data per substance.
"""
from __future__ import annotations
import copy,json,math
from asset_runtime import _annotations as _ann
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT,PHASE
from asset_runtime.antonpaar_abbematadvanced import validate
ROOT=Path(__file__).resolve().parents[2];ASSET_TYPE_ID='instrument.antonpaar_monowave400';CATALOG_ID='CHEM-015';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/antonpaar_monowave400_thermophysical_library/templates.json'
TEMP=number('K',288.15,573.15);PRESS=number('Pa',0,3e6);LIFT=number('m',0,.15);LOCK=number('m',0,.01)
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
RESULT=obj({'temperature_k':nullable(TEMP),'pressure_pa':nullable(number('Pa',0)),'microwave_power_w':number('W',0,850),'stir_rpm':number('rpm',0,1200),'phase':PHASE,'program_stage':TEXT,'fault':TEXT,'quality':TEXT,'conversion_fraction':{'type':'null'},'yield_fraction':{'type':'null'},'template_source':TEXT,'evidence':TEXT})
POINT=obj({'time_s':number('s',0),'temperature_k':nullable(TEMP),'pressure_pa':nullable(number('Pa',0)),'microwave_power_w':number('W',0,850)})
SAMPLE=obj({'substance_id':TEXT,'fit_id':TEXT,'container_id':TEXT,'volume_m3':number('m^3',6e-6,20e-6),'mass_kg':number('kg',.001,.1),'temperature_k':TEMP,'noncondensable_pressure_pa':number('Pa',0,110000),'heat_capacity_j_kg_k':nullable(number('J/(kg*K)',100,10000)),'vial_heat_capacity_j_k':number('J/K',1,500),'absorption_fraction':number('1',.01,1),'passive_ua_w_k':number('W/K',.01,10),'cooling_ua_w_k':number('W/K',.01,50),'compatible_sealed_g30':BOOL})
EXAMPLE=dict(substance_id='ethanol',fit_id='1',container_id='g30_01',volume_m3=10e-6,mass_kg=.008,temperature_k=298.15,noncondensable_pressure_pa=100000.,heat_capacity_j_kg_k=2400.,vial_heat_capacity_j_k=20.,absorption_fraction=.5,passive_ua_w_k=.1,cooling_ua_w_k=2.,compatible_sealed_g30=True)
METHOD=obj({'target_k':TEMP,'ramp_s':number('s',.1,360000),'hold_s':number('s',0,360000),'cool_to_k':TEMP,'stir_rpm':number('rpm',0,1200),'power_limit_w':number('W',0,850),'pressure_limit_pa':number('Pa',100000,3e6)})
NUM_OR_NULL=lambda u:nullable(number(u))
LIB_SOURCES=['nist_templates','pubchem_annotations','nist_srd69_fluid','nist_condensed_phase_cp','cem_solvent_choice']
FIT_INFO=obj({'fit_id':TEXT,'kind':TEXT,'evidence':TEXT,'source_interval_k':{'type':'array','items':number('K',0),'minItems':2,'maxItems':2},'runtime_interval_k':nullable({'type':'array','items':number('K',0),'minItems':2,'maxItems':2}),'psat_298_pa':NUM_OR_NULL('Pa'),'reference':TEXT})
DIEL=obj({'dielectric_constant':number('1',0),'tan_delta':number('1',0),'dielectric_loss':number('1',0),'frequency_hz':number('Hz',0),'evidence':TEXT,'licence_flag':TEXT,'coupling_class_vendor':{'type':'string','enum':['high','medium','low']}})
SEALED=obj({'fill_volume_m3':number('m^3',0),'vessel_volume_m3':number('m^3',0),'temperature_k':number('K',0),'pressure_psi_reading':number('psi',0)})
TEMPLATE_INFO=obj({'substance_id':TEXT,'cas':TEXT,'inchikey':nullable(TEXT),'name_zh':TEXT,'molar_mass_kg_mol':number('kg/mol',0),'runtime_eligible':BOOL,'cold_start_fit_ids':{'type':'array','items':TEXT},'blocked_reason':nullable(TEXT),'fits':{'type':'array','items':FIT_INFO},'heat_capacity_j_kg_k':NUM_OR_NULL('J/(kg*K)'),'heat_capacity_temperature_k':NUM_OR_NULL('K'),'heat_capacity_evidence':nullable(TEXT),'liquid_density_kg_m3':NUM_OR_NULL('kg/m^3'),'liquid_density_temperature_k':NUM_OR_NULL('K'),'dielectric_2450mhz':nullable(DIEL),'sealed_vessel_reference':{'type':'array','items':SEALED},'licence_flags':{'type':'array','items':TEXT},'template_source':TEXT,'evidence':TEXT})
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','button_power','magnetron'],'电源；断电取消加热并保留热/压状态','device_command','agent',{'enabled':True},['power.enabled','cycle.phase','microwave.output_w']),
 'lid.set_lift':(obj({'lift_m':LIFT}),obj({'lift_m':LIFT}),['lid'],'宿主控制示意顶盖；锁住时禁止移动','device_command','agent',{'lift_m':.15},['lid.lift_m']),
 'lock.set_travel':(obj({'travel_m':LOCK}),obj({'travel_m':LOCK}),['lock_pin'],'宿主示意锁；热压或运行时禁止释放','device_command','agent',{'travel_m':0.},['lock.travel_m']),
 'gas.configure':(obj({'connected':BOOL,'pressure_pa':number('Pa',0,1e6),'flow_l_min':number('L/min',0,500)}),obj({'configured':BOOL}),['cooling_gas_inlet','cooling_nozzle','gas_exhaust'],'宿主冷却气边界；流量参考状态未知，不求质量流量','configuration','evaluator',{'connected':True,'pressure_pa':575000.,'flow_l_min':200.},['gas.connected','gas.ready']),
 'sample.configure':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['sample_vial','vial_cap','vial_septum','vial_interface'],'宿主确认密闭G30装入；实际约束创建由宿主负责','configuration','evaluator',EXAMPLE,['sample.loaded','sample.input_kg','sample.retained_kg','sample.input_m3','sample.retained_m3','attachment.container_id']),
 'sample.unload':(obj({}),obj({'unloaded':BOOL,'attachment_event':TEXT}),['sample_vial','vial_interface'],'安全开盖后宿主移出；不替代机器人抓取','configuration','evaluator',{},['sample.loaded','sample.removed_kg','sample.retained_kg','sample.removed_m3','sample.retained_m3','attachment.container_id']),
 'method.configure':(METHOD,obj({'configured':BOOL}),['display_digits'],'单段升温-保温-冷却程序；范围以选定物性拟合约束','configuration','agent',dict(target_k=308.15,ramp_s=2.,hold_s=1.,cool_to_k=301.15,stir_rpm=600.,power_limit_w=850.,pressure_limit_pa=3e6),['method.target_k','method.stir_rpm','method.power_limit_w','method.pressure_limit_pa']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['magnetron','cavity','stir_motor','pressure_sensor','ir_sensor'],'闭盖锁紧、气源合格且有样后启动异步程序','device_command','agent',{},['cycle.phase','cycle.elapsed_s','microwave.output_w','stir.actual_rpm']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['magnetron','stir_motor'],'取消并保留已发生的热量、蒸发和记录','device_command','agent',{},['cycle.phase','microwave.output_w','stir.actual_rpm']),
 'reaction.measure':(obj({}),RESULT,['display_digits','ir_sensor','pressure_sensor'],'纯读未标定T/P代理，反应转化率与产率不可用','query','agent',{},[]),
 'history.read':(obj({}),obj({'records':{'type':'array','items':POINT},'truncated':BOOL,'evidence':TEXT,'template_source':TEXT}),['display_digits','usb_port'],'纯读1秒间隔、最多1000点的合成时间记录；非真实USB通信','query','agent',{},[]),
 'template.read':(obj({'substance_id':TEXT}),TEMPLATE_INFO,['display_digits'],'纯读公开物性参考：拟合区间、NIST液相Cp、密度、2.45GHz介电与CEM密闭管压力读数（参考，不改变吸收率）','query','agent',{'substance_id':'dmf'},[]),
 'product.read':(obj({}),obj({'retained_mass_kg':number('kg',0),'substance_id':nullable(TEXT),'conversion_fraction':{'type':'null'},'yield_fraction':{'type':'null'},'evidence':TEXT}),['sample_vial'],'宿主产品真值：仅热处理，无验证反应则身份质量不变','query','evaluator',{},[]),
}
SLOTS={}
def slot(k,s,u,o='derived',v='agent',c='display_digits',w=None):SLOTS[k]=(s,u,o,v,[c],w)
slot('power.enabled',BOOL,'1','setpoint',c='power_inlet',w=('power.set','enabled'))
slot('lid.lift_m',LIFT,'m','scene',c='lid',w=('lid.set_lift','lift_m'))
slot('lock.travel_m',LOCK,'m','scene',c='lock_pin',w=('lock.set_travel','travel_m'))
for k,s,u in [('target_k',TEMP,'K'),('stir_rpm',number('rpm',0,1200),'rpm'),('power_limit_w',number('W',0,850),'W'),('pressure_limit_pa',PRESS,'Pa')]:slot('method.'+k,s,u,'setpoint')
for k,s,u in [('cycle.phase',PHASE,'1'),('cycle.stage',TEXT,'1'),('cycle.elapsed_s',number('s',0),'s'),('cycle.fault',TEXT,'1'),('gas.connected',BOOL,'1'),('gas.ready',BOOL,'1'),('sample.loaded',BOOL,'1'),('attachment.container_id',nullable(TEXT),'1'),('thermal.indicated_k',nullable(TEMP),'K'),('pressure.indicated_pa',nullable(number('Pa',0)),'Pa'),('microwave.output_w',number('W',0,850),'W'),('stir.actual_rpm',number('rpm',0,1200),'rpm')]:slot(k,s,u)
for k,u in [('temperature_k','K'),('pressure_pa','Pa'),('liquid_kg','kg'),('vapor_kg','kg'),('stored_energy_j','J'),('absorbed_energy_j','J'),('heat_out_j','J')]:slot('truth.'+k,nullable(number(u)),u,'process_truth','evaluator','sample_vial')
for axis,unit in [('kg','kg'),('m3','m^3')]:
 for k in ['input','retained','removed']:slot(f'sample.{k}_{axis}',number(unit,0),unit,'process_truth','evaluator','sample_vial')
class AntonpaarMonowave400Runtime:
 def __init__(self,instance_id='monowave400_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.lift=0.;self.lock=0.;self.gas=dict(connected=False,pressure_pa=0.,flow_l_min=0.)
  self.phase='idle';self.stage='idle';self.elapsed_s=0.;self.hold_elapsed=0.;self.fault='';self.output_w=0.;self.actual_rpm=0.;self.operation_id=None;self._operation_count=0
  self.sample=None;self.temperature=None;self.absorbed=0.;self.heat_out=0.;self.start_temperature=None;self.history=[];self.history_truncated=False;self.next_record_s=0.
  self.method=copy.deepcopy(CAPS['method.configure'][6]);self.input_kg=0.;self.removed_kg=0.;self.input_m3=0.;self.removed_m3=0.
  self.templates={x['substance_id']:x for x in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return AntonpaarMonowave400Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _heat_capacity(self,rec,given):
  """Host value wins; None means 'use the template NIST liquid Cp' (constant-Cp approximation, ±10 K of 298.15 K source point)."""
  if given is not None:return float(given),'host_input'
  hc=rec.get('heat_capacity')
  if not hc or hc.get('phase')!='liquid' or abs(hc['temperature_k']-298.15)>10 or not 100<=hc['value_j_kg_k']<=10000:raise ActionRejected('PRECONDITION_FAILED')
  return float(hc['value_j_kg_k']),'template:'+hc['source']
 def _template_info(self,sid):
  rec=self.templates.get(sid)
  if rec is None:raise ActionRejected('PRECONDITION_FAILED')
  hc=rec.get('heat_capacity') or {};de=rec.get('liquid_density') or {};di=rec.get('dielectric_2450mhz')
  fits=[dict(fit_id=f['fit_id'],kind=f.get('kind','nist_antoine'),evidence=f['evidence'],source_interval_k=list(f['temperature_range_k']),runtime_interval_k=list(f['runtime_temperature_range_k']) if f['runtime_temperature_range_k'] else None,psat_298_pa=1e5*10**(f['A']-f['B_k']/(298.15+f['C_k'])) if f['temperature_range_k'][0]<=298.15<=f['temperature_range_k'][1] else None,reference=f['reference']) for f in rec['fits']]
  diel=None
  if di:diel=dict(dielectric_constant=di['dielectric_constant'],tan_delta=di['tan_delta'],dielectric_loss=di['dielectric_loss'],frequency_hz=di['conditions']['frequency_hz'],evidence=di['evidence'],licence_flag=di['licence_flag'],coupling_class_vendor='high' if di['dielectric_loss']>14 else 'medium' if di['dielectric_loss']>=1 else 'low')
  return dict(substance_id=sid,cas=rec['cas'],inchikey=rec.get('inchikey'),name_zh=rec['name_zh'],molar_mass_kg_mol=rec['molar_mass_kg_mol'],runtime_eligible=rec['runtime_eligible'],cold_start_fit_ids=list(rec['cold_start_fit_ids']),blocked_reason=rec.get('blocked_reason'),fits=fits,
   heat_capacity_j_kg_k=hc.get('value_j_kg_k'),heat_capacity_temperature_k=hc.get('temperature_k'),heat_capacity_evidence=hc.get('evidence'),liquid_density_kg_m3=de.get('value_kg_m3'),liquid_density_temperature_k=de.get('temperature_k'),dielectric_2450mhz=diel,
   sealed_vessel_reference=[dict(fill_volume_m3=x['fill_volume_m3'],vessel_volume_m3=x['vessel_volume_m3'],temperature_k=x['temperature_k'],pressure_psi_reading=float(x['pressure_psi'])) for x in rec.get('sealed_vessel_reference',[]) if not x.get('source_anomaly')],
   licence_flags=list(rec.get('licence_flags',[])),template_source=TEMPLATE_REL,evidence='public_reference_data_not_this_vial_state')
 def _gas_ready(self):return self.gas['connected'] and 550000<=self.gas['pressure_pa']<=600000 and self.gas['flow_l_min']>50
 def _equilibrium(self,t):
  s=self.sample;f=s['fit'];ps=1e5*10**(f['A']-f['B_k']/(t+f['C_k']));mv=min(s['mass_kg'],ps*s['headspace_m3']*s['mw']/(8.314462618*t));pv=mv/s['mw']*8.314462618*t/s['headspace_m3']
  return mv,pv+s['noncondensable_pressure_pa']*t/s['temperature_k']
 def _energy(self,t):
  s=self.sample;mv,_=self._equilibrium(t);return s['capacity']*(t-s['temperature_k'])+s['latent_j_kg']*(mv-s['initial_vapor_kg'])
 def _safe(self):return not self.sample or (self.temperature<=323.15+1e-8 and self._equilibrium(self.temperature)[1]<=120000+1e-6)
 def _values(self):
  s=self.sample;mv,p=self._equilibrium(self.temperature) if s else (None,None)
  r={'power.enabled':self.powered,'lid.lift_m':self.lift,'lock.travel_m':self.lock,'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.elapsed_s':self.elapsed_s,'cycle.fault':self.fault,'gas.connected':self.gas['connected'],'gas.ready':self._gas_ready(),'sample.loaded':bool(s),'attachment.container_id':s['container_id'] if s else None,'thermal.indicated_k':round(self.temperature,2) if s and self.powered and self.temperature>=303.15 else None,'pressure.indicated_pa':round(p,0) if s and self.powered else None,'microwave.output_w':self.output_w,'stir.actual_rpm':self.actual_rpm,'truth.temperature_k':self.temperature,'truth.pressure_pa':p,'truth.vapor_kg':mv,'truth.liquid_kg':s['mass_kg']-mv if s else None,'truth.stored_energy_j':self._energy(self.temperature) if s else None,'truth.absorbed_energy_j':self.absorbed if s else None,'truth.heat_out_j':self.heat_out if s else None}
  for k in ['target_k','stir_rpm','power_limit_w','pressure_limit_pa']:r['method.'+k]=self.method[k]
  for axis in ['kg','m3']:
   r[f'sample.input_{axis}']=getattr(self,'input_'+axis);r[f'sample.removed_{axis}']=getattr(self,'removed_'+axis);r[f'sample.retained_{axis}']=s['mass_kg' if axis=='kg' else 'volume_m3'] if s else 0.
  return r
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('evaluator','debug')})
 def debug_snapshot(self):return dict(state=self.read('evaluator'),sample=copy.deepcopy(self.sample),history=copy.deepcopy(self.history))
 def _measure(self):
  r=self._values();return dict(temperature_k=r['thermal.indicated_k'],pressure_pa=r['pressure.indicated_pa'],microwave_power_w=self.output_w,stir_rpm=self.actual_rpm,phase=self.phase,program_stage=self.stage,fault=self.fault,quality='synthetic_unvalidated_sensors' if self.powered and self.sample else 'unavailable',conversion_fraction=None,yield_fraction=None,template_source=TEMPLATE_REL,evidence='NIST_pressure_fit_derived_thermophysics_with_synthetic_time_dynamics')
 def _check_method(self,a,s=None):
  if a['cool_to_k']>a['target_k'] or a['ramp_s']+a['hold_s']>360000:raise ActionRejected('INVALID_ARGUMENT')
  s=s or self.sample
  if s:
   lo,hi=s['fit']['runtime_temperature_range_k']
   if not lo<=min(a['cool_to_k'],s['temperature_k'])<=max(a['target_k'],s['temperature_k'])<=hi or a['cool_to_k']<298.15:raise ActionRejected('PRECONDITION_FAILED')
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if self.phase=='running' and capability not in ('power.set','acquisition.stop','reaction.measure','history.read','product.read','gas.configure','template.read'):raise ActionRejected('BUSY')
  if capability=='power.set':
   self.powered=a['enabled']
   if not self.powered:
    if self.phase=='running':self.phase='cancelled';self.stage='cooling'
    self.output_w=0.;self.actual_rpm=0.
   r=dict(a)
  elif capability=='lid.set_lift':
   if self.lock>1e-5 or (a['lift_m']>1e-5 and not self._safe()):raise ActionRejected('PRECONDITION_FAILED')
   self.lift=a['lift_m'];r=dict(a)
  elif capability=='lock.set_travel':
   if (a['travel_m']>1e-5 and self.lift>1e-5) or (a['travel_m']<.009 and not self._safe()):raise ActionRejected('PRECONDITION_FAILED')
   self.lock=a['travel_m'];r=dict(a)
  elif capability=='gas.configure':
   self.gas=dict(a)
   if self.phase=='running' and not self._gas_ready():self.phase='failed';self.fault='cooling_gas_lost';self.stage='cooling';self.output_w=0.;self.actual_rpm=0.
   r={'configured':True}
  elif capability=='sample.configure':
   rec=self.templates.get(a['substance_id']);fit=next((f for f in rec['fits'] if f['fit_id']==a['fit_id']),None) if rec else None
   if self.sample or self.lift<.14 or not a['compatible_sealed_g30'] or not fit or not fit['runtime_temperature_range_k']:raise ActionRejected('PRECONDITION_FAILED')
   lo,hi=fit['runtime_temperature_range_k']
   if not lo<=a['temperature_k']<=min(hi,323.15) or not lo<=298.15<=hi:raise ActionRejected('PRECONDITION_FAILED')
   cp,cp_source=self._heat_capacity(rec,a['heat_capacity_j_kg_k'])
   s=copy.deepcopy(a);s.update(fit=copy.deepcopy(fit),mw=rec['molar_mass_kg_mol'],headspace_m3=30e-6-a['volume_m3'],heat_capacity_j_kg_k=cp,heat_capacity_source=cp_source,capacity=a['mass_kg']*cp+a['vial_heat_capacity_j_k'])
   t=a['temperature_k'];ps=1e5*10**(fit['A']-fit['B_k']/(t+fit['C_k']));s['initial_vapor_kg']=min(a['mass_kg'],ps*s['headspace_m3']*s['mw']/(8.314462618*t));s['latent_j_kg']=8.314462618*math.log(10)*fit['B_k']*t*t/(t+fit['C_k'])**2/s['mw']
   if ps+a['noncondensable_pressure_pa']>120000 or s['initial_vapor_kg']>=a['mass_kg']:raise ActionRejected('PRECONDITION_FAILED')
   self.sample=s;self.temperature=t;self.absorbed=0.;self.heat_out=0.;self.input_kg+=a['mass_kg'];self.input_m3+=a['volume_m3'];self.history=[];self.history_truncated=False;r=dict(configured=True,attachment_event='host_attach:'+a['container_id'])
  elif capability=='sample.unload':
   if not self.sample or self.lift<.14 or not self._safe():raise ActionRejected('PRECONDITION_FAILED')
   cid=self.sample['container_id'];self.removed_kg+=self.sample['mass_kg'];self.removed_m3+=self.sample['volume_m3'];self.sample=None;self.temperature=None;r=dict(unloaded=True,attachment_event='host_release:'+cid)
  elif capability=='method.configure':self._check_method(a);self.method=dict(a);r={'configured':True}
  elif capability=='acquisition.start':
   if not self.powered or not self.sample or self.lift>1e-5 or self.lock<.009 or not self._gas_ready():raise ActionRejected('PRECONDITION_FAILED')
   self._check_method(self.method)
   if self._equilibrium(self.temperature)[1]>=self.method['pressure_limit_pa'] or self.temperature>self.method['target_k']:raise ActionRejected('PRECONDITION_FAILED')
   self._operation_count+=1;self.operation_id=f'{self.instance_id}:microwave:{self._operation_count}';self.phase='running';self.stage='heating';self.fault='';self.elapsed_s=0.;self.hold_elapsed=0.;self.start_temperature=self.temperature;self.next_record_s=self.time_s;self.history=[];self.history_truncated=False;r=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase=='running':self.phase='cancelled';self.stage='cooling'
   self.output_w=0.;self.actual_rpm=0.;r={'phase':self.phase}
  elif capability=='history.read':return dict(records=copy.deepcopy(self.history),truncated=self.history_truncated,evidence='NIST_pressure_fit_with_synthetic_time_dynamics_not_measured_Monowave_trace',template_source=TEMPLATE_REL)
  elif capability=='template.read':return self._template_info(a['substance_id'])
  elif capability=='product.read':return dict(retained_mass_kg=self.sample['mass_kg'] if self.sample else 0.,substance_id=self.sample['substance_id'] if self.sample else None,conversion_fraction=None,yield_fraction=None,evidence='thermal_conditioning_only_no_reaction_kinetics')
  else:return self._measure()
  self.revision+=1;return copy.deepcopy(r)
 def _invert(self,energy,lo,hi):
  for _ in range(42):
   mid=(lo+hi)/2
   if self._energy(mid)<energy:lo=mid
   else:hi=mid
  return (lo+hi)/2
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<=0:raise ValueError('finite positive dt required')
  left=float(dt_s)
  while left>1e-9:
   h=min(.1,left);s=self.sample;active=self.phase=='running';self.output_w=0.;self.actual_rpm=0.
   if s:
    old=self.temperature;lo,hi=s['fit']['runtime_temperature_range_k'];ua=s['passive_ua_w_k']+(s['cooling_ua_w_k'] if self._gas_ready() and (self.stage=='cooling' or not active) else 0.)
    if active:
     self.elapsed_s+=h;self.actual_rpm=self.method['stir_rpm']
     if self.stage=='heating' and old>=self.method['target_k']-.1 and self.elapsed_s>=self.method['ramp_s']:self.stage='holding'
     if self.stage=='holding':
      if old>=self.method['target_k']-.1:self.hold_elapsed+=h
      if self.hold_elapsed>=self.method['hold_s']:self.stage='cooling';ua=s['passive_ua_w_k']+(s['cooling_ua_w_k'] if self._gas_ready() else 0.)
     if self.stage!='cooling':
      target=self.start_temperature+(self.method['target_k']-self.start_temperature)*min(1.,self.elapsed_s/self.method['ramp_s'])
      self.output_w=min(self.method['power_limit_w'],max(0.,(ua*(target-298.15)+20*(target-old))/s['absorption_fraction']))
     if self.elapsed_s>=360000:self.phase='failed';self.fault='maximum_program_time';self.stage='cooling';self.output_w=0.
    loss=ua*(old-298.15)*h;absorbed=self.output_w*s['absorption_fraction']*h;u=self._energy(old);wanted=u+absorbed-loss
    # Explicit no-extrapolation temperature boundary. Source fit is never switched.
    new=self._invert(max(self._energy(lo),min(self._energy(hi),wanted)),lo,hi)
    if active and self._equilibrium(new)[1]>self.method['pressure_limit_pa']:
     a,b=old,new
     for _ in range(42):
      mid=(a+b)/2
      if self._equilibrium(mid)[1]>self.method['pressure_limit_pa']:b=mid
      else:a=mid
     new=(a+b)/2;self.phase='failed';self.fault='pressure_limit';self.stage='cooling'
    if abs(new-hi)<1e-8 and active:self.phase='failed';self.fault='template_temperature_boundary';self.stage='cooling'
    # Ideal boundary-limited power cutoff; numerical energy ledger remains exact.
    actual_absorbed=max(0.,min(absorbed,self._energy(new)-u+loss));actual_loss=u+actual_absorbed-self._energy(new)
    self.absorbed+=actual_absorbed;self.heat_out+=actual_loss;self.temperature=new
    if self.phase!='running':self.output_w=0.;self.actual_rpm=0.
    if self.phase=='running' and self.stage=='cooling' and new<=self.method['cool_to_k']+1e-8:self.phase='succeeded';self.stage='complete';self.output_w=0.;self.actual_rpm=0.
   self.time_s+=h;left-=h
   if s and self.operation_id and self.time_s>=self.next_record_s-1e-8:
    observed=self._measure();self.history.append(dict(time_s=self.time_s,temperature_k=observed['temperature_k'],pressure_pa=observed['pressure_pa'],microwave_power_w=self.output_w));self.next_record_s=self.time_s+1.
    if len(self.history)>1000:self.history.pop(0);self.history_truncated=True
  self.revision+=1;return self.read('evaluator')
class AntonpaarMonowave400Session:
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
        meta=export_antonpaar_monowave400_contract_metadata()
        return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.1.0',
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

def export_antonpaar_monowave400_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'AntonpaarMonowave400Session.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key=='acquisition.start'; pre=[]
        conditions={
         'acquisition.start':('供电、已装密闭G30、闭盖锁紧、冷却气就绪、选定拟合覆盖程序',['power.enabled','sample.loaded','lid.lift_m','lock.travel_m','gas.ready'],'powered and loaded and lift<=1e-5 and lock>=.009 and gas_ready and chosen_fit_covers_method'),
         'sample.configure':('受信任宿主、空位开盖、真实物性拟合、低温低压安全封装；比热为null时需模板含NIST液相Cp',['sample.loaded','lid.lift_m'],'empty and lift>=.14 and compatible_sealed_g30 and valid_fit and initial_T<=323.15 and initial_P<=120000 and (heat_capacity given or template liquid Cp within 10 K of 298.15 K)'),
         'sample.unload':('受信任宿主、开盖、有样且热压安全',['sample.loaded','lid.lift_m'],'loaded and lift>=.14 and safe'),
         'lid.set_lift':('未运行、未锁紧且热压安全',['cycle.phase','lock.travel_m'],'not running and unlocked and (closed or safe)'),
         'lock.set_travel':('未运行、闭盖可锁、低温低压可解锁',['cycle.phase','lid.lift_m'],'not running and (unlock or lid_closed) and (lock or safe)')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.antonpaar_monowave400.AntonpaarMonowave400Session.invoke',invocation=f'session.invoke(instance_id="monowave400_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；完成升温保温冷却；取消保留热量、相分配和时间记录' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电不回滚已经发生的热量、蒸发和冷却。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。']+(['公开参考数据，不是本瓶状态；CEM数值为厂商表格转录（非开放许可，仅数字），介电数据不决定吸收率。'] if key=='template.read' else []),source_ids=['manufacturer_primary','simulation_assumption']+(LIB_SOURCES if key in ('template.read','sample.configure') else []))
    mechanism=dict(component_ids=['sample_vial','magnetron','cavity','cooling_nozzle','stir_motor','pressure_sensor','ir_sensor'],state_ids=['truth.temperature_k','truth.pressure_pa','truth.vapor_kg','truth.liquid_kg','truth.stored_energy_j','truth.absorbed_energy_j','truth.heat_out_j'],meaning_zh='纯流体有限蒸气库存、固定头空间和理想气体；能量守恒的教学微波热过程',implementation='lumped',equations=['Psat=1e5*10**(A-B/(T+C)) Pa, chosen fit (NIST Antoine, SRD69 refit or composite low-T fit) only in its source interval','mv=min(M,Psat*Vhead*MW/(R*T)); Pv=mv*R*T/(MW*Vhead)','Pabs=Pv+Pnoncond_initial*T/Tinitial','U=C*(T-Tinitial)+Linitial*(mv-mv_initial); C=M*cp+C_vial, cp=host value or NIST liquid Cp(≈298 K) when host passes null','Linitial=R*ln(10)*B*Tinitial**2/(Tinitial+C)**2/MW, derived constant latent proxy','U=absorbed_energy-heat_out; total_mass=liquid_mass+vapor_mass','absorbed=eta*microwave_power*dt; heat_out=UA*(T-298.15)*dt; ideal boundary cutoff','No kinetics: conversion/yield=null and identity unchanged'],parameters={'ambient':dict(value=298.15,unit='K',meaning_zh='工程环境温度',evidence='assumption',source_ids=['simulation_assumption'],uncertainty=None)},source_ids=['manufacturer_primary','simulation_assumption']+LIB_SOURCES,limitations=['真实数据为蒸气压拟合（NIST/SRD69/低温复合）与NIST液相Cp；热时间曲线、吸收率、UA、传感器响应与控制器未实测；2.45GHz介电与CEM密闭管压力仅作参考，不决定吸收功率。','恒定头空间忽略液体热膨胀和汽化导致的液面变化；无混合物活度、放气、化学反应。','压力按绝压代理；3MPa硬上限与理想瞬时切断不代表厂家固件或真实安全性能。','下限范围外不外推，能量边界截断为工程处理。','G4/G10/SiC、MAS24、ruby、Raman、真实密封/破裂/EM场不在本实现范围。'])
    _ann.annotate_export('antonpaar_monowave400',caps,slots,{'microwave_thermal':mechanism},inputs={})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'microwave_thermal':mechanism})

"""Autosamdri815B: pure-CO2 EOS + bounded host-parameter exchange/structure model."""
from __future__ import annotations
import copy,json,math,sys
from asset_runtime import _annotations as _ann
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.antonpaar_abbematadvanced import validate
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/expansion_batch03/t2_vacuum/tousimis_autosamdri815';ASSET_TYPE_ID='instrument.tousimis_autosamdri815';CATALOG_ID='CHEM-027';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/tousimis_autosamdri815_phase_library/templates.json';PSI=6894.757293168;TC=304.1282;PC=7377300.;CHAMBER=25e-6
PHASE={'type':'string','enum':['idle','running','succeeded','cancelled','failed']}
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
def arr(s):return {'type':'array','items':s}
MASS=number('kg',0);TEMP=number('K',243.15,333.15);FRAC=number('1',0,1);PRESS=number('Pa',0)
_CP=None
def cp():
 global _CP
 if _CP is None:
  sys.path.insert(0,str(OUT/'vendor'));import CoolProp.CoolProp as C
  if C.get_global_param_string('version')!='7.2.0':raise RuntimeError('requires pinned local CoolProp7.2.0')
  _CP=C
 return _CP
def props(output,**args):
 pairs=[y for x in args.items() for y in x];return cp().PropsSI(output,*pairs,'CarbonDioxide')
def co2_state(t,rho):
 if rho<=1e-10:return 0.,'empty',None
 p=props('P',T=t,Dmass=rho);q=props('Q',T=t,Dmass=rho);phase=cp().PhaseSI('T',t,'Dmass',rho,'CarbonDioxide');return p,phase,q if 0<=q<=1 else None
PLANT=obj({'ambient_k':number('K',288.15,303.15),'ambient_pa':number('Pa',80000,110000),'tank_mass_kg':number('kg',.001,30),'supply_gauge_pa':number('Pa',750*PSI,900*PSI),'co2_purity':number('1',.998,1),'dip_tube_liquid_confirmed':BOOL,'exhaust_connected':BOOL,'condenser_ready_drain_open':BOOL,'recipe_reviewed':BOOL,'cool_target_k':number('K',268.15,278.15),'thermal_tau_s':number('s',.2,300),'cooling_co2_kg_s':number('kg/s',0,.01),'fill_m3_s':number('m^3/s',1e-9,1e-5),'purge_m3_s':number('m^3/s',1e-9,1e-5),'max_exhaust_kg_s':number('kg/s',1e-7,.01),'heat_thermostat_k':number('K',307.15,311.15),'jt_cooling_k_pa':number('K/Pa',0,1e-5),'pressure_guard_gauge_pa':number('Pa',1200*PSI,1417.5*PSI),'transfer_rate_s_inv':number('1/s',1e-5,10),'max_residual_fraction':number('1',1e-8,.01),'coefficient_evidence':TEXT})
EXAMPLE=dict(ambient_k=295.15,ambient_pa=101325.,tank_mass_kg=1.,supply_gauge_pa=850*PSI,co2_purity=.999,dip_tube_liquid_confirmed=True,exhaust_connected=True,condenser_ready_drain_open=True,recipe_reviewed=True,cool_target_k=273.15,thermal_tau_s=10.,cooling_co2_kg_s=.0001,fill_m3_s=30e-6/60,purge_m3_s=20e-6/60,max_exhaust_kg_s=.001,heat_thermostat_k=309.15,jt_cooling_k_pa=1e-7,pressure_guard_gauge_pa=1400*PSI,transfer_rate_s_inv=.1,max_residual_fraction=.001,coefficient_evidence='synthetic_host_transfer_heat_and_guard_parameters_NOT_real_safety_configuration')
SAMPLE=obj({'sample_id':TEXT,'solid_description':TEXT,'solid_mass_kg':number('kg',1e-9,.1),'solid_volume_m3':number('m^3',1e-10,10e-6),'pore_volume_m3':number('m^3',1e-10,10e-6),'bath_volume_m3':number('m^3',1e-9,20e-6),'solvent_id':{'type':'string','enum':['ethanol','methanol','acetone']},'water_replaced_confirmed':BOOL,'solvent_density_kg_m3':number('kg/m^3',600,1200),'pore_radius_m':number('m',1e-9,1e-3),'contact_cosine':number('1',0,1),'yield_stress_pa':number('Pa',1,1e10),'pore_pressure_tau_s':number('s',.01,300),'damage_rate_s_inv':number('1/s',0,1)})
SAMPLE_EXAMPLE=dict(sample_id='wet_die_1',solid_description='synthetic porous specimen on10mm die; not a calibrated material',solid_mass_kg=.0001,solid_volume_m3=1e-6,pore_volume_m3=.5e-6,bath_volume_m3=10e-6,solvent_id='ethanol',water_replaced_confirmed=True,solvent_density_kg_m3=789.,pore_radius_m=100e-9,contact_cosine=1.,yield_stress_pa=1e6,pore_pressure_tau_s=1.,damage_rate_s_inv=.001)
METHOD=obj({'purge_time_s':number('s',0,2700),'bleed_rate_pa_s':number('Pa/s',100*PSI/60,150*PSI/60)})
METHOD_EXAMPLE=dict(purge_time_s=600.,bleed_rate_pa_s=125*PSI/60)
POINT=obj({'time_s':number('s',0),'stage':TEXT,'temperature_k':TEMP,'pressure_gauge_pa':PRESS,'co2_phase':TEXT})
RESULT=obj({'phase':PHASE,'stage':TEXT,'fault':TEXT,'temperature_k':nullable(TEMP),'pressure_gauge_pa':nullable(PRESS),'pressure_abs_pa':nullable(PRESS),'co2_phase':TEXT,'physical_supercritical':BOOL,'controller_critical_pass':BOOL,'stage_elapsed_s':number('s',0),'evidence':TEXT,'template_source':TEXT})
PRODUCT=obj({'sample_id':TEXT,'solid_mass_kg':MASS,'residual_solvent_kg':MASS,'dry_within_host_threshold':BOOL,'structure_retention_fraction':FRAC,'max_capillary_stress_pa':PRESS,'max_pore_pressure_difference_pa':PRESS,'measured_structure_retention':{'type':'null'},'evidence':TEXT,'template_source':TEXT})
REFERENCE=obj({'substance_id':TEXT,'temperature_k':number('K',1,1000),'surface_tension_n_m':number('N/m',0),'critical_temperature_k':number('K',1,1500),'critical_pressure_pa':PRESS,'saturation_pressure_pa':PRESS,'cpd_solvent_supported':BOOL,'fit_range_verified':BOOL,'source':TEXT,'evidence':TEXT,'template_source':TEXT})
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet'],'电源；断电阀代理关闭但热/孔压继续演化','device_command','agent',{'enabled':True},['power.enabled','cycle.phase']),
 'lid.set_lift':(obj({'lift_m':number('m',0,.065)}),obj({'lift_m':number('m',0,.065)}),['chamber_lid'],'教学可卸高压盖；残压/运行时禁止打开','device_command','agent',{'lift_m':.065},['lid.lift_m']),
 'holder.set_lift':(obj({'lift_m':number('m',0,.035)}),obj({'lift_m':number('m',0,.035)}),['sample_holder'],'盖移开后升降样品架','device_command','agent',{'lift_m':.035},['holder.lift_m']),
 'plant.configure':(PLANT,obj({'configured':BOOL}),['adiabatic_cooler','chamber_heater','pressure_regulator','soter_condenser'],'宿主有限CO2供应和工程热/交换参数；不代表安全认证','configuration','evaluator',EXAMPLE,['plant.configured']),
 'sample.configure':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['sample_holder','chamber'],'已换水的湿片及孔结构参数，一实例一批次','configuration','evaluator',SAMPLE_EXAMPLE,['sample.configured']),
 'method.configure':(METHOD,obj({'configured':BOOL}),['purge_timer','bleed_meter'],'实际秒数与压降速率；非SCFH AIR直接换算','configuration','agent',METHOD_EXAMPLE,['method.configured','method.purge_time_s']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['chamber','chamber_heater','adiabatic_cooler'],'依次预热/冷却/填充/置换/后填充/跨临界/保持/减压/排气','device_command','agent',{},['cycle.phase','cycle.stage']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['chamber'],'取消阀动作但不瞬间泄压，不回滚交换/损伤','device_command','agent',{},['cycle.phase']),
 'cpd.measure':(obj({}),RESULT,['temperature_gauge','pressure_gauge','host_display'],'纯CO2参考相态与设备阶段；物理跨临界不等于控制通过','query','agent',{},[]),
 'product.read':(obj({}),PRODUCT,['sample_holder'],'条件干燥/保真估计，真值限宿主；不伪装实测','query','evaluator',{},[]),
 'history.read':(obj({}),obj({'records':arr(POINT),'truncated':BOOL,'evidence':TEXT}),['host_display'],'阶段与10秒过程记录','query','agent',{},[]),
 'reference.lookup':(obj({'substance_id':TEXT,'temperature_k':number('K',1,1000)}),REFERENCE,['host_display'],'59纯流体表面张力参考，只有3种允许作本模型中间溶剂','query','agent',{'substance_id':'ethanol','temperature_k':298.15},[]),
 'sample.unload':(obj({'sample_id':TEXT}),obj({'unloaded':BOOL,'attachment_event':TEXT}),['sample_holder'],'完成且大气压、开盖升架后宿主取样','configuration','evaluator',{'sample_id':'wet_die_1'},['truth.removed_solid_kg','truth.removed_solvent_kg'])}
SLOTS={}
def slot(k,s,u,o='derived',v='agent',c='host_display',w=None):SLOTS[k]=(s,u,o,v,[c],w)
slot('power.enabled',BOOL,'1','setpoint',c='power_inlet',w=('power.set','enabled'));slot('lid.lift_m',number('m',0,.065),'m','scene',c='chamber_lid',w=('lid.set_lift','lift_m'));slot('holder.lift_m',number('m',0,.035),'m','scene',c='sample_holder',w=('holder.set_lift','lift_m'))
for k,s,u in [('plant.configured',BOOL,'1'),('sample.configured',BOOL,'1'),('method.configured',BOOL,'1'),('method.purge_time_s',number('s',0,2700),'s'),('cycle.phase',PHASE,'1'),('cycle.stage',TEXT,'1'),('cycle.fault',TEXT,'1'),('cycle.elapsed_s',number('s',0),'s'),('cycle.stage_elapsed_s',number('s',0),'s'),('thermal.indicated_k',nullable(TEMP),'K'),('pressure.indicated_gauge_pa',nullable(PRESS),'Pa'),('pressure.indicated_abs_pa',nullable(PRESS),'Pa'),('co2.phase',TEXT,'1'),('critical.physical',BOOL,'1'),('critical.controller_pass',BOOL,'1')]:slot(k,s,u)
for k in ['initial_co2','tank_co2','chamber_co2','exhaust_co2','initial_solvent','bath_solvent','pore_solvent','exhaust_solvent','removed_solvent','initial_solid','retained_solid','removed_solid']:slot('truth.'+k+'_kg',MASS,'kg','process_truth','evaluator','chamber')
class TousimisAutosamdri815Runtime:
 def __init__(self,instance_id='autosamdri_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.lift=0.;self.holder=0.;self.plant=None;self.sample=None;self.method=None;self.phase='idle';self.stage='standby';self.fault='';self.elapsed_s=0.;self.stage_elapsed=0.;self.operation_id=None;self._operation_count=0;self.temperature=295.15;self.pressure=101325.;self.phase_co2='empty';self.quality=None;self.tank=0.;self.mco2=0.;self.co2_out=0.;self.bath=0.;self.pore=0.;self.solvent_out=0.;self.initial_solvent=0.;self.retention=1.;self.pore_pressure=101325.;self.max_capillary=0.;self.max_pore_dp=0.;self.pressure_latched=False;self.controller_pass=False;self.critical_dwell=0.;self.removed=False;self.removed_solvent=0.;self.vent_start_pressure=None;self.history=[];self.history_truncated=False;self.next_sample=0.;self.templates={t['substance_id']:t for t in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return TousimisAutosamdri815Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _ambient(self):return self.plant['ambient_pa'] if self.plant else 101325.
 def _volume(self):return CHAMBER-(self.sample['solid_volume_m3'] if self.sample and not self.removed else 0.)-(self.bath+self.pore)/(self.sample['solvent_density_kg_m3'] if self.sample else 1.)
 def _update_pressure(self):
  if self.mco2<=1e-15:self.pressure=self._ambient();self.phase_co2='empty';self.quality=None;return
  if not hasattr(self,'_eos'):self._eos=cp().AbstractState('HEOS','CarbonDioxide')
  self._eos.update(cp().DmassT_INPUTS,self.mco2/self._volume(),self.temperature);p=self._eos.p();q=self._eos.Q();phase=self._eos.phase();self.phase_co2={cp().iphase_liquid:'liquid',cp().iphase_gas:'gas',cp().iphase_twophase:'twophase',cp().iphase_supercritical:'supercritical',cp().iphase_supercritical_liquid:'supercritical_liquid',cp().iphase_supercritical_gas:'supercritical_gas',cp().iphase_critical_point:'critical_point'}.get(phase,'unknown');self.quality=q if 0<=q<=1 else None;self.pressure=max(self._ambient(),p)
 def _inventory(self):return dict(initial_co2=self.plant['tank_mass_kg'] if self.plant else 0.,tank_co2=self.tank,chamber_co2=self.mco2,exhaust_co2=self.co2_out,initial_solvent=self.initial_solvent,bath_solvent=self.bath,pore_solvent=self.pore,exhaust_solvent=self.solvent_out,removed_solvent=self.removed_solvent,initial_solid=self.sample['solid_mass_kg'] if self.sample else 0.,retained_solid=self.sample['solid_mass_kg'] if self.sample and not self.removed else 0.,removed_solid=self.sample['solid_mass_kg'] if self.sample and self.removed else 0.)
 def _values(self):
  v={'power.enabled':self.powered,'lid.lift_m':self.lift,'holder.lift_m':self.holder,'plant.configured':self.plant is not None,'sample.configured':self.sample is not None,'method.configured':self.method is not None,'method.purge_time_s':self.method['purge_time_s'] if self.method else 0.,'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.fault':self.fault,'cycle.elapsed_s':self.elapsed_s,'cycle.stage_elapsed_s':self.stage_elapsed,'thermal.indicated_k':self.temperature if self.powered else None,'pressure.indicated_gauge_pa':self.pressure-self._ambient() if self.powered else None,'pressure.indicated_abs_pa':self.pressure if self.powered else None,'co2.phase':self.phase_co2,'critical.physical':self.temperature>TC and self.pressure>PC,'critical.controller_pass':self.controller_pass};v.update({'truth.'+k+'_kg':x for k,x in self._inventory().items()});return v
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('evaluator','debug')})
 def debug_snapshot(self):return copy.deepcopy(dict(state=self.read('evaluator'),retention=self.retention,pore_pressure=self.pore_pressure,critical_dwell=self.critical_dwell))
 def _measure(self):return dict(phase=self.phase,stage=self.stage,fault=self.fault,temperature_k=self.temperature if self.powered else None,pressure_gauge_pa=self.pressure-self._ambient() if self.powered else None,pressure_abs_pa=self.pressure if self.powered else None,co2_phase=self.phase_co2,physical_supercritical=self.temperature>TC and self.pressure>PC,controller_critical_pass=self.controller_pass,stage_elapsed_s=self.stage_elapsed,evidence='derived_pure_CO2_reference_EOS_with_synthetic_transfer_and_controls; NOT safety certification',template_source=TEMPLATE_REL)
 def _product(self):
  residual=self.removed_solvent if self.removed else self.bath+self.pore;return dict(sample_id=self.sample['sample_id'] if self.sample else '',solid_mass_kg=self.sample['solid_mass_kg'] if self.sample else 0.,residual_solvent_kg=residual,dry_within_host_threshold=bool(self.phase=='succeeded' and self.plant and residual<=self.initial_solvent*self.plant['max_residual_fraction']),structure_retention_fraction=self.retention,max_capillary_stress_pa=self.max_capillary,max_pore_pressure_difference_pa=self.max_pore_dp,measured_structure_retention=None,evidence='synthetic_host_pore_yield_damage_model; real retention unknown',template_source=TEMPLATE_REL)
 def _reference(self,sid,t):
  q=self.templates.get(sid)
  if not q or not q['conditions']['triple_temperature_k']<t<min(q['values']['surface_correlation_tc_k'],q['values']['critical_temperature_k']):raise ActionRejected('TEMPLATE_RANGE_UNAVAILABLE')
  v=q['values'];sigma=sum(a*(1-t/v['surface_correlation_tc_k'])**n for a,n in zip(v['surface_coefficients_n_m'],v['surface_exponents']));ps=cp().PropsSI('P','T',t,'Q',0,q['name'])
  return dict(substance_id=sid,temperature_k=t,surface_tension_n_m=max(0.,sigma),critical_temperature_k=v['critical_temperature_k'],critical_pressure_pa=v['critical_pressure_pa'],saturation_pressure_pa=ps,cpd_solvent_supported=q['conditions']['cpd_operating_solvent_supported'],fit_range_verified=False,source=q['source'],evidence='derived_literature_pure_liquid_vapor_correlation; empirical fit interval not reverified',template_source=TEMPLATE_REL)
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if self.phase=='running' and CAPS[capability][4]!='query' and capability not in ('power.set','acquisition.stop'):raise ActionRejected('BUSY')
  if capability=='power.set':
   self.powered=a['enabled']
   if not self.powered and self.phase=='running':self.phase='cancelled'
   out=a
  elif capability=='lid.set_lift':
   if self.pressure-self._ambient()>50 or self.phase=='running' or (a['lift_m']<.064 and self.holder>1e-9):raise ActionRejected('PRECONDITION_FAILED')
   self.lift=a['lift_m'];out=a
  elif capability=='holder.set_lift':
   if self.lift<.064 or self.pressure-self._ambient()>50 or self.phase=='running':raise ActionRejected('PRECONDITION_FAILED')
   self.holder=a['lift_m'];out=a
  elif capability=='plant.configure':
   if self.plant or not all(a[k] for k in ['dip_tube_liquid_confirmed','exhaust_connected','condenser_ready_drain_open','recipe_reviewed']) or a['fill_m3_s']<=a['purge_m3_s'] or not a['coefficient_evidence']:raise ActionRejected('PRECONDITION_FAILED')
   cp();self.plant=copy.deepcopy(a);self.tank=a['tank_mass_kg'];self.temperature=a['ambient_k'];self.pressure=self.pore_pressure=a['ambient_pa'];out={'configured':True}
  elif capability=='sample.configure':
   if not self.plant or self.sample or self.lift<.064 or self.holder<.034 or not a['sample_id'] or not a['water_replaced_confirmed'] or sum(a[k] for k in ['solid_volume_m3','pore_volume_m3','bath_volume_m3'])>=CHAMBER*.9:raise ActionRejected('PRECONDITION_FAILED')
   self.sample=copy.deepcopy(a);rho=a['solvent_density_kg_m3'];self.bath=a['bath_volume_m3']*rho;self.pore=a['pore_volume_m3']*rho;self.initial_solvent=self.bath+self.pore;out=dict(configured=True,attachment_event='host_attach:'+a['sample_id'])
  elif capability=='method.configure':
   if self._operation_count:raise ActionRejected('PRECONDITION_FAILED')
   self.method=copy.deepcopy(a);out={'configured':True}
  elif capability=='acquisition.start':
   if not self.powered or not self.plant or not self.sample or not self.method or self.lift>1e-9 or self.holder>1e-9 or self.removed or self.phase not in ('idle','cancelled'):raise ActionRejected('PRECONDITION_FAILED')
   self.phase='running'
   if not self._operation_count:self.stage='warmup';self.stage_elapsed=0.
   self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('PRECONDITION_FAILED')
   self.phase='cancelled';out=dict(phase=self.phase)
  elif capability=='cpd.measure':return self._measure()
  elif capability=='product.read':return self._product()
  elif capability=='history.read':return dict(records=copy.deepcopy(self.history),truncated=self.history_truncated,evidence='derived_EOS_and_synthetic_host_process; not recorded instrument log')
  elif capability=='reference.lookup':return self._reference(a['substance_id'],a['temperature_k'])
  elif capability=='sample.unload':
   if not self.sample or a['sample_id']!=self.sample['sample_id'] or self.removed or self.phase!='succeeded' or self.pressure-self._ambient()>50 or self.lift<.064 or self.holder<.034:raise ActionRejected('PRECONDITION_FAILED')
   self.removed=True;self.removed_solvent=self.bath+self.pore;self.bath=self.pore=0.;self._update_pressure();out=dict(unloaded=True,attachment_event='host_release:'+a['sample_id'])
  self.revision+=1;return copy.deepcopy(out)
 def _record(self):
  if len(self.history)<10000:self.history.append(dict(time_s=self.time_s,stage=self.stage,temperature_k=self.temperature,pressure_gauge_pa=self.pressure-self._ambient(),co2_phase=self.phase_co2))
  else:self.history_truncated=True
 def _next(self,stage):self.stage=stage;self.stage_elapsed=0.;self._record()
 def _fail(self,reason):self.phase='failed';self.fault=reason;self._record()
 def _take(self,m):
  x=min(self.tank,max(0.,m));self.tank-=x;return x
 def _fill(self,h):
  p=self.plant;rho=props('Dmass',T=self.temperature,P=p['ambient_pa']+p['supply_gauge_pa']);needed=max(0.,rho*self._volume()-self.mco2);self.mco2+=self._take(min(needed,p['fill_m3_s']*rho*h))
  if self.tank<=1e-15 and needed>1e-6:self._fail('co2_supply_empty')
 def _exchange(self,h):
  s=self.sample;p=self.plant;bulk_volume=CHAMBER-s['solid_volume_m3']-s['pore_volume_m3'];rho=s['solvent_density_kg_m3'];eq=self.bath/(rho*bulk_volume)*(rho*s['pore_volume_m3']);d=(self.pore-eq)*-math.expm1(-p['transfer_rate_s_inv']*h);d=max(-self.bath,min(self.pore,d));self.pore-=d;self.bath+=d
 def _vent_to(self,target,h,carry=True):
  wanted=props('Dmass',T=self.temperature,P=max(self._ambient(),target))*self._volume();removed=min(max(0.,self.mco2-wanted),self.plant['max_exhaust_kg_s']*h);f=removed/self.mco2 if self.mco2 else 0.;self.mco2-=removed;self.co2_out+=removed
  if carry:x=self.bath*f;self.bath-=x;self.solvent_out+=x
  return removed
 def _tick(self,h):
  p=self.plant;running=self.powered and self.phase=='running';oldp=self.pressure;target=p['ambient_k']
  if running and self.stage in ('cool','fill','purge','postfill'):target=p['cool_target_k']
  if running and self.stage in ('heat','equilibrium','bleed','vent'):target=p['heat_thermostat_k']
  self.temperature+=(target-self.temperature)*-math.expm1(-h/p['thermal_tau_s'])
  if running:
   self.elapsed_s+=h;self.stage_elapsed+=h
   if self.stage=='warmup':
    if self.stage_elapsed>=180:self._next('cool')
   elif self.stage=='cool':
    self.co2_out+=self._take(p['cooling_co2_kg_s']*h)
    if self.tank<=1e-15:self._fail('co2_supply_empty')
    elif abs(self.temperature-p['cool_target_k'])<=.5:self._next('fill')
   elif self.stage in ('fill','postfill'):
    self._fill(h)
    if self.stage_elapsed>=120 and self.phase=='running':self._next('purge' if self.stage=='fill' else 'heat')
   elif self.stage=='purge':
    self._exchange(h);v=CHAMBER-self.sample['solid_volume_m3']-self.sample['pore_volume_m3'];f=min(.2,p['purge_m3_s']*h/v);sol=self.bath*f;co2=self.mco2*f;self.bath-=sol;self.solvent_out+=sol;self.mco2-=co2;self.co2_out+=co2;self._fill(h)
    if self.stage_elapsed>=self.method['purge_time_s'] and self.phase=='running':self._next('postfill')
   elif self.stage in ('heat','equilibrium','bleed','vent'):
    self._exchange(h);self._update_pressure()
    if self.stage in ('heat','equilibrium') and self.pressure>p['ambient_pa']+1350*PSI:self._vent_to(p['ambient_pa']+1350*PSI,h)
    if self.stage=='bleed':self._vent_to(max(p['ambient_pa']+380*PSI,self.pressure-self.method['bleed_rate_pa_s']*h),h)
    if self.stage=='vent':
     targetp=p['ambient_pa']+max(0.,(self.vent_start_pressure-p['ambient_pa'])*(1-self.stage_elapsed/180));self._vent_to(targetp,h)
  self._update_pressure()
  if running and self.stage in ('bleed','vent'):
   self.temperature=max(243.15,self.temperature-p['jt_cooling_k_pa']*max(0.,oldp-self.pressure));self._update_pressure()
  if self.sample and not self.removed:
   s=self.sample;self.pore_pressure+=(self.pressure-self.pore_pressure)*-math.expm1(-h/s['pore_pressure_tau_s']);dp=abs(self.pressure-self.pore_pressure);self.max_pore_dp=max(self.max_pore_dp,dp);cap=0.
   if self.stage in ('bleed','vent','complete'):
    v=self.templates[s['solvent_id']]['values'];gamma=sum(a*max(0.,1-self.temperature/v['surface_correlation_tc_k'])**n for a,n in zip(v['surface_coefficients_n_m'],v['surface_exponents']));residual=min(1.,self.pore/(s['pore_volume_m3']*s['solvent_density_kg_m3']));cap=2*gamma*s['contact_cosine']/s['pore_radius_m']*residual
    if self.quality is not None and 0<self.quality<1:
     co2gamma=.07863*max(0.,1-self.temperature/304.128)**1.254;cap+=2*co2gamma*s['contact_cosine']/s['pore_radius_m']
   self.max_capillary=max(self.max_capillary,cap);self.retention*=math.exp(-s['damage_rate_s_inv']*max(0.,(dp+cap)/s['yield_stress_pa']-1)*h)
  if running and self.phase=='running':
   if self.pressure-p['ambient_pa']>p['pressure_guard_gauge_pa']:self._fail('host_pressure_guard')
   elif self.stage=='heat':
    if self.pressure-p['ambient_pa']>=1200*PSI:self.pressure_latched=True
    if self.temperature>=p['heat_thermostat_k']-.1:
     if not self.pressure_latched:self._fail('critical_signal_order_failure')
     elif self.bath+self.pore>self.initial_solvent*p['max_residual_fraction']:self._fail('insufficient_solvent_exchange')
     elif self.temperature>TC and self.pressure>PC:self.controller_pass=True;self.critical_dwell=0.;self._next('equilibrium')
   elif self.stage=='equilibrium':
    if self.temperature>TC and self.pressure>PC:self.critical_dwell+=h
    else:self.critical_dwell=0.
    if self.critical_dwell>=240:self._next('bleed')
   elif self.stage=='bleed' and self.pressure-p['ambient_pa']<=380*PSI+10:self.vent_start_pressure=self.pressure;self._next('vent')
   elif self.stage=='vent' and self.stage_elapsed>=180 and self.pressure-p['ambient_pa']<=50:self._next('complete');self.phase='succeeded'
  self.time_s+=h
  if self.time_s>=self.next_sample:self._record();self.next_sample=self.time_s+10.
 def step(self,dt):
  if isinstance(dt,bool) or not isinstance(dt,(int,float)) or not math.isfinite(dt) or not 0<dt<=7200:raise ValueError('finite dt in(0,7200] required')
  left=float(dt)
  while left>1e-10:
   h=min(left,1.)
   if self.plant:
    if self.sample:h=min(h,.1*self.sample['pore_pressure_tau_s'],.1*(CHAMBER-self.sample['solid_volume_m3']-self.sample['pore_volume_m3'])/self.plant['purge_m3_s'])
    self._tick(h)
   else:self.time_s+=h
   left-=h
  self.revision+=1;return self.read()
class TousimisAutosamdri815Session:
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
        meta=export_tousimis_autosamdri815_contract_metadata()
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

def export_tousimis_autosamdri815_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'TousimisAutosamdri815Session.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key=='acquisition.start'; pre=[]
        conditions={
         'acquisition.start':('供电、宿主装置样品方法已配置、盖/样品架闭合、未卸样，首次或取消续作',['power.enabled','plant.configured','sample.configured','method.configured','lid.lift_m','holder.lift_m','cycle.phase'],'powered and configured and assembled and resumable'),
         'plant.configure':('首次，液体CO2/通风/SOTER开排液/配方确认，fill>purge',['plant.configured'],'not configured and all_reviewed and fill_above_purge'),
         'sample.configure':('开盖升架，首次，已换水，3种中间溶剂，体积<90%腔容',['sample.configured','lid.lift_m','holder.lift_m'],'open and first and water_replaced and volume_fits'),
         'sample.unload':('完成、大气压附近、开盖升架，样品身份匹配',['cycle.phase','pressure.indicated_gauge_pa','lid.lift_m','holder.lift_m'],'completed and depressurized and open and matching_identity')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.tousimis_autosamdri815.TousimisAutosamdri815Session.invoke',invocation=f'session.invoke(instance_id="autosamdri_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；完整阶段完成且排气后残压<=50Pa教学阈值；取消阀动作但热和孔压继续演化' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电保留CO2、溶剂、结构损伤和残压；热/孔压继续演化；软件阀关闭不等于真实安全停机。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    evolving=[k for k in slots if k.startswith(('truth.','cycle.','thermal.','pressure.','co2.','critical.'))]
    for key in ['acquisition.start','plant.configure','sample.configure','sample.unload']:
        caps[key]['state_effects'] += [dict(state_id=k,timing='on_step' if key=='acquisition.start' else 'immediate',description_zh='保守物料及条件相态/结构模型') for k in evolving if k not in [e['state_id'] for e in caps[key]['state_effects']]]
    for key in ['lid.set_lift','holder.set_lift']:
        caps[key]['preconditions'].append(dict(description_zh='非运行且表压<=50Pa教学残压阈值，样品架升降另需盖移开',state_ids=['cycle.phase','pressure.indicated_gauge_pa','lid.lift_m'],enforced_by='runtime',check='not running and depressurized and mechanical_clearance'))
    caps['reference.lookup']['errors'].append(dict(code='TEMPLATE_RANGE_UNAVAILABLE',meaning_zh='物质或纯流体相态数学范围不可用；完整经验拟合区间未知',side_effects='none'))
    mechanism=dict(component_ids=['chamber','chamber_heater','adiabatic_cooler','pressure_regulator','soter_condenser','sample_holder'],state_ids=evolving,meaning_zh='纯CO2参考EOS与宿主条件溶剂交换/孔结构模型',implementation='lumped',equations=['CO2: HEOS Span-Wagner1996 via pinned CoolProp7.2.0, input T and mass/free_volume -> pressure and phase','free_volume=25mL-solid_volume-(bath_solvent+pore_solvent)/host_solvent_density','tank_CO2_initial=tank_remaining+chamber_CO2+exhaust_CO2','initial_solvent=bath+pore+exhaust+removed; dry_solid_initial=retained+removed','pore_exchange=(pore-bulk_concentration*pore_capacity)*(1-exp(-host_k*dt)); conservative transfer to bath','purge: well mixed bath/CO2 outflow; finite CO2 inflow limited by source pressure and stock','warmup180s,cool to host0C±5C,fill120s,purge0..2700s,postfill120s,heat,critical_equilibrium240s,bleed,vent180s target','P_regulator nominal1350psi gauge; remove mass toward EOS density at regulated P,T,limited by host exhaust rate','controller requires pressure>=1200psi gauge BEFORE thermostat34..38C; physical criterion T>304.1282K and absolute P>7.3773MPa kept separate','bleed target pressure decrease100..150psi/min to380psi gauge,then vent towardambient; actual flux limited by host exhaust conductance proxy','pore_pressure approaches chamber_pressure with host tau; capillary_stress=2*sigma*cos(theta)/radius*residual_solvent_fraction + pureCO2 two-phase term if present','sigma=sum(a_i*(1-T/Tc_surface)^n_i),Mulero2012 reference coefficients; NOT binary liquid mixture surface tension','retention*=exp(-host_damage_rate*max(0,(pore_dP+capillary_stress)/host_yield_stress-1)*dt)'],parameters={},source_ids=['manufacturer_manual','co2_eos','surface_correlation','simulation_assumption'],limitations=['Pure CO2 EOS only; residual solvent treated as volume exclusion,not binary mixture phase equilibrium or a true mixture critical line.','59pure-fluid references contain empirical fit coefficients and calculated surface tension,NOT59measured porous-solid retention results; only ethanol/methanol/acetone allowed as reviewed exchange solvents.','Empirical surface-correlation fit intervals not independently recovered; mathematical triple-to-Tc guards must not be mistaken for validated experimental domains.','Only one wet sample batch per instance; no water/acid loading; arbitrary reference fluids not approved operating fluids.','Initial air displacement and gas dissolution in solvent are neglected; pureCO2 pressure floored atambient when nearly empty,not a full air/CO2 mixture EOS.','Host thermal lag and JT cooling are approximations,not a complete energy balance; source liquid supply confirmed by host,not cylinder phase/scale dynamics.','Pore exchange,solvent entrainment,permeability,contact angle,yield stress and damage rate are host assumptions; true structure retention remains unknown.','FILL/postfill120s and equilibrium240s from manual; warmup180s lower end of3–5min,vent180s reference target not a guaranteed real time.','Cancel closes model active valves but thermal/pressure relaxation and damage continue; no automatic emergency safe vent or real interruption recovery guarantee.','2000psi rupture disc preserved as documentation,not physically simulated or treated as allowable setpoint; hardware safety not certified.','3nuts are visible but lifted with teaching lid; individual loosening/torque/leak seal not modeled; standard5die holder,optional25mmwafer holder absent.','SOTER external proxy geometry has unknown dimensions/thermal efficiency; waste capture bookkeeping,not a full condenser/refrigeration model.','3host digital readouts overlay actual analog gauges/timer; full factory sixLED blinking and buttons not implemented.','Host attach/release events have no robot constraint; gas/vent facilities and hardware protocols absent.'])
    _ann.annotate_export('tousimis_autosamdri815',caps,slots,{'critical_point_drying':mechanism},inputs={'plant.configure':(PLANT,EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'critical_point_drying':mechanism})

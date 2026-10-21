"""Monowave 400: finite-inventory Antoine pressure and explicitly synthetic thermal dynamics.

No reaction kinetics, product yield, electromagnetic field or manufacturer control firmware.
Trusted host establishes sample, gas boundary and mechanical attachment events.
Library v1.1: 72 cold-loadable pure liquids (NIST Antoine, NIST SRD 69 refits, composite low-T fits); real NIST liquid Cp
is used when the host passes heat_capacity_j_kg_k=None; `template.read` exposes the public reference data per substance.
"""
from __future__ import annotations
import copy,hashlib,json,math,os,tempfile
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
# ---------------------------------------------------------------- front panel and its device layer (8.4 in touch screen; panel_ui.json beside the contract)
# From the facility SOP of the Monowave 400 ('sop p.1 step N') and the specification sheet ('spec p.N').  The run screen, the alarm texts
# and the step dialog layout are not in the saved documents (assumptions, listed in the spec).
ASSET_VERSION='1.2.2';SERIAL='MW400-0001'
ACCOUNTS=['Administrator','Operator']                                    # assumption: two accounts; passwords are not modelled
VIALS=['G4','G10','G30','G30 Wide Neck','C10','C30 Wide Neck']           # spec p.12
HEATING=['As fast as possible','In time'];DEFAULT_COOL_C=55.;FAST_RAMP_S=.1
def _enum(xs):return {'type':'string','enum':list(xs)}
NAME40={'type':'string','minLength':1,'maxLength':40};INT0={'type':'integer','x-unit':'1','minimum':0}
EXPERIMENT=obj({'name':NAME40,'type':_enum(['Standard']),'heating':_enum(HEATING),'temperature_c':number('°C',15,300),'heat_time_s':number('s',1,360000),
 'hold_s':number('s',0,360000),'cool_to_c':number('°C',25,300),'stir_rpm':number('rpm',0,1200),'vial':_enum(VIALS),'power_limit_w':number('W',0,850),
 'pressure_limit_bar':number('bar',1,30)})
EXPERIMENT_DEFAULT=dict(type='Standard',heating='As fast as possible',temperature_c=120.,heat_time_s=60.,hold_s=600.,cool_to_c=DEFAULT_COOL_C,stir_rpm=600.,vial='G30',
 power_limit_w=850.,pressure_limit_bar=30.)
STEP_KINDS=['heat_fast','heat_time','hold','cool']
FAULT_TEXT={'pressure_limit':'Pressure limit reached: the heating was stopped.','template_temperature_boundary':'Temperature outside the valid range of the sample data.',
 'cooling_gas_lost':'Cooling air pressure too low.','maximum_program_time':'Maximum program time exceeded.','':''}
ANY={'type':'object'};ARR={'type':'array','items':{'type':'object'}}
CAPS.update({
 'user.logon':(obj({'user':_enum(ACCOUNTS)}),obj({'user':TEXT}),['front_panel'],'登录账户（sop p.1第5步）；未登录不能编辑或启动实验','device_command','agent',{'user':'Operator'},['device.users']),
 'user.logoff':(obj({}),obj({'user':nullable(TEXT)}),['front_panel'],'注销当前账户','device_command','agent',{},['device.users']),
 'experiment.save':(obj({'experiment':EXPERIMENT,'original':nullable(TEXT)},optional=('original',)),obj({'name':TEXT}),['front_panel'],
  '任务列表新建/编辑实验（名称、Standard类型、升温方式与温度/时间、保温、冷却到、搅拌、瓶型、功率与压力上限）','configuration','agent',
  {'experiment':dict(EXPERIMENT_DEFAULT,name='Test 1')},['experiments.list']),
 'experiment.copy':(obj({'name':NAME40}),obj({'name':TEXT}),['front_panel'],'复制实验，新名为<名称>_copy1（sop p.1第7步）','configuration','agent',{'name':'Test 1'},['experiments.list']),
 'experiment.delete':(obj({'name':NAME40}),obj({'deleted':BOOL}),['front_panel'],'删除实验（任务列表）','configuration','agent',{'name':'Test 1'},['experiments.list']),
 'experiment.insert_step':(obj({'name':NAME40,'step':_enum(STEP_KINDS),'temperature_c':number('°C',15,300),'time_s':number('s',0,360000)},optional=('temperature_c','time_s')),
  obj({'name':TEXT}),['front_panel'],'<Insert steps>：加入升温（尽快/按时间）、保温或冷却步骤；程序为一段升温-保温-冷却','configuration','agent',
  {'name':'Test 1','step':'hold','time_s':300.},['experiments.list']),
 'experiment.start':(obj({'name':NAME40}),obj({'phase':PHASE,'operation_id':TEXT}),['front_panel','magnetron','cavity','stir_motor'],
  '启动实验：检查登录、闭盖锁紧、已装G30瓶、冷却气，按实验写入方法后开始；结束时存入数据存储','device_command','agent',{'name':'Test 1'},
  ['run.state','cycle.phase','method.target_k','method.stir_rpm','method.power_limit_w','method.pressure_limit_pa']),
})
DEVICE_CAPS=('user.logon','user.logoff','experiment.save','experiment.copy','experiment.delete','experiment.insert_step','experiment.start')
for _k,_s in [('device.users',ANY),('experiments.list',ARR),('results.list',ARR),('run.state',nullable(ANY))]:slot(_k,_s,'1','setpoint' if _k=='experiments.list' else 'derived',c='front_panel')
_ENG=None
def _engine():
 """The generic front-panel engine (scripts/asset_runtime/_panel_ui.py)."""
 global _ENG
 if _ENG is None:
  from asset_runtime import _panel_ui as e
  _ENG=e
 return _ENG
def _check_new(k,a):
 if not isinstance(a,dict) or _engine().check_arguments(CAPS[k][0],a):raise ActionRejected('INVALID_ARGUMENT')
def steps_text(e):
 """The step list of an experiment as the task list shows it."""
 out=[f'Heat to {e["temperature_c"]:g} °C '+('as fast as possible' if e['heating']=='As fast as possible' else f'in {e["heat_time_s"]:g} s')]
 if e['hold_s']>0:out.append(f'Hold time {e["hold_s"]:g} s')
 out.append(f'Cool down to {e["cool_to_c"]:g} °C');return out
class AntonpaarMonowave400Runtime:
 def __init__(self,instance_id='monowave400_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.lift=0.;self.lock=0.;self.gas=dict(connected=False,pressure_pa=0.,flow_l_min=0.)
  self.phase='idle';self.stage='idle';self.elapsed_s=0.;self.hold_elapsed=0.;self.fault='';self.output_w=0.;self.actual_rpm=0.;self.operation_id=None;self._operation_count=0
  self.sample=None;self.temperature=None;self.absorbed=0.;self.heat_out=0.;self.start_temperature=None;self.history=[];self.history_truncated=False;self.next_record_s=0.
  self.method=copy.deepcopy(CAPS['method.configure'][6]);self.input_kg=0.;self.removed_kg=0.;self.input_m3=0.;self.removed_m3=0.
  self.templates={x['substance_id']:x for x in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
  self.dev=self._new_dev();self.panel_state=None;self.panel_capture_dir=None   # touch-screen device layer (plain data) and the panel UI state
  self._panel_session=None   # the session whose panel gesture is running: its caller rules apply to the commands the gesture calls
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
  r.update(self._dev_values(r))
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
  if capability in DEVICE_CAPS:return self._device_invoke(capability,a)
  if capability.startswith('panel.'):return self._panel_command(capability,a)
  validate(CAPS[capability][0],a)
  if self.phase=='running' and capability not in ('power.set','acquisition.stop','reaction.measure','history.read','product.read','gas.configure','template.read'):raise ActionRejected('BUSY')
  if capability=='power.set':
   was=self.powered;self.powered=a['enabled']
   if self.powered and not was:self.dev['power_epoch']+=1;self.dev['user']=None   # sop p.1 step 5: log in after switching on
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
   self._device_tick()
  self.revision+=1;return self.read('evaluator')
 # ---- device layer of the touch screen
 def _new_dev(self):return dict(user=None,experiments=[],results=[],next_result=1,run=None,power_epoch=0)
 def _dev_values(self,v):
  """Slots of the device layer; v = the instrument values being read (indicated temperature and pressure)."""
  d=self.dev;run=None
  if d['run']:
   r=d['run'];tk=v['thermal.indicated_k'];pa=v['pressure.indicated_pa']
   run=dict(experiment=r['experiment']['name'],user=r['user'],stage=self.stage,phase=self.phase,elapsed_s=round(self.elapsed_s,3),
            temperature_c=None if tk is None else round(tk-273.15,1),pressure_bar=None if pa is None else round(pa/1e5,2),
            power_w=round(self.output_w,1),stir_rpm=round(self.actual_rpm,1),**self._curve(r['t0']))
  last={r['experiment']:r for r in d['results']}                         # Details: graph and conditions of the last run of an experiment (sop p.1 step 8)
  return {'device.users':dict(current=d['user'],accounts=list(ACCOUNTS)),
          'experiments.list':[dict(copy.deepcopy(e),steps=steps_text(e),last_result=copy.deepcopy(last.get(e['name']))) for e in d['experiments']],
          'results.list':copy.deepcopy(d['results']),'run.state':run}
 def _curve(self,t0):
  """Temperature (°C) and pressure (bar) curves of the run for the screen: [[t, value], ...] with at most 300 points; span of the time axis."""
  pts=self.history[::max(1,len(self.history)//300)]
  t=[[round(p['time_s']-t0,3),round(p['temperature_k']-273.15,2)] for p in pts if p['temperature_k'] is not None]
  pr=[[round(p['time_s']-t0,3),round(p['pressure_pa']/1e5,3)] for p in pts if p['pressure_pa'] is not None]
  return dict(curve_t=t,curve_p=pr,span=max(60.,round(self.time_s-t0,3)))
 def _exp(self,name):
  e=next((x for x in self.dev['experiments'] if x['name']==name),None)
  if e is None:raise ActionRejected('NOT_FOUND')
  return e
 def _method_of(self,e):
  return dict(target_k=round(e['temperature_c']+273.15,6),ramp_s=FAST_RAMP_S if e['heating']=='As fast as possible' else float(e['heat_time_s']),hold_s=float(e['hold_s']),
              cool_to_k=round(e['cool_to_c']+273.15,6),stir_rpm=float(e['stir_rpm']),power_limit_w=float(e['power_limit_w']),pressure_limit_pa=float(e['pressure_limit_bar'])*1e5)
 def _device_invoke(self,k,a):
  """Commands of the touch-screen device layer; each checks everything before it changes the state."""
  _check_new(k,a);d=self.dev
  if not self.powered:raise ActionRejected('POWER_OFF')
  if k=='user.logon':d['user']=a['user'];out={'user':a['user']}
  elif k=='user.logoff':
   if d['run']:raise ActionRejected('BUSY')
   d['user']=None;out={'user':None}
  else:
   if d['user'] is None:raise ActionRejected('LOGON_REQUIRED')
   if k=='experiment.save':
    e=copy.deepcopy(a['experiment']);orig=a.get('original')
    if e['cool_to_c']>e['temperature_c']:raise ActionRejected('INVALID_ARGUMENT')
    if orig is not None:self._exp(orig)
    if any(x['name']==e['name'] for x in d['experiments'] if x['name']!=orig):raise ActionRejected('IN_USE')
    if d['run'] and d['run']['experiment']['name'] in (orig,e['name']):raise ActionRejected('BUSY')
    if orig is None:d['experiments'].append(e)
    else:d['experiments'][d['experiments'].index(self._exp(orig))]=e
    out={'name':e['name']}
   elif k=='experiment.copy':
    e=self._exp(a['name']);i=1
    while any(x['name']==f'{a["name"]}_copy{i}' for x in d['experiments']):i+=1
    new=dict(copy.deepcopy(e),name=f'{a["name"]}_copy{i}')                 # sop p.1 step 7: the first copy is named XXX_copy1
    if len(new['name'])>40:raise ActionRejected('INVALID_ARGUMENT')
    d['experiments'].append(new);out={'name':new['name']}
   elif k=='experiment.delete':
    e=self._exp(a['name'])
    if d['run'] and d['run']['experiment']['name']==a['name']:raise ActionRejected('BUSY')
    d['experiments'].remove(e);out={'deleted':True}
   elif k=='experiment.insert_step':
    e=self._exp(a['name']);s=a['step'];new=copy.deepcopy(e)
    if s in ('heat_fast','heat_time','cool') and 'temperature_c' not in a or s in ('heat_time','hold') and 'time_s' not in a:raise ActionRejected('INVALID_ARGUMENT')
    if s=='heat_fast':new.update(heating='As fast as possible',temperature_c=a['temperature_c'])
    elif s=='heat_time':
     if a['time_s']<1:raise ActionRejected('INVALID_ARGUMENT')
     new.update(heating='In time',temperature_c=a['temperature_c'],heat_time_s=a['time_s'])
    elif s=='hold':new['hold_s']=a['time_s']
    else:new['cool_to_c']=a['temperature_c']
    if new['cool_to_c']>new['temperature_c'] or new['cool_to_c']<25:raise ActionRejected('INVALID_ARGUMENT')
    if d['run'] and d['run']['experiment']['name']==a['name']:raise ActionRejected('BUSY')
    d['experiments'][d['experiments'].index(e)]=new;out={'name':a['name']}
   elif k=='experiment.start':
    e=self._exp(a['name'])
    if d['run'] or self.phase=='running':raise ActionRejected('BUSY')
    if self.lift>1e-5 or self.lock<.009:raise ActionRejected('LID_OPEN')
    if not self.sample:raise ActionRejected('NO_VIAL')
    if e['vial']!='G30':raise ActionRejected('VIAL_TYPE')                 # only the sealed G30 vial is modelled
    if not self._gas_ready():raise ActionRejected('NO_COOLING_AIR')
    old=copy.deepcopy(self.method)
    try:
     self.invoke('method.configure',**self._method_of(e));out=self.invoke('acquisition.start')
    except ActionRejected:
     self.method=old;raise
    d['run']=dict(experiment=copy.deepcopy(e),user=d['user'],t0=self.time_s,max_t=None,max_p=None)
   else:raise ActionRejected('UNKNOWN_CAPABILITY')
  self.revision+=1;return copy.deepcopy(out)
 def _device_tick(self):
  """A run ends: its result goes to the data memory (sop p.1 step 8-9: graph and conditions)."""
  d=self.dev;r=d['run']
  if not r:return
  m=self._measure()
  if m['temperature_k'] is not None:r['max_t']=max(r['max_t'] or 0.,m['temperature_k']-273.15)
  if m['pressure_pa'] is not None:r['max_p']=max(r['max_p'] or 0.,m['pressure_pa']/1e5)
  if self.phase=='running':return
  d['results'].append(dict(id=d['next_result'],experiment=r['experiment']['name'],user=r['user'],start_s=round(r['t0'],3),duration_s=round(self.time_s-r['t0'],3),
                           status=self.phase,fault=FAULT_TEXT.get(self.fault,self.fault),max_temperature_c=None if r['max_t'] is None else round(r['max_t'],1),
                           max_pressure_bar=None if r['max_p'] is None else round(r['max_p'],2),conditions=steps_text(r['experiment'])+[f'Stirrer {r["experiment"]["stir_rpm"]:g} rpm',
                           f'Vial {r["experiment"]["vial"]}'],**self._curve(r['t0'])))
  d['next_result']+=1;del d['results'][:-100];d['run']=None
 # ---- front panel: host protocol of the engine and the panel.* commands
 def panel_read(self):
  out=self.read('agent');out.update({'host.powered':self.powered,'host.power_epoch':self.dev['power_epoch'],'host.readout':self._readout()})
  return out
 def _readout(self):
  m=self._measure();d=self.dev
  return dict(user=d['user'] or '',temperature='---' if m['temperature_k'] is None else f'{m["temperature_k"]-273.15:.0f}',pressure='---' if m['pressure_pa'] is None else f'{m["pressure_pa"]/1e5:.1f}',
              power=f'{self.output_w:.0f}',stirrer=f'{self.actual_rpm:.0f}',stage={'idle':'Ready','heating':'Heating','holding':'Holding','cooling':'Cooling','complete':'Finished'}.get(self.stage,self.stage),
              lid='closed' if self.lift<=1e-5 else 'open',locked=self.lock>=.009,vial=self.sample['container_id'] if self.sample else '',cooling_air=self._gas_ready(),
              camera='empty' if not self.sample else ('stirring' if self.actual_rpm>0 else 'vial'),elapsed=f'{int(self.elapsed_s)//60}:{int(self.elapsed_s)%60:02d}',
              last_result=d['results'][-1] if d['results'] else None,fault=FAULT_TEXT.get(self.fault,self.fault))
 def _panel(self):
  p=_engine().PanelUI(PANEL_SPEC,_PanelHost(self))
  if self.panel_state is not None:p.ui=self.panel_state
  return p
 def _panel_command(self,k,a):
  e=_engine()
  if e.check_arguments(CAPS[k][0],a):raise ActionRejected('INVALID_ARGUMENT')
  p=self._panel()
  try:
   if k=='panel.touch':ok=p.touch(a['display_id'],a['x'],a['y'],float(a.get('duration_s',.1)),(a['to_x'],a['to_y']) if 'to_x' in a and 'to_y' in a else None)
   elif k=='panel.switch':ok=p.switch(a['input_id'],a['position'],())
   elif k=='panel.capture':
    data,w,h=p.png_bytes(a['display_id']);self.panel_state=p.ui;sha=hashlib.sha256(data).hexdigest()
    folder=Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir())/'chemistry_sim_panel'/self.instance_id
    folder.mkdir(parents=True,exist_ok=True);path=folder/f'{a["display_id"]}_{sha[:16]}.png'
    if not path.exists():path.write_bytes(data)
    return dict(display_id=a['display_id'],time_s=self.time_s,image=dict(path=str(path),format='png',width=w,height=h,sha256=sha))
   else:
    f=p.frame();self.panel_state=p.ui;return dict(frame=f)
  except e.PanelError:
   self.panel_state=p.ui;raise ActionRejected('INVALID_ARGUMENT') from None
  self.panel_state=p.ui;self.revision+=1;return dict(accepted=bool(ok))
class _PanelHost:
 """The engine's host protocol (panel_read / panel_invoke / panel_time) on top of a runtime; built per gesture."""
 def __init__(self,rt):self.rt=rt
 def panel_read(self):return self.rt.panel_read()
 def panel_invoke(self,capability_id,arguments):
  try:
   if self.rt._panel_session is not None:self.rt._panel_session._guard(capability_id,arguments)   # a gesture made through a session keeps its caller rules
   return self.rt.invoke(capability_id,**arguments)
  except ActionRejected as exc:raise _engine().PanelRejected(str(exc)) from None
 def panel_time(self):return self.rt.time_s
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
        return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,
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
        if capability_id in DEVICE_CAPS or capability_id.startswith('panel.'):_check_new(capability_id,arguments)
        else:validate(CAPS[capability_id][0],arguments)
        key=(self._principal,instance_id,request_id); digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
        if key in h._requests:
            old,result=h._requests[key]
            if old!=digest: raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(result)
        self._guard(capability_id,arguments); h._panel_session=self if capability_id.startswith('panel.') else None
        try: result=h.invoke(capability_id,**arguments)
        finally: h._panel_session=None
        op=result.get('operation_id')
        if op: h._owners[op]=self._principal
        if h.operation_id and h.operation_id not in h._owners: h._owners[h.operation_id]=self._principal   # a run started by a panel gesture
        response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=op,
          status='accepted' if op else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None)
        h._requests[key]=(digest,copy.deepcopy(response)); return response

    def _guard(self,capability_id,arguments):
        """An agent may not stop another agent's running experiment (direct call or panel gesture alike; lead rule 2026-10-19).  The mains switch
        (power.set) is a hard switch and the lid a physical part: never refused; a switch-off cancels the run as a power loss, the lid lock refuses
        opening during a run for everyone."""
        h=self._host; owner=h._owners.get(h.operation_id)
        if self._role=='agent' and h.phase=='running' and owner is not None and owner!=self._principal and capability_id=='acquisition.stop': raise ActionRejected('ACCESS_DENIED')

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
_PANEL_SPEC_PATH=ROOT/'assets/expansion_batch03/t5_automation/antonpaar_monowave400/panel_ui.json'
PANEL_SPEC=_engine().load_spec(_PANEL_SPEC_PATH,capabilities=set(CAPS)|{'panel.touch','panel.switch','panel.capture','panel.frame'}) \
    if _PANEL_SPEC_PATH.is_file() and not os.environ.get('MW_PANEL_BUILD') else None
PANEL_CAPS={k:(d['input_schema'],d['output_schema'],d['component_ids'],d['function_zh'],d['kind'],d['visibility'],d['example_arguments'],[])
            for k,d in (_engine().capability_definitions(PANEL_SPEC).items() if PANEL_SPEC else [])}
CAPS.update(PANEL_CAPS)

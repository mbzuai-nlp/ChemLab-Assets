"""Manual Atlas15T: explicit hand strokes; finite oil and powder; no electric control."""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.pccell_ed64002 import validate
ROOT=Path(__file__).resolve().parents[4] if 'outputs' in str(__file__) else Path(__file__).resolve().parents[2]
SLUG='specac_atlasmanual';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-051';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/specac_atlasmanual_compaction_library/templates.json';TF=9806.65;MAX_LOAD=15*TF;OIL=.000284
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
def nullable(s):return dict(anyOf=[s,dict(type='null')])
PLANT=obj({'hydraulic_area_m2':number('m^2',.0005,.01),'oil_compliance_m3_Pa':number('m^3/Pa',1e-15,1e-11),'pump_stroke_m3':number('m^3',1e-8,5e-6),'stroke_period_s':number('s',.2,5),'internal_leak_m3_Pa_s':number('m^3/Pa/s',0,1e-12),'release_rate_s':number('1/s',.1,10),'frame_stiffness_N_m':number('N/m',1e7,1e10),'compaction_rate_s':number('1/s',.001,10),'gauge_tau_s':number('s',.01,10),'screw_speed_m_s':number('m/s',.001,.05),'screw_pitch_m_rev':number('m',.0005,.01),'guard_speed_m_s':number('m/s',.05,1),'valve_speed_rad_s':number('rad/s',.5,20),'accept_uncalibrated_model':BOOL,'coefficient_evidence':TEXT})
PLANT_EXAMPLE=dict(hydraulic_area_m2=.003,oil_compliance_m3_Pa=1e-13,pump_stroke_m3=1e-6,stroke_period_s=.8,internal_leak_m3_Pa_s=1e-17,release_rate_s=2.,frame_stiffness_N_m=1e9,compaction_rate_s=.2,gauge_tau_s=.1,screw_speed_m_s=.01,screw_pitch_m_rev=.003,guard_speed_m_s=.3,valve_speed_rad_s=10.,accept_uncalibrated_model=True,coefficient_evidence='explicit_host_assumptions_NOT_Atlas_calibration')
METHOD=obj({'target_load_N':number('N',1,MAX_LOAD),'hold_s':number('s',.1,3600),'tolerance_fraction':number('1',.001,.1),'accept_host_observer_not_auto_press':BOOL})
METHOD_EXAMPLE=dict(target_load_N=7*TF,hold_s=12.,tolerance_fraction=.05,accept_host_observer_not_auto_press=True)
SAMPLE=obj({'substance_id':TEXT,'mass_kg':number('kg',1e-7,.02),'binder_id':TEXT,'binder_kg':number('kg',0,.02),'initial_relative_density':number('1',.05,.9),'yield_pressure_Pa':number('Pa',1e6,2e9),'effective_modulus_Pa':number('Pa',1e9,1e12),'die_friction':number('1',0,1),'radial_ratio':number('1',0,1),'accept_reference_scope':BOOL})
SAMPLE_EXAMPLE=dict(substance_id='specac_kbr',mass_kg=.0002,binder_id='specac_kbr',binder_kg=0.,initial_relative_density=.35,yield_pressure_Pa=100e6,effective_modulus_Pa=10e9,die_friction=.1,radial_ratio=.5,accept_reference_scope=True)
DIE=obj({'diameter_m':number('m',.003,.032),'fixed_stack_height_m':number('m',.038,.13),'maximum_load_N':number('N',1,MAX_LOAD),'centered':BOOL,'dry_clean':BOOL,'vacuum_required':BOOL,'accept_geometry_estimate':BOOL})
DIE_EXAMPLE=dict(diameter_m=.013,fixed_stack_height_m=.077,maximum_load_N=10*TF,centered=True,dry_clean=True,vacuum_required=False,accept_geometry_estimate=True)
MEASURE=obj({'phase':PHASE,'stage':TEXT,'indicated_load_N':number('N',0,MAX_LOAD),'hold_elapsed_s':number('s',0),'evidence':TEXT,'template_source':TEXT})
PRODUCT=obj({**{k:number('kg',0) for k in ['initial_sample_kg','initial_binder_kg','remaining_sample_kg','remaining_binder_kg','collected_sample_kg','collected_binder_kg']},'thickness_m':number('m',0),'unloaded_thickness_m':number('m',0),'relative_density':number('1',0,1),'mean_compaction_stress_Pa':number('Pa',0),'oil_total_reference_m3':number('m^3',0),'oil_reservoir_reference_m3':number('m^3',0),'oil_transferred_reference_m3':number('m^3',0),'ram_displaced_m3':number('m^3',0),'oil_compression_reference_m3':number('m^3',0),'pumped_cumulative_m3':number('m^3',0),'returned_cumulative_m3':number('m^3',0),'evidence':TEXT,'template_source':TEXT})
CAPS={
 'plant.configure':(PLANT,obj({'configured':BOOL}),['pump_block'],'显式宿主液压、手泵和压实速率系数','configuration','evaluator',PLANT_EXAMPLE),
 'method.configure':(METHOD,obj({'configured':BOOL}),['gauge_face'],'目标载荷/保压观察条件，不启动自动泵','configuration','agent',METHOD_EXAMPLE),
 'die.configure':(DIE,obj({'configured':BOOL}),['die_body'],'模具与居中/清洁条件；13mm强制10吨上限','configuration','evaluator',DIE_EXAMPLE),
 'sample.configure':(SAMPLE,obj({'configured':BOOL}),['die_body'],'有限粉末及粘结剂与显式基体参数','configuration','evaluator',SAMPLE_EXAMPLE),
 'guard.set_raised':(obj({'side':dict(type='string',enum=['front','rear']),'raised':BOOL}),obj({'raised':BOOL}),['guard_front','guard_rear'],'前后手动罩升降目标，受压时宿主禁止抬罩','device_command','agent',dict(side='front',raised=False)),
 'screw.set_position':(obj({'position_m':number('m',0,.089)}),obj({'position_m':number('m',0,.089)}),['lead_screw'],'上丝杆下行目标，只有step推进','device_command','agent',dict(position_m=.073)),
 'valve.set_open':(obj({'open':BOOL}),obj({'open':BOOL}),['release_valve'],'手动卸压阀目标；缓慢实际开阀后才回油','device_command','agent',dict(open=False)),
 'relief.set_limit':(obj({'load_N':number('N',1,MAX_LOAD)}),obj({'load_N':number('N',1,MAX_LOAD)}),['relief_valve'],'泄压阀宿主标定目标，默认10吨而非15吨','device_command','agent',dict(load_N=7*TF)),
 'vacuum.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['vacuum_port'],'外部抽气状态，非内置真空泵或真空读数','device_command','agent',dict(enabled=False)),
 'pump.stroke':(obj({}),obj({'queued':BOOL}),['pump_handle'],'排队一个完整手动往复行程，不隐式推进时间','device_command','agent',{}),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['gauge_face'],'开始宿主保压观察，必须另行手动泵压','device_command','agent',{}),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['pump_handle'],'停止宿主观察和待执行手泵动作；保留压力','device_command','agent',{}),
 'press.measure':(obj({}),MEASURE,['gauge_face'],'理想滞后载荷表读数，单位N不是油压Pa','query','agent',{}),
 'product.read':(obj({}),PRODUCT,['die_body'],'评价器粉体厚度/回弹及油和物料账本','query','evaluator',{}),
 'product.collect':(obj({'fraction':number('1',0,1),'recipient_id':TEXT,'accept_external_manual_ejection':BOOL}),obj({'transferred_kg':number('kg',0),'recipient_id':TEXT}),['die_body'],'泄压抬罩后外部脱模/有限取片事务，非真实机器人脱模','device_command','evaluator',dict(fraction=1.,recipient_id='external_IR',accept_external_manual_ejection=True)),
 'reference.lookup':(obj({'substance_id':TEXT}),obj({'density_kg_m3':number('kg/m^3',0),'yield_pressure_Pa':nullable(number('Pa',0)),'source':TEXT,'evidence':TEXT,'template_source':TEXT}),['die_body'],'真实/标称材料参考，原证据等级保留','query','agent',dict(substance_id='specac_kbr')),
}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,zh,o='derived',v='agent',c='body',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=zh
for n in ['plant','method','die','sample']:slot(n+'.configured',BOOL,'1',n+'宿主配置','setpoint')
for n in ['front','rear']:
 slot('guard_'+n+'.raised_target',BOOL,'1','防护罩升起目标','setpoint',c='guard_'+n);slot('guard_'+n+'.lift_m',number('m',0,.18),'m','照片估计罩升程','scene',c='guard_'+n)
slot('screw.target_m',number('m',0,.089),'m','丝杆下行目标','setpoint',c='lead_screw',w=('screw.set_position','position_m'));slot('screw.position_m',number('m',0,.089),'m','丝杆实际下行','scene',c='lead_screw');slot('screw.angle_rad',number('rad',0),'rad','螺距假设对应手轮转角','scene',c='handwheel');slot('ram.lift_m',number('m',0,.0254),'m','下活塞升程','scene',c='ram');slot('pump.angle_rad',number('rad',0,.8),'rad','手泵杆实际往复角','scene',c='pump_handle');slot('pump.active',BOOL,'1','单次手泵行程是否尚未完成');slot('valve.open_target',BOOL,'1','卸压目标','setpoint',c='release_valve',w=('valve.set_open','open'));slot('valve.angle_rad',number('rad',0,math.tau),'rad','卸压手柄转角代理','scene',c='release_valve');slot('relief.limit_n',number('N',1,MAX_LOAD),'N','宿主泄压上限','setpoint',c='relief_valve',w=('relief.set_limit','load_N'));slot('relief.angle_rad',number('rad',0,2.4),'rad','泄压调节角度非厂家标定','scene',c='relief_valve');slot('vacuum.enabled',BOOL,'1','外部抽气状态','setpoint',c='vacuum_port',w=('vacuum.set','enabled'))
slot('load.indicated_n',number('N',0,MAX_LOAD),'N','理想滞后载荷表，非液压压力','derived',c='gauge_face');slot('gauge.angle_rad',number('rad',-2.356194490192345,2.356194490192345),'rad','0–15吨圆表指针','scene',c='gauge_pointer');slot('cycle.phase',PHASE,'1','宿主保压观察状态');slot('cycle.stage',TEXT,'1','手动加压/观察阶段');slot('cycle.hold_s',number('s',0),'s','带内连续保压计时')
slot('truth.force_n',number('N',0,MAX_LOAD),'N','实际模型载荷真值','process_truth','evaluator','ram');slot('truth.hydraulic_pressure_pa',number('Pa',0),'Pa','相对油箱的液压表压真值，不是表盘吨数','process_truth','evaluator','pump_block');slot('truth.ledger',PRODUCT,'1','有限质量、油库存和压实结果','process_truth','evaluator','die_body')
class SpecacAtlasmanualRuntime:
 def __init__(self,instance_id='atlas_01'):
  self.instance_id=instance_id;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.plant=self.method=self.die=self.sample=None;self.guards={'front':True,'rear':False};self.guard_lifts={'front':.18,'rear':0.};self.screw_target=self.screw=self.ram=self.pump_angle=0.;self.valve_open=True;self.valve_angle=math.tau;self.relief_limit=10*TF;self.relief_angle=1.6;self.vacuum=False;self.pump_active=False;self.stroke_elapsed=0.;self.force=self.indicated=0.;self.oil=self.pumped=self.returned=0.;self.phi=self.initial_height=self.solid_volume=0.;self.collected_s=self.collected_b=0.;self.phase='idle';self.stage='idle';self.hold_s=self.time_s=0.;self.revision=0;self.operation_id='';self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility):
  if visibility not in ['agent','debug','evaluator'] or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return SpecacAtlasmanualSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def area(self):return math.pi*self.die['diameter_m']**2/4 if self.die else 1.
 def height(self,F=0.):return self.solid_volume/(self.area()*self.phi)/(1+F/(self.area()*self.sample['effective_modulus_Pa'])) if self.sample else 0.
 def mean_stress(self):
  if not self.sample:return 0.
  x=4*self.sample['die_friction']*self.sample['radial_ratio']*self.height(self.force)/self.die['diameter_m'];return self.force/self.area()*(-math.expm1(-x)/x if x else 1.)
 def gap(self):return .152-self.screw-self.die['fixed_stack_height_m']-self.initial_height if self.die else .152-self.screw
 def _resolve(self):
  p=self.plant;A=p['hydraulic_area_m2'];C=p['oil_compliance_m3_Pa'];maxx=min(.0254,.152-self.screw-.038);gap=max(0.,self.gap())
  if not self.sample or self.oil<=A*gap:
   self.ram=min(maxx,self.oil/A);self.force=0.;excess=max(0.,self.oil-A*self.ram)
   if excess>1e-14:self.phase='failed';self.stage='stroke_limit';self.pump_active=False;self.oil-=excess;self.returned+=excess
   return
  def x(F):return gap+self.initial_height-self.height(F)+F/p['frame_stiffness_N_m']
  def volume(F):return A*x(F)+C*F/A
  cap=min(MAX_LOAD,self.relief_limit,self.die['maximum_load_N']);limit_hit=False
  if x(cap)>maxx:
   lo=0.;hi=cap
   for _ in range(45):
    mid=(lo+hi)/2
    if x(mid)>maxx:hi=mid
    else:lo=mid
   cap=lo;limit_hit=True
  if self.oil>volume(cap):
   d=self.oil-volume(cap);self.oil-=d;self.returned+=d
   if limit_hit:self.phase='failed';self.stage='stroke_limit';self.pump_active=False
  lo=0.;hi=cap
  for _ in range(45):
   mid=(lo+hi)/2
   if volume(mid)>self.oil:hi=mid
   else:lo=mid
  self.force=(lo+hi)/2;self.ram=x(self.force)
  # Plastic shortening can exceed the volume available, leaving a free gap.
  if volume(0.)>self.oil:self.force=0.;self.ram=self.oil/A
 def _product(self):
  s=self.sample or {};p=self.plant or {};pressure=self.force/p['hydraulic_area_m2'] if p else 0.;return dict(initial_sample_kg=s.get('mass_kg',0.),initial_binder_kg=s.get('binder_kg',0.),remaining_sample_kg=s.get('mass_kg',0.)-self.collected_s,remaining_binder_kg=s.get('binder_kg',0.)-self.collected_b,collected_sample_kg=self.collected_s,collected_binder_kg=self.collected_b,thickness_m=self.height(self.force),unloaded_thickness_m=self.height(),relative_density=self.phi,mean_compaction_stress_Pa=self.mean_stress(),oil_total_reference_m3=OIL,oil_reservoir_reference_m3=OIL-self.oil,oil_transferred_reference_m3=self.oil,ram_displaced_m3=self.ram*p.get('hydraulic_area_m2',0.),oil_compression_reference_m3=pressure*p.get('oil_compliance_m3_Pa',0.),pumped_cumulative_m3=self.pumped,returned_cumulative_m3=self.returned,evidence='synthetic_quasistatic_compact;source_density_conditioned;NOT_measured_strength_optics_or_calibrated_press',template_source=TEMPLATE_REL)
 def _values(self):
  v={'plant.configured':bool(self.plant),'method.configured':bool(self.method),'die.configured':bool(self.die),'sample.configured':bool(self.sample),'screw.target_m':self.screw_target,'screw.position_m':self.screw,'screw.angle_rad':self.screw/(self.plant['screw_pitch_m_rev'] if self.plant else .003)*math.tau,'ram.lift_m':self.ram,'pump.angle_rad':self.pump_angle,'pump.active':self.pump_active,'valve.open_target':self.valve_open,'valve.angle_rad':self.valve_angle,'relief.limit_n':self.relief_limit,'relief.angle_rad':self.relief_angle,'vacuum.enabled':self.vacuum,'load.indicated_n':self.indicated,'gauge.angle_rad':-3*math.pi/4+1.5*math.pi*self.indicated/MAX_LOAD,'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.hold_s':self.hold_s,'truth.force_n':self.force,'truth.hydraulic_pressure_pa':self.force/self.plant['hydraulic_area_m2'] if self.plant else 0.,'truth.ledger':self._product()}
  for n in self.guards:v['guard_'+n+'.raised_target']=self.guards[n];v['guard_'+n+'.lift_m']=self.guard_lifts[n]
  return v
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ['debug','evaluator']})
 def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k!='templates'})
 def _ready(self):return bool(self.plant and self.method and self.sample and self.die and all(not x for x in self.guards.values()) and all(x<1e-9 for x in self.guard_lifts.values()) and not self.valve_open and self.valve_angle<1e-9 and abs(self.screw-self.screw_target)<1e-9 and self.gap()>=-1e-9 and (not self.die['vacuum_required'] or self.vacuum))
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if self.phase=='running' and capability in ['plant.configure','method.configure','die.configure','sample.configure','product.collect']:raise ActionRejected('BUSY')
  if capability=='plant.configure':
   if self.plant or not a['accept_uncalibrated_model'] or a['hydraulic_area_m2']*.0254+a['oil_compliance_m3_Pa']*MAX_LOAD/a['hydraulic_area_m2']>OIL:raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='method.configure':
   if self.phase!='idle' or not a['accept_host_observer_not_auto_press']:raise ActionRejected('PRECONDITION_FAILED')
   self.method=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='die.configure':
   if self.die or self.sample or not all(a[k] for k in ['centered','dry_clean','accept_geometry_estimate']) or self.guard_lifts['front']<.179 or abs(a['diameter_m']-.013)<1e-8 and a['maximum_load_N']>10*TF or abs(a['diameter_m']-.010)<1e-8 and a['maximum_load_N']>5*TF:raise ActionRejected('PRECONDITION_FAILED')
   self.die=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='sample.configure':
   if not self.plant or not self.die or self.sample or self.phase!='idle' or self.guard_lifts['front']<.179 or not a['accept_reference_scope']:raise ActionRejected('PRECONDITION_FAILED')
   if a['substance_id'] not in self.templates or a['binder_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   vs=a['mass_kg']/self.templates[a['substance_id']]['value']+a['binder_kg']/self.templates[a['binder_id']]['value'];h=vs/(self.area()*a['initial_relative_density'])
   if h+self.die['fixed_stack_height_m']>.152-self.screw:raise ActionRejected('CAPACITY_LIMIT')
   self.sample=copy.deepcopy(a);self.solid_volume=vs;self.phi=a['initial_relative_density'];self.initial_height=h;out=dict(configured=True)
  elif capability=='guard.set_raised':
   if a['raised'] and (self.force>10 or self.pump_active or self.phase=='running'):raise ActionRejected('PRECONDITION_FAILED')
   self.guards[a['side']]=a['raised'];out=dict(raised=a['raised'])
  elif capability=='screw.set_position':
   if self.force>10 or self.pump_active or self.ram>1e-6 or self.phase=='running' or self.sample and .152-a['position_m']<self.die['fixed_stack_height_m']+self.initial_height:raise ActionRejected('PRECONDITION_FAILED')
   self.screw_target=a['position_m'];out=a
  elif capability=='valve.set_open':self.valve_open=a['open'];out=a
  elif capability=='relief.set_limit':
   if self.force>10 or self.pump_active:raise ActionRejected('PRECONDITION_FAILED')
   self.relief_limit=a['load_N'];out=a
  elif capability=='vacuum.set':
   if not a['enabled'] and self.phase=='running' and self.die['vacuum_required']:raise ActionRejected('PRECONDITION_FAILED')
   self.vacuum=a['enabled'];out=a
  elif capability=='pump.stroke':
   if not self._ready() or self.pump_active or self.phase not in ['idle','running']:raise ActionRejected('PRECONDITION_FAILED')
   self.pump_active=True;self.stroke_elapsed=0.;out=dict(queued=True)
  elif capability=='acquisition.start':
   if not self._ready() or self.phase!='idle' or self.method['target_load_N']>min(self.relief_limit,self.die['maximum_load_N']):raise ActionRejected('PRECONDITION_FAILED')
   self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);self.phase='running';self.stage='awaiting_manual_pump';out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('PRECONDITION_FAILED')
   self.phase='cancelled';self.stage='cancelled_under_load' if self.force>10 else 'cancelled';self.pump_active=False;out=dict(phase=self.phase)
  elif capability=='press.measure':return dict(phase=self.phase,stage=self.stage,indicated_load_N=self.indicated,hold_elapsed_s=self.hold_s,evidence='synthetic_ideal_lagged_force_gauge;NOT_pressure_or_electronic_sensor',template_source=TEMPLATE_REL)
  elif capability=='product.read':return self._product()
  elif capability=='product.collect':
   if self.phase!='succeeded' or self.force>10 or self.ram>1e-6 or self.guard_lifts['front']<.179 or not a['accept_external_manual_ejection']:raise ActionRejected('PRECONDITION_FAILED')
   ms=(self.sample['mass_kg']-self.collected_s)*a['fraction'];mb=(self.sample['binder_kg']-self.collected_b)*a['fraction'];self.collected_s+=ms;self.collected_b+=mb;out=dict(transferred_kg=ms+mb,recipient_id=a['recipient_id'])
  elif capability=='reference.lookup':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   r=self.templates[a['substance_id']];return dict(density_kg_m3=r['value'],yield_pressure_Pa=r['yield_pressure_Pa'],source=r['source'],evidence=r['evidence'],template_source=TEMPLATE_REL)
  self.revision+=1;return copy.deepcopy(out)
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or not 0<=dt_s<=7200:raise ActionRejected('INVALID_ARGUMENT')
  left=float(dt_s)
  while left>1e-10:
   h=min(.02,left);p=self.plant
   if p:
    for n in self.guards:
     old=self.guard_lifts[n];target=.18 if self.guards[n] else 0.;self.guard_lifts[n]=old+max(-p['guard_speed_m_s']*h,min(p['guard_speed_m_s']*h,target-old))
    self.screw+=max(-p['screw_speed_m_s']*h,min(p['screw_speed_m_s']*h,self.screw_target-self.screw));self.valve_angle+=max(-p['valve_speed_rad_s']*h,min(p['valve_speed_rad_s']*h,(math.tau if self.valve_open else 0.)-self.valve_angle));self.relief_angle+=max(-h*2,min(h*2,2.4*self.relief_limit/MAX_LOAD-self.relief_angle))
    if self.pump_active:
     period=p['stroke_period_s'];old=self.stroke_elapsed;end=min(period,old+h);delivered=max(0.,min(end,period/2)-min(old,period/2))*2/period*p['pump_stroke_m3'];dv=min(delivered,OIL-self.oil);self.oil+=dv;self.pumped+=dv;self.stroke_elapsed=end;self.pump_angle=.8*math.sin(math.pi*end/period)
     if end>=period-1e-9:self.pump_active=False;self.pump_angle=0.
    else:self.pump_angle=max(0.,self.pump_angle-h*2)
    # Release and internal bypass return the same oil to the finite reservoir.
    pressure=self.force/p['hydraulic_area_m2'];release=self.oil*(-math.expm1(-p['release_rate_s']*self.valve_angle/math.tau*h));leak=pressure*p['internal_leak_m3_Pa_s']*h;dv=min(self.oil,release+leak);self.oil-=dv;self.returned+=dv;self._resolve()
    if self.sample and self.force>0:
     target=1-(1-self.sample['initial_relative_density'])*math.exp(-self.mean_stress()/self.sample['yield_pressure_Pa']);self.phi+=max(0.,target-self.phi)*(-math.expm1(-p['compaction_rate_s']*h));self.phi=min(1-1e-12,self.phi);self._resolve()
    self.indicated+=(self.force-self.indicated)*(-math.expm1(-h/p['gauge_tau_s']));self.indicated=max(0.,min(MAX_LOAD,self.indicated))
    if self.phase=='running':
     target=self.method['target_load_N'];tol=self.method['tolerance_fraction']
     if abs(self.force-target)<=target*tol:self.hold_s=min(self.method['hold_s'],self.hold_s+h);self.stage='holding'
     else:self.hold_s=0.;self.stage='awaiting_manual_pump'
     if self.hold_s>=self.method['hold_s']-1e-9:self.phase='succeeded';self.stage='hold_complete_still_loaded';self.pump_active=False
   self.time_s+=h;left-=h;self.revision+=1

class SpecacAtlasmanualSession:
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
        meta=export_specac_atlasmanual_contract_metadata()
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
        if op: h._owners.setdefault(op,self._principal)
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('press.measure'))

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])





def export_specac_atlasmanual_contract_metadata():
 caps={};slots={}
 for k,(schema,unit,origin,visibility,components,write) in SLOTS.items():slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'SpecacAtlasmanualSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
 immediate={'plant.configure':['plant.configured','screw.angle_rad'],'method.configure':['method.configured'],'die.configure':['die.configured'],'sample.configure':['sample.configured','truth.ledger'],'guard.set_raised':['guard_front.raised_target','guard_rear.raised_target'],'screw.set_position':['screw.target_m'],'valve.set_open':['valve.open_target'],'relief.set_limit':['relief.limit_n'],'vacuum.set':['vacuum.enabled'],'pump.stroke':['pump.active'],'acquisition.start':['cycle.phase','cycle.stage'],'acquisition.stop':['cycle.phase','cycle.stage','pump.active'],'product.collect':['truth.ledger']}
 evolving=[k for k in slots if k not in ['plant.configured','method.configured','die.configured','sample.configured','guard_front.raised_target','guard_rear.raised_target','screw.target_m','valve.open_target','relief.limit_n','vacuum.enabled']]
 for k,(inp,out,components,meaning,kind,vis,example) in CAPS.items():
  async_=k=='acquisition.start';query=kind=='query';caps[k]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.specac_atlasmanual.SpecacAtlasmanualSession.invoke',invocation=f'session.invoke(instance_id="atlas_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if query else [dict(description_zh='显式手泵动作、居中干模具、前后护罩、38mm联合间距、行程/额定/模具/泄压限值和有限库存守卫',state_ids=['cycle.phase','truth.force_n','guard_front.lift_m','guard_rear.lift_m'],enforced_by='runtime',check='SpecacAtlasmanualRuntime.invoke guards')],state_effects=[] if query else [dict(state_id=i,timing='immediate',description_zh='配置/目标/有限取片事务') for i in immediate.get(k,[])]+[dict(state_id=i,timing='on_step',description_zh='手动行程才泵压，step推进油量/压实/回弹/机械') for i in evolving],observes=[],model_effects=dict(mode='none',description_zh='实际求值后升格',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='宿主监测连续带内保压时间结束，仍带压，须另行泄压脱模' if async_ else '目标/排队/查询事务完成，stroke排队不等于行程结束',completion_state_ids=['cycle.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['finite_powder_binder','finite_oil','host_clock','principal_request_cache'],partial_failure_zh='取消保留油压与不可逆压实，不自动泄压或回滚粉末'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT','UNKNOWN_REFERENCE','CAPACITY_LIMIT']],example_arguments=copy.deepcopy(example),verification=[],limitations=['手动压机，无内置电控/温控/真空泵；过程/安全守卫为宿主近似，非厂家认证'],source_ids=['manufacturer_primary','simulation_assumption'])
 mech=dict(component_ids=['pump_block','pump_handle','ram','lead_screw','die_body','gauge_pointer'],state_ids=list(slots),meaning_zh='有限手泵油量、准静态接触/油压、黏塑性压实、卸载回弹及机械载荷表',implementation='lumped',equations=['1tonne-force=9806.65N;15T=147099.75N;13mm die max10T,default relief10T','V_oil_transferred=A_hydraulic*x_ram+C_oil*P_gauge;F=A_hydraulic*P_gauge','Oil_total=reservoir+transferred_reference;transferred=pumped-returned','x_contact=gap_initial+h_initial-h_unloaded/(1+F/(A_die*E_host))+F/K_frame','phi_eq=1-(1-phi_initial)*exp(-mean_stress/Py_host);phi increases at explicit host rate','mean_stress=F/A_die*(1-exp(-4mu*Kr*h/d))/(4mu*Kr*h/d)','h_unloaded=(m_sample/rho_ref+m_binder/rho_binder_ref)/(A_die*phi);elastic compression recovers on unloading','only explicitly queued stroke delivers finite oil;target/load observer never pumps','mass_initial=mass_remaining+mass_collected;no chemical reaction or mass loss'],parameters={},source_ids=['manufacturer_primary','manufacturer_die','nist_density','compaction_paper','simulation_assumption'],limitations=['55种参考中50种为NIST公开标称假设，4种文献实测密度和1种厂家KBr密度；不是50实测Atlas压实曲线。','4种文献拟合来自StylOne Evolution11.28mm润滑模具、20mm/s、最大400MPa，不能冒充本机慢压/回弹标定。','Heckel型动力学、油顺应性、泵排量、泄漏、摩擦、弹性模量和表针滞后显式未标定。','有效液压面积不是86mm压面面积；油压Pa和表盘吨力N分开。','负载目标和保压是宿主观察，机器不会自动打压。','保压成功仍带压；本模型不替代安全操作，外部脱模逻辑不等于机器人动作。','无能量/发热闭合模型，不声称热守恒或自带温控。','未模拟颗粒离散接触、裂纹/粘结强度、透明度、含水、真空传质、红外/XRF输出。','guard/sliding/screw pitch/gauge arc是照片代理；真实硬件和安全认证未验证。'])
 spec=importlib.util.spec_from_file_location('_atlas_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'compaction':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.configure':(SAMPLE,SAMPLE_EXAMPLE)});mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'compaction':mech})

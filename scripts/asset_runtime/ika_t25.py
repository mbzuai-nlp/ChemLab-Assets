"""T25 digital/N18G: finite two-phase ledger, shear-scale and thermal teaching model."""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.pccell_ed64002 import validate
ROOT=Path(__file__).resolve().parents[2];SLUG='ika_t25';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-043';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/ika_t25_dispersion_library/templates.json'
MIN_SPEED=3000*math.tau/60;MAX_SPEED=25000*math.tau/60;ROTOR_D=.0127;GAP=.0003;VESSEL_R=.06;AREA=math.pi*VESSEL_R**2;BASE_VOLUME=.001;BASE_IMMERSION=.06;DRIVE_Z=.02+BASE_VOLUME/AREA-BASE_IMMERSION+.194
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
def array(s,n=None):return dict(type='array',items=s,minItems=n or 0,**({'maxItems':n} if n else {}))
PLANT=obj({'acceleration_rad_s2':number('rad/s^2',1000,10000),'lift_velocity_m_s':number('m/s',.001,.1),'power_number':number('1',0,100),'viscous_coefficient':number('1',0,100),'heat_fraction':number('1',0,1),'vessel_heat_capacity_J_K':number('J/K',0,5000),'cooling_W_K':number('W/K',0,100),'ambient_K':number('K',278.15,313.15),'breakup_coefficient':number('1',0,.001),'critical_shear_s':number('1/s',0,100000),'weber_coefficient':number('1',0,100),'minimum_size_m':number('m',1e-8,1e-3),'reviewed_compatible_nonexplosive':BOOL,'coefficient_evidence':TEXT})
PLANT_EXAMPLE=dict(acceleration_rad_s2=2000.,lift_velocity_m_s=.03,power_number=2.,viscous_coefficient=5.,heat_fraction=.8,vessel_heat_capacity_J_K=100.,cooling_W_K=1.,ambient_K=298.15,breakup_coefficient=1e-5,critical_shear_s=1000.,weber_coefficient=1.,minimum_size_m=2e-6,reviewed_compatible_nonexplosive=True,coefficient_evidence='host_teaching_coefficients_not_factory_calibration')
METHOD=obj({'speed_rad_s':number('rad/s',MIN_SPEED,MAX_SPEED),'duration_s':number('s',.1,55),'head':dict(type='string',const='s25_n18g')})
METHOD_EXAMPLE=dict(speed_rad_s=15000*math.tau/60,duration_s=5.,head='s25_n18g')
SAMPLE=obj({'sample_id':TEXT,'carrier_id':TEXT,'carrier_mass_kg':number('kg',1e-6,5),'carrier_density_kg_m3':number('kg/m^3',100,3000),'dispersed_label':TEXT,'dispersed_mass_kg':number('kg',0,5),'dispersed_density_kg_m3':number('kg/m^3',100,30000),'dispersed_cp_J_kg_K':number('J/(kg*K)',1,10000),'kind':dict(type='string',enum=['emulsion','suspension']),'viscosity_Pa_s':number('Pa*s',1e-6,5),'interfacial_tension_N_m':number('N/m',1e-6,1),'initial_size_m':number('m',1e-7,.005),'temperature_K':number('K',278.15,453.15),'material_evidence':TEXT})
def example_sample():return dict(sample_id='host_emulsion',carrier_id='water',carrier_mass_kg=.9,carrier_density_kg_m3=1000.,dispersed_label='hypothetical_oil',dispersed_mass_kg=.08,dispersed_density_kg_m3=800.,dispersed_cp_J_kg_K=2000.,kind='emulsion',viscosity_Pa_s=.01,interfacial_tension_N_m=.03,initial_size_m=.0002,temperature_K=298.15,material_evidence='host_assumption_except_NIST_water_Cp')
MEASURE=obj({'phase':PHASE,'mode':TEXT,'indicated_speed_rad_s':number('rad/s',0,MAX_SPEED),'host_elapsed_s':number('s',0),'host_remaining_s':number('s',0),'evidence':TEXT,'template_source':TEXT})
PRODUCT=obj({'sample_id':TEXT,'carrier_id':TEXT,'kind':TEXT,'mass_kg':array(number('kg',0),2),'collected_mass_kg':array(number('kg',0),2),'initial_mass_kg':array(number('kg',0),2),'temperature_K':number('K',0),'size_m':number('m',0),'volume_m3':number('m^3',0),'shaft_energy_J':number('J',0),'heat_to_sample_J':number('J',0),'heat_to_ambient_J':number('J'),'thermal_storage_J':number('J'),'evidence':TEXT,'template_source':TEXT})
CAPS={
 'plant.configure':(PLANT,obj({'configured':BOOL}),['drive','rotor','vessel'],'宿主提供负载/传热/破碎系数与环境审查','configuration','evaluator',PLANT_EXAMPLE),
 'method.configure':(METHOD,obj({'configured':BOOL}),['drive','knob_speed'],'标准N18G、转速及宿主停机计划，非内置定时','configuration','agent',METHOD_EXAMPLE),
 'sample.configure':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['vessel','stator'],'宿主配置有限液体和分散相，非机器人加料','configuration','evaluator',example_sample()),
 'mount.configure':(obj({'head_locked':BOOL,'vessel_clamped':BOOL}),obj({'ready':BOOL}),['cross_sleeve','vessel_clamp','stator'],'宿主检查头已固定、容器夹紧；无夹紧传感器','configuration','evaluator',dict(head_locked=True,vessel_clamped=True)),
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_cable','drive'],'宿主供电状态；断电取消，复电不重启','device_command','agent',dict(enabled=True)),
 'motor.set_speed':(obj({'speed_rad_s':number('rad/s',MIN_SPEED,MAX_SPEED)}),obj({'speed_rad_s':number('rad/s',MIN_SPEED,MAX_SPEED)}),['knob_speed','drive'],'速度旋钮只改目标，step有限加速','device_command','agent',dict(speed_rad_s=12000*math.tau/60)),
 'mount.set_immersion':(obj({'immersion_m':number('m',.055,.165)}),obj({'immersion_m':number('m',.055,.165)}),['drive','cross_sleeve'],'停转后手工升降目标，55mm保守最小浸深','device_command','agent',dict(immersion_m=.065)),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['switch_run','drive','rotor'],'启动转子定子分散宿主异步作业','device_command','agent',{}),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['switch_run','rotor'],'取消保留效果，转子有限减速仍有耗散','device_command','agent',{}),
 'motor.toggle':(obj({}),obj({'phase':PHASE}),['switch_run','rotor'],'面板开关事件，启动或取消；不是原厂电气驱动','device_command','agent',{}),
 'dispersion.measure':(obj({}),MEASURE,['display_digits','drive'],'理想速度量化显示和宿主计时；不泄露温度/粒径真值','query','agent',{}),
 'product.read':(obj({}),PRODUCT,['vessel','rotor'],'评价器质量、温度、粒径尺度及能量账本','query','evaluator',{}),
 'product.collect':(obj({'fraction':number('1',0,1),'recipient_id':TEXT}),obj({'transferred_kg':number('kg',0),'recipient_id':TEXT}),['vessel'],'停转后均匀样品有限转移，非机器人倾倒','device_command','evaluator',dict(fraction=.25,recipient_id='receiver')),
 'observation.record':(obj({'temperature_K':number('K',0),'size_m':number('m',0),'measurement_source':TEXT}),obj({'recorded':BOOL}),['vessel'],'独立导入外部测温/粒径结果，不从隐藏真值生成','configuration','evaluator',dict(temperature_K=298.5,size_m=.00005,measurement_source='external_fixture')),
 'observation.read':(obj({}),obj({'available':BOOL,'temperature_K':dict(anyOf=[number('K',0),dict(type='null')]),'size_m':dict(anyOf=[number('m',0),dict(type='null')]),'measurement_source':TEXT}),['vessel'],'仅返回独立导入外部观测；无数据为null','query','agent',{}),
 'reference.lookup':(obj({'substance_id':TEXT}),obj({'cp_J_kg_K':number('J/(kg*K)',0),'temperature_K':number('K',0),'runtime_eligible':BOOL,'source':TEXT,'evidence':TEXT,'template_source':TEXT}),['display_digits'],'查可追溯液相比热点，不是乳化性能承诺','query','agent',dict(substance_id='water')),
}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,zh,o='derived',v='agent',c='drive',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=zh
for n in ['plant','method','sample']:slot(n+'.configured',BOOL,'1','宿主'+n+'已配置','setpoint')
slot('power.enabled',BOOL,'1','外部供电状态','setpoint',w=('power.set','enabled'));slot('mount.ready',BOOL,'1','宿主夹紧检查，不是传感器','setpoint',c='cross_sleeve')
slot('motor.enabled',BOOL,'1','面板运行开关的教学状态','setpoint',c='switch_run');slot('cycle.phase',PHASE,'1','宿主异步作业状态');slot('cycle.mode',TEXT,'1','加速、运行、收尾或模型适用边界')
for k,zh in [('elapsed_s','宿主已运行时间'),('remaining_s','宿主停机剩余时间')]:slot('cycle.'+k,number('s',0),'s',zh)
slot('motor.target_rad_s',number('rad/s',MIN_SPEED,MAX_SPEED),'rad/s','旋钮目标，不能瞬时替换真实速度','setpoint',c='knob_speed',w=('motor.set_speed','speed_rad_s'))
slot('rotor.speed_rad_s',number('rad/s',0,MAX_SPEED),'rad/s','过程转速真值','process_truth','evaluator',c='rotor')
slot('rotor.angle_rad',number('rad',0,math.tau),'rad','实际场景轴角，抽样会混叠而非真实慢转','scene',c='rotor')
slot('motor.indicated_speed_rad_s',number('rad/s',0,MAX_SPEED),'rad/s','按200rpm分辨率理想量化；非随机1%精度模型',c='display_digits')
slot('panel.speed_krpm',number('1',0,25),'1','LED显示值乘1000为rpm；不是计时或温度',c='display_digits')
slot('mount.immersion_m',number('m',0,.165),'m','当前液面到工具端面的浸深','scene',c='stator');slot('mount.target_immersion_m',number('m',.055,.165),'m','手工浸深目标','setpoint',c='drive',w=('mount.set_immersion','immersion_m'))
slot('mount.lift_m',number('m',-.15,.15),'m','整套驱动/头相对默认位置的升降位移','scene',c='drive')
for k,s,u,zh in [('mass_kg',array(number('kg',0),2),'kg','容器两相质量'),('initial_mass_kg',array(number('kg',0),2),'kg','初始两相质量'),('collected_mass_kg',array(number('kg',0),2),'kg','已收走两相质量'),('temperature_K',number('K',0),'K','集中参数真值温度，不是内置测温'),('size_m',number('m',0),'m','经验单一粒径尺度，不是实测分布'),('shear_s',number('1/s',0),'1/s','tip_speed/gap局部梯度尺度，不是CFD'),('shaft_power_W',number('W',0,500),'W','宿主负载公式且500W限幅'),('shaft_energy_J',number('J',0),'J','轴耗散累计能量'),('heat_to_sample_J',number('J',0),'J','分配给样品/容器的累计热'),('heat_to_ambient_J',number('J'),'J','累计散热，允许负值吸热'),('thermal_storage_J',number('J'),'J','样品加容器累计储热变化'),('volume_m3',number('m^3',0,.0015),'m^3','宿主密度和有限质量换算总体积')]:slot('truth.'+k,s,u,zh,'process_truth','evaluator',c='vessel' if 'rotor' not in k else 'rotor')

SLOTS={k.lower():v for k,v in SLOTS.items()};SLOT_ZH={k.lower():v for k,v in SLOT_ZH.items()}
class IkaT25Runtime:
 def __init__(self,instance_id='t25_01'):
  self.instance_id=instance_id;self.plant=self.method=self.sample=None;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.powered=self.mounted=self.motor=False;self.phase=self.mode='idle';self.time_s=self.elapsed_s=self.angle=self.speed=self.lift=self.lift_target=0.;self.immersion_target=BASE_IMMERSION;self.mass=[0.,0.];self.initial=[0.,0.];self.collected=[0.,0.];self.temperature=298.15;self.size=0.;self.capacity=0.;self.bounds=None;self.heat=self.loss=self.storage=self.energy=0.;self.observation=None;self.revision=0;self.operation_id='';self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility):
  if visibility not in ['agent','debug','evaluator'] or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return IkaT25Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def volume(self):return self.mass[0]/self.sample['carrier_density_kg_m3']+self.mass[1]/self.sample['dispersed_density_kg_m3'] if self.sample else 0.
 def immersion(self):return self.volume()/AREA-(DRIVE_Z+self.lift-.194-.02) if self.sample else 0.
 def indicated(self):return min(25000.,math.floor(self.speed*60/math.tau/200+.5)*200)*math.tau/60 if self.motor else 0.
 def load(self,w):
  if not self.sample or not self.plant:return 0.,0.,0.
  tip=w*ROTOR_D/2;shear=tip/GAP;n=w/math.tau;p=min(500.,self.plant['power_number']*self.sample['carrier_density_kg_m3']*n**3*ROTOR_D**5+self.plant['viscous_coefficient']*self.sample['viscosity_Pa_s']*w*w*ROTOR_D**3)
  return p,tip,shear
 def _remaining_time(self):return max(0.,self.method['duration_s']-self.elapsed_s) if self.method else 0.
 def _raw_values(self):
  p,tip,shear=self.load(self.speed)
  return {**{n+'.configured':bool(getattr(self,n)) for n in ['plant','method','sample']},'power.enabled':self.powered,'mount.ready':self.mounted,'motor.enabled':self.motor,'cycle.phase':self.phase,'cycle.mode':self.mode,'cycle.elapsed_s':self.elapsed_s,'cycle.remaining_s':self._remaining_time(),'motor.target_rad_s':self.method['speed_rad_s'] if self.method else MIN_SPEED,'rotor.speed_rad_s':self.speed,'rotor.angle_rad':self.angle%math.tau,'motor.indicated_speed_rad_s':self.indicated(),'panel.speed_krpm':self.indicated()*60/math.tau/1000,'mount.immersion_m':max(0.,self.immersion()),'mount.target_immersion_m':self.immersion_target,'mount.lift_m':self.lift,'truth.mass_kg':self.mass,'truth.initial_mass_kg':self.initial,'truth.collected_mass_kg':self.collected,'truth.temperature_K':self.temperature,'truth.size_m':self.size,'truth.shear_s':shear,'truth.shaft_power_W':p,'truth.shaft_energy_J':self.energy,'truth.heat_to_sample_J':self.heat,'truth.heat_to_ambient_J':self.loss,'truth.thermal_storage_J':self.storage,'truth.volume_m3':self.volume()}
 def _values(self):return {k.lower():v for k,v in self._raw_values().items()}
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ['debug','evaluator']})
 def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k!='templates'})
 def _can_start(self):return all([self.plant,self.method,self.sample,self.powered,self.mounted]) and self.phase=='idle' and abs(self.lift-self.lift_target)<1e-10 and .055-1e-10<=self.immersion()<=.165 and DRIVE_Z+self.lift-.194-.02>=.01-1e-10
 def _start(self):
  if not self._can_start():raise ActionRejected('PRECONDITION_FAILED')
  self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);self.phase='running';self.mode='dispersing';self.motor=True
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if (self.speed>1e-10 or self.phase=='running') and capability in ['plant.configure','method.configure','sample.configure','mount.configure','mount.set_immersion','product.collect']:raise ActionRejected('BUSY')
  if capability=='plant.configure':
   if self.plant or not a['reviewed_compatible_nonexplosive']:raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='method.configure':
   if not self.plant or self.phase!='idle':raise ActionRejected('PRECONDITION_FAILED')
   self.method=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='sample.configure':
   if not self.plant or self.sample:raise ActionRejected('PRECONDITION_FAILED')
   if a['carrier_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   r=self.templates[a['carrier_id']];bounds=r['allowed_approximation_interval_K'];volume=a['carrier_mass_kg']/a['carrier_density_kg_m3']+a['dispersed_mass_kg']/a['dispersed_density_kg_m3']
   if not r['runtime_eligible'] or not bounds or not bounds[0]<=a['temperature_K']<=bounds[1] or not bounds[0]<=self.plant['ambient_K']<=bounds[1]:raise ActionRejected('REFERENCE_RANGE')
   if not 1e-5<=volume<=.0015 or volume/AREA-self.immersion_target<.01:raise ActionRejected('CAPACITY_LIMIT')
   if a['initial_size_m']<self.plant['minimum_size_m']:raise ActionRejected('INVALID_ARGUMENT')
   self.sample=copy.deepcopy(a);self.mass=[a['carrier_mass_kg'],a['dispersed_mass_kg']];self.initial=self.mass.copy();self.bounds=bounds.copy();self.temperature=a['temperature_K'];self.size=a['initial_size_m'];self.capacity=self.mass[0]*r['value']+self.mass[1]*a['dispersed_cp_J_kg_K']+self.plant['vessel_heat_capacity_J_K'];self.lift_target=(volume-BASE_VOLUME)/AREA;out=dict(configured=True,attachment_event='host_finite_loading_not_robot')
  elif capability=='mount.configure':self.mounted=a['head_locked'] and a['vessel_clamped'];out=dict(ready=self.mounted)
  elif capability=='power.set':
   self.powered=a['enabled']
   if not self.powered:
    self.motor=False
    if self.phase=='running':self.phase='cancelled';self.mode='stopping'
   out=a
  elif capability=='motor.set_speed':
   if not self.method:raise ActionRejected('PRECONDITION_FAILED')
   self.method['speed_rad_s']=a['speed_rad_s'];out=a
  elif capability=='mount.set_immersion':
   if not self.sample or self.phase!='idle':raise ActionRejected('PRECONDITION_FAILED')
   if self.volume()/AREA-a['immersion_m']<.01:raise ActionRejected('PRECONDITION_FAILED')
   self.immersion_target=a['immersion_m'];self.lift_target=(self.volume()-BASE_VOLUME)/AREA+BASE_IMMERSION-self.immersion_target;out=a
  elif capability=='acquisition.start':self._start();out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('PRECONDITION_FAILED')
   self.motor=False;self.phase='cancelled';self.mode='stopping';out=dict(phase=self.phase)
  elif capability=='motor.toggle':
   if self.motor:self.motor=False;self.phase='cancelled';self.mode='stopping'
   else:self._start()
   out=dict(phase=self.phase)
  elif capability=='dispersion.measure':return dict(phase=self.phase,mode=self.mode,indicated_speed_rad_s=self.indicated(),host_elapsed_s=self.elapsed_s,host_remaining_s=self._remaining_time(),evidence='ideal_200rpm_quantized_LED_and_host_time_not_integral_thermal_or_size_sensor',template_source=TEMPLATE_REL)
  elif capability=='product.read':return dict(sample_id=self.sample['sample_id'] if self.sample else '',carrier_id=self.sample['carrier_id'] if self.sample else '',kind=self.sample['kind'] if self.sample else '',mass_kg=self.mass.copy(),collected_mass_kg=self.collected.copy(),initial_mass_kg=self.initial.copy(),temperature_K=self.temperature,size_m=self.size,volume_m3=self.volume(),shaft_energy_J=self.energy,heat_to_sample_J=self.heat,heat_to_ambient_J=self.loss,thermal_storage_J=self.storage,evidence='lumped_temperature_and_synthetic_size_scale_not_measured_emulsion_curve',template_source=TEMPLATE_REL)
  elif capability=='product.collect':
   if not self.sample or self.phase in ['idle','running']:raise ActionRejected('PRECONDITION_FAILED')
   moved=[m*a['fraction'] for m in self.mass]
   for i,m in enumerate(moved):self.mass[i]-=m;self.collected[i]+=m
   r=self.templates[self.sample['carrier_id']];self.capacity=self.mass[0]*r['value']+self.mass[1]*self.sample['dispersed_cp_J_kg_K']+self.plant['vessel_heat_capacity_J_K'];out=dict(transferred_kg=sum(moved),recipient_id=a['recipient_id'])
  elif capability=='observation.record':self.observation=copy.deepcopy(a);out=dict(recorded=True)
  elif capability=='observation.read':
   o=self.observation;return dict(available=o is not None,temperature_K=o['temperature_K'] if o else None,size_m=o['size_m'] if o else None,measurement_source=o['measurement_source'] if o else '')
  elif capability=='reference.lookup':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   r=self.templates[a['substance_id']];return dict(cp_J_kg_K=r['value'],temperature_K=r['conditions']['temperature_K'],runtime_eligible=r['runtime_eligible'],source=r['source'],evidence=r['evidence'],template_source=TEMPLATE_REL)
  self.revision+=1;return copy.deepcopy(out)
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or not 0<=dt_s<=3600:raise ActionRejected('INVALID_ARGUMENT')
  left=float(dt_s)
  while left>1e-12:
   h=min(.005,left);active=self.phase=='running' and self.motor;target=self.method['speed_rad_s'] if active else 0.;acc=self.plant['acceleration_rad_s2'] if self.plant else 2000.
   if active:h=min(h,self._remaining_time())
   reach=abs(target-self.speed)/acc
   if reach>1e-12:h=min(h,reach)
   if h<=1e-12:self.motor=False;self.mode='finishing';continue
   old=self.speed;self.speed=min(target,old+acc*h) if target>=old else max(target,old-acc*h);mid=(old+self.speed)/2;self.angle+=mid*h
   if self.sample and self.mode!='reference_limit' and self.capacity>0:
    p,tip,shear=self.load(mid);heat=self.plant['heat_fraction']*p;ua=self.plant['cooling_W_K'];amb=self.plant['ambient_K'];before=self.temperature
    after=amb+(before-amb-heat/ua)*math.exp(-ua*h/self.capacity)+heat/ua if ua else before+heat*h/self.capacity
    if not self.bounds[0]-1e-12<=after<=self.bounds[1]+1e-12:
     # Explicit host model boundary: no extrapolation or simulated hardware safety claim.
     self.speed=old;self.angle-=mid*h;self.phase='failed';self.mode='reference_limit';self.motor=False;self.speed=0.;left-=h;self.time_s+=h;self.revision+=1;continue
    self.temperature=after;stored=self.capacity*(after-before);self.energy+=p*h;self.heat+=heat*h;self.storage+=stored;self.loss+=heat*h-stored
    if mid>0 and self.mass[1]>0:
     floor=self.plant['minimum_size_m']
     if self.sample['kind']=='emulsion' and tip>0:floor=max(floor,self.plant['weber_coefficient']*self.sample['interfacial_tension_N_m']/(self.sample['carrier_density_kg_m3']*tip*tip))
     floor=min(self.size,floor);rate=self.plant['breakup_coefficient']*max(0.,shear-self.plant['critical_shear_s']);self.size=floor+(self.size-floor)*math.exp(-rate*h)
   if active:
    self.elapsed_s+=h
    if self._remaining_time()<1e-10:self.elapsed_s=self.method['duration_s'];self.motor=False;self.mode='finishing'
   if self.speed<1e-12:
    self.speed=0.
    if self.mode=='finishing':self.phase='succeeded';self.mode='idle'
    elif self.mode=='stopping':self.mode='idle'
   dl=(self.plant['lift_velocity_m_s'] if self.plant else .03)*h;self.lift+=min(dl,max(-dl,self.lift_target-self.lift));self.time_s+=h;left-=h;self.revision+=1

class IkaT25Session:
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
        meta=export_ika_t25_contract_metadata()
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('dispersion.measure'))

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])


def export_ika_t25_contract_metadata():
 caps={};slots={}
 for k,(schema,unit,origin,visibility,components,write) in SLOTS.items():
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'IkaT25Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
 immediate={'plant.configure':['plant.configured'],'method.configure':['method.configured','motor.target_rad_s','cycle.remaining_s'],'sample.configure':['sample.configured','truth.mass_kg','truth.initial_mass_kg','truth.temperature_K','truth.size_m','truth.volume_m3'],'mount.configure':['mount.ready'],'power.set':['power.enabled','motor.enabled','cycle.phase','cycle.mode','motor.indicated_speed_rad_s','panel.speed_krpm'],'motor.set_speed':['motor.target_rad_s'],'mount.set_immersion':['mount.target_immersion_m'],'acquisition.start':['cycle.phase','cycle.mode','motor.enabled','motor.indicated_speed_rad_s','panel.speed_krpm'],'acquisition.stop':['cycle.phase','cycle.mode','motor.enabled','motor.indicated_speed_rad_s','panel.speed_krpm'],'motor.toggle':['cycle.phase','cycle.mode','motor.enabled','motor.indicated_speed_rad_s','panel.speed_krpm'],'product.collect':['truth.mass_kg','truth.collected_mass_kg','truth.volume_m3','mount.immersion_m']}
 advance=[k for k in slots if k.startswith(('truth.','rotor.','cycle.'))]+['motor.indicated_speed_rad_s','panel.speed_krpm','motor.enabled','mount.lift_m','mount.immersion_m']
 for k,(inp,out,components,meaning,kind,vis,example) in CAPS.items():
  async_=k=='acquisition.start'
  caps[k]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.ika_t25.IkaT25Session.invoke',invocation=f'session.invoke(instance_id="t25_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if kind=='query' else [dict(description_zh='固定N18G、宿主兼容性和夹紧检查、转速/黏度/浸深/底距/容量/参考温区；拒绝无副作用',state_ids=['cycle.phase','sample.configured','power.enabled','mount.ready'],enforced_by='runtime',check='IkaT25Runtime.invoke capability-specific guards; not hardware safety')],state_effects=[dict(state_id=i.lower(),timing='immediate',description_zh='配置、目标、开关或有限转移') for i in immediate.get(k,[])]+([dict(state_id=i.lower(),timing='on_step',description_zh='有限加减速、耗散升温、经验尺度演化；宿主唯一时钟') for i in advance] if k in ['acquisition.start','motor.toggle','acquisition.stop','power.set','motor.set_speed','mount.set_immersion'] else []),observes=[],model_effects=dict(mode='none',description_zh='实际场景姿态/面板验证通过后才升格；不是机器人动作',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='宿主定时结束且减速至零；不保证特定乳化粒径' if async_ else '返回配置/目标/独立观测；只有step推进物理',completion_state_ids=['cycle.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['finite_two_phase_vessel','host_clock','principal_request_cache'],partial_failure_zh='取消保留质量和全部热/破碎历史；减速期间仍耗散。参考温区越界为模型失败，不是硬件保护。'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT','UNKNOWN_REFERENCE','REFERENCE_RANGE','CAPACITY_LIMIT']],example_arguments=copy.deepcopy(example),verification=[],limitations=['经验集中参数模型，非CFD、实测乳化性能、机器人或硬件；会话权限不是文件系统隔离。'],source_ids=['manufacturer_primary','manufacturer_manual','head_manual','simulation_assumption'])
 mechanism=dict(component_ids=['drive','rotor','stator','vessel','display_digits'],state_ids=list(slots),meaning_zh='有限两相守恒；真实液体Cp驱动温升；局部剪切尺度与经验破碎',implementation='lumped',equations=['omega target=3000–25000rpm converted to rad/s; finite host acceleration','tip=omega*0.0127/2; shear_scale=tip/0.0003; not a solved local shear field','Pshaft=min(500,Np*rho*n^3*D^5+Kmu*mu*omega^2*D^3); Np/Kmu explicitly host supplied','C=m_carrier*Cp_NIST+m_disperse*Cp_host+C_vessel_host','C*dT/dt=heat_fraction*Pshaft-UA*(T-Tambient); analytic per piecewise constant midpoint power','Energy ledger: heat_to_sample=thermal_storage+heat_to_ambient; heat_to_sample<=shaft_energy','d_floor=max(host_min,Cweber*sigma/(rho*tip^2)) for emulsion; host_min for suspension','d_next=d_floor+(d_old-d_floor)*exp(-host_k*max(0,shear-critical)*dt), floor clamped<=d_old','Initial mass=remaining+collected independently for carrier and dispersed phase; no chemical creation','V=sum(m/rho); immersion=liquid_surface-tool_tip; >=55mm and >=10mm vessel bottom clearance','Duration<=55s host schedule, acceleration>=1000rad/s² reserves<2.62s coast within head manual60s','Actual display=200rpm quantization; displayed number=RPM/1000; no built-in timer or thermometer'],parameters={},source_ids=['manufacturer_primary','manufacturer_manual','head_manual','nist_cp','simulation_assumption'],limitations=['Fixed standardS25N18G; other heads/ST/easyclean not implemented.','120mm vessel diameter teaching fixture; minimum55mm immersion+10mm bottom clearance narrows usable volumes; not full1mL–2L family.','79 real liquid Cp references,71 eligible; no79 measured T25 dispersion curves.','Density/viscosity/interfacial tension/dispersed Cp/power number/breakup/cooling/motion require host assumptions.','No CFD, droplet population balance, residence-time field, coagulation, evaporation, splashing, rheological evolution, seal wear or heat partition calibration.','Model range failure freezes unsupported physics and reports failed; not actual overtemperature firmware.','No Er3cooling mode/Er4/Er6firmware; host power interruption does prevent automatic restart.','No built-in temperature or particle-size sensor; public results require independent external observations.','Body2.5kg and head0.4kg separated; stand/vessel and component masses unknown.'])
 spec=importlib.util.spec_from_file_location('_t25_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'dispersion':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.configure':(SAMPLE,example_sample())})
 mechanism['parameters']={k.lower():v for k,v in mechanism['parameters'].items()}
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'dispersion':mechanism})

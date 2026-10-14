"""VCX750 + standard630-0220: event-split pulse/energy clock and bounded thermal proxy."""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.pccell_ed64002 import validate
ROOT=Path(__file__).resolve().parents[2];SLUG='sonics_vcx750';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-044';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/sonics_vcx750_sonication_library/templates.json'
VESSEL_R=.025;AREA=math.pi*VESSEL_R**2;BASE_VOLUME=.0001;VESSEL_BOTTOM=.012;PROBE_LENGTH=.139;CONVERTER_Z=VESSEL_BOTTOM+BASE_VOLUME/AREA/2+PROBE_LENGTH;LIFT_LIMITS=[-.012732395447351627,.08]
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
def nullable(s):return dict(anyOf=[s,dict(type='null')])
def array(s,n=None):return dict(type='array',items=s,minItems=n or 0,**({'maxItems':n} if n else {}))
PLANT=obj({'full_amplitude_probe_power_W':number('W',0,750),'amplitude_power_exponent':number('1',1,4),'absorbed_fraction':number('1',0,1),'vessel_heat_capacity_J_K':number('J/K',0,5000),'cooling_W_K':number('W/K',0,100),'ambient_K':number('K',278.15,313.15),'lift_velocity_m_s':number('m/s',.001,.1),'breakup_kg_J':number('kg/J',0,.01),'minimum_size_m':number('m',1e-9,.001),'temperature_hysteresis_K':number('K',.01,1),'reviewed_aqueous_compatible_nonexplosive':BOOL,'coefficient_evidence':TEXT})
PLANT_EXAMPLE=dict(full_amplitude_probe_power_W=90.,amplitude_power_exponent=2.,absorbed_fraction=.6,vessel_heat_capacity_J_K=30.,cooling_W_K=1.,ambient_K=298.15,lift_velocity_m_s=.03,breakup_kg_J=.0001,minimum_size_m=1e-6,temperature_hysteresis_K=.05,reviewed_aqueous_compatible_nonexplosive=True,coefficient_evidence='host_teaching_assumptions;90W manual example not calibration')
METHOD=obj({'amplitude_fraction':number('1',.2,1),'duration_s':number('s',0,35999),'pulse_enabled':BOOL,'pulse_on_s':number('s',1,59),'pulse_off_s':number('s',1,59),'energy_endpoint_J':number('J',0,1e7),'probe':dict(type='string',const='630-0220')})
METHOD_EXAMPLE=dict(amplitude_fraction=.4,duration_s=4.,pulse_enabled=True,pulse_on_s=1.,pulse_off_s=1.,energy_endpoint_J=0.,probe='630-0220')
SAMPLE=obj({'sample_id':TEXT,'carrier_id':dict(type='string',const='water'),'carrier_mass_kg':number('kg',1e-6,1),'carrier_density_kg_m3':number('kg/m^3',500,1500),'solid_label':TEXT,'solid_mass_kg':number('kg',0,1),'solid_density_kg_m3':number('kg/m^3',100,30000),'solid_cp_J_kg_K':number('J/(kg*K)',1,10000),'initial_size_m':number('m',1e-8,.005),'temperature_K':number('K',278.15,338.15),'material_evidence':TEXT})
def example_sample():return dict(sample_id='aqueous_suspension',carrier_id='water',carrier_mass_kg=.099,carrier_density_kg_m3=1000.,solid_label='host_inert_solid',solid_mass_kg=.002,solid_density_kg_m3=2000.,solid_cp_J_kg_K=800.,initial_size_m=.0001,temperature_K=298.15,material_evidence='host_assumption_except_NIST_water_Cp')
MEASURE=obj({'phase':PHASE,'mode':TEXT,'power_W':number('W',0,750),'energy_J':number('J',0),'on_time_s':number('s',0),'elapsed_s':number('s',0),'amplitude_fraction':number('1',.2,1),'temperature_K':nullable(number('K',0)),'evidence':TEXT,'template_source':TEXT})
PRODUCT=obj({'sample_id':TEXT,'mass_kg':array(number('kg',0),2),'collected_mass_kg':array(number('kg',0),2),'initial_mass_kg':array(number('kg',0),2),'temperature_K':number('K',0),'size_m':number('m',0),'volume_m3':number('m^3',0),'probe_energy_J':number('J',0),'absorbed_energy_J':number('J',0),'heat_to_ambient_J':number('J'),'thermal_storage_J':number('J'),'collected_sensible_heat_J':number('J'),'evidence':TEXT,'template_source':TEXT})
CAPS={
 'plant.configure':(PLANT,obj({'configured':BOOL}),['power_supply','converter','vessel'],'宿主负载、热量及解团聚系数，非额定功率直接加热','configuration','evaluator',PLANT_EXAMPLE),
 'method.configure':(METHOD,obj({'configured':BOOL}),['power_supply','button_ampl','button_pulse','button_timer','button_energy'],'振幅、ON定时、1–59秒脉冲与探头能量终点','configuration','agent',METHOD_EXAMPLE),
 'sample.configure':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['vessel','probe'],'宿主有限水基悬浮液，标准可换头不接受有机载液','configuration','evaluator',example_sample()),
 'mount.configure':(obj({'converter_clamped_upper':BOOL,'probe_tight':BOOL,'vessel_fixed':BOOL,'temperature_probe_present':BOOL}),obj({'ready':BOOL}),['clamp','converter','temperature_probe'],'宿主检查上部夹持、探头紧固及可选测温附件','configuration','evaluator',dict(converter_clamped_upper=True,probe_tight=True,vessel_fixed=True,temperature_probe_present=True)),
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['button_power_on','button_power_off','power_supply'],'正常供电；运行时先STOP；停电后180秒恢复保护为宿主规程','device_command','agent',dict(enabled=True)),
 'power.interrupt':(obj({}),obj({'phase':PHASE}),['power_inlet'],'宿主模拟意外断电，取消且不自动重启','simulation','evaluator',{}),
 'sonication.set_amplitude':(obj({'amplitude_fraction':number('1',.2,1)}),obj({'amplitude_fraction':number('1',.2,1)}),['button_ampl','converter'],'设置振幅比例；不是功率比例','device_command','agent',dict(amplitude_fraction=.5)),
 'sonication.amplitude_up':(obj({}),obj({'amplitude_fraction':number('1',.2,1)}),['button_up','converter'],'面板增加1个百分点的教学离散步长','device_command','agent',{}),
 'sonication.amplitude_down':(obj({}),obj({'amplitude_fraction':number('1',.2,1)}),['button_down','converter'],'面板减少1个百分点的教学离散步长','device_command','agent',{}),
 'thermal.set_temperature':(obj({'temperature_K':number('K',274.15,372.15)}),obj({'temperature_K':number('K',274.15,372.15)}),['button_temp','temperature_probe'],'可选测温探头的高温暂停阈值，不是加热目标','device_command','agent',dict(temperature_K=300.15)),
 'mount.set_immersion':(obj({'fraction':number('1',.4,.6)}),obj({'fraction':number('1',.4,.6)}),['converter','clamp','probe'],'停机后约半液深的手工升降目标，只有step移动','device_command','agent',dict(fraction=.5)),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['button_start_stop','converter','probe'],'启动异步超声；每循环能量及ON计时清零，热历史保留','device_command','agent',{}),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['button_start_stop','probe'],'立即停止并保留已发生效果','device_command','agent',{}),
 'sonication.toggle':(obj({}),obj({'phase':PHASE}),['button_start_stop','probe'],'START/STOP面板事件','device_command','agent',{}),
 'sonication.pause':(obj({}),obj({'paused':BOOL}),['button_pause'],'暂停/继续；ON与脉冲相位冻结，宿主经过时间仍计','device_command','agent',{}),
 'sonication.measure':(obj({}),MEASURE,['display_digits'],'理想化面板功率、探头能量、ON计时与可选温度','query','agent',{}),
 'product.read':(obj({}),PRODUCT,['vessel','probe'],'评价器可读有限质量、温度、解团聚尺度和热账本','query','evaluator',{}),
 'product.collect':(obj({'fraction':number('1',0,1),'recipient_id':TEXT}),obj({'transferred_kg':number('kg',0),'recipient_id':TEXT}),['vessel'],'停机后宿主有限均匀样品转移；不是机器人倾倒','device_command','evaluator',dict(fraction=.25,recipient_id='receiver')),
 'reference.lookup':(obj({'substance_id':TEXT}),obj({'cp_J_kg_K':number('J/(kg*K)',0),'temperature_K':number('K',0),'thermal_reference_eligible':BOOL,'installed_probe_eligible':BOOL,'source':TEXT,'evidence':TEXT,'template_source':TEXT}),['display_digits'],'查询真实液体比热点；非水条目不等于标准探头兼容','query','agent',dict(substance_id='water')),
}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,zh,o='derived',v='agent',c='power_supply',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=zh
for n in ['plant','method','sample']:slot(n+'.configured',BOOL,'1','宿主'+n+'配置完成','setpoint')
slot('power.enabled',BOOL,'1','供电，不等于超声发射','setpoint',w=('power.set','enabled'));slot('mount.ready',BOOL,'1','宿主安装检查，不是夹紧传感器','setpoint',c='clamp')
slot('cycle.phase',PHASE,'1','异步作业状态');slot('cycle.mode',TEXT,'1','发射/脉冲间歇/暂停/温限暂停/模型失效');slot('cycle.paused',BOOL,'1','操作员暂停状态','setpoint',c='button_pause')
for k,zh in [('on_time_s','仅ON累计计时'),('elapsed_s','本循环宿主经过时间，含暂停'),('remaining_s','剩余ON时间；连续模式为零'),('probe_energy_j','本循环探头能量理想读数，不是样品吸热')]:slot('cycle.'+k,number('J' if k.endswith('_j') else 's',0),'J' if k.endswith('_j') else 's',zh,c='display_digits')
slot('sonication.amplitude_fraction',number('1',.2,1),'1','振幅设定20–100%，不是750W百分比','setpoint',c='button_ampl',w=('sonication.set_amplitude','amplitude_fraction'))
slot('sonication.emitting',BOOL,'1','是否正处于超声ON段',c='probe');slot('sonication.power_w',number('W',0,750),'W','宿主负载公式产生理想面板瓦数，非标定测量',c='display_digits')
slot('thermal.limit_k',nullable(number('K',274.15,372.15)),'K','可选附件高温暂停阈值，未设置为null','setpoint',c='temperature_probe',w=('thermal.set_temperature','temperature_K'))
slot('thermal.indicated_temperature_k',nullable(number('K',0)),'K','可选附件理想整数摄氏温度；附件不存在返回null',c='temperature_probe')
slot('mount.lift_m',number('m',*LIFT_LIMITS),'m','沿支架手工升降的宿主场景位移','scene',c='converter');slot('mount.immersion_m',number('m',0,.139),'m','液面到探头端面浸入深度','scene',c='probe')
slot('panel.amplitude_percent',number('1',20,100),'1','振幅百分比面板显示',c='display_digits');slot('panel.energy_j',number('1',0),'1','LCD数值表示J，整数仅教学显示精度',c='display_digits');slot('panel.power_w',number('1',0,750),'1','LCD瓦数显示，非真实测量精度',c='display_digits');slot('panel.on_time_s',number('1',0,35999),'1','LCD秒数摘要，不是原厂完整字符屏',c='display_digits')
for k,s,u,zh in [('mass_kg',array(number('kg',0),2),'kg','水/固相剩余质量'),('initial_mass_kg',array(number('kg',0),2),'kg','装样初始质量'),('collected_mass_kg',array(number('kg',0),2),'kg','累计转出质量'),('temperature_k',number('K',0),'K','集中参数温度真值'),('size_m',number('m',0),'m','经验单一团聚粒径尺度，非真实粒度仪读数'),('total_probe_energy_j',number('J',0),'J','全部循环累计探头能量'),('absorbed_energy_j',number('J',0),'J','宿主吸收比例分配热量'),('heat_to_ambient_j',number('J'),'J','累计散热允许负值'),('thermal_storage_j',number('J'),'J','样品与容器储热变化'),('collected_heat_j',number('J'),'J','转出物料显热，以装样初温为参考'),('volume_m3',number('m^3',0,.00025),'m^3','有限质量与宿主密度得到体积')]:slot('truth.'+k,s,u,zh,'process_truth','evaluator',c='vessel')
class SonicsVcx750Runtime:
 def __init__(self,instance_id='vcx750_01'):
  self.instance_id=instance_id;self.plant=self.method=self.sample=None;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.powered=self.mounted=self.temp_probe=self.paused=self.thermal_hold=False;self.phase=self.mode='idle';self.time_s=self.elapsed_s=self.on_time=self.pulse_time=self.cycle_energy=self.total_energy=self.heat=self.loss=self.storage=self.collected_heat=self.lift=self.lift_target=0.;self.immersion_fraction=.5;self.limit=None;self.power_restore_at=0.;self.mass=[0.,0.];self.initial=self.mass.copy();self.collected=self.mass.copy();self.temperature=298.15;self.size=0.;self.capacity=0.;self.bounds=None;self.revision=0;self.operation_id='';self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility):
  if visibility not in ['agent','debug','evaluator'] or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return SonicsVcx750Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def volume(self):return self.mass[0]/self.sample['carrier_density_kg_m3']+self.mass[1]/self.sample['solid_density_kg_m3'] if self.sample else 0.
 def immersion(self):return VESSEL_BOTTOM+self.volume()/AREA-(CONVERTER_Z+self.lift-PROBE_LENGTH) if self.sample else 0.
 def amplitude(self):return self.method['amplitude_fraction'] if self.method else .2
 def emitting(self):return bool(self.phase=='running' and not self.paused and not self.thermal_hold and (not self.method['pulse_enabled'] or self.pulse_time<self.method['pulse_on_s']-1e-10))
 def load(self):return min(750.,self.plant['full_amplitude_probe_power_W']*self.amplitude()**self.plant['amplitude_power_exponent']) if self.plant else 0.
 def indicated_temperature(self):return math.floor(self.temperature-273.15+.5)+273.15 if self.temp_probe and self.sample else None
 def _remaining_time(self):return max(0.,self.method['duration_s']-self.on_time) if self.method and self.method['duration_s'] else 0.
 def _values(self):
  return {**{n+'.configured':bool(getattr(self,n)) for n in ['plant','method','sample']},'power.enabled':self.powered,'mount.ready':self.mounted,'cycle.phase':self.phase,'cycle.mode':self.mode,'cycle.paused':self.paused,'cycle.on_time_s':self.on_time,'cycle.elapsed_s':self.elapsed_s,'cycle.remaining_s':self._remaining_time(),'cycle.probe_energy_j':self.cycle_energy,'sonication.amplitude_fraction':self.amplitude(),'sonication.emitting':self.emitting(),'sonication.power_w':self.load() if self.emitting() else 0.,'thermal.limit_k':self.limit,'thermal.indicated_temperature_k':self.indicated_temperature(),'mount.lift_m':self.lift,'mount.immersion_m':max(0.,self.immersion()),'panel.amplitude_percent':round(self.amplitude()*100),'panel.energy_j':round(self.cycle_energy),'panel.power_w':round(self.load()) if self.emitting() else 0.,'panel.on_time_s':math.floor(self.on_time+1e-9),'truth.mass_kg':self.mass,'truth.initial_mass_kg':self.initial,'truth.collected_mass_kg':self.collected,'truth.temperature_k':self.temperature,'truth.size_m':self.size,'truth.total_probe_energy_j':self.total_energy,'truth.absorbed_energy_j':self.heat,'truth.heat_to_ambient_j':self.loss,'truth.thermal_storage_j':self.storage,'truth.collected_heat_j':self.collected_heat,'truth.volume_m3':self.volume()}
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ['debug','evaluator']})
 def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k!='templates'})
 def _can_start(self):return all([self.plant,self.method,self.sample,self.powered,self.mounted]) and self.phase!='running' and self.phase!='failed' and abs(self.lift-self.lift_target)<1e-10 and .4-1e-9<=self.immersion()/(self.volume()/AREA)<=.6+1e-9 and CONVERTER_Z+self.lift-PROBE_LENGTH-VESSEL_BOTTOM>=.01-1e-10 and self.volume()>=.00005-1e-12
 def _start(self):
  if not self._can_start():raise ActionRejected('PRECONDITION_FAILED')
  self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);self.phase='running';self.mode='emitting';self.paused=False;self.thermal_hold=bool(self.limit is not None and self.temperature>=self.limit);self.elapsed_s=self.on_time=self.pulse_time=self.cycle_energy=0.
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if self.phase=='running' and capability in ['plant.configure','method.configure','sample.configure','mount.configure','mount.set_immersion','product.collect']:raise ActionRejected('BUSY')
  if capability=='plant.configure':
   if self.plant or not a['reviewed_aqueous_compatible_nonexplosive']:raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='method.configure':
   if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
   if any(a[k]!=int(a[k]) for k in ['duration_s','pulse_on_s','pulse_off_s']) or (0<a['duration_s']<1):raise ActionRejected('INVALID_ARGUMENT')
   self.method=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='sample.configure':
   if not self.plant or self.sample:raise ActionRejected('PRECONDITION_FAILED')
   r=self.templates['water'];bounds=r['allowed_approximation_interval_K'];volume=a['carrier_mass_kg']/a['carrier_density_kg_m3']+a['solid_mass_kg']/a['solid_density_kg_m3']
   if not bounds[0]<=a['temperature_K']<=bounds[1] or not bounds[0]<=self.plant['ambient_K']<=bounds[1]:raise ActionRejected('REFERENCE_RANGE')
   if not .00005-1e-12<=volume<=.00025+1e-12:raise ActionRejected('CAPACITY_LIMIT')
   if a['initial_size_m']<self.plant['minimum_size_m']:raise ActionRejected('INVALID_ARGUMENT')
   self.sample=copy.deepcopy(a);self.mass=[a['carrier_mass_kg'],a['solid_mass_kg']];self.initial=self.mass.copy();self.temperature=a['temperature_K'];self.size=a['initial_size_m'];self.bounds=bounds.copy();self.capacity=self.mass[0]*r['value']+self.mass[1]*a['solid_cp_J_kg_K']+self.plant['vessel_heat_capacity_J_K'];self.lift_target=(volume-BASE_VOLUME)/AREA/2;out=dict(configured=True,attachment_event='host_finite_loading_not_robot')
  elif capability=='mount.configure':
   self.mounted=a['converter_clamped_upper'] and a['probe_tight'] and a['vessel_fixed'];self.temp_probe=a['temperature_probe_present'];self.limit=self.limit if self.temp_probe else None;out=dict(ready=self.mounted)
  elif capability=='power.set':
   if (not a['enabled'] and self.phase=='running') or (a['enabled'] and self.time_s<self.power_restore_at):raise ActionRejected('PRECONDITION_FAILED')
   self.powered=a['enabled'];out=a
  elif capability=='power.interrupt':
   self.powered=False;self.power_restore_at=self.time_s+180.;self.paused=False
   if self.phase=='running':self.phase='cancelled';self.mode='power_loss'
   out=dict(phase=self.phase)
  elif capability in ['sonication.set_amplitude','sonication.amplitude_up','sonication.amplitude_down']:
   if not self.method:raise ActionRejected('PRECONDITION_FAILED')
   value=a.get('amplitude_fraction',min(1.,max(.2,self.amplitude()+(.01 if capability.endswith('_up') else -.01))));self.method['amplitude_fraction']=value;out=dict(amplitude_fraction=value)
  elif capability=='thermal.set_temperature':
   if not self.temp_probe:raise ActionRejected('PRECONDITION_FAILED')
   self.limit=a['temperature_K'];out=a
  elif capability=='mount.set_immersion':
   if not self.sample:raise ActionRejected('PRECONDITION_FAILED')
   target=VESSEL_BOTTOM+(1-a['fraction'])*self.volume()/AREA+PROBE_LENGTH-CONVERTER_Z
   if not LIFT_LIMITS[0]-1e-12<=target<=LIFT_LIMITS[1] or (1-a['fraction'])*self.volume()/AREA<.01:raise ActionRejected('PRECONDITION_FAILED')
   self.immersion_fraction=a['fraction'];self.lift_target=target;out=a
  elif capability=='acquisition.start':self._start();out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('PRECONDITION_FAILED')
   self.phase='cancelled';self.mode='stopped';self.paused=False;out=dict(phase=self.phase)
  elif capability=='sonication.toggle':
   if self.phase=='running':self.phase='cancelled';self.mode='stopped';self.paused=False
   else:self._start()
   out=dict(phase=self.phase)
  elif capability=='sonication.pause':
   if self.phase!='running':raise ActionRejected('PRECONDITION_FAILED')
   self.paused=not self.paused;out=dict(paused=self.paused)
  elif capability=='sonication.measure':return dict(phase=self.phase,mode=self.mode,power_W=round(self.load()) if self.emitting() else 0.,energy_J=round(self.cycle_energy),on_time_s=self.on_time,elapsed_s=self.elapsed_s,amplitude_fraction=self.amplitude(),temperature_K=self.indicated_temperature(),evidence='ideal_panel_proxy;energy_to_probe_not_sample;not_factory_precision',template_source=TEMPLATE_REL)
  elif capability=='product.read':return dict(sample_id=self.sample['sample_id'] if self.sample else '',mass_kg=self.mass.copy(),collected_mass_kg=self.collected.copy(),initial_mass_kg=self.initial.copy(),temperature_K=self.temperature,size_m=self.size,volume_m3=self.volume(),probe_energy_J=self.total_energy,absorbed_energy_J=self.heat,heat_to_ambient_J=self.loss,thermal_storage_J=self.storage,collected_sensible_heat_J=self.collected_heat,evidence='lumped_heat_and_synthetic_deagglomeration_not_calibrated_cavitation',template_source=TEMPLATE_REL)
  elif capability=='product.collect':
   if not self.sample or self.phase=='idle':raise ActionRejected('PRECONDITION_FAILED')
   old_capacity=self.capacity
   moved=[m*a['fraction'] for m in self.mass]
   for i,m in enumerate(moved):self.mass[i]-=m;self.collected[i]+=m
   self.capacity=self.mass[0]*self.templates['water']['value']+self.mass[1]*self.sample['solid_cp_J_kg_K']+self.plant['vessel_heat_capacity_J_K'];carried=(old_capacity-self.capacity)*(self.temperature-self.sample['temperature_K']);self.storage-=carried;self.collected_heat+=carried;out=dict(transferred_kg=sum(moved),recipient_id=a['recipient_id'])
  elif capability=='reference.lookup':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   r=self.templates[a['substance_id']];return dict(cp_J_kg_K=r['value'],temperature_K=r['conditions']['temperature_K'],thermal_reference_eligible=r['runtime_eligible'],installed_probe_eligible=a['substance_id']=='water',source=r['source'],evidence=r['evidence'],template_source=TEMPLATE_REL)
  self.revision+=1;return copy.deepcopy(out)
 def _temperature_after(self,h,p):
  if not self.sample or not self.capacity:return self.temperature
  ua=self.plant['cooling_W_K'];amb=self.plant['ambient_K'];q=p*self.plant['absorbed_fraction']
  return amb+(self.temperature-amb-q/ua)*math.exp(-ua*h/self.capacity)+q/ua if ua else self.temperature+q*h/self.capacity
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or not 0<=dt_s<=3600:raise ActionRejected('INVALID_ARGUMENT')
  left=float(dt_s)
  while left>1e-11:
   active=self.phase=='running'
   if active and self.limit is not None:
    if not self.thermal_hold and self.temperature>=self.limit-1e-10:self.thermal_hold=True
    elif self.thermal_hold and self.temperature<=self.limit-self.plant['temperature_hysteresis_K']+1e-10:self.thermal_hold=False
   emit=self.emitting();p=self.load() if emit else 0.;h=min(.05,left)
   if active:
    if self.elapsed_s>=900.-1e-9:self.phase='failed';self.mode='host_uncooled_900s_boundary';continue
    h=min(h,900.-self.elapsed_s)
    if emit and self.method['duration_s']:h=min(h,self._remaining_time())
    if emit and self.method['energy_endpoint_J'] and p>0:h=min(h,max(0.,self.method['energy_endpoint_J']-self.cycle_energy)/p)
    if not self.paused and not self.thermal_hold and self.method['pulse_enabled']:
     edge=self.method['pulse_on_s'] if emit else self.method['pulse_on_s']+self.method['pulse_off_s'];h=min(h,edge-self.pulse_time)
    if h<1e-11:self.phase='succeeded';self.mode='endpoint';continue
   # Split an ideal temperature-limit crossing; hysteresis is explicit host assumption.
   if active and self.limit is not None and not self.paused:
    target=self.limit-self.plant['temperature_hysteresis_K'] if self.thermal_hold else self.limit;after=self._temperature_after(h,p)
    if (self.temperature<target<after) or (self.temperature>target>after):
     lo=0.;hi=h
     for _ in range(45):
      mid=(lo+hi)/2;v=self._temperature_after(mid,p)
      if (v<target)==(self.temperature<target):lo=mid
      else:hi=mid
     h=hi
   after=self._temperature_after(h,p)
   if self.sample and self.mode!='reference_limit':
    if not self.bounds[0]-1e-10<=after<=min(338.15,self.bounds[1])+1e-10:self.phase='failed';self.mode='reference_limit';p=0.;after=self.temperature
    else:
     stored=self.capacity*(after-self.temperature);self.temperature=after;self.total_energy+=p*h;self.heat+=self.plant['absorbed_fraction']*p*h;self.storage+=stored;self.loss+=self.plant['absorbed_fraction']*p*h-stored
     if self.mass[1]>0 and sum(self.mass)>0:
      floor=self.plant['minimum_size_m'];self.size=floor+(self.size-floor)*math.exp(-self.plant['breakup_kg_J']*self.plant['absorbed_fraction']*p*h/sum(self.mass))
   if active and self.phase=='running':
    self.elapsed_s+=h
    if emit:self.on_time+=h;self.cycle_energy+=p*h
    if not self.paused and not self.thermal_hold:
     self.pulse_time+=h
     if self.method['pulse_enabled'] and self.pulse_time>=self.method['pulse_on_s']+self.method['pulse_off_s']-1e-10:self.pulse_time=0.
    if (self.method['duration_s'] and self.on_time>=self.method['duration_s']-1e-9) or (self.method['energy_endpoint_J'] and self.cycle_energy>=self.method['energy_endpoint_J']-1e-9):self.phase='succeeded';self.mode='endpoint'
    else:self.mode='paused' if self.paused else ('temperature_hold' if self.thermal_hold else ('emitting' if self.emitting() else 'pulse_off'))
   dl=(self.plant['lift_velocity_m_s'] if self.plant else .03)*h;self.lift+=min(dl,max(-dl,self.lift_target-self.lift));self.time_s+=h;left-=h;self.revision+=1

class SonicsVcx750Session:
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
        meta=export_sonics_vcx750_contract_metadata()
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('sonication.measure'))

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])



def export_sonics_vcx750_contract_metadata():
 caps={};slots={}
 for k,(schema,unit,origin,visibility,components,write) in SLOTS.items():
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'SonicsVcx750Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode='integral' if k.endswith(('_energy_j','_time_s')) else 'instant',time_domain='simulation',window_description='本循环或全历史积分，见槽含义' if k.endswith(('_energy_j','_time_s')) else None),source_ids=['simulation_assumption'])
 immediate={'plant.configure':['plant.configured'],'method.configure':['method.configured','sonication.amplitude_fraction','panel.amplitude_percent','cycle.remaining_s'],'sample.configure':['sample.configured','truth.mass_kg','truth.initial_mass_kg','truth.temperature_k','truth.size_m','truth.volume_m3'],'mount.configure':['mount.ready','thermal.limit_k','thermal.indicated_temperature_k'],'power.set':['power.enabled'],'power.interrupt':['power.enabled','cycle.phase','cycle.paused','cycle.mode','sonication.emitting'],'sonication.set_amplitude':['sonication.amplitude_fraction','panel.amplitude_percent'],'sonication.amplitude_up':['sonication.amplitude_fraction','panel.amplitude_percent'],'sonication.amplitude_down':['sonication.amplitude_fraction','panel.amplitude_percent'],'thermal.set_temperature':['thermal.limit_k'],'acquisition.start':['cycle.phase','cycle.mode','cycle.paused','cycle.on_time_s','cycle.elapsed_s','cycle.probe_energy_j','sonication.emitting'],'acquisition.stop':['cycle.phase','cycle.mode','cycle.paused','sonication.emitting'],'sonication.toggle':['cycle.phase','cycle.mode','cycle.paused','sonication.emitting'],'sonication.pause':['cycle.paused','sonication.emitting'],'product.collect':['truth.mass_kg','truth.collected_mass_kg','truth.volume_m3','mount.immersion_m','truth.thermal_storage_j','truth.collected_heat_j']}
 advance=[k for k in slots if k.startswith(('truth.','cycle.','panel.','mount.'))]+['sonication.emitting','sonication.power_w','thermal.indicated_temperature_k']
 for k,(inp,out,components,meaning,kind,vis,example) in CAPS.items():
  async_=k=='acquisition.start';caps[k]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.sonics_vcx750.SonicsVcx750Session.invoke',invocation=f'session.invoke(instance_id="vcx750_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if kind=='query' else [dict(description_zh='宿主水基兼容性、上部夹持、浸深与温区检查；非厂家硬件互锁',state_ids=['cycle.phase','sample.configured','power.enabled','mount.ready'],enforced_by='runtime',check='SonicsVcx750Runtime.invoke capability guards')],state_effects=[dict(state_id=i,timing='immediate',description_zh='配置、目标、面板状态或有限转移') for i in immediate.get(k,[])]+([dict(state_id=i,timing='on_step',description_zh='事件分段ON定时/热量/质量账本；只有宿主step推进') for i in advance] if k in ['acquisition.start','acquisition.stop','sonication.toggle','sonication.pause','thermal.set_temperature','sonication.set_amplitude','mount.set_immersion'] else []),observes=[],model_effects=dict(mode='none',description_zh='真实场景测试后才声明耦合；20kHz微振动未做刚体动画',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='首个ON时间或探头能量终点；不保证解团聚粒径' if async_ else '配置/目标或查询成功，step才推进物理',completion_state_ids=['cycle.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['finite_aqueous_vessel','host_clock','principal_request_cache'],partial_failure_zh='暂停与取消保留热、粒径尺度、质量和累计能量；新循环只清零面板循环计数，模型越界不伪装硬件保护。'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT','UNKNOWN_REFERENCE','REFERENCE_RANGE','CAPACITY_LIMIT']],example_arguments=copy.deepcopy(example),verification=[],limitations=['集中参数教学模型，不是空化/声化学标定；标准630-0220只支持水基样品；权限适配不构成文件系统隔离。'],source_ids=['manufacturer_primary','manufacturer_manual','simulation_assumption'])
 mechanism=dict(component_ids=['power_supply','converter','probe','vessel','display_digits','temperature_probe'],state_ids=list(slots),meaning_zh='有限水基悬浮液；探头功率与样品吸热分离；事件分段脉冲和终点',implementation='lumped',equations=['Frequency=20000Hz; no explicit acoustic-wave or cavitation PDE solution','Pprobe=host_full_amplitude_power*(amplitude_fraction**host_exponent), bounded0..750W; not750*amplitude','Eprobe+=Pprobe*ON_dt; Eabsorbed=host_eta*Eprobe; timer integrates ON only','Pulse ON/OFF1..59s; pause freezes pulse phase and ON clock; host elapsed clock includes holds','Stop at first enabled ON-time or probe-energy endpoint; exact split at each pulse/time/energy edge','C=m_water*Cp_NIST_liquid+m_solid*Cp_host+C_vessel_host','C*dT/dt=eta*Pprobe-UA*(T-Tambient); analytical constant-power segment','Optional temperature threshold pauses sonication; resumes below threshold-host_hysteresis; not a heater','Absorbed_energy=thermal_storage+heat_to_ambient+collected_sensible_heat; carried_heat=removed_heat_capacity*(T-T_initial); each species initial=remaining+collected','d_next=d_min+(d_old-d_min)*exp(-k_host*absorbed_energy_increment/remaining_mass); synthetic size scale','Manual probe approximately halfway; teaching fixture permits0.4..0.6 liquid depth plus10mm bottom clearance','Host900s elapsed maximum because active converter-air-cooling unimplemented; not factory timer maximum35999s'],parameters={},source_ids=['manufacturer_primary','manufacturer_manual','nist_cp','simulation_assumption'],limitations=['Standard630-0220 replaceable tip only; solid630-0219/microtips/flowcells not implemented.','79 real liquid Cp reference entries are not79 measured ultrasound curves; only water carrier allowed with installed standard probe.','Host90W full-amplitude example and exponent are illustrative, not a calibrated amplitude–power relationship.','Absorption, heat transfer, solid Cp/density, size kinetics and temperature hysteresis need calibration.','No resolved20kHz vibration/cavitation field/radicals/sonochemistry/aerosols/probe erosion/evaporation/noise prediction.','Temperature proxy exists only when optional830-00060 is host-installed; factory precision/delay/hysteresis unknown.','Save/recall9programs/fullnumeric-key matrix/footswitch/I-O/fault firmware not implemented.','Nominal timer range accepted but uncooled900s host guard narrows physical task envelope.','Constant-Cp liquid reference valid within guarded narrow temperature interval; exceeding it fails model without extrapolation.','Cable1.8m is documented total length; visual cable is a partial segment, not flexible-body simulation.'])
 spec=importlib.util.spec_from_file_location('_vcx_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'sonication':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.configure':(SAMPLE,example_sample())});mechanism['parameters']={k.lower():v for k,v in mechanism['parameters'].items()}
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'sonication':mechanism})

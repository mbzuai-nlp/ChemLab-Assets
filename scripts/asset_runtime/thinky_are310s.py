"""ARE-310S-UL teaching model; family speed envelope is not UL certification."""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.pccell_ed64002 import validate
ROOT=Path(__file__).resolve().parents[2];SLUG='thinky_are310s';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-045';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/thinky_are310s_mixing_library/templates.json'
ORBIT_RADIUS=.07;ROTOR_Z=.225;TILT=math.pi/4;LID_Z=.315;LID_Y=.135
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
def nullable(s):return dict(anyOf=[s,dict(type='null')])
def array(s,n=None):return dict(type='array',items=s,minItems=n or 0,**({'maxItems':n} if n else {}))
PLANT=obj({'acceleration_rpm_s':number('rpm/s',100,10000),'brake_rpm_s':number('rpm/s',100,10000),'reference_dissipation_W':number('W',0,200),'vessel_heat_capacity_J_K':number('J/K',1,5000),'cooling_W_K':number('W/K',0,100),'ambient_K':number('K',283.15,308.15),'mixing_rate_s_1':number('1/s',0,10),'bubble_escape_rate_s_1':number('1/s',0,10),'reference_viscosity_Pa_s':number('Pa*s',.001,1000),'balance_tolerance_kg':number('kg',0,.02),'lid_rate_rad_s':number('rad/s',.1,3),'reviewed_cup_material_and_nonhazardous':BOOL,'coefficient_evidence':TEXT})
PLANT_EXAMPLE=dict(acceleration_rpm_s=2000.,brake_rpm_s=4000.,reference_dissipation_W=2.,vessel_heat_capacity_J_K=35.,cooling_W_K=.5,ambient_K=298.15,mixing_rate_s_1=.15,bubble_escape_rate_s_1=.25,reference_viscosity_Pa_s=1.,balance_tolerance_kg=.001,lid_rate_rad_s=1.5,reviewed_cup_material_and_nonhazardous=True,coefficient_evidence='host_teaching_assumptions_not_factory_calibration')
STAGE=obj({'mode':dict(type='string',enum=['mix','defoam']),'duration_s':number('s',1,1800),'revolution_rpm':number('rpm',0,2200)})
METHOD=obj({'program':dict(type='array',items=STAGE,minItems=1,maxItems=10),'speed_envelope':dict(type='string',const='family_SCE_reference_not_UL_verified')})
METHOD_EXAMPLE=dict(program=[dict(mode='mix',duration_s=4.,revolution_rpm=2000.),dict(mode='defoam',duration_s=3.,revolution_rpm=2200.)],speed_envelope='family_SCE_reference_not_UL_verified')
SAMPLE=obj({'sample_id':TEXT,'carrier_id':TEXT,'carrier_mass_kg':number('kg',1e-6,.25),'carrier_density_kg_m3':number('kg/m^3',500,2500),'solid_label':TEXT,'solid_mass_kg':number('kg',0,.25),'solid_density_kg_m3':number('kg/m^3',100,30000),'solid_cp_J_kg_K':number('J/(kg*K)',1,10000),'viscosity_Pa_s':number('Pa*s',.001,1000),'entrained_gas_mol':number('mol',0,.001),'temperature_K':number('K',283.15,308.15),'cup_volume_m3':dict(type='number',enum=[.00015,.0003],**{'x-unit':'m^3'}),'cup_tare_kg':number('kg',.001,.15),'adapter_mass_kg':number('kg',0,.15),'material_evidence':TEXT})
def example_sample():return dict(sample_id='aqueous_resin_proxy',carrier_id='water',carrier_mass_kg=.08,carrier_density_kg_m3=1000.,solid_label='host_inert_solid',solid_mass_kg=.02,solid_density_kg_m3=2000.,solid_cp_J_kg_K=800.,viscosity_Pa_s=1.,entrained_gas_mol=.00002,temperature_K=298.15,cup_volume_m3=.0003,cup_tare_kg=.04,adapter_mass_kg=0.,material_evidence='host_assumptions_except_NIST_liquid_Cp')
MEASURE=obj({'phase':PHASE,'mode':TEXT,'revolution_rpm':number('rpm',0,2200),'remaining_s':number('s',0,1800),'stage_index':number('1',0,9),'temperature_K':nullable(number('K',0)),'evidence':TEXT,'template_source':TEXT})
PRODUCT=obj({'sample_id':TEXT,'mass_kg':array(number('kg',0),2),'initial_mass_kg':array(number('kg',0),2),'collected_mass_kg':array(number('kg',0),2),'temperature_K':number('K',0),'mixing_index':number('1',0,1),'entrained_gas_mol':number('mol',0),'headspace_gas_mol':number('mol',0),'collected_gas_mol':number('mol',0),'dissipated_energy_J':number('J',0),'heat_to_ambient_J':number('J'),'thermal_storage_J':number('J'),'collected_sensible_heat_J':number('J'),'evidence':TEXT,'template_source':TEXT})
CAPS={
 'plant.configure':(PLANT,obj({'configured':BOOL}),['body','rotor','cup_holder'],'宿主动力学、配平容差与材料兼容性审核；非厂家标定','configuration','evaluator',PLANT_EXAMPLE),
 'method.configure':(METHOD,obj({'configured':BOOL}),['display_digits','button_setup'],'1至10段混合/脱泡程序；SCE速度范围仅作为显式教学包络','configuration','agent',METHOD_EXAMPLE),
 'sample.configure':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['cup_holder','cup_300','cup_150','adapter_150'],'宿主有限装样；300/150mL杯、净质量与含适配器总质量独立守卫','configuration','evaluator',example_sample()),
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['button_power','power_inlet'],'120V供电代理；转动未停不得正常关机','device_command','agent',dict(enabled=True)),
 'power.interrupt':(obj({}),obj({'phase':PHASE}),['power_inlet'],'意外断电取消保留盖锁；宿主减速不冒充真实惯性滑停','simulation','evaluator',{}),
 'lid.set_open':(obj({'open':BOOL}),obj({'open':BOOL}),['lid','button_open'],'手工盖角目标；step移动，未停稳拒绝开盖','device_command','agent',dict(open=False)),
 'balance.set':(obj({'gross_mass_kg':number('kg',0,.31)}),obj({'gross_mass_kg':number('kg',0,.31)}),['counterweight','knob_balance'],'配平标尺为杯+适配器+样品总质量；真实配重质量未知','device_command','agent',dict(gross_mass_kg=.14)),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['button_start_stop','rotor','cup_holder'],'异步程序；闭盖、配平、装杯就绪，完成包括宿主制动','device_command','agent',{}),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['button_start_stop','rotor'],'取消保留效果；继续step减速','device_command','agent',{}),
 'mixing.toggle':(obj({}),obj({'phase':PHASE}),['button_start_stop','rotor'],'面板START/STOP事件','device_command','agent',{}),
 'mixing.measure':(obj({}),MEASURE,['display_digits'],'理想转速/剩余时间；未装MultiSensor不提供温度','query','agent',{}),
 'product.read':(obj({}),PRODUCT,['cup_holder'],'评价器读取质量、气体、混合指数和热账本','query','evaluator',{}),
 'product.collect':(obj({'fraction':number('1',0,1),'recipient_id':TEXT}),obj({'transferred_kg':number('kg',0),'recipient_id':TEXT}),['cup_holder','lid'],'停稳开盖后宿主有限转移；气体和显热守恒','device_command','evaluator',dict(fraction=.25,recipient_id='receiver')),
 'reference.lookup':(obj({'substance_id':TEXT}),obj({'cp_J_kg_K':number('J/(kg*K)',0),'temperature_K':number('K',0),'thermal_reference_eligible':BOOL,'source':TEXT,'evidence':TEXT,'template_source':TEXT}),['display_digits'],'真实比热参考，非厂家混合曲线或材料兼容性认证','query','agent',dict(substance_id='water')),
}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,zh,o='derived',v='agent',c='body',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=zh
for n in ['plant','method','sample']:slot(n+'.configured',BOOL,'1','宿主'+n+'配置完成','setpoint')
slot('power.enabled',BOOL,'1','供电设定','setpoint',w=('power.set','enabled'));slot('lid.open_target',BOOL,'1','开盖目标；非瞬移','setpoint',c='lid',w=('lid.set_open','open'));slot('lid.angle_rad',number('rad',0,1.7),'rad','后铰链开盖角','scene',c='lid');slot('lid.locked',BOOL,'1','闭盖或未停稳的宿主锁代理',c='lid')
slot('balance.gross_mass_kg',number('kg',0,.31),'kg','配平标尺，不是真实配重质量','setpoint',c='knob_balance',w=('balance.set','gross_mass_kg'));slot('balance.dial_angle_rad',number('rad',-math.pi*5/6,math.pi*5/6),'rad','教学线性标尺0至310g映射±150度','scene',c='knob_balance')
slot('cycle.phase',PHASE,'1','异步程序状态');slot('cycle.mode',TEXT,'1','混合/脱泡/制动/空闲');slot('cycle.stage_index',number('1',0,9),'1','当前段索引');slot('cycle.elapsed_s',number('s',0),'s','程序累计时间');slot('cycle.remaining_s',number('s',0,1800),'s','剩余程序时间；不含制动')
slot('rotor.revolution_rpm',number('rpm',0,2200),'rpm','加减速后的公转',c='rotor');slot('rotor.spin_rpm',number('rpm',0,800),'rpm','混合0.4比例，脱泡60/2200；反向自转',c='cup_holder');slot('rotor.angle_rad',number('rad',0,math.tau),'rad','公转角模2π','scene',c='rotor');slot('cup.angle_rad',number('rad',-math.tau,0),'rad','45度轴反向自转角','scene',c='cup_holder')
slot('panel.rpm',number('1',0,2200),'1','整数rpm理想显示',c='display_digits');slot('panel.remaining_s',number('1',0,1800),'1','向上取整剩余秒',c='display_digits');slot('thermal.indicated_temperature_k',nullable(number('K',0)),'K','无可选MultiSensor返回null',c='display_digits')
for k,s,u,zh in [('mass_kg',array(number('kg',0),2),'kg','液/固剩余质量'),('initial_mass_kg',array(number('kg',0),2),'kg','初始质量'),('collected_mass_kg',array(number('kg',0),2),'kg','转出质量'),('temperature_k',number('K',0),'K','温度真值'),('mixing_index',number('1',0,1),'1','经验混合指数'),('entrained_gas_mol',number('mol',0),'mol','材料内气体'),('headspace_gas_mol',number('mol',0),'mol','杯顶空气体，非真空排气'),('collected_gas_mol',number('mol',0),'mol','转出气体'),('dissipated_energy_j',number('J',0),'J','经验耗散热'),('heat_to_ambient_j',number('J'),'J','累计散热'),('thermal_storage_j',number('J'),'J','留存显热'),('collected_heat_j',number('J'),'J','转移显热')]:slot('truth.'+k,s,u,zh,'process_truth','evaluator',c='cup_holder')
class ThinkyAre310sRuntime:
 def __init__(self,instance_id='are310s_01'):
  self.instance_id=instance_id;self.plant=self.method=self.sample=None;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.powered=False;self.lid_open=True;self.lid_angle=1.5;self.balance=0.;self.phase=self.mode='idle';self.time_s=self.elapsed_s=self.stage_time=self.rpm=self.spin=self.angle=self.cup_angle=self.heat=self.loss=self.storage=self.collected_heat=0.;self.stage_index=0;self.mass=[0.,0.];self.initial=self.mass.copy();self.collected=self.mass.copy();self.gas=self.headgas=self.collected_gas=self.mixed=0.;self.temperature=298.15;self.capacity=0.;self.bounds=None;self.revision=0;self.operation_id='';self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility):
  if visibility not in ['agent','debug','evaluator'] or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return ThinkyAre310sSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def gross(self):return sum(self.mass)+self.sample['cup_tare_kg']+self.sample['adapter_mass_kg'] if self.sample else 0.
 def volume(self):return self.mass[0]/self.sample['carrier_density_kg_m3']+self.mass[1]/self.sample['solid_density_kg_m3'] if self.sample else 0.
 def remaining(self):return max(0.,sum(s['duration_s'] for s in self.method['program'])-self.elapsed_s) if self.method else 0.
 def _values(self):return {'plant.configured':bool(self.plant),'method.configured':bool(self.method),'sample.configured':bool(self.sample),'power.enabled':self.powered,'lid.open_target':self.lid_open,'lid.angle_rad':self.lid_angle,'lid.locked':bool(self.phase=='running' or self.rpm>1e-9 or not self.lid_open),'balance.gross_mass_kg':self.balance,'balance.dial_angle_rad':-math.pi*5/6+self.balance/.31*math.pi*5/3,'cycle.phase':self.phase,'cycle.mode':self.mode,'cycle.stage_index':self.stage_index,'cycle.elapsed_s':self.elapsed_s,'cycle.remaining_s':self.remaining(),'rotor.revolution_rpm':self.rpm,'rotor.spin_rpm':self.spin,'rotor.angle_rad':self.angle,'cup.angle_rad':self.cup_angle,'panel.rpm':round(self.rpm),'panel.remaining_s':math.ceil(self.remaining()-1e-9),'thermal.indicated_temperature_k':None,'truth.mass_kg':self.mass,'truth.initial_mass_kg':self.initial,'truth.collected_mass_kg':self.collected,'truth.temperature_k':self.temperature,'truth.mixing_index':self.mixed,'truth.entrained_gas_mol':self.gas,'truth.headspace_gas_mol':self.headgas,'truth.collected_gas_mol':self.collected_gas,'truth.dissipated_energy_j':self.heat,'truth.heat_to_ambient_j':self.loss,'truth.thermal_storage_j':self.storage,'truth.collected_heat_j':self.collected_heat}
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ['debug','evaluator']})
 def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k!='templates'})
 def _start(self):
  if not all([self.plant,self.method,self.sample,self.powered]) or self.phase in ['running','failed'] or self.rpm>1e-9 or self.lid_open or self.lid_angle>1e-9 or sum(self.mass)<=0 or abs(self.balance-self.gross())>self.plant['balance_tolerance_kg']+1e-12:raise ActionRejected('PRECONDITION_FAILED')
  self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);self.phase='running';self.mode=self.method['program'][0]['mode'];self.elapsed_s=self.stage_time=0.;self.stage_index=0
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if (self.phase=='running' or self.rpm>1e-9) and capability in ['plant.configure','method.configure','sample.configure','balance.set','product.collect']:raise ActionRejected('BUSY')
  if capability=='plant.configure':
   if self.plant or not a['reviewed_cup_material_and_nonhazardous']:raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='method.configure':
   if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
   if sum(s['duration_s'] for s in a['program'])>1800:raise ActionRejected('INVALID_ARGUMENT')
   for s in a['program']:
    rpm=s['revolution_rpm'];lo,hi=(200,2000) if s['mode']=='mix' else (400,2200)
    if s['duration_s']!=int(s['duration_s']) or rpm%10 or (rpm!=0 and not lo<=rpm<=hi):raise ActionRejected('INVALID_ARGUMENT')
   self.method=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='sample.configure':
   if not self.plant or self.sample or self.lid_angle<1.4:raise ActionRejected('PRECONDITION_FAILED')
   if a['carrier_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   r=self.templates[a['carrier_id']];bounds=r['allowed_approximation_interval_K']
   if not r['runtime_eligible'] or not bounds[0]<=a['temperature_K']<=bounds[1] or not bounds[0]<=self.plant['ambient_K']<=bounds[1]:raise ActionRejected('REFERENCE_RANGE')
   mass=a['carrier_mass_kg']+a['solid_mass_kg'];volume=a['carrier_mass_kg']/a['carrier_density_kg_m3']+a['solid_mass_kg']/a['solid_density_kg_m3'];vmax=.00025 if a['cup_volume_m3']==.0003 else .00012;mmax=.25 if a['cup_volume_m3']==.0003 else .2
   gasvol=a['entrained_gas_mol']*8.314462618*a['temperature_K']/101325
   if mass>mmax+1e-12 or mass+a['cup_tare_kg']+a['adapter_mass_kg']>.31+1e-12 or volume+gasvol>vmax+1e-12:raise ActionRejected('CAPACITY_LIMIT')
   if a['cup_volume_m3']==.00015 and a['adapter_mass_kg']<=0:raise ActionRejected('PRECONDITION_FAILED')
   self.sample=copy.deepcopy(a);self.mass=[a['carrier_mass_kg'],a['solid_mass_kg']];self.initial=self.mass.copy();self.gas=a['entrained_gas_mol'];self.temperature=a['temperature_K'];self.bounds=bounds.copy();self.capacity=self.mass[0]*r['value']+self.mass[1]*a['solid_cp_J_kg_K']+self.plant['vessel_heat_capacity_J_K'];out=dict(configured=True,attachment_event='host_finite_loading_not_robot')
  elif capability=='power.set':
   if not a['enabled'] and (self.phase=='running' or self.rpm>1e-9):raise ActionRejected('PRECONDITION_FAILED')
   self.powered=a['enabled'];out=a
  elif capability=='power.interrupt':
   self.powered=False
   if self.phase=='running':self.phase='cancelled';self.mode='power_loss_braking'
   out=dict(phase=self.phase)
  elif capability=='lid.set_open':
   if a['open'] and (self.phase=='running' or self.rpm>1e-9 or not self.powered):raise ActionRejected('PRECONDITION_FAILED')
   self.lid_open=a['open'];out=a
  elif capability=='balance.set':
   if self.lid_angle<1.4:raise ActionRejected('PRECONDITION_FAILED')
   self.balance=a['gross_mass_kg'];out=a
  elif capability=='acquisition.start':self._start();out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('PRECONDITION_FAILED')
   self.phase='cancelled';self.mode='braking';out=dict(phase=self.phase)
  elif capability=='mixing.toggle':
   if self.phase=='running':self.phase='cancelled';self.mode='braking'
   else:self._start()
   out=dict(phase=self.phase)
  elif capability=='mixing.measure':return dict(phase=self.phase,mode=self.mode,revolution_rpm=round(self.rpm),remaining_s=self.remaining(),stage_index=self.stage_index,temperature_K=None,evidence='ideal_panel_quantization_not_factory_precision;no_optional_MultiSensor',template_source=TEMPLATE_REL)
  elif capability=='product.read':return dict(sample_id=self.sample['sample_id'] if self.sample else '',mass_kg=self.mass.copy(),initial_mass_kg=self.initial.copy(),collected_mass_kg=self.collected.copy(),temperature_K=self.temperature,mixing_index=self.mixed,entrained_gas_mol=self.gas,headspace_gas_mol=self.headgas,collected_gas_mol=self.collected_gas,dissipated_energy_J=self.heat,heat_to_ambient_J=self.loss,thermal_storage_J=self.storage,collected_sensible_heat_J=self.collected_heat,evidence='synthetic_mixing_escape_rates;NIST_Cp_not_mixing_curve',template_source=TEMPLATE_REL)
  elif capability=='product.collect':
   if not self.sample or self.phase=='idle' or self.lid_angle<1.4:raise ActionRejected('PRECONDITION_FAILED')
   oldcap=self.capacity;moved=[m*a['fraction'] for m in self.mass]
   for i,m in enumerate(moved):self.mass[i]-=m;self.collected[i]+=m
   gas=self.gas*a['fraction'];self.gas-=gas;self.collected_gas+=gas;self.capacity=self.mass[0]*self.templates[self.sample['carrier_id']]['value']+self.mass[1]*self.sample['solid_cp_J_kg_K']+self.plant['vessel_heat_capacity_J_K'];carried=(oldcap-self.capacity)*(self.temperature-self.sample['temperature_K']);self.storage-=carried;self.collected_heat+=carried;out=dict(transferred_kg=sum(moved),recipient_id=a['recipient_id'])
  elif capability=='reference.lookup':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   r=self.templates[a['substance_id']];return dict(cp_J_kg_K=r['value'],temperature_K=r['conditions']['temperature_K'],thermal_reference_eligible=r['runtime_eligible'],source=r['source'],evidence=r['evidence'],template_source=TEMPLATE_REL)
  self.revision+=1;return copy.deepcopy(out)
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or not 0<=dt_s<=3600:raise ActionRejected('INVALID_ARGUMENT')
  left=float(dt_s)
  while left>1e-11:
   h=min(.01,left);active=self.phase=='running' and self.mode!='braking';stage=self.method['program'][self.stage_index] if self.method else None
   if active:h=min(h,max(0.,stage['duration_s']-self.stage_time))
   if h<1e-11:
    if self.stage_index+1<len(self.method['program']):self.stage_index+=1;self.stage_time=0.;self.mode=self.method['program'][self.stage_index]['mode']
    else:self.mode='braking'
    continue
   target=stage['revolution_rpm'] if active and self.powered else 0.;acc=self.plant['acceleration_rpm_s'] if self.plant and target>self.rpm else (self.plant['brake_rpm_s'] if self.plant else 4000.);oldrpm=self.rpm;newrpm=oldrpm+min(acc*h,max(-acc*h,target-oldrpm));avg=(oldrpm+newrpm)/2;mode=stage['mode'] if stage else 'mix';ratio=.4 if mode=='mix' else 60/2200
   p=self.plant['reference_dissipation_W']*(avg/2000)**2 if self.plant and self.sample else 0.
   if self.sample and self.mode!='reference_limit':
    ua=self.plant['cooling_W_K'];amb=self.plant['ambient_K'];after=amb+(self.temperature-amb-p/ua)*math.exp(-ua*h/self.capacity)+p/ua if ua else self.temperature+p*h/self.capacity
    if not self.bounds[0]-1e-10<=after<=min(353.15,self.bounds[1])+1e-10:self.phase='failed';self.mode='reference_limit'
    else:
     stored=self.capacity*(after-self.temperature);self.temperature=after;self.heat+=p*h;self.storage+=stored;self.loss+=p*h-stored
     scale=min(1000.,self.plant['reference_viscosity_Pa_s']/self.sample['viscosity_Pa_s']);km=self.plant['mixing_rate_s_1']*(avg*ratio/800)*scale;kb=self.plant['bubble_escape_rate_s_1']*(avg/2200)**2*scale
     self.mixed=1-(1-self.mixed)*math.exp(-km*h);escaped=self.gas*(-math.expm1(-kb*h));self.gas-=escaped;self.headgas+=escaped
   self.rpm=newrpm;self.spin=newrpm*ratio;self.angle=(self.angle+avg*math.tau/60*h)%math.tau;self.cup_angle=-((-self.cup_angle+avg*ratio*math.tau/60*h)%math.tau)
   if active and self.phase=='running':self.stage_time+=h;self.elapsed_s+=h
   if self.phase=='running' and self.mode=='braking' and self.rpm<1e-9:self.phase='succeeded';self.mode='complete'
   targetlid=1.5 if self.lid_open else 0.;dl=(self.plant['lid_rate_rad_s'] if self.plant else 1.5)*h;self.lid_angle+=min(dl,max(-dl,targetlid-self.lid_angle));self.time_s+=h;left-=h;self.revision+=1

class ThinkyAre310sSession:
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
        meta=export_thinky_are310s_contract_metadata()
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('mixing.measure'))

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])




def export_thinky_are310s_contract_metadata():
 caps={};slots={}
 for k,(schema,unit,origin,visibility,components,write) in SLOTS.items():
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'ThinkyAre310sSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
 immediate={'plant.configure':['plant.configured'],'method.configure':['method.configured','cycle.remaining_s','panel.remaining_s'],'sample.configure':['sample.configured']+[k for k in slots if k.startswith('truth.')],'power.set':['power.enabled'],'power.interrupt':['power.enabled','cycle.phase','cycle.mode'],'lid.set_open':['lid.open_target','lid.locked'],'balance.set':['balance.gross_mass_kg','balance.dial_angle_rad'],'acquisition.start':['cycle.phase','cycle.mode','cycle.elapsed_s','cycle.stage_index','cycle.remaining_s','panel.remaining_s','lid.locked'],'acquisition.stop':['cycle.phase','cycle.mode'],'mixing.toggle':['cycle.phase','cycle.mode','cycle.elapsed_s','cycle.stage_index','cycle.remaining_s','panel.remaining_s','lid.locked'],'product.collect':[k for k in slots if k.startswith('truth.')]}
 for k,(inp,out,components,meaning,kind,vis,example) in CAPS.items():
  async_=k=='acquisition.start';caps[k]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.thinky_are310s.ThinkyAre310sSession.invoke',invocation=f'session.invoke(instance_id="are310s_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if kind=='query' else [dict(description_zh='装杯、质量、兼容性、盖锁与宿主配平容差；非安全认证',state_ids=['cycle.phase','sample.configured','power.enabled','lid.angle_rad','balance.gross_mass_kg'],enforced_by='runtime',check='ThinkyAre310sRuntime.invoke capability guards')],state_effects=[dict(state_id=i,timing='immediate',description_zh='配置、目标或有限转移') for i in immediate.get(k,[])]+([dict(state_id=i,timing='on_step',description_zh='唯一宿主时钟推进加减速、公自转、热量、混合和气体转移') for i in slots if i.startswith(('truth.','cycle.','rotor.','cup.','panel.','lid.'))] if k in ['acquisition.start','acquisition.stop','mixing.toggle','lid.set_open','power.interrupt'] else []),observes=[],model_effects=dict(mode='none',description_zh='实际场景验证后才声明联动；未求解流体',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='程序结束且制动停稳；非无泡质量认证' if async_ else '配置、目标或查询成功',completion_state_ids=['cycle.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['finite_cup','host_clock','principal_request_cache'],partial_failure_zh='取消保留混合、气体与热量；仍需step制动，盖锁保留'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT','UNKNOWN_REFERENCE','REFERENCE_RANGE','CAPACITY_LIMIT']],example_arguments=copy.deepcopy(example),verification=[],limitations=['SCE速度仅为显式教学包络，非UL手册认证；过程系数需标定'],source_ids=['manufacturer_primary','manufacturer_manual','simulation_assumption'])
 mechanism=dict(component_ids=['rotor','cup_holder','lid','counterweight','display_digits'],state_ids=list(slots),meaning_zh='45度杯公自转；有限非挥发双相样品与杯内气体守恒',implementation='lumped',equations=['Gross=liquid+solid+cup+adapter<=0.310kg;net<=0.250kg;150mL conservative family limits0.200kg/120mL','SCE reference:mix0 or200..2000rpm,spin=0.4*orbit;defoam0 or400..2200rpm,spin=(60/2200)*orbit;10rpm increments','Only step moves lid/integrates acceleration-limited orbit/opposite spin; timer includes ramp; completion waits for brake','I_next=1-(1-I)*exp(-host_kmix*(spin/800)*(mu_ref/mu)*dt); synthetic not CFD','n_next=n*exp(-host_kescape*(orbit/2200)^2*(mu_ref/mu)*dt); escaped gas stays in headspace','n_initial=n_entrained+n_headspace+n_collected; no vacuum/dissolution/reaction','Pheat=host_Pref*(orbit/2000)^2;900VA electrical apparent input is NOT sample heating W','C=m_liquid*Cp_NIST+m_solid*Cp_host+C_cup_host;C*dT/dt=Pheat-UA*(T-Tambient)','E=thermal_storage+ambient_loss+collected_heat;species initial=remaining+collected','Cp guarded local interval; boundary failure is not a factory overtemperature sensor'],parameters={},source_ids=['manufacturer_primary','manufacturer_manual','nist_cp','simulation_assumption'],limitations=['UL-specific numeric speed and orbit radius not verified;SCE parameters explicit task assumptions.','79 real liquid Cp references are not50 measured mixing/defoaming curves;density,viscosity,compatibility and kinetics remain host input.','No vacuum/CFD/bubble-size/surface-tension/coalescence/dissolution/vapor-pressure/resin-curing model.','300mL installed geometry;150mL cup and adapter parked; runtime150 cup not dynamically swapped.','Counterweight mass/travel,lock firmware,sensor errors and power-loss coast uncalibrated.','No optional MultiSensor/ENS10/full recipe storage/touch UI/communications.','Temperature evaluator-only;normal panel temperature null.','Part masses/inertia/friction unknown;0.1kg numerical parts and bounding-box inertias not actual decomposition.'])
 spec=importlib.util.spec_from_file_location('_thinky_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'mixing':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.configure':(SAMPLE,example_sample())});mechanism['parameters']={k.lower():v for k,v in mechanism['parameters'].items()}
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'mixing':mechanism})

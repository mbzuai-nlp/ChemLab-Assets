"""909 UV Digester: explicit teaching photochemistry, real OH reference inputs."""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.pccell_ed64002 import validate
ROOT=Path(__file__).resolve().parents[2];SLUG='metrohm_909uv';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-046';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/metrohm_909uv_digestion_library/templates.json'
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
def nullable(s):return dict(anyOf=[s,dict(type='null')])
def array(s,n=None):return dict(type='array',items=s,minItems=n or 0,**({'maxItems':n} if n else {}))
PLANT=obj({'ambient_K':number('K',283.15,308.15),'density_kg_m3':number('kg/m^3',950,1100),'liquid_cp_J_kg_K':number('J/(kg*K)',3800,4500),'dry_heat_capacity_J_K':number('J/K',10,5000),'absorbed_electrical_fraction':number('1',0,1),'passive_UA_W_K':number('W/K',0,10),'fan_UA_W_K':number('W/K',1,30),'lamp_power_limit_W':number('W',300,600),'oh_concentration_mol_m3':number('mol/m^3',0,1e-5),'site_weights':array(number('1',0,2),12),'temperature_rate_factor':number('1',0,100),'shield_speed_rad_s':number('rad/s',.1,5),'reviewed_aqueous_uv_h2o2_compatibility':BOOL,'accept_unverified_thermal_kinetics':BOOL,'coefficient_evidence':TEXT})
PLANT_EXAMPLE=dict(ambient_K=298.15,density_kg_m3=1000.,liquid_cp_J_kg_K=4180.,dry_heat_capacity_J_K=200.,absorbed_electrical_fraction=.7,passive_UA_W_K=.4,fan_UA_W_K=8.,lamp_power_limit_W=600.,oh_concentration_mol_m3=1e-9,site_weights=[1.]*12,temperature_rate_factor=1.,shield_speed_rad_s=math.pi,reviewed_aqueous_uv_h2o2_compatibility=True,accept_unverified_thermal_kinetics=True,coefficient_evidence='explicit_host_teaching_assumptions_not_device_calibration')
METHOD=obj({'temperature_K':number('K',353.15,373.15),'duration_s':number('s',60,59940)})
METHOD_EXAMPLE=dict(temperature_K=363.15,duration_s=120.)
TUBE=obj({'sample_id':TEXT,'substance_id':TEXT,'volume_m3':number('m^3',1e-6,12e-6),'parent_mol':number('mol',0,1e-5),'pH':nullable(number('1',0,14)),'accept_unknown_reference_pH':BOOL})
SAMPLE=obj({'tubes':array(TUBE,12),'temperature_K':number('K',283.15,308.15),'sensor_connected':BOOL,'sensor_position':dict(type='integer',const=6,**{'x-unit':'1'})})
def example_sample():return dict(tubes=[dict(sample_id='sample_'+str(i+1),substance_id='acetone' if i==5 else '',volume_m3=10e-6,parent_mol=1e-7 if i==5 else 0.,pH=7. if i==5 else None,accept_unknown_reference_pH=False) for i in range(12)],temperature_K=298.15,sensor_connected=True,sensor_position=6)
MEASURE=obj({'phase':PHASE,'status_text':TEXT,'temperature_K':nullable(number('K',0)),'target_K':number('K',353.15,373.15),'remaining_s':number('s',0,59940),'cooldown_s':number('s',0,600),'lamp_power_W':number('W',0,600),'evidence':TEXT,'template_source':TEXT})
PRODUCT=obj({'parent_mol':array(number('mol',0),12),'transformed_parent_equivalent_mol':array(number('mol',0),12),'collected_parent_equivalent_mol':array(number('mol',0),12),'initial_parent_mol':array(number('mol',0),12),'volume_m3':array(number('m^3',0),12),'collected_volume_m3':array(number('m^3',0),12),'absorbed_energy_J':number('J',0),'thermal_storage_J':number('J'),'ambient_heat_J':number('J'),'collected_heat_J':number('J'),'temperature_K':number('K',0),'evidence':TEXT,'template_source':TEXT})
CAPS={
 'plant.configure':(PLANT,obj({'configured':BOOL}),['body','lamp','fan'],'宿主光化学/热模型输入；没有从灯电功率虚构光强','configuration','evaluator',PLANT_EXAMPLE),
 'method.configure':(METHOD,obj({'configured':BOOL}),['display_digits','button_set'],'80–100°C整度、1–999整分钟；照射期间可修改','configuration','agent',METHOD_EXAMPLE),
 'thermal.set_temperature':(obj({'temperature_K':number('K',353.15,373.15)}),obj({'temperature_K':number('K',353.15,373.15)}),['display_digits','button_up','button_down'],'仅写温度目标，step推进实际温度','device_command','agent',dict(temperature_K=363.15)),
 'sample.configure':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['sample_holder','pt1000'],'12支石英管装样，空位必须有水；Pt1000固定6号位','configuration','evaluator',example_sample()),
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['button_power','power_inlet'],'供电；正常关机须完成冷却','device_command','agent',dict(enabled=True)),
 'power.interrupt':(obj({}),obj({'phase':PHASE}),['power_inlet'],'意外断电保留已反应样品，冷却计时暂停','simulation','evaluator',{}),
 'shield.set_open':(obj({'open':BOOL}),obj({'open':BOOL}),['shield'],'旋转防护罩目标；照射时拒绝属于宿主安全策略而非厂家电子开关','device_command','agent',dict(open=False)),
 'sensor.set_connected':(obj({'connected':BOOL}),obj({'connected':BOOL}),['pt1000','sensor_socket'],'宿主温度探头连接事件；断开照射失败','simulation','evaluator',dict(connected=True)),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['button_start_stop','lamp','fan'],'照射计时自点灯开始，结束后600s风冷','device_command','agent',{}),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['button_start_stop','lamp','fan'],'取消保留已发生效果，灯灭后600s冷却','device_command','agent',{}),
 'digestion.toggle':(obj({}),obj({'phase':PHASE}),['button_start_stop'],'START/STOP按键','device_command','agent',{}),
 'digestion.measure':(obj({}),MEASURE,['display_digits'],'理想6号管Pt1000温度与时间摘要；不输出有机组分真值','query','agent',{}),
 'product.read':(obj({}),PRODUCT,['sample_holder'],'评价器有限样品/母体当量/热账本，非矿化检测','query','evaluator',{}),
 'product.collect':(obj({'fraction':number('1',0,1),'recipient_id':TEXT}),obj({'transferred_volume_m3':number('m^3',0),'recipient_id':TEXT}),['sample_holder','shield'],'冷却结束开罩后宿主按比例转移12管；不是自动取管','device_command','evaluator',dict(fraction=.25,recipient_id='receiver')),
 'reference.lookup':(obj({'substance_id':TEXT}),obj({'rate_m3_mol_s':number('m^3/(mol*s)',0),'temperature_interval_K':array(number('K',0),2),'pH_interval':nullable(array(number('1',0,14),2)),'source':TEXT,'evidence':TEXT,'template_source':TEXT}),['display_digits'],'NIST水相OH速率参考，不代表本机实测曲线','query','agent',dict(substance_id='acetone')),
 'lamp.replace':(obj({'replacement_confirmed':BOOL}),obj({'usage_s':number('s',0),'ignitions':number('1',0)}),['lamp'],'断电冷却开罩后的宿主换灯事务；原始寿命归零','configuration','evaluator',dict(replacement_confirmed=True)),
 'fault.acknowledge':(obj({}),obj({'phase':PHASE}),['button_quit'],'原因已消除且冷却完毕后确认故障','device_command','agent',{}),
}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,zh,o='derived',v='agent',c='body',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=zh
for n in ['plant','method','sample']:slot(n+'.configured',BOOL,'1','宿主'+n+'配置完成','setpoint')
slot('power.enabled',BOOL,'1','供电设定','setpoint',c='button_power',w=('power.set','enabled'))
slot('shield.open_target',BOOL,'1','防护罩手工旋转目标','setpoint',c='shield',w=('shield.set_open','open'));slot('shield.angle_rad',number('rad',0,math.pi),'rad','旋转半罩绕Z角','scene',c='shield')
slot('thermal.target_k',number('K',353.15,373.15),'K','温控目标','setpoint',c='display_digits',w=('thermal.set_temperature','temperature_K'));slot('thermal.indicated_temperature_k',nullable(number('K',0)),'K','6号位理想温度取0.1K量化，不宣称±3K测量误差',c='display_digits')
slot('sensor.connected',BOOL,'1','Pt1000宿主连接状态','scene',c='pt1000')
slot('cycle.phase',PHASE,'1','照射异步状态');slot('cycle.status_text',TEXT,'1','Heating/Temp.reached/Lampcooling/故障');slot('cycle.elapsed_s',number('s',0),'s','点灯后累计照射秒');slot('cycle.remaining_s',number('s',0,59940),'s','剩余照射秒');slot('cycle.cooldown_s',number('s',0,600),'s','结束后风冷剩余秒')
slot('lamp.power_w',number('W',0,600),'W','自动调节灯电功率，不是光功率',c='lamp');slot('lamp.usage_s',number('s',0),'s','累计点灯时间',c='lamp');slot('lamp.ignitions',number('1',0),'1','点灯次数',c='lamp');slot('fan.running',BOOL,'1','照射和冷却时风扇工作',c='fan');slot('panel.remaining_min',number('1',0,999),'1','向上取整剩余分钟',c='display_digits')
for k,s,u,zh in [('parent_mol',array(number('mol',0),12),'mol','各管母体示踪摩尔数'),('converted_mol',array(number('mol',0),12),'mol','转化物母体当量，不代表产物摩尔数'),('initial_mol',array(number('mol',0),12),'mol','初始母体示踪摩尔数'),('collected_mol',array(number('mol',0),12),'mol','转出母体及转化池当量'),('volume_m3',array(number('m^3',0),12),'m^3','各管剩余液体体积'),('collected_volume_m3',array(number('m^3',0),12),'m^3','转出各管液体体积'),('temperature_k',number('K',0),'K','共温集中模型真值'),('absorbed_energy_j',number('J',0),'J','宿主热吸收累计'),('thermal_storage_j',number('J'),'J','保留显热'),('ambient_heat_j',number('J'),'J','向环境净放热'),('collected_heat_j',number('J'),'J','转出显热'),('oh_exposure_mol_s_m3',array(number('mol*s/m^3',0),12),'mol*s/m^3','各管宿主OH浓度时间积分')]:slot('truth.'+k,s,u,zh,'process_truth','evaluator',c='sample_holder')
class Metrohm909uvRuntime:
 def __init__(self,instance_id='uv909_01'):
  self.instance_id=instance_id;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.plant=self.method=self.sample=None;self.powered=False;self.shield_open=True;self.shield_angle=math.pi;self.sensor=False;self.phase='idle';self.fault='';self.temperature=self.initial_temperature=298.15;self.target=363.15;self.duration=5400.;self.time_s=self.elapsed_s=self.cooldown=self.lamp_power=self.lamp_usage=self.energy=self.loss=self.storage=self.collected_heat=0.;self.ignitions=0;self.parent=[0.]*12;self.converted=[0.]*12;self.initial=[0.]*12;self.collected=[0.]*12;self.volumes=[0.]*12;self.collected_volumes=[0.]*12;self.exposure=[0.]*12;self.capacity=0.;self.revision=0;self.operation_id='';self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility):
  if visibility not in ['agent','debug','evaluator'] or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return Metrohm909uvSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def remaining(self):return max(0.,self.duration-self.elapsed_s)
 def status_text(self):return self.fault or ('Lamp cooling' if self.cooldown>1e-9 else ('Temp. reached' if abs(self.temperature-self.target)<=3 else 'Heating') if self.phase=='running' else self.phase)
 def indicated(self):return round(self.temperature-273.15,1)+273.15 if self.sensor and self.powered else None
 def _values(self):return {'plant.configured':bool(self.plant),'method.configured':bool(self.method),'sample.configured':bool(self.sample),'power.enabled':self.powered,'shield.open_target':self.shield_open,'shield.angle_rad':self.shield_angle,'thermal.target_k':self.target,'thermal.indicated_temperature_k':self.indicated(),'sensor.connected':self.sensor,'cycle.phase':self.phase,'cycle.status_text':self.status_text(),'cycle.elapsed_s':self.elapsed_s,'cycle.remaining_s':self.remaining(),'cycle.cooldown_s':self.cooldown,'lamp.power_w':self.lamp_power,'lamp.usage_s':self.lamp_usage,'lamp.ignitions':self.ignitions,'fan.running':bool(self.powered and (self.phase=='running' or self.cooldown>1e-9)),'panel.remaining_min':math.ceil((self.remaining()-1e-9)/60),'truth.parent_mol':self.parent,'truth.converted_mol':self.converted,'truth.initial_mol':self.initial,'truth.collected_mol':self.collected,'truth.volume_m3':self.volumes,'truth.collected_volume_m3':self.collected_volumes,'truth.temperature_k':self.temperature,'truth.absorbed_energy_j':self.energy,'truth.thermal_storage_j':self.storage,'truth.ambient_heat_j':self.loss,'truth.collected_heat_j':self.collected_heat,'truth.oh_exposure_mol_s_m3':self.exposure}
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ['debug','evaluator']})
 def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k!='templates'})
 def _end(self,phase,fault=''):self.phase=phase;self.fault=fault;self.cooldown=600.;self.lamp_power=0.
 def _start(self):
  if not all([self.plant,self.method,self.sample,self.powered,self.sensor]) or self.phase in ['running','failed'] or self.cooldown>1e-9 or self.shield_open or self.shield_angle>1e-9 or min(self.volumes)<=0 or self.lamp_usage>=750*3600 or self.temperature>378.15:raise ActionRejected('PRECONDITION_FAILED')
  self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);self.phase='running';self.elapsed_s=0.;self.ignitions+=1;self.fault=''
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if (self.phase=='running' or self.cooldown>1e-9) and capability in ['plant.configure','sample.configure','product.collect','lamp.replace']:raise ActionRejected('BUSY')
  if capability=='plant.configure':
   if self.plant or not a['reviewed_aqueous_uv_h2o2_compatibility'] or not a['accept_unverified_thermal_kinetics'] or a['fan_UA_W_K']<a['passive_UA_W_K']:raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);out=dict(configured=True)
  elif capability in ['method.configure','thermal.set_temperature']:
   if abs((a['temperature_K']-273.15)-round(a['temperature_K']-273.15))>1e-8:raise ActionRejected('INVALID_ARGUMENT')
   if capability=='method.configure' and a['duration_s']%60:raise ActionRejected('INVALID_ARGUMENT')
   self.target=a['temperature_K']
   if capability=='method.configure':self.duration=a['duration_s'];self.method=copy.deepcopy(a);out=dict(configured=True)
   else:out=a
  elif capability=='sample.configure':
   if not self.plant or self.sample or self.shield_angle<math.pi-1e-9:raise ActionRejected('PRECONDITION_FAILED')
   for t in a['tubes']:
    sid=t['substance_id']
    if not sid:
     if t['parent_mol']!=0:raise ActionRejected('INVALID_ARGUMENT')
     continue
    if sid not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
    ph=self.templates[sid]['conditions']['pH_interval']
    if ph is None and not t['accept_unknown_reference_pH']:raise ActionRejected('REFERENCE_RANGE')
    if ph is not None and (t['pH'] is None or not ph[0]<=t['pH']<=ph[1]):raise ActionRejected('REFERENCE_RANGE')
   self.sample=copy.deepcopy(a);self.parent=[t['parent_mol'] for t in a['tubes']];self.initial=self.parent.copy();self.volumes=[t['volume_m3'] for t in a['tubes']];self.temperature=self.initial_temperature=a['temperature_K'];self.capacity=sum(self.volumes)*self.plant['density_kg_m3']*self.plant['liquid_cp_J_kg_K']+self.plant['dry_heat_capacity_J_K'];self.sensor=a['sensor_connected'];out=dict(configured=True,attachment_event='host_finite_12_tube_loading_not_robot')
  elif capability=='power.set':
   if not a['enabled'] and (self.phase=='running' or self.cooldown>1e-9):raise ActionRejected('PRECONDITION_FAILED')
   self.powered=a['enabled'];out=a
  elif capability=='power.interrupt':
   self.powered=False
   if self.phase=='running':self._end('cancelled')
   out=dict(phase=self.phase)
  elif capability=='shield.set_open':
   if a['open'] and self.phase=='running':raise ActionRejected('PRECONDITION_FAILED')
   self.shield_open=a['open'];out=a
  elif capability=='sensor.set_connected':
   self.sensor=a['connected']
   if not self.sensor and self.phase=='running':self._end('failed','No sensor connectd')
   out=a
  elif capability=='acquisition.start':self._start();out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('PRECONDITION_FAILED')
   self._end('cancelled');out=dict(phase=self.phase)
  elif capability=='digestion.toggle':
   if self.phase=='running':self._end('cancelled')
   else:self._start()
   out=dict(phase=self.phase)
  elif capability=='digestion.measure':return dict(phase=self.phase,status_text=self.status_text(),temperature_K=self.indicated(),target_K=self.target,remaining_s=self.remaining(),cooldown_s=self.cooldown,lamp_power_W=self.lamp_power,evidence='ideal_Pt1000_position6;host_thermal_model;not_measured_digestion_curve',template_source=TEMPLATE_REL)
  elif capability=='product.read':return dict(parent_mol=self.parent.copy(),transformed_parent_equivalent_mol=self.converted.copy(),collected_parent_equivalent_mol=self.collected.copy(),initial_parent_mol=self.initial.copy(),volume_m3=self.volumes.copy(),collected_volume_m3=self.collected_volumes.copy(),absorbed_energy_J=self.energy,thermal_storage_J=self.storage,ambient_heat_J=self.loss,collected_heat_J=self.collected_heat,temperature_K=self.temperature,evidence='NIST_room_temperature_OH_rates;synthetic_host_exposure_and_thermal_factor;NOT_mineralization',template_source=TEMPLATE_REL)
  elif capability=='product.collect':
   if not self.sample or self.phase=='idle' or self.shield_angle<math.pi-1e-9:raise ActionRejected('PRECONDITION_FAILED')
   f=a['fraction'];oldcap=self.capacity;v=sum(self.volumes)*f
   for i in range(12):self.collected[i]+=(self.parent[i]+self.converted[i])*f;self.parent[i]*=1-f;self.converted[i]*=1-f;self.collected_volumes[i]+=self.volumes[i]*f;self.volumes[i]*=1-f
   self.capacity=sum(self.volumes)*self.plant['density_kg_m3']*self.plant['liquid_cp_J_kg_K']+self.plant['dry_heat_capacity_J_K'];heat=(oldcap-self.capacity)*(self.temperature-self.initial_temperature);self.storage-=heat;self.collected_heat+=heat;out=dict(transferred_volume_m3=v,recipient_id=a['recipient_id'])
  elif capability=='reference.lookup':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   r=self.templates[a['substance_id']];return dict(rate_m3_mol_s=r['value'],temperature_interval_K=r['conditions']['temperature_interval_K'],pH_interval=r['conditions']['pH_interval'],source=r['source'],evidence=r['evidence'],template_source=TEMPLATE_REL)
  elif capability=='lamp.replace':
   if self.powered or self.shield_angle<math.pi-1e-9 or not a['replacement_confirmed']:raise ActionRejected('PRECONDITION_FAILED')
   self.lamp_usage=0.;self.ignitions=0;out=dict(usage_s=0.,ignitions=0)
  elif capability=='fault.acknowledge':
   if self.phase!='failed' or self.cooldown>1e-9 or not self.sensor or self.temperature>378.15 or self.lamp_usage>=750*3600:raise ActionRejected('PRECONDITION_FAILED')
   self.phase='idle';self.fault='';out=dict(phase=self.phase)
  self.revision+=1;return copy.deepcopy(out)
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or not 0<=dt_s<=3600:raise ActionRejected('INVALID_ARGUMENT')
  left=float(dt_s)
  while left>1e-10:
   h=min(.1,left);active=self.phase=='running'
   if active:
    if self.elapsed_s>=self.duration-1e-9:self._end('succeeded');continue
    if self.lamp_usage>=750*3600-1e-9:self._end('failed','Lamp exhausted');continue
    h=min(h,self.duration-self.elapsed_s,750*3600-self.lamp_usage)
   p=0.;cooling_before=self.cooldown>1e-9
   if self.sample:
    amb=self.plant['ambient_K'];ua=self.plant['passive_UA_W_K']
    if active:
     self.lamp_power=min(self.plant['lamp_power_limit_W'],max(300.,300.+100.*(self.target-self.temperature)));p=self.lamp_power*self.plant['absorbed_electrical_fraction']
     if self.temperature>=self.target-.5:ua=min(self.plant['fan_UA_W_K'],max(ua,p/(self.target-amb)))
    elif self.powered and cooling_before:ua=self.plant['fan_UA_W_K']
    after=amb+(self.temperature-amb-p/ua)*math.exp(-ua*h/self.capacity)+p/ua if ua else self.temperature+p*h/self.capacity
    stored=self.capacity*(after-self.temperature);self.temperature=after;self.energy+=p*h;self.storage+=stored;self.loss+=p*h-stored
    if active:
     for i,t in enumerate(self.sample['tubes']):
      dose=self.plant['oh_concentration_mol_m3']*self.plant['site_weights'][i]*h;self.exposure[i]+=dose
      if t['substance_id']:
       k=self.templates[t['substance_id']]['value']*self.plant['temperature_rate_factor'];changed=self.parent[i]*(-math.expm1(-k*dose));self.parent[i]-=changed;self.converted[i]+=changed
     self.elapsed_s+=h;self.lamp_usage+=h
     if self.temperature>378.15:self._end('failed','Max. temp. reached')
     elif self.elapsed_s>=self.duration-1e-9:self._end('succeeded')
   if cooling_before and self.powered:self.cooldown=max(0.,self.cooldown-h)
   target=math.pi if self.shield_open else 0.;dq=(self.plant['shield_speed_rad_s'] if self.plant else math.pi)*h;self.shield_angle+=min(dq,max(-dq,target-self.shield_angle));self.time_s+=h;left-=h;self.revision+=1

class Metrohm909uvSession:
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
        meta=export_metrohm_909uv_contract_metadata()
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('digestion.measure'))

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])





def export_metrohm_909uv_contract_metadata():
 caps={};slots={}
 for k,(schema,unit,origin,visibility,components,write) in SLOTS.items():slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'Metrohm909uvSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
 immediate={'plant.configure':['plant.configured'],'method.configure':['method.configured','thermal.target_k','cycle.remaining_s','panel.remaining_min'],'thermal.set_temperature':['thermal.target_k'],'sample.configure':['sample.configured','sensor.connected','thermal.indicated_temperature_k']+[k for k in slots if k.startswith('truth.')],'power.set':['power.enabled','thermal.indicated_temperature_k'],'power.interrupt':['power.enabled','cycle.phase','cycle.status_text','cycle.cooldown_s','lamp.power_w','fan.running','thermal.indicated_temperature_k'],'shield.set_open':['shield.open_target'],'sensor.set_connected':['sensor.connected','thermal.indicated_temperature_k','cycle.phase','cycle.status_text','cycle.cooldown_s','lamp.power_w'],'acquisition.start':['cycle.phase','cycle.status_text','cycle.elapsed_s','cycle.remaining_s','panel.remaining_min','lamp.ignitions','fan.running'],'acquisition.stop':['cycle.phase','cycle.status_text','cycle.cooldown_s','lamp.power_w'],'digestion.toggle':['cycle.phase','cycle.status_text','cycle.elapsed_s','cycle.remaining_s','panel.remaining_min','lamp.ignitions','cycle.cooldown_s','lamp.power_w','fan.running'],'product.collect':[k for k in slots if k.startswith('truth.')],'lamp.replace':['lamp.usage_s','lamp.ignitions'],'fault.acknowledge':['cycle.phase','cycle.status_text']}
 for k,(inp,out,components,meaning,kind,vis,example) in CAPS.items():
  async_=k=='acquisition.start';caps[k]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.metrohm_909uv.Metrohm909uvSession.invoke',invocation=f'session.invoke(instance_id="uv909_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if kind=='query' else [dict(description_zh='12管装样、温度探头、罩位置、供电与600s冷却；罩守卫是宿主政策非厂家电子联锁',state_ids=['cycle.phase','sample.configured','power.enabled','shield.angle_rad','sensor.connected','cycle.cooldown_s'],enforced_by='runtime',check='Metrohm909uvRuntime.invoke guards')],state_effects=[dict(state_id=i,timing='immediate',description_zh='配置/目标/有限转移或事件反馈') for i in immediate.get(k,[])]+([dict(state_id=i,timing='on_step',description_zh='唯一宿主时钟推进旋转罩/温度/光照剂量/有限反应/冷却') for i in slots if i.startswith(('truth.','cycle.','lamp.','fan.','thermal.indicated','shield.angle','panel.'))] if k in ['acquisition.start','acquisition.stop','digestion.toggle','shield.set_open','power.interrupt','thermal.set_temperature','method.configure'] else []),observes=[],model_effects=dict(mode='none',description_zh='独立物理层；实际场景验证后声明同步',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='照射计时结束，仍需600s冷却方可再启动；不是矿化完成认证' if async_ else '配置/目标/查询或宿主事务完成',completion_state_ids=['cycle.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['finite_tubes','host_clock','principal_request_cache'],partial_failure_zh='取消和失败保留已反应母体当量、热量；600s冷却继续由step推进'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT','UNKNOWN_REFERENCE','REFERENCE_RANGE']],example_arguments=copy.deepcopy(example),verification=[],limitations=['室温OH速率和显式宿主剂量不等于真实80–100°C消解曲线；罩守卫非厂家电子联锁'],source_ids=['manufacturer_primary','manufacturer_manual','simulation_assumption'])
 mech=dict(component_ids=['body','lamp','fan','sample_holder','pt1000','shield','display_digits'],state_ids=list(slots),meaning_zh='12管共温集总热过程、有限母体示踪攻击及转化池；只观测6号位',implementation='lumped',equations=['12 aqueous quartz tubes,0<V_i<=12e-6m3; unused positions contain water; sensor position6','Target353.15..373.15K in1K increments; timer60..59940s in60s increments; timer begins at lamp ignition','Lamp electrical power300..600W automatic modulation; host power cap not a factory manual UI setting','Pheat=host_absorbed_fraction*P_electrical; C=rho*sum(V)*Cp_host+C_dry_host','C*dT/dt=Pheat-UA_host*(T-Tambient); piecewise analytic integration; fan UA controls target','k_SI=0.001*k_NIST_dm3; k_used=k_SI*explicit_host_temperature_factor; room-temperature data not measured90C rate','D_i=integral(host_OH_concentration*host_site_weight_i dt); parent_next=parent*exp(-k_used*dD)','initial_parent_equivalent=parent+transformed_parent_equivalent+collected_equivalent; NOT carbon/mineralization/product speciation','initial_volume=remaining_volume+collected_volume; no evaporation model; dilute tracers negligible mass/heat','absorbed_heat=retained_sensible_heat+ambient_loss+collected_sensible_heat','After end/cancel/fault:lampOFF,fanON600s;if supply absent cooling timer pauses;over105C trip','Only step advances temperature/shield/exposure/clock; commands do not teleport mechanical states'],parameters={},source_ids=['manufacturer_manual','manufacturer_brochure','nist_oh','simulation_assumption'],limitations=['50 real aqueous OH reference rates but zero measured909 digestion curves.','OH exposure,thermal correction,site weights,heat capacity,UA and optical absorption uncalibrated host assumptions.','No H2O2 depletion,radical competition,pH drift,absorbance,aging flux law,evaporation or redox/mineralization network.','Common temperature represents12tubes; only sensor6; no CFD or radiant transport.','Shield safety guard is host policy,not verified electronic interlock.','Full graphical menus/lamp-life limit editing/sample-holder removal/probe cable flexibility not implemented.','Lamp lifetime fixed750h(default/recommendation); manufacturer configurable0..1000h not implemented.','Part masses/friction/inertia unknown; numerical0.1kg per part is not physical decomposition.','Robot/hardware/UV containment safety certification not verified.'])
 spec=importlib.util.spec_from_file_location('_metrohm_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'digestion':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.configure':(SAMPLE,example_sample())});mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'digestion':mech})

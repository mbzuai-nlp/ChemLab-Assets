"""Six-position acid hydrolysis teaching model; not an E500 fat analyzer."""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.pccell_ed64002 import validate
ROOT=Path(__file__).resolve().parents[4] if 'outputs' in str(__file__) else Path(__file__).resolve().parents[2]
SLUG='buchi_hydrolexh';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-049';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/buchi_hydrolexh_hydrolysis_library/templates.json'
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed']);MODE=dict(type='string',enum=['off','preheat','work']);POS=dict(type='integer',minimum=1,maximum=6,**{'x-unit':'1'})
def array(s):return dict(type='array',items=s)
PLANT=obj({'ambient_K':number('K',278.15,313.15),'heater_capacity_J_K':number('J/K',100,5000),'sample_capacity_J_K':number('J/K',10,2000),'plate_UA_W_K':number('W/K',0,10),'sample_UA_W_K':number('W/K',0,5),'contact_UA_W_K':number('W/K',.1,20),'work_fraction':number('1',.05,1),'sample_boiling_K':number('K',350,400),'filter_rate_m3_s':number('m^3/s',1e-8,10e-6),'tube_holdup_m3':number('m^3',1e-6,64e-6),'neutral_acid_mol_m3':number('mol/m^3',1e-6,.1),'mechanical_speed_fraction_s':number('1/s',.1,10),'accept_uncalibrated_model':BOOL,'coefficient_evidence':TEXT})
PLANT_EXAMPLE=dict(ambient_K=298.15,heater_capacity_J_K=1800.,sample_capacity_J_K=450.,plate_UA_W_K=1.,sample_UA_W_K=.1,contact_UA_W_K=5.,work_fraction=.65,sample_boiling_K=380.,filter_rate_m3_s=2e-6,tube_holdup_m3=4e-6,neutral_acid_mol_m3=.001,mechanical_speed_fraction_s=2.,accept_uncalibrated_model=True,coefficient_evidence='explicit_host_assumptions_not_measured_H506_calibration')
METHOD=obj({'hydrolysis_s':number('s',1,7200),'rinse_water_K':number('K',313.15,323.15),'minimum_rinse_cycles':dict(type='integer',minimum=3,maximum=20,**{'x-unit':'1'}),'minimum_rinse_m3':number('m^3',0,.001),'accept_host_manual_sequence_automation':BOOL})
METHOD_EXAMPLE=dict(hydrolysis_s=1800.,rinse_water_K=323.15,minimum_rinse_cycles=3,minimum_rinse_m3=400e-6,accept_host_manual_sequence_automation=True)
SAMPLE=obj({'position':POS,'substance_id':TEXT,'sample_mass_kg':number('kg',0,.010),'sample_liquid_m3':number('m^3',0,50e-6),'acid_volume_m3':number('m^3',1e-6,100e-6),'acid_mol_m3':number('mol/m^3',1,10000),'bound_fat_fraction':number('1',0,1),'release_rate_s_inverse':number('1/s',0,.1),'fat_capture_fraction':number('1',0,1),'accept_reference_not_hydrolysis_calibration':BOOL})
def example_sample(position=1):return dict(position=position,substance_id='buchi_milk_powder',sample_mass_kg=.002,sample_liquid_m3=0.,acid_volume_m3=100e-6,acid_mol_m3=4000.,bound_fat_fraction=.8,release_rate_s_inverse=.003,fat_capture_fraction=.995,accept_reference_not_hydrolysis_calibration=True)
MEASURE=obj({'phase':PHASE,'stage':TEXT,'elapsed_s':number('s',0),'power_mode':MODE,'positions_loaded':dict(type='integer',minimum=0,maximum=6,**{'x-unit':'1'}),'evidence':TEXT,'template_source':TEXT})
PRODUCT=obj({'initial_fat_kg':number('kg',0),'bound_in_vessels_kg':number('kg',0),'free_in_vessels_kg':number('kg',0),'retained_bound_kg':number('kg',0),'retained_extractable_kg':number('kg',0),'waste_fat_kg':number('kg',0),'collected_fat_kg':number('kg',0),'initial_acid_mol':number('mol',0),'remaining_acid_mol':number('mol',0),'waste_acid_mol':number('mol',0),'collected_acid_mol':number('mol',0),'initial_volume_m3':number('m^3',0),'remaining_volume_m3':number('m^3',0),'waste_volume_m3':number('m^3',0),'collected_volume_m3':number('m^3',0),'initial_dry_solid_kg':number('kg',0),'remaining_dry_solid_kg':number('kg',0),'collected_dry_solid_kg':number('kg',0),'electrical_J':number('J',0),'storage_J':number('J'),'loss_J':number('J'),'boiling_J':number('J',0),'rinse_sensible_J':number('J'),'evidence':TEXT,'template_source':TEXT})
CAPS={
 'plant.configure':(PLANT,obj({'configured':BOOL}),['heater'],'显式宿主七节点热/过滤参数','configuration','evaluator',PLANT_EXAMPLE),
 'method.configure':(METHOD,obj({'configured':BOOL}),['power_dial'],'宿主手工步骤自动化及水解/洗涤时间，不是原厂程序菜单','configuration','agent',METHOD_EXAMPLE),
 'sample.configure':(SAMPLE,obj({'configured':BOOL}),['rack'],'每位有限样品、酸、参考脂肪和基体参数','configuration','evaluator',example_sample()),
 'water.configure':(obj({'volume_m3':number('m^3',0,.01)}),obj({'configured':BOOL}),['water_port'],'外部有限温水库存，一次配置','configuration','evaluator',dict(volume_m3=.005)),
 'ventilation.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['fume_port'],'宿主通风安全条件，不是原厂电子传感器','simulation','evaluator',dict(enabled=True)),
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['button_power'],'主电源设定','device_command','agent',dict(enabled=True)),
 'power.toggle':(obj({}),obj({'enabled':BOOL}),['button_power'],'主电源按钮事件代理','device_command','agent',{}),
 'thermal.set_mode':(obj({'mode':MODE}),obj({'mode':MODE}),['power_dial'],'off/preheat/2.5工作档；实际功率映射未知','device_command','agent',dict(mode='preheat')),
 'rack.set_raised':(obj({'raised':BOOL}),obj({'raised':BOOL}),['rack','lever'],'手工升降目标，仅step移动','device_command','agent',dict(raised=False)),
 'valve.set':(obj({'position':POS,'open':BOOL}),obj({'open':BOOL}),['stopcock_'+str(i) for i in range(1,7)],'六位手动PTFE阀目标','device_command','agent',dict(position=1,open=True)),
 'vacuum.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['vacuum_port'],'外部抽吸开关，不声称内置真空泵','device_command','agent',dict(enabled=True)),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['heater','rack'],'预热600s后，全部沸腾再抽吸/计时的宿主程序','device_command','agent',{}),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['power_dial'],'停止加热，保留部分水解及热量','device_command','agent',{}),
 'hydrolysis.measure':(obj({}),MEASURE,['power_dial'],'宿主操作状态；无虚构脂肪或温度传感读数','query','agent',{}),
 'product.read':(obj({}),PRODUCT,['rack'],'评价器脂肪/固体/酸/液量/能量账本','query','evaluator',{}),
 'filtration.start':(obj({}),obj({'stage':TEXT}),['rack','vacuum_port'],'断热升架后外部真空过滤','device_command','agent',{}),
 'rinse.add':(obj({'position':POS,'volume_m3':number('m^3',1e-6,150e-6)}),obj({'transferred_m3':number('m^3',0)}),['water_port'],'有限温水手工加液事务；非机器人动作','device_command','evaluator',dict(position=1,volume_m3=100e-6)),
 'product.collect':(obj({'position':POS,'fraction':number('1',0,1),'recipient_id':TEXT}),obj({'transferred_wet_volume_m3':number('m^3',0),'recipient_id':TEXT}),['sample_tube_'+str(i) for i in range(1,7)],'洗净后湿滤样有限转移，仍需外部干燥与萃取','device_command','evaluator',dict(position=1,fraction=.5,recipient_id='external_drying')),
 'reference.lookup':(obj({'substance_id':TEXT}),obj({'fat_mass_fraction':number('kg/kg',0),'quantity':TEXT,'source':TEXT,'evidence':TEXT,'template_source':TEXT}),['rack'],'真实食品脂肪参考，不是本机读数','query','agent',dict(substance_id='buchi_milk_powder')),
}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,zh,o='derived',v='agent',c='body',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=zh
for n in ['plant','method','water']:slot(n+'.configured',BOOL,'1',n+'宿主配置','setpoint')
slot('power.enabled',BOOL,'1','主电源','setpoint',c='button_power',w=('power.set','enabled'));slot('thermal.mode',MODE,'1','功率档位','setpoint',c='power_dial',w=('thermal.set_mode','mode'))
slot('dial.angle_rad',number('rad',0,2.4),'rad','功率旋钮照片估计角度','scene',c='power_dial');slot('rack.raised_target',BOOL,'1','升架目标','setpoint',c='rack',w=('rack.set_raised','raised'));slot('rack.lift_m',number('m',0,.05),'m','照片估计50mm升程','scene',c='rack');slot('lever.angle_rad',number('rad',0,1.0),'rad','杠杆旋转，与升架反向','scene',c='lever')
slot('cycle.phase',PHASE,'1','宿主异步水解状态');slot('cycle.stage',TEXT,'1','宿主水解/过滤阶段');slot('cycle.elapsed_s',number('s',0),'s','全部沸腾后宿主累计水解秒');slot('thermal.preheat_s',number('s',0),'s','宿主预热计时非原厂显示');slot('vacuum.enabled',BOOL,'1','外部真空设定','setpoint',c='vacuum_port',w=('vacuum.set','enabled'));slot('ventilation.enabled',BOOL,'1','宿主通风确认','setpoint',v='evaluator',c='fume_port')
for i in range(1,7):slot('valve_'+str(i)+'.angle_rad',number('rad',0,math.pi/2),'rad','第'+str(i)+'位阀杆角','scene',c='stopcock_'+str(i))
slot('truth.heater_temperature_k',number('K',0),'K','热板温度真值，非液体/仪器读数','process_truth','evaluator','heater');slot('truth.sample_temperatures_k',array(number('K',0)),'K','按位置排列样品节点温度真值','process_truth','evaluator','rack')
for k,s,u in [('fat_ledger',PRODUCT,'1'),('water_remaining_m3',number('m^3',0),'m^3')]:slot('truth.'+k,s,u,'评价器有限过程账本','process_truth','evaluator')
class BuchiHydrolexhRuntime:
 def __init__(self,instance_id='h506_01'):
  self.instance_id=instance_id;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.plant=self.method=None;self.water=None;self.channels={};self.powered=False;self.ventilation=False;self.mode='off';self.dial_angle=0.;self.raised=True;self.rack_fraction=1.;self.valves=[False]*6;self.valve_angles=[0.]*6;self.vacuum=False;self.phase='idle';self.stage='idle';self.preheat=self.elapsed_s=self.time_s=0.;self.heater=298.15;self.electrical=self.storage=self.loss=self.boiling=self.rinse_energy=0.;self.initial_fat=self.initial_acid=self.initial_volume=self.initial_solid=0.;self.waste_fat=self.waste_acid=self.waste_volume=self.collected_fat=self.collected_acid=self.collected_volume=self.collected_solid=0.;self.revision=0;self.operation_id='';self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility):
  if visibility not in ['agent','debug','evaluator'] or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return BuchiHydrolexhSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _product(self):
  s=lambda k:sum(c[k] for c in self.channels.values())
  return dict(initial_fat_kg=self.initial_fat,bound_in_vessels_kg=s('bound'),free_in_vessels_kg=s('free'),retained_bound_kg=s('ret_bound'),retained_extractable_kg=s('ret_free'),waste_fat_kg=self.waste_fat,collected_fat_kg=self.collected_fat,initial_acid_mol=self.initial_acid,remaining_acid_mol=s('acid')+s('tube_acid'),waste_acid_mol=self.waste_acid,collected_acid_mol=self.collected_acid,initial_volume_m3=self.initial_volume,remaining_volume_m3=s('volume')+s('tube_volume')+(self.water or 0.),waste_volume_m3=self.waste_volume,collected_volume_m3=self.collected_volume,initial_dry_solid_kg=self.initial_solid,remaining_dry_solid_kg=s('solid')+s('ret_solid'),collected_dry_solid_kg=self.collected_solid,electrical_J=self.electrical,storage_J=self.storage,loss_J=self.loss,boiling_J=self.boiling,rinse_sensible_J=self.rinse_energy,evidence='synthetic_finite_wet_hydrolysate_model_conditioned_on_real_fat_references;NOT_fat_measurement',template_source=TEMPLATE_REL)
 def _values(self):
  v={'plant.configured':bool(self.plant),'method.configured':bool(self.method),'water.configured':self.water is not None,'power.enabled':self.powered,'thermal.mode':self.mode,'dial.angle_rad':self.dial_angle,'rack.raised_target':self.raised,'rack.lift_m':.05*self.rack_fraction,'lever.angle_rad':1.-self.rack_fraction,'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.elapsed_s':self.elapsed_s,'thermal.preheat_s':self.preheat,'vacuum.enabled':self.vacuum,'ventilation.enabled':self.ventilation,'truth.heater_temperature_k':self.heater,'truth.sample_temperatures_k':[self.channels[i]['temperature'] if i in self.channels else (self.plant['ambient_K'] if self.plant else 298.15) for i in range(1,7)],'truth.fat_ledger':self._product(),'truth.water_remaining_m3':self.water or 0.}
  v.update({'valve_'+str(i+1)+'.angle_rad':v for i,v in enumerate(self.valve_angles)});return v
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ['debug','evaluator']})
 def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k!='templates'})
 def _stop(self,phase,stage=None):self.phase=phase;self.stage=stage or phase;self.mode='off'
 def _filter_ready(self):return self.phase in ['succeeded','cancelled','failed'] and self.mode=='off' and self.rack_fraction>=1-1e-9 and self.vacuum and self.ventilation
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if self.phase=='running' and capability in ['plant.configure','method.configure','sample.configure','water.configure','filtration.start','rinse.add','product.collect']:raise ActionRejected('BUSY')
  if capability=='plant.configure':
   if self.plant or not a['accept_uncalibrated_model']:raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);self.heater=a['ambient_K'];out=dict(configured=True)
  elif capability=='method.configure':
   if self.phase!='idle' or not a['accept_host_manual_sequence_automation']:raise ActionRejected('PRECONDITION_FAILED')
   self.method=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='sample.configure':
   i=a['position']
   if not self.plant or self.phase!='idle' or i in self.channels or self.rack_fraction<1-1e-9 or not a['accept_reference_not_hydrolysis_calibration']:raise ActionRejected('PRECONDITION_FAILED')
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   fat=a['sample_mass_kg']*self.templates[a['substance_id']]['value'];vol=a['sample_liquid_m3']+a['acid_volume_m3'];acid=a['acid_volume_m3']*a['acid_mol_m3'];solid=a['sample_mass_kg']-fat+.002
   self.channels[i]=dict(config=copy.deepcopy(a),temperature=self.plant['ambient_K'],bound=fat*a['bound_fat_fraction'],free=fat*(1-a['bound_fat_fraction']),ret_bound=0.,ret_free=0.,solid=solid,ret_solid=.022,volume=vol,tube_volume=0.,acid=acid,tube_acid=0.,rinse_cycles=0,rinse_m3=0.)
   self.initial_fat+=fat;self.initial_acid+=acid;self.initial_volume+=vol;self.initial_solid+=solid+.022;out=dict(configured=True)
  elif capability=='water.configure':
   if self.water is not None:raise ActionRejected('PRECONDITION_FAILED')
   self.water=a['volume_m3'];self.initial_volume+=self.water;out=dict(configured=True)
  elif capability=='ventilation.set':
   self.ventilation=a['enabled']
   if not self.ventilation:
    if self.phase=='running':self._stop('failed','ventilation_lost')
    self.mode='off';self.vacuum=False
   out=a
  elif capability in ['power.set','power.toggle']:
   enabled=a['enabled'] if capability=='power.set' else not self.powered;self.powered=enabled
   if not enabled:
    if self.phase=='running':self._stop('cancelled','power_interrupted')
    self.mode='off'
   out=dict(enabled=enabled)
  elif capability=='thermal.set_mode':
   if a['mode']!='off' and (not self.plant or not self.powered or not self.ventilation or self.phase not in ['idle','running']):raise ActionRejected('PRECONDITION_FAILED')
   self.mode=a['mode'];out=a
  elif capability=='rack.set_raised':
   if self.phase=='running' or self.mode!='off' and a['raised']:raise ActionRejected('PRECONDITION_FAILED')
   self.raised=a['raised'];out=a
  elif capability=='valve.set':
   self.valves[a['position']-1]=a['open']
   if self.phase=='running' and (a['position'] in self.channels and not a['open'] or a['position'] not in self.channels and a['open']):self._stop('failed','stopcock_configuration_lost')
   out=dict(open=a['open'])
  elif capability=='vacuum.set':
   if a['enabled'] and (not self.ventilation or self.phase=='running' and self.stage!='hydrolyzing'):raise ActionRejected('PRECONDITION_FAILED')
   self.vacuum=a['enabled']
   if not self.vacuum and self.phase=='running':self._stop('failed','external_vacuum_lost')
   out=a
  elif capability=='acquisition.start':
   if not self.plant or not self.method or not self.channels or not self.powered or not self.ventilation or self.phase!='idle' or self.preheat<600-1e-9 or self.rack_fraction>1e-9 or any(not self.valves[i-1] or self.valve_angles[i-1]<math.pi/2-1e-9 for i in self.channels) or any(self.valves[i-1] for i in range(1,7) if i not in self.channels):raise ActionRejected('PRECONDITION_FAILED')
   self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);self.phase='running';self.stage='warming';self.mode='preheat';out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('PRECONDITION_FAILED')
   self._stop('cancelled');out=dict(phase=self.phase)
  elif capability=='hydrolysis.measure':return dict(phase=self.phase,stage=self.stage,elapsed_s=self.elapsed_s,power_mode=self.mode,positions_loaded=len(self.channels),evidence='host_operation_metadata_not_instrument_sensor;wet_extractable_product_is_evaluator_only',template_source=TEMPLATE_REL)
  elif capability=='product.read':return self._product()
  elif capability=='filtration.start':
   if not self._filter_ready() or any(self.valve_angles[i-1]<math.pi/2-1e-9 for i in self.channels):raise ActionRejected('PRECONDITION_FAILED')
   self.stage='filtering';out=dict(stage=self.stage)
  elif capability=='rinse.add':
   i=a['position']
   if i not in self.channels or self.stage!='filtered' or not self._filter_ready() or not self.method:raise ActionRejected('PRECONDITION_FAILED')
   c=self.channels[i];v=a['volume_m3']
   if self.water is None or self.water<v:raise ActionRejected('CAPACITY_LIMIT')
   if c['volume']>1e-12:raise ActionRejected('PRECONDITION_FAILED')
   old=c['temperature'];c['temperature']=self.method['rinse_water_K'];q=self.plant['sample_capacity_J_K']*(c['temperature']-old);self.rinse_energy+=q;self.storage+=q;self.water-=v;c['volume']+=v;c['rinse_cycles']+=1;c['rinse_m3']+=v;out=dict(transferred_m3=v)
  elif capability=='product.collect':
   i=a['position']
   if i not in self.channels or self.stage!='filtered' or not self._filter_ready():raise ActionRejected('PRECONDITION_FAILED')
   c=self.channels[i]
   if c['temperature']>323.15 or c['rinse_cycles']<self.method['minimum_rinse_cycles'] or c['rinse_m3']<self.method['minimum_rinse_m3']-1e-12 or c['volume']>1e-12 or c['tube_acid']>self.plant['neutral_acid_mol_m3']*max(c['tube_volume'],1e-15):raise ActionRejected('PRECONDITION_FAILED')
   f=a['fraction'];v=c['tube_volume']*f;self.collected_fat+=(c['ret_bound']+c['ret_free'])*f;self.collected_acid+=c['tube_acid']*f;self.collected_solid+=c['ret_solid']*f;self.collected_volume+=v
   for k in ['ret_bound','ret_free','tube_acid','ret_solid','tube_volume']:c[k]*=1-f
   out=dict(transferred_wet_volume_m3=v,recipient_id=a['recipient_id'])
  elif capability=='reference.lookup':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   r=self.templates[a['substance_id']];return dict(fat_mass_fraction=r['value'],quantity=r['quantity'],source=r['source'],evidence=r['evidence'],template_source=TEMPLATE_REL)
  self.revision+=1;return copy.deepcopy(out)
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or not 0<=dt_s<=7200:raise ActionRejected('INVALID_ARGUMENT')
  left=float(dt_s)
  while left>1e-10:
   h=min(.05,left);p=self.plant
   if self.phase=='running' and self.stage=='hydrolyzing':h=min(h,self.method['hydrolysis_s']-self.elapsed_s)
   if p:
    P=1200.*({'off':0.,'preheat':1.,'work':p['work_fraction']}[self.mode]) if self.powered and self.ventilation else 0.;amb=p['ambient_K'];loss=p['plate_UA_W_K']*(self.heater-amb)*h;transfers={i:p['contact_UA_W_K']*(self.heater-c['temperature'])*h*(1-self.rack_fraction) for i,c in self.channels.items()};dh=P*h-loss-sum(transfers.values());excess=max(0.,p['heater_capacity_J_K']*(self.heater-768.15)+dh);dh-=excess;self.heater+=dh/p['heater_capacity_J_K'];self.electrical+=P*h;self.storage+=dh;self.loss+=loss+excess
    for i,c in self.channels.items():
     sloss=p['sample_UA_W_K']*(c['temperature']-amb)*h;ds=transfers[i]-sloss;boil=max(0.,p['sample_capacity_J_K']*(c['temperature']-p['sample_boiling_K'])+ds);ds-=boil;c['temperature']+=ds/p['sample_capacity_J_K'];self.storage+=ds;self.loss+=sloss;self.boiling+=boil
     if self.phase=='running' and c['temperature']>=p['sample_boiling_K']-1e-6 and c['volume']>0:
      k=c['config']['release_rate_s_inverse']*min(1.,c['acid']/c['volume']/4000.);df=c['bound']*(-math.expm1(-k*h));c['bound']-=df;c['free']+=df
    if self.phase=='idle' and self.mode=='preheat' and P>0:self.preheat+=h
    if self.phase=='running':
     if self.stage=='warming' and all(c['temperature']>=p['sample_boiling_K']-1e-6 for c in self.channels.values()):self.stage='hydrolyzing';self.mode='work';self.vacuum=True
     elif self.stage=='hydrolyzing' and all(c['temperature']>=p['sample_boiling_K']-1e-6 for c in self.channels.values()):
      self.elapsed_s+=h
      if self.elapsed_s>=self.method['hydrolysis_s']-1e-9:self._stop('succeeded','hydrolysis_complete')
    if self.stage=='filtering' and self._filter_ready():
     for i,c in self.channels.items():
      if self.valve_angles[i-1]<math.pi/2-1e-9 or c['volume']<=1e-15:continue
      dv=min(c['volume'],p['filter_rate_m3_s']*h);f=dv/c['volume'];ia=c['acid']*f;c['acid']-=ia;c['volume']-=dv
      for key,ret in [('bound','ret_bound'),('free','ret_free')]:
       fat=c[key]*f;c[key]-=fat;c[ret]+=fat*c['config']['fat_capture_fraction'];self.waste_fat+=fat*(1-c['config']['fat_capture_fraction'])
      ds=c['solid']*f;c['solid']-=ds;c['ret_solid']+=ds;fill=min(dv,max(0.,p['tube_holdup_m3']-c['tube_volume']));cin=ia/dv;old=c['tube_acid'];c['tube_acid']+=cin*fill;c['tube_volume']+=fill;flow=dv-fill
      if flow>0:c['tube_acid']=cin*c['tube_volume']+(c['tube_acid']-cin*c['tube_volume'])*math.exp(-flow/c['tube_volume'])
      self.waste_acid+=old+ia-c['tube_acid'];self.waste_volume+=flow
     if all(c['volume']<=1e-12 for c in self.channels.values()):self.stage='filtered'
   speed=p['mechanical_speed_fraction_s'] if p else 2.
   for attr,target in [('rack_fraction',float(self.raised)),('dial_angle',{'off':0.,'work':1.7,'preheat':2.4}[self.mode])]:
    old=getattr(self,attr);setattr(self,attr,old+max(-speed*h,min(speed*h,target-old)))
   for i,old in enumerate(self.valve_angles):self.valve_angles[i]=old+max(-speed*h,min(speed*h,(math.pi/2 if self.valves[i] else 0.)-old))
   self.time_s+=h;left-=h;self.revision+=1

class BuchiHydrolexhSession:
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
        meta=export_buchi_hydrolexh_contract_metadata()
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('hydrolysis.measure'))

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])





def export_buchi_hydrolexh_contract_metadata():
 caps={};slots={}
 for k,(schema,unit,origin,visibility,components,write) in SLOTS.items():slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'BuchiHydrolexhSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
 immediate={'plant.configure':['plant.configured','truth.heater_temperature_k','truth.sample_temperatures_k'],'method.configure':['method.configured'],'sample.configure':['truth.fat_ledger','truth.sample_temperatures_k'],'water.configure':['water.configured','truth.water_remaining_m3','truth.fat_ledger'],'ventilation.set':['ventilation.enabled','thermal.mode','vacuum.enabled','cycle.phase','cycle.stage'],'power.set':['power.enabled','thermal.mode','cycle.phase','cycle.stage'],'power.toggle':['power.enabled','thermal.mode','cycle.phase','cycle.stage'],'thermal.set_mode':['thermal.mode'],'rack.set_raised':['rack.raised_target'],'valve.set':['cycle.phase','cycle.stage','thermal.mode'],'vacuum.set':['vacuum.enabled','cycle.phase','cycle.stage','thermal.mode'],'acquisition.start':['cycle.phase','cycle.stage','thermal.mode'],'acquisition.stop':['cycle.phase','cycle.stage','thermal.mode'],'filtration.start':['cycle.stage'],'rinse.add':['truth.water_remaining_m3','truth.fat_ledger','truth.sample_temperatures_k'],'product.collect':['truth.fat_ledger']}
 mechanics=['rack.lift_m','lever.angle_rad','dial.angle_rad']+['valve_'+str(i)+'.angle_rad' for i in range(1,7)]
 evolving=mechanics+['cycle.phase','cycle.stage','cycle.elapsed_s','thermal.mode','thermal.preheat_s','vacuum.enabled','truth.heater_temperature_k','truth.sample_temperatures_k','truth.fat_ledger']
 for k,(inp,out,components,meaning,kind,vis,example) in CAPS.items():
  async_=k=='acquisition.start';query=kind=='query';caps[k]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.buchi_hydrolexh.BuchiHydrolexhSession.invoke',invocation=f'session.invoke(instance_id="h506_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if query else [dict(description_zh='有限装样/水、600s预热、通风、升架/断热/阀门和洗涤守卫；宿主安全策略非硬件认证',state_ids=['cycle.phase','thermal.mode','rack.lift_m','ventilation.enabled'],enforced_by='runtime',check='BuchiHydrolexhRuntime.invoke guards')],state_effects=[] if query else [dict(state_id=i,timing='immediate',description_zh='配置/命令只改目标或有限事务；具体变化由逐API测试核验') for i in immediate.get(k,[])]+[dict(state_id=i,timing='on_step',description_zh='宿主step唯一推进热/水解/过滤/机械') for i in evolving],observes=[],model_effects=dict(mode='none',description_zh='实际求值验证后声明联动',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='全部沸腾后宿主处理时长结束，仍须过滤/洗涤/外部干燥萃取' if async_ else '事务/查询/目标写入完成',completion_state_ids=['cycle.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['finite_samples','finite_warm_water','host_clock','principal_request_cache'],partial_failure_zh='取消/失通风/断电保留部分水解和热量，不伪装完整成品'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT','UNKNOWN_REFERENCE','CAPACITY_LIMIT']],example_arguments=copy.deepcopy(example),verification=[],limitations=['本机无LCD/脂肪传感器，宿主结果不是原厂测量；动态过程synthetic'],source_ids=['manufacturer_primary','simulation_assumption'])
 mech=dict(component_ids=['heater','rack','power_dial','vacuum_port'],state_ids=list(slots),meaning_zh='六位酸热水解、有限过滤洗涤湿样、共用热板七节点能量',implementation='lumped',equations=['P_total<=1200W;C_plate*dT_plate/dt=P-UA_plate*(T_plate-Tambient)-sum(Q_to_sample)','C_sample_i*dT_i/dt=UA_contact*(T_plate-T_i)*lowered-UA_sample*(T_i-Tambient)-Q_boiling','E_electrical+E_rinse_sensible=E_storage+E_loss+E_boiling;heater<=768.15K','F_bound_next=F_bound*exp(-host_matrix_k*min(acid_concentration/4000,1)*boiling_time)','F_initial=F_bound+F_free+F_retained_bound+F_retained_free+F_waste+F_collected','A_initial=A_vessel+A_tube+A_waste+A_collected;washing is exact mixed-holdup exchange','V_initial=V_waterstock+V_vessels+V_tubes+V_waste+V_collected;ideal reflux,additive volumes','Dry_solid_initial=Dry_solid_vessel+Dry_solid_retained+Dry_solid_collected;no mineralization','Only step advances mechanics,heat,release and filtration;invocation/read never advances clock'],parameters={},source_ids=['manufacturer_primary','manufacturer_datasheet','dairy_hot','usda_foundation','simulation_assumption'],limitations=['54真实食品矩阵脂肪端点，不是50本机实测动态曲线；32厂家结果属于H506+E500完整方法。','热容/传热/工作档功率比例/酸沸点/基体速率/脂肪捕集/过滤流量未标定。','600s为手册等待时间；未宣称升温实测拟合。','采用手册全部沸腾后抽吸顺序，宿主自动化不是原厂内置程序。','理想回流忽略酸挥发和体积变化；多余沸腾热记作排放，无真实蒸发质量模型。','洗净判据是宿主酸浓度阈值，不是内置pH电极；手册需外部pH试纸确认。','湿样中仍有未水解脂肪/基体/砂/Celite，需外部干燥、萃取、称量。','65mL标称/64mL工作滤管与100mL酸水解容器分开。','不含玻璃13kg、含玻璃16.5kg为整机资料，部件真实参数未知。','未做机器人、硬件通信、安全认证、液体形变、软管力学或完整实验误差。'])
 spec=importlib.util.spec_from_file_location('_h506_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'hydrolysis':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.configure':(SAMPLE,example_sample())});mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'hydrolysis':mech})

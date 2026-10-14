"""UDK159 teaching process: finite nitrogen, reagents, colorimetric titration."""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.pccell_ed64002 import validate
ROOT=Path(__file__).resolve().parents[4] if 'outputs' in str(__file__) else Path(__file__).resolve().parents[2]
SLUG='velp_udk159';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-048';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/velp_udk159_nitrogen_library/templates.json';MN=.0140067
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
def nullable(s):return dict(anyOf=[s,dict(type='null')])
def array(s):return dict(type='array',items=s)
PLANT=obj({'ambient_K':number('K',283.15,303.15),'heat_capacity_J_K':number('J/K',100,5000),'thermal_efficiency':number('1',.1,1),'UA_W_K':number('W/K',0,10),'latent_J_kg':number('J/kg',2e6,2.5e6),'water_density_kg_m3':number('kg/m^3',950,1100),'transfer_rate_s_inverse':number('1/s',0,1),'capture_fraction':number('1',0,1),'dose_rate_m3_s':number('m^3/s',1e-8,1e-4),'titrant_rate_m3_s':number('m^3/s',1e-9,1e-5),'optical_scale_mol':number('mol',1e-8,1e-3),'condenser_approach_K':number('K',0,15),'door_speed_rad_s':number('rad/s',.1,5),'lever_speed_rad_s':number('rad/s',.1,5),'cooldown_s':number('s',0,600),'accept_uncalibrated_model':BOOL,'coefficient_evidence':TEXT})
PLANT_EXAMPLE=dict(ambient_K=298.15,heat_capacity_J_K=600.,thermal_efficiency=.75,UA_W_K=1.,latent_J_kg=2257000.,water_density_kg_m3=1000.,transfer_rate_s_inverse=.03,capture_fraction=.998,dose_rate_m3_s=10e-6,titrant_rate_m3_s=1e-6,optical_scale_mol=1e-5,condenser_approach_K=3.,door_speed_rad_s=math.pi,lever_speed_rad_s=math.pi,cooldown_s=60.,accept_uncalibrated_model=True,coefficient_evidence='explicit_host_teaching_assumptions_not_factory_calibration')
METHOD=obj({'steam_fraction':number('1',.1,1),'distillation_window_s':number('s',1,3600),'water_volume_m3':number('m^3',0,150e-6),'base_volume_m3':number('m^3',0,150e-6),'boric_volume_m3':number('m^3',1e-6,100e-6),'base_concentration_mol_m3':number('mol/m^3',1,20000),'boric_capacity_mol_m3':number('mol/m^3',1,2000),'titrant_equivalents_mol_m3':number('mol/m^3',1,2000),'blank_titrant_volume_m3':number('m^3',0,5e-6),'endpoint_tolerance_mol':number('mol',0,1e-5),'protein_factor':number('1',1,10)})
METHOD_EXAMPLE=dict(steam_fraction=1.,distillation_window_s=240.,water_volume_m3=50e-6,base_volume_m3=70e-6,boric_volume_m3=30e-6,base_concentration_mol_m3=8000.,boric_capacity_mol_m3=640.,titrant_equivalents_mol_m3=200.,blank_titrant_volume_m3=.1e-6,endpoint_tolerance_mol=1e-7,protein_factor=5.70)
SAMPLE=obj({'substance_id':TEXT,'sample_mass_kg':number('kg',0,1),'digest_volume_m3':number('m^3',1e-6,100e-6),'digest_acid_equivalents_mol':number('mol',0,2),'kjeldahl_available_fraction':number('1',0,1),'upstream_digestion_recovery':number('1',0,1),'predigestion_confirmed':BOOL,'accept_TN_to_TKN_assumption':BOOL})
def example_sample():return dict(substance_id='velp_durum_wheat',sample_mass_kg=.002,digest_volume_m3=10e-6,digest_acid_equivalents_mol=.1,kjeldahl_available_fraction=1.,upstream_digestion_recovery=1.,predigestion_confirmed=True,accept_TN_to_TKN_assumption=False)
STOCK_NAMES=['water','base','boric','titrant','steam_water'];STOCK=obj({k+'_m3':number('m^3',0,.1) for k in STOCK_NAMES});STOCK_EXAMPLE={k+'_m3':.001 for k in STOCK_NAMES}
COOLANT=obj({'temperature_K':number('K',278.15,313.15),'flow_m3_s':number('m^3/s',0,.001)})
COOLANT_EXAMPLE=dict(temperature_K=288.15,flow_m3_s=1e-3/60)
POINT=obj({'time_s':number('s',0),'titrant_volume_m3':number('m^3',0),'color_signal':number('1',-1,1)})
MEASURE=obj({'phase':PHASE,'stage':TEXT,'elapsed_s':number('s',0),'titrant_volume_m3':number('m^3',0),'color_signal':nullable(number('1',-1,1)),'nitrogen_kg':nullable(number('kg',0)),'protein_mass_fraction':nullable(number('kg/kg',0)),'nominal_range':BOOL,'evidence':TEXT,'template_source':TEXT})
PRODUCT=obj({'initial_N_kg':number('kg',0),'sample_N_kg':number('kg',0),'excluded_N_kg':number('kg',0),'receiver_N_kg':number('kg',0),'escaped_N_kg':number('kg',0),'waste_N_kg':number('kg',0),'collected_N_kg':number('kg',0),'volume_initial_m3':number('m^3',0),'volume_remaining_m3':number('m^3',0),'collected_volume_m3':number('m^3',0),'absorbed_J':number('J',0),'storage_J':number('J'),'ambient_loss_J':number('J'),'steam_latent_J':number('J',0),'evidence':TEXT,'template_source':TEXT})
CAPS={
 'plant.configure':(PLANT,obj({'configured':BOOL}),['boiler','condenser'],'显式宿主热、传质和比色参数','configuration','evaluator',PLANT_EXAMPLE),
 'method.configure':(METHOD,obj({'configured':BOOL}),['display_digits'],'蒸汽、宿主时限、试剂量、酸当量及终点','configuration','agent',METHOD_EXAMPLE),
 'sample.configure':(SAMPLE,obj({'configured':BOOL}),['sample_tube'],'外部预消解样品、来源及凯氏可用比例','configuration','evaluator',example_sample()),
 'stocks.configure':(STOCK,obj({'configured':BOOL}),['water_port','base_port','boric_port','titrant_port'],'一次有限试剂装载','configuration','evaluator',STOCK_EXAMPLE),
 'coolant.set':(COOLANT,obj({'configured':BOOL}),['coolant_port'],'冷却水温度/流量，运行掉流失败','simulation','evaluator',COOLANT_EXAMPLE),
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_selector'],'供电旋钮；运行中关机须先停止','device_command','agent',dict(enabled=True)),
 'power.interrupt':(obj({}),obj({'phase':PHASE}),['power_inlet'],'意外断电保留有限物料和热量','simulation','evaluator',{}),
 'door.set_open':(obj({'open':BOOL}),obj({'open':BOOL}),['door'],'防护门目标，step推进铰链','device_command','agent',dict(open=False)),
 'tube.set_clamped':(obj({'clamped':BOOL}),obj({'clamped':BOOL}),['lever','tube_holder'],'手工杠杆与托架联动目标','device_command','agent',dict(clamped=True)),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['button_touch_start','boiler','titration_vessel'],'加试剂、预热、在线比色蒸馏滴定','device_command','agent',{}),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['button_touch_stop'],'取消保留已转移/滴定部分','device_command','agent',{}),
 'analysis.measure':(obj({}),MEASURE,['display_digits'],'比色信号与终点结果；不是过程真值','query','agent',{}),
 'curve.read':(obj({}),obj({'points':array(POINT),'evidence':TEXT,'template_source':TEXT}),['display_digits'],'实际step生成的合成在线滴定轨迹','query','agent',{}),
 'product.read':(obj({}),PRODUCT,['sample_tube','titration_vessel'],'评价器有限氮、体积与热账本','query','evaluator',{}),
 'product.collect':(obj({'fraction':number('1',0,1),'recipient_id':TEXT}),obj({'transferred_volume_m3':number('m^3',0),'recipient_id':TEXT}),['distillate_port'],'接收杯馏出液有限转移，不是自动机器人取样','device_command','evaluator',dict(fraction=.5,recipient_id='receiver')),
 'residue.drain':(obj({}),obj({'waste_volume_m3':number('m^3',0)}),['waste_port'],'停机冷却后样管/滴定杯残液排放','device_command','agent',{}),
 'reference.lookup':(obj({'substance_id':TEXT}),obj({'nitrogen_mass_fraction':number('kg/kg',0),'quantity':TEXT,'protein_factor':nullable(number('1',0)),'source':TEXT,'evidence':TEXT,'template_source':TEXT}),['display_digits'],'真实端点参考；总氮/凯氏氮分别标注','query','agent',dict(substance_id='velp_durum_wheat')),
 'fault.acknowledge':(obj({}),obj({'phase':PHASE}),['display_digits'],'冷却且原因消除后确认；不重置物料','device_command','agent',{}),
}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,zh,o='derived',v='agent',c='body',w=None):SLOTS[k.lower()]=(s,u,o,v,[c],w);SLOT_ZH[k.lower()]=zh
for n in ['plant','method','sample','stocks']:slot(n+'.configured',BOOL,'1','宿主'+n+'配置完成','setpoint')
slot('power.enabled',BOOL,'1','供电设定','setpoint',c='power_selector',w=('power.set','enabled'))
slot('door.open_target',BOOL,'1','安全门打开目标','setpoint',c='door',w=('door.set_open','open'));slot('door.angle_rad',number('rad',0,1.8),'rad','防护门角','scene',c='door')
slot('tube.clamped_target',BOOL,'1','装管夹持目标','setpoint',c='lever',w=('tube.set_clamped','clamped'));slot('lever.angle_rad',number('rad',0,1.2),'rad','杠杆照片估计角','scene',c='lever');slot('holder.lift_m',number('m',0,.012),'m','托架照片估计升程','scene',c='tube_holder')
slot('cycle.phase',PHASE,'1','异步分析状态');slot('cycle.stage',TEXT,'1','加液/预热/蒸馏滴定/结束');slot('cycle.elapsed_s',number('s',0),'s','蒸馏滴定累计秒');slot('cycle.cooldown_s',number('s',0,600),'s','宿主冷却门守卫剩余秒')
slot('method.steam_fraction',number('1',.1,1),'1','蒸汽比例设定','setpoint',c='display_digits');slot('panel.steam_percent',number('1',10,100),'1','蒸汽百分比',c='display_digits');slot('panel.nitrogen_mg',nullable(number('1',0)),'1','mgN结果，无结果时null',c='display_digits');slot('panel.titrant_ml',number('1',0),'1','滴定体积mL',c='display_digits')
slot('analysis.color_signal',nullable(number('1',-1,1)),'1','理想比色氨酸当量差代理',c='titration_vessel');slot('analysis.nitrogen_kg',nullable(number('kg',0)),'kg','空白校正终点氮读数',c='display_digits');slot('coolant.valid',BOOL,'1','冷却水已达保守流量和温度条件',c='coolant_port')
for k,s,u,zh in [('initial_N_kg',number('kg',0),'kg','参考初始氮'),('sample_N_kg',number('kg',0),'kg','样管可蒸馏氮'),('excluded_N_kg',number('kg',0),'kg','非凯氏可用/未消解氮'),('receiver_N_kg',number('kg',0),'kg','接收杯总氮'),('escaped_N_kg',number('kg',0),'kg','未捕集氮'),('waste_N_kg',number('kg',0),'kg','废液氮'),('collected_N_kg',number('kg',0),'kg','转出氮'),('boiler_temperature_k',number('K',0),'K','蒸汽发生器温度真值非原厂面板'),('absorbed_j',number('J',0),'J','累计热吸收'),('storage_j',number('J'),'J','锅炉储热'),('loss_j',number('J'),'J','环境净放热'),('latent_j',number('J',0),'J','蒸汽潜热'),('receiver_volume_m3',number('m^3',0),'m^3','有限接收液体积')]:slot('truth.'+k,s,u,zh,'process_truth','evaluator')
class VelpUdk159Runtime:
 def __init__(self,instance_id='udk159_01'):
  self.instance_id=instance_id;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.plant=self.method=self.sample=self.stocks=self.coolant=None;self.powered=False;self.door_open=True;self.door_angle=1.8;self.clamped=False;self.lever_angle=0.;self.phase='idle';self.stage='idle';self.fault='';self.temperature=298.15;self.time_s=self.elapsed_s=self.cooldown=0.;self.initial_N=self.sample_N=self.excluded_N=self.receiver_N=self.escaped_N=self.waste_N=self.collected_N=0.;self.sample_volume=self.receiver_volume=self.waste_volume=self.collected_volume=self.initial_volume=0.;self.absorbed=self.storage=self.loss=self.latent=self.titrant_volume=0.;self.result=None;self.receiver_acid_eq=self.receiver_blank_eq=0.;self.curve=[];self.dosed={k:0. for k in ['water','base','boric']};self.revision=0;self.operation_id='';self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility):
  if visibility not in ['agent','debug','evaluator'] or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return VelpUdk159Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def coolant_valid(self):
  return bool(self.coolant and self.plant and self.coolant['temperature_K']<=303.15 and self.coolant['temperature_K']+self.plant['condenser_approach_K']<308.15 and self.coolant['flow_m3_s']>=(.5e-3/60 if self.coolant['temperature_K']<=288.15 else 1e-3/60))
 def color(self):
  if not self.powered or self.method is None or self.receiver_volume==0:return None
  excess=self.receiver_N/MN+self.receiver_blank_eq-self.receiver_acid_eq;return math.tanh(excess/self.plant['optical_scale_mol'])
 def _values(self):
  vals={'plant.configured':bool(self.plant),'method.configured':bool(self.method),'sample.configured':bool(self.sample),'stocks.configured':self.stocks is not None,'power.enabled':self.powered,'door.open_target':self.door_open,'door.angle_rad':self.door_angle,'tube.clamped_target':self.clamped,'lever.angle_rad':self.lever_angle,'holder.lift_m':.01*self.lever_angle,'cycle.phase':self.phase,'cycle.stage':self.fault or self.stage,'cycle.elapsed_s':self.elapsed_s,'cycle.cooldown_s':self.cooldown,'method.steam_fraction':self.method['steam_fraction'] if self.method else 1.,'panel.steam_percent':100*(self.method['steam_fraction'] if self.method else 1.),'panel.nitrogen_mg':None if self.result is None else self.result*1e6,'panel.titrant_ml':self.titrant_volume*1e6,'analysis.color_signal':self.color(),'analysis.nitrogen_kg':self.result,'coolant.valid':self.coolant_valid()}
  for k in ['initial_N','sample_N','excluded_N','receiver_N','escaped_N','waste_N','collected_N']:vals['truth.'+k+'_kg']=getattr(self,k)
  vals.update({'truth.boiler_temperature_k':self.temperature,'truth.absorbed_j':self.absorbed,'truth.storage_j':self.storage,'truth.loss_j':self.loss,'truth.latent_j':self.latent,'truth.receiver_volume_m3':self.receiver_volume});return {k.lower():v for k,v in vals.items()}
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ['debug','evaluator']})
 def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k!='templates'})
 def _end(self,phase,fault=''):
  self.phase=phase;self.stage=phase;self.fault=fault;self.cooldown=self.plant['cooldown_s'];self.result=max(0.,(self.titrant_volume-self.method['blank_titrant_volume_m3'])*self.method['titrant_equivalents_mol_m3']*MN) if phase=='succeeded' else None
 def _start(self):
  if not all([self.plant,self.method,self.sample,self.stocks,self.powered,self.coolant_valid()]) or self.phase!='idle' or self.cooldown>1e-9 or self.door_open or self.door_angle>1e-9 or not self.clamped or self.lever_angle<1.2-1e-9:raise ActionRejected('PRECONDITION_FAILED')
  m=self.method
  if self.sample_volume+m['water_volume_m3']+m['base_volume_m3']>250e-6 or any(self.stocks[k]<m[k+'_volume_m3'] for k in ['water','base','boric']):raise ActionRejected('CAPACITY_LIMIT')
  if m['base_volume_m3']*m['base_concentration_mol_m3']<self.sample['digest_acid_equivalents_mol']+self.sample_N/MN or m['boric_volume_m3']*m['boric_capacity_mol_m3']<self.sample_N/MN:raise ActionRejected('PRECONDITION_FAILED')
  required=self.sample_N/MN/m['titrant_equivalents_mol_m3']+m['blank_titrant_volume_m3']
  if self.stocks['titrant']<required or self.stocks['steam_water']<=0:raise ActionRejected('CAPACITY_LIMIT')
  self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);self.phase='running';self.stage='dosing';self.elapsed_s=0.;self.result=None
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if self.phase=='running' and capability in ['plant.configure','method.configure','sample.configure','stocks.configure','product.collect','residue.drain']:raise ActionRejected('BUSY')
  if capability=='plant.configure':
   if self.plant or not a['accept_uncalibrated_model']:raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);self.temperature=a['ambient_K'];out=dict(configured=True)
  elif capability=='method.configure':
   if self.phase!='idle':raise ActionRejected('PRECONDITION_FAILED')
   self.method=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='sample.configure':
   if not self.plant or self.sample or self.door_angle<1.8-1e-9 or self.clamped or not a['predigestion_confirmed']:raise ActionRejected('PRECONDITION_FAILED')
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   r=self.templates[a['substance_id']]
   if r['quantity']=='total_nitrogen_mass_fraction' and not a['accept_TN_to_TKN_assumption']:raise ActionRejected('REFERENCE_RANGE')
   n=a['sample_mass_kg']*r['value'];available=n*a['kjeldahl_available_fraction']*a['upstream_digestion_recovery']
   if n and not 4e-8<=available<=2.2e-4:raise ActionRejected('REFERENCE_RANGE')
   self.sample=copy.deepcopy(a);self.initial_N=n;self.sample_N=available;self.excluded_N=n-available;self.sample_volume=a['digest_volume_m3'];self.initial_volume+=self.sample_volume;out=dict(configured=True)
  elif capability=='stocks.configure':
   if self.stocks is not None:raise ActionRejected('PRECONDITION_FAILED')
   self.stocks={k:a[k+'_m3'] for k in STOCK_NAMES};self.initial_volume+=sum(self.stocks.values());out=dict(configured=True)
  elif capability=='coolant.set':
   self.coolant=copy.deepcopy(a)
   if self.phase=='running' and not self.coolant_valid():self._end('failed','coolant_flow_or_temperature')
   out=dict(configured=True)
  elif capability=='power.set':
   if not a['enabled'] and self.phase=='running':raise ActionRejected('PRECONDITION_FAILED')
   self.powered=a['enabled'];out=a
  elif capability=='power.interrupt':
   self.powered=False
   if self.phase=='running':self._end('cancelled','power_interrupted')
   out=dict(phase=self.phase)
  elif capability=='door.set_open':
   if a['open'] and (self.phase=='running' or self.cooldown>1e-9):raise ActionRejected('PRECONDITION_FAILED')
   self.door_open=a['open'];out=a
  elif capability=='tube.set_clamped':
   if self.phase=='running' or self.cooldown>1e-9 or self.door_angle<1.8-1e-9:raise ActionRejected('PRECONDITION_FAILED')
   self.clamped=a['clamped'];out=a
  elif capability=='acquisition.start':self._start();out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('PRECONDITION_FAILED')
   self._end('cancelled');out=dict(phase=self.phase)
  elif capability=='analysis.measure':return dict(phase=self.phase,stage=self.fault or self.stage,elapsed_s=self.elapsed_s,titrant_volume_m3=self.titrant_volume,color_signal=self.color(),nitrogen_kg=self.result,protein_mass_fraction=self.result/self.sample['sample_mass_kg']*self.method['protein_factor'] if self.result is not None and self.sample['sample_mass_kg'] else None,nominal_range=bool(self.result is not None and 4e-8<=self.result<=2.2e-4),evidence='synthetic_colorimetric_model_conditioned_on_real_reference;NOT_factory_recovery_guarantee',template_source=TEMPLATE_REL)
  elif capability=='curve.read':return dict(points=copy.deepcopy(self.curve),evidence='synthetic_time_resolved_model_NOT_measured_UDK159_trace',template_source=TEMPLATE_REL)
  elif capability=='product.read':return dict(initial_N_kg=self.initial_N,sample_N_kg=self.sample_N,excluded_N_kg=self.excluded_N,receiver_N_kg=self.receiver_N,escaped_N_kg=self.escaped_N,waste_N_kg=self.waste_N,collected_N_kg=self.collected_N,volume_initial_m3=self.initial_volume,volume_remaining_m3=sum(self.stocks.values() if self.stocks else [])+self.sample_volume+self.receiver_volume+self.waste_volume,collected_volume_m3=self.collected_volume,absorbed_J=self.absorbed,storage_J=self.storage,ambient_loss_J=self.loss,steam_latent_J=self.latent,evidence='finite_teaching_ledger_no_volume_contraction;explicit_TN_vs_TKN',template_source=TEMPLATE_REL)
  elif capability=='product.collect':
   if self.phase not in ['succeeded','cancelled','failed'] or self.cooldown>1e-9:raise ActionRejected('PRECONDITION_FAILED')
   f=a['fraction'];v=self.receiver_volume*f;self.collected_N+=self.receiver_N*f;self.receiver_N*=1-f;self.receiver_acid_eq*=1-f;self.receiver_blank_eq*=1-f;self.receiver_volume-=v;self.collected_volume+=v;out=dict(transferred_volume_m3=v,recipient_id=a['recipient_id'])
  elif capability=='residue.drain':
   if self.phase not in ['succeeded','cancelled','failed'] or self.cooldown>1e-9:raise ActionRejected('PRECONDITION_FAILED')
   self.waste_N+=self.receiver_N+self.sample_N+self.excluded_N;self.receiver_N=self.sample_N=self.excluded_N=self.receiver_acid_eq=self.receiver_blank_eq=0.;self.waste_volume+=self.receiver_volume+self.sample_volume;self.receiver_volume=self.sample_volume=0.;out=dict(waste_volume_m3=self.waste_volume)
  elif capability=='reference.lookup':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   r=self.templates[a['substance_id']];return dict(nitrogen_mass_fraction=r['value'],quantity=r['quantity'],protein_factor=r['protein_factor'],source=r['source'],evidence=r['evidence'],template_source=TEMPLATE_REL)
  elif capability=='fault.acknowledge':
   if self.phase!='failed' or self.cooldown>1e-9 or not self.coolant_valid():raise ActionRejected('PRECONDITION_FAILED')
   self.phase='cancelled';self.fault='acknowledged_partial_sample_requires_new_instance';out=dict(phase=self.phase)
  self.revision+=1;return copy.deepcopy(out)
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or not 0<=dt_s<=3600:raise ActionRejected('INVALID_ARGUMENT')
  left=float(dt_s)
  while left>1e-10:
   h=min(.1,left);p=self.plant;active=self.phase=='running';cooling=self.cooldown>1e-9
   if active and self.stage=='distill_titrate':h=min(h,self.method['distillation_window_s']-self.elapsed_s)
   if p:
    q=(2200.*p['thermal_efficiency']*(self.method['steam_fraction'] if self.stage=='distill_titrate' else 1.)) if active and self.stage in ['preheat','distill_titrate'] else 0.
    ua=p['UA_W_K'];C=p['heat_capacity_J_K'];amb=p['ambient_K'];after=amb+(self.temperature-amb-q/ua)*math.exp(-ua*h/C)+q/ua if ua else self.temperature+q*h/C
    latent=max(0.,C*(after-373.15));after=min(after,373.15);stored=C*(after-self.temperature);loss=q*h-stored-latent;self.temperature=after;self.absorbed+=q*h;self.storage+=stored;self.loss+=loss
    # Latent heat only becomes delivered steam during distillation; surplus preheat is vented heat.
    if not active or self.stage!='distill_titrate':self.loss+=latent;latent=0.
    if active and self.stage=='dosing':
     for k in ['water','base','boric']:
      dv=min(max(0.,self.method[k+'_volume_m3']-self.dosed[k]),p['dose_rate_m3_s']*h,self.stocks[k]);self.stocks[k]-=dv;self.dosed[k]+=dv
      if k=='boric':self.receiver_volume+=dv;self.receiver_blank_eq+=dv/self.method['boric_volume_m3']*self.method['blank_titrant_volume_m3']*self.method['titrant_equivalents_mol_m3']
      else:self.sample_volume+=dv
     if all(self.dosed[k]>=self.method[k+'_volume_m3']-1e-15 for k in self.dosed):self.stage='preheat'
    elif active and self.stage=='preheat' and self.temperature>=373.15-1e-9:self.stage='distill_titrate'
    elif active and self.stage=='distill_titrate':
     required=latent/(p['water_density_kg_m3']*p['latent_J_kg']);dv=min(self.stocks['steam_water'],required);self.stocks['steam_water']-=dv;self.receiver_volume+=dv;used=dv*p['water_density_kg_m3']*p['latent_J_kg'];self.latent+=used;self.loss+=latent-used
     effective=h*(dv/required if required>0 else 0.);dn=self.sample_N*(-math.expm1(-p['transfer_rate_s_inverse']*self.method['steam_fraction']*effective));self.sample_N-=dn;self.receiver_N+=dn*p['capture_fraction'];self.escaped_N+=dn*(1-p['capture_fraction'])
     eq=self.method['titrant_equivalents_mol_m3'];demand=max(0.,(self.receiver_N/MN+self.receiver_blank_eq-self.receiver_acid_eq)/eq);acid=min(demand,p['titrant_rate_m3_s']*h,self.stocks['titrant']);self.stocks['titrant']-=acid;self.receiver_volume+=acid;self.titrant_volume+=acid;self.receiver_acid_eq+=acid*eq;self.elapsed_s+=h
     if not self.curve or self.elapsed_s-self.curve[-1]['time_s']>=.999999 or self.elapsed_s>=self.method['distillation_window_s']-1e-9:self.curve.append(dict(time_s=self.elapsed_s,titrant_volume_m3=self.titrant_volume,color_signal=self.color()))
     if required>dv+1e-15:self._end('failed','steam_water_depleted')
     elif demand>acid+1e-15 and self.stocks['titrant']<1e-15:self._end('failed','titrant_depleted')
     elif self.elapsed_s>=self.method['distillation_window_s']-1e-9:
      residual=self.receiver_N/MN+self.receiver_blank_eq-self.receiver_acid_eq
      if residual<=self.method['endpoint_tolerance_mol']+1e-12:self._end('succeeded')
      else:self._end('failed','endpoint_not_reached_at_host_window')
   if cooling:self.cooldown=max(0.,self.cooldown-h)
   for attr,target,speed in [('door_angle',1.8 if self.door_open else 0.,p['door_speed_rad_s'] if p else math.pi),('lever_angle',1.2 if self.clamped else 0.,p['lever_speed_rad_s'] if p else math.pi)]:
    old=getattr(self,attr);setattr(self,attr,old+min(speed*h,max(-speed*h,target-old)))
   self.time_s+=h;left-=h;self.revision+=1

class VelpUdk159Session:
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
        meta=export_velp_udk159_contract_metadata()
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('analysis.measure'))

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])





def export_velp_udk159_contract_metadata():
 caps={};slots={}
 for k,(schema,unit,origin,visibility,components,write) in SLOTS.items():slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'VelpUdk159Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
 immediate={'plant.configure':['plant.configured','truth.boiler_temperature_k'],'method.configure':['method.configured','method.steam_fraction','panel.steam_percent'],'sample.configure':['sample.configured','truth.initial_n_kg','truth.sample_n_kg','truth.excluded_n_kg'],'stocks.configure':['stocks.configured'],'power.set':['power.enabled','analysis.color_signal'],'power.interrupt':['power.enabled','cycle.phase','cycle.stage','cycle.cooldown_s','analysis.color_signal'],'coolant.set':['coolant.valid','cycle.phase','cycle.stage','cycle.cooldown_s'],'door.set_open':['door.open_target'],'tube.set_clamped':['tube.clamped_target'],'acquisition.start':['cycle.phase','cycle.stage','cycle.elapsed_s','analysis.nitrogen_kg','panel.nitrogen_mg'],'acquisition.stop':['cycle.phase','cycle.stage','cycle.cooldown_s','analysis.nitrogen_kg','panel.nitrogen_mg'],'product.collect':['truth.receiver_n_kg','truth.collected_n_kg','truth.receiver_volume_m3','analysis.color_signal'],'residue.drain':['truth.receiver_n_kg','truth.sample_n_kg','truth.excluded_n_kg','truth.waste_n_kg','truth.receiver_volume_m3','analysis.color_signal'],'fault.acknowledge':['cycle.phase','cycle.stage']}
 step_effects=['cycle.phase','cycle.stage','cycle.elapsed_s','cycle.cooldown_s','analysis.color_signal','analysis.nitrogen_kg','panel.nitrogen_mg','panel.titrant_ml']+[k for k in slots if k.startswith('truth.')]
 for k,(inp,out,components,meaning,kind,vis,example) in CAPS.items():
  async_=k=='acquisition.start';query=kind=='query';caps[k]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.velp_udk159.VelpUdk159Session.invoke',invocation=f'session.invoke(instance_id="udk159_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if query else [dict(description_zh='预消解、有限试剂、碱/捕集容量、冷却水、门与管夹守卫；不是硬件安全认证',state_ids=['cycle.phase','sample.configured','power.enabled','door.angle_rad','lever.angle_rad','coolant.valid'],enforced_by='runtime',check='VelpUdk159Runtime.invoke guards')],state_effects=[] if query else [dict(state_id=i,timing='immediate',description_zh='配置目标或有限事务') for i in immediate.get(k,[])]+[dict(state_id=i,timing='on_step',description_zh='仅由宿主时钟推进') for i in (step_effects if k in ['acquisition.start','acquisition.stop','power.interrupt','coolant.set'] else ['door.angle_rad'] if k=='door.set_open' else ['lever.angle_rad','holder.lift_m'] if k=='tube.set_clamped' else [])],observes=[],model_effects=dict(mode='none',description_zh='实际场景验证后声明联动',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='有限蒸馏窗口结束且比色酸当量残差达宿主阈值' if async_ else '配置/事务/查询完成',completion_state_ids=['cycle.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['finite_reagents','finite_nitrogen','host_clock','principal_request_cache'],partial_failure_zh='取消或失败保留部分滴定和氮转移，无最终有效结果'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT','UNKNOWN_REFERENCE','REFERENCE_RANGE','CAPACITY_LIMIT']],example_arguments=copy.deepcopy(example),verification=[],limitations=['动态曲线为显式比色/传质假设，不是原厂实测或性能保证'],source_ids=['manufacturer_primary','manufacturer_cereals','simulation_assumption'])
 mech=dict(component_ids=['boiler','condenser','sample_tube','titration_vessel','display_digits'],state_ids=list(slots),meaning_zh='常压蒸汽蒸馏、同步比色滴定、有限氮/试剂/热账本',implementation='lumped',equations=['N_initial=reference_fraction*sample_mass;N_available=N_initial*explicit_Kjeldahl_fraction*upstream_recovery','N_initial=N_sample+N_excluded+N_receiver+N_escaped+N_waste+N_collected','C*dT/dt=eta*P-UA*(T-Tambient);T<=373.15K;surplus energy gives steam latent heat','P<=2200W;steam fraction0.1..1;electrical W is not entirely latent heat','N_sample_next=N_sample*exp(-host_k*steam_fraction*effective_steam_time);capture_fraction explicit','V_initial=V_stocks+V_sample+V_receiver+V_waste+V_collected;additive volumes,steam water finite','N_reported=max(0,(V_acid-V_blank)*acid_equivalents_mol_m3*0.0140067 kg/mol)','color=tanh((N_receiver/M_N+blank_equiv-acid_equiv)/host_optical_scale);not a pH electrode','Heating energy=storage+ambient_or_vent_loss+delivered_steam_latent;no full condenser CFD','Protein_fraction=N_reported/sample_mass*explicit_method_factor;not all nitrogen is protein','Only step moves door/lever/holder,heats,doses,transfers N and generates time-series'],parameters={},source_ids=['manufacturer_primary','manufacturer_cereals','usda_foundation','simulation_assumption'],limitations=['50 USDA total-N matrices and2 UDK159 measured cereal endpoints;zero measured time-resolved titration curves.','Total N is not automatically TKN;digestion,available fraction,transfer,capture and matrix effects uncalibrated.','Current manufacturer online endpoint has no required time setting;host time window retained for task.','Legacy25mL burette/2uLstep not verified onF30200155;no current capacity or digitization claim.','Thermal C,UA,efficiency,latent heat and pump/optical/door rates are host assumptions.','Thermal model covers lumped boiler,not sample/condenser multi-node energy;cooldown is host safety policy.','Single sample per instance;no automatic reloading,recipe archive,WiFi,full factory GUI or auto sampler.','No reactive speciation,pH calculation,volume contraction,entrainment,carryover,uncertainty or calibrated endpoint color.','Hot liquid,caustic/acid,pressure and hardware safety certification not verified.','31kg only whole instrument;part masses/inertias/friction unknown;0.1kg solver proxies.'])
 spec=importlib.util.spec_from_file_location('_udk_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'distillation':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.configure':(SAMPLE,example_sample())});mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'distillation':mech})

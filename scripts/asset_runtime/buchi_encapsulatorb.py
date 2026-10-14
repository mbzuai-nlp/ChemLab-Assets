"""B390 open single-nozzle pressure feed; finite gas/materials and explicit assumptions."""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.pccell_ed64002 import validate
ROOT=Path(__file__).resolve().parents[4] if 'outputs' in str(__file__) else Path(__file__).resolve().parents[2]
SLUG='buchi_encapsulatorb';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-052';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/buchi_encapsulatorb_bead_library/templates.json';R=8.314462618;PAMB=101325.;NOZZLES=[80e-6,120e-6,150e-6,200e-6,300e-6,450e-6,750e-6,.001]
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
PLANT=obj({'ambient_k':number('K',278.15,313.15),'bottle_volume_m3':{'type':'number','enum':[.0005],'x-unit':'m^3'},'dead_volume_m3':number('m^3',0,5e-6),'pressure_tau_s':number('s',.1,20),'valve_speed_s':number('1/s',.1,10),'line_resistance_pa_s_m3':number('Pa*s/m^3',3e10,1e14),'nozzle_length_m':number('m',1e-5,.01),'heater_power_w':number('W',1,150),'heater_capacity_j_k':number('J/K',10,1000),'nozzle_capacity_j_k':number('J/K',5,500),'heater_loss_w_k':number('W/K',.01,10),'nozzle_loss_w_k':number('W/K',.01,10),'coupling_w_k':number('W/K',.1,20),'heater_gain_w_k':number('W/K',.1,100),'heater_delay_s':number('s',0,120),'gelling_rate_s':number('1/s',.001,10),'ca_demand_mol_kg':number('mol/kg',.01,10),'gel_permeability_fraction':number('1',0,1),'coalescence_loss_fraction':number('1',0,.9),'electrostatic_scale_v':number('V',100,2500),'membrane_amplitude_m':number('m',0,2e-5),'accept_uncalibrated_model':BOOL})
PLANT_EXAMPLE=dict(ambient_k=298.15,bottle_volume_m3=.0005,dead_volume_m3=2e-6,pressure_tau_s=.7,valve_speed_s=4.,line_resistance_pa_s_m3=3.8e11,nozzle_length_m=.0003,heater_power_w=60.,heater_capacity_j_k=100.,nozzle_capacity_j_k=20.,heater_loss_w_k=.4,nozzle_loss_w_k=.2,coupling_w_k=2.,heater_gain_w_k=20.,heater_delay_s=0.,gelling_rate_s=.2,ca_demand_mol_kg=2.5,gel_permeability_fraction=.05,coalescence_loss_fraction=.15,electrostatic_scale_v=1000.,membrane_amplitude_m=1e-5,accept_uncalibrated_model=True)
METHOD=obj({'target_volume_m3':number('m^3',1e-7,.0004),'target_flow_m3_s':number('m^3/s',.5e-6/60,200e-6/60),'accept_host_observer_only':BOOL});METHOD_EXAMPLE=dict(target_volume_m3=5e-6,target_flow_m3_s=6e-6/60,accept_host_observer_only=True)
SAMPLE=obj({'volume_m3':number('m^3',5e-6,.00045),'polymer_kg_m3':number('kg/m^3',1,40),'payload_id':TEXT,'payload_kg_m3':number('kg/m^3',0,100),'density_kg_m3':number('kg/m^3',800,1300),'viscosity_pa_s':number('Pa*s',.0005,.4),'surface_tension_n_m':number('N/m',.01,.1),'viscosity_activation_k':number('K',0,10000),'payload_diffusion_m2_s':number('m^2/s',0,1e-8),'liquid_cp_j_kg_k':number('J/kg/K',1000,5000),'accept_unknown_compatibility':BOOL});SAMPLE_EXAMPLE=dict(volume_m3=15e-6,polymer_kg_m3=15.,payload_id='caffeine',payload_kg_m3=.5,density_kg_m3=1000.,viscosity_pa_s=.01,surface_tension_n_m=.055,viscosity_activation_k=1500.,payload_diffusion_m2_s=1e-10,liquid_cp_j_kg_k=4180.,accept_unknown_compatibility=True)
BATH=obj({'volume_m3':number('m^3',5e-5,.001),'capacity_m3':number('m^3',.0001,.002),'calcium_mol_m3':number('mol/m^3',0,500),'grounded':BOOL,'accept_ca_binding_proxy':BOOL});BATH_EXAMPLE=dict(volume_m3=.0002,capacity_m3=.0005,calcium_mol_m3=100.,grounded=True,accept_ca_binding_proxy=True)
GAS=obj({'supply_gauge_pa':number('Pa',150000,700000),'available_mol':number('mol',.0001,10),'accept_ideal_gas':BOOL});GAS_EXAMPLE=dict(supply_gauge_pa=200000.,available_mol=1.,accept_ideal_gas=True)
PRODUCT_FIELDS={}
for species in ['polymer','payload','water']:
 for loc in ['initial','feed','bead','bath','collected']:PRODUCT_FIELDS[loc+'_'+species+'_kg']=number('kg',0)
for k in ['initial_ca_mol','free_ca_mol','bead_ca_mol','collected_ca_mol','initial_gas_mol','supply_gas_mol','bottle_gas_mol','vented_gas_mol','bead_payload_mol','collected_payload_mol']:PRODUCT_FIELDS[k]=number('mol',0)
for k in ['feed_volume_m3','emitted_volume_m3','bead_volume_m3','collected_volume_m3']:PRODUCT_FIELDS[k]=number('m^3',0)
for k in ['mean_bead_diameter_m']:PRODUCT_FIELDS[k]=number('m',0)
for k in ['bead_count','mature_bead_count','encapsulation_efficiency']:PRODUCT_FIELDS[k]=number('1',0)
PRODUCT_FIELDS.update(thermal_residual_j=number('J'),evidence=TEXT,template_source=TEXT)
PRODUCT=obj(PRODUCT_FIELDS);MEASURE=obj({'phase':PHASE,'stage':TEXT,'frequency_hz':number('Hz',0,6000),'pressure_gauge_pa':number('Pa',0,150000),'heater_temperature_k':number('K',273.15,400),'voltage_v':number('V',0,2500),'evidence':TEXT,'template_source':TEXT})
CAPS={}
def cap(k,inp,out,c,zh,kind='device_command',vis='agent',example=None):CAPS[k]=(inp,out,c,zh,kind,vis,example or {})
for name,schema,ex,c in [('plant',PLANT,PLANT_EXAMPLE,'body'),('method',METHOD,METHOD_EXAMPLE,'display_digits'),('sample',SAMPLE,SAMPLE_EXAMPLE,'pressure_bottle'),('bath',BATH,BATH_EXAMPLE,'receiving_bath'),('air',GAS,GAS_EXAMPLE,'air_inlet')]:cap(name+'.configure',schema,obj({'configured':BOOL}),[c],name+'显式宿主配置','configuration','agent' if name=='method' else 'evaluator',ex)
cap('nozzle.configure',obj({'diameter_m':{'type':'number','enum':NOZZLES,'x-unit':'m'}}),obj({'diameter_m':number('m')}),['nozzle'],'8标准单喷嘴语义选择；不伪装同心或气辅','configuration',example={'diameter_m':.0003})
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['button_power'],'电源目标；断电不自动关闭手动气液阀',example={'enabled':True});cap('power.toggle',obj({}),obj({'enabled':BOOL}),['button_power'],'实际电源按钮翻转')
cap('air.set_pressure',obj({'gauge_pa':number('Pa',0,100000)}),obj({'gauge_pa':number('Pa',0,100000)}),['pressure_regulator'],'手动调压目标，只有step更新气体库存和压力',example={'gauge_pa':40000.})
cap('valve.set_opening',obj({'fraction':number('1',0,1)}),obj({'fraction':number('1',0,1)}),['flow_valve'],'液体管夹阀开度；实际流率由阻力压差决定',example={'fraction':1.})
cap('vibration.set_frequency',obj({'frequency_hz':number('Hz',40,6000)}),obj({'frequency_hz':number('Hz',40,6000)}),['membrane'],'振动频率目标，非测得颗粒频率',example={'frequency_hz':800.})
cap('vibration.toggle',obj({}),obj({'enabled':BOOL}),['button_touch_frequency','membrane'],'触屏振动开关')
cap('thermal.set_temperature',obj({'temperature_k':number('K',283.15,353.15)}),obj({'temperature_k':number('K',283.15,353.15)}),['heater_block'],'现版技术表10–80C目标，无主动冷却',example={'temperature_k':313.15})
cap('thermal.toggle',obj({}),obj({'enabled':BOOL}),['button_touch_heater','heater_block'],'触屏加热开关；延迟/热响应由宿主给定')
cap('electrostatic.set_voltage',obj({'voltage_v':number('V',250,2500)}),obj({'voltage_v':number('V',250,2500)}),['electrode'],'静电辅助目标，必须接地，不伪造测得包封率',example={'voltage_v':1000.})
cap('electrostatic.toggle',obj({}),obj({'enabled':BOOL}),['button_touch_electrode','electrode'],'触屏静电开关')
cap('electrode.set_gap',obj({'gap_m':number('m',.003,.008)}),obj({'gap_m':number('m',.003,.008)}),['electrode'],'手动电极间距；带电/出液时禁止',example={'gap_m':.005})
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['display_digits'],'宿主开始观察目标排液量，不自动开手动阀')
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['display_digits'],'只取消宿主观察，不回滚产物，也不假设手动阀关闭')
cap('encapsulation.measure',obj({}),MEASURE,['display_digits'],'面板理想示值；没有粒径分析传感器','query')
cap('product.read',obj({}),PRODUCT,['receiving_bath'],'评价器颗粒/凝胶/包封近似和有限账本','query','evaluator')
cap('product.collect',obj({'fraction':number('1',0,1),'recipient_id':TEXT,'accept_external_filtration':BOOL}),obj({'transferred_payload_kg':number('kg',0),'recipient_id':TEXT}),['receiving_bath'],'停止实际液流并关闭高压后外部过滤取成熟颗粒','device_command','evaluator',dict(fraction=.5,recipient_id='external_assay',accept_external_filtration=True))
cap('reference.lookup',obj({'substance_id':TEXT}),obj({'molar_mass_kg_mol':number('kg/mol',0),'formula':TEXT,'source':TEXT,'evidence':TEXT,'template_source':TEXT}),['pressure_bottle'],'原始身份/计算分子量参考，不返回虚构包埋率','query',example={'substance_id':'caffeine'})
SLOTS={};SLOT_ZH={}
def slot(k,s,u,zh,o='derived',v='agent',c='body',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=zh
for n in ['plant','method','sample','bath','air']:slot(n+'.configured',BOOL,'1',n+'宿主配置','setpoint')
slot('power.enabled',BOOL,'1','电源通断','setpoint',c='button_power',w=('power.set','enabled'))
slot('nozzle.diameter_m',number('m',80e-6,.001),'m','标准单喷孔语义直径','setpoint',c='nozzle',w=('nozzle.configure','diameter_m'))
for k,u,lo,hi,zh,c,capid,arg in [('air.target_pa','Pa',0,100000,'手动气压目标表压','pressure_regulator','air.set_pressure','gauge_pa'),('valve.target_fraction','1',0,1,'管夹阀开度目标','flow_valve','valve.set_opening','fraction'),('vibration.target_hz','Hz',40,6000,'振动频率目标','membrane','vibration.set_frequency','frequency_hz'),('thermal.target_k','K',283.15,353.15,'加热目标非喷嘴实温','heater_block','thermal.set_temperature','temperature_k'),('electrostatic.target_v','V',250,2500,'辅助电压目标','electrode','electrostatic.set_voltage','voltage_v'),('electrode.target_gap_m','m',.003,.008,'手动电极间距目标','electrode','electrode.set_gap','gap_m')]:slot(k,number(u,lo,hi),u,zh,'setpoint',c=c,w=(capid,arg))
for n,c in [('vibration','membrane'),('thermal','heater_block'),('electrostatic','electrode')]:slot(n+'.enabled',BOOL,'1','触屏'+n+'开关','setpoint',c=c)
for k,s,u,zh,c in [('regulator.angle_rad',number('rad',0,2.4),'rad','手动气压阀旋角代理','pressure_regulator'),('valve.angle_rad',number('rad',0,2.4),'rad','管夹阀旋角代理','flow_valve'),('electrode.drop_m',number('m',0,.005),'m','电极相对3mm位置向下调整','electrode'),('membrane.displacement_m',number('m',-2e-5,2e-5),'m','解析高频相位的端点采样，非真实振动动力学','membrane')]:slot(k,s,u,zh,'scene',c=c)
for k,u,lo,hi,zh in [('panel.frequency_hz','Hz',0,6000,'实际启用频率理想示值'),('panel.pressure_bar','bar',0,1.5,'相对101325Pa的表压'),('panel.temperature_c','degC',0,130,'加热块近旁理想温度，不是喷嘴温度'),('panel.voltage_v','V',0,2500,'实际启用辅助电压理想示值')]:slot(k,number(u,lo,hi),u,zh,c='display_digits')
slot('cycle.phase',PHASE,'1','宿主排液量观察状态');slot('cycle.stage',TEXT,'1','成滴稳定性/宿主观察阶段')
slot('truth.nozzle_temperature_k',number('K',273.15,400),'K','喷嘴温度真值非屏幕温度','process_truth','evaluator','nozzle');slot('truth.flow_m3_s',number('m^3/s',0),'m^3/s','实际液体体积流率，不是内置流量计','process_truth','evaluator','nozzle');slot('truth.wavelength_ratio',number('1',0),'1','实际波长除Weber最佳波长','process_truth','evaluator','nozzle');slot('truth.ledger',PRODUCT,'1','有限材料/气体/Ca/热量账本','process_truth','evaluator','receiving_bath')
class BuchiEncapsulatorbRuntime:
 def __init__(self,instance_id='encapsulator_01'):
  self.instance_id=instance_id;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.plant=self.method=self.sample=self.bath=self.air=None;self.power=False;self.nozzle=.0003;self.p_target=self.reg_angle=self.valve_target=self.valve_angle=0.;self.freq_target=800.;self.frequency=0.;self.vibration=False;self.heating=False;self.hv=False;self.t_target=313.15;self.h_temp=self.n_temp=298.15;self.voltage_target=1000.;self.voltage=0.;self.gap_target=self.gap=.003;self.membrane=self.vib_phase=0.;self.heating_age=0.;self.feed_volume=self.emitted=self.collected_volume=0.;self.initial=[0.,0.,0.];self.feed=[0.,0.,0.];self.bath_material=[0.,0.,0.];self.collected=[0.,0.,0.];self.packets=[];self.ca_initial=self.ca_free=self.ca_collected=0.;self.gas_initial=self.gas_supply=self.gas_bottle=self.gas_vent=0.;self.pressure=self.flow=self.ratio=0.;self.heat_input=self.heat_loss=self.heat_liquid=0.;self.phase='idle';self.stage='idle';self.start_emitted=0.;self.time_s=0.;self.revision=0;self.operation_id='';self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility):
  if visibility not in ['agent','debug','evaluator'] or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return BuchiEncapsulatorbSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _product(self):
  p={};tot=[sum(x['m'][i] for x in self.packets) for i in range(3)];count=sum(x['count'] for x in self.packets);mw=self.templates[self.sample['payload_id']]['value'] if self.sample else 1.
  for i,n in enumerate(['polymer','payload','water']):
   for loc,vals in [('initial',self.initial),('feed',self.feed),('bead',tot),('bath',self.bath_material),('collected',self.collected)]:p[loc+'_'+n+'_kg']=max(0.,vals[i])
  energy=0. if not self.plant else self.plant['heater_capacity_j_k']*(self.h_temp-self.plant['ambient_k'])+self.plant['nozzle_capacity_j_k']*(self.n_temp-self.plant['ambient_k'])
  p.update(initial_ca_mol=self.ca_initial,free_ca_mol=self.ca_free,bead_ca_mol=sum(x['ca'] for x in self.packets),collected_ca_mol=self.ca_collected,initial_gas_mol=self.gas_initial,supply_gas_mol=self.gas_supply,bottle_gas_mol=self.gas_bottle,vented_gas_mol=self.gas_vent,bead_payload_mol=tot[1]/mw,collected_payload_mol=self.collected[1]/mw,feed_volume_m3=self.feed_volume,emitted_volume_m3=self.emitted,bead_volume_m3=sum(x['volume'] for x in self.packets),collected_volume_m3=self.collected_volume,bead_count=count,mature_bead_count=sum(x['count'] for x in self.packets if x['gel']>=.95),mean_bead_diameter_m=sum(x['d']*x['count'] for x in self.packets)/count if count else 0.,encapsulation_efficiency=(tot[1]+self.collected[1])/max(1e-30,self.initial[1]-self.feed[1]) if self.initial[1]>0 else 0.,thermal_residual_j=energy-self.heat_input+self.heat_loss+self.heat_liquid,evidence='synthetic_bead_gelation_leakage;real_identity_and_manufacturer_conditions;NOT_measured_particle_assay',template_source=TEMPLATE_REL);return p
 def _values(self):
  v={n+'.configured':bool(getattr(self,n)) for n in ['plant','method','sample','bath','air']};v.update({'power.enabled':self.power,'nozzle.diameter_m':self.nozzle,'air.target_pa':self.p_target,'valve.target_fraction':self.valve_target,'vibration.target_hz':self.freq_target,'thermal.target_k':self.t_target,'electrostatic.target_v':self.voltage_target,'electrode.target_gap_m':self.gap_target,'vibration.enabled':self.vibration,'thermal.enabled':self.heating,'electrostatic.enabled':self.hv,'regulator.angle_rad':self.reg_angle,'valve.angle_rad':self.valve_angle,'electrode.drop_m':self.gap-.003,'membrane.displacement_m':self.membrane,'panel.frequency_hz':self.frequency,'panel.pressure_bar':self.pressure/1e5,'panel.temperature_c':self.h_temp-273.15,'panel.voltage_v':self.voltage,'cycle.phase':self.phase,'cycle.stage':self.stage,'truth.nozzle_temperature_k':self.n_temp,'truth.flow_m3_s':self.flow,'truth.wavelength_ratio':self.ratio,'truth.ledger':self._product()});return v
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ['debug','evaluator']})
 def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k!='templates'})
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if self.phase=='running' and capability.endswith('.configure'):raise ActionRejected('BUSY')
  if capability=='plant.configure':
   if self.plant or not a['accept_uncalibrated_model']:raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);self.h_temp=self.n_temp=a['ambient_k'];out=dict(configured=True)
  elif capability=='method.configure':
   if self.phase!='idle' or not a['accept_host_observer_only']:raise ActionRejected('PRECONDITION_FAILED')
   self.method=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='sample.configure':
   if not self.plant or self.sample or self.air or not a['accept_unknown_compatibility'] or a['polymer_kg_m3']+a['payload_kg_m3']>=a['density_kg_m3']:raise ActionRejected('PRECONDITION_FAILED')
   if a['payload_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   self.sample=copy.deepcopy(a);self.feed_volume=a['volume_m3'];self.initial=[a['volume_m3']*a['polymer_kg_m3'],a['volume_m3']*a['payload_kg_m3'],a['volume_m3']*(a['density_kg_m3']-a['polymer_kg_m3']-a['payload_kg_m3'])];self.feed=self.initial.copy();out=dict(configured=True)
  elif capability=='bath.configure':
   if self.bath or a['volume_m3']>=a['capacity_m3'] or not a['accept_ca_binding_proxy']:raise ActionRejected('PRECONDITION_FAILED')
   self.bath=copy.deepcopy(a);self.ca_initial=self.ca_free=a['volume_m3']*a['calcium_mol_m3'];out=dict(configured=True)
  elif capability=='air.configure':
   if not self.sample or self.air or not a['accept_ideal_gas']:raise ActionRejected('PRECONDITION_FAILED')
   self.air=copy.deepcopy(a);self.gas_supply=a['available_mol'];self.gas_bottle=PAMB*(self.plant['bottle_volume_m3']-self.feed_volume)/(R*self.plant['ambient_k']);self.gas_initial=self.gas_supply+self.gas_bottle;out=dict(configured=True)
  elif capability=='nozzle.configure':
   if self.flow>1e-12 or self.pressure>100 or self.hv or self.n_temp>313.15:raise ActionRejected('PRECONDITION_FAILED')
   self.nozzle=a['diameter_m'];out=a
  elif capability in ['power.set','power.toggle']:
   self.power=a['enabled'] if capability=='power.set' else not self.power;out=dict(enabled=self.power)
  elif capability=='air.set_pressure':
   if not self.air:raise ActionRejected('PRECONDITION_FAILED')
   self.p_target=a['gauge_pa'];out=a
  elif capability=='valve.set_opening':
   if not all([self.plant,self.sample,self.air,self.bath]):raise ActionRejected('PRECONDITION_FAILED')
   self.valve_target=a['fraction'];out=a
  elif capability=='vibration.set_frequency':self.freq_target=a['frequency_hz'];out=a
  elif capability=='vibration.toggle':self.vibration=not self.vibration;out=dict(enabled=self.vibration)
  elif capability=='thermal.set_temperature':self.t_target=a['temperature_k'];out=a
  elif capability=='thermal.toggle':self.heating=not self.heating;self.heating_age=0.;out=dict(enabled=self.heating)
  elif capability=='electrostatic.set_voltage':self.voltage_target=a['voltage_v'];out=a
  elif capability=='electrostatic.toggle':
   if not self.hv and (not self.bath or not self.bath['grounded']):raise ActionRejected('PRECONDITION_FAILED')
   self.hv=not self.hv;out=dict(enabled=self.hv)
  elif capability=='electrode.set_gap':
   if self.hv or self.flow>1e-12 or self.n_temp>313.15:raise ActionRejected('PRECONDITION_FAILED')
   self.gap_target=a['gap_m'];out=a
  elif capability=='acquisition.start':
   if not all([self.plant,self.method,self.sample,self.bath,self.air,self.power]) or self.phase!='idle' or self.method['target_volume_m3']>self.feed_volume-self.plant['dead_volume_m3'] or self.method['target_volume_m3']+self.bath['volume_m3']>self.bath['capacity_m3']:raise ActionRejected('PRECONDITION_FAILED')
   self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);self.start_emitted=self.emitted;self.phase='running';self.stage='observing';out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('PRECONDITION_FAILED')
   self.phase='cancelled';self.stage='observer_cancelled_flow_not_closed';out=dict(phase=self.phase)
  elif capability=='encapsulation.measure':return dict(phase=self.phase,stage=self.stage,frequency_hz=self.frequency,pressure_gauge_pa=self.pressure,heater_temperature_k=self.h_temp,voltage_v=self.voltage,evidence='ideal_panel_readouts_NOT_bead_diameter_or_nozzle_sensor',template_source=TEMPLATE_REL)
  elif capability=='product.read':return self._product()
  elif capability=='product.collect':
   if self.flow>1e-12 or self.valve_angle>1e-8 or self.hv or not a['accept_external_filtration']:raise ActionRejected('PRECONDITION_FAILED')
   amount=0.
   for x in self.packets:
    if x['gel']<.95:continue
    f=a['fraction'];amount+=x['m'][1]*f
    for i in range(3):self.collected[i]+=x['m'][i]*f;x['m'][i]*=1-f
    self.ca_collected+=x['ca']*f;x['ca']*=1-f;self.collected_volume+=x['volume']*f;x['volume']*=1-f;x['count']*=1-f
   out=dict(transferred_payload_kg=amount,recipient_id=a['recipient_id'])
  elif capability=='reference.lookup':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   r=self.templates[a['substance_id']];return dict(molar_mass_kg_mol=r['value'],formula=r['formula'],source=r['source'],evidence=r['evidence'],template_source=TEMPLATE_REL)
  self.revision+=1;return copy.deepcopy(out)
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or not 0<=dt_s<=7200:raise ActionRejected('INVALID_ARGUMENT')
  left=float(dt_s)
  while left>1e-10:
   h=min(.02,left);p=self.plant
   if p:
    self.reg_angle+=max(-2.4*h,min(2.4*h,2.4*self.p_target/1e5-self.reg_angle));self.valve_angle+=max(-2.4*p['valve_speed_s']*h,min(2.4*p['valve_speed_s']*h,2.4*self.valve_target-self.valve_angle));self.gap+=max(-.01*h,min(.01*h,self.gap_target-self.gap));self.frequency=self.freq_target if self.power and self.vibration else 0.;self.voltage=self.voltage_target if self.power and self.hv else 0.;self.vib_phase=(self.vib_phase+math.tau*self.frequency*h)%math.tau;self.membrane=p['membrane_amplitude_m']*math.sin(self.vib_phase) if self.frequency else 0.
    if self.air:
     vh=p['bottle_volume_m3']-self.feed_volume;goal=PAMB+self.reg_angle/2.4*1e5;dn=(goal*vh/(R*p['ambient_k'])-self.gas_bottle)*(-math.expm1(-h/p['pressure_tau_s']))
     if dn>0:dn=min(dn,self.gas_supply);self.gas_supply-=dn
     else:self.gas_vent-=dn
     self.gas_bottle+=dn;self.pressure=max(0.,self.gas_bottle*R*p['ambient_k']/vh-PAMB)
    self.flow=0.;self.ratio=0.
    if self.sample and self.bath and self.air:
     s=self.sample;mu=s['viscosity_pa_s']*math.exp(s['viscosity_activation_k']*(1/self.n_temp-1/p['ambient_k']));resistance=p['line_resistance_pa_s_m3']+128*mu*p['nozzle_length_m']/(math.pi*self.nozzle**4);flow=max(0.,self.pressure-4*s['surface_tension_n_m']/self.nozzle)/resistance*(self.valve_angle/2.4)**2;dv=min(flow*h,max(0.,self.feed_volume-p['dead_volume_m3']));self.flow=dv/h
     if dv>1e-20:
      velocity=self.flow/(math.pi*self.nozzle**2/4);wavelength=math.pi*math.sqrt(2)*self.nozzle*math.sqrt(1+3*mu/math.sqrt(s['density_kg_m3']*s['surface_tension_n_m']*self.nozzle));self.ratio=velocity/(self.frequency*wavelength) if self.frequency else 0.;stable=self.frequency>0 and .7<=self.ratio<=1.3;frac=1-p['coalescence_loss_fraction']*math.exp(-self.voltage/p['electrostatic_scale_v']) if stable else 0.
      if self.bath['volume_m3']+self.emitted-self.collected_volume+dv>self.bath['capacity_m3']:
       dv=max(0.,self.bath['capacity_m3']-self.bath['volume_m3']-self.emitted+self.collected_volume);self.flow=dv/h;self.phase='failed';self.stage='host_capacity_guard';self.valve_target=self.valve_angle=0.;frac=0.
      masses=[x*dv/self.feed_volume for x in self.feed]
      for i in range(3):self.feed[i]-=masses[i];self.bath_material[i]+=masses[i]*(1-frac)
      if frac>0:
       d=(6*self.flow/(math.pi*self.frequency))**(1/3);self.packets.append(dict(m=[x*frac for x in masses],volume=dv*frac,count=self.frequency*h*frac,d=d,gel=0.,ca=0.))
      self.feed_volume-=dv;self.emitted+=dv
      if self.phase=='running':self.stage='forming' if stable else 'jet_unstable'
    if self.bath:
     for x in self.packets:
      if x['volume']<=0:continue
      dg=(1-x['gel'])*(-math.expm1(-p['gelling_rate_s']*min(1.,self.ca_free/max(1e-30,self.bath['volume_m3']*100.))*h));need=x['m'][0]*p['ca_demand_mol_kg']*dg
      if need>self.ca_free:dg*=self.ca_free/need;need=self.ca_free
      self.ca_free-=need;x['ca']+=need;x['gel']+=dg;k=math.pi**2*self.sample['payload_diffusion_m2_s']/(x['d']/2)**2*(1-x['gel']+p['gel_permeability_fraction']*x['gel']);loss=x['m'][1]*(-math.expm1(-k*h));x['m'][1]-=loss;self.bath_material[1]+=loss
    self.heating_age+=h if self.power and self.heating else 0.;power=min(p['heater_power_w'],max(0.,p['heater_gain_w_k']*(self.t_target-self.h_temp))) if self.power and self.heating and self.heating_age>=p['heater_delay_s'] else 0.;link=p['coupling_w_k']*(self.h_temp-self.n_temp);hl=p['heater_loss_w_k']*(self.h_temp-p['ambient_k']);nl=p['nozzle_loss_w_k']*(self.n_temp-p['ambient_k']);liq=self.flow*(self.sample['density_kg_m3']*self.sample['liquid_cp_j_kg_k'] if self.sample else 0.)*(self.n_temp-p['ambient_k']);self.h_temp+=(power-hl-link)*h/p['heater_capacity_j_k'];self.n_temp+=(link-nl-liq)*h/p['nozzle_capacity_j_k'];self.heat_input+=power*h;self.heat_loss+=(hl+nl)*h;self.heat_liquid+=liq*h
    if self.phase=='running':
     if self.emitted-self.start_emitted>=self.method['target_volume_m3']-1e-14:self.phase='succeeded';self.stage='target_observed_manual_flow_continues'
     elif self.feed_volume<=p['dead_volume_m3']+1e-14:self.phase='failed';self.stage='feed_exhausted_with_dead_volume'
   self.time_s+=h;left-=h;self.revision+=1

class BuchiEncapsulatorbSession:
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
        meta=export_buchi_encapsulatorb_contract_metadata()
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('encapsulation.measure'))

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])





def export_buchi_encapsulatorb_contract_metadata():
 caps={};slots={}
 for k,(schema,unit,origin,visibility,components,write) in SLOTS.items():slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'BuchiEncapsulatorbSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
 immediate={'plant.configure':['plant.configured','panel.temperature_c','truth.nozzle_temperature_k'],'method.configure':['method.configured'],'sample.configure':['sample.configured','truth.ledger'],'bath.configure':['bath.configured','truth.ledger'],'air.configure':['air.configured','truth.ledger'],'nozzle.configure':['nozzle.diameter_m'],'power.set':['power.enabled'],'power.toggle':['power.enabled'],'air.set_pressure':['air.target_pa'],'valve.set_opening':['valve.target_fraction'],'vibration.set_frequency':['vibration.target_hz'],'vibration.toggle':['vibration.enabled'],'thermal.set_temperature':['thermal.target_k'],'thermal.toggle':['thermal.enabled'],'electrostatic.set_voltage':['electrostatic.target_v'],'electrostatic.toggle':['electrostatic.enabled'],'electrode.set_gap':['electrode.target_gap_m'],'acquisition.start':['cycle.phase','cycle.stage'],'acquisition.stop':['cycle.phase','cycle.stage'],'product.collect':['truth.ledger']}
 evolving=[k for k,v in SLOTS.items() if v[2]!='setpoint']
 for k,(inp,out,components,meaning,kind,vis,example) in CAPS.items():
  async_=k=='acquisition.start';query=kind=='query';caps[k]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.buchi_encapsulatorb.BuchiEncapsulatorbSession.invoke',invocation=f'session.invoke(instance_id="encapsulator_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if query else [dict(description_zh='有限气体/样品/浴容量、接地、手动喷嘴/电极调整限制与权限；容量边界是宿主数值停机非厂家互锁',state_ids=['cycle.phase','power.enabled','truth.flow_m3_s'],enforced_by='runtime',check='BuchiEncapsulatorbRuntime.invoke guards and step capacity guard')],state_effects=[] if query else [dict(state_id=i,timing='immediate',description_zh='配置/目标/宿主观察事务') for i in immediate.get(k,[])]+[dict(state_id=i,timing='on_step',description_zh='有限气体/液体、Weber成滴、固化泄漏、热响应和机械姿态') for i in evolving],observes=[],model_effects=dict(mode='none',description_zh='实际求值后升格',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='宿主观察排液目标达到；必须操作者关闭手动阀，完成不保证粒径/凝胶或流量自动停止' if async_ else '事务完成，不隐式推进宿主时间',completion_state_ids=['cycle.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['finite_sample','finite_gas','finite_calcium','host_clock','principal_request_cache'],partial_failure_zh='取消/目标达到不自动关手动气液阀；已排出材料、凝胶、泄漏和热量保留；物理持续过程仍随step演化'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT','UNKNOWN_REFERENCE']],example_arguments=copy.deepcopy(example),verification=[],limitations=['仅标准单喷嘴开放气压供液；没有B395无菌反应器/注射泵/粒径分析仪'],source_ids=['manufacturer_manual','simulation_assumption'])
 mech=dict(component_ids=['pressure_bottle','flow_valve','nozzle','membrane','electrode','receiving_bath','heater_block'],state_ids=list(slots),meaning_zh='理想气体压力供液、Weber振动成滴、有限Ca结合凝胶/载荷扩散泄漏及双节点喷嘴加热',implementation='lumped',equations=['P_absolute=n_gas*R*T/(V_bottle-V_liquid);P_gauge=P_absolute-101325Pa','gas_initial=supply+bottle+vented;regulator transfer limited by finite supply','Q=max(P_gauge-4sigma/D,0)/(R_line+128muL/(piD^4))*valve_fraction^2','lambda_opt=pi*sqrt(2)*D*sqrt(1+3mu/sqrt(rho*sigma*D));lambda_actual=4Q/(piD^2*f)','stable teaching regime:0.7<=lambda_actual/lambda_opt<=1.3;diameter=(6Q/(pi*f))^(1/3)','accepted_fraction=1-coalescence_host*exp(-voltage/scale_host);unstable liquid becomes bath material,not uniform beads','gelation:dg=(1-gel)*(1-exp(-k_host*c_relative*dt)),limited by remainingCa;Ca_free+Ca_beads+Ca_collected=Ca_initial','payload leakage:k=pi^2*D_host/(bead_radius^2)*(1-gel+gel_permeability*gel);leaked_mass transfers to bath','initial_species=feed+beads+bath+collected for polymer,payload,water;payload_moles=kg/PubChem_molar_mass','C_heater*dTh/dt=P_heat-G*(Th-Tn)-Kh*(Th-Ta);C_nozzle*dTn/dt=G*(Th-Tn)-Kn*(Tn-Ta)-Q*rho*cp*(Tn-Ta)','thermal_energy_change=integrated_power-ambient_loss-liquid_sensible_export;receiver thermal state not solved','manual gas/liquid valve continue without electrical power;observer start/stop do NOT auto-pump/shutflow'],parameters={},source_ids=['manufacturer_manual','manufacturer_technical','pubchem_identity','simulation_assumption'],limitations=['50参考仅PubChem计算分子量/身份，8组为同一藻酸盐家族实测条件；没有50种本机实测输出。','标准单300um外观，8喷嘴语义；flow-vibration80um颗粒、同心核壳和2–4mm大胶囊附件未实现。','压力瓶/浴为外部附件，主机320×340×290mm7kg；附件真实质量未知，场景总质量7kg仅数值假设。','150W是整机最大耗电，不作为加热器实际功率；10–80C为现版目标，30–70C旧版保留证据。','气阀时间常数/阻力、液体温黏关系、电分散、Ca结合、固化、扩散/渗透系数均需标定。','Ca结合仅代理，未实现Na/Ca完整离子交换和水凝胶体积收缩；湿珠体积按排出液体近似。','一次step产一连续统计队列，不模拟每个液滴或将计数宣称为整数测量。','膜片仅解析相位端点，不宣称PhysX解析40–6000Hz真实受力或振幅。','标准B390无内置流量、粒径或包封率传感器；粒径/包封结果只给评价器。','容量守卫为宿主数值停止，非厂家自动安全互锁；取消也不代表现实流路停机。','不预测材料相容性、活性/细胞存活、微生物、生物安全、无菌性或实际药物递送效果。'])
 spec=importlib.util.spec_from_file_location('_encapsulator_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'encapsulation':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.configure':(SAMPLE,SAMPLE_EXAMPLE)});mech['parameters']={k.lower():v for k,v in mech['parameters'].items()};return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'encapsulation':mech})

"""SFT110100mL: finite pure-CO2 process with measured binary solubility references."""
from __future__ import annotations
import copy,functools,importlib.util,json,math,sys
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.antonpaar_abbematadvanced import validate
ROOT=Path(__file__).resolve().parents[2];SLUG='sft_sft110';OUT=ROOT/'outputs/expansion_batch03/t2_vacuum/sft_sft110';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-032';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/sft_sft110_solubility_library/templates.json';VMAX=100e-6;PMAX=68.9e6;MWCO2=.0440098
def cp():
 sys.path.insert(0,str(OUT/'vendor')) if str(OUT/'vendor') not in sys.path else None
 import CoolProp.CoolProp as C
 if C.get_global_param_string('version')!='7.2.0':raise RuntimeError('Pinned CoolProp7.2.0 required')
 return C
@functools.lru_cache(maxsize=512)
def eos_tp(t,p):
 c=cp();return c.PropsSI('Dmass','T',t,'P',p,'CO2'),c.PropsSI('V','T',t,'P',p,'CO2')
PHASE={'type':'string','enum':['idle','running','succeeded','cancelled','failed']}
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
def arr(s):return {'type':'array','items':s}
PLANT=obj({'dip_tube_liquid_confirmed':BOOL,'exhaust_connected':BOOL,'compatible_dry_sample_reviewed':BOOL,'initial_co2_purge_confirmed':BOOL,'emulated_manual_sequence_confirmed':BOOL,'ambient_k':number('K',278.15,303.15),'ambient_pa':number('Pa',8e4,1.2e5),'tank_mass_kg':number('kg',.0001,100),'supply_temperature_k':number('K',253.15,293.15),'supply_pressure_abs_pa':number('Pa',4e6,7e6),'thermal_tau_s':number('s',1,1000),'restrictor_tau_s':number('s',1,1000),'cooling_tau_s':number('s',1,3000),'transfer_rate_s_inv':number('1/s',1e-6,1),'bed_area_m2':number('m^2',1e-6,.002),'bed_length_m':number('m',.001,.3),'porosity':number('1',.1,.9),'particle_diameter_m':number('m',1e-6,.01),'capture_fraction':number('1',0,1),'deposit_limit_kg':number('kg',1e-8,.01),'collection_capacity_kg':number('kg',1e-7,.1),'vent_rate_pa_s':number('Pa/s',1000,2e6),'vent_mass_limit_kg_s':number('kg/s',1e-7,.005),'handling_temperature_limit_k':number('K',293.15,333.15),'opening_pressure_margin_pa':number('Pa',100,2e4),'coefficient_evidence':TEXT})
EXAMPLE=dict(dip_tube_liquid_confirmed=True,exhaust_connected=True,compatible_dry_sample_reviewed=True,initial_co2_purge_confirmed=True,emulated_manual_sequence_confirmed=True,ambient_k=293.15,ambient_pa=101325.,tank_mass_kg=1.,supply_temperature_k=273.15,supply_pressure_abs_pa=5.5e6,thermal_tau_s=30.,restrictor_tau_s=20.,cooling_tau_s=80.,transfer_rate_s_inv=.02,bed_area_m2=.0004,bed_length_m=.08,porosity=.4,particle_diameter_m=.0005,capture_fraction=.95,deposit_limit_kg=.001,collection_capacity_kg=.01,vent_rate_pa_s=2e5,vent_mass_limit_kg_s=.001,handling_temperature_limit_k=323.15,opening_pressure_margin_pa=1000.,coefficient_evidence='synthetic_host_transfer_Ergun_bed_thermal_capture_deposition_not_SFT110_calibration')
METHOD=obj({'temperature_k':number('K',278.15,473.15),'pressure_gauge_pa':number('Pa',7.5e6,PMAX),'liquid_flow_m3_s':number('m^3/s',.01e-6/60,24e-6/60),'static_s':number('s',0,7200),'dynamic_s':number('s',0,7200),'restrictor_temperature_k':number('K',278.15,473.15),'timeout_s':number('s',1,21600),'modifier_fraction':dict(number('1',0,0),description='本单纯CO2实现只允许0；可选改性剂硬件端口未实现混合物模型')})
METHOD_EXAMPLE=dict(temperature_k=313.15,pressure_gauge_pa=20e6-101325.,liquid_flow_m3_s=10e-6/60,static_s=60.,dynamic_s=120.,restrictor_temperature_k=353.15,timeout_s=3600.,modifier_fraction=0.)
SAMPLE=obj({'sample_id':TEXT,'substance_id':TEXT,'solute_mass_kg':number('kg',1e-10,.001),'inert_matrix_mass_kg':number('kg',1e-6,.1),'single_dilute_nonreactive_solute_confirmed':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='caffeine_bed',substance_id='scco2_ryyvlzvuvijvgh_uhfffaoysa_n',solute_mass_kg=.0001,inert_matrix_mass_kg=.01,single_dilute_nonreactive_solute_confirmed=True)
RESULT=obj({'phase':PHASE,'stage':TEXT,'fault':TEXT,'temperature_k':number('K',0),'restrictor_temperature_k':number('K',0),'pressure_gauge_pa':number('Pa',0),'liquid_inlet_m3_s':number('m^3/s',0),'expanded_reference_m3_s':number('m^3/s',0),'reference_mole_fraction':nullable(number('1',0,1)),'evidence':TEXT,'template_source':TEXT})
PRODUCT=obj({'substance_id':nullable(TEXT),'collected_kg':number('kg',0),'residue_kg':number('kg',0),'dissolved_kg':number('kg',0),'restrictor_deposit_kg':number('kg',0),'inert_matrix_kg':number('kg',0),'measured_SFT110_recovery':{'type':'null'},'evidence':TEXT,'template_source':TEXT})
POINT=obj({'time_s':number('s',0),'temperature_k':number('K',0),'pressure_gauge_pa':number('Pa',0),'liquid_inlet_m3_s':number('m^3/s',0),'stage':TEXT})
REF=obj({'substance_id':TEXT,'mole_fraction':number('1',0,1),'temperature_k':number('K',0),'pressure_abs_pa':number('Pa',0),'evidence':TEXT,'source':TEXT,'original_literature':TEXT,'template_source':TEXT})
CAPS={
 'plant.configure':(PLANT,obj({'configured':BOOL}),['pump_head_0','pump_head_1','co2_inlet','vent_port'],'有限CO2供应、床/热/传质与捕集条件；宿主前置置换确认','configuration','evaluator',EXAMPLE,['plant.configured']),
 'method.configure':(METHOD,obj({'configured':BOOL}),['heater','restrictor','static_dynamic_valve'],'教学静态/动态顺序，不是原机自动配方固件','configuration','agent',METHOD_EXAMPLE,['method.configured','thermal.target_k','pressure.target_abs_pa']),
 'door.set_angle':(obj({'angle_rad':number('rad',0,1.5)}),obj({'angle_rad':number('rad',0,1.5)}),['door','door_handle'],'宿主手动开门位姿；非自驱动门','configuration','evaluator',{'angle_rad':1.2},['door.angle_rad']),
 'cap.set_lift':(obj({'lift_m':number('m',0,.07)}),obj({'lift_m':number('m',0,.07)}),['vessel_cap'],'宿主闭合件教学位姿；真实旋拧/密封未模拟','configuration','evaluator',{'lift_m':.07},['cap.lift_m']),
 'sample.configure':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['sample_basket','vessel'],'100mL容器内单稀溶质+惰性多孔床；原始物质真值仅宿主','configuration','evaluator',SAMPLE_EXAMPLE,['sample.configured']),
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['pump_housing','heater','restrictor'],'开关电；断电不自动放空，残压保留','device_command','agent',{'enabled':True},['power.enabled']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['vessel','static_dynamic_valve','pump_head_0','restrictor'],'开始/取消续作教学升温加压静动放空序列','device_command','agent',{},['cycle.phase','cycle.stage']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['pump_head_0','static_dynamic_valve'],'停止模型泵/阀，保留残压和部分萃取效果','device_command','agent',{},['cycle.phase']),
 'pressure.vent':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['static_dynamic_valve','restrictor','vent_port'],'宿主受控减压回收；不是急停或安全释放保证','device_command','agent',{},['cycle.phase','cycle.stage']),
 'extraction.measure':(obj({}),RESULT,['display_digits_temperature','display_digits_restrictor','display_digits_pressure','gas_flowmeter'],'模型派生温压流量与文献平衡参考，不是实测产率','query','agent',{},[]),
 'product.read':(obj({}),PRODUCT,['collection_vial','vessel','restrictor'],'萃取物/残余/溶解/沉积真值；非真实回收标定','query','evaluator',{},[]),
 'history.read':(obj({}),obj({'records':arr(POINT),'evidence':TEXT}),['display_digits_pressure'],'教学程序过程曲线','query','agent',{},[]),
 'reference.lookup':(obj({'substance_id':TEXT,'temperature_k':number('K',0),'pressure_abs_pa':number('Pa',0)}),REF,['vessel'],'60物质313.15K实验等温线内插；无外推','query','agent',{'substance_id':SAMPLE_EXAMPLE['substance_id'],'temperature_k':313.15,'pressure_abs_pa':20e6},[])}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,zh,o='derived',v='agent',c='display_digits_pressure',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=zh
for k,s,u,zh in [('power.enabled',BOOL,'1','主电源状态'),('plant.configured',BOOL,'1','宿主供液/床与系数是否配置'),('method.configured',BOOL,'1','方法是否配置'),('sample.configured',BOOL,'1','样品是否已装入'),('cycle.phase',PHASE,'1','运行状态'),('cycle.stage',TEXT,'1','顺序器当前阶段'),('cycle.fault',TEXT,'1','最近过程故障'),('cycle.elapsed_s',number('s',0),'s','累计运行时间'),('thermal.target_k',number('K',0),'K','烘箱目标温度'),('thermal.temperature_k',number('K',0),'K','集中参数容器温度'),('restrictor.temperature_k',number('K',0),'K','集中参数限流器温度'),('pressure.target_abs_pa',number('Pa',0),'Pa','方法腔内目标绝压'),('pressure.indicated_gauge_pa',number('Pa',0),'Pa','理想显示表压，相对宿主环境压'),('pressure.bed_drop_pa',number('Pa',0),'Pa','Ergun样品床压差，不是表压'),('flow.liquid_inlet_m3_s',number('m^3/s',0),'m^3/s','入口CO2在宿主供液温压的实际液体积流量'),('flow.expanded_reference_m3_s',number('m^3/s',0),'m^3/s','排气参考体积流量，参考273.15K/101325Pa'),('reference.solubility_y',nullable(number('1',0,1)),'1','方法条件二元平衡溶解度参考；无样品为null')]:slot(k,s,u,zh,o='setpoint' if k in ['thermal.target_k','pressure.target_abs_pa'] else 'derived')
for k,s,u,zh,c in [('door.angle_rad',number('rad',0,1.5),'rad','宿主手动烘箱门角','door'),('cap.lift_m',number('m',0,.07),'m','宿主高压闭合件教学升程','vessel_cap'),('valve.angle_rad',number('rad',0,math.pi/2),'rad','静动态阀教学角，运行程序驱动','static_dynamic_valve')]:slot(k,s,u,zh,o='scene' if k!='valve.angle_rad' else 'derived',c=c)
for group,places in [('co2',['initial','tank','chamber','exhaust']),('solute',['initial','residue','dissolved','collected','deposit'])]:
 for place in places:slot('truth.'+place+'_'+group+'_kg',number('kg',0),'kg',dict(initial='初始总计',tank='供液罐中',chamber='容器中',exhaust='累计排出',residue='残余样品中',dissolved='CO2溶解相中',collected='已收集中',deposit='限流器沉积中')[place]+dict(co2='CO2质量',solute='溶质质量')[group],o='process_truth',v='evaluator',c='vessel')
slot('truth.inert_matrix_kg',number('kg',0),'kg','不反应惰性基质质量',o='process_truth',v='evaluator',c='sample_basket')
class SftSft110Runtime:
 def __init__(self,instance_id='sft110_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.plant=None;self.method=None;self.sample=None;self.powered=False;self.phase='idle';self.stage='standby';self.fault='';self.elapsed_s=0.;self.stage_elapsed=0.;self.operation_id=None;self._operation_count=0;self.door=0.;self.cap=0.;self.temperature=293.15;self.restrictor_temperature=293.15;self.pressure=101325.;self.tank=0.;self.mco2=0.;self.initial_chamber=0.;self.exhaust=0.;self.residue=0.;self.dissolved=0.;self.collected=0.;self.deposit=0.;self.qin=0.;self.qgas=0.;self.drop=0.;self.ysat=None;self.history=[];self.next_record=0.;self.templates={x['substance_id']:x for x in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._eos=None
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return SftSft110Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _ambient(self):return self.plant['ambient_pa'] if self.plant else 101325.
 def _free(self):return VMAX-self.plant['bed_area_m2']*self.plant['bed_length_m']*(1-self.plant['porosity']) if self.plant else VMAX
 def _update_pressure(self):
  if self._eos is None:self._eos=cp().AbstractState('HEOS','CarbonDioxide')
  self._eos.update(cp().DmassT_INPUTS,max(1e-8,self.mco2/self._free()),self.temperature);self.pressure=max(self._ambient(),self._eos.p())
 def _valve_angle(self):return math.pi/2 if self.phase=='running' and self.stage in ['dynamic','venting'] else 0.
 def _values(self):
  v={'power.enabled':self.powered,'plant.configured':bool(self.plant),'method.configured':bool(self.method),'sample.configured':bool(self.sample),'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.fault':self.fault,'cycle.elapsed_s':self.elapsed_s,'thermal.target_k':self.method['temperature_k'] if self.method else 293.15,'thermal.temperature_k':self.temperature,'restrictor.temperature_k':self.restrictor_temperature,'pressure.target_abs_pa':self.method['pressure_gauge_pa']+self._ambient() if self.method else self._ambient(),'pressure.indicated_gauge_pa':max(0,self.pressure-self._ambient()),'pressure.bed_drop_pa':self.drop,'flow.liquid_inlet_m3_s':self.qin,'flow.expanded_reference_m3_s':self.qgas,'reference.solubility_y':self.ysat,'door.angle_rad':self.door,'cap.lift_m':self.cap,'valve.angle_rad':self._valve_angle(),'truth.initial_co2_kg':(self.plant['tank_mass_kg'] if self.plant else 0.)+self.initial_chamber,'truth.tank_co2_kg':self.tank,'truth.chamber_co2_kg':self.mco2,'truth.exhaust_co2_kg':self.exhaust,'truth.initial_solute_kg':self.sample['solute_mass_kg'] if self.sample else 0.,'truth.residue_solute_kg':self.residue,'truth.dissolved_solute_kg':self.dissolved,'truth.collected_solute_kg':self.collected,'truth.deposit_solute_kg':self.deposit,'truth.inert_matrix_kg':self.sample['inert_matrix_mass_kg'] if self.sample else 0.};return v
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('evaluator','debug')})
 def debug_snapshot(self):return copy.deepcopy(dict(state=self.read('evaluator'),method=self.method,sample=self.sample,stage_elapsed=self.stage_elapsed,time_s=self.time_s))
 def _reference(self,sid,t,p):
  r=self.templates.get(sid)
  if r is None:raise ActionRejected('TEMPLATE_RANGE_UNAVAILABLE')
  points=r['values']['solubility_curve']
  if abs(t-r['conditions']['temperature_k'])>1e-6 or p<points[0]['pressure_abs_pa'] or p>points[-1]['pressure_abs_pa']:raise ActionRejected('TEMPLATE_RANGE_UNAVAILABLE')
  for a,b in zip(points,points[1:]):
   if a['pressure_abs_pa']<=p<=b['pressure_abs_pa']:
    f=(p-a['pressure_abs_pa'])/(b['pressure_abs_pa']-a['pressure_abs_pa']);y=math.exp((1-f)*math.log(a['mole_fraction'])+f*math.log(b['mole_fraction']));break
  return dict(substance_id=sid,mole_fraction=y,temperature_k=t,pressure_abs_pa=p,evidence='derived_log_linear_interpolation_of_curated_measured_binary_solubility_not_extraction_recovery',source=r['source'],original_literature=r['original_literature'],template_source=TEMPLATE_REL)
 def _handling(self):return self.phase!='running' and self.pressure-self._ambient()<=self.plant['opening_pressure_margin_pa'] and self.temperature<=self.plant['handling_temperature_limit_k']
 def _bed_drop(self,t,p,q):
  rho,mu=eos_tp(t,p);v=q/rho/self.plant['bed_area_m2'];ep=self.plant['porosity'];d=self.plant['particle_diameter_m'];return self.plant['bed_length_m']*(150*mu*(1-ep)**2/ep**3/d**2*v+1.75*rho*(1-ep)/ep**3/d*v*v)
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if self.phase=='running' and CAPS[capability][4]!='query' and capability not in ('acquisition.stop','power.set'):raise ActionRejected('BUSY')
  if capability=='plant.configure':
   if self.plant or not all(a[k] for k in ['dip_tube_liquid_confirmed','exhaust_connected','compatible_dry_sample_reviewed','initial_co2_purge_confirmed','emulated_manual_sequence_confirmed']) or not a['coefficient_evidence'] or a['bed_area_m2']*a['bed_length_m']>=VMAX:raise ActionRejected('PRECONDITION_FAILED')
   if cp().PhaseSI('T',a['supply_temperature_k'],'P',a['supply_pressure_abs_pa'],'CO2')!='liquid':raise ActionRejected('LIQUID_SUPPLY_REQUIRED')
   self.plant=copy.deepcopy(a);self.temperature=a['ambient_k'];self.restrictor_temperature=a['ambient_k'];self.tank=a['tank_mass_kg'];self.initial_chamber=eos_tp(self.temperature,a['ambient_pa'])[0]*self._free();self.mco2=self.initial_chamber;self.pressure=a['ambient_pa'];out={'configured':True}
  elif capability=='method.configure':
   if not self.plant or self.sample or a['temperature_k']<self.plant['ambient_k'] or a['restrictor_temperature_k']<self.plant['ambient_k'] or a['static_s']+a['dynamic_s']<=0:raise ActionRejected('PRECONDITION_FAILED')
   p=a['pressure_gauge_pa']+self._ambient();rho=eos_tp(self.plant['supply_temperature_k'],self.plant['supply_pressure_abs_pa'])[0];drop=self._bed_drop(a['temperature_k'],p,a['liquid_flow_m3_s']*rho)
   if a['pressure_gauge_pa']+drop>PMAX:raise ActionRejected('PUMP_PRESSURE_ENVELOPE')
   self.method=copy.deepcopy(a);out={'configured':True}
  elif capability=='door.set_angle':
   if not self.plant or not self._handling() or (a['angle_rad']<.1 and self.cap>1e-5):raise ActionRejected('PRECONDITION_FAILED')
   self.door=a['angle_rad'];out={'angle_rad':self.door}
  elif capability=='cap.set_lift':
   if not self.plant or not self._handling() or self.door<1.:raise ActionRejected('PRECONDITION_FAILED')
   self.cap=a['lift_m'];out={'lift_m':self.cap}
  elif capability=='sample.configure':
   if not self.method or self.sample or self.door<1. or self.cap<.06 or not self._handling() or not a['sample_id'] or not a['single_dilute_nonreactive_solute_confirmed']:raise ActionRejected('PRECONDITION_FAILED')
   ref=self._reference(a['substance_id'],self.method['temperature_k'],self.method['pressure_gauge_pa']+self._ambient());self.sample=copy.deepcopy(a);self.residue=a['solute_mass_kg'];self.ysat=ref['mole_fraction'];out=dict(configured=True,attachment_event='host_attach:'+a['sample_id'])
  elif capability=='power.set':
   if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
   self.powered=a['enabled']
   if not self.powered and self.phase=='running':self.phase='cancelled';self.qin=self.qgas=self.drop=0.
   out={'enabled':self.powered}
  elif capability in ['acquisition.start','pressure.vent']:
   if not self.powered or not self.sample or self.door>1e-5 or self.cap>1e-5:raise ActionRejected('PRECONDITION_FAILED')
   if capability=='acquisition.start' and self.phase not in ['idle','cancelled']:raise ActionRejected('PRECONDITION_FAILED')
   if capability=='pressure.vent' and (self.phase=='running' or self.pressure<=self._ambient()+1):raise ActionRejected('PRECONDITION_FAILED')
   if capability=='pressure.vent':self.stage='venting';self.stage_elapsed=0.;self.fault=''
   elif self.phase=='idle':self.stage='conditioning';self.stage_elapsed=0.
   elif self.stage!='venting' and abs(self.temperature-self.method['temperature_k'])>.1:raise ActionRejected('RECONDITION_REQUIRED')
   self.phase='running';self._operation_count+=1;self.operation_id=f'{self.instance_id}:op{self._operation_count}';out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('PRECONDITION_FAILED')
   self.phase='cancelled';self.qin=self.qgas=self.drop=0.;out={'phase':self.phase}
  elif capability=='extraction.measure':out=dict(phase=self.phase,stage=self.stage,fault=self.fault,temperature_k=self.temperature,restrictor_temperature_k=self.restrictor_temperature,pressure_gauge_pa=max(0,self.pressure-self._ambient()),liquid_inlet_m3_s=self.qin,expanded_reference_m3_s=self.qgas,reference_mole_fraction=self.ysat,evidence='pure_CO2_reference_EOS_with_measured_solubility_and_synthetic_transport_capture',template_source=TEMPLATE_REL)
  elif capability=='product.read':out=dict(substance_id=self.sample['substance_id'] if self.sample else None,collected_kg=self.collected,residue_kg=self.residue,dissolved_kg=self.dissolved,restrictor_deposit_kg=self.deposit,inert_matrix_kg=self.sample['inert_matrix_mass_kg'] if self.sample else 0.,measured_SFT110_recovery=None,evidence='conditional_conservative_model_not_measured_SFT110_recovery',template_source=TEMPLATE_REL)
  elif capability=='history.read':out=dict(records=copy.deepcopy(self.history),evidence='synthetic_process_curve')
  elif capability=='reference.lookup':out=self._reference(a['substance_id'],a['temperature_k'],a['pressure_abs_pa'])
  else:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][1],out)
  if CAPS[capability][4]!='query':self.revision+=1
  return out
 def _fail(self,code):self.phase='failed';self.fault=code;self.qin=self.qgas=self.drop=0.
 def _next(self,stage):self.stage=stage;self.stage_elapsed=0.
 def _transfer(self,dt):
  # Only at the reference isotherm; warmup and depressurization are separate regimes.
  ref=self._reference(self.sample['substance_id'],self.temperature,self.pressure);y=ref['mole_fraction'];mw=self.templates[self.sample['substance_id']]['molar_mass_kg_mol'];cap=self.mco2/MWCO2*y/(1-y)*mw;want=(cap-self.dissolved)*(1-math.exp(-self.plant['transfer_rate_s_inv']*dt));dm=min(self.residue,want) if want>=0 else max(-self.dissolved,want);self.residue-=dm;self.dissolved+=dm
 def _outflow(self,dm,dt):
  dm=max(0.,min(dm,self.mco2));solute=self.dissolved*dm/max(self.mco2,1e-30);captured=solute*self.plant['capture_fraction'];deposited=solute-captured
  if self.collected+captured>self.plant['collection_capacity_kg']:self._fail('COLLECTION_FULL');return False
  if self.deposit+deposited>self.plant['deposit_limit_kg']:self._fail('RESTRICTOR_DEPOSITION_LIMIT');return False
  self.dissolved-=solute;self.collected+=captured;self.deposit+=deposited;self.mco2-=dm;self.exhaust+=dm;self.qgas=dm/dt/eos_tp(273.15,101325.)[0];return True
 def _tick(self,dt):
  self.qin=self.qgas=self.drop=0.
  if not self.plant:return
  target=self.method['temperature_k'] if self.powered and self.method else self.plant['ambient_k'];rtarget=self.method['restrictor_temperature_k'] if self.powered and self.method else self.plant['ambient_k'];tau=self.plant['thermal_tau_s'] if self.powered else self.plant['cooling_tau_s'];self.temperature=target+(self.temperature-target)*math.exp(-dt/tau);self.restrictor_temperature=rtarget+(self.restrictor_temperature-rtarget)*math.exp(-dt/(self.plant['restrictor_tau_s'] if self.powered else self.plant['cooling_tau_s']));self._update_pressure()
  if self.pressure-self._ambient()>PMAX+1:self._fail('OVERPRESSURE_MODEL_GUARD');return
  if self.phase!='running':return
  if self.stage!='venting' and self.elapsed_s>=self.method['timeout_s']:self._fail('TIMEOUT');return
  self.elapsed_s+=dt;self.stage_elapsed+=dt
  if self.stage=='conditioning':
   if abs(self.temperature-target)<.01 and abs(self.restrictor_temperature-rtarget)<.1:self.temperature=target;self.restrictor_temperature=rtarget;self._update_pressure();self._next('pressurizing')
  elif self.stage=='pressurizing':
   wanted=eos_tp(target,self.method['pressure_gauge_pa']+self._ambient())[0]*self._free();rho=eos_tp(self.plant['supply_temperature_k'],self.plant['supply_pressure_abs_pa'])[0];dm=min(max(0,wanted-self.mco2),24e-6/60*rho*dt)
   if dm>self.tank+1e-15:self._fail('CO2_SUPPLY_EMPTY');return
   self.tank-=dm;self.mco2+=dm;self.qin=dm/rho/dt;self._update_pressure()
   if abs(self.mco2-wanted)<1e-12:self._next('static' if self.method['static_s']>0 else 'dynamic')
  elif self.stage in ['static','dynamic']:
   if abs(self.temperature-target)>1e-6:self._fail('REFERENCE_ISOTHERM_REQUIRED');return
   if self.stage=='dynamic':
    rho=eos_tp(self.plant['supply_temperature_k'],self.plant['supply_pressure_abs_pa'])[0];dm=self.method['liquid_flow_m3_s']*rho*dt;dm=min(dm,.02*self.mco2);self.drop=self._bed_drop(self.temperature,self.pressure,dm/dt)
    if self.pressure-self._ambient()+self.drop>PMAX:self._fail('PUMP_PRESSURE_ENVELOPE');return
    if dm>self.tank:self._fail('CO2_SUPPLY_EMPTY');return
    # Transfer and well-mixed release are bounded and separately conserved.
    self._transfer(dt)
    if not self._outflow(dm,dt):return
    self.tank-=dm;self.mco2+=dm;self.qin=dm/rho/dt;self._update_pressure()
   else:self._transfer(dt)
   if self.stage=='static' and self.stage_elapsed>=self.method['static_s']:self._next('dynamic' if self.method['dynamic_s']>0 else 'venting')
   elif self.stage=='dynamic' and self.stage_elapsed>=self.method['dynamic_s']:self._next('venting')
  elif self.stage=='venting':
   newp=max(self._ambient(),self.pressure-self.plant['vent_rate_pa_s']*dt);targetmass=eos_tp(self.temperature,newp)[0]*self._free();dm=min(max(0,self.mco2-targetmass),self.plant['vent_mass_limit_kg_s']*dt)
   if not self._outflow(dm,dt):return
   self._update_pressure()
   if self.pressure<=self._ambient()+1:
    # Residual dissolved solute precipitates back into bed; an explicit uncalibrated limit.
    self.residue+=self.dissolved;self.dissolved=0.;self.phase='succeeded';self.stage='depressurized';self.qin=self.qgas=self.drop=0.
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<0:raise ActionRejected('INVALID_ARGUMENT')
  if dt_s==0:return
  remaining=float(dt_s)
  while remaining>1e-12:
   dt=min(.5,remaining)
   if self.phase=='running' and self.stage in ['static','dynamic']:
    duration=self.method['static_s'] if self.stage=='static' else self.method['dynamic_s']
    if duration<=self.stage_elapsed:self._next('dynamic' if self.stage=='static' and self.method['dynamic_s']>0 else 'venting')
    else:dt=min(dt,duration-self.stage_elapsed)
   self._tick(dt);self.time_s+=dt;remaining-=dt
   if self.time_s>=self.next_record:
    self.history.append(dict(time_s=self.time_s,temperature_k=self.temperature,pressure_gauge_pa=max(0,self.pressure-self._ambient()),liquid_inlet_m3_s=self.qin,stage=self.stage));self.next_record=self.time_s+10.
  self.revision+=1
class SftSft110Session:
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
        meta=export_sft_sft110_contract_metadata()
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('extraction.measure'))

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])

def export_sft_sft110_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'SftSft110Session.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key in ['acquisition.start','pressure.vent'];pre=[]
        premap={
          'plant.configure':('仅首次，液态CO2/排风/兼容性/预置换/教学程序均确认',['plant.configured'],'not configured and all_confirmations and source_phase_liquid'),
          'method.configure':('已配边界且未装样，目标符合温压流量和床压降范围',['plant.configured','sample.configured'],'plant and no_sample and within_envelope'),
          'sample.configure':('首次且门/闭合件打开，冷却降压；选定单物质在等温线内',['door.angle_rad','cap.lift_m','sample.configured','method.configured'],'open and cold and depressurized and one_valid_reference'),
          'door.set_angle':('停止、已降压且在宿主取放温度内，盖未升起才能关门',['cycle.phase','pressure.indicated_gauge_pa','thermal.temperature_k','cap.lift_m'],'not_running and safe_handling and no_cap_interference'),
          'cap.set_lift':('门开，停止、降压及宿主温度域内',['door.angle_rad','cycle.phase','pressure.indicated_gauge_pa','thermal.temperature_k'],'door>=1 and not_running and safe_handling'),
          'acquisition.start':('通电、样品就绪、门和闭合件关；首次或可续作',['power.enabled','sample.configured','door.angle_rad','cap.lift_m','cycle.phase'],'powered and loaded and closed and resumable'),
          'pressure.vent':('有样品、通电、关闭门盖、有残压且未运行',['power.enabled','sample.configured','door.angle_rad','cap.lift_m','cycle.phase','pressure.indicated_gauge_pa'],'powered and loaded and closed and not_running and positive_pressure')}
        if key in premap:
            text,states,expr=premap[key];pre=[dict(description_zh=text,state_ids=states,enforced_by='runtime',check=expr)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.sft_sft110.SftSft110Session.invoke',invocation=f'session.invoke(instance_id="sft110_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded且降压到环境；不保证取出回收或安全认证' if async_ else '同步更新宿主配置/命令或查询，不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='参数拒绝不改状态。取消/断电关闭模型泵阀，保留残压/溶解/收集/沉积；热松弛继续。物理故障保留已发生传质。不是紧急释放或安全认证；去重仅当前进程'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    evolving=list(slots)
    for key in ['acquisition.start','pressure.vent','acquisition.stop','power.set','plant.configure','sample.configure']:
        caps[key]['state_effects'] += [dict(state_id=k,timing='on_step' if key in ['acquisition.start','pressure.vent'] else 'immediate',description_zh='有限CO2/溶质账本、热松弛、残压与教学阀位') for k in evolving if k not in [x['state_id'] for x in caps[key]['state_effects']]]
    for key in ['sample.configure','reference.lookup']:caps[key]['errors'].append(dict(code='TEMPLATE_RANGE_UNAVAILABLE',meaning_zh='物质或温压在测得等温线之外，拒绝外推',side_effects='none'))
    for key,code in [('plant.configure','LIQUID_SUPPLY_REQUIRED'),('method.configure','PUMP_PRESSURE_ENVELOPE'),('acquisition.start','RECONDITION_REQUIRED')]:caps[key]['errors'].append(dict(code=code,meaning_zh=code,side_effects='none'))
    for key in ['acquisition.start','pressure.vent']:caps[key]['errors'] += [dict(code=x,meaning_zh=x,side_effects='partial') for x in ['TIMEOUT','CO2_SUPPLY_EMPTY','COLLECTION_FULL','RESTRICTOR_DEPOSITION_LIMIT','OVERPRESSURE_MODEL_GUARD','REFERENCE_ISOTHERM_REQUIRED']]
    mechanism=dict(component_ids=['vessel','sample_basket','pump_head_0','heater','static_dynamic_valve','restrictor','collection_vial'],state_ids=evolving,meaning_zh='单100mL容器、纯CO2、单稀溶质实测等温平衡与条件萃取动力学',implementation='lumped',equations=['Vfree=100mL-(1-porosity)*bed_area*bed_length','pureCO2 EOS Span-Wagner1996 via CoolProp7.2.0;P=f(T,mCO2/Vfree)','T_next=Ttarget+(T-Ttarget)*exp(-dt/host_tau);no full energybalance orJTcooling','source massflow=Qliquid_actual_at_supply_TP*rhoCO2(supply_TP);not standard gas or chambervolume','heat beforepressurization;clampT to exacttarget within0.01K;fill to EOSmass at targetpressure with24mL/minpumpmaximum','single measured313.15Kisotherm:ln(y)piecewise-linear inabsolute pressure;noextrapolation','equilibrium_dissolved_mass=mCO2/MWCO2*y/(1-y)*MWsolute','transfer=(equilibrium-dissolved)*(1-exp(-host_kdt)),boundedbyavailable residue/dissolved','Ergun dP=L*(150mu(1-epsilon)^2/(epsilon^3dp^2)*v+1.75rho(1-epsilon)/(epsilon^3dp)*v^2)','dynamic wellmixed outsolute=dissolved*dmCO2/mCO2;source replacesCO2;perstep outflow<=2%stock','outsolute→hostcapture_fraction*collected+(1-capture)*restrictordeposit;finitecapacities','vent pressure trajectory hostPa/s mapped throughisothermalEOS,limitedbyhostmassrate;remainingdissolvedprecipitatesintoresidueatambient','initialCO2=tank+chamber+exhaust;initialsolute=residue+dissolved+collected+deposit;inertmatrixconstant'],parameters={},source_ids=['manufacturer_primary','manufacturer_faq','solubility_dataset','co2_eos','simulation_assumption'],limitations=['60unique structures/406curated measuredsolubility points at313.15K,not60SFT110measuredextractionyieldcurves.Originalreferencesretained,notallindependentlyreread.','Only selected100mLvessel;5/10/25/50mLvariantsdocumentedbutgeometry/operationnotimplemented.','OnebinarydilutesoluteinCO2,notmixtures,modifierdoping,reactivechemistryorrealnatural-productmatrices.','PureCO2 EOS neglectssolutevolume/nonideality;warmupandpressurizationdissolutionnotadvanced;selectedcurvevalidonlyinitsownT/Pdomain.','Hostsequence emulatesmanualheating,static/dynamicvalvesandventing;notfactoryautomaticprogramorvalidatedoperatingprocedure.','Thermal/transfer/Ergunbed/capture/depositionareuncalibrated;noenergybalance,JouleThomsonfreezing,cloggeometry,particlecarryoverorCO2cylinderthermalmodel.','Flowismassconserving;requestedliquidflowmaybelimitedto2%chamberstockper0.5sstep,thisisnumericalnotfactoryflowcontrol.','Canceltrapsresidualpressureandpreservesmass;poststopdiffusionnotadvanced,thermalrelaxationcontinues;noemergencysafetyrelease.','Controlledventignoreslowpressureequilibriumcurve;dissolvedcarryoverisbasedonwellmixingandfinalresidualprecipitatesbackintobed,uncalibrated.','Manufacturer±0.5Kand±2%arepublishedspecificationsnotvalidatedmodelaccuracy;LEDroundingnotrealcalibratedsensors.','Externalcollectionvialproxy30mLbutmodelcapacityishostsolute-massbudget,notdensity-basedvolumeoverflow.','Manualdoor/capandstaticdynamicvalveteachingjoints;noindividualthreads,torque,sealcontactorrobotconstraints.','Localmass/inertia/frictionunknown;URDF0.1kgproxy.28kgrootselectedwholeunitnotallocatedtocomponents.','Rupturedisk/alarmsarephysicaldeclarationsonly;nohardwareconnection,safetycertificationorfullfactorymenus.','Gasflowmeter0–35SLPMdocumented;referencegas273.15K/101325Paisexplicitassumptionbecausemanufacturerstandardconditionsunspecified.'])
    spec=importlib.util.spec_from_file_location('_sft110_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann)
    ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
    ann.PARAM_ZH.update({'supply_temperature_k':'泵入口液态CO2参考温度','supply_pressure_abs_pa':'泵入口液态CO2绝压','restrictor_tau_s':'限流器热时间常数','cooling_tau_s':'断电自然冷却时间常数','transfer_rate_s_inv':'残余相到CO2的条件传质速率','bed_area_m2':'样品床截面积','bed_length_m':'样品床长度','capture_fraction':'减压出口溶质捕集分率','deposit_limit_kg':'限流器沉积故障阈值','collection_capacity_kg':'收集溶质质量容量','vent_rate_pa_s':'受控放空目标降压速率','vent_mass_limit_kg_s':'模型排气质量流率上限','handling_temperature_limit_k':'宿主取放温度阈值','opening_pressure_margin_pa':'宿主开闭件允许表压余量','compatible_dry_sample_reviewed':'干燥惰性床兼容性确认','initial_co2_purge_confirmed':'初始空气已置换确认','emulated_manual_sequence_confirmed':'接受教学手动阀序列代理确认'})
    slots['pressure.bed_drop_pa']['reference_conditions']='样品床前后压差，不是表压。';slots['flow.liquid_inlet_m3_s']['reference_conditions']='CO2在plant.supply_temperature_k和supply_pressure_abs_pa下的入口实际液体体积；非腔内体积。';slots['flow.expanded_reference_m3_s']['reference_conditions']='CO2排气在273.15K/101325Pa的参考体积；厂家SLPM基准未说明，本基准为显式假设。'
    ann.annotate_export(SLUG,caps,slots,{'supercritical_extraction':mechanism},inputs={'plant.configure':(PLANT,EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'supercritical_extraction':mechanism})

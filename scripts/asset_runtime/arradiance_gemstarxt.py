"""XT-SP finite-resource ALD teaching model; not an original controller or measured process calibration."""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];SLUG='arradiance_gemstarxt';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-064';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/arradiance_gemstarxt_ald_library/templates.json'
R=8.31446261815324;STD_T=273.15;STD_P=101325.;CARRIER_MAX=200e-6/60
ELEMENTS=['Al','Zn','Ti','C','H','O','Ar','N']
ATOMS={'TMA':[1,0,0,3,9,0,0,0],'DEZ':[0,1,0,4,10,0,0,0],'TTIP':[0,0,1,12,28,4,0,0],'H2O':[0,0,0,0,2,1,0,0],'O2':[0,0,0,0,0,2,0,0],'AR':[0,0,0,0,0,0,1,0],'N2':[0,0,0,0,0,0,0,2],'CH4':[0,0,0,1,4,0,0,0],'C2H6':[0,0,0,2,6,0,0,0],'IPA':[0,0,0,3,8,1,0,0],'CO2':[0,0,0,1,0,2,0,0],'Al2O3':[2,0,0,0,0,3,0,0],'ZnO':[0,1,0,0,0,1,0,0],'TiO2':[0,0,1,0,0,2,0,0]}
GASES=list(ATOMS)[:-3];FILMS=['Al2O3','ZnO','TiO2'];MASS={'Al2O3':.101960077,'ZnO':.081379,'TiO2':.079865}
LAWS={'alumina_tma_water':dict(a='TMA',b='H2O',na=2.,nb=3.,film='Al2O3',products={'CH4':6.},plasma=False),
'zinc_dez_water':dict(a='DEZ',b='H2O',na=1.,nb=1.,film='ZnO',products={'C2H6':2.},plasma=False),
'titania_ttip_water':dict(a='TTIP',b='H2O',na=1.,nb=2.,film='TiO2',products={'IPA':4.},plasma=False),
'plasma_tma_oxygen':dict(a='TMA',b='O2',na=2.,nb=12.,film='Al2O3',products={'CO2':6.,'H2O':9.},plasma=True)}
BOOL={'type':'boolean'};TEXT={'type':'string'}
class ActionRejected(ValueError):pass
def number(unit,low=None,high=None):
 s={'type':'number','x-unit':unit}
 if low is not None:s['minimum']=low
 if high is not None:s['maximum']=high
 return s
def obj(p):return dict(type='object',properties=p,required=list(p),additionalProperties=False)
def arr(s,n=None):
 x=dict(type='array',items=s)
 if n is not None:x.update(minItems=n,maxItems=n)
 return x
def integer(low,high):return dict(number('1',low,high),type='integer')
def validate(s,v):
 t=s.get('type')
 if t in ('number','integer'):
  if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):raise ActionRejected('INVALID_ARGUMENT')
  if t=='integer' and type(v) is not int:raise ActionRejected('INVALID_ARGUMENT')
  if not s.get('minimum',-math.inf)<=v<=s.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='boolean' and type(v) is not bool:raise ActionRejected('INVALID_ARGUMENT')
 elif t=='string' and not isinstance(v,str):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='object':
  if not isinstance(v,dict) or not set(s['required'])<=v.keys():raise ActionRejected('INVALID_ARGUMENT')
  for k,x in v.items():
   if k not in s['properties']:raise ActionRejected('INVALID_ARGUMENT')
   validate(s['properties'][k],x)
 elif t=='array':
  if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
  for x in v:validate(s['items'],x)
 if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
GASMAP=obj({s:number('mol',0) for s in GASES});FILMMAP=obj({s:number('mol',0) for s in FILMS})
PLANT=obj({'ambient_k':number('K',273.15,313.15),'ambient_pa':number('Pa',90000,110000),'volume_m3':number('m^3',1e-4,.1),'pump_m3_s':number('m^3/s',1e-5,.1),'base_pa':number('Pa',.001,10),'heat_capacity_j_k':arr(number('J/K',100,1e6),3),'heater_max_w':arr(number('W',1,10000),3),'heat_loss_w_k':arr(number('W/K',.01,100),3),'door_coupling_w_k':number('W/K',0,100),'control_gain_w_k':number('W/K',.01,1000),'initial_k':arr(number('K',273.15,573.15),3),'initial_pa':number('Pa',.001,110000),'warm_snapshot':BOOL,'residual_limit_pa':number('Pa',1e-6,1),'purge_timeout_s':number('s',1,3600),'watchdog_s':number('s',.1,86400),'rf_heat_fraction':number('1',0,1),'vent_tau_s':number('s',.1,100),'accept_uncalibrated_model':BOOL})
PLANT_EXAMPLE=dict(ambient_k=298.15,ambient_pa=101325.,volume_m3=.003,pump_m3_s=.003,base_pa=.1,heat_capacity_j_k=[1500.,600.,400.],heater_max_w=[1400.,550.,350.],heat_loss_w_k=[.5,.2,.2],door_coupling_w_k=1.,control_gain_w_k=100.,initial_k=[298.15]*3,initial_pa=101325.,warm_snapshot=False,residual_limit_pa=.01,purge_timeout_s=120.,watchdog_s=3600.,rf_heat_fraction=.1,vent_tau_s=.5,accept_uncalibrated_model=True)
WARM_EXAMPLE=dict(PLANT_EXAMPLE,initial_k=[523.15,523.15,373.15],initial_pa=10.,warm_snapshot=True)
SUPPLY=obj({'bottle_species':arr(dict(type='string',enum=['EMPTY','TMA','H2O','DEZ','TTIP']),4),'bottle_mol':arr(number('mol',0,100),4),'carrier_mol':number('mol',0,1000),'oxygen_mol':number('mol',0,1000),'inlet_gauge_pa':number('Pa',10*6894.757293,20*6894.757293),'cda_gauge_pa':number('Pa',75*6894.757293,85*6894.757293),'accept_host_supply':BOOL})
SUPPLY_EXAMPLE=dict(bottle_species=['TMA','H2O','EMPTY','EMPTY'],bottle_mol=[.01,.03,0.,0.],carrier_mol=.1,oxygen_mol=.1,inlet_gauge_pa=15*6894.757293,cda_gauge_pa=80*6894.757293,accept_host_supply=True)
KINETICS=obj({'law_id':dict(type='string',enum=list(LAWS)),'density_kg_m3':number('kg/m^3',100,25000),'adsorption_pa_s_inv':number('1/(Pa*s)',1e-6,1e5),'conversion_pa_s_inv':number('1/(Pa*s)',1e-6,1e5),'activation_j_mol':number('J/mol',0,2e5),'plasma_site_mol_m2':number('mol/m^2',0,1),'accept_uncalibrated_model':BOOL})
KINETICS_EXAMPLE=dict(law_id='alumina_tma_water',density_kg_m3=3000.,adsorption_pa_s_inv=2.,conversion_pa_s_inv=2.,activation_j_mol=10000.,plasma_site_mol_m2=0.,accept_uncalibrated_model=True)
SAMPLE=obj({'sample_id':TEXT,'area_m2':number('m^2',1e-8,math.pi*.1**2),'accept_host_attachment':BOOL,'accept_initial_hot_snapshot':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='substrate_01',area_m2=.001,accept_host_attachment=True,accept_initial_hot_snapshot=False)
HOT_SAMPLE_EXAMPLE=dict(SAMPLE_EXAMPLE,accept_initial_hot_snapshot=True)
METHOD=obj({'cycles':integer(1,100000),'reactor_k':number('K',273.15,573.15),'door_k':number('K',273.15,573.15),'manifold_k':number('K',273.15,473.15),'pulse_s':arr(number('s',.001,100),2),'exposure_s':arr(number('s',0,600),2),'purge_s':arr(number('s',.001,3600),2),'precursor_mol_s':arr(number('mol/s',1e-12,.1),2),'carrier_std_m3_s':number('m^3/s',0,CARRIER_MAX),'rf_w':number('W',0,300)})
METHOD_EXAMPLE=dict(cycles=2,reactor_k=523.15,door_k=523.15,manifold_k=373.15,pulse_s=[.2,.2],exposure_s=[.5,.5],purge_s=[12.,12.],precursor_mol_s=[2e-4,3e-4],carrier_std_m3_s=100e-6/60,rf_w=0.)
MEASURE=obj({'phase':PHASE,'stage':TEXT,'cycle_completed':integer(0,100000),'temperature_k':arr(number('K',0),3),'pressure_pa':number('Pa',0),'rf_power_w':number('W',0,300),'elapsed_s':number('s',0),'warning':TEXT,'evidence':TEXT})
ESTIMATE=obj({'thickness_m':number('m',0),'material':TEXT,'evidence':TEXT,'template_source':TEXT,'measured':BOOL})
LEDGER=obj({'bottle_remaining_mol':arr(number('mol',0),4),'carrier_remaining_mol':number('mol',0),'oxygen_remaining_mol':number('mol',0),'chamber_mol':GASMAP,'exhaust_mol':GASMAP,'air_added_mol':GASMAP,'film_mol':FILMMAP,'collected_film_mol':FILMMAP,'adsorbed_formula_mol':number('mol',0),'collected_adsorbate_mol':GASMAP,'atom_residual_mol':arr(number('mol'),8),'site_capacity_mol':number('mol',0),'thickness_m':number('m',0),'initial_sensible_j':number('J'),'stored_sensible_j':number('J'),'heater_j':number('J',0),'rf_heat_j':number('J',0),'environment_j':number('J'),'thermal_residual_j':number('J'),'evidence':TEXT})
TEMPLATE=obj({'substance_id':TEXT,'material':TEXT,'gpc_m_cycle':number('m/cycle',0),'temperature_k':number('K',0),'source':TEXT,'evidence':TEXT})
REFERENCE=obj({'reference_id':TEXT,'material':TEXT,'article_doi':TEXT,'dataset':TEXT,'values_raw_json':TEXT,'evidence':TEXT,'runnable':BOOL})
CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},[])
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body'],'三热节点、有限腔体抽速及一次性初始化','configuration','evaluator',PLANT_EXAMPLE)
cap('gas.install',SUPPLY,obj({'installed':BOOL,'attachment_event':TEXT}),['precursor_port_0','precursor_port_1','precursor_port_2','precursor_port_3','carrier_inlet','plasma_port_1'],'四物料端口/共享载气RF0/可选氧气RF1有限源','configuration','evaluator',SUPPLY_EXAMPLE)
cap('kinetics.configure',KINETICS,obj({'configured':BOOL}),['reactor'],'逐前驱体化学计量与显式动力学；PEALD不复用thermalGPC','configuration','evaluator',KINETICS_EXAMPLE)
cap('sample.install',SAMPLE,obj({'loaded':BOOL,'attachment_event':TEXT}),['end_effector'],'200mm端件内宿主装样；热态仅初始化快照','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.collect',obj({'recipient_id':TEXT}),obj({'recipient_id':TEXT,'attachment_event':TEXT}),['end_effector'],'冷却回常压开门后卸样，保留膜和吸附中间体账本','device_command','evaluator',{'recipient_id':'holder'})
cap('method.configure',METHOD,obj({'configured':BOOL}),['laptop_screen'],'脉冲/暴露/清扫/循环和三个温控区；不瞬时推进','configuration','agent',METHOD_EXAMPLE)
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet'],'主电源；关闭气阀和加热，RF实际输出归零',ex={'enabled':True})
cap('rf.power_set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['rf_power_inlet'],'RF独立供电输入',ex={'enabled':True})
cap('door.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['door'],'安全模型仅允许冷态常压开门，拒绝无副作用',ex={'opened':False})
cap('safety.emo',obj({}),obj({'latched':BOOL}),['emo_port'],'EMO接口代理锁存停气停热，不是真实安全设备')
cap('safety.reset',obj({}),obj({'latched':BOOL}),['emo_port'],'双电源关闭后复位EMO代理')
cap('control.heartbeat',obj({}),obj({'acknowledged':BOOL}),['usb_port'],'显式宿主通信心跳，超时关闭内部阀/RF/热')
cap('vacuum.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['vacuum_outlet'],'外部宿主干泵边界，不内建真实泵',ex={'enabled':True})
cap('vacuum.vent',obj({}),obj({'venting':BOOL}),['vent_port'],'有限回气，过程停止后保留全部残留')
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['reactor','manifold','icp_head','laptop_screen'],'依次抽空/热稳定/A/清扫/B/清扫；异步分子账本')
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['laptop_screen'],'关闭进气/RF/热并保留不完全循环和膜')
cap('process.measure',obj({}),MEASURE,['laptop_screen','pressure_gauge'],'理想温压/循环日志，不返回实测膜厚','query')
cap('film.estimate',obj({}),ESTIMATE,['end_effector'],'带模型证据的推断厚度，未安装QCM','query')
cap('product.read',obj({}),LEDGER,['end_effector'],'私有有限物质/元素和显热账本','query','evaluator')
cap('template.read',obj({'substance_id':TEXT}),TEMPLATE,['laptop_screen'],'3种独立原始文献生长参考，不是本机标定','query','agent',{'substance_id':'alumina_tma_water'})
cap('reference.read',obj({'reference_id':TEXT}),REFERENCE,['laptop_screen'],'人工标注原始字段隔离查询，禁止作为已验证recipe','query','agent',{'reference_id':'gold_002'})
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='body',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
for k in ['plant','method','kinetics']:slot(k+'.configured',BOOL,'1',k+'配置','setpoint')
slot('power.enabled',BOOL,'1','主机电源','setpoint',w=('power.set','enabled'))
slot('rf.enabled',BOOL,'1','RF独立电源','setpoint',c='rf_supply',w=('rf.power_set','enabled'))
slot('door.opened',BOOL,'1','前门状态','scene',c='door',w=('door.set','opened'))
slot('door.angle_rad',number('rad',0,math.radians(100)),'rad','左铰链向前100°工程代理','scene',c='door')
for i,n in enumerate(['reactor','door','manifold']):
 slot(n+'.temperature_k',number('K',0),'K',n+'理想温度量化0.1K，不是标定实测',c=n)
 slot(n+'.setpoint_k',number('K',273.15,573.15 if i<2 else 473.15),'K',n+'温控设定','setpoint',c=n)
slot('vacuum.pressure_pa',number('Pa',0),'Pa','腔内绝压，混合气体模型',c='pressure_gauge')
slot('vacuum.enabled',BOOL,'1','宿主泵隔离请求','setpoint',c='vacuum_outlet',w=('vacuum.set','enabled'))
slot('vacuum.angle_rad',number('rad',0,math.pi/2),'rad','内部真空隔离阀角度代理','scene',c='vacuum_valve')
slot('carrier.flow_std_m3_s',number('m^3/s',0,CARRIER_MAX),'m^3/s','唯一载气流率，RF入口0共享而非另加一份',c='carrier_mfc')
slot('carrier.travel_m',number('m',0,.002),'m','载气内部NC阀代理行程','scene',c='carrier_valve')
for i in range(4):slot('precursor.valve_'+str(i)+'_m',number('m',0,.002),'m','第'+str(i+1)+'路高速NC阀代理','scene',c='precursor_valve_'+str(i))
slot('plasma.travel_m',number('m',0,.002),'m','RF用户氧气入口1内部NC阀代理','scene',c='plasma_valve')
slot('rf.power_w',number('W',0,300),'W','RF实际模型输出，仅B等离子暴露阶段',c='rf_supply')
slot('cycle.phase',PHASE,'1','操作生命周期')
slot('cycle.stage',TEXT,'1','抽空、稳定、A、清扫、B阶段')
slot('cycle.completed',integer(0,100000),'1','两次清扫完成的完整循环数')
slot('cycle.elapsed_s',number('s',0),'s','本操作宿主时间')
slot('safety.emo_latched',BOOL,'1','软件EMO锁存非认证互锁',c='emo_port')
slot('safety.warning',TEXT,'1','模型告警')
slot('truth.ledger',LEDGER,'1','元素、吸附、沉积、有限源、局部热模型私有真值','process_truth','evaluator','end_effector')
for k in CAPS:
 if CAPS[k][4]!='query':CAPS[k][7].extend(SLOTS)
class ArradianceGemstarxtRuntime:
 def __init__(self,instance_id='gemstarxt_01'):
  self.instance_id=instance_id;lib=json.loads((ROOT/TEMPLATE_REL).read_text());self.templates={r['substance_id']:r for r in lib['templates']};self.references={r['reference_id']:r for r in lib['curated_references']}
  self.time_s=0.;self.initialized_at_s=None;self.revision=0;self.plant=None;self.supply=None;self.kinetics=None;self.sample=None;self.method=None
  self.temp=[298.15]*3;self.target=list(self.temp);self.heater=[0.]*3;self.powered=False;self.rf_enabled=False;self.opened=True;self.pump=False;self.vent=False;self.emo=False;self.heartbeat_age=0.
  self.gas={s:0. for s in GASES};self.initial_gas=dict(self.gas);self.air=dict(self.gas);self.exhaust=dict(self.gas);self.collected_ads=dict(self.gas)
  self.remaining=[0.]*4;self.carrier=0.;self.oxygen=0.;self.film={s:0. for s in FILMS};self.collected=dict(self.film);self.adsorbed=0.;self.converted_cycle=0.;self.capacity=0.
  self.valves=[0.]*4;self.carrier_flow=0.;self.plasma_valve=0.;self.rf=0.;self.thermal_initial=0.;self.thermal_heater=0.;self.thermal_rf=0.;self.thermal_environment=0.
  self.phase='idle';self.stage='idle';self.elapsed_s=0.;self.stage_elapsed=0.;self.completed=0;self.warning='';self.operation_id='';self._operation_count=0;self.cycles_log=[];self.log=[]
  self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._sample_ids=set()
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return ArradianceGemstarxtSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in ['templates','references']})
 def _p(self,s=None):return (self.gas[s] if s else sum(self.gas.values()))*R*self.temp[0]/self.plant['volume_m3'] if self.plant else 0.
 def _stored(self):return sum(c*(t-self.plant['ambient_k']) for c,t in zip(self.plant['heat_capacity_j_k'],self.temp)) if self.plant else 0.
 def _thickness(self):
  if not self.sample or not self.kinetics:return 0.
  return sum(self.film[f]*MASS[f] for f in FILMS)/(self.sample['area_m2']*self.kinetics['density_kg_m3'])
 def _ledger(self):
  incoming={s:self.initial_gas[s]+self.air[s] for s in GASES};outgoing={s:self.gas[s]+self.exhaust[s]+self.collected_ads[s] for s in GASES}
  if self.supply:
   for i,s in enumerate(self.supply['bottle_species']):
    if s!='EMPTY':incoming[s]+=self.supply['bottle_mol'][i];outgoing[s]+=self.remaining[i]
   incoming['AR']+=self.supply['carrier_mol'];incoming['O2']+=self.supply['oxygen_mol'];outgoing['AR']+=self.carrier;outgoing['O2']+=self.oxygen
  if self.kinetics:
   law=LAWS[self.kinetics['law_id']];outgoing[law['a']]+=self.adsorbed*law['na']
  delta=[sum((incoming[s]-outgoing[s])*ATOMS[s][i] for s in GASES)-sum((self.film[f]+self.collected[f])*ATOMS[f][i] for f in FILMS) for i in range(8)]
  return dict(bottle_remaining_mol=list(self.remaining),carrier_remaining_mol=self.carrier,oxygen_remaining_mol=self.oxygen,chamber_mol=dict(self.gas),exhaust_mol=dict(self.exhaust),air_added_mol=dict(self.air),film_mol=dict(self.film),collected_film_mol=dict(self.collected),adsorbed_formula_mol=self.adsorbed,collected_adsorbate_mol=dict(self.collected_ads),atom_residual_mol=delta,site_capacity_mol=self.capacity,thickness_m=self._thickness(),initial_sensible_j=self.thermal_initial,stored_sensible_j=self._stored(),heater_j=self.thermal_heater,rf_heat_j=self.thermal_rf,environment_j=self.thermal_environment,thermal_residual_j=self.thermal_initial+self.thermal_heater+self.thermal_rf-self.thermal_environment-self._stored(),evidence='synthetic finite-species and lumped sensible-energy model; no reaction/flow enthalpy or hardware thickness validation')
 def read(self,visibility='agent'):
  if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
  v={'plant.configured':bool(self.plant),'method.configured':bool(self.method),'kinetics.configured':bool(self.kinetics),'power.enabled':self.powered,'rf.enabled':self.rf_enabled,'door.opened':self.opened,'door.angle_rad':math.radians(100) if self.opened else 0.,'vacuum.pressure_pa':self._p(),'vacuum.enabled':self.pump,'vacuum.angle_rad':math.pi/2 if self.pump and not self.opened else 0.,'carrier.flow_std_m3_s':self.carrier_flow,'carrier.travel_m':.002 if self.carrier_flow else 0.,'plasma.travel_m':self.plasma_valve,'rf.power_w':self.rf,'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.completed':self.completed,'cycle.elapsed_s':self.elapsed_s,'safety.emo_latched':self.emo,'safety.warning':self.warning,'truth.ledger':self._ledger()}
  for i,n in enumerate(['reactor','door','manifold']):v[n+'.temperature_k']=round(self.temp[i]-273.15,1)+273.15;v[n+'.setpoint_k']=self.target[i]
  for i in range(4):v['precursor.valve_'+str(i)+'_m']=self.valves[i]
  return copy.deepcopy({k:x for k,x in v.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def invoke(self,capability_id,**a):
  if capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability_id][0],a);old=self.debug_snapshot()
  try:
   r=self._invoke(capability_id,a);validate(CAPS[capability_id][1],r)
   if CAPS[capability_id][4]!='query':self.revision+=1;self.log.append(dict(time_s=self.time_s,event=capability_id,phase=self.phase,stage=self.stage))
   return copy.deepcopy(r)
  except Exception:
   t=self.templates;refs=self.references;self.__dict__.clear();self.__dict__.update(old);self.templates=t;self.references=refs;raise
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _stop(self,phase='cancelled',warning=''):
  if self.phase=='running':self.phase=phase;self.stage='finished'
  self.heater=[0.]*3;self.rf=0.;self.valves=[0.]*4;self.carrier_flow=0.;self.plasma_valve=0.
  if warning:self.warning=warning
  if warning in ['POWER_OFF','EMO_LATCHED','WATCHDOG_TIMEOUT']:self.pump=False
 def _cold(self):
  self._idle()
  if self.powered or self.rf_enabled or max(self.temp)>333.15 or not self.plant or abs(self._p()-self.plant['ambient_pa'])>self.plant['ambient_pa']*.01:raise ActionRejected('COLD_VENTED_ACCESS_REQUIRED')
 def _invoke(self,k,a):
  if k=='template.read':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   r=self.templates[a['substance_id']];return dict(substance_id=r['substance_id'],material=r['name'],gpc_m_cycle=r['value'],temperature_k=r['conditions']['temperature_k'],source=r['source']['url'],evidence=r['evidence'])
  if k=='reference.read':
   if a['reference_id'] not in self.references:raise ActionRejected('UNKNOWN_REFERENCE')
   r=self.references[a['reference_id']];return dict(reference_id=r['reference_id'],material=r['material'] or '',article_doi=r['source']['article_doi'] or '',dataset=r['source']['dataset'],values_raw_json=json.dumps(r['values'],ensure_ascii=False),evidence=r['evidence_detail'],runnable=False)
  if k=='product.read':return self._ledger()
  if k=='process.measure':return dict(phase=self.phase,stage=self.stage,cycle_completed=self.completed,temperature_k=[round(t-273.15,1)+273.15 for t in self.temp],pressure_pa=self._p(),rf_power_w=self.rf,elapsed_s=self.elapsed_s,warning=self.warning,evidence='ideal model telemetry, not measured instrument data')
  if k=='film.estimate':
   sid=self.kinetics['law_id'] if self.kinetics else ''
   return dict(thickness_m=round(self._thickness(),13),material=LAWS[sid]['film'] if sid else '',evidence='synthetic_lumped_surface_model_not_measured',template_source=self.templates[sid]['source']['url'] if sid in self.templates else 'host_assumed_plasma_site_capacity_no_measured_GPC',measured=False)
  if k=='plant.configure':
   if self.plant or not a['accept_uncalibrated_model']:raise ActionRejected('INITIALIZATION_ONLY')
   if a['initial_k'][2]>473.15 or a['initial_pa']<a['base_pa'] or sum(a['heater_max_w'])>2400.:raise ActionRejected('INVALID_ARGUMENT')
   if not a['warm_snapshot'] and (a['initial_k']!=[a['ambient_k']]*3 or a['initial_pa']!=a['ambient_pa']):raise ActionRejected('EXPLICIT_WARM_SNAPSHOT_REQUIRED')
   self.plant=copy.deepcopy(a);self.temp=list(a['initial_k']);self.target=list(self.temp);self.initialized_at_s=self.time_s;self.opened=not a['warm_snapshot']
   n=a['initial_pa']*a['volume_m3']/(R*self.temp[0]);self.gas['AR']=n if a['warm_snapshot'] else 0.;self.gas['N2']=0. if a['warm_snapshot'] else .79*n;self.gas['O2']=0. if a['warm_snapshot'] else .21*n;self.initial_gas=dict(self.gas);self.thermal_initial=self._stored();return {'configured':True}
  if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
  if k=='gas.install':
   self._idle()
   if self.supply or not a['accept_host_supply'] or self.time_s!=self.initialized_at_s:raise ActionRejected('INITIALIZATION_ONLY')
   nonempty=[s for s in a['bottle_species'] if s!='EMPTY']
   if len(set(nonempty))!=len(nonempty) or any((s=='EMPTY')!=(n==0.) for s,n in zip(a['bottle_species'],a['bottle_mol'])):raise ActionRejected('INVALID_ARGUMENT')
   self.supply=copy.deepcopy(a);self.remaining=list(a['bottle_mol']);self.carrier=a['carrier_mol'];self.oxygen=a['oxygen_mol'];return dict(installed=True,attachment_event='host.attach.finite_supplies')
  if k=='kinetics.configure':
   self._idle()
   if self.sample or self.adsorbed or not a['accept_uncalibrated_model']:raise ActionRejected('PRECONDITION_FAILED')
   if LAWS[a['law_id']]['plasma']!=(a['plasma_site_mol_m2']>0):raise ActionRejected('PLASMA_CAPACITY_REQUIRED')
   self.kinetics=copy.deepcopy(a);self.method=None;return {'configured':True}
  if k=='sample.install':
   self._idle()
   if not self.kinetics or self.sample or not a['accept_host_attachment'] or not a['sample_id'] or a['sample_id'] in self._sample_ids:raise ActionRejected('PRECONDITION_FAILED')
   warm=self.plant['warm_snapshot'] and a['accept_initial_hot_snapshot'] and self.time_s==self.initialized_at_s and not self.powered
   if not warm:
    self._cold()
    if not self.opened:raise ActionRejected('PRECONDITION_FAILED')
   self.sample=copy.deepcopy(a);self._sample_ids.add(a['sample_id']);sid=self.kinetics['law_id'];law=LAWS[sid]
   self.capacity=(self.kinetics['plasma_site_mol_m2']*a['area_m2'] if law['plasma'] else self.templates[sid]['value']*self.kinetics['density_kg_m3']*a['area_m2']/MASS[law['film']])
   return dict(loaded=True,attachment_event='host.attach.substrate')
  if k=='sample.collect':
   self._cold()
   if not self.opened or not self.sample or not a['recipient_id']:raise ActionRejected('PRECONDITION_FAILED')
   for f in FILMS:self.collected[f]+=self.film[f];self.film[f]=0.
   law=LAWS[self.kinetics['law_id']];self.collected_ads[law['a']]+=self.adsorbed*law['na'];self.adsorbed=0.;self.capacity=0.;self.sample=None
   return dict(recipient_id=a['recipient_id'],attachment_event='host.release.substrate_with_film_and_bound_precursor')
  if k=='method.configure':
   self._idle()
   if not self.kinetics:raise ActionRejected('PRECONDITION_FAILED')
   sid=self.kinetics['law_id'];plasma=LAWS[sid]['plasma']
   if plasma!=(a['rf_w']>0.):raise ActionRejected('PLASMA_POWER_REQUIRED')
   if not plasma and abs(a['reactor_k']-self.templates[sid]['conditions']['temperature_k'])>1.:raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
   self.method=copy.deepcopy(a);self.target=[a[n+'_k'] for n in ['reactor','door','manifold']];return {'configured':True}
  if k in ['power.set','rf.power_set']:
   if a['enabled'] and self.emo:raise ActionRejected('EMO_LATCHED')
   if k=='power.set':self.powered=a['enabled']
   else:self.rf_enabled=a['enabled']
   if not a['enabled']:self._stop(warning='POWER_OFF')
   self.heartbeat_age=0.;return {'enabled':a['enabled']}
  if k=='door.set':
   self._idle()
   if a['opened']:self._cold();self.pump=False
   self.opened=a['opened'];return {'opened':a['opened']}
  if k=='safety.emo':self.emo=True;self._stop(warning='EMO_LATCHED');return {'latched':True}
  if k=='safety.reset':
   if self.powered or self.rf_enabled:raise ActionRejected('PRECONDITION_FAILED')
   self.emo=False;self.warning='';return {'latched':False}
  if k=='control.heartbeat':self.heartbeat_age=0.;return {'acknowledged':True}
  if k=='vacuum.set':
   self._idle()
   if a['enabled'] and (self.opened or self.vent):raise ActionRejected('PRECONDITION_FAILED')
   self.pump=a['enabled'];return {'enabled':a['enabled']}
  if k=='vacuum.vent':
   self._idle();self._stop();self.pump=False;self.vent=True;return {'venting':True}
  if k=='acquisition.start':
   self._idle()
   if not self.powered or self.emo or self.opened or self.vent or not self.supply or not self.sample or not self.kinetics or not self.method:raise ActionRejected('PRECONDITION_FAILED')
   law=LAWS[self.kinetics['law_id']]
   if law['plasma'] and (not self.rf_enabled or not self.oxygen):raise ActionRejected('PRECONDITION_FAILED')
   for s in [law['a']]+([] if law['plasma'] else [law['b']]):
    if s not in self.supply['bottle_species'] or self.remaining[self.supply['bottle_species'].index(s)]<=0:raise ActionRejected('SOURCE_EMPTY')
   if self.method['carrier_std_m3_s']<=0 or self.carrier<=0:raise ActionRejected('SOURCE_EMPTY')
   self._operation_count+=1;self.operation_id=self.instance_id+':ald:'+str(self._operation_count);self.phase='running';self.stage='evacuate';self.elapsed_s=0.;self.stage_elapsed=0.;self.completed=0;self.converted_cycle=0.;self.warning='';self.pump=True;self.heartbeat_age=0.;return dict(phase=self.phase,operation_id=self.operation_id)
  if k=='acquisition.stop':self._stop();return {'phase':self.phase}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _out(self,f):
  for s in GASES:n=self.gas[s]*f;self.gas[s]-=n;self.exhaust[s]+=n
 def _transition(self,stage):self.stage=stage;self.stage_elapsed=0.
 def _take(self,s,n):
  if s=='AR':available=self.carrier;self.carrier-=min(n,available)
  elif s=='O2':available=self.oxygen;self.oxygen-=min(n,available)
  else:
   i=self.supply['bottle_species'].index(s);available=self.remaining[i];self.remaining[i]-=min(n,available);self.valves[i]=.002
  taken=min(n,available);self.gas[s]+=taken
  if taken<n*(1-1e-10):self._stop('failed','SOURCE_EXHAUSTED')
  return taken
 def step(self,dt):
  validate(number('s',0,86400),dt)
  if not self.plant:
   if dt:self.time_s+=dt;self.revision+=1
   return
  left=dt
  while left>1e-12:
   h=min(.02,left)
   if self.phase=='running' and self.method:
    durations={'pulse_a':self.method['pulse_s'][0],'exposure_a':self.method['exposure_s'][0],'pulse_b':self.method['pulse_s'][1],'exposure_b':self.method['exposure_s'][1]}
    if self.stage in durations:
     h=min(h,max(0.,durations[self.stage]-self.stage_elapsed))
     if h<1e-12:
      self._transition({'pulse_a':'exposure_a','exposure_a':'purge_a','pulse_b':'exposure_b','exposure_b':'purge_b'}[self.stage]);continue
   self._step(h);self.time_s+=h;left-=h
  if dt:self.revision+=1
 def _step(self,dt):
  p=self.plant;old_stage=self.stage;self.valves=[0.]*4;self.carrier_flow=0.;self.plasma_valve=0.;self.rf=0.;self.heater=[0.]*3;self.heartbeat_age+=dt
  if self.powered and self.heartbeat_age>p['watchdog_s']:self._stop('failed','WATCHDOG_TIMEOUT');self.emo=True
  if self.phase=='running':
   self.elapsed_s+=dt;self.stage_elapsed+=dt
   if self.stage=='evacuate' and self._p()<=200.:self._transition('stabilize')
   if self.stage in ['evacuate','stabilize'] and self.elapsed_s>p['purge_timeout_s']*20:self._stop('failed','CONDITION_TIMEOUT')
   if self.stage=='stabilize' and max(abs(t-sp) for t,sp in zip(self.temp,self.target))<=1. and self._p()<=200.:self._transition('pulse_a')
  active=self.phase=='running'
  if active:
   law=LAWS[self.kinetics['law_id']];m=self.method
   if self.stage!='evacuate':
    n=self._take('AR',m['carrier_std_m3_s']*STD_P/(R*STD_T)*dt);self.carrier_flow=n*R*STD_T/STD_P/dt
   if self.phase=='running' and self.stage=='pulse_a' and old_stage=='pulse_a':self._take(law['a'],m['precursor_mol_s'][0]*dt)
   if self.phase=='running' and self.stage=='pulse_b':
    self._take(law['b'],m['precursor_mol_s'][1]*dt)
    if law['plasma']:self.plasma_valve=.002
   if self.phase=='running' and law['plasma'] and self.stage in ['pulse_b','exposure_b']:
    self.rf=m['rf_w'] if self.rf_enabled else 0.
  if self.pump and not self.opened and not self.vent:
   n=sum(self.gas.values());base=p['base_pa']*p['volume_m3']/(R*self.temp[0]);fraction=(1-math.exp(-p['pump_m3_s']*dt/p['volume_m3']))*max(0.,n-base)/max(n,1e-300);self._out(fraction)
  if self.vent:
   target=p['ambient_pa']*p['volume_m3']/(R*self.temp[0]);dn=(target-sum(self.gas.values()))*(1-math.exp(-dt/p['vent_tau_s']))
   if dn>=0:
    for s,f in [('N2',.79),('O2',.21)]:self.gas[s]+=dn*f;self.air[s]+=dn*f
   else:self._out(-dn/max(sum(self.gas.values()),1e-300))
   if abs(self._p()-p['ambient_pa'])<.1:self.vent=False
  if self.phase=='running' and self.sample:
   k=self.kinetics;law=LAWS[k['law_id']];ref=self.templates[k['law_id']]['conditions']['temperature_k'] if not law['plasma'] else self.method['reactor_k']
   thermal=math.exp(max(-50.,min(50.,-k['activation_j_mol']/R*(1/self.temp[0]-1/ref))))
   if self.stage in ['pulse_a','exposure_a','purge_a'] and old_stage==self.stage:
    free=max(0.,self.capacity-self.adsorbed-self.converted_cycle);extent=min(free*(-math.expm1(-min(700.,k['adsorption_pa_s_inv']*thermal*self._p(law['a'])*dt))),self.gas[law['a']]/law['na'])
    self.gas[law['a']]-=extent*law['na'];self.adsorbed+=extent
   if self.stage in ['pulse_b','exposure_b','purge_b']:
    factor=self.rf/300. if law['plasma'] else 1.
    extent=min(self.adsorbed*(-math.expm1(-min(700.,k['conversion_pa_s_inv']*thermal*factor*self._p(law['b'])*dt))),self.gas[law['b']]/law['nb'])
    self.gas[law['b']]-=extent*law['nb'];self.adsorbed-=extent;self.film[law['film']]+=extent;self.converted_cycle+=extent
    for s,c in law['products'].items():self.gas[s]+=extent*c
   if self.stage in ['purge_a','purge_b']:
    b=self.stage=='purge_b';s=law['b'] if b else law['a']
    # PEALD purge also clears product water before the next metal precursor pulse.
    residual=max(self._p(s),self._p('H2O') if law['plasma'] and b else 0.)
    if self.stage_elapsed>p['purge_timeout_s']+self.method['purge_s'][int(b)]:self._stop('failed','PURGE_RESIDUAL_TIMEOUT')
    elif self.stage_elapsed>=self.method['purge_s'][int(b)] and residual<=p['residual_limit_pa']:
     if b:
      self.completed+=1;self.cycles_log.append(dict(time_s=self.time_s+dt,cycle=self.completed,extent_mol=self.converted_cycle,adsorbed_mol=self.adsorbed,thickness_m=self._thickness(),residual_pa=residual,evidence='synthetic'))
      if self.completed==self.method['cycles']:self._stop('succeeded')
      else:self.converted_cycle=0.;self._transition('pulse_a')
     else:self._transition('pulse_b')
  # Explicit sensible thermal boundary: no reaction enthalpy, gas enthalpy or hardware power metering.
  heat=[0.]*3
  for i in range(3):
   if self.phase=='running' and self.powered and not self.opened and not self.emo:
    self.heater[i]=min(p['heater_max_w'][i],max(0.,p['control_gain_w_k']*(self.target[i]-self.temp[i])+p['heat_loss_w_k'][i]*(self.target[i]-p['ambient_k'])))
   loss=p['heat_loss_w_k'][i]*(self.temp[i]-p['ambient_k']);heat[i]=(self.heater[i]-loss)*dt;self.thermal_heater+=self.heater[i]*dt;self.thermal_environment+=loss*dt
  coupling=p['door_coupling_w_k']*(self.temp[0]-self.temp[1])*dt;heat[0]-=coupling;heat[1]+=coupling
  rfheat=self.rf*p['rf_heat_fraction']*dt;heat[0]+=rfheat;self.thermal_rf+=rfheat
  self.temp=[t+q/c for t,q,c in zip(self.temp,heat,p['heat_capacity_j_k'])]
  if self.phase=='running' and (self.temp[0]>574.15 or self.temp[1]>574.15 or self.temp[2]>474.15):self._stop('failed','MODEL_OVERTEMPERATURE')
  if self.phase!='running':self.valves=[0.]*4;self.carrier_flow=0.;self.plasma_valve=0.;self.rf=0.;self.heater=[0.]*3

class ArradianceGemstarxtSession:
    """Only the trusted host assigns roles; returned data obey the same boundary."""
    def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
    @property
    def _role(self):return self._host._principals[self._principal]
    def _target(self,i):
        if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
    def describe(self,*,instance_id=None,asset_type_id=None):
        if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None:self._target(instance_id)
        if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
        m=export_arradiance_gemstarxt_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=h.time_s,sample_window_s=None) for k in ids})
    def invoke(self,*,instance_id,capability_id,arguments,request_id):
        h=self._host
        try:
            self._target(instance_id)
            if not isinstance(capability_id,str) or capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
            if self._role=='agent' and CAPS[capability_id][5]!='agent':raise ActionRejected('ACCESS_DENIED')
            if not isinstance(request_id,str) or not request_id:raise ActionRejected('INVALID_ARGUMENT')
            validate(CAPS[capability_id][0],arguments);key=(self._principal,instance_id,request_id);digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
            if key in h._requests:
                old,result=h._requests[key]
                if old!=digest:raise ActionRejected('REQUEST_CONFLICT')
                return copy.deepcopy(result)
            result=h.invoke(capability_id,**arguments);op=result.get('operation_id')
            if op:h._owners[op]=self._principal
            response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=op,status='accepted' if op else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None);h._requests[key]=(digest,copy.deepcopy(response));return response
        except ActionRejected as exc:return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,status='rejected',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))
    def write(self,*,instance_id,state_id,value,request_id):
        self._target(instance_id)
        if state_id not in SLOTS or not SLOTS[state_id][5]:raise ActionRejected('ACCESS_DENIED')
        cap,arg=SLOTS[state_id][5];return self.invoke(instance_id=instance_id,capability_id=cap,arguments={arg:value},request_id=request_id)
    def poll(self,operation_id):
        h=self._host
        if operation_id!=h.operation_id or h._owners.get(operation_id)!=self._principal:raise ActionRejected('ACCESS_DENIED')
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('process.measure'))
    def cancel(self,operation_id,request_id):
        self.poll(operation_id);return self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)










def export_arradiance_gemstarxt_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'ArradianceGemstarxtSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
 evolving=[k for k,v in SLOTS.items() if v[2] in ['scene','derived','process_truth']]
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  asynchronous=k=='acquisition.start';query=kind=='query'
  caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.arradiance_gemstarxt.ArradianceGemstarxtSession.invoke',invocation=f'session.invoke(instance_id="gemstarxt_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if query else [dict(description_zh='已配置/双电源/门冷态常压/有限气源/前驱体关联/宿主权限',state_ids=['power.enabled','rf.enabled','door.opened','cycle.phase'],enforced_by='runtime',check='atomic Runtime.invoke and trusted-principal Session')],
  state_effects=[] if query else [dict(state_id=s,timing='immediate',description_zh='受检设置或宿主装载') for s in effects]+[dict(state_id=s,timing='on_step',description_zh='唯一宿主时钟推进') for s in evolving],observes=[],model_effects=dict(mode='none',description_zh='等待独立场景实测证据',binding_ids=[]),
  execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',completion_criterion='完整A/清扫/B/清扫循环计数达到设定，残余反应物在工程阈值下；不表示冷却可卸样' if asynchronous else '事务完成；不瞬间设置物理温度或压力',completion_state_ids=['cycle.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',retry='same_request_id',resources=['host_clock','finite_sources','substrate','reactor','pump','request_cache'],partial_failure_zh='取消停进气/RF/热，保留真空、吸附前驱体、膜、气体残留和余热。去重限进程内。'),
  errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','PRECONDITION_FAILED','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','INITIALIZATION_ONLY','EXPLICIT_WARM_SNAPSHOT_REQUIRED','COLD_VENTED_ACCESS_REQUIRED','PLASMA_CAPACITY_REQUIRED','PLASMA_POWER_REQUIRED','REFERENCE_TEMPERATURE_RANGE','EMO_LATCHED','BUSY','SOURCE_EMPTY']],
  example_arguments=copy.deepcopy(ex),verification=[],limitations=['集总表面反应与有限物料教学模型；不是原厂recipe、逐分子反应路径或真实安全控制'],source_ids=['manufacturer_primary','simulation_assumption'])
 mech=dict(component_ids=['reactor','door','manifold','end_effector','icp_head','carrier_mfc']+['precursor_valve_'+str(i) for i in range(4)],state_ids=list(slots),meaning_zh='不同前驱体的有限吸附/转化半反应、清扫与三热节点',implementation='lumped',
 equations=['P_i=n_i*R*Treactor/V; dn_carrier=Qstd*101325/(R*273.15)*dt; one shared carrier ledger also serves RF inlet0',
 'Thermal site_capacity=A*rho*GPC_ref/Mfilm; each reference at its own reported temperature only (±1K model window); PEALD capacity=A*host_site_mol_m2',
 'A pulse/exposure/purge: dx=min(free_sites*(1-exp(-host_ka*pA*Arrhenius*dt)),nA/nuA), nA-=nuA*dx; bound intermediate contains intact net precursor formula',
 'B pulse/exposure/purge: dx=min(bound*(1-exp(-host_kb*pB*Arrhenius*power_factor*dt)),nB/nuB); bound-=dx, film+=dx; finite gas products by each net stoichiometry',
 'Thermal: 2TMA+3H2O->Al2O3+6CH4; DEZ+H2O->ZnO+2C2H6; TTIP+2H2O->TiO2+4IPA',
 'PEALD overall oxidation proxy only: 2TMA+12O2->Al2O3+6CO2+9H2O; power_factor=RF/300; no conversion when RF=0; no copied thermalGPC',
 'n_out_i=n_i*(1-exp(-S*dt/V))*(1-n_base/n_total); purge must satisfy configured min time and residual partial pressure before advancing; water product also purged after PEALD',
 'thickness=sum(nfilm*Mfilm)/(rho*A); collected products and bound precursors remain in elemental ledger; every element including ligands conserved',
 'Ci*dTi=heater_i*dt-Ui*(Ti-Tamb)*dt+internal_door_coupling+host_fraction*RF*dt; heaters bounded by explicit host ratings, three setpoint limits documented',
 'Host.step sole clock; integration<=0.02s and clipped to every pulse/exposure boundary; no hidden advancement by read/describe; NC gas valves and RF off on stop/EMO/watchdog/powerloss'],
 parameters={},source_ids=['manufacturer_primary','alumina_primary','zinc_primary','titania_primary','simulation_assumption'],
 limitations=['只有3个热ALD材料的原始文献名义GPC，109人工标注行/45规范化标签仅离线参考，非50种本机标定工艺。',
 '表面中间体为守恒净反应等效物，不含逐配体裂解/成核/岛长大/高深宽比输运/表面OH位点与缺陷演化。',
 '密度、热容、动力学、腔容、有效抽速、压力阈值/超时、阀行程均显式assumption；不代表实测阀时序/热精度/生长率。',
 'PEALD仅TMA/O2一套未标定净反应；自由基密度/气体电离/鞘层/复合/离子能量/ICP场均未求解。',
 '能量账本仅集总显热，不包含反应热/气流焓/化学能；不能声称完整反应器能量守恒。',
 'QCM/电容规/臭氧源/450°C台/双歧管/外部退火未安装；全GEMFlowGUI/真实USB/机器人附着/实物测厚均absent。'])
 spec=importlib.util.spec_from_file_location('_gemstar_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
 ann.annotate_export(SLUG,caps,slots,{'ald':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'gas.install':(SUPPLY,SUPPLY_EXAMPLE),'kinetics.configure':(KINETICS,KINETICS_EXAMPLE),'sample.install':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 for k,v in slots.items():
  v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'，不代表实物标定。'
  if v['unit']=='Pa':v['reference_conditions']='腔体绝对压；气源/CDA输入参数为表压，不混淆'
  if v['unit']=='m^3/s':v['reference_conditions']='273.15K、101325Pa标准体积为宿主假设；厂家标准状态未说明'
 mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'ald':mech})


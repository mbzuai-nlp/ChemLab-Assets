"""AsiaFLLEX100uL conditional single-solute octanol/water model; no intrinsic liquid pumps."""
from __future__ import annotations
import copy,functools,importlib.util,json,math,sys
from pathlib import Path
import numpy as np
from scipy.linalg import expm
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.antonpaar_abbematadvanced import validate
ROOT=Path(__file__).resolve().parents[2];SLUG='syrris_asiafllex';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-033';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/syrris_asiafllex_partition_library/templates.json';V=100e-9
PHASE={'type':'string','enum':['idle','running','succeeded','cancelled','failed']}
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
PLANT=obj({'external_pumps_confirmed':BOOL,'pressure_controller_confirmed':BOOL,'pre_equilibrated_immiscible_phases':BOOL,'reference_conditions_accepted':BOOL,'vent_path_confirmed':BOOL,'phase_pair':{'type':'string','enum':['water_1_octanol']},'ambient_pa':number('Pa',8e4,1.2e5),'temperature_k':number('K',293.15,303.15),'gas_supply_gauge_pa':number('Pa',1e5,1e6),'gas_margin_pa':number('Pa',1e3,2e5),'max_phase_flow_m3_s':number('m^3/s',250e-9/60,500e-9/60),'pressure_tau_s':number('s',.1,100),'release_tau_s':number('s',.1,100),'mass_transfer_coeff_m_s':number('m/s',0,.1),'interfacial_area_m2':number('m^2',1e-8,.1),'max_dilute_concentration_kg_m3':number('kg/m^3',1e-4,10),'aqueous_collection_capacity_m3':number('m^3',1e-9,.1),'organic_collection_capacity_m3':number('m^3',1e-9,.1),'coefficient_evidence':TEXT})
EXAMPLE=dict(external_pumps_confirmed=True,pressure_controller_confirmed=True,pre_equilibrated_immiscible_phases=True,reference_conditions_accepted=True,vent_path_confirmed=True,phase_pair='water_1_octanol',ambient_pa=101325.,temperature_k=298.15,gas_supply_gauge_pa=6e5,gas_margin_pa=1e5,max_phase_flow_m3_s=250e-9/60,pressure_tau_s=2.,release_tau_s=3.,mass_transfer_coeff_m_s=2e-5,interfacial_area_m2=1e-4,max_dilute_concentration_kg_m3=1.,aqueous_collection_capacity_m3=50e-6,organic_collection_capacity_m3=50e-6,coefficient_evidence='synthetic_transfer_area_pressure_response_not_measured_FLLEX')
MEMBRANE=obj({'material':{'type':'string','enum':['PTFE']},'compatible_pre_wetted_reviewed':BOOL,'organic_permeance_m3_s_pa':number('m^3/(s*Pa)',1e-16,1e-10),'wetted_aqueous_permeance_m3_s_pa':number('m^3/(s*Pa)',1e-16,1e-10),'effective_pore_radius_m':number('m',1e-9,1e-5),'contact_angle_rad':number('rad',math.pi/2+.01,math.pi-.01),'interfacial_tension_n_m':number('N/m',1e-4,.1),'coefficient_evidence':TEXT})
MEMBRANE_EXAMPLE=dict(material='PTFE',compatible_pre_wetted_reviewed=True,organic_permeance_m3_s_pa=2e-13,wetted_aqueous_permeance_m3_s_pa=1e-13,effective_pore_radius_m=.5e-6,contact_angle_rad=2*math.pi/3,interfacial_tension_n_m=.008,coefficient_evidence='assumed_effective_capillarity_and_permeances_not_actual_membrane_specs')
METHOD=obj({'aqueous_flow_m3_s':number('m^3/s',1e-11,500e-9/60),'organic_flow_m3_s':number('m^3/s',1e-11,500e-9/60),'bpr_gauge_pa':number('Pa',1e5,9e5),'cross_membrane_pa':number('Pa',0,5e4),'duration_s':number('s',1,7200),'timeout_s':number('s',1,21600),'stop_on_breakthrough':BOOL})
METHOD_EXAMPLE=dict(aqueous_flow_m3_s=100e-9/60,organic_flow_m3_s=100e-9/60,bpr_gauge_pa=2e5,cross_membrane_pa=1e4,duration_s=120.,timeout_s=1800.,stop_on_breakthrough=True)
SAMPLE=obj({'sample_id':TEXT,'substance_id':TEXT,'aqueous_feed_volume_m3':number('m^3',1e-9,.1),'organic_feed_volume_m3':number('m^3',1e-9,.1),'aqueous_solute_mass_kg':number('kg',0,.001),'organic_solute_mass_kg':number('kg',0,.001),'neutral_dilute_stable_solute_confirmed':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='mephenesin_feed',substance_id='cas_59-47-2',aqueous_feed_volume_m3=5e-6,organic_feed_volume_m3=5e-6,aqueous_solute_mass_kg=1e-6,organic_solute_mass_kg=0.,neutral_dilute_stable_solute_confirmed=True)
RESULT=obj({'phase':PHASE,'stage':TEXT,'fault':TEXT,'bpr_gauge_pa':number('Pa',0),'cross_membrane_pa':number('Pa',0),'aqueous_flow_m3_s':number('m^3/s',0),'organic_flow_m3_s':number('m^3/s',0),'organic_outlet_purity':nullable(number('1',0,1)),'aqueous_outlet_purity':nullable(number('1',0,1)),'separation_state':TEXT,'evidence':TEXT,'template_source':TEXT})
PRODUCT=obj({'substance_id':nullable(TEXT),'aqueous_outlet_solute_kg':number('kg',0),'organic_outlet_solute_kg':number('kg',0),'aqueous_holdup_solute_kg':number('kg',0),'organic_holdup_solute_kg':number('kg',0),'aqueous_outlet_volume_m3':number('m^3',0),'organic_outlet_volume_m3':number('m^3',0),'measured_FLLEX_recovery':{'type':'null'},'evidence':TEXT,'template_source':TEXT})
REF=obj({'substance_id':TEXT,'log10_kow':number('1'),'kow':number('1',0),'temperature_k':{'type':'null'},'ph':{'type':'null'},'evidence':TEXT,'source':TEXT,'template_source':TEXT})
POINT=obj({'time_s':number('s',0),'bpr_gauge_pa':number('Pa',0),'cross_membrane_pa':number('Pa',0),'stage':TEXT,'separation_state':TEXT})
CAPS={
 'plant.configure':(PLANT,obj({'configured':BOOL}),['gas_inlet','aqueous_inlet','organic_inlet'],'外泵/气源/两相预平衡和未标定传质边界','configuration','evaluator',EXAMPLE,['plant.configured']),
 'membrane.configure':(MEMBRANE,obj({'configured':BOOL}),['ptfe_membrane','separator_base'],'首次膜材、预润湿与显式毛细/渗透假设','configuration','evaluator',MEMBRANE_EXAMPLE,['membrane.configured']),
 'method.configure':(METHOD,obj({'configured':BOOL}),['bpr_aqueous','bpr_organic','contactor_t'],'外泵目标流率与气控背压；本模块没有内置泵','configuration','agent',METHOD_EXAMPLE,['method.configured','pressure.bpr_target_pa','pressure.cmp_target_pa']),
 'tray.set_extension':(obj({'extension_m':number('m',0,.07)}),obj({'extension_m':number('m',0,.07)}),['tray','tray_handle'],'宿主手动托盘代理；不是电驱','configuration','evaluator',{'extension_m':.07},['tray.extension_m']),
 'separator.set_opening':(obj({'opening_m':number('m',0,.04)}),obj({'opening_m':number('m',0,.04)}),['separator_cover'],'宿主前半膜块拆开代理，非真实螺钉操作','configuration','evaluator',{'opening_m':.04},['separator.opening_m']),
 'sample.configure':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['aqueous_inlet','organic_inlet','contactor_t'],'两外部储液瓶装入真值；100µL初始为预润湿无溶质载液','configuration','evaluator',SAMPLE_EXAMPLE,['sample.configured']),
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['rear_housing'],'控制电源；断电停止宿主泵门控但不自动释放残压','device_command','agent',{'enabled':True},['power.enabled']),
 'panel.select':(obj({'mode':{'type':'string','enum':['cmp','bpr']}}),obj({'mode':{'type':'string','enum':['cmp','bpr']}}),['display_digits','encoder'],'教学切换单屏压差mbar/主背压bar；非全菜单固件','device_command','agent',{'mode':'cmp'},['panel.mode','panel.value']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['contactor_t','ptfe_membrane','bpr_aqueous','bpr_organic'],'启动宿主协调的外泵连续萃取，先稳定背压','device_command','agent',{},['cycle.phase','cycle.stage']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['aqueous_inlet','organic_inlet'],'停止外泵虚拟门控；保留持液、污染、收集物和残压','device_command','agent',{},['cycle.phase']),
 'pressure.release':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['gas_inlet','bpr_aqueous','bpr_organic'],'宿主确认放空路径后教学降压；非安全认证','device_command','agent',{},['cycle.phase','cycle.stage']),
 'separation.measure':(obj({}),RESULT,['display_digits','aqueous_outlet','organic_outlet'],'派生压力/流率及教学相纯度；没有真实组成传感器','query','agent',{},[]),
 'product.read':(obj({}),PRODUCT,['aqueous_outlet','organic_outlet','ptfe_membrane'],'双出口与持液真值；非真实产品回收标定','query','evaluator',{},[]),
 'history.read':(obj({}),obj({'records':{'type':'array','items':POINT},'evidence':TEXT}),['display_digits'],'宿主时钟过程曲线','query','agent',{},[]),
 'reference.lookup':(obj({'substance_id':TEXT}),REF,['contactor_t'],'实测编译辛醇/水分配参考，温度/pH缺失不伪造','query','agent',{'substance_id':'cas_59-47-2'},[])}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,zh,o='derived',v='agent',c='display_digits',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=zh
for k,s,u,zh in [('power.enabled',BOOL,'1','控制电源状态'),('plant.configured',BOOL,'1','宿主边界已配置'),('membrane.configured',BOOL,'1','膜配置已登记'),('method.configured',BOOL,'1','外部流程方法已配置'),('sample.configured',BOOL,'1','外部进料瓶已配置'),('cycle.phase',PHASE,'1','作业状态'),('cycle.stage',TEXT,'1','过程阶段'),('cycle.fault',TEXT,'1','最近过程故障'),('cycle.elapsed_s',number('s',0),'s','本次萃取作业累计时长，含加压'),('cycle.processed_s',number('s',0),'s','累计实际连续流动时长'),('pressure.bpr_target_pa',number('Pa',0),'Pa','水相出口背压目标表压'),('pressure.cmp_target_pa',number('Pa',0),'Pa','跨膜目标压差Paq减Porg'),('pressure.bpr_gauge_pa',number('Pa',0),'Pa','理想水相BPR表压'),('pressure.cmp_pa',number('Pa',0),'Pa','模型跨膜压差Paq减Porg'),('pressure.organic_gauge_pa',number('Pa',0),'Pa','有机相BPR表压等于水相减跨膜压差'),('flow.aqueous_m3_s',number('m^3/s',0),'m^3/s','外泵水相实际液体流率'),('flow.organic_m3_s',number('m^3/s',0),'m^3/s','外泵有机相实际液体流率'),('separation.state',TEXT,'1','教学夹带/润湿状态'),('separation.organic_purity',nullable(number('1',0,1)),'1','有机出口载液相纯度教学诊断，非物质纯度'),('separation.aqueous_purity',nullable(number('1',0,1)),'1','水相出口载液相纯度教学诊断，非物质纯度'),('panel.mode',{'type':'string','enum':['cmp','bpr']},'1','单屏当前压差或背压模式'),('panel.value',number('1',0),'1','单屏格式数值；cmp为mbar，bpr为bar；SI压力另有槽'),('membrane.wetted',BOOL,'1','工程非润湿相突破后的不可逆湿润标志')]:slot(k,s,u,zh,o='setpoint' if 'target' in k else 'derived')
slot('tray.extension_m',number('m',0,.07),'m','宿主托盘前移','scene',c='tray');slot('separator.opening_m',number('m',0,.04),'m','宿主前膜块向前移开','scene',c='separator_cover')
for phase in ['aqueous','organic']:
 for place in ['initial','feed','holdup','aqueous_outlet','organic_outlet']:
  slot('truth.'+place+'_'+phase+'_m3',number('m^3',0),'m^3',{'initial':'初始总计','feed':'外部进料瓶中','holdup':'模块持液中','aqueous_outlet':'水相出口收集器中','organic_outletlet':'有机出口收集器中'}.get(place,'有机出口收集器中')+('水相载液体积' if phase=='aqueous' else '有机载液体积'),'process_truth','evaluator','contactor_t')
for place in ['initial','aqueous_feed','organic_feed','aqueous_holdup','organic_holdup','aqueous_outlet','organic_outlet']:
 slot('truth.'+place+'_solute_kg',number('kg',0),'kg',{'initial':'初始总计','aqueous_feed':'水相进料瓶中','organic_feed':'有机进料瓶中','aqueous_holdup':'水相持液中','organic_holdup':'有机持液中','aqueous_outlet':'水相出口中','organic_outlet':'有机出口中'}[place]+'溶质质量','process_truth','evaluator','contactor_t')
@functools.lru_cache(maxsize=512)
def propagator(dt,qa,qo,va,vo,k,g,ca,co):
 a=np.zeros((5,5));a[0,0]=-qo/vo-g/(k*vo);a[0,1]=g/va;a[0,4]=qo*co;a[1,0]=g/(k*vo);a[1,1]=-qa/va-g/va;a[1,4]=qa*ca;a[2,0]=qo/vo;a[3,1]=qa/va
 return expm(a*dt)
class SyrrisAsiaFllexRuntime:
 def __init__(self,instance_id='fllex_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.plant=None;self.membrane=None;self.method=None;self.sample=None;self.powered=False;self.phase='idle';self.stage='standby';self.fault='';self.elapsed_s=0.;self.processed_s=0.;self.operation_id=None;self._operation_count=0;self.tray=0.;self.opening=0.;self.mode='cmp';self.bpr=0.;self.cmp=0.;self.release=False;self.wetted=False;self.q=np.zeros(2);self.purity=[None,None];self.separation='not_running';self.vol=np.zeros((4,2));self.mass=np.zeros(6);self.history=[];self.next_record=0.;self.templates={x['substance_id']:x for x in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return SyrrisAsiaFllexSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _targets(self):return (0.,0.) if self.release or not self.method else (self.method['bpr_gauge_pa'],self.method['cross_membrane_pa'])
 def _values(self):
  bp,dp=self._targets();v={'power.enabled':self.powered,'plant.configured':bool(self.plant),'membrane.configured':bool(self.membrane),'method.configured':bool(self.method),'sample.configured':bool(self.sample),'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.fault':self.fault,'cycle.elapsed_s':self.elapsed_s,'cycle.processed_s':self.processed_s,'pressure.bpr_target_pa':bp,'pressure.cmp_target_pa':dp,'pressure.bpr_gauge_pa':self.bpr,'pressure.cmp_pa':self.cmp,'pressure.organic_gauge_pa':max(0,self.bpr-self.cmp),'flow.aqueous_m3_s':self.q[0],'flow.organic_m3_s':self.q[1],'separation.state':self.separation,'separation.aqueous_purity':self.purity[0],'separation.organic_purity':self.purity[1],'panel.mode':self.mode,'panel.value':self.cmp/100 if self.mode=='cmp' else self.bpr/1e5,'membrane.wetted':self.wetted,'tray.extension_m':self.tray,'separator.opening_m':self.opening}
  for i,p in enumerate(['aqueous','organic']):
   v['truth.initial_'+p+'_m3']=self.initial_vol[i] if self.sample else 0.
   for j,place in enumerate(['feed','holdup','aqueous_outlet','organic_outlet']):v['truth.'+place+'_'+p+'_m3']=float(self.vol[j,i])
  v['truth.initial_solute_kg']=self.initial_mass if self.sample else 0.
  for j,place in enumerate(['aqueous_feed','organic_feed','aqueous_holdup','organic_holdup','aqueous_outlet','organic_outlet']):v['truth.'+place+'_solute_kg']=float(self.mass[j])
  return v
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('debug','evaluator')})
 def debug_snapshot(self):return copy.deepcopy(dict(state=self.read('evaluator'),plant=self.plant,method=self.method,membrane=self.membrane,sample=self.sample,clock=self.time_s,revision=self.revision,operation=self.operation_id,history=self.history))
 def _safe_service(self):return self.phase!='running' and self.bpr<1000 and self.cmp<1000 and not self.sample
 def _reference(self,sid):
  if sid not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
  r=self.templates[sid];return dict(substance_id=sid,log10_kow=r['values']['log10_kow'],kow=r['values']['kow'],temperature_k=None,ph=None,evidence=r['evidence'],source=r['source'],template_source=TEMPLATE_REL)
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if self.phase=='running' and CAPS[capability][4]!='query' and capability not in ['acquisition.stop','power.set','panel.select']:raise ActionRejected('BUSY')
  if capability=='plant.configure':
   if self.plant or not all(a[k] for k in ['external_pumps_confirmed','pressure_controller_confirmed','pre_equilibrated_immiscible_phases','reference_conditions_accepted','vent_path_confirmed']):raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);out={'configured':True}
  elif capability=='membrane.configure':
   if not self.plant or self.membrane or self.tray<.05 or self.opening<.03 or not self._safe_service() or not a['compatible_pre_wetted_reviewed']:raise ActionRejected('PRECONDITION_FAILED')
   self.membrane=copy.deepcopy(a);out={'configured':True}
  elif capability=='method.configure':
   if not self.plant or self.sample:raise ActionRejected('PRECONDITION_FAILED')
   if max(a['aqueous_flow_m3_s'],a['organic_flow_m3_s'])>self.plant['max_phase_flow_m3_s'] or a['timeout_s']<a['duration_s']:raise ActionRejected('METHOD_ENVELOPE')
   if a['bpr_gauge_pa']+self.plant['gas_margin_pa']>self.plant['gas_supply_gauge_pa']:raise ActionRejected('GAS_PRESSURE_MARGIN')
   self.method=copy.deepcopy(a);self.release=False;out={'configured':True}
  elif capability=='tray.set_extension':
   if not self._safe_service() or (a['extension_m']<.05 and self.opening>0):raise ActionRejected('PRECONDITION_FAILED')
   self.tray=a['extension_m'];out=a.copy()
  elif capability=='separator.set_opening':
   if not self._safe_service() or self.tray<.05:raise ActionRejected('PRECONDITION_FAILED')
   self.opening=a['opening_m'];out=a.copy()
  elif capability=='sample.configure':
   if not self.plant or not self.membrane or not self.method or self.sample or not a['neutral_dilute_stable_solute_confirmed'] or self.tray>0 or self.opening>0:raise ActionRejected('PRECONDITION_FAILED')
   ref=self._reference(a['substance_id']);vol=np.array([a['aqueous_feed_volume_m3'],a['organic_feed_volume_m3']]);mass=np.array([a['aqueous_solute_mass_kg'],a['organic_solute_mass_kg']]);concs=mass/vol
   if any(concs>self.plant['max_dilute_concentration_kg_m3']) or sum(mass)<=0:raise ActionRejected('DILUTE_REFERENCE_REQUIRED')
   q=np.array([self.method['aqueous_flow_m3_s'],self.method['organic_flow_m3_s']]);self.sample=copy.deepcopy(a);self.vol[0]=vol;self.vol[1]=V*q/q.sum();self.mass[:2]=mass;self.initial_vol=self.vol.sum(axis=0).copy();self.initial_mass=float(sum(mass));self.feed_conc=concs;self.kow=ref['kow'];out=dict(configured=True,attachment_event='host_confirmed_external_feeds_and_prewetted100uL')
  elif capability=='power.set':
   self.powered=a['enabled']
   if not self.powered and self.phase=='running':self.phase='cancelled';self.q[:]=0
   out=a.copy()
  elif capability=='panel.select':self.mode=a['mode'];out=a.copy()
  elif capability=='acquisition.start':
   if not self.powered or not self.sample or self.tray>0 or self.opening>0 or self.phase not in ('idle','cancelled') or self.wetted or self.release:raise ActionRejected('PRECONDITION_FAILED')
   self.phase='running';self.stage='pressurizing' if max(abs(self.bpr-self.method['bpr_gauge_pa']),abs(self.cmp-self.method['cross_membrane_pa']))>1. else 'extracting';self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase=='running':self.phase='cancelled';self.q[:]=0
   out=dict(phase=self.phase)
  elif capability=='pressure.release':
   if not self.plant or not self.powered or self.phase=='running' or self.bpr<=1. or self.tray>0 or self.opening>0:raise ActionRejected('PRECONDITION_FAILED')
   self.release=True;self.phase='running';self.stage='depressurizing';self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='separation.measure':out=dict(phase=self.phase,stage=self.stage,fault=self.fault,bpr_gauge_pa=self.bpr,cross_membrane_pa=self.cmp,aqueous_flow_m3_s=float(self.q[0]),organic_flow_m3_s=float(self.q[1]),aqueous_outlet_purity=self.purity[0],organic_outlet_purity=self.purity[1],separation_state=self.separation,evidence='derived_pressure_phase_purity_with_measured_partition_reference_not_FLLEX_sensor_or_recovery',template_source=TEMPLATE_REL)
  elif capability=='product.read':out=dict(substance_id=self.sample['substance_id'] if self.sample else None,aqueous_outlet_solute_kg=float(self.mass[4]),organic_outlet_solute_kg=float(self.mass[5]),aqueous_holdup_solute_kg=float(self.mass[2]),organic_holdup_solute_kg=float(self.mass[3]),aqueous_outlet_volume_m3=float(self.vol[2].sum()),organic_outlet_volume_m3=float(self.vol[3].sum()),measured_FLLEX_recovery=None,evidence='conditional_mass_and_carrier_volume_ledger_not_measured_FLLEX_recovery',template_source=TEMPLATE_REL)
  elif capability=='history.read':out=dict(records=copy.deepcopy(self.history),evidence='derived_model_pressure_and_phase_state')
  else:out=self._reference(a['substance_id'])
  validate(CAPS[capability][1],out)
  if CAPS[capability][4]!='query':self.revision+=1
  return copy.deepcopy(out)
 def _fail(self,code):self.phase='failed';self.fault=code;self.q[:]=0
 def _advance(self,dt):
  self.q[:]=0
  if self.plant and self.method and self.powered:
   bp,dp=self._targets();tau=self.plant['release_tau_s'] if self.release else self.plant['pressure_tau_s'];factor=math.exp(-dt/tau);self.bpr=bp+(self.bpr-bp)*factor;self.cmp=dp+(self.cmp-dp)*factor
   if max(abs(self.bpr-bp),abs(self.cmp-dp))<1.:self.bpr,self.cmp=bp,dp
  if self.phase!='running':return
  if self.release:
   if self.bpr<=1.:self.phase='succeeded';self.stage='depressurized'
   return
  self.elapsed_s+=dt
  if self.elapsed_s>self.method['timeout_s']:self._fail('TIMEOUT');return
  if self.stage=='pressurizing':
   if self.bpr==self.method['bpr_gauge_pa'] and self.cmp==self.method['cross_membrane_pa']:self.stage='extracting'
   return
  if self.stage!='extracting':return
  qa,qo=self.method['aqueous_flow_m3_s'],self.method['organic_flow_m3_s'];m=self.membrane
  entry=2*m['interfacial_tension_n_m']*abs(math.cos(m['contact_angle_rad']))/m['effective_pore_radius_m']
  if self.cmp>=entry:self.wetted=True
  fo=min(1.,m['organic_permeance_m3_s_pa']*self.cmp/qo);fa=min(1.,m['wetted_aqueous_permeance_m3_s_pa']*self.cmp/qa) if self.wetted else 0.
  self.separation='aqueous_breakthrough' if self.wetted else ('organic_breakthrough' if fo<1-1e-10 else 'separated')
  routing=np.array([[qa*(1-fa),qo*(1-fo)],[qa*fa,qo*fo]])
  self.purity=[float(routing[0,0]/routing[0].sum()) if routing[0].sum()>0 else None,float(routing[1,1]/routing[1].sum()) if routing[1].sum()>0 else None]
  h=min(dt,max(0.,self.method['duration_s']-self.processed_s),self.vol[0,0]/qa,self.vol[0,1]/qo)
  for row,cap in [(0,self.plant['aqueous_collection_capacity_m3']),(1,self.plant['organic_collection_capacity_m3'])]:
   if routing[row].sum()>0:h=min(h,max(0.,cap-self.vol[row+2].sum())/routing[row].sum())
  if h>1e-10:
   va,vo=self.vol[1];ca,co=self.feed_conc;g=self.plant['mass_transfer_coeff_m_s']*self.plant['interfacial_area_m2'];matrix=propagator(round(h,12),qa,qo,va,vo,self.kow,g,ca,co);y=matrix@np.array([self.mass[3],self.mass[2],0.,0.,1.]);raw_o,raw_a=y[2],y[3];self.mass[3],self.mass[2]=max(0.,y[0]),max(0.,y[1]);self.mass[0]=max(0.,self.mass[0]-qa*ca*h);self.mass[1]=max(0.,self.mass[1]-qo*co*h);self.mass[4]+=raw_a*(1-fa)+raw_o*(1-fo);self.mass[5]+=raw_a*fa+raw_o*fo;self.vol[0]=np.maximum(0.,self.vol[0]-np.array([qa,qo])*h);self.vol[2:]+=routing*h;self.processed_s+=h;self.q[:]=qa,qo
  if self.method['stop_on_breakthrough'] and self.separation!='separated':self._fail('MEMBRANE_WETTING' if self.wetted else 'ORGANIC_BREAKTHROUGH')
  elif self.processed_s>=self.method['duration_s']-1e-8:self.phase='succeeded';self.stage='finished_residual_pressure';self.q[:]=0
  elif min(self.vol[0])<1e-15:self._fail('FEED_EMPTY')
  elif any(self.vol[i+2].sum()>=cap-1e-15 for i,cap in enumerate([self.plant['aqueous_collection_capacity_m3'],self.plant['organic_collection_capacity_m3']])):self._fail('COLLECTION_FULL')
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<=0 or dt_s>3600:raise ActionRejected('INVALID_ARGUMENT')
  remaining=float(dt_s)
  while remaining>1e-10:
   d=min(.25,remaining);self._advance(d);self.time_s+=d;self.revision+=1;remaining-=d
   if self.time_s>=self.next_record:
    self.history.append(dict(time_s=self.time_s,bpr_gauge_pa=self.bpr,cross_membrane_pa=self.cmp,stage=self.stage,separation_state=self.separation));self.next_record=self.time_s+5
class SyrrisAsiaFllexSession:
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
        meta=export_syrris_asiafllex_contract_metadata()
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('separation.measure'))

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])

def export_syrris_asiafllex_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'SyrrisAsiaFllexSession.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key in ['acquisition.start','pressure.release'];pre=[]
        premap={
         'plant.configure':('首次且外泵/气源/互不混溶预平衡/参考条件/排气均确认',['plant.configured'],'not configured and all_confirmations'),
         'method.configure':('先配边界且未装样；每相流率与供气余量满足宿主界限',['plant.configured','sample.configured'],'plant and no_sample and rate<=host_max and bpr+margin<=gas'),
         'membrane.configure':('首次，托盘与膜块打开，停流且表压<1kPa，无样品',['membrane.configured','tray.extension_m','separator.opening_m','sample.configured'],'unconfigured and safe_service and tray>=.05 and opening>=.03'),
         'tray.set_extension':('停流降压、未装入液体；膜块打开时不允许收回托盘',['cycle.phase','pressure.bpr_gauge_pa','sample.configured','separator.opening_m'],'safe_service and no_cover_interference'),
         'separator.set_opening':('停流降压、未装入液体且托盘前移',['cycle.phase','pressure.bpr_gauge_pa','tray.extension_m','sample.configured'],'safe_service and tray>=.05'),
         'sample.configure':('方法/膜就绪，已关闭托盘膜块，单中性稀溶质与参考匹配',['sample.configured','method.configured','membrane.configured','tray.extension_m','separator.opening_m'],'not loaded and compatible and closed and known_reference and host_dilute_bound'),
         'acquisition.start':('通电、配样、未湿润、未进入放空；首次或取消续作',['power.enabled','sample.configured','membrane.wetted','cycle.phase'],'powered and loaded and closed and no_wetting and not_release and resumable'),
         'pressure.release':('停流、通电、气路确认、托盘膜块关闭且存在残压',['power.enabled','cycle.phase','pressure.bpr_gauge_pa','tray.extension_m','separator.opening_m'],'not_running and powered and closed and positive_pressure')}
        if key in premap:
            text,states,expr=premap[key];pre=[dict(description_zh=text,state_ids=states,enforced_by='runtime',check=expr)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.syrris_asiafllex.SyrrisAsiaFllexSession.invoke',invocation=f'session.invoke(instance_id="fllex_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion=('累计外泵流动达到duration_s；终点保留持液和背压' if key=='acquisition.start' else '宿主教学放空至表压0') if async_ else '同步配置或纯查询，不推进时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='参数拒绝无状态变化；过程突破、耗尽、满收集或超时保留所有已发生库存/持液/残压/湿润。暂停无传质，不是安全认证。去重仅当前进程'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    evolving=list(slots)
    for key in ['acquisition.start','pressure.release','acquisition.stop','power.set','plant.configure','sample.configure','membrane.configure']:
        caps[key]['state_effects'] += [dict(state_id=k,timing='on_step' if key in ['acquisition.start','pressure.release'] else 'immediate',description_zh='有限两相载液、单溶质账本、残压和膜状态') for k in evolving if k not in [x['state_id'] for x in caps[key]['state_effects']]]
    for key,code in [('sample.configure','UNKNOWN_REFERENCE'),('reference.lookup','UNKNOWN_REFERENCE'),('sample.configure','DILUTE_REFERENCE_REQUIRED'),('method.configure','METHOD_ENVELOPE'),('method.configure','GAS_PRESSURE_MARGIN')]:caps[key]['errors'].append(dict(code=code,meaning_zh=code,side_effects='none'))
    for key in ['acquisition.start','pressure.release']:caps[key]['errors'] += [dict(code=e,meaning_zh=e,side_effects='partial') for e in ['TIMEOUT','MEMBRANE_WETTING','ORGANIC_BREAKTHROUGH','FEED_EMPTY','COLLECTION_FULL']]
    mechanism=dict(component_ids=['contactor_t','ptfe_membrane','bpr_aqueous','bpr_organic','aqueous_outlet','organic_outlet'],state_ids=evolving,meaning_zh='100µL有限两相持液、实测Kow与未标定传质/毛细膜分相',implementation='lumped',equations=['Vaq+Vorg=100uL;initialphaseholdupratio=feedflowratio;prewetted purecarriers','K=Corg/Caq=10**measured_logP;originalT/pHunresolved,hostreferenceassumption','G=k_mass_transfer*interfacial_area,hostcoefficients','dmorg/dt=Qorg*Corg_feed-Qorg*morg/Vorg+G*(maq/Vaq-morg/(K*Vorg))','dmaq/dt=Qaq*Caq_feed-Qaq*maq/Vaq-G*(maq/Vaq-morg/(K*Vorg))','5x5 augmentedmatrixexponential integrates twoholdupmasses andtwo advectiveoutletintegrals exactly forfixedflowcoefficients','organicpermeatingfraction=min(1,Lorg*CMP/Qorg)','effective waterentrypressure=2*gamma*abs(cos(theta))/r;irreversiblewettingflag if CMP>=entry duringflow','aqueousleakfraction=min(1,Lwet*CMP/Qaq) whenwetted;otherwise0','eachcarriervolumeandadvectivesolutemassconservativelysplitbetweentwooutlets;finitefeedandreceiverstepclipping','initialsolute=twofeedbottles+twoholdups+twooutlets;initialeachcarrier=feed+holdup+bothoutlets','BPR/CMP exponentialtargettracking;Porg_gauge=Paq_gauge-CMP;shutdowntrapsmodeledpressure,explicitrelease separate'],parameters={},source_ids=['manufacturer_primary','manufacturer_legacy','primary_membrane_paper','experimental_logp','simulation_assumption'],limitations=['60measuredcompiledKow,not60measuredFLLEXrecoveriesormembraneefficiencycurves.OriginalT/pHunresolved;notarbitrarysolventpairs.','Single dilute neutral nonreactivesolute,pre-equilibratedwater/1-octanol only;noacid/baseequilibria,salt,cosolvent,mixturesorprecipitation.','100uLtotalholdupknown;phaseholdupbyflowratio andwellmixedphases areassumptions,notresolvedslugfloworactualRTD.','Permeance,effectivepore/contactangle/interfacialtension andkLa parametersarehostassumptions;capillaryentrycalculation notactualbubblepointcertification.','Membranewettingtriggersduringmodeledflow only,notstaticliquidexposure;oncewettednoinplacehealingormembranereplacement.','Pressuretrackingisuncalibrated;gasconsumption,compressibility,upstreamflowresistance andleakage notmodeled.','ExternalpumpsandAsiaPressureController arehostboundaries,notintrinsicFLLEXpumpsorrealnetworkcoupling.','Flowmaystartonlyafteridealizedpressureconditioning;stopandpoweroffretainmass/pressure;poststopdiffusionandpassivepressuredecaynotadvanced.','Completed timedextractiondoesnotempty100uLholdup orreleasepressure;pressure.release isseparatehostoperation,notemergencyrelief.','Singlepanelcmp/mbarandbpr/bar modes areteachingdisplay;fullencoderfirmwaremenus andindicatorLEDs unsupported.','Noactualsoluteconcentrationsensor;puritydiagnostic ismodeledcarrierphasepurity,notchemicalproductpurity.','Trayandseparatoropeningarehostmanualposeproxies;noscrewtorque,hoseattachment,robotconstraintsorsealcontact.','Totalmassunknownnull;Matterix10kgfallbacknotmanufacturerdata;localmass/inertia/frictionunknown,URDF0.1kgproxies.','Handlingafterfeedloading,drain/cleanup/reload andsolventcompatibilitycertificationnotimplemented.','Hosttemperature293.15..303.15Kchosenconditionaldomain;nofactoryFLLEXthermalratingclaimedandnoTdependenceofKow.'])
    spec=importlib.util.spec_from_file_location('_fllex_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
    ann.PARAM_ZH.update({'max_phase_flow_m3_s':'宿主确认的每相最大液体流率','gas_supply_gauge_pa':'外部气源表压','gas_margin_pa':'供气相对BPR的假设余量','mass_transfer_coeff_m_s':'假设液液传质速度系数','interfacial_area_m2':'假设液滴接触面积','release_tau_s':'假设外部放空时间常数','organic_permeance_m3_s_pa':'假设有机润湿相膜液力导通率','wetted_aqueous_permeance_m3_s_pa':'湿润后水相假设膜液力导通率','effective_pore_radius_m':'毛细模型有效孔半径，非厂家孔径','contact_angle_rad':'假设水相对膜接触角','interfacial_tension_n_m':'假设两相界面张力','aqueous_collection_capacity_m3':'外部水相收集器容量','organic_collection_capacity_m3':'外部有机相收集器容量','max_dilute_concentration_kg_m3':'宿主稀溶液条件上限，不是实测溶解度'})
    for k in ['pressure.bpr_target_pa','pressure.bpr_gauge_pa','pressure.organic_gauge_pa']:slots[k]['reference_conditions']='相对于plant.ambient_pa的表压；Porg=Paq-CMP。'
    for k in ['pressure.cmp_target_pa','pressure.cmp_pa']:slots[k]['reference_conditions']='水相减有机相压差Paq-Porg；非表压或绝压。'
    for k in ['flow.aqueous_m3_s','flow.organic_m3_s']:slots[k]['reference_conditions']='外部泵所供预平衡相在宿主温度的实际液体体积流率；非标准气量。'
    ann.annotate_export(SLUG,caps,slots,{'liquid_liquid_extraction':mechanism},inputs={'plant.configure':(PLANT,EXAMPLE),'membrane.configure':(MEMBRANE,MEMBRANE_EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'liquid_liquid_extraction':mechanism})

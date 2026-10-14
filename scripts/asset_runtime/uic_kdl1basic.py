"""UIC KDL1 Basic: finite three-cell wiped film and internal-condenser proxy.

No calibrated RTD, energy closure, DSMC, safety controller, or factory firmware.
step is the only physical clock; host explicitly supplies all unknown coefficients.
"""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
import numpy as np
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.pccell_ed64002 import validate
ROOT=Path(__file__).resolve().parents[2];SLUG='uic_kdl1basic';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-038';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/uic_kdl1basic_evaporation_library/templates.json'
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
STREAM=dict(type='string',enum=['distillate','concentrate'])
def integer(lo,hi):return dict(type='integer',minimum=lo,maximum=hi,**{'x-unit':'1'})
PLANT=obj({'compatible_vacuum_confirmed':BOOL,'compatible_heating_confirmed':BOOL,'cooling_confirmed':BOOL,'vent_confirmed':BOOL,'cold_trap_confirmed':BOOL,'low_viscosity_liquid_reviewed':BOOL,'ideal_mixture_reviewed':BOOL,'vacuum_min_pa':number('Pa',5,101325),'heating_max_k':number('K',293.15,473.15),'pressure_tau_s':number('s',.1,1000),'thermal_tau_s':number('s',.1,10000),'initial_temperature_k':number('K',293.15,313.15),'max_feed_kg_s':number('kg/s',1e-9,.001),'max_speed_rad_s':number('rad/s',.1,300),'rotor_acceleration_rad_s2':number('rad/s^2',.1,100),'residence_reference_s':number('s',1,1000),'speed_reference_rad_s':number('rad/s',.1,300),'wetting_thickness_m':number('m',1e-6,.001),'maximum_film_thickness_m':number('m',1e-5,.005),'effective_accommodation':number('1',0,1),'condenser_efficiency':number('1',0,1),'condenser_delta_k':number('K',1,200),'trap_efficiency':number('1',0,1),'trap_capacity_m3':number('m^3',1e-8,.001),'collision_diameter_m':number('m',1e-10,1e-8),'vapor_gap_m':number('m',.001,.1),'coefficient_evidence':TEXT})
PLANT_EXAMPLE=dict(compatible_vacuum_confirmed=True,compatible_heating_confirmed=True,cooling_confirmed=True,vent_confirmed=True,cold_trap_confirmed=True,low_viscosity_liquid_reviewed=True,ideal_mixture_reviewed=True,vacuum_min_pa=5.,heating_max_k=473.15,pressure_tau_s=1.,thermal_tau_s=2.,initial_temperature_k=303.15,max_feed_kg_s=.15/3600,max_speed_rad_s=100.,rotor_acceleration_rad_s2=10.,residence_reference_s=6.,speed_reference_rad_s=10.,wetting_thickness_m=.0001,maximum_film_thickness_m=.001,effective_accommodation=.00002,condenser_efficiency=.98,condenser_delta_k=20.,trap_efficiency=.95,trap_capacity_m3=.00005,collision_diameter_m=5e-10,vapor_gap_m=.013,coefficient_evidence='explicit_host_assumptions_not_UIC_calibration')
METHOD=obj({'wall_temperature_k':number('K',293.15,473.15),'condenser_temperature_k':number('K',253.15,473.15),'pressure_pa':number('Pa',5,101325),'feed_rate_kg_s':number('kg/s',0,.001),'wiper_speed_rad_s':number('rad/s',0,300),'duration_s':number('s',.1,86400),'timeout_s':number('s',.1,86400)})
METHOD_EXAMPLE=dict(wall_temperature_k=323.15,condenser_temperature_k=293.15,pressure_pa=5.,feed_rate_kg_s=.1/3600,wiper_speed_rad_s=10.,duration_s=120.,timeout_s=150.)
SPECIES=obj({'substance_id':TEXT,'fit_id':TEXT,'amount_mol':number('mol',1e-12,1000),'reference_molar_volume_m3_mol':number('m^3/mol',1e-6,.01)})
SAMPLE=obj({'sample_id':TEXT,'species':dict(type='array',items=SPECIES,minItems=1,maxItems=4),'reference_temperature_k':number('K',293.15,313.15),'room_temperature_liquid_confirmed':BOOL})
def example_sample():return dict(sample_id='teaching_hexane_heptane',species=[dict(substance_id='hexane',fit_id='0',amount_mol=.03,reference_molar_volume_m3_mol=.00013),dict(substance_id='heptane',fit_id='1',amount_mol=.03,reference_molar_volume_m3_mol=.000146)],reference_temperature_k=303.15,room_temperature_liquid_confirmed=True)
MEASURE=obj({'phase':PHASE,'fault':TEXT,'wall_temperature_k':number('K',293.15,473.15),'condenser_temperature_k':number('K',253.15,473.15),'pressure_pa':number('Pa',5,101325),'speed_rad_s':number('rad/s',0,300),'feed_rate_kg_s':number('kg/s',0,.001),'knudsen_estimate':number('1',0),'evidence':TEXT,'template_source':TEXT})
PRODUCT_ROW=obj({'substance_id':TEXT,'initial_mol':number('mol',0),'feed_mol':number('mol',0),'film_mol':dict(type='array',items=number('mol',0),minItems=3,maxItems=3),'distillate_mol':dict(type='array',items=number('mol',0),minItems=3,maxItems=3),'concentrate_mol':dict(type='array',items=number('mol',0),minItems=3,maxItems=3),'trap_mol':number('mol',0),'exhaust_mol':number('mol',0)})
POINT=obj({'time_s':number('s',0),'wall_temperature_k':number('K',293.15,473.15),'condenser_temperature_k':number('K',253.15,473.15),'pressure_pa':number('Pa',5,101325),'speed_rad_s':number('rad/s',0,300)})
CAPS={
 'plant.configure':(PLANT,obj({'configured':BOOL}),['port_vacuum','port_heating_in','port_cooling_in','cold_trap'],'宿主声明相容公用工程与未标定系数，非已连接共享设备','configuration','evaluator',PLANT_EXAMPLE),
 'method.configure':(METHOD,obj({'configured':BOOL}),['feed_valve','motor','heating_jacket','internal_condenser'],'设置Basic温压/进料/转速；不等于实际已达到','configuration','agent',METHOD_EXAMPLE),
 'sample.configure':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['feed_vessel'],'常温低黏度有限样品初始化，250mL上限；不是机器人加料','configuration','evaluator',example_sample()),
 'receiver.exchange':(obj({'stream':STREAM,'slot':integer(0,1)}),obj({'stream':STREAM,'slot':integer(0,1),'bottle':integer(0,2)}),['distillate_receiver_2','concentrate_receiver_2'],'停机通气后宿主将备用瓶换入一个真空瓶位；不移动几何冒充已执行','configuration','evaluator',dict(stream='distillate',slot=0)),
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['button_power'],'虚拟电源，断电取消并保留库存','device_command','agent',dict(enabled=True)),
 'temperature.set':(obj({'temperature_k':number('K',293.15,473.15)}),obj({'temperature_k':number('K',293.15,473.15)}),['heating_jacket'],'只改蒸发壁温度目标','device_command','agent',dict(temperature_k=323.15)),
 'condenser.set':(obj({'temperature_k':number('K',253.15,473.15)}),obj({'temperature_k':number('K',253.15,473.15)}),['internal_condenser'],'只改内冷凝器边界温度目标','device_command','agent',dict(temperature_k=293.15)),
 'vacuum.set':(obj({'pressure_pa':number('Pa',5,101325)}),obj({'pressure_pa':number('Pa',5,101325)}),['port_vacuum'],'基本版5Pa最低，外部泵能力也须覆盖','device_command','agent',dict(pressure_pa=5.)),
 'feed.set':(obj({'rate_kg_s':number('kg/s',0,.001)}),obj({'rate_kg_s':number('kg/s',0,.001)}),['feed_valve'],'宿主手动给料阀边界，不是自动泵反馈控制','configuration','evaluator',dict(rate_kg_s=.08/3600)),
 'wiper.set':(obj({'speed_rad_s':number('rad/s',0,300)}),obj({'speed_rad_s':number('rad/s',0,300)}),['knob_speed','wiper_rotor'],'速度目标；最大值为宿主保护不是原厂额定','device_command','agent',dict(speed_rad_s=12.)),
 'fraction.select':(obj({'stream':STREAM,'slot':integer(0,1)}),obj({'stream':STREAM,'slot':integer(0,1)}),['distillate_valve','concentrate_valve'],'宿主手动切换两个真空瓶位；阀位未到暂停物质转移','configuration','evaluator',dict(stream='distillate',slot=1)),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['evaporator','feed_vessel','wiper_rotor'],'开始短程蒸馏教学作业；未达到温压转速时等待','device_command','agent',{}),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['motor','feed_valve'],'取消但保留料、膜、两个产品流、冷阱及排气','device_command','agent',{}),
 'distillation.measure':(obj({}),MEASURE,['display_digits'],'温压/速度理想观测，不提供样品组成真值','query','agent',{}),
 'product.read':(obj({}),obj({'records':dict(type='array',items=PRODUCT_ROW),'evidence':TEXT,'template_source':TEXT}),['distillate_valve','concentrate_valve'],'逐物质/逐瓶有限库存真值，仅宿主评价器','query','evaluator',{}),
 'history.read':(obj({}),obj({'records':dict(type='array',items=POINT),'evidence':TEXT}),['display_digits'],'宿主时间记录；非原厂数据格式','query','agent',{}),
 'reference.lookup':(obj({'substance_id':TEXT,'fit_id':TEXT,'temperature_k':number('K',293.15,473.15)}),obj({'pressure_pa':number('Pa',0),'source':TEXT,'evidence':TEXT,'template_source':TEXT}),['evaporator'],'原NIST温区内蒸气压参考，不能外推','query','agent',dict(substance_id='hexane',fit_id='0',temperature_k=313.15))}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,zh,o='derived',v='agent',c='display_digits',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=zh
for n in ['plant','method','sample']:slot(n+'.configured',BOOL,'1',n+'配置状态')
slot('power.enabled',BOOL,'1','虚拟电源','setpoint',w=('power.set','enabled'))
slot('cycle.phase',PHASE,'1','作业状态');slot('cycle.fault',TEXT,'1','终止原因');slot('cycle.elapsed_s',number('s',0),'s','累计作业时间')
for key,low,high,cap,comp in [('wall',293.15,473.15,'temperature.set','heating_jacket'),('condenser',253.15,473.15,'condenser.set','internal_condenser')]:
 slot('thermal.'+key+'_target_k',number('K',low,high),'K',key+'温度目标','setpoint',c=comp,w=(cap,'temperature_k'));slot('thermal.'+key+'_temperature_k',number('K',low,high),'K',key+'温度理想观测，无传感噪声/校准',c=comp)
slot('vacuum.target_pa',number('Pa',5,101325),'Pa','绝对压力目标，最低5Pa','setpoint',c='port_vacuum',w=('vacuum.set','pressure_pa'));slot('vacuum.pressure_pa',number('Pa',5,101325),'Pa','腔体绝压一阶外部边界代理，非已标定真空系统',c='port_vacuum')
slot('wiper.target_rad_s',number('rad/s',0,300),'rad/s','刮膜速度目标，原厂最高转速未知','setpoint',c='knob_speed',w=('wiper.set','speed_rad_s'));slot('wiper.speed_rad_s',number('rad/s',0,300),'rad/s','刮膜理想速度，未做满速接触验证',c='wiper_rotor');slot('wiper.angle_rad',number('rad',-math.pi,math.pi),'rad','旋转笼角度按2pi折返，仅姿态映射',c='wiper_rotor')
slot('feed.target_kg_s',number('kg/s',0,.001),'kg/s','手动进料质量率宿主目标','setpoint','evaluator','feed_valve',('feed.set','rate_kg_s'));slot('feed.rate_kg_s',number('kg/s',0,.001),'kg/s','本步实际注入质量率，启动/阀切换/满膜暂停',c='feed_valve')
for stream in ['distillate','concentrate']:
 slot(stream+'.slot',integer(0,1),'1','两个在位真空接收瓶位的手动目标','setpoint',c=stream+'_valve');slot(stream+'.angle_rad',number('rad',0,math.pi),'rad','手动阀角度教学代理，不是电机自动瓶盘',c=stream+'_valve')
 slot(stream+'.bottles',dict(type='array',items=integer(0,2),minItems=2,maxItems=2),'1','两个在位槽到三只有限接收瓶账本的映射','derived','evaluator',stream+'_valve')
slot('transport.knudsen_estimate',number('1',0),'1','按假设碰撞直径/间隙估算Kn；不保证自由分子流',c='evaporator')
slot('panel.knob_angle_rad',number('rad',-5*math.pi/6,5*math.pi/6),'rad','宿主转速目标按已声明最大值映射到±150度；只过程到场景，不接受手旋输入',c='knob_speed')
slot('truth.feed_mol',number('mol',0),'mol','剩余进料真值','process_truth','evaluator','feed_vessel');slot('truth.film_mol',number('mol',0),'mol','三个膜单元总库存真值','process_truth','evaluator','evaporator');slot('truth.film_thickness_m',number('m',0),'m','参考液体体积/0.01m²，不是实测膜厚','process_truth','evaluator','evaporator');slot('truth.residence_s',number('s',0),'s','宿主转速相关三CSTR停留时间参数，非真实RTD','process_truth','evaluator','evaporator')

class UicKdl1basicRuntime:
 def __init__(self,instance_id='kdl1_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.plant=None;self.method=None;self.sample=None;self.powered=False;self.phase='idle';self.fault='';self.elapsed_s=0.;self.wall_t=303.15;self.cond_t=303.15;self.pressure=101325.;self.speed=0.;self.angle=0.;self.rate=0.;self.kn=0.;self.tau=0.;self.feed=np.zeros(0);self.film=np.zeros((3,0));self.products=np.zeros((2,3,0));self.trap=np.zeros(0);self.exhaust=np.zeros(0);self.initial=np.zeros(0);self.vm=np.zeros(0);self.mw=np.zeros(0);self.selection=[0,0];self.valve_angles=[0.,0.];self.bottles=[[0,1],[0,1]];self.history=[];self.operation_id=None;self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ['agent','evaluator','debug']:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return UicKdl1basicSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _fit(self,sid,fid):
  if sid not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
  for f in self.templates[sid]['fits']:
   if f['fit_id']==fid:return f
  raise ActionRejected('UNKNOWN_REFERENCE')
 def _psat(self,sid,fid,t):
  f=self._fit(sid,fid);lo,hi=f['runtime_temperature_range_k']
  if not lo<=t<=hi:raise ActionRejected('REFERENCE_RANGE')
  return 1e5*10**(f['A']-f['B_k']/(t+f['C_k']))
 def _values(self):
  m=self.method or METHOD_EXAMPLE
  knob=-5*math.pi/6 if not self.method else (m['wiper_speed_rad_s']/self.plant['max_speed_rad_s']-.5)*5*math.pi/3
  return {**{n+'.configured':bool(getattr(self,n)) for n in ['plant','method','sample']},'panel.knob_angle_rad':knob,'power.enabled':self.powered,'cycle.phase':self.phase,'cycle.fault':self.fault,'cycle.elapsed_s':self.elapsed_s,'thermal.wall_target_k':m['wall_temperature_k'],'thermal.wall_temperature_k':self.wall_t,'thermal.condenser_target_k':m['condenser_temperature_k'],'thermal.condenser_temperature_k':self.cond_t,'vacuum.target_pa':m['pressure_pa'],'vacuum.pressure_pa':self.pressure,'wiper.target_rad_s':m['wiper_speed_rad_s'],'wiper.speed_rad_s':self.speed,'wiper.angle_rad':self.angle,'feed.target_kg_s':m['feed_rate_kg_s'],'feed.rate_kg_s':self.rate,'distillate.slot':self.selection[0],'concentrate.slot':self.selection[1],'distillate.angle_rad':self.valve_angles[0],'concentrate.angle_rad':self.valve_angles[1],'distillate.bottles':list(self.bottles[0]),'concentrate.bottles':list(self.bottles[1]),'transport.knudsen_estimate':self.kn,'truth.feed_mol':float(self.feed.sum()),'truth.film_mol':float(self.film.sum()),'truth.film_thickness_m':float(self.film.sum(axis=0)@self.vm)/.01,'truth.residence_s':self.tau}
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ['debug','evaluator']})
 def debug_snapshot(self):return copy.deepcopy(dict(values=self._values(),plant=self.plant,method=self.method,sample=self.sample,feed=self.feed.tolist(),film=self.film.tolist(),products=self.products.tolist(),trap=self.trap.tolist(),exhaust=self.exhaust.tolist(),bottles=self.bottles,history=self.history,time=self.time_s,revision=self.revision,operation=self.operation_id))
 def _check_method(self,m):
  p=self.plant
  if not p or m['pressure_pa']<p['vacuum_min_pa'] or m['wall_temperature_k']>p['heating_max_k'] or m['feed_rate_kg_s']>p['max_feed_kg_s'] or m['wiper_speed_rad_s']>p['max_speed_rad_s']:raise ActionRejected('HOST_LIMIT')
  if self.sample and not self.fit_lo<=m['wall_temperature_k']<=self.fit_hi:raise ActionRejected('REFERENCE_RANGE')
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if self.phase=='running' and capability in ['plant.configure','method.configure','sample.configure','receiver.exchange']:raise ActionRejected('BUSY')
  if capability=='plant.configure':
   if self.plant or not all(a[k] for k in ['compatible_vacuum_confirmed','compatible_heating_confirmed','cooling_confirmed','vent_confirmed','cold_trap_confirmed','low_viscosity_liquid_reviewed','ideal_mixture_reviewed']) or a['maximum_film_thickness_m']<a['wetting_thickness_m']:raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);self.wall_t=self.cond_t=a['initial_temperature_k'];out=dict(configured=True)
  elif capability=='method.configure':self._check_method(a);self.method=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='sample.configure':
   if self.sample or not self.method or not a['room_temperature_liquid_confirmed']:raise ActionRejected('PRECONDITION_FAILED')
   ss=a['species'];n=np.array([s['amount_mol'] for s in ss]);vm=np.array([s['reference_molar_volume_m3_mol'] for s in ss]);volume=float(n@vm)
   if len({s['substance_id'] for s in ss})!=len(ss) or not 0<volume<=.00025:raise ActionRejected('FEED_CAPACITY')
   fits=[self._fit(s['substance_id'],s['fit_id']) for s in ss];lo=max(f['runtime_temperature_range_k'][0] for f in fits);hi=min(f['runtime_temperature_range_k'][1] for f in fits)
   for s in ss:
    record=self.templates[s['substance_id']];mp=record.get('melting_point_k');bp=record.get('normal_boiling_point_k');t=a['reference_temperature_k']
    known_psat=[self._psat(s['substance_id'],f['fit_id'],t) for f in record['fits'] if f['runtime_temperature_range_k'][0]<=t<=f['runtime_temperature_range_k'][1]]
    if (mp is not None and t<mp) or (bp is not None and t>=bp) or (known_psat and min(known_psat)>101325.):raise ActionRejected('ROOM_TEMPERATURE_PHASE')
   if lo>=hi or not lo<=self.method['wall_temperature_k']<=hi:raise ActionRejected('REFERENCE_RANGE')
   self.sample=copy.deepcopy(a);self.initial=n;self.vm=vm;self.mw=np.array([self.templates[s['substance_id']]['molar_mass_kg_mol'] for s in ss]);self.feed=n.copy();self.film=np.zeros((3,len(n)));self.products=np.zeros((2,3,len(n)));self.trap=np.zeros(len(n));self.exhaust=np.zeros(len(n));self.fit_lo=lo;self.fit_hi=hi;self.coeff=np.array([[f['A'],f['B_k'],f['C_k']] for f in fits]);out=dict(configured=True,attachment_event='host_finite_feed_initialization_not_robot_attachment')
  elif capability=='receiver.exchange':
   j=0 if a['stream']=='distillate' else 1
   if not self.sample or self.phase=='running' or self.pressure<100000 or self.speed>1e-6 or 2 in self.bottles[j]:raise ActionRejected('EXCHANGE_INTERLOCK')
   self.bottles[j][a['slot']]=2;out=dict(**a,bottle=2)
  elif capability=='power.set':
   self.powered=a['enabled']
   if not self.powered and self.phase=='running':self.phase='cancelled'
   out=a
  elif capability in ['temperature.set','condenser.set','vacuum.set','feed.set','wiper.set']:
   if not self.method:raise ActionRejected('PRECONDITION_FAILED')
   key={'temperature.set':'wall_temperature_k','condenser.set':'condenser_temperature_k','vacuum.set':'pressure_pa','feed.set':'feed_rate_kg_s','wiper.set':'wiper_speed_rad_s'}[capability];m=copy.deepcopy(self.method);m[key]=next(iter(a.values()));self._check_method(m);self.method=m;out=a
  elif capability=='fraction.select':
   j=0 if a['stream']=='distillate' else 1
   if not self.sample or float(self.products[j,self.bottles[j][a['slot']]]@self.vm)>=.0001-1e-12:raise ActionRejected('RECEIVER_FULL')
   self.selection[j]=a['slot'];out=a
  elif capability=='acquisition.start':
   if not self.powered or not self.sample or self.phase not in ['idle','cancelled'] or self.elapsed_s>=min(self.method['duration_s'],self.method['timeout_s']) or self.method['wiper_speed_rad_s']<=0:raise ActionRejected('PRECONDITION_FAILED')
   self._check_method(self.method);self.phase='running';self.fault='';self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase=='running':self.phase='cancelled'
   out=dict(phase=self.phase)
  elif capability=='distillation.measure':out=dict(phase=self.phase,fault=self.fault,wall_temperature_k=self.wall_t,condenser_temperature_k=self.cond_t,pressure_pa=self.pressure,speed_rad_s=self.speed,feed_rate_kg_s=self.rate,knudsen_estimate=self.kn,evidence='real_NIST_Psat_synthetic_host_film_and_molecular_transport',template_source=TEMPLATE_REL)
  elif capability=='product.read':out=dict(records=[dict(substance_id=s['substance_id'],initial_mol=float(self.initial[i]),feed_mol=float(self.feed[i]),film_mol=self.film[:,i].tolist(),distillate_mol=self.products[0,:,i].tolist(),concentrate_mol=self.products[1,:,i].tolist(),trap_mol=float(self.trap[i]),exhaust_mol=float(self.exhaust[i])) for i,s in enumerate((self.sample or {}).get('species',[]))],evidence='simulation_truth_not_measured_KDL1_fractions',template_source=TEMPLATE_REL)
  elif capability=='history.read':out=dict(records=copy.deepcopy(self.history),evidence='synthetic_time_history_not_factory_log')
  elif capability=='reference.lookup':out=dict(pressure_pa=self._psat(a['substance_id'],a['fit_id'],a['temperature_k']),source=self.templates[a['substance_id']]['source'],evidence='derived',template_source=TEMPLATE_REL)
  validate(CAPS[capability][1],out)
  if CAPS[capability][4]!='query':self.revision+=1
  return copy.deepcopy(out)
 def _transport(self,h):
  p=self.plant;m=self.method;area=.01/3;tot=self.film.sum(axis=1);x=np.divide(self.film,tot[:,None],out=np.zeros_like(self.film),where=tot[:,None]>0);self.tau=p['residence_reference_s']*p['speed_reference_rad_s']/max(self.speed,.01);cell_tau=max(.1,self.tau/3)
  ps=1e5*10**(self.coeff[:,0]-self.coeff[:,1]/(self.wall_t+self.coeff[:,2]));pb=(x*ps).sum(axis=1);back=np.maximum(0,1-np.divide(self.pressure,pb,out=np.full_like(pb,np.inf),where=pb>0));wet=np.minimum(1,(self.film@self.vm)/(area*p['wetting_thickness_m']))
  evap=area*wet[:,None]*p['effective_accommodation']*x*ps/np.sqrt(2*math.pi*self.mw*8.314462618*self.wall_t)*back[:,None]*h;adv=self.film*(1-math.exp(-h/cell_tau));out=evap+adv;local=np.minimum(1,np.min(np.divide(.8*self.film,out,out=np.full_like(out,np.inf),where=out>0),axis=1));evap*=local[:,None];adv*=local[:,None]
  eta=p['condenser_efficiency']*min(1,max(0,(self.wall_t-self.cond_t)/p['condenser_delta_k']));dist=eta*evap.sum(axis=0);conc=adv[-1].copy();dest=[self.bottles[j][self.selection[j]] for j in range(2)];scale=1.
  for j,n in enumerate([dist,conc]):
   vol=float(n@self.vm)
   if vol>0:scale=min(scale,max(0,(.0001-float(self.products[j,dest[j]]@self.vm))/vol))
  evap*=scale;adv*=scale;dist*=scale;conc*=scale
  # Limit downstream inflow by explicit finite film capacity, keeping paired transfers.
  capacity=area*p['maximum_film_thickness_m']
  for j in [1,0]:
   room=max(0,capacity-float((self.film[j+1]-evap[j+1]-adv[j+1])@self.vm));vol=float(adv[j]@self.vm)
   if vol>room and vol>0:adv[j]*=room/vol
  delta=-evap-adv;delta[1:]+=adv[:-1];self.film+=delta;self.products[0,dest[0]]+=dist;self.products[1,dest[1]]+=conc
  escaped=(1-eta)*evap.sum(axis=0);capture=escaped*p['trap_efficiency'];vol=float(capture@self.vm);room=max(0,p['trap_capacity_m3']-float(self.trap@self.vm))
  if vol>room and vol>0:capture*=room/vol
  self.trap+=capture;self.exhaust+=escaped-capture
  mass=float(self.feed@self.mw)
  if mass>0:
   take=self.feed*min(1,m['feed_rate_kg_s']*h/mass);room=max(0,capacity-float(self.film[0]@self.vm));vol=float(take@self.vm)
   if vol>room and vol>0:take*=room/vol
   self.feed-=take;self.film[0]+=take;self.rate=float(take@self.mw)/h
  if scale<1-1e-9:self.phase='failed';self.fault='RECEIVER_CAPACITY_TERMINATION'
 def step(self,dt_s):
  validate(number('s',0,86400),dt_s);remaining=float(dt_s)
  while remaining>1e-12:
   h=min(remaining,.1);p=self.plant;m=self.method;running=self.phase=='running';self.rate=0.
   if running:
    if self.elapsed_s>=m['duration_s']-1e-9:self.phase='succeeded';running=False
    elif self.elapsed_s>=m['timeout_s']-1e-9:self.phase='failed';self.fault='TIMEOUT';running=False
    else:h=min(h,m['duration_s']-self.elapsed_s,m['timeout_s']-self.elapsed_s)
   if p:
    target_speed=m['wiper_speed_rad_s'] if running else 0.;old_speed=self.speed;self.speed+=max(-p['rotor_acceleration_rad_s2']*h,min(p['rotor_acceleration_rad_s2']*h,target_speed-self.speed));self.angle=((self.angle+.5*(old_speed+self.speed)*h+math.pi)%(2*math.pi))-math.pi
    for j in range(2):self.valve_angles[j]+=max(-h,min(h,self.selection[j]*math.pi-self.valve_angles[j]))
    target_p=m['pressure_pa'] if running else 101325.;self.pressure=target_p+(self.pressure-target_p)*math.exp(-h/p['pressure_tau_s'])
    wall=m['wall_temperature_k'] if running else p['initial_temperature_k'];cond=m['condenser_temperature_k'] if running else p['initial_temperature_k'];self.wall_t=wall+(self.wall_t-wall)*math.exp(-h/p['thermal_tau_s']);self.cond_t=cond+(self.cond_t-cond)*math.exp(-h/p['thermal_tau_s']);self.kn=1.380649e-23*self.wall_t/(math.sqrt(2)*math.pi*p['collision_diameter_m']**2*self.pressure*p['vapor_gap_m'])
    if running:
     ready=self.fit_lo<=self.wall_t<=self.fit_hi and abs(self.wall_t-wall)<.1 and abs(self.pressure-target_p)<max(.01,.001*target_p) and self.speed>0 and abs(self.speed-target_speed)<.01 and all(abs(self.valve_angles[j]-self.selection[j]*math.pi)<1e-9 for j in range(2))
     if ready:self._transport(h)
     self.elapsed_s+=h
   self.time_s+=h;self.revision+=1;remaining-=h
   if self.sample:self.history.append(dict(time_s=self.time_s,wall_temperature_k=self.wall_t,condenser_temperature_k=self.cond_t,pressure_pa=self.pressure,speed_rad_s=self.speed))
  if self.phase=='running':
   if self.elapsed_s>=self.method['duration_s']-1e-9:self.phase='succeeded'
   elif self.elapsed_s>=self.method['timeout_s']-1e-9:self.phase='failed';self.fault='TIMEOUT'
  return self.read()

class UicKdl1basicSession:
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
        meta=export_uic_kdl1basic_contract_metadata()
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('distillation.measure'))

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])

def export_uic_kdl1basic_contract_metadata():
    caps={};slots={}
    for k,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions='绝对压力；宿主理想观测，非原厂传感器' if 'pressure' in k or 'vacuum' in k else None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'UicKdl1basicSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    immediate={'plant.configure':['plant.configured','thermal.wall_temperature_k','thermal.condenser_temperature_k'],'method.configure':['method.configured','thermal.wall_target_k','thermal.condenser_target_k','vacuum.target_pa','wiper.target_rad_s','feed.target_kg_s','panel.knob_angle_rad'],'sample.configure':['sample.configured','truth.feed_mol','truth.film_mol','truth.film_thickness_m'],'receiver.exchange':['distillate.bottles','concentrate.bottles'],'power.set':['power.enabled','cycle.phase'],'temperature.set':['thermal.wall_target_k'],'condenser.set':['thermal.condenser_target_k'],'vacuum.set':['vacuum.target_pa'],'feed.set':['feed.target_kg_s'],'wiper.set':['wiper.target_rad_s','panel.knob_angle_rad'],'fraction.select':['distillate.slot','concentrate.slot'],'acquisition.start':['cycle.phase','cycle.fault'],'acquisition.stop':['cycle.phase']}
    advancing=['cycle.phase','cycle.fault','cycle.elapsed_s','thermal.wall_temperature_k','thermal.condenser_temperature_k','vacuum.pressure_pa','wiper.speed_rad_s','wiper.angle_rad','feed.rate_kg_s','distillate.angle_rad','concentrate.angle_rad','transport.knudsen_estimate','truth.feed_mol','truth.film_mol','truth.film_thickness_m','truth.residence_s']
    for k,(inp,out,components,meaning,kind,vis,example) in CAPS.items():
        async_=k=='acquisition.start'
        caps[k]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.uic_kdl1basic.UicKdl1basicSession.invoke',invocation=f'session.invoke(instance_id="kdl1_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if kind=='query' else [dict(description_zh='外部边界兼容性显式确认、有限常温液样与公共拟合温区、Basic及宿主限值；换瓶需停转通气；拒绝不改状态',state_ids=['cycle.phase','plant.configured','sample.configured','power.enabled'],enforced_by='runtime',check='UicKdl1basicRuntime.invoke capability-specific guards')],state_effects=[dict(state_id=i,timing='immediate',description_zh='只更新目标/配置或操作状态；不推进时间') for i in immediate.get(k,[])]+([dict(state_id=i,timing='on_step',description_zh='宿主step推进三单元有限膜与内冷凝收集') for i in advancing] if async_ else []),observes=[],model_effects=dict(mode='none',description_zh='没有真实场景求值证据前不声明联动；换瓶仅库存连接账本',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='累计规定时长；超时/接收瓶满失败；结束可以保留剩余料和膜' if async_ else '同步返回，设置成功不代表状态已到达',completion_state_ids=['cycle.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['finite_feed_three_film_cells','internal_condenser_two_product_streams','two_vacuum_cuts_and_one_spare_per_stream','explicit_compatible_external_utilities','principal_request_cache'],partial_failure_zh='取消/断电/超时/满瓶保留逐物质进料、膜、各瓶、冷阱、排气；停机关闭物质输送，物理温压/转速继续衰减，不提供安全冷却认证。'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT','UNKNOWN_REFERENCE','REFERENCE_RANGE','HOST_LIMIT','FEED_CAPACITY','ROOM_TEMPERATURE_PHASE','EXCHANGE_INTERLOCK','RECEIVER_FULL']],example_arguments=copy.deepcopy(example),verification=[],limitations=['教学近似，不是厂家固件/自动换瓶/安全系统；宿主文件进程隔离未验证。'],source_ids=['manufacturer_primary','simulation_assumption'])
        if async_:caps[k]['errors'] += [dict(code=e,meaning_zh=e,side_effects='partial') for e in ['TIMEOUT','RECEIVER_CAPACITY_TERMINATION']]
    mechanism=dict(component_ids=['feed_vessel','evaporator','wiper_rotor','internal_condenser','distillate_valve','concentrate_valve','cold_trap'],state_ids=list(slots),meaning_zh='三串联有限薄膜库存、简化HK通量、内冷凝两路有限接收与冷阱排气守恒',implementation='lumped',equations=['Psat=1e5*10**(A-B/(T+C)); real NIST liquid fits only inside chosen domain','J_i=alpha_host*x_i*Psat_i(Twall)/sqrt(2*pi*M_i*R*Twall)*max(0,1-P/sum(x*Psat)); ideal mixture and backpressure proxy','three finite CSTR cells; tau=tau_ref*omega_ref/max(omega,0.01); each liquid outflow=n*(1-exp(-dt/(tau/3)))','A_wet=(0.01/3)*min(1,V_cell/(0.01/3*delta_host)); film thickness=sum(reference volume)/0.01','evap and liquid outflow limited jointly by available donor species; paired transfers conserve every species','internal condenser eta=eta_host*clamp((Twall-Tcond)/deltaT_host,0,1); NOT species phase equilibrium or energy closure','uncaptured vapor goes to finite trap then exhaust ledger; no disappearing mass','cold-trap and each of six0.1L bottles have finite reference-volume capacity; only two bottle positions active per stream','Kn=kB*T/(sqrt(2)*pi*d_host^2*P*gap_host) diagnostic only, not validity certification','initial feed<=0.25L; dry cells initially empty; heat and pressure approach host boundary first-order','step<=0.1s advances all physics; queries pure; mass transfer stops while manual routing or conditioning incomplete'],parameters={},source_ids=['manufacturer_primary','manufacturer_mechanism','nist_antoine','hertz_knudsen','simulation_assumption'],limitations=['81 pure-substance references are not measured KDL1 product curves; nonideal activity, high-boiler mixtures, material compatibility unverified.','No energy closure, latent/sensible heat, film temperature profile, viscosity, entrainment, decomposition, crystallization or pump curve. Wall temperature is film-surface proxy.','Hertz-Knudsen equation motivated by molecular flux; NIST paper is sublimation research and supplies no liquid KDL1 calibration. Effective accommodation and backpressure are host approximations.','Three serial CSTRs give an assumed RTD, not manufacturer data; wiper geometry and liquid-film thickness/transfer need independent validation.','Kn may be below1 even atBasic5Pa; no DSMC/continuum transition solver. Diagnostic never asserts free-molecular performance.','Condensation efficiency is common to species and empirical; no condenser liquid holdup or re-evaporation. Trap inventory finite but no refrigeration energy model.','Two active vacuum cuts per stream; third flask spare. Host exchange changes ledger binding only; geometric bottle replacement/robotics not implemented.','Unknown real speed/flow maximum, masses and dimensions staynull. Numerical host guards/0.8×0.4×1.2m photo envelope are not ratings.','Default shared pump200Pa cannot deliver5Pa; shared bath150°C cannot provide full200°C heating. Both references remainnot_connected.','Basic only: no250°C thermostat,0.001mbar diffusion pump, heated feed or diaphragm feed pump.','Cancellation freezes material transport but pressure/temperature/rotation settle; not a physical safe-state or validated post-stop evaporation model.','Public session filtering not deployed file/process isolation; Matterix acknowledgment is separate from native operation completion.'])
    spec=importlib.util.spec_from_file_location('_kdl1_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
    ann.annotate_export(SLUG,caps,slots,{'distillation':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'distillation':mechanism})

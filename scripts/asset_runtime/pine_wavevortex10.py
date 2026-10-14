"""CHEM-107 explicit external-bipotentiostat RRDE teaching subset; SI, trusted host."""
from pathlib import Path
import copy,math,json,importlib.util
PROTOCOL_VERSION='1.0.0';SLUG='pine_wavevortex10';CATALOG_ID='CHEM-107';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];TEMPLATE_REL='assets/expansion_batch04/t4_measure/'+SLUG+'/diffusion_library.json'
class ActionRejected(ValueError):pass
def number(unit='1',lo=None,hi=None):
 d={'type':'number','x-unit':unit}
 if lo is not None:d['minimum']=lo
 if hi is not None:d['maximum']=hi
 return d
def obj(p):return dict(type='object',properties=p,required=list(p),additionalProperties=False)
def arr(v):return dict(type='array',items=v)
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
def enum(v):return {'type':'string','enum':v}
TEXT={'type':'string'};BOOL={'type':'boolean'};PHASE=enum(['idle','running','succeeded','cancelled','failed'])
def validate(s,v):
 if 'anyOf' in s:
  for q in s['anyOf']:
   try:validate(q,v);return
   except ActionRejected:pass
  raise ActionRejected('INVALID_ARGUMENT')
 t=s.get('type')
 if t in ('number','integer'):
  if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or (t=='integer' and type(v) is not int) or not s.get('minimum',-math.inf)<=v<=s.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='boolean' and type(v) is not bool:raise ActionRejected('INVALID_ARGUMENT')
 elif t=='string' and not isinstance(v,str):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='null' and v is not None:raise ActionRejected('INVALID_ARGUMENT')
 elif t=='object':
  if not isinstance(v,dict) or not set(s.get('required',[]))<=v.keys() or set(v)-set(s['properties']):raise ActionRejected('INVALID_ARGUMENT')
  for k,x in v.items():validate(s['properties'][k],x)
 elif t=='array':
  if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
  for x in v:validate(s['items'],x)
 if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')
RPM=2*math.pi/60;F=96485.33212;R=8.31446261815324;T=298.15;DT=.01;AREA=math.pi*(.005/2)**2

def collection_geometry(d1=.005,d2=.0065,d3=.0075):
 def G(x):
  u=x**(1/3);return .25+math.sqrt(3)/(4*math.pi)*math.log((u+1)**3/(x+1))+3/(2*math.pi)*math.atan((2*u-1)/math.sqrt(3))
 s=d3/d1;a=(d2/d1)**3-1;b=s**3-(d2/d1)**3;c=a/b
 return 1-s*s+b**(2/3)-G(c)-b**(2/3)*G(a)+s*s*G(c*s**3)
N_GEOM=collection_geometry()
PLANT=obj({'rotor_inertia_kg_m2':number('kg*m^2',1e-6,1e-3),'drag_nm_s':number('N*m*s',1e-8,1e-5),'servo_time_s':number('s',.1,5),'accept_engineering_plant':BOOL})
PLANT_EXAMPLE=dict(rotor_inertia_kg_m2=2e-5,drag_nm_s=1e-6,servo_time_s=.25,accept_engineering_plant=True)
EXTERNAL=obj({'mode':enum(['local','external']),'gain_rpm_per_mv':number('rpm/mV'),'input_v':number('V',0,8),'stop_mode':enum(['active_high','active_low']),'stop_v':nullable(number('V',0,5))})
EXTERNAL_EXAMPLE=dict(mode='local',gain_rpm_per_mv=2.,input_v=0.,stop_mode='active_high',stop_v=None)
ELECTROCHEM=obj({'reference_electrode':TEXT,'disk_potential_v':number('V',-3,3),'ring_potential_v':number('V',-3,3),'disk_e0_v':number('V',-2,2),'ring_e0_v':number('V',-2,2),'rate_m_s':number('m/s',1e-10,1),'ring_transition_v':number('V',.001,.2),'accept_external_bipotentiostat':BOOL})
ELECTROCHEM_EXAMPLE=dict(reference_electrode='explicit model reference, not calibrated hardware',disk_potential_v=-.3,ring_potential_v=.4,disk_e0_v=0.,ring_e0_v=0.,rate_m_s=1e-4,ring_transition_v=.02,accept_external_bipotentiostat=True)
SAMPLE=obj({'sample_id':TEXT,'substance_id':TEXT,'volume_m3':number('m^3',1e-6,1e-3),'concentration_mol_m3':number('mol/m^3',1e-6,1e3),'kinematic_viscosity_m2_s':number('m^2/s',1e-7,1e-4),'immersion_m':number('m',.005,.010),'reaction':enum(['generator_collector','orr']),'disk_electrons':number('1',1,4),'ring_electrons':number('1',1,4),'intermediate_fraction':number('1',0,1),'decay_rate_s':number('1/s',0,100),'accept_effective_redox':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='private_oxygen_bath',substance_id='o2_neutral',volume_m3=50e-6,concentration_mol_m3=.25,kinematic_viscosity_m2_s=1e-6,immersion_m=.007,reaction='orr',disk_electrons=4.,ring_electrons=2.,intermediate_fraction=.35,decay_rate_s=0.,accept_effective_redox=True)
POINT=obj({'time_s':number('s',0),'rpm':number('rpm',0),'disk_potential_v':number('V'),'ring_potential_v':number('V'),'disk_current_a':number('A'),'ring_current_a':number('A')})
RESULT=obj({'phase':PHASE,'complete':BOOL,'elapsed_s':number('s',0),'points':arr(POINT),'template_source':TEXT,'evidence':TEXT,'warnings':arr(TEXT)})
LEDGER=obj({'loaded_equivalent_mol':number('mol',0),'bulk_mol':number('mol',0),'transit_mol':number('mol',0),'escaped_mol':number('mol',0),'inactive_mol':number('mol',0),'returned_mol':number('mol',0),'material_residual_mol':number('mol'),'disk_charge_c':number('C'),'ring_charge_c':number('C'),'disk_electron_mol':number('mol'),'ring_electron_mol':number('mol'),'charge_residual_c':number('C'),'rotor_energy_j':number('J',0),'motor_work_j':number('J',0),'dissipated_j':number('J',0),'energy_residual_j':number('J'),'sample_id':TEXT})
CAPS={};UNSUPPORTED={'hardware.connect','robot.tip_exchange','electrochem.impedance','calibration.oem'}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},effects or [])
cap('plant.configure',PLANT,obj({'configured':BOOL}),['motor'],'初始化显式转子工程动力学','configuration','evaluator',PLANT_EXAMPLE,['plant.configured'])
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['controller'],'上电必为暂停；掉电保留动能并被动减速',ex={'enabled':True},effects=['power.enabled','motor.run_requested'])
cap('speed.set',obj({'angular_speed_rad_s':number('rad/s',0,8000*RPM)}),obj({'angular_speed_rad_s':number('rad/s',0,8000*RPM)}),['speed_knob'],'本地10圈旋钮设定；选配电极运行不超过3000rpm',ex={'angular_speed_rad_s':1000*RPM},effects=['speed.setpoint_rad_s','knob.angle_rad'])
cap('control.configure',EXTERNAL,obj({'configured':BOOL}),['io_terminal'],'本地/外控增益和厂家外停电平；外控要求本地归零','configuration','agent',EXTERNAL_EXAMPLE,['control.external','control.stop_blocked'])
cap('motor.run',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['run_switch','motor'],'RUN/PAUSE保持与升窗、外停联锁',ex={'enabled':True},effects=['motor.run_requested'])
cap('enclosure.set',obj({'lift_m':number('m',0,.285)}),obj({'lift_m':number('m',0,.285)}),['window'],'人工升窗；抬起停止但保留RUN请求，落下自动恢复',ex={'lift_m':.285},effects=['enclosure.lift_m','enclosure.blocked'])
cap('head.set',obj({'lift_m':number('m',0,.06)}),obj({'lift_m':number('m',0,.06)}),['motor_head'],'停机升窗后的人工高度调整；行程工程代理',ex={'lift_m':.05},effects=['head.lift_m'])
cap('electrochem.configure',ELECTROCHEM,obj({'configured':BOOL}),['disk_port','ring_port'],'接入宿主双恒电位仪：独立盘/环电位与显式参比','configuration','evaluator',ELECTROCHEM_EXAMPLE,['electrochem.configured'])
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['cell_platform','disk','ring'],'有限外部浴液及显式反应假设；不推定59物种全具电活性','configuration','evaluator',SAMPLE_EXAMPLE,['sample.loaded','sample.identity'])
cap('sample.unload',obj({}),obj({'returned_mol':number('mol',0)}),['cell_platform'],'停止采集/转动且升窗后卸样；所有分析物当量归还','device_command','evaluator',effects=['sample.loaded','sample.identity'])
cap('acquisition.start',obj({'duration_s':number('s',.01,600),'sample_interval_s':number('s',.01,10)}),obj({'phase':PHASE,'operation_id':TEXT}),['motor','disk','ring','io_terminal'],'宿主异步联用采集，step为唯一时钟',ex={'duration_s':3.,'sample_interval_s':.1},effects=['acquisition.phase','acquisition.elapsed_s'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['controller'],'取消保留浴液、转子能量、电荷与部分观测',effects=['acquisition.phase'])
cap('process.measure',obj({}),RESULT,['controller'],'读取转速与外部盘/环电流观测','query')
cap('analysis.collection',obj({'disk_current_a':number('A',-10,-1e-15),'ring_current_a':number('A',0,10),'disk_electrons':number('1',1,4),'ring_electrons':number('1',1,4)}),obj({'collection_fraction':number('1',0,1)}),['disk','ring'],'从给定观测电流比计算经验收集率；不读取隐藏真值','analysis',ex={'disk_current_a':-1e-4,'ring_current_a':2.5e-5,'disk_electrons':1.,'ring_electrons':1.})
cap('analysis.orr',obj({'disk_current_a':number('A',-10,-1e-15),'ring_current_a':number('A',0,10),'collection_fraction':number('1',1e-6,1)}),obj({'electron_number':number('1',2,4),'peroxide_fraction':number('1',0,1)}),['disk','ring'],'观测电流与独立收集率推断ORR电子数、过氧化物比例','analysis',ex={'disk_current_a':-3e-4,'ring_current_a':2.5e-5,'collection_fraction':.25})
cap('analysis.levich',obj({'angular_speed_rad_s':dict(arr(number('rad/s',100*RPM,3000*RPM)),minItems=3,maxItems=100),'disk_current_a':dict(arr(number('A',-10,-1e-15)),minItems=3,maxItems=100),'electron_number':number('1',1,4),'concentration_mol_m3':number('mol/m^3',1e-9,1e4),'kinematic_viscosity_m2_s':number('m^2/s',1e-7,1e-4)}),obj({'diffusion_m2_s':number('m^2/s',0),'kinetic_current_magnitude_a':nullable(number('A',0)),'r_squared':number('1',0,1)}),['disk'],'观测1/|I|对1/sqrt(omega)拟合Koutecky–Levich，反推D与动力学电流','analysis',ex={'angular_speed_rad_s':[500*RPM,1000*RPM,2000*RPM],'disk_current_a':[-1e-4,-1.4142135623730951e-4,-2e-4],'electron_number':1.,'concentration_mol_m3':1.,'kinematic_viscosity_m2_s':1e-6})
cap('template.read',obj({'substance_id':TEXT}),obj({'substance_id':TEXT,'diffusion_m2_s':number('m^2/s',0),'template_source':TEXT,'evidence':TEXT}),['disk'],'查询真实D值及证据，非实测RRDE曲线','query',ex={'substance_id':'o2_neutral'})
cap('ledger.read',obj({}),LEDGER,['motor','disk','ring'],'可信宿主检查分析物当量/电荷/转子能量守恒','query','evaluator')
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'当前子集未实现')
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='controller',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
for k in ['plant','electrochem']:slot(k+'.configured',BOOL,'1',k+'已显式配置','setpoint')
slot('power.enabled',BOOL,'1','电源开关','setpoint',w=('power.set','enabled'));slot('motor.run_requested',BOOL,'1','RUN保持位','setpoint',w=('motor.run','enabled'));slot('speed.setpoint_rad_s',number('rad/s',0,8000*RPM),'rad/s','本地旋钮设定','setpoint',w=('speed.set','angular_speed_rad_s'))
slot('speed.target_rad_s',number('rad/s',0,8000*RPM),'rad/s','当前控制目标','derived');slot('motor.true_rad_s',number('rad/s',0),'rad/s','实际速度模长','process_truth','evaluator','motor');slot('rotor.velocity_rad_s',number('rad/s'),'rad/s','世界Z负方向顺时针过程速度','scene',c='rotor');slot('rotor.angle_rad',number('rad'),'rad','过程累计角度，Blender预览使用','scene',c='rotor')
slot('speed.rpm',number('rpm',0),'rpm','理想1rpm量化测速','sensor',c='motor');slot('display.rpm',number('rpm',0,8000),'rpm','暂停显示设定，RUN显示实际测速','sensor',c='display_digits');slot('monitor.v',number('V',0),'V','2rpm/mV转速输出','sensor',c='io_terminal');slot('knob.angle_rad',number('rad',0,20*math.pi),'rad','10圈本地旋钮角','scene',c='speed_knob')
for k,z in [('control.external','使用外部速度输入'),('control.stop_blocked','外停电平阻止旋转'),('enclosure.blocked','升窗联锁阻止旋转')]:slot(k,BOOL,'1',z)
slot('enclosure.lift_m',number('m',0,.285),'m','透明护窗升程','scene',c='window',w=('enclosure.set','lift_m'));slot('head.lift_m',number('m',0,.06),'m','人工旋转头高度代理','scene',c='motor_head',w=('head.set','lift_m'));slot('sample.loaded',BOOL,'1','外部有限浴液已装入');slot('sample.identity',TEXT,'1','浴液身份','process_truth','evaluator','cell_platform');slot('sample.bulk_mol',number('mol',0),'mol','浴液剩余分析物当量','process_truth','evaluator','cell_platform')
for k,u,z in [('disk.current_a','A','外部盘电流观测'),('ring.current_a','A','外部环电流观测')]:slot(k,nullable(number(u)),u,z,'sensor',c='disk_port' if k.startswith('disk') else 'ring_port')
slot('acquisition.phase',PHASE,'1','采集状态');slot('acquisition.elapsed_s',number('s',0),'s','已计算采集时长');slot('acquisition.completed_points',dict(number('1',0),type='integer'),'1','观测记录数')

for k in ['running','paused','stop','enclosure']:slot('lamp.'+k,BOOL,'1','面板'+k+'指示灯','derived',c='led_'+k)

class PineWavevortex10Runtime:
 def __init__(self,instance_id='wavevortex_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self._principals={};self._requests={};self._owners={};self.records={};self.operation_id='';self.serial=0;self.templates=json.loads((ROOT/TEMPLATE_REL).read_text())['substances'];self.plant=None;self.powered=False;self.running=False;self.setpoint=0.;self.control=copy.deepcopy(EXTERNAL_EXAMPLE);self.lift=0.;self.head=0.;self.omega=0.;self.angle=0.;self.work=0.;self.dissipation=0.;self.electrochem=None;self.sample=None;self.bulk=0.;self.transit=0.;self.escaped=0.;self.inactive=0.;self.loaded=0.;self.returned=0.;self.qd=0.;self.qr=0.;self.ed=0.;self.er=0.;self.current_d=None;self.current_r=None;self.phase='idle';self.elapsed=0.;self.points=[];self.pending=0.;self.ticks=0;self.duration_ticks=0;self.interval_ticks=1;self.sample_window=None;self.acc_d=0.;self.acc_r=0.;self.acquisition_start=0.;self.target_torque=0.
 def register_principal(self,principal_id,visibility='agent'):
  if not principal_id or visibility not in ['agent','evaluator','debug'] or principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return PineWavevortex10Session(self,principal_id)
 def describe(self):return export_pine_wavevortex10_contract_metadata()
 def stop_blocked(self):
  c=self.control;high=c['stop_v'] is None or c['stop_v']>2
  return (not high) if c['stop_mode']=='active_high' else high
 def target(self):return self.control['input_v']*1000*self.control['gain_rpm_per_mv']*RPM if self.control['mode']=='external' else self.setpoint
 def energy(self):return .5*self.plant['rotor_inertia_kg_m2']*self.omega**2 if self.plant else 0.
 def read(self,visibility='agent'):
  d={'plant.configured':self.plant is not None,'electrochem.configured':self.electrochem is not None,'power.enabled':self.powered,'motor.run_requested':self.running,'speed.setpoint_rad_s':self.setpoint,'speed.target_rad_s':self.target(),'motor.true_rad_s':self.omega,'rotor.velocity_rad_s':-self.omega,'rotor.angle_rad':self.angle,'speed.rpm':float(round(self.omega/RPM)),'display.rpm':float(round((self.omega if self.running else self.target())/RPM)) if self.powered else 0.,'monitor.v':self.omega/RPM/2000 if self.powered else 0.,'knob.angle_rad':self.setpoint/(8000*RPM)*20*math.pi,'control.external':self.control['mode']=='external','control.stop_blocked':self.stop_blocked(),'enclosure.blocked':self.lift>1e-9,'enclosure.lift_m':self.lift,'head.lift_m':self.head,'sample.loaded':self.sample is not None,'sample.identity':self.sample['sample_id'] if self.sample else '','sample.bulk_mol':self.bulk,'disk.current_a':self.current_d,'ring.current_a':self.current_r,'acquisition.phase':self.phase,'acquisition.elapsed_s':self.elapsed,'acquisition.completed_points':len(self.points)}
  d.update({'lamp.running':self.powered and self.running,'lamp.paused':self.powered and not self.running,'lamp.stop':self.powered and self.stop_blocked(),'lamp.enclosure':self.powered and self.lift>1e-9});return copy.deepcopy({k:v for k,v in d.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _ledger(self):return dict(loaded_equivalent_mol=self.loaded,bulk_mol=self.bulk,transit_mol=self.transit,escaped_mol=self.escaped,inactive_mol=self.inactive,returned_mol=self.returned,material_residual_mol=self.loaded-self.returned-self.bulk-self.transit-self.escaped-self.inactive,disk_charge_c=self.qd,ring_charge_c=self.qr,disk_electron_mol=self.ed,ring_electron_mol=self.er,charge_residual_c=(self.qd+F*self.ed)+(self.qr-F*self.er),rotor_energy_j=self.energy(),motor_work_j=self.work,dissipated_j=self.dissipation,energy_residual_j=self.work-self.dissipation-self.energy(),sample_id=self.sample['sample_id'] if self.sample else '')
 def _result(self):return dict(phase=self.phase,complete=self.phase=='succeeded',elapsed_s=self.elapsed,points=copy.deepcopy(self.points),template_source='USGS aqueous D,298.15K; identity withheld',evidence='real diffusion parameter; explicit synthetic kinetics/branching and ideal sensors',warnings=['External bipotentiostat required; no onboard current measurement','Finite analyte equivalents only; solvent/supporting electrolyte reservoir not closed','No measured RRDE traces or factory noise calibration','Collection and ORR inference require independently valid collection efficiency'])
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[k][0],a);before=copy.deepcopy(self.__dict__)
  try:
   r=self._invoke(k,a);validate(CAPS[k][1],r)
   if CAPS[k][4] not in ['query','analysis']:self.revision+=1
   return r
  except Exception:self.__dict__.clear();self.__dict__.update(before);raise
 def safe_target(self):
  t=self.target()
  if t>8000*RPM+1e-9 or 0<t<100*RPM-1e-9:raise ActionRejected('DRIVE_SPEED_RANGE')
  if self.running and t>3000*RPM+1e-9:raise ActionRejected('TIP_SPEED_LIMIT')
 def _invoke(self,k,a):
  if k=='process.measure':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='template.read':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   t=self.templates[a['substance_id']];return dict(substance_id=a['substance_id'],diffusion_m2_s=t['diffusion_m2_s'],template_source=t['source']['url'],evidence=t['evidence'])
  if k=='analysis.collection':
   n=a['ring_current_a']/abs(a['disk_current_a'])*a['disk_electrons']/a['ring_electrons']
   if n>1:raise ActionRejected('INFERENCE_DOMAIN')
   return {'collection_fraction':n}
  if k=='analysis.orr':
   d=abs(a['disk_current_a']);r=a['ring_current_a']/a['collection_fraction']
   if r>d:raise ActionRejected('INFERENCE_DOMAIN')
   return dict(electron_number=4*d/(d+r),peroxide_fraction=2*r/(d+r))
  if k=='analysis.levich':
   speeds=a['angular_speed_rad_s'];currents=a['disk_current_a']
   if len(speeds)!=len(currents) or len(set(speeds))<3:raise ActionRejected('INFERENCE_DOMAIN')
   x=[1/math.sqrt(w) for w in speeds];y=[1/abs(i) for i in currents];xm=sum(x)/len(x);ym=sum(y)/len(y);sxx=sum((v-xm)**2 for v in x);slope=sum((u-xm)*(v-ym) for u,v in zip(x,y))/sxx;intercept=ym-slope*xm
   if slope<=0 or intercept < -1e-9*ym:raise ActionRejected('INFERENCE_DOMAIN')
   intercept=max(0.,intercept);S=.620*a['electron_number']*F*AREA*a['concentration_mol_m3']*a['kinematic_viscosity_m2_s']**(-1/6);D=(1/(slope*S))**1.5;sst=sum((v-ym)**2 for v in y);sse=sum((v-(slope*u+intercept))**2 for u,v in zip(x,y));return dict(diffusion_m2_s=D,kinetic_current_magnitude_a=1/intercept if intercept>1e-10*ym else None,r_squared=max(0.,min(1.,1-sse/sst)))
  if k=='plant.configure':
   if self.plant or self.powered or not a['accept_engineering_plant']:raise ActionRejected('INITIALIZATION_ONLY')
   self.plant=copy.deepcopy(a);return {'configured':True}
  if k=='power.set':
   if a['enabled'] and not self.plant:raise ActionRejected('PLANT_REQUIRED')
   if a['enabled']!=self.powered:self.running=False
   self.powered=a['enabled']
   if not self.powered and self.phase=='running':self._finish('cancelled')
   return a
  if k=='speed.set':
   if self.control['mode']=='external' and a['angular_speed_rad_s']!=0:raise ActionRejected('LOCAL_ZERO_REQUIRED')
   self.setpoint=a['angular_speed_rad_s'];self.safe_target();return a
  if k=='control.configure':
   if a['gain_rpm_per_mv'] not in [1.,2.,4.] or (a['stop_v'] is not None and 0<a['stop_v']<=2):raise ActionRejected('UNSPECIFIED_INPUT')
   if a['mode']=='external' and self.setpoint!=0:raise ActionRejected('LOCAL_ZERO_REQUIRED')
   self.control=copy.deepcopy(a);self.safe_target();return {'configured':True}
  if k=='motor.run':
   if not self.powered:raise ActionRejected('POWER_REQUIRED')
   self.running=a['enabled'];self.safe_target();return a
  if k=='enclosure.set':self.lift=a['lift_m'];return a
  if k=='head.set':
   if self.lift<.20 or self.omega>RPM or self.running or self.phase=='running' or self.sample:raise ActionRejected('HEAD_INTERLOCK')
   self.head=a['lift_m'];return a
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self._finish('cancelled');return {'phase':self.phase}
  if self.phase=='running':raise ActionRejected('BUSY')
  if k=='electrochem.configure':
   if not a['accept_external_bipotentiostat'] or not a['reference_electrode']:raise ActionRejected('EXTERNAL_REQUIRED')
   self.electrochem=copy.deepcopy(a);return {'configured':True}
  if k=='sample.load':
   if self.sample or self.lift<.20 or self.running or self.omega>RPM or self.head!=0 or not a['accept_effective_redox']:raise ActionRejected('LOAD_PRECONDITION')
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   if a['reaction']=='orr' and (a['substance_id']!='o2_neutral' or a['ring_electrons']!=2 or a['disk_electrons']!=4 or a['decay_rate_s']!=0):raise ActionRejected('REACTION_UNSUPPORTED')
   if a['disk_electrons']!=int(a['disk_electrons']) or a['ring_electrons']!=int(a['ring_electrons']):raise ActionRejected('INTEGER_STOICHIOMETRY')
   self.sample=copy.deepcopy(a);self.bulk=a['volume_m3']*a['concentration_mol_m3'];self.loaded+=self.bulk;return {'loaded':True}
  if k=='sample.unload':
   if not self.sample or self.lift<.20 or self.running or self.omega>RPM:raise ActionRejected('UNLOAD_PRECONDITION')
   n=self.bulk+self.transit+self.escaped+self.inactive;self.returned+=n;self.bulk=self.transit=self.escaped=self.inactive=0.;self.sample=None;return {'returned_mol':n}
  if k=='acquisition.start':
   if not self.powered or not self.running or self.lift>1e-9 or self.stop_blocked() or not self.sample or not self.electrochem or self.target()<100*RPM:raise ActionRejected('NOT_READY')
   n=a['duration_s']/DT;q=a['sample_interval_s']/DT
   if abs(n-round(n))>1e-8 or abs(q-round(q))>1e-8 or round(n)%round(q) or n/q>20000:raise ActionRejected('SAMPLE_GRID')
   self.serial+=1;self.operation_id=f'{self.instance_id}:op:{self.serial}';self.phase='running';self.elapsed=0.;self.ticks=0;self.points=[];self.current_d=self.current_r=None;self.duration_ticks=round(n);self.interval_ticks=round(q);self.acc_d=self.acc_r=0.;self.acquisition_start=self.time_s-self.pending;return dict(phase=self.phase,operation_id=self.operation_id)
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _finish(self,phase):self.phase=phase;self.records[self.operation_id]=self._result()
 def _rotor(self,dt):
  if not self.plant:return
  p=self.plant;J=p['rotor_inertia_kg_m2'];b=p['drag_nm_s'];w0=self.omega;target=self.target() if self.powered and self.running and self.lift<=1e-9 and not self.stop_blocked() else 0.
  if not self.powered:w1=w0*(1-b*dt/(2*J))/(1+b*dt/(2*J));torque=0.
  else:
   goal=target+(w0-target)*math.exp(-dt/p['servo_time_s']);torque=max(-.0187,min(.0187,J*(goal-w0)/dt+b*(w0+goal)/2));w1=max(0.,(J*w0+dt*torque-b*dt*w0/2)/(J+b*dt/2))
   def work(w):return .5*J*(w*w-w0*w0)+b*dt*(w0*w0+w0*w+w*w)/3
   if work(w1)>11*dt:
    lo=w0;hi=w1
    for _ in range(40):
     mid=(lo+hi)/2
     if work(mid)>11*dt:hi=mid
     else:lo=mid
    w1=(lo+hi)/2
  drag=b*dt*(w0*w0+w0*w1+w1*w1)/3;de=.5*J*(w1*w1-w0*w0);work=de+drag
  if self.powered:self.work+=max(0.,work);self.dissipation+=drag+max(0.,-work)
  else:self.dissipation+=-de # exact passive energy decrease under trapezoidal drag integrator
  self.omega=w1;self.angle-=(w0+w1)/2*dt;self.target_torque=torque
 def _transport(self,dt,D):
  if self.transit<=0:return 0.
  s=self.sample;kT=self.omega/(1.28*(s['kinematic_viscosity_m2_s']/D)**(1/3));kd=s['decay_rate_s'];rate=kT+kd
  if rate<=0:return 0.
  n=self.transit*(-math.expm1(-rate*dt));self.transit-=n;out=n*kT/rate;decay=n-out;c=self.electrochem;gate=1/(1+math.exp(max(-60,min(60,-(c['ring_potential_v']-c['ring_e0_v'])/c['ring_transition_v']))));collected=out*N_GEOM*gate if self.phase=='running' else 0.
  self.bulk+=collected;self.escaped+=out-collected;self.inactive+=decay;electrons=s['ring_electrons']*collected;self.er+=electrons;q=F*electrons;self.qr+=q;return q
 def _chemistry(self,dt):
  s=self.sample;D=self.templates[s['substance_id']]['diffusion_m2_s'];qr=self._transport(dt/2,D);nD=4-2*s['intermediate_fraction'] if s['reaction']=='orr' else s['disk_electrons'];c=self.electrochem
  km=.620*D**(2/3)*s['kinematic_viscosity_m2_s']**(-1/6)*math.sqrt(self.omega);ke=c['rate_m_s']*math.exp(max(-60,min(60,-.5*nD*F*(c['disk_potential_v']-c['disk_e0_v'])/(R*T))));k=km*ke/(km+ke) if km>0 else 0.;dn=self.bulk*(-math.expm1(-AREA*k*dt/s['volume_m3']));self.bulk-=dn;self.transit+=s['intermediate_fraction']*dn;self.inactive+=(1-s['intermediate_fraction'])*dn;self.ed+=nD*dn;qd=-F*nD*dn;self.qd+=qd;qr+=self._transport(dt/2,D);return qd,qr
 def step(self,dt):
  validate(number('s',0,3600),dt)
  if dt==0:return
  self.pending+=dt
  while self.pending+1e-12>=DT:
   self.pending=max(0.,self.pending-DT);self._rotor(DT)
   if self.phase=='running':
    qd,qr=self._chemistry(DT);self.acc_d+=qd;self.acc_r+=qr;self.ticks+=1;self.elapsed=self.ticks*DT
    if self.ticks%self.interval_ticks==0:
     window=self.interval_ticks*DT;self.current_d=round(self.acc_d/window/1e-9)*1e-9;self.current_r=round(self.acc_r/window/1e-9)*1e-9;self.acc_d=self.acc_r=0.;self.sample_window=[self.acquisition_start+self.elapsed-window,self.acquisition_start+self.elapsed];self.points.append(dict(time_s=self.elapsed,rpm=float(round(self.omega/RPM)),disk_potential_v=self.electrochem['disk_potential_v'],ring_potential_v=self.electrochem['ring_potential_v'],disk_current_a=self.current_d,ring_current_a=self.current_r))
    if self.ticks>=self.duration_ticks:self._finish('succeeded')
   elif self.sample and self.electrochem:self._transport(DT,self.templates[self.sample['substance_id']]['diffusion_m2_s'])
  self.time_s+=dt;self.revision+=1

class PineWavevortex10Session:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,i):
  if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  m=export_pine_wavevortex10_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
 def read(self,*,instance_id,state_ids=None):
  self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
  if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
  if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
  current=lambda k:k in ['disk.current_a','ring.current_a']
  return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='unavailable' if v[k] is None else 'stale' if current(k) and h.phase!='running' else 'estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=h.sample_window[-1] if current(k) and h.sample_window else h.time_s,sample_window_s=copy.deepcopy(h.sample_window) if current(k) else None) for k in ids})
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
  except ActionRejected as exc:return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,status='unsupported' if str(exc)=='UNSUPPORTED' else 'rejected',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))
 def write(self,*,instance_id,state_id,value,request_id):
  self._target(instance_id)
  if state_id not in SLOTS or not SLOTS[state_id][5]:raise ActionRejected('ACCESS_DENIED')
  cap,arg=SLOTS[state_id][5];return self.invoke(instance_id=instance_id,capability_id=cap,arguments={arg:value},request_id=request_id)
 def poll(self,operation_id):
  h=self._host
  if h._owners.get(operation_id)!=self._principal:raise ActionRejected('ACCESS_DENIED')
  record=h.records.get(operation_id)
  if operation_id==h.operation_id:record=h._result()
  if record is None:raise ActionRejected('UNKNOWN_OPERATION')
  return dict(operation_id=operation_id,status=record['phase'],result=copy.deepcopy(record))
 def cancel(self,operation_id,request_id):
  self.poll(operation_id)
  if operation_id!=self._host.operation_id:raise ActionRejected('NOT_RUNNING')
  return self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)




def export_pine_wavevortex10_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions='external reference electrode and298.15K' if u=='V' else None,origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'PineWavevortex10Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNKNOWN_OPERATION','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','INITIALIZATION_ONLY','PLANT_REQUIRED','DRIVE_SPEED_RANGE','TIP_SPEED_LIMIT','POWER_REQUIRED','LOCAL_ZERO_REQUIRED','UNSPECIFIED_INPUT','HEAD_INTERLOCK','BUSY','EXTERNAL_REQUIRED','LOAD_PRECONDITION','UNKNOWN_REFERENCE','REACTION_UNSUPPORTED','INTEGER_STOICHIOMETRY','UNLOAD_PRECONDITION','NOT_READY','NOT_RUNNING','SAMPLE_GRID','INFERENCE_DOMAIN']
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  unsupported=k in UNSUPPORTED;asyn=k=='acquisition.start';caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.pine_wavevortex10.PineWavevortex10Session.invoke',invocation=f'session.invoke(instance_id="wavevortex_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if kind in ['query','analysis'] or unsupported else [dict(description_zh='参数/身份、电源、所选电极限速、外停、升窗、有限浴液及双恒电位仪依赖检查',state_ids=['power.enabled','motor.run_requested','enclosure.lift_m','sample.loaded','electrochem.configured','acquisition.phase'],enforced_by='runtime',check='atomic Runtime.invoke and trusted Session')],state_effects=[dict(state_id=x,timing='immediate',description_zh='受检事务改变逻辑状态') for x in effects],observes=[],model_effects=dict(mode='none',description_zh='等待独立场景验证',binding_ids=[]),execution=dict(mode='asynchronous' if asyn else 'synchronous',time_domain='simulation',completion_criterion='固定0.01s过程步积分至声明时长' if asyn else '事务返回，查询不推进时钟',completion_state_ids=['acquisition.phase'] if asyn else [],timeout_s=None,cancellation='supported' if asyn else 'unsupported',retry='same_request_id',resources=['host_clock','finite_analyte','rotor','request_cache'],partial_failure_zh='保留已转移分析物当量、电荷、转子动能和部分数据'),errors=[dict(code=x,meaning_zh=x,side_effects='none') for x in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=['真实电流曲线/噪声和电化学动力学、原厂伺服及校准未验证'],source_ids=['manufacturer_primary','simulation_assumption'])
  if asyn:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='宿主唯一step推进有限过程与观测') for x in ['disk.current_a','ring.current_a','speed.rpm','acquisition.elapsed_s','acquisition.phase']]
 mech=dict(component_ids=['controller','motor','rotor','disk','ring','cell_platform'],state_ids=list(slots),meaning_zh='转子能量与有限分析物环盘联用；观测电流比推断产物比例',implementation='lumped',equations=['km=.620*D^(2/3)*nu^(-1/6)*sqrt(omega); ke=k0*exp(-alpha*n*F*(E-E0)/(RT)); k=km*ke/(km+ke)','dn=Nbulk*(1-exp(-Adisk*k*dt/V)); Id=-nD*F*dn/dt','tau=1.28*(nu/D)^(1/3)/omega; dX/dt=production-X/tau-kdecay*X; ring collects geometric N times outflow and potential gate','ORR: nD=4-2*f, nR=2; n_inferred=4*abs(Id)/(abs(Id)+Ir/N); f_inferred=2*(Ir/N)/(abs(Id)+Ir/N)','Generator-collector observed N=(Ir/abs(Id))*(nD/nR)','loaded=bulk+transit+escaped+inactive+returned in mol analyte equivalents; diskQ=-F*diskElectronMol; ringQ=F*ringElectronMol','J*domega/dt=torque-b*omega; |continuous torque|<=.0187Nm; positive motor work<=11W*dt; work-dissipation=.5*J*omega^2','Factory active HIGH:0V stop,>2V allows; window lifted commands zero while preserving RUN; lowering auto-resumes','Fixed0.01s ticks, electrochemical source/transport split; rotation clockwise from top => negative worldZ angular velocity'],parameters={},source_ids=['manufacturer_primary','usgs_diffusion','simulation_assumption'],limitations=['59 public diffusion coefficients, zero measured RRDE response curves;50 experimental curves unmet.','Electrode chemistry/E0/kinetics/reference and branching supplied as assumptions; not all database species are electroactive.','Analyte-equivalent conservation excludes solvent/supporting electrolyte/proton reservoirs.','Empirical collection calibration is independent; geometric25.5511% differs from rounded manufacturer25%.','ORR only configured oxygen and zero decay; external potentiostat ideal, no internal current ADC claim.','No electrical double-layer, electrode fouling, migration/convection CFD, noisy tach response or full hydrodynamic transient.','Rotor/control constants engineering assumptions; factory precision and measured11W torque-speed characteristic not validated.','Manual head travel and transparent-window shapes are photo-based approximations; robot, hardware, real contacts not_run.'])
 spec=importlib.util.spec_from_file_location('_rrde_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'rrde':mech},inputs={k:(CAPS[k][0],CAPS[k][6]) for k in ['plant.configure','control.configure','sample.load','electrochem.configure']})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'。'
 for key in ['disk.current_a','ring.current_a']:slots[key]['sampling']=dict(mode='interval_mean',time_domain='simulation',window_description='最近外部采集区间电荷/时间，1nA理想量化；停止后stale')
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'rrde':mech})

"""ARE-310S-UL teaching model; family speed envelope is not UL certification.

Front panel (panel.press / panel.switch / panel.capture / panel.frame): the ARE-310 control panel of the instruction manual (TIME and SPEED
indicators, STD / STEP / MIX / DEFOAM / STEP 1-5 / MEMORY 1-5 / LOCK lamps, MODE, SETUP, DOWN, UP, START/STOP, STEP, MEMORY and OPEN keys,
power switch) in panel_ui.json beside the contract, run by the generic engine _panel_ui.py on top of the capabilities below.  The panel
operating conditions (STD / STEP mode, five memories per mode, five steps) are device settings kept here (conditions.*)."""
from __future__ import annotations
import copy,hashlib,importlib.util,json,math,os,tempfile,weakref
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT


def _rejected(code,detail):
    e=ActionRejected(code);e.detail=detail;return e   # 1.2.1: a refusal whose message names the cause
from asset_runtime.pccell_ed64002 import validate
ROOT=Path(__file__).resolve().parents[2];SLUG='thinky_are310s';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-045';PROTOCOL_VERSION='1.0.0';ASSET_VERSION='1.2.1';ASSET_DIR=ROOT/'assets/expansion_batch03/t1_thermal'/SLUG;TEMPLATE_REL='assets/reactions/thinky_are310s_mixing_library/templates.json'
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
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['button_power','power_inlet'],'120V供电代理（I/O硬开关）；转动中关断不被拒，按断电处理（运转取消、断电制动）','device_command','agent',dict(enabled=True)),
 'power.interrupt':(obj({}),obj({'phase':PHASE}),['power_inlet'],'意外断电取消保留盖锁；宿主减速不冒充真实惯性滑停','simulation','evaluator',{}),
 'lid.set_open':(obj({'open':BOOL}),obj({'open':BOOL}),['lid','key_open'],'手工盖角目标；step移动，未停稳拒绝开盖','device_command','agent',dict(open=False)),
 'balance.set':(obj({'gross_mass_kg':number('kg',0,.31)}),obj({'gross_mass_kg':number('kg',0,.31)}),['counterweight','knob_balance'],'配平标尺为杯+适配器+样品总质量；真实配重质量未知','device_command','agent',dict(gross_mass_kg=.14)),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['button_start_stop','rotor','cup_holder'],'异步程序；闭盖、配平、装杯就绪，完成包括宿主制动','device_command','agent',{}),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['button_start_stop','rotor'],'取消保留效果；继续step减速','device_command','agent',{}),
 'mixing.toggle':(obj({}),obj({'phase':PHASE,'operation_id':TEXT},optional=('operation_id',)),['button_start_stop','rotor'],'面板START/STOP事件；启动时返回operation_id（归调用者）','device_command','agent',{}),
 'mixing.measure':(obj({}),MEASURE,['display_digits'],'理想转速/剩余时间；未装MultiSensor不提供温度','query','agent',{}),
 'product.read':(obj({}),PRODUCT,['cup_holder'],'评价器读取质量、气体、混合指数和热账本','query','evaluator',{}),
 'product.collect':(obj({'fraction':number('1',0,1),'recipient_id':TEXT}),obj({'transferred_kg':number('kg',0),'recipient_id':TEXT}),['cup_holder','lid'],'停稳开盖后宿主有限转移；气体和显热守恒','device_command','evaluator',dict(fraction=.25,recipient_id='receiver')),
 'reference.lookup':(obj({'substance_id':TEXT}),obj({'cp_J_kg_K':number('J/(kg*K)',0),'temperature_K':number('K',0),'thermal_reference_eligible':BOOL,'source':TEXT,'evidence':TEXT,'template_source':TEXT}),['display_digits'],'真实比热参考，非厂家混合曲线或材料兼容性认证','query','agent',dict(substance_id='water')),
}
# Panel operating conditions (manual 4-3-1, 4-4): STD / STEP mode, five memories per mode with five steps (STD: step 1, fixed 2000 / 2200 rpm)
STEP=obj({'mix_time_s':number('s',0,1800),'mix_rpm':number('rpm',0,2000),'defoam_time_s':number('s',0,1800),'defoam_rpm':number('rpm',0,2200)})
NUMBER=dict(type='integer',minimum=1,maximum=5,**{'x-unit':'1'})
STD_RPM=dict(mix_rpm=2000.,defoam_rpm=2200.)
def zero_step(mode):return dict(mix_time_s=0.,defoam_time_s=0.,**(STD_RPM if mode=='STD' else dict(mix_rpm=0.,defoam_rpm=0.)))
def factory_memories():
 """Factory setting (manual p.40, p.42): memory 1 step 1 = mixing 30 s at 2000 rpm, de-foaming 30 s at 2200 rpm; everything else 0."""
 return {m:[[dict(mix_time_s=30.,mix_rpm=2000.,defoam_time_s=30.,defoam_rpm=2200.) if i==j==0 else zero_step(m) for j in range(5)] for i in range(5)] for m in ['STD','STEP']}
CAPS.update({
 'conditions.set_mode':(obj({'mode':dict(type='string',enum=['STD','STEP'])}),obj({'mode':TEXT}),['button_open','display_digits'],'MODE键：切换STD/STEP模式并调出该模式所选存储号的条件（未登录的工作条件丢失）','device_command','agent',dict(mode='STEP')),
 'conditions.select_memory':(obj({'number':NUMBER}),obj({'number':NUMBER}),['key_memory','display_digits'],'MEMORY键：选择存储号1-5并调出其条件','device_command','agent',dict(number=2)),
 'conditions.select_step':(obj({'number':NUMBER}),obj({'number':NUMBER}),['key_step','display_digits'],'STEP键：选择STEP 1-5（仅STEP模式）','device_command','agent',dict(number=2)),
 'conditions.set_step':(STEP,STEP,['button_setup','button_plus','button_minus','display_digits'],'SETUP/UP/DOWN：当前STEP的混合/脱泡时间（0-30 min，1 s）与公转速度（0或200-2000/400-2200 rpm，10 rpm；STD固定2000/2200）','device_command','agent',dict(mix_time_s=30.,mix_rpm=2000.,defoam_time_s=30.,defoam_rpm=2200.)),
 'conditions.register':(obj({}),obj({'mode':TEXT,'number':NUMBER,'steps':NUMBER}),['key_memory'],'长按MEMORY超过1 s：把STEP 1至当前STEP登录到所选存储号，其后的STEP删除；断电不丢失','device_command','agent',{}),
})
CONDITION_CAPS=['conditions.set_mode','conditions.select_memory','conditions.select_step','conditions.set_step','conditions.register']
# Error display (manual 5-4, p.61): lid sensor, vibration sensor and revolution sensor; operation stops and ErrX is shown.  Measures: Err1b close the
# lid, Err2 check the container / adjust the rotation balance; Err1c, Err3/4, Err10/11 and Err7-9 (electronics, software) need the dealer.
FAULT_CODES=['1b','1c','2','3','4','7','8','9','10','11']
OPERATOR_REMEDY={'1b':'lid','2':'balance'}
FAULT_CAPS=['fault.inject','fault.clear','fault.acknowledge']
CAPS.update({
 'fault.inject':(obj({'code':dict(type='string',enum=FAULT_CODES)}),obj({'code':TEXT,'phase':PHASE}),['display_digits','rotor'],'评测者注入手册5-4的错误（Err1b/1c/2/3/4/7-11）：运转中则停止并制动，面板显示ErrX；确认前拒绝启动','simulation','evaluator',dict(code='3')),
 'fault.clear':(obj({}),obj({'code':nullable(TEXT)}),['display_digits'],'评测者：排除故障原因（经销商维修或操作者补救）；ErrX显示仍需按START/STOP以外的任一键确认','simulation','evaluator',{}),
 'fault.acknowledge':(obj({}),obj({'code':TEXT}),['button_setup','button_open','button_plus','button_minus','key_step','key_memory','key_open'],'按START/STOP以外任一键：原因已排除（Err1b闭盖、Err2配平、其他经维修）时恢复功能并清除ErrX；否则显示不变','device_command','agent',{}),
})
SLOTS={};SLOT_ZH={}
def slot(k,s,u,zh,o='derived',v='agent',c='body',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=zh
for n in ['plant','method','sample']:slot(n+'.configured',BOOL,'1','宿主'+n+'配置完成','setpoint')
slot('power.enabled',BOOL,'1','供电设定','setpoint',w=('power.set','enabled'));slot('lid.open_target',BOOL,'1','开盖目标；非瞬移','setpoint',c='lid',w=('lid.set_open','open'));slot('lid.angle_rad',number('rad',0,1.7),'rad','后铰链开盖角','scene',c='lid');slot('lid.locked',BOOL,'1','闭盖或未停稳的宿主锁代理',c='lid')
slot('balance.gross_mass_kg',number('kg',0,.31),'kg','配平标尺，不是真实配重质量','setpoint',c='knob_balance',w=('balance.set','gross_mass_kg'));slot('balance.dial_angle_rad',number('rad',-math.pi*5/6,math.pi*5/6),'rad','教学线性标尺0至310g映射±150度','scene',c='knob_balance')
slot('cycle.phase',PHASE,'1','异步程序状态');slot('cycle.mode',TEXT,'1','混合/脱泡/制动/空闲');slot('cycle.stage_index',number('1',0,9),'1','当前段索引');slot('cycle.elapsed_s',number('s',0),'s','程序累计时间');slot('cycle.remaining_s',number('s',0,1800),'s','剩余程序时间；不含制动')
slot('rotor.revolution_rpm',number('rpm',0,2200),'rpm','加减速后的公转',c='rotor');slot('rotor.spin_rpm',number('rpm',0,800),'rpm','混合0.4比例，脱泡60/2200；反向自转',c='cup_holder');slot('rotor.angle_rad',number('rad',0,math.tau),'rad','公转角模2π','scene',c='rotor');slot('cup.angle_rad',number('rad',-math.tau,0),'rad','45度轴反向自转角','scene',c='cup_holder')
slot('panel.rpm',number('1',0,2200),'1','整数rpm理想显示',c='display_digits');slot('panel.remaining_s',number('1',0,1800),'1','向上取整剩余秒',c='display_digits');slot('thermal.indicated_temperature_k',nullable(number('K',0)),'K','无可选MultiSensor返回null',c='display_digits')
slot('conditions.mode',dict(type='string',enum=['STD','STEP']),'1','面板运行条件模式','setpoint',c='display_digits');slot('conditions.memory',NUMBER,'1','当前模式所选存储号','setpoint',c='display_digits');slot('conditions.step',NUMBER,'1','当前显示的STEP号','setpoint',c='display_digits')
slot('fault.code',nullable(dict(type='string',enum=FAULT_CODES)),'1','面板显示的错误代码ErrX（手册5-4；确认前保持）；无错误为null',c='display_digits');slot('conditions.current',STEP,'1','当前STEP的工作条件','setpoint',c='display_digits');slot('conditions.working',array(STEP,5),'1','五个STEP的工作条件（断电或调出存储时被覆盖）','setpoint',c='display_digits');slot('conditions.memories',obj({'STD':array(array(STEP,5),5),'STEP':array(array(STEP,5),5)}),'1','两种模式各5个存储（断电保持）','setpoint',c='display_digits')
for k,s,u,zh in [('mass_kg',array(number('kg',0),2),'kg','液/固剩余质量'),('initial_mass_kg',array(number('kg',0),2),'kg','初始质量'),('collected_mass_kg',array(number('kg',0),2),'kg','转出质量'),('temperature_k',number('K',0),'K','温度真值'),('mixing_index',number('1',0,1),'1','经验混合指数'),('entrained_gas_mol',number('mol',0),'mol','材料内气体'),('headspace_gas_mol',number('mol',0),'mol','杯顶空气体，非真空排气'),('collected_gas_mol',number('mol',0),'mol','转出气体'),('dissipated_energy_j',number('J',0),'J','经验耗散热'),('heat_to_ambient_j',number('J'),'J','累计散热'),('thermal_storage_j',number('J'),'J','留存显热'),('collected_heat_j',number('J'),'J','转移显热')]:slot('truth.'+k,s,u,zh,'process_truth','evaluator',c='cup_holder')
def _panel_engine():
 """The shared front-panel engine (_panel_ui.py beside this file): as a package module, else loaded from its file path."""
 try:
  from . import _panel_ui as engine;return engine
 except ImportError:
  import sys
  if '_chemsim_panel_ui' not in sys.modules:
   s=importlib.util.spec_from_file_location('_chemsim_panel_ui',Path(__file__).with_name('_panel_ui.py'));sys.modules['_chemsim_panel_ui']=importlib.util.module_from_spec(s);s.loader.exec_module(sys.modules['_chemsim_panel_ui'])
  return sys.modules['_chemsim_panel_ui']
_panel_ui=_panel_engine()
PANEL_SPEC=None if os.environ.get('THINKY_PANEL_BUILD') else _panel_ui.load_spec(ASSET_DIR/'panel_ui.json',capabilities=set(CAPS))  # THINKY_PANEL_BUILD: spec being written
PANEL_CAPS=_panel_ui.capability_definitions(PANEL_SPEC) if PANEL_SPEC else {}  # panel.* kept out of CAPS: CAPS stays the instrument's own command set
_PANELS=weakref.WeakKeyDictionary()
def visibility(k):return CAPS[k][5] if k in CAPS else PANEL_CAPS[k]['visibility']
def validate_arguments(k,v):
 """Capability arguments: the instrument's validator; panel.* schemas (optional keys) by the engine's checker."""
 if k in PANEL_CAPS:
  if not isinstance(v,dict) or _panel_ui.check_arguments(PANEL_CAPS[k]['input_schema'],v):raise ActionRejected('INVALID_ARGUMENT')
 else:validate(CAPS[k][0],v)
class ThinkyAre310sRuntime:
 def __init__(self,instance_id='are310s_01'):
  self.instance_id=instance_id;self.plant=self.method=self.sample=None;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.powered=False;self.lid_open=True;self.lid_angle=1.5;self.balance=0.;self.phase=self.mode='idle';self.time_s=self.elapsed_s=self.stage_time=self.rpm=self.spin=self.angle=self.cup_angle=self.heat=self.loss=self.storage=self.collected_heat=0.;self.stage_index=0;self.mass=[0.,0.];self.initial=self.mass.copy();self.collected=self.mass.copy();self.gas=self.headgas=self.collected_gas=self.mixed=0.;self.temperature=298.15;self.capacity=0.;self.bounds=None;self.revision=0;self.operation_id='';self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self.cond_mode='STD';self.memory_no={'STD':1,'STEP':1};self.memories=factory_memories();self.working=copy.deepcopy(self.memories['STD'][0]);self.step_no=1;self.power_epoch=0;self.panel_capture_dir=None;self.fault=None;self._caller=None
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
 def _values(self):return {'plant.configured':bool(self.plant),'method.configured':bool(self.method),'sample.configured':bool(self.sample),'power.enabled':self.powered,'lid.open_target':self.lid_open,'lid.angle_rad':self.lid_angle,'lid.locked':bool(self.phase=='running' or self.rpm>1e-9 or not self.lid_open),'balance.gross_mass_kg':self.balance,'balance.dial_angle_rad':-math.pi*5/6+self.balance/.31*math.pi*5/3,'cycle.phase':self.phase,'cycle.mode':self.mode,'cycle.stage_index':self.stage_index,'cycle.elapsed_s':self.elapsed_s,'cycle.remaining_s':self.remaining(),'rotor.revolution_rpm':self.rpm,'rotor.spin_rpm':self.spin,'rotor.angle_rad':self.angle,'cup.angle_rad':self.cup_angle,'panel.rpm':round(self.rpm),'panel.remaining_s':math.ceil(self.remaining()-1e-9),'thermal.indicated_temperature_k':None,'truth.mass_kg':self.mass,'truth.initial_mass_kg':self.initial,'truth.collected_mass_kg':self.collected,'truth.temperature_k':self.temperature,'truth.mixing_index':self.mixed,'truth.entrained_gas_mol':self.gas,'truth.headspace_gas_mol':self.headgas,'truth.collected_gas_mol':self.collected_gas,'truth.dissipated_energy_j':self.heat,'truth.heat_to_ambient_j':self.loss,'truth.thermal_storage_j':self.storage,'truth.collected_heat_j':self.collected_heat,'conditions.mode':self.cond_mode,'conditions.memory':self.memory_no[self.cond_mode],'conditions.step':self.step_no,'conditions.current':self.working[self.step_no-1],'conditions.working':self.working,'conditions.memories':self.memories,'fault.code':self.fault['code'] if self.fault else None}
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ['debug','evaluator']})
 def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k!='templates'})
 def _start_refusal(self):
  """1.2.1: why a start is refused; no IMBALANCE code is declared, so the code stays PRECONDITION_FAILED."""
  if not self.plant:return 'the trusted host has not configured the mixer (plant.configure)'
  if not self.method:return 'no mixing program is set (method.configure)'
  if not self.sample:return 'no cup is loaded'
  if not self.powered:return 'the mixer is switched off'
  if self.fault:return 'Err%s is shown; remove the cause and clear it first'%self.fault['code']
  if self.phase=='running' or self.rpm>1e-9:return 'the rotor is still turning'
  if self.mode=='reference_limit':return 'an earlier run stopped at the temperature limit of the sample reference data; this sample cannot be mixed further'
  if self.lid_open or self.lid_angle>1e-9:return 'the lid is not closed'
  if sum(self.mass)<=0:return 'the cup is empty'
  if abs(self.balance-self.gross())>self.plant['balance_tolerance_kg']+1e-12:
   return ('the rotation balance does not match the loaded container: set the balance scale (balance.set) to the gross mass of cup, adapter and '
           'sample within the tolerance (manual 5-4, Err2: adjust the rotation balance)')
  return None
 def _start(self):
  why=self._start_refusal()
  if why:raise _rejected('PRECONDITION_FAILED',why)
  self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);self.phase='running';self.mode=self.method['program'][0]['mode'];self.elapsed_s=self.stage_time=0.;self.stage_index=0
 @property
 def panel(self):
  """Front-panel UI state (engine object, kept outside __dict__ so snapshots stay plain data)."""
  if self not in _PANELS:_PANELS[self]=_panel_ui.PanelUI(PANEL_SPEC,self)
  return _PANELS[self]
 def panel_program(self):
  """Run program of the panel conditions: per STEP (STD: STEP 1) mixing then de-foaming; a part with time or speed 0 is skipped (manual p.38)."""
  steps=self.working[:1] if self.cond_mode=='STD' else self.working
  return [dict(mode=m,duration_s=x[m+'_time_s'],revolution_rpm=x[m+'_rpm']) for x in steps for m in ['mix','defoam'] if x[m+'_time_s']>0 and x[m+'_rpm']>0]
 def panel_read(self):
  """What the panel firmware sees: the agent slots plus power, power-up count, the panel run program, the running part's mode, remaining
  time and STEP number (when the run program is the panel's), and the speed shown."""
  d=self.read('agent');prog=self.panel_program();running=self.phase=='running' and self.mode!='braking' and self.method is not None;stage=self.method['program'][self.stage_index] if running else None
  steps=[i+1 for i,x in enumerate(self.working[:1] if self.cond_mode=='STD' else self.working) for m in ['mix','defoam'] if x[m+'_time_s']>0 and x[m+'_rpm']>0]
  d.update({'host.powered':self.powered,'host.power_epoch':self.power_epoch,'host.fault':self.fault['code'] if self.fault else None,'host.program':prog,'host.run_mode':stage['mode'] if stage else None,
            'host.run_remaining_s':math.ceil(max(0.,stage['duration_s']-self.stage_time)-1e-9) if stage else None,
            'host.run_step':steps[self.stage_index] if stage and self.method['program']==prog else None,
            'host.balance_ok':bool(self.sample and self.plant and abs(self.balance-self.gross())<=self.plant['balance_tolerance_kg']+1e-12)})
  return d
 def panel_invoke(self,capability_id,arguments):
  """A panel action takes the path of a direct call (a refusal is shown as the device's indication)."""
  try:return self.invoke(capability_id,**arguments)
  except ActionRejected as exc:raise _panel_ui.PanelRejected(str(exc)) from None
 def panel_time(self):return self.time_s
 def _panel_command(self,k,a):
  validate_arguments(k,a)
  if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
  if k=='panel.frame':return dict(frame=self.panel.frame())
  if k=='panel.capture':
   data,w,h=self.panel.png_bytes(a['display_id']);sha=hashlib.sha256(data).hexdigest();d=Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir())/'chemistry_sim_panel'/self.instance_id;d.mkdir(parents=True,exist_ok=True);p=d/f"{a['display_id']}_{sha[:16]}.png"
   if not p.exists():p.write_bytes(data)
   return dict(display_id=a['display_id'],time_s=self.time_s,image=dict(path=str(p),format='png',width=w,height=h,sha256=sha))
  try:
   if k=='panel.press':ok=self.panel.press(a['input_id'],float(a.get('duration_s',.1)),held=tuple(a.get('held',())))
   else:ok=self.panel.switch(a['input_id'],a['position'])
  except _panel_ui.PanelError:raise ActionRejected('INVALID_ARGUMENT') from None
  self.revision+=1;return {'accepted':bool(ok)}
 def _conditions(self,k,a):
  """Panel operating conditions and memories (manual 4-3-1, 4-4); refusals change nothing."""
  if not self.powered:raise ActionRejected('PRECONDITION_FAILED')
  mode=self.cond_mode
  if k=='conditions.set_mode':
   self.cond_mode=a['mode'];self._recall();return dict(mode=self.cond_mode)
  if k=='conditions.select_memory':
   self.memory_no[mode]=a['number'];self._recall();return dict(number=a['number'])
  if k=='conditions.select_step':
   if mode=='STD' and a['number']!=1:raise ActionRejected('PRECONDITION_FAILED')
   self.step_no=a['number'];return dict(number=a['number'])
  if k=='conditions.set_step':
   x={key:float(a[key]) for key in ['mix_time_s','mix_rpm','defoam_time_s','defoam_rpm']}
   if x['mix_time_s']!=int(x['mix_time_s']) or x['defoam_time_s']!=int(x['defoam_time_s']):raise ActionRejected('INVALID_ARGUMENT')
   for key,lo in [('mix_rpm',200),('defoam_rpm',400)]:
    if x[key]%10 or 0<x[key]<lo:raise ActionRejected('INVALID_ARGUMENT')
   if mode=='STD' and (x['mix_rpm'],x['defoam_rpm'])!=(2000.,2200.):raise ActionRejected('INVALID_ARGUMENT')  # STD: fixed speeds
   self.working[self.step_no-1]=x;return copy.deepcopy(x)
  n=self.step_no;self.memories[mode][self.memory_no[mode]-1]=[copy.deepcopy(x) if i<n else zero_step(mode) for i,x in enumerate(self.working)];return dict(mode=mode,number=self.memory_no[mode],steps=n)
 def _foreign_run(self):
  """Caller rule: an agent may not stop another caller's run (direct call or panel gesture, which acts as its session's caller)."""
  owner=self._owners.get(self.operation_id)
  return self._caller is not None and self._principals.get(self._caller)=='agent' and owner is not None and owner!=self._caller
 def _fault(self,k,a):
  """Manual 5-4: an abnormality stops the operation (brake) and shows ErrX; after the remedy any key except START/STOP restores the unit."""
  if k=='fault.inject':
   self.fault=dict(code=a['code'],cause=a['code'] not in OPERATOR_REMEDY)
   if self.phase=='running':self.phase='failed';self.mode='fault_stop'
   return dict(code=a['code'],phase=self.phase)
  if k=='fault.clear':
   code=self.fault['code'] if self.fault else None
   if self.fault:self.fault['cause']=False
   return dict(code=code)
  f=self.fault
  if not f or not self.powered:raise ActionRejected('PRECONDITION_FAILED')
  remedy=OPERATOR_REMEDY.get(f['code'])
  if f['cause'] or (remedy=='lid' and self.lid_angle>1e-9) or (remedy=='balance' and not (self.sample and abs(self.balance-self.gross())<=self.plant['balance_tolerance_kg']+1e-12)):raise ActionRejected('PRECONDITION_FAILED')
  self.fault=None;return dict(code=f['code'])
 def _recall(self):self.working=copy.deepcopy(self.memories[self.cond_mode][self.memory_no[self.cond_mode]-1]);self.step_no=1
 def invoke(self,capability,**a):
  if capability in PANEL_CAPS:return self._panel_command(capability,a)
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if (self.phase=='running' or self.rpm>1e-9) and capability in ['plant.configure','method.configure','sample.configure','balance.set','product.collect']+CONDITION_CAPS:raise ActionRejected('BUSY')
  if capability in ['acquisition.stop','mixing.toggle'] and self.phase=='running' and self._foreign_run():raise ActionRejected('ACCESS_DENIED')
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
   if not a['enabled'] and (self.phase=='running' or self.rpm>1e-9):   # the I/O switch is a hard switch (final sync 2026-10-19): never refused,
    self.powered=False                                                 # switching off while rotating is a power loss (as power.interrupt)
    if self.phase=='running':self.phase='cancelled'
    self.mode='power_loss_braking';out=a
   else:
    if a['enabled'] and not self.powered:self.power_epoch+=1;self._recall()  # power-up: the last memory's conditions (unregistered ones are lost)
    self.powered=a['enabled'];out=a
  elif capability=='power.interrupt':
   self.powered=False
   if self.phase=='running':self.phase='cancelled';self.mode='power_loss_braking'
   out=dict(phase=self.phase)
  elif capability=='lid.set_open':
   if a['open'] and (self.phase=='running' or self.rpm>1e-9 or not self.powered):
    raise _rejected('PRECONDITION_FAILED','the lid lock opens only with the power on' if not self.powered else 'the lid stays locked while the rotor turns')
   self.lid_open=a['open'];out=a
  elif capability=='balance.set':
   if self.lid_angle<1.4:raise _rejected('PRECONDITION_FAILED','open the lid fully before turning the balance knob')
   self.balance=a['gross_mass_kg'];out=a
  elif capability=='acquisition.start':self._start();out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase!='running':raise _rejected('PRECONDITION_FAILED','no run is in progress')
   self.phase='cancelled';self.mode='braking';out=dict(phase=self.phase)
  elif capability=='mixing.toggle':
   if self.phase=='running':self.phase='cancelled';self.mode='braking';out=dict(phase=self.phase)
   else:self._start();out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='mixing.measure':return dict(phase=self.phase,mode=self.mode,revolution_rpm=round(self.rpm),remaining_s=self.remaining(),stage_index=self.stage_index,temperature_K=None,evidence='ideal_panel_quantization_not_factory_precision;no_optional_MultiSensor',template_source=TEMPLATE_REL)
  elif capability=='product.read':return dict(sample_id=self.sample['sample_id'] if self.sample else '',mass_kg=self.mass.copy(),initial_mass_kg=self.initial.copy(),collected_mass_kg=self.collected.copy(),temperature_K=self.temperature,mixing_index=self.mixed,entrained_gas_mol=self.gas,headspace_gas_mol=self.headgas,collected_gas_mol=self.collected_gas,dissipated_energy_J=self.heat,heat_to_ambient_J=self.loss,thermal_storage_J=self.storage,collected_sensible_heat_J=self.collected_heat,evidence='synthetic_mixing_escape_rates;NIST_Cp_not_mixing_curve',template_source=TEMPLATE_REL)
  elif capability=='product.collect':
   if not self.sample or self.phase=='idle' or self.lid_angle<1.4:
    raise _rejected('PRECONDITION_FAILED','no cup is loaded' if not self.sample else 'nothing has been mixed yet' if self.phase=='idle' else 'open the lid fully first')
   oldcap=self.capacity;moved=[m*a['fraction'] for m in self.mass]
   for i,m in enumerate(moved):self.mass[i]-=m;self.collected[i]+=m
   gas=self.gas*a['fraction'];self.gas-=gas;self.collected_gas+=gas;self.capacity=self.mass[0]*self.templates[self.sample['carrier_id']]['value']+self.mass[1]*self.sample['solid_cp_J_kg_K']+self.plant['vessel_heat_capacity_J_K'];carried=(oldcap-self.capacity)*(self.temperature-self.sample['temperature_K']);self.storage-=carried;self.collected_heat+=carried;out=dict(transferred_kg=sum(moved),recipient_id=a['recipient_id'])
  elif capability in CONDITION_CAPS:out=self._conditions(capability,a)
  elif capability.startswith('fault.'):out=self._fault(capability,a)
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
        return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,
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
                result={},observations={},error=dict(code=str(exc),message=getattr(exc,'detail',None) or str(exc),retryable=False,side_effects='none'))

    def _invoke(self,*,instance_id,capability_id,arguments,request_id):
        self._target(instance_id); h=self._host
        if not isinstance(capability_id,str) or (capability_id not in CAPS and capability_id not in PANEL_CAPS): raise ActionRejected('UNKNOWN_CAPABILITY')
        if self._role=='agent' and visibility(capability_id)!='agent': raise ActionRejected('ACCESS_DENIED')
        if not isinstance(request_id,str) or not request_id: raise ActionRejected('INVALID_ARGUMENT')
        validate_arguments(capability_id,arguments)
        key=(self._principal,instance_id,request_id); digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
        if key in h._requests:
            old,result=h._requests[key]
            if old!=digest: raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(result)
        started=h.operation_id; h._caller=self._principal   # a panel gesture acts as this session's caller (stop guard in the host)
        try: result=h.invoke(capability_id,**arguments)
        finally: h._caller=None
        op=result.get('operation_id')
        if op: h._owners.setdefault(op,self._principal)
        if h.operation_id!=started: h._owners.setdefault(h.operation_id,self._principal)   # a run started by any command (panel START, mixing.toggle) belongs to the caller
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
 immediate={'plant.configure':['plant.configured'],'method.configure':['method.configured','cycle.remaining_s','panel.remaining_s'],'sample.configure':['sample.configured']+[k for k in slots if k.startswith('truth.')],'power.set':['power.enabled','conditions.step','conditions.current','conditions.working','cycle.phase','cycle.mode'],'power.interrupt':['power.enabled','cycle.phase','cycle.mode'],'lid.set_open':['lid.open_target','lid.locked'],'balance.set':['balance.gross_mass_kg','balance.dial_angle_rad'],'acquisition.start':['cycle.phase','cycle.mode','cycle.elapsed_s','cycle.stage_index','cycle.remaining_s','panel.remaining_s','lid.locked'],'acquisition.stop':['cycle.phase','cycle.mode'],'mixing.toggle':['cycle.phase','cycle.mode','cycle.elapsed_s','cycle.stage_index','cycle.remaining_s','panel.remaining_s','lid.locked'],'product.collect':[k for k in slots if k.startswith('truth.')],
  'conditions.set_mode':['conditions.mode','conditions.memory','conditions.step','conditions.current','conditions.working'],'conditions.select_memory':['conditions.memory','conditions.step','conditions.current','conditions.working'],
  'conditions.select_step':['conditions.step','conditions.current'],'conditions.set_step':['conditions.current','conditions.working'],'conditions.register':['conditions.memories'],
  'fault.inject':['fault.code','cycle.phase','cycle.mode'],'fault.acknowledge':['fault.code']}
 panel_fx=list(dict.fromkeys(x for c in (_panel_ui.spec_capabilities(PANEL_SPEC) if PANEL_SPEC else []) for x in immediate.get(c,[])))
 for k,(inp,out,components,meaning,kind,vis,example) in CAPS.items():
  async_=k=='acquisition.start';caps[k]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.thinky_are310s.ThinkyAre310sSession.invoke',invocation=f'session.invoke(instance_id="are310s_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if kind=='query' else [dict(description_zh='装杯、质量、兼容性、盖锁与宿主配平容差；非安全认证',state_ids=['cycle.phase','sample.configured','power.enabled','lid.angle_rad','balance.gross_mass_kg'],enforced_by='runtime',check='ThinkyAre310sRuntime.invoke capability guards')],state_effects=[dict(state_id=i,timing='immediate',description_zh='配置、目标或有限转移') for i in immediate.get(k,[])]+([dict(state_id=i,timing='on_step',description_zh='唯一宿主时钟推进加减速、公自转、热量、混合和气体转移') for i in slots if i.startswith(('truth.','cycle.','rotor.','cup.','panel.','lid.'))] if k in ['acquisition.start','acquisition.stop','mixing.toggle','lid.set_open','power.interrupt','fault.inject'] else []),observes=[],model_effects=dict(mode='none',description_zh='实际场景验证后才声明联动；未求解流体',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='程序结束且制动停稳；非无泡质量认证' if async_ else '配置、目标或查询成功',completion_state_ids=['cycle.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['finite_cup','host_clock','principal_request_cache'],partial_failure_zh='取消保留混合、气体与热量；仍需step制动，盖锁保留'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT','UNKNOWN_REFERENCE','REFERENCE_RANGE','CAPACITY_LIMIT']],example_arguments=copy.deepcopy(example),verification=[],limitations=['SCE速度仅为显式教学包络，非UL手册认证；过程系数需标定'],source_ids=['manufacturer_primary','manufacturer_manual','simulation_assumption'])
 for k,c in PANEL_CAPS.items():                                    # front-panel gestures: the commands the panel calls change what they change
  query=k in ('panel.capture','panel.frame');caps[k]=dict(function_zh=c['function_zh'],kind=c['kind'],visibility=c['visibility'],component_ids=c['component_ids'],availability='implemented',input_schema=copy.deepcopy(c['input_schema']),output_schema=copy.deepcopy(c['output_schema']),implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.thinky_are310s.ThinkyAre310sSession.invoke',invocation=f'session.invoke(instance_id="are310s_01",capability_id={k!r},arguments={c["example_arguments"]!r},request_id="unique")'),preconditions=[] if query else [dict(description_zh='已声明plant；断电时显示与指示灯全暗、只有电源开关起作用；运转中设置键无效、OPEN键无效；被调用能力的前提同样适用，被拒时显示仪器的Err代码或不响应，状态不变',state_ids=['power.enabled','cycle.phase','lid.locked'],enforced_by='runtime',check='ThinkyAre310sRuntime._panel_command')],state_effects=[] if query else [dict(state_id=i,timing='immediate',description_zh='手势调用的命令即时改变的量') for i in panel_fx],observes=[],model_effects=dict(mode='none',description_zh='按键关节触发与面板纹理属于后续Matterix步骤',binding_ids=[]),execution=dict(mode='synchronous',time_domain='simulation',completion_criterion='手势瞬时完成；结果只说明是否被仪器接受，显示须用panel.capture查看' if not query else '查询即时返回，不改变状态',completion_state_ids=[],timeout_s=None,cancellation='unsupported',retry='same_request_id',resources=['host_clock'],partial_failure_zh='一次手势可调用多个命令（START：载入面板条件再启动）；被拒的命令不改变状态'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','REQUEST_CONFLICT']],example_arguments=copy.deepcopy(c['example_arguments']),verification=[],limitations=['面板层按panel_ui.json（手册页码证据）解释手势并调用本运行层的同名能力；手势不推进仿真时钟'],source_ids=['manufacturer_manual','simulation_assumption'])
 mechanism=dict(component_ids=['rotor','cup_holder','lid','counterweight','display_digits'],state_ids=list(slots),meaning_zh='45度杯公自转；有限非挥发双相样品与杯内气体守恒',implementation='lumped',equations=['Gross=liquid+solid+cup+adapter<=0.310kg;net<=0.250kg;150mL conservative family limits0.200kg/120mL','SCE reference:mix0 or200..2000rpm,spin=0.4*orbit;defoam0 or400..2200rpm,spin=(60/2200)*orbit;10rpm increments','Only step moves lid/integrates acceleration-limited orbit/opposite spin; timer includes ramp; completion waits for brake','I_next=1-(1-I)*exp(-host_kmix*(spin/800)*(mu_ref/mu)*dt); synthetic not CFD','n_next=n*exp(-host_kescape*(orbit/2200)^2*(mu_ref/mu)*dt); escaped gas stays in headspace','n_initial=n_entrained+n_headspace+n_collected; no vacuum/dissolution/reaction','Pheat=host_Pref*(orbit/2000)^2;900VA electrical apparent input is NOT sample heating W','C=m_liquid*Cp_NIST+m_solid*Cp_host+C_cup_host;C*dT/dt=Pheat-UA*(T-Tambient)','E=thermal_storage+ambient_loss+collected_heat;species initial=remaining+collected','Cp guarded local interval; boundary failure is not a factory overtemperature sensor'],parameters={},source_ids=['manufacturer_primary','manufacturer_manual','nist_cp','simulation_assumption'],limitations=['UL-specific numeric speed and orbit radius not verified;SCE parameters explicit task assumptions.','79 real liquid Cp references are not50 measured mixing/defoaming curves;density,viscosity,compatibility and kinetics remain host input.','No vacuum/CFD/bubble-size/surface-tension/coalescence/dissolution/vapor-pressure/resin-curing model.','300mL installed geometry;150mL cup and adapter parked; runtime150 cup not dynamically swapped.','Counterweight mass/travel,lock firmware,sensor errors and power-loss coast uncalibrated.','No optional MultiSensor/ENS10/touch UI/communications; recipe storage is the panel memory of the manual (5 memories per mode, 5 steps).','Temperature evaluator-only;normal panel temperature null.','Part masses/inertia/friction unknown;0.1kg numerical parts and bounding-box inertias not actual decomposition.'])
 spec=importlib.util.spec_from_file_location('_thinky_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'mixing':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.configure':(SAMPLE,example_sample())});mechanism['parameters']={k.lower():v for k,v in mechanism['parameters'].items()}
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'mixing':mechanism})

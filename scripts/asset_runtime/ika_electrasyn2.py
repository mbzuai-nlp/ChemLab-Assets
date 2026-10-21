"""ElectraSyn standard Package. Conditional lumped redox circuit, not reaction-yield prediction.

Measured first reduction references in DMF versus SCE; explicit host-assumed kinetics,
conductivity, polarization, passivation, Faradaic efficiency and temperature. No RE fitted.

Front panel (panel.press / panel.turn / panel.capture / panel.frame): the TFT run screen, the IKA Menu Guide (main menu, new-experiment
guide, Smart Assist, saved experiments, edit screen, CV parameters, graph) with the knob and the capacitive keys ON/OFF, Menu, CV, Back, Graph,
Lock, from the operating instructions (panel_ui.json beside the contract), run by the generic engine _panel_ui.py on top of the capabilities
below (experiment.*, assist.configure, cv.configure and lock.set are the device functions behind those screens).
"""
from __future__ import annotations
import copy,hashlib,json,math,tempfile,weakref
from asset_runtime import _panel_ui
from asset_runtime import _annotations as _ann
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT,PHASE
from asset_runtime.antonpaar_abbematadvanced import validate
ROOT=Path(__file__).resolve().parents[2];ASSET_TYPE_ID='instrument.ika_electrasyn2';CATALOG_ID='CHEM-016';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/ika_electrasyn2_redox_library/templates.json';F=96485.33212;R=8.314462618;RPM=2*math.pi/60
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
LIFT=number('m',0,.08);SPEED=number('rad/s',0,1500*RPM);CURRENT=number('A',0,.1);VOLTAGE=number('V',0,30);CHARGE=number('C',0)
POINT=obj({'time_s':number('s',0),'current_a':nullable(CURRENT),'cell_voltage_v':nullable(VOLTAGE),'charge_c':CHARGE})
RESULT=obj({'current_a':nullable(CURRENT),'cell_voltage_v':nullable(VOLTAGE),'charge_c':CHARGE,'speed_rad_s':SPEED,'phase':PHASE,'fault':TEXT,'voltage_reference':TEXT,'we_reference_potential_v':{'type':'null'},'conversion_fraction':{'type':'null'},'yield_fraction':{'type':'null'},'template_source':TEXT,'evidence':TEXT})
SAMPLE=obj({'container_id':TEXT,'working_substance_id':TEXT,'counter_substance_id':TEXT,'volume_m3':number('m^3',1e-6,1e-5),'mass_kg':number('kg',.0001,.1),'working_mol':number('mol',1e-8,.1),'counter_mol':number('mol',1e-8,.1),'working_reduced_fraction':number('1',.001,.999),'counter_reduced_fraction':number('1',.001,.999),'temperature_k':number('K',278.15,323.15),'solvent':{'type':'string','enum':['DMF']},'conductivity_s_m':number('S/m',1e-6,100),'electrode_area_m2':number('m^2',1e-7,.001),'electrode_gap_m':number('m',.0001,.02),'polarization_resistance_ohm':number('ohm',0,1e6),'passivation_ohm_per_c':number('ohm/C',0,1e5),'faradaic_efficiency':number('1',.001,1),'compatible_10ml_vial':BOOL})
EXAMPLE=dict(container_id='electra10_01',working_substance_id='elhajj2024_q003',counter_substance_id='elhajj2024_q002',volume_m3=5e-6,mass_kg=.005,working_mol=.001,counter_mol=.001,working_reduced_fraction=.1,counter_reduced_fraction=.9,temperature_k=298.15,solvent='DMF',conductivity_s_m=.2,electrode_area_m2=.0001,electrode_gap_m=.005,polarization_resistance_ohm=20.,passivation_ohm_per_c=1.,faradaic_efficiency=.8,compatible_10ml_vial=True)
METHOD=obj({'mode':{'type':'string','enum':['constant_current','constant_cell_voltage']},'current_a':CURRENT,'cell_voltage_v':VOLTAGE,'voltage_limit_v':{'type':'number','enum':[10.,30.],'x-unit':'V'},'stop_by':{'type':'string','enum':['time','charge']},'duration_s':number('s',.01,360000),'charge_target_c':number('C',.000001,36000)})
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','touch_power','potentiostat'],'电源/待机；断电取消电解与搅拌但保留电量','device_command','agent',{'enabled':True},['power.enabled','cycle.phase','stir.actual_rad_s']),
 'cap.set_lift':(obj({'lift_m':LIFT}),obj({'lift_m':LIFT}),['electrode_cap','working_electrode','counter_electrode'],'电极盖示意升降；电解运行期间禁止移动','device_command','agent',{'lift_m':.08},['cap.lift_m']),
 'electrodes.configure':(obj({'working_material':{'type':'string','enum':['graphite']},'counter_material':{'type':'string','enum':['graphite']},'connected':BOOL}),obj({'configured':BOOL}),['working_electrode','counter_electrode','electrode_connector'],'宿主确认标配两石墨电极连接；不伪造参比','configuration','evaluator',{'working_material':'graphite','counter_material':'graphite','connected':True},['electrodes.connected']),
 'sample.configure':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['sample_vial','vial_interface'],'宿主装入10mL瓶与显式假设的电化学边界','configuration','evaluator',EXAMPLE,['sample.loaded','attachment.container_id','truth.working_reduced_mol','truth.counter_reduced_mol','sample.input_kg','sample.retained_kg','sample.input_m3','sample.retained_m3']),
 'sample.unload':(obj({}),obj({'unloaded':BOOL,'attachment_event':TEXT}),['sample_vial','vial_interface'],'断流开盖后宿主移出，记录事件而非创建约束','configuration','evaluator',{},['sample.loaded','attachment.container_id','sample.retained_kg','sample.removed_kg','sample.retained_m3','sample.removed_m3']),
 'method.configure':(METHOD,obj({'configured':BOOL}),['display_digits','potentiostat'],'恒电流或两电极恒槽压；按时间或电量停止','configuration','agent',dict(mode='constant_current',current_a=.01,cell_voltage_v=4.,voltage_limit_v=10.,stop_by='time',duration_s=2.,charge_target_c=.02),['method.mode','method.current_a','method.cell_voltage_v','method.voltage_limit_v','method.stop_by','method.duration_s','method.charge_target_c']),
 'stir.set_speed':(obj({'speed_rad_s':SPEED}),obj({'speed_rad_s':SPEED}),['knob_encoder','stir_motor','stir_bar'],'仅设定目标；非零50–1500rpm，取最近10rpm设置档','device_command','agent',{'speed_rad_s':600*RPM},['stir.target_rad_s']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['potentiostat','current_sensor','voltage_sensor'],'供电、装瓶、闭盖、电极连接后启动异步电解','device_command','agent',{},['cycle.phase','cycle.elapsed_s','electrochem.charge_c']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['potentiostat'],'取消后保留电量、形式库存与钝化残留；搅拌另控','device_command','agent',{},['cycle.phase','electrochem.indicated_current_a','electrochem.indicated_cell_voltage_v']),
 'electrochem.measure':(obj({}),RESULT,['display_digits','current_sensor','voltage_sensor'],'读取量化电流/槽压/积分电量；无参比读数或真实产率','query','agent',{},[]),
 'history.read':(obj({}),obj({'records':{'type':'array','items':POINT},'truncated':BOOL,'template_source':TEXT,'evidence':TEXT}),['display_digits','micro_usb'],'最多1000点的1秒合成记录；非真实USB导出','query','agent',{},[]),
 'product.read':(obj({}),obj({'mass_kg':number('kg',0),'working_substance_id':nullable(TEXT),'counter_substance_id':nullable(TEXT),'formal_electron_extent_mol':number('mol',0),'conversion_fraction':{'type':'null'},'yield_fraction':{'type':'null'},'evidence':TEXT}),['sample_vial'],'宿主形式电子账本；不把条件性电量模型称为化学产率','query','evaluator',{},[]),
}
SLOTS={}
def slot(k,s,u,o='derived',v='agent',c='display_digits',w=None):SLOTS[k]=(s,u,o,v,[c],w)
slot('power.enabled',BOOL,'1','setpoint',c='power_inlet',w=('power.set','enabled'))
slot('cap.lift_m',LIFT,'m','scene',c='electrode_cap',w=('cap.set_lift','lift_m'))
slot('stir.target_rad_s',SPEED,'rad/s','setpoint',c='knob_encoder',w=('stir.set_speed','speed_rad_s'))
for k,s,u in [('cycle.phase',PHASE,'1'),('cycle.elapsed_s',number('s',0),'s'),('cycle.fault',TEXT,'1'),('sample.loaded',BOOL,'1'),('attachment.container_id',nullable(TEXT),'1'),('electrodes.connected',BOOL,'1'),('stir.actual_rad_s',SPEED,'rad/s'),('electrochem.indicated_current_a',nullable(CURRENT),'A'),('electrochem.indicated_cell_voltage_v',nullable(VOLTAGE),'V'),('electrochem.charge_c',CHARGE,'C')]:slot(k,s,u)
for k,s in METHOD['properties'].items():slot('method.'+k,s,s.get('x-unit','1'),'setpoint')
for k,u in [('working_reduced_mol','mol'),('working_oxidized_mol','mol'),('counter_reduced_mol','mol'),('counter_oxidized_mol','mol'),('extent_mol','mol'),('sample_charge_c','C'),('target_charge_c','C'),('side_charge_c','C'),('resistance_ohm','ohm'),('current_a','A'),('cell_voltage_v','V'),('electrical_energy_j','J'),('ohmic_energy_j','J'),('redox_work_j','J')]:slot('truth.'+k,nullable(number(u)),u,'process_truth','evaluator','sample_vial')
for axis,u in [('kg','kg'),('m3','m^3')]:
 for k in ['input','retained','removed']:slot(f'sample.{k}_{axis}',number(u,0),u,'process_truth','evaluator','sample_vial')
# ---------------------------------------------------------------- IKA Menu Guide functions (operating instructions 06/2018 p.6-7, p.15-16, p.20)
ASSET_VERSION='1.1.2';ASSET_DIR=ROOT/'assets/expansion_batch03/t5_automation/ika_electrasyn2';MEMORY_SLOTS=5;CONTINUOUS_S=360000.
ENUM=lambda *v:{'type':'string','enum':list(v)};SLOT_NO={'type':'integer','x-unit':'1','minimum':1,'maximum':MEMORY_SLOTS}
EXPERIMENT=obj({'duration_mode':ENUM('charge','timer','continuous'),'substrate_mmol':nullable(number('mmol',.01,10)),'equivalents':nullable(number('F/mol',.1,20)),'alternate_polarity':BOOL,'interval_s':number('s',1,3600)})
EXPERIMENT_DEFAULT=dict(duration_mode='timer',substrate_mmol=None,equivalents=None,alternate_polarity=False,interval_s=60.)
CVSET=obj({'segments':{'type':'integer','x-unit':'1','minimum':1,'maximum':20},'initial_v':number('V',-10,10),'direction':ENUM('rising','falling'),'upper_v':number('V',-10,10),'lower_v':number('V',-10,10),'final_v':number('V',-10,10),'sweep_v_s':number('V/s',.01,1)})
CV_DEFAULT=dict(segments=3,initial_v=0.,direction='rising',upper_v=1.5,lower_v=-1.,final_v=-.1,sweep_v_s=.4)   # the manual's example (p.20)
ASSIST_V={'low':2.,'medium':4.,'high':6.}
_METHOD_SLOTS=['method.'+k for k in METHOD['properties']]
CAPS.update({
 'experiment.configure':(EXPERIMENT,obj({'configured':BOOL}),['display_digits','potentiostat'],'New experiment向导中电解方法以外的设定：持续方式（总电量/定时/连续）、底物mmol与电子当量F/mol（运行屏Charge Left）、交替极性与间隔（p.15；运行中每个间隔交换两支石墨电极的角色，钝化膜按电极分别累积）','configuration','agent',dict(EXPERIMENT_DEFAULT,alternate_polarity=True),['experiment.settings']),
 'experiment.save':(obj({'slot':SLOT_NO}),obj({'saved':BOOL}),['display_digits'],'保存实验参数（方法与向导设定）到存储位1–5（p.15步骤8）','configuration','agent',{'slot':1},['experiment.memory']),
 'experiment.load':(obj({'slot':SLOT_NO}),obj({'loaded':BOOL}),['display_digits','potentiostat'],'调出已存实验：恢复方法与向导设定（之后在edit屏start或edit）','configuration','agent',{'slot':1},_METHOD_SLOTS+['experiment.settings']),
 'assist.configure':(obj({'power_level':ENUM('low','medium','high'),'substrate_mmol':number('mmol',.01,10),'equivalents':number('F/mol',.1,20)}),obj({'current_a':CURRENT,'charge_target_c':CHARGE}),['display_digits','potentiostat','current_sensor','voltage_sensor'],'Smart Assist（p.16）：按功率档low/medium/high对应2/4/6 V槽压（无参比），由当前电池电路求起始电流，设为恒电流电解，按mmol与F/mol求总电量后停止','configuration','agent',dict(power_level='medium',substrate_mmol=.5,equivalents=2.),_METHOD_SLOTS+['experiment.settings']),
 'cv.configure':(CVSET,obj({'configured':BOOL}),['display_digits'],'循环伏安参数（段数、起始/上限/下限/终止电位、方向、扫速，p.20）；本标准包无CV电极组（参比、玻碳、铂），CV运行在面板上不支持','configuration','agent',CV_DEFAULT,['cv.parameters']),
 'lock.set':(obj({'locked':BOOL}),obj({'locked':BOOL}),['touch_lock','display_digits'],'Lock键：锁定全部操作元件，屏幕显示钥匙图标（p.6-7）；只作用于前面板','device_command','agent',{'locked':True},['controls.locked'])})
slot('experiment.settings',EXPERIMENT,'1','setpoint',w=None);slot('experiment.memory',{'type':'array','items':nullable({'type':'object'}),'minItems':MEMORY_SLOTS,'maxItems':MEMORY_SLOTS},'1','setpoint')
slot('cv.parameters',CVSET,'1','setpoint');slot('controls.locked',BOOL,'1','setpoint',c='touch_lock',w=('lock.set','locked'));slot('display.run',{'type':'object'},'1')
PANEL_SPEC=_panel_ui.load_spec(ASSET_DIR/'panel_ui.json',capabilities=set(CAPS))
PANEL_CAPS=_panel_ui.capability_definitions(PANEL_SPEC)      # panel.press / panel.turn / panel.capture / panel.frame
PANEL_GESTURES=[k for k in PANEL_CAPS if k not in ('panel.capture','panel.frame')]
_PANELS=weakref.WeakKeyDictionary()                                        # runtime -> its panel (kept out of the runtime's own data)


class IkaElectrasyn2Runtime:
 def __init__(self,instance_id='electrasyn2_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.lift=0.;self.connected=False;self.target_speed=0.;self.actual_speed=0.;self.sample=None;self.phase='idle';self.elapsed_s=0.;self.fault='';self.current=0.;self.voltage=0.;self.charge=0.;self.run_start_s=0.;self.operation_id=None;self._operation_count=0
  self.method=copy.deepcopy(CAPS['method.configure'][6]);self.history=[];self.history_truncated=False;self.next_record_s=0.;self.input_kg=0.;self.removed_kg=0.;self.input_m3=0.;self.removed_m3=0.;self.extent=0.;self.sample_charge=0.;self.electrical_energy=0.;self.ohmic_energy=0.;self.redox_work=0.
  self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
  self.experiment=dict(EXPERIMENT_DEFAULT);self.memory=[None]*MEMORY_SLOTS;self.cv=dict(CV_DEFAULT);self.locked=False;self.power_epoch=0;self.panel_capture_dir=None
  self.film=[0.,0.]                                                    # charge passed while each physical electrode held the fouling (working) role
  self._caller=None                                                    # session principal while a session call runs (panel gestures act as that caller)
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return IkaElectrasyn2Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _redox(self):
  s=self.sample;wr=s['working_mol']*s['working_reduced_fraction']+self.extent;cr=s['counter_mol']*s['counter_reduced_fraction']-self.extent
  ew=s['working_e0']+R*s['temperature_k']/F*math.log(max(1e-15,s['working_mol']-wr)/max(1e-15,wr));ec=s['counter_e0']+R*s['temperature_k']/F*math.log(max(1e-15,s['counter_mol']-cr)/max(1e-15,cr))
  resistance=s['electrode_gap_m']/(s['conductivity_s_m']*s['electrode_area_m2'])+s['polarization_resistance_ohm']+s['passivation_ohm_per_c']*self.film[self._pole()]
  return wr,cr,ec-ew,resistance
 def _pole(self):
  """Physical electrode in the working (fouling) role: 0 normally; with Alternate polarity the device swaps the electrodes every interval of
  the run (manual p.15 step 7), so the other electrode takes the role in every second interval."""
  e=self.experiment
  return 1 if e['alternate_polarity'] and self.phase=='running' and int(self.elapsed_s//e['interval_s'])%2==1 else 0
 def _values(self):
  s=self.sample;wr,cr,emf,res=self._redox() if s else (None,None,None,None)
  v={'power.enabled':self.powered,'cap.lift_m':self.lift,'stir.target_rad_s':self.target_speed,'stir.actual_rad_s':self.actual_speed,'cycle.phase':self.phase,'cycle.elapsed_s':self.elapsed_s,'cycle.fault':self.fault,'sample.loaded':bool(s),'attachment.container_id':s['container_id'] if s else None,'electrodes.connected':self.connected,'electrochem.indicated_current_a':round(self.current/.0001)*.0001 if self.powered and s else None,'electrochem.indicated_cell_voltage_v':round(self.voltage,1) if self.powered and s else None,'electrochem.charge_c':self.charge}
  v['electrochem.indicated_cell_voltage_v']=round(self.voltage,1) if self.powered and s else None
  for k in self.method:v['method.'+k]=self.method[k]
  vals={'working_reduced_mol':wr,'working_oxidized_mol':s['working_mol']-wr if s else None,'counter_reduced_mol':cr,'counter_oxidized_mol':s['counter_mol']-cr if s else None,'extent_mol':self.extent,'sample_charge_c':self.sample_charge,'target_charge_c':F*self.extent,'side_charge_c':self.sample_charge-F*self.extent,'resistance_ohm':res,'current_a':self.current,'cell_voltage_v':self.voltage,'electrical_energy_j':self.electrical_energy,'ohmic_energy_j':self.ohmic_energy,'redox_work_j':self.redox_work}
  for k,x in vals.items():v['truth.'+k]=x if s else None
  for ax in ['kg','m3']:v['sample.input_'+ax]=getattr(self,'input_'+ax);v['sample.removed_'+ax]=getattr(self,'removed_'+ax);v['sample.retained_'+ax]=s['mass_kg' if ax=='kg' else 'volume_m3'] if s else 0.
  v.update({'experiment.settings':dict(self.experiment),'experiment.memory':copy.deepcopy(self.memory),'cv.parameters':dict(self.cv),'controls.locked':self.locked,'display.run':self._run_display()})
  return v
 def _duration_mode(self):
  """Duration of the run screen: the guide's choice when it matches the method (charge <-> stop by charge), else from the method."""
  m,e=self.method,self.experiment
  if m['stop_by']=='charge':return 'charge'
  return e['duration_mode'] if e['duration_mode'] in ('timer','continuous') else 'timer'
 def _run_display(self):
  """Fields 04-07 and 15 of the run screen (p.7): polarity, timer / remaining charge, estimated duration / charge counter."""
  m,e=self.method,self.experiment;mode=self._duration_mode();running=self.phase=='running'
  rev=self._pole()==1
  left_c=max(0.,m['charge_target_c']-(self.charge if running else 0.))
  fmol=None if mode!='charge' or not e['substrate_mmol'] else left_c/(F*e['substrate_mmol']/1000.)
  remaining_s=max(0.,m['duration_s']-(self.elapsed_s if running else 0.))
  est=remaining_s if mode=='timer' else (left_c/m['current_a'] if mode=='charge' and m['mode']=='constant_current' and m['current_a']>0 else None)
  return dict(duration_mode=mode,polarity_reversed=rev,charge_left_f_mol=fmol,remaining_s=remaining_s if mode=='timer' else None,
              elapsed_s=self.elapsed_s,est_time_s=est,charge_counter_c=self.charge,charge_active=running,motor_active=self.actual_speed>0)
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('debug','evaluator')})
 def debug_snapshot(self):return dict(state=self.read('evaluator'),sample=copy.deepcopy(self.sample),history=copy.deepcopy(self.history))
 def _measure(self):
  v=self._values();return dict(current_a=v['electrochem.indicated_current_a'],cell_voltage_v=v['electrochem.indicated_cell_voltage_v'],charge_c=self.charge,speed_rad_s=self.actual_speed,phase=self.phase,fault=self.fault,voltage_reference='two_electrode_cell_CE_minus_WE; not vs SCE',we_reference_potential_v=None,conversion_fraction=None,yield_fraction=None,template_source=TEMPLATE_REL,evidence='measured_reference_potentials_with_synthetic_unvalidated_circuit_dynamics')
 def invoke(self,capability,**a):
  if capability in PANEL_CAPS:return self._panel_command(capability,a)
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if self.phase=='running' and capability not in ('power.set','acquisition.stop','electrochem.measure','history.read','product.read','stir.set_speed','lock.set'):raise ActionRejected('BUSY')
  if capability=='power.set':
   if a['enabled'] and not self.powered:self.power_epoch+=1
   self.powered=a['enabled']
   if not self.powered:
    if self.phase=='running':self.phase='cancelled'
    self.current=0.;self.voltage=0.;self.actual_speed=0.
   out=dict(a)
  elif capability=='cap.set_lift':self.lift=a['lift_m'];out=dict(a)
  elif capability=='electrodes.configure':self.connected=a['connected'];out={'configured':True}
  elif capability=='sample.configure':
   if self.sample or self.lift<.079 or not a['compatible_10ml_vial']:raise ActionRejected('PRECONDITION_FAILED')
   w=self.templates.get(a['working_substance_id']);c=self.templates.get(a['counter_substance_id'])
   if not w or not c or not w['runtime_lumped_eligible'] or not c['runtime_lumped_eligible']:raise ActionRejected('PRECONDITION_FAILED')
   # Nominally endergonic paired redox only; discharge/reversal intentionally unsupported.
   if c['values']['first_reduction_v']<=w['values']['first_reduction_v']:raise ActionRejected('PRECONDITION_FAILED')
   self.sample=dict(a,working_e0=w['values']['first_reduction_v'],counter_e0=c['values']['first_reduction_v']);self.extent=0.;self.sample_charge=0.;self.film=[0.,0.];self.electrical_energy=0.;self.ohmic_energy=0.;self.redox_work=0.;self.input_kg+=a['mass_kg'];self.input_m3+=a['volume_m3'];out=dict(configured=True,attachment_event='host_attach:'+a['container_id'])
  elif capability=='sample.unload':
   if not self.sample or self.lift<.079 or self.actual_speed>1e-9:raise ActionRejected('PRECONDITION_FAILED')
   cid=self.sample['container_id'];self.removed_kg+=self.sample['mass_kg'];self.removed_m3+=self.sample['volume_m3'];self.sample=None;out=dict(unloaded=True,attachment_event='host_release:'+cid)
  elif capability=='method.configure':
   if a['cell_voltage_v']>a['voltage_limit_v'] or abs(a['current_a']/.0001-round(a['current_a']/.0001))>1e-6 or abs(a['cell_voltage_v']/.01-round(a['cell_voltage_v']/.01))>1e-6:raise ActionRejected('INVALID_ARGUMENT')
   self.method=dict(a);out={'configured':True}
  elif capability=='stir.set_speed':
   rpm=a['speed_rad_s']/RPM
   if 0<rpm<50-1e-5:raise ActionRejected('INVALID_ARGUMENT')
   self.target_speed=round(rpm/10)*10*RPM;out={'speed_rad_s':self.target_speed}
  elif capability=='acquisition.start':
   if not self.powered or not self.sample or not self.connected or self.lift>1e-5:raise ActionRejected('PRECONDITION_FAILED')
   wr,cr,emf,res=self._redox()
   if self.sample['working_mol']-wr<=1e-12 or cr<=1e-12 or emf<0:raise ActionRejected('PRECONDITION_FAILED')
   self._operation_count+=1;self.operation_id=f'{self.instance_id}:electrolysis:{self._operation_count}';self.phase='running';self.fault='';self.elapsed_s=0.;self.charge=0.;self.run_start_s=self.time_s;self.history=[];self.history_truncated=False;self.next_record_s=self.time_s+1.;out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase=='running':self.phase='cancelled'
   self.current=0.;self.voltage=0.;out={'phase':self.phase}
  elif capability=='experiment.configure':
   if a['duration_mode']=='charge' and (a['substrate_mmol'] is None or a['equivalents'] is None):raise ActionRejected('INVALID_ARGUMENT')
   self.experiment=dict(a,interval_s=float(a['interval_s']));out={'configured':True}
  elif capability in ('experiment.save','experiment.load'):
   if isinstance(a['slot'],bool) or not isinstance(a['slot'],int):raise ActionRejected('INVALID_ARGUMENT')
   if capability=='experiment.save':self.memory[a['slot']-1]=dict(method=dict(self.method),experiment=dict(self.experiment));out={'saved':True}
   else:
    rec=self.memory[a['slot']-1]
    if rec is None:raise ActionRejected('PRECONDITION_FAILED')
    self.method=dict(rec['method']);self.experiment=dict(rec['experiment']);out={'loaded':True}
  elif capability=='assist.configure':
   if not self.powered or not self.sample or not self.connected:raise ActionRejected('PRECONDITION_FAILED')
   wr,cr,emf,res=self._redox();i=(ASSIST_V[a['power_level']]-emf)/res;q=a['substrate_mmol']/1000.*a['equivalents']*F
   if i<=0 or q>36000:raise ActionRejected('PRECONDITION_FAILED')
   i=min(.1,max(.0001,round(i/.0001)*.0001))
   self.method=dict(mode='constant_current',current_a=i,cell_voltage_v=self.method['cell_voltage_v'] if self.method['cell_voltage_v']<=30 else 4.,voltage_limit_v=30.,stop_by='charge',duration_s=CONTINUOUS_S,charge_target_c=q)
   self.experiment=dict(self.experiment,duration_mode='charge',substrate_mmol=a['substrate_mmol'],equivalents=a['equivalents'],alternate_polarity=False);out={'current_a':i,'charge_target_c':q}
  elif capability=='cv.configure':
   if isinstance(a['segments'],bool) or not isinstance(a['segments'],int) or not a['lower_v']<a['upper_v'] or not a['lower_v']<=a['initial_v']<=a['upper_v'] or not a['lower_v']<=a['final_v']<=a['upper_v']:raise ActionRejected('INVALID_ARGUMENT')
   self.cv=dict(a);out={'configured':True}
  elif capability=='lock.set':self.locked=a['locked'];out=dict(a)
  elif capability=='history.read':return dict(records=copy.deepcopy(self.history),truncated=self.history_truncated,template_source=TEMPLATE_REL,evidence='synthetic_time_series_not_measured_ElectraSyn_trace')
  elif capability=='product.read':return dict(mass_kg=self.sample['mass_kg'] if self.sample else 0.,working_substance_id=self.sample['working_substance_id'] if self.sample else None,counter_substance_id=self.sample['counter_substance_id'] if self.sample else None,formal_electron_extent_mol=self.extent if self.sample else 0.,conversion_fraction=None,yield_fraction=None,evidence='conditional_formal_redox_ledger_not_chemical_product_identification')
  else:return self._measure()
  self.revision+=1;return copy.deepcopy(out)
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<=0:raise ValueError('finite positive dt required')
  left=float(dt_s)
  while left>1e-10:
   h=min(.05,left);self.actual_speed=self.target_speed if self.powered else 0.;self.current=0.;self.voltage=0.;active=self.phase=='running'
   if active:
    s=self.sample;m=self.method;pole=self._pole();wr,cr,emf,res=self._redox();current=m['current_a'] if m['mode']=='constant_current' else min(.1,max(0.,(m['cell_voltage_v']-emf)/res));voltage=emf+current*res
    if voltage>m['voltage_limit_v']+1e-9:self.phase='failed';self.fault='voltage_limit';current=0.
    if active and self.phase=='running':
     run_h=min(h,max(0.,m['duration_s']-self.elapsed_s))
     q=current*run_h
     if m['stop_by']=='charge':q=min(q,max(0.,m['charge_target_c']-self.charge));run_h=q/current if current>0 else run_h
     capacity=max(0.,min(s['working_mol']-wr,cr)-1e-12)*F/s['faradaic_efficiency'];q_used=min(q,capacity)
     if q>capacity+1e-12:self.phase='failed';self.fault='formal_redox_inventory_exhausted';run_h=q_used/current if current else 0.
     self.current=current;self.voltage=voltage if current>0 else min(m['cell_voltage_v'],max(0.,emf)) if m['mode']=='constant_cell_voltage' else 0.;self.charge+=q_used;self.sample_charge+=q_used;self.film[pole]+=q_used;self.extent+=q_used*s['faradaic_efficiency']/F;self.elapsed_s+=run_h;self.electrical_energy+=q_used*voltage;self.ohmic_energy+=q_used*current*res;self.redox_work+=q_used*emf
     if self.phase=='running' and ((m['stop_by']=='charge' and self.charge>=m['charge_target_c']-1e-12) or (m['stop_by']=='time' and self.elapsed_s>=m['duration_s']-1e-9)):self.phase='succeeded'
     elif self.phase=='running' and self.elapsed_s>=m['duration_s']-1e-9:self.phase='failed';self.fault='charge_target_timeout'
   self.time_s+=h;left-=h
   if self.phase!='running':self.current=0.;self.voltage=0.
   if self.operation_id and (self.time_s>=self.next_record_s-1e-9 or (active and self.phase!='running')):
    m=self._measure();self.history.append(dict(time_s=self.time_s,current_a=m['current_a'],cell_voltage_v=m['cell_voltage_v'],charge_c=self.charge));self.next_record_s=self.time_s+1.
    if len(self.history)>1000:self.history.pop(0);self.history_truncated=True
  self.revision+=1;return self.read('evaluator')
 # ---- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
 @property
 def panel(self):
  p=_PANELS.get(self)
  if p is None:p=_PANELS[self]=_panel_ui.PanelUI(PANEL_SPEC,self)
  return p
 def panel_read(self):
  """What the firmware shows: the agent slots plus power, power-up count and the graph data of the last run (mA and V against seconds)."""
  out=self.read('agent');t0=self.run_start_s
  out.update({'host.powered':self.powered,'host.power_epoch':self.power_epoch,
              'host.graph_ma':[[r['time_s']-t0,r['current_a']*1000.] for r in self.history if r['current_a'] is not None],
              'host.graph_v':[[r['time_s']-t0,r['cell_voltage_v']] for r in self.history if r['cell_voltage_v'] is not None]})
  return out
 def panel_invoke(self,capability_id,arguments):
  """A panel action calls the same command a direct call would (each command checks before it changes anything).  A gesture made through a
  session acts as that session's caller: the session's ownership guard applies (stopping or switching off another agent's electrolysis is refused)."""
  try:
   if self._caller is not None:IkaElectrasyn2Session(self,self._caller)._guard(capability_id,arguments)
   return self.invoke(capability_id,**arguments)
  except ActionRejected as exc:raise _panel_ui.PanelRejected(str(exc)) from None
 def panel_time(self):return self.time_s
 def _panel_command(self,capability,a):
  validate(PANEL_CAPS[capability]['input_schema'],a)
  if _panel_ui.check_arguments(PANEL_CAPS[capability]['input_schema'],a):raise ActionRejected('INVALID_ARGUMENT')
  try:
   if capability=='panel.press':out=dict(accepted=bool(self.panel.press(a['input_id'],float(a.get('duration_s',.1)))))
   elif capability=='panel.turn':out=dict(accepted=bool(self.panel.turn(a['input_id'],int(a['detents']))))
   elif capability=='panel.capture':return self._panel_capture(a['display_id'])
   else:return dict(frame=self.panel.frame())
  except _panel_ui.PanelError:raise ActionRejected('INVALID_ARGUMENT') from None
  self.revision+=1;return out
 def _panel_capture(self,display_id):
  """Close-up camera of a display: a PNG file named by its content (large data is returned as a file reference)."""
  data,w,h=self.panel.png_bytes(display_id);sha=hashlib.sha256(data).hexdigest()
  d=Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir())/'chemistry_sim_panel'/self.instance_id
  d.mkdir(parents=True,exist_ok=True);path=d/f'{display_id}_{sha[:16]}.png'
  if not path.exists():path.write_bytes(data)
  return dict(display_id=display_id,time_s=self.time_s,image=dict(path=str(path),format='png',width=w,height=h,sha256=sha))
_CONTROL_GUARDED={'acquisition.stop','power.set','stir.set_speed'}       # commands that end or change another agent's electrolysis
class IkaElectrasyn2Session:
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
        meta=export_ika_electrasyn2_contract_metadata()
        return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,
          capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},
          state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})

    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id); h=self._host; vals=h.read(self._role); ids=list(vals) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids): raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in vals for k in ids): raise ActionRejected('ACCESS_DENIED')
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},
          observations={k:dict(value=copy.deepcopy(vals[k]),unit=SLOTS[k][1],quality='unavailable' if vals[k] is None else ('estimated' if SLOTS[k][2]=='derived' else 'valid'),sample_time_s=h.time_s,sample_window_s=[h.run_start_s,h.run_start_s+h.elapsed_s] if k=='electrochem.charge_c' else None) for k in ids})

    def invoke(self,*,instance_id,capability_id,arguments,request_id):
        try:
            return self._invoke(instance_id=instance_id,capability_id=capability_id,arguments=arguments,request_id=request_id)
        except ActionRejected as exc:
            return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,
                status='rejected',state_revision=self._host.revision,clock={'domain':'simulation','time_s':self._host.time_s},
                result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))

    def _invoke(self,*,instance_id,capability_id,arguments,request_id):
        self._target(instance_id); h=self._host
        if not isinstance(capability_id,str) or (capability_id not in CAPS and capability_id not in PANEL_CAPS): raise ActionRejected('UNKNOWN_CAPABILITY')
        vis,schema=(PANEL_CAPS[capability_id]['visibility'],PANEL_CAPS[capability_id]['input_schema']) if capability_id in PANEL_CAPS else (CAPS[capability_id][5],CAPS[capability_id][0])
        if self._role=='agent' and vis!='agent': raise ActionRejected('ACCESS_DENIED')
        if not isinstance(request_id,str) or not request_id: raise ActionRejected('INVALID_ARGUMENT')
        validate(schema,arguments)
        key=(self._principal,instance_id,request_id); digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
        if key in h._requests:
            old,result=h._requests[key]
            if old!=digest: raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(result)
        before=h.operation_id
        self._guard(capability_id,arguments); h._caller=self._principal
        try: result=h.invoke(capability_id,**arguments)
        finally: h._caller=None
        op=result.get('operation_id')
        if op: h._owners[op]=self._principal
        elif capability_id in PANEL_CAPS and h.operation_id and h.operation_id!=before: h._owners[h.operation_id]=self._principal   # a run started by a panel key belongs to the caller
        response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=op,
          status='accepted' if op else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None)
        h._requests[key]=(digest,copy.deepcopy(response)); return response

    def _guard(self,capability_id,arguments):
        """An agent may not stop, switch off or change (stirring speed) another agent's running electrolysis (direct call or panel knob push /
        turn / ON/OFF key); unowned runs and the evaluator are not restricted."""
        h=self._host
        if self._role!='agent' or capability_id not in _CONTROL_GUARDED or h.phase!='running': return
        if capability_id=='power.set' and (arguments.get('enabled') or not h.powered): return
        if h._owners.get(h.operation_id) not in (None,self._principal): raise ActionRejected('ACCESS_DENIED')

    def write(self,*,instance_id,state_id,value,request_id):
        self._target(instance_id)
        if state_id not in SLOTS or not SLOTS[state_id][5]: raise ActionRejected('ACCESS_DENIED')
        cap,arg=SLOTS[state_id][5]
        return self.invoke(instance_id=instance_id,capability_id=cap,arguments={arg:value},request_id=request_id)

    def poll(self,operation_id):
        h=self._host
        if operation_id!=h.operation_id or h._owners.get(operation_id)!=self._principal: raise ActionRejected('ACCESS_DENIED')
        return dict(operation_id=operation_id,status=h.phase,result=h._measure())

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])

PANEL_PRECONDITION_ZH='面板按当前屏幕与手册规则处理手势：待机时只有ON/OFF键起作用；锁定时只有Lock与ON/OFF键起作用；运行屏转旋钮调转速、按旋钮停止运行或进入edit屏；Menu进入主菜单（New experiment向导、Smart Assist、存储实验），CV键进入CV参数，Graph键显示曲线，Back返回一级；被调用能力的前提同样适用（被拒时显示仪器自己的提示，手势不被接受）'
PANEL_LIMITATION_ZH='面板层按panel_ui.json（操作说明书PDF页码证据）解释手势并调用本运行层的同名能力；手势不推进仿真时钟；手册未写之处（主菜单项、取值范围、交替极性间隔、存储位、提示文字）的选择见panel_ui.json的assumptions。'


def export_ika_electrasyn2_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'IkaElectrasyn2Session.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key=='acquisition.start'; pre=[]
        conditions={
         'acquisition.start':('供电、装瓶、闭盖、电极连接、形式库存与电位条件满足',['power.enabled','sample.loaded','cap.lift_m','electrodes.connected'],'powered and sample and connected and cap_closed and inventory_positive and counter_minus_working_emf>=0'),
         'sample.configure':('受信任宿主、开盖空位、可用非可滴定/非磺酸盐模板',['sample.loaded','cap.lift_m'],'empty and lift>=.079 and compatible_10ml and both_templates_eligible and counter_e0>working_e0'),
         'sample.unload':('受信任宿主、已停止电解与搅拌、开盖有瓶',['sample.loaded','cap.lift_m','cycle.phase','stir.actual_rad_s'],'loaded and lift>=.079 and not running and actual_speed==0'),
         'cap.set_lift':('电解未运行',['cycle.phase'],'phase!=running'),
         'experiment.configure':('电解未运行；按总电量须给底物mmol与F/mol',['cycle.phase'],'not running and (mode!=charge or mmol and equivalents)'),
         'experiment.save':('电解未运行',['cycle.phase'],'not running'),'experiment.load':('电解未运行；存储位已有实验',['cycle.phase','experiment.memory'],'not running and slot saved'),
         'assist.configure':('电解未运行；供电、装瓶、电极连接；所选槽压下电流为正；总电量≤36000 C',['power.enabled','sample.loaded','electrodes.connected','cycle.phase'],'powered and sample and connected and (V-emf)/R>0'),
         'cv.configure':('电解未运行；下限<上限，起始与终止电位在两者之间',['cycle.phase'],'not running and lower<upper and lower<=initial,final<=upper'),
         'lock.set':('无',[],'always')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.ika_electrasyn2.IkaElectrasyn2Session.invoke',invocation=f'session.invoke(instance_id="electrasyn2_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；达到时间或电量目标；取消保留电量、形式库存和钝化' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电不回滚已经发生的电量、形式库存和钝化。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    slots['electrochem.charge_c']['sampling']=dict(mode='integral',time_domain='simulation',window_description='本次acquisition.start到当前累计通电时间；dt唯一推进，取消保留')
    slots['electrochem.indicated_cell_voltage_v']['reference_conditions']='two-electrode CE minus WE cell voltage; not versus SCE; no RE installed'
    truth=[k for k in slots if k.startswith('truth.')]
    additions={'sample.configure':(truth,'immediate'),'sample.unload':(truth,'immediate'),'power.set':(['electrochem.indicated_current_a','electrochem.indicated_cell_voltage_v'],'immediate'),'stir.set_speed':(['stir.actual_rad_s'],'on_step'),'acquisition.start':(truth+['electrochem.indicated_current_a','electrochem.indicated_cell_voltage_v','cycle.fault'],'on_step')}
    for key,(states,timing) in additions.items():
        existing={e['state_id'] for e in caps[key]['state_effects']}
        caps[key]['state_effects'] += [dict(state_id=k,timing=timing,description_zh='由该操作引起的状态重置/后续时钟推进；真实化学产率仍未知') for k in states if k not in existing]
    effects={k:[e['state_id'] for e in v['state_effects']] for k,v in caps.items()}
    panel_fx=list(dict.fromkeys(x for c in _panel_ui.spec_capabilities(PANEL_SPEC) for x in effects.get(c,[])))
    for key,d in PANEL_CAPS.items():
        g=key in PANEL_GESTURES;fx=panel_fx if g else []
        comps=sorted(set(d['component_ids'])|({'display_digits','front_panel'} if key in ('panel.capture','panel.frame') else set()))
        caps[key]=dict(function_zh=d['function_zh'],kind=d['kind'],visibility=d['visibility'],component_ids=comps,availability='implemented',
          input_schema=copy.deepcopy(d['input_schema']),output_schema=copy.deepcopy(d['output_schema']),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.ika_electrasyn2.IkaElectrasyn2Session.invoke',invocation=f'session.invoke(instance_id="electrasyn2_01", capability_id={key!r}, arguments={d["example_arguments"]!r}, request_id="unique")'),
          preconditions=[dict(description_zh=PANEL_PRECONDITION_ZH,state_ids=['power.enabled','controls.locked','cycle.phase'],enforced_by='runtime',check='panel_ui.json screens, gates and bindings')] if g else [],
          state_effects=[dict(state_id=k,timing='immediate',description_zh='面板手势调用的能力的效果（与直接调用同一能力相同）') for k in fx],observes=[],
          model_effects=dict(mode='none',description_zh='屏幕纹理与旋钮/触控键联动属于后续Matterix步骤，本版本只在过程层。',binding_ids=[]),
          execution=dict(mode='synchronous',time_domain='simulation',completion_criterion='手势瞬时完成（不推进仿真时钟）；结果只说明是否被仪器接受，屏幕须用panel.capture查看' if g else '不推进物理时钟',
            completion_state_ids=fx,timeout_s=None,cancellation='unsupported',retry='same_request_id',resources=['target_instance','principal_request_cache'],
            partial_failure_zh='手势调用的每个能力各自先检查再修改；被拒绝时显示仪器自己的反应，手势返回accepted=false。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(d['example_arguments']),verification=[],limitations=[PANEL_LIMITATION_ZH],source_ids=['manufacturer_primary','simulation_assumption'])
    mechanism=dict(component_ids=['sample_vial','potentiostat','working_electrode','counter_electrode','current_sensor','voltage_sensor'],state_ids=truth,meaning_zh='实验首电子电位驱动的条件性形式氧化还原与串联电阻模型，不预测实际合成产物',implementation='lumped',equations=['Ered=Eref+(R*T/F)*ln(n_oxidized/n_reduced); first experimental potential used as assumed formal Eref','Ecell=Ecounter-Eworking; Rcell=gap/(conductivity*area)+Rpolarization+passivation_per_charge*Qfilm[e]; Qfilm[e]=charge passed while physical electrode e held the working (fouling) role; e=0, or with Alternate polarity e alternates every interval (both graphite)','CC: V=Ecell+Iset*Rcell; voltage compliance failure cuts off ideally','CV cell: I=clip((Vset-Ecell)/Rcell,0,0.1); current compliance causes voltage droop','Q=integral(I dt); extent=FE*Q/F; working_reduced+=extent; counter_reduced-=extent','Qsample=F*extent+Qside; no side-product identity or mass prediction','Ein=integral(V*I dt)=integral(I**2*R dt)+integral(Ecell*I dt), numerical accounting only','Retained+removed=loaded mass/volume; formal parent inventories conserved; no universal chemical conversion/yield'],parameters={'faraday_constant':dict(value=F,unit='C/mol',meaning_zh='法拉第常数数值',evidence='assumption',source_ids=['simulation_assumption'],uncertainty=None)},source_ids=['redox_templates','manufacturer_primary','simulation_assumption'],limitations=['文献条件DMF vsSCE，实验温度/电解质/电极未知；宿主温度下Nernst是条件性代理，不是实验外推验证。','非可滴定非磺酸盐205条可用；所有345条文献记录保留，第二次还原只参考。','标准瓶未隔膜，本模型两个形式反应库存忽略交叉反应、传质和溶液活度；不暗示真实可稳定共存。','FE、导电率、面积、间距、极化和钝化均显式工程输入；副产物气体/物种守恒未实现。','质量/体积封闭账本不意味着已经算出真实化学产物；转换率/产率为null。','恒工作电极参比电位、CV扫描、交替极性、脉冲、SmartAssist、完整菜单未实现。'])
    _ann.annotate_export('ika_electrasyn2',caps,slots,{'electrochemical_circuit':mechanism},inputs={})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'electrochemical_circuit':mechanism})

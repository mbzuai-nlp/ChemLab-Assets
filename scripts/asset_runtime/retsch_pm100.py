"""PM100 host-driven teaching model; no DEM, factory firmware or calibrated breakage.

JANAF solid Cp curves are real references. Jar heat transfer, acceleration and
particle-size kinetics are explicit host assumptions, never measured output.
"""
from __future__ import annotations
import bisect,copy,importlib.util,json,math
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.pccell_ed64002 import validate
ROOT=Path(__file__).resolve().parents[2];SLUG='retsch_pm100';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-040';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/retsch_pm100_thermal_library/templates.json'
WMIN=100*math.tau/60;WMAX=650*math.tau/60
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
PLANT=obj({'ambient_k':number('K',298.15,313.15),'heat_fraction':number('1',0,1),'jar_balls_heat_capacity_j_k':number('J/K',100,10000),'loss_w_k':number('W/K',0,20),'acceleration_rad_s2':number('rad/s^2',1,200),'ball_density_kg_m3':number('kg/m^3',1000,20000),'dry_material_reviewed':BOOL,'coefficient_evidence':TEXT})
PLANT_EXAMPLE=dict(ambient_k=298.15,heat_fraction=.15,jar_balls_heat_capacity_j_k=600.,loss_w_k=2.,acceleration_rad_s2=40.,ball_density_kg_m3=7800.,dry_material_reviewed=True,coefficient_evidence='explicit_host_assumptions_not_factory_or_experimental_calibration')
METHOD=obj({'speed_rad_s':number('rad/s',WMIN,WMAX),'duration_s':number('s',.1,359999),'interval_s':number('s',0,359999),'pause_s':number('s',0,359999),'reverse':BOOL,'direction':dict(type='integer',enum=[-1,1],**{'x-unit':'1'})})
METHOD_EXAMPLE=dict(speed_rad_s=300*math.tau/60,duration_s=20.,interval_s=5.,pause_s=2.,reverse=True,direction=1)
SAMPLE=obj({'sample_id':TEXT,'substance_id':TEXT,'jar_volume_m3':dict(type='number',enum=[.0005],**{'x-unit':'m^3'}),'sample_volume_m3':number('m^3',75e-6,220e-6),'ball_diameter_m':dict(type='number',enum=[.02],**{'x-unit':'m'}),'ball_count':dict(type='integer',enum=[25],**{'x-unit':'1'}),'ball_to_sample_ratio':number('1',.1,1000),'initial_d50_m':number('m',1e-9,.01),'max_feed_m':number('m',1e-9,.01),'floor_d50_m':number('m',1e-9,.01),'breakage_rate_s':number('1/s',0,1),'kinetic_evidence':TEXT})
def example_sample():return dict(sample_id='host_alumina',substance_id='al-096',jar_volume_m3=.0005,sample_volume_m3=.0001,ball_diameter_m=.02,ball_count=25,ball_to_sample_ratio=10.,initial_d50_m=.001,max_feed_m=.003,floor_d50_m=1e-6,breakage_rate_s=.002,kinetic_evidence='synthetic_explicit_demonstration_not_measured_PM100_PSD')
MEASURE=obj({'phase':PHASE,'mode':TEXT,'fault':TEXT,'speed_rad_s':number('rad/s',-WMAX,WMAX),'active_s':number('s',0),'wall_s':number('s',0),'remaining_s':number('s',0),'lid_angle_rad':number('rad',0,1.55),'evidence':TEXT,'template_source':TEXT})
PRODUCT=obj({'sample_id':TEXT,'substance_id':TEXT,'mass_kg':number('kg',0),'moles':number('mol',0),'d50_m':number('m',0),'temperature_k':number('K',298.15,500),'evidence':TEXT,'template_source':TEXT})
POINT=obj({'time_s':number('s',0),'speed_rad_s':number('rad/s',-WMAX,WMAX),'phase':PHASE,'mode':TEXT,'active_s':number('s',0)})
CAPS={
 'plant.configure':(PLANT,obj({'configured':BOOL}),['housing','sunwheel'],'宿主生热、罐球热容和材料审查；不是厂家热模型','configuration','evaluator',PLANT_EXAMPLE),
 'method.configure':(METHOD,obj({'configured':BOOL}),['panel','sunwheel'],'SI转速、有效磨削时间、间歇、停转暂停与反转','configuration','agent',METHOD_EXAMPLE),
 'sample.configure':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['jar','jar_lid','clamp'],'500mL单罐/25颗20mm磨球与有限固体账本；不代替机器人装罐','configuration','evaluator',example_sample()),
 'mount.configure':(obj({'clamped':BOOL,'balanced':BOOL}),obj({'clamped':BOOL,'balanced':BOOL}),['clamp','safety_slider','counterweight'],'宿主确认夹紧与配重，未验证夹紧力或真实失衡阈值','configuration','evaluator',dict(clamped=True,balanced=True)),
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['main_switch'],'供电代理，断电取消但仍需减速至停转才开盖','device_command','agent',dict(enabled=True)),
 'lid.set':(obj({'angle_rad':number('rad',0,1.55)}),obj({'angle_rad':number('rad',0,1.55)}),['lid'],'手动开盖目标，宿主推进；非机器人接触','device_command','agent',dict(angle_rad=1.55)),
 'milling.set_speed':(obj({'speed_rad_s':number('rad/s',WMIN,WMAX)}),obj({'speed_rad_s':number('rad/s',WMIN,WMAX)}),['knob_menu','sunwheel'],'停机时改转速目标，不瞬时产生转动','device_command','agent',dict(speed_rad_s=200*math.tau/60)),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['button_start','sunwheel'],'异步磨削；时间结束不保证目标粒径','device_command','agent',{}),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['button_stop','sunwheel'],'取消并保留产物；惯性减速教学代理仍由step推进','device_command','agent',{}),
 'milling.measure':(obj({}),MEASURE,['panel','display_digits'],'速度和作业记录；不公开选配罐温或真值成分','query','agent',{}),
 'product.read':(obj({}),PRODUCT,['jar'],'评价器样品守恒账本、合成粒径及罐温；非实验产物','query','evaluator',{}),
 'history.read':(obj({}),obj({'records':dict(type='array',items=POINT),'evidence':TEXT}),['panel'],'宿主运行记录，不是原厂导出文件','query','agent',{}),
 'reference.lookup':(obj({'substance_id':TEXT,'temperature_k':number('K',298.15,500)}),obj({'cp_j_mol_k':number('J/mol/K',0),'source':TEXT,'evidence':TEXT,'template_source':TEXT}),['jar'],'真实固体Cp表的相内插值，不作动力学外推','query','agent',dict(substance_id='al-096',temperature_k=350.))}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,zh,o='derived',v='agent',c='panel',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=zh
for k in ['plant','method','sample']:slot(k+'.configured',BOOL,'1',k+'配置状态')
slot('power.enabled',BOOL,'1','供电代理','setpoint',c='main_switch',w=('power.set','enabled'))
slot('mount.clamped',BOOL,'1','宿主夹紧确认','setpoint',c='clamp');slot('mount.balanced',BOOL,'1','宿主配重确认','setpoint',c='counterweight')
slot('cycle.phase',PHASE,'1','磨削作业状态');slot('cycle.mode',TEXT,'1','磨削/制动/暂停子阶段');slot('cycle.fault',TEXT,'1','故障或模型温区边界')
slot('cycle.active_s',number('s',0),'s','电机驱动区间累计时间，含教学加速段');slot('cycle.wall_s',number('s',0),'s','含暂停与制动的作业墙钟')
slot('milling.target_rad_s',number('rad/s',WMIN,WMAX),'rad/s','太阳轮速度目标','setpoint',c='sunwheel',w=('milling.set_speed','speed_rad_s'))
slot('milling.speed_rad_s',number('rad/s',-WMAX,WMAX),'rad/s','实际有符号教学转速',c='sunwheel')
slot('sun.angle_rad',number('rad'),'rad','太阳轮累计角度','scene',c='sunwheel');slot('jar.relative_angle_rad',number('rad'),'rad','罐相对父太阳轮角=-3q；绝对角=-2q','scene',c='jar_spindle')
slot('lid.angle_rad',number('rad',0,1.55),'rad','宿主推进后的盖角','scene',c='lid');slot('lid.target_rad',number('rad',0,1.55),'rad','盖角目标','setpoint',c='lid',w=('lid.set','angle_rad'))
slot('panel.knob_angle_rad',number('rad',-2.617994,2.617994),'rad','速度目标映射菜单旋钮，不是完整厂家UI','scene',c='knob_menu')
for k,u,zh in [('temperature_k','K','非内置读数的罐温代理'),('d50_m','m','合成粒径代理'),('mass_kg','kg','有限固体质量'),('sensible_energy_j','J','罐球样品显热'),('input_energy_j','J','仅摩擦显热输入'),('loss_energy_j','J','对环境显热损失')]:slot('truth.'+k,number(u,0),u,zh,'process_truth','evaluator',c='jar')

# ---------------------------------------------------------------- 1.1.0: functions of the display menu (operating manual chapter 6)
# Manual parameters changed at any time (also while grinding), programs 01-10 (grinding time and speed), Start in countdown, basic settings
# (automatic opening, language, brightness, date / time, acoustic signal, jar-clamping notice), operating hours, a new run after the end and
# the remaining time after a mains interruption.  The 1.0.0 commands in CAPS keep their meaning; the panel layer (panel_ui.json) drives both.
ASSET_VERSION='1.2.1';ASSET_DIR=ROOT/'assets/expansion_batch03/t1_thermal/retsch_pm100'
CLOCK_EPOCH='2026-01-01T00:00:00';SETTINGS_LOST_S=30*86400.;LANGUAGES=['English','Deutsch'];SOFTWARE={'display':'1.00','controller':'1.00'}
def _opt(props):return dict(type='object',properties=props,required=[],additionalProperties=False)
def _int(lo,hi):return dict(type='integer',minimum=lo,maximum=hi,**{'x-unit':'1'})
HMS=number('s',0,359999)
PARAMS_IN=_opt({'duration_s':number('s',1,359999),'speed_rad_s':number('rad/s',WMIN,WMAX),'interval_s':HMS,'pause_s':HMS,'reverse':BOOL})
SETTINGS_IN=_opt({'auto_open':BOOL,'language':dict(type='string',enum=LANGUAGES),'brightness_pct':number('1',10,100),'acoustic_signal':BOOL,'safety_notice':BOOL})
PROGRAM=obj({'slot':_int(1,10),'duration_s':number('s',1,359999),'speed_rad_s':number('rad/s',WMIN,WMAX)})
KEY_CAPS={
 'cycle.rearm':(obj({}),obj({'phase':PHASE}),['button_start','panel'],'结束、STOP或断电中止后重新开始一次研磨（时间从设定值重新计；断电后START继续剩余时间时不用）','device_command','agent',{}),
 'milling.set_parameters':(PARAMS_IN,obj({'configured':BOOL}),['knob_menu','display_digits'],'手动方式参数：研磨时间、转速、间歇、换向、暂停时间（任何时候可改，研磨中也可；无间歇不能设换向与暂停）','device_command','agent',dict(duration_s=300.,speed_rad_s=400*math.tau/60)),
 'program.select':(obj({'slot':_int(0,10)}),obj({'slot':_int(0,10)}),['knob_menu','display_digits'],'菜单导航C1：手动方式（0）或程序01–10（载入程序的研磨时间与转速；无间歇）；研磨中不能换','device_command','agent',dict(slot=1)),
 'program.save':(PROGRAM,obj({'slot':_int(1,10)}),['knob_menu','display_digits'],'Save parameters / Change program：把研磨时间与转速存入程序01–10','device_command','agent',dict(slot=1,duration_s=120.,speed_rad_s=400*math.tau/60)),
 'program.delete':(obj({'slot':_int(1,10)}),obj({'slot':_int(1,10)}),['knob_menu','display_digits'],'Delete program：删除程序存的参数（存储位仍在）','device_command','agent',dict(slot=1)),
 'acquisition.start_in':(obj({'delay_s':number('s',1,359999)}),obj({'start_at_s':number('s',0)}),['knob_menu','button_start'],'Start in：倒计时后自动开始研磨（STOP取消）','device_command','agent',dict(delay_s=60.)),
 'settings.configure':(SETTINGS_IN,obj({'configured':BOOL}),['knob_menu','display_digits'],'基本设置：自动开盖、语言、亮度、提示音、启动前夹紧安全提示','device_command','agent',dict(acoustic_signal=False)),
 'clock.set':(obj({'year':_int(2000,2099),'month':_int(1,12),'day':_int(1,31),'hour':_int(0,23),'minute':_int(0,59)}),obj({'clock':TEXT}),['knob_menu','display_digits'],'基本设置的日期与时间（断电超过30天丢失）','device_command','agent',dict(year=2026,month=10,day=18,hour=10,minute=30)),
}
KEY_ERRORS=['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT','INTERVAL_REQUIRED','PROGRAM_EMPTY']
# ---------------------------------------------------------------- 1.2.0 (sync fixes 2026-10-19)
# Fault messages of chapter 7 through an evaluator fault injection (the manual gives the code and its text only): the E codes and H10 stop the
# grinding (cycle.phase failed, cycle.fault = code), show the code with its text, lock the keys except the main switch and KH, and refuse every
# start until fault.clear (service repair; for H10 the drive has cooled down); H13 / H14 are overload hints during grinding (H14: the device has
# lowered the speed to the value the evaluator gives; H13 goes when the operator lowers the speed).  Caller rules: a grinding started with START,
# from the panel, or at the end of a Start-in countdown belongs to the caller who started it; only the owner may stop it, cancel its countdown,
# change its parameters or switch the mill off while it runs.
FAULT_TEXT={'E10':'DRIVE OVERLOAD','E20':'FAILURE MAIN BOARD','E23':'FAILURE FAN','E25':'FAILURE DISPLAY','E26':'FAILURE FREQUENCY CONVERTER','E41':'FAILURE SPEED SENSOR',
            'E47':'OUT OF BALANCE','E50':'FAILURE IN SAFETY CIRCUIT','H10':'ALLOW DRIVE TO COOL DOWN','H13':'OVERLOAD! REDUCE SPEED!','H14':'OVERLOAD! SPEED HAS BEEN REDUCED!'}
FAULT_CODES=list(FAULT_TEXT);FAULT_HINTS=('H13','H14');FAULT_STOPS=[c for c in FAULT_CODES if c not in FAULT_HINTS]
FAULT_CAPS={
 'fault.inject':(dict(type='object',properties={'code':dict(type='string',enum=FAULT_CODES),'speed_rad_s':number('rad/s',WMIN,WMAX)},required=['code'],additionalProperties=False),obj({'code':TEXT,'phase':PHASE}),['panel','display_digits'],'评测者注入手册第7章的故障信息：E代码与H10停止研磨（cycle.phase=failed）、显示代码与文字、锁定按键并拒绝启动直到fault.clear；H13/H14为研磨中的过载提示（H14须给出设备降到的转速speed_rad_s）','simulation','evaluator',dict(code='E47')),
 'fault.clear':(obj({}),obj({'code':dict(anyOf=[TEXT,dict(type='null')])}),['panel','display_digits'],'评测者：故障已排除（经销商/服务维修；H10为驱动已冷却），显示消失、可再启动','simulation','evaluator',{}),
}
FAULT_ERRORS=['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','REQUEST_CONFLICT']
FAULT_LOCKED={'acquisition.start','milling.set_speed'}|set(KEY_CAPS)
RUN_GUARDED={'acquisition.stop','milling.set_parameters'}
slot('program.selected',_int(0,10),'1','菜单导航：0=手动方式，1–10=程序','setpoint',c='display_digits')
slot('program.store',dict(type='array',items=PROGRAM,minItems=0,maxItems=10),'1','已存程序（研磨时间、转速）','setpoint',c='display_digits')
slot('cycle.start_in_s',dict(anyOf=[number('s',0),dict(type='null')]),'s','Start in倒计时剩余（无为空）',c='display_digits')
slot('cycle.interrupted',BOOL,'1','研磨中断电：保存剩余时间（开机显示H45，开盖检查后START继续）',c='display_digits')
slot('lid.inspected',BOOL,'1','断电后已开盖检查研磨腔（再关盖）',c='lid')
slot('settings.current',obj({'auto_open':BOOL,'language':TEXT,'brightness_pct':number('1',10,100),'acoustic_signal':BOOL,'safety_notice':BOOL}),'1','基本设置','setpoint',c='display_digits')
slot('panel.operating_hours_h',number('h',0),'h','累计研磨小时（START到STOP之间）',c='display_digits')
slot('clock.datetime',TEXT,'1','仪器日期时间（ISO）',c='display_digits')
slot('fault.code',dict(anyOf=[dict(type='string',enum=FAULT_CODES),dict(type='null')]),'1','显示的故障信息代码（手册第7章E10–E50、H10、H13、H14；H13/H14只在研磨中）；无为null',c='display_digits')
def _panel_engine():
    try:
        from . import _panel_ui as engine                                   # imported as asset_runtime.retsch_pm100 (tests, Matterix bridge)
        return engine
    except ImportError:                                                      # loaded from its file path
        import sys
        if '_chemsim_panel_ui' not in sys.modules:
            spec=importlib.util.spec_from_file_location('_chemsim_panel_ui',Path(__file__).with_name('_panel_ui.py'))
            sys.modules['_chemsim_panel_ui']=importlib.util.module_from_spec(spec);spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
        return sys.modules['_chemsim_panel_ui']
_panel_ui=_panel_engine()
PANEL_SPEC=_panel_ui.load_spec(ASSET_DIR/'panel_ui.json',capabilities=set(CAPS)|set(KEY_CAPS))
PANEL_CAPS={k:(v['input_schema'],v['output_schema'],v['component_ids'],v['function_zh'],v['kind'],v['visibility'],v['example_arguments'])
            for k,v in _panel_ui.capability_definitions(PANEL_SPEC).items()}      # panel.press / turn / switch / capture / frame (CAPS stay the 1.0.0 set)
ALL_CAPS=dict(CAPS,**KEY_CAPS,**FAULT_CAPS,**PANEL_CAPS)

class RetschPm100Runtime:
 def __init__(self,instance_id='pm100_01'):
  self.instance_id=instance_id;self.plant=self.method=self.sample=None;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.powered=self.clamped=self.balanced=False;self.phase='idle';self.mode='idle';self.fault='';self.time_s=self.active_s=self.wall_s=self.segment_s=self.pause_elapsed=0.;self.w=self.q=self.lid=self.lid_target=0.;self.direction=1;self.temperature=298.15;self.d50=self.mass=self.moles=self.u=self.input_j=self.loss_j=0.;self.history=[];self.revision=0;self.operation_id='';self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._menu_init();self.panel=_panel_ui.PanelUI(PANEL_SPEC,self)
 def register_principal(self,principal_id,visibility):
  if visibility not in ['agent','debug','evaluator'] or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return RetschPm100Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _reference(self,sid,t):
  if sid not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
  r=self.templates[sid];lo,hi=r['conditions']['temperature_range_k']
  if not lo<=t<=hi:raise ActionRejected('REFERENCE_RANGE')
  vs=r['values'];ts=[v['temperature_k'] for v in vs];i=min(len(vs)-2,max(0,bisect.bisect_right(ts,t)-1));a,b=vs[i:i+2];return a['cp_j_mol_k']+(b['cp_j_mol_k']-a['cp_j_mol_k'])*(t-ts[i])/(ts[i+1]-ts[i])
 def _thermal_table(self):
  ta=self.plant['ambient_k'];self._ts=[ta]+[r['temperature_k'] for r in self.templates[self.sample['substance_id']]['values'] if r['temperature_k']>ta];self._cs=[self.plant['jar_balls_heat_capacity_j_k']+self.moles*self._reference(self.sample['substance_id'],t) for t in self._ts];self._us=[0.]
  for i in range(len(self._ts)-1):self._us.append(self._us[-1]+.5*(self._cs[i]+self._cs[i+1])*(self._ts[i+1]-self._ts[i]))
 def _temp_from_u(self,u):
  i=min(len(self._us)-2,max(0,bisect.bisect_right(self._us,u)-1));du=u-self._us[i];slope=(self._cs[i+1]-self._cs[i])/(self._ts[i+1]-self._ts[i]);return self._ts[i]+2*du/(self._cs[i]+math.sqrt(max(0,self._cs[i]**2+2*slope*du)))
 def _target(self):return self.method['speed_rad_s'] if self.method else WMIN
 def _values(self):return {**{n+'.configured':bool(getattr(self,n)) for n in ['plant','method','sample']},'power.enabled':self.powered,'mount.clamped':self.clamped,'mount.balanced':self.balanced,'cycle.phase':self.phase,'cycle.mode':self.mode,'cycle.fault':self.fault,'cycle.active_s':self.active_s,'cycle.wall_s':self.wall_s,'milling.target_rad_s':self._target(),'milling.speed_rad_s':self.w,'sun.angle_rad':self.q,'jar.relative_angle_rad':-3*self.q,'lid.angle_rad':self.lid,'lid.target_rad':self.lid_target,'panel.knob_angle_rad':-2.6179938779914944+5.235987755982989*(self._target()-WMIN)/(WMAX-WMIN),'truth.temperature_k':self.temperature,'truth.d50_m':self.d50,'truth.mass_kg':self.mass,'truth.sensible_energy_j':self.u,'truth.input_energy_j':self.input_j,'truth.loss_energy_j':self.loss_j,**self._menu_values()}
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ['debug','evaluator']})
 def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in ('templates','panel')})
 def invoke(self,capability,**a):
  if capability not in ALL_CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(ALL_CAPS[capability][0],a)
  self._owner_guard(capability,a)
  if self.fault_code in FAULT_STOPS and self.powered and capability in FAULT_LOCKED:raise ActionRejected('PRECONDITION_FAILED')   # fault shown: keys locked, no start
  if (self.phase=='running' or abs(self.w)>1e-9) and capability in ['plant.configure','method.configure','sample.configure','mount.configure','milling.set_speed','lid.set']:raise ActionRejected('BUSY')
  if capability=='plant.configure':
   if self.plant or not a['dry_material_reviewed']:raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);self.temperature=a['ambient_k'];out=dict(configured=True)
  elif capability=='method.configure':
   if self.active_s or not self.plant:raise ActionRejected('PRECONDITION_FAILED')
   if a['interval_s']==0 and (a['pause_s'] or a['reverse']):raise ActionRejected('INTERVAL_REQUIRED')
   if 0<a['interval_s']<.1:raise ActionRejected('INVALID_ARGUMENT')
   self.method=copy.deepcopy(a);self.direction=a['direction'];out=dict(configured=True)
  elif capability=='sample.configure':
   if self.sample or not self.plant or self.lid<1.5 or self.clamped:raise ActionRejected('PRECONDITION_FAILED')
   self._reference(a['substance_id'],self.plant['ambient_k'])
   if not a['floor_d50_m']<=a['initial_d50_m']<=a['max_feed_m']:raise ActionRejected('INVALID_ARGUMENT')
   self.sample=copy.deepcopy(a);self.mass=a['ball_count']*4/3*math.pi*(a['ball_diameter_m']/2)**3*self.plant['ball_density_kg_m3']/a['ball_to_sample_ratio'];self.moles=self.mass/self.templates[a['substance_id']]['molar_mass_kg_mol'];self.d50=a['initial_d50_m'];self._thermal_table();out=dict(configured=True,attachment_event='host_ledger_initialization_not_robot_insertion')
  elif capability=='mount.configure':
   if self.lid<1.5 or not self.sample:raise ActionRejected('PRECONDITION_FAILED')
   self.clamped=a['clamped'];self.balanced=a['balanced'];out=a
  elif capability in KEY_CAPS:out=self._menu_fn(capability,a)
  elif capability in FAULT_CAPS:out=self._fault_fn(capability,a)
  elif capability in PANEL_CAPS:out=getattr(self,'_cmd_'+capability.replace('.','_'))(**a)
  elif capability=='power.set':
   if a['enabled'] and not self.powered:
    self.power_epoch+=1
    if self.off_t is not None and self.time_s-self.off_t>SETTINGS_LOST_S:self.clock_offset_s=-math.floor(self.time_s)   # date and time lost after 30 days
   if not a['enabled'] and self.powered:self.off_t=self.time_s;self.start_at=None
   if not a['enabled'] and self.phase=='running':self.interrupted=True;self.inspected=False               # remaining time kept (H45)
   self.powered=a['enabled']
   if not self.powered and self.phase=='running':self.phase='cancelled';self.mode='coasting'
   out=a
  elif capability=='lid.set':self.lid_target=a['angle_rad'];out=a
  elif capability=='milling.set_speed':
   if not self.method or self.active_s:raise ActionRejected('PRECONDITION_FAILED')
   self.method['speed_rad_s']=a['speed_rad_s'];out=a
  elif capability=='acquisition.start':
   if not all([self.plant,self.method,self.sample,self.powered,self.clamped,self.balanced]) or self.phase not in ['idle','cancelled'] or self.lid>1e-8 or self.lid_target>1e-8 or abs(self.w)>1e-8 or self.active_s>=self.method['duration_s']:raise ActionRejected('PRECONDITION_FAILED')
   self.phase='running';self.mode='grinding';self.fault='';self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);out=dict(phase=self.phase,operation_id=self.operation_id)
   if self._caller:self._owners[self.operation_id]=self._caller[0]      # the caller owns the grinding (also when started with the panel)
   if self.fault_code in FAULT_HINTS:self.fault_code=None
  elif capability=='acquisition.stop':
   self.start_at=None                                                     # STOP also cancels a Start in countdown
   if self.phase=='running':self.phase='cancelled';self.mode='coasting'
   out=dict(phase=self.phase)
  elif capability=='milling.measure':out=dict(phase=self.phase,mode=self.mode,fault=self.fault,speed_rad_s=self.w,active_s=self.active_s,wall_s=self.wall_s,remaining_s=max(0,self.method['duration_s']-self.active_s) if self.method else 0.,lid_angle_rad=self.lid,evidence='synthetic_host_motion_log_no_integral_jar_temperature_sensor',template_source=TEMPLATE_REL)
  elif capability=='product.read':
   if not self.sample:raise ActionRejected('PRECONDITION_FAILED')
   out=dict(sample_id=self.sample['sample_id'],substance_id=self.sample['substance_id'],mass_kg=self.mass,moles=self.moles,d50_m=self.d50,temperature_k=self.temperature,evidence='synthetic_PSD_and_thermal_history_real_JANAF_Cp_not_measured_grinding_product',template_source=TEMPLATE_REL)
  elif capability=='history.read':out=dict(records=copy.deepcopy(self.history),evidence='synthetic_run_log_no_factory_file_format')
  elif capability=='reference.lookup':out=dict(cp_j_mol_k=self._reference(a['substance_id'],a['temperature_k']),source=self.templates[a['substance_id']]['source'],evidence='derived_linear_interpolation_of_real_JANAF_Cp',template_source=TEMPLATE_REL)
  validate(ALL_CAPS[capability][1],out)
  if ALL_CAPS[capability][4]!='query':self.revision+=1
  return copy.deepcopy(out)
 # ---- 1.1.0 menu functions and the panel host protocol
 def _menu_init(self):
  self.programs={};self.program_slot=0;self.manual_params=None;self.start_at=None;self.interrupted=False;self.inspected=False;self.hours_s=0.;self.clock_offset_s=0.
  self.settings=dict(auto_open=True,language='English',brightness_pct=100.,acoustic_signal=True,safety_notice=True);self.language_confirmed=False
  self.off_t=None;self.power_epoch=0;self.panel_capture_dir=None
  self.fault_code=None;self.hint_speed=None;self.start_in_owner=None;self._caller=None
 def _clock(self):
  import datetime
  return (datetime.datetime.fromisoformat(CLOCK_EPOCH)+datetime.timedelta(seconds=int(math.floor(self.time_s+self.clock_offset_s)))).isoformat()
 def _menu_values(self):
  return {'program.selected':self.program_slot,'program.store':[dict(slot=k,**v) for k,v in sorted(self.programs.items())],
          'cycle.start_in_s':None if self.start_at is None else max(0.,self.start_at-self.time_s),'cycle.interrupted':self.interrupted,'lid.inspected':self.inspected,
          'settings.current':dict(self.settings),'panel.operating_hours_h':self.hours_s/3600.,'clock.datetime':self._clock(),
          'fault.code':self.fault_code if self.powered and (self.fault_code in FAULT_STOPS or self.phase=='running') else None}
 def _owner_guard(self,capability,a):
  c=self._caller
  if c is None or c[1]!='agent':return
  owner=self._owners.get(self.operation_id) if self.phase=='running' else self.start_in_owner if self.start_at is not None else None
  if owner is not None and owner!=c[0] and capability in RUN_GUARDED:raise ActionRejected('ACCESS_DENIED')   # the rear main switch (power.set) is a hard
                                                                       # switch: never refused, switching off acts as a power loss (1.2.1, lead rule 10-19)
 def _fault_fn(self,capability,a):
  if capability=='fault.clear':
   code=self.fault_code;self.fault_code=self.hint_speed=None;return dict(code=code)
  code=a['code']
  if code in FAULT_HINTS:
   if self.phase!='running' or not self.powered:raise ActionRejected('PRECONDITION_FAILED')       # overload hints come up while grinding
   if code=='H14':
    if 'speed_rad_s' not in a:raise ActionRejected('INVALID_ARGUMENT')                        # how far the device lowers the speed is not documented
    self.method['speed_rad_s']=min(self.method['speed_rad_s'],float(a['speed_rad_s']))
   self.hint_speed=self.method['speed_rad_s']
  else:
   self.start_at=None
   if self.phase=='running':self.phase='failed';self.mode='coasting';self.fault=code       # the drive stops and runs down
  self.fault_code=code;return dict(code=code,phase=self.phase)
 def _start_ok(self):
  return all([self.plant,self.method,self.sample,self.powered,self.clamped,self.balanced]) and self.phase in ['idle','cancelled'] and self.lid<=1e-8 and self.lid_target<=1e-8 and abs(self.w)<=1e-8 and self.active_s<self.method['duration_s']
 def _menu_fn(self,capability,a):
  running=self.phase=='running'
  if capability=='cycle.rearm':
   if running or self.phase=='idle' or abs(self.w)>1e-9:raise ActionRejected('PRECONDITION_FAILED')
   self.phase='idle';self.mode='idle';self.active_s=self.wall_s=self.segment_s=self.pause_elapsed=0.;self.interrupted=self.inspected=False
   if self.method:self.direction=self.method['direction']
   return dict(phase=self.phase)
  if capability in ['milling.set_parameters','program.select','program.save'] and not (self.method and self.plant):raise ActionRejected('PRECONDITION_FAILED')
  if capability=='milling.set_parameters':
   new=dict(self.method,**{k:(v if isinstance(v,bool) else float(v)) for k,v in a.items()})
   if new['interval_s']==0 and (new['pause_s'] or new['reverse']):raise ActionRejected('INTERVAL_REQUIRED')
   if 0<new['interval_s']<1:raise ActionRejected('INVALID_ARGUMENT')
   if running and new['duration_s']<=self.active_s:raise ActionRejected('INVALID_ARGUMENT')
   if self.fault_code=='H13' and new['speed_rad_s']<self.hint_speed-1e-9:self.fault_code=self.hint_speed=None   # the operator reduced the speed
   self.method=new;return dict(configured=True)
  if capability=='program.select':
   if running or self.start_at is not None:raise ActionRejected('BUSY')
   s=a['slot']
   if s and not self.program_slot:self.manual_params=dict(self.method)              # manual parameters come back with the manual mode
   if s==0 and self.program_slot and self.manual_params:self.method=dict(self.manual_params)
   if s and s in self.programs:self.method.update(duration_s=self.programs[s]['duration_s'],speed_rad_s=self.programs[s]['speed_rad_s'],interval_s=0.,pause_s=0.,reverse=False)
   self.program_slot=s;return dict(slot=s)
  if capability=='program.save':
   self.programs[a['slot']]=dict(duration_s=float(a['duration_s']),speed_rad_s=float(a['speed_rad_s']))
   if a['slot']==self.program_slot and not running:self.method.update(duration_s=float(a['duration_s']),speed_rad_s=float(a['speed_rad_s']))
   return dict(slot=a['slot'])
  if capability=='program.delete':
   if a['slot'] not in self.programs:raise ActionRejected('PROGRAM_EMPTY')
   del self.programs[a['slot']];return dict(slot=a['slot'])
  if capability=='acquisition.start_in':
   if self.program_slot and self.program_slot not in self.programs:raise ActionRejected('PROGRAM_EMPTY')
   if self.start_at is not None or not self._start_ok():raise ActionRejected('PRECONDITION_FAILED')
   self.start_at=self.time_s+a['delay_s'];self.start_in_owner=self._caller[0] if self._caller else None;return dict(start_at_s=self.start_at)
  if capability=='settings.configure':self.settings.update(a);self.language_confirmed=self.language_confirmed or 'language' in a;return dict(configured=True)
  if capability=='clock.set':
   import datetime
   try:target=datetime.datetime(a['year'],a['month'],a['day'],a['hour'],a['minute'])
   except ValueError:raise ActionRejected('INVALID_ARGUMENT') from None
   self.clock_offset_s=(target-datetime.datetime.fromisoformat(CLOCK_EPOCH)).total_seconds()-math.floor(self.time_s);return dict(clock=self._clock())
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def panel_read(self):
  out=self.read('agent');m=self.method
  out.update({'host.powered':self.powered,'host.power_epoch':self.power_epoch,'host.language_confirmed':self.language_confirmed,
              'host.remaining_s':max(0.,m['duration_s']-self.active_s) if m else 0.,'host.duration_s':m['duration_s'] if m else 0.,
              'host.speed_rpm':(m['speed_rad_s'] if m else WMIN)*60/math.tau,'host.interval_s':m['interval_s'] if m else 0.,'host.pause_s':m['pause_s'] if m else 0.,
              'host.reverse':bool(m and m['reverse']),'host.idle_finished':self.phase in ('succeeded','failed') or (self.phase=='cancelled' and not self.interrupted),
              'host.program_saved':self.program_slot==0 or self.program_slot in self.programs,'host.software':dict(SOFTWARE),
              'host.programs':{str(k):dict(v,speed_rpm=v['speed_rad_s']*60/math.tau) for k,v in self.programs.items()},
              'host.fault':bool(self.powered and self.fault_code in FAULT_STOPS),'host.fault_code':self.fault_code or '','host.fault_text':FAULT_TEXT.get(self.fault_code,''),
              'host.hint':bool(self.powered and self.fault_code in FAULT_HINTS and self.phase=='running'),'host.clock':self._clock(),**dict(zip(['host.year','host.month','host.day','host.hour','host.minute'],[int(x) for x in self._clock().replace('T','-').replace(':','-').split('-')[:5]])),'host.pause_left_s':max(0.,m['pause_s']-self.pause_elapsed) if m else 0.})
  return out
 def panel_invoke(self,capability_id,arguments):
  try:return self.invoke(capability_id,**arguments)
  except ActionRejected as exc:raise _panel_ui.PanelRejected(str(exc)) from None
 def panel_time(self):return self.time_s
 def _panel(self,gesture,*args):
  try:return dict(accepted=bool(gesture(*args)))
  except _panel_ui.PanelError:raise ActionRejected('INVALID_ARGUMENT') from None
 def _cmd_panel_press(self,input_id,duration_s=.1,held=None):return self._panel(self.panel.press,input_id,float(duration_s),tuple(held or ()))
 def _cmd_panel_turn(self,input_id,detents):return self._panel(self.panel.turn,input_id,int(detents))
 def _cmd_panel_switch(self,input_id,position,held=None):return self._panel(self.panel.switch,input_id,position,tuple(held or ()))
 def _cmd_panel_capture(self,display_id):
  import hashlib,tempfile
  data,w,h=self.panel.png_bytes(display_id);sha=hashlib.sha256(data).hexdigest()
  d=Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir())/'chemistry_sim_panel'/self.instance_id
  d.mkdir(parents=True,exist_ok=True);path=d/f'{display_id}_{sha[:16]}.png'
  if not path.exists():path.write_bytes(data)
  return dict(display_id=display_id,time_s=self.time_s,image=dict(path=str(path),format='png',width=w,height=h,sha256=sha))
 def _cmd_panel_frame(self):return dict(frame=self.panel.frame())
 def step(self,dt_s):
  validate(number('s',0,360000),dt_s);remaining=float(dt_s)
  while remaining>1e-10:
   if self.start_at is not None and self.time_s>=self.start_at-1e-9:              # Start in: the countdown has run down
    self.start_at=None
    if self._start_ok() and self.fault_code not in FAULT_STOPS:
     self.phase='running';self.mode='grinding';self.fault='';self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count)
     if self.start_in_owner:self._owners[self.operation_id]=self.start_in_owner   # the countdown's caller owns the grinding
     if self.fault_code in FAULT_HINTS:self.fault_code=None
   h=min(.02,remaining);running=self.phase=='running';grind=running and self.mode=='grinding';p=self.plant
   if grind:
    left=min(self.method['duration_s']-self.active_s,self.method['interval_s']-self.segment_s if self.method['interval_s'] else math.inf)
    if left<=1e-9:self.mode='braking_end' if self.active_s>=self.method['duration_s']-1e-9 else 'braking_pause';continue
    h=min(h,left)
   target=self.direction*self._target() if grind and self.powered else 0.;oldw=self.w;acc=p['acceleration_rad_s2'] if p else 40.;self.w+=max(-acc*h,min(acc*h,target-self.w));self.q+=(oldw+self.w)*h/2
   # Signed motion and heat are independent of query/API traffic.
   if self.sample:
    heat=750*p['heat_fraction']*((abs(oldw)+abs(self.w))/(2*WMAX))**2 if grind else 0.;ein=heat*h;loss=min(self.u,max(0,p['loss_w_k']*(self.temperature-p['ambient_k'])*h));next_u=self.u+ein-loss
    if next_u>self._us[-1]:
     # Stop at data-domain boundary, explicitly incomplete beyond this point.
     fraction=max(0,(self._us[-1]-self.u)/(ein-loss));ein*=fraction;loss*=fraction;next_u=self._us[-1];self.phase='failed';self.mode='coasting';self.fault='THERMAL_MODEL_RANGE'
    self.u=next_u;self.input_j+=ein;self.loss_j+=loss;self.temperature=self._temp_from_u(self.u)
    if grind:
     rate=self.sample['breakage_rate_s']*((abs(oldw)+abs(self.w))/(2*WMAX))**2*(self.sample['ball_to_sample_ratio']/10);floor=self.sample['floor_d50_m'];self.d50=floor+(self.d50-floor)*math.exp(-rate*h)
   if running:self.wall_s+=h;self.hours_s+=h
   if self.interrupted and self.lid>=1.5:self.inspected=True
   if grind:self.active_s+=h;self.segment_s+=h
   if self.phase=='running' and abs(self.w)<1e-10:
    if self.mode=='braking_end':
     self.phase='succeeded';self.mode='idle'
     if self.settings['auto_open']:self.lid_target=1.55                    # automatic opening at the end of grinding
    elif self.mode=='braking_pause':self.mode='pause';self.pause_elapsed=0.
    elif self.mode=='pause':
     self.pause_elapsed+=h
     if self.pause_elapsed>=self.method['pause_s']-1e-9:
      if self.method['reverse']:self.direction*=-1
      self.segment_s=0.;self.mode='grinding'
   if self.phase!='running' and abs(self.w)<1e-10:self.mode='idle'
   self.lid+=max(-.4*h,min(.4*h,self.lid_target-self.lid));self.time_s+=h;remaining-=h;self.revision+=1
   if not self.history or self.time_s-self.history[-1]['time_s']>=.2-1e-9:self.history.append(dict(time_s=self.time_s,speed_rad_s=self.w,phase=self.phase,mode=self.mode,active_s=self.active_s))
  return self.read()

class RetschPm100Session:
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
        meta=export_retsch_pm100_contract_metadata()
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
                result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))

    def _invoke(self,*,instance_id,capability_id,arguments,request_id):
        self._target(instance_id); h=self._host
        if not isinstance(capability_id,str) or capability_id not in ALL_CAPS: raise ActionRejected('UNKNOWN_CAPABILITY')
        if self._role=='agent' and ALL_CAPS[capability_id][5]!='agent': raise ActionRejected('ACCESS_DENIED')
        if not isinstance(request_id,str) or not request_id: raise ActionRejected('INVALID_ARGUMENT')
        validate(ALL_CAPS[capability_id][0],arguments)
        key=(self._principal,instance_id,request_id); digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
        if key in h._requests:
            old,result=h._requests[key]
            if old!=digest: raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(result)
        before=h.operation_id; caller=h._caller; h._caller=(self._principal,self._role)   # panel gestures and direct calls act as this caller
        try: result=h.invoke(capability_id,**arguments)
        finally: h._caller=caller
        op=result.get('operation_id')
        if op: h._owners[op]=self._principal
        elif capability_id in PANEL_CAPS and h.operation_id and h.operation_id!=before: h._owners[h.operation_id]=self._principal   # a run started by a panel key belongs to the caller
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('milling.measure'))

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])

def export_retsch_pm100_contract_metadata():
    caps={};slots={}
    for k,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'RetschPm100Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    immediate={'plant.configure':['plant.configured','truth.temperature_k'],'method.configure':['method.configured','milling.target_rad_s','panel.knob_angle_rad'],'sample.configure':['sample.configured','truth.mass_kg','truth.d50_m'],'mount.configure':['mount.clamped','mount.balanced'],'power.set':['power.enabled','cycle.phase'],'lid.set':['lid.target_rad'],'milling.set_speed':['milling.target_rad_s','panel.knob_angle_rad'],'acquisition.start':['cycle.phase','cycle.mode','cycle.fault'],'acquisition.stop':['cycle.phase','cycle.mode']}
    immediate.update({'cycle.rearm':['cycle.phase','cycle.mode','cycle.interrupted','lid.inspected'],'milling.set_parameters':['milling.target_rad_s','panel.knob_angle_rad'],
                      'program.select':['program.selected','milling.target_rad_s','panel.knob_angle_rad'],'program.save':['program.store','milling.target_rad_s','panel.knob_angle_rad'],
                      'program.delete':['program.store'],'acquisition.start_in':['cycle.start_in_s'],'settings.configure':['settings.current'],'clock.set':['clock.datetime']})
    immediate['power.set']=immediate['power.set']+['cycle.interrupted','lid.inspected','cycle.start_in_s','clock.datetime']
    immediate['acquisition.stop']=immediate['acquisition.stop']+['cycle.start_in_s']
    immediate['power.set']=immediate['power.set']+['fault.code']
    immediate['milling.set_parameters']=immediate['milling.set_parameters']+['fault.code']
    immediate.update({'fault.inject':['fault.code','cycle.phase','cycle.mode','cycle.fault','cycle.start_in_s','milling.target_rad_s','panel.knob_angle_rad'],'fault.clear':['fault.code']})
    _fx=list(dict.fromkeys(x for c in _panel_ui.spec_capabilities(PANEL_SPEC) for x in immediate.get(c,[])))
    immediate.update({k:list(_fx) for k in PANEL_CAPS if k not in ('panel.capture','panel.frame')})
    advancing=['cycle.phase','cycle.mode','cycle.fault','cycle.active_s','cycle.wall_s','milling.speed_rad_s','sun.angle_rad','jar.relative_angle_rad','truth.temperature_k','truth.d50_m','truth.sensible_energy_j','truth.input_energy_j','truth.loss_energy_j']
    for k,(inp,out,components,meaning,kind,vis,example) in ALL_CAPS.items():
        async_=k=='acquisition.start'
        caps[k]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.retsch_pm100.RetschPm100Session.invoke',invocation=f'session.invoke(instance_id="pm100_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if kind=='query' else [dict(description_zh='干磨材料审查、500mL单罐/25颗20mm球、75–220mL样品、停转盖锁、夹紧与配重确认；拒绝无副作用',state_ids=['cycle.phase','sample.configured','power.enabled','lid.angle_rad','mount.clamped','mount.balanced'],enforced_by='runtime',check='RetschPm100Runtime.invoke capability-specific guards')],state_effects=[dict(state_id=i,timing='immediate',description_zh='配置/操作状态，不推进物理时间') for i in immediate.get(k,[])]+([dict(state_id=i,timing='on_step',description_zh='步进运动、间歇反转、显热与合成粒径') for i in advancing] if async_ else [])+([dict(state_id='lid.angle_rad',timing='on_step',description_zh='0.4rad/s手动开盖教学代理')] if k=='lid.set' else []),observes=[],model_effects=dict(mode='none',description_zh='只有经实际求值验证的绑定才升格；LCD与菜单是简化教学映射',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='驱动时间完成且减速到零；不保证粒径' if async_ else '同步返回目标，真实状态仅step推进',completion_state_ids=['cycle.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['one_500mL_jar','25_20mm_visual_balls','finite_sample_ledger','principal_request_cache'],partial_failure_zh='取消保留产物，继续减速与显热冷却；数据温区边界后预测不完整。'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in (KEY_ERRORS if k in KEY_CAPS else FAULT_ERRORS if k in FAULT_CAPS else ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','REQUEST_CONFLICT'] if k in PANEL_CAPS else ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT','UNKNOWN_REFERENCE','REFERENCE_RANGE','INTERVAL_REQUIRED'])],example_arguments=copy.deepcopy(example),verification=[],limitations=(['面板层按panel_ui.json（手册页码证据）解释旋钮与按键手势并调用本运行层的同名能力；手势不推进仿真时钟（长按只按duration_s判断）。'] if k in PANEL_CAPS else ['评测者故障注入（1.2.0）：手册第7章只给代码与文字；停机、按键锁定（主开关与KH除外）、H13在降速后消失、H14降到评测者给定转速、fault.clear为服务维修或H10冷却完毕均为假设；故障背后的物理过程不建模。'] if k in FAULT_CAPS else ['显示菜单功能的最小模型（1.1.0，见运行层文档与panel_ui.json的assumptions）；程序与设置由仪器保存。'] if k in KEY_CAPS else ['不是原厂固件、DEM、机器人、硬件或安全认证；公开会话不替代文件/进程隔离。']),source_ids=['manufacturer_primary','manufacturer_manual','simulation_assumption'])
        if async_:caps[k]['errors'].append(dict(code='THERMAL_MODEL_RANGE',meaning_zh='达到固体Cp温区边界终止预测',side_effects='partial'))
    mechanism=dict(component_ids=['sunwheel','jar_spindle','jar','lid','clamp','counterweight','panel'],state_ids=list(slots),meaning_zh='单罐行星运动与间歇反转、真实Cp显热、宿主合成細化模型',implementation='lumped',equations=['omega target=[100,650]*2pi/60rad/s; finite host acceleration; q_sun integral omega dt','Assumed absolute jar omega=-2sun; parent-relative q_jar=-3q_sun; orbital radius=.141/2m','Pheat=750W*host_fraction*(abs(omega)/omega_max)^2 during drive;1250VA not jar heat','U=Cjar_balls_host*(T-Ta)+n*integral(Cp_JANAF(T)dT); linear Cp integrated exactly','DeltaU=Pheat*dt-UA_host*(T-Ta)*dt; loss bounded by available energy;dt<=.02s','d50=floor_host+(d50_initial-floor_host)*exp(-integral(k_host*(omega/omega_max)^2*(ball_sample_ratio/10)dt))','sample_mass=25*(4pi/3)*(.02/2)^3*rho_balls_host/ball_sample_ratio; moles=mass/reference_molar_mass; no material loss or reaction','Intervals count driven time including acceleration; brake to zero before pause/reversal; completion waits for zero speed','Only host step advances motion/heat/PSD; target updates do not teleport'],parameters={},source_ids=['manufacturer_primary','manufacturer_manual','nist_janaf','simulation_assumption'],limitations=['Only single500mL jar,25x20mmballs,75–220mL dry sample; other jars,stacking,wet milling,aeration,GrindControl not implemented.','73 independent formula Cp references, not measured milling/PSD/temperature experiments; phase names preserved, no material compatibility recommendation.','No DEM/contact force,abrasion/reaction/pressure/latent heat;heat fraction,UA,Cjar,acceleration and breakage are host assumptions.','Sensible energy excludes mechanical kinetic energy and drive losses; no whole-machine energy closure claim.','Typical1µm/colloidal0.1µm are sample-dependent, not guaranteed or a measured floor.','Counterweight and clamp are host confirmations, not calibrated sensors or robot installation.','298.15–500K data range is not a safe milling range; at boundary stop prediction, unmodelled subsequent heating remains a limitation.','Lid range/velocity and1:-2 absolute gear interpretation need calibration.','No invented integral jar-temperature sensor; 1.1.0 display menu (manual mode, programs 01-10, Start in, basic settings, service info) as minimal models in panel_ui.json; 1.2.0: fault messages E10-E50 / H10 / H13 / H14 only by evaluator fault injection (code and text from manual chapter 7, behaviour assumed; no drive, electronics, balance or overload physics).','Whole-unit nominal86kg conflicts with voltage-specific82/88kg; component masses unknown.'])
    spec=importlib.util.spec_from_file_location('_pm100_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'milling':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'milling':mechanism})

"""Regular two-channel flow: conservative tracer transport, explicit dead volume and bounded vapor data.

Module front ends (panel.turn / panel.press / panel.switch / panel.capture / panel.frame; panel_ui.json beside the contract, run by the shared
engine _panel_ui.py): the syringe pump's character display with its twist-and-click knob and Cancel button, the pressure controller's and the chip
climate controller's push / turn knobs with their LED displays, the reagent injector's knob with its F / I displays, and the rear power switch.  They
drive the commands below: pump.set_flow / pump.action / pump.configure / pump.set_clock / pump.usb (the pump firmware), pressure.set /
pressure.enable / pressure.tare, temperature.set / temperature.enable and injector.set_valve.  A flow set on the pump starts a manual run (no
method: the outlet goes to waste, the injector valves stay where the knob put them) beside the method runs of acquisition.start.

Pressure sensors (pump channels P1 / P2 and the pressure controller): reading = true gauge pressure + sensor offset - stored zero.  The offset is an
evaluator-only error (sensor.configure_error, default 0 = ideal); ZERO PRESSURE (pump p.27) stores the channel's current raw reading as its zero, the
tare at switch-on (pc p.16) stores the controller sensor's raw reading at ambient.  The pump displays, the OVER P / MAX PRESSURE checks and the
results use the readings; the controller holds its own reading at the set point, so the true back pressure is set point - (offset - zero)."""
from __future__ import annotations
import copy,datetime,json,math,weakref
from asset_runtime import _annotations as _ann
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT,PHASE
from asset_runtime.antonpaar_abbematadvanced import validate
ROOT=Path(__file__).resolve().parents[2];ASSET_TYPE_ID='instrument.syrris_asiadiscovery';CATALOG_ID='CHEM-019';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/syrris_asiadiscovery_flow_library/templates.json';ULMIN=1e-9/60
SYRINGES={'yellow':(1,250),'green':(5,1250),'blue':(10,2500),'red':(50,10000)}
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
def arr(s,n=None):
 a={'type':'array','items':s}
 if n is not None:a.update(minItems=n,maxItems=n)
 return a
FLOW=number('m^3/s',ULMIN,10000*ULMIN);TEMP=number('K',258.15,423.15);PRESS=number('Pa',1e5,2e6);LIFT=number('m',0,.04);INDEX={'type':'integer','minimum':0,'maximum':7,'x-unit':'1'}
PLANT=obj({'substance_id':TEXT,'fit_id':TEXT,'chip_volume_m3':{'type':'number','enum':[250e-9,1e-6],'x-unit':'m^3'},'density_kg_m3':number('kg/m^3',1,5000),'initial_temperature_k':TEMP,'pure_liquid_confirmed':BOOL,'pre_equilibrated':BOOL,'low_temperature_available':BOOL,'reservoir_volumes_m3':arr(number('m^3',1e-7,250e-6),2),'loop_volumes_m3':arr({'type':'number','enum':[.1e-6,1e-6,5e-6,10e-6],'x-unit':'m^3'},2),'loop_concentrations_mol_m3':arr(number('mol/m^3',0,100),2),'dead_volumes_m3':arr(number('m^3',1e-9,10e-6),3),'branch_resistance_pa_s_m3':arr(number('Pa*s/m^3',0,1e15),2),'common_resistance_pa_s_m3':number('Pa*s/m^3',0,1e15),'thermal_tau_s':number('s',.01,10000),'gas_supply_pa':number('Pa',2e5,2.5e6),'syringes':arr({'type':'string','enum':list(SYRINGES)},2),'container_ids':arr(TEXT,2),'chip_id':TEXT})
EXAMPLE=dict(substance_id='methanol',fit_id='1',chip_volume_m3=250e-9,density_kg_m3=790.,initial_temperature_k=298.15,pure_liquid_confirmed=True,pre_equilibrated=True,low_temperature_available=False,reservoir_volumes_m3=[20e-6]*2,loop_volumes_m3=[1e-6]*2,loop_concentrations_mol_m3=[1.,2.],dead_volumes_m3=[1e-8]*3,branch_resistance_pa_s_m3=[1e11]*2,common_resistance_pa_s_m3=1e11,thermal_tau_s=10.,gas_supply_pa=2.5e6,syringes=['red']*2,container_ids=['feed_A','feed_B'],chip_id='glass250')
WINDOW=obj({'start_s':number('s',0,86400),'end_s':number('s',0,86400),'slot':INDEX})
METHOD=obj({'flow_rates_m3_s':arr(FLOW,2),'duration_s':number('s',.01,86400),'injection_times_s':arr(number('s',0,86400),2),'collection_windows':arr(WINDOW)})
METHOD_EXAMPLE=dict(flow_rates_m3_s=[5000*ULMIN]*2,duration_s=4.,injection_times_s=[0.,.4],collection_windows=[dict(start_s=.5,end_s=3.,slot=0)])
POINT=obj({'time_s':number('s',0),'elapsed_s':number('s',0),'temperature_k':nullable(TEMP),'pressure_pa':arr(number('Pa',0),2),'collected_m3':number('m^3',0),'phase':PHASE})
RESULT=obj({'phase':PHASE,'fault':TEXT,'elapsed_s':number('s',0),'temperature_k':nullable(TEMP),'pump_pressures_pa':arr(number('Pa',0),2),'vapor_pressure_pa':nullable(number('Pa',0)),'vapor_margin_pa':nullable(number('Pa')),'collected_volume_m3':number('m^3',0),'waste_volume_m3':number('m^3',0),'conversion_fraction':{'type':'null'},'yield_fraction':{'type':'null'},'template_source':TEXT,'evidence':TEXT})
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet'],'供电；断电停止输送，保留残液','device_command','agent',{'enabled':True},['power.enabled','cycle.phase']),
 'clamp.set_lift':(obj({'lift_m':LIFT}),obj({'lift_m':LIFT}),['chip_clamp'],'芯片夹具教学升降；输送中禁止打开','device_command','agent',{'lift_m':.04},['clamp.lift_m']),
 'plant.configure':(PLANT,obj({'configured':BOOL,'attachment_events':arr(TEXT)}),['chip','input_store','injector'],'宿主一次性装配/预平衡/预充；同载液双被动示踪，显式参数','configuration','evaluator',EXAMPLE,['plant.configured','reactor.volume_m3','truth.initial_volume_m3','truth.initial_tracer_mol']),
 'method.configure':(METHOD,obj({'configured':BOOL}),['pump','injector','collector'],'两路流率、注入顺序与无重叠收集窗口；保留前次残液','configuration','agent',METHOD_EXAMPLE,['flow.targets_m3_s','method.duration_s']),
 'temperature.set':(obj({'target_k':TEMP}),obj({'target_k':TEMP}),['climate'],'Regular−15–150C；模板有效温段内且低温能力已确认','device_command','agent',{'target_k':303.15},['temperature.target_k']),
 'pressure.set':(obj({'backpressure_pa':PRESS}),obj({'backpressure_pa':PRESS}),['pressure','knob_backpressure'],'BPR1–20bar，表压；受供气约束；上游压降另计','device_command','agent',{'backpressure_pa':2e5},['pressure.backpressure_pa']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['pump','injector','collector'],'供电夹紧、配置完整且压力/汽化检查通过后开始连续输送','device_command','agent',{},['cycle.phase','cycle.elapsed_s','cycle.fault','flow.actual_m3_s']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['pump','injector'],'取消后保留所有流路/环内残液；采样钟仅step推进','device_command','agent',{},['cycle.phase','flow.actual_m3_s']),
 'flow.measure':(obj({}),RESULT,['display_digits'],'读取两路压力、芯片温度代理和收集体积，不报告产率','query','agent',{},[]),
 'history.read':(obj({}),obj({'records':arr(POINT),'truncated':BOOL,'template_source':TEXT,'evidence':TEXT}),['display_digits'],'最多1000条1s采样记录；不是实测Asia曲线','query','agent',{},[]),
 'product.read':(obj({}),obj({'volumes_m3':arr(number('m^3',0),8),'tracer_mol':arr(arr(number('mol',0),2),8),'reaction_products':{'type':'null'},'evidence':TEXT}),['collector'],'宿主读取收集的被动示踪物库存；化学产物未知','query','evaluator',{},[]),
 'collector.unload':(obj({'slot':INDEX,'container_id':TEXT}),obj({'unloaded':BOOL,'attachment_event':TEXT}),['collector'],'停泵移走收集瓶及库存；仅宿主事件，不创建约束','configuration','evaluator',{'slot':0,'container_id':'fraction_0'},['truth.removed_volume_m3','truth.removed_tracer_mol']),
 'residence.measure':(obj({}),obj({'mean_s':number('s',0),'variance_s2':number('s^2',0),'chip_mean_s':number('s',0),'evidence':TEXT}),['chip'],'理想线性串联槽网络RTD解析矩；包含进出口死体积','query','agent',{},[]),
}
SLOTS={}
def slot(k,s,u,o='derived',v='agent',c='display_digits',w=None):SLOTS[k]=(s,u,o,v,[c],w)
slot('power.enabled',BOOL,'1','setpoint',c='power_inlet',w=('power.set','enabled'));slot('clamp.lift_m',LIFT,'m','scene',c='chip_clamp',w=('clamp.set_lift','lift_m'))
slot('temperature.target_k',TEMP,'K','setpoint',c='climate',w=('temperature.set','target_k'));slot('pressure.backpressure_pa',PRESS,'Pa','setpoint',c='pressure',w=('pressure.set','backpressure_pa'))
for k,s,u in [('cycle.phase',PHASE,'1'),('cycle.elapsed_s',number('s',0),'s'),('cycle.fault',TEXT,'1'),('plant.configured',BOOL,'1'),('reactor.volume_m3',nullable(number('m^3',0)),'m^3'),('method.duration_s',number('s',0),'s'),('flow.targets_m3_s',arr(number('m^3/s',0),2),'m^3/s'),('flow.actual_m3_s',arr(number('m^3/s',0),2),'m^3/s'),('temperature.indicated_k',nullable(TEMP),'K'),('pressure.indicated_pa',arr(number('Pa',0),2),'Pa'),('pressure.vapor_pa',nullable(number('Pa',0)),'Pa'),('pressure.vapor_margin_pa',nullable(number('Pa')),'Pa'),('collection.total_m3',number('m^3',0),'m^3'),('collection.waste_m3',number('m^3',0),'m^3'),('collector.x_m',number('m',0,.30),'m'),('collector.y_m',number('m',0,.10),'m'),('injection.valves',arr(BOOL,2),'1')]:slot(k,s,u)
for k in ['initial','retained','collected','waste','removed']:slot('truth.'+k+'_volume_m3',number('m^3',0),'m^3','process_truth','evaluator','chip');slot('truth.'+k+'_mass_kg',number('kg',0),'kg','process_truth','evaluator','chip');slot('truth.'+k+'_tracer_mol',arr(number('mol',0),2),'mol','process_truth','evaluator','chip')
slot('truth.cell_tracer_mol',arr(arr(number('mol',0),2),7),'mol','process_truth','evaluator','chip');slot('truth.loop_remaining_m3',arr(number('m^3',0),2),'m^3','process_truth','evaluator','injector')
# ---- module front ends (Asia manuals, PDF pages: 'pump p.N' syringe pump v1.7, 'pc p.N' pressure controller v1.3, 'ccc p.N' chip climate
# controller v1.4, 'ri p.N' reagent injector v1.3)
ASSET_VERSION='1.2.1';ASSET_DIR=ROOT/'assets/expansion_batch03/t5_automation/syrris_asiadiscovery'
SYRINGE_UL={'yellow':(50,100),'green':(250,500),'blue':(500,1000),'red':(2500,5000)}   # syringe pair volumes (µl), pump p.26
AMBIENT_K=298.15;INIT_S=10.;LOG_MAX=200;CLOCK0=datetime.datetime(2026,1,1)   # assumptions: room temperature, INITIALISE duration, pump log length, pump clock at t=0
FIRMWARE=dict(top='0.9.31',channel_sw='1.60',channel_hw='2.00',serial='SIM-0001',pressure='01.01',injector='1.3')   # simulated unit (pump p.29-30 figures, pc p.27, ri p.22)
CH={'type':'string','enum':['P1','P2']};SYR={'type':'string','enum':list(SYRINGES)};POS={'type':'string','enum':['fill','inject']}
FLOW0=number('m^3/s',0,10000*ULMIN);MAXP=number('Pa',1e5,2e6);OVERP={'type':'string','enum':['single','both','all']}
DRIVE={'type':'string','enum':['idle','pumping','filling','emptying','initialising']}
def integer(lo,hi):return {'type':'integer','minimum':lo,'maximum':hi,'x-unit':'1'}
CHANNEL=obj({'syringe':SYR,'initialised':BOOL,'primed':BOOL,'drive':DRIVE,'flow_m3_s':FLOW0,'last_flow_m3_s':nullable(FLOW0),'fill_flow_m3_s':FLOW,'max_pressure_pa':MAXP,'over_pressure':OVERP,'alarm':TEXT,'zeroed_s':nullable(number('s',0))})
USB=obj({'inserted':BOOL,'files':arr(TEXT)});LOGREC=obj({'n':integer(1,10**9),'time_s':number('s',0),'clock':TEXT,'text':TEXT})
SETTINGS=obj({'channel':CH,'syringe':SYR,'fill_flow_m3_s':FLOW,'max_pressure_pa':MAXP,'over_pressure':OVERP,'initialised':BOOL})
PUMP_FX=['pump.flow_set_m3_s','pump.channels','pump.log','cycle.phase','cycle.manual','flow.actual_m3_s']
FRONT_CAPS={
 'pump.set_flow':(obj({'channel':CH,'flow_m3_s':FLOW0}),obj({'channel':CH,'flow_m3_s':FLOW0}),['pump','pump_red_disc'],'注射泵前面板设定通道流量（pump p.22-23）：在所设注射器量程内；0停该通道；空闲时开始面板手动输送（未充注先充注再泵送，出口去废液），运行中可改流量','device_command','agent',{'channel':'P1','flow_m3_s':100*ULMIN},PUMP_FX),
 'pump.action':(obj({'channel':CH,'action':{'type':'string','enum':['initialise','stop','fill','empty','zero_pressure']}}),obj({'channel':CH,'action':{'type':'string','enum':['initialise','stop','fill','empty','zero_pressure']}}),['pump','pump_red_disc','pump_red_dot'],'注射泵通道动作（pump p.24-28）：INITIALISE找原点、STOP、FILL/EMPTY按FILL@流量走完行程、ZERO PRESSURE压力传感器归零（须不在泵送）；STOP在PC方法运行中结束该运行','device_command','agent',{'channel':'P1','action':'stop'},PUMP_FX),
 'pump.configure':(obj({'channel':CH,'syringe':SYR,'fill_flow_m3_s':FLOW,'max_pressure_pa':MAXP,'over_pressure':OVERP},optional=('syringe','fill_flow_m3_s','max_pressure_pa','over_pressure')),SETTINGS,['pump'],'注射泵通道设置（pump p.24-27）：S. VOL注射器规格（改后须INITIALISE）、FILL@流量、MAX PRESSURE与OVER P（SINGLE/BOTH/ALL）','configuration','agent',{'channel':'P1','max_pressure_pa':1e6},['pump.channels','pump.log']),
 'pump.set_clock':(obj({'year':integer(2000,2099),'month':integer(1,12),'day':integer(1,31),'hour':integer(0,23),'minute':integer(0,59)}),obj({'clock':TEXT}),['pump'],'注射泵内部时钟（pump p.29，SET AND RESTART）：只用于日志时间戳；泵在动作时不重启','configuration','agent',{'year':2026,'month':10,'day':19,'hour':9,'minute':30},['pump.clock','pump.log']),
 'pump.usb':(obj({'action':{'type':'string','enum':['insert','copy_logs','remove']}}),USB,['pump'],'注射泵后部USB（pump p.31-32）：插入U盘、COPY DATA LOGS（泵空闲时）、SAFELY REMOVE后拔出','device_command','agent',{'action':'insert'},['pump.usb','pump.log']),
 'injector.set_valve':(obj({'valve':{'type':'string','enum':['left','right']},'position':POS}),obj({'valve':{'type':'string','enum':['left','right']},'position':POS}),['injector','injector_red_disc'],'试剂注入器单阀F（充样）/I（注入）位置（ri p.16-17）；面板手动运行中注入环随该通道流量进入芯片；PC方法运行中由方法控制','device_command','agent',{'valve':'left','position':'inject'},['injector.positions','injection.valves']),
 'pressure.enable':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['pressure','knob_backpressure'],'压力控制器OFF（pc p.20）：放空到室压（背压0）；pressure.set重新启用并设定','device_command','agent',{'enabled':False},['pressure.enabled','pressure.controller_pa']),
 'temperature.enable':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['climate','climate_red_disc'],'芯片控温OFF（ccc p.21）：被动回到室温；temperature.set重新启用并设定','device_command','agent',{'enabled':False},['temperature.enabled','temperature.indicated_k']),
 'pressure.tare':(obj({}),obj({'tared':BOOL}),['pressure','knob_backpressure'],'压力控制器内部传感器调零（pc p.16：开机时按住旋钮，此时尚未加压）：把传感器在室压下的原始读数存为零点；之后显示与控压都用扣零后的读数','device_command','agent',{},['pressure.tare_time_s','pressure.controller_pa','pressure.vapor_margin_pa','pressure.indicated_pa','truth.pressure_sensor_zero_pa','truth.backpressure_pa']),
}
CAPS.update(FRONT_CAPS)
SENSORS=['P1','P2','controller']
ERROR_CAPS={
 'sensor.configure_error':(obj({'sensor':{'type':'string','enum':SENSORS},'offset_pa':number('Pa',-2e5,2e5)}),obj({'sensor':{'type':'string','enum':SENSORS},'offset_pa':number('Pa',-2e5,2e5)}),['pump','pressure'],'评测者：泵通道或压力控制器压力传感器的零点偏置（原始读数 = 真实表压 + 偏置；默认0即理想传感器）；ZERO PRESSURE / tare存下的零点抵消它','simulation','evaluator',{'sensor':'P1','offset_pa':3e4},['truth.pressure_sensor_offset_pa','pressure.indicated_pa','pressure.controller_pa','pressure.vapor_margin_pa','truth.backpressure_pa']),
}
CAPS.update(ERROR_CAPS)
for k,s,u,o,c,w in [('cycle.manual',BOOL,'1','derived','pump',None),('pump.flow_set_m3_s',arr(FLOW0,2),'m^3/s','setpoint','pump',None),('pump.channels',arr(CHANNEL,2),'1','setpoint','pump',None),
                    ('pump.log',arr(LOGREC),'1','derived','pump',None),('pump.usb',USB,'1','derived','pump',None),('pump.clock',TEXT,'1','setpoint','pump',None),
                    ('pressure.enabled',BOOL,'1','setpoint','pressure',('pressure.enable','enabled')),('pressure.controller_pa',number('Pa',0),'Pa','derived','pressure',None),
                    ('pressure.tare_time_s',nullable(number('s',0)),'s','derived','pressure',None),('temperature.enabled',BOOL,'1','setpoint','climate',('temperature.enable','enabled')),
                    ('injector.positions',arr(POS,2),'1','setpoint','injector',None)]:slot(k,s,u,o,c=c,w=w)
slot('truth.pressure_sensor_offset_pa',arr(number('Pa'),3),'Pa','process_truth','evaluator','pressure');slot('truth.pressure_sensor_zero_pa',arr(number('Pa'),3),'Pa','process_truth','evaluator','pressure')
slot('truth.backpressure_pa',number('Pa',0),'Pa','process_truth','evaluator','pressure')
for k,extra in [('power.set',['injector.positions','pump.channels','pump.flow_set_m3_s','cycle.manual']),('pressure.set',['pressure.enabled','pressure.controller_pa','truth.backpressure_pa']),('temperature.set',['temperature.enabled']),
                ('plant.configure',['pump.channels']),('acquisition.start',['cycle.manual','injector.positions']),('acquisition.stop',['cycle.manual','pump.flow_set_m3_s','pump.channels']),('pump.action',['pressure.indicated_pa','truth.pressure_sensor_zero_pa'])]:
 CAPS[k]=CAPS[k][:7]+(CAPS[k][7]+extra,)
def _panel_engine():
 try:
  from asset_runtime import _panel_ui as engine
  return engine
 except ImportError:
  import importlib.util,sys
  if '_chemsim_panel_ui' not in sys.modules:
   spec=importlib.util.spec_from_file_location('_chemsim_panel_ui',Path(__file__).with_name('_panel_ui.py'));sys.modules['_chemsim_panel_ui']=importlib.util.module_from_spec(spec);spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
  return sys.modules['_chemsim_panel_ui']
_panel_ui=_panel_engine()
PANEL_SPEC=_panel_ui.load_spec(ASSET_DIR/'panel_ui.json',capabilities=set(CAPS)) if (ASSET_DIR/'panel_ui.json').exists() else None
PANEL_CAPS=_panel_ui.capability_definitions(PANEL_SPEC) if PANEL_SPEC else {}
_PANEL_FX=list(dict.fromkeys(x for c in (_panel_ui.spec_capabilities(PANEL_SPEC) if PANEL_SPEC else []) for x in CAPS[c][7]))
for _k,_v in PANEL_CAPS.items():CAPS[_k]=(_v['input_schema'],_v['output_schema'],_v['component_ids'],_v['function_zh'],_v['kind'],_v['visibility'],_v['example_arguments'],[] if _k in ('panel.capture','panel.frame') else _PANEL_FX)
_PANELS=weakref.WeakKeyDictionary()                                                    # one PanelUI per runtime, kept out of __dict__
MANUAL_OK=set(FRONT_CAPS)|{'pressure.set','temperature.set'}                         # commands the module fronts take during a manual (panel) run
class SyrrisAsiadiscoveryRuntime:
 def __init__(self,instance_id='asiadiscovery_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.lift=0.;self.plant=None;self.method=copy.deepcopy(METHOD_EXAMPLE);self.target_k=298.15;self.temp_k=298.15;self.bpr=2e5;self.phase='idle';self.elapsed_s=0.;self.fault='';self.operation_id=None;self._operation_count=0;self.history=[];self.history_truncated=False;self.next_record_s=0.;self.collector_xy=[0.,0.];self.valves=[False]*2;self.initial_volume=0.;self.initial_tracer=[0.,0.];self.reservoirs=[0.,0.];self.loops=[0.,0.];self.cells=[[0.,0.] for _ in range(7)];self.collected=[0.]*8;self.collected_tracer=[[0.,0.] for _ in range(8)];self.waste=0.;self.waste_tracer=[0.,0.];self.removed=0.;self.removed_tracer=[0.,0.];self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
  self.manual=False;self.pump_q=[0.,0.];self.ch=[self._channel('red') for _ in range(2)];self.drive_until=[None,None];self.drive_then=[None,None];self.p_enabled=True;self.t_enabled=True
  self.valve_pos=['fill','fill'];self.pump_log=[];self._log_n=0;self.usb=dict(inserted=False,files=[]);self.clock_offset_s=0.;self.tare_time_s=None;self.power_epoch=0;self.panel_capture_dir=None
  self.p_offset=[0.,0.,0.];self.p_zero=[0.,0.,0.];self._panel_session=None   # pressure sensors P1, P2, controller: evaluator offset and stored zero (Pa); gesturing session
 @property
 def panel(self):
  p=_PANELS.get(self)
  if p is None and PANEL_SPEC:p=_PANELS[self]=_panel_ui.PanelUI(PANEL_SPEC,self,hooks=PANEL_HOOKS)
  return p
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return SyrrisAsiadiscoverySession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def vapor(self,t):
  if not self.plant:return None
  f=self.fit;lo,hi=f['runtime_temperature_range_k']
  if not lo<=t<=hi:raise ActionRejected('TEMPLATE_RANGE_UNAVAILABLE')
  return 1e5*10**(f['A']-f['B_k']/(t+f['C_k']))
 def pressures(self):
  if not self.plant or self.phase!='running':return [0.,0.]
  q=self._flows();p=self.plant;return [self._p_line()+p['common_resistance_pa_s_m3']*sum(q)+p['branch_resistance_pa_s_m3'][i]*q[i] for i in range(2)]
 def _sensor(self,i,true_pa):return true_pa+self.p_offset[i]-self.p_zero[i]                  # i = 0 / 1 pump channel, 2 pressure controller
 def readings(self):
  """Pump channel pressures as the pump's own sensors read them (display, OVER P, results)."""
  t=self.pressures();return [self._sensor(i,t[i]) for i in range(2)]
 def _p_line(self):
  """True back pressure: the controller holds its own reading (offset, tared) at the set point; OFF vents to ambient."""
  b=self._bpr();return max(0.,b-(self.p_offset[2]-self.p_zero[2])) if b>0 else 0.
 def _guard(self,q=None,manual=False):
  p=self.plant;q=self._flows() if q is None else q;b=self._p_line();pr=[b+p['common_resistance_pa_s_m3']*sum(q)+p['branch_resistance_pa_s_m3'][i]*q[i] for i in range(2)]
  if not manual and any(pr[i]>2e6 or self._sensor(i,pr[i])>self.ch[i]['max_pressure_pa'] for i in range(2)):return 'overpressure'   # MAX PRESSURE acts on the pump's reading
  if b+101325<=max(self.vapor(self.temp_k),self.vapor(self._target())):return 'vapor_risk'
  return ''
 def _inventory(self):
  holdup=sum(self.volumes)+sum(self.plant['loop_volumes_m3']) if self.plant else 0.;retained=sum(self.reservoirs)+holdup
  tracer=[sum(c[i] for c in self.cells)+(self.loops[i]*self.plant['loop_concentrations_mol_m3'][i] if self.plant else 0.) for i in range(2)]
  return dict(initial=(self.initial_volume,self.initial_tracer),retained=(retained,tracer),collected=(sum(self.collected),[sum(c[i] for c in self.collected_tracer) for i in range(2)]),waste=(self.waste,self.waste_tracer),removed=(self.removed,self.removed_tracer))
 def _values(self):
  ps=self.vapor(self.temp_k);q=self.method['flow_rates_m3_s'];v={'power.enabled':self.powered,'clamp.lift_m':self.lift,'temperature.target_k':self.target_k,'pressure.backpressure_pa':self.bpr,'cycle.phase':self.phase,'cycle.elapsed_s':self.elapsed_s,'cycle.fault':self.fault,'plant.configured':self.plant is not None,'reactor.volume_m3':self.plant['chip_volume_m3'] if self.plant else None,'method.duration_s':self.method['duration_s'],'flow.targets_m3_s':q,'flow.actual_m3_s':self._flows() if self.phase=='running' else [0.,0.],'temperature.indicated_k':round(self.temp_k-273.15,1)+273.15 if self.powered and self.plant else None,'pressure.indicated_pa':[round(max(0.,x)/1e4)*1e4 for x in self.readings()],'pressure.vapor_pa':ps,'pressure.vapor_margin_pa':self._p_line()+101325-ps if ps is not None else None,'collection.total_m3':sum(self.collected),'collection.waste_m3':self.waste,'collector.x_m':self.collector_xy[0],'collector.y_m':self.collector_xy[1],'injection.valves':self.valves,'truth.cell_tracer_mol':self.cells,'truth.loop_remaining_m3':self.loops}
  v.update({'cycle.manual':self.manual,'pump.flow_set_m3_s':q[:] if self.phase=='running' and not self.manual else self.pump_q[:],'pump.channels':[dict(c,flow_m3_s=self.pump_q[i]) for i,c in enumerate(self.ch)],
            'pump.log':self.pump_log,'pump.usb':self.usb,'pump.clock':self._clock(),'pressure.enabled':self.p_enabled,'pressure.controller_pa':max(0.,self._sensor(2,self._p_line())) if self.powered else 0.,
            'pressure.tare_time_s':self.tare_time_s,'temperature.enabled':self.t_enabled,
            'injector.positions':['inject' if x else 'fill' for x in self.valves] if self.phase=='running' and not self.manual else self.valve_pos[:],
            'truth.pressure_sensor_offset_pa':self.p_offset[:],'truth.pressure_sensor_zero_pa':self.p_zero[:],'truth.backpressure_pa':self._p_line()})
  for name,(vol,mol) in self._inventory().items():v['truth.'+name+'_volume_m3']=vol;v['truth.'+name+'_mass_kg']=vol*(self.plant['density_kg_m3'] if self.plant else 0.);v['truth.'+name+'_tracer_mol']=mol
  return v
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('debug','evaluator')})
 def debug_snapshot(self):return dict(state=self.read('evaluator'),history=copy.deepcopy(self.history),plant=copy.deepcopy(self.plant))
 def _measure(self):
  v=self._values();return dict(phase=self.phase,fault=self.fault,elapsed_s=self.elapsed_s,temperature_k=v['temperature.indicated_k'],pump_pressures_pa=v['pressure.indicated_pa'],vapor_pressure_pa=v['pressure.vapor_pa'],vapor_margin_pa=v['pressure.vapor_margin_pa'],collected_volume_m3=sum(self.collected),waste_volume_m3=self.waste,conversion_fraction=None,yield_fraction=None,template_source=TEMPLATE_REL,evidence='synthetic_transport_temperature_pressure; NIST_literature_derived_pure_fluid_vapor_pressure')
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if (capability in FRONT_CAPS or capability in PANEL_CAPS) and _panel_ui.check_arguments(CAPS[capability][0],a):raise ActionRejected('INVALID_ARGUMENT')
  if 'slot' in a and (type(a['slot']) is not int or not 0<=a['slot']<8):raise ActionRejected('INVALID_ARGUMENT')
  if self.phase=='running' and capability not in ('power.set','acquisition.stop','flow.measure','history.read','product.read','residence.measure','sensor.configure_error') and not self._front_ok(capability,a):raise ActionRejected('BUSY')
  if capability=='power.set':
   was=self.powered;self.powered=a['enabled']
   if not self.powered and self.phase=='running':self.phase='cancelled';self.valves=[False]*2
   if not self.powered:self._stop_channels([0,1]);self.drive_until=[None,None];self.manual=False
   elif not was:self.power_epoch+=1;self.valve_pos=['fill','fill']                   # the injector valves initialise to F at switch-on (ri p.12)
   out=dict(a)
  elif capability=='clamp.set_lift':self.lift=a['lift_m'];out=dict(a)
  elif capability=='plant.configure':
   t=self.templates.get(a['substance_id']);f=next((f for f in t['fits'] if f['fit_id']==a['fit_id']),None) if t else None
   if self.plant is not None or self.lift<.039 or not a['pure_liquid_confirmed'] or not a['pre_equilibrated'] or len(set(a['container_ids']+[a['chip_id']]))!=3 or not f:raise ActionRejected('PRECONDITION_FAILED')
   if not f['runtime_temperature_range_k'][0]<=a['initial_temperature_k']<=f['runtime_temperature_range_k'][1] or (a['initial_temperature_k']<293.15 and not a['low_temperature_available']):raise ActionRejected('TEMPLATE_RANGE_UNAVAILABLE')
   for i,s in enumerate(a['syringes']):
    lo,hi=SYRINGES[s]
    if not lo*ULMIN<=self.method['flow_rates_m3_s'][i]<=hi*ULMIN:raise ActionRejected('INVALID_ARGUMENT')
   if self.bpr>a['gas_supply_pa']:raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);self.fit=f;self.temp_k=a['initial_temperature_k'];self.target_k=self.temp_k;self.reservoirs=a['reservoir_volumes_m3'][:];self.loops=a['loop_volumes_m3'][:];self.volumes=a['dead_volumes_m3'][:2]+[a['chip_volume_m3']/4]*4+[a['dead_volumes_m3'][2]];self.initial_volume=sum(self.reservoirs)+sum(self.loops)+sum(self.volumes);self.initial_tracer=[self.loops[i]*a['loop_concentrations_mol_m3'][i] for i in range(2)];self.ch=[self._channel(x) for x in a['syringes']];out=dict(configured=True,attachment_events=['host_attach:'+x for x in a['container_ids']+[a['chip_id']]])
  elif capability=='method.configure':
   windows=a['collection_windows'];prev=0.
   for w in windows:
    if type(w['slot']) is not int or not 0<=w['slot']<8 or not prev<=w['start_s']<w['end_s']<=a['duration_s']:raise ActionRejected('INVALID_ARGUMENT')
    prev=w['end_s']
   if any(t>a['duration_s'] for t in a['injection_times_s']):raise ActionRejected('INVALID_ARGUMENT')
   if self.plant:
    for i,s in enumerate(self.plant['syringes']):
     lo,hi=SYRINGES[s]
     if not lo*ULMIN<=a['flow_rates_m3_s'][i]<=hi*ULMIN:raise ActionRejected('INVALID_ARGUMENT')
   self.method=copy.deepcopy(a);out={'configured':True}
  elif capability=='temperature.set':
   if not self.plant or (a['target_k']<293.15 and not self.plant['low_temperature_available']):raise ActionRejected('PRECONDITION_FAILED')
   self.vapor(a['target_k']);self.target_k=a['target_k'];self.t_enabled=True;out=dict(a)
  elif capability=='pressure.set':
   if self.plant and a['backpressure_pa']>self.plant['gas_supply_pa']:raise ActionRejected('PRECONDITION_FAILED')
   self.bpr=a['backpressure_pa'];self.p_enabled=True;out=dict(a)
  elif capability=='acquisition.start':
   if not self.powered or self.lift>1e-5 or not self.plant or min(self.reservoirs)<=1e-15:raise ActionRejected('PRECONDITION_FAILED')
   fault=self._guard()
   if fault or not all(c['initialised'] for c in self.ch):raise ActionRejected('PRECONDITION_FAILED')
   if any(c['drive']!='idle' for c in self.ch):raise ActionRejected('BUSY')
   self.manual=False;self.valve_pos=['fill','fill']
   self._operation_count+=1;self.operation_id=f'{self.instance_id}:flow:{self._operation_count}';self.phase='running';self.fault='';self.elapsed_s=0.;self.history=[];self.history_truncated=False;self.next_record_s=self.time_s+1.;out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase=='running':self.phase='cancelled';self.valves=[False]*2
   if self.manual:self._stop_channels([0,1]);self.manual=False
   out={'phase':self.phase}
  elif capability=='collector.unload':
   i=a['slot']
   if a['container_id']!='fraction_'+str(i) or self.collected[i]<=0:raise ActionRejected('PRECONDITION_FAILED')
   self.removed+=self.collected[i];self.collected[i]=0.
   for j in range(2):self.removed_tracer[j]+=self.collected_tracer[i][j];self.collected_tracer[i][j]=0.
   out=dict(unloaded=True,attachment_event='host_release:'+a['container_id'])
  elif capability=='history.read':return dict(records=copy.deepcopy(self.history),truncated=self.history_truncated,template_source=TEMPLATE_REL,evidence='synthetic_trace_not_measured_Asia_output')
  elif capability=='product.read':return dict(volumes_m3=self.collected[:],tracer_mol=copy.deepcopy(self.collected_tracer),reaction_products=None,evidence='passive_tracer_inventory; no_chemical_yield')
  elif capability=='residence.measure':
   if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
   q=self.method['flow_rates_m3_s'];total=sum(q);taus=[self.volumes[i]/q[i] for i in range(2)];mean_in=sum(taus[i]*q[i]/total for i in range(2));var_in=sum(q[i]/total*(taus[i]**2+(taus[i]-mean_in)**2) for i in range(2));chip=self.plant['chip_volume_m3']/total;outlet=self.volumes[-1]/total
   return dict(mean_s=mean_in+chip+outlet,variance_s2=var_in+chip**2/4+outlet**2,chip_mean_s=chip,evidence='synthetic_linear_CSTR_network_analytic_moments; injection_loop_pulse_not_included')
  elif capability in FRONT_CAPS:return self._front(capability,a)
  elif capability in PANEL_CAPS:return self._panel_cmd(capability,a)
  elif capability=='sensor.configure_error':self.p_offset[SENSORS.index(a['sensor'])]=float(a['offset_pa']);out=dict(a)
  else:return self._measure()
  self.revision+=1;return copy.deepcopy(out)
 # ---- module front ends: pump channels P1 / P2, pressure and climate controllers, reagent injector
 def _channel(self,syringe):
  return dict(syringe=syringe,initialised=True,primed=True,drive='idle',last_flow_m3_s=None,fill_flow_m3_s=min(1000.,SYRINGES[syringe][1])*ULMIN,max_pressure_pa=2e6,over_pressure='both',alarm='',zeroed_s=None)
 def _ratio(self,i):
  """Delivered / set volume when the S. VOL setting differs from the fitted syringes (the drive speed follows the setting, pump p.26)."""
  return SYRINGE_UL[self.plant['syringes'][i]][1]/SYRINGE_UL[self.ch[i]['syringe']][1] if self.plant else 1.
 def _flows(self):
  if self.manual:return [self.pump_q[i]*self._ratio(i) if self.ch[i]['drive']=='pumping' else 0. for i in range(2)]
  return [x*self._ratio(i) for i,x in enumerate(self.method['flow_rates_m3_s'])]
 def _bpr(self):return self.bpr if self.p_enabled else 0.
 def _target(self):return self.target_k if self.t_enabled else AMBIENT_K
 def _clock(self):return (CLOCK0+datetime.timedelta(seconds=int(self.time_s+self.clock_offset_s))).strftime('%Y-%m-%dT%H:%M')
 def _log(self,text):
  self._log_n+=1;self.pump_log.append(dict(n=self._log_n,time_s=self.time_s,clock=self._clock(),text=text))
  if len(self.pump_log)>LOG_MAX:self.pump_log.pop(0)
 def _fill_s(self,i):return SYRINGE_UL[self.ch[i]['syringe']][1]*1e-9/self.ch[i]['fill_flow_m3_s']
 def _stop_channels(self,idx,alarm=''):
  for i in idx:
   self.pump_q[i]=0.;self.drive_until[i]=self.drive_then[i]=None;self.ch[i].update(drive='idle',alarm=alarm)
 def _end_manual(self,fault):
  """A manual run ends when no channel pumps or fills on its way to pumping."""
  if self.manual and not any(c['drive']=='pumping' or self.drive_then[i]=='pumping' for i,c in enumerate(self.ch)):
   self.manual=False;self.valves=[False]*2
   if self.phase=='running':self.phase='failed' if fault else 'idle';self.fault=fault
 def _over_pressure(self):
  """OVER P (pump p.27): a channel above its MAX PRESSURE stops alone (SINGLE) or with the other one (BOTH; ALL acts as BOTH)."""
  pr=self.readings()                                                                           # the channel's own sensor reading
  for i in range(2):
   if self.ch[i]['drive']=='pumping' and pr[i]>self.ch[i]['max_pressure_pa']+1e-6:
    self._stop_channels([i] if self.ch[i]['over_pressure']=='single' else [0,1],alarm='OVER P');self._log(f'PUMP{i+1} OVER P')
  self._end_manual('overpressure')
 def _drives(self):
  for i,c in enumerate(self.ch):
   t=self.drive_until[i]
   if t is None or self.time_s<t-1e-9:continue
   then,self.drive_until[i],self.drive_then[i]=self.drive_then[i],None,None
   if c['drive']=='initialising':c.update(initialised=True,primed=False,drive='idle')
   elif c['drive']=='emptying':c.update(primed=False,drive='idle')
   elif c['drive']=='filling':
    c.update(primed=True,drive='pumping' if then=='pumping' else 'idle')
    if then=='pumping':self._log(f'PUMP{i+1} PUMPING')
 def _front_ok(self,capability,a):
  """Commands taken while a run goes on: the panel itself; in a manual run the module fronts; in a PC method run only the pump's STOP and USB."""
  if capability in PANEL_CAPS:return True
  if self.manual:return capability in MANUAL_OK
  return capability=='pump.usb' or (capability=='pump.action' and a['action']=='stop')
 def _front(self,capability,a):
  i={'P1':0,'P2':1}.get(a.get('channel'));c=self.ch[i] if i is not None else None
  if capability!='pump.usb' and not self.powered:raise ActionRejected('PRECONDITION_FAILED')
  if capability.startswith('pump.') and capability not in ('pump.usb','pump.set_clock') and not self.plant:raise ActionRejected('PRECONDITION_FAILED')
  if capability=='pump.set_flow':
   q=a['flow_m3_s'];lo,hi=SYRINGES[c['syringe']]
   if q<=0:
    self._stop_channels([i]);self._log(f'PUMP{i+1} STOP');self._end_manual('')
   else:
    if not lo*ULMIN*(1-1e-9)<=q<=hi*ULMIN*(1+1e-9):raise ActionRejected('INVALID_ARGUMENT')
    if not c['initialised']:raise ActionRejected('PRECONDITION_FAILED')                         # inoperable until INITIALISE after a syringe change (pump p.26)
    if c['drive'] in ('initialising','emptying') or (c['drive']=='filling' and self.drive_then[i]!='pumping'):raise ActionRejected('BUSY')
    if c['drive']=='idle' and (self.lift>1e-5 or self.reservoirs[i]<=1e-15):raise ActionRejected('PRECONDITION_FAILED')
    if not self.manual:
     qs=[0.,0.];qs[i]=q*self._ratio(i)
     if self._guard(qs,True):raise ActionRejected('PRECONDITION_FAILED')
    self.pump_q[i]=q;c.update(last_flow_m3_s=q,alarm='')
    if c['drive']=='idle':
     if c['primed']:c['drive']='pumping';self._log(f'PUMP{i+1} PUMPING')
     else:c['drive']='filling';self.drive_until[i]=self.time_s+self._fill_s(i);self.drive_then[i]='pumping';self._log(f'PUMP{i+1} FILLING')   # pump p.23 step 5
    if not self.manual:
     self.manual=True;self._operation_count+=1;self.operation_id=f'{self.instance_id}:manual:{self._operation_count}';self.phase='running';self.fault='';self.elapsed_s=0.;self.history=[];self.history_truncated=False;self.next_record_s=self.time_s+1.
   out=dict(a)
  elif capability=='pump.action':
   act=a['action']
   if act=='stop':
    if self.phase=='running' and not self.manual:self.phase='cancelled';self.valves=[False]*2      # the pump stops: the PC run ends
    self._stop_channels([i]);self._log(f'PUMP{i+1} STOP');self._end_manual('')
   else:
    if c['drive']!='idle':raise ActionRejected('BUSY')
    if act=='zero_pressure':c['zeroed_s']=self.time_s;self.p_zero[i]=self.pressures()[i]+self.p_offset[i];self._log(f'PUMP{i+1} ZERO P')   # the raw reading now becomes 0 (pump p.27: tubing disconnected)
    elif act=='initialise':c['drive']='initialising';self.drive_until[i]=self.time_s+INIT_S;self._log(f'PUMP{i+1} INIT')
    else:
     if not c['initialised']:raise ActionRejected('PRECONDITION_FAILED')
     c['drive']='filling' if act=='fill' else 'emptying';self.drive_until[i]=self.time_s+self._fill_s(i);self._log(f'PUMP{i+1} '+('FILLING' if act=='fill' else 'EMPTYING'))
   out=dict(a)
  elif capability=='pump.configure':
   if len(a)<2:raise ActionRejected('INVALID_ARGUMENT')
   syr=a.get('syringe',c['syringe']);lo,hi=SYRINGES[syr];fill=a.get('fill_flow_m3_s')
   if syr!=c['syringe'] and c['drive']!='idle':raise ActionRejected('BUSY')
   if fill is not None and not lo*ULMIN*(1-1e-9)<=fill<=hi*ULMIN*(1+1e-9):raise ActionRejected('INVALID_ARGUMENT')
   if syr!=c['syringe']:c.update(syringe=syr,initialised=False,primed=False,fill_flow_m3_s=min(max(c['fill_flow_m3_s'],lo*ULMIN),hi*ULMIN));self._log(f'PUMP{i+1} S. VOL')
   if fill is not None:c['fill_flow_m3_s']=fill
   for k in ('max_pressure_pa','over_pressure'):
    if k in a:c[k]=a[k]
   out=dict(channel=a['channel'],**{k:c[k] for k in ('syringe','fill_flow_m3_s','max_pressure_pa','over_pressure','initialised')})
  elif capability=='pump.set_clock':
   try:new=datetime.datetime(a['year'],a['month'],a['day'],a['hour'],a['minute'])
   except ValueError:raise ActionRejected('INVALID_ARGUMENT') from None
   if self.manual or any(c['drive']!='idle' for c in self.ch):raise ActionRejected('BUSY')            # SET AND RESTART restarts the pump
   self.clock_offset_s=(new-CLOCK0).total_seconds()-int(self.time_s);self._log('CLOCK SET');out=dict(clock=self._clock())
  elif capability=='pump.usb':
   act=a['action']
   if act=='insert':self.usb['inserted']=True
   elif not self.usb['inserted']:raise ActionRejected('PRECONDITION_FAILED')
   elif act=='copy_logs':
    if not self.powered or self.phase=='running' or any(c['drive']!='idle' for c in self.ch):raise ActionRejected('PRECONDITION_FAILED')   # pump idle (pump p.31)
    stamp=self._clock().replace('-','').replace(':','').replace('T','_')
    self.usb['files']=sorted(set(self.usb['files'])|{'logs/asia_system.txt',f'logs/AsiaPumpDataLog_{stamp}.xls','logs/errorLog.xls'});self._log('LOGS COPIED')
   else:self.usb['inserted']=False
   out=copy.deepcopy(self.usb)
  elif capability=='injector.set_valve':
   if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
   self.valve_pos[0 if a['valve']=='left' else 1]=a['position'];out=dict(a)
  elif capability=='pressure.enable':self.p_enabled=a['enabled'];out=dict(a)
  elif capability=='temperature.enable':
   if not a['enabled'] and self.plant:self.vapor(AMBIENT_K)                                      # the fit must cover room temperature
   self.t_enabled=a['enabled'];out=dict(a)
  else:self.tare_time_s=self.time_s;self.p_zero[2]=self.p_offset[2];out={'tared':True}                   # switch-on tare: the sensor sees ambient (pc p.16)
  self.revision+=1;return copy.deepcopy(out)
 # ---- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
 def panel_read(self):
  out=self.read('agent');rng=[SYRINGES[c['syringe']] for c in self.ch]
  out.update({'host.powered':self.powered,'host.power_epoch':self.power_epoch,'host.max_bpr_bar':(self.plant['gas_supply_pa'] if self.plant else 2e6)/1e5,
              'host.flow_min_ul':[r[0] for r in rng],'host.flow_max_ul':[r[1] for r in rng],
              'host.firmware':FIRMWARE});return out
 def panel_invoke(self,capability_id,arguments):
  try:
   if self._panel_session is not None:self._panel_session._guard(capability_id,arguments)   # a gesture made through a session keeps that caller's access rules
   return self.invoke(capability_id,**arguments)
  except ActionRejected as exc:raise _panel_ui.PanelRejected(str(exc)) from None
 def panel_time(self):return self.time_s
 def _panel_cmd(self,capability,a):
  if self.panel is None:raise ActionRejected('UNSUPPORTED')
  try:
   if capability=='panel.press':out=dict(accepted=bool(self.panel.press(a['input_id'],float(a.get('duration_s',.1)))))
   elif capability=='panel.turn':out=dict(accepted=bool(self.panel.turn(a['input_id'],int(a['detents']))))
   elif capability=='panel.switch':out=dict(accepted=bool(self.panel.switch(a['input_id'],a['position'],held=tuple(a.get('held',())))))
   elif capability=='panel.frame':return dict(frame=self.panel.frame())
   else:
    import hashlib,tempfile
    data,w,h=self.panel.png_bytes(a['display_id']);sha=hashlib.sha256(data).hexdigest();d=Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir())/'chemistry_sim_panel'/self.instance_id;d.mkdir(parents=True,exist_ok=True);path=d/f"{a['display_id']}_{sha[:16]}.png"
    if not path.exists():path.write_bytes(data)
    return dict(display_id=a['display_id'],time_s=self.time_s,image=dict(path=str(path),format='png',width=w,height=h,sha256=sha))
  except _panel_ui.PanelError:raise ActionRejected('INVALID_ARGUMENT') from None
  self.revision+=1;return out
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<=0:raise ValueError('finite positive dt required')
  left=float(dt_s)
  while left>1e-10:
   h=min(.02,left);p=self.plant
   for t in self.drive_until:
    if t is not None and t-self.time_s>1e-9:h=min(h,t-self.time_s)
   if self.phase=='running' and self.manual:self._over_pressure()
   active=self.phase=='running';q=self._flows();total=sum(q)
   if active:
    lim=[.2*self.volumes[i]/(q[i] if i<2 else total) for i in range(7) if (q[i] if i<2 else total)>0]
    if self.manual:h=min([h]+lim)
    else:
     h=min(h,self.method['duration_s']-self.elapsed_s,*lim)
     events=self.method['injection_times_s']+[x[k] for x in self.method['collection_windows'] for k in ['start_s','end_s']]
     for event in events:
      if event>self.elapsed_s+1e-9:h=min(h,event-self.elapsed_s)
    dest=None if self.manual else next((x['slot'] for x in self.method['collection_windows'] if x['start_s']-1e-9<=self.elapsed_s<x['end_s']-1e-9),None)
    fault=self._guard(q,self.manual)
    if min(self.reservoirs[i]-q[i]*h for i in range(2))< -1e-15:fault='reservoir_empty'
    if dest is not None and self.collected[dest]+total*h>5e-6+1e-15:fault='collector_full'
    if fault:
     self.phase='failed';self.fault=fault;self.valves=[False]*2;active=False
     if self.manual:self._stop_channels([0,1]);self.manual=False
    else:
     old=copy.deepcopy(self.cells);outflows=[[old[i][j]/self.volumes[i]*(q[i] if i<2 else total)*h for j in range(2)] for i in range(7)]
     incoming=[[0.,0.] for _ in range(7)]
     for i in range(2):
      self.valves[i]=(self.valve_pos[i]=='inject' if self.manual else self.elapsed_s>=self.method['injection_times_s'][i]-1e-9) and self.loops[i]>1e-15
      dv=min(self.loops[i],q[i]*h) if self.valves[i] else 0.;incoming[i][i]=dv*p['loop_concentrations_mol_m3'][i];self.loops[i]-=dv;self.reservoirs[i]=max(0.,self.reservoirs[i]-q[i]*h)
     incoming[2]=[outflows[0][j]+outflows[1][j] for j in range(2)]
     for i in range(3,7):incoming[i]=outflows[i-1]
     self.cells=[[max(0.,old[i][j]+incoming[i][j]-outflows[i][j]) for j in range(2)] for i in range(7)]
     if dest is None:self.waste+=total*h;self.waste_tracer=[self.waste_tracer[j]+outflows[6][j] for j in range(2)]
     else:
      self.collector_xy=[(dest%4)*.1,(dest//4)*.1];self.collected[dest]+=total*h
      self.collected_tracer[dest]=[self.collected_tracer[dest][j]+outflows[6][j] for j in range(2)]
     self.elapsed_s+=h
     if not self.manual and self.elapsed_s>=self.method['duration_s']-1e-9:self.phase='succeeded';self.valves=[False]*2
   if self.powered and p:self.temp_k=self._target()+(self.temp_k-self._target())*math.exp(-h/p['thermal_tau_s'])
   self.time_s+=h;left-=h;self._drives()
   if self.operation_id and (self.time_s>=self.next_record_s-1e-9 or (active and self.phase!='running')):
    v=self._values();self.history.append(dict(time_s=self.time_s,elapsed_s=self.elapsed_s,temperature_k=v['temperature.indicated_k'],pressure_pa=v['pressure.indicated_pa'],collected_m3=sum(self.collected),phase=self.phase));self.next_record_s=self.time_s+1.
    if len(self.history)>1000:self.history.pop(0);self.history_truncated=True
  self.revision+=1;return self.read('evaluator')
# ---- pump firmware screens drawn by the runtime for the panel engine (hooks of panel_ui.json): the home screen with its arrows, the log list and
# the date / time line (pump p.22-23, p.28-31)
HOME_POS=['p1','f1','usb','m','p2','f2','log']
def _flow_text(ul):
 """Flow on the pump display: µl/min with one decimal below 100, whole numbers to 999, then ml/min with two decimals (pump p.22)."""
 if ul>=999.95:return f'{ul/1000:.2f}','ml/min'
 return (f'{ul:.0f}' if ul>=99.95 else f'{ul:.1f}'),'µl/min'
def _hook_flow(panel,ctx,s):
 q=panel._src(s['src'],ctx);val,unit=_flow_text((q or 0.)/ULMIN);return val+unit
def _home_positions(panel):return [p for p in HOME_POS if p!='usb' or panel._state()['pump.usb']['inserted']]
def _hook_home(panel,ctx,s):
 st,v=panel._state(),panel.ui['vars'];sel=v.get('sel') or 'p1';n=s['line'];editing=panel.ui['screen']=='flow_edit' and v.get('ch')==n+1
 ul=panel._src({'field':'flow'},ctx) if editing else st['pump.flow_set_m3_s'][n]/ULMIN
 val,unit=_flow_text(ul or 0.);P,F=('p1','f1') if n==0 else ('p2','f2')
 a=('→' if sel==P else ' ')+f'P{n+1}'+('←' if sel==P else '*' if editing else '→' if sel==F else ' ')+val+('*' if editing else '←' if sel==F else ' ')+unit
 if n==0:right=(('→ψ←' if sel=='usb' else 'ψ') if st['pump.usb']['inserted'] else '')+('→M←' if sel=='m' else ' M ')
 else:right='→LOG←' if sel=='log' else ' LOG '
 return a[:20-len(right)].ljust(20-len(right))+right
def _hook_home_move(panel,ctx,a):
 pos=_home_positions(panel);sel=panel.ui['vars'].get('sel') or 'p1';k=pos.index(sel) if sel in pos else 0
 panel.ui['vars']['sel']=pos[min(max(k+int(ctx['ev']['detents']),0),len(pos)-1)];return True
def _hook_log(panel,ctx,s):
 log=panel._state()['pump.log'];k=(panel.ui['vars'].get('log_i') or 0)+s['line']
 if not log:return 'NO LOG' if s['line']==0 else ''
 if k>=len(log):return ''
 return (('→' if s['line']==0 else ' ')+f"{log[k]['n']}) {log[k]['text']}")[:20]
def _hook_log_move(panel,ctx,a):
 n=len(panel._state()['pump.log']);panel.ui['vars']['log_i']=min(max((panel.ui['vars'].get('log_i') or 0)+int(ctx['ev']['detents']),0),max(n-1,0));return True
CLOCK_FIELDS=[('clk_h',0,23),('clk_mi',0,59),('clk_d',1,31),('clk_mo',1,12),('clk_y',2000,2099)]
def _hook_clock_open(panel,ctx,a):
 c=panel._state()['pump.clock'];y,mo,d,h,mi=int(c[0:4]),int(c[5:7]),int(c[8:10]),int(c[11:13]),int(c[14:16])
 panel.ui['vars'].update(clk_h=h,clk_mi=mi,clk_d=d,clk_mo=mo,clk_y=y,clk_sel=0,clk_edit=False);return True
def _hook_clock_turn(panel,ctx,a):
 """Turn: move between the values and SET AND RESTART, or change the value being edited (wrapping, as a clock does)."""
 v,n=panel.ui['vars'],int(ctx['ev']['detents'])
 if v.get('clk_edit'):
  k,lo,hi=CLOCK_FIELDS[v['clk_sel']];v[k]=lo+(v[k]-lo+n)%(hi-lo+1)
 else:v['clk_sel']=min(max(v['clk_sel']+n,0),len(CLOCK_FIELDS))
 return True
def _hook_clock(panel,ctx,s):
 v=panel.ui['vars'];sel,ed=v.get('clk_sel',0),v.get('clk_edit')
 def f(i,text):return ('*'+text+'*' if ed else '→'+text+'←') if sel==i else text
 if s['line']==1:return ('   →SET AND RESTART←' if sel==len(CLOCK_FIELDS) else '    SET AND RESTART')[:20]
 time,date=f(0,f"{v['clk_h']:02d}")+':'+f(1,f"{v['clk_mi']:02d}"),f(2,str(v['clk_d']))+'/'+f(3,str(v['clk_mo']))+'/'+f(4,str(v['clk_y']))
 return (time+date.rjust(20-len(time)))[:20]
PANEL_HOOKS={'asia_flow':_hook_flow,'asia_home':_hook_home,'asia_home_move':_hook_home_move,'asia_log':_hook_log,'asia_log_move':_hook_log_move,'asia_clock_open':_hook_clock_open,
             'asia_clock_turn':_hook_clock_turn,'asia_clock':_hook_clock}
RUN_GUARDED=('acquisition.stop','pump.set_flow','pump.action','pump.configure','injector.set_valve','pressure.set','pressure.enable','pressure.tare','temperature.set','temperature.enable')
class SyrrisAsiadiscoverySession:
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
        meta=export_syrris_asiadiscovery_contract_metadata()
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
        if not isinstance(capability_id,str) or capability_id not in CAPS: raise ActionRejected('UNKNOWN_CAPABILITY')
        if self._role=='agent' and CAPS[capability_id][5]!='agent': raise ActionRejected('ACCESS_DENIED')
        if not isinstance(request_id,str) or not request_id: raise ActionRejected('INVALID_ARGUMENT')
        validate(CAPS[capability_id][0],arguments)
        key=(self._principal,instance_id,request_id); digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
        if key in h._requests:
            old,result=h._requests[key]
            if old!=digest: raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(result)
        self._guard(capability_id,arguments); before=h.operation_id; h._panel_session=self if capability_id in PANEL_CAPS else None
        try: result=h.invoke(capability_id,**arguments)
        finally: h._panel_session=None
        op=result.get('operation_id')
        if op: h._owners[op]=self._principal
        elif h.operation_id and h.operation_id!=before: h._owners[h.operation_id]=self._principal   # a manual run started by a flow on the pump (panel or pump.set_flow) belongs to the caller
        response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=op,
          status='accepted' if op else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None)
        h._requests[key]=(digest,copy.deepcopy(response)); return response

    def _guard(self,capability_id,arguments):
        """An agent may not stop or change another agent's running method or manual run (direct call or module front alike).  The rear ON/OFF
        switches (power.set) are hard switches (lead rule 2026-10-19): never refused, the system reacts as to a power loss."""
        h=self._host; owner=h._owners.get(h.operation_id)
        if self._role=='agent' and h.phase=='running' and owner is not None and owner!=self._principal and capability_id in RUN_GUARDED: raise ActionRejected('ACCESS_DENIED')

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

def export_syrris_asiadiscovery_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'SyrrisAsiadiscoverySession.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key=='acquisition.start'; pre=[]
        conditions={
         'acquisition.start':('供电夹紧、已预充且泵压力/汽化边界允许',['power.enabled','clamp.lift_m','plant.configured'],'powered and clamped and configured and hydraulic_guard'),
         'plant.configure':('空系统开夹，宿主确认液态/预平衡，源温段支持',['plant.configured','clamp.lift_m'],'not configured and open and valid_pure_fluid'),
         'clamp.set_lift':('停泵',['cycle.phase'],'phase!=running'),
         'method.configure':('停泵、注射器流量与收集窗口有效',['cycle.phase'],'not running and syringe_ranges and ordered_nonoverlapping_windows'),
         'collector.unload':('停泵，有收集液且容器身份匹配',['cycle.phase','collection.total_m3'],'not running and loaded_slot and expected_container'),
         'pump.set_flow':('供电、已装配、通道已初始化且不在PC方法运行；流量在所设注射器量程内；从空闲开始需夹紧、该路储液未空且汽化检查通过',['power.enabled','plant.configured','pump.channels','cycle.phase','clamp.lift_m'],'powered and configured and initialised and not method_run and in_syringe_range'),
         'pump.action':('供电、已装配；STOP随时可用；其余动作须该通道空闲，FILL/EMPTY须已初始化',['power.enabled','plant.configured','pump.channels'],'powered and configured and (stop or channel_idle)'),
         'pump.configure':('供电、已装配、不在PC方法运行；换注射器须该通道空闲',['power.enabled','plant.configured','pump.channels','cycle.phase'],'powered and configured and not method_run'),
         'pump.set_clock':('供电，泵空闲（SET AND RESTART重启固件）',['power.enabled','pump.channels','cycle.manual'],'powered and pump_idle and valid_date'),
         'pump.usb':('COPY DATA LOGS须插着U盘、供电且泵空闲',['pump.usb','power.enabled','pump.channels','cycle.phase'],'inserted and pump_idle for copy'),
         'injector.set_valve':('供电、已装配、不在PC方法运行',['power.enabled','plant.configured','cycle.phase'],'powered and configured and not method_run'),
         'temperature.enable':('供电、不在PC方法运行；关闭时室温须在拟合温段内',['power.enabled','cycle.phase'],'powered and not method_run'),
         'pressure.enable':('供电、不在PC方法运行',['power.enabled','cycle.phase'],'powered and not method_run'),
         'pressure.tare':('供电、不在PC方法运行',['power.enabled','cycle.phase'],'powered and not method_run'),
         'sensor.configure_error':('无（评测者设定，缺省0）',['truth.pressure_sensor_offset_pa'],'always')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.syrris_asiadiscovery.SyrrisAsiadiscoverySession.invoke',invocation=f'session.invoke(instance_id="asiadiscovery_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；达到输送时长；取消保留残液和示踪库存' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电不回滚已经输送/收集的液体与示踪库存。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    evolving=[k for k in slots if k.startswith('truth.')]+['cycle.elapsed_s','cycle.phase','cycle.fault','temperature.indicated_k','pressure.indicated_pa','pressure.vapor_pa','pressure.vapor_margin_pa','flow.actual_m3_s','collector.x_m','collector.y_m','collection.total_m3','collection.waste_m3','injection.valves']
    caps['acquisition.start']['state_effects'] += [dict(state_id=k,timing='on_step',description_zh='step推进管网输送/热响应/窗口分流，读取不推进') for k in evolving if k not in [e['state_id'] for e in caps['acquisition.start']['state_effects']]]
    for key in ['plant.configure','collector.unload']:
        caps[key]['state_effects'] += [dict(state_id=k,timing='immediate',description_zh='宿主装入/移除引起库存变化') for k in evolving if k.startswith('truth.') and k not in [e['state_id'] for e in caps[key]['state_effects']]]
    caps['temperature.set']['state_effects'].append(dict(state_id='temperature.indicated_k',timing='on_step',description_zh='一阶未标定设定响应，非真实热量账本'))
    mechanism=dict(component_ids=['pump','injector','chip','climate','pressure','collector'],state_ids=evolving,meaning_zh='同纯载液两种被动示踪物的守恒有限体积网络，真实蒸气压仅限文献温段',implementation='lumped',equations=['Psat=1e5*10**(A-B/(T+C)); valid single NIST fit only','Ppump_i=BPR+Rcommon*(q0+q1)+Rbranch_i*qi <=2e6 Pa gauge','single_phase_guard: 101325+BPR > max(Psat(T),Psat(Ttarget))','two inlet CSTRs -> four equal chip CSTRs -> outlet CSTR; explicit conservative transfers','dMcell_i=Min-Mout; Mout=C_old*Q*dt; dt<=0.2*V/Q','sample loops ideal plug displacement; equal carrier refill, constant loop volume','initial=retained+collected+waste+removed for volume, constant-density mass and each tracer','tau_chip=Vchip/Qtotal; variance_chip=tau_chip**2/4; branch residence mixture plus outlet','T=Ttarget+(Told-Ttarget)*exp(-dt/tau_thermal), no calibrated energy model'],parameters={},source_ids=['manufacturer_primary','flow_templates','simulation_assumption'],limitations=['Host resistances/dead volumes/density/thermal tau explicit assumptions; no calibrated hydraulics or RTD.','Same pure carrier and passive dilute tracers; no true chemistry, product yield, mixing-solvent activity or multiphase simulation.','Pre-primed/pre-equilibrated liquid confirmed by host; no startup filling/purge or complete drainage.','8x5mL collector subset; instant virtual XY motion and static visible vial proxies.'])
    _ann.annotate_export('syrris_asiadiscovery',caps,slots,{'flow_transport':mechanism},inputs={'plant.configure':(PLANT,EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'flow_transport':mechanism})

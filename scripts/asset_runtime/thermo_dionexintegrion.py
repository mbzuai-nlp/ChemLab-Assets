"""CHEM-098 IC: aqueous diffusion data, finite fluid/species ledger and host clock.
Trusted Python host only. Sessions filter identity/truth; process/file isolation is external.
"""
from pathlib import Path
import copy,math,json,importlib.util,hashlib,os,re,tempfile
PROTOCOL_VERSION='1.0.0';SLUG='thermo_dionexintegrion';CATALOG_ID='CHEM-098';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];TEMPLATE_REL='assets/expansion_batch03/t3_analytical/'+SLUG+'/ion_library.json'
class ActionRejected(ValueError):pass
def number(unit='1',lo=None,hi=None):
 d={'type':'number','x-unit':unit}
 if lo is not None:d['minimum']=lo
 if hi is not None:d['maximum']=hi
 return d
def obj(p):return dict(type='object',properties=p,required=list(p),additionalProperties=False)
def arr(v,n=None):
 d=dict(type='array',items=v)
 if n is not None:d.update(minItems=n,maxItems=n)
 return d
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
def enum(v):return {'type':'string','enum':v}
TEXT={'type':'string'};BOOL={'type':'boolean'};ISO=enum(['2H','13C','15N','18O','34S']);PHASE=enum(['idle','running','succeeded','cancelled','failed'])
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
  if 'properties' not in s:                                            # free-form object (e.g. the structured panel frame)
   if not isinstance(v,dict):raise ActionRejected('INVALID_ARGUMENT')
   return
  if not isinstance(v,dict) or not set(s.get('required',[]))<=v.keys() or set(v)-set(s['properties']):raise ActionRejected('INVALID_ARGUMENT')
  for k,x in v.items():validate(s['properties'][k],x)
 elif t=='array':
  if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
  for x in v:validate(s['items'],x)
 if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')
PLANT=obj({'ambient_k':number('K',277.15,313.15),'reservoir_m3':number('m^3',1e-9,.1),'waste_capacity_m3':number('m^3',1e-9,.1),'hydraulic_resistance_pa_s_m3':number('Pa*s/m^3',1,1e17),'thermal_tau_s':number('s',.01,1e5),'suppressor_efficiency':number('1',.01,1),'accept_synthetic_retention':BOOL})
PLANT_EXAMPLE=dict(ambient_k=298.15,reservoir_m3=.002,waste_capacity_m3=.002,hydraulic_resistance_pa_s_m3=8e14,thermal_tau_s=2.,suppressor_efficiency=1.,accept_synthetic_retention=True)
METHOD=obj({'flow_m3_s':number('m^3/s',1e-9/60,1e-5/60),'eluent_mol_m3':number('mol/m^3',.1,100),'column_k':number('K',303.15,353.15),'suppression_current_a':number('A',0),'mode':enum(['anion','cation']),'rfic':BOOL,'run_s':number('s',.1,7200),'injection_m3':number('m^3',1e-10,1e-6)})
METHOD_EXAMPLE=dict(flow_m3_s=1e-6/60,eluent_mol_m3=10.,column_k=303.15,suppression_current_a=.05,mode='anion',rfic=True,run_s=60.,injection_m3=1e-8)
SOLUTE=obj({'substance_id':TEXT,'concentration_mol_m3':number('mol/m^3',0,1000),'retention_volume_m3':number('m^3',1e-9,.01),'width_fraction':number('1',.01,1)})
SAMPLE=obj({'sample_id':TEXT,'volume_m3':number('m^3',1e-10,.01),'solutes':dict(arr(SOLUTE),minItems=1,maxItems=59),'accept_unresolved_speciation':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='aqueous_01',volume_m3=1e-6,solutes=[dict(substance_id='cl_anion1',concentration_mol_m3=.2,retention_volume_m3=4e-7,width_fraction=.15),dict(substance_id='no3_anion1',concentration_mol_m3=.1,retention_volume_m3=6e-7,width_fraction=.13)],accept_unresolved_speciation=True)
POINT=obj({'time_s':number('s',0),'conductivity_s_m':number('S/m',0),'window_s':number('s',0)})
RESULT=obj({'phase':PHASE,'complete':BOOL,'points':arr(POINT),'elapsed_s':number('s',0),'template_source':TEXT,'evidence':TEXT,'warnings':arr(TEXT)})
LEDGER=obj({'reservoir_m3':number('m^3',0),'waste_m3':number('m^3',0),'sample_remaining_m3':number('m^3',0),'volume_residual_m3':number('m^3'),'species_residual_mol':number('mol'),'injected_mol':number('mol',0),'column_mol':number('mol',0),'eluted_mol':number('mol',0),'sample_id':TEXT})
CAPS={};UNSUPPORTED={'hardware.connect','speciation.solve'}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},effects or [])
cap('plant.configure',PLANT,obj({'configured':BOOL}),['housing','pump_drive'],'宿主配置有限淋洗液、废液容器、阻力与温控假设','configuration','evaluator',PLANT_EXAMPLE,['plant.configured','temperature.compartment_k','temperature.cd_cell_k'])
cap('method.configure',METHOD,obj({'configured':BOOL}),['pump_drive','column_compartment','suppressor','eluent_generator'],'RFIC约束下设置方法；不推进物理','configuration','agent',METHOD_EXAMPLE,['method.configured','pump.target_m3_s','column.target_k','eluent.target_mol_m3','suppressor.current_a'])
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_port'],'宿主连接有限样液；逐物质保留/展宽由宿主声明为synthetic','configuration','evaluator',SAMPLE_EXAMPLE,['sample.loaded'])
cap('sample.unload',obj({}),obj({'returned_m3':number('m^3',0)}),['sample_port'],'归还尚未进样的液体，已进柱物质保留','device_command','evaluator',effects=['sample.loaded'])
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet'],'供电；断电取消采集并关闭泵目标',ex={'enabled':True},effects=['power.enabled','pump.enabled','acquisition.phase','electrolytics.egc_on','electrolytics.crtc_on','electrolytics.suppressor_on','pump.priming','detector.total_us'])
cap('pump.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['pump_drive'],'只写泵运行目标；流量和体积只在step改变',ex={'enabled':True},effects=['pump.enabled','electrolytics.egc_on','electrolytics.crtc_on','electrolytics.suppressor_on','pump.priming'])
for k,c,limit in [('door.set','main_door',110),('display_door.set','display_door',95)]:cap(k,obj({'angle_rad':number('rad',0,math.radians(limit))}),obj({'angle_rad':number('rad',0,math.radians(limit))}),[c],'检修门角度（手工开关，任何时候都不被拒；运行中打开柱温箱门记录手册警告Column door opened，运行继续）',ex={'angle_rad':0.},effects=(['alerts'] if k=='door.set' else [])+['door.angle_rad' if c=='main_door' else 'display_door.angle_rad'])
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['injection_valve','column_separator','conductivity_cell'],'从有限样液取方法指定体积进柱，异步采集电导（序列运行：RFIC方法按仪器方法打开EGC、CR-TC与抑制器）；拒绝时不转移',effects=['acquisition.phase','acquisition.elapsed_s','valve.injected','valve.position','valve.elapsed_s','consumables'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['pump_drive','conductivity_cell'],'停止泵目标并取消，已进样/已流出物质不回滚',effects=['acquisition.phase','pump.enabled','electrolytics.egc_on','electrolytics.crtc_on','electrolytics.suppressor_on','pump.priming'])
cap('process.measure',obj({}),RESULT,['conductivity_cell','display_digits'],'只读实际已生成色谱窗口；不推时钟','query')
cap('template.read',obj({'substance_id':TEXT}),obj({'substance_id':TEXT,'diffusion_m2_s':number('m^2/s',0),'molar_conductivity_s_m2_mol':number('S*m^2/mol',0),'charge':dict(number(),type='integer'),'template_source':TEXT,'evidence':TEXT}),['conductivity_cell'],'读真实扩散模板和派生无限稀释电导','query',ex={'substance_id':'cl_anion1'})
cap('ledger.read',obj({}),LEDGER,['sample_port','waste_port','column_separator'],'私有体积与全部已注入物种量账本','query','evaluator')
cap('quantification.integrate',obj({'start_s':number('s',0),'stop_s':number('s',0),'calibration_s_m_s_per_mol_m3':number('S*s*m^2/mol',1e-20)}),obj({'area_s_s_m':number('S*s/m',0),'concentration_mol_m3':number('mol/m^3',0),'evidence':TEXT}),['conductivity_cell'],'窗口积分/调用者校准系数定量；不读取私有组分真值','analysis','agent',dict(start_s=0.,stop_s=60.,calibration_s_m_s_per_mol_m3=.02))
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['housing'],'未实现真实硬件或酸碱配位平衡')
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='display_digits',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','宿主有限流体边界已配置','setpoint',c='housing')
slot('power.enabled',BOOL,'1','逻辑供电目标','setpoint',c='power_inlet',w=('power.set','enabled'))
slot('pump.enabled',BOOL,'1','泵运行目标','setpoint',c='pump_drive',w=('pump.set','enabled'))
slot('pump.target_m3_s',nullable(number('m^3/s',0)),'m^3/s','液体实际体积流量目标','setpoint',c='pump_drive')
slot('pump.flow_m3_s',number('m^3/s',0),'m^3/s','最近宿主步的液体实际平均流量')
slot('pump.pressure_pa',number('Pa',0),'Pa','阻力乘流量的泵出口表压真值','process_truth','evaluator','pressure_sensor')
slot('pump.indicated_pa',number('Pa',0),'Pa','理想化泵出口表压观测，零参考环境压力')
slot('method.configured',BOOL,'1','有效方法已设定','setpoint')
slot('column.target_k',nullable(number('K',303.15,353.15)),'K','柱温目标','setpoint',c='column_compartment')
slot('column.temperature_k',nullable(number('K')),'K','单节点柱温过程真值','process_truth','evaluator','column_compartment')
slot('column.indicated_k',nullable(number('K')),'K','理想化柱温显示')
slot('eluent.target_mol_m3',nullable(number('mol/m^3',.1,100)),'mol/m^3','淋洗液浓度目标；1mM=1mol/m3','setpoint',c='eluent_generator')
slot('suppressor.current_a',nullable(number('A',0)),'A','抑制电流目标','setpoint',c='suppressor')
for sid,c,l in [('door.angle_rad','main_door',110),('display_door.angle_rad','display_door',95)]:slot(sid,number('rad',0,math.radians(l)),'rad','右铰链绕+Z角度，零为闭合','scene',c=c,w=(sid.replace('.angle_rad','.set'),'angle_rad'))
slot('sample.loaded',BOOL,'1','外部样液连接状态',c='sample_port')
slot('sample.identity',TEXT,'1','样品标签真值','process_truth','evaluator','sample_port')
slot('valve.injected',BOOL,'1','本轮已完成有限体积进样，非阀轴实测角度','derived',c='injection_valve')
slot('acquisition.phase',PHASE,'1','异步采集状态')
slot('acquisition.elapsed_s',number('s',0),'s','已采集时间')
slot('detector.conductivity_s_m',nullable(number('S/m',0)),'S/m','最近窗口的理想抑制电导，298.15K检测池参考')

BASE_CAPS=list(CAPS);BASE_SLOTS=list(SLOTS)
# ---- mobile app (front panel) device model: the direct-control functions of the Integrion HPIC Mobile App (operator manual ch.4,
# PDF p.97-126); every app control calls one of the commands below.  Units as everywhere in this runtime (SI); the app converts.
PSI=6894.757293;ML_MIN=1e-6/60;KCL_1MM_S_M=.0147;ALPHA_TRUE_PCT=1.7
PRIME_FLOWS=[1.,2.,3.,4.,5.,6.];RISE_TIMES=[0.,.01,.02,.05,.1,.2,.5,1.,2.,5.,10.];PLOT_TIMES=[1,2,5,10,15,30,60];PLOT_RANGES=[1.,10.,100.,1000.,15000.]
TTL_FUNCTIONS=['NONE','INJECT VALVE','AUTOZERO','PUMP ON','MARK'];TTL_TYPES=['NORMAL EDGE','INVERTED EDGE','NORMAL PULSE','INVERTED PULSE']
ANALOG_RANGES=[.01,.1,1.,10.,100.,1000.,15000.];LANGUAGES=['EN'];CELL_SOURCES=['column','gas','kcl_1mm'];INIT_S=10.
APP_DEFAULTS=dict(flow_unit='mL/min',pressure_unit='psi',high_limit_pa=6000*PSI,low_limit_pa=0.,prime_flow_m3_s=6*ML_MIN,degas_enabled=True,rise_time_s=2.,temp_comp_pct_per_k=1.7,plot_cd=True,plot_pressure=False,plot_time_min=1,plot_range_us=None,analog_mode='NORMAL',analog_polarity='NORMAL',analog_offset_pct=0.,analog_range_us=10.,ttl_out_v=[5,5],relay=['OPEN','OPEN'],relay_link=[False,False],ttl_function=['NONE']*4,ttl_type=['NORMAL EDGE']*4,language='EN')
CONSUMABLES=[dict(id='egc',part='075778',name='Dionex EGC 500 KOH',detected_by='Wired',expires='2028-09-30'),dict(id='crtc',part='088662',name='Dionex CR-ATC 600',detected_by='Wired'),dict(id='suppressor',part='088666',name='Dionex ADRS 600 (4 mm)',detected_by='Wired'),dict(id='guard',part='064149',name='Dionex IonPac AG18 (4 mm)',detected_by='RFID'),dict(id='separator',part='060549',name='Dionex IonPac AS18 (4 mm)',detected_by='RFID')]
EXTRA_ERRORS=['FLOW_OFF','PRESSURE_HIGH','LIMIT_RANGE','TEMPERATURE_RANGE','CONSUMABLE_DISABLED','CALIBRATION_FAILED','NO_METHOD','UNKNOWN_CONSUMABLE','NOTHING_TO_DISMISS']
def opt(p):return dict(type='object',properties=p,required=[],additionalProperties=False)
UNIT_P=dict(flow_unit=enum(['mL/min','uL/min']),pressure_unit=enum(['psi','bar','MPa']),high_limit_pa=number('Pa',0,6000*PSI),low_limit_pa=number('Pa',0,6000*PSI),prime_flow_m3_s=number('m^3/s',ML_MIN,6*ML_MIN),degas_enabled=BOOL)
cap('method.update',opt({'flow_m3_s':number('m^3/s',1e-9/60,1e-5/60),'eluent_mol_m3':number('mol/m^3',.1,100),'suppression_current_a':number('A',0)}),obj({'configured':BOOL}),['pump_drive','eluent_generator','suppressor'],'移动App直接控制：改当前方法的流量、淋洗液浓度或抑制电流（同method.configure的RFIC/压力/抑制电流检查；泵运行时也可改，采集运行中拒绝）',ex={'flow_m3_s':1e-6/60},effects=['pump.target_m3_s','eluent.target_mol_m3','suppressor.current_a'])
cap('pump.configure',opt(UNIT_P),obj({'configured':BOOL}),['pump_drive'],'泵页设置：流量/压力显示单位、压力上下限（上限≥下限+0.34MPa；超限停泵）、灌注流量、真空脱气泵（可选柱塞密封清洗泵未安装：RFIC配置只泵去离子水，安装说明附录D）',ex={'pressure_unit':'MPa'},effects=['app.settings'])
cap('pump.prime',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['pump_drive','priming_valve'],'灌注开/关：按灌注流量从淋洗液瓶抽液；灌注阀打开时经灌注废液管排出，关闭时流经系统',ex={'enabled':True},effects=['pump.priming'])
cap('priming_valve.set',obj({'open':BOOL}),obj({'open':BOOL}),['priming_valve'],'次泵头灌注阀：逆时针半圈打开/顺时针关闭；系统压力≥0.7MPa时拒绝',ex={'open':True},effects=['priming_valve.open'])
cap('valve.set',obj({'position':enum(['load','inject'])}),obj({'position':enum(['load','inject'])}),['injection_valve'],'进样阀Load/Inject；切到Inject时若定量环已装样且无残留则把方法进样体积送入色谱柱（与acquisition.start同一进样路径，不开始采集）',ex={'position':'load'},effects=['valve.position','valve.elapsed_s','valve.injected'])
cap('detector.configure',opt({'rise_time_s':{'type':'number','x-unit':'s','enum':RISE_TIMES},'temp_comp_pct_per_k':number('%/K',0,3)}),obj({'configured':BOOL}),['conductivity_cell'],'电导检测器：数据上升时间（显示/曲线/模拟输出滤波）与温度补偿系数（0–3%/°C）',ex={'rise_time_s':1.},effects=['detector.rise_time_s','detector.temp_comp_pct_per_k'])
cap('detector.autozero',obj({}),obj({'zero_us':number('uS/cm')}),['conductivity_cell'],'AUTOZERO：把当前总电导记为背景，偏移读数归零',effects=['detector.zero_us','detector.offset_us'])
cap('electrolytics.set',obj({'device':enum(['egc','crtc','suppressor']),'enabled':BOOL}),obj({'enabled':BOOL}),['eluent_generator','trap_column','suppressor'],'EGC/CR-TC/抑制器电源；泵流量关闭时不能打开，且泵停流时自动关闭',ex={'device':'egc','enabled':True},effects=['electrolytics.egc_on','electrolytics.crtc_on','electrolytics.suppressor_on','electrolytics.crtc_v'])
cap('temperature.set',dict(opt({'zone':enum(['compartment','column','cd_cell']),'enabled':BOOL,'setpoint_k':number('K',273.15,353.15)}),required=['zone']),obj({'zone':TEXT}),['detector_compartment','column_compartment','conductivity_cell'],'温控区开关与设定：检测器舱15–40°C（不低于环境−20°C）、柱温箱30–80°C（不低于环境+5°C，写入当前方法）、电导池加热（环境+7°C至60°C）',ex={'zone':'cd_cell','setpoint_k':308.15},effects=['temperature.compartment_on','temperature.compartment_target_k','temperature.column_heater_on','column.target_k','temperature.cd_cell_on','temperature.cd_cell_target_k'])
cap('plot.configure',opt({'cd':BOOL,'pressure':BOOL,'time_min':{'type':'integer','x-unit':'min','enum':PLOT_TIMES},'range_us':nullable({'type':'number','x-unit':'uS/cm','enum':PLOT_RANGES})}),obj({'configured':BOOL}),['display_digits'],'曲线页：选择电导/压力信号、时间轴长度与电导量程（None=AUTO）',ex={'pressure':True},effects=['app.settings'])
cap('analog.configure',opt({'mode':enum(['NORMAL','ZERO','FULL']),'polarity':enum(['NORMAL','INVERTED']),'offset_pct':number('%',0,100),'range_us':{'type':'number','x-unit':'uS/cm','enum':ANALOG_RANGES}}),obj({'configured':BOOL}),['rear_electronics'],'模拟输出1：NORMAL/ZERO/FULL、极性、偏置%、量程µS/1V',ex={'mode':'ZERO'},effects=['app.settings','analog.output_v'])
cap('io.configure_outputs',opt({'ttl_1_v':{'type':'integer','x-unit':'V','enum':[0,5]},'ttl_2_v':{'type':'integer','x-unit':'V','enum':[0,5]},'relay_1':enum(['OPEN','CLOSE']),'relay_2':enum(['OPEN','CLOSE']),'relay_1_link':BOOL,'relay_2_link':BOOL}),obj({'configured':BOOL}),['rear_electronics'],'TTL输出1/2（0V=开、5V=关）与继电器1/2（OPEN/CLOSE；LINK TO PUMP FLOW时随泵运转且流量>0自动闭合）；序列运行中也可改',ex={'relay_1':'CLOSE'},effects=['io.outputs'])
cap('io.configure_ttl_input',dict(opt({'input':{'type':'integer','x-unit':'1','minimum':1,'maximum':4},'function':enum(TTL_FUNCTIONS),'control_type':enum(TTL_TYPES)}),required=['input']),obj({'configured':BOOL}),['rear_electronics'],'TTL输入1–4的功能（INJECT VALVE/AUTOZERO/PUMP ON/MARK）与控制类型（NORMAL/INVERTED EDGE/PULSE）',ex={'input':1,'function':'INJECT VALVE'},effects=['app.settings'])
cap('io.ttl_input',obj({'input':{'type':'integer','x-unit':'1','minimum':1,'maximum':4},'level_v':{'type':'integer','x-unit':'V','enum':[0,5]}}),obj({'actions':arr(TEXT)}),['rear_electronics'],'评测/宿主：外部控制设备驱动后面板TTL输入电平；按所配功能与控制类型触发（边沿：下降沿开/上升沿关；脉冲：有效沿切换）','device_command','evaluator',{'input':1,'level_v':0},['io.ttl_inputs_v','valve.position','pump.enabled','detector.zero_us','analog.output_v'])
cap('consumables.select',obj({'id':TEXT,'checked':BOOL}),obj({'checked':BOOL}),['display_digits'],'耗材页勾选/取消一个RFID耗材（有线耗材总是勾选）；Rescan按钮变为Approve',ex={'id':'separator','checked':False},effects=['consumables'])
cap('consumables.approve',obj({}),obj({'approved':BOOL}),['display_digits'],'Approve：按勾选启用/停用RFID耗材（停用后不再累计其淋洗液体积与进样次数）',effects=['consumables'])
cap('consumables.rescan',obj({}),obj({'detected':{'type':'integer','x-unit':'1','minimum':0}}),['display_digits'],'Rescan：手动重扫RFID耗材（仿真中全部在位），放弃未批准的勾选',effects=['consumables'])
cap('cd_cell.connect',obj({'source':enum(CELL_SOURCES)}),obj({'source':TEXT}),['conductivity_cell'],'改接电导池入口（校准用）：column=正常柱后流路；gas=He/N2吹扫（0µS）；kcl_1mm=1.0mM KCl泵出液（147.00µS/cm，25°C）',ex={'source':'gas'},effects=['detector.cell_source'])
cap('sensor.calibrate_cd_offset',obj({}),obj({'offset_us':number('uS/cm')}),['conductivity_cell'],'校准页START OFFSET CALIBRATION：取当前读数为零点（读数须在±50µS内）',effects=['calibration.cd'])
cap('sensor.calibrate_cd_slope',obj({}),obj({'slope':number('1',0)}),['conductivity_cell'],'校准页START SLOPE CALIBRATION：以1.0mM KCl的147.00µS/cm求斜率（须先零点；扣零后读数须在117.6–176.4µS）',effects=['calibration.cd'])
cap('sensor.reset_calibration',obj({}),obj({'reset':BOOL}),['conductivity_cell'],'清除用户电导池校准，恢复出厂（零点0、斜率1）',effects=['calibration.cd'])
cap('sensor.configure_error',obj({'offset_us':number('uS/cm',-1000,1000),'gain':number('1',-.5,.5)}),obj({'configured':BOOL}),['conductivity_cell'],'评测：电导池测量误差（偏置µS/cm、增益相对误差）；默认0为理想传感器','configuration','evaluator',{'offset_us':0.,'gain':0.},['sensor.cd_error'])
cap('alerts.dismiss',obj({'index':{'type':'integer','x-unit':'1','minimum':-1}}),obj({'remaining':{'type':'integer','x-unit':'1','minimum':0}}),['display_digits'],'错误列表中删除一条（index=-1全部删除）；列表空时警告图标与红色报警灯熄灭',ex={'index':-1},effects=['alerts'])
cap('sequence.interrupt',obj({}),obj({'phase':PHASE}),['display_digits','pump_drive'],'App在序列运行中改设置时选择停止序列：同acquisition.stop并记录“Chromeleon run stopped from tablet”',effects=['acquisition.phase','pump.enabled','alerts'])
cap('display.configure',obj({'language':enum(LANGUAGES)}),obj({'language':TEXT}),['display_digits'],'工具栏语言选择（手册未列出其他语言包，仅English）',ex={'language':'EN'},effects=['app.settings'])
# Troubleshooting error messages (operator's manual Table 14 p.110-112, troubleshooting p.113-137): message, default severity, page.  Messages with a
# cause in the model come from it (Upper pressure limit exceeded, Pressure fallen below lower limit, Chromeleon run stopped from tablet, Column door
# opened).  The others are evaluator-only fault.inject {code} / fault.clear {code} (the remedy of the troubleshooting page); the error list, the
# alert LED and the toolbar warning icon show them; dismissing a message (alerts.dismiss) does not remove its cause.  Behaviour by severity
# (Table 13): Warning = message only; Abort (and Serious) = the running sequence is aborted; a pump / EG / CR-TC / suppressor message also turns
# that device off (pump faults stop the flow), and the Ready Check refuses a sequence start (NOT_READY) and the device's start until cleared.
# Not injectable: the ED cell / ED detector and EPM messages (no electrochemical detector or EPM in this RFIC configuration), the auxiliary power
# supply messages (no auxiliary device), and the Chromeleon software messages (method names, user-adjustable functions, pump linking).
DOC_ERRORS=[('2-channel board memory reset to defaults','Warning',113),('3-channel board memory reset to defaults','Warning',113),
 ('Abnormal drive current for x.x seconds','Warning',114),('Ambient temperature sensor error','Abort',114),('Camshaft index too early','Abort',115),
 ('Camshaft index too late','Abort',115),('Camshaft sensor always alight','Abort',116),('Camshaft sensor missing or dark','Abort',116),
 ('CD cell memory reset to defaults','Warning',116),('CD cell temperature is lower than expected','Abort',117),('CD cell temperature over safe limit','Abort',116),
 ('Column heater exceeds safe temperature limit','Abort',117),('Column heater memory reset to defaults','Warning',118),
 ('Column heater temperature calibration failed','Abort',118),('Column heater temperature is lower than expected','Abort',118),
 ('Column oven fan disconnected','Abort',118),('Column temperature sensor error','Abort',118),('CR-TC cartridge disconnected','Warning',119),
 ('CR-TC consumable inventory cable memory reset to defaults','Warning',122),('CR-TC invalid serial number','Abort',119),('CR-TC over current','Abort',119),
 ('CR-TC short circuit','Abort',120),('Detector compartment door opened','Warning',121),('Detector compartment exceeds safe temperature limit','Abort',121),
 ('Detector compartment memory reset to defaults','Warning',121),('Detector compartment temperature calibration failed','Abort',121),
 ('Detector compartment temperature is lower than expected','Abort',122),('Detector compartment temperature sensor error','Abort',122),
 ('EG consumable inventory cable access error','Abort',125),('EG consumable inventory cable memory reset to defaults','Warning',125),
 ('EG cartridge disconnected','Abort',125),('EG invalid activation date','Abort',126),('EG invalid concentration','Abort',126),('EG invalid flow rate','Abort',126),
 ('EG invalid ion count','Abort',126),('EG invalid serial number','Abort',127),('EG over current','Serious',127),('EGC over power','Abort',127),
 ('EGC over voltage','Abort',127),('EG wrong cartridge type','Abort',128),('Excessive drive current. Camshaft x.x.','Abort',128),
 ('Flash memory reset to defaults','Warning',128),('High-pressure valve busy','Warning',128),('High-pressure valve failed to reach commanded position','Warning',129),
 ('Incompatible CD detector installed','Abort',129),('Insufficient CD cell data for this request','Abort',116),('Internal hardware failure','Abort',129),
 ('Invalid flow value','Abort',129),('Leak detected. Flow stopped.','Abort',130),('Leak sensor wet','Warning',130),('Load/Inject valve busy','Warning',130),
 ('Load/Inject valve failed to reach commanded position','Abort',130),('Motor malfunction','Abort',131),('Motor position error. The motor is overloaded.','Abort',131),
 ('No RFID labels found','Warning',131),('RFID label 1-label 10 read/write failed: check label orientation','Warning',132),
 ('RFID label read failed: check label orientation','Warning',132),('RFID label write failed: check label orientation','Warning',133),
 ('RFID product type not recognized','Warning',133),('Right-hand pump block carryover pressure is too high','Abort',133),('Suppressor not installed','Abort',133),
 ('Suppressor over current','Abort',134),('Suppressor over power','Abort',134),('Suppressor over voltage','Abort',134),('Suppressor stopped due to flow rate','Warning',135),
 ('Suppressor type has changed','Warning',135),('The maximum purge pressure was exceeded','Abort',135),
 ('The pressure in the right-hand working cylinder exceeded the safety limit','Abort',135),('The system pressure exceeded the safety limit','Abort',135),
 ('Wrong device connected to connector A','Warning',137)]
def _err_code(m):return re.sub(r'[^a-z0-9]+','_',m.lower()).strip('_')
def _err_group(m):
 if m.startswith(('EG ','EGC ')):return 'egc'
 if m.startswith('CR-TC'):return 'crtc'
 if m.startswith('Suppressor') and 'stopped due to flow' not in m:return 'suppressor'
 if any(x in m for x in ('Camshaft','drive current','Leak detected','Motor','flow value','pump block','working cylinder','purge pressure','system pressure')):return 'pump'
 return None
DOC_ERROR={_err_code(m):dict(message=m,severity=s,page=p,group=_err_group(m)) for m,s,p in DOC_ERRORS}
cap('fault.inject',obj({'code':enum(sorted(DOC_ERROR))}),obj({'latched':arr(TEXT)}),['display_digits'],'评测者注入手册表14的错误消息（code为消息文字的小写下划线形式）：写入App错误列表与报警灯；Abort/Serious中止运行中的序列，'
    '泵/EG/CR-TC/抑制器类同时关闭该设备，清除前Ready Check拒绝开始；Warning只记消息','simulation','evaluator',{'code':'leak_detected_flow_stopped'},
    ['alerts','pump.enabled','acquisition.phase','electrolytics.egc_on','electrolytics.crtc_on','electrolytics.suppressor_on'])
cap('fault.clear',obj({'code':enum(sorted(DOC_ERROR))}),obj({'latched':arr(TEXT)}),['display_digits'],'评测者清除错误原因（按排障页处理后）；列表中的消息仍需删除','simulation','evaluator',
    {'code':'leak_detected_flow_stopped'},[])
APP_CAPS=[k for k in CAPS if k not in BASE_CAPS]
RUN_GUARDED={'acquisition.stop','sequence.interrupt','pump.set'}   # ThermoDionexintegrionRuntime._guard
slot('app.settings',{'type':'object'},'1','移动App设备设置（单位、压力限、灌注流量、脱气泵、曲线、模拟输出、TTL/继电器、语言）','setpoint',c='display_digits')
slot('pump.priming',BOOL,'1','灌注运行中','setpoint',c='pump_drive')
slot('priming_valve.open',BOOL,'1','灌注阀打开','scene',c='priming_valve')
slot('valve.position',enum(['load','inject']),'1','进样阀位置','setpoint',c='injection_valve')
slot('valve.elapsed_s',number('s',0),'s','距上次切阀/进样的时间',c='injection_valve')
for k,c in [('egc_on','eluent_generator'),('crtc_on','trap_column'),('suppressor_on','suppressor')]:slot('electrolytics.'+k,BOOL,'1',{'egc_on':'EGC电源','crtc_on':'CR-TC电源','suppressor_on':'抑制器电源'}[k],'setpoint',c=c)
slot('electrolytics.crtc_v',number('V',0),'V','CR-TC施加电压（开时24.00V，假设）',c='trap_column')
slot('egc.life_pct',number('%',0,100),'%','EGC剩余离子容量（新柱100%，消耗未建模）',c='eluent_generator')
for z,c,zh in [('compartment','detector_compartment','检测器舱'),('cd_cell','conductivity_cell','电导池加热')]:
 slot(f'temperature.{z}_on',BOOL,'1',zh+'开关','setpoint',c=c);slot(f'temperature.{z}_target_k',number('K'),'K',zh+'设定','setpoint',c=c);slot(f'temperature.{z}_k',nullable(number('K')),'K',zh+'理想化温度显示',c=c)
slot('temperature.column_heater_on',BOOL,'1','柱温箱加热开关','setpoint',c='column_compartment')
slot('detector.total_us',nullable(number('uS/cm')),'uS/cm','App显示的总电导（温度补偿、校准、上升时间滤波后，未扣背景）',c='conductivity_cell')
slot('detector.offset_us',nullable(number('uS/cm')),'uS/cm','App显示的偏移电导（总电导减AUTOZERO背景）',c='conductivity_cell')
slot('detector.zero_us',number('uS/cm'),'uS/cm','AUTOZERO记下的背景电导','setpoint',c='conductivity_cell')
slot('detector.rise_time_s',number('s',0,10),'s','数据上升时间','setpoint',c='conductivity_cell')
slot('detector.temp_comp_pct_per_k',number('%/K',0,3),'%/K','温度补偿系数','setpoint',c='conductivity_cell')
slot('detector.cell_source',enum(CELL_SOURCES),'1','电导池入口接法（校准时改接）','scene',c='conductivity_cell')
slot('calibration.cd',{'type':'object'},'1','电导池用户校准：零点、斜率、状态','setpoint',c='conductivity_cell')
slot('sensor.cd_error',{'type':'object'},'1','评测注入的电导池测量误差（默认0）','setpoint','evaluator','conductivity_cell')
slot('degas.vacuum_ok',BOOL,'1','脱气真空正常（启用且通电）',c='pump_drive')
slot('analog.output_v',number('V'),'V','模拟输出1电压（满量程1V）',c='rear_electronics')
slot('io.outputs',{'type':'object'},'1','TTL输出电平与继电器状态（含随泵联动）',c='rear_electronics')
slot('io.ttl_inputs_v',arr({'type':'integer','x-unit':'V'},4),'V','TTL输入1–4电平',c='rear_electronics')
slot('consumables',arr({'type':'object'}),'1','耗材表（有线/RFID检测、勾选、启用、累计淋洗液体积与进样次数）',c='display_digits')
slot('alerts',arr({'type':'object'}),'1','App错误列表（时间、消息、严重度）',c='display_digits')
def _panel_engine():
 try:
  from . import _panel_ui as engine                                    # imported as asset_runtime.thermo_dionexintegrion (tests, bridges)
  return engine
 except ImportError:                                                    # loaded from its file path
  import sys
  if '_chemsim_panel_ui' not in sys.modules:
   spec=importlib.util.spec_from_file_location('_chemsim_panel_ui',Path(__file__).with_name('_panel_ui.py'));sys.modules['_chemsim_panel_ui']=importlib.util.module_from_spec(spec);spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
  return sys.modules['_chemsim_panel_ui']
_panel_ui=_panel_engine();ASSET_DIR=ROOT/'assets/expansion_batch03/t3_analytical'/SLUG;ASSET_VERSION='1.3.0'
PANEL_SPEC=None if os.environ.get('INTEGRION_PANEL_BUILD') else _panel_ui.load_spec(ASSET_DIR/'panel_ui.json',capabilities=set(CAPS))
PANEL_CAPS=_panel_ui.capability_definitions(PANEL_SPEC) if PANEL_SPEC else {}
_PANEL_EFFECTS=list(dict.fromkeys(e for c in (_panel_ui.spec_capabilities(PANEL_SPEC) if PANEL_SPEC else []) for e in CAPS[c][7]))
for _k,_d in PANEL_CAPS.items():cap(_k,_d['input_schema'],_d['output_schema'],_d['component_ids'],_d['function_zh'],_d['kind'],_d['visibility'],_d['example_arguments'],[] if _k in ['panel.capture','panel.frame'] else _PANEL_EFFECTS)
class _Panel(_panel_ui.PanelUI):
 """The panel's UI state belongs to the instrument state: two panels are equal when their UI states are (state snapshots compare runtimes)."""
 def __eq__(self,o):return isinstance(o,_panel_ui.PanelUI) and self.ui==o.ui
 __hash__=None

class ThermoDionexintegrionRuntime:
 def __init__(self,instance_id='integrion_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.pump=False;self.angle=0.;self.display_angle=0.;self.plant=None;self.method=None;self.sample=None;self.temperature=None;self.reservoir=0.;self.waste=0.;self.sample_loaded=0.;self.sample_returned=0.;self.flow=0.;self.pressure=0.;self.bands=[];self.phase='idle';self.elapsed=0.;self.points=[];self.signal=None;self.operation_id=None;self.serial=0;self.records={};self.templates=json.loads((ROOT/TEMPLATE_REL).read_text())['substances'];self._principals={};self._requests={};self._owners={};self._app_init()
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ['agent','evaluator','debug'] or principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return ThermoDionexintegrionSession(self,principal_id)
 def describe(self):return export_thermo_dionexintegrion_contract_metadata()
 def read(self,visibility='agent'):
  if visibility not in ['agent','evaluator','debug']:raise ActionRejected('ACCESS_DENIED')
  m=self.method or {};v={'plant.configured':bool(self.plant),'power.enabled':self.powered,'pump.enabled':self.pump,'pump.target_m3_s':m.get('flow_m3_s'),'pump.flow_m3_s':self.flow,'pump.pressure_pa':self.pressure,'pump.indicated_pa':self.pressure,'method.configured':bool(self.method),'column.target_k':m.get('column_k'),'column.temperature_k':self.temperature,'column.indicated_k':self.temperature,'eluent.target_mol_m3':m.get('eluent_mol_m3'),'suppressor.current_a':m.get('suppression_current_a'),'door.angle_rad':self.angle,'display_door.angle_rad':self.display_angle,'sample.loaded':bool(self.sample),'sample.identity':self.sample['sample_id'] if self.sample else '','valve.injected':bool(self.bands),'acquisition.phase':self.phase,'acquisition.elapsed_s':self.elapsed,'detector.conductivity_s_m':self.signal};v.update(self._app_read())
  return copy.deepcopy({k:x for k,x in v.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _result(self):return dict(phase=self.phase,complete=self.phase=='succeeded',points=copy.deepcopy(self.points),elapsed_s=self.elapsed,template_source='USGS PHREEQC aqueous diffusion data; substance identity withheld',evidence='derived conductivity + synthetic chromatogram retention and lognormal bandshape',warnings=['298.15K ideal limiting conductivity; no activity/speciation or calibrated noise','Host supplied retention for this method; not manufacturer column calibration','Cancelled/finite-fluid flow exhaustion retains partial fluid/species transfer'])
 def _ledger(self):
  remaining=self.sample['remaining'] if self.sample else 0.;inj=sum(x['n'] for x in self.bands);elu=sum(x['eluted'] for x in self.bands);col=inj-elu;initial=self.plant['reservoir_m3'] if self.plant else 0.;return dict(reservoir_m3=self.reservoir,waste_m3=self.waste,sample_remaining_m3=remaining,volume_residual_m3=initial+self.sample_loaded-self.reservoir-self.waste-remaining-self.sample_returned,species_residual_mol=inj-col-elu,injected_mol=inj,column_mol=max(0.,col),eluted_mol=elu,sample_id=self.sample['sample_id'] if self.sample else '')
 def invoke(self,capability_id,**arguments):
  if capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability_id][0],arguments)
  if capability_id in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  before=copy.deepcopy({k:v for k,v in self.__dict__.items() if k!='panel'})
  try:
   r=self._invoke(capability_id,copy.deepcopy(arguments));self._app_sync();validate(CAPS[capability_id][1],r)
   if CAPS[capability_id][4] not in ['query','analysis']:self.revision+=1
   return copy.deepcopy(r)
  except Exception:p=self.__dict__.get('panel');self.__dict__.clear();self.__dict__.update(before);self.panel=p;raise
 def _invoke(self,k,a):
  if k=='process.measure':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='template.read':
   t=self.templates.get(a['substance_id'])
   if t is None:raise ActionRejected('UNKNOWN_REFERENCE')
   return dict(substance_id=t['substance_id'],diffusion_m2_s=t['diffusion_m2_s'],molar_conductivity_s_m2_mol=t['molar_conductivity_S_m2_mol'],charge=t['charge'],template_source=t['source'],evidence=t['evidence'])
  if k=='quantification.integrate':
   if a['stop_s']<=a['start_s'] or not self.points or a['stop_s']>self.elapsed+1e-10:raise ActionRejected('INVALID_WINDOW')
   area=sum(p['conductivity_s_m']*max(0.,min(p['time_s'],a['stop_s'])-max(p['time_s']-p['window_s'],a['start_s'])) for p in self.points);return dict(area_s_s_m=area,concentration_mol_m3=area/a['calibration_s_m_s_per_mol_m3'],evidence='derived from observed window integral and caller calibration; no hidden species truth')
  if k=='plant.configure':
   if self.plant or self.powered or not a['accept_synthetic_retention']:raise ActionRejected('INITIALIZATION_ONLY')
   self.plant=a;self.reservoir=a['reservoir_m3'];self.temperature=a['ambient_k']
   for z in self.zones.values():z['k']=a['ambient_k']
   return {'configured':True}
  # Passive door manipulation and power toggles are usable before plant setup.
  if k in ['door.set','display_door.set']:
   # The doors open by hand at any time (final sync 2026-10-19: physical actions are not refused); opening the column compartment door during a
   # run logs the documented warning (Table 14 'Column door opened', p.117), the run continues.
   if k=='door.set':
    if a['angle_rad']>1e-6 and self.angle<=1e-6 and self.phase=='running':self._alert('Column door opened','Warning')
    self.angle=a['angle_rad']
   else:self.display_angle=a['angle_rad']
   return a
  if k=='power.set':
   if a['enabled'] and not self.powered:self.power_t=self.time_s;self.power_epoch+=1
   self.powered=a['enabled']
   if not self.powered:
    self.pump=False
    if self.phase=='running':self.phase='cancelled';self._save_record()
   return a
  if k in PANEL_CAPS:return self._panel_cmd(k,a)
  if k in APP_CAPS:return self._app_invoke(k,a)
  if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
  if self.phase=='running' and k not in ['acquisition.stop','pump.set']:raise ActionRejected('BUSY')
  if k=='method.configure':
   if self.pump or self.sample or self._ledger()['column_mol']>1e-14:raise ActionRejected('BUSY')
   self._check_method(a);self.method=a;return {'configured':True}
  if k=='sample.load':
   if self.sample or not self.method:raise ActionRejected('SAMPLE_METHOD_REQUIRED')
   if not a['sample_id'] or not a['accept_unresolved_speciation']:raise ActionRejected('PREPARATION_REQUIRED')
   ids=[x['substance_id'] for x in a['solutes']]
   if len(set(ids))!=len(ids):raise ActionRejected('DUPLICATE_SOLUTE')
   for x in a['solutes']:
    if x['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
    z=self.templates[x['substance_id']]['charge']
    if z and (z<0)!=(self.method['mode']=='anion'):raise ActionRejected('COLUMN_POLARITY')
   self.sample=dict(a,remaining=a['volume_m3']);self.sample_loaded+=a['volume_m3'];return {'loaded':True}
  if k=='sample.unload':
   if not self.sample:raise ActionRejected('NO_SAMPLE')
   v=self.sample['remaining'];self.sample_returned+=v;self.sample=None;return {'returned_m3':v}
  if k=='pump.set':
   if a['enabled'] and self._latched('pump'):raise ActionRejected('NOT_READY')
   if a['enabled'] and (not self.powered or not self.method or self.angle>1e-6 or self.display_angle>1e-6 or self.reservoir<=0 or self.waste>=self.plant['waste_capacity_m3']):raise ActionRejected('NOT_READY')
   self.pump=a['enabled']
   if not self.pump and self.phase=='running':self.phase='cancelled';self._save_record()
   return a
  if k=='acquisition.start':
   if self._latched():raise ActionRejected('NOT_READY')   # Ready Check: an Abort message not yet remedied (Table 13)
   if not self.powered or not self.pump or not self.method or not self.sample or abs(self.temperature-self.method['column_k'])>.5:raise ActionRejected('NOT_READY')
   vol=self.method['injection_m3']
   if self.sample['remaining']<vol or self._ledger()['column_mol']>1e-14:raise ActionRejected('SAMPLE_OR_CARRYOVER')
   if self.waste+vol>self.plant['waste_capacity_m3']:raise ActionRejected('WASTE_FULL')
   if self.method['rfic']:self.elec=dict(egc=True,crtc=True,suppressor=True)   # the Chromeleon run switches them on as its instrument method does (wired consumables)
   # Unmodeled plug solvent mixing is an instantaneous incompressible displacement into waste;
   # analyte amounts remain in bands and are conserved independently.
   self._inject(vol,True);self.serial+=1;self.operation_id=f'{self.instance_id}:op:{self.serial}';self.phase='running';self.elapsed=0.;self.points=[];self.signal=None;return dict(phase='running',operation_id=self.operation_id)
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self.phase='cancelled';self.pump=False;self._save_record();return {'phase':self.phase}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _save_record(self):
  if self.operation_id:self.records[self.operation_id]=self._result()
 def step(self,dt_s):
  validate(number('s',1e-12,1e6),dt_s)
  self.flow=0.;self.pressure=0.;signal=None;v_col=0.;was=self.phase
  if self.plant:
   target=self.method['column_k'] if self.powered and self.method and self.column_heater else self.plant['ambient_k'];self.temperature=target+(self.temperature-target)*math.exp(-dt_s/self.plant['thermal_tau_s'])
   prime=self.powered and self.priming and not self.pump
   if self.powered and (self.pump and self.method or prime):
    # Priming runs the pump at the priming flow; with the priming valve open the flow leaves through the priming waste line (no column flow).
    q=self.method['flow_m3_s'] if self.pump else self.app['prime_flow_m3_s'];through=not(prime and self.priming_valve_open);cap=self.plant['waste_capacity_m3']-self.waste;duration=min(dt_s,self.reservoir/q,max(0,cap)/q)
    if self.phase=='running':duration=min(duration,max(0,self.method['run_s']-self.elapsed))
    v=q*duration;self.reservoir=max(0.,self.reservoir-v);self.waste+=v;self.flow=v/dt_s;self.pressure=self.plant['hydraulic_resistance_pa_s_m3']*self.flow if through else 0.;signal=0.
    for band in self.bands if through and self._eluent_on() else []:   # no eluent (RFIC with the EGC off): the ions stay bound on the column
     volume=band['volume']+v;f=0. if volume<=0 else .5*(1+math.erf(math.log(volume/band['retention_volume_m3'])/(band['width_fraction']*math.sqrt(2))));new=band['n']*f;dn=max(0.,new-band['eluted']);band['eluted']=new;band['volume']=volume;t=self.templates[band['substance_id']];counter=self.templates['h_cation1' if t['charge']<0 else 'oh_anion1']['molar_conductivity_S_m2_mol'];lam=t['molar_conductivity_S_m2_mol']+abs(t['charge'])*counter
     if v>0:signal+=lam*dn/v
    v_col=v if through else 0.
    if self.phase=='running':
     if duration>0:self.elapsed+=duration;self.signal=self._reading_s_m(signal);self.points.append(dict(time_s=self.elapsed,conductivity_s_m=self.signal,window_s=duration))
     if self.elapsed>=self.method['run_s']-1e-10:self.phase='succeeded';self.pump=False
     elif duration<dt_s-1e-10:self.phase='failed';self.pump=False
     self._save_record()
    elif duration<dt_s-1e-10:self.pump=False;self.priming=False
   self._app_step(dt_s,signal if v_col>0 else None,v_col,was)
  self.time_s+=dt_s;self.revision+=1;return self.read('evaluator')

 # ---- mobile app model (front panel): device settings, electrolytic supplies, temperature zones, CD read-out chain, I/O, consumables, alerts
 def _app_init(self):
  self.app=copy.deepcopy(APP_DEFAULTS);self.priming=False;self.priming_valve_open=False;self.valve_pos='load';self.valve_t=0.;self.elec=dict(egc=False,crtc=False,suppressor=False);self.column_heater=True
  self.doc_faults=[]
  self.zones=dict(compartment=dict(on=True,target_k=288.15,k=None),cd_cell=dict(on=True,target_k=308.15,k=None));self.cell_source='column';self.ttl_in=[5,5,5,5];self.mark_until=-1.;self.alerts=[];self.trace=[];self.trace_t0=0.
  self.cd=dict(zero_us=0.,shown_us=None,live_s_m=0.,cal_offset_s_m=0.,cal_slope=1.,cal_state='factory',err_offset_us=0.,err_gain=0.);self.power_t=None;self.power_epoch=0
  self.consumables=[dict(c,serial=f'{151028019+5*i:09d}',lot=f'{1427187+i:08d}' if c['detected_by']=='RFID' else 'N/A',enabled=True,checked=True,eluent_ml=0.,injections=0) for i,c in enumerate(CONSUMABLES)]
  self.panel=_Panel(PANEL_SPEC,self) if PANEL_SPEC else None
 def _app_read(self):
  z,cd,a=self.zones,self.cd,self.app
  return {'app.settings':a,'pump.priming':self.priming,'priming_valve.open':self.priming_valve_open,'valve.position':self.valve_pos,'valve.elapsed_s':max(0.,self.time_s-self.valve_t),'electrolytics.egc_on':self.elec['egc'],'electrolytics.crtc_on':self.elec['crtc'],'electrolytics.suppressor_on':self.elec['suppressor'],'electrolytics.crtc_v':24. if self.elec['crtc'] else 0.,'egc.life_pct':100.,
   **{f'temperature.{n}_{x}':z[n][y] for n in z for x,y in [('on','on'),('target_k','target_k'),('k','k')]},'temperature.column_heater_on':self.column_heater,'detector.total_us':cd['shown_us'],'detector.offset_us':None if cd['shown_us'] is None else cd['shown_us']-cd['zero_us'],'detector.zero_us':cd['zero_us'],'detector.rise_time_s':a['rise_time_s'],'detector.temp_comp_pct_per_k':a['temp_comp_pct_per_k'],'detector.cell_source':self.cell_source,
   'calibration.cd':dict(offset_us=cd['cal_offset_s_m']*1e4,slope=cd['cal_slope'],state=cd['cal_state']),'sensor.cd_error':dict(offset_us=cd['err_offset_us'],gain=cd['err_gain']),'degas.vacuum_ok':bool(self.powered and a['degas_enabled']),'analog.output_v':self._analog_v(),'io.outputs':dict(ttl_v=list(a['ttl_out_v']),relay=self._relays()),'io.ttl_inputs_v':list(self.ttl_in),'consumables':self.consumables,'alerts':self.alerts}
 def _check_method(self,a):
  if a['column_k']<self.plant['ambient_k']+5:raise ActionRejected('COLUMN_RANGE')
  if a['rfic'] and not .1e-6/60<=a['flow_m3_s']<=3e-6/60:raise ActionRejected('RFIC_FLOW_RANGE')
  maxp=(5000 if a['rfic'] else 6000)*6894.757293
  if self.plant['hydraulic_resistance_pa_s_m3']*a['flow_m3_s']>maxp:raise ActionRejected('PRESSURE_LIMIT')
  if a['suppression_current_a']*self.plant['suppressor_efficiency']+1e-12<96485.33212*a['eluent_mol_m3']*a['flow_m3_s']:raise ActionRejected('INSUFFICIENT_SUPPRESSION_CURRENT')
 def _inject(self,vol,sequence):
  # Unmodeled plug solvent mixing is an instantaneous incompressible displacement into waste; analyte amounts remain in bands and are conserved.
  self.sample['remaining']-=vol;self.waste+=vol;self.bands += [dict(s,n=s['concentration_mol_m3']*vol,eluted=0.,volume=0.) for s in self.sample['solutes']];self.valve_pos='inject';self.valve_t=self.time_s
  if sequence:
   self.trace=[];self.trace_t0=self.time_s
   for c in self.consumables:c['injections']+=c['enabled'] and c['detected_by']=='RFID'
 def _consumable(self,cid):
  for c in self.consumables:
   if c['id']==cid:return c
  raise ActionRejected('UNKNOWN_CONSUMABLE')
 def _latched(self,group=None):
  """Abort / Serious messages not yet remedied (fault.clear): of one device group, or any (Ready Check)."""
  return [c for c in getattr(self,'doc_faults',[]) if group is None or DOC_ERROR[c]['group']==group]
 def _alert(self,text,severity):self.alerts=(self.alerts+[dict(time_s=self.time_s,text=text,severity=severity)])[-50:]
 def _eluent_on(self):
  """Eluent reaches the column: RFIC methods pump deionized water and need the EGC to generate the KOH eluent; other methods pump a prepared eluent."""
  return bool(self.method) and (not self.method['rfic'] or self.elec['egc'])
 def _cell_c(self):k=self.zones['cd_cell']['k'];return 25. if k is None else k-273.15
 def _background(self):
  # Unsuppressed KOH eluent (EGC on, suppressor off, anion mode): K+ and OH- limiting conductivities of the library; MSA (cation mode) is not in the library.
  m=self.method
  if not(self.elec['egc'] and not self.elec['suppressor'] and m and m['mode']=='anion'):return 0.
  return m['eluent_mol_m3']*(self.templates['k_cation1']['molar_conductivity_S_m2_mol']+self.templates['oh_anion1']['molar_conductivity_S_m2_mol'])
 def _reading_s_m(self,eluate,cal=True):
  """CD read-out chain (S/m): true cell conductivity at 25 C -> cell temperature (true coefficient 1.7 %/C) -> sensor error (default 0) ->
  temperature compensation (setting) -> user calibration (offset, slope).  With the defaults it returns its input unchanged."""
  k=0. if self.cell_source=='gas' else KCL_1MM_S_M if self.cell_source=='kcl_1mm' else eluate+self._background()
  cd=self.cd;d=self._cell_c()-25.;f_set=1+self.app['temp_comp_pct_per_k']/100*d;ratio=(1+ALPHA_TRUE_PCT/100*d)/f_set
  m=k*ratio*(1+cd['err_gain'])+cd['err_offset_us']*1e-4/f_set
  return (m-cd['cal_offset_s_m'])*cd['cal_slope'] if cal else m
 def _uncal_s_m(self):return self._reading_s_m(self.cd['live_s_m'],False)
 def _analog_v(self):
  a=self.app
  if not self.powered:return 0.
  if a['analog_mode']=='ZERO':v=0.
  elif a['analog_mode']=='FULL':v=1.
  else:
   r=0. if self.cd['shown_us'] is None else self.cd['shown_us']-self.cd['zero_us'];v=a['analog_offset_pct']/100+(1 if a['analog_polarity']=='NORMAL' else -1)*r/a['analog_range_us']
  return v+(.1 if self.time_s<self.mark_until-1e-12 else 0.)
 def _relays(self):
  flowing=bool(self.powered and self.pump and self.method and self.method['flow_m3_s']>0)
  return [('CLOSE' if flowing else 'OPEN') if self.app['relay_link'][i] else self.app['relay'][i] for i in range(2)]
 def _app_sync(self):
  # The EGC, CR-TC and suppressor are always turned off when the pump flow is off (manual p.114-115); flow on ends priming.
  if not(self.powered and self.pump):self.elec=dict(egc=False,crtc=False,suppressor=False)
  if not self.powered or self.pump:self.priming=False
  if not self.powered:self.cd['shown_us']=None
 def _stop_flow(self,text):
  self.pump=False;self.priming=False;self._alert(text,'Abort')
  if self.phase=='running':self.phase='failed';self._save_record()
 def _app_step(self,dt,signal,v_col,was):
  tau=self.plant['thermal_tau_s'];amb=self.plant['ambient_k']
  for z in self.zones.values():
   tgt=z['target_k'] if self.powered and z['on'] else amb;z['k']=tgt+((z['k'] if z['k'] is not None else amb)-tgt)*math.exp(-dt/tau)
  if (self.pump or self.priming) and self.powered:
   if self.pressure>self.app['high_limit_pa']+1e-6:self._stop_flow('Upper pressure limit exceeded')
   elif self.pump and self.flow>0 and self.pressure<self.app['low_limit_pa']-1e-6:self._stop_flow('Pressure fallen below lower limit')
  if signal is not None:self.cd['live_s_m']=signal
  for c in self.consumables:
   if c['enabled'] and c['detected_by']=='RFID':c['eluent_ml']+=v_col*1e6
  self._app_sync()
  if self.powered:
   total=self._reading_s_m(self.cd['live_s_m'])*1e4;rise=self.app['rise_time_s'];old=self.cd['shown_us']
   self.cd['shown_us']=total if old is None or rise<=0 else old+(total-old)*(1-math.exp(-dt*2.2/rise))
   if (was=='running')!=(self.phase=='running'):self.trace=[];self.trace_t0=self.time_s+dt
   self.trace=[x for x in self.trace if self.time_s+dt-self.trace_t0-x[0]<=3600.]+[[self.time_s+dt-self.trace_t0,self.cd['shown_us']-self.cd['zero_us'],self.pressure]]
 def _app_invoke(self,k,a):
  if k=='sensor.configure_error':self.cd.update(err_offset_us=a['offset_us'],err_gain=a['gain']);return {'configured':True}
  if k in ('fault.inject','fault.clear'):
   code=a['code'];d=DOC_ERROR[code]
   if k=='fault.clear':self.doc_faults=[x for x in self.doc_faults if x!=code];return {'latched':list(self.doc_faults)}
   self._alert(d['message'],d['severity'])
   if d['severity']!='Warning':
    if code not in self.doc_faults:self.doc_faults.append(code)
    if d['group']=='pump':self.pump=False;self.priming=False
    elif d['group']:self.elec[d['group']]=False
    if self.phase=='running':self.phase='failed';self._save_record()
   return {'latched':list(self.doc_faults)}
  if k=='priming_valve.set':
   if self.pressure>=.7e6:raise ActionRejected('PRESSURE_HIGH')
   self.priming_valve_open=a['open'];return a
  if k=='cd_cell.connect':
   if self.phase=='running':raise ActionRejected('BUSY')
   self.cell_source=a['source'];return a
  if k=='io.ttl_input':return self._ttl_input(a['input']-1,a['level_v'])
  if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
  if not self.powered:raise ActionRejected('NOT_READY')
  if self.phase=='running' and k in ['method.update','pump.configure','pump.prime','valve.set','detector.configure','electrolytics.set','temperature.set','sensor.calibrate_cd_offset','sensor.calibrate_cd_slope','sensor.reset_calibration']:raise ActionRejected('BUSY')
  if k in ['method.update','pump.configure','detector.configure','plot.configure','analog.configure','io.configure_outputs'] and not a:raise ActionRejected('INVALID_ARGUMENT')
  if k=='method.update':
   if not self.method:raise ActionRejected('NO_METHOD')
   m=dict(self.method,**a);self._check_method(m);self.method=m;return {'configured':True}
  if k=='pump.configure':
   s=dict(self.app,**a)
   if s['high_limit_pa']<s['low_limit_pa']+.34e6-1e-6:raise ActionRejected('LIMIT_RANGE')
   self.app=s;return {'configured':True}
  if k=='pump.prime':
   if a['enabled'] and (self.pump or self.reservoir<=0 or self.waste>=self.plant['waste_capacity_m3']):raise ActionRejected('NOT_READY')
   self.priming=a['enabled'];return a
  if k=='valve.set':
   m=self.method;vol=m['injection_m3'] if m else 0.
   if a['position']=='inject' and self.valve_pos=='load' and m and self.sample and self.sample['remaining']>=vol and self._ledger()['column_mol']<=1e-14 and self.waste+vol<=self.plant['waste_capacity_m3']:self._inject(vol,False)
   if a['position']!=self.valve_pos:self.valve_pos=a['position'];self.valve_t=self.time_s
   return a
  if k=='detector.configure':self.app.update(a);return {'configured':True}
  if k=='detector.autozero':self.cd['zero_us']=self.cd['shown_us'] or 0.;return {'zero_us':self.cd['zero_us']}
  if k=='electrolytics.set':
   if a['enabled'] and self._latched(a['device']):raise ActionRejected('NOT_READY')
   if a['enabled'] and not self.pump:raise ActionRejected('FLOW_OFF')
   if a['enabled'] and not self._consumable(a['device'])['enabled']:raise ActionRejected('CONSUMABLE_DISABLED')
   self.elec[a['device']]=a['enabled'];return {'enabled':a['enabled']}
  if k=='temperature.set':
   z,sp,amb=a['zone'],a.get('setpoint_k'),self.plant['ambient_k']
   if z=='column':
    if sp is not None:
     if not self.method:raise ActionRejected('NO_METHOD')
     if not 303.15-1e-9<=sp<=353.15+1e-9:raise ActionRejected('TEMPERATURE_RANGE')
     m=dict(self.method,column_k=sp);self._check_method(m);self.method=m
    if 'enabled' in a:self.column_heater=a['enabled']
   else:
    lo,hi=(max(288.15,amb-20),313.15) if z=='compartment' else (amb+7,333.15)
    if sp is not None and not lo-1e-9<=sp<=hi+1e-9:raise ActionRejected('TEMPERATURE_RANGE')
    if sp is not None:self.zones[z]['target_k']=sp
    if 'enabled' in a:self.zones[z]['on']=a['enabled']
   return {'zone':z}
  if k=='plot.configure':
   for x,y in [('cd','plot_cd'),('pressure','plot_pressure'),('time_min','plot_time_min'),('range_us','plot_range_us')]:
    if x in a:self.app[y]=a[x]
   return {'configured':True}
  if k=='analog.configure':
   for x in a:self.app['analog_'+x]=a[x]
   return {'configured':True}
  if k=='io.configure_outputs':
   for x,v in a.items():
    i=int(x.split('_')[1])-1
    if x.startswith('ttl'):self.app['ttl_out_v'][i]=v
    elif x.endswith('link'):self.app['relay_link'][i]=v
    else:self.app['relay'][i]=v
   return {'configured':True}
  if k=='io.configure_ttl_input':
   i=a['input']-1
   if 'function' in a:self.app['ttl_function'][i]=a['function']
   if 'control_type' in a:self.app['ttl_type'][i]=a['control_type']
   return {'configured':True}
  if k=='consumables.select':
   c=self._consumable(a['id'])
   if c['detected_by']=='RFID':c['checked']=a['checked']
   return {'checked':c['checked']}
  if k=='consumables.approve':
   for c in self.consumables:c['enabled']=c['checked']
   return {'approved':True}
  if k=='consumables.rescan':
   for c in self.consumables:c['checked']=c['enabled']
   return {'detected':len(self.consumables)}
  if k=='sensor.calibrate_cd_offset':
   m=self._uncal_s_m()
   if abs(m)*1e4>50.:raise ActionRejected('CALIBRATION_FAILED')
   self.cd.update(cal_offset_s_m=m,cal_slope=1.,cal_state='offset');return {'offset_us':m*1e4}
  if k=='sensor.calibrate_cd_slope':
   d=(self._uncal_s_m()-self.cd['cal_offset_s_m'])*1e4
   if self.cd['cal_state']!='offset' or not 147*.8<=d<=147*1.2:raise ActionRejected('CALIBRATION_FAILED')
   self.cd.update(cal_slope=147./d,cal_state='calibrated');return {'slope':self.cd['cal_slope']}
  if k=='sensor.reset_calibration':self.cd.update(cal_offset_s_m=0.,cal_slope=1.,cal_state='factory');return {'reset':True}
  if k=='alerts.dismiss':
   i=a['index']
   if i==-1:self.alerts=[]
   elif i<len(self.alerts):self.alerts=self.alerts[:i]+self.alerts[i+1:]
   else:raise ActionRejected('NOTHING_TO_DISMISS')
   return {'remaining':len(self.alerts)}
  if k=='sequence.interrupt':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self.phase='cancelled';self.pump=False;self._save_record();self._alert('Chromeleon run stopped from tablet','Abort');return {'phase':self.phase}
  if k=='display.configure':self.app['language']=a['language'];return a
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _ttl_input(self,i,level):
  """Rear-panel TTL input i (0-based) driven to level (V): edge types act on both edges (two-option functions) or the active edge (one-shot
  functions); pulse types act on the active edge only and toggle two-option functions (manual p.91-92, toggling is an assumption)."""
  old,self.ttl_in[i]=self.ttl_in[i],level;fn,typ=self.app['ttl_function'][i],self.app['ttl_type'][i];acts=[]
  if old==level or fn=='NONE' or not(self.plant and self.powered):return {'actions':acts}
  active=(level<old)==typ.startswith('NORMAL')
  if 'PULSE' in typ and not active:return {'actions':acts}
  if fn in ['AUTOZERO','MARK']:
   if active:
    if fn=='AUTOZERO':self.cd['zero_us']=self.cd['shown_us'] or 0.
    else:self.mark_until=self.time_s+.5
    acts.append(fn)
   return {'actions':acts}
  on=(not(self.valve_pos=='load') if fn=='INJECT VALVE' else not self.pump) if 'PULSE' in typ else active
  try:
   if fn=='INJECT VALVE':self._app_invoke('valve.set',{'position':'load' if on else 'inject'});acts.append('valve '+self.valve_pos)
   else:self._invoke('pump.set',{'enabled':on});acts.append('pump '+('on' if on else 'off'))
  except ActionRejected as exc:acts.append(f'{fn} refused: {exc}')
  return {'actions':acts}
 # ---- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
 def panel_read(self):
  out=self.read('agent');t=self.trace;a=self.app;u={'psi':1/PSI,'bar':1e-5,'MPa':1e-6}[a['pressure_unit']]
  out.update({'host.powered':self.powered,'host.power_epoch':self.power_epoch,'host.initializing':bool(self.powered and self.power_t is not None and self.time_s-self.power_t<INIT_S),'host.locked':self.phase=='running','host.flowing':bool(self.powered and self.pump),
   'host.plot_cd':[[x[0]/60,x[1]] for x in t],'host.plot_p':[[x[0]/60,x[2]*u] for x in t],'host.elapsed_min':(t[-1][0] if t else 0.)/60,'host.pending':any(c['checked']!=c['enabled'] for c in self.consumables),
   'host.alert_count':len(self.alerts),'host.serial':self.instance_id,'host.last_alert':self.alerts[-1]['text'] if self.alerts else '','host.mark':self.time_s<self.mark_until-1e-12})
  return out
 def _guard(self,k,principal):
  """Caller rule (final sync 2026-10-19): a sequence run belongs to the session that started it; another agent may not stop it (acquisition.stop,
  the app's stop-sequence choice, pump off) on either path.  The rear power switch, the doors and the evaluator are not restricted."""
  if principal is not None and self._principals.get(principal)=='agent' and k in RUN_GUARDED and self.phase=='running':
   owner=self._owners.get(self.operation_id)
   if owner is not None and owner!=principal:raise ActionRejected('ACCESS_DENIED')
 def panel_invoke(self,capability_id,arguments):
  """A panel action calls the same command a direct call would (each command checks before it changes anything); a gesture made through a
  session keeps that caller's access rules (_guard)."""
  try:
   self._guard(capability_id,getattr(self,'_caller',None))
   return self.invoke(capability_id,**arguments)
  except ActionRejected as exc:raise _panel_ui.PanelRejected(str(exc)) from None
 def panel_time(self):return self.time_s
 def _panel_cmd(self,k,a):
  p=self.panel
  try:
   if k=='panel.switch':return {'accepted':bool(p.switch(a['input_id'],a['position']))}
   if k=='panel.turn':return {'accepted':bool(p.turn(a['input_id'],a['detents']))}
   if k=='panel.touch':return {'accepted':bool(p.touch(a['display_id'],a['x'],a['y'],a.get('duration_s',.1),(a['to_x'],a['to_y']) if 'to_x' in a and 'to_y' in a else None))}
   if k=='panel.press':return {'accepted':bool(p.press(a['input_id'],a.get('duration_s',.1)))}
   if k=='panel.frame':return {'frame':p.frame()}
   data,w,h=p.png_bytes(a['display_id'])
  except _panel_ui.PanelError:raise ActionRejected('INVALID_ARGUMENT') from None
  sha=hashlib.sha256(data).hexdigest();d=Path(self.panel_capture_dir) if getattr(self,'panel_capture_dir',None) else Path(tempfile.gettempdir())/'chemistry_sim_panel'/self.instance_id
  d.mkdir(parents=True,exist_ok=True);path=d/f"{a['display_id']}_{sha[:16]}.png"
  if not path.exists():path.write_bytes(data)
  return dict(display_id=a['display_id'],time_s=self.time_s,image=dict(path=str(path),format='png',width=w,height=h,sha256=sha))
class ThermoDionexintegrionSession:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,i):
  if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  m=export_thermo_dionexintegrion_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
 def read(self,*,instance_id,state_ids=None):
  self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
  if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
  if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
  return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='unavailable' if v[k] is None else 'estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=h.time_s,sample_window_s=None) for k in ids})
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
   h._guard(capability_id,self._principal);h._caller=self._principal   # a tablet gesture acts as this session's caller
   try:result=h.invoke(capability_id,**arguments)
   finally:h._caller=None
   op=result.get('operation_id')
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


def export_thermo_dionexintegrion_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():
  ref='泵出口相对环境表压；环境为0Pa基准' if u=='Pa' else '液体实际体积流量，不是气体标准体积' if u=='m^3/s' else None
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=ref,origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'ThermoDionexintegrionSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  unsupported=k in UNSUPPORTED;asyn=k=='acquisition.start'
  caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.thermo_dionexintegrion.ThermoDionexintegrionSession.invoke',invocation=f'session.invoke(instance_id="integrion_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if kind in ['query','analysis'] or unsupported else [dict(description_zh='类型/身份、方法限值、闭门、温度准备、有限体积及忙碌检查；不冒充原厂安全互锁',state_ids=['power.enabled','pump.enabled','method.configured','door.angle_rad','display_door.angle_rad','acquisition.phase'],enforced_by='runtime',check='atomic Runtime.invoke plus trusted-principal Session')],state_effects=[dict(state_id=x,timing='immediate',description_zh='受检事务目标/逻辑状态变化') for x in effects],observes=[],model_effects=dict(mode='none',description_zh='等待独立实际场景求值；数值过程不自动证明模型联动',binding_ids=[]),execution=dict(mode='asynchronous' if asyn else 'synchronous',time_domain='simulation',completion_criterion='采集实际时间达到run_s；残留柱物质仍记账' if asyn else '受检事务返回；read/describe/poll不推进时钟',completion_state_ids=['acquisition.phase'] if asyn else [],timeout_s=None,cancellation='supported' if asyn else 'unsupported',retry='same_request_id',resources=['host_clock','sample_fluid','eluent_reservoir','waste_container','request_cache'],partial_failure_zh='已进样/已流出物质保留；取消停止泵目标，不逆向回滚；去重仅当前宿主进程'),errors=[dict(code=x,meaning_zh=x,side_effects='none') for x in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','PRECONDITION_FAILED','INITIALIZATION_ONLY','BUSY','COLUMN_RANGE','RFIC_FLOW_RANGE','PRESSURE_LIMIT','INSUFFICIENT_SUPPRESSION_CURRENT','SAMPLE_METHOD_REQUIRED','PREPARATION_REQUIRED','DUPLICATE_SOLUTE','UNKNOWN_REFERENCE','COLUMN_POLARITY','NO_SAMPLE','NOT_READY','SAMPLE_OR_CARRYOVER','WASTE_FULL','NOT_RUNNING','INVALID_WINDOW']+(EXTRA_ERRORS if k in APP_CAPS else [])],example_arguments=copy.deepcopy(ex),verification=[],limitations=['教学过程；真实保留曲线/噪声/活动度/硬件未校准'],source_ids=['manufacturer_operator','simulation_assumption'])
  if asyn:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='唯一由宿主step推进') for x in ['acquisition.elapsed_s','detector.conductivity_s_m','pump.flow_m3_s','pump.pressure_pa','acquisition.phase']]
 mech=dict(component_ids=['sample_port','injection_valve','pump_drive','column_separator','suppressor','conductivity_cell','waste_port'],state_ids=list(slots),meaning_zh='有限液体和物种账本、受限RFIC方法、lognormal保留体积分布与扩散电导模板',implementation='lumped',equations=['lambda_i=z_i^2*F^2*D_i/(R*298.15K), from USGS -dw; neutral z=0 gives zero conductivity','Band CDF(V)=0.5*(1+erf(log(V/V_ret)/(sqrt(2)*width_fraction))); V_ret and width explicit host assumptions','n_eluted=n_injected*CDF(V); n_column=n_injected-n_eluted; window conductivity=sum((lambda_i+abs(z_i)*lambda_counterion)*dn_i/dV)','Finite eluent V: dV=min(Q*dt, reservoir_remaining, waste_capacity_remaining, run_time_remaining*Q)','Finite injection takes sample injection_m3; solvent plug is instantaneous incompressible displacement into waste, analytes retained in column; no void-volume pressure pulse model','T(t+dt)=Ttarget+(T-Ttarget)*exp(-dt/tau); temperature setting changes target only','Pressure_gauge=hydraulic_resistance*step_average_flow; 5000psi RFIC /6000psi pump limits; no pressure transient or detector backpressure model','suppression_current*efficiency >= F*eluent_concentration*Q; ideal monovalent eluent Faraday current condition','Host clock only; read and observed-window quantification never inspect hidden sample composition'],parameters={},source_ids=['manufacturer_operator','usgs_diffusion','simulation_assumption'],limitations=['59公开水溶物性模板，其中38带电；未提供50条实测离子色谱。','保留体积/峰宽逐样品显式提供，整条色谱标synthetic；物性质据不冒充实机校准。','298.15K理想稀溶液与理想抑制，弱酸解离、配位、电荷配平、反应和高浓度非理想未实现。','样品溶剂进柱用等体积废液置换简化，不解算真实死体积和压力脉冲。','有限容器体积和进柱物种量守恒；未执行真实进样器、机器人或硬件。','机顶外部淋洗液瓶为宿主边界；几何只有本体承托盘与接管。','可选移动设备上的Integrion HPIC Mobile App由前面板层（panel_ui.json）驱动本运行层的App命令；App模型的简化见合同限制。','真值经会话过滤，宿主内存及共享文件需由部署环境另行隔离。'])
 spec=importlib.util.spec_from_file_location('_integrion_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'ion_chromatography':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'。'
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'ion_chromatography':mech})

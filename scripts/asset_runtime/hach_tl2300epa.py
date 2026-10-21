"""Referenced NTU replay with explicitly synthetic, non-factory optical dynamics."""
from __future__ import annotations
import copy,datetime as _dt,hashlib,json,math,tempfile,weakref
from asset_runtime import _panel_ui
from asset_runtime import _annotations as _ann
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT,PHASE
from asset_runtime.antonpaar_abbematadvanced import validate
ROOT=Path(__file__).resolve().parents[2];ASSET_TYPE_ID='instrument.hach_tl2300epa';CATALOG_ID='CHEM-014';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/hach_tl2300epa_turbidity_library/templates.json'
ANGLE=number('rad',0,1.7);NTU=number('NTU',0,4000);NULL_NTU={'anyOf':[NTU,{'type':'null'}]};TEMP=number('K',273.15,343.15)
COUNT={'type':'integer','enum':[1,10],'x-unit':'1'};MODE={'type':'string','enum':['single','continuous']}
NULL_TIME={'anyOf':[number('s',0),{'type':'null'}]}
RESULT=obj({'ntu':NULL_NTU,'stable':BOOL,'phase':PHASE,'quality':TEXT,'calibration_status':TEXT,'reference_accuracy_ntu':NULL_NTU,'sample_window_start_s':NULL_TIME,'sample_window_end_s':NULL_TIME,'tss_kg_m3':{'type':'null'},'evidence':TEXT,'template_source':TEXT})
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','button_power','tungsten_lamp'],'供电；断电取消并重置预热','device_command','agent',{'enabled':True},['power.enabled','warmup.elapsed_s','cycle.phase']),
 'lid.set_angle':(obj({'angle_rad':ANGLE}),obj({'angle_rad':ANGLE}),['lid'],'宿主驱动盖，开盖取消测量','device_command','agent',{'angle_rad':1.7},['lid.angle_rad','cycle.phase']),
 'filter.set_installed':(obj({'installed':BOOL}),obj({'installed':BOOL}),['epa_filter'],'断电维护EPA滤片状态','configuration','evaluator',{'installed':True},['filter.installed']),
 'sample.configure':(obj({'substance_id':TEXT,'volume_m3':number('m^3',20e-6,30e-6),'mass_kg':number('kg',1e-6,.1),'temperature_k':TEMP,'clean_indexed_cell':BOOL,'particle_response_gain':number('1',.1,2),'color_absorbance':number('1',0,5),'bubble_excess_ntu':number('NTU',0,1000),'bubble_tau_s':number('s',.1,3600)}),obj({'configured':BOOL}),['sample_cell','cell_interface'],'宿主装样与真实模板；独立光学参数均工程输入','configuration','evaluator',{'substance_id':'stablcal_20','volume_m3':20e-6,'mass_kg':.02,'temperature_k':296.15,'clean_indexed_cell':True,'particle_response_gain':1.,'color_absorbance':0.,'bubble_excess_ntu':0.,'bubble_tau_s':30.},['sample.loaded','sample.input_kg','sample.retained_kg','sample.input_m3','sample.retained_m3','turbidity.indicated_ntu']),
 'sample.unload':(obj({}),obj({'unloaded':BOOL}),['sample_cell','cell_interface'],'开盖移出样品，保留累计账本','configuration','evaluator',{},['sample.loaded','sample.removed_kg','sample.retained_kg','sample.removed_m3','sample.retained_m3','turbidity.indicated_ntu']),
 'method.configure':(obj({'ratio_enabled':BOOL,'average_count':COUNT,'mode':MODE}),obj({'configured':BOOL}),['display_digits','detector_90','detector_forward','detector_transmitted'],'比率、单次/连续与1/10次平均；其它档未实现','configuration','agent',{'ratio_enabled':True,'average_count':1,'mode':'single'},['method.ratio_enabled','method.average_count','method.mode','turbidity.indicated_ntu']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['tungsten_lamp','epa_filter','detector_90','detector_forward','detector_transmitted','display_digits'],'预热闭盖后定时测量','device_command','agent',{},['cycle.phase','cycle.elapsed_s','turbidity.stable','turbidity.indicated_ntu']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['display_digits'],'取消保留窗口与气泡衰减','device_command','agent',{},['cycle.phase']),
 'calibration.rapid':(obj({}),obj({'gain':number('1',.1,10),'range_max_ntu':number('NTU',40,40)}),['epa_filter','display_digits'],'20NTU标准完成测量后单点增益代理；暗电流假设0，仅0–40NTU','configuration','agent',{},['calibration.gain','calibration.status','turbidity.indicated_ntu']),
 'calibration.verify':(obj({'reference_ntu':number('NTU',.1,4000)}),obj({'deviation_ntu':number('NTU',-4000,4000),'within_five_percent':BOOL}),['display_digits'],'已知标准±5%核查，不自动校准','query','agent',{'reference_ntu':20.},[]),
 'turbidity.measure':(obj({}),RESULT,['display_digits','detector_90'],'纯读NTU、稳定性和校准；无通用TSS','query','agent',{},[]),
}
SLOTS={
 'power.enabled':(BOOL,'1','setpoint','agent',['power_inlet'],('power.set','enabled')),
 'lid.angle_rad':(ANGLE,'rad','scene','agent',['lid'],('lid.set_angle','angle_rad')),
 'filter.installed':(BOOL,'1','scene','agent',['epa_filter'],None),
 'warmup.elapsed_s':(number('s',0),'s','derived','agent',['tungsten_lamp'],None),
 'warmup.ready':(BOOL,'1','derived','agent',['tungsten_lamp'],None),
 'method.ratio_enabled':(BOOL,'1','setpoint','agent',['display_digits'],None),
 'method.average_count':(COUNT,'1','setpoint','agent',['display_digits'],None),
 'method.mode':(MODE,'1','setpoint','agent',['display_digits'],None),
 'cycle.phase':(PHASE,'1','derived','agent',['display_digits'],None),
 'cycle.elapsed_s':(number('s',0),'s','derived','agent',['display_digits'],None),
 'turbidity.indicated_ntu':(NULL_NTU,'NTU','derived','agent',['display_digits','detector_90'],None),
 'turbidity.stable':(BOOL,'1','derived','agent',['display_digits'],None),
 'calibration.gain':(number('1',.1,10),'1','setpoint','agent',['display_digits'],None),
 'calibration.status':(TEXT,'1','derived','agent',['display_digits'],None),
 'sample.loaded':(BOOL,'1','derived','agent',['sample_cell'],None),
 'sample.temperature_k':({'anyOf':[TEMP,{'type':'null'}]},'K','process_truth','evaluator',['sample_cell'],None),
 'sample.reference_ntu':(NULL_NTU,'NTU','process_truth','evaluator',['sample_cell'],None),
 'sample.bubble_excess_ntu':(number('NTU',0,1000),'NTU','process_truth','evaluator',['sample_cell'],None),
 'detector.normalized_90':(number('1',0),'1','process_truth','evaluator',['detector_90'],None),
 'detector.normalized_transmitted':(number('1',0,1),'1','process_truth','evaluator',['detector_transmitted'],None),
}
for axis,unit in [('kg','kg'),('m3','m^3')]:
 for key in ['input','retained','removed']:SLOTS[f'sample.{key}_{axis}']=(number(unit,0),unit,'process_truth','evaluator',['sample_cell'],None)
# ---------------------------------------------------------------- TL2300 touch-screen terminal (user manual DOC342.53.80533, sections 5-8)
# The device functions behind the screens: reading / data-log / calibration / verification setup, operator and sample IDs, security, data log,
# multi-point calibration and verification procedures with the kit's sealed standards, timer, sleep timer, date and time, self-check, reminders.
# They are added as capabilities of their own (TERMINAL_CAPS); the eleven instrument commands in CAPS keep their semantics.
# 1.3.0 (2026-10-21): plain 7 in. 5:3 touch glass (152.4 x 91.44 mm) in the 3D model and an 800 x 480 panel screen (manual figure 3 proportions)
ASSET_VERSION='1.3.0';ASSET_DIR=ROOT/'assets/expansion_batch03/t4_measure/hach_tl2300epa'
EBC_PER_NTU=9.8/40.                     # manual p.26: 40 NTU = 9.8 EBC
SELFCHECK_S=10.;CAL_WINDOW_S=60.;LOG_MAX=2000;MAX_OPERATORS=30;MAX_SAMPLE_IDS=1000;EPOCH0=1767225600.   # device clock 2026-01-01 00:00 at t=0 (assumption)
STD_LABEL=lambda n:'<0.1 NTU' if n==0 else f'{n:g} NTU'
CURVES={
 'stablcal_rapid':dict(label='StablCal RapidCal (0-40 NTU)',standards=[20.],range_ntu=40.,rapid=True),
 'stablcal_full':dict(label='StablCal (0-4000 NTU)',standards=[0.,20.,200.,1000.,4000.],range_ntu=4000.,rapid=False),
 'formazin_rapid':dict(label='Formazin RapidCal (0-40 NTU)',standards=[20.],range_ntu=40.,rapid=True),
 'formazin_full':dict(label='Formazin (0-4000 NTU)',standards=[0.,20.,200.,1000.,4000.],range_ntu=4000.,rapid=False,blank='Dilution water'),
 'sdvb_full':dict(label='SDVB (0-4000 NTU)',standards=[0.,20.,200.,1000.,4000.],range_ntu=4000.,rapid=False,blank='Dilution water'),
 'eu_pharm':dict(label='EU Pharm (0-30 NTU)',standards=[0.,3.,6.,18.,30.],range_ntu=30.,rapid=False)}
SECURITY_TASKS=['reading_setup','calibration','calibration_setup','verification','verification_setup','delete_data','sample_id','operator_id','setup','diagnostics']
LEVELS={'off':0,'one':1,'two':2}
SOUND_EVENTS=['key_press','reading_done','calibration_done','reminder','timer']
ICONS=['person','fish','butterfly','soccer_ball']
COLORS=['red','orange','yellow','green','blue','purple']
REMINDER={'type':'integer','x-unit':'d','enum':[0,1,7,30,90]}
ENUM=lambda *v:{'type':'string','enum':list(v)}
SETTINGS={   # key: (schema, factory default, security task, evidence page)
 'reading.mode':(ENUM('single','continuous','rst'),'single','reading_setup',14),'reading.signal_avg':(BOOL,False,'reading_setup',14),
 'reading.signal_avg_s':(number('s',5,90),10.,'reading_setup',14),'reading.unit':(ENUM('NTU','EBC'),'NTU','reading_setup',14),
 'reading.ratio':(BOOL,True,'reading_setup',14),'reading.bubble_reject':(BOOL,True,'reading_setup',14),
 'datalog.auto_store':(BOOL,True,'reading_setup',14),'datalog.send_format':(ENUM('csv','xml','bmp'),'xml','reading_setup',14),
 'datalog.print_format':(ENUM('quick','detailed'),'quick','reading_setup',14),'datalog.comments':(BOOL,False,'reading_setup',14),
 'datalog.auto_send':(ENUM('off','new_file','continue_file'),'off','reading_setup',14),
 'datalog.filter':(BOOL,False,None,25),'datalog.filter_from':({'anyOf':[TEXT,{'type':'null'}]},None,None,25),'datalog.filter_to':({'anyOf':[TEXT,{'type':'null'}]},None,None,25),
 'datalog.filter_operator':({'anyOf':[TEXT,{'type':'null'}]},None,None,25),'datalog.filter_sample':({'anyOf':[TEXT,{'type':'null'}]},None,None,25),
 'calibration.curve':(ENUM(*CURVES),'stablcal_rapid','calibration_setup',17),'calibration.verify_after_cal':(BOOL,False,'calibration_setup',17),
 'calibration.reminder_days':(REMINDER,0,'calibration_setup',17),
 'verification.standard_ntu':(number('NTU',.001,4000),20.,'verification_setup',20),'verification.acceptance':(number('1',.001,800),5.,'verification_setup',20),
 'verification.acceptance_unit':(ENUM('percent','ntu'),'percent','verification_setup',20),'verification.reminder_days':(REMINDER,0,'verification_setup',20),
 'location':(TEXT,'','setup',13),'date_format':(ENUM('dd-mm-yyyy','yyyy-mm-dd','dd.mm.yyyy','mm-dd-yyyy'),'dd-mm-yyyy','setup',13),
 'time_format':(ENUM('24','12'),'24','setup',13),'security.enabled':(BOOL,False,None,13),
 **{f'security.level.{t}':(ENUM('off','one','two'),'off',None,13) for t in SECURITY_TASKS},
 **{f'sound.{e}':(BOOL,True,'setup',13) for e in SOUND_EVENTS},
 'power.sleep_timer_s':({'type':'integer','x-unit':'s','enum':[0,1800,3600,7200,43200]},3600,'setup',14),
 'service.reminder':(BOOL,False,'diagnostics',35),'service.last_date':({'anyOf':[TEXT,{'type':'null'}]},None,'diagnostics',35),
 'service.next_date':({'anyOf':[TEXT,{'type':'null'}]},None,'diagnostics',35)}
SETTINGS_SCHEMA=dict(type='object',properties={k:v[0] for k,v in SETTINGS.items()},required=[],additionalProperties=False,minProperties=1)
LOG=ENUM('reading','calibration','verification')
OBJ={'type':'object'};LIST={'type':'array','items':{'type':'object'}};NULL_TEXT={'anyOf':[TEXT,{'type':'null'}]}
HW_CODES=[0,1,4,7,9,10,11,12,13]   # manual p.33 self-check error numbers whose cause is internal hardware (3 lid open and 8 vial inside are simulated from the scene)
HW_CODE={'type':'integer','x-unit':'1','enum':HW_CODES};NULL_CODE={'anyOf':[HW_CODE,{'type':'null'}]}
_SCREEN=['screen','display_digits'];_CELL=['sample_cell','cell_interface']
TERMINAL_CAPS={
 'settings.configure':(obj({'settings':SETTINGS_SCHEMA}),obj({'settings':OBJ}),_SCREEN,'触摸屏设置：Reading Setup（读数模式Single/Continuous/RST、Signal Avg、NTU/EBC、Ratio、Bubble Reject）、Data Log Setup、Calibration/Verification Setup、Setup（位置、日期时间格式、安全、声音、睡眠定时）、Service Time、数据日志筛选；受安全列表限制','configuration','agent',{'settings':{'reading.unit':'EBC'}},['device.settings','method.mode','method.ratio_enabled','display.reading']),
 'clock.set':(obj({'date':TEXT,'time':TEXT}),obj({'datetime':TEXT}),_SCREEN,'Setup > Date & Time：设备日期与时间（YYYY-MM-DD、HH:MM）','configuration','agent',{'date':'2026-10-19','time':'08:30'},['device.datetime']),
 'security.set_password':(obj({'password':TEXT}),obj({'set':BOOL}),_SCREEN,'Setup > Security > Security Password：管理员密码（最多10字符，区分大小写；空串清除）','configuration','agent',{'password':'admin1'},['security.state']),
 'security.unlock':(obj({'password':TEXT}),obj({'unlocked':BOOL}),_SCREEN,'输入管理员密码：解锁安全列表中受限的设置与任务（到注销或关机）','device_command','agent',{'password':'admin1'},['security.state']),
 'operator.save':(obj({'operator_id':TEXT,'icon':ENUM(*ICONS),'password':NULL_TEXT,'level':ENUM('off','one','two'),'replace':NULL_TEXT}),obj({'operator_id':TEXT}),_SCREEN,'Login > Options > New / Edit：操作员ID（最多30个，20字符）、图标、密码（null为不改）、安全级别','configuration','agent',{'operator_id':'OP1','icon':'fish','password':'','level':'off','replace':None},['operators.list']),
 'operator.delete':(obj({'operator_id':TEXT}),obj({'deleted':BOOL}),_SCREEN,'Login > Options > Delete：删除操作员ID（不能删除已登录者）','configuration','agent',{'operator_id':'OP1'},['operators.list']),
 'session.login':(obj({'operator_id':TEXT,'password':TEXT}),obj({'operator_id':TEXT}),_SCREEN,'Login：选择操作员ID并输入其密码登录（侧栏Login图标变为该ID）','device_command','agent',{'operator_id':'OP1','password':''},['session.operator']),
 'session.logout':(obj({}),obj({'operator_id':NULL_TEXT}),_SCREEN,'Logout：注销当前操作员（并撤销管理员解锁）','device_command','agent',{},['session.operator','security.state']),
 'sample_id.save':(obj({'sample_id':TEXT,'collected_date':NULL_TEXT,'collected_time':NULL_TEXT,'number':{'anyOf':[{'type':'integer','x-unit':'1','minimum':0,'maximum':999},{'type':'null'}]},'color':{'anyOf':[ENUM(*COLORS),{'type':'null'}]},'replace':NULL_TEXT}),obj({'sample_id':TEXT}),_SCREEN,'Sample ID > Options > New / Edit：样品ID（最多1000个，20字符）、采样日期时间、测量编号起点0–999、颜色','configuration','agent',{'sample_id':'Outlet','collected_date':None,'collected_time':None,'number':0,'color':'blue','replace':None},['sample_ids.list']),
 'sample_id.delete':(obj({'sample_id':NULL_TEXT}),obj({'deleted':number('1',0)}),_SCREEN,'Sample ID > Options > Delete（null为Delete All Sample IDs）','configuration','agent',{'sample_id':'Outlet'},['sample_ids.list','sample_ids.selected']),
 'sample_id.select':(obj({'sample_id':NULL_TEXT}),obj({'sample_id':NULL_TEXT}),_SCREEN,'Sample ID > Select：选当前样品ID（主屏显示，随读数记入日志）','device_command','agent',{'sample_id':'Outlet'},['sample_ids.selected']),
 'datalog.store':(obj({}),obj({'entry_id':number('1',1)}),_SCREEN,'Options > Store：Auto Store关时手动把最近读数存入读数日志','device_command','agent',{},['datalog.entries']),
 'datalog.comment':(obj({'log':LOG,'entry_id':{'type':'integer','x-unit':'1','minimum':1},'comment':TEXT}),obj({'entry_id':number('1',1)}),_SCREEN,'日志条目的注释（Comments设置开时）','device_command','agent',{'log':'reading','entry_id':1,'comment':'duplicate'},['datalog.entries']),
 'datalog.delete':(obj({'log':LOG,'scope':ENUM('single','filtered','all'),'entry_id':{'anyOf':[{'type':'integer','x-unit':'1','minimum':1},{'type':'null'}]}}),obj({'deleted':number('1',0)}),_SCREEN,'Data Log > Options > Delete Data：删除单条、筛选结果或全部','device_command','agent',{'log':'reading','scope':'all','entry_id':None},['datalog.entries']),
 'calibration.start':(obj({}),obj({'curve':TEXT,'standards':{'type':'array','items':number('NTU',0,4000)}}),['screen','display_digits','epa_filter'],'Calibration：进入所选校准曲线的校准流程（显示各标准值；需开机60 min）','device_command','agent',{},['procedure.state']),
 'calibration.read':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['screen','display_digits','tungsten_lamp','detector_90'],'校准流程中的Read：测当前标准（约1 min），检查稳定与数值范围后记录该点；最后一点后按Verify after Cal测核查标准','device_command','agent',{},['procedure.state','cycle.phase']),
 'calibration.store':(obj({}),obj({'curve':TEXT,'gain':number('1',.1,10)}),['screen','display_digits'],'校准流程中的Store：保存新校准曲线（RapidCal为过暗电流零点的单点增益，满量程为分段线性），写校准日志','configuration','agent',{},['calibration.gain','calibration.status','procedure.state','datalog.entries']),
 'verification.read':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['screen','display_digits','tungsten_lamp','detector_90'],'Verification：测核查标准（Gelex或StablCal），按接受范围给出Pass/Fail并自动存入核查日志','device_command','agent',{},['procedure.state','cycle.phase','datalog.entries']),
 'procedure.cancel':(obj({}),obj({'cancelled':BOOL}),_SCREEN,'离开校准或核查流程（未保存的校准数据丢弃，测量中先停止）','device_command','agent',{},['procedure.state','cycle.phase']),
 'standard.load':(obj({'material':ENUM('stablcal','formazin','sdvb','gelex','dilution_water'),'value_ntu':number('NTU',0,4000)}),obj({'loaded':BOOL}),_CELL,'评测者/宿主：开盖把密封标准管（StablCal、Gelex、自配Formazin/SDVB或稀释水）放入样品座','configuration','evaluator',{'material':'stablcal','value_ntu':20.},['standard.loaded']),
 'standard.unload':(obj({}),obj({'unloaded':BOOL}),_CELL,'评测者/宿主：开盖取出密封标准管','configuration','evaluator',{},['standard.loaded']),
 'timer.start':(obj({'duration_s':number('s',1,5999)}),obj({'running':BOOL}),_SCREEN,'Timer：倒计时启动（到时提示音）','device_command','agent',{'duration_s':60.},['timer.state']),
 'timer.stop':(obj({}),obj({'running':BOOL}),_SCREEN,'Timer：停止倒计时','device_command','agent',{},['timer.state']),
 'fault.inject':(obj({'code':HW_CODE}),obj({'fault':NULL_CODE}),['screen','display_digits','tungsten_lamp'],'评测者：注入手册第8节开机自检硬件错误号（0 RTC、1 Touch IC、4放大系数、7灯电压、9 SDRAM、10 NOR flash、11 SPI flash、12电池电压、13电源电压）；开机时仪器即检出：进行中的读数失败、Read与校准/核查读数被拒，主屏显示Hardware error；重新开机后自检停在The self-check stopped. Hardware error.与Error N；故障在修复前每次开机再现','configuration','evaluator',{'code':7},['fault.injected','selfcheck.state','display.reading','cycle.phase']),
 'fault.clear':(obj({}),obj({'fault':NULL_CODE}),['screen'],'评测者：撤销注入的硬件故障（代表技术支持维修）；屏上的错误在下次关机再开机（手册：关机20 s后再开）时消失','configuration','evaluator',{},['fault.injected'])}
TERMINAL_SLOTS={
 'device.settings':(OBJ,'1','setpoint','agent',['screen'],None),'device.datetime':(TEXT,'1','derived','agent',['screen'],None),
 'security.state':(OBJ,'1','derived','agent',['screen'],None),'operators.list':(LIST,'1','setpoint','agent',['screen'],None),
 'session.operator':(NULL_TEXT,'1','derived','agent',['screen'],None),'sample_ids.list':(LIST,'1','setpoint','agent',['screen'],None),
 'sample_ids.selected':(NULL_TEXT,'1','setpoint','agent',['screen'],None),'datalog.entries':(OBJ,'1','derived','agent',['screen'],None),
 'datalog.view':(OBJ,'1','derived','agent',['screen'],None),'display.reading':(OBJ,'1','derived','agent',['display_digits'],None),
 'procedure.state':({'anyOf':[OBJ,{'type':'null'}]},'1','derived','agent',['screen'],None),'standard.loaded':({'anyOf':[OBJ,{'type':'null'}]},'1','scene','evaluator',['sample_cell'],None),
 'timer.state':(OBJ,'1','derived','agent',['screen'],None),'display.sleeping':(BOOL,'1','derived','agent',['screen'],None),
 'selfcheck.state':(OBJ,'1','derived','agent',['screen'],None),'reminders.due':({'type':'array','items':TEXT},'1','derived','agent',['screen'],None),
 'fault.injected':(NULL_CODE,'1','process_truth','evaluator',['screen'],None)}
SLOTS.update(TERMINAL_SLOTS)
TERMINAL_ZH={'device.settings':'触摸屏设置表（键见SETTINGS；reading.mode与reading.ratio跟随运行层读数模式与比率）','device.datetime':'设备日期时间（ISO，开机后随仿真时钟走）',
 'security.state':'安全：管理员密码已设、是否已解锁、当前操作员级别','operators.list':'操作员ID列表（不含密码）','session.operator':'已登录的操作员ID',
 'sample_ids.list':'样品ID列表（采样时间、测量编号、颜色）','sample_ids.selected':'当前样品ID','datalog.entries':'读数、校准、核查日志（共2000条，满时删最旧）',
 'datalog.view':'按筛选设置显示的日志（最新在前）','display.reading':'主屏读数区：数值文字、单位、模式、Ratio Off指示、提示与错误信息','procedure.state':'校准或核查流程（曲线、标准、已测点、提示、结果）',
 'standard.loaded':'样品座中的密封标准管（材质与真值）','timer.state':'倒计时（设定、剩余、运行、到时）','display.sleeping':'睡眠定时到：屏幕关闭（触摸唤醒）',
 'selfcheck.state':'开机自检（进行中、通过或硬件错误号）','reminders.due':'到期提醒（校准、核查、服务）',
 'fault.injected':'评测者注入的硬件故障（手册第8节自检错误号；null为无）'}


def _iso(t):return _dt.datetime.fromtimestamp(t,_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')


def _fmt_ntu(v):
 """Display digits (assumption: four significant digits down to the 0.001 NTU resolution)."""
 return f'{v:.3f}' if v<10 else f'{v:.2f}' if v<100 else f'{v:.1f}' if v<1000 else f'{v:.0f}'


class _Tl2300Terminal:
 """Touch-screen device functions of the TL2300 (mixed into the runtime; plain data only, so copies and comparisons of the runtime stay exact)."""
 def _terminal_init(self):
  self.settings={k:copy.deepcopy(v[1]) for k,v in SETTINGS.items() if k not in ('reading.mode','reading.ratio')}
  self.clock_offset=EPOCH0;self.admin_password='';self.admin_unlocked=False;self.operators=[];self.operator=None;self.sample_ids=[];self.sample_id=None
  self.logs={'reading':[],'calibration':[],'verification':[]};self.log_seq=0;self.pending=None;self.shown=None;self.notice=None
  self.standard=None;self.procedure=None;self.curve=None;self.curve_name=None;self.last_cal_s=None;self.last_ver_s=None
  self.timer={'duration_s':60.,'remaining_s':60.,'running':False,'expired':False};self.last_activity_s=0.;self.rst=False;self.rst_sum=0.;self.rst_n=0
  self.selfcheck={'start':-SELFCHECK_S,'error':None} if self.powered else None          # a trusted powered start has passed its self-check
  self.panel_capture_dir=None;self.power_epoch=1 if self.powered else 0
  self.hw_fault=None;self.hw_error=None      # injected hardware fault (evaluator) / the error number the instrument has detected (until the next power-up)
 # ---- clock, settings view, display
 def _now(self):return self.clock_offset+self.time_s
 def _settings_view(self):
  return dict(self.settings,**{'reading.mode':'rst' if self.rst else self.mode,'reading.ratio':self.ratio})
 def _level(self):
  op=next((o for o in self.operators if o['operator_id']==self.operator),None)
  return LEVELS[op['level']] if op else 0
 def _need_access(self,task):
  if task is None or not self.settings['security.enabled'] or self.admin_unlocked:return
  if self._level()<LEVELS[self.settings[f'security.level.{task}']]:raise ActionRejected('ACCESS_LEVEL')
 def _due(self):
  out=[];now=self._now()
  for what,last,days in (('calibration',self.last_cal_s,self.settings['calibration.reminder_days']),('verification',self.last_ver_s,self.settings['verification.reminder_days'])):
   if days and (last is None or now-last>=days*86400-1e-6):out.append(what)
  nd=self.settings['service.next_date']
  if self.settings['service.reminder'] and nd and _iso(now)[:10]>=nd:out.append('service')
  return out
 def _display(self):
  """What the reading area of the home screen shows (value in the selected unit, bubble-reject filtering, range and reminder messages)."""
  unit=self.settings['reading.unit'];k=EBC_PER_NTU if unit=='EBC' else 1.;s=self.shown;msgs=[]
  if self.phase=='failed' and self.indicated is None and self.raw_mean is not None and self.hw_error is None:msgs.append('The measurement range is exceeded.')
  rng=CURVES[self.curve_name]['range_ntu'] if self.curve_name else 40.
  if s and self.calibrated and s['ntu']>rng+1e-9:msgs.append('The calibration range is exceeded.')
  if self.powered and self.hw_error is not None:msgs.append('Hardware error')   # manual p.34 (Reading: Hardware error / instrument error)
  msgs+=[f'Next {d} is due!' for d in self._due()]
  if self.notice:msgs.append(self.notice)
  return {'text':'----' if s is None else _fmt_ntu(s['ntu']*k),'value':None if s is None else s['ntu']*k,'unit':unit,'mode':{'single':'Single','continuous':'Continuous'}[self.mode] if not self.rst else 'RST',
          'ratio_off':not self.ratio,'measuring':self.phase=='running' and self.procedure is None,'stable':bool(s and s['stable']),'messages':msgs,'message':msgs[0] if msgs else None}
 def _sleeping(self):
  t=self.settings['power.sleep_timer_s']
  return bool(self.powered and t and self.time_s-self.last_activity_s>=t-1e-9)
 def _selfcheck_view(self):
  c=self.selfcheck
  if not self.powered or c is None:return {'phase':'off','error':None}
  if self.time_s-c['start']<SELFCHECK_S-1e-9:return {'phase':'running','error':None}
  return {'phase':'error' if c['error'] is not None else 'passed','error':c['error']}
 def _terminal_values(self):
  view={};f=self.settings
  for log,rows in self.logs.items():
   keep=[r for r in rows if not f['datalog.filter'] or ((f['datalog.filter_from'] is None or r['time'][:10]>=f['datalog.filter_from']) and (f['datalog.filter_to'] is None or r['time'][:10]<=f['datalog.filter_to'])
          and (f['datalog.filter_operator'] is None or r.get('operator')==f['datalog.filter_operator']) and (log!='reading' or f['datalog.filter_sample'] is None or r.get('sample_id')==f['datalog.filter_sample']))]
   view[log]=list(reversed(keep))
  return {'device.settings':self._settings_view(),'device.datetime':_iso(self._now()),'security.state':{'password_set':bool(self.admin_password),'unlocked':self.admin_unlocked,'level':self._level()},
          'operators.list':[{k:v for k,v in o.items() if k!='password'}|{'has_password':bool(o['password'])} for o in self.operators],'session.operator':self.operator,
          'sample_ids.list':copy.deepcopy(self.sample_ids),'sample_ids.selected':self.sample_id,'datalog.entries':copy.deepcopy(self.logs),'datalog.view':copy.deepcopy(view),
          'display.reading':self._display(),'procedure.state':self._procedure_view(),'standard.loaded':copy.deepcopy(self.standard),'timer.state':dict(self.timer),
          'display.sleeping':self._sleeping(),'selfcheck.state':self._selfcheck_view(),'reminders.due':self._due(),'fault.injected':self.hw_fault}
 def _procedure_view(self):
  p=copy.deepcopy(self.procedure)
  if p and p['kind']=='calibration':
   blank=CURVES[p['curve']].get('blank','<0.1 NTU')
   p['rows']=[{'label':blank if n==0 else STD_LABEL(n),'value':_fmt_ntu(p['points'][i][0]) if i<len(p['points']) else '','current':i==p['index']} for i,n in enumerate(p['standards'])]
   if p['verify_after_cal']:
    v=p['verification'];p['rows'].append({'label':f"Verification {p['verification_ntu']:g} NTU",'value':'' if v is None else f"{_fmt_ntu(v['value_ntu'])} {'Pass' if v['pass'] else 'Fail'}",'current':p['step']=='verify'})
  return p
 # ---- measurement hooks
 def _holder(self):return self.sample is not None or self.standard is not None
 def _window_s(self):
  if self.procedure and self.procedure.get('measuring') and self.procedure['kind']=='calibration':return CAL_WINDOW_S
  if self.settings['reading.signal_avg'] and not self.procedure:
   return float(min(self.settings['reading.signal_avg_s'],15.) if self.mode=='single' else self.settings['reading.signal_avg_s'])
  return 6.8 if self.average_count==1 else 14.
 def _calibrated(self,raw):
  if not self.curve:return self.gain*raw
  pts=self.curve                                                         # piecewise linear through the stored points (sorted by raw signal)
  for i in range(1,len(pts)):
   (r0,n0),(r1,n1)=pts[i-1],pts[i]
   if raw<=r1 or i==len(pts)-1:return max(0.,n0+(raw-r0)*(n1-n0)/(r1-r0))
  return max(0.,pts[0][1])
 def _log(self,log,entry):
  self.log_seq+=1;entry=dict(entry,id=self.log_seq,time=_iso(self._now()),operator=self.operator,comment='')
  self.logs[log].append(entry)
  while sum(len(v) for v in self.logs.values())>LOG_MAX:                 # full: the oldest record of all logs goes
   oldest=min((v[0]['id'],k) for k,v in self.logs.items() if v);self.logs[oldest[1]].pop(0)
  return entry['id']
 def _reading_entry(self):
  sid=next((s for s in self.sample_ids if s['sample_id']==self.sample_id),None)
  return dict(sample_id=self.sample_id,number=sid['next_number'] if sid and sid['number'] is not None else None,value_ntu=self.indicated,
              unit=self.settings['reading.unit'],value=self.indicated*(EBC_PER_NTU if self.settings['reading.unit']=='EBC' else 1.),
              mode='RST' if self.rst else self.mode,ratio=self.ratio,stable=self.stable,curve=self.curve_name)
 def _reading_done(self):
  """A completed measurement (single window, or Done in continuous / RST): number, data log (Auto Store) or Options > Store, Auto Send."""
  if self.indicated is None or (self.settings['reading.bubble_reject'] and not self.stable):return
  e=self._reading_entry()
  sid=next((s for s in self.sample_ids if s['sample_id']==self.sample_id),None)
  if sid and sid['number'] is not None:sid['next_number']=(sid['next_number']+1)%1000
  if self.settings['datalog.auto_store']:self._log('reading',e);self.pending=None
  else:self.pending=e
  self.notice='Connect a receiving device.' if self.settings['datalog.auto_send']!='off' else None
 def _window_done(self):
  """Hook at the end of each averaging window (step): RST trend mean, shown value, procedures, single-reading completion."""
  if self.rst and self.indicated is not None and not self.procedure:
   self.rst_sum+=self.indicated;self.rst_n+=1;self.indicated=round(self.rst_sum/self.rst_n,3)
  p=self.procedure
  if p and p.get('measuring'):
   p['measuring']=False;self.phase='succeeded' if self.indicated is not None or p['kind']=='calibration' else 'failed'
   (self._cal_point if p['kind']=='calibration' else self._ver_result)(p)
   return
  if self.indicated is not None and (self.stable or not self.settings['reading.bubble_reject']):
   self.shown={'ntu':self.indicated,'stable':self.stable}
  if self.mode=='single':
   self.phase='succeeded' if self.indicated is not None else 'failed';self._reading_done()
 def _cal_point(self,p):
  raw=self.raw_mean;i=p['index'];n=p['standards'][i] if i<len(p['standards']) else None
  if n is None:                                                          # the verification standard measured after the last point
   v=self._calibrated_with(p,raw);p['verification']={'value_ntu':round(v,3),'pass':self._accept(v)};p['step']='store';p['message']=None;return
  if not self.stable:p['message']='The standard is not stable.';return
  if raw>6000:p['message']='The standard value is out of the measurement range.';return
  if (n==0 and raw>.5) or (n>0 and raw>1.5*n):p['message']='The standard value is too high.';return
  if n>0 and raw<.5*n:p['message']='The standard value is too low.';return
  p['points'].append([raw,n]);p['index']+=1;p['message']=None
  if p['index']<len(p['standards']):p['step']='standard'
  else:p['step']='verify' if p['verify_after_cal'] else 'store'
 def _calibrated_with(self,p,raw):
  pts=sorted(p['points']);rapid=CURVES[p['curve']]['rapid']
  if rapid:return raw*pts[-1][1]/pts[-1][0]
  saved=self.curve;self.curve=pts
  try:return self._calibrated(raw)
  finally:self.curve=saved
 def _accept(self,v):
  ref=self.settings['verification.standard_ntu'];acc=self.settings['verification.acceptance']
  lim=acc/100.*ref if self.settings['verification.acceptance_unit']=='percent' else acc
  return abs(v-ref)<=lim+1e-12
 def _ver_result(self,p):
  v=self.indicated
  if v is None:p['message']='Instrument error';return
  ok=self._accept(v);p['result']={'value_ntu':v,'pass':ok};p['message']=None if ok else 'Verification failed.'
  self._log('verification',dict(standard_ntu=self.settings['verification.standard_ntu'],value_ntu=v,acceptance=self.settings['verification.acceptance'],
                                acceptance_unit=self.settings['verification.acceptance_unit'],passed=ok,source='verification'))
  self.last_ver_s=self._now()
 def _start_measuring(self):
  self._operation_count+=1;self.operation_id=f'{self.instance_id}:ntu:{self._operation_count}';self.phase='running';self.elapsed_s=0.;self.window_elapsed=0.;self.integral=0.;self.window_first=self.bubbles;self._invalidate()
  return {'phase':self.phase,'operation_id':self.operation_id}
 def _need_measurable(self,warmup):
  if not self.powered or self.warmup_s<warmup or not self.filter_installed or self.lid_angle>1e-4 or not self._holder() or (self.sample and not self.sample['clean_indexed_cell']) or self.hw_error is not None:raise ActionRejected('PRECONDITION_FAILED')
 def _fault_detected(self):
  """The instrument detects the injected hardware fault: a running reading fails; it is shown until the next power-up."""
  self.hw_error=self.hw_fault
  if self.selfcheck and self.time_s-self.selfcheck['start']<SELFCHECK_S-1e-9:self.selfcheck['error']=self.hw_fault   # during the self-check: it stops
  if self.phase=='running':
   self.phase='failed';self.stable=False
   if self.procedure:self.procedure['measuring']=False
 # ---- terminal capabilities
 def _terminal(self,capability,a):
  if capability=='fault.inject':                                        # evaluator: hardware fault of the manual's self-check table (p.33)
   self.hw_fault=a['code']
   if self.powered:self._fault_detected()
   return {'fault':self.hw_fault}
  if capability=='fault.clear':                                         # evaluator: the repair; the shown error goes at the next power-up
   self.hw_fault=None;return {'fault':None}
  if self.phase=='running' and capability not in ('timer.start','timer.stop','procedure.cancel'):raise ActionRejected('BUSY')
  if capability not in ('standard.load','standard.unload') and not self.powered:raise ActionRejected('POWER_OFF')
  st=self.settings
  if capability=='settings.configure':
   ch=a['settings'];new=dict(self._settings_view(),**ch)
   for k in ch:self._need_access(SETTINGS[k][2])
   if any(k.startswith('security.') for k in ch) and st['security.enabled'] and not self.admin_unlocked:raise ActionRejected('ACCESS_LEVEL')
   if new['security.enabled'] and not self.admin_password:raise ActionRejected('PASSWORD_REQUIRED')
   if not 5<=new['reading.signal_avg_s']<=(15 if new['reading.mode']=='single' else 90):raise ActionRejected('OUT_OF_RANGE')
   if new['verification.acceptance_unit']=='percent' and not 1<=new['verification.acceptance']<=20:raise ActionRejected('OUT_OF_RANGE')
   if new['verification.acceptance_unit']=='ntu' and not .001<=new['verification.acceptance']<=.2*CURVES[new['calibration.curve']]['range_ntu']:raise ActionRejected('OUT_OF_RANGE')
   if len(new['location'])>20 or any(x is not None and not _date_ok(x) for x in (new['service.last_date'],new['service.next_date'],new['datalog.filter_from'],new['datalog.filter_to'])):raise ActionRejected('INVALID_ARGUMENT')
   if 'reading.mode' in ch or 'reading.ratio' in ch:
    self.mode='single' if new['reading.mode']=='single' else 'continuous';self.rst=new['reading.mode']=='rst';self.ratio=new['reading.ratio'];self._invalidate();self.shown=None
   if ch.keys()&{'reading.signal_avg','reading.signal_avg_s','reading.unit'}:self._invalidate()
   if 'calibration.curve' in ch and ch['calibration.curve']!=st['calibration.curve']:self.calibrated=False;self.curve=None;self.curve_name=None;self.gain=1.   # a new curve must be calibrated (p.15)
   st.update({k:v for k,v in ch.items() if k not in ('reading.mode','reading.ratio')});return {'settings':self._settings_view()}
  if capability=='clock.set':
   self._need_access('setup')
   try:t=_dt.datetime.strptime(a['date']+'T'+a['time'],'%Y-%m-%dT%H:%M').replace(tzinfo=_dt.timezone.utc).timestamp()
   except ValueError:raise ActionRejected('INVALID_ARGUMENT') from None
   self.clock_offset=t-self.time_s;return {'datetime':_iso(self._now())}
  if capability=='security.set_password':
   if self.admin_password and st['security.enabled'] and not self.admin_unlocked:raise ActionRejected('ACCESS_LEVEL')
   if len(a['password'])>10 or (not a['password'] and st['security.enabled']):raise ActionRejected('INVALID_ARGUMENT')
   self.admin_password=a['password'];return {'set':bool(self.admin_password)}
  if capability=='security.unlock':
   if not self.admin_password or a['password']!=self.admin_password:raise ActionRejected('INVALID_PASSWORD')
   self.admin_unlocked=True;return {'unlocked':True}
  if capability=='operator.save':
   self._need_access('operator_id');oid=a['operator_id'];old=a['replace']
   if not 1<=len(oid)<=20 or len(a['password'] or '')>10 or (a['level']!='off' and not st['security.enabled']):raise ActionRejected('INVALID_ARGUMENT')
   if old is not None and not any(o['operator_id']==old for o in self.operators):raise ActionRejected('NOT_FOUND')
   if oid!=old and any(o['operator_id']==oid for o in self.operators):raise ActionRejected('DUPLICATE')
   if old is None and len(self.operators)>=MAX_OPERATORS:raise ActionRejected('LIST_FULL')
   pw=a['password'] if a['password'] is not None else next((o['password'] for o in self.operators if o['operator_id']==old),'')
   rec=dict(operator_id=oid,icon=a['icon'],password=pw,level=a['level'])
   if old is None:self.operators.append(rec)
   else:
    self.operators=[rec if o['operator_id']==old else o for o in self.operators]
    if self.operator==old:self.operator=oid
   return {'operator_id':oid}
  if capability=='operator.delete':
   self._need_access('operator_id')
   if not any(o['operator_id']==a['operator_id'] for o in self.operators):raise ActionRejected('NOT_FOUND')
   if self.operator==a['operator_id']:raise ActionRejected('LOGGED_IN')
   self.operators=[o for o in self.operators if o['operator_id']!=a['operator_id']];return {'deleted':True}
  if capability=='session.login':
   op=next((o for o in self.operators if o['operator_id']==a['operator_id']),None)
   if op is None:raise ActionRejected('NOT_FOUND')
   if op['password'] and op['password']!=a['password']:raise ActionRejected('INVALID_PASSWORD')
   self.operator=op['operator_id'];return {'operator_id':self.operator}
  if capability=='session.logout':
   self.operator=None;self.admin_unlocked=False;return {'operator_id':None}
  if capability=='sample_id.save':
   self._need_access('sample_id');sid=a['sample_id'];old=a['replace']
   col=None if a['collected_date'] is None else f"{a['collected_date']}T{a['collected_time'] or '00:00'}"
   if not 1<=len(sid)<=20 or (col is not None and not _datetime_ok(col)):raise ActionRejected('INVALID_ARGUMENT')
   if old is not None and not any(s['sample_id']==old for s in self.sample_ids):raise ActionRejected('NOT_FOUND')
   if sid!=old and any(s['sample_id']==sid for s in self.sample_ids):raise ActionRejected('DUPLICATE')
   if old is None and len(self.sample_ids)>=MAX_SAMPLE_IDS:raise ActionRejected('LIST_FULL')
   rec=dict(sample_id=sid,collected=col,number=a['number'],next_number=a['number'],color=a['color'])
   if old is None:self.sample_ids.append(rec)
   else:
    self.sample_ids=[rec if s['sample_id']==old else s for s in self.sample_ids]
    if self.sample_id==old:self.sample_id=sid
   return {'sample_id':sid}
  if capability=='sample_id.delete':
   self._need_access('sample_id')
   if a['sample_id'] is None:n=len(self.sample_ids);self.sample_ids=[];self.sample_id=None;return {'deleted':n}
   if not any(s['sample_id']==a['sample_id'] for s in self.sample_ids):raise ActionRejected('NOT_FOUND')
   self.sample_ids=[s for s in self.sample_ids if s['sample_id']!=a['sample_id']]
   if self.sample_id==a['sample_id']:self.sample_id=None
   return {'deleted':1}
  if capability=='sample_id.select':
   if a['sample_id'] is not None and not any(s['sample_id']==a['sample_id'] for s in self.sample_ids):raise ActionRejected('NOT_FOUND')
   self.sample_id=a['sample_id'];return {'sample_id':self.sample_id}
  if capability=='datalog.store':
   if self.pending is None:raise ActionRejected('NO_READING')
   i=self._log('reading',self.pending);self.pending=None;return {'entry_id':i}
  if capability=='datalog.comment':
   if not st['datalog.comments'] or len(a['comment'])>40:raise ActionRejected('PRECONDITION_FAILED' if not st['datalog.comments'] else 'INVALID_ARGUMENT')
   e=next((r for r in self.logs[a['log']] if r['id']==a['entry_id']),None)
   if e is None:raise ActionRejected('NOT_FOUND')
   e['comment']=a['comment'];return {'entry_id':e['id']}
  if capability=='datalog.delete':
   self._need_access('delete_data');rows=self.logs[a['log']]
   if a['scope']=='single':
    if not any(r['id']==a['entry_id'] for r in rows):raise ActionRejected('NOT_FOUND')
    keep=[r for r in rows if r['id']!=a['entry_id']]
   elif a['scope']=='filtered':
    gone={r['id'] for r in self._terminal_values()['datalog.view'][a['log']]};keep=[r for r in rows if r['id'] not in gone]
   else:keep=[]
   n=len(rows)-len(keep);self.logs[a['log']]=keep;return {'deleted':n}
  if capability=='calibration.start':
   self._need_access('calibration')
   if self.warmup_s<3600:raise ActionRejected('WARMING_UP')
   c=CURVES[st['calibration.curve']]
   self.procedure=dict(kind='calibration',curve=st['calibration.curve'],label=c['label'],standards=list(c['standards']),index=0,points=[],step='standard',
                       verify_after_cal=st['calibration.verify_after_cal'],verification_ntu=st['verification.standard_ntu'],measuring=False,message=None,verification=None)
   return {'curve':c['label'],'standards':list(c['standards'])}
  if capability=='calibration.read':
   p=self.procedure
   if not p or p['kind']!='calibration' or p['step']=='store':raise ActionRejected('PRECONDITION_FAILED')
   self._need_measurable(3600);p['measuring']=True;p['message']=None;return self._start_measuring()
  if capability=='calibration.store':
   p=self.procedure
   if not p or p['kind']!='calibration' or p['step']!='store':raise ActionRejected('PRECONDITION_FAILED')
   pts=sorted(p['points'],key=lambda x:x[1]);c=CURVES[p['curve']]
   gain=pts[-1][1]/pts[-1][0] if c['rapid'] else next((x[1]/x[0] for x in pts if x[1]>=3),pts[-1][1]/pts[-1][0])
   if not .1<=gain<=10 or any(b[0]<=a_[0] for a_,b in zip(pts,pts[1:])):p['message']='Instrument error';raise ActionRejected('INSTRUMENT_ERROR')
   self.curve=None if c['rapid'] else [list(x) for x in pts];self.gain=gain
   self.calibrated=True;self.curve_name=p['curve'];self.last_cal_s=self._now();self._invalidate();self.shown=None
   self._log('calibration',dict(curve=c['label'],points=[{'standard_ntu':n,'measured_raw':round(r,4)} for r,n in pts],gain=self.gain))
   if p['verification']:
    v=p['verification'];self._log('verification',dict(standard_ntu=p['verification_ntu'],value_ntu=v['value_ntu'],acceptance=st['verification.acceptance'],
                                                     acceptance_unit=st['verification.acceptance_unit'],passed=v['pass'],source='verify_after_cal'));self.last_ver_s=self._now()
   self.procedure=None;return {'curve':c['label'],'gain':self.gain}
  if capability=='verification.read':
   self._need_access('verification');self._need_measurable(1800 if self.ratio else 3600)
   self.procedure=dict(kind='verification',standard_ntu=st['verification.standard_ntu'],measuring=True,message=None,result=None);return self._start_measuring()
  if capability=='procedure.cancel':
   if self.procedure is None:raise ActionRejected('PRECONDITION_FAILED')
   if self.phase=='running':self.phase='cancelled';self.stable=False
   self.procedure=None;return {'cancelled':True}
  if capability=='standard.load':
   if self.lid_angle<1.4 or self._holder():raise ActionRejected('PRECONDITION_FAILED')
   self.standard=dict(a);self.bubbles=0.;self._invalidate();return {'loaded':True}
  if capability=='standard.unload':
   if self.lid_angle<1.4 or self.standard is None:raise ActionRejected('PRECONDITION_FAILED')
   self.standard=None;self._invalidate();return {'unloaded':True}
  if capability=='timer.start':
   self.timer={'duration_s':float(a['duration_s']),'remaining_s':float(a['duration_s']),'running':True,'expired':False};return {'running':True}
  if capability=='timer.stop':
   self.timer['running']=False;return {'running':False}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _terminal_step(self,h):
  t=self.timer
  if t['running']:
   t['remaining_s']=max(0.,t['remaining_s']-h)
   if t['remaining_s']<=1e-9:t.update(running=False,expired=True)
 # ---- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
 @property
 def panel(self):
  p=_PANELS.get(self)
  if p is None:p=_PANELS[self]=_panel_ui.PanelUI(PANEL_SPEC,self)
  return p
 def panel_read(self):
  """What the terminal firmware shows: the agent slots plus power, power-up count, the selected curve's label and the next standard."""
  out=self.read('agent');p=self.procedure
  out.update({'host.powered':self.powered,'host.power_epoch':self.power_epoch,'host.curve_label':CURVES[self.settings['calibration.curve']]['label'],
              'host.calibrated_label':CURVES[self.curve_name]['label'] if self.curve_name else None,
              'host.standard_label':(CURVES[p['curve']].get('blank','<0.1 NTU') if p['standards'][p['index']]==0 else STD_LABEL(p['standards'][p['index']])) if p and p['kind']=='calibration' and p['index']<len(p['standards']) else None,
              'host.pending':self.pending is not None,'host.view_counts':{k:len(v) for k,v in out['datalog.view'].items()},
              'host.hardware_error':self.powered and self.hw_error is not None})
  return out
 def panel_invoke(self,capability_id,arguments):
  """A panel action calls the same command a direct call would (each command checks before it changes anything).  A gesture made through a
  session acts as that session's caller: the session's ownership guard applies (Done or the power key on another agent's reading is refused)."""
  try:
   if self._caller is not None:HachTl2300epaSession(self,self._caller)._guard(capability_id,arguments)
   return self.invoke(capability_id,**arguments)
  except ActionRejected as exc:raise _panel_ui.PanelRejected(str(exc)) from None
 def panel_time(self):return self.time_s
 def _panel_command(self,capability,a):
  validate(PANEL_CAPS[capability]['input_schema'],a)
  if _panel_ui.check_arguments(PANEL_CAPS[capability]['input_schema'],a):raise ActionRejected('INVALID_ARGUMENT')
  try:
   if capability=='panel.press':out=dict(accepted=bool(self.panel.press(a['input_id'],float(a.get('duration_s',.1)))))
   elif capability=='panel.touch':out=dict(accepted=bool(self.panel.touch(a['display_id'],a['x'],a['y'],float(a.get('duration_s',.1)),(a['to_x'],a['to_y']) if 'to_x' in a and 'to_y' in a else None)))
   elif capability=='panel.capture':return self._panel_capture(a['display_id'])
   else:return dict(frame=self.panel.frame())
  except _panel_ui.PanelError:raise ActionRejected('INVALID_ARGUMENT') from None
  self.last_activity_s=self.time_s;self.revision+=1                      # any gesture is operator activity (sleep timer)
  return out
 def _panel_capture(self,display_id):
  """Close-up camera of a display: a PNG file named by its content (large data is returned as a file reference)."""
  data,w,h=self.panel.png_bytes(display_id);sha=hashlib.sha256(data).hexdigest()
  d=Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir())/'chemistry_sim_panel'/self.instance_id
  d.mkdir(parents=True,exist_ok=True);path=d/f'{display_id}_{sha[:16]}.png'
  if not path.exists():path.write_bytes(data)
  return dict(display_id=display_id,time_s=self.time_s,image=dict(path=str(path),format='png',width=w,height=h,sha256=sha))


def _date_ok(s):
 try:_dt.datetime.strptime(s,'%Y-%m-%d');return True
 except (TypeError,ValueError):return False


def _datetime_ok(s):
 try:_dt.datetime.strptime(s,'%Y-%m-%dT%H:%M');return True
 except (TypeError,ValueError):return False


_PANELS=weakref.WeakKeyDictionary()                                        # runtime -> its panel (kept out of the runtime's own data)
PANEL_SPEC=_panel_ui.load_spec(ASSET_DIR/'panel_ui.json',capabilities=set(CAPS)|set(TERMINAL_CAPS))
PANEL_CAPS=_panel_ui.capability_definitions(PANEL_SPEC)      # panel.press / panel.touch / panel.capture / panel.frame
PANEL_GESTURES=[k for k in PANEL_CAPS if k not in ('panel.capture','panel.frame')]
ALL_CAPS={**CAPS,**TERMINAL_CAPS}


class HachTl2300epaRuntime(_Tl2300Terminal):
 def __init__(self,instance_id='tl2300_01',*,initial_powered=False,prior_powered_time_s=0.):
  # Trusted world initialization, not an agent capability or elapsed simulation time.
  if type(initial_powered) is not bool or isinstance(prior_powered_time_s,bool) or not isinstance(prior_powered_time_s,(int,float)) or not math.isfinite(prior_powered_time_s) or prior_powered_time_s<0 or (not initial_powered and prior_powered_time_s!=0):raise ValueError('invalid initial power history')
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=initial_powered;self.warmup_s=float(prior_powered_time_s);self.lid_angle=0.;self.filter_installed=True
  self.ratio=True;self.average_count=1;self.mode='single';self.phase='idle';self.elapsed_s=0.;self.operation_id=None;self._operation_count=0
  self.sample=None;self.bubbles=0.;self.indicated=None;self.stable=False;self.gain=1.;self.calibrated=False;self.raw_mean=None;self.window=None;self.window_elapsed=0.;self.integral=0.;self.window_first=None
  self.input_kg=0.;self.removed_kg=0.;self.input_m3=0.;self.removed_m3=0.
  self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']}
  self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
  self._caller=None                                                    # session principal while a session call runs (panel gestures act as that caller)
  self._terminal_init()
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return HachTl2300epaSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _base(self):return self.standard['value_ntu'] if self.standard else 0. if not self.sample else self.sample['reference']['value']*self.sample['particle_response_gain']
 def _transmission(self):return 1. if not self.sample else math.exp(-self.sample['color_absorbance'])
 def _cal_status(self):return self.curve_name if self.curve else 'rapid_0_40' if self.calibrated and self.indicated is not None and self.indicated<=40 else 'rapid_outside_validated_range' if self.calibrated else 'uncalibrated'
 def _values(self):
  s=self.sample
  r={'power.enabled':self.powered,'lid.angle_rad':self.lid_angle,'filter.installed':self.filter_installed,'warmup.elapsed_s':self.warmup_s,'warmup.ready':self.powered and self.warmup_s>=(1800 if self.ratio else 3600),'method.ratio_enabled':self.ratio,'method.average_count':self.average_count,'method.mode':self.mode,'cycle.phase':self.phase,'cycle.elapsed_s':self.elapsed_s,'turbidity.indicated_ntu':self.indicated,'turbidity.stable':self.stable,'calibration.gain':self.gain,'calibration.status':self._cal_status(),'sample.loaded':s is not None,'sample.temperature_k':s['temperature_k'] if s else None,'sample.reference_ntu':s['reference']['value'] if s else None,'sample.bubble_excess_ntu':self.bubbles,'detector.normalized_90':(self._base()+self.bubbles)*self._transmission()/4000.,'detector.normalized_transmitted':self._transmission()}
  for axis in ['kg','m3']:
   r[f'sample.input_{axis}']=getattr(self,'input_'+axis);r[f'sample.removed_{axis}']=getattr(self,'removed_'+axis);r[f'sample.retained_{axis}']=0. if not s else s['mass_kg' if axis=='kg' else 'volume_m3']
  r.update(self._terminal_values())
  return r
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('evaluator','debug')})
 def debug_snapshot(self):return dict(state=self.read('evaluator'),sample=copy.deepcopy(self.sample))
 def _measure(self):
  quality='unavailable' if self.indicated is None else 'stale' if not self.powered or not self.stable or self.phase=='cancelled' else 'estimated'
  tolerance=None if self.indicated is None else (.02*self.indicated+.01 if self.indicated<=1000 else .05*self.indicated)
  return dict(ntu=self.indicated,stable=self.stable,phase=self.phase,quality=quality,calibration_status=self._cal_status(),reference_accuracy_ntu=tolerance,sample_window_start_s=self.window[0] if self.window else None,sample_window_end_s=self.window[1] if self.window else None,tss_kg_m3=None,evidence='measured_or_nominal_reference_with_synthetic_optics; accuracy_is_vendor_reference_not_guarantee',template_source=TEMPLATE_REL)
 def _invalidate(self):self.indicated=None;self.stable=False;self.raw_mean=None;self.window=None;self.shown=None;self.rst_sum=0.;self.rst_n=0
 def invoke(self,capability,**a):
  if capability in PANEL_CAPS:return self._panel_command(capability,a)
  if capability in TERMINAL_CAPS:
   validate(TERMINAL_CAPS[capability][0],a);r=self._terminal(capability,a);self.revision+=1
   if capability not in ('fault.inject','fault.clear'):self.last_activity_s=self.time_s   # an injected fault is not operator activity
   return copy.deepcopy(r)
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if self.phase=='running' and capability not in ('power.set','lid.set_angle','acquisition.stop','turbidity.measure'):raise ActionRejected('BUSY')
  if capability=='power.set':
   was=self.powered;self.powered=a['enabled'];self._power_changed(was)
   if not self.powered:
    self.warmup_s=0.;self.stable=False
    if self.phase=='running':self.phase='cancelled'
   r=dict(a)
  elif capability=='lid.set_angle':
   self.lid_angle=a['angle_rad']
   if self.lid_angle>1e-4:
    self.stable=False
    if self.phase=='running':self.phase='cancelled'
   r=dict(a)
  elif capability=='filter.set_installed':
   if self.powered:raise ActionRejected('PRECONDITION_FAILED')
   self.filter_installed=a['installed'];self._invalidate();r=dict(a)
  elif capability=='sample.configure':
   rec=self.templates.get(a['substance_id'])
   if self.lid_angle<1.4 or self.sample or self.standard or not rec or not rec['runtime_eligible']:raise ActionRejected('PRECONDITION_FAILED')
   self.sample=copy.deepcopy(a);self.sample['reference']=copy.deepcopy(rec);self.bubbles=a['bubble_excess_ntu'];self.input_kg+=a['mass_kg'];self.input_m3+=a['volume_m3'];self._invalidate();r={'configured':True}
  elif capability=='sample.unload':
   if self.lid_angle<1.4 or not self.sample:raise ActionRejected('PRECONDITION_FAILED')
   self.removed_kg+=self.sample['mass_kg'];self.removed_m3+=self.sample['volume_m3'];self.sample=None;self.bubbles=0.;self._invalidate();r={'unloaded':True}
  elif capability=='method.configure':
   if type(a['average_count']) is not int:raise ActionRejected('INVALID_ARGUMENT')
   self.ratio=a['ratio_enabled'];self.average_count=a['average_count'];self.mode=a['mode'];self.rst=False;self.settings['reading.signal_avg']=False;self._invalidate();r={'configured':True}
  elif capability=='acquisition.start':
   if not self._values()['warmup.ready'] or not self.filter_installed or self.lid_angle>1e-4 or not self._holder() or (self.sample and not self.sample['clean_indexed_cell']) or self.hw_error is not None:raise ActionRejected('PRECONDITION_FAILED')
   self._operation_count+=1;self.operation_id=f'{self.instance_id}:ntu:{self._operation_count}';self.phase='running';self.elapsed_s=0.;self.window_elapsed=0.;self.integral=0.;self.window_first=self.bubbles;self._invalidate();r={'phase':self.phase,'operation_id':self.operation_id}
  elif capability=='acquisition.stop':
   if self.phase=='running':
    if self.mode=='continuous' and not self.procedure:self._reading_done()   # Done ends a continuous / RST reading: the last value is the reading
    self.phase='cancelled';self.stable=False
   r={'phase':self.phase}
  elif capability=='calibration.rapid':
   if not self.powered or self.warmup_s<3600 or not self.sample or self.sample['substance_id']!='stablcal_20' or self.sample['temperature_k']>313.15 or not self.stable or not self.raw_mean or self.lid_angle>1e-4 or not .1<=20/self.raw_mean<=10:raise ActionRejected('PRECONDITION_FAILED')
   self.gain=20/self.raw_mean;self.calibrated=True;self.indicated=20.;r={'gain':self.gain,'range_max_ntu':40.}
   self.curve=None;self.curve_name='stablcal_rapid';self.last_cal_s=self._now();self.shown={'ntu':20.,'stable':True}
   self._log('calibration',dict(curve=CURVES['stablcal_rapid']['label'],points=[{'standard_ntu':20.,'measured_raw':round(self.raw_mean,4)}],gain=self.gain))
  elif capability=='calibration.verify':
   if not self.powered or not self.stable or self.indicated is None or self.warmup_s<3600:raise ActionRejected('PRECONDITION_FAILED')
   d=self.indicated-a['reference_ntu'];return {'deviation_ntu':d,'within_five_percent':abs(d)<=.05*a['reference_ntu']+1e-12}
  else:return self._measure()
  if self.procedure and self.procedure.get('measuring') and self.phase!='running':self.procedure['measuring']=False   # lid opened / power off / stopped
  self.revision+=1;self.last_activity_s=self.time_s;return copy.deepcopy(r)
 def _power_changed(self,was):
  if self.powered and not was:
   self.power_epoch+=1;self.hw_error=self.hw_fault                     # p.33: an injected hardware fault / lid open / vial inside stops the self-check
   self.selfcheck={'start':self.time_s,'error':self.hw_fault if self.hw_fault is not None else 3 if self.lid_angle>1e-4 else 8 if self._holder() else None}
  if self.powered!=was:
   self.procedure=None;self.admin_unlocked=False;self.operator=None;self.timer['running']=False;self.pending=None;self.notice=None
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<=0:raise ValueError('finite positive dt_s required')
  left=float(dt_s)
  while left>1e-10:
   active=self.phase=='running';duration=self._window_s();h=min(left,duration-self.window_elapsed) if active else left
   old=self.bubbles;tau=self.sample['bubble_tau_s'] if self.sample else 1.;self.bubbles=old*math.exp(-h/tau)
   if self.powered:self.warmup_s+=h
   if active:
    factor=1. if self.ratio or self._window_s()==CAL_WINDOW_S else self._transmission();self.integral+=factor*(self._base()*h+old*tau*(1-math.exp(-h/tau)));self.window_elapsed+=h;self.elapsed_s+=h
    if self.window_elapsed>=duration-1e-9:
     self.raw_mean=self.integral/duration;v=self._calibrated(self.raw_mean);limit=4000 if self.ratio else 40
     self.indicated=round(v,3) if 0<=v<=limit+1e-9 else None
     self.stable=self.indicated is not None and abs(self.gain*factor*(self.window_first-self.bubbles))<=max(.01,.02*v)
     self.window=(self.time_s+h-duration,self.time_s+h)
     self._window_done()
     self.window_elapsed=0.;self.integral=0.;self.window_first=self.bubbles
   self._terminal_step(h);self.time_s+=h;left-=h
  self.revision+=1;return self.read('evaluator')
_CONTROL_GUARDED={'acquisition.stop','power.set','procedure.cancel'}   # commands that end or change another agent's reading (opening the lid is physical: not refused)


class HachTl2300epaSession:
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
        meta=export_hach_tl2300epa_contract_metadata()
        caps=dict(meta['capabilities'],**export_hach_tl2300epa_terminal_metadata()['capabilities'])
        return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,
          capabilities={k:v for k,v in caps.items() if v['visibility']=='agent' or self._role!='agent'},
          state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})

    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id); h=self._host; vals=h.read(self._role); ids=list(vals) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids): raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in vals for k in ids): raise ActionRejected('ACCESS_DENIED')
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},
          observations={k:dict(value=copy.deepcopy(vals[k]),unit=SLOTS[k][1],quality=h._measure()['quality'] if k=='turbidity.indicated_ntu' else ('unavailable' if vals[k] is None else ('estimated' if SLOTS[k][2]=='derived' else 'valid')),sample_time_s=h.window[1] if k=='turbidity.indicated_ntu' and h.window else h.time_s,sample_window_s=list(h.window) if k=='turbidity.indicated_ntu' and h.window else None) for k in ids})

    def invoke(self,*,instance_id,capability_id,arguments,request_id):
        try:
            return self._invoke(instance_id=instance_id,capability_id=capability_id,arguments=arguments,request_id=request_id)
        except ActionRejected as exc:
            return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,
                status='rejected',state_revision=self._host.revision,clock={'domain':'simulation','time_s':self._host.time_s},
                result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))

    def _invoke(self,*,instance_id,capability_id,arguments,request_id):
        self._target(instance_id); h=self._host
        if not isinstance(capability_id,str) or (capability_id not in ALL_CAPS and capability_id not in PANEL_CAPS): raise ActionRejected('UNKNOWN_CAPABILITY')
        vis,schema=(PANEL_CAPS[capability_id]['visibility'],PANEL_CAPS[capability_id]['input_schema']) if capability_id in PANEL_CAPS else (ALL_CAPS[capability_id][5],ALL_CAPS[capability_id][0])
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
        """An agent may not stop, switch off or change another agent's running reading (direct call or panel Done / power key / procedure
        Cancel); unowned readings and the evaluator are not restricted.  Opening the lid is a physical action and is not refused: the instrument
        itself cancels the reading."""
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

def export_hach_tl2300epa_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=TERMINAL_ZH.get(key),origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'HachTl2300epaSession.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    slots['turbidity.indicated_ntu']['sampling']=dict(mode='interval_mean',time_domain='simulation',window_description='最近完成的6.8或14秒光学均值窗口；纯读保留原始端点，过期读数不改成当前瞬时值')
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key=='acquisition.start'; pre=[]
        conditions={
         'acquisition.start':('预热、闭盖、EPA滤片、清洁索引管与样品',['warmup.ready','lid.angle_rad','filter.installed','sample.loaded'],'warmup>=1800 ratio_on or 3600 ratio_off; lid<=1e-4; filter and sample.clean_indexed_cell'),
         'sample.configure':('宿主开盖、空位、有效定量模板',['lid.angle_rad','sample.loaded'],'lid>=1.4 and empty and runtime_eligible'),
         'sample.unload':('宿主开盖移出已装样品',['lid.angle_rad','sample.loaded'],'lid>=1.4 and loaded'),
         'filter.set_installed':('断电维护滤片',['power.enabled'],'not powered'),
         'calibration.rapid':('1h预热、20NTU标准稳定、<=40C、闭盖',['warmup.elapsed_s','turbidity.stable','lid.angle_rad'],'powered and warmup>=3600 and stablcal_20 and stable and T<=313.15 and .1<=20/raw<=10'),
         'calibration.verify':('1h预热且最近读数稳定',['warmup.elapsed_s','turbidity.stable'],'powered and warmup>=3600 and stable and indicated is not None')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.hach_tl2300epa.HachTl2300epaSession.invoke',invocation=f'session.invoke(instance_id="tl2300_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；取消保留窗口及气泡衰减；连续模式须显式停止' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电不回滚已经发生的气泡衰减和完成的窗口。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    for key,extra in EXTRA_EFFECTS.items():                          # effects of the instrument commands on the terminal slots
        caps[key]['state_effects']+=[dict(state_id=k,timing='immediate',description_zh=z) for k,z in extra]
    mechanism=dict(component_ids=['tungsten_lamp','epa_filter','sample_cell','detector_90','detector_forward','detector_transmitted'],state_ids=['sample.reference_ntu','sample.bubble_excess_ntu','turbidity.indicated_ntu','sample.input_kg','sample.retained_kg','sample.removed_kg','sample.input_m3','sample.retained_m3','sample.removed_m3'],meaning_zh='实测NTU回放与非厂家颜色/气泡代理，不作NTU质量换算',implementation='lumped',equations=['T=exp(-A); S90=(NTU_reference*particle_gain+bubbles)*T/4000','ratio_on=4000*S90/T; ratio_off=4000*S90','bubbles=bubbles0*exp(-t/tau); exact time integral over 6.8 or 14 seconds','display=round(gain*average,3); range 0..4000 on, 0..40 off','mass_input=mass_retained+mass_removed; volume_input=volume_retained+volume_removed'],parameters={'stray_light':dict(value=0.,unit='NTU',meaning_zh='未标定暗电流/杂散光零值代理',evidence='assumption',source_ids=['simulation_assumption'],uncertainty=None)},source_ids=['manufacturer_primary','water_templates','simulation_assumption'],limitations=['前向探测器只有几何，无真实比率权重；颜色完全抵消仅教学代理。','定时完成不保证真实固件稳定；2%或0.01NTU漂移阈值是工程假设。','method.configure只有1/10次平均；触摸屏Signal Avg按5–15 s（单次）或5–90 s（连续）时间窗。','0.001NTU量化不代表高量程真实分辨率。','快速校准仅20NTU单点增益、暗电流0；触摸屏的校准流程实现RapidCal与满量程分段线性曲线（标准值检查按±50%假设），RST按累积均值、Bubble Reject只作用于显示与日志（见前面板层）。','无Mie求解、沉降、粒径/TSS推断或真实精度验证。'])
    _ann.annotate_export('hach_tl2300epa',caps,slots,{'turbidity_optics':mechanism},inputs={})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'turbidity_optics':mechanism})


EXTRA_EFFECTS={
 'power.set':[('selfcheck.state','开机自检（开盖：错误3；管内有样品：错误8）'),('session.operator','开关机注销操作员'),('security.state','开关机撤销管理员解锁'),
              ('procedure.state','开关机丢弃未保存的校准/核查流程'),('timer.state','开关机停止倒计时'),('display.reading','开关机清除待存读数与提示')],
 'sample.configure':[('display.reading','新样品清除显示读数')],'sample.unload':[('display.reading','取出样品清除显示读数')],
 'method.configure':[('device.settings','读数模式与比率随之改变，RST与Signal Avg关闭'),('display.reading','清除显示读数')],
 'acquisition.start':[('display.reading','开始读数')],
 'acquisition.stop':[('datalog.entries','连续/RST模式按Done结束：最后读数按Auto Store存入读数日志'),('display.reading','结束读数')],
 'calibration.rapid':[('datalog.entries','写校准日志'),('display.reading','显示标准值20 NTU'),('reminders.due','校准提醒重新计时')]}
TERMINAL_PRE={   # description, state ids, check
 'settings.configure':('开机；无测量进行；安全开启时受安全列表限制（操作员级别或管理员解锁）；开启安全须先设管理员密码；Signal Avg单次5–15 s、连续5–90 s；接受范围1–20 %或0.001 NTU到曲线量程的20 %',['power.enabled','cycle.phase','device.settings','security.state']),
 'clock.set':('开机；安全列表Setup级别',['power.enabled','security.state']),'security.set_password':('开机；安全开启时须管理员解锁；不超过10字符',['power.enabled','security.state']),
 'security.unlock':('开机；已设管理员密码且密码正确',['power.enabled','security.state']),
 'operator.save':('开机；ID 1–20字符不重复、最多30个；安全未开启时级别只能Off',['power.enabled','operators.list','device.settings']),
 'operator.delete':('开机；ID存在且未登录',['power.enabled','operators.list','session.operator']),'session.login':('开机；ID存在；有密码时须正确',['power.enabled','operators.list']),
 'session.logout':('开机',['power.enabled']),'sample_id.save':('开机；ID 1–20字符不重复、最多1000个；采样日期YYYY-MM-DD与时间HH:MM',['power.enabled','sample_ids.list']),
 'sample_id.delete':('开机；ID存在（null删除全部）',['power.enabled','sample_ids.list']),'sample_id.select':('开机；ID存在或null',['power.enabled','sample_ids.list']),
 'datalog.store':('开机；Auto Store关且有未保存的读数',['power.enabled','device.settings']),'datalog.comment':('开机；Comments设置开；条目存在；注释不超过40字符',['power.enabled','device.settings','datalog.entries']),
 'datalog.delete':('开机；安全列表Delete Data级别；单条须存在',['power.enabled','datalog.entries']),
 'calibration.start':('开机满60 min；安全列表Calibration级别；无测量进行',['power.enabled','warmup.elapsed_s','security.state']),
 'calibration.read':('校准流程中且未到Store；开机满60 min、闭盖、EPA滤片、样品座有标准或干净样品',['procedure.state','warmup.elapsed_s','lid.angle_rad','filter.installed','sample.loaded','standard.loaded']),
 'calibration.store':('校准流程所有标准（及核查标准）已测；增益0.1–10且信号随标准单调增',['procedure.state']),
 'verification.read':('开机预热完成（Ratio开30 min、关60 min）、闭盖、滤片、样品座有标准或干净样品；安全列表Verification级别',['warmup.ready','lid.angle_rad','filter.installed','sample.loaded','standard.loaded','security.state']),
 'procedure.cancel':('校准或核查流程中',['procedure.state']),'standard.load':('开盖且样品座空',['lid.angle_rad','sample.loaded','standard.loaded']),
 'standard.unload':('开盖且样品座有标准管',['lid.angle_rad','standard.loaded']),'timer.start':('开机',['power.enabled']),'timer.stop':('开机',['power.enabled']),
 'fault.inject':('评测者；任何状态（关机时在下次开机自检检出）',['power.enabled','fault.injected']),'fault.clear':('评测者；任何状态',['fault.injected'])}
TERMINAL_ERRORS=['POWER_OFF','ACCESS_LEVEL','PASSWORD_REQUIRED','OUT_OF_RANGE','INVALID_PASSWORD','NOT_FOUND','DUPLICATE','LIST_FULL','LOGGED_IN','NO_READING','WARMING_UP','INSTRUMENT_ERROR']
PANEL_PRECONDITION_ZH='面板按当前屏幕与手册规则处理手势：关机时只有电源键起作用；开机自检约10 s（开盖或管内有样品时停在硬件错误3/8，直到重新开机）；睡眠时第一次触摸只唤醒屏幕；被调用能力的前提同样适用（被拒时显示仪器自己的信息，手势不被接受）'
PANEL_LIMITATION_ZH='面板层按panel_ui.json（手册PDF页码证据）解释手势并调用本运行层的同名能力；手势不推进仿真时钟（长按只按duration_s判断）；手册未写之处的选择见panel_ui.json的assumptions。'


def _cap_entry(key,inp,out,components,meaning,kind,vis,example,effects,pre,errors,limitations,sources):
    return dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=list(components),availability='implemented',
      input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
      implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
      binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.hach_tl2300epa.HachTl2300epaSession.invoke',invocation=f'session.invoke(instance_id="tl2300_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
      preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
      model_effects=dict(mode='none',description_zh='此处仅声明运行过程；屏幕纹理与触摸联动属于后续Matterix步骤。',binding_ids=[]),
      execution=dict(mode='synchronous',time_domain='simulation',completion_criterion='设定写入或命令返回；不推进物理时钟（测量类在cycle.phase完成）',
        completion_state_ids=list(effects),timeout_s=None,cancellation='unsupported',retry='same_request_id',resources=['target_instance','principal_request_cache'],
        partial_failure_zh='拒绝不改任何状态（先检查再修改）。去重只在当前进程内存有效。'),
      errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in errors],example_arguments=copy.deepcopy(example),verification=[],limitations=limitations,source_ids=sources)


def export_hach_tl2300epa_terminal_metadata():
    """Capabilities of the touch-screen terminal (TERMINAL_CAPS) and of the front panel (PANEL_CAPS); the instrument's own eleven commands and all
    state slots are in export_hach_tl2300epa_contract_metadata()."""
    base=['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']
    caps={}
    for key,(inp,out,components,meaning,kind,vis,example,effects) in TERMINAL_CAPS.items():
        d,ids=TERMINAL_PRE[key]
        caps[key]=_cap_entry(key,inp,out,components,meaning,kind,vis,example,effects,[dict(description_zh=d,state_ids=ids,enforced_by='runtime',check=d)],base+TERMINAL_ERRORS,
                             ['触摸屏终端功能的最小模型（手册第5–8节）；U盘、打印机、条码枪与键盘不在模拟配置中；安全列表项目、声音事件、图标与颜色名单为假设（见panel_ui.json assumptions）。'],
                             ['manufacturer_primary','simulation_assumption'])
    effects={k:[x for x in row[7]] for k,row in {**CAPS,**TERMINAL_CAPS}.items()}
    fx=list(dict.fromkeys(x for c in _panel_ui.spec_capabilities(PANEL_SPEC) for x in effects.get(c,[])))
    for key,d in PANEL_CAPS.items():
        gesture=key in PANEL_GESTURES;comps=list(d['component_ids'])
        if key in ('panel.touch','panel.capture','panel.frame'):comps=sorted(set(comps)|{'display_digits','touch_read_zone'})
        caps[key]=_cap_entry(key,d['input_schema'],d['output_schema'],comps,d['function_zh'],d['kind'],d['visibility'],d['example_arguments'],fx if gesture else [],
                             [dict(description_zh=PANEL_PRECONDITION_ZH,state_ids=['power.enabled','selfcheck.state','display.sleeping'],enforced_by='runtime',check='panel_ui.json screens, gates and bindings')] if gesture else [],
                             ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','REQUEST_CONFLICT'],[PANEL_LIMITATION_ZH],['manufacturer_primary','simulation_assumption'])
        caps[key]['execution']['completion_criterion']='手势瞬时完成（不推进仿真时钟）；结果只说明是否被仪器接受，屏幕须用panel.capture查看' if gesture else '不推进物理时钟'
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps)

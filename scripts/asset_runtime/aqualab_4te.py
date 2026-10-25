"""4TE cold-mirror water activity: measured solution points, conservative water ledger.

1.1.0 device layer (AQUALAB 4 manual 18280-6, 'aq p.N' = PDF page; the 2019 Series 4 manual 13484-15 for the on-instrument %Moisture
calibration): device clock, regional formatting, autosave and the data store (view, annotate, delete all), beeps, serial print mode, product
isotherm models (select, offset, copy, delete), ISO 18787 mode, administrator / users / access, and the front panel (panel_ui.json beside the
contract, run by the generic engine _panel_ui.py on top of these capabilities: 128 x 64 LCD, four arrow keys, three round context keys, the
chamber lever and the power switch).  1.2.0: evaluator fault injection of the four error messages of aq p.58 Table 6, panel readings owned by
the gesturing caller."""
from __future__ import annotations
import copy,datetime,hashlib,json,math,os,tempfile,weakref
from asset_runtime import _annotations as _ann
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT,PHASE
def _rejected(code,detail):
    e=ActionRejected(code);e.detail=detail;return e   # 1.3.1: a refusal whose message names the cause
from asset_runtime.antonpaar_abbematadvanced import validate
ROOT=Path(__file__).resolve().parents[2];ASSET_TYPE_ID='instrument.aqualab_4te';CATALOG_ID='CHEM-007';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/aqualab_4te_water_activity_library/templates.json'
TEMP=number('K',288.15,323.15);AW=number('1',.03,1.);NULL_AW={'anyOf':[AW,{'type':'null'}]};ANGLE=number('rad',0,math.pi/2);NULL_NUM={'anyOf':[number('1',0),{'type':'null'}]}
MODE={'type':'string','enum':['single','continuous','custom','low_emitting','iso18787']}   # 1.1.0: + ISO 18787 (aq p.32)
RESULT=obj({'water_activity':NULL_AW,'temperature_k':TEMP,'equilibrated':BOOL,'phase':PHASE,'quality':TEXT,'evidence':TEXT,'template_source':TEXT,'moisture_dry_basis':NULL_NUM,'moisture_evidence':TEXT})
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','button_power'],'电源设定，断电取消；预热状态单独读出','device_command','agent',{'enabled':True},['power.enabled','cycle.phase','warmup.elapsed_s']),
 'thermal.set_temperature':(obj({'temperature_k':TEMP}),obj({'temperature_k':TEMP}),['sample_peltier','mirror_peltier'],'15–50C、0.1C步进目标，设置不推进温度','device_command','agent',{'temperature_k':298.15},['thermal.target_k','water_activity.indicated']),
 'lid.set_angle':(obj({'angle_rad':ANGLE}),obj({'angle_rad':ANGLE}),['lid'],'锁杆释放后才能开盖；被动盖宿主姿态，不是机器人技能','device_command','agent',{'angle_rad':math.pi/2},['lid.angle_rad']),
 'latch.set_angle':(obj({'angle_rad':ANGLE}),obj({'angle_rad':ANGLE}),['lever_lock'],'闭盖后锁定READ；释放锁杆取消采集','device_command','agent',{'angle_rad':0.},['latch.angle_rad','cycle.phase']),
 'sample.configure':(obj({'substance_id':TEXT,'record_index':number('1',0),'mass_kg':number('kg',1e-5,.02),'volume_m3':number('m^3',1e-7,7e-6),'matrix_id':TEXT,'initial_temperature_k':TEMP,'equilibration_tau_s':number('s',5,600),'volatile_interference':BOOL}),obj({'configured':BOOL}),['sample_cup','cup_interface'],'宿主装样，选择实测溶液组成；质量和体积为独立输入，非隐含密度换算','configuration','evaluator',{'substance_id':'sodium_chloride','record_index':1,'mass_kg':.003,'volume_m3':3e-6,'matrix_id':'salt','initial_temperature_k':298.15,'equilibration_tau_s':35.,'volatile_interference':False},['sample.loaded','sample.input_kg','sample.retained_kg','water_activity.indicated']),
 'sample.unload':(obj({}),obj({'unloaded':BOOL}),['sample_cup','cup_interface'],'开盖后宿主移出样品，守恒账本保留实际水分交换','configuration','evaluator',{},['sample.loaded','sample.retained_kg','sample.removed_kg']),
 'method.configure':(obj({'mode':MODE,'stability_delta':number('1',.0003,.02),'temperature_tolerance_k':number('K',.1,4.),'consecutive_readings':number('1',1,9)}),obj({'configured':BOOL}),['display_digits'],'单次、连续、自定义与低释放模式的平衡代理，非厂家完整算法','configuration','agent',{'mode':'single','stability_delta':.0005,'temperature_tolerance_k':.5,'consecutive_readings':1},['method.mode','equilibrium.stable']),
 'calibration.configure':(obj({'offset_aw':number('1',-.03,.03),'source':TEXT}),obj({'offset_aw':number('1',-.03,.03)}),['chilled_mirror','display_digits'],'有来源的线性偏置代理，不宣称真实工厂校准','configuration','agent',{'offset_aw':0.,'source':'synthetic zero-offset example'},['calibration.offset_aw']),
 'calibration.verify':(obj({'reference_aw':AW}),obj({'deviation_aw':number('1',-1,1),'within_vendor_tolerance':BOOL}),['chilled_mirror'],'完成预热且最近平衡后对已知标准核查，不自动调参','query','agent',{'reference_aw':.92},[]),
 'isotherm.configure':(obj({'matrix_id':TEXT,'temperature_k':TEMP,'source':TEXT,'points':{'type':'array','minItems':2,'maxItems':128,'items':obj({'water_activity':AW,'moisture_dry_basis':number('1',0)})}}),obj({'configured':BOOL}),['display_digits'],'仅输入有来源的特定产品等温线，适用温度内插，不提供通用含水率','configuration','agent',{'matrix_id':'demo','temperature_k':298.15,'source':'synthetic test only','points':[{'water_activity':.1,'moisture_dry_basis':.01},{'water_activity':.99,'moisture_dry_basis':.4}]},['isotherm.configured']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['chilled_mirror','optical_sensor','thermopile','chamber_fan','display_digits'],'闭盖锁紧后按实际稳定判据采集；典型5min非固定周期','device_command','agent',{},['cycle.phase','cycle.elapsed_s','equilibrium.stable']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['display_digits'],'停止采集，水分转移和热过程不回滚','device_command','agent',{},['cycle.phase']),
 'water_activity.measure':(obj({}),RESULT,['chilled_mirror','thermopile','display_digits'],'纯读水活度、样温和平衡状态；不泄露样品真值','query','agent',{},[]),
}
SLOTS={
 'power.enabled':(BOOL,'1','setpoint','agent',['power_inlet'],('power.set','enabled')),
 'thermal.target_k':(TEMP,'K','setpoint','agent',['sample_peltier'],('thermal.set_temperature','temperature_k')),
 'thermal.block_k':(TEMP,'K','process_truth','evaluator',['sample_block'],None),
 'thermal.sample_k':(TEMP,'K','process_truth','evaluator',['sample_cup'],None),
 'thermal.indicated_temperature_k':(TEMP,'K','derived','agent',['thermopile','display_digits'],None),
 'lid.angle_rad':(ANGLE,'rad','scene','agent',['lid'],('lid.set_angle','angle_rad')),
 'latch.angle_rad':(ANGLE,'rad','scene','agent',['lever_lock'],('latch.set_angle','angle_rad')),
 'warmup.elapsed_s':(number('s',0),'s','derived','agent',['electronics'],None),
 'warmup.ready':(BOOL,'1','derived','agent',['electronics'],None),
 'method.mode':(MODE,'1','setpoint','agent',['display_digits'],None),
 'cycle.phase':(PHASE,'1','derived','agent',['display_digits'],None),
 'cycle.elapsed_s':(number('s',0),'s','derived','agent',['display_digits'],None),
 'equilibrium.stable':(BOOL,'1','derived','agent',['display_digits'],None),
 'water_activity.indicated':(NULL_AW,'1','derived','agent',['chilled_mirror','display_digits'],None),
 'dewpoint.indicated_k':({'anyOf':[number('K',200,323.15),{'type':'null'}]},'K','derived','agent',['chilled_mirror'],None),
 'calibration.offset_aw':(number('1',-.03,.03),'1','setpoint','agent',['chilled_mirror'],None),
 'isotherm.configured':(BOOL,'1','derived','agent',['display_digits'],None),
 'sample.loaded':(BOOL,'1','derived','agent',['sample_cup'],None),
 'sample.input_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_cup'],None),
 'sample.retained_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_cup'],None),
 'sample.removed_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_cup'],None),
 'water.input_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_cup'],None),
 'water.sample_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_cup'],None),
 'water.headspace_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_block'],None),
 'water.removed_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_cup'],None),
 'water.ambient_in_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_block'],None),
 'water.ambient_out_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_block'],None),
}
# ---------------------------------------------------------------- 1.1.0 device layer: settings, clock, data store, models, users (aq p.25-38)
def _fmt_date(iso,fmt):
    d=datetime.datetime.fromisoformat(iso);return f"{d:%m/%d/%y}" if fmt=='mm/dd/yy' else f"{d:%d/%m/%y}"
ASSET_VERSION='1.3.1'   # 1.3.1 (2026-10-22): sample example = the template's headline record; refusal messages
ASSET_DIR=ROOT/'assets/expansion_batch04/t4_measure/aqualab_4te'
SERIAL='AL400000001'                                                    # assumption: serial number shown on About (format of aq p.37 Figure 44)
LID_SERIAL='ALB00000001'
FIRMWARE='AS4 3.00.11'                                                  # aq p.37 Figure 44
CLOCK_EPOCH=datetime.datetime(2026,1,1,8,0,0)                           # assumption: device clock at simulation time 0
MAX_RECORDS=8000                                                        # aq p.36
SETTINGS={'temperature_unit':['C','F'],'date_format':['mm/dd/yy','dd/mm/yy'],'time_format':['12h','24h'],'language':['English'],
          'autosave':[True,False],'beeps':['four','continuous','none'],'print_mode':['off','on','detailed']}   # aq p.34, p.36
SETTINGS_DEFAULT={'temperature_unit':'C','date_format':'mm/dd/yy','time_format':'12h','language':'English','autosave':True,'beeps':'four','print_mode':'off'}
ACCESS=['calibration','temperature','temp_eq','sensor','mode','date_time','region','password','autosave','beeps','delete']   # aq p.32 lockable options
NAME={'type':'string','minLength':1,'maxLength':20};PASSWORD={'type':'string','minLength':1,'maxLength':16};LINE={'type':'string','maxLength':20}
RECORD=obj({'seq':number('1',1),'name':LINE,'water_activity':NULL_AW,'temperature_k':TEMP,'test_time_s':number('s',0),'user':TEXT,'datetime':TEXT,
            'sensor':TEXT,'moisture_dry_basis':NULL_NUM,'model':{'anyOf':[TEXT,{'type':'null'}]}})
SETTINGS_SCHEMA=obj({k:({'type':'boolean'} if k=='autosave' else {'type':'string','enum':v}) for k,v in SETTINGS.items()},optional=tuple(SETTINGS))
ACCESS_SCHEMA=obj({k:BOOL for k in ACCESS})
USERS=obj({'admin_set':BOOL,'current':{'anyOf':[TEXT,{'type':'null'}]},'users':{'type':'array','maxItems':20,'items':obj({'name':TEXT,'access':ACCESS_SCHEMA})}})
MODEL_NAME={'anyOf':[NAME,{'type':'null'}]}
FAULTS={'dew_point_sensor':('Dew point sensor failure',False),'crystal':('Crystal Failure. See Manual for options.',True),   # aq p.58 (54) Table 6;
        'contaminated_mirror':('Contaminated mirror.',False),'firmware':('Firmware is corrupted. See Manual for options.',False)}  # True: a power cycle clears it
FAULT={'anyOf':[{'type':'string','enum':list(FAULTS)},{'type':'null'}]}
DEVICE_CAPS={
 'settings.configure':(obj({'settings':SETTINGS_SCHEMA}),obj({'settings':SETTINGS_SCHEMA}),['display_digits'],'设备设置（温度单位、日期格式、12/24小时、语言、自动保存、结束提示音、串口打印模式；aq p.34、p.36）：只改给出的项，断电保留','configuration','agent',{'settings':{'autosave':True}},['device.settings']),
 'clock.set_date':(obj({'year':number('1',2000,2099),'month':number('1',1,12),'day':number('1',1,31)}),obj({'datetime':TEXT}),['electronics'],'设定仪器日期（aq p.33 Date）；时刻不变，记录随每次读数保存','configuration','agent',{'year':2026,'month':3,'day':15},['device.datetime']),
 'clock.set_time':(obj({'hour':number('1',0,23),'minute':number('1',0,59)}),obj({'datetime':TEXT}),['electronics'],'设定仪器时刻（aq p.34 Time，秒归零）','configuration','agent',{'hour':9,'minute':30},['device.datetime']),
 'data.save':(obj({'name':LINE}),obj({'seq':number('1',1)}),['display_digits'],'读数结束后、下次测量前手动保存/注释最近读数（aq p.13、p.36）：已自动保存的读数改名并按当前产品模型更新含水率，否则新存一条','device_command','agent',{'name':'SAMPLE5'},['data.records','data.last_reading']),
 'data.delete_all':(obj({}),obj({'deleted':number('1',0)}),['electronics'],'删除全部已存读数（aq p.34；不能单条删除，删除不可恢复）','device_command','agent',{},['data.records','data.last_reading']),
 'beeper.silence':(obj({}),obj({'beeping':BOOL}),['electronics'],'AUDIO OFF：停止结束提示音（aq p.25 Table 2）','device_command','agent',{},['beeper.on']),
 'isotherm.select':(obj({'name':MODEL_NAME}),obj({'name':MODEL_NAME}),['display_digits'],'选择用于含水率的产品等温线模型（测量页UP/DOWN、%Moisture，aq p.7、p.33；null为不显示含水率）','configuration','agent',{'name':None},['isotherm.selected','isotherm.configured']),
 'isotherm.set_offset':(obj({'name':NAME,'offset_dry_basis':number('1',-1,1)}),obj({'name':TEXT,'offset_dry_basis':number('1',-1,1)}),['display_digits'],'模型含水率线性偏置（2019手册2.5.4 Edit与2.5.5 Defaults=0）：之后该模型的含水率加此偏置','configuration','agent',{'name':'demo','offset_dry_basis':0.},['isotherm.models']),
 'isotherm.copy':(obj({'source':NAME,'name':NAME,'offset_dry_basis':number('1',-1,1)}),obj({'name':TEXT}),['display_digits'],'以已有模型为底建立新模型并带偏置（2019手册2.5.4 New）','configuration','agent',{'source':'demo','name':'demo B','offset_dry_basis':0.},['isotherm.models']),
 'isotherm.delete':(obj({'name':NAME}),obj({'name':TEXT}),['display_digits'],'从仪器删除模型（2019手册2.5.6，不可恢复）；若正被选用则改为不显示含水率','configuration','agent',{'name':'demo'},['isotherm.models','isotherm.selected','isotherm.configured']),
 'users.set_admin_password':(obj({'password':PASSWORD}),obj({'admin_set':BOOL}),['electronics'],'设定或更改管理员密码（aq p.30-31；忘记后须送厂复位）','configuration','agent',{'password':'admin1'},['users.state']),
 'users.add':(obj({'name':NAME,'password':PASSWORD}),obj({'name':TEXT}),['electronics'],'管理员新增用户（User Setup，aq p.31；须先设管理员）','configuration','agent',{'name':'Jean Paul','password':'jp'},['users.state']),
 'users.set_password':(obj({'name':NAME,'password':PASSWORD}),obj({'name':TEXT}),['electronics'],'更改用户密码（User Setup > Password）','configuration','agent',{'name':'Jean Paul','password':'jp2'},['users.state']),
 'users.set_access':(obj({'name':NAME,'access':ACCESS_SCHEMA}),obj({'name':TEXT}),['electronics'],'用户的Access：逐项锁定配置选项（true=锁定；aq p.32）','configuration','agent',{'name':'Jean Paul','access':{k:False for k in ACCESS}},['users.state']),
 'users.delete':(obj({'name':NAME}),obj({'name':TEXT}),['electronics'],'删除用户','configuration','agent',{'name':'Jean Paul'},['users.state']),
 'users.login':(obj({'name':NAME,'password':PASSWORD}),obj({'current':TEXT}),['electronics'],'用户（或Admin）登录：之后读数记录该用户，其Access锁定的选项在面板上不可进入（登录流程手册未写，假设）','device_command','agent',{'name':'Admin','password':'admin1'},['users.state']),
 'fault.inject':(obj({'code':{'type':'string','enum':list(FAULTS)},'persistent':BOOL},optional=('persistent',)),obj({'fault':FAULT}),['electronics'],
                 '评测者：注入手册p.58 Table 6的故障信息（Dew point sensor failure、Crystal Failure、Contaminated mirror、Firmware is corrupted）：屏幕显示该信息，'
                 '按键无反应（电源开关与锁杆除外），进行中的读数取消，不能开始读数；Crystal Failure断电重启即清除（persistent=True时仍在），其余须维修或清洁镜面（fault.clear）',
                 'simulation','evaluator',{'code':'crystal'},['device.fault','cycle.phase']),
 'fault.clear':(obj({}),obj({'fault':FAULT}),['electronics'],'评测者：排除注入的故障（代表Addium维修，或Contaminated mirror时按手册4.2清洁镜面与样品室）',
                'simulation','evaluator',{},['device.fault']),
 'users.logout':(obj({}),obj({'current':{'anyOf':[TEXT,{'type':'null'}]}}),['electronics'],'注销当前用户','device_command','agent',{},['users.state']),
}
DEVICE_SLOTS={
 'device.settings':(SETTINGS_SCHEMA,'1','setpoint','agent',['display_digits'],None),
 'device.datetime':(TEXT,'1','derived','agent',['electronics'],None),
 'data.records':({'type':'array','maxItems':MAX_RECORDS,'items':RECORD},'1','derived','agent',['electronics'],None),
 'data.last_reading':({'anyOf':[RECORD,{'type':'null'}]},'1','derived','agent',['display_digits'],None),
 'beeper.on':(BOOL,'1','derived','agent',['electronics'],None),
 'serial.output':({'type':'array','maxItems':50,'items':TEXT},'1','derived','agent',['rs232_port'],None),
 'isotherm.models':({'type':'array','maxItems':64,'items':obj({'name':TEXT,'matrix_id':TEXT,'temperature_k':TEMP,'offset_dry_basis':number('1',-1,1)})},'1','derived','agent',['display_digits'],None),
 'isotherm.selected':(MODEL_NAME,'1','setpoint','agent',['display_digits'],None),
 'users.state':(USERS,'1','derived','agent',['electronics'],None),
 'device.fault':(FAULT,'1','derived','agent',['display_digits'],None),
}
SLOTS.update(DEVICE_SLOTS)
CAPS['acquisition.start'][7].extend(['data.last_reading','beeper.on'])     # a new test ends the save window of the last reading
CAPS['isotherm.configure'][7].extend(['isotherm.models','isotherm.selected'])   # the supplied isotherm is stored as a model and selected
DEVICE_PRE={'clock.set_date':('日期须存在（如无2月30日）；无测量进行',['cycle.phase']),'clock.set_time':('无测量进行',['cycle.phase']),
            'data.save':('有最近完成的读数且下一次测量尚未开始；存储未满8000条',['data.last_reading','data.records']),
            'isotherm.select':('模型在仪器上（null不选）',['isotherm.models']),'isotherm.set_offset':('模型在仪器上；无测量进行',['isotherm.models','cycle.phase']),
            'isotherm.copy':('源模型在仪器上、新名称未用；无测量进行',['isotherm.models','cycle.phase']),'isotherm.delete':('模型在仪器上；无测量进行',['isotherm.models','cycle.phase']),
            'users.add':('已设管理员密码；名称未用（Admin保留）',['users.state']),'users.set_password':('用户存在',['users.state']),'users.set_access':('用户存在',['users.state']),
            'users.delete':('用户存在',['users.state']),'users.login':('用户（或Admin）存在且密码正确',['users.state']),
            'settings.configure':('无测量进行',['cycle.phase']),'data.delete_all':('无测量进行',['cycle.phase']),'users.set_admin_password':('无测量进行',['cycle.phase']),
            'fault.inject':('评测者；任何时候（断电时开机后显示）',[]),'fault.clear':('评测者',['device.fault'])}
DEVICE_CAP_ERRORS={'clock.set_date':['BUSY'],'clock.set_time':['BUSY'],'data.save':['NO_READING','MEMORY_FULL'],'isotherm.select':['MODEL_NOT_FOUND'],
                   'isotherm.set_offset':['MODEL_NOT_FOUND','BUSY'],'isotherm.copy':['MODEL_NOT_FOUND','NAME_EXISTS','BUSY'],'isotherm.delete':['MODEL_NOT_FOUND','BUSY'],
                   'users.add':['ADMIN_REQUIRED','NAME_EXISTS','BUSY'],'users.set_password':['USER_NOT_FOUND','BUSY'],'users.set_access':['USER_NOT_FOUND','BUSY'],
                   'users.delete':['USER_NOT_FOUND','BUSY'],'users.login':['USER_NOT_FOUND','WRONG_PASSWORD','BUSY'],'users.logout':[],'beeper.silence':[],
                   'settings.configure':['BUSY'],'data.delete_all':['BUSY'],'users.set_admin_password':['BUSY'],'fault.inject':[],'fault.clear':[]}
DEVICE_ERRORS={'NO_READING':'没有可保存的最近读数（下一次测量已开始或尚未完成读数）','MEMORY_FULL':'已存8000条读数，存储已满','MODEL_NOT_FOUND':'仪器上没有该名称的模型',
               'NAME_EXISTS':'同名模型或用户已存在','ADMIN_REQUIRED':'须先设定管理员密码','USER_NOT_FOUND':'没有该用户','WRONG_PASSWORD':'密码不正确'}

from asset_runtime import _panel_ui                                     # noqa: E402  (front-panel engine shared by every panel asset)
PANEL_PATH=ASSET_DIR/'panel_ui.json'
PANEL_SPEC=None if os.environ.get('AQUALAB_PANEL_BUILD')=='1' or not PANEL_PATH.is_file() else _panel_ui.load_spec(PANEL_PATH,capabilities=set(CAPS)|set(DEVICE_CAPS))
PANEL_CAPS=_panel_ui.capability_definitions(PANEL_SPEC) if PANEL_SPEC else {}   # panel.press / switch / capture / frame (kept out of CAPS)
PANEL_HOOKS={}
_PANELS=weakref.WeakKeyDictionary()
_PANEL_EFFECTS=list(dict.fromkeys(x for c in (_panel_ui.spec_capabilities(PANEL_SPEC) if PANEL_SPEC else []) for x in {**CAPS,**DEVICE_CAPS}[c][7]))
ALL_CAPS={**CAPS,**DEVICE_CAPS,**{k:(d['input_schema'],d['output_schema'],d['component_ids'],d['function_zh'],d['kind'],d['visibility'],d['example_arguments'],
                                    [] if k in ('panel.capture','panel.frame') else _PANEL_EFFECTS) for k,d in PANEL_CAPS.items()}}


def psat(t):
    c=t-273.15;return 610.94*math.exp(17.625*c/(243.04+c))
def dewpoint(p):
    x=math.log(max(p,1e-12)/610.94);return 273.15+243.04*x/(17.625-x)
class Aqualab4teRuntime:
    def __init__(self,instance_id='aqualab4te_01'):
        self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.warmup_s=0.
        self.target_k=298.15;self.block_k=298.15;self.sample_k=298.15;self.lid_angle=0.;self.latch_angle=0.
        self.mode='single';self.delta=.0005;self.temp_tolerance=.5;self.consecutive=1;self.offset=0.;self.isotherm=None
        self.phase='idle';self.elapsed_s=0.;self.operation_id=None;self._operation_count=0;self.stable=False;self.indicated_aw=None;self.indicated_dew=None;self.history=[];self._stable_count=0
        self.templates=json.loads((ROOT/TEMPLATE_REL).read_text())['substances'];self.sample=None
        self.input_kg=0.;self.removed_kg=0.;self.water_input=0.;self.water_removed=0.;self.sample_water=0.;self.sample_dry=0.
        self.vapor=psat(298.15)*.5*20e-6*.01801528/(8.314462618*298.15);self.ambient_in=self.vapor;self.ambient_out=0.
        self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._caller=None
        # 1.1.0 device layer (non-volatile: kept through power cycles)
        self.settings=dict(SETTINGS_DEFAULT);self.clock_offset_s=0.;self.records=[];self._seq=0;self.last_reading=None;self.beep_until=-1.;self.serial_out=[]
        self.models={};self.model_selected=None;self.admin_password=None;self.users={};self.current_user=None;self.power_epoch=0;self._was_stable=False
        self.panel_capture_dir=None;self.fault=None
    @property
    def panel(self):
        """The front panel of this instance (kept outside the instance dict, which tests compare as the runtime state)."""
        if self not in _PANELS:
            _PANELS[self]=_panel_ui.PanelUI(PANEL_SPEC,self,hooks=PANEL_HOOKS)
        return _PANELS[self]
    def register_principal(self,principal_id,visibility='agent'):
        if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id='__public_agent__'):
        if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return Aqualab4teSession(self,principal_id)
    def describe(self):return self.session().describe(instance_id=self.instance_id)
    def _values(self):
        return {'power.enabled':self.powered,'thermal.target_k':self.target_k,'thermal.block_k':self.block_k,'thermal.sample_k':self.sample_k,'thermal.indicated_temperature_k':round(self.sample_k-273.15,2)+273.15,'lid.angle_rad':self.lid_angle,'latch.angle_rad':self.latch_angle,'warmup.elapsed_s':self.warmup_s,'warmup.ready':self.warmup_s>=900,'method.mode':self.mode,'cycle.phase':self.phase,'cycle.elapsed_s':self.elapsed_s,'equilibrium.stable':self.stable,'water_activity.indicated':self.indicated_aw,'dewpoint.indicated_k':self.indicated_dew,'calibration.offset_aw':self.offset,'isotherm.configured':self.isotherm is not None,'sample.loaded':self.sample is not None,'sample.input_kg':self.input_kg,'sample.retained_kg':self.sample_water+self.sample_dry,'sample.removed_kg':self.removed_kg,'water.input_kg':self.water_input,'water.sample_kg':self.sample_water,'water.headspace_kg':self.vapor,'water.removed_kg':self.water_removed,'water.ambient_in_kg':self.ambient_in,'water.ambient_out_kg':self.ambient_out,
                'device.settings':dict(self.settings),'device.datetime':self._now().isoformat(timespec='seconds'),'data.records':copy.deepcopy(self.records),
                'data.last_reading':copy.deepcopy(self.last_reading),'beeper.on':self.time_s<self.beep_until,'serial.output':list(self.serial_out),
                'isotherm.models':[dict(name=n,matrix_id=m['matrix_id'],temperature_k=m['temperature_k'],offset_dry_basis=m['offset']) for n,m in self.models.items()],
                'isotherm.selected':self.model_selected,
                'users.state':dict(admin_set=self.admin_password is not None,current=self.current_user,users=[dict(name=n,access=dict(u['access'])) for n,u in self.users.items()]),
                'device.fault':self.fault['code'] if self.fault and self.powered else None}
    def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('evaluator','debug')})
    def debug_snapshot(self):return dict(state=self.read('evaluator'),sample=copy.deepcopy(self.sample))
    def _measure(self):
        moisture=None
        if self.sample and self.isotherm and self.indicated_aw is not None and self.isotherm['matrix_id']==self.sample['matrix_id'] and abs(self.sample_k-self.isotherm['temperature_k'])<.05:
            for a,b in zip(self.isotherm['points'],self.isotherm['points'][1:]):
                if a['water_activity']<=self.indicated_aw<=b['water_activity']:
                    moisture=a['moisture_dry_basis']+(b['moisture_dry_basis']-a['moisture_dry_basis'])*(self.indicated_aw-a['water_activity'])/(b['water_activity']-a['water_activity'])+self.isotherm.get('offset',0.);break
        quality='unavailable' if self.indicated_aw is None else 'warmup_incomplete' if self.warmup_s<900 else 'suspect_volatile' if self.sample and self.sample['volatile_interference'] else 'estimated'
        return dict(water_activity=self.indicated_aw,temperature_k=self._values()['thermal.indicated_temperature_k'],equilibrated=self.stable,phase=self.phase,quality=quality,evidence='derived_measured_reference_with_synthetic_dynamics',template_source=TEMPLATE_REL,moisture_dry_basis=moisture,moisture_evidence='user_supplied_isotherm' if moisture is not None else 'unavailable')
    def invoke(self,capability,**a):
        if capability in PANEL_CAPS:return self._panel_command(capability,a)
        if capability in DEVICE_CAPS:return self._device(capability,a)
        if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability][0],a)
        if self.phase=='running' and capability not in ('power.set','latch.set_angle','acquisition.stop','water_activity.measure','calibration.verify'):raise ActionRejected('BUSY')
        if capability=='power.set':
            if not self.powered and a['enabled']:
                self.warmup_s=0.;self.power_epoch+=1
                if self.fault and not self.fault['persistent']:self.fault=None   # aq p.58: Crystal Failure - cycle the power
            self.powered=a['enabled']
            if not self.powered:
                self.stable=False
                if self.phase=='running':self.phase='cancelled'
            r=dict(a)
        elif capability=='thermal.set_temperature':
            if abs((a['temperature_k']-273.15)*10-round((a['temperature_k']-273.15)*10))>1e-7:raise ActionRejected('INVALID_ARGUMENT')
            self.target_k=a['temperature_k'];self.indicated_aw=None;self.stable=False;r=dict(a)
        elif capability=='lid.set_angle':
            if a['angle_rad']>1e-4 and self.latch_angle>.1:raise ActionRejected('PRECONDITION_FAILED')
            self.lid_angle=a['angle_rad'];self.stable=False;r=dict(a)
        elif capability=='latch.set_angle':
            if a['angle_rad']>.1 and self.lid_angle>1e-4:raise ActionRejected('PRECONDITION_FAILED')
            self.latch_angle=a['angle_rad']
            if self.latch_angle<1.4:
                self.stable=False
                if self.phase=='running':self.phase='cancelled'
            r=dict(a)
        elif capability=='sample.configure':
            if not a['matrix_id'].strip():raise ActionRejected('INVALID_ARGUMENT')
            row=self.templates.get(a['substance_id']);index=a['record_index']
            if self.lid_angle<1.4 or self.sample or not row or not row['runtime_eligible'] or index!=int(index) or index>=len(row['records']):raise ActionRejected('PRECONDITION_FAILED')
            record=row['records'][int(index)];self.sample=copy.deepcopy(a);self.sample['reference']=copy.deepcopy(record)
            self.sample_water=a['mass_kg']*record['water_mass_fraction'];self.sample_dry=a['mass_kg']-self.sample_water;self.input_kg+=a['mass_kg'];self.water_input+=self.sample_water;self.sample_k=a['initial_temperature_k'];self.indicated_aw=None;self.indicated_dew=None;self.stable=False;r={'configured':True}
        elif capability=='sample.unload':
            if self.lid_angle<1.4 or not self.sample:raise ActionRejected('PRECONDITION_FAILED')
            self.removed_kg+=self.sample_water+self.sample_dry;self.water_removed+=self.sample_water;self.sample=None;self.sample_water=0.;self.sample_dry=0.;self.indicated_aw=None;self.indicated_dew=None;self.stable=False;r={'unloaded':True}
        elif capability=='method.configure':
            n=a['consecutive_readings'];mode=a['mode'];delta=a['stability_delta']
            if n!=int(n) or (mode=='custom' and (n<2 or delta<.0005)) or (mode=='low_emitting' and delta>.003) or (mode=='iso18787' and abs(delta-.0003)>1e-12):raise ActionRejected('INVALID_ARGUMENT')
            self.mode=mode;self.delta=delta;self.temp_tolerance=a['temperature_tolerance_k'];self.consecutive=int(n);self.stable=False;r={'configured':True}
        elif capability=='calibration.configure':
            if not a['source'].strip():raise ActionRejected('INVALID_ARGUMENT')
            self.offset=a['offset_aw'];self.indicated_aw=None;self.stable=False;r={'offset_aw':self.offset}
        elif capability=='calibration.verify':
            if not self.powered or self.indicated_aw is None or not self.stable or self.warmup_s<900:raise ActionRejected('PRECONDITION_FAILED')
            delta=self.indicated_aw-a['reference_aw'];return {'deviation_aw':delta,'within_vendor_tolerance':abs(delta)<=.003}
        elif capability=='isotherm.configure':
            p=a['points']
            if not a['source'].strip() or not a['matrix_id'].strip() or any(b['water_activity']<=x['water_activity'] or b['moisture_dry_basis']<x['moisture_dry_basis'] for x,b in zip(p,p[1:])):raise ActionRejected('INVALID_ARGUMENT')
            self.isotherm=dict(copy.deepcopy(a),offset=0.);self.models[a['matrix_id']]=self.isotherm;self.model_selected=a['matrix_id'];r={'configured':True}   # also stored as a model on the instrument
        elif capability=='acquisition.start':
            why=('the instrument is switched off' if not self.powered else 'a fault is shown (%s)'%self.fault if self.fault else 'no sample cup is loaded' if not self.sample else
                 'the drawer lid is open' if self.lid_angle>1e-4 else 'the drawer is not latched' if self.latch_angle<1.4 else
                 'the block set point %.2f K differs from the sample reference temperature %.2f K by more than 0.05 K'%(self.target_k,self.sample['reference']['temperature_k'])
                 if abs(self.target_k-self.sample['reference']['temperature_k'])>.050001 else None)
            if why:raise _rejected('PRECONDITION_FAILED',why)   # 1.3.1: the message names the cause
            self._operation_count+=1;self.operation_id=f'{self.instance_id}:aw:{self._operation_count}';self.phase='running';self.elapsed_s=0.;self.history=[];self._stable_count=0;self.stable=False;self.indicated_aw=None;r={'phase':'running','operation_id':self.operation_id}
            self.last_reading=None;self._was_stable=False;self.beep_until=-1.;self.raw_aw=None
        elif capability=='acquisition.stop':
            if self.phase=='running':self.phase='cancelled'
            r={'phase':self.phase}
        else:return self._measure()
        self.revision+=1;return copy.deepcopy(r)
    # ---- 1.1.0 device layer
    def _now(self):
        return CLOCK_EPOCH+datetime.timedelta(seconds=round(self.clock_offset_s+self.time_s))
    def _reading_done(self):
        """A finished reading (aq p.8): results with the SAVE icon, the end beeps, autosave (aq p.36) and the serial print (2019 manual Table 3)."""
        m=self._measure();t=self._now()
        rec=dict(seq=0,name='',water_activity=self.indicated_aw,temperature_k=m['temperature_k'],test_time_s=self.elapsed_s,user=self.current_user or '',
                 datetime=t.isoformat(timespec='seconds'),sensor='Dew Point',moisture_dry_basis=m['moisture_dry_basis'],model=self.model_selected)
        if self.settings['autosave'] and len(self.records)<MAX_RECORDS:
            self._seq+=1;rec['seq']=self._seq;self.records.append(copy.deepcopy(rec))
        self.last_reading=rec
        self.beep_until={'four':self.time_s+2.,'continuous':math.inf}.get(self.settings['beeps'],-1.)
        if self.settings['print_mode']!='off':
            line=f"{self.elapsed_s/60:.1f} {m['temperature_k']-273.15:.1f} {self.indicated_aw:.4f}"
            if self.settings['print_mode']=='detailed':line=f"{t:%d-%m-%y} {t:%H:%M} {SERIAL} "+line
            self.serial_out=(self.serial_out+[line])[-50:]
    def _device(self,cap,a):
        validate(DEVICE_CAPS[cap][0],a)
        if self.phase=='running' and cap not in ('isotherm.select','beeper.silence','data.save','users.logout','fault.inject','fault.clear'):raise ActionRejected('BUSY')
        if cap=='fault.inject':
            self.fault=dict(code=a['code'],persistent=bool(a.get('persistent',False)) or not FAULTS[a['code']][1]);self.stable=False
            if self.phase=='running':self.phase='cancelled'                # the reading cannot go on
            r={'fault':a['code']}
        elif cap=='fault.clear':
            self.fault=None;r={'fault':None}
        elif cap=='settings.configure':
            self.settings.update(a['settings']);r=dict(settings=dict(self.settings))
        elif cap in ('clock.set_date','clock.set_time'):
            if any(v!=int(v) for v in a.values()):raise ActionRejected('INVALID_ARGUMENT')
            now=self._now()
            try:new=now.replace(year=int(a['year']),month=int(a['month']),day=int(a['day'])) if cap=='clock.set_date' else now.replace(hour=int(a['hour']),minute=int(a['minute']),second=0)
            except ValueError:raise ActionRejected('INVALID_ARGUMENT') from None
            self.clock_offset_s+=(new-now).total_seconds();r={'datetime':self._now().isoformat(timespec='seconds')}
        elif cap=='data.save':
            rec=self.last_reading
            if rec is None:raise ActionRejected('NO_READING')
            if not rec['seq'] and len(self.records)>=MAX_RECORDS:raise ActionRejected('MEMORY_FULL')
            m=self._measure();rec.update(name=a['name'],model=self.model_selected,moisture_dry_basis=m['moisture_dry_basis'])   # the model selected now (aq p.8 note)
            if rec['seq']:self.records=[copy.deepcopy(rec) if x['seq']==rec['seq'] else x for x in self.records]
            else:self._seq+=1;rec['seq']=self._seq;self.records.append(copy.deepcopy(rec))
            r={'seq':rec['seq']}
        elif cap=='data.delete_all':
            r={'deleted':len(self.records)};self.records=[]
            if self.last_reading:self.last_reading['seq']=0
        elif cap=='beeper.silence':
            self.beep_until=-1.;r={'beeping':False}
        elif cap in ('isotherm.select','isotherm.set_offset','isotherm.copy','isotherm.delete'):
            name=a.get('source',a['name']) if cap=='isotherm.copy' else a['name']
            if name is not None and name not in self.models:raise ActionRejected('MODEL_NOT_FOUND')
            if cap=='isotherm.select':self.model_selected=name
            elif cap=='isotherm.set_offset':self.models[name]['offset']=a['offset_dry_basis']
            elif cap=='isotherm.copy':
                if a['name'] in self.models:raise ActionRejected('NAME_EXISTS')
                self.models[a['name']]=dict(copy.deepcopy(self.models[name]),offset=a['offset_dry_basis'])
            else:
                del self.models[name]
                if self.model_selected==name:self.model_selected=None
            self.isotherm=self.models.get(self.model_selected)
            r={'name':self.model_selected} if cap=='isotherm.select' else dict(a) if cap=='isotherm.set_offset' else {'name':a['name']}
        elif cap=='users.set_admin_password':
            self.admin_password=a['password'];r={'admin_set':True}
        elif cap=='users.login':
            pw=self.admin_password if a['name']=='Admin' else self.users.get(a['name'],{}).get('password')
            if pw is None:raise ActionRejected('USER_NOT_FOUND')
            if pw!=a['password']:raise ActionRejected('WRONG_PASSWORD')
            self.current_user=a['name'];r={'current':a['name']}
        elif cap=='users.logout':
            self.current_user=None;r={'current':None}
        else:
            name=a['name']
            if cap=='users.add':
                if self.admin_password is None:raise ActionRejected('ADMIN_REQUIRED')
                if name=='Admin' or name in self.users:raise ActionRejected('NAME_EXISTS')
                self.users[name]=dict(password=a['password'],access={k:False for k in ACCESS})
            elif name not in self.users:raise ActionRejected('USER_NOT_FOUND')
            elif cap=='users.set_password':self.users[name]['password']=a['password']
            elif cap=='users.set_access':self.users[name]['access']=dict(a['access'])
            else:
                del self.users[name]
                if self.current_user==name:self.current_user=None
            r={'name':name}
        self.revision+=1;return copy.deepcopy(r)
    # ---- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
    def panel_read(self):
        """What the instrument firmware sees: the agent slots plus its own sensors and formatted texts (clock, data list, model list)."""
        out=self.read('agent');t=self._now();st=self.settings
        hm=f"{t:%H:%M}" if st['time_format']=='24h' else f"{(t.hour%12) or 12}:{t:%M}{'AM' if t.hour<12 else 'PM'}"
        date=f"{t:%m/%d/%y}" if st['date_format']=='mm/dd/yy' else f"{t:%d/%m/%y}"
        names=list(self.models);cyc=[None]+names;i=cyc.index(self.model_selected) if self.model_selected in cyc else 0
        def line(r):
            d=datetime.datetime.fromisoformat(r['datetime'])
            dd=f"{d:%m/%d}" if st['date_format']=='mm/dd/yy' else f"{d:%d/%m}"
            tt=f"{d:%H:%M}" if st['time_format']=='24h' else f"{(d.hour%12) or 12}:{d:%M}{'AM' if d.hour<12 else 'PM'}"
            return f"{dd},{tt},{'-' if r['water_activity'] is None else format(r['water_activity'],'.4f')}"
        user=self.users.get(self.current_user) if self.current_user not in (None,'Admin') else None
        dew=self.indicated_dew if self.phase=='running' and self.indicated_dew is not None else self.block_k
        out.update({'host.powered':self.powered,'host.power_epoch':self.power_epoch,'host.time_text':hm,'host.date_text':date,
                    'host.date_long':f"{t:%b} {t.day:02d}, {t.year}",'host.clock':dict(year=t.year,month=t.month,day=t.day,hour=t.hour,minute=t.minute),
                    'host.sealed':self.lid_angle<1e-4 and self.latch_angle>=1.4,'host.running':self.phase=='running',
                    'host.completed':self.last_reading is not None and self.phase!='running','host.raw_aw':getattr(self,'raw_aw',None),
                    'host.delta_t':round(self.sample_k-self.block_k,2),'host.hot':self.phase=='running' and self.sample_k-self.block_k>4.,
                    'host.lid_k':round(self.block_k,2),'host.base_k':round(self.block_k,2),'host.sample_k':round(self.sample_k,2),'host.mirror_k':round(dew,2),
                    'host.optical_mv':1745. if self.powered else 0.,'host.moisture':self._measure()['moisture_dry_basis'],
                    'host.model_next':cyc[(i+1)%len(cyc)],'host.model_prev':cyc[(i-1)%len(cyc)],
                    'host.records_view':[dict(seq=r['seq'],line=line(r),date=_fmt_date(r['datetime'],st['date_format']),time=line(r).split(',')[1],index=i+1)
                                         for i,r in enumerate(self.records)][::-1],'host.record_count':len(self.records),
                    'host.record_first':self.records[0]['seq'] if self.records else 0,'host.record_last':self.records[-1]['seq'] if self.records else 0,
                    'host.users_login':['Admin']+list(self.users),'host.users_exist':bool(self.users),
                    'host.locked':[k for k,v in user['access'].items() if v] if user else [],'host.serial':SERIAL,'host.lid_serial':LID_SERIAL,'host.firmware':FIRMWARE,
                    'host.method':dict(mode=self.mode,delta=self.delta,tolerance=self.temp_tolerance,consecutive=self.consecutive),'host.calibration_offset':self.offset})
        return out
    def panel_invoke(self,capability_id,arguments):
        try:
            validate(ALL_CAPS[capability_id][0],arguments)
            if self._caller is not None:Aqualab4teSession(self,self._caller)._guard(capability_id,arguments)   # a gesture acts as the session's caller
            return self.invoke(capability_id,**arguments)
        except ActionRejected as exc:raise _panel_ui.PanelRejected(str(exc)) from None
    def panel_time(self):return self.time_s
    def _panel_command(self,k,a):
        validate(ALL_CAPS[k][0],a)
        if k=='panel.frame':return dict(frame=self.panel.frame())
        if k=='panel.capture':
            data,w,h=self.panel.png_bytes(a['display_id']);sha=hashlib.sha256(data).hexdigest()
            d=Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir())/'chemistry_sim_panel'/self.instance_id
            d.mkdir(parents=True,exist_ok=True);path=d/f"{a['display_id']}_{sha[:16]}.png"
            if not path.exists():path.write_bytes(data)
            return dict(display_id=a['display_id'],time_s=self.time_s,image=dict(path=str(path),format='png',width=w,height=h,sha256=sha))
        try:
            ok=self.panel.press(a['input_id'],float(a.get('duration_s',.1))) if k=='panel.press' else self.panel.switch(a['input_id'],a['position'])
        except _panel_ui.PanelError:raise ActionRejected('INVALID_ARGUMENT') from None
        self.revision+=1;return dict(accepted=bool(ok))
    def step(self,dt_s):
        if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<=0:raise ValueError('finite positive dt_s required')
        left=float(dt_s)
        while left>1e-10:
            active=self.phase=='running';h=min(left,1.)
            if active:h=min(h,(math.floor(self.elapsed_s/10+1e-8)+1)*10-self.elapsed_s)
            if self.powered:self.warmup_s+=h
            target=self.target_k if self.powered else 298.15;self.block_k=target+(self.block_k-target)*math.exp(-h/25.);self.sample_k=self.block_k+(self.sample_k-self.block_k)*math.exp(-h/35.)
            closed=self.lid_angle<1e-4 and self.latch_angle>=1.4
            if self.sample and closed:
                aw=self.sample['reference']['value'];eq=aw*psat(self.sample_k)*20e-6*.01801528/(8.314462618*self.sample_k);transfer=(eq-self.vapor)*(1-math.exp(-h/self.sample['equilibration_tau_s']))
                transfer=max(-self.vapor,min(self.sample_water,transfer));self.sample_water-=transfer;self.vapor+=transfer
            elif not closed:
                eq=.5*psat(self.sample_k)*20e-6*.01801528/(8.314462618*self.sample_k);transfer=(eq-self.vapor)*(1-math.exp(-h/5.));self.vapor+=transfer
                if transfer>=0:self.ambient_in+=transfer
                else:self.ambient_out-=transfer
            if active:
                self.elapsed_s+=h;p=self.vapor*8.314462618*self.sample_k/(20e-6*.01801528);true_aw=p/psat(self.sample_k)
                indicated=round(true_aw+self.offset+(.005 if self.sample['volatile_interference'] else 0.),4);self.indicated_aw=indicated if .03<=indicated<=1 else None;self.raw_aw=indicated;self.indicated_dew=round(dewpoint(p)-273.15,2)+273.15
                if abs(self.elapsed_s/10-round(self.elapsed_s/10))<1e-7:
                    self.history.append((self.elapsed_s,indicated));self.history=self.history[-7:]
                    ref=(self.history[-7] if len(self.history)>=7 else None) if self.mode=='iso18787' else self.history[-2] if len(self.history)>=2 else None   # ISO 18787: change over one minute
                    stable=self.elapsed_s>=60 and ref is not None and abs(self.history[-1][1]-ref[1])<=self.delta and abs(self.sample_k-self.block_k)<=self.temp_tolerance and self.indicated_aw is not None
                    self._stable_count=self._stable_count+1 if stable else 0;self.stable=self._stable_count>=self.consecutive
                    if self.stable and self.mode!='continuous':self.phase='succeeded'
                    if self.stable and not self._was_stable:self._reading_done()
                    self._was_stable=self.stable
            self.time_s+=h;left-=h
        self.revision+=1;return self.read('evaluator')
class Aqualab4teSession:
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
        meta=export_aqualab_4te_contract_metadata();meta['capabilities'].update(export_panel_contract_metadata())
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
        if not isinstance(capability_id,str) or capability_id not in ALL_CAPS: raise ActionRejected('UNKNOWN_CAPABILITY')
        if self._role=='agent' and ALL_CAPS[capability_id][5]!='agent': raise ActionRejected('ACCESS_DENIED')
        if not isinstance(request_id,str) or not request_id: raise ActionRejected('INVALID_ARGUMENT')
        validate(ALL_CAPS[capability_id][0],arguments)
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
        elif capability_id in PANEL_CAPS and h.operation_id and h.operation_id!=before: h._owners[h.operation_id]=self._principal   # a reading started by the lever / ENTER belongs to the caller
        response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=op,
          status='accepted' if op else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None)
        h._requests[key]=(digest,copy.deepcopy(response)); return response

    def _guard(self,capability_id,arguments):
        """An agent may not stop another agent's running reading (direct call, or the panel CANCEL key); unowned readings and the evaluator
        are not restricted; the chamber lever is mechanical and the rear power switch is a hard switch (lead clarification 10-19): neither is
        guarded, switching off cancels the reading as on a power loss."""
        h=self._host
        if self._role!='agent' or capability_id not in _CONTROL_GUARDED or h.phase!='running': return
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

_CONTROL_GUARDED={'acquisition.stop'}                                    # commands that end another agent's reading (power switch: physical)

def export_aqualab_4te_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'Aqualab4teSession.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in {**CAPS,**DEVICE_CAPS}.items():
        async_=key=='acquisition.start'; pre=[]
        conditions={
         'acquisition.start':('供电、闭盖锁紧、已装样，目标温度匹配实测记录',['power.enabled','lid.angle_rad','latch.angle_rad','sample.loaded','thermal.target_k'],'powered and lid<=1e-4 and latch>=1.4 and loaded and abs(target-reference_T)<=0.050001'),
         'sample.configure':('受信任宿主、开盖、空杯、记录可用',['lid.angle_rad','sample.loaded'],'lid>=1.4 and empty and eligible and integral record index'),
         'sample.unload':('受信任宿主、开盖且有样',['lid.angle_rad','sample.loaded'],'lid>=1.4 and loaded'),
         'lid.set_angle':('无采集且锁杆已释放才能开盖',['cycle.phase','latch.angle_rad'],'not running and (angle<=1e-4 or latch<=0.1)'),
         'latch.set_angle':('只有盖闭时可锁；释放取消采集',['lid.angle_rad'],'angle<=0.1 or lid<=1e-4'),
         'calibration.verify':('已供电预热15分钟并有平衡读数',['power.enabled','warmup.ready','equilibrium.stable'],'powered and warmup>=900 and stable and indicated is not None')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        if key in DEVICE_PRE:
            pre=[dict(description_zh=DEVICE_PRE[key][0],state_ids=DEVICE_PRE[key][1],enforced_by='runtime',check='Aqualab4teRuntime._device')]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.aqualab_4te.Aqualab4teSession.invoke',invocation=f'session.invoke(instance_id="aqualab4te_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；取消保留热状态和水分转移；连续模式须显式停止' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电不回滚已经发生的水分转移和热过程。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']]
                 +[dict(code=e,meaning_zh=DEVICE_ERRORS[e],side_effects='none') for e in DEVICE_CAP_ERRORS.get(key,[]) if e in DEVICE_ERRORS],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。']+(DEVICE_LIMITS if key in DEVICE_CAPS else []),
          source_ids=['manufacturer_primary','simulation_assumption'] if key not in DEVICE_CAPS else ['manufacturer_manual','simulation_assumption']+(['manufacturer_manual_2019'] if key.startswith('isotherm.') else []))
    mechanism=dict(component_ids=['sample_cup','sample_block','chilled_mirror','thermopile','sample_peltier','mirror_peltier','chamber_fan'],
      state_ids=['thermal.block_k','thermal.sample_k','water.input_kg','water.sample_kg','water.headspace_kg','water.removed_kg','water.ambient_in_kg','water.ambient_out_kg'],
      meaning_zh='实测二元水溶液活度参考点驱动头空间传质、露点与温控代理，闭合水质量账本',implementation='lumped',
      equations=['aw=p_vapor/p_sat(T_sample)', 'p_sat=610.94*exp(17.625*T_C/(243.04+T_C)) Pa (Magnus engineering approximation)',
      'm_eq=aw_reference*p_sat*V_headspace*M_water/(R*T); dm=(m_eq-m_vapor)*(1-exp(-dt/tau))',
      'water_input+ambient_in=sample_water+headspace_water+removed_water+ambient_out',
      'open chamber vapor relaxes toward assumed ambient RH=0.5; direct open-sample evaporation omitted',
      'moisture=piecewise linear supplied product isotherm only at matching matrix/temperature, no extrapolation'],
      parameters={'headspace_volume':dict(value=20e-6,unit='m^3',meaning_zh='未知真实体积的传质代理',evidence='assumption',source_ids=['simulation_assumption'],uncertainty=None)},
      source_ids=['aw_templates','simulation_assumption'],limitations=['平衡时间、热滞后和Magnus露点均engineering_assumption，不保证厂家准确度。','传质中以原始溶液活度近似不变；未求组成变化后的活度及开盖样品蒸发。','未实现真实凝结液膜、光路/冷镜伺服、挥发物补偿或完整厂家算法；ISO 18787模式按一分钟内读数变化≤0.0003判定（代理）。','没有实测产品吸附等温线时含水率不可用；公开水溶液平衡点不是产品吸附等温线。'])
    _ann.annotate_export('aqualab_4te',caps,slots,{'water_activity':mechanism},inputs={})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'water_activity':mechanism})


DEVICE_LIMITS=['设备层（1.1.0）：设置、时钟、数据存储、提示音、串口打印、产品模型与用户均为仪器内的非易失状态（断电保留）；时钟为仿真时间加偏置（t=0时为2026-01-01 08:00，假设）；'
               '串口打印只记录仪器发出的行（serial.output），无真实串口设备；AquaLink/SKALA的模型下载与数据备份没有连接（需USB/云），删除前的备份提醒因此总是出现。',
               '用户与Access：手册写了管理员密码、用户增删与逐项锁定，未写登录流程；本资产在有用户时开机先选用户并输密码（假设），锁定的选项在面板上不可进入，直接命令不受锁定限制（假设）。',
               '%Moisture校准（New/Edit/Delete/Defaults）按2019版Series 4手册2.5.4–2.5.6实现为模型含水率线性偏置；2025版手册把模型偏置移到SKALA，仪器上的该菜单保留。',
               '故障（1.2.0）：手册p.58 Table 6的四条故障信息由评测者fault.inject注入（默认无）：屏幕显示该信息（版面为假设），按键无反应（电源开关与锁杆除外），进行中的读数取消、不能开始读数；Crystal Failure断电重启清除（persistent=True时仍在），Dew point sensor failure与Firmware is corrupted须维修，Contaminated mirror须按4.2清洁镜面（清洁本身未建模），均由fault.clear代表；冷镜、晶振与固件的内部原因不建模。']
PANEL_LIMITS=['前面板层（panel.*）：按panel_ui.json（AQUALAB 4手册18280-6页码证据）实现128×64点阵屏的三个标签页（Measurement、Configuration、Data）与各配置屏、四个方向键、'
              '三个随图标变化的圆键、样品室锁杆（READ开始读数、OPEN中断）与后部电源开关；每个面板动作都调用本运行层的同名能力（没有第二份仪器逻辑）；手势不推进仿真时钟。',
              '圆键图标按手册Table 2写为文字（ENTER、CANCEL、MENU、SAVE、OK、AUDIO）；测量页ENTER在样品室已锁紧时重新开始读数、读数进行中只显示CANCEL、提示音进行中左键为AUDIO OFF，均为手册未写的假设，见panel_ui.json。',
              '面板不含3D联动：屏幕纹理（render()）与键关节触发手势属于后续Matterix步骤；Matterix现有的“左圆键=开始测量”映射是场景捷径，与手册（锁杆到READ开始）不同。',
              '调用者规则（1.2.0）：面板手势以会话调用者身份执行：锁杆到READ或ENTER开始的读数属于该调用者（可poll/cancel）；另一agent不能用CANCEL键或直接调用acquisition.stop结束他人的读数（被拒、状态不变）；样品室锁杆是机械件、后部电源开关是硬开关（负责人澄清10-19），都不受此限，关断电源时读数按断电取消。',
              '一致性清查（1.2.1）：另一agent被拒的CANCEL键在键图标上方一行显示“Started by other user” 2 s（文字为假设：真实仪器没有调用者概念）。']


def export_panel_contract_metadata():
    """Contract entries of the panel capabilities: a gesture changes what the commands it calls change."""
    out={}
    for k,d in PANEL_CAPS.items():
        query=k in ('panel.capture','panel.frame')
        c={key:copy.deepcopy(d[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']}
        c.update(availability='implemented',implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
                 binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.aqualab_4te.Aqualab4teSession.invoke',
                              invocation=f'session.invoke(instance_id="aqualab4te_01", capability_id={k!r}, arguments={d["example_arguments"]!r}, request_id="unique")'),
                 preconditions=[dict(description_zh='断电时屏幕暗、按键无反应（只有电源开关起作用）；被调用能力的前提同样适用（被拒时屏幕显示仪器的提示，手势不被接受）'
                                     if not query else '无（关机时为暗屏）',state_ids=['power.enabled'],enforced_by='runtime',check='Aqualab4teRuntime.invoke')],
                 state_effects=[] if query else [dict(state_id=x,timing='immediate',description_zh='手势调用的能力所写的状态（导航本身不改状态槽）') for x in _PANEL_EFFECTS],
                 observes=[],model_effects=dict(mode='none',description_zh='面板层只改界面状态与被调用能力的状态，屏幕画面由panel.capture渲染',binding_ids=[]),
                 execution=dict(mode='synchronous',time_domain='simulation',completion_criterion='立即返回当前画面' if query else '手势瞬时完成（不推进仿真时钟）：结果只说明是否被仪器接受，屏幕须用panel.capture查看',
                                completion_state_ids=[],timeout_s=None,cancellation='unsupported',retry='same_request_id',resources=['target_instance'],
                                partial_failure_zh='被拒的手势不改变运行层状态（只显示仪器的提示）'),
                 errors=[dict(code=e,meaning_zh={'INVALID_ARGUMENT':'未知输入件或显示件、手势与输入件不符','ACCESS_DENIED':'评测者专用','REQUEST_CONFLICT':'同一request_id用于不同请求',
                                                 'UNKNOWN_INSTANCE':'实例ID不存在'}[e],side_effects='none')
                         for e in ['INVALID_ARGUMENT','REQUEST_CONFLICT','UNKNOWN_INSTANCE']+(['ACCESS_DENIED'] if d['visibility']!='agent' else [])],
                 verification=[],limitations=list(PANEL_LIMITS),source_ids=['manufacturer_manual','manufacturer_manual_2019','simulation_assumption'])
        out[k]=c
    return out


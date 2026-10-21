"""JASCO P-4200: measured reference specific rotations, explicitly idealized polarimetry.

Front panel (1.1.0): the integrated touch panel of the P-4000 series (panel_ui.json beside the contract, run by the shared engine _panel_ui.py)
drives the commands below plus the device functions behind its screens (DEVICE_CAPS: light-source window, measurement parameters and the six
data modes of the P-2000 manual p.82-84, zero clear, analyzer / automatic measurement, temperature start conditions, analysis list, daily
check, validation, jig registration, users, backup, self diagnosis).  No P-4000 operating manual is saved: the screens follow the P-4000
product page and application notes, fields, ranges and formulas the P-2000 iRM predecessor manual (see panel_ui.json 'assumptions').

1.1.1 (sync fixes 2026-10-19): one rotation model for every reading path (_alpha: quartz correction gain, temperature correction, blank value,
Zero Clear; the points of scan.rotation_rad / result.read / poll and the device data list); the Meas. Point selects the in-cell or the holder
sensor for the shown temperature, the temperature correction, the start conditions and the Peltier control; runs started from the panel, by the
device Start / Blank keys, by Automatic Meas. or after a temperature start condition belong to the session that asked for them, and another
agent may not end them (Stop key / measure.stop / acquisition.stop, lamp off, validation cancel: ACCESS_DENIED on the direct and the panel
path alike; evaluator / debug callers and unowned runs are not restricted).
1.1.2 (sweep 2026-10-19): opening the sample-compartment lid and the power switch (power.set models it) are physical actions and are never
refused; the lid interlock ends a running measurement (LID_OPEN), switching off acts as a power loss (POWER_OFF), whoever does it."""
from __future__ import annotations
import copy,hashlib,importlib.util,json,math,os,tempfile
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2];SLUG='jasco_p4200';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-070';PROTOCOL_VERSION='1.0.0';ASSET_VERSION='1.1.2'
ASSET_DIR=ROOT/'assets/expansion_batch03/t3_analytical/jasco_p4200'
TEMPLATE_REL='assets/reactions/jasco_p4200_rotation_library/templates.json'
BOOL={'type':'boolean'};TEXT={'type':'string'}
class ActionRejected(ValueError):pass
def number(unit,low=None,high=None):
 s={'type':'number','x-unit':unit}
 if low is not None:s['minimum']=low
 if high is not None:s['maximum']=high
 return s
def obj(p):return dict(type='object',properties=p,required=list(p),additionalProperties=False)
def arr(s,n=None):
 x=dict(type='array',items=s)
 if n is not None:x.update(minItems=n,maxItems=n)
 return x
def integer(low,high):return dict(number('1',low,high),type='integer')
def validate(s,v):
 if 'anyOf' in s:
  if v is None and any(q.get('type')=='null' for q in s['anyOf']):return
  return validate(next(q for q in s['anyOf'] if q.get('type')!='null'),v)
 t=s.get('type')
 if t in ('number','integer'):
  if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):raise ActionRejected('INVALID_ARGUMENT')
  if t=='integer' and type(v) is not int:raise ActionRejected('INVALID_ARGUMENT')
  if not s.get('minimum',-math.inf)<=v<=s.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='boolean' and type(v) is not bool:raise ActionRejected('INVALID_ARGUMENT')
 elif t=='string' and (not isinstance(v,str) or not s.get('minLength',0)<=len(v)<=s.get('maxLength',math.inf)):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='object':
  if not isinstance(v,dict) or not set(s['required'])<=v.keys():raise ActionRejected('INVALID_ARGUMENT')
  for k,x in v.items():
   if k not in s['properties']:raise ActionRejected('INVALID_ARGUMENT')
   validate(s['properties'][k],x)
 elif t=='array':
  if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
  for x in v:validate(s['items'],x)
 if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')

PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
PLANT=obj({'ambient_k':number('K',288.15,313.15),'heat_capacity_j_k':number('J/K',1,1e5),'thermal_ua_w_k':number('W/K',.001,100),'control_gain_w_k':number('W/K',.01,1000),'peltier_limit_w':number('W',.1,1000),'instrument_gain':number('1',.5,1.5),'zero_offset_rad':number('rad',-.05,.05),'detector_dark':number('1',0,1),'noise_density_sqrt_s':number('s^0.5',0,.01),'seed':integer(0,2147483647),'accept_uncalibrated_model':BOOL})
SAMPLE=obj({'sample_id':TEXT,'kind':dict(type='string',enum=['blank','sample','quartz']),'template_id':TEXT,'solvent':TEXT,'volume_m3':number('m^3',0,2e-6),'concentration_kg_m3':number('kg/m^3',0,1000),'path_m':{'type':'number','enum':[.1],'x-unit':'m'},'reference_temperature_k':number('K',288.15,313.15),'transmittance':number('1',.01,1),'quartz_angle_rad':nullable(number('rad',-1.3962634016,1.3962634016)),'accept_missing_source_concentration':BOOL,'accept_ideal_quartz':BOOL})
METHOD=obj({'lamp':{'type':'string','enum':['Na']},'wavelength_m':{'type':'number','enum':[589e-9],'x-unit':'m'},'integration_s':number('s',1,100),'repeats':integer(1,100),'declared_concentration_kg_m3':number('kg/m^3',.001,1000),'declared_path_m':number('m',.001,.1)})
PLANT_EXAMPLE=dict(ambient_k=293.15,heat_capacity_j_k=40.,thermal_ua_w_k=.5,control_gain_w_k=20.,peltier_limit_w=12.,instrument_gain=1.02,zero_offset_rad=.001,detector_dark=.01,noise_density_sqrt_s=0.,seed=70,accept_uncalibrated_model=True)
SAMPLE_EXAMPLE=dict(sample_id='sample_01',kind='sample',template_id='pubchem_5988',solvent='water',volume_m3=1e-6,concentration_kg_m3=100.,path_m=.1,reference_temperature_k=293.15,transmittance=.8,quartz_angle_rad=None,accept_missing_source_concentration=True,accept_ideal_quartz=False)
BLANK_EXAMPLE=dict(SAMPLE_EXAMPLE,sample_id='blank_01',kind='blank',template_id='',concentration_kg_m3=0.)
QUARTZ_EXAMPLE=dict(SAMPLE_EXAMPLE,sample_id='quartz_01',kind='quartz',template_id='',solvent='',volume_m3=0.,concentration_kg_m3=0.,quartz_angle_rad=math.radians(10),accept_ideal_quartz=True)
METHOD_EXAMPLE=dict(lamp='Na',wavelength_m=589e-9,integration_s=1.,repeats=3,declared_concentration_kg_m3=100.,declared_path_m=.1)
FAST_PLANT=copy.deepcopy(PLANT_EXAMPLE)
POINT=obj({'raw_rotation_rad':number('rad'),'rotation_rad':nullable(number('rad')),'photometric_channels':arr(number('1'),4),'sample_time_s':number('s',0),'integration_s':number('s',1,100),'elapsed_s':number('s',0),'temperature_k':number('K'),'detector_sigma_rad':number('rad',0)})
MEASURE=obj({'phase':PHASE,'rotation_rad':nullable(number('rad')),'raw_rotation_rad':nullable(number('rad')),'elapsed_s':number('s',0),'completed_points':integer(0,100),'warning':TEXT})
RESULT=obj({'phase':PHASE,'kind':TEXT,'complete':BOOL,'points':arr(POINT),'settings_json':TEXT,'template_source':TEXT,'warning':TEXT,'evidence':TEXT,'limitations':arr(TEXT)})
SPECIFIC=obj({'mean_rotation_rad':number('rad'),'specific_rotation_rad_m2_kg':number('rad*m^2/kg'),'repeat_sd_rad':nullable(number('rad')),'count':integer(1,100),'evidence':TEXT})
CONCENTRATION=obj({'concentration_kg_m3':number('kg/m^3',0),'evidence':TEXT})
LEDGER=obj({**{k:number(u) for k,u in [('registered_volume_m3','m^3'),('attached_volume_m3','m^3'),('external_volume_m3','m^3'),('volume_residual_m3','m^3'),('registered_solute_kg','kg'),('attached_solute_kg','kg'),('external_solute_kg','kg'),('mass_residual_kg','kg'),('thermal_delta_j','J'),('controller_energy_j','J'),('ambient_loss_j','J'),('thermal_residual_j','J')]},'active_template_id':TEXT,'evidence':TEXT})
TEMPLATE=obj({'substance_id':TEXT,'name':TEXT,'specific_rotation_rad_m2_kg':number('rad*m^2/kg'),'conditions_json':TEXT,'source_json':TEXT,'evidence':TEXT})
CAPS={}
def cap(k,inp,out,c,z,kind='device_command',vis='agent',ex=None):CAPS[k]=[inp,out,c,z,kind,vis,{} if ex is None else ex,[]]
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body','detector'],'宿主一次性声明传递增益/零偏/噪声/热参数；均非原厂辨识','configuration','evaluator',PLANT_EXAMPLE)
cap('sample.register',SAMPLE,obj({'registered':BOOL}),['sample_holder'],'登记已装池溶液或显式理想石英参考板；真实文献条件须匹配','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.load',obj({'sample_id':TEXT}),obj({'loaded':BOOL,'attachment_event':TEXT}),['sample_holder','quartz_cell'],'开盖逻辑挂接完整预装池或标准板；非机器人','device_command','evaluator',{'sample_id':'sample_01'})
cap('sample.unload',obj({'recipient_id':TEXT}),obj({'unloaded':BOOL,'attachment_event':TEXT}),['sample_holder','quartz_cell'],'开盖完整卸载；体积和溶质质量不消耗','device_command','evaluator',{'recipient_id':'external_rack'})
cap('method.configure',METHOD,obj({'configured':BOOL}),['sodium_lamp','analyzer','detector'],'标准Na589nm；积分1–100s，重复1–100为宿主限制；声明c/l仅用于分析','configuration','agent',METHOD_EXAMPLE)
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_face','power_inlet'],'逻辑供电，断电取消/熄灯/失效校准；无按钮接触',ex={'enabled':True})
cap('lid.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['sample_lid'],'工程90°掀盖；开盖终止采集并关闭灯门控',ex={'opened':False})
cap('lamp.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['sodium_lamp'],'标准Na灯逻辑光路门控；无灯预热/寿命模型',ex={'enabled':True})
cap('thermal.set_temperature',obj({'temperature_k':number('K',288.15,313.15)}),obj({'temperature_k':number('K')}),['peltier','sample_holder'],'标准温控15–40°C集总模型；设置不瞬时改变温度',ex={'temperature_k':293.15})
cap('acquisition.start',obj({'kind':dict(type='string',enum=['blank','sample','quartz'])}),obj({'phase':PHASE,'operation_id':TEXT}),['quartz_cell','analyzer','detector'],'先分析器有限速率就位，再完整积分；样品须匹配空白和石英校准',ex={'kind':'sample'})
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['sodium_lamp'],'取消保留已完成点与姿态/时间，不接受不完整校准')
cap('process.measure',obj({}),MEASURE,['host_screen'],'只读最后已完成积分点；没有测量时null','query')
cap('result.read',obj({}),RESULT,['host_screen'],'参考比旋光度驱动的模拟角度时序；不是原始测量文件','query')
cap('calibration.quartz',obj({'certified_angle_rad':number('rad',-1.3962634016,1.3962634016),'accept_simulated_standard':BOOL}),obj({'calibrated':BOOL,'gain_estimate':number('1',.1,10)}),['analyzer','detector'],'完成石英观测减空白后与调用者标准角比较；不读隐藏增益/真值',ex={'certified_angle_rad':math.radians(10),'accept_simulated_standard':True})
cap('quantify.specific',obj({'rotation_rad':arr(number('rad')),'declared_concentration_kg_m3':number('kg/m^3',.001,1000),'declared_path_m':number('m',.001,.1)}),SPECIFIC,['host_screen'],'只用观测平均角/声明c/l求比旋光度，重复SD非总不确定度','analysis','agent',{'rotation_rad':[math.radians(6.637)]*3,'declared_concentration_kg_m3':100.,'declared_path_m':.1})
cap('quantify.concentration',obj({'rotation_rad':number('rad'),'reference_specific_rotation_rad_m2_kg':number('rad*m^2/kg'),'declared_path_m':number('m',.001,.1),'same_conditions_confirmed':BOOL}),CONCENTRATION,['host_screen'],'观测角/调用者参考比旋光度及光程反算；条件匹配调用者确认','analysis','agent',{'rotation_rad':math.radians(6.637),'reference_specific_rotation_rad_m2_kg':66.37*math.pi/18000,'declared_path_m':.1,'same_conditions_confirmed':True})
cap('template.read',obj({'substance_id':TEXT}),TEMPLATE,['host_screen'],'公开文献参数，当前样品选择仅evaluator可见','query','agent',{'substance_id':'pubchem_5988'})
cap('product.read',obj({}),LEDGER,['sample_holder'],'预装池体积/溶质质量和独立温控能量私有账本','query','evaluator')
cap('ord.scan',obj({}),obj({}),['sodium_lamp'],'其他灯源/手动滤片选件未装，ORD光谱未实现','analysis')
cap('optical_purity.absolute',obj({}),obj({}),['detector'],'混合物/变旋/对映体绝对纯度未验证，不把角度比无条件称ee','analysis')
UNSUPPORTED={'ord.scan','optical_purity.absolute'}
# ---- device functions of the touch panel (kept out of CAPS, which stays the instrument command set the runtime suite exercises)
DEG=math.pi/180.;SR=math.pi/18000.                                     # deg -> rad; deg mL/(g dm) -> rad m^2/kg
MODES={'optical_rotation':('Optical Rotation','deg',4),'specific_rotation':('Specific O.R.','deg',2),'concentration':('Concentration','w/v%',4),'sugar_scale':('Sugar Scale','Z',3),'brix_purity':('Brix Purity','%',2),'optical_purity':('Optical Purity','%',2)}
BRIX_SG=[1.,1.00389,1.00779,1.01172,1.01567,1.01965,1.02366,1.0277,1.03176,1.03586,1.03998,1.04413,1.04831,1.05252,1.05677,1.06104,1.06534,1.06968,1.07404,1.07844,1.08287,1.08733,1.09183,1.09636,1.10092,1.10551,1.11014,1.1148,1.11949,1.12422,1.12898,1.13378,1.13861,1.14347,1.14837,1.15331,1.15828,1.16329,1.16833,1.17341,1.17853]   # manual p.84 table 10.1
DEFAULT_PARAMS=dict(data_mode='optical_rotation',cycle_interval_s=0.,sample_no=1,subtract_blank=True,blank_rad=0.,path_m=.1,concentration_kg_m3=10.,specific_rotation_rad_m2_kg=66.37*SR,sugar_factor=17.313,brix_percent=20.,sp_or_std_rad_m2_kg=66.37*SR,factor=1.,sample_name='',comment='',operator='',division='',meas_point='holder',temp_correct_per_k=0.,analyzer_after=False)
DEFAULT_DIT_S,DEFAULT_CYCLES=5.,1                                         # parameters of the method the device configures when none is set (assumption)
# in-cell sensor (Meas. Point Cell, P-2000 p.20 / p.77; standard on the P-4000: specifications 'Within cell holder/cell'): engineering values
CELL_SENSOR_TAU_S=60.        # first-order lag of the in-cell sensor behind the lumped holder / sample temperature
CONTROL_PERIOD_S=.5          # with Cell the controller re-computes the holder target every period (zero-order hold)
CELL_CASCADE_GAIN=1.         # holder target = set + gain*(set - cell reading) ...
CELL_HOLDER_SPAN_K=5.        # ... limited to set +/- 5 K
STR30=dict(type='string',maxLength=30)
PARAMS=dict(type='object',additionalProperties=False,required=[],properties={'data_mode':dict(type='string',enum=list(MODES)),'cycle_interval_s':number('s',0,15000),'sample_no':integer(1,999),'subtract_blank':BOOL,'blank_rad':number('rad',-math.pi/2,math.pi/2),'path_m':number('m',.001,.1),'concentration_kg_m3':number('kg/m^3',.001,1000),'specific_rotation_rad_m2_kg':number('rad*m^2/kg',-10000*SR,10000*SR),'sugar_factor':number('1',17,18),'brix_percent':number('1',.1,40),'sp_or_std_rad_m2_kg':number('rad*m^2/kg',-1000*SR,1000*SR),'factor':number('1',-10,10),'sample_name':STR30,'comment':STR30,'operator':STR30,'division':STR30,'meas_point':dict(type='string',enum=['cell','holder']),'temp_correct_per_k':number('1/K',-1,1),'analyzer_after':BOOL})
def opt(p,*req):return dict(type='object',properties=p,required=list(req),additionalProperties=False)
DEVICE_CAPS={}
def dcap(k,inp,out,c,z,ex):DEVICE_CAPS[k]=[inp,out,c,z,'device_command','agent',ex,[]]
OK=obj({'ok':BOOL})
dcap('device.light_source',opt({'na_lamp':BOOL,'non_control':BOOL,'apply':BOOL}),OK,['host_screen','sodium_lamp'],'开机光源选择窗口（P-2000 p.3）：勾选Na灯/Non-Control Mode，apply=按OK应用；开机10 s未按OK自动应用当前选择；也用于Control > Light Source',{'na_lamp':True,'apply':True})
dcap('device.control',opt({'analyzer':BOOL,'auto_meas':BOOL,'clear_message':BOOL}),OK,['host_screen','analyzer'],'Control菜单：Analyzer（检偏器保持运行并实时显示，打开光路门控）、Automatic Meas.（关盖自动开始测量）、确认设备消息',{'auto_meas':True})
dcap('device.parameters',obj({'settings':PARAMS}),OK,['host_screen'],'参数窗口（P-2000 p.19-21）中不属于method.configure的项：数据模式、循环间隔、样品号、扣空白与空白值、光程、浓度、比旋光度、糖度/Brix系数、标准比旋光度、系数、文本、测温点、温度校正系数、测后检偏器；SI单位',{'settings':{'data_mode':'specific_rotation','concentration_kg_m3':100.}})
dcap('device.zero_clear',opt({}),OK,['host_screen','analyzer'],'Zero Clear（P-2000 p.17）：把当前旋光读数置零（存储零点，此后屏幕、数据列表与scan.rotation_rad / result.read / poll的旋光读数都减去它；关机清除）',{})
dcap('measure.start',obj({'kind':dict(type='string',enum=['sample','blank'])}),obj({'started':BOOL,'waiting':BOOL}),['host_screen','sodium_lamp','analyzer'],'Start/Blank键：未设方法时按设备默认参数配置，按温度开始条件（类型1立即/2稳定/3穿越）立即或等待后打开光路门控并开始采集；样品室里是石英板时按石英采集；启动的采集（含等待温度后开始的）属于发出请求的会话，可poll/cancel',{'kind':'blank'})
dcap('measure.stop',opt({}),OK,['host_screen'],'Stop键：取消等待温度的开始或正在进行的采集；其他代理会话的运行或等待中的开始不能停止（ACCESS_DENIED，直接调用与面板相同）',{})
dcap('thermal.condition',opt({'control':BOOL,'meas_point':dict(type='string',enum=['cell','holder']),'start_type':integer(1,3),'tolerance_k':number('K',0,10),'stabilization_s':number('s',0,100000),'attain':integer(0,10000),'timeout_s':number('s',0,100000)}),OK,['host_screen','peltier'],'温度条件（P-2000 p.76-77表9.3）：控温开关、测温点（Cell池内传感器/Holder池架传感器：所选传感器给出显示温度、温度校正的t、开始条件，并作为Peltier控制的传感器，p.20、p.77）、开始条件类型、容差、稳定时间、穿越次数、超时；设定温度用thermal.set_temperature',{'start_type':2,'tolerance_k':.1,'stabilization_s':30.})
dcap('analysis.edit',opt({'op':dict(type='string',enum=['select','unselect','select_all','delete','recalc','clear']),'no':integer(0,100000),'mode':dict(type='string',enum=list(MODES))},'op'),OK,['host_screen'],'解析窗口（P-2000 p.22-23）：选择/取消计算行、全选、删除所选、按另一数据模式重算所选、清除全部数据；统计（平均、SD、RSD）随所选更新',{'op':'select','no':1,'mode':'optical_rotation'})
dcap('data.save',obj({'name':dict(type='string',minLength=1,maxLength=30),'content':dict(type='string',enum=['results','daily_check','validation'])}),OK,['host_screen'],'保存结果或导出文本到内部文件夹（P-2000 p.24；P-4000导出到指定文件夹）；USB/打印/LIMS在仿真中无设备',{'name':'result_01','content':'results'})
dcap('daily_check.start',obj({'jig_id':TEXT}),obj({'started':BOOL,'waiting':BOOL}),['host_screen','quartz_cell','analyzer'],'日常点检（应用说明280-PL-0008）：测已登记校准石英板的旋光度与光源能量（HT电压代理），按时间顺序列表',{'jig_id':'jig_1'})
dcap('jig.edit',opt({'op':dict(type='string',enum=['add','update','delete']),'jig_id':TEXT,'name':dict(type='string',minLength=1,maxLength=30),'certified_rad':number('rad',-math.pi/2,math.pi/2),'uncertainty_rad':number('rad',0,.01)},'op'),OK,['host_screen'],'管理>治具登记：登记/修改/删除点检与验证用的校准石英板（名称、校准值、扩展不确定度）',{'op':'add','jig_id':'','name':'Quartz +10','certified_rad':10*DEG,'uncertainty_rad':.002*DEG})
dcap('validation.configure',opt({'method':dict(type='string',enum=['basic','usp','ep','jp']),'jig_ids':dict(arr(TEXT),maxItems=5),'repeats':integer(2,100),'reference_temperature_k':nullable(number('K',273.15,373.15)),'inspector':STR30}),OK,['host_screen'],'验证参数（应用说明200-PL-0010，P-2000 p.51-56）：检查方法、石英板、重复次数、参考温度计读数（USP温控）、检查者',{'method':'basic','jig_ids':['jig_1'],'repeats':5,'reference_temperature_k':None,'inspector':''})
dcap('validation.start',opt({}),OK,['host_screen'],'开始验证：按屏幕提示逐步放置空白池/石英板并测量',{})
dcap('validation.next',opt({}),obj({'started':BOOL,'waiting':BOOL}),['host_screen','analyzer'],'验证提示对话框OK（或开启自动测量时关盖）：测量当前步骤',{})
dcap('validation.cancel',opt({}),OK,['host_screen'],'取消验证（结果丢弃，P-2000 p.48）',{})
dcap('users.edit',opt({'op':dict(type='string',enum=['add','delete','login']),'name':dict(type='string',minLength=1,maxLength=30),'role':dict(type='string',enum=['administrator','operator'])},'op','name'),OK,['host_screen'],'用户账户：添加/删除用户（管理员/测量者权限），切换登录用户；测量者不能改条件与管理项',{'op':'login','name':'Administrator','role':'administrator'})
dcap('backup.run',opt({}),OK,['host_screen'],'立即备份（设置与数据快照存于内部）',{})
dcap('backup.configure',obj({'auto_at_power_off':BOOL}),OK,['host_screen'],'关机时自动备份开关',{'auto_at_power_off':True})
dcap('diagnosis.run',opt({}),OK,['host_screen','sodium_lamp','temperature_probe','analyzer'],'自诊断（应用说明280-PL-0008）：光源、温度传感器、检偏器电机、Peltier状态',{})
def dslot(k,z,c='host_screen'):SLOTS[k]=(dict(type='object'),'1','derived','agent',[c],None);SLOT_ZH[k]=z
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='host_screen',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','显式宿主参数已配置','setpoint',c='body')
slot('method.configured',BOOL,'1','D线积分方法已设置','setpoint')
slot('power.enabled',BOOL,'1','软件供电','setpoint',c='power_face',w=('power.set','enabled'))
slot('lid.opened',BOOL,'1','样品盖开闭','scene',c='sample_lid',w=('lid.set','opened'))
slot('lid.angle_rad',number('rad',0,math.pi/2),'rad','工程盖角，轴-X','scene',c='sample_lid')
slot('lamp.enabled',BOOL,'1','软件Na灯光路门控','setpoint',c='sodium_lamp',w=('lamp.set','enabled'))
slot('sample.loaded',BOOL,'1','预装池逻辑挂接','scene',c='quartz_cell')
slot('thermal.setpoint_k',nullable(number('K',288.15,313.15)),'K','标准Peltier温度目标','setpoint',c='peltier',w=('thermal.set_temperature','temperature_k'))
slot('thermal.temperature_k',number('K'),'K','样品夹持器等温集总估计','derived',c='sample_holder')
slot('blank.ready',BOOL,'1','同光路/T空白就绪，液体另检查溶剂/光程')
slot('calibration.ready',BOOL,'1','观测石英比例校准就绪')
slot('calibration.gain',nullable(number('1')),'1','观测估计增益，不是隐藏增益')
slot('scan.phase',PHASE,'1','采集生命周期')
slot('scan.stage',dict(type='string',enum=['idle','slewing','integrating']),'1','有限转速/积分阶段')
slot('scan.elapsed_s',number('s',0),'s','本次采集时间，含分析器就位')
slot('scan.completed_points',integer(0,100),'1','完整积分次数')
slot('scan.rotation_rad',nullable(number('rad')),'rad','校准后最后观测角（与屏幕和数据列表同一模型：石英校正增益、按测温点温度的温度校正、扣空白值、Zero Clear），空白/石英未校准时null')
slot('scan.raw_rotation_rad',nullable(number('rad')),'rad','四相光度反算原始角，非私有真角')
slot('optics.analyzer_rad',number('rad',-math.pi/2,math.pi/2),'rad','由理想光度相位驱动的分析器姿态代理','scene',c='analyzer')
slot('method.wavelength_m',nullable(number('m')),'m','标准Na标称589nm','setpoint',c='sodium_lamp')
slot('method.integration_s',nullable(number('s')),'s','每次积分时间','setpoint',c='detector')
slot('safety.warning',TEXT,'1','不含私有样品身份的告警')
slot('truth.ledger',LEDGER,'1','预装池物料/真实模板身份与热量账本','process_truth','evaluator','sample_holder')
dslot('device.settings','触摸屏设备设置：光源选择窗口、参数窗口各项（SI）、方法摘要、Analyzer/自动测量、温度条件、零点、当前用户与权限、验证参数')
dslot('device.display','测量画面：数据模式、读数文字与单位、实时标志、状态、循环[n/N]、温度、空白值、样品号、待确认的设备消息代码')
dslot('device.data','数据：测量结果列表（可选计算行）与所选统计、内部文件、日常点检表、验证进度与历史')
dslot('device.admin','管理：登记的校准石英板、用户、备份、自诊断结果、装置状态（灯时、测量次数、温度、版本）')
def _panel_engine():
 try:
  from . import _panel_ui as engine                                   # imported as asset_runtime.jasco_p4200 (tests, Matterix bridge)
  return engine
 except ImportError:                                                  # loaded from its file path
  import sys
  if '_chemsim_panel_ui' not in sys.modules:
   spec=importlib.util.spec_from_file_location('_chemsim_panel_ui',Path(__file__).with_name('_panel_ui.py'));sys.modules['_chemsim_panel_ui']=importlib.util.module_from_spec(spec);spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
  return sys.modules['_chemsim_panel_ui']
_panel_ui=_panel_engine()
if os.environ.get('JASCO_P4200_PANEL_BUILD'):PANEL_SPEC,PANEL_DEFS=None,{}     # spec builder: runtime constants without the spec being written
else:
 PANEL_SPEC=_panel_ui.load_spec(ASSET_DIR/'panel_ui.json',capabilities=set(CAPS)|set(DEVICE_CAPS))
 PANEL_DEFS=_panel_ui.capability_definitions(PANEL_SPEC)                # panel.switch / touch / capture / frame
PANEL_CAPS={k:[d['input_schema'],d['output_schema'],d['component_ids'],d['function_zh'],d['kind'],d['visibility'],d['example_arguments'],[]] for k,d in PANEL_DEFS.items()}
ALL_CAPS={**CAPS,**DEVICE_CAPS,**PANEL_CAPS}
class JascoP4200Runtime:
 def __init__(self,instance_id='p4200_01'):
  self.instance_id=instance_id;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.time_s=0.;self.revision=0;self.plant=None;self.method=None;self.samples={};self.active=None
  self.powered=False;self.opened=True;self.lamp=False;self.temperature_k=293.15;self.setpoint_k=None;self.controller_energy_j=0.;self.ambient_loss_j=0.
  self.blank=None;self.calibration=None;self.phase='idle';self.stage='idle';self.kind='';self.elapsed_s=0.;self.points=[];self.warning='';self.operation_id='';self._op_count=0
  self.analyzer_rad=0.;self._target_angle=0.;self._remaining=0.;self._signals=np.zeros(4);self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._operation_records={};self._rng=np.random.default_rng(0)
  self.cell_k=293.15;self._cell_hold=None;self._caller=None             # in-cell sensor reading; held holder target (Meas. Point Cell); session making the current call
  self.dev=self._dev_initial();self.panel_capture_dir=None;self.panel=_panel_ui.PanelUI(PANEL_SPEC,self) if PANEL_SPEC else None
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return JascoP4200Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def debug_snapshot(self):
  v=copy.deepcopy({k:x for k,x in self.__dict__.items() if k not in ['templates','_rng','_signals','panel']});v['panel_ui']=copy.deepcopy(self.panel.ui) if self.panel else None;v['_rng_state']=copy.deepcopy(self._rng.bit_generator.state);v['_signals']=self._signals.tolist();return v
 def _sample(self):return self.samples.get(self.active)
 def _ledger(self):
  s=self._sample();vol=sum(v['volume_m3'] for v in self.samples.values());inside=s['volume_m3'] if s else 0.;mass=sum(v['volume_m3']*v['concentration_kg_m3'] for v in self.samples.values());insidemass=s['volume_m3']*s['concentration_kg_m3'] if s else 0.;delta=self.plant['heat_capacity_j_k']*(self.temperature_k-self.plant['ambient_k']) if self.plant else 0.
  return dict(registered_volume_m3=vol,attached_volume_m3=inside,external_volume_m3=vol-inside,volume_residual_m3=vol-inside-(vol-inside),registered_solute_kg=mass,attached_solute_kg=insidemass,external_solute_kg=mass-insidemass,mass_residual_kg=mass-insidemass-(mass-insidemass),thermal_delta_j=delta,controller_energy_j=self.controller_energy_j,ambient_loss_j=self.ambient_loss_j,thermal_residual_j=delta-self.controller_energy_j+self.ambient_loss_j,active_template_id=s['template_id'] if s else '',evidence='intact prefilled cell relocation, no reaction/evaporation; ideal quartz has no declared solution; lumped thermal energy only, not full lamp/electrical energy')
 def _optical_key(self):return (self.method['lamp'],self.method['wavelength_m'],self.setpoint_k) if self.method else None
 def _blank_ok(self):
  if not self.blank or not self.active or self.blank['key']!=self._optical_key():return False
  s=self._sample()
  return s['kind']=='quartz' or (s['solvent']==self.blank['solvent'] and s['path_m']==self.blank['path_m'] and s['reference_temperature_k']==self.blank['reference_temperature_k'])
 def _cal_ok(self):return bool(self.calibration and self.calibration['key']==self._optical_key())
 def _foreign_run(self):
  """An agent may not end another agent's measurement (Stop key / measure.stop / acquisition.stop, lamp off, validation cancel)
  nor cancel its start waiting for the temperature condition; the same check for the direct call and the panel gesture, which both run with the
  session caller in _caller.  Unowned runs and evaluator / debug callers are not restricted.  The lid and the power switch are physical and
  not guarded."""
  c=self._caller
  if c is None or self._principals.get(c)!='agent':return False
  q=self.dev['pending']
  o=self._owners.get(self.operation_id) if self.phase=='running' else q.get('owner') if q else None
  return o is not None and o!=c
 def read(self,visibility='agent'):
  if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
  p=self.points[-1] if self.points else {};m=self.method
  v={'plant.configured':bool(self.plant),'method.configured':bool(m),'power.enabled':self.powered,'lid.opened':self.opened,'lid.angle_rad':math.pi/2 if self.opened else 0.,'lamp.enabled':self.lamp,'sample.loaded':bool(self.active),'thermal.setpoint_k':self.setpoint_k,'thermal.temperature_k':self.temperature_k,'blank.ready':self._blank_ok(),'calibration.ready':self._cal_ok(),'calibration.gain':self.calibration['gain'] if self._cal_ok() else None,'scan.phase':self.phase,'scan.stage':self.stage,'scan.elapsed_s':self.elapsed_s,'scan.completed_points':len(self.points),'scan.rotation_rad':p.get('rotation_rad'),'scan.raw_rotation_rad':p.get('raw_rotation_rad'),'optics.analyzer_rad':self.analyzer_rad,'method.wavelength_m':m['wavelength_m'] if m else None,'method.integration_s':m['integration_s'] if m else None,'safety.warning':self.warning,'truth.ledger':self._ledger(),'device.settings':self._dev_settings(),'device.display':self._dev_display(),'device.data':self._dev_data(),'device.admin':self._dev_admin()}
  return copy.deepcopy({k:x for k,x in v.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _operation_snapshot(self):
  angles=self.session().read(instance_id=self.instance_id,state_ids=['scan.rotation_rad','scan.raw_rotation_rad'])['observations']
  return dict(operation_id=self.operation_id,status=self.phase,result=copy.deepcopy(self._invoke('process.measure',{})),observations=angles)
 def _archive_operation(self):
  if self.operation_id and self.phase in ('succeeded','cancelled','failed') and self.operation_id not in self._operation_records:
   self._operation_records[self.operation_id]=self._operation_snapshot()
 def _clear_result(self):
  self._archive_operation();self.points=[];self.phase='idle';self.stage='idle';self.kind='';self.elapsed_s=0.;self.warning=''
 def _invalidate(self):self.blank=None;self.calibration=None;self._clear_result()
 def _stop(self,phase,warning):
  if self.phase=='running':self.phase=phase
  self.stage='idle';self.lamp=False;self.warning=warning;self._archive_operation()
  t=self.dev['task'] if hasattr(self,'dev') else None
  if t:                                                               # a stopped daily-check / validation measurement: the step can be repeated
   self.dev['task']=None;self.dev['message']='MEASUREMENT_STOPPED'
   if t['kind']=='validation' and self.dev['val']:self.dev['val']['state']='prompt'
 @staticmethod
 def demodulate(signals):
  a=np.asarray(signals,dtype=float)
  if a.shape!=(4,) or not np.all(np.isfinite(a)):raise ActionRejected('INVALID_ARGUMENT')
  u=float(a[1]-a[3]);q=float(a[0]-a[2])
  if math.hypot(u,q)<1e-10:raise ActionRejected('LOW_POLARIZED_SIGNAL')
  return .5*math.atan2(u,q)
 def _signals_for_sample(self):
  s=self._sample();p=self.plant
  theta=0. if s['kind']=='blank' else s['quartz_angle_rad'] if s['kind']=='quartz' else self.templates[s['template_id']]['specific_rotation_rad_m2_kg']*s['concentration_kg_m3']*s['path_m']
  raw=p['instrument_gain']*theta+p['zero_offset_rad']
  # +/-90° equivalent under Malus; refuse the ambiguous endpoint and all out-of-branch angles.
  if abs(theta)>=math.pi/2-1e-8 or abs(raw)>=math.pi/2-1e-8:raise ActionRejected('ANGLE_BRANCH_RANGE')
  psi=np.arange(4)*math.pi/4
  signals=s['transmittance']*np.cos(psi-raw)**2+p['detector_dark']
  return signals,self.demodulate(signals)
 def invoke(self,capability_id,**arguments):
  if capability_id not in ALL_CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(ALL_CAPS[capability_id][0],arguments)
  if capability_id in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  if capability_id in PANEL_CAPS:return self._panel_command(capability_id,arguments)
  if ALL_CAPS[capability_id][4] in ['query','analysis']:return copy.deepcopy(self._invoke(capability_id,copy.deepcopy(arguments)))
  old={k:copy.deepcopy(v) for k,v in self.__dict__.items() if k not in ('templates','panel')};templates=self.templates;panel=self.panel
  try:result=self._invoke(capability_id,copy.deepcopy(arguments));self.revision+=1;return copy.deepcopy(result)
  except Exception:self.__dict__.clear();self.__dict__.update(old);self.templates=templates;self.panel=panel;raise
 def _invoke(self,k,a):
  if k=='template.read':
   r=self.templates.get(a['substance_id'])
   if r is None:raise ActionRejected('UNKNOWN_REFERENCE')
   return {**{q:r[q] for q in ['substance_id','name','specific_rotation_rad_m2_kg','evidence']},'conditions_json':json.dumps(r['conditions'],ensure_ascii=False),'source_json':json.dumps(r['source'],ensure_ascii=False)}
  if k=='product.read':return self._ledger()
  if k=='process.measure':
   p=self.points[-1] if self.points else {};return dict(phase=self.phase,rotation_rad=p.get('rotation_rad'),raw_rotation_rad=p.get('raw_rotation_rad'),elapsed_s=self.elapsed_s,completed_points=len(self.points),warning=self.warning)
  if k=='result.read':return dict(phase=self.phase,kind=self.kind,complete=self.phase=='succeeded',points=self.points,settings_json=json.dumps(self.method),template_source=TEMPLATE_REL+'; current identity restricted to evaluator',warning=self.warning,evidence='reference measured specific rotation parameter; simulated ideal Malus quadrature/servo/integration; blank and quartz simulated, no factory certification',limitations=['Not a measured P4200 trace or proprietary algorithm','Na D at source solvent/temperature and reported concentration only; missing concentration explicitly acknowledged','No noise/thermal/accuracy calibration, no ORD or mutarotation/mixture model','Returned detector_sigma excludes blank/quartz/reference uncertainty; not a total measurement uncertainty','Analysis only from caller observations and declared c/l, no hidden concentration or automatic ee'])
  if k=='quantify.specific':
   values=a['rotation_rad']
   if not 1<=len(values)<=100 or any(abs(v)>=math.pi/2 for v in values):raise ActionRejected('ANALYSIS_INPUT')
   mean=float(np.mean(values));return dict(mean_rotation_rad=mean,specific_rotation_rad_m2_kg=mean/(a['declared_concentration_kg_m3']*a['declared_path_m']),repeat_sd_rad=float(np.std(values,ddof=1)) if len(values)>1 else None,count=len(values),evidence='observed angles and caller-declared concentration/path only; repeat SD excludes systematic/reference uncertainty; correct solvent/T/wavelength required')
  if k=='quantify.concentration':
   alpha=a['reference_specific_rotation_rad_m2_kg']
   if not a['same_conditions_confirmed'] or abs(alpha)<1e-12:raise ActionRejected('REFERENCE_CONDITIONS_MISMATCH')
   concentration=a['rotation_rad']/(alpha*a['declared_path_m'])
   if concentration<0 or abs(a['rotation_rad'])>=math.pi/2:raise ActionRejected('ANALYSIS_INPUT')
   return dict(concentration_kg_m3=concentration,evidence='caller-observed angle / caller reference alpha and path; source solvent/T/wavelength/concentration-dependence conditions caller confirmed; no hidden sample fields')
  if k=='plant.configure':
   if self.plant or self.powered or not a['accept_uncalibrated_model']:raise ActionRejected('INITIALIZATION_ONLY')
   self.plant=a;self.temperature_k=a['ambient_k'];self.cell_k=a['ambient_k'];self.setpoint_k=a['ambient_k'];self._rng=np.random.default_rng(a['seed']);return {'configured':True}
  if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
  if self.phase=='running' and k in ['sample.register','sample.load','sample.unload','method.configure','thermal.set_temperature','calibration.quartz']:raise ActionRejected('BUSY')
  if k in DEVICE_CAPS:return self._device(k,a)
  if k=='sample.register':
   if a['sample_id'] in self.samples or not a['sample_id']:raise ActionRejected('REQUEST_CONFLICT')
   if a['kind']=='quartz':
    if not a['accept_ideal_quartz'] or a['quartz_angle_rad'] is None or abs(a['quartz_angle_rad'])<math.radians(.1) or a['volume_m3'] or a['concentration_kg_m3'] or a['solvent'] or a['template_id']:raise ActionRejected('QUARTZ_ASSUMPTION_REQUIRED')
   else:
    if a['quartz_angle_rad'] is not None or a['volume_m3']<1e-6 or not a['solvent']:raise ActionRejected('CELL_CONFIGURATION')
    if a['kind']=='blank':
     if a['template_id'] or a['concentration_kg_m3']:raise ActionRejected('INVALID_ARGUMENT')
    else:
     if a['template_id'] not in self.templates or a['concentration_kg_m3']<=0:raise ActionRejected('UNKNOWN_REFERENCE')
     r=self.templates[a['template_id']]['conditions'];c=r['concentration_kg_m3']
     if a['solvent']!=r['solvent']:raise ActionRejected('SOURCE_SOLVENT_MISMATCH')
     if abs(a['reference_temperature_k']-r['temperature_k'])>1e-8:raise ActionRejected('SOURCE_TEMPERATURE_MISMATCH')
     if c is None and not a['accept_missing_source_concentration']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
     if c is not None and abs(c-a['concentration_kg_m3'])>max(1e-8,c*1e-6):raise ActionRejected('SOURCE_CONCENTRATION_MISMATCH')
   self.samples[a['sample_id']]=a;return {'registered':True}
  if k=='sample.load':
   if not self.opened or self.active or a['sample_id'] not in self.samples:raise ActionRejected('SAMPLE_ACCESS_REQUIRED')
   self.active=a['sample_id'];self._clear_result();return dict(loaded=True,attachment_event='host_attach_prefilled_cell_or_quartz')
  if k=='sample.unload':
   if not self.opened or not self.active or not a['recipient_id']:raise ActionRejected('SAMPLE_ACCESS_REQUIRED')
   self.active=None;return dict(unloaded=True,attachment_event='host_detach_intact_cell_or_quartz')
  if k=='method.configure':self.method=a;self._invalidate();return {'configured':True}
  if k=='power.set':
   was=self.powered;self.powered=a['enabled']
   if not self.powered:self._stop('cancelled','POWER_OFF');self.blank=None;self.calibration=None
   self._dev_power(was,self.powered)
   return a
  if k=='lid.set':
   if a['opened']:self._stop('cancelled','LID_OPEN')                    # physical lid: never refused, the interlock ends the run
   was=self.opened;self.opened=a['opened']
   if was and not self.opened and self.dev['auto_meas'] and self.powered:   # Automatic Meas.: closing the lid starts the measurement (P-2000 p.17)
    err=self._try(self._dev_auto)
    if err:self.dev['message']=err
   return a
  if k=='lamp.set':
   if a['enabled']:
    if not self.powered or self.opened or not self.method:raise ActionRejected('OPTICS_NOT_READY')
   elif self.phase=='running':
    if self._foreign_run():raise ActionRejected('ACCESS_DENIED')
    self._stop('cancelled','LAMP_OFF')
   self.lamp=a['enabled'];return a
  if k=='thermal.set_temperature':self.setpoint_k=a['temperature_k'];self._invalidate();return a
  if k=='acquisition.stop':
   if self.phase=='running' and self._foreign_run():raise ActionRejected('ACCESS_DENIED')
   self._stop('cancelled','CANCELLED');return {'phase':self.phase}
  if k=='calibration.quartz':
   if self.phase!='succeeded' or self.kind!='quartz' or not self._blank_ok() or not a['accept_simulated_standard'] or abs(a['certified_angle_rad'])<math.radians(.1):raise ActionRejected('QUARTZ_CALIBRATION_REQUIRED')
   estimate=(float(np.mean([p['raw_rotation_rad'] for p in self.points]))-self.blank['zero'])/a['certified_angle_rad']
   if not .1<=estimate<=10:raise ActionRejected('INVALID_CALIBRATION')
   self.calibration=dict(key=self._optical_key(),gain=estimate);return dict(calibrated=True,gain_estimate=estimate)
  if k=='acquisition.start':
   if not self.powered or self.opened or not self.lamp:raise ActionRejected('OPTICS_NOT_READY')
   if self.phase=='running':raise ActionRejected('BUSY')
   if not self.method or not self.active:raise ActionRejected('SAMPLE_METHOD_REQUIRED')
   s=self._sample()
   if s['kind']!=a['kind']:raise ActionRejected('SAMPLE_KIND_MISMATCH')
   if abs(self.temperature_k-s['reference_temperature_k'])>.2 or abs(self.setpoint_k-s['reference_temperature_k'])>1e-8:raise ActionRejected('REFERENCE_TEMPERATURE_MISMATCH')
   if a['kind'] in ['quartz','sample'] and not self._blank_ok():raise ActionRejected('BLANK_REQUIRED')
   if a['kind']=='sample' and not self._cal_ok():raise ActionRejected('QUARTZ_CALIBRATION_REQUIRED')
   signals,target=self._signals_for_sample();self._archive_operation()
   if a['kind']=='blank':self.blank=None;self.calibration=None
   self._signals=signals;self._target_angle=target;self._remaining=abs(target-self.analyzer_rad)/math.radians(6)
   self.stage='slewing' if self._remaining>1e-12 else 'integrating'
   if self.stage=='integrating':self._remaining=self.method['integration_s']
   self.points=[];self.elapsed_s=0.;self.kind=a['kind'];self.phase='running';self.warning=''
   self._op_count+=1;self.operation_id=self.instance_id+':'+str(self._op_count);return dict(phase=self.phase,operation_id=self.operation_id)
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _thermal(self,dt):
  p=self.plant
  if not p:return
  if not (self.powered and self.dev['temp']['control'] and self.dev['params']['meas_point']=='cell'):self._holder(self.setpoint_k,dt);return
  # Meas. Point Cell (P-2000 p.77 "selects the sensor for temperature control"): the controller holds the in-cell sensor at the set temperature by
  # moving the holder target (cascade, zero-order hold on the absolute control period); at steady state both equal the set temperature
  t0=self.time_s;remaining=dt;P=CONTROL_PERIOD_S
  while remaining>1e-12:
   k=math.floor((t0+1e-9)/P)
   if self._cell_hold is None or self._cell_hold[0]!=k:
    sp=self.setpoint_k;self._cell_hold=(k,min(max(sp+CELL_CASCADE_GAIN*(sp-self.cell_k),sp-CELL_HOLDER_SPAN_K),sp+CELL_HOLDER_SPAN_K))
   h=min(remaining,max((k+1)*P-t0,1e-12));self._holder(self._cell_hold[1],h);t0+=h;remaining-=h
 @staticmethod
 def _lag(x,eq,d,bb,s):
  """In-cell sensor x' = (T(s)-x)/tau following the holder temperature T(s) = eq + d*exp(-bb*s) of one exact piece, integrated exactly over s."""
  a=1./CELL_SENSOR_TAU_S;ea=math.exp(-a*s)
  if abs(a-bb)>1e-9:A=d*a/(a-bb);return eq+A*math.exp(-bb*s)+(x-eq-A)*ea
  return eq+(x-eq)*ea+d*a*s*ea
 def _holder(self,target,dt):
  """Exact piecewise-affine integration of the lumped holder / sample with the proportional Peltier controller holding `target`."""
  p=self.plant
  C=p['heat_capacity_j_k'];U=p['thermal_ua_w_k'];K=p['control_gain_w_k'];limit=p['peltier_limit_w'];ambient=p['ambient_k'];remaining=dt
  for _ in range(8):
   if remaining<=1e-13:break
   t=self.temperature_k;control=bool(self.powered) and self.dev['temp']['control'];feed=U*(target-ambient) if control else 0.;demand=K*(target-t)+feed if control else 0.
   if not control:aa=U*ambient/C;bb=U/C;boundary=None
   elif demand>limit+1e-10:aa=(limit+U*ambient)/C;bb=U/C;boundary=target+(feed-limit)/K
   elif demand<-limit-1e-10:aa=(-limit+U*ambient)/C;bb=U/C;boundary=target+(feed+limit)/K
   else:aa=(K+U)*target/C;bb=(K+U)/C;boundary=None
   equilibrium=aa/bb;span=remaining
   if boundary is not None and abs(t-equilibrium)>1e-15:
    ratio=(boundary-equilibrium)/(t-equilibrium)
    if 0<ratio<1:
     hit=-math.log(ratio)/bb
     if hit>1e-13:span=min(span,hit)
   new=equilibrium+(t-equilibrium)*math.exp(-bb*span);integral=(equilibrium-ambient)*span+(t-equilibrium)*(-math.expm1(-bb*span))/bb
   self.cell_k=self._lag(self.cell_k,equilibrium,t-equilibrium,bb,span)
   loss=U*integral;self.ambient_loss_j+=loss;self.controller_energy_j+=C*(new-t)+loss;self.temperature_k=new;remaining-=span
  if remaining>1e-10:raise RuntimeError('thermal branch convergence')
 def _meas_k(self):
  """The sample temperature the device measures: the in-cell or the holder sensor, as Meas. Point selects (P-2000 p.20, p.77)."""
  return self.cell_k if self.dev['params']['meas_point']=='cell' else self.temperature_k
 def _record(self):
  m=self.method;p=self.plant;s=self._sample();sigma=p['noise_density_sqrt_s']/math.sqrt(m['integration_s'])
  signals=self._signals+self._rng.normal(0.,sigma,4)
  try:raw=self.demodulate(signals)
  except ActionRejected:self._stop('failed','LOW_POLARIZED_SIGNAL');return
  # atan2 returns a principal angle: noise can wrap +90 degrees to -90.
  # The settled analyzer pose provides a continuous measured-phase reference.
  if abs(raw-self.analyzer_rad)>=math.pi/2 or abs(raw)>=math.pi/2-1e-8:
   self._stop('failed','ANGLE_BRANCH_RANGE');return
  # one rotation model for every reading path (P-2000 p.82): the device's quartz correction gain, temperature correction at the measured
  # temperature, blank value and Zero Clear (_alpha); the screen, the data list, scan.rotation_rad, result.read and poll all use these points
  t_k=self._meas_k();calibrated=self._alpha(raw,t_k,self.dev['params']) if self._blank_ok() and self._cal_ok() else None
  if calibrated is not None and abs(calibrated)>=math.pi/2:self._stop('failed','ANGLE_BRANCH_RANGE');return
  self.points.append(dict(raw_rotation_rad=raw,rotation_rad=calibrated,photometric_channels=signals.tolist(),sample_time_s=self.time_s,integration_s=m['integration_s'],elapsed_s=self.elapsed_s,temperature_k=t_k,detector_sigma_rad=math.sqrt(2)*sigma/(2*s['transmittance'])))
  if len(self.points)==m['repeats']:
   self.phase='succeeded';self.stage='idle';self.lamp=bool(self.dev['analyzer'] or self.dev['params']['analyzer_after'])   # the analyzer stays active only when set so
   if self.kind=='blank':self.blank=dict(key=self._optical_key(),solvent=s['solvent'],path_m=s['path_m'],reference_temperature_k=s['reference_temperature_k'],zero=float(np.mean([x['raw_rotation_rad'] for x in self.points])))
   self._archive_operation();self._dev_completed()
  else:self._remaining=max(m['integration_s'],self.dev['params']['cycle_interval_s'])   # Cycle Interval: next point at the interval (P-2000 p.19)
 def step(self,dt_s):
  validate(number('s',0,86400),dt_s)
  if dt_s==0:return
  remaining=dt_s
  while remaining>1e-12:
   dt=min(remaining,max(0.,self._remaining)) if self.phase=='running' else remaining
   if dt>1e-13:
    self._thermal(dt)
    if self.phase=='running':
     if self.stage=='slewing':
      delta=self._target_angle-self.analyzer_rad;self.analyzer_rad+=math.copysign(min(abs(delta),math.radians(6)*dt),delta)
     self.elapsed_s+=dt;self._remaining=max(0.,self._remaining-dt)
    self.time_s+=dt;remaining-=dt;self._dev_step(dt)
   if self.phase=='running' and abs(self.temperature_k-self._sample()['reference_temperature_k'])>.2:self._stop('failed','REFERENCE_TEMPERATURE_MISMATCH')
   if self.phase=='running' and self._remaining<=1e-12:
    if self.stage=='slewing':self.analyzer_rad=self._target_angle;self.stage='integrating';self._remaining=self.method['integration_s']
    else:self._record()
   if dt<=1e-13 and self.phase!='running':self._thermal(remaining);self.time_s+=remaining;self._dev_step(remaining);remaining=0.
  self.revision+=1
 # ---- device functions behind the touch panel (DEVICE_CAPS); P-2000 manual pages as cited, P-4000 product page / application notes
 def _dev_initial(self):
  return dict(started=False,na_lamp=True,non_control=False,power_on_s=None,params=copy.deepcopy(DEFAULT_PARAMS),zero_rad=0.,analyzer=False,auto_meas=False,
   temp=dict(control=True,start_type=1,tolerance_k=.2,stabilization_s=60.,attain=1,timeout_s=600.),pending=None,task=None,results=[],next_no=1,files=[],
   daily=[],jigs=[],next_jig=1,val_config=dict(method='basic',jig_ids=[],repeats=5,reference_temperature_k=None,inspector=''),val=None,validations=[],
   users=[dict(name='Administrator',role='administrator')],user='Administrator',backups=[],auto_backup=False,diagnosis=None,lamp_s=0.,measurements=0,message='')
 def _role(self):return next((u['role'] for u in self.dev['users'] if u['name']==self.dev['user']),'operator')
 def _admin_only(self):
  if self._role()!='administrator':raise ActionRejected('ACCESS_RIGHTS')
 def _dev_ready(self):
  d=self.dev
  if not self.powered:raise ActionRejected('PRECONDITION_FAILED')
  if not d['started']:raise ActionRejected('LIGHT_SOURCE_WINDOW')
  if d['non_control']:raise ActionRejected('NON_CONTROL_MODE')
  if not d['na_lamp']:raise ActionRejected('LIGHT_SOURCE_OFF')
 def _dev_busy(self):
  d=self.dev
  if self.phase=='running' or d['pending'] or (d['val'] and d['val']['state']!='done'):raise ActionRejected('BUSY')
 def _gain(self):return self.calibration['gain'] if self._cal_ok() else 1.
 def _live(self):return self.phase!='running' and self.lamp and (self.dev['analyzer'] or self.dev['params']['analyzer_after'])
 def _reading_now(self):
  """The rotation on the screen (rad): the live analyzer pose while it runs, the last point while measuring, else the mean of the last measurement."""
  p=self.dev['params']
  if self._live():return self._alpha(self.analyzer_rad,self._meas_k(),p)
  if not self.points:return None
  al=[self._alpha(x['raw_rotation_rad'],x['temperature_k'],p) for x in self.points]
  return al[-1] if self.phase=='running' else float(np.mean(al))
 def _alpha(self,raw,t_k,p):
  """Optical rotation (rad) of the device, manual p.82: the quartz correction gain, alpha20=alpha_t/(1+k(t-20)) with t from the Meas. Point sensor
  (Brix purity k=-0.00037, p.83), minus the blank value, minus the Zero Clear (p.17: the current rotation set to 0)."""
  k=-.00037 if p['data_mode']=='brix_purity' else p['temp_correct_per_k'];g=self._gain()
  a=(raw/g)/(1+k*(t_k-293.15))
  return a-(p['blank_rad']/g if p['subtract_blank'] else 0.)-self.dev['zero_rad']
 @staticmethod
 def mode_value(alpha,p,mode=None):
  """Value of a data mode from a displayed rotation (manual p.82-84, deg, w/v%, mm); None where a divisor is zero; times the Factor (p.20)."""
  m=mode or p['data_mode'];d=math.degrees(alpha);L=p['path_m']*1000.;C=p['concentration_kg_m3']/10.
  def div(x,y):return None if abs(y)<1e-15 else x/y
  if m=='optical_rotation':v=d
  elif m=='specific_rotation':v=div(d*10000.,C*L)
  elif m=='concentration':v=div(d*10000.,p['specific_rotation_rad_m2_kg']/SR*L)
  elif m=='sugar_scale':v=div(d*10000.,p['sugar_factor']*L)
  elif m=='brix_purity':
   b=p['brix_percent'];i=min(int(b),39);sg=BRIX_SG[i]+(BRIX_SG[i+1]-BRIX_SG[i])*(b-i)
   v=div(26.*d*10000./(p['sugar_factor']*L),99.718*sg*b/100.)
  else:
   s=div(d*10000.,C*L);v=None if s is None else div(100.*s,p['sp_or_std_rad_m2_kg']/SR)
  return None if v is None else v*p['factor']
 @staticmethod
 def _num(v,n,sign=''):return f'{(round(v,n) or 0.):{sign}.{n}f}'               # display text without a negative zero
 @classmethod
 def _fmt(cls,v,mode):return '-----' if v is None else cls._num(v,MODES[mode][2])
 @staticmethod
 def _spec_accuracy(cert):return .002*DEG if abs(cert)<=DEG else .002*abs(cert)   # P-4200 accuracy +/-0.002 deg (<=1 deg), +/-0.2 % (>1 deg)
 def _jig(self,jid):
  j=next((x for x in self.dev['jigs'] if x['id']==jid),None)
  if j is None:raise ActionRejected('UNKNOWN_REFERENCE')
  return j
 def _ensure_method(self):
  p=self.dev['params']
  if not self.method:self._invoke('method.configure',dict(lamp='Na',wavelength_m=589e-9,integration_s=DEFAULT_DIT_S,repeats=DEFAULT_CYCLES,declared_concentration_kg_m3=p['concentration_kg_m3'],declared_path_m=p['path_m']))
 def _dev_begin(self,kind,task=None,owner=None):
  """Open the light path and start the acquisition through the same commands a direct caller uses (a quartz plate in the holder is measured as a plate);
  the operation belongs to the session that asked for it (owner)."""
  self._ensure_method();s=self._sample()
  if kind=='sample' and s is not None and s['kind']=='quartz':kind='quartz'
  if not self.lamp:self._invoke('lamp.set',{'enabled':True})
  out=self._invoke('acquisition.start',{'kind':kind});self.dev['task']=task;self.dev['message']=''
  if owner:self._owners[out['operation_id']]=owner
  return out
 def _dev_request(self,kind,task):
  """Start now (start condition type 1) or wait for the temperature start condition (types 2 / 3, P-2000 p.77); the requesting session owns the run."""
  if self.dev['temp']['start_type']==1 or not self.dev['temp']['control']:self._dev_begin(kind,task,self._caller);return dict(started=True,waiting=False)
  self.dev['pending']=dict(kind=kind,task=task,elapsed_s=0.,stable_s=0.,sign=None,crossings=0,owner=self._caller);return dict(started=False,waiting=True)
 def _try(self,fn):
  """Run a device action atomically from the clock or a hook: on a refusal the state is restored and the code returned."""
  old={k:copy.deepcopy(v) for k,v in self.__dict__.items() if k not in ('templates','panel')};templates=self.templates;panel=self.panel
  try:fn();return None
  except ActionRejected as exc:self.__dict__.clear();self.__dict__.update(old);self.templates=templates;self.panel=panel;return str(exc)
 def _dev_auto(self):
  v=self.dev['val']
  if v and v['state']=='prompt':self._val_next();return
  if self.phase!='running' and not self.dev['pending']:self._dev_ready();self._dev_request('sample',None)
 def _val_next(self):
  v=self.dev['val'];st=v['steps'][v['index']];self._dev_ready()
  out=self._dev_request(st['kind'],dict(kind='validation'));v['state']='measuring';return out
 def _dev_power(self,was,now):
  d=self.dev
  if was and not now:                                                 # switching off: the light-source window comes back at the next power-up
   if d['auto_backup']:self._dev_backup('power-off')
   d.update(started=False,pending=None,task=None,val=None,zero_rad=0.,analyzer=False,message='',power_on_s=None)
  if now and not was:d.update(started=False,power_on_s=self.time_s)
 def _dev_backup(self,why):
  d=self.dev;d['backups'].append(dict(no=len(d['backups'])+1,time_s=self.time_s,trigger=why,results=len(d['results']),daily=len(d['daily']),validations=len(d['validations']),jigs=len(d['jigs'])))
 def _dev_step(self,dt):
  d=self.dev
  if not self.powered:return
  if not d['started'] and d['power_on_s'] is not None and self.time_s-d['power_on_s']>=10.-1e-9:d['started']=True   # P-2000 p.3: applied after 10 s
  if d['started'] and d['na_lamp'] and not d['non_control']:d['lamp_s']+=dt
  if self._live() and self.active and not self.opened:                # the running analyzer follows the sample in real time (6 deg/s)
   try:target=self._signals_for_sample()[1]
   except ActionRejected:target=None
   if target is not None:delta=target-self.analyzer_rad;self.analyzer_rad+=math.copysign(min(abs(delta),math.radians(6)*dt),delta)
  q=d['pending']
  if not q:return
  c=d['temp'];t=self._meas_k();s=self.setpoint_k;q['elapsed_s']+=dt
  if c['start_type']==2:
   q['stable_s']=q['stable_s']+dt if abs(t-s)<=c['tolerance_k']+1e-12 else 0.;ok=q['stable_s']>=c['stabilization_s']-1e-9
  else:
   sign=1 if t>=s else -1
   if q['sign'] is not None and sign!=q['sign']:q['crossings']+=1
   q['sign']=sign;ok=q['crossings']>=c['attain']
  if ok:
   d['pending']=None;err=self._try(lambda:self._dev_begin(q['kind'],q['task'],q.get('owner')))
   if err:
    self.dev['message']=err
    if q['task'] and q['task']['kind']=='validation' and self.dev['val']:self.dev['val']['state']='prompt'
  elif c['timeout_s']>0 and q['elapsed_s']>=c['timeout_s']-1e-9:
   d['pending']=None;d['message']='TEMPERATURE_TIMEOUT'
   if q['task'] and q['task']['kind']=='validation' and d['val']:d['val']['state']='prompt'
 def _fill(self,r,mode):
  vals=[None if r['kind']=='blank' else self.mode_value(a,r['params'],mode) for a in r['alphas_rad']]
  if r['kind']=='blank':vals=[math.degrees(a) for a in r['alphas_rad']];mode='optical_rotation'
  ok=[x for x in vals if x is not None];r.update(mode=mode,values=vals,value=float(np.mean(ok)) if ok else None,sd=float(np.std(ok,ddof=1)) if len(ok)>1 else None)
 def _dev_completed(self):
  """A completed acquisition: the blank writes the blank value (P-2000 p.21), a measurement goes to the data list (p.22) and a sample advances the sample number
  (p.20); daily-check and validation measurements go to their own tables."""
  d=self.dev;p=d['params'];raws=[x['raw_rotation_rad'] for x in self.points];kind=self.kind;task=d['task'];d['task']=None;d['measurements']+=1;owner=self._owners.get(self.operation_id)
  if kind=='blank':p['blank_rad']=self.blank['zero']
  if task is None:
   alphas=[r for r in raws] if kind=='blank' else [self._alpha(x['raw_rotation_rad'],x['temperature_k'],p) for x in self.points]   # the points' own model
   r=dict(no=d['next_no'],kind=kind,sample_no=p['sample_no'] if kind=='sample' else None,sample=p['sample_name'],comment=p['comment'],operator=p['operator'],division=p['division'],temperature_k=self._meas_k(),time_s=self.time_s,alphas_rad=alphas,params={k:p[k] for k in ('path_m','concentration_kg_m3','specific_rotation_rad_m2_kg','sugar_factor','brix_percent','sp_or_std_rad_m2_kg','factor')},selected=False)
   self._fill(r,p['data_mode']);d['results'].append(r);d['next_no']+=1
   if kind=='sample':p['sample_no']=min(999,p['sample_no']+1)
   return
  plate=float(np.mean([(x-self.blank['zero'])/self._gain() for x in raws])) if kind=='quartz' and self.blank else None
  if task['kind']=='daily':
   j=self._jig(task['jig_id']);energy=float(np.mean([sum(x['photometric_channels'])/2. for x in self.points]));ht=min(999.,250./math.sqrt(max(energy,1e-6)))
   dev=plate-j['certified_rad'];tol=j['uncertainty_rad']+self._spec_accuracy(j['certified_rad'])
   d['daily'].append(dict(no=len(d['daily'])+1,time_s=self.time_s,jig_id=j['id'],jig=j['name'],certified_rad=j['certified_rad'],measured_rad=plate,deviation_rad=dev,tolerance_rad=tol,ht_v=ht,passed=bool(abs(dev)<=tol+1e-15 and ht<=300.)))
   return
  v=d['val'];st=v['steps'][v['index']];st['values'].append(float(np.mean(raws)) if kind=='blank' else plate)
  if len(st['values'])<st['n']:                                        # the next repeat of this step starts by itself
   err=self._try(lambda:self._dev_begin(st['kind'],dict(kind='validation'),owner))
   if err:self.dev['message']=err;self.dev['val']['state']='prompt'
   return
  v['index']+=1
  if v['index']<len(v['steps']):v['state']='prompt'
  else:self._val_finish(v)
 def _val_finish(self,v):
  """Validation results (application note 200-PL-0010 table 1 items; criteria from the P-4200 specification and P-2000 p.44-47)."""
  c=v['config'];st=v['steps'];items=[];R=.004*DEG
  def acc(s):
   j=self._jig(s['jig_id']);m=float(np.mean(s['values']));dev=m-j['certified_rad'];tol=j['uncertainty_rad']+self._spec_accuracy(j['certified_rad'])
   return dict(jig=j['name'],mean_rad=m,deviation_rad=dev,tolerance_rad=tol,passed=bool(abs(dev)<=tol+1e-15))
  if c['method']=='usp':
   ref=c['reference_temperature_k'];dt=None if ref is None else v['temperature_k']-ref
   items.append(dict(item='Temperature control',value_text='not entered' if dt is None else f'{dt:+.2f} C',criterion_text='within +/-0.5 C',passed=bool(dt is not None and abs(dt)<=.5)))
  if c['method']=='basic':
   z=st[0]['values'];rz=max(z)-min(z);items.append(dict(item='Zero repeatability',value_text=f'{math.degrees(rz):.4f} deg',criterion_text='max-min <= 0.0040 deg',passed=bool(rz<=R+1e-15)))
  a=acc(st[1]);items.append(dict(item='Accuracy of O.R.',value_text=self._num(math.degrees(a['deviation_rad']),4,'+')+' deg',criterion_text=f'+/-{math.degrees(a["tolerance_rad"]):.4f} deg',passed=a['passed']))
  if c['method'] in ('basic','usp'):
   x=st[1]['values'];rr=max(x)-min(x);items.append(dict(item='Repeatability of O.R.',value_text=f'{math.degrees(rr):.4f} deg',criterion_text='max-min <= 0.0040 deg',passed=bool(rr<=R+1e-15)))
  if c['method'] in ('usp','ep'):
   al=[acc(s) for s in st[1:]];items.append(dict(item='Linearity of O.R.',value_text=f'{sum(x["passed"] for x in al)}/{len(al)} plates within accuracy',criterion_text='all plates within accuracy',passed=all(x['passed'] for x in al)))
  res=dict(no=len(self.dev['validations'])+1,time_s=self.time_s,method=c['method'],inspector=c['inspector'],items=items,passed=all(i['passed'] for i in items))
  self.dev['validations'].append(res);v.update(state='done',result=res)
 def _device(self,k,a):
  d=self.dev
  if not self.powered:raise ActionRejected('PRECONDITION_FAILED')
  if k=='device.light_source':
   for q in ('na_lamp','non_control'):
    if q in a:d[q]=a[q]
   if a.get('apply'):d['started']=True
   if d['started'] and (not d['na_lamp'] or d['non_control']) and (self.lamp or self.phase=='running'):self._invoke('lamp.set',{'enabled':False});d['analyzer']=False
   return {'ok':True}
  if k=='device.control':
   if 'analyzer' in a:
    if a['analyzer']:
     self._dev_ready();self._ensure_method()
     if not self.lamp:self._invoke('lamp.set',{'enabled':True})       # the running analyzer needs light (OPTICS_NOT_READY with the lid open)
    elif self.lamp and self.phase!='running':self._invoke('lamp.set',{'enabled':False})
    d['analyzer']=a['analyzer']
   if 'auto_meas' in a:d['auto_meas']=a['auto_meas']
   if a.get('clear_message'):d['message']=''
   return {'ok':True}
  if k=='device.parameters':d['params']=dict(d['params'],**a['settings']);return {'ok':True}
  if k=='device.zero_clear':
   self._dev_ready();cur=self._reading_now()                         # the shown rotation becomes 0 (p.17); with no reading the analyzer position
   d['zero_rad']+=self._alpha(self.analyzer_rad,self._meas_k(),d['params']) if cur is None else cur;return {'ok':True}
  if k=='measure.start':self._dev_ready();self._dev_busy();return self._dev_request(a['kind'],None)
  if k=='measure.stop':
   if self._foreign_run():raise ActionRejected('ACCESS_DENIED')
   q=d['pending'];d['pending']=None
   if q and q['task'] and q['task']['kind']=='validation' and d['val']:d['val']['state']='prompt'
   if self.phase=='running':self._invoke('acquisition.stop',{})
   return {'ok':True}
  if k=='thermal.condition':
   self._admin_only();d['temp'].update({q:x for q,x in a.items() if q!='meas_point'})
   if 'meas_point' in a:d['params']['meas_point']=a['meas_point']
   return {'ok':True}
  if k=='analysis.edit':
   op=a['op'];rs=d['results']
   if op in ('select','unselect'):
    r=next((x for x in rs if x['no']==a.get('no')),None)
    if r is None:raise ActionRejected('UNKNOWN_REFERENCE')
    r['selected']=op=='select'
   elif op=='select_all':
    for r in rs:r['selected']=True
   elif op=='delete':d['results']=[r for r in rs if not r['selected']]
   elif op=='recalc':
    if 'mode' not in a:raise ActionRejected('INVALID_ARGUMENT')
    for r in rs:
     if r['selected'] and r['kind']!='blank':self._fill(r,a['mode'])
   else:d['results']=[]
   return {'ok':True}
  if k=='data.save':
   if any(f['name']==a['name'] for f in d['files']):raise ActionRejected('FILE_EXISTS')
   rows={'results':len(d['results']),'daily_check':len(d['daily']),'validation':len(d['validations'])}[a['content']]
   d['files'].append(dict(name=a['name'],content=a['content'],rows=rows,time_s=self.time_s));return {'ok':True}
  if k=='daily_check.start':
   self._dev_ready();self._dev_busy();j=self._jig(a['jig_id']);s=self._sample()
   if s is None or s['kind']!='quartz':raise ActionRejected('QUARTZ_PLATE_REQUIRED')
   return self._dev_request('quartz',dict(kind='daily',jig_id=j['id']))
  if k=='jig.edit':
   self._admin_only()
   if d['val'] and d['val']['state']!='done':raise ActionRejected('BUSY')
   if a['op']=='add':
    if 'name' not in a or 'certified_rad' not in a:raise ActionRejected('INVALID_ARGUMENT')
    if abs(a['certified_rad'])<.1*DEG:raise ActionRejected('QUARTZ_ASSUMPTION_REQUIRED')
    jid=f"jig_{d['next_jig']}";d['next_jig']+=1;d['jigs'].append(dict(id=jid,name=a['name'],certified_rad=a['certified_rad'],uncertainty_rad=a.get('uncertainty_rad',0.)));return {'ok':True}
   j=self._jig(a.get('jig_id'))
   if a['op']=='update':
    if abs(a.get('certified_rad',j['certified_rad']))<.1*DEG:raise ActionRejected('QUARTZ_ASSUMPTION_REQUIRED')
    j.update({q:a[q] for q in ('name','certified_rad','uncertainty_rad') if q in a})
   else:d['jigs'].remove(j);d['val_config']['jig_ids']=[x for x in d['val_config']['jig_ids'] if x!=j['id']]
   return {'ok':True}
  if k=='validation.configure':
   self._admin_only()
   if d['val'] and d['val']['state']!='done':raise ActionRejected('BUSY')
   for jid in a.get('jig_ids',[]):self._jig(jid)
   d['val_config'].update(a);return {'ok':True}
  if k=='validation.start':
   self._dev_ready();self._dev_busy();c=copy.deepcopy(d['val_config']);need=3 if c['method'] in ('usp','ep') else 1
   if len(c['jig_ids'])<need:raise ActionRejected('VALIDATION_SETUP')
   jigs=c['jig_ids'] if c['method'] in ('usp','ep') else c['jig_ids'][:1]
   steps=[dict(kind='blank',jig_id=None,n=c['repeats'] if c['method']=='basic' else 1,values=[])]+[dict(kind='quartz',jig_id=j,n=c['repeats'],values=[]) for j in jigs]
   d['val']=dict(state='prompt',index=0,steps=steps,config=c,temperature_k=self._meas_k(),result=None);return {'ok':True}
  if k=='validation.next':
   if not d['val'] or d['val']['state']!='prompt':raise ActionRejected('PRECONDITION_FAILED')
   return self._val_next()
  if k=='validation.cancel':
   if self._foreign_run():raise ActionRejected('ACCESS_DENIED')
   v=d['val'];q=d['pending'];d['val']=None;d['pending']=None
   if self.phase=='running' and d['task'] and d['task']['kind']=='validation':d['task']=None;self._invoke('acquisition.stop',{})
   return {'ok':True}
  if k=='users.edit':
   u=next((x for x in d['users'] if x['name']==a['name']),None)
   if a['op']=='login':
    if u is None:raise ActionRejected('UNKNOWN_REFERENCE')
    d['user']=u['name'];return {'ok':True}
   self._admin_only()
   if a['op']=='add':
    if u is not None:raise ActionRejected('REQUEST_CONFLICT')
    d['users'].append(dict(name=a['name'],role=a.get('role','operator')));return {'ok':True}
   if u is None:raise ActionRejected('UNKNOWN_REFERENCE')
   if u['name']==d['user'] or (u['role']=='administrator' and sum(x['role']=='administrator' for x in d['users'])<2):raise ActionRejected('IN_USE')
   d['users'].remove(u);return {'ok':True}
  if k=='backup.run':self._admin_only();self._dev_backup('manual');return {'ok':True}
  if k=='backup.configure':self._admin_only();d['auto_backup']=a['auto_at_power_off'];return {'ok':True}
  if k=='diagnosis.run':
   lit=d['started'] and d['na_lamp'] and not d['non_control']
   d['diagnosis']=dict(time_s=self.time_s,items=[dict(item='Light source (Na lamp)',result='OK' if lit else 'Not lit'),dict(item='Temperature sensor (holder)',result='OK',value_text=f'{self.temperature_k-273.15:.2f} C'),
    dict(item='Temperature sensor (cell)',result='OK',value_text=f'{self.cell_k-273.15:.2f} C'),dict(item='Analyzer motor',result='OK'),dict(item='Peltier element',result='OK' if d['temp']['control'] else 'Control off')]);return {'ok':True}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _dev_settings(self):
  d=self.dev;m=self.method
  return copy.deepcopy(dict(started=d['started'],na_lamp=d['na_lamp'],non_control=d['non_control'],analyzer=d['analyzer'],auto_meas=d['auto_meas'],params=d['params'],
   method=dict(configured=bool(m),integration_s=m['integration_s'] if m else DEFAULT_DIT_S,repeats=m['repeats'] if m else DEFAULT_CYCLES),temp=dict(d['temp'],setpoint_k=self.setpoint_k),
   zero_rad=d['zero_rad'],user=d['user'],role=self._role(),auto_backup=d['auto_backup'],validation=d['val_config']))
 def _dev_display(self):
  d=self.dev;p=d['params'];mode=p['data_mode'];a=self._reading_now();v=None if a is None else self.mode_value(a,p)
  q=d['pending'];val=d['val']
  status=('Power-up' if not d['started'] else 'Non-Control Mode' if d['non_control'] else 'Waiting for temperature' if q else
   ('Analyzer moving' if self.stage=='slewing' else 'Measuring') if self.phase=='running' else 'Light source off' if not d['na_lamp'] else 'Ready') if self.powered else 'Off'
  return dict(mode=mode,mode_text=MODES[mode][0],unit=MODES[mode][1],value=v,value_text=self._fmt(v,mode),live=self._live(),status=status,phase=self.phase,
   cycle_text=f"[{len(self.points)}/{self.method['repeats']}]" if self.method and self.phase in ('running','succeeded') else '',temperature_text=f'{self._meas_k()-273.15:.2f}',meas_point=p['meas_point'],
   holder_text=f'{self.temperature_k-273.15:.2f}',cell_text=f'{self.cell_k-273.15:.2f}',set_text='--' if self.setpoint_k is None else f'{self.setpoint_k-273.15:.1f}',blank_text=self._num(math.degrees(p['blank_rad']),4),sample_no=p['sample_no'],
   message=d['message'],task=None if not d['task'] else d['task']['kind'],validation=None if not val else val['state'])
 def _dev_data(self):
  d=self.dev;rows=[]
  for r in d['results']:
   rows.append(dict(no=r['no'],kind=r['kind'],calc='*' if r['selected'] else '',sample=r['sample'] or ('Blank' if r['kind']=='blank' else f"No.{r['sample_no']}" if r['sample_no'] else 'Quartz'),
    mode_text=MODES[r['mode']][0],unit=MODES[r['mode']][1],value=r['value'],value_text=self._fmt(r['value'],r['mode']),sd=r['sd'],temperature_text=f"{r['temperature_k']-273.15:.2f}",selected=r['selected'],n=len(r['values'])))
  vals=[r['value'] for r in d['results'] if r['selected'] and r['value'] is not None];stats=None
  if vals:
   avg=float(np.mean(vals));sd=float(np.std(vals,ddof=1)) if len(vals)>1 else None;rsd=None if sd is None or abs(avg)<1e-15 else 100.*sd/abs(avg)
   stats=dict(n=len(vals),avg=avg,sd=sd,rsd=rsd,text=f'n={len(vals)}  Avg.={avg:.4f}  SD={"-" if sd is None else f"{sd:.4f}"}  RSD={"-" if rsd is None else f"{rsd:.3f}"}%')
  daily=[dict(r,certified_text=self._num(math.degrees(r['certified_rad']),4),measured_text=self._num(math.degrees(r['measured_rad']),4),deviation_text=self._num(math.degrees(r['deviation_rad']),4,'+'),ht_text=f"{r['ht_v']:.0f}",judge='Pass' if r['passed'] else 'Fail') for r in d['daily']]
  v=d['val'];vs=None
  if v:
   st=v['steps'][min(v['index'],len(v['steps'])-1)];what='the blank (solvent) cell' if st['kind']=='blank' else 'quartz plate '+next((j['name'] for j in d['jigs'] if j['id']==st['jig_id']),st['jig_id'])
   vs=dict(state=v['state'],method=v['config']['method'],step=v['index']+1,steps=len(v['steps']),done=len(st['values']),n=st['n'],prompt=f'Set {what}, close the lid and press OK.',result=v['result'])
  return copy.deepcopy(dict(results=rows,stats=stats,files=d['files'],daily=daily,daily_last=daily[-1] if daily else None,validation=vs,validations=d['validations'],last_validation=d['validations'][-1] if d['validations'] else None))
 def _dev_admin(self):
  d=self.dev
  return copy.deepcopy(dict(jigs=[dict(j,certified_text=f"{math.degrees(j['certified_rad']):.4f}",uncertainty_text=f"{math.degrees(j['uncertainty_rad']):.4f}") for j in d['jigs']],
   users=d['users'],backups=d['backups'],diagnosis=d['diagnosis'],status=dict(lamp_hours_text=f"{d['lamp_s']/3600.:.1f} h",measurements=d['measurements'],version='1.00',
   temperature_text=f'{self._meas_k()-273.15:.2f} C',light_source='Na 589 nm',user=d['user'])))
 # ---- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
 def panel_read(self):
  out=self.read('agent');out['host.powered']=self.powered;return out
 def panel_invoke(self,capability_id,arguments):
  """A panel action calls the same command a direct call would (each refusal leaves the state unchanged)."""
  try:return self.invoke(capability_id,**arguments)
  except ActionRejected as exc:raise _panel_ui.PanelRejected(str(exc)) from None
 def panel_time(self):return self.time_s
 def _panel_command(self,k,a):
  try:
   if k=='panel.capture':
    data,w,h=self.panel.png_bytes(a['display_id']);sha=hashlib.sha256(data).hexdigest()
    folder=Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir())/'chemistry_sim_panel'/self.instance_id;folder.mkdir(parents=True,exist_ok=True)
    path=folder/f"{a['display_id']}_{sha[:16]}.png"
    if not path.exists():path.write_bytes(data)
    return dict(display_id=a['display_id'],time_s=self.time_s,image=dict(path=str(path),format='png',width=w,height=h,sha256=sha))
   if k=='panel.frame':return dict(frame=self.panel.frame())
   if ('to_x' in a)!=('to_y' in a):raise ActionRejected('INVALID_ARGUMENT')
   ok={'panel.switch':lambda:self.panel.switch(a['input_id'],a['position']),'panel.press':lambda:self.panel.press(a['input_id'],a.get('duration_s',.1)),
       'panel.touch':lambda:self.panel.touch(a['display_id'],a['x'],a['y'],a.get('duration_s',.1),(a['to_x'],a['to_y']) if 'to_x' in a else None)}[k]()
  except _panel_ui.PanelError:raise ActionRejected('INVALID_ARGUMENT') from None
  self.revision+=1;return dict(accepted=bool(ok))
class JascoP4200Session:
    """Only the trusted host assigns roles; returned data obey the same boundary."""
    def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
    @property
    def _role(self):return self._host._principals[self._principal]
    def _target(self,i):
        if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
    def describe(self,*,instance_id=None,asset_type_id=None):
        if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None:self._target(instance_id)
        if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
        m=export_jasco_p4200_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='unavailable' if v[k] is None else 'estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=h.points[-1]['sample_time_s'] if k.startswith('scan.') and k in ['scan.rotation_rad','scan.raw_rotation_rad'] and h.points else h.time_s,sample_window_s=[h.points[-1]['sample_time_s']-h.points[-1]['integration_s'],h.points[-1]['sample_time_s']] if k in ['scan.rotation_rad','scan.raw_rotation_rad'] and h.points else None) for k in ids})
    def invoke(self,*,instance_id,capability_id,arguments,request_id):
        h=self._host
        try:
            self._target(instance_id)
            if not isinstance(capability_id,str) or capability_id not in ALL_CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
            if self._role=='agent' and ALL_CAPS[capability_id][5]!='agent':raise ActionRejected('ACCESS_DENIED')
            if not isinstance(request_id,str) or not request_id:raise ActionRejected('INVALID_ARGUMENT')
            validate(ALL_CAPS[capability_id][0],arguments);key=(self._principal,instance_id,request_id);digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
            if key in h._requests:
                old,result=h._requests[key]
                if old!=digest:raise ActionRejected('REQUEST_CONFLICT')
                return copy.deepcopy(result)
            before,caller=h.operation_id,h._caller;h._caller=self._principal   # device functions and panel gestures act as this session (owner guards in _foreign_run)
            try:result=h.invoke(capability_id,**arguments)
            finally:h._caller=caller
            op=result.get('operation_id')
            if op:h._owners[op]=self._principal
            elif h.operation_id and h.operation_id!=before:h._owners.setdefault(h.operation_id,self._principal)   # a run started by a panel key / Start / lid close belongs to the caller
            response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=op,status='accepted' if op else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None);h._requests[key]=(digest,copy.deepcopy(response));return response
        except ActionRejected as exc:return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,status='unsupported' if str(exc)=='UNSUPPORTED' else 'rejected',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))
    def write(self,*,instance_id,state_id,value,request_id):
        self._target(instance_id)
        if state_id not in SLOTS or not SLOTS[state_id][5]:raise ActionRejected('ACCESS_DENIED')
        cap,arg=SLOTS[state_id][5];return self.invoke(instance_id=instance_id,capability_id=cap,arguments={arg:value},request_id=request_id)
    def poll(self,operation_id):
        h=self._host
        if h._owners.get(operation_id)!=self._principal:raise ActionRejected('ACCESS_DENIED')
        if operation_id in h._operation_records:return copy.deepcopy(h._operation_records[operation_id])
        if operation_id!=h.operation_id:raise ActionRejected('ACCESS_DENIED')
        return h._operation_snapshot()
    def cancel(self,operation_id,request_id):
        snapshot=self.poll(operation_id);h=self._host
        if not isinstance(request_id,str) or not request_id:raise ActionRejected('INVALID_ARGUMENT')
        key=(self._principal,h.instance_id,request_id);digest=json.dumps(['acquisition.cancel',operation_id])
        if key in h._requests:
            old,response=h._requests[key]
            if old!=digest:raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(response)
        result=h.invoke('acquisition.stop') if snapshot['status']=='running' else {'phase':snapshot['status']}
        response=dict(protocol_version=PROTOCOL_VERSION,instance_id=h.instance_id,request_id=request_id,operation_id=operation_id,status='succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None)
        h._requests[key]=(digest,copy.deepcopy(response));return response


def export_jasco_p4200_contract_metadata():
 caps={};slots={}
 cleared=['scan.phase','scan.stage','scan.elapsed_s','scan.completed_points','scan.rotation_rad','scan.raw_rotation_rad','safety.warning']
 invalidated=['blank.ready','calibration.ready','calibration.gain',*cleared]
 interrupted=['scan.phase','scan.stage','lamp.enabled','safety.warning']
 immediate={
  'plant.configure':['plant.configured','thermal.setpoint_k','thermal.temperature_k'],
  'sample.register':['truth.ledger'],
  'sample.load':['sample.loaded','blank.ready','truth.ledger',*cleared],
  'sample.unload':['sample.loaded','blank.ready','truth.ledger'],
  'method.configure':['method.configured','method.wavelength_m','method.integration_s',*invalidated],
  'power.set':['power.enabled','blank.ready','calibration.ready','calibration.gain',*interrupted],
  'lid.set':['lid.opened','lid.angle_rad',*interrupted],
  'lamp.set':interrupted,
  'thermal.set_temperature':['thermal.setpoint_k',*invalidated],
  'acquisition.start':invalidated,
  'acquisition.stop':interrupted,
  'calibration.quartz':['calibration.ready','calibration.gain'],
 }
 on_step={
  'power.set':['thermal.temperature_k','truth.ledger'],
  'thermal.set_temperature':['thermal.temperature_k','truth.ledger'],
  'acquisition.start':['scan.phase','scan.stage','scan.elapsed_s','scan.completed_points','scan.rotation_rad','scan.raw_rotation_rad','optics.analyzer_rad','safety.warning','lamp.enabled'],
 }
 on_completion={'acquisition.start':['scan.phase','scan.stage','blank.ready','lamp.enabled']}
 # 1.1.0 front panel: what the existing commands now also change on the device slots (screen readout, settings, data, status)
 DS=['device.settings','device.display']
 for key,extra in {'plant.configure':DS+['device.admin'],'sample.load':['device.display'],'sample.unload':['device.display'],'method.configure':DS,'power.set':DS+['device.data','device.admin'],
                   'lid.set':DS+['device.data'],'lamp.set':['device.display'],'thermal.set_temperature':DS,'acquisition.start':['device.display'],'acquisition.stop':['device.display','device.data'],
                   'calibration.quartz':['device.display']}.items():immediate[key]=immediate[key]+extra
 meas_steps=['scan.phase','scan.stage','scan.elapsed_s','scan.completed_points','scan.rotation_rad','scan.raw_rotation_rad','optics.analyzer_rad','safety.warning','lamp.enabled','blank.ready','device.display','device.data','device.settings']
 for key,extra in {'power.set':DS+['device.admin'],'thermal.set_temperature':['device.display','device.admin'],'acquisition.start':['device.display','device.data','device.settings'],'lid.set':meas_steps}.items():on_step[key]=on_step.get(key,[])+[x for x in extra if x not in on_step.get(key,[])]
 on_completion['acquisition.start']=on_completion['acquisition.start']+['device.data','device.settings','device.display']
 observed={
  'plant.configure':['plant.configured'],'method.configure':['method.configured'],
  'sample.load':['sample.loaded'],'sample.unload':['sample.loaded'],
  'power.set':['power.enabled'],'lid.set':['lid.opened'],'lamp.set':['lamp.enabled'],
  'thermal.set_temperature':['thermal.setpoint_k'],'acquisition.start':['scan.phase'],'acquisition.stop':['scan.phase'],
  'calibration.quartz':['calibration.ready','calibration.gain','scan.raw_rotation_rad'],
  'process.measure':['scan.phase','scan.rotation_rad','scan.raw_rotation_rad','scan.elapsed_s','scan.completed_points','safety.warning'],
  'result.read':['scan.phase','scan.rotation_rad','scan.raw_rotation_rad','scan.elapsed_s','scan.completed_points','method.wavelength_m','method.integration_s','safety.warning'],
  'product.read':['truth.ledger'],
 }
 def effects_for(key):
  descriptions={'immediate':'本次通过校验的控制/配置改变或清除该状态；不推进仿真时间','on_step':'后续宿主step推进温控、分析器就位或积分；设定返回不表示过程完成','on_completion':'全部积分完成后设置终态并关闭灯门控；仅blank完整采集提交有效空白'}
  return [dict(state_id=state,timing=timing,description_zh=descriptions[timing]) for timing,mapping in [('immediate',immediate),('on_step',on_step),('on_completion',on_completion)] for state in mapping.get(key,[])]
 for k,(s,u,o,v,c,w) in SLOTS.items():
  measured=k in ['scan.rotation_rad','scan.raw_rotation_rad']
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'JascoP4200Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='interval_mean' if measured else 'instant',time_domain='simulation',window_description='分析器就位后的[t_point-integration,t_point]光度积分相位估计，不是整个step窗口' if measured else None),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNSUPPORTED','PRECONDITION_FAILED','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','INITIALIZATION_ONLY','SAMPLE_ACCESS_REQUIRED','SAMPLE_METHOD_REQUIRED','EVIDENCE_ACK_REQUIRED','SOURCE_SOLVENT_MISMATCH','SOURCE_TEMPERATURE_MISMATCH','SOURCE_CONCENTRATION_MISMATCH','QUARTZ_ASSUMPTION_REQUIRED','CELL_CONFIGURATION','OPTICS_NOT_READY','BLANK_REQUIRED','REFERENCE_TEMPERATURE_MISMATCH','SAMPLE_KIND_MISMATCH','BUSY','ANALYSIS_INPUT','REFERENCE_CONDITIONS_MISMATCH','QUARTZ_CALIBRATION_REQUIRED','INVALID_CALIBRATION','ANGLE_BRANCH_RANGE','LOW_POLARIZED_SIGNAL']
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  asynchronous=k=='acquisition.start';query=kind in ['query','analysis'];unsupported=k in UNSUPPORTED
  caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.jasco_p4200.JascoP4200Session.invoke',invocation=f'session.invoke(instance_id="p4200_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if query or unsupported else [dict(description_zh='受检宿主配置、供电关盖、D线/来源条件、完整匹配空白和石英观测校准；每能力仅检查适用前提，详见Runtime._invoke',state_ids=['power.enabled','lid.opened','lamp.enabled','blank.ready','calibration.ready','scan.phase'],enforced_by='runtime',check='atomic Runtime.invoke and trusted-principal Session')],
  state_effects=[] if query or unsupported else effects_for(k),
  observes=observed.get(k,[]),model_effects=dict(mode='none',description_zh='不支持无变化' if unsupported else '待独立场景验证',binding_ids=[]),
  execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',completion_criterion='分析器就位后全部积分完成并关闭逻辑光路门控' if asynchronous else '事务返回；只读/分析不推进时间',completion_state_ids=['scan.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',retry='same_request_id',resources=['host_clock','sample_holder','request_cache'],partial_failure_zh='开盖/断电/熄灯/取消/温度越界或噪声角度分支跳变保留部分点与姿态/热量；不完整空白/石英不接受校准。终态poll快照受原身份保护，后续采集后仍可查询；取消历史终态不停止新采集；历史与去重仅进程内'),
  errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=['文献参数驱动理想Malus/Stokes/分析器代理，非原厂检测机构或实机标定；ORD/绝对光学纯度不支持'],source_ids=['manufacturer_primary','simulation_assumption'])
 # device functions and panel gestures (front panel, 1.1.0)
 dev_errors=errors+['ACCESS_RIGHTS','LIGHT_SOURCE_WINDOW','NON_CONTROL_MODE','LIGHT_SOURCE_OFF','QUARTZ_PLATE_REQUIRED','VALIDATION_SETUP','FILE_EXISTS','IN_USE']
 meas=['method.configured','method.wavelength_m','method.integration_s','lamp.enabled',*invalidated]
 dev_fx={'device.light_source':(DS+interrupted,[]),'device.control':(DS+meas,['optics.analyzer_rad','device.display']),'device.parameters':(DS,[]),'device.zero_clear':(DS,[]),
  'measure.start':(DS+meas,meas_steps),'measure.stop':(DS+interrupted+['device.data'],[]),'thermal.condition':(DS,['thermal.temperature_k','truth.ledger']),
  'analysis.edit':(['device.data'],[]),'data.save':(['device.data'],[]),'daily_check.start':(DS+meas,meas_steps),'jig.edit':(['device.admin','device.settings','device.data'],[]),
  'validation.configure':(['device.settings'],[]),'validation.start':(['device.data','device.display'],[]),'validation.next':(DS+meas+['device.data'],meas_steps),
  'validation.cancel':(['device.data','device.display',*interrupted],[]),'users.edit':(['device.settings','device.admin','device.display'],[]),'backup.run':(['device.admin'],[]),
  'backup.configure':(['device.settings'],[]),'diagnosis.run':(['device.admin'],[])}
 def fx(imm,later):
  d={'immediate':'面板设备功能：本次调用立即改变；拒绝时不变','on_step':'后续宿主step推进（等待温度开始条件、采集、实时检偏器、10 s光源窗口）'}
  return [dict(state_id=x,timing='immediate',description_zh=d['immediate']) for x in dict.fromkeys(imm)]+[dict(state_id=x,timing='on_step',description_zh=d['on_step']) for x in dict.fromkeys(later)]
 def entry(k,inp,out,c,z,kind,vis,ex,imm,later,obs,pre):
  return dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
   implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
   binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.jasco_p4200.JascoP4200Session.invoke',invocation=f'session.invoke(instance_id="p4200_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),
   preconditions=pre,state_effects=fx(imm,later),observes=obs,model_effects=dict(mode='none',description_zh='无几何变化；屏幕画面由panel.capture提供（3D屏幕纹理属后续步骤）',binding_ids=[]),
   execution=dict(mode='synchronous',time_domain='simulation',completion_criterion='事务返回；手势与设备功能不推进仿真时钟，测量、等待温度与10 s光源窗口由宿主step推进',completion_state_ids=[],timeout_s=None,cancellation='unsupported',retry='same_request_id',resources=['host_clock','sample_holder','request_cache'],partial_failure_zh='设备功能原子：拒绝时状态不变；一个面板手势按屏幕规则可调用多个命令，已被接受的前序命令保留'),
   errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in dev_errors],example_arguments=copy.deepcopy(ex),verification=[],
   limitations=['P-4000未存操作手册：画面与功能按产品页/应用说明，字段、范围与公式按P-2000 iRM前代手册（panel_ui.json的assumptions）'],source_ids=['p2000_manual','manufacturer_primary','simulation_assumption'])
 pre=[dict(description_zh='供电；开始测量另需光源窗口已应用、非Non-Control、Na灯开；管理/验证设置需管理员权限；测量同原命令前提（关盖、方法、空白、石英校正、温度）',state_ids=['power.enabled','device.settings','lid.opened','blank.ready','calibration.ready','scan.phase'],enforced_by='runtime',check='JascoP4200Runtime._device (atomic Runtime.invoke)')]
 for k,(inp,out,c,z,kind,vis,ex,_) in DEVICE_CAPS.items():
  imm,later=dev_fx[k];caps[k]=entry(k,inp,out,c,z,kind,vis,ex,imm,later,['device.settings','device.display','device.data','device.admin'],pre)
 called=_panel_ui.spec_capabilities(PANEL_SPEC) if PANEL_SPEC else []
 g_imm=[e['state_id'] for k in called for e in caps[k]['state_effects'] if e['timing']=='immediate'];g_later=[e['state_id'] for k in called for e in caps[k]['state_effects'] if e['timing']!='immediate']
 for k,(inp,out,c,z,kind,vis,ex,_) in PANEL_CAPS.items():
  gesture=k not in ('panel.capture','panel.frame')
  caps[k]=entry(k,inp,out,c,z,kind,vis,ex,g_imm if gesture else [],g_later if gesture else [],[] if gesture else ['device.display'],
   [dict(description_zh='按panel_ui.json的屏幕规则：关机时只有电源开关有效；被调用命令各自检查前提',state_ids=['power.enabled'],enforced_by='runtime',check='JascoP4200Runtime._panel_command（面板引擎_panel_ui.py）')] if gesture else [])
 mech=dict(component_ids=['sodium_lamp','polarizer','quartz_cell','analyzer','detector','peltier','sample_holder','host_screen'],state_ids=list(slots),meaning_zh='67种真实参考比旋光度、显式传感增益/零偏/噪声、四相光度相位、有限速率与积分、标准温控和观测校准',implementation='lumped',
 equations=['alpha_SI [rad*m^2/kg]=alpha_source [deg*mL/(g*dm)]*pi/18000; theta_true=alpha_SI*c_kg_m3*l_m',
 'Reference constraints: sodium D (nominal589nm vs source589.3nm), exact source solvent and reported concentration; missing concentration host-acknowledged; T_source +/-0.2K only',
 'theta_sensor=host_gain*theta_true+host_zero; I(psi)=transmittance*cos(psi-theta_sensor)^2+dark; psi=0,pi/4,pi/2,3pi/4',
 'theta_observed=0.5*atan2(I45-I135,I0-I90); reject abs(theta_true) or abs(theta_sensor)>=pi/2-1e-8; endpoint branch ambiguity explicitly not supported; noisy demodulated phase differing from settled analyzer by >=pi/2 fails ANGLE_BRANCH_RANGE',
 'Analyzer teaching joint slews at6deg/s to noiseless photometric phase before integrating; no claim this is OEM servo/optical train',
 'Each channel receives Normal(0,host_noise_density/sqrt(integration_s)); 1..100s integration, 1..100 repeats host ceiling',
 'Blank zero=mean(raw_blank_angles); gain_est=(mean(raw_quartz_angles)-zero)/caller_certified_angle',
 'Device rotation (one model for screen, data list, scan.rotation_rad, result.read and poll; P-2000 p.82): alpha=(raw/gain_est)/(1+k*(t-293.15K))-blank_value/gain_est-zero_clear; blank_value=blank zero unless edited, subtracted only when Subtract Blank; k=temperature correction factor (Brix purity -0.00037); t=Meas. Point sensor; zero_clear=shown rotation at Zero Clear; defaults k=0, zero_clear=0 give (raw-zero)/gain_est',
 'Detector sigma estimate=sqrt(2)*noise_sigma/(2*transmittance); excludes blank/quartz/reference uncertainties, not total uncertainty',
 'Specific rotation=mean(caller_observed_angles)/(declared_c*declared_path); concentration=caller_angle/(caller_reference_alpha*declared_path); no hidden truth',
 'Standard Peltier C*dT/dt=clip(K*(set-T)+UA*(set-Tambient),-Pmax,+Pmax)-UA*(T-Tambient); piecewise exact affine integration',
 'In-cell sensor dTcell/dt=(T-Tcell)/60s (engineering lag, exact per piece); Meas. Point Cell: holder target=clip(set+(set-Tcell),set-5K,set+5K) re-computed every 0.5s (cascade, zero-order hold), Holder: target=set; the selected sensor gives the shown temperature, t of the temperature correction and the start conditions',
 'C*deltaT=controller_energy-ambient_loss; holder/sample isothermal lumped proxy, not calibrated temperature accuracy or full electrical energy',
 'step sole simulation clock; whole prefilled cell volume/solute conserved; no mutation/reaction/evaporation; quartz logical standard explicit synthetic'],
 parameters={},source_ids=['manufacturer_primary','manufacturer_spec','manufacturer_us','manufacturer_accessories','pubchem_primary','pubchem_sucrose','simulation_assumption'],
 limitations=['Factory550W350D210Hmm/24.5kg; standard15–40°C holder control +/-0.2°C and monitoring +/-0.1°C are specifications, not validated model errors.',
 '67 independently reviewed stereochemical identities with measured reference specific rotations; no measured raw optical signals or P4200 traces.',
 'No ORD/extrapolation, concentration dependence calibration, mutarotation/pH/mixtures or unconditional ee; invalid source derivative identities excluded.',
 'Na589nm only; other lamps/wavelengths/automatic changer absent; P4200 must not inherit P4300 standard configuration.',
 'Malus/Stokes signal and analyzer finite rate are explicit idealized model, not manufacturer demodulation or servo design; unresolved +/-90deg branch endpoints and noisy principal-angle wraps relative to settled analyzer pose are rejected with partial data retained.',
 'Gain/offset/noise/transmittance/thermal parameters are host assumptions; source display increment/accuracy/repeatability are not noise distributions.',
 'Quartz standard is host-synthetic with caller-declared angle, no ISO17025 certificate; calibration tested algorithmically only.',
 'Known reference concentration matched; missing concentration acknowledged; traditional percent->g/100mL convention retained with original source wording.',
 'Prefilled-cell logical attachment, fixed geometry no fill/robot/contact; 2mL logical ceiling is host assumption, not manufacturer capacity.',
 'Temperature holder/sample lumped; the in-cell sensor is a first-order lag (60 s, engineering value) of that lumped temperature, not a validated probe delay or heat transfer geometry; no long-term drift or failure probabilities.',
 'Six simplified screen readouts are simulation_only, no full native GUI/CFR/ALCOA+/LIMS/hardware driver.',
 'Session access and in-process request dedup are not OS file isolation or restart idempotency; no full native Matterix async envelope or hardware/HIL.'])
 spec=importlib.util.spec_from_file_location('_p4200_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
 ann.annotate_export(SLUG,caps,slots,{'polarimetry':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.register':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'，非实机标定。'
 mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'polarimetry':mech})

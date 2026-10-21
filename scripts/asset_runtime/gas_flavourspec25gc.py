"""CHEM-100 GC-IMS: finite gas/headspace transfer, PAL axis motion and literature peak descriptors.
Trusted Python host only. Sessions filter identity/truth; process/file isolation is external.
Touch-screen firmware 4.80 (Defaults, Programs, recording, files, modes, system settings, snapshot, errors, log) and the front panel
(panel.touch / turn / press / switch / capture / frame; panel_ui.json beside the contract, run by the generic engine _panel_ui.py).
"""
from pathlib import Path
import copy,math,json,importlib.util
PROTOCOL_VERSION='1.0.0';SLUG='gas_flavourspec25gc';CATALOG_ID='CHEM-100';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];TEMPLATE_REL='assets/expansion_batch04/t3_analytical/'+SLUG+'/ims_library.json'
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
  if not isinstance(v,dict) or not set(s.get('required',[]))<=v.keys() or (s.get('additionalProperties',True) is False and set(v)-set(s.get('properties',{}))):raise ActionRejected('INVALID_ARGUMENT')
  for k,x in v.items():
   if k in s.get('properties',{}):validate(s['properties'][k],x)
 elif t=='array':
  if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
  for x in v:validate(s['items'],x)
 if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')
import numpy as np
R=8.31446261815324
PLANT=obj({'ambient_k':number('K',278.15,308.15),'gas_supply_mol':number('mol',1e-9,1e4),'gas_reference_k':number('K',200,400),'gas_reference_pa':number('Pa',1e4,1e6),'thermal_tau_s':number('s',.01,1e5),'travel_s':number('s',.1,120),'stroke_s':number('s',.1,120),'gain_s_per_mol':number('s/mol',1e-9,1e15),'gc_sigma_s':number('s',.1,100),'drift_sigma_ratio':number('1',.001,.2),'accept_synthetic_response':BOOL})
PLANT_EXAMPLE=dict(ambient_k=298.15,gas_supply_mol=10.,gas_reference_k=293.15,gas_reference_pa=101325.,thermal_tau_s=2.,travel_s=1.,stroke_s=1.,gain_s_per_mol=1e9,gc_sigma_s=2.,drift_sigma_ratio=.015,accept_synthetic_response=True)
METHOD=obj({'gc_k':number('K',278.15,373.15),'ims_k':number('K',278.15,373.15),'injector_k':number('K',278.15,473.15),'incubator_k':number('K',278.15,473.15),'incubation_s':number('s',0,7200),'injection_m3':number('m^3',1e-9,1e-6),'column_length_m':number('m',.1,60),'gas':enum(['nitrogen','synthetic_air']),'carrier_scale':number('1',.01,10),'drift_flow_m3_s':number('m^3/s',1e-9,1e-4),'retention_scale':number('1',.05,20),'drift_ratio_scale':number('1',.1,10),'run_s':number('s',1,7200),'accept_method_transfer':BOOL})
METHOD_EXAMPLE=dict(gc_k=333.15,ims_k=318.15,injector_k=358.15,incubator_k=353.15,incubation_s=10.,injection_m3=500e-9,column_length_m=15.,gas='nitrogen',carrier_scale=1.,drift_flow_m3_s=150e-6/60,retention_scale=1.,drift_ratio_scale=1.,run_s=180.,accept_method_transfer=True)
SOLUTE=obj({'substance_id':TEXT,'mole_fraction':number('1',0,1)})
SAMPLE=obj({'sample_id':TEXT,'vial_index':dict(number('1',0,59),type='integer'),'vial_m3':enum(['10mL','20mL']),'headspace_m3':number('m^3',1e-9,20e-6),'pressure_pa':number('Pa',1e4,3e5),'temperature_k':number('K',278.15,473.15),'solutes':dict(arr(SOLUTE),minItems=1,maxItems=77)})
SAMPLE_EXAMPLE=dict(sample_id='headspace_01',vial_index=0,vial_m3='20mL',headspace_m3=15e-6,pressure_pa=101325.,temperature_k=353.15,solutes=[dict(substance_id='cas_64_17_5',mole_fraction=1e-5),dict(substance_id='cas_67_64_1',mole_fraction=2e-5)])
DRIFT_AXIS=np.linspace(.6,2.5,96).tolist();SIGNAL_ARRAY=arr(number('1',0),96)
POINT=obj({'time_s':number('s',0),'window_s':number('s',0),'signal':SIGNAL_ARRAY})
RESULT=obj({'phase':PHASE,'workflow':TEXT,'complete':BOOL,'points':arr(POINT),'drift_ratio_axis':arr(number('1'),96),'elapsed_s':number('s',0),'template_source':TEXT,'evidence':TEXT,'warnings':arr(TEXT)})
ROI=obj({'start_s':number('s',0),'stop_s':number('s',0),'drift_min_ratio':number('1',.6,2.5),'drift_max_ratio':number('1',.6,2.5)})
ROI_EXAMPLE=dict(start_s=0.,stop_s=180.,drift_min_ratio=.6,drift_max_ratio=2.5)
CAL_POINT=obj({'concentration_mol_m3':number('mol/m^3',0),'area_s':number('s',0)})
CAL=obj({'roi':ROI,'standards':dict(arr(CAL_POINT),minItems=2,maxItems=100),'label':TEXT})
CAL_EXAMPLE=dict(roi=ROI_EXAMPLE,standards=[dict(concentration_mol_m3=0.,area_s=0.),dict(concentration_mol_m3=.1,area_s=50.)],label='external example calibration; synthetic')
LEDGER=obj({'gas_remaining_mol':number('mol',0),'gas_consumed_mol':number('mol',0),'sample_remaining_mol':number('mol',0),'syringe_mol':number('mol',0),'column_mol':number('mol',0),'exhaust_mol':number('mol',0),'total_residual_mol':number('mol'),'analyte_residual_mol':number('mol'),'sample_id':TEXT})
CAPS={};UNSUPPORTED={'hardware.connect','ion_chemistry.solve'}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},effects or [])
cap('plant.configure',PLANT,obj({'configured':BOOL}),['housing','ims_tube'],'初始化有限气源和热/运动/峰展宽/离子信号假设','configuration','evaluator',PLANT_EXAMPLE,['plant.configured','device.parameters','snapshot.state'])
cap('method.configure',METHOD,obj({'configured':BOOL}),['gc_oven','ims_tube','incubator','syringe_body'],'设GC/IMS≤100C、针≤200C、≤60m柱及显式方法迁移','configuration','agent',METHOD_EXAMPLE,['method.configured','gc.target_k','ims.target_k','injector.target_k','incubator.target_k','calibration.ready','device.defaults','device.parameters','snapshot.state'])
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['vial_rack'],'宿主放置10或20mL瓶，给有限已平衡顶空气体组成','configuration','evaluator',SAMPLE_EXAMPLE,['sample.loaded','sample.identity','sample.vial_index'])
cap('sample.unload',obj({}),obj({'returned_mol':number('mol',0)}),['vial_rack'],'只归还原瓶内余气，不回滚注射器与柱内物质','device_command','evaluator',effects=['sample.loaded','sample.identity'])
for k,part,z in [('power.set','power_inlet','逻辑供电；断电取消并停止供气目标'),('gas.set','gas_inlet','有限气源启停目标；step唯一消费气体')]:cap(k,obj({'enabled':BOOL}),obj({'enabled':BOOL}),[part],z,ex={'enabled':True},effects=['power.enabled','gas.enabled','acquisition.phase','device.mode','recording.state','files.stored','device.errors','device.log','device.parameters','snapshot.state'] if k=='power.set' else ['gas.enabled','device.parameters','snapshot.state'])
cap('cover.set',obj({'angle_rad':number('rad',0,1.45)}),obj({'angle_rad':number('rad',0,1.45)}),['service_cover'],'主机检修盖工程关节；忙碌/供气时拒绝开启',ex={'angle_rad':0.},effects=['cover.angle_rad'])
AXES=obj({'x_m':number('m',-.47,.40),'y_m':number('m',0,.39),'z_m':number('m',0,.18)})
cap('gantry.move',AXES,AXES,['gantry_x','gantry_y','syringe_z'],'空闲宿主设PAL工程轴位置；非机器人接触操纵',ex={'x_m':0.,'y_m':0.,'z_m':0.},effects=['gantry.x_m','gantry.y_m','gantry.z_m'])
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['incubator','syringe_body','gc_oven','ims_tube'],'孵育、PAL移动、有限吸样/注入、二维采集；异步且可取消',effects=['acquisition.phase','workflow.phase','acquisition.elapsed_s','recording.state','sample_names.state','device.log','device.parameters','snapshot.state'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['syringe_body','gc_oven','ims_tube'],'停止程序和供气目标，原瓶/针筒/柱/排气保留各自物质',effects=['acquisition.phase','gas.enabled','recording.state','files.stored','device.log','device.errors','device.parameters','snapshot.state'])
cap('syringe.purge',obj({}),obj({'purged_mol':number('mol',0)}),['syringe_body','exhaust_port'],'将残留针筒气体明确排到排气账本','device_command','evaluator')
cap('process.measure',obj({}),RESULT,['ims_tube','display_digits'],'读已采集二维窗口及无量纲RIP漂移轴','query')
cap('signal.integrate',ROI,obj({'area_s':number('s',0)}),['ims_tube'],'从观测二维窗口积分选区；不读取物种标签','analysis','agent',ROI_EXAMPLE)
cap('calibration.fit',CAL,obj({'slope_s_m3_mol':number('s*m^3/mol',0),'intercept_s':number('s'),'r_squared':number('1',0,1)}),['ims_tube'],'用外部标准浓度/观测积分拟合标线，限定当前方法与ROI','analysis','agent',CAL_EXAMPLE,['calibration.ready'])
cap('concentration.analyze',obj({}),obj({'concentration_mol_m3':nullable(number('mol/m^3',0)),'label':TEXT,'quality':TEXT,'evidence':TEXT}),['ims_tube'],'观测ROI积分与标线浓度；完成/范围检查，绝不读取隐藏样品真值','analysis')
cap('fingerprint.compare',obj({'reference':arr(number('1',0),1920)}),obj({'cosine_similarity':nullable(number('1',0,1)),'features':arr(number('1',0),1920),'evidence':TEXT}),['ims_tube'],'20保留时间格×96漂移格的观测指纹与外部参考比较','analysis',ex={'reference':[0.]*1920})
cap('template.read',obj({'substance_id':TEXT}),obj({'substance_id':TEXT,'retention_times_s':arr(number('s',0)),'drift_ratios':arr(number('1',0)),'template_source':TEXT,'evidence':TEXT}),['ims_tube'],'公开实验峰位置模板；源方法与25型号迁移未验证','query',ex={'substance_id':'cas_64_17_5'})
cap('ledger.read',obj({}),LEDGER,['vial_rack','syringe_body','gc_oven','gas_inlet','exhaust_port'],'私有有限摩尔量转移与守恒账本','query','evaluator')
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['housing'],'真实硬件或完整离子簇反应未实现')
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='display_digits',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','宿主气源与条件模型已配置','setpoint',c='housing');slot('method.configured',BOOL,'1','有效方法已配置','setpoint')
for sid,capid,comp in [('power.enabled','power.set','power_inlet'),('gas.enabled','gas.set','gas_inlet')]:slot(sid,BOOL,'1','运行目标；物理仅宿主step推进','setpoint',c=comp,w=(capid,'enabled'))
for name,comp,hi in [('gc','gc_oven',373.15),('ims','ims_tube',373.15),('injector','syringe_body',473.15),('incubator','incubator',473.15)]:
 slot(name+'.target_k',nullable(number('K',278.15,hi)),'K','模块温度目标；孵育温度上界沿用宿主工程限制，非独立PAL额定证据','setpoint',c=comp)
 slot(name+'.temperature_k',nullable(number('K')),'K','单节点实际温度真值','process_truth','evaluator',comp)
 slot(name+'.indicated_k',nullable(number('K')),'K','理想化温度观测，无实测噪声')
slot('cover.angle_rad',number('rad',0,1.45),'rad','服务盖绕+X开合角；零闭合','scene',c='service_cover',w=('cover.set','angle_rad'))
for name,lo,hi,comp in [('x',-.47,.40,'gantry_x'),('y',0,.39,'gantry_y'),('z',0,.18,'syringe_z')]:slot('gantry.'+name+'_m',number('m',lo,hi),'m','PAL工程轴q；X沿+X/Y沿-Y/Z沿-Z；任务中step按阶段移动','scene',c=comp)
slot('sample.loaded',BOOL,'1','有限顶空瓶已放置',c='vial_rack');slot('sample.identity',TEXT,'1','瓶身份真值','process_truth','evaluator','vial_rack');slot('sample.vial_index',nullable(dict(number('1',0,59),type='integer')),'1','60位瓶架公开位置',c='vial_rack')
slot('acquisition.phase',PHASE,'1','采集任务阶段');slot('workflow.phase',TEXT,'1','孵育/轴运动/吸样/注入/分离子阶段');slot('acquisition.elapsed_s',number('s',0),'s','进样后的GC采集时间')
slot('detector.total_signal',nullable(number('1',0)),'1','最近GC时间窗口内96个IMS漂移通道的信号和；响应增益显式假设')
slot('gas.remaining_mol',number('mol',0),'mol','有限载气和漂移气共享供给的剩余摩尔量','process_truth','evaluator','gas_inlet');slot('calibration.ready',BOOL,'1','当前方法和ROI存在外部标线')

# ---------------------------------------------------------------- touch-screen firmware 4.80 (manual section 6): Defaults window, measurement programs,
# recording, measurement files, modes, system settings, snapshot, error information and log.  The front panel (panel_ui.json, panel.* commands) and the
# API call the same commands below.  Numbers from the manual: defaults table p.240, program table p.241, technical data p.232-233.
import datetime,hashlib,re,tempfile,weakref
ASSET_VERSION='1.3.0';FW_VERSION='4.80';DEVICE_TYPE='FlavourSpec 25';DEVICE_SERIAL='SIM-0001'
EPOCH_S=1767225600.                     # device clock at host time 0: 2026-01-01 00:00:00 (assumption; System > Settings > Date/Time moves it)
STORAGE_B=32e9                          # internal flash memory 32 GB (brochure)
RAW_HZ=1/.021;SPECTRUM_B=150e3*.021*2   # trigger repetition 21 ms, 150 kHz x 14 bit (stored as 2 bytes, assumption) per raw spectrum (manual p.232)
RESOLUTION=45.                          # IMS resolution ~45 (manual p.232)
K0_RIP={'positive':2.06e-4,'negative':2.29e-4}   # reduced mobility of the reaction ion peak, m^2/(V s) (literature values, assumption)
RIP_HEIGHT_V={'positive':1.2,'negative':.8}      # RIP height at 100 us trigger duration (assumption)
GAS_VISCOSITY={'air':1.81e-5,'nitrogen':1.76e-5,'helium':1.96e-5,'argon':2.23e-5,'oxygen':2.04e-5,'carbon_dioxide':1.47e-5}  # Pa s at 293.15 K
TIMEZONES={'UTC':0,'Europe/Berlin':1,'Europe/London':0,'America/New_York':-5,'Asia/Shanghai':8,'Asia/Tokyo':9,'Australia/Sydney':10}
ML=1e-6/60;MAX_PROGRAMS=100;PROGRAM_MAX_S=3599.98;MAX_LOG=200
FS_DEFAULTS=dict(t1_c=45.,t2_c=40.,t3_c=80.,t4_c=80.,t5_c=45.,epc_ims_ml_min=75.,epc_gc_ml_min=5.,polarity='positive',averaging=6,trigger_us=100)
T_NODE={'t1_c':'ims','t2_c':'gc','t3_c':'injector'}
def integer(lo,hi):return {'type':'integer','x-unit':'1','minimum':lo,'maximum':hi}
def oopt(p,req=()):return dict(type='object',properties=p,required=list(req),additionalProperties=False)
FREE={'type':'object'};NAME=TEXT;IP=TEXT
GASES=['air','nitrogen','helium','argon','oxygen','carbon_dioxide']
FS_SETTINGS={  # key: (schema, factory value); manual p.65-69, p.75-79, p.176
 'network.ip':(IP,'192.168.100.52'),'view.mode':(enum(['normal','simplified']),'normal'),'view.append_id':(BOOL,False),'view.id':(NAME,''),
 'display.brightness':(integer(1,5),5),'display.screensaver_min':(nullable(integer(1,120)),60),'fan.start_c':(number('degC',20,70),35.),'fan.max_c':(number('degC',20,80),50.),
 'overheat.threshold_c':(number('degC',30,90),55.),'after_seq':(enum(['no_action','cleaning','standby','custom']),'no_action'),
 'contamination.threshold_mv':(number('mV',1,10000),200.),'contamination.search_from':(number('1',.6,2.5),1.085),
 'column.name':(NAME,'MXT-5'),'column.length_m':(number('m',.05,100),15.),'column.id_mm':(number('mm',.01,10),.53),'column.film_um':(number('um',.01,8),1.),
 'column.polarity':(enum(['polar','non-polar','other']),'non-polar'),'column.comment':(NAME,''),
 'drift.tube_mm':(number('mm',10,200),53.),'drift.voltage_v':(number('V',100,5000),2700.),'drift.gas':(enum(['nitrogen','air','other']),'nitrogen'),
 'septum.max_injections':(integer(1,500),100),'alarm.frequency_hz':(integer(100,5000),440),'epc.ims_gas':(enum(GASES),'nitrogen'),'epc.gc_gas':(enum(GASES),'nitrogen'),
 'export.auto':(BOOL,False),'export.mea':(BOOL,True),'export.denoised':(BOOL,False),'export.zip':(BOOL,False),
 'modes.cleaning_h':(nullable(integer(1,96)),15),'modes.standby_h':(nullable(integer(1,96)),15),
 'server.protocol':(enum(['smb','sftp','tftp']),'smb'),'server.user':(NAME,''),'server.ip':(IP,''),'server.share':(NAME,''),
 **{f'snapshot.{k}_{e}':(number('%',1,500),v) for k,lo,hi in [('epc_gc_pressure',95.,110.),('rip_height',80.,150.),('rip_position',95.,105.),('fwhm',80.,120.)] for e,v in (('lo',lo),('hi',hi))}}
SNAP_KEYS=['epc_gc_pressure','rip_height','rip_position','fwhm']
COLUMN_PRESETS=[dict(name='MXT-5',length_m=15.,id_mm=.53,film_um=1.,polarity='non-polar'),dict(name='MXT-5',length_m=30.,id_mm=.53,film_um=1.,polarity='non-polar'),
                dict(name='MXT-WAX',length_m=30.,id_mm=.53,film_um=1.,polarity='polar'),dict(name='MXT-200',length_m=15.,id_mm=.53,film_um=1.,polarity='other')]
_DEF_IN=oopt({'t1_c':nullable(number('degC',5,100)),'t2_c':nullable(number('degC',5,100)),'t3_c':nullable(number('degC',5,200)),'t4_c':nullable(number('degC',5,100)),
              't5_c':nullable(number('degC',5,100)),'epc_ims_ml_min':nullable(number('mL/min',1,500)),'epc_gc_ml_min':nullable(number('mL/min',1,150)),
              'polarity':enum(['positive','negative']),'averaging':integer(0,99),'trigger_us':integer(10,2000)})
_SCREEN=['display_digits','rotary_knob']
FW_ERRORS={}
def fwcap(k,i,o,z,kind='device_command',ex=None,effects=None,errors=(),c=None):cap(k,i,o,c or _SCREEN,z,kind,'agent',ex,effects);FW_ERRORS[k]=list(errors)
fwcap('defaults.configure',_DEF_IN,obj({'defaults':FREE}),'Defaults窗口：T1 IMS、T2柱、T3进样口、T4/T5传输线温度（off或5–100 C，T3至200 C）、EPC_IMS漂移气（1–500 mL/min）与EPC_GC载气（1–150 mL/min）、漂移电压极性、谱图平均（0–99）、触发时长（10–2000 us）','configuration',
      {'t1_c':45.,'averaging':6},['device.defaults','gc.target_k','ims.target_k','injector.target_k','device.parameters','snapshot.state','device.log'],['POWER_OFF'],['display_digits','rotary_knob','ims_tube','gc_oven','syringe_body'])
fwcap('program.manage',oopt({'op':enum(['create','rename','copy','delete','move_up','move_down','select','set_averages']),'index':integer(0,MAX_PROGRAMS-1),'name':NAME,
      'averages':{'type':'integer','x-unit':'1','enum':[0,6,99]}},['op']),obj({'selected':nullable(integer(0,MAX_PROGRAMS-1)),'count':integer(0,MAX_PROGRAMS)}),
      'Programs窗口：新建（键盘输入名称）、改名、复制、删除、上移/下移、选择程序与平均设置（off/6/99）；最多100个','configuration',{'op':'create','name':'QC'},['programs.list'],
      ['NO_PROGRAM','PROGRAM_LIST_FULL','INVALID_NAME'])
fwcap('program.action',oopt({'op':enum(['add','edit','copy','delete','set_end']),'line':integer(0,999),'time_s':number('s',0,PROGRAM_MAX_S),'rec':nullable(enum(['rec','stop'])),
      'e_ims':nullable(number('mL/min',0,500)),'e_gc':nullable(number('mL/min',0,150)),'end_s':number('s',.02,PROGRAM_MAX_S)},['op']),obj({'program':FREE}),
      '选中程序的动作行：开始时间、记录rec/stop、E_IMS与E_GC流量斜坡设定点（空=不涉及）；新建/编辑/复制/删除行，程序结束行时间决定运行时长','configuration',
      {'op':'add','time_s':60.,'e_gc':10.},['programs.list'],['NO_PROGRAM','INVALID_TIME'])
fwcap('program.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),'Spectra窗口Program Start（手动进样信息窗选YES）：按选中程序的流量斜坡与记录动作运行；由PAL完成吸样/进样（仿真替代手动注射）；异步',
      effects=['acquisition.phase','workflow.phase','acquisition.elapsed_s','recording.state','device.parameters','snapshot.state','device.log'],errors=['POWER_OFF','MODE_ACTIVE','NO_PROGRAM','INVALID_PROGRAM','RECORDING_ACTIVE','DRIFT_FLOW_LOW','STORAGE_FULL'],
      c=['display_digits','rotary_knob','gc_oven','ims_tube','syringe_body'])
fwcap('recording.set',obj({'on':BOOL}),obj({'on':BOOL,'file':TEXT}),'Spectra窗口Recording START/STOP：手动记录实时谱，停止时在内部闪存生成测量文件（剩余存储分钟按平均数计算）',ex={'on':True},
      effects=['recording.state','files.stored','device.log','device.errors'],errors=['POWER_OFF','MODE_ACTIVE','STORAGE_FULL'])
fwcap('sample_names.generate',obj({}),obj({'count':integer(0,999)}),'Trigger模式样品名单：自动生成mea_1–mea_999（导入名单需U盘或共享文件夹）','configuration',effects=['sample_names.state'],errors=['POWER_OFF'])
fwcap('mode.set',oopt({'mode':enum(['normal','trigger','remote','cleaning','standby']),'hours':nullable(integer(1,96)),'hv_on':BOOL},['mode']),obj({'mode':TEXT,'until_s':nullable(number('s'))}),
      'System > Modes：Trigger模式（自动进样器为主机）、Remote模式（等待Sequence Designer文件）、Cleaning（1–96 h或无限；加热到最高、漂移电压关，HV On可临时开）、Standby（EPC_IMS 10、EPC_GC 5 mL/min）；normal结束模式',
      ex={'mode':'cleaning','hours':1},effects=['device.mode','device.parameters','snapshot.state','device.log'],errors=['POWER_OFF','RECORDING_ACTIVE'])
fwcap('settings.configure',obj({'settings':FREE}),obj({'settings':FREE}),'System > Settings / Transfer：IP、简化视图与ID、显示亮度与屏保、风扇、过热阈值、序列后动作、快照限值、污染搜索、GC柱数据、漂移管长度/电压、漂移气、隔垫进样次数上限、报警音频率、EPC气体类型、自动导出与服务器',
      'configuration',{'settings':{'display.brightness':4}},['device.settings','device.parameters','snapshot.state'],['UNKNOWN_SETTING','INVALID_SETTING'])
fwcap('settings.reset_default',obj({}),obj({'reset':BOOL}),'System > Settings > Default：系统设置恢复出厂值，删除全部用户测量程序','configuration',effects=['device.settings','programs.list','device.log','device.parameters','snapshot.state'],errors=['POWER_OFF'])
fwcap('clock.set',oopt({'date':TEXT,'time':TEXT,'timezone':enum(list(TIMEZONES))}),obj({'clock':TEXT}),'System > Settings > Date/Time：设定日期、时间与时区','configuration',{'time':'08:30:00'},['device.clock'],['INVALID_DATE'])
fwcap('snapshot.take',obj({}),obj({'snapshot':FREE}),'Snapshot窗口Take new Snapshot：记录当前EPC_GC压力、RIP高度、101.33 kPa下RIP位置与半峰宽及设定点，作为性能检查的目标值',effects=['snapshot.state','device.errors','device.log'],errors=['POWER_OFF'])
fwcap('files.delete',oopt({'names':arr(TEXT),'all':BOOL}),obj({'deleted':integer(0,10**6),'remaining':integer(0,10**6)}),'System > Transfer > MEA Files Delete：删除内部存储的测量文件（选择或全部）',
      ex={'all':True},effects=['files.stored','recording.state','device.log'],errors=['NO_MEASUREMENTS','UNKNOWN_FILE'])
# 1.2.0 (sync audit): the gas really connected to the EPC inlets; with a different EPC gas type setting the real flows deviate (see _epc_factors)
cap('gas.configure_supply',oopt({'ims_gas':enum(GASES),'gc_gas':enum(GASES)}),obj({'supplied':FREE}),['gas_inlet'],
    '评测者：接到EPC_IMS（漂移气）与EPC_GC（载气）入口的实际气体（默认氮气，手册p.119-120出厂设定）；EPC气体类型设定与之不符时，实际流量=设定流量×η(设定气体)/η(实际气体)',
    'configuration','evaluator',{'gc_gas':'nitrogen'},['gas.supplied']);FW_ERRORS['gas.configure_supply']=['INVALID_ARGUMENT']
slot('gas.supplied',FREE,'1','接到EPC_IMS/EPC_GC入口的实际气体（评测者设定，默认氮气）','process_truth','evaluator','gas_inlet')
# 1.3.0 (sweep 10-19): the device errors of manual p.39-40 and the error list p.244-247 that need no absent equipment (fault.inject, evaluator)
FS_FAULTS={'high_pressure':'High pressure','drift_voltage_supply':'Drift voltage supply.','drift_supply_init':"Can't initalize drift supply. Program can not be started.",
           'hardware_access_failure':'Hardware access failure'}
cap('fault.inject',obj({'fault':enum(list(FS_FAULTS)),'active':BOOL}),obj({'faults':arr(TEXT)}),['housing','ims_tube'],
    '评测者：手册记载的设备错误（原因出现=active true，排除=false）：high_pressure过压（立即报警框与蜂鸣，全部温度控制器与流量控制器关闭，正在进行的测量中止；确认报警框或重启后恢复，原因仍在则再次报警）、'
    'drift_voltage_supply漂移电压电源故障（致命：漂移电压关、测量中止、启动被拒，排除后重启才恢复）、drift_supply_init漂移电源初始化失败（程序启动被拒）、'
    'hardware_access_failure切换漂移电压极性失败（极性不变，屏幕显示Hardware access failure）','simulation','evaluator',{'fault':'high_pressure','active':True},
    ['acquisition.phase','gas.enabled','recording.state','files.stored','device.errors','device.log','device.parameters','safety.high_pressure','truth.device_faults','snapshot.state'])
FW_ERRORS['fault.inject']=['INVALID_ARGUMENT']
fwcap('pressure_alarm.confirm',obj({}),obj({'alarm':BOOL}),'High pressure报警框确认（手册p.40）：过压原因已排除时全部温度与流量回到正常值；原因仍在则立即再次报警（报警框保留）',
      effects=['device.parameters','safety.high_pressure','device.errors','device.log','snapshot.state'],errors=['POWER_OFF'])
slot('safety.high_pressure',BOOL,'1','High pressure报警框（过压：温度与流量控制器关闭，确认或重启后恢复）')
slot('truth.device_faults',arr(TEXT),'1','注入且未排除的设备错误原因（评测者）','process_truth','evaluator','housing')
EXTRA_ERRORS={'acquisition.start':['DRIFT_VOLTAGE_ERROR','DRIFT_SUPPLY_INIT','HIGH_PRESSURE'],'program.start':['DRIFT_VOLTAGE_ERROR','DRIFT_SUPPLY_INIT','HIGH_PRESSURE'],
              'defaults.configure':['HARDWARE_ACCESS_FAILURE']}   # 1.3.0: refusals by the injected device errors
_FW_SLOTS=[('device.defaults','Defaults窗口设定值（T1–T5 C或null=off、EPC流量mL/min、极性、平均、触发时长）','setpoint'),('device.parameters','设备参数（实际温度、EPC流量与压力、机内温度、风扇、漂移电压、RIP）','derived'),
           ('device.mode','特殊模式（normal/trigger/remote/cleaning/standby、结束时间、HV On）','derived'),('programs.list','测量程序表与选中程序','setpoint'),
           ('recording.state','手动记录状态、正在运行的程序与剩余存储分钟','derived'),('files.stored','内部存储的测量文件','derived'),('device.errors','错误信息窗口条目（发生/解决时间）','derived'),
           ('device.log','日志消息（警告/错误）','derived'),('device.settings','系统设置表','setpoint'),('device.clock','设备时钟（本地时间ISO）','derived'),
           ('sample_names.state','样品名单与当前名','derived'),('snapshot.state','快照目标值、当前值与检查结果','derived')]
for _k,_z,_o in _FW_SLOTS:slot(_k,arr(FREE) if _k in ('files.stored','device.errors','device.log') else TEXT if _k=='device.clock' else FREE,'1',_z,_o)
def _ramp(pts,t):
 for (t0,v0),(t1,v1) in zip(pts,pts[1:]):
  if t<t1:return v0+(v1-v0)*(t-t0)/(t1-t0) if t1>t0 else v1
 return pts[-1][1]
def _ramp_volume(pts,a,b):
 """Integral of a piecewise-linear set-point course (constant after the last point) between program times a and b."""
 knots=sorted({a,b}|{t for t,_ in pts if a<t<b});return sum((_ramp(pts,x)+_ramp(pts,y))/2*(y-x) for x,y in zip(knots,knots[1:]))
def _num(schema,v):
 """Numbers as the schema types them (a panel keypad gives 70 for 70.0)."""
 t=[x.get('type') for x in schema.get('anyOf',[schema])]
 return float(v) if 'number' in t and isinstance(v,(int,float)) and not isinstance(v,bool) else v
def _ipv4(s):return isinstance(s,str) and re.fullmatch(r'(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})',s) is not None and all(int(x)<=255 for x in s.split('.'))
def _safe_name(s):return isinstance(s,str) and 0<len(s)<=30 and re.fullmatch(r'[A-Za-z0-9_\- ]+',s) is not None
_PANELS=weakref.WeakKeyDictionary()      # front panel per runtime (kept out of __dict__: rejected commands restore __dict__ from a copy)
class FlavourSpecFirmware:
 """Firmware state and commands of the FlavourSpec touch screen (mixed into the runtime)."""
 def _fw_init(self):
  self.fw=dict(defaults=copy.deepcopy(FS_DEFAULTS),settings={k:copy.deepcopy(v[1]) for k,v in FS_SETTINGS.items()},programs=[],selected=None,recording=None,files=[],mode='normal',mode_until=None,hv_on=False,
               sample_names=[],sample_index=0,clock_offset=0.,timezone='UTC',errors=[],log=[],next_error=1,injections=0,housing_k=None,fan=0.,temps={'t4':None,'t5':None},
               low_pressure_s=0.,heaters_off=False,live=[0.]*96,run=None,run_end_s=None,power_epoch=0,serial=0,faults=[],hp_alarm=False,fatal=False)
  self.fw['snapshot']=dict(values=self._snapshot_values(nominal=True),setpoints=self._setpoints(),taken='factory acceptance')
 # ---- clock, log, errors
 def _clock(self,t=None):
  s=EPOCH_S+(self.time_s if t is None else t)+self.fw['clock_offset']+3600*TIMEZONES[self.fw['timezone']]
  return datetime.datetime.fromtimestamp(math.floor(s),tz=datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
 def _log(self,level,text):
  self.fw['log'].append(dict(time=self._clock(),level=level,text=text));del self.fw['log'][:-MAX_LOG]
 def _error(self,key,active,reporter,text,event=False):
  open_=[e for e in self.fw['errors'] if e['key']==key and e['resolved'] is None]
  if active and (event or not open_):
   e=dict(id=self.fw['next_error'],key=key,reporter=reporter,text=text,occurred=self._clock(),occurred_s=self.time_s,resolved=self._clock() if event else None,resolved_s=self.time_s if event else None)
   self.fw['next_error']+=1;self.fw['errors'].append(e);self._log('error',text)
  if not active:
   for e in open_:e['resolved']=self._clock();e['resolved_s']=self.time_s
 # ---- thermal, flows, IMS
 def _target_k(self,name):
  amb=self.plant['ambient_k']
  if not self.powered or self.fw['heaters_off'] or self.fw.get('hp_alarm'):return amb   # 1.3.0: overpressure switches the temperature controllers off
  if name=='incubator':return self.method['incubator_k'] if self.method else amb
  if self.fw['mode']=='cleaning':return 473.15 if name=='injector' else 393.15
  v=self.fw['defaults'][{'ims':'t1_c','gc':'t2_c','injector':'t3_c','t4':'t4_c','t5':'t5_c'}[name]]
  return amb if v is None else max(amb,v+273.15)
 def _profile(self,prog):
  d=self.fw['defaults'];pts={'ims':[(0.,d['epc_ims_ml_min'] or 0.)],'gc':[(0.,d['epc_gc_ml_min'] or 0.)]}
  for a in prog['actions']:
   for key,f in (('ims','e_ims'),('gc','e_gc')):
    if a[f] is not None:
     if a['time_s']<=0:pts[key][0]=(0.,a[f])
     else:pts[key].append((a['time_s'],a[f]))
  return pts
 def _rec_intervals(self,prog):
  out,on=[],None
  for a in prog['actions']:
   if a['rec']=='rec' and on is None:on=a['time_s']
   elif a['rec']=='stop' and on is not None:out.append((on,a['time_s']));on=None
  if on is not None:out.append((on,prog['end_s']))
  return out
 def _idle_flows(self):
  """Drift and carrier flow (m^3/s) outside a run: the Defaults set points, or the standby flows (10 / 5 mL/min, manual p.85)."""
  if self.fw['mode']=='standby':return 10*ML,5*ML
  d=self.fw['defaults'];return (d['epc_ims_ml_min'] or 0.)*ML,(d['epc_gc_ml_min'] or 0.)*ML
 def _flows(self):
  if not (self.powered and self.gas and not self._gas_empty()) or self.fw.get('hp_alarm'):return 0.,0.
  run=self.fw['run']
  if self.phase=='running' and run:
   t=self.elapsed if self.workflow in ('separating','retract_inlet') else 0.
   if run.get('profile'):return _ramp(run['profile']['ims'],t)*ML,_ramp(run['profile']['gc'],t)*ML
   return self.method['drift_flow_m3_s'],self.method['carrier_scale']*source_carrier_flow(t if t>0 else 0.)
  return self._idle_flows()
 def _epc_factors(self):
  """Real / set flow of EPC_IMS and EPC_GC.  Each EPC holds the inlet pressure it computes for the gas type set in System > Settings > EPC Settings;
  laminar flow at a given pressure is inversely proportional to the viscosity, so a wrong gas type gives real = set * eta(set) / eta(connected)
  (manual p.75, p.120: the EPCs must be set to the gas actually used).  1 when the setting matches the connected gas (default nitrogen / nitrogen)."""
  s,g=self.fw['settings'],self.__dict__.get('supply_gas') or dict(ims='nitrogen',gc='nitrogen')
  return GAS_VISCOSITY[s['epc.ims_gas']]/GAS_VISCOSITY[g['ims']],GAS_VISCOSITY[s['epc.gc_gas']]/GAS_VISCOSITY[g['gc']]
 def _gas_empty(self):return self.supply<=1e-15                     # the supply bisection leaves a residue of rounding size
 def _hv(self):return self.powered and not self.fw.get('fatal') and (self.fw['mode']!='cleaning' or self.fw['hv_on'])   # 1.3.0: drift voltage supply fault: off
 def _ion_factor(self):
  """Analyte signal per eluted amount: linear in the trigger (gate) duration; the literature templates are positive-mode peaks, so the negative drift
  voltage shows none of them; no signal while the drift voltage is off (cleaning without HV On)."""
  d=self.fw['defaults'];return 0. if (d['polarity']=='negative' or not self._hv()) else d['trigger_us']/100.
 def _setpoints(self):
  d=self.fw['defaults'];return {k:d[k] for k in ('t1_c','t2_c','t3_c','t4_c','t5_c','epc_ims_ml_min','epc_gc_ml_min','polarity')}
 def _snapshot_values(self,nominal=False):
  s,d=self.fw['settings'],self.fw['defaults']
  if nominal:
   t1=(d['t1_c'] if d['t1_c'] is not None else 25.)+273.15;t2=(d['t2_c'] if d['t2_c'] is not None else 25.)+273.15;q=(d['epc_gc_ml_min'] or 0.)*ML;hv=True
  else:
   amb=self.plant['ambient_k'] if self.plant else 298.15;t1=self.temperature['ims'] or amb;t2=self.temperature['gc'] or amb;q=self._flows()[1];hv=self._hv()
  po=101325.;eta=GAS_VISCOSITY[s['epc.gc_gas']]*(t2/293.15)**.7;dd=s['column.id_mm']*1e-3
  pin=math.sqrt(po*po+256*eta*s['column.length_m']*q*po/(math.pi*dd**4))   # compressible Poiseuille flow through the capillary column
  pol=d['polarity'];k=K0_RIP[pol]*t1/273.15;td=(s['drift.tube_mm']*1e-3)**2/(k*s['drift.voltage_v'])   # drift time at 101.33 kPa, t = L^2/(K V)
  return dict(epc_gc_pressure=round(pin/1e3,4),rip_height=round(RIP_HEIGHT_V[pol]*d['trigger_us']/100. if hv else 0.,4),rip_position=round(td*1e3,5),fwhm=round(td*1e3/RESOLUTION,5))
 def _snapshot_check(self):
  sn=self.fw['snapshot'];cur=self._snapshot_values();s=self.fw['settings']
  if sn is None or sn['setpoints']!=self._setpoints():return cur,None
  bad=[k for k in SNAP_KEYS if not sn['values'][k]*s[f'snapshot.{k}_lo']/100-1e-9<=cur[k]<=sn['values'][k]*s[f'snapshot.{k}_hi']/100+1e-9]
  return cur,bad
 def _contamination_mv(self):
  """Highest live IMS signal right of the search start (RIP-relative drift); the detector signal unit is taken as mV (assumption)."""
  return max([v for x,v in zip(DRIFT_AXIS,self.fw['live']) if x>=self.fw['settings']['contamination.search_from']] or [0.])
 # ---- storage and files
 def _bytes_per_s(self,averaging):return RAW_HZ/max(1,averaging)*SPECTRUM_B
 def _used_b(self):return sum(f['size_b'] for f in self.fw['files'])
 def _free_min(self):return max(0.,(STORAGE_B-self._used_b())/self._bytes_per_s(self.fw['defaults']['averaging'])/60)
 def _new_file(self,kind,start_clock,seconds,averaging,program=None,sample=None):
  if seconds<=0:return None
  size=seconds*self._bytes_per_s(averaging)
  if size>STORAGE_B-self._used_b():self._error('storage',True,'Storage',"Can't save measurement.",event=True);return None
  neg=self.fw['defaults']['polarity']=='negative';base=start_clock.replace('-','').replace(':','').replace('T','_')+('_NEG' if neg else '');name=base+'.mea';n=2
  while any(f['name']==name for f in self.fw['files']):name=f'{base}_{n}.mea';n+=1
  s=self.fw['settings'];f=dict(name=name,created=start_clock,kind=kind,duration_s=round(seconds,3),size_b=int(size),averaging=averaging,polarity='negative' if neg else 'positive',program=program,sample_name=sample,
       id=s['view.id'] if s['view.append_id'] else None,column=dict(name=s['column.name'],length_m=s['column.length_m'],id_mm=s['column.id_mm'],film_um=s['column.film_um'],polarity=s['column.polarity']),
       drift_gas=s['drift.gas'],tube_mm=s['drift.tube_mm'],drift_voltage_v=s['drift.voltage_v'],exported=False)
  self.fw['files'].append(f);self._log('info','Measurement saved: '+name)
  if s['export.auto']:self._error('export',True,'Export',s['server.protocol']+' connection failed:',event=True)   # no file server in the simulated lab
  return name
 def _selected(self):
  i=self.fw['selected'];return self.fw['programs'][i] if i is not None else None
 # ---- runs (program start, trigger, API)
 def _fw_begin_run(self,prog,kind):
  if prog:end=prog['end_s'];rec=self._rec_intervals(prog);prof=self._profile(prog);avg=prog['averages'];name=prog['name']
  else:end=self.method['run_s'];rec=[(0.,end)];prof=None;avg=self.fw['defaults']['averaging'];name=None
  sample=None
  if kind=='trigger' and self.fw['sample_names']:
   sample=self.fw['sample_names'][self.fw['sample_index']%len(self.fw['sample_names'])];self.fw['sample_index']+=1
  self.fw['run']=dict(kind=kind,program=name,profile=prof,rec=rec,averages=avg,start=self._clock(),sample=sample,end_s=end);self.fw['run_end_s']=end
  self._log('info',('Program started: '+name) if name else 'Measurement started')
 def _fw_close_run(self):
  run=self.fw['run']
  if not run or self.phase=='running':return
  t=self.elapsed;rec=sum(max(0.,min(b,t)-a) for a,b in run['rec'] if a<t)
  self._new_file(run['kind'],run['start'],rec,run['averages'],run['program'],run['sample']);self._log('info','Measurement '+self.phase);self.fw['run']=None
 def _start_checks(self):
  if not self.sample or not self.method or not self.gas or self.cover>1e-6 or self.syringe or self._ledger()['column_mol']>1e-15:raise ActionRejected('NOT_READY')
  if max(abs(self.temperature[k]-self.method[k+'_k']) for k in ['gc','ims','injector','incubator'])>1. or self.method['injection_m3']>self.sample['headspace_m3']:raise ActionRejected('NOT_READY')
 def _start(self):
  self.serial+=1;self.operation_id=f'{self.instance_id}:op:{self.serial}';self.phase='running';self.workflow='incubating';self.stage_elapsed=0.;self.stage_start_q=self.q[:];self.elapsed=0.;self.gc_volume=0.;self.points=[];self.signal=None
  return dict(phase=self.phase,operation_id=self.operation_id)
 # ---- commands
 def _fw_invoke(self,k,a):
  fw=self.fw
  if k in PANEL_CAPS:return self._panel_cmd(k,a)
  if k=='clock.set':return self._fw_clock(a)
  if k=='files.delete':
   if not fw['files']:raise ActionRejected('NO_MEASUREMENTS')
   names=[f['name'] for f in fw['files']] if a.get('all') else a.get('names',[])
   if any(n not in [f['name'] for f in fw['files']] for n in names):raise ActionRejected('UNKNOWN_FILE')
   fw['files']=[f for f in fw['files'] if f['name'] not in names];self._log('info',f'{len(names)} measurement files deleted');return dict(deleted=len(names),remaining=len(fw['files']))
  if k=='gas.configure_supply':
   if not a:raise ActionRejected('INVALID_ARGUMENT')
   if self.phase=='running':raise ActionRejected('BUSY')
   self.supply_gas.update({x[:-4]:v for x,v in a.items()});return dict(supplied=dict(self.supply_gas))
  if k=='settings.configure':
   new=copy.deepcopy(fw['settings'])
   for key,v in a['settings'].items():
    if key not in FS_SETTINGS:raise ActionRejected('UNKNOWN_SETTING')
    validate(FS_SETTINGS[key][0],v);v=_num(FS_SETTINGS[key][0],v)
    if key in ('network.ip','server.ip') and not (_ipv4(v) or (key=='server.ip' and v=='')):raise ActionRejected('INVALID_SETTING')
    if FS_SETTINGS[key][0] is NAME and len(v)>30:raise ActionRejected('INVALID_SETTING')
    new[key]=v
   if new['fan.start_c']>=new['fan.max_c'] or any(new[f'snapshot.{x}_lo']>=new[f'snapshot.{x}_hi'] for x in SNAP_KEYS):raise ActionRejected('INVALID_SETTING')
   if new['septum.max_injections']!=fw['settings']['septum.max_injections']:fw['injections']=0   # setting the limit after a septum change restarts the count (assumption)
   fw['settings']=new;return {'settings':copy.deepcopy(new)}
  if k=='fault.inject':return self._fw_fault(a)
  if not self.powered:raise ActionRejected('POWER_OFF')
  if k=='defaults.configure':
   d=dict(fw['defaults'],**{key:_num(_DEF_IN['properties'][key],v) for key,v in a.items()})
   if d['polarity']!=fw['defaults']['polarity'] and 'hardware_access_failure' in fw['faults']:   # 1.3.0: the polarity switch fails (manual p.247)
    raise ActionRejected('HARDWARE_ACCESS_FAILURE')                  # refused: nothing changes (the dialog shows the message)
   if self.method:
    for key,node in T_NODE.items():
     if d[key] is not None:self.method[node+'_k']=d[key]+273.15
    if d['epc_ims_ml_min'] is not None:self.method['drift_flow_m3_s']=d['epc_ims_ml_min']*ML
   fw['defaults']=d;self._log('info','Defaults changed');return {'defaults':copy.deepcopy(d)}
  if k=='program.manage':return self._fw_program(a)
  if k=='program.action':return self._fw_action(a)
  if k=='pressure_alarm.confirm':
   if fw['hp_alarm'] and 'high_pressure' not in fw['faults']:fw['hp_alarm']=False;self._log('info','High pressure confirmed: temperatures and flows reset')
   return {'alarm':fw['hp_alarm']}
  if k=='program.start':
   self._fault_start_checks()
   if fw['mode']!='normal':raise ActionRejected('MODE_ACTIVE')
   prog=self._selected()
   if prog is None:raise ActionRejected('NO_PROGRAM')
   if not prog['actions']:raise ActionRejected('INVALID_PROGRAM')
   if fw['recording']:raise ActionRejected('RECORDING_ACTIVE')
   if self.phase=='running':raise ActionRejected('BUSY')
   if not self.gas or self._gas_empty() or not fw['defaults']['epc_ims_ml_min']:raise ActionRejected('DRIFT_FLOW_LOW')
   if sum(b-a_ for a_,b in self._rec_intervals(prog))*self._bytes_per_s(prog['averages'])>STORAGE_B-self._used_b():raise ActionRejected('STORAGE_FULL')
   self._start_checks();out=self._start();self._fw_begin_run(prog,'program');return out
  if k=='recording.set':
   if a['on']:
    if fw['recording']:return dict(on=True,file='')
    if fw['mode']!='normal':raise ActionRejected('MODE_ACTIVE')
    if self.phase=='running':raise ActionRejected('BUSY')
    if self._free_min()<=0:raise ActionRejected('STORAGE_FULL')
    fw['recording']=dict(start_s=self.time_s,start=self._clock(),averaging=fw['defaults']['averaging']);self._log('info','Recording started');return dict(on=True,file='')
   r=fw['recording'];fw['recording']=None
   if not r:return dict(on=False,file='')
   return dict(on=False,file=self._new_file('recording',r['start'],self.time_s-r['start_s'],r['averaging']) or '')
  if k=='sample_names.generate':
   fw['sample_names']=[f'mea_{i}' for i in range(1,1000)];fw['sample_index']=0;return dict(count=999)
  if k=='mode.set':
   m=a['mode']
   if m!='normal' and m!=fw['mode']:
    if self.phase=='running':raise ActionRejected('BUSY')
    if fw['recording']:raise ActionRejected('RECORDING_ACTIVE')
   if m==fw['mode']=='cleaning' and 'hv_on' in a:fw['hv_on']=a['hv_on'];return dict(mode=m,until_s=fw['mode_until'])
   h=a.get('hours');fw['mode']=m;fw['mode_until']=self.time_s+3600*h if (m in ('cleaning','standby') and h is not None) else None;fw['hv_on']=bool(a.get('hv_on',False)) if m=='cleaning' else False
   self._log('info',f'{m} mode');return dict(mode=m,until_s=fw['mode_until'])
  if k=='settings.reset_default':
   fw['settings']={key:copy.deepcopy(v[1]) for key,v in FS_SETTINGS.items()};fw['programs']=[];fw['selected']=None;self._log('info','System settings reset to factory default');return dict(reset=True)
  if k=='snapshot.take':
   fw['snapshot']=dict(values=self._snapshot_values(),setpoints=self._setpoints(),taken=self._clock());self._fw_check();self._log('info','New snapshot taken');return dict(snapshot=copy.deepcopy(fw['snapshot']))
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _fw_clock(self,a):
  if set(a)=={'timezone'}:self.fw['timezone']=a['timezone'];return dict(clock=self._clock())   # the zone moves the local time shown, not the device time
  cur=self._clock()
  try:
   d=datetime.datetime.strptime(f"{a.get('date',cur[:10])} {a.get('time',cur[11:19])}",'%Y-%m-%d %H:%M:%S' if len(a.get('time',cur[11:19]))==8 else '%Y-%m-%d %H:%M').replace(tzinfo=datetime.timezone.utc)
  except ValueError:raise ActionRejected('INVALID_DATE') from None
  if 'timezone' in a:self.fw['timezone']=a['timezone']
  self.fw['clock_offset']=d.timestamp()-(EPOCH_S+self.time_s)-3600*TIMEZONES[self.fw['timezone']]+(0. if 'time' in a else self.time_s%1);return dict(clock=self._clock())
 def _fw_program(self,a):
  fw,op=self.fw,a['op'];progs=fw['programs']
  if op=='create':
   if len(progs)>=MAX_PROGRAMS:raise ActionRejected('PROGRAM_LIST_FULL')
   name=a.get('name') or f'Program_{len(progs)+1}'
   if not _safe_name(name):raise ActionRejected('INVALID_NAME')
   d=fw['defaults'];progs.append(dict(name=name,averages=6,actions=[dict(time_s=0.,rec='rec',e_ims=d['epc_ims_ml_min'],e_gc=d['epc_gc_ml_min'])],end_s=600.));fw['selected']=len(progs)-1
  elif op=='select':
   if not progs or a.get('index',0)>=len(progs):raise ActionRejected('NO_PROGRAM')
   fw['selected']=a.get('index',0)
  else:
   i=fw['selected']
   if i is None:raise ActionRejected('NO_PROGRAM')
   if op=='rename':
    if not _safe_name(a.get('name')):raise ActionRejected('INVALID_NAME')
    progs[i]['name']=a['name']
   elif op=='copy':
    if len(progs)>=MAX_PROGRAMS:raise ActionRejected('PROGRAM_LIST_FULL')
    c=copy.deepcopy(progs[i]);c['name']=(c['name'][:25]+'_copy');progs.insert(i+1,c);fw['selected']=i+1
   elif op=='delete':
    del progs[i];fw['selected']=min(i,len(progs)-1) if progs else None
   elif op in ('move_up','move_down'):
    j=i-1 if op=='move_up' else i+1
    if 0<=j<len(progs):progs[i],progs[j]=progs[j],progs[i];fw['selected']=j
   elif op=='set_averages':progs[i]['averages']=a.get('averages',6)
  return dict(selected=fw['selected'],count=len(progs))
 def _fw_action(self,a):
  prog=self._selected()
  if prog is None:raise ActionRejected('NO_PROGRAM')
  if self.phase=='running' and self.fw['run'] and self.fw['run']['program']==prog['name']:raise ActionRejected('BUSY')
  p=copy.deepcopy(prog);acts=p['actions'];op=a['op'];line=a.get('line',len(acts)-1)
  if op in ('edit','copy','delete') and not 0<=line<len(acts):raise ActionRejected('INVALID_ARGUMENT')
  if op=='add':acts.append(dict(time_s=a.get('time_s',0.),rec=a.get('rec'),e_ims=a.get('e_ims'),e_gc=a.get('e_gc')))
  elif op=='edit':acts[line].update({key:a[key] for key in ('time_s','rec','e_ims','e_gc') if key in a})
  elif op=='copy':acts.insert(line+1,copy.deepcopy(acts[line]))
  elif op=='delete':del acts[line]
  elif op=='set_end':p['end_s']=a.get('end_s',p['end_s'])
  acts.sort(key=lambda x:x['time_s'])                                 # the device keeps the actions in chronological order (manual p.57)
  if acts and acts[-1]['time_s']>=p['end_s']:raise ActionRejected('INVALID_TIME')
  prog.update(p);return {'program':copy.deepcopy(prog)}
 # ---- time evolution of the firmware state
 def _fw_step(self,dt):
  fw=self.fw
  if not self.plant:return
  amb=self.plant['ambient_k'];tau=self.plant['thermal_tau_s']
  for n in ('t4','t5'):
   t=fw['temps'][n] if fw['temps'][n] is not None else amb;tg=self._target_k(n);fw['temps'][n]=tg+(t-tg)*math.exp(-dt/tau)
  th=fw['housing_k'] if fw['housing_k'] is not None else amb;s=fw['settings']
  fw['fan']=min(1.,max(0.,(th-273.15-s['fan.start_c'])/(s['fan.max_c']-s['fan.start_c']))) if self.powered else 0.
  heat=[self.temperature[k] for k in ('ims','gc','injector')]+[fw['temps']['t4'],fw['temps']['t5']]
  rise=(4.+.12*sum(max(0.,x-amb) for x in heat)/5)*(1-.4*fw['fan']) if self.powered else 0.   # electronics + heater losses, fan cooling (engineering)
  fw['housing_k']=amb+rise+(th-amb-rise)*math.exp(-dt/600.)
  if fw['mode'] in ('cleaning','standby') and fw['mode_until'] is not None and self.time_s>=fw['mode_until']-1e-9:
   self._log('info',fw['mode']+' finished');fw['mode'],fw['mode_until'],fw['hv_on']='normal',None,False
  low=self.powered and self._gas_empty()
  fw['low_pressure_s']=fw['low_pressure_s']+dt if low else 0.
  fw['heaters_off']=low and fw['low_pressure_s']>=300.                 # low pressure: alarm after 10 s, temperature controllers off after 5 min (manual p.39)
  r=fw['recording']
  if r and (self.time_s-r['start_s'])*self._bytes_per_s(r['averaging'])>STORAGE_B-self._used_b():
   fw['recording']=None;self._new_file('recording',r['start'],(STORAGE_B-self._used_b())/self._bytes_per_s(r['averaging']),r['averaging'])
  self._fw_check()
 def _fw_check(self):
  fw=self.fw
  if not (self.plant and self.powered):return
  s=fw['settings'];fw['live_mv']=self._contamination_mv()
  self._error('low_pressure',fw['low_pressure_s']>=10.,'PressureControl','Low pressure')
  self._error('high_pressure',fw.get('hp_alarm',False),'PressureControl','High pressure')
  self._error('drift_voltage',bool(fw.get('fatal')),'DriftVoltage','Drift voltage supply.')
  self._error('overheat',fw['housing_k'] is not None and fw['housing_k']-273.15>s['overheat.threshold_c'],'TemperatureSupervision','Overheat')
  cur,bad=self._snapshot_check()
  self._error('snapshot_missing',bad is None,'DeviceSupervision','No snapshot found for current device settings')
  self._error('snapshot_range',bool(bad),'DeviceSupervision','At least one checked value is out of range')
  self._error('contamination',fw['live_mv']>s['contamination.threshold_mv'],'Contamination check','System contaminated')
  self._error('septum',fw['injections']>=s['septum.max_injections'],'Septum','Maximum allowed injections reached')
 def _fw_power(self,on):
  fw=self.fw
  if on:
   fw['power_epoch']+=1;fw['errors']=[];fw['low_pressure_s']=0.;fw['heaters_off']=False;self._log('info','System start')   # restart deletes the error events (manual p.86)
   fw['hp_alarm']='high_pressure' in fw['faults'];fw['fatal']='drift_voltage_supply' in fw['faults']   # 1.3.0: a restart resets temperatures and flows unless the cause remains
  else:
   r=fw['recording'];fw['recording']=None
   if r:self._new_file('recording',r['start'],self.time_s-r['start_s'],r['averaging'])
   fw['mode'],fw['mode_until'],fw['hv_on']='normal',None,False;self._log('info','System shut down')
 def _fault_start_checks(self):
  fw=self.fw
  if fw.get('fatal'):raise ActionRejected('DRIFT_VOLTAGE_ERROR')
  if 'drift_supply_init' in fw['faults']:raise ActionRejected('DRIFT_SUPPLY_INIT')
  if fw.get('hp_alarm'):raise ActionRejected('HIGH_PRESSURE')
 def _fw_fault(self,a):
  """1.3.0: evaluator fault.inject - the cause of a documented device error appears (active) or is removed.  Overpressure and the drift voltage
  supply act at once while powered (alarm / fatal error, the running measurement is aborted); their reset needs the confirmation or a restart."""
  fw=self.fw;f=a['fault'];fw['faults']=sorted((set(fw['faults'])|{f}) if a['active'] else (set(fw['faults'])-{f}))
  if a['active'] and self.powered and f in ('high_pressure','drift_voltage_supply'):
   if f=='high_pressure':fw['hp_alarm']=True
   else:fw['fatal']=True
   self._log('error',FS_FAULTS[f])
   if self.phase=='running':self.phase='failed';self.records[self.operation_id]=self._result()   # the measurement is aborted (assumption)
  self._fw_check();return {'faults':list(fw['faults'])}
 def _fw_read(self):
  fw=self.fw;amb=self.plant['ambient_k'] if self.plant else None;c=lambda k:None if k is None else round(k-273.15,3)   # noqa: E731
  drift,carrier=self._flows() if self.plant else (0.,0.);cur=self._snapshot_values() if self.plant else None;_,bad=self._snapshot_check() if self.plant else (None,None)
  params=dict(t1_c=c(self.temperature['ims']),t2_c=c(self.temperature['gc']),t3_c=c(self.temperature['injector']),t4_c=c(fw['temps']['t4'] if fw['temps']['t4'] is not None else amb),
              t5_c=c(fw['temps']['t5'] if fw['temps']['t5'] is not None else amb),epc_ims_ml_min=round(drift/ML,4),epc_gc_ml_min=round(carrier/ML,4),
              epc_gc_pressure_kpa=cur['epc_gc_pressure'] if cur else None,housing_c=c(fw['housing_k'] if fw['housing_k'] is not None else amb),fan=round(fw['fan'],4),
              drift_voltage_on=bool(self.plant) and self._hv(),polarity=fw['defaults']['polarity'],rip_position_ms=cur['rip_position'] if cur else None,
              rip_height_v=cur['rip_height'] if cur else None,fwhm_ms=cur['fwhm'] if cur else None,contamination_mv=round(fw.get('live_mv',0.),6),low_pressure=fw['low_pressure_s']>=10.,
              heaters_off=fw['heaters_off'],injections=fw['injections'])
  rem=None if fw['mode_until'] is None else max(0.,fw['mode_until']-self.time_s)
  r=fw['recording'];run=fw['run']
  return {'device.defaults':copy.deepcopy(fw['defaults']),'device.parameters':params,'device.mode':dict(mode=fw['mode'],until_s=fw['mode_until'],remaining_s=rem,hv_on=fw['hv_on']),
          'programs.list':dict(programs=copy.deepcopy(fw['programs']),selected=fw['selected']),
          'recording.state':dict(on=r is not None,since_s=r['start_s'] if r else None,free_min=round(self._free_min(),3),program=run['program'] if run else None),
          'files.stored':copy.deepcopy(fw['files']),'device.errors':copy.deepcopy(fw['errors']),'safety.high_pressure':bool(self.powered and fw['hp_alarm']),'truth.device_faults':list(fw['faults']),'device.log':copy.deepcopy(fw['log']),'device.settings':copy.deepcopy(fw['settings']),
          'device.clock':self._clock(),'sample_names.state':dict(count=len(fw['sample_names']),index=fw['sample_index'],current=fw['sample_names'][fw['sample_index']%len(fw['sample_names'])] if fw['sample_names'] else None),
          'snapshot.state':dict(values=copy.deepcopy(fw['snapshot']['values']) if fw['snapshot'] else None,setpoints=copy.deepcopy(fw['snapshot']['setpoints']) if fw['snapshot'] else None,
                                taken=fw['snapshot']['taken'] if fw['snapshot'] else None,current=cur,out_of_range=bad)}
 # ---- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
 @property
 def panel(self):
  if self not in _PANELS:_PANELS[self]=_panel_ui.PanelUI(PANEL_SPEC,self,hooks=PANEL_HOOKS)
  return _PANELS[self]
 def panel_read(self):
  out=self.read('agent');fw=self.fw;c=self._clock();sel=self._selected()
  out.update({'host.powered':self.powered and self.plant is not None,'host.power_epoch':fw['power_epoch'],
              'host.clock':dict(year=int(c[0:4]),month=int(c[5:7]),day=int(c[8:10]),hour=int(c[11:13]),minute=int(c[14:16]),second=int(c[17:19])),
              'host.spectrum':self._panel_spectrum(),'host.program_rows':self._panel_program_rows(),'host.programs':[dict(idx=i,name=p['name'],avg='off' if not p['averages'] else str(p['averages']),
               duration=_mmss(p['end_s'],False)) for i,p in enumerate(fw['programs'])],'host.program_view':self._panel_program_view(),
              'host.flows_differ':self._flows_differ(),'host.errors':self._panel_errors(),'host.has_errors':any(e['resolved'] is None for e in fw['errors']),
              'host.log':[dict(idx=i,**e) for i,e in enumerate(reversed(fw['log']))],'host.files':[dict(idx=i,**f) for i,f in enumerate(fw['files'])],
              'host.sample_names':[dict(idx=i,name=n,current=i==fw['sample_index']%max(1,len(fw['sample_names']))) for i,n in enumerate(fw['sample_names'][:200])],
              'host.column_presets':[dict(idx=i,**p) for i,p in enumerate(COLUMN_PRESETS)],'host.version':FW_VERSION,'host.serial':DEVICE_SERIAL,'host.timezone':fw['timezone'],
              'host.selected_name':sel['name'] if sel else None,'host.selected_end':sel['end_s'] if sel else None,'host.selected_avg':sel['averages'] if sel else None,
              'host.default_name':f"Program_{len(fw['programs'])+1}"})
  return out
 def _flows_differ(self):
  p=self._selected();d=self.fw['defaults']
  if not p:return False
  prof=self._profile(dict(p,actions=[a for a in p['actions'] if a['time_s']<=0]))
  return any(abs(prof[k][0][1]-(d[f] or 0.))>1e-9 for k,f in (('ims','epc_ims_ml_min'),('gc','epc_gc_ml_min')))
 def _panel_spectrum(self):
  """Live spectrum as the Spectra window draws it: drift time (ms) against signal (mV), the reaction ion peak (assumed Gaussian of the snapshot model)
  plus the analyte signal of the last time window."""
  if not (self.plant and self.powered):return []
  cur=self._snapshot_values();t_rip=cur['rip_position'];sig=t_rip/RESOLUTION/2.3548
  return [[round(x*t_rip,4),round(1e3*cur['rip_height']*math.exp(-.5*((x*t_rip-t_rip)/sig)**2)+v,4)] for x,v in zip(DRIFT_AXIS,self.fw['live'])]
 def _panel_program_rows(self):
  p=self._selected()
  if not p:return []
  f=lambda v:'|' if v is None else f'{v:.1f}'   # noqa: E731
  rows=[dict(idx=i,time=_mmss(a['time_s']),rec={'rec':'rec','stop':'stop',None:'|'}[a['rec']],e_ims=f(a['e_ims']),e_gc=f(a['e_gc']),end=False,time_s=a['time_s'],rec_v=a['rec'],
             e_ims_v=a['e_ims'],e_gc_v=a['e_gc']) for i,a in enumerate(p['actions'])]
  return rows+[dict(idx=len(rows),time=_mmss(p['end_s']),rec='---',e_ims='---',e_gc='---',end=True,time_s=p['end_s'],rec_v=None,e_ims_v=None,e_gc_v=None)]
 def _panel_program_view(self):
  p=self._selected()
  if not p:return dict(ims=[],gc=[],rec=[])
  prof=self._profile(p);xs=sorted({0.,p['end_s']}|{t for t,_ in prof['ims']+prof['gc']});iv=self._rec_intervals(p)
  return dict(ims=[[t,_ramp(prof['ims'],t)] for t in xs],gc=[[t,_ramp(prof['gc'],t)] for t in xs],
              rec=[[t,10. if any(a<=t<b for a,b in iv) else 0.] for t in sorted(set(xs)|{x for ab in iv for x in ab})])
 def _panel_errors(self):
  es=[dict(e,report='resolve' if e['resolved'] else 'report') for e in self.fw['errors']]
  return dict(current=[e for e in es if e['resolved'] is None],occurred=sorted(es,key=lambda e:-e['occurred_s']),
              resolved=sorted([e for e in es if e['resolved']],key=lambda e:-e['resolved_s']),events=sorted(es,key=lambda e:-max(e['occurred_s'],e['resolved_s'] or 0)))
 def panel_invoke(self,capability_id,arguments):
  try:return self.invoke(capability_id,**arguments)
  except ActionRejected as exc:raise _panel_ui.PanelRejected(str(exc)) from None
 def panel_time(self):return self.time_s
 def _panel_cmd(self,k,a):
  if not self.plant:raise ActionRejected('NOT_READY')
  if k=='panel.capture':
   data,w,h=self.panel.png_bytes(a['display_id']);sha=hashlib.sha256(data).hexdigest()
   d=Path(self.__dict__.get('panel_capture_dir') or Path(tempfile.gettempdir())/'chemistry_sim_panel'/self.instance_id);d.mkdir(parents=True,exist_ok=True);p=d/f"{a['display_id']}_{sha[:16]}.png"
   if not p.exists():p.write_bytes(data)
   return dict(display_id=a['display_id'],time_s=self.time_s,image=dict(path=str(p),format='png',width=w,height=h,sha256=sha))
  if k=='panel.frame':return dict(frame=self.panel.frame())
  g={'panel.press':lambda:self.panel.press(a['input_id'],float(a.get('duration_s',.1))),'panel.switch':lambda:self.panel.switch(a['input_id'],a['position']),
     'panel.turn':lambda:self.panel.turn(a['input_id'],a['detents']),'panel.touch':lambda:self.panel.touch(a['display_id'],a['x'],a['y'],float(a.get('duration_s',.1)),
     (a['to_x'],a['to_y']) if 'to_x' in a and 'to_y' in a else None)}[k]
  try:return dict(accepted=bool(g()))
  except _panel_ui.PanelError:raise ActionRejected('INVALID_ARGUMENT') from None
def _mmss(t,ms=True):
 m,s=divmod(t,60.);return f'{int(m):02d}:{s:06.3f}' if ms else f'{int(m):02d}:{int(s):02d}'

def _panel_engine():
 try:
  from . import _panel_ui as engine                                   # imported as asset_runtime.gas_flavourspec25gc (tests, Matterix bridge)
  return engine
 except ImportError:                                                  # loaded from its file path
  import importlib.util as iu,sys
  if '_chemsim_panel_ui' not in sys.modules:
   sp=iu.spec_from_file_location('_chemsim_panel_ui',Path(__file__).with_name('_panel_ui.py'));sys.modules['_chemsim_panel_ui']=iu.module_from_spec(sp);sp.loader.exec_module(sys.modules['_chemsim_panel_ui'])
  return sys.modules['_chemsim_panel_ui']
def _hook_text(panel,ctx,s):
 """Fields keyed in as numbers, written as text (dates YYYY-MM-DD, times HH:MM:SS, IP addresses): s['pattern'].format(*fields)."""
 return s['pattern'].format(*[int(round(panel._src({'field':f},ctx) or 0)) for f in s['fields']])
_panel_ui=_panel_engine();_SPEC_PATH=ROOT/'assets/expansion_batch04/t3_analytical'/SLUG/'panel_ui.json'
PANEL_SPEC=_panel_ui.load_spec(_SPEC_PATH,capabilities=set(CAPS)) if _SPEC_PATH.exists() else None
PANEL_CAPS=_panel_ui.capability_definitions(PANEL_SPEC) if PANEL_SPEC else {}
def _hook_thresholds(panel,ctx,s):
 """Snapshot thresholds of the value chosen in the snapshot window (var snap_sel) as settings keys."""
 k=panel.ui['vars']['snap_sel'];return {f'snapshot.{k}_lo':panel._src({'field':'th_lo'},ctx),f'snapshot.{k}_hi':panel._src({'field':'th_hi'},ctx)}
PANEL_HOOKS={'fs_text':_hook_text,'fs_thresholds':_hook_thresholds}
_PANEL_FX=list(dict.fromkeys(x for c in (_panel_ui.spec_capabilities(PANEL_SPEC) if PANEL_SPEC else []) for x in CAPS[c][7]))
for _k,_v in PANEL_CAPS.items():
 CAPS[_k]=(_v['input_schema'],_v['output_schema'],_v['component_ids'],_v['function_zh'],_v['kind'],_v['visibility'],_v['example_arguments'],[] if _k in ('panel.capture','panel.frame') else list(_PANEL_FX));FW_ERRORS[_k]=[]

def source_carrier_volume(t):
 t=max(0.,t);v=2*min(t,120.)
 if t>120:
  z=min(t-120,1080.);v+=2*z+(.5*98/1080)*z*z
 if t>1200:v+=100*(t-1200)
 return v*1e-6/60

def source_carrier_flow(t):return (2. if t<=120 else 2+98*(t-120)/1080 if t<1200 else 100)*1e-6/60

def vial_target(index):return [-.46+(index%10)*.030,.05-(-.31+(index//10)*.033),0.]

class GasFlavourspec25gcRuntime(FlavourSpecFirmware):
 def __init__(self,instance_id='gcims_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.gas=False;self.cover=0.;self.q=[0.,0.,0.];self.plant=None;self.method=None;self.temperature={k:None for k in ['gc','ims','injector','incubator']};self.supply=0.;self.consumed=0.;self.sample=None;self.sample_loaded_n=0.;self.sample_returned_n=0.;self.syringe=None;self.sample_exhaust_n=0.;self.bands=[];self.phase='idle';self.workflow='idle';self.stage_elapsed=0.;self.stage_start_q=[0.,0.,0.];self.elapsed=0.;self.gc_volume=0.;self.points=[];self.signal=None;self.calibration=None;self.serial=0;self.operation_id=None;self.records={};self.templates=json.loads((ROOT/TEMPLATE_REL).read_text())['substances'];self._principals={};self._requests={};self._owners={};self._caller=None;self.supply_gas=dict(ims='nitrogen',gc='nitrogen');self._fw_init()
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ['agent','evaluator','debug'] or principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return GasFlavourspec25gcSession(self,principal_id)
 def describe(self):return export_gas_flavourspec25gc_contract_metadata()
 def read(self,visibility='agent'):
  m=self.method or {};d={'plant.configured':self.plant is not None,'method.configured':self.method is not None,'power.enabled':self.powered,'gas.enabled':self.gas,'cover.angle_rad':self.cover,'sample.loaded':self.sample is not None,'sample.identity':self.sample['sample_id'] if self.sample else '','sample.vial_index':self.sample['vial_index'] if self.sample else None,'acquisition.phase':self.phase,'workflow.phase':self.workflow,'acquisition.elapsed_s':self.elapsed,'detector.total_signal':self.signal,'gas.remaining_mol':self.supply,'calibration.ready':self.calibration is not None}
  for name in self.temperature:d.update({name+'.target_k':m.get(name+'_k'),name+'.temperature_k':self.temperature[name],name+'.indicated_k':self.temperature[name]})
  for i,name in enumerate(['x','y','z']):d['gantry.'+name+'_m']=self.q[i]
  d['gas.supplied']=dict(self.supply_gas);d.update(self._fw_read())
  return copy.deepcopy({k:v for k,v in d.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _ledger(self):
  sn=self.sample['remaining_n'] if self.sample else 0.;sy=self.syringe['n'] if self.syringe else 0.;col=sum(b['n']-b['eluted'] for b in self.bands);inj=sum(b['n'] for b in self.bands);out=sum(b['eluted'] for b in self.bands);init=self.plant['gas_supply_mol'] if self.plant else 0.;exhaust=self.consumed+self.sample_exhaust_n
  return dict(gas_remaining_mol=self.supply,gas_consumed_mol=self.consumed,sample_remaining_mol=sn,syringe_mol=sy,column_mol=col,exhaust_mol=exhaust,total_residual_mol=init+self.sample_loaded_n-self.supply-sn-sy-col-exhaust-self.sample_returned_n,analyte_residual_mol=inj-out-col,sample_id=self.sample['sample_id'] if self.sample else '')
 def _result(self):return dict(phase=self.phase,workflow=self.workflow,complete=self.phase=='succeeded',points=copy.deepcopy(self.points),drift_ratio_axis=DRIFT_AXIS[:],elapsed_s=self.elapsed,template_source='Ma et al. 10.1093/jaoacint/qsac108 Table1; identities withheld',evidence='measured_literature positions; synthetic reconstructed 2D peak widths, gas transfer and detector response',warnings=['Source FlavourSpec is not FlavourSpec25 calibration','Drift axis is dimensionless t/t_RIP, NOT milliseconds or absolute mobility','Known finite equilibrated headspace supplied by host; no matrix partition/ion cluster chemistry','Gain/peak shapes/reference gas state/method transfer explicitly assumed'])
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[k][0],a)
  if self._foreign_run(k,a):raise ActionRejected('ACCESS_DENIED')
  before=copy.deepcopy(self.__dict__)
  try:
   result=self._fw_invoke(k,a) if k in FW_ERRORS else self._invoke(k,a);self._fw_close_run();validate(CAPS[k][1],result)
   if CAPS[k][4] not in ['query','analysis'] or k=='calibration.fit':self.revision+=1
   return result
  except Exception:self.__dict__.clear();self.__dict__.update(before);raise
 def _foreign_run(self,k,a):
  """An agent may not stop or change another agent's running measurement: direct acquisition.stop, Defaults set points, EPC gas types and the factory
  default, and the same commands called by the Spectra Stop button and the windows (the session sets _caller around the call); unowned runs and
  evaluators are not restricted.  1.3.0 (lead rule 10-19): the rear power switch (and power.set, which models it) is a hard switch - a physical
  action that is never refused; switching off acts as a power loss."""
  change=k in ('defaults.configure','settings.reset_default') or (k=='settings.configure' and any(x.startswith('epc.') for x in a.get('settings',{})))
  if self.phase!='running' or not (change or k=='acquisition.stop'):return False
  owner=self._owners.get(self.operation_id);c=self.__dict__.get('_caller')
  return owner is not None and c is not None and c!=owner and self._principals.get(c)=='agent'
 def _area(self,roi):
  if roi['stop_s']<=roi['start_s'] or roi['drift_max_ratio']<=roi['drift_min_ratio'] or roi['stop_s']>self.elapsed+1e-8:raise ActionRejected('INVALID_WINDOW')
  selected=[i for i,x in enumerate(DRIFT_AXIS) if roi['drift_min_ratio']<=x<=roi['drift_max_ratio']]
  return sum(sum(p['signal'][i] for i in selected)*max(0.,min(p['time_s'],roi['stop_s'])-max(p['time_s']-p['window_s'],roi['start_s'])) for p in self.points)
 def _invoke(self,k,a):
  if k=='process.measure':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='template.read':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   t=self.templates[a['substance_id']];return dict(substance_id=a['substance_id'],retention_times_s=[x['retention_time_s'] for x in t['channels']],drift_ratios=[x['drift_ratio'] for x in t['channels']],template_source=t['source']['url']+' # Table1 '+t['cas'],evidence='measured_literature; source method conditions retained')
  if k=='signal.integrate':return {'area_s':self._area(a)}
  if k=='fingerprint.compare':
   f=np.zeros((20,96));span=self.fw['run_end_s'] or (self.method['run_s'] if self.method else 1.)
   for p in self.points:
    lo=p['time_s']-p['window_s'];hi=p['time_s']
    for j in range(20):f[j]+=np.asarray(p['signal'])*max(0.,min(hi,(j+1)*span/20)-max(lo,j*span/20))
   f=f.ravel();ref=np.asarray(a['reference']);den=float(np.linalg.norm(f)*np.linalg.norm(ref));score=float(np.dot(f,ref)/den) if den else None;return dict(cosine_similarity=min(1.,max(0.,score)) if score is not None else None,features=f.tolist(),evidence='integrated observed 20x96 bins; similarity alone is not chemical identification')
  if k=='calibration.fit':
   if not self.method or self.phase=='running':raise ActionRejected('PRECONDITION_FAILED')
   roi=a['roi']
   if not 0<=roi['start_s']<roi['stop_s']<=self.method['run_s'] or roi['drift_min_ratio']>=roi['drift_max_ratio']:raise ActionRejected('INVALID_WINDOW')
   x=np.asarray([v['concentration_mol_m3'] for v in a['standards']]);y=np.asarray([v['area_s'] for v in a['standards']]);sx=float(np.sum((x-x.mean())**2));sy=float(np.sum((y-y.mean())**2))
   if sx==0 or sy==0:raise ActionRejected('INVALID_CALIBRATION')
   slope=float(np.sum((x-x.mean())*(y-y.mean()))/sx);intercept=float(y.mean()-slope*x.mean());r2=max(0.,1-float(np.sum((y-intercept-slope*x)**2))/sy)
   if slope<=0 or r2<.95:raise ActionRejected('INVALID_CALIBRATION')
   self.calibration=dict(roi=copy.deepcopy(roi),slope=slope,intercept=intercept,lo=float(min(x)),hi=float(max(x)),label=a['label']);return dict(slope_s_m3_mol=slope,intercept_s=intercept,r_squared=r2)
  if k=='concentration.analyze':
   c=self.calibration;v=None;quality='incomplete'
   if self.phase=='succeeded':
    if not c:quality='uncalibrated'
    else:
     estimate=(self._area(c['roi'])-c['intercept'])/c['slope'];quality='estimated' if c['lo']<=estimate<=c['hi'] else 'outside_calibration';v=estimate if quality=='estimated' else None
   return dict(concentration_mol_m3=v,label=c['label'] if c else '',quality=quality,evidence='observed ROI integral and supplied external calibration; no sample composition access')
  if k=='plant.configure':
   if self.plant or self.powered or not a['accept_synthetic_response']:raise ActionRejected('INITIALIZATION_ONLY')
   self.plant=copy.deepcopy(a);self.supply=a['gas_supply_mol'];self.temperature={k:a['ambient_k'] for k in self.temperature};return {'configured':True}
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self.phase='cancelled';self.gas=False;self.records[self.operation_id]=self._result();return {'phase':self.phase}
  if k=='power.set':
   was=self.powered;self.powered=a['enabled']
   if self.powered!=was:self._fw_power(self.powered)
   if not self.powered:
    self.gas=False
    if self.phase=='running':self.phase='cancelled';self.records[self.operation_id]=self._result()
   return a
  if self.phase=='running':raise ActionRejected('BUSY')
  if k=='method.configure':
   if not self.plant or self.gas or not a['accept_method_transfer']:raise ActionRejected('PRECONDITION_FAILED')
   if min(a[k+'_k'] for k in self.temperature)<self.plant['ambient_k']:raise ActionRejected('TEMPERATURE_RANGE')
   self.method=copy.deepcopy(a);self.calibration=None
   self.fw['defaults'].update(t1_c=round(a['ims_k']-273.15,6),t2_c=round(a['gc_k']-273.15,6),t3_c=round(a['injector_k']-273.15,6),epc_ims_ml_min=round(a['drift_flow_m3_s']/ML,6),
                              epc_gc_ml_min=round(2*a['carrier_scale'],6))   # the method's temperatures and flows are the Defaults window set points
   return {'configured':True}
  if k=='gas.set':
   if a['enabled'] and (not self.powered or not self.method or self.cover>1e-6 or self.supply<=0):raise ActionRejected('NOT_READY')
   self.gas=a['enabled'];return a
  if k=='cover.set':
   if self.gas:raise ActionRejected('BUSY')
   self.cover=a['angle_rad'];return a
  if k=='gantry.move':self.q=[a['x_m'],a['y_m'],a['z_m']];return a
  if k=='sample.load':
   if self.sample or not self.plant or a['headspace_m3']>(10e-6 if a['vial_m3']=='10mL' else 20e-6) or sum(s['mole_fraction'] for s in a['solutes'])>1 or len({s['substance_id'] for s in a['solutes']})!=len(a['solutes']):raise ActionRejected('SAMPLE_INVALID')
   if any(s['substance_id'] not in self.templates for s in a['solutes']):raise ActionRejected('UNKNOWN_REFERENCE')
   self.sample=copy.deepcopy(a);n=a['pressure_pa']*a['headspace_m3']/(R*a['temperature_k']);self.sample['remaining_n']=n;self.sample['species']={s['substance_id']:n*s['mole_fraction'] for s in a['solutes']};self.sample_loaded_n+=n;return {'loaded':True}
  if k=='sample.unload':
   if not self.sample:raise ActionRejected('NO_SAMPLE')
   n=self.sample['remaining_n'];self.sample_returned_n+=n;self.sample=None;return {'returned_mol':n}
  if k=='syringe.purge':
   n=self.syringe['n'] if self.syringe else 0.;self.sample_exhaust_n+=n;self.syringe=None;return {'purged_mol':n}
  if k=='acquisition.start':
   self._fault_start_checks()
   if self.fw['mode'] in ('remote','cleaning','standby') or self.fw['recording']:raise ActionRejected('BUSY')   # in trigger mode the autosampler starts the selected program
   self._start_checks();out=self._start();trig=self.fw['mode']=='trigger';prog=self._selected() if trig else None
   self._fw_begin_run(prog if prog and prog['actions'] else None,'trigger' if trig else 'api');return out
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _stages(self):
  v=vial_target(self.sample['vial_index']);down=[v[0],v[1],.145];inlet=[.10,.04,0.];dip=[.10,.04,.065];p=self.plant
  return [('incubating',self.method['incubation_s'],self.q),('above_vial',p['travel_s'],v),('lower_vial',p['stroke_s'],down),('aspirating',p['stroke_s'],down),('retract_vial',p['stroke_s'],v),('above_inlet',p['travel_s'],inlet),('lower_inlet',p['stroke_s'],dip),('injecting',p['stroke_s'],dip),('retract_inlet',p['stroke_s'],inlet)]
 def _transition(self):
  stages=self._stages();idx=[s[0] for s in stages].index(self.workflow)
  if self.workflow=='aspirating':
   fraction=self.method['injection_m3']/self.sample['headspace_m3'];n=self.sample['remaining_n']*fraction;species={k:v*fraction for k,v in self.sample['species'].items()};self.sample['remaining_n']-=n
   for k,v in species.items():self.sample['species'][k]-=v
   self.syringe=dict(n=n,species=species)
  if self.workflow=='injecting':
   sy=self.syringe;self.fw['injections']+=1
   for sid,n in sy['species'].items():
    channels=self.templates[sid]['channels'];weights=[x['signal_volume_arb_units'][0] for x in channels];total=sum(weights)
    for ch,w in zip(channels,weights):
     mu=ch['drift_ratio']*self.method['drift_ratio_scale'];sigma=self.plant['drift_sigma_ratio'];dx=DRIFT_AXIS[1]-DRIFT_AXIS[0];cdf=lambda x:.5*(1+math.erf((x-mu)/(sigma*math.sqrt(2))));probs=[cdf(x+dx/2)-cdf(x-dx/2) for x in DRIFT_AXIS];self.bands.append(dict(n=n*w/total,eluted=0.,volume=0.,mean=source_carrier_volume(ch['retention_time_s'])*self.method['retention_scale'],sigma=max(1e-12,source_carrier_flow(ch['retention_time_s'])*self.plant['gc_sigma_s']),prob=probs))
   self.sample_exhaust_n+=sy['n']-sum(sy['species'].values());self.syringe=None
  self.workflow=stages[idx+1][0] if idx+1<len(stages) else 'separating';self.stage_elapsed=0.;self.stage_start_q=self.q[:]
 def _gas_need(self,duration,collecting):
  if self.fw.get('hp_alarm'):return 0.,0.                          # 1.3.0: overpressure switches the flow controllers off
  m=self.method;run=self.fw['run'];prof=run.get('profile') if (run and self.phase=='running') else None
  if prof:                                                         # program run: linear flow ramps of E_IMS / E_GC (manual p.59)
   t0=self.elapsed if collecting else 0.;carrier=_ramp_volume(prof['gc'],t0,t0+duration)*ML if collecting else _ramp(prof['gc'],0.)*ML*duration
   drift=_ramp_volume(prof['ims'],t0,t0+duration)*ML if collecting else _ramp(prof['ims'],0.)*ML*duration
  elif self.phase=='running':
   carrier=m['carrier_scale']*(source_carrier_volume(self.elapsed+duration)-source_carrier_volume(self.elapsed)) if collecting else m['carrier_scale']*source_carrier_flow(0)*duration;drift=m['drift_flow_m3_s']*duration
  else:
   fd,fc=self._idle_flows();carrier=fc*duration;drift=fd*duration      # Defaults window set points (standby: 10 / 5 mL/min)
  fi,fc=self._epc_factors();carrier*=fc;drift*=fi                   # the real flows when the EPC gas type setting differs from the connected gas
  n=(carrier+drift)*self.plant['gas_reference_pa']/(R*self.plant['gas_reference_k']);return n,carrier
 def _advance(self,duration,collecting):
  n,v=self._gas_need(duration,collecting);actual=duration
  if n>self.supply:
   lo,hi=0.,duration
   for _ in range(55):mid=(lo+hi)/2;need,_=self._gas_need(mid,collecting);lo,hi=(mid,hi) if need<=self.supply else (lo,mid)
   actual=lo;n,v=self._gas_need(actual,collecting)
  self.supply=max(0.,self.supply-n);self.consumed+=n;signal=np.zeros(96)
  for b in self.bands:
   b['volume']+=v;cdf=lambda x:.5*(1+math.erf((x-b['mean'])/(b['sigma']*math.sqrt(2))));f0=cdf(0);new=b['n']*max(0.,min(1.,(cdf(b['volume'])-f0)/(1-f0)));dn=max(0.,new-b['eluted']);b['eluted']=new;self.sample_exhaust_n+=dn
   if actual>0:signal+=self.plant['gain_s_per_mol']*self._ion_factor()*dn/actual*np.asarray(b['prob'])
  if actual>0:self.fw['live']=signal.tolist()
  if collecting and actual>0:self.elapsed+=actual;self.gc_volume+=v;self.signal=float(signal.sum());self.points.append(dict(time_s=self.elapsed,window_s=actual,signal=signal.tolist()))
  if actual<duration-1e-9:
   self.gas=False;self.phase='failed' if self.phase=='running' else self.phase
   if self.operation_id:self.records[self.operation_id]=self._result()
  return actual
 def step(self,dt_s):
  validate(number('s',0,10000),dt_s)
  if dt_s==0:return self.read()
  dt=float(dt_s);self.time_s+=dt;self.revision+=1
  if self.plant:
   for k,t in self.temperature.items():target=self._target_k(k);self.temperature[k]=target+(t-target)*math.exp(-dt/self.plant['thermal_tau_s'])
  remain=dt
  while remain>1e-10 and self.powered and self.gas and self.method:
   if self.phase!='running':self._advance(remain,False);break
   if self.workflow=='separating':
    duration=min(remain,max(0.,self.fw['run_end_s']-self.elapsed));used=self._advance(duration,True);remain-=used
    if self.phase=='running' and self.elapsed>=self.fw['run_end_s']-1e-8:self.phase='succeeded';self.workflow='complete';self.gas=False;self.records[self.operation_id]=self._result()
    if used<=0:break
   else:
    name,total,target=next(s for s in self._stages() if s[0]==self.workflow);duration=min(remain,max(0.,total-self.stage_elapsed));collect=self.workflow=='retract_inlet';duration=min(duration,max(0.,self.fw['run_end_s']-self.elapsed)) if collect else duration;used=self._advance(duration,collect);remain-=used;self.stage_elapsed+=used
    fraction=1. if total==0 else min(1.,self.stage_elapsed/total);self.q=[a+(b-a)*fraction for a,b in zip(self.stage_start_q,target)]
    if self.phase!='running':break
    if collect and self.elapsed>=self.fw['run_end_s']-1e-8:self.phase='succeeded';self.workflow='complete';self.gas=False;self.records[self.operation_id]=self._result();break
    if self.stage_elapsed>=total-1e-9:self._transition()
    elif used<=0:break
  if not (self.powered and self.gas):self.fw['live']=[0.]*96
  self._fw_step(dt);self._fw_close_run();return self.read()
class GasFlavourspec25gcSession:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,i):
  if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  m=export_gas_flavourspec25gc_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
 def read(self,*,instance_id,state_ids=None):
  self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
  if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
  if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
  return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='unavailable' if v[k] is None else 'estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=h.time_s,sample_window_s=[h.points[-1]['time_s']-h.points[-1]['window_s'],h.points[-1]['time_s']] if k=='detector.total_signal' and h.points else None) for k in ids})
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
   before=h.operation_id;h._caller=self._principal
   try:result=h.invoke(capability_id,**arguments)
   finally:h._caller=None
   op=result.get('operation_id')
   if op:h._owners[op]=self._principal
   elif capability_id in PANEL_CAPS and h.operation_id and h.operation_id!=before:h._owners[h.operation_id]=self._principal   # a program started on the touch screen belongs to the caller
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



def export_gas_flavourspec25gc_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():
  ref='气体绝对压力' if u=='Pa' else '以plant气体参考温度/绝对压力定义的气体体积流量' if u=='m^3/s' else None
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=ref,origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'GasFlavourspec25gcSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  unsupported=k in UNSUPPORTED;asyn=k in ('acquisition.start','program.start')
  caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.gas_flavourspec25gc.GasFlavourspec25gcSession.invoke',invocation=f'session.invoke(instance_id="gcims_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if kind in ['query','analysis'] or unsupported else [dict(description_zh='类型/身份、方法限值、闭门、温度准备、有限体积及忙碌检查；不冒充原厂安全互锁',state_ids=['power.enabled','gas.enabled','method.configured','cover.angle_rad','acquisition.phase'],enforced_by='runtime',check='atomic Runtime.invoke plus trusted-principal Session')],state_effects=[dict(state_id=x,timing='immediate',description_zh='受检事务目标/逻辑状态变化') for x in effects],observes=[],model_effects=dict(mode='none',description_zh='等待独立实际场景求值；数值过程不自动证明模型联动',binding_ids=[]),execution=dict(mode='asynchronous' if asyn else 'synchronous',time_domain='simulation',completion_criterion='采集实际时间达到run_s；残留柱物质仍记账' if asyn else '受检事务返回；read/describe/poll不推进时钟',completion_state_ids=['acquisition.phase'] if asyn else [],timeout_s=None,cancellation='supported' if asyn else 'unsupported',retry='same_request_id',resources=['host_clock','finite_headspace','finite_gas_supply','gas_exhaust','request_cache'],partial_failure_zh='已进样/已流出物质保留；取消停止气体目标，不逆向回滚；去重仅当前宿主进程'),errors=[dict(code=x,meaning_zh=x,side_effects='none') for x in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','PRECONDITION_FAILED','INITIALIZATION_ONLY','BUSY','COLUMN_RANGE','SAMPLE_COMPATIBILITY','PRESSURE_LIMIT','INVALID_CALIBRATION','SAMPLE_METHOD_REQUIRED','PREPARATION_REQUIRED','DUPLICATE_SOLUTE','UNKNOWN_REFERENCE','COLUMN_POLARITY','NO_SAMPLE','NOT_READY','SAMPLE_OR_CARRYOVER','WASTE_FULL','NOT_RUNNING','INVALID_WINDOW']+FW_ERRORS.get(k,[])+EXTRA_ERRORS.get(k,[])],example_arguments=copy.deepcopy(ex),verification=[],limitations=['教学过程；GC/IMS模板方法迁移、峰宽、离子增益与实机未校准'],source_ids=['manufacturer_primary','simulation_assumption'])
  if asyn:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='唯一由宿主step推进') for x in ['acquisition.elapsed_s','detector.total_signal','gantry.x_m','gantry.y_m','gantry.z_m','acquisition.phase']]
 mech=dict(component_ids=['vial_rack','incubator','syringe_body','gc_oven','ims_tube','gas_inlet','exhaust_port'],state_ids=list(slots),meaning_zh='有限顶空气体与载气的GC-IMS二维峰位重建；机械轴由宿主step推进',implementation='lumped',equations=['n=P*V/(R*T); finite equilibrated headspace supplied explicitly, not inferred from food matrix','Aspirated amount = remaining vial gas * injection_volume/headspace_volume; vial -> syringe -> column/exhaust; cancellation preserves each compartment','N2 required = (carrier_reference_volume+drift_reference_volume)*P_reference/(R*T_reference); gas exhaustion truncates time','Source carrier program 2mL/min first120s, linear to100mL/min at1200s, then100mL/min; integral defines synthetic retention-volume coordinate','Eluted analyte amount = injected amount * positive-truncated Gaussian CDF(carrier_volume); signal=sum(gain*dn/dt * IMS-bin Gaussian probability)','IMS axis is measured t_d/t_RIP dimensionless; widths/gain/method transfer assumed; source QH1 monomer/dimer relative volumes used only as explicit matrix-dependent assumed channel weights','ROI area from observed signal windows; external standards area=slope*concentration+intercept; no hidden composition read by analyzer','Fingerprint cosine similarity uses observed integrated20x96 bins; no direct identity inference','Tnew=Tset+(Told-Tset)*exp(-dt/tau); single-node thermal surrogate; PAL stages use explicit engineering travel durations','EPC gas type: Q_real=Q_set*eta(set gas)/eta(connected gas) per EPC (inlet pressure held for the set gas, laminar flow); 1 when they match (default nitrogen)'],parameters={},source_ids=['manufacturer_primary','gc_ims_literature','simulation_assumption'],limitations=['77 distinct CAS with80 empirical peaks from one food-matrix study on earlier FlavourSpec; no raw electronic 2D response arrays.','GC/IMS max100C and syringe max200C from FlavourSpec25; incubator bound and travel/thermal parameters are engineering assumptions.','Host supplies an already-equilibrated finite headspace; no matrix partition kinetics, pressure regulation or actual vial opening.','Source drift coordinates are dimensionless RIP ratios; absolute drift seconds, mobility and reduced mobility are unknown.','Signal widths/gain, source monomer/dimer weights and method transfer are explicit synthetic assumptions; no ion reaction chemistry/noise/saturation model.','Concentrations require caller-supplied calibration; source food peak volumes are not pure standards.','Mass and geometry motions verified only within the implemented surrogate; robot manipulation/hardware not_run.','Trusted-host Session filtering is not filesystem or process isolation.'])
 spec=importlib.util.spec_from_file_location('_ims_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'gc_ims':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'。'
 slots['detector.total_signal']['sampling']=dict(mode='interval_mean',time_domain='simulation',window_description='最近实际GC时间窗口内96个漂移通道信号和；停止后保持')
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'gc_ims':mech})

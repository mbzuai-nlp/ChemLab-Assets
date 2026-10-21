"""CHEM-114 finite-gas dual-direction pycnometry with temperature-aware pressure inversion."""
from pathlib import Path
import copy,math,json,importlib.util,hashlib,os,re,tempfile
PROTOCOL_VERSION='1.0.0';SLUG='antonpaar_ultrapyc5000';CATALOG_ID='CHEM-114';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch03/t4_measure'/SLUG
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
  if not isinstance(v,dict):raise ActionRejected('INVALID_ARGUMENT')
  if 'properties' not in s:return                                         # a free-form object (e.g. the structured panel frame)
  if not set(s.get('required',[]))<=v.keys() or set(v)-set(s['properties']):raise ActionRejected('INVALID_ARGUMENT')
  for k,x in v.items():validate(s['properties'][k],x)
 elif t=='array':
  if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
  for x in v:validate(s['items'],x)
 if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')
DT=.1;R=8.314462618;CV=[1.5*R,2.5*R,1.5*R];GASES=['He','N2','Ar'];CELLS={'large':135e-6,'medium':50e-6,'small':10e-6,'micro':4.5e-6,'meso':1.8e-6,'nano':.25e-6}
PLANT=obj({'ambient_k':number('K',283.15,308.15),'ambient_pa':number('Pa',80000,110000),'initial_temperature_k':number('K',276.15,323.15),'source_gas':enum(GASES),'source_volume_m3':number('m^3',.00001,.1),'source_gauge_pa':number('Pa',1000,207000),'regulator_gauge_pa':number('Pa',1000,207000),'fill_conductance_mol_s_pa':number('mol/(s*Pa)',1e-11,1e-6),'connect_conductance_mol_s_pa':number('mol/(s*Pa)',1e-11,1e-6),'vent_conductance_mol_s_pa':number('mol/(s*Pa)',1e-11,1e-6),'leak_conductance_mol_s_pa':number('mol/(s*Pa)',0,1e-7),'heat_capacity_j_k':number('J/K',100,10000),'heat_loss_w_k':number('W/K',.01,10),'thermal_power_w':number('W',1,60),'pressure_gain':arr(number('1',.8,1.2),2),'pressure_offset_pa':arr(number('Pa',-1000,1000),2),'cell_volume_factor':number('1',.95,1.05),'reference_volume_factor':number('1',.95,1.05),'pump_available':BOOL,'pump_floor_pa':number('Pa',10,10000),'accept_engineering_model':BOOL})
PLANT_EXAMPLE=dict(ambient_k=298.15,ambient_pa=101325.,initial_temperature_k=298.15,source_gas='He',source_volume_m3=.002,source_gauge_pa=207000.,regulator_gauge_pa=207000.,fill_conductance_mol_s_pa=2e-8,connect_conductance_mol_s_pa=2e-8,vent_conductance_mol_s_pa=3e-8,leak_conductance_mol_s_pa=0.,heat_capacity_j_k=1200.,heat_loss_w_k=1.,thermal_power_w=35.,pressure_gain=[1.,1.],pressure_offset_pa=[0.,0.],cell_volume_factor=1.,reference_volume_factor=1.,pump_available=True,pump_floor_pa=500.,accept_engineering_model=True)
METHOD=obj({'cell':enum(list(CELLS)),'reference':enum(['auto','small','large']),'direction':enum(['sample_first','powder_protect']),'preparation':enum(['none','flow','pulse','vacuum']),'flow_m3_s':number('m^3/s',1e-8,1e-5),'preparation_s':number('s',1,59940),'purge_pulses':dict(number('1',3,999),type='integer'),'target_gauge_pa':number('Pa',10000,190000),'max_runs':dict(number('1',1,99),type='integer'),'runs_to_average':dict(number('1',1,99),type='integer'),'automatic':BOOL,'finish_rsd':number('1',.000025,.001),'stage_timeout_s':number('s',10,7200),'equilibration_s':number('s',.1,60),'mode':enum(['measure','calibration','leak']),'leak_hold_s':number('s',1,1200),'leak_limit_pa_s':number('Pa/s',.001,100)})
METHOD_EXAMPLE=dict(cell='medium',reference='auto',direction='powder_protect',preparation='none',flow_m3_s=1e-6,preparation_s=60.,purge_pulses=3,target_gauge_pa=100000.,max_runs=3,runs_to_average=3,automatic=False,finish_rsd=.0001,stage_timeout_s=600.,equilibration_s=.5,mode='measure',leak_hold_s=20.,leak_limit_pa_s=1.)
SAMPLE=obj({'sample_id':TEXT,'reference_id':TEXT,'true_mass_kg':number('kg',1e-7,3.),'closed_pore_m3':number('m^3',0,.00013),'open_pore_m3':number('m^3',0,.00013),'reference_temperature_k':number('K',276.15,323.15),'volume_expansion_per_k':number('1/K',0,.001),'accept_reference_transfer':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='private_al_powder',reference_id='nist_al',true_mass_kg=.05,closed_pore_m3=0.,open_pore_m3=10e-6,reference_temperature_k=298.15,volume_expansion_per_k=0.,accept_reference_transfer=True)
CAL=obj({'blank_ratio':number('1',.0001,10000),'sphere_ratio':number('1',.0001,10000),'sphere_volume_m3':number('m^3',1e-9,.00013)})
CAL_EXAMPLE=dict(blank_ratio=1.,sphere_ratio=.8,sphere_volume_m3=1e-5)
PCAL=obj({'reference_pa':arr(number('Pa',1000,320000),2),'sample_measured_pa':arr(number('Pa',0,400000),2),'reference_measured_pa':arr(number('Pa',0,400000),2)})
PCAL_EXAMPLE=dict(reference_pa=[101325.,201325.],sample_measured_pa=[101325.,201325.],reference_measured_pa=[101325.,201325.])
TRACE=obj({'time_s':number('s',0),'stage':TEXT,'sample_pa':number('Pa',0),'reference_pa':number('Pa',0),'temperature_k':number('K',200,400)})
RUN=obj({'index':dict(number('1',1),type='integer'),'initial_high_pa':number('Pa',0),'initial_low_pa':number('Pa',0),'final_pa':number('Pa',0),'initial_k':number('K',200,400),'final_k':number('K',200,400),'ratio':number('1',0),'volume_m3':nullable(number('m^3')),'density_kg_m3':nullable(number('kg/m^3'))})
RESULT=obj({'phase':PHASE,'stage':TEXT,'elapsed_s':number('s',0),'runs':arr(RUN),'trace':arr(TRACE),'mean_volume_m3':nullable(number('m^3')),'mean_density_kg_m3':nullable(number('kg/m^3')),'standard_deviation_m3':nullable(number('m^3',0)),'rsd':nullable(number('1',0)),'leak_rate_pa_s':nullable(number('Pa/s',0)),'leak_pass':nullable(BOOL),'quality':TEXT,'template_source':TEXT,'warnings':arr(TEXT)})
LEDGER=obj({'source_mol':number('mol',0),'sample_chamber_mol':arr(number('mol',0),3),'reference_chamber_mol':arr(number('mol',0),3),'vented_mol':arr(number('mol',0),3),'evacuated_mol':arr(number('mol',0),3),'ambient_in_mol':arr(number('mol',0),3),'species_residual_mol':arr(number('mol'),3),'gas_consumed_mol':number('mol',0),'current_sample_mass_kg':number('kg',0),'returned_sample_mass_kg':number('kg',0),'sample_residual_kg':number('kg'),'skeleton_volume_m3':number('m^3',0),'free_volume_m3':number('m^3',0),'open_pore_m3':number('m^3',0),'energy_residual_j':number('J'),'heater_energy_j':number('J'),'ambient_loss_j':number('J'),'boundary_enthalpy_j':number('J'),'loading_work_j':number('J'),'source_pressure_pa':number('Pa',0)})
CAPS={};SLOTS={};SLOT_ZH={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},effects or [])
def slot(k,s,u,z,o='derived',v='agent',c='controller',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller','thermal_block','reference_large','reference_small'],'初始化有限惰性气源、空腔偏差、热参数和压力偏置','configuration','evaluator',PLANT_EXAMPLE,['plant.configured'])
cap('method.configure',METHOD,obj({'configured':BOOL}),['controller'],'双向膨胀、空腔与参考腔、预处理和重复方法','configuration',ex=METHOD_EXAMPLE,effects=['method.configured'])
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['controller','power_switch'],'电源；断电停止供气、后续step泄压并保留已完成结果',ex={'enabled':True},effects=['power.enabled','power.angle_rad'])
for k,part,arg,hi,u in [('lid','lid_lift','lift_m',.13,'m'),('lock','lower_ring','angle_rad',1.1,'rad'),('seal','upper_ring','angle_rad',1.4,'rad'),('cup','sample_cup','lift_m',.12,'m')]:
 cap(k+'.set',obj({arg:number(u,0,hi)}),obj({arg:number(u,0,hi)}),[part],'双旋盖和杯的受检人工动作；真实机器人不实现',ex={arg:0.},effects=[k+'.'+arg]);slot(k+'.'+arg,number(u,0,hi),u,'密封盖、旋环或50cm3杯位置','scene',c=part,w=(k+'.set',arg))
cap('panel.home',obj({}),obj({'page':TEXT}),['home_button','display_digits'],'Home触点；后续step释放短行程',effects=['panel.page','home.stroke_m'])
cap('thermal.set_temperature',obj({'temperature_k':number('K',276.15,323.15)}),obj({'temperature_k':number('K',276.15,323.15)}),['thermal_block','peltier'],'仅写温控目标，升降温由step推进',ex={'temperature_k':298.15},effects=['thermal.target_k'])
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_cup'],'可信宿主装入有限固体；样品和名义密度迁移需显式接受','configuration','evaluator',SAMPLE_EXAMPLE,['sample.loaded'])
cap('sample.unload',obj({}),obj({'returned_mass_kg':number('kg',0)}),['sample_cup'],'可信宿主领回固体；不自动恢复气源',vis='evaluator',effects=['sample.loaded'])
cap('sample.set_mass',obj({'mass_kg':number('kg',1e-7,3.)}),obj({'mass_kg':number('kg',1e-7,3.)}),['controller'],'输入独立称量值，不修改样品真实质量','configuration',ex={'mass_kg':.05},effects=['sample.entered_mass_kg'])
cap('pressure.calibrate',PCAL,obj({'calibrated':BOOL}),['controller'],'两个压力传感器各两点线性标定，测量输入由宿主提供','configuration',ex=PCAL_EXAMPLE,effects=['pressure.calibrated'])
cap('volume.calibrate',CAL,obj({'cell_volume_m3':number('m^3',0),'reference_volume_m3':number('m^3',0)}),['sample_well','reference_large','reference_small'],'按空杯和已知球的实际压力比反演两腔体积','configuration',ex=CAL_EXAMPLE,effects=['volume.calibrated'])
cap('acquisition.start',obj({}),obj({'operation_id':TEXT,'phase':PHASE}),['controller','valve_0','valve_1','thermal_block'],'温度稳定→预处理→有限充气→膨胀→压力比反演→泄压和重复',effects=['acquisition.phase','acquisition.stage'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['controller','valve_0','valve_1'],'取消保留已消耗气体/已完成结果，泄压需step',effects=['acquisition.phase'])
cap('process.measure',obj({}),RESULT,['controller','display_digits'],'只读压力和温度曲线、体积/密度及重复性','query')
cap('ledger.read',obj({}),LEDGER,['controller'],'三种惰性气体、有限固体与集总能量账本','query','evaluator')
cap('template.reference',obj({'substance_id':TEXT}),obj({'name':TEXT,'density_kg_m3':number('kg/m^3',0),'evidence':TEXT,'template_source':TEXT,'limitations':TEXT}),['controller'],'只读密度来源及名义/实测性质','query',ex={'substance_id':'nist_al'})
UNSUPPORTED={'hardware.connect','foam.open_cell_method','sample.adsorption','fault.probability_model','gas.real_eos','robot.load'}
for k in sorted(UNSUPPORTED):cap(k,obj({}),obj({}),['controller'],'该功能未实现')
for k in ['plant','method','pressure','volume']:slot(k+('.configured' if k in ['plant','method'] else '.calibrated'),BOOL,'1',k+'配置/校准有效状态','setpoint')
slot('power.enabled',BOOL,'1','逻辑电源','setpoint',w=('power.set','enabled'));slot('power.angle_rad',number('rad',0,.2),'rad','后部电源开关角度','scene',c='power_switch');slot('home.stroke_m',number('m',0,.0015),'m','Home按钮按下深度','scene',c='home_button');slot('panel.page',TEXT,'1','本地界面页面，不复刻OEM软件')
slot('sample.loaded',BOOL,'1','有限固体已装入');slot('sample.entered_mass_kg',nullable(number('kg',0)),'kg','操作者输入质量，与真实质量独立','setpoint',w=('sample.set_mass','mass_kg'));slot('sample.identity',TEXT,'1','真实样品身份','process_truth','evaluator');slot('sample.true_mass_kg',number('kg',0),'kg','真实固体质量，不从输入称量值推定','process_truth','evaluator')
slot('thermal.target_k',number('K',276.15,323.15),'K','Peltier温控目标；±0.02C规格未经验验证','setpoint',w=('thermal.set_temperature','temperature_k'));slot('thermal.indicated_k',number('K',200,400),'K','共温区理想温度读数','sensor');slot('thermal.power_w',number('W',-60,60),'W','工程冷热功率，负为制冷','derived');slot('thermal.truth_k',number('K',200,400),'K','集总块和气体共同温度','process_truth','evaluator')
for k in ['sample','reference']:slot('pressure.'+k+'_pa',number('Pa',0),'Pa','校正后'+k+'腔绝对压力','sensor')
slot('acquisition.phase',PHASE,'1','异步状态');slot('acquisition.stage',TEXT,'1','热平衡/预处理/膨胀阶段');slot('acquisition.elapsed_s',number('s',0),'s','当前操作已推进时间');slot('acquisition.failure',TEXT,'1','公开失败码')
for k,u in [('volume_m3','m^3'),('density_kg_m3','kg/m^3'),('rsd','1')]:slot('result.'+k,nullable(number(u)),u,'压力反演得到的结果，不是直接读取密度真值')
for k in ['pressure','temperature','volume','density']:slot('display.'+k+'_text',TEXT,'1','屏幕字符：pressure kPa/temperature C/volume cm3/density g/cm3；关机空白、无结果横线','derived',c='display_digits')
# ---------------------------------------------------------------- front panel and its device layer (7 in touch screen; panel_ui.json beside the contract)
# Approximate: no Ultrapyc 5000 manual is saved.  The menu functions follow the predecessor Ultrapyc 1200e operating manual ('up p.N'):
# Run (Run Parameters, Sample Parameters, Start), Calibrate (cell), System Settings, User ID, Shutdown; the overview screen follows the
# brochure ('bro p.3': temperature, pressure, valve status, preliminary results).
ASSET_VERSION='1.2.0';SHUTDOWN_S=60.
UP_LANGUAGES=['English','German','French','Spanish','Italian']   # 6-Change Languages (up p.31): the manual names no list; assumed, texts stay English
UP_SETTINGS=dict(sample_id=dict(type='string',maxLength=16),user_id=dict(type='string',maxLength=16),pressure_unit=enum(['kPa','psig']),gas_name=dict(type='string',maxLength=16),
                 number_reports=BOOL,language=enum(UP_LANGUAGES))
UP_DEFAULTS=dict(sample_id='',user_id='',pressure_unit='kPa',gas_name='Helium',number_reports=True,language='English')
cap('settings.configure',obj({'settings':dict(type='object',properties=UP_SETTINGS,required=[],additionalProperties=False)}),obj({'settings':{'type':'object'}}),['controller','display_digits'],
    '本机设置：样品ID、用户ID、压力单位、气体名称、报告编号、语言（1200e菜单；语言选择保存、显示并写入报告，文字仍为英文）','configuration',ex={'settings':{'sample_id':'AL-1'}},effects=['device.settings'])
cap('calibration.run',obj({'kind':enum(['blank','sphere'])}),obj({'operation_id':TEXT,'phase':PHASE}),['controller','valve_0','valve_1','sample_well'],
    '腔体标定的一次运行（空杯或标定球）：以calibration模式运行当前方法，结束后保存平均压力比',ex={'kind':'blank'},
    effects=['acquisition.phase','acquisition.stage','device.calibration','panel.page'])
cap('calibration.apply',obj({'sphere_volume_m3':number('m^3',1e-9,.00013)}),obj({'cell_volume_m3':number('m^3',0),'reference_volume_m3':number('m^3',0)}),['controller','sample_well','reference_large','reference_small'],
    '由保存的空杯与标定球压力比和球体积计算样品腔与参考腔体积（volume.calibrate）','configuration',ex={'sphere_volume_m3':1e-5},effects=['volume.calibrated','device.calibration'])
cap('leak_check.start',obj({}),obj({'operation_id':TEXT,'phase':PHASE}),['controller','valve_0','valve_1'],'泄漏检查（1200e System Settings）：以leak模式运行，结果写入报告',
    effects=['acquisition.phase','acquisition.stage','panel.page'])
cap('reports.delete',obj({}),obj({'deleted':dict(number('1',0),type='integer')}),['controller'],'删除本机保存的报告（1200e Delete Reports）','configuration',effects=['device.reports'])
cap('system.shutdown',obj({}),obj({'shutdown':BOOL}),['controller'],'关机程序（1200e 8-Shutdown）：操作系统约1分钟关闭后再关电源开关',effects=['device.status'])
# Error codes of the 1200e (up p.73-75, Table G-1): the run stops, the display alternates 'Error n occurred' with the message and with 'Restart: Press
# clear'; CLEAR acknowledges.  13 (network), 14 / 15 (serial port / balance time-out) need hardware absent from the simulated configuration.
ERROR_CODES={1:'Pressure unstable (system isolated)',2:'Low Pressure',3:'Pressure unstable (VA isolated)',4:'Pressure unstable (VA in)',5:'Pressure A # Pressure B',
             6:'No pressure for purge',7:'Pressure unstable (open to ambient)',8:'Clock failure',9:'Invalid volume',10:'Divide by zero error',11:'Hardware error',
             12:'Calculation error',17:'Calibration file error'}
cap('error.clear',obj({}),obj({'cleared':BOOL}),['controller','display_digits'],'CLEAR确认显示的错误码（1200e p.73-74：Restart: Press clear）；原因未消除时下次运行再次报同一错误',effects=['device.status'])
cap('fault.inject',obj({'code':dict(type='integer',enum=sorted(ERROR_CODES),**{'x-unit':'1'})}),obj({'code':dict(type='integer',**{'x-unit':'1'}),'phase':PHASE}),['controller','display_digits'],
    '评测者注入1200e说明书表G-1的错误码（原因：传感器不稳、泄漏、样品杯未密封、低供气压等）：运行中停止测量循环并显示Error n occurred及描述，确认前不能再启动；Error 8（时钟）只记入报告，不影响结果','simulation','evaluator',{'code':2},['device.status','acquisition.phase','fault.cause'])
cap('fault.clear',obj({}),obj({'cleared':BOOL}),['controller'],'评测者消除注入的错误原因（如重新密封样品杯、修复泄漏、接通气源）；显示的错误仍须CLEAR确认','simulation','evaluator',{},['fault.cause'])
DEVICE_CAPS=('settings.configure','calibration.run','calibration.apply','leak_check.start','reports.delete','system.shutdown','error.clear')
for _k,_s,_z in [('device.settings',{'type':'object'},'本机设置'),('device.reports',{'type':'array','items':{'type':'object'}},'本机报告（每次运行：类型、报告文件名、样品ID、用户、体积、密度、RSD、泄漏）'),
                 ('device.calibration',{'type':'object'},'腔体标定向导：空杯与标定球的平均压力比'),('device.status',{'type':'object'},'关机状态与正在进行的本机程序'),
                 ('method.settings',nullable({'type':'object'}),'当前方法参数（运行参数页面读取）')]:slot(_k,_s,'1',_z,'setpoint' if _k=='device.settings' else 'derived')
slot('fault.cause',nullable(dict(type='integer',**{'x-unit':'1'})),'1','评测者注入且未消除的错误原因（1200e错误码），无为null','process_truth','evaluator')
VALVES={'idle':'all closed','thermal':'vent open','baseline':'vent open','between_runs':'vent open','venting':'vent open','pulse_vent':'vent open','flow':'gas in + vent open',
        'vacuum':'vacuum open','pulse_fill':'gas in open','fill':'gas in open','leak_fill':'gas in open','expand':'expansion open','leak_hold':'all closed'}
_ENG=None
def _engine():
 global _ENG
 if _ENG is None:
  try:
   from . import _panel_ui as e
  except ImportError:
   import sys
   if '_chemsim_panel_ui' not in sys.modules:
    sp=importlib.util.spec_from_file_location('_chemsim_panel_ui',Path(__file__).with_name('_panel_ui.py'));m=importlib.util.module_from_spec(sp);sys.modules['_chemsim_panel_ui']=m;sp.loader.exec_module(m)
   e=sys.modules['_chemsim_panel_ui']
  _ENG=e
 return _ENG
class AntonpaarUltrapyc5000Runtime:
 def __init__(self,instance_id='ultrapyc5000_01'):
  self.instance_id=instance_id;self.time_s=0.;self.pending=0.;self.revision=0;self.plant=None;self.method=None;self.powered=False;self.lid=0.;self.lock=1.1;self.seal=1.4;self.cup=0.;self.home=0.;self.home_ticks=0;self.page='home';self.sample=None;self.mass_input=None;self.run_mass=None;self.mass_loaded=0.;self.mass_returned=0.;self.temp=298.15;self.target=298.15;self.power_w=0.;self.cell=50e-6;self.ref=50e-6;self.ns=[0.,0.,0.];self.nr=[0.,0.,0.];self.source=0.;self.source_initial=0.;self.initial_gas=[0.,0.,0.];self.ambient_in=[0.,0.,0.];self.vented=[0.,0.,0.];self.evacuated=[0.,0.,0.];self.cal_pressure=None;self.cal_volume=None;self.heat=0.;self.loss=0.;self.enthalpy=0.;self.loading_work=0.;self.energy0=0.;self.phase='idle';self.stage='idle';self.elapsed=0.;self.stage_time=0.;self.stable_time=0.;self.trace=[];self.runs=[];self.initial_point=None;self.pulses=0;self.leak_rate=None;self.leak_pass=None;self.failure='';self.serial=0;self.operation_id=None;self.records={};self._principals={};self._owners={};self._requests={};self.library=json.loads((ASSET/'density_library.json').read_text())
  self.dev=self._new_dev();self.panel_state=None;self.panel_capture_dir=None   # touch-screen device layer (plain data) and the panel UI state
  self.fault=None;self._panel_session=None   # evaluator-injected error cause (1200e code); the session whose panel gesture runs (its caller rules apply)
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ['agent','debug','evaluator'] or principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return AntonpaarUltrapyc5000Session(self,principal_id)
 def describe(self):return export_antonpaar_ultrapyc5000_contract_metadata()
 def skeleton(self):
  if not self.sample:return 0.
  s=self.sample;v=s['true_mass_kg']/self.library['substances'][s['reference_id']]['density_kg_m3'];return v*(1+s['volume_expansion_per_k']*(self.temp-s['reference_temperature_k']))+s['closed_pore_m3']
 def volumes(self):return [self.cell-self.skeleton(),self.ref]
 def pressure(self,i):
  if not self.plant:return 101325.
  return sum(self.ns if i==0 else self.nr)*R*self.temp/self.volumes()[i]
 def _reading_of(self,i,p):
  """What transducer i shows for the absolute pressure p: the plant gain / offset (evaluator errors, default ideal), then the stored pressure calibration."""
  value=p*self.plant['pressure_gain'][i]+self.plant['pressure_offset_pa'][i]
  if self.cal_pressure:value=(value-self.cal_pressure[i][1])/self.cal_pressure[i][0]
  return value
 def indicated_p(self,i):
  if not self.plant:return 101325.
  return self._reading_of(i,self.pressure(i))
 def gauge_p(self,i):
  """The controller's pressure: transducer i reset to zero at ambient before each run (up p.11: 'The transducer is automatically reset to zero
  prior to each run'; p.28: the pressure above ambient), so a static offset cancels and the gain and the stored calibration slope act."""
  return self.indicated_p(i)-self._reading_of(i,self.plant['ambient_pa'])
 def zeroed_p(self,i):
  """The pressure the volume and leak calculations use: the same zeroed gauge reading as the controller (up p.11; p.94: 'Pa is made to read
  zero, all pressure measurements are relative to Pa', eq. I.10), referred to the ambient pressure for the temperature-aware inversion.  Ideal
  sensors (gain 1, offset 0, no calibration): the true pressure, as before; a static offset cancels as it does for the control."""
  return self.gauge_p(i)+self.plant['ambient_pa']
 def _true_for_gauge(self,i,g):
  """The absolute pressure at which transducer i shows the gauge reading g (where the controller's set point lies physically)."""
  pa=self.plant['ambient_pa'];k=self.plant['pressure_gain'][i]/(self.cal_pressure[i][0] if self.cal_pressure else 1.)
  return pa+g/k
 def energy(self):return ((self.plant['heat_capacity_j_k'] if self.plant else 1200.)+sum((a+b)*cv for a,b,cv in zip(self.ns,self.nr,CV)))*self.temp
 def _stats(self):
  rows=[r for r in self.runs if r['volume_m3'] is not None]
  if not rows:return None,None,None,None
  rows=rows[-self.method['runs_to_average']:];vs=[r['volume_m3'] for r in rows];mean=sum(vs)/len(vs);sd=math.sqrt(sum((v-mean)**2 for v in vs)/(len(vs)-1)) if len(vs)>1 else None;rho=self.run_mass/mean if self.run_mass is not None and mean>0 else None;return mean,rho,sd,sd/mean if sd is not None and mean>0 else None
 def read(self,visibility='agent'):
  v,r,sd,rsd=self._stats();d={'plant.configured':bool(self.plant),'method.configured':bool(self.method),'pressure.calibrated':self.cal_pressure is not None,'volume.calibrated':self.cal_volume is not None,'power.enabled':self.powered,'power.angle_rad':.2 if self.powered else 0.,'lid.lift_m':self.lid,'lock.angle_rad':self.lock,'seal.angle_rad':self.seal,'cup.lift_m':self.cup,'home.stroke_m':self.home,'panel.page':self.page,'sample.loaded':bool(self.sample),'sample.entered_mass_kg':self.mass_input,'sample.identity':self.sample['sample_id'] if self.sample else '', 'sample.true_mass_kg':self.sample['true_mass_kg'] if self.sample else 0.,'thermal.target_k':self.target,'thermal.indicated_k':self.temp,'thermal.truth_k':self.temp,'thermal.power_w':self.power_w,'pressure.sample_pa':self.indicated_p(0),'pressure.reference_pa':self.indicated_p(1),'acquisition.phase':self.phase,'acquisition.stage':self.stage,'acquisition.elapsed_s':self.elapsed,'acquisition.failure':self.failure,'result.volume_m3':v,'result.density_kg_m3':r,'result.rsd':rsd};d.update({'display.pressure_text':f'{self.indicated_p(0)/1000:.3f}' if self.powered else '', 'display.temperature_text':f'{self.temp-273.15:.2f}' if self.powered else '', 'display.volume_text':('---' if v is None else f'{v*1e6:.3f}') if self.powered else '', 'display.density_text':('---' if r is None else f'{r/1000:.4f}') if self.powered else ''});d.update(self._dev_values());return copy.deepcopy({k:v for k,v in d.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _result(self):
  v,r,sd,rsd=self._stats();return dict(phase=self.phase,stage=self.stage,elapsed_s=self.elapsed,runs=copy.deepcopy(self.runs),trace=copy.deepcopy(self.trace),mean_volume_m3=v,mean_density_kg_m3=r,standard_deviation_m3=sd,rsd=rsd,leak_rate_pa_s=self.leak_rate,leak_pass=self.leak_pass,quality='engineering_gas_law_inversion_no_empirical_noise',template_source='NIST XrayMassCoef Table1 nominal solids or J.Mater.Res.Technol doi10.1016/j.jmrt.2026.06.063 Table1; source selection private to host',warnings=['Nominal NIST densities are not measured pycnometry standards; temperature transfer explicitly assumed','Open pores assumed gas-accessible; closed pores are displaced with solid; no real adsorption/permeation kinetics','Repeatability from modeled cycles does not validate manufacturer precision; literature SD not used as noise','Cancellation retains gas consumption; finite venting continues only on step','Only medium50cm3 cup is articulated; other inserts static visual accessories'])
 def _ledger(self):
  p=self.plant;idx=GASES.index(p['source_gas']) if p else 0;remaining=[a+b+(self.source if j==idx else 0.) for j,(a,b) in enumerate(zip(self.ns,self.nr))];res=[self.initial_gas[j]+self.ambient_in[j]+(self.source_initial if j==idx else 0.)-remaining[j]-self.vented[j]-self.evacuated[j] for j in range(3)];mass=self.sample['true_mass_kg'] if self.sample else 0.;return dict(source_mol=self.source,sample_chamber_mol=self.ns.copy(),reference_chamber_mol=self.nr.copy(),vented_mol=self.vented.copy(),evacuated_mol=self.evacuated.copy(),ambient_in_mol=self.ambient_in.copy(),species_residual_mol=res,gas_consumed_mol=self.source_initial-self.source,current_sample_mass_kg=mass,returned_sample_mass_kg=self.mass_returned,sample_residual_kg=self.mass_loaded-self.mass_returned-mass,skeleton_volume_m3=self.skeleton(),free_volume_m3=self.volumes()[0],open_pore_m3=self.sample['open_pore_m3'] if self.sample else 0.,energy_residual_j=self.energy()-self.energy0-self.enthalpy-self.heat+self.loss-self.loading_work if p else 0.,heater_energy_j=self.heat,ambient_loss_j=self.loss,boundary_enthalpy_j=self.enthalpy,loading_work_j=self.loading_work,source_pressure_pa=self.source*R*p['ambient_k']/p['source_volume_m3'] if p else 0.)
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[k][0],a);before=copy.deepcopy({x:v for x,v in self.__dict__.items() if x not in ('library','_panel_session')})
  try:
   result=self._invoke(k,a);validate(CAPS[k][1],result)
   if CAPS[k][4] not in ['query','analysis']:self.revision+=1
   return result
  except Exception:
   lib=self.library;ps=self.__dict__.get('_panel_session');self.__dict__.clear();self.__dict__.update(before);self.library=lib;self._panel_session=ps;raise
 def _reset_ambient(self):
  olde=self.energy();oldh=self.enthalpy;p=self.plant
  for i,n in enumerate([self.ns,self.nr]):
   for j,x in enumerate(n):self.vented[j]+=x;self.enthalpy-=x*(CV[j]+R)*self.temp
   new=p['ambient_pa']*self.volumes()[i]/(R*self.temp);n[:]=[0.,new,0.];self.ambient_in[1]+=new;self.enthalpy+=new*(CV[1]+R)*p['ambient_k']
  self.loading_work+=self.energy()-olde-(self.enthalpy-oldh)
 def _invoke(self,k,a):
  if k=='process.measure':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='template.reference':
   r=self.library['substances'].get(a['substance_id'])
   if r is None:raise ActionRejected('UNKNOWN_REFERENCE')
   return {**{x:r[x] for x in ['name','density_kg_m3','evidence','limitations']},'template_source':r['source']['url']}
  if k=='plant.configure':
   if self.plant or self.powered or not a['accept_engineering_model']:raise ActionRejected('INITIALIZATION_ONLY')
   if a['regulator_gauge_pa']>a['source_gauge_pa']:raise ActionRejected('INVALID_ARGUMENT')
   self.plant=copy.deepcopy(a);self.temp=a['initial_temperature_k'];self.target=self.temp;self.cell*=a['cell_volume_factor'];self.ref*=a['reference_volume_factor'];self.source=(a['ambient_pa']+a['source_gauge_pa'])*a['source_volume_m3']/(R*a['ambient_k']);self.source_initial=self.source
   self.ns=[0.,a['ambient_pa']*self.cell/(R*self.temp),0.];self.nr=[0.,a['ambient_pa']*self.ref/(R*self.temp),0.];self.initial_gas=[0.,self.ns[1]+self.nr[1],0.];self.energy0=self.energy();return {'configured':True}
  if k=='fault.inject':
   self.fault=a['code']
   if self.powered:self._raise_error(a['code'])
   return {'code':a['code'],'phase':self.phase}
  if k=='fault.clear':cleared=self.fault is not None;self.fault=None;return {'cleared':cleared}
  if k in DEVICE_CAPS:return self._device_invoke(k,a)
  if k in PANEL_CAPS:return self._panel_command(k,a)
  if k=='panel.home':self.home=.0015;self.home_ticks=2;self.page='home';return {'page':self.page}
  if k=='power.set':
   if not a['enabled'] and self.phase=='running':self._end('cancelled')
   if a['enabled'] and not self.powered:self.dev['power_epoch']+=1;self.dev['shutdown']=None;self.dev['error']=None   # a power cycle restarts without the shown error
   self.powered=a['enabled'];return a
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self._end('cancelled');return {'phase':self.phase}
  if self.phase=='running':raise ActionRejected('BUSY')
  if k in ['lid.set','lock.set','seal.set','cup.set']:
   if self.plant and max(abs(self.gauge_p(i)) for i in [0,1])>30.:raise ActionRejected('PRESSURE_INTERLOCK')   # the device's own pressure reading
   n=k.split('.')[0];v=next(iter(a.values()))
   if n=='lid' and (self.lock>.01 or self.seal>.01 or (v<.1 and self.cup>.005)):raise ActionRejected('LID_INTERLOCK')
   if n=='lock' and (self.lid>.001 or self.seal>.01):raise ActionRejected('LOCK_SEQUENCE')
   if n=='seal' and (self.lid>.001 or (v>.01 and self.lock<1.09)):raise ActionRejected('LOCK_SEQUENCE')
   if n=='cup' and self.lid<.11:raise ActionRejected('LID_INTERLOCK')
   setattr(self,n,v);return a
  if k=='thermal.set_temperature':
   if not self.plant:raise ActionRejected('NOT_CONFIGURED')
   self.target=a['temperature_k'];return a
  if k=='method.configure':
   if not self.plant or a['runs_to_average']>a['max_runs']:raise ActionRejected('INVALID_ARGUMENT')
   if a['preparation']=='vacuum' and a['preparation_s']<60.:raise ActionRejected('INVALID_ARGUMENT')
   cv=CELLS[a['cell']];rv=8e-6 if a['reference']=='small' or (a['reference']=='auto' and cv<=10e-6) else 50e-6
   volume_changed=abs(cv*self.plant['cell_volume_factor']-self.cell)>1e-12 or abs(rv*self.plant['reference_volume_factor']-self.ref)>1e-12
   if self.sample and volume_changed:raise ActionRejected('REMOVE_SAMPLE_FIRST')
   if self.method and volume_changed and (self.lid<.11 or self.cup<.10 or max(abs(self.gauge_p(i)) for i in [0,1])>30.):raise ActionRejected('INSERT_ACCESS')
   changed=self.method is None or any(self.method.get(x)!=a[x] for x in ['cell','reference','direction']);self.method=copy.deepcopy(a);self.cell=cv*self.plant['cell_volume_factor'];self.ref=rv*self.plant['reference_volume_factor']
   if changed:self.cal_volume=None
   if volume_changed:self._reset_ambient()
   return {'configured':True}
  if k=='sample.load':
   if not self.plant or not self.method or self.sample or self.lid<.11 or self.cup<.10 or not a['accept_reference_transfer']:raise ActionRejected('LOAD_PRECONDITION')
   if a['reference_id'] not in self.library['substances']:raise ActionRejected('UNKNOWN_REFERENCE')
   self.sample=copy.deepcopy(a)
   if self.skeleton()+a['open_pore_m3']>=self.cell*.98:raise ActionRejected('CELL_CAPACITY')
   self.mass_loaded+=a['true_mass_kg'];self.mass_input=None;self._reset_ambient();return {'loaded':True}
  if k=='sample.unload':
   if not self.sample or self.lid<.11 or self.cup<.10:raise ActionRejected('LOAD_PRECONDITION')
   mass=self.sample['true_mass_kg'];self.mass_returned+=mass;self.sample=None;self.mass_input=None;self._reset_ambient();return {'returned_mass_kg':mass}
  if k=='sample.set_mass':self.mass_input=a['mass_kg'];return a
  if k=='pressure.calibrate':
   x,y=a['reference_pa']
   if abs(x-y)<10000:raise ActionRejected('INVALID_CALIBRATION')
   vals=[]
   for key in ['sample_measured_pa','reference_measured_pa']:
    u,v=a[key];slope=(v-u)/(y-x);offset=u-slope*x
    if not .8<=slope<=1.2 or abs(offset)>1000.0001:raise ActionRejected('INVALID_CALIBRATION')
    vals.append([slope,offset])
   self.cal_pressure=vals;self.cal_volume=None;return {'calibrated':True}
  if k=='volume.calibrate':
   if not self.method:raise ActionRejected('NOT_CONFIGURED')
   b,s,v=a['blank_ratio'],a['sphere_ratio'],a['sphere_volume_m3']
   den=b-s if self.method['direction']=='powder_protect' else 1/b-1/s
   if den<=1e-8:raise ActionRejected('INVALID_CALIBRATION')
   rv=v/den;cv=rv*b if self.method['direction']=='powder_protect' else rv/b
   nominal=CELLS[self.method['cell']]
   if not .90*nominal<=cv<=1.10*nominal or not .90*self.ref<=rv<=1.10*self.ref or v>=cv:raise ActionRejected('INVALID_CALIBRATION')
   self.cal_volume=(cv,rv);return dict(cell_volume_m3=cv,reference_volume_m3=rv)
  if k=='acquisition.start':
   if not self.powered or not self.plant or not self.method or self.lid>.001 or self.cup>.001 or self.lock<1.09 or self.seal<1.39:raise ActionRejected('NOT_READY')
   if self.dev['error'] is not None:raise ActionRejected('NOT_READY')   # an error is shown: CLEAR first (up p.73-74)
   m=self.method
   if m['mode']=='measure' and (not self.sample or self.mass_input is None or self.cal_volume is None):raise ActionRejected('NOT_READY')
   if m['mode']=='leak' and self.sample:raise ActionRejected('REMOVE_SAMPLE_FIRST')
   if m['preparation']=='vacuum' and not self.plant['pump_available']:raise ActionRejected('PUMP_UNAVAILABLE')
   if m['target_gauge_pa']>=self.plant['regulator_gauge_pa']:raise ActionRejected('SUPPLY_RANGE')
   if self.sample and self.skeleton()+self.sample['open_pore_m3']>=self.cell*.98:raise ActionRejected('CELL_CAPACITY')
   self.trace=[];self.runs=[];self.run_mass=self.mass_input;self.elapsed=0.;self.leak_rate=None;self.leak_pass=None;self.pulses=0;self.phase='running';self.failure='';self._goto('thermal');self.serial+=1;self.operation_id=f'{self.instance_id}:op:{self.serial}';self.page='measurement';return dict(operation_id=self.operation_id,phase=self.phase)
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _end(self,phase,reason=''):
  self.phase=phase;self.failure=reason;self.stage='venting';self.stage_time=0.;self.stable_time=0.
  if self.operation_id:self.records[self.operation_id]=self._result()
 def _raise_error(self,code):
  """A 1200e error (up p.73-75): the measurement cycle stops and the display shows 'Error n occurred' until CLEAR; Error 8 (clock calendar
  circuit) is only printed in the report and does not affect the analysis."""
  if code==8:return
  if self.phase=='running':self._end('failed',f'ERROR_{code}')
  self.dev['error']=dict(code=code,text=ERROR_CODES[code])
 def _goto(self,stage):self.stage=stage;self.stage_time=0.;self.stable_time=0.
 def _source_pressure(self):return min(self.source*R*self.plant['ambient_k']/self.plant['source_volume_m3'],self.plant['ambient_pa']+self.plant['regulator_gauge_pa'])
 def _fill(self,i,gauge):
  """Gas in to chamber i until the controller's gauge reading reaches the set point (the true pressure where the transducer shows it)."""
  target=self._true_for_gauge(i,gauge);p=self.plant;n=self.ns if i==0 else self.nr;pres=self.pressure(i);ps=self._source_pressure();v=self.volumes()[i]
  if pres>=target-.01:return True
  if ps<=pres+.01:self._end('failed','GAS_DEPLETED');return False
  dn=min(p['fill_conductance_mol_s_pa']*(ps-pres)*DT,(target-pres)*v/(R*self.temp),self.source,(ps-pres)/(R*(self.temp/v+p['ambient_k']/p['source_volume_m3'])));j=GASES.index(p['source_gas']);n[j]+=dn;self.source-=dn;self.enthalpy+=dn*(CV[j]+R)*p['ambient_k'];return self.pressure(i)>=target-.01
 def _connect(self):
  a,b=self.pressure(0),self.pressure(1);v,w=self.volumes();dn=min(abs(a-b)*self.plant['connect_conductance_mol_s_pa']*DT,abs(a-b)/(R*self.temp*(1/v+1/w)));src,dst=(self.ns,self.nr) if a>b else (self.nr,self.ns);total=sum(src)
  if not total:return
  for j in range(3):x=dn*src[j]/total;src[j]-=x;dst[j]+=x
 def _vent(self,i,target=None,vacuum=False,conductance=None):
  p=self.plant;target=p['ambient_pa'] if target is None else target;n=self.ns if i==0 else self.nr;pres=self.pressure(i);v=self.volumes()[i];k=p['vent_conductance_mol_s_pa'] if conductance is None else conductance;dn=min(abs(pres-target)*k*DT,abs(pres-target)*v/(R*self.temp));total=sum(n)
  if pres>target and total:
   for j in range(3):x=dn*n[j]/total;n[j]-=x;(self.evacuated if vacuum else self.vented)[j]+=x;self.enthalpy-=x*(CV[j]+R)*self.temp
  elif pres<target and not vacuum:n[1]+=dn;self.ambient_in[1]+=dn;self.enthalpy+=dn*(CV[1]+R)*p['ambient_k']
 def _flow_purge(self):
  p=self.plant;total=sum(self.ns)+sum(self.nr);dn=self.method['flow_m3_s']*101325./(R*273.15)*DT
  if self.source<dn or self._source_pressure()<=p['ambient_pa']+100:self._end('failed','GAS_DEPLETED');return
  j=GASES.index(p['source_gas']);old=[a+b for a,b in zip(self.ns,self.nr)];decay=math.exp(-dn/max(total,1e-20));new=[x*decay+(total*(1-decay) if i==j else 0.) for i,x in enumerate(old)];self.source-=dn;self.enthalpy+=dn*(CV[j]+R)*p['ambient_k'];vs,vr=self.volumes()
  for i in range(3):out=old[i]+(dn if i==j else 0.)-new[i];self.vented[i]+=max(0.,out);self.enthalpy-=out*(CV[i]+R)*self.temp;self.ns[i]=new[i]*vs/(vs+vr);self.nr[i]=new[i]*vr/(vs+vr)
 def _steady(self,condition):
  self.stable_time=self.stable_time+DT if condition else 0.;return self.stable_time+1e-10>=self.method['equilibration_s']
 def _record_run(self):
  m=self.method;hi,lo,ti=self.initial_point;pf=(self.zeroed_p(0)+self.zeroed_p(1))/2;tf=self.temp;den=pf/tf-lo/ti;num=hi/ti-pf/tf
  if den<=0 or num<=0:self._end('failed','INVALID_PRESSURE_RATIO');return
  ratio=num/den;vs=None;rho=None
  if m['mode']=='measure':
   c,r=self.cal_volume;vs=c-r*ratio if m['direction']=='powder_protect' else c-r/ratio
   if not 0<vs<c:self._end('failed','INVALID_VOLUME');return
   rho=self.run_mass/vs
  self.runs.append(dict(index=len(self.runs)+1,initial_high_pa=hi,initial_low_pa=lo,final_pa=pf,initial_k=ti,final_k=tf,ratio=ratio,volume_m3=vs,density_kg_m3=rho));self._goto('between_runs')
 def _process_tick(self):
  m=self.method;p=self.plant;pa=p['ambient_pa'];self.elapsed+=DT;self.stage_time+=DT;stage=self.stage
  if self.fault is not None and self.fault!=8:self._raise_error(self.fault);return   # the injected cause is still there: the same error again
  if self.sample and self.skeleton()+self.sample['open_pore_m3']>=self.cell*.995:self._end('failed','CELL_CAPACITY');return
  if self.stage_time>m['stage_timeout_s'] and stage not in ['flow','vacuum']:self._end('failed','STAGE_TIMEOUT');return
  if stage=='thermal':
   for i in [0,1]:self._vent(i)
   if self._steady(abs(self.temp-self.target)<=.02):self._goto({'none':'baseline','flow':'flow','pulse':'pulse_fill','vacuum':'vacuum'}[m['preparation']])
  elif stage=='flow':
   self._flow_purge()
   if self.phase=='running' and self.stage_time>=m['preparation_s']:self._goto('baseline')
  elif stage=='vacuum':
   for i in [0,1]:self._vent(i,p['pump_floor_pa'],True)
   if self.stage_time>=m['preparation_s']:self._goto('baseline')
  elif stage=='pulse_fill':
   for i in [0,1]:self._fill(i,21000.)
   if self.phase=='running' and min(self.gauge_p(0),self.gauge_p(1))>=20999.:self._goto('pulse_vent')
  elif stage=='pulse_vent':
   for i in [0,1]:self._vent(i)
   if max(abs(self.gauge_p(i)) for i in [0,1])<.1:
    self.pulses+=1;self._goto('baseline' if self.pulses>=m['purge_pulses'] else 'pulse_fill')
  elif stage in ['baseline','between_runs']:
   for i in [0,1]:self._vent(i)
   if self._steady(max(abs(self.gauge_p(i)) for i in [0,1])<.05 and abs(self.temp-self.target)<=.02):
    if stage=='between_runs':
     v,r,sd,rsd=self._stats();enough=len(self.runs)>=m['runs_to_average'];auto=m['automatic'] and enough and rsd is not None and rsd<=m['finish_rsd']
     if len(self.runs)>=m['max_runs'] or auto:self._end('succeeded');return
    self._goto('leak_fill' if m['mode']=='leak' else 'fill')
  elif stage=='fill':
   i=0 if m['direction']=='sample_first' else 1;self._fill(i,m['target_gauge_pa']);self._vent(1-i)
   if self.phase=='running' and self._steady(abs(self.gauge_p(i)-m['target_gauge_pa'])<.05 and abs(self.gauge_p(1-i))<.05):self.initial_point=(self.zeroed_p(i),self.zeroed_p(1-i),self.temp);self._goto('expand')
  elif stage=='expand':
   self._connect()
   if self._steady(abs(self.pressure(0)-self.pressure(1))<.05):self._record_run()   # flow stopped: the readings are steady whatever their static error
  elif stage=='leak_fill':
   for i in [0,1]:self._fill(i,m['target_gauge_pa'])
   if self.phase=='running' and self._steady(max(abs(self.gauge_p(i)-m['target_gauge_pa']) for i in [0,1])<25.):self.initial_point=((self.zeroed_p(0)+self.zeroed_p(1))/2,self.temp);self._goto('leak_hold')
  elif stage=='leak_hold':
   if self.stage_time>=m['leak_hold_s']:
    pi,ti=self.initial_point;pf=(self.zeroed_p(0)+self.zeroed_p(1))/2;self.leak_rate=abs(pf*ti/self.temp-pi)/self.stage_time;self.leak_pass=self.leak_rate<=m['leak_limit_pa_s'];self._end('succeeded')
 def _tick(self):
  self.home_ticks=max(0,self.home_ticks-1)
  if not self.home_ticks:self.home=0.
  if not self.plant:return
  p=self.plant;olde=self.energy();oldh=self.enthalpy
  if self.phase=='running':self._process_tick()
  else:
   for i in [0,1]:self._vent(i)
  if p['leak_conductance_mol_s_pa']:
   for i in [0,1]:self._vent(i,conductance=p['leak_conductance_mol_s_pa'])
  self.power_w=max(-p['thermal_power_w'],min(p['thermal_power_w'],15*(self.target-self.temp)+p['heat_loss_w_k']*(self.target-p['ambient_k']))) if self.powered else 0.;q=self.power_w*DT;loss=p['heat_loss_w_k']*(self.temp-p['ambient_k'])*DT;capacity=p['heat_capacity_j_k']+sum((a+b)*cv for a,b,cv in zip(self.ns,self.nr,CV));self.temp=(olde+self.enthalpy-oldh+q-loss)/capacity;self.heat+=q;self.loss+=loss
  if self.phase=='running' and round(self.time_s/DT)%10==0:self.trace.append(dict(time_s=self.time_s,stage=self.stage,sample_pa=self.indicated_p(0),reference_pa=self.indicated_p(1),temperature_k=self.temp))
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<0 or dt_s>7200:raise ActionRejected('INVALID_ARGUMENT')
  self.pending+=dt_s
  while self.pending+1e-12>=DT:self.pending=max(0.,self.pending-DT);self.time_s+=DT;self._tick();self._device_tick();self.revision+=1
 # ---- device layer of the touch screen
 def _new_dev(self):return dict(settings=dict(UP_DEFAULTS),reports=[],next_report=1,cal=dict(blank=None,sphere=None),run=None,shutdown=None,power_epoch=0,reported=None,error=None)
 def _dev_values(self):
  d=self.dev;sd=d['shutdown']
  return {'device.settings':copy.deepcopy(d['settings']),'device.reports':copy.deepcopy(d['reports']),'device.calibration':copy.deepcopy(d['cal']),
          'device.status':dict(shutdown='none' if sd is None else ('closing' if self.time_s<sd-1e-9 else 'done'),run=None if d['run'] is None else d['run']['kind'],power_epoch=d['power_epoch'],
                                error=copy.deepcopy(d['error'])),
          'method.settings':copy.deepcopy(self.method)}
 def _device_invoke(self,k,a):
  d=self.dev
  if not self.powered:raise ActionRejected('POWER_OFF')
  if d['shutdown'] is not None:raise ActionRejected('SHUT_DOWN')
  if k=='error.clear':cleared=d['error'] is not None;d['error']=None;return {'cleared':cleared}   # CLEAR: restart (up p.73-74)
  if k=='settings.configure':
   if not a['settings']:raise ActionRejected('INVALID_ARGUMENT')
   d['settings'].update(copy.deepcopy(a['settings']));return {'settings':copy.deepcopy(d['settings'])}
  if k=='system.shutdown':
   if self.phase=='running':raise ActionRejected('BUSY')
   d['shutdown']=self.time_s+SHUTDOWN_S;return {'shutdown':True}
  if self.phase=='running':raise ActionRejected('BUSY')
  if k=='reports.delete':n=len(d['reports']);d['reports']=[];return {'deleted':n}
  if k=='calibration.apply':
   c=d['cal']
   if c['blank'] is None or c['sphere'] is None:raise ActionRejected('NOT_READY')
   out=self._invoke('volume.calibrate',dict(blank_ratio=c['blank'],sphere_ratio=c['sphere'],sphere_volume_m3=a['sphere_volume_m3']))
   self._report('cell calibration',extra=dict(cell_volume_cm3=round(out['cell_volume_m3']*1e6,4),reference_volume_cm3=round(out['reference_volume_m3']*1e6,4)));return out
  if not self.method:raise ActionRejected('NOT_CONFIGURED')
  mode='calibration' if k=='calibration.run' else 'leak';old=self.method['mode'];self.method['mode']=mode
  try:out=self._invoke('acquisition.start',{})
  except ActionRejected:self.method['mode']=old;raise
  d['run']=dict(kind=a['kind'] if k=='calibration.run' else 'leak',mode_before=old)
  if k=='calibration.run':d['cal'][a['kind']]=None
  return out
 def _report(self,kind,extra=None):
  """Save a report.  Report files (up p.34, p.59): run reports ultraReport<SAMPLE_ID>, cell calibration reports vcReport (the blank and sphere
  runs of the calibration too); with Number Report Files on a repeated name gets -1, -2, ...; off, the new report replaces the older one of
  that name.  A leak check report has no documented file name and is always kept (assumption)."""
  d=self.dev;v,rho,sd,rsd=self._stats();s=d['settings']
  base='ultraReport'+s['sample_id'] if kind=='sample' else 'vcReport' if kind.startswith('cell calibration') or kind.startswith('calibration run') else None
  name=None
  if base is not None:
   same=[x for x in d['reports'] if x.get('file')==base or re.fullmatch(re.escape(base)+r'-\d+',x.get('file') or '')]
   if not s['number_reports']:d['reports']=[x for x in d['reports'] if x not in same];name=base
   else:name=base if not same else f"{base}-{1+max(int(x['file'][len(base)+1:]) if x['file']!=base else 0 for x in same)}"
  rec=dict(id=d['next_report'],kind=kind,file=name,sample_id=s['sample_id'],user_id=s['user_id'],gas=s['gas_name'],language=s['language'],time_s=round(self.time_s,1),phase=self.phase,failure=self.failure,runs=len(self.runs),
           volume_cm3=None if v is None else round(v*1e6,5),density_g_cm3=None if rho is None else round(rho/1000.,5),rsd_pct=None if rsd is None else round(rsd*100,4),
           leak_rate_pa_s=self.leak_rate,leak_pass=self.leak_pass,**(extra or {}))
  if self.fault==8:rec['error']='Error 8 occurred: On-board clock failure'   # up p.74: printed, the results are not affected
  d['reports'].append(rec);d['next_report']+=1;del d['reports'][:-50]
 def _device_tick(self):
  """A run ends: its report is kept (1200e: reports are always saved on the instrument); a calibration run keeps its mean pressure ratio."""
  d=self.dev
  if d['shutdown'] is not None and not self.powered:d['shutdown']=None
  if self.phase=='running' or self.operation_id is None or self.operation_id==d['reported']:return
  r=d['run']
  if r is not None:
   if r['kind'] in ('blank','sphere'):
    rows=[x['ratio'] for x in self.runs][-self.method['runs_to_average']:]
    if self.phase=='succeeded' and rows:d['cal'][r['kind']]=sum(rows)/len(rows)
    self._report(f'calibration run ({r["kind"]})')
   else:self._report('leak check')
   self.method['mode']=r['mode_before'];d['run']=None
  else:self._report('sample')
  d['reported']=self.operation_id
 # ---- front panel: host protocol of the engine and the panel.* commands
 def panel_read(self):
  out=self.read('agent');out.update({'host.powered':self.powered,'host.power_epoch':self.dev['power_epoch'],'host.readout':self._readout()})
  return out
 def _readout(self):
  d=self.dev;u=d['settings']['pressure_unit'];v,rho,sd,rsd=self._stats()
  f=lambda i:(f'{self.gauge_p(i)/6894.757:.3f}' if self.plant else '0.000') if u=='psig' else f'{self.indicated_p(i)/1000:.3f}'   # noqa: E731  psig: the zeroed gauge reading
  return dict(temperature=f'{self.temp-273.15:.2f}',pressure_sample=f(0),pressure_reference=f(1),unit=u,stage=self.stage,
              valves=VALVES.get(self.stage,self.stage) if self.phase=='running' or self.stage=='venting' else 'all closed',runs=len(self.runs),
              volume='---' if v is None else f'{v*1e6:.4f}',density='---' if rho is None else f'{rho/1000:.4f}',rsd='---' if rsd is None else f'{rsd*100:.3f}',
              phase=self.phase,failure=self.failure,target=f'{self.target-273.15:.2f}',sample_id=d['settings']['sample_id'] or '-',user_id=d['settings']['user_id'] or '-',
              mass='---' if self.mass_input is None else f'{self.mass_input*1000:.4f}',calibrated=self.cal_volume is not None,
              blank='---' if d['cal']['blank'] is None else f'{d["cal"]["blank"]:.5f}',sphere='---' if d['cal']['sphere'] is None else f'{d["cal"]["sphere"]:.5f}',
              last=d['reports'][-1] if d['reports'] else None,error_code='' if d['error'] is None else str(d['error']['code']),error_text='' if d['error'] is None else d['error']['text'])
 def _panel(self):
  p=_engine().PanelUI(PANEL_SPEC,_PanelHost(self))
  if self.panel_state is not None:p.ui=self.panel_state
  return p
 def _panel_command(self,k,a):
  e=_engine();p=self._panel()
  try:
   if k=='panel.touch':ok=p.touch(a['display_id'],a['x'],a['y'],float(a.get('duration_s',.1)),(a['to_x'],a['to_y']) if 'to_x' in a and 'to_y' in a else None)
   elif k=='panel.press':ok=p.press(a['input_id'],float(a.get('duration_s',.1)),())
   elif k=='panel.switch':ok=p.switch(a['input_id'],a['position'],())
   elif k=='panel.capture':
    data,w,h=p.png_bytes(a['display_id']);self.panel_state=p.ui;sha=hashlib.sha256(data).hexdigest()
    folder=Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir())/'chemistry_sim_panel'/self.instance_id
    folder.mkdir(parents=True,exist_ok=True);path=folder/f'{a["display_id"]}_{sha[:16]}.png'
    if not path.exists():path.write_bytes(data)
    return dict(display_id=a['display_id'],time_s=self.time_s,image=dict(path=str(path),format='png',width=w,height=h,sha256=sha))
   else:
    f=p.frame();self.panel_state=p.ui;return dict(frame=f)
  except e.PanelError:
   self.panel_state=p.ui;raise ActionRejected('INVALID_ARGUMENT') from None
  self.panel_state=p.ui;return dict(accepted=bool(ok))
class _PanelHost:
 """The engine's host protocol (panel_read / panel_invoke / panel_time) on top of a runtime; built per gesture."""
 def __init__(self,rt):self.rt=rt
 def panel_read(self):return self.rt.panel_read()
 def panel_invoke(self,capability_id,arguments):
  try:
   if self.rt._panel_session is not None:self.rt._panel_session._guard(capability_id,arguments)   # a gesture made through a session keeps its caller rules
   return self.rt.invoke(capability_id,**arguments)
  except ActionRejected as exc:raise _engine().PanelRejected(str(exc)) from None
 def panel_time(self):return self.rt.time_s

class AntonpaarUltrapyc5000Session:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,i):
  if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  m=export_antonpaar_ultrapyc5000_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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
   self._guard(capability_id,arguments);h._panel_session=self if capability_id.startswith('panel.') else None
   try:result=h.invoke(capability_id,**arguments)
   finally:h._panel_session=None
   op=result.get('operation_id')
   if op:h._owners[op]=self._principal
   if h.operation_id and h.operation_id not in h._owners:h._owners[h.operation_id]=self._principal   # a run started by a panel gesture
   response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=op,status='accepted' if op else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None);h._requests[key]=(digest,copy.deepcopy(response));return response
  except ActionRejected as exc:return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,status='unsupported' if str(exc)=='UNSUPPORTED' else 'rejected',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))
 def _guard(self,capability_id,arguments):
  """Lead rule 2026-10-19: an agent may not stop another agent's run (measurement, calibration run, leak check), nor change the sample /
  user / gas / report settings that go into its report, by a direct call or a panel gesture alike; the evaluator is not restricted.  The rear
  power switch (power.set) is a hard switch and the lid / lock rings are physical parts: never refused; a switch-off cancels the run as a power
  loss, the instrument's own interlocks refuse the lid during a run for everyone."""
  h=self._host;owner=h._owners.get(h.operation_id) if h.operation_id else None
  if self._role!='agent' or h.phase!='running' or owner is None or owner==self._principal:return
  if capability_id=='acquisition.stop' or \
     (capability_id=='settings.configure' and {k for k,v in arguments.get('settings',{}).items() if h.dev['settings'].get(k)!=v}-{'pressure_unit'}):raise ActionRejected('ACCESS_DENIED')   # the display unit stays free
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





_PANEL_SPEC_PATH=ASSET/'panel_ui.json'
PANEL_SPEC=_engine().load_spec(_PANEL_SPEC_PATH,capabilities=set(CAPS)|{'panel.touch','panel.press','panel.switch','panel.capture','panel.frame'}) \
 if _PANEL_SPEC_PATH.is_file() and not os.environ.get('UP_PANEL_BUILD') else None
PANEL_CAPS={k:(d['input_schema'],d['output_schema'],d['component_ids'],d['function_zh'],d['kind'],d['visibility'],d['example_arguments'],[])
            for k,d in (_engine().capability_definitions(PANEL_SPEC).items() if PANEL_SPEC else [])}
CAPS.update(PANEL_CAPS)

def export_antonpaar_ultrapyc5000_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions='All pressure slots absolute Pa; target/supply inputs gauge relative to explicit ambient_pa. Purge m3/s refers101325Pa273.15K; volume SI m3; joint axes in manifest frames',origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'AntonpaarUltrapyc5000Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description='fixed0.1s physical integration; traces sampled at1s; reads never advance'),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNKNOWN_OPERATION','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','INITIALIZATION_ONLY','BUSY','UNKNOWN_REFERENCE','NOT_READY','NOT_RUNNING','PRESSURE_INTERLOCK','LID_INTERLOCK','LOCK_SEQUENCE','LOAD_PRECONDITION','CELL_CAPACITY','REMOVE_SAMPLE_FIRST','NOT_CONFIGURED','PUMP_UNAVAILABLE','SUPPLY_RANGE','INVALID_CALIBRATION','INSERT_ACCESS']
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  un=k in UNSUPPORTED;asyn=k=='acquisition.start';caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if un else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if un else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if un else 'implemented'),binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.antonpaar_ultrapyc5000.AntonpaarUltrapyc5000Session.invoke',invocation=f'session.invoke(instance_id="ultrapyc5000_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if kind=='query' or un else [dict(description_zh='有限样品/气源，双旋盖闭合、气压允许、标定及配置匹配；拒绝原子回退',state_ids=['power.enabled','method.configured','sample.loaded','lid.lift_m','lock.angle_rad','seal.angle_rad','cup.lift_m','acquisition.phase'],enforced_by='runtime',check='Runtime.invoke atomic rollback; authenticated Session; step failure retains gas consumed and completed runs')],state_effects=[dict(state_id=x,timing='immediate',description_zh='受检动作更新状态') for x in effects],observes=[],model_effects=dict(mode='none',description_zh='待实际场景验证',binding_ids=[]),execution=dict(mode='asynchronous' if asyn else 'synchronous',time_domain='simulation',completion_criterion='全部重复或自动重复性阈值满足；泄压完毕' if asyn else '事务返回；只有step推进过程',completion_state_ids=['acquisition.phase'] if asyn else [],timeout_s=None,cancellation='supported' if asyn else 'unsupported',retry='same_request_id',resources=['host_clock','sample','source_gas','sample_chamber','reference_chamber','request_cache'],partial_failure_zh='已消耗气体及已完成数据不回滚；停止气源供给，后续step继续有限泄压；固体质量保留'),errors=[dict(code=x,meaning_zh=x,side_effects='none') for x in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=['名义密度与实测平均分开；没有50种实测本机响应或校准噪声'],source_ids=['manufacturer_manual','simulation_assumption'])
  if asyn:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='有限气体、温度和压力反演过程推进') for x in ['thermal.indicated_k','pressure.sample_pa','pressure.reference_pa','result.volume_m3','result.density_kg_m3','result.rsd','acquisition.stage','acquisition.elapsed_s','acquisition.phase']]
 mech=dict(component_ids=['controller','thermal_block','sample_well','reference_large','reference_small','sample_cup','valve_0','valve_1'],state_ids=list(slots),meaning_zh='有限惰性气体的双腔膨胀、温压修正体积反演和集总冷热平衡',implementation='lumped',equations=['Vs=mtrue/rho_ref*[1+alphaV*(T-Tref)]+Vclosed; open pores accessible and excluded from displaced volume; envelope=Vs+Vopen<cell','P=nRT/Vfree; three explicit He,N2,Ar inventories; ambient approximated N2; pressure sensor indicated=gain*P+offset with independent2point corrections','Source pressure=min(nsource*R*Tamb/Vsource,ambient+regulator); finite flow mol/s=k*dP limited by finite inventories and pressure equilibration','ratio=(Phi/Ti-Pf/Tf)/(Pf/Tf-Plo/Ti); sample_first:Vs=Vc-Vr/ratio; powder_protect:Vs=Vc-Vr*ratio; reported density=entered_mass/Vs','Volume calibration: powder_protect Vr=Vsphere/(rblank-rsphere), Vc=Vr*rblank; sample_first Vr=Vsphere/(1/rblank-1/rsphere), Vc=Vr/rblank','Flow purge exact mixed exponential y_old*exp(-dn/n); pulse purge bounded21000Pa gauge; vacuum finite removal with explicit external pump floor; no adsorbate removal','Finite species input+source+ambient ingress=remaining+vented+evacuated; solid load=remaining+returned','E=(Cblock+sum n_i*Cv_i)*T; Enew=Eold+net flow enthalpy+Qheater-Qloss; cp=Cv+R; ideal He/Ar monatomic,N2diatomic','Thermal controller Q=clip(15*(Tset-T)+U*(Tset-Tamb),-Qmax,Qmax); Qloss=U*(T-Tamb); open loading boundary-work ledger accounts ambient replacement at fixed host temperature','Repeat sample standard deviation and mean over selected lastN runs; no random noise; leakage criterion uses temperature-normalized measured dP/dt'],parameters={},source_ids=['manufacturer_specs','manufacturer_manual','nist_density','simulation_assumption'],limitations=['73public density records:71NISTnominal/2Ultrapyc5000means;50measured material responses unavailable.','Local dimensions/travel/mass/inertia, gas and thermal rates, sensor gains/offsets and reference temperature transfer are explicit assumptions.','No empirically calibrated adsorption, pore accessibility timescale, moisture removal, EOS, pressure-dependent compressibility or foam methods.','NIST graphite1.7g/cm3 retained as nominal attenuation-medium density, not recast as perfect crystal density.','Global sample thermoelastic work, supply-bottle/regulator/pump energy and actual Peltier COP not solved; ledger covers defined block+gas model.','Only medium50cm3 cup articulated; other cup geometries static accessories; no OEM CAD or real seal/robot/hardware proof.','No certified ±0.02C stability/0.02% accuracy, empirical drift/noise distribution or OS-level truth isolation.'])
 spec=importlib.util.spec_from_file_location('_ultrapyc_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'gas_pycnometry':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'。'
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'gas_pycnometry':mech})

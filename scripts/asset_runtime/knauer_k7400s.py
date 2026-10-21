"""CHEM-106 finite-water freezing-point osmometer educational process; trusted host API, no experimental calibration.
Front panel (1.1.0; panel_ui.json beside the contract, run by the shared engine _panel_ui.py): the 2 x 24 LCD with its eight screens, Start/Stop, Vib,
the four arrows and the power switch; behind them the device settings (data output, result and temperature units, clock), the printed / RS-232 output
log, the cooling cavity temperature, the CALIBRATE fields (----, %%%% relative mode, 50-2000 mOsmol) and the CALIBRATE-T pre-calibration (head offset)."""
from pathlib import Path
import copy,math,json,importlib.util,datetime,hashlib,os,tempfile,weakref
PROTOCOL_VERSION='1.0.0';ASSET_VERSION='1.1.2';SLUG='knauer_k7400s';CATALOG_ID='CHEM-106';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];TEMPLATE_REL='assets/expansion_batch04/t4_measure/'+SLUG+'/osmotic_library.json'
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
  if not isinstance(v,dict) or not set(s.get('required',[]))<=v.keys() or set(v)-set(s['properties']):raise ActionRejected('INVALID_ARGUMENT')
  for k,x in v.items():validate(s['properties'][k],x)
 elif t=='array':
  if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
  for x in v:validate(s['items'],x)
 if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')
CP=4184.;LATENT=333550.;KF=1.858;TF=273.15;DT=.1
PLANT=obj({'cooling_rate_k_s':number('K/s',0,2),'warming_rate_k_s':number('K/s',.05,3),'sensor_tau_s':number('s',.01,10),'sensor_gain':number('1',.9,1.1),'sensor_bias_k':number('K',-.5,.5),'fault':enum(['none','early_freeze','no_freeze']),'accept_thermal_surrogate':BOOL})
PLANT_EXAMPLE=dict(cooling_rate_k_s=.4,warming_rate_k_s=.8,sensor_tau_s=1.,sensor_gain=1.,sensor_bias_k=0.,fault='none',accept_thermal_surrogate=True)
METHOD=obj({'sample_volume_m3':{'type':'number','enum':[50e-9,150e-9],'x-unit':'m^3'},'freeze_k':number('K',260.15,269.15),'cooling_limit_k':number('K',255.15,261.15),'sample_interval_s':number('s',.1,10)})
METHOD_EXAMPLE=dict(sample_volume_m3=150e-9,freeze_k=267.15,cooling_limit_k=258.15,sample_interval_s=1.)
COMPONENT=obj({'substance_id':TEXT,'molality_mol_kg':number('mol/kg',1e-5,1.)})
SAMPLE=obj({'sample_id':TEXT,'kind':enum(['reference','solution']),'volume_m3':{'type':'number','enum':[50e-9,150e-9],'x-unit':'m^3'},'water_mass_kg':number('kg',1e-5,2e-4),'initial_temperature_k':number('K',283.15,308.15),'components':dict(arr(COMPONENT),maxItems=10),'reference_osmolality_mol_kg':number('mol/kg',0,2),'accept_temperature_transfer':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='NaCl_0p1m',kind='solution',volume_m3=150e-9,water_mass_kg=.00015,initial_temperature_k=293.15,components=[dict(substance_id='NaCl',molality_mol_kg=.1)],reference_osmolality_mol_kg=0.,accept_temperature_transfer=True)
def reference(value,volume=150e-9):return dict(SAMPLE_EXAMPLE,sample_id='certified_reference_'+str(value),kind='reference',volume_m3=volume,water_mass_kg=volume*1000.,components=[],reference_osmolality_mol_kg=value)
POINT=obj({'time_s':number('s',0),'temperature_k':number('K'),'stage':TEXT})
RESULT=obj({'phase':PHASE,'complete':BOOL,'elapsed_s':number('s',0),'points':arr(POINT),'freezing_temperature_k':nullable(number('K')),'osmolality_mol_kg':nullable(number('mol/kg',0,2)),'quality':TEXT,'template_source':TEXT,'evidence':TEXT,'warnings':arr(TEXT)})
LEDGER=obj({'water_loaded_kg':number('kg',0),'water_remaining_kg':number('kg',0),'water_ice_kg':number('kg',0),'water_liquid_kg':number('kg',0),'water_returned_kg':number('kg',0),'water_residual_kg':number('kg'),'solute_residual_mol':number('mol'),'enthalpy_j':number('J'),'loaded_enthalpy_j':number('J'),'returned_enthalpy_j':number('J'),'heat_added_j':number('J',0),'heat_removed_j':number('J',0),'energy_residual_j':number('J')})
CAPS={};UNSUPPORTED={'software.euroosmo','hardware.connect','mixture.equilibrium','fault.probability_model'}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},effects or [])
cap('plant.configure',PLANT,obj({'configured':BOOL}),['peltier','thermistor'],'显式名义热过程、温度传感器及确定性故障注入','configuration','evaluator',PLANT_EXAMPLE,['plant.configured'])
cap('method.configure',METHOD,obj({'configured':BOOL,'calibration_valid':BOOL}),['controller','cooling_cavity'],'选择50/150µL头、触发与冷却温度；变更后重新校准','configuration','agent',METHOD_EXAMPLE,['method.configured','calibration.valid'])
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['controller','peltier'],'逻辑供电与5min预热，断电取消主动过程',ex={'enabled':True},effects=['power.enabled','acquisition.phase'])
cap('head.position',obj({'position':enum(['measuring','raised','parked'])}),obj({'position':TEXT}),['head_slide','measuring_head'],'人工测量头拆装/停车姿态（手上操作，运行中与样品冻结时都不拒绝；测量头带样品管离开冷却孔后样品不再被冷却：冷却阶段按COOLER TIMEOUT失败，平台阶段样品融化、峰温升高（OUT OF RANGE或结果偏低）；离开冷却孔时按解冻速率向20°C升温为假设）',ex={'position':'raised'},effects=['head.position','head.y_m','head.z_m'])
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_tube','thermistor'],'宿主装入有限水和已知摩尔数，身份/真值不向agent公开','configuration','evaluator',SAMPLE_EXAMPLE,['sample.loaded','sample.identity'])
cap('sample.unload',obj({}),obj({'returned_water_kg':number('kg',0)}),['sample_tube'],'仅解冻且抬头后归还全部有限水/溶质/焓','device_command','evaluator',effects=['sample.loaded','sample.identity'])
cap('vibrator.pulse',obj({}),obj({'active':BOOL}),['stirring_wire'],'装管后手动5s振丝/混匀；运动不作真实振幅校准',effects=['vibrator.active'])
cap('acquisition.start',obj({'purpose':enum(['sample','calibration','pre_calibration'])}),obj({'phase':PHASE,'operation_id':TEXT}),['peltier','thermistor','stirring_wire'],'过冷→主动成核→观测峰温→解冻，固定时钟推进；pre_calibration为CALIBRATE-T预校准（水样）',ex={'purpose':'sample'},effects=['acquisition.phase','acquisition.stage','acquisition.elapsed_s'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['controller','peltier'],'取消并保留水/溶质/冰/热量，通电后继续解冻',effects=['acquisition.phase','acquisition.stage'])
cap('calibration.accept',obj({'known_osmolality_mol_kg':number('mol/kg',0,2)}),obj({'points':dict(number('1',1,3),type='integer'),'valid':BOOL}),['controller','thermistor'],'把最近成功校准运行的观测峰温与独立标准标值关联','configuration','agent',{'known_osmolality_mol_kg':.4},['calibration.valid','calibration.points'])
cap('process.measure',obj({}),RESULT,['thermistor','lcd'],'读取观测温度、峰温和经两/三点曲线换算的渗透浓度','query')
cap('template.evaluate',obj({'substance_id':TEXT,'molality_mol_kg':number('mol/kg',0,1)}),obj({'osmotic_coefficient':number('1',0),'osmolality_mol_kg':number('mol/kg',0),'temperature_k':number('K'),'template_source':TEXT,'evidence':TEXT}),['thermistor'],'查询公开298.15K渗透系数表；不声称冰点温度实测','query',ex={'substance_id':'NaCl','molality_mol_kg':.1})
cap('ledger.read',obj({}),LEDGER,['sample_tube','peltier'],'宿主查询水/溶质/焓守恒','query','evaluator')
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'当前子集未实现')
# front panel (1.1.0): device settings, clock, CALIBRATE fields and CALIBRATE-T (manual p.16-20, Fig. 9)
SERIAL='000136429';FIRMWARE='01.18';CLOCK_EPOCH='2026-01-01T00:00:00';COOLER_TAU_S=20.;STANDBY_K=293.15;PRE_TOL_K=.6;LOG_MAX=200
SETTINGS=obj({'data_output':enum(['PRINTER','PC']),'result_unit':enum(['mOsmol','degC']),'temp_unit':enum(['degC','degF'])})
INT=lambda lo,hi:{'type':'integer','minimum':lo,'maximum':hi,'x-unit':'1'}
SELECTION={'anyOf':[enum(['off','relative']),number('mol/kg',.05,2.)]};FIELD_VALUE={'anyOf':[number('mol/kg',0,2),enum(['relative']),{'type':'null'}]}
LOG_ENTRY=obj({'time_s':number('s',0),'port':enum(['PRINTER','PC']),'lines':arr(TEXT,2)})
cap('device.configure',SETTINGS,SETTINGS,['controller','lcd'],'屏幕1结果单位mOsmol/°C、屏幕2数据输出PRINTER/PC、屏幕3温度单位°C/°F（手册p.16-17）；通电时设定，断电保持',ex=dict(data_output='PC',result_unit='mOsmol',temp_unit='degC'),effects=['device.settings'])
cap('device.set_clock',obj({'year':INT(2000,2099),'month':INT(1,12),'day':INT(1,31),'hour':INT(0,23),'minute':INT(0,59),'second':INT(0,59)}),obj({'datetime':TEXT}),['controller','lcd'],'屏幕5 DATE：设定日期与时间（断电保持；用于打印/输出时间戳）',ex=dict(year=2026,month=3,day=23,hour=8,minute=24,second=35),effects=['device.datetime'])
cap('calibration.select_field',obj({'field':INT(2,3),'value':SELECTION}),obj({'fields':arr(FIELD_VALUE,3)}),['controller','lcd'],'屏幕6 CALIBRATE第2/3字段：----停用该点、%%%%停用其他点并转为相对模式（结果以该点的%显示）、50–2000 mOsmol/kg（50步）为下次校准的标值（手册p.19-20）',ex=dict(field=2,value=.4),effects=['calibration.fields','calibration.valid','calibration.points','calibration.relative_mode'])
cap('calibration.field_accept',obj({'field':INT(1,3)}),obj({'points':INT(0,3),'valid':BOOL}),['controller','thermistor'],'校准结果“o.k.? <start>”时按Start：把刚完成的校准运行的观测峰温赋给该字段（第1字段固定0000；同一字段的旧点被覆盖，%%%%字段成为相对点）',ex=dict(field=2),effects=['calibration.valid','calibration.points','calibration.pending','calibration.fields','calibration.relative_mode','device.output_log'])
cap('calibration.discard',obj({}),obj({'pending':BOOL}),['controller'],'校准结果“o.k.?”时按其他键：放弃新值，原校准点保持',effects=['calibration.pending'])
cap('calibration.pre_accept',obj({}),obj({'head_offset_k':number('K')}),['controller','thermistor'],'屏幕8 CALIBRATE-T“>successful<”时按Start确认：保存测量头温度修正（使水的峰温为0°C）并删除已有校准点（手册p.19）',effects=['calibration.head_offset_k','calibration.pre_result','calibration.valid','calibration.points','calibration.fields','calibration.relative_mode'])
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='controller',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
for k in ['plant','method']:slot(k+'.configured',BOOL,'1',k+'已配置','setpoint')
for _k,_e in {'calibration.accept':['calibration.fields','calibration.pending','device.output_log'],'acquisition.start':['acquisition.purpose','calibration.pending','calibration.pre_result','osmolality.relative_percent'],'method.configure':['calibration.relative_mode']}.items():CAPS[_k][7].extend(_e)   # front panel (1.1.0): effects on the new slots
slot('device.settings',SETTINGS,'1','面板设定：数据输出PRINTER/PC、结果单位mOsmol/°C、温度单位°C/°F','setpoint',c='lcd');slot('device.datetime',TEXT,'1','设备时钟（DD.MM.YY hh:mm:ss，屏幕5）',c='lcd');slot('device.info',obj({'serial':TEXT,'firmware':TEXT}),'1','屏幕4 INFO：序列号与固件版本',c='lcd');slot('device.output_log',arr(LOG_ENTRY),'1','RS-232输出记录（打印格式两行：Measured/Calibrated NNNN mOsmol或错误、日期时间；最多200条）')
slot('cooler.temperature_k',number('K'),'K','冷却腔温度（Peltier腔，冷却与测量时趋向cooling limit，其余趋向20°C待机，时间常数20s，工程值）',c='cooling_cavity');slot('calibration.fields',arr(FIELD_VALUE,3),'1','屏幕6三个校准字段：0000固定、第2/3字段为标值mol/kg、relative（%%%%）或null（----）');slot('calibration.relative_mode',BOOL,'1','%%%%相对模式：结果以相对点的%显示')
slot('calibration.pending',BOOL,'1','已完成的校准运行等待“o.k.? <start>”确认');slot('calibration.pre_result',nullable(enum(['successful','not_successful'])),'1','CALIBRATE-T预校准结果（待确认）；无为null');slot('calibration.head_offset_k',number('K'),'K','预校准保存的测量头温度修正（加到观测温度上）','setpoint',c='thermistor')
slot('acquisition.purpose',nullable(enum(['sample','calibration','pre_calibration'])),'1','当前/最近运行的用途');slot('osmolality.relative_percent',nullable(number('%')),'%','相对模式下的结果（相对点的%）',c='lcd')
slot('power.enabled',BOOL,'1','逻辑电源','setpoint',w=('power.set','enabled'));slot('power.warmup_s',number('s',0),'s','本次持续通电时长');slot('head.position',enum(['measuring','raised','parked']),'1','人工头姿态','scene',c='measuring_head')
slot('head.y_m',number('m',0,.12),'m','从冷却孔到停车位的工程位移','scene',c='head_slide');slot('head.z_m',number('m',0,.06),'m','测量头工程抬升','scene',c='measuring_head');slot('sample.loaded',BOOL,'1','有限样品管已装入');slot('sample.identity',TEXT,'1','样品身份','process_truth','evaluator','sample_tube')
slot('sample.true_temperature_k',nullable(number('K')),'K','有限焓状态温度','process_truth','evaluator','thermistor');slot('sample.ice_kg',number('kg',0),'kg','有限冻结水质量','process_truth','evaluator','sample_tube');slot('sample.thawed',BOOL,'1','已解冻且接近20摄氏度待机');slot('temperature.k',nullable(number('K')),'K','理想量化温度观测','derived',c='thermistor');slot('temperature.display_c',nullable(number('degC')),'degC','面板摄氏显示，由SI观测温度减273.15','derived',c='lcd')
slot('freezing.temperature_k',nullable(number('K')),'K','观测成核回升峰值温度','derived',c='thermistor');slot('osmolality.mol_kg',nullable(number('mol/kg',0,2)),'mol/kg','由观测峰值及校准曲线推断','derived',c='lcd');slot('osmolality.display_mosm_kg',nullable(number('mOsmol/kg',0,2000)),'mOsmol/kg','面板整数显示，SI槽的1000倍','derived',c='lcd');slot('vibrator.active',BOOL,'1','振丝触发/混匀时段','derived',c='stirring_wire');slot('calibration.valid',BOOL,'1','含水零点和至少一个非零点');slot('calibration.points',dict(number('1',0,3),type='integer'),'1','已接受校准点数');slot('acquisition.phase',PHASE,'1','异步操作状态');slot('acquisition.stage',enum(['idle','cooling','measuring','thawing','ready']),'1','热过程阶段');slot('acquisition.elapsed_s',number('s',0),'s','当前运行已推进时间');slot('acquisition.failure',TEXT,'1','失败代码')
PANEL_DEVICE={'device.configure','device.set_clock','calibration.select_field','calibration.field_accept','calibration.discard','calibration.pre_accept'}
def _panel_engine():
 try:
  from . import _panel_ui as engine                                         # imported as asset_runtime.knauer_k7400s (tests, Matterix bridge)
  return engine
 except ImportError:                                                         # loaded from its file path
  import sys
  if '_chemsim_panel_ui' not in sys.modules:
   s=importlib.util.spec_from_file_location('_chemsim_panel_ui',Path(__file__).with_name('_panel_ui.py'));sys.modules['_chemsim_panel_ui']=importlib.util.module_from_spec(s);s.loader.exec_module(sys.modules['_chemsim_panel_ui'])
  return sys.modules['_chemsim_panel_ui']
_panel_ui=_panel_engine();ASSET=ROOT/'assets/expansion_batch04/t4_measure'/SLUG
if os.environ.get('K7400S_PANEL_BUILD'):PANEL_SPEC,_PANEL_DEFS=None,{}      # spec builder: runtime constants without the spec being written
else:PANEL_SPEC=_panel_ui.load_spec(ASSET/'panel_ui.json',capabilities=set(CAPS));_PANEL_DEFS=_panel_ui.capability_definitions(PANEL_SPEC)
PANEL_CAPS={k:(d['input_schema'],d['output_schema'],list(d['component_ids']),d['function_zh'],d['kind'],d['visibility'],d['example_arguments'],[]) for k,d in _PANEL_DEFS.items()}   # kept out of CAPS
ALL_CAPS=dict(CAPS,**PANEL_CAPS);_PANELS=weakref.WeakKeyDictionary()
class KnauerK7400sRuntime:
 def __init__(self,instance_id='osmometer_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.warmup=0.;self.plant=None;self.method=None;self.sample=None;self.pose='measuring';self.q=[0.,0.];self.phase='idle';self.stage='idle';self.elapsed=0.;self.points=[];self.failure='';self.water=0.;self.ice=0.;self.solutes={};self.loaded_solutes={};self.returned_solutes={};self.loaded_water=0.;self.returned_water=0.;self.H=0.;self.loaded_H=0.;self.returned_H=0.;self.heat_added=0.;self.heat_removed=0.;self.temp=None;self.sensor=None;self.observed=None;self.freezing=None;self.osm=None;self.peak=None;self.thawed=True;self.mixed=True;self.nucleated=False;self.vib_until=0.;self.calibration=[];self.measuring_s=0.;self.purpose=None;self.sample_window=None;self.result_time=None;self.serial=0;self.operation_id=None;self.records={};self.pending=0.;self._principals={};self._requests={};self._owners={};self.templates=json.loads((ROOT/TEMPLATE_REL).read_text())['substances']
  self.dev=dict(data_output='PRINTER',result_unit='mOsmol',temp_unit='degC',clock_offset_s=0.,log=[]);self.cooler=STANDBY_K;self.cal_fields={2:None,3:None};self.cal_owner={2:None,3:None};self.cal_relative=None;self.rel_pct=None;self.cal_handled=True;self.pre_handled=True;self.head_offset=0.;self.panel_capture_dir=None
  self._caller=None   # principal of the session call in progress (set by KnauerK7400sSession.invoke; panel keys act as that caller)
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ['agent','evaluator','debug'] or principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return KnauerK7400sSession(self,principal_id)
 def describe(self):return export_knauer_k7400s_contract_metadata()
 def calibrated(self):return bool(self.calibration) and self.calibration[0]['known']==0. and (len(self.calibration)>=2 or self.cal_relative is not None)
 def phi(self,sid,m):
  if sid not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
  rows=[dict(molality_mol_kg=0.,osmotic_coefficient=1.),*self.templates[sid]['rows']]
  if m>rows[-1]['molality_mol_kg']+1e-12:raise ActionRejected('SOURCE_RANGE')
  for a,b in zip(rows,rows[1:]):
   if m<=b['molality_mol_kg']+1e-12:
    f=(m-a['molality_mol_kg'])/(b['molality_mol_kg']-a['molality_mol_kg']);return a['osmotic_coefficient']+f*(b['osmotic_coefficient']-a['osmotic_coefficient'])
  raise ActionRejected('SOURCE_RANGE')
 def osmo(self,liquid):return sum((n/liquid if sid=='reference_effective_particles' else 2*n/liquid*self.phi(sid,n/liquid)) for sid,n in self.solutes.items())
 def max_ice(self):
  reserve=max([.5*self.water]+[n/self.templates[sid]['rows'][-1]['molality_mol_kg'] for sid,n in self.solutes.items() if sid!='reference_effective_particles']);return max(0.,self.water-reserve)
 def equilibrium(self,h):
  tf0=TF-KF*self.osmo(self.water)
  if not self.nucleated or h>=self.water*CP*(tf0-TF):return TF+h/(self.water*CP),0.
  def f(ice):t=TF-KF*self.osmo(self.water-ice);return self.water*CP*(t-TF)-ice*LATENT
  lo=0.;hi=self.max_ice()
  if h<f(hi)-1e-10:raise ActionRejected('SOURCE_RANGE')
  for _ in range(50):
   mid=(lo+hi)/2
   if f(mid)>h:lo=mid
   else:hi=mid
  ice=(lo+hi)/2;return TF-KF*self.osmo(self.water-ice),ice
 def _ledger(self):return dict(water_loaded_kg=self.loaded_water,water_remaining_kg=self.water,water_ice_kg=self.ice,water_liquid_kg=self.water-self.ice,water_returned_kg=self.returned_water,water_residual_kg=self.loaded_water-self.returned_water-self.water,solute_residual_mol=sum(abs(n-self.solutes.get(k,0.)-self.returned_solutes.get(k,0.)) for k,n in self.loaded_solutes.items()),enthalpy_j=self.H,loaded_enthalpy_j=self.loaded_H,returned_enthalpy_j=self.returned_H,heat_added_j=self.heat_added,heat_removed_j=self.heat_removed,energy_residual_j=self.loaded_H+self.heat_added-self.heat_removed-self.returned_H-self.H)
 def read(self,visibility='agent'):
  d={'plant.configured':self.plant is not None,'method.configured':self.method is not None,'power.enabled':self.powered,'power.warmup_s':self.warmup,'head.position':self.pose,'head.y_m':self.q[0],'head.z_m':self.q[1],'sample.loaded':self.sample is not None,'sample.identity':self.sample['sample_id'] if self.sample else '','sample.true_temperature_k':self.temp,'sample.ice_kg':self.ice,'sample.thawed':self.thawed,'temperature.k':self.observed,'temperature.display_c':None if self.observed is None else self.observed-TF,'freezing.temperature_k':self.freezing,'osmolality.mol_kg':self.osm,'osmolality.display_mosm_kg':None if self.osm is None else round(self.osm*1000),'vibrator.active':self.powered and self.time_s<self.vib_until,'calibration.valid':self.calibrated(),'calibration.points':len(self.calibration),'acquisition.phase':self.phase,'acquisition.stage':self.stage,'acquisition.elapsed_s':self.elapsed,'acquisition.failure':self.failure}
  d.update(self._panel_values())
  return copy.deepcopy({k:v for k,v in d.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _result(self):return dict(phase=self.phase,complete=self.phase=='succeeded',elapsed_s=self.elapsed,points=copy.deepcopy(self.points),freezing_temperature_k=self.freezing,osmolality_mol_kg=self.osm,quality='estimated' if self.phase=='succeeded' else 'partial_or_unavailable',template_source='Hamer/Wu1972 aqueous osmotic coefficients at298.15K; sample identity withheld',evidence='published reference curves + explicit thermal/temperature-transfer surrogate + observed-peak calibration',warnings=['Reference coefficients at298.15K; freezing-temperature transfer not calibrated','No full mixed-salt speciation/precipitation; additive particle-effect approximation','Thermal rates, sensor dynamics and deterministic faults are engineering inputs','Manufacturer precision is declared specification, not experimentally verified performance'])
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[k][0],a);before=copy.deepcopy(self.__dict__)
  try:
   r=self._invoke(k,a);validate(CAPS[k][1],r)
   if CAPS[k][4] not in ['query','analysis']:self.revision+=1
   return r
  except Exception:self.__dict__.clear();self.__dict__.update(before);raise
 def _foreign_run(self):
  """An agent may not stop (Start/Stop key, acquisition.stop) another agent's running measurement; the same check for the direct call and the
  panel key, because both run with the session caller in _caller.  The power switch (power.set models it) cuts the power: never refused, the
  run ends as on a power loss (1.1.2, lead rule 10-19)."""
  c=self._caller;o=self._owners.get(self.operation_id)
  return self.phase=='running' and c is not None and self._principals.get(c)=='agent' and o is not None and o!=c
 def _invoke(self,k,a):
  if k=='process.measure':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='template.evaluate':
   p=self.phi(a['substance_id'],a['molality_mol_kg']);t=self.templates[a['substance_id']];return dict(osmotic_coefficient=p,osmolality_mol_kg=2*a['molality_mol_kg']*p,temperature_k=298.15,template_source=t['source']['doi']+' Table'+str(t['source']['table']),evidence=t['evidence'])
  if k=='plant.configure':
   if self.plant or self.sample or self.powered or not a['accept_thermal_surrogate']:raise ActionRejected('INITIALIZATION_ONLY')
   self.plant=copy.deepcopy(a);return {'configured':True}
  if k=='power.set':
   if self.powered!=a['enabled']:self.warmup=0.
   self.powered=a['enabled']
   if not self.powered and self.phase=='running':self.phase='cancelled';self.stage='thawing';self.records[self.operation_id]=self._result()
   return a
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   if self._foreign_run():raise ActionRejected('ACCESS_DENIED')
   self.phase='cancelled';self.stage='thawing';self.records[self.operation_id]=self._result();return {'phase':self.phase}
  if k=='head.position':   # a hand action (1.1.2 sweep, lead 10-19): never refused during a run or with a frozen sample (the tube stays on the head); out of the cooling cavity the sample is no longer cooled (_tick)
   self.pose=a['position'];self.q={'measuring':[0.,0.],'raised':[0.,.06],'parked':[.12,.04]}[self.pose];return a
  if self.phase=='running':raise ActionRejected('BUSY')
  if k in PANEL_DEVICE:return getattr(self,'_cmd_'+k.replace('.','_'))(**a)
  if k=='method.configure':
   if self.sample:raise ActionRejected('UNLOAD_REQUIRED')
   if a['freeze_k']<a['cooling_limit_k'] or any(abs((a[k]-TF)*2-round((a[k]-TF)*2))>1e-8 for k in ['freeze_k','cooling_limit_k']) or abs(a['sample_interval_s']/DT-round(a['sample_interval_s']/DT))>1e-8:raise ActionRejected('METHOD_GRID')
   if not self.method or any(a[k]!=self.method[k] for k in ['sample_volume_m3','freeze_k','cooling_limit_k']):self.calibration=[];self.cal_owner={2:None,3:None};self.cal_relative=None
   self.method=copy.deepcopy(a);return dict(configured=True,calibration_valid=self.calibrated())
  if k=='sample.load':
   if not self.plant or not self.method or self.sample or self.pose=='measuring' or not a['accept_temperature_transfer']:raise ActionRejected('LOAD_PRECONDITION')
   if a['volume_m3']!=self.method['sample_volume_m3']:raise ActionRejected('VOLUME_MISMATCH')
   if a['kind']=='reference' and a['components'] or a['kind']=='solution' and (not a['components'] or a['reference_osmolality_mol_kg']!=0):raise ActionRejected('SAMPLE_INVALID')
   if len({v['substance_id'] for v in a['components']})!=len(a['components']):raise ActionRejected('DUPLICATE_SPECIES')
   sol={}
   if a['kind']=='reference':sol={'reference_effective_particles':a['reference_osmolality_mol_kg']*a['water_mass_kg']}
   else:
    for c in a['components']:
     self.phi(c['substance_id'],c['molality_mol_kg']);maximum=self.templates[c['substance_id']]['rows'][-1]['molality_mol_kg']
     if c['molality_mol_kg']>.8*maximum:raise ActionRejected('FREEZING_DOMAIN_MARGIN')
     sol[c['substance_id']]=c['molality_mol_kg']*a['water_mass_kg']
   self.solutes=sol;self.water=a['water_mass_kg']
   if self.osmo(self.water)>2.+1e-12:raise ActionRejected('OUT_OF_RANGE')
   self.sample=copy.deepcopy(a);self.ice=0.;self.nucleated=False;self.temp=a['initial_temperature_k'];self.H=self.water*CP*(self.temp-TF);self.loaded_H+=self.H;self.loaded_water+=self.water
   for sid,n in sol.items():self.loaded_solutes[sid]=self.loaded_solutes.get(sid,0.)+n
   self.sensor=self.temp;self.observed=None;self.thawed=True;self.mixed=True;self.stage='idle';return {'loaded':True}
  if k=='sample.unload':
   if not self.sample or self.pose=='measuring' or not self.thawed:raise ActionRejected('THAW_RAISE_REQUIRED')
   w=self.water;self.returned_water+=w;self.returned_H+=self.H
   for sid,n in self.solutes.items():self.returned_solutes[sid]=self.returned_solutes.get(sid,0.)+n
   self.sample=None;self.water=0.;self.ice=0.;self.solutes={};self.H=0.;self.temp=None;self.sensor=None;self.observed=None;return {'returned_water_kg':w}
  if k=='vibrator.pulse':
   if not self.powered or not self.sample or not self.thawed:raise ActionRejected('TUBE_THAW_REQUIRED')
   self.vib_until=self.time_s+5.;self.mixed=True;return {'active':True}
  if k=='calibration.accept':
   known=a['known_osmolality_mol_kg']
   if self.phase!='succeeded' or self.purpose!='calibration' or self.freezing is None:raise ActionRejected('CALIBRATION_RUN_REQUIRED')
   if abs(known/.05-round(known/.05))>1e-8:raise ActionRejected('CALIBRATION_GRID')
   new=sorted([v for v in self.calibration if v['known']!=known]+[dict(known=known,temperature_k=self.freezing)],key=lambda v:v['known'])
   if len(new)>3 or any(b['temperature_k']>=c['temperature_k']-.001 for c,b in zip(new,new[1:])):raise ActionRejected('CALIBRATION_INVALID')
   self.calibration=new;self.cal_handled=True;self._own(known);self._log(['Calibrated %04d mOsmol'%round(known*1000)]);return dict(points=len(new),valid=self.calibrated())
  if k=='acquisition.start':
   if not self.plant or not self.method or not self.sample or not self.powered or self.warmup<300.-1e-8 or self.pose!='measuring' or not self.thawed or not self.mixed:raise ActionRejected('NOT_READY')
   if a['purpose']=='sample' and not self.calibrated():raise ActionRejected('NOT_CALIBRATED')
   self.serial+=1;self.operation_id=f'{self.instance_id}:op:{self.serial}';self.phase='running';self.stage='cooling';self.elapsed=0.;self.points=[];self.failure='';self.freezing=None;self.osm=None;self.result_time=None;self.peak=None;self.nucleated=False;self.thawed=False;self.measuring_s=0.;self.purpose=a['purpose'];self.pending=0.;self.start_time=self.time_s;self.cal_handled=self.pre_handled=False;self.rel_pct=None;return dict(phase=self.phase,operation_id=self.operation_id)
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def calibrated_osm(self,observed):
  a,b=self.calibration[:2]
  for lo,hi in zip(self.calibration,self.calibration[1:]):
   a,b=lo,hi
   if observed>=b['temperature_k']:break
  x=a['known']+(observed-a['temperature_k'])*(b['known']-a['known'])/(b['temperature_k']-a['temperature_k']);return round(x*1000)/1000.
 def _fail(self,code):self.failure=code;self.phase='failed';self.stage='thawing';self._log([{'FREEZE':'FREEZE','NO_FREEZE':'NO FREEZE'}.get(code,'ERROR')])
 def _heat(self,delta):
  h=self.H+delta
  try:t,ice=self.equilibrium(h)
  except ActionRejected:self._fail('SOURCE_RANGE');return
  self.H=h;self.temp=t;self.ice=ice
  if delta>=0:self.heat_added+=delta
  else:self.heat_removed-=delta
 def _tick(self):
  self.time_s+=DT
  if self.powered:self.warmup+=DT
  tgt=self.method['cooling_limit_k'] if self.powered and self.method and self.stage in ('cooling','measuring') else STANDBY_K;self.cooler=tgt+(self.cooler-tgt)*math.exp(-DT/COOLER_TAU_S)
  if not self.sample or not self.powered or not self.plant:return
  if self.phase=='running':self.elapsed+=DT
  if self.stage in ('cooling','measuring') and self.pose!='measuring':   # head lifted out of the cooling cavity during the run: the sample warms towards 20 C (thaw rate, assumption); cooling never reaches the freeze trigger (COOLER TIMEOUT after 5 min from START, manual 9.1), a lifted plateau melts and raises the peak (OUT OF RANGE or a low value)
   self._heat(min(self.water*CP*self.plant['warming_rate_k_s']*DT,self.water*CP*20.-self.H))
   if self.stage=='measuring':self.measuring_s+=DT
   elif self.elapsed>=300.:self._fail('COOLER_TIMEOUT')
  elif self.stage=='cooling':
   rate=self.water*CP*self.plant['cooling_rate_k_s'];floor=self.water*CP*(self.method['cooling_limit_k']-TF);self._heat(max(-rate*DT,floor-self.H))
   tf0=TF-KF*self.osmo(self.water)
   if self.plant['fault']=='early_freeze' and self.temp<tf0-.05:self.nucleated=True;self.temp,self.ice=self.equilibrium(self.H);self._fail('FREEZE')
   elif self.temp<=self.method['freeze_k']+1e-10:
    if self.plant['fault']=='no_freeze':self._fail('NO_FREEZE')
    else:self.nucleated=True;self.temp,self.ice=self.equilibrium(self.H);self.stage='measuring';self.measuring_s=0.;self.vib_until=self.time_s+1.;self.mixed=False
   elif self.elapsed>=300.:self._fail('COOLER_TIMEOUT')
  elif self.stage=='measuring':self._heat(-self.water*CP*self.plant['cooling_rate_k_s']*.03*DT);self.measuring_s+=DT
  elif self.stage=='thawing':
   target=self.water*CP*20.;self._heat(min(self.water*CP*self.plant['warming_rate_k_s']*DT,target-self.H))
   if self.ice<1e-12 and self.temp>=293.15-1e-8:
    self.thawed=True;self.stage='ready'
    if self.phase=='running':self.phase='succeeded'
  self.sensor=self.temp+(self.sensor-self.temp)*math.exp(-DT/self.plant['sensor_tau_s']);raw=TF+self.plant['sensor_gain']*(self.sensor-TF)+self.plant['sensor_bias_k'];raw=raw+self.head_offset if self.head_offset else raw;self.observed=round(raw/.001)*.001;self.sample_window=[self.time_s-DT,self.time_s]
  if self.stage=='measuring':
   self.peak=self.observed if self.peak is None else max(self.peak,self.observed)
   if self.measuring_s>=20.-1e-8:
    self.freezing=self.peak;self.result_time=self.time_s
    if self.purpose=='sample' and self.cal_relative is not None:
     t0=self.calibration[0]['temperature_k'];self.rel_pct=round(100.*(t0-self.freezing)/(t0-self.cal_relative),1);self._log(['Measured %5.1f %%'%self.rel_pct])
    elif self.purpose=='sample':
     value=self.calibrated_osm(self.freezing)
     if not 0<=value<=2.:self._fail('OUT_OF_RANGE')
     else:self.osm=value;self._log(['Measured %04d mOsmol'%round(value*1000)])
    self.stage='thawing'
  if self.phase=='running' and self.elapsed>=300.:self._fail('MEASUREMENT_TIMEOUT')
  if self.operation_id and self.elapsed>0 and self.phase=='running' and abs(self.elapsed/self.method['sample_interval_s']-round(self.elapsed/self.method['sample_interval_s']))<1e-6:self.points.append(dict(time_s=round(self.elapsed,8),temperature_k=self.observed,stage=self.stage))
  if self.operation_id and self.phase!='running':self.records[self.operation_id]=self._result()
 # ---- front panel (1.1.0): device settings, clock, calibration fields, pre-calibration, output log
 def _date(self):return datetime.datetime.fromisoformat(CLOCK_EPOCH)+datetime.timedelta(seconds=int(math.floor(self.time_s+self.dev['clock_offset_s']+1e-6)))
 def _log(self,lines):
  if not self.powered:return
  log=self.dev['log'];log.append(dict(time_s=round(self.time_s,6),port=self.dev['data_output'],lines=list(lines)+[self._date().strftime('%d.%m.%y %H:%M:%S')]));del log[:-LOG_MAX]
 def _own(self,known):
  if known==0.:return
  f=next((f for f in (2,3) if self.cal_fields[f]==known),None) or next((f for f in (2,3) if self.cal_owner[f] is None),None)
  if f is not None:
   self.cal_owner={g:(None if g!=f and self.cal_owner[g]==known else self.cal_owner[g]) for g in (2,3)};self.cal_owner[f]=known;self.cal_fields[f]=known
 def _pre_result(self):
  if self.purpose!='pre_calibration' or self.pre_handled or self.phase not in ('succeeded','failed'):return None
  return 'successful' if self.phase=='succeeded' and self.freezing is not None and abs(self.freezing-TF)<PRE_TOL_K else 'not_successful'
 def _cal_pending(self):return self.phase=='succeeded' and self.purpose=='calibration' and self.freezing is not None and not self.cal_handled
 def _panel_values(self):
  return {'device.settings':{k:self.dev[k] for k in ('data_output','result_unit','temp_unit')},'device.datetime':self._date().strftime('%d.%m.%y %H:%M:%S'),'device.info':dict(serial=SERIAL,firmware=FIRMWARE),'device.output_log':copy.deepcopy(self.dev['log']),'cooler.temperature_k':self.cooler,'calibration.fields':[0.,self.cal_fields[2],self.cal_fields[3]],'calibration.relative_mode':self.cal_relative is not None or 'relative' in self.cal_fields.values(),'calibration.pending':self._cal_pending(),'calibration.pre_result':self._pre_result(),'calibration.head_offset_k':self.head_offset,'acquisition.purpose':self.purpose,'osmolality.relative_percent':self.rel_pct}
 def _cmd_device_configure(self,data_output,result_unit,temp_unit):
  if not self.powered:raise ActionRejected('POWER_OFF')
  self.dev.update(data_output=data_output,result_unit=result_unit,temp_unit=temp_unit);return dict(data_output=data_output,result_unit=result_unit,temp_unit=temp_unit)
 def _cmd_device_set_clock(self,year,month,day,hour,minute,second):
  if not self.powered:raise ActionRejected('POWER_OFF')
  try:t=datetime.datetime(year,month,day,hour,minute,second)
  except ValueError:raise ActionRejected('INVALID_DATE') from None
  self.dev['clock_offset_s']=(t-datetime.datetime.fromisoformat(CLOCK_EPOCH)).total_seconds()-math.floor(self.time_s+1e-6);return {'datetime':self._date().strftime('%d.%m.%y %H:%M:%S')}
 def _cmd_calibration_select_field(self,field,value):
  if not self.powered:raise ActionRejected('POWER_OFF')
  if value not in ('off','relative') and abs(value/.05-round(value/.05))>1e-8:raise ActionRejected('CALIBRATION_GRID')
  drop=set()
  if value=='off':drop={self.cal_owner[field]};self.cal_owner[field]=None;self.cal_fields[field]=None
  elif value=='relative':
   drop={self.cal_owner[2],self.cal_owner[3]};self.cal_owner={2:None,3:None};self.cal_fields={g:('relative' if g==field else None) for g in (2,3)};self.cal_relative=None
  else:
   if self.cal_fields[field]=='relative':self.cal_relative=None
   self.cal_fields[field]=float(value)
  self.calibration=[p for p in self.calibration if p['known']==0. or p['known'] not in drop];return {'fields':[0.,self.cal_fields[2],self.cal_fields[3]]}
 def _cmd_calibration_field_accept(self,field):
  if not self._cal_pending():raise ActionRejected('CALIBRATION_RUN_REQUIRED')
  if field==1:known=0.
  else:
   known=self.cal_fields[field]
   if known is None:raise ActionRejected('CALIBRATION_FIELD_OFF')
   if known=='relative':
    if not self.calibration or self.calibration[0]['known']!=0. or self.freezing>=self.calibration[0]['temperature_k']-.001:raise ActionRejected('CALIBRATION_INVALID')
    self.cal_relative=self.freezing;self.cal_handled=True;self._log(['Calibrated  %%%% relative']);return dict(points=len(self.calibration),valid=self.calibrated())
  old=self.cal_owner.get(field);keep=[v for v in self.calibration if v['known']!=known and (field==1 or v['known']!=old or old==0.)]
  new=sorted(keep+[dict(known=known,temperature_k=self.freezing)],key=lambda v:v['known'])
  if len(new)>3 or any(b['temperature_k']>=c['temperature_k']-.001 for c,b in zip(new,new[1:])):raise ActionRejected('CALIBRATION_INVALID')
  self.calibration=new;self.cal_handled=True
  if field!=1:self.cal_owner[field]=known
  self._log(['Calibrated %04d mOsmol'%round(known*1000)]);return dict(points=len(new),valid=self.calibrated())
 def _cmd_calibration_discard(self):
  if not self._cal_pending():raise ActionRejected('NO_PENDING')
  self.cal_handled=True;return {'pending':False}
 def _cmd_calibration_pre_accept(self):
  if self._pre_result()!='successful':raise ActionRejected('NO_PENDING')
  self.head_offset=self.head_offset+TF-self.freezing;self.calibration=[];self.cal_owner={2:None,3:None};self.cal_fields={2:None,3:None};self.cal_relative=None;self.pre_handled=True;return {'head_offset_k':self.head_offset}
 # ---- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
 @property
 def panel(self):
  p=_PANELS.get(self)
  if p is None and PANEL_SPEC:p=_PANELS[self]=_panel_ui.PanelUI(PANEL_SPEC,self)
  return p
 def panel_read(self):
  d=self._date();out=self.read('agent');out.update({'host.powered':self.powered,'host.elapsed_text':'%d:%02d'%divmod(int(self.elapsed),60),'host.elapsed_mmss':'%02d:%02d'%divmod(int(self.elapsed),60),'host.clock':dict(year=d.year,month=d.month,day=d.day,hour=d.hour,minute=d.minute,second=d.second),'host.method':copy.deepcopy(self.method)});return out
 def panel_invoke(self,capability_id,arguments):
  try:return self.invoke(capability_id,**arguments)
  except ActionRejected as exc:raise _panel_ui.PanelRejected(str(exc)) from None
 def panel_time(self):return self.time_s
 def _panel_entry(self,capability_id,arguments):
  validate(PANEL_CAPS[capability_id][0],arguments);out=getattr(self,'_cmd_'+capability_id.replace('.','_'))(**arguments)
  if PANEL_CAPS[capability_id][4]=='device_command':self.revision+=1
  return out
 def _panel(self,gesture,*args):
  try:return dict(accepted=bool(gesture(*args)))
  except _panel_ui.PanelError:raise ActionRejected('INVALID_ARGUMENT') from None
 def _cmd_panel_press(self,input_id,duration_s=.1):return self._panel(self.panel.press,input_id,float(duration_s))
 def _cmd_panel_turn(self,input_id,detents):return self._panel(self.panel.turn,input_id,int(detents))
 def _cmd_panel_switch(self,input_id,position):return self._panel(self.panel.switch,input_id,position)
 def _cmd_panel_touch(self,display_id,x,y,duration_s=.1,to_x=None,to_y=None):
  if (to_x is None)!=(to_y is None):raise ActionRejected('INVALID_ARGUMENT')
  return self._panel(self.panel.touch,display_id,int(x),int(y),float(duration_s),None if to_x is None else (int(to_x),int(to_y)))
 def _cmd_panel_capture(self,display_id):
  try:data,w,h=self.panel.png_bytes(display_id)
  except _panel_ui.PanelError:raise ActionRejected('INVALID_ARGUMENT') from None
  sha=hashlib.sha256(data).hexdigest();folder=Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir())/'chemistry_sim_panel'/self.instance_id;folder.mkdir(parents=True,exist_ok=True);path=folder/f'{display_id}_{sha[:16]}.png'
  if not path.exists():path.write_bytes(data)
  return dict(display_id=display_id,time_s=self.time_s,image=dict(path=str(path),format='png',width=w,height=h,sha256=sha))
 def _cmd_panel_frame(self):return dict(frame=self.panel.frame())
 def step(self,dt):
  validate(number('s',0,86400),dt)
  if dt==0:return
  self.pending+=dt
  while self.pending+1e-12>=DT:self._tick();self.pending=max(0.,self.pending-DT)
  self.revision+=1

class KnauerK7400sSession:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,i):
  if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  m=export_knauer_k7400s_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
 def read(self,*,instance_id,state_ids=None):
  self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
  if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
  if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
  live=['temperature.k','temperature.display_c','sample.true_temperature_k'];last=['freezing.temperature_k','osmolality.mol_kg','osmolality.display_mosm_kg']
  return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='unavailable' if v[k] is None else 'stale' if k in live and not h.powered else 'estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=h.result_time if k in last else h.sample_window[-1] if k in live and h.sample_window else h.time_s,sample_window_s=copy.deepcopy(h.sample_window) if k in live else None) for k in ids})

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
   before=h.operation_id;h._caller=self._principal
   try:result=h._panel_entry(capability_id,arguments) if capability_id in PANEL_CAPS else h.invoke(capability_id,**arguments)
   finally:h._caller=None
   op=result.get('operation_id')
   if op:h._owners[op]=self._principal
   elif capability_id in PANEL_CAPS and h.operation_id and h.operation_id!=before:h._owners[h.operation_id]=self._principal   # a run started by the Start/Stop key belongs to the caller
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





ON_STEP_EXTRA={'acquisition.start':['acquisition.purpose','calibration.pending','calibration.pre_result','osmolality.relative_percent','osmolality.display_mosm_kg','device.output_log','cooler.temperature_k'],'power.set':['cooler.temperature_k','device.datetime'],'device.set_clock':['device.datetime'],'method.configure':['cooler.temperature_k']}
ON_STEP_ZH={'acquisition.start':'运行结束时给出校准待确认、预校准结果、相对%结果与输出记录；冷却与测量阶段冷却腔趋向cooling limit','power.set':'通电后冷却腔温度与设备时钟随step推进','device.set_clock':'设备时钟随step走时','method.configure':'冷却腔在运行中趋向新的cooling limit'}
def export_knauer_k7400s_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions='Aqueous reference coefficients298.15K; Kf1.858K/(mol/kg)',origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'KnauerK7400sSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description='temperature: last0.1s tick; freezing/osmolality: retained peak inference time'),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNKNOWN_OPERATION','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','INITIALIZATION_ONLY','BUSY','UNKNOWN_REFERENCE','NOT_READY','NOT_RUNNING','METHOD_GRID','SOURCE_RANGE','LOAD_PRECONDITION','THAW_REQUIRED','VOLUME_MISMATCH','SAMPLE_INVALID','DUPLICATE_SPECIES','SOURCE_MARGIN','OUT_OF_RANGE','UNLOAD_REQUIRED','TUBE_THAW_REQUIRED','CALIBRATION_RUN_REQUIRED','CALIBRATION_GRID','CALIBRATION_INVALID','NOT_CALIBRATED','POWER_OFF','INVALID_DATE','CALIBRATION_FIELD_OFF','NO_PENDING']
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  unsupported=k in UNSUPPORTED;asyn=k=='acquisition.start'
  caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.knauer_k7400s.KnauerK7400sSession.invoke',invocation=f'session.invoke(instance_id="osmometer_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if kind=='query' or unsupported else [dict(description_zh='显式热过程/方法/有限样品，5min预热、校准、头位置、解冻及数据域检查；拒绝请求原子回退',state_ids=['power.enabled','method.configured','sample.loaded','sample.thawed','calibration.valid','head.position','acquisition.phase'],enforced_by='runtime',check='atomic Runtime.invoke and trusted Session')],state_effects=[dict(state_id=x,timing='immediate',description_zh='受检事务改变状态') for x in effects],observes=[],model_effects=dict(mode='none',description_zh='等待独立场景验证',binding_ids=[]),execution=dict(mode='asynchronous' if asyn else 'synchronous',time_domain='simulation',completion_criterion='观测成核回升峰温后通电解冻至20°C待机' if asyn else '受检事务返回，查询不推进时间',completion_state_ids=['acquisition.phase'] if asyn else [],timeout_s=300. if asyn else None,cancellation='supported' if asyn else 'unsupported',retry='same_request_id',resources=['host_clock','finite_water','finite_solutes','enthalpy','request_cache'],partial_failure_zh='保留水/溶质/冰/热量与部分曲线；断电暂停主动热交换；去重限当前宿主'),errors=[dict(code=x,meaning_zh=x,side_effects='none') for x in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=['工程热动力学；温度转移、真实噪声、厂家精密度和故障概率未验证'],source_ids=['manufacturer_primary','simulation_assumption'])
  if asyn:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='固定0.1s格推进有限热过程与温度观测') for x in ['temperature.k','freezing.temperature_k','osmolality.mol_kg','sample.ice_kg','sample.thawed','acquisition.elapsed_s','acquisition.phase']]
  caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh=ON_STEP_ZH[k]) for x in ON_STEP_EXTRA.get(k,[])]
 for k,(inp,out,c,z,kind,vis,ex,effects) in PANEL_CAPS.items():
  gesture=kind=='device_command';imm=sorted({e for x in _panel_ui.spec_capabilities(PANEL_SPEC) for e in CAPS[x][7]}) if gesture else [];later=sorted({e for x in _panel_ui.spec_capabilities(PANEL_SPEC) for e in ON_STEP_EXTRA.get(x,[])}) if gesture else []
  caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.knauer_k7400s.KnauerK7400sSession.invoke',invocation=f'session.invoke(instance_id="osmometer_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[dict(description_zh='按panel_ui.json的面板规则：断电时只有电源开关有效；运行中只有Start/Stop（停止）有效，Vib无功能；被调用的命令各自检查前提，被拒时显示> ERROR <且状态不变',state_ids=['power.enabled','acquisition.phase'],enforced_by='runtime',check='KnauerK7400sRuntime._panel_entry（面板引擎_panel_ui.py）')] if gesture else [],state_effects=[dict(state_id=x,timing='immediate',description_zh='手势调用的命令立即改变的状态') for x in imm]+[dict(state_id=x,timing='on_step',description_zh='手势调用的命令在之后的step中推进的状态') for x in later],observes=[],model_effects=dict(mode='none',description_zh='LCD画面由panel.capture提供；3D屏幕纹理与按键关节触发手势属后续Matterix步骤',binding_ids=[]),execution=dict(mode='synchronous',time_domain='simulation',completion_criterion='手势瞬时完成（不推进仿真时钟）；被调用命令的过程由step推进',completion_state_ids=[],timeout_s=None,cancellation='unsupported',retry='same_request_id',resources=['panel'],partial_failure_zh='一个手势按面板规则可调用多个命令：已被接受的前序命令保留，被拒的命令不改变状态' if gesture else '只读'),errors=[dict(code=x,meaning_zh=x,side_effects='none') for x in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','REQUEST_CONFLICT','ACCESS_DENIED']],example_arguments=copy.deepcopy(ex),verification=[],limitations=['面板层：panel_ui.json（K-7400S说明书V3700 p.6、16-25与Fig. 9画面）解释按键并调用本运行层的命令；手册未写之处见panel_ui.json的assumptions'],source_ids=['manufacturer_primary','simulation_assumption'])
 mech=dict(component_ids=['controller','peltier','thermistor','sample_tube','stirring_wire'],state_ids=list(slots),meaning_zh='有限水/溶质/焓守恒、过冷成核回升、传感器峰温和两/三点观测校准',implementation='lumped',equations=['O=sum(2*m_i*phi_i(m_i,298.15K)); ideal reference particle inventory O_reference*water_mass','Tf=273.15-1.858*O(liquid_water); all solute retained in unfrozen water','H=water_mass*4184*(T-273.15)-ice_mass*333550; Cp and latent heat nominal constants','Nucleation: solve equilibrium T and ice from unchanged H; cooling/warming adds explicit heat ledger','Sensor dTs/dt=(T-Ts)/tau; gain/bias and0.001K ideal quantization explicit; peak=max(observed temperature during20s crystallization window)','O_inferred=piecewise-linear interpolation/extrapolation from independent standard labels and observed peak temperatures; quantization0.001mol/kg','Fixed0.1s host clock ticks; timeout300s; warmup300s; freeze/limit settings0.5K grid','loaded water/solute=active+returned; loaded H+heat_added-heat_removed-returned H=active H'],parameters={},source_ids=['manufacturer_primary','hamer_wu_1972','simulation_assumption'],limitations=['61 published critically evaluated aqueous uni-univalent electrolyte coefficient curves,529 reference points at298.15K; not61 directly measured K7400S freezing traces.','Temperature transfer to freezing and multi-solute additivity are engineering approximations; no full mixture speciation or precipitation.','Public source text OCR: ambiguous rows omitted; NaNO3 old table excluded because of later revision; original row locators retained.','Cooling/warming rates, sensor lag/gain/bias, heat capacity and latent heat nominal; latent heat/solute exclusion conserve the declared inventories.','Effective-particle calibration references use ideal finite particles, not a validated commercial-standard formulation.','Manufacturer SD/RSD/linearity are specifications only; noise, temperature calibration, pre-calibration head tuning, EuroOsmoRS, stochastic faults, robot and hardware not_run.','Head Y/Z are manual placement abstractions; microscopic vibrator amplitude and fluid/contact mechanics not calibrated.'])
 spec=importlib.util.spec_from_file_location('_osm_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'freezing_osmometry':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'。'
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'freezing_osmometry':mech})

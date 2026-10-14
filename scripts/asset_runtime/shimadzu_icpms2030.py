"""ICPMS-2030: sequential single-charge mass-channel counts, explicit isotope and response provenance."""
from __future__ import annotations
import ast,copy,csv,hashlib,importlib.util,io,json,math
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
SLUG='shimadzu_icpms2030';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-083';PROTOCOL_VERSION='1.0.0';ASSET_VERSION='1.0.0'
TEMPLATE_REL='assets/expansion_batch04/t3_analytical/shimadzu_icpms2030/output_templates/library.json'
R=8.31446261815324;MAX_RESULTS=256;MAX_CURVES=32;MAX_TRACE_POINTS=20000
class ActionRejected(ValueError): pass
BOOL={'type':'boolean'}; TEXT={'type':'string'}
def number(unit,lo=None,hi=None):
 s={'type':'number','x-unit':unit}
 if lo is not None:s['minimum']=lo
 if hi is not None:s['maximum']=hi
 return s
def integer(lo,hi):return dict(number('1',lo,hi),type='integer')
def enum(values):return {'type':'string','enum':values}
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
def arr(s,lo=0,hi=20000):return dict(type='array',items=s,minItems=lo,maxItems=hi)
def obj(fields):return dict(type='object',properties=fields,required=list(fields),additionalProperties=False)
def validate(s,v):
 if 'anyOf' in s:
  if v is None and any(x.get('type')=='null' for x in s['anyOf']):return
  return validate(next(x for x in s['anyOf'] if x.get('type')!='null'),v)
 t=s.get('type')
 if t in ['number','integer']:
  if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):raise ActionRejected('INVALID_ARGUMENT')
  if t=='integer' and type(v) is not int:raise ActionRejected('INVALID_ARGUMENT')
  if not s.get('minimum',-math.inf)<=v<=s.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='boolean' and type(v) is not bool:raise ActionRejected('INVALID_ARGUMENT')
 elif t=='string' and not isinstance(v,str):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='object':
  if not isinstance(v,dict) or not set(s['required'])<=v.keys() or not v.keys()<=s['properties'].keys():raise ActionRejected('INVALID_ARGUMENT')
  for k,x in v.items():validate(s['properties'][k],x)
 elif t=='array':
  if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
  for x in v:validate(s['items'],x)
 elif t=='null' and v is not None:raise ActionRejected('INVALID_ARGUMENT')
 if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')
PLANT=obj(dict(ambient_k=number('K',278.15,308.15),rf_ramp_w_s=number('W/s',1,100000),thermal_capacity_j_k=number('J/K',.01,10000),thermal_loss_w_k=number('W/K',.001,10000),absorbed_rf_fraction=number('1',0,1),ignition_s=number('s',.02,600),startup_stabilization_s=number('s',0,3600),uptake_m3_s=number('m^3/s',1e-12,1e-6),pump_volume_per_rad_m3=number('m^3',1e-12,1e-5),aerosol_fraction=number('1',.001,1),aerosol_residence_s=number('s',.001,100),plasma_residence_s=number('s',.001,10),atomization_yield=number('1',.001,1),ionization_fraction=number('1',0,1),ion_transmission=number('1',0,1),detector_efficiency=number('1',0,1),background_count_rate_s_inv=number('s^-1',0,1e9),detector_dead_time_s=number('s',0,1e-3),detector_saturation_rate_s_inv=number('s^-1',1,1e12),poisson_noise=BOOL,helium_analyte_loss_s_m3=number('s/m^3',0,1e10),helium_interference_loss_s_m3=number('s/m^3',0,1e10),helium_ramp_m3_s2=number('m^3/s^2',1e-12,1e-3),argon_inventory_mol=number('mol',0,1e6),helium_inventory_mol=number('mol',0,1e6),gas_reference_temperature_k=number('K',200,400),gas_reference_pressure_pa=number('Pa',50000,200000),vacuum_chamber_volume_m3=number('m^3',1e-6,1),vacuum_base_pressure_pa=number('Pa',1e-8,10),vacuum_decay_s_inv=number('s^-1',.001,1000),vacuum_ready_threshold_pa=number('Pa',1e-7,100),torch_speed_m_s=number('m/s',.0001,1),alignment_width_m=number('m',.0001,.1),coolant_available=BOOL,exhaust_available=BOOL,seed=integer(0,2147483647),accept_assumed_response=BOOL))
PLANT_EXAMPLE=dict(ambient_k=298.15,rf_ramp_w_s=5000.,thermal_capacity_j_k=.2,thermal_loss_w_k=.1,absorbed_rf_fraction=.5,ignition_s=.08,startup_stabilization_s=.10,uptake_m3_s=1e-8,pump_volume_per_rad_m3=1e-9,aerosol_fraction=.05,aerosol_residence_s=.04,plasma_residence_s=.02,atomization_yield=.95,ionization_fraction=.01,ion_transmission=.01,detector_efficiency=.8,background_count_rate_s_inv=10.,detector_dead_time_s=0.,detector_saturation_rate_s_inv=1e9,poisson_noise=False,helium_analyte_loss_s_m3=2e6,helium_interference_loss_s_m3=2e7,helium_ramp_m3_s2=1e-6,argon_inventory_mol=10.,helium_inventory_mol=1.,gas_reference_temperature_k=298.15,gas_reference_pressure_pa=101325.,vacuum_chamber_volume_m3=.01,vacuum_base_pressure_pa=.001,vacuum_decay_s_inv=40.,vacuum_ready_threshold_pa=.1,torch_speed_m_s=.01,alignment_width_m=.005,coolant_available=True,exhaust_available=True,seed=83,accept_assumed_response=True)
QUANT=obj(dict(isotope_id=TEXT,basis=enum(['element','isotope'])))
METHOD=obj(dict(mass_numbers=arr(integer(5,260),1,64),quantifications=arr(QUANT,1,32),dwell_s=number('s',.001,10),settling_s=number('s',0,10),sweeps=integer(1,1000),repeats=integer(1,20),stabilization_s=number('s',.02,600),flush_s=number('s',.02,600),rf_power_w=number('W',500,1400),argon_flow_m3_s=number('m^3/s',1e-6,.001),helium_flow_m3_s=number('m^3/s',0,10e-6/60),internal_standard_isotope_id=TEXT,internal_standard_concentration_kg_m3=number('kg/m^3',0,100),deadtime_correction=BOOL))
METHOD_EXAMPLE=dict(mass_numbers=[65,66],quantifications=[dict(isotope_id='cu_65',basis='element'),dict(isotope_id='zn_66',basis='element')],dwell_s=.02,settling_s=.002,sweeps=10,repeats=1,stabilization_s=.2,flush_s=.3,rf_power_w=1200.,argon_flow_m3_s=11e-3/60,helium_flow_m3_s=0.,internal_standard_isotope_id='',internal_standard_concentration_kg_m3=0.,deadtime_correction=True)
INTERFERENCE=obj(dict(mass_number=integer(5,260),incident_count_rate_s_inv=number('s^-1',0,1e12)))
SAMPLE=obj(dict(sample_id=TEXT,template_id=TEXT,kind=enum(['sample','standard','blank']),volume_m3=number('m^3',1e-12,.1),dilution_factor=number('1',1,1e6),internal_standard_reference_id=TEXT,internal_standard_dilution_factor=number('1',1,1e6),matrix_transmission=number('1',0,1),interferences=arr(INTERFERENCE,0,64),is_aqueous=BOOL,accept_natural_isotope_assumption=BOOL,accept_unknown_conditions=BOOL))
SAMPLE_EXAMPLE=dict(sample_id='unknown_solution',template_id='infant1849a_measured_digest',kind='sample',volume_m3=1e-5,dilution_factor=20.,internal_standard_reference_id='',internal_standard_dilution_factor=1.,matrix_transmission=1.,interferences=[],is_aqueous=True,accept_natural_isotope_assumption=True,accept_unknown_conditions=True)
STANDARD_EXAMPLE=dict(SAMPLE_EXAMPLE,sample_id='observed_reference',template_id='infant_cal4',kind='standard',dilution_factor=1.)
BLANK_EXAMPLE=dict(SAMPLE_EXAMPLE,sample_id='matrix_blank',template_id='',kind='blank',dilution_factor=1.)
TORCH=obj(dict(x_m=number('m',-.003,.003),y_m=number('m',-.003,.003),z_m=number('m',-.003,.003)));TORCH_EXAMPLE=dict(x_m=0.,y_m=0.,z_m=0.)
WINDOW=arr(number('s',0),2,2)
POINT=obj(dict(time_s=number('s',0),sample_window_s=WINDOW,acquiring=BOOL,mass_number=integer(5,260),counts=integer(0,10**16),count_rate_s_inv=number('s^-1',0),corrected_count_rate_s_inv=nullable(number('s^-1',0)),saturated=BOOL))
CHANNEL=obj(dict(mass_number=integer(5,260),counts=integer(0,10**16),dwell_time_s=number('s',0),corrected_count_rate_s_inv=nullable(number('s^-1'))))
ASSAY=obj(dict(points=arr(POINT,0,MAX_TRACE_POINTS),channels=arr(CHANNEL,1,64),complete=BOOL))
CONCENTRATION=obj(dict(isotope_id=TEXT,element=TEXT,basis=TEXT,mass_number=integer(5,260),concentration_kg_m3=nullable(number('kg/m^3')),sd_kg_m3=nullable(number('kg/m^3',0)),cv=nullable(number('1',0)),net_count_rate_s_inv=nullable(number('s^-1')),internal_standard_ratio=nullable(number('1')),quality=TEXT))
RESULT=obj(dict(phase=TEXT,stage=TEXT,complete=BOOL,concentrations=arr(CONCENTRATION,0,32),assays=arr(ASSAY,0,20),active_assay=nullable(ASSAY),qc=arr(TEXT,0,100),evidence=TEXT,template_source=TEXT,acquisition_method_json=TEXT))
SPECTRUM=obj(dict(channels=arr(CHANNEL,0,64),sample_windows_s=arr(WINDOW,0,MAX_TRACE_POINTS),evidence=TEXT,coverage=TEXT))
MASS_ROW=obj({k:(TEXT if k=='isotope_id' else number('kg')) for k in ['isotope_id','registered','sample','aerosol','plasma','waste','exhaust','removed','residual']})
VOLUME=obj({k:number('m^3') for k in ['registered','sample','aerosol','waste','evaporated_liquid_equivalent','removed','residual']});GAS=obj({k:number('mol') for k in ['initial','remaining','consumed','residual']})
LEDGER=obj(dict(isotopes=arr(MASS_ROW,0,400),liquid_volume_m3=VOLUME,argon_mol=GAS,helium_mol=GAS,vacuum_gas_mol=GAS,rf_input_j=number('J',0),absorbed_heat_j=number('J',0),lost_heat_j=number('J'),stored_heat_j=number('J'),thermal_residual_j=number('J'),active_template_id=nullable(TEXT),evidence=TEXT))
CAL_OBSERVATION=obj(dict(reference_id=TEXT,dilution_factor=number('1',1,1e6),channels=arr(CHANNEL,1,64)))
CAPS={}
def cap(k,ins,outs,components,description,kind='device_command',visibility='agent',example=None):CAPS[k]=[ins,outs,components,description,kind,visibility,example or {},[]]
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body','rf_generator','quadrupole'],'可信宿主一次性明确离子化/传输/碰撞/计数与热流代理，非厂家标定','configuration','evaluator',PLANT_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_inlet'],'装入已外部配制最终溶液；真实元素浓度及天然丰度拆分有条件假设，源未知保持缺测','device_command','evaluator',SAMPLE_EXAMPLE)
cap('sample.unload',obj({'recipient_id':TEXT}),obj({'unloaded':BOOL,'volume_m3':number('m^3',0)}),['sample_inlet'],'仅返回未耗供样，不返还离子化或废弃物','device_command','evaluator',{'recipient_id':'host'})
cap('fluidics.clean',obj({}),obj({'removed_mass_kg':number('kg',0),'removed_volume_m3':number('m^3',0)}),['spray_chamber','torch'],'冷态外部清理残留到废液仓；不冒原厂自动维护','device_command','evaluator')
cap('cover.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['plasma_cover'],'罩开闭；保守宿主安全策略开罩熄炬并停止采集，不声称实机电子联锁验证','device_command','evaluator',{'opened':False})
cap('torch.position',TORCH,obj({'positioning':BOOL}),['torch_x','torch_y','torch_z'],'自动XYZ调整目标；有限位置推进，工程行程/响应，不伪测得最优对准',example=TORCH_EXAMPLE)
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','rf_generator'],'逻辑供电与安全断电；RF有限衰减',example={'enabled':True})
cap('vacuum.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['quadrupole'],'真空宿主服务目标，理想单腔压力有限演化；非实测多级泵曲线',example={'enabled':True})
cap('method.configure',METHOD,obj({'configured':BOOL}),['quadrupole','collision_cell','rf_generator','detector'],'选择整数质量数/驻留/He/RF/定量同位素与内标；质量数单位1，非假SI m/z',example=METHOD_EXAMPLE)
cap('plasma.ignite',obj({}),obj({'igniting':BOOL}),['torch','rf_generator'],'供气/冷却/排风/闭罩/真空就绪下有限点炬')
cap('plasma.extinguish',obj({}),obj({'lit':BOOL}),['torch','rf_generator'],'熄炬取消并保留已转移同位素质量及完整计数窗')
cap('measurement.start',obj({}),obj({'operation_id':TEXT}),['pump_rotor','torch','collision_cell','quadrupole','detector'],'四极杆顺序质量通道驻留计数与多元素/同位素实测标准标定反演')
cap('measurement.stop',obj({}),obj({'phase':TEXT}),['pump_rotor','detector'],'停止泵供样与本次计数，保留部分计数及残料')
cap('process.measure',obj({}),obj(dict(phase=TEXT,stage=TEXT,progress=number('1',0,1),count_rate_s_inv=nullable(number('s^-1',0)),concentrations=arr(CONCENTRATION,0,32))),['display_panel'],'纯读进度、离子计数率与推断浓度','query')
cap('result.read',obj({}),RESULT,['display_panel'],'读取完整重复/部分计数与冻结方法对应的推断结果','query')
cap('spectrum.read',obj({}),SPECTRUM,['detector'],'已选择质量通道的实际顺序计数谱，非同时全质量采集','query')
cap('result.export',obj({'format':enum(['json','csv'])}),obj({'format':TEXT,'content':TEXT}),['display_panel'],'导出实际计数数组为JSON/CSV字符串，不写任意外部路径','query',example={'format':'json'})
cap('blank.read',obj({}),obj({'valid':BOOL,'blank_json':TEXT}),['display_panel'],'纯读对应响应条件的实际计数空白','query')
cap('calibration.fit',obj({'curve_id':TEXT,'observations':arr(CAL_OBSERVATION,3,20),'accept_same_response_conditions':BOOL}),obj({'curve_id':TEXT,'isotope_count':integer(1,32),'minimum_r_squared':number('1')}),['display_panel'],'由真实标准浓度与实际净计数率/内标比值拟合，禁止把天然丰度当浓度校准','analysis',example={'curve_id':'observed','observations':[],'accept_same_response_conditions':True})
cap('calibration.select',obj({'curve_id':TEXT}),obj({'selected':TEXT}),['display_panel'],'选择匹配质量通道/He/RF/对准/内标方法的观察标线',example={'curve_id':'observed'})
cap('calibration.read',obj({'curve_id':TEXT}),obj({'curve_json':TEXT}),['display_panel'],'纯读真实供给的标准观测与拟合参数','query',example={'curve_id':'observed'})
cap('records.list',obj({}),obj({'record_ids':arr(TEXT,0,MAX_RESULTS),'curve_ids':arr(TEXT,0,MAX_CURVES)}),['display_panel'],'实例独立的工程容量目录','query')
cap('records.read',obj({'record_id':TEXT}),RESULT,['display_panel'],'纯读历史完整结果及当时条件','query',example={'record_id':'record_1'})
cap('records.remove',obj({'record_id':TEXT}),obj({'removed':BOOL}),['display_panel'],'删除指定结果记录；受权限保护异步终态不删除',example={'record_id':'record_1'})
cap('template.read',obj({'substance_id':TEXT}),obj({'template_json':TEXT}),['display_panel'],'原子丰度/质量与源浓度分开查询；歧义基准不伪物理样本','query',example={'substance_id':'cu_65'})
cap('product.read',obj({}),LEDGER,['spray_chamber','torch'],'仅可信宿主查看同位素/液体/Ar/He/理想真空气体与热代理账本','query','evaluator')
UNSUPPORTED={'autosampler.sequence','online_internal_standard.mix','oem.interference_correction','hardware.connect'}
for k in sorted(UNSUPPORTED):cap(k,obj({}),obj({}),['body'],'未安装选件或完整OEM算法/硬件未实现，明确拒绝')
for d,r in [(100.,1.),(10.,10.),(1.,100.)]:CAPS['calibration.fit'][6]['observations'].append(dict(reference_id='infant_cal4',dilution_factor=d,channels=[dict(mass_number=a,counts=int(r),dwell_time_s=1.,corrected_count_rate_s_inv=r) for a in [65,66]]))
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,origin='derived',visibility='agent',components=('display_panel',),write=None):SLOTS[k]=(s,u,origin,visibility,list(components),write);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','可信宿主代理已配置','setpoint',components=('body',));slot('sample.loaded',BOOL,'1','外部预配溶液连接存在','scene',components=('sample_inlet',));slot('power.enabled',BOOL,'1','逻辑供电','setpoint',components=('power_inlet',),write=('power.set','enabled'))
slot('cover.opened',BOOL,'1','等离子体维护罩已开','scene',components=('plasma_cover',));slot('cover.angle_rad',number('rad',0,1.7),'rad','维护罩工程转角；不是厂家实测行程','scene',components=('plasma_cover',));slot('pump.angle_rad',number('rad',0,2*math.pi),'rad','标准四通道泵转子的工程循环角','scene',components=('pump_rotor',))
for axis in ['x','y','z']:slot('torch.'+axis+'_m',number('m',-.003,.003),'m','火炬'+axis.upper()+'有限工程位移；真实自动调整存在，行程未厂家给出','scene',components=('torch_'+axis,))
slot('vacuum.pump_enabled',BOOL,'1','宿主真空泵使能','setpoint',components=('quadrupole',),write=('vacuum.set','enabled'));slot('vacuum.pressure_pa',number('Pa',0,200000),'Pa','分析器单腔理想绝压，非实际多级泵传感器','derived',components=('quadrupole',));slot('vacuum.ready',BOOL,'1','真空达到宿主工程阈值','derived',components=('quadrupole',))
for k in ['target_power_w','actual_power_w']:slot('rf.'+k,number('W',0,1400),'W','27MHz RF目标/有限输出；±0.3%非浓度噪声','setpoint' if k.startswith('target') else 'derived',components=('rf_generator',))
for k in ['lit','igniting']:slot('plasma.'+k,BOOL,'1','有限点炬的逻辑状态','derived',components=('torch',))
slot('plasma.temperature_k',number('K',0,1e9),'K','单热容RF热代理，非已测等离子体电子温度','derived',components=('torch',));slot('helium.flow_m3_s',number('m^3/s',0,10e-6/60),'m^3/s','碰撞He实际代理参考体积流量；气体温压由宿主声明','derived',components=('collision_cell',))
slot('mass.current_number',nullable(integer(5,260)),'1','当前四极杆选定整数质量数，单电荷假设；不叫SI m/z','setpoint',components=('quadrupole',));slot('method.mass_numbers',arr(integer(5,260),0,64),'1','顺序驻留质量通道列表','setpoint',components=('quadrupole',))
for k in ['phase','stage']:slot('measurement.'+k,TEXT,'1','测量过程状态')
slot('measurement.elapsed_s',number('s',0),'s','当前测量已推进时间');slot('measurement.progress',number('1',0,1),'1','独立重复/质量通道计划完成比例');slot('measurement.completed_repeats',integer(0,20),'1','真实完整重复次数')
slot('detector.count_rate_s_inv',nullable(number('s^-1',0)),'s^-1','最近完整驻留窗的真实模拟整数计数/窗长，非W','sensor',components=('detector',));slot('detector.counts',nullable(integer(0,10**16)),'1','最近窗模拟整数离子计数，非丰度或原子质量','sensor',components=('detector',));slot('detector.mass_number',nullable(integer(5,260)),'1','最近已采窗的质量数，可不同于当前四极杆下一目标','sensor',components=('detector',));slot('detector.last_sample_time_s',nullable(number('s',0)),'s','最近完整驻留结束时刻','sensor',components=('detector',))
slot('result.concentrations',arr(CONCENTRATION,0,32),'1','逐元素/同位素结果浓度内字段为kg/m³');slot('result.concentration_kg_m3',nullable(number('kg/m^3')),'kg/m^3','首个所选定量项的已校准推断浓度；无效为null')
for k in ['blank.valid','calibration.ready']:slot(k,BOOL,'1','当前响应方法匹配状态')
slot('storage.result_count',integer(0,MAX_RESULTS),'1','工程结果容量占用');slot('storage.curve_count',integer(0,MAX_CURVES),'1','工程标线容量占用');slot('safety.warning',TEXT,'1','当前有限模型安全或质量提示');slot('truth.ledger',LEDGER,'1','受限同位素与物料/能量真值仓','process_truth','evaluator',components=('torch',))

def _library():
 p=ROOT/TEMPLATE_REL
 return json.loads(p.read_text()) if p.exists() else {'isotopes':{},'compounds':{}}
def _move(v,t,rate,dt):return v+max(-rate*dt,min(rate*dt,t-v))
def _values(record):
 values=record.get('element_concentrations_kg_m3',{})
 if not isinstance(values,dict):raise ActionRejected('LIQUID_CONCENTRATION_REQUIRED')
 out={k:v for k,v in values.items() if v is not None}
 if not out:raise ActionRejected('LIQUID_CONCENTRATION_REQUIRED')
 for e,v in out.items():
  if not isinstance(e,str) or not e:raise ActionRejected('INVALID_REFERENCE')
  validate(number('kg/m^3',0,100),v)
 return out

class ShimadzuIcpms2030Runtime:
 def __init__(self,instance_id='icpms_01',library=None):
  self.instance_id=instance_id;self.library=_library() if library is None else copy.deepcopy(library);self.templates=self.library.get('compounds',{});self.isotopes=self.library.get('isotopes',{});self.time_s=0.;self.revision=0;self.plant=None;self.method=None;self.sample=None;self.powered=False;self.opened=False;self.torch=[0.,0.,0.];self.torch_target=[0.,0.,0.];self.pump_angle=0.;self.vacuum_pump=False;self.pressure=101325.;self.rf=0.;self.he_flow=0.;self.lit=False;self.igniting=False;self.ignition_elapsed=0.;self.stable_elapsed=0.;self.temperature=self.initial_temperature=298.15;self.rf_energy=self.absorbed_heat=self.lost_heat=0.;self.gas=dict(initial=0.,remaining=0.,consumed=0.);self.helium=dict(initial=0.,remaining=0.,consumed=0.);self.vacuum_gas=dict(initial=0.,remaining=0.,consumed=0.);self._masses={};self._volume=dict(registered=0.,aerosol=0.,waste=0.,evaporated_liquid_equivalent=0.,removed=0.);self.phase=self.stage='idle';self.elapsed_s=0.;self.warning='';self.current_mass=None;self.operation_id='';self._op_count=0;self._records={};self._curves={};self._blanks={};self._selected='';self._run_method=None;self._run_key=None;self._run_kind='';self._run_dilution=1.;self._run_blank={};self._run_curve=None;self._schedule=[];self._plan_index=0;self._stage_elapsed=0.;self._active=None;self.assays=[];self._concentrations=[];self._qc=[];self._last_point=None;self._pending_atoms={};self._pending_dt=0.;self._sample_start=0.;self._rng=np.random.default_rng(0);self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._operation_records={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ['agent','debug','evaluator'] or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return ShimadzuIcpms2030Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in ['library','templates','isotopes','_rng']})|{'_rng_state':copy.deepcopy(self._rng.bit_generator.state)}
 def _checkpoint(self):
  keep=['library','templates','isotopes','_records','_curves','_operation_records','_requests','_rng'];snap=copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in keep})
  for k in keep:snap[k]=dict(getattr(self,k)) if k in ['_records','_curves','_operation_records','_requests'] else getattr(self,k)
  return snap,copy.deepcopy(self._rng.bit_generator.state)
 def _reference(self,key,physical=True):
  r=self.templates.get(key,self.isotopes.get(key))
  if r is None:raise ActionRejected('UNKNOWN_REFERENCE')
  if physical:
   if r.get('physical_load_eligible') is False:raise ActionRejected('AMBIGUOUS_REFERENCE_BASIS')
   _values(r)
  return r
 def _iso(self,k):
  i=self.isotopes.get(k)
  if not i:raise ActionRejected('UNKNOWN_ISOTOPE')
  validate(integer(5,260),i.get('mass_number'));validate(number('kg',1e-29,1e-23),i.get('atomic_mass_kg'));validate(number('1',0,1),i.get('isotope_mass_fraction_of_element'));return i
 def _split(self,concentrations):
  out={}
  for e,c in concentrations.items():
   entries={k:self._iso(k) for k,v in self.isotopes.items() if v.get('element')==e and v.get('isotope_mass_fraction_of_element') is not None and v.get('isotope_mass_fraction_of_element')>0}
   if not entries or abs(sum(v['isotope_mass_fraction_of_element'] for v in entries.values())-1)>1e-8:raise ActionRejected('ISOTOPE_COMPOSITION_UNAVAILABLE')
   for k,v in entries.items():out[k]=c*v['isotope_mass_fraction_of_element']
  return out
 def _target_concentration(self,record,q):
  values=_values(record);iso=self._iso(q['isotope_id'])
  if iso['element'] not in values:raise ActionRejected('UNREPORTED_ELEMENT')
  return values[iso['element']]*(iso['isotope_mass_fraction_of_element'] if q['basis']=='isotope' else 1.)
 def _key(self):return json.dumps(dict(method={k:v for k,v in (self.method or {}).items() if k not in ['repeats']},torch_target=self.torch_target),sort_keys=True)
 def _blank_valid(self):return self._key() in self._blanks
 def _calibration_ready(self):return self._selected in self._curves and self._curves[self._selected]['key']==self._key()
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _cold(self):return not self.lit and not self.igniting and self.temperature<=353.15
 def _rf_target(self):return self.method['rf_power_w'] if (self.lit or self.igniting) and self.method and self.powered else 0.
 def _argon_flow(self):return self.method['argon_flow_m3_s'] if (self.lit or self.igniting) and self.method and self.powered else 0.
 def _he_target(self):return self.method['helium_flow_m3_s'] if (self.lit or self.igniting) and self.method and self.powered else 0.
 def _vacuum_ready(self):return self.plant is not None and self.pressure<=self.plant['vacuum_ready_threshold_pa']
 def _positioned(self):return max(abs(a-b) for a,b in zip(self.torch,self.torch_target))<1e-10
 def _element(self,k):
  if k not in self._masses:self._masses[k]=dict(registered=0.,aerosol=0.,plasma=0.,waste=0.,exhaust=0.,removed=0.)
  return self._masses[k]
 def _ledger(self):
  rows=[]
  for k,m in sorted(self._masses.items()):
   x=dict(m,sample=self.sample['concentrations'].get(k,0.)*self.sample['volume'] if self.sample else 0.);x['residual']=x['registered']-math.fsum(v for key,v in x.items() if key!='registered');rows.append(dict(isotope_id=k,**x))
  v=dict(self._volume,sample=self.sample['volume'] if self.sample else 0.);v['residual']=v['registered']-math.fsum(x for k,x in v.items() if k!='registered');gas=lambda x:dict(x,residual=x['initial']-x['remaining']-x['consumed']);stored=(self.temperature-self.initial_temperature)*(self.plant['thermal_capacity_j_k'] if self.plant else 0.)
  return dict(isotopes=rows,liquid_volume_m3=v,argon_mol=gas(self.gas),helium_mol=gas(self.helium),vacuum_gas_mol=gas(self.vacuum_gas),rf_input_j=self.rf_energy,absorbed_heat_j=self.absorbed_heat,lost_heat_j=self.lost_heat,stored_heat_j=stored,thermal_residual_j=self.absorbed_heat-self.lost_heat-stored,active_template_id=self.sample['template_id'] if self.sample else None,evidence='Nuclear-isotope continuous mass ledger; not individual stochastic ion/electron charge accounting. Unreported elements/matrix not invented. Evaporation liquid-equivalent, not gas volume; pre-mixed sample loaded as final volume; Ar/He explicit reference T/P; ideal vacuum and lumped absorbed RF heat.')
 def _result(self):return dict(phase=self.phase,stage=self.stage,complete=self.phase=='succeeded',concentrations=copy.deepcopy(self._concentrations),assays=copy.deepcopy(self.assays),active_assay=copy.deepcopy(self._active),qc=list(dict.fromkeys(self._qc)),evidence='synthetic ion counts from source concentrations and assumed isotope/ion response; not original measured detector counts',template_source=TEMPLATE_REL,acquisition_method_json=json.dumps(self._run_method,sort_keys=True))
 def read(self,visibility='agent'):
  progress=(len(self.assays)+self._plan_index/max(1,len(self._schedule)))/max(1,(self._run_method or {}).get('repeats',1)) if self.phase=='running' else 1. if self.phase=='succeeded' else 0.;last=self._last_point or {};v={'plant.configured':self.plant is not None,'sample.loaded':self.sample is not None,'power.enabled':self.powered,'cover.opened':self.opened,'cover.angle_rad':1.7 if self.opened else 0.,'pump.angle_rad':self.pump_angle,**{'torch.'+a+'_m':self.torch[i] for i,a in enumerate(['x','y','z'])},'vacuum.pump_enabled':self.vacuum_pump,'vacuum.pressure_pa':self.pressure,'vacuum.ready':self._vacuum_ready(),'rf.target_power_w':self._rf_target(),'rf.actual_power_w':self.rf,'plasma.lit':self.lit,'plasma.igniting':self.igniting,'plasma.temperature_k':self.temperature,'helium.flow_m3_s':self.he_flow,'mass.current_number':self.current_mass,'method.mass_numbers':list(self.method['mass_numbers']) if self.method else [],'measurement.phase':self.phase,'measurement.stage':self.stage,'measurement.elapsed_s':self.elapsed_s,'measurement.progress':max(0.,min(1.,progress)),'measurement.completed_repeats':len(self.assays),'detector.count_rate_s_inv':last.get('count_rate_s_inv'),'detector.counts':last.get('counts'),'detector.mass_number':last.get('mass_number'),'detector.last_sample_time_s':last.get('time_s'),'result.concentrations':copy.deepcopy(self._concentrations),'result.concentration_kg_m3':self._concentrations[0]['concentration_kg_m3'] if self._concentrations else None,'blank.valid':self._blank_valid(),'calibration.ready':self._calibration_ready(),'storage.result_count':len(self._records),'storage.curve_count':len(self._curves),'safety.warning':self.warning}
  if visibility!='agent':v['truth.ledger']=self._ledger()
  return v
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[k][0],a);snap,rng=self._checkpoint()
  try:result=self._invoke(k,a);validate(CAPS[k][1],result)
  except Exception:self.__dict__.clear();self.__dict__.update(snap);self._rng.bit_generator.state=rng;raise
  if CAPS[k][4]!='query':self.revision+=1;self._archive_operation()
  return copy.deepcopy(result)
 def _invoke(self,k,a):
  if k=='plant.configure':
   if self.plant is not None or self.time_s!=0:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_assumed_response']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   if a['vacuum_base_pressure_pa']>=a['vacuum_ready_threshold_pa']:raise ActionRejected('VACUUM_THRESHOLD_ORDER')
   self.plant=copy.deepcopy(a);self._rng=np.random.default_rng(a['seed']);self.temperature=self.initial_temperature=a['ambient_k'];self.pressure=a['gas_reference_pressure_pa'];self.gas=dict(initial=a['argon_inventory_mol'],remaining=a['argon_inventory_mol'],consumed=0.);self.helium=dict(initial=a['helium_inventory_mol'],remaining=a['helium_inventory_mol'],consumed=0.);n=self.pressure*a['vacuum_chamber_volume_m3']/(R*a['ambient_k']);self.vacuum_gas=dict(initial=n,remaining=n,consumed=0.);return {'configured':True}
  if k=='sample.load':
   self._idle()
   if self.sample:raise ActionRejected('UNLOAD_REQUIRED')
   if not a['is_aqueous']:raise ActionRejected('UNSUPPORTED_MATRIX')
   if not a['sample_id'] or not a['accept_unknown_conditions'] or not a['accept_natural_isotope_assumption']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   values={} if a['kind']=='blank' else _values(self._reference(a['template_id']));elements={e:c/a['dilution_factor'] for e,c in values.items()};is_elements={}
   if a['internal_standard_reference_id']:
    if a['kind']=='blank':raise ActionRejected('BLANK_MUST_NOT_CONTAIN_INTERNAL_STANDARD')
    is_elements={e:c/a['internal_standard_dilution_factor'] for e,c in _values(self._reference(a['internal_standard_reference_id'])).items()}
    if set(is_elements)&set(elements):raise ActionRejected('INTERNAL_STANDARD_OVERLAPS_SOURCE')
    elements.update(is_elements)
   isotopes=self._split(elements)
   if len(isotopes)>400:raise ActionRejected('ISOTOPE_CAPACITY')
   self.sample=dict(id=a['sample_id'],template_id=a['template_id'],kind=a['kind'],volume=a['volume_m3'],concentrations=isotopes,elements=elements,dilution=a['dilution_factor'],is_elements=is_elements,matrix=a['matrix_transmission'],interferences=copy.deepcopy(a['interferences']));self._volume['registered']+=a['volume_m3']
   for key,c in isotopes.items():self._element(key)['registered']+=c*a['volume_m3']
   return {'loaded':True}
  if k=='sample.unload':
   self._idle()
   if not self.sample:raise ActionRejected('SAMPLE_REQUIRED')
   if not a['recipient_id']:raise ActionRejected('RECIPIENT_REQUIRED')
   v=self.sample['volume'];self._volume['removed']+=v
   for key,c in self.sample['concentrations'].items():self._element(key)['removed']+=c*v
   self.sample=None;return dict(unloaded=True,volume_m3=v)
  if k=='fluidics.clean':
   self._idle()
   if not self._cold():raise ActionRejected('COLD_MAINTENANCE_REQUIRED')
   mass=0.
   for m in self._masses.values():mass+=m['aerosol']+m['plasma'];m['waste']+=m['aerosol']+m['plasma'];m['aerosol']=m['plasma']=0.
   v=self._volume['aerosol'];self._volume['waste']+=v;self._volume['aerosol']=0.;return dict(removed_mass_kg=mass,removed_volume_m3=v)
  if k=='cover.set':
   self.opened=a['opened']
   if self.opened:self._extinguish('SAFETY_COVER_OPEN')
   return {'opened':self.opened}
  if k=='torch.position':
   self._idle();self.torch_target=[a[x+'_m'] for x in ['x','y','z']];self.stable_elapsed=0.;return {'positioning':not self._positioned()}
  if k=='power.set':
   if a['enabled'] and (not self.plant or self.opened):raise ActionRejected('POWER_INTERLOCK')
   self.powered=a['enabled']
   if not self.powered:self._extinguish('POWER_LOSS');self.vacuum_pump=False
   return {'enabled':self.powered}
  if k=='vacuum.set':
   if a['enabled'] and not self.powered:raise ActionRejected('POWER_REQUIRED')
   self.vacuum_pump=a['enabled'];return {'enabled':self.vacuum_pump}
  if k=='method.configure':
   self._idle()
   if not self.plant:raise ActionRejected('PLANT_REQUIRED')
   if self.lit or self.igniting:raise ActionRejected('EXTINGUISH_BEFORE_METHOD_CHANGE')
   if len(set(a['mass_numbers']))!=len(a['mass_numbers']):raise ActionRejected('DUPLICATE_MASS_CHANNEL')
   qids=[q['isotope_id'] for q in a['quantifications']];numbers=[self._iso(i)['mass_number'] for i in qids]
   if len(set(qids))!=len(qids) or len(set(numbers))!=len(numbers):raise ActionRejected('UNRESOLVED_ISOBAR_QUANTIFICATION')
   if a['internal_standard_isotope_id']:
    ismass=self._iso(a['internal_standard_isotope_id'])['mass_number']
    if a['internal_standard_concentration_kg_m3']<=0:raise ActionRejected('INTERNAL_STANDARD_DOSE_REQUIRED')
    if self._iso(a['internal_standard_isotope_id'])['element'] in [self._iso(i)['element'] for i in qids]:raise ActionRejected('INTERNAL_STANDARD_OVERLAPS_ANALYTE')
    if ismass in numbers:raise ActionRejected('INTERNAL_STANDARD_OVERLAPS_ANALYTE')
    numbers.append(ismass)
   if not a['internal_standard_isotope_id'] and a['internal_standard_concentration_kg_m3']!=0:raise ActionRejected('INTERNAL_STANDARD_ID_REQUIRED')
   if not set(numbers)<=set(a['mass_numbers']):raise ActionRejected('REQUIRED_MASS_CHANNEL_MISSING')
   if len(a['mass_numbers'])*a['sweeps']*a['repeats']>MAX_TRACE_POINTS:raise ActionRejected('TRACE_CAPACITY')
   self.method=copy.deepcopy(a);return {'configured':True}
  if k=='plasma.ignite':
   self._idle()
   if not self.powered or not self.method or self.opened or self.lit or self.igniting or not self._vacuum_ready() or not self._positioned() or not self.plant['coolant_available'] or not self.plant['exhaust_available'] or self.gas['remaining']<=0 or (self.method['helium_flow_m3_s']>0 and self.helium['remaining']<=0):raise ActionRejected('IGNITION_INTERLOCK')
   self.igniting=True;self.ignition_elapsed=0.;self.stable_elapsed=0.;return {'igniting':True}
  if k=='plasma.extinguish':self._extinguish('USER_EXTINGUISH');return {'lit':False}
  if k=='measurement.start':return self._start()
  if k=='measurement.stop':self._stop('USER_CANCEL');return {'phase':self.phase}
  if k=='process.measure':return dict(phase=self.phase,stage=self.stage,progress=self.read()['measurement.progress'],count_rate_s_inv=self.read()['detector.count_rate_s_inv'],concentrations=copy.deepcopy(self._concentrations))
  if k=='result.read':return self._result()
  if k=='spectrum.read':
   a=self._active or (self.assays[-1] if self.assays else None);return dict(channels=copy.deepcopy(a['channels']) if a else [],sample_windows_s=[p['sample_window_s'] for p in a['points']] if a else [],evidence='synthetic sequential single-charge mass-channel ion counts, not original source detector data',coverage='Only selected integer mass numbers; sequential windows, not simultaneous full5–260scan. Isobar contributions are summed; no unique identification of unresolved species or molecular/double-charge correction.')
  if k=='result.export':
   if a['format']=='json':content=json.dumps(self._result(),ensure_ascii=False,allow_nan=False)
   else:
    f=io.StringIO();w=csv.writer(f);w.writerow(['repeat','mass_number','window_start_s','window_end_s','counts','count_rate_s_inv','corrected_count_rate_s_inv','saturated'])
    for i,assay in enumerate(self.assays+([self._active] if self._active else [])):
     for p in assay['points']:w.writerow([i,p['mass_number'],*p['sample_window_s'],p['counts'],p['count_rate_s_inv'],p['corrected_count_rate_s_inv'],p['saturated']])
    content=f.getvalue()
   return dict(format=a['format'],content=content)
  if k=='blank.read':return dict(valid=self._blank_valid(),blank_json=json.dumps(self._blanks.get(self._key()),sort_keys=True))
  if k=='calibration.fit':return self._fit(a)
  if k=='calibration.select':
   self._idle();curve=self._curves.get(a['curve_id'])
   if not curve:raise ActionRejected('UNKNOWN_CURVE')
   if curve['key']!=self._key():raise ActionRejected('CALIBRATION_SIGNATURE_MISMATCH')
   self._selected=a['curve_id'];return {'selected':self._selected}
  if k=='calibration.read':
   if a['curve_id'] not in self._curves:raise ActionRejected('UNKNOWN_CURVE')
   return {'curve_json':json.dumps(self._curves[a['curve_id']],sort_keys=True)}
  if k=='records.list':return dict(record_ids=list(self._records),curve_ids=list(self._curves))
  if k=='records.read':
   if a['record_id'] not in self._records:raise ActionRejected('UNKNOWN_RECORD')
   return self._records[a['record_id']]
  if k=='records.remove':
   if a['record_id'] not in self._records:raise ActionRejected('UNKNOWN_RECORD')
   del self._records[a['record_id']];return {'removed':True}
  if k=='template.read':return {'template_json':json.dumps(self._reference(a['substance_id'],False),ensure_ascii=False,allow_nan=False)}
  if k=='product.read':return self._ledger()
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _response(self,channels,q,method=None):
  method=method or self.method;rows={c['mass_number']:c for c in channels};n=self._iso(q['isotope_id'])['mass_number'];value=rows.get(n,{}).get('corrected_count_rate_s_inv')
  if value is None:raise ActionRejected('VALID_COUNT_CHANNEL_REQUIRED')
  if method['internal_standard_isotope_id']:
   i=self._iso(method['internal_standard_isotope_id'])['mass_number'];denom=rows.get(i,{}).get('corrected_count_rate_s_inv')
   if denom is None or denom<=0:raise ActionRejected('INTERNAL_STANDARD_SIGNAL_REQUIRED')
   value=value/denom
  if not math.isfinite(value):raise ActionRejected('NONFINITE_RESPONSE')
  return value
 def _fit(self,a):
  self._idle()
  if not self.method or not self._blank_valid():raise ActionRejected('MATCHED_BLANK_REQUIRED')
  if not a['curve_id'] or a['curve_id'] in self._curves:raise ActionRejected('CURVE_ID_CONFLICT')
  if len(self._curves)>=MAX_CURVES:raise ActionRejected('CURVE_CAPACITY')
  if not a['accept_same_response_conditions']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
  fits={}
  for q in self.method['quantifications']:
   xs=[];ys=[];element=self._iso(q['isotope_id'])['element']
   for o in a['observations']:
    ref=self._reference(o['reference_id'])
    if element not in ref.get('calibration_eligible_elements',[]):raise ActionRejected('CALIBRATION_REFERENCE_REQUIRED')
    if len({c['mass_number'] for c in o['channels']})!=len(o['channels']):raise ActionRejected('DUPLICATE_MASS_CHANNEL')
    xs.append(self._target_concentration(ref,q)/o['dilution_factor']);ys.append(self._response(o['channels'],q))
   if len(set(xs))<3:raise ActionRejected('CALIBRATION_DISTINCT_LEVELS')
   x=np.asarray(xs);y=np.asarray(ys);center=float(x.mean());scale=float(np.ptp(x));z=(x-center)/scale;yscale=max(float(np.max(np.abs(y))),1e-300);yn=y/yscale;coef=np.linalg.lstsq(np.column_stack([z,np.ones(len(z))]),yn,rcond=None)[0];pred=coef[0]*z+coef[1];ss=float(np.sum((yn-yn.mean())**2));r2=1-float(np.sum((pred-yn)**2))/ss if ss>0 else -math.inf
   with np.errstate(over='ignore',invalid='ignore'):slope=float(coef[0]*yscale/scale);intercept=float((coef[1]-coef[0]*center/scale)*yscale)
   if not all(math.isfinite(v) for v in [slope,intercept,r2]) or slope<=0 or r2<.95:raise ActionRejected('CALIBRATION_FIT_INVALID')
   fits[q['isotope_id']]=dict(slope=slope,intercept=intercept,r_squared=r2,concentration_range_kg_m3=[min(xs),max(xs)],basis=q['basis'],response_unit='1' if self.method['internal_standard_isotope_id'] else 's^-1')
  self._curves[a['curve_id']]=dict(key=self._key(),fits=fits,method=copy.deepcopy(self.method),observations=copy.deepcopy(a['observations']),evidence='reported actual reference concentrations with caller supplied simulated measured count observations; not certified OEM response');return dict(curve_id=a['curve_id'],isotope_count=len(fits),minimum_r_squared=min(x['r_squared'] for x in fits.values()))
 def _new_assay(self):return dict(points=[],channels=[dict(mass_number=n,counts=0,dwell_time_s=0.,corrected_count_rate_s_inv=0.) for n in self._run_method['mass_numbers']],complete=False)
 def _enter(self):
  self.stage,duration,self.current_mass=self._schedule[self._plan_index];self._stage_elapsed=0.;self._pending_atoms={};self._pending_dt=0.;self._sample_start=self.time_s
 def _start(self):
  self._idle()
  if not self.plant or not self.method or not self.sample:raise ActionRejected('SAMPLE_METHOD_REQUIRED')
  if not self.powered or not self.lit or self.opened or not self._positioned() or not self._vacuum_ready() or abs(self.rf-self._rf_target())>1e-8 or abs(self.he_flow-self._he_target())>1e-14 or self.stable_elapsed+1e-10<self.plant['startup_stabilization_s']:raise ActionRejected('MEASUREMENT_INTERLOCK')
  if len(self._records)>=MAX_RESULTS:raise ActionRejected('RESULT_CAPACITY')
  if self.sample['kind']!='blank':
   for q in self.method['quantifications']:
    if q['isotope_id'] not in self.sample['concentrations']:raise ActionRejected('UNREPORTED_ELEMENT')
    if self.sample['kind']=='standard' and self._iso(q['isotope_id'])['element'] not in self._reference(self.sample['template_id']).get('calibration_eligible_elements',[]):raise ActionRejected('CALIBRATION_REFERENCE_REQUIRED')
   if self.method['internal_standard_isotope_id']:
    e=self._iso(self.method['internal_standard_isotope_id'])['element'];actual=self.sample['is_elements'].get(e)
    if actual is None:raise ActionRejected('PREPARED_INTERNAL_STANDARD_REQUIRED')
    if not math.isclose(actual,self.method['internal_standard_concentration_kg_m3'],rel_tol=1e-10,abs_tol=0.):raise ActionRejected('INTERNAL_STANDARD_DOSE_MISMATCH')
   if not self._blank_valid():raise ActionRejected('MATCHED_BLANK_REQUIRED')
  if self.sample['kind']=='sample' and not self._calibration_ready():raise ActionRejected('CALIBRATION_REQUIRED')
  duration=self.method['stabilization_s']+len(self.method['mass_numbers'])*(self.method['dwell_s']+self.method['settling_s'])*self.method['sweeps']
  if self.sample['volume']+1e-20<self.plant['uptake_m3_s']*duration*self.method['repeats']:raise ActionRejected('INSUFFICIENT_SAMPLE_VOLUME')
  self._run_method=copy.deepcopy(self.method);self._run_key=self._key();self._run_kind=self.sample['kind'];self._run_dilution=self.sample['dilution'];self._run_blank=copy.deepcopy(self._blanks.get(self._key(),{}));self._run_curve=copy.deepcopy(self._curves.get(self._selected)) if self._calibration_ready() else None
  self._schedule=[('stabilization',self.method['stabilization_s'],None)]
  for _ in range(self.method['sweeps']):
   for mass in self.method['mass_numbers']:
    if self.method['settling_s']>0:self._schedule.append(('mass_settle',self.method['settling_s'],mass))
    self._schedule.append(('dwell',self.method['dwell_s'],mass))
  self._schedule.append(('flush',self.method['flush_s'],None));self.phase='running';self.elapsed_s=0.;self.assays=[];self._active=self._new_assay();self._concentrations=[];self._qc=[];self._last_point=None;self.warning='';self._plan_index=0;self._enter();self._op_count+=1;self.operation_id=f'{self.instance_id}:operation:{self._op_count}';return {'operation_id':self.operation_id}
 def _stop(self,reason):
  if self.phase=='running':self.phase=self.stage='cancelled';self.warning=reason;self._concentrations=[];self._qc.append('partial acquisition, completed dwell windows retained');self._pending_atoms={};self._pending_dt=0.
 def _extinguish(self,reason):
  running=self.phase=='running';self._stop(reason);self.lit=self.igniting=False;self.stable_elapsed=0.
  if running and reason in ['ARGON_EXHAUSTED','HELIUM_EXHAUSTED']:self.phase=self.stage='failed'
  if reason=='HELIUM_EXHAUSTED':self.he_flow=0.
  self.warning=reason;self._archive_operation()
 def _detector(self):
  p=self.plant;dt=self._pending_dt
  if dt<=0:raise RuntimeError('Dwell with no elapsed sample window')
  atoms=math.fsum(v for k,v in self._pending_atoms.items() if self.isotopes[k]['mass_number']==self.current_mass);alignment=math.exp(-sum(x*x for x in self.torch)/(2*p['alignment_width_m']**2));ion_eff=p['ionization_fraction']*p['ion_transmission']*p['detector_efficiency']*(self.rf/1400.)*alignment*self.sample['matrix']*math.exp(-p['helium_analyte_loss_s_m3']*self.he_flow)
  molecular=math.fsum(x['incident_count_rate_s_inv'] for x in self.sample['interferences'] if x['mass_number']==self.current_mass)*math.exp(-p['helium_interference_loss_s_m3']*self.he_flow);true_rate=atoms*ion_eff/dt+molecular+p['background_count_rate_s_inv'];expected_rate=true_rate/(1+true_rate*p['detector_dead_time_s']);saturated=expected_rate>=p['detector_saturation_rate_s_inv'];expected=min(expected_rate,p['detector_saturation_rate_s_inv'])*dt;counts=int(self._rng.poisson(expected)) if p['poisson_noise'] else int(round(expected));limit=int(math.floor(p['detector_saturation_rate_s_inv']*dt));saturated=saturated or counts>limit;counts=min(counts,limit);rate=counts/dt;denom=1-rate*p['detector_dead_time_s'];corrected=rate
  if self._run_method['deadtime_correction']:
   if denom<=.05:saturated=True;corrected=None
   else:corrected=rate/denom
  if saturated:corrected=None;self._qc.append('saturated_mass:'+str(self.current_mass))
  point=dict(time_s=self.time_s,sample_window_s=[self._sample_start,self.time_s],acquiring=True,mass_number=self.current_mass,counts=counts,count_rate_s_inv=rate,corrected_count_rate_s_inv=corrected,saturated=saturated);self._active['points'].append(point);self._last_point=point
  row=next(x for x in self._active['channels'] if x['mass_number']==self.current_mass);previous_time=row['dwell_time_s'];old=row['corrected_count_rate_s_inv'];row['counts']+=counts;row['dwell_time_s']+=dt;row['corrected_count_rate_s_inv']=None if corrected is None or old is None else (old*previous_time+corrected*dt)/row['dwell_time_s']
 def _finish_assay(self):
  self._active['complete']=True
  if self._run_kind!='blank':
   for row in self._active['channels']:
    if row['corrected_count_rate_s_inv'] is not None:row['corrected_count_rate_s_inv']-=self._run_blank.get(str(row['mass_number']),0.)
  self.assays.append(self._active);self._active=None
  if len(self.assays)<self._run_method['repeats']:self._active=self._new_assay();self._plan_index=0;self._enter();return
  self.phase='succeeded';self.stage='complete';self.current_mass=None
  if self._run_kind=='blank':
   if any(row['corrected_count_rate_s_inv'] is None for a in self.assays for row in a['channels']):self._qc.append('invalid_blank_saturation')
   else:self._blanks[self._run_key]={str(n):float(np.mean([next(x['corrected_count_rate_s_inv'] for x in a['channels'] if x['mass_number']==n) for a in self.assays])) for n in self._run_method['mass_numbers']}
  for q in self._run_method['quantifications']:
   iso=self._iso(q['isotope_id']);c=sd=cv=ratio=net=None;quality='uncalibrated standard/blank count observation'
   try:
    responses=[self._response(a['channels'],q,self._run_method) for a in self.assays];net=float(np.mean([next(x['corrected_count_rate_s_inv'] for x in a['channels'] if x['mass_number']==iso['mass_number']) for a in self.assays]));ratio=float(np.mean(responses)) if self._run_method['internal_standard_isotope_id'] else None;fit=(self._run_curve or {}).get('fits',{}).get(q['isotope_id'])
    if fit and self._run_kind=='sample':
     confounded=any(k!=q['isotope_id'] and candidate['mass_number']==iso['mass_number'] and candidate.get('isotope_mass_fraction_of_element',0)>0 for k,candidate in self.isotopes.items());effective=[(v-fit['intercept'])/fit['slope'] for v in responses];lo,hi=fit['concentration_range_kg_m3']
     if confounded:quality='public isotope catalog has unresolved isobar candidates; concentration unavailable';self._qc.append('unresolved_isobar:'+q['isotope_id'])
     elif any(not math.isfinite(v) or v<lo or v>hi for v in effective):quality='outside calibration interpolation domain';self._qc.append('outside_calibration:'+q['isotope_id'])
     else:
      donor=np.asarray(effective)*self._run_dilution;c=float(donor.mean());sd=float(donor.std(ddof=1)) if len(donor)>1 else None;cv=sd/abs(c) if sd is not None and c else None;quality='conditional inferred concentration from simulated observations; natural isotope and unmodeled interference assumptions retained'
   except ActionRejected as exc:quality=str(exc);self._qc.append(str(exc))
   self._concentrations.append(dict(isotope_id=q['isotope_id'],element=iso['element'],basis=q['basis'],mass_number=iso['mass_number'],concentration_kg_m3=c,sd_kg_m3=sd,cv=cv,net_count_rate_s_inv=net,internal_standard_ratio=ratio,quality=quality))
  self._records[f'record_{self._op_count}']=self._result();self._archive_operation()
 def _physical(self,dt):
  p=self.plant
  if p is None:return
  self.torch=[_move(v,t,p['torch_speed_m_s'],dt) for v,t in zip(self.torch,self.torch_target)]
  if self.vacuum_pump and self.powered:
   old=self.pressure;self.pressure=p['vacuum_base_pressure_pa']+(old-p['vacuum_base_pressure_pa'])*math.exp(-p['vacuum_decay_s_inv']*dt);n=(old-self.pressure)*p['vacuum_chamber_volume_m3']/(R*p['ambient_k']);self.vacuum_gas['remaining']-=n;self.vacuum_gas['consumed']+=n
  power0=self.rf;self.rf=_move(self.rf,self._rf_target(),p['rf_ramp_w_s'],dt);slope=(self.rf-power0)/dt;h=p['thermal_loss_w_k']/p['thermal_capacity_j_k'];x=h*dt;h1=-math.expm1(-x)/h;h2=(dt-h1)/h if abs(x)>1e-5 else dt*dt*(.5-x/6+x*x/24-x*x*x/120);old=self.temperature;self.temperature=p['ambient_k']+(old-p['ambient_k'])*math.exp(-x)+p['absorbed_rf_fraction']/p['thermal_capacity_j_k']*(power0*h1+slope*h2);energy=(power0+self.rf)/2*dt;absorbed=p['absorbed_rf_fraction']*energy;self.rf_energy+=energy;self.absorbed_heat+=absorbed;self.lost_heat+=absorbed-p['thermal_capacity_j_k']*(self.temperature-old)
  he0=self.he_flow;self.he_flow=_move(he0,self._he_target(),p['helium_ramp_m3_s2'],dt)
  for gas,volume in [(self.gas,self._argon_flow()*dt),(self.helium,(he0+self.he_flow)/2*dt)]:
   amount=min(gas['remaining'],volume*p['gas_reference_pressure_pa']/(R*p['gas_reference_temperature_k']));gas['remaining']-=amount;gas['consumed']+=amount
  if self.igniting:self.ignition_elapsed+=dt
  if self.lit and abs(power0-self._rf_target())<1e-8 and abs(he0-self._he_target())<1e-14 and self._positioned():self.stable_elapsed+=dt
  if not self.lit:return
  a=1/p['aerosol_residence_s'];b=1/p['plasma_residence_s'];ea=math.exp(-a*dt);eb=math.exp(-b*dt);ha=-math.expm1(-a*dt)/a;hb=-math.expm1(-b*dt)/b;kernel=(ea-eb)/(b-a) if abs(b-a)>1e-7*max(a,b) else dt*math.exp(-.5*(a+b)*dt);feed_volume=p['uptake_m3_s']*dt if self.phase=='running' and self.stage in ['stabilization','mass_settle','dwell'] else 0.
  if self.sample:feed_volume=min(feed_volume,self.sample['volume']);self.sample['volume']-=feed_volume
  if feed_volume:self.pump_angle=(self.pump_angle+feed_volume/p['pump_volume_per_rad_m3'])%(2*math.pi)
  rate=feed_volume/dt;eta=p['aerosol_fraction'];y=p['atomization_yield']
  for key,m in self._masses.items():
   c=self.sample['concentrations'].get(key,0.) if self.sample else 0.;feed=c*rate*eta;old_a=m['aerosol'];old_p=m['plasma'];new_a=old_a*ea+feed*ha;released=max(0.,old_a+feed*dt-new_a);new_p=max(0.,old_p*eb+y*(a*old_a-feed)*kernel+y*feed*hb);out=max(0.,old_p+y*released-new_p);m['aerosol']=new_a;m['plasma']=new_p;m['waste']+=(1-eta)*c*feed_volume+(1-y)*released;m['exhaust']+=out
   if self.phase=='running' and self.stage=='dwell':self._pending_atoms[key]=self._pending_atoms.get(key,0.)+out/self.isotopes[key]['atomic_mass_kg']
  oldv=self._volume['aerosol'];newv=oldv*ea+eta*rate*ha;self._volume['aerosol']=newv;self._volume['evaporated_liquid_equivalent']+=max(0.,oldv+eta*feed_volume-newv);self._volume['waste']+=(1-eta)*feed_volume
  if self.phase=='running' and self.stage=='dwell':self._pending_dt+=dt
 def step(self,dt):
  validate(number('s',0,86400),dt)
  if dt==0:return self.read('evaluator')
  end=self.time_s+dt
  while self.time_s<end-1e-12:
   dt0=end-self.time_s
   if self.plant:
    p=self.plant
    for distance,rate in [(abs(self.rf-self._rf_target()),p['rf_ramp_w_s']),(abs(self.he_flow-self._he_target()),p['helium_ramp_m3_s2']),*[(abs(v-t),p['torch_speed_m_s']) for v,t in zip(self.torch,self.torch_target)]]:
     if distance>1e-16:dt0=min(dt0,distance/rate)
    if self.igniting and self.ignition_elapsed<p['ignition_s']-1e-12:dt0=min(dt0,p['ignition_s']-self.ignition_elapsed)
    molfactor=p['gas_reference_pressure_pa']/(R*p['gas_reference_temperature_k'])
    for gas,flow in [(self.gas,self._argon_flow()),(self.helium,max(self.he_flow,self._he_target()))]:
     if flow>0 and gas['remaining']>1e-15:dt0=min(dt0,gas['remaining']/(flow*molfactor))
    if self.phase=='running':dt0=min(dt0,max(0.,self._schedule[self._plan_index][1]-self._stage_elapsed))
   if dt0<=1e-12:dt0=min(end-self.time_s,1e-12)
   running=self.phase=='running';self._physical(dt0);self.time_s+=dt0
   if running:self.elapsed_s+=dt0;self._stage_elapsed+=dt0
   if self.igniting and self.ignition_elapsed+1e-10>=self.plant['ignition_s'] and abs(self.rf-self._rf_target())<1e-8:self.igniting=False;self.lit=True;self.stable_elapsed=0.
   if self.phase=='running' and self._stage_elapsed+1e-10>=self._schedule[self._plan_index][1]:
    if self.stage=='dwell':self._detector()
    if self.stage=='flush':self._finish_assay()
    else:self._plan_index+=1;self._enter()
   if (self.lit or self.igniting) and self.gas['remaining']<=1e-15:self._extinguish('ARGON_EXHAUSTED')
   if (self.lit or self.igniting) and self._he_target()>0 and self.helium['remaining']<=1e-15:self._extinguish('HELIUM_EXHAUSTED')
  self.time_s=end;self.revision+=1;self._archive_operation();return self.read('evaluator')
 def _operation_snapshot(self):return dict(protocol_version=PROTOCOL_VERSION,instance_id=self.instance_id,operation_id=self.operation_id,status=self.phase,state_revision=self.revision,clock={'domain':'simulation','time_s':self.time_s},result=self._result(),observations=self.session().read(instance_id=self.instance_id)['observations'],error=dict(code=self.warning,side_effects='partial') if self.phase=='failed' else None)
 def _archive_operation(self):
  if self.operation_id and self.phase in ['succeeded','failed','cancelled'] and self.operation_id not in self._operation_records:self._operation_records[self.operation_id]=self._operation_snapshot()

class ShimadzuIcpms2030Session:
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
        m=export_shimadzu_icpms2030_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        def timing(key):
            if key.startswith('detector.') and h._last_point:return h._last_point['time_s'],h._last_point['sample_window_s'] if key!='detector.last_sample_time_s' else None
            if key.startswith('result.'):
                points=[p for a in h.assays for p in a['points'] if p['acquiring']]
                if points:return points[-1]['time_s'],[points[0]['sample_window_s'][0],points[-1]['time_s']]
            return h.time_s,None
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='unavailable' if v[k] is None else 'estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=timing(k)[0],sample_window_s=timing(k)[1]) for k in ids})
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
            result=h.invoke(capability_id,**arguments);op=result.get('operation_id')
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
        if operation_id in h._operation_records:return copy.deepcopy(h._operation_records[operation_id])
        if operation_id!=h.operation_id:raise ActionRejected('ACCESS_DENIED')
        return copy.deepcopy(h._operation_snapshot())
    def cancel(self,operation_id,request_id):
        snapshot=self.poll(operation_id);h=self._host
        if not isinstance(request_id,str) or not request_id:raise ActionRejected('INVALID_ARGUMENT')
        key=(self._principal,h.instance_id,request_id);digest=json.dumps(['measurement.cancel',operation_id])
        if key in h._requests:
            old,response=h._requests[key]
            if old!=digest:raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(response)
        result=h.invoke('measurement.stop') if snapshot['status']=='running' else {'phase':snapshot['status']}
        response=dict(protocol_version=PROTOCOL_VERSION,instance_id=h.instance_id,request_id=request_id,operation_id=operation_id,status='succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None)
        h._requests[key]=(digest,copy.deepcopy(response));return response

LIMITATIONS=[
'ICPMS-2030 uses sequential quadrupole mass selection with single-charge atomic-ion proxy; integer mass numbers5–260 are dimensionless, not a mislabeled SI m/z axis. Relative neutral atomic masses and natural representative abundances are evaluated references, not measured sample isotope composition or detector gain.',
'RF27MHz and maximum1400W documented; +/-0.3% RF specification is not concentration noise or detector repeatability. Main unit870x645x587mm145kg follows specified guide; brochure140kg and excluded protrusions preserved separately.6kVA supply is not absorbed RF heat.',
'Standard four-channel peristaltic pump and automatic torch XYZ adjustment are documented. Only sample/drain channels are modeled; two others geometrically idle. XYZ+/-3mm, cover1.7rad is instantaneous trusted-host mechanical repositioning, not a powered hinge trajectory; pump volume per radian and rates are engineering proxies. Online internal-standard mixing kit/autosampler absent; explicit host premixing loads final sample volume, not hidden additional volume.',
'Natural atom fractions convert to isotope mass fractions x_i*M_i/sum(x_j*M_j), not direct multiplication of elemental mass by abundance. Atomic mass kg uses cited CODATA2022 m_u. All isotope nuclear masses balance through donor/aerosol/plasma/waste/exhaust/removed; electron charge, molecular products and individual stochastic ion conservation are not solved.',
'Only unambiguous source solution concentrations or documented weighed/digested values may load. Ambiguous EPA ppb mass/volume basis remains query-only; density is never assumed1.0 silently.0–100kg/m3 numeric range per element and400isotope warehouses are model bounds, not OEM analytical range.',
'Only configured integer mass channels are observed in sequential settling/dwell windows. Isobars add in a common channel; inferred target concentration becomes null when the public isotope catalog contains an unresolved same-mass candidate, regardless of hidden sample composition. Unknown matrix, extra isotopes/molecular or double-charge interferences are not automatically identified. Synthetic host interference-rate inputs are not empirical source spectra.',
'Ion counts arise from transported isotope atoms times assumed ionization, transmission, efficiency, RF and alignment factors. He attenuates atomic and supplied interference components with independent assumed coefficients; this is not validated OEM collision/KED chemistry. Relative isotope masses/abundance/Aki are never concentration-response calibration coefficients.',
'Counts are Poisson samples when enabled, otherwise rounded expected counts for deterministic numerical checks. Dead time uses an assumed nonparalyzable detector relation; inversion uses the same explicit host tau and becomes unavailable near saturation. Counts are integers, count rates s^-1, concentration kg/m3; no radiometric W or isotope-fraction masquerading as counts.',
'Observed window count rates with optional dead-time correction are dwell-weighted, then matched observed blank is subtracted. Internal standard uses a separately prepared known dose and observed net rate ratio; blank intentionally excludes IS. Calibration uses >=3 actual simulated standard observations and documented prepared/certified concentrations; source values never directly populate measurement results.',
'Element-basis response calibrates total-element concentration, isotope basis calibrates that nuclide mass concentration under explicit natural-composition assumption. View/optics from ICP-OES are inapplicable. Calibration binds masses,dwell,sweeps,He,RF,torch alignment,IS dose and correction; only repeat count may change. Outside interpolation, saturated or unresolved-isobar results remain null; no hidden outlier deletion.',
'Ar/He consumption uses explicit host absolute reference T/P because source pressure/flow bases are unspecified. Coolant/exhaust booleans are host startup conditions, not real electronics validation. Single ideal vacuum chamber neglects source gas load/leaks and true split-flow turbopump stages. Bulk thermal proxy is not electron temperature. No borrowed180s maintenance timer; host cold threshold353.15K is an explicit engineering guard.',
'Read/describe/poll do not advance time/RNG. Only step advances finite pumping/XYZ/RF/He/transport and complete dwell events. Cancel retains completed dwell windows and transferred isotope mass; partial dwell is not published as complete. Owner-protected historical operation snapshots survive later runs/record deletion. Process-local256results/32curves/20000dwell records are engineering capacities, not OEM storage or reboot persistence.',
'Not validated: actual OEM source response/precision/LOD, isotope tracer enrichment or isotope dilution metrology, molecular/double-charge corrections, complete collision chemistry, detector analog/pulse crossover, real pump/seals/vacuum controls, automatic tuning, optional online mixing/autosampler, Eco standby, hardware/HIL, robot contact and full parameter/noise convergence.'
]
def export_shimadzu_icpms2030_contract_metadata():
 caps={};slots={};run=['measurement.phase','measurement.stage','measurement.elapsed_s','measurement.progress','measurement.completed_repeats'];det=['detector.count_rate_s_inv','detector.counts','detector.mass_number','detector.last_sample_time_s'];result=['result.concentrations','result.concentration_kg_m3'];plasma=['plasma.lit','plasma.igniting','rf.target_power_w'];heat=['rf.actual_power_w','plasma.temperature_k','helium.flow_m3_s','truth.ledger'];cancel=['measurement.phase','measurement.stage','measurement.progress',*result,'safety.warning']
 immediate={'plant.configure':['plant.configured','plasma.temperature_k','vacuum.pressure_pa','vacuum.ready','truth.ledger'],'sample.load':['sample.loaded','truth.ledger'],'sample.unload':['sample.loaded','truth.ledger'],'fluidics.clean':['truth.ledger'],'cover.set':['cover.opened','cover.angle_rad',*plasma,*cancel],'torch.position':['blank.valid','calibration.ready'],'power.set':['power.enabled','vacuum.pump_enabled',*plasma,*cancel],'vacuum.set':['vacuum.pump_enabled'],'method.configure':['method.mass_numbers','blank.valid','calibration.ready'],'plasma.ignite':['plasma.igniting','rf.target_power_w'],'plasma.extinguish':[*plasma,*cancel],'measurement.start':[*run,*det,*result,'mass.current_number','safety.warning'],'measurement.stop':cancel,'calibration.fit':['storage.curve_count'],'calibration.select':['calibration.ready'],'records.remove':['storage.result_count']}
 onstep={'cover.set':heat,'torch.position':['torch.x_m','torch.y_m','torch.z_m'],'power.set':heat,'vacuum.set':['vacuum.pressure_pa','vacuum.ready','truth.ledger'],'plasma.ignite':[*heat,'plasma.lit','plasma.igniting','safety.warning'],'plasma.extinguish':heat,'measurement.start':[*run,*det,*result,'mass.current_number','pump.angle_rad','truth.ledger','blank.valid','storage.result_count','safety.warning',*plasma],'measurement.stop':['truth.ledger']}
 complete={'measurement.start':[*result,'measurement.phase','measurement.stage','measurement.progress','measurement.completed_repeats','mass.current_number','blank.valid','storage.result_count','truth.ledger']}
 observes={'plant.configure':['plant.configured'],'sample.load':['sample.loaded'],'sample.unload':['sample.loaded'],'cover.set':['cover.opened'],'power.set':['power.enabled'],'vacuum.set':['vacuum.pump_enabled'],'plasma.ignite':['plasma.igniting'],'plasma.extinguish':['plasma.lit'],'measurement.start':['measurement.phase'],'measurement.stop':['measurement.phase'],'process.measure':['measurement.phase','measurement.stage','measurement.progress','detector.count_rate_s_inv','result.concentrations'],'result.read':['measurement.phase','measurement.stage','result.concentrations'],'result.export':['measurement.phase','measurement.stage','result.concentrations'],'spectrum.read':['detector.last_sample_time_s'],'blank.read':['blank.valid'],'records.list':['storage.result_count','storage.curve_count'],'product.read':['truth.ledger']}
 for k,(schema,unit,origin,vis,components,write) in SLOTS.items():
  window=k.startswith('detector.') or k.startswith('result.');slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,origin=origin,visibility=vis,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'ShimadzuIcpms2030Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode='interval_mean' if window else 'instant',time_domain='simulation',window_description='计数对应最近完整顺序驻留窗；推断结果对应完整重复采样窗与冻结方法' if window else None),source_ids=['simulation_assumption'])
 errors=sorted({'INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT'}|{n.exc.args[0].value for n in ast.walk(ast.parse(Path(__file__).read_text())) if isinstance(n,ast.Raise) and isinstance(n.exc,ast.Call) and isinstance(n.exc.func,ast.Name) and n.exc.func.id=='ActionRejected' and n.exc.args and isinstance(n.exc.args[0],ast.Constant) and isinstance(n.exc.args[0].value,str)})
 for k,(inp,out,components,label,kind,vis,example,_) in CAPS.items():
  unsupported=k in UNSUPPORTED;query=kind=='query';async_=k=='measurement.start';effects=[dict(state_id=state,timing=timing,description_zh={'immediate':'原子命令返回，不隐式推进时钟','on_step':'唯一宿主时钟推进实际有限位置/物料/驻留计数','on_completion':'完整观察重复后写入标定推断；取消保留已完成窗'}[timing]) for timing,table in [('immediate',immediate),('on_step',onstep),('on_completion',complete)] for state in dict.fromkeys(table.get(k,[]))]
  caps[k]=dict(function_zh=label,kind=kind,visibility=vis,component_ids=components,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.shimadzu_icpms2030.ShimadzuIcpms2030Session.invoke',invocation=f'session.invoke(instance_id="icpms_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if query or unsupported else [dict(description_zh='可信宿主水相/参考与天然组成声明；电源/气源/真空/罩/XYZ及观察空白和标线按调用检查',state_ids=['power.enabled','cover.opened','plasma.lit','vacuum.ready','measurement.phase'],enforced_by='runtime',check='Runtime._invoke and Session principal ACL')],state_effects=[] if query or unsupported else effects,observes=[] if unsupported else observes.get(k,[]),model_effects=dict(mode='none',description_zh='模型联动另由几何/真实场景绑定，查询不动作',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='所选质量通道顺序驻留与所有重复和冲洗阶段完成' if async_ else '原子返回，有限响应仅step推进',completion_state_ids=['measurement.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','prepared_aqueous_sample','peristaltic_pump','Ar_He_inventory','torch_RF','quadrupole_mass_selection','ion_detector','result_store_256','curve_store_32'],partial_failure_zh='取消/开罩/失气保留物料转移和完整已采驻留窗，不隐式退样'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in errors]+([dict(code='ARGON_EXHAUSTED',meaning_zh='Ar耗尽停止，部分效应保留',side_effects='partial'),dict(code='HELIUM_EXHAUSTED',meaning_zh='He耗尽停止，部分效应保留',side_effects='partial')] if async_ else []),example_arguments=copy.deepcopy(example),verification=[],limitations=LIMITATIONS,source_ids=['manufacturer_primary','simulation_assumption'])
 mech=dict(component_ids=['sample_inlet','pump_rotor','spray_chamber','torch','torch_x','torch_y','torch_z','rf_generator','collision_cell','quadrupole','detector'],state_ids=list(slots),meaning_zh='自然丰度有条件拆分核素质量仓，有限雾化/等离子体/碰撞代理与顺序离子计数，经真实标准浓度和模拟观察标定推断',implementation='lumped',equations=['isotope mass fraction=x_i*M_i/sum_j(x_j*M_j); reference atomic mass kg=relative atomic mass*CODATA m_u.','Per nuclide dA/dt=eta*Q*c-A/tau_a; dP/dt=yield*A/tau_a-P/tau_p. Exact transfers preserve registered=donor+aerosol+plasma+waste+exhaust+removed.','Expected incident count rate=transmitted isotope atoms/dwell * assumed ionization*transmission*efficiency*RFfactor*alignment*matrix*exp(-k_He*Q_He)+assumed interference/background.','Nonparalyzable detector observed rate=lambda/(1+lambda*tau); actual integer counts sampled Poisson or rounded expectation; correction=r/(1-r*tau), undefined near saturation.','Dwell-weighted corrected channel rate minus observed blank; optional response=net analyte rate/net internal-standard rate. Fit response=slope*reference concentration/dilution+intercept; interpolation only.','Argon/helium mol=P_ref*V_ref/(R*T_ref); ideal vacuum gas and lumped C*dT=absorbed RF heat-loss tracked separately from unmodeled plasma chemistry.'],parameters={},source_ids=['manufacturer_primary','simulation_assumption'],limitations=LIMITATIONS)
 spec=importlib.util.spec_from_file_location('_icpms_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'icp_mass_spectrometry':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'torch.position':(TORCH,TORCH_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'；实际计数为本模型观察，不代表厂仪器实测。'
 mech['parameters']={k.lower():v for k,v in mech['parameters'].items()};return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'icp_mass_spectrometry':mech})

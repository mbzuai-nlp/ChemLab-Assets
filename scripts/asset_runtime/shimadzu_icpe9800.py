"""ICPE-9820 dual-view, simultaneous multi-element ICP-OES with explicit surrogate response."""
from __future__ import annotations
import ast, copy, csv, hashlib, importlib.util, io, json, math
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
SLUG='shimadzu_icpe9800'; ASSET_TYPE_ID='instrument.'+SLUG; CATALOG_ID='CHEM-082'; PROTOCOL_VERSION='1.0.0'; ASSET_VERSION='1.0.0'
TEMPLATE_REL='assets/expansion_batch04/t3_analytical/shimadzu_icpe9800/output_templates/library.json'
R=8.31446261815324; MAX_RESULTS=256; MAX_CURVES=32; MAX_TRACE_POINTS=20000
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
PLANT=obj(dict(ambient_k=number('K',278.15,308.15),rf_ramp_w_s=number('W/s',1,100000),thermal_capacity_j_k=number('J/K',.01,10000),thermal_loss_w_k=number('W/K',.001,10000),absorbed_rf_fraction=number('1',0,1),ignition_s=number('s',.02,600),startup_stabilization_s=number('s',0,3600),natural_uptake_m3_s=number('m^3/s',1e-12,1e-6),aerosol_fraction=number('1',.001,1),aerosol_residence_s=number('s',.001,100),plasma_residence_s=number('s',.001,10),atomization_yield=number('1',.001,1),emission_gain_per_kg=number('kg^-1',1,1e18),axial_multiplier=number('1',.01,100),detector_noise_sd=number('1',0,10),continuum_relative=number('1',0,1000),line_sigma_m=number('m',1e-13,1e-9),detector_period_s=number('s',.001,10),saturation_relative=number('1',.001,1e12),argon_inventory_mol=number('mol',0,1e6),gas_reference_temperature_k=number('K',200,400),gas_reference_pressure_pa=number('Pa',50000,200000),vacuum_chamber_volume_m3=number('m^3',1e-6,1),vacuum_base_pressure_pa=number('Pa',.001,100),vacuum_decay_s_inv=number('s^-1',.001,1000),vacuum_ready_threshold_pa=number('Pa',.01,10000),mirror_speed_m_s=number('m/s',.001,1),valve_speed_m_s=number('m/s',.0001,1),coolant_temperature_k=number('K',278.15,303.15),coolant_flow_m3_s=number('m^3/s',2.5e-5,.01),exhaust_available=BOOL,argon_purity=number('1',.9995,1),seed=integer(0,2147483647),accept_assumed_response=BOOL))
PLANT_EXAMPLE=dict(ambient_k=298.15,rf_ramp_w_s=5000.,thermal_capacity_j_k=.2,thermal_loss_w_k=.1,absorbed_rf_fraction=.5,ignition_s=.08,startup_stabilization_s=.10,natural_uptake_m3_s=1e-8,aerosol_fraction=.05,aerosol_residence_s=.04,plasma_residence_s=.02,atomization_yield=.95,emission_gain_per_kg=1e14,axial_multiplier=2.,detector_noise_sd=1e-6,continuum_relative=.01,line_sigma_m=2.1e-12,detector_period_s=.02,saturation_relative=1e5,argon_inventory_mol=10.,gas_reference_temperature_k=298.15,gas_reference_pressure_pa=101325.,vacuum_chamber_volume_m3=.01,vacuum_base_pressure_pa=.1,vacuum_decay_s_inv=20.,vacuum_ready_threshold_pa=100.,mirror_speed_m_s=.1,valve_speed_m_s=.01,coolant_temperature_k=293.15,coolant_flow_m3_s=3e-5,exhaust_available=True,argon_purity=.9999,seed=82,accept_assumed_response=True)
METHOD=obj(dict(line_ids=arr(TEXT,1,32),view=enum(['axial','radial']),rf_power_w=number('W',500,1600),argon_flow_m3_s=number('m^3/s',1e-6,.002),stabilization_s=number('s',.02,600),integration_s=number('s',.02,600),flush_s=number('s',.02,600),repeats=integer(1,20),background=enum(['linear_reference','off'])))
METHOD_EXAMPLE=dict(line_ids=['cu_324754','zn_213857'],view='axial',rf_power_w=1300.,argon_flow_m3_s=11.7e-3/60,stabilization_s=.2,integration_s=.4,flush_s=.3,repeats=1,background='linear_reference')
ASSUMED_ELEMENT=obj(dict(element=TEXT,concentration_kg_m3=number('kg/m^3',0,100)))
SAMPLE=obj(dict(sample_id=TEXT,template_id=TEXT,kind=enum(['sample','standard','blank']),volume_m3=number('m^3',1e-12,.1),dilution_factor=number('1',1,1e6),background_relative=number('1',0,1000),background_slope=number('1',-1,1),assumed_elements=arr(ASSUMED_ELEMENT,0,128),accept_assumed_elements=BOOL,accept_unknown_conditions=BOOL,is_aqueous=BOOL))
SAMPLE_EXAMPLE=dict(sample_id='unknown_multi',template_id='shimadzu_epa_qcs_axial',kind='sample',volume_m3=1e-5,dilution_factor=1.,background_relative=.02,background_slope=.01,assumed_elements=[],accept_assumed_elements=False,accept_unknown_conditions=True,is_aqueous=True)
STANDARD_EXAMPLE=dict(SAMPLE_EXAMPLE,sample_id='reference_multi',template_id='shimadzu_epa_ipc_assigned',kind='standard')
BLANK_EXAMPLE=dict(SAMPLE_EXAMPLE,sample_id='matrix_blank',template_id='',kind='blank',background_relative=0.,background_slope=0.)
WINDOW=arr(number('s',0),2,2)
LINE_OBSERVATION=obj(dict(line_id=TEXT,element=TEXT,wavelength_m=number('m',167e-9,800e-9),intensity_relative=number('1'),background_relative=number('1'),corrected_relative=number('1'),saturated=BOOL,element_response_available=BOOL))
POINT=obj(dict(time_s=number('s',0),sample_window_s=WINDOW,acquiring=BOOL,lines=arr(LINE_OBSERVATION,1,1024)))
LINE_INTEGRAL=obj(dict(line_id=TEXT,integrated_intensity_s=number('s')))
ASSAY=obj(dict(points=arr(POINT,0,MAX_TRACE_POINTS),line_integrals=arr(LINE_INTEGRAL,1,1024),integration_time_s=number('s',0),complete=BOOL))
CONCENTRATION=obj(dict(element=TEXT,line_id=TEXT,concentration_kg_m3=nullable(number('kg/m^3')),sd_kg_m3=nullable(number('kg/m^3',0)),cv=nullable(number('1',0)),mean_integrated_intensity_s=nullable(number('s')),quality=TEXT))
RESULT=obj(dict(phase=TEXT,stage=TEXT,complete=BOOL,view=TEXT,concentrations=arr(CONCENTRATION,0,32),assays=arr(ASSAY,0,20),active_assay=nullable(ASSAY),qc=arr(TEXT,0,100),evidence=TEXT,template_source=TEXT,acquisition_method_json=TEXT))
SPECTRUM=obj(dict(wavelength_m=arr(number('m',167e-9,800e-9),0,30000),intensity_relative=arr(number('1'),0,30000),line_readouts=arr(LINE_OBSERVATION,0,1024),sample_window_s=nullable(WINDOW),evidence=TEXT,coverage=TEXT))
MASS_ROW=obj({k:(TEXT if k=='element' else number('kg')) for k in ['element','registered','sample','aerosol','plasma','waste','exhaust','removed','residual']})
VOLUME_LEDGER=obj({k:number('m^3') for k in ['registered','sample','aerosol','waste','evaporated_liquid_equivalent','removed','residual']})
GAS_LEDGER=obj({k:number('mol') for k in ['initial','remaining','consumed','residual']})
LEDGER=obj(dict(elements=arr(MASS_ROW,0,128),liquid_volume_m3=VOLUME_LEDGER,argon_mol=GAS_LEDGER,vacuum_gas_mol=GAS_LEDGER,rf_input_j=number('J',0),absorbed_heat_j=number('J',0),lost_heat_j=number('J'),stored_heat_j=number('J'),thermal_residual_j=number('J'),active_template_id=nullable(TEXT),evidence=TEXT))
CAL_OBSERVATION=obj(dict(reference_id=TEXT,dilution_factor=number('1',1,1e6),line_integrals=arr(LINE_INTEGRAL,1,32)))
CAPS={}
def cap(k,ins,outs,components,description,kind='device_command',visibility='agent',example=None):CAPS[k]=[ins,outs,components,description,kind,visibility,example or {},[]]
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body','rf_generator','spectrometer_vacuum'],'一次性可信宿主初始化响应/热/流/真空代理；未知量明确假设','configuration','evaluator',PLANT_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_inlet'],'可信宿主连接多元素溶液；未报告元素保持未知，不从标签直接返回浓度','device_command','evaluator',SAMPLE_EXAMPLE)
cap('sample.unload',obj({'recipient_id':TEXT}),obj({'unloaded':BOOL,'volume_m3':number('m^3',0)}),['sample_inlet'],'仅将剩余外部溶液交回宿主，不返还已耗样品','device_command','evaluator',{'recipient_id':'host_return'})
cap('fluidics.clean',obj({}),obj({'removed_mass_kg':number('kg',0),'removed_volume_m3':number('m^3',0)}),['spray_chamber','torch'],'熄炬冷态外部清理残料到废弃仓；非未安装的自动洗样器','device_command','evaluator')
cap('cover.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['plasma_door'],'维护门开闭；开门立即熄炬并取消采集，已转移物料和已采数据保留；180s维护触摸建议另行查询','device_command','evaluator',{'opened':False})
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','rf_generator'],'逻辑供电；断电停止等离子体与当前采集并保留残留',example={'enabled':True})
cap('vacuum.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['spectrometer_vacuum','vacuum_isolation_valve'],'真空泵目标与有限阀芯；理想绝压演化不等于厂家泵速或泄漏标定',example={'enabled':True})
cap('method.configure',METHOD,obj({'configured':BOOL}),['view_mirror','rf_generator','echelle_grating'],'同次多元素谱线定量；视路镜移动，不旋转炬管；方法变化失配空白和标线',example=METHOD_EXAMPLE)
cap('plasma.ignite',obj({}),obj({'igniting':BOOL}),['torch','rf_generator'],'真空/冷却/排风/闭门/气源许可下有限点炬和宿主稳定等待')
cap('plasma.extinguish',obj({}),obj({'lit':BOOL}),['torch','rf_generator'],'熄炬停止RF和测量，不回滚已取溶液')
cap('plasma.standby',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['torch','rf_generator'],'Eco仅待机：500W及5.5L/min已报整机氩耗参考；待机不执行分析',example={'enabled':True})
cap('measurement.start',obj({}),obj({'operation_id':TEXT}),['sample_inlet','spray_chamber','torch','ccd_detector'],'共享采样窗同时观测所有已建线位；所选谱线按各自实际观察标线定量')
cap('measurement.stop',obj({}),obj({'phase':TEXT}),['spray_chamber','torch'],'取消保留已转移元素、残料和完整已采窗口，不返回伪完成浓度')
cap('process.measure',obj({}),obj(dict(phase=TEXT,stage=TEXT,progress=number('1',0,1),concentrations=arr(CONCENTRATION,0,32),total_relative=nullable(number('1')))),['display_panel'],'纯读取进度与多元素推断结果','query')
cap('result.read',obj({}),RESULT,['display_panel'],'读取冻结采集方法对应多元素结果','query')
cap('spectrum.read',obj({}),SPECTRUM,['ccd_detector'],'读取同次观测的167–800nm非均匀合成光谱与全部已建线读数；非原始百万像素CCD','query')
cap('spectrum.lines',obj({'line_ids':arr(TEXT,1,32)}),obj({'lines':arr(LINE_OBSERVATION,1,32),'sample_window_s':nullable(WINDOW)}),['ccd_detector'],'从已存同次全线观测再读原未选择的谱线，不重采样不推进时间','query',example={'line_ids':['cu_324754']})
cap('result.export',obj({'format':enum(['json','csv'])}),obj({'format':TEXT,'content':TEXT}),['display_panel'],'返回实际多元素数组的JSON/CSV内容；不写任意外部文件','query',example={'format':'json'})
cap('blank.read',obj({}),obj({'valid':BOOL,'blank_json':TEXT}),['display_panel'],'纯读条件匹配的实际观察空白','query')
cap('calibration.fit',obj({'curve_id':TEXT,'observations':arr(CAL_OBSERVATION,3,20),'accept_same_response_conditions':BOOL}),obj({'curve_id':TEXT,'line_count':integer(1,32),'minimum_r_squared':number('1')}),['display_panel'],'实际多元素标准积分与已报告标准浓度逐线拟合；仅内插，不把理论元素比例作实测','analysis',example={'curve_id':'multi','observations':[],'accept_same_response_conditions':True})
cap('calibration.select',obj({'curve_id':TEXT}),obj({'selected':TEXT}),['display_panel'],'选择响应条件匹配且覆盖全部定量谱线的标线',example={'curve_id':'multi'})
cap('calibration.read',obj({'curve_id':TEXT}),obj({'curve_json':TEXT}),['display_panel'],'纯读标线与原始观察点','query',example={'curve_id':'multi'})
cap('records.list',obj({}),obj({'record_ids':arr(TEXT,0,MAX_RESULTS),'curve_ids':arr(TEXT,0,MAX_CURVES)}),['display_panel'],'实例内记录/标线目录；工程容量非OEM承诺','query')
cap('records.read',obj({'record_id':TEXT}),RESULT,['display_panel'],'读取完整历史观测与当时方法','query',example={'record_id':'record_1'})
cap('records.remove',obj({'record_id':TEXT}),obj({'removed':BOOL}),['display_panel'],'明确删除已归档结果；不删除受权限保护的异步终态快照',example={'record_id':'record_1'})
cap('template.read',obj({'substance_id':TEXT}),obj({'template_json':TEXT}),['display_panel'],'查询真实数据及单位/来源；不将缺失浓度或原子线位伪装物理样本','query',example={'substance_id':'cu_324754'})
cap('product.read',obj({}),LEDGER,['spray_chamber','torch'],'仅宿主读取每元素/溶液/氩/真空气体/热代理账本','query','evaluator')
cap('maintenance.check',obj({}),obj({'allowed':BOOL,'wait_remaining_s':number('s',0)}),['torch'],'纯查熄炬至少180s且宿主热代理已冷却的接触维护许可；非厂家电子计时门锁','query','evaluator')
UNSUPPORTED={'autosampler.sequence','internal_standard.add','torch.remove','oem.interference_correction','hardware.connect'}
for k in sorted(UNSUPPORTED):cap(k,obj({}),obj({}),['body'],'选配机构或完整原厂行为未实现，明确拒绝，不假成功')
for i in [1,2,3]:CAPS['calibration.fit'][6]['observations'].append(dict(reference_id='shimadzu_epa_ipc_assigned',dilution_factor=float(10**(3-i)),line_integrals=[dict(line_id=k,integrated_intensity_s=.1*i) for k in METHOD_EXAMPLE['line_ids']]))
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,origin='derived',visibility='agent',components=('display_panel',),write=None):SLOTS[k]=(s,u,origin,visibility,list(components),write);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','一次性宿主配置已就绪','setpoint',components=('body',));slot('sample.loaded',BOOL,'1','当前外部溶液连接存在','scene',components=('sample_inlet',));slot('power.enabled',BOOL,'1','逻辑供电','setpoint',components=('power_inlet',),write=('power.set','enabled'))
slot('cover.opened',BOOL,'1','等离子体舱维护门开','scene',components=('plasma_door',));slot('cover.angle_rad',number('rad',0,1.5),'rad','维护门工程角，需与最终来源几何一致','scene',components=('plasma_door',))
slot('view_mirror.position_m',number('m',0,.04),'m','视路镜有限位移，径向0/轴向.04m为工程映射','scene',components=('view_mirror',))
slot('vacuum.valve_lift_m',number('m',0,.003),'m','真空隔离阀工程升程','scene',components=('vacuum_isolation_valve',));slot('vacuum.pump_enabled',BOOL,'1','真空泵宿主目标','setpoint',components=('spectrometer_vacuum',),write=('vacuum.set','enabled'));slot('vacuum.pressure_pa',number('Pa',0,200000),'Pa','光谱腔理想绝压；非已标定压力传感器','derived',components=('spectrometer_vacuum',));slot('vacuum.ready',BOOL,'1','绝压低于宿主工程阈值','derived',components=('spectrometer_vacuum',))
for k in ['target_power_w','actual_power_w']:slot('rf.'+k,number('W',0,1600),'W','RF设定或有限爬升输出；27MHz频率固定，非6kVA整机功率','setpoint' if k.startswith('target') else 'derived',components=('rf_generator',))
for k in ['lit','igniting','standby']:slot('plasma.'+k,BOOL,'1','等离子体逻辑状态；Eco仅待机，不能采样','derived',components=('torch',))
slot('plasma.temperature_k',number('K',0,1e9),'K','单热容理想热代理，不是已测电子/激发温度','derived',components=('torch',));slot('maintenance.elapsed_s',number('s',0),'s','距熄炬时间；至少180s且冷态才允许维护','derived',components=('torch',));slot('gas.total_flow_m3_s',number('m^3/s',0,.002),'m^3/s','氩总体积流量，按宿主声明参考温压计算摩尔消耗','derived',components=('argon_inlet',))
slot('method.view',TEXT,'1','当前方法轴向或径向观测','setpoint',components=('view_mirror',));slot('method.line_ids',arr(TEXT,0,32),'1','当前仅定量所选线ID，全线观测另行保存','setpoint',components=('ccd_detector',))
for k in ['phase','stage']:slot('measurement.'+k,TEXT,'1','当前有限测量阶段','derived')
slot('measurement.elapsed_s',number('s',0),'s','当前采集已推进时间');slot('measurement.progress',number('1',0,1),'1','独立重复与阶段的推进比例');slot('measurement.completed_repeats',integer(0,20),'1','完成的独立重复次数')
slot('detector.total_relative',nullable(number('1')),'1','最近完整共同窗口内建模线峰相对信号和，非W','sensor',components=('ccd_detector',));slot('detector.last_sample_time_s',nullable(number('s',0)),'s','最近完整共同光学窗口结束时刻','sensor',components=('ccd_detector',))
slot('result.concentrations',arr(CONCENTRATION,0,32),'1','每元素浓度字段各自为kg/m³，含空缺和质量标识')
for e in ['cu','zn']:slot('result.'+e+'_kg_m3',nullable(number('kg/m^3')),'kg/m^3',e.upper()+'所选线的已校准推断值，未测/未标定为null')
for k in ['blank.valid','calibration.ready']:slot(k,BOOL,'1','当前响应条件及所选谱线匹配状态')
slot('storage.result_count',integer(0,MAX_RESULTS),'1','实例内完整结果数');slot('storage.curve_count',integer(0,MAX_CURVES),'1','实例内多元素标线数');slot('safety.warning',TEXT,'1','有限模型互锁/QC状态，不含样品隐藏真值');slot('truth.ledger',LEDGER,'1','真实元素及物料账本仅可信宿主可见','process_truth','evaluator',('torch','spray_chamber'))

def _library():
 p=ROOT/TEMPLATE_REL
 return json.loads(p.read_text()) if p.exists() else {'lines':{},'compounds':{}}
def _move(value,target,rate,dt):return value+max(-rate*dt,min(rate*dt,target-value))
def _values(record):
 result=record.get('element_concentrations_kg_m3')
 if result is None and record.get('element'):
  result={record['element']:record.get('concentration_kg_m3')}
 if not isinstance(result,dict):raise ActionRejected('LIQUID_CONCENTRATION_REQUIRED')
 clean={}
 for element,c in result.items():
  if c is None:continue
  if not isinstance(element,str) or not element:raise ActionRejected('INVALID_REFERENCE')
  validate(number('kg/m^3',0,100),c);clean[element]=float(c)
 if not clean:raise ActionRejected('LIQUID_CONCENTRATION_REQUIRED')
 return clean

class ShimadzuIcpe9800Runtime:
 def __init__(self,instance_id='icpe_01',library=None):
  self.instance_id=instance_id;self.library=_library() if library is None else copy.deepcopy(library);self.templates=self.library.get('compounds',{});self.lines=self.library.get('lines',{});self.time_s=0.;self.revision=0;self.plant=None;self.method=None;self.sample=None;self.powered=False;self.opened=False;self.mirror=0.;self.valve=0.;self.pump=False;self.pressure=101325.;self.rf=0.;self.lit=False;self.igniting=False;self.standby=False;self.ignition_elapsed=0.;self.stable_elapsed=0.;self.maintenance_elapsed=180.;self.temperature=298.15;self.initial_temperature=298.15;self.rf_energy=0.;self.absorbed_heat=0.;self.lost_heat=0.;self.gas=dict(initial=0.,remaining=0.,consumed=0.);self.vacuum_gas=dict(initial=0.,remaining=0.,consumed=0.)
  self._masses={};self._known_elements=set();self._volume=dict(registered=0.,aerosol=0.,waste=0.,evaporated_liquid_equivalent=0.,removed=0.);self.phase='idle';self.stage='idle';self.elapsed_s=0.;self.warning='';self.operation_id='';self._op_count=0;self._records={};self._curves={};self._selected='';self._blanks={};self._run_method=None;self._run_key=None;self._run_kind='';self._run_dilution=1.;self._run_blank={};self._run_curve=None;self._stage_elapsed=0.;self._plan_index=0;self._active=None;self.assays=[];self._concentrations=[];self._qc=[];self._last_point=None;self._spectrum=dict(wavelength_m=[],intensity_relative=[],line_readouts=[],sample_window_s=None,evidence='not_sampled',coverage='No acquisition yet.');self._pending_mass={};self._pending_dt=0.;self._sample_start=0.;self._next_detector=0.;self._spectral_cache=None
  self._rng=np.random.default_rng(0);self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._operation_records={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return ShimadzuIcpe9800Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in ['library','templates','lines','_rng','_spectral_cache']})|{'_rng_state':copy.deepcopy(self._rng.bit_generator.state)}
 def _checkpoint(self):
  keep=['library','templates','lines','_records','_curves','_operation_records','_requests','_rng','_spectral_cache'];snapshot=copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in keep})
  for k in keep:snapshot[k]=dict(getattr(self,k)) if k in ['_records','_curves','_operation_records','_requests'] else getattr(self,k)
  return snapshot,copy.deepcopy(self._rng.bit_generator.state)
 def _reference(self,key,physical=True):
  r=self.templates.get(key,self.lines.get(key))
  if r is None:raise ActionRejected('UNKNOWN_REFERENCE')
  if physical:_values(r)
  return r
 def _key(self):return json.dumps({k:v for k,v in (self.method or {}).items() if k not in ['repeats','line_ids']},sort_keys=True)
 def _blank_valid(self):return self._key() in self._blanks and all(k in self._blanks[self._key()] for k in (self.method or {}).get('line_ids',[]))
 def _calibration_ready(self):return self._selected in self._curves and self._curves[self._selected]['key']==self._key() and set((self.method or {}).get('line_ids',[]))<=set(self._curves[self._selected]['lines'])
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _cold(self):return not self.lit and not self.igniting and self.temperature<=353.15
 def _rf_target(self):return 500. if self.lit and self.standby else self.method['rf_power_w'] if (self.lit or self.igniting) and self.method and self.powered else 0.
 def _flow(self):return 5.5e-3/60 if self.lit and self.standby else self.method['argon_flow_m3_s'] if (self.lit or self.igniting) and self.method and self.powered else 0.
 def _vacuum_ready(self):return self.plant is not None and self.pressure<=self.plant['vacuum_ready_threshold_pa']
 def _mirror_target(self):return .04 if self.method and self.method['view']=='axial' else 0.
 def _element(self,e):
  if e not in self._masses:self._masses[e]=dict(registered=0.,aerosol=0.,plasma=0.,waste=0.,exhaust=0.,removed=0.)
  return self._masses[e]
 def _ledger(self):
  rows=[]
  for e,m in sorted(self._masses.items()):
   x=dict(m,sample=(self.sample['concentrations'].get(e,0.)*self.sample['volume'] if self.sample else 0.));x['residual']=x['registered']-math.fsum(v for k,v in x.items() if k!='registered');rows.append(dict(element=e,**x))
  v=dict(self._volume,sample=self.sample['volume'] if self.sample else 0.);v['residual']=v['registered']-math.fsum(x for k,x in v.items() if k!='registered');stored=(self.temperature-self.initial_temperature)*(self.plant['thermal_capacity_j_k'] if self.plant else 0.)
  gas=lambda x:dict(x,residual=x['initial']-x['remaining']-x['consumed'])
  return dict(elements=rows,liquid_volume_m3=v,argon_mol=gas(self.gas),vacuum_gas_mol=gas(self.vacuum_gas),rf_input_j=self.rf_energy,absorbed_heat_j=self.absorbed_heat,lost_heat_j=self.lost_heat,stored_heat_j=stored,thermal_residual_j=self.absorbed_heat-self.lost_heat-stored,active_template_id=self.sample['template_id'] if self.sample else None,evidence='Tracked declared elements only; unreported composition is unknown. Evaporation is liquid-equivalent volume, not gas volume. Ar reference T/P explicit; ideal vacuum chamber gas and lumped absorbed-RF heat, not complete plasma chemistry.')
 def _result(self):return dict(phase=self.phase,stage=self.stage,complete=self.phase=='succeeded',view=(self._run_method or {}).get('view',''),concentrations=copy.deepcopy(self._concentrations),assays=copy.deepcopy(self.assays),active_assay=copy.deepcopy(self._active),qc=list(dict.fromkeys(self._qc)),evidence='synthetic_simultaneous_emission_from_source_concentrations_and_atomic_lines; not measured CCD or OEM response',template_source=TEMPLATE_REL,acquisition_method_json=json.dumps(self._run_method,sort_keys=True))
 def read(self,visibility='agent'):
  prog=(len(self.assays)+self._plan_index/3)/max(1,(self._run_method or {}).get('repeats',1)) if self.phase=='running' else 1. if self.phase=='succeeded' else 0.
  conc=lambda e:next((x['concentration_kg_m3'] for x in self._concentrations if x['element'].lower()==e),None)
  v={'plant.configured':self.plant is not None,'sample.loaded':self.sample is not None,'power.enabled':self.powered,'cover.opened':self.opened,'cover.angle_rad':1.5 if self.opened else 0.,'view_mirror.position_m':self.mirror,'vacuum.valve_lift_m':self.valve,'vacuum.pump_enabled':self.pump,'vacuum.pressure_pa':self.pressure,'vacuum.ready':self._vacuum_ready(),'rf.target_power_w':self._rf_target(),'rf.actual_power_w':self.rf,'plasma.lit':self.lit,'plasma.igniting':self.igniting,'plasma.standby':self.standby,'plasma.temperature_k':self.temperature,'maintenance.elapsed_s':self.maintenance_elapsed,'gas.total_flow_m3_s':self._flow(),'method.view':self.method['view'] if self.method else '', 'method.line_ids':list(self.method['line_ids']) if self.method else [],'measurement.phase':self.phase,'measurement.stage':self.stage,'measurement.elapsed_s':self.elapsed_s,'measurement.progress':max(0.,min(1.,prog)),'measurement.completed_repeats':len(self.assays),'detector.total_relative':sum(x['intensity_relative'] for x in self._last_point['lines']) if self._last_point else None,'detector.last_sample_time_s':self._last_point['time_s'] if self._last_point else None,'result.concentrations':copy.deepcopy(self._concentrations),'result.cu_kg_m3':conc('cu'),'result.zn_kg_m3':conc('zn'),'blank.valid':self._blank_valid(),'calibration.ready':self._calibration_ready(),'storage.result_count':len(self._records),'storage.curve_count':len(self._curves),'safety.warning':self.warning}
  if visibility!='agent':v['truth.ledger']=self._ledger()
  return v
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[k][0],a);snapshot,rng=self._checkpoint()
  try:
   result=self._invoke(k,a);validate(CAPS[k][1],result)
  except Exception:
   self.__dict__.clear();self.__dict__.update(snapshot);self._rng.bit_generator.state=rng;raise
  if CAPS[k][4]!='query':self.revision+=1;self._archive_operation()
  return copy.deepcopy(result)
 def _invoke(self,k,a):
  if k=='plant.configure':
   if self.plant is not None or self.time_s!=0:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_assumed_response']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   if a['vacuum_base_pressure_pa']>=a['vacuum_ready_threshold_pa']:raise ActionRejected('VACUUM_THRESHOLD_ORDER')
   self.plant=copy.deepcopy(a);self._rng=np.random.default_rng(a['seed']);self.temperature=self.initial_temperature=a['ambient_k'];self.pressure=a['gas_reference_pressure_pa'];self.gas=dict(initial=a['argon_inventory_mol'],remaining=a['argon_inventory_mol'],consumed=0.);n=self.pressure*a['vacuum_chamber_volume_m3']/(R*a['ambient_k']);self.vacuum_gas=dict(initial=n,remaining=n,consumed=0.);return {'configured':True}
  if k=='sample.load':
   self._idle()
   if self.sample:raise ActionRejected('UNLOAD_REQUIRED')
   if not a['is_aqueous']:raise ActionRejected('UNSUPPORTED_MATRIX')
   if not a['sample_id'] or not a['accept_unknown_conditions']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   values={} if a['kind']=='blank' else _values(self._reference(a['template_id']))
   if a['assumed_elements'] and not a['accept_assumed_elements']:raise ActionRejected('ASSUMED_ELEMENT_ACK_REQUIRED')
   if a['kind']=='standard' and a['assumed_elements']:raise ActionRejected('STANDARD_REQUIRES_REPORTED_VALUES')
   if len({x['element'] for x in a['assumed_elements']})!=len(a['assumed_elements']):raise ActionRejected('DUPLICATE_ELEMENT')
   for x in a['assumed_elements']:
    if x['element'] in values:raise ActionRejected('REFERENCE_OVERRIDE_FORBIDDEN')
    values[x['element']]=x['concentration_kg_m3']
   if len(values)>128:raise ActionRejected('ELEMENT_CAPACITY')
   if a['kind']!='blank' and not values:raise ActionRejected('LIQUID_CONCENTRATION_REQUIRED')
   if a['kind']=='standard':
    eligible=self._reference(a['template_id']).get('calibration_eligible_elements',[])
    if not eligible:raise ActionRejected('CALIBRATION_REFERENCE_REQUIRED')
   effective={e:c/a['dilution_factor'] for e,c in values.items()};self.sample=dict(id=a['sample_id'],template_id=a['template_id'],kind=a['kind'],volume=a['volume_m3'],concentrations=effective,dilution=a['dilution_factor'],background=a['background_relative'],slope=a['background_slope']);self._known_elements.update(effective);self._volume['registered']+=a['volume_m3']
   for e,c in effective.items():self._element(e)['registered']+=c*a['volume_m3']
   return {'loaded':True}
  if k=='sample.unload':
   self._idle()
   if not self.sample:raise ActionRejected('SAMPLE_REQUIRED')
   if not a['recipient_id']:raise ActionRejected('RECIPIENT_REQUIRED')
   v=self.sample['volume'];self._volume['removed']+=v
   for e,c in self.sample['concentrations'].items():self._element(e)['removed']+=c*v
   self.sample=None;return dict(unloaded=True,volume_m3=v)
  if k=='fluidics.clean':
   self._idle()
   if not self._cold() or self.maintenance_elapsed<180.:raise ActionRejected('MAINTENANCE_WAIT_REQUIRED')
   mass=0.
   for m in self._masses.values():mass+=m['aerosol']+m['plasma'];m['waste']+=m['aerosol']+m['plasma'];m['aerosol']=m['plasma']=0.
   v=self._volume['aerosol'];self._volume['waste']+=v;self._volume['aerosol']=0.;return dict(removed_mass_kg=mass,removed_volume_m3=v)
  if k=='cover.set':
   self.opened=a['opened']
   if self.opened:self._extinguish('SAFETY_DOOR_OPEN')
   return {'opened':self.opened}
  if k=='maintenance.check':return dict(allowed=self._cold() and self.maintenance_elapsed>=180.,wait_remaining_s=max(0.,180.-self.maintenance_elapsed))
  if k=='power.set':
   if a['enabled'] and (not self.plant or self.opened):raise ActionRejected('POWER_INTERLOCK')
   self.powered=a['enabled']
   if not self.powered:self._extinguish('POWER_LOSS');self.pump=False
   return {'enabled':self.powered}
  if k=='vacuum.set':
   if a['enabled'] and not self.powered:raise ActionRejected('POWER_REQUIRED')
   self.pump=a['enabled'];return {'enabled':self.pump}
  if k=='method.configure':
   self._idle()
   if not self.plant:raise ActionRejected('PLANT_REQUIRED')
   if self.lit or self.igniting:raise ActionRejected('EXTINGUISH_BEFORE_METHOD_CHANGE')
   if len(set(a['line_ids']))!=len(a['line_ids']):raise ActionRejected('DUPLICATE_LINE')
   for line_id in a['line_ids']:
    line=self.lines.get(line_id)
    if not line or not isinstance(line.get('element'),str):raise ActionRejected('UNKNOWN_LINE')
    if not line.get('spectrum_eligible',False):raise ActionRejected('SOURCE_REFERENCE_ONLY')
    validate(number('m',167e-9,800e-9),line.get('wavelength_m'))
   if sum(math.ceil(a[k]/self.plant['detector_period_s']-1e-12) for k in ['stabilization_s','integration_s','flush_s'])*a['repeats']>MAX_TRACE_POINTS:raise ActionRejected('TRACE_CAPACITY')
   self.method=copy.deepcopy(a);return {'configured':True}
  if k=='plasma.ignite':
   self._idle()
   if not self.powered or not self.method or self.opened or self.lit or self.igniting or not self._vacuum_ready() or not self.plant['exhaust_available'] or self.gas['remaining']<=0 or abs(self.mirror-self._mirror_target())>1e-10:raise ActionRejected('IGNITION_INTERLOCK')
   self.igniting=True;self.ignition_elapsed=0.;self.stable_elapsed=0.;self.standby=False;self.maintenance_elapsed=0.;return {'igniting':True}
  if k=='plasma.extinguish':self._extinguish('USER_EXTINGUISH');return {'lit':False}
  if k=='plasma.standby':
   self._idle()
   if not self.lit:raise ActionRejected('PLASMA_REQUIRED')
   self.standby=a['enabled'];self.stable_elapsed=0.;return {'enabled':self.standby}
  if k=='measurement.start':return self._start()
  if k=='measurement.stop':self._stop('USER_CANCEL');return {'phase':self.phase}
  if k=='process.measure':return dict(phase=self.phase,stage=self.stage,progress=self.read()['measurement.progress'],concentrations=copy.deepcopy(self._concentrations),total_relative=self.read()['detector.total_relative'])
  if k=='result.read':return self._result()
  if k=='spectrum.read':return self._spectrum
  if k=='spectrum.lines':
   lookup={x['line_id']:x for x in self._spectrum['line_readouts']}
   if any(x not in lookup for x in a['line_ids']):raise ActionRejected('LINE_NOT_CAPTURED')
   return dict(lines=[lookup[x] for x in a['line_ids']],sample_window_s=self._spectrum['sample_window_s'])
  if k=='result.export':
   if a['format']=='json':content=json.dumps(self._result(),ensure_ascii=False,allow_nan=False)
   else:
    f=io.StringIO();w=csv.writer(f);w.writerow(['repeat','time_s','window_start_s','window_end_s','acquiring','element','line_id','wavelength_m','intensity_relative','background_relative','corrected_relative'])
    for i,assay in enumerate(self.assays+([self._active] if self._active else [])):
     for p in assay['points']:
      for row in p['lines']:w.writerow([i,p['time_s'],*p['sample_window_s'],p['acquiring'],row['element'],row['line_id'],row['wavelength_m'],row['intensity_relative'],row['background_relative'],row['corrected_relative']])
    content=f.getvalue()
   return dict(format=a['format'],content=content)
  if k=='blank.read':return dict(valid=self._blank_valid(),blank_json=json.dumps(self._blanks.get(self._key()),sort_keys=True))
  if k=='calibration.fit':return self._fit(a)
  if k=='calibration.select':
   self._idle();curve=self._curves.get(a['curve_id'])
   if not curve:raise ActionRejected('UNKNOWN_CURVE')
   if curve['key']!=self._key() or not set(self.method['line_ids'])<=set(curve['lines']):raise ActionRejected('CALIBRATION_SIGNATURE_MISMATCH')
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
 def _fit(self,a):
  self._idle()
  if not self.method or not self._blank_valid():raise ActionRejected('MATCHED_BLANK_REQUIRED')
  if not a['curve_id'] or a['curve_id'] in self._curves:raise ActionRejected('CURVE_ID_CONFLICT')
  if len(self._curves)>=MAX_CURVES:raise ActionRejected('CURVE_CAPACITY')
  if not a['accept_same_response_conditions']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
  fitted={}
  for line_id in self.method['line_ids']:
   element=self.lines[line_id]['element'];xs=[];ys=[]
   for obs in a['observations']:
    ref=self._reference(obs['reference_id']);values=_values(ref)
    if element not in values or element not in ref.get('calibration_eligible_elements',[]):raise ActionRejected('CALIBRATION_REFERENCE_REQUIRED')
    points={x['line_id']:x['integrated_intensity_s'] for x in obs['line_integrals']}
    if len(points)!=len(obs['line_integrals']) or line_id not in points:raise ActionRejected('CALIBRATION_LINE_REQUIRED')
    xs.append(values[element]/obs['dilution_factor']);ys.append(points[line_id])
   x=np.asarray(xs);y=np.asarray(ys)
   if len(set(xs))<3 or max(xs)<=min(xs):raise ActionRejected('CALIBRATION_DISTINCT_LEVELS')
   center=float(x.mean());scale=float(np.ptp(x));z=(x-center)/scale;yscale=max(float(np.max(np.abs(y))),1e-300);yn=y/yscale;coefficient=np.linalg.lstsq(np.column_stack([z,np.ones(len(x))]),yn,rcond=None)[0];prediction=coefficient[0]*z+coefficient[1];ss=float(np.sum((yn-yn.mean())**2));r2=1-float(np.sum((yn-prediction)**2))/ss if ss>0 else -math.inf
   with np.errstate(over='ignore',invalid='ignore'):
    slope=float(coefficient[0]*yscale/scale);intercept=float((coefficient[1]-coefficient[0]*center/scale)*yscale)
   if not all(math.isfinite(v) for v in [slope,intercept,r2]) or slope<=0 or r2<.95:raise ActionRejected('CALIBRATION_FIT_INVALID')
   fitted[line_id]=dict(element=element,slope=slope,intercept=intercept,r_squared=r2,concentration_range_kg_m3=[min(xs),max(xs)])
  self._curves[a['curve_id']]=dict(key=self._key(),lines=fitted,observations=copy.deepcopy(a['observations']),method=copy.deepcopy(self.method),units='integrated relative intensity seconds = slope(s*m^3/kg)*post-dilution concentration(kg/m^3)+intercept(s)',evidence='actual caller supplied standard observations and reported prepared-reference concentrations; no measured factory response inferred');return dict(curve_id=a['curve_id'],line_count=len(fitted),minimum_r_squared=min(x['r_squared'] for x in fitted.values()))
 def _physical_lines(self):return {k:v for k,v in self.lines.items() if v.get('spectrum_eligible',False) and 167e-9<=v.get('wavelength_m',0)<=800e-9}
 def _new_assay(self):return dict(points=[],line_integrals=[dict(line_id=k,integrated_intensity_s=0.) for k in self._physical_lines()],integration_time_s=0.,complete=False)
 def _start(self):
  self._idle()
  if not self.plant or not self.method or not self.sample:raise ActionRejected('SAMPLE_METHOD_REQUIRED')
  if not self.powered or not self.lit or self.standby or self.opened or not self._vacuum_ready() or abs(self.mirror-self._mirror_target())>1e-10 or abs(self.rf-self._rf_target())>1e-8 or self.stable_elapsed+1e-10<self.plant['startup_stabilization_s']:raise ActionRejected('MEASUREMENT_INTERLOCK')
  if len(self._records)>=MAX_RESULTS:raise ActionRejected('RESULT_CAPACITY')
  for k in self.method['line_ids']:
   e=self.lines[k]['element']
   if self.sample['kind']!='blank' and e not in self.sample['concentrations']:raise ActionRejected('UNREPORTED_ELEMENT')
   if self.sample['kind']=='standard' and e not in self._reference(self.sample['template_id']).get('calibration_eligible_elements',[]):raise ActionRejected('CALIBRATION_REFERENCE_REQUIRED')
  if self.sample['kind']!='blank' and not self._blank_valid():raise ActionRejected('MATCHED_BLANK_REQUIRED')
  if self.sample['kind']=='sample' and not self._calibration_ready():raise ActionRejected('CALIBRATION_REQUIRED')
  required=self.plant['natural_uptake_m3_s']*(self.method['stabilization_s']+self.method['integration_s'])*self.method['repeats']
  if self.sample['volume']+1e-20<required:raise ActionRejected('INSUFFICIENT_SAMPLE_VOLUME')
  self._run_method=copy.deepcopy(self.method);self._run_key=self._key();self._run_kind=self.sample['kind'];self._run_dilution=self.sample['dilution'];self._run_blank=copy.deepcopy(self._blanks.get(self._key(),{}));self._run_curve=copy.deepcopy(self._curves.get(self._selected)) if self._calibration_ready() else None
  self.phase='running';self.stage='stabilization';self.elapsed_s=0.;self._plan_index=0;self._stage_elapsed=0.;self.assays=[];self._active=self._new_assay();self._concentrations=[];self._qc=[];self.warning='';self._last_point=None;self._spectrum=dict(wavelength_m=[],intensity_relative=[],line_readouts=[],sample_window_s=None,evidence='not_sampled',coverage='No complete window in this acquisition yet.');self._pending_mass={};self._pending_dt=0.;self._sample_start=self.time_s;self._next_detector=self.time_s+self.plant['detector_period_s'];self._op_count+=1;self.operation_id=f'{self.instance_id}:operation:{self._op_count}'
  return {'operation_id':self.operation_id}
 def _stop(self,reason):
  if self.phase=='running':
   self.phase='cancelled';self.stage='cancelled';self.warning=reason;self._concentrations=[];self._qc.append('partial acquisition; no completed inference');self._pending_mass={};self._pending_dt=0.
 def _extinguish(self,reason):
  self._stop(reason);was_active=self.lit or self.igniting;self.lit=self.igniting=self.standby=False;self.stable_elapsed=0.
  if was_active:self.maintenance_elapsed=0.;self.warning=reason
  if reason=='ARGON_EXHAUSTED' and self.phase=='cancelled':self.phase=self.stage='failed'
  self._archive_operation()
 def _prepare_spectrum(self):
  if self._spectral_cache is not None:return self._spectral_cache
  lines=self._physical_lines();sigma=self.plant['line_sigma_m'];centers=np.array([v['wavelength_m'] for v in lines.values()]);axis=np.unique(np.clip(np.concatenate([np.linspace(167e-9,800e-9,1024),*(c+sigma*np.linspace(-6,6,25) for c in centers)]),167e-9,800e-9))
  profiles=np.exp(-.5*((axis[None,:]-centers[:,None])/sigma)**2)
  indices=[(int(np.argmin(abs(axis-c))),int(np.argmin(abs(axis-max(167e-9,c-6*sigma)))),int(np.argmin(abs(axis-min(800e-9,c+6*sigma))))) for c in centers]
  self._spectral_cache=(lines,axis,profiles,indices);return self._spectral_cache
 def _detector(self):
  if self._pending_dt<=1e-14 or self.phase!='running':return
  lines,axis,profiles,indices=self._prepare_spectrum();p=self.plant;dt=self._pending_dt;factor=p['emission_gain_per_kg']*(self._run_method['rf_power_w']/1300.)*(p['axial_multiplier'] if self._run_method['view']=='axial' else 1.)
  amps=np.array([self._pending_mass.get(v['element'],0.)/dt*factor for v in lines.values()]);background=p['continuum_relative']+(self.sample['background'] if self.sample else 0.);slope=self.sample['slope'] if self.sample else 0.
  signal=np.sum(amps[:,None]*profiles,axis=0)+background+slope*(axis-483.5e-9)/(633e-9)
  if p['detector_noise_sd']:signal+=self._rng.normal(0.,p['detector_noise_sd'],len(axis))
  clipped=signal>=p['saturation_relative'];signal=np.minimum(signal,p['saturation_relative']);rows=[]
  for (k,line),(mid,left,right) in zip(lines.items(),indices):
   bg=float((signal[left]+signal[right])/2);raw=float(signal[mid]);corrected=raw-bg if self._run_method['background']=='linear_reference' else raw;available=self._run_kind=='blank' or line['element'] in (self.sample['concentrations'] if self.sample else {})
   rows.append(dict(line_id=k,element=line['element'],wavelength_m=line['wavelength_m'],intensity_relative=raw,background_relative=bg,corrected_relative=corrected,saturated=bool(clipped[mid] or clipped[left] or clipped[right]),element_response_available=available))
  point=dict(time_s=self.time_s,sample_window_s=[self._sample_start,self.time_s],acquiring=self.stage=='integration',lines=rows);self._active['points'].append(point);self._last_point=point
  self._spectrum=dict(wavelength_m=axis.tolist(),intensity_relative=signal.tolist(),line_readouts=copy.deepcopy(rows),sample_window_s=point['sample_window_s'],evidence='synthetic relative emission; common finite integration window; not original CCD pixels or radiometric power',coverage='167–800 nm:1024 coarse background points plus 25 points per eligible NIST transition, unique nonuniform grid. Only declared elements and retained residues contribute; unreported elements are unknown, not proven zero. OEM source-only aliases do not add peaks. Air/vacuum source coordinates retained without silent conversion.')
  if point['acquiring']:
   self._active['integration_time_s']+=dt
   for out,row in zip(self._active['line_integrals'],rows):out['integrated_intensity_s']+=row['corrected_relative']*dt
   for row in rows:
    if row['saturated'] and row['line_id'] in self._run_method['line_ids']:self._qc.append('saturated:'+row['line_id'])
  self._pending_mass={};self._pending_dt=0.;self._sample_start=self.time_s;self._next_detector=self.time_s+p['detector_period_s']
 def _finish_assay(self):
  self._active['complete']=True
  if self._run_kind!='blank':
   for row in self._active['line_integrals']:row['integrated_intensity_s']-=self._run_blank.get(row['line_id'],0.)
  self.assays.append(self._active);self._active=None
  if len(self.assays)<self._run_method['repeats']:
   self._active=self._new_assay();self._plan_index=0;self.stage='stabilization';self._stage_elapsed=0.;return
  self.phase='succeeded';self.stage='complete'
  if self._run_kind=='blank':self._blanks[self._run_key]={k:float(np.mean([next(x['integrated_intensity_s'] for x in a['line_integrals'] if x['line_id']==k) for a in self.assays])) for k in self._physical_lines()}
  for k in self._run_method['line_ids']:
   values=[next(x['integrated_intensity_s'] for x in a['line_integrals'] if x['line_id']==k) for a in self.assays];c=sd=cv=None;quality='uncalibrated standard/blank observation';curve=(self._run_curve or {}).get('lines',{}).get(k)
   if curve and self._run_kind=='sample':
    effective=[(x-curve['intercept'])/curve['slope'] for x in values];lo,hi=curve['concentration_range_kg_m3'];saturated='saturated:'+k in self._qc
    if saturated:quality='saturated; concentration unavailable'
    elif any(not math.isfinite(x) or x<lo or x>hi for x in effective):quality='outside calibration interpolation domain; concentration unavailable';self._qc.append('outside_calibration:'+k)
    else:
     donor=np.asarray(effective)*self._run_dilution;c=float(donor.mean());sd=float(donor.std(ddof=1)) if len(donor)>1 else None;cv=sd/abs(c) if sd is not None and c else None;quality='conditional synthetic response calibrated from observations; no OEM precision claim'
   self._concentrations.append(dict(element=self.lines[k]['element'],line_id=k,concentration_kg_m3=c,sd_kg_m3=sd,cv=cv,mean_integrated_intensity_s=float(np.mean(values)),quality=quality))
  self._records[f'record_{self._op_count}']=self._result();self._archive_operation()
 def _physical(self,dt):
  p=self.plant
  if p is None:return
  self.mirror=_move(self.mirror,self._mirror_target(),p['mirror_speed_m_s'],dt);v0=self.valve;self.valve=_move(self.valve,.003 if self.pump and self.powered else 0.,p['valve_speed_m_s'],dt)
  if self.pump and self.powered:
   old=self.pressure;self.pressure=p['vacuum_base_pressure_pa']+(old-p['vacuum_base_pressure_pa'])*math.exp(-p['vacuum_decay_s_inv']*(v0+self.valve)/.006*dt);n=(old-self.pressure)*p['vacuum_chamber_volume_m3']/(R*p['ambient_k']);self.vacuum_gas['remaining']-=n;self.vacuum_gas['consumed']+=n
  power0=self.rf;self.rf=_move(self.rf,self._rf_target(),p['rf_ramp_w_s'],dt);slope=(self.rf-power0)/dt;decay=p['thermal_loss_w_k']/p['thermal_capacity_j_k'];x=decay*dt;h1=-math.expm1(-x)/decay;h2=(dt-h1)/decay if abs(x)>1e-5 else dt*dt*(.5-x/6+x*x/24-x*x*x/120);old=self.temperature;self.temperature=p['ambient_k']+(old-p['ambient_k'])*math.exp(-x)+p['absorbed_rf_fraction']/p['thermal_capacity_j_k']*(power0*h1+slope*h2);energy=(power0+self.rf)/2*dt;absorbed=p['absorbed_rf_fraction']*energy;self.rf_energy+=energy;self.absorbed_heat+=absorbed;self.lost_heat+=absorbed-p['thermal_capacity_j_k']*(self.temperature-old)
  flow=self._flow();gas=flow*p['gas_reference_pressure_pa']/(R*p['gas_reference_temperature_k'])*dt;gas=min(gas,self.gas['remaining']);self.gas['remaining']-=gas;self.gas['consumed']+=gas
  if self.lit or self.igniting:self.maintenance_elapsed=0.
  else:self.maintenance_elapsed+=dt
  if self.igniting:self.ignition_elapsed+=dt
  if self.lit and not self.standby and abs(power0-self._rf_target())<1e-8:self.stable_elapsed+=dt
  if not self.lit:return
  a=1/p['aerosol_residence_s'];b=1/p['plasma_residence_s'];ea=math.exp(-a*dt);eb=math.exp(-b*dt);ha=-math.expm1(-a*dt)/a;hb=-math.expm1(-b*dt)/b;kernel=(ea-eb)/(b-a) if abs(b-a)>1e-7*max(a,b) else dt*math.exp(-.5*(a+b)*dt)
  feed_volume=p['natural_uptake_m3_s']*dt if self.phase=='running' and self.stage in ['stabilization','integration'] else 0.
  if self.sample:feed_volume=min(feed_volume,self.sample['volume']);self.sample['volume']-=feed_volume
  feed_rate=feed_volume/dt;eta=p['aerosol_fraction'];y=p['atomization_yield']
  for e,m in self._masses.items():
   c=self.sample['concentrations'].get(e,0.) if self.sample else 0.;feed=c*feed_rate*eta;old_a=m['aerosol'];old_p=m['plasma'];new_a=old_a*ea+feed*ha;released=max(0.,old_a+feed*dt-new_a);new_p=old_p*eb+y*(a*old_a-feed)*kernel+y*feed*hb;new_p=max(0.,new_p);out=max(0.,old_p+y*released-new_p);m['aerosol']=new_a;m['plasma']=new_p;m['waste']+=(1-eta)*c*feed_volume+(1-y)*released;m['exhaust']+=out
   if self.phase=='running':self._pending_mass[e]=self._pending_mass.get(e,0.)+out/b
  oldv=self._volume['aerosol'];newv=oldv*ea+eta*feed_rate*ha;self._volume['aerosol']=newv;self._volume['evaporated_liquid_equivalent']+=max(0.,oldv+eta*feed_volume-newv);self._volume['waste']+=(1-eta)*feed_volume
  if self.phase=='running':self._pending_dt+=dt
 def step(self,dt):
  validate(number('s',0,86400),dt)
  if dt==0:return self.read('evaluator')
  end=self.time_s+dt
  while self.time_s<end-1e-12:
   step=end-self.time_s
   if self.plant:
    p=self.plant
    for distance,rate in [(abs(self.rf-self._rf_target()),p['rf_ramp_w_s']),(abs(self.mirror-self._mirror_target()),p['mirror_speed_m_s']),(abs(self.valve-(.003 if self.pump and self.powered else 0.)),p['valve_speed_m_s'])]:
     if distance>1e-12:step=min(step,distance/rate)
    if self.igniting and self.ignition_elapsed<p['ignition_s']-1e-12:step=min(step,p['ignition_s']-self.ignition_elapsed)
    rate=self._flow()*p['gas_reference_pressure_pa']/(R*p['gas_reference_temperature_k'])
    if rate>0 and self.gas['remaining']>1e-15:step=min(step,self.gas['remaining']/rate)
    if self.phase=='running':
     duration=self._run_method[{'stabilization':'stabilization_s','integration':'integration_s','flush':'flush_s'}[self.stage]];step=min(step,max(0.,duration-self._stage_elapsed),max(0.,self._next_detector-self.time_s))
   if step<=1e-12:step=min(end-self.time_s,1e-12)
   running=self.phase=='running';self._physical(step);self.time_s+=step
   if running:self.elapsed_s+=step;self._stage_elapsed+=step
   if self.igniting and self.ignition_elapsed+1e-10>=self.plant['ignition_s'] and abs(self.rf-self._rf_target())<1e-8:self.igniting=False;self.lit=True;self.stable_elapsed=0.
   if self.phase=='running':
    duration=self._run_method[{'stabilization':'stabilization_s','integration':'integration_s','flush':'flush_s'}[self.stage]];boundary=self._stage_elapsed+1e-10>=duration
    if self.time_s+1e-10>=self._next_detector or boundary:self._detector()
    if boundary:
     if self.stage=='flush':self._finish_assay()
     else:self._plan_index+=1;self.stage=['stabilization','integration','flush'][self._plan_index];self._stage_elapsed=0.
   if (self.lit or self.igniting) and self.gas['remaining']<=1e-15:self._extinguish('ARGON_EXHAUSTED')
  self.time_s=end;self.revision+=1;self._archive_operation();return self.read('evaluator')
 def _operation_snapshot(self):return dict(protocol_version=PROTOCOL_VERSION,instance_id=self.instance_id,operation_id=self.operation_id,status=self.phase,state_revision=self.revision,clock={'domain':'simulation','time_s':self.time_s},result=self._result(),observations=self.session().read(instance_id=self.instance_id)['observations'],error=dict(code=self.warning,side_effects='partial') if self.phase=='failed' else None)
 def _archive_operation(self):
  if self.operation_id and self.phase in ['succeeded','failed','cancelled'] and self.operation_id not in self._operation_records:self._operation_records[self.operation_id]=self._operation_snapshot()

class ShimadzuIcpe9800Session:
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
        m=export_shimadzu_icpe9800_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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
'Chosen configuration is ICPE-9820 simultaneous axial/radial ICP-OES; ICPE-9810 is axial only. Standard natural aspiration and gravity drainage. AS-10/ASX autosamplers, peristaltic pump and online internal-standard addition are optional and absent. No original instrument LCD: six engineering readouts are a simulation-only external panel.',
'167–800nm coverage; <=0.005nm resolution is documented at200nm only. 27MHz RF maximum1600W;6kVA main-unit supply is not RF power or total heat. Host Gaussian sigma, detector noise, view factor and concentration gain are independent assumptions, not OEM calibrations or NIST Aki-derived response.',
'Normal11.7L/min and Eco5.5L/min are reported total-Ar reference consumptions without a manufacturer temperature/pressure basis. The host explicitly supplies absolute reference T/P for ideal molar accounting. Eco is standby only,500W; brochure approximately5L/min plasma gas is not5.5L/min total consumption. Other brochure12.333L/min normal consumption and10min/30min startup references remain source conflicts, not calibrated runtime defaults.',
'Finite mirror position selects view while the vertical torch remains fixed. Door angle, mirror displacement, isolation-valve stroke and rates are engineering proxies. Vacuum is an ideal pumped chamber with finite isolation valve and no modeled leak/backflow, not whole-optics argon purge. Ideal lumped plasma thermal state is not a measured electron temperature.',
'Opening the plasma door or losing modeled Ar extinguishes plasma and cancels measurement while retaining transferred matter and completed windows. Host coolant range/flow and exhaust are startup conditions; live coolant stoppage and the actual OEM control electronics are not modeled. At least180s after extinction plus an assumed cold-temperature threshold is an explicit host maintenance policy based on touch-safety advice, not a demonstrated OEM electronic timer lock.',
'True source concentrations drive multi-element liquid inputs. Unreported, censored, saturated-source and Si/SiO2-basis-ambiguous entries remain unknown; source-only atomic references cannot be loaded as concentration samples. At most128 tracked declared elements and0–100kg/m3 per-element numeric domain are engineering limits, not OEM quantitation ranges. Matrix and missing elemental species are not inferred.',
'All68 eligible NIST observed I/II lines are modeled concurrently irrespective of selected quantitative lines. OEM source-only lines do not create duplicate peaks. Source air/vacuum wavelength coordinates are retained without conversion; the synthetic grid is not a unified metrological vacuum axis. Aki and wavelength uncertainty are references, not signal gain or line-spread sigma.',
'Saved spectrum uses1024 coarse background points plus25 dense points per eligible line on a unique nonuniform167–800nm grid. It is a synthetic spectral surrogate, not the million-pixel cooled CCD or a measured whole spectrum. Contributions of unreported elements, other transitions, line self-absorption and radiative transfer are unmodeled; element_response_available=false never establishes chemical absence.',
'Natural uptake withdraws finite solution into aerosol and plasma compartments with exact first-order transfer; rejected aerosol and incomplete atomization go to waste. Each declared element balances registered=donor+aerosol+plasma+waste+exhaust+removed. Carrier solution evaporation is liquid-equivalent volume, not water gas volume or full plasma reaction stoichiometry. Ar and ideal vacuum gas each balance initial=remaining+consumed.',
'Each complete detector window shares full spectral values and simultaneous reference-flank background. Integrals use actual window lengths; matched observed blank and >=3 observed multi-element standards determine per-line response. Only calibration interpolation returns concentrations; clipping/out-of-range remain null. Source concentrations are not copied to output and no original source CCD/noise is reconstructed.',
'Response signatures bind view, RF, gas flow, stabilization/integration/flush windows and background. Selected line subset and repeat count may change without invalidating already observed full-line blank/covering curve. Dilution is explicit external host preparation, not an installed automatic diluter. All repeats retained; no hidden outlier deletion or negative-area clipping.',
'Historical asynchronous results are owner protected and immutable after later acquisitions/method changes or record deletion. Queries do not consume time or random numbers. Storage256results/32curves and20000trace-points are engineering bounds; no reboot persistence or OS-level isolation claim.',
'Published application options (autosampler, pump and online Y internal standard), exact original line/matrix/RF/view protocols, full OEM interference correction, certified response/noise/LOD/precision convergence, real hardware/HIL and reverse robot contact remain not_run. The current standard aqueous model does not install organic-solvent special Ar/O2 hardware.'
]
def export_shimadzu_icpe9800_contract_metadata():
 caps={};slots={};run=['measurement.phase','measurement.stage','measurement.elapsed_s','measurement.progress','measurement.completed_repeats'];detector=['detector.total_relative','detector.last_sample_time_s'];results=['result.concentrations','result.cu_kg_m3','result.zn_kg_m3'];plasma=['plasma.lit','plasma.igniting','plasma.standby','rf.target_power_w','gas.total_flow_m3_s'];thermal=['rf.actual_power_w','plasma.temperature_k','truth.ledger','maintenance.elapsed_s']
 immediate={'plant.configure':['plant.configured','plasma.temperature_k','vacuum.pressure_pa','vacuum.ready','truth.ledger'],'sample.load':['sample.loaded','truth.ledger'],'sample.unload':['sample.loaded','truth.ledger'],'fluidics.clean':['truth.ledger'],'cover.set':['cover.opened','cover.angle_rad',*plasma,'measurement.phase','measurement.stage','measurement.progress',*results,'safety.warning','maintenance.elapsed_s'],'power.set':['power.enabled','vacuum.pump_enabled',*plasma,'measurement.phase','measurement.stage','measurement.progress',*results,'safety.warning','maintenance.elapsed_s'],'vacuum.set':['vacuum.pump_enabled'],'method.configure':['method.view','method.line_ids','blank.valid','calibration.ready'],'plasma.ignite':['plasma.igniting','plasma.standby','rf.target_power_w','gas.total_flow_m3_s','maintenance.elapsed_s'],'plasma.extinguish':[*plasma,'measurement.phase','measurement.stage','measurement.progress',*results,'safety.warning','maintenance.elapsed_s'],'plasma.standby':['plasma.standby','rf.target_power_w','gas.total_flow_m3_s'],'measurement.start':[*run,*detector,*results,'safety.warning'],'measurement.stop':['measurement.phase','measurement.stage','measurement.progress',*results,'safety.warning'],'calibration.fit':['storage.curve_count'],'calibration.select':['calibration.ready'],'records.remove':['storage.result_count']}
 onstep={'cover.set':thermal,'power.set':[*thermal,'vacuum.valve_lift_m','vacuum.pressure_pa','vacuum.ready'],'vacuum.set':['vacuum.valve_lift_m','vacuum.pressure_pa','vacuum.ready','truth.ledger'],'method.configure':['view_mirror.position_m'],'plasma.ignite':[*thermal,'plasma.igniting','plasma.lit','safety.warning','rf.target_power_w','gas.total_flow_m3_s'],'plasma.extinguish':thermal,'plasma.standby':thermal,'measurement.start':[*run,*detector,*results,'truth.ledger','safety.warning','blank.valid','storage.result_count',*plasma],'measurement.stop':['truth.ledger']}
 complete={'measurement.start':[*results,'measurement.phase','measurement.stage','measurement.progress','measurement.completed_repeats','blank.valid','storage.result_count','truth.ledger']}
 observes={'plant.configure':['plant.configured'],'sample.load':['sample.loaded'],'sample.unload':['sample.loaded'],'cover.set':['cover.opened'],'power.set':['power.enabled'],'vacuum.set':['vacuum.pump_enabled'],'plasma.ignite':['plasma.igniting'],'plasma.extinguish':['plasma.lit'],'plasma.standby':['plasma.standby'],'measurement.start':['measurement.phase'],'measurement.stop':['measurement.phase'],'process.measure':['measurement.phase','measurement.stage','measurement.progress','result.concentrations','detector.total_relative'],'result.read':['measurement.phase','measurement.stage','result.concentrations'],'result.export':['measurement.phase','measurement.stage','result.concentrations'],'spectrum.read':['detector.total_relative','detector.last_sample_time_s'],'spectrum.lines':['detector.last_sample_time_s'],'blank.read':['blank.valid'],'records.list':['storage.result_count','storage.curve_count'],'product.read':['truth.ledger'],'maintenance.check':['maintenance.elapsed_s','plasma.temperature_k','plasma.lit','plasma.igniting']}
 for k,(schema,unit,origin,vis,components,write) in SLOTS.items():
  window=k.startswith('detector.') or k.startswith('result.');slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,origin=origin,visibility=vis,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'ShimadzuIcpe9800Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode='interval_mean' if window else 'instant',time_domain='simulation',window_description='最近完整共同光学窗口；浓度对应已完成重复及冻结响应方法' if window else None),source_ids=['simulation_assumption'])
 errors=sorted({'INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT'}|{n.exc.args[0].value for n in ast.walk(ast.parse(Path(__file__).read_text())) if isinstance(n,ast.Raise) and isinstance(n.exc,ast.Call) and isinstance(n.exc.func,ast.Name) and n.exc.func.id=='ActionRejected' and n.exc.args and isinstance(n.exc.args[0],ast.Constant) and isinstance(n.exc.args[0].value,str)})
 for k,(inp,out,components,label,kind,vis,example,_) in CAPS.items():
  unsupported=k in UNSUPPORTED;query=kind=='query';async_=k=='measurement.start';effects=[dict(state_id=state,timing=timing,description_zh={'immediate':'原子命令返回；无隐式时钟推进','on_step':'宿主时钟推进有限运动/多元素转移/共同采样','on_completion':'完成重复后保存观察结果与标定推断'}[timing]) for timing,table in [('immediate',immediate),('on_step',onstep),('on_completion',complete)] for state in dict.fromkeys(table.get(k,[]))]
  caps[k]=dict(function_zh=label,kind=kind,visibility=vis,component_ids=components,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.shimadzu_icpe9800.ShimadzuIcpe9800Session.invoke',invocation=f'session.invoke(instance_id="icpe_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if query or unsupported else [dict(description_zh='可信宿主多元素液体/响应配置；按能力核验电源、门、气源、真空、冷态与观察空白/标线',state_ids=['power.enabled','cover.opened','plasma.lit','vacuum.ready','measurement.phase'],enforced_by='runtime',check='Runtime._invoke and Session principal ACL')],state_effects=[] if query or unsupported else effects,observes=[] if unsupported else observes.get(k,[]),model_effects=dict(mode='none',description_zh='几何/实际场景另行绑定，查询不产生动作',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='所有独立重复完成有限稳定、同步多线积分及残料排出阶段' if async_ else '原子返回；未来有限响应仅由step推进',completion_state_ids=['measurement.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','multi_element_solution','natural_aspiration','argon_inventory','RF_surrogate','vacuum_optics','CCD_surrogate','result_store_256','curve_store_32'],partial_failure_zh='取消、开舱或失气保留已转移物料与完整已采窗口，不回滚实际作用'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in errors]+([dict(code='ARGON_EXHAUSTED',meaning_zh='气源耗尽熄炬，保留物料和已采数据',side_effects='partial')] if async_ else []),example_arguments=copy.deepcopy(example),verification=[],limitations=LIMITATIONS,source_ids=['manufacturer_primary','manufacturer_brochure','simulation_assumption'])
 mech=dict(component_ids=['sample_inlet','spray_chamber','torch','rf_generator','view_mirror','spectrometer_vacuum','vacuum_isolation_valve','echelle_grating','ccd_detector'],state_ids=list(slots),meaning_zh='各元素有限供样/气溶胶/等离子体仓守恒，同次全线合成发射与逐线真实观察标定',implementation='lumped',equations=['For each declared element dA/dt=eta*Q*c-A/tau_a; dP/dt=yield*A/tau_a-P/tau_p; exact first-order compartment transfers preserve elemental mass.','Rejected aerosol and unatomized mass go to waste; plasma outflow goes to exhaust. Registered=sum(donor,aerosol,plasma,waste,exhaust,removed).','I(lambda)=sum(gain*mean(P_element)*RF/1300*view_factor*Gaussian(lambda-line))+background+assumed event noise; intensity is relative, not W.','Observed corrected area=sum((line center-mean simultaneous flanks)*window_dt)-matched observed blank. Fit area=slope*prepared-reference concentration/dilution+intercept; interpolation only.','C*dT/dt=absorbed_fraction*RF-h*(T-Tambient); linearly ramped RF integrated analytically; absorbed-lost=C*(T-Tinitial).','Ar molar consumption=P_reference*Q_reference*dt/(R*T_reference); vacuum remaining gas=P_chamber*V/(R*Tambient), evacuated gas retained in ledger.'],parameters={},source_ids=['manufacturer_brochure','simulation_assumption'],limitations=LIMITATIONS)
 spec=importlib.util.spec_from_file_location('_icpe_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'icp_emission':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'；未知响应为宿主代理而非厂家标定。'
 mech['parameters']={k.lower():v for k,v in mech['parameters'].items()};return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'icp_emission':mech})

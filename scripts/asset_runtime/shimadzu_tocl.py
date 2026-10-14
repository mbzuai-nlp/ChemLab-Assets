"""TOC-L CSH: finite aqueous carbon aliquots, observed NDIR calibration and bounded records."""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
SLUG='shimadzu_tocl';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-080';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/expansion_batch03/t3_analytical/shimadzu_tocl/output_templates/library.json'
BOOL={'type':'boolean'};TEXT={'type':'string'}
class ActionRejected(ValueError):pass

def number(unit,low=None,high=None):
 s={'type':'number','x-unit':unit}
 if low is not None:s['minimum']=low
 if high is not None:s['maximum']=high
 return s

def obj(p):return dict(type='object',properties=p,required=list(p),additionalProperties=False)
def arr(s):return dict(type='array',items=s)
def integer(low,high):return dict(number('1',low,high),type='integer')
def enum(values):return dict(type='string',enum=values)
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
def vector(s,low=2,high=10000):return dict(arr(s),minItems=low,maxItems=high)
def validate(s,v):
 if 'anyOf' in s:
  if v is None and any(q.get('type')=='null' for q in s['anyOf']):return
  return validate(next(q for q in s['anyOf'] if q.get('type')!='null'),v)
 t=s.get('type')
 if t in ('number','integer'):
  if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):raise ActionRejected('INVALID_ARGUMENT')
  if t=='integer' and type(v) is not int:raise ActionRejected('INVALID_ARGUMENT')
  if not s.get('minimum',-math.inf)<=v<=s.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='null' and v is not None:raise ActionRejected('INVALID_ARGUMENT')
 elif t=='boolean' and type(v) is not bool:raise ActionRejected('INVALID_ARGUMENT')
 elif t=='string' and not isinstance(v,str):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='object':
  if not isinstance(v,dict) or not set(s['required'])<=v.keys() or not v.keys()<=s['properties'].keys():raise ActionRejected('INVALID_ARGUMENT')
  for k,x in v.items():validate(s['properties'][k],x)
 elif t=='array':
  if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
  for x in v:validate(s['items'],x)
 if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')


CHANNELS=['TC','IC','NPOC'];QUANTITIES=['TC','IC','TOC','NPOC'];CARBON_PARTS=['IC','NPOC','POC'];C_KG_MOL=.012011
RESULT_CAPACITY=2000;CURVE_CAPACITY=60;SYRINGE_MAX_M3=2e-6;SYRINGE_STROKE_M=.06
PHASE=enum(['idle','running','succeeded','cancelled','failed'])
def qobj(schema):return obj({k:copy.deepcopy(schema) for k in QUANTITIES})
CONCENTRATIONS=qobj(nullable(number('kg/m^3')))
PLANT=obj({'ambient_k':number('K',278.15,308.15),'furnace_capacity_j_k':number('J/K',1,1e5),'furnace_conductance_w_k':number('W/K',.001,1e5),'liquid_rate_m3_s':number('m^3/s',1e-10,1e-4),'selector_speed_rad_s':number('rad/s',.01,100),'ic_pump_speed_rad_s':number('rad/s',0,100),'conversion_rate_s_inv':number('1/s',.001,1000),'ic_release_rate_s_inv':number('1/s',.001,1000),'poc_strip_rate_s_inv':number('1/s',0,1000),'transport_tau_s':number('s',.001,1000),'ndir_gain_s_kg':number('s/kg',1,1e14),'ndir_baseline':number('1',-10,10),'ndir_noise_sd':number('1',0,.1),'detector_period_s':number('s',.001,1),'diluent_volume_m3':number('m^3',0,1.),'acid_volume_m3':number('m^3',0,1.),'acid_concentration_mol_m3':number('mol/m^3',.001,1e5),'oxygen_inventory_mol':number('mol',0,1000),'oxygen_mol_per_oc_mol':number('1',0,10),'carrier_ready':BOOL,'seed':integer(0,2147483647),'accept_lumped_model':BOOL})
PLANT_EXAMPLE=dict(ambient_k=298.15,furnace_capacity_j_k=20.,furnace_conductance_w_k=100.,liquid_rate_m3_s=2e-6,selector_speed_rad_s=30.,ic_pump_speed_rad_s=6.,conversion_rate_s_inv=100.,ic_release_rate_s_inv=100.,poc_strip_rate_s_inv=50.,transport_tau_s=.02,ndir_gain_s_kg=1e8,ndir_baseline=.01,ndir_noise_sd=1e-6,detector_period_s=.005,diluent_volume_m3=.001,acid_volume_m3=.001,acid_concentration_mol_m3=1000.,oxygen_inventory_mol=.1,oxygen_mol_per_oc_mol=1.,carrier_ready=True,seed=80,accept_lumped_model=True)
SAMPLE=obj({'sample_id':TEXT,'kind':enum(['blank','standard','sample']),'template_id':TEXT,'template_channel':enum(QUANTITIES),'volume_m3':number('m^3',1e-9,.1),'assumed_ic_kg_m3':nullable(number('kg/m^3',0,35)),'assumed_poc_kg_m3':nullable(number('kg/m^3',0,30)),'assumed_npoc_kg_m3':nullable(number('kg/m^3',0,30)),'accept_partition_assumptions':BOOL,'accept_unknown_conditions':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='external_water_sample',kind='sample',template_id='shimadzu_pfos_1',template_channel='TC',volume_m3=20e-6,assumed_ic_kg_m3=None,assumed_poc_kg_m3=0.,assumed_npoc_kg_m3=None,accept_partition_assumptions=True,accept_unknown_conditions=True)
BLANK_EXAMPLE=dict(SAMPLE_EXAMPLE,sample_id='external_carbon_free_blank',kind='blank',template_id='')
STANDARD_EXAMPLE=dict(SAMPLE_EXAMPLE,sample_id='external_carbon_reference',kind='standard',template_id='crm_altus_khp_10mg',template_channel='TC',assumed_ic_kg_m3=0.)
IC_STANDARD_EXAMPLE=dict(STANDARD_EXAMPLE,template_id='crm_altus_bicarbonate_1mg',template_channel='IC',assumed_ic_kg_m3=None,assumed_npoc_kg_m3=0.)
FURNACE=obj({'temperature_k':number('K',278.15,953.15)});FURNACE_EXAMPLE={'temperature_k':953.15}
METHOD=obj({'mode':enum(['TC','IC','TOC','NPOC']),'injection_volume_m3':number('m^3',1e-8,2e-6),'dilution_factor':number('1',1,50),'repeats':integer(1,10),'acid_volume_m3':number('m^3',1e-10,2e-7),'acidification_s':number('s',.001,3600),'sparging_s':number('s',.001,3600),'combustion_s':number('s',.001,3600),'minimum_peak_s':number('s',.001,1000),'maximum_peak_s':number('s',.01,3600),'baseline_threshold':number('1',1e-8,.1),'baseline_samples':integer(1,100),'temperature_tolerance_k':number('K',.01,10)})
METHOD_EXAMPLE=dict(mode='TOC',injection_volume_m3=5e-7,dilution_factor=1.,repeats=1,acid_volume_m3=1e-9,acidification_s=.02,sparging_s=.2,combustion_s=.15,minimum_peak_s=.03,maximum_peak_s=1.,baseline_threshold=1e-5,baseline_samples=2,temperature_tolerance_k=2.)
CAL_INPUT=obj({'curve_id':TEXT,'channel':enum(CHANNELS),'observations':vector(obj({'reference_id':TEXT,'reference_channel':enum(QUANTITIES),'injection_volume_m3':number('m^3',1e-8,2e-6),'dilution_factor':number('1',1,50),'peak_area_s':number('s')}),3,100),'accept_source_channel_transfer':BOOL,'accept_same_response_conditions':BOOL})
CAL_OUTPUT=obj({'curve_id':TEXT,'channel':TEXT,'slope_s_kg':number('s/kg',0),'intercept_s':number('s'),'r_squared':number('1'),'carbon_mass_range_kg':arr(number('kg',0)),'source_channel_transfer':BOOL,'evidence':TEXT})
CALIBRATION_EXAMPLE=dict(curve_id='illustrative_tc',channel='TC',observations=[dict(reference_id='crm_altus_khp_10mg',reference_channel='TC',injection_volume_m3=1e-7,dilution_factor=d,peak_area_s=.01*1e-7/d*1e8) for d in [10.,2.,1.]],accept_source_channel_transfer=False,accept_same_response_conditions=True)
POINT=obj({'time_s':number('s',0),'sample_window_s':arr(number('s',0)),'signal':number('1'),'corrected_signal':number('1')})
ASSAY=obj({'channel':enum(CHANNELS),'repeat_index':integer(0,9),'status':TEXT,'injection_volume_m3':number('m^3',0),'original_sample_volume_m3':number('m^3',0),'peak_area_s':number('s'),'concentration_kg_m3':nullable(number('kg/m^3')),'quality':TEXT,'peak':arr(POINT),'settings_json':TEXT,'calibration_json':TEXT})
RESULT=obj({'template_source':TEXT,'phase':PHASE,'stage':TEXT,'complete':BOOL,'record_id':nullable(TEXT),'mode':TEXT,'kind':TEXT,'model_assumptions':arr(TEXT),'concentrations_kg_m3':CONCENTRATIONS,'sd_kg_m3':CONCENTRATIONS,'cv':qobj(nullable(number('1'))),'qc':arr(TEXT),'assays':arr(ASSAY),'active_assay':nullable(ASSAY),'evidence':TEXT})
MEASURE=obj({'phase':PHASE,'stage':TEXT,'elapsed_s':number('s',0),'progress':number('1',0,1),'ndir_signal':nullable(number('1')),'concentrations_kg_m3':CONCENTRATIONS,'qc':arr(TEXT),'warning':TEXT})
MASS_WAREHOUSE=obj({p:number('kg',0) for p in CARBON_PARTS});MASS_RESIDUAL=obj({p:number('kg') for p in CARBON_PARTS})
LEDGER=obj({'carbon_kg':obj({p:(MASS_RESIDUAL if p=='residual' else MASS_WAREHOUSE) for p in ['registered','sample','syringe','reactor','co2_buffer','co2_tail','detected_exhaust','purged','liquid_waste','removed','residual']}),'volume_m3':obj({p:number('m^3') for p in ['registered_sample','initial_diluent','initial_acid','sample','diluent','acid','syringe','reactor','waste','removed','residual']}),'oxygen_initial_mol':number('mol',0),'oxygen_remaining_mol':number('mol',0),'oxygen_consumed_mol':number('mol',0),'oxygen_residual_mol':number('mol'),'acid_initial_mol':number('mol',0),'acid_available_mol':number('mol',0),'acid_injected_mol':number('mol',0),'acid_residual_mol':number('mol'),'net_furnace_heat_j':number('J'),'stored_furnace_heat_j':number('J'),'thermal_residual_j':number('J'),'active_template_id':nullable(TEXT),'carbon_partition_evidence':TEXT,'evidence':TEXT})
TEMPLATE=obj({'substance_id':TEXT,'name':TEXT,'template_json':TEXT});PAGE_INPUT=obj({'offset':integer(0,RESULT_CAPACITY),'limit':integer(1,100)})
CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None):CAPS[k]=[i,o,c,z,kind,vis,{} if ex is None else ex,[]]
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body','combustion_furnace','ndir_detector'],'可信宿主一次声明流量/热学/转换与NDIR代理参数；LOD、600VA与重复性不当噪声或炉功率','configuration','evaluator',PLANT_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_inlet'],'外部水样及真实来源浓度；IC/不可吹脱OC/可吹脱OC缺项需显式假设，源TOC不直接冒充NPOC','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.unload',obj({'recipient_id':TEXT}),obj({'unloaded':BOOL,'volume_m3':number('m^3',0)}),['sample_inlet'],'取回瓶内剩余水样，已消耗体积及反应残余不退回','device_command','evaluator',{'recipient_id':'external_bottle'})
cap('fluidics.drain',obj({}),obj({'drained_volume_m3':number('m^3',0)}),['syringe_plunger','ic_reactor'],'停机后可信宿主将取消后残余液体转入废液仓；外部恢复，不伪造机器人','device_command','evaluator')
cap('cover.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['front_door'],'维护前门开门中止；外部接样不依赖前门','device_command','evaluator',{'opened':False})
cap('top_cover.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['top_cover'],'冷态断电顶盖维护；不是自动取样盖','device_command','evaluator',{'opened':False})
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','button_power','combustion_furnace'],'逻辑供电和停止互锁，有限升降温在step',ex={'enabled':True})
cap('power.toggle',obj({}),obj({'enabled':BOOL}),['power_inlet','button_power','combustion_furnace'],'真实电源键空参数切换，同power.set原子互锁')
cap('furnace.configure',FURNACE,obj({'configured':BOOL}),['combustion_furnace'],'680°C催化燃烧炉目标，改变目标不瞬移炉温','configuration','agent',FURNACE_EXAMPLE)
cap('method.configure',METHOD,obj({'configured':BOOL}),['selector_valve','syringe_plunger','ic_pump_rotor'],'独立TC/IC/NPOC或两份未燃烧原样分别TC与IC求TOC；1倍不稀释，2–50倍自动稀释','configuration','agent',METHOD_EXAMPLE)
cap('measurement.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['button_start','selector_valve','syringe_plunger','ic_reactor','ic_pump_rotor','combustion_furnace','ndir_detector'],'有限选阀/取液稀释/注射、酸化吹扫或680°C转换及NDIR观测；每重复独立消耗原样')
cap('measurement.stop',obj({}),obj({'phase':PHASE}),['button_stop','syringe_plunger','ic_pump_rotor'],'中止保留未完成液体和碳仓及已有峰，不补造最后结果')
cap('process.measure',obj({}),MEASURE,['display_panel'],'只读最新NDIR、进度和观察标线结果','query')
cap('result.read',obj({}),RESULT,['display_panel'],'各重复完整峰、原方法/标线、均值SD/CV与宿主QC；TOC负差保留不截零','query')
cap('peak.read',obj({}),obj({'assays':arr(ASSAY),'active_assay':nullable(ASSAY),'evidence':TEXT}),['display_panel'],'实际积分窗NDIR代理峰，不是来源实测时间序列','query')
cap('result.export',obj({'format':enum(['json','csv'])}),obj({'format':TEXT,'content':TEXT}),['display_panel'],'纯返回完整JSON/CSV数据，不写文件或假装USB硬件','query','agent',{'format':'json'})
cap('blank.read',obj({}),obj({'valid_channels':arr(TEXT),'blank_json':TEXT}),['display_panel'],'当前响应设置下完整空白的观察基线；未完成空白不启用','query')
cap('calibration.fit',CAL_INPUT,CAL_OUTPUT,['display_panel'],'真实来源参考浓度与调用者实际标准峰面积拟合，最多60条；源channel跨用必须明确接受且标记','configuration','agent',CALIBRATION_EXAMPLE)
cap('calibration.select',obj({'channel':enum(CHANNELS),'curve_id':TEXT}),obj({'selected':BOOL}),['display_panel'],'选用现有对应channel标线；空ID取消选择','configuration','agent',{'channel':'TC','curve_id':'illustrative_tc'})
cap('calibration.read',obj({'curve_id':TEXT}),obj({'curve_json':TEXT}),['display_panel'],'读取指定已存完整标线','query','agent',{'curve_id':'illustrative_tc'})
cap('calibration.list',obj({}),obj({'capacity':integer(60,60),'count':integer(0,60),'curve_ids':arr(TEXT)}),['display_panel'],'完整列出实例内最多60条标线，不覆盖既有条目','query')
cap('records.list',PAGE_INPUT,obj({'capacity':integer(2000,2000),'count':integer(0,2000),'record_ids':arr(TEXT)}),['display_panel'],'分页列出最多2000份完整样品记录，满容量拒绝新测量','query','agent',{'offset':0,'limit':20})
cap('records.read',obj({'record_id':TEXT}),RESULT,['display_panel'],'按ID读取完整结果与所有重复峰，保持原方法和质量标记','query','agent',{'record_id':'tocl_01:result:1'})
cap('records.remove',obj({'record_id':TEXT}),obj({'removed':BOOL}),['display_panel'],'显式删除实例结果库条目释放容量；原主体异步终态历史不被改写','configuration','agent',{'record_id':'tocl_01:result:1'})
cap('template.read',obj({'substance_id':TEXT}),TEMPLATE,['display_panel'],'真实来源各carbon quantity与未知项原样查询，水样不冒充不同分子','query','agent',{'substance_id':SAMPLE_EXAMPLE['template_id']})
cap('product.read',obj({}),LEDGER,['combustion_furnace','ic_reactor'],'私有三碳仓、体积、氧/酸试剂及集总净热量守恒，非完整有机氧化反应式','query','evaluator')
for key,label in [('poc.measure','POC分离检测选件'),('tn.measure','总氮选件'),('autosampler.run','外接自动进样器选件')]:cap(key,obj({}),obj({}),['body'],label+'未实现，不返回假成功')
UNSUPPORTED={'poc.measure','tn.measure','autosampler.run'}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='display_panel',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','显式宿主模型已配置','setpoint',c='body');slot('sample.loaded',BOOL,'1','外部水样瓶已连接','scene',c='sample_inlet');slot('power.enabled',BOOL,'1','逻辑供电','setpoint',c='power_inlet',w=('power.set','enabled'))
slot('cover.opened',BOOL,'1','维护前门开启','scene',c='front_door');slot('cover.angle_rad',number('rad',0,1.60),'rad','维护前门工程转角','scene',c='front_door');slot('top_cover.opened',BOOL,'1','冷维护顶盖开启','scene',c='top_cover');slot('top_cover.lift_m',number('m',0,.15),'m','可拆顶盖冷态向上托持工程行程，非铰链','scene',c='top_cover')
slot('syringe.position_m',number('m',0,SYRINGE_STROKE_M),'m','注射器随实际暂存液体体积的有限工程行程','scene',c='syringe_plunger');slot('valve.angle_rad',number('rad',0,7*math.pi/4),'rad','8-port工程选位转角，不称厂家孔号','scene',c='selector_valve');slot('ic_pump.angle_rad',number('rad',0,2*math.pi),'rad','IC处理泵有限累计转角模2π','scene',c='ic_pump_rotor')
slot('furnace.target_k',number('K',278.15,953.15),'K','催化炉目标最高953.15K','setpoint',c='combustion_furnace');slot('furnace.actual_k',number('K',278.15,953.15),'K','炉温集总估计而非工厂传感器','derived',c='combustion_furnace');slot('furnace.ready',BOOL,'1','已在方法容差内且达680°C目标','derived',c='combustion_furnace')
slot('measurement.phase',PHASE,'1','本次采集生命周期');slot('measurement.stage',TEXT,'1','有限流路和检测阶段');slot('measurement.elapsed_s',number('s',0),'s','本次实际运行耗时');slot('measurement.progress',number('1',0,1),'1','已完成独立aliquot占本方法总数');slot('measurement.channel',TEXT,'1','当前TC/IC/NPOC支路');slot('measurement.completed_aliquots',integer(0,20),'1','已完成独立进样数')
slot('detector.ndir_signal',nullable(number('1')),'1','最近完整NDIR相对积分观测');slot('detector.last_sample_time_s',nullable(number('s',0)),'s','最近NDIR观测世界时刻')
for q in QUANTITIES:slot('result.'+q.lower()+'_kg_m3',nullable(number('kg/m^3')),'kg/m^3',q+'观察标线浓度；负TOC差不截零，缺标定为null')
slot('method.mode',TEXT,'1','TC/IC/TOC/NPOC模式','setpoint');slot('blank.valid',BOOL,'1','当前方法所需channel均有完整空白');slot('calibration.ready',BOOL,'1','所需channel均选中匹配响应设置标线');slot('storage.result_count',integer(0,2000),'1','完整结果库条目数');slot('storage.curve_count',integer(0,60),'1','已保存标线数');slot('safety.warning',TEXT,'1','公开逻辑异常，不含水样身份');slot('truth.ledger',LEDGER,'1','私有三碳仓、液体与试剂热学账本','process_truth','evaluator','ic_reactor')


def _library():
 p=ROOT/TEMPLATE_REL
 return json.loads(p.read_text()) if p.is_file() else {'compounds':{}}
def _nulls():return {q:None for q in QUANTITIES}
def _mass(a):return {k:float(v) for k,v in zip(CARBON_PARTS,a)}

class ShimadzuToclRuntime:
 def __init__(self,instance_id='tocl_01',library=None):
  self.instance_id=instance_id;self.library=_library() if library is None else copy.deepcopy(library);self.templates=self.library['compounds'];self.time_s=0.;self.revision=0;self.plant=None;self.method=None;self.sample=None;self.powered=False;self.opened=False;self.top_opened=False;self.temperature=298.15;self.target=298.15;self.initial_temperature=298.15;self.heat=0.;self.valve=0.;self.pump=0.
  for name in ['registered','syringe','reactor','co2','tail','exhaust','purged','waste','removed']:setattr(self,'_'+name,np.zeros(3))
  self._registered_v=0.;self._removed_v=0.;self._syringe_v=0.;self._reactor_v=0.;self._waste_v=0.;self._diluent_v=0.;self._acid_v=0.;self._oxygen_initial=0.;self._oxygen_remaining=0.;self._oxygen_consumed=0.;self._acid_chamber=0.
  self.phase='idle';self.stage='idle';self.elapsed_s=0.;self.warning='';self.operation_id='';self._op_count=0;self._record_id=None;self._records={};self._curves={};self._selected={c:'' for c in CHANNELS};self._blanks={};self._run_blanks={};self._run_method=None;self._run_key=None;self._run_curves={};self._run_kind='';self._run_assumptions=[];self._plan=[];self._index=0;self._active=None;self.assays=[];self._summary=_nulls();self._sd=_nulls();self._cv=_nulls();self._qc=[];self._stage_elapsed=0.;self._target_angle=0.;self._stage_remaining_v=0.;self._channel='';self._next_detector=None;self._detector_start=None;self._pending_area=0.;self._baseline=0.;self._prebaseline=[];self._baseline_count=0;self._last_point=None;self._signal=None
  self._rng=np.random.default_rng(0);self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._operation_records={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return ShimadzuToclSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in ['library','templates','_rng']})|{'_rng_state':copy.deepcopy(self._rng.bit_generator.state)}
 def _checkpoint(self):
  # Stored records, curves and terminal responses are append-only immutable values; copying their index avoids quadratic peak-array copies.
  frozen=['library','templates','_records','_curves','_operation_records','_requests','_rng'];q=copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in frozen})
  for k in frozen:q[k]=dict(getattr(self,k)) if k in ['_records','_curves','_operation_records','_requests'] else getattr(self,k)
  q={k:q[k] for k in self.__dict__}
  return q,copy.deepcopy(self._rng.bit_generator.state)
 def _channels(self):return ['TC','IC'] if self.method and self.method['mode']=='TOC' else [self.method['mode']] if self.method else []
 def _key(self):
  # Fits use absolute carbon mass, so injection/dilution/repeat/mode do not redefine detector gain; treatment and peak integration conditions do.
  return json.dumps({k:v for k,v in (self.method or {}).items() if k not in ['mode','injection_volume_m3','dilution_factor','repeats']},sort_keys=True)
 def _blank_valid(self):return bool(self.method and all(c in self._blanks and self._blanks[c]['key']==self._key() for c in self._channels()))
 def _calibration_ready(self):return bool(self.method and all(self._selected[c] in self._curves and self._curves[self._selected[c]]['key']==self._key() for c in self._channels()))
 def _ready(self):return bool(self.plant and self.method and self.powered and not self.opened and not self.top_opened and self.target==953.15 and abs(self.temperature-self.target)<=self.method['temperature_tolerance_k'])
 def _ledger(self):
  sample_c=self.sample['concentrations']*self.sample['volume'] if self.sample else np.zeros(3);sample_v=self.sample['volume'] if self.sample else 0.;residual=self._registered-sample_c-self._syringe-self._reactor-self._co2-self._tail-self._exhaust-self._purged-self._waste-self._removed;di=self.plant['diluent_volume_m3'] if self.plant else 0.;ai=self.plant['acid_volume_m3'] if self.plant else 0.;ac=self.plant['acid_concentration_mol_m3'] if self.plant else 0.;heat=(self.temperature-self.initial_temperature)*(self.plant['furnace_capacity_j_k'] if self.plant else 0.)
  return dict(carbon_kg=dict(registered=_mass(self._registered),sample=_mass(sample_c),syringe=_mass(self._syringe),reactor=_mass(self._reactor),co2_buffer=_mass(self._co2),co2_tail=_mass(self._tail),detected_exhaust=_mass(self._exhaust),purged=_mass(self._purged),liquid_waste=_mass(self._waste),removed=_mass(self._removed),residual=_mass(residual)),volume_m3=dict(registered_sample=self._registered_v,initial_diluent=di,initial_acid=ai,sample=sample_v,diluent=self._diluent_v,acid=self._acid_v,syringe=self._syringe_v,reactor=self._reactor_v,waste=self._waste_v,removed=self._removed_v,residual=self._registered_v+di+ai-sample_v-self._diluent_v-self._acid_v-self._syringe_v-self._reactor_v-self._waste_v-self._removed_v),oxygen_initial_mol=self._oxygen_initial,oxygen_remaining_mol=self._oxygen_remaining,oxygen_consumed_mol=self._oxygen_consumed,oxygen_residual_mol=self._oxygen_initial-self._oxygen_remaining-self._oxygen_consumed,acid_initial_mol=ai*ac,acid_available_mol=self._acid_v*ac,acid_injected_mol=(ai-self._acid_v)*ac,acid_residual_mol=ai*ac-self._acid_v*ac-(ai-self._acid_v)*ac,net_furnace_heat_j=self.heat,stored_furnace_heat_j=heat,thermal_residual_j=self.heat-heat,active_template_id=self.sample['input']['template_id'] if self.sample else None,carbon_partition_evidence=self.sample['partition_evidence'] if self.sample else '',evidence='three carbon-origin warehouses and aqueous volume proxy; pure diluent/acid carbon-free assumptions; oxygen coefficient and conservative carbonate proton demand are host model choices; acid ledger is delivered reagent amount, not full speciation/pH; net furnace heat is not total electrical energy or water-vapour enthalpy')
 def _result(self):return dict(template_source=TEMPLATE_REL,phase=self.phase,stage=self.stage,complete=self.phase=='succeeded',record_id=self._record_id,mode=self._run_method['mode'] if self._run_method else '',kind=self._run_kind,model_assumptions=list(self._run_assumptions),concentrations_kg_m3=copy.deepcopy(self._summary),sd_kg_m3=copy.deepcopy(self._sd),cv=copy.deepcopy(self._cv),qc=list(self._qc),assays=copy.deepcopy(self.assays),active_assay=copy.deepcopy(self._active),evidence='source carbon quantity retained in template; observed synthetic NDIR peaks and caller-fitted carbon-mass calibration; host QC/mean/sample-SD/CV are not CPH software or manufacturer repeatability validation')
 def _measure(self):return dict(phase=self.phase,stage=self.stage,elapsed_s=self.elapsed_s,progress=len(self.assays)/len(self._plan) if self._plan else 0.,ndir_signal=self._signal,concentrations_kg_m3=copy.deepcopy(self._summary),qc=list(self._qc),warning=self.warning)
 def read(self,visibility='agent'):
  if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
  v={'plant.configured':self.plant is not None,'sample.loaded':self.sample is not None,'power.enabled':self.powered,'cover.opened':self.opened,'cover.angle_rad':1.60 if self.opened else 0.,'top_cover.opened':self.top_opened,'top_cover.lift_m':.15 if self.top_opened else 0.,'syringe.position_m':min(SYRINGE_STROKE_M,max(0.,self._syringe_v/SYRINGE_MAX_M3*SYRINGE_STROKE_M)),'valve.angle_rad':self.valve,'ic_pump.angle_rad':self.pump,'furnace.target_k':self.target,'furnace.actual_k':self.temperature,'furnace.ready':self._ready(),'measurement.phase':self.phase,'measurement.stage':self.stage,'measurement.elapsed_s':self.elapsed_s,'measurement.progress':len(self.assays)/len(self._plan) if self._plan else 0.,'measurement.channel':self._channel,'measurement.completed_aliquots':len(self.assays),'detector.ndir_signal':self._signal,'detector.last_sample_time_s':self._last_point['time_s'] if self._last_point else None,'method.mode':self.method['mode'] if self.method else '', 'blank.valid':self._blank_valid(),'calibration.ready':self._calibration_ready(),'storage.result_count':len(self._records),'storage.curve_count':len(self._curves),'safety.warning':self.warning,'truth.ledger':self._ledger()}
  v.update({'result.'+q.lower()+'_kg_m3':self._summary[q] for q in QUANTITIES})
  if visibility=='agent':v.pop('truth.ledger')
  return copy.deepcopy(v)
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[k][0],a)
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  query=CAPS[k][4]=='query';checkpoint=None if query else self._checkpoint()
  try:
   result=self._invoke(k,copy.deepcopy(a));validate(CAPS[k][1],result)
   if not query:self.revision+=1;self._archive_operation()
   return copy.deepcopy(result)
  except Exception:
   if checkpoint:self.__dict__.clear();self.__dict__.update(checkpoint[0]);self._rng.bit_generator.state=checkpoint[1]
   raise
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _reference(self,key,channel=None,allow_query=False):
  if key not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
  q=self.templates[key];values=q.get('carbon_concentrations_kg_m3',{})
  if not allow_query and not any(values.get(c) is not None for c in QUANTITIES):raise ActionRejected('INVALID_REFERENCE')
  for v in values.values():
   if v is not None:validate(number('kg/m^3',0),v)
  if channel and values.get(channel) is None:raise ActionRejected('UNREPORTED_SOURCE_CHANNEL')
  return q
 def _sample(self,a):
  if not a['sample_id']:raise ActionRejected('INVALID_ARGUMENT')
  if a['kind']=='blank':
   if a['template_id'] or any(a[k] not in (None,0.) for k in ['assumed_ic_kg_m3','assumed_poc_kg_m3','assumed_npoc_kg_m3']):raise ActionRejected('BLANK_COMPOSITION')
   return dict(input=a,volume=a['volume_m3'],concentrations=np.zeros(3),partition_evidence='explicit_host_carbon_free_blank')
  q=self._reference(a['template_id'],a['template_channel']);c=q['carbon_concentrations_kg_m3'];value=c[a['template_channel']]
  if not a['accept_unknown_conditions']:raise ActionRejected('UNKNOWN_CONDITIONS_ACK_REQUIRED')
  selected=a['template_channel'];ic=c.get('IC') if selected!='IC' else value;npoc=c.get('NPOC') if selected!='NPOC' else value;poc=None;assumptions=[]
  if ic is None:
   ic=a['assumed_ic_kg_m3']
   if ic is None:raise ActionRejected('CARBON_PARTITION_REQUIRED')
   assumptions.append('IC')
  elif a['assumed_ic_kg_m3'] is not None and abs(a['assumed_ic_kg_m3']-ic)>1e-15:raise ActionRejected('REPORTED_VALUE_OVERRIDE')
  if selected=='IC':
   if npoc is None:
    npoc=a['assumed_npoc_kg_m3'];assumptions.append('NPOC')
   poc=a['assumed_poc_kg_m3'];assumptions.append('POC')
  elif selected=='NPOC':poc=a['assumed_poc_kg_m3'];assumptions.append('POC')
  else:
   oc=value-ic if selected=='TC' else value
   if oc<0:raise ActionRejected('INCONSISTENT_CARBON_REFERENCE')
   if npoc is not None:
    poc=oc-npoc
    if poc<0:raise ActionRejected('INCONSISTENT_CARBON_REFERENCE')
    if a['assumed_poc_kg_m3'] is not None and abs(a['assumed_poc_kg_m3']-poc)>1e-15:raise ActionRejected('REPORTED_VALUE_OVERRIDE')
   else:poc=a['assumed_poc_kg_m3'];npoc=oc-poc if poc is not None else None;assumptions.append('POC')
  if any(v is None for v in [ic,npoc,poc]):raise ActionRejected('CARBON_PARTITION_REQUIRED')
  if min(ic,npoc,poc)<0 or not all(math.isfinite(v) for v in [ic,npoc,poc]):raise ActionRejected('CARBON_PARTITION_RANGE')
  if a['assumed_npoc_kg_m3'] is not None and abs(a['assumed_npoc_kg_m3']-npoc)>1e-15:raise ActionRejected('REPORTED_VALUE_OVERRIDE')
  if assumptions and not a['accept_partition_assumptions']:raise ActionRejected('PARTITION_ACK_REQUIRED')
  return dict(input=a,volume=a['volume_m3'],concentrations=np.array([ic,npoc,poc]),partition_evidence=json.dumps(dict(source_channel=selected,source_evidence=q.get('evidence','unknown'),assumed_channels=assumptions,assumption='explicit host partition, not source IC/POC measurement; derived TC-IC or TOC-POC only when selected source quantity supports it'),sort_keys=True))
 def _invoke(self,k,a):
  if k=='plant.configure':
   if self.plant is not None or self.sample is not None:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_lumped_model']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   self.plant=a;self.temperature=self.target=self.initial_temperature=a['ambient_k'];self._diluent_v=a['diluent_volume_m3'];self._acid_v=a['acid_volume_m3'];self._oxygen_initial=self._oxygen_remaining=a['oxygen_inventory_mol'];self._rng=np.random.default_rng(a['seed']);return {'configured':True}
  if k=='sample.load':
   self._idle()
   if not self.plant:raise ActionRejected('PLANT_REQUIRED')
   if self.sample is not None:raise ActionRejected('UNLOAD_REQUIRED')
   self.sample=self._sample(a);self._registered+=self.sample['concentrations']*a['volume_m3'];self._registered_v+=a['volume_m3'];return {'loaded':True}
  if k=='sample.unload':
   self._idle()
   if self.sample is None:raise ActionRejected('SAMPLE_REQUIRED')
   if not a['recipient_id']:raise ActionRejected('RECIPIENT_REQUIRED')
   v=self.sample['volume'];self._removed+=self.sample['concentrations']*v;self._removed_v+=v;self.sample=None;return dict(unloaded=True,volume_m3=v)
  if k=='fluidics.drain':
   self._idle()
   if self.powered:raise ActionRejected('POWER_OFF_REQUIRED')
   v=self._syringe_v+self._reactor_v;self._waste_v+=v;self._syringe_v=self._reactor_v=0.;self._waste+=self._syringe+self._reactor;self._syringe[:]=0.;self._reactor[:]=0.;self._tail+=self._co2;self._co2[:]=0.;self._acid_chamber=0.;return dict(drained_volume_m3=v)
  if k in ['cover.set','top_cover.set']:
   if k=='top_cover.set' and (self.powered or self.temperature>323.15):raise ActionRejected('COLD_MAINTENANCE_REQUIRED')
   if a['opened'] and self.phase=='running':self._stop('COVER_OPEN')
   if k=='cover.set':self.opened=a['opened']
   else:self.top_opened=a['opened']
   return {'opened':a['opened']}
  if k=='power.toggle':return self._invoke('power.set',{'enabled':not self.powered})
  if k=='power.set':
   if a['enabled'] and (not self.plant or self.opened or self.top_opened):raise ActionRejected('POWER_INTERLOCK')
   if not a['enabled'] and self.phase=='running':self._stop('POWER_OFF')
   self.powered=a['enabled'];return {'enabled':self.powered}
  if k=='furnace.configure':
   self._idle()
   if not self.plant:raise ActionRejected('PLANT_REQUIRED')
   self.target=a['temperature_k'];return {'configured':True}
  if k=='method.configure':
   self._idle()
   if a['dilution_factor']!=1 and a['dilution_factor']<2:raise ActionRejected('DILUTION_RANGE')
   if a['maximum_peak_s']<a['minimum_peak_s'] or self.plant and a['maximum_peak_s']<self.plant['detector_period_s']*a['baseline_samples']:raise ActionRejected('PEAK_WINDOW_RANGE')
   self.method=a;return {'configured':True}
  if k=='measurement.start':return self._start()
  if k=='measurement.stop':
   if self.phase=='running':self._stop('CANCELLED')
   return {'phase':self.phase}
  if k=='process.measure':return self._measure()
  if k=='result.read':return self._result()
  if k=='peak.read':return dict(assays=copy.deepcopy(self.assays),active_assay=copy.deepcopy(self._active),evidence='synthetic relative NDIR observed integration windows, not manufacturer raw ADC/volts or source measured peak trace')
  if k=='result.export':
   result=self._result()
   if a['format']=='json':content=json.dumps(result,sort_keys=True,allow_nan=False)
   else:
    import csv,io
    f=io.StringIO();f.write('# metadata_json='+json.dumps({z:v for z,v in result.items() if z not in ['assays','active_assay']},sort_keys=True)+'\n');w=csv.writer(f);w.writerow(['channel','repeat_index','status','injection_volume_m3','original_sample_volume_m3','peak_area_s','concentration_kg_m3','quality','peak_json','settings_json','calibration_json'])
    for q in result['assays']+([result['active_assay']] if result['active_assay'] else []):w.writerow([q[k] if k!='peak_json' else json.dumps(q['peak'],sort_keys=True) for k in ['channel','repeat_index','status','injection_volume_m3','original_sample_volume_m3','peak_area_s','concentration_kg_m3','quality','peak_json','settings_json','calibration_json']])
    content=f.getvalue()
   return dict(format=a['format'],content=content)
  if k=='blank.read':return dict(valid_channels=[c for c,v in self._blanks.items() if v['key']==self._key()],blank_json=json.dumps(self._blanks,sort_keys=True))
  if k=='calibration.fit':return self._fit(a)
  if k=='calibration.select':
   self._idle();key=a['curve_id'];curve=self._curves.get(key)
   if key and (not curve or curve['channel']!=a['channel']):raise ActionRejected('CURVE_CHANNEL_MISMATCH')
   self._selected[a['channel']]=key;return {'selected':True}
  if k=='calibration.read':
   if a['curve_id'] not in self._curves:raise ActionRejected('UNKNOWN_CURVE')
   return {'curve_json':json.dumps(self._curves[a['curve_id']],sort_keys=True)}
  if k=='calibration.list':return dict(capacity=CURVE_CAPACITY,count=len(self._curves),curve_ids=list(self._curves))
  if k=='records.list':return dict(capacity=RESULT_CAPACITY,count=len(self._records),record_ids=list(self._records)[a['offset']:a['offset']+a['limit']])
  if k=='records.read':
   if a['record_id'] not in self._records:raise ActionRejected('UNKNOWN_RECORD')
   return copy.deepcopy(self._records[a['record_id']])
  if k=='records.remove':
   self._idle()
   if a['record_id'] not in self._records:raise ActionRejected('UNKNOWN_RECORD')
   del self._records[a['record_id']];return {'removed':True}
  if k=='template.read':
   q=self._reference(a['substance_id'],allow_query=True);return dict(substance_id=a['substance_id'],name=q.get('name',a['substance_id']),template_json=json.dumps(q,sort_keys=True,allow_nan=False))
  if k=='product.read':return self._ledger()
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _fit(self,a):
  self._idle()
  if not self.method or a['channel'] not in self._blanks or self._blanks[a['channel']]['key']!=self._key():raise ActionRejected('MATCHED_BLANK_REQUIRED')
  if not a['curve_id']:raise ActionRejected('INVALID_ARGUMENT')
  if a['curve_id'] in self._curves:raise ActionRejected('CURVE_ID_EXISTS')
  if len(self._curves)>=CURVE_CAPACITY:raise ActionRejected('CURVE_STORE_FULL')
  if not a['accept_same_response_conditions']:raise ActionRejected('CALIBRATION_ACK_REQUIRED')
  x=[];y=[];transfer=False;source_evidence=[]
  for o in a['observations']:
   if o['dilution_factor']!=1 and o['dilution_factor']<2:raise ActionRejected('DILUTION_RANGE')
   q=self._reference(o['reference_id'],o['reference_channel']);ev=q.get('evidence','')
   if not any(t in ev for t in ['measured','certified','reference','gravimetric']):raise ActionRejected('REAL_REFERENCE_REQUIRED')
   if o['reference_channel']!=a['channel']:
    if not a['accept_source_channel_transfer']:raise ActionRejected('SOURCE_CHANNEL_TRANSFER_ACK_REQUIRED')
    transfer=True
   mass=q['carbon_concentrations_kg_m3'][o['reference_channel']]*o['injection_volume_m3']/o['dilution_factor'];x.append(mass);y.append(o['peak_area_s']);source_evidence.append(dict(reference_id=o['reference_id'],reference_channel=o['reference_channel'],evidence=ev,certificate=q.get('certificate'),conditions=q.get('conditions'),calibration_eligible_channels=q.get('calibration_eligible_channels')))
  x=np.array(x,dtype=np.longdouble);y=np.array(y,dtype=np.longdouble);xm=x.mean();ym=y.mean();dx=x-xm;dy=y-ym;den=np.sum(dx*dx)
  if den<=0:raise ActionRejected('DEGENERATE_CALIBRATION')
  slope=np.sum(dx*dy)/den;intercept=ym-slope*xm;ss=np.sum(dy*dy);r2=1-np.sum((y-slope*x-intercept)**2)/ss if ss>0 else np.longdouble(0)
  if slope<=0 or not all(np.isfinite(v) and abs(v)<=np.finfo(float).max for v in [slope,intercept,r2]):raise ActionRejected('CALIBRATION_RANGE')
  result=dict(curve_id=a['curve_id'],channel=a['channel'],slope_s_kg=float(slope),intercept_s=float(intercept),r_squared=float(r2),carbon_mass_range_kg=[float(min(x)),float(max(x))],source_channel_transfer=transfer,evidence='caller observed standard peak areas with source measured/certified concentration; dilution is a declared host preparation; cross-quantity transfer is an assumption when flagged, not source NPOC/IC certification')
  self._curves[a['curve_id']]=dict(result,key=self._key(),observations=copy.deepcopy(a['observations']),source_evidence=source_evidence);return result
 def _start(self):
  self._idle()
  if not self.plant or not self.method or not self.sample:raise ActionRejected('PLANT_METHOD_SAMPLE_REQUIRED')
  if not self.powered or self.opened or self.top_opened or not self.plant['carrier_ready']:raise ActionRejected('GAS_POWER_INTERLOCK')
  if self.method['mode']!='IC' and not self._ready():raise ActionRejected('FURNACE_NOT_READY')
  if self._syringe_v>1e-18 or self._reactor_v>1e-18 or np.any(self._co2>1e-18):raise ActionRejected('DRAIN_CANCELLED_RESIDUE_REQUIRED')
  if len(self._records)>=RESULT_CAPACITY:raise ActionRejected('RESULT_STORE_FULL')
  n=len(self._channels())*self.method['repeats'];source_v=self.method['injection_volume_m3']/self.method['dilution_factor'];diluent=n*(self.method['injection_volume_m3']-source_v);acid=sum(c in ['IC','NPOC'] for c in self._channels())*self.method['repeats']*self.method['acid_volume_m3']
  if self.sample['volume']+1e-18<n*source_v:raise ActionRejected('SAMPLE_VOLUME_LIMIT')
  if self._diluent_v+1e-18<diluent or self._acid_v+1e-18<acid:raise ActionRejected('REAGENT_VOLUME_LIMIT')
  c=self.sample['concentrations']
  if self.method['mode'] in ['TC','TOC','NPOC'] and float(c.sum())>30:raise ActionRejected('TC_RANGE')
  if self.method['mode'] in ['IC','TOC'] and c[0]>35:raise ActionRejected('IC_RANGE')
  if self.sample['input']['kind']!='blank' and not self._blank_valid():raise ActionRejected('MATCHED_BLANK_REQUIRED')
  if n*math.ceil(self.method['maximum_peak_s']/self.plant['detector_period_s'])>1000000:raise ActionRejected('EVENT_RESOURCE_LIMIT')
  self._run_method=copy.deepcopy(self.method);self._run_key=self._key();self._run_kind=self.sample['input']['kind'];self._run_assumptions=[]
  if self._run_kind!='blank':
   partition=json.loads(self.sample['partition_evidence']);self._run_assumptions=['sample_'+c+'_partition_is_explicit_host_assumption' for c in partition['assumed_channels']]
   if partition['source_channel']=='TOC' and self.method['mode']=='NPOC':self._run_assumptions.append('source_TOC_conditioned_as_NPOC_is_not_measured_source_NPOC')
  self._run_curves={c:copy.deepcopy(self._curves.get(self._selected[c])) for c in CHANNELS};self._run_blanks={};self._plan=[(c,i) for i in range(self.method['repeats']) for c in self._channels()];self._index=0;self.assays=[];self._active=None;self._summary=_nulls();self._sd=_nulls();self._cv=_nulls();self._qc=[];self._last_point=None;self._signal=None;self._record_id=None;self.phase='running';self.elapsed_s=0.;self.warning='';self._op_count+=1;self.operation_id=f'{self.instance_id}:measurement:{self._op_count}';self._begin();return dict(phase=self.phase,operation_id=self.operation_id)
 def _begin(self):
  c,i=self._plan[self._index];self._channel=c;self._active=dict(channel=c,repeat_index=i,status='running',injection_volume_m3=self._run_method['injection_volume_m3'],original_sample_volume_m3=self._run_method['injection_volume_m3']/self._run_method['dilution_factor'],peak_area_s=0.,concentration_kg_m3=None,quality='not_completed',peak=[],settings_json=json.dumps(self._run_method,sort_keys=True),calibration_json=json.dumps(self._run_curves.get(c),sort_keys=True));self._prebaseline=[];self._enter('baseline')
 def _enter(self,stage):
  self.stage=stage;self._stage_elapsed=0.;self._next_detector=None;self._pending_area=0.
  angles={'select_sample':0.,'select_diluent':math.pi/4,'select_reactor':math.pi/2,'select_acid':math.pi,'select_combustion':3*math.pi/4,'select_detector':5*math.pi/4}
  if stage in angles:self._target_angle=angles[stage]
  elif stage=='aspirate_sample':self._stage_remaining_v=self._active['original_sample_volume_m3']
  elif stage=='aspirate_diluent':self._stage_remaining_v=self._active['injection_volume_m3']-self._active['original_sample_volume_m3']
  elif stage=='inject':self._stage_remaining_v=self._syringe_v
  elif stage in ['baseline','detect']:
   self._detector_start=self.time_s;self._next_detector=self.time_s+self.plant['detector_period_s'];self._baseline_count=0
 def _stop(self,why,failed=False):
  self.phase='failed' if failed else 'cancelled';self.stage='idle';self.warning=why;self._next_detector=None
  if self._active:self._active['status']=self.phase;self._active['quality']='incomplete_no_final_concentration'
 def _physical(self,dt):
  if self.plant:
   eq=self.target if self.powered and not self.opened and not self.top_opened else self.plant['ambient_k'];delta=(eq-self.temperature)*(-math.expm1(-self.plant['furnace_conductance_w_k']/self.plant['furnace_capacity_j_k']*dt));self.temperature+=delta;self.heat+=delta*self.plant['furnace_capacity_j_k']
   if self.powered and self.plant['carrier_ready']:
    tail=self._tail*(-math.expm1(-dt/self.plant['transport_tau_s']));self._tail-=tail;self._exhaust+=tail
    if self.phase=='running' and self.stage in ['baseline','detect']:self._pending_area+=float(tail.sum())*self.plant['ndir_gain_s_kg']
  if self.phase!='running':return
  self.elapsed_s+=dt;self._stage_elapsed+=dt;p=self.plant;m=self._run_method
  if self.stage.startswith('select_'):
   dist=self._target_angle-self.valve;self.valve+=math.copysign(min(abs(dist),p['selector_speed_rad_s']*dt),dist)
  elif self.stage in ['aspirate_sample','aspirate_diluent','inject']:
   v=min(self._stage_remaining_v,p['liquid_rate_m3_s']*dt);self._stage_remaining_v=max(0.,self._stage_remaining_v-v)
   if self.stage=='aspirate_sample':
    self.sample['volume']=max(0.,self.sample['volume']-v);self._syringe+=self.sample['concentrations']*v;self._syringe_v+=v
   elif self.stage=='aspirate_diluent':self._diluent_v=max(0.,self._diluent_v-v);self._syringe_v+=v
   else:
    carbon=self._syringe*(v/self._syringe_v if self._syringe_v>0 else 0.);self._syringe-=carbon;self._reactor+=carbon;self._syringe_v=max(0.,self._syringe_v-v);self._reactor_v+=v
  elif self.stage=='acidify':
   v=min(self._acid_v,m['acid_volume_m3']/m['acidification_s']*dt);self._acid_v-=v;self._reactor_v+=v;self._acid_chamber+=v*p['acid_concentration_mol_m3'];self.pump=(self.pump+p['ic_pump_speed_rad_s']*dt)%(2*math.pi)
  elif self.stage=='sparge':
   converted=min(self._reactor[0]*(-math.expm1(-p['ic_release_rate_s_inv']*dt)),self._acid_chamber*C_KG_MOL/2);self._reactor[0]-=converted;self._acid_chamber=max(0.,self._acid_chamber-2*converted/C_KG_MOL)
   if self._channel=='IC':self._co2[0]+=converted
   else:
    poc=self._reactor[2]*(-math.expm1(-p['poc_strip_rate_s_inv']*dt));self._reactor[2]-=poc;self._purged[0]+=converted;self._purged[2]+=poc
   self.pump=(self.pump+p['ic_pump_speed_rad_s']*dt)%(2*math.pi)
  elif self.stage=='combust':
   converted=self._reactor*(-math.expm1(-p['conversion_rate_s_inv']*dt));need=float(converted[1:].sum())/C_KG_MOL*p['oxygen_mol_per_oc_mol']
   if need>self._oxygen_remaining and need>0:converted[1:]*=self._oxygen_remaining/need;need=self._oxygen_remaining
   self._reactor-=converted;self._co2+=converted;self._oxygen_remaining=max(0.,self._oxygen_remaining-need);self._oxygen_consumed+=need
  elif self.stage=='detect':
   mass=self._co2*(-math.expm1(-dt/p['transport_tau_s']));self._co2-=mass;self._exhaust+=mass;self._pending_area+=float(mass.sum())*p['ndir_gain_s_kg']
 def _due(self):
  if self.stage.startswith('select_'):return abs(self._target_angle-self.valve)/self.plant['selector_speed_rad_s']
  if self.stage in ['aspirate_sample','aspirate_diluent','inject']:return self._stage_remaining_v/self.plant['liquid_rate_m3_s']
  if self.stage in ['baseline','detect']:
   delta=max(0.,self._next_detector-self.time_s)
   return min(delta,max(0.,self._run_method['maximum_peak_s']-self._stage_elapsed)) if self.stage=='detect' else delta
  return max(0.,{'acidify':self._run_method['acidification_s'],'sparge':self._run_method['sparging_s'],'combust':self._run_method['combustion_s']}[self.stage]-self._stage_elapsed)
 def _event(self):
  stage=self.stage
  if stage=='baseline':
   self._detector(baseline=True)
   if len(self._prebaseline)>=max(3,self._run_method['baseline_samples']):self._baseline=float(np.mean(self._prebaseline)) if self._run_kind=='blank' else self._blanks[self._channel]['baseline'];self._enter('select_sample')
  elif stage=='select_sample':self._enter('aspirate_sample')
  elif stage=='aspirate_sample':self._enter('select_diluent')
  elif stage=='select_diluent':self._enter('aspirate_diluent')
  elif stage=='aspirate_diluent':self._enter('select_reactor')
  elif stage=='select_reactor':self._enter('inject')
  elif stage=='inject':self._enter('select_acid' if self._channel in ['IC','NPOC'] else 'select_combustion')
  elif stage=='select_acid':self._enter('acidify')
  elif stage=='acidify':self._enter('sparge')
  elif stage=='sparge':self._enter('select_detector' if self._channel=='IC' else 'select_combustion')
  elif stage=='select_combustion':self._enter('combust')
  elif stage=='combust':self._enter('select_detector')
  elif stage=='select_detector':self._enter('detect')
  elif stage=='detect':
   if self._next_detector<=self.time_s+1e-11:self._detector()
   if self._stage_elapsed+1e-12>=self._run_method['minimum_peak_s'] and self._baseline_count>=self._run_method['baseline_samples']:self._finish_aliquot()
   elif self._stage_elapsed+1e-12>=self._run_method['maximum_peak_s']:self._stop('NDIR_BASELINE_TIMEOUT',True)
 def _detector(self,baseline=False):
  duration=self.time_s-self._detector_start
  if duration<=0:raise RuntimeError('nonpositive detector window')
  signal=self.plant['ndir_baseline']+self._pending_area/duration+float(self._rng.normal(0,self.plant['ndir_noise_sd']));corrected=signal-(self._baseline if not baseline else 0.);q=dict(time_s=self.time_s,sample_window_s=[self._detector_start,self.time_s],signal=signal,corrected_signal=corrected);self._last_point=q;self._signal=signal
  if baseline:self._prebaseline.append(signal)
  else:
   self._active['peak'].append(q);self._active['peak_area_s']+=corrected*duration;self._baseline_count=self._baseline_count+1 if abs(corrected)<=self._run_method['baseline_threshold'] else 0
  self._pending_area=0.;self._detector_start=self.time_s;self._next_detector=self.time_s+self.plant['detector_period_s']
 def _finish_aliquot(self):
  q=self._active;q['status']='succeeded';q['quality']='blank' if self._run_kind=='blank' else 'calibration_unavailable';curve=self._run_curves.get(self._channel)
  if self._run_kind=='blank':self._run_blanks.setdefault(self._channel,[]).append(self._baseline)
  elif curve and curve['key']==self._run_key:
   mass=(np.longdouble(q['peak_area_s'])-curve['intercept_s'])/curve['slope_s_kg'];lo,hi=curve['carbon_mass_range_kg'];tol=max(hi,1e-300)*1e-9;concentration=mass/q['original_sample_volume_m3']
   if mass<lo-tol or mass>hi+tol:q['quality']='outside_calibrated_mass_range'
   elif not np.isfinite(concentration) or abs(concentration)>np.finfo(float).max:q['quality']='numerical_range'
   else:q['concentration_kg_m3']=float(concentration);q['quality']='estimated_with_source_channel_transfer' if curve['source_channel_transfer'] else 'estimated_from_observed_calibration'
  self.assays.append(copy.deepcopy(q));self._tail+=self._co2;self._co2[:]=0.;self._waste+=self._reactor;self._reactor[:]=0.;self._waste_v+=self._reactor_v;self._reactor_v=0.;self._acid_chamber=0.;self._active=None;self._index+=1
  if self._index<len(self._plan):self._begin()
  else:
   self.phase='succeeded';self.stage='idle';self._channel='';self._next_detector=None
   if self._run_kind=='blank':
    for c,values in self._run_blanks.items():self._blanks[c]=dict(key=self._run_key,baseline=float(np.mean(values)),replicates=len(values))
   self._aggregate();self._record_id=f'{self.instance_id}:result:{self._op_count}';self._records[self._record_id]=self._result()
 def _aggregate(self):
  values={c:[q['concentration_kg_m3'] for q in self.assays if q['channel']==c] for c in CHANNELS}
  if self._run_method['mode']=='TOC':values['TOC']=[a-b if a is not None and b is not None else None for a,b in zip(values['TC'],values['IC'])]
  for channel,items in values.items():
   if not items or any(v is None for v in items):continue
   a=np.array(items,dtype=np.longdouble);mean=a.mean();sd=np.sqrt(np.sum((a-mean)**2)/(len(a)-1)) if len(a)>1 else None;self._summary[channel]=float(mean);self._sd[channel]=float(sd) if sd is not None else None;self._cv[channel]=float(sd/abs(mean)) if sd is not None and mean>0 else None
   if channel=='TOC' and mean<0:self._qc.append('negative_TOC_difference_preserved')
   if 0<=mean<4e-6:self._qc.append(channel+'_below_documented_LOD_reference_not_noise_model')
  for q in self.assays:
   if q['quality'] not in ['blank','estimated_from_observed_calibration','estimated_with_source_channel_transfer']:self._qc.append(q['channel']+'_'+q['quality'])
   if q['quality']=='estimated_with_source_channel_transfer':self._qc.append(q['channel']+'_source_channel_transfer_assumption')
  self._qc=list(dict.fromkeys(self._qc))
 def step(self,dt_s):
  validate(number('s',0,86400),dt_s)
  if dt_s==0:return self.read()
  target=self.time_s+dt_s;events=0
  while self.time_s<target-1e-12 or self.phase=='running' and self._due()<=1e-12:
   if self.phase!='running':self._physical(target-self.time_s);self.time_s=target;break
   remaining=max(0.,target-self.time_s);due=max(0.,self._due());delta=min(remaining,due)
   if delta>0:self._physical(delta);self.time_s+=delta
   if due<=delta+1e-12:self._event();events+=1
   else:break
   if events>2000000:raise RuntimeError('event resource limit')
   if self.time_s>=target-1e-12 and (self.phase!='running' or self._due()>1e-12):break
  if self.time_s<target:self._physical(target-self.time_s);self.time_s=target
  self.revision+=1;self._archive_operation();return self.read()
 def _operation_snapshot(self):return dict(protocol_version=PROTOCOL_VERSION,instance_id=self.instance_id,operation_id=self.operation_id,status=self.phase,state_revision=self.revision,clock={'domain':'simulation','time_s':self.time_s},result=self._result(),observations=self.session().read(instance_id=self.instance_id)['observations'],error=dict(code=self.warning,message=self.warning) if self.phase=='failed' else None)
 def _archive_operation(self):
  if self.operation_id and self.phase in ['succeeded','cancelled','failed'] and self.operation_id not in self._operation_records:self._operation_records[self.operation_id]=self._operation_snapshot()

class ShimadzuToclSession:
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
        m=export_shimadzu_tocl_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.1',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        def timing(key):
            if key in ['detector.ndir_signal','detector.last_sample_time_s'] and h._last_point:return h._last_point['time_s'],h._last_point['sample_window_s'] if key=='detector.ndir_signal' else None
            if key.startswith('result.'):
                channel=key.split('.')[1].split('_')[0].upper();points=[p for a in h.assays if a['channel']==channel or channel=='TOC' and a['channel'] in ['TC','IC'] for p in a['peak']]
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
'Selected standaloneCSH has native LCD/keypad, documented340x660x480mm and approximately35kg. Hinged front door and removable top cover with host-supported0.15m vertical maintenance lift, syringe60mm working stroke mapped to2mL and8-port angles are engineering geometry; total syringe nominal capacity/travel unverified. External autosampler/POC/TN options unsupported.',
'TC0–30kgC/m3 andIC0–35kgC/m3 source ranges;4microgram/L=4e-6kgC/m3 is conditional documented LOD, not Gaussian sigma or a nonzero lower range bound. CV1.5%max or±4microgram/L and dilution±2/5% are not simulated noise distributions.',
'Documented10–2000microlitre injections. Dilution1 means none;2–50 means explicitly modeled carbon-free dilution. Each aliquot withdraws injection_volume/dilution from the original bottle and consumes exact diluent volume; acid is separately added. Pure acid/diluent and homogeneous mixing are explicit assumptions.',
'680C=953.15K catalytic conversion; furnace finite first-order thermal parameters and accelerated examples are host assumptions.600VA is not furnace power. Net C*dT heat balance is not electrical/chemical/vapour energy closure.',
'Sample truth usesIC/nonpurgeableOC/purgeableOC origin warehouses. Missing IC or organic partition requires trusted explicit assumptions; TOC is not silently NPOC and DOC is not accepted as total-water TOC. Public source records preserve unknown channels and conditions.',
'TC converts an independent original aliquot; IC acidifies and sweeps a different unburned original aliquot with no organic oxidation. TOC is mean of paired observedTC-IC values. NPOC acidifies/sparges before remaining carbon is catalytically oxidized; purgeableOC and IC go to explicit purge accounts, not TOC measurements.',
'Acid release uses a conservative2mol proton/molIC-carbon carbonate-equivalent budget and host first-order coefficient; actual bicarbonate/carbonate/alkalinity/pH not reconstructed. Acid ledger tracks delivered reagent amount. Original source water speciation remains unknown.',
'Organic oxidation uses host carbon conversion coefficient and explicitly supplied molO2/molOC coefficient. Oxygen-limited residual carbon is retained. Unknown molecular oxygen/hydrogen stoichiometry is not inferred; no full CO2/water product mass or species-resolved mineralization claim.',
'Preparation vessel, acidification, IC transport and catalytic aliquot routes are lumped functional buffers, not fully resolved OEM8-port pneumatic plumbing. Partial acid removal or sparging can bias NPOC; slow stripping/reaction are not forcibly completed to match source values.',
'NDIR is relative baseline+gain_s_kg*released_carbon/window+host Gaussian event noise. Peak gas/tails release exponentially; observed baseline threshold controls completion. Earlier finite tails can contaminate later gates, and undetected remaining carbon is retained; this is synthetic observed response, not manufacturer raw trace/volts.',
'Blank baselines are measured from pre-injection events; they become available only after the entire declared blank completes. Matching response conditions exclude mode/injection/dilution/repeats because calibration uses absolute carbon mass, but retain treatment and peak-window settings. Matrix drift/nonlinearity/cross-sensitivity are unmodeled.',
'At least3 actual supplied standard areas fit a stable affine curve versus known source carbon concentration*injection/dilution. Cross-source-quantity transfer requires explicit acknowledgment and stays flagged. Historical certified assigned values are documentary references, not new individual measurements or currently valid physical CRM lots.',
'Only interpolation within calibrated absolute carbon-mass range yields concentration. No unknown-sample identity lookup in inference. Original settings and selected complete curves freeze with each aliquot; unavailable/out-of-range values remain null. Source-reference composition and observed synthetic response are distinct.',
'PairedTOC differences remain signed; negative differences yield explicitQC without clipping. All replicates remain present; sampleSD(ddof1) is null for one observation andCV is null for nonpositive means. No silent outlier deletion or CPH LabSolutions ER/ES/QC claim.',
'Instance memory stores2000 complete results including all replicate peaks and60 complete calibration curves; capacity-full and duplicate curve IDs reject before mutation. Results may be explicitly removed; no automatic eviction. This is process-local storage, not USB writing, reboot persistence or OEM data format.',
'Cancellation retains already withdrawn liquid, carbon and partial peaks; power-off trusted drain moves residual liquid to waste and residual gas to tail. It does not return consumed aliquots to the bottle. Hot self-sustaining chemistry, evaporation and real emergency shutdown are not modeled.',
'Pure queries do not advance clock or draw noise. Historical terminal polls preserve complete method/calibration/peaks under owner ACL, even after result-store removal or later methods. Access controls are process-local, not protection against host OS/file access.',
'Full quantitative range, certified repeatability/LOD, actual NDIR response, pH/speciation, variable matrix recovery, full parameter/noise convergence, real hardware/robot contact, USB/reboot storage and optional POC/TN/autosampler remain not_run.'
]

def export_shimadzu_tocl_contract_metadata():
 caps={};slots={};results=['result.'+q.lower()+'_kg_m3' for q in QUANTITIES];run=['measurement.phase','measurement.stage','measurement.elapsed_s','measurement.progress','measurement.channel','measurement.completed_aliquots'];detector=['detector.ndir_signal','detector.last_sample_time_s'];thermal=['furnace.actual_k','furnace.ready','truth.ledger'];motion=['syringe.position_m','valve.angle_rad','ic_pump.angle_rad']
 immediate={'plant.configure':['plant.configured','furnace.actual_k','furnace.target_k','truth.ledger'],'sample.load':['sample.loaded','truth.ledger'],'sample.unload':['sample.loaded','truth.ledger'],'fluidics.drain':['syringe.position_m','truth.ledger'],'cover.set':['cover.opened','cover.angle_rad','furnace.ready','measurement.phase','measurement.stage','safety.warning'],'top_cover.set':['top_cover.opened','top_cover.lift_m'],'power.set':['power.enabled','furnace.ready','measurement.phase','measurement.stage','safety.warning'],'furnace.configure':['furnace.target_k','furnace.ready'],'method.configure':['method.mode','furnace.ready','blank.valid','calibration.ready'],'measurement.start':[*run,*detector,*results,'safety.warning'],'measurement.stop':['measurement.phase','measurement.stage','safety.warning'],'calibration.fit':['storage.curve_count'],'calibration.select':['calibration.ready'],'records.remove':['storage.result_count']}
 immediate['power.toggle']=immediate['power.set'];onstep={k:thermal for k in ['power.set','power.toggle','cover.set','furnace.configure']};onstep['measurement.start']=[*run,*detector,*results,*thermal,*motion,'blank.valid','storage.result_count','safety.warning'];completion={'measurement.start':['measurement.phase','measurement.stage','measurement.progress','measurement.completed_aliquots','measurement.channel',*results,'blank.valid','storage.result_count','truth.ledger']}
 observes={'plant.configure':['plant.configured'],'sample.load':['sample.loaded'],'sample.unload':['sample.loaded'],'cover.set':['cover.opened'],'top_cover.set':['top_cover.opened'],'power.set':['power.enabled'],'power.toggle':['power.enabled'],'measurement.start':['measurement.phase'],'measurement.stop':['measurement.phase'],'process.measure':['measurement.phase','measurement.stage','measurement.elapsed_s','measurement.progress','detector.ndir_signal',*results,'safety.warning'],'result.read':['measurement.phase','measurement.stage',*results],'result.export':['measurement.phase','measurement.stage',*results],'peak.read':[],'calibration.list':['storage.curve_count'],'records.list':['storage.result_count'],'product.read':['truth.ledger']}
 for k,(s,u,o,v,c,w) in SLOTS.items():
  mean=k=='detector.ndir_signal' or k in results;slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'ShimadzuToclSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='interval_mean' if mean else 'instant',time_domain='simulation',window_description='NDIR为最近完整检测窗；结果由完整对应峰面积、原注入/稀释和标线换算后按重复平均，TOC窗包括两份独立TC/IC，不是同份燃烧后IC' if mean else None),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','INVALID_REFERENCE','UNREPORTED_SOURCE_CHANNEL','UNKNOWN_CONDITIONS_ACK_REQUIRED','CARBON_PARTITION_REQUIRED','REPORTED_VALUE_OVERRIDE','INCONSISTENT_CARBON_REFERENCE','CARBON_PARTITION_RANGE','PARTITION_ACK_REQUIRED','INITIALIZATION_ONLY','EVIDENCE_ACK_REQUIRED','PLANT_REQUIRED','UNLOAD_REQUIRED','SAMPLE_REQUIRED','RECIPIENT_REQUIRED','POWER_OFF_REQUIRED','COLD_MAINTENANCE_REQUIRED','POWER_INTERLOCK','DILUTION_RANGE','PEAK_WINDOW_RANGE','UNKNOWN_CURVE','UNKNOWN_RECORD','CURVE_CHANNEL_MISMATCH','MATCHED_BLANK_REQUIRED','CURVE_ID_EXISTS','CURVE_STORE_FULL','CALIBRATION_ACK_REQUIRED','REAL_REFERENCE_REQUIRED','SOURCE_CHANNEL_TRANSFER_ACK_REQUIRED','DEGENERATE_CALIBRATION','CALIBRATION_RANGE','PLANT_METHOD_SAMPLE_REQUIRED','GAS_POWER_INTERLOCK','FURNACE_NOT_READY','DRAIN_CANCELLED_RESIDUE_REQUIRED','RESULT_STORE_FULL','SAMPLE_VOLUME_LIMIT','REAGENT_VOLUME_LIMIT','TC_RANGE','IC_RANGE','EVENT_RESOURCE_LIMIT','BUSY','BLANK_COMPOSITION']
 for k,(inp,out,c,z,kind,vis,ex,_) in CAPS.items():
  query=kind=='query';unsupported=k in UNSUPPORTED;async_=k=='measurement.start';effects=[dict(state_id=state,timing=timing,description_zh={'immediate':'经权限/条件检查的目标、外部液体事件、存储或测量初始化，不推进时钟','on_step':'有限炉温/阀泵/液体与碳转换；NDIR完整采样窗才抽噪声','on_completion':'所有独立aliquot完成后保存全部峰、真实观察换算结果和原方法，完整空白才启用'}[timing]) for timing,mapping in [('immediate',immediate),('on_step',onstep),('on_completion',completion)] for state in mapping.get(k,[])]
  caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.shimadzu_tocl.ShimadzuToclSession.invoke',invocation=f'session.invoke(instance_id="tocl_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if query or unsupported else [dict(description_zh='宿主权限、源quantity/未知partition、液体/试剂容量、门盖与680°C、完整空白、标线及结果容量均原子检查',state_ids=['power.enabled','cover.opened','top_cover.opened','furnace.ready','blank.valid','measurement.phase'],enforced_by='runtime',check='Runtime._invoke / host-assigned principal Session')],state_effects=[] if query or unsupported else effects,observes=[] if unsupported else observes.get(k,[]),model_effects=dict(mode='none',description_zh='独立场景绑定另验；查询不动作',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='各独立原样aliquot完成有限液路/预处理/氧化/NDIR峰，随后保存完整结果；峰超时failed保留残料数据' if async_ else '原子返回，不推进时间',completion_state_ids=['measurement.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','aqueous_sample','carbon_free_diluent_acid','finite_oxygen','ndir','result_store_2000','curve_store_60','request_cache'],partial_failure_zh='取消/开门/断电及峰超时保留消费量和部分峰；未完成无浓度，原历史按主体保护；存储满在消费前拒绝'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in errors]+([dict(code='NDIR_BASELINE_TIMEOUT',meaning_zh='观察峰未按方法回基线；保留碳/体积仓与已有峰',side_effects='partial')] if async_ else []),example_arguments=copy.deepcopy(ex),verification=[],limitations=LIMITATIONS,source_ids=['manufacturer_primary','simulation_assumption'])
 mech=dict(component_ids=['sample_inlet','syringe_plunger','selector_valve','ic_reactor','ic_pump_rotor','combustion_furnace','ndir_detector'],state_ids=list(slots),meaning_zh='水样独立TC/IC/NPOC进样、酸化吹扫与催化碳转换、NDIR观察标定和完整容量存储',implementation='lumped',equations=['Original aliquot volume=final_injection_volume/dilution; diluent=final_injection_volume-original_volume; acid volume separately conserved.','TC=C_IC+C_NPOC+C_POC after conversion; IC from separately acidified unburned aliquot; pairedTOC=observedTC-observedIC, signed.','NPOC pretreatment removes finite IC and purgeableOC to purge warehouse; remaining carbon feeds catalytic detector, so incomplete stripping can bias NPOC.','First-order conversion/release limited by external oxygen/carbonate-equivalent proton budgets; unconverted carbon retained.','NDIR_mean=baseline+gain_s_kg*released_carbon/window+host_noise; peak_area=sum((NDIR-observed_blank)*window).','Fit area=slope*source_concentration*injection_volume/dilution+intercept; observed_concentration=(area-intercept)/slope/original_aliquot_volume.','Carbon registered=sum(sample,syringe,reactor,CO2buffer,tail,detected_exhaust,purged,liquid_waste,removed), separately for IC/NPOC/POC origins.','Sample+diluent+acid input volume=current liquids+waste+removed; net furnace heat=C*(T-T_initial), no full apparatus energy claim.','Complete result store capacity2000; calibration curve capacity60; no eviction, pure indexed reads and explicit result removal.'],parameters={},source_ids=['manufacturer_primary','simulation_assumption'],limitations=LIMITATIONS)
 spec=importlib.util.spec_from_file_location('_tocl_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'aqueous_carbon_analysis':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'；非原厂标定。'
 mech['parameters']={k.lower():v for k,v in mech['parameters'].items()};return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'aqueous_carbon_analysis':mech})

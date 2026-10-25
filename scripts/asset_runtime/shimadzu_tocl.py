"""TOC-L CSH: finite aqueous carbon aliquots, observed NDIR calibration and bounded records.

1.3.0 (sweep 2026-10-19): an agent may not stop or switch off another agent's analysis or maintenance item (STOP key, measurement.stop /
maintenance.stop, the front power key; the session sets _caller), the right-side main switch is a hard switch and never refused; evaluator
fault.inject / fault.clear for the main-unit codes of the TOC-L error list (Error!! ...: the analysis stops, the error screen stays until the
instrument is switched off and on, it shows again at power-up while the cause is present) and its messages (CF card, back-up battery, 8-port
valve retry, syringe zero point correction); the ASI-L / OCT-L / TNM-L codes need the absent options."""
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

# ---------------------------------------------------------------- 1.1.0: standalone CSH operating functions (panel_ui.json)
# Main power switch, standalone shutdown, catalyst type and syringe washes, the Maintenance items (bubble removal, TC catalyst regeneration,
# IC reagent delivery, residue removal, syringe zero point, TC blank check) with their counters, the Background Monitor / READY lamp and
# clearing of the measurement log.  The panel layer (panel_ui.json) drives these and the 1.0.x commands; CAPS keep their meaning.
ASSET_VERSION='1.3.1'
CATALYSTS=['regular','high'];MAINT_ITEMS=['bubble_removal','catalyst_regeneration','ic_regeneration','residue_removal','syringe_zero']
MAINT_S={'bubble_removal':60.,'catalyst_regeneration':300.,'ic_regeneration':10.,'residue_removal':120.,'syringe_zero':20.}
BUBBLE_DILUENT_M3=1e-6;BUBBLE_ACID_M3=1e-7;REGEN_ACID_M3=5e-7;RESIDUE_FLUSH_M3=2e-6;IC_REAGENT_STEP=.25
BLANK_CHECK_INJECTIONS=5;BLANK_CHECK_INJECTION_S=180.;SHUTDOWN_S=1800.;BASELINE_SETTLE_S=1800.;CARRIER_FLOW_M3_S=150e-6/60;DEHUMIDIFIER_K=274.15;FURNACE_K=953.15
COUNTERS={'valve_operations':('1','8PV Ope. Counts'),'syringe_strokes':('1','Syringe Stroke Number'),'combustion_injection_m3':('m^3','Combustion Tube Sample Injection Volume'),'acid_used_m3':('m^3','Acid Usage Volume'),'diluent_used_m3':('m^3','Dilution Water Usage Volume'),'co2_absorber_s':('s','CO2 Absorber'),'ic_pump_s':('s','IC Tubing Pump')}
KEY_CAPS={}
def kcap(k,i,o,c,z,kind='device_command',vis='agent',ex=None):KEY_CAPS[k]=[i,o,c,z,kind,vis,{} if ex is None else ex,[]]
kcap('power.set_mains',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['main_switch'],'右侧主电源开关：关断即断电并中止运行/维护/关机；开后仍需按前面电源键',ex={'enabled':True})
kcap('power.shutdown',obj({}),obj({'shutdown_s':number('s',0)}),['button_power','display_panel'],'单机关机确认（F6 Yes）：炉加热关闭冷却，30 min后自行断电（之后关右侧主开关）')
kcap('settings.configure',obj({'catalyst':enum(CATALYSTS),'syringe_washes':integer(0,9)}),obj({'configured':BOOL}),['display_panel'],'Conditions - Unit催化剂类型（High/Regular）与Meas. Cond.注射器清洗次数0–9（每次清洗以一份原样冲洗注射器后排废）','configuration','agent',{'catalyst':'regular','syringe_washes':0})
kcap('maintenance.run',obj({'item':enum(MAINT_ITEMS)}),obj({'item':TEXT,'duration_s':number('s',0)}),['display_panel','syringe_plunger','selector_valve','ic_reactor','combustion_furnace'],'Maintenance：气泡去除/TC催化剂再生/IC试剂补充/残液去除/注射器零点检测；有限时长，试剂用量在开始时计入废液',ex={'item':'bubble_removal'})
kcap('maintenance.tc_blank_check',obj({'sets':integer(1,10)}),obj({'injections':integer(5,50),'duration_s':number('s',0)}),['display_panel','syringe_plunger','selector_valve','combustion_furnace','ndir_detector'],'TC Blank Check：高灵敏度催化剂、炉温就绪时以稀释水做sets×5次TC进样（稀释水碳零假设，空白峰面积为0）',ex={'sets':10})
kcap('maintenance.stop',obj({}),obj({'phase':TEXT}),['button_stop'],'STOP中止当前维护项（已消耗试剂保留在废液）')
kcap('maintenance.reset_counter',obj({'counter':enum(list(COUNTERS))}),obj({'counter':TEXT}),['display_panel'],'维护计数器清零（更换部件后）',ex={'counter':'syringe_strokes'})
kcap('maintenance.set_counter_limits',obj({k:nullable(number(u,0)) for k,(u,_) in COUNTERS.items()}),obj({'configured':BOOL}),['display_panel'],'维护计数器上限（null为不报警）；超过则面板显示Counter Over-','configuration','agent',{k:None for k in COUNTERS})
kcap('records.clear',obj({}),obj({'removed':integer(0,RESULT_CAPACITY)}),['display_panel'],'Measurement Log的Clear All：删除实例结果库全部条目（运行历史不改写）','configuration')
COUNTER_SLOT=obj({k:obj({'value':number(u,0),'limit':nullable(number(u,0)),'over':BOOL}) for k,(u,_) in COUNTERS.items()})
slot('power.mains',BOOL,'1','右侧主电源开关','setpoint',c='main_switch');slot('power.shutdown_remaining_s',nullable(number('s',0)),'s','单机关机倒计时（无关机为空）',c='button_power')
slot('settings.catalyst',enum(CATALYSTS),'1','催化剂类型设置（TC Blank Check需High）','setpoint');slot('settings.syringe_washes',integer(0,9),'1','注射器清洗次数','setpoint',c='syringe_plunger')
slot('maintenance.phase',PHASE,'1','维护项生命周期');slot('maintenance.item',TEXT,'1','当前或最近维护项');slot('maintenance.remaining_s',number('s',0),'s','维护项剩余时间')
slot('maintenance.ic_reagent_level',number('1',0,1),'1','IC试剂液位（到排液管口为1；只由维护补充，测量模型不消耗）',c='ic_reactor');slot('maintenance.blank_check_area_s',nullable(number('s')),'s','最近TC Blank Check平均峰面积（稀释水碳零假设）',c='ndir_detector');slot('maintenance.syringe_zeroed',BOOL,'1','已做注射器零点检测',c='syringe_plunger')
slot('maintenance.counters',COUNTER_SLOT,'1','维护计数器（值、上限、超限）；体积m^3、时间s、次数1')
slot('monitor.carrier_flow_m3_s',number('m^3/s',0),'m^3/s','Background Monitor载气流量（就绪时150 mL/min，否则0）',c='body');slot('monitor.furnace_stable',BOOL,'1','炉温在设定±1 K内',c='combustion_furnace');slot('monitor.dehumidifier_k',nullable(number('K')),'K','除湿器温度（假设1 °C恒定；断电为空）',c='body')
slot('monitor.baseline_ok',BOOL,'1','基线位置/波动/噪声OK（通电满30 min的假设）',c='ndir_detector');slot('monitor.ready',BOOL,'1','READY灯：Background Monitor全部OK（炉温±10 K、载气、基线、门盖关闭）',c='ready_lamp')
# ---------------------------------------------------------------- 1.2.0 (sync fixes 2026-10-19): brochure p.11 functions of the standalone model
# Automatic selection of the optimal calibration curve: up to three curves per channel are set for sample measurements; each aliquot is
# quantified with the curve whose calibrated carbon-mass range contains its result (several: the lowest range; none: the first curve, flagged
# out of range).  Automatic sleep / restart: after the analyses (and maintenance) are completed and the set time has elapsed the instrument goes
# to sleep (assumed: furnace heating and carrier gas off, READY off); it restarts at the set time of day (or with Restart on the sleep screen);
# the baseline settles again for 30 min.  The setting values, the sleep screen and what sleep switches off are assumptions (manual member-only).
DAY_S=86400.;SLEEP_DEFAULT=dict(auto_sleep=False,sleep_after_s=3600.,auto_restart=False,restart_hour=8,restart_minute=0)
kcap('calibration.set_slot',obj({'channel':enum(CHANNELS),'slot':integer(1,3),'curve_id':TEXT}),obj({'selected':BOOL}),['display_panel'],'CAL：每个channel最多3条用于样品测量的标线（槽1即calibration.select的选用标线，空ID清空该槽）；测量时每份aliquot自动选用校准质量范围覆盖其结果的标线（多条覆盖取范围上限最小者）','configuration','agent',{'channel':'TC','slot':2,'curve_id':''})
kcap('sleep.configure',obj({'auto_sleep':BOOL,'sleep_after_s':number('s',60,59940),'auto_restart':BOOL,'restart_hour':integer(0,23),'restart_minute':integer(0,59)}),obj({'configured':BOOL}),['display_panel'],'Conditions - Others：自动休眠（分析与维护结束后空闲设定时间进入休眠）与定时重启（每天设定时刻）','configuration','agent',dict(SLEEP_DEFAULT))
# main-unit error codes and messages of the TOC-L error list (SSI Virtual Advisor; other__ssi_virtual_advisor_toc-l_error_codes.html), 1.3.0 sweep
TOC_ERRORS={'pressure_sensor_3':'Error!! Pressure Sensor 3','pressure_sensor_2':'Error!! Pressure Sensor 2','pressure_sensor_1':'Error!! Pressure Sensor 1',
            'sparge_gas_flow_line':'Error!! Sparge Gas Flow Line','toc_detector_connection':'Error!! TOC Detector Connection',
            'toc_detector_memory':'Error!! TOC Detector Memory','toc_detector_motor':'Error!! TOC Detector Motor','toc_detector_temp_control':'Error!! TOC Detector Temp. Control',
            'cooler_temp_control':'Error!! Cooler Temp. Control','cooler_fan_stop':'Error!! Cooler Fan Stop','furnace_power':'Error!! Furnace Power',
            'furnace_safety_relay':'Error!! Defect safety relay of Furnace','main_fan_stop':'Error!! Main Fan Stop','ic_slider':'Error!! IC Slider','tc_slider':'Error!! TC Slider',
            '8port_valve_position':'Error!! 8-Port Valve Position Detection','syringe_zero_point':'Error!! Syringe zero point detection',
            'syringe_operation':'Error!! Syringe Operation','syringe_position':'Error!! Syringe Position','furnace_temperature_sensor':'Error!! Furnace Temperature Sensor',
            'furnace_overheating':'Error!! Furnace Overheating','flash_rom':'Error!! FLASH ROM'}
TOC_NOTICES={'cf_card':'Unable to access the CF card.','backup_battery':'Exchange the back-up battery.','valve_retry':'8-Port Valve Operation Retry has occurred.',
             'syringe_zero_failed':'Syringe Zero Point Correction failed.'}
TOC_FAULTS=list(TOC_ERRORS)+list(TOC_NOTICES)
kcap('fault.inject',obj({'code':enum(TOC_FAULTS)}),obj({'injected':arr(TEXT)}),['display_panel'],'评测者：注入TOC-L错误表中主机的错误代码（Error!! …：正在进行的分析与维护停止，错误画面保持到关机再开，原因仍在时开机再次显示）与信息（CF卡不可访问：拒绝分析；更换备份电池与8通阀重试：只显示；注射器零点校正失败：该维护项失败）','simulation','evaluator',{'code':'main_fan_stop'})
kcap('fault.clear',obj({'code':enum(TOC_FAULTS)}),obj({'injected':arr(TEXT)}),['display_panel'],'评测者：错误表中的措施已完成（清除颗粒、清洗阀、装好注射器、服务修理）：消除原因；显示中的错误仍须关机再开','simulation','evaluator',{'code':'main_fan_stop'})
kcap('sleep.restart',obj({}),obj({'sleeping':BOOL}),['display_panel'],'休眠画面F1 Restart：手动结束休眠（炉重新升温、载气恢复、基线重新稳定30 min）')
slot('calibration.curve_slots',obj({c:dict(type='array',items=TEXT,minItems=3,maxItems=3) for c in CHANNELS}),'1','每个channel用于样品测量的最多3条标线（槽1=选用标线；空串为空槽）','setpoint')
slot('sleep.settings',obj({'auto_sleep':BOOL,'sleep_after_s':number('s',60,59940),'auto_restart':BOOL,'restart_hour':integer(0,23),'restart_minute':integer(0,59)}),'1','自动休眠/定时重启设置','setpoint')
slot('fault.injected',arr(TEXT),'1','评测者注入且尚未消除的错误原因（fault.inject）','setpoint','evaluator');slot('sleep.active',BOOL,'1','休眠中（假设：炉加热与载气关闭，READY灭）',c='display_panel');slot('clock.time_of_day_s',number('s',0,DAY_S),'s','仪器时钟的当日时刻（仿真时间0为00:00，假设）',c='display_panel')
def _panel_engine():
 try:
  from . import _panel_ui as engine                                      # imported as asset_runtime.shimadzu_tocl (tests, Matterix bridge)
  return engine
 except ImportError:                                                     # loaded from its file path
  import sys
  if '_chemsim_panel_ui' not in sys.modules:
   spec=importlib.util.spec_from_file_location('_chemsim_panel_ui',Path(__file__).with_name('_panel_ui.py'))
   sys.modules['_chemsim_panel_ui']=importlib.util.module_from_spec(spec);spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
  return sys.modules['_chemsim_panel_ui']
_panel_ui=_panel_engine()
PANEL_SPEC=_panel_ui.load_spec(ROOT/'assets/expansion_batch03/t3_analytical/shimadzu_tocl/panel_ui.json',capabilities=set(CAPS)|set(KEY_CAPS))
PANEL_CAPS={k:[v['input_schema'],v['output_schema'],v['component_ids'],v['function_zh'],v['kind'],v['visibility'],v['example_arguments'],[]] for k,v in _panel_ui.capability_definitions(PANEL_SPEC).items()}
ALL_CAPS=dict(CAPS,**KEY_CAPS,**PANEL_CAPS)
NEW_ERRORS={'power.set_mains':['INVALID_ARGUMENT','REQUEST_CONFLICT'],'power.shutdown':['POWER_OFF','BUSY','REQUEST_CONFLICT'],'settings.configure':['INVALID_ARGUMENT','BUSY','REQUEST_CONFLICT'],
 'maintenance.run':['INVALID_ARGUMENT','BUSY','POWER_OFF','GAS_POWER_INTERLOCK','OVEN_TEMPERATURE_LOW','DRAIN_CANCELLED_RESIDUE_REQUIRED','REAGENT_VOLUME_LIMIT','REQUEST_CONFLICT'],
 'maintenance.tc_blank_check':['INVALID_ARGUMENT','BUSY','POWER_OFF','GAS_POWER_INTERLOCK','CATALYST_NOT_HIGH','FURNACE_NOT_READY','DRAIN_CANCELLED_RESIDUE_REQUIRED','REAGENT_VOLUME_LIMIT','REQUEST_CONFLICT'],
 'maintenance.stop':['REQUEST_CONFLICT'],'calibration.set_slot':['INVALID_ARGUMENT','BUSY','CURVE_CHANNEL_MISMATCH','REQUEST_CONFLICT'],
 'sleep.configure':['INVALID_ARGUMENT','REQUEST_CONFLICT'],'sleep.restart':['NOT_SLEEPING','REQUEST_CONFLICT'],'maintenance.reset_counter':['INVALID_ARGUMENT','REQUEST_CONFLICT'],'maintenance.set_counter_limits':['INVALID_ARGUMENT','REQUEST_CONFLICT'],'records.clear':['BUSY','REQUEST_CONFLICT'],
 **{k:['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PLANT_REQUIRED','REQUEST_CONFLICT'] for k in PANEL_CAPS}}
NEW_LIMITS={**{k:['面板层按panel_ui.json（SSI Virtual Advisor、关机/开机说明与样本为证据，未写之处见assumptions）解释按键与开关手势并调用本运行层的同名能力；手势不推进仿真时钟。'] for k in PANEL_CAPS},
 'power.set_mains':['主开关的最小模型：关断等同断电（运行/维护/关机中止）；接通后前面电源键才可开机。'],
 'power.shutdown':['单机关机的最小模型：加热关闭、炉温按集总热模型冷却，30 min后自行断电；载气关闭与3次空白冲洗属操作者。'],
 'settings.configure':['催化剂类型只作为设置与TC Blank Check前提，不改变转化动力学；每次清洗把一份原样aliquot经注射器排废（瞬时，不另推进时间）。'],
 'maintenance.run':['维护项为有限时长的最小模型（时长为假设）：试剂用量在开始时由稀释水/酸瓶计入废液；残液去除把注射器/反应器残液及其碳转入废液、残气转入尾气；IC试剂液位只由补充增加，测量模型不消耗IC试剂；STOP中止后已用试剂不退回。'],
 'maintenance.tc_blank_check':['稀释水按运行层假设为无碳：空白峰面积为0；不做逐针NDIR积分，sets×5针的稀释水、注射次数、阀动作与燃烧管进样量计入计数器。'],
 'maintenance.stop':['只中止维护项；测量用measurement.stop。'],
 'maintenance.reset_counter':['计数器由运行层累计（阀动作、注射行程、燃烧管进样量、酸/稀释水用量、通电时间、IC泵时间）；清零只改显示零点。'],
 'maintenance.set_counter_limits':['上限只用于面板Counter Over-提示，不阻止测量。'],
 'records.clear':['删除全部实例结果条目；已完成运行的历史快照不改写。'],
 'calibration.set_slot':['样本p.11只说明最多3条标线并自动选最佳：选择规则（结果所在校准范围、多条时取范围上限最小者、都不覆盖时用槽1并标记超范围）与CAL画面为假设；标线须与当前测量条件匹配才参与。'],
 'sleep.configure':['样本p.11只说明分析结束一定时间后自动休眠、可在设定时刻自动重启：设置项、默认值（关闭、60 min、08:00）、休眠时关闭炉加热与载气为假设；按键操作不计入空闲计时。'],
 'sleep.restart':['手动重启键（休眠画面F1）为假设；重启后炉升温、载气恢复，基线按开机同样的30 min重新稳定。']}
# 1.3.0 sweep: instrument errors (fault.inject / fault.clear) and the owner guard of the maintenance STOP
for _k in ('maintenance.run','maintenance.tc_blank_check'):NEW_ERRORS[_k]=NEW_ERRORS[_k][:-1]+['INSTRUMENT_ERROR']+(['SYRINGE_ZERO_CORRECTION_FAILED'] if _k=='maintenance.run' else [])+NEW_ERRORS[_k][-1:]
NEW_ERRORS['maintenance.stop']=['ACCESS_DENIED','REQUEST_CONFLICT']
NEW_ERRORS.update({'fault.inject':['INVALID_ARGUMENT','REQUEST_CONFLICT'],'fault.clear':['INVALID_ARGUMENT','REQUEST_CONFLICT']})
NEW_LIMITS.update({'fault.inject':['只含TOC-L错误表中主机的代码与信息；故障原因（颗粒、电子、风扇等）不建模，由评测者给出；Error!!画面保持到关机再开，原因在时开机再次出现（Furnace Overheating的通电冷却20 min不检查）。'],
                   'fault.clear':['表示错误表中的措施已完成；显示中的Error!!仍须关机再开。']})

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
  self._caller=None;self.faults=[];self.error_shown=None   # session caller of the call in progress; evaluator faults and the error screen (1.3.0)
  self.mains=True;self.power_epoch=0;self._power_on_s=None;self._shutdown_at=None;self.catalyst='regular';self.washes=0;self._maint=None;self._maint_last=dict(item='',phase='idle');self.ic_reagent_level=0.;self.blank_check_area=None;self.syringe_zeroed=False
  self._n_valve=0;self._n_strokes=0;self._v_combustion=0.;self._t_powered=0.;self._t_ic_pump=0.;self._counter_zero={k:0. for k in COUNTERS};self._counter_limits={k:None for k in COUNTERS}
  self._extra_curves={c:['',''] for c in CHANNELS};self._run_curve_sets={};self.sleep_cfg=dict(SLEEP_DEFAULT);self.sleeping=False;self._last_activity_s=0.;self._restart_at=None;self._settle_s=None
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return ShimadzuToclSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in ['library','templates','_rng','_panel']})|{'_rng_state':copy.deepcopy(self._rng.bit_generator.state)}
 def _checkpoint(self):
  # Stored records, curves and terminal responses are append-only immutable values; copying their index avoids quadratic peak-array copies.
  frozen=['library','templates','_records','_curves','_operation_records','_requests','_rng','_panel'];q=copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in frozen})
  for k in frozen:
   if k in self.__dict__:q[k]=dict(getattr(self,k)) if k in ['_records','_curves','_operation_records','_requests'] else getattr(self,k)
  q={k:q[k] for k in self.__dict__}
  return q,copy.deepcopy(self._rng.bit_generator.state)
 def _channels(self):return ['TC','IC'] if self.method and self.method['mode']=='TOC' else [self.method['mode']] if self.method else []
 def _key(self):
  # Fits use absolute carbon mass, so injection/dilution/repeat/mode do not redefine detector gain; treatment and peak integration conditions do.
  return json.dumps({k:v for k,v in (self.method or {}).items() if k not in ['mode','injection_volume_m3','dilution_factor','repeats']},sort_keys=True)
 def _blank_valid(self):return bool(self.method and all(c in self._blanks and self._blanks[c]['key']==self._key() for c in self._channels()))
 def _slot_ids(self,c):return [x for x in [self._selected[c]]+self._extra_curves[c] if x]
 def _calibration_ready(self):return bool(self.method and all(any(i in self._curves and self._curves[i]['key']==self._key() for i in self._slot_ids(c)) for c in self._channels()))
 def _ready(self):return bool(self.plant and self.method and self.powered and not self.opened and not self.top_opened and self.target==953.15 and abs(self.temperature-self.target)<=self.method['temperature_tolerance_k'])
 def _ledger(self):
  sample_c=self.sample['concentrations']*self.sample['volume'] if self.sample else np.zeros(3);sample_v=self.sample['volume'] if self.sample else 0.;residual=self._registered-sample_c-self._syringe-self._reactor-self._co2-self._tail-self._exhaust-self._purged-self._waste-self._removed;di=self.plant['diluent_volume_m3'] if self.plant else 0.;ai=self.plant['acid_volume_m3'] if self.plant else 0.;ac=self.plant['acid_concentration_mol_m3'] if self.plant else 0.;heat=(self.temperature-self.initial_temperature)*(self.plant['furnace_capacity_j_k'] if self.plant else 0.)
  return dict(carbon_kg=dict(registered=_mass(self._registered),sample=_mass(sample_c),syringe=_mass(self._syringe),reactor=_mass(self._reactor),co2_buffer=_mass(self._co2),co2_tail=_mass(self._tail),detected_exhaust=_mass(self._exhaust),purged=_mass(self._purged),liquid_waste=_mass(self._waste),removed=_mass(self._removed),residual=_mass(residual)),volume_m3=dict(registered_sample=self._registered_v,initial_diluent=di,initial_acid=ai,sample=sample_v,diluent=self._diluent_v,acid=self._acid_v,syringe=self._syringe_v,reactor=self._reactor_v,waste=self._waste_v,removed=self._removed_v,residual=self._registered_v+di+ai-sample_v-self._diluent_v-self._acid_v-self._syringe_v-self._reactor_v-self._waste_v-self._removed_v),oxygen_initial_mol=self._oxygen_initial,oxygen_remaining_mol=self._oxygen_remaining,oxygen_consumed_mol=self._oxygen_consumed,oxygen_residual_mol=self._oxygen_initial-self._oxygen_remaining-self._oxygen_consumed,acid_initial_mol=ai*ac,acid_available_mol=self._acid_v*ac,acid_injected_mol=(ai-self._acid_v)*ac,acid_residual_mol=ai*ac-self._acid_v*ac-(ai-self._acid_v)*ac,net_furnace_heat_j=self.heat,stored_furnace_heat_j=heat,thermal_residual_j=self.heat-heat,active_template_id=self.sample['input']['template_id'] if self.sample else None,carbon_partition_evidence=self.sample['partition_evidence'] if self.sample else '',evidence='three carbon-origin warehouses and aqueous volume proxy; pure diluent/acid carbon-free assumptions; oxygen coefficient and conservative carbonate proton demand are host model choices; acid ledger is delivered reagent amount, not full speciation/pH; net furnace heat is not total electrical energy or water-vapour enthalpy')
 def _result(self):return dict(template_source=TEMPLATE_REL,phase=self.phase,stage=self.stage,complete=self.phase=='succeeded',record_id=self._record_id,mode=self._run_method['mode'] if self._run_method else '',kind=self._run_kind,model_assumptions=list(self._run_assumptions),concentrations_kg_m3=copy.deepcopy(self._summary),sd_kg_m3=copy.deepcopy(self._sd),cv=copy.deepcopy(self._cv),qc=list(self._qc),assays=copy.deepcopy(self.assays),active_assay=copy.deepcopy(self._active),evidence='source carbon quantity retained in template; observed synthetic NDIR peaks and caller-fitted carbon-mass calibration; host QC/mean/sample-SD/CV are not CPH software or manufacturer repeatability validation')
 def _measure(self):return dict(phase=self.phase,stage=self.stage,elapsed_s=self.elapsed_s,progress=len(self.assays)/len(self._plan) if self._plan else 0.,ndir_signal=self._signal,concentrations_kg_m3=copy.deepcopy(self._summary),qc=list(self._qc),warning=self.warning)
 def read(self,visibility='agent'):
  if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
  v={'plant.configured':self.plant is not None,'sample.loaded':self.sample is not None,'power.enabled':self.powered,'cover.opened':self.opened,'cover.angle_rad':1.60 if self.opened else 0.,'top_cover.opened':self.top_opened,'top_cover.lift_m':.15 if self.top_opened else 0.,'syringe.position_m':min(SYRINGE_STROKE_M,max(0.,self._syringe_v/SYRINGE_MAX_M3*SYRINGE_STROKE_M)),'valve.angle_rad':self.valve,'ic_pump.angle_rad':self.pump,'furnace.target_k':self.target,'furnace.actual_k':self.temperature,'furnace.ready':self._ready(),'measurement.phase':self.phase,'measurement.stage':self.stage,'measurement.elapsed_s':self.elapsed_s,'measurement.progress':len(self.assays)/len(self._plan) if self._plan else 0.,'measurement.channel':self._channel,'measurement.completed_aliquots':len(self.assays),'detector.ndir_signal':self._signal,'detector.last_sample_time_s':self._last_point['time_s'] if self._last_point else None,'method.mode':self.method['mode'] if self.method else '', 'blank.valid':self._blank_valid(),'calibration.ready':self._calibration_ready(),'storage.result_count':len(self._records),'storage.curve_count':len(self._curves),'safety.warning':self.warning,'truth.ledger':self._ledger()}
  v.update({'result.'+q.lower()+'_kg_m3':self._summary[q] for q in QUANTITIES});v.update(self._key_values())
  if visibility=='agent':                                                 # agents see only agent slots (truth.ledger, fault.injected: evaluator; 1.3.1)
   for k in [k for k in v if SLOTS[k][3]!='agent']:v.pop(k)
  return copy.deepcopy(v)
 def invoke(self,k,**a):
  if k in PANEL_CAPS:                                                     # panel gestures: optional arguments, checked by the engine's validator
   if _panel_ui.check_arguments(PANEL_CAPS[k][0],a):raise ActionRejected('INVALID_ARGUMENT')
   if not self.plant:raise ActionRejected('PLANT_REQUIRED')
   out=self._cmd_panel(k,a)
   if PANEL_CAPS[k][4]!='query':self.revision+=1
   return copy.deepcopy(out)
  if k not in CAPS and k not in KEY_CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  C=CAPS[k] if k in CAPS else KEY_CAPS[k]
  validate(C[0],a)
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  self._owner_guard(k,a)
  query=C[4]=='query';checkpoint=None if query else self._checkpoint()
  try:
   result=self._invoke(k,copy.deepcopy(a));validate(C[1],result)
   if not query:self.revision+=1;self._archive_operation()
   return copy.deepcopy(result)
  except Exception:
   if checkpoint:self.__dict__.clear();self.__dict__.update(checkpoint[0]);self._rng.bit_generator.state=checkpoint[1]
   raise
 def _owner_guard(self,k,a):
  """An agent may not stop or switch off another agent's analysis or maintenance item (STOP key / measurement.stop / maintenance.stop, the front
  power key); the direct call and the panel key run with the session caller in _caller.  The main switch is a hard switch and never refused."""
  c=self._caller
  if c is None or self._principals.get(c)!='agent':return
  foreign=lambda o:o is not None and o!=c and self._principals.get(o)=='agent'
  run=foreign(self._owners.get(self.operation_id)) if self.phase=='running' else False
  mnt=foreign((self._maint or {}).get('owner'))
  if (k=='measurement.stop' and run) or (k=='maintenance.stop' and mnt) or ((k=='power.toggle' and self.powered or k=='power.set' and not a['enabled']) and (run or mnt)):
   raise ActionRejected('ACCESS_DENIED')
 def _error_gate(self):
  if self.error_shown:raise ActionRejected('INSTRUMENT_ERROR')
 def _fault_inject(self,code):
  if code not in self.faults:self.faults.append(code)
  if code in TOC_ERRORS and self.powered:
   self.error_shown=code
   if self.phase=='running':self._stop('INSTRUMENT_ERROR',failed=True)
   self._maint_stop()
  return {'injected':list(self.faults)}
 def _notice(self):
  if not self.powered:return ''
  return next((TOC_NOTICES[c] for c in ('backup_battery','valve_retry') if c in self.faults),'')
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
  if k in KEY_CAPS:return self._key_fn(k,a)
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
   if a['opened']:self._maint_stop()
   if k=='cover.set':self.opened=a['opened']
   else:self.top_opened=a['opened']
   return {'opened':a['opened']}
  if k=='power.toggle':return self._invoke('power.set',{'enabled':not self.powered})
  if k=='power.set':
   if a['enabled'] and (not self.plant or self.opened or self.top_opened or not self.mains):raise ActionRejected('POWER_INTERLOCK')
   if not a['enabled'] and self.phase=='running':self._stop('POWER_OFF')
   if a['enabled'] and not self.powered:
    self.power_epoch+=1;self._power_on_s=self.time_s;self._last_activity_s=self.time_s;self._settle_s=None
    self.error_shown=next((c for c in TOC_ERRORS if c in self.faults),None)   # a cause still present shows again at power-up
   if not a['enabled']:self.error_shown=None
   if not a['enabled']:self._maint_stop();self._shutdown_at=None;self._power_on_s=None;self.sleeping=False;self._restart_at=None
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
   if key and key in self._extra_curves[a['channel']]:raise ActionRejected('INVALID_ARGUMENT')   # already set in slot 2 or 3
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
  self._error_gate()
  if 'cf_card' in self.faults:raise ActionRejected('CF_CARD_ERROR')       # results cannot be stored (service)
  if self._maint or self._shutdown_at is not None or self.sleeping:raise ActionRejected('BUSY')
  if not self.plant or not self.method or not self.sample:raise ActionRejected('PLANT_METHOD_SAMPLE_REQUIRED')
  if not self.powered or self.opened or self.top_opened or not self.plant['carrier_ready']:raise ActionRejected('GAS_POWER_INTERLOCK')
  if self.method['mode']!='IC' and not self._ready():raise ActionRejected('FURNACE_NOT_READY')
  if self._syringe_v>1e-18 or self._reactor_v>1e-18 or np.any(self._co2>1e-18):raise ActionRejected('DRAIN_CANCELLED_RESIDUE_REQUIRED')
  if len(self._records)>=RESULT_CAPACITY:raise ActionRejected('RESULT_STORE_FULL')
  n=len(self._channels())*self.method['repeats'];source_v=self.method['injection_volume_m3']/self.method['dilution_factor'];diluent=n*(self.method['injection_volume_m3']-source_v);acid=sum(c in ['IC','NPOC'] for c in self._channels())*self.method['repeats']*self.method['acid_volume_m3']
  if self.sample['volume']+1e-18<n*source_v*(1+self.washes):raise ActionRejected('SAMPLE_VOLUME_LIMIT')
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
  self._run_curves={c:copy.deepcopy(self._curves.get(self._selected[c])) for c in CHANNELS};self._run_curve_sets={c:[copy.deepcopy(self._curves[i]) for i in self._slot_ids(c) if i in self._curves] for c in CHANNELS};self._run_blanks={};self._plan=[(c,i) for i in range(self.method['repeats']) for c in self._channels()];self._index=0;self.assays=[];self._active=None;self._summary=_nulls();self._sd=_nulls();self._cv=_nulls();self._qc=[];self._last_point=None;self._signal=None;self._record_id=None;self.phase='running';self.elapsed_s=0.;self.warning='';self._op_count+=1;self.operation_id=f'{self.instance_id}:measurement:{self._op_count}';self._begin();return dict(phase=self.phase,operation_id=self.operation_id)
 def _begin(self):
  c,i=self._plan[self._index];self._channel=c;self._active=dict(channel=c,repeat_index=i,status='running',injection_volume_m3=self._run_method['injection_volume_m3'],original_sample_volume_m3=self._run_method['injection_volume_m3']/self._run_method['dilution_factor'],peak_area_s=0.,concentration_kg_m3=None,quality='not_completed',peak=[],settings_json=json.dumps(self._run_method,sort_keys=True),calibration_json=json.dumps(self._run_curves.get(c),sort_keys=True));self._prebaseline=[]
  if self.washes:
   v=min(self.sample['volume'],self.washes*self._active['original_sample_volume_m3']);self._waste+=self.sample['concentrations']*v;self._waste_v+=v;self.sample['volume']-=v;self._n_strokes+=self.washes
  self._enter('baseline')
 def _enter(self,stage):
  self.stage=stage;self._stage_elapsed=0.;self._next_detector=None;self._pending_area=0.
  angles={'select_sample':0.,'select_diluent':math.pi/4,'select_reactor':math.pi/2,'select_acid':math.pi,'select_combustion':3*math.pi/4,'select_detector':5*math.pi/4}
  if stage in angles:self._n_valve+=1
  if stage=='inject':self._n_strokes+=1
  elif stage=='combust':self._v_combustion+=self._reactor_v
  if stage in angles:self._target_angle=angles[stage]
  elif stage=='aspirate_sample':self._stage_remaining_v=self._active['original_sample_volume_m3']
  elif stage=='aspirate_diluent':self._stage_remaining_v=self._active['injection_volume_m3']-self._active['original_sample_volume_m3']
  elif stage=='inject':self._stage_remaining_v=self._syringe_v
  elif stage in ['baseline','detect']:
   self._detector_start=self.time_s;self._next_detector=self.time_s+self.plant['detector_period_s'];self._baseline_count=0
 def _stop(self,why,failed=False):
  self._last_activity_s=self.time_s
  self.phase='failed' if failed else 'cancelled';self.stage='idle';self.warning=why;self._next_detector=None
  if self._active:self._active['status']=self.phase;self._active['quality']='incomplete_no_final_concentration'
 def _physical(self,dt):
  if self.plant:
   eq=self.target if self.powered and not self.opened and not self.top_opened and self._shutdown_at is None and not self.sleeping else self.plant['ambient_k'];delta=(eq-self.temperature)*(-math.expm1(-self.plant['furnace_conductance_w_k']/self.plant['furnace_capacity_j_k']*dt));self.temperature+=delta;self.heat+=delta*self.plant['furnace_capacity_j_k']
   if self.powered:self._t_powered+=dt
   if self.powered and self.plant['carrier_ready'] and not self.sleeping:
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
   v=min(self._acid_v,m['acid_volume_m3']/m['acidification_s']*dt);self._acid_v-=v;self._reactor_v+=v;self._acid_chamber+=v*p['acid_concentration_mol_m3'];self.pump=(self.pump+p['ic_pump_speed_rad_s']*dt)%(2*math.pi);self._t_ic_pump+=dt
  elif self.stage=='sparge':
   converted=min(self._reactor[0]*(-math.expm1(-p['ic_release_rate_s_inv']*dt)),self._acid_chamber*C_KG_MOL/2);self._reactor[0]-=converted;self._acid_chamber=max(0.,self._acid_chamber-2*converted/C_KG_MOL)
   if self._channel=='IC':self._co2[0]+=converted
   else:
    poc=self._reactor[2]*(-math.expm1(-p['poc_strip_rate_s_inv']*dt));self._reactor[2]-=poc;self._purged[0]+=converted;self._purged[2]+=poc
   self.pump=(self.pump+p['ic_pump_speed_rad_s']*dt)%(2*math.pi);self._t_ic_pump+=dt
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
  if self._run_kind!='blank':curve=self._auto_curve(q,curve)
  if self._run_kind=='blank':self._run_blanks.setdefault(self._channel,[]).append(self._baseline)
  elif curve and curve['key']==self._run_key:
   mass=(np.longdouble(q['peak_area_s'])-curve['intercept_s'])/curve['slope_s_kg'];lo,hi=curve['carbon_mass_range_kg'];tol=max(hi,1e-300)*1e-9;concentration=mass/q['original_sample_volume_m3']
   if mass<lo-tol or mass>hi+tol:q['quality']='outside_calibrated_mass_range'
   elif not np.isfinite(concentration) or abs(concentration)>np.finfo(float).max:q['quality']='numerical_range'
   else:q['concentration_kg_m3']=float(concentration);q['quality']='estimated_with_source_channel_transfer' if curve['source_channel_transfer'] else 'estimated_from_observed_calibration'
  self.assays.append(copy.deepcopy(q));self._tail+=self._co2;self._co2[:]=0.;self._waste+=self._reactor;self._reactor[:]=0.;self._waste_v+=self._reactor_v;self._reactor_v=0.;self._acid_chamber=0.;self._active=None;self._index+=1
  if self._index<len(self._plan):self._begin()
  else:
   self.phase='succeeded';self.stage='idle';self._channel='';self._next_detector=None;self._last_activity_s=self.time_s
   if self._run_kind=='blank':
    for c,values in self._run_blanks.items():self._blanks[c]=dict(key=self._run_key,baseline=float(np.mean(values)),replicates=len(values))
   self._aggregate();self._record_id=f'{self.instance_id}:result:{self._op_count}';self._records[self._record_id]=self._result()
 def _auto_curve(self,q,first):
  """Automatic selection among the curves set for the channel (brochure p.11): the curve whose calibrated carbon-mass range contains the
  aliquot's result, the lowest range when several do; the first curve (result flagged out of range) when none does."""
  cands=[c for c in self._run_curve_sets.get(self._channel,[]) if c['key']==self._run_key]
  if len(cands)<2:return first
  inside=[]
  for c in cands:
   mass=(np.longdouble(q['peak_area_s'])-c['intercept_s'])/c['slope_s_kg'];lo,hi=c['carbon_mass_range_kg'];tol=max(hi,1e-300)*1e-9
   if lo-tol<=mass<=hi+tol:inside.append(c)
  best=min(inside,key=lambda c:c['carbon_mass_range_kg'][1]) if inside else first if first and first['key']==self._run_key else cands[0]
  q['calibration_json']=json.dumps(best,sort_keys=True);return best
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
  self._fire_timers()                                                     # a sleep that became due while idle (e.g. auto sleep switched on late)
  end=self.time_s+dt_s
  while True:                                                              # maintenance end / shutdown power-off split the step exactly
   cut=min([end]+[t for t in self._timers() if self.time_s<t<end])
   self._advance(cut-self.time_s);self._fire_timers()
   if cut>=end:break
  return self.read()
 def _sleep_at(self):
  if not (self.sleep_cfg['auto_sleep'] and self.powered and not self.sleeping and self._shutdown_at is None):return None
  if self.phase=='running' or self._maint:return self.time_s+self.sleep_cfg['sleep_after_s']   # step in chunks so the sleep after the end is exact
  return self._last_activity_s+self.sleep_cfg['sleep_after_s']
 def _timers(self):return [t for t in (self._maint['end_s'] if self._maint else None,self._shutdown_at,self._sleep_at(),self._restart_at if self.sleeping else None) if t is not None]
 def _fire_timers(self):
  m=self._maint
  if m and self.time_s>=m['end_s']-1e-9:
   if m['item']=='ic_regeneration':self.ic_reagent_level=min(1.,self.ic_reagent_level+IC_REAGENT_STEP)
   elif m['item']=='syringe_zero':self.syringe_zeroed=True
   elif m['item']=='tc_blank_check':self.blank_check_area=0.        # carbon-free dilution water: NDIR baseline only, no peak
   self._maint=None;self._maint_last=dict(item=m['item'],phase='succeeded');self.revision+=1
  if self._shutdown_at is not None and self.time_s>=self._shutdown_at-1e-9:
   self._shutdown_at=None;self.powered=False;self._power_on_s=None;self.sleeping=False;self._restart_at=None;self.revision+=1
  if m and not self._maint:self._last_activity_s=m['end_s']
  s=self._sleep_at()
  if s is not None and self.phase!='running' and not self._maint and self.time_s>=s-1e-9:   # analyses done and the set idle time elapsed
   self.sleeping=True;self._restart_at=self._next_restart() if self.sleep_cfg['auto_restart'] else None;self.revision+=1
  if self.sleeping and self._restart_at is not None and self.time_s>=self._restart_at-1e-9:self._wake()
 def _advance(self,dt_s):
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
  self.revision+=1;self._archive_operation()
 # ---- 1.1.0 operating functions and the panel host protocol
 def _maint_stop(self):
  if self._maint:self._maint_last=dict(item=self._maint['item'],phase='cancelled');self._maint=None;self._last_activity_s=self.time_s
 def _next_restart(self):
  t=math.floor(self.time_s/DAY_S)*DAY_S+self.sleep_cfg['restart_hour']*3600.+self.sleep_cfg['restart_minute']*60.
  return t if t>self.time_s+1e-9 else t+DAY_S
 def _wake(self):
  self.sleeping=False;self._restart_at=None;self._settle_s=self.time_s;self._last_activity_s=self.time_s;self.revision+=1
 def _counter_totals(self):
  p=self.plant;return {'valve_operations':float(self._n_valve),'syringe_strokes':float(self._n_strokes),'combustion_injection_m3':self._v_combustion,'acid_used_m3':max(0.,p['acid_volume_m3']-self._acid_v) if p else 0.,'diluent_used_m3':max(0.,p['diluent_volume_m3']-self._diluent_v) if p else 0.,'co2_absorber_s':self._t_powered,'ic_pump_s':self._t_ic_pump}
 def _monitor(self):
  on=bool(self.powered and self.plant);flow=CARRIER_FLOW_M3_S if on and self.plant['carrier_ready'] and not self.sleeping else 0.;hot=on and self.target==FURNACE_K and not self.sleeping
  base=on and not self.sleeping and self._power_on_s is not None and self.time_s-max(self._power_on_s,self._settle_s if self._settle_s is not None else -math.inf)>=BASELINE_SETTLE_S-1e-9
  near=hot and abs(self.temperature-self.target)<=10.
  return {'monitor.carrier_flow_m3_s':flow,'monitor.furnace_stable':bool(hot and abs(self.temperature-self.target)<=1.),'monitor.dehumidifier_k':DEHUMIDIFIER_K if on else None,'monitor.baseline_ok':bool(base),'monitor.ready':bool(near and flow>0 and base and not self.opened and not self.top_opened and self._shutdown_at is None)}
 def _key_values(self):
  tot=self._counter_totals();cur=self._maint or self._maint_last
  counters={k:dict(value=max(0.,tot[k]-self._counter_zero[k]),limit=self._counter_limits[k],over=self._counter_limits[k] is not None and tot[k]-self._counter_zero[k]>self._counter_limits[k]+1e-12) for k in COUNTERS}
  return {'power.mains':self.mains,'power.shutdown_remaining_s':None if self._shutdown_at is None else max(0.,self._shutdown_at-self.time_s),'settings.catalyst':self.catalyst,'settings.syringe_washes':self.washes,'maintenance.phase':'running' if self._maint else self._maint_last['phase'],'maintenance.item':cur['item'],'maintenance.remaining_s':max(0.,self._maint['end_s']-self.time_s) if self._maint else 0.,'maintenance.ic_reagent_level':self.ic_reagent_level,'maintenance.blank_check_area_s':self.blank_check_area,'maintenance.syringe_zeroed':self.syringe_zeroed,'maintenance.counters':counters,**self._monitor(),
          'calibration.curve_slots':{c:[self._selected[c]]+list(self._extra_curves[c]) for c in CHANNELS},'sleep.settings':dict(self.sleep_cfg),'sleep.active':self.sleeping,'clock.time_of_day_s':self.time_s%DAY_S,'fault.injected':list(self.faults)}
 def _key_fn(self,k,a):
  if k=='fault.inject':return self._fault_inject(a['code'])
  if k=='fault.clear':
   if a['code'] in self.faults:self.faults.remove(a['code'])
   return {'injected':list(self.faults)}
  if k=='power.set_mains':
   if not a['enabled']:
    self.error_shown=None
    if self.phase=='running':self._stop('POWER_OFF')
    self._maint_stop();self._shutdown_at=None;self.powered=False;self._power_on_s=None;self.sleeping=False;self._restart_at=None
   self.mains=a['enabled'];return {'enabled':self.mains}
  if k=='power.shutdown':
   if not self.powered:raise ActionRejected('POWER_OFF')
   if self.phase=='running' or self._maint:raise ActionRejected('BUSY')
   if self._shutdown_at is None:self._shutdown_at=self.time_s+SHUTDOWN_S
   self.sleeping=False;self._restart_at=None
   return {'shutdown_s':self._shutdown_at-self.time_s}
  if k=='settings.configure':
   self._idle();self.catalyst=a['catalyst'];self.washes=a['syringe_washes'];return {'configured':True}
  if k=='calibration.set_slot':
   self._idle();ch,key=a['channel'],a['curve_id'];curve=self._curves.get(key)
   if key and (not curve or curve['channel']!=ch):raise ActionRejected('CURVE_CHANNEL_MISMATCH')
   ids=[self._selected[ch]]+list(self._extra_curves[ch])
   if key and key in [x for i,x in enumerate(ids) if i!=a['slot']-1]:raise ActionRejected('INVALID_ARGUMENT')   # one curve, one slot
   if a['slot']==1:self._selected[ch]=key
   else:self._extra_curves[ch]=[key if i==a['slot']-2 else x for i,x in enumerate(self._extra_curves[ch])]
   return {'selected':True}
  if k=='sleep.configure':
   self.sleep_cfg=dict(a,sleep_after_s=float(a['sleep_after_s']))
   if self.sleeping:self._restart_at=self._next_restart() if a['auto_restart'] else None
   return {'configured':True}
  if k=='sleep.restart':
   if not self.sleeping:raise ActionRejected('NOT_SLEEPING')
   self._wake();return {'sleeping':False}
  if k=='maintenance.stop':
   self._maint_stop();return {'phase':self._maint_last['phase']}
  if k=='maintenance.reset_counter':
   self._counter_zero=dict(self._counter_zero,**{a['counter']:self._counter_totals()[a['counter']]});return {'counter':a['counter']}
  if k=='maintenance.set_counter_limits':
   self._counter_limits=dict(a);return {'configured':True}
  if k=='records.clear':
   self._idle();n=len(self._records);self._records={};return {'removed':n}
  self._idle()                                                           # maintenance.run / maintenance.tc_blank_check
  if not self.powered:raise ActionRejected('POWER_OFF')
  self._error_gate()
  if self._maint or self._shutdown_at is not None or self.sleeping:raise ActionRejected('BUSY')
  if self.opened or self.top_opened:raise ActionRejected('GAS_POWER_INTERLOCK')
  item='tc_blank_check' if k=='maintenance.tc_blank_check' else a['item'];residue=self._syringe_v>1e-18 or self._reactor_v>1e-18 or bool(np.any(self._co2>1e-18))
  diluent=acid=0.;strokes=valves=0;combustion=0.;pump=0.
  if item=='bubble_removal':diluent,acid,strokes,valves=BUBBLE_DILUENT_M3,BUBBLE_ACID_M3,2,4
  elif item=='catalyst_regeneration':
   if self.target!=FURNACE_K or self.temperature<self.target-10.:raise ActionRejected('OVEN_TEMPERATURE_LOW')
   acid,strokes,valves,combustion=REGEN_ACID_M3,1,2,REGEN_ACID_M3
  elif item=='ic_regeneration':pump=MAINT_S[item]
  elif item=='residue_removal':diluent,strokes,valves=RESIDUE_FLUSH_M3,2,4
  elif item=='syringe_zero':
   if self._syringe_v>1e-18:raise ActionRejected('DRAIN_CANCELLED_RESIDUE_REQUIRED')
   if 'syringe_zero_failed' in self.faults:raise ActionRejected('SYRINGE_ZERO_CORRECTION_FAILED')   # syringe not properly installed
   strokes=1
  else:
   if self.catalyst!='high':raise ActionRejected('CATALYST_NOT_HIGH')
   if not self.plant['carrier_ready']:raise ActionRejected('GAS_POWER_INTERLOCK')
   if self.target!=FURNACE_K or abs(self.temperature-self.target)>10.:raise ActionRejected('FURNACE_NOT_READY')
   if residue:raise ActionRejected('DRAIN_CANCELLED_RESIDUE_REQUIRED')
   n=a['sets']*BLANK_CHECK_INJECTIONS;volume=(self.method or METHOD_EXAMPLE)['injection_volume_m3'];diluent,strokes,valves,combustion=n*volume,n,5*n,n*volume
  if self._diluent_v+1e-18<diluent or self._acid_v+1e-18<acid:raise ActionRejected('REAGENT_VOLUME_LIMIT')
  self._diluent_v-=diluent;self._acid_v-=acid;self._waste_v+=diluent+acid       # carbon-free reagents to waste at the start
  if item=='residue_removal':                                            # the residue of a cancelled run goes to waste (as fluidics.drain, powered)
   v=self._syringe_v+self._reactor_v;self._waste_v+=v;self._syringe_v=self._reactor_v=0.;self._waste+=self._syringe+self._reactor;self._syringe[:]=0.;self._reactor[:]=0.;self._tail+=self._co2;self._co2[:]=0.;self._acid_chamber=0.
  self._n_strokes+=strokes;self._n_valve+=valves;self._v_combustion+=combustion;self._t_ic_pump+=pump
  duration=MAINT_S[item] if item in MAINT_S else n*BLANK_CHECK_INJECTION_S
  self._maint=dict(item=item,end_s=self.time_s+duration,owner=self._caller);self._maint_last=dict(item=item,phase='running')
  return dict(injections=n,duration_s=duration) if item=='tc_blank_check' else dict(item=item,duration_s=duration)
 @property
 def panel(self):
  if '_panel' not in self.__dict__:self.__dict__['_panel']=_panel_ui.PanelUI(PANEL_SPEC,self)
  return self.__dict__['_panel']
 def panel_read(self):
  out=self.read('agent');m=self.method or METHOD_EXAMPLE;recs=list(self._records.items())[::-1][:10]
  def text(r):
   c=r['concentrations_kg_m3'];q=r['mode'] if r['mode'] in c and c.get(r['mode']) is not None else next((x for x in QUANTITIES if c.get(x) is not None),None)
   return '---' if q is None else f'{q} {c[q]*1000.:.3f} mg/L'
  out.update({'host.error_text':TOC_ERRORS.get(self.error_shown,'') if self.powered else '','host.notice':self._notice()})
  out.update({'host.powered':bool(self.powered),'host.power_epoch':self.power_epoch,'host.powered_for_s':self.time_s-self._power_on_s if self._power_on_s is not None else 0.,
              'host.method':dict(m),'host.aliquots':len(['TC','IC'] if m['mode']=='TOC' else [m['mode']])*m['repeats'],'host.ambient_k':self.plant['ambient_k'] if self.plant else 298.15,
              'host.carrier_ready':bool(self.plant and self.plant['carrier_ready']),'host.record_count':len(self._records),
              'host.records':[dict(id=i,number=int(i.rsplit(':',1)[-1]) if i.rsplit(':',1)[-1].isdigit() else 0,mode=r['mode'],kind=r['kind'],text=text(r)) for i,r in recs],
              'host.curves':dict(self._selected),'host.next_curve':{c:self._next_curve(c) for c in CHANNELS},
              'host.slot_curves':{c:list(self._extra_curves[c]) for c in CHANNELS},'host.next_slot_curve':{c:[self._next_slot_curve(c,s) for s in (2,3)] for c in CHANNELS},
              'host.restart_hhmm':f"{self.sleep_cfg['restart_hour']:02d}:{self.sleep_cfg['restart_minute']:02d}",
              'host.counter_over':next((COUNTERS[k][1] for k,v in out['maintenance.counters'].items() if v['over']),None)})
  return out
 def _next_slot_curve(self,channel,s):
  others=[x for i,x in enumerate([self._selected[channel]]+self._extra_curves[channel]) if i!=s-1 and x]
  ids=[k for k,v in self._curves.items() if v['channel']==channel and k not in others]+['']
  cur=self._extra_curves[channel][s-2];return ids[(ids.index(cur)+1)%len(ids)] if cur in ids else ids[0]
 def _next_curve(self,channel):
  ids=[k for k,v in self._curves.items() if v['channel']==channel and k not in self._extra_curves[channel]]+['']
  cur=self._selected[channel];return ids[(ids.index(cur)+1)%len(ids)] if cur in ids else ids[0]
 def panel_invoke(self,capability_id,arguments):
  try:return self.invoke(capability_id,**arguments)
  except ActionRejected as exc:raise _panel_ui.PanelRejected(str(exc)) from None
 def panel_time(self):return self.time_s
 def _panel_gesture(self,gesture,*args):
  try:return dict(accepted=bool(gesture(*args)))
  except _panel_ui.PanelError:raise ActionRejected('INVALID_ARGUMENT') from None
 def _cmd_panel(self,k,a):
  if k=='panel.press':return self._panel_gesture(self.panel.press,a['input_id'],float(a.get('duration_s',.1)),tuple(a.get('held') or ()))
  if k=='panel.switch':return self._panel_gesture(self.panel.switch,a['input_id'],a['position'],tuple(a.get('held') or ()))
  if k=='panel.frame':return dict(frame=self.panel.frame())
  import hashlib,tempfile
  data,w,h=self.panel.png_bytes(a['display_id']);sha=hashlib.sha256(data).hexdigest()
  d=Path(getattr(self,'panel_capture_dir',None) or Path(tempfile.gettempdir())/'chemistry_sim_panel'/self.instance_id)
  d.mkdir(parents=True,exist_ok=True);path=d/f"{a['display_id']}_{sha[:16]}.png"
  if not path.exists():path.write_bytes(data)
  return dict(display_id=a['display_id'],time_s=self.time_s,image=dict(path=str(path),format='png',width=w,height=h,sha256=sha))
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
        m=export_shimadzu_tocl_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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
            if not isinstance(capability_id,str) or capability_id not in ALL_CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
            if self._role=='agent' and ALL_CAPS[capability_id][5]!='agent':raise ActionRejected('ACCESS_DENIED')
            if not isinstance(request_id,str) or not request_id:raise ActionRejected('INVALID_ARGUMENT')
            if capability_id in PANEL_CAPS:
                if _panel_ui.check_arguments(PANEL_CAPS[capability_id][0],arguments):raise ActionRejected('INVALID_ARGUMENT')
            else:validate(ALL_CAPS[capability_id][0],arguments)
            key=(self._principal,instance_id,request_id);digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
            if key in h._requests:
                old,result=h._requests[key]
                if old!=digest:raise ActionRejected('REQUEST_CONFLICT')
                return copy.deepcopy(result)
            before=h.operation_id;caller,h._caller=h._caller,self._principal   # the owner guards in the runtime see this session (direct and panel)
            try:result=h.invoke(capability_id,**arguments)
            finally:h._caller=caller
            op=result.get('operation_id')
            if op:h._owners[op]=self._principal
            if capability_id in PANEL_CAPS and h.operation_id and h.operation_id!=before:h._owners.setdefault(h.operation_id,self._principal)   # START on the panel: the presser owns the run
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
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','INSTRUMENT_ERROR','CF_CARD_ERROR','UNKNOWN_REFERENCE','INVALID_REFERENCE','UNREPORTED_SOURCE_CHANNEL','UNKNOWN_CONDITIONS_ACK_REQUIRED','CARBON_PARTITION_REQUIRED','REPORTED_VALUE_OVERRIDE','INCONSISTENT_CARBON_REFERENCE','CARBON_PARTITION_RANGE','PARTITION_ACK_REQUIRED','INITIALIZATION_ONLY','EVIDENCE_ACK_REQUIRED','PLANT_REQUIRED','UNLOAD_REQUIRED','SAMPLE_REQUIRED','RECIPIENT_REQUIRED','POWER_OFF_REQUIRED','COLD_MAINTENANCE_REQUIRED','POWER_INTERLOCK','DILUTION_RANGE','PEAK_WINDOW_RANGE','UNKNOWN_CURVE','UNKNOWN_RECORD','CURVE_CHANNEL_MISMATCH','MATCHED_BLANK_REQUIRED','CURVE_ID_EXISTS','CURVE_STORE_FULL','CALIBRATION_ACK_REQUIRED','REAL_REFERENCE_REQUIRED','SOURCE_CHANNEL_TRANSFER_ACK_REQUIRED','DEGENERATE_CALIBRATION','CALIBRATION_RANGE','PLANT_METHOD_SAMPLE_REQUIRED','GAS_POWER_INTERLOCK','FURNACE_NOT_READY','DRAIN_CANCELLED_RESIDUE_REQUIRED','RESULT_STORE_FULL','SAMPLE_VOLUME_LIMIT','REAGENT_VOLUME_LIMIT','TC_RANGE','IC_RANGE','EVENT_RESOURCE_LIMIT','BUSY','BLANK_COMPOSITION']
 new_immediate={'power.set_mains':['power.mains','power.enabled','power.shutdown_remaining_s','maintenance.phase','measurement.phase','safety.warning','monitor.ready'],'power.shutdown':['power.shutdown_remaining_s','monitor.ready'],'settings.configure':['settings.catalyst','settings.syringe_washes'],'maintenance.run':['maintenance.phase','maintenance.item','maintenance.remaining_s','maintenance.counters','syringe.position_m','truth.ledger'],'maintenance.tc_blank_check':['maintenance.phase','maintenance.item','maintenance.remaining_s','maintenance.counters','truth.ledger'],'maintenance.stop':['maintenance.phase'],'maintenance.reset_counter':['maintenance.counters'],'maintenance.set_counter_limits':['maintenance.counters'],'records.clear':['storage.result_count']}
 new_immediate.update({'fault.inject':['fault.injected','measurement.phase','maintenance.phase'],'fault.clear':['fault.injected']})
 new_immediate.update({'calibration.set_slot':['calibration.curve_slots','calibration.ready'],'sleep.configure':['sleep.settings'],'sleep.restart':['sleep.active','monitor.carrier_flow_m3_s','monitor.baseline_ok','monitor.ready']})
 immediate.update(new_immediate);immediate['power.set']=immediate['power.set']+['power.shutdown_remaining_s','maintenance.phase','monitor.ready','sleep.active'];immediate['power.toggle']=immediate['power.set']
 immediate['calibration.select']=immediate['calibration.select']+['calibration.curve_slots'];immediate['power.set_mains']=immediate['power.set_mains']+['sleep.active'];immediate['power.shutdown']=immediate['power.shutdown']+['sleep.active']
 onstep['sleep.configure']=['sleep.active','furnace.actual_k','furnace.ready','monitor.carrier_flow_m3_s','monitor.furnace_stable','monitor.baseline_ok','monitor.ready'];onstep['sleep.restart']=['furnace.actual_k','furnace.ready','monitor.furnace_stable','monitor.baseline_ok','monitor.ready']
 onstep['power.shutdown']=['power.enabled','power.shutdown_remaining_s','furnace.actual_k','monitor.ready','monitor.furnace_stable'];onstep['maintenance.run']=['maintenance.phase','maintenance.remaining_s','maintenance.ic_reagent_level','maintenance.syringe_zeroed'];onstep['maintenance.tc_blank_check']=['maintenance.phase','maintenance.remaining_s','maintenance.blank_check_area_s']
 for k in ['power.set','power.toggle']:onstep[k]=onstep[k]+['monitor.carrier_flow_m3_s','monitor.furnace_stable','monitor.dehumidifier_k','monitor.baseline_ok','monitor.ready','maintenance.counters']
 onstep['measurement.start']=onstep['measurement.start']+['maintenance.counters']
 for k,(inp,out,c,z,kind,vis,ex,_) in ALL_CAPS.items():
  query=kind=='query';unsupported=k in UNSUPPORTED;async_=k=='measurement.start';new=k in KEY_CAPS or k in PANEL_CAPS;effects=[dict(state_id=state,timing=timing,description_zh={'immediate':'经权限/条件检查的目标、外部液体事件、存储或测量初始化，不推进时钟','on_step':'有限炉温/阀泵/液体与碳转换；NDIR完整采样窗才抽噪声','on_completion':'所有独立aliquot完成后保存全部峰、真实观察换算结果和原方法，完整空白才启用'}[timing]) for timing,mapping in [('immediate',immediate),('on_step',onstep),('on_completion',completion)] for state in mapping.get(k,[])]
  caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.shimadzu_tocl.ShimadzuToclSession.invoke',invocation=f'session.invoke(instance_id="tocl_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if query or unsupported else [dict(description_zh='宿主权限、源quantity/未知partition、液体/试剂容量、门盖与680°C、完整空白、标线及结果容量均原子检查',state_ids=['power.enabled','cover.opened','top_cover.opened','furnace.ready','blank.valid','measurement.phase'],enforced_by='runtime',check='Runtime._invoke / host-assigned principal Session')],state_effects=[] if query or unsupported else effects,observes=[] if unsupported else observes.get(k,[]),model_effects=dict(mode='none',description_zh='独立场景绑定另验；查询不动作',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='各独立原样aliquot完成有限液路/预处理/氧化/NDIR峰，随后保存完整结果；峰超时failed保留残料数据' if async_ else '原子返回，不推进时间',completion_state_ids=['measurement.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','aqueous_sample','carbon_free_diluent_acid','finite_oxygen','ndir','result_store_2000','curve_store_60','request_cache'],partial_failure_zh='取消/开门/断电及峰超时保留消费量和部分峰；未完成无浓度，原历史按主体保护；存储满在消费前拒绝'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in (NEW_ERRORS[k] if new else errors)]+([dict(code='NDIR_BASELINE_TIMEOUT',meaning_zh='观察峰未按方法回基线；保留碳/体积仓与已有峰',side_effects='partial')] if async_ else []),example_arguments=copy.deepcopy(ex),verification=[],limitations=NEW_LIMITS[k] if new else LIMITATIONS,source_ids=['manufacturer_service','manufacturer_startup_note','manufacturer_catalog','manufacturer_primary','simulation_assumption'] if new else ['manufacturer_primary','simulation_assumption'])
 mech=dict(component_ids=['sample_inlet','syringe_plunger','selector_valve','ic_reactor','ic_pump_rotor','combustion_furnace','ndir_detector'],state_ids=list(slots),meaning_zh='水样独立TC/IC/NPOC进样、酸化吹扫与催化碳转换、NDIR观察标定和完整容量存储',implementation='lumped',equations=['Original aliquot volume=final_injection_volume/dilution; diluent=final_injection_volume-original_volume; acid volume separately conserved.','TC=C_IC+C_NPOC+C_POC after conversion; IC from separately acidified unburned aliquot; pairedTOC=observedTC-observedIC, signed.','NPOC pretreatment removes finite IC and purgeableOC to purge warehouse; remaining carbon feeds catalytic detector, so incomplete stripping can bias NPOC.','First-order conversion/release limited by external oxygen/carbonate-equivalent proton budgets; unconverted carbon retained.','NDIR_mean=baseline+gain_s_kg*released_carbon/window+host_noise; peak_area=sum((NDIR-observed_blank)*window).','Fit area=slope*source_concentration*injection_volume/dilution+intercept; observed_concentration=(area-intercept)/slope/original_aliquot_volume.','Carbon registered=sum(sample,syringe,reactor,CO2buffer,tail,detected_exhaust,purged,liquid_waste,removed), separately for IC/NPOC/POC origins.','Sample+diluent+acid input volume=current liquids+waste+removed; net furnace heat=C*(T-T_initial), no full apparatus energy claim.','Complete result store capacity2000; calibration curve capacity60; no eviction, pure indexed reads and explicit result removal.'],parameters={},source_ids=['manufacturer_primary','simulation_assumption'],limitations=LIMITATIONS)
 spec=importlib.util.spec_from_file_location('_tocl_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'aqueous_carbon_analysis':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'；非原厂标定。'
 mech['parameters']={k.lower():v for k,v in mech['parameters'].items()};return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'aqueous_carbon_analysis':mech})

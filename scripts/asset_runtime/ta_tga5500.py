"""CHEM132 finite-inventory TGA curve-anchored engineering proxy; observation and truth separated.

Front panel (panel.switch / panel.touch / panel.capture / panel.frame): the One-Touch-Away app-style touch screen and the power switch
(panel_ui.json beside the contract, approximate: the Discovery screen pages are not documented) run by the generic engine _panel_ui.py on top
of the capabilities below (no second copy of the logic)."""
from pathlib import Path
import copy,json,math,importlib.util,bisect,hashlib,os,tempfile,weakref
import numpy as np
PROTOCOL_VERSION='1.0.0';ASSET_VERSION='1.2.1';SLUG='ta_tga5500';CATALOG_ID='CHEM-132';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch03/t1_thermal'/SLUG
DT=.1;R_GAS=8.31446261815324
class ActionRejected(ValueError):pass
def number(unit='1',lo=None,hi=None):
 s={'type':'number','x-unit':unit}
 if lo is not None:s['minimum']=lo
 if hi is not None:s['maximum']=hi
 return s
def integer(lo=0,hi=1000000):return dict(type='integer',minimum=lo,maximum=hi,**{'x-unit':'1'})
def obj(properties):return dict(type='object',properties=properties,required=list(properties),additionalProperties=False)
def array(items,minimum=0,maximum=10000):return dict(type='array',items=items,minItems=minimum,maxItems=maximum)
def enum(items):return dict(type='string',enum=list(items))
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
BOOL={'type':'boolean'};TEXT={'type':'string'};PHASE=enum(['idle','running','succeeded','failed','cancelled'])
def validate(s,v):
 if 'anyOf' in s:
  for option in s['anyOf']:
   try:validate(option,v);return
   except ActionRejected:pass
  raise ActionRejected('INVALID_ARGUMENT')
 kind=s.get('type')
 if kind in ('number','integer'):
  if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or kind=='integer' and type(v) is not int or not s.get('minimum',-math.inf)<=v<=s.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
 elif kind=='boolean' and type(v) is not bool:raise ActionRejected('INVALID_ARGUMENT')
 elif kind=='string' and not isinstance(v,str):raise ActionRejected('INVALID_ARGUMENT')
 elif kind=='null' and v is not None:raise ActionRejected('INVALID_ARGUMENT')
 elif kind=='object':
  if not isinstance(v,dict) or set(v)!=set(s['properties']):raise ActionRejected('INVALID_ARGUMENT')
  for key,value in v.items():validate(s['properties'][key],value)
 elif kind=='array':
  if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
  for value in v:validate(s['items'],value)
 if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')
_LIBRARY_CACHE={}
def library():
 """Parsed measured-reference library, cached per file (mtime, size); callers deep-copy what they keep."""
 p=ASSET/'tga_library.json'
 if not p.exists():return {'substances':{}}
 st=p.stat();key=(str(p),st.st_mtime_ns,st.st_size)
 if _LIBRARY_CACHE.get('key')!=key:_LIBRARY_CACHE.clear();_LIBRARY_CACHE.update(key=key,value=json.loads(p.read_text()))
 return _LIBRARY_CACHE['value']
def runtime_reference_ids():
 """Every library record whose real measured curve the sample model can load (runtime_usable)."""
 return sorted(k for k,r in library()['substances'].items() if r.get('runtime_usable'))

M_O2=.031998;PAN_LIMITS={'ceramic':1473.15,'platinum':1273.15,'aluminum':873.15}
PLANT_EXAMPLE=dict(accept_engineering=True,ambient_k=298.15,furnace_capacity_j_k=2.,pan_capacity_j_k=.018,pan_mass_kg=5e-5,sample_conductance_w_k=.05,ambient_conductance_w_k=.02,forced_cooling_conductance_w_k=.15,controller_gain_w_k=25.,heater_max_w=100.,safe_access_temperature_k=313.15,sensor_tau_s=.1,mass_gain=1.,mass_offset_kg=0.,temperature_gain=1.,temperature_offset_k=0.,chamber_volume_m3=2e-6,chamber_reference_temperature_k=298.15,chamber_reference_pressure_pa=101325.,furnace_speed_m_s=.055,carousel_speed_rad_s=1.,punch_speed_m_s=.008)
PLANT=obj({'accept_engineering':BOOL,'ambient_k':number('K',283.15,313.15),'furnace_capacity_j_k':number('J/K',1e-5,1e4),'pan_capacity_j_k':number('J/K',1e-7,100),'pan_mass_kg':number('kg',1e-9,.01),'sample_conductance_w_k':number('W/K',1e-7,1000),'ambient_conductance_w_k':number('W/K',0,1000),'forced_cooling_conductance_w_k':number('W/K',0,1000),'controller_gain_w_k':number('W/K',1e-5,1e5),'heater_max_w':number('W',1e-5,1e5),'safe_access_temperature_k':number('K',283.15,373.15),'sensor_tau_s':number('s',1e-5,100),'mass_gain':number('1',.01,100),'mass_offset_kg':number('kg',-.01,.01),'temperature_gain':number('1',.5,1.5),'temperature_offset_k':number('K',-20,20),'chamber_volume_m3':number('m^3',1e-12,.1),'chamber_reference_temperature_k':number('K',200,500),'chamber_reference_pressure_pa':number('Pa',10000,1e6),'furnace_speed_m_s':number('m/s',1e-4,.1),'carousel_speed_rad_s':number('rad/s',.01,10),'punch_speed_m_s':number('m/s',1e-4,.03)})
SAMPLE_EXAMPLE=dict(reference_id='pending_verified_tga_curve',mass_kg=1e-5,pan_material='ceramic',heat_capacity_j_kg_k=1000.,loss_relaxation_s=1.,loss_heat_j_kg=0.,oxidation_capacity_fraction=0.,oxidation_rate_s_inv=.1,oxidation_activation_j_mol=50000.,oxidation_reference_temperature_k=600.,oxidation_heat_j_kg=0.,accept_engineering=True,accept_curve_projection=True,accept_condition_transfer=True,accept_cold_extension=True)
SAMPLE=obj({'reference_id':TEXT,'mass_kg':number('kg',1e-10,.001),'pan_material':enum(PAN_LIMITS),'heat_capacity_j_kg_k':number('J/(kg*K)',1,1e5),'loss_relaxation_s':number('s',1e-6,1e5),'loss_heat_j_kg':number('J/kg',-1e7,1e7),'oxidation_capacity_fraction':number('1',0,10),'oxidation_rate_s_inv':number('1/s',0,1e4),'oxidation_activation_j_mol':number('J/mol',0,5e5),'oxidation_reference_temperature_k':number('K',283.15,1473.15),'oxidation_heat_j_kg':number('J/kg',-1e7,1e7),'accept_engineering':BOOL,'accept_curve_projection':BOOL,'accept_condition_transfer':BOOL,'accept_cold_extension':BOOL})
GAS_EXAMPLE=dict(gas='N2',amount_mol=.1,reference_temperature_k=298.15,reference_pressure_pa=101325.,accept_reference_conditions=True)
GAS=obj({'gas':enum(['N2','O2']),'amount_mol':number('mol',1e-18,1000),'reference_temperature_k':number('K',200,500),'reference_pressure_pa':number('Pa',10000,1e7),'accept_reference_conditions':BOOL})
CALIBRATION_EXAMPLE=dict(mass_gain_correction=1.,mass_zero_kg=0.,temperature_gain_correction=1.,temperature_zero_k=0.)
CALIBRATION=obj({'mass_gain_correction':number('1',.01,100),'mass_zero_kg':number('kg',-.01,.01),'temperature_gain_correction':number('1',.1,10),'temperature_zero_k':number('K',-100,100)})
SEGMENT=obj({'target_k':number('K',283.15,1473.15),'rate_k_s':number('K/s',.01/60,500/60),'hold_s':number('s',0,100000),'gas':enum(['N2','O2'])})
METHOD_EXAMPLE=dict(start_temperature_k=298.15,segments=[dict(target_k=773.15,rate_k_s=5.,hold_s=10.,gas='N2')],sample_interval_s=.1,start_tolerance_k=.5,timeout_s=10000.)
METHOD=obj({'start_temperature_k':number('K',283.15,1473.15),'segments':array(SEGMENT,1,100),'sample_interval_s':number('s',.1,60),'start_tolerance_k':number('K',.001,10),'timeout_s':number('s',.1,200000)})
ROW=obj({'time_s':number('s',0),'elapsed_s':number('s',0),'segment_index':integer(0,99),'stage':enum(['ramp','hold']),'target_k':number('K',283.15,1473.15),'furnace_k':nullable(number('K')),'sample_k':nullable(number('K')),'mass_kg':nullable(number('kg')),'sample_gas_target':enum(['N2','O2']),'oxygen_fraction':nullable(number('1',0,1))})
RESULT=obj({'operation_id':nullable(TEXT),'phase':PHASE,'elapsed_s':number('s',0),'rows':array(ROW,0,2000000),'reference_id':nullable(TEXT),'template_source':nullable(TEXT),'projection_json':nullable(TEXT),'method_json':nullable(TEXT),'calibration_json':nullable(TEXT),'evidence':TEXT,'error_code':nullable(TEXT)})
STAGE_RESULT=obj({'start_time_s':number('s',0),'end_time_s':number('s',0),'mass_change_kg':number('kg'),'loss_fraction':number('1'),'peak_loss_rate_kg_s':number('kg/s'),'peak_temperature_k':number('K')})
ANALYSIS=obj({'initial_observed_mass_kg':number('kg'),'final_observed_mass_kg':number('kg'),'residue_fraction':number('1'),'net_loss_fraction':number('1'),'stages':array(STAGE_RESULT,0,10000),'evidence':TEXT})
def analyze_mass(time_s,temperature_k,mass_kg,baseline_kg,initial_mass_kg,stage_boundaries):
 t,T,m,b=map(lambda x:np.asarray(x,float),(time_s,temperature_k,mass_kg,baseline_kg))
 if t.ndim!=1 or len(t)<3 or any(x.shape!=t.shape or not np.all(np.isfinite(x)) for x in [T,m,b]) or not np.all(np.isfinite(t)) or np.any(np.diff(t)<=0) or initial_mass_kg<=0:raise ActionRejected('ANALYSIS_DATA')
 if len(stage_boundaries)<2 or stage_boundaries[0]!=0 or stage_boundaries[-1]!=len(t)-1 or any(type(x) is not int for x in stage_boundaries) or any(a>=b for a,b in zip(stage_boundaries,stage_boundaries[1:])):raise ActionRejected('ANALYSIS_BOUNDARIES')
 q=m-b;stages=[]
 for a,z in zip(stage_boundaries,stage_boundaries[1:]):
  rates=-np.diff(q[a:z+1])/np.diff(t[a:z+1]);i=int(np.argmax(rates));stages.append(dict(start_time_s=float(t[a]),end_time_s=float(t[z]),mass_change_kg=float(q[z]-q[a]),loss_fraction=float((q[a]-q[z])/initial_mass_kg),peak_loss_rate_kg_s=float(rates[i]),peak_temperature_k=float((T[a+i]+T[a+i+1])/2)))
 return dict(initial_observed_mass_kg=float(q[0]),final_observed_mass_kg=float(q[-1]),residue_fraction=float(q[-1]/initial_mass_kg),net_loss_fraction=float((q[0]-q[-1])/initial_mass_kg),stages=stages,evidence='External observed mass minus supplied blank, divided by supplied independently weighed mass; explicit index windows, finite-difference DTG midpoint temperatures. Gain/zero uncertainty and incomplete windows remain. No sample truth, automatic chemical identity, Arrhenius fit or certified decomposition temperature.')

def mix_chamber(n2,o2,feed,amount,capacity):
 """Exact finite CSTR exchange after explicit filling of an oxygen-consumption deficit."""
 if min(n2,o2,amount)<0 or capacity<=0 or n2+o2>capacity*(1+1e-10) or feed not in ['N2','O2']:raise ActionRejected('MIXING_DOMAIN')
 initial=[n2,o2];i=0 if feed=='N2' else 1;fill=min(amount,max(0.,capacity-n2-o2));v=initial.copy();v[i]+=fill;rem=amount-fill
 if rem>0:
  decay=math.exp(-rem/capacity);v=[x*decay for x in v];v[i]+=capacity*(-math.expm1(-rem/capacity))
 used=[initial[j]+(amount if j==i else 0)-v[j] for j in range(2)];return [float(x) for x in v],[max(0.,float(x)) for x in used]

def reference_fraction(temperature,model):
 x=model['temperature'];y=model['mass_fraction'];i=bisect.bisect_right(x,temperature)
 if i==0:return y[0]
 if i==len(x):return y[-1]
 return y[i-1]+(y[i]-y[i-1])*(temperature-x[i-1])/(x[i]-x[i-1])

def prepare_reference(ref,accepted):
 if not ref.get('runtime_usable'):raise ActionRejected('INVALID_REFERENCE')
 try:T=np.asarray(ref['values']['temperature_k'],float);m=np.asarray(ref['values']['mass_fraction'],float)
 except (KeyError,TypeError,ValueError):raise ActionRejected('INVALID_REFERENCE')
 if T.ndim!=1 or len(T)<3 or T.shape!=m.shape or not np.all(np.isfinite(T)) or not np.all(np.isfinite(m)) or m[0]<=0:raise ActionRejected('INVALID_REFERENCE')
 order=np.argsort(T,kind='stable');u,first=np.unique(T[order],return_index=True);raw=m[order][first]/m[0];projected=np.minimum.accumulate(np.clip(raw,0.,1.));stats=dict(original_points=len(T),usable_temperature_points=len(u),reordered_points=int(np.sum(order!=np.arange(len(T)))),duplicate_temperatures_removed=len(T)-len(u),normalized_by_first_mass_fraction=float(m[0]),clipped_points=int(np.sum((raw<0)|(raw>1))),monotonic_projection_points=int(np.sum(projected!=np.clip(raw,0,1))),maximum_fraction_adjustment=float(np.max(abs(projected-raw))),transformation='stable ascending temperature, first source sample at duplicate temperatures, divide by original initial fraction, clip to [0,1], nonincreasing mass projection; raw source retained')
 if not accepted:raise ActionRejected('CURVE_PROJECTION_NOT_ACCEPTED')
 return dict(temperature=u.tolist(),mass_fraction=projected.tolist(),stats=stats)

CAPS={};SLOTS={}
def cap(k,i,o,c,z,kind='device_command',visibility='agent',example=None):CAPS[k]=dict(input_schema=i,output_schema=o,component_ids=c,function_zh=z,kind=kind,visibility=visibility,example_arguments=copy.deepcopy(example or {}),effects=[])
def slot(k,s,u,z,origin='derived',visibility='agent',component='controller'):SLOTS[k]=dict(schema=s,unit=u,meaning=z,origin=origin,visibility=visibility,component=component,write=None)
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller','furnace','balance'],'显式热量/气体/天平工程参数','configuration','evaluator',PLANT_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_pan'],'冷态装载真实曲线锚定有限样品','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.remove',obj({}),obj({'removed':BOOL}),['sample_pan'],'冷态回收剩余残渣及显热','configuration','evaluator')
cap('gas.load',GAS,obj({'loaded':BOOL}),['gas_manifold'],'有限N2/O2来源，明确体积流量参考温压','configuration','evaluator',GAS_EXAMPLE)
cap('gas.unload',obj({'gas':enum(['N2','O2'])}),obj({'unloaded':BOOL}),['gas_manifold'],'关闭支路后回收剩余气体','configuration','evaluator',{'gas':'N2'})
FLOW=number('m^3/s',0,500e-6/60);FLOW['x-reference-conditions']='Corresponding finite gas.load explicitly supplied reference absolute temperature and pressure; OEM does not define STP.'
cap('gas.set_flow',obj({'sample_flow_m3_s':FLOW,'balance_flow_m3_s':FLOW}),obj({'configured':BOOL}),['purge_inlet','purge_inlet_b'],'分离样品与天平N2保护参考流量',example={'sample_flow_m3_s':25e-6/60,'balance_flow_m3_s':10e-6/60})
cap('gas.select',obj({'gas':enum(['N2','O2'])}),obj({'gas':enum(['N2','O2'])}),['gas_manifold'],'改变样品气体目标，实际腔浓度由有限混合推进',example={'gas':'N2'})
cap('gas.set_open',obj({'opened':BOOL}),obj({'opened':BOOL}),['gas_manifold'],'同时启闭样品与天平保护支路',example={'opened':True})
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_switch','controller'],'电源和温控使能',example={'enabled':True})
cap('furnace.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['furnace'],'冷态有限速下降炉体，非瞬移',example={'opened':False})
cap('carousel.index',obj({'position':integer(1,25)}),obj({'position':integer(1,25)}),['carousel'],'冷态25位转盘最短路径工程运动',example={'position':2})
cap('punch.set',obj({'pressed':BOOL}),obj({'pressed':BOOL}),['pan_punch'],'仅第1演示位冷态冲头运动，不模拟盖膜破裂',example={'pressed':True})
cap('thermal.set_temperature',obj({'temperature_k':number('K',283.15,1473.15),'rate_k_s':number('K/s',.01/60,500/60)}),obj({'target_k':number('K',283.15,1473.15)}),['furnace'],'有限速目标温程，实际温度有限热容量',example={'temperature_k':400.,'rate_k_s':1.})
cap('thermal.disable',obj({}),obj({'enabled':BOOL}),['furnace'],'停加热保留余热和真实质量状态')
cap('thermal.set_cooling',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['furnace'],'工程强制空气冷却至环境热汇',example={'enabled':True})
cap('balance.set_range',obj({'range':enum(['low','high'])}),obj({'range':enum(['low','high'])}),['balance'],'100mg或1000mg量程，分辨率不等于精度',example={'range':'low'})
cap('sensor.calibrate',CALIBRATION,CALIBRATION,['balance','controller'],'外部质量/温度增益零点，不读取真值','configuration',example=CALIBRATION_EXAMPLE)
cap('balance.tare',obj({}),obj({'zeroed':BOOL}),['balance'],'空盘稳定观测归零')
cap('method.configure',METHOD,METHOD,['controller'],'线性分段目标、保温和有限气氛置换','configuration',example=METHOD_EXAMPLE)
cap('acquisition.start',obj({}),obj({'operation_id':TEXT}),['controller','furnace','sample_pan','balance'],'异步真实质量变化/热滞后曲线锚定工程过程')
cap('acquisition.stop',obj({}),obj({'phase':enum(['cancelled'])}),['controller','furnace'],'停止目标程序，保留余热/残渣/气体')
cap('acquisition.result',obj({}),RESULT,['controller'],'冻结观测历史，仅异步owner','query')
cap('reference.query',obj({'reference_id':TEXT}),obj({'reference_json':TEXT,'template_source':TEXT}),['controller'],'未经运行投影的真实源曲线','query',example={'reference_id':SAMPLE_EXAMPLE['reference_id']})
cap('analysis.mass_stages',obj({'time_s':array(number('s',0),3,1000000),'temperature_k':array(number('K'),3,1000000),'mass_kg':array(number('kg'),3,1000000),'baseline_kg':array(number('kg'),3,1000000),'initial_mass_kg':number('kg',1e-12,1),'stage_boundaries':array(integer(0,999999),2,10001)}),ANALYSIS,['controller'],'外部观测失重区间、残留率与有限差分DTG','analysis',example={'time_s':[0.,1.,2.],'temperature_k':[300.,310.,320.],'mass_kg':[1e-5,8e-6,7e-6],'baseline_kg':[0.,0.,0.],'initial_mass_kg':1e-5,'stage_boundaries':[0,2]})
LEDGER_UNITS={k:u for k,u in [('sample_loaded_kg','kg'),('sample_present_kg','kg'),('sample_emitted_kg','kg'),('oxygen_uptake_kg','kg'),('sample_recovered_kg','kg'),('sample_error_kg','kg'),('heater_input_j','J'),('reaction_heat_j','J'),('ambient_loss_j','J'),('cooling_removed_j','J'),('emitted_sensible_j','J'),('recovered_sensible_j','J'),('furnace_stored_j','J'),('sample_stored_j','J'),('energy_error_j','J')]}
for gas in ['n2','o2']:
 for name in ['loaded','remaining','initial_chamber','chamber','exhausted','recovered','consumed','error']:LEDGER_UNITS[gas+'_'+name+'_mol']='mol'
LEDGER=obj({k:number(u) for k,u in LEDGER_UNITS.items()})
cap('ledger.read',obj({}),LEDGER,['controller'],'样品/N2/O2/热量有限守恒账本','query','evaluator')
cap('sim.step',obj({'dt_s':number('s',0,3600)}),obj({'time_s':number('s',0)}),['controller'],'唯一宿主时钟','simulation','evaluator',{'dt_s':.1})
cap('emergency.stop',obj({}),obj({'latched':BOOL}),['controller'],'软件停机保留物料和余热')
cap('emergency.reset',obj({}),obj({'latched':BOOL}),['controller'],'冷态复位，不自动重启')
cap('method.advance',obj({}),obj({'segment_index':integer(0,100)}),['controller'],'运行中跳到方法下一段（从当前程序温度开始，切换到该段气体）；已是最后一段则正常结束方法')  # touch screen: Advance method segments
# Autosampler pan transfer (brochure p.12: loading / unloading pans; Q500 guide p.16 LOAD/UNLOAD, p.17 PAN TO FRONT; 25 positions p.29): pans
# wait on the carousel outside the furnace; a load / unload turns the carousel to the position and the pan enters / leaves the balance through the
# same steps as sample.load / sample.remove when the carousel is there.  A pan returned after it was heated holds its residue and is not reloaded.
CAROUSEL_N=25;CPOS=integer(1,CAROUSEL_N)
cap('tray.place',obj({'position':CPOS,'sample':SAMPLE}),obj({'placed':BOOL}),['carousel'],'手工把装好样品的盘放到转盘位置1-25（炉外，室温）','configuration','evaluator',{'position':2,'sample':SAMPLE_EXAMPLE})
cap('tray.remove',obj({'position':CPOS}),obj({'removed':BOOL}),['carousel'],'手工从转盘位置取走样品盘（含已测残渣）','configuration','evaluator',{'position':2})
cap('autosampler.load',obj({'position':CPOS}),obj({'accepted':BOOL}),['carousel','balance_hook','sample_pan'],'自动进样器LOAD：转盘转到所选位置后把样品盘挂上天平（炉体打开、天平空、冷态）',example={'position':2})
cap('autosampler.unload',obj({'position':CPOS}),obj({'accepted':BOOL}),['carousel','balance_hook','sample_pan'],'自动进样器UNLOAD：转盘转到所选空位后把天平上的样品盘（含残渣）放回',example={'position':2})
OWNER_GUARDED=('acquisition.stop','acquisition.result','method.advance')  # owner-only while another agent's run is the current one
RUN_GUARDED=('thermal.set_cooling',)  # accepted while a run is active (air cool acts on the furnace), so owner-only while another agent's run is running
UNSUPPORTED=['autosampler.sequence','hires.native','mtga.native','tga_ms.acquire','tga_ftir.acquire','curie.calibrate','kinetics.arrhenius_identify','noise.empirical','accuracy.certification','hardware.connect','pan.sealed_pressure','gas.pyrolysis_composition']
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'明确未实现：'+k)
for k,c,z in [('plant.configured','controller','模型配置'),('power.enabled','power_switch','电源'),('sample.present','sample_pan','有限样品存在'),('gas.opened','gas_manifold','两吹扫支路开启'),('thermal.enabled','furnace','主动加热'),('cooling.enabled','furnace','强制冷却'),('emergency.latched','controller','软件停机锁存'),('furnace.opened','furnace','炉体完全下降'),('furnace.moving','furnace','炉体运动'),('carousel.moving','carousel','转盘运动'),('punch.moving','pan_punch','冲头运动')]:slot(k,BOOL,'1',z,component=c)
for k,hi,c in [('furnace.lower_m',.11,'furnace'),('furnace.target_m',.11,'furnace'),('punch.press_m',.016,'pan_punch'),('punch.target_m',.016,'pan_punch')]:slot(k,number('m',0,hi),'m','工程运动位置或目标','setpoint' if 'target' in k else 'scene',component=c)
slot('carousel.angle_rad',number('rad',-math.pi,math.pi),'rad','25位转盘实际角','scene',component='carousel');slot('carousel.target_index',integer(1,25),'1','转盘目标位置','setpoint',component='carousel');slot('power.angle_rad',number('rad',0,.28),'rad','开关姿态','scene',component='power_switch')
slot('gas.selected',enum(['N2','O2']),'1','样品气体目标，不等于腔气氛','setpoint',component='gas_manifold')
for k in ['gas.sample_flow_m3_s','gas.balance_flow_m3_s']:slot(k,FLOW,'m^3/s','明确气源参考温压的支路流量','setpoint',component='gas_manifold')
slot('balance.range',enum(['low','high']),'1','样品净质量量程','setpoint',component='balance')
for k in ['thermal.target_k','thermal.final_target_k']:slot(k,number('K',283.15,1473.15),'K','温度程序目标','setpoint',component='furnace')
for k,u in [('furnace.indicated_k','K'),('sample.indicated_k','K'),('mass.indicated_kg','kg'),('gas.oxygen_fraction','1')]:slot(k,nullable(number(u)),u,'独立工程传感器观测',component='balance' if k.startswith('mass') else 'controller')
for k,u in [('truth.furnace_k','K'),('truth.sample_k','K'),('truth.sample_mass_kg','kg'),('truth.loss_kg','kg'),('truth.oxygen_uptake_kg','kg'),('truth.n2_mol','mol'),('truth.o2_mol','mol'),('truth.chamber_n2_mol','mol'),('truth.chamber_o2_mol','mol'),('truth.chamber_pressure_pa','Pa'),('truth.heater_w','W')]:slot(k,number(u),u,'过程真值，仅评估器','process_truth','evaluator')
slot('clock.time_s',number('s',0),'s','唯一宿主时钟');slot('acquisition.phase',PHASE,'1','异步状态');slot('acquisition.elapsed_s',number('s',0),'s','实际运行时间');slot('acquisition.points',integer(0,2000000),'1','观测点');slot('acquisition.segment_index',integer(0,99),'1','当前温度段');slot('acquisition.stage',enum(['ramp','hold']),'1','当前阶段');slot('panel.temperature_text',TEXT,'1','断电空；温度粗显示°C一位小数');slot('panel.mass_text',TEXT,'1','断电空；质量mg六位小数')
SAMPLE_EXAMPLE['reference_id']='fsri_abs_n2_10k_r1';CAPS['sample.load']['example_arguments']=copy.deepcopy(SAMPLE_EXAMPLE);CAPS['reference.query']['example_arguments']={'reference_id':SAMPLE_EXAMPLE['reference_id']}
slot('model.fault',nullable(TEXT),'1','超工程温域/源温域后停止未校准反应，已产生物料不复原')
slot('autosampler.tray_occupied',array(BOOL,CAROUSEL_N,CAROUSEL_N),'1','转盘25个位置是否有样品盘',component='carousel');slot('autosampler.balance_origin',nullable(CPOS),'1','天平上样品盘来自的转盘位置（手工挂上时为空）',component='sample_pan');slot('autosampler.transfer',nullable(enum(['load','unload'])),'1','正在进行的取放',component='carousel')
def _panel_engine():
 """The shared front-panel engine (_panel_ui.py beside this file): as a package module, else loaded from its file path."""
 try:
  from . import _panel_ui as engine;return engine
 except ImportError:
  import sys
  if '_chemsim_panel_ui' not in sys.modules:
   s=importlib.util.spec_from_file_location('_chemsim_panel_ui',Path(__file__).with_name('_panel_ui.py'));sys.modules['_chemsim_panel_ui']=importlib.util.module_from_spec(s);s.loader.exec_module(sys.modules['_chemsim_panel_ui'])
  return sys.modules['_chemsim_panel_ui']
_panel_ui=_panel_engine()
PANEL_SPEC=None if os.environ.get('TA_PANEL_BUILD') else _panel_ui.load_spec(ASSET/'panel_ui.json',capabilities=set(CAPS))  # TA_PANEL_BUILD: spec being written
PANEL_CAPS=_panel_ui.capability_definitions(PANEL_SPEC) if PANEL_SPEC else {}  # panel.* kept out of CAPS: CAPS stays the instrument's own command set
ALL_CAPS=dict(CAPS,**PANEL_CAPS);_PANELS=weakref.WeakKeyDictionary()
def validate_arguments(k,v):
 """Capability arguments: the instrument's own validator; panel.* schemas (optional keys) by the engine's checker."""
 if k in PANEL_CAPS:
  if not isinstance(v,dict) or _panel_ui.check_arguments(PANEL_CAPS[k]['input_schema'],v):raise ActionRejected('INVALID_ARGUMENT')
 else:validate(CAPS[k]['input_schema'],v)

class TaTga5500Runtime:
 def __init__(self,instance_id='tga5500_01'):
  self.instance_id=instance_id;self.plant=None;self.sample=None;self.reference=None;self.model=None;self.power=False;self.furnace_lower=.11;self.furnace_target=.11;self.carousel_angle=0.;self.carousel_target_angle=0.;self.carousel_index=1;self.punch=0.;self.punch_target=0.;self.thermal_enabled=False;self.cooling=False;self.emergency=False;self.model_fault=None;self.gas_open=False;self.selected='N2';self.sample_flow=0.;self.balance_flow=0.;self.gases={};self.gas_remaining={'N2':0.,'O2':0.};self.gas_loaded={'N2':0.,'O2':0.};self.gas_recovered={'N2':0.,'O2':0.};self.gas_exhausted={'N2':0.,'O2':0.};self.chamber=[0.,0.];self.initial_chamber=[0.,0.];self.capacity=0.;self.furnace=298.15;self.sample_temperature=298.15;self.sample_energy=0.;self.target=298.15;self.final_target=298.15;self.rate=1.;self.heater_power=0.;self.obs_mass=0.;self.obs_oxygen=0.;self.balance_range='low';self.mass_loaded=0.;self.mass_recovered=0.;self.mass_emitted=0.;self.oxygen_uptake=0.;self.loss=0.;self.uptake=0.;self.heater_input=0.;self.reaction_heat=0.;self.ambient_loss=0.;self.cooling_removed=0.;self.emitted_sensible=0.;self.recovered_sensible=0.;self.method=copy.deepcopy(METHOD_EXAMPLE);self.calibration=copy.deepcopy(CALIBRATION_EXAMPLE);self.phase='idle';self.elapsed=0.;self.segment_index=0;self.stage='ramp';self.rows=[];self.operation_id=None;self.operation_method=None;self.operation_calibration=None;self.operation_reference=None;self.operation_source=None;self.operation_projection=None;self.operation_start=0.;self.error=None;self.records={};self.schedule=[];self.duration=0.;self._counter=0;self.time_s=0.;self._ticks=0;self._remainder=0.;self.revision=0;self._requests={};self._owners={};self._cancel_operations={};self._principals={'agent':'agent','evaluator':'evaluator'};self.power_epoch=0;self.panel_capture_dir=None;self.tray=[None]*CAROUSEL_N;self.balance_origin=None;self.transfer=None;self.pan_run=False;self._caller=None
 def register_principal(self,principal_id,visibility='agent'):
  if not isinstance(principal_id,str) or not principal_id or principal_id in self._principals or visibility not in ('agent','debug','evaluator'):raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='agent'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return TaTga5500Session(self,principal_id)
 def describe(self):return export_ta_tga5500_contract_metadata()
 def _mass(self):return self.sample['mass_kg']-self.loss+self.uptake if self.sample else 0.
 def _cp(self,m=None):return self.plant['pan_capacity_j_k']+((self._mass() if m is None else m)*self.sample['heat_capacity_j_kg_k'] if self.sample else 0.)
 def _pan_limit(self):return PAN_LIMITS[self.sample['pan_material']] if self.sample else 1473.15
 def _carousel_target(self):return ((-2*math.pi*(self.carousel_index-1)/25+math.pi)%(2*math.pi))-math.pi
 def _angle_error(self):return (self.carousel_target_angle-self.carousel_angle+math.pi)%(2*math.pi)-math.pi
 def _moving(self):return bool(abs(self.furnace_target-self.furnace_lower)>1e-12 or abs(self._angle_error())>1e-12 or abs(self.punch_target-self.punch)>1e-12)
 def _cold(self):return max(self.furnace,self.sample_temperature)<=self.plant['safe_access_temperature_k']
 def _mechanical(self):
  if self.phase=='running':raise ActionRejected('BUSY')
  if not self._cold():raise ActionRejected('HOT_CELL')
  if self.thermal_enabled:raise ActionRejected('THERMAL_CONTROL_ACTIVE')
  if self._moving():raise ActionRejected('MECHANISM_MOVING')
 def _domain(self,t):
  if not self.plant['ambient_k']<=t<=self._pan_limit():raise ActionRejected('PAN_TEMPERATURE_RANGE')
  if self.sample:
   if t>self.model['temperature'][-1]+1e-8:raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
   if t<self.model['temperature'][0]-1e-8 and not self.sample['accept_cold_extension']:raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
 def _source_conditions(self,gas,rate=None):
  if not self.sample or self.sample['accept_condition_transfer']:return
  c=self.reference.get('conditions',{});sourcegas={'N2':'N2','nitrogen':'N2','O2':'O2','oxygen':'O2'}.get(c.get('gas'));r=c.get('heating_rate_k_s')
  matches=sourcegas==gas and c.get('pan_material')==self.sample['pan_material'] and isinstance(c.get('initial_mass_kg'),(int,float)) and math.isclose(self.sample['mass_kg'],c['initial_mass_kg'],rel_tol=1e-6)
  if rate is not None:matches=matches and isinstance(r,(int,float)) and math.isclose(rate,r,rel_tol=1e-6,abs_tol=1e-10)
  # Unknown gas reference conditions or ambiguous source branch totals cannot establish a matched experiment.
  for key,current in [('sample_purge_flow_m3_s',self.sample_flow),('balance_purge_flow_m3_s',self.balance_flow)]:matches=matches and isinstance(c.get(key),(int,float)) and math.isclose(c[key],current,rel_tol=1e-6,abs_tol=1e-15)
  stock=self.gases.get(gas,{})
  for key,target in [('flow_reference_temperature_k','reference_temperature_k'),('flow_reference_pressure_pa','reference_pressure_pa')]:matches=matches and isinstance(c.get(key),(int,float)) and target in stock and math.isclose(c[key],stock[target],rel_tol=1e-6)
  if not matches:raise ActionRejected('CONDITION_TRANSFER_NOT_ACCEPTED')
 def _advance_segment(self):
  """End the running segment now and continue with the next one from the current program temperature (the method ends after the last)."""
  e=self.elapsed;i=self.segment_index;segs=self.method['segments']
  base=next((lo+(hi-lo)*max(0.,min(1.,(e-a)/(b-a))) for a,b,lo,hi,j,stage,gas in self.schedule if e<b-1e-10),self.schedule[-1][3])
  new=[x for x in self.schedule if x[1]<=e+1e-10]+[(a,e,lo,base,j,stage,gas) for a,b,lo,hi,j,stage,gas in self.schedule if a<e-1e-10 and e<b-1e-10]
  t=e;previous=base
  for j,seg in enumerate(segs[i+1:],i+1):
   ramp=abs(seg['target_k']-previous)/seg['rate_k_s']
   if ramp>0:new.append((t,t+ramp,previous,seg['target_k'],j,'ramp',seg['gas']));t+=ramp
   if seg['hold_s']>0:new.append((t,t+seg['hold_s'],seg['target_k'],seg['target_k'],j,'hold',seg['gas']));t+=seg['hold_s']
   previous=seg['target_k']
  if t<=e+1e-12:self.duration=e;self._finish('succeeded');return i
  nxt=next(x for x in new if x[0]>=e-1e-10);self.schedule=new;self.duration=t;self.segment_index=nxt[4];self.stage=nxt[5];self.selected=nxt[6];return self.segment_index
 def _ready(self,require_sample=True):
  if not self.power or self.emergency or self.furnace_lower>1e-12 or self._moving() or self.punch>1e-12:raise ActionRejected('NOT_READY')
  if require_sample and not self.sample:raise ActionRejected('NO_SAMPLE')
  if not self.gas_open or self.sample_flow<=0 or self.balance_flow<=0 or self.gas_remaining['N2']<=0 or self.gas_remaining[self.selected]<=0:raise ActionRejected('NO_GAS')
  if self.model_fault:raise ActionRejected('MODEL_FAULT')
 def _indicated(self):
  if not self.power or not self.plant:return dict(furnace=None,sample=None,mass=None,oxygen=None)
  p=self.plant;c=self.calibration;mass=(self.obs_mass-c['mass_zero_kg'])*c['mass_gain_correction'];resolution=8e-12 if self.balance_range=='low' else 6e-11;limit=.0001 if self.balance_range=='low' else .001
  return dict(furnace=(p['temperature_gain']*self.furnace+p['temperature_offset_k']-c['temperature_zero_k'])*c['temperature_gain_correction'],sample=(p['temperature_gain']*self.sample_temperature+p['temperature_offset_k']-c['temperature_zero_k'])*c['temperature_gain_correction'],mass=None if self._mass()>limit+1e-15 else float(round(mass/resolution)*resolution),oxygen=float(self.obs_oxygen))
 def _control_k(self,t):
  """The true furnace temperature at which the device's own furnace reading (sensor error and stored calibration, as _indicated) shows t:
  the controller holds its reading at the program temperature, so a sensor error or a temperature calibration moves the furnace."""
  p=self.plant;c=self.calibration;return (t/c['temperature_gain_correction']+c['temperature_zero_k']-p['temperature_offset_k'])/p['temperature_gain']
 def _guard(self,k,principal,role):
  """A run belongs to the operator who started it: another agent may not stop it, read its result, skip its segments or switch air cooling
  while it runs (direct call or panel)."""
  if k in OWNER_GUARDED and role=='agent' and self.operation_id is not None and self._owners.get(self.operation_id)!=principal:raise ActionRejected('ACCESS_DENIED')
  if k in RUN_GUARDED and role=='agent' and self.phase=='running' and self._owners.get(self.operation_id)!=principal:raise ActionRejected('ACCESS_DENIED')
 def read(self,role='evaluator'):
  a=self._indicated();p=self.plant;pressure=sum(self.chamber)*R_GAS*p['chamber_reference_temperature_k']/p['chamber_volume_m3'] if p else 0.
  d={'plant.configured':p is not None,'power.enabled':self.power,'sample.present':self.sample is not None,'gas.opened':self.gas_open,'thermal.enabled':self.thermal_enabled,'cooling.enabled':self.cooling,'emergency.latched':self.emergency,'furnace.opened':bool(self.furnace_lower>=.11-1e-12),'furnace.moving':bool(abs(self.furnace_target-self.furnace_lower)>1e-12),'carousel.moving':bool(abs(self._angle_error())>1e-12),'punch.moving':bool(abs(self.punch_target-self.punch)>1e-12),'furnace.lower_m':self.furnace_lower,'furnace.target_m':self.furnace_target,'punch.press_m':self.punch,'punch.target_m':self.punch_target,'carousel.angle_rad':self.carousel_angle,'carousel.target_index':self.carousel_index,'power.angle_rad':.28 if self.power else 0.,'gas.selected':self.selected,'gas.sample_flow_m3_s':self.sample_flow,'gas.balance_flow_m3_s':self.balance_flow,'balance.range':self.balance_range,'thermal.target_k':self.target,'thermal.final_target_k':self.final_target,'furnace.indicated_k':a['furnace'],'sample.indicated_k':a['sample'],'mass.indicated_kg':a['mass'],'gas.oxygen_fraction':a['oxygen'],'truth.furnace_k':self.furnace,'truth.sample_k':self.sample_temperature,'truth.sample_mass_kg':self._mass(),'truth.loss_kg':self.loss,'truth.oxygen_uptake_kg':self.uptake,'truth.n2_mol':self.gas_remaining['N2'],'truth.o2_mol':self.gas_remaining['O2'],'truth.chamber_n2_mol':self.chamber[0],'truth.chamber_o2_mol':self.chamber[1],'truth.chamber_pressure_pa':pressure,'truth.heater_w':self.heater_power,'clock.time_s':self.time_s,'acquisition.phase':self.phase,'acquisition.elapsed_s':self.elapsed,'acquisition.points':len(self.rows),'acquisition.segment_index':self.segment_index,'acquisition.stage':self.stage,'panel.temperature_text':'' if a['furnace'] is None else f"{a['furnace']-273.15:.1f}",'panel.mass_text':'' if a['mass'] is None else f"{a['mass']*1e6:.6f}",'model.fault':self.model_fault}
  d.update({'autosampler.tray_occupied':[x is not None for x in self.tray],'autosampler.balance_origin':self.balance_origin,'autosampler.transfer':self.transfer['kind'] if self.transfer else None})
  return {k:v for k,v in d.items() if role!='agent' or SLOTS[k]['visibility']=='agent'}
 def _ledger(self):
  Ef=self.plant['furnace_capacity_j_k']*(self.furnace-self.plant['ambient_k']) if self.plant else 0.
  out=dict(sample_loaded_kg=self.mass_loaded,sample_present_kg=self._mass(),sample_emitted_kg=self.mass_emitted,oxygen_uptake_kg=self.oxygen_uptake,sample_recovered_kg=self.mass_recovered,sample_error_kg=self.mass_loaded+self.oxygen_uptake-self._mass()-self.mass_emitted-self.mass_recovered,heater_input_j=self.heater_input,reaction_heat_j=self.reaction_heat,ambient_loss_j=self.ambient_loss,cooling_removed_j=self.cooling_removed,emitted_sensible_j=self.emitted_sensible,recovered_sensible_j=self.recovered_sensible,furnace_stored_j=Ef,sample_stored_j=self.sample_energy,energy_error_j=self.heater_input+self.reaction_heat-self.ambient_loss-self.cooling_removed-self.emitted_sensible-self.recovered_sensible-Ef-self.sample_energy)
  for i,g in enumerate(['N2','O2']):
   consumed=self.oxygen_uptake/M_O2 if g=='O2' else 0.;values=dict(loaded=self.gas_loaded[g],remaining=self.gas_remaining[g],initial_chamber=self.initial_chamber[i],chamber=self.chamber[i],exhausted=self.gas_exhausted[g],recovered=self.gas_recovered[g],consumed=consumed,error=self.gas_loaded[g]+self.initial_chamber[i]-self.gas_remaining[g]-self.chamber[i]-self.gas_exhausted[g]-self.gas_recovered[g]-consumed)
   out.update({g.lower()+'_'+k+'_mol':v for k,v in values.items()})
  return out
 def _row(self):
  a=self._indicated();return dict(time_s=self.time_s,elapsed_s=self.elapsed,segment_index=self.segment_index,stage=self.stage,target_k=self.target,furnace_k=a['furnace'],sample_k=a['sample'],mass_kg=a['mass'],sample_gas_target=self.selected,oxygen_fraction=a['oxygen'])
 def _record(self):
  r=self._row()
  if self.rows and abs(self.rows[-1]['elapsed_s']-r['elapsed_s'])<1e-11:self.rows[-1]=r
  else:self.rows.append(r)
 def _result(self):
  if self.phase!='running' and self.operation_id in self.records:return copy.deepcopy(self.records[self.operation_id])
  return dict(operation_id=self.operation_id,phase=self.phase,elapsed_s=self.elapsed,rows=copy.deepcopy(self.rows),reference_id=self.operation_reference,template_source=self.operation_source,projection_json=self.operation_projection,method_json=json.dumps(self.operation_method,sort_keys=True) if self.operation_method else None,calibration_json=json.dumps(self.operation_calibration,sort_keys=True) if self.operation_calibration else None,evidence='Measured source T/mass curve anchors explicitly accepted projected irreversible loss with thermal lag and engineering relaxation. Not calibrated Arrhenius kinetics or arbitrary-rate prediction; transferred rate/gas/pan/mass/temperature conditions require explicit acceptance. Source raw arrays remain unmodified. Optional O2 uptake uses explicit engineering capacity/rate/Ea, never inferred from loss curve. Calibrated independent dynamic balance observes pan+residue; no true mass or source residue is returned. Isothermal reference-volume N2/O2 chamber mixes finite inventories; unknown evolved species are a separate mass reservoir and do not alter chamber gas pressure. Finite heat ledger includes reaction heat and emitted sensible enthalpy; incoming O2 reference enthalpy is zero at ambient. Manufacturer resolution is quantization, not certified noise or accuracy.',error_code=self.error)
 def _finish(self,phase,error=None):
  self.phase=phase;self.error=error;self.thermal_enabled=False;self.heater_power=0.;self._record();self.records[self.operation_id]=self._result()
 @property
 def panel(self):
  """Front-panel UI state (engine object, kept outside __dict__ so state snapshots and atomic rollback stay plain data)."""
  if self not in _PANELS:_PANELS[self]=_panel_ui.PanelUI(PANEL_SPEC,self)
  return _PANELS[self]
 def panel_read(self):
  """What the panel firmware sees: the agent slots plus power, power-up count, serial, remaining run time, the active method, weight percent
  of the run's first weight and the weight-percent / temperature traces of the current or last run (chart)."""
  d=self.read('agent');rows=self.rows if self.phase=='running' or not self.operation_id else self.records.get(self.operation_id,{}).get('rows',self.rows)
  pick=rows[::max(1,len(rows)//300)]+rows[-1:] if rows else [];m=d['mass.indicated_kg'];ref=next((r['mass_kg'] for r in rows if r['mass_kg']),None) or m
  seg=[dict(n=i+1,target_c=s['target_k']-273.15,rate_c_min=s['rate_k_s']*60,hold_min=s['hold_s']/60,gas=s['gas'],current=self.phase=='running' and i==self.segment_index) for i,s in enumerate(self.method['segments'])]
  d.update({'host.powered':self.power,'host.power_epoch':self.power_epoch,'host.instance_id':self.instance_id,'host.remaining_s':max(0.,self.duration-self.elapsed) if self.phase=='running' else None,
            'host.weight_pct':100*m/ref if m is not None and ref else None,'host.method':dict(start_c=self.method['start_temperature_k']-273.15,segments=seg),
            'host.plot_w':[[r['elapsed_s']/60,100*r['mass_kg']/ref] for r in pick if r['mass_kg'] is not None and ref],
            'host.plot_t':[[r['elapsed_s']/60,r['sample_k']-273.15] for r in pick if r['sample_k'] is not None],'host.plot_span':max(1.,pick[-1]['elapsed_s']/60) if pick else 10.})
  return d
 def panel_time(self):return self.time_s
 def panel_invoke(self,capability_id,arguments):
  """A panel action takes the path of a direct call (atomic; a refusal is shown as the device's message); a gesture made through a session keeps
  that caller's access rules (_guard)."""
  try:
   if self._caller:self._guard(capability_id,*self._caller)
   return self.invoke(capability_id,**arguments)
  except ActionRejected as exc:raise _panel_ui.PanelRejected(str(exc)) from None
 def _panel_command(self,k,a):
  validate_arguments(k,a)
  if not self.plant:raise ActionRejected('NOT_CONFIGURED')
  if k=='panel.frame':return dict(frame=self.panel.frame())
  if k=='panel.capture':
   data,w,h=self.panel.png_bytes(a['display_id']);sha=hashlib.sha256(data).hexdigest();d=Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir())/'chemistry_sim_panel'/self.instance_id;d.mkdir(parents=True,exist_ok=True);p=d/f"{a['display_id']}_{sha[:16]}.png"
   if not p.exists():p.write_bytes(data)
   return dict(display_id=a['display_id'],time_s=self.time_s,image=dict(path=str(p),format='png',width=w,height=h,sha256=sha))
  if ('to_x' in a)!=('to_y' in a):raise ActionRejected('INVALID_ARGUMENT')
  try:
   if k=='panel.press':ok=self.panel.press(a['input_id'],float(a.get('duration_s',.1)))
   elif k=='panel.switch':ok=self.panel.switch(a['input_id'],a['position'])
   else:ok=self.panel.touch(a['display_id'],a['x'],a['y'],float(a.get('duration_s',.1)),to=(a['to_x'],a['to_y']) if 'to_x' in a else None)
  except _panel_ui.PanelError:raise ActionRejected('INVALID_ARGUMENT') from None
  self.revision+=1;return {'accepted':bool(ok)}
 def invoke(self,capability,**kwargs):
  if capability in PANEL_CAPS:return self._panel_command(capability,kwargs)
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability]['input_schema'],kwargs)
  if capability in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  if capability=='sim.step':self.step(kwargs['dt_s']);return {'time_s':self.time_s}
  snapshot=copy.deepcopy(self.__dict__)
  try:
   result=self._invoke(capability,kwargs);validate(CAPS[capability]['output_schema'],result)
   if CAPS[capability]['kind'] not in ['query','analysis']:self.revision+=1
   return result
  except Exception:self.__dict__.clear();self.__dict__.update(snapshot);raise
 def _invoke(self,k,a):
  if k=='plant.configure':
   if self.plant is not None or self.time_s!=0 or self.power:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   if a['safe_access_temperature_k']<a['ambient_k']:raise ActionRejected('PLANT_CONFIGURATION')
   self.plant=copy.deepcopy(a);self.furnace=self.sample_temperature=self.target=self.final_target=a['ambient_k'];self.capacity=a['chamber_volume_m3']*a['chamber_reference_pressure_pa']/(R_GAS*a['chamber_reference_temperature_k']);self.chamber=[self.capacity,0.];self.initial_chamber=self.chamber.copy();self.obs_mass=a['mass_gain']*a['pan_mass_kg']+a['mass_offset_kg'];return {'configured':True}
  if not self.plant and k not in ['reference.query','analysis.mass_stages','acquisition.result','ledger.read']:raise ActionRejected('NOT_CONFIGURED')
  if k=='sample.load':
   self._mechanical()
   if self.sample:raise ActionRejected('ALREADY_LOADED')
   if self.furnace_lower<.11-1e-12:raise ActionRejected('CELL_NOT_ACCESSIBLE')
   if not a['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   if a['mass_kg']>(.0001 if self.balance_range=='low' else .001):raise ActionRejected('BALANCE_OVERLOAD')
   ref=library()['substances'].get(a['reference_id'])
   if not ref:raise ActionRejected('INVALID_REFERENCE')
   model=prepare_reference(ref,a['accept_curve_projection'])
   if self.sample_temperature>model['temperature'][-1]:raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
   if self.sample_temperature<model['temperature'][0] and not a['accept_cold_extension']:raise ActionRejected('COLD_EXTENSION_NOT_ACCEPTED')
   self.sample=copy.deepcopy(a);self.reference=copy.deepcopy(ref);self.model=model;self.pan_run=False;self.loss=self.uptake=0.;self.model_fault=None;self.mass_loaded+=a['mass_kg'];self.sample_temperature=self.plant['ambient_k']+self.sample_energy/self._cp();return {'loaded':True}
  if k=='sample.remove':
   self._mechanical()
   if not self.sample:raise ActionRejected('NOT_LOADED')
   if self.furnace_lower<.11-1e-12:raise ActionRejected('CELL_NOT_ACCESSIBLE')
   m=self._mass();energy=m*self.sample['heat_capacity_j_kg_k']*(self.sample_temperature-self.plant['ambient_k']);self.mass_recovered+=m;self.recovered_sensible+=energy;self.sample_energy-=energy;self.balance_origin=None;self.sample=None;self.reference=None;self.model=None;self.loss=self.uptake=0.;self.model_fault=None;return {'removed':True}
  if k=='gas.load':
   g=a['gas']
   if g in self.gases or self.gas_open or self.phase=='running':raise ActionRejected('BUSY')
   if not a['accept_reference_conditions']:raise ActionRejected('REFERENCE_CONDITIONS_NOT_ACCEPTED')
   self.gases[g]=copy.deepcopy(a);self.gas_remaining[g]=a['amount_mol'];self.gas_loaded[g]+=a['amount_mol'];return {'loaded':True}
  if k=='gas.unload':
   g=a['gas']
   if g not in self.gases:raise ActionRejected('NOT_LOADED')
   if self.gas_open or self.phase=='running':raise ActionRejected('BUSY')
   self.gas_recovered[g]+=self.gas_remaining[g];self.gas_remaining[g]=0.;del self.gases[g];return {'unloaded':True}
  if k=='gas.set_flow':
   if self.phase=='running':raise ActionRejected('BUSY')
   self.sample_flow=a['sample_flow_m3_s'];self.balance_flow=a['balance_flow_m3_s'];return {'configured':True}
  if k=='gas.select':
   if self.phase=='running':raise ActionRejected('BUSY')
   self._source_conditions(a['gas'])
   if self.gas_open and self.gas_remaining[a['gas']]<=0:raise ActionRejected('NO_GAS')
   self.selected=a['gas'];return {'gas':self.selected}
  if k=='gas.set_open':
   if self.phase=='running':raise ActionRejected('BUSY')
   if a['opened'] and (not self.power or self.emergency or self.gas_remaining['N2']<=0 or self.gas_remaining[self.selected]<=0 or self.sample_flow<=0 or self.balance_flow<=0):raise ActionRejected('NO_GAS')
   self.gas_open=a['opened'];return {'opened':self.gas_open}
  if k=='power.set':
   if self.phase=='running' and not a['enabled']:self._finish('failed','POWER_LOSS')  # the power switch is a hard switch (final sync): never refused; a running experiment ends as on a power loss
   if a['enabled'] and not self.power:self.power_epoch+=1
   self.power=a['enabled']
   if not self.power:self.thermal_enabled=False;self.gas_open=False;self.heater_power=0.;self.furnace_target=self.furnace_lower;self.punch_target=self.punch;self.carousel_target_angle=self.carousel_angle;self.transfer=None
   return {'enabled':self.power}
  if k=='furnace.set':
   self._mechanical()
   if not self.power or self.punch>1e-12:raise ActionRejected('NOT_READY')
   self.furnace_target=.11 if a['opened'] else 0.;return {'opened':a['opened']}
  if k=='carousel.index':
   self._mechanical()
   if not self.power or self.furnace_lower<.11-1e-12 or self.punch>1e-12:raise ActionRejected('NOT_READY')
   self.carousel_index=a['position'];self.carousel_target_angle=self._carousel_target();return {'position':a['position']}
  if k=='punch.set':
   self._mechanical()
   if not self.power or self.furnace_lower<.11-1e-12 or self.carousel_index!=1:raise ActionRejected('PUNCH_DEMO_POSITION')
   self.punch_target=.016 if a['pressed'] else 0.;return {'pressed':a['pressed']}
  if k=='thermal.set_temperature':
   if self.phase=='running':raise ActionRejected('BUSY')
   self._ready(False);self._domain(a['temperature_k']);self._source_conditions(self.selected,a['rate_k_s']);self.final_target=a['temperature_k'];self.rate=a['rate_k_s'];self.thermal_enabled=True;return {'target_k':self.final_target}
  if k=='thermal.disable':
   if self.phase=='running':raise ActionRejected('BUSY')
   self.thermal_enabled=False;self.heater_power=0.;return {'enabled':False}
  if k=='thermal.set_cooling':self.cooling=a['enabled'];return {'enabled':self.cooling}
  if k=='balance.set_range':
   if self.phase=='running':raise ActionRejected('BUSY')
   if self._mass()>(.0001 if a['range']=='low' else .001):raise ActionRejected('BALANCE_OVERLOAD')
   self.balance_range=a['range'];return {'range':self.balance_range}
  if k=='sensor.calibrate':
   if self.phase=='running':raise ActionRejected('BUSY')
   self.calibration=copy.deepcopy(a);return copy.deepcopy(a)
  if k=='balance.tare':
   if self.phase=='running' or self.sample or not self.power or self.thermal_enabled or not self._cold():raise ActionRejected('TARE_REQUIRES_EMPTY_STABLE')
   raw=self.plant['mass_gain']*self.plant['pan_mass_kg']+self.plant['mass_offset_kg']
   if abs(raw-self.obs_mass)>8e-12:raise ActionRejected('TARE_REQUIRES_EMPTY_STABLE')
   self.calibration['mass_zero_kg']=self.obs_mass;return {'zeroed':True}
  if k=='method.configure':
   if self.phase=='running':raise ActionRejected('BUSY')
   if abs(a['sample_interval_s']/DT-round(a['sample_interval_s']/DT))>1e-9:raise ActionRejected('SAMPLING_GRID')
   for t in [a['start_temperature_k']]+[s['target_k'] for s in a['segments']]:self._domain(t)
   for s in a['segments']:self._source_conditions(s['gas'],s['rate_k_s'])
   self.method=copy.deepcopy(a);return copy.deepcopy(a)
  if k=='acquisition.start':
   if self.phase=='running':raise ActionRejected('BUSY')
   self._ready();observed=self._indicated()
   if observed['mass'] is None:raise ActionRejected('BALANCE_OVERLOAD')
   if max(abs(observed[x]-self.method['start_temperature_k']) for x in ['furnace','sample'])>self.method['start_tolerance_k']:raise ActionRejected('START_NOT_EQUILIBRATED')
   for t in [self.method['start_temperature_k']]+[s['target_k'] for s in self.method['segments']]:self._domain(t)
   self.schedule=[];t=0.;previous=self.method['start_temperature_k']
   for i,s in enumerate(self.method['segments']):
    self._source_conditions(s['gas'],s['rate_k_s'])
    if self.gas_remaining[s['gas']]<=0:raise ActionRejected('NO_GAS')
    ramp=abs(s['target_k']-previous)/s['rate_k_s']
    if ramp>0:self.schedule.append((t,t+ramp,previous,s['target_k'],i,'ramp',s['gas']));t+=ramp
    if s['hold_s']>0:self.schedule.append((t,t+s['hold_s'],s['target_k'],s['target_k'],i,'hold',s['gas']));t+=s['hold_s']
    previous=s['target_k']
   if t<=0:raise ActionRejected('EMPTY_PROGRAM')
   if t/self.method['sample_interval_s']>1999900:raise ActionRejected('PROGRAM_TOO_LONG')
   self.pan_run=True;self.duration=t;self._counter+=1;self.operation_id=f'{self.instance_id}:tga:{self._counter}';self.operation_start=self.time_s;self.phase='running';self.elapsed=0.;self.segment_index=0;self.stage=self.schedule[0][5];self.selected=self.schedule[0][6];self.rows=[];self.error=None;self.operation_method=copy.deepcopy(self.method);self.operation_calibration=copy.deepcopy(self.calibration);self.operation_reference=self.sample['reference_id'];self.operation_source=json.dumps(self.reference.get('source',{}),sort_keys=True);self.operation_projection=json.dumps(self.model['stats'],sort_keys=True);self.target=self.method['start_temperature_k'];self.final_target=previous;self.thermal_enabled=True;self._record();return {'operation_id':self.operation_id}
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self._finish('cancelled');return {'phase':'cancelled'}
  if k=='acquisition.result':return self._result()
  if k=='reference.query':
   ref=library()['substances'].get(a['reference_id'])
   if not ref:raise ActionRejected('INVALID_REFERENCE')
   return dict(reference_json=json.dumps(ref,sort_keys=True,allow_nan=False),template_source=json.dumps(ref.get('source',{}),sort_keys=True))
  if k=='analysis.mass_stages':return analyze_mass(**a)
  if k=='ledger.read':return self._ledger()
  if k=='emergency.stop':
   self.emergency=True;self.thermal_enabled=False;self.heater_power=0.;self.gas_open=False;self.furnace_target=self.furnace_lower;self.punch_target=self.punch;self.carousel_target_angle=self.carousel_angle;self.transfer=None
   if self.phase=='running':self._finish('failed','SOFTWARE_STOP')
   return {'latched':True}
  if k=='emergency.reset':
   if not self._cold():raise ActionRejected('HOT_CELL')
   self.emergency=False;return {'latched':False}
  if k=='method.advance':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   return {'segment_index':self._advance_segment()}
  if k=='tray.place':
   if self.tray[a['position']-1] is not None:raise ActionRejected('TRAY_POSITION_OCCUPIED')
   if not a['sample']['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   ref=library()['substances'].get(a['sample']['reference_id'])
   if not ref:raise ActionRejected('INVALID_REFERENCE')
   prepare_reference(ref,a['sample']['accept_curve_projection']);self.tray[a['position']-1]=dict(sample=copy.deepcopy(a['sample']),run=False);return {'placed':True}
  if k=='tray.remove':
   if self.tray[a['position']-1] is None:raise ActionRejected('TRAY_POSITION_EMPTY')
   if self.transfer and self.transfer['position']==a['position']:raise ActionRejected('MECHANISM_MOVING')
   self.tray[a['position']-1]=None;return {'removed':True}
  if k in ['autosampler.load','autosampler.unload']:
   n=a['position'];load=k=='autosampler.load';pan=self.tray[n-1]
   self._mechanical()
   if not self.power or self.emergency or self.punch>1e-12 or self.transfer:raise ActionRejected('NOT_READY')
   if self.furnace_lower<.11-1e-12:raise ActionRejected('CELL_NOT_ACCESSIBLE')
   if load and pan is None:raise ActionRejected('TRAY_POSITION_EMPTY')
   if load and pan['run']:raise ActionRejected('PAN_ALREADY_RUN')
   if not load and pan is not None:raise ActionRejected('TRAY_POSITION_OCCUPIED')
   if load and self.sample:raise ActionRejected('BALANCE_OCCUPIED')
   if not load and not self.sample:raise ActionRejected('BALANCE_EMPTY')
   self._dry_run('sample.load',pan['sample']) if load else self._dry_run('sample.remove',{})   # the pan can go on / off the balance
   self.carousel_index=n;self.carousel_target_angle=self._carousel_target();self.transfer=dict(kind='load' if load else 'unload',position=n);return {'accepted':True}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _dry_run(self,k,a):
  snapshot=copy.deepcopy(self.__dict__)
  try:self._invoke(k,a)
  finally:self.__dict__.clear();self.__dict__.update(snapshot)
 def _finish_transfer(self):
  """The carousel is at the position: the pan goes on the balance as sample.load, or comes off as sample.remove and back on the carousel."""
  t=self.transfer;self.transfer=None;n=t['position']
  try:
   if t['kind']=='load':
    self._invoke('sample.load',self.tray[n-1]['sample']);self.tray[n-1]=None;self.balance_origin=n
   else:
    run=self.pan_run or abs(self._mass()-self.sample['mass_kg'])>1e-15;pan=dict(sample=dict(self.sample,mass_kg=self._mass()),run=run);self._invoke('sample.remove',{});self.tray[n-1]=pan
  except ActionRejected:pass                                                  # conditions changed while turning: the pan stays where it was
 def _program(self,t):
  for start,end,lo,hi,i,stage,gas in self.schedule:
   if t<end-1e-10:return lo+(hi-lo)*max(0.,min(1.,(t-start)/(end-start))),i,stage,gas
  s=self.schedule[-1];return s[3],s[4],s[5],s[6]
 def _gas_rates(self):
  if not self.gas_open:return {'N2':0.,'O2':0.},0.,0.
  pN=self.gases.get('N2');pS=self.gases.get(self.selected)
  b=self.balance_flow*pN['reference_pressure_pa']/(R_GAS*pN['reference_temperature_k']) if pN else 0.;s=self.sample_flow*pS['reference_pressure_pa']/(R_GAS*pS['reference_temperature_k']) if pS else 0.
  return {'N2':b+(s if self.selected=='N2' else 0.),'O2':s if self.selected=='O2' else 0.},s,b
 def _thermal_solution(self,h,mass,energy):
  p=self.plant;amb=p['ambient_k'];C=self._cp(mass);G=p['sample_conductance_w_k'];cf=p['furnace_capacity_j_k'];ga=p['ambient_conductance_w_k'];gc=p['forced_cooling_conductance_w_k'] if self.cooling and self.power else 0.;gain=p['controller_gain_w_k'] if self.thermal_enabled and self.power and not self.emergency else 0.;den=C+h*G;a=energy/den;b=h*G/den;base=cf/h+ga+gc+G*(1-b);rhs=cf/h*(self.furnace-amb)+G*a;target=self._control_k(self.target)-amb   # the controller acts on the device's furnace reading
  x=(rhs+gain*target)/(base+gain);heat=gain*(target-x)
  if heat<0:x=rhs/base;heat=0.
  elif heat>p['heater_max_w']:heat=p['heater_max_w'];x=(rhs+heat)/base
  Ts=amb+a+b*x;Tf=amb+x;return Tf,Ts,C*(Ts-amb),heat,ga*x*h,gc*x*h
 def _physics(self,h,sample_moles,balance_moles):
  p=self.plant;self.chamber,ex=mix_chamber(*self.chamber,self.selected,sample_moles,self.capacity);self.gas_exhausted['N2']+=ex[0]+balance_moles;self.gas_exhausted['O2']+=ex[1]
  dl=du=qh=eout=0.;mass=self._mass();energy=self.sample_energy
  if self.sample and not self.model_fault:
   s=self.sample;T=self.sample_temperature
   if T>self.model['temperature'][-1]+1e-8:self.model_fault='REFERENCE_TEMPERATURE_RANGE'
   else:
    fraction=reference_fraction(T,self.model);desired=s['mass_kg']*(1-fraction);dl=max(0.,desired-self.loss)*(-math.expm1(-h/s['loss_relaxation_s']));oxygen=self.chamber[1]/sum(self.chamber) if sum(self.chamber)>0 else 0.;exponent=max(-700.,min(50.,-s['oxidation_activation_j_mol']/R_GAS*(1/max(T,1.)-1/s['oxidation_reference_temperature_k'])));k=s['oxidation_rate_s_inv']*math.exp(exponent)*oxygen;du=min(max(0.,s['mass_kg']*s['oxidation_capacity_fraction']-self.uptake)*(-math.expm1(-k*h)),self.chamber[1]*M_O2);qh=dl*s['loss_heat_j_kg']+du*s['oxidation_heat_j_kg'];eout=dl*s['heat_capacity_j_kg_k']*(T-p['ambient_k']);mass+=du-dl;energy+=qh-eout
  values=self._thermal_solution(h,mass,energy)
  if min(values[:2])<=0 or max(values[:2])>self._pan_limit()+1e-8:
   self.model_fault='THERMAL_MODEL_RANGE';self.thermal_enabled=False;dl=du=qh=eout=0.;mass=self._mass();values=self._thermal_solution(h,mass,self.sample_energy)
  self.furnace,self.sample_temperature,self.sample_energy,self.heater_power,ambient,cooling=values;self.loss+=dl;self.uptake+=du;self.mass_emitted+=dl;self.oxygen_uptake+=du;self.chamber[1]=max(0.,self.chamber[1]-du/M_O2);self.reaction_heat+=qh;self.emitted_sensible+=eout;self.heater_input+=self.heater_power*h;self.ambient_loss+=ambient;self.cooling_removed+=cooling
  w=-math.expm1(-h/p['sensor_tau_s']);self.obs_mass+=w*(p['mass_gain']*(p['pan_mass_kg']+self._mass())+p['mass_offset_kg']-self.obs_mass);oxy=self.chamber[1]/sum(self.chamber) if sum(self.chamber)>0 else 0.;self.obs_oxygen+=w*(oxy-self.obs_oxygen)
 def _advance(self,dt):
  left=dt
  while left>1e-12:
   h=min(.01,left);running=self.phase=='running'
   if running:
    future=[x-self.elapsed for x in [self.duration,self.method['timeout_s']]+[s[1] for s in self.schedule] if x>self.elapsed+1e-10]
    if future:h=min(h,min(future))
    _,_,_,self.selected=self._program(self.elapsed+min(h/2,1e-6))
   rates,s,b=self._gas_rates();gas_limit=min([self.gas_remaining[g]/r for g,r in rates.items() if r>0] or [math.inf]);h=min(h,gas_limit)
   if h<=0:
    self.gas_open=False;self.thermal_enabled=False;self.heater_power=0.
    if running:self._finish('failed','GAS_EXHAUSTED')
    continue
   if running:self.target,self.segment_index,self.stage,_=self._program(self.elapsed+h)
   elif self.thermal_enabled:self.target+=max(-self.rate*h,min(self.rate*h,self.final_target-self.target))
   if self.plant:
    self._physics(h,s*h,b*h)
    if self.power and not self.emergency:
     self.furnace_lower+=max(-self.plant['furnace_speed_m_s']*h,min(self.plant['furnace_speed_m_s']*h,self.furnace_target-self.furnace_lower));self.punch+=max(-self.plant['punch_speed_m_s']*h,min(self.plant['punch_speed_m_s']*h,self.punch_target-self.punch));delta=max(-self.plant['carousel_speed_rad_s']*h,min(self.plant['carousel_speed_rad_s']*h,self._angle_error()));self.carousel_angle=(self.carousel_angle+delta+math.pi)%(2*math.pi)-math.pi
     if self.transfer and not self._moving() and abs(((self._carousel_target()-self.carousel_angle+math.pi)%(2*math.pi))-math.pi)<=1e-9:self._finish_transfer()
   for g,r in rates.items():self.gas_remaining[g]=max(0.,self.gas_remaining[g]-r*h)
   self.time_s+=h;left-=h
   if running:self.elapsed+=h
   exhausted=gas_limit<=h+1e-16
   if exhausted:
    self.gas_open=False;self.thermal_enabled=False;self.heater_power=0.
    if self.phase=='running':self._finish('failed','GAS_EXHAUSTED')
   if self.model_fault:
    self.thermal_enabled=False;self.heater_power=0.
    if self.phase=='running':self._finish('failed',self.model_fault)
   if self.phase=='running':
    if self._mass()>(.0001 if self.balance_range=='low' else .001)+1e-15:self._finish('failed','BALANCE_OVERLOAD')
    elif self.elapsed>=self.method['timeout_s']-1e-9 and self.elapsed<self.duration-1e-9:self._finish('failed','TIMEOUT')
    elif self.elapsed>=self.duration-1e-9:self.elapsed=self.duration;self._finish('succeeded')
    elif any(abs(self.elapsed-x[1])<1e-9 for x in self.schedule):self._record()
 def step(self,dt_s):
  validate(number('s',0,3600),dt_s);self._remainder+=dt_s;ticks=int((self._remainder+1e-10)/DT);self._remainder-=ticks*DT
  if abs(self._remainder)<1e-10:self._remainder=0.
  for _ in range(ticks):
   self._advance(DT);self._ticks+=1;self.time_s=self._ticks*DT;self.revision+=1
   if self.phase=='running' and abs(self.elapsed/self.method['sample_interval_s']-round(self.elapsed/self.method['sample_interval_s']))<1e-7:self._record()
  return self.read()

class TaTga5500Session:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,instance_id):
  if instance_id!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  meta=export_ta_tga5500_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
 def read(self,*,instance_id,state_ids=None):
  self._target(instance_id);h=self._host;values=h.read(self._role);ids=list(values) if state_ids is None else state_ids
  if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
  if any(k not in values for k in ids):raise ActionRejected('ACCESS_DENIED')
  return dict(instance_id=instance_id,status='succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=values[k],unit=SLOTS[k]['unit'],quality='unavailable' if values[k] is None else 'estimated' if SLOTS[k]['origin']=='derived' else 'valid',sample_time_s=h.time_s,sample_window_s=None) for k in ids})
 def invoke(self,*,instance_id,capability_id,arguments,request_id):
  h=self._host
  try:
   self._target(instance_id)
   if not isinstance(capability_id,str) or capability_id not in ALL_CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
   if self._role=='agent' and ALL_CAPS[capability_id]['visibility']!='agent':raise ActionRejected('ACCESS_DENIED')
   if not isinstance(request_id,str) or not request_id:raise ActionRejected('INVALID_ARGUMENT')
   validate_arguments(capability_id,arguments);key=(self._principal,request_id);digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
   if key in h._requests:
    old,result=h._requests[key]
    if old!=digest:raise ActionRejected('REQUEST_CONFLICT')
    return copy.deepcopy(result)
   h._guard(capability_id,self._principal,self._role);started=h.operation_id;h._caller=(self._principal,self._role)
   try:result=h.invoke(capability_id,**arguments)
   finally:h._caller=None
   operation=result.get('operation_id') if capability_id=='acquisition.start' else None
   if operation:h._owners[operation]=self._principal
   if capability_id in PANEL_CAPS and h.operation_id!=started:h._owners[h.operation_id]=self._principal  # Start on the touch screen: the run belongs to the operator who pressed it
   response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=operation,status='accepted' if operation else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None);h._requests[key]=(digest,copy.deepcopy(response));return response
  except ActionRejected as exc:return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,status='unsupported' if str(exc)=='UNSUPPORTED' else 'rejected',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))
 def write(self,*,instance_id,state_id,value,request_id):
  self._target(instance_id);entry=SLOTS.get(state_id)
  if not entry or not entry['write']:raise ActionRejected('ACCESS_DENIED')
  capability,argument=entry['write'];return self.invoke(instance_id=instance_id,capability_id=capability,arguments={argument:value},request_id=request_id)
 def poll(self,operation_id):
  h=self._host
  if h._owners.get(operation_id)!=self._principal:raise ActionRejected('ACCESS_DENIED')
  result=h._result() if operation_id==h.operation_id and h.phase=='running' else h.records.get(operation_id)
  if result is None:raise ActionRejected('UNKNOWN_OPERATION')
  return dict(operation_id=operation_id,status=result['phase'],result=copy.deepcopy(result))
 def cancel(self,operation_id,request_id):
  self.poll(operation_id);h=self._host
  if not isinstance(request_id,str) or not request_id:raise ActionRejected('INVALID_ARGUMENT')
  key=(self._principal,request_id)
  if key in h._cancel_operations:
   if h._cancel_operations[key]!=operation_id:raise ActionRejected('REQUEST_CONFLICT')
   return copy.deepcopy(h._requests[key][1])
  if key in h._requests:raise ActionRejected('REQUEST_CONFLICT')
  if operation_id!=h.operation_id:raise ActionRejected('NOT_RUNNING')
  result=self.invoke(instance_id=h.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
  if result['status']=='succeeded':h._cancel_operations[key]=operation_id
  return result

OBSERVATIONS=['furnace.indicated_k','sample.indicated_k','mass.indicated_kg','gas.oxygen_fraction','panel.temperature_text','panel.mass_text']
EFFECTS={'plant.configure':['plant.configured','truth.furnace_k','truth.sample_k','truth.chamber_n2_mol','truth.chamber_pressure_pa','thermal.target_k','thermal.final_target_k'],
'sample.load':['sample.present','truth.sample_mass_kg','truth.sample_k','truth.loss_kg','truth.oxygen_uptake_kg','model.fault']+OBSERVATIONS,
'sample.remove':['sample.present','truth.sample_mass_kg','truth.loss_kg','truth.oxygen_uptake_kg','model.fault','autosampler.balance_origin']+OBSERVATIONS,
'gas.load':['truth.n2_mol','truth.o2_mol'],'gas.unload':['truth.n2_mol','truth.o2_mol'],'gas.set_flow':['gas.sample_flow_m3_s','gas.balance_flow_m3_s'],'gas.select':['gas.selected'],'gas.set_open':['gas.opened'],
'power.set':['power.enabled','power.angle_rad','thermal.enabled','acquisition.phase','acquisition.points','gas.opened','truth.heater_w','furnace.target_m','furnace.moving','punch.target_m','punch.moving','carousel.target_index','carousel.moving','autosampler.transfer']+OBSERVATIONS,
'furnace.set':['furnace.target_m','furnace.moving'],'carousel.index':['carousel.target_index','carousel.moving'],'punch.set':['punch.target_m','punch.moving'],
 'thermal.set_temperature':['thermal.final_target_k','thermal.enabled'],'thermal.disable':['thermal.enabled','truth.heater_w'],'thermal.set_cooling':['cooling.enabled'],
'balance.set_range':['balance.range']+OBSERVATIONS,'sensor.calibrate':OBSERVATIONS,'balance.tare':OBSERVATIONS,
'acquisition.start':['acquisition.phase','acquisition.elapsed_s','acquisition.points','acquisition.segment_index','acquisition.stage','thermal.target_k','thermal.final_target_k','thermal.enabled','gas.selected'],
'acquisition.stop':['acquisition.phase','acquisition.points','thermal.enabled','truth.heater_w'],
'emergency.stop':['emergency.latched','acquisition.phase','acquisition.points','thermal.enabled','gas.opened','truth.heater_w','furnace.target_m','furnace.moving','punch.target_m','punch.moving','carousel.moving','autosampler.transfer'],
'emergency.reset':['emergency.latched'],'tray.place':['autosampler.tray_occupied'],'tray.remove':['autosampler.tray_occupied'],'autosampler.load':['carousel.target_index','carousel.moving','autosampler.transfer'],'autosampler.unload':['carousel.target_index','carousel.moving','autosampler.transfer'],'method.advance':['acquisition.segment_index','acquisition.stage','acquisition.phase','acquisition.points','thermal.enabled','gas.selected','truth.heater_w'],'sim.step':list(SLOTS)}
for k,v in EFFECTS.items():CAPS[k]['effects']=v
_PANEL_EFFECTS=list(dict.fromkeys(x for c in (_panel_ui.spec_capabilities(PANEL_SPEC) if PANEL_SPEC else []) for x in CAPS[c]['effects']))
for k in PANEL_CAPS:PANEL_CAPS[k]['effects']=[] if k in ('panel.capture','panel.frame') else list(_PANEL_EFFECTS)  # a gesture changes what the commands it calls change
PANEL_LIMITATIONS=['面板层按panel_ui.json解释手势并调用本运行层的同名能力；Discovery触屏页面无公开操作手册，页面布局、标签与提示文字为近似（approximate），见panel_ui.json的assumptions。','手势不推进仿真时钟；长按无文档含义（按短按处理）；屏幕无超时。']
def _panel_contract_entry(k):
 """Contract fields of a panel.* capability that differ from the instrument commands."""
 query=k in ('panel.capture','panel.frame')
 return dict(preconditions=[] if query else [dict(description_zh='已声明plant（否则NOT_CONFIGURED）；关机时屏幕暗、只有电源开关起作用；被调用能力的前提同样适用，被拒时显示仪器的错误对话框且手势不被接受，状态不变。',state_ids=['plant.configured','power.enabled','acquisition.phase'],enforced_by='runtime',check='TaTga5500Runtime._panel_command')],
  errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['NOT_CONFIGURED','INVALID_ARGUMENT','UNKNOWN_CAPABILITY','UNKNOWN_INSTANCE','ACCESS_DENIED','REQUEST_CONFLICT']],
  model_effects=dict(mode='none',description_zh='屏幕纹理与按键关节触发属于后续Matterix步骤',binding_ids=[]),
  execution=dict(mode='synchronous',time_domain='simulation',completion_criterion='手势瞬时完成：界面状态与被调用能力的效果立即生效；结果只说明是否被仪器接受，屏幕须用panel.capture查看' if not query else '查询即时返回，不改变状态',completion_state_ids=[],timeout_s=None,cancellation='unsupported',retry='same_request_id',resources=['host_clock'],partial_failure_zh='一次手势可调用多个能力；被拒的能力原子回滚并显示错误对话框，此前已执行的能力保留（与实机逐次按键一致）。'),
  limitations=list(PANEL_LIMITATIONS),source_ids=['simulation_assumption'])
ERRORS=['UNKNOWN_CAPABILITY','UNKNOWN_INSTANCE','UNKNOWN_OPERATION','INVALID_ARGUMENT','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','BUSY','NOT_RUNNING','INITIALIZATION_ONLY','MODEL_NOT_ACCEPTED','PLANT_CONFIGURATION','NOT_CONFIGURED','HOT_CELL','THERMAL_CONTROL_ACTIVE','MECHANISM_MOVING','CELL_NOT_ACCESSIBLE','ALREADY_LOADED','NOT_LOADED','INVALID_REFERENCE','CURVE_PROJECTION_NOT_ACCEPTED','COLD_EXTENSION_NOT_ACCEPTED','CONDITION_TRANSFER_NOT_ACCEPTED','REFERENCE_CONDITIONS_NOT_ACCEPTED','NO_GAS','NO_SAMPLE','NOT_READY','MODEL_FAULT','BALANCE_OVERLOAD','PAN_TEMPERATURE_RANGE','REFERENCE_TEMPERATURE_RANGE','THERMAL_MODEL_RANGE','PUNCH_DEMO_POSITION','TARE_REQUIRES_EMPTY_STABLE','SAMPLING_GRID','START_NOT_EQUILIBRATED','EMPTY_PROGRAM','PROGRAM_TOO_LONG','GAS_EXHAUSTED','TIMEOUT','SOFTWARE_STOP','ANALYSIS_DATA','ANALYSIS_BOUNDARIES','MIXING_DOMAIN']
def export_ta_tga5500_contract_metadata():
 slots={};caps={}
 for k,v in SLOTS.items():slots[k]=dict(meaning_zh=v['meaning'],component_ids=[v['component']],value_schema=copy.deepcopy(v['schema']),unit=v['unit'],reference_conditions='SI. Gas volumes require explicit source reference absolute pressure/temperature. Isothermal chamber pressure is an engineering reference-volume proxy, not hot furnace pressure. Low/high mass resolution8e-12/6e-11kg is quantization, not accuracy.',origin=v['origin'],visibility=v['visibility'],access='read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'TaTga5500Session.read(state_ids=[{k!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description='Host0.1s fixed partition; <=0.01s implicit thermal/conservative gas steps plus program and finite inventory endpoints; queries never step.'),source_ids=['simulation_assumption'])
 for k,c in ALL_CAPS.items():
  un=k in UNSUPPORTED;async_=k=='acquisition.start';caps[k]={key:copy.deepcopy(c[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']};caps[k].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ['scene','robot','hardware'] else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.ta_tga5500.TaTga5500Session.invoke',invocation=f'session.invoke(instance_id="tga5500_01",capability_id={k!r},arguments={c["example_arguments"]!r},request_id="unique")'),preconditions=[] if c['kind'] in ['query','analysis'] or un else [dict(description_zh='冷态开炉和互锁、盘温域/真实源温域、显式假设、有限两气源和天平N2保护；拒绝原子无副作用',state_ids=['power.enabled','furnace.lower_m','sample.present','gas.opened','thermal.enabled','acquisition.phase'],enforced_by='runtime',check='atomic invoke and physical preconditions')],state_effects=[dict(state_id=x,timing='immediate',description_zh='实际命令即时变化') for x in c['effects']],observes=[],model_effects=dict(mode='none',description_zh='四关节/数字屏由独立场景验证；样品残渣无网格变化，自动盘转移不实现',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='完成有限温度目标段和保温；实际炉温及样品温度有热滞后，不保证化学转化完成' if async_ else '返回接受；运动/质量/热量等待宿主step',completion_state_ids=['acquisition.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','furnace','sample_pan','balance','N2','O2','carousel'],partial_failure_zh='取消/耗尽停止主动加热，保留残渣/氧/余热；后续由实际温度继续有限反应，冷却后不锁存高温失重目标。终态观测历史冻结。模型超域停止未校准反应而不回滚已发生变化。'),errors=[dict(code=x,meaning_zh=x,side_effects='partial' if async_ and x in ['GAS_EXHAUSTED','BALANCE_OVERLOAD','THERMAL_MODEL_RANGE','REFERENCE_TEMPERATURE_RANGE','TIMEOUT','SOFTWARE_STOP'] else 'none') for x in ERRORS],verification=[],limitations=['原始TGA曲线经明确接受的质量投影仅锚定工程失重目标，不是任意条件机理动力学','升温/气体/装样质量/盘配置迁移与未知Cp/G/tau/氧化参数均显式工程输入','厂家质量精密度与基线/ballistic指标不等于已验证模拟精度','HiRes/MTGA/逸出气鉴定与完整自动换盘未实现'],source_ids=['manufacturer_specs','simulation_assumption'])
  if k in ['acquisition.start','acquisition.stop','emergency.stop','power.set','thermal.set_temperature','thermal.disable','thermal.set_cooling','furnace.set','carousel.index','punch.set','gas.set_open','gas.select','method.advance','autosampler.load','autosampler.unload']:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='宿主推进温度/质量/独立观测/气体/关节') for x in SLOTS if x not in ['plant.configured','power.enabled','power.angle_rad','sample.present','emergency.latched']]
  if k in PANEL_CAPS:caps[k].update(_panel_contract_entry(k))
  if k in ['tray.place','tray.remove','autosampler.load','autosampler.unload']:caps[k]['errors']+=[dict(code=e,meaning_zh=z,side_effects='none') for e,z in [('TRAY_POSITION_EMPTY','转盘位置没有样品盘'),('TRAY_POSITION_OCCUPIED','转盘位置已有样品盘'),('BALANCE_OCCUPIED','天平上已有样品盘'),('BALANCE_EMPTY','天平上没有样品盘'),('PAN_ALREADY_RUN','样品盘已测过（残渣），不再装载')]]
 mechanism=dict(component_ids=['controller','furnace','sample_pan','balance','gas_manifold'],state_ids=list(slots),meaning_zh='曲线锚定失重、有限氧化/两气体混合与开口焓守恒',implementation='lumped',equations=[
 'Raw experimental T,m/m0 arrays remain unchanged in reference.query. Explicit accepted transform: stable ascending T, first at duplicate T, normalize original first mass, clip[0,1], cummin. Every changed/removed point and max adjustment is recorded. This is a curve-anchored engineering proxy; variable-rate and atmosphere transfer are not Arrhenius kinetic prediction.',
 'L_ref(Ts)=m0*(1-interp(projected_mass_fraction,Ts)); dL=max(0,L_ref(Ts)-L)*(1-exp(-dt/tau)). Irreversible actual L, but target is NOT latched: cancelled hot sample can continue losing mass under residual heat; cooled sample with L_ref<L stops. Above measured T rejected/faulted; optional cold extension is flat initial response explicitly accepted.',
 'Oxidation du=min((m0*capacity-u)*(1-exp(-k*xO2*dt)),nO2*M_O2). k=k_ref*exp[-Ea/R*(1/Ts-1/Tref)] with explicit engineering inputs and overflow-bounded exponent. nO2 decreases by du/M_O2; uptake remains finite. This optional model is not inferred or calibrated by the mass-loss source.',
 'Gas1 finite N2 supplies sample N2 plus independent balance N2. Gas2 finite O2 supplies sample only. dn=q_ref*p_ref/(R*T_ref)*dt. Chamber initial N2 is accounted. Fill the finite O2-consumption deficit first; remaining inlet performs exact exponential CSTR replacement exp(-dn/n_capacity), explicitly exhausting each species. Consumed O2 lowers total chamber moles/pressure until finite feed replenishes it; no free N2.',
 'Reference-volume chamber is isothermal at explicitly supplied mixing T; p=nRT/V is not furnace hot-gas pressure. Unknown evolved volatile mass is tracked separately without guessed molecular weight; pyrolysis gas composition, pressure and sensible gas transport are not solved.',
 'Es=(Cpan+mCp)*(Ts-Tamb); Ef=Cf*(Tf-Tamb). Emitted dm exports dm*Cp*(Ts_old-Tamb); incoming O2 has zero reference sensible enthalpy at ambient, explicit reaction heat q=dm_loss*h_loss+dm_O2*h_ox. Update finite sample energy, then solve the two-node thermal coupling implicitly. No Cp-change heat is invented.',
 'Cf*dTf/dt=Pheater-Gambient*(Tf-Tamb)-Gcool*(Tf-Tamb)-Gs*(Tf-Ts); dEs/dt=Gs*(Tf-Ts)+reaction_heat-emitted_sensible. Heater=clip(K*(Tcontrol-Tf),0,Pmax) with Tcontrol=(Tprogram/temperature_gain_correction+temperature_zero_k-temperature_offset_k)/temperature_gain: the controller holds the device reading (same affine map as the indicated temperatures) at the program temperature, so sensor errors and the stored temperature calibration move the furnace; identity with the default zero errors and no calibration. Ambient/forced-cooling exchanges are signed. Gcool draws heat to ambient and cannot drive a passive body below ambient; heat removed remains in ledger. Whole-instrument1200W is NOT a measured heater rating.',
 'Independent mass signal first-order approaches gain*(pan_mass+sample_mass)+offset; external calibration and empty-pan observed tare precede quantization8ng/60ng. Overload is unavailable, not a clipped sample truth. Furnace/sample temperatures are gain/offset observation proxies; no invented empirical noise or accuracy certification.',
 'analysis.mass_stages consumes external observed time/T/mass/blank and independently supplied initial weighed mass. Explicit index windows yield signed mass changes, residual fraction and finite-difference DTG peak midpoint temperatures. This does not identify chemistry, source true residue or kinetic activation energy.',
 'Pan domain: ceramic1473.15K, platinum1273.15K, aluminum873.15K, each ambient lower bound. The full furnace1200°C envelope is not valid for every pan. Linear ramp.01..500K/min; no unsupported ballistic algorithm.',
 'Four engineering mechanisms: cold IR furnace vertical opening.11m;25-position shortest-path carousel;index1-only .016m punch demo;power. Autosampler load / unload move a carousel pan onto or off the balance as a logical transfer when the carousel reaches the position (no hook contact model or scene reparenting); a pan returned after heating holds its residue and is not reloaded; no physical lid rupture. Cold/load guards protect finite specimen and balance.'
 ],parameters={},source_ids=['manufacturer_specs','simulation_assumption','fsri_sta_2023','zenodo_23013247_phytochemical_tga'],limitations=['No OEM HiRes/MTGA/MS/FTIR proprietary algorithm or calibrated sensor noise.','Reaction enthalpy, Cp, conductance, relaxation and optional O2 kinetics explicitly assumed.','Finite balance N2 stream is accounted but thermal entrainment/buoyancy of purge is not modeled.','Loss species mass reservoir has no asserted molecular composition, chamber pressure feedback or ideal-gas conversion.','After a model-domain fault reactions halt until sample removal/reload; no unsupported extrapolation or free restoration.','Joint/mesh and Matterix multi-env validation are separate; no autonomous physical pan pickup.'])
 spec=importlib.util.spec_from_file_location('_tga_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()});ann.annotate_export(SLUG,caps,slots,{'tga_conservation':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'gas.load':(GAS,GAS_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sensor.calibrate':(CALIBRATION,CALIBRATION_EXAMPLE)})
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'tga_conservation':mechanism})

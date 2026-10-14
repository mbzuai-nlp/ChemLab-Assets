"""CHEM134 TMA: source-anchored thermal expansion and finite-load compression."""
from pathlib import Path
import copy,json,math,importlib.util,bisect
import numpy as np
PROTOCOL_VERSION='1.0.0';SLUG='ta_tma450';CATALOG_ID='CHEM-134';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch04/t1_thermal'/SLUG
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
def library():
 p=ASSET/'tma_library.json';return json.loads(p.read_text()) if p.exists() else {'substances':{}}

PLANT_EXAMPLE=dict(accept_engineering=True,ambient_k=303.15,cooler='none',furnace_capacity_j_k=10.,fixture_capacity_j_k=1.,sample_conductance_w_k=2.,ambient_conductance_w_k=.05,forced_cooling_conductance_w_k=.08,controller_gain_w_k=40.,heater_max_w=200.,cooler_max_w=40.,safe_access_temperature_k=313.15,force_servo_tau_s=.02,displacement_sensor_tau_s=.02,force_sensor_tau_s=.01,force_gain=1.,force_offset_n=0.,displacement_gain=1.,displacement_offset_m=0.,frame_drift_m_k=0.,temperature_gain=1.,temperature_offset_k=0.,displacement_quantum_m=2.5e-10,probe_speed_m_s=.002,furnace_speed_m_s=.01,coolant_latent_j_kg=199000.,coolant_efficiency=.7)
PLANT=obj({'accept_engineering':BOOL,'ambient_k':number('K',283.15,313.15),'cooler':enum(['none','LN2']),'furnace_capacity_j_k':number('J/K',1e-5,1e4),'fixture_capacity_j_k':number('J/K',1e-7,1000),'sample_conductance_w_k':number('W/K',1e-7,1000),'ambient_conductance_w_k':number('W/K',0,1000),'forced_cooling_conductance_w_k':number('W/K',0,1000),'controller_gain_w_k':number('W/K',1e-5,1e5),'heater_max_w':number('W',1e-5,1e5),'cooler_max_w':number('W',0,1e5),'safe_access_temperature_k':number('K',283.15,373.15),'force_servo_tau_s':number('s',1e-8,100),'displacement_sensor_tau_s':number('s',1e-8,100),'force_sensor_tau_s':number('s',1e-8,100),'force_gain':number('1',.01,100),'force_offset_n':number('N',-2,2),'displacement_gain':number('1',.01,100),'displacement_offset_m':number('m',-.01,.01),'frame_drift_m_k':number('m/K',-1e-5,1e-5),'temperature_gain':number('1',.5,1.5),'temperature_offset_k':number('K',-20,20),'displacement_quantum_m':number('m',1e-12,4.99e-10),'probe_speed_m_s':number('m/s',1e-5,.05),'furnace_speed_m_s':number('m/s',1e-4,.1),'coolant_latent_j_kg':number('J/kg',1,1e7),'coolant_efficiency':number('1',.01,1)})
SAMPLE_EXAMPLE=dict(reference_id='ptfe_tma450em_expansion',length_m=.0016233939208984375,diameter_m=.006400000095,mass_kg=.0001,heat_capacity_j_kg_k=1000.,reference_temperature_k=303.15,elastic_stiffness_n_m=1e8,retarded_stiffness_n_m=1e10,relaxation_s=10.,activation_j_mol=0.,maximum_compression_fraction=.1,accept_engineering_compliance=True,accept_condition_transfer=True,accept_reference_processing=True)
SAMPLE=obj({'reference_id':TEXT,'length_m':number('m',1e-6,.026),'diameter_m':number('m',1e-6,.010),'mass_kg':number('kg',1e-12,.1),'heat_capacity_j_kg_k':number('J/(kg*K)',1,1e5),'reference_temperature_k':number('K',123.15,1273.15),'elastic_stiffness_n_m':number('N/m',1,1e14),'retarded_stiffness_n_m':number('N/m',1,1e14),'relaxation_s':number('s',1e-8,1e8),'activation_j_mol':number('J/mol',0,3e5),'maximum_compression_fraction':number('1',1e-8,.5),'accept_engineering_compliance':BOOL,'accept_condition_transfer':BOOL,'accept_reference_processing':BOOL})
GAS_EXAMPLE=dict(port=1,gas='N2',amount_mol=1.,reference_temperature_k=298.15,reference_pressure_pa=101325.,supply_pressure_gauge_pa=100000.,accept_reference_conditions=True)
GAS=obj({'port':integer(1,2),'gas':enum(['N2','Ar','He','air']),'amount_mol':number('mol',1e-18,10000),'reference_temperature_k':number('K',200,500),'reference_pressure_pa':number('Pa',10000,1e7),'supply_pressure_gauge_pa':number('Pa',70000,140000),'accept_reference_conditions':BOOL})
COOLANT_EXAMPLE=dict(amount_kg=.1,accept_engineering_latent=True);COOLANT=obj({'amount_kg':number('kg',1e-12,100),'accept_engineering_latent':BOOL})
CALIBRATION_EXAMPLE=dict(force_gain_correction=1.,force_zero_n=0.,displacement_gain_correction=1.,temperature_gain_correction=1.,temperature_zero_k=0.)
CALIBRATION=obj({'force_gain_correction':number('1',.01,100),'force_zero_n':number('N',-2,2),'displacement_gain_correction':number('1',.01,100),'temperature_gain_correction':number('1',.1,10),'temperature_zero_k':number('K',-100,100)})
SEGMENT=obj({'target_temperature_k':number('K',123.15,1273.15),'temperature_rate_k_s':number('K/s',.01/60,150/60),'motor_force_n':number('N',.001,1),'force_rate_n_s':number('N/s',1e-6,10),'hold_s':number('s',0,1e5)})
METHOD_EXAMPLE=dict(start_temperature_k=303.15,start_tolerance_k=.5,segments=[dict(target_temperature_k=323.15,temperature_rate_k_s=150/60,motor_force_n=.05,force_rate_n_s=.1,hold_s=5.)],sample_interval_s=.1,timeout_s=1e5,accept_condition_transfer=True)
METHOD=obj({'start_temperature_k':number('K',123.15,1273.15),'start_tolerance_k':number('K',.001,2),'segments':array(SEGMENT,1,100),'sample_interval_s':number('s',.1,1000),'timeout_s':number('s',.1,1e6),'accept_condition_transfer':BOOL})
ROW=obj({'time_s':number('s',0),'elapsed_s':number('s',0),'segment_index':integer(0,99),'stage':enum(['ramp','hold']),'temperature_k':nullable(number('K')),'furnace_k':nullable(number('K')),'displacement_m':nullable(number('m')),'force_n':nullable(number('N'))})
RESULT=obj({'operation_id':nullable(TEXT),'phase':PHASE,'elapsed_s':number('s',0),'rows':array(ROW,0,2000000),'reference_id':nullable(TEXT),'template_source':nullable(TEXT),'processing_json':nullable(TEXT),'method_json':nullable(TEXT),'calibration_json':nullable(TEXT),'evidence':TEXT,'error_code':nullable(TEXT)})

def first_order_linear(value,initial_target,final_target,h,tau):
 r=h/tau;one=-math.expm1(-r);ramp=r/2-r*r/6+r**3/24 if r<1e-4 else 1-one/r
 return value*(1-one)+initial_target*one+(final_target-initial_target)*ramp

def kelvin_step(force0_n,force1_n,retarded_m,elastic_stiffness_n_m,retarded_stiffness_n_m,relaxation_s,dt_s):
 """PWL actual force; series elastic spring + Kelvin element. No instrument creep mode."""
 if min(elastic_stiffness_n_m,retarded_stiffness_n_m,relaxation_s,dt_s)<=0:raise ActionRejected('COMPLIANCE_DOMAIN')
 ke=elastic_stiffness_n_m;kv=retarded_stiffness_n_m;tau=relaxation_s;h=dt_s;r=h/tau
 new=first_order_linear(retarded_m,force0_n/kv,force1_n/kv,h,tau);a=(force0_n/kv-retarded_m)/tau;b=(force1_n-force0_n)/(h*kv)
 if r<1e-3:
  # Positive Gaussian integration avoids cancellation for almost-linear velocity.
  nodes=[.046910077030668,.230765344947158,.5,.769234655052842,.953089922969332];weights=[.118463442528095,.239314335249683,.284444444444444,.239314335249683,.118463442528095]
  integral=h*sum(w*(a*math.exp(-r*x)+b*(-math.expm1(-r*x)))**2 for x,w in zip(nodes,weights))
 else:
  c=a-b;integral=b*b*h+2*b*c*tau*(-math.expm1(-r))+c*c*tau*(-math.expm1(-2*r))/2
 diss=max(0.,kv*tau*integral);old=force0_n**2/(2*ke)+kv*retarded_m**2/2;stored=force1_n**2/(2*ke)+kv*new**2/2
 return dict(retarded_m=new,elastic_m=force1_n/ke,stored_j=stored,previous_stored_j=old,dissipated_j=diss,compression_work_j=stored-old+diss)

def prepare_reference(record,accept_processing):
 if not record or not record.get('runtime_usable'):raise ActionRejected('INVALID_REFERENCE')
 raw=record.get('runtime_values') or record.get('values',{});processed=record.get('runtime_values') is not None
 if processed and not accept_processing:raise ActionRejected('REFERENCE_PROCESSING_NOT_ACCEPTED')
 try:T=np.asarray(raw['temperature_k'],float)
 except (KeyError,TypeError,ValueError):raise ActionRejected('INVALID_REFERENCE')
 if T.ndim!=1 or len(T)<2 or not np.all(np.isfinite(T)) or np.any(np.diff(T)<=0):raise ActionRejected('REFERENCE_NOT_MONOTONE')
 basis=record.get('conditions',{}).get('cte_definition','unknown')
 if 'expansion_ratio' in raw:
  y=np.asarray(raw['expansion_ratio'],float);mode='expansion_ratio'
 elif 'linear_cte_k_inv' in raw:
  alpha=np.asarray(raw['linear_cte_k_inv'],float)
  if alpha.shape!=T.shape or not np.all(np.isfinite(alpha)):raise ActionRejected('INVALID_REFERENCE')
  y=np.r_[0.,np.cumsum(np.diff(T)*(alpha[1:]+alpha[:-1])/2)];mode='integrated_cte'
  if basis=='instantaneous_dlogL_dT':y=np.expm1(y)
  elif basis!='engineering_dstrain_dT':raise ActionRejected('UNKNOWN_CTE_BASIS')
 else:raise ActionRejected('INVALID_REFERENCE')
 if y.shape!=T.shape or not np.all(np.isfinite(y)) or np.any(1+y<=0):raise ActionRejected('INVALID_REFERENCE')
 domain=record.get('runtime_domain',{});lo=max(float(T[0]),domain.get('minimum_temperature_k',-math.inf));hi=min(float(T[-1]),domain.get('maximum_temperature_k',math.inf))
 if lo>=hi:raise ActionRejected('INVALID_REFERENCE')
 return dict(T=T,y=y,minimum_k=lo,maximum_k=hi,mode=mode,processing=dict(uses_runtime_values=processed,points=len(T),temperature_order='Original increasing branch or published CTE table. No runtime sorting, smoothing or clipping.',cte_definition=basis,source_processing=record.get('processing',record.get('runtime_processing',{}))))

def fit_cte(temperature_k,displacement_m,blank_m,initial_length_m,start_index,end_index):
 T=np.asarray(temperature_k,float);d=np.asarray(displacement_m,float);blank=np.asarray(blank_m,float)
 if T.ndim!=1 or len(T)<3 or T.shape!=d.shape or T.shape!=blank.shape or not all(np.all(np.isfinite(x)) for x in [T,d,blank]) or not 0<=start_index<end_index<len(T) or end_index-start_index<2 or initial_length_m<=0:raise ActionRejected('ANALYSIS_DATA')
 x=T[start_index:end_index+1];y=(d-blank)[start_index:end_index+1]
 if np.ptp(x)<1e-6 or np.any(np.diff(x)<-1e-7):raise ActionRejected('ANALYSIS_TEMPERATURE_WINDOW')
 origin=float(np.mean(x));M=np.column_stack([np.ones(len(x)),x-origin]);coef=np.linalg.lstsq(M,y,rcond=None)[0];res=y-M@coef;slope=float(coef[1]);return dict(linear_cte_k_inv=slope/initial_length_m,secant_cte_k_inv=float((y[-1]-y[0])/(initial_length_m*(x[-1]-x[0]))) if x[-1]!=x[0] else slope/initial_length_m,slope_m_k=slope,fit_rms_m=float(np.sqrt(np.mean(res*res))),temperature_min_k=float(min(x)),temperature_max_k=float(max(x)),points=len(x),evidence='Regression of external observed displacement minus supplied blank versus observed sample temperature, normalized by independently supplied reference length. Engineering CTE uses1/Lref; probe loading, source transfer and calibration uncertainty are not removed automatically.')

def softening_intersection(temperature_k,displacement_m,blank_m,first_window,second_window):
 T=np.asarray(temperature_k,float);d=np.asarray(displacement_m,float)-np.asarray(blank_m,float)
 if T.ndim!=1 or T.shape!=d.shape or not np.all(np.isfinite(T)) or not np.all(np.isfinite(d)) or np.any(np.diff(T)<-1e-7):raise ActionRejected('ANALYSIS_DATA')
 a,b=first_window;c,e=second_window
 if not 0<=a<b<c<e<len(T) or b-a<2 or e-c<2:raise ActionRejected('ANALYSIS_WINDOWS')
 fitted=[]
 for i,j in [(a,b),(c,e)]:
  x=T[i:j+1];y=d[i:j+1]
  if np.ptp(x)<1e-6:raise ActionRejected('ANALYSIS_WINDOWS')
  M=np.column_stack([np.ones(len(x)),x-T[0]]);coef=np.linalg.lstsq(M,y,rcond=None)[0];fitted.append(coef)
 diff=fitted[0][1]-fitted[1][1];cross=float(T[0]+(fitted[1][0]-fitted[0][0])/diff) if abs(diff)>1e-15 else None;ok=cross is not None and T[b]<=cross<=T[c]
 return dict(softening_temperature_k=cross if ok else None,bracketed=bool(ok),first_slope_m_k=float(fitted[0][1]),second_slope_m_k=float(fitted[1][1]),evidence='Intersection of two explicitly chosen observed displacement/temperature regression windows. Only an intersection bracketed between the windows is reported; this is method/load-dependent apparent softening, not hidden Tg or intrinsic material identity.')
CAPS={};SLOTS={}
def cap(k,i,o,c,z,kind='device_command',visibility='agent',example=None):CAPS[k]=dict(input_schema=i,output_schema=o,component_ids=c,function_zh=z,kind=kind,visibility=visibility,example_arguments=copy.deepcopy(example or {}),effects=[])
def slot(k,s,u,z,origin='derived',visibility='agent',component='controller'):SLOTS[k]=dict(schema=s,unit=u,meaning=z,origin=origin,visibility=visibility,component=component,write=None)
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller','furnace','force_coil','lvdt'],'显式热/气/力伺服/观测工程参数','configuration','evaluator',PLANT_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['specimen','sample_platform'],'实测尺寸响应及显式有限试样/附加压缩模型','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.remove',obj({}),obj({'removed':BOOL}),['specimen'],'冷态离样回收有限样片/剩余内能','configuration','evaluator')
cap('gas.load',GAS,obj({'loaded':BOOL}),['controller'],'两个入口有限purge及显式参考温压','configuration','evaluator',GAS_EXAMPLE)
cap('gas.unload',obj({'port':integer(1,2)}),obj({'unloaded':BOOL}),['controller'],'关阀回收有限气源','configuration','evaluator',{'port':1})
cap('gas.select',obj({'port':integer(1,2)}),obj({'selected':integer(1,2)}),['controller'],'选择两入口之一，未声称腔气氛瞬时置换',example={'port':1})
cap('gas.set_flow',obj({'flow_m3_s':number('m^3/s',0,200e-6/60)}),obj({'flow_m3_s':number('m^3/s',0,200e-6/60)}),['controller'],'显式参考体积吹扫流量',example={'flow_m3_s':100e-6/60})
cap('gas.set_open',obj({'opened':BOOL}),obj({'opened':BOOL}),['controller'],'开关有限吹扫源；关闭停止主动热控制',example={'opened':True})
cap('coolant.load',COOLANT,obj({'loaded':BOOL}),['furnace'],'LN2有限冷量工程模型','configuration','evaluator',COOLANT_EXAMPLE)
cap('coolant.unload',obj({}),obj({'unloaded':BOOL}),['furnace'],'停止制冷后回收有限液氮','configuration','evaluator')
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_switch'],'供电/停止活动保留余热',example={'enabled':True})
cap('furnace.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['furnace'],'实际50mm有限速度炉体升降',example={'opened':False})
cap('probe.select',obj({'kind':enum(['expansion','penetration'])}),obj({'kind':enum(['expansion','penetration'])}),['probe'],'冷态离样手工选探针，penetration仅显式工程过程附件',example={'kind':'expansion'})
cap('probe.move',obj({'position_m':number('m',0,.030)}),obj({'accepted':BOOL}),['probe'],'离样探针有限速位置，零点为样品平台',example={'position_m':.028})
cap('probe.contact',obj({'engaged':BOOL}),obj({'accepted':BOOL}),['probe','force_coil'],'有限接近后接触/有限卸载后抬起',example={'engaged':True})
cap('load.set',obj({'motor_force_n':number('N',.001,1),'rate_n_s':number('N/s',1e-6,10)}),obj({'target_force_n':number('N',.001,2)}),['force_coil','probe'],'数字电机.001..1N有限ramp/servo，附加砝码另算',example={'motor_force_n':.05,'rate_n_s':.1})
cap('load.deadweight',obj({'force_n':number('N',0,1)}),obj({'force_n':number('N',0,1)}),['probe'],'冷态离样人工砝码等效重力；总载荷不超过2N',example={'force_n':0.})
cap('thermal.set_temperature',obj({'temperature_k':number('K',123.15,1273.15),'rate_k_s':number('K/s',.01/60,150/60)}),obj({'target_k':number('K',123.15,1273.15)}),['furnace'],'有限温程目标，真实两节点热量守恒',example={'temperature_k':313.15,'rate_k_s':150/60})
cap('thermal.disable',obj({}),obj({'enabled':BOOL}),['furnace'],'停止主动温控并保留实际余热')
cap('thermal.set_cooling',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['furnace'],'环境空气热汇强制冷却，冷却量入账',example={'enabled':True})
cap('sensor.calibrate',CALIBRATION,CALIBRATION,['lvdt','force_coil'],'外部观测增益/零点校正','configuration',example=CALIBRATION_EXAMPLE)
cap('displacement.zero',obj({}),obj({'zeroed':BOOL}),['lvdt'],'静态接触时观测位移归零，测量域±2.5mm')
cap('method.configure',METHOD,METHOD,['controller'],'温度/载荷联合ramp和hold；真实源工况迁移显式接受','configuration',example=METHOD_EXAMPLE)
cap('acquisition.start',obj({}),obj({'operation_id':TEXT}),['controller','furnace','probe','lvdt'],'异步真实温程/负载下长度变化观测')
cap('acquisition.stop',obj({}),obj({'phase':enum(['cancelled'])}),['controller','furnace'],'停止程序，保留实际载荷/压缩/余热')
cap('acquisition.result',obj({}),RESULT,['controller'],'冻结观测曲线及来源，owner限定','query')
cap('reference.query',obj({'reference_id':TEXT}),obj({'reference_json':TEXT,'template_source':TEXT}),['controller'],'原始/处理源数组、明确EM迁移与参考CTE','query',example={'reference_id':SAMPLE_EXAMPLE['reference_id']})
ANALYSIS_INPUT={'temperature_k':array(number('K'),3,2000000),'displacement_m':array(number('m'),3,2000000),'blank_m':array(number('m'),3,2000000)}
CTE_RESULT=obj({'linear_cte_k_inv':number('1/K'),'secant_cte_k_inv':number('1/K'),'slope_m_k':number('m/K'),'fit_rms_m':number('m',0),'temperature_min_k':number('K'),'temperature_max_k':number('K'),'points':integer(3,2000000),'evidence':TEXT})
cap('analysis.cte',obj(dict(ANALYSIS_INPUT,initial_length_m=number('m',1e-9,1),start_index=integer(0,1999999),end_index=integer(0,1999999))),CTE_RESULT,['controller'],'外部观测减blank后按外部L0归一化求CTE','analysis',example={'temperature_k':[300.,310.,320.],'displacement_m':[0.,1e-6,2e-6],'blank_m':[0.,0.,0.],'initial_length_m':.01,'start_index':0,'end_index':2})
cap('analysis.softening',obj(dict(ANALYSIS_INPUT,first_window=array(integer(0,1999999),2,2),second_window=array(integer(0,1999999),2,2))),obj({'softening_temperature_k':nullable(number('K')),'bracketed':BOOL,'first_slope_m_k':number('m/K'),'second_slope_m_k':number('m/K'),'evidence':TEXT}),['controller'],'两观测窗口交点，未包围则null','analysis',example={'temperature_k':[300.,310.,320.,330.,340.,350.,360.],'displacement_m':[0.,1e-6,2e-6,3e-6,2e-6,1e-6,0.],'blank_m':[0.]*7,'first_window':[0,2],'second_window':[4,6]})
LEDGER_UNITS={k:u for k,u in [('sample_loaded_kg','kg'),('sample_present_kg','kg'),('sample_recovered_kg','kg'),('sample_error_kg','kg'),('coolant_loaded_kg','kg'),('coolant_remaining_kg','kg'),('coolant_evaporated_kg','kg'),('coolant_recovered_kg','kg'),('coolant_error_kg','kg'),('mechanical_input_j','J'),('mechanical_recovered_j','J'),('elastic_stored_j','J'),('dissipated_j','J'),('expansion_work_j','J'),('recovered_elastic_j','J'),('mechanical_error_j','J'),('heater_input_j','J'),('ambient_loss_j','J'),('cooling_removed_j','J'),('coolant_cold_used_j','J'),('coolant_cold_error_j','J'),('furnace_stored_j','J'),('sample_stored_j','J'),('recovered_sensible_j','J'),('thermal_error_j','J')]}
for g in ['port1','port2']:
 for k in ['loaded','remaining','exhausted','recovered','error']:LEDGER_UNITS[g+'_'+k+'_mol']='mol'
cap('ledger.read',obj({}),obj({k:number(u) for k,u in LEDGER_UNITS.items()}),['controller'],'有限样片/两气源/LN2及机械-热能账本','query','evaluator')
cap('sim.step',obj({'dt_s':number('s',0,3600)}),obj({'time_s':number('s',0)}),['controller'],'宿主唯一时钟','simulation','evaluator',{'dt_s':.1})
cap('emergency.stop',obj({}),obj({'latched':BOOL}),['controller'],'软件停机，保持物理载荷/压缩/余热')
cap('emergency.reset',obj({}),obj({'latched':BOOL}),['controller'],'冷态显式复位，不自动续程')
UNSUPPORTED=['isostrain.native','creep.em','stress_relaxation.em','dynamic.em','modulated.em','fixture.tension','fixture.flexure','fixture.volumetric','cooler.mca70','noise.empirical','accuracy.certification','robot.load','hardware.connect','sample.fracture']
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'本配置未实现：'+k)
for k,c in [('plant.configured','controller'),('power.enabled','power_switch'),('sample.present','specimen'),('gas.opened','controller'),('thermal.enabled','furnace'),('cooling.enabled','furnace'),('emergency.latched','controller'),('furnace.opened','furnace'),('furnace.moving','furnace'),('probe.engaged','probe'),('probe.moving','probe'),('displacement.zeroed','lvdt')]:slot(k,BOOL,'1','实际状态/工程互锁',component=c)
for k,lo,hi,c,visibility in [('furnace.lift_m',0,.05,'furnace','agent'),('furnace.target_m',0,.05,'furnace','agent'),('probe.position_m',0,.030,'probe','evaluator'),('probe.target_m',0,.030,'probe','evaluator')]:slot(k,number('m',lo,hi),'m','平台为probe零高、+Z向上；有限真实位置/目标','setpoint' if 'target' in k else 'scene',visibility,component=c)
slot('sample.height_m',number('m',0,.030),'m','热响应/载荷后的真实高度，用于几何；评估器可见','process_truth','evaluator','specimen')
slot('power.angle_rad',number('rad',0,.28),'rad','开关姿态','scene',component='power_switch');slot('probe.kind',enum(['expansion','penetration']),'1','手工探针配置');slot('probe.mode',enum(['hold','move','approach','contact','release']),'1','实际探针运动/接触状态')
for k in ['load.motor_target_n','load.motor_program_n']:slot(k,number('N',0,1),'N','数字电机目标及有限ramp设定，不是真实载荷','setpoint',component='force_coil')
slot('load.deadweight_n',number('N',0,1),'N','手工砝码等效重力','setpoint',component='probe');slot('gas.selected_port',integer(1,2),'1','选中气源入口，不代表腔气氛瞬变','setpoint');slot('gas.flow_m3_s',number('m^3/s',0,200e-6/60),'m^3/s','所选有限气源的显式参考体积流量','setpoint')
for k in ['thermal.target_k','thermal.final_target_k']:slot(k,number('K',123.15,1273.15),'K','有限速温度目标','setpoint',component='furnace')
for k,u in [('force.indicated_n','N'),('displacement.indicated_m','m'),('furnace.indicated_k','K'),('sample.indicated_k','K')]:slot(k,nullable(number(u)),u,'独立工程观测通道，未标定noise/accuracy',component='lvdt' if k.startswith('displacement') else 'controller')
for k,u in [('truth.force_n','N'),('truth.elastic_j','J'),('truth.retarded_m','m'),('truth.natural_length_m','m'),('truth.sample_k','K'),('truth.furnace_k','K'),('truth.port1_mol','mol'),('truth.port2_mol','mol'),('truth.coolant_kg','kg'),('truth.sample_mass_kg','kg')]:slot(k,number(u),u,'过程真值，仅评估器','process_truth','evaluator')
slot('model.fault',nullable(TEXT),'1','过程域失败原因，不含材料隐藏参数');slot('clock.time_s',number('s',0),'s','唯一宿主时钟');slot('acquisition.phase',PHASE,'1','异步状态');slot('acquisition.elapsed_s',number('s',0),'s','实际采集时间');slot('acquisition.points',integer(0,2000000),'1','实际记录点');slot('acquisition.segment_index',integer(0,99),'1','温程区块');slot('acquisition.stage',enum(['ramp','hold']),'1','实际程序阶段');slot('panel.temperature_text',TEXT,'1','C一位小数，断电空');slot('panel.displacement_text',TEXT,'1','µm三位小数，断电或未归零空')

def coupled_thermal_step(plant,furnace_k,sample_energy_j,sample_capacity_j_k,dt_s,target_k,enabled,air_cooling,coolant_kg,dissipated_j,force_mean_n,length_old_m,length_function):
 """Implicit two-node energy balance including compressive work opposing expansion."""
 p=plant;h=dt_s;amb=p['ambient_k'];Cf=p['furnace_capacity_j_k'];Cs=sample_capacity_j_k;G=p['sample_conductance_w_k'];Ga=p['ambient_conductance_w_k']+(p['forced_cooling_conductance_w_k'] if air_cooling else 0.);K=p['controller_gain_w_k'] if enabled else 0.;cold=min(p['cooler_max_w'],coolant_kg*p['coolant_latent_j_kg']*p['coolant_efficiency']/h) if p['cooler']=='LN2' else 0.
 def evaluate(Ts):
  tf0=furnace_k-amb;s=Ts-amb;tf=(Cf/h*tf0+G*s+K*(target_k-amb))/(Cf/h+Ga+G+K);P=K*(target_k-amb-tf)
  if P>p['heater_max_w']:P=p['heater_max_w'];tf=(Cf/h*tf0+G*s+P)/(Cf/h+Ga+G)
  if P<-cold:P=-cold;tf=(Cf/h*tf0+G*s+P)/(Cf/h+Ga+G)
  dl=length_function(Ts)-length_old_m;res=Cs*s-sample_energy_j-dissipated_j+force_mean_n*dl-h*G*(tf-s)
  return res,tf+amb,P,dl
 # Broad thermodynamic bracket permits detecting physical temperature-domain violations without clipping energy.
 lo=1.;hi=max(2000.,furnace_k+abs(sample_energy_j+dissipated_j)/max(Cs,1e-20)+100.)
 rlo=evaluate(lo)[0];rhi=evaluate(hi)[0]
 if rlo>0 or rhi<0:raise ActionRejected('THERMAL_MODEL_RANGE')
 x=max(lo,min(hi,amb+sample_energy_j/Cs))
 for _ in range(60):
  residual,Tf,P,dl=evaluate(x)
  if abs(residual)<max(1e-13,16*np.finfo(float).eps*max(1.,Cs*abs(x),h*G*abs(x))):break
  if residual>0:hi=x
  else:lo=x
  delta=max(1e-4,abs(x)*1e-7);slope=(evaluate(x+delta)[0]-evaluate(x-delta)[0])/(2*delta)
  if slope<=0:raise ActionRejected('THERMOMECHANICAL_CAPACITY')
  candidate=x-residual/slope;x=candidate if lo<candidate<hi else (lo+hi)/2
 else:raise ActionRejected('THERMAL_CONVERGENCE')
 return dict(furnace_k=float(Tf),sample_k=float(x),sample_energy_j=float(Cs*(x-amb)),heater_j=float(max(P,0)*h),cooler_j=float(max(-P,0)*h),ambient_j=float(Ga*(Tf-amb)*h),expansion_work_j=float(-force_mean_n*dl))

class TaTma450Runtime:
 def __init__(self,instance_id='tma450_01'):
  self.instance_id=instance_id;self.plant=None;self.sample=None;self.reference=None;self.model=None;self.power=False;self.emergency=False;self.furnace_lift=.05;self.furnace_target=.05;self.probe=.028;self.probe_target=.028;self.probe_mode='hold';self.probe_kind='expansion';self.force=0.;self.motor_set=.05;self.motor_program=.05;self.force_rate=.1;self.deadweight=0.;self.retarded=0.;self.natural=0.;self.model_fault=None;self.gases={};self.gas_remaining={1:0.,2:0.};self.gas_loaded={1:0.,2:0.};self.gas_used={1:0.,2:0.};self.gas_recovered={1:0.,2:0.};self.gas_open=False;self.gas_selected=1;self.gas_flow=100e-6/60;self.coolant_present=False;self.coolant_remaining=0.;self.coolant_loaded=0.;self.coolant_used=0.;self.coolant_recovered=0.;self.coolant_cold_used=0.;self.thermal_enabled=False;self.air_cooling=False;self.target=303.15;self.final_target=303.15;self.rate=2.5;self.furnace=303.15;self.sample_temperature=303.15;self.sample_energy=0.;self.heater_input=0.;self.cooling_removed=0.;self.ambient_loss=0.;self.recovered_sensible=0.;self.mechanical_input=0.;self.mechanical_recovered=0.;self.dissipated=0.;self.expansion_work=0.;self.recovered_elastic=0.;self.mass_loaded=0.;self.mass_recovered=0.;self.obs_force=0.;self.obs_displacement=.028;self.displacement_zero=0.;self.zeroed=False;self.calibration=copy.deepcopy(CALIBRATION_EXAMPLE);self.method=copy.deepcopy(METHOD_EXAMPLE);self.phase='idle';self.stage='ramp';self.elapsed=0.;self.segment_index=0;self.hold_elapsed=0.;self.rows=[];self.error=None;self.pending_fault=None;self.operation_id=None;self.operation_reference=None;self.operation_source=None;self.operation_processing=None;self.operation_method=None;self.operation_calibration=None;self.records={};self._counter=0;self.time_s=0.;self._ticks=0;self._remainder=0.;self.revision=0;self._requests={};self._owners={};self._cancel_operations={};self._principals={'agent':'agent','evaluator':'evaluator'}
 def register_principal(self,principal_id,visibility='agent'):
  if not isinstance(principal_id,str) or not principal_id or principal_id in self._principals or visibility not in ('agent','debug','evaluator'):raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='agent'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return TaTma450Session(self,principal_id)
 def describe(self):return export_ta_tma450_contract_metadata()
 def _cp(self):return self.plant['fixture_capacity_j_k']+(self.sample['mass_kg']*self.sample['heat_capacity_j_kg_k'] if self.sample else 0.)
 def _length(self,T):
  if not self.sample:return 0.
  if self.model_fault:return self.natural
  y=float(np.interp(T,self.model['T'],self.model['y']));y0=float(np.interp(self.sample['reference_temperature_k'],self.model['T'],self.model['y']))
  return self.sample['length_m']*((1+y)/(1+y0) if (self.reference.get('conditions',{}).get('reference_temperature_k') is not None or self.model['processing']['cte_definition']=='instantaneous_dlogL_dT') else 1+y-y0)
 def _elastic(self):return self.force*self.force/(2*self.sample['elastic_stiffness_n_m'])+self.sample['retarded_stiffness_n_m']*self.retarded*self.retarded/2 if self.sample else 0.
 def _height(self):return self.natural-self.force/self.sample['elastic_stiffness_n_m']-self.retarded if self.sample else 0.
 def _cold(self):return max(self.furnace,self.sample_temperature)<=self.plant['safe_access_temperature_k']
 def _moving(self):return bool(abs(self.furnace_lift-self.furnace_target)>1e-12 or self.probe_mode in ['move','approach','release'])
 def _mechanical(self):
  if self.emergency:raise ActionRejected('EMERGENCY_LATCHED')
  if self.phase=='running':raise ActionRejected('BUSY')
  if not self._cold():raise ActionRejected('HOT_CELL')
  if self.thermal_enabled:raise ActionRejected('THERMAL_CONTROL_ACTIVE')
  if self._moving():raise ActionRejected('MECHANISM_MOVING')
 def _temperature_domain(self,T,source=True):
  if not (123.15 if self.plant['cooler']=='LN2' else self.plant['ambient_k'])<=T<=1273.15:raise ActionRejected('COOLER_TEMPERATURE_RANGE')
  if source and self.sample and not self.model['minimum_k']<=T<=self.model['maximum_k']:raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
 def _flow_ready(self,T):return self.gas_open and self.gas_remaining[self.gas_selected]>0 and self.gas_flow>=(200e-6/60 if T<173.15 else 100e-6/60)-1e-15
 def _indicated(self):
  if not self.power or not self.plant:return dict(force=None,displacement=None,furnace=None,sample=None)
  p=self.plant;c=self.calibration;F=(self.obs_force-c['force_zero_n'])*c['force_gain_correction'];x=(self.obs_displacement-self.displacement_zero)*c['displacement_gain_correction'];Q=p['displacement_quantum_m'];return dict(force=float(F) if 0<=F<=2 else None,displacement=float(round(x/Q)*Q) if self.zeroed and abs(x)<=.0025 else None,furnace=float((p['temperature_gain']*self.furnace+p['temperature_offset_k']-c['temperature_zero_k'])*c['temperature_gain_correction']),sample=float((p['temperature_gain']*self.sample_temperature+p['temperature_offset_k']-c['temperature_zero_k'])*c['temperature_gain_correction']))
 def read(self,role='evaluator'):
  a=self._indicated();d={'plant.configured':self.plant is not None,'power.enabled':self.power,'sample.present':self.sample is not None,'gas.opened':self.gas_open,'thermal.enabled':self.thermal_enabled,'cooling.enabled':self.air_cooling,'emergency.latched':self.emergency,'furnace.opened':self.furnace_lift>=.05-1e-12,'furnace.moving':abs(self.furnace_lift-self.furnace_target)>1e-12,'probe.engaged':self.probe_mode=='contact','probe.moving':self.probe_mode in ['move','approach','release'],'displacement.zeroed':self.zeroed,'furnace.lift_m':self.furnace_lift,'furnace.target_m':self.furnace_target,'probe.position_m':self.probe,'probe.target_m':self.probe_target,'sample.height_m':self._height(),'power.angle_rad':.28 if self.power else 0.,'probe.kind':self.probe_kind,'probe.mode':self.probe_mode,'load.motor_target_n':self.motor_set,'load.motor_program_n':self.motor_program,'load.deadweight_n':self.deadweight,'gas.selected_port':self.gas_selected,'gas.flow_m3_s':self.gas_flow,'thermal.target_k':self.target,'thermal.final_target_k':self.final_target,'force.indicated_n':a['force'],'displacement.indicated_m':a['displacement'],'furnace.indicated_k':a['furnace'],'sample.indicated_k':a['sample'],'truth.force_n':self.force,'truth.elastic_j':self._elastic(),'truth.retarded_m':self.retarded,'truth.natural_length_m':self.natural,'truth.sample_k':self.sample_temperature,'truth.furnace_k':self.furnace,'truth.port1_mol':self.gas_remaining[1],'truth.port2_mol':self.gas_remaining[2],'truth.coolant_kg':self.coolant_remaining,'truth.sample_mass_kg':self.sample['mass_kg'] if self.sample else 0.,'model.fault':self.model_fault,'clock.time_s':self.time_s,'acquisition.phase':self.phase,'acquisition.elapsed_s':self.elapsed,'acquisition.points':len(self.rows),'acquisition.segment_index':self.segment_index,'acquisition.stage':self.stage,'panel.temperature_text':'' if a['furnace'] is None else f"{a['furnace']-273.15:.1f}",'panel.displacement_text':'' if a['displacement'] is None else f"{a['displacement']*1e6:.3f}"};return {k:v for k,v in d.items() if role!='agent' or SLOTS[k]['visibility']=='agent'}
 def _ledger(self):
  mass=self.sample['mass_kg'] if self.sample else 0.;Ef=self.plant['furnace_capacity_j_k']*(self.furnace-self.plant['ambient_k']) if self.plant else 0.;available=self.coolant_used*self.plant['coolant_latent_j_kg']*self.plant['coolant_efficiency'] if self.plant else 0.;U=self._elastic();d=dict(sample_loaded_kg=self.mass_loaded,sample_present_kg=mass,sample_recovered_kg=self.mass_recovered,sample_error_kg=self.mass_loaded-mass-self.mass_recovered,coolant_loaded_kg=self.coolant_loaded,coolant_remaining_kg=self.coolant_remaining,coolant_evaporated_kg=self.coolant_used,coolant_recovered_kg=self.coolant_recovered,coolant_error_kg=self.coolant_loaded-self.coolant_remaining-self.coolant_used-self.coolant_recovered,mechanical_input_j=self.mechanical_input,mechanical_recovered_j=self.mechanical_recovered,elastic_stored_j=U,dissipated_j=self.dissipated,expansion_work_j=self.expansion_work,recovered_elastic_j=self.recovered_elastic,mechanical_error_j=self.mechanical_input-self.mechanical_recovered-U-self.dissipated-self.expansion_work-self.recovered_elastic,heater_input_j=self.heater_input,ambient_loss_j=self.ambient_loss,cooling_removed_j=self.cooling_removed,coolant_cold_used_j=self.coolant_cold_used,coolant_cold_error_j=available-self.coolant_cold_used,furnace_stored_j=Ef,sample_stored_j=self.sample_energy,recovered_sensible_j=self.recovered_sensible,thermal_error_j=self.heater_input+self.dissipated+self.expansion_work-self.ambient_loss-self.cooling_removed-Ef-self.sample_energy-self.recovered_sensible)
  for g in [1,2]:
   vals=dict(loaded=self.gas_loaded[g],remaining=self.gas_remaining[g],exhausted=self.gas_used[g],recovered=self.gas_recovered[g],error=self.gas_loaded[g]-self.gas_remaining[g]-self.gas_used[g]-self.gas_recovered[g]);d.update({'port'+str(g)+'_'+k+'_mol':v for k,v in vals.items()})
  return d
 def _record(self):
  a=self._indicated();r=dict(time_s=self.time_s,elapsed_s=self.elapsed,segment_index=self.segment_index,stage=self.stage,temperature_k=a['sample'],furnace_k=a['furnace'],displacement_m=a['displacement'],force_n=a['force'])
  if self.rows and abs(self.rows[-1]['time_s']-r['time_s'])<1e-8:self.rows[-1]=r
  else:self.rows.append(r)
 def _result(self):
  if self.phase!='running' and self.operation_id in self.records:return copy.deepcopy(self.records[self.operation_id])
  return dict(operation_id=self.operation_id,phase=self.phase,elapsed_s=self.elapsed,rows=copy.deepcopy(self.rows),reference_id=self.operation_reference,template_source=self.operation_source,processing_json=json.dumps(self.operation_processing,sort_keys=True) if self.operation_processing else None,method_json=json.dumps(self.operation_method,sort_keys=True) if self.operation_method else None,calibration_json=json.dumps(self.operation_calibration,sort_keys=True) if self.operation_calibration else None,evidence='Standard TMA temperature/force ramp with source-anchored expansion. Source processed branch retains original acquisition order; EM standard-expansion transfer is explicit, not current450 native calibration. Additional elastic/Kelvin compression and heat/force/sensor dynamics are evaluator engineering inputs, not OEM creep/DTMA functions. External observed sampleT/displacement minus user blank and independent L0 drive CTE/softening fits. Motor<=1N plus manual<=1N deadweight; quantization<.5nm is not accuracy;15nm manufacturer sensitivity/wording discrepancy preserved. Finite gas/LN2/sample and mechanical/thermal ledgers; no mass destruction or free restoration.',error_code=self.error)
 def _finish(self,phase,error=None):
  self.phase=phase;self.error=error;self.thermal_enabled=False;self.motor_set=self.motor_program;self._record();self.records[self.operation_id]=self._result()
 def invoke(self,capability,**kwargs):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability]['input_schema'],kwargs)
  if capability in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  if capability=='sim.step':self.step(kwargs['dt_s']);return {'time_s':self.time_s}
  before=copy.deepcopy(self.__dict__)
  try:
   result=self._invoke(capability,kwargs);validate(CAPS[capability]['output_schema'],result)
   if CAPS[capability]['kind'] not in ['query','analysis']:self.revision+=1
   return result
  except Exception:self.__dict__.clear();self.__dict__.update(before);raise
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _powered(self):
  if not self.power:raise ActionRejected('POWER_OFF')
  if self.emergency:raise ActionRejected('EMERGENCY_LATCHED')
 def _source(self,identifier):
  record=library()['substances'].get(identifier)
  if record is None:raise ActionRejected('INVALID_REFERENCE')
  return record
 def _validate_method(self,a):
  if not a['accept_condition_transfer']:raise ActionRejected('CONDITION_TRANSFER_NOT_ACCEPTED')
  if abs(a['sample_interval_s']/DT-round(a['sample_interval_s']/DT))>1e-8:raise ActionRejected('SAMPLE_CLOCK_ALIGNMENT')
  if a['timeout_s']/a['sample_interval_s']>1999999:raise ActionRejected('TOO_MANY_POINTS')
  self._temperature_domain(a['start_temperature_k'])
  for segment in a['segments']:
   self._temperature_domain(segment['target_temperature_k'])
   if segment['motor_force_n']+self.deadweight>2:raise ActionRejected('LOAD_RANGE')
 def _invoke(self,k,a):
  if k=='reference.query':
   rec=copy.deepcopy(self._source(a['reference_id']));compact={key:val for key,val in rec.items() if key not in ['values','processed_values','raw_values']};compact['values']=rec.get('runtime_values',rec.get('values',{}));compact['reference_scope']='Runtime retained true rows or published table; complete original and processed arrays remain in immutable tma_library.json and cited source files.';return {'reference_json':json.dumps(compact,sort_keys=True,allow_nan=False),'template_source':json.dumps(rec.get('source',{}),sort_keys=True)}
  if k=='analysis.cte':return fit_cte(**a)
  if k=='analysis.softening':return softening_intersection(**a)
  if k=='acquisition.result':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='plant.configure':
   if self.plant is not None:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   if a['safe_access_temperature_k']<a['ambient_k']:raise ActionRejected('SAFE_ACCESS_RANGE')
   self.plant=copy.deepcopy(a);self.furnace=self.sample_temperature=self.target=self.final_target=a['ambient_k'];return {'configured':True}
  if self.plant is None:raise ActionRejected('NOT_CONFIGURED')
  if k=='emergency.stop':
   if self.phase=='running':self._finish('failed','SOFTWARE_STOP')
   self.emergency=True;self.thermal_enabled=False;self.gas_open=False;self.furnace_target=self.furnace_lift;self.probe_target=self.probe;self.probe_mode='contact' if self.probe_mode=='contact' else 'hold';self.motor_set=self.motor_program;return {'latched':True}
  if k=='emergency.reset':
   self._idle()
   if not self._cold():raise ActionRejected('HOT_CELL')
   self.emergency=False;return {'latched':False}
  if k=='power.set':
   if a['enabled']:
    if self.emergency:raise ActionRejected('EMERGENCY_LATCHED')
   else:
    if self.phase=='running':raise ActionRejected('BUSY')
    self.thermal_enabled=False;self.gas_open=False;self.furnace_target=self.furnace_lift;self.probe_target=self.probe;self.probe_mode='contact' if self.probe_mode=='contact' else 'hold'
   self.power=a['enabled'];return {'enabled':self.power}
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self._finish('cancelled');return {'phase':'cancelled'}
  self._idle()
  if k=='sample.load':
   self._mechanical()
   if self.sample is not None:raise ActionRejected('ALREADY_LOADED')
   if self.furnace_lift<.05-1e-12 or self.probe<.028-1e-12 or self.probe_mode!='hold':raise ActionRejected('SAMPLE_ACCESS')
   if not a['accept_engineering_compliance']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   if not a['accept_condition_transfer']:raise ActionRejected('CONDITION_TRANSFER_NOT_ACCEPTED')
   ref=self._source(a['reference_id']);model=prepare_reference(ref,a['accept_reference_processing'])
   if not model['minimum_k']<=self.sample_temperature<=model['maximum_k'] or not model['minimum_k']<=a['reference_temperature_k']<=model['maximum_k']:raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
   self.sample=copy.deepcopy(a);self.reference=copy.deepcopy({key:val for key,val in ref.items() if key not in ['values','processed_values','raw_values','runtime_values']});self.model=model;self.model_fault=None;self.natural=self._length(self.sample_temperature)
   if not 0<self.natural<=.026:raise ActionRejected('SAMPLE_HEIGHT_RANGE')
   self.model['processing']['geometry_mapping']='Known source physical Lref: Linput*(1+epsilon(T))/(1+epsilon(userTref)); PTFE unknown referenceT: Lconfig denominator retained, user baseline subtracts epsilon(userTref) as an explicit displacement-to-geometry assumption.'
   added=a['mass_kg']*a['heat_capacity_j_kg_k']*(self.sample_temperature-self.plant['ambient_k']);self.sample_energy+=added;self.recovered_sensible-=added;self.mass_loaded+=a['mass_kg'];self.retarded=0.;self.force=0.;self.zeroed=False;return {'loaded':True}
  if k=='sample.remove':
   self._mechanical()
   if self.sample is None:raise ActionRejected('NOT_LOADED')
   if self.furnace_lift<.05-1e-12 or self.probe<.028-1e-12 or self.probe_mode!='hold' or self.force>1e-12:raise ActionRejected('SAMPLE_ACCESS')
   removed=self.sample['mass_kg']*self.sample['heat_capacity_j_kg_k']*(self.sample_temperature-self.plant['ambient_k']);self.recovered_sensible+=removed;self.sample_energy-=removed;self.recovered_elastic+=self._elastic();self.mass_recovered+=self.sample['mass_kg'];self.sample=None;self.reference=None;self.model=None;self.retarded=0.;self.force=0.;self.natural=0.;self.model_fault=None;self.zeroed=False;return {'removed':True}
  if k in ['gas.load','gas.unload']:
   if self.gas_open:raise ActionRejected('GAS_VALVE_OPEN')
   port=a['port']
   if k=='gas.load':
    if port in self.gases:raise ActionRejected('ALREADY_LOADED')
    if not a['accept_reference_conditions']:raise ActionRejected('REFERENCE_CONDITIONS_NOT_ACCEPTED')
    self.gases[port]=copy.deepcopy(a);self.gas_remaining[port]=a['amount_mol'];self.gas_loaded[port]+=a['amount_mol'];return {'loaded':True}
   if port not in self.gases:raise ActionRejected('NOT_LOADED')
   self.gas_recovered[port]+=self.gas_remaining[port];self.gas_remaining[port]=0.;del self.gases[port];return {'unloaded':True}
  if k=='gas.select':
   if self.gas_open:raise ActionRejected('GAS_VALVE_OPEN')
   if a['port'] not in self.gases:raise ActionRejected('NO_GAS')
   self.gas_selected=a['port'];return {'selected':self.gas_selected}
  if k=='gas.set_flow':
   if self.thermal_enabled and a['flow_m3_s']<(200e-6/60 if min(self.target,self.sample_temperature)<173.15 else 100e-6/60)-1e-15:raise ActionRejected('PURGE_FLOW_TOO_LOW')
   self.gas_flow=a['flow_m3_s'];return {'flow_m3_s':self.gas_flow}
  if k=='gas.set_open':
   if a['opened']:
    self._powered()
    if self.gas_selected not in self.gases or self.gas_remaining[self.gas_selected]<=0:raise ActionRejected('NO_GAS')
   else:self.thermal_enabled=False
   self.gas_open=a['opened'];return {'opened':self.gas_open}
  if k=='coolant.load':
   if self.thermal_enabled:raise ActionRejected('THERMAL_CONTROL_ACTIVE')
   if self.plant['cooler']!='LN2':raise ActionRejected('COOLER_NOT_CONFIGURED')
   if self.coolant_present:raise ActionRejected('ALREADY_LOADED')
   if not a['accept_engineering_latent']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   self.coolant_present=True;self.coolant_remaining=a['amount_kg'];self.coolant_loaded+=a['amount_kg'];return {'loaded':True}
  if k=='coolant.unload':
   if self.thermal_enabled:raise ActionRejected('THERMAL_CONTROL_ACTIVE')
   if not self.coolant_present:raise ActionRejected('NOT_LOADED')
   self.coolant_recovered+=self.coolant_remaining;self.coolant_remaining=0.;self.coolant_present=False;return {'unloaded':True}
  if k=='furnace.set':
   self._powered();self._mechanical();self.furnace_target=.05 if a['opened'] else 0.;return {'opened':a['opened']}
  if k=='probe.select':
   self._mechanical()
   if self.furnace_lift<.05-1e-12 or self.probe_mode!='hold' or self.probe<.028-1e-12:raise ActionRejected('PROBE_ACCESS')
   self.probe_kind=a['kind'];self.zeroed=False;return {'kind':self.probe_kind}
  if k=='probe.move':
   self._powered();self._mechanical()
   if self.furnace_lift<.05-1e-12 or self.probe_mode=='contact' or self.force>1e-12:raise ActionRejected('PROBE_ACCESS')
   if a['position_m']<(self._height() if self.sample else 0.)+1e-6:raise ActionRejected('PROBE_COLLISION')
   self.probe_target=a['position_m'];self.probe_mode='move';self.zeroed=False;return {'accepted':True}
  if k=='probe.contact':
   self._powered();self._mechanical()
   if self.furnace_lift<.05-1e-12:raise ActionRejected('PROBE_ACCESS')
   if not self.sample:raise ActionRejected('NO_SAMPLE')
   if self.model_fault and a['engaged']:raise ActionRejected('MODEL_FAULT_RELOAD_REQUIRED')
   if a['engaged']:
    if self.probe_mode=='contact':raise ActionRejected('ALREADY_ENGAGED')
    self.probe_mode='approach';self.probe_target=self._height()
   else:
    if self.probe_mode!='contact':raise ActionRejected('NOT_ENGAGED')
    self.probe_mode='release';self.probe_target=.028
   self.zeroed=False;return {'accepted':True}
  if k=='load.set':
   self._powered();self.motor_set=a['motor_force_n'];self.force_rate=a['rate_n_s'];return {'target_force_n':self.motor_set+self.deadweight}
  if k=='load.deadweight':
   self._mechanical()
   if self.furnace_lift<.05-1e-12 or self.probe_mode!='hold' or self.probe<.028-1e-12:raise ActionRejected('DEADWEIGHT_REQUIRES_LIFT')
   self.deadweight=a['force_n'];return {'force_n':self.deadweight}
  if k=='thermal.set_temperature':
   self._powered();self._temperature_domain(a['temperature_k'])
   if self._moving() or self.furnace_lift>1e-12:raise ActionRejected('FURNACE_NOT_CLOSED')
   if not self._flow_ready(min(a['temperature_k'],self.sample_temperature)):raise ActionRejected('PURGE_NOT_READY')
   if a['temperature_k']<self.plant['ambient_k'] and self.coolant_remaining<=0:raise ActionRejected('NO_COOLANT')
   self.final_target=a['temperature_k'];self.rate=a['rate_k_s'];self.thermal_enabled=True;return {'target_k':self.final_target}
  if k=='thermal.disable':self.thermal_enabled=False;return {'enabled':False}
  if k=='thermal.set_cooling':
   self._powered();self.air_cooling=a['enabled'];return {'enabled':self.air_cooling}
  if k=='sensor.calibrate':
   if self._moving():raise ActionRejected('MECHANISM_MOVING')
   self.calibration=copy.deepcopy(a);return copy.deepcopy(a)
  if k=='displacement.zero':
   self._powered()
   if self.probe_mode!='contact' or self.force<.000999 or self._moving():raise ActionRejected('ZERO_REQUIRES_CONTACT')
   self.displacement_zero=self.obs_displacement;self.zeroed=True;return {'zeroed':True}
  if k=='method.configure':self._validate_method(a);self.method=copy.deepcopy(a);return copy.deepcopy(a)
  if k=='acquisition.start':
   self._powered();self._validate_method(self.method)
   if not self.sample:raise ActionRejected('NO_SAMPLE')
   if self.model_fault:raise ActionRejected('MODEL_FAULT_RELOAD_REQUIRED')
   if self.furnace_lift>1e-12 or self._moving() or self.probe_mode!='contact' or not self.zeroed or self.force<.000999:raise ActionRejected('NOT_READY')
   if not self._flow_ready(min([self.sample_temperature]+[s['target_temperature_k'] for s in self.method['segments']])):raise ActionRejected('PURGE_NOT_READY')
   if max(abs(self.furnace-self.method['start_temperature_k']),abs(self.sample_temperature-self.method['start_temperature_k']))>self.method['start_tolerance_k']:raise ActionRejected('INITIAL_TEMPERATURE')
   if min(s['target_temperature_k'] for s in self.method['segments'])<self.plant['ambient_k'] and self.coolant_remaining<=0:raise ActionRejected('NO_COOLANT')
   self._counter+=1;self.operation_id=f'{self.instance_id}:tma:{self._counter}';self.operation_reference=self.sample['reference_id'];self.operation_source=json.dumps(self.reference.get('source',{}),sort_keys=True);self.operation_processing=copy.deepcopy(self.model['processing']);self.operation_method=copy.deepcopy(self.method);self.operation_calibration=copy.deepcopy(self.calibration);self.phase='running';self.elapsed=0.;self.rows=[];self.error=None;self.segment_index=0;self.stage='ramp';self.hold_elapsed=0.;self.target=self.method['start_temperature_k'];self._set_segment();self._record();return {'operation_id':self.operation_id}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _set_segment(self):
  s=self.method['segments'][self.segment_index];self.final_target=s['target_temperature_k'];self.rate=s['temperature_rate_k_s'];self.motor_set=s['motor_force_n'];self.force_rate=s['force_rate_n_s'];self.thermal_enabled=True;self.stage='ramp';self.hold_elapsed=0.
 def _fault(self,reason):
  self.pending_fault=reason;self.thermal_enabled=False
  if reason in ['REFERENCE_TEMPERATURE_RANGE','SAMPLE_COMPRESSION_DOMAIN','THERMAL_MODEL_RANGE','THERMOMECHANICAL_CAPACITY','THERMAL_CONVERGENCE']:self.model_fault=reason
 def _physics(self,h):
  p=self.plant;oldF=self.force;oldMotor=self.motor_program;self.motor_program=self._toward(self.motor_program,self.motor_set,self.force_rate*h)
  contact=self.probe_mode=='contact';target0=oldMotor+self.deadweight if contact else 0.;target1=self.motor_program+self.deadweight if contact else 0.
  newF=first_order_linear(oldF,target0,target1,h,p['force_servo_tau_s']) if self.sample and (not self.model_fault or self.probe_mode=='release') else oldF
  if newF<1e-15:newF=0.
  mechanical=dict(retarded_m=self.retarded,dissipated_j=0.,compression_work_j=0.)
  if self.sample and (not self.model_fault or self.probe_mode=='release'):
   s=self.sample;exponent=s['activation_j_mol']/R_GAS*(1/self.sample_temperature-1/s['reference_temperature_k'])
   if abs(exponent)>80:self._fault('THERMOMECHANICAL_CAPACITY');newF=oldF
   else:mechanical=kelvin_step(oldF,newF,self.retarded,s['elastic_stiffness_n_m'],s['retarded_stiffness_n_m'],s['relaxation_s']*math.exp(exponent),h)
   proposed=self.natural-newF/s['elastic_stiffness_n_m']-mechanical['retarded_m']
   if proposed<=0 or self.natural-proposed>self.natural*s['maximum_compression_fraction']:
    self._fault('SAMPLE_COMPRESSION_DOMAIN');newF=oldF;mechanical=dict(retarded_m=self.retarded,dissipated_j=0.,compression_work_j=0.)
  oldnatural=self.natural
  try:thermal=coupled_thermal_step(p,self.furnace,self.sample_energy,self._cp(),h,self.target,self.thermal_enabled,self.air_cooling,self.coolant_remaining,mechanical['dissipated_j'],(oldF+newF)/2,oldnatural,self._length)
  except ActionRejected as exc:
   self._fault(str(exc));newF=oldF;mechanical=dict(retarded_m=self.retarded,dissipated_j=0.,compression_work_j=0.);thermal=coupled_thermal_step(p,self.furnace,self.sample_energy,self._cp(),h,self.target,False,self.air_cooling,self.coolant_remaining,0.,0.,oldnatural,lambda T:oldnatural)
  if self.sample and not self.model_fault and not self.model['minimum_k']-1e-8<=thermal['sample_k']<=self.model['maximum_k']+1e-8:
   self._fault('REFERENCE_TEMPERATURE_RANGE');newF=oldF;mechanical=dict(retarded_m=self.retarded,dissipated_j=0.,compression_work_j=0.);thermal=coupled_thermal_step(p,self.furnace,self.sample_energy,self._cp(),h,self.target,False,self.air_cooling,self.coolant_remaining,0.,0.,oldnatural,lambda T:oldnatural)
  self.force=float(newF);self.retarded=float(mechanical['retarded_m']);self.furnace=thermal['furnace_k'];self.sample_temperature=thermal['sample_k'];self.sample_energy=thermal['sample_energy_j'];self.natural=self._length(self.sample_temperature)
  work=mechanical['compression_work_j']+thermal['expansion_work_j'];self.mechanical_input+=max(work,0.);self.mechanical_recovered+=max(-work,0.);self.dissipated+=mechanical['dissipated_j'];self.expansion_work+=thermal['expansion_work_j'];self.heater_input+=thermal['heater_j'];self.cooling_removed+=thermal['cooler_j'];self.ambient_loss+=thermal['ambient_j']
  used=thermal['cooler_j']/(p['coolant_latent_j_kg']*p['coolant_efficiency']);used=min(used,self.coolant_remaining);self.coolant_remaining-=used;self.coolant_used+=used;self.coolant_cold_used+=thermal['cooler_j']
  if self.thermal_enabled and self.target<self.furnace and p['cooler']=='LN2' and self.coolant_remaining<=0:self._fault('COOLANT_EXHAUSTED')
  if self.gas_open:
   port=self.gas_selected;g=self.gases[port];need=self.gas_flow*g['reference_pressure_pa']/(R_GAS*g['reference_temperature_k'])*h;used=min(need,self.gas_remaining[port]);self.gas_remaining[port]-=used;self.gas_used[port]+=used
   if self.gas_remaining[port]<=0:self.gas_open=False;self._fault('GAS_EXHAUSTED')
  if self.power and not self.emergency:
   self.furnace_lift=self._toward(self.furnace_lift,self.furnace_target,p['furnace_speed_m_s']*h)
   if self.probe_mode=='move':
    self.probe=self._toward(self.probe,self.probe_target,p['probe_speed_m_s']*h)
    if self.probe==self.probe_target:self.probe_mode='hold'
   elif self.probe_mode=='approach':
    self.probe_target=self._height();self.probe=self._toward(self.probe,self.probe_target,p['probe_speed_m_s']*h)
    if self.probe==self.probe_target:self.probe_mode='contact'
   elif self.probe_mode=='release':
    if self.force<=1e-12:self.probe=self._toward(self.probe,self.probe_target,p['probe_speed_m_s']*h)
    if self.probe==self.probe_target:self.probe_mode='hold'
   elif self.probe_mode=='contact':self.probe=self._height();self.probe_target=self.probe
  forceobs=p['force_gain']*self.force+p['force_offset_n'];xobs=p['displacement_gain']*self.probe+p['displacement_offset_m']+p['frame_drift_m_k']*(self.furnace-p['ambient_k'])
  self.obs_force=first_order_linear(self.obs_force,forceobs,forceobs,h,p['force_sensor_tau_s']);self.obs_displacement=first_order_linear(self.obs_displacement,xobs,xobs,h,p['displacement_sensor_tau_s'])
 @staticmethod
 def _toward(value,target,delta):return float(target if abs(target-value)<=delta else value+math.copysign(delta,target-value))
 def _advance(self,h):
  remaining=h
  while remaining>1e-12:
   z=min(.01,remaining);running=self.phase=='running'
   if running:
    if self.stage=='ramp':
     boundary=max(abs(self.final_target-self.target)/self.rate,abs(self.motor_set-self.motor_program)/self.force_rate)
     if boundary<=1e-10:self.stage='hold';self.hold_elapsed=0.;continue
     z=min(z,boundary)
    else:
     boundary=self.method['segments'][self.segment_index]['hold_s']-self.hold_elapsed
     if boundary<=1e-10:
      if self.segment_index+1==len(self.method['segments']):self._finish('succeeded');continue
      self.segment_index+=1;self._set_segment();continue
     z=min(z,boundary)
    z=min(z,self.method['timeout_s']-self.elapsed)
    nextsample=(math.floor((self.elapsed+1e-8)/self.method['sample_interval_s'])+1)*self.method['sample_interval_s'];z=min(z,nextsample-self.elapsed)
   if self.gas_open and self.gas_flow>0:
    g=self.gases[self.gas_selected];rate=self.gas_flow*g['reference_pressure_pa']/(R_GAS*g['reference_temperature_k']);z=min(z,self.gas_remaining[self.gas_selected]/rate)
   if z<=1e-14:
    if running and self.elapsed>=self.method['timeout_s']-1e-10:self._finish('failed','TIMEOUT');continue
    if self.gas_open:self.gas_used[self.gas_selected]+=self.gas_remaining[self.gas_selected];self.gas_remaining[self.gas_selected]=0.;self.gas_open=False;self._fault('GAS_EXHAUSTED')
    if running:self._finish('failed',self.pending_fault or 'TIMEOUT');self.pending_fault=None
    continue
   if self.thermal_enabled:self.target=self._toward(self.target,self.final_target,self.rate*z)
   self._physics(z);self.time_s+=z;remaining-=z
   if running:
    self.elapsed+=z
    if self.stage=='hold':self.hold_elapsed+=z
    if abs(self.elapsed/self.method['sample_interval_s']-round(self.elapsed/self.method['sample_interval_s']))<1e-7:self._record()
    if self.pending_fault:self._finish('failed',self.pending_fault)
    elif self.elapsed>=self.method['timeout_s']-1e-9:self._finish('failed','TIMEOUT')
   self.pending_fault=None
 def step(self,dt_s):
  validate(number('s',0,3600),dt_s)
  self._remainder+=dt_s;n=int(math.floor((self._remainder+1e-10)/DT));self._remainder-=n*DT
  if self._remainder<0:self._remainder=0.
  for _ in range(n):
   if self.plant is not None:self._advance(DT)
   self._ticks+=1;self.time_s=self._ticks*DT;self.revision+=1

class TaTma450Session:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,instance_id):
  if instance_id!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  meta=export_ta_tma450_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
 def read(self,*,instance_id,state_ids=None):
  self._target(instance_id);h=self._host;values=h.read(self._role);ids=list(values) if state_ids is None else state_ids
  if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
  if any(k not in values for k in ids):raise ActionRejected('ACCESS_DENIED')
  return dict(instance_id=instance_id,status='succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=values[k],unit=SLOTS[k]['unit'],quality='unavailable' if values[k] is None else 'estimated' if SLOTS[k]['origin']=='derived' else 'valid',sample_time_s=h.time_s,sample_window_s=None) for k in ids})
 def invoke(self,*,instance_id,capability_id,arguments,request_id):
  h=self._host
  try:
   self._target(instance_id)
   if not isinstance(capability_id,str) or capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
   if self._role=='agent' and CAPS[capability_id]['visibility']!='agent':raise ActionRejected('ACCESS_DENIED')
   if not isinstance(request_id,str) or not request_id:raise ActionRejected('INVALID_ARGUMENT')
   validate(CAPS[capability_id]['input_schema'],arguments);key=(self._principal,request_id);digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
   if key in h._requests:
    old,result=h._requests[key]
    if old!=digest:raise ActionRejected('REQUEST_CONFLICT')
    return copy.deepcopy(result)
   if capability_id in ('acquisition.stop','acquisition.result') and self._role=='agent' and h.operation_id is not None and h._owners.get(h.operation_id)!=self._principal:raise ActionRejected('ACCESS_DENIED')
   result=h.invoke(capability_id,**arguments);operation=result.get('operation_id') if capability_id=='acquisition.start' else None
   if operation:h._owners[operation]=self._principal
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

OBSERVATIONS=['force.indicated_n','displacement.indicated_m','furnace.indicated_k','sample.indicated_k','panel.temperature_text','panel.displacement_text']
EFFECTS={'plant.configure':['plant.configured','truth.furnace_k','truth.sample_k','thermal.target_k','thermal.final_target_k']+OBSERVATIONS,
'sample.load':['sample.present','truth.sample_mass_kg','truth.natural_length_m','truth.retarded_m','truth.force_n','truth.elastic_j','sample.height_m','displacement.zeroed','model.fault']+OBSERVATIONS,
'sample.remove':['sample.present','truth.sample_mass_kg','truth.natural_length_m','truth.retarded_m','truth.force_n','truth.elastic_j','sample.height_m','displacement.zeroed','model.fault']+OBSERVATIONS,
'gas.load':['truth.port1_mol','truth.port2_mol'],'gas.unload':['truth.port1_mol','truth.port2_mol'],'gas.select':['gas.selected_port'],'gas.set_flow':['gas.flow_m3_s'],'gas.set_open':['gas.opened','thermal.enabled'],
'coolant.load':['truth.coolant_kg'],'coolant.unload':['truth.coolant_kg'],
'power.set':['power.enabled','power.angle_rad','thermal.enabled','gas.opened','furnace.target_m','furnace.moving','probe.target_m','probe.mode','probe.moving','probe.engaged']+OBSERVATIONS,
'furnace.set':['furnace.target_m','furnace.moving'],'probe.select':['probe.kind','displacement.zeroed']+OBSERVATIONS,
'probe.move':['probe.target_m','probe.mode','probe.moving','displacement.zeroed']+OBSERVATIONS,'probe.contact':['probe.target_m','probe.mode','probe.moving','probe.engaged','displacement.zeroed']+OBSERVATIONS,
'load.set':['load.motor_target_n'],'load.deadweight':['load.deadweight_n'],
 'thermal.set_temperature':['thermal.final_target_k','thermal.enabled'],'thermal.disable':['thermal.enabled'],'thermal.set_cooling':['cooling.enabled'],'sensor.calibrate':OBSERVATIONS,'displacement.zero':['displacement.zeroed']+OBSERVATIONS,
'acquisition.start':['acquisition.phase','acquisition.elapsed_s','acquisition.points','acquisition.segment_index','acquisition.stage','thermal.target_k','thermal.final_target_k','thermal.enabled','load.motor_target_n'],
'acquisition.stop':['acquisition.phase','acquisition.points','thermal.enabled','load.motor_target_n'],
'emergency.stop':['emergency.latched','acquisition.phase','acquisition.points','thermal.enabled','gas.opened','furnace.target_m','furnace.moving','probe.target_m','probe.mode','probe.moving','probe.engaged','load.motor_target_n'],
'emergency.reset':['emergency.latched'],'sim.step':list(SLOTS)}
for k,v in EFFECTS.items():CAPS[k]['effects']=v
ERRORS=['UNKNOWN_CAPABILITY','UNKNOWN_INSTANCE','UNKNOWN_OPERATION','INVALID_ARGUMENT','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','BUSY','NOT_RUNNING','INITIALIZATION_ONLY','MODEL_NOT_ACCEPTED','NOT_CONFIGURED','HOT_CELL','THERMAL_CONTROL_ACTIVE','MECHANISM_MOVING','ALREADY_LOADED','NOT_LOADED','INVALID_REFERENCE','CONDITION_TRANSFER_NOT_ACCEPTED','REFERENCE_CONDITIONS_NOT_ACCEPTED','REFERENCE_PROCESSING_NOT_ACCEPTED','REFERENCE_NOT_MONOTONE','REFERENCE_TEMPERATURE_RANGE','UNKNOWN_CTE_BASIS','NO_GAS','NO_SAMPLE','NOT_READY','COOLER_TEMPERATURE_RANGE','NO_COOLANT','COOLER_NOT_CONFIGURED','COOLANT_EXHAUSTED','GAS_EXHAUSTED','TIMEOUT','SOFTWARE_STOP','ANALYSIS_DATA','ANALYSIS_TEMPERATURE_WINDOW','ANALYSIS_WINDOWS','COMPLIANCE_DOMAIN','SAFE_ACCESS_RANGE','SAMPLE_ACCESS','SAMPLE_HEIGHT_RANGE','GAS_VALVE_OPEN','PURGE_FLOW_TOO_LOW','PURGE_NOT_READY','PROBE_ACCESS','PROBE_COLLISION','MODEL_FAULT_RELOAD_REQUIRED','ALREADY_ENGAGED','NOT_ENGAGED','DEADWEIGHT_REQUIRES_LIFT','FURNACE_NOT_CLOSED','ZERO_REQUIRES_CONTACT','INITIAL_TEMPERATURE','SAMPLE_CLOCK_ALIGNMENT','TOO_MANY_POINTS','LOAD_RANGE','THERMAL_MODEL_RANGE','THERMOMECHANICAL_CAPACITY','THERMAL_CONVERGENCE','SAMPLE_COMPRESSION_DOMAIN','EMERGENCY_LATCHED','POWER_OFF']
def export_ta_tma450_contract_metadata():
 slots={};caps={}
 for k,v in SLOTS.items():slots[k]=dict(meaning_zh=v['meaning'],component_ids=[v['component']],value_schema=copy.deepcopy(v['schema']),unit=v['unit'],reference_conditions='SI. Gas volume reference T/P explicitly supplied; inlet pressure70..140kPa gauge. TMA span+-2.5mm relative observed zero; engineering quantum<.5nm is not accuracy. Coarse probe0..30mm from platform is not measurement span.',origin=v['origin'],visibility=v['visibility'],access='read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'TaTma450Session.read(state_ids=[{k!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description='Host0.1s partitions; physical steps<=.01s. Reads never advance time.'),source_ids=['simulation_assumption'])
 for k,c in CAPS.items():
  un=k in UNSUPPORTED;async_=k=='acquisition.start';caps[k]={key:copy.deepcopy(c[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']};caps[k].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ['scene','robot','hardware'] else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.ta_tma450.TaTma450Session.invoke',invocation=f'session.invoke(instance_id="tma450_01",capability_id={k!r},arguments={c["example_arguments"]!r},request_id="unique")'),preconditions=[] if c['kind'] in ['query','analysis'] or un else [dict(description_zh='冷态开炉离样装卸，数字电机≤1N+人工砝码≤1N，有限purge/LN2与实测参考温区，原子拒绝',state_ids=['power.enabled','furnace.lift_m','sample.present','gas.opened','probe.mode','acquisition.phase'],enforced_by='runtime',check='atomic invocation with source, access, force, thermal and finite-resource guards')],state_effects=[dict(state_id=x,timing='immediate',description_zh='实际命令即时变化') for x in c['effects']],observes=[],model_effects=dict(mode='none',description_zh='三关节和两数值屏由场景独立验证；Matterix参考圆片网格未实时形变',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='温度与载荷目标有限ramp后完成每段驻留；不冒称真实样品已平衡' if async_ else '命令原子接受；运动/热/载荷响应等待宿主step',completion_state_ids=['acquisition.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','furnace','probe','specimen','lvdt','purge','LN2'],partial_failure_zh='取消保留实际载荷、可恢复压缩和余热。模型出域停止本构更新并保留最后有效力/长度，继续被动热交换；重载样品后才可重新测量。'),errors=[dict(code=e,meaning_zh=e,side_effects='partial' if async_ and e in ['SAMPLE_COMPRESSION_DOMAIN','REFERENCE_TEMPERATURE_RANGE','THERMAL_MODEL_RANGE','COOLANT_EXHAUSTED','GAS_EXHAUSTED','TIMEOUT','SOFTWARE_STOP'] else 'none') for e in ERRORS],verification=[],limitations=['仅标准450温度/force ramp的固体膨胀；EM creep/relaxation/dynamic/modulated以及isostrain当前未实现','内部Kelvin材料压缩记忆是显式工程假设，不是宣称标准450拥有EM功能','PTFE原机为450EM标准膨胀模式，保留跨型号/载荷/速率工程迁移；铜是NIST干涉仪参考表','CTE仅外部观察减blank回归，不从隐藏alpha返回结果；软化仅所选窗口交点','原厂heater功率未知，1200W为供电而非热端实测；LN2有效潜热与换热系数显式输入','双气源库存守恒；未求解腔气氛置换、purge显热与真实温度梯度'],source_ids=['manufacturer_specs','simulation_assumption'])
  if k in ['acquisition.start','acquisition.stop','emergency.stop','power.set','thermal.set_temperature','thermal.disable','thermal.set_cooling','furnace.set','probe.move','probe.contact','load.set','gas.set_open']:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='宿主推进有限运动/压缩/热/观测/气耗') for x in SLOTS if x not in ['plant.configured','power.enabled','power.angle_rad','sample.present','emergency.latched']]
 mechanism=dict(component_ids=['controller','furnace','probe','specimen','lvdt','force_coil'],state_ids=list(slots),meaning_zh='真实源长度响应与有限载荷/热/观测耦合',implementation='lumped',equations=[
 'Known physical reference epsilon=(L-Lref)/Lref: Lfree=Linput*(1+epsilon(T))/(1+epsilon(Tuserref)). PTFE configured referenceT unknown: retain Lconfig denominator and map measured deltaL increment as Linput*[1+epsilon(T)-epsilon(Tuserref)]. This separate geometry assumption does not identify unloaded intrinsic CTE.',
 'Runtime source retains original acquisition-order increasing branch with retained source-row indices. No runtime sorting/smoothing/clipping or temperature extrapolation. Copper certificate epsilon is fixedL293, not integral(dlogL); any alpha-only record requires explicit definition.',
 'Actual motor load has finite ramp and first-order servo. Digital motor .001..1N, manual deadweight0..1N, sum<=2N. F command does not teleport probe. Cold probe approaches contact at finite speed; unloading retains compression and exports recovered work.',
 'Series spring and Kelvin: h=Lfree-F/ke-xv, dxv/dt=(F/kv-xv)/tau(T), tau=tau_ref exp[Ea/R(1/T-1/Tref)]. ke,kv,tau,Ea are explicit engineering inputs. U=F^2/(2ke)+kv*xv^2/2; D=integral kv*tau*(dxv/dt)^2 dt. PWL actual force update and dashpot integral are analytical (positive Gaussian limit for very small h/tau).',
 'Mechanical work Wext=deltaU+D-Fmean*deltaLfree. Signed work separates supplied/recovered; thermal expansion against compressive load returns work. No wear/debris or destroyed sample mass is invented. Removal exports remaining mechanical and sensible energy.',
 'Thermal implicit two-node balance: Cf*dTf=Pheater-Pcool-Gambient*(Tf-Tamb)-Gs*(Tf-Ts); Cs*dTs=Gs*(Tf-Ts)+Ddot-F*dLfree/dt. Nonlinear source length is solved with heat balance; power saturates. None cooler can only lose heat to ambient, LN2 cooling consumes finite mass*latent*efficiency.',
 'Finite purge from selected port: dn=Qref*Pref/(R*Tref)*dt. Exhaust and recovered gas are explicit; gas enthalpy/chamber replacement unmodeled.100mL/min at>=173.15K;200mL/min below; max200mL/min.',
 'Independent force/LVDT first-order observation gain/zero/drift and explicit quantization; calibrated force0..2N and observed-zero displacement+-2.5mm else unavailable. CTE regression uses only external observed sampleT, displacement minus supplied blank, and supplied L0. Softening intersection is load/window-dependent and must be bracketed.',
 'Host0.1s fixed partition is unique clock; before explicit plant configuration only clock/revision advance, with no automatic plant or resources. Internal h<=.01s with method/sample/resource endpoint subdivision. Cancel freezes observations but leaves physical relaxation/thermal state. Constitutive-domain faults retain last valid configuration and allow passive thermal exchange, without hidden extension of measured curve.'
 ],parameters={},source_ids=['manufacturer_specs','simulation_assumption','ta_tma450em_teflon_example','nist_srm736'],limitations=['No EM features, tension/flexure/dilatometer fixtures, MCA70, empirical accuracy/noise certification or robotic manipulation.','Numerical penetration uses explicitly supplied compression parameters; displayed geometry remains expansion probe.','Standard specimen geometry is source PTFE disk; Matterix static specimen does not deform, while physical probe displacement is unamplified.'])
 spec=importlib.util.spec_from_file_location('_tma_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()});ann.annotate_export(SLUG,caps,slots,{'tma_conservation':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'gas.load':(GAS,GAS_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sensor.calibrate':(CALIBRATION,CALIBRATION_EXAMPLE)})
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'tma_conservation':mechanism})

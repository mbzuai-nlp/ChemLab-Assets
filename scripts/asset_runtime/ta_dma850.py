"""CHEM133 linear-tension DMA: measured anchors and explicit generalized Maxwell response."""
from pathlib import Path
import copy,json,math,importlib.util,bisect
import numpy as np
PROTOCOL_VERSION='1.0.0';SLUG='ta_dma850';CATALOG_ID='CHEM-133';ASSET_TYPE_ID='instrument.'+SLUG
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
 p=ASSET/'dma_library.json';return json.loads(p.read_text()) if p.exists() else {'substances':{}}

PLANT_EXAMPLE=dict(accept_engineering=True,ambient_k=298.15,cooler='none',furnace_capacity_j_k=10.,fixture_capacity_j_k=1.,sample_conductance_w_k=.5,ambient_conductance_w_k=.05,controller_gain_w_k=20.,heater_max_w=100.,cooler_max_w=40.,safe_access_temperature_k=313.15,force_sensor_tau_s=0.00001,displacement_sensor_tau_s=0.00001,force_gain=1.,force_offset_n=0.,displacement_gain=1.,displacement_offset_m=0.,temperature_gain=1.,temperature_offset_k=0.,furnace_speed_m_s=.05,drive_speed_m_s=.002,clamp_speed_m_s=.001,air_flow_m3_s=2e-3/60,purge_flow_m3_s=50e-6/60,npc_n2_flow_m3_s=30e-3/60,air_pressure_gauge_pa=430000.,coolant_latent_j_kg=199000.,coolant_efficiency=.7)
PLANT=obj({'accept_engineering':BOOL,'ambient_k':number('K',283.15,313.15),'cooler':enum(['none','NPC']),'furnace_capacity_j_k':number('J/K',1e-5,1e4),'fixture_capacity_j_k':number('J/K',1e-7,1000),'sample_conductance_w_k':number('W/K',1e-7,1000),'ambient_conductance_w_k':number('W/K',0,1000),'controller_gain_w_k':number('W/K',1e-5,1e5),'heater_max_w':number('W',1e-5,1e5),'cooler_max_w':number('W',0,1e5),'safe_access_temperature_k':number('K',283.15,373.15),'force_sensor_tau_s':number('s',1e-8,100),'displacement_sensor_tau_s':number('s',1e-8,100),'force_gain':number('1',.01,100),'force_offset_n':number('N',-18,18),'displacement_gain':number('1',.01,100),'displacement_offset_m':number('m',-.01,.01),'temperature_gain':number('1',.5,1.5),'temperature_offset_k':number('K',-20,20),'furnace_speed_m_s':number('m/s',1e-4,.1),'drive_speed_m_s':number('m/s',1e-6,.1),'clamp_speed_m_s':number('m/s',1e-5,.01),'air_flow_m3_s':number('m^3/s',2e-3/60,2e-3/60),'purge_flow_m3_s':number('m^3/s',0,.001),'npc_n2_flow_m3_s':number('m^3/s',30e-3/60,30e-3/60),'air_pressure_gauge_pa':number('Pa',410000,450000),'coolant_latent_j_kg':number('J/kg',1,1e7),'coolant_efficiency':number('1',.01,1)})
BRANCH=obj({'modulus_pa':number('Pa',1,3e12),'relaxation_s':number('s',1e-8,1e8)})
SOURCE_RESPONSE=obj({'mode':enum(['source_anchor']),'reference_id':TEXT,'temperature_k':number('K',113.15,873.15),'frequency_hz':number('Hz',.001,200),'anchor_relaxation_s':number('s',1e-8,1e8),'temperature_tolerance_k':number('K',.001,2),'accept_equivalent_model':BOOL,'accept_condition_transfer':BOOL})
MAXWELL_RESPONSE=obj({'mode':enum(['engineering_maxwell']),'equilibrium_modulus_pa':number('Pa',1e3,3e12),'branches':array(BRANCH,0,20),'reference_temperature_k':number('K',113.15,873.15),'activation_j_mol':number('J/mol',0,3e5),'accept_engineering_spectrum':BOOL})
SAMPLE_EXAMPLE=dict(form='film',length_m=.01,width_m=.004,thickness_m=.0002,diameter_m=.0002,mass_kg=1e-5,heat_capacity_j_kg_k=1500.,maximum_linear_strain=.02,accept_engineering_geometry=True,response=dict(mode='source_anchor',reference_id='phd_hub_network_dma850_1hz',temperature_k=298.15,frequency_hz=1.,anchor_relaxation_s=.01,temperature_tolerance_k=.5,accept_equivalent_model=True,accept_condition_transfer=True))
ENGINEERING_SAMPLE_EXAMPLE=dict(SAMPLE_EXAMPLE,response=dict(mode='engineering_maxwell',equilibrium_modulus_pa=1e8,branches=[dict(modulus_pa=2e8,relaxation_s=.2),dict(modulus_pa=5e7,relaxation_s=.02)],reference_temperature_k=298.15,activation_j_mol=0.,accept_engineering_spectrum=True))
SAMPLE=obj({'form':enum(['film','fiber']),'length_m':number('m',.005,.03),'width_m':number('m',1e-6,.008),'thickness_m':number('m',1e-7,.002),'diameter_m':number('m',1e-7,.0008),'mass_kg':number('kg',1e-12,.1),'heat_capacity_j_kg_k':number('J/(kg*K)',1,1e5),'maximum_linear_strain':number('1',1e-7,.1),'accept_engineering_geometry':BOOL,'response':{'anyOf':[SOURCE_RESPONSE,MAXWELL_RESPONSE]}})
GAS_EXAMPLE=dict(gas='air',amount_mol=1.,reference_temperature_k=298.15,reference_pressure_pa=101325.,accept_reference_conditions=True)
GAS=obj({'gas':enum(['air','N2']),'amount_mol':number('mol',1e-18,10000),'reference_temperature_k':number('K',200,500),'reference_pressure_pa':number('Pa',10000,1e7),'accept_reference_conditions':BOOL})
COOLANT_EXAMPLE=dict(amount_kg=.1,accept_engineering_latent=True)
COOLANT=obj({'amount_kg':number('kg',1e-12,100),'accept_engineering_latent':BOOL})
CALIBRATION_EXAMPLE=dict(force_gain_correction=1.,force_zero_n=0.,displacement_gain_correction=1.,displacement_zero_m=0.,temperature_gain_correction=1.,temperature_zero_k=0.)
CALIBRATION=obj({'force_gain_correction':number('1',.01,100),'force_zero_n':number('N',-18,18),'displacement_gain_correction':number('1',.01,100),'displacement_zero_m':number('m',-.01,.01),'temperature_gain_correction':number('1',.1,10),'temperature_zero_k':number('K',-100,100)})
BLOCK=obj({'temperature_k':number('K',113.15,873.15),'frequency_hz':number('Hz',.001,200),'control':enum(['strain','force']),'strain_amplitude':number('1',1e-8,.1),'force_amplitude_n':number('N',1e-4,18),'settle_cycles':integer(1,100),'measure_cycles':integer(2,1000)})
METHOD_EXAMPLE=dict(blocks=[dict(temperature_k=298.15,frequency_hz=1.,control='strain',strain_amplitude=1e-4,force_amplitude_n=.01,settle_cycles=5,measure_cycles=3)],heating_rate_k_s=10/60,cooling_rate_k_s=5/60,temperature_tolerance_k=.3,timeout_s=100000.,samples_per_cycle=128)
METHOD=obj({'blocks':array(BLOCK,1,100),'heating_rate_k_s':number('K/s',.1/60,20/60),'cooling_rate_k_s':number('K/s',.1/60,10/60),'temperature_tolerance_k':number('K',.001,2),'timeout_s':number('s',.1,2e6),'samples_per_cycle':integer(64,512)})
ROW=obj({'time_s':number('s',0),'elapsed_s':number('s',0),'block_index':integer(0,99),'stage':enum(['settle','measure']),'frequency_hz':number('Hz',.001,200),'force_n':nullable(number('N')),'displacement_m':nullable(number('m')),'sample_temperature_k':nullable(number('K'))})
MODULUS=obj({'storage_modulus_pa':number('Pa'),'loss_modulus_pa':number('Pa'),'tan_delta':number('1'),'force_amplitude_n':number('N',0),'displacement_amplitude_m':number('m',0),'mean_force_n':number('N'),'phase_rad':number('rad'),'cycles':number('1',0),'force_fit_rms_n':number('N',0),'displacement_fit_rms_m':number('m',0),'passive_compatible':BOOL,'evidence':TEXT})
BLOCK_RESULT=obj({'block_index':integer(0,99),'frequency_hz':number('Hz',.001,200),'mean_temperature_k':number('K'),'analysis':nullable(MODULUS),'analysis_error':nullable(TEXT)})
RESULT=obj({'operation_id':nullable(TEXT),'phase':PHASE,'elapsed_s':number('s',0),'rows':array(ROW,0,2000000),'blocks':array(BLOCK_RESULT,0,100),'reference_id':nullable(TEXT),'template_source':nullable(TEXT),'method_json':nullable(TEXT),'calibration_json':nullable(TEXT),'evidence':TEXT,'error_code':nullable(TEXT)})

def maxwell_modulus(frequency_hz,equilibrium_modulus_pa,branches):
 w=2*math.pi*frequency_hz;z=complex(equilibrium_modulus_pa)
 for b in branches:
  v=w*b['relaxation_s'];z+=b['modulus_pa']*complex(v*v,v)/(1+v*v)
 return z

def maxwell_step(strain0,strain1,stress,equilibrium_modulus_pa,branches,dt_s,volume_m3):
 """Exact constant strain-rate Maxwell branches; nonnegative analytical dashpot loss."""
 if dt_s<=0 or len(stress)!=len(branches):raise ActionRejected('MECHANICAL_DOMAIN')
 de=strain1-strain0;new=[];diss=0.
 for q,b in zip(stress,branches):
  E=b['modulus_pa'];tau=b['relaxation_s'];r=dt_s/tau;one=-math.expm1(-r);q1=q*(1-one)+E*de*(one/r);new.append(q1)
  if r<1e-3:
   c0=1-r+2*r*r/3-r**3/3+2*r**4/15;c1=.5-r/2+7*r*r/24-r**3/8+31*r**4/720;c2=1/3-r/4+7*r*r/60-r**3/24+31*r**4/2520
  else:
   second=-math.expm1(-2*r);c0=second/(2*r);c1=(one-second/2)/(r*r);c2=(r-2*one+second/2)/(r**3)
  integral=dt_s*(q*q*c0+2*q*E*de*c1+(E*de)**2*c2);diss+=volume_m3*max(0.,integral)/(E*tau)
 old=volume_m3*(equilibrium_modulus_pa*strain0*strain0/2+sum(q*q/(2*b['modulus_pa']) for q,b in zip(stress,branches)));stored=volume_m3*(equilibrium_modulus_pa*strain1*strain1/2+sum(q*q/(2*b['modulus_pa']) for q,b in zip(new,branches)))
 return dict(stress_pa=new,stored_j=stored,previous_stored_j=old,dissipated_j=diss,work_j=stored-old+diss)

def fit_dynamic_modulus(time_s,force_n,displacement_m,frequency_hz,length_m,area_m2):
 t,F,x=map(lambda v:np.asarray(v,float),(time_s,force_n,displacement_m));f=frequency_hz
 if t.ndim!=1 or len(t)<17 or F.shape!=t.shape or x.shape!=t.shape or not all(np.all(np.isfinite(v)) for v in [t,F,x]) or np.any(np.diff(t)<=0) or min(f,length_m,area_m2)<=0:raise ActionRejected('ANALYSIS_DATA')
 periods=(t[-1]-t[0])*f
 if periods<2-1e-8 or abs(periods-round(periods))>1e-6 or max(np.diff(t))*f>1/16+1e-8 or not np.allclose(np.diff(t),np.diff(t)[0],rtol=1e-6,atol=1e-9):raise ActionRejected('HARMONIC_WINDOW')
 u=t-t[0];w=2*math.pi*f;M=np.column_stack([np.ones(len(t)),u,np.cos(w*u),np.sin(w*u)]);a=np.linalg.lstsq(M,F,rcond=None)[0];b=np.linalg.lstsq(M,x,rcond=None)[0];Fp=complex(a[2],-a[3]);xp=complex(b[2],-b[3])
 if abs(xp)<1e-12:raise ActionRejected('DISPLACEMENT_UNRESOLVED')
 z=Fp/xp*length_m/area_m2
 if abs(z.real)<1e-12:raise ActionRejected('STORAGE_UNRESOLVED')
 return dict(storage_modulus_pa=float(z.real),loss_modulus_pa=float(z.imag),tan_delta=float(z.imag/z.real),force_amplitude_n=float(abs(Fp)),displacement_amplitude_m=float(abs(xp)),mean_force_n=float(np.mean(F)),phase_rad=float(np.angle(z)),cycles=float(periods),force_fit_rms_n=float(np.sqrt(np.mean((F-M@a)**2))),displacement_fit_rms_m=float(np.sqrt(np.mean((x-M@b)**2))),passive_compatible=bool(z.real>0 and z.imag>=0),evidence='Independent observed force/displacement OLS harmonics with DC+linear trend. E*=Fphasor/Xphasor*supplied L/A; Re(Z exp(iwt)) convention. Gain, phase, fixture and thermal uncertainty remain; no material truth or preset modulus is returned.')

def transition_peak(temperature_k,tan_delta,frequency_hz):
 T=np.asarray(temperature_k,float);d=np.asarray(tan_delta,float);f=np.asarray(frequency_hz,float)
 if len(T)<3 or T.shape!=d.shape or T.shape!=f.shape or not all(np.all(np.isfinite(v)) for v in [T,d,f]) or np.any(np.diff(T)<=0) or np.min(f)<=0 or np.max(abs(f-f[0]))>1e-8:raise ActionRejected('TRANSITION_DATA')
 i=int(np.argmax(d));bracketed=0<i<len(T)-1
 return dict(transition_temperature_k=float(T[i]) if bracketed else None,peak_tan_delta=float(d[i]),bracketed=bool(bracketed),evidence='Discrete observed tan(delta) peak at one fixed frequency and increasing temperature; boundary maxima are unbracketed/null. Not certified Tg, onset, or an intrinsic material constant.')
CAPS={};SLOTS={}
def cap(k,i,o,c,z,kind='device_command',visibility='agent',example=None):CAPS[k]=dict(input_schema=i,output_schema=o,component_ids=c,function_zh=z,kind=kind,visibility=visibility,example_arguments=copy.deepcopy(example or {}),effects=[])
def slot(k,s,u,z,origin='derived',visibility='agent',component='controller'):SLOTS[k]=dict(schema=s,unit=u,meaning=z,origin=origin,visibility=visibility,component=component,write=None)
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller','furnace','air_bearing'],'显式热/气/传感器工程模型','configuration','evaluator',PLANT_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['specimen','fixed_clamp','moving_clamp'],'有限拉伸试样及严格实测锚/工程谱选择','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.remove',obj({}),obj({'removed':BOOL}),['specimen'],'冷态卸载回收样品与剩余显热/弹性能','configuration','evaluator')
cap('gas.load',GAS,obj({'loaded':BOOL}),['air_bearing','furnace'],'有限air/N2及显式参考流量温压','configuration','evaluator',GAS_EXAMPLE)
cap('gas.unload',obj({'gas':enum(['air','N2'])}),obj({'unloaded':BOOL}),['air_bearing','furnace'],'关阀回收有限气源','configuration','evaluator',{'gas':'air'})
cap('gas.set_open',obj({'opened':BOOL}),obj({'opened':BOOL}),['air_bearing','furnace'],'开启airbearing与N2吹扫支路',example={'opened':True})
cap('coolant.load',COOLANT,obj({'loaded':BOOL}),['furnace'],'NPC有限液氮及显式有效潜热','configuration','evaluator',COOLANT_EXAMPLE)
cap('coolant.unload',obj({}),obj({'unloaded':BOOL}),['furnace'],'冷态停机回收剩余液氮','configuration','evaluator')
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_switch','power_controller'],'供电',example={'enabled':True})
cap('furnace.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['furnace'],'炉体150mm有限速开合',example={'opened':False})
cap('clamp.set',obj({'locked':BOOL}),obj({'locked':BOOL}),['clamp_jaw','lower_clamp_jaw','fixed_clamp','moving_clamp'],'冷态无载夹具锁紧',example={'locked':True})
cap('drive.move',obj({'position_m':number('m',-.005,.020)}),obj({'accepted':BOOL}),['drive_shaft','moving_clamp'],'仅空夹具有限速定位，正向下移增大夹距',example={'position_m':0.})
cap('drive.preload',obj({'force_n':number('N',.0001,18),'ramp_s':number('s',.01,1e5)}),obj({'accepted':BOOL}),['drive_shaft','moving_clamp','load_cell'],'按平衡模量计算静态预伸长，有限ramp并保留支路瞬态',example={'force_n':.05,'ramp_s':2.})
cap('drive.unload',obj({'ramp_s':number('s',.01,1e5)}),obj({'accepted':BOOL}),['drive_shaft','moving_clamp','load_cell'],'有限速度卸载并用零牵引边界避免压屈；不清空应力',example={'ramp_s':2.})
cap('thermal.set_temperature',obj({'temperature_k':number('K',113.15,873.15),'rate_k_s':number('K/s',.1/60,20/60)}),obj({'target_k':number('K',113.15,873.15)}),['furnace'],'真实有限热容量温程；降温速率另限10K/min',example={'temperature_k':303.15,'rate_k_s':10/60})
cap('thermal.disable',obj({}),obj({'enabled':BOOL}),['furnace'],'停止主动热控制，保留余热和冷态恢复')
cap('sensor.calibrate',CALIBRATION,CALIBRATION,['load_cell','controller'],'外部力/位移/温度增益零点','configuration',example=CALIBRATION_EXAMPLE)
cap('sensor.zero',obj({}),obj({'zeroed':BOOL}),['load_cell','controller'],'空夹具静止时观测归零')
cap('method.configure',METHOD,METHOD,['controller'],'温度/frequency区块、实际settle与测量整周期','configuration',example=METHOD_EXAMPLE)
cap('acquisition.start',obj({}),obj({'operation_id':TEXT}),['controller','drive_shaft','load_cell','furnace'],'异步DMA，内部高速采样与物理Maxwell记忆')
cap('acquisition.stop',obj({}),obj({'phase':enum(['cancelled'])}),['controller','drive_shaft'],'取消保持实际应变与支路记忆，停止主动加热')
cap('acquisition.result',obj({}),RESULT,['controller'],'冻结观测F/x与外部拟合；owner限定','query')
cap('reference.query',obj({'reference_id':TEXT}),obj({'reference_json':TEXT,'template_source':TEXT}),['controller'],'真实原T/f/E′/E″数据及适用条件','query',example={'reference_id':SAMPLE_EXAMPLE['response']['reference_id']})
cap('analysis.modulus',obj({'time_s':array(number('s',0),17,2000000),'force_n':array(number('N'),17,2000000),'displacement_m':array(number('m'),17,2000000),'frequency_hz':number('Hz',.001,200),'length_m':number('m',1e-6,1),'area_m2':number('m^2',1e-15,1)}),MODULUS,['controller'],'外部观测谐波幅相拟合拉伸模量','analysis')
cap('analysis.transition',obj({'temperature_k':array(number('K'),3,10000),'tan_delta':array(number('1'),3,10000),'frequency_hz':array(number('Hz',.001,200),3,10000)}),obj({'transition_temperature_k':nullable(number('K')),'peak_tan_delta':number('1'),'bracketed':BOOL,'evidence':TEXT}),['controller'],'固定频率观测tanδ内部峰，不外推Tg','analysis',example={'temperature_k':[290.,300.,310.],'tan_delta':[.1,.3,.2],'frequency_hz':[1.,1.,1.]})
LEDGER_UNITS={k:u for k,u in [('sample_loaded_kg','kg'),('sample_present_kg','kg'),('sample_recovered_kg','kg'),('sample_error_kg','kg'),('coolant_loaded_kg','kg'),('coolant_remaining_kg','kg'),('coolant_evaporated_kg','kg'),('coolant_recovered_kg','kg'),('coolant_error_kg','kg'),('mechanical_input_j','J'),('mechanical_recovered_j','J'),('elastic_stored_j','J'),('dissipated_j','J'),('recovered_elastic_j','J'),('mechanical_error_j','J'),('heater_input_j','J'),('ambient_loss_j','J'),('cooling_removed_j','J'),('coolant_cold_available_j','J'),('coolant_cold_used_j','J'),('coolant_cold_error_j','J'),('furnace_stored_j','J'),('sample_stored_j','J'),('recovered_sensible_j','J'),('thermal_error_j','J')]}
for g in ['air','n2']:
 for k in ['loaded','remaining','exhausted','recovered','error']:LEDGER_UNITS[g+'_'+k+'_mol']='mol'
LEDGER=obj({k:number(u) for k,u in LEDGER_UNITS.items()})
cap('ledger.read',obj({}),LEDGER,['controller'],'有限样片/空气/N2/LN2与力功/热量账本','query','evaluator')
cap('sim.step',obj({'dt_s':number('s',0,3600)}),obj({'time_s':number('s',0)}),['controller'],'唯一宿主时钟','simulation','evaluator',{'dt_s':.1})
cap('emergency.stop',obj({}),obj({'latched':BOOL}),['controller'],'软件停机保持应变/残留弹性能')
cap('emergency.reset',obj({}),obj({'latched':BOOL}),['controller'],'冷态明确复位，不自动恢复振动')
UNSUPPORTED=['fixture.bending','fixture.shear','fixture.compression','fixture.immersion','autostrain.native','tts.native','creep.certified','noise.empirical','accuracy.certification','hardware.connect','robot.load','sample.fracture']
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'明确未实现：'+k)
for k,c in [('plant.configured','controller'),('power.enabled','power_switch'),('sample.present','specimen'),('gas.opened','air_bearing'),('thermal.enabled','furnace'),('emergency.latched','controller'),('furnace.opened','furnace'),('furnace.moving','furnace'),('clamp.locked','clamp_jaw'),('clamp.moving','clamp_jaw'),('drive.moving','drive_shaft')]:slot(k,BOOL,'1','实际状态/工程互锁',component=c)
for k,lo,hi,c in [('furnace.lift_m',0,.15,'furnace'),('furnace.target_m',0,.15,'furnace'),('clamp.release_m',0,.002,'clamp_jaw'),('clamp.target_m',0,.002,'clamp_jaw'),('drive.displacement_m',-.005,.020,'drive_shaft'),('drive.target_m',-.005,.020,'drive_shaft')]:slot(k,number('m',lo,hi),'m','有限运动位置或目标，drive正向下移','setpoint' if 'target' in k else 'scene',component=c)
slot('power.angle_rad',number('rad',0,.28),'rad','电源开关姿态','scene',component='power_switch');slot('drive.preload_n',number('N',0,18),'N','静态预载设定','setpoint',component='drive_shaft');slot('drive.mode',enum(['hold','move','preload','release','oscillate']),'1','实际运动模式')
for k in ['thermal.target_k','thermal.final_target_k']:slot(k,number('K',113.15,873.15),'K','有限速温度目标','setpoint',component='furnace')
for k,u in [('force.indicated_n','N'),('displacement.indicated_m','m'),('furnace.indicated_k','K'),('sample.indicated_k','K')]:slot(k,nullable(number(u)),u,'独立工程传感通道',component='load_cell' if k.startswith('force') else 'controller')
for k,u in [('truth.force_n','N'),('truth.strain','1'),('truth.elastic_j','J'),('truth.sample_k','K'),('truth.furnace_k','K'),('truth.air_mol','mol'),('truth.n2_mol','mol'),('truth.coolant_kg','kg'),('truth.sample_mass_kg','kg')]:slot(k,number(u),u,'过程真值，仅评估器','process_truth','evaluator')
slot('clock.time_s',number('s',0),'s','宿主物理时钟');slot('acquisition.phase',PHASE,'1','异步状态');slot('acquisition.elapsed_s',number('s',0),'s','实际采集时间');slot('acquisition.points',integer(0,2000000),'1','内部观测点数');slot('acquisition.block_index',integer(0,99),'1','温频区块');slot('acquisition.stage',enum(['conditioning','settle','measure']),'1','实际预调温/稳态等待/测量');slot('panel.temperature_text',TEXT,'1','断电空温度C一位小数');slot('panel.displacement_text',TEXT,'1','断电空实际位移mm四位小数')
BLOCK_RESULT['properties'].update(force_target_n=nullable(number('N')),force_tracking_error_n=nullable(number('N')));BLOCK_RESULT['required']=list(BLOCK_RESULT['properties'])
_t=np.linspace(0,2,257);CAPS['analysis.modulus']['example_arguments']=dict(time_s=_t.tolist(),force_n=(.2+.01*np.sin(2*np.pi*_t)+.002*np.cos(2*np.pi*_t)).tolist(),displacement_m=(1e-6*np.sin(2*np.pi*_t)).tolist(),frequency_hz=1.,length_m=.01,area_m2=8e-7)

def source_pair(record,temperature_k,frequency_hz):
 if not record.get('runtime_usable'):raise ActionRejected('INVALID_REFERENCE')
 domain=record.get('runtime_domain',{})
 if not domain.get('minimum_temperature_k',-math.inf)<=temperature_k<=domain.get('maximum_temperature_k',math.inf):raise ActionRejected('SOURCE_TEMPERATURE_RANGE')
 try:
  v=record['values'];rows=[(float(T),float(E),float(D)) for T,f,E,D in zip(v['temperature_k'],v['frequency_hz'],v['storage_modulus_pa'],v['loss_modulus_pa']) if math.isclose(float(f),frequency_hz,rel_tol=1e-7,abs_tol=1e-9)]
 except (KeyError,TypeError,ValueError):raise ActionRejected('INVALID_REFERENCE')
 if not rows or any(not all(math.isfinite(x) for x in r) for r in rows):raise ActionRejected('SOURCE_FREQUENCY_RANGE')
 rows.sort();unique=[]
 for row in rows:
  if unique and abs(row[0]-unique[-1][0])<1e-10:continue
  unique.append(row)
 if temperature_k<unique[0][0]-1e-8 or temperature_k>unique[-1][0]+1e-8:raise ActionRejected('SOURCE_TEMPERATURE_RANGE')
 E=float(np.interp(temperature_k,[r[0] for r in unique],[r[1] for r in unique]));D=float(np.interp(temperature_k,[r[0] for r in unique],[r[2] for r in unique]))
 if E<=0 or D<0:raise ActionRejected('NONPASSIVE_SOURCE')
 return E,D

class TaDma850Runtime:
 def __init__(self,instance_id='dma850_01'):
  self.instance_id=instance_id;self.plant=None;self.sample=None;self.reference=None;self.E=0.;self.branches=[];self.stress=[];self.power=False;self.emergency=False;self.furnace_lift=.15;self.furnace_target=.15;self.clamp=.002;self.clamp_target=.002;self.drive=0.;self.drive_target=0.;self.drive_mode='hold';self.drive_velocity=0.;self.preload=0.;self.gases={};self.gas_remaining={'air':0.,'N2':0.};self.gas_loaded={'air':0.,'N2':0.};self.gas_used={'air':0.,'N2':0.};self.gas_recovered={'air':0.,'N2':0.};self.gas_open=False;self.coolant_present=False;self.coolant_remaining=0.;self.coolant_loaded=0.;self.coolant_used=0.;self.coolant_recovered=0.;self.coolant_cold_used=0.;self.thermal_enabled=False;self.target=298.15;self.final_target=298.15;self.rate=1/6;self.furnace=298.15;self.sample_temperature=298.15;self.sample_energy=0.;self.heater_input=0.;self.cooling_removed=0.;self.ambient_loss=0.;self.recovered_sensible=0.;self.mechanical_input=0.;self.mechanical_recovered=0.;self.dissipated=0.;self.recovered_elastic=0.;self.mass_loaded=0.;self.mass_recovered=0.;self.obs_force=0.;self.obs_displacement=0.;self.calibration=copy.deepcopy(CALIBRATION_EXAMPLE);self.method=copy.deepcopy(METHOD_EXAMPLE);self.phase='idle';self.stage='conditioning';self.elapsed=0.;self.block_index=0;self.block_elapsed=0.;self.block_amplitude=0.;self.bias=0.;self.rows=[];self.results=[];self.error=None;self.pending_fault=None;self.operation_id=None;self.operation_reference=None;self.operation_source=None;self.operation_method=None;self.operation_calibration=None;self.records={};self._counter=0;self.time_s=0.;self._ticks=0;self._remainder=0.;self.revision=0;self._requests={};self._owners={};self._cancel_operations={};self._principals={'agent':'agent','evaluator':'evaluator'}
 def register_principal(self,principal_id,visibility='agent'):
  if not isinstance(principal_id,str) or not principal_id or principal_id in self._principals or visibility not in ('agent','debug','evaluator'):raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='agent'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return TaDma850Session(self,principal_id)
 def describe(self):return export_ta_dma850_contract_metadata()
 def _base(self):return self.sample['length_m']-.01 if self.sample else 0.
 def _area(self):return self.sample['width_m']*self.sample['thickness_m'] if self.sample['form']=='film' else math.pi*self.sample['diameter_m']**2/4
 def _volume(self):return self._area()*self.sample['length_m'] if self.sample else 0.
 def _strain(self):return (self.drive-self._base())/self.sample['length_m'] if self.sample else 0.
 def _force(self):return self._area()*(self.E*self._strain()+sum(self.stress)) if self.sample else 0.
 def _elastic(self):return self._volume()*(self.E*self._strain()**2/2+sum(q*q/(2*b['modulus_pa']) for q,b in zip(self.stress,self.branches))) if self.sample else 0.
 def _cp(self):return self.plant['fixture_capacity_j_k']+(self.sample['mass_kg']*self.sample['heat_capacity_j_kg_k'] if self.sample else 0.)
 def _effective_branches(self):
  factor=1.
  if self.sample and self.sample['response']['mode']=='engineering_maxwell':
   s=self.sample['response'];factor=math.exp(max(-40.,min(40.,s['activation_j_mol']/R_GAS*(1/self.sample_temperature-1/s['reference_temperature_k']))))
  return [dict(modulus_pa=b['modulus_pa'],relaxation_s=b['relaxation_s']*factor) for b in self.branches]
 def _cold(self):return max(self.furnace,self.sample_temperature)<=self.plant['safe_access_temperature_k']
 def _moving(self):return bool(abs(self.furnace_lift-self.furnace_target)>1e-12 or abs(self.clamp-self.clamp_target)>1e-12 or self.drive_mode!='hold')
 def _mechanical(self):
  if self.emergency:raise ActionRejected('EMERGENCY_LATCHED')
  if self.phase=='running':raise ActionRejected('BUSY')
  if not self._cold():raise ActionRejected('HOT_CELL')
  if self.thermal_enabled:raise ActionRejected('THERMAL_CONTROL_ACTIVE')
  if self._moving():raise ActionRejected('MECHANISM_MOVING')
 def _temperature_domain(self,t):
  if not (113.15 if self.plant['cooler']=='NPC' else self.plant['ambient_k'])<=t<=873.15:raise ActionRejected('COOLER_TEMPERATURE_RANGE')
 def _ready(self):
  if not self.power or self.emergency or not self.sample or self.furnace_lift>1e-12 or self.clamp>1e-12 or not self.gas_open or self.gas_remaining['air']<=0 or self.gas_remaining['N2']<=0:raise ActionRejected('NOT_READY')
  if self.drive_mode!='hold' or abs(self.furnace_lift-self.furnace_target)>1e-12:raise ActionRejected('MECHANISM_MOVING')
 def _indicated(self):
  if not self.power or not self.plant:return dict(force=None,displacement=None,furnace=None,sample=None)
  p=self.plant;c=self.calibration;F=(self.obs_force-c['force_zero_n'])*c['force_gain_correction'];x=(self.obs_displacement-c['displacement_zero_m'])*c['displacement_gain_correction'];return dict(force=F if abs(F)<=18 else None,displacement=float(round(x/1e-10)*1e-10),furnace=(p['temperature_gain']*self.furnace+p['temperature_offset_k']-c['temperature_zero_k'])*c['temperature_gain_correction'],sample=(p['temperature_gain']*self.sample_temperature+p['temperature_offset_k']-c['temperature_zero_k'])*c['temperature_gain_correction'])
 def read(self,role='evaluator'):
  a=self._indicated();d={'plant.configured':self.plant is not None,'power.enabled':self.power,'sample.present':self.sample is not None,'gas.opened':self.gas_open,'thermal.enabled':self.thermal_enabled,'emergency.latched':self.emergency,'furnace.opened':self.furnace_lift>=.15-1e-12,'furnace.moving':abs(self.furnace_lift-self.furnace_target)>1e-12,'clamp.locked':self.clamp<1e-12,'clamp.moving':abs(self.clamp-self.clamp_target)>1e-12,'drive.moving':self.drive_mode!='hold','furnace.lift_m':self.furnace_lift,'furnace.target_m':self.furnace_target,'clamp.release_m':self.clamp,'clamp.target_m':self.clamp_target,'drive.displacement_m':self.drive,'drive.target_m':self.drive_target,'power.angle_rad':.28 if self.power else 0.,'drive.preload_n':self.preload,'drive.mode':self.drive_mode,'thermal.target_k':self.target,'thermal.final_target_k':self.final_target,'force.indicated_n':a['force'],'displacement.indicated_m':a['displacement'],'furnace.indicated_k':a['furnace'],'sample.indicated_k':a['sample'],'truth.force_n':self._force(),'truth.strain':self._strain(),'truth.elastic_j':self._elastic(),'truth.sample_k':self.sample_temperature,'truth.furnace_k':self.furnace,'truth.air_mol':self.gas_remaining['air'],'truth.n2_mol':self.gas_remaining['N2'],'truth.coolant_kg':self.coolant_remaining,'truth.sample_mass_kg':self.sample['mass_kg'] if self.sample else 0.,'clock.time_s':self.time_s,'acquisition.phase':self.phase,'acquisition.elapsed_s':self.elapsed,'acquisition.points':len(self.rows),'acquisition.block_index':self.block_index,'acquisition.stage':self.stage,'panel.temperature_text':'' if a['furnace'] is None else f"{a['furnace']-273.15:.1f}",'panel.displacement_text':'' if a['displacement'] is None else f"{a['displacement']*1000:.4f}"};return {k:v for k,v in d.items() if role!='agent' or SLOTS[k]['visibility']=='agent'}
 def _ledger(self):
  mass=self.sample['mass_kg'] if self.sample else 0.;Ef=self.plant['furnace_capacity_j_k']*(self.furnace-self.plant['ambient_k']) if self.plant else 0.;available=self.coolant_used*self.plant['coolant_latent_j_kg']*self.plant['coolant_efficiency'] if self.plant else 0.;U=self._elastic();d=dict(sample_loaded_kg=self.mass_loaded,sample_present_kg=mass,sample_recovered_kg=self.mass_recovered,sample_error_kg=self.mass_loaded-mass-self.mass_recovered,coolant_loaded_kg=self.coolant_loaded,coolant_remaining_kg=self.coolant_remaining,coolant_evaporated_kg=self.coolant_used,coolant_recovered_kg=self.coolant_recovered,coolant_error_kg=self.coolant_loaded-self.coolant_remaining-self.coolant_used-self.coolant_recovered,mechanical_input_j=self.mechanical_input,mechanical_recovered_j=self.mechanical_recovered,elastic_stored_j=U,dissipated_j=self.dissipated,recovered_elastic_j=self.recovered_elastic,mechanical_error_j=self.mechanical_input-self.mechanical_recovered-U-self.dissipated-self.recovered_elastic,heater_input_j=self.heater_input,ambient_loss_j=self.ambient_loss,cooling_removed_j=self.cooling_removed,coolant_cold_available_j=available,coolant_cold_used_j=self.coolant_cold_used,coolant_cold_error_j=available-self.coolant_cold_used,furnace_stored_j=Ef,sample_stored_j=self.sample_energy,recovered_sensible_j=self.recovered_sensible,thermal_error_j=self.heater_input+self.dissipated-self.ambient_loss-self.cooling_removed-Ef-self.sample_energy-self.recovered_sensible)
  for g in ['air','N2']:
   vals=dict(loaded=self.gas_loaded[g],remaining=self.gas_remaining[g],exhausted=self.gas_used[g],recovered=self.gas_recovered[g],error=self.gas_loaded[g]-self.gas_remaining[g]-self.gas_used[g]-self.gas_recovered[g]);d.update({g.lower()+'_'+k+'_mol':v for k,v in vals.items()})
  return d
 def _row(self):
  a=self._indicated();b=self.method['blocks'][self.block_index];return dict(time_s=self.time_s,elapsed_s=self.elapsed,block_index=self.block_index,stage=self.stage,frequency_hz=b['frequency_hz'],force_n=a['force'],displacement_m=a['displacement'],sample_temperature_k=a['sample'])
 def _record(self):
  if self.stage=='conditioning':return
  r=self._row()
  if self.rows and abs(self.rows[-1]['time_s']-r['time_s'])<1e-7:self.rows[-1]=r
  else:self.rows.append(r)
 def _result(self):
  if self.phase!='running' and self.operation_id in self.records:return copy.deepcopy(self.records[self.operation_id])
  return dict(operation_id=self.operation_id,phase=self.phase,elapsed_s=self.elapsed,rows=copy.deepcopy(self.rows),blocks=copy.deepcopy(self.results),reference_id=self.operation_reference,template_source=self.operation_source,method_json=json.dumps(self.operation_method,sort_keys=True) if self.operation_method else None,calibration_json=json.dumps(self.operation_calibration,sort_keys=True) if self.operation_calibration else None,evidence='Uniform uniaxial linear tension. Raw F/x observations with independent gain/offset/dynamics and displacement quantization drive OLS complex modulus, never direct material coefficients. Source-anchor SLS matches one measured T/f pair using explicitly assumed relaxation time; it is not an identified broadband spectrum. Engineering Maxwell spectra/temperature shift are separate evaluator inputs. Force-amplitude path is ideal harmonic displacement feedforward, not OEM closed-loop force control; actual tracking error is reported. Internal>=64 samples/cycle prevent host0.1s aliasing. Static preload, unilateral unload and retained Maxwell memory conserve work/elastic energy/heat; no fracture or sample loss.',error_code=self.error)
 def _finish(self,phase,error=None):
  self.phase=phase;self.error=error;self.thermal_enabled=False;self.drive_mode='hold';self.drive_target=self.drive;self._record();self.records[self.operation_id]=self._result()
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
 def _invoke(self,k,a):
  if k=='plant.configure':
   if self.plant or self.time_s!=0 or self.power:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_engineering'] or a['safe_access_temperature_k']<a['ambient_k']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   self.plant=copy.deepcopy(a);self.furnace=self.sample_temperature=self.target=self.final_target=a['ambient_k'];self.obs_force=a['force_offset_n'];self.obs_displacement=a['displacement_offset_m'];return {'configured':True}
  if not self.plant and k not in ['reference.query','analysis.modulus','analysis.transition','ledger.read','acquisition.result']:raise ActionRejected('NOT_CONFIGURED')
  if k=='sample.load':
   self._mechanical()
   if self.sample:raise ActionRejected('ALREADY_LOADED')
   if self.furnace_lift<.15-1e-12 or self.clamp<.002-1e-12 or abs(self.drive-(a['length_m']-.01))>1e-9:raise ActionRejected('SAMPLE_GEOMETRY_NOT_READY')
   if not a['accept_engineering_geometry']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   r=a['response'];ref=None
   if r['mode']=='engineering_maxwell':
    if not r['accept_engineering_spectrum']:raise ActionRejected('MODEL_NOT_ACCEPTED')
    E=r['equilibrium_modulus_pa'];branches=copy.deepcopy(r['branches'])
   else:
    if not r['accept_equivalent_model'] or not r['accept_condition_transfer']:raise ActionRejected('CONDITION_TRANSFER_NOT_ACCEPTED')
    ref=library()['substances'].get(r['reference_id'])
    if not ref:raise ActionRejected('INVALID_REFERENCE')
    real,imag=source_pair(ref,r['temperature_k'],r['frequency_hz']);z=2*math.pi*r['frequency_hz']*r['anchor_relaxation_s'];E=real-imag*z;branches=[dict(modulus_pa=imag*(1+z*z)/z,relaxation_s=r['anchor_relaxation_s'])] if imag>0 else []
    if E<=0:raise ActionRejected('NONPASSIVE_ANCHOR')
   self.sample=copy.deepcopy(a);self.reference=copy.deepcopy(ref);self.E=E;self.branches=branches;self.stress=[0.]*len(branches);self.preload=0.;self.sample_temperature=self.plant['ambient_k']+self.sample_energy/self._cp();self.mass_loaded+=a['mass_kg'];return {'loaded':True}
  if k=='sample.remove':
   self._mechanical()
   if not self.sample:raise ActionRejected('NO_SAMPLE')
   if self.furnace_lift<.15-1e-12 or self.clamp<.002-1e-12 or abs(self._force())>1e-4 or abs(self._strain())>1e-6:raise ActionRejected('SAMPLE_NOT_UNLOADED')
   energy=self.sample['mass_kg']*self.sample['heat_capacity_j_kg_k']*(self.sample_temperature-self.plant['ambient_k']);self.recovered_sensible+=energy;self.sample_energy-=energy;self.recovered_elastic+=self._elastic();self.mass_recovered+=self.sample['mass_kg'];self.sample=None;self.reference=None;self.E=0.;self.branches=[];self.stress=[];self.preload=0.;return {'removed':True}
  if k=='gas.load':
   g=a['gas']
   if g in self.gases or self.gas_open or self.phase=='running':raise ActionRejected('BUSY')
   if not a['accept_reference_conditions']:raise ActionRejected('REFERENCE_CONDITIONS_NOT_ACCEPTED')
   self.gases[g]=copy.deepcopy(a);self.gas_remaining[g]=a['amount_mol'];self.gas_loaded[g]+=a['amount_mol'];return {'loaded':True}
  if k=='gas.unload':
   g=a['gas']
   if self.gas_open or self.phase=='running':raise ActionRejected('BUSY')
   if g not in self.gases:raise ActionRejected('NOT_LOADED')
   self.gas_recovered[g]+=self.gas_remaining[g];self.gas_remaining[g]=0.;del self.gases[g];return {'unloaded':True}
  if k=='gas.set_open':
   if self.phase=='running' or self.drive_mode!='hold':raise ActionRejected('BUSY')
   if a['opened'] and (not self.power or self.emergency or min(self.gas_remaining.values())<=0):raise ActionRejected('NO_GAS')
   self.gas_open=a['opened']
   if not self.gas_open:self.thermal_enabled=False
   return {'opened':self.gas_open}
  if k=='coolant.load':
   if self.coolant_present or self.phase=='running' or self.thermal_enabled:raise ActionRejected('BUSY')
   if self.plant['cooler']!='NPC' or not a['accept_engineering_latent']:raise ActionRejected('COOLER_NOT_CONFIGURED')
   self.coolant_present=True;self.coolant_remaining=a['amount_kg'];self.coolant_loaded+=a['amount_kg'];return {'loaded':True}
  if k=='coolant.unload':
   if not self.coolant_present:raise ActionRejected('NOT_LOADED')
   if self.phase=='running' or self.thermal_enabled:raise ActionRejected('BUSY')
   self.coolant_recovered+=self.coolant_remaining;self.coolant_remaining=0.;self.coolant_present=False;return {'unloaded':True}
  if k=='power.set':
   if self.phase=='running':raise ActionRejected('BUSY')
   self.power=a['enabled']
   if not self.power:self.thermal_enabled=False;self.gas_open=False;self.drive_mode='hold';self.drive_target=self.drive;self.furnace_target=self.furnace_lift;self.clamp_target=self.clamp
   return {'enabled':self.power}
  if k=='furnace.set':
   self._mechanical()
   if not self.power:raise ActionRejected('NOT_READY')
   self.furnace_target=.15 if a['opened'] else 0.;return {'opened':a['opened']}
  if k=='clamp.set':
   self._mechanical()
   if self.furnace_lift<.15-1e-12 or not self.power or abs(self._force())>1e-4 or abs(self._strain())>1e-6:raise ActionRejected('CLAMP_UNDER_LOAD')
   self.clamp_target=0. if a['locked'] else .002;return {'locked':a['locked']}
  if k=='drive.move':
   self._mechanical()
   if self.sample or self.furnace_lift<.15-1e-12 or self.clamp<.002-1e-12 or not self.power or not self.gas_open:raise ActionRejected('EMPTY_DRIVE_ONLY')
   self.drive_target=a['position_m'];self.drive_velocity=self.plant['drive_speed_m_s'];self.drive_mode='move';return {'accepted':True}
  if k in ['drive.preload','drive.unload']:
   if self.phase=='running' or self.drive_mode!='hold':raise ActionRejected('BUSY')
   if not self.sample or self.clamp>1e-12 or not self.power or self.emergency or not self.gas_open:raise ActionRejected('NOT_READY')
   if k=='drive.preload':
    extension=a['force_n']*self.sample['length_m']/(self._area()*self.E);target=self._base()+extension
    if extension/self.sample['length_m']>self.sample['maximum_linear_strain'] or target>.020 or a['force_n']*(self.E+sum(b['modulus_pa'] for b in self.branches))/self.E>18:raise ActionRejected('LINEAR_FORCE_DOMAIN')
    self.preload=a['force_n'];self.drive_target=target;self.drive_mode='preload'
   else:self.drive_target=self._base();self.drive_mode='release';self.preload=0.
   self.drive_velocity=abs(self.drive_target-self.drive)/a['ramp_s'];return {'accepted':True}
  if k=='thermal.set_temperature':
   if self.phase=='running':raise ActionRejected('BUSY')
   if not self.power or self.emergency or self.furnace_lift>1e-12 or not self.gas_open:raise ActionRejected('NOT_READY')
   self._temperature_domain(a['temperature_k'])
   if a['temperature_k']<self.furnace and a['rate_k_s']>10/60:raise ActionRejected('COOLING_RATE')
   if a['temperature_k']<self.plant['ambient_k'] and self.coolant_remaining<=0:raise ActionRejected('NO_COOLANT')
   self.final_target=a['temperature_k'];self.rate=a['rate_k_s'];self.thermal_enabled=True;return {'target_k':self.final_target}
  if k=='thermal.disable':
   if self.phase=='running':raise ActionRejected('BUSY')
   self.thermal_enabled=False;return {'enabled':False}
  if k=='sensor.calibrate':
   if self.phase=='running':raise ActionRejected('BUSY')
   self.calibration=copy.deepcopy(a);return copy.deepcopy(a)
  if k=='sensor.zero':
   if self.sample or self._moving() or not self.power:raise ActionRejected('ZERO_REQUIRES_EMPTY')
   self.calibration['force_zero_n']=self.obs_force;self.calibration['displacement_zero_m']=self.obs_displacement;return {'zeroed':True}
  if k=='method.configure':
   if self.phase=='running':raise ActionRejected('BUSY')
   for b in a['blocks']:
    self._temperature_domain(b['temperature_k'])
    if self.sample and self.sample['response']['mode']=='source_anchor':
     r=self.sample['response']
     if abs(b['temperature_k']-r['temperature_k'])>1e-8 or not math.isclose(b['frequency_hz'],r['frequency_hz'],rel_tol=1e-7):raise ActionRejected('SOURCE_ANCHOR_ONLY')
   if sum((b['settle_cycles']+b['measure_cycles'])*a['samples_per_cycle']+2 for b in a['blocks'])>1999900:raise ActionRejected('TOO_MANY_POINTS')
   self.method=copy.deepcopy(a);return copy.deepcopy(a)
  if k=='acquisition.start':
   if self.phase=='running':raise ActionRejected('BUSY')
   self._ready()
   if self.preload<=0 or self._force()<=0:raise ActionRejected('NO_PRELOAD')
   for b in self.method['blocks']:self._validate_block(b)
   self._counter+=1;self.operation_id=f'{self.instance_id}:dma:{self._counter}';self.phase='running';self.stage='conditioning';self.elapsed=0.;self.block_index=0;self.block_elapsed=0.;self.rows=[];self.results=[];self.error=None;self.pending_fault=None;self.operation_reference=self.sample['response'].get('reference_id');self.operation_source=json.dumps(self.reference.get('source',{}),sort_keys=True) if self.reference else 'explicit_evaluator_engineering_Maxwell_spectrum';self.operation_method=copy.deepcopy(self.method);self.operation_calibration=copy.deepcopy(self.calibration);self.thermal_enabled=True;self.final_target=self.method['blocks'][0]['temperature_k'];return {'operation_id':self.operation_id}
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self._finish('cancelled');return {'phase':'cancelled'}
  if k=='acquisition.result':return self._result()
  if k=='reference.query':
   r=library()['substances'].get(a['reference_id'])
   if not r:raise ActionRejected('INVALID_REFERENCE')
   return dict(reference_json=json.dumps(r,sort_keys=True,allow_nan=False),template_source=json.dumps(r.get('source',{}),sort_keys=True))
  if k=='analysis.modulus':return fit_dynamic_modulus(**a)
  if k=='analysis.transition':return transition_peak(**a)
  if k=='ledger.read':return self._ledger()
  if k=='emergency.stop':
   self.emergency=True;self.thermal_enabled=False;self.gas_open=False;self.drive_mode='hold';self.drive_target=self.drive;self.furnace_target=self.furnace_lift;self.clamp_target=self.clamp
   if self.phase=='running':self._finish('failed','SOFTWARE_STOP')
   return {'latched':True}
  if k=='emergency.reset':
   if not self._cold():raise ActionRejected('HOT_CELL')
   self.emergency=False;return {'latched':False}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _validate_block(self,b):
  self._temperature_domain(b['temperature_k']);r=self.sample['response']
  if r['mode']=='source_anchor' and (abs(b['temperature_k']-r['temperature_k'])>1e-8 or not math.isclose(b['frequency_hz'],r['frequency_hz'],rel_tol=1e-7)):raise ActionRejected('SOURCE_ANCHOR_ONLY')
  z=maxwell_modulus(b['frequency_hz'],self.E,self._effective_branches());amp=b['strain_amplitude']*self.sample['length_m'] if b['control']=='strain' else b['force_amplitude_n']*self.sample['length_m']/(self._area()*abs(z));dynamic=self._area()*abs(z)*amp/self.sample['length_m'];bias=self.preload*self.sample['length_m']/(self._area()*self.E)
  if not 5e-9<=amp<=.01 or (bias+amp)/self.sample['length_m']>self.sample['maximum_linear_strain'] or self._base()+bias+amp>.020 or self._base()+bias-amp<-.005:raise ActionRejected('LINEAR_AMPLITUDE_DOMAIN')
  if dynamic<1e-4 or self.preload-dynamic<=0 or self.preload+dynamic>18:raise ActionRejected('TENSION_FORCE_DOMAIN')
  return amp,bias
 def _mechanics(self,h,new_drive):
  if not self.sample:self.drive=new_drive;return 0.
  eps0=self._strain();eps1=(new_drive-self._base())/self.sample['length_m'];branches=self._effective_branches()
  if self.drive_mode=='release':
   coefficients=[b['modulus_pa']*(-math.expm1(-h/b['relaxation_s']))/(h/b['relaxation_s']) for b in branches];surviving=sum(q*math.exp(-h/b['relaxation_s']) for q,b in zip(self.stress,branches));free=(sum(coefficients)*eps0-surviving)/(self.E+sum(coefficients));eps1=max(eps1,free,0.);new_drive=self._base()+eps1*self.sample['length_m']
  result=maxwell_step(eps0,eps1,self.stress,self.E,branches,h,self._volume());force=self._area()*(self.E*eps1+sum(result['stress_pa']))
  if abs(force)>18+1e-10 or (self.drive_mode=='oscillate' and force<=0) or abs(eps1)>self.sample['maximum_linear_strain']+1e-12:
   self.pending_fault='FORCE_OR_LINEAR_DOMAIN';self.drive_mode='hold';new_drive=self.drive;result=maxwell_step(eps0,eps0,self.stress,self.E,branches,h,self._volume())
  self.drive=new_drive;self.stress=result['stress_pa'];w=result['work_j'];self.mechanical_input+=max(w,0.);self.mechanical_recovered+=max(-w,0.);self.dissipated+=result['dissipated_j'];return result['dissipated_j']
 def _heat(self,h,diss):
  p=self.plant;amb=p['ambient_k'];C=self._cp();G=p['sample_conductance_w_k'];cf=p['furnace_capacity_j_k'];ga=p['ambient_conductance_w_k'];gain=p['controller_gain_w_k'] if self.thermal_enabled and self.power and not self.emergency else 0.;energy=self.sample_energy+diss;den=C+h*G;a=energy/den;b=h*G/den;base=cf/h+ga+G*(1-b);rhs=cf/h*(self.furnace-amb)+G*a;target=self.target-amb;x=(rhs+gain*target)/(base+gain);power=gain*(target-x);maximum_cold=min(p['cooler_max_w'],self.coolant_remaining*p['coolant_latent_j_kg']*p['coolant_efficiency']/h) if p['cooler']=='NPC' else 0.
  if power>p['heater_max_w']:power=p['heater_max_w'];x=(rhs+power)/base
  if power<-maximum_cold:power=-maximum_cold;x=(rhs+power)/base
  self.furnace=amb+x;self.sample_temperature=amb+a+b*x;self.sample_energy=C*(self.sample_temperature-amb);self.heater_input+=max(power,0.)*h;removed=max(-power,0.)*h;self.cooling_removed+=removed;self.ambient_loss+=ga*x*h
  if removed>0:
   amount=min(self.coolant_remaining,removed/(p['coolant_latent_j_kg']*p['coolant_efficiency']));self.coolant_remaining-=amount;self.coolant_used+=amount;self.coolant_cold_used+=removed
  if self.thermal_enabled and self.target<amb and self.coolant_remaining<=1e-20:self.pending_fault='COOLANT_EXHAUSTED';self.thermal_enabled=False
 def _sensor(self,oldF,oldx,h):
  p=self.plant
  for name,tau,u0,u1 in [('obs_force',p['force_sensor_tau_s'],p['force_gain']*oldF+p['force_offset_n'],p['force_gain']*self._force()+p['force_offset_n']),('obs_displacement',p['displacement_sensor_tau_s'],p['displacement_gain']*oldx+p['displacement_offset_m'],p['displacement_gain']*self.drive+p['displacement_offset_m'])]:
   r=h/tau;one=-math.expm1(-r);ramp=r/2-r*r/6+r**3/24 if r<1e-4 else 1-one/r;setattr(self,name,getattr(self,name)*(1-one)+u0*one+(u1-u0)*ramp)
 def _complete_block(self):
  b=self.method['blocks'][self.block_index];rows=[r for r in self.rows if r['block_index']==self.block_index and r['stage']=='measure'];error=None;analysis=None
  try:analysis=fit_dynamic_modulus([r['time_s'] for r in rows],[r['force_n'] for r in rows],[r['displacement_m'] for r in rows],b['frequency_hz'],self.sample['length_m'],self._area())
  except (ActionRejected,TypeError,ValueError) as exc:error=str(exc)
  self.results.append(dict(block_index=self.block_index,frequency_hz=b['frequency_hz'],mean_temperature_k=float(np.mean([r['sample_temperature_k'] for r in rows])) if rows else self.sample_temperature,analysis=analysis,analysis_error=error,force_target_n=b['force_amplitude_n'] if b['control']=='force' else None,force_tracking_error_n=(analysis['force_amplitude_n']-b['force_amplitude_n']) if analysis and b['control']=='force' else None))
  if self.block_index+1==len(self.method['blocks']):self._finish('succeeded')
  else:self.block_index+=1;self.stage='conditioning';self.block_elapsed=0.;self.drive_mode='hold';self.drive_target=self.drive;self.final_target=self.method['blocks'][self.block_index]['temperature_k']
 def _advance(self,dt):
  left=dt
  while left>1e-12:
   h=min(.01,left);running=self.phase=='running';b=self.method['blocks'][self.block_index]
   if running:
    self.final_target=b['temperature_k'];self.rate=self.method['heating_rate_k_s'] if self.final_target>=self.target else self.method['cooling_rate_k_s'];h=min(h,max(0.,self.method['timeout_s']-self.elapsed))
    if self.stage=='conditioning' and max(abs(self.furnace-b['temperature_k']),abs(self.sample_temperature-b['temperature_k']))<=self.method['temperature_tolerance_k']:
     try:self.block_amplitude,self.bias=self._validate_block(b)
     except ActionRejected as exc:self._finish('failed',str(exc));continue
     self.stage='settle';self.block_elapsed=0.;self.drive_mode='oscillate';self._record()
    if self.stage!='conditioning':
     grid=1/(b['frequency_hz']*self.method['samples_per_cycle']);nextgrid=(math.floor(self.block_elapsed/grid+1e-7)+1)*grid;end=(b['settle_cycles']+b['measure_cycles'])/b['frequency_hz'];h=min(h,max(1e-16,nextgrid-self.block_elapsed),max(1e-16,end-self.block_elapsed))
   rates={'air':0.,'N2':0.}
   if self.gas_open:
    npc=self.plant['npc_n2_flow_m3_s'] if self.plant['cooler']=='NPC' and self.thermal_enabled and self.final_target<self.plant['ambient_k'] else 0.
    for gas,flow in [('air',self.plant['air_flow_m3_s']),('N2',self.plant['purge_flow_m3_s']+npc)]:
     stock=self.gases[gas];rates[gas]=flow*stock['reference_pressure_pa']/(R_GAS*stock['reference_temperature_k'])
   available=min([self.gas_remaining[g]/r for g,r in rates.items() if r>0] or [math.inf]);h=min(h,available)
   if h<=0:
    if running:self._finish('failed','TIMEOUT' if self.elapsed>=self.method['timeout_s']-1e-10 else 'GAS_EXHAUSTED')
    self.gas_open=False;self.thermal_enabled=False;self.drive_mode='hold';self.drive_target=self.drive;continue
   oldF=self._force();oldx=self.drive;newx=self.drive
   if self.plant:
    if self.thermal_enabled:self.target+=max(-self.rate*h,min(self.rate*h,self.final_target-self.target))
    if self.power and not self.emergency:
     self.furnace_lift+=max(-self.plant['furnace_speed_m_s']*h,min(self.plant['furnace_speed_m_s']*h,self.furnace_target-self.furnace_lift));self.clamp+=max(-self.plant['clamp_speed_m_s']*h,min(self.plant['clamp_speed_m_s']*h,self.clamp_target-self.clamp))
     if self.gas_open:
      if self.drive_mode=='oscillate':newx=self._base()+self.bias+self.block_amplitude*math.sin(2*math.pi*b['frequency_hz']*(self.block_elapsed+h));self.drive_target=newx
      elif self.drive_mode in ['move','preload','release']:newx=self.drive+max(-self.drive_velocity*h,min(self.drive_velocity*h,self.drive_target-self.drive))
    dissipated=self._mechanics(h,newx);self._heat(h,dissipated);self._sensor(oldF,oldx,h)
    if self.drive_mode in ['move','preload','release'] and abs(self.drive-self.drive_target)<1e-12:self.drive_mode='hold'
   for g,r in rates.items():used=min(self.gas_remaining[g],r*h);self.gas_remaining[g]-=used;self.gas_used[g]+=used
   self.time_s+=h;left-=h
   if running:
    self.elapsed+=h
    if self.stage!='conditioning':
     self.block_elapsed+=h;grid=1/(b['frequency_hz']*self.method['samples_per_cycle'])
     if self.block_elapsed>=b['settle_cycles']/b['frequency_hz']-1e-9:self.stage='measure'
     if abs(self.block_elapsed/grid-round(self.block_elapsed/grid))<1e-6:self._record()
   if available<=h+1e-16:
    self.gas_open=False;self.thermal_enabled=False;self.drive_mode='hold';self.drive_target=self.drive
    if self.phase=='running':self._finish('failed','GAS_EXHAUSTED')
   if self.sample and self.sample['response']['mode']=='source_anchor' and running and self.stage!='conditioning' and abs(self.sample_temperature-self.sample['response']['temperature_k'])>self.sample['response']['temperature_tolerance_k']:self.pending_fault='SOURCE_TEMPERATURE_RANGE'
   if self.plant and not 113.15<=min(self.furnace,self.sample_temperature)<=max(self.furnace,self.sample_temperature)<=873.15:self.pending_fault='THERMAL_MODEL_RANGE'
   if self.pending_fault:
    if self.phase=='running':self._finish('failed',self.pending_fault)
    self.thermal_enabled=False;self.drive_mode='hold';self.drive_target=self.drive;self.pending_fault=None
   if self.phase=='running':
    if self.elapsed>=self.method['timeout_s']-1e-9:self._finish('failed','TIMEOUT')
    elif self.stage!='conditioning' and self.block_elapsed>=(b['settle_cycles']+b['measure_cycles'])/b['frequency_hz']-1e-9:self._complete_block()
 def step(self,dt_s):
  validate(number('s',0,3600),dt_s);self._remainder+=dt_s;ticks=int((self._remainder+1e-10)/DT);self._remainder-=ticks*DT
  if abs(self._remainder)<1e-10:self._remainder=0.
  for _ in range(ticks):self._advance(DT);self._ticks+=1;self.time_s=self._ticks*DT;self.revision+=1
  return self.read()

class TaDma850Session:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,instance_id):
  if instance_id!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  meta=export_ta_dma850_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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
'sample.load':['sample.present','truth.sample_mass_kg','truth.sample_k','drive.preload_n','truth.force_n','truth.strain','truth.elastic_j']+OBSERVATIONS,
'sample.remove':['sample.present','truth.sample_mass_kg','drive.preload_n','truth.force_n','truth.strain','truth.elastic_j']+OBSERVATIONS,
'gas.load':['truth.air_mol','truth.n2_mol'],'gas.unload':['truth.air_mol','truth.n2_mol'],'gas.set_open':['gas.opened','thermal.enabled'],
'coolant.load':['truth.coolant_kg'],'coolant.unload':['truth.coolant_kg'],
'power.set':['power.enabled','power.angle_rad','thermal.enabled','gas.opened','drive.mode','drive.moving','drive.target_m','furnace.target_m','furnace.moving','clamp.target_m','clamp.moving']+OBSERVATIONS,
'furnace.set':['furnace.target_m','furnace.moving'],'clamp.set':['clamp.target_m','clamp.moving'],
'drive.move':['drive.mode','drive.moving','drive.target_m'],'drive.preload':['drive.mode','drive.moving','drive.target_m','drive.preload_n'],'drive.unload':['drive.mode','drive.moving','drive.target_m','drive.preload_n'],
 'thermal.set_temperature':['thermal.final_target_k','thermal.enabled'],'thermal.disable':['thermal.enabled'],'sensor.calibrate':OBSERVATIONS,'sensor.zero':OBSERVATIONS,
'acquisition.start':['acquisition.phase','acquisition.elapsed_s','acquisition.points','acquisition.block_index','acquisition.stage','thermal.final_target_k','thermal.enabled'],
'acquisition.stop':['acquisition.phase','acquisition.points','thermal.enabled','drive.mode','drive.moving','drive.target_m'],
'emergency.stop':['emergency.latched','acquisition.phase','acquisition.points','thermal.enabled','gas.opened','drive.mode','drive.moving','drive.target_m','furnace.target_m','furnace.moving','clamp.target_m','clamp.moving'],
'emergency.reset':['emergency.latched'],'sim.step':list(SLOTS)}
for k,v in EFFECTS.items():CAPS[k]['effects']=v
ERRORS=['EMERGENCY_LATCHED','UNKNOWN_CAPABILITY','UNKNOWN_INSTANCE','UNKNOWN_OPERATION','INVALID_ARGUMENT','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','BUSY','NOT_RUNNING','INITIALIZATION_ONLY','MODEL_NOT_ACCEPTED','NOT_CONFIGURED','HOT_CELL','THERMAL_CONTROL_ACTIVE','MECHANISM_MOVING','ALREADY_LOADED','NOT_LOADED','INVALID_REFERENCE','CONDITION_TRANSFER_NOT_ACCEPTED','REFERENCE_CONDITIONS_NOT_ACCEPTED','NO_GAS','NO_SAMPLE','NOT_READY','COOLER_TEMPERATURE_RANGE','NO_COOLANT','COOLING_RATE','COOLER_NOT_CONFIGURED','SAMPLE_GEOMETRY_NOT_READY','SAMPLE_NOT_UNLOADED','CLAMP_UNDER_LOAD','EMPTY_DRIVE_ONLY','LINEAR_FORCE_DOMAIN','ZERO_REQUIRES_EMPTY','TOO_MANY_POINTS','SOURCE_ANCHOR_ONLY','NO_PRELOAD','LINEAR_AMPLITUDE_DOMAIN','TENSION_FORCE_DOMAIN','FORCE_OR_LINEAR_DOMAIN','THERMAL_MODEL_RANGE','SOURCE_TEMPERATURE_RANGE','SOURCE_FREQUENCY_RANGE','NONPASSIVE_SOURCE','NONPASSIVE_ANCHOR','COOLANT_EXHAUSTED','GAS_EXHAUSTED','TIMEOUT','SOFTWARE_STOP','ANALYSIS_DATA','HARMONIC_WINDOW','DISPLACEMENT_UNRESOLVED','STORAGE_UNRESOLVED','TRANSITION_DATA','MECHANICAL_DOMAIN']
def export_ta_dma850_contract_metadata():
 slots={};caps={}
 for k,v in SLOTS.items():slots[k]=dict(meaning_zh=v['meaning'],component_ids=[v['component']],value_schema=copy.deepcopy(v['schema']),unit=v['unit'],reference_conditions='SI. Gas volume uses explicit absolute reference T/P; airbearing pressure410..450kPa is gauge. Displacement0.1nm is quantization, not calibrated accuracy. Positive drive moves lower clamp down; physical gap=.010m+drive, specimen rest offset=L-.010.',origin=v['origin'],visibility=v['visibility'],access='read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'TaDma850Session.read(state_ids=[{k!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description='Host0.1s fixed partitions; physical substeps<=.01s and >=64 samples per cycle; reads never advance time.'),source_ids=['simulation_assumption'])
 for k,c in CAPS.items():
  un=k in UNSUPPORTED;asynchronous=k=='acquisition.start';caps[k]={key:copy.deepcopy(c[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']};caps[k].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ['scene','robot','hardware'] else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.ta_dma850.TaDma850Session.invoke',invocation=f'session.invoke(instance_id="dma850_01",capability_id={k!r},arguments={c["example_arguments"]!r},request_id="unique")'),preconditions=[] if c['kind'] in ['query','analysis'] or un else [dict(description_zh='冷态开炉/无载夹紧、有限airbearing与N2、正拉伸预载、源T/f域或显式工程谱，原子拒绝',state_ids=['power.enabled','furnace.lift_m','sample.present','gas.opened','drive.mode','acquisition.phase'],enforced_by='runtime',check='atomic invocation and mechanical, thermal, source-domain guards')],state_effects=[dict(state_id=x,timing='immediate',description_zh='实际命令即时状态变化') for x in c['effects']],observes=[],model_effects=dict(mode='none',description_zh='五关节（四独立运动状态）与双数值屏由场景独立验证；Matterix参考试片网格不形变',binding_ids=[]),execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',completion_criterion='真实预调温、settle周期和完整观测周期全部完成；不保证已校准材料本构' if asynchronous else '命令接受后有限运动与热/应力过程等待宿主step',completion_state_ids=['acquisition.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',retry='same_request_id',resources=['host_clock','furnace','drive_shaft','specimen','load_cell','air','N2','NPC_LN2'],partial_failure_zh='取消/断气/软件停机保留实际应变、Maxwell支路应力、弹性能与余热；终态观测不可变，不自动复原。'),errors=[dict(code=e,meaning_zh=e,side_effects='partial' if asynchronous and e in ['FORCE_OR_LINEAR_DOMAIN','THERMAL_MODEL_RANGE','SOURCE_TEMPERATURE_RANGE','COOLANT_EXHAUSTED','GAS_EXHAUSTED','TIMEOUT','SOFTWARE_STOP'] else 'none') for e in ERRORS],verification=[],limitations=['仅线性单轴拉伸film/fiber，其他夹具无默认支持','实测锚只在原频率和来源允许温区内插值，单点SLS谱依赖显式tau，并非由单点辨识材料谱','力幅值模式是谐波位移前馈，实际力跟踪误差另报，不冒称原机伺服','内部观测谐波拟合含显式传感器增益、零点、相位延迟，未制造噪声分布/厂家accuracy','NPC冷量/气量有限；炉/试片两节点工程热模型不是原机热校准；no cooler不能主动冷到ambient以下'],source_ids=['manufacturer_specs','simulation_assumption'])
  if k in ['acquisition.start','acquisition.stop','emergency.stop','power.set','thermal.set_temperature','thermal.disable','furnace.set','clamp.set','drive.move','drive.preload','drive.unload','gas.set_open']:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='宿主推进有限运动/粘弹应力/热/独立观测/气耗') for x in SLOTS if x not in ['plant.configured','power.enabled','power.angle_rad','sample.present','emergency.latched']]
 mechanism=dict(component_ids=['controller','furnace','specimen','drive_shaft','load_cell','air_bearing'],state_ids=list(slots),meaning_zh='有限资源线性拉伸Maxwell响应、观测谐波模量与机械热能守恒',implementation='lumped',equations=[
 'Geometry: A=W*t (film) or pi*d^2/4 (fiber), V=A*L, strain=(drive-(L-.010))/L. Positive lower-clamp displacement stretches sample. All A/L/mass/Cp/linear-strain limits are explicitly supplied geometry/engineering inputs.',
 'Generalized Maxwell: sigma=Einf*epsilon+sum(qi); dqi/dt=Ei*d(epsilon)/dt-qi/taui(T). With linear strain within each internal step: qi1=qi0*exp(-h/taui)+Ei*delta_epsilon*(1-exp(-h/taui))/(h/taui). tau(T)=tau_ref*exp[Ea/R*(1/T-1/Tref)] only for accepted engineering model; moduli are held constant. Exponent bounded+-40 is explicit numerical domain assumption.',
 'Stored mechanical energy U=V*(Einf*epsilon^2/2+sum(qi^2/(2Ei))). Dashpot dissipation D=V*integral sum(qi^2/(Ei*taui))dt>=0 evaluated analytically for the piecewise-linear strain; work=deltaU+D. Signed external work separates supplied/recovered; dissipation heats finite sample. No fracture/erosion, sample mass is preserved.',
 'Unilateral unloading solves sigma_new>=0 at each step, preserving branch stress and returned mechanical work rather than clipping output force or deleting elastic energy. Final zero-strain recovery is asymptotic for viscoelastic branches; finite tolerances control cold sample removal and exported remaining elastic energy is ledgered.',
 'Measured-source anchor: interpolate positive Eprime/Esecond versus T only within explicitly published runtime domain at one actual source frequency; exact f matching. Raw arrays are preserved, temperature rows are sorted and duplicate temperatures use first value. A single explicitly supplied tau constructs SLS with Ei=Esecond*(1+(omega*tau)^2)/(omega*tau), Einf=Eprime-Esecond*omega*tau>0. This is a local equivalent response, not recovered broadband kinetics. Unknown specimen strain/geometry requires explicit transfer acceptance.',
 'Displacement-controlled sine uses explicit strain amplitude. Force-amplitude setting calculates harmonic displacement amplitude=Famp*L/(A*abs(E*(omega))), with same real Maxwell transient and actual sensor fitting; this is feedforward, and fitted amplitude minus target is exported. Static preload exceeds dynamic force and combined force<=18N; instantaneous transient excursions fail, never silently clamp sensors into range.',
 'F/x sensors are independent linear first-order channels with explicit gain/zero/time constant; x quantization1e-10m. Observed E*=Fphasor/Xphasor*L/A via OLS DC+linear trend+sin/cos on uniform integer-cycle windows; Re(Z exp(iwt)) convention. Negative observed loss caused by calibration/phase bias is preserved and passive_compatible=false, never clipped. No hidden material coefficient is returned as a measurement.',
 'Thermal: Cf*dTf/dt=Pheater-Pcool-Gambient*(Tf-Tamb)-Gs*(Tf-Ts); Cs*dTs/dt=Gs*(Tf-Ts)+Ddot. Implicit energy-conserving two-node update with bounded heater/cooler; parameters explicitly engineering. NPC cooling consumes finite LN2 with supplied effective latent heat, and30L/min finite N2; none accessory cools passively to ambient only. Heat removal and signed ambient exchange stay in ledger.',
 'Airbearing2L/min and N2purge use finite inventories dn=qref*Pref/(R*Tref)*dt; reference states explicitly accepted. Exhaust moles are tracked separately. Gas enthalpy, pressure regulator dynamics and real bearing stiffness are not modeled.',
 'All oscillations internally resolve at least64 points/cycle even at200Hz with hostdt.1s; physical signal points have their actual simulation timestamps. Finite inventory endpoints, method boundaries and complete cycles split steps. Cancellation freezes partial observations but retains actual stress/heat and permits passive relaxation.',
 'Temperature/frequency sweep modulus is observed per complete block. External transition analysis reports a discrete interior tan(delta) maximum at fixed frequency only; boundary maxima remain null, and no preassigned Tg is returned.'
 ],parameters={},source_ids=['manufacturer_specs','simulation_assumption'],limitations=['No empirical noise/accuracy certification, nonlinear yield/fracture, OEM Autostrain/TTS or non-tension fixture.','Intrinsic spectrum from one Eprime/Esecond pair is not identifiable; local source anchor has an explicit assumed relaxation time.','Furnace/coolant/gas thermal parameters and transfer coefficients are uncalibrated engineering inputs.','Five mechanism joints (four distinct motion states) and two screen readouts are separately validated; no real robotic sample manipulation, clamp compliance, or deformable Matterix strip is claimed.'])
 spec=importlib.util.spec_from_file_location('_dma_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()});ann.annotate_export(SLUG,caps,slots,{'dma_conservation':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'gas.load':(GAS,GAS_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sensor.calibrate':(CALIBRATION,CALIBRATION_EXAMPLE)})
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'dma_conservation':mechanism})

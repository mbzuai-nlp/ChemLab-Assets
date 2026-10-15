"""CHEM128 finite-source tribology, exact path work and differential sensing."""
from pathlib import Path
import copy,json,math,importlib.util
import numpy as np
PROTOCOL_VERSION='1.0.0';SLUG='antonpaar_trb';CATALOG_ID='CHEM-128';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch04/t1_thermal'/SLUG
DT=.1;G=9.80665;OMEGA_MIN=.2*2*math.pi/60;OMEGA_MAX=2000*2*math.pi/60;TORQUE_MAX=.45;FRICTION_MAX=20.
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
 p=ASSET/'tribology_library.json';return json.loads(p.read_text()) if p.exists() else {'substances':{}}

def sine_variation(phase):
 """Exact integral0..phase |cos(u)|du for phase>=0, including arbitrary reversals."""
 n=math.floor(phase/math.pi);r=phase-n*math.pi;return 2*n+(math.sin(r) if r<=math.pi/2 else 2-math.sin(r))
def motion(drive,radius,t):
 mode=drive['mode']
 if mode=='rotary':return dict(angle_rad=drive['angular_speed_rad_s']*t,position_m=0.,signed_speed_m_s=radius*drive['angular_speed_rad_s'],distance_m=radius*drive['angular_speed_rad_s']*t)
 phase=2*math.pi*drive['frequency_hz']*t
 if mode=='linear':amplitude=drive['stroke_m']/2;return dict(angle_rad=0.,position_m=amplitude*math.sin(phase),signed_speed_m_s=amplitude*2*math.pi*drive['frequency_hz']*math.cos(phase),distance_m=amplitude*sine_variation(phase))
 amplitude=drive['angular_amplitude_rad'];return dict(angle_rad=amplitude*math.sin(phase),position_m=0.,signed_speed_m_s=radius*amplitude*2*math.pi*drive['frequency_hz']*math.cos(phase),distance_m=radius*amplitude*sine_variation(phase))

SCALAR_BASIS='scalar_mean_friction';SPEED_BASIS='scalar_speed_series_friction';SCALAR_BASES=(SCALAR_BASIS,SPEED_BASIS)
UNBOUNDED_DOMAIN_M=1e9
SCALAR_EVIDENCE='Published scalar friction coefficient of this material pair (mean as defined by the source: steady-state or test-duration average; speed-series values interpolated in log(speed) between source speeds) replayed as a CONSTANT mu over the source sliding-distance domain. No running-in transient, stick-slip, fluctuation or wear-driven drift is represented; this is not a measured friction curve. Source specific wear rates (if any) are reference data only; wear uses the declared Archard inputs.'
def scalar_curve(reference,speed_m_s=None,accept_unknown=True,accept_transfer=True):
 """Constant-mu reference from a published scalar; returns (x,mu,info)."""
 try:rs=reference['runtime_scalars']
 except (KeyError,TypeError):raise ActionRejected('INVALID_REFERENCE')
 if not reference.get('runtime_usable') or reference.get('runtime_basis') not in SCALAR_BASES or not isinstance(rs,dict):raise ActionRejected('INVALID_REFERENCE')
 domain=rs.get('sliding_distance_domain_m');info=dict(basis=reference['runtime_basis'],domain_known=domain is not None,speed_clamped=False)
 if domain is None:
  if not accept_unknown:raise ActionRejected('UNKNOWN_REFERENCE_CONDITIONS')
  domain=UNBOUNDED_DOMAIN_M
 if isinstance(domain,bool) or not isinstance(domain,(int,float)) or not math.isfinite(domain) or domain<=0:raise ActionRejected('INVALID_REFERENCE')
 if reference['runtime_basis']==SCALAR_BASIS:mu=rs.get('friction_coefficient')
 else:
  try:v=np.asarray(rs['speeds_m_s'],float);m=np.asarray(rs['friction_coefficients'],float)
  except (KeyError,TypeError,ValueError):raise ActionRejected('INVALID_REFERENCE')
  if v.ndim!=1 or v.shape!=m.shape or len(v)<2 or np.any(~np.isfinite(v)) or np.any(v<=0) or np.any(np.diff(v)<=0) or np.any(~np.isfinite(m)):raise ActionRejected('INVALID_REFERENCE')
  s=float(v[0]) if speed_m_s is None else float(speed_m_s)
  if s<=0:raise ActionRejected('REFERENCE_CONDITIONS')
  if not v[0]*(1-1e-9)<=s<=v[-1]*(1+1e-9):
   if not accept_transfer:raise ActionRejected('REFERENCE_CONDITIONS')
   info['speed_clamped']=True
  s=min(max(s,float(v[0])),float(v[-1]));mu=float(np.interp(math.log(s),np.log(v),m));info['speed_m_s']=s
 if isinstance(mu,bool) or not isinstance(mu,(int,float)) or not math.isfinite(mu) or not 0<=mu<=2:raise ActionRejected('INVALID_REFERENCE')
 info['friction_coefficient']=float(mu)
 return np.array([0.,float(domain)]),np.array([float(mu),float(mu)]),info
def curve(reference,accept_unknown=True):
 if isinstance(reference,dict) and reference.get('runtime_basis') in SCALAR_BASES:
  x,mu,_=scalar_curve(reference,None,accept_unknown,True);return x,mu
 try:x=np.asarray(reference['values']['sliding_distance_m'],float);mu=np.asarray(reference['values']['friction_coefficient'],float)
 except (KeyError,TypeError,ValueError):raise ActionRejected('INVALID_REFERENCE')
 if not reference.get('runtime_usable') or len(x)<2 or x.shape!=mu.shape or np.any(~np.isfinite(x)) or np.any(~np.isfinite(mu)) or np.any(np.diff(x)<=0) or x[0]<0 or np.any(mu<0):raise ActionRejected('INVALID_REFERENCE')
 return x,mu

def source_mu(reference,distance_m):
 x,mu=curve(reference)
 if distance_m<x[0]-1e-12 or distance_m>x[-1]+1e-12:raise ActionRejected('REFERENCE_DISTANCE_RANGE')
 return float(np.interp(distance_m,x,mu))
def source_integral(reference,start_m,end_m):
 x,mu=curve(reference)
 if start_m<x[0]-1e-12 or end_m>x[-1]+1e-12 or end_m<start_m:raise ActionRejected('REFERENCE_DISTANCE_RANGE')
 grid=np.r_[start_m,x[(x>start_m)&(x<end_m)],end_m];return float(np.trapz(np.interp(grid,x,mu),grid))

PLANT_EXAMPLE=dict(accept_engineering=True,ambient_k=296.15,relative_humidity=.5,humidity_offset=0.,thermal_capacity_j_k=100.,heat_loss_w_k=1.,heater_installed=False,heater_max_w=0.,heater_gain_w_k=10.,safe_access_temperature_k=313.15,temperature_max_k=373.15,stage_mass_kg=.2,rotational_inertia_kg_m2=.0001,linear_drive_limit_n=100.,normal_force_factor=1.,normal_read_gain=1.,normal_read_offset_n=0.,friction_gain_left=1.,friction_gain_right=1.,friction_offset_left_n=0.,friction_offset_right_n=0.,common_drift_n_s=0.,thermal_common_drift_n_k=0.,force_sensor_tau_s=.002,minimum_observable_load_n=.05,wear_sensor_enabled=False,wear_gain=1.,wear_offset_m=0.,temperature_sensor_offset_k=0.)
PLANT=obj({'accept_engineering':BOOL,'ambient_k':number('K',273.15,323.15),'relative_humidity':number('1',0,1),'humidity_offset':number('1',-.5,.5),'thermal_capacity_j_k':number('J/K',.01,1e6),'heat_loss_w_k':number('W/K',0,1000),'heater_installed':BOOL,'heater_max_w':number('W',0,10000),'heater_gain_w_k':number('W/K',.001,1e6),'safe_access_temperature_k':number('K',273.15,373.15),'temperature_max_k':number('K',273.15,723.15),'stage_mass_kg':number('kg',.001,100),'rotational_inertia_kg_m2':number('kg*m^2',1e-8,10),'linear_drive_limit_n':number('N',.001,1e5),'normal_force_factor':number('1',.1,2),'normal_read_gain':number('1',.1,2),'normal_read_offset_n':number('N',-5,5),'friction_gain_left':number('1',.01,100),'friction_gain_right':number('1',.01,100),'friction_offset_left_n':number('N',-20,20),'friction_offset_right_n':number('N',-20,20),'common_drift_n_s':number('N/s',-1,1),'thermal_common_drift_n_k':number('N/K',-1,1),'force_sensor_tau_s':number('s',1e-5,100),'minimum_observable_load_n':number('N',1e-6,1),'wear_sensor_enabled':BOOL,'wear_gain':number('1',.01,100),'wear_offset_m':number('m',-.002,.002),'temperature_sensor_offset_k':number('K',-20,20)})
SAMPLE_EXAMPLE=dict(reference_id='cr100_leadbronze_dry_003',volume_m3=1e-6,density_kg_m3=7800.,hardness_pa=2e9,archard_coefficient=1e-5,wear_track_area_m2=1e-4,accept_engineering=True,accept_unknown_conditions=True,accept_condition_transfer=True)
SAMPLE=obj({'reference_id':TEXT,'volume_m3':number('m^3',1e-15,.001),'density_kg_m3':number('kg/m^3',100,25000),'hardness_pa':number('Pa',1e3,1e12),'archard_coefficient':number('1',0,1),'wear_track_area_m2':number('m^2',1e-12,.01),'accept_engineering':BOOL,'accept_unknown_conditions':BOOL,'accept_condition_transfer':BOOL})
PIN_EXAMPLE=dict(volume_m3=1e-8,density_kg_m3=7800.,hardness_pa=4e9,archard_coefficient=1e-6,radius_m=.00476,accept_engineering=True)
PIN=obj({'volume_m3':number('m^3',1e-15,.0001),'density_kg_m3':number('kg/m^3',100,25000),'hardness_pa':number('Pa',1e3,1e12),'archard_coefficient':number('1',0,1),'radius_m':number('m',.0001,.01),'accept_engineering':BOOL})
LUBRICANT_EXAMPLE=dict(identity='engineering_oil',volume_m3=1e-6,density_kg_m3=850.,loss_per_distance_m2=1e-9,accept_engineering=True)
LUBRICANT=obj({'identity':TEXT,'volume_m3':number('m^3',1e-15,1e-4),'density_kg_m3':number('kg/m^3',100,3000),'loss_per_distance_m2':number('m^2',0,1e-3),'accept_engineering':BOOL})
DRIVE_EXAMPLE=dict(mode='rotary',angular_speed_rad_s=10.,frequency_hz=1.,stroke_m=.01,angular_amplitude_rad=math.pi/6)
DRIVE=obj({'mode':enum(['rotary','linear','angular']),'angular_speed_rad_s':number('rad/s',OMEGA_MIN,OMEGA_MAX),'frequency_hz':number('Hz',.01,10),'stroke_m':number('m',.00001,.06),'angular_amplitude_rad':number('rad',math.pi/36,5*math.pi/6)})
METHOD_EXAMPLE=dict(duration_s=20.,sample_interval_s=.1,stop_distance_m=100.,source_start_distance_m=0.,temperature_tolerance_k=1.)
METHOD=obj({'duration_s':number('s',.1,3600),'sample_interval_s':number('s',.1,60),'stop_distance_m':number('m',1e-9,1e6),'source_start_distance_m':number('m',0,1e9),'temperature_tolerance_k':number('K',.01,50)})
CALIBRATION=obj({'friction_gain_correction':number('1',.001,1000),'friction_zero_n':number('N',-20,20),'normal_gain_correction':number('1',.001,1000),'normal_zero_n':number('N',-5,5),'wear_zero_m':number('m',-.002,.002),'temperature_zero_k':number('K',-20,20)})
ROW=obj({'time_s':number('s'),'elapsed_s':number('s'),'sliding_distance_m':number('m',0),'signed_speed_m_s':number('m/s'),'disk_angle_rad':number('rad'),'linear_position_m':number('m'),'normal_force_n':nullable(number('N')),'friction_force_n':nullable(number('N')),'friction_coefficient':nullable(number('1')),'sensor_left_n':nullable(number('N')),'sensor_right_n':nullable(number('N')),'wear_depth_m':nullable(number('m')),'temperature_k':nullable(number('K')),'cumulative_observed_work_j':number('J'),'unavailable_reason':nullable(TEXT)})
RESULT=obj({'operation_id':nullable(TEXT),'phase':PHASE,'elapsed_s':number('s'),'rows':array(ROW,0,40000),'sliding_distance_m':number('m',0),'observed_work_j':number('J'),'reference_id':nullable(TEXT),'template_source':nullable(TEXT),'method_json':nullable(TEXT),'evidence':TEXT,'error_code':nullable(TEXT)})
CAPS={};SLOTS={}
def cap(k,i,o,c,z,kind='device_command',visibility='agent',example=None,effects=None):CAPS[k]=dict(input_schema=i,output_schema=o,component_ids=c,function_zh=z,kind=kind,visibility=visibility,example_arguments=example or {},effects=effects or [])
def slot(k,s,u,z,origin='derived',visibility='agent',component='controller'):SLOTS[k]=dict(schema=s,unit=u,meaning=z,origin=origin,visibility=visibility,component=component,write=None)
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller'],'显式工程热/惯量/差分观测参数','configuration','evaluator',PLANT_EXAMPLE)
for k,sc,ex,c in [('specimen',SAMPLE,SAMPLE_EXAMPLE,'specimen'),('pin',PIN,PIN_EXAMPLE,'pin'),('lubricant',LUBRICANT,LUBRICANT_EXAMPLE,'sample_holder')]:
 cap(k+'.load',sc,obj({'loaded':BOOL}),[c],'装入有限'+k,'configuration','evaluator',ex);cap(k+'.remove',obj({}),obj({'removed':BOOL}),[c],'停机抬臂后回收'+k,'configuration','evaluator')
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['controller','power_switch'],'供电；实际运动与热由宿主step推进',example={'enabled':True})
cap('normal_load.set',obj({'force_n':number('N',0,60)}),obj({'force_n':number('N',0,60)}),['deadweight','lever_arm'],'抬臂静止时更换死重；非主动载荷伺服',example={'force_n':5.})
cap('radius.set',obj({'radius_m':number('m',0,.04)}),obj({'radius_m':number('m',0,.04)}),['pin_holder'],'抬臂停机手动设轨道半径',example={'radius_m':.01})
cap('drive.configure',DRIVE,DRIVE,['disk','linear_stage'],'选择互斥旋转/线性/角往复工程波形','configuration',example=DRIVE_EXAMPLE)
cap('motion.home',obj({}),obj({'homing':BOOL}),['disk','linear_stage'],'抬臂后以工程低速真实回中，非瞬移')
cap('contact.set',obj({'engaged':BOOL}),obj({'engaged':BOOL}),['lever_arm','pin'],'停机手动抬起或放下测量臂',example={'engaged':True})
cap('thermal.set_temperature',obj({'temperature_k':number('K',273.15,723.15)}),obj({'temperature_k':number('K',273.15,723.15)}),['sample_holder'],'可选工程加热模块目标；未装不能加热',example={'temperature_k':298.15})
cap('sensor.calibrate',CALIBRATION,CALIBRATION,['friction_sensor_left','friction_sensor_right'],'外部标定常数，不能免费读取真值','configuration',example={'friction_gain_correction':1.,'friction_zero_n':0.,'normal_gain_correction':1.,'normal_zero_n':0.,'wear_zero_m':0.,'temperature_zero_k':0.})
cap('sensor.zero',obj({}),obj({'zeroed':BOOL}),['friction_sensor_left','friction_sensor_right'],'抬臂静止无接触的差分零点')
cap('method.configure',METHOD,METHOD,['controller'],'设置有限时间/距离和原曲线起点','configuration',example=METHOD_EXAMPLE)
cap('acquisition.start',obj({}),obj({'operation_id':TEXT}),['controller','disk','linear_stage','friction_sensor_left','friction_sensor_right'],'异步三模式试验；精确运动距离/模板摩擦功积分')
cap('acquisition.stop',obj({}),obj({'phase':enum(['cancelled'])}),['controller','disk','linear_stage'],'取消保留真实磨耗/碎屑/温度与部分曲线')
cap('acquisition.result',obj({}),RESULT,['controller'],'纯读冻结历史，不返回真实摩擦系数或库存','query')
cap('reference.query',obj({'reference_id':TEXT}),obj({'reference_json':TEXT,'template_source':TEXT,'evidence':TEXT}),['controller'],'原始成对材料曲线与来源工况','query',example={'reference_id':SAMPLE_EXAMPLE['reference_id']})
cap('lid.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['lid'],'停机透明安全罩',example={'opened':True})
cap('emergency.stop',obj({}),obj({'latched':BOOL}),['estop'],'本地工程安全锁存停止，非厂家认证急停；保留实际温度和磨耗')
cap('emergency.reset',obj({}),obj({'latched':BOOL}),['estop'],'停机后明确复位，不自动续行')
PROFILE=obj({'x_m':array(number('m'),3,10000),'height_m':array(number('m'),3,10000),'baseline_m':array(number('m'),3,10000),'geometry':enum(['linear','circular']),'track_length_m':number('m',1e-6,10),'radius_m':number('m',0,.04),'accept_uniform_profile':BOOL})
cap('analysis.wear_profile',PROFILE,obj({'loss_volume_m3':number('m^3',0),'pileup_volume_m3':number('m^3',0),'net_volume_m3':number('m^3'),'evidence':TEXT}),['controller'],'独立外部截面基线：损失、堆积、带符号净量分别积分','analysis',example={'x_m':[-.001,0.,.001],'height_m':[0.,-.0001,0.],'baseline_m':[0.,0.,0.],'geometry':'circular','track_length_m':.01,'radius_m':.01,'accept_uniform_profile':True})
HERTZ=obj({'normal_force_n':number('N',1e-6,60),'radius_m':number('m',1e-5,.1),'young_sample_pa':number('Pa',1e5,1e12),'poisson_sample':number('1',-.9,.49),'young_pin_pa':number('Pa',1e5,1e12),'poisson_pin':number('1',-.9,.49),'accept_elastic_homogeneous':BOOL})
cap('analysis.hertz',HERTZ,obj({'effective_modulus_pa':number('Pa'),'contact_radius_m':number('m'),'indentation_m':number('m'),'peak_pressure_pa':number('Pa'),'evidence':TEXT}),['controller'],'小接触、均匀弹性无黏附Hertz分析，不冒充涂层/塑性解','analysis',example={'normal_force_n':5.,'radius_m':.00476,'young_sample_pa':1e11,'poisson_sample':.3,'young_pin_pa':2e11,'poisson_pin':.3,'accept_elastic_homogeneous':True})
LEDGER=obj({k:number(u) for k,u in [('specimen_loaded_kg','kg'),('specimen_remaining_kg','kg'),('specimen_debris_kg','kg'),('specimen_recovered_kg','kg'),('specimen_debris_recovered_kg','kg'),('specimen_error_kg','kg'),('pin_loaded_kg','kg'),('pin_remaining_kg','kg'),('pin_debris_kg','kg'),('pin_recovered_kg','kg'),('pin_debris_recovered_kg','kg'),('pin_error_kg','kg'),('lubricant_loaded_kg','kg'),('lubricant_remaining_kg','kg'),('lubricant_dragout_kg','kg'),('lubricant_recovered_kg','kg'),('lubricant_error_kg','kg'),('friction_input_j','J'),('heater_input_j','J'),('ambient_loss_j','J'),('stored_thermal_energy_j','J'),('thermal_error_j','J'),('specimen_wear_volume_m3','m^3'),('pin_wear_volume_m3','m^3')]})
cap('debris.collect',obj({}),obj({'collected':BOOL}),['sample_holder'],'开罩抬臂后回收有限双方磨屑','configuration','evaluator')
cap('ledger.read',obj({}),LEDGER,['controller'],'双方试片/销→磨屑、润滑剂与摩擦功/热账本','query','evaluator')
cap('sim.step',obj({'dt_s':number('s',0,3600)}),obj({'time_s':number('s')}),['controller'],'唯一宿主时钟，固定0.1秒含转向/观测内部细分','simulation','evaluator',{'dt_s':.1})
UNSUPPORTED=['wear.atomic_morphology','friction.molecular_dynamics','hardware.connect','robot.sample_change','noise.empirical','accuracy.certification','humidity.control','vacuum.control','electrical_contact_resistance.measure','astm.certification']
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'未实现：'+k)
for k,z,c in [('plant.configured','显式工程参数已接受','controller'),('power.enabled','供电','controller'),('lid.open','透明罩开启','lid'),('emergency.latched','急停锁存','estop'),('contact.engaged','死重臂接触样片','lever_arm'),('specimen.present','有限试片存在','specimen'),('pin.present','有限销/球存在','pin'),('lubricant.present','润滑剂已加载','sample_holder'),('motion.homing','抬臂低速回中中','disk')]:slot(k,BOOL,'1',z,component=c)
for k,lo,hi,u,z,c in [('disk.angle_rad',-math.pi,math.pi,'rad','转盘实际工程姿态，显示取模不影响真实累计距离','disk'),('linear.position_m',-.03,.03,'m','独立线性附件实际位置，主旋转几何不声称此变体','linear_stage'),('radius.position_m',0.,.04,'m','手动轨道半径','radius_carriage'),('arm.angle_rad',0,.22,'rad','手动接触臂铰接0接触/.22抬起','lever_arm'),('lid.angle_rad',0,1.55,'rad','透明罩手动铰接','lid'),('power.angle_rad',0,.28,'rad','电源工程姿态','power_switch'),('estop.press_m',0,.003,'m','照片红按钮的本地工程安全锁存姿态，厂家功能未确证','estop')]:slot(k,number(u,lo,hi),u,z,'scene',component=c)
slot('drive.mode',enum(['rotary','linear','angular']),'1','互斥运动配置','setpoint',component='disk');slot('normal.setting_n',number('N',0,60),'N','人工死重名义载荷','setpoint',component='deadweight');slot('thermal.target_k',number('K',273.15,723.15),'K','可选工程加热目标','setpoint',component='sample_holder')
for k,u,z,c in [('normal.indicated_n','N','名义死重独立观测校准，不是主动伺服真值','deadweight'),('friction.indicated_n','N','双传感器差分校正摩擦力，反转/过载不可用','friction_sensor_left'),('friction.coefficient','1','观测摩擦力幅值除观测载荷；低载不可用','controller'),('friction.left_n','N','左侧力传感器工程读数','friction_sensor_left'),('friction.right_n','N','右侧力传感器工程读数','friction_sensor_right'),('wear.indicated_m','m','可选在线位移观测代理，非来源实测磨损','sample_holder'),('thermal.indicated_k','K','独立校准温度显示','sample_holder')]:slot(k,nullable(number(u)),u,z,component=c)
slot('motion.signed_speed_m_s',number('m/s'),'m/s','规定波形瞬时带符号滑动速度，反转时为零',component='disk');slot('motion.distance_m',number('m',0),'m','实际累计路径积分，不能由姿态差替代',component='disk');slot('friction.unavailable_reason',nullable(TEXT),'1','低载/反转/传感器量程不可用原因')
for k,u,z in [('truth.normal_force_n','N','接触实际法向死重力'),('truth.friction_coefficient','1','经验源曲线代理摩擦响应'),('truth.specimen_volume_m3','m^3','有限试片剩余体积'),('truth.pin_volume_m3','m^3','有限销剩余体积'),('truth.debris_kg','kg','磨屑质量'),('truth.lubricant_volume_m3','m^3','有限润滑剂剩余量'),('truth.temperature_k','K','集中热容实际温度'),('truth.friction_work_j','J','经验曲线沿真实滑程的摩擦耗散')]:slot(k,number(u,0),u,z,'process_truth','evaluator')
slot('clock.time_s',number('s',0),'s','宿主时钟');slot('acquisition.phase',PHASE,'1','异步终态');slot('acquisition.elapsed_s',number('s',0),'s','实际试验经过时间');slot('acquisition.points',integer(),'1','已采集曲线点')

def profile_analysis(a):
 x=np.array(a['x_m']);h=np.array(a['height_m']);base=np.array(a['baseline_m'])
 if not a['accept_uniform_profile']:raise ActionRejected('MODEL_NOT_ACCEPTED')
 if x.shape!=h.shape or x.shape!=base.shape or np.any(np.diff(x)<=0):raise ActionRejected('PROFILE_GRID')
 if a['geometry']=='circular' and np.any(a['radius_m']+x<=0):raise ActionRejected('PROFILE_RADIUS')
 loss=pile=0.
 for i in range(len(x)-1):
  x0,x1=x[i:i+2];d0,d1=(base-h)[i:i+2];cuts=[0.,1.]
  if d0*d1<0:cuts.insert(1,float(-d0/(d1-d0)))
  for t0,t1 in zip(cuts,cuts[1:]):
   # Exact product of linear deficit and linear ring circumference on each signed piece.
   dd=d1-d0;dx=x1-x0
   if a['geometry']=='linear':v=dx*a['track_length_m']*(d0*(t1-t0)+dd*(t1*t1-t0*t0)/2)
   else:
    r0=a['radius_m']+x0;v=2*math.pi*dx*(d0*r0*(t1-t0)+(d0*dx+dd*r0)*(t1*t1-t0*t0)/2+dd*dx*(t1**3-t0**3)/3)
   if v>=0:loss+=v
   else:pile-=v
 return dict(loss_volume_m3=float(loss),pileup_volume_m3=float(pile),net_volume_m3=float(loss-pile),evidence='External user-supplied profile inputs and explicit uniform-track assumption. Baseline must be independently supplied; deficit and pile-up are integrated separately with exact zero crossings. Circular volume retains radial2pi(r+x) weight. Net signed volume can be negative; no automatic claim of true material loss.')
def hertz_analysis(a):
 if not a['accept_elastic_homogeneous']:raise ActionRejected('MODEL_NOT_ACCEPTED')
 e=1/((1-a['poisson_sample']**2)/a['young_sample_pa']+(1-a['poisson_pin']**2)/a['young_pin_pa']);r=(3*a['normal_force_n']*a['radius_m']/(4*e))**(1/3)
 if r/a['radius_m']>.1:raise ActionRejected('HERTZ_MODEL_DOMAIN')
 return dict(effective_modulus_pa=e,contact_radius_m=r,indentation_m=r*r/a['radius_m'],peak_pressure_pa=3*a['normal_force_n']/(2*math.pi*r*r),evidence='Homogeneous nonadhesive elastic sphere-flat Hertz approximation; not a layered coating, plasticity or roughness solution.')

class AntonpaarTrbRuntime:
 def __init__(self,instance_id='trb_01'):
  self.instance_id=instance_id;self.plant=None;self.specimen=None;self.pin=None;self.lubricant=None;self.reference=None;self.ref_x=np.array([]);self.ref_mu=np.array([]);self.specimen_volume=0.;self.pin_volume=0.;self.lubricant_volume=0.;self.specimen_loaded=0.;self.pin_loaded=0.;self.lubricant_loaded=0.;self.specimen_recovered=0.;self.pin_recovered=0.;self.lubricant_recovered=0.;self.specimen_debris=0.;self.pin_debris=0.;self.specimen_debris_recovered=0.;self.pin_debris_recovered=0.;self.lubricant_dragout=0.;self.specimen_wear=0.;self.pin_wear=0.;self.sample_distance=0.;self.source_origin=None;self.power=False;self.lid_open=True;self.contact=False;self.emergency=False;self.homing=False;self.radius=.01;self.normal_setting=0.;self.disk_angle=0.;self.linear_position=0.;self.signed_speed=0.;self.temperature=296.15;self.initial_temperature=296.15;self.temperature_target=296.15;self.heater_energy=0.;self.friction_energy=0.;self.ambient_loss=0.;self.left=0.;self.right=0.;self.calibration=copy.deepcopy(CAPS['sensor.calibrate']['example_arguments']);self.drive=copy.deepcopy(DRIVE_EXAMPLE);self.method=copy.deepcopy(METHOD_EXAMPLE);self.phase='idle';self.elapsed=0.;self.rows=[];self.observed_work=0.;self.operation_distance=0.;self.operation_start=0.;self.operation_id=None;self.angle_base=0.;self.source=None;self.result_reference=None;self.operation_method=None;self.result_evidence=None;self.error=None;self._counter=0;self.records={};self.time_s=0.;self._ticks=0;self._remainder=0.;self.revision=0;self._requests={};self._cancel_operations={};self._owners={};self._principals={'agent':'agent','evaluator':'evaluator'};self.scalar_info=None
 def register_principal(self,principal_id,visibility='agent'):
  if not isinstance(principal_id,str) or not principal_id or principal_id in self._principals or visibility not in ('agent','debug','evaluator'):raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='agent'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return AntonpaarTrbSession(self,principal_id)
 def describe(self):return export_antonpaar_trb_contract_metadata()
 def _normal_true(self):return self.normal_setting*self.plant['normal_force_factor'] if self.contact and self.plant else 0.
 def _normal_observed(self):
  if not self.power or not self.contact:return None
  return (self.normal_setting*self.plant['normal_read_gain']+self.plant['normal_read_offset_n']-self.calibration['normal_zero_n'])/self.calibration['normal_gain_correction']
 def _coordinate(self):return (self.source_origin if self.source_origin is not None else self.ref_x[0] if len(self.ref_x) else 0.)+self.sample_distance
 def _mu(self):return float(np.interp(self._coordinate(),self.ref_x,self.ref_mu)) if len(self.ref_x) else 0.
 def _sensing(self):
  if not self.power:return None,None,'POWER_OFF'
  if not self.contact or self.phase!='running':return None,None,'NO_SLIDING_CONTACT'
  f=(self.left-self.right-self.calibration['friction_zero_n'])/self.calibration['friction_gain_correction'];n=self._normal_observed()
  if abs(f)>FRICTION_MAX:return None,None,'FRICTION_SENSOR_OVER_RANGE'
  if abs(self.signed_speed)<1e-10:return None,None,'REVERSAL_ZERO_SPEED'
  if n is None or n<self.plant['minimum_observable_load_n']:return f,None,'LOW_LOAD'
  return f,abs(f)/n,None
 def _wear_read(self):
  if not self.plant or not self.plant['wear_sensor_enabled'] or not self.specimen or not self.power:return None
  value=self.specimen_wear/self.specimen['wear_track_area_m2']*self.plant['wear_gain']+self.plant['wear_offset_m']-self.calibration['wear_zero_m'];return value if -.002<=value<=.002 else None
 def read(self,role='evaluator'):
  f,mu,reason=self._sensing();d={'plant.configured':self.plant is not None,'power.enabled':self.power,'lid.open':self.lid_open,'emergency.latched':self.emergency,'contact.engaged':self.contact,'specimen.present':self.specimen is not None,'pin.present':self.pin is not None,'lubricant.present':self.lubricant is not None,'motion.homing':self.homing,'disk.angle_rad':self.disk_angle,'linear.position_m':self.linear_position,'radius.position_m':self.radius,'arm.angle_rad':0. if self.contact else .22,'lid.angle_rad':1.55 if self.lid_open else 0.,'power.angle_rad':.28 if self.power else 0.,'estop.press_m':.003 if self.emergency else 0.,'drive.mode':self.drive['mode'],'normal.setting_n':self.normal_setting,'thermal.target_k':self.temperature_target,'normal.indicated_n':self._normal_observed(),'friction.indicated_n':f,'friction.coefficient':mu,'friction.left_n':self.left if self.power else None,'friction.right_n':self.right if self.power else None,'wear.indicated_m':self._wear_read(),'thermal.indicated_k':self.temperature+self.plant['temperature_sensor_offset_k']-self.calibration['temperature_zero_k'] if self.power else None,'environment.relative_humidity':max(0.,min(1.,self.plant['relative_humidity']+self.plant['humidity_offset'])) if self.power else None,'motion.signed_speed_m_s':self.signed_speed,'motion.distance_m':self.operation_distance,'friction.unavailable_reason':reason,'truth.normal_force_n':self._normal_true(),'truth.friction_coefficient':self._mu(),'truth.specimen_volume_m3':self.specimen_volume,'truth.pin_volume_m3':self.pin_volume,'truth.debris_kg':self.specimen_debris+self.pin_debris,'truth.lubricant_volume_m3':self.lubricant_volume,'truth.temperature_k':self.temperature,'truth.friction_work_j':self.friction_energy,'clock.time_s':self.time_s,'acquisition.phase':self.phase,'acquisition.elapsed_s':self.elapsed,'acquisition.points':len(self.rows)}
  return copy.deepcopy({k:v for k,v in d.items() if role in ('evaluator','debug') or SLOTS[k]['visibility']=='agent'})
 def _ledger(self):
  s=self.specimen_volume*self.specimen['density_kg_m3'] if self.specimen else 0.;p=self.pin_volume*self.pin['density_kg_m3'] if self.pin else 0.;l=self.lubricant_volume*self.lubricant['density_kg_m3'] if self.lubricant else 0.;stored=self.plant['thermal_capacity_j_k']*(self.temperature-self.initial_temperature) if self.plant else 0.
  return dict(specimen_loaded_kg=self.specimen_loaded,specimen_remaining_kg=s,specimen_debris_kg=self.specimen_debris,specimen_recovered_kg=self.specimen_recovered,specimen_debris_recovered_kg=self.specimen_debris_recovered,specimen_error_kg=self.specimen_loaded-s-self.specimen_debris-self.specimen_recovered-self.specimen_debris_recovered,pin_loaded_kg=self.pin_loaded,pin_remaining_kg=p,pin_debris_kg=self.pin_debris,pin_recovered_kg=self.pin_recovered,pin_debris_recovered_kg=self.pin_debris_recovered,pin_error_kg=self.pin_loaded-p-self.pin_debris-self.pin_recovered-self.pin_debris_recovered,lubricant_loaded_kg=self.lubricant_loaded,lubricant_remaining_kg=l,lubricant_dragout_kg=self.lubricant_dragout,lubricant_recovered_kg=self.lubricant_recovered,lubricant_error_kg=self.lubricant_loaded-l-self.lubricant_dragout-self.lubricant_recovered,friction_input_j=self.friction_energy,heater_input_j=self.heater_energy,ambient_loss_j=self.ambient_loss,stored_thermal_energy_j=stored,thermal_error_j=self.friction_energy+self.heater_energy-self.ambient_loss-stored,specimen_wear_volume_m3=self.specimen_wear,pin_wear_volume_m3=self.pin_wear)
 def _result(self):
  if self.phase!='running' and self.operation_id in self.records:return copy.deepcopy(self.records[self.operation_id])
  return dict(operation_id=self.operation_id,phase=self.phase,elapsed_s=self.elapsed,rows=copy.deepcopy(self.rows),sliding_distance_m=self.operation_distance,observed_work_j=self.observed_work,reference_id=self.result_reference,template_source=self.source,method_json=self.operation_method,evidence=self.result_evidence or 'No operation; no hardware metrology validated.',error_code=self.error)
 def invoke(self,capability,**arguments):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability]['input_schema'],arguments)
  if capability=='sim.step':self.step(arguments['dt_s']);return {'time_s':self.time_s}
  before=copy.deepcopy(self.__dict__)
  try:
   result=self._invoke(capability,arguments);validate(CAPS[capability]['output_schema'],result)
   if CAPS[capability]['kind'] not in ('query','analysis'):self.revision+=1
   return copy.deepcopy(result)
  except Exception:self.__dict__.clear();self.__dict__.update(before);raise
 def _stopped(self):
  if self.phase=='running' or self.homing:raise ActionRejected('BUSY')
 def _access(self):
  self._stopped()
  if self.contact:raise ActionRejected('ARM_LOADED')
  if not self.lid_open:raise ActionRejected('LID_CLOSED')
  if self.plant and self.temperature>self.plant['safe_access_temperature_k']:raise ActionRejected('HOT_SPECIMEN')
 def _check_drive(self,a):
  if a['mode']=='linear' and math.pi*a['stroke_m']*a['frequency_hz']>.370+1e-12:raise ActionRejected('LINEAR_SPEED_RANGE')
  if a['mode']=='angular' and (a['frequency_hz']>7 or not OMEGA_MIN<=2*math.pi*a['frequency_hz']*a['angular_amplitude_rad']<=OMEGA_MAX):raise ActionRejected('ANGULAR_COMBINATION_RANGE')
  if self.plant:
   if a['mode']=='linear' and self.plant['stage_mass_kg']*(2*math.pi*a['frequency_hz'])**2*a['stroke_m']/2>=self.plant['linear_drive_limit_n']:raise ActionRejected('DRIVE_INERTIA_RANGE')
   if a['mode']=='angular' and self.plant['rotational_inertia_kg_m2']*(2*math.pi*a['frequency_hz'])**2*a['angular_amplitude_rad']>=TORQUE_MAX:raise ActionRejected('DRIVE_INERTIA_RANGE')
 def _mu_limit(self):
  fn=self._normal_true()
  if fn<=0:return math.inf
  limit=FRICTION_MAX/fn
  if self.drive['mode']=='linear':limit=min(limit,(self.plant['linear_drive_limit_n']-self.plant['stage_mass_kg']*(2*math.pi*self.drive['frequency_hz'])**2*self.drive['stroke_m']/2)/fn)
  else:
   inertia=self.plant['rotational_inertia_kg_m2']*(2*math.pi*self.drive['frequency_hz'])**2*self.drive['angular_amplitude_rad'] if self.drive['mode']=='angular' else 0.;limit=min(limit,(TORQUE_MAX-inertia)/(fn*self.radius)) if self.radius>0 else limit
  return limit
 def _speed(self):return self.radius*self.drive['angular_speed_rad_s'] if self.drive['mode']=='rotary' else abs(motion(self.drive,self.radius,0.)['signed_speed_m_s'])
 def _check_conditions(self):
  s=self.specimen;c=self.reference.get('conditions',{});speed=self._speed();pairs=[('normal_force_n',self._normal_true()),('sliding_speed_m_s',speed),('temperature_k',self.temperature),('relative_humidity',self.plant['relative_humidity']),('track_radius_m',self.radius),('counterbody_radius_m',self.pin['radius_m'])]
  if self.reference.get('runtime_basis')==SPEED_BASIS:pairs=[x for x in pairs if x[0]!='sliding_speed_m_s']
  if any(c.get(k) is None for k,_ in pairs) and not s['accept_unknown_conditions']:raise ActionRejected('UNKNOWN_REFERENCE_CONDITIONS')
  for k,v in pairs:
   if c.get(k) is not None and abs(float(c[k])-v)>(self.method['temperature_tolerance_k'] if k=='temperature_k' else max(1e-10,abs(v)*1e-6)) and not s['accept_condition_transfer']:raise ActionRejected('REFERENCE_CONDITIONS')
  if c.get('motion_mode') is not None and c['motion_mode']!=self.drive['mode'] and not s['accept_condition_transfer']:raise ActionRejected('REFERENCE_CONDITIONS')
  if self.lubricant and not s['accept_condition_transfer']:raise ActionRejected('REFERENCE_LUBRICATION')
 def _invoke(self,k,a):
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  if k=='acquisition.result':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='analysis.wear_profile':return profile_analysis(a)
  if k=='analysis.hertz':return hertz_analysis(a)
  if k=='reference.query':
   rec=library()['substances'].get(a['reference_id'])
   if rec is None:raise ActionRejected('INVALID_REFERENCE')
   return dict(reference_json=json.dumps(rec,ensure_ascii=False),template_source=json.dumps(rec.get('source'),ensure_ascii=False),evidence=json.dumps(rec.get('evidence'),ensure_ascii=False))
  if k=='plant.configure':
   if self.plant or self.time_s:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   if a['temperature_max_k']<a['ambient_k'] or not a['heater_installed'] and a['heater_max_w']>0:raise ActionRejected('THERMAL_CONFIGURATION')
   self.plant=copy.deepcopy(a);self.temperature=self.initial_temperature=self.temperature_target=a['ambient_k'];return {'configured':True}
  if k=='power.set':
   self._stopped()
   if a['enabled'] and (not self.plant or self.emergency):raise ActionRejected('NOT_READY')
   self.power=a['enabled'];return a
  if k=='lid.set':self._stopped();self.lid_open=a['opened'];return a
  if k=='emergency.stop':
   self.emergency=True;self.homing=False
   if self.phase=='running':self._finish('failed','EMERGENCY_STOP')
   self.signed_speed=0.;return {'latched':True}
  if k=='emergency.reset':self._stopped();self.emergency=False;return {'latched':False}
  if k=='normal_load.set':
   self._access()
   if not self.plant:raise ActionRejected('NOT_READY')
   if a['force_n']*self.plant['normal_force_factor']>60:raise ActionRejected('NORMAL_FORCE_RANGE')
   self.normal_setting=a['force_n'];return a
  if k=='radius.set':self._access();self.radius=a['radius_m'];return a
  if k=='drive.configure':self._access();self._check_drive(a);self.drive=copy.deepcopy(a);return a
  if k=='motion.home':
   self._access()
   if not self.power or self.emergency:raise ActionRejected('NOT_READY')
   self.homing=True;return {'homing':True}
  if k=='contact.set':
   self._stopped()
   if not self.lid_open:raise ActionRejected('LID_CLOSED')
   if a['engaged'] and (not self.specimen or not self.pin):raise ActionRejected('NO_CONTACT_PAIR')
   self.contact=a['engaged'];return a
  if k in ('specimen.load','pin.load','lubricant.load'):
   self._access()
   if not self.plant:raise ActionRejected('NOT_READY')
   if not a['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   name=k.split('.')[0]
   if getattr(self,name) is not None:raise ActionRejected('ALREADY_LOADED')
   if name=='specimen':
    rec=copy.deepcopy(library()['substances'].get(a['reference_id'],{}));x,mu=curve(rec,a['accept_unknown_conditions']);self.reference=rec;self.ref_x=x;self.ref_mu=mu;self.specimen_wear=0.;self.sample_distance=0.;self.source_origin=None
   if name=='pin':self.pin_wear=0.
   if name=='lubricant' and (self.temperature>423.15 or self.temperature_target>423.15):raise ActionRejected('LIQUID_TEMPERATURE_RANGE')
   setattr(self,name,copy.deepcopy(a));setattr(self,name+'_volume',a['volume_m3']);setattr(self,name+'_loaded',getattr(self,name+'_loaded')+a['volume_m3']*a['density_kg_m3']);return {'loaded':True}
  if k in ('specimen.remove','pin.remove','lubricant.remove'):
   self._access();name=k.split('.')[0];item=getattr(self,name)
   if item is None:raise ActionRejected('NOT_LOADED')
   setattr(self,name+'_recovered',getattr(self,name+'_recovered')+getattr(self,name+'_volume')*item['density_kg_m3']);setattr(self,name+'_volume',0.);setattr(self,name,None)
   if name=='specimen':self.ref_x=np.array([]);self.ref_mu=np.array([]);self.reference=None;self.source_origin=None;self.sample_distance=0.;self.specimen_wear=0.
   if name=='pin':self.pin_wear=0.
   return {'removed':True}
  if k=='debris.collect':
   self._access();self.specimen_debris_recovered+=self.specimen_debris;self.pin_debris_recovered+=self.pin_debris;self.specimen_debris=0.;self.pin_debris=0.;return {'collected':True}
  if k=='thermal.set_temperature':
   self._stopped()
   if not self.plant:raise ActionRejected('NOT_READY')
   t=a['temperature_k']
   if t<self.plant['ambient_k']:raise ActionRejected('NO_ACTIVE_COOLING')
   if t>self.plant['ambient_k'] and not self.plant['heater_installed']:raise ActionRejected('HEATER_NOT_INSTALLED')
   if t>self.plant['temperature_max_k']:raise ActionRejected('THERMAL_MODEL_RANGE')
   if self.lubricant and t>423.15:raise ActionRejected('LIQUID_TEMPERATURE_RANGE')
   self.temperature_target=t;return a
  if k=='sensor.calibrate':self._stopped();self.calibration=copy.deepcopy(a);return a
  if k=='sensor.zero':
   self._access()
   if not self.power:raise ActionRejected('NOT_READY')
   self.calibration['friction_zero_n']=self.left-self.right;return {'zeroed':True}
  if k=='method.configure':
   self._stopped()
   if abs(a['sample_interval_s']/DT-round(a['sample_interval_s']/DT))>1e-8:raise ActionRejected('SAMPLING_GRID')
   if self.source_origin is not None and a['source_start_distance_m']!=self.source_origin:raise ActionRejected('SOURCE_ORIGIN_LOCKED')
   self.method=copy.deepcopy(a);return a
  if k=='acquisition.start':
   self._stopped()
   if not self.power or self.emergency or self.lid_open or not self.contact or not self.specimen or not self.pin:raise ActionRejected('NOT_READY')
   if self.specimen_volume<=0 or self.pin_volume<=0:raise ActionRejected('SPECIMEN_EXHAUSTED')
   if self.drive['mode']=='linear' and abs(self.linear_position)>1e-12:raise ActionRejected('HOME_REQUIRED')
   if self.drive['mode']!='linear' and self.radius<=0:raise ActionRejected('ZERO_TRACK_RADIUS')
   self._check_drive(self.drive)
   if self.reference.get('runtime_basis') in SCALAR_BASES:self.ref_x,self.ref_mu,self.scalar_info=scalar_curve(self.reference,self._speed(),self.specimen['accept_unknown_conditions'],self.specimen['accept_condition_transfer'])
   self._check_conditions();coordinate=self.method['source_start_distance_m']+self.sample_distance
   if not self.ref_x[0]<=coordinate<self.ref_x[-1]:raise ActionRejected('REFERENCE_DISTANCE_RANGE')
   if float(np.interp(coordinate,self.ref_x,self.ref_mu))>self._mu_limit():raise ActionRejected('DRIVE_FORCE_RANGE')
   if abs(self.temperature-self.temperature_target)>self.method['temperature_tolerance_k']:raise ActionRejected('TEMPERATURE_NOT_READY')
   self.source_origin=self.method['source_start_distance_m'];self._counter+=1;self.operation_id=self.instance_id+':'+str(self._counter);self.phase='running';self.elapsed=0.;self.rows=[];self.observed_work=0.;self.operation_distance=0.;self.operation_start=self.time_s;self.angle_base=self.disk_angle;self.error=None;self.result_reference=self.specimen['reference_id'];self.source=json.dumps(self.reference.get('source'),ensure_ascii=False);self.operation_method=json.dumps({'method':self.method,'drive':self.drive,'radius_m':self.radius,'nominal_deadweight_force_n':self.normal_setting},sort_keys=True);self.result_evidence=(SCALAR_EVIDENCE+' Scalar replay: '+json.dumps(self.scalar_info,sort_keys=True)+'. ' if self.reference.get('runtime_basis') in SCALAR_BASES else 'Original positive empirical mu(distance) response transplanted under explicitly accepted conditions; source CSV preserved and source-node subsampling is not native resolution. ')+'Deadweight not servo; prescribed waveform not validated motor/contact dynamics. Wear uses explicit Archard inputs; differential sensors, heating/cooling and online wear proxy are engineering assumptions, not measured accuracy/noise. True private coefficients and inventories remain evaluator-only. Sources: '+json.dumps(self.reference.get('evidence'),ensure_ascii=False);self._record();return {'operation_id':self.operation_id}
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self._finish('cancelled');return {'phase':'cancelled'}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _heat(self,dt,friction_j=0.):
  if not self.plant or dt<=0:return
  p=self.plant;c=p['thermal_capacity_j_k'];g=p['heat_loss_w_k'];ambient=p['ambient_k'];q=friction_j/dt;gain=p['heater_gain_w_k'] if self.power and not self.emergency and p['heater_installed'] else 0.;maximum=p['heater_max_w'] if gain else 0.;target=self.temperature_target;remaining=dt;loops=0
  while remaining>1e-14:
   loops+=1
   if loops>10:raise RuntimeError('thermal event partition did not converge')
   t0=self.temperature;rate0=q-g*(t0-ambient);low=target-maximum/gain if gain else -math.inf
   if gain and maximum>0 and (t0<low-1e-10 or abs(t0-low)<=1e-10 and rate0+maximum<=0):a=g;b=q+maximum+g*ambient;bounds=[low]
   elif gain and maximum>0 and (t0<target-1e-10 or abs(t0-target)<=1e-10 and rate0<0):a=g+gain;b=q+gain*target+g*ambient;bounds=[low,target]
   else:a=g;b=q+g*ambient;bounds=[target] if gain and maximum>0 else []
   rate=(b-a*t0)/c
   if abs(rate)<1e-14:integral=t0*remaining;t1=t0;duration=remaining
   else:
    equilibrium=b/a if a else 0.;predict=equilibrium+(t0-equilibrium)*math.exp(-a*remaining/c) if a else t0+b*remaining/c;duration=remaining
    for boundary in bounds:
     if (boundary-t0)*rate>1e-10 and min(t0,predict)<boundary<max(t0,predict):
      ratio=(boundary-equilibrium)/(t0-equilibrium) if a else 0.;cross=-c/a*math.log(ratio) if a else (boundary-t0)*c/b
      if 0<cross<duration:duration=cross
    if a:
     decay=math.exp(-a*duration/c);t1=equilibrium+(t0-equilibrium)*decay;integral=equilibrium*duration+(t0-equilibrium)*c/a*(-math.expm1(-a*duration/c))
    else:t1=t0+b*duration/c;integral=(t0+t1)*duration/2
   loss=g*(integral-ambient*duration);heat=c*(t1-t0)-q*duration+loss
   if gain==0 or maximum==0:heat=0.;loss=q*duration-c*(t1-t0)
   self.ambient_loss+=loss;self.heater_energy+=heat;self.temperature=t1;remaining-=duration
 def _reference_work(self,ds):
  start=self._coordinate();end=start+ds;lo=int(np.searchsorted(self.ref_x,start,side='right'));hi=int(np.searchsorted(self.ref_x,end,side='left'));x=np.r_[start,self.ref_x[lo:hi],end];return float(np.trapz(np.interp(x,self.ref_x,self.ref_mu),x))*self._normal_true()
 def _allowed_distance(self,ds):
  limit=ds;reason=None
  candidates=[(self.method['stop_distance_m']-self.operation_distance,'STOP_DISTANCE'),(self.ref_x[-1]-self._coordinate(),'REFERENCE_DISTANCE_RANGE')];fn=self._normal_true()
  if fn>0:
   for item,volume,label in [(self.specimen,self.specimen_volume,'SPECIMEN_EXHAUSTED'),(self.pin,self.pin_volume,'PIN_EXHAUSTED')]:
    if item['archard_coefficient']>0:candidates.append((volume*item['hardness_pa']/(item['archard_coefficient']*fn),label))
  if self.lubricant and self.lubricant['loss_per_distance_m2']>0:candidates.append((self.lubricant_volume/self.lubricant['loss_per_distance_m2'],'LUBRICANT_EXHAUSTED'))
  for distance,label in candidates:
   if distance<=limit:limit=max(0.,distance);reason=label
  start=self._coordinate();end=start+limit;lo=int(np.searchsorted(self.ref_x,start,side='right'));hi=int(np.searchsorted(self.ref_x,end,side='left'));x=np.r_[start,self.ref_x[lo:hi],end];mu=np.interp(x,self.ref_x,self.ref_mu);bound=self._mu_limit()
  for i in range(len(x)-1):
   if mu[i]>bound:limit=max(0.,x[i]-start);reason='DRIVE_FORCE_RANGE';break
   if mu[i+1]>bound:
    crossing=x[i]+(x[i+1]-x[i])*(bound-mu[i])/(mu[i+1]-mu[i]);limit=max(0.,crossing-start);reason='DRIVE_FORCE_RANGE';break
  return limit,reason
 def _sensors(self,dt,mean_force,ds_signed):
  p=self.plant;common=p['common_drift_n_s']*self.time_s+p['thermal_common_drift_n_k']*(self.temperature-p['ambient_k']);target1=.5*mean_force*p['friction_gain_left']+p['friction_offset_left_n']+common;target2=-.5*mean_force*p['friction_gain_right']+p['friction_offset_right_n']+common;tau=p['force_sensor_tau_s'];one=-math.expm1(-dt/tau)
  int1=target1*dt+(self.left-target1)*tau*one;int2=target2*dt+(self.right-target2)*tau*one;self.left+=one*(target1-self.left);self.right+=one*(target2-self.right)
  if dt>0:self.observed_work+=(int1-int2-self.calibration['friction_zero_n']*dt)/self.calibration['friction_gain_correction']*ds_signed/dt
 def _advance_motion(self,elapsed_end):
  elapsed_start=self.time_s-self.operation_start;begin=motion(self.drive,self.radius,elapsed_start);end=motion(self.drive,self.radius,elapsed_end);requested=max(0.,end['distance_m']-begin['distance_m']);ds,reason=self._allowed_distance(requested)
  if ds<requested:
   lo=elapsed_start;hi=elapsed_end
   for _ in range(55):
    mid=(lo+hi)/2
    if motion(self.drive,self.radius,mid)['distance_m']-begin['distance_m']<ds:lo=mid
    else:hi=mid
   elapsed_end=(lo+hi)/2;end=motion(self.drive,self.radius,elapsed_end)
  dt=max(0.,elapsed_end-elapsed_start)
  if dt>1e-14:
   energy=self._reference_work(ds);fn=self._normal_true();signed=math.copysign(ds,motion(self.drive,self.radius,(elapsed_start+elapsed_end)/2)['signed_speed_m_s']);mean_force=energy/ds*math.copysign(1.,signed) if ds>0 else 0.;self.friction_energy+=energy;self._heat(dt,energy)
   for name in ['specimen','pin']:
    item=getattr(self,name);old=getattr(self,name+'_volume');wear=min(old,item['archard_coefficient']*fn*ds/item['hardness_pa']);setattr(self,name+'_volume',old-wear);setattr(self,name+'_wear',getattr(self,name+'_wear')+wear);setattr(self,name+'_debris',getattr(self,name+'_debris')+wear*item['density_kg_m3'])
   if self.lubricant:
    loss=min(self.lubricant_volume,self.lubricant['loss_per_distance_m2']*ds);self.lubricant_volume-=loss;self.lubricant_dragout+=loss*self.lubricant['density_kg_m3']
   self.sample_distance+=ds;self.operation_distance+=ds;self.time_s=self.operation_start+elapsed_end;self.elapsed=elapsed_end;self.disk_angle=(self.angle_base+end['angle_rad']+math.pi)%(2*math.pi)-math.pi;self.linear_position=end['position_m'];self.signed_speed=end['signed_speed_m_s'];self._sensors(dt,mean_force,signed)
  if self.temperature>self.plant['temperature_max_k']:reason='THERMAL_MODEL_RANGE'
  if self.lubricant and self.temperature>423.15:reason='LIQUID_TEMPERATURE_RANGE'
  if self._sensing()[2]=='FRICTION_SENSOR_OVER_RANGE':reason='FRICTION_SENSOR_OVER_RANGE'
  if not self.specimen['accept_condition_transfer']:
   try:self._check_conditions()
   except ActionRejected as exc:reason=str(exc)
  if reason:self._finish('succeeded' if reason=='STOP_DISTANCE' else 'failed',None if reason=='STOP_DISTANCE' else reason)
 def _record(self):
  if self.rows and abs(self.rows[-1]['time_s']-self.time_s)<=1e-12:return
  f,mu,reason=self._sensing();self.rows.append(dict(time_s=self.time_s,elapsed_s=self.time_s-self.operation_start,sliding_distance_m=self.operation_distance,signed_speed_m_s=self.signed_speed,disk_angle_rad=self.disk_angle,linear_position_m=self.linear_position,normal_force_n=self._normal_observed(),friction_force_n=f,friction_coefficient=mu,sensor_left_n=self.left if self.power else None,sensor_right_n=self.right if self.power else None,wear_depth_m=self._wear_read(),temperature_k=self.temperature+self.plant['temperature_sensor_offset_k']-self.calibration['temperature_zero_k'] if self.power else None,cumulative_observed_work_j=self.observed_work,unavailable_reason=reason))
 def _finish(self,phase,error=None):
  self.elapsed=self.time_s-self.operation_start;self._record();self.phase=phase;self.error=error;self.signed_speed=0.
  if self.operation_id:self.records[self.operation_id]=self._result()
 def _tick(self):
  start=(self._ticks-1)*DT;end=self._ticks*DT;self.time_s=start
  while self.time_s<end-1e-12:
   if self.phase=='running':
    phase_time=self.time_s-self.operation_start;interval=.01
    if self.drive['mode']!='rotary':
     f=self.drive['frequency_hz'];interval=min(interval,1/(64*f));next_turn=(math.floor((phase_time*f-.25)/.5+1e-10)+1)*.5/f+.25/f
     if next_turn>phase_time+1e-12:interval=min(interval,next_turn-phase_time)
    endpoint=min(end-self.operation_start,phase_time+interval,self.method['duration_s']);before=self.time_s;self._advance_motion(endpoint)
    if self.phase=='running' and self.elapsed>=self.method['duration_s']-1e-10:self._finish('succeeded')
    if self.phase=='running' and self.time_s<=before+1e-14:raise RuntimeError('motion integration made no progress')
   else:
    dt=end-self.time_s;self._heat(dt)
    if self.plant and self.power:self._sensors(dt,0.,0.)
    if self.power and not self.emergency and self.homing:
     self.linear_position+=max(-.03*dt,min(.03*dt,-self.linear_position));self.disk_angle+=max(-1.*dt,min(1.*dt,-self.disk_angle));self.homing=abs(self.linear_position)>1e-12 or abs(self.disk_angle)>1e-12
    self.time_s=end
  self.time_s=end
  if self.phase=='running' and (self._ticks-round(self.operation_start/DT))%round(self.method['sample_interval_s']/DT)==0:self._record()
 def step(self,dt_s):
  validate(number('s',0,3600),dt_s);total=self._remainder+dt_s;n=int((total+1e-12)/DT);self._remainder=total-n*DT
  for _ in range(n):self._ticks+=1;self._tick()
  self.revision+=n

class AntonpaarTrbSession:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,instance_id):
  if instance_id!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  meta=export_antonpaar_trb_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.1.0',capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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

slot('environment.relative_humidity',nullable(number('1',0,1)),'1','显式环境RH与校准偏置后的限幅读数；不声称湿度控制附件已实现',component='controller')
EFFECTS={
 'plant.configure':['plant.configured','truth.temperature_k','thermal.target_k'],
 'specimen.load':['specimen.present','truth.specimen_volume_m3','truth.friction_coefficient','wear.indicated_m'],
 'specimen.remove':['specimen.present','truth.specimen_volume_m3','truth.friction_coefficient','wear.indicated_m'],
 'pin.load':['pin.present','truth.pin_volume_m3'],'pin.remove':['pin.present','truth.pin_volume_m3'],
 'lubricant.load':['lubricant.present','truth.lubricant_volume_m3'],'lubricant.remove':['lubricant.present','truth.lubricant_volume_m3'],
 'debris.collect':['truth.debris_kg'],
 'power.set':['power.enabled','power.angle_rad','normal.indicated_n','friction.indicated_n','friction.coefficient','friction.left_n','friction.right_n','wear.indicated_m','thermal.indicated_k','friction.unavailable_reason','environment.relative_humidity'],
 'normal_load.set':['normal.setting_n'],'radius.set':['radius.position_m'],'drive.configure':['drive.mode'],
 'contact.set':['contact.engaged','arm.angle_rad','normal.indicated_n','truth.normal_force_n','friction.unavailable_reason'],
 'lid.set':['lid.open','lid.angle_rad'],'motion.home':['motion.homing'],
 'emergency.stop':['emergency.latched','estop.press_m','motion.homing','acquisition.phase','acquisition.elapsed_s','acquisition.points','motion.signed_speed_m_s','friction.indicated_n','friction.coefficient','friction.unavailable_reason'],
 'emergency.reset':['emergency.latched','estop.press_m'],'thermal.set_temperature':['thermal.target_k'],
 'sensor.calibrate':['normal.indicated_n','friction.indicated_n','friction.coefficient','wear.indicated_m','thermal.indicated_k','friction.unavailable_reason'],'sensor.zero':[],
 'acquisition.start':['acquisition.phase','acquisition.elapsed_s','acquisition.points','motion.distance_m','friction.indicated_n','friction.coefficient','friction.unavailable_reason','truth.friction_coefficient'],
 'acquisition.stop':['acquisition.phase','acquisition.elapsed_s','acquisition.points','motion.signed_speed_m_s','friction.indicated_n','friction.coefficient','friction.unavailable_reason'],
 'sim.step':list(SLOTS)
}
for k,v in EFFECTS.items():CAPS[k]['effects']=v
PHYSICS_EFFECTS=['disk.angle_rad','linear.position_m','motion.homing','normal.indicated_n','friction.indicated_n','friction.coefficient','friction.left_n','friction.right_n','wear.indicated_m','thermal.indicated_k','motion.signed_speed_m_s','motion.distance_m','friction.unavailable_reason','truth.friction_coefficient','truth.specimen_volume_m3','truth.pin_volume_m3','truth.debris_kg','truth.lubricant_volume_m3','truth.temperature_k','truth.friction_work_j','clock.time_s','acquisition.phase','acquisition.elapsed_s','acquisition.points']

def export_antonpaar_trb_contract_metadata():
 slots={};caps={}
 for k,v in SLOTS.items():slots[k]=dict(meaning_zh=v['meaning'],component_ids=[v['component']],value_schema=copy.deepcopy(v['schema']),unit=v['unit'],reference_conditions='SI. Positive prescribed direction; friction observation is signed, mu=absolute indicated friction/positive indicated load. Linear stage exists only in separate engineering variant. Normal load is manual deadweight. Inputs are explicitly declared engineering coefficients.',origin=v['origin'],visibility=v['visibility'],access='read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'AntonpaarTrbSession.read(state_ids=[{k!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description='Host0.1s ticks, internal<=10ms and64steps/reciprocation with exact reversal events. Instant sampled readings can alias; exact cumulative distance/work uses full path. No empirical noise claim.'),source_ids=['simulation_assumption'])
 errors=['UNKNOWN_CAPABILITY','UNKNOWN_INSTANCE','UNKNOWN_OPERATION','INVALID_ARGUMENT','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','BUSY','NOT_RUNNING','NOT_READY','INITIALIZATION_ONLY','MODEL_NOT_ACCEPTED','ARM_LOADED','LID_CLOSED','HOT_SPECIMEN','ALREADY_LOADED','NOT_LOADED','INVALID_REFERENCE','NO_CONTACT_PAIR','NORMAL_FORCE_RANGE','LINEAR_SPEED_RANGE','ANGULAR_COMBINATION_RANGE','DRIVE_INERTIA_RANGE','DRIVE_FORCE_RANGE','ZERO_TRACK_RADIUS','HOME_REQUIRED','REFERENCE_DISTANCE_RANGE','UNKNOWN_REFERENCE_CONDITIONS','REFERENCE_CONDITIONS','REFERENCE_LUBRICATION','SOURCE_ORIGIN_LOCKED','SPECIMEN_EXHAUSTED','PIN_EXHAUSTED','LUBRICANT_EXHAUSTED','FRICTION_SENSOR_OVER_RANGE','EMERGENCY_STOP','THERMAL_CONFIGURATION','THERMAL_MODEL_RANGE','LIQUID_TEMPERATURE_RANGE','NO_ACTIVE_COOLING','HEATER_NOT_INSTALLED','TEMPERATURE_NOT_READY','SAMPLING_GRID','PROFILE_GRID','PROFILE_RADIUS','HERTZ_MODEL_DOMAIN']
 for k,c in CAPS.items():
  un=k in UNSUPPORTED;async_=k=='acquisition.start';caps[k]={key:copy.deepcopy(c[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']};caps[k].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ['scene','robot','hardware'] else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.antonpaar_trb.AntonpaarTrbSession.invoke',invocation=f'session.invoke(instance_id="trb_01",capability_id={k!r},arguments={c["example_arguments"]!r},request_id="unique")'),preconditions=[] if c['kind'] in ['query','analysis'] or un else [dict(description_zh='有限试片/销/润滑剂、源距离域、停机抬臂死重/半径/装卸、安全罩、驱动组合、温区前提；拒绝原子',state_ids=['power.enabled','lid.open','contact.engaged','emergency.latched','specimen.present','pin.present','acquisition.phase'],enforced_by='runtime',check='atomic invoke and registered Session')],state_effects=[dict(state_id=x,timing='immediate',description_zh='实际命令改变的状态') for x in c['effects']],observes=[],model_effects=dict(mode='none',description_zh='主旋转/角往复与独立linear附件几何另验证；此处不声称PhysX摩擦/磨耗接触求解',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='真实时间/累计路径达到方法终点；失败/取消保留部分物理效果' if async_ else '命令返回不隐式推进物理时间',completion_state_ids=['acquisition.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','contact_pair','motion_module','differential_force_sensors'],partial_failure_zh='摩擦功、升温、磨耗和磨屑不回滚；取消冻结来源/曲线，停止运动；温度继续按宿主时间冷却'),errors=[dict(code=x,meaning_zh=x,side_effects='partial' if async_ and x in ['DRIVE_FORCE_RANGE','REFERENCE_DISTANCE_RANGE','SPECIMEN_EXHAUSTED','PIN_EXHAUSTED','LUBRICANT_EXHAUSTED','THERMAL_MODEL_RANGE','FRICTION_SENSOR_OVER_RANGE','EMERGENCY_STOP','REFERENCE_CONDITIONS'] else 'none') for x in errors],verification=[],limitations=['源经验摩擦响应与未知Archard/传感器/热参数分开','硬件范围不是已验证精度或全部上限可同时满足','主Matterix构型仅旋转/角往复；linear单独工程变体'],source_ids=['manufacturer_specs','simulation_assumption'])
  if k in ['acquisition.start','motion.home','thermal.set_temperature','power.set']:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='宿主推进运动/热/观测/有限磨损') for x in PHYSICS_EFFECTS if x!='clock.time_s']
 mechanism=dict(component_ids=['disk','linear_stage','specimen','pin','lever_arm','deadweight','friction_sensor_left','friction_sensor_right','sample_holder'],state_ids=list(slots),meaning_zh='有限材料对经验摩擦、精确滑程、差分观测及热/磨耗账本',implementation='lumped',equations=[
 'Rotary s=r*omega*t. Linear x=stroke/2*sin(2pi*f*t); angular theta=amplitude*sin(2pi*f*t). Exact s integrates |cos| across every reversal, not endpoint displacement. Linear vmax=pi*stroke*f<=.370m/s; angular peakomega in.2..2000rpm andf<=7Hz is an explicit peak-speed interpretation, allowing zero instantaneous speed at reversals.',
 'Empirical positive mu(s) uses original source-node piecewise linear interpolation. Friction work=Fn*integral mu(s)ds exactly across source knots; original signal resolution and transfer of material-pair conditions are disclosed. Negative empirical coefficients are not silently made dissipative truth.',
 'Records with runtime_basis=scalar_mean_friction (published mean mu of the pair) or scalar_speed_series_friction (published mu at several speeds, interpolated in log(speed) at the actual drive speed; outside the source range only with accepted condition transfer, clamped to the end value) are replayed as a CONSTANT mu(s) over [0, source sliding distance]; if the source does not report the distance the domain is unbounded and requires accept_unknown_conditions. Result evidence labels the scalar replay.',
 'Deadweight nominal Fn set only at stopped raised arm. Actual Fn=nominal*explicit mechanical factor; indicated load uses separately supplied observation gain/offset. No active load servo.',
 'Conservative combined envelope: Fn*mu*r + J*angular_acceleration_peak <=.45Nm; linear Fn*mu + moving_mass*acceleration_peak <=explicit engineering drive force. Friction force<=20N. First empirical threshold crossing/finite inventory/source endpoint stops with actual partial effects.',
 'Dual sensing targets y1=+F/2*g1+offset1+common_drift(t,T), y2=-F/2*g2+offset2+common_drift(t,T). Identical first-order response cancels common component. ObservedF=(y1-y2-external_zero)/external_gain; observedmu=abs(Fobs)/Fn_obs. Low load, zero velocity and sensor overrange yield null with reason.',
 'Internal sensor forcing uses distance-mean empirical friction on each monotonic segment. Exact exponential sensor update and its time integral times segment mean signed velocity estimate observed work. This is an explicit piecewise-constant observation approximation; true friction work is independently exact along source distance.',
 'Archard dV=k*Fn*ds/H separately for specimen and pin, with explicit coefficients/hardness. Removed volumes times individual densities exactly enter separate debris reservoirs. Finite lubricant dragout=declared loss_per_distance*ds enters a catch inventory, not invented evaporation.',
 'C*dT/dt=Pfriction_interval+clamp(K*(Ttarget-T),0,Pmax)-G*(T-Tambient). Piecewise exact exponential thermal regimes preserve heater+friction-ambient=stored heat. Heater characteristics are supplied engineering assumptions; not OEM power/ramp calibration. No active cooling or flash-contact-temperature prediction.',
 'External wear profile integrates positive baseline-height deficit and negative pile-up separately. Circular track integrates2pi*(r+x)*depth(x), retaining radial weight and signed net. Uniform-track assumption is explicit. Hertz sphere-flat a=(3FnR/(4E*))^(1/3),1/E*=(1-v1^2)/E1+(1-v2^2)/E2,small-contact a/R<=.1.'
 ],parameters={},source_ids=['manufacturer_specs','simulation_assumption'],limitations=['Default curve is another tribometer material-pair experiment, not current TRB3native raw acquisition; scalar TRB3 and literature means are replayed only as labelled constant mu, never as synthetic transient curves.','Constant-mu scalar replay has no running-in, stick-slip, fluctuation, humidity/temperature or wear-driven drift; mean definitions (steady-state vs whole-test) follow each source.','Concentration/grade/load/speed/temperature variants do not count as50independent pure substances.','No empirical noise distribution, achieved.06mN force resolution, ASTM certification or true wear calibration.','The photographed red button is bound to a local engineering stop/reset interlock; original button function and certified emergency-stop behavior are not confirmed.','Predetermined motion has no acceleration ramp or motor/contact dynamics; peak-inertia envelope is conservative and explicitly engineering.','Online wear depth proxy derives declared uniform track from engineering Archard wear; original source wear data not fabricated frommu.','Strict source-condition mode terminates with partial effects if friction heating moves outside its explicitly configured source-temperature tolerance.','Thermal forcing uses exact interval work but interval-mean heat power; no flash-temperature, radiation/nonlinear property or insert/remove enthalpy model.','Linear geometry exists separately and is not silently overlaid on rotary main variant; actual scene tests determine availability there.'])
 caps['acquisition.start']['source_ids'].append('mendeley_leadbronze');caps['reference.query']['source_ids'].append('mendeley_leadbronze');mechanism['source_ids'].append('mendeley_leadbronze');caps['analysis.hertz']['source_ids'].append('nist_elastic_contact');caps['analysis.wear_profile']['source_ids'].append('nist_wear_method');mechanism['source_ids']+=['nist_elastic_contact','nist_wear_method']
 spec=importlib.util.spec_from_file_location('_trb_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()});ann.annotate_export(SLUG,caps,slots,{'tribology':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'specimen.load':(SAMPLE,SAMPLE_EXAMPLE),'pin.load':(PIN,PIN_EXAMPLE),'lubricant.load':(LUBRICANT,LUBRICANT_EXAMPLE),'drive.configure':(DRIVE,DRIVE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'tribology':mechanism})

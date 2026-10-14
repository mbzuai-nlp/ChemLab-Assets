"""CHEM129 finite scratch process: empirical Ft, explicit force/heat/removal proxy."""
from pathlib import Path
import copy,json,math,importlib.util
import numpy as np
PROTOCOL_VERSION='1.0.0';SLUG='antonpaar_rst300';CATALOG_ID='CHEM-129';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch04/t1_thermal'/SLUG
DT=.1;SPEED_MIN=.4e-3/60;SPEED_MAX=.01;FORCE_MAX=200.;DEPTH_MAX=.001
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
 p=ASSET/'scratch_library.json';return json.loads(p.read_text()) if p.exists() else {'substances':{}}

def force_ramp(force0,target0,slope,tau,dt):
 """First-order force tracking of a linear target: endpoint, integralF and integral(tF)."""
 if dt==0:return force0,0.,0.
 q=dt/tau
 if q<.02:
  end=force0;i0=force0*dt;i1=force0*dt*dt/2
  for n in range(1,11):
   term=(target0-force0)*(-1)**(n+1)*q**n/math.factorial(n)
   if n>=2:term+=slope*dt*(-1)**n*q**(n-1)/math.factorial(n)
   end+=term;i0+=term*dt/(n+1);i1+=term*dt*dt/(n+2)
  return end,i0,i1
 e=math.exp(-q);m0=-tau*math.expm1(-q);m1=tau*tau*(-math.expm1(-q)-q*e);a=target0-slope*tau;b=force0-a
 return a+slope*dt+b*e,a*dt+slope*dt*dt/2+b*m0,a*dt*dt/2+slope*dt**3/3+b*m1

def curve(reference,accept_negative=False):
 try:x=np.asarray(reference['values']['distance_m'],float);f=np.asarray(reference['values']['tangential_force_n'],float);n=np.asarray(reference['values']['normal_force_n'],float)
 except (TypeError,ValueError,KeyError):raise ActionRejected('INVALID_REFERENCE')
 if not reference.get('runtime_usable') or len(x)<2 or x.shape!=f.shape or x.shape!=n.shape or np.any(~np.isfinite(x)) or np.any(~np.isfinite(f)) or np.any(~np.isfinite(n)) or np.any(np.diff(x)<=0) or np.any(n<=0):raise ActionRejected('INVALID_REFERENCE')
 changes=int(np.sum(f<0))
 if changes and not accept_negative:raise ActionRejected('NEGATIVE_SOURCE_FORCE')
 return x,np.maximum(f,0)/n,changes

def critical_load(rows,channel,threshold,persistence,load_rows=None):
 """Contiguous observed threshold bracket; postscan Lc needs original scratch load."""
 if not rows or not any(r.get(channel) is not None for r in rows):return dict(critical_load_n=None,position_m=None,reason='NO_AVAILABLE_OBSERVATIONS')
 if rows[0].get(channel) is not None and rows[0][channel]>=threshold:return dict(critical_load_n=None,position_m=None,reason='LEFT_CENSORED')
 for i in range(1,len(rows)):
  before,now=rows[i-1:i+1];v0=before.get(channel);v1=now.get(channel)
  if v0 is None or v1 is None or not v0<threshold<=v1 or i+persistence>len(rows):continue
  if not all(r.get(channel) is not None and r[channel]>=threshold for r in rows[i:i+persistence]):continue
  fraction=(threshold-v0)/(v1-v0);position=before['distance_m']+fraction*(now['distance_m']-before['distance_m'])
  if channel=='residual_depth_m':
   if not load_rows:return dict(critical_load_n=None,position_m=position,reason='POSTSCAN_POSITION_ONLY_REQUIRES_SCRATCH_LOAD')
   for lo,hi in zip(load_rows,load_rows[1:]):
    if lo.get('normal_force_n') is None or hi.get('normal_force_n') is None:continue
    if lo['distance_m']<=position<=hi['distance_m'] and hi['distance_m']>lo['distance_m']:
     weight=(position-lo['distance_m'])/(hi['distance_m']-lo['distance_m']);load=lo['normal_force_n']+weight*(hi['normal_force_n']-lo['normal_force_n']);return dict(critical_load_n=load,position_m=position,reason='POSTSCAN_THRESHOLD_MAPPED_TO_SAME_TRACK_SCRATCH_LOAD')
   return dict(critical_load_n=None,position_m=position,reason='SCRATCH_LOAD_NOT_BRACKETED')
  if before.get('normal_force_n') is None or now.get('normal_force_n') is None:return dict(critical_load_n=None,position_m=position,reason='NO_LOAD_OBSERVATION')
  return dict(critical_load_n=before['normal_force_n']+fraction*(now['normal_force_n']-before['normal_force_n']),position_m=position,reason='OBSERVED_THRESHOLD_CROSSING_NOT_INTRINSIC_ADHESION')
 return dict(critical_load_n=None,position_m=None,reason='NO_CONFIRMED_BRACKET')

PLANT_EXAMPLE=dict(accept_engineering=True,ambient_k=296.15,thermal_capacity_j_k=100.,heat_loss_w_k=1.,temperature_max_k=333.15,safe_access_temperature_k=313.15,force_response_tau_s=.05,force_settle_tolerance_n=.001,force_release_n=1e-6,contact_stiffness_n_m=1e7,head_speed_m_s=.01,force_gain=1.,force_offset_n=0.,friction_gain=1.,friction_offset_n=0.,depth_gain=1.,depth_offset_m=0.,sensor_tau_s=.002,scan_friction_coefficient=.03)
PLANT=obj({'accept_engineering':BOOL,'ambient_k':number('K',273.15,323.15),'thermal_capacity_j_k':number('J/K',.001,1e6),'heat_loss_w_k':number('W/K',0,1000),'temperature_max_k':number('K',273.15,373.15),'safe_access_temperature_k':number('K',273.15,333.15),'force_response_tau_s':number('s',.00001,100),'force_settle_tolerance_n':number('N',1e-6,.1),'force_release_n':number('N',1e-12,.001),'contact_stiffness_n_m':number('N/m',1e3,1e12),'head_speed_m_s':number('m/s',.0001,.02),'force_gain':number('1',.1,10),'force_offset_n':number('N',-5,5),'friction_gain':number('1',.1,10),'friction_offset_n':number('N',-5,5),'depth_gain':number('1',.1,10),'depth_offset_m':number('m',-.001,.001),'sensor_tau_s':number('s',.00001,100),'scan_friction_coefficient':number('1',0,1)})
SAMPLE_EXAMPLE=dict(reference_id='borofloat33_10n_r1',length_m=.12,width_m=.08,substrate_thickness_m=.006,coating_thickness_m=0.,coating_density_kg_m3=4000.,substrate_density_kg_m3=2230.,coating_removal_energy_j_m3=1e9,substrate_removal_energy_j_m3=1e9,sample_wear_energy_fraction=.1,track_width_m=.0001,surface_slope_x=0.,accept_engineering=True,accept_condition_transfer=True,accept_unknown_conditions=True,accept_negative_force_projection=True)
SAMPLE=obj({'reference_id':TEXT,'length_m':number('m',.07,.2),'width_m':number('m',.06,.2),'substrate_thickness_m':number('m',1e-9,.01),'coating_thickness_m':number('m',0,.001),'coating_density_kg_m3':number('kg/m^3',100,25000),'substrate_density_kg_m3':number('kg/m^3',100,25000),'coating_removal_energy_j_m3':number('J/m^3',1e3,1e13),'substrate_removal_energy_j_m3':number('J/m^3',1e3,1e13),'sample_wear_energy_fraction':number('1',0,1),'track_width_m':number('m',1e-7,.003),'surface_slope_x':number('1',-.001,.001),'accept_engineering':BOOL,'accept_condition_transfer':BOOL,'accept_unknown_conditions':BOOL,'accept_negative_force_projection':BOOL})
STYLUS_EXAMPLE=dict(radius_m=.0002,cone_angle_rad=2*math.pi/3,volume_m3=1e-9,density_kg_m3=3510.,removal_energy_j_m3=1e10,wear_energy_fraction=.001,accept_engineering=True)
STYLUS=obj({'radius_m':number('m',1e-5,.003),'cone_angle_rad':number('rad',math.pi/6,5*math.pi/6),'volume_m3':number('m^3',1e-15,1e-6),'density_kg_m3':number('kg/m^3',100,25000),'removal_energy_j_m3':number('J/m^3',1e3,1e14),'wear_energy_fraction':number('1',0,1),'accept_engineering':BOOL})
METHOD_EXAMPLE=dict(pass_kind='scratch',start_x_m=0.,path_y_m=0.,length_m=.002,speed_m_s=10e-3/60,initial_force_n=10.,final_force_n=10.,scan_force_n=.1,source_start_distance_m=5.6467971453911e-6,sample_interval_s=.1,criterion_channel='friction_force_n',criterion_threshold_n=2.,criterion_threshold_m=5e-6,criterion_threshold_fraction=.2,criterion_persistence=2,stop_on_criterion=False,timeout_s=180.)
METHOD=obj({'pass_kind':enum(['prescan','scratch','postscan']),'start_x_m':number('m',0,.07),'path_y_m':number('m',-.03,.03),'length_m':number('m',1e-5,.07),'speed_m_s':number('m/s',SPEED_MIN,SPEED_MAX),'initial_force_n':number('N',.001,200),'final_force_n':number('N',.001,200),'scan_force_n':number('N',.001,1),'source_start_distance_m':number('m',0,1.),'sample_interval_s':number('s',.1,60),'criterion_channel':enum(['friction_force_n','friction_coefficient','penetration_depth_m','residual_depth_m']),'criterion_threshold_n':number('N',0,200),'criterion_threshold_m':number('m',0,.001),'criterion_threshold_fraction':number('1',0,200),'criterion_persistence':integer(1,100),'stop_on_criterion':BOOL,'timeout_s':number('s',.1,20000)})
CALIBRATION_EXAMPLE=dict(normal_gain_correction=1.,normal_zero_n=0.,friction_gain_correction=1.,friction_zero_n=0.,depth_gain_correction=1.,depth_zero_m=0.)
CALIBRATION=obj({'normal_gain_correction':number('1',.001,1000),'normal_zero_n':number('N',-10,10),'friction_gain_correction':number('1',.001,1000),'friction_zero_n':number('N',-10,10),'depth_gain_correction':number('1',.001,1000),'depth_zero_m':number('m',-.001,.001)})
ROW=obj({'time_s':number('s'),'elapsed_s':number('s'),'phase':TEXT,'distance_m':number('m',0),'stage_x_m':number('m',0,.07),'normal_target_n':number('N',0,200),'normal_force_n':nullable(number('N')),'friction_force_n':nullable(number('N')),'friction_coefficient':nullable(number('1')),'raw_depth_m':nullable(number('m')),'penetration_depth_m':nullable(number('m')),'residual_depth_m':nullable(number('m')),'temperature_k':nullable(number('K')),'observed_tangential_work_j':number('J'),'unavailable_reason':nullable(TEXT)})
CRITICAL=obj({'critical_load_n':nullable(number('N')),'position_m':nullable(number('m')),'reason':TEXT})
RESULT=obj({'operation_id':nullable(TEXT),'phase':PHASE,'pass_kind':nullable(TEXT),'elapsed_s':number('s'),'distance_m':number('m',0),'rows':array(ROW,0,250000),'critical':CRITICAL,'criterion_unit':TEXT,'template_source':nullable(TEXT),'reference_id':nullable(TEXT),'source_projection_count':integer(),'evidence':TEXT,'method_json':nullable(TEXT),'error_code':nullable(TEXT)})
CAPS={};SLOTS={}
def cap(k,i,o,c,z,kind='device_command',visibility='agent',example=None):CAPS[k]=dict(input_schema=i,output_schema=o,component_ids=c,function_zh=z,kind=kind,visibility=visibility,example_arguments=example or {},effects=[])
def slot(k,s,u,z,origin='derived',visibility='agent',component='controller'):SLOTS[k]=dict(schema=s,unit=u,meaning=z,origin=origin,visibility=visibility,component=component,write=None)
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller'],'显式有限响应/热/观测工程模型','configuration','evaluator',PLANT_EXAMPLE)
for name,sc,ex,component in [('specimen',SAMPLE,SAMPLE_EXAMPLE,'specimen'),('stylus',STYLUS,STYLUS_EXAMPLE,'stylus')]:
 cap(name+'.load',sc,obj({'loaded':BOOL}),[component],'抬头松夹具装入有限'+name,'configuration','evaluator',ex);cap(name+'.remove',obj({}),obj({'removed':BOOL}),[component],'抬头后回收剩余'+name,'configuration','evaluator')
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_switch','controller'],'电源开关，过程只由step推进',example={'enabled':True})
cap('clamp.set',obj({'locked':BOOL}),obj({'locked':BOOL}),['clamp_jaw'],'抬头且静止时手动锁紧试片',example={'locked':True})
cap('stage.move',obj({'x_m':number('m',0,.07),'y_m':number('m',-.03,.03),'speed_m_s':number('m/s',SPEED_MIN,SPEED_MAX)}),obj({'moving':BOOL}),['stage_x','stage_y'],'抬头后异步XY对位，不能瞬移',example={'x_m':0.,'y_m':0.,'speed_m_s':.01})
cap('head.set_position',obj({'distance_m':number('m',0,.04)}),obj({'target_m':number('m',0,.04)}),['load_head'],'粗抬头目标，速度为显式工程参数',example={'distance_m':0.})
cap('tool.select',obj({'mode':enum(['stylus','microscope'])}),obj({'mode':enum(['stylus','microscope'])}),['microscope','stylus'],'仅软件工位选择；固定显微镜靠Y台对位，不虚构转塔',example={'mode':'stylus'})
cap('contact.set',obj({'engaged':BOOL}),obj({'engaged':BOOL}),['load_head','stylus'],'工位到位后允许接触加载；卸载必须等待真实载荷释放',example={'engaged':True})
cap('sensor.calibrate',CALIBRATION,CALIBRATION,['controller'],'外部标定参数，不免费读取过程真值','configuration',example=CALIBRATION_EXAMPLE)
cap('sensor.zero',obj({}),obj({'zeroed':BOOL}),['controller'],'无接触静止状态独立传感器零点')
cap('method.configure',METHOD,METHOD,['controller'],'预扫描/单道划痕/后扫描以及显式观察判据','configuration',example=METHOD_EXAMPLE)
cap('acquisition.start',obj({}),obj({'operation_id':TEXT}),['load_head','stage_x','stylus','controller'],'异步真实加载/进给/卸载，保留有限损伤轨道')
cap('acquisition.stop',obj({}),obj({'phase':enum(['cancelled'])}),['load_head','stage_x','controller'],'取消冻结部分测量，保留损伤并按宿主步进卸载')
cap('acquisition.result',obj({}),RESULT,['controller'],'纯读属于操作owner的冻结结果','query')
cap('reference.query',obj({'reference_id':TEXT}),obj({'reference_json':TEXT,'template_source':TEXT}),['controller'],'原曲线/图像/工况参考，不将FnC/Rd空值冒实测','query',example={'reference_id':SAMPLE_EXAMPLE['reference_id']})
cap('image.reference',obj({'reference_id':TEXT}),obj({'images_json':TEXT,'evidence':TEXT}),['microscope'],'仅返回实文献划痕图像索引，非本次动态显微成像','query',example={'reference_id':SAMPLE_EXAMPLE['reference_id']})
cap('analysis.critical_load',obj({'rows':array(ROW,1,250000),'load_rows':array(ROW,0,250000),'channel':METHOD['properties']['criterion_channel'],'threshold_n':number('N',0,200),'threshold_m':number('m',0,.001),'threshold_fraction':number('1',0,200),'persistence':integer(1,100)}),CRITICAL,['controller'],'由外部观测的包围阈值交点得到Lc；非固有粘附强度','analysis')
cap('debris.collect',obj({}),obj({'collected':BOOL}),['sample_holder'],'抬头松夹具回收有限涂层/基底/压头碎屑','configuration','evaluator')
LEDGER=obj({k:number(u) for k,u in [(n+'_'+f,'kg') for n in ['coating','substrate','stylus'] for f in ['loaded_kg','remaining_kg','debris_kg','recovered_kg','debris_recovered_kg','error_kg']]+[(k,'J') for k in ['tangential_work_j','wear_work_j','heat_input_j','ambient_loss_j','stored_thermal_j','thermal_error_j','normal_loading_work_j','normal_recovered_work_j','stored_elastic_j','normal_error_j']]+[(k,'m^3') for k in ['coating_removed_m3','substrate_removed_m3','stylus_removed_m3']]})
cap('ledger.read',obj({}),LEDGER,['controller'],'有限涂层/基底/压头/碎屑和切向热、法向弹簧账本','query','evaluator')
cap('sim.step',obj({'dt_s':number('s',0,3600)}),obj({'time_s':number('s')}),['controller'],'唯一宿主物理推进入口','simulation','evaluator',{'dt_s':.1})
cap('emergency.stop',obj({}),obj({'latched':BOOL}),['controller'],'本地软件停机锁存，非原厂急停认证')
cap('emergency.reset',obj({}),obj({'latched':BOOL}),['controller'],'载荷卸除后明确复位，不自动续划')
UNSUPPORTED=['acoustic_emission.acquire','panorama.capture','vickers.measure','scratch.multipass','scratch.multipostscan','scratch.crosscan','depth.closed_loop','hardware.connect','robot.sample_change','noise.empirical','accuracy.certification','astm.certification','thermal.set_temperature']
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'未实现：'+k)
for k,z,c in [('plant.configured','显式工程参数已接受','controller'),('power.enabled','供电','power_switch'),('clamp.locked','试片夹具锁紧','clamp_jaw'),('contact.engaged','压头接触使能','stylus'),('specimen.present','有限试片存在','specimen'),('stylus.present','有限压头存在','stylus'),('emergency.latched','软件安全停机锁存','controller'),('stage.moving','抬头后的对位移动中','stage_x'),('head.moving','粗抬头目标未到达','load_head')]:slot(k,BOOL,'1',z,component=c)
for k,lo,hi,u,z,c in [('stage.x_m',0,.07,'m','实际X台位置','stage_x'),('stage.y_m',-.03,.03,'m','实际Y台位置','stage_y'),('head.lift_m',0,.04,'m','头部粗抬升，0为名义接触平面','load_head'),('head.penetration_m',0,.001,'m','弹簧压入与局部移除槽深的工程姿态，不是校准Pd真值','stylus'),('clamp.release_m',0,.003,'m','夹具手动释放代理','clamp_jaw'),('power.angle_rad',0,.28,'rad','电源开关工程姿态','power_switch')]:slot(k,number(u,lo,hi),u,z,'scene',component=c)
for k,lo,hi,u,z,c in [('stage.target_x_m',0,.07,'m','X对位目标','stage_x'),('stage.target_y_m',-.03,.03,'m','Y对位目标','stage_y'),('head.target_lift_m',0,.04,'m','粗抬头目标','load_head'),('load.target_n',0,200,'N','当前高量程加载控制目标，非实际法向力','load_head')]:slot(k,number(u,lo,hi),u,z,'setpoint',component=c)
slot('tool.mode',enum(['stylus','microscope']),'1','软件工位；固定物镜通过台Y对位，非转塔','setpoint',component='microscope')
for k,u,z in [('load.indicated_n','N','实际工程力经独立gain/offset/响应的观测'),('friction.indicated_n','N','源Ft/FnC派生代理经实际载荷与独立观测'),('friction.coefficient','1','观测Ft除观测Fn，非直接读取源真实系数'),('depth.raw_m','m','含基线/弹性/局部残余的工程深度观测'),('depth.penetration_m','m','主划痕观测减同坐标预扫描，非卸载后残余'),('depth.residual_m','m','只有后扫描可得的同坐标残余观测'),('thermal.indicated_k','K','理想集中节点温度，未实现校准噪声')]:slot(k,nullable(number(u)),u,z)
for k,u,z in [('truth.normal_force_n','N','一阶闭环实际载荷'),('truth.coating_volume_m3','m^3','有限涂层剩余体积'),('truth.substrate_volume_m3','m^3','有限基底剩余体积'),('truth.stylus_volume_m3','m^3','有限压头剩余体积'),('truth.debris_kg','kg','涂层基底压头碎屑总质量'),('truth.local_groove_m','m','当前被移除的局部平均槽深'),('truth.temperature_k','K','集中热节点真值'),('truth.tangential_work_j','J','源响应作用于实际位移的切向耗散')]:slot(k,number(u,0),u,z,'process_truth','evaluator')
slot('clock.time_s',number('s',0),'s','宿主唯一时钟');slot('acquisition.phase',PHASE,'1','操作生命周期');slot('acquisition.pass_phase',enum(['idle','loading','scanning','unloading']),'1','真实加载/扫描/卸载阶段');slot('acquisition.distance_m',number('m',0,.07),'m','本次实际划程');slot('acquisition.elapsed_s',number('s',0),'s','实际经过时间');slot('acquisition.points',integer(0,250000),'1','实际记录点数');slot('observation.unavailable_reason',nullable(TEXT),'1','未接触/量程/基线不可用原因')

def _threshold(a,prefix='criterion_'):
 channel=a[prefix+'channel'] if prefix else a['channel'];key='threshold_n' if channel=='friction_force_n' else 'threshold_fraction' if channel=='friction_coefficient' else 'threshold_m';return a[prefix+key],('N' if key.endswith('_n') else '1' if key.endswith('_fraction') else 'm')

class AntonpaarRst300Runtime:
 def __init__(self,instance_id='rst300_01'):
  self.instance_id=instance_id;self.plant=None;self.specimen=None;self.stylus=None;self.reference=None;self.ref_x=np.array([]);self.ref_mu=np.array([]);self.projection_count=0;self.volumes={k:0. for k in ['coating','substrate','stylus']};self.mass_loaded={k:0. for k in self.volumes};self.mass_recovered=self.mass_loaded.copy();self.debris=self.mass_loaded.copy();self.debris_recovered=self.mass_loaded.copy();self.removed_volumes=self.mass_loaded.copy();self.power=False;self.clamped=False;self.contact=False;self.emergency=False;self.stage_x=0.;self.stage_y=0.;self.target_x=0.;self.target_y=0.;self.stage_speed=.01;self.head_lift=.04;self.head_target=.04;self.tool='stylus';self.force=0.;self.force_target=0.;self.local_groove=0.;self.temperature=296.15;self.initial_temperature=296.15;self.tangential_work=0.;self.wear_work=0.;self.heat_input=0.;self.ambient_loss=0.;self.normal_loading=0.;self.normal_recovered=0.;self.observed_force=0.;self.observed_friction=0.;self.observed_depth=0.;self.observed_work=0.;self.calibration=copy.deepcopy(CALIBRATION_EXAMPLE);self.method=copy.deepcopy(METHOD_EXAMPLE);self.prescan_records={};self.tracks=[];self.active_track=None;self.baseline=None;self.phase='idle';self.pass_phase='idle';self.distance=0.;self.elapsed=0.;self.rows=[];self.operation_start=0.;self.operation_id=None;self.operation_method=None;self.operation_reference=None;self.operation_source=None;self.operation_projection=0;self.operation_evidence=None;self.operation_kind=None;self.error=None;self._counter=0;self.records={};self.time_s=0.;self._ticks=0;self._remainder=0.;self.revision=0;self._requests={};self._owners={};self._cancel_operations={};self._principals={'agent':'agent','evaluator':'evaluator'}
 def register_principal(self,principal_id,visibility='agent'):
  if not isinstance(principal_id,str) or not principal_id or principal_id in self._principals or visibility not in ('agent','debug','evaluator'):raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='agent'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return AntonpaarRst300Session(self,principal_id)
 def describe(self):return export_antonpaar_rst300_contract_metadata()
 def _moving(self):return bool(abs(self.stage_x-self.target_x)>1e-12 or abs(self.stage_y-self.target_y)>1e-12)
 def _head_moving(self):return bool(abs(self.head_lift-self.head_target)>1e-12)
 def _depth(self):return self.force/self.plant['contact_stiffness_n_m']+self.local_groove if self.contact and self.plant else 0.
 def _indicated(self):
  if not self.power:return dict(normal=None,friction=None,mu=None,raw=None,penetration=None,residual=None,reason='POWER_OFF')
  c=self.calibration;n=(self.observed_force-c['normal_zero_n'])/c['normal_gain_correction'];f=(self.observed_friction-c['friction_zero_n'])/c['friction_gain_correction'];raw=(self.observed_depth-c['depth_zero_m'])/c['depth_gain_correction'];reason=None;pen=res=None;mu=None
  if abs(n)>200 or abs(f)>200 or abs(raw)>DEPTH_MAX:reason='SENSOR_OVER_RANGE';n=f=raw=None
  elif not self.contact:reason='NO_CONTACT';f=raw=None
  elif self.phase!='running' or self.pass_phase!='scanning':reason='NO_SCANNING';f=None
  else:
   mu=abs(f)/n if n is not None and n>=.001 else None
   if mu is None:reason='LOW_LOAD'
   if self.operation_kind in ('scratch','postscan') and self.baseline:
    baseline=float(np.interp(self.distance,self.baseline['distance'],self.baseline['depth']));value=raw-baseline
    if self.operation_kind=='scratch':pen=value
    else:res=value
  return dict(normal=n,friction=f,mu=mu,raw=raw,penetration=pen,residual=res,reason=reason)
 def read(self,role='evaluator'):
  v=self._indicated();d={'plant.configured':self.plant is not None,'power.enabled':self.power,'clamp.locked':self.clamped,'contact.engaged':self.contact,'specimen.present':self.specimen is not None,'stylus.present':self.stylus is not None,'emergency.latched':self.emergency,'stage.moving':self._moving(),'head.moving':self._head_moving(),'stage.x_m':self.stage_x,'stage.y_m':self.stage_y,'head.lift_m':self.head_lift,'head.penetration_m':self._depth(),'clamp.release_m':0. if self.clamped else .003,'power.angle_rad':.28 if self.power else 0.,'stage.target_x_m':self.target_x,'stage.target_y_m':self.target_y,'head.target_lift_m':self.head_target,'load.target_n':self.force_target,'tool.mode':self.tool,'load.indicated_n':v['normal'],'friction.indicated_n':v['friction'],'friction.coefficient':v['mu'],'depth.raw_m':v['raw'],'depth.penetration_m':v['penetration'],'depth.residual_m':v['residual'],'thermal.indicated_k':self.temperature if self.power else None,'truth.normal_force_n':self.force,'truth.coating_volume_m3':self.volumes['coating'],'truth.substrate_volume_m3':self.volumes['substrate'],'truth.stylus_volume_m3':self.volumes['stylus'],'truth.debris_kg':sum(self.debris.values()),'truth.local_groove_m':self.local_groove,'truth.temperature_k':self.temperature,'truth.tangential_work_j':self.tangential_work,'clock.time_s':self.time_s,'acquisition.phase':self.phase,'acquisition.pass_phase':self.pass_phase,'acquisition.distance_m':self.distance,'acquisition.elapsed_s':self.elapsed,'acquisition.points':len(self.rows),'observation.unavailable_reason':v['reason']}
  return copy.deepcopy({k:x for k,x in d.items() if role in ('evaluator','debug') or SLOTS[k]['visibility']=='agent'})
 def _density(self,n):return self.stylus['density_kg_m3'] if n=='stylus' and self.stylus else self.specimen[n+'_density_kg_m3'] if self.specimen and n!='stylus' else 0.
 def _ledger(self):
  r={}
  for n in self.volumes:
   remaining=self.volumes[n]*self._density(n)
   for k,v in [('loaded_kg',self.mass_loaded[n]),('remaining_kg',remaining),('debris_kg',self.debris[n]),('recovered_kg',self.mass_recovered[n]),('debris_recovered_kg',self.debris_recovered[n]),('error_kg',self.mass_loaded[n]-remaining-self.mass_recovered[n]-self.debris[n]-self.debris_recovered[n])]:r[n+'_'+k]=v
   r[n+'_removed_m3']=self.removed_volumes[n]
  stored=self.plant['thermal_capacity_j_k']*(self.temperature-self.initial_temperature) if self.plant else 0.;elastic=self.force*self.force/(2*self.plant['contact_stiffness_n_m']) if self.plant else 0.
  r.update(tangential_work_j=self.tangential_work,wear_work_j=self.wear_work,heat_input_j=self.heat_input,ambient_loss_j=self.ambient_loss,stored_thermal_j=stored,thermal_error_j=self.tangential_work-self.wear_work-self.ambient_loss-stored,normal_loading_work_j=self.normal_loading,normal_recovered_work_j=self.normal_recovered,stored_elastic_j=elastic,normal_error_j=self.normal_loading-self.normal_recovered-elastic);return r
 def _result(self):
  if self.phase!='running' and self.operation_id in self.records:return copy.deepcopy(self.records[self.operation_id])
  threshold,unit=_threshold(json.loads(self.operation_method) if self.operation_method else self.method);channel=(json.loads(self.operation_method) if self.operation_method else self.method)['criterion_channel'];persistence=(json.loads(self.operation_method) if self.operation_method else self.method)['criterion_persistence'];scan=[r for r in self.rows if r['phase']=='scanning'];critical=critical_load(scan,channel,threshold,persistence,self.active_track.get('observed_rows') if self.active_track and self.operation_kind=='postscan' else None)
  return dict(operation_id=self.operation_id,phase=self.phase,pass_kind=self.operation_kind,elapsed_s=self.elapsed,distance_m=self.distance,rows=copy.deepcopy(self.rows),critical=critical,criterion_unit=unit,template_source=self.operation_source,reference_id=self.operation_reference,source_projection_count=self.operation_projection,evidence=self.operation_evidence or 'No operation.',method_json=self.operation_method,error_code=self.error)
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
  if self.phase=='running' or self._moving() or self._head_moving():raise ActionRejected('BUSY')
 def _released(self):
  if self.force>0:raise ActionRejected('LOAD_NOT_RELEASED')
 def _access(self):
  self._stopped();self._released()
  if self.contact or self.head_lift<.02:raise ActionRejected('HEAD_NOT_CLEAR')
  if self.clamped:raise ActionRejected('CLAMP_LOCKED')
  if self.plant and self.temperature>self.plant['safe_access_temperature_k']:raise ActionRejected('HOT_SPECIMEN')
 def _key(self,a=None):
  a=a or self.method;return (a['start_x_m'],a['path_y_m'],a['length_m'])
 def _overlap(self,t,a):return t['distance']>0 and abs(t['y']-a['path_y_m'])<self.specimen['track_width_m'] and max(t['start'],a['start_x_m'])<min(t['start']+t['distance'],a['start_x_m']+a['length_m'])-1e-12
 def _conditions(self):
  a=self.method;c=self.reference.get('conditions',{});s=self.specimen
  pairs=[('speed_m_s',a['speed_m_s']),('tip_radius_m',self.stylus['radius_m']),('tip_included_angle_rad',self.stylus['cone_angle_rad']),('temperature_k',self.temperature)]
  for k,v in pairs:
   if c.get(k) is None:
    if not s['accept_unknown_conditions']:raise ActionRejected('UNKNOWN_REFERENCE_CONDITIONS')
   elif not s['accept_condition_transfer'] and abs(c[k]-v)>max(1e-10,abs(v)*1e-6):raise ActionRejected('REFERENCE_CONDITIONS')
  if not s['accept_condition_transfer']:
   source_n=float(np.interp(a['source_start_distance_m'],self.ref_x,np.asarray(self.reference['values']['normal_force_n'])))
   if max(abs(a['initial_force_n']-source_n),abs(a['final_force_n']-source_n))>1e-5 or s['coating_thickness_m']>0:raise ActionRejected('REFERENCE_CONDITIONS')
 def _invoke(self,k,a):
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  if k=='ledger.read':return self._ledger()
  if k=='acquisition.result':return self._result()
  if k=='analysis.critical_load':return critical_load(a['rows'],a['channel'],_threshold(a,'')[0],a['persistence'],a['load_rows'])
  if k in ('reference.query','image.reference'):
   rec=library()['substances'].get(a['reference_id'])
   if rec is None:raise ActionRejected('INVALID_REFERENCE')
   if k=='reference.query':return dict(reference_json=json.dumps(rec,ensure_ascii=False),template_source=json.dumps(rec.get('source'),ensure_ascii=False))
   return dict(images_json=json.dumps(rec.get('images',[]),ensure_ascii=False),evidence='Published reference images only; no new image captured for this operation. Empty list means no linked source image. Inline panorama<=30mm is not implemented; no hidden true damage is turned into a microscope image.')
  if k=='plant.configure':
   if self.plant or self.time_s:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   if a['temperature_max_k']<a['ambient_k'] or a['force_release_n']>a['force_settle_tolerance_n']:raise ActionRejected('PLANT_CONFIGURATION')
   self.plant=copy.deepcopy(a);self.temperature=self.initial_temperature=a['ambient_k'];return {'configured':True}
  if k=='power.set':
   self._stopped()
   if a['enabled'] and (not self.plant or self.emergency):raise ActionRejected('NOT_READY')
   if not a['enabled']:self._released()
   self.power=a['enabled'];return a
  if k=='emergency.stop':
   self.emergency=True;self.target_x=self.stage_x;self.target_y=self.stage_y;self.head_target=self.head_lift
   if self.phase=='running':self._finish('failed','SOFTWARE_STOP')
   self.force_target=0.;return {'latched':True}
  if k=='emergency.reset':self._stopped();self._released();self.emergency=False;return {'latched':False}
  if k=='clamp.set':
   self._stopped();self._released()
   if self.contact or self.head_lift<.02:raise ActionRejected('HEAD_NOT_CLEAR')
   if a['locked'] and not self.specimen:raise ActionRejected('NO_SPECIMEN')
   self.clamped=a['locked'];return a
  if k=='stage.move':
   self._stopped();self._released()
   if not self.power or self.emergency:raise ActionRejected('NOT_READY')
   if self.contact or self.head_lift<.02:raise ActionRejected('HEAD_NOT_CLEAR')
   self.target_x=a['x_m'];self.target_y=a['y_m'];self.stage_speed=a['speed_m_s'];return {'moving':self._moving()}
  if k=='head.set_position':
   self._stopped();self._released()
   if not self.power or self.emergency:raise ActionRejected('NOT_READY')
   if self.contact:raise ActionRejected('CONTACT_ENGAGED')
   if a['distance_m']<.02 and (not self.specimen or not self.clamped):raise ActionRejected('NO_CLAMPED_SPECIMEN')
   self.head_target=a['distance_m'];return {'target_m':self.head_target}
  if k=='tool.select':
   self._stopped();self._released()
   if self.contact or self.head_lift<.02:raise ActionRejected('HEAD_NOT_CLEAR')
   self.tool=a['mode'];return a
  if k=='contact.set':
   self._stopped();self._released()
   if a['engaged'] and (not self.power or self.emergency or not self.specimen or not self.stylus or not self.clamped or self.head_lift>1e-12 or self.tool!='stylus'):raise ActionRejected('CONTACT_NOT_READY')
   self.contact=a['engaged'];self.local_groove=0.;return a
  if k in ('specimen.load','stylus.load'):
   self._access()
   if not self.plant:raise ActionRejected('NOT_READY')
   if not a['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   name=k.split('.')[0]
   if getattr(self,name) is not None:raise ActionRejected('ALREADY_LOADED')
   if name=='specimen':
    rec=copy.deepcopy(library()['substances'].get(a['reference_id'],{}));x,mu,changes=curve(rec,a['accept_negative_force_projection']);self.reference=rec;self.ref_x=x;self.ref_mu=mu;self.projection_count=changes;self.prescan_records={};self.tracks=[];self.active_track=None;self.baseline=None;self.local_groove=0.;self.volumes['coating']=a['length_m']*a['width_m']*a['coating_thickness_m'];self.volumes['substrate']=a['length_m']*a['width_m']*a['substrate_thickness_m']
    for n in ['coating','substrate']:self.mass_loaded[n]+=self.volumes[n]*a[n+'_density_kg_m3']
   else:self.volumes['stylus']=a['volume_m3'];self.mass_loaded['stylus']+=a['volume_m3']*a['density_kg_m3']
   setattr(self,name,copy.deepcopy(a));return {'loaded':True}
  if k in ('specimen.remove','stylus.remove'):
   self._access();name=k.split('.')[0]
   if getattr(self,name) is None:raise ActionRejected('NOT_LOADED')
   for n in (['coating','substrate'] if name=='specimen' else ['stylus']):self.mass_recovered[n]+=self.volumes[n]*self._density(n);self.volumes[n]=0.
   setattr(self,name,None)
   if name=='specimen':self.reference=None;self.ref_x=np.array([]);self.ref_mu=np.array([]);self.local_groove=0.;self.prescan_records={};self.tracks=[]
   return {'removed':True}
  if k=='debris.collect':
   self._access()
   for n in self.debris:self.debris_recovered[n]+=self.debris[n];self.debris[n]=0.
   return {'collected':True}
  if k=='sensor.calibrate':self._stopped();self._released();self.calibration=copy.deepcopy(a);return a
  if k=='sensor.zero':
   self._stopped();self._released()
   if self.contact or not self.power:raise ActionRejected('ZERO_REQUIRES_NO_CONTACT')
   self.calibration.update(normal_zero_n=self.observed_force,friction_zero_n=self.observed_friction,depth_zero_m=self.observed_depth);return {'zeroed':True}
  if k=='method.configure':
   self._stopped();self._released()
   if a['start_x_m']+a['length_m']>.07+1e-12:raise ActionRejected('STAGE_PATH_RANGE')
   if a['final_force_n']<a['initial_force_n']:raise ActionRejected('DECREASING_LOAD_UNSUPPORTED')
   if abs(a['sample_interval_s']/DT-round(a['sample_interval_s']/DT))>1e-8:raise ActionRejected('SAMPLING_GRID')
   if a['pass_kind']=='prescan' and a['stop_on_criterion']:raise ActionRejected('CRITERION_PASS_MISMATCH')
   self.method=copy.deepcopy(a);return a
  if k=='acquisition.start':
   self._stopped();self._released();a=self.method
   if not self.power or self.emergency or not self.specimen or not self.stylus or not self.clamped or not self.contact or self.head_lift>1e-12 or self.tool!='stylus':raise ActionRejected('NOT_READY')
   if self.specimen['sample_wear_energy_fraction']+self.stylus['wear_energy_fraction']>1:raise ActionRejected('ENERGY_FRACTIONS')
   if self.volumes['substrate']<=0 or self.volumes['stylus']<=0:raise ActionRejected('MATERIAL_EXHAUSTED')
   if abs(self.stage_x-a['start_x_m'])>1e-10 or abs(self.stage_y-a['path_y_m'])>1e-10:raise ActionRejected('STAGE_NOT_AT_START')
   if a['pass_kind'] in ('scratch','prescan') and any(self._overlap(t,a) for t in self.tracks):raise ActionRejected('TRACK_ALREADY_DAMAGED')
   key=self._key();baseline=self.prescan_records.get(key);track=next((t for t in self.tracks if t['key']==key and t['distance']>0),None)
   if a['pass_kind'] in ('scratch','postscan'):
    if baseline is None:raise ActionRejected('PRESCAN_REQUIRED')
    if baseline['calibration']!=self.calibration:raise ActionRejected('BASELINE_CALIBRATION_MISMATCH')
    if baseline['scan_force_n']!=a['scan_force_n']:raise ActionRejected('BASELINE_LOAD_MISMATCH')
   if a['pass_kind']=='postscan' and track is None:raise ActionRejected('NO_SCRATCH_TRACK')
   if a['pass_kind']=='scratch':
    self._conditions()
    if a['source_start_distance_m']<self.ref_x[0]-1e-14 or a['source_start_distance_m']+a['length_m']>self.ref_x[-1]+1e-14:raise ActionRejected('REFERENCE_DISTANCE_RANGE')
   self._counter+=1;self.operation_id=self.instance_id+':'+str(self._counter);self.operation_start=self.time_s;self.operation_method=json.dumps(a,sort_keys=True);self.operation_reference=self.specimen['reference_id'];self.operation_source=json.dumps(self.reference.get('source'),ensure_ascii=False);self.operation_projection=self.projection_count;self.operation_kind=a['pass_kind'];self.operation_evidence='Empirical Ft divided by source FnC command is a derived_nominal_load_ratio proxy, not measured source true COF. Negative Ft source nodes retained in library and explicitly projected to0 only in nonnegative dissipative proxy; modified nodes='+str(self.projection_count)+'. No normalized length axis: source distance=start+actual travel. Source Pd/zero Rd are reference only; this process depth is engineering elastic+finite-removal groove, postscan requires same-path measured baseline. Finite energy-partition wear and thermal parameters are assumed; full normal plastic/topographic work not solved. No empirical noise/accuracy, acoustic emission or native microscope image claim.';self.phase='running';self.pass_phase='loading';self.distance=0.;self.elapsed=0.;self.rows=[];self.observed_work=0.;self.error=None;self.local_groove=0.;self.baseline=baseline;self.active_track=track
   if a['pass_kind']=='scratch':self.active_track=dict(key=key,start=a['start_x_m'],y=a['path_y_m'],distance=0.,segments=[],observed_rows=[]);self.tracks.append(self.active_track)
   self.force_target=a['initial_force_n'] if a['pass_kind']=='scratch' else a['scan_force_n'];self._record();return {'operation_id':self.operation_id}
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self._finish('cancelled');return {'phase':'cancelled'}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _thermal_endpoint(self,dt,heat):
  p=self.plant;c=p['thermal_capacity_j_k'];g=p['heat_loss_w_k'];a=p['ambient_k']
  if dt<=0:return self.temperature
  if g==0:return self.temperature+heat/c
  decay=math.exp(-g*dt/c);return a+(self.temperature-a)*decay+heat/(g*dt)*(-math.expm1(-g*dt/c))
 def _set_force(self,value,release=False):
  if release and value<=self.plant['force_release_n']:value=0.
  before=self.force*self.force/(2*self.plant['contact_stiffness_n_m']);after=value*value/(2*self.plant['contact_stiffness_n_m']);change=after-before
  if change>=0:self.normal_loading+=change
  else:self.normal_recovered-=change
  self.force=value
 def _profile(self,distance):
  if not self.active_track:return 0.
  for seg in self.active_track['segments']:
   if seg['x0']-1e-12<=distance<=seg['x1']+1e-12:return seg['depth_m']
  return 0.
 def _predict(self,dt,scanning):
  a=self.method;p=self.plant;v=a['speed_m_s'] if scanning else 0.;ds=v*dt;scratch=scanning and self.operation_kind=='scratch';slope=(a['final_force_n']-a['initial_force_n'])/a['length_m']*v if scratch else 0.;f1,i0,i1=force_ramp(self.force,self.force_target,slope,p['force_response_tau_s'],dt)
  if scratch:
   x=a['source_start_distance_m']+self.distance;m0=float(np.interp(x,self.ref_x,self.ref_mu));m1=float(np.interp(x+ds,self.ref_x,self.ref_mu));gradient=(m1-m0)/dt if dt>0 else 0.;energy=max(0.,v*(m0*i0+gradient*i1));friction_end=m1*f1
  elif scanning:energy=p['scan_friction_coefficient']*v*i0;friction_end=p['scan_friction_coefficient']*f1
  else:energy=0.;friction_end=0.
  removed={n:0. for n in self.volumes};wear=0.;groove=self._profile(self.distance+ds) if scanning and self.operation_kind=='postscan' else self.local_groove if not scanning else 0.
  if scratch and ds>0:
   sample_energy=self.specimen['sample_wear_energy_fraction']*energy;uc=self.specimen['coating_removal_energy_j_m3'];ub=self.specimen['substrate_removal_energy_j_m3'];coating_local_cap=self.specimen['track_width_m']*self.specimen['coating_thickness_m']*ds;removed['coating']=min(coating_local_cap,sample_energy/uc);removed['substrate']=max(0.,sample_energy-removed['coating']*uc)/ub;removed['stylus']=self.stylus['wear_energy_fraction']*energy/self.stylus['removal_energy_j_m3'];wear=sample_energy+self.stylus['wear_energy_fraction']*energy;groove=(removed['coating']+removed['substrate'])/(self.specimen['track_width_m']*ds)
  heat=energy-wear;t1=self._thermal_endpoint(dt,heat);depth=f1/p['contact_stiffness_n_m']+groove;reason=None
  for n,vrem in removed.items():
   if vrem>self.volumes[n]:reason=n.upper()+'_EXHAUSTED'
  if scratch and groove>self.specimen['coating_thickness_m']+self.specimen['substrate_thickness_m']:reason='LOCAL_SPECIMEN_THICKNESS'
  if depth>DEPTH_MAX:reason='DEPTH_RANGE'
  if friction_end>FORCE_MAX:reason='FRICTION_FORCE_RANGE'
  if t1>p['temperature_max_k']:reason='THERMAL_MODEL_RANGE'
  return dict(dt=dt,ds=ds,force=f1,force_integral=i0,target=self.force_target+slope*dt,energy=energy,wear=wear,heat=heat,temperature=t1,removed=removed,groove=groove,friction_end=friction_end,reason=reason)
 def _sensors(self,dt,normal,friction,depth,ds=0.):
  if not self.plant or dt<=0:return
  p=self.plant;one=-math.expm1(-dt/p['sensor_tau_s']);target=friction*p['friction_gain']+p['friction_offset_n'];integral=target*dt+(self.observed_friction-target)*p['sensor_tau_s']*one;self.observed_friction+=one*(target-self.observed_friction);self.observed_force+=one*(normal*p['force_gain']+p['force_offset_n']-self.observed_force);self.observed_depth+=one*(depth*p['depth_gain']+p['depth_offset_m']-self.observed_depth)
  if ds:self.observed_work+=(integral-self.calibration['friction_zero_n']*dt)/self.calibration['friction_gain_correction']*ds/dt
 def _advance(self,dt,scanning):
  prediction=self._predict(dt,scanning);reason=prediction['reason']
  if reason:
   lo=0.;hi=dt
   for _ in range(45):
    mid=(lo+hi)/2
    if self._predict(mid,scanning)['reason'] is None:lo=mid
    else:hi=mid
   prediction=self._predict(lo,scanning);dt=lo
  d=prediction
  if dt>0:
   old_t=self.temperature;self.tangential_work+=d['energy'];self.wear_work+=d['wear'];self.heat_input+=d['heat'];self.temperature=d['temperature'];self.ambient_loss+=d['heat']-self.plant['thermal_capacity_j_k']*(self.temperature-old_t);self._set_force(d['force'],release=self.force_target==0);self.force_target=d['target']
   if scanning:
    start=self.distance;self.distance=min(self.method['length_m'],self.distance+d['ds']);self.stage_x=self.method['start_x_m']+self.distance;self.local_groove=d['groove']
    for n,volume in d['removed'].items():self.volumes[n]-=volume;self.removed_volumes[n]+=volume;self.debris[n]+=volume*self._density(n)
    if self.operation_kind=='scratch':self.active_track['segments'].append(dict(x0=start,x1=self.distance,depth_m=self.local_groove));self.active_track['distance']=self.distance
   raw_depth=self._depth()-self.specimen['surface_slope_x']*self.stage_x if self.contact and self.specimen else 0.;friction=d['energy']/d['ds'] if d['ds']>0 else 0.;self._sensors(dt,d['force_integral']/dt,friction,raw_depth,d['ds']);self.time_s+=dt;self.elapsed=self.time_s-self.operation_start
  if not reason and self._indicated()['reason']=='SENSOR_OVER_RANGE':reason='SENSOR_OVER_RANGE'
  if reason:self._finish('failed',reason)
 def _record(self):
  if self.rows and abs(self.rows[-1]['time_s']-self.time_s)<1e-12 and self.rows[-1]['phase']==self.pass_phase:return
  v=self._indicated();self.rows.append(dict(time_s=self.time_s,elapsed_s=self.time_s-self.operation_start,phase=self.pass_phase,distance_m=self.distance,stage_x_m=self.stage_x,normal_target_n=self.force_target,normal_force_n=v['normal'],friction_force_n=v['friction'],friction_coefficient=v['mu'],raw_depth_m=v['raw'],penetration_depth_m=v['penetration'],residual_depth_m=v['residual'],temperature_k=self.temperature if self.power else None,observed_tangential_work_j=self.observed_work,unavailable_reason=v['reason']))
 def _finish(self,phase,error=None):
  self.elapsed=self.time_s-self.operation_start;self._record();self.phase=phase;self.error=error;self.target_x=self.stage_x;self.target_y=self.stage_y;self.force_target=0.
  if self.operation_kind=='scratch' and self.active_track:self.active_track['observed_rows']=copy.deepcopy([r for r in self.rows if r['phase']=='scanning'])
  if phase=='succeeded' and self.operation_kind=='prescan':
   rows=[r for r in self.rows if r['phase']=='scanning' and r['raw_depth_m'] is not None];self.prescan_records[self._key()]=dict(distance=[r['distance_m'] for r in rows],depth=[r['raw_depth_m'] for r in rows],scan_force_n=self.method['scan_force_n'],calibration=copy.deepcopy(self.calibration))
  self.records[self.operation_id]=self._result();self.pass_phase='idle'
 def _idle(self,dt):
  if self.plant:
   f,i0,_=force_ramp(self.force,0.,0.,self.plant['force_response_tau_s'],dt);self._set_force(f,release=True);self.force_target=0.;old=self.temperature;self.temperature=self._thermal_endpoint(dt,0.);self.ambient_loss+=self.plant['thermal_capacity_j_k']*(old-self.temperature)
   if self.power:self._sensors(dt,i0/dt,0.,self._depth()-self.specimen['surface_slope_x']*self.stage_x if self.contact and self.specimen else 0.)
   if self.power and not self.emergency:
    dx=self.target_x-self.stage_x;dy=self.target_y-self.stage_y;distance=math.hypot(dx,dy)
    if distance:scale=min(1.,self.stage_speed*dt/distance);self.stage_x+=scale*dx;self.stage_y+=scale*dy
    self.head_lift+=max(-self.plant['head_speed_m_s']*dt,min(self.plant['head_speed_m_s']*dt,self.head_target-self.head_lift))
  self.time_s+=dt
 def _tick(self):
  self.time_s=(self._ticks-1)*DT;end=self._ticks*DT
  while self.time_s<end-1e-12:
   if self.phase!='running':self._idle(end-self.time_s);break
   dt=min(.01,end-self.time_s,self.method['timeout_s']-(self.time_s-self.operation_start));before=self.time_s
   if dt<=1e-13:self._finish('failed','TIMEOUT');continue
   scanning=self.pass_phase=='scanning'
   if scanning:
    remaining=self.method['length_m']-self.distance
    if remaining<=1e-13:self._record();self.pass_phase='unloading';self.force_target=0.;continue
    dt=min(dt,remaining/self.method['speed_m_s'])
    if self.operation_kind=='scratch':
     coordinate=self.method['source_start_distance_m']+self.distance;idx=int(np.searchsorted(self.ref_x,coordinate+1e-13,side='right'))
     if idx<len(self.ref_x):dt=min(dt,float(self.ref_x[idx]-coordinate)/self.method['speed_m_s'])
   self._advance(dt,scanning)
   if self.phase!='running':continue
   if self.pass_phase=='loading' and abs(self.force-self.force_target)<=self.plant['force_settle_tolerance_n']:self.pass_phase='scanning';self.target_x=self.method['start_x_m']+self.method['length_m'];self._record()
   elif self.pass_phase=='unloading' and self.force==0:self._finish('succeeded')
   if self.phase=='running' and self.time_s<=before+1e-15:raise RuntimeError('No progress in scratch integration')
  self.time_s=end
  if self.phase=='running' and (self._ticks-round(self.operation_start/DT))%round(self.method['sample_interval_s']/DT)==0:
   self._record()
   if self.method['stop_on_criterion'] and self._result()['critical']['position_m'] is not None:self.pass_phase='unloading';self.force_target=0.;self.target_x=self.stage_x
 def step(self,dt_s):
  validate(number('s',0,3600),dt_s);total=self._remainder+dt_s;n=int((total+1e-12)/DT);self._remainder=total-n*DT
  for _ in range(n):self._ticks+=1;self._tick()
  self.revision+=n

class AntonpaarRst300Session:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,instance_id):
  if instance_id!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  meta=export_antonpaar_rst300_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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

# Honest schema example: externally supplied observations, not a generated failure trace.
_example_row={k:(None if 'anyOf' in schema else '' if schema.get('type')=='string' else 0.) for k,schema in ROW['properties'].items()};_example_row.update(phase='scanning',normal_force_n=10.,friction_force_n=1.,distance_m=0.,stage_x_m=0.,normal_target_n=10.,temperature_k=296.15)
CAPS['analysis.critical_load']['example_arguments']=dict(rows=[_example_row],load_rows=[],channel='friction_force_n',threshold_n=2.,threshold_m=5e-6,threshold_fraction=.2,persistence=1)
EFFECTS={
'plant.configure':['plant.configured','truth.temperature_k'],
'specimen.load':['specimen.present','truth.coating_volume_m3','truth.substrate_volume_m3','truth.local_groove_m','head.penetration_m'],
'specimen.remove':['specimen.present','truth.coating_volume_m3','truth.substrate_volume_m3','truth.local_groove_m','head.penetration_m'],
'stylus.load':['stylus.present','truth.stylus_volume_m3'],'stylus.remove':['stylus.present','truth.stylus_volume_m3'],
'power.set':['power.enabled','power.angle_rad','load.indicated_n','friction.indicated_n','friction.coefficient','depth.raw_m','depth.penetration_m','depth.residual_m','thermal.indicated_k','observation.unavailable_reason'],
'clamp.set':['clamp.locked','clamp.release_m'],'stage.move':['stage.target_x_m','stage.target_y_m','stage.moving'],'head.set_position':['head.target_lift_m','head.moving'],'tool.select':['tool.mode'],
'contact.set':['contact.engaged','head.penetration_m','truth.local_groove_m','depth.raw_m','depth.penetration_m','depth.residual_m','friction.indicated_n','friction.coefficient','observation.unavailable_reason'],
'sensor.calibrate':['load.indicated_n','friction.indicated_n','friction.coefficient','depth.raw_m','depth.penetration_m','depth.residual_m','observation.unavailable_reason'],
'sensor.zero':['load.indicated_n','friction.indicated_n','friction.coefficient','depth.raw_m','observation.unavailable_reason'],
'acquisition.start':['acquisition.phase','acquisition.pass_phase','acquisition.distance_m','acquisition.elapsed_s','acquisition.points','load.target_n','truth.local_groove_m','head.penetration_m','friction.indicated_n','friction.coefficient','depth.penetration_m','depth.residual_m','observation.unavailable_reason'],
'acquisition.stop':['acquisition.phase','acquisition.pass_phase','acquisition.elapsed_s','acquisition.points','stage.target_x_m','stage.target_y_m','stage.moving','load.target_n','friction.indicated_n','friction.coefficient','depth.penetration_m','depth.residual_m','observation.unavailable_reason'],
'emergency.stop':['emergency.latched','acquisition.phase','acquisition.pass_phase','acquisition.elapsed_s','acquisition.points','stage.target_x_m','stage.target_y_m','stage.moving','head.target_lift_m','head.moving','load.target_n','friction.indicated_n','friction.coefficient','depth.penetration_m','depth.residual_m','observation.unavailable_reason'],
'emergency.reset':['emergency.latched'],'debris.collect':['truth.debris_kg'],'sim.step':list(SLOTS)}
for k,v in EFFECTS.items():CAPS[k]['effects']=v
PHYSICS_EFFECTS=['stage.x_m','stage.y_m','stage.moving','head.lift_m','head.moving','head.penetration_m','load.target_n','load.indicated_n','friction.indicated_n','friction.coefficient','depth.raw_m','depth.penetration_m','depth.residual_m','thermal.indicated_k','truth.normal_force_n','truth.coating_volume_m3','truth.substrate_volume_m3','truth.stylus_volume_m3','truth.debris_kg','truth.local_groove_m','truth.temperature_k','truth.tangential_work_j','acquisition.phase','acquisition.pass_phase','acquisition.distance_m','acquisition.elapsed_s','acquisition.points','observation.unavailable_reason','stage.target_x_m','stage.target_y_m']

def export_antonpaar_rst300_contract_metadata():
 slots={};caps={}
 for k,v in SLOTS.items():slots[k]=dict(meaning_zh=v['meaning'],component_ids=[v['component']],value_schema=copy.deepcopy(v['schema']),unit=v['unit'],reference_conditions='SI; high-force200N/1mm range. Downward penetration positive; stageX0..70mm. Empirical Ft/FnC uses original command-load channel, not measured true coefficient. Coarse motion/ideal geometry do not solve deformation contact.',origin=v['origin'],visibility=v['visibility'],access='read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'AntonpaarRst300Session.read(state_ids=[{k!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description='Host0.1s ticks, internal<=10ms plus exact source knots. Rows include explicit loading/scanning/unloading boundary events, so event row spacing need not equal requested sampling interval.'),source_ids=['simulation_assumption'])
 errors=['UNKNOWN_CAPABILITY','UNKNOWN_INSTANCE','UNKNOWN_OPERATION','INVALID_ARGUMENT','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','BUSY','NOT_RUNNING','NOT_READY','INITIALIZATION_ONLY','MODEL_NOT_ACCEPTED','PLANT_CONFIGURATION','LOAD_NOT_RELEASED','HEAD_NOT_CLEAR','CLAMP_LOCKED','NO_SPECIMEN','HOT_SPECIMEN','CONTACT_ENGAGED','NO_CLAMPED_SPECIMEN','CONTACT_NOT_READY','ALREADY_LOADED','NOT_LOADED','INVALID_REFERENCE','NEGATIVE_SOURCE_FORCE','ZERO_REQUIRES_NO_CONTACT','STAGE_PATH_RANGE','DECREASING_LOAD_UNSUPPORTED','SAMPLING_GRID','CRITERION_PASS_MISMATCH','ENERGY_FRACTIONS','MATERIAL_EXHAUSTED','STAGE_NOT_AT_START','TRACK_ALREADY_DAMAGED','PRESCAN_REQUIRED','BASELINE_CALIBRATION_MISMATCH','BASELINE_LOAD_MISMATCH','NO_SCRATCH_TRACK','REFERENCE_DISTANCE_RANGE','UNKNOWN_REFERENCE_CONDITIONS','REFERENCE_CONDITIONS','COATING_EXHAUSTED','SUBSTRATE_EXHAUSTED','STYLUS_EXHAUSTED','LOCAL_SPECIMEN_THICKNESS','DEPTH_RANGE','FRICTION_FORCE_RANGE','THERMAL_MODEL_RANGE','SENSOR_OVER_RANGE','SOFTWARE_STOP','TIMEOUT']
 for k,c in CAPS.items():
  un=k in UNSUPPORTED;async_=k=='acquisition.start';caps[k]={key:copy.deepcopy(c[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']};caps[k].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ['scene','robot','hardware'] else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.antonpaar_rst300.AntonpaarRst300Session.invoke',invocation=f'session.invoke(instance_id="rst300_01",capability_id={k!r},arguments={c["example_arguments"]!r},request_id="unique")'),preconditions=[] if c['kind'] in ['query','analysis'] or un else [dict(description_zh='抬头/夹具/有限试片压头、已释放载荷、同路径预扫描与未损伤轨道、源工况/绝对坐标域及温区；拒绝原子',state_ids=['power.enabled','clamp.locked','contact.engaged','head.lift_m','specimen.present','stylus.present','acquisition.phase'],enforced_by='runtime',check='atomic invoke and registered Session')],state_effects=[dict(state_id=x,timing='immediate',description_zh='真实命令改变的状态') for x in c['effects']],observes=[],model_effects=dict(mode='none',description_zh='几何耦合由独立场景验证；刚性试片不会实际刻槽，微压入是显式姿态代理',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='实际加载、划程和卸载完成；失败/取消保留有限磨耗和载荷残留' if async_ else '命令不隐式推进物理时间；运动目标等待宿主step',completion_state_ids=['acquisition.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','specimen_track','force_controller','stylus','sensors'],partial_failure_zh='保留碎屑、槽深、热和剩余载荷；取消后按宿主时间卸载，不免费回填轨道'),errors=[dict(code=x,meaning_zh=x,side_effects='partial' if async_ and x in ['COATING_EXHAUSTED','SUBSTRATE_EXHAUSTED','STYLUS_EXHAUSTED','LOCAL_SPECIMEN_THICKNESS','DEPTH_RANGE','FRICTION_FORCE_RANGE','THERMAL_MODEL_RANGE','SENSOR_OVER_RANGE','SOFTWARE_STOP','TIMEOUT'] else 'none') for x in errors],verification=[],limitations=['高量程200N/1mm，不偷换精细量程；标称分辨率不等于准确度/噪声验证','裸材源响应向涂层/变速/递增加载迁移需显式接受工程假设','已损伤路径拒绝再划，multipass/crosscan未实现'],source_ids=['manufacturer_specs','simulation_assumption'])
  if k in ['acquisition.start','acquisition.stop','emergency.stop','stage.move','head.set_position','power.set']:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='宿主推进加载/移动/观测/有限移除/热') for x in PHYSICS_EFFECTS]
 mechanism=dict(component_ids=['load_head','stage_x','stage_y','stylus','specimen','sample_holder','controller'],state_ids=list(slots),meaning_zh='实源切向响应与有限能量分配划痕、独立观察和同道前后扫描',implementation='lumped',equations=[
 'Fn_target(x)=Fstart+(Fend-Fstart)*x/L for progressive or constant load. dFn/dt=(Fn_target-Fn)/tau. Exact linear-target solution and two integral moments; hostfixed0.1s with <=10ms internal and source-knot events. High range Fn,Ft<=200N, depth<=1mm, speed0.4..600mm/min and length<=70mm.',
 'mu_proxy(x)=max(Ft_source(x),0)/FnC_source(x) at source nodes, piecewise linear. FnC is the source command/control channel, not measured normal load. Source original Ft/Pd/Rd/mu arrays are preserved; negative projection requires explicit acceptance and result modification count. x_source=source_start+actual travel; no axis normalization or extrapolation.',
 'W_t=integral Fn_actual(t)*mu_proxy(x(t))*v dt exactly on source-linear segments with analytic exponential force moments. Measured friction channel has independent gain/offset/tau; observed mu=Fobs/Nobs. Source native mu is not asserted to equal the derived nominal-load ratio.',
 'Explicit energy allocation eta_sample+eta_stylus<=1. For each new path increment, coating removal capped by width*thickness*dx then substrate removal, with dV=dW_removal/u. Stylus dV=eta_stylus*dW_t/u_stylus. Each density transfers removed mass to separate debris. Unallocated work enters finite heat node, C*dT/dt=Pheat-G*(T-Tambient).',
 'Ideal normal spring U=Fn^2/(2k). Positive deltaU tracked as loading work, negative deltaU as recovered actuator work; no normal energy disappears on unloading. Full normal plastic/topographic work is not solved and is not relabeled as tangential heat.',
 'Local average groove=(dVcoat+dVsubstrate)/(declared width*dx), stored along the exact finite damaged path. Raw depth=Fn/k+local_groove-surface_slope*stageX, through independent sensor response. Prescan and postscan use explicitly assumed nondestructive low force. Penetration and residual observations subtract the same-path prescan; never treat source zero Rd as measured zero residual.',
 'Critical threshold detection requires adjacent available observations and consecutive confirmation; missing points break a bracket. It reports an observed criterion, not intrinsic adhesion. Postscan residual crossings map to preserved original same-track scratch observedFn(position), never the low probing force; without load bracket Lc=null.',
 'Damaged overlapping tracks cannot be reused; arbitrary multipass does not reset finite coating. Cancellation stops feed and freezes history but actual normal load relaxes on later steps. Fixed optical microscope has no simulated carousel; published image lookup is reference-only, dynamic panorama and AE calibration are unsupported.'
 ],parameters={},source_ids=['manufacturer_specs','simulation_assumption','brittle_scratch_2018'],limitations=['All depth/removal/thermal/force-control parameters are explicit engineering inputs; original Pd/Rd are not silently promoted to model truth.','High-range nominal0.2mN/.5nm resolutions and ideal-laboratory1mN/2.5nm RMS floors are specifications only; no achieved metrology or stochastic noise claim.','Default empirical record is bare borofloat glass from another scratch experiment, not native RST300 coating force/depth calibration.','Image references do not generate a new scratch image. Manufacturer panorama inspection limit30mm and scratch travel70mm are different; automatic panorama acquisition not implemented.','No manufacturer heater is invented. Heat comes only from declared portion of tangential work; passive cooling, no heating-control capability.','Normal plastic deformation/topographic work, real fracture/delamination, pile-up shape, AE waveform and actual indentation contact dynamics are not solved.','Observed postscan residual profile uses ideal nondestructive probe and explicit constant track width; not full Hertz/spheroconical cross-section inversion.','ASTM C1624 scope or CRM critical loads do not calibrate synthetic wear coefficients or confer certification.'])
 caps['reference.query']['source_ids'].append('brittle_scratch_2018');caps['acquisition.start']['source_ids'].append('brittle_scratch_2018')
 spec=importlib.util.spec_from_file_location('_rst_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()});ann.annotate_export(SLUG,caps,slots,{'scratch':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'specimen.load':(SAMPLE,SAMPLE_EXAMPLE),'stylus.load':(STYLUS,STYLUS_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'scratch':mechanism})

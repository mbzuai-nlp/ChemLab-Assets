"""CHEM130 bounded empirical indentation, independent observations and Oliver–Pharr analysis."""
from pathlib import Path
import copy,json,math,importlib.util
import numpy as np
PROTOCOL_VERSION='1.0.0';SLUG='antonpaar_hit300';CATALOG_ID='CHEM-130';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch04/t1_thermal'/SLUG
DT=.1;FORCE_MAX=.5;DEPTH_MAX=.0002;STAGE_MAX=.04;HEAD_MAX=.09
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
 p=ASSET/'indentation_library.json';return json.loads(p.read_text()) if p.exists() else {'substances':{}}

def fit_unloading(load_n,depth_m,minimum_fraction=.2,maximum_fraction=.95):
 """P=a*((h-hf)/hscale)^m on an explicit positive-load unloading window."""
 from scipy.optimize import least_squares
 p=np.asarray(load_n,float);h=np.asarray(depth_m,float)
 if p.ndim!=1 or p.shape!=h.shape or len(p)<6 or not np.all(np.isfinite(p)) or not np.all(np.isfinite(h)) or max(p)<=0:raise ActionRejected('FIT_DATA')
 pmax=float(max(p));peak_index=int(np.argmax(p));hpeak=float(h[peak_index])
 if p[0]<.98*pmax or np.any(np.diff(p)>.02*pmax) or np.median(np.diff(p))>0:raise ActionRejected('NOT_UNLOADING')
 if max(h)>hpeak+max(1e-12,.02*abs(hpeak)):raise ActionRejected('UNLOADING_NOSE')
 mask=(p>=minimum_fraction*pmax)&(p<=maximum_fraction*pmax)
 if np.count_nonzero(mask)<6:raise ActionRejected('FIT_POINTS')
 x=h[mask];y=p[mask]/pmax;scale=float(max(h)-min(h))
 if scale<=1e-14 or np.ptp(x)<=1e-14 or np.ptp(y)<=1e-8:raise ActionRejected('FIT_DOMAIN')
 origin=float(min(h));u=(x-origin)/scale;upper=float(min(u))-1e-9
 def residual(q):return q[0]*np.maximum(u-q[1],1e-15)**q[2]-y
 # Multistart prevents a silently accepted local minimum for shallow unloading arcs.
 candidates=[]
 for m in [1.1,1.5,2.,2.8]:
  hf=min(upper-.05,-.05);a=float(np.median(y/np.maximum(u-hf,1e-12)**m));q=least_squares(residual,[max(.000001,min(a,1e5)),max(-99.,hf),m],bounds=([1e-8,-100.,1.],[1e6,upper,3.]),xtol=1e-12,ftol=1e-12,gtol=1e-12,max_nfev=10000);candidates.append(q)
 q=min(candidates,key=lambda q:float(np.dot(q.fun,q.fun)));a,hf,m=map(float,q.x);hmax=hpeak;offset=origin+hf*scale;factor=pmax*a/scale**m;stiffness=factor*m*(hmax-offset)**(m-1);rms=float(np.sqrt(np.mean(q.fun*q.fun)))*pmax
 if not q.success or not all(math.isfinite(v) for v in [offset,m,stiffness,factor]) or stiffness<=0:raise ActionRejected('FIT_FAILED')
 return dict(coefficient_n_m_power=factor,exponent=m,residual_depth_m=offset,stiffness_n_m=stiffness,fit_rms_n=rms,fit_points=int(mask.sum()),max_load_n=pmax,max_depth_m=hmax)

CALIBRATION_EXAMPLE=dict(force_gain_correction=1.,force_zero_n=0.,depth_gain_correction=1.,depth_zero_m=0.,frame_compliance_m_n=0.,area_c2=24.5,area_c1_m=0.,area_c0_m2=0.,area_min_depth_m=1e-9,area_max_depth_m=.0002,epsilon=.75,beta=1.034,poisson_sample=.22,young_tip_pa=1.14e12,poisson_tip=.07,fit_min_fraction=.2,fit_max_fraction=.95,accept_elastic_unloading=True)
CALIBRATION=obj({'force_gain_correction':number('1',.01,100),'force_zero_n':number('N',-.05,.05),'depth_gain_correction':number('1',.01,100),'depth_zero_m':number('m',-.0002,.0002),'frame_compliance_m_n':number('m/N',0,.001),'area_c2':number('1',.1,1000),'area_c1_m':number('m',0,.001),'area_c0_m2':number('m^2',0,1e-6),'area_min_depth_m':number('m',1e-12,.0002),'area_max_depth_m':number('m',1e-12,.0002),'epsilon':number('1',.1,1.),'beta':number('1',.5,2.),'poisson_sample':number('1',0,.499),'young_tip_pa':number('Pa',1e9,2e12),'poisson_tip':number('1',0,.499),'fit_min_fraction':number('1',.001,.9),'fit_max_fraction':number('1',.1,1.),'accept_elastic_unloading':BOOL})
OP_RESULT=obj({'peak_load_n':number('N',0),'max_depth_m':number('m'),'contact_depth_m':number('m',0),'contact_area_m2':number('m^2',0),'stiffness_n_m':number('N/m',0),'hardness_pa':number('Pa',0),'reduced_modulus_pa':number('Pa',0),'sample_modulus_pa':number('Pa',0),'unloading_exponent':number('1',1,3),'fit_residual_depth_m':number('m'),'fit_rms_n':number('N',0),'fit_points':integer(6,1000000),'evidence':TEXT})
def oliver_pharr(load_n,depth_m,calibration):
 """External OBSERVED unloading arrays. Correct frame displacement exactly once."""
 validate(CALIBRATION,calibration);c=calibration
 if not c['accept_elastic_unloading']:raise ActionRejected('ELASTIC_UNLOADING_NOT_ACCEPTED')
 if c['fit_min_fraction']>=c['fit_max_fraction'] or c['area_min_depth_m']>=c['area_max_depth_m']:raise ActionRejected('CALIBRATION_DOMAIN')
 p=np.asarray(load_n,float);h=np.asarray(depth_m,float)
 if p.shape!=h.shape:raise ActionRejected('FIT_DATA')
 # Sensor corrections have already been applied by the observation layer; do not repeat them.
 h=h-c['frame_compliance_m_n']*p
 fit=fit_unloading(p,h,c['fit_min_fraction'],c['fit_max_fraction']);pmax=fit['max_load_n'];hmax=fit['max_depth_m'];s=fit['stiffness_n_m'];hc=hmax-c['epsilon']*pmax/s
 if not c['area_min_depth_m']<=hc<=c['area_max_depth_m']:raise ActionRejected('CONTACT_AREA_DOMAIN')
 area=c['area_c2']*hc*hc+c['area_c1_m']*hc+c['area_c0_m2'];er=math.sqrt(math.pi)*s/(2*c['beta']*math.sqrt(area));inverse=1/er-(1-c['poisson_tip']**2)/c['young_tip_pa']
 if inverse<=0:raise ActionRejected('MODULUS_DOMAIN')
 return dict(peak_load_n=pmax,max_depth_m=hmax,contact_depth_m=hc,contact_area_m2=area,stiffness_n_m=s,hardness_pa=pmax/area,reduced_modulus_pa=er,sample_modulus_pa=(1-c['poisson_sample']**2)/inverse,unloading_exponent=fit['exponent'],fit_residual_depth_m=fit['residual_depth_m'],fit_rms_n=fit['fit_rms_n'],fit_points=fit['fit_points'],evidence='Oliver–Pharr analysis of supplied observed unloading; h_corrected=h_observed-Cf*P_observed once. Explicit area/epsilon/beta/tip/Poisson inputs; homogeneous elastic unloading assumed, no true sample H/E read or certification.')

PLANT_EXAMPLE=dict(accept_engineering=True,ambient_k=296.15,thermal_capacity_j_k=10.,heat_loss_w_k=.1,temperature_max_k=333.15,safe_access_temperature_k=313.15,force_response_tau_s=.01,force_release_n=1e-10,sensor_tau_s=.001,force_gain=1.,force_offset_n=0.,depth_gain=1.,depth_offset_m=0.,frame_compliance_m_n=0.,common_drift_m_s=0.,differential_drift_m_s=0.,head_speed_m_s=.01,stage_speed_m_s=.005,array_clearance_m=.002,plastic_heat_fraction=1.)
PLANT=obj({'accept_engineering':BOOL,'ambient_k':number('K',273.15,323.15),'thermal_capacity_j_k':number('J/K',1e-6,1e6),'heat_loss_w_k':number('W/K',0,1000),'temperature_max_k':number('K',273.15,373.15),'safe_access_temperature_k':number('K',273.15,373.15),'force_response_tau_s':number('s',1e-5,10),'force_release_n':number('N',1e-14,1e-7),'sensor_tau_s':number('s',1e-5,10),'force_gain':number('1',.1,10),'force_offset_n':number('N',-.05,.05),'depth_gain':number('1',.1,10),'depth_offset_m':number('m',-.0002,.0002),'frame_compliance_m_n':number('m/N',0,.001),'common_drift_m_s':number('m/s',-1e-5,1e-5),'differential_drift_m_s':number('m/s',-1e-7,1e-7),'head_speed_m_s':number('m/s',1e-5,.05),'stage_speed_m_s':number('m/s',1e-5,.05),'array_clearance_m':number('m',.001,.01),'plastic_heat_fraction':number('1',0,1)})
SAMPLE_EXAMPLE=dict(reference_id='borofloat33_5mn_rep1',length_m=.05,width_m=.05,thickness_m=.005,density_kg_m3=2230.,damage_radius_m=5e-5,deformation_shape_factor=.3333333333333333,deformation_area_c2=24.5,accept_engineering=True,accept_projection=True,accept_unknown_conditions=True,accept_condition_transfer=True,accept_elastic_release_extrapolation=True)
SAMPLE=obj({'reference_id':TEXT,'length_m':number('m',.04,.1),'width_m':number('m',.04,.1),'thickness_m':number('m',1e-9,.02),'density_kg_m3':number('kg/m^3',100,25000),'damage_radius_m':number('m',1e-7,.005),'deformation_shape_factor':number('1',.01,10),'deformation_area_c2':number('1',1,1000),'accept_engineering':BOOL,'accept_projection':BOOL,'accept_unknown_conditions':BOOL,'accept_condition_transfer':BOOL,'accept_elastic_release_extrapolation':BOOL})
METHOD_EXAMPLE=dict(mode='quasi_static',control='load_rate',peak_load_n=.005,loading_rate_n_s=.1/60,hold_s=5.,unloading_rate_n_s=.1/60,x_positions_m=[.02,.022],sample_interval_s=.1,timeout_s=300.,accept_no_creep_hold=True)
METHOD=obj({'mode':enum(['quasi_static','dynamic']),'control':enum(['load_rate','strain_rate']),'peak_load_n':number('N',1e-7,.5),'loading_rate_n_s':number('N/s',1e-7,1.),'hold_s':number('s',0,3600),'unloading_rate_n_s':number('N/s',1e-7,1.),'x_positions_m':array(number('m',0,.04),1,100),'sample_interval_s':number('s',.1,60),'timeout_s':number('s',.1,20000),'accept_no_creep_hold':BOOL})
ROW=obj({'time_s':number('s',0),'elapsed_s':number('s',0),'indent_index':integer(0,99),'phase':enum(['loading','holding','unloading','release','retracting','moving','approaching']),'stage_x_m':number('m',0,.04),'stage_y_m':number('m',0,.04),'force_target_n':number('N',0,.5),'force_n':nullable(number('N')),'depth_m':nullable(number('m')),'tip_indicated_m':nullable(number('m')),'ring_indicated_m':nullable(number('m')),'temperature_k':nullable(number('K')),'unavailable_reason':nullable(TEXT)})
POINT_RESULT=obj({'index':integer(0,99),'x_m':number('m',0,.04),'y_m':number('m',0,.04),'phase':enum(['succeeded','failed','cancelled']),'analysis':nullable(OP_RESULT),'analysis_error':nullable(TEXT)})
RESULT=obj({'operation_id':nullable(TEXT),'phase':PHASE,'elapsed_s':number('s',0),'completed_indents':integer(0,100),'rows':array(ROW,0,250000),'points':array(POINT_RESULT,0,100),'reference_id':nullable(TEXT),'template_source':nullable(TEXT),'projection_json':nullable(TEXT),'method_json':nullable(TEXT),'evidence':TEXT,'error_code':nullable(TEXT)})
CAPS={};SLOTS={}
def cap(k,i,o,c,z,kind='device_command',visibility='agent',example=None):CAPS[k]=dict(input_schema=i,output_schema=o,component_ids=c,function_zh=z,kind=kind,visibility=visibility,example_arguments=copy.deepcopy(example or {}),effects=[])
def slot(k,s,u,z,origin='derived',visibility='agent',component='controller'):SLOTS[k]=dict(schema=s,unit=u,meaning=z,origin=origin,visibility=visibility,component=component,write=None)
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller'],'显式力伺服/独立双位移观测/集中热参数','configuration','evaluator',PLANT_EXAMPLE)
cap('specimen.load',SAMPLE,obj({'loaded':BOOL}),['specimen'],'装入有限体积试片与实测源响应代理','configuration','evaluator',SAMPLE_EXAMPLE)
cap('specimen.remove',obj({}),obj({'removed':BOOL}),['specimen'],'回收完整质量，塑性变形不伪装成磨屑','configuration','evaluator')
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_switch','controller'],'电源开关',example={'enabled':True})
cap('clamp.set',obj({'locked':BOOL}),obj({'locked':BOOL}),['clamp_jaw'],'抬头无载时手动夹紧',example={'locked':True})
cap('stage.move_x',obj({'position_m':number('m',0,.04),'speed_m_s':number('m/s',1e-5,.05)}),obj({'target_m':number('m',0,.04)}),['stage_x'],'电动X台位置目标，随宿主移动',example={'position_m':.02,'speed_m_s':.005})
cap('stage.set_y',obj({'position_m':number('m',0,.04),'accept_manual':BOOL}),obj({'position_m':number('m',0,.04)}),['stage_y'],'明确接受的手动Y定位代理，非自动二维阵列',example={'position_m':.02,'accept_manual':True})
cap('head.set_position',obj({'distance_m':number('m',0,.09)}),obj({'target_m':number('m',0,.09)}),['load_head','reference_ring'],'90mm粗Z行程目标，随宿主推进',example={'distance_m':0.})
cap('contact.set',obj({'engaged':BOOL}),obj({'engaged':BOOL}),['stylus','reference_ring'],'抬头到位后使能参考环接触',example={'engaged':True})
cap('laser.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['laser_x','laser_y'],'双激光瞄准可视状态，不产生测量',example={'enabled':True})
cap('sensor.calibrate',CALIBRATION,CALIBRATION,['controller','reference_ring'],'外部力/位移校正和OP面积/机架/弹性假设','configuration',example=CALIBRATION_EXAMPLE)
cap('sensor.zero',obj({}),obj({'zeroed':BOOL}),['controller','reference_ring'],'抬头无接触时由独立读数归零')
cap('method.configure',METHOD,METHOD,['controller'],'有界加载/保持/卸载和固定Y的X线阵','configuration',example=METHOD_EXAMPLE)
cap('acquisition.start',obj({}),obj({'operation_id':TEXT}),['controller','stylus','reference_ring','stage_x','load_head'],'异步逐点力伺服压入、卸载、抬头移动，永久保留压痕')
cap('acquisition.stop',obj({}),obj({'phase':enum(['cancelled'])}),['controller','stylus'],'停止新压入并冻结部分观测，剩余载荷随后释放')
cap('acquisition.result',obj({}),RESULT,['controller'],'owner限定历史结果与真实OP分析','query')
cap('reference.query',obj({'reference_id':TEXT}),obj({'reference_json':TEXT,'template_source':TEXT}),['controller'],'原P-h及来源，时间未知不伪造','query',example={'reference_id':SAMPLE_EXAMPLE['reference_id']})
cap('analysis.oliver_pharr',obj({'load_n':array(number('N',0,.5),6,1000000),'depth_m':array(number('m',-.0002,.0002),6,1000000),'calibration':CALIBRATION}),OP_RESULT,['controller'],'外部观测卸载拟合硬度、约化模量及样品模量','analysis',example={'load_n':[.005*(x/10)**1.5 for x in range(10,0,-1)],'depth_m':[1e-7+1e-7*x/10 for x in range(10,0,-1)],'calibration':CALIBRATION_EXAMPLE})
LEDGER=obj({k:number(u) for k,u in [('sample_loaded_kg','kg'),('sample_present_kg','kg'),('sample_recovered_kg','kg'),('sample_error_kg','kg'),('pristine_volume_m3','m^3'),('deformed_volume_m3','m^3'),('volume_error_m3','m^3'),('mechanical_input_j','J'),('elastic_recovered_j','J'),('elastic_stored_j','J'),('plastic_work_j','J'),('energy_error_j','J'),('heat_loss_j','J'),('thermal_stored_j','J'),('thermal_error_j','J'),('heat_input_j','J'),('defect_stored_j','J')]})
cap('ledger.read',obj({}),LEDGER,['controller'],'无材料凭空消失；塑性变形与弹性回收/热账本','query','evaluator')
cap('sim.step',obj({'dt_s':number('s',0,3600)}),obj({'time_s':number('s',0)}),['controller'],'唯一宿主时钟','simulation','evaluator',{'dt_s':.1})
cap('emergency.stop',obj({}),obj({'latched':BOOL}),['controller'],'软件停机锁存，非认证硬件急停')
cap('emergency.reset',obj({}),obj({'latched':BOOL}),['controller'],'实际载荷释放后明确复位')
UNSUPPORTED=['dynamic.acquire','strain_rate.control','analysis.viscoelastic','sample.repeat_indent','array.automatic_y','camera.capture','hardware.connect','noise.empirical','accuracy.certification','standard.certification','thermal.set_temperature']
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'未实现：'+k)
for k,z,c in [('plant.configured','工程模型已接受','controller'),('power.enabled','电源','power_switch'),('clamp.locked','夹具锁紧','clamp_jaw'),('contact.engaged','参考环接触使能','reference_ring'),('specimen.present','试片存在','specimen'),('laser.enabled','双激光瞄准使能','laser_x'),('emergency.latched','软件停机锁存','controller'),('stage.moving','电动X台移动中','stage_x'),('head.moving','粗Z移动中','load_head')]:slot(k,BOOL,'1',z,component=c)
for k,hi,z,c in [('stage.x_m',.04,'实际X台位置','stage_x'),('stage.y_m',.04,'手动Y台位置','stage_y'),('head.lift_m',.09,'粗Z抬升','load_head'),('head.penetration_m',.0002,'工程压入姿态，非物理网格变形','stylus'),('clamp.release_m',.003,'夹具释放代理','clamp_jaw')]:slot(k,number('m',0,hi),'m',z,'scene',component=c)
slot('power.angle_rad',number('rad',0,.28),'rad','电源开关代理','scene',component='power_switch')
for k,u,hi,z,c in [('stage.target_x_m','m',.04,'X台目标','stage_x'),('head.target_lift_m','m',.09,'粗Z目标','load_head'),('force.target_n','N',.5,'控制目标，不是实际力','load_head')]:slot(k,number(u,0,hi),u,z,'setpoint',component=c)
for k,u,z,c in [('force.indicated_n','N','独立力读数','controller'),('depth.indicated_m','m','tip减reference的独立深度读数','controller'),('tip.indicated_m','m','带共同漂移的独立tip通道','stylus'),('ring.indicated_m','m','独立参考环位移通道','reference_ring'),('thermal.indicated_k','K','集中温度理想代理，非已校准传感器','controller')]:slot(k,nullable(number(u)),u,z,component=c)
for k,u,z in [('truth.force_n','N','有限一阶实际载荷'),('truth.plastic_depth_m','m','当前点持久塑性深度'),('truth.elastic_depth_m','m','当前非线性弹簧位移'),('truth.deformed_volume_m3','m^3','总持久变形区体积'),('truth.sample_mass_kg','kg','试片完整质量'),('truth.temperature_k','K','集中热真值'),('truth.plastic_work_j','J','累计塑性耗散')]:slot(k,number(u,0),u,z,'process_truth','evaluator')
slot('acquisition.phase',PHASE,'1','操作生命周期');slot('acquisition.point_phase',enum(['idle','loading','holding','unloading','release','retracting','moving','approaching']),'1','当前物理段');slot('acquisition.indent_index',integer(0,99),'1','当前线阵点');slot('acquisition.completed_indents',integer(0,100),'1','完整卸载完成的点数');slot('acquisition.elapsed_s',number('s',0),'s','宿主实际历时');slot('acquisition.points',integer(0,250000),'1','真实观测行数');slot('clock.time_s',number('s',0),'s','唯一宿主时钟');slot('observation.unavailable_reason',nullable(TEXT),'1','无电、无接触、量程不可用原因')

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

SYNTHESIS_BASIS='scalar_hardness_modulus_synthesis'
SYNTHESIS_EVIDENCE='SYNTHESIZED load-depth response, not a measured curve: published scalar indentation hardness H and reduced modulus Er (with stated tip/Poisson conversion) are inverted through ideal-Berkovich Oliver-Pharr relations (A=P/H=c2*hc^2, S=2*beta*Er*sqrt(A)/sqrt(pi), hmax=hc+epsilon*P/S, he=m*P/S) at the configured peak load. Loading follows ideal self-similar Kick P=K*h^2; unloading exponent m is the source value or a declared assumption. No measured time axis, creep, indentation-size effect, pile-up/sink-in, cracking or substrate effect.'
def synthesis_model(reference,accept_projection,peak_load_n=None):
 """Clearly labelled synthesized P-h from real scalar H/Er; built at the method peak load so OP of the unloading reproduces the source scalars."""
 try:s=reference['runtime_scalars'];H=float(s['hardness_pa']);er=float(s['reduced_modulus_pa']);m=float(s['unloading_exponent']);eps=float(s['epsilon']);beta=float(s['beta']);c2=float(s['area_c2']);domain=float(s['load_domain_max_n'])
 except (KeyError,TypeError,ValueError):raise ActionRejected('INVALID_REFERENCE')
 if not reference.get('runtime_usable') or reference.get('runtime_basis')!=SYNTHESIS_BASIS or not all(math.isfinite(v) and v>0 for v in [H,er,m,eps,beta,c2,domain]) or not 1<m<=2 or domain>FORCE_MAX:raise ActionRejected('INVALID_REFERENCE')
 if not accept_projection:raise ActionRejected('PROJECTION_NOT_ACCEPTED')
 p0=domain if peak_load_n is None else float(peak_load_n)
 if not 0<p0<=domain*(1+1e-12):raise ActionRejected('REFERENCE_LOAD_DOMAIN')
 area=p0/H;hc=math.sqrt(area/c2);stiffness=2*beta*er*math.sqrt(area)/math.sqrt(math.pi);he=m*p0/stiffness;hmax=hc+eps*p0/stiffness
 # Monotone plastic envelope for m<=2 requires he<m*hmax/2 at the peak (derivative of hmax*sqrt(P/P0)-(P/a)^(1/m)).
 if not he<.5*m*hmax or hmax>DEPTH_MAX:raise ActionRejected('REFERENCE_DOMAIN')
 a=p0/he**m;x=p0*np.linspace(0.,1.,201)**2;hp=hmax*np.sqrt(x/p0)-(x/a)**(1/m);hp[0]=0.
 if np.any(np.diff(x)<=0) or np.any(np.diff(hp)<-1e-18):raise ActionRejected('REFERENCE_DOMAIN')
 hp=np.maximum.accumulate(np.maximum(hp,0.))
 projection=dict(basis=SYNTHESIS_BASIS,synthesized_curve=True,measured_curve=False,synthesis_peak_load_n=p0,load_domain_max_n=domain,load_domain_basis=s.get('load_domain_basis'),hardness_pa=H,reduced_modulus_pa=er,reduced_modulus_basis=s.get('reduced_modulus_basis'),unloading_exponent=m,unloading_exponent_basis=s.get('unloading_exponent_basis'),epsilon=eps,beta=beta,area_c2=c2,loading_law='P=K*h^2 ideal self-similar Berkovich loading through (hmax,P)',contact_depth_m=hc,maximum_depth_m=hmax,elastic_recovery_m=he,unloading_stiffness_n_m=stiffness,load_projection_nodes=0,plastic_projection_nodes=0,duplicate_load_nodes_removed=0,padding_nodes_excluded=0,source_time_known=False,evidence=SYNTHESIS_EVIDENCE)
 return dict(load=x,plastic=hp,coefficient=a,exponent=m,maximum_load=p0,projection=projection)

def source_model(reference,accept_projection):
 if isinstance(reference,dict) and reference.get('runtime_basis')==SYNTHESIS_BASIS:return synthesis_model(reference,accept_projection)
 try:p=np.asarray(reference['values']['load_n'],float);h=np.asarray(reference['values']['depth_m'],float);domain=reference['runtime_domain'];last=int(domain['active_last_index']);end=int(domain['loading_end_index']);unload=int(domain.get('unloading_start_index',domain.get('hold_end_index')));start=int(domain.get('loading_start_index',0))
 except (KeyError,TypeError,ValueError):raise ActionRejected('INVALID_REFERENCE')
 if not reference.get('runtime_usable') or p.shape!=h.shape or p.ndim!=1 or len(p)<12 or not 0<=start<end-1 or not 2<=end<unload<last<len(p) or np.any(~np.isfinite(p)) or np.any(~np.isfinite(h)):raise ActionRejected('INVALID_REFERENCE')
 fit=fit_unloading(p[unload:last+1],h[unload:last+1]);m=fit['exponent'];a=fit['coefficient_n_m_power'];xp=np.maximum.accumulate(np.maximum(p[start:end+1],0.));raw_hp=h[start:end+1]-np.maximum(xp,0.)**(1/m)/a**(1/m);hp=np.maximum.accumulate(np.maximum(raw_hp,0.));keep=np.r_[np.diff(xp)>0,True];xp=xp[keep];hp=hp[keep]
 if len(xp)<3 or xp[0]!=0 or hp[0]!=0 or xp[-1]>.5 or h[start:last+1].max()>.0002:raise ActionRejected('REFERENCE_DOMAIN')
 changes=dict(load_projection_nodes=int(np.count_nonzero(np.maximum.accumulate(np.maximum(p[start:end+1],0.))!=p[start:end+1])),plastic_projection_nodes=int(np.count_nonzero(np.maximum.accumulate(np.maximum(raw_hp,0.))!=raw_hp)),duplicate_load_nodes_removed=int((~keep).sum()),padding_nodes_excluded=len(p)-last-1,loading_end_index=end,unloading_start_index=unload,active_last_index=last,source_time_known=reference['values'].get('time_s') is not None,elastic_release_below_observed_min_load_n=float(min(p[unload:last+1])),fit_rms_n=fit['fit_rms_n'],fit_exponent=m)
 if start:changes.update(loading_start_index=start,pre_contact_nodes_excluded=start)
 if not accept_projection and any(changes[k] for k in ['load_projection_nodes','plastic_projection_nodes','duplicate_load_nodes_removed']):raise ActionRejected('PROJECTION_NOT_ACCEPTED')
 return dict(load=xp,plastic=hp,coefficient=a,exponent=m,maximum_load=float(xp[-1]),projection=changes)

class AntonpaarHit300Runtime:
 def __init__(self,instance_id='hit300_01'):
  self.instance_id=instance_id;self.plant=None;self.specimen=None;self.reference=None;self.model=None;self.power=False;self.clamped=False;self.contact=False;self.laser=False;self.emergency=False;self.stage_x=.02;self.stage_y=.02;self.target_x=.02;self.stage_speed=.005;self.head_lift=.09;self.head_target=.09;self.force=0.;self.force_target=0.;self.plastic_depth=0.;self.temperature=296.15;self.initial_temperature=296.15;self.observed_force=0.;self.observed_tip=0.;self.observed_ring=0.;self.mass_loaded=0.;self.mass_recovered=0.;self.deformed_volume=0.;self.mechanical_input=0.;self.elastic_recovered=0.;self.plastic_work=0.;self.heat_input=0.;self.heat_loss=0.;self.thermal_excess_j=0.;self.damage=[];self.active_damage=None;self.method=copy.deepcopy(METHOD_EXAMPLE);self.calibration=copy.deepcopy(CALIBRATION_EXAMPLE);self.phase='idle';self.point_phase='idle';self.phase_elapsed=0.;self.indent_index=0;self.completed=0;self.elapsed=0.;self.rows=[];self.point_results=[];self.operation_start=0.;self.operation_id=None;self.operation_method=None;self.operation_calibration=None;self.operation_reference=None;self.operation_source=None;self.operation_projection=None;self.error=None;self.records={};self._counter=0;self.time_s=0.;self._ticks=0;self._remainder=0.;self.revision=0;self._requests={};self._owners={};self._cancel_operations={};self._principals={'agent':'agent','evaluator':'evaluator'}
 def register_principal(self,principal_id,visibility='agent'):
  if not isinstance(principal_id,str) or not principal_id or principal_id in self._principals or visibility not in ('agent','debug','evaluator'):raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='agent'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return AntonpaarHit300Session(self,principal_id)
 def describe(self):return export_antonpaar_hit300_contract_metadata()
 def _moving(self):return bool(abs(self.stage_x-self.target_x)>1e-12)
 def _head_moving(self):return bool(abs(self.head_lift-self.head_target)>1e-12)
 def _elastic(self,p=None):
  p=self.force if p is None else p;return (p/self.model['coefficient'])**(1/self.model['exponent']) if self.model and p>0 else 0.
 def _stored(self,p=None):
  p=self.force if p is None else p;return p*self._elastic(p)/(self.model['exponent']+1) if self.model else 0.
 def _depth(self):return self.plastic_depth+self._elastic() if self.contact else 0.
 def _indicated(self):
  if not self.power:return dict(force=None,depth=None,tip=None,ring=None,reason='POWER_OFF')
  c=self.calibration;p=(self.observed_force-c['force_zero_n'])/c['force_gain_correction'];h=(self.observed_tip-self.observed_ring-c['depth_zero_m'])/c['depth_gain_correction'];reason=None
  if abs(p)>.5 or abs(h)>.0002:p=h=None;reason='SENSOR_OVER_RANGE'
  elif not self.contact:h=None;reason='NO_CONTACT'
  return dict(force=p,depth=h,tip=self.observed_tip,ring=self.observed_ring,reason=reason)
 def read(self,role='evaluator'):
  v=self._indicated();d={'plant.configured':self.plant is not None,'power.enabled':self.power,'clamp.locked':self.clamped,'contact.engaged':self.contact,'specimen.present':self.specimen is not None,'laser.enabled':self.laser,'emergency.latched':self.emergency,'stage.moving':self._moving(),'head.moving':self._head_moving(),'stage.x_m':self.stage_x,'stage.y_m':self.stage_y,'head.lift_m':self.head_lift,'head.penetration_m':self._depth(),'clamp.release_m':0. if self.clamped else .003,'power.angle_rad':.28 if self.power else 0.,'stage.target_x_m':self.target_x,'head.target_lift_m':self.head_target,'force.target_n':self.force_target,'force.indicated_n':v['force'],'depth.indicated_m':v['depth'],'tip.indicated_m':v['tip'],'ring.indicated_m':v['ring'],'thermal.indicated_k':self.temperature if self.power else None,'truth.force_n':self.force,'truth.plastic_depth_m':self.plastic_depth,'truth.elastic_depth_m':self._elastic(),'truth.deformed_volume_m3':self.deformed_volume,'truth.sample_mass_kg':self._volume()*self.specimen['density_kg_m3'] if self.specimen else 0.,'truth.temperature_k':self.temperature,'truth.plastic_work_j':self.plastic_work,'acquisition.phase':self.phase,'acquisition.point_phase':self.point_phase,'acquisition.indent_index':self.indent_index,'acquisition.completed_indents':self.completed,'acquisition.elapsed_s':self.elapsed,'acquisition.points':len(self.rows),'clock.time_s':self.time_s,'observation.unavailable_reason':v['reason']};return copy.deepcopy({k:x for k,x in d.items() if role in ('evaluator','debug') or SLOTS[k]['visibility']=='agent'})
 def _volume(self):return self.specimen['length_m']*self.specimen['width_m']*self.specimen['thickness_m'] if self.specimen else 0.
 def _ledger(self):
  v=self._volume();mass=v*self.specimen['density_kg_m3'] if self.specimen else 0.;stored=self._stored();thermal=self.thermal_excess_j
  return dict(sample_loaded_kg=self.mass_loaded,sample_present_kg=mass,sample_recovered_kg=self.mass_recovered,sample_error_kg=self.mass_loaded-mass-self.mass_recovered,pristine_volume_m3=v-self.deformed_volume,deformed_volume_m3=self.deformed_volume,volume_error_m3=v-(v-self.deformed_volume)-self.deformed_volume,mechanical_input_j=self.mechanical_input,elastic_recovered_j=self.elastic_recovered,elastic_stored_j=stored,plastic_work_j=self.plastic_work,energy_error_j=self.mechanical_input-self.elastic_recovered-stored-self.plastic_work,heat_loss_j=self.heat_loss,thermal_stored_j=thermal,thermal_error_j=self.heat_input-self.heat_loss-thermal,heat_input_j=self.heat_input,defect_stored_j=self.plastic_work-self.heat_input)
 def _result(self):
  if self.phase!='running' and self.operation_id in self.records:return copy.deepcopy(self.records[self.operation_id])
  return dict(operation_id=self.operation_id,phase=self.phase,elapsed_s=self.elapsed,completed_indents=self.completed,rows=copy.deepcopy(self.rows),points=copy.deepcopy(self.point_results),reference_id=self.operation_reference,template_source=self.operation_source,projection_json=self.operation_projection,method_json=self.operation_method,evidence=SYNTHESIS_EVIDENCE+' Force/sensors/frame/drift are independent inputs. Oliver–Pharr uses only observed unloading with explicit area/frame/tip/Poisson calibration; source scalars are not read by the analysis and no manufacturer accuracy/noise claim. Fixed-Y automatic X array; Y remains a manual proxy.' if self.operation_projection and SYNTHESIS_BASIS in self.operation_projection else 'Measured P-h source drives a bounded engineering elastoplastic proxy. Source time is unknown; rate and hold produce an explicit engineering schedule, with no fitted viscoelastic creep. Loading plastic envelope and unloading power law are derived, projection counts retained. Zero-force elastic release below observed tail is explicitly accepted extrapolation. Force/sensors/frame/drift are independent inputs. Oliver–Pharr uses observed unloading with explicit area/frame/tip/Poisson calibration; no hidden true H/E or manufacturer accuracy/noise claim. Fixed-Y automatic X array; Y remains a manual proxy.',error_code=self.error)
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
 def _clear(self):
  self._stopped();self._released()
  if self.contact or self.head_lift<.002:raise ActionRejected('HEAD_NOT_CLEAR')
 def _access(self):
  self._clear()
  if self.clamped:raise ActionRejected('CLAMP_LOCKED')
  if self.plant and self.temperature>self.plant['safe_access_temperature_k']:raise ActionRejected('HOT_SPECIMEN')
 def _invoke(self,k,a):
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  if k=='ledger.read':return self._ledger()
  if k=='acquisition.result':return self._result()
  if k=='analysis.oliver_pharr':return oliver_pharr(a['load_n'],a['depth_m'],a['calibration'])
  if k=='reference.query':
   r=library()['substances'].get(a['reference_id'])
   if r is None:raise ActionRejected('INVALID_REFERENCE')
   return dict(reference_json=json.dumps(r,ensure_ascii=False),template_source=json.dumps(r.get('source'),ensure_ascii=False))
  if k=='plant.configure':
   if self.plant or self.time_s:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   if a['temperature_max_k']<a['ambient_k']:raise ActionRejected('PLANT_CONFIGURATION')
   self.plant=copy.deepcopy(a);self.temperature=self.initial_temperature=a['ambient_k'];return {'configured':True}
  if k=='specimen.load':
   self._access()
   if not self.plant:raise ActionRejected('NOT_READY')
   if self.specimen:raise ActionRejected('ALREADY_LOADED')
   if not a['accept_engineering'] or not a['accept_elastic_release_extrapolation']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   r=copy.deepcopy(library()['substances'].get(a['reference_id'],{}));model=source_model(r,a['accept_projection']);self.reference=r;self.model=model;self.specimen=copy.deepcopy(a);self.mass_loaded+=self._volume()*a['density_kg_m3'];self.deformed_volume=0.;self.damage=[];self.active_damage=None;self.plastic_depth=0.;return {'loaded':True}
  if k=='specimen.remove':
   self._access()
   if not self.specimen:raise ActionRejected('NOT_LOADED')
   self.mass_recovered+=self._volume()*self.specimen['density_kg_m3'];self.specimen=None;self.model=None;self.reference=None;self.deformed_volume=0.;self.plastic_depth=0.;self.damage=[];self.active_damage=None;return {'removed':True}
  if k=='power.set':
   self._stopped();self._released()
   if a['enabled'] and (not self.plant or self.emergency):raise ActionRejected('NOT_READY')
   self.power=a['enabled']
   if not self.power:self.laser=False
   return a
  if k=='laser.set':
   if a['enabled'] and not self.power:raise ActionRejected('NOT_READY')
   self.laser=a['enabled'];return a
  if k=='clamp.set':
   self._clear()
   if a['locked'] and not self.specimen:raise ActionRejected('NO_SPECIMEN')
   self.clamped=a['locked'];return a
  if k=='stage.move_x':
   self._clear()
   if not self.power or self.emergency:raise ActionRejected('NOT_READY')
   self.target_x=a['position_m'];self.stage_speed=a['speed_m_s'];return {'target_m':self.target_x}
  if k=='stage.set_y':
   self._clear()
   if not a['accept_manual']:raise ActionRejected('MANUAL_ACTION_NOT_ACCEPTED')
   self.stage_y=a['position_m'];return {'position_m':self.stage_y}
  if k=='head.set_position':
   self._stopped();self._released()
   if self.contact:raise ActionRejected('CONTACT_ENGAGED')
   if not self.power or self.emergency:raise ActionRejected('NOT_READY')
   if a['distance_m']<.002 and (not self.specimen or not self.clamped):raise ActionRejected('NO_CLAMPED_SPECIMEN')
   self.head_target=a['distance_m'];return {'target_m':self.head_target}
  if k=='contact.set':
   self._stopped();self._released()
   if a['engaged'] and (not self.power or self.emergency or not self.specimen or not self.clamped or self.head_lift>1e-12):raise ActionRejected('CONTACT_NOT_READY')
   self.contact=a['engaged'];self.plastic_depth=0.;return a
  if k=='sensor.calibrate':
   self._stopped();self._released()
   if a['fit_min_fraction']>=a['fit_max_fraction'] or a['area_min_depth_m']>=a['area_max_depth_m']:raise ActionRejected('CALIBRATION_DOMAIN')
   self.calibration=copy.deepcopy(a);return a
  if k=='sensor.zero':
   self._clear()
   if not self.power:raise ActionRejected('NOT_READY')
   self.calibration.update(force_zero_n=self.observed_force,depth_zero_m=self.observed_tip-self.observed_ring);return {'zeroed':True}
  if k=='method.configure':
   self._stopped();self._released()
   if a['mode']!='quasi_static' or a['control']!='load_rate':raise ActionRejected('UNSUPPORTED')
   if not a['accept_no_creep_hold']:raise ActionRejected('HOLD_MODEL_NOT_ACCEPTED')
   if abs(a['sample_interval_s']/DT-round(a['sample_interval_s']/DT))>1e-8:raise ActionRejected('SAMPLING_GRID')
   self.method=copy.deepcopy(a);return a
  if k=='acquisition.start':
   self._stopped();self._released();m=self.method
   if not self.power or self.emergency or not self.specimen or not self.clamped or not self.contact or self.head_lift>1e-12:raise ActionRejected('NOT_READY')
   if abs(self.stage_x-m['x_positions_m'][0])>1e-12:raise ActionRejected('STAGE_NOT_AT_START')
   if self.reference.get('runtime_basis')==SYNTHESIS_BASIS:self.model=synthesis_model(self.reference,self.specimen['accept_projection'],m['peak_load_n'])
   if m['peak_load_n']>self.model['maximum_load']+1e-14:raise ActionRejected('REFERENCE_LOAD_DOMAIN')
   positions=m['x_positions_m'];radius=self.specimen['damage_radius_m']
   if any(abs(x-y)<2*radius for i,x in enumerate(positions) for y in positions[i+1:]):raise ActionRejected('ARRAY_OVERLAP')
   if any(math.hypot(x-d['x'],self.stage_y-d['y'])<radius+d['radius'] for x in positions for d in self.damage if d['plastic_depth_m']>0):raise ActionRejected('POSITION_ALREADY_DAMAGED')
   if not self.specimen['accept_unknown_conditions'] and self.reference.get('conditions',{}).get('temperature_k') is None:raise ActionRejected('UNKNOWN_REFERENCE_CONDITIONS')
   c=self.reference.get('conditions',{})
   if not self.specimen['accept_condition_transfer']:
    for key,value in [('load_rate_n_s',m['loading_rate_n_s']),('unload_rate_n_s',m['unloading_rate_n_s']),('hold_s',m['hold_s']),('peak_load_n',m['peak_load_n']),('temperature_k',self.temperature)]:
     if c.get(key) is not None and abs(c[key]-value)>max(1e-12,abs(value)*1e-6):raise ActionRejected('REFERENCE_CONDITIONS')
   self._counter+=1;self.operation_id=self.instance_id+':'+str(self._counter);self.operation_start=self.time_s;self.operation_method=json.dumps(m,sort_keys=True);self.operation_calibration=copy.deepcopy(self.calibration);self.operation_reference=self.specimen['reference_id'];self.operation_source=json.dumps(self.reference.get('source'),ensure_ascii=False);self.operation_projection=json.dumps(self.model['projection'],sort_keys=True);self.phase='running';self.elapsed=0.;self.rows=[];self.point_results=[];self.indent_index=0;self.completed=0;self.error=None;self._new_point();return {'operation_id':self.operation_id}
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self._finish('cancelled');return {'phase':'cancelled'}
  if k=='emergency.stop':
   self.emergency=True;self.target_x=self.stage_x;self.head_target=self.head_lift
   if self.phase=='running':self._finish('failed','SOFTWARE_STOP')
   self.force_target=0.;return {'latched':True}
  if k=='emergency.reset':self._stopped();self._released();self.emergency=False;return {'latched':False}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _new_point(self):
  self.contact=True;self.plastic_depth=0.;self.force_target=0.;self.point_phase='loading';self.phase_elapsed=0.;self.active_damage=dict(x=self.stage_x,y=self.stage_y,radius=self.specimen['damage_radius_m'],plastic_depth_m=0.);self.damage.append(self.active_damage);self._record()
 def _thermal_energy(self,dt,heat):
  p=self.plant;x=p['heat_loss_w_k']*dt/p['thermal_capacity_j_k']
  return self.thermal_excess_j+heat if x==0 else self.thermal_excess_j*math.exp(-x)+heat*(-math.expm1(-x))/x
 def _thermal(self,dt,heat):return self.plant['ambient_k']+self._thermal_energy(dt,heat)/self.plant['thermal_capacity_j_k']
 def _prediction(self,dt,target,slope,plastic):
  f1,i0,_=force_ramp(self.force,target,slope,self.plant['force_response_tau_s'],dt)
  if target==0 and slope==0 and f1<=self.plant['force_release_n']:f1=0.
  hp=self.plastic_depth;work=0.
  if plastic and f1>self.force:
   x=self.model['load'];y=self.model['plastic'];extra=float(np.interp(hp,y,x));p=np.unique(np.r_[self.force,x[(x>self.force)&(x<f1)],extra if self.force<extra<f1 else self.force,f1]);h=np.maximum(hp,np.interp(p,x,y));work=float(np.sum((p[1:]+p[:-1])*.5*np.diff(h)));hp=float(h[-1])
  coefficient=self.specimen['deformation_shape_factor']*self.specimen['deformation_area_c2'] if self.specimen else 0.;dv=coefficient*(hp**3-self.plastic_depth**3);heat=self.plant['plastic_heat_fraction']*work;t=self._thermal(dt,heat);depth=hp+self._elastic(f1);reason=None
  if dv>self._volume()-self.deformed_volume:reason='SPECIMEN_VOLUME_EXHAUSTED'
  if self.specimen and depth>self.specimen['thickness_m']:reason='SPECIMEN_THICKNESS'
  if depth>DEPTH_MAX:reason='DEPTH_RANGE'
  if f1>FORCE_MAX:reason='FORCE_RANGE'
  if self._thermal_energy(dt,heat)>self.plant['thermal_capacity_j_k']*(self.plant['temperature_max_k']-self.plant['ambient_k']):reason='THERMAL_MODEL_RANGE'
  return dict(dt=dt,force=f1,force_integral=i0,plastic_depth=hp,plastic_work=work,delta_volume=dv,heat=heat,temperature=t,thermal_energy=self._thermal_energy(dt,heat),reason=reason)
 def _sensors(self,dt,mean_force):
  if not self.power or dt<=0:return
  p=self.plant;one=-math.expm1(-dt/p['sensor_tau_s']);common=p['common_drift_m_s']*(self.time_s+dt);tip=(self._depth()+p['frame_compliance_m_n']*self.force)*p['depth_gain']+p['depth_offset_m']+common+p['differential_drift_m_s']*(self.time_s+dt);self.observed_force+=one*(mean_force*p['force_gain']+p['force_offset_n']-self.observed_force);self.observed_tip+=one*(tip-self.observed_tip);self.observed_ring+=one*(common-self.observed_ring)
 def _advance(self,dt,target,slope,plastic):
  q=self._prediction(dt,target,slope,plastic);reason=q['reason']
  if reason:
   lo=0.;hi=dt
   for _ in range(45):
    mid=(lo+hi)/2
    if self._prediction(mid,target,slope,plastic)['reason'] is None:lo=mid
    else:hi=mid
   dt=lo;q=self._prediction(dt,target,slope,plastic)
  if dt>0:
   delta_u=self._stored(q['force'])-self._stored();self.mechanical_input+=max(delta_u,0.)+q['plastic_work'];self.elastic_recovered+=max(-delta_u,0.);self.plastic_work+=q['plastic_work'];self.heat_input+=q['heat'];self.heat_loss+=q['heat']-(q['thermal_energy']-self.thermal_excess_j);self.thermal_excess_j=q['thermal_energy'];self.force=q['force'];self.plastic_depth=q['plastic_depth'];self.deformed_volume+=q['delta_volume'];self.temperature=q['temperature'];self.force_target=max(0.,min(FORCE_MAX,target+slope*dt))
   if self.active_damage:self.active_damage['plastic_depth_m']=max(self.active_damage['plastic_depth_m'],self.plastic_depth)
   self._sensors(dt,q['force_integral']/dt);self.time_s+=dt;self.phase_elapsed+=dt;self.elapsed=self.time_s-self.operation_start
  if self._indicated()['reason']=='SENSOR_OVER_RANGE':reason='SENSOR_OVER_RANGE'
  if reason:self._finish('failed',reason)
 def _record(self):
  if self.point_phase=='idle':return
  if self.rows and abs(self.rows[-1]['time_s']-self.time_s)<1e-12 and self.rows[-1]['phase']==self.point_phase and self.rows[-1]['indent_index']==self.indent_index:return
  v=self._indicated();self.rows.append(dict(time_s=self.time_s,elapsed_s=self.time_s-self.operation_start,indent_index=self.indent_index,phase=self.point_phase,stage_x_m=self.stage_x,stage_y_m=self.stage_y,force_target_n=self.force_target,force_n=v['force'],depth_m=v['depth'],tip_indicated_m=v['tip'],ring_indicated_m=v['ring'],temperature_k=self.temperature if self.power else None,unavailable_reason=v['reason']))
 def _point_result(self,phase):
  if any(p['index']==self.indent_index for p in self.point_results):return
  rows=[r for r in self.rows if r['indent_index']==self.indent_index and r['phase']=='unloading'];analysis=None;error=None
  if phase=='succeeded':
   try:
    if any(r['force_n'] is None or r['depth_m'] is None for r in rows):raise ActionRejected('UNAVAILABLE_UNLOADING')
    analysis=oliver_pharr([r['force_n'] for r in rows],[r['depth_m'] for r in rows],self.operation_calibration)
   except ActionRejected as exc:error=str(exc)
  else:error='INCOMPLETE_UNLOADING'
  self.point_results.append(dict(index=self.indent_index,x_m=self.active_damage['x'] if self.active_damage else self.stage_x,y_m=self.stage_y,phase=phase,analysis=analysis,analysis_error=error))
 def _finish(self,phase,error=None):
  self.elapsed=self.time_s-self.operation_start;self._record();self._point_result(phase);self.phase=phase;self.error=error;self.force_target=0.;self.target_x=self.stage_x;self.head_target=self.head_lift;self.records[self.operation_id]=self._result();self.point_phase='idle'
 def _change_phase(self,phase):self.point_phase=phase;self.phase_elapsed=0.;self._record()
 def _move(self,dt):
  self.stage_x+=max(-self.stage_speed*dt,min(self.stage_speed*dt,self.target_x-self.stage_x));self.head_lift+=max(-self.plant['head_speed_m_s']*dt,min(self.plant['head_speed_m_s']*dt,self.head_target-self.head_lift))
 def _idle(self,dt):
  if self.plant:
   if self.model:
    q=self._prediction(dt,0.,0.,False);du=self._stored()-self._stored(q['force']);self.elastic_recovered+=du;self.force=q['force'];self.force_target=0.
   old=self.thermal_excess_j;self.thermal_excess_j=self._thermal_energy(dt,0.);self.temperature=self.plant['ambient_k']+self.thermal_excess_j/self.plant['thermal_capacity_j_k'];self.heat_loss+=old-self.thermal_excess_j;self._sensors(dt,self.force)
   if self.power and not self.emergency:self._move(dt)
  self.time_s+=dt
 def _tick(self):
  self.time_s=(self._ticks-1)*DT;end=self._ticks*DT
  while self.time_s<end-1e-12:
   if self.phase!='running':self._idle(end-self.time_s);break
   m=self.method;dt=min(.005,end-self.time_s,m['timeout_s']-(self.time_s-self.operation_start));phase=self.point_phase
   if dt<=1e-13:self._finish('failed','TIMEOUT');continue
   if phase in ('retracting','moving','approaching'):
    self._idle(dt);self.elapsed=self.time_s-self.operation_start
    if phase=='retracting' and not self._head_moving():self.target_x=m['x_positions_m'][self.indent_index+1];self.stage_speed=self.plant['stage_speed_m_s'];self._change_phase('moving')
    elif phase=='moving' and not self._moving():self.head_target=0.;self._change_phase('approaching')
    elif phase=='approaching' and not self._head_moving():self.indent_index+=1;self._new_point()
    continue
   duration=m['peak_load_n']/m['loading_rate_n_s'] if phase=='loading' else m['hold_s'] if phase=='holding' else m['peak_load_n']/m['unloading_rate_n_s'] if phase=='unloading' else math.inf
   remaining=duration-self.phase_elapsed
   if remaining<=1e-12:
    if phase=='loading':self.force_target=m['peak_load_n'];self._change_phase('holding')
    elif phase=='holding':self._change_phase('unloading')
    elif phase=='unloading':self.force_target=0.;self._change_phase('release')
    continue
   dt=min(dt,remaining);slope=m['loading_rate_n_s'] if phase=='loading' else -m['unloading_rate_n_s'] if phase=='unloading' else 0.;target=m['loading_rate_n_s']*self.phase_elapsed if phase=='loading' else m['peak_load_n']-m['unloading_rate_n_s']*self.phase_elapsed if phase=='unloading' else m['peak_load_n'] if phase=='holding' else 0.;self._advance(dt,max(0.,target),slope,phase in ('loading','holding','unloading'))
   if self.phase!='running':continue
   if self.point_phase=='release' and self.force==0:
    self._record();self._point_result('succeeded');self.completed+=1
    if self.indent_index+1==len(m['x_positions_m']):self._finish('succeeded')
    else:self.contact=False;self.plastic_depth=0.;self.head_target=self.plant['array_clearance_m'];self._change_phase('retracting')
  self.time_s=end
  if self.phase=='running' and (self._ticks-round(self.operation_start/DT))%round(self.method['sample_interval_s']/DT)==0:self._record()
 def step(self,dt_s):
  validate(number('s',0,3600),dt_s);total=self._remainder+dt_s;n=int((total+1e-12)/DT);self._remainder=total-n*DT
  for _ in range(n):self._ticks+=1;self._tick()
  self.revision+=n

class AntonpaarHit300Session:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,instance_id):
  if instance_id!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  meta=export_antonpaar_hit300_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.1.0',capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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


EFFECTS={
'plant.configure':['plant.configured','truth.temperature_k'],
'specimen.load':['specimen.present','truth.sample_mass_kg','truth.deformed_volume_m3','truth.plastic_depth_m','head.penetration_m'],
'specimen.remove':['specimen.present','truth.sample_mass_kg','truth.deformed_volume_m3','truth.plastic_depth_m','head.penetration_m'],
'power.set':['power.enabled','power.angle_rad','laser.enabled','force.indicated_n','depth.indicated_m','tip.indicated_m','ring.indicated_m','thermal.indicated_k','observation.unavailable_reason'],
'clamp.set':['clamp.locked','clamp.release_m'],'laser.set':['laser.enabled'],'stage.move_x':['stage.target_x_m','stage.moving'],'stage.set_y':['stage.y_m'],'head.set_position':['head.target_lift_m','head.moving'],
'contact.set':['contact.engaged','head.penetration_m','truth.plastic_depth_m','depth.indicated_m','observation.unavailable_reason'],
'sensor.calibrate':['force.indicated_n','depth.indicated_m','observation.unavailable_reason'],'sensor.zero':['force.indicated_n','depth.indicated_m','observation.unavailable_reason'],
'acquisition.start':['acquisition.phase','acquisition.point_phase','acquisition.indent_index','acquisition.completed_indents','acquisition.elapsed_s','acquisition.points','truth.plastic_depth_m','head.penetration_m','force.target_n'],
'acquisition.stop':['acquisition.phase','acquisition.point_phase','acquisition.elapsed_s','acquisition.points','force.target_n','stage.target_x_m','stage.moving','head.target_lift_m','head.moving'],
'emergency.stop':['emergency.latched','acquisition.phase','acquisition.point_phase','acquisition.elapsed_s','acquisition.points','force.target_n','stage.target_x_m','stage.moving','head.target_lift_m','head.moving'],'emergency.reset':['emergency.latched'],'sim.step':list(SLOTS)}
for k,v in EFFECTS.items():CAPS[k]['effects']=v
PHYSICS_EFFECTS=[k for k in SLOTS if k not in ['plant.configured','specimen.present','clamp.locked','clamp.release_m','power.enabled','power.angle_rad','laser.enabled','emergency.latched','stage.y_m','truth.sample_mass_kg']]
def export_antonpaar_hit300_contract_metadata():
 slots={};caps={}
 for k,v in SLOTS.items():slots[k]=dict(meaning_zh=v['meaning'],component_ids=[v['component']],value_schema=copy.deepcopy(v['schema']),unit=v['unit'],reference_conditions='SI; 500mN/200um range. X motorized0..40mm; Y manual0..40mm. Head coarse90mm, fine penetration200um. Source P-h has no measured time axis; prescribed rate/hold is an explicit engineering schedule.',origin=v['origin'],visibility=v['visibility'],access='read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'AntonpaarHit300Session.read(state_ids=[{k!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description='Host0.1s; internal<=5ms plus load/hold/unload/array events. Boundary rows are included even if off sampling grid.'),source_ids=['simulation_assumption'])
 errors=['UNKNOWN_CAPABILITY','UNKNOWN_INSTANCE','UNKNOWN_OPERATION','INVALID_ARGUMENT','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','BUSY','NOT_RUNNING','NOT_READY','INITIALIZATION_ONLY','MODEL_NOT_ACCEPTED','PLANT_CONFIGURATION','LOAD_NOT_RELEASED','HEAD_NOT_CLEAR','CLAMP_LOCKED','NO_SPECIMEN','HOT_SPECIMEN','CONTACT_ENGAGED','NO_CLAMPED_SPECIMEN','CONTACT_NOT_READY','ALREADY_LOADED','NOT_LOADED','INVALID_REFERENCE','REFERENCE_DOMAIN','PROJECTION_NOT_ACCEPTED','MANUAL_ACTION_NOT_ACCEPTED','CALIBRATION_DOMAIN','SAMPLING_GRID','HOLD_MODEL_NOT_ACCEPTED','STAGE_NOT_AT_START','REFERENCE_LOAD_DOMAIN','ARRAY_OVERLAP','POSITION_ALREADY_DAMAGED','UNKNOWN_REFERENCE_CONDITIONS','REFERENCE_CONDITIONS','SPECIMEN_VOLUME_EXHAUSTED','SPECIMEN_THICKNESS','DEPTH_RANGE','FORCE_RANGE','THERMAL_MODEL_RANGE','SENSOR_OVER_RANGE','SOFTWARE_STOP','TIMEOUT','FIT_DATA','FIT_POINTS','FIT_DOMAIN','FIT_FAILED','NOT_UNLOADING','UNLOADING_NOSE','CONTACT_AREA_DOMAIN','MODULUS_DOMAIN','ELASTIC_UNLOADING_NOT_ACCEPTED','UNAVAILABLE_UNLOADING','INCOMPLETE_UNLOADING']
 for k,c in CAPS.items():
  un=k in UNSUPPORTED;async_=k=='acquisition.start';caps[k]={key:copy.deepcopy(c[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']};caps[k].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ['scene','robot','hardware'] else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.antonpaar_hit300.AntonpaarHit300Session.invoke',invocation=f'session.invoke(instance_id="hit300_01",capability_id={k!r},arguments={c["example_arguments"]!r},request_id="unique")'),preconditions=[] if c['kind'] in ['query','analysis'] or un else [dict(description_zh='抬头/卸载/夹紧、有限样片、未损伤位置、源载荷域、手动Y授权及工程假设显式接受；所有拒绝原子',state_ids=['power.enabled','clamp.locked','contact.engaged','head.lift_m','specimen.present','acquisition.phase'],enforced_by='runtime',check='atomic invoke and registered Session')],state_effects=[dict(state_id=x,timing='immediate',description_zh='真实命令改变的状态') for x in c['effects']],observes=[],model_effects=dict(mode='none',description_zh='几何联动由独立场景验证；压入为刚性姿态代理，没有真实网格凹坑/接触求解',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='逐点加载/保持/卸载到零与自动X转位完成' if async_ else '命令不推进时钟；电动目标等待宿主step，Y明确手动定位',completion_state_ids=['acquisition.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','specimen','indenter','force_controller','reference_ring','x_stage'],partial_failure_zh='永久保留已压入点与塑性耗散；取消保留实际载荷，随后hoststep按显式弹簧释放；未完成点不计完整结果'),errors=[dict(code=x,meaning_zh=x,side_effects='partial' if async_ and x in ['SPECIMEN_VOLUME_EXHAUSTED','SPECIMEN_THICKNESS','DEPTH_RANGE','FORCE_RANGE','THERMAL_MODEL_RANGE','SENSOR_OVER_RANGE','SOFTWARE_STOP','TIMEOUT'] else 'none') for x in errors],verification=[],limitations=['标称20nN/10pm分辨率不等于准确度/噪声验证','硬件支持动态模式，但本模型无相应源响应/校准，动态与黏弹分析明确unsupported','Y仅手动定位，固定Y的X线阵不是自动二维阵列'],source_ids=['manufacturer_specs','simulation_assumption'])
  if k in ['acquisition.start','acquisition.stop','emergency.stop','stage.move_x','head.set_position','power.set']:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='宿主推进真实移动、有限力响应、独立观察及能量变化') for x in PHYSICS_EFFECTS]
 mechanism=dict(component_ids=['load_head','stage_x','stage_y','stylus','reference_ring','specimen','controller'],state_ids=list(slots),meaning_zh='真实P-h锚定的有界弹塑性代理与独立OP卸载分析',implementation='lumped',equations=[
 'Finite servo dP/dt=(Ptarget-P)/tau under explicit linear loading/unloading rates. Hostfixed0.1s with<=5ms internal and phase events. Targets never directly become true force; reference maximum load and instrument0.5N/200um bounds enforced.',
 'Original P-h arrays and unknown time remain intact. Source metadata defines loading, unloading, and active end indices; identical export padding excluded only from model. Unloading P=a*(h-hf)^m is fitted over20–95% positive load. a has units N/m^m; it is internal, not a fixed-unit public stiffness.',
 'Engineering elastic spring e(P)=(P/a)^(1/m), U=P*e/(m+1). Loading plastic curve hp(P)=cummax(max(h_source(P)-e(P),0)); source P and hp projections counted and explicitly accepted. Actual hp=max(history,hp_curve(P)). Loading beyond source maximum rejected; below-observed-tail elastic release requires explicit acceptance.',
 'Plastic work is integral P dhp using piecewise-linear hp(P) source knots; mechanical input=positive changeU+plastic work, recovered=negative changeU. Plastic volume uses explicit shape_factor*area_factor*hp^3; finite pristine volume moves into persistent deformed region without deleting sample mass or inventing debris.',
 'C*dDeltaT/dt=heat_fraction*dWplastic/dt-G*DeltaT. Store tiny thermal excess energy separately from ambient temperature to avoid losing nanojoule heat to absolute-temperature cancellation. Remaining plastic work is defect stored energy, explicitly assumed.',
 'Tip and reference ring have common drift and independent depth/force calibration. Differential depth removes only common-mode component; differential drift/frame compliance remains. No arbitrary stochastic noise or achieved manufacturer RMS accuracy claim.',
 'Observed OP input: h_corrected=h_observed-Cf*P_observed exactly once, fit positive unloading P=a*(h-hf)^m, S=dP/dh at paired peakP/peak-depth point. Reject loading input, >2% load reversals or >2% creep nose; tolerance is an engineering analysis domain, not metrology accuracy. hc=hmax-epsilon*Pmax/S; A=c2*hc^2+c1*hc+c0.',
 'H=Pmax/A; Er=sqrt(pi)*S/(2*beta*sqrt(A)); E_sample=(1-nu_sample^2)/(1/Er-(1-nu_tip^2)/E_tip). All area/Cf/epsilon/beta/Poisson/tip values explicitly supplied, calibrated area-domain enforced. No sample true H/E field exists.',
 'Hold is an explicit no-creep equilibrium proxy, not inferred real viscoelastic response. Dynamic hardware exists but dynamic waveform, Eprime/Edoubleprime/tan(delta) and strain-rate-control are unsupported here.',
 'Automatic X line array: unload each point, lift reference head, move X, lower and start next point. Y is manual accepted positioning only. Persistent damage zones prevent duplicate or overlapping indentations; cancellation freezes partial rows and does not undo deformation.',
 'Records with runtime_basis=scalar_hardness_modulus_synthesis carry only published scalar H and Er (Er either source-reported or derived from E_IT with the stated sample Poisson ratio and diamond tip E=1141 GPa, nu=0.07). At acquisition.start the curve is SYNTHESIZED at the configured peak P: A=P/H, hc=sqrt(A/c2), S=2*beta*Er*sqrt(A)/sqrt(pi), hmax=hc+epsilon*P/S, he=m*P/S, elastic P=a*he^m with a=P/he^m, loading h=hmax*sqrt(P/Pmax). Peak load is bounded by the record load_domain_max_n; projection_json.basis and result evidence label the synthesis; it is never relabelled as a measured curve.'
 ],parameters={},source_ids=['manufacturer_specs','simulation_assumption','brittle_nanoindentation_2018'],limitations=['Cross-instrument P-h empirical reference is not native HIT300 calibration.','No time axis was measured in the selected source exports. Prescribed loading rates and holds do not create empirical creep kinetics.','No fracture, cracking, pile-up morphology, substrate-film inverse model, strain-rate constitutive law or viscoelastic rheology is solved.','Source raw P-h and derived loading/unloading proxy are distinct; full source unloading to zero is not available.','Scalar-H/Er records produce a synthesized ideal-Berkovich curve; it reproduces the source H and Er through Oliver–Pharr by construction and carries no information on creep, indentation-size effect, pile-up/sink-in, cracking, film/substrate effects or the true unloading shape (m is source-reported or an assumption).','OP assumes elastic unloading and area calibration, not arbitrary polymer/creep or pile-up validity.','The sample conserves total mass; affected volume and residual depth are engineering proxies, not profilometer measurements.','Within each <=5ms internal step, the sensor model is driven by interval-mean force and endpoint displacement; exact continuous sensor convolution is not claimed.','Actual noise floors, active vibration-isolation performance, physical hardware and standards certification are not verified.'])
 caps['reference.query']['source_ids'].append('brittle_nanoindentation_2018');caps['analysis.oliver_pharr']['source_ids'].append('oliver_pharr_method')
 spec=importlib.util.spec_from_file_location('_hit_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()});ann.annotate_export(SLUG,caps,slots,{'indentation':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'specimen.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sensor.calibrate':(CALIBRATION,CALIBRATION_EXAMPLE)})
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'indentation':mechanism})

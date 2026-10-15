"""CHEM-124 FT4 powder mechanics; measured energy anchors with explicit continuum assumptions."""
from pathlib import Path
import copy,json,math,importlib.util
import numpy as np
PROTOCOL_VERSION='1.0.0';SLUG='freeman_ft4powder';CATALOG_ID='CHEM-124';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch03/t4_measure'/SLUG
DT=.1;R=8.31446261815324;MN=.0280134;MAX_SPEED=4*math.pi;MAX_STROKE=.185;MAX_VELOCITY=.03
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
 p=ASSET/'powder_flow_library.json';return json.loads(p.read_text()) if p.exists() else {'substances':{}}
# Measured-anchor helpers (linear Mohr-Coulomb locus; pre-shear point on the locus; major Mohr circle tangent there).
def locus_metrics(cohesion_pa,friction_angle_rad,preconsolidation_stress_pa):
 c,phi,sp=cohesion_pa,friction_angle_rad,preconsolidation_stress_pa;t=c+sp*math.tan(phi);sm=sp+t*math.tan(phi);r=t/math.cos(phi);s1=sm+r;sc=2*c*math.cos(phi)/(1-math.sin(phi))
 return dict(major_principal_stress_pa=s1,unconfined_yield_strength_pa=sc,flow_function_coefficient=s1/sc if sc>0 else math.inf,effective_angle_rad=math.asin(min(1.,r/sm)),preshear_shear_stress_pa=t)
def cohesion_from_ffc(ffc,friction_angle_rad,preconsolidation_stress_pa):
 s=math.sin(friction_angle_rad);co=math.cos(friction_angle_rad);a=preconsolidation_stress_pa*(1+s)/co**2;b=(1+s)/co;d=2*co/(1-s)
 if not ffc*d>b:raise ActionRejected('INVALID_REFERENCE')
 return a/(ffc*d-b)
def locus_from_ffc_effective_angle(ffc,effective_angle_rad,preconsolidation_stress_pa):
 se=math.sin(effective_angle_rad);sp=preconsolidation_stress_pa
 def at(phi):
  t=se*sp*math.cos(phi)/(1-se*math.sin(phi));c=t-sp*math.tan(phi);return c,(locus_metrics(c,phi,sp)['flow_function_coefficient'] if c>0 else math.inf)
 lo,hi=0.,effective_angle_rad
 if not at(lo)[1]<ffc:raise ActionRejected('INVALID_REFERENCE')
 for _ in range(200):
  mid=(lo+hi)/2
  if at(mid)[1]<ffc:lo=mid
  else:hi=mid
 phi=(lo+hi)/2;return at(phi)[0],phi
def resolve_anchors(record,sample):
 """Per-mode real-data anchors for one loaded record; engineering-angle variants use the sample's explicit friction_angle_rad."""
 a=record.get('runtime_anchors') or {};out={}
 if a.get('dynamic'):out['dynamic']=copy.deepcopy(a['dynamic'])
 s=a.get('shear')
 if s:
  s=copy.deepcopy(s);m=s['method']
  if m=='ffc_with_engineering_angle':s['friction_angle_rad']=sample['friction_angle_rad'];s['cohesion_pa']=cohesion_from_ffc(s['flow_function_coefficient'],s['friction_angle_rad'],s['preconsolidation_stress_pa'])
  elif m=='cohesion_with_engineering_angle':s['friction_angle_rad']=sample['friction_angle_rad']
  out['shear']=s
 if a.get('compression'):out['compression']=copy.deepcopy(a['compression'])
 return out
PLANT_EXAMPLE=dict(accept_engineering=True,ambient_k=298.15,thermal_capacity_j_k=100.,maximum_temperature_rise_k=20.,heat_loss_w_k=.1,force_tau_s=.02,torque_tau_s=.02,pressure_tau_s=.2,gas_viscosity_pa_s=1.85e-5,gas_pressure_pa=101325.,gas_temperature_k=298.15,maximum_gas_flow_m3_s=1e-5,maximum_pressure_drop_pa=10000.,vessel_diameter_m=.05,vessel_volume_m3=85e-6,blade_radius_m=.024,shear_inner_radius_m=.01,shear_outer_radius_m=.024,compression_tau_s=.5,shear_tau_s=.3)
PLANT=obj({'accept_engineering':BOOL,'ambient_k':number('K',283.15,313.15),'thermal_capacity_j_k':number('J/K',.01,10000),'maximum_temperature_rise_k':number('K',1,100),'heat_loss_w_k':number('W/K',0,100),'force_tau_s':number('s',.001,100),'torque_tau_s':number('s',.001,100),'pressure_tau_s':number('s',.001,100),'gas_viscosity_pa_s':number('Pa*s',1e-6,1e-3),'gas_pressure_pa':number('Pa',80000,120000),'gas_temperature_k':number('K',273.15,333.15),'maximum_gas_flow_m3_s':number('m^3/s',1e-10,1e-3),'maximum_pressure_drop_pa':number('Pa',1,100000),'vessel_diameter_m':number('m',.01,.1),'vessel_volume_m3':number('m^3',1e-6,260e-6),'blade_radius_m':number('m',.001,.05),'shear_inner_radius_m':number('m',0,.049),'shear_outer_radius_m':number('m',.001,.05),'compression_tau_s':number('s',.001,100),'shear_tau_s':number('s',.001,100)})
SAMPLE_EXAMPLE=dict(reference_id='tablettose_70_2026',mass_kg=.051,accept_engineering=True,accept_condition_scaling=True,conditioned_bulk_density_kg_m3=600.,solid_density_kg_m3=1500.,initial_packing_ratio=1.,initial_history_factor=1.2,conditioning_rate_s=2.,packing_energy_exponent=2.,tip_speed_exponent=0.,force_work_fraction=.2,missing_bfe_j=.1,missing_specific_energy_j_kg=5.,cohesion_pa=500.,friction_angle_rad=.4,compression_modulus_pa=1e5,permeability_m2=1e-11,aeration_coefficient_s_m=10.)
SAMPLE=obj({'reference_id':TEXT,'mass_kg':number('kg',1e-8,2.),'accept_engineering':BOOL,'accept_condition_scaling':BOOL,'conditioned_bulk_density_kg_m3':number('kg/m^3',1,20000),'solid_density_kg_m3':number('kg/m^3',10,30000),'initial_packing_ratio':number('1',.5,2.),'initial_history_factor':number('1',.1,10.),'conditioning_rate_s':number('1/s',0,1000),'packing_energy_exponent':number('1',0,10),'tip_speed_exponent':number('1',-3,3),'force_work_fraction':number('1',.01,.99),'missing_bfe_j':number('J',1e-6,10),'missing_specific_energy_j_kg':number('J/kg',1e-3,10000),'cohesion_pa':number('Pa',0,1e5),'friction_angle_rad':number('rad',0,1.3),'compression_modulus_pa':number('Pa',100,1e8),'permeability_m2':number('m^2',1e-16,1e-7),'aeration_coefficient_s_m':number('s/m',0,1e5)})
METHOD_EXAMPLE=dict(mode='dynamic',start_position_m=.157,end_position_m=.182,axial_speed_m_s=.1*math.tan(math.radians(5)),angular_speed_rad_s=.1/.024,conditioning_cycles=1,measurement_cycles=7,normal_stresses_pa=[1000.,2000.,3000.],hold_s=2.,gas_flow_m3_s=1e-6,weighed_mass_kg=.051)
METHOD=obj({'mode':enum(['dynamic','shear','compression','permeability']),'start_position_m':number('m',0,MAX_STROKE),'end_position_m':number('m',0,MAX_STROKE),'axial_speed_m_s':number('m/s',1e-5,MAX_VELOCITY),'angular_speed_rad_s':number('rad/s',1e-4,MAX_SPEED),'conditioning_cycles':integer(0,20),'measurement_cycles':integer(1,20),'normal_stresses_pa':array(number('Pa',0,1e5),1,20),'hold_s':number('s',.1,3600),'gas_flow_m3_s':number('m^3/s',0,1e-3),'weighed_mass_kg':number('kg',1e-8,2.)})
GAS_EXAMPLE=dict(amount_mol=.01);GAS=obj({'amount_mol':number('mol',1e-15,1000)})
ROW=obj({'time_s':number('s'),'elapsed_s':number('s'),'segment':TEXT,'cycle':integer(),'position_m':number('m'),'rotation_rad':number('rad'),'force_n':number('N'),'torque_nm':number('N*m'),'pressure_drop_pa':number('Pa'),'gas_flow_m3_s':number('m^3/s'),'observed_work_j':number('J')})
POINT=obj({'kind':enum(['down','up','shear','compression','permeability']),'cycle':integer(),'energy_j':number('J'),'specific_energy_j_kg':nullable(number('J/kg')),'normal_stress_pa':nullable(number('Pa')),'shear_stress_pa':nullable(number('Pa')),'bulk_density_kg_m3':nullable(number('kg/m^3')),'compressibility_fraction':nullable(number('1')),'permeability_m2':nullable(number('m^2'))})
RESULT=obj({'operation_id':nullable(TEXT),'phase':PHASE,'elapsed_s':number('s'),'mode':nullable(TEXT),'rows':array(ROW,0,1000000),'points':array(POINT,0,10000),'basic_flowability_energy_j':nullable(number('J')),'specific_energy_j_kg':nullable(number('J/kg')),'reference_id':nullable(TEXT),'template_source':nullable(TEXT),'reference_anchor_json':nullable(TEXT),'evidence':TEXT,'error_code':nullable(TEXT)})
LEDGER=obj({k:number(u) for k,u in [('powder_loaded_kg','kg'),('powder_present_kg','kg'),('powder_recovered_kg','kg'),('powder_error_kg','kg'),('gas_loaded_mol','mol'),('gas_present_mol','mol'),('gas_exhaust_recovered_mol','mol'),('gas_error_mol','mol'),('mass_error_kg','kg'),('mechanical_dissipation_j','J'),('gas_dissipation_j','J'),('heat_loss_j','J'),('stored_heat_j','J'),('energy_error_j','J')]})
CAPS={};SLOTS={}
def cap(k,i,o,c,z,kind='device_command',visibility='agent',example=None,effects=None):CAPS[k]=dict(input_schema=i,output_schema=o,component_ids=c,function_zh=z,kind=kind,visibility=visibility,example_arguments=example or {},effects=effects or [])
def slot(k,s,u,z,origin='derived',visibility='agent',component='controller',write=None):SLOTS[k]=dict(schema=s,unit=u,meaning=z,origin=origin,visibility=visibility,component=component,write=write)
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller','load_cell','gas_manifold'],'显式确认有效热学、传感与附件尺寸；不声明厂家准确度','configuration','evaluator',PLANT_EXAMPLE,['plant.configured','truth.temperature_k'])
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_vessel'],'宿主装填有限粉体；记录的实测锚点（BFE/SE、屈服轨迹或FFc、CPS压缩率）按模式锚定响应，其余参数显式假设','configuration','evaluator',SAMPLE_EXAMPLE,['sample.present','truth.powder_mass_kg','truth.packing_ratio','truth.history_factor','truth.bulk_density_kg_m3','truth.bed_height_m'])
cap('sample.remove',obj({}),obj({'removed':BOOL}),['sample_vessel'],'轴顶端停转松夹后回收粉体','configuration','evaluator',effects=['sample.present','truth.powder_mass_kg','truth.packing_ratio','truth.history_factor','truth.bulk_density_kg_m3','truth.bed_height_m'])
cap('gas.load',GAS,obj({'loaded':BOOL}),['gas_manifold'],'装有限氮气库存；气流为所声明温压下实际体积','configuration','evaluator',GAS_EXAMPLE,['truth.gas_stock_mol'])
cap('gas.unload',obj({}),obj({'unloaded':BOOL}),['gas_manifold'],'关闭气路后回收剩余气体','configuration','evaluator',effects=['truth.gas_stock_mol'])
for k,a,s,c,z,ex,effect in [('power.set','enabled',BOOL,'controller','仪器供电',True,['power.enabled','force.indicated_n','torque.indicated_nm','pressure.indicated_pa','axis.target_m','spindle.target_rad_s','spindle.speed_rad_s','gas.enabled']),('clamp.set','locked',BOOL,'vessel_clamp','手动锁紧样品杯夹具',True,['clamp.locked','clamp.release_m']),('service.set','distance_m',number('m',0,.060),'service_cover','断电后盖位移',.060,['service.slide_m']),('tool.select','tool_type',enum(['blade','shear','piston','permeability']),'drive_head','人工更换过程附件；场景模型能力另行验证','blade',['tool.type']),('gas.open','enabled',BOOL,'gas_manifold','有限气路开关',True,['gas.enabled'])]:cap(k,obj({a:s}),obj({a:s}),[c],z,example={a:ex},effects=effect)
cap('door.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['safety_door'],'安全门打开立即停运动并冻结失败；关闭后必须显式重启',example={'opened':False},effects=['door.angle_rad','acquisition.phase','axis.target_m','spindle.target_rad_s','spindle.speed_rad_s','gas.enabled'])
slot('door.angle_rad',number('rad',0,1.65),'rad','透明安全门开角','scene',component='safety_door')
cap('axis.move',obj({'position_m':number('m',0,MAX_STROKE),'speed_m_s':number('m/s',1e-5,MAX_VELOCITY)}),obj({'position_m':number('m')}),['drive_head'],'下行为正的轴向目标，0为上端，实际由host步进',example={'position_m':0.,'speed_m_s':.03},effects=['axis.target_m','axis.speed_target_m_s'])
cap('spindle.set_speed',obj({'angular_speed_rad_s':number('rad/s',-MAX_SPEED,MAX_SPEED)}),obj({'angular_speed_rad_s':number('rad/s')}),['spindle'],'叶片旋转目标，±120rpm',example={'angular_speed_rad_s':0.},effects=['spindle.target_rad_s'])
cap('gas.set_flow',obj({'flow_m3_s':number('m^3/s',0,1e-3)}),obj({'flow_m3_s':number('m^3/s')}),['gas_manifold'],'所声明供气温压下实际气流',example={'flow_m3_s':1e-6},effects=['gas.target_m3_s'])
cap('method.configure',METHOD,METHOD,['controller','blade','sample_vessel'],'粉体路径、预处理/测量循环、法向应力和透气流量','configuration',example=METHOD_EXAMPLE)
CAL=obj({'force_zero_n':number('N',-.1,.1),'torque_zero_nm':number('N*m',-.01,.01),'pressure_zero_pa':number('Pa',-1000,1000)})
cap('sensor.calibrate',CAL,CAL,['load_cell','gas_manifold'],'输入独立零点校准；不从样品真值免费校准','configuration',example={'force_zero_n':0.,'torque_zero_nm':0.,'pressure_zero_pa':0.},effects=['force.indicated_n','torque.indicated_nm','pressure.indicated_pa'])
cap('acquisition.start',obj({}),obj({'operation_id':TEXT}),['controller','drive_head','spindle','blade','sample_vessel'],'异步执行粉体动态或附件试验，积分两种机械功',effects=['acquisition.phase','acquisition.elapsed_s','acquisition.segment','acquisition.cycle','acquisition.points','acquisition.work_j','axis.target_m','axis.speed_target_m_s','spindle.target_rad_s','gas.target_m3_s'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['controller'],'取消保留压实/填装历史、有限气耗与已耗散能','device_command',effects=['acquisition.phase','axis.target_m','spindle.target_rad_s','spindle.speed_rad_s','gas.enabled'])
cap('acquisition.result',obj({}),RESULT,['controller'],'纯读冻结结果与源条件；非第七次合规下行不自动称BFE','query')
cap('reference.query',obj({'reference_id':TEXT}),obj({'reference_json':TEXT,'template_source':TEXT,'evidence':TEXT}),['controller'],'查询原始粉体实测统计和方法条件，不假造力/扭矩逐点曲线','query',example={'reference_id':SAMPLE_EXAMPLE['reference_id']})
ENERGY=obj({'position_m':array(number('m'),2,100000),'rotation_rad':array(number('rad'),2,100000),'force_n':array(number('N'),2,100000),'torque_nm':array(number('N*m'),2,100000)})
cap('analysis.energy',ENERGY,obj({'energy_j':number('J')}),['controller'],'独立外部力/扭矩轨迹梯形积分，不读过程真值','analysis',example={'position_m':[0.,.01,.02],'rotation_rad':[0.,1.,2.],'force_n':[1.,1.,1.],'torque_nm':[.01,.01,.01]})
SHEAR=obj({'normal_stress_pa':array(number('Pa',0),3,10000),'shear_stress_pa':array(number('Pa',0),3,10000)})
cap('analysis.shear_yield',SHEAR,obj({'cohesion_pa':number('Pa',0),'friction_angle_rad':number('rad',0),'unconfined_yield_strength_pa':number('Pa',0),'residual_pa':number('Pa',0)}),['controller'],'外部屈服轨迹拟合Mohr-Coulomb；不冒称标准完整固结或料斗设计','analysis',example={'normal_stress_pa':[1000.,2000.,3000.],'shear_stress_pa':[1000.,1500.,2000.]})
DARCY=obj({'pressure_drop_pa':number('Pa',1e-6,1e6),'flow_m3_s':number('m^3/s',1e-15,1e-3),'bed_height_m':number('m',1e-6,1.),'area_m2':number('m^2',1e-8,.1),'gas_viscosity_pa_s':number('Pa*s',1e-6,1e-3)})
cap('analysis.permeability',DARCY,obj({'permeability_m2':number('m^2',0)}),['controller'],'外部压差/实际流量/床高按Darcy计算，低压差层流近似','analysis',example={'pressure_drop_pa':100.,'flow_m3_s':1e-6,'bed_height_m':.04,'area_m2':math.pi*.025**2,'gas_viscosity_pa_s':1.85e-5})
cap('ledger.read',obj({}),LEDGER,['controller'],'宿主粉体/氮气/机械耗散与热账本','query','evaluator')
cap('sim.step',obj({'dt_s':number('s',0,3600)}),obj({'time_s':number('s')}),['controller'],'唯一宿主时间推进','simulation','evaluator',{'dt_s':.1})
UNSUPPORTED=['dem.particles','noise.empirical','accuracy.certification','robot.tool_change','hardware.connect','liquid.viscosity','hopper.design_standard','electrostatics.measure']
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'未实现或未经认证：'+k)
for k,z,c in [('plant.configured','显式工程参数就绪','controller'),('sample.present','杯内有限粉体','sample_vessel'),('power.enabled','供电','controller'),('clamp.locked','夹具锁紧','vessel_clamp'),('gas.enabled','气路开启','gas_manifold')]:slot(k,BOOL,'1',z,component=c)
for k,u,lo,hi,z,c in [('axis.position_m','m',0,MAX_STROKE,'轴下行位移，0上端','drive_head'),('spindle.angle_rad','rad',-math.pi,math.pi,'旋转可见姿态，累计转角另记','spindle'),('clamp.release_m','m',0,.003,'人工拇指螺钉松开轴向代理','vessel_clamp'),('service.slide_m','m',0,.060,'服务盖位移','service_cover')]:slot(k,number(u,lo,hi),u,z,'scene',component=c)
slot('tool.type',enum(['blade','shear','piston','permeability']),'1','当前过程附件；场景仅绑定已验证变体',component='drive_head')
for k,u,z,c in [('axis.target_m','m','轴向目标','drive_head'),('axis.speed_target_m_s','m/s','轴速度上限目标','drive_head'),('spindle.target_rad_s','rad/s','旋转目标','spindle'),('gas.target_m3_s','m^3/s','已声明温压下气体实际流量目标','gas_manifold')]:slot(k,number(u),u,z,'setpoint',component=c)
for k,u,z,c in [('force.indicated_n','N','1e-4N量化阻力，未认证误差或噪声','load_cell'),('torque.indicated_nm','N*m','2e-6Nm量化扭矩，未认证准确度','load_cell'),('pressure.indicated_pa','Pa','样品床上下差压，工程传感器响应','gas_manifold')]:slot(k,nullable(number(u)),u,z,component=c)
for k,u,z,c in [('spindle.rotation_total_rad','rad','用于积分的连续累计转角','spindle'),('spindle.speed_rad_s','rad/s','当前理想运动角速度','spindle'),('gas.indicated_m3_s','m^3/s','实际转移气流的理想观测','gas_manifold')]:slot(k,number(u),u,z,component=c)
for k,u,z in [('truth.powder_mass_kg','kg','有限粉体质量'),('truth.packing_ratio','1','相对条件化体积密度'),('truth.history_factor','1','填装历史工程状态'),('truth.bulk_density_kg_m3','kg/m^3','粉体体积密度'),('truth.bed_height_m','m','固定截面粉床高度'),('truth.temperature_k','K','等效耗散热节点'),('truth.gas_stock_mol','mol','有限氮气库存'),('truth.force_n','N','真实阻力，非实测'),('truth.torque_nm','N*m','真实扭矩，非实测'),('truth.pressure_drop_pa','Pa','Darcy差压，非实测'),('truth.work_j','J','累计真实机械耗散')]:slot(k,number(u),u,z,'process_truth','evaluator')
slot('clock.time_s',number('s',0),'s','host时钟');slot('acquisition.phase',PHASE,'1','操作阶段');slot('acquisition.elapsed_s',number('s',0),'s','操作时间');slot('acquisition.segment',TEXT,'1','当前预处理/测量路径段');slot('acquisition.cycle',integer(),'1','测量循环零基索引');slot('acquisition.points',integer(),'1','已完成段数');slot('acquisition.work_j',number('J'),'J','当前段观测积分功')
PHYSICS_EFFECTS=['clock.time_s','axis.position_m','spindle.angle_rad','spindle.rotation_total_rad','spindle.speed_rad_s','axis.target_m','axis.speed_target_m_s','spindle.target_rad_s','force.indicated_n','torque.indicated_nm','pressure.indicated_pa','gas.indicated_m3_s','truth.packing_ratio','truth.history_factor','truth.bulk_density_kg_m3','truth.bed_height_m','truth.temperature_k','truth.gas_stock_mol','truth.force_n','truth.torque_nm','truth.pressure_drop_pa','truth.work_j','acquisition.phase','acquisition.elapsed_s','acquisition.segment','acquisition.cycle','acquisition.points','acquisition.work_j','gas.enabled']
CAPS['clamp.set']['component_ids']=['vessel_clamp','clamp_left','clamp_right']
CAPS['sim.step']['effects']=PHYSICS_EFFECTS
CAPS['gas.set_flow']['input_schema']['properties']['flow_m3_s']['x-reference-conditions']='Actual volume at evaluator-supplied gas_temperature_k and gas_pressure_pa; no implicit STP conversion'

class FreemanFt4powderRuntime:
 def __init__(self,instance_id='ft4powder_01'):
  self.instance_id=instance_id;self.plant=None;self.sample=None;self.reference=None;self.method=copy.deepcopy(METHOD_EXAMPLE);self.power=False;self.door=True;self.clamp=False;self.service=0.;self.tool='blade';self.position=0.;self.axis_target=0.;self.axis_speed=.03;self.rotation=0.;self.omega=0.;self.omega_target=0.;self.flow_target=0.;self.gas_enabled=False;self.gas_flow=0.;self.gas_stock=0.;self.gas_loaded=0.;self.gas_exhaust=0.;self.powder_loaded=0.;self.powder_recovered=0.;self.packing=0.;self.history=0.;self.temperature=298.15;self.mechanical=0.;self.gas_work=0.;self.heat_loss=0.;self.force=0.;self.torque=0.;self.pressure=0.;self.force_sensor=0.;self.torque_sensor=0.;self.pressure_sensor=0.;self.zero={'force_zero_n':0.,'torque_zero_nm':0.,'pressure_zero_pa':0.};self.phase='idle';self.elapsed=0.;self.segment='idle';self.cycle=0;self.segment_elapsed=0.;self.segment_work=0.;self.points=[];self.rows=[];self.segments=[];self.segment_index=0;self.operation_id=None;self.result_reference=None;self.source=None;self.result_mode=None;self.operation_method=None;self.error=None;self.anchors={};self.result_anchor=None;self._counter=0;self.records={};self.time_s=0.;self._ticks=0;self._remainder=0.;self.revision=0;self._requests={};self._owners={};self._principals={'agent':'agent','evaluator':'evaluator'}
 def register_principal(self,principal_id,visibility='agent'):
  if not isinstance(principal_id,str) or not principal_id or principal_id in self._principals or visibility not in ('agent','debug','evaluator'):raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='agent'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return FreemanFt4powderSession(self,principal_id)
 def describe(self):return export_freeman_ft4powder_contract_metadata()
 def _area(self):return math.pi*(self.plant['vessel_diameter_m']/2)**2
 def _density(self):return self.sample['conditioned_bulk_density_kg_m3']*self.packing if self.sample else 0.
 def _height(self):return self.sample['mass_kg']/(self._density()*self._area()) if self.sample else 0.
 def _shear_factor(self):return 2*math.pi/3*(self.plant['shear_outer_radius_m']**3-self.plant['shear_inner_radius_m']**3)
 def _indications(self):return (round((self.force_sensor-self.zero['force_zero_n'])/1e-4)*1e-4,round((self.torque_sensor-self.zero['torque_zero_nm'])/2e-6)*2e-6,self.pressure_sensor-self.zero['pressure_zero_pa'])
 def read(self,role='evaluator'):
  f,t,p=self._indications();d={'plant.configured':self.plant is not None,'sample.present':self.sample is not None,'power.enabled':self.power,'door.angle_rad':1.65 if self.door else 0.,'clamp.locked':self.clamp,'clamp.release_m':0. if self.clamp else .003,'service.slide_m':self.service,'tool.type':self.tool,'axis.position_m':self.position,'axis.target_m':self.axis_target,'axis.speed_target_m_s':self.axis_speed,'spindle.angle_rad':(self.rotation+math.pi)%(2*math.pi)-math.pi,'spindle.rotation_total_rad':self.rotation,'spindle.speed_rad_s':self.omega,'spindle.target_rad_s':self.omega_target,'gas.target_m3_s':self.flow_target,'gas.enabled':self.gas_enabled,'gas.indicated_m3_s':self.gas_flow,'force.indicated_n':f if self.power else None,'torque.indicated_nm':t if self.power else None,'pressure.indicated_pa':p if self.power else None,'truth.powder_mass_kg':self.sample['mass_kg'] if self.sample else 0.,'truth.packing_ratio':self.packing,'truth.history_factor':self.history,'truth.bulk_density_kg_m3':self._density(),'truth.bed_height_m':self._height(),'truth.temperature_k':self.temperature,'truth.gas_stock_mol':self.gas_stock,'truth.force_n':self.force,'truth.torque_nm':self.torque,'truth.pressure_drop_pa':self.pressure,'truth.work_j':self.mechanical,'clock.time_s':self.time_s,'acquisition.phase':self.phase,'acquisition.elapsed_s':self.elapsed,'acquisition.segment':self.segment,'acquisition.cycle':self.cycle,'acquisition.points':len(self.points),'acquisition.work_j':self.segment_work}
  return {k:copy.deepcopy(v) for k,v in d.items() if role!='agent' or SLOTS[k]['visibility']=='agent'}
 def _ledger(self):
  powder=self.sample['mass_kg'] if self.sample else 0.;pe=self.powder_loaded-powder-self.powder_recovered;ge=self.gas_loaded-self.gas_stock-self.gas_exhaust;stored=self.plant['thermal_capacity_j_k']*(self.temperature-self.plant['ambient_k']) if self.plant else 0.
  return dict(powder_loaded_kg=self.powder_loaded,powder_present_kg=powder,powder_recovered_kg=self.powder_recovered,powder_error_kg=pe,gas_loaded_mol=self.gas_loaded,gas_present_mol=self.gas_stock,gas_exhaust_recovered_mol=self.gas_exhaust,gas_error_mol=ge,mass_error_kg=pe+ge*MN,mechanical_dissipation_j=self.mechanical,gas_dissipation_j=self.gas_work,heat_loss_j=self.heat_loss,stored_heat_j=stored,energy_error_j=self.mechanical+self.gas_work-self.heat_loss-stored)
 def _result(self):
  if self.phase!='running' and self.operation_id in self.records:return copy.deepcopy(self.records[self.operation_id])
  down=[x for x in self.points if x['kind']=='down'];up=[x for x in self.points if x['kind']=='up'];m=self.operation_method
  # The seventh conditioned down-test is the conventional BFE selection, never the first downstroke.
  bfe=down[6]['energy_j'] if len(down)>=7 and m and m['conditioning_cycles']>=1 else None
  return dict(operation_id=self.operation_id,phase=self.phase,elapsed_s=self.elapsed,mode=self.result_mode,rows=copy.deepcopy(self.rows),points=copy.deepcopy(self.points),basic_flowability_energy_j=bfe,specific_energy_j_kg=up[-1]['specific_energy_j_kg'] if up else None,reference_id=self.result_reference,template_source=self.source,reference_anchor_json=self.result_anchor,evidence='Published powder BFE/SE scalar anchors; generated force/torque traces, path, force-work partition, history/packing, rate/volume scaling and continuum shear/compression/permeability are explicit engineering assumptions. BFE field selects seventh conditioned downstroke of this configured protocol, not a claim of reproducing original unknown acquisition path. SE divides observed upstroke energy by independently supplied weighed_mass_kg. No DEM or native raw trace/accuracy claim.',error_code=self.error)
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _ready(self):
  if not self.plant or not self.power or self.service>1e-9:raise ActionRejected('NOT_READY')
  if self.door:raise ActionRejected('DOOR_INTERLOCK')
  if self.temperature-self.plant['ambient_k']>self.plant['maximum_temperature_rise_k']:raise ActionRejected('THERMAL_MODEL_RANGE')
 def _access(self):
  if self.position>1e-9 or abs(self.omega)>1e-9 or abs(self.omega_target)>1e-9 or self.gas_enabled or self.clamp or not self.door:raise ActionRejected('SAMPLE_ACCESS')
 def _method_check(self,m):
  if not self.sample:raise ActionRejected('NO_SAMPLE')
  if m['start_position_m']>=m['end_position_m']:raise ActionRejected('PATH_RANGE')
  if m['end_position_m']-m['start_position_m']>self._height()+1e-12:raise ActionRejected('POWDER_DEPTH')
  if m['gas_flow_m3_s']>self.plant['maximum_gas_flow_m3_s']:raise ActionRejected('FLOW_RANGE')
  if max(m['normal_stresses_pa'])*self._area()>50.:raise ActionRejected('FORCE_RANGE')
  if m['mode']=='permeability' and m['gas_flow_m3_s']<=0:raise ActionRejected('FLOW_RANGE')
  # Original path and component-resolved signals are unavailable: all generated trajectories require acceptance.
  if not self.sample['accept_condition_scaling']:raise ActionRejected('CONDITION_SCALING_NOT_ACCEPTED')
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[k]['input_schema'],a)
  if k=='sim.step':self.step(a['dt_s']);return {'time_s':self.time_s}
  before=copy.deepcopy(self.__dict__)
  try:
   result=self._invoke(k,a);validate(CAPS[k]['output_schema'],result)
   if CAPS[k]['kind'] not in ('query','analysis'):self.revision+=1
   return result
  except Exception:self.__dict__.clear();self.__dict__.update(before);raise
 def _invoke(self,k,a):
  if k=='acquisition.result':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='reference.query':
   r=library()['substances'].get(a['reference_id'])
   if r is None:raise ActionRejected('INVALID_REFERENCE')
   return dict(reference_json=json.dumps(r,sort_keys=True),template_source=json.dumps(r['source'],sort_keys=True),evidence=json.dumps(r['evidence'],sort_keys=True))
  if k=='analysis.energy':
   if len({len(v) for v in a.values()})!=1:raise ActionRejected('INVALID_TIME_SERIES')
   z=np.asarray(a['position_m']);t=np.asarray(a['rotation_rad']);f=np.asarray(a['force_n']);q=np.asarray(a['torque_nm']);return {'energy_j':float(np.sum((f[1:]+f[:-1])/2*np.diff(z)+(q[1:]+q[:-1])/2*np.diff(t)))}
  if k=='analysis.shear_yield':
   x=np.asarray(a['normal_stress_pa']);y=np.asarray(a['shear_stress_pa'])
   if len(x)!=len(y) or np.any(np.diff(x)<=0):raise ActionRejected('INVALID_TIME_SERIES')
   design=np.column_stack([x,np.ones(len(x))]);mu,c=np.linalg.lstsq(design,y,rcond=None)[0]
   if mu<0 or c<0:raise ActionRejected('INVALID_YIELD_LOCUS')
   phi=math.atan(mu);return dict(cohesion_pa=float(c),friction_angle_rad=phi,unconfined_yield_strength_pa=float(2*c*math.cos(phi)/(1-math.sin(phi))),residual_pa=float(np.sqrt(np.mean((design@np.array([mu,c])-y)**2))))
  if k=='analysis.permeability':return {'permeability_m2':a['gas_viscosity_pa_s']*a['flow_m3_s']*a['bed_height_m']/(a['area_m2']*a['pressure_drop_pa'])}
  if k=='door.set':
   if a['opened']:
    self.axis_target=self.position;self.omega_target=self.omega=0.;self.gas_enabled=False
    if self.phase=='running':self._finish('failed','DOOR_INTERLOCK')
   self.door=a['opened'];return a
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self.axis_target=self.position;self.omega_target=0.;self.gas_enabled=False;self._finish('cancelled');return {'phase':self.phase}
  self._idle()
  if k=='plant.configure':
   if self.plant is not None:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   if not a['shear_inner_radius_m']<a['shear_outer_radius_m']<=a['vessel_diameter_m']/2 or a['blade_radius_m']>=a['vessel_diameter_m']/2:raise ActionRejected('ATTACHMENT_GEOMETRY')
   self.plant=copy.deepcopy(a);self.temperature=a['ambient_k'];return {'configured':True}
  if k=='sample.load':
   self._access()
   if not self.plant or self.sample:raise ActionRejected('SAMPLE_PRESENT')
   if not a['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   r=library()['substances'].get(a['reference_id'])
   if r is None or r.get('runtime_usable') is not True:raise ActionRejected('INVALID_REFERENCE')
   if not r.get('runtime_anchors'):raise ActionRejected('INVALID_REFERENCE')
   anchors=resolve_anchors(r,a)
   rho=a['conditioned_bulk_density_kg_m3']*a['initial_packing_ratio'];volume=a['mass_kg']/rho
   if rho>=.74*a['solid_density_kg_m3']:raise ActionRejected('PACKING_RANGE')
   if volume>self.plant['vessel_volume_m3']+1e-15:raise ActionRejected('VESSEL_CAPACITY')
   self.sample=copy.deepcopy(a);self.reference=copy.deepcopy(r);self.anchors=anchors;self.packing=a['initial_packing_ratio'];self.history=a['initial_history_factor'];self.powder_loaded+=a['mass_kg'];return {'loaded':True}
  if k=='sample.remove':
   self._access()
   if not self.sample:raise ActionRejected('NO_SAMPLE')
   self.powder_recovered+=self.sample['mass_kg'];self.sample=None;self.reference=None;self.anchors={};self.packing=self.history=0.;return {'removed':True}
  if k in ('gas.load','gas.unload'):
   if not self.plant or self.gas_enabled:raise ActionRejected('GAS_ACCESS')
   if k=='gas.unload':self.gas_exhaust+=self.gas_stock;self.gas_stock=0.;return {'unloaded':True}
   if self.gas_stock>0:raise ActionRejected('STOCK_PRESENT')
   self.gas_stock=a['amount_mol'];self.gas_loaded+=self.gas_stock;return {'loaded':True}
  if k=='power.set':
   if a['enabled'] and (not self.plant or self.service>1e-9):raise ActionRejected('NOT_READY')
   self.power=a['enabled']
   if not self.power:self.axis_target=self.position;self.omega_target=self.omega=0.;self.gas_enabled=False
   return a
  if k=='clamp.set':
   if self.position>1e-9 or abs(self.omega)>1e-9 or not self.door or self.gas_enabled:raise ActionRejected('SAMPLE_ACCESS')
   self.clamp=a['locked'];return a
  if k=='service.set':
   if self.power:raise ActionRejected('POWER_INTERLOCK')
   self.service=a['distance_m'];return a
  if k=='tool.select':
   if self.position>1e-9 or abs(self.omega)>1e-9 or abs(self.omega_target)>1e-9 or not self.door or self.gas_enabled:raise ActionRejected('TOOL_ACCESS')
   self.tool=a['tool_type'];return a
  if k=='axis.move':
   self._ready()
   if self.sample and not self.clamp:raise ActionRejected('CLAMP_REQUIRED')
   self.axis_target=a['position_m'];self.axis_speed=a['speed_m_s'];return {'position_m':self.axis_target}
  if k=='spindle.set_speed':
   self._ready()
   if self.sample and not self.clamp:raise ActionRejected('CLAMP_REQUIRED')
   self.omega_target=a['angular_speed_rad_s'];return a
  if k=='gas.set_flow':
   if not self.plant or a['flow_m3_s']>self.plant['maximum_gas_flow_m3_s']:raise ActionRejected('FLOW_RANGE')
   self.flow_target=a['flow_m3_s'];return a
  if k=='gas.open':
   if a['enabled']:
    self._ready()
    if not self.sample or not self.clamp or self.gas_stock<=0 or self.flow_target<=0:raise ActionRejected('FINITE_INVENTORY')
   self.gas_enabled=a['enabled'];return a
  if k=='sensor.calibrate':self.zero=copy.deepcopy(a);return a
  if k=='method.configure':self._method_check(a);self.method=copy.deepcopy(a);return a
  if k=='acquisition.start':
   self._ready();self._method_check(self.method)
   if not self.clamp:raise ActionRejected('CLAMP_REQUIRED')
   expected={'dynamic':'blade','shear':'shear','compression':'piston','permeability':'permeability'}[self.method['mode']]
   if self.tool!=expected:raise ActionRejected('ATTACHMENT_REQUIRED')
   # Dynamic energies must be anchored by a measured BFE (known reference volume) or SE; no fully assumed flow-energy run.
   if self.method['mode']=='dynamic' and 'dynamic' not in self.anchors:raise ActionRejected('REFERENCE_MODE_UNANCHORED')
   if self.method['mode']=='permeability' and (not self.gas_enabled or self.gas_stock<=0):raise ActionRejected('FINITE_INVENTORY')
   self.operation_method=copy.deepcopy(self.method);self.compression_initial_height=self._height();self.flow_target=self.method['gas_flow_m3_s'] if self.method['mode']=='permeability' else self.flow_target;self._counter+=1;self.operation_id=self.instance_id+':'+str(self._counter);self.result_reference=self.sample['reference_id'];self.result_anchor=json.dumps(dict(mode=self.method['mode'],anchored_modes=sorted(self.anchors),anchor=self.anchors.get(self.method['mode']),engineering_inputs_only=self.method['mode'] not in self.anchors),sort_keys=True);self.source=json.dumps(self.reference['source'],sort_keys=True);self.result_mode=self.method['mode'];self.phase='running';self.error=None;self.elapsed=0.;self.rows=[];self.points=[];self.segments=[dict(kind='position',target=self.method['start_position_m'],cycle=0)]
   if self.method['mode']=='dynamic':
    for group,count in [('condition',self.method['conditioning_cycles']),('measure',self.method['measurement_cycles'])]:
     for i in range(count):self.segments.extend([dict(kind=group+'_down',target=self.method['end_position_m'],cycle=i),dict(kind=group+'_up',target=self.method['start_position_m'],cycle=i)])
   else:
    for i,stress in enumerate(self.method['normal_stresses_pa']):self.segments.append(dict(kind=self.method['mode'],target=self.method['start_position_m'],cycle=i,stress=stress))
   self.segment_index=0;self.axis_speed=self.method['axial_speed_m_s'];self._activate_segment();return {'operation_id':self.operation_id}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _activate_segment(self):
  seg=self.segments[self.segment_index];self.segment=seg['kind'];self.cycle=seg['cycle'];self.axis_target=seg['target'] if self.segment=='position' or '_' in self.segment else self.position;self.axis_speed=MAX_VELOCITY if self.segment=='position' else self.method['axial_speed_m_s'];self.segment_elapsed=0.;self.segment_work=0.;self.omega_target=0. if self.segment in ['position','compression','permeability'] else self.method['angular_speed_rad_s']*(-1 if self.segment.endswith('_up') else 1);self._observe()
 def _observe(self):
  f,t,p=self._indications();self.rows.append(dict(time_s=self.time_s,elapsed_s=self.elapsed,segment=self.segment,cycle=self.cycle,position_m=self.position,rotation_rad=self.rotation,force_n=f,torque_nm=t,pressure_drop_pa=p,gas_flow_m3_s=self.gas_flow,observed_work_j=self.segment_work))
 def _finish(self,phase,error=None):
  self.phase=phase;self.error=error;self.axis_target=self.position;self.omega_target=self.omega=0.
  if self.operation_id:self.records[self.operation_id]=self._result()
 def _permeability(self):
  phi=1-self._density()/self.sample['solid_density_kg_m3'];phi0=1-self.sample['conditioned_bulk_density_kg_m3']/self.sample['solid_density_kg_m3'];return self.sample['permeability_m2']*(phi/phi0)**3*((1-phi0)/(1-phi))**2
 def _tick(self):
  if not self.plant:return
  p=self.plant;oldf,oldt,_=self._indications();oldz=self.position;oldrot=self.rotation;failure=None;energy=0.;gas_energy=0.;self.force=self.torque=self.pressure=0.;self.gas_flow=0.;active=self.phase=='running';old_height=self._height();self.omega=0.
  if self.power and not self.door:
   delta=self.axis_target-self.position;dz=math.copysign(min(abs(delta),self.axis_speed*DT),delta) if delta else 0.;self.position+=dz;duration=abs(dz)/self.axis_speed if active and self.segment!='position' and ('_down' in self.segment or '_up' in self.segment) else DT;self.rotation+=self.omega_target*duration;self.omega=(self.rotation-oldrot)/DT
   if active and self.segment not in ['position','compression','permeability','shear']:
    sample=self.sample;direction=1. if self.segment.endswith('_down') else -1.;span=self.method['end_position_m']-self.method['start_position_m'];mid=(self.position+oldz)/2;u=(mid-self.method['start_position_m'])/span;shape=.5+u;values=self.anchors.get('dynamic',{});base=values.get('basic_flowability_energy_j') if direction>0 else values.get('specific_energy_j_kg');base=base if base is not None else sample['missing_bfe_j'] if direction>0 else sample['missing_specific_energy_j_kg'];reference_volume=values.get('reference_sample_volume_m3');volume=sample['mass_kg']/sample['conditioned_bulk_density_kg_m3'];scale=volume/reference_volume if reference_volume else 1.;base*=scale if direction>0 else sample['mass_kg'];tip=self.method['angular_speed_rad_s']*p['blade_radius_m'];reference_tip=values.get('reference_blade_tip_speed_m_s') or tip;rate=(tip/reference_tip)**sample['tip_speed_exponent'];aero=1/(1+sample['aeration_coefficient_s_m']*(self.flow_target/self._area() if self.gas_enabled else 0.));gradient=base/span*shape*self.packing**sample['packing_energy_exponent']*self.history*rate*aero;fraction=sample['force_work_fraction'];self.force=direction*fraction*gradient;self.torque=math.copysign((1-fraction)*gradient*self.axis_speed/self.method['angular_speed_rad_s'],self.omega_target);energy=max(0.,self.force*dz+self.torque*(self.rotation-oldrot))
    if self.segment.startswith('condition'):
     alpha=-math.expm1(-sample['conditioning_rate_s']*duration);self.packing+=(1-self.packing)*alpha;self.history+=(1-self.history)*alpha
   elif active and self.segment in ['shear','compression','permeability']:
    stress=self.segments[self.segment_index]['stress'];sample=self.sample;alpha=-math.expm1(-DT/p['compression_tau_s']);limit=.739*sample['solid_density_kg_m3']/sample['conditioned_bulk_density_kg_m3'];target=min(limit,1+stress/self.anchors.get('compression',sample)['compression_modulus_pa']);candidate=self.packing+(max(self.packing,target)-self.packing)*alpha;wanted_height=sample['mass_kg']/(sample['conditioned_bulk_density_kg_m3']*candidate*self._area());wanted_dz=max(0.,old_height-wanted_height);dz=min(wanted_dz,self.axis_speed*DT,MAX_STROKE-self.position);self.position+=dz;self.axis_target=self.position;self.packing=sample['mass_kg']/(max(1e-15,old_height-dz)*self._area()*sample['conditioned_bulk_density_kg_m3']);self.force=stress*self._area();energy=self.force*dz
    if wanted_dz>dz+1e-12 and self.position>=MAX_STROKE-1e-12:failure='AXIS_LIMIT'
    if self.segment=='shear':
     yl=self.anchors.get('shear',sample);shear=(yl['cohesion_pa']+stress*math.tan(yl['friction_angle_rad']))*(-math.expm1(-(self.segment_elapsed+DT)/p['shear_tau_s']));self.torque=shear*self._shear_factor();energy+=self.torque*(self.rotation-oldrot)
  if self.gas_enabled and self.power and not self.door:
   demand=self.flow_target*p['gas_pressure_pa']/(R*p['gas_temperature_k'])*DT;amount=min(demand,self.gas_stock);self.gas_stock=max(0.,self.gas_stock-amount);self.gas_exhaust+=amount;self.gas_flow=amount*R*p['gas_temperature_k']/p['gas_pressure_pa']/DT
   if self.sample:self.pressure=p['gas_viscosity_pa_s']*self._height()*self.gas_flow/(self._permeability()*self._area());gas_energy=self.pressure*self.gas_flow*DT
   if amount<demand:failure='GAS_EXHAUSTED';self.gas_enabled=False
  if abs(self.force)>50.:failure='FORCE_OVERLOAD'
  if abs(self.torque)>.9:failure='TORQUE_OVERLOAD'
  if self.pressure>p['maximum_pressure_drop_pa'] or self.pressure>.1*p['gas_pressure_pa']:failure='PRESSURE_MODEL_RANGE'
  self.mechanical+=energy;self.gas_work+=gas_energy;capacity=p['thermal_capacity_j_k'];h=p['heat_loss_w_k'];self.temperature=(capacity*self.temperature+energy+gas_energy+DT*h*p['ambient_k'])/(capacity+DT*h);self.heat_loss+=h*(self.temperature-p['ambient_k'])*DT
  if self.temperature-p['ambient_k']>p['maximum_temperature_rise_k']:failure='THERMAL_MODEL_RANGE'
  force_target=max(-50.,min(50.,self.force));torque_target=max(-.9,min(.9,self.torque));self.force_sensor=force_target+(self.force_sensor-force_target)*math.exp(-DT/p['force_tau_s']);self.torque_sensor=torque_target+(self.torque_sensor-torque_target)*math.exp(-DT/p['torque_tau_s']);self.pressure_sensor=self.pressure+(self.pressure_sensor-self.pressure)*math.exp(-DT/p['pressure_tau_s']);newf,newt,_=self._indications()
  if failure:self.gas_enabled=False
  if not active:return
  self.elapsed+=DT;self.segment_elapsed+=DT;self.segment_work+=(oldf+newf)/2*(self.position-oldz)+(oldt+newt)/2*(self.rotation-oldrot);self._observe()
  if failure:self.gas_enabled=False;self._finish('failed',failure);return
  finished=abs(self.position-self.axis_target)<1e-12 if self.segment=='position' or '_' in self.segment else self.segment_elapsed>=self.method['hold_s']-1e-9
  if not finished:return
  if self.segment.startswith('measure') or self.segment in ['shear','compression','permeability']:
   kind=self.segment.removeprefix('measure_');f,t,dp=self._indications();height=self._height();rho=self.method['weighed_mass_kg']/(height*self._area());initial_height=self.compression_initial_height;point=dict(kind=kind,cycle=self.cycle,energy_j=self.segment_work,specific_energy_j_kg=self.segment_work/self.method['weighed_mass_kg'] if kind=='up' else None,normal_stress_pa=f/self._area() if kind in ['shear','compression','permeability'] else None,shear_stress_pa=t/self._shear_factor() if kind=='shear' else None,bulk_density_kg_m3=rho if kind in ['compression','permeability'] else None,compressibility_fraction=1-height/initial_height if kind=='compression' else None,permeability_m2=p['gas_viscosity_pa_s']*self.gas_flow*height/(self._area()*dp) if kind=='permeability' and dp>0 else None);self.points.append(point)
  self.segment_index+=1
  if self.segment_index>=len(self.segments):self._finish('succeeded')
  else:self._activate_segment()
 def step(self,dt_s):
  validate(number('s',0,3600),dt_s);total=self._remainder+dt_s;n=int((total+1e-12)/DT);self._remainder=total-n*DT
  for _ in range(n):self._ticks+=1;self.time_s=self._ticks*DT;self._tick()
  self.revision+=n

class FreemanFt4powderSession:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,instance_id):
  if instance_id!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  meta=export_freeman_ft4powder_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.1.0',capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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
  self.poll(operation_id)
  if operation_id!=self._host.operation_id:raise ActionRejected('NOT_RUNNING')
  return self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)

def export_freeman_ft4powder_contract_metadata():
 slots={};caps={}
 for k,v in SLOTS.items():slots[k]=dict(meaning_zh=v['meaning'],component_ids=[v['component']],value_schema=copy.deepcopy(v['schema']),unit=v['unit'],reference_conditions='SI; axial position downward-positive from top; force and torque use drive-load sign so F*dz+T*dtheta is dissipative; gas flow actual volume at explicit gas temperature and absolute pressure; pressure sensor is bed differential pressure',origin=v['origin'],visibility=v['visibility'],access='read_write' if v['write'] else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'FreemanFt4powderSession.read(state_ids=[{k!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description='Fixed0.1s host ticks; force1e-4N and torque2e-6Nm quantization, explicitly assumed lag, no empirical noise or accuracy claim'),source_ids=['simulation_assumption'])
 errors=['UNKNOWN_CAPABILITY','UNKNOWN_INSTANCE','UNKNOWN_OPERATION','INVALID_ARGUMENT','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','BUSY','NOT_READY','NOT_RUNNING','INITIALIZATION_ONLY','MODEL_NOT_ACCEPTED','SAMPLE_ACCESS','SAMPLE_PRESENT','NO_SAMPLE','INVALID_REFERENCE','ATTACHMENT_GEOMETRY','PACKING_RANGE','VESSEL_CAPACITY','GAS_ACCESS','STOCK_PRESENT','POWER_INTERLOCK','TOOL_ACCESS','CLAMP_REQUIRED','FLOW_RANGE','FINITE_INVENTORY','PATH_RANGE','POWDER_DEPTH','FORCE_RANGE','CONDITION_SCALING_NOT_ACCEPTED','ATTACHMENT_REQUIRED','DOOR_INTERLOCK','GAS_EXHAUSTED','FORCE_OVERLOAD','TORQUE_OVERLOAD','PRESSURE_MODEL_RANGE','THERMAL_MODEL_RANGE','AXIS_LIMIT','INVALID_TIME_SERIES','INVALID_YIELD_LOCUS','REFERENCE_MODE_UNANCHORED']
 partial=['DOOR_INTERLOCK','GAS_EXHAUSTED','FORCE_OVERLOAD','TORQUE_OVERLOAD','PRESSURE_MODEL_RANGE','THERMAL_MODEL_RANGE','AXIS_LIMIT']
 for k,c in CAPS.items():
  un=k in UNSUPPORTED;async_=k=='acquisition.start';caps[k]={key:copy.deepcopy(c[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']};caps[k].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ['scene','robot','hardware'] else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.freeman_ft4powder.FreemanFt4powderSession.invoke',invocation=f'session.invoke(instance_id="ft4powder_01",capability_id={k!r},arguments={c["example_arguments"]!r},request_id="unique")'),preconditions=[] if c['kind'] in ['query','analysis'] or un else [dict(description_zh='实际参数、权限、有限库存、门/夹具/附件与运动前提；拒绝原子，非原机安全认证',state_ids=['power.enabled','plant.configured','acquisition.phase','door.angle_rad','clamp.locked'],enforced_by='runtime',check='atomic invoke and registered Session')],state_effects=[dict(state_id=x,timing='immediate',description_zh='实际命令变化') for x in c['effects']],observes=[],model_effects=dict(mode='none',description_zh='桥接姿态另行验证；粉体接触力/电机动力学及多附件场景尚不由此实现',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='完成预处理/动态循环或剪切压缩透气各应力步，或明确失败/取消' if async_ else '命令完成不推进时钟',completion_state_ids=['acquisition.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','drive_head','spindle','sample','gas'],partial_failure_zh='保留已发生packing/history、功耗、气耗、观测；冻结终态；安全门打开即停，关闭门不自动恢复'),errors=[dict(code=x,meaning_zh=x,side_effects='partial' if async_ and x in partial else 'none') for x in errors],verification=[],limitations=['过程粉体等效连续体，不是液体黏度或颗粒级DEM','原始BFE/SE仅统计标量；力/扭矩分配、轨迹与动态变换均显式工程假设','剪切/压缩/透气附件过程实现不等于场景已换装'],source_ids=['manufacturer_specs','simulation_assumption'])
  if async_ or k in ['axis.move','spindle.set_speed','gas.open','power.set']:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='宿主推进运动、粉体历史、有限气流、功耗及观测') for x in PHYSICS_EFFECTS if x!='clock.time_s']
 mech=dict(component_ids=['controller','drive_head','spindle','blade','sample_vessel','load_cell','gas_manifold'],state_ids=list(slots),meaning_zh='实测动态能量锚定粉体连续体、装填历史、剪切压缩和Darcy气流',implementation='lumped',equations=['E_observed=sum((F_i+F_i-1)/2*dz+(T_i+T_i-1)/2*dtheta), each segment starts with its actual sensor baseline; signed drive loads yield positive ideal dissipation','BFE: seventh conditioned downward test energy; fewer cycles or no conditioning ->null; SE=upstroke energy/independent user supplied weighed mass','Published BFE scales by (loadedmass/assumedconditioneddensity)/sourcevolume; SE anchor multiplies actual finite powder mass; unknown-path shape .5+normalized_depth and torque/force-work fraction are explicitly supplied/accepted engineering approximations','Dynamic resistance scales with packing_ratio^exponent, history_factor, explicit tip-speed power law and engineering aeration response; no measured free extrapolation or Newtonian-liquid viscosity inference','Conditioning exponentially relaxes packing/history to conditioned baseline; finite mass remains unchanged while volume and bedheight change','Compression: targetpacking=min(1+normalstress/assumedmodulus,packinglimit); one-way plastic relaxation limited by actual axial speed/stroke; W=normalforce*compression_displacement','Shear: tau=(cohesion+normalstress*tan(phi))*(1-exp(-t/shear_tau)); uniform annulus T=2*pi/3*tau*(outerradius^3-innerradius^3); coefficients explicit engineering inputs','Gas: dN=min(stock,P*Q*dt/(R*T)); pressure drop=viscosity*bedheight*Q/(permeability*area); relative Kozeny-Carman packing factor is assumed; pressure-drop/absolute-pressure<=0.1 guard','Mechanical and Darcy dissipation -> effective thermal node; implicit heat loss update preserves Emechanical+Egas=stored_heat+loss; thermal properties are not manufacturer measured','Powder loaded=remaining+recovered; gas loaded=stock+exhaust/recovered, no invented powder disappearance','External yield locus least squares tau=c+sigma*tan(phi); UYS=2*c*cos(phi)/(1-sin(phi)); not a certified full ASTM/flow-function/hopper design procedure','Measured anchors per loaded record: shear uses published (c,phi) when both measured; metal FFc+effective angle at pre-shear sigma_pre solves (c,phi) with tau_pre=c+sigma_pre*tan(phi), sigma_m=sigma_pre+tau_pre*tan(phi), R=tau_pre/cos(phi), sigma1=sigma_m+R, FFc=sigma1/UYS, sin(phi_e)=R/sigma_m; FFc-only records solve c=A/(FFc*D-B) for the explicit engineering phi (A=sigma_pre(1+sin)/cos^2, B=(1+sin)/cos, D=2cos/(1-sin)); compression modulus M=sigma_ref*(1-CPS)/CPS reproduces measured CPS at sigma_ref; unanchored modes keep explicit engineering coefficients; dynamic mode requires a measured BFE (known reference volume) or SE'],parameters={},source_ids=['manufacturer_specs','simulation_assumption'],limitations=['Manufacturer±50N,1e-4N force resolution;0.9Nm range,2e-6Nm torque resolution;120rpm,185mm stroke,30mm/s axial rate are implemented bounds, not accuracy qualification','Manufacturer environment10–40C enforced at plant setup;20–80%RH/noncondensing environment is not modeled and must not be claimed validated','Original reference blade angle text is not silently reinterpreted as helix angle; default axial path/speed independent engineering inputs within hardware bounds','All generated traces are mixed measured_scalar_anchor_plus_engineering_response, not nativeFT4 force/torque raw data; missing scalar anchors use explicitly provided fallback values and remain assumptions; FFc-only and cohesion-only shear anchors still need the explicit engineering friction angle; the linear Mohr-Coulomb locus with pre-shear point on the locus is an approximation of the FT4 software construction','No measured particle contacts, cohesive/dissipative DEM calibration, particle attrition, segregation or electrostatic simulation','Manual motion outside acquisition is a kinematic positioning proxy; force production and packing effects are modeled only in acquisition','Scene may validate blade only; shear head/piston/permeability process support does not prove physical tool exchange or accessory collision','Power shutdown/cancel and safetydoor freeze actual partial effects; a new acquisition.start is required after door closure','Registered Session filters truth and owner-bound result/poll/cancel; trusted low-level Runtime is not filesystem/process isolation','Gas temperature/viscosity, thermal capacity, maximum temperature rise, packing law, generated profile and sensor lag are useraccepted assumptions; thermal-domain excess explicitly fails; empirical noise and OEM calibration remain not_run'])
 spec=importlib.util.spec_from_file_location('_ft4_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()});ann.annotate_export(SLUG,caps,slots,{'powder_mechanics':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'powder_mechanics':mech})

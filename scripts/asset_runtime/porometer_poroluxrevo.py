"""CHEM-125 capillary flow porometry: finite gas/liquid, wet-then-dry through-pore analysis."""
from pathlib import Path
import copy,json,math,importlib.util
import numpy as np
PROTOCOL_VERSION='1.0.0';SLUG='porometer_poroluxrevo';CATALOG_ID='CHEM-125';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch03/t4_measure'/SLUG
DT=.1;R=8.31446261815324;MN=.0280134;MAX_PRESSURE=3.45e6;MAX_FLOW=200e-3/60
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
 p=ASSET/'porometry_library.json';return json.loads(p.read_text()) if p.exists() else {'substances':{}}
PLANT_EXAMPLE=dict(accept_engineering=True,temperature_k=298.15,ambient_pressure_pa=101325.,gas_volume_m3=1e-5,flow_reference_temperature_k=298.15,flow_reference_pressure_pa=101325.,gas_cv_j_mol_k=20.8,regulator_tau_s=.2,vent_tau_s=.2,pressure_sensor_tau_s=.1,flow_sensor_tau_s=.1,pressure_safe_open_pa=50.,pressure_calibration_gain=1.,pressure_calibration_offset_pa=0.,flow_calibration_gain=1.,flow_calibration_offset_m3_s=0.)
PLANT=obj({'accept_engineering':BOOL,'temperature_k':number('K',278.15,323.15),'ambient_pressure_pa':number('Pa',80000,120000),'gas_volume_m3':number('m^3',1e-8,.01),'flow_reference_temperature_k':number('K',273.15,323.15),'flow_reference_pressure_pa':number('Pa',80000,120000),'gas_cv_j_mol_k':number('J/(mol*K)',10,100),'regulator_tau_s':number('s',.001,100),'vent_tau_s':number('s',.001,100),'pressure_sensor_tau_s':number('s',.001,100),'flow_sensor_tau_s':number('s',.001,100),'pressure_safe_open_pa':number('Pa',0,100),'pressure_calibration_gain':number('1',.1,10),'pressure_calibration_offset_pa':number('Pa',-10000,10000),'flow_calibration_gain':number('1',.1,10),'flow_calibration_offset_m3_s':number('m^3/s',-1e-5,1e-5)})
SAMPLE_EXAMPLE=dict(reference_id='pvdf_fc43_2016',diameter_m=.025,active_diameter_m=.0184,dry_mass_kg=1e-4,liquid_capacity_kg=1e-6,drain_tau_s=.05,accept_engineering=True,accept_unknown_conditions=True,accept_pressure_transform=False,accept_monotone_projection=True)
SAMPLE=obj({'reference_id':TEXT,'diameter_m':{'type':'number','enum':[.013,.025,.047],'x-unit':'m'},'active_diameter_m':number('m',.001,.047),'dry_mass_kg':number('kg',1e-9,.1),'liquid_capacity_kg':number('kg',1e-12,.01),'drain_tau_s':number('s',.001,1000),'accept_engineering':BOOL,'accept_unknown_conditions':BOOL,'accept_pressure_transform':BOOL,'accept_monotone_projection':BOOL})
LIQUID_EXAMPLE=dict(name='FC-43',mass_kg=.001,density_kg_m3=1800.,surface_tension_n_m=.016,contact_angle_rad=0.,accept_engineering=True)
LIQUID=obj({'name':TEXT,'mass_kg':number('kg',1e-15,1.),'density_kg_m3':number('kg/m^3',100,3000),'surface_tension_n_m':number('N/m',.001,.1),'contact_angle_rad':number('rad',0,1.55),'accept_engineering':BOOL})
GAS_EXAMPLE=dict(amount_mol=1.);GAS=obj({'amount_mol':number('mol',1e-15,1000)})
METHOD_EXAMPLE=dict(mode='wet_dry',pressure_steps_pa=[5000.,15000.,24000.,31000.,43000.,60000.],minimum_dwell_s=.5,maximum_dwell_s=3.,stability_window_s=.3,pressure_tolerance_pa=100.,flow_slope_tolerance_m3_s2=1e-8,bubble_fraction=.05)
METHOD=obj({'mode':enum(['wet','dry','wet_dry']),'pressure_steps_pa':array(number('Pa',0,MAX_PRESSURE),3,100),'minimum_dwell_s':number('s',.1,3600),'maximum_dwell_s':number('s',.2,3600),'stability_window_s':number('s',.2,60),'pressure_tolerance_pa':number('Pa',.1,10000),'flow_slope_tolerance_m3_s2':number('m^3/s^2',1e-15,1),'bubble_fraction':number('1',1e-6,.49)})
ROW=obj({'time_s':number('s'),'elapsed_s':number('s'),'scan':enum(['wet','dry','vent']),'step':integer(),'pressure_pa':number('Pa'),'flow_m3_s':number('m^3/s'),'target_pressure_pa':number('Pa')})
POINT=obj({'pressure_pa':number('Pa'),'flow_m3_s':number('m^3/s'),'target_pressure_pa':number('Pa'),'dwell_s':number('s'),'stable':BOOL})
ANALYSIS=obj({'bubble_pressure_pa':nullable(number('Pa')),'bubble_diameter_m':nullable(number('m')),'mean_flow_pressure_pa':nullable(number('Pa')),'mean_flow_diameter_m':nullable(number('m')),'pressure_pa':array(number('Pa'),0,10000),'diameter_m':array(number('m'),0,10000),'cumulative_flow_fraction':array(number('1'),0,10000),'differential_flow_fraction':array(number('1'),0,10000),'covered_flow_fraction':number('1'),'unobserved_flow_fraction':number('1'),'projection_changes':integer(),'reasons':array(TEXT,0,100)})
RESULT=obj({'operation_id':nullable(TEXT),'phase':PHASE,'elapsed_s':number('s'),'rows':array(ROW,0,1000000),'wet_points':array(POINT,0,10000),'dry_points':array(POINT,0,10000),'analysis':nullable(ANALYSIS),'reference_id':nullable(TEXT),'template_source':nullable(TEXT),'evidence':TEXT,'error_code':nullable(TEXT),'analysis_error_code':nullable(TEXT)})
LEDGER=obj({k:number(u) for k,u in [('gas_loaded_mol','mol'),('gas_present_mol','mol'),('gas_exhaust_recovered_mol','mol'),('gas_error_mol','mol'),('liquid_loaded_kg','kg'),('liquid_present_kg','kg'),('liquid_trap_recovered_kg','kg'),('liquid_error_kg','kg'),('membrane_loaded_kg','kg'),('membrane_present_kg','kg'),('membrane_recovered_kg','kg'),('mass_error_kg','kg'),('gas_enthalpy_in_j','J'),('gas_enthalpy_out_j','J'),('isothermal_heat_removed_j','J'),('gas_stored_energy_j','J'),('gas_energy_error_j','J')]})
CAPS={};SLOTS={}
def cap(k,i,o,c,z,kind='device_command',visibility='agent',example=None,effects=None):CAPS[k]=dict(input_schema=i,output_schema=o,component_ids=c,function_zh=z,kind=kind,visibility=visibility,example_arguments=example or {},effects=effects or [])
def slot(k,s,u,z,origin='derived',visibility='agent',component='controller',write=None):SLOTS[k]=dict(schema=s,unit=u,meaning=z,origin=origin,visibility=visibility,component=component,write=write)
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller','pressure_controller'],'显式接受等温有限气室、流量参考温压、传感滞后和安全开盖阈值','configuration','evaluator',PLANT_EXAMPLE,['plant.configured','truth.gas_cell_mol','truth.temperature_k'])
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_holder'],'降压开夹后装同一膜片；实测曲线与有限润湿容量分开','configuration','evaluator',SAMPLE_EXAMPLE,['sample.present','sample.wetted','truth.membrane_mass_kg','truth.open_fraction','truth.sample_liquid_kg'])
cap('sample.remove',obj({}),obj({'removed':BOOL}),['sample_holder'],'安全卸样，残液与膜片入回收账本','configuration','evaluator',effects=['sample.present','sample.wetted','truth.membrane_mass_kg','truth.open_fraction','truth.sample_liquid_kg'])
cap('liquid.load',LIQUID,obj({'loaded':BOOL}),['liquid_trap'],'宿主登记有限润湿液及其参数；不声称参数为厂家测定','configuration','evaluator',LIQUID_EXAMPLE,['truth.liquid_stock_kg'])
cap('liquid.unload',obj({}),obj({'unloaded':BOOL}),['liquid_trap'],'回收有限润湿液库存','configuration','evaluator',effects=['truth.liquid_stock_kg'])
cap('sample.wet',obj({}),obj({'wetted':BOOL}),['sample_holder'],'开夹低压下从有限库存补足润湿；清除旧湿干关联',effects=['sample.wetted','truth.open_fraction','truth.sample_liquid_kg','truth.liquid_stock_kg'])
cap('gas.load',GAS,obj({'loaded':BOOL}),['gas_inlet'],'宿主装入有限氮气','configuration','evaluator',GAS_EXAMPLE,['truth.gas_stock_mol'])
cap('gas.unload',obj({}),obj({'unloaded':BOOL}),['gas_inlet'],'进气关闭后回收库存，不清空气室残压','configuration','evaluator',effects=['truth.gas_stock_mol'])
for k,a,sc,c,z,ex,eff in [('power.set','enabled',BOOL,'controller','供电',True,['power.enabled','power.angle_rad','pressure.indicated_pa','flow.indicated_m3_s','gas.enabled']),('holder.set','opened',BOOL,'sample_lid','样品夹具人工开闭；须真实降压',False,['holder.open','holder.lift_m']),('service.set','distance_m',number('m',0,.08),'service_cover','低压断电服务盖',.08,['service.slide_m']),('gas.open','enabled',BOOL,'gas_inlet','有限进气调压通道',True,['gas.enabled'])]:cap(k,obj({a:sc}),obj({a:sc}),[c],z,example={a:ex},effects=eff)
cap('pressure.set',obj({'pressure_pa':number('Pa',0,MAX_PRESSURE)}),obj({'pressure_pa':number('Pa')}),['pressure_controller'],'相对常压出口压差目标；不瞬移真实压力',example={'pressure_pa':0.},effects=['pressure.target_pa','vent.enabled'])
cap('pressure.vent',obj({}),obj({'venting':BOOL}),['pressure_controller'],'关闭进气并由宿主推进真实排气降压，不瞬间归零',effects=['pressure.target_pa','gas.enabled','vent.enabled'])
cap('sensor.calibrate',obj({'pressure_zero_pa':number('Pa',-10000,10000),'flow_zero_m3_s':number('m^3/s',-1e-5,1e-5)}),obj({'pressure_zero_pa':number('Pa'),'flow_zero_m3_s':number('m^3/s')}),['pressure_sensor','flow_sensor'],'外部独立零点输入，不读真值免费标定','configuration',example={'pressure_zero_pa':0.,'flow_zero_m3_s':0.},effects=['pressure.indicated_pa','flow.indicated_m3_s'])
cap('method.configure',METHOD,METHOD,['controller','pressure_controller'],'压力阶梯、显式稳定窗口；不冒称专利MP²','configuration',example=METHOD_EXAMPLE)
cap('acquisition.start',obj({}),obj({'operation_id':TEXT}),['controller','sample_holder','pressure_controller'],'同片湿后干压力阶梯异步采集，部分排液保留',effects=['acquisition.phase','acquisition.elapsed_s','acquisition.scan','acquisition.step','acquisition.points','pressure.target_pa','vent.enabled'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['controller'],'取消关进气，保留残压和实际排液；结果冻结',effects=['acquisition.phase','gas.enabled'])
cap('acquisition.result',obj({}),RESULT,['controller'],'纯读湿干曲线及贯通孔分析，历史/来源冻结','query')
cap('reference.query',obj({'reference_id':TEXT}),obj({'reference_json':TEXT,'template_source':TEXT,'evidence':TEXT}),['controller'],'查询未改动的公开膜片曲线/条件','query',example={'reference_id':SAMPLE_EXAMPLE['reference_id']})
SCALAR=obj({'kind':enum(['bubble_point','mean_flow','mean_pore','smallest_pore']),'diameter_m':number('m',0),'pressure_pa':number('Pa',0),'within_instrument_range':BOOL,'published_pressure_pa':nullable(number('Pa',0)),'source_liquid_pressure_pa':nullable(number('Pa',0))})
SCALARS_OUT=obj({'reference_id':TEXT,'name':TEXT,'material_group':TEXT,'membrane_class':TEXT,'evidence':TEXT,'template_source':TEXT,'licence':TEXT,'source_liquid':nullable(TEXT),'source_surface_tension_n_m':nullable(number('N/m',0)),'scalars':array(SCALAR,1,4),'instrument_max_pressure_pa':number('Pa'),'warnings':array(TEXT,0,20)})
cap('reference.pore_scalars',obj({'reference_id':TEXT,'surface_tension_n_m':number('N/m',.001,.1),'contact_angle_rad':number('rad',0,1.55)}),SCALARS_OUT,['controller'],'已发表泡点/平均流量/平均/最小孔径按指定润湿液Laplace换算压力并判断Revo量程；只用已发表标量，不生成流量曲线或PSD','analysis',example={'reference_id':'glass_fibre_whatman_s14','surface_tension_n_m':.016,'contact_angle_rad':0.})
EXTERNAL=obj({'wet_pressure_pa':array(number('Pa',0),3,10000),'wet_flow_m3_s':array(number('m^3/s',0),3,10000),'dry_pressure_pa':array(number('Pa',0),3,10000),'dry_flow_m3_s':array(number('m^3/s',0),3,10000),'surface_tension_n_m':number('N/m',.001,.1),'contact_angle_rad':number('rad',0,1.55),'bubble_fraction':number('1',1e-6,.49),'accept_monotone_projection':BOOL})
cap('analysis.porometry',EXTERNAL,ANALYSIS,['controller'],'外部独立曲线公开pCF/half-dry分析；不外推MFP，不伪完整归一化PSD','analysis',example={'wet_pressure_pa':[0.,10000.,20000.,40000.],'wet_flow_m3_s':[0.,1e-7,1e-6,4e-6],'dry_pressure_pa':[0.,10000.,20000.,40000.],'dry_flow_m3_s':[0.,1e-6,2e-6,4e-6],'surface_tension_n_m':.016,'contact_angle_rad':0.,'bubble_fraction':.01,'accept_monotone_projection':False})
cap('ledger.read',obj({}),LEDGER,['controller'],'宿主氮气/润湿液/膜片及等温气室能量账本','query','evaluator')
cap('sim.step',obj({'dt_s':number('s',0,3600)}),obj({'time_s':number('s')}),['controller'],'唯一宿主物理时钟','simulation','evaluator',{'dt_s':.1})
UNSUPPORTED=['algorithm.mp2','bubble.dpdt_proprietary','pores.total_porosity','pores.closed_distribution','pores.number_distribution','liquid_liquid.displacement','noise.empirical','accuracy.certification','robot.sample_handling','hardware.connect']
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'未实现或无法据贯通流量推断：'+k)
for k,z,c in [('plant.configured','已接受等温工程参数','controller'),('sample.present','同片膜在位','sample_holder'),('sample.wetted','本膜已有有效润湿准备','sample_holder'),('power.enabled','供电','controller'),('holder.open','夹具开启','sample_lid'),('gas.enabled','进气调压开启','gas_inlet'),('vent.enabled','排气开启','pressure_controller')]:slot(k,BOOL,'1',z,component=c)
slot('power.angle_rad',number('rad',0,.28),'rad','电源开关工程姿态','scene',component='power_switch')
slot('holder.lift_m',number('m',0,.09),'m','样品盖人工抬起代理','scene',component='sample_lid');slot('service.slide_m',number('m',0,.08),'m','服务盖位移','scene',component='service_cover');slot('pressure.target_pa',number('Pa',0,MAX_PRESSURE),'Pa','上游对常压出口压差目标；厂家压力基准未知，此为工程约定','setpoint',component='pressure_controller')
slot('pressure.indicated_pa',nullable(number('Pa')),'Pa','上游-下游差压一阶观测，未认证噪声/准确度',component='pressure_sensor');slot('flow.indicated_m3_s',nullable(number('m^3/s')),'m^3/s','显式参考温压下气体体积流量观测',component='flow_sensor')
for k,u,z in [('truth.pressure_pa','Pa','有限气室真实压差'),('truth.gas_cell_mol','mol','压缩气室氮气'),('truth.gas_stock_mol','mol','有限供气库存'),('truth.liquid_stock_kg','kg','有限润湿液库存'),('truth.sample_liquid_kg','kg','膜中残留润湿液'),('truth.membrane_mass_kg','kg','膜片干基质量'),('truth.open_fraction','1','流导开放比例；非孔数/总孔隙率'),('truth.temperature_k','K','假设等温气体温度'),('truth.flow_m3_s','m^3/s','实际气量转移对应参考体积流')]:slot(k,number(u,0),u,z,'process_truth','evaluator')
slot('clock.time_s',number('s',0),'s','宿主时间');slot('acquisition.phase',PHASE,'1','操作终态');slot('acquisition.elapsed_s',number('s',0),'s','本次操作时间');slot('acquisition.scan',enum(['wet','dry','vent']),'1','同片湿/干/中间降压阶段');slot('acquisition.step',integer(),'1','当前压力阶梯零基编号');slot('acquisition.points',integer(),'1','稳定接受的观测点数')
PHYSICS_EFFECTS=['clock.time_s','truth.pressure_pa','truth.gas_cell_mol','truth.gas_stock_mol','truth.sample_liquid_kg','truth.open_fraction','truth.flow_m3_s','pressure.indicated_pa','flow.indicated_m3_s','pressure.target_pa','gas.enabled','vent.enabled','acquisition.phase','acquisition.elapsed_s','acquisition.scan','acquisition.step','acquisition.points']
CAPS['sim.step']['effects']=PHYSICS_EFFECTS

def source_arrays(record):
 v=record['values'];wp=v.get('wet_pressure_pa',v.get('pressure_pa'));dp=v.get('dry_pressure_pa',v.get('pressure_pa'));w=v.get('wet_flow_m3_s');d=v.get('dry_flow_m3_s')
 for x,y in [(wp,w),(dp,d)]:
  if not isinstance(x,list) or not isinstance(y,list) or len(x)!=len(y) or len(x)<3 or any(not math.isfinite(a) or a<0 for a in x+y) or any(b<=a for a,b in zip(x,x[1:])):raise ActionRejected('INVALID_REFERENCE')
 return wp,w,dp,d

def analyze(a):
 wp=a['wet_pressure_pa'];w=a['wet_flow_m3_s'];dp=a['dry_pressure_pa'];d=a['dry_flow_m3_s']
 for x,y in [(wp,w),(dp,d)]:
  if len(x)!=len(y) or any(not math.isfinite(v) or v<0 for v in x+y) or any(b<=c for c,b in zip(x,x[1:])):raise ActionRejected('INVALID_TIME_SERIES')
 lo=max(wp[0],dp[0]);hi=min(wp[-1],dp[-1])
 if lo>=hi:raise ActionRejected('INSUFFICIENT_COMMON_RANGE')
 grid=sorted({lo,hi}|{x for x in wp+dp if lo<x<hi});grid=[x for x in grid if x>0 and float(np.interp(x,dp,d))>0]
 if len(grid)<2:raise ActionRejected('INSUFFICIENT_COMMON_RANGE')
 wet=np.interp(grid,wp,w);dry=np.interp(grid,dp,d);raw=wet/dry;ratio=np.maximum.accumulate(np.clip(raw,0.,1.));changes=int(np.count_nonzero(abs(ratio-raw)>1e-12))
 if changes and not a['accept_monotone_projection']:raise ActionRejected('NONMONOTONE_FLOW_RATIO')
 # Preserve original curves; any accepted projection applies to this analysis copy only.
 wet=ratio*dry;reasons=[]
 def crossing(fraction,name):
  y=wet-fraction*dry
  if y[0]>0 or y[-1]<0:reasons.append(name+'_not_bracketed');return None
  for i in range(len(grid)-1):
   if y[i]==0:return grid[i]
   if y[i]<0<=y[i+1]:return float(grid[i]+(grid[i+1]-grid[i])*(-y[i])/(y[i+1]-y[i]))
  if y[-1]==0:return grid[-1]
  reasons.append(name+'_not_bracketed');return None
 bp=crossing(a['bubble_fraction'],'bubble');mp=crossing(.5,'mean_flow');factor=4*a['surface_tension_n_m']*math.cos(a['contact_angle_rad']);covered=float(ratio[-1]-ratio[0]);unobserved=max(0.,1-covered)
 if unobserved>1e-9:reasons.append('truncated_flow_coverage_not_renormalized')
 if changes:reasons.append('explicit_clipping_and_cumulative_max_projection')
 return dict(bubble_pressure_pa=bp,bubble_diameter_m=factor/bp if bp else None,mean_flow_pressure_pa=mp,mean_flow_diameter_m=factor/mp if mp else None,pressure_pa=list(map(float,grid)),diameter_m=[factor/x for x in grid],cumulative_flow_fraction=list(map(float,ratio)),differential_flow_fraction=list(map(float,np.diff(ratio))),covered_flow_fraction=covered,unobserved_flow_fraction=unobserved,projection_changes=changes,reasons=reasons)

SCALAR_FIELDS=[('bubble_point','published_bubble_point_pore_diameter_m','published_bubble_point_pressure_pa'),('mean_flow','published_mean_flow_pore_diameter_m','published_mean_flow_pressure_pa'),('mean_pore','published_mean_pore_diameter_m',None),('smallest_pore','published_smallest_pore_diameter_m','published_smallest_pore_pressure_pa')]
def pore_scalars(reference_id,record,a):
 """Published scalars only: P=4*gamma*cos(theta)/d for the requested liquid; no curve, no PSD."""
 v=record['values'];c=record.get('conditions',{});factor=4*a['surface_tension_n_m']*math.cos(a['contact_angle_rad']);gs=c.get('surface_tension_n_m');ts=c.get('contact_angle_rad') or 0.;rows=[];warnings=[]
 for kind,dk,pk in SCALAR_FIELDS:
  d=v.get(dk)
  if d is None or not math.isfinite(d) or d<=0:continue
  p=factor/d;pub=v.get(pk) if pk else None;src=4*gs*math.cos(ts)/d if gs is not None else None
  rows.append(dict(kind=kind,diameter_m=float(d),pressure_pa=float(p),within_instrument_range=bool(p<=MAX_PRESSURE),published_pressure_pa=None if pub is None else float(pub),source_liquid_pressure_pa=None if src is None else float(src)))
  if pub is not None and src is not None and abs(pub/src-1)>.1:warnings.append(f'{kind}: published pressure differs from Laplace(source liquid, published diameter) by {100*(pub/src-1):+.0f}% (source shape factor/reporting convention); both returned unchanged')
 if not rows:raise ActionRejected('INVALID_REFERENCE')
 if gs is None:warnings.append('source paper did not state the wetting-liquid surface tension; source_liquid_pressure_pa is null')
 if any(not r['within_instrument_range'] for r in rows):warnings.append('some pore sizes need more than the Revo 3.45 MPa differential with the requested liquid')
 warnings.append('cylindrical-throat Laplace equivalent; published scalars from '+str(c.get('instrument') or record.get('native_instrument') or 'unspecified porometer')+', not Revo raw data; no flow curve or PSD is generated')
 return dict(reference_id=reference_id,name=record['name'],material_group=record['material_group'],membrane_class=record.get('membrane_class') or 'unspecified',evidence=record['evidence'],template_source=record['source'].get('url') or json.dumps(record['source'],sort_keys=True),licence=record.get('licence') or record['source'].get('license') or 'see source',source_liquid=c.get('wetting_liquid'),source_surface_tension_n_m=gs,scalars=rows,instrument_max_pressure_pa=MAX_PRESSURE,warnings=warnings)

class PorometerPoroluxrevoRuntime:
 def __init__(self,instance_id='poroluxrevo_01'):
  self.instance_id=instance_id;self.plant=None;self.sample=None;self.reference=None;self.liquid=None;self.method=copy.deepcopy(METHOD_EXAMPLE);self.power=False;self.holder_open=True;self.service=0.;self.inlet=False;self.vent=False;self.target=0.;self.cell=0.;self.initial_cell=0.;self.gas_stock=0.;self.gas_loaded=0.;self.gas_exhaust=0.;self.liquid_stock=0.;self.sample_liquid=0.;self.liquid_loaded=0.;self.liquid_recovered=0.;self.membrane_loaded=0.;self.membrane_recovered=0.;self.open_fraction=1.;self.wetted=False;self.pressure_sensor=0.;self.flow_sensor=0.;self.flow=0.;self.pressure_zero=0.;self.flow_zero=0.;self.enthalpy_in=0.;self.enthalpy_out=0.;self.thermostat_removed=0.;self.pressure_scale=1.;self.projection_changes=0;self.source_grid=[];self.source_ratio=[];self.dry_grid=[];self.dry_values=[];self.valid_wet=None;self.phase='idle';self.elapsed=0.;self.scan='wet';self.step_index=0;self.dwell=0.;self.window=[];self.rows=[];self.wet_points=[];self.dry_points=[];self.source=None;self.result_reference=None;self.analysis=None;self.analysis_error=None;self.error=None;self.operation_id=None;self.operation_liquid=None;self.sample_fluid=None;self.operation_method=None;self._counter=0;self._specimen_counter=0;self.specimen=None;self.records={};self.time_s=0.;self._ticks=0;self._remainder=0.;self.revision=0;self._requests={};self._owners={};self._principals={'agent':'agent','evaluator':'evaluator'}
 def register_principal(self,principal_id,visibility='agent'):
  if not isinstance(principal_id,str) or not principal_id or principal_id in self._principals or visibility not in ('agent','debug','evaluator'):raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='agent'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return PorometerPoroluxrevoSession(self,principal_id)
 def describe(self):return export_porometer_poroluxrevo_contract_metadata()
 def _pressure(self):return max(0.,self.cell*R*self.plant['temperature_k']/self.plant['gas_volume_m3']-self.plant['ambient_pressure_pa']) if self.plant else 0.
 def _indications(self):
  if not self.plant:return 0.,0.
  return self.pressure_sensor*self.plant['pressure_calibration_gain']+self.plant['pressure_calibration_offset_pa']-self.pressure_zero,self.flow_sensor*self.plant['flow_calibration_gain']+self.plant['flow_calibration_offset_m3_s']-self.flow_zero
 def read(self,role='evaluator'):
  pressure,flow=self._indications();d={'plant.configured':self.plant is not None,'sample.present':self.sample is not None,'sample.wetted':self.wetted,'power.enabled':self.power,'power.angle_rad':.28 if self.power else 0.,'holder.open':self.holder_open,'holder.lift_m':.09 if self.holder_open else 0.,'service.slide_m':self.service,'gas.enabled':self.inlet,'vent.enabled':self.vent,'pressure.target_pa':self.target,'pressure.indicated_pa':pressure if self.power else None,'flow.indicated_m3_s':flow if self.power else None,'truth.pressure_pa':self._pressure(),'truth.gas_cell_mol':self.cell,'truth.gas_stock_mol':self.gas_stock,'truth.liquid_stock_kg':self.liquid_stock,'truth.sample_liquid_kg':self.sample_liquid,'truth.membrane_mass_kg':self.sample['dry_mass_kg'] if self.sample else 0.,'truth.open_fraction':self.open_fraction,'truth.temperature_k':self.plant['temperature_k'] if self.plant else 298.15,'truth.flow_m3_s':self.flow,'clock.time_s':self.time_s,'acquisition.phase':self.phase,'acquisition.elapsed_s':self.elapsed,'acquisition.scan':self.scan,'acquisition.step':self.step_index,'acquisition.points':len(self.wet_points)+len(self.dry_points)}
  return {k:copy.deepcopy(v) for k,v in d.items() if role!='agent' or SLOTS[k]['visibility']=='agent'}
 def _ledger(self):
  ge=self.gas_loaded-self.cell-self.gas_stock-self.gas_exhaust;le=self.liquid_loaded-self.liquid_stock-self.sample_liquid-self.liquid_recovered;mem=self.sample['dry_mass_kg'] if self.sample else 0.;me=self.membrane_loaded-mem-self.membrane_recovered;stored=self.plant['gas_cv_j_mol_k']*self.plant['temperature_k']*(self.cell-self.initial_cell) if self.plant else 0.
  return dict(gas_loaded_mol=self.gas_loaded,gas_present_mol=self.cell+self.gas_stock,gas_exhaust_recovered_mol=self.gas_exhaust,gas_error_mol=ge,liquid_loaded_kg=self.liquid_loaded,liquid_present_kg=self.liquid_stock+self.sample_liquid,liquid_trap_recovered_kg=self.liquid_recovered,liquid_error_kg=le,membrane_loaded_kg=self.membrane_loaded,membrane_present_kg=mem,membrane_recovered_kg=self.membrane_recovered,mass_error_kg=MN*ge+le+me,gas_enthalpy_in_j=self.enthalpy_in,gas_enthalpy_out_j=self.enthalpy_out,isothermal_heat_removed_j=self.thermostat_removed,gas_stored_energy_j=stored,gas_energy_error_j=self.enthalpy_in-self.enthalpy_out-self.thermostat_removed-stored)
 def _result(self):
  if self.phase!='running' and self.operation_id in self.records:return copy.deepcopy(self.records[self.operation_id])
  return dict(operation_id=self.operation_id,phase=self.phase,elapsed_s=self.elapsed,rows=copy.deepcopy(self.rows),wet_points=copy.deepcopy(self.wet_points),dry_points=copy.deepcopy(self.dry_points),analysis=copy.deepcopy(self.analysis),reference_id=self.result_reference,template_source=self.source,evidence='Original separate wet/dry curves from cited instrument; transfer to Revo is a reference, not Revo validation. Explicitly accepted source flow-reference/temperature and aperture assumptions, finite isothermal gas and liquid relaxation. Runtime-only clipping/cumulative-max changes='+str(self.projection_changes)+'. pCF bubble_fraction and half-dry intersection differ from source-report bubble algorithm. Flow-weighted through-pore coverage only; no total porosity or pore-number inference; no MP2 reproduction. Original negative observed flows are retained and cause explicit analysis rejection rather than silent zero clipping.',error_code=self.error,analysis_error_code=self.analysis_error)
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _depressurized(self):
  if not self.plant or self.inlet or self._pressure()>self.plant['pressure_safe_open_pa']:raise ActionRejected('PRESSURIZED')
 def _access(self):
  self._depressurized()
  if not self.holder_open:raise ActionRejected('HOLDER_CLOSED')
 def _ready(self):
  if not self.plant or not self.power or self.service>1e-9:raise ActionRejected('NOT_READY')
  if self.holder_open:raise ActionRejected('HOLDER_OPEN')
 def _prepare_reference(self,record):
  wp,w,dp,d=source_arrays(record);lo=max(wp[0],dp[0]);hi=min(wp[-1],dp[-1])
  if lo>=hi:raise ActionRejected('INSUFFICIENT_COMMON_RANGE')
  grid=sorted({lo,hi}|{x for x in wp+dp if lo<x<hi});wet=np.interp(grid,wp,w);dry=np.interp(grid,dp,d);raw=np.divide(wet,dry,out=np.zeros(len(grid)),where=dry>0);ratio=np.maximum.accumulate(np.clip(raw,0,1));changes=int(np.count_nonzero(abs(raw-ratio)>1e-12))
  if changes and not self.sample['accept_monotone_projection']:raise ActionRejected('NONMONOTONE_FLOW_RATIO')
  self.source_grid=list(map(float,grid));self.source_ratio=list(map(float,ratio));self.dry_grid=dp;self.dry_values=d;self.projection_changes=changes
 def _liquid_conditions(self):
  c=self.reference.get('conditions',{});known_gamma=c.get('surface_tension_n_m');known_angle=c.get('contact_angle_rad');known_name=c.get('wetting_liquid');liquid=self.liquid
  if liquid is None:raise ActionRejected('NO_LIQUID')
  unknown=known_gamma is None or known_angle is None
  if unknown and not self.sample['accept_unknown_conditions']:raise ActionRejected('UNKNOWN_REFERENCE_CONDITIONS')
  gamma=known_gamma if known_gamma is not None else liquid['surface_tension_n_m'];angle=known_angle if known_angle is not None else liquid['contact_angle_rad'];scale=liquid['surface_tension_n_m']*math.cos(liquid['contact_angle_rad'])/(gamma*math.cos(angle));changed=abs(scale-1)>1e-10 or known_name is not None and known_name.casefold()!=liquid['name'].casefold()
  if changed and not self.sample['accept_pressure_transform']:raise ActionRejected('REFERENCE_LIQUID_MISMATCH')
  return scale
 def _method_check(self,m):
  if not self.sample:raise ActionRejected('NO_SAMPLE')
  x=m['pressure_steps_pa']
  if any(b<=a for a,b in zip(x,x[1:])):raise ActionRejected('INVALID_PRESSURE_STEPS')
  if m['minimum_dwell_s']>m['maximum_dwell_s'] or m['stability_window_s']>m['maximum_dwell_s']:raise ActionRejected('INVALID_DWELL')
  scale=self.pressure_scale if m['mode']=='dry' else self._liquid_conditions();upper=min(self.source_grid[-1]*scale,self.dry_grid[-1])
  if x[0]<max(self.source_grid[0]*scale,self.dry_grid[0])-1e-9 or x[-1]>upper+1e-9:raise ActionRejected('REFERENCE_PRESSURE_RANGE')
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
  if k=='analysis.porometry':return analyze(a)
  if k=='reference.pore_scalars':
   record=library()['substances'].get(a['reference_id'])
   if record is None or record.get('scalar_usable') is not True:raise ActionRejected('INVALID_REFERENCE')
   return pore_scalars(a['reference_id'],record,a)
  if k=='reference.query':
   record=library()['substances'].get(a['reference_id'])
   if record is None:raise ActionRejected('INVALID_REFERENCE')
   return dict(reference_json=json.dumps(record,sort_keys=True),template_source=json.dumps(record['source'],sort_keys=True),evidence=json.dumps(record['evidence'],sort_keys=True))
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self.inlet=False;self._finish('cancelled');return {'phase':self.phase}
  self._idle()
  if k=='plant.configure':
   if self.plant is not None:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   self.plant=copy.deepcopy(a);self.cell=self.initial_cell=a['ambient_pressure_pa']*a['gas_volume_m3']/(R*a['temperature_k']);self.gas_loaded=self.cell;return {'configured':True}
  if k=='sample.load':
   self._access()
   if self.sample:raise ActionRejected('SAMPLE_PRESENT')
   if not a['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   record=library()['substances'].get(a['reference_id'])
   if record is None or record.get('runtime_usable') is not True:raise ActionRejected('INVALID_REFERENCE')
   c=record.get('conditions',{});known=c.get('sample_diameter_m')
   if a['active_diameter_m']>a['diameter_m'] or known is not None and abs(known-a['active_diameter_m'])>1e-10:raise ActionRejected('REFERENCE_APERTURE')
   if any(c.get(k) is None for k in ['temperature_k','flow_reference_temperature_k','flow_reference_pressure_pa']) and not a['accept_unknown_conditions']:raise ActionRejected('UNKNOWN_REFERENCE_CONDITIONS')
   if c.get('temperature_k') is not None and abs(c['temperature_k']-self.plant['temperature_k'])>1e-6:raise ActionRejected('REFERENCE_TEMPERATURE')
   self.sample=copy.deepcopy(a);self.reference=copy.deepcopy(record);self._prepare_reference(record);self.sample_liquid=0.;self.open_fraction=1.;self.wetted=False;self.membrane_loaded+=a['dry_mass_kg'];self._specimen_counter+=1;self.specimen=self._specimen_counter;self.valid_wet=None;return {'loaded':True}
  if k=='sample.remove':
   self._access()
   if not self.sample:raise ActionRejected('NO_SAMPLE')
   self.membrane_recovered+=self.sample['dry_mass_kg'];self.liquid_recovered+=self.sample_liquid;self.sample=None;self.reference=None;self.sample_liquid=0.;self.open_fraction=1.;self.wetted=False;self.valid_wet=None;return {'removed':True}
  if k in ('liquid.load','liquid.unload'):
   self._access()
   if k=='liquid.unload':
    if self.sample_liquid>0:raise ActionRejected('RESIDUAL_LIQUID')
    self.liquid_recovered+=self.liquid_stock;self.liquid_stock=0.;return {'unloaded':True}
   if self.liquid_stock>0 or self.sample_liquid>0:raise ActionRejected('STOCK_PRESENT')
   if not a['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   self.liquid=copy.deepcopy(a);self.liquid_stock=a['mass_kg'];self.liquid_loaded+=a['mass_kg'];return {'loaded':True}
  if k=='sample.wet':
   self._access()
   if not self.sample:raise ActionRejected('NO_SAMPLE')
   scale=self._liquid_conditions();needed=self.sample['liquid_capacity_kg']-self.sample_liquid
   if needed>self.liquid_stock:raise ActionRejected('FINITE_LIQUID_INVENTORY')
   self.liquid_stock-=needed;self.sample_liquid+=needed;self.open_fraction=0.;self.wetted=True;self.sample_fluid=copy.deepcopy(self.liquid);self.pressure_scale=scale;self.valid_wet=None;return {'wetted':True}
  if k in ('gas.load','gas.unload'):
   if not self.plant or self.inlet:raise ActionRejected('GAS_ACCESS')
   if k=='gas.unload':self.gas_exhaust+=self.gas_stock;self.gas_stock=0.;return {'unloaded':True}
   if self.gas_stock>0:raise ActionRejected('STOCK_PRESENT')
   self.gas_stock=a['amount_mol'];self.gas_loaded+=self.gas_stock;return {'loaded':True}
  if k=='holder.set':
   if a['opened']:self._depressurized()
   self.holder_open=a['opened'];return a
  if k=='power.set':
   if a['enabled'] and (not self.plant or self.service>1e-9):raise ActionRejected('NOT_READY')
   self.power=a['enabled']
   if not self.power:self.inlet=False
   return a
  if k=='service.set':
   self._depressurized()
   if self.power:raise ActionRejected('POWER_INTERLOCK')
   self.service=a['distance_m'];return a
  if k=='pressure.set':
   if not self.plant:raise ActionRejected('NOT_READY')
   if self.sample and a['pressure_pa']>min(self.source_grid[-1]*self.pressure_scale,self.dry_grid[-1])+1e-9:raise ActionRejected('REFERENCE_PRESSURE_RANGE')
   self.target=a['pressure_pa'];self.vent=False;return a
  if k=='pressure.vent':self.target=0.;self.inlet=False;self.vent=True;return {'venting':True}
  if k=='gas.open':
   if a['enabled']:
    self._ready()
    if self.gas_stock<=0:raise ActionRejected('FINITE_GAS_INVENTORY')
   self.inlet=a['enabled'];return a
  if k=='sensor.calibrate':self.pressure_zero=a['pressure_zero_pa'];self.flow_zero=a['flow_zero_m3_s'];return a
  if k=='method.configure':self._method_check(a);self.method=copy.deepcopy(a);return a
  if k=='acquisition.start':
   self._ready();self._method_check(self.method)
   if not self.inlet or self.gas_stock<=0:raise ActionRejected('FINITE_GAS_INVENTORY')
   if self.method['mode'] in ['wet','wet_dry'] and (not self.wetted or self.sample_liquid<self.sample['liquid_capacity_kg']*(1-1e-10)):raise ActionRejected('COMPLETE_WETTING_REQUIRED')
   if self.method['mode']=='dry' and (self.sample_liquid>0 or self.valid_wet is None or self.valid_wet['specimen']!=self.specimen):raise ActionRejected('SAME_DRY_SPECIMEN_REQUIRED')
   self._counter+=1;self.operation_id=self.instance_id+':'+str(self._counter);self.operation_liquid=copy.deepcopy(self.valid_wet['liquid'] if self.method['mode']=='dry' else self.sample_fluid);self.operation_method=copy.deepcopy(self.method);self.phase='running';self.elapsed=0.;self.rows=[];self.wet_points=copy.deepcopy(self.valid_wet['points']) if self.method['mode']=='dry' else [];self.dry_points=[];self.scan='dry' if self.method['mode']=='dry' else 'wet';self.source=json.dumps(self.reference['source'],sort_keys=True);self.result_reference=self.sample['reference_id'];self.error=None;self.analysis=None;self.analysis_error=None;self.step_index=0;self.vent=False;self._new_step();return {'operation_id':self.operation_id}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _new_step(self):self.target=self.method['pressure_steps_pa'][self.step_index];self.dwell=0.;self.window=[]
 def _observe(self):
  p,q=self._indications();self.rows.append(dict(time_s=self.time_s,elapsed_s=self.elapsed,scan=self.scan,step=self.step_index,pressure_pa=p,flow_m3_s=q,target_pressure_pa=self.target))
 def _finish(self,phase,error=None):
  self.phase=phase;self.error=error;self.inlet=False
  if self.wet_points and self.dry_points:
   try:self.analysis=analyze(dict(wet_pressure_pa=[x['pressure_pa'] for x in self.wet_points],wet_flow_m3_s=[x['flow_m3_s'] for x in self.wet_points],dry_pressure_pa=[x['pressure_pa'] for x in self.dry_points],dry_flow_m3_s=[x['flow_m3_s'] for x in self.dry_points],surface_tension_n_m=self.operation_liquid['surface_tension_n_m'],contact_angle_rad=self.operation_liquid['contact_angle_rad'],bubble_fraction=self.operation_method['bubble_fraction'],accept_monotone_projection=self.sample['accept_monotone_projection']))
   except ActionRejected as exc:
    self.analysis=None;self.analysis_error=str(exc)
  if self.operation_id:self.records[self.operation_id]=self._result()
 def _dry_molar_flow(self,pressure):
  if not self.sample or pressure<=0:return 0.
  c=self.reference.get('conditions',{});refp=c.get('flow_reference_pressure_pa') or self.plant['flow_reference_pressure_pa'];reft=c.get('flow_reference_temperature_k') or self.plant['flow_reference_temperature_k']
  if pressure<self.dry_grid[0]:q=self.dry_values[0]*pressure/self.dry_grid[0]
  else:q=float(np.interp(pressure,self.dry_grid,self.dry_values))
  return q*refp/(R*reft)
 def _tick(self):
  if not self.plant:return
  p=self.plant;pressure=self._pressure();failure=None
  if self.sample and self.sample_liquid>0 and not self.holder_open:
   wanted=float(np.interp(pressure/self.pressure_scale,self.source_grid,self.source_ratio,left=0.,right=self.source_ratio[-1]));new=self.open_fraction+max(0.,wanted-self.open_fraction)*(-math.expm1(-DT/self.sample['drain_tau_s']))
   if new>1-1e-9:new=1.
   drained=self.sample['liquid_capacity_kg']*(new-self.open_fraction);drained=min(drained,self.sample_liquid);self.sample_liquid-=drained;self.liquid_recovered+=drained;self.open_fraction=new
   if self.sample_liquid<=self.sample['liquid_capacity_kg']*1e-12:self.liquid_recovered+=self.sample_liquid;self.sample_liquid=0.;self.open_fraction=1.
  mol_per_ref=p['flow_reference_pressure_pa']/(R*p['flow_reference_temperature_k']);demand=self._dry_molar_flow(pressure)*self.open_fraction*DT if not self.holder_open else 0.;flow_limit=MAX_FLOW*mol_per_ref*DT
  if demand>flow_limit:failure='FLOW_RANGE';demand=flow_limit
  ambient=p['ambient_pressure_pa']*p['gas_volume_m3']/(R*p['temperature_k']);target=(p['ambient_pressure_pa']+self.target)*p['gas_volume_m3']/(R*p['temperature_k']);incoming=0.;outgoing=0.;old=self.cell
  if self.inlet and self.power and not self.holder_open and not self.vent:
   request=max(0.,target-self.cell)*(-math.expm1(-DT/p['regulator_tau_s']))+demand;incoming=min(request,flow_limit,self.gas_stock);self.gas_stock=max(0.,self.gas_stock-incoming)
   if request>self.gas_stock+incoming and incoming<min(request,flow_limit):failure='GAS_EXHAUSTED'
  self.cell+=incoming;through=min(demand,max(0.,self.cell-ambient));self.cell-=through;outgoing+=through;self.flow=through/DT/mol_per_ref
  if self.vent or self.inlet and self.power and self.cell>target:
   desired=ambient if self.vent else max(ambient,target);rate=p['vent_tau_s'] if self.vent else p['regulator_tau_s'];released=max(0.,self.cell-desired)*(-math.expm1(-DT/rate));self.cell-=released;outgoing+=released
   if self.vent and self._pressure()<1e-6:outgoing+=self.cell-ambient;self.cell=ambient
  self.gas_exhaust+=outgoing;cp=p['gas_cv_j_mol_k']+R;self.enthalpy_in+=incoming*cp*p['temperature_k'];self.enthalpy_out+=outgoing*cp*p['temperature_k'];self.thermostat_removed+=(incoming-outgoing)*R*p['temperature_k'];truep=self._pressure()
  if truep>MAX_PRESSURE+1e-6:failure='PRESSURE_OVERLOAD'
  self.pressure_sensor=truep+(self.pressure_sensor-truep)*math.exp(-DT/p['pressure_sensor_tau_s']);self.flow_sensor=self.flow+(self.flow_sensor-self.flow)*math.exp(-DT/p['flow_sensor_tau_s'])
  if failure:self.inlet=False
  if self.phase!='running':return
  self.elapsed+=DT;self.dwell+=DT;self._observe()
  if failure:self._finish('failed',failure);return
  if self.scan=='vent':
   if truep<=p['pressure_safe_open_pa']:self.scan='dry';self.step_index=0;self.inlet=True;self.vent=False;self._new_step()
   elif self.dwell>self.method['maximum_dwell_s']:self._finish('failed','VENT_TIMEOUT')
   return
  indicated,flow=self._indications();self.window.append((self.time_s,indicated,flow));cut=self.time_s-self.method['stability_window_s'];self.window=[v for v in self.window if v[0]>=cut-DT/2];full=len(self.window)>2 and self.window[-1][0]-self.window[0][0]>=self.method['stability_window_s']-1e-9;rate=(max(x[2] for x in self.window)-min(x[2] for x in self.window))/(self.window[-1][0]-self.window[0][0]) if full else math.inf;stable=full and self.dwell>=self.method['minimum_dwell_s']-1e-9 and max(abs(x[1]-self.target) for x in self.window)<=self.method['pressure_tolerance_pa'] and rate<=self.method['flow_slope_tolerance_m3_s2']
  if not stable:
   if self.dwell>=self.method['maximum_dwell_s']-1e-9:self._finish('failed','STABILITY_TIMEOUT')
   return
  point=dict(pressure_pa=indicated,flow_m3_s=flow,target_pressure_pa=self.target,dwell_s=self.dwell,stable=True);(self.wet_points if self.scan=='wet' else self.dry_points).append(point)
  if self.step_index+1<len(self.method['pressure_steps_pa']):self.step_index+=1;self._new_step();return
  if self.scan=='wet':
   self.valid_wet=dict(specimen=self.specimen,points=copy.deepcopy(self.wet_points),liquid=copy.deepcopy(self.operation_liquid))
   if self.method['mode']=='wet_dry':
    if self.sample_liquid>0:self._finish('failed','INCOMPLETE_DRAINAGE');return
    self.scan='vent';self.target=0.;self.inlet=False;self.vent=True;self.dwell=0.;return
  self._finish('succeeded')
 def step(self,dt_s):
  validate(number('s',0,3600),dt_s);total=self._remainder+dt_s;n=int((total+1e-12)/DT);self._remainder=total-n*DT
  for _ in range(n):self._ticks+=1;self.time_s=self._ticks*DT;self._tick()
  self.revision+=n

class PorometerPoroluxrevoSession:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,instance_id):
  if instance_id!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  meta=export_porometer_poroluxrevo_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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


def export_porometer_poroluxrevo_contract_metadata():
 slots={};caps={}
 for k,v in SLOTS.items():slots[k]=dict(meaning_zh=v['meaning'],component_ids=[v['component']],value_schema=copy.deepcopy(v['schema']),unit=v['unit'],reference_conditions='Pressure is modeled upstream-minus-ambient-outlet deltaP. OEM pressure datum and200L/min reference T/P are unspecified; adopted3.45MPa differential bound and explicitly supplied flow reference are engineering conventions.',origin=v['origin'],visibility=v['visibility'],access='read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'PorometerPoroluxrevoSession.read(state_ids=[{k!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description='Fixed0.1s host ticks; first-order pressure/flow sensor response explicitly assumed; accepted pressure-step points use complete stability window; no MP2 or empirical noise claim'),source_ids=['simulation_assumption'])
 errors=['UNKNOWN_CAPABILITY','UNKNOWN_INSTANCE','UNKNOWN_OPERATION','INVALID_ARGUMENT','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','BUSY','NOT_READY','NOT_RUNNING','INITIALIZATION_ONLY','MODEL_NOT_ACCEPTED','PRESSURIZED','HOLDER_CLOSED','HOLDER_OPEN','SAMPLE_PRESENT','NO_SAMPLE','INVALID_REFERENCE','UNKNOWN_REFERENCE_CONDITIONS','REFERENCE_APERTURE','REFERENCE_TEMPERATURE','REFERENCE_LIQUID_MISMATCH','REFERENCE_PRESSURE_RANGE','NO_LIQUID','RESIDUAL_LIQUID','STOCK_PRESENT','FINITE_LIQUID_INVENTORY','FINITE_GAS_INVENTORY','GAS_ACCESS','POWER_INTERLOCK','INVALID_PRESSURE_STEPS','INVALID_DWELL','COMPLETE_WETTING_REQUIRED','SAME_DRY_SPECIMEN_REQUIRED','GAS_EXHAUSTED','FLOW_RANGE','PRESSURE_OVERLOAD','STABILITY_TIMEOUT','VENT_TIMEOUT','INCOMPLETE_DRAINAGE','INVALID_TIME_SERIES','INSUFFICIENT_COMMON_RANGE','NONMONOTONE_FLOW_RATIO']
 for k,c in CAPS.items():
  un=k in UNSUPPORTED;async_=k=='acquisition.start';caps[k]={key:copy.deepcopy(c[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']};caps[k].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ['scene','robot','hardware'] else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.porometer_poroluxrevo.PorometerPoroluxrevoSession.invoke',invocation=f'session.invoke(instance_id="poroluxrevo_01",capability_id={k!r},arguments={c["example_arguments"]!r},request_id="unique")'),preconditions=[] if c['kind'] in ['query','analysis'] or un else [dict(description_zh='实际权限/参数/有限库存与同片干湿/原曲线范围/真实降压前提；拒绝原子，非厂家安全认证',state_ids=['power.enabled','plant.configured','acquisition.phase','holder.open'],enforced_by='runtime',check='atomic invoke and registered Session')],state_effects=[dict(state_id=x,timing='immediate',description_zh='实际命令状态变化') for x in c['effects']],observes=[],model_effects=dict(mode='none',description_zh='盖/服务盖/电源开关联动另行验证；气体、膜与夹具接触不由此证明',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='同片湿测、完全排液、降压及干测完成；否则明确稳定/排液/库存失败' if async_ else '命令返回但不推进物理时钟',completion_state_ids=['acquisition.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','pressure_cell','membrane','wetting_liquid','gas'],partial_failure_zh='保留真实残压、有限气耗与已排液；终态历史冻结，后续压力仍随真实流通/排气演化；重新湿测需重新润湿'),errors=[dict(code=x,meaning_zh=x,side_effects='partial' if async_ and x in ['GAS_EXHAUSTED','FLOW_RANGE','PRESSURE_OVERLOAD','STABILITY_TIMEOUT','VENT_TIMEOUT','INCOMPLETE_DRAINAGE'] else 'none') for x in errors],verification=[],limitations=['真实原曲线与运行时接受的投影/等温/动力学假设分开','公开pCF/half-dry算法不是专利MP2或原报告bubble阈值拟合','仅流量加权贯通孔信息，不输出总孔隙或孔数分布'],source_ids=['manufacturer_specs','simulation_assumption'])
  if async_ or k in ['gas.open','pressure.set','pressure.vent','power.set']:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='有限气体调压/排气、不可逆排液与观测推进') for x in PHYSICS_EFFECTS if x!='clock.time_s']
 mech=dict(component_ids=['controller','pressure_controller','pressure_sensor','flow_sensor','sample_holder','sample_lid','liquid_trap','gas_inlet'],state_ids=list(slots),meaning_zh='有限N2等温可压缩气室、同片不可逆排液及公开贯通孔分析',implementation='lumped',equations=['P_upstream=n*R*T/effective_volume; deltaP=P_upstream-P_ambient; regulator/vent relaxation and feedforward actual outlet flow use explicitly supplied time constants and finite N2 stock','Source dry flow is piecewise-linearly interpolated on original pressure grid; independent wet/dry grids merged only on common domain; unknown source Q-reference T/P requires explicit accepted assumption','Wet open-conductance equilibrium is sourceQwet/sourceQdry; any clipping[0,1]/cumulative-max projection is explicitly accepted and counted while source arrays remain unchanged','Lumped irreversible drainage: open_fraction approaches max(previous,source_ratio(deltaP/liquid_scale)) with explicit tau; remainingmass=initial_liquid_capacity*(1-open_fraction); capacity and flow-volume partition are engineering inputs, not measured pore volume','Liquid pressure scaling=(gamma*cos(theta))/(source_gamma*cos(source_theta)) requires explicit engineering acceptance; aperture is preserved and no free area scaling is applied','Dry scan requires prior wet record of same specimen and exactly completed finite drainage; cancellation leaves partial wetness; wet_dry mode vents then restarts the pressure sequence without replacing the specimen','D=4*gamma*cos(theta)/deltaP; pCF bubble solves Qwet-fraction*Qdry=0; MFP solves Qwet-.5*Qdry=0 on common observed brackets only; unbracketed result is null with reason','Flow-weighted cumulative ratio and adjacent ratio differences retain covered and unobserved fractions; incomplete scan is not renormalized to a full PSD; ratios do not count pores or total pore volume','Gas loaded=cell+stock+exhaust/recovered; wetting liquid loaded=stock+sample+trap/recovered; finite membrane mass conserved','Isothermal gas control-volume bookkeeping: Hin=Cp*T*nin, Hout=Cp*T*nout, stored=Cv*T*delta_n, thermostat heat removed=R*T*delta_n; explicit ideal thermostat, not solved heater/cooler hardware','Full pressure/flow stability window and minimum dwell are required; max dwell fails rather than silently accepting unstable observations; this is an engineering controller and not measured MP2'],parameters={},source_ids=['manufacturer_specs','simulation_assumption'],limitations=['Manufacturer webpage35bar versus PDF34.5bar conflict: model conservatively uses3.45MPa differential limit, datum is an explicitly adopted convention, not manufacturer-confirmed gauge specification','200L/min maximum has unspecified OEM reference conditions; model uses explicitly supplied reference temperature and absolute pressure','13nm–500um is wetting-liquid-dependent coverage, not a promise for every gamma/theta; gamma.016N/m theta0 at3.45MPa gives18.55nm','Default reference PVDF/FC43 was acquired on POROLUX1000; source transfer does not validate Revo accuracy or native MP2','Original FC43 active aperture18.4mm is preserved inside a standard25mm holder by explicit adapter assumption; not equal to unrestricted25mm active area','Original low-pressure background can left-censor1%pCF; default5% is an explicit different threshold, not original report BP algorithm','Dry2 half-dry export is derived, never treated as an independent measured dry scan','Effective fixed gas volume excludes resolved pore-volume displacement; drainage flow-volume partition and ideal isothermal thermostat are engineering simplifications','No thermal hardware/transient gas temperature, seal deformation, liquid evaporation/swelling, capillary-network DEM, empirical noise or certified pressure/flow accuracy','No closed-pore/total-porosity/pore-count inference, liquid-liquid module, robot or hardware execution','Registered Session filters agenttruth and owner-bound result/poll/cancel; trusted raw Runtime does not provide filesystem/process isolation'])
 spec=importlib.util.spec_from_file_location('_revo_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()});ann.annotate_export(SLUG,caps,slots,{'capillary_flow_porometry':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'liquid.load':(LIQUID,LIQUID_EXAMPLE)})
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'capillary_flow_porometry':mech})

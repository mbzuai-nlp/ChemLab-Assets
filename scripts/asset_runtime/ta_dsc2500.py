"""CHEM131 conservative three-node DSC proxy; observed heat flow, finite melting and gas."""
from pathlib import Path
import copy,json,math,importlib.util
import numpy as np
PROTOCOL_VERSION='1.0.0';SLUG='ta_dsc2500';CATALOG_ID='CHEM-131';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch03/t1_thermal'/SLUG
DT=.1;R_GAS=8.31446261815324
SAMPLER_REGISTERED_XY=[(0.,0.),(.012,0.)]+[(round(.165+.020*c,12),round(y+.075,12)) for y in [-.075,-.055,-.035,-.005,.015,.035] for c in range(9)]
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
 p=ASSET/'thermal_library.json';return json.loads(p.read_text()) if p.exists() else {'substances':{}}

# Each accessory has its own supported temperature domain. Engineering power does not certify cooling rate.
COOLERS={'FACS':(None,998.15),'RCS120':(153.15,673.15),'RCS90':(183.15,823.15),'RCS40':(233.15,673.15),'LN2P':(93.15,823.15)}
PLANT_EXAMPLE=dict(accept_engineering=True,ambient_k=298.15,cooler='FACS',furnace_capacity_j_k=2.,sample_pan_capacity_j_k=.018,reference_pan_capacity_j_k=.018,sample_conductance_w_k=.04,reference_conductance_w_k=.04,ambient_conductance_w_k=.05,facs_conductance_w_k=.5,controller_gain_w_k=25.,heater_max_w=100.,cooler_max_w=50.,maximum_program_rate_k_s=2.,safe_access_temperature_k=313.15,sensor_tau_s=.05,sample_flow_gain=1.,reference_flow_gain=1.,sample_flow_offset_w=0.,reference_flow_offset_w=0.,temperature_gain=1.,temperature_offset_k=0.,sampler_speed_m_s=.05,lid_speed_m_s=.03)
PLANT=obj({'accept_engineering':BOOL,'ambient_k':number('K',283.15,313.15),'cooler':enum(COOLERS),'furnace_capacity_j_k':number('J/K',1e-5,10000),'sample_pan_capacity_j_k':number('J/K',1e-7,100),'reference_pan_capacity_j_k':number('J/K',1e-7,100),'sample_conductance_w_k':number('W/K',1e-7,1000),'reference_conductance_w_k':number('W/K',1e-7,1000),'ambient_conductance_w_k':number('W/K',0,1000),'facs_conductance_w_k':number('W/K',0,1000),'controller_gain_w_k':number('W/K',1e-5,1e5),'heater_max_w':number('W',1e-5,1e5),'cooler_max_w':number('W',0,1e5),'maximum_program_rate_k_s':number('K/s',.001,20),'safe_access_temperature_k':number('K',283.15,373.15),'sensor_tau_s':number('s',1e-5,100),'sample_flow_gain':number('1',.01,100),'reference_flow_gain':number('1',.01,100),'sample_flow_offset_w':number('W',-1,1),'reference_flow_offset_w':number('W',-1,1),'temperature_gain':number('1',.5,1.5),'temperature_offset_k':number('K',-20,20),'sampler_speed_m_s':number('m/s',.0001,.2),'lid_speed_m_s':number('m/s',.0001,.1)})
SAMPLE_EXAMPLE=dict(reference_id='indium_srm2232a',mass_kg=2e-6,heat_capacity_j_kg_k=233.,transition_width_k=.2,initial_temperature_k=298.15,accept_engineering=True,accept_reversible_phase_change=True,accept_condition_transfer=True)
SAMPLE=obj({'reference_id':TEXT,'mass_kg':number('kg',1e-10,.0001),'heat_capacity_j_kg_k':number('J/(kg*K)',1,100000),'transition_width_k':number('K',.001,20),'initial_temperature_k':number('K',93.15,998.15),'accept_engineering':BOOL,'accept_reversible_phase_change':BOOL,'accept_condition_transfer':BOOL})
GAS_EXAMPLE=dict(gas='N2',amount_mol=.1,reference_temperature_k=298.15,reference_pressure_pa=101325.,accept_reference_conditions=True)
GAS=obj({'gas':enum(['N2']),'amount_mol':number('mol',1e-18,1000),'reference_temperature_k':number('K',200,400),'reference_pressure_pa':number('Pa',10000,1e7),'accept_reference_conditions':BOOL})
CALIBRATION_EXAMPLE=dict(sample_flow_gain_correction=1.,reference_flow_gain_correction=1.,sample_flow_zero_w=0.,reference_flow_zero_w=0.,temperature_gain_correction=1.,temperature_zero_k=0.)
CALIBRATION=obj({'sample_flow_gain_correction':number('1',.01,100),'reference_flow_gain_correction':number('1',.01,100),'sample_flow_zero_w':number('W',-10,10),'reference_flow_zero_w':number('W',-10,10),'temperature_gain_correction':number('1',.1,10),'temperature_zero_k':number('K',-100,100)})
SEGMENT=obj({'target_k':number('K',93.15,998.15),'rate_k_s':number('K/s',.0001,20),'hold_s':number('s',0,100000)})
METHOD_EXAMPLE=dict(start_temperature_k=424.7485,segments=[dict(target_k=436.7485,rate_k_s=1/6,hold_s=20.)],sample_interval_s=.1,modulation_amplitude_k=0.,modulation_period_s=60.,start_tolerance_k=.3,timeout_s=1000.)
METHOD=obj({'start_temperature_k':number('K',93.15,998.15),'segments':array(SEGMENT,1,100),'sample_interval_s':number('s',.1,60),'modulation_amplitude_k':number('K',0,10),'modulation_period_s':number('s',1,10000),'start_tolerance_k':number('K',.001,10),'timeout_s':number('s',.1,200000)})
ROW=obj({'time_s':number('s',0),'elapsed_s':number('s',0),'segment_index':integer(0,99),'stage':enum(['ramp','hold']),'target_k':number('K',93.15,998.15),'furnace_k':nullable(number('K')),'sample_k':nullable(number('K')),'reference_k':nullable(number('K')),'sample_heatflow_w':nullable(number('W')),'reference_heatflow_w':nullable(number('W')),'heatflow_w':nullable(number('W'))})
RESULT=obj({'operation_id':nullable(TEXT),'phase':PHASE,'elapsed_s':number('s',0),'rows':array(ROW,0,2000000),'reference_id':nullable(TEXT),'template_source':nullable(TEXT),'method_json':nullable(TEXT),'calibration_json':nullable(TEXT),'evidence':TEXT,'error_code':nullable(TEXT)})
INTEGRAL=obj({'energy_j':number('J'),'specific_enthalpy_j_kg':number('J/kg'),'peak_temperature_k':number('K'),'peak_time_s':number('s'),'peak_heatflow_w':number('W'),'duration_s':number('s',0),'evidence':TEXT})
MODULATED=obj({'apparent_cp_real_j_kg_k':number('J/(kg*K)'),'apparent_cp_loss_j_kg_k':number('J/(kg*K)'),'apparent_cp_magnitude_j_kg_k':number('J/(kg*K)',0),'temperature_amplitude_k':number('K',0),'heatflow_amplitude_w':number('W',0),'phase_rad':number('rad'),'cycles':number('1',0),'fit_rms_w':number('W',0),'evidence':TEXT})
def _series(t,*ys):
 t=np.asarray(t,float);out=[np.asarray(y,float) for y in ys]
 if t.ndim!=1 or len(t)<3 or not np.all(np.isfinite(t)) or np.any(np.diff(t)<=0) or any(y.shape!=t.shape or not np.all(np.isfinite(y)) for y in out):raise ActionRejected('ANALYSIS_DATA')
 return [t]+out

def integrate_heatflow(time_s,heatflow_w,temperature_k,baseline_w,sample_mass_kg):
 t,q,T,b=_series(time_s,heatflow_w,temperature_k,baseline_w)
 if sample_mass_kg<=0:raise ActionRejected('ANALYSIS_DATA')
 corrected=q-b;energy=float(np.trapezoid(corrected,t));i=int(np.argmax(abs(corrected)))
 return dict(energy_j=energy,specific_enthalpy_j_kg=energy/sample_mass_kg,peak_temperature_k=float(T[i]),peak_time_s=float(t[i]),peak_heatflow_w=float(corrected[i]),duration_s=float(t[-1]-t[0]),evidence='Observed signed heatflow minus explicitly supplied baseline; trapezoid integration divided by supplied measured mass. Peak is the sampled absolute heatflow maximum, not extrapolated onset or automatic complete fusion enthalpy; no sample truth accessed.')

def apparent_modulated_cp(time_s,heatflow_w,temperature_k,sample_mass_kg,period_s,accept_apparatus_response):
 t,q,T=_series(time_s,heatflow_w,temperature_k);cycles=(t[-1]-t[0])/period_s
 if not accept_apparatus_response:raise ActionRejected('APPARATUS_RESPONSE_NOT_ACCEPTED')
 if cycles<2-1e-9 or abs(cycles-round(cycles))>1e-7 or max(np.diff(t))>period_s/12 or not np.allclose(np.diff(t),np.diff(t)[0],rtol=1e-7,atol=1e-9):raise ActionRejected('FOURIER_WINDOW')
 w=2*math.pi/period_s;x=t-t[0];matrix=np.column_stack([np.ones(len(t)),x,np.cos(w*x),np.sin(w*x)]);aq=np.linalg.lstsq(matrix,q,rcond=None)[0];aT=np.linalg.lstsq(matrix,T,rcond=None)[0];Q=complex(aq[2],-aq[3]);theta=complex(aT[2],-aT[3])
 if abs(theta)<1e-8:raise ActionRejected('TEMPERATURE_AMPLITUDE')
 cp=Q/(1j*w*theta)/sample_mass_kg
 return dict(apparent_cp_real_j_kg_k=float(cp.real),apparent_cp_loss_j_kg_k=float(-cp.imag),apparent_cp_magnitude_j_kg_k=float(abs(cp)),temperature_amplitude_k=float(abs(theta)),heatflow_amplitude_w=float(abs(Q)),phase_rad=float(np.angle(cp)),cycles=float(cycles),fit_rms_w=float(np.sqrt(np.mean((q-matrix@aq)**2))),evidence='OLS Fourier observed heatflow/(i*omega*observed sample-temperature amplitude)/supplied mass, with independent affine trend. Includes pan/transfer/sensor response and requires explicit acceptance; not calibrated true Cp, patented T4P, reversing/nonreversing separation, or a viscoelastic result.')

CAPS={};SLOTS={}
def cap(k,i,o,c,z,kind='device_command',visibility='agent',example=None):CAPS[k]=dict(input_schema=i,output_schema=o,component_ids=c,function_zh=z,kind=kind,visibility=visibility,example_arguments=copy.deepcopy(example or {}),effects=[])
def slot(k,s,u,z,origin='derived',visibility='agent',component='controller'):SLOTS[k]=dict(schema=s,unit=u,meaning=z,origin=origin,visibility=visibility,component=component,write=None)
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller','furnace'],'显式三节点热量模型与冷却附件','configuration','evaluator',PLANT_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_pan'],'冷态打开炉盖装入有限质量真实熔融参考','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.remove',obj({}),obj({'removed':BOOL}),['sample_pan'],'冷态回收样品及当前相态，质量不丢失','configuration','evaluator')
cap('gas.load',GAS,obj({'loaded':BOOL}),['purge_inlet'],'有限氮气库存与明确流量参考态','configuration','evaluator',GAS_EXAMPLE)
cap('gas.unload',obj({}),obj({'unloaded':BOOL}),['purge_inlet'],'关闭后回收剩余有限氮气','configuration','evaluator')
cap('gas.set_flow',obj({'flow_m3_s':dict(number('m^3/s',0,1e-5),**{'x-reference-conditions':'Gas.load explicitly supplied reference temperature and absolute pressure.'})}),obj({'flow_m3_s':number('m^3/s',0,1e-5)}),['purge_inlet'],'氮气吹扫参考流量目标；连续消耗由hoststep',example={'flow_m3_s':50e-6/60})
cap('gas.set_open',obj({'opened':BOOL}),obj({'opened':BOOL}),['purge_inlet'],'开闭有限氮气吹扫阀',example={'opened':True})
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_switch','controller'],'电源与热控制使能',example={'enabled':True})
cap('lid.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['lid'],'冷态开启炉盖工程姿态',example={'opened':False})
cap('thermal.set_temperature',obj({'temperature_k':number('K',93.15,998.15),'rate_k_s':number('K/s',.0001,20)}),obj({'target_k':number('K',93.15,998.15)}),['controller','furnace'],'设定有限速温度程序，不瞬变炉体',example={'temperature_k':424.7485,'rate_k_s':1.})
cap('thermal.disable',obj({}),obj({'enabled':BOOL}),['furnace'],'停止主动热控制；保留热量并被动散热')
cap('sensor.calibrate',CALIBRATION,CALIBRATION,['controller'],'外部独立双热流和温度校准','configuration',example=CALIBRATION_EXAMPLE)
cap('sensor.zero',obj({}),obj({'zeroed':BOOL}),['controller'],'空样品且热平衡时从观测通道归零')
cap('method.configure',METHOD,METHOD,['controller'],'分段线性程序、保持与显式正弦调制','configuration',example=METHOD_EXAMPLE)
cap('acquisition.start',obj({}),obj({'operation_id':TEXT}),['controller','furnace','sample_pan','reference_pan'],'异步DSC，质量有限且相变保能')
cap('acquisition.stop',obj({}),obj({'phase':enum(['cancelled'])}),['controller','furnace'],'停止新程序和主动热控制，保留相态及余热')
cap('acquisition.result',obj({}),RESULT,['controller'],'冻结的观测热流历史；owner限定','query')
cap('reference.query',obj({'reference_id':TEXT}),obj({'reference_json':TEXT,'template_source':TEXT}),['controller'],'真实熔融数据及原始来源，不是原机热流回放','query',example={'reference_id':SAMPLE_EXAMPLE['reference_id']})
cap('analysis.integrate',obj({'time_s':array(number('s',0),3,1000000),'heatflow_w':array(number('W'),3,1000000),'temperature_k':array(number('K'),3,1000000),'baseline_w':array(number('W'),3,1000000),'sample_mass_kg':number('kg',1e-12,1)}),INTEGRAL,['controller'],'外部观测基线相减、积分焓和采样峰值','analysis',example={'time_s':[0.,1.,2.],'heatflow_w':[0.,.001,0.],'temperature_k':[400.,401.,402.],'baseline_w':[0.,0.,0.],'sample_mass_kg':2e-6})
_t=np.linspace(0,120,121);cap('analysis.modulated_cp',obj({'time_s':array(number('s',0),25,1000000),'heatflow_w':array(number('W'),25,1000000),'temperature_k':array(number('K'),25,1000000),'sample_mass_kg':number('kg',1e-12,1),'period_s':number('s',1,10000),'accept_apparatus_response':BOOL}),MODULATED,['controller'],'明确包含仪器传热响应的观测复数表观热容','analysis',example={'time_s':_t.tolist(),'heatflow_w':(.001*np.cos(2*np.pi*_t/60)).tolist(),'temperature_k':(300+np.sin(2*np.pi*_t/60)).tolist(),'sample_mass_kg':2e-6,'period_s':60.,'accept_apparatus_response':True})
LEDGER=obj({k:number(u) for k,u in [('sample_loaded_kg','kg'),('sample_present_kg','kg'),('sample_recovered_kg','kg'),('sample_error_kg','kg'),('gas_loaded_mol','mol'),('gas_remaining_mol','mol'),('gas_exhausted_mol','mol'),('gas_recovered_mol','mol'),('gas_error_mol','mol'),('heater_input_j','J'),('cooler_removed_j','J'),('ambient_loss_j','J'),('initial_energy_j','J'),('sample_loaded_energy_j','J'),('sample_recovered_energy_j','J'),('furnace_stored_j','J'),('sample_stored_j','J'),('reference_stored_j','J'),('energy_error_j','J')]})
cap('ledger.read',obj({}),LEDGER,['controller'],'有限样品/气体质量及炉盘相变总能量账本','query','evaluator')
cap('sim.step',obj({'dt_s':number('s',0,3600)}),obj({'time_s':number('s',0)}),['controller'],'唯一宿主时钟','simulation','evaluator',{'dt_s':.1})
cap('emergency.stop',obj({}),obj({'latched':BOOL}),['controller'],'软件停机保留余热和有限相态')
cap('emergency.reset',obj({}),obj({'latched':BOOL}),['controller'],'冷态明确复位，不自动重启')
UNSUPPORTED=['autosampler.sequence','t4p.native','analysis.tg','analysis.crystallization_kinetics','mdsc.reversing_nonreversing','pan.pressure_reaction','photocalorimetry.acquire','hardware.connect','noise.empirical','accuracy.certification','cooler.external_geometry']
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'明确未实现：'+k)
for k,z,c in [('plant.configured','工程配置已接受','controller'),('power.enabled','电源','power_switch'),('lid.opened','炉盖开启','lid'),('sample.present','样品存在','sample_pan'),('gas.present','有限气源连接','purge_inlet'),('gas.opened','吹扫阀开启','purge_inlet'),('thermal.enabled','主动热控制使能','furnace'),('emergency.latched','软件停机锁存','controller')]:slot(k,BOOL,'1',z,component=c)
slot('lid.lift_m',number('m',0,.06),'m','工程退盖距离，不等于纯竖直升程','scene',component='lid');slot('power.angle_rad',number('rad',0,.28),'rad','开关姿态','scene',component='power_switch')
for k,z in [('thermal.target_k','当前程序目标'),('thermal.final_target_k','预调温最终目标')]:slot(k,number('K',93.15,998.15),'K',z,'setpoint',component='furnace')
slot('gas.flow_m3_s',number('m^3/s',0,1e-5),'m^3/s','明确参考温压的流量目标','setpoint',component='purge_inlet')
for k,u,z in [('furnace.indicated_k','K','独立校准炉温'),('sample.indicated_k','K','样品盘温度观测代理'),('reference.indicated_k','K','参考盘温度观测代理'),('sample.heatflow_w','W','样品传热通道'),('reference.heatflow_w','W','参考盘传热通道'),('heatflow.indicated_w','W','差分热流，吸热为正')]:slot(k,nullable(number(u)),u,z,component='controller')
for k,u,z in [('truth.furnace_k','K','真实炉温'),('truth.sample_k','K','真实样品温度'),('truth.reference_k','K','真实参考温度'),('truth.liquid_fraction','1','有限相变分数'),('truth.sample_mass_kg','kg','真实有限样品质量'),('truth.gas_mol','mol','剩余气源'),('truth.heater_w','W','热控制功率')]:slot(k,number(u),u,z,'process_truth','evaluator')
slot('clock.time_s',number('s',0),'s','宿主时钟');slot('acquisition.phase',PHASE,'1','异步操作阶段');slot('acquisition.elapsed_s',number('s',0),'s','实际采集时间');slot('acquisition.points',integer(0,2000000),'1','观测点数');slot('acquisition.segment_index',integer(0,99),'1','温度段索引');slot('acquisition.stage',enum(['ramp','hold']),'1','当前段');slot('panel.temperature_text',TEXT,'1','屏幕炉温文本，无电为空');slot('panel.heatflow_text',TEXT,'1','屏幕热流文本，无电为空')

cap('sampler.move',obj({'x_m':number('m',0,.330),'y_m':number('m',0,.110),'z_m':number('m',0,.060)}),obj({'accepted':BOOL}),['sampler_x','sampler_y','sampler_z'],'工程XYZ定位；真实夹取/54位自动换样未实现',example={'x_m':.1,'y_m':.05,'z_m':.06})
for axis,hi in [('x',.330),('y',.110),('z',.060)]:
 slot('sampler.'+axis+'_m',number('m',0,hi),'m','实际采样臂'+axis+'位置','scene',component='sampler_'+axis)
 slot('sampler.target_'+axis+'_m',number('m',0,hi),'m','采样臂'+axis+'目标','setpoint',component='sampler_'+axis)
slot('sampler.moving',BOOL,'1','采样臂正在运动');slot('lid.moving',BOOL,'1','炉盖正在移动');slot('lid.target_m',number('m',0,.060),'m','炉盖目标','setpoint',component='lid')

def phase_fraction(temperature_k,melting_temperature_k,width_k):
 return min(1.,max(0.,(temperature_k-(melting_temperature_k-width_k/2))/width_k))
def enthalpy(temperature_k,ambient_k,capacity_j_k,latent_j=0.,melting_temperature_k=1e9,width_k=1.):
 return capacity_j_k*(temperature_k-ambient_k)+latent_j*phase_fraction(temperature_k,melting_temperature_k,width_k)
def enthalpy_temperature(energy_j,ambient_k,capacity_j_k,latent_j=0.,melting_temperature_k=1e9,width_k=1.):
 # Exact monotone piecewise inverse, including added implicit conductance in capacity.
 low=melting_temperature_k-width_k/2;high=low+width_k;elo=capacity_j_k*(low-ambient_k);ehi=capacity_j_k*(high-ambient_k)+latent_j
 if energy_j<=elo:return ambient_k+energy_j/capacity_j_k
 if energy_j>=ehi:return ambient_k+(energy_j-latent_j)/capacity_j_k
 return low+(energy_j-elo)/(capacity_j_k+latent_j/width_k)

class TaDsc2500Runtime:
 def __init__(self,instance_id='dsc2500_01'):
  self.instance_id=instance_id;self.plant=None;self.sample=None;self.reference=None;self.gas=None;self.power=False;self.lid_lift=.06;self.lid_target=.06;self.sampler=[0.,0.,.06];self.sampler_target=self.sampler.copy();self.thermal_enabled=False;self.emergency=False;self.gas_open=False;self.flow=0.;self.gas_remaining=0.;self.furnace=298.15;self.sample_temperature=298.15;self.reference_temperature=298.15;self.sample_energy=0.;self.target=298.15;self.final_target=298.15;self.rate=1.;self.heater_power=0.;self.obs_s=0.;self.obs_r=0.;self.mass_loaded=0.;self.mass_recovered=0.;self.gas_loaded=0.;self.gas_used=0.;self.gas_recovered=0.;self.heater_input=0.;self.cooler_removed=0.;self.ambient_loss=0.;self.initial_energy=0.;self.loaded_energy=0.;self.recovered_energy=0.;self.method=copy.deepcopy(METHOD_EXAMPLE);self.calibration=copy.deepcopy(CALIBRATION_EXAMPLE);self.phase='idle';self.elapsed=0.;self.segment_index=0;self.stage='ramp';self.rows=[];self.operation_id=None;self.operation_method=None;self.operation_calibration=None;self.operation_reference=None;self.operation_source=None;self.operation_start=0.;self.error=None;self.records={};self.schedule=[];self.duration=0.;self._counter=0;self.time_s=0.;self._ticks=0;self._remainder=0.;self.revision=0;self._requests={};self._owners={};self._cancel_operations={};self._principals={'agent':'agent','evaluator':'evaluator'}
 def register_principal(self,principal_id,visibility='agent'):
  if not isinstance(principal_id,str) or not principal_id or principal_id in self._principals or visibility not in ('agent','debug','evaluator'):raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='agent'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return TaDsc2500Session(self,principal_id)
 def describe(self):return export_ta_dsc2500_contract_metadata()
 def _limits(self):
  lo,hi=COOLERS[self.plant['cooler']];return (self.plant['ambient_k'] if lo is None else lo),hi
 def _temperature_domain(self,t):
  lo,hi=self._limits()
  if not lo<=t<=hi:raise ActionRejected('COOLER_TEMPERATURE_RANGE')
 def _cp(self):return self.plant['sample_pan_capacity_j_k']+(self.sample['mass_kg']*self.sample['heat_capacity_j_kg_k'] if self.sample else 0.)
 def _latent(self):return self.sample['mass_kg']*self.reference['values']['fusion_enthalpy_j_kg'] if self.sample else 0.
 def _tm(self):return self.reference['values']['melting_temperature_k'] if self.sample else 1e9
 def _width(self):return self.sample['transition_width_k'] if self.sample else 1.
 def _sample_h(self,T):return enthalpy(T,self.plant['ambient_k'],self._cp(),self._latent(),self._tm(),self._width())
 def _fraction(self):return phase_fraction(self.sample_temperature,self._tm(),self._width()) if self.sample else 0.
 def _moving(self):return bool(any(abs(a-b)>1e-12 for a,b in zip(self.sampler,self.sampler_target)))
 def _lid_moving(self):return bool(abs(self.lid_lift-self.lid_target)>1e-12)
 def _cold(self):return max(self.furnace,self.sample_temperature,self.reference_temperature)<=self.plant['safe_access_temperature_k']
 def _mechanical(self):
  if self.phase=='running':raise ActionRejected('BUSY')
  if not self._cold():raise ActionRejected('HOT_CELL')
  if self.thermal_enabled:raise ActionRejected('THERMAL_CONTROL_ACTIVE')
 def _indicated(self):
  if not self.power:return dict(furnace=None,sample=None,reference=None,qs=None,qr=None,q=None)
  p=self.plant;c=self.calibration
  f=lambda t:(p['temperature_gain']*t+p['temperature_offset_k']-c['temperature_zero_k'])/c['temperature_gain_correction']
  qs=(self.obs_s-c['sample_flow_zero_w'])/c['sample_flow_gain_correction'];qr=(self.obs_r-c['reference_flow_zero_w'])/c['reference_flow_gain_correction']
  return dict(furnace=f(self.furnace),sample=f(self.sample_temperature),reference=f(self.reference_temperature),qs=qs,qr=qr,q=qs-qr)
 def read(self,role='evaluator'):
  a=self._indicated() if self.plant else dict(furnace=None,sample=None,reference=None,qs=None,qr=None,q=None)
  d={'plant.configured':self.plant is not None,'power.enabled':self.power,'lid.opened':bool(self.lid_lift>=.06-1e-12),'sample.present':self.sample is not None,'gas.present':self.gas is not None,'gas.opened':self.gas_open,'thermal.enabled':self.thermal_enabled,'emergency.latched':self.emergency,'lid.lift_m':self.lid_lift,'lid.target_m':self.lid_target,'lid.moving':self._lid_moving(),'sampler.moving':self._moving(),'power.angle_rad':.28 if self.power else 0.,'thermal.target_k':self.target,'thermal.final_target_k':self.final_target,'gas.flow_m3_s':self.flow,'furnace.indicated_k':a['furnace'],'sample.indicated_k':a['sample'],'reference.indicated_k':a['reference'],'sample.heatflow_w':a['qs'],'reference.heatflow_w':a['qr'],'heatflow.indicated_w':a['q'],'truth.furnace_k':self.furnace,'truth.sample_k':self.sample_temperature,'truth.reference_k':self.reference_temperature,'truth.liquid_fraction':self._fraction(),'truth.sample_mass_kg':self.sample['mass_kg'] if self.sample else 0.,'truth.gas_mol':self.gas_remaining,'truth.heater_w':self.heater_power,'clock.time_s':self.time_s,'acquisition.phase':self.phase,'acquisition.elapsed_s':self.elapsed,'acquisition.points':len(self.rows),'acquisition.segment_index':self.segment_index,'acquisition.stage':self.stage,'panel.temperature_text':'' if a['furnace'] is None else f"{a['furnace']-273.15:.1f}",'panel.heatflow_text':'' if a['q'] is None else f"{a['q']*1000:.3f}"}
  for i,axis in enumerate('xyz'):d['sampler.'+axis+'_m']=self.sampler[i];d['sampler.target_'+axis+'_m']=self.sampler_target[i]
  return {k:v for k,v in d.items() if role!='agent' or SLOTS[k]['visibility']=='agent'}
 def _ledger(self):
  p=self.plant;Ef=p['furnace_capacity_j_k']*(self.furnace-p['ambient_k']) if p else 0.;Er=p['reference_pan_capacity_j_k']*(self.reference_temperature-p['ambient_k']) if p else 0.;mass=self.sample['mass_kg'] if self.sample else 0.
  return dict(sample_loaded_kg=self.mass_loaded,sample_present_kg=mass,sample_recovered_kg=self.mass_recovered,sample_error_kg=self.mass_loaded-self.mass_recovered-mass,gas_loaded_mol=self.gas_loaded,gas_remaining_mol=self.gas_remaining,gas_exhausted_mol=self.gas_used,gas_recovered_mol=self.gas_recovered,gas_error_mol=self.gas_loaded-self.gas_remaining-self.gas_used-self.gas_recovered,heater_input_j=self.heater_input,cooler_removed_j=self.cooler_removed,ambient_loss_j=self.ambient_loss,initial_energy_j=self.initial_energy,sample_loaded_energy_j=self.loaded_energy,sample_recovered_energy_j=self.recovered_energy,furnace_stored_j=Ef,sample_stored_j=self.sample_energy,reference_stored_j=Er,energy_error_j=self.initial_energy+self.loaded_energy-self.recovered_energy+self.heater_input-self.cooler_removed-self.ambient_loss-Ef-self.sample_energy-Er)
 def _row(self):
  a=self._indicated();return dict(time_s=self.time_s,elapsed_s=self.elapsed,segment_index=self.segment_index,stage=self.stage,target_k=self.target,furnace_k=a['furnace'],sample_k=a['sample'],reference_k=a['reference'],sample_heatflow_w=a['qs'],reference_heatflow_w=a['qr'],heatflow_w=a['q'])
 def _record(self):
  r=self._row()
  if self.rows and abs(self.rows[-1]['elapsed_s']-r['elapsed_s'])<1e-10:self.rows[-1]=r
  else:self.rows.append(r)
 def _result(self):
  if self.phase!='running' and self.operation_id in self.records:return copy.deepcopy(self.records[self.operation_id])
  return dict(operation_id=self.operation_id,phase=self.phase,elapsed_s=self.elapsed,rows=copy.deepcopy(self.rows),reference_id=self.operation_reference,template_source=self.operation_source,method_json=json.dumps(self.operation_method,sort_keys=True) if self.operation_method else None,calibration_json=json.dumps(self.operation_calibration,sort_keys=True) if self.operation_calibration else None,evidence='Finite conservative three-node DSC engineering response anchored by published melting temperature and fusion enthalpy. Cp, conductances, pan capacities, reversible mushy width, cooling power and sensors explicitly assumed; source is not a complete/native DSC heatflow trace. Observed difference=calibrated sample heatflow minus reference heatflow; endothermic positive. No proprietary T4P algorithm or certified accuracy. Purge is a finite isothermal reference-state N2 inventory; purge sensible enthalpy exchange is not solved. Results contain no hidden sample mass, phase fraction or true enthalpy.',error_code=self.error)
 def _finish(self,phase,error=None):
  self.phase=phase;self.error=error;self.thermal_enabled=False;self.heater_power=0.;self._record();self.records[self.operation_id]=self._result()
 def invoke(self,capability,**kwargs):
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
   self.plant=copy.deepcopy(a);self.furnace=self.sample_temperature=self.reference_temperature=self.target=self.final_target=a['ambient_k'];return {'configured':True}
  if not self.plant and k not in ['reference.query','analysis.integrate','analysis.modulated_cp','acquisition.result','ledger.read']:raise ActionRejected('NOT_CONFIGURED')
  if k=='sample.load':
   self._mechanical()
   if self.sample is not None:raise ActionRejected('ALREADY_LOADED')
   if self.lid_lift<.06-1e-12 or self._moving() or self._lid_moving() or self.sampler[:2]!=[0.,0.] or self.sampler[2]<.06-1e-12:raise ActionRejected('CELL_NOT_ACCESSIBLE')
   if not a['accept_engineering'] or not a['accept_reversible_phase_change']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   ref=library()['substances'].get(a['reference_id'])
   if not ref or not ref.get('runtime_usable'):raise ActionRejected('INVALID_REFERENCE')
   try:tm=float(ref['values']['melting_temperature_k']);latent=float(ref['values']['fusion_enthalpy_j_kg'])
   except (KeyError,TypeError,ValueError):raise ActionRejected('INVALID_REFERENCE')
   if not math.isfinite(tm) or not math.isfinite(latent) or latent<=0:raise ActionRejected('INVALID_REFERENCE')
   if not a['accept_condition_transfer']:raise ActionRejected('CONDITION_TRANSFER_NOT_ACCEPTED')
   self._temperature_domain(a['initial_temperature_k'])
   if a['initial_temperature_k']>self.plant['safe_access_temperature_k']:raise ActionRejected('HOT_SAMPLE_LOAD')
   self.sample=copy.deepcopy(a);self.reference=copy.deepcopy(ref);added=enthalpy(a['initial_temperature_k'],self.plant['ambient_k'],a['mass_kg']*a['heat_capacity_j_kg_k'],self._latent(),self._tm(),self._width());self.sample_energy+=added;self.loaded_energy+=added;self.sample_temperature=enthalpy_temperature(self.sample_energy,self.plant['ambient_k'],self._cp(),self._latent(),self._tm(),self._width());self.mass_loaded+=a['mass_kg'];return {'loaded':True}
  if k=='sample.remove':
   self._mechanical()
   if self.sample is None:raise ActionRejected('NOT_LOADED')
   if self.lid_lift<.06-1e-12 or self._moving() or self._lid_moving():raise ActionRejected('CELL_NOT_ACCESSIBLE')
   self.mass_recovered+=self.sample['mass_kg'];old=self.sample_energy;self.sample=None;self.reference=None;self.sample_energy=self._sample_h(self.sample_temperature);self.recovered_energy+=old-self.sample_energy;return {'removed':True}
  if k=='gas.load':
   if self.gas is not None or self.gas_open or self.phase=='running':raise ActionRejected('BUSY')
   if not a['accept_reference_conditions']:raise ActionRejected('REFERENCE_CONDITIONS_NOT_ACCEPTED')
   self.gas=copy.deepcopy(a);self.gas_remaining=a['amount_mol'];self.gas_loaded+=a['amount_mol'];return {'loaded':True}
  if k=='gas.unload':
   if self.gas is None:raise ActionRejected('NOT_LOADED')
   if self.gas_open or self.phase=='running':raise ActionRejected('BUSY')
   self.gas_recovered+=self.gas_remaining;self.gas_remaining=0.;self.gas=None;return {'unloaded':True}
  if k=='gas.set_flow':
   if self.phase=='running':raise ActionRejected('BUSY')
   self.flow=a['flow_m3_s'];return {'flow_m3_s':self.flow}
  if k=='gas.set_open':
   if self.phase=='running':raise ActionRejected('BUSY')
   if a['opened'] and (not self.power or not self.gas or self.gas_remaining<=0 or self.flow<=0):raise ActionRejected('NO_GAS')
   self.gas_open=a['opened'];return {'opened':self.gas_open}
  if k=='power.set':
   if self.phase=='running':raise ActionRejected('BUSY')
   self.power=a['enabled']
   if not self.power:self.thermal_enabled=False;self.gas_open=False;self.heater_power=0.
   return {'enabled':self.power}
  if k=='lid.set':
   self._mechanical()
   if self._moving():raise ActionRejected('SAMPLER_MOVING')
   if not a['opened'] and (self.sampler[:2]!=[0.,0.] or self.sampler[2]<.06-1e-12):raise ActionRejected('SAMPLER_NOT_PARKED')
   self.lid_target=.06 if a['opened'] else 0.;return {'opened':a['opened']}
  if k=='sampler.move':
   self._mechanical()
   if not self.power or self._lid_moving():raise ActionRejected('NOT_READY')
   if self._moving():raise ActionRejected('SAMPLER_MOVING')
   xy=abs(a['x_m']-self.sampler[0])+abs(a['y_m']-self.sampler[1])>1e-12
   if self.lid_lift<.06-1e-12 or (xy and (self.sampler[2]<.06-1e-12 or a['z_m']<.06-1e-12)):raise ActionRejected('SAMPLER_CLEARANCE')
   if a['z_m']<.06-1e-12 and not any(abs(a['x_m']-x)<1e-7 and abs(a['y_m']-y)<1e-7 for x,y in SAMPLER_REGISTERED_XY):raise ActionRejected('SAMPLER_TARGET_NOT_REGISTERED')
   self.sampler_target=[a['x_m'],a['y_m'],a['z_m']];return {'accepted':True}
  if k=='thermal.set_temperature':
   if self.phase=='running':raise ActionRejected('BUSY')
   self._ready(False);self._temperature_domain(a['temperature_k'])
   if a['rate_k_s']>self.plant['maximum_program_rate_k_s']:raise ActionRejected('PROGRAM_RATE')
   self.final_target=a['temperature_k'];self.rate=a['rate_k_s'];self.thermal_enabled=True;return {'target_k':self.final_target}
  if k=='thermal.disable':
   if self.phase=='running':raise ActionRejected('BUSY')
   self.thermal_enabled=False;self.heater_power=0.;return {'enabled':False}
  if k=='sensor.calibrate':
   if self.phase=='running':raise ActionRejected('BUSY')
   self.calibration=copy.deepcopy(a);return copy.deepcopy(a)
  if k=='sensor.zero':
   if self.phase=='running' or self.sample or not self.power or self.thermal_enabled or max(abs(self.furnace-self.sample_temperature),abs(self.furnace-self.reference_temperature),abs(self.furnace-self.plant['ambient_k']))>.001:raise ActionRejected('ZERO_REQUIRES_EMPTY_EQUILIBRIUM')
   self.calibration['sample_flow_zero_w']=self.obs_s;self.calibration['reference_flow_zero_w']=self.obs_r;return {'zeroed':True}
  if k=='method.configure':
   if self.phase=='running':raise ActionRejected('BUSY')
   if abs(a['sample_interval_s']/DT-round(a['sample_interval_s']/DT))>1e-9:raise ActionRejected('SAMPLING_GRID')
   amp=a['modulation_amplitude_k'];omega=2*math.pi/a['modulation_period_s']
   for t in [a['start_temperature_k']]+[x['target_k'] for x in a['segments']]:self._temperature_domain(t-amp);self._temperature_domain(t+amp)
   if a['sample_interval_s']>a['modulation_period_s']/12 and amp>0:raise ActionRejected('MODULATION_SAMPLING')
   if any(x['rate_k_s']+amp*omega>self.plant['maximum_program_rate_k_s']+1e-12 for x in a['segments']):raise ActionRejected('PROGRAM_RATE')
   self.method=copy.deepcopy(a);return copy.deepcopy(a)
  if k=='acquisition.start':
   self._ready(True)
   if self.phase=='running':raise ActionRejected('BUSY')
   observed=self._indicated()
   if max(abs(observed[x]-self.method['start_temperature_k']) for x in ['furnace','sample','reference'])>self.method['start_tolerance_k']:raise ActionRejected('START_NOT_EQUILIBRATED')
   self.schedule=[];t=0.;previous=self.method['start_temperature_k']
   for i,s in enumerate(self.method['segments']):
    ramp=abs(s['target_k']-previous)/s['rate_k_s']
    if ramp>0:self.schedule.append((t,t+ramp,previous,s['target_k'],i,'ramp'));t+=ramp
    if s['hold_s']>0:self.schedule.append((t,t+s['hold_s'],s['target_k'],s['target_k'],i,'hold'));t+=s['hold_s']
    previous=s['target_k']
   if t<=0:raise ActionRejected('EMPTY_PROGRAM')
   if t/self.method['sample_interval_s']>1999900:raise ActionRejected('PROGRAM_TOO_LONG')
   self.duration=t;self._counter+=1;self.operation_id=f'{self.instance_id}:dsc:{self._counter}';self.operation_start=self.time_s;self.phase='running';self.elapsed=0.;self.segment_index=0;self.stage=self.schedule[0][5];self.rows=[];self.error=None;self.operation_method=copy.deepcopy(self.method);self.operation_calibration=copy.deepcopy(self.calibration);self.operation_reference=self.sample['reference_id'];self.operation_source=json.dumps(self.reference.get('source',{}),sort_keys=True);self.target=self.method['start_temperature_k'];self.final_target=previous;self.thermal_enabled=True;self._record();return {'operation_id':self.operation_id}
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self._finish('cancelled');return {'phase':'cancelled'}
  if k=='acquisition.result':return self._result()
  if k=='reference.query':
   r=library()['substances'].get(a['reference_id'])
   if not r:raise ActionRejected('INVALID_REFERENCE')
   return {'reference_json':json.dumps(r,sort_keys=True,allow_nan=False),'template_source':json.dumps(r.get('source',{}),sort_keys=True)}
  if k=='analysis.integrate':return integrate_heatflow(**a)
  if k=='analysis.modulated_cp':return apparent_modulated_cp(**a)
  if k=='ledger.read':return self._ledger()
  if k=='emergency.stop':
   self.emergency=True;self.thermal_enabled=False;self.heater_power=0.;self.gas_open=False;self.sampler_target=self.sampler.copy();self.lid_target=self.lid_lift
   if self.phase=='running':self._finish('failed','SOFTWARE_STOP')
   return {'latched':True}
  if k=='emergency.reset':
   if not self._cold():raise ActionRejected('HOT_CELL')
   self.emergency=False;return {'latched':False}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _ready(self,require_sample):
  if not self.power or self.emergency or self.lid_lift>1e-12 or self._lid_moving() or self._moving():raise ActionRejected('NOT_READY')
  if require_sample and not self.sample:raise ActionRejected('NO_SAMPLE')
  if require_sample and (not self.gas_open or self.gas_remaining<=0 or self.flow<=0):raise ActionRejected('NO_GAS')
 def _program(self,t):
  for a,b,start,end,i,stage in self.schedule:
   if t<=b+1e-10:return start+(end-start)*min(1.,max(0.,(t-a)/(b-a)))+self.method['modulation_amplitude_k']*math.sin(2*math.pi*t/self.method['modulation_period_s']),i,stage
  return self.schedule[-1][3],self.schedule[-1][4],self.schedule[-1][5]
 def _heat_step(self,dt,target):
  p=self.plant;ambient=p['ambient_k'];cf=p['furnace_capacity_j_k'];cr=p['reference_pan_capacity_j_k'];gs=p['sample_conductance_w_k'];gr=p['reference_conductance_w_k'];oldf=self.furnace;oldr=self.reference_temperature;olds=self.sample_energy;cs=self._cp();latent=self._latent();tm=self._tm();width=self._width();active=self.power and self.thermal_enabled and not self.emergency
  # Endpoint implicit coupling, a monotone piecewise-affine scalar equation. Power saturates continuously.
  def evaluate(Tf):
   Ts=enthalpy_temperature(olds+dt*gs*(Tf-ambient),ambient,cs+dt*gs,latent,tm,width);Tr=(cr*oldr+dt*gr*Tf)/(cr+dt*gr);command=p['controller_gain_w_k']*(target-Tf) if active else 0.;heat=min(p['heater_max_w'],max(0.,command));cool=0.
   if active and command<0:
    available=p['facs_conductance_w_k']*max(0.,Tf-ambient) if p['cooler']=='FACS' else p['cooler_max_w']
    cool=min(-command,p['cooler_max_w'],available)
   loss=p['ambient_conductance_w_k']*(Tf-ambient);qs=gs*(Tf-Ts);qr=gr*(Tf-Tr);residual=cf*(Tf-oldf)-dt*(heat-cool-loss-qs-qr)
   return residual,Ts,Tr,heat,cool,loss,qs,qr
  # all breakpoints are physical min/max temperatures; stable for arbitrarily stiff accepted G/C.
  lo=min(oldf,oldr,self.sample_temperature,ambient,target);hi=max(oldf,oldr,self.sample_temperature,ambient,target)
  if hi-lo<1e-12:Tf=lo
  else:
   from scipy.optimize import brentq
   Tf=float(brentq(lambda T:evaluate(T)[0],lo,hi,xtol=1e-12,rtol=1e-14))
  _,Ts,Tr,heat,cool,loss,qs,qr=evaluate(Tf);self.furnace=Tf;self.sample_temperature=float(Ts);self.reference_temperature=float(Tr);self.sample_energy=olds+dt*qs;self.heater_input+=dt*heat;self.cooler_removed+=dt*cool;self.ambient_loss+=dt*loss;self.heater_power=heat-cool
  sensor=-math.expm1(-dt/p['sensor_tau_s']);self.obs_s+=sensor*(p['sample_flow_gain']*qs+p['sample_flow_offset_w']-self.obs_s);self.obs_r+=sensor*(p['reference_flow_gain']*qr+p['reference_flow_offset_w']-self.obs_r)
 def _advance(self,dt):
  remaining=dt
  while remaining>1e-12:
   h=min(.01,remaining);running=self.phase=='running'
   if running:
    boundaries=[self.duration,self.method['timeout_s']]+[x[1] for x in self.schedule]
    future=[x-self.elapsed for x in boundaries if x>self.elapsed+1e-10]
    if future:h=min(h,min(future))
   gasrate=self.flow*self.gas['reference_pressure_pa']/(R_GAS*self.gas['reference_temperature_k']) if self.gas_open and self.gas else 0.
   if gasrate>0:h=min(h,self.gas_remaining/gasrate)
   if h<=0:
    self.gas_open=False
    if running:self._finish('failed','GAS_EXHAUSTED')
    continue
   if running:self.target,self.segment_index,self.stage=self._program(self.elapsed+h)
   elif self.thermal_enabled:self.target+=max(-self.rate*h,min(self.rate*h,self.final_target-self.target))
   if self.plant:
    self._heat_step(h,self.target)
    if self.power and not self.emergency:
     for i in range(3):self.sampler[i]+=max(-self.plant['sampler_speed_m_s']*h,min(self.plant['sampler_speed_m_s']*h,self.sampler_target[i]-self.sampler[i]))
     self.lid_lift+=max(-self.plant['lid_speed_m_s']*h,min(self.plant['lid_speed_m_s']*h,self.lid_target-self.lid_lift))
   used=min(self.gas_remaining,gasrate*h);self.gas_remaining-=used;self.gas_used+=used;self.time_s+=h
   if running:self.elapsed+=h
   remaining-=h
   if gasrate>0 and self.gas_remaining<=max(1e-30,gasrate*1e-13):
    # Charge the tiny final inventory rather than silently discarding mass.
    self.gas_used+=self.gas_remaining;self.gas_remaining=0.;self.gas_open=False
    if self.phase=='running':self._finish('failed','GAS_EXHAUSTED')
   if self.phase=='running':
    if self.elapsed>=self.method['timeout_s']-1e-9 and self.elapsed<self.duration-1e-9:self._finish('failed','TIMEOUT')
    elif self.elapsed>=self.duration-1e-9:self.elapsed=self.duration;self._finish('succeeded')
    elif any(abs(self.elapsed-x[1])<1e-9 for x in self.schedule):self._record()
 def step(self,dt_s):
  validate(number('s',0,3600),dt_s);self._remainder+=dt_s;ticks=int((self._remainder+1e-10)/DT);self._remainder-=ticks*DT
  if abs(self._remainder)<1e-10:self._remainder=0.
  for _ in range(ticks):
   self._advance(DT);self._ticks+=1;self.time_s=self._ticks*DT;self.revision+=1
   if self.phase=='running' and abs(self.elapsed/self.method['sample_interval_s']-round(self.elapsed/self.method['sample_interval_s']))<1e-7:self._record()
  return self.read()

class TaDsc2500Session:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,instance_id):
  if instance_id!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  meta=export_ta_dsc2500_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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

OBSERVATION_STATES=['furnace.indicated_k','sample.indicated_k','reference.indicated_k','sample.heatflow_w','reference.heatflow_w','heatflow.indicated_w','panel.temperature_text','panel.heatflow_text']
EFFECTS={'plant.configure':['plant.configured','truth.furnace_k','truth.sample_k','truth.reference_k','thermal.target_k','thermal.final_target_k'],
'sample.load':['sample.present','truth.sample_k','truth.sample_mass_kg','truth.liquid_fraction']+OBSERVATION_STATES,
'sample.remove':['sample.present','truth.sample_mass_kg','truth.liquid_fraction'],
'gas.load':['gas.present','truth.gas_mol'],'gas.unload':['gas.present','truth.gas_mol'],'gas.set_flow':['gas.flow_m3_s'],'gas.set_open':['gas.opened'],
'power.set':['power.enabled','power.angle_rad','thermal.enabled','gas.opened','truth.heater_w']+OBSERVATION_STATES,
'lid.set':['lid.target_m','lid.moving'],'sampler.move':['sampler.target_x_m','sampler.target_y_m','sampler.target_z_m','sampler.moving'],
'thermal.set_temperature':['thermal.final_target_k','thermal.enabled'],'thermal.disable':['thermal.enabled','truth.heater_w'],
'sensor.calibrate':OBSERVATION_STATES,'sensor.zero':OBSERVATION_STATES,
'acquisition.start':['acquisition.phase','acquisition.elapsed_s','acquisition.points','acquisition.segment_index','acquisition.stage','thermal.target_k','thermal.final_target_k','thermal.enabled'],
'acquisition.stop':['acquisition.phase','acquisition.points','thermal.enabled','truth.heater_w'],
'emergency.stop':['emergency.latched','acquisition.phase','acquisition.points','thermal.enabled','gas.opened','truth.heater_w','sampler.target_x_m','sampler.target_y_m','sampler.target_z_m','sampler.moving','lid.target_m','lid.moving'],
'emergency.reset':['emergency.latched'],'sim.step':list(SLOTS)}
for k,v in EFFECTS.items():CAPS[k]['effects']=v
PHYSICS_EFFECTS=[k for k in SLOTS if k not in ['plant.configured','power.enabled','power.angle_rad','sample.present','gas.present','emergency.latched','truth.sample_mass_kg','gas.flow_m3_s']]
def export_ta_dsc2500_contract_metadata():
 slots={};caps={}
 for k,v in SLOTS.items():slots[k]=dict(meaning_zh=v['meaning'],component_ids=[v['component']],value_schema=copy.deepcopy(v['schema']),unit=v['unit'],reference_conditions='SI. Heatflow endothermic positive. Purge volume flow at gas.load reference absolute pressure/temperature, no unspecified STP. FACS ambient..998.15K; other accessories have separate bounds. lid.lift_m is diagonal engineering retreat distance, not vertical height.',origin=v['origin'],visibility=v['visibility'],access='read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'TaDsc2500Session.read(state_ids=[{k!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description='Host fixed0.1s with<=0.01s implicit thermal steps and program/gas events; observed rows also contain segment endpoints. No read advances time.'),source_ids=['simulation_assumption'])
 errors=['UNKNOWN_CAPABILITY','UNKNOWN_INSTANCE','UNKNOWN_OPERATION','INVALID_ARGUMENT','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','BUSY','NOT_RUNNING','INITIALIZATION_ONLY','MODEL_NOT_ACCEPTED','PLANT_CONFIGURATION','NOT_CONFIGURED','HOT_CELL','THERMAL_CONTROL_ACTIVE','CELL_NOT_ACCESSIBLE','ALREADY_LOADED','NOT_LOADED','INVALID_REFERENCE','CONDITION_TRANSFER_NOT_ACCEPTED','HOT_SAMPLE_LOAD','REFERENCE_CONDITIONS_NOT_ACCEPTED','NO_GAS','NO_SAMPLE','NOT_READY','SAMPLER_MOVING','SAMPLER_NOT_PARKED','SAMPLER_CLEARANCE','SAMPLER_TARGET_NOT_REGISTERED','PROGRAM_RATE','COOLER_TEMPERATURE_RANGE','ZERO_REQUIRES_EMPTY_EQUILIBRIUM','SAMPLING_GRID','MODULATION_SAMPLING','START_NOT_EQUILIBRATED','EMPTY_PROGRAM','PROGRAM_TOO_LONG','GAS_EXHAUSTED','TIMEOUT','SOFTWARE_STOP','ANALYSIS_DATA','FOURIER_WINDOW','TEMPERATURE_AMPLITUDE','APPARATUS_RESPONSE_NOT_ACCEPTED']
 for k,c in CAPS.items():
  un=k in UNSUPPORTED;async_=k=='acquisition.start';caps[k]={key:copy.deepcopy(c[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']};caps[k].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ['scene','robot','hardware'] else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.ta_dsc2500.TaDsc2500Session.invoke',invocation=f'session.invoke(instance_id="dsc2500_01",capability_id={k!r},arguments={c["example_arguments"]!r},request_id="unique")'),preconditions=[] if c['kind'] in ['query','analysis'] or un else [dict(description_zh='冷态开盖/采样臂清空、冷却附件温域、显式物性与工程假设、有限吹扫库存、开始温度观测平衡；拒绝不改变状态',state_ids=['power.enabled','lid.lift_m','sample.present','gas.opened','thermal.enabled','acquisition.phase'],enforced_by='runtime',check='atomic invoke, physical interlocks and host registered Session')],state_effects=[dict(state_id=x,timing='immediate',description_zh='实际命令即时变化') for x in c['effects']],observes=[],model_effects=dict(mode='none',description_zh='关节联动由独立场景验证；样品相变非网格熔化，54位自动夹取未实现',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='全部有限速温度段和保持时间结束；不保证达到完全熔融或热平衡' if async_ else '返回命令接受；目标与实际分开，运动和热交换等待宿主step',completion_state_ids=['acquisition.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','furnace','sample_pan','reference_pan','purge','sampler'],partial_failure_zh='取消/气耗尽保留真实相态、余热和库存；停止主动热控制后继续被动热交换，历史结果冻结'),errors=[dict(code=x,meaning_zh=x,side_effects='partial' if async_ and x in ['GAS_EXHAUSTED','TIMEOUT','SOFTWARE_STOP'] else 'none') for x in errors],verification=[],limitations=['有限传热热峰是显式工程模型，真实源只锚定熔融温度与焓','厂家温度/焓精密度及基线指标不等于已验证模拟精度','默认FACS不能低于环境；外部制冷动态与几何没有原厂校准','专利T4P、原生MDSC反转分离及Tg识别未实现'],source_ids=['manufacturer_specs','simulation_assumption'])
  if k in ['acquisition.start','acquisition.stop','emergency.stop','power.set','thermal.set_temperature','thermal.disable','lid.set','sampler.move','gas.set_open']:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='宿主推进热量、独立观测、有限库存和关节目标') for x in PHYSICS_EFFECTS]
 mechanism=dict(component_ids=['controller','furnace','sample_pan','reference_pan','purge_inlet'],state_ids=list(slots),meaning_zh='保能三节点差示热量与真实物性锚定相变',implementation='lumped',equations=[
 'Ef=Cf*(Tf-Tamb); Es=(Csample_pan+m*Cp)*(Ts-Tamb)+m*Lf*f(Ts); Er=Cref*(Tr-Tamb). f=clip((Ts-(Tm-width/2))/width,0,1). Tm/Lf from explicit measured source; Cp/pan/G/width engineering inputs. Reversible equilibrium mushy interval is not nucleation, supercooling or Tg kinetics.',
 'Cf*dTf/dt=Pheat-Pcool-Gambient*(Tf-Tamb)-Gs*(Tf-Ts)-Gr*(Tf-Tr); dEs/dt=Gs*(Tf-Ts); Cr*dTr/dt=Gr*(Tf-Tr). All three are solved together by monotone implicit enthalpy inversion and a scalar furnace residual each<=0.01s. Equal/opposite pan fluxes conserve energy for arbitrarily stiff accepted capacities/conductances.',
 'Pcommand=Kcontroller*(Tprogram-Tf), bounded by explicit heating/cooling powers. FACS Pcool<=Gfacs*max(Tf-Tamb,0), so it cannot extract heat below ambient. External RCS/LN2P use explicit engineering power but distinct manufacturer temperature domains; no OEM full-range cooling-rate curve or external hardware geometry is claimed.',
 'Program targets use actual elapsed host time, piecewise linear ramps then holds, with optional A*sin(2*pi*t/period). Combined |ramp|+A*omega must respect explicit engineering program-rate limit. Actual furnace never becomes target instantaneously.',
 'Independent sample/reference heatflow gain/offset and first-order observations give calibrated qobs_s-qobs_r, endothermic positive. No direct latent-fraction or true enthalpy is returned to agents; no random noise or certified accuracy is fabricated.',
 'analysis.integrate uses externally supplied observed times/heatflows/temperatures/baseline/measured mass: DeltaH_obs=trapz(qobs-baseline,time)/mass. Peak temperature is the sampled heatflow peak, not an extrapolated onset or certified melting temperature. Partial windows remain partial.',
 'analysis.modulated_cp uses equally spaced observed complete integer-cycle windows: independently regress constant+linear+cos+sin, C_app=Qcomplex/(i*omega*Tcomplex)/mass, where phasors use Re(Z*exp(i*omega*t)). Loss convention C_app=Cprime-i*Cdoubleprime. Pan, transfer, sensor phase and transient contamination remain; this is not T4P or calibrated intrinsic Cp.',
 'N2 inventory consumption dn=p_ref*q_ref/(R*T_ref)*dt; exact finite exhaustion event closes valve and fails active acquisition. Isothermal purge reference inventory does not model purge sensible heat exchange, oxygen reaction or buoyancy.',
 'Sample melting conserves mass. Loading adds external material enthalpy to existing pan energy and equilibrates that lump without inventing heat. Removing sample exports its current sensible+latent enthalpy; finite pan energy remains. Heater input-cooler removal-ambient loss equals change of all stored energies plus recovered/loaded material energy.',
 'Power off/cancel stops active control but retained energy and phase keep evolving under passive coupling. Frozen terminal rows do not change when later cooling, sample changes, calibration, or a new run occurs.',
 'XYZ sampler movement and diagonal lid retreat are engineering geometric motions only. Clearance is enforced before XY traversal; Z descent only at sample/reference cell or54 registered engineering tray positions within1e-7m tolerance; fixed pan geometry does not perform native 54-position pickup/placement or reparenting.'
 ],parameters={},source_ids=['manufacturer_specs','simulation_assumption'],limitations=['Cp, transfer constants, phase interval and sensor parameters are engineering inputs, not manufacturer calibration.','Mass loss, reactions, pan pressure, Tg relaxation and kinetic crystallization are not modeled.','Thermal equation is a DSC engineering proxy, not patented T4P sensor compensation.','Ambient loss is signed; below-ambient external cooling allows ambient heat ingress and records it.','External cooler geometry, LN2 consumption and full refrigeration thermodynamics are not modeled.','Exact joint kinematics, USD readout and multi-env Matterix checks are separate parent-owned validations.','Model resolution/convergence are tested but no manufacturer precision, noise floor or accuracy is certified.'])
 spec=importlib.util.spec_from_file_location('_dsc_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()});ann.annotate_export(SLUG,caps,slots,{'dsc_enthalpy':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'gas.load':(GAS,GAS_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sensor.calibrate':(CALIBRATION,CALIBRATION_EXAMPLE)})
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'dsc_enthalpy':mechanism})

"""CHEM-120 finite force tensiometry with literature surface tension references."""
from pathlib import Path
import copy,math,json,importlib.util
PROTOCOL_VERSION='1.0.0';SLUG='biolin_sigma700';CATALOG_ID='CHEM-120';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch03/t4_measure'/SLUG
class ActionRejected(ValueError):pass
def number(unit='1',lo=None,hi=None):
 d={'type':'number','x-unit':unit}
 if lo is not None:d['minimum']=lo
 if hi is not None:d['maximum']=hi
 return d
def obj(p):return dict(type='object',properties=p,required=list(p),additionalProperties=False)
def arr(v,n=None):
 d=dict(type='array',items=v)
 if n is not None:d.update(minItems=n,maxItems=n)
 return d
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
def enum(v):return {'type':'string','enum':v}
TEXT={'type':'string'};BOOL={'type':'boolean'};PHASE=enum(['idle','running','succeeded','cancelled','failed'])
def validate(s,v):
 if 'anyOf' in s:
  for q in s['anyOf']:
   try:validate(q,v);return
   except ActionRejected:pass
  raise ActionRejected('INVALID_ARGUMENT')
 t=s.get('type')
 if t in ('number','integer'):
  if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or (t=='integer' and type(v) is not int) or not s.get('minimum',-math.inf)<=v<=s.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='boolean' and type(v) is not bool:raise ActionRejected('INVALID_ARGUMENT')
 elif t=='string' and not isinstance(v,str):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='null' and v is not None:raise ActionRejected('INVALID_ARGUMENT')
 elif t=='object':
  if not isinstance(v,dict) or not set(s.get('required',[]))<=v.keys() or set(v)-set(s['properties']):raise ActionRejected('INVALID_ARGUMENT')
  for k,x in v.items():validate(s['properties'][k],x)
 elif t=='array':
  if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
  for x in v:validate(s['items'],x)
 if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')
import numpy as np
from scipy.optimize import minimize_scalar
DT=.01;G=9.80665;STAGE_MAX_M=.109;INT=lambda lo=0,hi=1000000:dict(type='integer',minimum=lo,maximum=hi,**{'x-unit':'1'})
LIB=json.loads((ASSET/'surface_library.json').read_text())
def tension(sid,temp,c,accept_unknown=False):
 if sid not in LIB['substances']:raise ActionRejected('INVALID_REFERENCE')
 s=LIB['substances'][sid];v=s['values']
 if s['kind']=='pure':
  if not v['temperature_min_k']<=temp<=v['temperature_max_k']:raise ActionRejected('TEMPERATURE_RANGE')
  return (v['a_mn_m']-v['b_mn_m_k']*(temp-273.15))*.001
 t=s['conditions']['temperature_k']
 if t is None and not accept_unknown:raise ActionRejected('UNKNOWN_CONDITIONS')
 if t is not None and abs(t-temp)>.05:raise ActionRejected('TEMPERATURE_RANGE')
 xs=v['concentration_kg_m3'];ys=v['tension_n_m']
 if c<xs[0]-1e-12 or c>xs[-1]+1e-12:raise ActionRejected('CONCENTRATION_RANGE')
 if c<=xs[1] and xs[0]==0:return float(np.interp(c,xs[:2],ys[:2]))
 return float(np.interp(math.log(max(c,1e-30)),np.log(np.maximum(xs,1e-30)),ys))
def fit_cmc(cs,ys):
 if len(cs)!=len(ys) or len(cs)<8 or any(c<=0 for c in cs) or any(a>=b for a,b in zip(cs,cs[1:])):raise ActionRejected('INVALID_ARGUMENT')
 x=np.log(cs);y=np.array(ys)
 def solve(b):
  mat=np.array([np.ones(len(x)),np.minimum(x-b,0),np.maximum(x-b,0)]).T;v=np.linalg.lstsq(mat,y,rcond=None)[0];return float(np.sum((mat@v-y)**2)),v
 candidates=[]
 for i in range(2,len(x)-3):
  r=minimize_scalar(lambda b:solve(b)[0],bounds=(x[i],x[i+1]),method='bounded');err,v=solve(r.x)
  if v[1]<-1e-5 and abs(v[2])<.35*abs(v[1]):candidates.append((err,r.x,v))
 if not candidates:raise ActionRejected('NO_BREAKPOINT')
 err,b,v=min(candidates,key=lambda z:z[0]);return dict(cmc_kg_m3=math.exp(b),tension_at_cmc_n_m=float(v[0]),slope_before_n_m=float(v[1]),slope_after_n_m=float(v[2]),residual_sum_n2_m2=err,method='Continuous two-slope least squares versus ln(mass concentration), >=3 points per branch; plateau criterion; no chemical identity inferred')
PLANT_EXAMPLE=dict(accept_engineering=True,ambient_k=293.15,liquid_density_kg_m3=791.01243,liquid_cp_j_kg_k=2504.5896,upper_density_kg_m3=0.,heat_loss_w_k=.05,bath_conductance_w_k=.4,bath_limit_w=10.,sensor_tau_s=.08,force_gain=1.,force_offset_n=0.,mix_tau_s=1.,true_ring_factor=.85,film_height_m=.003,contact_adv_rad=0.,contact_rec_rad=0.,probe_mass_kg=.002)
PUNITS={'ambient_k':'K','liquid_density_kg_m3':'kg/m^3','liquid_cp_j_kg_k':'J/kg/K','upper_density_kg_m3':'kg/m^3','heat_loss_w_k':'W/K','bath_conductance_w_k':'W/K','bath_limit_w':'W','sensor_tau_s':'s','force_offset_n':'N','mix_tau_s':'s','film_height_m':'m','contact_adv_rad':'rad','contact_rec_rad':'rad','probe_mass_kg':'kg'}
PLANT=obj({k:BOOL if isinstance(v,bool) else number(PUNITS.get(k,'1'),-10 if k=='force_offset_n' else 0,10000) for k,v in PLANT_EXAMPLE.items()})
METHOD_EXAMPLE=dict(speed_m_s=.008,immersion_m=.002,hold_s=3.,sample_interval_s=.1,plate_width_m=.0195,plate_thickness_m=.00012,ring_radius_m=.00955,analysis_contact_rad=0.,analysis_density_difference_kg_m3=791.01243,ring_correction=.85,accept_ring_proxy=False)
METHOD=obj({'speed_m_s':number('m/s',.01e-3/60,.5/60),'immersion_m':number('m',.0001,.010),'hold_s':number('s',.2,600),'sample_interval_s':number('s',.01,5),'plate_width_m':number('m',.001,.05),'plate_thickness_m':number('m',1e-5,.003),'ring_radius_m':number('m',.002,.025),'analysis_contact_rad':number('rad',0,math.pi),'analysis_density_difference_kg_m3':number('kg/m^3',0,3000),'ring_correction':number('1',.1,2),'accept_ring_proxy':BOOL})
SAMPLE_EXAMPLE=dict(substance_id='cas_67_56_1',volume_m3=3e-5,concentration_kg_m3=0.,temperature_k=293.15,accept_unknown_conditions=False)
SAMPLE=obj({'substance_id':TEXT,'volume_m3':number('m^3',1e-6,5e-5),'concentration_kg_m3':number('kg/m^3',0,100),'temperature_k':number('K',253.15,473.15),'accept_unknown_conditions':BOOL})
ROW=obj({'time_s':number('s'),'stage_m':number('m'),'depth_m':number('m'),'force_n':number('N'),'tension_n_m':nullable(number('N/m')),'temperature_k':number('K'),'branch':TEXT})
RESULT=obj({'phase':PHASE,'stage':TEXT,'operation_id':nullable(TEXT),'elapsed_s':number('s'),'rows':arr(ROW),'tension_n_m':nullable(number('N/m')),'template_source':nullable(TEXT),'evidence':nullable(TEXT),'error_code':nullable(TEXT)})
LEDGER=obj({k:number(u) for k,u in [('liquid_loaded_kg','kg'),('stock_added_kg','kg'),('stock_present_kg','kg'),('liquid_present_kg','kg'),('waste_kg','kg'),('mass_error_kg','kg'),('solute_added_kg','kg'),('solute_present_kg','kg'),('stock_solute_kg','kg'),('solute_waste_kg','kg'),('solute_error_kg','kg'),('heat_input_j','J'),('heat_out_j','J'),('heat_stored_j','J'),('heat_error_j','J')]})
CAPS={};SLOTS={};SLOT_ZH={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},effects or [])
def slot(k,s,u,z,o='derived',v='agent',c='controller',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
cap('plant.configure',PLANT,obj({'configured':BOOL}),['balance','controller'],'宿主显式配置有效热学、测力响应和未知物性','configuration','evaluator',PLANT_EXAMPLE,['plant.configured'])
for k,arg,schema,c,z,effect in [('power.set','enabled',BOOL,'power_switch','切换供电','power.enabled'),('door.set','angle_rad',number('rad',0,2.35),'door','手动旋转防风罩','door.angle_rad'),('service.set','distance_m',number('m',0,.10),'service_cover','断电拆开后部维护板','service.slide_m'),('probe.select','probe',enum(['plate','ring']),'balance_hook','人工更换环或板；失去原去皮基准','probe.kind'),('thermal.set_temperature','temperature_k',number('K',253.15,473.15),'bath_in','外接温控浴目标，不代表13W本体带加热器','thermal.target_k'),('stirrer.set_speed','speed_rad_s',number('rad/s',0,200),'stirrer_rotor','显式工程搅拌角速度','stirrer.speed_rad_s')]:
 ex={arg:False if schema.get('type')=='boolean' else 'plate' if arg=='probe' else 293.15 if arg=='temperature_k' else 0.};cap(k,obj({arg:schema}),obj({arg:schema}),[c],z,ex=ex,effects=[effect])
cap('method.configure',METHOD,METHOD,['balance','stage'],'设置速度、浸深、观测和外部分析参数','configuration',ex=METHOD_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_vessel'],'宿主加载有限液体及公开响应来源','configuration','evaluator',SAMPLE_EXAMPLE,['sample.present','truth.liquid_volume_m3'])
cap('sample.remove',obj({}),obj({'removed':BOOL}),['sample_vessel'],'宿主取出样品并记入外部收集账本','configuration','evaluator',effects=['sample.present','truth.liquid_volume_m3'])
STOCK=obj({'volume_m3':number('m^3',0,2e-5),'concentration_kg_m3':number('kg/m^3',0,100)})
cap('stock.load',STOCK,obj({'loaded':BOOL}),['dispenser_port'],'外接20mL计量滴定器库存初始化','configuration','evaluator',dict(volume_m3=2e-5,concentration_kg_m3=1.),['truth.stock_volume_m3'])
cap('balance.tare',obj({}),obj({'tared':BOOL}),['balance'],'空中探针去皮，不扣除后续浮力',effects=['balance.tared'])
CAL=obj({'reference_forces_n':arr(number('N',-2,2),2),'observed_raw_forces_n':arr(number('N',-3,3),2)})
cap('balance.calibrate',CAL,obj({'gain':number('1'),'offset_n':number('N')}),['balance','calibration_weight'],'按外部独立参比作两点仿射校准，非原厂内校序列','configuration',ex=dict(reference_forces_n=[0.,.1],observed_raw_forces_n=[0.,.1]))
for k,inp,ex,z in [('stage.move',obj({'position_m':number('m',0,STAGE_MAX_M)}),{'position_m':0.},'按当前速度异步升降载台'),('acquisition.start',obj({}),{},'板浸入保持撤回或环拉脱峰值测量'),('dose.start',obj({'volume_m3':number('m^3',1e-9,2e-5),'flow_m3_s':number('m^3/s',1e-10,1e-6)}),{'volume_m3':1e-6,'flow_m3_s':1e-7},'有限外接滴定液异步加入')]:cap(k,inp,obj({'operation_id':TEXT}),['stage' if k!='dose.start' else 'dispenser_port','balance'],z,ex=ex,effects=['acquisition.phase'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['controller'],'取消保留载台位置、已加液、热量与曲线',effects=['acquisition.phase'])
cap('acquisition.result',obj({}),RESULT,['balance'],'只读测力曲线、拟合值和来源','query')
CMC=obj({'concentrations_kg_m3':arr(number('kg/m^3',1e-12,100)),'tensions_n_m':arr(number('N/m',.001,2))})
CMCO=obj({'cmc_kg_m3':number('kg/m^3'),'tension_at_cmc_n_m':number('N/m'),'slope_before_n_m':number('N/m'),'slope_after_n_m':number('N/m'),'residual_sum_n2_m2':number('N^2/m^2'),'method':TEXT})
cap('analysis.cmc',CMC,CMCO,['controller'],'根据外部观测浓度序列作分段回归，不调用样品真值','analysis',ex={'concentrations_kg_m3':[.001,.002,.004,.008,.016,.032,.064,.128],'tensions_n_m':[.07,.065,.06,.055,.05,.05,.05,.05]})
cap('ledger.read',obj({}),LEDGER,['sample_vessel','dispenser_port'],'宿主守恒审计','query','evaluator')
cap('sim.step',obj({'dt_s':number('s',0,3600)}),obj({'time_s':number('s')}),['controller'],'唯一宿主时间推进','simulation','evaluator',dict(dt_s=.1))
UNSUPPORTED=['calibration.oem_internal','ring.huh_mason','cmc.automatic_dispenser','powder.washburn','density.measure','sedimentation.measure','surface_energy.measure','hardware.connect','robot.exchange','fault.empirical','noise.empirical']
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'未取得必要附件、算法或验证：'+k)
for k,u,z,c,hi,w in [('door.angle_rad','rad','透明罩角度','door',2.35,('door.set','angle_rad')),('service.slide_m','m','维护板后移','service_cover',.10,('service.set','distance_m')),('stage.position_m','m','实际载台位移，按速度推进','stage',STAGE_MAX_M,None),('stage.target_m','m','目标位移，非实际位置','stage',STAGE_MAX_M,('stage.move','position_m')),('plate.park_m','m','板人工移至存放位的显示代理','probe_plate',.065,None),('ring.attach_m','m','环人工移至挂钩的显示代理','probe_ring',.065,None),('power.angle_rad','rad','电源摇臂角度','power_switch',.32,None),('stirrer.angle_rad','rad','磁搅拌转子角度','stirrer_rotor',2*math.pi,None),('stirrer.speed_rad_s','rad/s','搅拌目标角速度，非实测转速','stirrer_rotor',200,('stirrer.set_speed','speed_rad_s'))]:slot(k,number(u,0,hi),u,z,'setpoint' if w else 'scene',c=c,w=w)
for k,z,c in [('plant.configured','工程参数已显式给定','controller'),('power.enabled','通电状态','power_switch'),('lamp.power','电源状态灯代理','lamp_power'),('balance.tared','空中去皮已记录','balance'),('sample.present','样品存在，非物种标签','sample_vessel')]:slot(k,BOOL,'1',z,'derived',c=c)
slot('probe.kind',enum(['plate','ring']),'1','已挂接测量工具','setpoint',c='balance_hook',w=('probe.select','probe'))
slot('thermal.target_k',number('K',253.15,473.15),'K','外接浴温度目标','setpoint',c='bath_in',w=('thermal.set_temperature','temperature_k'))
slot('thermal.indicated_k',nullable(number('K')),'K','理想0.01K量化温度指示；无实测噪声','derived',c='temperature_probe')
slot('force.indicated_n',nullable(number('N')),'N','0.1µN量化并校准/去皮后的力；无经验噪声','derived',c='balance')
slot('tension.indicated_n_m',nullable(number('N/m')),'N/m','由观测力及方法参数计算并按1µN/m量化的张力','derived',c='balance')
slot('clock.time_s',number('s'),'s','宿主仿真时间')
for k,u,z in [('truth.temperature_k','K','过程液温'),('truth.liquid_volume_m3','m^3','有限液体体积'),('truth.solute_kg','kg','当前溶质质量'),('truth.stock_volume_m3','m^3','有限滴定液库存'),('truth.surface_tension_n_m','N/m','有效表面张力过程真值'),('truth.force_n','N','悬挂探针受力真值')]:slot(k,nullable(number(u)),u,z,'process_truth','evaluator')
slot('acquisition.phase',PHASE,'1','异步操作状态');slot('acquisition.stage',TEXT,'1','approach/hold/withdraw/move/dose阶段');slot('acquisition.elapsed_s',number('s'),'s','当前操作累计推进时间');slot('acquisition.points',INT(),'1','真实采样记录数')
# The 0.109 m travel is a local collision-limited scene envelope, not an OEM stroke.
# Observe-derived readings can change with commands even though physics advances only in step.
COMMON_DYNAMICS=['stirrer.angle_rad','thermal.indicated_k','force.indicated_n','tension.indicated_n_m','truth.temperature_k','truth.surface_tension_n_m','truth.force_n']
PHYSICS_EFFECTS=['clock.time_s','stage.position_m','stage.target_m','truth.liquid_volume_m3','truth.solute_kg','truth.stock_volume_m3','acquisition.phase','acquisition.stage','acquisition.elapsed_s','acquisition.points']+COMMON_DYNAMICS
IMMEDIATE_EFFECTS={
 'plant.configure':['plant.configured','truth.temperature_k','thermal.target_k'],
 'power.set':['power.enabled','power.angle_rad','lamp.power','thermal.indicated_k','force.indicated_n','tension.indicated_n_m'],
 'probe.select':['probe.kind','plate.park_m','ring.attach_m','balance.tared'],
 'sample.load':['sample.present','truth.liquid_volume_m3','truth.solute_kg','truth.temperature_k','truth.surface_tension_n_m','thermal.indicated_k'],
 'sample.remove':['sample.present','truth.liquid_volume_m3','truth.solute_kg','truth.surface_tension_n_m','thermal.indicated_k'],
 'balance.tare':['balance.tared','force.indicated_n'],
 'balance.calibrate':['balance.tared'],
 'stage.move':['stage.target_m','acquisition.phase','acquisition.stage','acquisition.elapsed_s','acquisition.points'],
 'acquisition.start':['stage.target_m','acquisition.phase','acquisition.stage','acquisition.elapsed_s','acquisition.points'],
 'dose.start':['acquisition.phase','acquisition.stage','acquisition.elapsed_s','acquisition.points'],
 'acquisition.stop':['stage.target_m','acquisition.phase'],
 'sim.step':PHYSICS_EFFECTS,
}
STEP_EFFECTS={
 'stage.move':COMMON_DYNAMICS+['stage.position_m','stage.target_m','acquisition.elapsed_s','acquisition.phase','acquisition.points'],
 'acquisition.start':COMMON_DYNAMICS+['stage.position_m','stage.target_m','acquisition.elapsed_s','acquisition.phase','acquisition.stage','acquisition.points'],
 'dose.start':COMMON_DYNAMICS+['truth.liquid_volume_m3','truth.stock_volume_m3','truth.solute_kg','acquisition.elapsed_s','acquisition.phase'],
 'thermal.set_temperature':['truth.temperature_k','thermal.indicated_k','truth.surface_tension_n_m','truth.force_n','force.indicated_n','tension.indicated_n_m'],
 'stirrer.set_speed':['stirrer.angle_rad'],
 'power.set':COMMON_DYNAMICS,
 'balance.tare':['force.indicated_n','tension.indicated_n_m'],
 'balance.calibrate':['force.indicated_n','tension.indicated_n_m'],
}

class BiolinSigma700Runtime:
 def __init__(self,instance_id='sigma700_01'):
  self.instance_id=instance_id;self.plant=None;self.method=copy.deepcopy(METHOD_EXAMPLE);self.power=False;self.door=0.;self.service=0.;self.probe='plate';self.q=0.;self.target=0.;self.omega=0.;self.rotor=0.;self.temperature=293.15;self.thermal_target=293.15;self.sid=None;self.accept_unknown=False;self.volume=0.;self.solute=0.;self.stock=0.;self.stock_c=0.;self.loaded=0.;self.stock_added=0.;self.waste=0.;self.solute_added=0.;self.solute_waste=0.;self.heat_in=0.;self.heat_out=0.;self.gamma=None;self.trueforce=0.;self.raw=0.;self.gain=1.;self.offset=0.;self.tare=0.;self.tared=False;self.force=None;self.indicated=None;self.time_s=0.;self._ticks=0;self._remainder=0.;self.revision=0;self.phase='idle';self.stage='idle';self.elapsed=0.;self.rows=[];self.hold=0.;self.dose_remaining=0.;self.dose_flow=0.;self.peak=None;self.result_source=None;self.result_evidence=None;self.error=None;self.operation_id=None;self._counter=0;self._requests={};self._owners={};self.records={};self._principals={'agent':'agent','evaluator':'evaluator'}
 def register_principal(self,principal_id,visibility='agent'):
  if not isinstance(principal_id,str) or not principal_id or principal_id in self._principals or visibility not in ['agent','debug','evaluator']:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='agent'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return BiolinSigma700Session(self,principal_id)
 def describe(self):return export_biolin_sigma700_contract_metadata()
 def read(self,role='evaluator'):
  d={'plant.configured':self.plant is not None,'power.enabled':self.power,'power.angle_rad':.32 if self.power else 0.,'lamp.power':self.power,'door.angle_rad':self.door,'service.slide_m':self.service,'stage.position_m':self.q,'stage.target_m':self.target,'plate.park_m':.065 if self.probe=='ring' else 0.,'ring.attach_m':.065 if self.probe=='ring' else 0.,'stirrer.angle_rad':self.rotor,'stirrer.speed_rad_s':self.omega,'probe.kind':self.probe,'balance.tared':self.tared,'sample.present':self.volume>0,'thermal.target_k':self.thermal_target,'thermal.indicated_k':round(self.temperature,2) if self.power and self.volume else None,'force.indicated_n':self.force if self.power else None,'tension.indicated_n_m':self.indicated if self.power else None,'clock.time_s':self.time_s,'truth.temperature_k':self.temperature,'truth.liquid_volume_m3':self.volume,'truth.solute_kg':self.solute,'truth.stock_volume_m3':self.stock,'truth.surface_tension_n_m':self.gamma,'truth.force_n':self.trueforce,'acquisition.phase':self.phase,'acquisition.stage':self.stage,'acquisition.elapsed_s':self.elapsed,'acquisition.points':len(self.rows)}
  return {k:copy.deepcopy(v) for k,v in d.items() if role!='agent' or SLOTS[k][3]=='agent'}
 def _result(self):return dict(phase=self.phase,stage=self.stage,operation_id=self.operation_id,elapsed_s=self.elapsed,rows=copy.deepcopy(self.rows),tension_n_m=self.peak,template_source=self.result_source,evidence=self.result_evidence,error_code=self.error)
 def _ledger(self):
  p=self.plant or PLANT_EXAMPLE;rho=p['liquid_density_kg_m3'];stored=self.volume*rho*p['liquid_cp_j_kg_k']*(self.temperature-p['ambient_k']);return dict(liquid_loaded_kg=self.loaded,stock_added_kg=self.stock_added,stock_present_kg=self.stock*rho,liquid_present_kg=self.volume*rho,waste_kg=self.waste,mass_error_kg=self.loaded+self.stock_added-self.stock*rho-self.volume*rho-self.waste,solute_added_kg=self.solute_added,solute_present_kg=self.solute,stock_solute_kg=self.stock*self.stock_c,solute_waste_kg=self.solute_waste,solute_error_kg=self.solute_added-self.solute-self.stock*self.stock_c-self.solute_waste,heat_input_j=self.heat_in,heat_out_j=self.heat_out,heat_stored_j=stored,heat_error_j=self.heat_in-self.heat_out-stored)
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _access(self):
  if self.door<1.5 or self.q>1e-6:raise ActionRejected('SAMPLE_ACCESS')
 def _ready(self):
  if not self.plant or not self.power or self.service>1e-6 or self.door>.01:raise ActionRejected('NOT_READY')
 def _depth(self):
  if not self.volume:return -.2
  m=self.method;area=m['plate_width_m']*m['plate_thickness_m'] if self.probe=='plate' else 0.;raw=.2945+self.volume/(math.pi*.025**2)+self.q-.404
  return raw/(1-area/(math.pi*.025**2)) if raw>0 else raw
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[k][0],a)
  if k=='sim.step':self.step(a['dt_s']);return dict(time_s=self.time_s)
  before=copy.deepcopy(self.__dict__)
  try:
   r=self._invoke(k,a);validate(CAPS[k][1],r)
   if CAPS[k][4] not in ['query','analysis']:self.revision+=1
   return r
  except Exception:self.__dict__.clear();self.__dict__.update(before);raise
 def _invoke(self,k,a):
  if k=='acquisition.result':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='analysis.cmc':return fit_cmc(a['concentrations_kg_m3'],a['tensions_n_m'])
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self.target=self.q;self._finish('cancelled');return {'phase':self.phase}
  self._idle()
  if k=='plant.configure':
   if self.plant is not None or self.power:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   if min(a[x] for x in ['liquid_density_kg_m3','liquid_cp_j_kg_k','sensor_tau_s','force_gain','mix_tau_s','true_ring_factor','film_height_m'])<=0 or a['liquid_density_kg_m3']<=a['upper_density_kg_m3'] or max(a['contact_adv_rad'],a['contact_rec_rad'])>math.pi or a['probe_mass_kg']>.21 or not 253.15<=a['ambient_k']<=473.15:raise ActionRejected('INVALID_ARGUMENT')
   self.plant=copy.deepcopy(a);self.temperature=self.thermal_target=a['ambient_k'];return {'configured':True}
  if k=='power.set':
   if a['enabled'] and (not self.plant or self.service>1e-6):raise ActionRejected('NOT_READY')
   self.power=a['enabled'];return a
  if k=='door.set':self.door=a['angle_rad'];return a
  if k=='service.set':
   if self.power:raise ActionRejected('POWER_INTERLOCK')
   self.service=a['distance_m'];return a
  if k=='probe.select':self._access();self.probe=a['probe'];self.tared=False;return a
  if k=='method.configure':self.method=copy.deepcopy(a);return a
  if k=='thermal.set_temperature':self.thermal_target=a['temperature_k'];return a
  if k=='stirrer.set_speed':self.omega=a['speed_rad_s'];return a
  if k=='sample.load':
   self._access()
   if not self.plant or self.volume:raise ActionRejected('SAMPLE_PRESENT')
   gamma=tension(a['substance_id'],a['temperature_k'],a['concentration_kg_m3'],a['accept_unknown_conditions']);self.sid=a['substance_id'];self.volume=a['volume_m3'];self.solute=self.volume*a['concentration_kg_m3'];self.temperature=a['temperature_k'];self.accept_unknown=a['accept_unknown_conditions'];self.gamma=gamma;self.loaded+=self.volume*self.plant['liquid_density_kg_m3'];self.solute_added+=self.solute;self.heat_in+=self.volume*self.plant['liquid_density_kg_m3']*self.plant['liquid_cp_j_kg_k']*(self.temperature-self.plant['ambient_k']);return {'loaded':True}
  if k=='sample.remove':
   self._access()
   if not self.plant or not self.volume:raise ActionRejected('NO_SAMPLE')
   self.waste+=self.volume*self.plant['liquid_density_kg_m3'];self.solute_waste+=self.solute;self.heat_out+=self._ledger()['heat_stored_j'];self.volume=0.;self.solute=0.;self.gamma=None;self.sid=None;return {'removed':True}
  if k=='stock.load':
   if not self.plant or self.stock:raise ActionRejected('STOCK_PRESENT')
   self.stock=a['volume_m3'];self.stock_c=a['concentration_kg_m3'];self.stock_added+=self.stock*self.plant['liquid_density_kg_m3'];self.solute_added+=self.stock*self.stock_c;return {'loaded':True}
  self._ready()
  if k=='balance.tare':
   if self._depth()>=-.0005:raise ActionRejected('PROBE_NOT_IN_AIR')
   self.tare=self.gain*self.raw+self.offset;self.tared=True;self.force=0.;return {'tared':True}
  if k=='balance.calibrate':
   x=a['observed_raw_forces_n'];y=a['reference_forces_n']
   if abs(x[1]-x[0])<1e-7 or y[1]<=y[0] or x[1]<=x[0]:raise ActionRejected('INVALID_CALIBRATION')
   self.gain=(y[1]-y[0])/(x[1]-x[0]);self.offset=y[0]-self.gain*x[0];self.tared=False;return dict(gain=self.gain,offset_n=self.offset)
  if k in ['stage.move','acquisition.start','dose.start']:
   if k!='stage.move' and not self.volume:raise ActionRejected('NO_SAMPLE')
   if k=='acquisition.start':
    if not self.tared:raise ActionRejected('NOT_TARED')
    if self.probe=='ring' and not self.method['accept_ring_proxy']:raise ActionRejected('RING_MODEL_NOT_ACCEPTED')
    if self.q>1e-6:raise ActionRejected('STAGE_NOT_HOME')
    if abs(math.cos(self.method['analysis_contact_rad']))<.01:raise ActionRejected('INVALID_ARGUMENT')
    self.target=.404-.2945-self.volume/(math.pi*.025**2)+self.method['immersion_m']*(1-self.method['plate_width_m']*self.method['plate_thickness_m']/(math.pi*.025**2) if self.probe=='plate' else 1)
    if not 0<=self.target<=STAGE_MAX_M:raise ActionRejected('STAGE_RANGE')
    self.stage='approach';self.result_source=json.dumps(LIB['substances'][self.sid]['source'],sort_keys=True);self.result_evidence='Literature equilibrium reference; derived finite-force observation; ring meniscus shape/correction explicit engineering proxy' if self.probe=='ring' else 'Literature equilibrium reference; derived Wilhelmy force with buoyancy and0.1uN quantization'
   elif k=='stage.move':
    if self.volume and self.probe=='plate' and .2945+self.volume/(math.pi*.025**2)+a['position_m']-.404>.019:raise ActionRejected('OVERIMMERSION')
    self.target=a['position_m'];self.stage='move';self.result_source=None;self.result_evidence=None
   else:
    if self.q>1e-6:raise ActionRejected('STAGE_NOT_HOME')
    if a['volume_m3']>self.stock or self.volume+a['volume_m3']>5e-5:raise ActionRejected('FINITE_STOCK_OR_CAPACITY')
    tension(self.sid,self.temperature,(self.solute+a['volume_m3']*self.stock_c)/(self.volume+a['volume_m3']),self.accept_unknown);self.dose_remaining=a['volume_m3'];self.dose_flow=a['flow_m3_s'];self.stage='dose';self.result_source=json.dumps(LIB['substances'][self.sid]['source']);self.result_evidence='Finite external stock transfer; no automatic OEM dispenser control'
   self._counter+=1;self.operation_id=self.instance_id+':'+str(self._counter);self.phase='running';self.elapsed=0.;self.rows=[];self.hold=0.;self.peak=None;self.error=None;return {'operation_id':self.operation_id}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _finish(self,phase,error=None):
  self.phase=phase;self.error=error
  if self.operation_id:self.records[self.operation_id]=self._result()
 def _tick(self):
  p=self.plant
  if not p:return
  if self.power:self.rotor=(self.rotor+self.omega*DT)%(2*math.pi)
  moving=self.phase=='running' and self.stage in ['approach','withdraw','move'];sign=1 if self.target>=self.q else -1
  if moving:self.q+=sign*min(abs(self.target-self.q),self.method['speed_m_s']*DT)
  if self.phase=='running' and self.stage=='dose':
   dv=min(self.dose_remaining,self.dose_flow*DT,self.stock);old=self.volume;self.temperature=(old*self.temperature+dv*p['ambient_k'])/(old+dv);self.volume+=dv;self.solute+=dv*self.stock_c;self.stock-=dv;self.dose_remaining-=dv
  if self.volume:
   mcp=self.volume*p['liquid_density_kg_m3']*p['liquid_cp_j_kg_k'];delta=self.thermal_target-self.temperature;kb=min(p['bath_conductance_w_k'],p['bath_limit_w']/max(abs(delta),1e-30)) if self.power else 0.;h=p['heat_loss_w_k'];total=kb+h
   if total:
    equilibrium=(kb*self.thermal_target+h*p['ambient_k'])/total;k=total/mcp;e=math.exp(-k*DT);integral=equilibrium*DT+(self.temperature-equilibrium)*(1-e)/k;self.heat_in+=kb*(self.thermal_target*DT-integral);self.heat_out+=h*(integral-p['ambient_k']*DT);self.temperature=equilibrium+(self.temperature-equilibrium)*e
   try:target_gamma=tension(self.sid,self.temperature,self.solute/self.volume,self.accept_unknown)
   except ActionRejected as error:
    target_gamma=self.gamma
    if self.phase=='running':self._finish('failed',str(error))
   self.gamma=target_gamma+(self.gamma-target_gamma)*math.exp(-DT/p['mix_tau_s'])
  depth=self._depth();m=self.method;capillary=0.;buoy=0.
  if self.volume:
   if self.probe=='plate' and depth>=0:
    theta=p['contact_rec_rad'] if moving and sign<0 else p['contact_adv_rad'];capillary=2*(m['plate_width_m']+m['plate_thickness_m'])*self.gamma*math.cos(theta);buoy=(p['liquid_density_kg_m3']-p['upper_density_kg_m3'])*G*m['plate_width_m']*m['plate_thickness_m']*min(depth,.020)
   elif self.probe=='ring' and self.stage=='withdraw' and -p['film_height_m']<=depth<=0:
    u=-depth/p['film_height_m'];capillary=4*math.pi*m['ring_radius_m']*self.gamma/p['true_ring_factor']*4*u*(1-u)
  self.trueforce=p['probe_mass_kg']*G+capillary-buoy;signal=p['force_gain']*self.trueforce+p['force_offset_n'];self.raw=signal+(self.raw-signal)*math.exp(-DT/p['sensor_tau_s']);quant=round(self.raw/1e-7)*1e-7;self.force=self.gain*quant+self.offset-self.tare
  indicated=None
  if self.probe=='plate' and depth>=0 and self.tared and self.volume:indicated=(self.force+m['analysis_density_difference_kg_m3']*G*m['plate_width_m']*m['plate_thickness_m']*min(depth,.020))/(2*(m['plate_width_m']+m['plate_thickness_m'])*math.cos(m['analysis_contact_rad']))
  if self.probe=='ring' and self.stage=='withdraw' and depth<=0 and self.tared:indicated=self.force*m['ring_correction']/(4*math.pi*m['ring_radius_m'])
  self.indicated=round(indicated/1e-6)*1e-6 if indicated is not None and .001<=indicated<=2 else None
  if self.phase!='running':return
  self.elapsed+=DT
  if abs(self.trueforce)>G*.21:self.target=self.q;self._finish('failed','BALANCE_OVERLOAD');return
  if self.stage=='dose':
   if self.dose_remaining<=1e-15:self.dose_remaining=0.;self._finish('succeeded')
   elif self.stock<=0:self._finish('failed','STOCK_EXHAUSTED')
   return
  n=max(1,round(m['sample_interval_s']/DT))
  if round(self.elapsed/DT)%n==0:
   self.rows.append(dict(time_s=self.time_s,stage_m=self.q,depth_m=depth,force_n=self.force,tension_n_m=self.indicated,temperature_k=round(self.temperature,2),branch=self.stage))
   if self.probe=='ring' and self.stage=='withdraw' and self.indicated is not None:self.peak=max(self.peak or 0.,self.indicated)
  if self.stage=='move' and abs(self.q-self.target)<1e-12:self._finish('succeeded')
  elif self.stage=='approach' and abs(self.q-self.target)<1e-12:self.stage='hold';self.hold=0.
  elif self.stage=='hold':
   self.hold+=DT
   if self.hold>=m['hold_s']-1e-12:
    if self.probe=='plate':ys=[r['tension_n_m'] for r in self.rows if r['branch']=='hold' and r['tension_n_m'] is not None and r['time_s']>self.time_s-min(m['hold_s']/2,1.)];self.peak=float(np.mean(ys)) if ys else None
    self.target=0.;self.stage='withdraw'
  elif self.stage=='withdraw' and self.q<=1e-12:self.q=0.;self._finish('succeeded' if self.peak is not None else 'failed',None if self.peak is not None else 'NO_VALID_READING')
 def step(self,dt_s):
  validate(number('s',0,3600),dt_s);total=self._remainder+dt_s;n=int((total+1e-12)/DT);self._remainder=total-n*DT
  for _ in range(n):self._ticks+=1;self.time_s=self._ticks*DT;self._tick()
  if n:self.revision+=n

class BiolinSigma700Session:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,i):
  if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  m=export_biolin_sigma700_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
 def read(self,*,instance_id,state_ids=None):
  self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
  if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
  if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
  return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='unavailable' if v[k] is None else 'estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=h.time_s,sample_window_s=None) for k in ids})

 def invoke(self,*,instance_id,capability_id,arguments,request_id):
  h=self._host
  try:
   self._target(instance_id)
   if not isinstance(capability_id,str) or capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
   if self._role=='agent' and CAPS[capability_id][5]!='agent':raise ActionRejected('ACCESS_DENIED')
   if not isinstance(request_id,str) or not request_id:raise ActionRejected('INVALID_ARGUMENT')
   validate(CAPS[capability_id][0],arguments);key=(self._principal,instance_id,request_id);digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
   if key in h._requests:
    old,result=h._requests[key]
    if old!=digest:raise ActionRejected('REQUEST_CONFLICT')
    return copy.deepcopy(result)
   if capability_id=='acquisition.stop' and self._role=='agent' and h._owners.get(h.operation_id,self._principal)!=self._principal:raise ActionRejected('ACCESS_DENIED')
   result=h.invoke(capability_id,**arguments);op=result.get('operation_id') if capability_id in ['acquisition.start','stage.move','dose.start'] else None
   if op:h._owners[op]=self._principal
   response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=op,status='accepted' if op else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None);h._requests[key]=(digest,copy.deepcopy(response));return response
  except ActionRejected as exc:return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,status='unsupported' if str(exc)=='UNSUPPORTED' else 'rejected',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))
 def write(self,*,instance_id,state_id,value,request_id):
  self._target(instance_id)
  if state_id not in SLOTS or not SLOTS[state_id][5]:raise ActionRejected('ACCESS_DENIED')
  cap,arg=SLOTS[state_id][5];return self.invoke(instance_id=instance_id,capability_id=cap,arguments={arg:value},request_id=request_id)
 def poll(self,operation_id):
  h=self._host
  if h._owners.get(operation_id)!=self._principal:raise ActionRejected('ACCESS_DENIED')
  record=h.records.get(operation_id)
  if operation_id==h.operation_id and h.phase=='running':record=h._result()
  if record is None:raise ActionRejected('UNKNOWN_OPERATION')
  return dict(operation_id=operation_id,status=record['phase'],result=copy.deepcopy(record))
 def cancel(self,operation_id,request_id):
  self.poll(operation_id)
  if operation_id!=self._host.operation_id:raise ActionRejected('NOT_RUNNING')
  return self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)





def export_biolin_sigma700_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions='SI; stage relative to fixed home, force downward positive, bath absolute temperature; concentration is mass per liquid volume',origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'BiolinSigma700Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description='0.01s physical ticks; readings use0.1µN quantization; tension mean from latehold observations'),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNKNOWN_OPERATION','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','INITIALIZATION_ONLY','BUSY','NOT_READY','NOT_RUNNING','MODEL_NOT_ACCEPTED','SAMPLE_ACCESS','POWER_INTERLOCK','SAMPLE_PRESENT','NO_SAMPLE','STOCK_PRESENT','PROBE_NOT_IN_AIR','INVALID_CALIBRATION','NOT_TARED','RING_MODEL_NOT_ACCEPTED','STAGE_NOT_HOME','STAGE_RANGE','FINITE_STOCK_OR_CAPACITY','INVALID_REFERENCE','TEMPERATURE_RANGE','CONCENTRATION_RANGE','UNKNOWN_CONDITIONS','NO_BREAKPOINT','OVERIMMERSION','STOCK_EXHAUSTED']
 async_ids=['acquisition.start','stage.move','dose.start']
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  un=k in UNSUPPORTED;asyn=k in async_ids;caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if un else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if un else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if un else 'implemented'),binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.biolin_sigma700.BiolinSigma700Session.invoke',invocation=f'session.invoke(instance_id="sigma700_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if kind in ['query','analysis'] or un else [dict(description_zh='原子化参数、访问、防风罩/样品/去皮/载台/供电及库存检查；仿真操作互锁，非原厂硬件认证',state_ids=['power.enabled','plant.configured','acquisition.phase','door.angle_rad','stage.position_m'],enforced_by='runtime',check='atomic invoke;registered Session;host step only')],state_effects=[dict(state_id=x,timing='immediate',description_zh='直接命令状态') for x in IMMEDIATE_EFFECTS.get(k,effects)],observes=[],model_effects=dict(mode='none',description_zh='联动须由实际姿态与读数另行验证',binding_ids=[]),execution=dict(mode='asynchronous' if asyn else 'synchronous',time_domain='simulation',completion_criterion='到达位置、测量撤回完成、有限剂量已加完' if asyn else '命令已接受；物理时间仅宿主step推进',completion_state_ids=['acquisition.phase'] if asyn else [],timeout_s=None,cancellation='supported' if asyn else 'unsupported',retry='same_request_id',resources=['host_clock','balance','stage','liquid','stock'],partial_failure_zh='已移动位置、已加库存、热量和采样保留，不回滚真实部分效果'),errors=[dict(code=x,meaning_zh=x,side_effects='partial' if asyn and x=='STOCK_EXHAUSTED' else 'none') for x in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=['硬件误差、污染、蒸发、故障和噪声统计未取得','环法有限薄膜峰形及校正系数是明确工程代理；不声称Huh-Mason'],source_ids=['manufacturer_specs','simulation_assumption'])
  if k in STEP_EFFECTS:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='宿主时钟推进有限库存、载台、热量、搅拌或受力观测') for x in STEP_EFFECTS[k]]
 mech=dict(component_ids=['balance','stage','probe_plate','probe_ring','sample_vessel','dispenser_port','temperature_probe','stirrer_rotor'],state_ids=list(slots),meaning_zh='环板准静态受力与外部滴定、温控下的有限物料响应',implementation='lumped',equations=['sigma(T)=(a-b*(T-273.15))*1e-3 from413 empirical Jasper/Lange fits, no extrapolation','Curves use source concentration/kg m^-3 and equilibrium tension/N m^-1, log-concentration interpolation above first point; unknown temperatures/formulations explicitly accepted','Wilhelmy F=mg+2(w+t)*sigma*cos(theta)-delta_rho*g*w*t*immersed_depth; liquid-level displacement solved for plate; dynamic angleadv/recede explicit host inputs','Ring withdrawal F=mg+(4*pi*R*sigma/f_true)*4u(1-u),u=filmheight/h,0<=u<=1; bounded finite meniscus proxy, not full YoungLaplace or HuhMason','Observed force:gain_true*F+offset_true -> exponential filter ->0.1uN quantization ->independent affinecalibration and air tare; displayed sigma quantized1e-6N/m','Stage |dq/dt|<=commanded0.01–500mm/min; advance/hold/withdraw only host step; specimen remains finite and is not consumed by ideal repeated dipping','Dose dV=min(Qdt,stock,dose_remaining); solution concentration=solute_mass/volume; stock atambient mixesenthalpy with existing sample; same effective density/Cp assumption','mCp*dT=Kb*(Tbath-T)-H*(T-Tambient); exact piecewise effectiveKb exponential integration, external bath powerlimited.13W body consumption is not heater power','Surface tension relaxes to measured equilibrium interpolation with explicitly assumed mixing time; no adsorption mass/depletion kinetic claim','CMC continuous two-slope least squares on ln(c_mass),>=3points each side and post-slope<35%preslope; no plateau ->NO_BREAKPOINT; no hidden truth lookup','Registered sessions filter truth/configuration; pure reads and result queries cannot advance time or claim operation ownership'],parameters={},source_ids=['manufacturer_specs','manufacturer_brochure','manufacturer_faq','jasper_data','apg_data','interface_data','simulation_assumption'],limitations=['413named pure-liquid evaluated coefficients,5unassigned APG formulations and2SDS/SiO2 phasecurves; no50nativeSigma force-trace/error datasets','Pure liquid data original printed NIST PDF returned503; machine transcription provenance/hash preserved; independent allrow printcrosscheck not_run','All local CAD, probe dimensions/weights, density/Cp for arbitraryliquids, timeconstants/truecontacts/ringfactor are explicit engineering assumptions','Ring waveform/correction is proxy; HuhMason/ZuidemaWaters/internal automatic calibration not implemented','No full two-layer buoyancy/meniscus geometry forinterfacial cases; effective density difference must be independently given','External T101 stock mass transfer modeled, actual automatic CMC hardware sequence not implemented','PowderWashburn,sedimentation,density,SFE,evaporation/chemistry,empiricalnoise/faults,realrobot/hardware not_run','Ring/plate exchange geometry represents host manual placement; no automatic probe changer','Sample liquid mesh is nominal30mL appearance, not quantitative dynamic volume display','Stage travel0–0.109m is a local collision-limited scene envelope; OEM stroke unknown, manufacturer speed limits retained','No deployment filesystem/process truth isolation claim'])
 spec=importlib.util.spec_from_file_location('_sigma_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'force_tensiometry':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'。'
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'force_tensiometry':mech})

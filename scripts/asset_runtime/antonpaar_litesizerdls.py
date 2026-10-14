"""CHEM-117 finite DLS/ELS optical sample and explicit measured-data transfer."""
from pathlib import Path
import copy,math,json,importlib.util
PROTOCOL_VERSION='1.0.0';SLUG='antonpaar_litesizerdls';CATALOG_ID='CHEM-117';ASSET_TYPE_ID='instrument.'+SLUG
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
from scipy.optimize import nnls,least_squares
KB=1.380649e-23;R=8.314462618;EPS0=8.8541878128e-12;WAVELENGTH=658e-9;DT=.1
LIB=json.loads((ASSET/'dls_library.json').read_text());SID='cad_09';INT=lambda lo,hi:dict(number('1',lo,hi),type='integer')
def wavevector(n,angle,wl=WAVELENGTH):return 4*math.pi*n*math.sin(math.radians(angle)/2)/wl
def second_order(tau,diameter,pdi,temp,eta,n,angle,beta):
 gamma=wavevector(n,angle)**2*KB*temp/(3*math.pi*eta*diameter);x=np.asarray(tau)*gamma
 return 1+beta*np.exp(-2*x) if pdi<1e-8 else 1+beta*np.exp(-2*np.log1p(pdi*x)/pdi)
def analyze(tau,g2,temp,eta,n,angle,calibration_factor=1.,regularization=.002):
 t=np.asarray(tau,float);y=np.asarray(g2,float)-1
 if len(t)!=len(y) or len(t)<20 or not np.all(np.isfinite(y)) or not np.all(np.diff(t)>0):raise ActionRejected('INVALID_CORRELATION')
 # Ignore first0.1µs channels with source detector artifacts, and inspect positive decaying segment.
 mask=(t>=2e-7)&(y>0);peak=float(np.max(y[mask])) if np.any(mask) else 0.
 if peak<.003:raise ActionRejected('INSUFFICIENT_CORRELATION')
 mask&=(y>=peak*.20)&(y<=peak*.97);tt=t[mask];yy=y[mask]
 if len(tt)<8:raise ActionRejected('INSUFFICIENT_CORRELATION')
 scale=float(np.median(tt));xx=tt/scale;co=np.polyfit(xx,.5*np.log(yy),2);gamma=-co[1]/scale;mu2=2*co[0]/scale**2
 if gamma<=0:raise ActionRejected('NONDECAYING_CORRELATION')
 # A gamma cumulant resummation avoids negative g1 tails; is an explicit fit model, not OEM algorithm.
 def model(z):
  b,g,p=z;return b*np.exp(-2*np.log1p(p*g*xx)/p) if p>1e-8 else b*np.exp(-2*g*xx)
 init=[min(max(math.exp(2*co[2]),peak),2.),max(gamma*scale,1e-4),min(max(mu2/gamma**2,.001),.95)]
 fit=least_squares(lambda z:model(z)-yy,init,bounds=([.001,1e-6,0.],[2.,1e4,1.]),max_nfev=400,xtol=1e-11,ftol=1e-11,gtol=1e-11);beta,gs,pdi=fit.x;gamma=gs/scale;q=wavevector(n,angle);dh=KB*temp*q*q/(3*math.pi*eta*gamma)*calibration_factor
 if not .3e-9<=dh<=15e-6:raise ActionRejected('SIZE_RANGE')
 # Independent nonnegative Laplace inversion of g1 on a diameter grid.
 d=np.geomspace(.3e-9,15e-6,120);rates=q*q*KB*temp/(3*math.pi*eta*d);mask2=(t>=2e-7)&(y>beta*.002)&(y<beta*1.05);tf=t[mask2];target=np.sqrt(np.maximum(y[mask2]/beta,0));K=np.exp(-tf[:,None]*rates[None,:]);L=np.diff(np.eye(len(d)),2,axis=0);aug=np.vstack([K,math.sqrt(regularization)*L,np.ones((1,len(d)))*8]);rhs=np.r_[target,np.zeros(len(d)-2),8.]
 weights,_=nnls(aug,rhs,maxiter=2000);weights/=max(weights.sum(),1e-30);rmse=float(np.sqrt(np.mean((model(fit.x)-yy)**2)));warn=['NNLS intensity distribution; not number/volume distribution or MAPS','Gamma-cumulant fit and regularized inversion; no OEM algorithm equivalence']
 if rmse>max(.01,.05*beta):warn.append('poor_correlation_fit')
 return dict(z_average_m=float(dh),pdi=float(pdi),beta=float(beta),fit_rmse=rmse,diameter_m=(d*calibration_factor).tolist(),intensity_fraction=weights.tolist(),warnings=warn)
PLANT=obj({'ambient_k':number('K',283.15,308.15),'heat_capacity_j_k':number('J/K',10,200),'sample_specific_heat_j_kg_k':number('J/(kg*K)',500,5000),'conductance_w_k':number('W/K',.01,2),'ambient_conductance_w_k':number('W/K',.001,.5),'peltier_max_w':number('W',.1,40),'temperature_gain':number('1',.95,1.05),'temperature_offset_k':number('K',-5,5),'solvent_density_kg_m3':number('kg/m^3',500,2000),'true_viscosity_pa_s':number('Pa*s',1e-5,.1),'viscosity_reference_k':number('K',273.15,393.15),'viscosity_activation_j_mol':number('J/mol',0,40000),'true_refractive_index':number('1',1.28,1.50),'true_relative_permittivity':number('1',2,120),'true_henry_f':number('1',1,1.5),'conductivity_s_m':number('S/m',0,20),'eof_velocity_m_s':number('m/s',-.001,.001),'electrode_area_m2':number('m^2',1e-8,1e-4),'electrode_gap_m':number('m',.001,.03),'laser_absorbed_fraction':number('1',0,1),'correlation_noise_sd':number('1',0,.02),'phase_noise_sd_rad':number('rad',0,.1),'beta':number('1',.02,.9),'min_liquid_k':number('K',273.15,350),'max_liquid_k':number('K',300,393.15),'dewpoint_k':number('K',253.15,305),'accept_engineering_model':BOOL})
PLANT_EXAMPLE=dict(ambient_k=293.15,heat_capacity_j_k=40.,sample_specific_heat_j_kg_k=4180.,conductance_w_k=.4,ambient_conductance_w_k=.015,peltier_max_w=15.,temperature_gain=1.,temperature_offset_k=0.,solvent_density_kg_m3=998.,true_viscosity_pa_s=.001002,viscosity_reference_k=293.15,viscosity_activation_j_mol=15500.,true_refractive_index=1.333,true_relative_permittivity=80.2,true_henry_f=1.5,conductivity_s_m=.05,eof_velocity_m_s=1e-6,electrode_area_m2=1e-6,electrode_gap_m=.010,laser_absorbed_fraction=.01,correlation_noise_sd=0.,phase_noise_sd_rad=0.,beta=.4,min_liquid_k=273.15,max_liquid_k=368.15,dewpoint_k=283.15,accept_engineering_model=True)
SAMPLE=obj({'sample_id':TEXT,'reference_id':TEXT,'volume_m3':number('m^3',650e-9,3e-6),'particle_mass_kg':number('kg',1e-10,.001),'initial_temperature_k':number('K',273.15,393.15),'aqueous':BOOL,'bubble_free':BOOL,'accept_cross_instrument_transfer':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='cad9_aqueous',reference_id=SID,volume_m3=.7e-6,particle_mass_kg=7e-7,initial_temperature_k=293.15,aqueous=True,bubble_free=True,accept_cross_instrument_transfer=True)
METHOD=obj({'mode':enum(['dls','zeta']),'angle_deg':dict(number('deg'),enum=[15,90,175]),'temperature_k':number('K',273.15,393.15),'entered_viscosity_pa_s':number('Pa*s',1e-5,.1),'entered_refractive_index':number('1',1.28,1.50),'entered_relative_permittivity':number('1',2,120),'henry_f':number('1',1,1.5),'field_v_m':number('V/m',1,10000),'acquisition_s':number('s',1,30),'acquisitions':INT(1,30),'temperature_tolerance_k':number('K',.02,1),'equilibration_s':number('s',.1,300),'timeout_s':number('s',10,7200),'regularization':number('1',1e-6,1)})
METHOD_EXAMPLE=dict(mode='dls',angle_deg=90,temperature_k=293.15,entered_viscosity_pa_s=.001002,entered_refractive_index=1.333,entered_relative_permittivity=80.2,henry_f=1.5,field_v_m=500.,acquisition_s=2.,acquisitions=3,temperature_tolerance_k=.2,equilibration_s=1.,timeout_s=300.,regularization=.002)
TCAL=obj({'reference_k':arr(number('K',273.15,393.15),2),'measured_k':arr(number('K',250,420),2)})
MCAL=obj({'reference_m2_v_s':arr(number('m^2/(V*s)',-2e-7,2e-7),2),'measured_m2_v_s':arr(number('m^2/(V*s)',-3e-7,3e-7),2)})
DLSRESULT=obj({'z_average_m':number('m',0),'pdi':number('1',0,1),'beta':number('1',0),'fit_rmse':number('1',0),'diameter_m':arr(number('m',0)),'intensity_fraction':arr(number('1',0,1)),'warnings':arr(TEXT)})
ELSRESULT=obj({'mobility_m2_v_s':number('m^2/(V*s)'),'zeta_v':number('V'),'field_v_m':number('V/m'),'time_s':arr(number('s',0)),'phase_plus_rad':arr(number('rad')),'phase_minus_rad':arr(number('rad')),'phase_residual_rad':number('rad',0),'source':TEXT})
RESULT=obj({'template_source':TEXT,'evidence':TEXT,'phase':PHASE,'stage':TEXT,'elapsed_s':number('s',0),'acquisitions_done':INT(0,30),'mode':TEXT,'lag_s':arr(number('s',0)),'g2':arr(number('1')),'dls':nullable(DLSRESULT),'els':nullable(ELSRESULT),'warnings':arr(TEXT)})
LEDGER=obj({k:number(u) for k,u in [('loaded_liquid_kg','kg'),('present_liquid_kg','kg'),('returned_liquid_kg','kg'),('liquid_residual_kg','kg'),('loaded_particles_kg','kg'),('present_particles_kg','kg'),('returned_particles_kg','kg'),('particle_residual_kg','kg'),('stored_heat_j','J'),('introduced_heat_j','J'),('returned_heat_j','J'),('peltier_heat_j','J'),('ambient_heat_j','J'),('laser_absorbed_j','J'),('electrode_heat_j','J'),('heat_balance_j','J'),('gas_added_mol','mol'),('gas_stock_mol','mol'),('gas_exhaust_mol','mol'),('gas_residual_mol','mol')]})
CAPS={};SLOTS={};SLOT_ZH={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},effects or [])
def slot(k,s,u,z,o='derived',v='agent',c='controller',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller','peltier','laser'],'宿主配置有限样品、热学、真实溶剂与可选测量误差假设','configuration','evaluator',PLANT_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['cuvette','omega_body','sample_liquid'],'人工装入有限胶体；数据条件迁移必须显式接受','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.unload',obj({}),obj({'returned_liquid_kg':number('kg',0),'returned_particles_kg':number('kg',0)}),['cuvette','sample_liquid'],'取回未消耗的胶体与液体，热量和物质账本持续保留',vis='evaluator')
cap('cell.configure',obj({'kind':enum(['quartz','omega'])}),obj({'kind':TEXT}),['cell_select','cuvette','omega_body'],'人工更换石英池或Omega电位池；几何平移为选择代理非真实执行器','configuration',ex={'kind':'omega'},effects=['cell.kind','cell.select_m'])
cap('cuvette.set',obj({'lift_m':number('m',0,.120)}),obj({'lift_m':number('m',0,.120)}),['cuvette'],'打开外盖后人工取放光学池；运行和高温互锁',ex={'lift_m':.12},effects=['cuvette.lift_m'])
cap('cell_cap.set',obj({'lift_m':number('m',0,.022)}),obj({'lift_m':number('m',0,.022)}),['cell_cap'],'池外受检人工开合比色皿盖与堵头',ex={'lift_m':.022},effects=['cell_cap.lift_m'])
cap('lid.release',obj({}),obj({'released':BOOL}),['open_button','sample_lid'],'按OPEN释放外盖，按键0.2s后复位',effects=['open_button.travel_m','lid.released'])
cap('lid.set',obj({'angle_rad':number('rad',0,1.70)}),obj({'angle_rad':number('rad',0,1.70)}),['sample_lid'],'释放后向后翻盖；闭盖检查比色皿插入和封口',ex={'angle_rad':1.7},effects=['lid.angle_rad'])
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_switch','lamp_power','lamp_status'],'6分钟激光预热；断电取消采集、停止激光与电场，样品继续被动换热',ex={'enabled':True},effects=['power.enabled','power.angle_rad','lamp.power_green','lamp.power_blue','lamp.status_green'])
cap('purge.fill',obj({'moles':number('mol',0,20)}),obj({'stock_mol':number('mol',0)}),['purge_in'],'宿主加入有限干燥N2储量','configuration','evaluator',{'moles':1.})
cap('purge.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['purge_in','purge_out'],'低于露点时启用2L/min干燥N2吹扫；有限耗气',ex={'enabled':True},effects=['purge.enabled'])
cap('method.configure',METHOD,obj({'configured':BOOL}),['controller','peltier','laser','apd_15','apd_90','apd_175'],'DLS三角或15度ELS，独立输入溶剂及温度参数','configuration',ex=METHOD_EXAMPLE)
cap('temperature.calibrate',TCAL,obj({'calibrated':BOOL}),['thermistor'],'输入独立外部两点温度标准，禁止读取真值自校准','configuration',ex={'reference_k':[293.15,313.15],'measured_k':[293.15,313.15]})
cap('dls.calibrate',obj({'reference_m':number('m',.3e-9,15e-6),'measured_m':number('m',.3e-9,15e-6)}),obj({'factor':number('1',0)}),['controller','apd_90'],'保存独立标准与观测的粒径比例校正；示例为工程输入','configuration',ex={'reference_m':100e-9,'measured_m':100e-9})
cap('mobility.calibrate',MCAL,obj({'calibrated':BOOL}),['omega_electrode_0','omega_electrode_1'],'外部迁移率两点标定，作用于相位反演结果','configuration',ex={'reference_m2_v_s':[-1e-8,1e-8],'measured_m2_v_s':[-1e-8,1e-8]})
cap('acquisition.start',obj({}),obj({'operation_id':TEXT,'phase':PHASE}),['controller','laser','apd_15','apd_90','apd_175','omega_electrode_0','omega_electrode_1'],'温度平衡后按时间采集相关函数或正反电场相位',effects=['acquisition.phase','acquisition.stage'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['controller'],'取消保留采集和物质/能量；立即撤电场及测量激光',effects=['acquisition.phase','acquisition.stage'])
cap('process.measure',obj({}),RESULT,['controller'],'纯读g2、非负反演强度PSD、PDI、迁移率与ζ及来源','query')
cap('ledger.read',obj({}),LEDGER,['controller'],'有限液体/粒子/N2和两节点热量守恒','query','evaluator')
cap('template.reference',obj({'substance_id':TEXT}),obj({'name':TEXT,'evidence':TEXT,'source':TEXT,'raw_acquisitions':INT(0,100),'dls_statistics_available':BOOL,'zeta_available':BOOL}),['controller'],'真实材料原始统计量/曲线证据查询','query',ex={'substance_id':SID})
UNSUPPORTED={'hardware.connect','fault.probability_model','robot.load','analysis.mie_number_concentration','analysis.maps','analysis.sls_molar_mass','accessory.univette','accessory.ultralowvolume','analysis.cmpals_oem'}
for k in sorted(UNSUPPORTED):cap(k,obj({}),obj({}),['controller'],'范围外功能，明确unsupported不输出伪读数')
slot('plant.configured',BOOL,'1','宿主物理参数已配置','setpoint');slot('power.enabled',BOOL,'1','电源状态','setpoint',w=('power.set','enabled'));slot('power.angle_rad',number('rad',0,.32),'rad','后部电源摇臂位置','scene',c='power_switch')
for k,part in [('power_green','lamp_power'),('power_blue','lamp_power'),('status_green','lamp_status'),('status_red','lamp_status')]:slot('lamp.'+k,BOOL,'1','指示灯色通道 '+k,c=part)
slot('laser.warmup_s',number('s',0,360),'s','激光上电预热时间');slot('laser.emitting',BOOL,'1','外盖互锁后的测量激光发射状态',c='laser')
for k,s,u,z,c in [('lid.angle_rad',number('rad',0,1.7),'rad','外盖翻转角','sample_lid'),('open_button.travel_m',number('m',0,.002),'m','OPEN按键行程','open_button'),('cell.select_m',number('m',0,.06),'m','可换池人工选择代理','cell_select'),('cuvette.lift_m',number('m',0,.12),'m','人工移出池高度','cuvette'),('cell_cap.lift_m',number('m',0,.022),'m','比色皿盖和Luer堵头升程','cell_cap')]:slot(k,s,u,z,'scene',c=c)
slot('lid.released',BOOL,'1','样品盖已释放');slot('cell.kind',TEXT,'1','当前相容池类型',c='cell_select');slot('sample.loaded',BOOL,'1','有限样品已装入',c='sample_liquid');slot('sample.identity',TEXT,'1','样品私有身份','process_truth','evaluator','sample_liquid');slot('sample.mass_kg',number('kg',0),'kg','私有颗粒质量','process_truth','evaluator','sample_liquid');slot('sample.temperature_k',number('K',0),'K','私有样品温度','process_truth','evaluator','sample_liquid');slot('temperature.measured_k',number('K',0),'K','经独立校正的座体传感器读数','sensor',c='thermistor');slot('temperature.target_k',number('K',273.15,393.15),'K','方法设定温度','setpoint',c='peltier');slot('field.measured_v_m',number('V/m'),'V/m','已启用电场代理','sensor',c='omega_electrode_0');slot('current.measured_a',number('A'),'A','电导与有效截面电流代理','sensor',c='omega_electrode_0');slot('purge.enabled',BOOL,'1','N2吹扫状态','setpoint');slot('purge.stock_mol',number('mol',0),'mol','有限N2余量','sensor',c='purge_in')
for k,s,u,z in [('phase',PHASE,'1','异步测量阶段'),('stage',TEXT,'1','温度平衡/采集阶段'),('elapsed_s',number('s',0),'s','测量经过时间'),('failure',TEXT,'1','明确失败原因'),('acquisitions_done',INT(0,30),'1','完成的重复采集数')]:slot('acquisition.'+k,s,u,z)
# State effects include conditional changes and their derived sensor/lamp observations.
# Analysis calibration factors are internal settings: they affect only future captures.
IMMEDIATE_EFFECTS={
 'plant.configure':['plant.configured','sample.temperature_k','temperature.measured_k','temperature.target_k'],
 'sample.load':['sample.loaded','sample.identity','sample.mass_kg','sample.temperature_k'],
 'sample.unload':['sample.loaded','sample.identity','sample.mass_kg'],
 'purge.fill':['purge.stock_mol'],
 'method.configure':['temperature.target_k'],
 'temperature.calibrate':['temperature.measured_k'],
 'lid.set':['lid.angle_rad','lid.released'],
 'power.set':['power.enabled','power.angle_rad','lamp.power_green','lamp.power_blue','lamp.status_green','lamp.status_red','laser.warmup_s','laser.emitting','field.measured_v_m','current.measured_a','acquisition.phase','acquisition.stage'],
 'acquisition.start':['acquisition.phase','acquisition.stage','acquisition.elapsed_s','acquisition.failure','acquisition.acquisitions_done','lamp.status_green','lamp.status_red'],
 'acquisition.stop':['acquisition.phase','acquisition.stage','laser.emitting','field.measured_v_m','current.measured_a'],
}
STEP_EFFECTS={
 'acquisition.start':['temperature.measured_k','sample.temperature_k','laser.emitting','field.measured_v_m','current.measured_a','purge.stock_mol','purge.enabled','acquisition.elapsed_s','acquisition.phase','acquisition.stage','acquisition.failure','acquisition.acquisitions_done','lamp.status_green','lamp.status_red'],
 'lid.release':['open_button.travel_m'],
 'method.configure':['temperature.measured_k','sample.temperature_k'],
 'power.set':['laser.warmup_s','lamp.power_green','lamp.power_blue','temperature.measured_k','sample.temperature_k'],
 'purge.set':['purge.stock_mol','purge.enabled'],
}
class AntonpaarLitesizerdlsRuntime:
 def __init__(self,instance_id='litesizerdls_01'):
  self.instance_id=instance_id;self.time_s=0.;self.pending=0.;self.revision=0;self.plant=None;self.powered=False;self.warmup=0.;self.lid=0.;self.released=False;self.button_timer=0.;self.lift=0.;self.caplift=0.;self.cell='quartz';self.sample=None;self.temperature=293.15;self.block=293.15;self.target=293.15;self.method=None;self.tcal=(1.,0.);self.dcal=1.;self.mcal=(1.,0.);self.loaded_liquid=0.;self.returned_liquid=0.;self.loaded_particles=0.;self.returned_particles=0.;self.heat_in=0.;self.heat_out=0.;self.heat_peltier=0.;self.heat_ambient=0.;self.heat_laser=0.;self.heat_electrode=0.;self.purge=False;self.gas_added=0.;self.gas=0.;self.gas_exhaust=0.;self.phase='idle';self.stage='idle';self.failure='';self.elapsed=0.;self.stable=0.;self.sample_timer=0.;self.count=0;self.corr=[];self.lag=[];self.dls=None;self.els=None;self.warnings=[];self.result_source='';self.result_mode='';self.evidence='';self.serial=0;self.operation_id=None;self.records={};self._principals={};self._owners={};self._requests={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ['agent','debug','evaluator'] or principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return AntonpaarLitesizerdlsSession(self,principal_id)
 def describe(self):return export_antonpaar_litesizerdls_contract_metadata()
 def eta(self):
  p=self.plant;return p['true_viscosity_pa_s']*math.exp(p['viscosity_activation_j_mol']/R*(1/self.temperature-1/p['viscosity_reference_k'])) if p else .001
 def indicated(self):return (self.block*self.plant['temperature_gain']+self.plant['temperature_offset_k']-self.tcal[1])/self.tcal[0] if self.plant else self.block
 def active(self):return self.powered and self.warmup>=360-1e-8 and self.phase=='running' and self.stage=='acquiring' and self.lid<1e-5 and self.lift<1e-5
 def field(self):return self.method['field_v_m'] if self.active() and self.method['mode']=='zeta' else 0.
 def capacity(self):return self.sample['volume_m3']*self.plant['solvent_density_kg_m3']*self.plant['sample_specific_heat_j_kg_k'] if self.sample else 0.
 def read(self,visibility='agent'):
  d={'plant.configured':self.plant is not None,'power.enabled':self.powered,'power.angle_rad':.32 if self.powered else 0.,'lamp.power_green':self.powered and self.warmup>=360-1e-8,'lamp.power_blue':self.powered and self.warmup<360-1e-8,'lamp.status_green':self.powered and self.phase!='failed','lamp.status_red':self.powered and self.phase=='failed','laser.warmup_s':self.warmup,'laser.emitting':self.active(),'lid.angle_rad':self.lid,'lid.released':self.released,'open_button.travel_m':.002 if self.button_timer>0 else 0.,'cuvette.lift_m':self.lift,'cell_cap.lift_m':self.caplift,'cell.select_m':.06 if self.cell=='omega' else 0.,'cell.kind':self.cell,'sample.loaded':self.sample is not None,'sample.identity':self.sample['sample_id'] if self.sample else '','sample.mass_kg':self.sample['particle_mass_kg'] if self.sample else 0.,'sample.temperature_k':self.temperature,'temperature.measured_k':self.indicated(),'temperature.target_k':self.target,'field.measured_v_m':self.field(),'current.measured_a':self.plant['conductivity_s_m']*self.field()*self.plant['electrode_area_m2'] if self.plant else 0.,'purge.enabled':self.purge,'purge.stock_mol':self.gas,'acquisition.phase':self.phase,'acquisition.stage':self.stage,'acquisition.elapsed_s':self.elapsed,'acquisition.failure':self.failure,'acquisition.acquisitions_done':self.count}
  return {k:v for k,v in d.items() if visibility!='agent' or SLOTS[k][3]=='agent'}
 def _result(self):return dict(template_source=self.result_source,evidence=self.evidence,phase=self.phase,stage=self.stage,elapsed_s=self.elapsed,acquisitions_done=self.count,mode=self.result_mode,lag_s=copy.deepcopy(self.lag),g2=np.mean(self.corr,axis=0).tolist() if self.corr else [],dls=copy.deepcopy(self.dls),els=copy.deepcopy(self.els),warnings=list(self.warnings))
 def _ledger(self):
  liquid=self.sample['volume_m3']*self.plant['solvent_density_kg_m3'] if self.sample else 0.;pm=self.sample['particle_mass_kg'] if self.sample else 0.;stored=(self.plant['heat_capacity_j_k']*(self.block-self.plant['ambient_k'])+self.capacity()*(self.temperature-self.plant['ambient_k'])) if self.plant else 0.
  return dict(loaded_liquid_kg=self.loaded_liquid,present_liquid_kg=liquid,returned_liquid_kg=self.returned_liquid,liquid_residual_kg=self.loaded_liquid-liquid-self.returned_liquid,loaded_particles_kg=self.loaded_particles,present_particles_kg=pm,returned_particles_kg=self.returned_particles,particle_residual_kg=self.loaded_particles-pm-self.returned_particles,stored_heat_j=stored,introduced_heat_j=self.heat_in,returned_heat_j=self.heat_out,peltier_heat_j=self.heat_peltier,ambient_heat_j=self.heat_ambient,laser_absorbed_j=self.heat_laser,electrode_heat_j=self.heat_electrode,heat_balance_j=stored+self.heat_out-self.heat_in-self.heat_peltier-self.heat_ambient-self.heat_laser-self.heat_electrode,gas_added_mol=self.gas_added,gas_stock_mol=self.gas,gas_exhaust_mol=self.gas_exhaust,gas_residual_mol=self.gas_added-self.gas-self.gas_exhaust)
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[k][0],a);before=copy.deepcopy(self.__dict__)
  try:
   r=self._invoke(k,a);validate(CAPS[k][1],r)
   if CAPS[k][4] not in ['query','analysis']:self.revision+=1
   return r
  except Exception:self.__dict__.clear();self.__dict__.update(before);raise
 def _invoke(self,k,a):
  if k=='process.measure':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='template.reference':
   d=LIB['substances'].get(a['substance_id'])
   if not d:raise ActionRejected('UNKNOWN_REFERENCE')
   return dict(name=d['name'],evidence=d['evidence'],source=d['source']['url'],raw_acquisitions=len(d['value']['g2']['acquisitions']) if d['value']['g2'] else 0,dls_statistics_available=d['value']['diameter_m'] is not None,zeta_available=d['value']['zeta_v'] is not None)
  if k=='plant.configure':
   if self.plant or self.powered or not a['accept_engineering_model']:raise ActionRejected('INITIALIZATION_ONLY')
   if a['max_liquid_k']<=a['min_liquid_k'] or not a['min_liquid_k']<=a['ambient_k']<=a['max_liquid_k']:raise ActionRejected('INVALID_ARGUMENT')
   self.plant=copy.deepcopy(a);self.temperature=self.block=self.target=a['ambient_k'];return {'configured':True}
  if k=='power.set':
   if a['enabled'] and not self.powered:self.warmup=0.
   self.powered=a['enabled']
   if not self.powered:
    self.warmup=0.
    if self.phase=='running':self._end('cancelled')
   return a
  if not self.plant:raise ActionRejected('NOT_CONFIGURED')
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self._end('cancelled');return {'phase':self.phase}
  if self.phase=='running':raise ActionRejected('BUSY')
  if k=='lid.release':self.released=True;self.button_timer=.2;return {'released':True}
  if k=='lid.set':
   if a['angle_rad']>self.lid and not self.released:raise ActionRejected('LID_NOT_RELEASED')
   if a['angle_rad']<.01 and (self.lift>.001 or self.caplift>.001):raise ActionRejected('CLOSURE_INTERLOCK')
   self.lid=a['angle_rad']
   if self.lid<.01:self.released=False
   return a
  if k=='cuvette.set':
   if self.lid<1.2 or self.temperature>313.15:raise ActionRejected('SAMPLE_ACCESS_INTERLOCK')
   if a['lift_m']<.001 and self.caplift>.001:raise ActionRejected('CLOSURE_INTERLOCK')
   self.lift=a['lift_m'];return a
  if k=='cell_cap.set':
   if self.lift<.10 or self.temperature>313.15:raise ActionRejected('SAMPLE_ACCESS_INTERLOCK')
   self.caplift=a['lift_m'];return a
  if k=='cell.configure':
   if self.sample or self.lift<.10:raise ActionRejected('SAMPLE_ACCESS_INTERLOCK')
   self.cell=a['kind'];return a
  if k=='sample.load':
   if self.sample or self.lift<.10 or self.caplift<.02:raise ActionRejected('SAMPLE_ACCESS_INTERLOCK')
   if not a['accept_cross_instrument_transfer']:raise ActionRejected('TRANSFER_NOT_ACCEPTED')
   if a['reference_id'] not in LIB['substances']:raise ActionRejected('UNKNOWN_REFERENCE')
   if not a['bubble_free']:raise ActionRejected('BUBBLES_UNMODELED')
   if self.cell=='omega' and (not a['aqueous'] or not 650e-9<=a['volume_m3']<=900e-9):raise ActionRejected('CELL_COMPATIBILITY')
   if self.cell=='quartz' and not .85e-6<=a['volume_m3']<=3e-6:raise ActionRejected('CELL_VOLUME')
   if a['particle_mass_kg']>a['volume_m3']*self.plant['solvent_density_kg_m3']*.1:raise ActionRejected('DILUTE_MODEL_LIMIT')
   if not self.plant['min_liquid_k']<=a['initial_temperature_k']<=self.plant['max_liquid_k']:raise ActionRejected('LIQUID_PHASE_UNMODELED')
   self.sample=copy.deepcopy(a);self.temperature=a['initial_temperature_k'];self.loaded_liquid+=a['volume_m3']*self.plant['solvent_density_kg_m3'];self.loaded_particles+=a['particle_mass_kg'];self.heat_in+=self.capacity()*(self.temperature-self.plant['ambient_k']);return {'loaded':True}
  if k=='sample.unload':
   if not self.sample or self.lift<.10 or self.caplift<.02 or self.temperature>313.15:raise ActionRejected('SAMPLE_ACCESS_INTERLOCK')
   liquid=self.sample['volume_m3']*self.plant['solvent_density_kg_m3'];pm=self.sample['particle_mass_kg'];self.returned_liquid+=liquid;self.returned_particles+=pm;self.heat_out+=self.capacity()*(self.temperature-self.plant['ambient_k']);self.sample=None;return dict(returned_liquid_kg=liquid,returned_particles_kg=pm)
  if k=='purge.fill':self.gas+=a['moles'];self.gas_added+=a['moles'];return {'stock_mol':self.gas}
  if k=='purge.set':
   if a['enabled'] and self.gas<=0:raise ActionRejected('GAS_EXHAUSTED')
   self.purge=a['enabled'];return a
  if k=='method.configure':
   if a['mode']=='zeta' and a['angle_deg']!=15:raise ActionRejected('ELS_ANGLE')
   if not self.plant['min_liquid_k']+.01<=a['temperature_k']<=self.plant['max_liquid_k']-.01:raise ActionRejected('LIQUID_PHASE_UNMODELED')
   self.method=copy.deepcopy(a);self.target=a['temperature_k'];return {'configured':True}
  if k in ['temperature.calibrate','mobility.calibrate']:
   kk=['reference_k','measured_k'] if k.startswith('temperature') else ['reference_m2_v_s','measured_m2_v_s'];x,y=(a[q] for q in kk)
   if x[1]<=x[0] or y[1]<=y[0]:raise ActionRejected('INVALID_CALIBRATION')
   gain=(y[1]-y[0])/(x[1]-x[0]);offset=y[0]-gain*x[0]
   if not .5<=gain<=2:raise ActionRejected('INVALID_CALIBRATION')
   if k.startswith('temperature'):self.tcal=(gain,offset)
   else:self.mcal=(gain,offset)
   return {'calibrated':True}
  if k=='dls.calibrate':
   f=a['reference_m']/a['measured_m']
   if not .5<=f<=2:raise ActionRejected('INVALID_CALIBRATION')
   self.dcal=f;return {'factor':f}
  if k=='acquisition.start':
   if not self.powered or self.warmup<360-1e-8 or not self.sample or not self.method:raise ActionRejected('NOT_READY')
   if self.lid>.001 or self.lift>.001 or self.caplift>.001:raise ActionRejected('CLOSURE_INTERLOCK')
   if self.method['temperature_k']<self.plant['dewpoint_k'] and (not self.purge or self.gas<=0):raise ActionRejected('PURGE_REQUIRED')
   ref=LIB['substances'][self.sample['reference_id']]
   if self.method['mode']=='zeta':
    if self.cell!='omega':raise ActionRejected('CELL_COMPATIBILITY')
    if ref['value']['zeta_v'] is None:raise ActionRejected('ZETA_UNAVAILABLE')
   self.phase='running';self.stage='equilibrating';self.failure='';self.elapsed=self.stable=self.sample_timer=0.;self.count=0;self.corr=[];self.lag=[];self.dls=None;self.els=None;self.warnings=['cross_instrument_and_conditions_transfer_engineering_assumption'];self.result_source=ref['source']['url'];self.result_mode=self.method['mode'];self.evidence='measured_literature raw_g2; derived timewarp and inversion' if ref['value']['g2'] and self.method['mode']=='dls' else 'measured_literature scalar inputs; engineering_assumption signal; derived inversion';self.serial+=1;self.operation_id=self.instance_id+':'+str(self.serial);return dict(operation_id=self.operation_id,phase=self.phase)
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _end(self,phase,failure=''):
  self.phase=phase;self.stage='complete' if phase=='succeeded' else phase;self.failure=failure
  if failure:self.warnings.append(failure)
  if self.operation_id:self.records[self.operation_id]=self._result()
 def _capture(self):
  m=self.method;p=self.plant;ref=LIB['substances'][self.sample['reference_id']];rng=np.random.default_rng(1901+self.count)
  if m['mode']=='dls':
   if ref['value']['g2']:
    c=ref['conditions'];raw=ref['value']['g2'];lag=np.asarray(raw['lag_s']);curves=list(raw['acquisitions'].values());y=np.asarray(curves[self.count%len(curves)]);factor=(self.temperature/self.eta())/(c['temperature_k']/c['viscosity_pa_s'])*(wavevector(p['true_refractive_index'],m['angle_deg'])/wavevector(c['refractive_index'],c['angle_deg'],c['wavelength_m']))**2
    g2=np.interp(lag*factor,lag,y,left=float(np.max(y[:8])),right=1.);self.warnings=list(dict.fromkeys(self.warnings+['raw_source_conditions_scaled; source viscosity discrepancy retained']))
   else:
    lag=np.geomspace(1e-7,10.,280);v=ref['value'];g2=second_order(lag,v['diameter_m'],v['pdi'],self.temperature,self.eta(),p['true_refractive_index'],m['angle_deg'],p['beta']);self.warnings=list(dict.fromkeys(self.warnings+['gamma_decay_distribution_constructed_from_scalar_statistics; no measured PSD or g2 for this reference']))
   g2+=rng.normal(0,p['correlation_noise_sd']/math.sqrt(m['acquisition_s']),len(g2));self.lag=lag.tolist();self.corr.append(g2.tolist());mean=np.mean(self.corr,axis=0);self.dls=analyze(lag,mean,self.indicated(),m['entered_viscosity_pa_s'],m['entered_refractive_index'],m['angle_deg'],self.dcal,m['regularization'])
  else:
   mu=2*EPS0*p['true_relative_permittivity']*ref['value']['zeta_v']*p['true_henry_f']/(3*self.eta());field=m['field_v_m'];q=wavevector(p['true_refractive_index'],15);t=np.linspace(0,.05,256);dt=t[1]-t[0]
   if q*(abs(mu*field)+abs(p['eof_velocity_m_s']))*dt>=math.pi:raise ActionRejected('PHASE_ALIASING')
   if not 1e-11<=abs(mu)<=2e-7:raise ActionRejected('MOBILITY_RANGE')
   traces=[];slopes=[];residual=[]
   for sign in [1,-1]:
    phase=q*(sign*mu*field+p['eof_velocity_m_s'])*t+.21+rng.normal(0,p['phase_noise_sd_rad'],len(t));wrapped=np.angle(np.exp(1j*phase));unwrapped=np.unwrap(wrapped);fit=np.polyfit(t,unwrapped,1);slopes.append(fit[0]);traces.append(wrapped.tolist());residual.append(np.std(unwrapped-np.polyval(fit,t)))
   qm=wavevector(m['entered_refractive_index'],15);measured=((slopes[0]-slopes[1])/(2*qm*field)-self.mcal[1])/self.mcal[0];zeta=3*m['entered_viscosity_pa_s']*measured/(2*EPS0*m['entered_relative_permittivity']*m['henry_f']);self.els=dict(mobility_m2_v_s=float(measured),zeta_v=float(zeta),field_v_m=field,time_s=t.tolist(),phase_plus_rad=traces[0],phase_minus_rad=traces[1],phase_residual_rad=float(max(residual)),source='engineered antisymmetric ELS phase; not OEM cmPALS acquisition');self.warnings=list(dict.fromkeys(self.warnings+['source_zeta_condition_transfer; Henry model and constant EOF explicit; nonlinear surface conduction omitted']))
  self.count+=1
 def step(self,dt):
  validate(number('s',0,7200),dt);self.pending+=dt
  while self.pending>=DT-1e-10:
   self.pending-=DT;self.time_s+=DT;self.revision+=1;self.button_timer=max(0.,self.button_timer-DT)
   if self.powered:self.warmup=min(360.,self.warmup+DT)
   if not self.plant:continue
   p=self.plant
   if self.purge:
    flow=(2e-3/60)*101325/(R*p['ambient_k'])*DT;use=min(self.gas,flow);self.gas-=use;self.gas_exhaust+=use
    if self.gas<=1e-14:
     self.gas=0.;self.purge=False
     if self.phase=='running' and self.target<p['dewpoint_k']:self._end('failed','GAS_EXHAUSTED')
   qheat=max(-p['peltier_max_w'],min(p['peltier_max_w'],20*(self.target-self.indicated()))) if self.powered else 0.;qamb=p['ambient_conductance_w_k']*(p['ambient_k']-self.block);qsample=p['conductance_w_k']*(self.block-self.temperature) if self.sample and self.lift<.001 else 0.;qlaser=.04*p['laser_absorbed_fraction'] if self.active() else 0.;qels=p['conductivity_s_m']*self.field()**2*min(self.sample['volume_m3'],p['electrode_area_m2']*p['electrode_gap_m']) if self.sample else 0.
   self.block+=(qheat+qamb-qsample)*DT/p['heat_capacity_j_k'];self.heat_peltier+=qheat*DT;self.heat_ambient+=qamb*DT;self.heat_laser+=qlaser*DT;self.heat_electrode+=qels*DT
   if self.sample:
    self.temperature+=(qsample+qlaser+qels)*DT/self.capacity()
    if not p['min_liquid_k']<=self.temperature<=p['max_liquid_k'] and self.phase=='running':self._end('failed','LIQUID_PHASE_UNMODELED')
   if self.phase!='running':continue
   self.elapsed+=DT
   if self.elapsed>=self.method['timeout_s']:self._end('failed','ACQUISITION_TIMEOUT');continue
   stable=abs(self.indicated()-self.target)<=self.method['temperature_tolerance_k'] and abs(self.temperature-self.block)<=self.method['temperature_tolerance_k']
   if not stable:self.stage='equilibrating';self.stable=0.;self.sample_timer=0.;continue
   self.stable+=DT
   if self.stable<self.method['equilibration_s']:continue
   self.stage='acquiring';self.sample_timer+=DT
   if self.sample_timer+1e-8>=self.method['acquisition_s']:
    self.sample_timer=0.
    try:self._capture()
    except ActionRejected as e:self._end('failed',str(e));continue
    if self.count>=self.method['acquisitions']:self._end('succeeded')
  self.pending=max(0.,self.pending)
  return self.read()

class AntonpaarLitesizerdlsSession:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,i):
  if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  m=export_antonpaar_litesizerdls_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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
   result=h.invoke(capability_id,**arguments);op=result.get('operation_id')
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





def export_antonpaar_litesizerdls_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions='SI; diameter not radius; source conditions retained; modeled optical and thermal transfers explicit',origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'AntonpaarLitesizerdlsSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description='fixed0.1s thermal/clock integration; pure read'),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNKNOWN_OPERATION','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','INITIALIZATION_ONLY','BUSY','NOT_READY','NOT_RUNNING','NOT_CONFIGURED','UNKNOWN_REFERENCE','TRANSFER_NOT_ACCEPTED','SAMPLE_ACCESS_INTERLOCK','LID_NOT_RELEASED','CLOSURE_INTERLOCK','CELL_COMPATIBILITY','CELL_VOLUME','BUBBLES_UNMODELED','DILUTE_MODEL_LIMIT','LIQUID_PHASE_UNMODELED','GAS_EXHAUSTED','PURGE_REQUIRED','ELS_ANGLE','ZETA_UNAVAILABLE','INVALID_CALIBRATION','PHASE_ALIASING','MOBILITY_RANGE','ACQUISITION_TIMEOUT','INVALID_CORRELATION','INSUFFICIENT_CORRELATION','NONDECAYING_CORRELATION','SIZE_RANGE']
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  un=k in UNSUPPORTED;asyn=k=='acquisition.start';caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if un else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if un else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if un else 'implemented'),binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.antonpaar_litesizerdls.AntonpaarLitesizerdlsSession.invoke',invocation=f'session.invoke(instance_id="litesizerdls_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if kind in ['query','analysis'] or un else [dict(description_zh='已配置真实条件和有限样品；受检光学盖/池/电极/温度/6分钟预热互锁',state_ids=['power.enabled','plant.configured','acquisition.phase','lid.angle_rad','cuvette.lift_m','cell_cap.lift_m'],enforced_by='runtime',check='atomic invoke; authenticated Session; step-only physics')],state_effects=[dict(state_id=x,timing='immediate',description_zh='受检状态更新') for x in IMMEDIATE_EFFECTS.get(k,effects)],observes=[],model_effects=dict(mode='none',description_zh='需要独立场景验证',binding_ids=[]),execution=dict(mode='asynchronous' if asyn else 'synchronous',time_domain='simulation',completion_criterion='温度平衡并完成指定重复采集' if asyn else '接受命令，过程step推进',completion_state_ids=['acquisition.phase'] if asyn else [],timeout_s=None,cancellation='supported' if asyn else 'unsupported',retry='same_request_id',resources=['host_clock','finite_sample','laser','peltier','purge_gas','request_cache'],partial_failure_zh='已采数据及物质/能量账本保留；停止电场和测量激光，有限温度不瞬间重置'),errors=[dict(code=x,meaning_zh=x,side_effects='none') for x in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=['实机误差、校准和故障分布未知；外部校准示例为工程输入'],source_ids=['manufacturer_specs','simulation_assumption'])
  if k in STEP_EFFECTS:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='由宿主step推进温控、按键复位、有限气源或采集状态') for x in STEP_EFFECTS[k]]
 mech=dict(component_ids=['controller','laser','apd_15','apd_90','apd_175','sample_liquid','cuvette','omega_body','omega_electrode_0','omega_electrode_1','peltier','thermistor'],state_ids=list(slots),meaning_zh='有限胶体、热学、真实DLS相关曲线/统计量输入与电泳相位反演',implementation='lumped',equations=['q=4*pi*n*sin(theta/2)/lambda; D=kB*T/(3*pi*eta*d_h); Gamma=q²D; lambda658nm, theta15/90/175degrees','Raw measured g2 retains original tau and all acquisition columns. Accepted cross-condition transfer rescales tau by (T/eta)/(Tref/etaref)*(q/qref)² with unchanged intensity weights','Scalar-only CAD records: assumed gamma distribution of decay rates gives g1=(1+PDI*Gamma_mean*tau)^(-1/PDI); g2=1+beta*g1²; this is constructed, not measured raw signal','Output size/PDI fitted from measured-or-modeled g2, never copied from reference scalar; independent NNLS inverse Laplace with second-difference regularization gives intensity weights over0.3nm–15µm; number distribution unsupported','Electrophoresis mu=2*epsilon0*epsilon_r*zeta*f_H/(3*eta); phase±=q*(±mu*E+v_EOF)*t+phase0; wrapped phase unwrapped, opposite fitted slopes remove constant EOF; mu_measured=(slope_plus-slope_minus)/(2*q_entered*E); zeta_reported=3*eta_entered*mu_cal/(2*epsilon_entered*f_entered)','Finite heat Cblock*dTb=Qpeltier+Gambient*(Tamb-Tb)-Gsample*(Tb-Ts); Csample*dTs=Gsample*(Tb-Ts)+laser_absorption+sigma*E²*Vactive; Qpeltier bounded independently, manufacturer50W not assumed heating power','Liquid/particles loaded=present+returned; measurement nondestructive; N2 added=stock+exhaust; purge2L/min converted at ambient n=PV/RT','External two-point temperature/mobility calibration and scalar DLS size factor are independent inputs. True viscosity/refractive index/permittivity and entered values are separated','Output source and evidence survive unload; host-authenticated private truth, request idempotence, owned async operations, failed call atomic rollback; pure reads never advance time'],parameters={},source_ids=['manufacturer_specs','manufacturer_manual500','cad_data','raynals_data','simulation_assumption'],limitations=['103 measured material references:96 chemically distinct CAD lipids in fixed formulation and7protein/gold systems; 124real acquisition columns, not124new materials.','CAD raw g2 and distributions absent; gamma reconstruction assumption. Raw Wyatt curves transferred to Litesizer by Brownian scaling, optical angular weighting held fixed; no vendor reproduction claim.','Current7010.3nm–15µm,0–120C; old500manual0.3nm–10µm,0–90C not used to narrow701hardware. Chosen liquid-phase envelope restricts measurement to host-approved solvent range.','Current701Omega and quartz represented; parked duplicate-cell selector is manual configuration proxy, not real internal motion. OEM cell dimensions except quartz12.5×12.5×45mm, part masses and travel unknown.','No full Mie/concentration/multiple scattering/MAPS, no number PSD, no SLS molecular weight, no true cmPALS control-loop implementation; disabled explicit unsupported.','Temperature law, thermal conductances, optical beta, optional random noise and electric parameters explicit engineering assumptions. Source uncertainty is not device noise. Effective liquid-node thermal storage excludes detailed particles/cuvette/whole-machine enthalpy and evaporation.','Independent calibration values accepted; no empirical OEM calibration/fault distribution or full standard-validation data.','Heated sample handling limited40C. Lid mutual exclusion protects simulated signal; actual laser/electrical safety certification, seal/contact, robot grasp and hardware not_run.','Source Raynals20C with0.00089Pa.s retained; CAD16/34/51source XLSX/PDF conflict preserved; CAD85originalTableS1andAtlasPDI0.16; previouslocal0.18transcriptionerrorcorrected.'])
 spec=importlib.util.spec_from_file_location('_litesizer_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'light_scattering':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'。'
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'light_scattering':mech})

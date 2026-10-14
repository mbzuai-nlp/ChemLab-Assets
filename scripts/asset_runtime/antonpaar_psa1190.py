"""CHEM-118 finite wet/dry laser diffraction with explicit optical transfer."""
from pathlib import Path
import copy,math,json,importlib.util
PROTOCOL_VERSION='1.0.0';SLUG='antonpaar_psa1190';CATALOG_ID='CHEM-118';ASSET_TYPE_ID='instrument.'+SLUG
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
from scipy.special import spherical_jn,spherical_yn,j1
from scipy.optimize import nnls
from functools import lru_cache
DT=.1;RGAS=8.314462618;LIB=json.loads((ASSET/'particle_library.json').read_text());SID='steel_batch_1'
EDGES=np.geomspace(.04e-6,2500e-6,111);DIAM=np.sqrt(EDGES[:-1]*EDGES[1:]);THETA=np.r_[np.geomspace(.03,35,40),np.geomspace(.2,88,24),np.geomspace(.015,.06,7)];WL=np.r_[np.full(40,830e-9),np.full(24,635e-9),np.full(7,830e-9)]
INT=lambda lo,hi:dict(number('1',lo,hi),type='integer')
def mie_scattering(x,m,theta):
 """Riccati-Bessel Lorenz-Mie sphere; m.imag>=0 with outgoing Hankel+ convention."""
 ns=int(x+4*x**(1/3)+2);nm=int(max(ns,abs(m*x)))+25;z=m*x;D=np.zeros(nm+1,dtype=complex)
 for n in range(nm,0,-1):D[n-1]=n/z-1/(D[n]+n/z)
 n=np.arange(1,ns+1);ps=x*spherical_jn(n,x);pm=x*spherical_jn(n-1,x);xi=ps+1j*x*spherical_yn(n,x);xm=pm+1j*x*spherical_yn(n-1,x);a=((D[n]/m+n/x)*ps-pm)/((D[n]/m+n/x)*xi-xm);b=((m*D[n]+n/x)*ps-pm)/((m*D[n]+n/x)*xi-xm)
 mu=np.cos(theta);pi0=np.zeros(len(theta));pi1=np.ones(len(theta));s1=np.zeros(len(theta),complex);s2=s1.copy()
 for j in range(ns):
  nn=j+1;tau=nn*mu*pi1-(nn+1)*pi0;fac=(2*nn+1)/(nn*(nn+1));s1+=fac*(a[j]*pi1+b[j]*tau);s2+=fac*(a[j]*tau+b[j]*pi1);p2=((2*nn+1)*mu*pi1-(nn+1)*pi0)/nn;pi0,pi1=pi1,p2
 qe=2/x**2*np.sum((2*n+1)*np.real(a+b));qs=2/x**2*np.sum((2*n+1)*(abs(a)**2+abs(b)**2));return (abs(s1)**2+abs(s2)**2)/2,float(qe),float(qs)
@lru_cache(maxsize=32)
def optical_kernel(n635,k635,n830,k830,medium,model):
 K=np.zeros((71,len(DIAM)));ext=np.zeros((2,len(DIAM)))
 for it,wl in enumerate([635e-9,830e-9]):
  sel=WL==wl;th=np.radians(THETA[sel]);wave=2*math.pi*medium/wl;m=complex(n635,k635)/medium if it==0 else complex(n830,k830)/medium
  for j,d in enumerate(DIAM):
   x=wave*d/2
   if model=='fraunhofer' or x>180:
    u=x*np.sin(th);f=2*j1(u)/np.maximum(u,1e-15);intensity=x**4*f*f/4;qe=2.
   else:intensity,qe,_=mie_scattering(x,m,th)
   K[sel,j]=intensity/wave**2*6/(math.pi*d**3);ext[it,j]=max(0,qe)*3/(2*d)
 K.setflags(write=False);ex=ext.mean(axis=0);ex.setflags(write=False);return K,ex

def quantiles(p,scale=1.):
 p=np.maximum(np.asarray(p),0);p=p/p.sum();cdf=np.r_[0.,np.cumsum(p)];return [float(np.exp(np.interp(q,cdf,np.log(EDGES)))*scale) for q in [.1,.5,.9]]
def invert(signal,method,calibration=1.):
 y=np.asarray(signal,float)
 if y.shape!=(71,) or not np.all(np.isfinite(y)) or max(y)<=0:raise ActionRejected('INVALID_SIGNAL')
 K,_=optical_kernel(method['n635'],method['k635'],method['n830'],method['k830'],method['medium_n'],method['model']);scale=max(y);y=y/scale;weights=1/np.sqrt(np.maximum(y,1e-4));M=K*weights[:,None];norms=np.linalg.norm(M,axis=0);M=M/norms;target=y*weights
 # Nonnegative inverse uses signal only; no source distribution/quantiles enter the solution.
 lam=method['regularization'];smooth=np.diff(np.eye(len(DIAM)),2,axis=0);B=np.vstack([M,lam*smooth]);v=nnls(B,np.r_[target,np.zeros(len(DIAM)-2)],maxiter=10000)[0]/norms
 if v.sum()<=0:raise ActionRejected('INVERSION_FAILED')
 fitted=K@v;res=float(np.linalg.norm((fitted-y)*weights)/max(np.linalg.norm(y*weights),1e-15));p=v/v.sum();d10,d50,d90=quantiles(p,calibration)
 return dict(diameter_m=(DIAM*calibration).tolist(),bin_edges_m=(EDGES*calibration).tolist(),volume_fraction=p.tolist(),cumulative_volume=np.cumsum(p).tolist(),d10_m=d10,d50_m=d50,d90_m=d90,span=(d90-d10)/d50,relative_fit_residual=res,reconstructed_signal=(fitted*scale).tolist())
def template(k,accept_cross_method,accept_truncation,mode):
 if k not in LIB['substances']:raise ActionRejected('UNKNOWN_REFERENCE')
 r=LIB['substances'][k]
 if not r['reference_eligible']:raise ActionRejected('INVALID_REFERENCE')
 if r['cross_method'] and not accept_cross_method:raise ActionRejected('CROSS_METHOD_NOT_ACCEPTED')
 if r['diameter_m'] is not None:
  d=np.asarray(r['diameter_m']);v=np.asarray(r['volume_fraction']);se=np.exp(np.r_[np.log(d[0])-(np.log(d[1])-np.log(d[0]))/2,(np.log(d[:-1])+np.log(d[1:]))/2,np.log(d[-1])+(np.log(d[-1])-np.log(d[-2]))/2]);c=np.r_[0,np.cumsum(v/v.sum())];f=np.interp(np.log(EDGES),np.log(se),c);evidence='derived from measured distribution; modeled scattering'
 else:
  q=r['quantiles_m'];d=np.array([q['d10']**2/q['d50'],q['d10'],q['d50'],q['d90'],q['d90']**2/q['d50']]);f=np.interp(np.log(EDGES),np.log(d),[0,.1,.5,.9,1]);evidence='assumed piecewise log-quantile distribution from measured scalar values; modeled scattering'
 p=np.diff(f);outside=1-p.sum()
 if mode=='dry':outside+=p[DIAM<.1e-6].sum();p[DIAM<.1e-6]=0
 if outside>1e-6 and not accept_truncation:raise ActionRejected('SIZE_RANGE')
 if p.sum()<=0:raise ActionRejected('SIZE_RANGE')
 return p/p.sum(),max(0,float(outside)),evidence,r['source']
PLANT=obj({'ambient_k':number('K',288.15,303.15),'liquid_density_kg_m3':number('kg/m^3',500,2000),'liquid_cp_j_kg_k':number('J/(kg*K)',500,5000),'liquid_viscosity_pa_s':number('Pa*s',1e-5,1.),'medium_n':number('1',1.,2.),'n635':number('1',1.,5.),'k635':number('1',0.,5.),'n830':number('1',1.,5.),'k830':number('1',0.,5.),'pump_full_m3_s':number('m^3/s',1e-7,1e-4),'drain_m3_s':number('m^3/s',1e-7,1e-4),'air_full_std_m3_s':number('m^3/s',1e-6,.01),'vacuum_m3_s':number('m^3/s',.015,.05),'dry_feed_kg_s':number('kg/s',1e-5,.02),'wet_path_m':number('m',.0001,.01),'dry_path_m':number('m',.0001,.01),'ultrasound_absorbed_fraction':number('1',0,1),'deagglomeration_kg_j':number('kg/J',0,.1),'settling_factor':number('1',0,1),'heat_loss_w_k':number('W/K',0,10),'detector_gain':number('1',.5,1.5),'detector_background':number('1',0,.01),'noise_sd':number('1',0,.01),'accept_engineering_model':BOOL})
PLANT_EXAMPLE=dict(ambient_k=293.15,liquid_density_kg_m3=998.20715,liquid_cp_j_kg_k=4184.0509,liquid_viscosity_pa_s=.0010015961,medium_n=1.33,n635=2.9,k635=3.4,n830=2.9,k830=3.1,pump_full_m3_s=2e-5,drain_m3_s=2e-5,air_full_std_m3_s=.001,vacuum_m3_s=.015,dry_feed_kg_s=.001,wet_path_m=.001,dry_path_m=.002,ultrasound_absorbed_fraction=.6,deagglomeration_kg_j=.0001,settling_factor=0.,heat_loss_w_k=.3,detector_gain=1.,detector_background=.000001,noise_sd=0.,accept_engineering_model=True)
METHOD=obj({'mode':enum(['wet','dry']),'model':enum(['mie_hybrid','fraunhofer']),'n635':number('1',1.,5.),'k635':number('1',0.,5.),'n830':number('1',1.,5.),'k830':number('1',0.,5.),'medium_n':number('1',1.,2.),'pump_level':INT(0,4),'stirrer_level':INT(0,4),'ultrasound_enabled':BOOL,'air_pressure_pa':number('Pa',0,4e5),'feed_fraction':number('1',0,1),'dispersion_s':number('s',0,600),'acquisition_s':number('s',.1,59.9),'regularization':number('1',0,.1),'timeout_s':number('s',1,7200)})
METHOD_EXAMPLE=dict(mode='wet',model='mie_hybrid',n635=2.9,k635=3.4,n830=2.9,k830=3.1,medium_n=1.33,pump_level=4,stirrer_level=4,ultrasound_enabled=True,air_pressure_pa=2e5,feed_fraction=.4,dispersion_s=10.,acquisition_s=5.,regularization=.0001,timeout_s=300.)
SAMPLE=obj({'substance_id':TEXT,'mass_kg':number('kg',1e-8,.3),'true_density_kg_m3':number('kg/m^3',100,25000),'agglomerated_fraction':number('1',0,1),'accept_cross_method':BOOL,'accept_range_truncation':BOOL,'accept_quantile_reconstruction':BOOL})
SAMPLE_EXAMPLE=dict(substance_id=SID,mass_kg=.008,true_density_kg_m3=7837.3,agglomerated_fraction=0.,accept_cross_method=False,accept_range_truncation=False,accept_quantile_reconstruction=True)
LEDGER=obj({k:number(u) for k,u in [('solid_loaded_kg','kg'),('solid_instrument_kg','kg'),('solid_waste_kg','kg'),('solid_returned_kg','kg'),('solid_error_kg','kg'),('liquid_loaded_kg','kg'),('liquid_present_kg','kg'),('liquid_waste_kg','kg'),('liquid_error_kg','kg'),('air_loaded_mol','mol'),('air_stock_mol','mol'),('air_used_mol','mol'),('air_error_mol','mol'),('heat_input_j','J'),('heat_out_j','J'),('heat_storage_j','J'),('heat_error_j','J')]})
PSD=obj({'diameter_m':arr(number('m',0)),'bin_edges_m':arr(number('m',0)),'volume_fraction':arr(number('1',0,1)),'cumulative_volume':arr(number('1',0,1.000001)),'d10_m':number('m',0),'d50_m':number('m',0),'d90_m':number('m',0),'span':number('1',0),'relative_fit_residual':number('1',0),'reconstructed_signal':arr(number('1',0),71)})
RESULT=obj({'phase':PHASE,'stage':TEXT,'operation_id':nullable(TEXT),'signal':arr(number('1'),71),'angles_deg':arr(number('deg'),71),'wavelength_m':arr(number('m'),71),'psd':nullable(PSD),'elapsed_s':number('s',0),'obscuration':number('1',0,1),'template_source':nullable(TEXT),'evidence':nullable(TEXT),'excluded_source_volume_fraction':number('1',0,1),'error_code':nullable(TEXT)})
CAPS={};SLOTS={};SLOT_ZH={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},effects or [])
def slot(k,s,u,z,o='derived',v='agent',c='controller',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller'],'显式配置宿主真实光学、流体、热学和噪声参数','configuration','evaluator',PLANT_EXAMPLE,['plant.configured'])
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_switch'],'主电源和60秒激光预热',ex={'enabled':True},effects=['power.enabled','power.angle_rad'])
for k,arg,unit,hi,component,label in [('cover.set','angle_rad','rad',1.2,'service_cover','停机检修主罩'),('tank_lid.set','slide_m','m',.17,'tank_lid','手动湿法槽滑盖'),('dry_hood.set','lift_m','m',.20,'dry_hood','手动干粉罩升降'),('distributor.set','angle_rad','rad',math.pi,'distributor_ring','手动物料分配环')]:cap(k,obj({arg:number(unit,0,hi)}),obj({arg:number(unit,0,hi)}),[component],label,ex={arg:hi},effects=[k.split('.')[0]+'.'+arg])
cap('ultrasound.select',obj({'power_w':dict(number('W'),enum=[20.,35.,50.])}),obj({'power_w':number('W')}),['ultrasound_knob'],'停机开罩设定20/35/50W超声档位',ex={'power_w':50.},effects=['ultrasound.power_w','ultrasound.knob_rad'])
cap('liquid.fill',obj({'volume_m3':number('m^3',1e-6,.00045)}),obj({'volume_m3':number('m^3')}),['wet_tank'],'从宿主供体转入有限载液','configuration','evaluator',{'volume_m3':.00045},['liquid.volume_m3'])
cap('air.add',obj({'amount_mol':number('mol',.001,100)}),obj({'stock_mol':number('mol')}),['air_in'],'宿主接入有限干燥压缩空气量','configuration','evaluator',{'amount_mol':2.},['air.stock_mol'])
cap('method.configure',METHOD,obj({'configured':BOOL}),['controller'],'设置干湿分散、泵速、搅拌、折射率和采集；不推进物理',ex=METHOD_EXAMPLE,effects=['method.mode'])
cap('sample.load',SAMPLE,obj({'accepted_mass_kg':number('kg',0)}),['wet_tank','hopper'],'转入有限真实参考粉体；异源输入和重建须显式接受','configuration','evaluator',SAMPLE_EXAMPLE,['truth.solid_kg'])
cap('sample.recover',obj({}),obj({'returned_mass_kg':number('kg',0)}),['hopper'],'停机从打开的干粉罩回收剩余料斗粉体；返回真实质量限宿主评估器',vis='evaluator',effects=['truth.solid_kg'])
for k,z,c in [('background.start','采集60秒空白',['detector']),('acquisition.start','分散并采集角散射信号，独立反演体积PSD',['laser_1','laser_2','laser_3','detector','flow_cell']),('liquid.drain','异步排出有限湿法载液和粉体',['drain_pump'])]:cap(k,obj({}),obj({'operation_id':TEXT}),c,z,effects=['acquisition.phase','acquisition.stage'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['controller'],'取消并保留已消耗物料、温升和已采信号',effects=['acquisition.phase'])
cap('acquisition.result',obj({}),RESULT,['controller'],'读取纯函数结果','query')
cap('calibration.size',obj({'reference_m':number('m',.04e-6,.0025),'observed_m':number('m',.04e-6,.0025)}),obj({'factor':number('1')}),['detector'],'使用独立外部标准和观测输入校准粒径尺度',ex={'reference_m':50e-6,'observed_m':49e-6},effects=['calibration.size_factor'])
cap('ledger.read',obj({}),LEDGER,['controller'],'评估器读取质量、气量和液体热账本','query','evaluator')
cap('sim.step',obj({'dt_s':number('s',0,7200)}),obj({'time_s':number('s',0)}),['controller'],'宿主唯一物理推进入口','simulation','evaluator',{'dt_s':.1},['clock.time_s'])
UNSUPPORTED=['analysis.mie_oem','analysis.freefall_camera','accessory.svu','accessory.autosampler','hardware.connect','robot.sample','fault.probability','accuracy.empirical']
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'未实现或未取得独立验证：'+k)
for k,s,u,z,o,v,c,w in [
 ('plant.configured',BOOL,'1','宿主模型已配置','derived','agent','controller',None),('power.enabled',BOOL,'1','交流电源已启用','setpoint','agent','power_switch',('power.set','enabled')),('power.angle_rad',number('rad'),'rad','电源摇臂位置','scene','agent','power_switch',None),('power.lamp',BOOL,'1','左侧电源指示灯','derived','agent','lamp_power',None),('laser.warmup_s',number('s'),'s','激光累计预热时间，60秒就绪','derived','agent','laser_1',None),('clock.time_s',number('s'),'s','宿主仿真时钟','derived','agent','controller',None),('cover.angle_rad',number('rad'),'rad','主罩绕后铰链负X方向角度','scene','agent','service_cover',('cover.set','angle_rad')),('tank_lid.slide_m',number('m'),'m','湿法滑盖沿+Y位移','scene','agent','tank_lid',('tank_lid.set','slide_m')),('dry_hood.lift_m',number('m'),'m','干法罩手动+Z提升','scene','agent','dry_hood',('dry_hood.set','lift_m')),('distributor.angle_rad',number('rad'),'rad','分配环转角代理，不代表实测给料标定','scene','agent','distributor_ring',('distributor.set','angle_rad')),('ultrasound.power_w',number('W'),'W','手动超声电功率档位，非吸收热功率','setpoint','agent','ultrasound_knob',('ultrasound.select','power_w')),('ultrasound.knob_rad',number('rad'),'rad','三档超声旋钮角度','scene','agent','ultrasound_knob',None),('circulation_pump.angle_rad',number('rad'),'rad','循环泵转子演示角度','scene','agent','circulation_pump',None),('drain_pump.angle_rad',number('rad'),'rad','排液泵转子演示角度','scene','agent','drain_pump',None),('stirrer.angle_rad',number('rad'),'rad','槽内搅拌轴角度','scene','agent','stirrer',None),('method.mode',enum(['wet','dry']),'1','方法选择；物理路径始终共存','setpoint','agent','controller',None),('liquid.volume_m3',number('m^3'),'m^3','有限载液体积过程真值','process_truth','evaluator','wet_tank',None),('liquid.level_ok',BOOL,'1','按450mL工作容量的液位逻辑代理','derived','agent','level_sensor',None),('air.stock_mol',number('mol'),'mol','有限供气剩余量真值','process_truth','evaluator','air_in',None),('truth.solid_kg',number('kg'),'kg','仪器内悬浮沉降及料斗粉体总质量','process_truth','evaluator','wet_tank',None),('truth.temperature_k',number('K'),'K','等效载液温度，无虚构面板温度传感器','process_truth','evaluator','wet_tank',None),('truth.agglomerated_fraction',number('1'),'1','工程去团聚比例，不是传感器','process_truth','evaluator','wet_tank',None),('calibration.size_factor',number('1'),'1','外部标准参考尺寸/实际观测尺寸','setpoint','agent','detector',None),('background.ready',BOOL,'1','当前干湿介质配置的空白已完成','derived','agent','detector',None),('acquisition.phase',PHASE,'1','采集异步状态','derived','agent','controller',None),('acquisition.stage',TEXT,'1','背景、分散、测量或排液阶段','derived','agent','controller',None),('acquisition.elapsed_s',number('s'),'s','当前操作持续时间','derived','agent','controller',None),('optics.obscuration',number('1'),'1','模型透射光衰减；非厂家噪声验证','derived','agent','detector',None),('d10_m',nullable(number('m')),'m','角散射反演体积10%分位直径','derived','agent','controller',None),('d50_m',nullable(number('m')),'m','角散射反演体积50%分位直径','derived','agent','controller',None),('d90_m',nullable(number('m')),'m','角散射反演体积90%分位直径','derived','agent','controller',None)]:slot(k,s,u,z,o,v,c,w)

ASYNC_CAPABILITIES=('background.start','acquisition.start','liquid.drain')
# Conditional synchronous effects include derived observations, while the host clock
# accounts for all actual process changes when sim.step is explicitly invoked.
PHYSICS_EFFECTS=['clock.time_s','laser.warmup_s','circulation_pump.angle_rad','drain_pump.angle_rad','stirrer.angle_rad','liquid.volume_m3','liquid.level_ok','air.stock_mol','truth.solid_kg','truth.temperature_k','truth.agglomerated_fraction','background.ready','acquisition.phase','acquisition.stage','acquisition.elapsed_s','optics.obscuration','d10_m','d50_m','d90_m']
IMMEDIATE_EFFECTS={
 'plant.configure':['plant.configured','truth.temperature_k'],
 'power.set':['power.enabled','power.angle_rad','power.lamp','laser.warmup_s','acquisition.phase'],
 'liquid.fill':['liquid.volume_m3','liquid.level_ok','truth.temperature_k','background.ready'],
 'method.configure':['method.mode','background.ready'],
 'sample.load':['truth.solid_kg','truth.agglomerated_fraction'],
 'sim.step':PHYSICS_EFFECTS,
}
for _capability in ASYNC_CAPABILITIES:IMMEDIATE_EFFECTS[_capability]=['acquisition.phase','acquisition.stage','acquisition.elapsed_s','d10_m','d50_m','d90_m']
STEP_EFFECTS={
 'background.start':['circulation_pump.angle_rad','stirrer.angle_rad','truth.temperature_k','background.ready','acquisition.elapsed_s','acquisition.phase'],
 'acquisition.start':['circulation_pump.angle_rad','stirrer.angle_rad','air.stock_mol','truth.solid_kg','truth.temperature_k','truth.agglomerated_fraction','acquisition.elapsed_s','acquisition.phase','acquisition.stage','optics.obscuration','d10_m','d50_m','d90_m'],
 'liquid.drain':['drain_pump.angle_rad','liquid.volume_m3','liquid.level_ok','truth.solid_kg','truth.temperature_k','background.ready','acquisition.elapsed_s','acquisition.phase'],
 'power.set':['laser.warmup_s','truth.temperature_k'],
}

class AntonpaarPsa1190Runtime:
 def __init__(self,instance_id='psa1190_01'):
  self.instance_id=instance_id;self.plant=None;self.method=copy.deepcopy(METHOD_EXAMPLE);self.power=False;self.time_s=0.;self.revision=0;self._ticks=0;self._remainder=0.;self.warm=0.;self.cover=0.;self.tanklid=0.;self.dryhood=0.;self.ring=0.;self.uspower=20.;self.pumpangle=0.;self.drainangle=0.;self.stirangle=0.;self.liquid=0.;self.liquid_loaded=0.;self.liquid_waste=0.;self.temperature=293.15;self.air=0.;self.air_loaded=0.;self.air_used=0.;self.mass_loaded=0.;self.mass_waste=0.;self.mass_returned=0.;self.wet=np.zeros(len(DIAM));self.settled=self.wet.copy();self.hopper=self.wet.copy();self.rho=1000.;self.agg=0.;self.heat_in=0.;self.heat_out=0.;self.phase='idle';self.stage='idle';self.elapsed=0.;self.stage_elapsed=0.;self.obsc=0.;self.bg_ready=False;self.bg=np.zeros(71);self.signal=np.zeros(71);self.signal_integral=np.zeros(71);self.signal_time=0.;self.psd=None;self.source=None;self.evidence=None;self.outside=0.;self.result_source=None;self.result_evidence=None;self.result_outside=0.;self.error=None;self.factor=1.;self.operation_id=None;self.records={};self._counter=0;self._requests={};self._owners={};self._principals={'agent':'agent','evaluator':'evaluator'};self.rng=np.random.default_rng(118)
 def register_principal(self,principal_id,visibility='agent'):
  if not isinstance(principal_id,str) or not principal_id or visibility not in ['agent','debug','evaluator'] or principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='agent'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return AntonpaarPsa1190Session(self,principal_id)
 def read(self,role='evaluator'):
  d={'plant.configured':self.plant is not None,'power.enabled':self.power,'power.angle_rad':.32 if self.power else 0.,'power.lamp':self.power,'laser.warmup_s':self.warm,'clock.time_s':self.time_s,'cover.angle_rad':self.cover,'tank_lid.slide_m':self.tanklid,'dry_hood.lift_m':self.dryhood,'distributor.angle_rad':self.ring,'ultrasound.power_w':self.uspower,'ultrasound.knob_rad':{20.:0.,35.:math.pi/2,50.:math.pi}[self.uspower],'circulation_pump.angle_rad':self.pumpangle,'drain_pump.angle_rad':self.drainangle,'stirrer.angle_rad':self.stirangle,'method.mode':self.method['mode'],'liquid.volume_m3':self.liquid,'liquid.level_ok':self.liquid>=.00044,'air.stock_mol':self.air,'truth.solid_kg':float(self.wet.sum()+self.settled.sum()+self.hopper.sum()),'truth.temperature_k':self.temperature,'truth.agglomerated_fraction':self.agg,'calibration.size_factor':self.factor,'background.ready':self.bg_ready,'acquisition.phase':self.phase,'acquisition.stage':self.stage,'acquisition.elapsed_s':self.elapsed,'optics.obscuration':self.obsc}
  for k in ['d10_m','d50_m','d90_m']:d[k]=None if self.psd is None else self.psd[k]
  return {k:copy.deepcopy(v) for k,v in d.items() if role!='agent' or SLOTS[k][3]=='agent'}
 def describe(self):return export_antonpaar_psa1190_contract_metadata()
 def _ledger(self):
  p=self.plant or PLANT_EXAMPLE;present=p['liquid_density_kg_m3']*self.liquid;storage=present*p['liquid_cp_j_kg_k']*(self.temperature-p['ambient_k']);solid=float(self.wet.sum()+self.settled.sum()+self.hopper.sum())
  return dict(solid_loaded_kg=self.mass_loaded,solid_instrument_kg=solid,solid_waste_kg=self.mass_waste,solid_returned_kg=self.mass_returned,solid_error_kg=self.mass_loaded-solid-self.mass_waste-self.mass_returned,liquid_loaded_kg=self.liquid_loaded,liquid_present_kg=present,liquid_waste_kg=self.liquid_waste,liquid_error_kg=self.liquid_loaded-present-self.liquid_waste,air_loaded_mol=self.air_loaded,air_stock_mol=self.air,air_used_mol=self.air_used,air_error_mol=self.air_loaded-self.air-self.air_used,heat_input_j=self.heat_in,heat_out_j=self.heat_out,heat_storage_j=storage,heat_error_j=self.heat_in-self.heat_out-storage)
 def _result(self):return dict(phase=self.phase,stage=self.stage,operation_id=self.operation_id,signal=self.signal.tolist(),angles_deg=THETA.tolist(),wavelength_m=WL.tolist(),psd=copy.deepcopy(self.psd),elapsed_s=self.elapsed,obscuration=self.obsc,template_source=self.result_source,evidence=self.result_evidence,excluded_source_volume_fraction=self.result_outside,error_code=self.error)
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _ready(self):
  if not self.power or self.warm<60-1e-9 or self.cover>.01:raise ActionRejected('NOT_READY')
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[k][0],a)
  if k=='sim.step':self.step(a['dt_s']);return {'time_s':self.time_s}
  snapshot=copy.deepcopy(self.__dict__)
  try:r=self._invoke(k,a);validate(CAPS[k][1],r);self.revision+=0 if CAPS[k][4]=='query' else 1;return r
  except Exception:self.__dict__.clear();self.__dict__.update(snapshot);raise
 def _invoke(self,k,a):
  if k=='acquisition.result':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='plant.configure':
   if (self.plant is not None and self.time_s) or self.power or self.mass_loaded or self.liquid_loaded:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_engineering_model']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   self.plant=copy.deepcopy(a);self.temperature=a['ambient_k'];return {'configured':True}
  if self.plant is None:raise ActionRejected('NOT_CONFIGURED')
  if k=='power.set':
   if a['enabled'] and self.cover>.01:raise ActionRejected('COVER_INTERLOCK')
   if not a['enabled'] and self.phase=='running':self._finish('cancelled','POWER_OFF')
   if a['enabled']!=self.power:self.warm=0.
   self.power=a['enabled'];return dict(enabled=self.power)
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self._finish('cancelled',None);return {'phase':self.phase}
  self._idle()
  if k=='cover.set':
   if self.power:raise ActionRejected('POWER_INTERLOCK')
   self.cover=a['angle_rad'];return a
  if k=='tank_lid.set':self.tanklid=a['slide_m'];return a
  if k=='dry_hood.set':self.dryhood=a['lift_m'];return a
  if k=='distributor.set':self.ring=a['angle_rad'];return a
  if k=='ultrasound.select':
   if self.power or self.cover<1.:raise ActionRejected('SERVICE_ACCESS')
   self.uspower=a['power_w'];return a
  if k=='liquid.fill':
   if self.tanklid<.12 or self.method['mode']!='wet':raise ActionRejected('SAMPLE_ACCESS')
   if self.liquid+a['volume_m3']>.00045+1e-12:raise ActionRejected('TANK_CAPACITY')
   v=a['volume_m3'];self.temperature=(self.temperature*self.liquid+self.plant['ambient_k']*v)/(self.liquid+v);self.liquid+=v;self.liquid_loaded+=v*self.plant['liquid_density_kg_m3'];self.bg_ready=False;return dict(volume_m3=self.liquid)
  if k=='air.add':self.air+=a['amount_mol'];self.air_loaded+=a['amount_mol'];return dict(stock_mol=self.air)
  if k=='method.configure':
   if a['mode']!=self.method['mode'] and self.read()['truth.solid_kg']>1e-12:raise ActionRejected('SAMPLE_PRESENT')
   if a['mode']!=self.method['mode'] or a['medium_n']!=self.method['medium_n']:self.bg_ready=False
   self.method=copy.deepcopy(a);return dict(configured=True)
  if k=='sample.load':
   if self.read()['truth.solid_kg']>1e-12:raise ActionRejected('SAMPLE_PRESENT')
   if (self.method['mode']=='wet' and (self.tanklid<.12 or self.liquid<.00044)) or (self.method['mode']=='dry' and self.dryhood<.15):raise ActionRejected('SAMPLE_ACCESS')
   p,lost,evidence,source=template(a['substance_id'],a['accept_cross_method'],a['accept_range_truncation'],self.method['mode']);r=LIB['substances'][a['substance_id']]
   if r['diameter_m'] is None and not a['accept_quantile_reconstruction']:raise ActionRejected('RECONSTRUCTION_NOT_ACCEPTED')
   if self.method['mode']=='dry' and a['mass_kg']<.001:raise ActionRejected('DRY_SAMPLE_QUANTITY')
   if a['agglomerated_fraction']>0 and p[DIAM>DIAM[-1]/2].sum()>1e-8:raise ActionRejected('SIZE_RANGE')
   self.rho=a['true_density_kg_m3'];self.agg=a['agglomerated_fraction'];self.source=source;self.evidence=evidence;self.outside=lost;self.mass_loaded+=a['mass_kg']
   if self.method['mode']=='wet':self.wet=p*a['mass_kg']
   else:self.hopper=p*a['mass_kg']
   return dict(accepted_mass_kg=a['mass_kg'])
  if k=='sample.recover':
   if self.method['mode']!='dry' or self.dryhood<.15 or self.power:raise ActionRejected('SAMPLE_ACCESS')
   m=float(self.hopper.sum());self.mass_returned+=m;self.hopper[:]=0;return dict(returned_mass_kg=m)
  if k=='calibration.size':
   factor=a['reference_m']/a['observed_m']
   if not .5<factor<2.:raise ActionRejected('INVALID_CALIBRATION')
   self.factor=factor;return dict(factor=factor)
  if k in ['background.start','acquisition.start','liquid.drain']:
   self._ready();mode=self.method['mode']
   if self.tanklid>.01 or self.dryhood>.01:raise ActionRejected('CLOSURE_INTERLOCK')
   if mode=='wet' and (self.liquid<.00044 or not self.method['pump_level']):raise ActionRejected('LIQUID_LEVEL')
   if k=='background.start' and self.read()['truth.solid_kg']>1e-12:raise ActionRejected('BLANK_NOT_EMPTY')
   if k=='acquisition.start':
    if not self.bg_ready:raise ActionRejected('NO_BACKGROUND')
    if self.read()['truth.solid_kg']<=0:raise ActionRejected('NO_SAMPLE')
    if mode=='dry' and (self.air<=0 or self.ring<.1 or self.method['air_pressure_pa']<=0 or self.method['feed_fraction']<=0):raise ActionRejected('DRY_FLOW_NOT_READY')
   if k=='liquid.drain' and mode!='wet':raise ActionRejected('MODE_INCOMPATIBLE')
   self.result_source,self.result_evidence,self.result_outside=(self.source,self.evidence,self.outside) if k=='acquisition.start' else (None,None,0.)
   self._counter+=1;self.operation_id=f'{self.instance_id}:{self._counter}';self.phase='running';self.stage={'background.start':'background','acquisition.start':'dispersing','liquid.drain':'draining'}[k];self.elapsed=0.;self.stage_elapsed=0.;self.signal_integral[:]=0.;self.signal_time=0.;self.signal[:]=0.;self.psd=None;self.error=None;return dict(operation_id=self.operation_id)
  raise ActionRejected('UNSUPPORTED')
 def _finish(self,phase,error):
  self.phase=phase;self.error=error
  if self.signal_time>0:self.signal=self.signal_integral/self.signal_time
  self.records[self.operation_id]=copy.deepcopy(self._result())
 def _effective(self,m):
  # Two-diameter agglomerate proxy conserves particle material volume; no new mass created.
  ii=np.clip(np.searchsorted(DIAM,DIAM*2),0,len(DIAM)-1);p=(1-self.agg)*m;np.add.at(p,ii,self.agg*m);return p
 def _signal(self,mass,denominator):
  p=self.plant;m=self.method;medium=p['medium_n'] if m['mode']=='wet' else 1.;K,ext=optical_kernel(p['n635'],p['k635'],p['n830'],p['k830'],medium,'mie_hybrid');concentration=self._effective(mass)/(self.rho*denominator);path=p['wet_path_m'] if m['mode']=='wet' else p['dry_path_m'];tau=float(ext@concentration*path);self.obsc=-math.expm1(-tau);raw=K@concentration*path*math.exp(-tau/2)*p['detector_gain'];raw=raw+ p['detector_background'];raw+=self.rng.normal(0,p['noise_sd']*max(float(raw.max()),1e-12),71);return raw-self.bg
 def step(self,dt_s):
  validate(number('s',0,7200),dt_s);total=self._remainder+dt_s;n=int(math.floor((total+1e-10)/DT));self._remainder=total-n*DT
  for _ in range(n):
   self._ticks+=1;self.time_s=self._ticks*DT;self._tick(DT)
  if n:self.revision+=1
 def _tick(self,dt):
  if self.plant is None:return
  p=self.plant;m=self.method;run=self.phase=='running';self.warm=min(60.,self.warm+dt) if self.power else 0.;wet=m['mode']=='wet';active=run and self.stage in ['dispersing','measuring','background']
  if self.liquid>1e-14:
   ml=self.liquid*p['liquid_density_kg_m3'];C=ml*p['liquid_cp_j_kg_k'];qin=(self.uspower*p['ultrasound_absorbed_fraction'] if active and wet and m['ultrasound_enabled'] and self.stage!='background' else 0.)+(2*m['pump_level']/4 if active and wet else 0.);qloss=p['heat_loss_w_k']*(self.temperature-p['ambient_k']);self.temperature+=(qin-qloss)*dt/C;self.heat_in+=qin*dt;self.heat_out+=qloss*dt
   if self.wet.sum()+self.settled.sum()>0:
    vel=np.clip((self.rho-p['liquid_density_kg_m3'])*9.80665*DIAM**2/(18*p['liquid_viscosity_pa_s']),0,.05);loss=self.wet*(-np.expm1(-vel/.060*dt*p['settling_factor']));gain=self.settled*(-math.expm1(-.1*m['stirrer_level']*dt)) if active and wet else np.zeros(len(DIAM));self.wet+=gain-loss;self.settled+=loss-gain
    if active and wet and m['ultrasound_enabled']:self.agg*=math.exp(-p['deagglomeration_kg_j']*self.uspower*p['ultrasound_absorbed_fraction']*dt/ml)
  if not run:return
  self.elapsed+=dt;self.stage_elapsed+=dt
  if self.elapsed>m['timeout_s']+1e-8:self._finish('failed','TIMEOUT');return
  if self.temperature>313.15:self._finish('failed','LIQUID_TEMPERATURE_LIMIT');return
  if self.stage=='draining':
   dv=min(self.liquid,p['drain_m3_s']*dt);frac=dv/self.liquid if self.liquid else 0.;dm=(self.wet+self.settled)*frac;self.mass_waste+=float(dm.sum());self.wet*=1-frac;self.settled*=1-frac;lm=dv*p['liquid_density_kg_m3'];self.liquid_waste+=lm;self.heat_out+=lm*p['liquid_cp_j_kg_k']*(self.temperature-p['ambient_k']);self.liquid-=dv;self.drainangle=(self.drainangle+4*math.pi*dt)%(2*math.pi);self.bg_ready=False
   if self.liquid<=1e-14:self.liquid=0.;self._finish('succeeded',None)
   return
  if wet:
   self.pumpangle=(self.pumpangle+m['pump_level']/4*4*math.pi*dt)%(2*math.pi);self.stirangle=(self.stirangle+m['stirrer_level']/4*15*math.pi*dt)%(2*math.pi)
  if self.stage=='background':
   self.signal_integral+=np.full(71,p['detector_background'])*dt;self.signal_time+=dt
   if self.stage_elapsed>=60-1e-8:self.bg=self.signal_integral/self.signal_time;self.bg_ready=True;self._finish('succeeded',None)
   return
  dry_consumed=None
  if not wet:
   gas=101325.*p['air_full_std_m3_s']*(m['air_pressure_pa']/4e5)*dt/(RGAS*293.15)
   if self.air+1e-14<gas:self._finish('failed','GAS_EXHAUSTED');return
   self.air-=gas;self.air_used+=gas;stock=float(self.hopper.sum());desired=p['dry_feed_kg_s']*m['feed_fraction']*min(1.,self.ring/.5)*dt
   if stock<=1e-14:self._finish('failed','HOPPER_EMPTY');return
   dry_consumed=self.hopper*(min(stock,desired)/stock);self.hopper-=dry_consumed;self.mass_waste+=float(dry_consumed.sum());self.agg*=math.exp(-.03*(m['air_pressure_pa']/1e5)*dt)
  if self.stage=='dispersing':
   if self.stage_elapsed>=m['dispersion_s']-1e-8:self.stage='measuring';self.stage_elapsed=0.
   return
  if self.stage=='measuring':
   signal=self._signal(self.wet,self.liquid) if wet else self._signal(dry_consumed/dt,p['vacuum_m3_s'])
   if not (1e-6<self.obsc<.3):self._finish('failed','OBSCURATION_RANGE');return
   self.signal_integral+=signal*dt;self.signal_time+=dt
   if self.stage_elapsed>=m['acquisition_s']-1e-8:
    self.signal=self.signal_integral/self.signal_time
    try:self.psd=invert(self.signal,m,self.factor)
    except (ActionRejected,RuntimeError):self._finish('failed','INVERSION_FAILED');return
    self._finish('succeeded',None)

class AntonpaarPsa1190Session:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,i):
  if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  m=export_antonpaar_psa1190_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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
   result=h.invoke(capability_id,**arguments);op=result.get('operation_id') if capability_id in ASYNC_CAPABILITIES else None
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





def export_antonpaar_psa1190_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions='SI; particle diameter not radius; gas flow at101325Pa293.15K; air pressure gauge relative101325Pa',origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'AntonpaarPsa1190Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description='fixed0.1s process integration; pure read'),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNKNOWN_OPERATION','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','INITIALIZATION_ONLY','BUSY','NOT_READY','NOT_RUNNING','NOT_CONFIGURED','UNKNOWN_REFERENCE','MODEL_NOT_ACCEPTED','SAMPLE_ACCESS','COVER_INTERLOCK','CLOSURE_INTERLOCK','POWER_INTERLOCK','SERVICE_ACCESS','TANK_CAPACITY','SAMPLE_PRESENT','INVALID_REFERENCE','CROSS_METHOD_NOT_ACCEPTED','RECONSTRUCTION_NOT_ACCEPTED','DRY_SAMPLE_QUANTITY','INVALID_CALIBRATION','LIQUID_LEVEL','BLANK_NOT_EMPTY','NO_BACKGROUND','NO_SAMPLE','DRY_FLOW_NOT_READY','MODE_INCOMPATIBLE','SIZE_RANGE','INVALID_SIGNAL','INVERSION_FAILED']
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  un=k in UNSUPPORTED;asyn=k in ASYNC_CAPABILITIES;caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if un else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if un else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if un else 'implemented'),binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.antonpaar_psa1190.AntonpaarPsa1190Session.invoke',invocation=f'session.invoke(instance_id="psa1190_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if kind in ['query','analysis'] or un else [dict(description_zh='显式宿主模型；运行互斥、加样访问及主罩/液位/预热/背景检查',state_ids=['power.enabled','plant.configured','acquisition.phase','cover.angle_rad','tank_lid.slide_m','dry_hood.lift_m','background.ready'],enforced_by='runtime',check='atomic invoke; authenticated Session; step-only physics')],state_effects=[dict(state_id=x,timing='immediate',description_zh='受检命令状态') for x in IMMEDIATE_EFFECTS.get(k,effects)],observes=[],model_effects=dict(mode='none',description_zh='需要单独场景验证',binding_ids=[]),execution=dict(mode='asynchronous' if asyn else 'synchronous',time_domain='simulation',completion_criterion='背景60秒/分散后采集/有限排液完成' if asyn else '接受命令，物理step推进',completion_state_ids=['acquisition.phase'] if asyn else [],timeout_s=None,cancellation='supported' if asyn else 'unsupported',retry='same_request_id',resources=['host_clock','finite_sample','liquid','compressed_air','laser','request_cache'],partial_failure_zh='已转移样品、排液、耗气、温升及采集角信号保留；取消不自动补回物质'),errors=[dict(code=x,meaning_zh=x,side_effects='none') for x in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=['实机误差、故障和外部校准的实测验证缺失；例示系数不得视作厂家值'],source_ids=['manufacturer_specs','simulation_assumption'])
  if k in STEP_EFFECTS:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='宿主时钟推进有限物质、热量、转动或光学读数') for x in STEP_EFFECTS[k]]
 mech=dict(component_ids=['controller','laser_1','laser_2','laser_3','detector','wet_tank','flow_cell','hopper','ultrasound','circulation_pump','drain_pump','venturi'],state_ids=list(slots),meaning_zh='干湿有限粉体、样品分散和激光角散射反演',implementation='lumped',equations=['Laser830/635/830nm;71engineering-angle channels over0.015–88deg, exact detector angular layout unknown','Lorenz-Mie sphere coefficients via Riccati-Bessel logarithmic derivative; x=pi*n_medium*d/lambda; dSigma/dOmega=(|S1|²+|S2|²)/(2k²); Kvolume=6*dSigma/(pi*d³)','For size parameter x>180 use opaque-disk Fraunhofer asymptotic and Qext=2; explicit approximation, not fullOEMMie/camera. Optional all-sizeFraunhofer also assumption','Raw input PSD volume conservatively rebinned in logd; scalarD10/D50/D90 only: explicitpiecewise log-CDF reconstructed tails; source curves and synthetic detector signals have distinct evidence','Signal=sum(Kvolume *mass_bin/(rho*V)*path)*exp(-tau/2); tau=sum(3Qext/(2d)*volume_concentration*path); obscuration=1-exp(-tau), single scattering only','Independent weighted NNLS inverse with nonnegative volume distribution; cumulative log-bin quantilesD10/D50/D90; entered optical properties can differ from host truth; scalar external size calibration follows inversion','Wet mass bins suspended and settled; settlingStokes velocity clipped0.05m/s with explicit factor and resuspension. Phenomenological two-diameter agglomerates conserve material; ultrasound first-order decay by depositedJ/kg','Wet450mL finite volume; drain advects powder and stored sensible heat; dryhopper finitefeed goes to waste, regulated airfiniteMol decreases atPV/RT standardflow; externalvacuum15L/s boundary','Effective liquid heat mCp*dT=absorbed_ultrasound +pumpheat -H*(T-Tamb); whole machine and solvent phase change not modeled. NISTwater293.15K rho998.20715,Cp4184.0509,eta.0010015961 supplied explicitly','Only hoststep advances;60s warmup,60s background, separatedispersion/acquisition<60s. Sessionprincipal filtering, request dedupe, owned async records and atomic rejection'],parameters={},source_ids=['manufacturer_specs','manufacturer_manual','soil_data','river_data','pharma_data','steel_data','nist_water','simulation_assumption'],limitations=['232records include118realPSD samples,112QICPICmaterial references and2nativePSAsteelquantiles; sample/grade/mixture counts not chemical species count','SoilREADME number wording conflicts with author primary thesis volume definition; adopt volume, retains original metadata. Soil only53um sieve-under fraction, no reconstructed sand fraction','Pharma14anonymous rows andLidocaine D50>D90 excluded; workbook112 vslanding113 count conflict; QICPIC-to-volume assumption must be explicitly accepted','Actual rawPSA angular data, optical calibration, dark noise distributions, accuracy<1%/repeatability<0.5% and instrumentfault validation not available','Manufacturer2024 approx55kg versus2018L/D63kg conflict: taskspecified55kg used; componentmass unknown and dynamicdistributionengineering','Part masses, inertias, friction, flowpath volume,71individualdetector angles and dryfeed/air/pump/stirrer rate maps unmeasured. All motion targets are process-authoritative proxies','Ultrasound20/35/50W and reference120rpmpump450rpmstirrer documented; fourlevels linearmapping andfluidheatingfraction are assumptions','Solventonlyeffectiveheat; no whole-machine thermal energy, evaporation, dissolution, bubble-optics, non-spherical/MAPS/multiple scattering or truefreefallcamera implementation','Robotcontact, realhardware, laser safetycertification and deployment process/filesystemtruth isolation not_run'])
 spec=importlib.util.spec_from_file_location('_psa1190_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'laser_diffraction':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'。'
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'laser_diffraction':mech})

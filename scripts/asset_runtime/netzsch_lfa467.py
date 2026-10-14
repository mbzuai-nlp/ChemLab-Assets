"""CHEM135 LFA467: finite-energy one-dimensional flash response and observed analysis."""
from pathlib import Path
import copy,json,math,importlib.util,bisect
import numpy as np
from scipy.optimize import brentq,minimize_scalar
PROTOCOL_VERSION='1.0.0';SLUG='netzsch_lfa467';CATALOG_ID='CHEM-135';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch04/t1_thermal'/SLUG
HOST_DT=.1;R_GAS=8.31446261815324
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
 p=ASSET/'lfa_library.json';return json.loads(p.read_text()) if p.exists() else {'substances':{}}

def source_alpha(record,temperature_k):
 if not record or not record.get('runtime_usable'):raise ActionRejected('INVALID_REFERENCE')
 v=record['values'];T=np.asarray(v['temperature_k'],float);a=np.asarray(v['thermal_diffusivity_m2_s'],float)
 if T.ndim!=1 or len(T)<1 or T.shape!=a.shape or not np.all(np.isfinite(T)) or not np.all(np.isfinite(a)) or np.any(a<=0) or np.any(np.diff(T)<=0):raise ActionRejected('INVALID_REFERENCE')
 d=record.get('runtime_domain',{});lo=max(float(T[0]),d.get('minimum_temperature_k',-math.inf));hi=min(float(T[-1]),d.get('maximum_temperature_k',math.inf))
 if not lo<=temperature_k<=hi:raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
 value=float(np.interp(temperature_k,T,a))
 if not 1e-8<=value<=.002:raise ActionRejected('DIFFUSIVITY_RANGE')
 return value

def thermal_modes(thickness_m,area_m2,density_kg_m3,heat_capacity_j_kg_k,diffusivity_m2_s,front_loss_w_m2_k,rear_loss_w_m2_k,cells,furnace_capacity_j_k,ambient_conductance_w_k):
 """Conservative FV slab plus finite furnace; half-cell Robin surface resistances."""
 if min(thickness_m,area_m2,density_kg_m3,heat_capacity_j_kg_k,diffusivity_m2_s,furnace_capacity_j_k)<=0 or min(front_loss_w_m2_k,rear_loss_w_m2_k,ambient_conductance_w_k)<0 or type(cells) is not int or not 4<=cells<=256:raise ActionRejected('THERMAL_DOMAIN')
 n=cells;dx=thickness_m/n;kappa=diffusivity_m2_s*density_kg_m3*heat_capacity_j_kg_k;C=np.r_[furnace_capacity_j_k,np.full(n,density_kg_m3*heat_capacity_j_kg_k*area_m2*dx)];L=np.zeros((n+1,n+1));G=kappa*area_m2/dx
 def edge(i,j,g):L[i,i]+=g;L[j,j]+=g;L[i,j]-=g;L[j,i]-=g
 for i in range(1,n):edge(i,i+1,G)
 front_fraction=1/(1+front_loss_w_m2_k*dx/(2*kappa));rear_fraction=1/(1+rear_loss_w_m2_k*dx/(2*kappa));edge(0,1,front_loss_w_m2_k*area_m2*front_fraction);edge(0,n,rear_loss_w_m2_k*area_m2*rear_fraction);L[0,0]+=ambient_conductance_w_k;sqrtC=np.sqrt(C);B=-L/np.outer(sqrtC,sqrtC)
 # Exact null vectors are fixed by graph connectivity, not an eigenvalue cutoff.
 # Isolated slab and furnace have TWO conserved modes when both faces are adiabatic.
 components=[list(range(n+1))] if front_loss_w_m2_k>0 or rear_loss_w_m2_k>0 else [[0],list(range(1,n+1))]
 vectors=[]
 for component in components:
  if ambient_conductance_w_k>0 and 0 in component:continue
  vector=np.zeros(n+1);vector[component]=sqrtC[component];vector/=np.linalg.norm(vector);vectors.append(vector)
 if vectors:
  null=np.column_stack(vectors);Q=np.linalg.qr(null,mode='complete')[0][:,len(vectors):];reduced=Q.T@B@Q;lam,W=np.linalg.eigh(reduced);V=np.column_stack([Q@W,null]);lam=np.r_[lam,np.zeros(len(vectors))]
 else:lam,V=np.linalg.eigh(B)
 if np.max(lam)>1e-7*max(1.,np.max(abs(lam))):raise ActionRejected('NONPASSIVE_THERMAL_MATRIX')
 lam=np.minimum(lam,0.)
 rear=np.zeros(n+1);rear[0]=1-rear_fraction;rear[-1]=rear_fraction;front=np.zeros(n+1);front[0]=1-front_fraction;front[1]=front_fraction;injection=np.zeros(n+1);injection[0]=1-front_fraction;injection[1]=front_fraction
 return dict(C=C,sqrtC=sqrtC,eigenvalues=lam,eigenvectors=V,rear=rear,front=front,pulse_injection=injection,front_direct_k_w=1/(2*G+front_loss_w_m2_k*area_m2),ambient_conductance_w_k=ambient_conductance_w_k,kappa_w_m_k=kappa)

def modal_kernels(eigenvalues,time_s):
 lam=np.asarray(eigenvalues,float)[:,None];t=np.atleast_1d(np.asarray(time_s,float))[None,:];x=lam*t;E=np.exp(x);small=abs(x)<1e-4;safe=np.where(lam==0,1.,lam)
 phi1=np.where(small,t*(1+x/2+x*x/6+x**3/24+x**4/120),np.expm1(x)/safe)
 phi2=np.where(small,t*t*(.5+x/6+x*x/24+x**3/120+x**4/720),(np.expm1(x)-x)/(safe*safe))
 return E,phi1,phi2

def detector_kernels(eigenvalues,time_s,tau_s):
 lam=np.asarray(eigenvalues,float)[:,None];t=np.atleast_1d(np.asarray(time_s,float))[None,:];r=1/tau_s;u=r*t;E=np.exp(lam*t);decay=np.exp(-u);sumrate=lam+r;z=sumrate*t
 # Divided difference remains finite when a diffusion rate equals detector rate.
 A=np.empty_like(z);near=abs(z)<1e-5;A[near]=(np.broadcast_to(r*t,z.shape)*np.exp(np.broadcast_to(-u,z.shape))*(1+z/2+z*z/6+z**3/24))[near]
 positive=(z>=0)&~near;negative=(z<0)&~near;A[positive]=(r*E*(-np.expm1(-np.maximum(z,0)))/np.where(sumrate==0,1,sumrate))[positive];A[negative]=(r*decay*np.expm1(np.minimum(z,0))/np.where(sumrate==0,1,sumrate))[negative]
 one=-np.expm1(-u);safe=np.where(lam==0,1.,lam);B=(A-one)/safe;small=abs(lam*t)<1e-4
 # Integral Ik=integral0^t r exp[-r(t-s)] s^k ds; series avoids small-r*t cancellation.
 integrals=[];prev=one
 for k in [1,2,3]:
  ordinary=t**k-k/r*prev;series=np.zeros_like(t)
  for j in range(20):series+=(-u)**j*math.factorial(k)/math.factorial(k+j+1)
  cur=np.where(u<1.,r*t**(k+1)*series,ordinary);integrals.append(cur);prev=cur
 approx=integrals[0]+lam*integrals[1]/2+lam*lam*integrals[2]/6;B=np.where(small,approx,B)
 return A,B,decay[0],one[0]

def propagate_modes(model,temperature_delta_k,heater_w,absorbed_pulse_w,dt_s,detector_delta_k=0.,detector_tau_s=1e-6,detector_gain=1.,detector_offset_k=0.,sample_times_s=None):
 """Exact constant-power propagation; observations can be sampled inside a host interval."""
 if dt_s<0 or detector_tau_s<=0:raise ActionRejected('THERMAL_DOMAIN')
 V=model['eigenvectors'];lam=model['eigenvalues'];Croot=model['sqrtC'];q=np.asarray(temperature_delta_k,float);source=model['pulse_injection']*absorbed_pulse_w;source=source.copy();source[0]+=heater_w;a=V.T@(Croot*q);b=V.T@(source/Croot);E,p1,p2=modal_kernels(lam,[dt_s]);coeff=a*E[:,0]+b*p1[:,0];new=V@coeff/Croot;integral=V@(a*p1[:,0]+b*p2[:,0])/Croot
 times=np.asarray([dt_s] if sample_times_s is None else sample_times_s,float)
 if times.ndim!=1 or np.any(times<0) or np.any(times>dt_s+1e-12):raise ActionRejected('SAMPLE_TIME_RANGE')
 Ee,p11,_=modal_kernels(lam,times);c=(model['rear']/Croot)@V;rear=c@(a[:,None]*Ee+b[:,None]*p11);A,B,decay,one=detector_kernels(lam,times,detector_tau_s);sensor=detector_delta_k*decay+detector_offset_k*one+detector_gain*(c@(a[:,None]*A+b[:,None]*B))
 AA,BB,dd,oo=detector_kernels(lam,[dt_s],detector_tau_s);last=float(detector_delta_k*dd[0]+detector_offset_k*oo[0]+detector_gain*(c@(a*AA[:,0]+b*BB[:,0])))
 return dict(temperature_delta_k=new,temperature_integral_k_s=integral,detector_delta_k=last,rear_delta_k=rear,observed_delta_k=sensor,ambient_j=float(model['ambient_conductance_w_k']*integral[0]),heat_input_j=float((heater_w+absorbed_pulse_w)*dt_s),stored_change_j=float(model['C']@(new-q)))

def parker_response(fourier_number):
 x=np.asarray(fourier_number,float);flat=x.ravel();out=np.zeros_like(flat)
 for j,v in enumerate(flat):
  if v<=0:continue
  if v<.07:out[j]=2/math.sqrt(math.pi*v)*sum(math.exp(-(2*m+1)**2/(4*v)) for m in range(12))
  else:out[j]=1+2*sum((-1)**n*math.exp(-n*n*math.pi**2*v) for n in range(1,60))
 return out.reshape(x.shape)
PARKER_HALF=float(brentq(lambda x:float(parker_response(x))-.5,.05,.3,xtol=1e-15))

def analyze_parker(time_s,temperature_k,thickness_m,pulse_time_s,baseline_k,plateau_k,accept_adiabatic_instant_pulse):
 if not accept_adiabatic_instant_pulse:raise ActionRejected('ANALYSIS_ASSUMPTION_NOT_ACCEPTED')
 t=np.asarray(time_s,float)
 if len(t)!=len(temperature_k) or len(t)<3 or not np.all(np.isfinite(t)) or np.any(np.diff(t)<=0) or thickness_m<=0 or plateau_k<=baseline_k:raise ActionRejected('ANALYSIS_DATA')
 target=(baseline_k+plateau_k)/2;cross=None;previous=None
 for ti,yi in zip(t,temperature_k):
  if yi is None:previous=None;continue
  if not math.isfinite(yi):raise ActionRejected('ANALYSIS_DATA')
  if ti<pulse_time_s:previous=None;continue
  if previous is None:
   if yi>=target:return dict(diffusivity_m2_s=None,half_time_s=None,bracketed=False,half_fourier_number=PARKER_HALF,evidence='First valid postpulse observation already exceeds half-rise: left-censored, no extrapolation.')
  elif previous[1]<target<=yi:
   cross=float(previous[0]+(ti-previous[0])*(target-previous[1])/(yi-previous[1])-pulse_time_s);break
  previous=(ti,yi)
 return dict(diffusivity_m2_s=PARKER_HALF*thickness_m**2/cross if cross is not None and cross>0 else None,half_time_s=cross,bracketed=cross is not None and cross>0,half_fourier_number=PARKER_HALF,evidence='External observed half-rise bracket and supplied baseline/plateau/thickness. Numerically solved ideal Parker normalized-response half-time; finite pulse, heat loss and detector lag are NOT corrected by this estimator, and no hidden source alpha is read.')
def finite_profile(time_s,diffusivity_m2_s,thickness_m,volumetric_capacity_j_m3_k,front_loss_w_m2_k,rear_loss_w_m2_k,pulse_width_s,detector_tau_s,cells=64):
 """External-analysis fixed-baseline Robin slab, unit incident energy per area."""
 t=np.asarray(time_s,float);m=thermal_modes(thickness_m,1.,volumetric_capacity_j_m3_k,1.,diffusivity_m2_s,front_loss_w_m2_k,rear_loss_w_m2_k,cells,1e30,0.);q=np.zeros(cells+1);out=np.zeros_like(t);pulse=propagate_modes(m,q,0.,1/pulse_width_s,pulse_width_s,detector_tau_s=detector_tau_s)
 during=(t>=0)&(t<=pulse_width_s);after=t>pulse_width_s
 if np.any(during):out[during]=propagate_modes(m,q,0.,1/pulse_width_s,pulse_width_s,detector_tau_s=detector_tau_s,sample_times_s=t[during])['observed_delta_k']
 if np.any(after):out[after]=propagate_modes(m,pulse['temperature_delta_k'],0.,0.,float(np.max(t[after])-pulse_width_s),detector_delta_k=pulse['detector_delta_k'],detector_tau_s=detector_tau_s,sample_times_s=t[after]-pulse_width_s)['observed_delta_k']
 return out

def fit_diffusivity(time_s,temperature_k,thickness_m,pulse_time_s,pulse_width_s,volumetric_capacity_j_m3_k,front_loss_w_m2_k,rear_loss_w_m2_k,detector_tau_s,minimum_diffusivity_m2_s,maximum_diffusivity_m2_s,cells,accept_homogeneous_robin_model):
 if not accept_homogeneous_robin_model:raise ActionRejected('ANALYSIS_ASSUMPTION_NOT_ACCEPTED')
 t=np.asarray(time_s,float);y=np.asarray(temperature_k,float)
 if t.ndim!=1 or t.shape!=y.shape or len(t)<20 or np.any(np.diff(t)<=0) or not np.all(np.isfinite(t)) or not np.all(np.isfinite(y)) or np.ptp(y)<1e-9 or t[-1]<=pulse_time_s+pulse_width_s or minimum_diffusivity_m2_s>=maximum_diffusivity_m2_s:raise ActionRejected('ANALYSIS_DATA')
 index=np.unique(np.linspace(0,len(t)-1,min(len(t),1200)).astype(int));t=t[index]-pulse_time_s;y=y[index];scale=max(np.ptp(y),1e-9);tn=(t-np.mean(t))/max(np.ptp(t),1e-12);cache={}
 def evaluate(logalpha):
  alpha=math.exp(float(logalpha));curve=finite_profile(t,alpha,thickness_m,volumetric_capacity_j_m3_k,front_loss_w_m2_k,rear_loss_w_m2_k,pulse_width_s,detector_tau_s,cells);norm=float(np.max(abs(curve)))
  if norm<1e-20:return 1e100,None
  X=np.column_stack([np.ones(len(t)),tn,curve/norm]);coef=np.linalg.lstsq(X,y,rcond=None)[0];res=y-X@coef;error=float(np.mean((res/scale)**2)) if coef[2]>0 else 1e100;return error,(coef,res)
 lo=math.log(minimum_diffusivity_m2_s);hi=math.log(maximum_diffusivity_m2_s);grid=np.linspace(lo,hi,21);errs=[evaluate(x)[0] for x in grid];best=int(np.argmin(errs));bounds=(grid[max(0,best-1)],grid[min(len(grid)-1,best+1)]);fit=minimize_scalar(lambda z:evaluate(z)[0],bounds=bounds,method='bounded',options={'xatol':1e-10});error,data=evaluate(fit.x)
 if not fit.success or data is None or not math.isfinite(error) or fit.x-lo<1e-4 or hi-fit.x<1e-4:raise ActionRejected('ANALYSIS_UNRESOLVED')
 coef,res=data
 return dict(diffusivity_m2_s=float(math.exp(fit.x)),fit_rms_k=float(np.sqrt(np.mean(res*res))),fitted_rise_amplitude_k=float(coef[2]),baseline_at_window_center_k=float(coef[0]),baseline_slope_k_s=float(coef[1]/np.ptp(t)),points_used=len(t),cells=cells,evidence='Fit external observed thermal trace with homogeneous1D Robin half-cell FV model and explicitly supplied pulse width, detector time constant, volumetric capacity and face heat losses. Alpha optimized from signal; amplitude/offset/linear baseline nuisance terms fitted independently. Fixed-temperature surroundings are an analysis approximation; no host/material true alpha is read. Uniform index selection limits fit to1200 observed nodes and is reported. Not proprietary Proteus/Cowan/Cape-Lehman/pulse-mapping certification.')

PLANT_EXAMPLE=dict(accept_engineering=True,ambient_k=298.15,cooler='none',detector='MCT',furnace_capacity_j_k=20.,ambient_conductance_w_k=.1,forced_cooling_conductance_w_k=.3,controller_gain_w_k=30.,heater_max_w=100.,cooler_max_w=50.,safe_access_temperature_k=313.15,detector_tau_s=1e-6,detector_gain=1.,detector_offset_k=0.,detector_quantum_k=1e-7,detector_minimum_k=123.15,detector_maximum_k=1000.,pulse_monitor_gain=1.,pulse_monitor_offset_w=0.,detector_cooling_w=2.,coolant_latent_j_kg=199000.,coolant_efficiency=.7,furnace_speed_m_s=.02,sampler_speed_m_s=.02,maximum_local_temperature_rise_k=100.,cells=64)
PLANT=obj({'accept_engineering':BOOL,'ambient_k':number('K',283.15,313.15),'cooler':enum(['none','LN2']),'detector':enum(['InSb','MCT']),'furnace_capacity_j_k':number('J/K',.01,1e4),'ambient_conductance_w_k':number('W/K',0,1000),'forced_cooling_conductance_w_k':number('W/K',0,1000),'controller_gain_w_k':number('W/K',.001,1e5),'heater_max_w':number('W',.001,1e5),'cooler_max_w':number('W',0,1e5),'safe_access_temperature_k':number('K',283.15,373.15),'detector_tau_s':number('s',1e-9,10),'detector_gain':number('1',.01,100),'detector_offset_k':number('K',-100,100),'detector_quantum_k':number('K',1e-10,1.),'detector_minimum_k':number('K',1,2000),'detector_maximum_k':number('K',1,2000),'pulse_monitor_gain':number('1',.01,100),'pulse_monitor_offset_w':number('W',-1e5,1e5),'detector_cooling_w':number('W',1e-6,1000),'coolant_latent_j_kg':number('J/kg',1,1e7),'coolant_efficiency':number('1',.01,1),'furnace_speed_m_s':number('m/s',.001,.1),'sampler_speed_m_s':number('m/s',.001,.1),'maximum_local_temperature_rise_k':number('K',.01,500),'cells':integer(16,128)})
SAMPLE_EXAMPLE=dict(reference_id='nist_sic',position=1,thickness_m=.002,diameter_m=.0127,density_kg_m3=3160.,heat_capacity_j_kg_k=715.,absorptance=.8,front_loss_w_m2_k=10.,rear_loss_w_m2_k=10.,accept_homogeneous_opaque=True,accept_condition_transfer=True)
SAMPLE=obj({'reference_id':TEXT,'position':integer(1,16),'thickness_m':number('m',1e-5,.006),'diameter_m':number('m',.006,.0127),'density_kg_m3':number('kg/m^3',10,50000),'heat_capacity_j_kg_k':number('J/(kg*K)',1,1e5),'absorptance':number('1',.001,1.),'front_loss_w_m2_k':number('W/(m^2*K)',0,1e5),'rear_loss_w_m2_k':number('W/(m^2*K)',0,1e5),'accept_homogeneous_opaque':BOOL,'accept_condition_transfer':BOOL})
GAS_EXAMPLE=dict(channel='purge1',gas='N2',amount_mol=1.,reference_temperature_k=298.15,reference_pressure_pa=101325.,accept_reference_conditions=True)
CHANNELS=['purge1','purge2','protective'];GAS=obj({'channel':enum(CHANNELS),'gas':enum(['N2','Ar','He','air']),'amount_mol':number('mol',1e-18,10000),'reference_temperature_k':number('K',200,500),'reference_pressure_pa':number('Pa',1e4,1e7),'accept_reference_conditions':BOOL})
COOLANT_EXAMPLE=dict(reservoir='detector',amount_kg=.1,accept_engineering_latent=True);COOLANT=obj({'reservoir':enum(['detector','furnace']),'amount_kg':number('kg',1e-12,100),'accept_engineering_latent':BOOL})
CALIBRATION_EXAMPLE=dict(temperature_gain_correction=1.,temperature_zero_k=0.,pulse_gain_correction=1.,pulse_zero_w=0.)
CALIBRATION=obj({'temperature_gain_correction':number('1',.01,100),'temperature_zero_k':number('K',-100,100),'pulse_gain_correction':number('1',.01,100),'pulse_zero_w':number('W',-1e5,1e5)})
METHOD_EXAMPLE=dict(temperature_k=298.15,rate_k_s=50/60,equilibrium_tolerance_k=.05,equilibrium_dwell_s=.2,pretrigger_s=.001,pulse_energy_j=.1,pulse_width_s=.0001,sample_interval_s=.0001,record_duration_s=.25,timeout_s=1000.,accept_condition_transfer=True)
METHOD=obj({'temperature_k':number('K',173.15,773.15),'rate_k_s':number('K/s',.001,50/60),'equilibrium_tolerance_k':number('K',1e-5,5.),'equilibrium_dwell_s':number('s',0,1000),'pretrigger_s':number('s',0,1.),'pulse_energy_j':number('J',1e-8,10.),'pulse_width_s':number('s',10e-6,1500e-6),'sample_interval_s':number('s',.5e-6,.1),'record_duration_s':number('s',.001,120.),'timeout_s':number('s',.001,1e6),'accept_condition_transfer':BOOL})
ROW=obj({'time_s':number('s',0),'pulse_elapsed_s':number('s'),'temperature_k':nullable(number('K')),'pulse_power_w':nullable(number('W'))})
RESULT=obj({'operation_id':nullable(TEXT),'phase':PHASE,'elapsed_s':number('s',0),'stage':enum(['idle','equilibrating','baseline','pulse','recording','complete']),'pulse_time_s':nullable(number('s',0)),'rows':array(ROW,0,200001),'reference_id':nullable(TEXT),'template_source':nullable(TEXT),'method_json':nullable(TEXT),'calibration_json':nullable(TEXT),'evidence':TEXT,'error_code':nullable(TEXT)})
CAPS={};SLOTS={}
def cap(k,i,o,c,z,kind='device_command',visibility='agent',example=None):CAPS[k]=dict(input_schema=i,output_schema=o,component_ids=c,function_zh=z,kind=kind,visibility=visibility,example_arguments=copy.deepcopy(example or {}),effects=[])
def slot(k,s,u,z,origin='derived',visibility='agent',component='controller'):SLOTS[k]=dict(schema=s,unit=u,meaning=z,origin=origin,visibility=visibility,component=component,write=None)
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller','furnace','ir_detector'],'显式热/IR/冷量/运动工程参数','configuration','evaluator',PLANT_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['specimen','sample_holder'],'单实际圆片、真实alpha参考和独立ρCp/吸收损失','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.remove',obj({}),obj({'removed':BOOL}),['specimen'],'冷态回收试片和残余显热','configuration','evaluator')
cap('gas.load',GAS,obj({'loaded':BOOL}),['controller'],'三路独立有限气源','configuration','evaluator',GAS_EXAMPLE)
cap('gas.unload',obj({'channel':enum(CHANNELS)}),obj({'unloaded':BOOL}),['controller'],'关阀回收气源','configuration','evaluator',{'channel':'purge1'})
cap('gas.set_flow',obj({'channel':enum(CHANNELS),'flow_m3_s':number('m^3/s',0,1e-4)}),obj({'flow_m3_s':number('m^3/s',0,1e-4)}),['controller'],'参考流量为显式工程输入，非厂家MFC校准',example={'channel':'purge1','flow_m3_s':30e-6/60})
cap('gas.set_open',obj({'channel':enum(CHANNELS),'opened':BOOL}),obj({'opened':BOOL}),['controller'],'三气路阀/有限排气',example={'channel':'purge1','opened':True})
cap('coolant.load',COOLANT,obj({'loaded':BOOL}),['detector_dewar','furnace'],'探测器与炉体两独立有限LN2库存','configuration','evaluator',COOLANT_EXAMPLE)
cap('coolant.unload',obj({'reservoir':enum(['detector','furnace'])}),obj({'unloaded':BOOL}),['detector_dewar','furnace'],'停止对应冷却后回收LN2','configuration','evaluator',{'reservoir':'detector'})
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_switch'],'断电停止活动并保留显热/储能',example={'enabled':True})
cap('furnace.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['furnace'],'冷态开合炉头工程行程',example={'opened':False})
cap('sampler.select',obj({'position':integer(1,16)}),obj({'position':integer(1,16)}),['sample_carousel'],'4内衬各4孔XY选位工程代理，非16等角转盘',example={'position':1})
cap('detector.set_enabled',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['ir_detector','detector_dewar'],'有限LN2支持理想冷态IR前端',example={'enabled':True})
cap('pulse.charge',obj({'energy_j':number('J',1e-8,10.),'accept_optical_budget':BOOL}),obj({'charged_j':number('J',0,10.)}),['xenon_flash'],'外部提供光能等效储能，非已知电容电路效率','configuration','evaluator',{'energy_j':1.,'accept_optical_budget':True})
cap('pulse.discharge',obj({}),obj({'discharged_j':number('J',0,10.)}),['xenon_flash'],'停机回收未发射光能预算','configuration','evaluator')
cap('thermal.set_temperature',obj({'temperature_k':number('K',173.15,773.15),'rate_k_s':number('K/s',.001,50/60)}),obj({'target_k':number('K',173.15,773.15)}),['furnace'],'有限速设定和真实炉体/试片热惯性',example={'temperature_k':303.15,'rate_k_s':50/60})
cap('thermal.disable',obj({}),obj({'enabled':BOOL}),['furnace'],'停止主动炉控保留余热')
cap('thermal.set_cooling',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['furnace'],'环境冷却工程热导，去热有账本',example={'enabled':True})
cap('sensor.calibrate',CALIBRATION,CALIBRATION,['ir_detector'],'外部独立IR与光脉冲观测校正','configuration',example=CALIBRATION_EXAMPLE)
cap('method.configure',METHOD,METHOD,['controller'],'基温平衡/矩形有限脉冲/纳秒事件采样方法','configuration',example=METHOD_EXAMPLE)
cap('acquisition.start',obj({}),obj({'operation_id':TEXT}),['controller','furnace','xenon_flash','ir_detector'],'异步实际导热温升采集')
cap('acquisition.stop',obj({}),obj({'phase':enum(['cancelled'])}),['controller','xenon_flash'],'允许微秒中止；保留已吸收能量和剩余储能')
cap('acquisition.result',obj({}),RESULT,['controller'],'owner受限冻结IR/脉冲双观测曲线','query')
cap('reference.query',obj({'reference_id':TEXT}),obj({'reference_json':TEXT,'template_source':TEXT}),['controller'],'原始材料参考与来源，非隐藏实例真值','query',example={'reference_id':'nist_sic'})
PARKER_INPUT=obj({'time_s':array(number('s'),3,200001),'temperature_k':array(nullable(number('K')),3,200001),'thickness_m':number('m',1e-8,.1),'pulse_time_s':number('s'),'baseline_k':number('K'),'plateau_k':number('K'),'accept_adiabatic_instant_pulse':BOOL})
cap('analysis.parker',PARKER_INPUT,obj({'diffusivity_m2_s':nullable(number('m^2/s',0)),'half_time_s':nullable(number('s',0)),'bracketed':BOOL,'half_fourier_number':number('1',0),'evidence':TEXT}),['controller'],'外部观测半高法，明确瞬时绝热前提','analysis',example={'time_s':[0.,.01,.02],'temperature_k':[300.,300.5,301.],'thickness_m':.002,'pulse_time_s':0.,'baseline_k':300.,'plateau_k':301.,'accept_adiabatic_instant_pulse':True})
FIT_INPUT=obj({'time_s':array(number('s'),20,200001),'temperature_k':array(number('K'),20,200001),'thickness_m':number('m',1e-8,.1),'pulse_time_s':number('s'),'pulse_width_s':number('s',1e-8,.1),'volumetric_capacity_j_m3_k':number('J/(m^3*K)',1,1e10),'front_loss_w_m2_k':number('W/(m^2*K)',0,1e5),'rear_loss_w_m2_k':number('W/(m^2*K)',0,1e5),'detector_tau_s':number('s',1e-9,10),'minimum_diffusivity_m2_s':number('m^2/s',1e-8,.002),'maximum_diffusivity_m2_s':number('m^2/s',1e-8,.002),'cells':integer(16,128),'accept_homogeneous_robin_model':BOOL})
FIT_EXAMPLE=dict(time_s=np.linspace(0,.25,51).tolist(),temperature_k=(300.+parker_response(5e-5*np.linspace(0,.25,51)/.002**2)).tolist(),thickness_m=.002,pulse_time_s=0.,pulse_width_s=.0001,volumetric_capacity_j_m3_k=3160*715.,front_loss_w_m2_k=0.,rear_loss_w_m2_k=0.,detector_tau_s=1e-6,minimum_diffusivity_m2_s=1e-6,maximum_diffusivity_m2_s=.001,cells=64,accept_homogeneous_robin_model=True)
cap('analysis.finite_pulse',FIT_INPUT,obj({'diffusivity_m2_s':number('m^2/s',0),'fit_rms_k':number('K',0),'fitted_rise_amplitude_k':number('K',0),'baseline_at_window_center_k':number('K'),'baseline_slope_k_s':number('K/s'),'points_used':integer(20,1200),'cells':integer(16,128),'evidence':TEXT}),['controller'],'外部有限脉冲/Robin损失/传感滞后曲线拟合alpha','analysis',example=FIT_EXAMPLE)
cap('analysis.conductivity',obj({'diffusivity_m2_s':number('m^2/s',0),'density_kg_m3':number('kg/m^3',1e-9),'heat_capacity_j_kg_k':number('J/(kg*K)',1e-9)}),obj({'conductivity_w_m_k':number('W/(m*K)',0),'evidence':TEXT}),['controller'],'只用外部独立alpha rho Cp推算kappa','analysis',example={'diffusivity_m2_s':5e-5,'density_kg_m3':3160.,'heat_capacity_j_kg_k':715.})
cap('sim.step',obj({'dt_s':number('s',0,3600)}),obj({'time_s':number('s',0)}),['controller'],'宿主纳秒事件时钟，允许微秒内取消','simulation','evaluator',{'dt_s':.1})
cap('emergency.stop',obj({}),obj({'latched':BOOL}),['controller'],'工程软件停机，保留实际热/库存')
cap('emergency.reset',obj({}),obj({'latched':BOOL}),['controller'],'冷态显式复位，不自动重发脉冲')
UNSUPPORTED=['autosampler.batch','sample.transparent','sample.multilayer','sample.anisotropy','holder.liquid','vacuum.control','pulse.mapping_oem','analysis.cowan_oem','analysis.cape_lehman_oem','cp.comparative','noise.empirical','accuracy.certification','cooler.pressurized_air','cooler.external_chiller','robot.load','hardware.connect']
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'未实现：'+k)
LEDGER_UNITS={k:u for k,u in [('sample_loaded_kg','kg'),('sample_present_kg','kg'),('sample_recovered_kg','kg'),('sample_error_kg','kg'),('optical_loaded_j','J'),('optical_stored_j','J'),('optical_emitted_j','J'),('optical_recovered_j','J'),('optical_error_j','J'),('pulse_absorbed_j','J'),('pulse_reflected_j','J'),('pulse_error_j','J'),('heater_input_j','J'),('ambient_loss_j','J'),('furnace_cooling_j','J'),('sample_stored_j','J'),('furnace_stored_j','J'),('recovered_sensible_j','J'),('thermal_error_j','J')]}
for channel in CHANNELS:
 for k in ['loaded','remaining','exhausted','recovered','error']:LEDGER_UNITS[channel+'_'+k+'_mol']='mol'
for tank in ['detector','furnace']:
 for k in ['loaded','remaining','evaporated','recovered','error']:LEDGER_UNITS[tank+'_coolant_'+k+'_kg']='kg'
 LEDGER_UNITS[tank+'_cold_used_j']='J';LEDGER_UNITS[tank+'_cold_error_j']='J'
cap('ledger.read',obj({}),obj({k:number(u) for k,u in LEDGER_UNITS.items()}),['controller'],'三气源/两LN2/样片/光能/全热量守恒','query','evaluator')
CAPS['sampler.select']['component_ids']=['sample_holder']
for k,c in [('plant.configured','controller'),('power.enabled','power_switch'),('sample.present','specimen'),('detector.enabled','ir_detector'),('detector.ready','ir_detector'),('thermal.enabled','furnace'),('cooling.enabled','furnace'),('emergency.latched','controller'),('furnace.opened','furnace'),('furnace.moving','furnace'),('sampler.moving','sample_holder')]:slot(k,BOOL,'1','实际配置/状态/工程互锁',component=c)
for k,c in [('lamp.purge1','purge_inlet_1'),('lamp.purge2','purge_inlet_2'),('lamp.protective','protective_inlet'),('lamp.locked','furnace'),('lamp.unlocked','furnace')]:slot(k,BOOL,'1','可见灯的显式工程状态映射，非厂家灯算法校准','scene',component=c)
slot('furnace.lift_m',number('m',0,.12),'m','工程炉头升高；0闭/.12开','scene',component='furnace');slot('furnace.target_m',number('m',0,.12),'m','工程炉头目标','setpoint',component='furnace');slot('power.angle_rad',number('rad',0,.28),'rad','电源开关姿态','scene',component='power_switch')
for axis,c in [('x','sampler_x'),('y','sample_holder')]:
 for target in ['', 'target_']:slot('sampler.'+target+axis+'_m',number('m',-.028,.028),'m','4×4孔XY选位工程代理，目标为孔坐标负值','setpoint' if target else 'scene',component=c)
slot('sampler.position',integer(1,16),'1','已选物理孔位','setpoint',component='sample_holder');slot('sample.position',nullable(integer(1,16)),'1','已装单样片孔位','scene',component='specimen')
for k in ['thermal.target_k','thermal.final_target_k']:slot(k,number('K',173.15,773.15),'K','有限速目标，不是真温','setpoint',component='furnace')
for k,u in [('furnace.indicated_k','K'),('detector.indicated_k','K'),('pulse.indicated_w','W')]:slot(k,nullable(number(u)),u,'独立工程观测，饱和/未冷却则null，非accuracy','sensor',component='ir_detector' if k.startswith('detector') else 'controller')
for k,u in [('truth.front_k','K'),('truth.rear_k','K'),('truth.mean_k','K'),('truth.furnace_k','K'),('truth.alpha_m2_s','m^2/s'),('truth.sample_mass_kg','kg'),('truth.optical_stored_j','J'),('truth.detector_coolant_kg','kg'),('truth.furnace_coolant_kg','kg')]:slot(k,nullable(number(u)),u,'真实过程量，仅评估器','process_truth','evaluator')
for g in CHANNELS:
 slot('gas.'+g+'.opened',BOOL,'1','实际阀状态');slot('gas.'+g+'.flow_m3_s',number('m^3/s',0,1e-4),'m^3/s','显式参考状态流量','setpoint');slot('truth.'+g+'_mol',number('mol',0),'mol','有限气源库存','process_truth','evaluator')
slot('clock.time_s',number('s',0),'s','唯一宿主时钟，以ns事件解析微秒脉冲');slot('acquisition.phase',PHASE,'1','异步终态');slot('acquisition.stage',enum(['idle','equilibrating','baseline','pulse','recording','complete']),'1','物理采集阶段');slot('acquisition.elapsed_s',number('s',0),'s','宿主推进的真实过程时长');slot('acquisition.points',integer(0,200001),'1','实际内部时刻观测节点数');slot('model.fault',nullable(TEXT),'1','模型域或有限库存故障')

class NetzschLfa467Runtime:
 def __init__(self,instance_id='lfa467_01'):
  self.instance_id=instance_id;self.plant=None;self.sample=None;self.reference=None;self.power=False;self.emergency=False;self.furnace_lift=.12;self.furnace_target=.12;self.sampler_x=self.sampler_y=self.sampler_target_x=self.sampler_target_y=.028;self.position=1;self.detector_enabled=False;self.thermal_enabled=False;self.air_cooling=False;self.target=self.final_target=298.15;self.rate=50/60;self.theta=np.zeros(1);self.alpha=None;self.model=None;self.model_key=None;self.sensor_delta=0.;self.gases={};self.gas_open={g:False for g in CHANNELS};self.gas_flow={g:30e-6/60 for g in CHANNELS};self.gas_loaded={g:0. for g in CHANNELS};self.gas_remaining={g:0. for g in CHANNELS};self.gas_used={g:0. for g in CHANNELS};self.gas_recovered={g:0. for g in CHANNELS};self.coolant_present={g:False for g in ['detector','furnace']};self.coolant_loaded={g:0. for g in ['detector','furnace']};self.coolant_remaining={g:0. for g in ['detector','furnace']};self.coolant_used={g:0. for g in ['detector','furnace']};self.coolant_recovered={g:0. for g in ['detector','furnace']};self.cold_used={g:0. for g in ['detector','furnace']};self.optical_loaded=self.optical_stored=self.optical_emitted=self.optical_recovered=self.absorbed=self.reflected=0.;self.mass_loaded=self.mass_recovered=self.recovered_sensible=0.;self.heater_input=self.ambient_loss=self.furnace_cooling=0.;self.method=copy.deepcopy(METHOD_EXAMPLE);self.calibration=copy.deepcopy(CALIBRATION_EXAMPLE);self.phase='idle';self.stage='idle';self.rows=[];self.time_ns=0;self.operation_start_ns=0;self.stage_start_ns=0;self.pulse_start_ns=None;self.next_sample_ns=None;self.elapsed=0.;self.equilibrium_ns=0;self.operation_id=None;self.operation_reference=None;self.operation_source=None;self.operation_method=None;self.operation_calibration=None;self.error=None;self.model_fault=None;self.pending_fault=None;self.records={};self._counter=0;self.revision=0;self._requests={};self._owners={};self._cancel_operations={};self._principals={'agent':'agent','evaluator':'evaluator'}
 @property
 def time_s(self):return self.time_ns/1e9
 def register_principal(self,principal_id,visibility='agent'):
  if not isinstance(principal_id,str) or not principal_id or principal_id in self._principals or visibility not in ('agent','debug','evaluator'):raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='agent'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return NetzschLfa467Session(self,principal_id)
 def describe(self):return export_netzsch_lfa467_contract_metadata()
 def _mass(self):return self.sample['density_kg_m3']*math.pi*self.sample['diameter_m']**2/4*self.sample['thickness_m'] if self.sample else 0.
 def _furnace(self):return float((self.plant['ambient_k'] if self.plant else 298.15)+self.theta[0])
 def _mean(self):return float(self.plant['ambient_k']+np.mean(self.theta[1:])) if self.sample else self._furnace()
 def _rear(self):return float(self.plant['ambient_k']+self.model['rear']@self.theta) if self.sample and self.model is not None else None
 def _front(self,power=0.):return float(self.plant['ambient_k']+self.model['front']@self.theta+self.model['front_direct_k_w']*power) if self.sample and self.model is not None else None
 def _moving(self):return abs(self.furnace_lift-self.furnace_target)>1e-12 or abs(self.sampler_x-self.sampler_target_x)>1e-12 or abs(self.sampler_y-self.sampler_target_y)>1e-12
 def _cold(self):return max(self._furnace(),self.plant['ambient_k']+float(np.max(self.theta)))<=self.plant['safe_access_temperature_k']
 def _detector_ready(self):return self.power and self.detector_enabled and self.coolant_remaining['detector']>0
 def _gas_ready(self):return self.gas_open['protective'] and self.gas_flow['protective']>0 and any(self.gas_open[g] and self.gas_flow[g]>0 for g in ['purge1','purge2'])
 def _domain(self,T):
  low=self.plant['ambient_k'] if self.plant['cooler']=='none' or self.plant['detector']=='InSb' else 173.15
  if not low<=T<=773.15:raise ActionRejected('CONFIGURATION_TEMPERATURE_RANGE')
  if self.sample:source_alpha(self.reference,T)
 def _ensure_model(self):
  p=self.plant;Ga=p['ambient_conductance_w_k']+(p['forced_cooling_conductance_w_k'] if self.air_cooling else 0.);key=(self.alpha,Ga,self.sample is not None)
  if key==self.model_key:return
  if self.sample:
   s=self.sample;self.model=thermal_modes(s['thickness_m'],math.pi*s['diameter_m']**2/4,s['density_kg_m3'],s['heat_capacity_j_kg_k'],self.alpha,s['front_loss_w_m2_k'],s['rear_loss_w_m2_k'],p['cells'],p['furnace_capacity_j_k'],Ga)
  else:
   C=np.array([p['furnace_capacity_j_k']]);self.model=dict(C=C,sqrtC=np.sqrt(C),eigenvalues=np.array([-Ga/C[0]]),eigenvectors=np.eye(1),rear=np.ones(1),front=np.ones(1),pulse_injection=np.ones(1),front_direct_k_w=0.,ambient_conductance_w_k=Ga,kappa_w_m_k=0.)
  self.model_key=key
 def _observation(self,sensor=None,pulse_w=0.):
  if not self._detector_ready() or not self.sample:return None,None
  p=self.plant;c=self.calibration;y=p['ambient_k']+((self.sensor_delta if sensor is None else float(sensor))-c['temperature_zero_k'])*c['temperature_gain_correction'];q=p['detector_quantum_k'];y=float(round(y/q)*q) if p['detector_minimum_k']<=y<=p['detector_maximum_k'] else None;power=(p['pulse_monitor_gain']*pulse_w+p['pulse_monitor_offset_w']-c['pulse_zero_w'])*c['pulse_gain_correction'];return y,float(power)
 def _current_pulse_power(self):return self.method['pulse_energy_j']/self.method['pulse_width_s'] if self.phase=='running' and self.stage=='pulse' else 0.
 def read(self,role='evaluator'):
  T,P=self._observation(pulse_w=self._current_pulse_power());d={'plant.configured':self.plant is not None,'power.enabled':self.power,'sample.present':self.sample is not None,'detector.enabled':self.detector_enabled,'detector.ready':self._detector_ready(),'thermal.enabled':self.thermal_enabled,'cooling.enabled':self.air_cooling,'emergency.latched':self.emergency,'furnace.opened':self.furnace_lift>=.12-1e-12,'furnace.moving':abs(self.furnace_lift-self.furnace_target)>1e-12,'sampler.moving':abs(self.sampler_x-self.sampler_target_x)>1e-12 or abs(self.sampler_y-self.sampler_target_y)>1e-12,'lamp.locked':self.power and self.furnace_lift<=1e-12 and self.furnace_target==0.,'lamp.unlocked':self.power and self.furnace_lift>=.12-1e-12 and self.furnace_target==.12,'furnace.lift_m':self.furnace_lift,'furnace.target_m':self.furnace_target,'power.angle_rad':.28 if self.power else 0.,'sampler.x_m':self.sampler_x,'sampler.y_m':self.sampler_y,'sampler.target_x_m':self.sampler_target_x,'sampler.target_y_m':self.sampler_target_y,'sampler.position':self.position,'sample.position':self.sample['position'] if self.sample else None,'thermal.target_k':self.target,'thermal.final_target_k':self.final_target,'furnace.indicated_k':self._furnace() if self.power else None,'detector.indicated_k':T,'pulse.indicated_w':P,'truth.front_k':self._front(self._current_pulse_power()*(self.sample['absorptance'] if self.sample else 0.)),'truth.rear_k':self._rear(),'truth.mean_k':self._mean(),'truth.furnace_k':self._furnace(),'truth.alpha_m2_s':self.alpha,'truth.sample_mass_kg':self._mass(),'truth.optical_stored_j':self.optical_stored,'truth.detector_coolant_kg':self.coolant_remaining['detector'],'truth.furnace_coolant_kg':self.coolant_remaining['furnace'],'clock.time_s':self.time_s,'acquisition.phase':self.phase,'acquisition.stage':self.stage,'acquisition.elapsed_s':self.elapsed,'acquisition.points':len(self.rows),'model.fault':self.model_fault}
  for g in CHANNELS:d.update({'gas.'+g+'.opened':self.gas_open[g],'gas.'+g+'.flow_m3_s':self.gas_flow[g],'truth.'+g+'_mol':self.gas_remaining[g],'lamp.'+g:self.power and self.gas_open[g]})
  return {k:v for k,v in d.items() if role!='agent' or SLOTS[k]['visibility']=='agent'}
 def _ledger(self):
  sample_j=float(self.model['C'][1:]@self.theta[1:]) if self.sample else 0.;furnace_j=self.plant['furnace_capacity_j_k']*self.theta[0] if self.plant else 0.;d=dict(sample_loaded_kg=self.mass_loaded,sample_present_kg=self._mass(),sample_recovered_kg=self.mass_recovered,sample_error_kg=self.mass_loaded-self._mass()-self.mass_recovered,optical_loaded_j=self.optical_loaded,optical_stored_j=self.optical_stored,optical_emitted_j=self.optical_emitted,optical_recovered_j=self.optical_recovered,optical_error_j=self.optical_loaded-self.optical_stored-self.optical_emitted-self.optical_recovered,pulse_absorbed_j=self.absorbed,pulse_reflected_j=self.reflected,pulse_error_j=self.optical_emitted-self.absorbed-self.reflected,heater_input_j=self.heater_input,ambient_loss_j=self.ambient_loss,furnace_cooling_j=self.furnace_cooling,sample_stored_j=sample_j,furnace_stored_j=furnace_j,recovered_sensible_j=self.recovered_sensible,thermal_error_j=self.heater_input+self.absorbed-self.ambient_loss-self.furnace_cooling-sample_j-furnace_j-self.recovered_sensible)
  for g in CHANNELS:
   for k,v in dict(loaded=self.gas_loaded[g],remaining=self.gas_remaining[g],exhausted=self.gas_used[g],recovered=self.gas_recovered[g],error=self.gas_loaded[g]-self.gas_remaining[g]-self.gas_used[g]-self.gas_recovered[g]).items():d[g+'_'+k+'_mol']=v
  for g in ['detector','furnace']:
   for k,v in dict(loaded=self.coolant_loaded[g],remaining=self.coolant_remaining[g],evaporated=self.coolant_used[g],recovered=self.coolant_recovered[g],error=self.coolant_loaded[g]-self.coolant_remaining[g]-self.coolant_used[g]-self.coolant_recovered[g]).items():d[g+'_coolant_'+k+'_kg']=v
   d[g+'_cold_used_j']=self.cold_used[g];d[g+'_cold_error_j']=self.coolant_used[g]*self.plant['coolant_latent_j_kg']*self.plant['coolant_efficiency']-self.cold_used[g] if self.plant else 0.
  return {k:float(v) for k,v in d.items()}
 def _result(self):
  if self.phase!='running' and self.operation_id in self.records:return copy.deepcopy(self.records[self.operation_id])
  return dict(operation_id=self.operation_id,phase=self.phase,elapsed_s=self.elapsed,stage=self.stage,pulse_time_s=self.pulse_start_ns/1e9 if self.pulse_start_ns is not None else None,rows=copy.deepcopy(self.rows),reference_id=self.operation_reference,template_source=self.operation_source,method_json=json.dumps(self.operation_method,sort_keys=True) if self.operation_method else None,calibration_json=json.dumps(self.operation_calibration,sort_keys=True) if self.operation_calibration else None,evidence='Source diffusivity anchors homogeneous opaque slab1D finite-volume thermal response, with independent supplied density/Cp, absorptance, Robin losses, constant-alpha small-shot approximation and IR lag/gain/offset/saturation. Finite rectangular pulse is an engineering waveform, not OEM pulse mapping. Rear surface uses half-cell Robin interface rather than cell-center substitution. Uniform internal sensor times are evaluated analytically within host steps, not claimed2MHz real hardware. Only external observation analysis estimates alpha; source alpha is never returned as a measured result. One loaded disk and selected slot, no automatic batch. Partial cancel preserves deposited heat and remaining charge; standard non-HT temperature/detector/cooler limits enforced.',error_code=self.error)
 def _finish(self,phase,error=None):
  self.phase=phase;self.stage='complete';self.error=error;self.thermal_enabled=False;self.records[self.operation_id]=self._result();self._invalidate_control()
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
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _powered(self):
  if not self.power:raise ActionRejected('POWER_OFF')
  if self.emergency:raise ActionRejected('EMERGENCY_LATCHED')
 def _mechanical(self):
  self._idle()
  if self.emergency:raise ActionRejected('EMERGENCY_LATCHED')
  if not self._cold():raise ActionRejected('HOT_SAMPLE')
  if self.thermal_enabled:raise ActionRejected('THERMAL_CONTROL_ACTIVE')
  if self._moving():raise ActionRejected('MECHANISM_MOVING')
 def _invalidate_control(self):self._cached_power=None;self._control_until_ns=0
 def _validate_method(self,a):
  self._domain(a['temperature_k'])
  if not a['accept_condition_transfer']:raise ActionRejected('CONDITION_TRANSFER_NOT_ACCEPTED')
  if abs(a['pulse_width_s']*1e6-round(a['pulse_width_s']*1e6))>1e-7:raise ActionRejected('PULSE_WIDTH_GRID')
  for k in ['sample_interval_s','pretrigger_s','record_duration_s','equilibrium_dwell_s','timeout_s']:
   if abs(a[k]*1e9-round(a[k]*1e9))>1e-4:raise ActionRejected('TIME_GRID')
  if math.ceil((a['pretrigger_s']+a['record_duration_s'])/a['sample_interval_s'])+3>200001:raise ActionRejected('TOO_MANY_POINTS')
  if a['record_duration_s']<a['pulse_width_s']:raise ActionRejected('RECORD_SHORTER_THAN_PULSE')
 def _invoke(self,k,a):
  if k=='reference.query':
   record=library()['substances'].get(a['reference_id'])
   if record is None:raise ActionRejected('INVALID_REFERENCE')
   return {'reference_json':json.dumps(record,sort_keys=True,allow_nan=False),'template_source':json.dumps(record.get('source',{}),sort_keys=True)}
  if k=='analysis.parker':return analyze_parker(**a)
  if k=='analysis.finite_pulse':return fit_diffusivity(**a)
  if k=='analysis.conductivity':return {'conductivity_w_m_k':a['diffusivity_m2_s']*a['density_kg_m3']*a['heat_capacity_j_kg_k'],'evidence':'Product of independently supplied external alpha, density and mass-specific heat capacity; no sample truth or comparative cp calibration is accessed.'}
  if k=='acquisition.result':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='plant.configure':
   if self.plant is not None:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   if a['safe_access_temperature_k']<a['ambient_k'] or a['detector_minimum_k']>=a['detector_maximum_k']:raise ActionRejected('INVALID_CONFIGURATION')
   self.plant=copy.deepcopy(a);self.target=self.final_target=a['ambient_k'];self._invalidate_control();self._ensure_model();return {'configured':True}
  if self.plant is None:raise ActionRejected('NOT_CONFIGURED')
  if k=='emergency.stop':
   if self.phase=='running':self._record_here();self._finish('failed','SOFTWARE_STOP')
   self.emergency=True;self.thermal_enabled=False;self.gas_open={g:False for g in CHANNELS};self.detector_enabled=False;self.furnace_target=self.furnace_lift;self.sampler_target_x=self.sampler_x;self.sampler_target_y=self.sampler_y;self._invalidate_control();return {'latched':True}
  if k=='emergency.reset':
   self._idle()
   if not self._cold():raise ActionRejected('HOT_SAMPLE')
   self.emergency=False;return {'latched':False}
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self._record_here();self._finish('cancelled');self._invalidate_control();return {'phase':'cancelled'}
  if k=='power.set' and not a['enabled'] and self.phase=='running':self._record_here();self._finish('failed','POWER_LOSS')
  self._idle()
  if k=='power.set':
   if a['enabled'] and self.emergency:raise ActionRejected('EMERGENCY_LATCHED')
   self.power=a['enabled']
   if not self.power:self.thermal_enabled=False;self.detector_enabled=False;self.gas_open={g:False for g in CHANNELS};self.furnace_target=self.furnace_lift;self.sampler_target_x=self.sampler_x;self.sampler_target_y=self.sampler_y
   self._invalidate_control();return {'enabled':self.power}
  if k=='sample.load':
   self._mechanical()
   if self.sample:raise ActionRejected('ALREADY_LOADED')
   if self.furnace_lift<.12-1e-12 or self.position!=a['position']:raise ActionRejected('SAMPLE_ACCESS')
   if not a['accept_homogeneous_opaque']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   if not a['accept_condition_transfer']:raise ActionRejected('CONDITION_TRANSFER_NOT_ACCEPTED')
   rec=library()['substances'].get(a['reference_id']);alpha=source_alpha(rec,self.plant['ambient_k']);self.sample=copy.deepcopy(a);self.reference=copy.deepcopy(rec);self.alpha=alpha;self.theta=np.r_[self.theta[0],np.zeros(self.plant['cells'])];self.model_key=None;self._ensure_model();self.mass_loaded+=self._mass();self.model_fault=None;self._invalidate_control();return {'loaded':True}
  if k=='sample.remove':
   self._mechanical()
   if not self.sample:raise ActionRejected('NOT_LOADED')
   if self.furnace_lift<.12-1e-12 or self.position!=self.sample['position']:raise ActionRejected('SAMPLE_ACCESS')
   self.mass_recovered+=self._mass();self.recovered_sensible+=float(self.model['C'][1:]@self.theta[1:]);self.theta=self.theta[:1].copy();self.sample=self.reference=None;self.alpha=None;self.model_key=None;self._ensure_model();self.model_fault=None;self._invalidate_control();return {'removed':True}
  if k in ['gas.load','gas.unload']:
   g=a['channel']
   if self.gas_open[g]:raise ActionRejected('GAS_VALVE_OPEN')
   if k=='gas.load':
    if g in self.gases:raise ActionRejected('ALREADY_LOADED')
    if not a['accept_reference_conditions']:raise ActionRejected('REFERENCE_CONDITIONS_NOT_ACCEPTED')
    self.gases[g]=copy.deepcopy(a);self.gas_remaining[g]=a['amount_mol'];self.gas_loaded[g]+=a['amount_mol'];return {'loaded':True}
   if g not in self.gases:raise ActionRejected('NOT_LOADED')
   self.gas_recovered[g]+=self.gas_remaining[g];self.gas_remaining[g]=0.;del self.gases[g];return {'unloaded':True}
  if k=='gas.set_flow':
   if self.thermal_enabled and a['flow_m3_s']==0:raise ActionRejected('PURGE_REQUIRED')
   self.gas_flow[a['channel']]=a['flow_m3_s'];return {'flow_m3_s':a['flow_m3_s']}
  if k=='gas.set_open':
   g=a['channel']
   if a['opened']:
    self._powered()
    if g not in self.gases or self.gas_remaining[g]<=0:raise ActionRejected('NO_GAS')
   self.gas_open[g]=a['opened']
   if not self._gas_ready():self.thermal_enabled=False;self._invalidate_control()
   return {'opened':self.gas_open[g]}
  if k in ['coolant.load','coolant.unload']:
   g=a['reservoir']
   if g=='furnace' and self.thermal_enabled or g=='detector' and self.detector_enabled:raise ActionRejected('COOLING_ACTIVE')
   if g=='furnace' and self.plant['cooler']!='LN2':raise ActionRejected('COOLER_NOT_CONFIGURED')
   if k=='coolant.load':
    if self.coolant_present[g]:raise ActionRejected('ALREADY_LOADED')
    if not a['accept_engineering_latent']:raise ActionRejected('MODEL_NOT_ACCEPTED')
    self.coolant_present[g]=True;self.coolant_remaining[g]=a['amount_kg'];self.coolant_loaded[g]+=a['amount_kg'];return {'loaded':True}
   if not self.coolant_present[g]:raise ActionRejected('NOT_LOADED')
   self.coolant_recovered[g]+=self.coolant_remaining[g];self.coolant_remaining[g]=0.;self.coolant_present[g]=False;return {'unloaded':True}
  if k=='furnace.set':
   self._powered();self._mechanical();self.furnace_target=.12 if a['opened'] else 0.;return {'opened':a['opened']}
  if k=='sampler.select':
   self._powered();self._mechanical()
   if self.furnace_lift<.12-1e-12:raise ActionRejected('FURNACE_NOT_OPEN')
   self.position=a['position'];coords=[-.028,-.012,.012,.028];self.sampler_target_x=-coords[(self.position-1)%4];self.sampler_target_y=-coords[(self.position-1)//4];return {'position':self.position}
  if k=='detector.set_enabled':
   if a['enabled']:
    self._powered()
    if self.coolant_remaining['detector']<=0:raise ActionRejected('NO_DETECTOR_COOLANT')
   self.detector_enabled=a['enabled'];return {'enabled':self.detector_enabled}
  if k=='pulse.charge':
   self._powered()
   if not a['accept_optical_budget']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   if self.optical_stored+a['energy_j']>10.:raise ActionRejected('OPTICAL_CAPACITY')
   self.optical_loaded+=a['energy_j'];self.optical_stored+=a['energy_j'];return {'charged_j':self.optical_stored}
  if k=='pulse.discharge':
   out=self.optical_stored;self.optical_recovered+=out;self.optical_stored=0.;return {'discharged_j':out}
  if k=='thermal.set_temperature':
   self._powered();self._domain(a['temperature_k'])
   if self._moving() or self.furnace_lift>1e-12:raise ActionRejected('FURNACE_NOT_CLOSED')
   if not self._gas_ready():raise ActionRejected('PURGE_REQUIRED')
   if a['temperature_k']<self.plant['ambient_k'] and self.coolant_remaining['furnace']<=0:raise ActionRejected('NO_FURNACE_COOLANT')
   self.final_target=a['temperature_k'];self.rate=a['rate_k_s'];self.thermal_enabled=True;self._invalidate_control();return {'target_k':self.final_target}
  if k=='thermal.disable':self.thermal_enabled=False;self._invalidate_control();return {'enabled':False}
  if k=='thermal.set_cooling':self._powered();self.air_cooling=a['enabled'];self._ensure_model();self._invalidate_control();return {'enabled':self.air_cooling}
  if k=='sensor.calibrate':self.calibration=copy.deepcopy(a);return copy.deepcopy(a)
  if k=='method.configure':self._validate_method(a);self.method=copy.deepcopy(a);return copy.deepcopy(a)
  if k=='acquisition.start':
   self._powered();self._validate_method(self.method)
   if not self.sample:raise ActionRejected('NO_SAMPLE')
   if self.model_fault:raise ActionRejected('MODEL_FAULT_RELOAD_REQUIRED')
   if self.position!=self.sample['position']:raise ActionRejected('NO_SAMPLE_AT_SELECTED_SLOT')
   if self._moving() or self.furnace_lift>1e-12:raise ActionRejected('FURNACE_NOT_CLOSED')
   if not self._gas_ready():raise ActionRejected('PURGE_REQUIRED')
   if not self._detector_ready():raise ActionRejected('DETECTOR_NOT_READY')
   if self.method['pulse_energy_j']>self.optical_stored:raise ActionRejected('INSUFFICIENT_OPTICAL_ENERGY')
   if self.method['temperature_k']<self.plant['ambient_k'] and self.coolant_remaining['furnace']<=0:raise ActionRejected('NO_FURNACE_COOLANT')
   self._counter+=1;self.operation_id=f'{self.instance_id}:flash:{self._counter}';self.operation_start_ns=self.time_ns;self.stage_start_ns=self.time_ns;self.equilibrium_ns=0;self.rows=[];self.phase='running';self.stage='equilibrating';self.elapsed=0.;self.error=None;self.pending_fault=None;self.pulse_start_ns=None;self.planned_pulse_ns=None;self.next_sample_ns=None;self.operation_reference=self.sample['reference_id'];self.operation_source=json.dumps(self.reference.get('source',{}),sort_keys=True);self.operation_method=copy.deepcopy(self.method);self.operation_calibration=copy.deepcopy(self.calibration);self.final_target=self.method['temperature_k'];self.rate=self.method['rate_k_s'];self.thermal_enabled=True;self._invalidate_control();return {'operation_id':self.operation_id}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 @staticmethod
 def _toward(value,target,delta):return float(target if abs(target-value)<=delta else value+math.copysign(delta,target-value))
 def _control(self):
  if self._cached_power is not None and self.time_ns<self._control_until_ns:return self._cached_power
  self._ensure_model();end=(self.time_ns//10000000+1)*10000000;h=(end-self.time_ns)/1e9;p=self.plant
  if not self.thermal_enabled:P=0.
  else:
   desired=self._toward(self.target,self.final_target,self.rate*h);zero=propagate_modes(self.model,self.theta,0.,0.,h,detector_tau_s=p['detector_tau_s']);_,phi,_=modal_kernels(self.model['eigenvalues'],[h]);response=float(np.sum(self.model['eigenvectors'][0,:]**2*phi[:,0])/p['furnace_capacity_j_k']);raw=p['controller_gain_w_k']*(desired-p['ambient_k']-zero['temperature_delta_k'][0])/(1+p['controller_gain_w_k']*response);cold=p['cooler_max_w'] if p['cooler']=='LN2' and self.coolant_remaining['furnace']>0 else 0.;P=max(-cold,min(p['heater_max_w'],raw))
  self._cached_power=float(P);self._control_until_ns=end;return self._cached_power
 def _equilibrated(self):
  tol=self.method['equilibrium_tolerance_k'];return abs(self.target-self.final_target)<1e-10 and abs(self._furnace()-self.final_target)<=tol and abs(self._mean()-self.final_target)<=tol and (not self.sample or float(np.ptp(self.theta[1:]))<=tol)
 def _row(self,time_ns,sensor_delta,pulse_w):
  T,P=self._observation(sensor_delta,pulse_w);row=dict(time_s=time_ns/1e9,pulse_elapsed_s=(time_ns-self.planned_pulse_ns)/1e9,temperature_k=T,pulse_power_w=P)
  if self.rows and abs(self.rows[-1]['time_s']-row['time_s'])<1e-12:self.rows[-1]=row
  else:self.rows.append(row)
 def _record_here(self):
  if getattr(self,'planned_pulse_ns',None) is not None:self._row(self.time_ns,self.sensor_delta,self._current_pulse_power())
 def _transitions(self):
  if self.phase!='running':return
  if self.time_ns-self.operation_start_ns>=round(self.method['timeout_s']*1e9):self._record_here();self._finish('failed','TIMEOUT');self._invalidate_control();return
  if self.stage=='equilibrating' and self._equilibrated() and self.equilibrium_ns>=round(self.method['equilibrium_dwell_s']*1e9):
   try:self.alpha=source_alpha(self.reference,self._mean())
   except ActionRejected as exc:self._finish('failed',str(exc));return
   self.model_key=None;self._ensure_model();self.shot_base_k=self._mean();self.stage='baseline';self.stage_start_ns=self.time_ns;self.planned_pulse_ns=self.time_ns+round(self.method['pretrigger_s']*1e9);self.next_sample_ns=self.time_ns;self._record_here();self.next_sample_ns+=round(self.method['sample_interval_s']*1e9)
  if self.stage=='baseline' and self.time_ns>=self.planned_pulse_ns:
   absorbed_power=self.method['pulse_energy_j']*self.sample['absorptance']/self.method['pulse_width_s'];conservative=self.method['pulse_energy_j']*self.sample['absorptance']/float(np.min(self.model['C'][1:]))+self.model['front_direct_k_w']*absorbed_power
   if conservative>self.plant['maximum_local_temperature_rise_k']:self.model_fault='PULSE_LINEAR_DOMAIN';self._record_here();self._finish('failed','PULSE_LINEAR_DOMAIN');self._invalidate_control();return
   self.stage='pulse';self.pulse_start_ns=self.time_ns;self.stage_start_ns=self.time_ns;self._record_here()
  if self.stage=='pulse' and self.time_ns>=self.pulse_start_ns+round(self.method['pulse_width_s']*1e9):self.stage='recording';self.stage_start_ns=self.time_ns
  if self.stage=='recording' and self.time_ns>=self.pulse_start_ns+round(self.method['record_duration_s']*1e9):self._record_here();self._finish('succeeded');self._invalidate_control()
 def _physics(self,ns,power):
  h=ns/1e9;p=self.plant;emitted_power=self._current_pulse_power();emitted=min(self.optical_stored,emitted_power*h);absorbed=emitted*(self.sample['absorptance'] if self.sample else 0.);pulse=absorbed/h;self._ensure_model();times_ns=[]
  if self.phase=='running' and self.stage in ['baseline','pulse','recording']:
   inc=round(self.method['sample_interval_s']*1e9)
   while self.next_sample_ns is not None and self.next_sample_ns<=self.time_ns+ns:times_ns.append(self.next_sample_ns);self.next_sample_ns+=inc
  times=(np.asarray(times_ns,dtype=np.int64)-self.time_ns)/1e9 if times_ns else None
  out=propagate_modes(self.model,self.theta,power,pulse,h,self.sensor_delta,p['detector_tau_s'],p['detector_gain'],p['detector_offset_k'],times)
  for timestamp,sensor in zip(times_ns,out['observed_delta_k']):
   physical_power=emitted_power if self.pulse_start_ns is not None and self.pulse_start_ns<=timestamp<self.pulse_start_ns+round(self.method['pulse_width_s']*1e9) else 0.;self._row(int(timestamp),float(sensor),physical_power)
  self.theta=out['temperature_delta_k'];self.sensor_delta=out['detector_delta_k'];self.heater_input+=max(power,0.)*h;self.furnace_cooling+=max(-power,0.)*h;self.ambient_loss+=out['ambient_j'];self.optical_stored-=emitted;self.optical_emitted+=emitted;self.absorbed+=absorbed;self.reflected+=emitted-absorbed
  cold={'detector':p['detector_cooling_w']*h if self._detector_ready() else 0.,'furnace':max(-power,0.)*h}
  for tank,Q in cold.items():
   used=min(self.coolant_remaining[tank],Q/(p['coolant_latent_j_kg']*p['coolant_efficiency']));self.coolant_remaining[tank]-=used;self.coolant_used[tank]+=used;self.cold_used[tank]+=used*p['coolant_latent_j_kg']*p['coolant_efficiency']
   if Q>0 and self.coolant_remaining[tank]<=0:self.pending_fault='DETECTOR_COOLANT_EXHAUSTED' if tank=='detector' else 'FURNACE_COOLANT_EXHAUSTED';self.thermal_enabled=False;self._invalidate_control()
  for g in CHANNELS:
   if self.gas_open[g]:
    stock=self.gases[g];need=self.gas_flow[g]*stock['reference_pressure_pa']/(R_GAS*stock['reference_temperature_k'])*h;used=min(need,self.gas_remaining[g]);self.gas_remaining[g]-=used;self.gas_used[g]+=used
    if self.gas_remaining[g]<=0:self.gas_open[g]=False;self.pending_fault='GAS_EXHAUSTED';self.thermal_enabled=False;self._invalidate_control()
  if self.power and not self.emergency:
   self.furnace_lift=self._toward(self.furnace_lift,self.furnace_target,p['furnace_speed_m_s']*h);self.sampler_x=self._toward(self.sampler_x,self.sampler_target_x,p['sampler_speed_m_s']*h);self.sampler_y=self._toward(self.sampler_y,self.sampler_target_y,p['sampler_speed_m_s']*h)
  if self.thermal_enabled:self.target=self._toward(self.target,self.final_target,self.rate*h)
  if self.phase=='running' and self.stage=='equilibrating':self.equilibrium_ns=self.equilibrium_ns+ns if self._equilibrated() else 0
  if self.phase=='running' and self.stage in ['pulse','recording'] and self.sample and float(np.max(self.theta[1:]))+p['ambient_k']-self.shot_base_k>p['maximum_local_temperature_rise_k']:self.model_fault='PULSE_LINEAR_DOMAIN';self.pending_fault='PULSE_LINEAR_DOMAIN'
 def step(self,dt_s):
  validate(number('s',0,3600),dt_s);delta=round(dt_s*1e9)
  if dt_s>0 and delta==0:raise ActionRejected('TIME_RESOLUTION')
  if delta==0:return
  end=self.time_ns+delta
  if self.plant is None:self.time_ns=end;self.revision+=int(delta>0);return
  while self.time_ns<end:
   self._transitions();power=self._control();ns=min(end-self.time_ns,self._control_until_ns-self.time_ns)
   if self.phase=='running':
    ns=min(ns,max(1,self.operation_start_ns+round(self.method['timeout_s']*1e9)-self.time_ns))
    if self.stage=='equilibrating' and self._equilibrated():ns=min(ns,max(1,round(self.method['equilibrium_dwell_s']*1e9)-self.equilibrium_ns))
    if self.stage=='baseline':ns=min(ns,self.planned_pulse_ns-self.time_ns)
    elif self.stage=='pulse':ns=min(ns,self.pulse_start_ns+round(self.method['pulse_width_s']*1e9)-self.time_ns)
    elif self.stage=='recording':ns=min(ns,self.pulse_start_ns+round(self.method['record_duration_s']*1e9)-self.time_ns)
   resources=[]
   for g in CHANNELS:
    if self.gas_open[g] and self.gas_flow[g]>0:
     stock=self.gases[g];rate=self.gas_flow[g]*stock['reference_pressure_pa']/(R_GAS*stock['reference_temperature_k']);resources.append(('gas',g,self.gas_remaining[g]/rate))
   for tank,Q in [('detector',self.plant['detector_cooling_w'] if self._detector_ready() else 0.),('furnace',max(-power,0.))]:
    if Q>0:resources.append(('cold',tank,self.coolant_remaining[tank]*self.plant['coolant_latent_j_kg']*self.plant['coolant_efficiency']/Q))
   exhausted=[]
   for kind,name,seconds in resources:
    ticks=int(math.floor(seconds*1e9+1e-6))
    if ticks<1:exhausted.append((kind,name))
    else:ns=min(ns,ticks)
   if exhausted:
    for kind,name in exhausted:
     if kind=='gas':self.gas_used[name]+=self.gas_remaining[name];self.gas_remaining[name]=0.;self.gas_open[name]=False;reason='GAS_EXHAUSTED'
     else:used=self.coolant_remaining[name];self.coolant_used[name]+=used;self.coolant_remaining[name]=0.;self.cold_used[name]+=used*self.plant['coolant_latent_j_kg']*self.plant['coolant_efficiency'];reason='DETECTOR_COOLANT_EXHAUSTED' if name=='detector' else 'FURNACE_COOLANT_EXHAUSTED'
    self.thermal_enabled=False;self._invalidate_control()
    if self.phase=='running':self._record_here();self._finish('failed',reason)
    continue
   if ns<=0:self._transitions();continue
   self._physics(ns,power);self.time_ns+=ns
   if self.phase=='running':
    self.elapsed=(self.time_ns-self.operation_start_ns)/1e9
    if self.pending_fault:self._record_here();self._finish('failed',self.pending_fault);self._invalidate_control()
   self.pending_fault=None
  self._transitions();self.revision+=int(delta>0)

class NetzschLfa467Session:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,instance_id):
  if instance_id!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  meta=export_netzsch_lfa467_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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

OBSERVATIONS=['furnace.indicated_k','detector.indicated_k','pulse.indicated_w']
MOTION=['furnace.target_m','furnace.moving','sampler.target_x_m','sampler.target_y_m','sampler.moving']
GAS_STATES=[k for k in SLOTS if k.startswith('gas.') or k.startswith('lamp.') or k in ['truth.'+g+'_mol' for g in CHANNELS]]
ACQ=['acquisition.phase','acquisition.stage','acquisition.elapsed_s','acquisition.points']
EFFECTS={
 'plant.configure':['plant.configured','truth.furnace_k','thermal.target_k','thermal.final_target_k']+OBSERVATIONS,
 'sample.load':['sample.present','sample.position','truth.alpha_m2_s','truth.sample_mass_kg','truth.front_k','truth.rear_k','truth.mean_k','model.fault']+OBSERVATIONS,
 'sample.remove':['sample.present','sample.position','truth.alpha_m2_s','truth.sample_mass_kg','truth.front_k','truth.rear_k','truth.mean_k','model.fault']+OBSERVATIONS,
 'gas.load':GAS_STATES,'gas.unload':GAS_STATES,'gas.set_flow':GAS_STATES,'gas.set_open':GAS_STATES+['thermal.enabled'],
 'coolant.load':['truth.detector_coolant_kg','truth.furnace_coolant_kg','detector.ready']+OBSERVATIONS,
 'coolant.unload':['truth.detector_coolant_kg','truth.furnace_coolant_kg','detector.ready']+OBSERVATIONS,
 'power.set':['power.enabled','power.angle_rad','thermal.enabled','detector.enabled','detector.ready','truth.front_k']+ACQ+MOTION+GAS_STATES+OBSERVATIONS,
 'furnace.set':['furnace.target_m','furnace.moving','lamp.locked','lamp.unlocked'],
 'sampler.select':['sampler.position','sampler.target_x_m','sampler.target_y_m','sampler.moving'],
 'detector.set_enabled':['detector.enabled','detector.ready']+OBSERVATIONS,
 'pulse.charge':['truth.optical_stored_j'],'pulse.discharge':['truth.optical_stored_j'],
 'thermal.set_temperature':['thermal.enabled','thermal.final_target_k'],'thermal.disable':['thermal.enabled'],'thermal.set_cooling':['cooling.enabled'],
 'sensor.calibrate':OBSERVATIONS,'acquisition.start':ACQ+['thermal.enabled','thermal.final_target_k'],
 'acquisition.stop':ACQ+['thermal.enabled','truth.front_k']+OBSERVATIONS,
 'emergency.stop':['emergency.latched','thermal.enabled','detector.enabled','detector.ready','truth.front_k']+ACQ+MOTION+GAS_STATES+OBSERVATIONS,
 'emergency.reset':['emergency.latched'],'sim.step':list(SLOTS)}
for k,v in EFFECTS.items():CAPS[k]['effects']=list(dict.fromkeys(v))
ERRORS=sorted(set(__import__('re').findall(r"ActionRejected\('([A-Z_0-9]+)'",Path(__file__).read_text()))|{'TIMEOUT','GAS_EXHAUSTED','DETECTOR_COOLANT_EXHAUSTED','FURNACE_COOLANT_EXHAUSTED','SOFTWARE_STOP','PULSE_LINEAR_DOMAIN'})
def export_netzsch_lfa467_contract_metadata():
 slots={};caps={}
 for k,v in SLOTS.items():slots[k]=dict(meaning_zh=v['meaning'],component_ids=[v['component']],value_schema=copy.deepcopy(v['schema']),unit=v['unit'],reference_conditions='SI. Three gas flows require explicit reference T/P. Non-HT467, MCT173.15..773.15K or InSb ambient..773.15K. Engineered sensor quantization is not manufacturer accuracy.',origin=v['origin'],visibility=v['visibility'],access='read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'NetzschLfa467Session.read(state_ids=[{k!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description='Unique host nanosecond event clock; exact constant-power propagation with analytic internal thermal/IR samples. Reads do not advance time.'),source_ids=['simulation_assumption'])
 limits=['Only ordinary LFA467, not HT. One homogeneous opaque isotropic disk in sixteen-position12.7mm holder; no automatic batch or dynamic pan reparenting.','Finite rectangular pulse is an explicitly accepted waveform; no measured OEM pulse mapping, proprietary Cowan/Cape-Lehman algorithm, or hardware accuracy/noise certification.','Alpha interpolated only inside the real reference domain, sampled at equilibrated baseline and held fixed during small temperature-rise shot. Unknown density/Cp/absorptance/face losses and detector response are engineering inputs.','Finite gas exhaust is tracked per three branches; chamber mixing, pressure/vacuum and gas enthalpy not solved. Detector cooling is ideal regulated cold-front-end supplied by finite explicit LN2 budget; no empirical cooldown model.','Only none/LN2 furnace cooling implemented. Independent detector and furnace LN2 inventories; no artificial clipping to ambient. No comparative heat-capacity calibration.','SamplerXY/head paths are engineering geometry proxies, not a reconstruction of proprietary mechanics. Static scene specimen represents slot1; alternate loaded slots and dimensions lack dynamic geometry replacement.']
 for k,c in CAPS.items():
  un=k in UNSUPPORTED;async_=k=='acquisition.start';caps[k]={key:copy.deepcopy(c[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']}
  caps[k].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ['scene','robot','hardware'] else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.netzsch_lfa467.NetzschLfa467Session.invoke',invocation=f'session.invoke(instance_id="lfa467_01",capability_id={k!r},arguments={c["example_arguments"]!r},request_id="unique")'),preconditions=[] if c['kind'] in ['query','analysis'] or un else [dict(description_zh='冷态开炉装卸、有限三路气源/双LN2/光能预算、源温区与脉冲/采样边界，原子拒绝',state_ids=['power.enabled','furnace.lift_m','sample.present','detector.ready','acquisition.phase'],enforced_by='runtime',check='atomic invocation with thermal, position, reference and resource guards')],state_effects=[dict(state_id=x,timing='immediate',description_zh='实际命令即时变化') for x in c['effects']],observes=[],model_effects=dict(mode='none',description_zh='四关节与六状态灯由独立场景验证；无数值机身显示',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='实际基温平衡后完成有限矩形脉冲及真实内部采样记录窗口' if async_ else '原子命令接受；有限运动/热响应等待宿主step',completion_state_ids=['acquisition.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','furnace','specimen','xenon_flash','ir_detector','gas','LN2'],partial_failure_zh='取消/故障冻结已采集IR及脉冲观测，保留空间温度场、已排气/蒸发LN2与未发射能量；不回滚物理状态。'),errors=[dict(code=e,meaning_zh=e,side_effects='partial' if async_ and e in ['TIMEOUT','GAS_EXHAUSTED','DETECTOR_COOLANT_EXHAUSTED','FURNACE_COOLANT_EXHAUSTED','SOFTWARE_STOP','PULSE_LINEAR_DOMAIN','REFERENCE_TEMPERATURE_RANGE'] else 'none') for e in ERRORS],verification=[],limitations=limits,source_ids=['manufacturer_specs','simulation_assumption'])
  if k in ['acquisition.start','acquisition.stop','emergency.stop','power.set','thermal.set_temperature','thermal.disable','thermal.set_cooling','furnace.set','sampler.select','gas.set_open','detector.set_enabled']:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='宿主推进有限运动/导热/观测/库存') for x in SLOTS if x not in ['plant.configured','power.enabled','power.angle_rad','sample.present','sample.position','emergency.latched']]
 mechanism=dict(component_ids=['controller','furnace','specimen','xenon_flash','ir_detector','detector_dewar'],state_ids=list(slots),meaning_zh='真实alpha参考、保能一维导热、独立IR观测及外部逆分析',implementation='lumped',equations=[
 'kappa=alpha*rho*Cp. Slab finite volumes C=rho*Cp*A*dx and intercell G=kappa*A/dx; furnace finite Cf. Half-cell Robin face G=h*A/(1+h*dx/(2*kappa)); rear surface=(Tlast+h*dx/(2*kappa)*Tf)/(1+h*dx/(2*kappa)).',
 'Front absorbed power enters first cell by fraction1/(1+hfront*dx/(2*kappa)), remainder enters furnace via face Robin path; surface direct temperature includes Pabs/(2*kappa*A/dx+hfront*A). Uniform absorption is assumed over disk front face.',
 'Capacity-scaled symmetric thermal graph is diagonalized; exact constant-power propagator gives temperatures, stored energy, heat-loss integral and internally timed rear observations. Every ungrounded connected component has an explicit sqrt(C)-weighted null mode, preserving uniform equilibrium even for thin/high-alpha stiff limits.',
 'Controller predicts next fixed10ms-grid furnace state and uses bounded P=K*(target_end-Tfree_end)/(1+K*dTf_end/dP). None cooler has no negative heater; passive/forced heat goes to ambient. LN2 cooling consumes exactly removedQ/(latent*efficiency), in an independent furnace tank.',
 'Optical budget<=10J: loaded=remaining+emitted+recovered; emitted=absorbed+reflected. Finite10..1500us rectangular pulse is1us-grid input; partial cancellation keeps consumed energy. Sample mass is unchanged by ideal heat diffusion; removal exports remaining sensible energy.',
 'IR first-order detector dy/dt=(gain*rear_delta+offset-y)/tau is propagated by exact modal convolution, then delta-temperature calibration ycorrected=Tamb+(ydelta-zero)*gain, quantization and range checked. Thermal and pulse observations have explicit internal timestamps up to2MHz; acquisition is not an instantaneous0.1s-host sample.',
 'Three finite gas branches consume Qref*Pref/(R*Tref)*dt and exhaust accounting. Two independent detector/furnace LN2 inventories use explicit latent and efficiency. Residual sub-nanosecond resource quantities are exhausted at the event-clock resolution and recorded, not replenished.',
 'Parker external half-height estimator alpha=Fo_half*L^2/t_half uses numerically solved Fo_half=.13878529704272033, supplied plateau/baseline, and real bracket only; finite pulse/heat loss/lag are not silently corrected.',
 'External finite-pulse inverse analysis fits alpha plus nuisance amplitude/offset/linear baseline from supplied time-temperature observations. User supplies pulse width, detector tau, volumetric capacity and front/rear losses. Fixed surroundings is an analysis approximation; no host true alpha or OEM correction algorithm is accessed.',
 'Host integer-nanosecond clock is unique. Unconfigured host advances only clock/revision. step(0) is pure. Internal exact pulse/resource/sample events permit cancellation after5us within a10us pulse without advancing a separate hidden clock.'
 ],parameters={},source_ids=['manufacturer_specs','simulation_assumption','nist_sic','nist_alumina','parker1961'],limitations=limits)
 spec=importlib.util.spec_from_file_location('_lfa_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()});ann.annotate_export(SLUG,caps,slots,{'flash_conservation':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'gas.load':(GAS,GAS_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sensor.calibrate':(CALIBRATION,CALIBRATION_EXAMPLE)})
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'flash_conservation':mechanism})

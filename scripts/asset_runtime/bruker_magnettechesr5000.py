"""Magnettech ESR5000: literature HFC parameters, explicit CW-EPR numerical proxy."""
from __future__ import annotations
import copy,hashlib,importlib.util,json,math
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2];SLUG='bruker_magnettechesr5000';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-069';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/bruker_magnettechesr5000_epr_library/templates.json'
BOOL={'type':'boolean'};TEXT={'type':'string'}
class ActionRejected(ValueError):pass
def number(unit,low=None,high=None):
 s={'type':'number','x-unit':unit}
 if low is not None:s['minimum']=low
 if high is not None:s['maximum']=high
 return s
def obj(p):return dict(type='object',properties=p,required=list(p),additionalProperties=False)
def arr(s,n=None):
 x=dict(type='array',items=s)
 if n is not None:x.update(minItems=n,maxItems=n)
 return x
def integer(low,high):return dict(number('1',low,high),type='integer')
def validate(s,v):
 if 'anyOf' in s:
  if v is None and any(q.get('type')=='null' for q in s['anyOf']):return
  return validate(next(q for q in s['anyOf'] if q.get('type')!='null'),v)
 t=s.get('type')
 if t in ('number','integer'):
  if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):raise ActionRejected('INVALID_ARGUMENT')
  if t=='integer' and type(v) is not int:raise ActionRejected('INVALID_ARGUMENT')
  if not s.get('minimum',-math.inf)<=v<=s.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='boolean' and type(v) is not bool:raise ActionRejected('INVALID_ARGUMENT')
 elif t=='string' and not isinstance(v,str):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='object':
  if not isinstance(v,dict) or not set(s['required'])<=v.keys():raise ActionRejected('INVALID_ARGUMENT')
  for k,x in v.items():
   if k not in s['properties']:raise ActionRejected('INVALID_ARGUMENT')
   validate(s['properties'][k],x)
 elif t=='array':
  if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
  for x in v:validate(s['items'],x)
 if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')

PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
H_PLANCK=6.62607015e-34;MU_B=9.2740100657e-24;AVOGADRO=6.02214076e23
PLANT=obj({'ambient_k':number('K',273,313),'temperature_accessory':BOOL,'heat_capacity_j_k':number('J/K',1,1e5),'thermal_ua_w_k':number('W/K',.001,100),'control_gain_w_k':number('W/K',.01,1000),'peltier_limit_w':number('W',.1,1000),'noise_density_sqrt_s':number('s^0.5',0,1),'baseline':number('1',-100,100),'baseline_slope_per_t':number('1/T',-1e4,1e4),'reference_spin_count':number('1',1e6,1e25),'shape_scale_t2':number('T^2',1e-15,1),'seed':integer(0,2147483647),'accept_uncalibrated_model':BOOL})
SAMPLE=obj({'sample_id':TEXT,'kind':dict(type='string',enum=['blank','sample']),'material_phase':dict(type='string',enum=['liquid','solid']),'template_id':TEXT,'solvent_code':TEXT,'volume_m3':number('m^3',0,1e-5),'mass_kg':number('kg',0,.1),'concentration_mol_m3':number('mol/m^3',0,1000),'solid_spin_count':number('1',0,1e25),'g_assumed':nullable(number('1',1.5,3.)),'linewidth_pp_t':number('T',1e-5,.01),'saturation_power_w':number('W',1e-7,100),'reference_temperature_k':number('K',273,313),'tube_od_m':{'type':'number','enum':[.003,.004,.005,.006],'x-unit':'m'},'accept_missing_source_conditions':BOOL,'accept_synthetic_solid':BOOL})
METHOD=obj({'field_center_t':number('T',-.01,.65),'sweep_width_t':number('T',1e-5,.66),'microwave_frequency_hz':number('Hz',8e9,12e9),'microwave_power_w':number('W',1e-6,.1),'modulation_frequency_hz':{'type':'number','enum':[1e4,1e5],'x-unit':'Hz'},'modulation_pp_t':number('T',1e-7,.005),'points':integer(16,4096),'dwell_s':number('s',.001,100),'lockin_time_constant_s':number('s',0,10),'gain':number('1',.001,1000)})
PLANT_EXAMPLE=dict(ambient_k=298.15,temperature_accessory=True,heat_capacity_j_k=50.,thermal_ua_w_k=1.,control_gain_w_k=20.,peltier_limit_w=15.,noise_density_sqrt_s=0.,baseline=.01,baseline_slope_per_t=.02,reference_spin_count=1e12,shape_scale_t2=1e-8,seed=69,accept_uncalibrated_model=True)
SAMPLE_EXAMPLE=dict(sample_id='sample_01',kind='sample',material_phase='liquid',template_id='niehs_109',solvent_code='BZ',volume_m3=1e-7,mass_kg=0.,concentration_mol_m3=.0001,solid_spin_count=0.,g_assumed=2.0055,linewidth_pp_t=.0004,saturation_power_w=.1,reference_temperature_k=298.15,tube_od_m=.005,accept_missing_source_conditions=True,accept_synthetic_solid=False)
METHOD_EXAMPLE=dict(field_center_t=H_PLANCK*9.5e9/(MU_B*2.0055),sweep_width_t=.016,microwave_frequency_hz=9.5e9,microwave_power_w=.001,modulation_frequency_hz=1e5,modulation_pp_t=.00005,points=129,dwell_s=.01,lockin_time_constant_s=0.,gain=1.)
FAST_PLANT=copy.deepcopy(PLANT_EXAMPLE)
BLANK_EXAMPLE=dict(SAMPLE_EXAMPLE,sample_id='blank_01',kind='blank',template_id='',concentration_mol_m3=0.,solid_spin_count=0.,mass_kg=0.)
POINT=obj({'field_t':number('T'),'intensity':number('1'),'raw_intensity':number('1'),'sample_time_s':number('s',0),'integration_s':number('s',0),'elapsed_s':number('s',0),'temperature_k':number('K'),'estimated_sigma':number('1',0)})
MEASURE=obj({'phase':PHASE,'field_t':nullable(number('T')),'intensity':nullable(number('1')),'elapsed_s':number('s',0),'completed_points':integer(0,4096),'warning':TEXT})
RESULT=obj({'phase':PHASE,'kind':TEXT,'complete':BOOL,'points':arr(POINT),'settings_json':TEXT,'warning':TEXT,'evidence':TEXT,'limitations':arr(TEXT)})
ANALYSIS=obj({'g_apparent':nullable(number('1')),'center_field_t':nullable(number('T')),'linewidth_pp_t':nullable(number('T')),'double_integral_t2':number('T^2'),'edge_fraction':number('1',0),'evidence':TEXT})
RELATIVE=obj({'relative_spin_number':number('1'),'relative_spin_concentration':number('1'),'reference_integral_t2':number('T^2'),'sample_integral_t2':number('T^2'),'evidence':TEXT})
TRACE=obj({'field_t':arr(number('T')),'intensity':arr(number('1'))})
LEDGER=obj({**{k:number(u) for k,u in [('registered_volume_m3','m^3'),('attached_volume_m3','m^3'),('external_volume_m3','m^3'),('volume_residual_m3','m^3'),('registered_solute_mol','mol'),('attached_solute_mol','mol'),('external_solute_mol','mol'),('solute_residual_mol','mol'),('registered_solid_kg','kg'),('attached_solid_kg','kg'),('external_solid_kg','kg'),('mass_residual_kg','kg'),('registered_spins','1'),('attached_spins','1'),('external_spins','1'),('spin_residual','1'),('thermal_delta_j','J'),('controller_energy_j','J'),('ambient_loss_j','J'),('thermal_residual_j','J'),('rf_source_energy_j','J')]},'active_template_id':TEXT,'evidence':TEXT})
TEMPLATE=obj({'substance_id':TEXT,'name':TEXT,'hyperfine_t':arr(number('T'),2),'g_iso':nullable(number('1')),'source_linewidth_pp_t':nullable(number('T')),'conditions_json':TEXT,'source_json':TEXT,'evidence':TEXT})
DEMO_TRACE=dict(field_t=np.linspace(.33,.35,33).tolist(),intensity=(-np.linspace(-1,1,33)*np.exp(-25*np.linspace(-1,1,33)**2)).tolist())
CAPS={}
def cap(k,inp,out,c,z,kind='device_command',vis='agent',ex=None):CAPS[k]=[inp,out,c,z,kind,vis,{} if ex is None else ex,[]]
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body','lockin'],'宿主一次性声明未标定控制/噪声参数','configuration','evaluator',PLANT_EXAMPLE)
cap('sample.register',SAMPLE,obj({'registered':BOOL}),['sample_holder'],'登记预装液相真实HFC或显式合成各向同性固体管','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.load',obj({'sample_id':TEXT}),obj({'loaded':BOOL,'attachment_event':TEXT}),['sample_holder','quartz_tube'],'开盖宿主挂接完整管，固定网格非机器人动作','device_command','evaluator',{'sample_id':'sample_01'})
cap('sample.unload',obj({'recipient_id':TEXT}),obj({'unloaded':BOOL,'attachment_event':TEXT}),['sample_holder','quartz_tube'],'开盖卸载完整管，物料/自旋不消耗','device_command','evaluator',{'recipient_id':'external_rack'})
cap('method.configure',METHOD,obj({'configured':BOOL}),['magnet_yoke','microwave_bridge','lockin'],'场中心/宽度、功率/频率、调制pp和频率、点数/积分与检测参数','configuration','agent',METHOD_EXAMPLE)
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_face','power_inlet'],'软件供电；断电关闭微波并取消，非物理按键碰撞',ex={'enabled':True})
cap('lid.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['sample_lid'],'样品盖+Z30mm工程代理，开盖关闭微波并取消',ex={'opened':False})
cap('microwave.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['microwave_bridge'],'供电关盖才可使能；停微波取消，非安全认证',ex={'enabled':True})
cap('thermal.set_temperature',obj({'temperature_k':number('K',273,313)}),obj({'temperature_k':number('K')}),['sample_holder'],'只有宿主明确安装BTC03；273..313K温控数值代理',ex={'temperature_k':298.15})
cap('acquisition.start',obj({'kind':dict(type='string',enum=['blank','sample'])}),obj({'phase':PHASE,'operation_id':TEXT}),['quartz_tube','magnet_yoke','lockin'],'恒场逐点异步扫描；先完整同条件空白',ex={'kind':'sample'})
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['microwave_bridge'],'停止保留部分点与时间/能量')
cap('process.measure',obj({}),MEASURE,['host_screen'],'读最后真实采样点；缺测null','query')
cap('spectrum.read',obj({}),RESULT,['host_screen'],'导出文献参数驱动的模拟EPR谱；不伪造raw实测','query')
cap('spectrum.analyze',obj({'trace':TRACE,'microwave_frequency_hz':number('Hz',8e9,12e9),'isolated_line_assertion':BOOL}),ANALYSIS,['host_screen'],'仅由调用者观测双积分、表观g；非孤立多重峰不报单线线宽','analysis','agent',{'trace':DEMO_TRACE,'microwave_frequency_hz':9.5e9,'isolated_line_assertion':False})
cap('quantify.relative',obj({'reference':TRACE,'sample':TRACE,'reference_volume_m3':number('m^3',1e-12,1e-3),'sample_volume_m3':number('m^3',1e-12,1e-3),'same_settings_confirmed':BOOL}),RELATIVE,['host_screen'],'相同设置/温度/相位/饱和条件下双积分和体积比；无隐藏浓度','analysis','agent',{'reference':DEMO_TRACE,'sample':dict(field_t=DEMO_TRACE['field_t'],intensity=[2*v for v in DEMO_TRACE['intensity']]),'reference_volume_m3':1e-7,'sample_volume_m3':1e-7,'same_settings_confirmed':True})
cap('template.read',obj({'substance_id':TEXT}),TEMPLATE,['host_screen'],'公开参考参数，不透露当前样品身份','query','agent',{'substance_id':'niehs_109'})
cap('product.read',obj({}),LEDGER,['sample_holder'],'私有管内物料/自旋位置与独立热量/RF源能量账本','query','evaluator')
cap('powder.measure',obj({}),obj({}),['resonator'],'真实各向异性粉末/多电子Hamiltonian未实现','analysis')
cap('spincount.absolute',obj({}),obj({}),['lockin'],'原厂reference-free绝对SpinCount未实现','analysis')
UNSUPPORTED={'powder.measure','spincount.absolute'}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='host_screen',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','显式宿主配置完成','setpoint',c='body')
slot('method.configured',BOOL,'1','EPR扫描方法配置','setpoint')
slot('power.enabled',BOOL,'1','逻辑供电','setpoint',c='power_face',w=('power.set','enabled'))
slot('lid.opened',BOOL,'1','样品口开启','scene',c='sample_lid',w=('lid.set','opened'))
slot('lid.travel_m',number('m',0,.03),'m','样品口盖工程平移30mm','scene',c='sample_lid')
slot('microwave.enabled',BOOL,'1','微波门控软件状态','setpoint',c='microwave_bridge',w=('microwave.set','enabled'))
slot('sample.loaded',BOOL,'1','宿主挂接预装石英管','scene',c='quartz_tube')
slot('thermal.installed',BOOL,'1','BTC03选件宿主声明安装','setpoint',c='sample_holder')
slot('thermal.setpoint_k',nullable(number('K',273,313)),'K','选件温度设置','setpoint',c='sample_holder',w=('thermal.set_temperature','temperature_k'))
slot('thermal.temperature_k',number('K'),'K','未标定集中模型样品温度','derived',c='sample_holder')
slot('blank.ready',BOOL,'1','同方法/溶剂/参考温度空白')
slot('scan.phase',PHASE,'1','异步采集生命周期')
slot('scan.elapsed_s',number('s',0),'s','本次采集推进时间')
slot('scan.completed_points',integer(0,4096),'1','已完成点数')
slot('scan.field_t',nullable(number('T')),'T','最后实际采样点磁场')
slot('scan.intensity',nullable(number('1')),'1','相对EPR一阶谐波数值，非原厂定标')
slot('method.power_w',nullable(number('W',1e-6,.1)),'W','微波功率设置，非样品B1','setpoint',c='microwave_bridge')
slot('method.modulation_hz',nullable(number('Hz')),'Hz','调制频率，电子学频响未校准','setpoint',c='lockin')
slot('method.frequency_hz',nullable(number('Hz')),'Hz','宿主提供X波段频率，非厂家固定频率','setpoint',c='microwave_bridge')
slot('safety.warning',TEXT,'1','不泄漏身份的告警')
slot('truth.ledger',LEDGER,'1','物料/自旋、模板身份与能量私有账本','process_truth','evaluator','sample_holder')
class BrukerMagnettechesr5000Runtime:
 def __init__(self,instance_id='esr5000_01'):
  self.instance_id=instance_id;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.time_s=0.;self.revision=0;self.plant=None;self.method=None;self.samples={};self.active=None
  self.powered=False;self.opened=True;self.microwave=False;self.temperature_k=298.15;self.setpoint_k=None;self.controller_energy_j=0.;self.ambient_loss_j=0.;self.rf_source_energy_j=0.
  self.blank=None;self.phase='idle';self.kind='';self.elapsed_s=0.;self.points=[];self.warning='';self.operation_id='';self._op_count=0;self._schedule=[];self._due=0.;self._index=0;self._filter=0.;self._filter_integral=0.
  self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._operation_records={};self._rng=np.random.default_rng(0)
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return BrukerMagnettechesr5000Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def debug_snapshot(self):
  v=copy.deepcopy({k:x for k,x in self.__dict__.items() if k not in ['templates','_rng']});v['_rng_state']=copy.deepcopy(self._rng.bit_generator.state);return v
 def _sample(self):return self.samples.get(self.active)
 def _spins(self,s):return s['volume_m3']*s['concentration_mol_m3']*AVOGADRO if s['material_phase']=='liquid' else s['solid_spin_count']
 def _ledger(self):
  active=self._sample();r={}
  for prefix,unit,fn in [('volume','m3',lambda s:s['volume_m3']),('solute','mol',lambda s:s['volume_m3']*s['concentration_mol_m3']),('solid','kg',lambda s:s['mass_kg'])]:
   total=sum(fn(s) for s in self.samples.values());inside=fn(active) if active else 0.;r['registered_'+prefix+'_'+unit]=total;r['attached_'+prefix+'_'+unit]=inside;r['external_'+prefix+'_'+unit]=total-inside
  r.update(volume_residual_m3=r['registered_volume_m3']-r['attached_volume_m3']-r['external_volume_m3'],solute_residual_mol=r['registered_solute_mol']-r['attached_solute_mol']-r['external_solute_mol'],mass_residual_kg=r['registered_solid_kg']-r['attached_solid_kg']-r['external_solid_kg'])
  total=sum(self._spins(s) for s in self.samples.values());inside=self._spins(active) if active else 0.;r.update(registered_spins=total,attached_spins=inside,external_spins=total-inside,spin_residual=total-inside-(total-inside))
  delta=self.plant['heat_capacity_j_k']*(self.temperature_k-self.plant['ambient_k']) if self.plant else 0.
  r.update(thermal_delta_j=delta,controller_energy_j=self.controller_energy_j,ambient_loss_j=self.ambient_loss_j,thermal_residual_j=delta-self.controller_energy_j+self.ambient_loss_j,rf_source_energy_j=self.rf_source_energy_j,active_template_id=active['template_id'] if active else '',evidence='complete prefilled tube conserved; spin population count not consumed by steady-state EPR; independent lumped thermal balance; RF source energy boundary only, not sample RF heat or full electromagnetic conservation')
  return r
 def _fingerprint(self):
  s=self._sample();return json.dumps([self.method,s['material_phase'],s['solvent_code'],s['reference_temperature_k'],self.setpoint_k],sort_keys=True)
 def _blank_ok(self):return bool(self.blank and self.active and self.blank['fingerprint']==self._fingerprint())
 def read(self,visibility='agent'):
  if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
  p=self.points[-1] if self.points else {};m=self.method
  v={'plant.configured':bool(self.plant),'method.configured':bool(m),'power.enabled':self.powered,'lid.opened':self.opened,'lid.travel_m':.03 if self.opened else 0.,'microwave.enabled':self.microwave,'sample.loaded':bool(self.active),'thermal.installed':bool(self.plant and self.plant['temperature_accessory']),'thermal.setpoint_k':self.setpoint_k,'thermal.temperature_k':self.temperature_k,'blank.ready':self._blank_ok(),'scan.phase':self.phase,'scan.elapsed_s':self.elapsed_s,'scan.completed_points':len(self.points),'scan.field_t':p.get('field_t'),'scan.intensity':p.get('intensity'),'method.power_w':m['microwave_power_w'] if m else None,'method.modulation_hz':m['modulation_frequency_hz'] if m else None,'method.frequency_hz':m['microwave_frequency_hz'] if m else None,'safety.warning':self.warning,'truth.ledger':self._ledger()}
  return copy.deepcopy({k:x for k,x in v.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _archive_operation(self):
  if self.operation_id and self.phase in ('succeeded','cancelled','failed') and self.operation_id not in self._operation_records:
   self._operation_records[self.operation_id]=dict(operation_id=self.operation_id,status=self.phase,result=copy.deepcopy(self._invoke('process.measure',{})))
 def _invalidate(self):
  self._archive_operation();self.blank=None;self.points=[];self.phase='idle';self.kind='';self.elapsed_s=0.;self.warning=''
 def _stop(self,phase,warning):
  if self.phase=='running':self.phase=phase
  self.microwave=False;self.warning=warning;self._archive_operation()
 def _ready(self):
  if not self.plant or not self.powered or self.opened:raise ActionRejected('RF_NOT_READY')
 def _grid(self):return np.linspace(self.method['field_center_t']-self.method['sweep_width_t']/2,self.method['field_center_t']+self.method['sweep_width_t']/2,self.method['points'])
 def _profile(self,fields,quadrature=256):
  s=self._sample();m=self.method
  if s['kind']=='blank':return np.zeros_like(np.asarray(fields,dtype=float))
  template=self.templates[s['template_id']] if s['material_phase']=='liquid' else None
  g=template['g_iso'] if template and template['g_iso'] is not None else s['g_assumed'];b0=H_PLANCK*m['microwave_frequency_hz']/(MU_B*g)
  if template:
   an,ah=template['hyperfine_t'];centers=np.asarray([b0-mn*an-mh*ah for mn in [-1,0,1] for mh in [-.5,.5]])
   if max(an,ah)/b0>.02:raise ActionRejected('HIGH_FIELD_MODEL_LIMIT')
  else:centers=np.asarray([b0])
  sat=m['microwave_power_w']/s['saturation_power_w'];gamma=math.sqrt(3)*s['linewidth_pp_t']/2*math.sqrt(1+sat)
  # Normalized Lorentzian has area1; PP=FWHM/sqrt(3). Saturation broadens and reduces integrated absorption.
  amp=(self._spins(s)/self.plant['reference_spin_count'])*math.sqrt(m['microwave_power_w']/.001)/math.sqrt(1+sat)
  fields=np.asarray(fields,dtype=float);theta=(np.arange(quadrature)+.5)*(2*math.pi/quadrature);co=np.cos(theta);a=m['modulation_pp_t']/2
  if a/gamma>8:raise ActionRejected('MODULATION_MODEL_LIMIT')
  x=fields[:,None,None]+a*co[None,:,None]-centers[None,None,:]
  lorentz=gamma/(math.pi*(x*x+gamma*gamma));h1=(2/a)*np.mean(np.mean(lorentz,axis=2)*co[None,:],axis=1)
  return h1*amp*self.plant['shape_scale_t2']*m['gain']
 @staticmethod
 def analyze(trace,frequency_hz,isolated=False):
  validate(TRACE,trace);b=np.asarray(trace['field_t'],dtype=float);y=np.asarray(trace['intensity'],dtype=float)
  if len(b)!=len(y) or not 16<=len(b)<=4096 or np.any(b[1:]<=b[:-1]):raise ActionRejected('ANALYSIS_INPUT')
  # Baselines are linear in magnetic field, including nonuniform sample grids.
  # Normalize first, then convert the area back to T^2 with a finite-value guard.
  try:
   with np.errstate(over='raise',invalid='raise',divide='raise'):
    span=float(b[-1]-b[0]);u=(b-b[0])/span;scale=float(np.max(np.abs(y))) or 1.;yn=y/scale
    d=yn-(yn[0]+(yn[-1]-yn[0])*u)
    if float(np.max(np.abs(d)))<=32*np.finfo(float).eps:d=np.zeros_like(d)
    absorption=np.r_[0.,np.cumsum((d[1:]+d[:-1])*.5*np.diff(u))]
    absorption-=absorption[0]+(absorption[-1]-absorption[0])*u
    normalized_area=float(np.trapezoid(absorption,u));area=float(np.longdouble(normalized_area)*np.longdouble(scale)*np.longdouble(span)**2)
    edge=max(abs(float(yn[0])),abs(float(yn[-1])))
    center=float(b[0]+span*np.trapezoid(absorption*u,u)/normalized_area) if area>1e-30 else None
    if center is not None and (center<=0 or center<b[0] or center>b[-1]):center=None
    width=float(abs(b[np.argmax(d)]-b[np.argmin(d)])) if isolated and float(np.ptp(d))>1e-20/scale else None
    g=H_PLANCK*frequency_hz/(MU_B*center) if center else None
    if not all(v is None or math.isfinite(v) for v in [area,edge,center,width,g]):raise ArithmeticError
  except (ArithmeticError,ValueError):raise ActionRejected('ANALYSIS_INPUT') from None
  return dict(g_apparent=g,center_field_t=center,linewidth_pp_t=width,double_integral_t2=area,edge_fraction=edge,evidence='caller-observed first-harmonic trace, field-coordinate endpoint linear baselines and trapezoidal double integration; apparent centroid g; linewidth only caller-asserted isolated line; finite-window/noise/calibration bias uncorrected')

 def invoke(self,capability_id,**arguments):
  if capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability_id][0],arguments)
  if capability_id in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  if CAPS[capability_id][4] in ['query','analysis']:return copy.deepcopy(self._invoke(capability_id,copy.deepcopy(arguments)))
  old={k:copy.deepcopy(v) for k,v in self.__dict__.items() if k!='templates'};templates=self.templates
  try:result=self._invoke(capability_id,copy.deepcopy(arguments));self.revision+=1;return copy.deepcopy(result)
  except Exception:self.__dict__.clear();self.__dict__.update(old);self.templates=templates;raise
 def _invoke(self,k,a):
  if k=='template.read':
   r=self.templates.get(a['substance_id'])
   if r is None:raise ActionRejected('UNKNOWN_REFERENCE')
   return {**{q:r[q] for q in ['substance_id','name','hyperfine_t','g_iso','source_linewidth_pp_t','evidence']},'conditions_json':json.dumps(r['conditions'],ensure_ascii=False),'source_json':json.dumps(r['source'],ensure_ascii=False)}
  if k=='product.read':return self._ledger()
  if k=='process.measure':
   p=self.points[-1] if self.points else {};return dict(phase=self.phase,field_t=p.get('field_t'),intensity=p.get('intensity'),elapsed_s=self.elapsed_s,completed_points=len(self.points),warning=self.warning)
  if k=='spectrum.read':return dict(phase=self.phase,kind=self.kind,complete=self.phase=='succeeded',points=self.points,settings_json=json.dumps(self.method),warning=self.warning,evidence='liquid: literature HFC (+g if provided) with simulated first-order CW spectrum; solid: explicitly host-defined synthetic isotropic line; blank/noise/saturation/instrument transfer simulated',limitations=['Not measured raw spectra or OEM SpinFit/SpinCount','Only two dominant isotropic HFC terms; no anisotropic powder or multispin Hamiltonian','Constant-field dwell and instantaneous retuning; 4096-point host ceiling not manufacturer capacity','10/100kHz selectable but frequency-dependent electronics not modeled','Unknown source temperatures/g/linewidth are explicit host assumptions; source solvent required','Finite window, baseline, lock-in/modulation and saturation affect apparent g/linewidth/double integral'])
  if k=='spectrum.analyze':return self.analyze(a['trace'],a['microwave_frequency_hz'],a['isolated_line_assertion'])
  if k=='quantify.relative':
   if not a['same_settings_confirmed'] or a['reference']['field_t']!=a['sample']['field_t']:raise ActionRejected('REFERENCE_CONDITIONS_MISMATCH')
   ref=self.analyze(a['reference'],9.5e9)['double_integral_t2'];sample=self.analyze(a['sample'],9.5e9)['double_integral_t2']
   if ref<=1e-25 or sample<0:raise ActionRejected('INVALID_REFERENCE_INTEGRAL')
   ratio=sample/ref;concentration=ratio*(a['reference_volume_m3']/a['sample_volume_m3'])
   if not math.isfinite(ratio) or not math.isfinite(concentration):raise ActionRejected('INVALID_REFERENCE_INTEGRAL')
   return dict(relative_spin_number=ratio,relative_spin_concentration=concentration,reference_integral_t2=ref,sample_integral_t2=sample,evidence='caller-observed double-integral ratio and declared volumes; same gain/power/saturation/temperature/modulation/phase/resonator loading required and caller-asserted; no hidden concentration or absolute spin calibration')
  if k=='plant.configure':
   if self.plant or self.powered or not a['accept_uncalibrated_model']:raise ActionRejected('INITIALIZATION_ONLY')
   self.plant=a;self.temperature_k=a['ambient_k'];self.setpoint_k=a['ambient_k'] if a['temperature_accessory'] else None;self._rng=np.random.default_rng(a['seed']);return {'configured':True}
  if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
  if self.phase=='running' and k in ['sample.register','sample.load','sample.unload','method.configure','thermal.set_temperature']:raise ActionRejected('BUSY')
  if k=='sample.register':
   if a['sample_id'] in self.samples or not a['sample_id']:raise ActionRejected('REQUEST_CONFLICT')
   if not a['accept_missing_source_conditions']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   if a['kind']=='blank':
    if a['template_id'] or a['concentration_mol_m3'] or a['solid_spin_count'] or a['mass_kg']:raise ActionRejected('INVALID_ARGUMENT')
   elif a['material_phase']=='liquid':
    if a['template_id'] not in self.templates or a['concentration_mol_m3']<=0 or a['volume_m3']<=0 or a['mass_kg'] or a['solid_spin_count']:raise ActionRejected('UNKNOWN_REFERENCE')
    r=self.templates[a['template_id']]
    if a['solvent_code']!=r['conditions']['solvent_code']:raise ActionRejected('SOURCE_SOLVENT_MISMATCH')
    if r['g_iso'] is None and a['g_assumed'] is None:raise ActionRejected('MISSING_G_ASSUMPTION')
    if r['g_iso'] is not None and a['g_assumed'] is not None and abs(r['g_iso']-a['g_assumed'])>1e-10:raise ActionRejected('SOURCE_G_CONFLICT')
   else:
    if not a['accept_synthetic_solid'] or a['template_id'] or a['solvent_code'] or a['volume_m3'] or a['concentration_mol_m3'] or a['mass_kg']<=0 or a['solid_spin_count']<=0 or a['g_assumed'] is None:raise ActionRejected('SYNTHETIC_SOLID_ACK_REQUIRED')
   if a['material_phase']=='liquid' and a['volume_m3']<=0:raise ActionRejected('INVALID_ARGUMENT')
   self.samples[a['sample_id']]=a;return {'registered':True}
  if k=='sample.load':
   if not self.opened or self.active or a['sample_id'] not in self.samples:raise ActionRejected('SAMPLE_ACCESS_REQUIRED')
   self.active=a['sample_id'];return dict(loaded=True,attachment_event='host_attach_prefilled_quartz_tube')
  if k=='sample.unload':
   if not self.opened or not self.active or not a['recipient_id']:raise ActionRejected('SAMPLE_ACCESS_REQUIRED')
   self.active=None;return dict(unloaded=True,attachment_event='host_detach_intact_quartz_tube')
  if k=='method.configure':
   if a['field_center_t']-a['sweep_width_t']/2<-.01-1e-12 or a['field_center_t']+a['sweep_width_t']/2>.65+1e-12:raise ActionRejected('FIELD_RANGE')
   self.method=a;self._invalidate();return {'configured':True}
  if k=='power.set':
   self.powered=a['enabled']
   if not self.powered:self._stop('cancelled','POWER_OFF');self.blank=None
   return a
  if k=='lid.set':
   if a['opened']:self._stop('cancelled','LID_OPEN')
   self.opened=a['opened'];return a
  if k=='microwave.set':
   if a['enabled']:
    self._ready()
    if not self.method:raise ActionRejected('SAMPLE_METHOD_REQUIRED')
   elif self.phase=='running':self._stop('cancelled','RF_OFF')
   self.microwave=a['enabled'];return a
  if k=='thermal.set_temperature':
   if not self.plant['temperature_accessory']:raise ActionRejected('ACCESSORY_NOT_INSTALLED')
   self.setpoint_k=a['temperature_k'];self._invalidate();return a
  if k=='acquisition.stop':self._stop('cancelled','CANCELLED');return {'phase':self.phase}
  if k=='acquisition.start':
   self._ready()
   if not self.microwave:raise ActionRejected('RF_NOT_READY')
   if self.phase=='running':raise ActionRejected('BUSY')
   if not self.method or not self.active:raise ActionRejected('SAMPLE_METHOD_REQUIRED')
   s=self._sample()
   if s['kind']!=a['kind']:raise ActionRejected('SAMPLE_KIND_MISMATCH')
   if abs(self.temperature_k-s['reference_temperature_k'])>.25:raise ActionRejected('REFERENCE_TEMPERATURE_MISMATCH')
   if a['kind']=='sample' and not self._blank_ok():raise ActionRejected('BLANK_REQUIRED')
   self._archive_operation()
   if a['kind']=='blank':self.blank=None
   fields=self._grid();profile=self._profile(fields);p=self.plant;m=self.method
   self._schedule=[dict(field_t=float(b),target=float(v+p['baseline']+p['baseline_slope_per_t']*(b-m['field_center_t']))) for b,v in zip(fields,profile)]
   self.points=[];self.elapsed_s=0.;self._index=0;self._due=m['dwell_s'];self._filter=0.;self._filter_integral=0.;self.kind=a['kind'];self.phase='running';self.warning=''
   if m['sweep_width_t']/(m['points']-1)>s['linewidth_pp_t']/2:self.warning='UNDERSAMPLED_LINEWIDTH'
   self._op_count+=1;self.operation_id=self.instance_id+':'+str(self._op_count);return dict(phase=self.phase,operation_id=self.operation_id)
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _thermal(self,dt):
  p=self.plant
  if not p:return
  C=p['heat_capacity_j_k'];U=p['thermal_ua_w_k'];K=p['control_gain_w_k'];limit=p['peltier_limit_w'];ambient=p['ambient_k'];remaining=dt
  for _ in range(8):
   if remaining<=1e-13:break
   t=self.temperature_k;control=bool(self.powered and p['temperature_accessory']);feed=U*(self.setpoint_k-ambient) if control else 0.;demand=K*(self.setpoint_k-t)+feed if control else 0.
   if not control:aa=U*ambient/C;bb=U/C;boundary=None
   elif demand>limit+1e-10:aa=(limit+U*ambient)/C;bb=U/C;boundary=self.setpoint_k+(feed-limit)/K
   elif demand<-limit-1e-10:aa=(-limit+U*ambient)/C;bb=U/C;boundary=self.setpoint_k+(feed+limit)/K
   else:aa=(K+U)*self.setpoint_k/C;bb=(K+U)/C;boundary=None
   equilibrium=aa/bb;span=remaining
   if boundary is not None and abs(t-equilibrium)>1e-15:
    ratio=(boundary-equilibrium)/(t-equilibrium)
    if 0<ratio<1:
     hit=-math.log(ratio)/bb
     if hit>1e-13:span=min(span,hit)
   new=equilibrium+(t-equilibrium)*math.exp(-bb*span);integral=(equilibrium-ambient)*span+(t-equilibrium)*(-math.expm1(-bb*span))/bb
   loss=U*integral;self.ambient_loss_j+=loss;self.controller_energy_j+=C*(new-t)+loss;self.temperature_k=new;remaining-=span
  if remaining>1e-10:raise RuntimeError('thermal branch convergence')
 def _record(self):
  m=self.method;p=self.plant;e=self._schedule[self._index];sigma=p['noise_density_sqrt_s']*m['gain']/math.sqrt(m['dwell_s'])
  raw=self._filter_integral/m['dwell_s']+float(self._rng.normal(0.,sigma));blank=self.blank['points'][self._index] if self.kind=='sample' else None;value=raw-blank['raw_intensity'] if blank else raw
  self.points.append(dict(field_t=e['field_t'],intensity=value,raw_intensity=raw,sample_time_s=self.time_s,integration_s=m['dwell_s'],elapsed_s=self.elapsed_s,temperature_k=self.temperature_k,estimated_sigma=math.hypot(sigma,blank['estimated_sigma']) if blank else sigma))
  self._index+=1;self._filter_integral=0.
  if self._index==len(self._schedule):
   self.phase='succeeded';self.microwave=False
   if self.kind=='blank':self.blank=dict(fingerprint=self._fingerprint(),points=copy.deepcopy(self.points))
   self._archive_operation()
  else:self._due+=m['dwell_s']
 def step(self,dt_s):
  validate(number('s',0,86400),dt_s)
  if dt_s==0:return
  remaining=dt_s
  while remaining>1e-12:
   dt=min(remaining,max(0.,self._due-self.elapsed_s)) if self.phase=='running' else remaining
   if dt>1e-13:
    self._thermal(dt)
    if self.microwave and self.method:self.rf_source_energy_j+=self.method['microwave_power_w']*dt
    if self.phase=='running':
     target=self._schedule[self._index]['target'];tau=self.method['lockin_time_constant_s']
     if tau>0:
      d=-math.expm1(-dt/tau);self._filter_integral+=target*dt+(self._filter-target)*tau*d;self._filter=target+(self._filter-target)*(1-d)
     else:self._filter=target;self._filter_integral+=target*dt
     self.elapsed_s+=dt
    self.time_s+=dt;remaining-=dt
   if self.phase=='running' and abs(self.temperature_k-self._sample()['reference_temperature_k'])>.25:self._stop('failed','REFERENCE_TEMPERATURE_MISMATCH')
   if self.phase=='running' and self.elapsed_s+1e-12>=self._due:self._record()
   if dt<=1e-13 and self.phase!='running':self._thermal(remaining);self.time_s+=remaining;remaining=0.
  self.revision+=1
class BrukerMagnettechesr5000Session:
    """Only the trusted host assigns roles; returned data obey the same boundary."""
    def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
    @property
    def _role(self):return self._host._principals[self._principal]
    def _target(self,i):
        if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
    def describe(self,*,instance_id=None,asset_type_id=None):
        if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None:self._target(instance_id)
        if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
        m=export_bruker_magnettechesr5000_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.1',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='unavailable' if v[k] is None else 'estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=h.points[-1]['sample_time_s'] if k.startswith('scan.') and k in ['scan.intensity','scan.field_t'] and h.points else h.time_s,sample_window_s=[h.points[-1]['sample_time_s']-h.points[-1]['integration_s'],h.points[-1]['sample_time_s']] if k=='scan.intensity' and h.points else None) for k in ids})
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
        if operation_id in h._operation_records:return copy.deepcopy(h._operation_records[operation_id])
        if operation_id!=h.operation_id:raise ActionRejected('ACCESS_DENIED')
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('process.measure'))
    def cancel(self,operation_id,request_id):
        snapshot=self.poll(operation_id);h=self._host
        if not isinstance(request_id,str) or not request_id:raise ActionRejected('INVALID_ARGUMENT')
        key=(self._principal,h.instance_id,request_id);digest=json.dumps(['acquisition.cancel',operation_id])
        if key in h._requests:
            old,response=h._requests[key]
            if old!=digest:raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(response)
        result=h.invoke('acquisition.stop') if snapshot['status']=='running' else {'phase':snapshot['status']}
        response=dict(protocol_version=PROTOCOL_VERSION,instance_id=h.instance_id,request_id=request_id,operation_id=operation_id,status='succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None)
        h._requests[key]=(digest,copy.deepcopy(response));return response


def export_bruker_magnettechesr5000_contract_metadata():
 caps={};slots={}
 reset=['blank.ready','scan.phase','scan.elapsed_s','scan.completed_points','scan.field_t','scan.intensity','safety.warning']
 interrupt=['scan.phase','microwave.enabled','safety.warning']
 immediate={
  'plant.configure':['plant.configured','thermal.installed','thermal.setpoint_k','thermal.temperature_k'],
  'sample.register':['truth.ledger'],
  'sample.load':['sample.loaded','blank.ready','truth.ledger'],
  'sample.unload':['sample.loaded','blank.ready','truth.ledger'],
  'method.configure':['method.configured','method.power_w','method.modulation_hz','method.frequency_hz',*reset],
  'power.set':['power.enabled','blank.ready',*interrupt],
  'lid.set':['lid.opened','lid.travel_m',*interrupt],
  'microwave.set':interrupt,
  'thermal.set_temperature':['thermal.setpoint_k',*reset],
  'acquisition.start':reset,
  'acquisition.stop':interrupt,
 }
 on_step={
  'power.set':['thermal.temperature_k','truth.ledger'],
  'method.configure':['truth.ledger'],
  'microwave.set':['truth.ledger'],
  'thermal.set_temperature':['thermal.temperature_k','truth.ledger'],
  'acquisition.start':['scan.phase','scan.elapsed_s','scan.completed_points','scan.field_t','scan.intensity','safety.warning','truth.ledger','microwave.enabled'],
 }
 on_completion={'acquisition.start':['scan.phase','blank.ready','microwave.enabled']}
 observed={
  'plant.configure':['plant.configured'],'method.configure':['method.configured'],
  'sample.load':['sample.loaded'],'sample.unload':['sample.loaded'],
  'power.set':['power.enabled'],'lid.set':['lid.opened'],'microwave.set':['microwave.enabled'],
  'thermal.set_temperature':['thermal.setpoint_k'],'acquisition.start':['scan.phase'],'acquisition.stop':['scan.phase'],
  'process.measure':['scan.phase','scan.field_t','scan.intensity','scan.elapsed_s','scan.completed_points','safety.warning'],
  'spectrum.read':['scan.phase','scan.field_t','scan.intensity','scan.elapsed_s','scan.completed_points','method.power_w','method.modulation_hz','method.frequency_hz','safety.warning'],
  'product.read':['truth.ledger'],
 }
 def effects_for(key):
  descriptions={'immediate':'本次通过校验的控制/配置改变或清除该状态；不推进仿真时间','on_step':'后续宿主step推进对应温控、RF源能量或采集过程；设定返回不表示过程完成','on_completion':'全部采样完成后设置终态并关闭微波；仅blank完整采集提交有效空白'}
  return [dict(state_id=state,timing=timing,description_zh=descriptions[timing]) for timing,mapping in [('immediate',immediate),('on_step',on_step),('on_completion',on_completion)] for state in mapping.get(key,[])]
 for k,(s,u,o,v,c,w) in SLOTS.items():
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'BrukerMagnettechesr5000Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='interval_mean' if k=='scan.intensity' else 'instant',time_domain='simulation',window_description='恒场积分[t_point-dwell,t_point]，减同方法空白；不是整个step窗口' if k=='scan.intensity' else None),source_ids=['simulation_assumption'])
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  asynchronous=k=='acquisition.start';query=kind in ['query','analysis'];unsupported=k in UNSUPPORTED
  caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.bruker_magnettechesr5000.BrukerMagnettechesr5000Session.invoke',invocation=f'session.invoke(instance_id="esr5000_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if query or unsupported else [dict(description_zh='可信宿主配置、供电关盖微波使能、来源溶剂/温度匹配、有效同方法空白；温控须BTC03安装',state_ids=['power.enabled','lid.opened','microwave.enabled','thermal.installed','blank.ready','scan.phase'],enforced_by='runtime',check='atomic Runtime.invoke and trusted-principal Session')],
  state_effects=[] if query or unsupported else effects_for(k),
  observes=observed.get(k,[]),model_effects=dict(mode='none',description_zh='不支持选项无变化' if unsupported else '待独立场景验证',binding_ids=[]),
  execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',completion_criterion='全部恒场采样点完成，关闭微波门控' if asynchronous else '事务返回；只读/分析不推进时间',completion_state_ids=['scan.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',retry='same_request_id',resources=['host_clock','sample_holder','request_cache'],partial_failure_zh='开盖/断电/停微波/取消/温度越界保留部分点、已用时间与源能量；空白仅完整完成后有效。终态poll快照受原调用身份保护并在后续采集后保留；取消终态操作不停止新采集；历史和请求去重仅进程内。'),
  errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ["INVALID_ARGUMENT","UNKNOWN_INSTANCE","UNKNOWN_CAPABILITY","UNSUPPORTED","PRECONDITION_FAILED","ACCESS_DENIED","REQUEST_CONFLICT","UNKNOWN_REFERENCE","INITIALIZATION_ONLY","SAMPLE_ACCESS_REQUIRED","SAMPLE_METHOD_REQUIRED","EVIDENCE_ACK_REQUIRED","SOURCE_SOLVENT_MISMATCH","MISSING_G_ASSUMPTION","SOURCE_G_CONFLICT","SYNTHETIC_SOLID_ACK_REQUIRED","FIELD_RANGE","RF_NOT_READY","BLANK_REQUIRED","HIGH_FIELD_MODEL_LIMIT","MODULATION_MODEL_LIMIT","REFERENCE_TEMPERATURE_MISMATCH","SAMPLE_KIND_MISMATCH","ACCESSORY_NOT_INSTALLED","BUSY","ANALYSIS_INPUT","REFERENCE_CONDITIONS_MISMATCH","INVALID_REFERENCE_INTEGRAL"]],example_arguments=copy.deepcopy(ex),verification=[],limitations=['文献aN/aH驱动的一阶各向同性数值代理，不是原厂实机校准；粉末/SpinCount不支持'],source_ids=['manufacturer_primary','simulation_assumption'])
 mech=dict(component_ids=['magnet_yoke','resonator','microwave_bridge','lockin','quartz_tube','sample_holder','host_screen'],state_ids=list(slots),meaning_zh='文献HFC、显式缺失g/线宽假设、CW一阶调制、逐点积分/空白及条件化BTC03温控',implementation='lumped',
 equations=['a_T=1e-4*a_G; source g null unless explicitly reported; S=1/2 first-order high-field fast-motion liquid spin adduct only',
 'B0=h*nu/(g*mu_B); Bline=B0-mN*aN-mH*aH; mN=-1,0,1, mH=-1/2,+1/2; equal 1/6 intensities; require max(a)/B0<=0.02',
 'L(B)=gamma/[pi*((B-Bline)^2+gamma^2)]; intrinsic gamma=sqrt(3)*linewidth_pp/2; FWHM=sqrt(3)*PP, not FWHM=PP',
 's=P/Psat; gamma_eff=gamma*sqrt(1+s); integrated amplitude=(N/Nref)*sqrt(P/0.001W)/sqrt(1+s); Psat host assumption, no measured cavity B1/Q',
 'Finite modulation pp: a=modulation_pp/2; normalized first harmonic=(2/a)*mean(L(B+a*cos(theta))*cos(theta)); 256 midpoint phases; peak modulation/gamma<=8 resource/convergence guard; small-amplitude limit dL/dB',
 'Dimensionless relative signal=first_harmonic*shape_scale_T2*amplitude*gain; source spin count=N_A*c*V, solid count host-defined synthetic singlet',
 'Per-point steady target plus declared baseline; dfilter/dt=(target-filter)/tau; exact interval mean over dwell; tau=0 instantaneous; no field slew dynamics',
 'Noise Normal(0,host_density*gain/sqrt(dwell)); subtract completed matched blank; combined sigma by quadrature; signed signals retained',
 'C*dT/dt=clip(K*(set-T)+UA*(set-Tambient),-Pmax,+Pmax)-UA*(T-Tambient); exact piecewise affine integration; BTC03 only if explicitly installed',
 'Thermal C*deltaT=controller_energy-ambient_loss; RF source integral(P dt) separately reported, not coupled to sample heating or full electromagnetic energy',
 'Observed analysis uses endpoint derivative/absorption baselines linear in magnetic field (including nonuniform grids), scale-normalized trapezoidal double integration; g_app=h*nu/(mu_B*absorption_centroid); PP only for caller-asserted isolated line',
 'Relative spin number=double_integral_sample/double_integral_reference; relative concentration=number_ratio*Vref/Vsample; same settings/loading/temperature/saturation required; no hidden truth',
 'step is sole simulation clock; whole prefilled tube volume/solute/solid mass/spin number conserved; no reaction or spin trapping kinetics'],
 parameters={},source_ids=['manufacturer_primary','manufacturer_accessories','niehs_primary','easyspin_primary','easyspin_modulation','simulation_assumption'],
 limitations=['Factory -10..650mT,1uW..100mW,10/100kHz,45kg397W262D192Hmm; host4096 points is not factory>=250000 capability.',
 '60 reviewed distinct DMPO/PBN adducts contain measured literature dominant aN/aH; no raw measured curves; only11 explicit g; source temperature/linewidth/frequency null.',
 'Source solvent required, unknown temperature acknowledged with host reference +/-0.25K; thermal controller range not real spectral temperature coverage.',
 'Solid isotropic singlet is synthetic host reference only; anisotropic powder, slow-motion, exchange, multispin/ZFS and mixed spectra not implemented.',
 'Uncalibrated Psat/linewidth/g fallback/noise/gain/lock-in and instantaneous field steps; modulation-frequency electronics not modeled; no20pM sensitivity validation.',
 'Microwave safety and magnetic field enclosure are software interlocks/proxy geometry, not hardware or occupational safety certification.',
 'Fixed tube marker and host attachment, no physical liquid/robot contact or actual fill animation; simulation-only telemetry not native ESRStudio.',
 'Relative double integral subject to baseline/window/noise and caller conditions; numerical input that cannot yield finite SI analysis/ratios is rejected. Endpoint baselines are linear in magnetic field; residuals below floating-point relative resolution are zeroed. No reference-free absolute SpinCount or OEM SpinFit.',
 'Native async envelope/restart idempotency and file/container isolation not validated; no hardware driver/HIL or full controller GUI.'])
 spec=importlib.util.spec_from_file_location('_esr5000_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
 ann.annotate_export(SLUG,caps,slots,{'cw_epr':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.register':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'，不是实机标定。'
 mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'cw_epr':mech})


"""CHEM-113 SurPASS3 finite electrolyte, pressure decay, streaming current and pH titration."""
from pathlib import Path
import copy,math,json,importlib.util
PROTOCOL_VERSION='1.0.0';SLUG='antonpaar_surpass3';CATALOG_ID='CHEM-113';ASSET_TYPE_ID='instrument.'+SLUG
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
DT=.1;EPS0=8.8541878128e-12
PLANT=obj({'temperature_k':number('K',293.15,313.15),'viscosity_pa_s':number('Pa*s',.0005,.002),'relative_permittivity':number('1',60,90),'kw_mol2_m6':number('mol^2/m^6',1e-9,1e-7),'molar_conductivities':arr(number('S*m^2/mol',.001,.1),4),'ambient_pa':number('Pa',8e4,12e4),'gas_volume_m3':number('m^3',1e-5,.0001),'extra_resistance_pa_s_m3':number('Pa*s/m^3',0,1e13),'surface_conductance_s':number('S',0,.01),'dose_flow_m3_s':number('m^3/s',1e-10,1e-7),'ph_slope_v':number('V',-.0615,-.0568),'ph_offset_v':number('V',-.03,.03),'conductivity_gain':number('1',.5,2),'voltage_offset_v':number('V',-.1,.1),'current_offset_a':number('A',-1e-5,1e-5),'voltage_gain':number('1',.5,2),'current_gain':number('1',.5,2),'blocked_fraction':number('1',0,1),'accept_engineering_model':BOOL})
PLANT_EXAMPLE=dict(temperature_k=298.15,viscosity_pa_s=.000890,relative_permittivity=78.3,kw_mol2_m6=1e-8,molar_conductivities=[.00693,.00720,.034965,.0198],ambient_pa=101325.,gas_volume_m3=5e-5,extra_resistance_pa_s_m3=0.,surface_conductance_s=0.,dose_flow_m3_s=1e-8,ph_slope_v=-.05916,ph_offset_v=0.,conductivity_gain=1.,voltage_offset_v=0.,current_offset_a=0.,voltage_gain=1.,current_gain=1.,blocked_fraction=0.,accept_engineering_model=True)
METHOD=obj({'ph_targets':dict(arr(number('1',2,12)),minItems=1,maxItems=30),'start_pressure_pa':number('Pa',1000,120000),'stop_pressure_pa':number('Pa',500,119000),'cycles':dict(number('1',1,5),type='integer'),'fill_volume_m3':number('m^3',2e-5,.0001),'mode':enum(['zeta','rinse']),'ramp_timeout_s':number('s',10,600)})
METHOD_EXAMPLE=dict(ph_targets=[6.,5.,4.,3.],start_pressure_pa=60000.,stop_pressure_pa=20000.,cycles=2,fill_volume_m3=.0001,mode='zeta',ramp_timeout_s=120.)
LIQUID=obj({'volume_m3':number('m^3',1e-5,.00025),'kcl_mol_m3':number('mol/m^3',.01,60),'initial_ph':number('1',2,12),'acid_stock_m3':number('m^3',0,.001),'base_stock_m3':number('m^3',0,.001),'titrant_mol_m3':number('mol/m^3',1,1000)})
LIQUID_EXAMPLE=dict(volume_m3=.0002,kcl_mol_m3=10.,initial_ph=6.,acid_stock_m3=1e-5,base_stock_m3=1e-5,titrant_mol_m3=50.)
SAMPLE=obj({'sample_id':TEXT,'reference_id':TEXT,'ph_slope_v':number('V',-.05,.05),'accept_geometry_transfer':BOOL,'accept_ph_extrapolation':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='private_cotton_equivalent',reference_id='cotton_agcl',ph_slope_v=-.01,accept_geometry_transfer=True,accept_ph_extrapolation=True)
PHCAL=obj({'buffer_ph':arr(number('1',2,12),2),'measured_v':arr(number('V',-1,1),2)})
PHCAL_EXAMPLE=dict(buffer_ph=[4.,7.],measured_v=[-.23664,-.41412])
COND=obj({'reference_s_m':number('S/m',.0001,1.),'indicated_s_m':number('S/m',.0001,2.)})
COND_EXAMPLE=dict(reference_s_m=.1413,indicated_s_m=.1413)
POINT=obj({'time_s':number('s',0),'pressure_pa':number('Pa'),'flow_m3_s':number('m^3/s'),'streaming_v':number('V',-2,2),'streaming_a':number('A',-.002,.002),'ph':number('1',2,12),'conductivity_s_m':number('S/m',.0001,1),'direction':dict(number('1',-1,1),type='integer')})
FIT=obj({'ph':number('1'),'slope_v_pa':number('V/Pa'),'slope_a_pa':number('A/Pa'),'zeta_from_v_v':number('V'),'zeta_from_i_v':number('V'),'r2_v':number('1'),'r2_i':number('1'),'samples':dict(number('1',2),type='integer')})
RESULT=obj({'phase':PHASE,'stage':TEXT,'elapsed_s':number('s',0),'trace':arr(POINT),'fits':arr(FIT),'iep_ph':nullable(number('1',2,12)),'quality':TEXT,'template_source':TEXT,'warnings':arr(TEXT)})
LEDGER=obj({'liquid_volume_m3':number('m^3',0),'loaded_volume_m3':number('m^3',0),'acid_added_m3':number('m^3',0),'base_added_m3':number('m^3',0),'returned_volume_m3':number('m^3',0),'volume_residual_m3':number('m^3'),'k_residual_mol':number('mol'),'cl_residual_mol':number('mol'),'electroneutral_residual_mol':number('mol'),'acid_stock_m3':number('m^3',0),'base_stock_m3':number('m^3',0),'acid_syringe_m3':number('m^3',0),'base_syringe_m3':number('m^3',0),'titrant_residual_m3':number('m^3'),'gas_pressure_pa':number('Pa',0),'gas_mole_residual':number('mol'),'pneumatic_work_j':number('J',0),'hydraulic_dissipation_j':number('J',0),'work_residual_j':number('J'),'circulated_m3':number('m^3',0)})
CAPS={};SLOTS={};SLOT_ZH={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},effects or [])
def slot(k,s,u,z,o='derived',v='agent',c='controller',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller'],'初始化水物性、有限气室和显式误差系数','configuration','evaluator',PLANT_EXAMPLE,['plant.configured'])
cap('method.configure',METHOD,obj({'configured':BOOL}),['controller'],'压力下降扫描、反向循环与pH目标序列','configuration',ex=METHOD_EXAMPLE,effects=['method.configured'])
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['controller','power_switch','power_lamp'],'开关电源；断电停止并泄压，保留液体',ex={'enabled':True},effects=['power.enabled','power.angle_rad'])
for name,part,arg,lo,hi in [('cell','cell_carriage','withdrawal_m',0.,.095),('gap','gap_jaw','height_m',.00002,.0003),('lock','cell_lock','angle_rad',0.,1.2),('lid','beaker_lid','lift_m',0.,.105)]:
 u='rad' if name=='lock' else 'm';cap(name+'.set',obj({arg:number(u,lo,hi)}),obj({arg:number(u,lo,hi)}),[part],'受检人工机构动作',ex={arg:hi},effects=[name+'.'+arg]);slot(name+'.'+arg,number(u,lo,hi),u,'人工设定的'+name+'机构位置','scene',c=part,w=(name+'.set',arg))
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_lower','sample_upper'],'可信装样；棉布单点参考转入平板模型需明确接受假设','configuration','evaluator',SAMPLE_EXAMPLE,['sample.loaded'])
cap('sample.unload',obj({}),obj({'unloaded':BOOL}),['sample_lower','sample_upper'],'退回完整固体试片，不消耗样品',vis='evaluator',effects=['sample.loaded'])
cap('electrolyte.load',LIQUID,obj({'loaded':BOOL}),['beaker_wall','acid_bottle','base_bottle'],'有限KCl/酸碱库存装载','configuration','evaluator',LIQUID_EXAMPLE,['electrolyte.loaded'])
cap('electrolyte.drain',obj({}),obj({'volume_m3':number('m^3',0),'potassium_mol':number('mol',0),'chloride_mol':number('mol',0)}),['beaker_wall'],'可信宿主领取全部有限残液；保留酸碱库存',vis='evaluator',effects=['electrolyte.loaded'])
cap('ph.calibrate',PHCAL,obj({'slope_v':number('V'),'offset_v':number('V')}),['ph_probe'],'根据两点真实输入标定pH读数；不宣称实机校准','configuration',ex=PHCAL_EXAMPLE,effects=['ph.calibrated'])
cap('conductivity.calibrate',COND,obj({'factor':number('1',0)}),['conductivity_probe'],'单点电导比例校正','configuration',ex=COND_EXAMPLE,effects=['conductivity.calibrated'])
cap('acquisition.start',obj({}),obj({'operation_id':TEXT,'phase':PHASE}),['controller','acid_piston','base_piston','hydraulic_module'],'有限滴定→填充→正反压差下降扫描→拟合ζ与过零IEP',effects=['acquisition.phase','acquisition.stage'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['controller'],'取消保留滴定效果与已得数据，内部液体返杯并泄压',effects=['acquisition.phase'])
cap('process.measure',obj({}),RESULT,['controller'],'返回理想/显式偏置传感器曲线与拟合ζ；不返回样品真值','query')
cap('ledger.read',obj({}),LEDGER,['beaker_wall','hydraulic_module'],'体积、K/Cl、电中性及气体/流动功账本','query','evaluator')
cap('template.reference',obj({'substance_id':TEXT}),obj({'name':TEXT,'zeta_v':number('V'),'evidence':TEXT,'template_source':TEXT,'applicable_to_surpass':BOOL,'limitations':TEXT}),['controller'],'只读公开参考，不推进时间','query',ex={'substance_id':'cotton_agcl'})
UNSUPPORTED={'hardware.connect','adsorption.kinetics','cell.cylindrical','cell.other_geometries','pressure.external','thermal.control','fault.probability_model'}
for k in sorted(UNSUPPORTED):cap(k,obj({}),obj({}),['controller'],'该配置/功能尚未实现')
for k in ['plant','method']:slot(k+'.configured',BOOL,'1',k+'已配置','setpoint')
slot('power.enabled',BOOL,'1','逻辑电源','setpoint',w=('power.set','enabled'));slot('power.angle_rad',number('rad',0,.2),'rad','电源跷板角度','scene',c='power_switch')
for k in ['acid','base']:slot(k+'.stroke_m',number('m',0,.05),'m','1mL注射器活塞位置，体积/2e-5m2的几何代理','scene',c=k+'_piston')
for k in ['sample.loaded','electrolyte.loaded','ph.calibrated','conductivity.calibrated']:slot(k,BOOL,'1',k+'状态')
slot('sample.identity',TEXT,'1','私有固体试片身份','process_truth','evaluator');slot('electrolyte.ph_truth',nullable(number('1')),'1','真实理想电荷平衡pH','process_truth','evaluator');slot('electrolyte.volume_m3',number('m^3',0),'m^3','有限内部液体及杯内总量','process_truth','evaluator')
for k,s,u,z in [('ph.indicated',nullable(number('1')),'1','两点校正pH'),('conductivity.indicated_s_m',nullable(number('S/m',0)),'S/m','单点校正电导'),('pressure.indicated_pa',number('Pa'),'Pa','测量池有符号压差'),('streaming.indicated_v',number('V',-2,2),'V','流动电位理想加偏置读数'),('streaming.indicated_a',number('A',-.002,.002),'A','流动电流理想加偏置读数'),('temperature.indicated_k',number('K',293.15,313.15),'K','宿主恒定温度理想读数')]:slot(k,s,u,z,'sensor')
slot('acquisition.phase',PHASE,'1','异步状态');slot('acquisition.stage',TEXT,'1','滴定/填充/压力下降扫描阶段');slot('acquisition.elapsed_s',number('s',0),'s','本轮推进时间');slot('acquisition.failure',TEXT,'1','失败码');slot('result.zeta_v',nullable(number('V')),'V','流动电流斜率得到ζ推断');slot('result.iep_ph',nullable(number('1',2,12)),'1','仅已测pH点包围零点时线性插值IEP')
class AntonpaarSurpass3Runtime:
 def __init__(self,instance_id='surpass3_01'):
  self.instance_id=instance_id;self.time_s=0.;self.pending=0.;self.revision=0;self.plant=None;self.method=None;self.powered=False;self.cell=0.;self.gap=.0001;self.lock=1.2;self.lid=0.;self.sample=None;self.liquid=None;self.V=0.;self.nk=0.;self.ncl=0.;self.loaded_v=0.;self.loaded_k=0.;self.loaded_cl=0.;self.acid=0.;self.base=0.;self.syringe=[0.,0.];self.refilling=None;self.titrant_initial=0.;self.added=[0.,0.];self.returned_v=0.;self.returned_k=0.;self.returned_cl=0.;self.stock_c=50.;self.cal_ph=None;self.cal_cond=None;self.phase='idle';self.stage='idle';self.elapsed=0.;self.stage_time=0.;self.trace=[];self.fits=[];self.fit_rows=[];self.target_idx=0;self.cycle=0;self.direction=1;self.pressure=0.;self.cell_pressure=0.;self.flow=0.;self.voltage=0.;self.current=0.;self.gas_n=0.;self.gas_initial_n=0.;self.gas_v=0.;self.moved=0.;self.chamber=[0.,0.];self.work=0.;self.dissipation=0.;self.circulated=0.;self.failure='';self.serial=0;self.operation_id=None;self.records={};self._principals={};self._owners={};self._requests={};self.library=json.loads((ASSET/'zeta_library.json').read_text())
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ['agent','debug','evaluator'] or principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return AntonpaarSurpass3Session(self,principal_id)
 def describe(self):return export_antonpaar_surpass3_contract_metadata()
 def chemistry(self):
  if not self.V or not self.plant:return None,None,None
  d=(self.ncl-self.nk)/self.V;kw=self.plant['kw_mol2_m6'];root=math.sqrt(d*d+4*kw);h=(d+root)/2 if d>=0 else 2*kw/(root-d);oh=kw/h;ph=-math.log10(h/1000);l=self.plant['molar_conductivities'];k=l[0]*self.nk/self.V+l[1]*self.ncl/self.V+l[2]*h+l[3]*oh;return ph,k,(h,oh)
 def indicated(self):
  ph,k,_=self.chemistry()
  if ph is None:return None,None
  p=self.plant;ep=p['ph_offset_v']+p['ph_slope_v']*ph;return ((ep-self.cal_ph[1])/self.cal_ph[0] if self.cal_ph else None,k*p['conductivity_gain']*self.cal_cond if self.cal_cond else None)
 def read(self,visibility='agent'):
  ph,k=self.indicated();d={'plant.configured':bool(self.plant),'method.configured':bool(self.method),'power.enabled':self.powered,'power.angle_rad':.2 if self.powered else 0.,'cell.withdrawal_m':self.cell,'gap.height_m':self.gap,'lock.angle_rad':self.lock,'lid.lift_m':self.lid,'acid.stroke_m':self.syringe[0]/2e-5,'base.stroke_m':self.syringe[1]/2e-5,'sample.loaded':bool(self.sample),'electrolyte.loaded':self.V>0,'ph.calibrated':bool(self.cal_ph),'conductivity.calibrated':self.cal_cond is not None,'sample.identity':self.sample['sample_id'] if self.sample else '', 'electrolyte.ph_truth':self.chemistry()[0],'electrolyte.volume_m3':self.V,'ph.indicated':ph,'conductivity.indicated_s_m':k,'pressure.indicated_pa':self.cell_pressure*self.direction,'streaming.indicated_v':self.voltage,'streaming.indicated_a':self.current,'temperature.indicated_k':self.plant['temperature_k'] if self.plant else 298.15,'acquisition.phase':self.phase,'acquisition.stage':self.stage,'acquisition.elapsed_s':self.elapsed,'acquisition.failure':self.failure,'result.zeta_v':self.fits[-1]['zeta_from_i_v'] if self.fits else None,'result.iep_ph':self._iep()};return copy.deepcopy({k:v for k,v in d.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _iep(self):
  if not self.fits:return None
  points={round(f['ph'],6):[] for f in self.fits}
  for f in self.fits:points[round(f['ph'],6)].append(f['zeta_from_i_v'])
  data=sorted((p,sum(v)/len(v)) for p,v in points.items())
  for p,z in data:
   if abs(z)<1e-12:return p
  for (a,x),(b,y) in zip(data,data[1:]):
   if x*y<0:return a-x*(b-a)/(y-x)
  return None
 def _result(self):return dict(phase=self.phase,stage=self.stage,elapsed_s=self.elapsed,trace=copy.deepcopy(self.trace),fits=copy.deepcopy(self.fits),iep_ph=self._iep(),quality='engineering_model_with_explicit_sensor_bias',template_source='AntonPaar D85IB016EN-F p25 cotton verification reference; pH slope and geometry transfer explicit assumptions',warnings=['Only cotton reference is loadable;69nanoparticle records are reference-only','pH response slope, pressure compliance and hydraulic resistance are assumptions','No calibrated noise/real measurement uncertainty; voltage zeta biased by surface conductance','No adsorption chemistry, CO2, activities, thermal feedback or real seal contact'])
 def _ledger(self):
  ph,k,ho=self.chemistry();kw=self.plant['kw_mol2_m6'] if self.plant else 1e-8;charge=0 if ho is None else self.nk+ho[0]*self.V-self.ncl-ho[1]*self.V
  return dict(liquid_volume_m3=self.V,loaded_volume_m3=self.loaded_v,acid_added_m3=self.added[0],base_added_m3=self.added[1],returned_volume_m3=self.returned_v,volume_residual_m3=self.loaded_v+sum(self.added)-self.V-self.returned_v,k_residual_mol=self.loaded_k+self.added[1]*self.stock_c-self.nk-self.returned_k,cl_residual_mol=self.loaded_cl+self.added[0]*self.stock_c-self.ncl-self.returned_cl,electroneutral_residual_mol=charge,acid_stock_m3=self.acid,base_stock_m3=self.base,acid_syringe_m3=self.syringe[0],base_syringe_m3=self.syringe[1],titrant_residual_m3=self.titrant_initial-self.acid-self.base-sum(self.syringe)-sum(self.added),gas_pressure_pa=self.pressure,gas_mole_residual=(self.gas_initial_n-(self.pressure+self.plant['ambient_pa'])*(self.gas_v+self.moved)/(8.314462618*self.plant['temperature_k'])) if self.gas_n else 0.,pneumatic_work_j=self.work,hydraulic_dissipation_j=self.dissipation,work_residual_j=self.work-self.dissipation,circulated_m3=self.circulated)
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[k][0],a);before=copy.deepcopy({x:v for x,v in self.__dict__.items() if x!='library'})
  try:
   v=self._invoke(k,a);validate(CAPS[k][1],v)
   if CAPS[k][4] not in ['query','analysis']:self.revision+=1
   return v
  except Exception:
   lib=self.library;self.__dict__.clear();self.__dict__.update(before);self.library=lib;raise
 def _invoke(self,k,a):
  if k=='process.measure':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='template.reference':
   r=self.library['substances'].get(a['substance_id'])
   if r is None:raise ActionRejected('UNKNOWN_REFERENCE')
   return {**{x:r[x] for x in ['name','zeta_v','evidence','applicable_to_surpass','limitations']},'template_source':r['source']['url']+' '+str(r['source'].get('row',r['source'].get('page')))}
  if k=='plant.configure':
   if self.plant or self.powered or not a['accept_engineering_model']:raise ActionRejected('INITIALIZATION_ONLY')
   self.plant=copy.deepcopy(a);return {'configured':True}
  if k=='power.set':
   if not a['enabled'] and self.phase=='running':self._end('cancelled')
   self.powered=a['enabled'];return a
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self._end('cancelled');return {'phase':self.phase}
  if self.phase=='running':raise ActionRejected('BUSY')
  if k in ['cell.set','gap.set','lock.set','lid.set']:
   n=k.split('.')[0];v=next(iter(a.values()))
   if n=='cell' and (self.lock>.05 or self.V>0):raise ActionRejected('CELL_ACCESS')
   if n=='lock' and v>.05 and self.cell>.001:raise ActionRejected('CELL_ACCESS')
   if n=='gap' and abs(v/1e-6-round(v/1e-6))>1e-7:raise ActionRejected('GAP_RESOLUTION')
   setattr(self,n,v);return a
  if k=='sample.load':
   if not self.plant or self.sample or self.cell<.08 or self.lock>.05 or not a['accept_geometry_transfer'] or not a['accept_ph_extrapolation']:raise ActionRejected('LOAD_PRECONDITION')
   if a['reference_id']!='cotton_agcl':raise ActionRejected('PARTICLE_REFERENCE_ONLY')
   self.sample=copy.deepcopy(a);return {'loaded':True}
  if k=='sample.unload':
   if not self.sample or self.cell<.08 or self.V>0:raise ActionRejected('CELL_ACCESS')
   self.sample=None;return {'unloaded':True}
  if k=='electrolyte.load':
   if not self.plant or self.liquid or self.lid<.08:raise ActionRejected('LOAD_PRECONDITION')
   self.liquid=copy.deepcopy(a);self.V=a['volume_m3'];n=self.V*a['kcl_mol_m3'];h=1000*10**(-a['initial_ph']);d=(h-self.plant['kw_mol2_m6']/h)*self.V;self.nk=n+max(0.,-d);self.ncl=n+max(0.,d);self.loaded_v=self.V;self.loaded_k=self.nk;self.loaded_cl=self.ncl;self.acid=a['acid_stock_m3'];self.base=a['base_stock_m3'];self.stock_c=a['titrant_mol_m3'];self.titrant_initial=self.acid+self.base;return {'loaded':True}
  if k=='electrolyte.drain':
   if self.lid<.08:raise ActionRejected('ACCESS_INTERLOCK')
   ans=dict(volume_m3=self.V,potassium_mol=self.nk,chloride_mol=self.ncl);self.returned_v+=self.V;self.returned_k+=self.nk;self.returned_cl+=self.ncl;self.V=0.;self.nk=0.;self.ncl=0.;return ans
  if k=='ph.calibrate':
   x,y=a['buffer_ph'];u,v=a['measured_v']
   if abs(x-y)<1:raise ActionRejected('INVALID_CALIBRATION')
   slope=(v-u)/(y-x);offset=u-slope*x
   if not -.0615<=slope<=-.0568 or abs(offset)>.030001:raise ActionRejected('INVALID_CALIBRATION')
   self.cal_ph=(slope,offset);return dict(slope_v=slope,offset_v=offset)
  if k=='conductivity.calibrate':self.cal_cond=a['reference_s_m']/a['indicated_s_m'];return dict(factor=self.cal_cond)
  if k=='method.configure':
   if not self.plant or a['stop_pressure_pa']>=a['start_pressure_pa']:raise ActionRejected('INVALID_ARGUMENT')
   self.method=copy.deepcopy(a);return {'configured':True}
  if k=='acquisition.start':
   if not self.powered or not self.plant or not self.method or not self.sample or not self.V or not self.cal_ph or self.cal_cond is None or self.cell>.001 or self.lock<1. or self.lid>.001:raise ActionRejected('NOT_READY')
   ph,kap=self.indicated()
   if not 2-1e-8<=ph<=12+1e-8 or not .0001<=kap<=1.:raise ActionRejected('ELECTROLYTE_RANGE')
   self.trace=[];self.fits=[];self.fit_rows=[];self.target_idx=0;self.cycle=0;self.direction=1;self.elapsed=0.;self.stage='dose';self.stage_time=0.;self.phase='running';self.failure='';self.serial+=1;self.operation_id=f'{self.instance_id}:op:{self.serial}';return dict(operation_id=self.operation_id,phase=self.phase)
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _end(self,phase,reason=''):
  self.phase=phase;self.failure=reason;self.pressure=0.;self.cell_pressure=0.;self.flow=0.;self.voltage=0.;self.current=0.;self.chamber=[0.,0.];self.gas_n=0.;self.gas_initial_n=0.
  if self.operation_id:self.records[self.operation_id]=self._result()
 def _goto(self,stage):self.stage=stage;self.stage_time=0.
 def _dose(self):
  p=self.plant;t=self.method['ph_targets'][self.target_idx];# Control targets observed calibrated pH, not inaccessible true pH.
  true_target=(self.cal_ph[1]+self.cal_ph[0]*t-p['ph_offset_v'])/p['ph_slope_v'];h=1000*10**(-true_target);desired=h-p['kw_mol2_m6']/h;n=self.ncl-self.nk;diff=desired*self.V-n
  if abs(diff)<1e-15:self._goto('fill');return
  i=0 if diff>0 else 1;sign=1 if i==0 else -1;need=diff/(sign*self.stock_c-desired)
  if need<=0:self._end('failed','TITRANT_TOO_WEAK');return
  if self.syringe[i]<1e-15:self.refilling=i
  if self.refilling==i:
   stock=self.acid if i==0 else self.base;v=min(max(0.,1e-6-self.syringe[i]),stock,p['dose_flow_m3_s']*DT*10)
   if v<1e-15:
    if self.syringe[i]>1e-15:self.refilling=None;return
    self._end('failed','TITRANT_DEPLETED');return
   self.syringe[i]+=v
   if i==0:self.acid-=v
   else:self.base-=v
   if self.syringe[i]>=1e-6-1e-15:self.refilling=None
   return
  v=min(need,p['dose_flow_m3_s']*DT,self.syringe[i],.00025-self.V)
  if v<1e-16:self._end('failed','BEAKER_CAPACITY');return
  self.syringe[i]-=v;self.added[i]+=v;self.V+=v
  if i==0:self.ncl+=v*self.stock_c
  else:self.nk+=v*self.stock_c
 def _fit(self):
  if len(self.fit_rows)<2:return
  rows=self.fit_rows;x=[r['pressure_pa'] for r in rows];mx=sum(x)/len(x);den=sum((v-mx)**2 for v in x)
  if den<1e-12:self._end('failed','NO_PRESSURE_SPAN');return
  def reg(k):
   y=[r[k] for r in rows];my=sum(y)/len(y);s=sum((a-mx)*(b-my) for a,b in zip(x,y))/den;ss=sum((b-my)**2 for b in y);err=sum((b-(my+s*(a-mx)))**2 for a,b in zip(x,y));return s,1.-err/ss if ss>1e-30 else 1.
  su,ru=reg('streaming_v');si,ri=reg('streaming_a');p=self.plant;fac=p['viscosity_pa_s']/(EPS0*p['relative_permittivity']);kap=sum(r['conductivity_s_m'] for r in rows)/len(rows);self.fits.append(dict(ph=sum(r['ph'] for r in rows)/len(rows),slope_v_pa=su,slope_a_pa=si,zeta_from_v_v=su*fac*kap,zeta_from_i_v=si*fac*.020/(.010*self.gap),r2_v=ru,r2_i=ri,samples=len(rows)));self.fit_rows=[]
 def _tick(self):
  if self.phase!='running':return
  self.elapsed+=DT;self.stage_time+=DT;p=self.plant;m=self.method
  if self.stage=='dose':self._dose();return
  if self.stage=='fill':
   if self.V+1e-15<m['fill_volume_m3']:self._end('failed','INSUFFICIENT_ELECTROLYTE');return
   self.chamber=[m['fill_volume_m3'],0.];self.moved=0.;self.gas_v=p['gas_volume_m3'];self.pressure=m['start_pressure_pa'];self.gas_n=(self.pressure+p['ambient_pa'])*self.gas_v/(8.314462618*p['temperature_k']);self.gas_initial_n=self.gas_n;self.fit_rows=[];self._goto('ramp');return
  if self.stage!='ramp':return
  ph,kap=self.indicated();truth,ktrue,_=self.chemistry()
  if ph is None or not 2-1e-7<=ph<=12+1e-7 or not .0001<=kap<=1.:self._end('failed','ELECTROLYTE_RANGE');return
  resistance=12*p['viscosity_pa_s']*.02/(.01*self.gap**3)+p['extra_resistance_pa_s_m3'];q=self.pressure/resistance*(1-p['blocked_fraction']);oldvg=self.gas_v+self.moved;pv=self.gas_n*8.314462618*p['temperature_k'];vstop=max(0.,pv/(p['ambient_pa']+m['stop_pressure_pa'])-oldvg);dv=min(q*DT,self.chamber[0],vstop);self.chamber[0]-=dv;self.chamber[1]+=dv;self.moved+=dv;self.circulated+=dv;newvg=self.gas_v+self.moved;newp=pv/newvg-p['ambient_pa'];w=max(0.,pv*math.log(newvg/oldvg)-p['ambient_pa']*dv);self.work+=w;self.dissipation+=w;pressure=(self.pressure+newp)/2;rs=12*p['viscosity_pa_s']*.02/(.01*self.gap**3);pressure*=rs/resistance*(1-p['blocked_fraction']);self.cell_pressure=pressure;self.flow=self.direction*dv/DT
  ref=self.library['substances'][self.sample['reference_id']];zeta=ref['zeta_v']+self.sample['ph_slope_v']*(truth-ref['ph']);area=.01*self.gap;conductance=ktrue*area/.02+p['surface_conductance_s'];current=EPS0*p['relative_permittivity']*zeta/p['viscosity_pa_s']*area/.02*pressure*self.direction;u=current/conductance;pvol=u*p['voltage_gain']+p['voltage_offset_v'];icur=current*p['current_gain']+p['current_offset_a']
  if abs(pvol)>2 or abs(icur)>.002 or not 5<=1/conductance<=2e7:self._end('failed','ELECTRICAL_RANGE');return
  self.voltage=pvol;self.current=icur;self.pressure=max(0.,newp)
  row=dict(time_s=self.time_s,pressure_pa=pressure*self.direction,flow_m3_s=self.flow,streaming_v=self.voltage,streaming_a=self.current,ph=min(12.,max(2.,ph)),conductivity_s_m=kap,direction=self.direction);self.trace.append(row)
  if m['mode']=='zeta':self.fit_rows.append(row)
  if self.pressure<=m['stop_pressure_pa']+1e-6:
   if m['mode']=='zeta':self._fit()
   if self.phase!='running':return
   self.pressure=0.;self.cell_pressure=0.;self.flow=0.;self.voltage=0.;self.current=0.;self.chamber=[0.,0.];self.gas_n=0.;self.gas_initial_n=0.
   if self.direction==1:self.direction=-1;self._goto('fill')
   else:
    self.direction=1;self.cycle+=1
    if self.cycle<m['cycles']:self._goto('fill')
    else:
     self.cycle=0;self.target_idx+=1
     if self.target_idx<len(m['ph_targets']):self._goto('dose')
     else:self._end('succeeded')
  elif self.chamber[0]<1e-15:self._end('failed','CHAMBER_EMPTY')
  elif self.stage_time>=m['ramp_timeout_s']:self._end('failed','PRESSURE_TIMEOUT')
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<0 or dt_s>7200:raise ActionRejected('INVALID_ARGUMENT')
  self.pending+=dt_s
  while self.pending+1e-12>=DT:self.pending=max(0.,self.pending-DT);self.time_s+=DT;self._tick();self.revision+=1

class AntonpaarSurpass3Session:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,i):
  if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  m=export_antonpaar_surpass3_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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
  if operation_id==h.operation_id:record=h._result()
  if record is None:raise ActionRejected('UNKNOWN_OPERATION')
  return dict(operation_id=operation_id,status=record['phase'],result=copy.deepcopy(record))
 def cancel(self,operation_id,request_id):
  self.poll(operation_id)
  if operation_id!=self._host.operation_id:raise ActionRejected('NOT_RUNNING')
  return self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)





def export_antonpaar_surpass3_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions='pressure: signed differential across modeled slit; gas pressure gauge vs explicit ambient_pa; liquid flow actual volume at host constant temperature; displacement in manifest joint frame',origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'AntonpaarSurpass3Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description='fixed0.1s physical integration; current/voltage and pressure at flow-step midpoint'),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNKNOWN_OPERATION','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','INITIALIZATION_ONLY','BUSY','UNKNOWN_REFERENCE','NOT_READY','NOT_RUNNING','CELL_ACCESS','ACCESS_INTERLOCK','LOAD_PRECONDITION','PARTICLE_REFERENCE_ONLY','GAP_RESOLUTION','ELECTROLYTE_RANGE','INVALID_CALIBRATION']
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  un=k in UNSUPPORTED;asyn=k=='acquisition.start';caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if un else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if un else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if un else 'implemented'),binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.antonpaar_surpass3.AntonpaarSurpass3Session.invoke',invocation=f'session.invoke(instance_id="surpass3_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if kind=='query' or un else [dict(description_zh='有限样品/电解液，标定有效、锁杆夹紧、无纳米参考冒用；拒绝原子回退',state_ids=['power.enabled','method.configured','sample.loaded','electrolyte.loaded','cell.withdrawal_m','lock.angle_rad','acquisition.phase'],enforced_by='runtime',check='Runtime.invoke atomic rollback; authenticated Session; failures in step preserve completed dosing and curves')],state_effects=[dict(state_id=x,timing='immediate',description_zh='受检动作更新状态') for x in effects],observes=[],model_effects=dict(mode='none',description_zh='待实际场景验证',binding_ids=[]),execution=dict(mode='asynchronous' if asyn else 'synchronous',time_domain='simulation',completion_criterion='全部目标pH与正反向扫描完成并拟合' if asyn else '事务返回；只有step推进过程',completion_state_ids=['acquisition.phase'] if asyn else [],timeout_s=None,cancellation='supported' if asyn else 'unsupported',retry='same_request_id',resources=['host_clock','sample','electrolyte','acid','base','gas_chamber','request_cache'],partial_failure_zh='已加入酸碱、改变pH及已完成扫描不回滚；泄压、内部液体返杯；固体样品不消耗'),errors=[dict(code=x,meaning_zh=x,side_effects='none') for x in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=['棉布单点，pH依赖工程假设；69条纳米参考禁止转用宏观样品'],source_ids=['manufacturer_manual','simulation_assumption'])
  if asyn:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='推进有限滴定、压力扫描与斜率推断') for x in ['acid.stroke_m','base.stroke_m','ph.indicated','pressure.indicated_pa','streaming.indicated_v','streaming.indicated_a','result.zeta_v','result.iep_ph','acquisition.stage','acquisition.elapsed_s','acquisition.phase']]
 mech=dict(component_ids=['controller','hydraulic_module','sample_lower','sample_upper','acid_piston','base_piston','beaker_wall'],state_ids=list(slots),meaning_zh='有限强电解质滴定、等温气室驱动平板层流及Helmholtz–Smoluchowski拟合',implementation='lumped',equations=['electroneutrality: K+ + H+ = Cl- + OH-; H*OH=Kw; pH=-log10(H/1000), concentrations SI mol/m3','dose volume=(desired_net_H_conc*V-(nCl-nK))/(sign*Cstock-desired_net_H_conc); finite1mL syringes refill from finite bottles','Rslit=12*eta*L/(w*h^3); Q=Pgas/(Rslit+Rextra); measured pressure across slit=Q*Rslit; complete blockage closes flow','Pgas_abs*(Vg0+transferred_volume)=constant; stop at requested gauge stop pressure; cannot transfer beyond finite chamber liquid','Istr=epsilon*zeta/eta*(w*h/L)*Pslit; G=kappa*w*h/L+Gsurface; Ustr=Istr/G','OLS voltage and current slopes include independently configured gains/offsets; zetaU=dU/dP*eta*kappa/epsilon; zetaI=dI/dP*eta*L/(epsilon*w*h)','zeta(pH)=cotton_zeta_reference+explicit_assumed_slope*(pH-6); onlybracketed observed zero crossing→IEP, no extrapolated IEP','sum volume and K/Cl ledger conserves loaded+dosed=remaining+returned; total liquid includes beaker and both internal chambers','isothermal pressure work=P0V0*ln(Vg_final/Vg_initial)-Pambient*liquid_transfer; all assigned hydraulic dissipation, bath removes work; no thermal solver','pH electrode E=hostE0+hostSlope*pH; two-buffer measured inputs determine calibration; conductivity reference/indicated ratio'],parameters={},source_ids=['manufacturer_specs','manufacturer_manual','hs_current','simulation_assumption'],limitations=['Only1manufacturer macro-surface verification material;50macro materials unavailable;69electrophoretic nanomaterial formulations reference-only.','pH curve slope, cotton-cylinder→planar transfer, gas capacity, tubing resistance, ideal sensor models and bias coefficients explicit assumptions.','No empirical covariance/noise/drift/failure probability or calibrated recovery/response.','No surface adsorption/buffering, CO2, activities, competitive double-layer chemistry, streaming electroosmotic feedback or3D field/CFD.','Temperature constant from host; no thermal control or heat balance of neutralization/gas compression.','Other cell geometries, adsorption mode, external3500mbar option and OEM hardware unsupported.','Syringe volume→stroke uses assumed2e-5m2 bore; current motion surrogate not measured mechanical dimensions.','Robot manipulation/sealing and OS-level truth isolation not_run.'])
 spec=importlib.util.spec_from_file_location('_surpass_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'surface_electrokinetics':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'electrolyte.load':(LIQUID,LIQUID_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'。'
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'surface_electrokinetics':mech})

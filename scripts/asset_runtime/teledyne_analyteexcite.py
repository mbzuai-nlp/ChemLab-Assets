"""CHEM-110 finite-material Analyte Excite+ finite solid ablation and two-volume aerosol transport."""
from pathlib import Path
import copy,math,json,importlib.util
PROTOCOL_VERSION='1.0.0';SLUG='teledyne_analyteexcite';CATALOG_ID='CHEM-110';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch03/t3_analytical'/SLUG
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
R=8.314462618;DT=.01
PLANT=obj({'gas_reference_k':number('K',273.15,313.15),'gas_reference_pa':number('Pa',90000,120000),'helium_supply_mol':number('mol',.001,100),'argon_supply_mol':number('mol',0,100),'inner_volume_m3':number('m^3',1e-9,1e-4),'outer_volume_m3':number('m^3',1e-9,1e-3),'entrainment_fraction':number('1',0,1),'delivery_fraction':number('1',0,1),'optical_throughput':number('1',.01,1),'move_speed_m_s':number('m/s',.0001,.1),'accept_engineering_model':BOOL})
PLANT_EXAMPLE=dict(gas_reference_k=293.15,gas_reference_pa=101325.,helium_supply_mol=1.,argon_supply_mol=1.,inner_volume_m3=1e-7,outer_volume_m3=4e-7,entrainment_fraction=.85,delivery_fraction=.92,optical_throughput=.95,move_speed_m_s=.025,accept_engineering_model=True)
XYZ=arr(number('m',0,.1),3)
METHOD=obj({'shape':enum(['circle','square']),'spot_size_m':number('m',1e-6,245e-6),'fluence_j_m2':number('J/m^2',1e4,15e4),'frequency_hz':number('Hz',1,300),'mode':enum(['spot','scan']),'shots':dict(number('1',1,3000),type='integer'),'path_m':dict(arr(XYZ),minItems=1,maxItems=100),'scan_speed_m_s':number('m/s',1e-7,.01),'inner_flow_m3_s':number('m^3/s',1e-8,1e-4),'outer_flow_m3_s':number('m^3/s',1e-8,1e-4),'argon_flow_m3_s':number('m^3/s',0,1e-4),'washout_s':number('s',.01,60),'accept_aperture_interpolation':BOOL})
METHOD_EXAMPLE=dict(shape='square',spot_size_m=80e-6,fluence_j_m2=6e4,frequency_hz=10.,mode='spot',shots=10,path_m=[[.02,.02,.01]],scan_speed_m_s=.0004,inner_flow_m3_s=2e-6,outer_flow_m3_s=3e-6,argon_flow_m3_s=1e-6,washout_s=1.,accept_aperture_interpolation=False)
SAMPLE=obj({'sample_id':TEXT,'reference_id':enum(['NISTSRM610','NISTSRM612']),'origin_xy_m':arr(number('m',0,.1),2),'size_xy_m':arr(number('m',1e-5,.1),2),'thickness_m':number('m',1e-9,.01),'density_kg_m3':number('kg/m^3',100,20000),'grid_pitch_m':number('m',1e-8,20e-6),'penetration_length_m':number('m',1e-10,1e-5),'threshold_j_m2':number('J/m^2',100,2e5),'accept_homogeneous_model':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='private_glass',reference_id='NISTSRM610',origin_xy_m=[0.,0.],size_xy_m=[.1,.1],thickness_m=.001,density_kg_m3=2500.,grid_pitch_m=8e-6,penetration_length_m=1e-7,threshold_j_m2=1.5e4,accept_homogeneous_model=True)
PULSE=obj({'index':dict(number('1',0),type='integer'),'time_s':number('s',0),'position_m':XYZ,'energy_j':number('J',0),'fluence_j_m2':number('J/m^2',0)})
RESULT=obj({'phase':PHASE,'stage':TEXT,'elapsed_s':number('s',0),'pulses':arr(PULSE),'quality':TEXT,'evidence':TEXT,'warnings':arr(TEXT)})
PACKET=obj({'substance_id':TEXT,'mass_kg':number('kg',0)})
ROW=obj({'substance_id':TEXT,'loaded_kg':number('kg',0),'solid_kg':number('kg',0),'inner_kg':number('kg',0),'outer_kg':number('kg',0),'wall_kg':number('kg',0),'output_kg':number('kg',0),'external_kg':number('kg',0),'returned_kg':number('kg',0),'residual_kg':number('kg')})
LEDGER=obj({'species':arr(ROW),'helium_remaining_mol':number('mol',0),'helium_used_mol':number('mol',0),'helium_residual_mol':number('mol'),'argon_remaining_mol':number('mol',0),'argon_used_mol':number('mol',0),'argon_residual_mol':number('mol'),'energy_j':number('J',0),'removed_volume_m3':number('m^3',0)})
SURFACE=obj({'cells':arr(obj({'i':dict(number('1'),type='integer'),'j':dict(number('1'),type='integer'),'depth_m':number('m',0)})),'grid_pitch_m':nullable(number('m')),'max_depth_m':number('m',0),'evidence':TEXT})
CAPS={};UNSUPPORTED={'detector.icp_ms','hardware.connect','software.chromium','fault.probability_model','gas.exicheck'}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},effects or [])
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller','cell_body'],'初始化有限气源与两室传输/光路效率工程参数','configuration','evaluator',PLANT_EXAMPLE,['plant.configured'])
cap('method.configure',METHOD,obj({'configured':BOOL}),['controller','objective'],'设定孔径、能流、频率、单点/连续扫描、双路氦气与外部补充氩气','configuration','agent',METHOD_EXAMPLE,['method.configured'])
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['controller','status_power'],'电源逻辑，关机取消并保存有限物料',ex={'enabled':True},effects=['power.enabled'])
for k,part,arg,hi in [('guard','guard','angle_rad',1.1),('service','gas_door','angle_rad',1.35),('drawer','drawer','open_m',.23)]:cap(k+'.set',obj({arg:number('m' if k=='drawer' else 'rad',0,hi)}),obj({arg:number('m' if k=='drawer' else 'rad',0,hi)}),[part],'受检人工开合；运行拒绝，开抽屉需防护窗打开',ex={arg:hi},effects=[k+'.'+arg])
cap('stage.move',obj({'position_m':XYZ}),obj({'target_m':XYZ}),['stage_x','stage_y','stage_z'],'仅设置目标；宿主步进移动XYZ，Z限制50mm',ex={'position_m':[.03,.04,.02]},effects=['stage.target_m'])
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['holder'],'可信宿主加载有限均匀玻璃样品；成分来自原始参考值','configuration','evaluator',SAMPLE_EXAMPLE,['sample.loaded'])
cap('sample.unload',obj({}),obj({'returned':BOOL}),['holder'],'可信宿主归还样品剩余固体；已经释放气溶胶保留','device_command','evaluator',effects=['sample.loaded'])
for k,z in [('acquisition.start','定位、逐脉冲单点/同步连续扫描、两室洗脱'),('washout.start','保留当前固体与气溶胶，单独洗脱到外部ICP-MS接口')]:cap(k,obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['stage_x','stage_y','stage_z','cell_body','status_laser'],z,effects=['acquisition.phase','acquisition.stage'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['controller'],'取消关闭光路，保留坑形和气溶胶，不回滚物质',effects=['acquisition.phase'])
cap('process.measure',obj({}),RESULT,['controller'],'读取理想坐标/脉冲日志；本机没有ICP-MS检测器','query')
cap('eqc.measure',obj({}),obj({'fluence_j_m2':number('J/m^2',0),'pulse_energy_j':number('J',0),'evidence':TEXT}),['eqc_port'],'理想化eQC光路读数；只用显式透过率，不声称实测校准精度','query')
cap('aerosol.collect',obj({}),obj({'species':arr(PACKET),'evidence':TEXT}),['aerosol_port'],'可信宿主领取有限气溶胶质量包，重复领取不创造物料','device_command','evaluator')
cap('ledger.read',obj({}),LEDGER,['holder','cell_body'],'逐组分、氦/氩、光能与有限表面体积账本','query','evaluator')
cap('surface.read',obj({}),SURFACE,['holder'],'稀疏高度场真值，仅可信评估器可见','query','evaluator')
cap('template.reference',obj({'reference_id':enum(['NISTSRM610','NISTSRM612']),'substance_id':TEXT}),obj({'mass_fraction':number('1',0,1),'uncertainty_mass_fraction':nullable(number('1',0)),'uncertainty_type':nullable(TEXT),'evidence':TEXT,'template_source':TEXT}),['controller'],'查询公开GeoReM成分及原始不确定度；不泄露当前样品身份','query',ex={'reference_id':'NISTSRM610','substance_id':'Sr'})
for k in sorted(UNSUPPORTED):cap(k,obj({}),obj({}),['controller'],'当前子集未实现')
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='controller',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
for k in ['plant','method']:slot(k+'.configured',BOOL,'1',k+'配置状态','setpoint')
slot('power.enabled',BOOL,'1','逻辑电源','setpoint',w=('power.set','enabled'))
for k,part,arg,hi in [('guard','guard','angle_rad',1.1),('service','gas_door','angle_rad',1.35),('drawer','drawer','open_m',.23)]:slot(k+'.'+arg,number('m' if k=='drawer' else 'rad',0,hi),'m' if k=='drawer' else 'rad','工程开合姿态；运行拒绝','scene',c=part,w=(k+'.set',arg))
for i in range(3):slot('stage.'+'xyz'[i]+'_m',number('m',0,[.1,.1,.05][i]),'m','标准XYZ实际坐标；宿主权威','scene',c=['stage_x','stage_y','stage_z'][i])
slot('stage.target_m',XYZ,'m','XYZ目标，调用只写目标','setpoint');slot('sample.loaded',BOOL,'1','样品是否装入');slot('sample.identity',TEXT,'1','私有样品身份','process_truth','evaluator');slot('acquisition.phase',PHASE,'1','异步阶段');slot('acquisition.stage',TEXT,'1','运动/剥蚀/洗脱阶段');slot('acquisition.elapsed_s',number('s',0),'s','本轮实际推进时长');slot('acquisition.failure',TEXT,'1','失败原因');slot('laser.pulse_count',dict(number('1',0),type='integer'),'1','当前流程已发生脉冲数');slot('laser.fluence_j_m2',number('J/m^2',0),'J/m^2','理想eQC显示，光学透过率未实机标定');slot('signal.laser',BOOL,'1','激光脉冲序列正在进行');slot('gas.flow_m3_s',number('m^3/s',0),'m^3/s','输入参考温压下He+Ar体积流量');slot('solid.remaining_kg',number('kg',0),'kg','有限剩余固体质量','process_truth','evaluator');slot('aerosol.pending_kg',number('kg',0),'kg','两容积气溶胶质量','process_truth','evaluator')
def add(pool,k,x):pool[k]=pool.get(k,0.)+x
class TeledyneAnalyteexciteRuntime:
 def __init__(self,instance_id='excite_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.pending=0.;self.plant=None;self.method=None;self.powered=False;self.guard=0.;self.service=0.;self.drawer=0.;self.q=[0.,0.,0.];self.target=self.q.copy();self.sample=None;self.depth={};self.fractions={};self.loaded={};self.solid={};self.inner={};self.outer={};self.wall={};self.output={};self.external={};self.returned={};self.he=0.;self.ar=0.;self.he_used=0.;self.ar_used=0.;self.energy=0.;self.removed=0.;self.phase='idle';self.stage='idle';self.elapsed=0.;self.stage_time=0.;self.pulses=[];self.next_pulse=0;self.failure='';self.serial=0;self.operation_id=None;self.records={};self._principals={};self._owners={};self._requests={};self.library=json.loads((ASSET/'reference_library.json').read_text())
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ['agent','evaluator','debug'] or principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return TeledyneAnalyteexciteSession(self,principal_id)
 def describe(self):return export_teledyne_analyteexcite_contract_metadata()
 def _fluence(self):return self.method['fluence_j_m2']*self.plant['optical_throughput'] if self.method and self.plant else 0.
 def _area(self):return self.method['spot_size_m']**2*(math.pi/4 if self.method['shape']=='circle' else 1.)
 def read(self,visibility='agent'):
  d={'plant.configured':bool(self.plant),'method.configured':bool(self.method),'power.enabled':self.powered,'guard.angle_rad':self.guard,'service.angle_rad':self.service,'drawer.open_m':self.drawer,'stage.x_m':self.q[0],'stage.y_m':self.q[1],'stage.z_m':self.q[2],'stage.target_m':self.target,'sample.loaded':bool(self.sample),'sample.identity':self.sample['sample_id'] if self.sample else '', 'acquisition.phase':self.phase,'acquisition.stage':self.stage,'acquisition.elapsed_s':self.elapsed,'acquisition.failure':self.failure,'laser.pulse_count':len(self.pulses),'laser.fluence_j_m2':self._fluence() if self.powered else 0.,'signal.laser':self.phase=='running' and self.stage=='ablate','gas.flow_m3_s':sum(self.method[k] for k in ['inner_flow_m3_s','outer_flow_m3_s','argon_flow_m3_s']) if self.phase=='running' else 0.,'solid.remaining_kg':sum(self.solid.values()),'aerosol.pending_kg':sum(self.inner.values())+sum(self.outer.values())};return copy.deepcopy({k:v for k,v in d.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _result(self):return dict(phase=self.phase,stage=self.stage,elapsed_s=self.elapsed,pulses=copy.deepcopy(self.pulses),quality='engineering_process',evidence='documented operating ranges and real glass composition; assumed material removal/transfer/ideal eQC',warnings=['External ICP-MS required; no concentration or isotope measurement returned','Sparse height field, uniform composition, no elemental fractionation or particle sizes','64 aperture full list unavailable; explicit user acceptance required beyond source examples','Stage/optics/gas transport coefficients and laser noise uncalibrated'])
 def _ledger(self):
  rows=[]
  for k,n in self.loaded.items():
   fields={name+'_kg':getattr(self,name).get(k,0.) for name in ['solid','inner','outer','wall','output','external','returned']};rows.append(dict(substance_id=k,loaded_kg=n,residual_kg=n-sum(fields.values()),**fields))
  return dict(species=rows,helium_remaining_mol=self.he,helium_used_mol=self.he_used,helium_residual_mol=(self.plant['helium_supply_mol'] if self.plant else 0.)-self.he-self.he_used,argon_remaining_mol=self.ar,argon_used_mol=self.ar_used,argon_residual_mol=(self.plant['argon_supply_mol'] if self.plant else 0.)-self.ar-self.ar_used,energy_j=self.energy,removed_volume_m3=self.removed)
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[k][0],a);before=copy.deepcopy(self.__dict__)
  try:
   v=self._invoke(k,a);validate(CAPS[k][1],v)
   if CAPS[k][4] not in ['query','analysis']:self.revision+=1
   return v
  except Exception:self.__dict__.clear();self.__dict__.update(before);raise
 def _invoke(self,k,a):
  if k=='process.measure':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='surface.read':return dict(cells=[dict(i=i,j=j,depth_m=v) for (i,j),v in sorted(self.depth.items())],grid_pitch_m=self.sample['grid_pitch_m'] if self.sample else None,max_depth_m=max(self.depth.values(),default=0.),evidence='engineering sparse midpoint-cell crater; not observed profilometry')
  if k=='template.reference':
   rows=self.library['matrices'][a['reference_id']]['elements']
   if a['substance_id'] not in rows:raise ActionRejected('UNKNOWN_REFERENCE')
   r=rows[a['substance_id']];return dict(mass_fraction=r['mass_fraction'],uncertainty_mass_fraction=None if r['original_uncertainty'] is None else r['original_uncertainty']*1e-6,uncertainty_type=r['uncertainty_type'],evidence=r['evidence'],template_source=r['source']['url']+' GeoReM5211/Jochum2011')
  if k=='eqc.measure':
   if not self.powered or not self.method or self.guard or self.drawer or self.service:raise ActionRejected('NOT_READY')
   return dict(fluence_j_m2=self._fluence(),pulse_energy_j=self._fluence()*self._area(),evidence='ideal optical throughput readout; actual eQC metrology not calibrated')
  if k=='plant.configure':
   if self.plant or self.powered or not a['accept_engineering_model']:raise ActionRejected('INITIALIZATION_ONLY')
   self.plant=copy.deepcopy(a);self.he=a['helium_supply_mol'];self.ar=a['argon_supply_mol'];return {'configured':True}
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self.phase='cancelled';self.target=self.q.copy();self.records[self.operation_id]=self._result();return {'phase':self.phase}
  if k=='power.set':
   if not a['enabled'] and self.phase=='running':self._invoke('acquisition.stop',{})
   self.powered=a['enabled'];return a
  if k=='aerosol.collect':
   packet=[dict(substance_id=x,mass_kg=v) for x,v in self.output.items() if v>0]
   for x in packet:add(self.external,x['substance_id'],x['mass_kg'])
   self.output={};return dict(species=packet,evidence='finite aerosol mass host handoff; not a detector result')
  if self.phase=='running':raise ActionRejected('BUSY')
  if k in ['guard.set','service.set','drawer.set']:
   key=k.split('.')[0];val=next(iter(a.values()))
   if key=='drawer' and val>0 and self.guard<.8:raise ActionRejected('ACCESS_INTERLOCK')
   if key=='guard' and val<.8 and self.drawer>1e-8:raise ActionRejected('ACCESS_INTERLOCK')
   setattr(self,key,val);return a
  if k=='method.configure':
   if not self.plant:raise ActionRejected('NOT_READY')
   known=(a['shape']=='circle' and abs(a['spot_size_m']-50e-6)<1e-12) or (a['shape']=='square' and abs(a['spot_size_m']-80e-6)<1e-12)
   if not known and not a['accept_aperture_interpolation']:raise ActionRejected('APERTURE_LIST_UNAVAILABLE')
   if any(p[2]>.05 for p in a['path_m']) or (a['mode']=='spot' and len(a['path_m'])!=1) or (a['mode']=='scan' and len(a['path_m'])<2):raise ActionRejected('PATH_INVALID')
   if a['mode']=='scan':
    length=sum(math.dist(p,q) for p,q in zip(a['path_m'],a['path_m'][1:]))
    if length<=0 or length/a['scan_speed_m_s']*a['frequency_hz']>3000:raise ActionRejected('PROGRAM_TOO_LARGE')
   self.method=copy.deepcopy(a);return {'configured':True}
  if k=='stage.move':
   if not self.powered or not self.plant or self.drawer or a['position_m'][2]>.05:raise ActionRejected('NOT_READY')
   self.target=a['position_m'].copy();return {'target_m':self.target.copy()}
  if k=='sample.load':
   if self.sample or self.guard<.8 or self.drawer<.15 or not a['accept_homogeneous_model']:raise ActionRejected('LOAD_PRECONDITION')
   if any(x+y>.1000000001 for x,y in zip(a['origin_xy_m'],a['size_xy_m'])):raise ActionRejected('SAMPLE_BOUNDS')
   self.sample=copy.deepcopy(a);self.depth={};self.fractions={s:r['mass_fraction'] for s,r in self.library['matrices'][a['reference_id']]['elements'].items()};self.fractions['matrix_remainder']=1-sum(self.fractions.values());m=math.prod(a['size_xy_m'])*a['thickness_m']*a['density_kg_m3'];self.solid={s:m*w for s,w in self.fractions.items()}
   for s,n in self.solid.items():add(self.loaded,s,n)
   return {'loaded':True}
  if k=='sample.unload':
   if not self.sample or self.guard<.8 or self.drawer<.15:raise ActionRejected('LOAD_PRECONDITION')
   for s,n in self.solid.items():add(self.returned,s,n)
   self.solid={};self.sample=None;self.depth={};return {'returned':True}
  if k in ['acquisition.start','washout.start']:
   if not self.powered or not self.method or self.guard or self.drawer or self.service:raise ActionRejected('NOT_READY')
   if k=='acquisition.start':
    if not self.sample:raise ActionRejected('NOT_READY')
    if self.sample['grid_pitch_m']>self.method['spot_size_m']/10*(1+1e-10):raise ActionRejected('GRID_RESOLUTION')
    r=self.method['spot_size_m']/2;origin=self.sample['origin_xy_m'];size=self.sample['size_xy_m']
    if any(not origin[i]+r<=p[i]<=origin[i]+size[i]-r for p in self.method['path_m'] for i in range(2)):raise ActionRejected('SAMPLE_BOUNDS')
   self.phase='running';self.stage='position' if k=='acquisition.start' else 'washout';self.target=self.method['path_m'][0].copy() if k=='acquisition.start' else self.q.copy();self.elapsed=0.;self.stage_time=0.;self.next_pulse=0;self.pulses=[];self.failure='';self.serial+=1;self.operation_id=self.instance_id+':op:'+str(self.serial);self.records[self.operation_id]=self._result();return dict(phase='running',operation_id=self.operation_id)
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _transport(self,dt):
  if dt<=0:return
  m=self.method;p=self.plant;he=(m['inner_flow_m3_s']+m['outer_flow_m3_s'])*p['gas_reference_pa']*dt/(R*p['gas_reference_k']);ar=m['argon_flow_m3_s']*p['gas_reference_pa']*dt/(R*p['gas_reference_k'])
  if self.he+1e-15<he or self.ar+1e-15<ar:raise ActionRejected('GAS_DEPLETED')
  self.he=max(0.,self.he-he);self.ar=max(0.,self.ar-ar);self.he_used+=he;self.ar_used+=ar
  ki=m['inner_flow_m3_s']/p['inner_volume_m3'];ko=(m['inner_flow_m3_s']+m['outer_flow_m3_s'])/p['outer_volume_m3'];ei=math.exp(-ki*dt);eo=math.exp(-ko*dt)
  for sid in self.inner.keys()|self.outer.keys():
   i=self.inner.get(sid,0.);o=self.outer.get(sid,0.);ni=i*ei;no=o*eo+ki*i*(ei-eo)/(ko-ki) if abs(ko-ki)>1e-10 else (o+ki*i*dt)*eo;removed=max(0.,i+o-ni-no);self.inner[sid]=ni;self.outer[sid]=max(0.,no);add(self.output,sid,removed*p['delivery_fraction']);add(self.wall,sid,removed*(1-p['delivery_fraction']))
 def _path(self,t):
  m=self.method
  if m['mode']=='spot':return m['path_m'][0].copy()
  dist=t*m['scan_speed_m_s']
  for a,b in zip(m['path_m'],m['path_m'][1:]):
   n=math.dist(a,b)
   if n>0 and dist<=n:return [x+(y-x)*dist/n for x,y in zip(a,b)]
   dist-=n
  return m['path_m'][-1].copy()
 def _pulse(self,t):
  s=self.sample;m=self.method;pitch=s['grid_pitch_m'];pos=self._path(t);r=m['spot_size_m']/2;delta=s['penetration_length_m']*max(0.,math.log(self._fluence()/s['threshold_j_m2']));dv=0.;x,y=pos[:2]
  for i in range(math.floor((x-r)/pitch),math.ceil((x+r)/pitch)):
   for j in range(math.floor((y-r)/pitch),math.ceil((y+r)/pitch)):
    dx=(i+.5)*pitch-x;dy=(j+.5)*pitch-y
    if (abs(dx)>r+1e-14 or abs(dy)>r+1e-14) or (m['shape']=='circle' and dx*dx+dy*dy>r*r):continue
    prev=self.depth.get((i,j),0.);dn=min(delta,max(0.,s['thickness_m']-prev));self.depth[(i,j)]=prev+dn;dv+=pitch*pitch*dn
  mass=dv*s['density_kg_m3'];self.removed+=dv
  for sid,w in self.fractions.items():
   n=min(self.solid[sid],mass*w);self.solid[sid]-=n;add(self.inner,sid,n*self.plant['entrainment_fraction']);add(self.wall,sid,n*(1-self.plant['entrainment_fraction']))
  energy=self._fluence()*self._area();self.energy+=energy;self.pulses.append(dict(index=len(self.pulses),time_s=self.elapsed-DT+t-self.stage_time,position_m=pos,energy_j=energy,fluence_j_m2=self._fluence()))
 def _move(self,dt):
  distance=math.dist(self.q,self.target);step=self.plant['move_speed_m_s']*dt if self.plant else 0.
  if distance<=step+1e-12:self.q=self.target.copy();return True
  if distance:self.q=[x+(y-x)*step/distance for x,y in zip(self.q,self.target)]
  return False
 def _tick(self):
  if self.phase!='running':
   if self.powered:self._move(DT)
   return
  self.elapsed+=DT
  if self.stage=='position':
   self._transport(DT)
   if self._move(DT):self.stage='ablate';self.stage_time=0.
  elif self.stage=='ablate':
   m=self.method;last=(m['shots']-1)/m['frequency_hz'] if m['mode']=='spot' else sum(math.dist(a,b) for a,b in zip(m['path_m'],m['path_m'][1:]))/m['scan_speed_m_s'];old_time=self.stage_time;end=min(self.stage_time+DT,last);now=self.stage_time
   while self.next_pulse/m['frequency_hz']<=end+1e-12:
    t=self.next_pulse/m['frequency_hz'];self._transport(max(0.,t-now));now=t;self._pulse(t);self.next_pulse+=1
   self._transport(max(0.,end-now));self.q=self._path(end);self.target=self.q.copy();self.stage_time=end
   if end>=last-1e-12:
    remainder=max(0.,DT-(end-old_time));self._transport(remainder);self.stage='washout';self.stage_time=remainder
  elif self.stage=='washout':
   self._transport(DT);self.stage_time+=DT
   if self.stage_time+1e-12>=self.method['washout_s']:self.phase='succeeded';self.stage='complete';self.records[self.operation_id]=self._result()
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<0 or dt_s>7200:raise ActionRejected('INVALID_ARGUMENT')
  self.pending+=dt_s
  while self.pending+1e-12>=DT:
   self.pending=max(0.,self.pending-DT);self.time_s+=DT
   try:self._tick()
   except ActionRejected as e:self.phase='failed';self.failure=str(e);self.target=self.q.copy();self.records[self.operation_id]=self._result()
   self.revision+=1
class TeledyneAnalyteexciteSession:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,i):
  if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  m=export_teledyne_analyteexcite_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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





def export_teledyne_analyteexcite_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions='gas flow refers to host gas_reference_k/gas_reference_pa; XYZ displacement relative to tray home; laser spot size is circle diameter or square side',origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'TeledyneAnalyteexciteSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description='fixed0.01s mechanical tick; pulse events and two-volume transport resolved analytically within tick'),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNKNOWN_OPERATION','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','INITIALIZATION_ONLY','BUSY','UNKNOWN_REFERENCE','NOT_READY','NOT_RUNNING','ACCESS_INTERLOCK','APERTURE_LIST_UNAVAILABLE','PATH_INVALID','PROGRAM_TOO_LARGE','LOAD_PRECONDITION','SAMPLE_BOUNDS','GRID_RESOLUTION','GAS_DEPLETED']
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  un=k in UNSUPPORTED;asyn=k in ['acquisition.start','washout.start'];caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if un else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if un else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if un else 'implemented'),binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.teledyne_analyteexcite.TeledyneAnalyteexciteSession.invoke',invocation=f'session.invoke(instance_id="excite_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if kind=='query' or un else [dict(description_zh='电源、光窗/抽屉/维护门、XY/Z行程、样品网格/边界、有限气源；拒绝原子回退',state_ids=['power.enabled','method.configured','sample.loaded','guard.angle_rad','drawer.open_m','service.angle_rad','acquisition.phase'],enforced_by='runtime',check='Runtime.invoke atomic rollback and authenticated Session')],state_effects=[dict(state_id=x,timing='immediate',description_zh='受检动作改变状态') for x in effects],observes=[],model_effects=dict(mode='none',description_zh='待独立场景验证',binding_ids=[]),execution=dict(mode='asynchronous' if asyn else 'synchronous',time_domain='simulation',completion_criterion='末脉冲完成后经过显式洗脱时长；保留未洗净残留' if asyn else '受检事务返回；只step推进物理',completion_state_ids=['acquisition.phase'] if asyn else [],timeout_s=None,cancellation='supported' if asyn else 'unsupported',retry='same_request_id',resources=['host_clock','finite_solid','helium','argon','aerosol_pools','request_cache'],partial_failure_zh='已烧蚀坑、已释放气溶胶及输出不回滚；取消关闭流路并保留残留，缓存只在host寿命有效'),errors=[dict(code=x,meaning_zh=x,side_effects='none') for x in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=['实机剥蚀深度/气溶胶分馏/噪声/传输效率与eQC精度未校准'],source_ids=['manufacturer_primary','simulation_assumption'])
  if asyn:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='宿主推进坐标、脉冲及有限库存') for x in ['stage.x_m','stage.y_m','stage.z_m','laser.pulse_count','acquisition.stage','acquisition.elapsed_s','acquisition.phase']]
 mech=dict(component_ids=['controller','stage_x','stage_y','stage_z','objective','holder','cell_body','aerosol_port','eqc_port'],state_ids=list(slots),meaning_zh='193nm逐脉冲烧蚀有限高度网格、均匀成分、双容积气溶胶解析传输与外部ICP-MS物料接口',implementation='lumped',equations=['F_sample=F_set*optical_throughput; pulse_energy=F_sample*aperture_area','delta_depth=penetration_length*max(0,ln(F_sample/threshold)); local depth capped at sample thickness','sparse midpoint height cells: delta_volume=sum(cell_area*min(delta_depth,remaining_thickness)); removed_mass=density*delta_volume','trace fractions=GeoReM2011 preferred microgram/g *1e-6; matrix_remainder=1-sum(trace_fractions), no fabricated major-oxide normalization','dI/dt=-ki*I; dO/dt=ki*I-ko*O; ki=Qinner/Vinner; ko=(Qinner+Qouter)/Vouter; exact two-exponential/equal-rate solution','removed solid partitions entrainment*mass to inner and rest to wall; outflow partitions delivery*mass to output and rest to wall','each species loaded=solid+inner+outer+wall+output+external+returned; He and Ar consumed at explicit reference P/T','scan: piecewise linear XYZ at commanded speed; pulse events n/f; single-spot mode exactly specified shot count','positions advance on fixed0.01s ticks; laser pulses resolved at actual event times inside tick; cancellation retains all pools'],parameters={},source_ids=['manufacturer_primary','manufacturer_brochure','georem2011','simulation_assumption'],limitations=['67 unique real element references across2glasses, not50different materials ablation response curves.','Sparse midpoint area approximation, grid<=spot/10; no particle size/thermomechanical/elemental fractionation or microheterogeneity.','Complete64aperture list unavailable; source50µm circle and80µm square accepted directly; other spot sizes explicit interpolation assumption.','Finite laser energy ledger is incident optical energy only; heat/melting/plasma energy partition not modeled.','External ICP-MS needed; no detector response, sample identity or concentration inferred from private solid inventory.','Actual shape, mass, movement speed, drawer/door mechanics, laser interlock timing and collision contact not metrologically validated.','Hardware/Chromium/ExiCheck gas preservation not implemented; offline process simulation only.'])
 spec=importlib.util.spec_from_file_location('_excite_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'laser_ablation':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'。'
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'laser_ablation':mech})

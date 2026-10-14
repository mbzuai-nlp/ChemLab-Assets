"""CHEM-112 UNITY-xr finite thermal desorption, trap and split recollection."""
from pathlib import Path
import copy,math,json,importlib.util
PROTOCOL_VERSION='1.0.0';SLUG='markes_unityxr';CATALOG_ID='CHEM-112';ASSET_TYPE_ID='instrument.'+SLUG
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
R=8.314462618;DT=.05
PLANT=obj({'ambient_k':number('K',288.15,303.15),'ambient_pa':number('Pa',90000,110000),'gas_reference_k':number('K',273.15,313.15),'gas_reference_pa':number('Pa',90000,120000),'carrier':enum(['helium','nitrogen']),'carrier_gauge_pa':number('Pa',34474.,415000.),'dry_gauge_pa':number('Pa',340000.,415000.),'dry_dewpoint_k':number('K',150,223.15),'carrier_supply_mol':number('mol',.001,100),'dry_supply_mol':number('mol',.001,100),'capacities_j_k':arr(number('J/K',.05,1000),4),'peltier_cop':number('1',.5,4),'trap_bed_kg':number('kg',30e-6,100e-6),'trap_capacity_kg':number('kg',1e-12,1e-3),'recollect_bed_kg':number('kg',1e-5,1e-3),'recollect_capacity_kg':number('kg',1e-12,1e-3),'leak_volume_m3':number('m^3',1e-7,1e-4),'leak_rate_s_inv':number('1/s',0,1),'leak_time_s':number('s',.1,60),'leak_tolerance_pa':number('Pa',10,10000),'accept_engineering_model':BOOL})
PLANT_EXAMPLE=dict(ambient_k=293.15,ambient_pa=101325.,gas_reference_k=293.15,gas_reference_pa=101325.,carrier='helium',carrier_gauge_pa=250000.,dry_gauge_pa=370000.,dry_dewpoint_k=213.15,carrier_supply_mol=1.,dry_supply_mol=1.,capacities_j_k=[12.,1.,10.,4.],peltier_cop=1.,trap_bed_kg=30e-6,trap_capacity_kg=1e-5,recollect_bed_kg=.0002,recollect_capacity_kg=1e-4,leak_volume_m3=2e-6,leak_rate_s_inv=.0001,leak_time_s=2.,leak_tolerance_pa=2000.,accept_engineering_model=True)
METHOD=obj({'mode':enum(['analyze','tube_condition','trap_condition']),'tube_k':number('K',308.15,698.15),'tube_hold_s':number('s',0,36000),'trap_low_k':number('K',243.15,323.15),'trap_high_k':number('K',308.15,698.15),'trap_hold_s':number('s',0,3600),'trap_heating':enum(['programmed','ballistic']),'trap_ramp_k_s':number('K/s',1,40),'valve_k':number('K',323.15,483.15),'line_k':number('K',323.15,523.15),'prepurge_s':number('s',0,3600),'trap_purge_s':number('s',0,3600),'purge_flow_m3_s':number('m^3/s',2e-6/60,500e-6/60),'trap_flow_m3_s':number('m^3/s',2e-6/60,500e-6/60),'inlet_split_m3_s':number('m^3/s',0,500e-6/60),'outlet_split_m3_s':number('m^3/s',0,500e-6/60),'column_flow_m3_s':number('m^3/s',.2e-6/60,20e-6/60),'recollect':BOOL})
METHOD_EXAMPLE=dict(mode='analyze',tube_k=573.15,tube_hold_s=12.,trap_low_k=263.15,trap_high_k=573.15,trap_hold_s=12.,trap_heating='programmed',trap_ramp_k_s=40.,valve_k=463.15,line_k=473.15,prepurge_s=6.,trap_purge_s=6.,purge_flow_m3_s=20e-6/60,trap_flow_m3_s=20e-6/60,inlet_split_m3_s=20e-6/60,outlet_split_m3_s=20e-6/60,column_flow_m3_s=2.1e-6/60,recollect=True)
ITEM=obj({'substance_id':TEXT,'mass_kg':number('kg',1e-15,1e-3),'adsorption_enthalpy_j_mol':number('J/mol',1e3,2e5)})
SAMPLE=obj({'sample_id':TEXT,'sorbent_mass_kg':number('kg',.0001,.0003),'species':dict(arr(ITEM),minItems=1,maxItems=100),'accept_temperature_extrapolation':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='private_air',sorbent_mass_kg=.0002,species=[dict(substance_id='tenax_toluene',mass_kg=1e-8,adsorption_enthalpy_j_mol=65000.),dict(substance_id='tenax_hexane',mass_kg=5e-9,adsorption_enthalpy_j_mol=55000.)],accept_temperature_extrapolation=True)
TRACE=obj({'time_s':number('s',0),'tube_k':number('K',0),'trap_k':number('K',0),'stage':TEXT,'gc_trigger':BOOL})
RESULT=obj({'phase':PHASE,'stage':TEXT,'elapsed_s':number('s',0),'thermal_trace':arr(TRACE),'leak_result':TEXT,'quality':TEXT,'template_source':TEXT,'warnings':arr(TEXT)})
PACKET=obj({'substance_id':TEXT,'mass_kg':number('kg',0)})
ROW=obj({'substance_id':TEXT,'loaded_kg':number('kg',0),'tube_kg':number('kg',0),'trap_kg':number('kg',0),'recollect_kg':number('kg',0),'waste_kg':number('kg',0),'output_kg':number('kg',0),'external_kg':number('kg',0),'returned_kg':number('kg',0),'archived_kg':number('kg',0),'residual_kg':number('kg')})
LEDGER=obj({'species':arr(ROW),'carrier_remaining_mol':number('mol',0),'carrier_flow_used_mol':number('mol',0),'leak_inventory_mol':number('mol',0),'leak_vent_mol':number('mol',0),'carrier_residual_mol':number('mol'),'dry_remaining_mol':number('mol',0),'dry_used_mol':number('mol',0),'dry_residual_mol':number('mol'),'heater_j':number('J',0),'cooling_j':number('J',0),'stored_j':number('J'),'environment_j':number('J'),'energy_residual_j':number('J'),'peak_electrical_w':number('W',0,650)})
CAPS={};UNSUPPORTED={'detector.gc_ms','hardware.connect','gas.hydrogen','sampling.automatic','sequence.overlap','purge.elevated','fault.probability_model'}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},effects or [])
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller','cold_trap_box'],'初始化有限气源/吸附容量/热容/检漏参数','configuration','evaluator',PLANT_EXAMPLE,['plant.configured'])
cap('method.configure',METHOD,obj({'configured':BOOL}),['controller','tube_oven','cold_trap'],'两级解吸/吹扫/分流/再收集/管或阱调理方法','configuration','agent',METHOD_EXAMPLE,['method.configured'])
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['controller','power_switch'],'逻辑电源；断电取消但保留库存与可感热',ex={'enabled':True},effects=['power.enabled','power.angle_rad'])
for k,p,arg,hi in [('lid','top_lid','lift_m',.18),('rear','rear_cover','slide_m',.15),('shield','valve_guard','lift_m',.12),('sample_lever','sample_lever','angle_rad',1.1),('recollect_lever','recollect_lever','angle_rad',1.1)]:cap(k+'.set',obj({arg:number('rad' if 'lever' in k else 'm',0,hi)}),obj({arg:number('rad' if 'lever' in k else 'm',0,hi)}),[p],'人工盖板/封管杆操作；运行锁止与热安全检查',ex={arg:hi},effects=[k+'.'+arg])
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_tube'],'可信宿主装入已采样有限Tenax管，原表值和假定吸附焓分开','configuration','evaluator',SAMPLE_EXAMPLE,['sample.loaded'])
cap('sample.unload',obj({}),obj({'species':arr(PACKET)}),['sample_tube'],'冷却且开杆后退回未解吸物料',vis='evaluator',effects=['sample.loaded'])
cap('recollection.install',obj({'installed':BOOL}),obj({'installed':BOOL}),['recollection_tube'],'在开杆条件更换空回收管；有残留必须先领取',vis='evaluator',ex={'installed':True},effects=['recollection.installed'])
cap('recollection.collect',obj({}),obj({'species':arr(PACKET),'evidence':TEXT}),['recollection_tube'],'冷却开杆后领取有限回收物料，不声称100%实测回收',vis='evaluator')
cap('gc.ready',obj({'ready':BOOL}),obj({'ready':BOOL}),['gc_remote_port'],'外部GC宿主输入就绪状态',vis='evaluator',ex={'ready':True},effects=['gc.ready'])
cap('acquisition.start',obj({}),obj({'operation_id':TEXT,'phase':PHASE}),['controller','tube_oven','cold_trap'],'检漏→吹扫→管解吸→阱干吹→等待GC→阱反吹→冷却',effects=['acquisition.phase','acquisition.stage'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['controller'],'取消关闭分析流路，保留残余样品、阱与回收管库存',effects=['acquisition.phase'])
cap('process.measure',obj({}),RESULT,['controller'],'读取理想温度/检漏/时序记录；本机不内置GC检测器','query')
cap('products.collect',obj({}),obj({'species':arr(PACKET),'evidence':TEXT}),['transfer_port'],'可信宿主领取送往外部GC的有限气体产物包',vis='evaluator')
cap('ledger.read',obj({}),LEDGER,['tube_oven','cold_trap','recollection_tube'],'逐物种、载气/干气与可感热守恒账本','query','evaluator')
cap('template.reference',obj({'substance_id':TEXT}),obj({'name':TEXT,'retention_volume_m3':number('m^3',0),'safe_sampling_volume_m3':number('m^3',0),'temperature_k':number('K',0),'sorbent_mass_kg':number('kg',0),'evidence':TEXT,'template_source':TEXT,'flags':arr(TEXT)}),['controller'],'查询公开Tenax20C参考值；不是私有样品观测','query',ex={'substance_id':'tenax_toluene'})
for k in sorted(UNSUPPORTED):cap(k,obj({}),obj({}),['controller'],'当前配置/子集未实现')
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='controller',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
for k in ['plant','method']:slot(k+'.configured',BOOL,'1',k+'配置状态','setpoint')
slot('power.enabled',BOOL,'1','逻辑电源','setpoint',w=('power.set','enabled'));slot('power.angle_rad',number('rad',0,.2),'rad','后电源跷板工程角度','scene',c='power_switch')
for k,p,arg,hi in [('lid','top_lid','lift_m',.18),('rear','rear_cover','slide_m',.15),('shield','valve_guard','lift_m',.12),('sample_lever','sample_lever','angle_rad',1.1),('recollect_lever','recollect_lever','angle_rad',1.1)]:slot(k+'.'+arg,number('rad' if 'lever' in k else 'm',0,hi),'rad' if 'lever' in k else 'm','人工机构姿态','scene',c=p,w=(k+'.set',arg))
for k in ['tube','trap','valve','line']:slot(k+'.temperature_k',number('K',0),'K','理想'+k+'温度传感器','sensor')
slot('gc.ready',BOOL,'1','外部GC就绪输入','sensor');slot('gc.trigger_count',dict(number('1',0),type='integer'),'1','实际发出的GC触发数量');slot('leak.pressure_pa',number('Pa',0),'Pa','隔离气室绝对压力，基准为宿主环境绝压','sensor');slot('leak.result',TEXT,'1','室温无流检漏结果');slot('sample.loaded',BOOL,'1','吸附管是否已装入');slot('sample.identity',TEXT,'1','私有样品身份','process_truth','evaluator');slot('recollection.installed',BOOL,'1','回收管是否装入');slot('acquisition.phase',PHASE,'1','异步状态');slot('acquisition.stage',TEXT,'1','检漏/解吸/GC等待/冷却阶段');slot('acquisition.elapsed_s',number('s',0),'s','本轮推进时间');slot('acquisition.failure',TEXT,'1','失败原因');slot('tube.remaining_kg',number('kg',0),'kg','原管剩余物料','process_truth','evaluator');slot('trap.inventory_kg',number('kg',0),'kg','聚焦阱内有限物料','process_truth','evaluator');slot('recollection.inventory_kg',number('kg',0),'kg','回收管内有限物料','process_truth','evaluator')
def add(pool,k,x):pool[k]=pool.get(k,0.)+x
class MarkesUnityxrRuntime:
 def __init__(self,instance_id='unityxr_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.pending=0.;self.plant=None;self.method=None;self.powered=False;self.lid=0.;self.rear=0.;self.shield=0.;self.sample_lever=0.;self.recollect_lever=0.;self.gc_ready=False;self.installed=False;self.sample=None;self.bed=.0002;self.hads={};self.loaded={};self.tube={};self.trap={};self.recollect={};self.waste={};self.output={};self.external={};self.returned={};self.archived={};self.carrier=0.;self.carrier_used=0.;self.dry=0.;self.dry_used=0.;self.leak_mol=0.;self.leak_vent=0.;self.pressure=101325.;self.leak_result='not_run';self.T=[293.15]*4;self.targets=self.T.copy();self.heat=0.;self.cooling=0.;self.env_heat=0.;self.peak_power=0.;self.phase='idle';self.stage='idle';self.elapsed=0.;self.stage_time=0.;self.hold_time=0.;self.trace=[];self.triggers=0;self.failure='';self.serial=0;self.operation_id=None;self.records={};self._principals={};self._owners={};self._requests={};self.library=json.loads((ASSET/'retention_library.json').read_text())
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ['agent','evaluator','debug'] or principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return MarkesUnityxrSession(self,principal_id)
 def describe(self):return export_markes_unityxr_contract_metadata()
 def read(self,visibility='agent'):
  d={'plant.configured':bool(self.plant),'method.configured':bool(self.method),'power.enabled':self.powered,'power.angle_rad':.2 if self.powered else 0.,'lid.lift_m':self.lid,'rear.slide_m':self.rear,'shield.lift_m':self.shield,'sample_lever.angle_rad':self.sample_lever,'recollect_lever.angle_rad':self.recollect_lever,'gc.ready':self.gc_ready,'gc.trigger_count':self.triggers,'leak.pressure_pa':self.pressure,'leak.result':self.leak_result,'sample.loaded':bool(self.sample),'sample.identity':self.sample['sample_id'] if self.sample else '', 'recollection.installed':self.installed,'acquisition.phase':self.phase,'acquisition.stage':self.stage,'acquisition.elapsed_s':self.elapsed,'acquisition.failure':self.failure,'tube.remaining_kg':sum(self.tube.values()),'trap.inventory_kg':sum(self.trap.values()),'recollection.inventory_kg':sum(self.recollect.values())};d.update({k+'.temperature_k':self.T[i] for i,k in enumerate(['tube','trap','valve','line'])});return copy.deepcopy({k:v for k,v in d.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _result(self):return dict(phase=self.phase,stage=self.stage,elapsed_s=self.elapsed,thermal_trace=copy.deepcopy(self.trace),leak_result=self.leak_result,quality='engineering_process_ideal_sensors',template_source='Markes TDTS20 v8 June2012 table2; actual published extrapolated Tenax retention values',warnings=['20C reference values; hot extrapolation uses explicit assumed adsorption enthalpy','Finite capacity and no elemental reaction model; no internal GC/MS detector','Ideal temperature/pressure sensors, no calibrated noise/recovery/kinetics','Standard He/N2 model; hydrogen/Multi-Gas and automated sampling unsupported'])
 def _ledger(self):
  rows=[]
  for k,n in self.loaded.items():
   fields={p+'_kg':getattr(self,p).get(k,0.) for p in ['tube','trap','recollect','waste','output','external','returned','archived']};rows.append(dict(substance_id=k,loaded_kg=n,residual_kg=n-sum(fields.values()),**fields))
  stored=sum(c*(t-self.plant['ambient_k']) for c,t in zip(self.plant['capacities_j_k'],self.T)) if self.plant else 0.
  return dict(species=rows,carrier_remaining_mol=self.carrier,carrier_flow_used_mol=self.carrier_used,leak_inventory_mol=self.leak_mol,leak_vent_mol=self.leak_vent,carrier_residual_mol=(self.plant['carrier_supply_mol'] if self.plant else 0.)-self.carrier-self.carrier_used-self.leak_mol-self.leak_vent,dry_remaining_mol=self.dry,dry_used_mol=self.dry_used,dry_residual_mol=(self.plant['dry_supply_mol'] if self.plant else 0.)-self.dry-self.dry_used,heater_j=self.heat,cooling_j=self.cooling,stored_j=stored,environment_j=self.env_heat,energy_residual_j=self.heat-self.cooling-stored-self.env_heat,peak_electrical_w=self.peak_power)
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
 def _take(self,name,destination):
  pool=getattr(self,name);packet=[dict(substance_id=x,mass_kg=v) for x,v in pool.items() if v>0]
  for p in packet:add(getattr(self,destination),p['substance_id'],p['mass_kg'])
  pool.clear();return packet
 def _invoke(self,k,a):
  if k=='process.measure':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='template.reference':
   if a['substance_id'] not in self.library['substances']:raise ActionRejected('UNKNOWN_REFERENCE')
   r=self.library['substances'][a['substance_id']];return dict(name=r['name'],retention_volume_m3=r['retention_volume_m3'],safe_sampling_volume_m3=r['safe_sampling_volume_m3'],temperature_k=293.15,sorbent_mass_kg=.0002,evidence=r['evidence'],template_source=r['source']['url']+' table2 p'+str(r['source']['page']),flags=r['flags'].copy())
  if k=='products.collect':return dict(species=self._take('output','external'),evidence='finite gas material handoff to external GC; not detector result')
  if k=='plant.configure':
   if self.plant or self.powered or not a['accept_engineering_model'] or a['dry_dewpoint_k']>=223.15:raise ActionRejected('INITIALIZATION_ONLY')
   self.plant=copy.deepcopy(a);self.carrier=a['carrier_supply_mol'];self.dry=a['dry_supply_mol'];self.T=[a['ambient_k']]*4;self.targets=self.T.copy();self.pressure=a['ambient_pa'];return {'configured':True}
  if k=='power.set':
   if not a['enabled'] and self.phase=='running':self._invoke('acquisition.stop',{})
   self.powered=a['enabled'];return a
  if k=='gc.ready':self.gc_ready=a['ready'];return a
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self.phase='cancelled';self.records[self.operation_id]=self._result();return {'phase':self.phase}
  if self.phase=='running':raise ActionRejected('BUSY')
  if k in ['lid.set','rear.set','shield.set','sample_lever.set','recollect_lever.set']:
   name=k.split('.')[0];value=next(iter(a.values()))
   if name in ['rear','shield'] and max(self.T)>323.15:raise ActionRejected('HOT_INTERLOCK')
   if 'lever' in name and (self.lid<.12 or self.T[0]>323.15):raise ActionRejected('ACCESS_INTERLOCK')
   if name=='lid' and value<.12 and (self.sample_lever>.05 or self.recollect_lever>.05):raise ActionRejected('ACCESS_INTERLOCK')
   setattr(self,name,value);return a
  if k=='method.configure':
   if not self.plant:raise ActionRejected('NOT_READY')
   if a['tube_k']>623.15 or a['trap_high_k']>623.15:raise ActionRejected('TENAX_TEMPERATURE_LIMIT')
   if a['trap_high_k']<a['trap_low_k'] or any(abs(a[x]-273.15-round(a[x]-273.15))>1e-8 for x in ['tube_k','trap_low_k','trap_high_k','valve_k','line_k']):raise ActionRejected('INVALID_ARGUMENT')
   if any(abs(a[x]/6-round(a[x]/6))>1e-8 for x in ['tube_hold_s','trap_hold_s','prepurge_s','trap_purge_s']):raise ActionRejected('INVALID_ARGUMENT')
   limit=250 if self.plant['carrier']=='nitrogen' else 500
   for x in ['purge_flow_m3_s','trap_flow_m3_s','inlet_split_m3_s','outlet_split_m3_s']:
    ml=a[x]*6e7
    if ml>limit+1e-8 or (ml and ml<2.-1e-8) or abs(ml-round(ml))>1e-8:raise ActionRejected('FLOW_RANGE')
   if a['outlet_split_m3_s']==0 and a['column_flow_m3_s']<=2e-6/60:raise ActionRejected('SPLITLESS_FLOW_TOO_LOW')
   self.method=copy.deepcopy(a);return {'configured':True}
  if k=='sample.load':
   if not self.plant or self.sample or self.lid<.12 or self.sample_lever<.8 or self.T[0]>self.plant['ambient_k']+5 or not a['accept_temperature_extrapolation']:raise ActionRejected('LOAD_PRECONDITION')
   if len({x['substance_id'] for x in a['species']})!=len(a['species']):raise ActionRejected('INVALID_ARGUMENT')
   for x in a['species']:
    sid=x['substance_id'];row=self.library['substances'].get(sid)
    if row is None or not row['kinetics_enabled']:raise ActionRejected('REFERENCE_UNSUPPORTED')
    if sid in self.hads and self.hads[sid]!=x['adsorption_enthalpy_j_mol'] and sum(getattr(self,n).get(sid,0.) for n in ['tube','trap','recollect'])>0:raise ActionRejected('KINETICS_CONFLICT')
   self.sample=copy.deepcopy(a);self.bed=a['sorbent_mass_kg']
   for x in a['species']:sid=x['substance_id'];self.hads[sid]=x['adsorption_enthalpy_j_mol'];add(self.loaded,sid,x['mass_kg']);add(self.tube,sid,x['mass_kg'])
   return {'loaded':True}
  if k=='sample.unload':
   if not self.sample or self.lid<.12 or self.sample_lever<.8 or self.T[0]>323.15:raise ActionRejected('ACCESS_INTERLOCK')
   packet=self._take('tube','returned');self.sample=None;return dict(species=packet)
  if k=='recollection.install':
   if self.lid<.12 or self.recollect_lever<.8 or sum(self.recollect.values())>1e-20:raise ActionRejected('ACCESS_INTERLOCK')
   self.installed=a['installed'];return a
  if k=='recollection.collect':
   if self.lid<.12 or self.recollect_lever<.8:raise ActionRejected('ACCESS_INTERLOCK')
   return dict(species=self._take('recollect','archived'),evidence='finite recovered material; empirical recovery uncalibrated')
  if k=='acquisition.start':
   if not self.powered or not self.plant or not self.method or self.lid or self.rear or self.shield or self.sample_lever or self.recollect_lever or self.carrier<=0 or self.dry<=0:raise ActionRejected('NOT_READY')
   if self.method['mode']!='trap_condition' and (not self.sample or self.T[0]>self.plant['ambient_k']+5):raise ActionRejected('NOT_READY')
   if self.method['recollect'] and not self.installed:raise ActionRejected('NOT_READY')
   self.phase='running';self._goto('leak');self.elapsed=0.;self.trace=[];self.failure='';self.leak_result='pending';self.serial+=1;self.operation_id=f'{self.instance_id}:op:{self.serial}';return dict(operation_id=self.operation_id,phase=self.phase)
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _goto(self,s):self.stage=s;self.stage_time=0.;self.hold_time=0.
 def _fail(self,z):self.phase='failed';self.failure=z
 def _rate(self,sid,temp,bed,flow):
  vr=self.library['substances'][sid]['retention_volume_m3']*(bed/.0002)*math.exp(max(-700.,min(700.,self.hads[sid]/R*(1/temp-1/293.15))));return flow/max(vr,1e-300)
 def _release(self,pool,temp,bed,flow):
  result={}
  for sid,n in list(pool.items()):v=n*(-math.expm1(-min(1e300,self._rate(sid,temp,bed,flow))*DT));pool[sid]-=v;result[sid]=v
  return result
 def _capture(self,values,target,capacity):
  pool=getattr(self,target);mass=sum(values.values());f=min(1.,max(0.,capacity-sum(pool.values()))/mass) if mass else 1.
  for sid,n in values.items():add(pool,sid,n*f);add(self.waste,sid,n*(1-f))
 def _split(self,values,fraction):
  rec={}
  for sid,n in values.items():v=n*fraction;rec[sid]=v
  if self.method['recollect'] and self.installed:self._capture(rec,'recollect',self.plant['recollect_capacity_kg'])
  else:
   for sid,n in rec.items():add(self.waste,sid,n)
 def _tick(self):
  if not self.plant:return
  p=self.plant;m=self.method;amb=p['ambient_k'];running=self.phase=='running';self.elapsed+=DT if running else 0.;self.stage_time+=DT if running else 0.;nf=p['gas_reference_pa']/R/p['gas_reference_k'];q=0.;splitq=0.
  if self.powered:
   needed=(100e-6/60)*nf*DT;used=min(needed,self.dry);self.dry-=used;self.dry_used+=used
   if running and used+1e-15<needed:self._fail('DRY_GAS_DEPLETED')
  if self.phase=='running' and self.stage=='leak':
   if self.stage_time<=DT+1e-9:
    self.leak_vent+=self.leak_mol;self.leak_mol=0.;need=p['carrier_gauge_pa']*p['leak_volume_m3']/R/amb
    if need>self.carrier:self._fail('CARRIER_DEPLETED')
    else:self.carrier-=need;self.leak_mol=need
   old=self.leak_mol;self.leak_mol*=math.exp(-p['leak_rate_s_inv']*DT);self.leak_vent+=old-self.leak_mol;self.pressure=p['ambient_pa']+self.leak_mol*R*amb/p['leak_volume_m3']
   if self.stage_time+1e-9>=p['leak_time_s']:
    drop=p['ambient_pa']+p['carrier_gauge_pa']-self.pressure;self.leak_result='passed' if drop<=p['leak_tolerance_pa'] else 'failed';self.leak_vent+=self.leak_mol;self.leak_mol=0.;self.pressure=p['ambient_pa']
    if self.leak_result=='failed':self._fail('LEAK_TEST_FAILED')
    else:self._goto('trap_ready')
  if self.phase=='running':
   if self.stage=='prepurge':q=m['purge_flow_m3_s']
   elif self.stage=='primary':q=m['trap_flow_m3_s']+m['inlet_split_m3_s'];splitq=m['inlet_split_m3_s']
   elif self.stage=='trap_purge':q=m['purge_flow_m3_s']
   elif self.stage=='secondary':q=m['column_flow_m3_s']+m['outlet_split_m3_s'];splitq=m['outlet_split_m3_s']
   needed=q*nf*DT;used=min(needed,self.carrier);fraction=used/needed if needed else 1.;self.carrier-=used;self.carrier_used+=used
   if fraction<1-1e-12:self._fail('CARRIER_DEPLETED')
   q*=fraction;splitq*=fraction
  desired=[amb]*4
  if self.powered and m:
   desired=[m['tube_k'] if self.phase=='running' and self.stage=='primary' else amb,m['trap_high_k'] if self.phase=='running' and self.stage=='secondary' else m['trap_low_k'],m['valve_k'],m['line_k']]
  rates=[2.5,100. if m and m['trap_heating']=='ballistic' else m['trap_ramp_k_s'] if m else 40.,30.,30.];power_sum=25. if self.powered else 0.
  for i in range(4):
   speed=rates[i];self.targets[i]+=max(-speed*DT,min(speed*DT,desired[i]-self.targets[i]));c=p['capacities_j_k'][i];g=[.12+(1. if desired[0]==amb else 0.),.025,.06,.04][i];old=self.T[i];need=g*(old-amb)+c*(self.targets[i]-old)/DT;power=max(-50*p['peltier_cop'] if i==1 else 0.,min([200.,200.,100.,75.][i],need)) if self.powered else 0.;new=amb+power/g+(old-amb-power/g)*math.exp(-g*DT/c);self.T[i]=new;self.heat+=max(0.,power)*DT;self.cooling+=max(0.,-power)*DT;self.env_heat+=power*DT-c*(new-old);power_sum+=max(0.,power)+max(0.,-power)/p['peltier_cop']
  self.peak_power=max(self.peak_power,power_sum)
  if q:
   if self.stage in ['prepurge','primary']:
    released=self._release(self.tube,self.T[0],self.bed,q)
    if self.stage=='prepurge' or m['mode']=='tube_condition':
     for sid,n in released.items():add(self.waste,sid,n)
    else:
     lost=self._release(self.trap,self.T[1],p['trap_bed_kg'],q-splitq)
     for sid,n in lost.items():add(self.waste,sid,n)
     self._split(released,splitq/q);self._capture({k:v*(1-splitq/q) for k,v in released.items()},'trap',p['trap_capacity_kg'])
   elif self.stage in ['trap_purge','secondary']:
    released=self._release(self.trap,self.T[1],p['trap_bed_kg'],q)
    if self.stage=='trap_purge' or m['mode']=='trap_condition':
     for sid,n in released.items():add(self.waste,sid,n)
    else:
     self._split(released,splitq/q)
     for sid,n in released.items():add(self.output,sid,n*(1-splitq/q))
   if splitq and m['recollect']:
    released=self._release(self.recollect,amb,p['recollect_bed_kg'],splitq)
    for sid,n in released.items():add(self.waste,sid,n)
  if self.phase=='running':
   if self.stage=='trap_ready' and abs(self.T[1]-m['trap_low_k'])<1. and abs(self.T[2]-m['valve_k'])<1. and abs(self.T[3]-m['line_k'])<1.:self._goto('wait_gc' if m['mode']=='trap_condition' else 'prepurge')
   elif self.stage=='prepurge' and self.stage_time+1e-9>=m['prepurge_s']:self._goto('primary')
   elif self.stage=='primary':
    self.hold_time+=DT if abs(self.T[0]-m['tube_k'])<1. else 0.
    if self.hold_time+1e-9>=m['tube_hold_s']:self._goto('cool' if m['mode']=='tube_condition' else 'trap_purge')
   elif self.stage=='trap_purge' and self.stage_time+1e-9>=m['trap_purge_s']:self._goto('wait_gc')
   elif self.stage=='wait_gc' and (self.gc_ready or m['mode']=='trap_condition'):
    self._goto('secondary')
    if m['mode']=='analyze':self.triggers+=1;self.gc_ready=False;self.trace.append(dict(time_s=self.time_s,tube_k=self.T[0],trap_k=self.T[1],stage=self.stage,gc_trigger=True))
   elif self.stage=='secondary':
    self.hold_time+=DT if abs(self.T[1]-m['trap_high_k'])<1. else 0.
    if self.hold_time+1e-9>=m['trap_hold_s']:self._goto('cool')
   elif self.stage=='cool' and self.T[0]<=323.15 and abs(self.T[1]-m['trap_low_k'])<1.:self.phase='succeeded'
  if int((self.time_s+1e-8)/DT)%20==0:self.trace.append(dict(time_s=self.time_s,tube_k=self.T[0],trap_k=self.T[1],stage=self.stage,gc_trigger=False))
  if self.operation_id and self.phase!='running':self.records[self.operation_id]=self._result()
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<0 or dt_s>7200:raise ActionRejected('INVALID_ARGUMENT')
  self.pending+=dt_s
  while self.pending+1e-12>=DT:self.pending=max(0.,self.pending-DT);self.time_s+=DT;self._tick();self.revision+=1

class MarkesUnityxrSession:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,i):
  if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  m=export_markes_unityxr_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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





def export_markes_unityxr_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions='K absolute; pressure state is absolute vs host ambient_pa; supply pressures are gauge; gas flows at explicit gas_reference_k/gas_reference_pa; displacement +Z lids,+Y rear cover',origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'MarkesUnityxrSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description='fixed0.05s split thermal/retention flow integration; exact first-order substeps'),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNKNOWN_OPERATION','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','INITIALIZATION_ONLY','BUSY','UNKNOWN_REFERENCE','REFERENCE_UNSUPPORTED','NOT_READY','NOT_RUNNING','HOT_INTERLOCK','ACCESS_INTERLOCK','LOAD_PRECONDITION','TENAX_TEMPERATURE_LIMIT','FLOW_RANGE','SPLITLESS_FLOW_TOO_LOW','KINETICS_CONFLICT']
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  un=k in UNSUPPORTED;asyn=k=='acquisition.start';caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if un else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if un else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if un else 'implemented'),binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.markes_unityxr.MarkesUnityxrSession.invoke',invocation=f'session.invoke(instance_id="unityxr_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if kind=='query' or un else [dict(description_zh='冷管、盖板、封管杆、有限气源、Tenax温度限制、检漏与GC握手；拒绝原子回退',state_ids=['power.enabled','method.configured','sample.loaded','lid.lift_m','sample_lever.angle_rad','acquisition.phase'],enforced_by='runtime',check='Runtime.invoke atomic rollback; authenticated Session; gas leak failure inside step preserves sample')],state_effects=[dict(state_id=x,timing='immediate',description_zh='受检动作更新状态') for x in effects],observes=[],model_effects=dict(mode='none',description_zh='待实际场景验证',binding_ids=[]),execution=dict(mode='asynchronous' if asyn else 'synchronous',time_domain='simulation',completion_criterion='分析阶段完成、管温≤50C且阱恢复低温' if asyn else '受检事务返回，只step推进物理',completion_state_ids=['acquisition.phase'] if asyn else [],timeout_s=None,cancellation='supported' if asyn else 'unsupported',retry='same_request_id',resources=['host_clock','finite_tube','cold_trap_capacity','recollection_capacity','carrier','dry_gas','sensible_heat','request_cache'],partial_failure_zh='已流出的气体、已捕集/回收物料与温度不回滚；取消关闭分析流路并保留有限池，断电后传热继续'),errors=[dict(code=x,meaning_zh=x,side_effects='none') for x in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=['保留体积参考为外推表，热脱附/再收集动力学及噪声尚未实测标定'],source_ids=['manufacturer_manual','simulation_assumption'])
  if asyn:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='宿主推进检漏、热过程、有限物料及GC触发') for x in ['tube.temperature_k','trap.temperature_k','leak.pressure_pa','leak.result','acquisition.stage','acquisition.elapsed_s','acquisition.phase','gc.trigger_count']]
 mech=dict(component_ids=['controller','tube_oven','sample_tube','cold_trap','recollection_tube','valve_body','transfer_port'],state_ids=list(slots),meaning_zh='无流检漏、Tenax参考保留体积驱动的两级有限热脱附与分流再收集',implementation='lumped',equations=['Vr(T,m)=Vr20C,200mg*(m/200mg)*exp(Hads/R*(1/T-1/293.15)); Hads explicitly assumed, no calibrated hot kinetic curve','release=inventory*(1-exp(-Q*dt/Vr)); uniform lumped sorbent/headspace inventory, reverse-flow semantics without axial resolution','common trap/recollection mass capacity; simultaneous incoming species scale proportionally when remaining capacity exhausted; overflow to waste','primary: fraction to trap = Qtrap/(Qtrap+Qsplit_in); secondary fraction to GC=Qcolumn/(Qcolumn+Qsplit_out)','loaded=tube+trap+recollect+waste+GCoutput+external+returned+archived, each species','leak: n0=P_gauge*V/(R*Tamb), n=n0*exp(-leak_rate*t); pressure=Pambient+nRT/V; pre-leak sample inventory unchanged','finite carrier=remaining+flow_used+leak_inventory+leak_vent; finite dry gas=remaining+used at100mL/min reference P/T','Tnext=Ta+P/G+(T-Ta-P/G)*exp(-Gdt/C); heater - Peltier removal = stored sensible heat+environment exchange','heater budgets200/200/100/75W; Peltier electrical<=50W,25W control budget, peak<=650W; whole thermal model is explicit assumption','tube ramp2.5K/s; programmed trap1..40K/s; engineering ballistic100K/s cap not calibrated measured trajectory; hold counts after within1K','selected Tenax350C ceiling from EPA TO17 table3; hardware425C capability remains documented, other sorbents unsupported'],parameters={},source_ids=['manufacturer_specs','manufacturer_manual','markes_td20','epa_to17','simulation_assumption'],limitations=['197 complete source table entries;19 flagged for known ambiguity or inconsistency;178 enabled engineering references, not independently validated by Markes.','Temperature and bed-mass extrapolation, adsorption enthalpy, kinetic mixing, common capacity and ideal MFC response uncalibrated.','No axial sorbent profile, chemically selective competition, humidity/ice/degradation chemistry or gas/sample advected enthalpy and adsorption heat.','Tube/trap/collector hold combined sorbed and local gas inventory; closed-valve cancellation has no external mass transfer.','Primary hold and readiness1K tolerances are explicit control approximations, not manufacturer timing accuracy.','Per-compound calibrated recovery/noise/GC-MS response, Multi-Gas hydrogen, automation/overlap/elevated purge and hardware not_run.','Robot grasp, actual valve mechanics, seal contact and dimensional/mass metrology not_run.'])
 spec=importlib.util.spec_from_file_location('_unityxr_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'thermal_desorption':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'。'
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'thermal_desorption':mech})

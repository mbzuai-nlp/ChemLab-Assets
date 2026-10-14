"""CHEM-098 IC: aqueous diffusion data, finite fluid/species ledger and host clock.
Trusted Python host only. Sessions filter identity/truth; process/file isolation is external.
"""
from pathlib import Path
import copy,math,json,importlib.util
PROTOCOL_VERSION='1.0.0';SLUG='thermo_dionexintegrion';CATALOG_ID='CHEM-098';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];TEMPLATE_REL='assets/expansion_batch03/t3_analytical/'+SLUG+'/ion_library.json'
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
TEXT={'type':'string'};BOOL={'type':'boolean'};ISO=enum(['2H','13C','15N','18O','34S']);PHASE=enum(['idle','running','succeeded','cancelled','failed'])
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
PLANT=obj({'ambient_k':number('K',277.15,313.15),'reservoir_m3':number('m^3',1e-9,.1),'waste_capacity_m3':number('m^3',1e-9,.1),'hydraulic_resistance_pa_s_m3':number('Pa*s/m^3',1,1e17),'thermal_tau_s':number('s',.01,1e5),'suppressor_efficiency':number('1',.01,1),'accept_synthetic_retention':BOOL})
PLANT_EXAMPLE=dict(ambient_k=298.15,reservoir_m3=.002,waste_capacity_m3=.002,hydraulic_resistance_pa_s_m3=8e14,thermal_tau_s=2.,suppressor_efficiency=1.,accept_synthetic_retention=True)
METHOD=obj({'flow_m3_s':number('m^3/s',1e-9/60,1e-5/60),'eluent_mol_m3':number('mol/m^3',.1,100),'column_k':number('K',303.15,353.15),'suppression_current_a':number('A',0),'mode':enum(['anion','cation']),'rfic':BOOL,'run_s':number('s',.1,7200),'injection_m3':number('m^3',1e-10,1e-6)})
METHOD_EXAMPLE=dict(flow_m3_s=1e-6/60,eluent_mol_m3=10.,column_k=303.15,suppression_current_a=.05,mode='anion',rfic=True,run_s=60.,injection_m3=1e-8)
SOLUTE=obj({'substance_id':TEXT,'concentration_mol_m3':number('mol/m^3',0,1000),'retention_volume_m3':number('m^3',1e-9,.01),'width_fraction':number('1',.01,1)})
SAMPLE=obj({'sample_id':TEXT,'volume_m3':number('m^3',1e-10,.01),'solutes':dict(arr(SOLUTE),minItems=1,maxItems=59),'accept_unresolved_speciation':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='aqueous_01',volume_m3=1e-6,solutes=[dict(substance_id='cl_anion1',concentration_mol_m3=.2,retention_volume_m3=4e-7,width_fraction=.15),dict(substance_id='no3_anion1',concentration_mol_m3=.1,retention_volume_m3=6e-7,width_fraction=.13)],accept_unresolved_speciation=True)
POINT=obj({'time_s':number('s',0),'conductivity_s_m':number('S/m',0),'window_s':number('s',0)})
RESULT=obj({'phase':PHASE,'complete':BOOL,'points':arr(POINT),'elapsed_s':number('s',0),'template_source':TEXT,'evidence':TEXT,'warnings':arr(TEXT)})
LEDGER=obj({'reservoir_m3':number('m^3',0),'waste_m3':number('m^3',0),'sample_remaining_m3':number('m^3',0),'volume_residual_m3':number('m^3'),'species_residual_mol':number('mol'),'injected_mol':number('mol',0),'column_mol':number('mol',0),'eluted_mol':number('mol',0),'sample_id':TEXT})
CAPS={};UNSUPPORTED={'hardware.connect','speciation.solve'}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},effects or [])
cap('plant.configure',PLANT,obj({'configured':BOOL}),['housing','pump_drive'],'宿主配置有限淋洗液、废液容器、阻力与温控假设','configuration','evaluator',PLANT_EXAMPLE,['plant.configured'])
cap('method.configure',METHOD,obj({'configured':BOOL}),['pump_drive','column_compartment','suppressor','eluent_generator'],'RFIC约束下设置方法；不推进物理','configuration','agent',METHOD_EXAMPLE,['method.configured','pump.target_m3_s','column.target_k','eluent.target_mol_m3','suppressor.current_a'])
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_port'],'宿主连接有限样液；逐物质保留/展宽由宿主声明为synthetic','configuration','evaluator',SAMPLE_EXAMPLE,['sample.loaded'])
cap('sample.unload',obj({}),obj({'returned_m3':number('m^3',0)}),['sample_port'],'归还尚未进样的液体，已进柱物质保留','device_command','evaluator',effects=['sample.loaded'])
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet'],'供电；断电取消采集并关闭泵目标',ex={'enabled':True},effects=['power.enabled','pump.enabled','acquisition.phase'])
cap('pump.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['pump_drive'],'只写泵运行目标；流量和体积只在step改变',ex={'enabled':True},effects=['pump.enabled'])
for k,c,limit in [('door.set','main_door',110),('display_door.set','display_door',95)]:cap(k,obj({'angle_rad':number('rad',0,math.radians(limit))}),obj({'angle_rad':number('rad',0,math.radians(limit))}),[c],'受宿主控制的检修门角度；运行中开门被拒',ex={'angle_rad':0.},effects=['door.angle_rad' if c=='main_door' else 'display_door.angle_rad'])
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['injection_valve','column_separator','conductivity_cell'],'从有限样液取方法指定体积进柱，异步采集电导；拒绝时不转移',effects=['acquisition.phase','acquisition.elapsed_s','valve.injected'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['pump_drive','conductivity_cell'],'停止泵目标并取消，已进样/已流出物质不回滚',effects=['acquisition.phase','pump.enabled'])
cap('process.measure',obj({}),RESULT,['conductivity_cell','display_digits'],'只读实际已生成色谱窗口；不推时钟','query')
cap('template.read',obj({'substance_id':TEXT}),obj({'substance_id':TEXT,'diffusion_m2_s':number('m^2/s',0),'molar_conductivity_s_m2_mol':number('S*m^2/mol',0),'charge':dict(number(),type='integer'),'template_source':TEXT,'evidence':TEXT}),['conductivity_cell'],'读真实扩散模板和派生无限稀释电导','query',ex={'substance_id':'cl_anion1'})
cap('ledger.read',obj({}),LEDGER,['sample_port','waste_port','column_separator'],'私有体积与全部已注入物种量账本','query','evaluator')
cap('quantification.integrate',obj({'start_s':number('s',0),'stop_s':number('s',0),'calibration_s_m_s_per_mol_m3':number('S*s*m^2/mol',1e-20)}),obj({'area_s_s_m':number('S*s/m',0),'concentration_mol_m3':number('mol/m^3',0),'evidence':TEXT}),['conductivity_cell'],'窗口积分/调用者校准系数定量；不读取私有组分真值','analysis','agent',dict(start_s=0.,stop_s=60.,calibration_s_m_s_per_mol_m3=.02))
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['housing'],'未实现真实硬件或酸碱配位平衡')
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='display_digits',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','宿主有限流体边界已配置','setpoint',c='housing')
slot('power.enabled',BOOL,'1','逻辑供电目标','setpoint',c='power_inlet',w=('power.set','enabled'))
slot('pump.enabled',BOOL,'1','泵运行目标','setpoint',c='pump_drive',w=('pump.set','enabled'))
slot('pump.target_m3_s',nullable(number('m^3/s',0)),'m^3/s','液体实际体积流量目标','setpoint',c='pump_drive')
slot('pump.flow_m3_s',number('m^3/s',0),'m^3/s','最近宿主步的液体实际平均流量')
slot('pump.pressure_pa',number('Pa',0),'Pa','阻力乘流量的泵出口表压真值','process_truth','evaluator','pressure_sensor')
slot('pump.indicated_pa',number('Pa',0),'Pa','理想化泵出口表压观测，零参考环境压力')
slot('method.configured',BOOL,'1','有效方法已设定','setpoint')
slot('column.target_k',nullable(number('K',303.15,353.15)),'K','柱温目标','setpoint',c='column_compartment')
slot('column.temperature_k',nullable(number('K')),'K','单节点柱温过程真值','process_truth','evaluator','column_compartment')
slot('column.indicated_k',nullable(number('K')),'K','理想化柱温显示')
slot('eluent.target_mol_m3',nullable(number('mol/m^3',.1,100)),'mol/m^3','淋洗液浓度目标；1mM=1mol/m3','setpoint',c='eluent_generator')
slot('suppressor.current_a',nullable(number('A',0)),'A','抑制电流目标','setpoint',c='suppressor')
for sid,c,l in [('door.angle_rad','main_door',110),('display_door.angle_rad','display_door',95)]:slot(sid,number('rad',0,math.radians(l)),'rad','右铰链绕+Z角度，零为闭合','scene',c=c,w=(sid.replace('.angle_rad','.set'),'angle_rad'))
slot('sample.loaded',BOOL,'1','外部样液连接状态',c='sample_port')
slot('sample.identity',TEXT,'1','样品标签真值','process_truth','evaluator','sample_port')
slot('valve.injected',BOOL,'1','本轮已完成有限体积进样，非阀轴实测角度','derived',c='injection_valve')
slot('acquisition.phase',PHASE,'1','异步采集状态')
slot('acquisition.elapsed_s',number('s',0),'s','已采集时间')
slot('detector.conductivity_s_m',nullable(number('S/m',0)),'S/m','最近窗口的理想抑制电导，298.15K检测池参考')

class ThermoDionexintegrionRuntime:
 def __init__(self,instance_id='integrion_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.pump=False;self.angle=0.;self.display_angle=0.;self.plant=None;self.method=None;self.sample=None;self.temperature=None;self.reservoir=0.;self.waste=0.;self.sample_loaded=0.;self.sample_returned=0.;self.flow=0.;self.pressure=0.;self.bands=[];self.phase='idle';self.elapsed=0.;self.points=[];self.signal=None;self.operation_id=None;self.serial=0;self.records={};self.templates=json.loads((ROOT/TEMPLATE_REL).read_text())['substances'];self._principals={};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ['agent','evaluator','debug'] or principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return ThermoDionexintegrionSession(self,principal_id)
 def describe(self):return export_thermo_dionexintegrion_contract_metadata()
 def read(self,visibility='agent'):
  if visibility not in ['agent','evaluator','debug']:raise ActionRejected('ACCESS_DENIED')
  m=self.method or {};v={'plant.configured':bool(self.plant),'power.enabled':self.powered,'pump.enabled':self.pump,'pump.target_m3_s':m.get('flow_m3_s'),'pump.flow_m3_s':self.flow,'pump.pressure_pa':self.pressure,'pump.indicated_pa':self.pressure,'method.configured':bool(self.method),'column.target_k':m.get('column_k'),'column.temperature_k':self.temperature,'column.indicated_k':self.temperature,'eluent.target_mol_m3':m.get('eluent_mol_m3'),'suppressor.current_a':m.get('suppression_current_a'),'door.angle_rad':self.angle,'display_door.angle_rad':self.display_angle,'sample.loaded':bool(self.sample),'sample.identity':self.sample['sample_id'] if self.sample else '','valve.injected':bool(self.bands),'acquisition.phase':self.phase,'acquisition.elapsed_s':self.elapsed,'detector.conductivity_s_m':self.signal}
  return copy.deepcopy({k:x for k,x in v.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _result(self):return dict(phase=self.phase,complete=self.phase=='succeeded',points=copy.deepcopy(self.points),elapsed_s=self.elapsed,template_source='USGS PHREEQC aqueous diffusion data; substance identity withheld',evidence='derived conductivity + synthetic chromatogram retention and lognormal bandshape',warnings=['298.15K ideal limiting conductivity; no activity/speciation or calibrated noise','Host supplied retention for this method; not manufacturer column calibration','Cancelled/finite-fluid flow exhaustion retains partial fluid/species transfer'])
 def _ledger(self):
  remaining=self.sample['remaining'] if self.sample else 0.;inj=sum(x['n'] for x in self.bands);elu=sum(x['eluted'] for x in self.bands);col=inj-elu;initial=self.plant['reservoir_m3'] if self.plant else 0.;return dict(reservoir_m3=self.reservoir,waste_m3=self.waste,sample_remaining_m3=remaining,volume_residual_m3=initial+self.sample_loaded-self.reservoir-self.waste-remaining-self.sample_returned,species_residual_mol=inj-col-elu,injected_mol=inj,column_mol=max(0.,col),eluted_mol=elu,sample_id=self.sample['sample_id'] if self.sample else '')
 def invoke(self,capability_id,**arguments):
  if capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability_id][0],arguments)
  if capability_id in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  before=copy.deepcopy(self.__dict__)
  try:
   r=self._invoke(capability_id,copy.deepcopy(arguments));validate(CAPS[capability_id][1],r)
   if CAPS[capability_id][4] not in ['query','analysis']:self.revision+=1
   return copy.deepcopy(r)
  except Exception:self.__dict__.clear();self.__dict__.update(before);raise
 def _invoke(self,k,a):
  if k=='process.measure':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='template.read':
   t=self.templates.get(a['substance_id'])
   if t is None:raise ActionRejected('UNKNOWN_REFERENCE')
   return dict(substance_id=t['substance_id'],diffusion_m2_s=t['diffusion_m2_s'],molar_conductivity_s_m2_mol=t['molar_conductivity_S_m2_mol'],charge=t['charge'],template_source=t['source'],evidence=t['evidence'])
  if k=='quantification.integrate':
   if a['stop_s']<=a['start_s'] or not self.points or a['stop_s']>self.elapsed+1e-10:raise ActionRejected('INVALID_WINDOW')
   area=sum(p['conductivity_s_m']*max(0.,min(p['time_s'],a['stop_s'])-max(p['time_s']-p['window_s'],a['start_s'])) for p in self.points);return dict(area_s_s_m=area,concentration_mol_m3=area/a['calibration_s_m_s_per_mol_m3'],evidence='derived from observed window integral and caller calibration; no hidden species truth')
  if k=='plant.configure':
   if self.plant or self.powered or not a['accept_synthetic_retention']:raise ActionRejected('INITIALIZATION_ONLY')
   self.plant=a;self.reservoir=a['reservoir_m3'];self.temperature=a['ambient_k'];return {'configured':True}
  # Passive door manipulation and power toggles are usable before plant setup.
  if k in ['door.set','display_door.set']:
   if self.pump or self.phase=='running':raise ActionRejected('BUSY')
   if k=='door.set':self.angle=a['angle_rad']
   else:self.display_angle=a['angle_rad']
   return a
  if k=='power.set':
   self.powered=a['enabled']
   if not self.powered:
    self.pump=False
    if self.phase=='running':self.phase='cancelled';self._save_record()
   return a
  if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
  if self.phase=='running' and k not in ['acquisition.stop','pump.set']:raise ActionRejected('BUSY')
  if k=='method.configure':
   if self.pump or self.sample or self._ledger()['column_mol']>1e-14:raise ActionRejected('BUSY')
   if a['column_k']<self.plant['ambient_k']+5:raise ActionRejected('COLUMN_RANGE')
   if a['rfic'] and not .1e-6/60<=a['flow_m3_s']<=3e-6/60:raise ActionRejected('RFIC_FLOW_RANGE')
   maxp=(5000 if a['rfic'] else 6000)*6894.757293
   if self.plant['hydraulic_resistance_pa_s_m3']*a['flow_m3_s']>maxp:raise ActionRejected('PRESSURE_LIMIT')
   if a['suppression_current_a']*self.plant['suppressor_efficiency']+1e-12<96485.33212*a['eluent_mol_m3']*a['flow_m3_s']:raise ActionRejected('INSUFFICIENT_SUPPRESSION_CURRENT')
   self.method=a;return {'configured':True}
  if k=='sample.load':
   if self.sample or not self.method:raise ActionRejected('SAMPLE_METHOD_REQUIRED')
   if not a['sample_id'] or not a['accept_unresolved_speciation']:raise ActionRejected('PREPARATION_REQUIRED')
   ids=[x['substance_id'] for x in a['solutes']]
   if len(set(ids))!=len(ids):raise ActionRejected('DUPLICATE_SOLUTE')
   for x in a['solutes']:
    if x['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
    z=self.templates[x['substance_id']]['charge']
    if z and (z<0)!=(self.method['mode']=='anion'):raise ActionRejected('COLUMN_POLARITY')
   self.sample=dict(a,remaining=a['volume_m3']);self.sample_loaded+=a['volume_m3'];return {'loaded':True}
  if k=='sample.unload':
   if not self.sample:raise ActionRejected('NO_SAMPLE')
   v=self.sample['remaining'];self.sample_returned+=v;self.sample=None;return {'returned_m3':v}
  if k=='pump.set':
   if a['enabled'] and (not self.powered or not self.method or self.angle>1e-6 or self.display_angle>1e-6 or self.reservoir<=0 or self.waste>=self.plant['waste_capacity_m3']):raise ActionRejected('NOT_READY')
   self.pump=a['enabled']
   if not self.pump and self.phase=='running':self.phase='cancelled';self._save_record()
   return a
  if k=='acquisition.start':
   if not self.powered or not self.pump or not self.method or not self.sample or abs(self.temperature-self.method['column_k'])>.5:raise ActionRejected('NOT_READY')
   vol=self.method['injection_m3']
   if self.sample['remaining']<vol or self._ledger()['column_mol']>1e-14:raise ActionRejected('SAMPLE_OR_CARRYOVER')
   if self.waste+vol>self.plant['waste_capacity_m3']:raise ActionRejected('WASTE_FULL')
   # Unmodeled plug solvent mixing is an instantaneous incompressible displacement into waste;
   # analyte amounts remain in bands and are conserved independently.
   self.sample['remaining']-=vol;self.waste+=vol;self.bands += [dict(s,n=s['concentration_mol_m3']*vol,eluted=0.,volume=0.) for s in self.sample['solutes']];self.serial+=1;self.operation_id=f'{self.instance_id}:op:{self.serial}';self.phase='running';self.elapsed=0.;self.points=[];self.signal=None;return dict(phase='running',operation_id=self.operation_id)
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self.phase='cancelled';self.pump=False;self._save_record();return {'phase':self.phase}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _save_record(self):
  if self.operation_id:self.records[self.operation_id]=self._result()
 def step(self,dt_s):
  validate(number('s',1e-12,1e6),dt_s)
  self.flow=0.;self.pressure=0.
  if self.plant:
   target=self.method['column_k'] if self.powered and self.method else self.plant['ambient_k'];self.temperature=target+(self.temperature-target)*math.exp(-dt_s/self.plant['thermal_tau_s'])
   if self.powered and self.pump and self.method:
    q=self.method['flow_m3_s'];cap=self.plant['waste_capacity_m3']-self.waste;duration=min(dt_s,self.reservoir/q,max(0,cap)/q)
    if self.phase=='running':duration=min(duration,max(0,self.method['run_s']-self.elapsed))
    v=q*duration;self.reservoir=max(0.,self.reservoir-v);self.waste+=v;self.flow=v/dt_s;self.pressure=self.plant['hydraulic_resistance_pa_s_m3']*self.flow;signal=0.
    for band in self.bands:
     volume=band['volume']+v;f=0. if volume<=0 else .5*(1+math.erf(math.log(volume/band['retention_volume_m3'])/(band['width_fraction']*math.sqrt(2))));new=band['n']*f;dn=max(0.,new-band['eluted']);band['eluted']=new;band['volume']=volume;t=self.templates[band['substance_id']];counter=self.templates['h_cation1' if t['charge']<0 else 'oh_anion1']['molar_conductivity_S_m2_mol'];lam=t['molar_conductivity_S_m2_mol']+abs(t['charge'])*counter
     if v>0:signal+=lam*dn/v
    if self.phase=='running':
     if duration>0:self.elapsed+=duration;self.signal=signal;self.points.append(dict(time_s=self.elapsed,conductivity_s_m=signal,window_s=duration))
     if self.elapsed>=self.method['run_s']-1e-10:self.phase='succeeded';self.pump=False
     elif duration<dt_s-1e-10:self.phase='failed';self.pump=False
     self._save_record()
    elif duration<dt_s-1e-10:self.pump=False
  self.time_s+=dt_s;self.revision+=1;return self.read('evaluator')
class ThermoDionexintegrionSession:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,i):
  if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  m=export_thermo_dionexintegrion_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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


def export_thermo_dionexintegrion_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():
  ref='泵出口相对环境表压；环境为0Pa基准' if u=='Pa' else '液体实际体积流量，不是气体标准体积' if u=='m^3/s' else None
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=ref,origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'ThermoDionexintegrionSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  unsupported=k in UNSUPPORTED;asyn=k=='acquisition.start'
  caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.thermo_dionexintegrion.ThermoDionexintegrionSession.invoke',invocation=f'session.invoke(instance_id="integrion_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if kind in ['query','analysis'] or unsupported else [dict(description_zh='类型/身份、方法限值、闭门、温度准备、有限体积及忙碌检查；不冒充原厂安全互锁',state_ids=['power.enabled','pump.enabled','method.configured','door.angle_rad','display_door.angle_rad','acquisition.phase'],enforced_by='runtime',check='atomic Runtime.invoke plus trusted-principal Session')],state_effects=[dict(state_id=x,timing='immediate',description_zh='受检事务目标/逻辑状态变化') for x in effects],observes=[],model_effects=dict(mode='none',description_zh='等待独立实际场景求值；数值过程不自动证明模型联动',binding_ids=[]),execution=dict(mode='asynchronous' if asyn else 'synchronous',time_domain='simulation',completion_criterion='采集实际时间达到run_s；残留柱物质仍记账' if asyn else '受检事务返回；read/describe/poll不推进时钟',completion_state_ids=['acquisition.phase'] if asyn else [],timeout_s=None,cancellation='supported' if asyn else 'unsupported',retry='same_request_id',resources=['host_clock','sample_fluid','eluent_reservoir','waste_container','request_cache'],partial_failure_zh='已进样/已流出物质保留；取消停止泵目标，不逆向回滚；去重仅当前宿主进程'),errors=[dict(code=x,meaning_zh=x,side_effects='none') for x in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','PRECONDITION_FAILED','INITIALIZATION_ONLY','BUSY','COLUMN_RANGE','RFIC_FLOW_RANGE','PRESSURE_LIMIT','INSUFFICIENT_SUPPRESSION_CURRENT','SAMPLE_METHOD_REQUIRED','PREPARATION_REQUIRED','DUPLICATE_SOLUTE','UNKNOWN_REFERENCE','COLUMN_POLARITY','NO_SAMPLE','NOT_READY','SAMPLE_OR_CARRYOVER','WASTE_FULL','NOT_RUNNING','INVALID_WINDOW']],example_arguments=copy.deepcopy(ex),verification=[],limitations=['教学过程；真实保留曲线/噪声/活动度/硬件未校准'],source_ids=['manufacturer_operator','simulation_assumption'])
  if asyn:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='唯一由宿主step推进') for x in ['acquisition.elapsed_s','detector.conductivity_s_m','pump.flow_m3_s','pump.pressure_pa','acquisition.phase']]
 mech=dict(component_ids=['sample_port','injection_valve','pump_drive','column_separator','suppressor','conductivity_cell','waste_port'],state_ids=list(slots),meaning_zh='有限液体和物种账本、受限RFIC方法、lognormal保留体积分布与扩散电导模板',implementation='lumped',equations=['lambda_i=z_i^2*F^2*D_i/(R*298.15K), from USGS -dw; neutral z=0 gives zero conductivity','Band CDF(V)=0.5*(1+erf(log(V/V_ret)/(sqrt(2)*width_fraction))); V_ret and width explicit host assumptions','n_eluted=n_injected*CDF(V); n_column=n_injected-n_eluted; window conductivity=sum((lambda_i+abs(z_i)*lambda_counterion)*dn_i/dV)','Finite eluent V: dV=min(Q*dt, reservoir_remaining, waste_capacity_remaining, run_time_remaining*Q)','Finite injection takes sample injection_m3; solvent plug is instantaneous incompressible displacement into waste, analytes retained in column; no void-volume pressure pulse model','T(t+dt)=Ttarget+(T-Ttarget)*exp(-dt/tau); temperature setting changes target only','Pressure_gauge=hydraulic_resistance*step_average_flow; 5000psi RFIC /6000psi pump limits; no pressure transient or detector backpressure model','suppression_current*efficiency >= F*eluent_concentration*Q; ideal monovalent eluent Faraday current condition','Host clock only; read and observed-window quantification never inspect hidden sample composition'],parameters={},source_ids=['manufacturer_operator','usgs_diffusion','simulation_assumption'],limitations=['59公开水溶物性模板，其中38带电；未提供50条实测离子色谱。','保留体积/峰宽逐样品显式提供，整条色谱标synthetic；物性质据不冒充实机校准。','298.15K理想稀溶液与理想抑制，弱酸解离、配位、电荷配平、反应和高浓度非理想未实现。','样品溶剂进柱用等体积废液置换简化，不解算真实死体积和压力脉冲。','有限容器体积和进柱物种量守恒；未执行真实进样器、机器人或硬件。','机顶外部淋洗液瓶为宿主边界；几何只有本体承托盘与接管。','可选平板仅简化遥测，不复制完整移动App。','真值经会话过滤，宿主内存及共享文件需由部署环境另行隔离。'])
 spec=importlib.util.spec_from_file_location('_integrion_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'ion_chromatography':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'。'
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'ion_chromatography':mech})

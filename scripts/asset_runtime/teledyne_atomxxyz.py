"""CHEM-109 finite-material Atomx XYZ process. Host API; documented data, explicit engineering kinetics."""
from pathlib import Path
import copy,math,json,importlib.util
PROTOCOL_VERSION='1.0.0';SLUG='teledyne_atomxxyz';CATALOG_ID='CHEM-109';ASSET_TYPE_ID='instrument.'+SLUG
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
R=8.314462618;DT=.1;FLOW=number('m^3/s',5e-6/60,500e-6/60)
PLANT=obj({'ambient_k':number('K',283.15,303.15),'gas_reference_k':number('K',273.15,313.15),'gas_reference_pa':number('Pa',90000,120000),'gas_supply_mol':number('mol',.001,100),'gas_inlet_gauge_pa':number('Pa',65*6894.757293168,100*6894.757293168),'gas':enum(['nitrogen','helium']),'water_supply_m3':number('m^3',0,.02),'methanol_supply_m3':number('m^3',0,.005),'transfer_efficiency':number('1',.01,1),'trap_capture_fraction':number('1',0,1),'trap_capacity_mol':number('mol',1e-10,.001),'desorb_rate_s_inv':number('1/s',.001,10),'bake_rate_s_inv':number('1/s',.001,10),'thermal_tau_s':number('s',.1,100),'motion_speed_m_s':number('m/s',.005,.5),'soil_extraction_rate_s_inv':number('1/s',.001,1),'pressure_drop_pa':number('Pa',0,100000),'accept_engineering_model':BOOL})
PLANT_EXAMPLE=dict(ambient_k=293.15,gas_reference_k=293.15,gas_reference_pa=101325.,gas_supply_mol=2.,gas_inlet_gauge_pa=80*6894.757293168,gas='nitrogen',water_supply_m3=.001,methanol_supply_m3=.0001,transfer_efficiency=.8,trap_capture_fraction=.97,trap_capacity_mol=1e-5,desorb_rate_s_inv=.08,bake_rate_s_inv=.1,thermal_tau_s=3.,motion_speed_m_s=.08,soil_extraction_rate_s_inv=.1,pressure_drop_pa=5000.,accept_engineering_model=True)
METHOD=obj({'matrix':enum(['water','soil_low','soil_high']),'sample_volume_m3':number('m^3',1e-6,25e-6),'sparger_capacity_m3':{'type':'number','enum':[5e-6,25e-6],'x-unit':'m^3'},'dilution':{'type':'integer','enum':[1,2,5,10,25,50,100],'x-unit':'1'},'purge_k':number('K',283.15,363.15),'purge_s':number('s',1,1800),'purge_flow_m3_s':FLOW,'dry_purge_s':number('s',0,600),'dry_flow_m3_s':FLOW,'desorb_k':number('K',373.15,573.15),'desorb_s':number('s',1,600),'desorb_carrier_m3_s':number('m^3/s',.1e-6/60,100e-6/60),'bake_k':number('K',373.15,573.15),'bake_s':number('s',1,600),'bake_flow_m3_s':FLOW,'rinse_volume_m3':number('m^3',1e-6,25e-6),'rinse_count':dict(number('1',0,5),type='integer'),'standard_doses_m3':arr({'type':'number','enum':[0.,1e-9,2e-9,5e-9,10e-9,20e-9],'x-unit':'m^3'},3),'extraction_s':number('s',1,600),'accept_water_henry_transfer':BOOL})
METHOD_EXAMPLE=dict(matrix='water',sample_volume_m3=5e-6,sparger_capacity_m3=5e-6,dilution=1,purge_k=293.15,purge_s=660.,purge_flow_m3_s=40e-6/60,dry_purge_s=60.,dry_flow_m3_s=100e-6/60,desorb_k=523.15,desorb_s=240.,desorb_carrier_m3_s=.8e-6/60,bake_k=533.15,bake_s=120.,bake_flow_m3_s=200e-6/60,rinse_volume_m3=7e-6,rinse_count=1,standard_doses_m3=[0.,0.,0.],extraction_s=30.,accept_water_henry_transfer=True)
SPECIES=obj({'substance_id':TEXT,'moles':number('mol',0,.001),'soil_kd_m3_kg':number('m^3/kg',0,100)})
SAMPLE=obj({'sample_id':TEXT,'vial_index':dict(number('1',0,83),type='integer'),'matrix':enum(['water','soil_low','soil_high']),'liquid_volume_m3':number('m^3',0,40e-6),'soil_mass_kg':number('kg',0,.02),'components':dict(arr(SPECIES),minItems=1,maxItems=20)})
SAMPLE_EXAMPLE=dict(sample_id='aqueous_benzene',vial_index=0,matrix='water',liquid_volume_m3=20e-6,soil_mass_kg=0.,components=[dict(substance_id='benzene',moles=1e-8,soil_kd_m3_kg=0.)])
STANDARD=obj({'index':dict(number('1',0,2),type='integer'),'volume_m3':number('m^3',1e-9,15e-6),'components':dict(arr(SPECIES),minItems=1,maxItems=20)})
POINT=obj({'time_s':number('s',0),'stage':TEXT,'gas_flow_m3_s':number('m^3/s',0),'trap_temperature_k':number('K'),'gauge_pressure_pa':number('Pa',0)})
RESULT=obj({'phase':PHASE,'stage':TEXT,'complete':BOOL,'elapsed_s':number('s',0),'points':arr(POINT),'gc_start':BOOL,'quality':TEXT,'template_source':TEXT,'evidence':TEXT,'warnings':arr(TEXT)})
POOL=obj({'substance_id':TEXT,'loaded_mol':number('mol',0),'vials_mol':number('mol',0),'cell_mol':number('mol',0),'standards_mol':number('mol',0),'trap_mol':number('mol',0),'gc_mol':number('mol',0),'external_mol':number('mol',0),'vent_mol':number('mol',0),'waste_mol':number('mol',0),'returned_mol':number('mol',0),'residual_mol':number('mol')})
LEDGER=obj({'species':arr(POOL),'liquid_loaded_m3':number('m^3',0),'liquid_remaining_m3':number('m^3',0),'liquid_waste_m3':number('m^3',0),'liquid_returned_m3':number('m^3',0),'liquid_residual_m3':number('m^3'),'gas_remaining_mol':number('mol',0),'gas_vent_mol':number('mol',0),'gas_gc_mol':number('mol',0),'gas_residual_mol':number('mol')})
CAPS={};UNSUPPORTED={'detector.chromatogram','hardware.connect','software.teklink','fault.probability_model'}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},effects or [])
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller','trap_core'],'显式有限气源/溶剂与非标定的传质捕集动力学','configuration','evaluator',PLANT_EXAMPLE,['plant.configured'])
cap('method.configure',METHOD,obj({'configured':BOOL}),['controller','sparge_vessel'],'水/低土/甲醇提取方法，流量温度、吹扫捕集、稀释、内标和冲洗','configuration','agent',METHOD_EXAMPLE,['method.configured'])
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['controller','status_power'],'电源逻辑；断电取消正在执行的流程，保留所有物料',ex={'enabled':True},effects=['power.enabled','acquisition.phase'])
cap('guard.set',obj({'angle_rad':number('rad',0,1.4)}),obj({'angle_rad':number('rad',0,1.4)}),['guard'],'手动护罩工程铰链；流程运行中拒绝',ex={'angle_rad':1.},effects=['guard.angle_rad'])
cap('trap_door.set',obj({'angle_rad':number('rad',0,1.45)}),obj({'angle_rad':number('rad',0,1.45)}),['trap_door'],'捕集阱维护门工程铰链；热/运行状态拒绝',ex={'angle_rad':1.},effects=['trap_door.angle_rad'])
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['tray'],'宿主装载有限40mL VOA样品，支持84位置独立库存','configuration','evaluator',SAMPLE_EXAMPLE,['sample.count'])
cap('sample.unload',obj({'vial_index':dict(number('1',0,83),type='integer')}),obj({'returned':BOOL}),['tray'],'空闲时归还选定瓶全部剩余物料','device_command','evaluator',{'vial_index':0},['sample.count'])
cap('standard.load',STANDARD,obj({'loaded':BOOL}),['standard_0','standard_1','standard_2'],'宿主装三路15mL内标有限库存','configuration','evaluator',dict(index=0,volume_m3=15e-6,components=[dict(substance_id='toluene',moles=1e-6,soil_kd_m3_kg=0.)]),['standard.loaded_count'])
cap('acquisition.start',obj({'vial_index':dict(number('1',0,83),type='integer')}),obj({'phase':PHASE,'operation_id':TEXT}),['gantry_x','gantry_y','gantry_z','sample_elevator','syringe_plunger','trap_core','status_run'],'按位置取样→定量/稀释→吹扫/捕集→热解吸送GC→烘烤冲洗归位',ex={'vial_index':0},effects=['acquisition.phase','acquisition.stage','acquisition.elapsed_s'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['controller','trap_core'],'取消保留针筒/吹扫管/捕集阱与输出；不回滚物质',effects=['acquisition.phase'])
cap('system.rinse',obj({'volume_m3':number('m^3',1e-6,25e-6)}),obj({'rinsed_volume_m3':number('m^3',0)}),['syringe_barrel','sparge_vessel'],'空闲时显式水冲洗，把残留吹扫液转入废液；吸附阱不假装清空',ex={'volume_m3':7e-6})
cap('gas.collect',obj({}),obj({'species':arr(obj({'substance_id':TEXT,'moles':number('mol',0)})),'evidence':TEXT}),['gc_out_port'],'宿主领取有限已解吸气体包；再领取不重复创造物料','device_command','evaluator')
cap('process.measure',obj({}),RESULT,['controller'],'读取过程日志/温度/气路状态；仪器无内置GC/MS检测器','query')
cap('ledger.read',obj({}),LEDGER,['sparge_vessel','trap_core'],'宿主逐物种、液体与气体守恒','query','evaluator')
cap('template.evaluate',obj({'substance_id':TEXT,'temperature_k':number('K',283.15,363.15)}),obj({'hscp_mol_m3_pa':number('mol/(m^3*Pa)',0),'water_to_gas_ratio':number('1',0),'evidence':TEXT,'template_source':TEXT}),['sparge_vessel'],'查询公开实测Henry参数与原文温度系数','query',ex={'substance_id':'benzene','temperature_k':298.15})
cap('template.reference',obj({'substance_id':TEXT}),obj({'retention_time_s':number('s',0),'average_rrf':number('1',0),'mdl_kg_m3':number('kg/m^3',0),'recovery_fraction':number('1',0),'evidence':TEXT,'template_source':TEXT}),['controller'],'查询AN2205指定GC/MS联机实验统计，保留外部检测器边界','query',ex={'substance_id':'benzene'})
for k in sorted(UNSUPPORTED):cap(k,obj({}),obj({}),['controller'],'当前子集未实现')
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='controller',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
for k in ['plant','method']:slot(k+'.configured',BOOL,'1',k+'已配置','setpoint')
slot('power.enabled',BOOL,'1','逻辑电源','setpoint',w=('power.set','enabled'))
for k,part,hi in [('guard','guard',1.4),('trap_door','trap_door',1.45)]:slot(k+'.angle_rad',number('rad',0,hi),'rad','人工维护门转角，非厂家互锁规格','scene',c=part,w=(k+'.set','angle_rad'))
for k,u,hi,part in [('gantry.x_m','m',.365,'gantry_x'),('gantry.y_m','m',.4,'gantry_y'),('gantry.z_m','m',.115,'gantry_z'),('elevator.lift_m','m',.1,'sample_elevator'),('syringe.stroke_m','m',.11,'syringe_plunger')]:slot(k,number(u,0,hi),u,'工程运动坐标，由宿主时钟控制','scene',c=part)
slot('sample.count',dict(number('1',0,84),type='integer'),'1','已装VOA瓶数量');slot('standard.loaded_count',dict(number('1',0,3),type='integer'),'1','有内标库存路数')
slot('sample.identity',TEXT,'1','当前样品身份','process_truth','evaluator');slot('acquisition.phase',PHASE,'1','异步流程状态');slot('acquisition.stage',TEXT,'1','当前实际流程阶段');slot('acquisition.elapsed_s',number('s',0),'s','本次流程时间');slot('acquisition.failure',TEXT,'1','流程失败原因')
slot('trap.temperature_k',nullable(number('K')),'K','理想温度显示，工程一阶热过程');slot('trap.target_k',nullable(number('K')),'K','捕集/解吸/烘烤温度目标','setpoint');slot('gas.flow_m3_s',number('m^3/s',0),'m^3/s','当前方法气体参考体积流量，温压由host明确提供');slot('gas.gauge_pressure_pa',number('Pa',0),'Pa','气路参考压降工程显示，非实测压力');slot('gc.start',BOOL,'1','开始解吸触发外部GC，阶段结束复位');slot('signal.running',BOOL,'1','流程运行状态灯');slot('gas.remaining_mol',number('mol',0),'mol','有限气源库存','process_truth','evaluator');slot('cell.volume_m3',number('m^3',0),'m^3','有限吹扫管当前液体库存','process_truth','evaluator')
def add(pool,sid,n):pool[sid]=pool.get(sid,0.)+n
def vial_target(i):return [.013+(i%7)*.033,.3765-(i//7)*.033,0.]
class TeledyneAtomxxyzRuntime:
 def __init__(self,instance_id='atomx_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.pending=0.;self.powered=False;self.plant=None;self.method=None;self.guard=0.;self.door=0.;self.q=[0.,0.,0.];self.elevator=0.;self.syringe=0.;self.vials={};self.standards={};self.loaded={};self.cell={};self.cell_v=0.;self.cell_soil=0.;self.kd={};self.trap={};self.gc={};self.external={};self.vent={};self.waste={};self.returned={};self.liquid_loaded=0.;self.liquid_waste=0.;self.liquid_returned=0.;self.water=0.;self.methanol=0.;self.gas=0.;self.gas_vent=0.;self.gas_gc=0.;self.phase='idle';self.stage='idle';self.elapsed=0.;self.stage_elapsed=0.;self.temp=None;self.target=None;self.flow=0.;self.failure='';self.index=None;self.points=[];self.serial=0;self.operation_id=None;self.records={};self._principals={};self._owners={};self._requests={};self.templates=json.loads((ASSET/'henry_library.json').read_text())['substances'];self.references=json.loads((ASSET/'application_library.json').read_text())['substances']
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ['agent','evaluator','debug'] or principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return TeledyneAtomxxyzSession(self,principal_id)
 def describe(self):return export_teledyne_atomxxyz_contract_metadata()
 def henry(self,sid,t):
  if sid not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
  v=self.templates[sid];a=v['temperature_coefficient_k']
  if a is None and abs(t-298.15)>1e-6:raise ActionRejected('TEMPERATURE_COEFFICIENT_MISSING')
  return v['henry_hscp_mol_m3_pa']*math.exp((a or 0.)*(1/t-1/298.15))
 def read(self,visibility='agent'):
  d={'plant.configured':self.plant is not None,'method.configured':self.method is not None,'power.enabled':self.powered,'guard.angle_rad':self.guard,'trap_door.angle_rad':self.door,'gantry.x_m':self.q[0],'gantry.y_m':self.q[1],'gantry.z_m':self.q[2],'elevator.lift_m':self.elevator,'syringe.stroke_m':self.syringe,'sample.count':len(self.vials),'standard.loaded_count':len(self.standards),'sample.identity':self.vials.get(self.index,{}).get('sample_id',''),'acquisition.phase':self.phase,'acquisition.stage':self.stage,'acquisition.elapsed_s':self.elapsed,'acquisition.failure':self.failure,'trap.temperature_k':self.temp,'trap.target_k':self.target,'gas.flow_m3_s':self.flow,'gas.gauge_pressure_pa':self.plant['pressure_drop_pa'] if self.flow and self.plant else 0.,'gc.start':self.stage=='desorb' and self.phase=='running','signal.running':self.phase=='running','gas.remaining_mol':self.gas,'cell.volume_m3':self.cell_v}
  return copy.deepcopy({k:v for k,v in d.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _ledger(self):
  rows=[]
  for sid,n in self.loaded.items():
   values=dict(vials_mol=sum(v['n'].get(sid,0.)+v.get('extracted',{}).get(sid,0.) for v in self.vials.values()),standards_mol=sum(v['n'].get(sid,0.) for v in self.standards.values()),cell_mol=self.cell.get(sid,0.),trap_mol=self.trap.get(sid,0.),gc_mol=self.gc.get(sid,0.),external_mol=self.external.get(sid,0.),vent_mol=self.vent.get(sid,0.),waste_mol=self.waste.get(sid,0.),returned_mol=self.returned.get(sid,0.));rows.append(dict(substance_id=sid,loaded_mol=n,residual_mol=n-sum(values.values()),**values))
  remain=self.water+self.methanol+self.cell_v+sum(x['v'] for x in self.vials.values())+sum(x['v'] for x in self.standards.values());return dict(species=rows,liquid_loaded_m3=self.liquid_loaded,liquid_remaining_m3=remain,liquid_waste_m3=self.liquid_waste,liquid_returned_m3=self.liquid_returned,liquid_residual_m3=self.liquid_loaded-remain-self.liquid_waste-self.liquid_returned,gas_remaining_mol=self.gas,gas_vent_mol=self.gas_vent,gas_gc_mol=self.gas_gc,gas_residual_mol=(self.plant['gas_supply_mol'] if self.plant else 0.)-self.gas-self.gas_vent-self.gas_gc)
 def _result(self):return dict(phase=self.phase,stage=self.stage,complete=self.phase=='succeeded',elapsed_s=self.elapsed,points=copy.deepcopy(self.points),gc_start=self.stage=='desorb' and self.phase=='running',quality='engineering_process',template_source='Sander2023 measured Henry records; AN2205 method; identities withheld',evidence='measured equilibrium inputs with explicitly assumed kinetics; not detector measurements',warnings=['External GC/MS required; no concentration inferred from hidden inventory','Finite dilute-water equilibrium with host transfer/capture/desorption inputs','Soil extraction and methanol-water transfer are uncalibrated approximations','Motion and valve timing, pressure drop and thermal lag are engineering proxies'])
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[k][0],a);before=copy.deepcopy(self.__dict__)
  try:
   r=self._invoke(k,a);validate(CAPS[k][1],r)
   if CAPS[k][4] not in ['query','analysis']:self.revision+=1
   return r
  except Exception:self.__dict__.clear();self.__dict__.update(before);raise
 def _components(self,components):
  if len({x['substance_id'] for x in components})!=len(components):raise ActionRejected('DUPLICATE_SPECIES')
  for x in components:
   if x['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
 def _invoke(self,k,a):
  if k=='process.measure':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='template.evaluate':
   h=self.henry(a['substance_id'],a['temperature_k']);v=self.templates[a['substance_id']];return dict(hscp_mol_m3_pa=h,water_to_gas_ratio=h*R*a['temperature_k'],evidence=v['evidence']+'; temperature expression from original source',template_source=v['source']['doi']+' reference '+v['source']['reference_id'])
  if k=='template.reference':
   if a['substance_id'] not in self.references:raise ActionRejected('UNKNOWN_REFERENCE')
   v=self.references[a['substance_id']];return dict(retention_time_s=v['retention_time_s'],average_rrf=v['average_rrf'],mdl_kg_m3=v['mdl_kg_m3'],recovery_fraction=v['midpoint_recovery_fraction'],evidence='measured_literature; Atomx+TRACE1610+ISQ7610, source-specific method',template_source=v['source']['url']+' TableIII')
  if k=='plant.configure':
   if self.plant or self.powered or not a['accept_engineering_model']:raise ActionRejected('INITIALIZATION_ONLY')
   self.plant=copy.deepcopy(a);self.water=a['water_supply_m3'];self.methanol=a['methanol_supply_m3'];self.gas=a['gas_supply_mol'];self.liquid_loaded+=self.water+self.methanol;self.temp=a['ambient_k'];self.target=a['ambient_k'];return {'configured':True}
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self.phase='cancelled';self.flow=0.;self.target=self.plant['ambient_k']
   v=self.vials.get(self.index,{})
   for sid,n in v.pop('extracted',{}).items():add(v['n'],sid,n)
   self.records[self.operation_id]=self._result();return {'phase':self.phase}
  if k=='power.set':
   if not a['enabled'] and self.phase=='running':self._invoke('acquisition.stop',{})
   self.powered=a['enabled'];return a
  if k=='gas.collect':
   packet=[dict(substance_id=sid,moles=n) for sid,n in self.gc.items() if n>0]
   for x in packet:add(self.external,x['substance_id'],x['moles'])
   self.gc={};return dict(species=packet,evidence='finite host species packet; external GC transport interface, not observed detector result')
  if self.phase=='running':raise ActionRejected('BUSY')
  if k in ['guard.set','trap_door.set']:
   if k=='trap_door.set' and self.temp is not None and self.temp>323.15:raise ActionRejected('HOT_TRAP')
   if k=='guard.set':self.guard=a['angle_rad']
   else:self.door=a['angle_rad']
   return a
  if k=='method.configure':
   if not self.plant or not a['accept_water_henry_transfer']:raise ActionRejected('PRECONDITION_FAILED')
   if abs(a['sample_volume_m3']/1e-7-round(a['sample_volume_m3']/1e-7))>1e-6:raise ActionRejected('DISPENSE_GRID')
   if a['sample_volume_m3']>a['sparger_capacity_m3']+1e-12 or (a['matrix']=='soil_high' and (a['sample_volume_m3']!=5e-6 or a['dilution'] not in [50,100])) or (a['matrix']=='soil_low' and a['dilution']!=1):raise ActionRejected('METHOD_MATRIX')
   for name in ['purge_s','dry_purge_s','desorb_s','bake_s','extraction_s']:
    if abs(a[name]/DT-round(a[name]/DT))>1e-6:raise ActionRejected('METHOD_GRID')
   self.method=copy.deepcopy(a);return {'configured':True}
  if k=='sample.load':
   self._components(a['components']);i=a['vial_index']
   if i in self.vials or (a['matrix']=='water' and (a['liquid_volume_m3']==0 or a['soil_mass_kg']!=0)) or (a['matrix']!='water' and a['soil_mass_kg']==0):raise ActionRejected('LOAD_PRECONDITION')
   v=dict(sample_id=a['sample_id'],matrix=a['matrix'],v=a['liquid_volume_m3'],soil=a['soil_mass_kg'],n={x['substance_id']:x['moles'] for x in a['components']},kd={x['substance_id']:x['soil_kd_m3_kg'] for x in a['components']});self.vials[i]=v;self.liquid_loaded+=v['v']
   for sid,n in v['n'].items():add(self.loaded,sid,n)
   return {'loaded':True}
  if k=='sample.unload':
   i=a['vial_index']
   if i not in self.vials:raise ActionRejected('EMPTY_POSITION')
   v=self.vials.pop(i);self.liquid_returned+=v['v']
   for sid,n in v['n'].items():add(self.returned,sid,n)
   return {'returned':True}
  if k=='standard.load':
   self._components(a['components'])
   if a['index'] in self.standards:raise ActionRejected('STANDARD_ALREADY_LOADED')
   v=dict(v=a['volume_m3'],n={x['substance_id']:x['moles'] for x in a['components']});self.standards[a['index']]=v;self.liquid_loaded+=v['v']
   for sid,n in v['n'].items():add(self.loaded,sid,n)
   return {'loaded':True}
  if k=='system.rinse':
   if not self.plant or self.water+1e-14<a['volume_m3']:raise ActionRejected('WATER_DEPLETED')
   self.water-=a['volume_m3'];self.liquid_waste+=self.cell_v+a['volume_m3'];self.cell_v=0.
   for sid,n in self.cell.items():add(self.waste,sid,n)
   self.cell={};self.cell_soil=0.;self.kd={};self.syringe=0.;return {'rinsed_volume_m3':a['volume_m3']}
  if k=='acquisition.start':
   i=a['vial_index'];m=self.method;p=self.plant
   if not self.powered or not m or not p or self.guard>.001 or self.door>.001 or i not in self.vials or self.cell_v>1e-12:raise ActionRejected('NOT_READY')
   v=self.vials[i]
   if v['matrix']!=m['matrix']:raise ActionRejected('METHOD_MATRIX')
   for sid in set(v['n'])|{sid for s in self.standards.values() for sid in s['n']}:self.henry(sid,m['purge_k'])
   dose=sum(m['standard_doses_m3']);base=m['sample_volume_m3']-dose
   if base<=0:raise ActionRejected('METHOD_MATRIX')
   aliquot=base/m['dilution'];water=base-aliquot
   if m['matrix']=='water' and v['v']+1e-14<aliquot:raise ActionRejected('SAMPLE_DEPLETED')
   if m['matrix']=='soil_low':
    water=base
    if v['v']+water+dose>40e-6:raise ActionRejected('VIAL_OVERFLOW')
   if m['matrix']=='soil_high' and (self.methanol<5e-6 or v['v']+5e-6>40e-6):raise ActionRejected('METHANOL_DEPLETED')
   if self.water+1e-14<water+m['rinse_volume_m3']*m['rinse_count']:raise ActionRejected('WATER_DEPLETED')
   for j,d in enumerate(m['standard_doses_m3']):
    if d and (j not in self.standards or self.standards[j]['v']+1e-14<d):raise ActionRejected('STANDARD_DEPLETED')
   self.index=i;self.phase='running';self.stage='position';self.stage_elapsed=0.;self.elapsed=0.;self.points=[];self.failure='';self.serial+=1;self.operation_id=self.instance_id+'_op_'+str(self.serial);self.target=313.15;self.move_phase=0;return dict(phase=self.phase,operation_id=self.operation_id)
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _stage(self,s):self.stage=s;self.stage_elapsed=0.
 def _fail(self,code):self.failure=code;self.phase='failed';self.flow=0.;self.target=self.plant['ambient_k'];self.records[self.operation_id]=self._result()
 def _transfer(self,pool,dest,f):
  for sid,n in list(pool.items()):v=n*f;pool[sid]-=v;add(dest,sid,v)
 def _prepare(self):
  m=self.method;v=self.vials[self.index];dose=sum(m['standard_doses_m3']);base=m['sample_volume_m3']-dose;aliquot=base/m['dilution'];water=base-aliquot
  if m['matrix']=='soil_low':
   water=base;self.cell_v=v['v']+water;v['v']=0.;self._transfer(v['n'],self.cell,1.);self.cell_soil=v['soil'];self.kd=copy.deepcopy(v['kd'])
  else:
   pool=v['extracted'] if m['matrix']=='soil_high' else v['n'];self._transfer(pool,self.cell,aliquot/v['v']);v['v']-=aliquot;self.cell_v=base
   if m['matrix']=='soil_high':
    for sid,n in v.pop('extracted').items():add(v['n'],sid,n)
  self.water-=water
  for j,d in enumerate(m['standard_doses_m3']):
   if d:s=self.standards[j];self._transfer(s['n'],self.cell,d/s['v']);s['v']-=d;self.cell_v+=d
  self.syringe=.11*m['sample_volume_m3']/25e-6;self.elevator=.1;self._stage('purge')
 def _motion(self,target):
  # Safe transit: retract Z, translate X/Y, then lower. Exact finite speed per host tick.
  speed=self.plant['motion_speed_m_s']*DT
  if self.move_phase==0:ids=[2];dest=[self.q[0],self.q[1],0.]
  elif self.move_phase==1:ids=[0,1];dest=target
  else:ids=[2];dest=target
  for i in ids:self.q[i]+=max(-speed,min(speed,dest[i]-self.q[i]))
  if all(abs(self.q[i]-dest[i])<1e-10 for i in ids):self.move_phase+=1
  return self.move_phase>=3
 def _tick(self):
  self.time_s=round(self.time_s+DT,10)
  if self.plant and self.temp is not None:
   t=self.target if self.powered else self.plant['ambient_k'];self.temp+=(t-self.temp)*(-math.expm1(-DT/self.plant['thermal_tau_s']))
  if self.phase!='running':return
  self.elapsed=round(self.elapsed+DT,10);self.stage_elapsed=round(self.stage_elapsed+DT,10);m=self.method;p=self.plant;v=self.vials[self.index];self.flow=0.
  if self.stage=='position':
   target=vial_target(self.index);target[2]=.09
   if self._motion(target):self.move_phase=0;self._stage('to_station')
  elif self.stage=='to_station':
   if self._motion([.33,.04,0.]):
    self.move_phase=0
    if m['matrix']=='soil_high':
     self.methanol-=5e-6;v['v']+=5e-6;v['extracted']={};self._stage('extract')
    else:self._stage('prepare')
  elif self.stage=='extract':
   self._transfer(v['n'],v['extracted'],-math.expm1(-p['soil_extraction_rate_s_inv']*DT))
   if self.stage_elapsed>=m['extraction_s']:self._stage('prepare')
  elif self.stage=='prepare':
   self.syringe=.11*m['sample_volume_m3']/25e-6*min(1.,self.stage_elapsed/2.)
   if self.stage_elapsed>=2.:self._prepare()
  elif self.stage in ['purge','dry','desorb','bake']:
   stage=self.stage;self.flow=m[{'purge':'purge_flow_m3_s','dry':'dry_flow_m3_s','desorb':'desorb_carrier_m3_s','bake':'bake_flow_m3_s'}[stage]];dn=self.flow*p['gas_reference_pa']/(R*p['gas_reference_k'])*DT
   if self.gas+1e-14<dn:self._fail('GAS_DEPLETED');return
   self.gas-=dn
   if stage=='desorb':self.gas_gc+=dn
   else:self.gas_vent+=dn
   if stage=='purge':
    desired={}
    # Actual gas volume at sample T; Henry c_l/p, gas ideal pressure consistent with Q reference.
    qactual=self.flow*m['purge_k']/p['gas_reference_k']
    for sid,n in self.cell.items():
     hcc=self.henry(sid,m['purge_k'])*R*m['purge_k'];den=self.cell_v+self.kd.get(sid,0.)*self.cell_soil;rate=p['transfer_efficiency']*qactual/(hcc*den);desired[sid]=n*(-math.expm1(-rate*DT))
    total=sum(desired.values())*p['trap_capture_fraction'];available=max(0.,p['trap_capacity_mol']-sum(self.trap.values()));f=min(1.,available/total) if total else 1.
    for sid,n in desired.items():captured=n*p['trap_capture_fraction']*f;self.cell[sid]-=n;add(self.trap,sid,captured);add(self.vent,sid,n-captured)
    if self.stage_elapsed>=m['purge_s']:self.syringe=0.;self._stage('dry' if m['dry_purge_s'] else 'preheat')
   elif stage=='dry':
    if self.stage_elapsed>=m['dry_purge_s']:self._stage('preheat')
   elif stage=='desorb':
    self._transfer(self.trap,self.gc,-math.expm1(-p['desorb_rate_s_inv']*DT))
    if self.stage_elapsed>=m['desorb_s']:self.target=m['bake_k'];self._stage('bake')
   elif stage=='bake':
    self._transfer(self.trap,self.vent,-math.expm1(-p['bake_rate_s_inv']*DT))
    if self.stage_elapsed>=m['bake_s']:self.target=313.15;self._stage('rinse')
  elif self.stage=='preheat':
   self.target=m['desorb_k']
   if abs(self.temp-self.target)<1.:self._stage('desorb')
  elif self.stage=='rinse':
   if self.stage_elapsed>=2.:
    rinse=m['rinse_volume_m3']*m['rinse_count'];self.water-=rinse;self.liquid_waste+=rinse
    if m['matrix']=='soil_low':
     v['v']+=self.cell_v;self._transfer(self.cell,v['n'],1.)
    else:self.liquid_waste+=self.cell_v;self._transfer(self.cell,self.waste,1.)
    self.cell={};self.cell_v=0.;self.cell_soil=0.;self.kd={};self.elevator=0.;self.syringe=0.;self.move_phase=0;self._stage('return')
  elif self.stage=='return':
   if self._motion(vial_target(self.index)):self.move_phase=0;self._stage('cooldown')
  elif self.stage=='cooldown':
   if abs(self.temp-313.15)<1.:self.phase='succeeded';self.flow=0.;self._stage('ready')
  if abs(self.elapsed-round(self.elapsed))<1e-8:self.points.append(dict(time_s=self.elapsed,stage=self.stage,gas_flow_m3_s=self.flow,trap_temperature_k=self.temp,gauge_pressure_pa=p['pressure_drop_pa'] if self.flow else 0.))
  if self.phase!='running':self.records[self.operation_id]=self._result()
 def step(self,dt):
  validate(number('s',0,86400),dt)
  if not dt:return
  self.pending+=dt
  while self.pending+1e-12>=DT:self._tick();self.pending=max(0.,self.pending-DT)
  self.revision+=1

class TeledyneAtomxxyzSession:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,i):
  if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  m=export_teledyne_atomxxyz_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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





def export_teledyne_atomxxyz_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions='Flow reference temperature and absolute pressure explicitly provided by plant; gas pressure display gauge',origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'TeledyneAtomxxyzSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description='last fixed0.1s host tick; logs1s'),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNKNOWN_OPERATION','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','INITIALIZATION_ONLY','BUSY','UNKNOWN_REFERENCE','NOT_READY','NOT_RUNNING','METHOD_GRID','DISPENSE_GRID','METHOD_MATRIX','LOAD_PRECONDITION','DUPLICATE_SPECIES','SAMPLE_DEPLETED','STANDARD_DEPLETED','WATER_DEPLETED','METHANOL_DEPLETED','STANDARD_ALREADY_LOADED','EMPTY_POSITION','TEMPERATURE_COEFFICIENT_MISSING','HOT_TRAP','VIAL_OVERFLOW']
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  unsupported=k in UNSUPPORTED;asyn=k=='acquisition.start';caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.teledyne_atomxxyz.TeledyneAtomxxyzSession.invoke',invocation=f'session.invoke(instance_id="atomx_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if kind=='query' or unsupported else [dict(description_zh='显式物料/方法、门状态/温度/库参数域、有限库存与矩阵检查，动作拒绝原子回退',state_ids=['power.enabled','method.configured','sample.count','guard.angle_rad','trap_door.angle_rad','acquisition.phase'],enforced_by='runtime',check='Runtime.invoke atomic rollback, trusted Session')],state_effects=[dict(state_id=x,timing='immediate',description_zh='受检调用改变状态') for x in effects],observes=[],model_effects=dict(mode='none',description_zh='待独立场景验证',binding_ids=[]),execution=dict(mode='asynchronous' if asyn else 'synchronous',time_domain='simulation',completion_criterion='样品归位且捕集阱冷却，过程完整日志' if asyn else '受检事务返回，查询不推进时间',completion_state_ids=['acquisition.phase'] if asyn else [],timeout_s=None,cancellation='supported' if asyn else 'unsupported',retry='same_request_id',resources=['host_clock','vials','cell','trap','solvents','gas','standards','request_cache'],partial_failure_zh='保留所有已转移物种/溶剂和未解吸残留；取消不回滚；去重只在当前host寿命内'),errors=[dict(code=x,meaning_zh=x,side_effects='none') for x in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=['真实噪声、GC响应、土壤甲醇提取/吸附热动力学未校准'],source_ids=['manufacturer_primary','simulation_assumption'])
  if asyn:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='固定0.1s物质/运动/热状态推进') for x in ['gantry.x_m','gantry.y_m','gantry.z_m','elevator.lift_m','syringe.stroke_m','trap.temperature_k','acquisition.stage','acquisition.elapsed_s','acquisition.phase']]
 mech=dict(component_ids=['controller','gantry_x','gantry_y','gantry_z','sample_elevator','syringe_plunger','sparge_vessel','trap_core','gc_out_port'],state_ids=list(slots),meaning_zh='有限库存、Henry气液分配、容量限制捕集、热解吸、气体送外部GC及归位冲洗',implementation='lumped',equations=['Hscp(T)=Hscp298*exp(a*(1/T-1/298.15)); c_water/p_gas, only original measured M records','Hcc=Hscp*R*T; dn=n*(1-exp(-efficiency*Q_actual*dt/(Hcc*(V+Kd*Msoil))))','capture=min(removed*capture_fraction,remaining_shared_trap_capacity); shared capacity allocation proportional to desired uptake','desorb dn=trap_n*(1-exp(-k_desorb*dt)); bake analogous to vent; trap mass is retained across cycles','high-soil extraction dn=bound_n*(1-exp(-k_extract*dt)); finite5mL methanol; aliquot to final5mL at50/100 dilution','sample aliquot=(target_volume-standard_doses)/dilution; DI fills declared total; finite standard vessels','gas dn=Q_reference*P_reference*dt/(R*T_reference); external carrier consumed from declared common gas inventory','Ttrap += (Ttarget-Ttrap)*(1-exp(-dt/tau)); fixed0.1s ticks, safe retract/translate/lower geometry','Every analyte: loaded=vials+standards+cell+trap+GC+external+vent+waste+returned; solvent volume and gas moles separately conserved'],parameters={},source_ids=['manufacturer_primary','manufacturer_an2205','sander2023','simulation_assumption'],limitations=['77 real measured aqueous Henry constants and81 source-specific combined-GC/MS statistical records; no raw measured gas-release traces.','Soil Kd/extraction, methanol cosolvent transfer, mixed-solute additivity, trap efficiency/capacity, desorption and thermal lag are explicit uncalibrated host inputs.','External GC/MS required; returns finite gas to trusted host and agent-visible process logs; no detector identity/concentration leakage.','Single active sample, up to84 independent vial inventories; no scheduler queue or firmware/USB implementation.','Nominal volumetric steps and doses are checked; no precision/noise/fault-probability claims.','84 visible fixed vial templates; robot grasp, collision transfer and separate filled-vial masses not_run.','Common gas inventory approximates purge and external desorption carrier; no detailed valves/MCS water vapor/multi-bed adsorption PDE.'])
 spec=importlib.util.spec_from_file_location('_atomx_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'purge_trap':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'。'
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'purge_trap':mech})

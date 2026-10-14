"""CHEM-099 GPC/SEC: finite mass transport and observed-signal molecular weight analysis.
Trusted Python host only. Sessions filter identity/truth; process/file isolation is external.
"""
from pathlib import Path
import copy,math,json,importlib.util
PROTOCOL_VERSION='1.0.0';SLUG='agilent_1290infinity';CATALOG_ID='CHEM-099';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];TEMPLATE_REL='assets/expansion_batch04/t3_analytical/'+SLUG+'/sec_library.json'
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
import numpy as np
COLUMN=obj({'polymer_family':enum(['ps','pmma','peg_peo']),'solvent':TEXT,'log10_intercept_kg_mol':number(),'log10_slope_per_m3':number('1/m^3',-1e9,-1.),'void_m3':number('m^3',1e-9,.001),'total_m3':number('m^3',1e-9,.01),'band_sigma_m3':number('m^3',1e-10,.001),'resistance_pa_s_m3':number('Pa*s/m^3',1,1e17),'max_pressure_pa':number('Pa',1,1.3e8)})
COLUMN_EXAMPLE=dict(polymer_family='ps',solvent='THF',log10_intercept_kg_mol=6.,log10_slope_per_m3=-4e6,void_m3=2e-7,total_m3=1.8e-6,band_sigma_m3=2e-9,resistance_pa_s_m3=1e14,max_pressure_pa=2e7)
PLANT=obj({'ambient_k':number('K',277.15,313.15),'reservoir_m3':number('m^3',1e-9,.1),'waste_capacity_m3':number('m^3',1e-9,.1),'thermal_tau_s':number('s',.01,1e5),'columns':arr(COLUMN,8),'accept_synthetic_separation':BOOL})
PLANT_EXAMPLE=dict(ambient_k=298.15,reservoir_m3=.002,waste_capacity_m3=.002,thermal_tau_s=2.,columns=[dict(COLUMN_EXAMPLE) for _ in range(8)],accept_synthetic_separation=True)
METHOD=obj({'flow_m3_s':number('m^3/s',1e-9/60,5e-6/60),'column_index':dict(number('1',0,7),type='integer'),'zone_targets_k':arr(number('K',277.15,383.15),2),'injection_m3':number('m^3',1e-10,1e-7),'run_s':number('s',.1,7200),'solvent':TEXT,'dn_dc_m3_kg':number('m^3/kg',1e-8,.01)})
METHOD_EXAMPLE=dict(flow_m3_s=1e-6/60,column_index=0,zone_targets_k=[313.15,313.15],injection_m3=1e-8,run_s=120.,solvent='THF',dn_dc_m3_kg=.000185)
SOLUTE=obj({'substance_id':TEXT,'concentration_kg_m3':number('kg/m^3',0,100)})
SAMPLE=obj({'sample_id':TEXT,'volume_m3':number('m^3',1e-10,.01),'solvent':TEXT,'solutes':dict(arr(SOLUTE),minItems=1,maxItems=73)})
SAMPLE_EXAMPLE=dict(sample_id='PS_mix',volume_m3=1e-6,solvent='THF',solutes=[dict(substance_id='pl2013_1001',concentration_kg_m3=.1),dict(substance_id='pl2013_5001',concentration_kg_m3=.1)])
POINT=obj({'time_s':number('s',0),'window_s':number('s',0),'volume_m3':number('m^3',0),'window_m3':number('m^3',0),'delta_n':number('1',-.0006,.0006),'saturated':BOOL})
RESULT=obj({'phase':PHASE,'complete':BOOL,'points':arr(POINT),'elapsed_s':number('s',0),'template_source':TEXT,'evidence':TEXT,'warnings':arr(TEXT)})
CAL_POINT=obj({'elution_volume_m3':number('m^3',1e-12,.01),'mp_kg_mol':number('kg/mol',1e-6,1e7)})
CAL=obj({'points':dict(arr(CAL_POINT),minItems=2,maxItems=100),'basis':enum(['ps','pmma','peg_peo']),'accept_nominal_or_external_standard':BOOL})
CAL_EXAMPLE=dict(points=[dict(elution_volume_m3=5e-7,mp_kg_mol=1e4),dict(elution_volume_m3=15e-7,mp_kg_mol=1.)],basis='ps',accept_nominal_or_external_standard=True)
ANALYSIS=obj({'mn_kg_mol':nullable(number('kg/mol',0)),'mw_kg_mol':nullable(number('kg/mol',0)),'dispersity':nullable(number('1',1)),'mass_kg':nullable(number('kg',0)),'quality':TEXT,'basis':TEXT,'evidence':TEXT})
LEDGER=obj({'reservoir_m3':number('m^3',0),'waste_m3':number('m^3',0),'sample_remaining_m3':number('m^3',0),'volume_residual_m3':number('m^3'),'mass_residual_kg':number('kg'),'injected_kg':number('kg',0),'column_kg':number('kg',0),'eluted_kg':number('kg',0),'sample_id':TEXT})
CAPS={};UNSUPPORTED={'hardware.connect','absolute_molar_mass.measure'}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},effects or [])
cap('plant.configure',PLANT,obj({'configured':BOOL}),['housing','pump_drive'],'宿主有限流体、双温区时间常数及8个柱位的显式分离/阻力模型','configuration','evaluator',PLANT_EXAMPLE,['plant.configured'])
cap('method.configure',METHOD,obj({'configured':BOOL}),['pump_drive','column_bay','rid_cell'],'泵降额限压、柱压力/兼容性、双温区与100微升进样配置','configuration','agent',METHOD_EXAMPLE,['method.configured','pump.target_m3_s','zone1.target_k','zone2.target_k','column.selected'])
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_tray'],'打开抽屉后连接有限聚合物溶液，物质记录由模板提供','configuration','evaluator',SAMPLE_EXAMPLE,['sample.loaded'])
cap('sample.unload',obj({}),obj({'returned_m3':number('m^3',0)}),['sample_tray'],'归还未注入样液；保留柱内已注入质量','device_command','evaluator',effects=['sample.loaded'])
for k,part,z in [('power.set','power_inlet','设供电，断电取消采集并停泵目标'),('pump.set','pump_drive','只设泵运行目标；仅step推进流量')]:cap(k,obj({'enabled':BOOL}),obj({'enabled':BOOL}),[part],z,ex={'enabled':True},effects=['power.enabled' if k=='power.set' else 'pump.enabled'])
MOTION=[('sampler_door','door.angle_rad','angle_rad','rad',1.8),('sample_drawer','drawer.position_m','position_m','m',.22),('column_cover','column_cover.angle_rad','angle_rad','rad',1.75)]
for part,sid,arg,unit,limit in MOTION:cap(sid.rsplit('.',1)[0]+'.set',obj({arg:number(unit,0,limit)}),obj({arg:number(unit,0,limit)}),[part],'宿主过程权威机构位置；运行/泵开启时拒绝检修动作',ex={arg:0.},effects=[sid])
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['injection_valve','column_bay','rid_cell'],'有限样液注入、异步获取RID洗脱图，支持取消',effects=['acquisition.phase','acquisition.elapsed_s'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['pump_drive','rid_cell'],'停止泵目标，保留已注入和已洗脱物质',effects=['acquisition.phase','pump.enabled'])
cap('process.measure',obj({}),RESULT,['rid_cell'],'返回已经采集的RID窗口，无推进时钟','query')
cap('calibration.fit',CAL,obj({'intercept_log10_kg_mol':number(),'slope_per_m3':number('1/m^3'),'r_squared':number('1',0,1)}),['column_bay'],'从调用者提供的标准峰体积与Mp拟合logM校准，不访问未知样品真值','analysis','agent',CAL_EXAMPLE,['calibration.ready'])
cap('molecular_weight.analyze',obj({}),ANALYSIS,['rid_cell'],'对实测窗口质量权重按已拟合校准计算Mn/Mw/分散系数；失败质量标签和null','analysis')
cap('template.read',obj({'substance_id':TEXT}),obj({'substance_id':TEXT,'mp_kg_mol':number('kg/mol'),'dispersity':number('1',1),'template_source':TEXT,'evidence':TEXT}),['rid_cell'],'查公开厂家名义标准模板，与样品身份隔离','query',ex={'substance_id':'pl2013_1001'})
cap('ledger.read',obj({}),LEDGER,['sample_tray','waste_port','column_bay'],'私有有限体积、进柱/柱内/流出质量守恒账本','query','evaluator')
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['housing'],'本配置未实现真实硬件或无校准绝对分子量探测')
CAPS['power.set'][7].extend(['pump.enabled','acquisition.phase'])
CAPS['method.configure'][7].append('calibration.ready')
for _k in ['sample.load','sample.unload']:CAPS[_k][7].append('sample.identity')
CAPS['plant.configure'][7].extend(['zone1.temperature_k','zone2.temperature_k','zone1.indicated_k','zone2.indicated_k'])
CAPS['acquisition.start'][7].extend(['detector.delta_n','detector.saturated'])
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='rid_cell',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','8柱位的宿主流体边界已配置','setpoint',c='housing')
slot('power.enabled',BOOL,'1','整套模块的逻辑供电目标','setpoint',c='power_inlet',w=('power.set','enabled'))
slot('pump.enabled',BOOL,'1','泵运行目标','setpoint',c='pump_drive',w=('pump.set','enabled'))
slot('pump.target_m3_s',nullable(number('m^3/s',0)),'m^3/s','实际液体流量目标','setpoint',c='pump_drive')
slot('pump.flow_m3_s',number('m^3/s',0),'m^3/s','最近step实际液体平均流量')
slot('pump.pressure_pa',number('Pa',0),'Pa','柱阻力乘实际流量，泵出口表压真值','process_truth','evaluator','pump_drive')
slot('pump.indicated_pa',number('Pa',0),'Pa','理想化泵表压观测，环境为零')
slot('method.configured',BOOL,'1','有效方法已配置','setpoint')
slot('column.selected',nullable(dict(number('1',0,7),type='integer')),'1','选用8个10cm柱位之一；不是8支30cm柱','setpoint',c='column_bay')
for i in [1,2]:
 slot(f'zone{i}.target_k',nullable(number('K',277.15,383.15)),'K','柱箱独立温区目标','setpoint',c='column_bay')
 slot(f'zone{i}.temperature_k',nullable(number('K')),'K','单节点温区温度真值','process_truth','evaluator','column_bay')
 slot(f'zone{i}.indicated_k',nullable(number('K')),'K','无噪声理想温区温度观测')
for part,sid,arg,unit,limit in MOTION:slot(sid,number(unit,0,limit),unit,'机构位置，零闭合；正方向见manifest','scene',c=part,w=(sid.rsplit('.',1)[0]+'.set',arg))
slot('sample.loaded',BOOL,'1','外部有限样液连接',c='sample_tray');slot('sample.identity',TEXT,'1','样品身份真值','process_truth','evaluator','sample_tray')
slot('acquisition.phase',PHASE,'1','异步采集阶段');slot('acquisition.elapsed_s',number('s',0),'s','采集实际时间');slot('detector.delta_n',nullable(number('1',-.0006,.0006)),'1','RI差值，±600e-6饱和剪裁；显式假设dn/dc响应');slot('detector.saturated',BOOL,'1','本采集出现过RID量程饱和');slot('calibration.ready',BOOL,'1','当前方法签名对应的校准可用')

class Agilent1290infinityRuntime:
 def __init__(self,instance_id='sec_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.pump=False;self.motion={s:0. for _,s,_,_,_ in MOTION};self.plant=None;self.method=None;self.sample=None;self.temperature=[None,None];self.reservoir=0.;self.waste=0.;self.sample_loaded=0.;self.sample_returned=0.;self.flow=0.;self.pressure=0.;self.bands=[];self.phase='idle';self.elapsed=0.;self.volume=0.;self.points=[];self.signal=None;self.saturated=False;self.calibration=None;self.operation_id=None;self.serial=0;self.records={};self.templates=json.loads((ROOT/TEMPLATE_REL).read_text())['records'];self._principals={};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ['agent','evaluator','debug'] or principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return Agilent1290infinitySession(self,principal_id)
 def describe(self):return export_agilent_1290infinity_contract_metadata()
 def read(self,visibility='agent'):
  m=self.method or {};d={'plant.configured':self.plant is not None,'power.enabled':self.powered,'pump.enabled':self.pump,'pump.target_m3_s':m.get('flow_m3_s'),'pump.flow_m3_s':self.flow,'pump.pressure_pa':self.pressure,'pump.indicated_pa':self.pressure,'method.configured':self.method is not None,'column.selected':m.get('column_index'),'sample.loaded':self.sample is not None,'sample.identity':self.sample['sample_id'] if self.sample else '','acquisition.phase':self.phase,'acquisition.elapsed_s':self.elapsed,'detector.delta_n':self.signal,'detector.saturated':self.saturated,'calibration.ready':self.calibration is not None,**self.motion}
  for i in range(2):d.update({f'zone{i+1}.target_k':m.get('zone_targets_k',[None,None])[i],f'zone{i+1}.temperature_k':self.temperature[i],f'zone{i+1}.indicated_k':self.temperature[i]})
  return copy.deepcopy({k:v for k,v in d.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _ledger(self):
  injected=sum(x['mass'] for x in self.bands);eluted=sum(x['eluted'] for x in self.bands);remain=sum(x['mass']-x['eluted'] for x in self.bands);sample=self.sample['remaining'] if self.sample else 0.;initial=self.plant['reservoir_m3'] if self.plant else 0.
  return dict(reservoir_m3=self.reservoir,waste_m3=self.waste,sample_remaining_m3=sample,volume_residual_m3=initial+self.sample_loaded-self.reservoir-self.waste-self.sample_returned-sample,mass_residual_kg=injected-eluted-remain,injected_kg=injected,column_kg=remain,eluted_kg=eluted,sample_id=self.sample['sample_id'] if self.sample else '')
 def _result(self):return dict(phase=self.phase,complete=self.phase=='succeeded',points=copy.deepcopy(self.points),elapsed_s=self.elapsed,template_source='Agilent 5990-7996E standard-material nominal Mp/dispersity; sample identities withheld',evidence='documented nominal standards + synthetic mass distribution, retention, broadening and dn/dc',warnings=['No batch COA or experimental column calibration','Single concentration detector gives conventional relative molecular mass; not absolute mass','Cancelled/failed runs retain injected and eluted mass; read never advances time'])
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[k][0],a);before=copy.deepcopy(self.__dict__)
  try:
   result=self._invoke(k,a);validate(CAPS[k][1],result)
   if CAPS[k][4] not in ['query','analysis'] or k=='calibration.fit':self.revision+=1
   return result
  except Exception:self.__dict__.clear();self.__dict__.update(before);raise
 def _invoke(self,k,a):
  if k=='process.measure':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='template.read':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   t=self.templates[a['substance_id']];return dict(substance_id=a['substance_id'],mp_kg_mol=t['mp_kg_mol'],dispersity=t['dispersity'],template_source=t['source']['url']+' # '+t['source']['locator'],evidence=t['evidence'])
  if k=='molecular_weight.analyze':return self._analyze()
  if k=='calibration.fit':
   if self.phase=='running' or not self.method or not a['accept_nominal_or_external_standard']:raise ActionRejected('PRECONDITION_FAILED')
   x=np.asarray([p['elution_volume_m3'] for p in a['points']]);y=np.log10([p['mp_kg_mol'] for p in a['points']]);order=np.argsort(x)
   if len(set(x))!=len(x) or any(np.diff(y[order])>=0):raise ActionRejected('INVALID_CALIBRATION')
   b=float(np.sum((x-x.mean())*(y-y.mean()))/np.sum((x-x.mean())**2));intercept=float(y.mean()-b*x.mean());err=float(np.sum((y-(intercept+b*x))**2));r2=max(0.,1-err/float(np.sum((y-y.mean())**2)))
   if b>=0 or r2<.95:raise ActionRejected('INVALID_CALIBRATION')
   self.calibration=dict(a=intercept,b=b,lo=float(min(x)),hi=float(max(x)),basis=a['basis']);return dict(intercept_log10_kg_mol=intercept,slope_per_m3=b,r_squared=r2)
  if k=='plant.configure':
   if self.plant or self.powered or not a['accept_synthetic_separation']:raise ActionRejected('INITIALIZATION_ONLY')
   if any(c['total_m3']<=c['void_m3'] for c in a['columns']):raise ActionRejected('COLUMN_RANGE')
   self.plant=copy.deepcopy(a);self.reservoir=a['reservoir_m3'];self.temperature=[a['ambient_k']]*2;return {'configured':True}
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self.phase='cancelled';self.pump=False;self.records[self.operation_id]=self._result();return {'phase':self.phase}
  if k=='power.set':
   self.powered=a['enabled']
   if not self.powered:
    self.pump=False
    if self.phase=='running':self.phase='cancelled';self.records[self.operation_id]=self._result()
   return a
  if self.phase=='running':raise ActionRejected('BUSY')
  if k=='method.configure':
   if not self.plant or self.pump:raise ActionRejected('PRECONDITION_FAILED')
   c=self.plant['columns'][a['column_index']];q=a['flow_m3_s'];ml=q*6e7;limit=(1300 if ml<=2 else 1300-(ml-2)*500/3)*1e5
   if q*c['resistance_pa_s_m3']>min(limit,c['max_pressure_pa']):raise ActionRejected('PRESSURE_LIMIT')
   if min(a['zone_targets_k'])<self.plant['ambient_k']-20 or a['solvent']!=c['solvent']:raise ActionRejected('COLUMN_RANGE')
   self.method=copy.deepcopy(a);self.calibration=None;return {'configured':True}
  if k=='pump.set':
   if a['enabled'] and (not self.powered or not self.method or any(self.motion.values())):raise ActionRejected('NOT_READY')
   self.pump=a['enabled'];return a
  for part,sid,arg,u,l in MOTION:
   if k==sid.rsplit('.',1)[0]+'.set':
    if self.pump:raise ActionRejected('BUSY')
    self.motion[sid]=a[arg];return a
  if k=='sample.load':
   if self.sample or not self.method or self.motion['drawer.position_m']<.15:raise ActionRejected('PREPARATION_REQUIRED')
   col=self.plant['columns'][self.method['column_index']]
   if a['solvent']!=col['solvent'] or len({v['substance_id'] for v in a['solutes']})!=len(a['solutes']):raise ActionRejected('SAMPLE_COMPATIBILITY')
   for s in a['solutes']:
    if s['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
    if self.templates[s['substance_id']]['polymer_family']!=col['polymer_family']:raise ActionRejected('SAMPLE_COMPATIBILITY')
   self.sample=copy.deepcopy(a);self.sample['remaining']=a['volume_m3'];self.sample_loaded+=a['volume_m3'];return {'loaded':True}
  if k=='sample.unload':
   if not self.sample or self.motion['drawer.position_m']<.15:raise ActionRejected('PREPARATION_REQUIRED')
   v=self.sample['remaining'];self.sample_returned+=v;self.sample=None;return {'returned_m3':v}
  if k=='acquisition.start':
   if not self.sample or not self.pump or not self.method or any(self.motion.values()) or max(abs(t-x) for t,x in zip(self.temperature,self.method['zone_targets_k']))>.5:raise ActionRejected('NOT_READY')
   vol=self.method['injection_m3']
   if self.sample['remaining']<vol or self._ledger()['column_kg']>1e-12:raise ActionRejected('SAMPLE_OR_CARRYOVER')
   if self.waste+vol>self.plant['waste_capacity_m3'] or self.reservoir<=0:raise ActionRejected('WASTE_OR_ELUENT')
   c=self.plant['columns'][self.method['column_index']];nodes,weights=np.polynomial.hermite.hermgauss(41);weights/=math.sqrt(math.pi)
   for s in self.sample['solutes']:
    t=self.templates[s['substance_id']];logs=math.log(t['mp_kg_mol'])+math.sqrt(2*math.log(t['dispersity']))*nodes
    for logm,w in zip(logs,weights):
     center=max(c['void_m3'],min(c['total_m3'],(float(logm)/math.log(10)-c['log10_intercept_kg_mol'])/c['log10_slope_per_m3']));self.bands.append(dict(mass=float(w)*s['concentration_kg_m3']*vol,eluted=0.,volume=0.,mean=center,sigma=c['band_sigma_m3']))
   self.sample['remaining']-=vol;self.waste+=vol;self.serial+=1;self.operation_id=f'{self.instance_id}:op:{self.serial}';self.phase='running';self.elapsed=0.;self.volume=0.;self.points=[];self.signal=None;self.saturated=False;return dict(phase='running',operation_id=self.operation_id)
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _analyze(self):
  result=dict(mn_kg_mol=None,mw_kg_mol=None,dispersity=None,mass_kg=None,quality='incomplete',basis=self.calibration['basis'] if self.calibration else 'uncalibrated',evidence='observed RID windows, caller dn/dc and conventional logM calibration; relative molecular masses')
  if self.phase!='succeeded':return result
  if not self.calibration:result['quality']='uncalibrated';return result
  if self.saturated:result['quality']='saturated';return result
  c=self.calibration;items=[];total_area=sum(max(0,p['delta_n'])*p['window_m3'] for p in self.points)
  if total_area<=0:result['quality']='no_signal';return result
  outside=0.
  for p in self.points:
   mass=max(0,p['delta_n'])*p['window_m3']/self.method['dn_dc_m3_kg'];v=p['volume_m3']-p['window_m3']/2
   if not c['lo']<=v<=c['hi']:outside+=mass;continue
   logm=c['a']+c['b']*v
   if not -10<logm<12:result['quality']='invalid_calibration';return result
   items.append((mass,10**logm))
  mass=sum(m for m,M in items)
  if outside/(total_area/self.method['dn_dc_m3_kg'])>.001 or mass<=0:result['quality']='outside_calibration';return result
  mn=mass/sum(m/M for m,M in items);mw=sum(m*M for m,M in items)/mass;result.update(mn_kg_mol=mn,mw_kg_mol=mw,dispersity=max(1.,mw/mn),mass_kg=mass,quality='estimated_relative');return result
 def step(self,dt_s):
  validate(number('s',0,1e5),dt_s)
  if dt_s==0:return self.read()
  dt=float(dt_s);self.time_s+=dt;self.revision+=1
  if self.plant:
   target=self.method['zone_targets_k'] if self.method and self.powered else [self.plant['ambient_k']]*2
   self.temperature=[x+(t-x)*math.exp(-dt/self.plant['thermal_tau_s']) for t,x in zip(self.temperature,target)]
  self.flow=0.;self.pressure=0.
  if self.powered and self.pump and self.method:
   duration=min(dt,self.method['run_s']-self.elapsed) if self.phase=='running' else dt;q=self.method['flow_m3_s'];v=max(0.,min(q*duration,self.reservoir,self.plant['waste_capacity_m3']-self.waste));self.flow=v/dt;self.pressure=self.flow*self.plant['columns'][self.method['column_index']]['resistance_pa_s_m3'];self.reservoir-=v;self.waste+=v;dm=0.
   for b in self.bands:
    b['volume']+=v;cdf=lambda x:.5*(1+math.erf((x-b['mean'])/(b['sigma']*math.sqrt(2))));f0=cdf(0.);f=max(0.,min(1.,(cdf(b['volume'])-f0)/(1-f0)));new=b['mass']*f;dm+=max(0.,new-b['eluted']);b['eluted']=new
   if self.phase=='running' and v>0:
    self.elapsed+=v/q;self.volume+=v;signal=self.method['dn_dc_m3_kg']*dm/v;sat=abs(signal)>.0006;self.saturated|=sat;self.signal=max(-.0006,min(.0006,signal));self.points.append(dict(time_s=self.elapsed,window_s=v/q,volume_m3=self.volume,window_m3=v,delta_n=self.signal,saturated=sat))
   if self.phase=='running' and self.elapsed>=self.method['run_s']-1e-8:self.phase='succeeded';self.pump=False;self.records[self.operation_id]=self._result()
   elif v<q*duration-1e-15:
    self.pump=False
    if self.phase=='running':self.phase='failed';self.records[self.operation_id]=self._result()
  return self.read()
class Agilent1290infinitySession:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,i):
  if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  m=export_agilent_1290infinity_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
 def read(self,*,instance_id,state_ids=None):
  self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
  if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
  if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
  return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='unavailable' if v[k] is None else 'estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=h.time_s,sample_window_s=[h.points[-1]['time_s']-h.points[-1]['window_s'],h.points[-1]['time_s']] if k=='detector.delta_n' and h.points else None) for k in ids})
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


def export_agilent_1290infinity_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():
  ref='泵出口相对环境表压；环境为0Pa基准' if u=='Pa' else '液体实际体积流量，不是气体标准体积' if u=='m^3/s' else None
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=ref,origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'Agilent1290infinitySession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  unsupported=k in UNSUPPORTED;asyn=k=='acquisition.start'
  caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.agilent_1290infinity.Agilent1290infinitySession.invoke',invocation=f'session.invoke(instance_id="sec_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if kind in ['query','analysis'] or unsupported else [dict(description_zh='类型/身份、方法限值、闭门、温度准备、有限体积及忙碌检查；不冒充原厂安全互锁',state_ids=['power.enabled','pump.enabled','method.configured','door.angle_rad','drawer.position_m','acquisition.phase'],enforced_by='runtime',check='atomic Runtime.invoke plus trusted-principal Session')],state_effects=[dict(state_id=x,timing='immediate',description_zh='受检事务目标/逻辑状态变化') for x in effects],observes=[],model_effects=dict(mode='none',description_zh='等待独立实际场景求值；数值过程不自动证明模型联动',binding_ids=[]),execution=dict(mode='asynchronous' if asyn else 'synchronous',time_domain='simulation',completion_criterion='采集实际时间达到run_s；残留柱物质仍记账' if asyn else '受检事务返回；read/describe/poll不推进时钟',completion_state_ids=['acquisition.phase'] if asyn else [],timeout_s=None,cancellation='supported' if asyn else 'unsupported',retry='same_request_id',resources=['host_clock','sample_fluid','eluent_reservoir','waste_container','request_cache'],partial_failure_zh='已进样/已流出物质保留；取消停止泵目标，不逆向回滚；去重仅当前宿主进程'),errors=[dict(code=x,meaning_zh=x,side_effects='none') for x in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','PRECONDITION_FAILED','INITIALIZATION_ONLY','BUSY','COLUMN_RANGE','SAMPLE_COMPATIBILITY','PRESSURE_LIMIT','INVALID_CALIBRATION','SAMPLE_METHOD_REQUIRED','PREPARATION_REQUIRED','DUPLICATE_SOLUTE','UNKNOWN_REFERENCE','COLUMN_POLARITY','NO_SAMPLE','NOT_READY','SAMPLE_OR_CARRYOVER','WASTE_FULL','NOT_RUNNING','INVALID_WINDOW']],example_arguments=copy.deepcopy(ex),verification=[],limitations=['教学过程；柱保留映射、dn/dc、分布、噪声与实机未校准'],source_ids=['manufacturer_selection','simulation_assumption'])
  if asyn:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='唯一由宿主step推进') for x in ['acquisition.elapsed_s','detector.delta_n','pump.flow_m3_s','pump.pressure_pa','acquisition.phase']]
 mech=dict(component_ids=['sample_tray','injection_valve','pump_drive','column_bay','rid_cell','waste_port'],state_ids=list(slots),meaning_zh='传统相对分子量GPC/SEC：有限液体、离散聚合物质量分布与显式校准',implementation='lumped',equations=['logM mass distribution: mu=ln(nominal Mp), sigma^2=ln(nominal Mw/Mn); 41-point Gauss-Hermite weights; ASSUMED distribution, not measured','V_elute=clip((log10(M_kg_mol)-a)/b, void_volume,total_volume), b<0; host separation coefficients synthetic','narrow band cumulative mass from positive-volume truncated Gaussian; dm_eluted=mass*(CDF_new-CDF_old)','RI_delta=(dn/dc)*sum(dm)/dV, limited to +/-600e-6; dn/dc supplied explicitly, not manufacturer measurement','Mn=sum(dm)/sum(dm/M); Mw=sum(dm*M)/sum(dm); D=Mw/Mn; mass from observed RI windows and user calibration, never hidden truth','Tnew=Tset+(Told-Tset)*exp(-dt/tau); two independent temperature zones; tau explicit assumption','Q limit5mL/min; pressure limit1300bar at<=2mL/min, linearly decreasing to800bar at5mL/min; lower column limit also enforced','Finite eluent volume and waste capacity; injection draws actual sample volume, displaces equal solvent volume to waste; polymer mass retained until eluted'],parameters={},source_ids=['manufacturer_selection','manufacturer_standards','simulation_assumption'],limitations=['73不同标准材料等级，3个聚合物族；不是50种不同聚合物结构或73条实验色谱。','模板是厂家名义Mp/分散系数，不是指定批次COA；分布采用logM高斯假设。','dn/dc、保留映射、峰扩散及阻力/时间常数均宿主显式假设；不实现所有溶剂、柱化学、非排阻吸附或非线性高浓度响应。','RI仅输出一个总浓度信号；无MALS/粘度检测器，绝对分子量能力unsupported。','运行时泵压限值不是整条流路每部件的真实耐压；柱额定压由宿主另给。','100微升进样配置；不包含1500微升多次抽吸、双针、多洗及6144样位全自动。','8个10cm柱位；绘制短柱，不将四支30cm柱的容量混淆。','有限模型守恒不是真实密封/高压脉冲/自动装卸；机器人与硬件未验证。','会话真值过滤不是文件/进程隔离。'])
 spec=importlib.util.spec_from_file_location('_sec_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'size_exclusion':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'。'
 slots['detector.delta_n']['sampling']=dict(mode='interval_mean',time_domain='simulation',window_description='最近真实进柱体积窗口对应的时间区间；停止后保持最近采集窗口')
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'size_exclusion':mech})

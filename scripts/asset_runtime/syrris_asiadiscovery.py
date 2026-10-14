"""Regular two-channel flow: conservative tracer transport, explicit dead volume and bounded vapor data."""
from __future__ import annotations
import copy,json,math
from asset_runtime import _annotations as _ann
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT,PHASE
from asset_runtime.antonpaar_abbematadvanced import validate
ROOT=Path(__file__).resolve().parents[2];ASSET_TYPE_ID='instrument.syrris_asiadiscovery';CATALOG_ID='CHEM-019';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/syrris_asiadiscovery_flow_library/templates.json';ULMIN=1e-9/60
SYRINGES={'yellow':(1,250),'green':(5,1250),'blue':(10,2500),'red':(50,10000)}
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
def arr(s,n=None):
 a={'type':'array','items':s}
 if n is not None:a.update(minItems=n,maxItems=n)
 return a
FLOW=number('m^3/s',ULMIN,10000*ULMIN);TEMP=number('K',258.15,423.15);PRESS=number('Pa',1e5,2e6);LIFT=number('m',0,.04);INDEX={'type':'integer','minimum':0,'maximum':7,'x-unit':'1'}
PLANT=obj({'substance_id':TEXT,'fit_id':TEXT,'chip_volume_m3':{'type':'number','enum':[250e-9,1e-6],'x-unit':'m^3'},'density_kg_m3':number('kg/m^3',1,5000),'initial_temperature_k':TEMP,'pure_liquid_confirmed':BOOL,'pre_equilibrated':BOOL,'low_temperature_available':BOOL,'reservoir_volumes_m3':arr(number('m^3',1e-7,250e-6),2),'loop_volumes_m3':arr({'type':'number','enum':[.1e-6,1e-6,5e-6,10e-6],'x-unit':'m^3'},2),'loop_concentrations_mol_m3':arr(number('mol/m^3',0,100),2),'dead_volumes_m3':arr(number('m^3',1e-9,10e-6),3),'branch_resistance_pa_s_m3':arr(number('Pa*s/m^3',0,1e15),2),'common_resistance_pa_s_m3':number('Pa*s/m^3',0,1e15),'thermal_tau_s':number('s',.01,10000),'gas_supply_pa':number('Pa',2e5,2.5e6),'syringes':arr({'type':'string','enum':list(SYRINGES)},2),'container_ids':arr(TEXT,2),'chip_id':TEXT})
EXAMPLE=dict(substance_id='methanol',fit_id='1',chip_volume_m3=250e-9,density_kg_m3=790.,initial_temperature_k=298.15,pure_liquid_confirmed=True,pre_equilibrated=True,low_temperature_available=False,reservoir_volumes_m3=[20e-6]*2,loop_volumes_m3=[1e-6]*2,loop_concentrations_mol_m3=[1.,2.],dead_volumes_m3=[1e-8]*3,branch_resistance_pa_s_m3=[1e11]*2,common_resistance_pa_s_m3=1e11,thermal_tau_s=10.,gas_supply_pa=2.5e6,syringes=['red']*2,container_ids=['feed_A','feed_B'],chip_id='glass250')
WINDOW=obj({'start_s':number('s',0,86400),'end_s':number('s',0,86400),'slot':INDEX})
METHOD=obj({'flow_rates_m3_s':arr(FLOW,2),'duration_s':number('s',.01,86400),'injection_times_s':arr(number('s',0,86400),2),'collection_windows':arr(WINDOW)})
METHOD_EXAMPLE=dict(flow_rates_m3_s=[5000*ULMIN]*2,duration_s=4.,injection_times_s=[0.,.4],collection_windows=[dict(start_s=.5,end_s=3.,slot=0)])
POINT=obj({'time_s':number('s',0),'elapsed_s':number('s',0),'temperature_k':nullable(TEMP),'pressure_pa':arr(number('Pa',0),2),'collected_m3':number('m^3',0),'phase':PHASE})
RESULT=obj({'phase':PHASE,'fault':TEXT,'elapsed_s':number('s',0),'temperature_k':nullable(TEMP),'pump_pressures_pa':arr(number('Pa',0),2),'vapor_pressure_pa':nullable(number('Pa',0)),'vapor_margin_pa':nullable(number('Pa')),'collected_volume_m3':number('m^3',0),'waste_volume_m3':number('m^3',0),'conversion_fraction':{'type':'null'},'yield_fraction':{'type':'null'},'template_source':TEXT,'evidence':TEXT})
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet'],'供电；断电停止输送，保留残液','device_command','agent',{'enabled':True},['power.enabled','cycle.phase']),
 'clamp.set_lift':(obj({'lift_m':LIFT}),obj({'lift_m':LIFT}),['chip_clamp'],'芯片夹具教学升降；输送中禁止打开','device_command','agent',{'lift_m':.04},['clamp.lift_m']),
 'plant.configure':(PLANT,obj({'configured':BOOL,'attachment_events':arr(TEXT)}),['chip','input_store','injector'],'宿主一次性装配/预平衡/预充；同载液双被动示踪，显式参数','configuration','evaluator',EXAMPLE,['plant.configured','reactor.volume_m3','truth.initial_volume_m3','truth.initial_tracer_mol']),
 'method.configure':(METHOD,obj({'configured':BOOL}),['pump','injector','collector'],'两路流率、注入顺序与无重叠收集窗口；保留前次残液','configuration','agent',METHOD_EXAMPLE,['flow.targets_m3_s','method.duration_s']),
 'temperature.set':(obj({'target_k':TEMP}),obj({'target_k':TEMP}),['climate'],'Regular−15–150C；模板有效温段内且低温能力已确认','device_command','agent',{'target_k':303.15},['temperature.target_k']),
 'pressure.set':(obj({'backpressure_pa':PRESS}),obj({'backpressure_pa':PRESS}),['pressure','knob_backpressure'],'BPR1–20bar，表压；受供气约束；上游压降另计','device_command','agent',{'backpressure_pa':2e5},['pressure.backpressure_pa']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['pump','injector','collector'],'供电夹紧、配置完整且压力/汽化检查通过后开始连续输送','device_command','agent',{},['cycle.phase','cycle.elapsed_s','cycle.fault','flow.actual_m3_s']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['pump','injector'],'取消后保留所有流路/环内残液；采样钟仅step推进','device_command','agent',{},['cycle.phase','flow.actual_m3_s']),
 'flow.measure':(obj({}),RESULT,['display_digits'],'读取两路压力、芯片温度代理和收集体积，不报告产率','query','agent',{},[]),
 'history.read':(obj({}),obj({'records':arr(POINT),'truncated':BOOL,'template_source':TEXT,'evidence':TEXT}),['display_digits'],'最多1000条1s采样记录；不是实测Asia曲线','query','agent',{},[]),
 'product.read':(obj({}),obj({'volumes_m3':arr(number('m^3',0),8),'tracer_mol':arr(arr(number('mol',0),2),8),'reaction_products':{'type':'null'},'evidence':TEXT}),['collector'],'宿主读取收集的被动示踪物库存；化学产物未知','query','evaluator',{},[]),
 'collector.unload':(obj({'slot':INDEX,'container_id':TEXT}),obj({'unloaded':BOOL,'attachment_event':TEXT}),['collector'],'停泵移走收集瓶及库存；仅宿主事件，不创建约束','configuration','evaluator',{'slot':0,'container_id':'fraction_0'},['truth.removed_volume_m3','truth.removed_tracer_mol']),
 'residence.measure':(obj({}),obj({'mean_s':number('s',0),'variance_s2':number('s^2',0),'chip_mean_s':number('s',0),'evidence':TEXT}),['chip'],'理想线性串联槽网络RTD解析矩；包含进出口死体积','query','agent',{},[]),
}
SLOTS={}
def slot(k,s,u,o='derived',v='agent',c='display_digits',w=None):SLOTS[k]=(s,u,o,v,[c],w)
slot('power.enabled',BOOL,'1','setpoint',c='power_inlet',w=('power.set','enabled'));slot('clamp.lift_m',LIFT,'m','scene',c='chip_clamp',w=('clamp.set_lift','lift_m'))
slot('temperature.target_k',TEMP,'K','setpoint',c='climate',w=('temperature.set','target_k'));slot('pressure.backpressure_pa',PRESS,'Pa','setpoint',c='pressure',w=('pressure.set','backpressure_pa'))
for k,s,u in [('cycle.phase',PHASE,'1'),('cycle.elapsed_s',number('s',0),'s'),('cycle.fault',TEXT,'1'),('plant.configured',BOOL,'1'),('reactor.volume_m3',nullable(number('m^3',0)),'m^3'),('method.duration_s',number('s',0),'s'),('flow.targets_m3_s',arr(number('m^3/s',0),2),'m^3/s'),('flow.actual_m3_s',arr(number('m^3/s',0),2),'m^3/s'),('temperature.indicated_k',nullable(TEMP),'K'),('pressure.indicated_pa',arr(number('Pa',0),2),'Pa'),('pressure.vapor_pa',nullable(number('Pa',0)),'Pa'),('pressure.vapor_margin_pa',nullable(number('Pa')),'Pa'),('collection.total_m3',number('m^3',0),'m^3'),('collection.waste_m3',number('m^3',0),'m^3'),('collector.x_m',number('m',0,.30),'m'),('collector.y_m',number('m',0,.10),'m'),('injection.valves',arr(BOOL,2),'1')]:slot(k,s,u)
for k in ['initial','retained','collected','waste','removed']:slot('truth.'+k+'_volume_m3',number('m^3',0),'m^3','process_truth','evaluator','chip');slot('truth.'+k+'_mass_kg',number('kg',0),'kg','process_truth','evaluator','chip');slot('truth.'+k+'_tracer_mol',arr(number('mol',0),2),'mol','process_truth','evaluator','chip')
slot('truth.cell_tracer_mol',arr(arr(number('mol',0),2),7),'mol','process_truth','evaluator','chip');slot('truth.loop_remaining_m3',arr(number('m^3',0),2),'m^3','process_truth','evaluator','injector')
class SyrrisAsiadiscoveryRuntime:
 def __init__(self,instance_id='asiadiscovery_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.lift=0.;self.plant=None;self.method=copy.deepcopy(METHOD_EXAMPLE);self.target_k=298.15;self.temp_k=298.15;self.bpr=2e5;self.phase='idle';self.elapsed_s=0.;self.fault='';self.operation_id=None;self._operation_count=0;self.history=[];self.history_truncated=False;self.next_record_s=0.;self.collector_xy=[0.,0.];self.valves=[False]*2;self.initial_volume=0.;self.initial_tracer=[0.,0.];self.reservoirs=[0.,0.];self.loops=[0.,0.];self.cells=[[0.,0.] for _ in range(7)];self.collected=[0.]*8;self.collected_tracer=[[0.,0.] for _ in range(8)];self.waste=0.;self.waste_tracer=[0.,0.];self.removed=0.;self.removed_tracer=[0.,0.];self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return SyrrisAsiadiscoverySession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def vapor(self,t):
  if not self.plant:return None
  f=self.fit;lo,hi=f['runtime_temperature_range_k']
  if not lo<=t<=hi:raise ActionRejected('TEMPLATE_RANGE_UNAVAILABLE')
  return 1e5*10**(f['A']-f['B_k']/(t+f['C_k']))
 def pressures(self):
  if not self.plant or self.phase!='running':return [0.,0.]
  q=self.method['flow_rates_m3_s'];p=self.plant;return [self.bpr+p['common_resistance_pa_s_m3']*sum(q)+p['branch_resistance_pa_s_m3'][i]*q[i] for i in range(2)]
 def _guard(self):
  p=self.plant;q=self.method['flow_rates_m3_s']
  if max(self.bpr+p['common_resistance_pa_s_m3']*sum(q)+p['branch_resistance_pa_s_m3'][i]*q[i] for i in range(2))>2e6:return 'overpressure'
  if self.bpr+101325<=max(self.vapor(self.temp_k),self.vapor(self.target_k)):return 'vapor_risk'
  return ''
 def _inventory(self):
  holdup=sum(self.volumes)+sum(self.plant['loop_volumes_m3']) if self.plant else 0.;retained=sum(self.reservoirs)+holdup
  tracer=[sum(c[i] for c in self.cells)+(self.loops[i]*self.plant['loop_concentrations_mol_m3'][i] if self.plant else 0.) for i in range(2)]
  return dict(initial=(self.initial_volume,self.initial_tracer),retained=(retained,tracer),collected=(sum(self.collected),[sum(c[i] for c in self.collected_tracer) for i in range(2)]),waste=(self.waste,self.waste_tracer),removed=(self.removed,self.removed_tracer))
 def _values(self):
  ps=self.vapor(self.temp_k);q=self.method['flow_rates_m3_s'];v={'power.enabled':self.powered,'clamp.lift_m':self.lift,'temperature.target_k':self.target_k,'pressure.backpressure_pa':self.bpr,'cycle.phase':self.phase,'cycle.elapsed_s':self.elapsed_s,'cycle.fault':self.fault,'plant.configured':self.plant is not None,'reactor.volume_m3':self.plant['chip_volume_m3'] if self.plant else None,'method.duration_s':self.method['duration_s'],'flow.targets_m3_s':q,'flow.actual_m3_s':q if self.phase=='running' else [0.,0.],'temperature.indicated_k':round(self.temp_k-273.15,1)+273.15 if self.powered and self.plant else None,'pressure.indicated_pa':[round(x/1e4)*1e4 for x in self.pressures()],'pressure.vapor_pa':ps,'pressure.vapor_margin_pa':self.bpr+101325-ps if ps is not None else None,'collection.total_m3':sum(self.collected),'collection.waste_m3':self.waste,'collector.x_m':self.collector_xy[0],'collector.y_m':self.collector_xy[1],'injection.valves':self.valves,'truth.cell_tracer_mol':self.cells,'truth.loop_remaining_m3':self.loops}
  for name,(vol,mol) in self._inventory().items():v['truth.'+name+'_volume_m3']=vol;v['truth.'+name+'_mass_kg']=vol*(self.plant['density_kg_m3'] if self.plant else 0.);v['truth.'+name+'_tracer_mol']=mol
  return v
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('debug','evaluator')})
 def debug_snapshot(self):return dict(state=self.read('evaluator'),history=copy.deepcopy(self.history),plant=copy.deepcopy(self.plant))
 def _measure(self):
  v=self._values();return dict(phase=self.phase,fault=self.fault,elapsed_s=self.elapsed_s,temperature_k=v['temperature.indicated_k'],pump_pressures_pa=v['pressure.indicated_pa'],vapor_pressure_pa=v['pressure.vapor_pa'],vapor_margin_pa=v['pressure.vapor_margin_pa'],collected_volume_m3=sum(self.collected),waste_volume_m3=self.waste,conversion_fraction=None,yield_fraction=None,template_source=TEMPLATE_REL,evidence='synthetic_transport_temperature_pressure; NIST_literature_derived_pure_fluid_vapor_pressure')
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if 'slot' in a and (type(a['slot']) is not int or not 0<=a['slot']<8):raise ActionRejected('INVALID_ARGUMENT')
  if self.phase=='running' and capability not in ('power.set','acquisition.stop','flow.measure','history.read','product.read','residence.measure'):raise ActionRejected('BUSY')
  if capability=='power.set':
   self.powered=a['enabled']
   if not self.powered and self.phase=='running':self.phase='cancelled';self.valves=[False]*2
   out=dict(a)
  elif capability=='clamp.set_lift':self.lift=a['lift_m'];out=dict(a)
  elif capability=='plant.configure':
   t=self.templates.get(a['substance_id']);f=next((f for f in t['fits'] if f['fit_id']==a['fit_id']),None) if t else None
   if self.plant is not None or self.lift<.039 or not a['pure_liquid_confirmed'] or not a['pre_equilibrated'] or len(set(a['container_ids']+[a['chip_id']]))!=3 or not f:raise ActionRejected('PRECONDITION_FAILED')
   if not f['runtime_temperature_range_k'][0]<=a['initial_temperature_k']<=f['runtime_temperature_range_k'][1] or (a['initial_temperature_k']<293.15 and not a['low_temperature_available']):raise ActionRejected('TEMPLATE_RANGE_UNAVAILABLE')
   for i,s in enumerate(a['syringes']):
    lo,hi=SYRINGES[s]
    if not lo*ULMIN<=self.method['flow_rates_m3_s'][i]<=hi*ULMIN:raise ActionRejected('INVALID_ARGUMENT')
   if self.bpr>a['gas_supply_pa']:raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);self.fit=f;self.temp_k=a['initial_temperature_k'];self.target_k=self.temp_k;self.reservoirs=a['reservoir_volumes_m3'][:];self.loops=a['loop_volumes_m3'][:];self.volumes=a['dead_volumes_m3'][:2]+[a['chip_volume_m3']/4]*4+[a['dead_volumes_m3'][2]];self.initial_volume=sum(self.reservoirs)+sum(self.loops)+sum(self.volumes);self.initial_tracer=[self.loops[i]*a['loop_concentrations_mol_m3'][i] for i in range(2)];out=dict(configured=True,attachment_events=['host_attach:'+x for x in a['container_ids']+[a['chip_id']]])
  elif capability=='method.configure':
   windows=a['collection_windows'];prev=0.
   for w in windows:
    if type(w['slot']) is not int or not 0<=w['slot']<8 or not prev<=w['start_s']<w['end_s']<=a['duration_s']:raise ActionRejected('INVALID_ARGUMENT')
    prev=w['end_s']
   if any(t>a['duration_s'] for t in a['injection_times_s']):raise ActionRejected('INVALID_ARGUMENT')
   if self.plant:
    for i,s in enumerate(self.plant['syringes']):
     lo,hi=SYRINGES[s]
     if not lo*ULMIN<=a['flow_rates_m3_s'][i]<=hi*ULMIN:raise ActionRejected('INVALID_ARGUMENT')
   self.method=copy.deepcopy(a);out={'configured':True}
  elif capability=='temperature.set':
   if not self.plant or (a['target_k']<293.15 and not self.plant['low_temperature_available']):raise ActionRejected('PRECONDITION_FAILED')
   self.vapor(a['target_k']);self.target_k=a['target_k'];out=dict(a)
  elif capability=='pressure.set':
   if self.plant and a['backpressure_pa']>self.plant['gas_supply_pa']:raise ActionRejected('PRECONDITION_FAILED')
   self.bpr=a['backpressure_pa'];out=dict(a)
  elif capability=='acquisition.start':
   if not self.powered or self.lift>1e-5 or not self.plant or min(self.reservoirs)<=1e-15:raise ActionRejected('PRECONDITION_FAILED')
   fault=self._guard()
   if fault:raise ActionRejected('PRECONDITION_FAILED')
   self._operation_count+=1;self.operation_id=f'{self.instance_id}:flow:{self._operation_count}';self.phase='running';self.fault='';self.elapsed_s=0.;self.history=[];self.history_truncated=False;self.next_record_s=self.time_s+1.;out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase=='running':self.phase='cancelled';self.valves=[False]*2
   out={'phase':self.phase}
  elif capability=='collector.unload':
   i=a['slot']
   if a['container_id']!='fraction_'+str(i) or self.collected[i]<=0:raise ActionRejected('PRECONDITION_FAILED')
   self.removed+=self.collected[i];self.collected[i]=0.
   for j in range(2):self.removed_tracer[j]+=self.collected_tracer[i][j];self.collected_tracer[i][j]=0.
   out=dict(unloaded=True,attachment_event='host_release:'+a['container_id'])
  elif capability=='history.read':return dict(records=copy.deepcopy(self.history),truncated=self.history_truncated,template_source=TEMPLATE_REL,evidence='synthetic_trace_not_measured_Asia_output')
  elif capability=='product.read':return dict(volumes_m3=self.collected[:],tracer_mol=copy.deepcopy(self.collected_tracer),reaction_products=None,evidence='passive_tracer_inventory; no_chemical_yield')
  elif capability=='residence.measure':
   if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
   q=self.method['flow_rates_m3_s'];total=sum(q);taus=[self.volumes[i]/q[i] for i in range(2)];mean_in=sum(taus[i]*q[i]/total for i in range(2));var_in=sum(q[i]/total*(taus[i]**2+(taus[i]-mean_in)**2) for i in range(2));chip=self.plant['chip_volume_m3']/total;outlet=self.volumes[-1]/total
   return dict(mean_s=mean_in+chip+outlet,variance_s2=var_in+chip**2/4+outlet**2,chip_mean_s=chip,evidence='synthetic_linear_CSTR_network_analytic_moments; injection_loop_pulse_not_included')
  else:return self._measure()
  self.revision+=1;return copy.deepcopy(out)
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<=0:raise ValueError('finite positive dt required')
  left=float(dt_s)
  while left>1e-10:
   h=min(.02,left);active=self.phase=='running';p=self.plant;q=self.method['flow_rates_m3_s'];total=sum(q)
   if active:
    h=min(h,self.method['duration_s']-self.elapsed_s,*[.2*self.volumes[i]/(q[i] if i<2 else total) for i in range(7)])
    events=self.method['injection_times_s']+[x[k] for x in self.method['collection_windows'] for k in ['start_s','end_s']]
    for event in events:
     if event>self.elapsed_s+1e-9:h=min(h,event-self.elapsed_s)
    dest=next((x['slot'] for x in self.method['collection_windows'] if x['start_s']-1e-9<=self.elapsed_s<x['end_s']-1e-9),None)
    fault=self._guard()
    if min(self.reservoirs[i]-q[i]*h for i in range(2))< -1e-15:fault='reservoir_empty'
    if dest is not None and self.collected[dest]+total*h>5e-6+1e-15:fault='collector_full'
    if fault:self.phase='failed';self.fault=fault;self.valves=[False]*2;active=False
    else:
     old=copy.deepcopy(self.cells);outflows=[[old[i][j]/self.volumes[i]*(q[i] if i<2 else total)*h for j in range(2)] for i in range(7)]
     incoming=[[0.,0.] for _ in range(7)]
     for i in range(2):
      self.valves[i]=self.elapsed_s>=self.method['injection_times_s'][i]-1e-9 and self.loops[i]>1e-15
      dv=min(self.loops[i],q[i]*h) if self.valves[i] else 0.;incoming[i][i]=dv*p['loop_concentrations_mol_m3'][i];self.loops[i]-=dv;self.reservoirs[i]=max(0.,self.reservoirs[i]-q[i]*h)
     incoming[2]=[outflows[0][j]+outflows[1][j] for j in range(2)]
     for i in range(3,7):incoming[i]=outflows[i-1]
     self.cells=[[max(0.,old[i][j]+incoming[i][j]-outflows[i][j]) for j in range(2)] for i in range(7)]
     if dest is None:self.waste+=total*h;self.waste_tracer=[self.waste_tracer[j]+outflows[6][j] for j in range(2)]
     else:
      self.collector_xy=[(dest%4)*.1,(dest//4)*.1];self.collected[dest]+=total*h
      self.collected_tracer[dest]=[self.collected_tracer[dest][j]+outflows[6][j] for j in range(2)]
     self.elapsed_s+=h
     if self.elapsed_s>=self.method['duration_s']-1e-9:self.phase='succeeded';self.valves=[False]*2
   if self.powered and p:self.temp_k=self.target_k+(self.temp_k-self.target_k)*math.exp(-h/p['thermal_tau_s'])
   self.time_s+=h;left-=h
   if self.operation_id and (self.time_s>=self.next_record_s-1e-9 or (active and self.phase!='running')):
    v=self._values();self.history.append(dict(time_s=self.time_s,elapsed_s=self.elapsed_s,temperature_k=v['temperature.indicated_k'],pressure_pa=v['pressure.indicated_pa'],collected_m3=sum(self.collected),phase=self.phase));self.next_record_s=self.time_s+1.
    if len(self.history)>1000:self.history.pop(0);self.history_truncated=True
  self.revision+=1;return self.read('evaluator')
class SyrrisAsiadiscoverySession:
    """Host registers principals; public calls have no visibility override."""
    def __init__(self,host,principal_id): self._host=host; self._principal=principal_id
    @property
    def _role(self): return self._host._principals[self._principal]
    def _target(self,i):
        if i!=self._host.instance_id: raise ActionRejected('UNKNOWN_INSTANCE')

    def describe(self,*,instance_id=None,asset_type_id=None):
        if (instance_id is None)==(asset_type_id is None): raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None: self._target(instance_id)
        if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID: raise ActionRejected('UNKNOWN_INSTANCE')
        meta=export_syrris_asiadiscovery_contract_metadata()
        return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',
          capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},
          state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})

    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id); h=self._host; vals=h.read(self._role); ids=list(vals) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids): raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in vals for k in ids): raise ActionRejected('ACCESS_DENIED')
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},
          observations={k:dict(value=copy.deepcopy(vals[k]),unit=SLOTS[k][1],quality='unavailable' if vals[k] is None else ('estimated' if SLOTS[k][2]=='derived' else 'valid'),sample_time_s=h.time_s,sample_window_s=None) for k in ids})

    def invoke(self,*,instance_id,capability_id,arguments,request_id):
        try:
            return self._invoke(instance_id=instance_id,capability_id=capability_id,arguments=arguments,request_id=request_id)
        except ActionRejected as exc:
            return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,
                status='rejected',state_revision=self._host.revision,clock={'domain':'simulation','time_s':self._host.time_s},
                result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))

    def _invoke(self,*,instance_id,capability_id,arguments,request_id):
        self._target(instance_id); h=self._host
        if not isinstance(capability_id,str) or capability_id not in CAPS: raise ActionRejected('UNKNOWN_CAPABILITY')
        if self._role=='agent' and CAPS[capability_id][5]!='agent': raise ActionRejected('ACCESS_DENIED')
        if not isinstance(request_id,str) or not request_id: raise ActionRejected('INVALID_ARGUMENT')
        validate(CAPS[capability_id][0],arguments)
        key=(self._principal,instance_id,request_id); digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
        if key in h._requests:
            old,result=h._requests[key]
            if old!=digest: raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(result)
        result=h.invoke(capability_id,**arguments); op=result.get('operation_id')
        if op: h._owners[op]=self._principal
        response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=op,
          status='accepted' if op else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None)
        h._requests[key]=(digest,copy.deepcopy(response)); return response

    def write(self,*,instance_id,state_id,value,request_id):
        self._target(instance_id)
        if state_id not in SLOTS or not SLOTS[state_id][5]: raise ActionRejected('ACCESS_DENIED')
        cap,arg=SLOTS[state_id][5]
        return self.invoke(instance_id=instance_id,capability_id=cap,arguments={arg:value},request_id=request_id)

    def poll(self,operation_id):
        h=self._host
        if operation_id!=h.operation_id or h._owners.get(operation_id)!=self._principal: raise ActionRejected('ACCESS_DENIED')
        return dict(operation_id=operation_id,status=h.phase,result=h._measure())

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])

def export_syrris_asiadiscovery_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'SyrrisAsiadiscoverySession.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key=='acquisition.start'; pre=[]
        conditions={
         'acquisition.start':('供电夹紧、已预充且泵压力/汽化边界允许',['power.enabled','clamp.lift_m','plant.configured'],'powered and clamped and configured and hydraulic_guard'),
         'plant.configure':('空系统开夹，宿主确认液态/预平衡，源温段支持',['plant.configured','clamp.lift_m'],'not configured and open and valid_pure_fluid'),
         'clamp.set_lift':('停泵',['cycle.phase'],'phase!=running'),
         'method.configure':('停泵、注射器流量与收集窗口有效',['cycle.phase'],'not running and syringe_ranges and ordered_nonoverlapping_windows'),
         'collector.unload':('停泵，有收集液且容器身份匹配',['cycle.phase','collection.total_m3'],'not running and loaded_slot and expected_container')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.syrris_asiadiscovery.SyrrisAsiadiscoverySession.invoke',invocation=f'session.invoke(instance_id="asiadiscovery_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；达到输送时长；取消保留残液和示踪库存' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电不回滚已经输送/收集的液体与示踪库存。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    evolving=[k for k in slots if k.startswith('truth.')]+['cycle.elapsed_s','cycle.phase','cycle.fault','temperature.indicated_k','pressure.indicated_pa','pressure.vapor_pa','pressure.vapor_margin_pa','flow.actual_m3_s','collector.x_m','collector.y_m','collection.total_m3','collection.waste_m3','injection.valves']
    caps['acquisition.start']['state_effects'] += [dict(state_id=k,timing='on_step',description_zh='step推进管网输送/热响应/窗口分流，读取不推进') for k in evolving if k not in [e['state_id'] for e in caps['acquisition.start']['state_effects']]]
    for key in ['plant.configure','collector.unload']:
        caps[key]['state_effects'] += [dict(state_id=k,timing='immediate',description_zh='宿主装入/移除引起库存变化') for k in evolving if k.startswith('truth.') and k not in [e['state_id'] for e in caps[key]['state_effects']]]
    caps['temperature.set']['state_effects'].append(dict(state_id='temperature.indicated_k',timing='on_step',description_zh='一阶未标定设定响应，非真实热量账本'))
    mechanism=dict(component_ids=['pump','injector','chip','climate','pressure','collector'],state_ids=evolving,meaning_zh='同纯载液两种被动示踪物的守恒有限体积网络，真实蒸气压仅限文献温段',implementation='lumped',equations=['Psat=1e5*10**(A-B/(T+C)); valid single NIST fit only','Ppump_i=BPR+Rcommon*(q0+q1)+Rbranch_i*qi <=2e6 Pa gauge','single_phase_guard: 101325+BPR > max(Psat(T),Psat(Ttarget))','two inlet CSTRs -> four equal chip CSTRs -> outlet CSTR; explicit conservative transfers','dMcell_i=Min-Mout; Mout=C_old*Q*dt; dt<=0.2*V/Q','sample loops ideal plug displacement; equal carrier refill, constant loop volume','initial=retained+collected+waste+removed for volume, constant-density mass and each tracer','tau_chip=Vchip/Qtotal; variance_chip=tau_chip**2/4; branch residence mixture plus outlet','T=Ttarget+(Told-Ttarget)*exp(-dt/tau_thermal), no calibrated energy model'],parameters={},source_ids=['manufacturer_primary','flow_templates','simulation_assumption'],limitations=['Host resistances/dead volumes/density/thermal tau explicit assumptions; no calibrated hydraulics or RTD.','Same pure carrier and passive dilute tracers; no true chemistry, product yield, mixing-solvent activity or multiphase simulation.','Pre-primed/pre-equilibrated liquid confirmed by host; no startup filling/purge or complete drainage.','8x5mL collector subset; instant virtual XY motion and static visible vial proxies.'])
    _ann.annotate_export('syrris_asiadiscovery',caps,slots,{'flow_transport':mechanism},inputs={'plant.configure':(PLANT,EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'flow_transport':mechanism})

"""CPC250: conservative finite plate model; measured Kow, uncalibrated hydraulics/retention."""
from __future__ import annotations
import copy,json,math
from asset_runtime import _annotations as _ann
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.antonpaar_abbematadvanced import validate
ROOT=Path(__file__).resolve().parents[2];ASSET_TYPE_ID='instrument.gilson_cpc250';CATALOG_ID='CHEM-029';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/gilson_cpc250_partition_library/templates.json';CV=250e-6;N=24;WMAX=3000*math.pi/30;WREF=1600*math.pi/30;BPR=20*6894.757293168
PHASE={'type':'string','enum':['idle','running','succeeded','cancelled','failed']}
def arr(x):return {'type':'array','items':x}
def nullable(x):return {'anyOf':[x,{'type':'null'}]}
PLANT=obj({'reservoir_m3':number('m^3',1e-6,.01),'reference_stationary_fraction':number('1',.2,.8),'transfer_rate_s_inv':number('1/s',.001,10),'hydraulic_resistance_pa_s_m3':number('Pa*s/m^3',1e10,1e14),'pressure_tau_s':number('s',.1,30),'angular_ramp_rad_s2':number('rad/s^2',.1,100),'pre_equilibrated_confirmed':BOOL,'dilute_stable_neutral_reviewed':BOOL,'coefficient_evidence':TEXT})
EXAMPLE=dict(reservoir_m3=.002,reference_stationary_fraction=.65,transfer_rate_s_inv=1.,hydraulic_resistance_pa_s_m3=2e12,pressure_tau_s=2.,angular_ramp_rad_s2=20.,pre_equilibrated_confirmed=True,dilute_stable_neutral_reviewed=True,coefficient_evidence='synthetic_unvalidated_retention_transfer_hydraulic_coefficients')
METHOD=obj({'speed_rad_s':number('rad/s',100*math.pi/30,WMAX),'direction':{'type':'string','enum':['ASC','DSC']},'phase_system':{'type':'string','enum':['water_1octanol']},'flow_m3_s':number('m^3/s',1e-6/60,15e-6/60),'duration_s':number('s',1,20000),'collection_start_s':number('s',0,20000),'collection_end_s':number('s',0,20000),'fraction_volume_m3':number('m^3',1e-6,30e-6),'exceptional_speed_reviewed':BOOL})
METHOD_EXAMPLE=dict(speed_rad_s=WREF,direction='DSC',phase_system='water_1octanol',flow_m3_s=15e-6/60,duration_s=2400.,collection_start_s=0.,collection_end_s=2400.,fraction_volume_m3=30e-6,exceptional_speed_reviewed=False)
SOLUTE=obj({'substance_id':TEXT,'mass_kg':number('kg',1e-12,.006)})
SAMPLE=obj({'sample_id':TEXT,'volume_m3':number('m^3',1e-9,10e-6),'solutes':arr(SOLUTE),'filtered_10um_or_finer':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='reference_mixture',volume_m3=5e-6,solutes=[dict(substance_id='cas_57-55-6',mass_kg=1e-5),dict(substance_id='cas_67-64-1',mass_kg=1e-5)],filtered_10um_or_finer=True)
RESULT=obj({'phase':PHASE,'stage':TEXT,'fault':TEXT,'speed_rad_s':number('rad/s',0,WMAX),'pressure_gauge_pa':number('Pa',0),'concentration_kg_m3':number('kg/m^3',0),'stationary_fraction':number('1',0,1),'evidence':TEXT,'template_source':TEXT})
POINT=obj({'time_s':number('s',0),'elution_time_s':number('s',0),'pressure_gauge_pa':number('Pa',0),'total_concentration_kg_m3':number('kg/m^3',0)})
FRACTION=obj({'tube_index':number('1',0,191),'volume_m3':number('m^3',0,30e-6),'solute_masses_kg':arr(number('kg',0))})
PRODUCT=obj({'substance_ids':arr(TEXT),'fractions':arr(FRACTION),'waste_solute_masses_kg':arr(number('kg',0)),'measured_cpc250_recoveries':{'type':'null'},'evidence':TEXT,'template_source':TEXT})
REFERENCE=obj({'substance_id':TEXT,'log10_kow':number('1',-1,1.6),'stationary_mobile_partition':number('1',0),'temperature_k':{'type':'null'},'ph':{'type':'null'},'source':TEXT,'evidence':TEXT,'template_source':TEXT})
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['cpc_ac','plc_ac'],'电源，开机30s；正常关机需转子停下','device_command','agent',{'enabled':True},['power.enabled']),
 'plant.configure':(PLANT,obj({'configured':BOOL}),['rotor','pump'],'宿主已清洗预平衡与有限溶剂、工程系数','configuration','evaluator',EXAMPLE,['plant.configured']),
 'method.configure':(METHOD,obj({'configured':BOOL}),['backflush_valve','quaternary_valve','rotor'],'转速/ASC或DSC/两相/流率/收集窗口；仅启动前','configuration','agent',METHOD_EXAMPLE,['method.configured']),
 'sample.configure':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['sample_port','sample_loop'],'10mL环，最高6g应用相关；样品预溶于所选流动相','configuration','evaluator',SAMPLE_EXAMPLE,['sample.configured']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['rotor','pump','injection_valve','collection_valve'],'启动或取消后续作；启动应答不是分离完成','device_command','agent',{},['cycle.phase','cycle.stage']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['rotor','pump'],'停止泵，转子减速，保留已分配与已收集物料','device_command','agent',{},['cycle.phase','cycle.stage']),
 'cpc.measure':(obj({}),RESULT,['display_digits','dad'],'理想总浓度与压力；不是DAD真实吸光度','query','agent',{},[]),
 'history.read':(obj({}),obj({'records':arr(POINT),'truncated':BOOL,'evidence':TEXT}),['display_digits'],'每10秒压力/浓度过程记录','query','agent',{},[]),
 'product.read':(obj({}),PRODUCT,['collection_probe'],'各馏分条件组分质量；宿主真值','query','evaluator',{},[]),
 'reference.lookup':(obj({'substance_id':TEXT}),REFERENCE,['display_digits'],'实验Kow按流向换算固定/流动相比，不转换到任意溶剂','query','agent',{'substance_id':'cas_57-55-6'},[])}
SLOTS={}
def slot(k,s,u,o='derived',v='agent',c='display_digits',w=None):SLOTS[k]=(s,u,o,v,[c],w)
slot('power.enabled',BOOL,'1','setpoint',c='cpc_ac',w=('power.set','enabled'))
for k,s,u in [('plant.configured',BOOL,'1'),('method.configured',BOOL,'1'),('sample.configured',BOOL,'1'),('cycle.phase',PHASE,'1'),('cycle.stage',TEXT,'1'),('cycle.fault',TEXT,'1'),('cycle.elapsed_s',number('s',0),'s'),('rotor.speed_rad_s',number('rad/s',0,WMAX),'rad/s'),('rotor.angle_rad',number('rad',0,2*math.pi),'rad'),('rotor.rcf_m_s2',number('m/s^2',0),'m/s^2'),('pressure.gauge_pa',number('Pa',0),'Pa'),('detector.total_kg_m3',number('kg/m^3',0),'kg/m^3'),('column.stationary_fraction',number('1',0,1),'1'),('collector.x_m',number('m',0,.213),'m'),('collector.y_m',number('m',0,.36),'m'),('collector.tube_index',number('1',0,191),'1')]:slot(k,s,u)
for k in ['initial','loop','column','collected','waste']:slot('truth.'+k+'_solute_kg',number('kg',0),'kg','process_truth','evaluator','rotor')
for k in ['initial','reservoir','loop','column','collected','waste']:slot('truth.'+k+'_liquid_m3',number('m^3',0),'m^3','process_truth','evaluator','rotor')
class GilsonCpc250Runtime:
 def __init__(self,instance_id='cpc250_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.boot=0.;self.plant=None;self.method=None;self.sample=None;self.phase='idle';self.stage='standby';self.fault='';self.elapsed_s=0.;self.operation_id=None;self._operation_count=0;self.speed=0.;self.angle=0.;self.pressure=0.;self.signal=0.;self.sf=0.;self.reservoir=0.;self.loop_volume=0.;self.col_volume=0.;self.waste_volume=0.;self.tube=0;self.fractions=[];self.mobile=[];self.stationary=[];self.loop=[];self.waste=[];self.k=[];self.history=[];self.next_record=0.;self.templates={x['substance_id']:x for x in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return GilsonCpc250Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _values(self):
  initial=sum(x['mass_kg'] for x in self.sample['solutes']) if self.sample else 0.;collected=sum(sum(f['solute_masses_kg']) for f in self.fractions)
  v={'power.enabled':self.powered,'plant.configured':self.plant is not None,'method.configured':self.method is not None,'sample.configured':self.sample is not None,'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.fault':self.fault,'cycle.elapsed_s':self.elapsed_s,'rotor.speed_rad_s':self.speed,'rotor.angle_rad':self.angle,'rotor.rcf_m_s2':self.speed**2*(195*9.80665/WREF**2),'pressure.gauge_pa':self.pressure,'detector.total_kg_m3':self.signal,'column.stationary_fraction':self.sf,'collector.x_m':(self.tube//64)*.078+(self.tube%4)*.019,'collector.y_m':((self.tube%64)//4)*.024,'collector.tube_index':self.tube}
  for k,x in dict(initial=initial,loop=sum(self.loop),column=sum(map(sum,self.mobile))+sum(map(sum,self.stationary)),collected=collected,waste=sum(self.waste)).items():v['truth.'+k+'_solute_kg']=x
  for k,x in dict(initial=(self.plant['reservoir_m3'] if self.plant else 0.)+(CV if self.sample else 0.)+(self.sample['volume_m3'] if self.sample else 0.),reservoir=self.reservoir,loop=self.loop_volume,column=CV if self.sample else 0.,collected=self.col_volume,waste=self.waste_volume).items():v['truth.'+k+'_liquid_m3']=x
  return v
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('evaluator','debug')})
 def debug_snapshot(self):return copy.deepcopy(dict(state=self.read('evaluator'),mobile=self.mobile,stationary=self.stationary))
 def _measure(self):return dict(phase=self.phase,stage=self.stage,fault=self.fault,speed_rad_s=self.speed,pressure_gauge_pa=self.pressure,concentration_kg_m3=self.signal,stationary_fraction=self.sf,evidence='measured_Kow_reference_with_synthetic_plate_retention_hydraulic_model; concentration_not_UV_absorbance',template_source=TEMPLATE_REL)
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if self.phase=='running' and CAPS[capability][4]!='query' and capability!='acquisition.stop':raise ActionRejected('BUSY')
  if capability=='power.set':
   if not a['enabled'] and self.speed>1e-8:raise ActionRejected('PRECONDITION_FAILED')
   if a['enabled'] and not self.powered:self.boot=0.
   self.powered=a['enabled'];out=a
  elif capability=='plant.configure':
   if self.plant or not a['pre_equilibrated_confirmed'] or not a['dilute_stable_neutral_reviewed'] or not a['coefficient_evidence']:raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);self.reservoir=a['reservoir_m3'];out={'configured':True}
  elif capability=='method.configure':
   if self.sample or a['collection_start_s']>a['collection_end_s'] or a['collection_end_s']>a['duration_s'] or (a['speed_rad_s']>2000*math.pi/30 and not a['exceptional_speed_reviewed']):raise ActionRejected('PRECONDITION_FAILED')
   self.method=copy.deepcopy(a);out={'configured':True}
  elif capability=='sample.configure':
   if self.sample or not self.plant or not self.method or not a['filtered_10um_or_finer'] or not a['sample_id'] or not 1<=len(a['solutes'])<=8 or sum(x['mass_kg'] for x in a['solutes'])>.006 or len({x['substance_id'] for x in a['solutes']})!=len(a['solutes']):raise ActionRejected('PRECONDITION_FAILED')
   if any(x['substance_id'] not in self.templates for x in a['solutes']):raise ActionRejected('TEMPLATE_RANGE_UNAVAILABLE')
   self.sample=copy.deepcopy(a);self.loop_volume=a['volume_m3'];self.loop=[x['mass_kg'] for x in a['solutes']];self.waste=[0.]*len(self.loop);self.mobile=[[0.]*N for x in self.loop];self.stationary=copy.deepcopy(self.mobile);self.k=[self.templates[x['substance_id']]['values']['kow']**(1 if self.method['direction']=='DSC' else -1) for x in a['solutes']];self.sf=max(.1,min(.85,self.plant['reference_stationary_fraction']*math.sqrt(self.method['speed_rad_s']/WREF)/(1+.2*(self.method['flow_m3_s']/(15e-6/60)-1))));out=dict(configured=True,attachment_event='host_attach:'+a['sample_id'])
  elif capability=='acquisition.start':
   if not self.powered or self.boot<30 or not self.sample or self.phase not in ('idle','cancelled'):raise ActionRejected('PRECONDITION_FAILED')
   self.phase='running';self.stage='ramping';self._operation_count+=1;self.operation_id=f'{self.instance_id}:op{self._operation_count}';out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('PRECONDITION_FAILED')
   self.phase='cancelled';self.stage='coasting';out={'phase':self.phase}
  elif capability=='cpc.measure':out=self._measure()
  elif capability=='history.read':out=dict(records=copy.deepcopy(self.history),truncated=False,evidence='derived_pressure_and_mass_concentration;not_measured_UV_chromatogram')
  elif capability=='product.read':out=dict(substance_ids=[x['substance_id'] for x in self.sample['solutes']] if self.sample else [],fractions=copy.deepcopy(self.fractions),waste_solute_masses_kg=list(self.waste),measured_cpc250_recoveries=None,evidence='conditional_conservative_plate_model_not_experimental_CPC250_fractions',template_source=TEMPLATE_REL)
  elif capability=='reference.lookup':
   q=self.templates.get(a['substance_id'])
   if q is None:raise ActionRejected('TEMPLATE_RANGE_UNAVAILABLE')
   out=dict(substance_id=a['substance_id'],log10_kow=q['values']['log10_kow'],stationary_mobile_partition=q['values']['kow']**(-1 if self.method and self.method['direction']=='ASC' else 1),temperature_k=None,ph=None,source=q['source'],evidence=q['evidence'],template_source=TEMPLATE_REL)
  else:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][1],out)
  if CAPS[capability][4]!='query':self.revision+=1
  return out
 def _fail(self,msg):self.phase='failed';self.stage='coasting';self.fault=msg
 def _flow(self,dt):
  q=self.method['flow_m3_s'];v=q*dt;loop_out=min(v,self.loop_volume);stock_out=v-loop_out
  if stock_out>self.reservoir+1e-15:self._fail('RESERVOIR_EMPTY');return
  collect=self.method['collection_start_s']<=self.elapsed_s<self.method['collection_end_s']
  if collect and self.col_volume+v>192*self.method['fraction_volume_m3']+1e-14:self._fail('COLLECTOR_FULL');return
  incoming=[m*loop_out/self.loop_volume if self.loop_volume>0 else 0. for m in self.loop];self.loop=[max(0.,m-x) for m,x in zip(self.loop,incoming)];self.loop_volume=max(0.,self.loop_volume-loop_out);self.reservoir=max(0.,self.reservoir-stock_out);vm=CV*(1-self.sf)/N;vs=CV*self.sf/N;output=[]
  for k,m,s,feed in zip(self.k,self.mobile,self.stationary,incoming):
   outgoing=[x*v/vm for x in m];output.append(outgoing[-1]);m[:]=[x-y+(feed if i==0 else outgoing[i-1]) for i,(x,y) in enumerate(zip(m,outgoing))]
   for i in range(N):
    target=(m[i]+s[i])*vm/(vm+k*vs);delta=(target-m[i])*(1-math.exp(-self.plant['transfer_rate_s_inv']*dt));m[i]+=delta;s[i]-=delta
  self.signal=sum(output)/v
  if collect:
   remaining=v
   while remaining>1e-18:
    if not self.fractions or self.fractions[-1]['volume_m3']>=self.method['fraction_volume_m3']-1e-15:self.fractions.append(dict(tube_index=len(self.fractions),volume_m3=0.,solute_masses_kg=[0.]*len(output)))
    f=self.fractions[-1];take=min(remaining,self.method['fraction_volume_m3']-f['volume_m3']);f['volume_m3']+=take;f['solute_masses_kg']=[x+y*take/v for x,y in zip(f['solute_masses_kg'],output)];remaining-=take
   self.col_volume+=v;self.tube=self.fractions[-1]['tube_index']
  else:self.waste=[x+y for x,y in zip(self.waste,output)];self.waste_volume+=v
  self.elapsed_s+=dt
 def step(self,dt_s):
  if not isinstance(dt_s,(int,float)) or isinstance(dt_s,bool) or not math.isfinite(dt_s) or dt_s<0:raise ActionRejected('INVALID_ARGUMENT')
  if dt_s==0:return
  remaining=float(dt_s)
  while remaining>1e-12:
   dt=min(.25,remaining)
   if self.powered:self.boot+=dt
   target=self.method['speed_rad_s'] if self.phase=='running' else 0.;ramp=self.plant['angular_ramp_rad_s2'] if self.plant else 20.;self.speed+=max(-ramp*dt,min(ramp*dt,target-self.speed));self.angle=(self.angle+self.speed*dt)%(2*math.pi)
   flow=self.phase=='running' and abs(self.speed-target)<1e-10
   if flow:
    proposed=BPR+self.plant['hydraulic_resistance_pa_s_m3']*self.method['flow_m3_s']
    if proposed>1e7:self._fail('CPC_100BAR_GUARD');flow=False
   ptarget=proposed if flow else 0.;tau=self.plant['pressure_tau_s'] if self.plant else 2.;self.pressure+=(ptarget-self.pressure)*(1-math.exp(-dt/tau))
   if flow:
    self.stage='injection' if self.loop_volume>1e-15 else 'elution';used=min(dt,self.method['duration_s']-self.elapsed_s)
    # Split exactly on collection boundaries to avoid window ambiguity.
    for edge in [self.method['collection_start_s'],self.method['collection_end_s']]:
     if self.elapsed_s<edge<self.elapsed_s+used:used=edge-self.elapsed_s
    self._flow(used)
    if self.phase=='running' and self.elapsed_s>=self.method['duration_s']-1e-10:self.phase='succeeded';self.stage='coasting'
   if self.phase!='running' and self.speed==0:self.stage='complete' if self.phase=='succeeded' else 'stopped'
   self.time_s+=dt;remaining-=dt
   if self.time_s>=self.next_record:self.history.append(dict(time_s=self.time_s,elution_time_s=self.elapsed_s,pressure_gauge_pa=self.pressure,total_concentration_kg_m3=self.signal));self.next_record=self.time_s+10.
  self.revision+=1
class GilsonCpc250Session:
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
        meta=export_gilson_cpc250_contract_metadata()
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

def export_gilson_cpc250_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'GilsonCpc250Session.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key=='acquisition.start'; pre=[]
        if key=='acquisition.start':pre=[dict(description_zh='供电启动30s且宿主装置/方法/样品已配置，首次或取消续作',state_ids=['power.enabled','plant.configured','method.configured','sample.configured','cycle.phase'],enforced_by='runtime',check='powered and boot30s and configured and resumable')]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.gilson_cpc250.GilsonCpc250Session.invoke',invocation=f'session.invoke(instance_id="cpc250_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded表示规定流动时间结束；转子随后减速，正常断电需speed=0' if async_ else '不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝无修改；取消保留所有物料、转子减速、压力衰减，静止时两相再平衡和固定相流失未模拟；不是实机安全认证。去重仅进程内'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    evolving=list(slots)
    for key in ['acquisition.start','acquisition.stop','plant.configure','sample.configure']:
        caps[key]['state_effects'] += [dict(state_id=k,timing='on_step' if key.startswith('acquisition') else 'immediate',description_zh='有限体积、溶质账本与条件分配过程') for k in evolving if k not in [x['state_id'] for x in caps[key]['state_effects']]]
    mechanism=dict(component_ids=['rotor','pump','injection_valve','backflush_valve','dad','collection_probe'],state_ids=evolving,meaning_zh='24级守恒两相交换/平流与馏分收集',implementation='lumped',equations=['CV=250mL; N=24 equal mixed stages; Vs=S*CV/N,Vm=(1-S)*CV/N','S=clip(Sref*sqrt(omega/omega_ref)/(1+0.2*(Q/Qref-1)),0.1,0.85),host initial pre-equilibrated column only','Ksm=10**measured_logP for DSC(water mobile),inverse for ASC(octanol mobile)','out_i=Mmobile_i*Q*dt/Vm; upstream/outlet transport conserves each solute','Mmobile_eq=Mtotal*Vm/(Vm+Ksm*Vs); delta=(Mmobile_eq-Mmobile)*(1-exp(-host_k*dt))','initial_solute=loop+mobile+stationary+fractions+waste; initial_liquid=reservoir+loop+column+fractions+waste','pressure_target=20psi+host_R*Q; first-order lag; stop pump if target>100bar gauge','30s boot; speed finite host ramp; angular phase wraps2pi; RCF=omega^2*(195g/(1600rpm)^2)','collection time windows route to192finite30mLmaximum tubes or waste; head coordinate estimated4x16x3 layout'],parameters={},source_ids=['manufacturer_manual','manufacturer_plc','experimental_logp','simulation_assumption'],limitations=['60measured literature logP values are not60measured CPC250 chromatograms. Temperature/pH in dataset unknown; no claimed real instrument recovery or retention calibration.','Only reviewed dilute neutral stable solutes in equilibrated1-octanol/water; mutual solubility,ionization,chemical reactions,nonideal activity and temperature changes absent.','Stationary retention formula,24ideal stages,transfer rate,hydraulic resistance and ramp are host assumptions. Column starts pre-cleaned and pre-equilibrated; no stationary fill/equilibration/extrusion/backflush protocol.','ASC/DSC chosen before sample loading; automatic valve mid-run switching absent though hardware supports it. No arbitrary solvent system or independent sample solubility certification.','Stopping preserves phase inventory; physical loss of stationary phase,post-stop redistribution and emulsion formation not modeled. Resume is teaching continuation,not factory recovery guarantee.','Only total mass concentration signal; no measured UV spectra/molar absorptivity or detector delay/noise. Selected200–600nm DAD is geometry/configuration only.','Collector moves instantaneously between logical tube centers; exact factory trajectories,probe travel timing,drop transit and splash/contact absent.','Normal power off requires stopped rotor; real forced power failure,servo belt dynamics and emergency protection absent.','CPCbody70kg andPLC65kg separate; local physical mass/inertia/friction null. Proxy0.1kg inertias do not represent validated dynamics.','CPCfixed screwed cover is not a user-actuated hatch. Full factory touchscreens,LED timings,manual injection and service cleaning absent.','No external vacuum/cooling bath/gas attached. Solvent/waste facilities and actual electrical/hardware/robot constraints not verified.','NativeSession identity/async and process-to-scene verified separately; no deployment file isolation or Matterix nativeasync envelope.'])
    _ann.annotate_export('gilson_cpc250',caps,slots,{'centrifugal_partition':mechanism},inputs={'plant.configure':(PLANT,EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'centrifugal_partition':mechanism})

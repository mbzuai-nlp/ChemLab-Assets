"""Referenced NTU replay with explicitly synthetic, non-factory optical dynamics."""
from __future__ import annotations
import copy,json,math
from asset_runtime import _annotations as _ann
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT,PHASE
from asset_runtime.antonpaar_abbematadvanced import validate
ROOT=Path(__file__).resolve().parents[2];ASSET_TYPE_ID='instrument.hach_tl2300epa';CATALOG_ID='CHEM-014';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/hach_tl2300epa_turbidity_library/templates.json'
ANGLE=number('rad',0,1.7);NTU=number('NTU',0,4000);NULL_NTU={'anyOf':[NTU,{'type':'null'}]};TEMP=number('K',273.15,343.15)
COUNT={'type':'integer','enum':[1,10],'x-unit':'1'};MODE={'type':'string','enum':['single','continuous']}
NULL_TIME={'anyOf':[number('s',0),{'type':'null'}]}
RESULT=obj({'ntu':NULL_NTU,'stable':BOOL,'phase':PHASE,'quality':TEXT,'calibration_status':TEXT,'reference_accuracy_ntu':NULL_NTU,'sample_window_start_s':NULL_TIME,'sample_window_end_s':NULL_TIME,'tss_kg_m3':{'type':'null'},'evidence':TEXT,'template_source':TEXT})
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','button_power','tungsten_lamp'],'供电；断电取消并重置预热','device_command','agent',{'enabled':True},['power.enabled','warmup.elapsed_s','cycle.phase']),
 'lid.set_angle':(obj({'angle_rad':ANGLE}),obj({'angle_rad':ANGLE}),['lid'],'宿主驱动盖，开盖取消测量','device_command','agent',{'angle_rad':1.7},['lid.angle_rad','cycle.phase']),
 'filter.set_installed':(obj({'installed':BOOL}),obj({'installed':BOOL}),['epa_filter'],'断电维护EPA滤片状态','configuration','evaluator',{'installed':True},['filter.installed']),
 'sample.configure':(obj({'substance_id':TEXT,'volume_m3':number('m^3',20e-6,30e-6),'mass_kg':number('kg',1e-6,.1),'temperature_k':TEMP,'clean_indexed_cell':BOOL,'particle_response_gain':number('1',.1,2),'color_absorbance':number('1',0,5),'bubble_excess_ntu':number('NTU',0,1000),'bubble_tau_s':number('s',.1,3600)}),obj({'configured':BOOL}),['sample_cell','cell_interface'],'宿主装样与真实模板；独立光学参数均工程输入','configuration','evaluator',{'substance_id':'stablcal_20','volume_m3':20e-6,'mass_kg':.02,'temperature_k':296.15,'clean_indexed_cell':True,'particle_response_gain':1.,'color_absorbance':0.,'bubble_excess_ntu':0.,'bubble_tau_s':30.},['sample.loaded','sample.input_kg','sample.retained_kg','sample.input_m3','sample.retained_m3','turbidity.indicated_ntu']),
 'sample.unload':(obj({}),obj({'unloaded':BOOL}),['sample_cell','cell_interface'],'开盖移出样品，保留累计账本','configuration','evaluator',{},['sample.loaded','sample.removed_kg','sample.retained_kg','sample.removed_m3','sample.retained_m3','turbidity.indicated_ntu']),
 'method.configure':(obj({'ratio_enabled':BOOL,'average_count':COUNT,'mode':MODE}),obj({'configured':BOOL}),['display_digits','detector_90','detector_forward','detector_transmitted'],'比率、单次/连续与1/10次平均；其它档未实现','configuration','agent',{'ratio_enabled':True,'average_count':1,'mode':'single'},['method.ratio_enabled','method.average_count','method.mode','turbidity.indicated_ntu']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['tungsten_lamp','epa_filter','detector_90','detector_forward','detector_transmitted','display_digits'],'预热闭盖后定时测量','device_command','agent',{},['cycle.phase','cycle.elapsed_s','turbidity.stable','turbidity.indicated_ntu']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['display_digits'],'取消保留窗口与气泡衰减','device_command','agent',{},['cycle.phase']),
 'calibration.rapid':(obj({}),obj({'gain':number('1',.1,10),'range_max_ntu':number('NTU',40,40)}),['epa_filter','display_digits'],'20NTU标准完成测量后单点增益代理；暗电流假设0，仅0–40NTU','configuration','agent',{},['calibration.gain','calibration.status','turbidity.indicated_ntu']),
 'calibration.verify':(obj({'reference_ntu':number('NTU',.1,4000)}),obj({'deviation_ntu':number('NTU',-4000,4000),'within_five_percent':BOOL}),['display_digits'],'已知标准±5%核查，不自动校准','query','agent',{'reference_ntu':20.},[]),
 'turbidity.measure':(obj({}),RESULT,['display_digits','detector_90'],'纯读NTU、稳定性和校准；无通用TSS','query','agent',{},[]),
}
SLOTS={
 'power.enabled':(BOOL,'1','setpoint','agent',['power_inlet'],('power.set','enabled')),
 'lid.angle_rad':(ANGLE,'rad','scene','agent',['lid'],('lid.set_angle','angle_rad')),
 'filter.installed':(BOOL,'1','scene','agent',['epa_filter'],None),
 'warmup.elapsed_s':(number('s',0),'s','derived','agent',['tungsten_lamp'],None),
 'warmup.ready':(BOOL,'1','derived','agent',['tungsten_lamp'],None),
 'method.ratio_enabled':(BOOL,'1','setpoint','agent',['display_digits'],None),
 'method.average_count':(COUNT,'1','setpoint','agent',['display_digits'],None),
 'method.mode':(MODE,'1','setpoint','agent',['display_digits'],None),
 'cycle.phase':(PHASE,'1','derived','agent',['display_digits'],None),
 'cycle.elapsed_s':(number('s',0),'s','derived','agent',['display_digits'],None),
 'turbidity.indicated_ntu':(NULL_NTU,'NTU','derived','agent',['display_digits','detector_90'],None),
 'turbidity.stable':(BOOL,'1','derived','agent',['display_digits'],None),
 'calibration.gain':(number('1',.1,10),'1','setpoint','agent',['display_digits'],None),
 'calibration.status':(TEXT,'1','derived','agent',['display_digits'],None),
 'sample.loaded':(BOOL,'1','derived','agent',['sample_cell'],None),
 'sample.temperature_k':({'anyOf':[TEMP,{'type':'null'}]},'K','process_truth','evaluator',['sample_cell'],None),
 'sample.reference_ntu':(NULL_NTU,'NTU','process_truth','evaluator',['sample_cell'],None),
 'sample.bubble_excess_ntu':(number('NTU',0,1000),'NTU','process_truth','evaluator',['sample_cell'],None),
 'detector.normalized_90':(number('1',0),'1','process_truth','evaluator',['detector_90'],None),
 'detector.normalized_transmitted':(number('1',0,1),'1','process_truth','evaluator',['detector_transmitted'],None),
}
for axis,unit in [('kg','kg'),('m3','m^3')]:
 for key in ['input','retained','removed']:SLOTS[f'sample.{key}_{axis}']=(number(unit,0),unit,'process_truth','evaluator',['sample_cell'],None)
class HachTl2300epaRuntime:
 def __init__(self,instance_id='tl2300_01',*,initial_powered=False,prior_powered_time_s=0.):
  # Trusted world initialization, not an agent capability or elapsed simulation time.
  if type(initial_powered) is not bool or isinstance(prior_powered_time_s,bool) or not isinstance(prior_powered_time_s,(int,float)) or not math.isfinite(prior_powered_time_s) or prior_powered_time_s<0 or (not initial_powered and prior_powered_time_s!=0):raise ValueError('invalid initial power history')
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=initial_powered;self.warmup_s=float(prior_powered_time_s);self.lid_angle=0.;self.filter_installed=True
  self.ratio=True;self.average_count=1;self.mode='single';self.phase='idle';self.elapsed_s=0.;self.operation_id=None;self._operation_count=0
  self.sample=None;self.bubbles=0.;self.indicated=None;self.stable=False;self.gain=1.;self.calibrated=False;self.raw_mean=None;self.window=None;self.window_elapsed=0.;self.integral=0.;self.window_first=None
  self.input_kg=0.;self.removed_kg=0.;self.input_m3=0.;self.removed_m3=0.
  self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']}
  self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return HachTl2300epaSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _base(self):return 0. if not self.sample else self.sample['reference']['value']*self.sample['particle_response_gain']
 def _transmission(self):return 1. if not self.sample else math.exp(-self.sample['color_absorbance'])
 def _cal_status(self):return 'rapid_0_40' if self.calibrated and self.indicated is not None and self.indicated<=40 else 'rapid_outside_validated_range' if self.calibrated else 'uncalibrated'
 def _values(self):
  s=self.sample
  r={'power.enabled':self.powered,'lid.angle_rad':self.lid_angle,'filter.installed':self.filter_installed,'warmup.elapsed_s':self.warmup_s,'warmup.ready':self.powered and self.warmup_s>=(1800 if self.ratio else 3600),'method.ratio_enabled':self.ratio,'method.average_count':self.average_count,'method.mode':self.mode,'cycle.phase':self.phase,'cycle.elapsed_s':self.elapsed_s,'turbidity.indicated_ntu':self.indicated,'turbidity.stable':self.stable,'calibration.gain':self.gain,'calibration.status':self._cal_status(),'sample.loaded':s is not None,'sample.temperature_k':s['temperature_k'] if s else None,'sample.reference_ntu':s['reference']['value'] if s else None,'sample.bubble_excess_ntu':self.bubbles,'detector.normalized_90':(self._base()+self.bubbles)*self._transmission()/4000.,'detector.normalized_transmitted':self._transmission()}
  for axis in ['kg','m3']:
   r[f'sample.input_{axis}']=getattr(self,'input_'+axis);r[f'sample.removed_{axis}']=getattr(self,'removed_'+axis);r[f'sample.retained_{axis}']=0. if not s else s['mass_kg' if axis=='kg' else 'volume_m3']
  return r
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('evaluator','debug')})
 def debug_snapshot(self):return dict(state=self.read('evaluator'),sample=copy.deepcopy(self.sample))
 def _measure(self):
  quality='unavailable' if self.indicated is None else 'stale' if not self.powered or not self.stable or self.phase=='cancelled' else 'estimated'
  tolerance=None if self.indicated is None else (.02*self.indicated+.01 if self.indicated<=1000 else .05*self.indicated)
  return dict(ntu=self.indicated,stable=self.stable,phase=self.phase,quality=quality,calibration_status=self._cal_status(),reference_accuracy_ntu=tolerance,sample_window_start_s=self.window[0] if self.window else None,sample_window_end_s=self.window[1] if self.window else None,tss_kg_m3=None,evidence='measured_or_nominal_reference_with_synthetic_optics; accuracy_is_vendor_reference_not_guarantee',template_source=TEMPLATE_REL)
 def _invalidate(self):self.indicated=None;self.stable=False;self.raw_mean=None;self.window=None
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if self.phase=='running' and capability not in ('power.set','lid.set_angle','acquisition.stop','turbidity.measure'):raise ActionRejected('BUSY')
  if capability=='power.set':
   self.powered=a['enabled']
   if not self.powered:
    self.warmup_s=0.;self.stable=False
    if self.phase=='running':self.phase='cancelled'
   r=dict(a)
  elif capability=='lid.set_angle':
   self.lid_angle=a['angle_rad']
   if self.lid_angle>1e-4:
    self.stable=False
    if self.phase=='running':self.phase='cancelled'
   r=dict(a)
  elif capability=='filter.set_installed':
   if self.powered:raise ActionRejected('PRECONDITION_FAILED')
   self.filter_installed=a['installed'];self._invalidate();r=dict(a)
  elif capability=='sample.configure':
   rec=self.templates.get(a['substance_id'])
   if self.lid_angle<1.4 or self.sample or not rec or not rec['runtime_eligible']:raise ActionRejected('PRECONDITION_FAILED')
   self.sample=copy.deepcopy(a);self.sample['reference']=copy.deepcopy(rec);self.bubbles=a['bubble_excess_ntu'];self.input_kg+=a['mass_kg'];self.input_m3+=a['volume_m3'];self._invalidate();r={'configured':True}
  elif capability=='sample.unload':
   if self.lid_angle<1.4 or not self.sample:raise ActionRejected('PRECONDITION_FAILED')
   self.removed_kg+=self.sample['mass_kg'];self.removed_m3+=self.sample['volume_m3'];self.sample=None;self.bubbles=0.;self._invalidate();r={'unloaded':True}
  elif capability=='method.configure':
   if type(a['average_count']) is not int:raise ActionRejected('INVALID_ARGUMENT')
   self.ratio=a['ratio_enabled'];self.average_count=a['average_count'];self.mode=a['mode'];self._invalidate();r={'configured':True}
  elif capability=='acquisition.start':
   if not self._values()['warmup.ready'] or not self.filter_installed or self.lid_angle>1e-4 or not self.sample or not self.sample['clean_indexed_cell']:raise ActionRejected('PRECONDITION_FAILED')
   self._operation_count+=1;self.operation_id=f'{self.instance_id}:ntu:{self._operation_count}';self.phase='running';self.elapsed_s=0.;self.window_elapsed=0.;self.integral=0.;self.window_first=self.bubbles;self._invalidate();r={'phase':self.phase,'operation_id':self.operation_id}
  elif capability=='acquisition.stop':
   if self.phase=='running':self.phase='cancelled';self.stable=False
   r={'phase':self.phase}
  elif capability=='calibration.rapid':
   if not self.powered or self.warmup_s<3600 or not self.sample or self.sample['substance_id']!='stablcal_20' or self.sample['temperature_k']>313.15 or not self.stable or not self.raw_mean or self.lid_angle>1e-4 or not .1<=20/self.raw_mean<=10:raise ActionRejected('PRECONDITION_FAILED')
   self.gain=20/self.raw_mean;self.calibrated=True;self.indicated=20.;r={'gain':self.gain,'range_max_ntu':40.}
  elif capability=='calibration.verify':
   if not self.powered or not self.stable or self.indicated is None or self.warmup_s<3600:raise ActionRejected('PRECONDITION_FAILED')
   d=self.indicated-a['reference_ntu'];return {'deviation_ntu':d,'within_five_percent':abs(d)<=.05*a['reference_ntu']+1e-12}
  else:return self._measure()
  self.revision+=1;return copy.deepcopy(r)
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<=0:raise ValueError('finite positive dt_s required')
  left=float(dt_s)
  while left>1e-10:
   active=self.phase=='running';duration=6.8 if self.average_count==1 else 14.;h=min(left,duration-self.window_elapsed) if active else left
   old=self.bubbles;tau=self.sample['bubble_tau_s'] if self.sample else 1.;self.bubbles=old*math.exp(-h/tau)
   if self.powered:self.warmup_s+=h
   if active:
    factor=1. if self.ratio else self._transmission();self.integral+=factor*(self._base()*h+old*tau*(1-math.exp(-h/tau)));self.window_elapsed+=h;self.elapsed_s+=h
    if self.window_elapsed>=duration-1e-9:
     self.raw_mean=self.integral/duration;v=self.gain*self.raw_mean;limit=4000 if self.ratio else 40
     self.indicated=round(v,3) if 0<=v<=limit+1e-9 else None
     self.stable=self.indicated is not None and abs(self.gain*factor*(self.window_first-self.bubbles))<=max(.01,.02*v)
     self.window=(self.time_s+h-duration,self.time_s+h)
     if self.mode=='single':self.phase='succeeded' if self.indicated is not None else 'failed'
     self.window_elapsed=0.;self.integral=0.;self.window_first=self.bubbles
   self.time_s+=h;left-=h
  self.revision+=1;return self.read('evaluator')
class HachTl2300epaSession:
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
        meta=export_hach_tl2300epa_contract_metadata()
        return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',
          capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},
          state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})

    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id); h=self._host; vals=h.read(self._role); ids=list(vals) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids): raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in vals for k in ids): raise ActionRejected('ACCESS_DENIED')
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},
          observations={k:dict(value=copy.deepcopy(vals[k]),unit=SLOTS[k][1],quality=h._measure()['quality'] if k=='turbidity.indicated_ntu' else ('unavailable' if vals[k] is None else ('estimated' if SLOTS[k][2]=='derived' else 'valid')),sample_time_s=h.window[1] if k=='turbidity.indicated_ntu' and h.window else h.time_s,sample_window_s=list(h.window) if k=='turbidity.indicated_ntu' and h.window else None) for k in ids})

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

def export_hach_tl2300epa_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'HachTl2300epaSession.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    slots['turbidity.indicated_ntu']['sampling']=dict(mode='interval_mean',time_domain='simulation',window_description='最近完成的6.8或14秒光学均值窗口；纯读保留原始端点，过期读数不改成当前瞬时值')
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key=='acquisition.start'; pre=[]
        conditions={
         'acquisition.start':('预热、闭盖、EPA滤片、清洁索引管与样品',['warmup.ready','lid.angle_rad','filter.installed','sample.loaded'],'warmup>=1800 ratio_on or 3600 ratio_off; lid<=1e-4; filter and sample.clean_indexed_cell'),
         'sample.configure':('宿主开盖、空位、有效定量模板',['lid.angle_rad','sample.loaded'],'lid>=1.4 and empty and runtime_eligible'),
         'sample.unload':('宿主开盖移出已装样品',['lid.angle_rad','sample.loaded'],'lid>=1.4 and loaded'),
         'filter.set_installed':('断电维护滤片',['power.enabled'],'not powered'),
         'calibration.rapid':('1h预热、20NTU标准稳定、<=40C、闭盖',['warmup.elapsed_s','turbidity.stable','lid.angle_rad'],'powered and warmup>=3600 and stablcal_20 and stable and T<=313.15 and .1<=20/raw<=10'),
         'calibration.verify':('1h预热且最近读数稳定',['warmup.elapsed_s','turbidity.stable'],'powered and warmup>=3600 and stable and indicated is not None')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.hach_tl2300epa.HachTl2300epaSession.invoke',invocation=f'session.invoke(instance_id="tl2300_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；取消保留窗口及气泡衰减；连续模式须显式停止' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电不回滚已经发生的气泡衰减和完成的窗口。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    mechanism=dict(component_ids=['tungsten_lamp','epa_filter','sample_cell','detector_90','detector_forward','detector_transmitted'],state_ids=['sample.reference_ntu','sample.bubble_excess_ntu','turbidity.indicated_ntu','sample.input_kg','sample.retained_kg','sample.removed_kg','sample.input_m3','sample.retained_m3','sample.removed_m3'],meaning_zh='实测NTU回放与非厂家颜色/气泡代理，不作NTU质量换算',implementation='lumped',equations=['T=exp(-A); S90=(NTU_reference*particle_gain+bubbles)*T/4000','ratio_on=4000*S90/T; ratio_off=4000*S90','bubbles=bubbles0*exp(-t/tau); exact time integral over 6.8 or 14 seconds','display=round(gain*average,3); range 0..4000 on, 0..40 off','mass_input=mass_retained+mass_removed; volume_input=volume_retained+volume_removed'],parameters={'stray_light':dict(value=0.,unit='NTU',meaning_zh='未标定暗电流/杂散光零值代理',evidence='assumption',source_ids=['simulation_assumption'],uncertainty=None)},source_ids=['manufacturer_primary','water_templates','simulation_assumption'],limitations=['前向探测器只有几何，无真实比率权重；颜色完全抵消仅教学代理。','定时完成不保证真实固件稳定；2%或0.01NTU漂移阈值是工程假设。','只有1/10次平均；新版按时间窗设置，未假造中间档。','0.001NTU量化不代表高量程真实分辨率。','快速校准仅20NTU单点增益、暗电流0；完整多点、RST、Bubble Reject未实现。','无Mie求解、沉降、粒径/TSS推断或真实精度验证。'])
    _ann.annotate_export('hach_tl2300epa',caps,slots,{'turbidity_optics':mechanism},inputs={})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'turbidity_optics':mechanism})

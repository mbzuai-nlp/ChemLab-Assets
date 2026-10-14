"""MP70 four-capillary ramp/transmission surrogate; SI, no empirical video or purity assay."""
from __future__ import annotations
import copy, json, math
from asset_runtime import _annotations as _ann
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected, number, obj, BOOL, TEXT, PHASE
from asset_runtime.antonpaar_abbematadvanced import validate
ROOT=Path(__file__).resolve().parents[2]
ASSET_TYPE_ID='instrument.mt_mp70'; CATALOG_ID='CHEM-006'; PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/mt_mp70_thermal_library/templates.json'
TEMP=number('K',293.15,623.15); NULL_TEMP={'anyOf':[TEMP,{'type':'null'}]}; FRAC=number('1',0,1); NULL_FRAC={'anyOf':[FRAC,{'type':'null'}]}
CHANNEL={'type':'number','enum':[0,1,2,3],'x-unit':'1'}
METHOD=obj({'method_id':TEXT,'start_k':TEMP,'end_k':TEMP,'ramp_k_s':number('K/s',.1/60,20/60),'criterion':{'type':'string','enum':['transmission','visual_proxy']}})
POINT=obj({'time_s':number('s',0),'temperature_k':TEMP,'transmission':FRAC})
RESULT=obj({'channel':CHANNEL,'onset_k':NULL_TEMP,'clear_k':NULL_TEMP,'transmission':NULL_FRAC,'phase':PHASE,'quality':TEXT,'evidence':TEXT,'template_source':TEXT,'video_status':{'type':'string','const':'not_implemented'}})
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','display_digits'],'供电；断电取消但保留样品状态','device_command','agent',{'enabled':True},['power.enabled','cycle.phase']),
 'thermal.set_temperature':(obj({'temperature_k':TEMP}),obj({'temperature_k':TEMP}),['heater','temperature_sensor'],'写温度目标，实际升降温只由step推进','device_command','agent',{'temperature_k':293.15},['thermal.target_k']),
 'method.configure':(METHOD,obj({'stored_count':number('1',1,20)}),['display_digits'],'保存/替换最多20种温度和判定方法','configuration','agent',{'method_id':'demo','start_k':303.15,'end_k':363.15,'ramp_k_s':20/60,'criterion':'transmission'},['method.count']),
 'method.select':(obj({'method_id':TEXT}),obj({'method_id':TEXT}),['display_digits'],'选择已保存方法；不推进时间','configuration','agent',{'method_id':'demo'},['method.selected']),
 'sample.configure':(obj({'channel':CHANNEL,'substance_id':TEXT,'mass_kg':number('kg',1e-7,1e-4),'purity':number('1',.5,1),'particle_size_m':number('m',1e-6,1e-3),'decomposition_onset_k':NULL_TEMP,'decomposition_source':TEXT}),obj({'configured':BOOL}),['capillary_0','capillary_1','capillary_2','capillary_3'],'受信任宿主初始化独立毛细管固体样品；不是机器人装样','configuration','evaluator',{'channel':0,'substance_id':'benzoic_acid','mass_kg':2e-6,'purity':1.,'particle_size_m':50e-6,'decomposition_onset_k':None,'decomposition_source':''},['sample.input_kg','sample.retained_kg','sample.loaded_count']),
 'sample.unload':(obj({'channel':CHANNEL}),obj({'unloaded':BOOL}),['capillary_0','capillary_1','capillary_2','capillary_3'],'冷却后宿主卸载至外部接收账本','configuration','evaluator',{'channel':0},['sample.retained_kg','sample.removed_kg','sample.loaded_count']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['heater','led_source','camera_sensor','display_digits'],'预热、规定速率升温、透光阈值识别四路熔程代理','device_command','agent',{},['cycle.phase','cycle.elapsed_s','thermal.target_k']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['display_digits'],'取消后保留温度、熔化与分解进度和曲线','device_command','agent',{},['cycle.phase','thermal.target_k']),
 'melting.measure':(obj({'channel':CHANNEL}),RESULT,['camera_sensor','display_digits'],'纯读熔化起止代理；不返回样品身份或纯度','query','agent',{'channel':0},[]),
 'melting.curve':(obj({'channel':CHANNEL}),obj({'channel':CHANNEL,'points':{'type':'array','items':POINT},'evidence':{'type':'string','const':'synthetic'},'template_source':TEXT,'sample_interval_s':number('s',1,1),'truncated':BOOL}),['camera_sensor','display_digits'],'返回1s采样合成透光曲线，最多20000点，不是视频','query','agent',{'channel':0},[]),
}
SLOTS={
 'power.enabled':(BOOL,'1','setpoint','agent',['power_inlet'],('power.set','enabled')),
 'thermal.target_k':(TEMP,'K','setpoint','agent',['heater'],('thermal.set_temperature','temperature_k')),
 'thermal.temperature_k':(TEMP,'K','process_truth','evaluator',['heater'],None),
 'thermal.indicated_temperature_k':(TEMP,'K','derived','agent',['temperature_sensor','display_digits'],None),
 'method.count':(number('1',0,20),'1','derived','agent',['display_digits'],None),
 'method.selected':(TEXT,'1','setpoint','agent',['display_digits'],('method.select','method_id')),
 'cycle.phase':(PHASE,'1','derived','agent',['display_digits'],None),
 'cycle.stage':({'type':'string','enum':['idle','preheat','ramp','hold']},'1','derived','agent',['heater'],None),
 'cycle.elapsed_s':(number('s',0),'s','derived','agent',['display_digits'],None),
 'sample.loaded_count':(number('1',0,4),'1','derived','agent',['capillary_0','capillary_1','capillary_2','capillary_3'],None),
 'sample.input_kg':(number('kg',0),'kg','process_truth','evaluator',['heater'],None),
 'sample.retained_kg':(number('kg',0),'kg','process_truth','evaluator',['heater'],None),
 'sample.removed_kg':(number('kg',0),'kg','process_truth','evaluator',['heater'],None),
 'sample.degraded_kg':(number('kg',0),'kg','process_truth','evaluator',['heater'],None),
}
for i in range(4):
    for field,schema,unit in [('onset_k',NULL_TEMP,'K'),('clear_k',NULL_TEMP,'K'),('transmission',NULL_FRAC,'1')]:
        SLOTS[f'channel_{i}.{field}']=(schema,unit,'derived','agent',['camera_sensor','display_digits'],None)
class MtMp70Runtime:
    def __init__(self,instance_id='mp70_01'):
        self.instance_id=instance_id; self.time_s=0.; self.revision=0; self.powered=False; self.temperature_k=293.15; self.target_k=293.15
        self.methods={}; self.selected=''; self.samples={}; self.input_kg=0.; self.removed_kg=0.
        self.phase='idle'; self.stage='idle'; self.elapsed_s=0.; self.operation_id=None; self._operation_count=0
        self.templates=json.loads((ROOT/TEMPLATE_REL).read_text())['substances']
        self._principals={'__public_agent__':'agent'}; self._requests={}; self._owners={}
    def register_principal(self,principal_id,visibility='agent'):
        if visibility not in ('agent','evaluator','debug'): raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals: raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id='__public_agent__'):
        if principal_id not in self._principals: raise ActionRejected('ACCESS_DENIED')
        return MtMp70Session(self,principal_id)
    def describe(self): return self.session().describe(instance_id=self.instance_id)
    def _values(self):
        r={'power.enabled':self.powered,'thermal.target_k':self.target_k,'thermal.temperature_k':self.temperature_k,'thermal.indicated_temperature_k':round(self.temperature_k-273.15,2)+273.15,'method.count':len(self.methods),'method.selected':self.selected,'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.elapsed_s':self.elapsed_s,'sample.loaded_count':len(self.samples),'sample.input_kg':self.input_kg,'sample.retained_kg':sum(s['mass_kg'] for s in self.samples.values()),'sample.removed_kg':self.removed_kg,'sample.degraded_kg':sum(s['mass_kg']*s['degraded'] for s in self.samples.values())}
        for i in range(4):
            s=self.samples.get(i,{})
            for k in ['onset_k','clear_k','transmission']: r[f'channel_{i}.{k}']=s.get(k)
        return r
    def read(self,visibility='agent'): return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('evaluator','debug')})
    def debug_snapshot(self): return dict(state=self.read('evaluator'),samples=copy.deepcopy(self.samples),methods=copy.deepcopy(self.methods))
    def _measure(self,channel=0):
        s=self.samples.get(channel,{})
        return dict(channel=channel,onset_k=s.get('onset_k'),clear_k=s.get('clear_k'),transmission=s.get('transmission'),phase=self.phase,quality='unavailable' if not s else ('suspect_decomposition' if s.get('degraded',0)>.01 else 'estimated_decomposition_unknown' if s.get('decomposition_onset_k') is None else 'estimated'),evidence='synthetic',template_source=TEMPLATE_REL,video_status='not_implemented')
    def invoke(self,capability,**a):
        if capability not in CAPS: raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability][0],a)
        if capability not in ('power.set','acquisition.stop','melting.measure','melting.curve') and self.phase=='running': raise ActionRejected('BUSY')
        if capability=='power.set':
            self.powered=a['enabled']
            if not self.powered and self.phase=='running': self.phase='cancelled'; self.stage='hold'; self.target_k=293.15
            r=dict(a)
        elif capability=='thermal.set_temperature': self.target_k=a['temperature_k']; r=dict(a)
        elif capability=='method.configure':
            if not a['method_id'].strip() or a['start_k']>=a['end_k']: raise ActionRejected('INVALID_ARGUMENT')
            if a['method_id'] not in self.methods and len(self.methods)>=20: raise ActionRejected('PRECONDITION_FAILED')
            self.methods[a['method_id']]=copy.deepcopy(a); r={'stored_count':len(self.methods)}
        elif capability=='method.select':
            if a['method_id'] not in self.methods: raise ActionRejected('PRECONDITION_FAILED')
            self.selected=a['method_id']; r=dict(a)
        elif capability=='sample.configure':
            t=self.templates.get(a['substance_id'])
            if not t or not t['runtime_eligible'] or a['channel'] in self.samples or self.temperature_k>323.15 or self.temperature_k>=t['value']-1: raise ActionRejected('PRECONDITION_FAILED')
            if a['decomposition_onset_k'] is not None and not a['decomposition_source'].strip(): raise ActionRejected('INVALID_ARGUMENT')
            s=copy.deepcopy(a); s.update(temperature_k=self.temperature_k,melt_fraction=0.,degraded=0.,onset_k=None,clear_k=None,transmission=.05,curve=[],truncated=False,used=False)
            self.samples[int(a['channel'])]=s; self.input_kg+=a['mass_kg']; r={'configured':True}
        elif capability=='sample.unload':
            if a['channel'] not in self.samples or self.temperature_k>323.15: raise ActionRejected('PRECONDITION_FAILED')
            self.removed_kg+=self.samples.pop(a['channel'])['mass_kg']; r={'unloaded':True}
        elif capability=='acquisition.start':
            if not self.powered or self.selected not in self.methods or not self.samples or any(s['used'] for s in self.samples.values()): raise ActionRejected('PRECONDITION_FAILED')
            m=self.methods[self.selected]
            if any(m['start_k']>=self.templates[s['substance_id']]['value']-1 for s in self.samples.values()): raise ActionRejected('PRECONDITION_FAILED')
            self._operation_count+=1; self.operation_id=f'{self.instance_id}:measurement:{self._operation_count}'; self.phase='running'; self.stage='preheat'; self.elapsed_s=0.; self.target_k=m['start_k']
            for s in self.samples.values(): s['used']=True
            r={'phase':self.phase,'operation_id':self.operation_id}
        elif capability=='acquisition.stop':
            if self.phase=='running': self.phase='cancelled'; self.stage='hold'; self.target_k=self.temperature_k
            r={'phase':self.phase}
        elif capability=='melting.measure': return self._measure(a['channel'])
        else:
            s=self.samples.get(a['channel'],{}); return dict(channel=a['channel'],points=copy.deepcopy(s.get('curve',[])),evidence='synthetic',template_source=TEMPLATE_REL,sample_interval_s=1.,truncated=s.get('truncated',False))
        self.revision+=1; return copy.deepcopy(r)
    def step(self,dt_s):
        if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<=0: raise ValueError('finite positive dt_s required')
        left=float(dt_s)
        while left>1e-10:
            # Host-aligned integration; sample output on exact elapsed integer seconds.
            active=self.phase=='running'; h=min(left,1.)
            if active: h=min(h,math.floor(self.elapsed_s+1e-8)+1-self.elapsed_s)
            if active and self.stage=='ramp':
                m=self.methods[self.selected]; h=min(h,max((m['end_k']-self.target_k)/m['ramp_k_s'],1e-8)); self.target_k=min(m['end_k'],self.target_k+m['ramp_k_s']*h)
            target=self.target_k if self.powered else 293.15
            # At 20 C ambient, 150*ln((350-20)/(50-20)) ~= 359.7 s, not fixed 330 s.
            tau=150. if target<self.temperature_k else 15.
            self.temperature_k=target+(self.temperature_k-target)*math.exp(-h/tau)
            if active: self.elapsed_s+=h
            # Thermal/phase changes continue after acquisition cancellation; only recording stops.
            for s in self.samples.values():
                tau_s=1.+2.*s['particle_size_m']/50e-6
                s['temperature_k']=self.temperature_k+(s['temperature_k']-self.temperature_k)*math.exp(-h/tau_s)
                center=self.templates[s['substance_id']]['value']-10*(1-s['purity']); width=.25+5*(1-s['purity'])+s['particle_size_m']/1e-3
                fraction=1/(1+math.exp(max(-60,min(60,-(s['temperature_k']-center)/width))))
                s['melt_fraction']=max(s['melt_fraction'],fraction)
                if s['decomposition_onset_k'] is not None and s['temperature_k']>=s['decomposition_onset_k']: s['degraded']=1-(1-s['degraded'])*math.exp(-h/30.)
                s['transmission']=max(0.,min(1.,(.05+.9*s['melt_fraction'])*(1-.85*s['degraded'])))
                criterion=s['melt_fraction'] if active and self.methods[self.selected]['criterion']=='visual_proxy' else (s['transmission']-.05)/.9
                if active and self.stage=='ramp':
                    indicated=round(self.temperature_k-273.15,2)+273.15
                    if criterion>=.1 and s['onset_k'] is None: s['onset_k']=indicated
                    if criterion>=.9 and s['clear_k'] is None: s['clear_k']=indicated
                if active and abs(self.elapsed_s-round(self.elapsed_s))<1e-7:
                    if len(s['curve'])<20000: s['curve'].append(dict(time_s=round(self.elapsed_s,6),temperature_k=round(self.temperature_k-273.15,2)+273.15,transmission=s['transmission']))
                    else: s['truncated']=True
            if active:
                if self.stage=='preheat' and abs(self.temperature_k-self.target_k)<.1: self.stage='ramp'
                if self.stage=='ramp' and self.target_k>=self.methods[self.selected]['end_k']-1e-8: self.phase='succeeded'; self.stage='hold'
            self.time_s+=h; left-=h
        self.revision+=1; return self.read('evaluator')
class MtMp70Session:
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
        meta=export_mt_mp70_contract_metadata()
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

def export_mt_mp70_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'MtMp70Session.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key=='acquisition.start'; pre=[]
        conditions={
         'acquisition.start':('供电、方法存在且有未使用固体样品；初温低于文献熔点',['power.enabled','method.selected','sample.loaded_count','cycle.phase'],'powered and selected in methods and samples and all not used and start < reference-1'),
         'sample.configure':('宿主授权、槽为空、炉温不高于50C且低于样品熔点',['thermal.indicated_temperature_k','cycle.phase'],'not running and channel empty and eligible reference and T<=323.15 and T<reference-1'),
         'sample.unload':('宿主授权、已冷却、槽有样品',['thermal.indicated_temperature_k','cycle.phase'],'not running and T<=323.15 and channel loaded'),
         'method.configure':('运行中锁定；最多20方法，初温低于终温',['cycle.phase','method.count'],'not running and start<end and (existing id or count<20)'),
         'method.select':('运行中锁定且方法存在',['cycle.phase','method.count'],'not running and id in methods'),
         'thermal.set_temperature':('运行中锁定温度目标',['cycle.phase'],'not running')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.mt_mp70.MtMp70Session.invoke',invocation=f'session.invoke(instance_id="mp70_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；取消保留温度、相态、分解和曲线' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电不回滚已经发生的熔化和分解。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    mechanism=dict(component_ids=['heater','temperature_sensor','led_source','camera_sensor','capillary_0','capillary_1','capillary_2','capillary_3'],
      state_ids=['thermal.temperature_k','thermal.target_k','sample.retained_kg','sample.input_kg','sample.removed_kg','sample.degraded_kg'],
      meaning_zh='文献熔点参考驱动四路毛细管热滞后、纯度/颗粒展宽与合成透光，质量账本守恒',implementation='lumped',
      equations=['T_next=T_target+(T-T_target)*exp(-dt/tau); tau_heating=15s, tau_cooling=150s',
      'ramp_target_next=min(end,target+rate*dt)',
      'Tm_proxy=Tm_reference-10*(1-purity); width=0.25+5*(1-purity)+particle_size/0.001',
      'melt_fraction=max(previous,logistic((T_sample-Tm_proxy)/width)); transmission=(0.05+0.9*melt_fraction)*(1-0.85*degraded)',
      'known-host-decomposition only: degraded=1-(1-degraded)*exp(-dt/30) when T_sample>=threshold',
      'input_mass=retained_mass+removed_mass; retained_mass=intact_mass+degraded_mass (no reaction stoichiometry claimed)'],
      parameters={'cooling_tau':dict(value=150.,unit='s',meaning_zh='温差相关冷却代理，不把厂家最短330s当固定工况',evidence='assumption',source_ids=['simulation_assumption'],uncertainty=None)},
      source_ids=['thermal_templates','simulation_assumption'],limitations=['未标定纯度/颗粒/热滞后系数；不输出纯度检测结果。','分解阈值未知时不推断；使用宿主提供有来源的阈值仍仅为暗化代理，未求化学反应和气体逸出。','熔程和透光曲线synthetic，视频未实现；非DSC热流模型。'])
    _ann.annotate_export('mt_mp70',caps,slots,{'capillary_melting':mechanism},inputs={})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'capillary_melting':mechanism})

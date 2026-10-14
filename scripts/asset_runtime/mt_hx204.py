"""CHEM-002 HX204: public boiling points plus explicitly synthetic drying kinetics."""
from __future__ import annotations
import copy
from asset_runtime import _annotations as _ann
import json
import math
from pathlib import Path

PROTOCOL_VERSION='1.0.0'
ASSET_TYPE_ID='instrument.mt_hx204'
CATALOG_ID='CHEM-002'
ROOT=Path(__file__).resolve().parents[2]
TEMPLATE_REL='assets/reactions/mt_hx204_moisture_library/templates.json'

class ActionRejected(ValueError):
    """Stable errors never disclose sample composition."""

def number(unit,lo=None,hi=None):
    d={'type':'number','x-unit':unit}
    if lo is not None: d['minimum']=lo
    if hi is not None: d['maximum']=hi
    return d

def obj(props):
    return dict(type='object',properties=props,required=list(props),additionalProperties=False)

BOOL={'type':'boolean'}
TEXT={'type':'string'}
PHASE={'type':'string','enum':['idle','running','succeeded','cancelled']}
MODE={'type':'string','enum':['standard','rapid','gentle','steps']}
BASIS={'type':'string','enum':['wet','dry']}
POINT=obj({'time_s':number('s',0),'mass_kg':number('kg',0),'loss_fraction':number('1',0,1)})
RESULT=obj({'phase':PHASE,'mass_kg':number('kg',0),'loss_fraction':number('1',0,1),
 'reported_ratio':{'anyOf':[number('1',0),{'type':'null'}]},'basis':BASIS,
 'curve':{'type':'array','items':POINT},'template_source':TEXT,
 'evidence':{'type':'string','const':'synthetic'},
 'interpretation':{'type':'string','const':'loss_on_drying_not_water_specific'}})

# input, output, components, meaning, kind, visibility, example, effects
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','display_digits'],'供电设定；断电取消采集并保留失重','device_command','agent',{'enabled':True},['power.enabled','cycle.phase']),
 'thermal.set_temperature':(obj({'temperature_k':number('K',313.15,503.15)}),obj({'temperature_k':number('K',313.15,503.15)}),['heater','temperature_sensor','display_digits'],'整数摄氏度目标写入；实际升温只随step演化','device_command','agent',{'temperature_k':378.15},['thermal.target_k']),
 'method.configure':(obj({'mode':MODE,'duration_s':number('s',60,28800),'basis':BASIS}),obj({'mode':MODE,'duration_s':number('s',60,28800),'basis':BASIS}),['terminal','display_digits'],'升温模式工程代理、定时终点、湿基/干基结果设置','configuration','agent',{'mode':'standard','duration_s':60,'basis':'wet'},['method.mode','method.duration_s','method.basis']),
 'lid.set_position':(obj({'position_m':number('m',0,.1085)}),obj({'position_m':number('m',0,.1085)}),['lid','heater'],'宿主设置升降盖位置；采集中不得打开','device_command','agent',{'position_m':.1085},['lid.position_m']),
 'sample.configure':(obj({'species_kg':{'type':'object','additionalProperties':number('kg',0,.201)},'residue_kg':number('kg',0,.201)}),obj({'configured':BOOL}),['sample_pan','balance_sensor'],'受信任宿主初始化样品账本；非机器人投料','configuration','evaluator',{'species_kg':{'ethanol':.001},'residue_kg':.001},['sample.initial_kg','sample.remaining_kg','sample.evaporated_kg','balance.indicated_kg','cycle.phase']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['heater','sample_pan','balance_sensor','display_digits'],'启动干燥；必须由宿主推进时间完成','device_command','agent',{},['cycle.phase','cycle.elapsed_s']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['heater','display_digits'],'取消并保留已蒸发质量与曲线','device_command','agent',{},['cycle.phase']),
 'moisture.measure':(obj({}),RESULT,['sample_pan','balance_sensor','display_digits'],'纯读取质量曲线与失重率；不识别化学水','query','agent',{},[]),
}
# schema, unit, origin, visibility, components, writable capability/argument
SLOTS={
 'power.enabled':(BOOL,'1','setpoint','agent',['power_inlet'],('power.set','enabled')),
 'thermal.target_k':(number('K',313.15,503.15),'K','setpoint','agent',['heater'],('thermal.set_temperature','temperature_k')),
 'thermal.temperature_k':(number('K',0),'K','process_truth','evaluator',['heater'],None),
 'thermal.indicated_temperature_k':(number('K',0),'K','derived','agent',['temperature_sensor','display_digits'],None),
 'lid.position_m':(number('m',0,.1085),'m','scene','agent',['lid'],('lid.set_position','position_m')),
 'method.mode':(MODE,'1','setpoint','agent',['terminal'],None),
 'method.duration_s':(number('s',60,28800),'s','setpoint','agent',['terminal'],None),
 'method.basis':(BASIS,'1','setpoint','agent',['terminal'],None),
 'cycle.phase':(PHASE,'1','derived','agent',['display_digits'],None),
 'cycle.elapsed_s':(number('s',0),'s','derived','agent',['display_digits'],None),
 'balance.indicated_kg':(number('kg',0),'kg','derived','agent',['balance_sensor','display_digits'],None),
 'sample.initial_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_pan'],None),
 'sample.remaining_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_pan'],None),
 'sample.evaporated_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_pan'],None),
}

def validate(schema,value):
    typ=schema.get('type')
    if typ=='number':
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value): raise ActionRejected('INVALID_ARGUMENT')
        if value<schema.get('minimum',-math.inf) or value>schema.get('maximum',math.inf): raise ActionRejected('INVALID_ARGUMENT')
    elif typ=='boolean' and type(value) is not bool: raise ActionRejected('INVALID_ARGUMENT')
    elif typ=='string' and not isinstance(value,str): raise ActionRejected('INVALID_ARGUMENT')
    elif typ=='object':
        if not isinstance(value,dict): raise ActionRejected('INVALID_ARGUMENT')
        props=schema.get('properties',{})
        if not set(schema.get('required',[]))<=value.keys(): raise ActionRejected('INVALID_ARGUMENT')
        for key,val in value.items():
            if not isinstance(key,str): raise ActionRejected('INVALID_ARGUMENT')
            sub=props.get(key,schema.get('additionalProperties',True))
            if sub is False: raise ActionRejected('INVALID_ARGUMENT')
            if isinstance(sub,dict): validate(sub,val)
    if 'enum' in schema and value not in schema['enum']: raise ActionRejected('INVALID_ARGUMENT')

class MtHx204Runtime:
    def __init__(self,instance_id='hx204_01'):
        self.instance_id=instance_id; self.time_s=0.; self.revision=0
        self.powered=False; self.target_k=378.15; self.temperature_k=298.15
        self.lid_m=0.; self.mode='standard'; self.duration_s=600.; self.basis='wet'
        self.phase='idle'; self.elapsed_s=0.; self.residue_kg=0.
        self.initial_species={}; self.remaining={}; self.evaporated={}; self.curve=[]
        self.operation_id=None; self._operation_count=0
        self._principals={'__public_agent__':'agent'}; self._requests={}; self._owners={}
        self.templates=json.loads((ROOT/TEMPLATE_REL).read_text())['substances']

    def register_principal(self,principal_id,visibility='agent'):
        if visibility not in ('agent','evaluator','debug'): raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals: raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility

    def session(self,principal_id='__public_agent__'):
        if principal_id not in self._principals: raise ActionRejected('ACCESS_DENIED')
        return MtHx204Session(self,principal_id)

    def _values(self):
        m=self.residue_kg+sum(self.remaining.values())
        return {'power.enabled':self.powered,'thermal.target_k':self.target_k,
          'thermal.temperature_k':self.temperature_k,'thermal.indicated_temperature_k':round(self.temperature_k-273.15,1)+273.15,
          'lid.position_m':self.lid_m,'method.mode':self.mode,'method.duration_s':self.duration_s,'method.basis':self.basis,
          'cycle.phase':self.phase,'cycle.elapsed_s':self.elapsed_s,'balance.indicated_kg':round(m/1e-7)*1e-7,
          'sample.initial_kg':self.residue_kg+sum(self.initial_species.values()),'sample.remaining_kg':m,
          'sample.evaporated_kg':sum(self.evaporated.values())}

    def read(self,visibility='agent'):
        return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('evaluator','debug')})

    def describe(self): return self.session().describe(instance_id=self.instance_id)

    def debug_snapshot(self):
        return dict(state=self.read('evaluator'), species=copy.deepcopy(self.remaining), evaporated=copy.deepcopy(self.evaporated))

    def _measure(self):
        v=self._values(); initial=v['sample.initial_kg']; m=v['balance.indicated_kg']
        loss=max(0.,min(1.,(initial-m)/initial)) if initial else 0.
        ratio=loss if self.basis=='wet' else (loss*initial/m if m>0 else None)
        return dict(phase=self.phase,mass_kg=m,loss_fraction=loss,reported_ratio=ratio,basis=self.basis,
          curve=copy.deepcopy(self.curve),template_source=TEMPLATE_REL,evidence='synthetic',interpretation='loss_on_drying_not_water_specific')

    def invoke(self,capability,**a):
        if capability not in CAPS: raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability][0],a)
        if capability in ('method.configure','thermal.set_temperature','sample.configure') and self.phase=='running': raise ActionRejected('BUSY')
        if capability=='thermal.set_temperature':
            if not math.isclose(a['temperature_k']-273.15,round(a['temperature_k']-273.15),abs_tol=1e-7): raise ActionRejected('INVALID_ARGUMENT')
            self.target_k=a['temperature_k']; result=dict(a)
        elif capability=='power.set':
            self.powered=a['enabled']
            if not self.powered and self.phase=='running': self.phase='cancelled'
            result=dict(a)
        elif capability=='method.configure':
            self.mode=a['mode']; self.duration_s=a['duration_s']; self.basis=a['basis']; result=dict(a)
        elif capability=='lid.set_position':
            if not self.powered or (self.phase=='running' and a['position_m']>0): raise ActionRejected('PRECONDITION_FAILED')
            self.lid_m=a['position_m']; result=dict(a)
        elif capability=='sample.configure':
            species=a['species_kg']; residue=a['residue_kg']
            if self.lid_m<.1: raise ActionRejected('PRECONDITION_FAILED')
            if not set(species)<=self.templates.keys() or not .0001<=residue+sum(species.values())<=.201: raise ActionRejected('INVALID_ARGUMENT')
            self.initial_species=dict(species); self.remaining=dict(species); self.evaporated={k:0. for k in species}
            self.residue_kg=residue; self.curve=[]; self.phase='idle'; self.elapsed_s=0.; self.operation_id=None
            result={'configured':True}
        elif capability=='acquisition.start':
            if self.phase=='running': raise ActionRejected('BUSY')
            if not self.powered or self.lid_m>1e-6 or self._values()['sample.remaining_kg']<.0001 or self.phase!='idle': raise ActionRejected('PRECONDITION_FAILED')
            self._operation_count+=1; self.operation_id=f'{self.instance_id}:drying:{self._operation_count}'
            self.phase='running'; self.elapsed_s=0.; self.curve=[]; self._record()
            result={'phase':self.phase,'operation_id':self.operation_id}
        elif capability=='acquisition.stop':
            if self.phase=='running': self.phase='cancelled'
            result={'phase':self.phase}
        else: return self._measure()
        self.revision+=1
        return copy.deepcopy(result)

    def _record(self):
        v=self._values(); initial=v['sample.initial_kg']; m=v['balance.indicated_kg']
        self.curve.append(dict(time_s=self.elapsed_s,mass_kg=m,loss_fraction=max(0,min(1,(initial-m)/initial)) if initial else 0.))

    def step(self,dt_s):
        if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<=0: raise ValueError('dt_s must be finite and positive')
        left=float(dt_s)
        while left>1e-10:
            active=self.phase=='running'; h=min(left,1.)
            if active: h=min(h,max(0.,self.duration_s-self.elapsed_s))
            if h<=1e-10: self.phase='succeeded'; continue
            target=self.target_k if active else 298.15
            tau={'standard':45.,'rapid':20.,'gentle':100.,'steps':45.}[self.mode]
            if active and self.mode=='steps' and self.elapsed_s<self.duration_s/2: target=(298.15+self.target_k)/2
            self.temperature_k=target+(self.temperature_k-target)*math.exp(-h/tau)
            if active:
                for sid,m in self.remaining.items():
                    k=.002*math.exp(max(-15,min(8,(self.temperature_k-self.templates[sid]['value'])/30)))
                    rem=m*math.exp(-k*h); self.remaining[sid]=rem; self.evaporated[sid]+=m-rem
                self.elapsed_s+=h; self._record()
                if self.elapsed_s>=self.duration_s-1e-9: self.phase='succeeded'
            self.time_s+=h; left-=h
        self.revision+=1
        return self.read('evaluator')

class MtHx204Session:
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
        meta=export_mt_hx204_contract_metadata()
        return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',
          capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},
          state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})

    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id); h=self._host; vals=h.read(self._role); ids=list(vals) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids): raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in vals for k in ids): raise ActionRejected('ACCESS_DENIED')
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},
          observations={k:dict(value=copy.deepcopy(vals[k]),unit=SLOTS[k][1],quality='estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=h.time_s,sample_window_s=None) for k in ids})

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

def export_mt_hx204_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'MtHx204Session.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key=='acquisition.start'; pre=[]
        conditions={
         'acquisition.start':('供电、盖关闭、有样品、已重新初始化样品',['power.enabled','lid.position_m','sample.remaining_kg','cycle.phase'],'powered and lid_m<=1e-6 and remaining>=0.0001 and phase==idle'),
         'sample.configure':('盖打开且无采集，样品0.1–201g且模板存在',['lid.position_m','cycle.phase'],'lid_m>=0.1 and phase!=running; validate species/mass before mutation'),
         'method.configure':('采集时锁定方法',['cycle.phase'],'phase!=running'),
         'thermal.set_temperature':('采集时锁定温度；摄氏度步进1度',['cycle.phase'],'phase!=running and integral_celsius'),
         'lid.set_position':('供电且采集中不得开盖',['power.enabled','cycle.phase'],'powered and (position_m==0 or phase!=running)')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.mt_hx204.MtHx204Session.invoke',invocation=f'session.invoke(instance_id="hx204_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；取消保留部分失重' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电不回滚已蒸发质量。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    mechanism=dict(component_ids=['heater','temperature_sensor','sample_pan','balance_sensor'],
      state_ids=['thermal.temperature_k','sample.remaining_kg','sample.evaporated_kg','balance.indicated_kg'],
      meaning_zh='公开沸点驱动合成失重，逐物质质量守恒，无水选择性',implementation='lumped',
      equations=['T_next=T_target+(T-T_target)*exp(-dt/tau)','k_i=0.002*exp(clip((T-Tboil_i)/30,-15,8)); m_next=m_i*exp(-k_i*dt)','m_initial_i=m_remaining_i+m_evaporated_i; displayed_mass=round(m/1e-7)*1e-7'],
      parameters={'standard_tau_s':dict(value=45.,unit='s',meaning_zh='标准升温时间常数，未标定',evidence='assumption',source_ids=['simulation_assumption'],uncertainty=None)},
      source_ids=['nist_templates','simulation_assumption'],limitations=['未求解潜热能量守恒、扩散/活度/分解、厂家自动终点和真实传感器噪声。'])
    _ann.annotate_export('mt_hx204',caps,slots,{'drying':mechanism},inputs={})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'drying':mechanism})

"""CHEM-003 DMA 5002. Literature density plus explicitly synthetic off-reference response."""
from __future__ import annotations
import copy
from asset_runtime import _annotations as _ann
import json
import math
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected, number, obj, validate, BOOL, TEXT, PHASE
ROOT=Path(__file__).resolve().parents[2]
PROTOCOL_VERSION='1.0.0'
ASSET_TYPE_ID='instrument.antonpaar_dma5002'
CATALOG_ID='CHEM-003'
TEMPLATE_REL='assets/reactions/antonpaar_dma5002_density_library/templates.json'
NULL_RHO={'anyOf':[number('kg/m^3',0,3000),{'type':'null'}]}
RESULT=obj({'density_kg_m3':NULL_RHO,'temperature_k':number('K',273.15,373.15),'phase':PHASE,
 'filled':BOOL,'bubble_warning':BOOL,'quality':{'type':'string','enum':['unavailable','estimated','suspect']},
 'evidence':{'type':'string','enum':['derived','synthetic','unavailable']},'template_source':TEXT})
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','display_digits'],'设定供电；断电取消过程','device_command','agent',{'enabled':True},['power.enabled','cycle.phase']),
 'thermal.set_temperature':(obj({'temperature_k':number('K',273.15,373.15)}),obj({'temperature_k':number('K',273.15,373.15)}),['peltier','temperature_sensor','display_digits'],'目标温度；实际温度仅step变化','device_command','agent',{'temperature_k':293.15},['thermal.target_k']),
 'method.configure':(obj({'mode':{'type':'string','enum':['density']}}),obj({'mode':{'type':'string','enum':['density']}}),['display_digits'],'仅实现原始密度法；浓度换算方法未实现','configuration','agent',{'mode':'density'},['method.mode']),
 'sample.configure':(obj({'substance_id':TEXT,'volume_m3':number('m^3',1e-6,5e-6),'bubble_fraction':number('1',0,.2),'contamination_fraction':number('1',0,.2)}),obj({'configured':BOOL}),['sample_inlet','measuring_tube'],'受信任宿主初始化液体，不是机器人注射','configuration','evaluator',{'substance_id':'ethanol','volume_m3':1e-6,'bubble_fraction':0.,'contamination_fraction':0.},['sample.cell_kg','sample.total_input_kg','fill.detected','bubble.detected','density.indicated_kg_m3']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['measuring_tube','excitation','temperature_sensor','display_digits'],'恒温且填充后启动典型30秒测量','device_command','agent',{},['cycle.phase','cycle.elapsed_s']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['display_digits'],'取消测量或清洗，保留部分物料转移','device_command','agent',{},['cycle.phase']),
 'cleaning.start':(obj({'volume_m3':number('m^3',1e-6,1e-4)}),obj({'phase':PHASE,'operation_id':TEXT}),['sample_inlet','sample_outlet','measuring_tube'],'简化60秒清洗；清洗液密度1000kg/m³为代理','device_command','agent',{'volume_m3':1e-5},['cycle.phase','cycle.elapsed_s','cleaner.input_kg','cleaner.remaining_kg']),
 'density.measure':(obj({}),RESULT,['measuring_tube','display_digits'],'只读最近测量；不推进时间','query','agent',{},[]),
 'calibration.check':(obj({'reference_density_kg_m3':number('kg/m^3',0,3000),'reference_temperature_k':number('K',273.15,373.15)}),obj({'deviation_kg_m3':number('kg/m^3'),'adjusted':{'type':'boolean','const':False}}),['measuring_tube','display_digits'],'对照用户标准值计算偏差；不冒充厂家校准调整','query','agent',{'reference_density_kg_m3':789.3,'reference_temperature_k':293.15},[]),
}
SLOTS={
 'power.enabled':(BOOL,'1','setpoint','agent',['power_inlet'],('power.set','enabled')),
 'thermal.target_k':(number('K',273.15,373.15),'K','setpoint','agent',['peltier'],('thermal.set_temperature','temperature_k')),
 'thermal.temperature_k':(number('K',273.15,373.15),'K','process_truth','evaluator',['measuring_tube'],None),
 'thermal.indicated_temperature_k':(number('K',273.15,373.15),'K','derived','agent',['temperature_sensor','display_digits'],None),
 'method.mode':({'type':'string','const':'density'},'1','setpoint','agent',['display_digits'],None),
 'cycle.phase':(PHASE,'1','derived','agent',['display_digits'],None),
 'cycle.elapsed_s':(number('s',0),'s','derived','agent',['display_digits'],None),
 'cycle.operation':({'type':'string','enum':['none','measurement','cleaning']},'1','derived','agent',['display_digits'],None),
 'fill.detected':(BOOL,'1','derived','agent',['fill_sensor'],None),
 'bubble.detected':(BOOL,'1','derived','agent',['camera'],None),
 'density.indicated_kg_m3':(NULL_RHO,'kg/m^3','derived','agent',['measuring_tube','display_digits'],None),
 'sample.cell_kg':(number('kg',0),'kg','process_truth','evaluator',['measuring_tube'],None),
 'sample.waste_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_outlet'],None),
 'sample.total_input_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_inlet'],None),
 'cleaner.input_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_inlet'],None),
 'cleaner.remaining_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_inlet'],None),
 'cleaner.waste_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_outlet'],None),
}

class AntonpaarDma5002Runtime:
    def __init__(self,instance_id='dma5002_01'):
        self.instance_id=instance_id; self.time_s=0.; self.revision=0
        self.powered=False; self.target_k=293.15; self.temperature_k=293.15; self.mode='density'
        self.phase='idle'; self.operation='none'; self.elapsed_s=0.; self.operation_id=None; self._operation_count=0
        self.sid=None; self.volume_m3=0.; self.bubbles=0.; self.contamination=0.
        self.cell_kg=0.; self.waste_kg=0.; self.input_kg=0.
        self.cleaner_input_kg=0.; self.cleaner_remaining_kg=0.; self.cleaner_waste_kg=0.; self._clean_start_kg=0.
        self.result=None
        self.templates=json.loads((ROOT/TEMPLATE_REL).read_text())['substances']
        self._principals={'__public_agent__':'agent'}; self._requests={}; self._owners={}
    def register_principal(self,principal_id,visibility='agent'):
        if visibility not in ('agent','evaluator','debug'): raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals: raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id='__public_agent__'):
        if principal_id not in self._principals: raise ActionRejected('ACCESS_DENIED')
        return AntonpaarDma5002Session(self,principal_id)
    def describe(self): return self.session().describe(instance_id=self.instance_id)
    def _values(self):
        return {'power.enabled':self.powered,'thermal.target_k':self.target_k,'thermal.temperature_k':self.temperature_k,
          'thermal.indicated_temperature_k':round(self.temperature_k-273.15,3)+273.15,'method.mode':self.mode,
          'cycle.phase':self.phase,'cycle.elapsed_s':self.elapsed_s,'cycle.operation':self.operation,
          'fill.detected':self.cell_kg>1e-12 and self.volume_m3>=1e-6,'bubble.detected':self.bubbles>.002,
          'density.indicated_kg_m3':self.result['density_kg_m3'] if self.result else None,
          'sample.cell_kg':self.cell_kg,'sample.waste_kg':self.waste_kg,'sample.total_input_kg':self.input_kg,
          'cleaner.input_kg':self.cleaner_input_kg,'cleaner.remaining_kg':self.cleaner_remaining_kg,'cleaner.waste_kg':self.cleaner_waste_kg}
    def read(self,visibility='agent'):
        return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('evaluator','debug')})
    def debug_snapshot(self): return dict(state=self.read('evaluator'),substance_id=self.sid,bubbles=self.bubbles,contamination=self.contamination)
    def _measure(self):
        if self.result: return copy.deepcopy(self.result)
        v=self._values()
        return dict(density_kg_m3=None,temperature_k=v['thermal.indicated_temperature_k'],phase=self.phase,
            filled=v['fill.detected'],bubble_warning=v['bubble.detected'],quality='unavailable',evidence='unavailable',template_source=TEMPLATE_REL)
    def _density(self):
        t=self.templates[self.sid]
        # beta is an uncalibrated engineering placeholder, not measured expansion.
        rho=t['value']/(1+.001*(self.temperature_k-t['temperature_k']))
        rho=rho*(1-self.bubbles)+5*self.contamination
        return min(3000.,max(0.,round(rho/.005)*.005))
    def _start(self,kind):
        self._operation_count+=1; self.operation_id=f'{self.instance_id}:{kind}:{self._operation_count}'
        self.phase='running'; self.operation=kind; self.elapsed_s=0.; self.result=None
        return dict(phase=self.phase,operation_id=self.operation_id)
    def invoke(self,capability,**a):
        if capability not in CAPS: raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability][0],a)
        if capability in ('thermal.set_temperature','method.configure','sample.configure','acquisition.start','cleaning.start') and self.phase=='running': raise ActionRejected('BUSY')
        if capability=='power.set':
            self.powered=a['enabled']
            if not self.powered and self.phase=='running': self.phase='cancelled'
            r=dict(a)
        elif capability=='thermal.set_temperature':
            self.target_k=a['temperature_k']; self.result=None; r=dict(a)
        elif capability=='method.configure': self.mode=a['mode']; r=dict(a)
        elif capability=='sample.configure':
            sid=a['substance_id']
            if not self.powered or self.cell_kg>1e-12 or self.cleaner_remaining_kg>1e-12: raise ActionRejected('PRECONDITION_FAILED')
            if sid not in self.templates: raise ActionRejected('INVALID_ARGUMENT')
            t=self.templates[sid]
            if not t.get('runtime_eligible',False): raise ActionRejected('PRECONDITION_FAILED')
            if t['phase']=='unresolved' and abs(self.temperature_k-t['temperature_k'])>.001: raise ActionRejected('PRECONDITION_FAILED')
            if not (t.get('melting_point_k') or 0)<self.temperature_k<(t.get('boiling_point_k') or 10000): raise ActionRejected('PRECONDITION_FAILED')
            self.sid=sid; self.volume_m3=a['volume_m3']; self.bubbles=a['bubble_fraction']; self.contamination=a['contamination_fraction']
            self.cell_kg=t['value']/(1+.001*(self.temperature_k-t['temperature_k']))*self.volume_m3*(1-self.bubbles)
            self.input_kg+=self.cell_kg; self.result=None; self.phase='idle'; self.operation='none'; self.operation_id=None; self.elapsed_s=0.
            r={'configured':True}
        elif capability=='acquisition.start':
            if not self.powered or not self._values()['fill.detected'] or abs(self.temperature_k-self.target_k)>.05: raise ActionRejected('PRECONDITION_FAILED')
            t=self.templates[self.sid]
            if t['phase']=='unresolved' and abs(self.target_k-t['temperature_k'])>.001: raise ActionRejected('PRECONDITION_FAILED')
            if not (t.get('melting_point_k') or 0)<self.target_k<(t.get('boiling_point_k') or 10000): raise ActionRejected('PRECONDITION_FAILED')
            r=self._start('measurement')
        elif capability=='cleaning.start':
            if not self.powered: raise ActionRejected('PRECONDITION_FAILED')
            mass=a['volume_m3']*1000.; self.cleaner_input_kg+=mass; self.cleaner_remaining_kg+=mass; self._clean_start_kg=self.cleaner_remaining_kg
            r=self._start('cleaning')
        elif capability=='acquisition.stop':
            if self.phase=='running': self.phase='cancelled'
            r={'phase':self.phase}
        elif capability=='calibration.check':
            if not self.result or abs(a['reference_temperature_k']-self.result['temperature_k'])>.05: raise ActionRejected('PRECONDITION_FAILED')
            return dict(deviation_kg_m3=self.result['density_kg_m3']-a['reference_density_kg_m3'],adjusted=False)
        else: return self._measure()
        self.revision+=1; return copy.deepcopy(r)
    def step(self,dt_s):
        if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<=0: raise ValueError('finite positive dt_s required')
        left=float(dt_s)
        while left>1e-10:
            active=self.phase=='running'; duration=30. if self.operation=='measurement' else 60.
            h=min(left,1.,duration-self.elapsed_s) if active else min(left,1.)
            target=self.target_k if self.powered else 293.15
            self.temperature_k=target+(self.temperature_k-target)*math.exp(-h/25.)
            if active:
                self.elapsed_s+=h
                if self.operation=='cleaning':
                    transfer=self.cell_kg*(1-math.exp(-h/10.)); self.cell_kg-=transfer; self.waste_kg+=transfer
                    self.volume_m3*=math.exp(-h/10.)
                    cleaner=min(self.cleaner_remaining_kg,self._clean_start_kg*h/60.); self.cleaner_remaining_kg-=cleaner; self.cleaner_waste_kg+=cleaner
                    self.contamination*=math.exp(-h/15.)
                if self.elapsed_s>=duration-1e-9:
                    self.phase='succeeded'
                    if self.operation=='cleaning':
                        self.waste_kg+=self.cell_kg; self.cell_kg=0.; self.volume_m3=0.; self.sid=None; self.bubbles=0.
                        self.cleaner_waste_kg+=self.cleaner_remaining_kg; self.cleaner_remaining_kg=0.
                    else:
                        t=self.templates[self.sid]; v=self._values()
                        self.result=dict(density_kg_m3=self._density(),temperature_k=v['thermal.indicated_temperature_k'],phase='succeeded',filled=True,
                            bubble_warning=v['bubble.detected'],quality='suspect' if self.bubbles>.002 or self.contamination>.002 else 'estimated',
                            evidence='derived' if abs(self.temperature_k-t['temperature_k'])<1e-6 and self.bubbles==0 and self.contamination==0 else 'synthetic',template_source=TEMPLATE_REL)
            left-=h; self.time_s+=h
        self.revision+=1; return self.read('evaluator')

class AntonpaarDma5002Session:
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
        meta=export_antonpaar_dma5002_contract_metadata()
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

def export_antonpaar_dma5002_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'AntonpaarDma5002Session.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key in ('acquisition.start','cleaning.start'); pre=[]
        conditions={
         'acquisition.start':('供电、液体填充、温差小于0.05K且无其他过程',['power.enabled','fill.detected','cycle.phase'],'powered and cell_kg>0 and abs(T-target)<=0.05 and not running'),
         'sample.configure':('宿主装样需空池、无剩余清洗液、已知相态允许液体',['power.enabled','sample.cell_kg','cleaner.remaining_kg'],'powered and cell_kg=0 and cleaner_remaining_kg=0; valid template and phase'),
         'cleaning.start':('供电且无运行中过程',['power.enabled','cycle.phase'],'powered and not running'),
         'thermal.set_temperature':('过程运行时温度锁定',['cycle.phase'],'not running'),
         'method.configure':('过程运行时方法锁定',['cycle.phase'],'not running'),
         'calibration.check':('有已完成测量且标准温度匹配',['density.indicated_kg_m3'],'result exists and abs(reference_temperature-result_temperature)<=0.05')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.antonpaar_dma5002.AntonpaarDma5002Session.invoke',invocation=f'session.invoke(instance_id="dma5002_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；取消保留部分清洗转移' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电不回滚已排入废液的质量。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    mechanism=dict(component_ids=['measuring_tube','temperature_sensor','peltier','sample_inlet','sample_outlet'],
      state_ids=['thermal.temperature_k','sample.cell_kg','sample.waste_kg','sample.total_input_kg','density.indicated_kg_m3'],
      meaning_zh='公开单点液体密度与明确合成的温度/气泡/污染响应；清洗物料守恒',implementation='lumped',
      equations=['T_next=T_target+(T-T_target)*exp(-dt/25)',
      'rho=rho_ref/(1+0.001*(T-T_ref)); rho_display=quantize_0.005(rho*(1-bubble_fraction)+5*contamination_fraction)',
      'sample_input=sample_cell+sample_waste; cleaner_input=cleaner_remaining+cleaner_waste'],
      parameters={'temperature_tau_s':dict(value=25.,unit='s',meaning_zh='未标定集中参数热时间常数',evidence='assumption',source_ids=['simulation_assumption'],uncertainty=None),
      'thermal_expansion_per_k':dict(value=.001,unit='1/K',meaning_zh='所有物质共用的合成热膨胀占位；非实测系数',evidence='assumption',source_ids=['simulation_assumption'],uncertainty=None)},
      source_ids=['density_templates','simulation_assumption'],limitations=['未实现真实脉冲激励/黏度修正、厂家校准、压力、U-View图像或真实精度噪声。'])
    _ann.annotate_export('antonpaar_dma5002',caps,slots,{'density_measurement':mechanism},inputs={})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'density_measurement':mechanism})

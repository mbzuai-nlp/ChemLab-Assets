"""CHEM-004: measured nD references, synthetic thermal/mixture optics, explicit calibration."""
from __future__ import annotations
import copy
from asset_runtime import _annotations as _ann
import json
import math
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT,PHASE
ROOT=Path(__file__).resolve().parents[2]
ASSET_TYPE_ID='instrument.antonpaar_abbematadvanced'; CATALOG_ID='CHEM-004'; PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/antonpaar_abbematadvanced_refractive_library/templates.json'
MODE={'type':'string','enum':['refractive_index','calibrated_concentration']}
ND=number('1',1.26,1.72); NULL_ND={'anyOf':[ND,{'type':'null'}]}
FRACTION=number('1',0,1); NULL_FRACTION={'anyOf':[FRACTION,{'type':'null'}]}
POINT=obj({'index':ND,'mass_fraction':FRACTION})
RESULT=obj({'index':NULL_ND,'temperature_k':number('K',277.15,378.15),'wavelength_m':number('m',589.3e-9,589.3e-9),
 'mass_fraction':NULL_FRACTION,'phase':PHASE,'quality':{'type':'string','enum':['estimated','suspect','unavailable']},
 'evidence':{'type':'string','enum':['derived','synthetic','unavailable']},'concentration_evidence':{'type':'string','enum':['user_supplied_calibration','unavailable']},'template_source':TEXT})
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','display_digits'],'供电设定；断电取消进行中操作','device_command','agent',{'enabled':True},['power.enabled','cycle.phase']),
 'thermal.set_temperature':(obj({'temperature_k':number('K',277.15,378.15)}),obj({'temperature_k':number('K',277.15,378.15)}),['peltier','temperature_sensor'],'写温控目标，不隐式推进时间','device_command','agent',{'temperature_k':293.15},['thermal.target_k']),
 'lid.set_angle':(obj({'angle_rad':number('rad',0,math.pi/2)}),obj({'angle_rad':number('rad',0,math.pi/2)}),['lid','lid_handle'],'被动盖宿主位置设置，采集时锁定；不是机器人技能','device_command','agent',{'angle_rad':math.pi/2},['lid.angle_rad']),
 'sample.configure':(obj({'volume_fractions':{'type':'object','additionalProperties':FRACTION},'volume_m3':number('m^3',70e-9,2e-6),'matrix_id':TEXT,'contamination_fraction':number('1',0,.2)}),obj({'configured':BOOL}),['sample_well','sapphire_prism'],'受信任宿主初始化液体账本，体积在装样参考温度定义','configuration','evaluator',{'volume_fractions':{'ethanol':1.},'volume_m3':100e-9,'matrix_id':'ethanol','contamination_fraction':0.},['sample.remaining_m3','sample.input_m3','sample.filled','cycle.phase','optical.indicated_index']),
 'method.configure':(obj({'mode':MODE}),obj({'mode':MODE}),['display_digits'],'仅nD或专属标定浓度；不自动套Brix','configuration','agent',{'mode':'refractive_index'},['method.mode']),
 'calibration.configure':(obj({'matrix_id':TEXT,'temperature_k':number('K',277.15,378.15),'points':{'type':'array','items':POINT,'minItems':2,'maxItems':32},'source':TEXT}),obj({'configured':BOOL}),['display_digits','sapphire_prism'],'用户提供的特定体系标定表；要求nD与浓度严格单调，不宣称厂家校准','configuration','agent',{'matrix_id':'ethanol','temperature_k':293.15,'points':[{'index':1.35,'mass_fraction':0.},{'index':1.38,'mass_fraction':1.}],'source':'synthetic demonstration only'},['calibration.configured']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['sapphire_prism','led_source','detector','display_digits'],'闭盖、填样、恒温后进行10s光学读数代理','device_command','agent',{},['cycle.phase','cycle.elapsed_s']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['display_digits'],'取消测量或清洗，保留实际已发生的体积转移','device_command','agent',{},['cycle.phase']),
 'cleaning.start':(obj({'volume_m3':number('m^3',70e-9,1e-4)}),obj({'phase':PHASE,'operation_id':TEXT}),['sample_well','sapphire_prism'],'开盖进行30s工程清洗；清洗液和样品参考体积分别守恒','device_command','agent',{'volume_m3':1e-6},['cycle.phase','cycle.elapsed_s','cleaner.input_m3','cleaner.remaining_m3']),
 'refractometry.measure':(obj({}),RESULT,['sapphire_prism','detector','display_digits'],'纯读最近nD、温度及有适用标定时的浓度','query','agent',{},[]),
}
SLOTS={
 'power.enabled':(BOOL,'1','setpoint','agent',['power_inlet'],('power.set','enabled')),
 'thermal.target_k':(number('K',277.15,378.15),'K','setpoint','agent',['peltier'],('thermal.set_temperature','temperature_k')),
 'thermal.temperature_k':(number('K',277.15,378.15),'K','process_truth','evaluator',['sapphire_prism'],None),
 'thermal.indicated_temperature_k':(number('K',277.15,378.15),'K','derived','agent',['temperature_sensor','display_digits'],None),
 'lid.angle_rad':(number('rad',0,math.pi/2),'rad','scene','agent',['lid'],('lid.set_angle','angle_rad')),
 'method.mode':(MODE,'1','setpoint','agent',['display_digits'],None),
 'calibration.configured':(BOOL,'1','derived','agent',['display_digits'],None),
 'cycle.phase':(PHASE,'1','derived','agent',['display_digits'],None),
 'cycle.elapsed_s':(number('s',0),'s','derived','agent',['display_digits'],None),
 'cycle.operation':({'type':'string','enum':['none','measurement','cleaning']},'1','derived','agent',['display_digits'],None),
 'sample.filled':(BOOL,'1','derived','agent',['sample_well'],None),
 'optical.indicated_index':(NULL_ND,'1','derived','agent',['detector','display_digits'],None),
 'concentration.indicated_mass_fraction':(NULL_FRACTION,'1','derived','agent',['display_digits'],None),
 'sample.remaining_m3':(number('m^3',0),'m^3','process_truth','evaluator',['sample_well'],None),
 'sample.input_m3':(number('m^3',0),'m^3','process_truth','evaluator',['sample_well'],None),
 'sample.waste_m3':(number('m^3',0),'m^3','process_truth','evaluator',['sample_well'],None),
 'cleaner.input_m3':(number('m^3',0),'m^3','process_truth','evaluator',['sample_well'],None),
 'cleaner.remaining_m3':(number('m^3',0),'m^3','process_truth','evaluator',['sample_well'],None),
 'cleaner.waste_m3':(number('m^3',0),'m^3','process_truth','evaluator',['sample_well'],None),
}
def validate(schema,value):
    """Explicit supported JSON subset; finite numeric values, strict bool/array/const handling."""
    if 'anyOf' in schema:
        for sub in schema['anyOf']:
            try: validate(sub,value); return
            except ActionRejected: pass
        raise ActionRejected('INVALID_ARGUMENT')
    typ=schema.get('type')
    if typ=='number':
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not schema.get('minimum',-math.inf)<=value<=schema.get('maximum',math.inf): raise ActionRejected('INVALID_ARGUMENT')
    elif typ=='boolean':
        if type(value) is not bool: raise ActionRejected('INVALID_ARGUMENT')
    elif typ=='null':
        if value is not None: raise ActionRejected('INVALID_ARGUMENT')
    elif typ=='string':
        if not isinstance(value,str): raise ActionRejected('INVALID_ARGUMENT')
    elif typ=='array':
        if not isinstance(value,list) or not schema.get('minItems',0)<=len(value)<=schema.get('maxItems',math.inf): raise ActionRejected('INVALID_ARGUMENT')
        for v in value: validate(schema['items'],v)
    elif typ=='object':
        if not isinstance(value,dict) or not set(schema.get('required',[]))<=value.keys(): raise ActionRejected('INVALID_ARGUMENT')
        for k,v in value.items():
            if not isinstance(k,str): raise ActionRejected('INVALID_ARGUMENT')
            sub=schema.get('properties',{}).get(k,schema.get('additionalProperties',True))
            if sub is False: raise ActionRejected('INVALID_ARGUMENT')
            if isinstance(sub,dict): validate(sub,v)
    if 'enum' in schema and value not in schema['enum']: raise ActionRejected('INVALID_ARGUMENT')
    if 'const' in schema and value!=schema['const']: raise ActionRejected('INVALID_ARGUMENT')

class AntonpaarAbbematadvancedRuntime:
    def __init__(self,instance_id='abbemat5201_01'):
        self.instance_id=instance_id; self.time_s=0.; self.revision=0; self.powered=False
        self.target_k=293.15; self.temperature_k=293.15; self.lid_angle=0.; self.mode='refractive_index'; self.calibration=None
        self.phase='idle'; self.operation='none'; self.elapsed_s=0.; self.operation_id=None; self._operation_count=0
        self.remaining={}; self.initial_species={}; self.waste_species={}; self.matrix_id=None; self.contamination=0.
        self.cleaner_input_m3=0.; self.cleaner_remaining_m3=0.; self.cleaner_waste_m3=0.; self._clean_start_m3=0.
        self.result=None; self.templates=json.loads((ROOT/TEMPLATE_REL).read_text())['substances']
        self._principals={'__public_agent__':'agent'}; self._requests={}; self._owners={}
    def register_principal(self,principal_id,visibility='agent'):
        if visibility not in ('agent','evaluator','debug'): raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals: raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id='__public_agent__'):
        if principal_id not in self._principals: raise ActionRejected('ACCESS_DENIED')
        return AntonpaarAbbematadvancedSession(self,principal_id)
    def describe(self): return self.session().describe(instance_id=self.instance_id)
    def _values(self):
        return {'power.enabled':self.powered,'thermal.target_k':self.target_k,'thermal.temperature_k':self.temperature_k,
          'thermal.indicated_temperature_k':round(self.temperature_k-273.15,3)+273.15,'lid.angle_rad':self.lid_angle,'method.mode':self.mode,
          'calibration.configured':self.calibration is not None,'cycle.phase':self.phase,'cycle.elapsed_s':self.elapsed_s,'cycle.operation':self.operation,
          'sample.filled':sum(self.remaining.values())>=70e-9-1e-15,
          'optical.indicated_index':self.result['index'] if self.result else None,
          'concentration.indicated_mass_fraction':self.result['mass_fraction'] if self.result else None,
          'sample.remaining_m3':sum(self.remaining.values()),'sample.input_m3':sum(self.initial_species.values()),'sample.waste_m3':sum(self.waste_species.values()),
          'cleaner.input_m3':self.cleaner_input_m3,'cleaner.remaining_m3':self.cleaner_remaining_m3,'cleaner.waste_m3':self.cleaner_waste_m3}
    def read(self,visibility='agent'):
        return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('evaluator','debug')})
    def debug_snapshot(self): return dict(state=self.read('evaluator'),remaining=copy.deepcopy(self.remaining),input=copy.deepcopy(self.initial_species),waste=copy.deepcopy(self.waste_species),matrix_id=self.matrix_id)
    def _measure(self):
        if self.result: return copy.deepcopy(self.result)
        return dict(index=None,temperature_k=self._values()['thermal.indicated_temperature_k'],wavelength_m=589.3e-9,mass_fraction=None,phase=self.phase,
            quality='unavailable',evidence='unavailable',concentration_evidence='unavailable',template_source=TEMPLATE_REL)
    def _index(self):
        total=sum(self.remaining.values()); lorentz=0.
        for sid,v in self.remaining.items():
            t=self.templates[sid]; n=t['value']-.0004*(self.temperature_k-t['temperature_k'])
            lorentz+=v/total*(n*n-1)/(n*n+2)
        return round(math.sqrt((1+2*lorentz)/(1-lorentz))+.001*self.contamination,6)
    def _concentration(self,n):
        c=self.calibration
        if self.mode!='calibrated_concentration' or not c or c['matrix_id']!=self.matrix_id or abs(c['temperature_k']-self.temperature_k)>.05: return None
        p=c['points']
        if not p[0]['index']<=n<=p[-1]['index']: return None
        for a,b in zip(p,p[1:]):
            if a['index']<=n<=b['index']: return a['mass_fraction']+(b['mass_fraction']-a['mass_fraction'])*(n-a['index'])/(b['index']-a['index'])
    def _check_phase(self,sid,temp):
        row=self.templates[sid]
        if not row['runtime_eligible']: raise ActionRejected('PRECONDITION_FAILED')
        if not (row.get('melting_point_k') or 0)<temp<(row.get('boiling_point_k') or 10000): raise ActionRejected('PRECONDITION_FAILED')
        if (row.get('melting_point_k') is None or row.get('boiling_point_k') is None) and abs(temp-row['temperature_k'])>.001: raise ActionRejected('PRECONDITION_FAILED')
    def _start(self,kind):
        self._operation_count+=1; self.operation_id=f'{self.instance_id}:{kind}:{self._operation_count}'
        self.phase='running'; self.operation=kind; self.elapsed_s=0.; self.result=None
        return dict(phase=self.phase,operation_id=self.operation_id)
    def invoke(self,capability,**a):
        if capability not in CAPS: raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability][0],a)
        if capability not in ('power.set','acquisition.stop','refractometry.measure') and self.phase=='running': raise ActionRejected('BUSY')
        if capability=='power.set':
            self.powered=a['enabled']
            if not self.powered and self.phase=='running': self.phase='cancelled'
            r=dict(a)
        elif capability=='thermal.set_temperature': self.target_k=a['temperature_k']; self.result=None; r=dict(a)
        elif capability=='lid.set_angle': self.lid_angle=a['angle_rad']; r=dict(a)
        elif capability=='method.configure': self.mode=a['mode']; self.result=None; r=dict(a)
        elif capability=='calibration.configure':
            p=a['points']; delta=[b['mass_fraction']-x['mass_fraction'] for x,b in zip(p,p[1:])]
            if not a['matrix_id'].strip() or not a['source'].strip() or any(b['index']<=x['index'] for x,b in zip(p,p[1:])) or not (all(d>0 for d in delta) or all(d<0 for d in delta)): raise ActionRejected('INVALID_ARGUMENT')
            self.calibration=copy.deepcopy(a); self.result=None; r={'configured':True}
        elif capability=='sample.configure':
            f=a['volume_fractions']
            if not f or not set(f)<=self.templates.keys() or not math.isclose(sum(f.values()),1.,abs_tol=1e-9) or not a['matrix_id'].strip() or any(x<=0 for x in f.values()): raise ActionRejected('INVALID_ARGUMENT')
            if not self.powered or self.lid_angle<1.4 or sum(self.remaining.values())>1e-15 or self.cleaner_remaining_m3>1e-15: raise ActionRejected('PRECONDITION_FAILED')
            for sid in f: self._check_phase(sid,self.temperature_k)
            self.remaining={sid:fraction*a['volume_m3'] for sid,fraction in f.items()}
            for sid,v in self.remaining.items(): self.initial_species[sid]=self.initial_species.get(sid,0.)+v; self.waste_species.setdefault(sid,0.)
            self.matrix_id=a['matrix_id']; self.contamination=a['contamination_fraction']; self.result=None
            self.phase='idle'; self.operation='none'; self.elapsed_s=0.; self.operation_id=None; r={'configured':True}
        elif capability=='acquisition.start':
            if not self.powered or self.lid_angle>1e-6 or not self._values()['sample.filled'] or abs(self.temperature_k-self.target_k)>.05: raise ActionRejected('PRECONDITION_FAILED')
            for sid in self.remaining: self._check_phase(sid,self.target_k)
            n=self._index()
            if not 1.26<=n<=1.72 or (self.mode=='calibrated_concentration' and self._concentration(n) is None): raise ActionRejected('PRECONDITION_FAILED')
            r=self._start('measurement')
        elif capability=='cleaning.start':
            if not self.powered or self.lid_angle<1.4: raise ActionRejected('PRECONDITION_FAILED')
            self.cleaner_input_m3+=a['volume_m3']; self.cleaner_remaining_m3+=a['volume_m3']; self._clean_start_m3=self.cleaner_remaining_m3
            r=self._start('cleaning')
        elif capability=='acquisition.stop':
            if self.phase=='running': self.phase='cancelled'
            r={'phase':self.phase}
        else: return self._measure()
        self.revision+=1; return copy.deepcopy(r)
    def step(self,dt_s):
        if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<=0: raise ValueError('finite positive dt_s required')
        left=float(dt_s)
        while left>1e-10:
            active=self.phase=='running'; duration=10. if self.operation=='measurement' else 30.
            h=min(left,1.,duration-self.elapsed_s) if active else min(left,1.)
            target=self.target_k if self.powered else 293.15; self.temperature_k=target+(self.temperature_k-target)*math.exp(-h/20.)
            if active:
                self.elapsed_s+=h
                if self.operation=='cleaning':
                    for sid,v in self.remaining.items():
                        transfer=v*(1-math.exp(-h/5.)); self.remaining[sid]-=transfer; self.waste_species[sid]+=transfer
                    flush=min(self.cleaner_remaining_m3,self._clean_start_m3*h/30.); self.cleaner_remaining_m3-=flush; self.cleaner_waste_m3+=flush
                    self.contamination*=math.exp(-h/8.)
                if self.elapsed_s>=duration-1e-9:
                    self.phase='succeeded'
                    if self.operation=='cleaning':
                        for sid,v in self.remaining.items(): self.waste_species[sid]+=v
                        self.remaining={}; self.matrix_id=None; self.cleaner_waste_m3+=self.cleaner_remaining_m3; self.cleaner_remaining_m3=0.
                    else:
                        n=self._index(); conc=self._concentration(n); valid=1.26<=n<=1.72
                        exact=len(self.remaining)==1 and self.contamination==0 and all(abs(self.temperature_k-self.templates[s]['temperature_k'])<1e-6 for s in self.remaining)
                        self.result=dict(index=n if valid else None,temperature_k=self._values()['thermal.indicated_temperature_k'],wavelength_m=589.3e-9,mass_fraction=conc,
                            phase='succeeded',quality='suspect' if not valid or self.contamination>.002 or (self.mode=='calibrated_concentration' and conc is None) else 'estimated',
                            evidence='derived' if exact else 'synthetic',concentration_evidence='user_supplied_calibration' if conc is not None else 'unavailable',template_source=TEMPLATE_REL)
            self.time_s+=h; left-=h
        self.revision+=1; return self.read('evaluator')

class AntonpaarAbbematadvancedSession:
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
        meta=export_antonpaar_abbematadvanced_contract_metadata()
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

def export_antonpaar_abbematadvanced_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'AntonpaarAbbematadvancedSession.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key in ('acquisition.start','cleaning.start'); pre=[]
        conditions={
         'acquisition.start':('供电、盖闭、足量液体、恒温且方法标定适用',['power.enabled','lid.angle_rad','sample.filled','thermal.target_k','cycle.phase'],'powered and closed and filled and abs(T-target)<=0.05 and no active operation; selected calibration matches matrix and temperature'),
         'sample.configure':('受信任宿主、盖打开且空池，模板与相态可用',['lid.angle_rad','sample.remaining_m3','cleaner.remaining_m3'],'powered and lid>=1.4 and empty and no cleaner; fractions sum to 1; verify phase before mutation'),
         'calibration.configure':('无操作在运行；明确体系/来源，至少两点且nD及浓度严格单调',['cycle.phase'],'not running; n strictly increasing and concentration strictly monotonic'),
         'cleaning.start':('供电、盖打开、没有其他运行中的过程',['power.enabled','lid.angle_rad','cycle.phase'],'powered and lid>=1.4 and not running'),
         'lid.set_angle':('进行中过程锁定盖',['cycle.phase'],'not running'),
         'thermal.set_temperature':('运行中锁定温度',['cycle.phase'],'not running'),
         'method.configure':('运行中锁定方法',['cycle.phase'],'not running')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.antonpaar_abbematadvanced.AntonpaarAbbematadvancedSession.invoke',invocation=f'session.invoke(instance_id="abbemat5201_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；取消保留部分清洗参考体积转移' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电不回滚已排入废液的参考体积。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    mechanism=dict(component_ids=['sapphire_prism','peltier','led_source','interference_filter','detector','sample_well'],
      state_ids=['thermal.temperature_k','optical.indicated_index','sample.remaining_m3','sample.input_m3','sample.waste_m3'],
      meaning_zh='文献单点nD、Lorentz–Lorenz理想混合、温度/污染工程代理；参考体积账本守恒',implementation='lumped',
      equations=['T_next=T_target+(T-T_target)*exp(-dt/20)',
      'n_i(T)=n_ref_i-0.0004*(T-T_ref_i); L=sum(phi_i*(n_i^2-1)/(n_i^2+2)); n=sqrt((1+2L)/(1-L))+0.001*contamination',
      'concentration=piecewise_linear_calibration(n) only for matching matrix and temperature; never universal Brix',
      'input_reference_volume_i=remaining_reference_volume_i+waste_reference_volume_i'],
      parameters={'dn_dt':dict(value=-.0004,unit='1/K',meaning_zh='未取得实测系数的统一热光代理',evidence='assumption',source_ids=['simulation_assumption'],uncertainty=None)},
      source_ids=['refractive_templates','simulation_assumption'],limitations=['温度系数、混合、污染响应均未标定；未重建光线追迹/临界角成像。','无样品密度时不宣称质量守恒；本过程守恒各物质在装样参考温度定义的体积账本。','用户标定曲线仅做数学插值，实验有效性需用户证据；不外推。'])
    for key,slot in slots.items():
        if key.endswith('_m3'): slot['meaning_zh']=(slot['meaning_zh'] or '')+'；装样参考温度下体积账本，不是真实膨胀后的几何体积'
    _ann.annotate_export('antonpaar_abbematadvanced',caps,slots,{'refractometry':mechanism},inputs={})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'refractometry':mechanism})

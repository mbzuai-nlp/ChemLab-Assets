"""Original S60H operating surrogate; no computed cavitation/cleanliness claim."""
from __future__ import annotations
import copy,json,math
from asset_runtime import _annotations as _ann
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT,PHASE
from asset_runtime.antonpaar_abbematadvanced import validate
ASSET_TYPE_ID='instrument.elma_elmasonics60h';CATALOG_ID='CHEM-013';PROTOCOL_VERSION='1.0.0'
TIMES=[60.,120.,180.,240.,300.,600.,900.,1200.,1500.,1800.]
TEMP=number('K',278.15,373.15);TARGET=number('K',303.15,353.15);ANGLE=number('rad',0,math.pi/2)
MODE={'type':'string','enum':['normal','sweep','degas']};NULL_TIME={'anyOf':[number('s',0),{'type':'null'}]}
RESULT=obj({'temperature_k':TEMP,'remaining_s':NULL_TIME,'elapsed_s':number('s',0),'phase':PHASE,'mode':MODE,'waiting_for_temperature':BOOL,'ultrasound_active':BOOL,'cleaning_efficiency':{'type':'null'},'degassing_fraction':{'type':'null'},'evidence':TEXT})
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['button_power','power_inlet'],'供电；断电取消超声并停热，不回滚显热','device_command','agent',{'enabled':True},['power.enabled','heater.enabled','cycle.phase']),
 'thermal.set_temperature':(obj({'temperature_k':TARGET}),obj({'temperature_k':TARGET}),['heater','knob_temperature'],'30–80C，5C离散档；设定本身不推进温度','device_command','agent',{'temperature_k':313.15},['thermal.target_k']),
 'thermal.select_stage':(obj({'stage':{'type':'integer','minimum':0,'maximum':10,'x-unit':'1'}}),obj({'temperature_k':TARGET}),['knob_temperature','heater'],'物理旋钮离散档0–10映射30–80C；整数避免float32 K边界误差，不含关闭位','device_command','agent',{'stage':2},['thermal.target_k']),
 'thermal.set_enabled':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['heater','knob_temperature'],'温度旋钮非零位的逻辑使能，禁止空槽加热','device_command','agent',{'enabled':True},['heater.enabled']),
 'timer.configure':(obj({'continuous':BOOL,'duration_s':number('s',60,1800)}),obj({'configured':BOOL}),['knob_timer'],'1/2/3/4/5/10/15/20/25/30min或连续；运行时锁定','configuration','agent',{'continuous':False,'duration_s':60.},['timer.continuous','timer.duration_s']),
 'ultrasound.set_mode':(obj({'mode':MODE}),obj({'mode':MODE}),['button_sweep','button_degas','transducers'],'normal/sweep/degas单一枚举，扫频与脱气互斥','device_command','agent',{'mode':'sweep'},['ultrasound.mode']),
 'cover.set_lift':(obj({'lift_m':number('m',0,.30)}),obj({'lift_m':number('m',0,.30)}),['cover'],'可移除盖的宿主竖直搬离代理，不是实物铰链或电动升降','device_command','agent',{'lift_m':.30},['cover.lift_m']),
 'bath.configure':(obj({'volume_m3':number('m^3',.0001,.00575),'density_kg_m3':number('kg/m^3',500,2000),'cp_j_kg_k':number('J/(kg*K)',100,10000),'initial_temperature_k':number('K',283.15,333.15),'compatible_aqueous':BOOL}),obj({'configured':BOOL}),['tank','bath_port'],'受信任宿主注入已审查水性浴液，质量密度热容显式输入','configuration','evaluator',{'volume_m3':.0038,'density_kg_m3':1000.,'cp_j_kg_k':4180.,'initial_temperature_k':298.15,'compatible_aqueous':True},['bath.volume_m3','mass.input_kg','thermal.temperature_k']),
 'load.configure':(obj({'mass_kg':number('kg',.001,2),'displaced_volume_m3':number('m^3',0,.001),'cp_j_kg_k':number('J/(kg*K)',100,10000),'initial_temperature_k':number('K',283.15,333.15)}),obj({'configured':BOOL}),['basket','sample_port'],'受信任宿主将器件置入悬空篮；质量上限2kg仅工程任务域，不是厂家额定负载','configuration','evaluator',{'mass_kg':.1,'displaced_volume_m3':.00002,'cp_j_kg_k':500.,'initial_temperature_k':298.15},['load.present','mass.input_kg','thermal.temperature_k']),
 'load.unload':(obj({}),obj({'unloaded':BOOL}),['basket','sample_port'],'停机开盖移出器件，保留质量与显热账本','configuration','evaluator',{},['load.present','mass.removed_kg']),
 'drain.set_angle':(obj({'angle_rad':ANGLE}),obj({'angle_rad':ANGLE}),['knob_drain','drain_port'],'0横向关闭、pi/2竖向打开；液体只由step排出','device_command','agent',{'angle_rad':math.pi/2},['drain.angle_rad']),
 'ultrasound.start':(obj({'temperature_controlled':BOOL}),obj({'phase':PHASE,'operation_id':TEXT}),['transducers','button_start'],'开始定时或连续超声；温控模式达到设定后才开始计时','device_command','agent',{'temperature_controlled':False},['cycle.phase','timer.elapsed_s','ultrasound.waiting']),
 'degas.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['transducers','button_degas'],'Auto-Degas固定600s，结束停超声但保留加热状态','device_command','agent',{},['cycle.phase','ultrasound.mode','timer.elapsed_s']),
 'ultrasound.stop':(obj({}),obj({'phase':PHASE}),['transducers','button_start'],'取消保留浴温、已消耗能量和已排出液体','device_command','agent',{},['cycle.phase','ultrasound.active']),
 'status.measure':(obj({}),RESULT,['led_temperature','led_time','temperature_sensor'],'工程浴温与操作状态；效率/脱气比例无证据返回null','query','agent',{},[]),
}
SLOTS={
 'power.enabled':(BOOL,'1','setpoint','agent',['power_inlet'],('power.set','enabled')),
 'heater.enabled':(BOOL,'1','setpoint','agent',['heater'],('thermal.set_enabled','enabled')),
 'thermal.target_k':(TARGET,'K','setpoint','agent',['heater'],('thermal.set_temperature','temperature_k')),
 'thermal.temperature_k':(TEMP,'K','process_truth','evaluator',['tank'],None),
 'thermal.indicated_temperature_k':(TEMP,'K','derived','agent',['temperature_sensor'],None),
 'thermal.heater_power_w':(number('W',0,400),'W','process_truth','evaluator',['heater'],None),
 'ultrasound.mode':(MODE,'1','setpoint','agent',['transducers'],('ultrasound.set_mode','mode')),
 'ultrasound.active':(BOOL,'1','derived','agent',['transducers'],None),
 'ultrasound.waiting':(BOOL,'1','derived','agent',['transducers'],None),
 'ultrasound.effective_power_w':(number('W',0,150),'W','process_truth','evaluator',['transducers'],None),
 'cover.lift_m':(number('m',0,.30),'m','scene','agent',['cover'],('cover.set_lift','lift_m')),
 'drain.angle_rad':(ANGLE,'rad','scene','agent',['knob_drain'],('drain.set_angle','angle_rad')),
 'timer.continuous':(BOOL,'1','setpoint','agent',['knob_timer'],None),
 'timer.duration_s':(number('s',60,1800),'s','setpoint','agent',['knob_timer'],None),
 'timer.remaining_s':(NULL_TIME,'s','derived','agent',['led_time'],None),
 'timer.elapsed_s':(number('s',0),'s','derived','agent',['led_time'],None),
 'cycle.phase':(PHASE,'1','derived','agent',['button_start'],None),
 'cycle.error':(TEXT,'1','derived','agent',['control_panel'],None),
 'bath.volume_m3':(number('m^3',0,.00575),'m^3','process_truth','evaluator',['tank'],None),
 'load.present':(BOOL,'1','derived','agent',['basket'],None),
}
for k in ['input','retained','removed']:SLOTS['mass.'+k+'_kg']=(number('kg',0),'kg','process_truth','evaluator',['tank'],None)
for k in ['heater_input','ultrasound_input','acoustic_loss','loading_input','stored','ambient_loss','unloading_output']:SLOTS['energy.'+k+'_j']=(number('J'),'J','process_truth','evaluator',['tank'],None)
for group,n,comp in [('time',11,'led_time'),('temperature',11,'led_temperature'),('mode',4,'control_panel')]:
    for i in range(n):SLOTS[f'panel.{group}_{i}']=(BOOL,'1','derived','agent',[comp],None)
class ElmaElmasonics60hRuntime:
    def __init__(self,instance_id='s60h_01'):
        self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.heater=False;self.ambient=298.15;self.temperature=298.15;self.target=313.15;self.heater_w=0.;self.ultrasound_w=0.
        self.cover=0.;self.drain=0.;self.volume=0.;self.density=1000.;self.cp=4180.;self.load=None;self.mode='normal';self.continuous=False;self.duration=60.;self.elapsed_s=0.;self.wait_s=0.;self.waiting=False;self.auto_degas=False;self.active=False
        self.phase='idle';self.error='';self.operation_id=None;self._operation_count=0;self.mass_in=0.;self.mass_out=0.;self.energy={k:0. for k in ['heater_input','ultrasound_input','acoustic_loss','loading_input','ambient_loss','unloading_output']}
        self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
    def register_principal(self,principal_id,visibility='agent'):
        if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id='__public_agent__'):
        if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return ElmaElmasonics60hSession(self,principal_id)
    def describe(self):return self.session().describe(instance_id=self.instance_id)
    def _mass(self):return self.volume*self.density+(self.load['mass_kg'] if self.load else 0.)
    def _capacity(self):return 1000.+self.volume*self.density*self.cp+(self.load['mass_kg']*self.load['cp_j_kg_k'] if self.load else 0.)
    def _values(self):
        v={'power.enabled':self.powered,'heater.enabled':self.heater,'thermal.target_k':self.target,'thermal.temperature_k':self.temperature,'thermal.indicated_temperature_k':round(self.temperature-273.15,1)+273.15,'thermal.heater_power_w':self.heater_w,'ultrasound.mode':self.mode,'ultrasound.active':self.active,'ultrasound.waiting':self.waiting,'ultrasound.effective_power_w':self.ultrasound_w,'cover.lift_m':self.cover,'drain.angle_rad':self.drain,'timer.continuous':self.continuous,'timer.duration_s':self.duration,'timer.remaining_s':max(0.,(600. if self.auto_degas else self.duration)-self.elapsed_s) if (self.auto_degas or not self.continuous) else None,'timer.elapsed_s':self.elapsed_s,'cycle.phase':self.phase,'cycle.error':self.error,'bath.volume_m3':self.volume,'load.present':self.load is not None,'mass.input_kg':self.mass_in,'mass.retained_kg':self._mass(),'mass.removed_kg':self.mass_out,'energy.stored_j':self._capacity()*(self.temperature-self.ambient)}
        v.update({'energy.'+k+'_j':x for k,x in self.energy.items()})
        flash=int(self.time_s*2)%2==0
        for i in range(11):
            setting=(i==10 if self.continuous and not self.auto_degas else i<10 and TIMES[i]==(600. if self.auto_degas else self.duration))
            remain=v['timer.remaining_s'];current=next((j for j,d in enumerate(TIMES) if remain is not None and 0<remain<=d),None)
            v[f'panel.time_{i}']=self.powered and (setting or (self.phase=='running' and i==current and flash))
            target=i*5+303.15;current_temp=round((self.temperature-303.15)/5)
            v[f'panel.temperature_{i}']=self.powered and ((self.heater and abs(self.target-target)<1e-8) or (i==current_temp and flash))
        for i,on in enumerate([self.active and self.mode=='sweep',self.active and self.mode=='degas' and (not self.auto_degas or flash),self.active or (self.waiting and flash),self.powered]):v[f'panel.mode_{i}']=self.powered and on
        return v
    def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('evaluator','debug')})
    def debug_snapshot(self):return dict(state=self.read('evaluator'),load=copy.deepcopy(self.load))
    def _measure(self):return dict(temperature_k=self._values()['thermal.indicated_temperature_k'],remaining_s=self._values()['timer.remaining_s'],elapsed_s=self.elapsed_s,phase=self.phase,mode=self.mode,waiting_for_temperature=self.waiting,ultrasound_active=self.active,cleaning_efficiency=None,degassing_fraction=None,evidence='engineering_assumption; no measured cleaning or degassing efficiency')
    def _access(self):return not self.powered and self.cover>=.20 and self.phase!='running'
    def invoke(self,capability,**a):
        if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability][0],a)
        if capability=='status.measure':return self._measure()
        if capability=='power.set':
            self.powered=a['enabled']
            if not self.powered:
                self.heater=False;self.active=False;self.waiting=False;self.heater_w=0.;self.ultrasound_w=0.
                if self.phase=='running':self.phase='cancelled'
            r={'enabled':self.powered}
        elif capability=='thermal.set_temperature':
            if abs((a['temperature_k']-303.15)/5-round((a['temperature_k']-303.15)/5))>1e-8:raise ActionRejected('INVALID_ARGUMENT')
            self.target=a['temperature_k'];r={'temperature_k':self.target}
        elif capability=='thermal.select_stage':
            if type(a['stage']) is not int or not 0<=a['stage']<=10:raise ActionRejected('INVALID_ARGUMENT')
            self.target=303.15+5*a['stage'];r={'temperature_k':self.target}
        elif capability=='thermal.set_enabled':
            if a['enabled'] and (not self.powered or self.volume<=1e-9 or self.drain>0 or self.error):raise ActionRejected('PRECONDITION_FAILED')
            self.heater=a['enabled'];r={'enabled':self.heater}
        elif capability=='timer.configure':
            if self.phase=='running':raise ActionRejected('BUSY')
            if a['duration_s'] not in TIMES:raise ActionRejected('INVALID_ARGUMENT')
            self.continuous=a['continuous'];self.duration=a['duration_s'];r={'configured':True}
        elif capability=='ultrasound.set_mode':
            if self.auto_degas and self.phase=='running':raise ActionRejected('BUSY')
            self.mode=a['mode'];r={'mode':self.mode}
        elif capability=='cover.set_lift':self.cover=a['lift_m'];r={'lift_m':self.cover}
        elif capability=='bath.configure':
            if not self._access() or self.volume>1e-12 or self.load or self.drain or not a['compatible_aqueous']:raise ActionRejected('PRECONDITION_FAILED')
            c=self._capacity();m=a['volume_m3']*a['density_kg_m3'];cap=m*a['cp_j_kg_k'];self.temperature=(c*self.temperature+cap*a['initial_temperature_k'])/(c+cap);self.energy['loading_input']+=cap*(a['initial_temperature_k']-self.ambient)
            self.volume=a['volume_m3'];self.density=a['density_kg_m3'];self.cp=a['cp_j_kg_k'];self.mass_in+=m;self.error='';r={'configured':True}
        elif capability=='load.configure':
            if not self._access() or self.load or self.volume<=0 or self.volume+a['displaced_volume_m3']>.00575:raise ActionRejected('PRECONDITION_FAILED')
            c=self._capacity();cap=a['mass_kg']*a['cp_j_kg_k'];self.temperature=(c*self.temperature+cap*a['initial_temperature_k'])/(c+cap);self.energy['loading_input']+=cap*(a['initial_temperature_k']-self.ambient);self.mass_in+=a['mass_kg'];self.load=copy.deepcopy(a);r={'configured':True}
        elif capability=='load.unload':
            if not self._access() or not self.load:raise ActionRejected('PRECONDITION_FAILED')
            self.mass_out+=self.load['mass_kg'];self.energy['unloading_output']+=self.load['mass_kg']*self.load['cp_j_kg_k']*(self.temperature-self.ambient);self.load=None;r={'unloaded':True}
        elif capability=='drain.set_angle':
            if a['angle_rad']>0 and (self.powered or self.load or self.temperature>333.15):raise ActionRejected('PRECONDITION_FAILED')
            self.drain=a['angle_rad'];r={'angle_rad':self.drain}
        elif capability in ('ultrasound.start','degas.start'):
            if not self.powered or self.phase=='running' or self.volume<=1e-9 or self.drain>0 or self.error:raise ActionRejected('PRECONDITION_FAILED')
            wait=capability=='ultrasound.start' and a['temperature_controlled']
            if wait and not self.heater:raise ActionRejected('PRECONDITION_FAILED')
            self._operation_count+=1;self.operation_id=f'{self.instance_id}:sonication:{self._operation_count}';self.phase='running';self.elapsed_s=0.;self.wait_s=0.;self.waiting=wait and self.temperature<self.target;self.auto_degas=capability=='degas.start'
            if self.auto_degas:self.mode='degas'
            r={'phase':self.phase,'operation_id':self.operation_id}
        elif capability=='ultrasound.stop':
            if self.phase=='running':self.phase='cancelled'
            self.active=False;self.waiting=False;self.ultrasound_w=0.;r={'phase':self.phase}
        self.revision+=1;validate(CAPS[capability][1],r);return r
    def step(self,dt_s):
        validate(number('s',0,86400),dt_s)
        if dt_s==0:return
        remaining=float(dt_s)
        while remaining>1e-10:
            dt=min(.5,remaining)
            limit=600. if self.auto_degas else (43200. if self.continuous else self.duration)
            if self.phase=='running' and not self.waiting:dt=min(dt,max(1e-10,limit-self.elapsed_s))
            if self.waiting:dt=min(dt,5.-self.wait_s%65 if self.wait_s%65<5 else 65.-self.wait_s%65)
            self.active=self.powered and self.phase=='running' and self.volume>1e-9 and (not self.waiting or self.wait_s%65<5)
            self.heater_w=400. if self.powered and self.heater and self.volume>1e-9 and self.temperature<self.target else 0.
            self.ultrasound_w=(75. if self.mode=='degas' else 150.) if self.active else 0.
            p=self.heater_w+.7*self.ultrasound_w;ua=3. if self.cover<.01 else 6.;c=self._capacity();old=self.temperature
            self.temperature=self.ambient+p/ua+(old-self.ambient-p/ua)*math.exp(-ua*dt/c)
            self.energy['heater_input']+=self.heater_w*dt;self.energy['ultrasound_input']+=self.ultrasound_w*dt;self.energy['acoustic_loss']+=.3*self.ultrasound_w*dt;self.energy['ambient_loss']+=p*dt-c*(self.temperature-old)
            if self.phase=='running':
                if self.waiting:
                    self.wait_s+=dt
                    if self.temperature>=self.target:self.waiting=False
                else:
                    self.elapsed_s+=dt
                    if self.elapsed_s>=limit-1e-8:
                        self.elapsed_s=limit;self.phase='succeeded';self.active=False;self.ultrasound_w=0.
                        if self.continuous and not self.auto_degas:self.powered=False;self.heater=False;self.heater_w=0.
            if self.temperature>=368.15:
                self.error='SIMULATION_OVER_TEMPERATURE';self.phase='failed';self.powered=False;self.heater=False;self.active=False;self.waiting=False;self.heater_w=0.;self.ultrasound_w=0.
            if self.drain>0 and self.volume>0:
                dv=min(self.volume,2e-5*(self.drain/(math.pi/2))*math.sqrt(self.volume/.00575)*dt);dm=dv*self.density
                self.energy['unloading_output']+=dm*self.cp*(self.temperature-self.ambient);self.volume-=dv;self.mass_out+=dm
            self.time_s+=dt;remaining-=dt
        self.revision+=1
class ElmaElmasonics60hSession:
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
        meta=export_elma_elmasonics60h_contract_metadata()
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
        response=self.invoke(instance_id=self._host.instance_id,capability_id='ultrasound.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])

def export_elma_elmasonics60h_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'ElmaElmasonics60hSession.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key in ('ultrasound.start','degas.start'); pre=[]
        conditions={
         'ultrasound.start':('供电、有液、排液关闭、无错误；温控模式需先启加热',['power.enabled','bath.volume_m3','drain.angle_rad','cycle.phase','heater.enabled'],'powered and volume>0 and drain==0 and not running and not error; temperature_controlled requires heater'),
         'degas.start':('供电、有液、排液关闭且未运行',['power.enabled','bath.volume_m3','drain.angle_rad','cycle.phase'],'powered and volume>0 and drain==0 and not running and not error'),
         'bath.configure':('宿主身份，断电开盖空槽、无负载、排液关闭且介质已审查',['power.enabled','cover.lift_m','bath.volume_m3','load.present','drain.angle_rad'],'not powered and cover>=0.2 and empty and no load and drain==0 and compatible_aqueous'),
         'load.configure':('宿主身份、断电开盖、有浴液、没有器件且总体积不超5.75L',['power.enabled','cover.lift_m','bath.volume_m3','load.present'],'access and volume>0 and no load and volume+displaced_volume<=0.00575'),
         'load.unload':('宿主身份、断电开盖且有器件',['power.enabled','cover.lift_m','load.present'],'access and load exists'),
         'drain.set_angle':('打开时要求断电、无器件、浴温不高于60C；这是仿真宿主安全策略',['power.enabled','load.present','thermal.temperature_k'],'angle==0 or (not powered and no load and T<=333.15)'),
         'thermal.set_enabled':('启热需供电有液、阀关闭且无错误',['power.enabled','bath.volume_m3','drain.angle_rad','cycle.error'],'not enabled or (powered and volume>0 and drain==0 and not error)'),
         'timer.configure':('停止状态才可切换定时参数',['cycle.phase'],'not running'),
         'ultrasound.set_mode':('Auto-Degas运行期间模式锁定',['cycle.phase','ultrasound.mode'],'not (auto_degas and running)')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.elma_elmasonics60h.ElmaElmasonics60hSession.invoke',invocation=f'session.invoke(instance_id="s60h_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；定时/Auto-Degas完成；连续12h关机；取消保留浴温和能量' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电不回滚已经发生的计时、排液和热过程。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    mechanism=dict(component_ids=['tank','heater','transducers','basket','drain_port'],state_ids=['thermal.temperature_k','bath.volume_m3','mass.input_kg','mass.retained_kg','mass.removed_kg']+[k for k in SLOTS if k.startswith('energy.')],
      meaning_zh='能量守恒集总浴槽及排液；不解声场、气泡动力学或清洁程度',implementation='lumped',
      equations=['C=1000+m_liquid*Cp_liquid+m_load*Cp_load',
      'P_heater=400W if enabled and T<Ttarget, else 0; P_ultrasound=150W normal/sweep, 75W assumed averaged degas',
      'P_bath=P_heater+0.7*P_ultrasound; 0.3*P_ultrasound accounted outside bath',
      'Tnext=Tambient+P_bath/UA+(T-Tambient-P_bath/UA)*exp(-UA*dt/C)',
      'Qdrain=2e-5*(angle/(pi/2))*sqrt(V/0.00575) m3/s, limited by retained volume',
      'mass_input=mass_retained+mass_removed',
      'heater_input+ultrasound_input+loading_input=stored+ambient_loss+acoustic_loss+unloading_output'],
      parameters={'ultrasound_effective':dict(value=150.,unit='W',meaning_zh='原版有效功率，不用600W峰值作平均输入',evidence='documented',source_ids=['manufacturer_primary'],uncertainty=None),
      'ultrasound_peak':dict(value=600.,unit='W',meaning_zh='铭牌峰值仅元数据，未积分进能量',evidence='documented',source_ids=['manufacturer_primary'],uncertainty=None),
      'ultrasound_frequency':dict(value=37000.,unit='Hz',meaning_zh='37kHz；不解调制声波',evidence='documented',source_ids=['manufacturer_primary'],uncertainty=None)},
      source_ids=['manufacturer_primary','manufacturer_manual','simulation_assumption'],limitations=['UA闭盖3/开盖6W/K、壳体热容1000J/K、70%热化、Degas平均50%占空及排液系数均为未标定工程假设。',
      '温控预混合采用约5s开/60s停；补充2018手册12h连续安全关机未证明适用于所有2007序列号。',
      '加料瞬时完全混合保证能量账本，不表示实物立即均温；无蒸发、溶出、声场、空化、清洗效率或脱气浓度求解。',
      '95C停机是数值任务域保护，不冒充厂家超温互锁；排液需断电无负载且<=60C也是宿主策略。'])
    _ann.annotate_export('elma_elmasonics60h',caps,slots,{'ultrasonic_bath':mechanism},inputs={})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'ultrasonic_bath':mechanism})

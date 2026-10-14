"""KS4000 i control: orbital translation, timed pause and energy-accounted incubation."""
from __future__ import annotations
import copy,json,math
from asset_runtime import _annotations as _ann
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT,PHASE
from asset_runtime.antonpaar_abbematadvanced import validate
ASSET_TYPE_ID='instrument.ika_ks4000';CATALOG_ID='CHEM-009';PROTOCOL_VERSION='1.0.0';RPM=math.pi/30
TEMP=number('K',288.15,363.15);TARGET=number('K',293.15,353.15);SPEED=number('rad/s',10*RPM,500*RPM);ANGLE=number('rad',0,math.pi/2);DURATION=number('s',60,3596400);NULL_TIME={'anyOf':[number('s',0),{'type':'null'}]}
IND_TEMP=number('K',283.15,368.15)
RESULT=obj({'speed_rad_s':number('rad/s',0,500*RPM),'temperature_k':IND_TEMP,'remaining_s':NULL_TIME,'elapsed_s':number('s',0),'phase':PHASE,'paused':BOOL,'error':TEXT,'evidence':TEXT})
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['button_power','power_inlet'],'默认A模式供电；断电不自动重启','device_command','agent',{'enabled':True},['power.enabled','heater.enabled','cycle.phase']),
 'thermal.set_temperature':(obj({'temperature_k':TARGET}),obj({'temperature_k':TARGET}),['heater','display_digits'],'环境+5至80C、0.1C步进；设置不推进温度','device_command','agent',{'temperature_k':310.15},['thermal.target_k']),
 'thermal.set_enabled':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['heater','button_temp'],'闭罩供电才可开启加热；与摇动定时独立','device_command','agent',{'enabled':True},['heater.enabled']),
 'shaking.set_speed':(obj({'speed_rad_s':SPEED}),obj({'speed_rad_s':SPEED}),['motor','button_speed'],'10–500rpm、1rpm步进并服从负载与安全上限，允许运行中调速','device_command','agent',{'speed_rad_s':120*RPM},['shaking.target_rad_s']),
 'hood.set_angle':(obj({'angle_rad':ANGLE}),obj({'angle_rad':ANGLE}),['hood'],'被动罩宿主控制；开罩停热/停驱动，惯性不瞬间归零','device_command','agent',{'angle_rad':math.pi/2},['hood.angle_rad','heater.enabled','timer.paused','cycle.phase']),
 'timer.configure':(obj({'mode':{'type':'string','enum':['timed','continuous']},'duration_s':DURATION}),obj({'configured':BOOL}),['button_timer','display_digits'],'当前规格最小60s、最大999h；运行时禁止改模式/时长','configuration','agent',{'mode':'timed','duration_s':60.},['timer.mode','timer.duration_s','timer.remaining_s']),
 'fixture.configure':(obj({'installed':BOOL,'mass_kg':number('kg',.01,20)}),obj({'configured':BOOL}),['fixture_frame'],'AS4000.1演示夹具，宿主声明实际质量，含在20kg限额内','configuration','evaluator',{'installed':True,'mass_kg':3.2},['fixture.installed','fixture.mass_kg']),
 'load.configure':(obj({'slot':number('1',0,8),'vessel_id':TEXT,'container_mass_kg':number('kg',.001,10),'liquid_mass_kg':number('kg',.000001,10),'volume_m3':number('m^3',1e-9,.0005),'effective_cp_j_kg_k':number('J/(kg*K)',1,10000),'initial_temperature_k':TEMP,'secured':BOOL}),obj({'configured':BOOL}),['fixture_frame','platform'],'开罩静止下宿主装入容器与液体；质量和体积独立输入，无密度换算','configuration','evaluator',{'slot':4,'vessel_id':'synthetic_demo','container_mass_kg':.2,'liquid_mass_kg':.2,'volume_m3':.0002,'effective_cp_j_kg_k':3000.,'initial_temperature_k':298.15,'secured':True},['load.count','mass.input_kg','mass.retained_kg','thermal.temperature_k']),
 'load.unload':(obj({'slot':number('1',0,8)}),obj({'unloaded':BOOL}),['fixture_frame','platform'],'开罩且余转停止后移出单个容器，保留质量与显热账本','configuration','evaluator',{'slot':4},['load.count','mass.removed_kg','mass.retained_kg']),
 'safety.configure':(obj({'speed_limit_rad_s':SPEED,'temperature_limit_k':TEMP}),obj({'configured':BOOL}),['safety_sensor','display_digits'],'停摇停热后设置限制；不替代对所用介质的真实安全评估','configuration','agent',{'speed_limit_rad_s':500*RPM,'temperature_limit_k':363.15},['safety.speed_limit_rad_s','safety.temperature_limit_k']),
 'calibration.configure':(obj({'offset_k':number('K',-5,5),'source':TEXT}),obj({'offset_k':number('K',-5,5)}),['pt1000_internal','display_digits'],'有来源温度偏置代理；非厂家完整实物校准流程','configuration','agent',{'offset_k':0.,'source':'synthetic zero-offset example'},['thermal.indicated_temperature_k']),
 'shaking.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['motor','eccentric_drive','platform','display_digits'],'闭罩、夹具已装、所有负载固定且质量转速合规后开始','device_command','agent',{},['cycle.phase','timer.elapsed_s','timer.paused']),
 'shaking.stop':(obj({}),obj({'phase':PHASE}),['motor','button_speed'],'取消摇动，余转和热状态继续演化；不回滚计时或能量','device_command','agent',{},['cycle.phase','timer.paused']),
 'status.measure':(obj({}),RESULT,['display_digits','pt1000_internal','motor'],'只读实际速度、箱温与剩余时间；无物质分析读数','query','agent',{},[]),
}
SLOTS={
 'power.enabled':(BOOL,'1','setpoint','agent',['power_inlet'],('power.set','enabled')),
 'heater.enabled':(BOOL,'1','setpoint','agent',['heater'],('thermal.set_enabled','enabled')),
 'thermal.target_k':(TARGET,'K','setpoint','agent',['heater'],('thermal.set_temperature','temperature_k')),
 'thermal.temperature_k':(TEMP,'K','process_truth','evaluator',['chamber'],None),
 'thermal.indicated_temperature_k':(IND_TEMP,'K','derived','agent',['pt1000_internal','display_digits'],None),
 'thermal.heater_power_w':(number('W',0,1000),'W','process_truth','evaluator',['heater'],None),
 'shaking.target_rad_s':(SPEED,'rad/s','setpoint','agent',['motor'],('shaking.set_speed','speed_rad_s')),
 'shaking.actual_rad_s':(number('rad/s',0,500*RPM),'rad/s','process_truth','evaluator',['motor'],None),
 'shaking.indicated_rad_s':(number('rad/s',0,500*RPM),'rad/s','derived','agent',['motor','display_digits'],None),
 'orbit.phase_rad':(number('rad',0,2*math.pi),'rad','process_truth','evaluator',['eccentric_drive'],None),
 'orbit.x_m':(number('m',-.01,.01),'m','scene','agent',['orbit_x'],None),
 'orbit.y_m':(number('m',-.01,.01),'m','scene','agent',['platform'],None),
 'hood.angle_rad':(ANGLE,'rad','scene','agent',['hood'],('hood.set_angle','angle_rad')),
 'timer.mode':({'type':'string','enum':['timed','continuous']},'1','setpoint','agent',['display_digits'],None),
 'timer.duration_s':(DURATION,'s','setpoint','agent',['display_digits'],None),
 'timer.remaining_s':(NULL_TIME,'s','derived','agent',['display_digits'],None),
 'timer.elapsed_s':(number('s',0),'s','derived','agent',['display_digits'],None),
 'timer.paused':(BOOL,'1','derived','agent',['display_digits'],None),
 'cycle.phase':(PHASE,'1','derived','agent',['display_digits'],None),
 'cycle.error':(TEXT,'1','derived','agent',['display_digits'],None),
 'fixture.installed':(BOOL,'1','derived','agent',['fixture_frame'],None),
 'fixture.mass_kg':(number('kg',0,20),'kg','process_truth','evaluator',['fixture_frame'],None),
 'load.count':(number('1',0,9),'1','derived','agent',['platform'],None),
 'mass.input_kg':(number('kg',0),'kg','process_truth','evaluator',['platform'],None),
 'mass.retained_kg':(number('kg',0,20),'kg','process_truth','evaluator',['platform'],None),
 'mass.removed_kg':(number('kg',0),'kg','process_truth','evaluator',['platform'],None),
 'energy.heater_input_j':(number('J',0),'J','process_truth','evaluator',['heater'],None),
 'energy.loading_input_j':(number('J'),'J','process_truth','evaluator',['platform'],None),
 'energy.loss_j':(number('J'),'J','process_truth','evaluator',['chamber'],None),
 'energy.unloading_output_j':(number('J'),'J','process_truth','evaluator',['platform'],None),
 'energy.stored_j':(number('J'),'J','process_truth','evaluator',['chamber'],None),
 'safety.speed_limit_rad_s':(SPEED,'rad/s','setpoint','agent',['motor'],None),
 'safety.temperature_limit_k':(TEMP,'K','setpoint','agent',['safety_sensor'],None),
}
def load_limit(mass):
    for m,r in [(6,500),(12,400),(15,300),(20,120)]:
        if mass<=m+1e-12:return r*RPM
    return 0.
class IkaKs4000Runtime:
    def __init__(self,instance_id='ks4000_01'):
        self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.heater=False;self.ambient=298.15;self.temperature=298.15;self.target=310.15;self.offset=0.;self.heater_w=0.
        self.speed_target=120*RPM;self.speed=0.;self.angle=0.;self.hood=0.;self.mode='timed';self.duration=60.;self.elapsed_s=0.;self.paused=False;self.hood_open_s=0.
        self.phase='idle';self.error='';self.operation_id=None;self._operation_count=0;self.fixture=False;self.fixture_mass=0.;self.loads={};self.mass_in=0.;self.mass_out=0.;self.heat_in=0.;self.loading_in=0.;self.loss=0.;self.unloading_out=0.
        self.speed_limit=500*RPM;self.temperature_limit=363.15;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
    def register_principal(self,principal_id,visibility='agent'):
        if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id='__public_agent__'):
        if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return IkaKs4000Session(self,principal_id)
    def describe(self):return self.session().describe(instance_id=self.instance_id)
    def _mass(self):return sum(s['container_mass_kg']+s['liquid_mass_kg'] for s in self.loads.values())
    def _capacity(self):return 8000.+sum((s['container_mass_kg']+s['liquid_mass_kg'])*s['effective_cp_j_kg_k'] for s in self.loads.values())
    def _values(self):
        return {'power.enabled':self.powered,'heater.enabled':self.heater,'thermal.target_k':self.target,'thermal.temperature_k':self.temperature,'thermal.indicated_temperature_k':round(self.temperature+self.offset-273.15,1)+273.15,'thermal.heater_power_w':self.heater_w,'shaking.target_rad_s':self.speed_target,'shaking.actual_rad_s':self.speed,'shaking.indicated_rad_s':round(self.speed/RPM)*RPM,'orbit.phase_rad':self.angle,'orbit.x_m':.01*math.cos(self.angle),'orbit.y_m':.01*math.sin(self.angle),'hood.angle_rad':self.hood,'timer.mode':self.mode,'timer.duration_s':self.duration,'timer.remaining_s':max(0.,self.duration-self.elapsed_s) if self.mode=='timed' else None,'timer.elapsed_s':self.elapsed_s,'timer.paused':self.paused,'cycle.phase':self.phase,'cycle.error':self.error,'fixture.installed':self.fixture,'fixture.mass_kg':self.fixture_mass,'load.count':len(self.loads),'mass.input_kg':self.mass_in,'mass.retained_kg':self._mass(),'mass.removed_kg':self.mass_out,'energy.heater_input_j':self.heat_in,'energy.loading_input_j':self.loading_in,'energy.loss_j':self.loss,'energy.unloading_output_j':self.unloading_out,'energy.stored_j':self._capacity()*(self.temperature-self.ambient),'safety.speed_limit_rad_s':self.speed_limit,'safety.temperature_limit_k':self.temperature_limit}
    def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('evaluator','debug')})
    def debug_snapshot(self):return dict(state=self.read('evaluator'),loads=copy.deepcopy(self.loads))
    def _measure(self):
        s=self._values();return dict(speed_rad_s=s['shaking.indicated_rad_s'],temperature_k=s['thermal.indicated_temperature_k'],remaining_s=s['timer.remaining_s'],elapsed_s=self.elapsed_s,phase=self.phase,paused=self.paused,error=self.error,evidence='engineering_assumption')
    def _access(self):return self.hood>=1.4 and self.speed<.001 and self.phase!='running'
    def invoke(self,capability,**a):
        if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability][0],a)
        if capability=='power.set':
            if a['enabled'] and not self.powered and self.hood<=1e-4 and self.temperature<self.temperature_limit:self.error=''
            self.powered=a['enabled']
            if not self.powered:
                self.heater=False;self.heater_w=0.;self.paused=False
                if self.phase=='running':self.phase='cancelled'
            r=dict(a)
        elif capability=='thermal.set_temperature':
            t=a['temperature_k']
            if t<self.ambient+5 or t>self.temperature_limit or abs((t-273.15)*10-round((t-273.15)*10))>1e-7:raise ActionRejected('INVALID_ARGUMENT')
            self.target=t;r=dict(a)
        elif capability=='thermal.set_enabled':
            if a['enabled'] and (not self.powered or self.hood>1e-4 or self.error or self.temperature>=self.temperature_limit):raise ActionRejected('PRECONDITION_FAILED')
            self.heater=a['enabled'];r=dict(a)
        elif capability=='shaking.set_speed':
            v=a['speed_rad_s'];rpm=v/RPM
            if abs(rpm-round(rpm))>1e-7 or v>min(self.speed_limit,load_limit(self.fixture_mass+self._mass()))+1e-9:raise ActionRejected('PRECONDITION_FAILED')
            self.speed_target=v;r=dict(a)
        elif capability=='hood.set_angle':
            was_closed=self.hood<=1e-4;self.hood=a['angle_rad']
            if self.hood>1e-4:
                self.heater=False;self.heater_w=0.
                if self.phase=='running':
                    if self.mode=='timed':self.paused=True
                    else:self.phase='cancelled'
                if was_closed:self.hood_open_s=0.
            elif self.phase=='running' and self.paused:self.paused=False;self.hood_open_s=0.
            r=dict(a)
        elif capability=='timer.configure':
            if self.phase=='running':raise ActionRejected('BUSY')
            if abs(a['duration_s']-round(a['duration_s']))>1e-7:raise ActionRejected('INVALID_ARGUMENT')
            self.mode=a['mode'];self.duration=a['duration_s'];self.elapsed_s=0.;r={'configured':True}
        elif capability=='fixture.configure':
            if not self._access() or self.loads:raise ActionRejected('PRECONDITION_FAILED')
            self.fixture=a['installed'];self.fixture_mass=a['mass_kg'] if self.fixture else 0.;r={'configured':True}
        elif capability=='load.configure':
            i=a['slot'];m=a['container_mass_kg']+a['liquid_mass_kg']
            if not self._access() or not self.fixture or i!=int(i) or int(i) in self.loads or not a['vessel_id'].strip() or self.fixture_mass+self._mass()+m>20:raise ActionRejected('PRECONDITION_FAILED')
            cp=m*a['effective_cp_j_kg_k'];c=self._capacity();self.temperature=(c*self.temperature+cp*a['initial_temperature_k'])/(c+cp);self.loading_in+=cp*(a['initial_temperature_k']-self.ambient);self.loads[int(i)]=copy.deepcopy(a);self.mass_in+=m;r={'configured':True}
        elif capability=='load.unload':
            i=a['slot']
            if not self._access() or i!=int(i) or int(i) not in self.loads:raise ActionRejected('PRECONDITION_FAILED')
            s=self.loads[int(i)];m=s['container_mass_kg']+s['liquid_mass_kg'];self.unloading_out+=m*s['effective_cp_j_kg_k']*(self.temperature-self.ambient);self.mass_out+=m;del self.loads[int(i)];r={'unloaded':True}
        elif capability=='safety.configure':
            if self.phase=='running' or self.heater or a['temperature_limit_k']<self.target or a['speed_limit_rad_s']<self.speed_target:raise ActionRejected('PRECONDITION_FAILED')
            self.speed_limit=a['speed_limit_rad_s'];self.temperature_limit=a['temperature_limit_k'];r={'configured':True}
        elif capability=='calibration.configure':
            if self.heater or not a['source'].strip():raise ActionRejected('PRECONDITION_FAILED')
            if not 288.15<=self.temperature+a['offset_k']<=363.15:raise ActionRejected('INVALID_ARGUMENT')
            self.offset=a['offset_k'];r={'offset_k':self.offset}
        elif capability=='shaking.start':
            if not self.powered or self.hood>1e-4 or not self.fixture or self.error or self.phase=='running' or any(not s['secured'] for s in self.loads.values()) or self.speed_target>min(self.speed_limit,load_limit(self.fixture_mass+self._mass()))+1e-9:raise ActionRejected('PRECONDITION_FAILED')
            self._operation_count+=1;self.operation_id=f'{self.instance_id}:shake:{self._operation_count}';self.phase='running';self.elapsed_s=0.;self.paused=False;self.hood_open_s=0.;r={'phase':self.phase,'operation_id':self.operation_id}
        elif capability=='shaking.stop':
            if self.phase=='running':self.phase='cancelled'
            self.paused=False;r={'phase':self.phase}
        else:return self._measure()
        self.revision+=1;return copy.deepcopy(r)
    def step(self,dt_s):
        if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<=0:raise ValueError('finite positive dt_s required')
        left=float(dt_s)
        while left>1e-10:
            active=self.powered and self.phase=='running' and not self.paused and self.hood<=1e-4;h=min(left,.1)
            if active and self.mode=='timed':h=min(h,self.duration-self.elapsed_s)
            if self.phase=='running' and self.paused:h=min(h,900-self.hood_open_s)
            target=self.speed_target if active else 0.;tau=2. if active else 1.;decay=math.exp(-h/tau);self.angle=(self.angle+target*h+(self.speed-target)*tau*(1-decay))%(2*math.pi);self.speed=target+(self.speed-target)*decay
            c=self._capacity();p=min(1000.,max(0.,150*(self.target-self.temperature-self.offset)+12*(self.target-self.ambient))) if self.powered and self.heater and self.hood<=1e-4 else 0.;self.heater_w=p
            ua=12. if self.hood<=1e-4 else 40.;eq=self.ambient+p/ua;tnew=eq+(self.temperature-eq)*math.exp(-ua*h/c);stored_change=c*(tnew-self.temperature);self.temperature=tnew;self.heat_in+=p*h;self.loss+=p*h-stored_change
            if self.temperature>=self.temperature_limit:
                self.heater=False;self.heater_w=0.;self.error='engineering_safety_temperature_trip'
            if active:
                self.elapsed_s+=h
                if self.mode=='timed' and self.elapsed_s>=self.duration-1e-8:self.elapsed_s=self.duration;self.phase='succeeded'
            if self.phase=='running' and self.paused:
                self.hood_open_s+=h
                if self.hood_open_s>=900-1e-8:self.phase='failed';self.paused=False;self.error='hood_open_timer_timeout'
            self.time_s+=h;left-=h
        self.revision+=1;return self.read('evaluator')
class IkaKs4000Session:
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
        meta=export_ika_ks4000_contract_metadata()
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
        response=self.invoke(instance_id=self._host.instance_id,capability_id='shaking.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])

def export_ika_ks4000_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'IkaKs4000Session.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key=='shaking.start'; pre=[]
        conditions={
         'shaking.start':('供电闭罩、夹具、固定负载和负载速度限制',['power.enabled','hood.angle_rad','fixture.installed','load.count','shaking.target_rad_s'],'powered and closed and fixture and all secured and target<=min(safety_limit,load_limit)'),
         'load.configure':('受信任宿主、开罩、余转停止、空位、总重含夹具不超20kg',['hood.angle_rad','shaking.actual_rad_s','fixture.installed','mass.retained_kg'],'hood>=1.4 and speed<0.001 and idle and slot empty and total<=20'),
         'load.unload':('受信任宿主、开罩静止且槽位有负载',['hood.angle_rad','shaking.actual_rad_s','load.count'],'hood>=1.4 and speed<0.001 and not running and slot exists'),
         'thermal.set_enabled':('开加热需闭罩供电，无错误且低于安全上限',['power.enabled','hood.angle_rad','cycle.error','safety.temperature_limit_k'],'not enabled or (powered and closed and not error and T<safe)'),
         'timer.configure':('运行中锁定计时模式',['cycle.phase'],'not running'),
         'fixture.configure':('受信任宿主、开罩静止、没有负载',['hood.angle_rad','shaking.actual_rad_s','load.count'],'open and stopped and empty')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.ika_ks4000.IkaKs4000Session.invoke',invocation=f'session.invoke(instance_id="ks4000_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；定时完成或显式停止；开罩暂停最多900s；取消保留余转和热状态' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电不回滚已经发生的计时、运动和热过程。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    mechanism=dict(component_ids=['chamber','heater','motor','eccentric_drive','orbit_x','platform','fixture_frame'],
      state_ids=['thermal.temperature_k','shaking.actual_rad_s','orbit.phase_rad','mass.input_kg','mass.retained_kg','mass.removed_kg','energy.heater_input_j','energy.loading_input_j','energy.stored_j','energy.loss_j','energy.unloading_output_j'],
      meaning_zh='不自转平台20mm圆轨道、负载限速、定时暂停和集总能量账本',implementation='lumped',
      equations=['omega_next=target+(omega-target)*exp(-dt/tau); phase integrates omega analytically',
      'x=0.01*cos(phase); y=0.01*sin(phase); platform orientation constant',
      'P=clip(150*(Ttarget-T-offset)+12*(Ttarget-Tambient),0,1000) W',
      'C=8000+sum(m_vessel_and_liquid*effective_cp); Tnext=Tambient+P/UA+(T-Tambient-P/UA)*exp(-UA*dt/C)',
      'mass_input=mass_retained+mass_removed',
      'heater_input+loading_enthalpy=stored_enthalpy+ambient_loss+unloading_enthalpy'],
      parameters={'orbit_radius':dict(value=.01,unit='m',meaning_zh='厂家20mm直径的一半',evidence='documented',source_ids=['manufacturer_primary'],uncertainty=None),
      'chamber_capacity':dict(value=8000.,unit='J/K',meaning_zh='箱体热容未实测，数值代理',evidence='assumption',source_ids=['simulation_assumption'],uncertainty=None)},
      source_ids=['manufacturer_manual','simulation_assumption'],limitations=['转速起动tau=2s、停转tau=1s，UA=12/40W/K闭/开罩均未标定。','负载加入时使用瞬时完全混合温度以守恒能量，不宣称真实样品均温或控温精度。','两个虚拟平移DOF只表示采样运动学，不是实物滑轨或负载动力学/晃液模型。','不输出培养、溶解、萃取效率或化学反应结果，物性模板不适用。'])
    _ann.annotate_export('ika_ks4000',caps,slots,{'incubated_orbit':mechanism},inputs={})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'incubated_orbit':mechanism})

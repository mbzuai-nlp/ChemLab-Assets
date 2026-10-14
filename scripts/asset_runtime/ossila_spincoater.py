"""CHEM-053 Standard V3. Finite film, host clock, no user acceleration control."""
from __future__ import annotations
import copy, importlib.util, json, math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SLUG='ossila_spincoater'; ASSET_TYPE_ID='instrument.'+SLUG; CATALOG_ID='CHEM-053'; PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/ossila_spincoater_film_library/templates.json'
RPM=2*math.pi/60; BOOL={'type':'boolean'}; TEXT={'type':'string'}

class ActionRejected(ValueError):
    """Rejected operations leave process state unchanged."""

def number(unit,low=None,high=None):
    s={'type':'number','x-unit':unit}
    if low is not None:s['minimum']=low
    if high is not None:s['maximum']=high
    return s

def obj(p):return dict(type='object',properties=p,required=list(p),additionalProperties=False)

def validate(s,v):
    t=s.get('type')
    if t in ('number','integer'):
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):raise ActionRejected('INVALID_ARGUMENT')
        if t=='integer' and type(v) is not int:raise ActionRejected('INVALID_ARGUMENT')
        if not s.get('minimum',-math.inf)<=v<=s.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
    elif t=='boolean' and type(v) is not bool:raise ActionRejected('INVALID_ARGUMENT')
    elif t=='string' and not isinstance(v,str):raise ActionRejected('INVALID_ARGUMENT')
    elif t=='object':
        if not isinstance(v,dict) or not set(s['required'])<=v.keys():raise ActionRejected('INVALID_ARGUMENT')
        for k,x in v.items():
            if k not in s['properties']:raise ActionRejected('INVALID_ARGUMENT')
            validate(s['properties'][k],x)
    elif t=='array':
        if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
        for x in v:validate(s['items'],x)
    if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')

PHASE=dict(type='string',enum=['idle','running','stopping','succeeded','cancelled'])
PLANT=obj({'temperature_k':number('K',283.15,313.15),'motor_acceleration_rad_s2':number('rad/s^2',1,2000),'motor_deceleration_rad_s2':number('rad/s^2',1,2000),'mass_transfer_m_s':number('m/s',0,.1),'lid_speed_rad_s':number('rad/s',.01,10),'accept_uncalibrated_model':BOOL})
PLANT_EXAMPLE=dict(temperature_k=298.15,motor_acceleration_rad_s2=200.,motor_deceleration_rad_s2=100.,mass_transfer_m_s=.001,lid_speed_rad_s=1.5,accept_uncalibrated_model=True)
METHOD=obj({'steps':dict(type='array',minItems=1,maxItems=50,items=obj({'speed_rad_s':number('rad/s',120*RPM,6000*RPM),'duration_s':number('s',1,1000)}))})
METHOD_EXAMPLE=dict(steps=[dict(speed_rad_s=1000*RPM,duration_s=2.),dict(speed_rad_s=3000*RPM,duration_s=4.)])
SAMPLE=obj({'solvent_id':TEXT,'solvent_kg':number('kg',1e-9,.002),'solute_kg':number('kg',0,.001),'solvent_density_kg_m3':number('kg/m^3',100,3000),'solute_density_kg_m3':number('kg/m^3',100,10000),'viscosity_pa_s':number('Pa*s',.00001,10),'substrate_width_m':number('m',.005,.05),'substrate_depth_m':number('m',.005,.075),'accept_uniform_film_and_compatibility':BOOL})
SAMPLE_EXAMPLE=dict(solvent_id='ethanol',solvent_kg=.00008,solute_kg=.000004,solvent_density_kg_m3=789.,solute_density_kg_m3=1200.,viscosity_pa_s=.003,substrate_width_m=.025,substrate_depth_m=.025,accept_uniform_film_and_compatibility=True)
RESULT=obj({'phase':PHASE,'speed_rad_s':number('rad/s',0),'elapsed_s':number('s',0),'step_index':dict(type='integer',minimum=0,maximum=50,**{'x-unit':'1'}),'evidence':TEXT,'template_source':TEXT})
PRODUCT_FIELDS={loc+'_'+sp+'_kg':number('kg',0) for loc in ('initial','film','waste','evaporated','collected') for sp in ('solvent','solute')}
PRODUCT_FIELDS.update(wet_thickness_m=number('m',0),dry_equivalent_thickness_m=number('m',0),evidence=TEXT,template_source=TEXT)
PRODUCT=obj(PRODUCT_FIELDS); CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=()):CAPS[k]=(i,o,c,z,kind,vis,ex or {},list(effects))
for key,s,ex,vis,c in [('plant',PLANT,PLANT_EXAMPLE,'evaluator','motor'),('method',METHOD,METHOD_EXAMPLE,'agent','display_digits'),('sample',SAMPLE,SAMPLE_EXAMPLE,'evaluator','substrate')]:
    cap(key+'.configure',s,obj({'configured':BOOL}),[c],'宿主参数/装样' if key!='method' else '标准版分步速度和时间；无加速度参数','configuration',vis,ex,[key+'.configured']+(['truth.ledger'] if key=='sample' else []))
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['switch_power'],'电源通断；断电启动减速',ex={'enabled':True},effects=['power.enabled','cycle.phase','motor.target_rad_s'])
cap('power.toggle',obj({}),obj({'enabled':BOOL}),['switch_power'],'电源开关翻转',effects=['power.enabled','cycle.phase','motor.target_rad_s'])
cap('lid.set_angle',obj({'angle_rad':number('rad',0,1.7)}),obj({'angle_rad':number('rad',0,1.7)}),['lid'],'开合盖宿主目标，开盖触发停止',ex={'angle_rad':1.5},effects=['lid.target_rad','cycle.phase','motor.target_rad_s'])
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['button_start','motor','chuck'],'闭盖上电且配置就绪后执行程序',effects=['cycle.phase','cycle.elapsed_s','cycle.step_index','motor.target_rad_s'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['button_stop','motor'],'中止程序减速，保留已发生材料转移',effects=['cycle.phase','motor.target_rad_s'])
cap('coating.measure',obj({}),RESULT,['display_digits'],'理想速度/时间面板，无膜厚传感器','query')
cap('product.read',obj({}),PRODUCT,['substrate','bowl'],'评价器液膜和有限材料账本','query','evaluator')
cap('product.collect',obj({'recipient_id':TEXT}),obj({'recipient_id':TEXT,'attachment_event':TEXT}),['substrate'],'停转开盖后宿主释放基片','device_command','evaluator',{'recipient_id':'sample_output'},['sample.configured','truth.ledger'])
cap('reference.lookup',obj({'substance_id':TEXT,'temperature_k':number('K',1)}),obj({'vapor_pressure_pa':number('Pa',0),'evidence':TEXT,'source':TEXT,'template_source':TEXT}),['substrate'],'原始温区内NIST蒸气压参考','query','agent',{'substance_id':'ethanol','temperature_k':298.15})
SLOTS={}; SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='body',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
for k in ('plant','method','sample'):slot(k+'.configured',BOOL,'1',k+'配置/装样标记','setpoint')
slot('power.enabled',BOOL,'1','电源目标','setpoint',c='switch_power',w=('power.set','enabled'))
slot('lid.target_rad',number('rad',0,1.7),'rad','盖目标角度，零闭合','setpoint',c='lid',w=('lid.set_angle','angle_rad'))
slot('lid.angle_rad',number('rad',0,1.7),'rad','盖实际教学运动学角度','scene',c='lid')
slot('motor.target_rad_s',number('rad/s',0,6000*RPM),'rad/s','当前步骤转速目标','setpoint',c='motor')
slot('motor.indicated_rad_s',number('rad/s',0,6000*RPM),'rad/s','理想转速示值；不代表2%性能校准',c='display_digits')
slot('chuck.angle_rad',number('rad',-math.pi,math.pi),'rad','相位主值映射卡盘关节','scene',c='chuck')
slot('cycle.phase',PHASE,'1','含减速阶段的程序状态',c='display_digits')
slot('cycle.elapsed_s',number('s',0),'s','累计运行时间；含加速为教学时序假设',c='display_digits')
slot('cycle.step_index',dict(type='integer',minimum=0,maximum=50,**{'x-unit':'1'}),'1','程序步骤编号，未启动为零',c='display_digits')
slot('truth.ledger',PRODUCT,'1','有限溶剂/非挥发溶质与推导膜厚，无真实膜厚传感器','process_truth','evaluator','substrate')

class OssilaSpincoaterRuntime:
    def __init__(self,instance_id='spincoater_01'):
        self.instance_id=instance_id;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']}
        self.time_s=0.;self.revision=0;self.powered=False;self.plant=None;self.method=None;self.sample=None;self.sample_present=False
        self.phase='idle';self.stop_result='cancelled';self.elapsed_s=0.;self.step_elapsed=0.;self.step_index=0
        self.lid_target=0.;self.lid_angle=0.;self.target_speed=0.;self.speed=0.;self.angle=0.
        self.initial=[0.,0.];self.film=[0.,0.];self.waste=[0.,0.];self.evaporated=[0.,0.];self.collected=[0.,0.]
        self.operation_id='';self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
    def register_principal(self,principal_id,visibility='agent'):
        if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id='__public_agent__'):
        if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return OssilaSpincoaterSession(self,principal_id)
    def describe(self):return self.session().describe(instance_id=self.instance_id)
    def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k!='templates'})
    def _reference(self,sid,t):
        if sid not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
        r=self.templates[sid];fits=r.get('fits',r['values']['antoine_fits']);ok=[f for f in fits if f['temperature_range_k'][0]<=t<=f['temperature_range_k'][1]]
        if not ok:raise ActionRejected('REFERENCE_OUT_OF_RANGE')
        f=ok[0];return dict(vapor_pressure_pa=1e5*10**(f['A']-f['B_k']/(t+f['C_k'])),evidence='derived_NIST_Antoine_no_extrapolation',source=r['source'],template_source=TEMPLATE_REL)
    def _product(self):
        p={loc+'_'+sp+'_kg':v[i] for loc,v in [('initial',self.initial),('film',self.film),('waste',self.waste),('evaporated',self.evaporated),('collected',self.collected)] for i,sp in enumerate(('solvent','solute'))}
        area=self.sample['substrate_width_m']*self.sample['substrate_depth_m'] if self.sample else 1.
        vol=sum(m/r for m,r in zip(self.film,[self.sample['solvent_density_kg_m3'],self.sample['solute_density_kg_m3']])) if self.sample else 0.
        dry=self.film[1]/self.sample['solute_density_kg_m3']/area if self.sample else 0.
        p.update(wet_thickness_m=vol/area,dry_equivalent_thickness_m=dry,evidence='synthetic_uniform_film;real_NIST_solvent_reference;NOT_measured_thickness',template_source=TEMPLATE_REL);return p
    def read(self,visibility='agent'):
        if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
        v={'plant.configured':self.plant is not None,'method.configured':self.method is not None,'sample.configured':self.sample_present,'power.enabled':self.powered,'lid.target_rad':self.lid_target,'lid.angle_rad':self.lid_angle,'motor.target_rad_s':self.target_speed,'motor.indicated_rad_s':self.speed,'chuck.angle_rad':self.angle,'cycle.phase':self.phase,'cycle.elapsed_s':self.elapsed_s,'cycle.step_index':self.step_index,'truth.ledger':self._product()}
        return copy.deepcopy({k:x for k,x in v.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
    def _stop(self,result='cancelled'):
        if self.phase in ('running','stopping'):
            self.target_speed=0.;self.stop_result=result;self.phase='stopping' if self.speed>1e-12 else result
    def invoke(self,capability,**a):
        if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability][0],a)
        if capability.endswith('.configure') and self.phase in ('running','stopping'):raise ActionRejected('BUSY')
        if capability=='plant.configure':
            if self.plant or not a['accept_uncalibrated_model']:raise ActionRejected('PRECONDITION_FAILED')
            self.plant=copy.deepcopy(a);out={'configured':True}
        elif capability=='method.configure':self.method=copy.deepcopy(a);out={'configured':True}
        elif capability=='sample.configure':
            if not self.plant or self.sample is not None or self.speed>1e-12 or self.lid_angle<1. or not a['accept_uniform_film_and_compatibility']:raise ActionRejected('PRECONDITION_FAILED')
            w,d=a['substrate_width_m'],a['substrate_depth_m']
            if not any(abs(w-x)<1e-9 and abs(d-y)<1e-9 for x,y in [(.020,.015),(.025,.025),(.05,.05),(.025,.075)]):raise ActionRejected('UNSUPPORTED_CHUCK')
            self._reference(a['solvent_id'],self.plant['temperature_k'])
            vol=a['solvent_kg']/a['solvent_density_kg_m3']+a['solute_kg']/a['solute_density_kg_m3']
            if vol/(w*d)>.003:raise ActionRejected('PRECONDITION_FAILED')
            self.sample=copy.deepcopy(a);self.sample_present=True;self.initial=[a['solvent_kg'],a['solute_kg']];self.film=self.initial.copy();out={'configured':True}
        elif capability in ('power.set','power.toggle'):
            self.powered=a['enabled'] if capability=='power.set' else not self.powered
            if not self.powered:self._stop()
            out={'enabled':self.powered}
        elif capability=='lid.set_angle':
            if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
            self.lid_target=a['angle_rad']
            if self.lid_target>.001:self._stop()
            out=a.copy()
        elif capability=='acquisition.start':
            if not all([self.plant,self.method,self.sample_present,self.powered]) or self.lid_angle>.001 or self.lid_target>.001 or self.speed>1e-12 or self.phase in ('running','stopping'):raise ActionRejected('PRECONDITION_FAILED')
            self.phase='running';self.elapsed_s=0.;self.step_elapsed=0.;self.step_index=1;self.target_speed=self.method['steps'][0]['speed_rad_s'];self._operation_count+=1;self.operation_id=self.instance_id+':spin:'+str(self._operation_count);out=dict(phase=self.phase,operation_id=self.operation_id)
        elif capability=='acquisition.stop':self._stop();out={'phase':self.phase}
        elif capability=='coating.measure':return dict(phase=self.phase,speed_rad_s=self.speed,elapsed_s=self.elapsed_s,step_index=self.step_index,evidence='ideal_panel_proxy_not_thickness_sensor',template_source=TEMPLATE_REL)
        elif capability=='product.read':return self._product()
        elif capability=='reference.lookup':return self._reference(a['substance_id'],a['temperature_k'])
        elif capability=='product.collect':
            if not self.sample_present or self.speed>1e-12 or self.lid_angle<1. or self.phase in ('running','stopping'):raise ActionRejected('PRECONDITION_FAILED')
            self.collected=[x+y for x,y in zip(self.collected,self.film)];self.film=[0.,0.];self.sample_present=False;out=dict(recipient_id=a['recipient_id'],attachment_event='host_release_substrate')
        else:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability][1],out);self.revision+=1;return copy.deepcopy(out)
    def _film_step(self,dt,omega):
        if not self.sample_present or self.film[0]<=1e-20:return
        s=self.sample;area=s['substrate_width_m']*s['substrate_depth_m'];rho=[s['solvent_density_kg_m3'],s['solute_density_kg_m3']];vol=sum(m/r for m,r in zip(self.film,rho));h=vol/area;density=sum(self.film)/vol;fraction=self.film[1]/rho[1]/vol
        mu=s['viscosity_pa_s']*math.exp(min(14.,5*fraction/max(.01,1-fraction)));k=2*density*omega*omega/(3*mu);remaining=1/math.sqrt(1+2*k*h*h*dt)
        for i in range(2):
            expelled=self.film[i]*(1-remaining);self.film[i]-=expelled;self.waste[i]+=expelled
        pressure=self._reference(s['solvent_id'],self.plant['temperature_k'])['vapor_pressure_pa'];mw=self.templates[s['solvent_id']]['molar_mass_kg_mol'];f=self.film[0]/max(1e-30,sum(self.film))
        rate=self.plant['mass_transfer_m_s']*area*pressure*mw/(8.314462618*self.plant['temperature_k'])*f;ev=min(self.film[0],rate*dt);self.film[0]-=ev;self.evaporated[0]+=ev
    def step(self,dt_s):
        validate(number('s',1e-12,1000),dt_s);remaining=float(dt_s)
        while remaining>1e-12:
            dt=min(remaining,.01)
            if self.phase=='running':dt=min(dt,self.method['steps'][self.step_index-1]['duration_s']-self.step_elapsed)
            if self.plant:
                move=self.plant['lid_speed_rad_s']*dt;self.lid_angle+=max(-move,min(move,self.lid_target-self.lid_angle));target=self.target_speed if self.powered and self.lid_angle<=.001 else 0.;accel=self.plant['motor_acceleration_rad_s2'] if target>self.speed else self.plant['motor_deceleration_rad_s2'];before=self.speed;self.speed+=max(-accel*dt,min(accel*dt,target-self.speed));omega=(before+self.speed)/2;self.angle=(self.angle+omega*dt+math.pi)%(2*math.pi)-math.pi;self._film_step(dt,omega)
            if self.phase=='running':
                self.step_elapsed+=dt;self.elapsed_s+=dt
                if self.step_elapsed>=self.method['steps'][self.step_index-1]['duration_s']-1e-10:
                    if self.step_index==len(self.method['steps']):self._stop('succeeded')
                    else:self.step_index+=1;self.step_elapsed=0.;self.target_speed=self.method['steps'][self.step_index-1]['speed_rad_s']
            elif self.phase=='stopping' and self.speed<=1e-12:self.speed=0.;self.phase=self.stop_result
            self.time_s+=dt;remaining-=dt
        self.revision+=1;return self.read('evaluator')

class OssilaSpincoaterSession:
    """Only the trusted host assigns roles; returned data obey the same boundary."""
    def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
    @property
    def _role(self):return self._host._principals[self._principal]
    def _target(self,i):
        if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
    def describe(self,*,instance_id=None,asset_type_id=None):
        if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None:self._target(instance_id)
        if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
        m=export_ossila_spincoater_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=h.time_s,sample_window_s=None) for k in ids})
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
        except ActionRejected as exc:return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,status='rejected',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))
    def write(self,*,instance_id,state_id,value,request_id):
        self._target(instance_id)
        if state_id not in SLOTS or not SLOTS[state_id][5]:raise ActionRejected('ACCESS_DENIED')
        cap,arg=SLOTS[state_id][5];return self.invoke(instance_id=instance_id,capability_id=cap,arguments={arg:value},request_id=request_id)
    def poll(self,operation_id):
        h=self._host
        if operation_id!=h.operation_id or h._owners.get(operation_id)!=self._principal:raise ActionRejected('ACCESS_DENIED')
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('coating.measure'))
    def cancel(self,operation_id,request_id):
        self.poll(operation_id);return self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)

def export_ossila_spincoater_contract_metadata():
    caps={};slots={}
    for k,(s,u,o,v,c,w) in SLOTS.items():slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'OssilaSpincoaterSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    evolving=['lid.angle_rad','motor.indicated_rad_s','chuck.angle_rad','cycle.phase','cycle.elapsed_s','cycle.step_index','motor.target_rad_s','truth.ledger']
    for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
        async_=k=='acquisition.start';query=kind=='query'
        caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.ossila_spincoater.OssilaSpincoaterSession.invoke',invocation=f'session.invoke(instance_id="spincoater_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if query else [dict(description_zh='停转/闭盖/上电/装样/温区及宿主身份检查',state_ids=['cycle.phase','lid.angle_rad','power.enabled'],enforced_by='runtime',check='OssilaSpincoaterRuntime.invoke guards; Session identity')],state_effects=[] if query else [dict(state_id=s,timing='immediate',description_zh='事务写入配置/目标或装取样账本') for s in effects]+[dict(state_id=s,timing='on_step',description_zh='程序、姿态和有限材料随宿主step演化') for s in evolving],observes=[],model_effects=dict(mode='none',description_zh='场景求值另行验证',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='所有步骤完成且减速到零；不表示膜干燥/合格' if async_ else '事务完成不推进时钟',completion_state_ids=['cycle.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','finite_sample','request_cache'],partial_failure_zh='取消不回滚膜/废液/蒸发，减速及自然挥发继续；幂等仅本进程生命周期'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','PRECONDITION_FAILED','BUSY','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','REFERENCE_OUT_OF_RANGE','UNSUPPORTED_CHUCK']],example_arguments=copy.deepcopy(ex),verification=[],limitations=['Standard V3无可调加速度、真空泵或膜厚传感器'],source_ids=['manufacturer_primary','simulation_assumption'])
    mech=dict(component_ids=['motor','chuck','substrate','bowl'],state_ids=list(slots),meaning_zh='均匀Newtonian液膜离心排液与溶剂挥发',implementation='lumped',equations=['domega/dt=host acceleration/deceleration; not Standard user setting','dh/dt=-2*rho*omega^2*h^3/(3*mu); exact positive substep h_new=h/sqrt(1+4*rho*omega^2*h^2*dt/(3*mu))','expelled fraction transfers solvent and solute to finite waste','P_sat/Pa=100000*10^(A-B/(T/K+C)); original NIST temperature interval only','evap_kg_s=k_mass*area*P_sat*M/(R*T)*solvent_mass_fraction; limited by inventory','mu=mu_host*exp(min(14,5*solute_volume_fraction/(1-solute_volume_fraction))) is assumption','initial_species=film+waste+evaporated+collected','wet_thickness=sum(mass_i/density_i)/area; dry_equivalent=solute_mass/(solute_density*area)'],parameters={},source_ids=['nist_vapor_pressure','simulation_assumption'],limitations=['无CFD、非牛顿流变、径向膜分布、结晶、边缘珠、挥发冷却和热能闭合。','65实源蒸气压参考不是50种本机实测膜厚曲线；厚度synthetic。','仅标准四尺寸基片语义，外观25×25mm；定制100mm卡盘、注射泵/脚踏附件未实现。','含加速的步骤时序和<2%稳定性未按实机标定。','角色过滤不代表进程/文件部署隔离；Runtime原始入口仅供受信任宿主。'])
    spec=importlib.util.spec_from_file_location('_spin_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'spin_coating':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.configure':(SAMPLE,SAMPLE_EXAMPLE)});mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'spin_coating':mech})

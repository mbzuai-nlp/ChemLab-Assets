"""CHEM-055 Current100mm glass configuration. Finite bead, blade film, SI host clock; not calibrated."""
from __future__ import annotations
import copy, importlib.util, json, math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SLUG='ossila_doctorblade'; ASSET_TYPE_ID='instrument.'+SLUG; CATALOG_ID='CHEM-055'; PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/ossila_doctorblade_film_library/templates.json'
BOOL={'type':'boolean'}; TEXT={'type':'string'}

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



SPEED=number('m/s',.00005,.25);POS=number('m',0,.29);GAP=number('m',0,.01)
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
PLANT=obj({'temperature_k':number('K',278.15,313.15),'mass_transfer_m_s':number('m/s',0,.1),'manual_gap_speed_m_s':number('m/s',.00001,.1),'accept_uncalibrated_model':BOOL})
PLANT_EXAMPLE=dict(temperature_k=298.15,mass_transfer_m_s=.0002,manual_gap_speed_m_s=.005,accept_uncalibrated_model=True)
METHOD=obj({'distance_m':number('m',.001,.29),'speed_m_s':SPEED})
METHOD_EXAMPLE=dict(distance_m=.26,speed_m_s=.02)
CHARGE=obj({'solvent_id':TEXT,'solvent_kg':number('kg',1e-9,.1),'solute_kg':number('kg',0,.1),'solvent_density_kg_m3':number('kg/m^3',100,3000),'solute_density_kg_m3':number('kg/m^3',100,10000),'viscosity_pa_s':number('Pa*s',.0001,100),'reservoir_area_m2':number('m^2',.0001,.01),'blade_contact_length_m':number('m',.001,.02),'accept_newtonian_compatibility':BOOL})
CHARGE_EXAMPLE=dict(solvent_id='ethanol',solvent_kg=.0008,solute_kg=.0018,solvent_density_kg_m3=789.,solute_density_kg_m3=2500.,viscosity_pa_s=.5,reservoir_area_m2=.0025,blade_contact_length_m=.005,accept_newtonian_compatibility=True)
SAMPLE=obj({'width_m':number('m',.001,.1),'length_m':number('m',.001,.29),'start_m':POS,'accept_host_attachment':BOOL})
SAMPLE_EXAMPLE=dict(width_m=.1,length_m=.2,start_m=.02,accept_host_attachment=True)
PRODUCT_FIELDS={loc+'_'+sp+'_kg':number('kg',0) for loc in ('initial','bead','film','waste','evaporated','collected') for sp in ('solvent','solute')}
PRODUCT_FIELDS.update(wet_thickness_m=number('m',0),dry_equivalent_thickness_m=number('m',0),film_profile_m=dict(type='array',items=number('m',0),minItems=64,maxItems=64),evidence=TEXT,template_source=TEXT)
PRODUCT=obj(PRODUCT_FIELDS)
RESULT=obj({'phase':PHASE,'stage':TEXT,'position_m':POS,'speed_m_s':number('m/s',-.25,.25),'left_gap_m':GAP,'right_gap_m':GAP,'elapsed_s':number('s',0),'evidence':TEXT,'template_source':TEXT})
CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=()):CAPS[k]=(i,o,c,z,kind,vis,ex or {},list(effects))
for key,s,ex,vis,c in [('plant',PLANT,PLANT_EXAMPLE,'evaluator','motor'),('method',METHOD,METHOD_EXAMPLE,'agent','display_digits'),('charge',CHARGE,CHARGE_EXAMPLE,'evaluator','reservoir'),('substrate',SAMPLE,SAMPLE_EXAMPLE,'evaluator','substrate')]:
    cap(key+'.configure',s,obj({'configured':BOOL}),[c],'配置/有限装料/固定基板宿主附着','configuration',vis,ex,[key+'.configured']+(['truth.ledger'] if key in ['charge','substrate'] else []))
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['switch_power'],'后部电源；急停释放后须关开电源才复位',ex={'enabled':True},effects=['power.enabled','stage.homed','cycle.phase','safety.latched'])
cap('power.toggle',obj({}),obj({'enabled':BOOL}),['switch_power'],'后部电源开关翻转',effects=['power.enabled','stage.homed','cycle.phase','safety.latched'])
cap('stage.home',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['button_return','motor','carriage'],'规定速度回后端；空料无膜时才允许',effects=['cycle.phase','stage.target_m'])
cap('stage.move_to',obj({'position_m':POS,'speed_m_s':SPEED}),obj({'phase':PHASE,'operation_id':TEXT}),['carriage','motor'],'无料轴定位，非自动涂布',ex={'position_m':.05,'speed_m_s':.02},effects=['cycle.phase','stage.target_m'])
cap('gap.set',obj({'left_m':GAP,'right_m':GAP}),obj({'left_m':GAP,'right_m':GAP}),['blade','gauge_left','gauge_right'],'宿主手动双端调隙目标；非机载电动执行','configuration','agent',{'left_m':.0001,'right_m':.0001},['gap.left_target_m','gap.right_target_m'])
cap('emergency.stop',obj({}),obj({'latched':BOOL}),['button_emergency'],'急停锁存；驱动停止且回零失效',effects=['safety.pressed','safety.latched','power.enabled','stage.homed','cycle.phase'])
cap('emergency.release',obj({}),obj({'released':BOOL}),['button_emergency'],'宿主顺时针释放按钮，尚未重置驱动故障','configuration','agent',{},['safety.pressed'])
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['button_start','motor','carriage','blade'],'单次恒速前向刮涂，有限浆料',effects=['cycle.phase','cycle.elapsed_s','stage.target_m'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['button_stop','motor'],'硬停止；保留部分成膜和残余料',effects=['cycle.phase','stage.target_m'])
cap('coating.measure',obj({}),RESULT,['display_digits','gauge_left','gauge_right'],'理想位置/速度/间隙读数；不是膜厚仪','query')
cap('product.read',obj({}),PRODUCT,['substrate','reservoir','waste_tray'],'有限浆料、膜、废料、蒸发评价账本','query','evaluator')
cap('product.collect',obj({'recipient_id':TEXT}),obj({'recipient_id':TEXT,'attachment_event':TEXT}),['substrate'],'停止后宿主释放基板并转移膜账本','device_command','evaluator',{'recipient_id':'coated_slide'},['substrate.configured','truth.ledger'])
cap('reservoir.empty',obj({}),obj({'emptied':BOOL}),['reservoir','waste_tray'],'停止后宿主清空残余料至废料，非自驱清洗','configuration','evaluator',{},['charge.configured','truth.ledger'])
cap('reference.lookup',obj({'substance_id':TEXT,'temperature_k':number('K',1)}),obj({'vapor_pressure_pa':number('Pa',0),'evidence':TEXT,'source':TEXT,'template_source':TEXT}),['reservoir'],'NIST有效原始温区蒸气压参考','query','agent',{'substance_id':'ethanol','temperature_k':298.15})
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='body',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
for key in ['plant','method','charge','substrate']:slot(key+'.configured',BOOL,'1',key+'配置或存在标记','setpoint')
slot('power.enabled',BOOL,'1','驱动电源有效；急停时为否',c='switch_power',w=('power.set','enabled'))
slot('stage.homed',BOOL,'1','本次通电已回零，断电急停失效',c='carriage')
slot('stage.target_m',POS,'m','后端为零，沿-Y前进的目标','setpoint',c='carriage')
slot('stage.position_m',POS,'m','沿-Y运动学位移理想示值','scene',c='carriage')
slot('stage.speed_m_s',number('m/s',-.25,.25),'m/s','前向正后向负理想速度',c='display_digits')
for end in ['left','right']:
    slot('gap.'+end+'_target_m',GAP,'m','手动调节'+end+'端目标，1µm网格','setpoint',c='gauge_'+end)
    slot('gap.'+end+'_m',GAP,'m','手动调节'+end+'端理想数字示值，未标定3µm精度','scene',c='gauge_'+end)
slot('gap.mean_m',GAP,'m','刮刀平均升起量，非湿膜厚度','scene',c='blade_lift')
slot('gap.tilt_rad',number('rad',-.1,.1),'rad','刮刀绕Y倾斜角，右高为负','scene',c='blade')
slot('safety.pressed',BOOL,'1','急停按钮宿主事件，不是机器人触发证明','scene',c='button_emergency')
slot('safety.latched',BOOL,'1','必须释放后关开电源的故障锁存',c='button_emergency')
slot('cycle.phase',PHASE,'1','回零/定位/涂布异步状态',c='display_digits')
slot('cycle.stage',TEXT,'1','homing/manual/coating/stopped/idle',c='display_digits')
slot('cycle.elapsed_s',number('s',0),'s','本次操作宿主累计时间',c='display_digits')
slot('truth.ledger',PRODUCT,'1','64条带薄膜/有限浆料/废料/蒸发/收集物种账本','process_truth','evaluator','substrate')

class OssilaDoctorbladeRuntime:
    """Trusted host controls time and manual attachment; no hardware/robot driver."""
    def __init__(self,instance_id='doctorblade_01'):
        self.instance_id=instance_id;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']}
        self.time_s=0.;self.revision=0;self.power_switch=False;self.powered=False;self.homed=False;self.pressed=False;self.latched=False;self.reset_off=False
        self.plant=None;self.method=None;self.charge_cfg=None;self.sample=None;self.sample_present=False
        self.q=0.;self.target=0.;self.speed=0.;self.manual_speed=.02;self.gaps=[0.,0.];self.gap_targets=[0.,0.]
        self.phase='idle';self.stage='idle';self.elapsed_s=0.;self.initial=[0.,0.];self.bead=[0.,0.];self.waste=[0.,0.];self.evaporated=[0.,0.];self.collected=[0.,0.];self.film=[[0.,0.] for _ in range(64)];self.evap_flux=0.
        self.operation_id='';self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
    def register_principal(self,principal_id,visibility='agent'):
        if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id='__public_agent__'):
        if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return OssilaDoctorbladeSession(self,principal_id)
    def describe(self):return self.session().describe(instance_id=self.instance_id)
    def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k!='templates'})
    def _reference(self,sid,t):
        if sid not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
        r=self.templates[sid];fits=[f for f in r['fits'] if f['temperature_range_k'][0]<=t<=f['temperature_range_k'][1]]
        if not fits:raise ActionRejected('REFERENCE_OUT_OF_RANGE')
        f=fits[0];return dict(vapor_pressure_pa=1e5*10**(f['A']-f['B_k']/(t+f['C_k'])),evidence='derived_NIST_Antoine_no_extrapolation',source=r['source'],template_source=TEMPLATE_REL)

    def _volume(self,m):
        c=self.charge_cfg
        return m[0]/c['solvent_density_kg_m3']+m[1]/c['solute_density_kg_m3'] if c else 0.
    def _product(self):
        films=[sum(f[i] for f in self.film) for i in range(2)]
        p={loc+'_'+sp+'_kg':m[i] for loc,m in [('initial',self.initial),('bead',self.bead),('film',films),('waste',self.waste),('evaporated',self.evaporated),('collected',self.collected)] for i,sp in enumerate(['solvent','solute'])}
        area=self.sample['width_m']*self.sample['length_m'] if self.sample else 1.;rho=self.charge_cfg['solute_density_kg_m3'] if self.charge_cfg else 1.
        profile=[self._volume(f)/(area/64) for f in self.film]
        p.update(wet_thickness_m=sum(profile)/64,dry_equivalent_thickness_m=films[1]/rho/area,film_profile_m=profile,evidence='synthetic_Newtonian_parallelplate_film;real_NIST_reference;NOT_measured_thickness',template_source=TEMPLATE_REL)
        return p
    def read(self,visibility='agent'):
        if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
        v={'plant.configured':self.plant is not None,'method.configured':self.method is not None,'charge.configured':self.charge_cfg is not None,'substrate.configured':self.sample_present,'power.enabled':self.powered,'stage.homed':self.homed,'stage.target_m':self.target,'stage.position_m':self.q,'stage.speed_m_s':self.speed,'gap.left_target_m':self.gap_targets[0],'gap.right_target_m':self.gap_targets[1],'gap.left_m':self.gaps[0],'gap.right_m':self.gaps[1],'gap.mean_m':sum(self.gaps)/2,'gap.tilt_rad':-math.atan2(self.gaps[1]-self.gaps[0],.1),'safety.pressed':self.pressed,'safety.latched':self.latched,'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.elapsed_s':self.elapsed_s,'truth.ledger':self._product()}
        return copy.deepcopy({k:x for k,x in v.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
    def _stop(self,result='cancelled'):
        if self.phase=='running':self.phase=result;self.stage='stopped';self.target=self.q;self.speed=0.
    def _new_operation(self,stage,target):
        self.phase='running';self.stage=stage;self.target=target;self.elapsed_s=0.;self._operation_count+=1
        self.operation_id=self.instance_id+':motion:'+str(self._operation_count)
        return dict(phase=self.phase,operation_id=self.operation_id)
    def _model_check(self):
        c=self.charge_cfg
        if max(self.gaps)/c['blade_contact_length_m']>.1 or min(self.gaps)<=0:raise ActionRejected('MODEL_OUT_OF_RANGE')
        if self.sample['width_m']!=.1:raise ActionRejected('MODEL_OUT_OF_RANGE')
    @staticmethod
    def _grid(value,quantum):
        if abs(value/quantum-round(value/quantum))>1e-6:raise ActionRejected('INVALID_ARGUMENT')
    def invoke(self,capability,**a):
        if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability][0],a)
        if capability=='plant.configure':
            if self.plant or not a['accept_uncalibrated_model']:raise ActionRejected('PRECONDITION_FAILED')
            self.plant=copy.deepcopy(a);out={'configured':True}
        elif capability=='method.configure':
            if self.phase=='running':raise ActionRejected('BUSY')
            self._grid(a['speed_m_s'],.00001);self.method=a.copy();out={'configured':True}
        elif capability=='substrate.configure':
            if self.phase=='running' or self.sample_present or self.q!=0 or not a['accept_host_attachment']:raise ActionRejected('PRECONDITION_FAILED')
            if a['start_m']+a['length_m']>.29:raise ActionRejected('INVALID_GEOMETRY')
            self.sample=a.copy();self.sample_present=True;out={'configured':True}
        elif capability=='charge.configure':
            if not self.plant or self.charge_cfg or self.phase=='running' or not a['accept_newtonian_compatibility']:raise ActionRejected('PRECONDITION_FAILED')
            ref=self._reference(a['solvent_id'],self.plant['temperature_k'])
            volume=a['solvent_kg']/a['solvent_density_kg_m3']+a['solute_kg']/a['solute_density_kg_m3']
            if volume/a['reservoir_area_m2']>.02:raise ActionRejected('RESERVOIR_OVERFLOW')
            self.charge_cfg=a.copy();self.bead=[a['solvent_kg'],a['solute_kg']];self.initial=self.bead.copy()
            self.evap_flux=self.plant['mass_transfer_m_s']*ref['vapor_pressure_pa']*self.templates[a['solvent_id']]['molar_mass_kg_mol']/(8.314462618*self.plant['temperature_k']);out={'configured':True}
        elif capability in ('power.set','power.toggle'):
            enabled=a['enabled'] if capability=='power.set' else not self.power_switch
            if enabled and self.latched and (self.pressed or not self.reset_off):raise ActionRejected('PRECONDITION_FAILED')
            self.power_switch=enabled
            if not enabled:self.powered=False;self.homed=False;self._stop();self.reset_off=self.latched and not self.pressed
            else:self.powered=True;self.latched=False;self.reset_off=False
            out={'enabled':self.powered}
        elif capability=='gap.set':
            if not self.plant or self.phase=='running':raise ActionRejected('PRECONDITION_FAILED')
            self._grid(a['left_m'],1e-6);self._grid(a['right_m'],1e-6)
            self.gap_targets=[a['left_m'],a['right_m']];out=a.copy()
        elif capability=='emergency.stop':
            self.pressed=True;self.latched=True;self.reset_off=False;self.powered=False;self.homed=False;self._stop('failed');out={'latched':True}
        elif capability=='emergency.release':
            self.pressed=False;out={'released':True}
        elif capability in ('stage.home','stage.move_to','acquisition.start'):
            if not self.plant or not self.powered or self.latched or self.phase=='running':raise ActionRejected('PRECONDITION_FAILED')
            if capability=='acquisition.start':
                if not all([self.method,self.charge_cfg,self.sample_present,self.homed]) or self.q!=0 or any(abs(a-b)>1e-12 for a,b in zip(self.gaps,self.gap_targets)):raise ActionRejected('PRECONDITION_FAILED')
                if self.bead[0]<=1e-15:raise ActionRejected('RESERVOIR_EXHAUSTED')
                self._model_check();out=self._new_operation('coating',self.method['distance_m'])
            else:
                if sum(self.bead)>1e-15 or sum(map(sum,self.film))>1e-15:raise ActionRejected('PRECONDITION_FAILED')
                if capability=='stage.home':out=self._new_operation('homing',0.)
                else:
                    if not self.homed:raise ActionRejected('PRECONDITION_FAILED')
                    self._grid(a['speed_m_s'],.00001);self.manual_speed=a['speed_m_s'];out=self._new_operation('manual',a['position_m'])
        elif capability=='acquisition.stop':self._stop();out={'phase':self.phase}
        elif capability=='coating.measure':
            return dict(phase=self.phase,stage=self.stage,position_m=self.q,speed_m_s=self.speed,left_gap_m=self.gaps[0],right_gap_m=self.gaps[1],elapsed_s=self.elapsed_s,evidence='ideal_panel_and_gauges_NOT_thickness_sensor',template_source=TEMPLATE_REL)
        elif capability=='product.read':return self._product()
        elif capability=='reference.lookup':return self._reference(a['substance_id'],a['temperature_k'])
        elif capability=='product.collect':
            if self.phase=='running' or not self.sample_present:raise ActionRejected('PRECONDITION_FAILED')
            for i in range(2):self.collected[i]+=sum(f[i] for f in self.film)
            self.film=[[0.,0.] for _ in range(64)];self.sample_present=False
            out=dict(recipient_id=a['recipient_id'],attachment_event='host_release_substrate')
        elif capability=='reservoir.empty':
            if self.phase=='running':raise ActionRejected('PRECONDITION_FAILED')
            for i in range(2):self.waste[i]+=self.bead[i];self.bead[i]=0.
            # Configuration retained so densities and historical ledger remain meaningful; refill is unsupported.
            out={'emptied':True}
        else:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability][1],out);self.revision+=1;return copy.deepcopy(out)
    def _deposit(self,lo,hi,speed):
        if hi<=lo or not self.charge_cfg or self._volume(self.bead)<=1e-30:return
        c=self.charge_cfg;s=self.sample;dl=s['length_m']/64;cuts=[lo]+[s['start_m']+i*dl for i in range(65) if lo<s['start_m']+i*dl<hi]+[hi]
        left,right=self.gaps;mean=(left+right)/2;cube=(left**3+left**2*right+left*right**2+right**3)/4
        a=.1*mean/2
        for start,end in zip(cuts,cuts[1:]):
            volume=self._volume(self.bead)
            if volume<=1e-30:break
            rho=sum(self.bead)/volume;b=.1*rho*9.80665*cube/(12*c['viscosity_pa_s']*c['blade_contact_length_m']*speed*c['reservoir_area_m2'])
            dx=end-start
            remaining=max(0.,volume*math.exp(-b*dx)-a*(-math.expm1(-b*dx))/b) if b>1e-20 else max(0.,volume-a*dx)
            fraction=min(1.,max(0.,(volume-remaining)/volume))
            mid=(start+end)/2;idx=math.floor((mid-s['start_m'])/dl)
            dest=self.film[idx] if 0<=idx<64 else self.waste
            for sp in range(2):
                mass=self.bead[sp]*fraction;self.bead[sp]-=mass;dest[sp]+=mass
    def _evaporate(self,dt):
        if not self.charge_cfg:return
        area=self.sample['width_m']*self.sample['length_m']/64 if self.sample else 0.
        # Waste is a covered bookkeeping receiver; no waste evaporation is claimed.
        for masses,exposed in [(self.bead,self.charge_cfg['reservoir_area_m2'])]+[(f,area) for f in self.film]:
            ev=min(masses[0],self.evap_flux*exposed*masses[0]/max(sum(masses),1e-30)*dt)
            masses[0]-=ev;self.evaporated[0]+=ev
    def step(self,dt_s):
        validate(number('s',1e-12,1000),dt_s);remaining=float(dt_s)
        while remaining>1e-12:
            dt=min(.02,remaining)
            if self.phase=='running':
                distance=self.target-self.q
                if abs(distance)<1e-12:
                    self.q=self.target;self.homed=self.homed or self.stage=='homing';self.phase='succeeded';self.stage='idle';self.speed=0.;continue
                speed=self.method['speed_m_s'] if self.stage=='coating' else .02 if self.stage=='homing' else self.manual_speed
                dt=min(dt,abs(distance)/speed);before=self.q;self.q+=math.copysign(speed*dt,distance);self.speed=math.copysign(speed,distance)
                if self.stage=='coating':self._deposit(before,self.q,speed)
                self.elapsed_s+=dt
                if abs(self.q-self.target)<1e-11:
                    self.q=self.target;self.homed=self.homed or self.stage=='homing';self.phase='succeeded';self.stage='idle';self.speed=0.
            else:self.speed=0.
            if self.plant:
                for i in range(2):
                    dx=self.plant['manual_gap_speed_m_s']*dt;self.gaps[i]+=max(-dx,min(dx,self.gap_targets[i]-self.gaps[i]))
            self._evaporate(dt);self.time_s+=dt;remaining-=dt
        self.revision+=1;return self.read('evaluator')
class OssilaDoctorbladeSession:
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
        m=export_ossila_doctorblade_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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



def export_ossila_doctorblade_contract_metadata():
    caps={};slots={}
    for k,(s,u,o,v,c,w) in SLOTS.items():
        slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',
            read_binding=dict(backend='host',artifact_id='runtime',accessor=f'OssilaDoctorbladeSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,
            sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    evolving=['stage.position_m','stage.speed_m_s','stage.homed','gap.left_m','gap.right_m','gap.mean_m','gap.tilt_rad','cycle.phase','cycle.stage','cycle.elapsed_s','truth.ledger']
    for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
        asynchronous=k in ['acquisition.start','stage.home','stage.move_to'];query=kind=='query'
        caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
            implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
            binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.ossila_doctorblade.OssilaDoctorbladeSession.invoke',invocation=f'session.invoke(instance_id="doctorblade_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),
            preconditions=[] if query else [dict(description_zh='停止/上电/回零/急停复位/有限料/薄隙Newtonian条件，宿主身份',state_ids=['cycle.phase','power.enabled','stage.homed','safety.latched'],enforced_by='runtime',check='Runtime.invoke guards; Session trusted principal policy')],
            state_effects=[] if query else [dict(state_id=s,timing='immediate',description_zh='事务配置、目标或有限样品附着/释放') for s in effects]+[dict(state_id=s,timing='on_step',description_zh='唯一宿主时钟推进轴、手动调隙代理和有限材料') for s in evolving],
            observes=[],model_effects=dict(mode='none',description_zh='过程存在不等于已验证几何绑定',binding_ids=[]),
            execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',completion_criterion='轴达到目标；不保证干燥/膜质量' if asynchronous else '事务完成，物理时钟未推进',
                completion_state_ids=['cycle.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',retry='same_request_id',
                resources=['host_clock','finite_charge','substrate_attachment','request_cache'],partial_failure_zh='停止/故障保留已沉积材料和挥发，残余液体继续蒸发；不回滚，不提供跨进程幂等'),
            errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','PRECONDITION_FAILED','BUSY','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','REFERENCE_OUT_OF_RANGE','INVALID_GEOMETRY','RESERVOIR_OVERFLOW','RESERVOIR_EXHAUSTED','MODEL_OUT_OF_RANGE']],
            example_arguments=copy.deepcopy(ex),verification=[],limitations=['无膜厚传感器；急停是教学锁存，不是实机安全认证；调隙需宿主手动行为'],source_ids=['manufacturer_primary','simulation_assumption'])

    mech=dict(component_ids=['motor','carriage','blade','reservoir','substrate','waste_tray'],state_ids=list(slots),meaning_zh='有限浆料/移动刮刀/固定基板/64条带薄隙流/蒸发',
        implementation='lumped',equations=[
          'dq/dt=commanded signed speed; q in [0,.290]m along -Y; no torque/acceleration solver',
          'gap mean=(g_left+g_right)/2; tilt_y=-atan2(g_right-g_left,.1m)',
          'mean_cube=(g_left^3+g_left^2*g_right+g_left*g_right^2+g_right^3)/4',
          'H=bead_volume/reservoir_area; deltaP=rho*g*H; Q/width=U*gap_mean/2+deltaP*mean_cube/(12*mu*contact_length)',
          'dV/dx=-a-b*V; a=width*gap_mean/2; b=width*rho*g*mean_cube/(12*mu*length*U*reservoir_area); exact positive inventory-limited segment integral',
          'film receives overlap strips; outside substrate -> waste; species transfer by bead mass fraction',
          'P_sat=100000*10^(A-B/(T+C)); original NIST temperature range only',
          'evap_kg_s=k_mass*exposed_area*P_sat*M/(R*T)*solvent_mass_fraction, inventory limited',
          'initial_species=bead+film+waste+evaporated+collected; nonvolatile solute evaporation=0'],
        parameters={},source_ids=['manufacturer_primary','nist_vapor_pressure','simulation_assumption'],
        limitations=['平行板Couette-Poiseuille教学近似；g/刀口长度<=.1，Newtonian液体，无自由液面/弯月面/屈服应力/剪切变稀/颗粒/润湿标定。','65种真实蒸气压不等于50种实测刮涂膜厚；35种覆盖298.15K。','选择100mm刮刀、8mm固定玻璃台；200mm、真空台、加热、注液、气刀没有实现。','单前向恒速涂布+后端回零+硬停；20程序100步骤/软停/前端回零和完整LCD菜单未实现。','64条带，不沿宽度解析膜剖面；不建结晶、流平、干燥收缩、挥发冷却或CFD。','理想测微器示值不证明厂家3µm精度；手动调隙为宿主运动学，不是电动执行或力反馈。','急停释放且关开电源才可恢复，需重新回零；未验证硬件断电、制动距离或安全等级。','几何固定100×200mm示例基板；宿主不同基板参数不自动改网格。','废料接收器假设覆盖，不计算废料蒸发；单次装料不可补液。','代理API过滤不等于文件/进程隔离；原始Runtime仅供受信任宿主。'])
    spec=importlib.util.spec_from_file_location('_blade_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann)
    ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'doctor_blade':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'charge.configure':(CHARGE,CHARGE_EXAMPLE),'substrate.configure':(SAMPLE,SAMPLE_EXAMPLE)})
    mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'doctor_blade':mech})



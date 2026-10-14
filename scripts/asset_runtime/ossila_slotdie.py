"""CHEM-056 V2: finite metered supply, moving stage and lumped thermal balance."""
from __future__ import annotations
import copy, importlib.util, json, math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SLUG='ossila_slotdie'; ASSET_TYPE_ID='instrument.'+SLUG; CATALOG_ID='CHEM-056'; PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/ossila_slotdie_film_library/templates.json'
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




SPEED=number('m/s',.00005,.075);POS=number('m',0,.15);GAP=number('m',0,.013)
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
PISTON_AREA=math.pi*.0103**2/4;Q_MIN=PISTON_AREA*6e-6;Q_MAX=PISTON_AREA*.0075;PLUNGER_MAX=5e-6/PISTON_AREA
FLOW=number('m^3/s',Q_MIN,Q_MAX)
PLANT=obj({'ambient_k':number('K',278.15,313.15),'mass_transfer_m_s':number('m/s',0,.01),'manual_gap_speed_m_s':number('m/s',.00001,.1),'heat_capacity_j_k':number('J/K',10,10000),'loss_w_k':number('W/K',0,10),'heater_max_w':number('W',1,148.8),'controller_w_k':number('W/K',.1,100),'line_dead_volume_m3':number('m^3',1e-9,1e-6),'bead_length_m':number('m',.0001,.005),'min_gap_film_ratio':number('1',.01,100),'max_gap_film_ratio':number('1',.01,100),'accept_uncalibrated_model':BOOL})
PLANT_EXAMPLE=dict(ambient_k=298.15,mass_transfer_m_s=.0002,manual_gap_speed_m_s=.005,heat_capacity_j_k=120.,loss_w_k=.15,heater_max_w=80.,controller_w_k=10.,line_dead_volume_m3=5e-8,bead_length_m=.001,min_gap_film_ratio=.5,max_gap_film_ratio=10.,accept_uncalibrated_model=True)
METHOD=obj({'distance_m':number('m',.001,.15),'speed_m_s':SPEED,'flow_m3_s':FLOW,'stage_delay_s':number('s',0,1000)})
METHOD_EXAMPLE=dict(distance_m=.14,speed_m_s=.01,flow_m3_s=2e-8,stage_delay_s=.2)
INK=obj({'solvent_id':TEXT,'solvent_kg':number('kg',1e-9,.02),'solute_kg':number('kg',0,.02),'solvent_density_kg_m3':number('kg/m^3',100,3000),'solute_density_kg_m3':number('kg/m^3',100,10000),'viscosity_pa_s':number('Pa*s',.00001,1.),'surface_tension_n_m':number('N/m',.001,.2),'latent_heat_j_kg':number('J/kg',100000,5000000),'accept_newtonian_and_connections':BOOL})
INK_EXAMPLE=dict(solvent_id='ethanol',solvent_kg=.0015,solute_kg=.00015,solvent_density_kg_m3=789.,solute_density_kg_m3=1200.,viscosity_pa_s=.005,surface_tension_n_m=.022,latent_heat_j_kg=840000.,accept_newtonian_and_connections=True)
SAMPLE=obj({'width_m':number('m',.075,.1),'length_m':number('m',.001,.15),'start_m':POS,'accept_host_attachment':BOOL})
SAMPLE_EXAMPLE=dict(width_m=.1,length_m=.12,start_m=.01,accept_host_attachment=True)
PRODUCT_FIELDS={loc+'_'+sp+'_kg':number('kg',0) for loc in ('initial','syringe','line','bead','film','waste','evaporated','collected') for sp in ('solvent','solute')}
PRODUCT_FIELDS.update(energy_input_j=number('J',0),energy_loss_j=number('J'),latent_j=number('J',0),sensible_j=number('J'),wet_thickness_m=number('m',0),dry_equivalent_thickness_m=number('m',0),film_profile_m=dict(type='array',items=number('m',0),minItems=64,maxItems=64),evidence=TEXT,template_source=TEXT)
PRODUCT=obj(PRODUCT_FIELDS)
RESULT=obj({'phase':PHASE,'stage':TEXT,'position_m':POS,'speed_m_s':number('m/s',-.075,.075),'left_gap_m':GAP,'right_gap_m':GAP,'temperature_k':number('K',1,450),'flow_m3_s':number('m^3/s',0,Q_MAX),'elapsed_s':number('s',0),'evidence':TEXT,'template_source':TEXT})
CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=()):CAPS[k]=(i,o,c,z,kind,vis,ex or {},list(effects))
for key,s,ex,vis,c in [('plant',PLANT,PLANT_EXAMPLE,'evaluator','motor'),('method',METHOD,METHOD_EXAMPLE,'agent','display_digits'),('ink',INK,INK_EXAMPLE,'evaluator','syringe_barrel'),('substrate',SAMPLE,SAMPLE_EXAMPLE,'evaluator','substrate')]:
    cap(key+'.configure',s,obj({'configured':BOOL}),[c],'配置/有限装料/移动基板宿主附着','configuration',vis,ex,[key+'.configured']+(['truth.ledger'] if key in ['ink','substrate'] else []))
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['switch_power'],'后部电源；急停释放后须关开电源才复位',ex={'enabled':True},effects=['power.enabled','stage.homed','cycle.phase','safety.latched'])
cap('power.toggle',obj({}),obj({'enabled':BOOL}),['switch_power'],'后部电源开关翻转',effects=['power.enabled','stage.homed','cycle.phase','safety.latched'])
cap('stage.home',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['button_return','motor','carriage'],'规定速度回后端；空料无膜时才允许',effects=['cycle.phase','stage.target_m'])
cap('stage.move_to',obj({'position_m':POS,'speed_m_s':SPEED}),obj({'phase':PHASE,'operation_id':TEXT}),['carriage','motor'],'无料轴定位，非自动涂布',ex={'position_m':.05,'speed_m_s':.02},effects=['cycle.phase','stage.target_m'])
cap('gap.set',obj({'left_m':GAP,'right_m':GAP}),obj({'left_m':GAP,'right_m':GAP}),['head','gauge_left','gauge_right'],'宿主手动双端调隙目标；非机载电动执行','configuration','agent',{'left_m':.0001,'right_m':.0001},['gap.left_target_m','gap.right_target_m'])
cap('emergency.stop',obj({}),obj({'latched':BOOL}),['button_emergency'],'急停锁存；驱动停止且回零失效',effects=['safety.pressed','safety.latched','power.enabled','stage.homed','cycle.phase'])
cap('emergency.release',obj({}),obj({'released':BOOL}),['button_emergency'],'宿主顺时针释放按钮，尚未重置驱动故障','configuration','agent',{},['safety.pressed'])
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['button_start','motor','carriage','head','plunger'],'单步定量供液同步移动基板，有限注射器与已预充管路',effects=['cycle.phase','cycle.elapsed_s','stage.target_m'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['button_stop','motor'],'硬停止；保留部分成膜和残余料',effects=['cycle.phase','stage.target_m'])
cap('coating.measure',obj({}),RESULT,['display_digits','gauge_left','gauge_right'],'理想位置/速度/间隙读数；不是膜厚仪','query')
cap('product.read',obj({}),PRODUCT,['substrate','syringe_barrel','waste_tray'],'有限注射器/管路/液桥/膜/废料/蒸发与能量账本','query','evaluator')
cap('product.collect',obj({'recipient_id':TEXT}),obj({'recipient_id':TEXT,'attachment_event':TEXT}),['substrate'],'停止后宿主释放基板并转移膜账本','device_command','evaluator',{'recipient_id':'coated_slide'},['substrate.configured','truth.ledger'])
cap('fluid.drain',obj({}),obj({'emptied':BOOL}),['syringe_barrel','waste_tray'],'停止后宿主将注射器/管路/液桥余料转至废料，非自驱清洗','configuration','evaluator',{},['ink.configured','truth.ledger'])
cap('reference.lookup',obj({'substance_id':TEXT,'temperature_k':number('K',1)}),obj({'vapor_pressure_pa':number('Pa',0),'evidence':TEXT,'source':TEXT,'template_source':TEXT}),['syringe_barrel'],'NIST有效原始温区蒸气压参考','query','agent',{'substance_id':'ethanol','temperature_k':298.15})

cap('pump.dispense',obj({'volume_m3':number('m^3',1e-9,5e-6),'flow_m3_s':FLOW}),obj({'phase':PHASE,'operation_id':TEXT}),['plunger','syringe_barrel','feed_tube'],'有限注射器定量预充管路/液桥，余量去废料；无反抽',ex={'volume_m3':1e-7,'flow_m3_s':2e-8},effects=['cycle.phase','pump.flow_m3_s','pump.position_m'])
cap('thermal.set_temperature',obj({'temperature_k':number('K',278.15,413.15)}),obj({'temperature_k':number('K',278.15,413.15)}),['hotplate'],'写入140℃以内温度目标，非瞬时升温',ex={'temperature_k':313.15},effects=['thermal.target_k'])
cap('thermal.set_enabled',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['hotplate'],'独立热台开关；运动停止不自动关热',ex={'enabled':True},effects=['thermal.enabled'])
cap('thermal.set_offset',obj({'offset_k':number('K',-10,10)}),obj({'offset_k':number('K',-10,10)}),['display_digits'],'理想显示偏置，不伪称真实表面温度校准','configuration','agent',{'offset_k':1.},['thermal.offset_k'])
# Immediate effects are distinct from later physical evolution.
for key in ['power.set','power.toggle','emergency.stop']:CAPS[key][7].extend(['thermal.enabled','pump.flow_m3_s','stage.speed_m_s'])
CAPS['acquisition.stop'][7].extend(['pump.flow_m3_s','stage.speed_m_s'])
CAPS['ink.configure'][7].append('pump.position_m')
CAPS['plant.configure'][7].extend(['thermal.target_k','thermal.indicated_k'])
CAPS['fluid.drain'][7].append('safety.fault')
SLOTS={};SLOT_ZH={}

def slot(k,s,u,z,o='derived',v='agent',c='body',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
for key in ['plant','method','ink','substrate']:slot(key+'.configured',BOOL,'1',key+'配置或存在标记','setpoint')
slot('power.enabled',BOOL,'1','驱动电源有效；急停时为否',c='switch_power',w=('power.set','enabled'))
slot('stage.homed',BOOL,'1','本次通电已回零，断电急停失效',c='carriage')
slot('stage.target_m',POS,'m','后端为零，沿-Y前进的目标','setpoint',c='carriage')
slot('stage.position_m',POS,'m','沿-Y运动学位移理想示值','scene',c='carriage')
slot('stage.speed_m_s',number('m/s',-.075,.075),'m/s','前向正后向负理想速度',c='display_digits')
for end in ['left','right']:
    slot('gap.'+end+'_target_m',GAP,'m','手动调节'+end+'端目标，手动目标','setpoint',c='gauge_'+end)
    slot('gap.'+end+'_m',GAP,'m','手动调节'+end+'端理想数字示值，未标定热态平整度','scene',c='gauge_'+end)
slot('gap.mean_m',GAP,'m','模头平均升起量，非湿膜厚度','scene',c='head_lift')
slot('gap.tilt_rad',number('rad',-.131,.131),'rad','模头绕Y倾斜角，右高为负','scene',c='head')
slot('safety.pressed',BOOL,'1','急停按钮宿主事件，不是机器人触发证明','scene',c='button_emergency')
slot('safety.latched',BOOL,'1','必须释放后关开电源的故障锁存',c='button_emergency')
slot('cycle.phase',PHASE,'1','回零/定位/涂布异步状态',c='display_digits')
slot('cycle.stage',TEXT,'1','homing/manual/coating/stopped/idle',c='display_digits')
slot('cycle.elapsed_s',number('s',0),'s','本次操作宿主累计时间',c='display_digits')
slot('pump.position_m',number('m',0,PLUNGER_MAX),'m','5mL/10.3mm内径柱塞位移，不是质量比例','scene',c='plunger')
slot('pump.flow_m3_s',number('m^3/s',0,Q_MAX),'m^3/s','理想实际液体体积流量，不是额外流量传感器',c='display_digits')
slot('pump.delivered_m3',number('m^3',0,5e-6),'m^3','累计柱塞排出体积，含预充与废料',c='display_digits')
slot('thermal.target_k',number('K',278.15,413.15),'K','热台设定值，上限140℃','setpoint',c='hotplate',w=('thermal.set_temperature','temperature_k'))
slot('thermal.enabled',BOOL,'1','独立加热开关','setpoint',c='hotplate',w=('thermal.set_enabled','enabled'))
slot('thermal.offset_k',number('K',-10,10),'K','仅理想数字显示偏置','setpoint',c='display_digits',w=('thermal.set_offset','offset_k'))
slot('thermal.indicated_k',number('K',1,450),'K','集总热台理想温度加显示偏置，非实测基板温度',c='display_digits')
slot('safety.fault',TEXT,'1','空注射器或越出模型温区故障标记',c='display_digits')
slot('truth.ledger',PRODUCT,'1','64条带薄膜/有限浆料/废料/蒸发/收集物种账本','process_truth','evaluator','substrate')

class OssilaSlotdieRuntime:
    def __init__(self,instance_id='slotdie_01'):
        self.instance_id=instance_id;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']}
        self.time_s=0.;self.revision=0;self.power_switch=False;self.powered=False;self.homed=False;self.pressed=False;self.latched=False;self.reset_off=False;self.fault=''
        self.plant=None;self.method=None;self.ink_cfg=None;self.sample=None;self.sample_present=False
        self.q=0.;self.target=0.;self.speed=0.;self.manual_speed=.01;self.gaps=[0.,0.];self.gap_targets=[0.,0.]
        self.phase='idle';self.stage='idle';self.elapsed_s=0.;self.delay_left=0.;self.dose_left=0.;self.dose_flow=0.
        self.temp=298.15;self.target_temp=298.15;self.offset=0.;self.heating=False;self.energy_input=0.;self.energy_loss=0.;self.latent=0.
        self.initial=[0.,0.];self.syringe=[0.,0.];self.line=[0.,0.];self.bead=[0.,0.];self.waste=[0.,0.];self.evaporated=[0.,0.];self.collected=[0.,0.];self.film=[[0.,0.] for _ in range(64)]
        self.delivered=0.;self.pump_q=0.;self.flow=0.;self.fit=None
        self.operation_id='';self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
    def register_principal(self,principal_id,visibility='agent'):
        if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id='__public_agent__'):
        if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return OssilaSlotdieSession(self,principal_id)
    def describe(self):return self.session().describe(instance_id=self.instance_id)
    def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k!='templates'})
    def _reference(self,sid,t):
        if sid not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
        r=self.templates[sid];fits=[f for f in r['fits'] if f['temperature_range_k'][0]<=t<=f['temperature_range_k'][1]]
        if not fits:raise ActionRejected('REFERENCE_OUT_OF_RANGE')
        f=fits[0];return dict(vapor_pressure_pa=1e5*10**(f['A']-f['B_k']/(t+f['C_k'])),evidence='derived_NIST_Antoine_no_extrapolation',source=r['source'],template_source=TEMPLATE_REL)


    def _volume(self,m):
        c=self.ink_cfg
        return m[0]/c['solvent_density_kg_m3']+m[1]/c['solute_density_kg_m3'] if c else 0.
    def _product(self):
        films=[sum(f[i] for f in self.film) for i in range(2)]
        p={loc+'_'+sp+'_kg':m[i] for loc,m in [('initial',self.initial),('syringe',self.syringe),('line',self.line),('bead',self.bead),('film',films),('waste',self.waste),('evaporated',self.evaporated),('collected',self.collected)] for i,sp in enumerate(['solvent','solute'])}
        area=self.sample['width_m']*self.sample['length_m'] if self.sample else 1.;rho=self.ink_cfg['solute_density_kg_m3'] if self.ink_cfg else 1.
        profile=[self._volume(f)/(area/64) for f in self.film]
        p.update(wet_thickness_m=sum(profile)/64,dry_equivalent_thickness_m=films[1]/rho/area,film_profile_m=profile,
            energy_input_j=self.energy_input,energy_loss_j=self.energy_loss,latent_j=self.latent,sensible_j=self.plant['heat_capacity_j_k']*(self.temp-self.plant['ambient_k']) if self.plant else 0.,
            evidence='synthetic_metered_64strip_film_and_lumped_heat;real_NIST_reference;NOT_measured_thickness',template_source=TEMPLATE_REL)
        return p
    def read(self,visibility='agent'):
        if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
        v={'plant.configured':self.plant is not None,'method.configured':self.method is not None,'ink.configured':self.ink_cfg is not None,'substrate.configured':self.sample_present,
        'power.enabled':self.powered,'stage.homed':self.homed,'stage.target_m':self.target,'stage.position_m':self.q,'stage.speed_m_s':self.speed,
        'gap.left_target_m':self.gap_targets[0],'gap.right_target_m':self.gap_targets[1],'gap.left_m':self.gaps[0],'gap.right_m':self.gaps[1],'gap.mean_m':sum(self.gaps)/2,'gap.tilt_rad':-math.asin((self.gaps[1]-self.gaps[0])/.1),
        'safety.pressed':self.pressed,'safety.latched':self.latched,'safety.fault':self.fault,'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.elapsed_s':self.elapsed_s,
        'pump.position_m':self.pump_q,'pump.flow_m3_s':self.flow,'pump.delivered_m3':self.delivered,'thermal.target_k':self.target_temp,'thermal.enabled':self.heating,'thermal.offset_k':self.offset,'thermal.indicated_k':self.temp+self.offset,'truth.ledger':self._product()}
        return copy.deepcopy({k:x for k,x in v.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
    def _stop(self,result='cancelled'):
        if self.phase=='running':self.phase=result;self.stage='stopped';self.target=self.q
        self.speed=0.;self.flow=0.
    def _new_operation(self,stage,target):
        self.phase='running';self.stage=stage;self.target=target;self.elapsed_s=0.;self._operation_count+=1
        self.operation_id=self.instance_id+':motion:'+str(self._operation_count)
        return dict(phase=self.phase,operation_id=self.operation_id)
    def _bead_capacity(self):return .1*sum(self.gaps)/2*self.plant['bead_length_m']
    def _model_check(self):
        if self.sample['width_m']!=.1 or min(self.gaps)<=0:raise ActionRejected('MODEL_OUT_OF_RANGE')
        h=self.method['flow_m3_s']/(.1*self.method['speed_m_s'])
        if not all(self.plant['min_gap_film_ratio']<=g/h<=self.plant['max_gap_film_ratio'] for g in self.gaps):raise ActionRejected('MODEL_OUT_OF_RANGE')
        if self._volume(self.line)<self.plant['line_dead_volume_m3']*(1-1e-8) or self._volume(self.bead)<self._bead_capacity()*(1-1e-8):raise ActionRejected('NOT_PRIMED')
    def _fit_for(self,config,target):
        if config['solvent_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
        low=min(self.temp,self.plant['ambient_k'],target);high=max(self.temp,self.plant['ambient_k'],target)
        matches=[f for f in self.templates[config['solvent_id']]['fits'] if f['temperature_range_k'][0]<=low and high<=f['temperature_range_k'][1]]
        if not matches:raise ActionRejected('REFERENCE_OUT_OF_RANGE')
        return copy.deepcopy(matches[0])
    def invoke(self,capability,**a):
        if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability][0],a)
        if capability=='plant.configure':
            if self.plant or not a['accept_uncalibrated_model'] or a['min_gap_film_ratio']>=a['max_gap_film_ratio']:raise ActionRejected('PRECONDITION_FAILED')
            self.plant=a.copy();self.temp=a['ambient_k'];self.target_temp=self.temp;out={'configured':True}
        elif capability=='method.configure':
            if self.phase=='running':raise ActionRejected('BUSY')
            self.method=a.copy();out={'configured':True}
        elif capability=='substrate.configure':
            if self.phase=='running' or self.sample_present or self.q!=0 or not self.homed or not a['accept_host_attachment'] or self.temp>323.15:raise ActionRejected('PRECONDITION_FAILED')
            if a['start_m']+a['length_m']>.15:raise ActionRejected('INVALID_GEOMETRY')
            self.sample=a.copy();self.sample_present=True;out={'configured':True}
        elif capability=='ink.configure':
            if not self.plant or self.ink_cfg or self.phase=='running' or not a['accept_newtonian_and_connections']:raise ActionRejected('PRECONDITION_FAILED')
            fit=self._fit_for(a,self.target_temp);volume=a['solvent_kg']/a['solvent_density_kg_m3']+a['solute_kg']/a['solute_density_kg_m3']
            if volume>5e-6:raise ActionRejected('SYRINGE_OVERFLOW')
            self.ink_cfg=a.copy();self.fit=fit;self.syringe=[a['solvent_kg'],a['solute_kg']];self.initial=self.syringe.copy();self.pump_q=(5e-6-volume)/PISTON_AREA;out={'configured':True}
        elif capability in ('power.set','power.toggle'):
            enabled=a['enabled'] if capability=='power.set' else not self.power_switch
            if enabled and self.latched and (self.pressed or not self.reset_off):raise ActionRejected('PRECONDITION_FAILED')
            self.power_switch=enabled
            if not enabled:self.powered=False;self.homed=False;self.heating=False;self._stop();self.reset_off=self.latched and not self.pressed
            else:self.powered=True;self.latched=False;self.reset_off=False
            out={'enabled':self.powered}
        elif capability=='gap.set':
            if not self.plant or self.phase=='running':raise ActionRejected('PRECONDITION_FAILED')
            self.gap_targets=[a['left_m'],a['right_m']];out=a.copy()
        elif capability=='emergency.stop':
            self.pressed=True;self.latched=True;self.reset_off=False;self.powered=False;self.homed=False;self.heating=False;self._stop('failed');out={'latched':True}
        elif capability=='emergency.release':self.pressed=False;out={'released':True}
        elif capability in ('thermal.set_temperature','thermal.set_enabled','thermal.set_offset'):
            if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
            if capability=='thermal.set_temperature':
                fit=self._fit_for(self.ink_cfg,a['temperature_k']) if self.ink_cfg and sum(self.syringe+self.line+self.bead)+sum(map(sum,self.film))>1e-15 else self.fit
                self.target_temp=a['temperature_k'];self.fit=fit
            elif capability=='thermal.set_enabled':
                if a['enabled'] and (not self.powered or self.latched):raise ActionRejected('PRECONDITION_FAILED')
                self.heating=a['enabled']
            else:self.offset=a['offset_k']
            out=a.copy()
        elif capability in ('stage.home','stage.move_to','acquisition.start','pump.dispense'):
            if not self.plant or not self.powered or self.latched or self.fault or self.phase=='running':raise ActionRejected('PRECONDITION_FAILED')
            if capability=='acquisition.start':
                if not all([self.method,self.ink_cfg,self.sample_present,self.homed]) or any(abs(a-b)>1e-12 for a,b in zip(self.gaps,self.gap_targets)):raise ActionRejected('PRECONDITION_FAILED')
                if self.q+self.method['distance_m']>.15+1e-12:raise ActionRejected('INVALID_GEOMETRY')
                if self._volume(self.syringe)<=1e-15:raise ActionRejected('SYRINGE_EMPTY')
                self._model_check();self.delay_left=self.method['stage_delay_s'];out=self._new_operation('delay' if self.delay_left>0 else 'coating',self.q+self.method['distance_m'])
            elif capability=='pump.dispense':
                if not self.ink_cfg or not self.homed or min(self.gaps)<=0:raise ActionRejected('PRECONDITION_FAILED')
                if a['volume_m3']>self._volume(self.syringe)+1e-15:raise ActionRejected('SYRINGE_EMPTY')
                self.dose_left=a['volume_m3'];self.dose_flow=a['flow_m3_s'];out=self._new_operation('priming',self.q)
            else:
                if (self._volume(self.bead)>0 or sum(map(sum,self.film))>1e-15) and min(self.gaps)<.001:raise ActionRejected('PRECONDITION_FAILED')
                if capability=='stage.home':out=self._new_operation('homing',0.)
                else:
                    if not self.homed:raise ActionRejected('PRECONDITION_FAILED')
                    self.manual_speed=a['speed_m_s'];out=self._new_operation('manual',a['position_m'])
        elif capability=='acquisition.stop':self._stop();out={'phase':self.phase}
        elif capability=='coating.measure':
            return dict(phase=self.phase,stage=self.stage,position_m=self.q,speed_m_s=self.speed,left_gap_m=self.gaps[0],right_gap_m=self.gaps[1],temperature_k=self.temp+self.offset,flow_m3_s=self.flow,elapsed_s=self.elapsed_s,evidence='ideal_panel_NOT_thickness_sensor',template_source=TEMPLATE_REL)
        elif capability=='product.read':return self._product()
        elif capability=='reference.lookup':return self._reference(a['substance_id'],a['temperature_k'])
        elif capability=='product.collect':
            if self.phase=='running' or not self.sample_present or self.temp>323.15:raise ActionRejected('PRECONDITION_FAILED')
            for i in range(2):self.collected[i]+=sum(f[i] for f in self.film)
            self.film=[[0.,0.] for _ in range(64)];self.sample_present=False;out=dict(recipient_id=a['recipient_id'],attachment_event='host_release_substrate')
        elif capability=='fluid.drain':
            if self.phase=='running':raise ActionRejected('PRECONDITION_FAILED')
            for masses in [self.syringe,self.line,self.bead]:
                for i in range(2):self.waste[i]+=masses[i];masses[i]=0.
            self.fault='';out={'emptied':True}
        else:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability][1],out);self.revision+=1;return copy.deepcopy(out)
    def _deposit(self,masses,lo,hi):
        if not self.sample_present:
            for i in range(2):self.waste[i]+=masses[i]
            return
        s=self.sample;dl=s['length_m']/64
        if hi<=lo+1e-15:
            idx=math.floor((lo-s['start_m'])/dl);dest=self.film[idx] if 0<=idx<64 else self.waste
            for i in range(2):dest[i]+=masses[i]
            return
        cuts=[lo]+[s['start_m']+i*dl for i in range(65) if lo<s['start_m']+i*dl<hi]+[hi]
        for start,end in zip(cuts,cuts[1:]):
            idx=math.floor(((start+end)/2-s['start_m'])/dl);dest=self.film[idx] if 0<=idx<64 else self.waste
            for i in range(2):dest[i]+=masses[i]*(end-start)/(hi-lo)
    def _pump(self,volume):
        available=self._volume(self.syringe);fraction=min(1.,volume/max(available,1e-30));masses=[m*fraction for m in self.syringe]
        for i in range(2):self.syringe[i]-=masses[i]
        actual=available*fraction;self.delivered+=actual;self.pump_q=min(PLUNGER_MAX,self.pump_q+actual/PISTON_AREA)
        for dest,capacity in [(self.line,self.plant['line_dead_volume_m3']),(self.bead,self._bead_capacity())]:
            space=max(0.,capacity-self._volume(dest));vol=self._volume(masses);f=min(1.,space/max(vol,1e-30))
            for i in range(2):mass=masses[i]*f;masses[i]-=mass;dest[i]+=mass
        return masses,actual
    def _thermal(self,dt):
        if not self.plant:return
        p=self.plant;heat=min(p['heater_max_w'],max(0.,p['controller_w_k']*(self.target_temp-self.temp))) if self.heating and self.powered else 0.
        loss=p['loss_w_k']*(self.temp-p['ambient_k']);evaps=[0.]*64
        if self.ink_cfg and self.sample and sum(f[0] for f in self.film)>1e-20:
            low,high=self.fit['temperature_range_k']
            if not low-1e-9<=self.temp<=high+1e-9:
                self.fault='REFERENCE_OUT_OF_RANGE';self.heating=False;self._stop('failed');heat=0.
            else:
                ref=self._reference(self.ink_cfg['solvent_id'],max(low,min(high,self.temp)))
                flux=p['mass_transfer_m_s']*ref['vapor_pressure_pa']*self.templates[self.ink_cfg['solvent_id']]['molar_mass_kg_mol']/(8.314462618*self.temp)
                area=self.sample['width_m']*self.sample['length_m']/64
                evaps=[min(f[0],flux*area*f[0]/max(sum(f),1e-30)*dt) for f in self.film]
                limit=max(0.,p['heat_capacity_j_k']*(self.temp-low)+(heat-loss)*dt)/self.ink_cfg['latent_heat_j_kg']
                scale=min(1.,limit/max(sum(evaps),1e-30));evaps=[e*scale for e in evaps]
        ev=sum(evaps);latent=ev*self.ink_cfg['latent_heat_j_kg'] if self.ink_cfg else 0.
        for f,e in zip(self.film,evaps):f[0]-=e
        self.evaporated[0]+=ev;self.energy_input+=heat*dt;self.energy_loss+=loss*dt;self.latent+=latent
        self.temp+=((heat-loss)*dt-latent)/p['heat_capacity_j_k']
    def step(self,dt_s):
        validate(number('s',1e-12,1000),dt_s);left=float(dt_s)
        while left>1e-12:
            dt=min(.02,left);before=self.q;flow=0.;speed=0.;stage=self.stage
            if self.phase=='running':
                if stage in ['homing','manual','coating']:
                    distance=self.target-self.q
                    if abs(distance)<1e-12:
                        self.q=self.target;self.homed=self.homed or stage=='homing';self.phase='succeeded';self.stage='idle';self.speed=0.;self.flow=0.;continue
                    speed=self.method['speed_m_s'] if stage=='coating' else .01 if stage=='homing' else self.manual_speed
                    dt=min(dt,abs(distance)/speed);speed=math.copysign(speed,distance)
                if stage in ['delay','coating','priming']:
                    flow=self.dose_flow if stage=='priming' else self.method['flow_m3_s']
                    if self._volume(self.syringe)<=1e-18:self.fault='SYRINGE_EMPTY';self._stop('failed');continue
                    dt=min(dt,self._volume(self.syringe)/flow)
                    if stage=='priming':dt=min(dt,self.dose_left/flow)
                    if stage=='delay':dt=min(dt,self.delay_left)
                if dt<=1e-12:
                    if stage=='delay':self.stage='coating';continue
                    if stage=='priming':self.phase='succeeded';self.stage='idle';self.flow=0.;continue
                self.q+=speed*dt;self.speed=speed;self.flow=flow;self.elapsed_s+=dt
                if flow>0:
                    masses,actual=self._pump(flow*dt);self._deposit(masses,before,self.q)
                    if stage=='priming':self.dose_left=max(0.,self.dose_left-actual)
                if stage=='delay':
                    self.delay_left=max(0.,self.delay_left-dt)
                    if self.delay_left<1e-12:self.stage='coating'
                if stage in ['homing','manual','coating'] and abs(self.q-self.target)<1e-11:
                    self.q=self.target;self.homed=self.homed or stage=='homing';self.phase='succeeded';self.stage='idle';self.speed=0.;self.flow=0.
                if stage=='priming' and self.dose_left<1e-15:self.phase='succeeded';self.stage='idle';self.flow=0.
            else:self.speed=0.;self.flow=0.
            if self.plant:
                for i in range(2):
                    dx=self.plant['manual_gap_speed_m_s']*dt;self.gaps[i]+=max(-dx,min(dx,self.gap_targets[i]-self.gaps[i]))
            self._thermal(dt);self.time_s+=dt;left-=dt
        self.revision+=1;return self.read('evaluator')
class OssilaSlotdieSession:
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
        m=export_ossila_slotdie_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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



def export_ossila_slotdie_contract_metadata():
    caps={};slots={}
    for k,(s,u,o,v,c,w) in SLOTS.items():
        slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',
            read_binding=dict(backend='host',artifact_id='runtime',accessor=f'OssilaSlotdieSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,
            sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    evolving=['stage.position_m','stage.speed_m_s','stage.homed','gap.left_m','gap.right_m','gap.mean_m','gap.tilt_rad','cycle.phase','cycle.stage','cycle.elapsed_s','pump.position_m','pump.flow_m3_s','pump.delivered_m3','thermal.indicated_k','truth.ledger','safety.fault']
    for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
        asynchronous=k in ['acquisition.start','stage.home','stage.move_to','pump.dispense'];query=kind=='query'
        caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
            implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
            binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.ossila_slotdie.OssilaSlotdieSession.invoke',invocation=f'session.invoke(instance_id="slotdie_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),
            preconditions=[] if query else [dict(description_zh='停止/上电/回零/急停复位/有限料/计量稳定窗口/预充/NIST温区条件，宿主身份',state_ids=['cycle.phase','power.enabled','stage.homed','safety.latched'],enforced_by='runtime',check='Runtime.invoke guards; Session trusted principal policy')],
            state_effects=[] if query else [dict(state_id=s,timing='immediate',description_zh='事务配置、目标或有限样品附着/释放') for s in effects]+[dict(state_id=s,timing='on_step',description_zh='唯一宿主时钟推进轴、手动调隙代理和有限材料') for s in evolving],
            observes=[],model_effects=dict(mode='none',description_zh='过程存在不等于已验证几何绑定',binding_ids=[]),
            execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',completion_criterion='轴达到目标；不保证干燥/膜质量' if asynchronous else '事务完成，物理时钟未推进',
                completion_state_ids=['cycle.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',retry='same_request_id',
                resources=['host_clock','finite_charge','substrate_attachment','request_cache'],partial_failure_zh='停止/故障保留已沉积材料和挥发，残余液体继续蒸发；不回滚，不提供跨进程幂等'),
            errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','PRECONDITION_FAILED','BUSY','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','REFERENCE_OUT_OF_RANGE','INVALID_GEOMETRY','RESERVOIR_OVERFLOW','RESERVOIR_EXHAUSTED','MODEL_OUT_OF_RANGE']],
            example_arguments=copy.deepcopy(ex),verification=[],limitations=['无膜厚传感器；急停是教学锁存，不是实机安全认证；调隙需宿主手动行为'],source_ids=['manufacturer_primary','simulation_assumption'])


    mech=dict(component_ids=['motor','carriage','head','syringe_barrel','plunger','feed_tube','substrate','hotplate'],state_ids=list(slots),
        meaning_zh='有限注射供液/预充/液桥/移动基板计量膜与集总热平衡',implementation='lumped',
        equations=['dq/dt=commanded speed; q in [0,.150]m along -Y','Q=A_piston*v_piston; A=pi*(.0103m)^2/4; v in [6e-6,.0075]m/s; capacity5e-6m3',
        'line fills declared dead volume, then bead width*mean_gap*declared_length; excess -> film overlap or waste',
        'steady primed film thickness=Q/(width*stage_speed), inventory limited; static dispense -> local puddle/waste, never divide by zero',
        'head mean=(gL+gR)/2; tilt_y=-asin((gR-gL)/.1m); manual host authority',
        'P_sat=100000*10^(A-B/(T+C)), original fit interval only; film evaporation=k_mass*area*P_sat*M/(R*T)*solvent_mass_fraction',
        'C*dT/dt=P_control-K*(T-Ta)-L*dm_evap/dt; P_control=clamp(kp*(target-T),0,Pmax), disabled on poweroff/emergency',
        'evaporation bounded by inventory and energy down to the valid NIST lower-temperature boundary',
        'initial_species=syringe+line+bead+film+waste+evaporated+collected; solute evaporation=0',
        'C*(T-Ta)=integral(heater_power)-integral(loss)-evaporation_latent_heat'],
        parameters={},source_ids=['manufacturer_primary','manufacturer_manual','nist_vapor_pressure','simulation_assumption'],
        limitations=['单步供液/平台+预延迟，非20程序50步完整固件；只选100mm标准头/5mL注射器，未做高黏度变体。','稳定窗口是宿主显式假设，不解弯月面自由界面、接触角、空气卷入、压降、背压或非Newtonian流变。','注射器/管路/液桥/废料视为封闭存量，仅热台上膜挥发；停机管路渗漏/被动滴落未求解。','单温度整体热台/膜，不代表实测表面温度、热膨胀调平；热容/功率/散热/潜热需材料实测。','65种NIST参考不等于50种实测狭缝涂膜，35种覆盖25℃；80nm匿名染料示例不作为确定物质模板。','加载液体后目标温度必须处于一个覆盖当前/环境/目标的有效NIST温区；不外推，干台可设置140℃。','小示例100×120mm基板、固定几何；动态膜/湿润前缘/变尺寸网格未做。','理想位置/泵流量/温度/数字间隙示值，无真实传感器噪声、精度或力标定。','零件质量/惯量/摩擦未知；机器人、真实安全接触、硬件和部署文件隔离均未验证。'])
    spec=importlib.util.spec_from_file_location('_slot_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann)
    ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'slot_die':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'ink.configure':(INK,INK_EXAMPLE),'substrate.configure':(SAMPLE,SAMPLE_EXAMPLE)})
    mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'slot_die':mech})



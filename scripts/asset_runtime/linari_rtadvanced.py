"""CHEM-059 two HV groups, eight finite syringe drives; explicitly uncalibrated electrospinning."""
from __future__ import annotations
import copy, importlib.util, json, math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SLUG='linari_rtadvanced'; ASSET_TYPE_ID='instrument.'+SLUG; CATALOG_ID='CHEM-059'; PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/linari_rtadvanced_fiber_library/templates.json'
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






EPS0=8.8541878128e-12
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
COUNT=dict(type='integer',minimum=0,maximum=10000000,**{'x-unit':'1'})
PUMP=dict(type='integer',minimum=0,maximum=7,**{'x-unit':'1'})
def arr(s,n):return dict(type='array',items=s,minItems=n,maxItems=n)
PLANT=obj({'ambient_k':number('K',288.15,313.15),'ambient_rh':number('1',.2,.7),'thermal_capacity_j_k':number('J/K',1,100000),'heater_w':number('W',1,4000),'loss_w_k':number('W/K',0,100),'controller_w_k':number('W/K',.1,1000),'humidity_tau_s':number('s',.01,10000),'voltage_tau_s':number('s',.01,100),'voltage_capacitance_f':number('F',1e-12,1e-5),'rotor_tau_s':number('s',.01,100),'needle_speed_m_s':number('m/s',.0001,.1),'syringe_id_m':number('m',.01,.05),'cone_residence_s':number('s',.01,100),'needle_radius_m':number('m',1e-5,.005),'field_enhancement':number('1',1,1000),'jet_solvent_evap_fraction':number('1',0,1),'mat_evap_tau_s':number('s',.01,100000),'mat_porosity':number('1',0,.99),'accept_uncalibrated_model':BOOL})
PLANT_EXAMPLE=dict(ambient_k=298.15,ambient_rh=.4,thermal_capacity_j_k=1000.,heater_w=400.,loss_w_k=5.,controller_w_k=100.,humidity_tau_s=20.,voltage_tau_s=1.,voltage_capacitance_f=1e-9,rotor_tau_s=.5,needle_speed_m_s=.02,syringe_id_m=.022,cone_residence_s=.2,needle_radius_m=.0003,field_enhancement=30.,jet_solvent_evap_fraction=.7,mat_evap_tau_s=30.,mat_porosity=.8,accept_uncalibrated_model=True)
SOLUTION=obj({'pump':PUMP,'reference_id':TEXT,'solvent_kg':number('kg',1e-12,.2),'polymer_kg':number('kg',1e-12,.2),'solvent_density_kg_m3':number('kg/m^3',100,3000),'polymer_density_kg_m3':number('kg/m^3',100,10000),'surface_tension_n_m':number('N/m',.001,.2),'accept_material_and_units_assumption':BOOL})
SOLUTION_EXAMPLE=dict(pump=0,reference_id='prelim_row_1261',solvent_kg=.0009,polymer_kg=.0001,solvent_density_kg_m3=1000.,polymer_density_kg_m3=1310.,surface_tension_n_m=.044,accept_material_and_units_assumption=True)
COLLECTOR=obj({'diameter_m':number('m',.001,.25),'length_m':number('m',.01,.5),'accept_host_attachment':BOOL})
COLLECTOR_EXAMPLE=dict(diameter_m=.1,length_m=.5,accept_host_attachment=True)
METHOD=obj({'voltage_v':arr(number('V',0,60000),2),'flows_m3_s':arr(number('m^3/s',0,1e-7),8),'rpm':number('1/min',-5000,5000),'needle_low_m':arr(number('m',0,.5),2),'needle_high_m':arr(number('m',0,.5),2),'gap_m':arr(number('m',.001,.5),2),'duration_s':number('s',.02,36000)})
METHOD_EXAMPLE=dict(voltage_v=[15000.,15000.],flows_m3_s=[.2e-6/3600]+[0.]*7,rpm=600.,needle_low_m=[.05,.05],needle_high_m=[.45,.45],gap_m=[.04,.04],duration_s=10.)
MEASURE=obj({'phase':PHASE,'temperature_k':number('K',1,400),'humidity':number('1',0,1),'voltage_v':arr(number('V',0,60000),2),'rpm':number('1/min',-5000,5000),'needle_z_m':arr(number('m',0,.5),2),'mean_fiber_diameter_m':number('m',0),'deposited_polymer_kg':number('kg',0),'instability_proxy':arr(number('1',0),8),'evidence':TEXT,'template_source':TEXT})
FIELDS={loc+'_'+sp+'_kg':number('kg',0) for loc in ['initial','syringe','cone','mat','waste','evaporated','collected'] for sp in ['solvent','polymer']}
FIELDS.update(axial_polymer_kg=arr(number('kg',0),32),axial_thickness_m=arr(number('m',0),32),heater_input_j=number('J',0),thermal_loss_j=number('J'),sensible_j=number('J'),hv_source_j=number('J',0),hv_dissipated_j=number('J',0),hv_stored_j=number('J',0),evidence=TEXT,template_source=TEXT)
PRODUCT=obj(FIELDS)
CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=()):CAPS[k]=(i,o,c,z,kind,vis,ex or {},list(effects))
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body'],'宿主显式热/HV/针筒/应力门限参数','configuration','evaluator',PLANT_EXAMPLE,['plant.configured','thermal.temperature_k','thermal.target_k','climate.humidity','climate.target_rh'])
cap('collector.configure',COLLECTOR,obj({'configured':BOOL}),['collector'],'宿主滚筒附着，静止与无电压后可执行','configuration','evaluator',COLLECTOR_EXAMPLE,['collector.present'])
cap('solution.load',SOLUTION,obj({'loaded':BOOL}),['pump_'+str(i) for i in range(8)],'8针筒分别装入有限溶液；每筒至多50mL','configuration','evaluator',SOLUTION_EXAMPLE,['truth.ledger'])
cap('solution.drain',obj({'pump':PUMP}),obj({'emptied':BOOL}),['pump_'+str(i) for i in range(8)]+['waste_receiver'],'静止断高压后针筒及锥液转废物','configuration','evaluator',{'pump':0},['truth.ledger'])
cap('method.configure',METHOD,obj({'configured':BOOL}),['tablet','needle_group_0','needle_group_1','collector'],'两独立高压目标、8流量、转速、竖直往复范围与时长','configuration','agent',METHOD_EXAMPLE,['method.configured'])
for name,c,z,v in [('power','power_inlet','主电源；断电停止泵并衰减高压和转速','agent'),('vent','exhaust_port','宿主排风可用边界，非实测气流开关','evaluator'),('hv','hv_0','双高压使能，开门/失排风/急停时禁止','agent'),('climate','uta_port','显式配置UTA的温湿控制使能','agent')]:
    cap(name+'.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),[c],'宿主'+z,vis=v,ex={'enabled':True},effects=[name+'.enabled'])
cap('door.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['front_door'],'前门宿主开合；有实际高压/转速/运行时不可打开',ex={'opened':True},effects=['door.opened','door.angle_rad'])
cap('climate.set_targets',obj({'temperature_k':number('K',288.15,323.15),'humidity':number('1',.2,.7)}),obj({'temperature_k':number('K',288.15,323.15),'humidity':number('1',.2,.7)}),['uta_port','display_digits'],'室温至50℃和20–70%RH；RH为0..1而非百分数',ex={'temperature_k':303.15,'humidity':.5},effects=['thermal.target_k','climate.target_rh'])
cap('safety.trip',obj({}),obj({'latched':BOOL}),['emergency_button'],'急停锁存；停止泵，高压/转子有限衰减',effects=['safety.latched','hv.enabled','cycle.phase'])
cap('safety.reset',obj({}),obj({'latched':BOOL}),['emergency_button'],'高压和转速衰减、门关闭后宿主复位',effects=['safety.latched','safety.fault'])
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['needle_group_0','needle_group_1','collector'],'有限定时电纺；应力不足时滴落废液',effects=['cycle.phase','cycle.elapsed_s'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['pump_'+str(i) for i in range(8)],'停泵保留锥中残留；高压由独立开关控制',effects=['cycle.phase'])
cap('needle.clean',obj({'group':dict(type='integer',minimum=0,maximum=1,**{'x-unit':'1'})}),obj({'cleaned':BOOL}),['gas_clean_0','gas_clean_1'],'停机断高压后压缩气清理锥残留到废料；非真实气路模型',ex={'group':0},effects=['truth.ledger'])
cap('spinning.measure',obj({}),MEASURE,['display_digits'],'理想示值与合成纤维直径，不是激光厚度传感器','query')
cap('product.read',obj({}),PRODUCT,['collector','waste_receiver'],'8针筒有限双组分、轴向膜厚和显热/HV能量账本','query','evaluator')
cap('product.collect',obj({'recipient_id':TEXT}),obj({'recipient_id':TEXT,'attachment_event':TEXT}),['collector'],'安全静止后释放滚筒产品到宿主','device_command','evaluator',{'recipient_id':'fiber_mat'},['collector.present','truth.ledger'])
cap('reference.lookup',obj({'substance_id':TEXT}),obj({'substance_id':TEXT,'mean_diameter_m':number('m',0),'evidence':TEXT,'source':TEXT,'template_source':TEXT}),['tablet'],'真实文献均径查表，逐记录含来源；不是本机实测','query','agent',{'substance_id':'prelim_row_1261'})
cap('power.toggle',obj({}),obj({'enabled':BOOL}),['power_button'],'实体电源按钮驱动宿主切换，数值按压不证明硬件','device_command','agent',{},['power.enabled','hv.enabled','climate.enabled','cycle.phase'])
CAPS['hv.set'][2].append('hv_1')
for k in ['power.set','vent.set','safety.trip']:
    CAPS[k][7].extend(['hv.enabled','cycle.phase','climate.enabled'])
CAPS['vent.set'][7].append('safety.fault')
CAPS['safety.trip'][7].append('safety.fault')
CAPS['product.collect'][7].append('fiber.mean_diameter_m')
for k in ['solution.load','solution.drain']:CAPS[k][7].extend(['plunger.pump_'+str(i)+'_m' for i in range(8)])
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='body',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
for k in ['plant','method']:slot(k+'.configured',BOOL,'1',k+'已配置','setpoint')
for k in ['power','hv','climate']:slot(k+'.enabled',BOOL,'1',k+'使能','setpoint',w=(k+'.set','enabled'))
slot('vent.enabled',BOOL,'1','宿主排风可用标志非传感器','scene')
slot('collector.present',BOOL,'1','宿主滚筒附着','scene',c='collector')
slot('door.opened',BOOL,'1','前门开状态','scene',c='front_door',w=('door.set','opened'))
slot('door.angle_rad',number('rad',0,1.3),'rad','门绕Z假定铰链','scene',c='front_door')
slot('safety.latched',BOOL,'1','急停锁存','setpoint',c='emergency_button')
slot('safety.fault',TEXT,'1','宿主模型故障，不是厂家代码')
for i in range(2):
    slot('hv.group_'+str(i)+'_v',number('V',0,60000),'V','组'+str(i)+'实际RC电压')
    slot('needle.group_'+str(i)+'_m',number('m',0,.5),'m','组'+str(i)+'竖直位移','scene',c='needle_group_'+str(i))
for i in range(8):slot('plunger.pump_'+str(i)+'_m',number('m',0,1),'m','第'+str(i)+'针筒有限耗液活塞行程','scene',c='plunger_'+str(i))
slot('collector.angle_rad',number('rad',0,2*math.pi),'rad','竖直滚筒轴角','scene',c='collector')
slot('collector.rpm',number('1/min',-5000,5000),'1/min','惯性转子示值',c='collector')
slot('thermal.temperature_k',number('K',1,400),'K','UTA单热节点示值')
slot('thermal.target_k',number('K',288.15,323.15),'K','UTA温度目标','setpoint')
slot('climate.humidity',number('1',0,1),'1','规定RH一阶理想模型')
slot('climate.target_rh',number('1',.2,.7),'1','任务20..70%RH目标','setpoint')
slot('cycle.phase',PHASE,'1','异步电纺状态')
slot('cycle.elapsed_s',number('s',0),'s','该次运行累计时长')
slot('fiber.mean_diameter_m',number('m',0),'m','文学参考缩放的合成直径')
slot('truth.ledger',PRODUCT,'1','双组分/显热/HV账本，只供evaluator','process_truth','evaluator','collector')

class LinariRtadvancedRuntime:
    def __init__(self,instance_id='rtadvanced_01'):
        self.instance_id=instance_id;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']}
        self.time_s=0.;self.revision=0;self.plant=None;self.method=None;self.collector_cfg=None;self.collector_present=False
        self.solutions=[None]*8;self.syringes=[[0.,0.] for _ in range(8)];self.cones=[[0.,0.] for _ in range(8)];self.loaded_volumes=[0.]*8
        self.initial=[0.,0.];self.waste=[0.,0.];self.evaporated=[0.,0.];self.collected=[0.,0.];self.mat=[[0.,0.] for _ in range(32)];self.mat_volume=[0.]*32;self.diameter_moment=0.;self.fiber_mass=0.
        self.powered=False;self.vent=False;self.hv=False;self.climate=False;self.door=True;self.latched=False;self.fault=''
        self.volts=[0.,0.];self.rpm=0.;self.angle=0.;self.needle=[0.,0.];self.direction=[1.,1.];self.temperature=298.15;self.target_k=298.15;self.rh=.4;self.target_rh=.4
        self.heater_input=0.;self.thermal_loss=0.;self.hv_source=0.;self.hv_dissipated=0.;self.instability=[0.]*8;self.phase='idle';self.elapsed_s=0.
        self.operation_id='';self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
    def register_principal(self,principal_id,visibility='agent'):
        if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id='__public_agent__'):
        if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return LinariRtadvancedSession(self,principal_id)
    def describe(self):return self.session().describe(instance_id=self.instance_id)
    def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k!='templates'})
    def _volume(self,m,c):return m[0]/c['solvent_density_kg_m3']+m[1]/c['polymer_density_kg_m3']
    def _safe(self):return self.phase!='running' and not self.hv and max(self.volts)<1e-3 and abs(self.rpm)<1e-3
    def _product(self):
        vals={}
        for loc,m in [('initial',self.initial),('syringe',[sum(x[j] for x in self.syringes) for j in range(2)]),('cone',[sum(x[j] for x in self.cones) for j in range(2)]),('mat',[sum(x[j] for x in self.mat) for j in range(2)]),('waste',self.waste),('evaporated',self.evaporated),('collected',self.collected)]:
            for i,s in enumerate(['solvent','polymer']):vals[loc+'_'+s+'_kg']=m[i]
        area=math.pi*self.collector_cfg['diameter_m']*self.collector_cfg['length_m']/32 if self.collector_cfg else 1.
        vals.update(axial_polymer_kg=[x[1] for x in self.mat],axial_thickness_m=[v/(area*(1-self.plant['mat_porosity'])) if self.plant else 0. for v in self.mat_volume],
          heater_input_j=self.heater_input,thermal_loss_j=self.thermal_loss,sensible_j=self.plant['thermal_capacity_j_k']*(self.temperature-self.plant['ambient_k']) if self.plant else 0.,
          hv_source_j=self.hv_source,hv_dissipated_j=self.hv_dissipated,hv_stored_j=.5*self.plant['voltage_capacitance_f']*sum(v*v for v in self.volts) if self.plant else 0.,
          evidence='synthetic_reduced_order_with_measured_literature_diameter_anchor',template_source=TEMPLATE_REL)
        return vals
    def _diameter(self):return self.diameter_moment/self.fiber_mass if self.fiber_mass>0 else 0.
    def read(self,visibility='agent'):
        if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
        v={'plant.configured':self.plant is not None,'method.configured':self.method is not None,'power.enabled':self.powered,'vent.enabled':self.vent,'hv.enabled':self.hv,'climate.enabled':self.climate,'collector.present':self.collector_present,'door.opened':self.door,'door.angle_rad':1.3 if self.door else 0.,'safety.latched':self.latched,'safety.fault':self.fault,'collector.angle_rad':self.angle,'collector.rpm':self.rpm,'thermal.temperature_k':self.temperature,'thermal.target_k':self.target_k,'climate.humidity':self.rh,'climate.target_rh':self.target_rh,'cycle.phase':self.phase,'cycle.elapsed_s':self.elapsed_s,'fiber.mean_diameter_m':self._diameter(),'truth.ledger':self._product()}
        for i in range(2):v['hv.group_'+str(i)+'_v']=self.volts[i];v['needle.group_'+str(i)+'_m']=self.needle[i]
        for i,c in enumerate(self.solutions):v['plunger.pump_'+str(i)+'_m']=max(0.,self.loaded_volumes[i]-self._volume(self.syringes[i],c))/(math.pi*self.plant['syringe_id_m']**2/4) if c else 0.
        return copy.deepcopy({k:x for k,x in v.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
    def _stop(self,phase='cancelled'):
        if self.phase=='running':self.phase=phase
    def invoke(self,capability_id,**a):
        if capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability_id][0],a)
        # Entire validation/mutation is transactional, including cross-parameter guards.
        old=copy.deepcopy({k:v for k,v in self.__dict__.items() if k!='templates'})
        try:r=self._invoke(capability_id,a)
        except Exception:
            for k in list(self.__dict__):
                if k!='templates':del self.__dict__[k]
            self.__dict__.update(old);raise
        if CAPS[capability_id][4]!='query':self.revision+=1
        return copy.deepcopy(r)
    def _invoke(self,k,a):
        if k=='reference.lookup':
            r=self.templates.get(a['substance_id'])
            if r is None:raise ActionRejected('UNKNOWN_REFERENCE')
            return dict(substance_id=r['substance_id'],mean_diameter_m=r['value'],evidence=r['evidence'],source=r['source'],template_source=TEMPLATE_REL)
        if k=='product.read':return self._product()
        if k=='spinning.measure':return dict(phase=self.phase,temperature_k=self.temperature,humidity=self.rh,voltage_v=self.volts,rpm=self.rpm,needle_z_m=self.needle,mean_fiber_diameter_m=self._diameter(),deposited_polymer_kg=sum(x[1] for x in self.mat),instability_proxy=self.instability,evidence='synthetic_transform_of_literature;NOT_RT_Advanced_measured',template_source=TEMPLATE_REL)
        if k=='plant.configure':
            if self.plant is not None or self.powered or not a['accept_uncalibrated_model']:raise ActionRejected('PRECONDITION_FAILED')
            if 50e-6/(math.pi*a['syringe_id_m']**2/4)>.16:raise ActionRejected('GEOMETRY_STROKE_LIMIT')
            self.plant=a;self.temperature=self.target_k=a['ambient_k'];self.rh=self.target_rh=a['ambient_rh'];return {'configured':True}
        if k=='power.toggle':return self._invoke('power.set',{'enabled':not self.powered})
        if k=='power.set':
            if a['enabled'] and (self.plant is None or self.latched):raise ActionRejected('PRECONDITION_FAILED')
            self.powered=a['enabled']
            if not self.powered:self._stop();self.hv=False;self.climate=False
            return a
        if k=='vent.set':
            self.vent=a['enabled']
            if not self.vent:self._stop('failed');self.hv=False;self.climate=False;self.fault='VENT_UNAVAILABLE'
            return a
        if k=='door.set':
            if a['opened'] and not self._safe():raise ActionRejected('RESIDUAL_ENERGY')
            self.door=a['opened'];return a
        if k=='safety.trip':self.latched=True;self._stop('failed');self.hv=False;self.climate=False;self.fault='EMERGENCY';return {'latched':True}
        if k=='safety.reset':
            if not self._safe() or self.door:raise ActionRejected('PRECONDITION_FAILED')
            self.latched=False;self.fault='';return {'latched':False}
        if k=='hv.set':
            if a['enabled'] and (not self.powered or not self.vent or self.door or self.latched or self.method is None):raise ActionRejected('PRECONDITION_FAILED')
            self.hv=a['enabled'];return a
        if k=='climate.set':
            if a['enabled'] and (not self.powered or self.latched):raise ActionRejected('PRECONDITION_FAILED')
            self.climate=a['enabled'];return a
        if k=='climate.set_targets':
            if not self.plant or a['temperature_k']<self.plant['ambient_k']:raise ActionRejected('PRECONDITION_FAILED')
            self.target_k=a['temperature_k'];self.target_rh=a['humidity'];return a
        if k=='collector.configure':
            if not self._safe() or not self.door or not a['accept_host_attachment'] or any(sum(x)>0 for x in self.mat):raise ActionRejected('PRECONDITION_FAILED')
            if self.method and max(self.method['needle_high_m'])>a['length_m']:raise ActionRejected('COLLECTOR_RANGE')
            self.collector_cfg=a;self.collector_present=True;return {'configured':True}
        if k=='solution.load':
            i=a['pump']
            if not self.plant or not self._safe() or not self.door or self.solutions[i] is not None or not a['accept_material_and_units_assumption']:raise ActionRejected('PRECONDITION_FAILED')
            r=self.templates.get(a['reference_id'])
            if not r or not r['usable_as_runtime_anchor']:raise ActionRejected('UNKNOWN_REFERENCE')
            vol=self._volume([a['solvent_kg'],a['polymer_kg']],a)
            if vol>50e-6:raise ActionRejected('SYRINGE_CAPACITY')
            self.solutions[i]=a;self.syringes[i]=[a['solvent_kg'],a['polymer_kg']];self.loaded_volumes[i]=vol
            for j in range(2):self.initial[j]+=self.syringes[i][j]
            return {'loaded':True}
        if k=='solution.drain':
            i=a['pump']
            if not self._safe() or not self.door:raise ActionRejected('RESIDUAL_ENERGY')
            for j in range(2):self.waste[j]+=self.syringes[i][j]+self.cones[i][j]
            self.syringes[i]=[0.,0.];self.cones[i]=[0.,0.];self.solutions[i]=None;self.loaded_volumes[i]=0.;return {'emptied':True}
        if k=='method.configure':
            if not self._safe():raise ActionRejected('BUSY')
            if any(lo>hi for lo,hi in zip(a['needle_low_m'],a['needle_high_m'])):raise ActionRejected('INVALID_ARGUMENT')
            if self.collector_cfg and max(a['needle_high_m'])>self.collector_cfg['length_m']:raise ActionRejected('COLLECTOR_RANGE')
            if not any(a['flows_m3_s']):raise ActionRejected('INVALID_ARGUMENT')
            self.method=a;return {'configured':True}
        if k=='acquisition.start':
            if not self.powered or not self.vent or not self.hv or self.door or self.latched or not self.collector_present or not self.method or self.phase=='running':raise ActionRejected('PRECONDITION_FAILED')
            if any(q>0 and (self.solutions[i] is None or sum(self.syringes[i])<=0) for i,q in enumerate(self.method['flows_m3_s'])):raise ActionRejected('SYRINGE_EMPTY')
            self.phase='running';self.elapsed_s=0.;self._operation_count+=1;self.operation_id=self.instance_id+':job:'+str(self._operation_count);self.fault=''
            return dict(phase=self.phase,operation_id=self.operation_id)
        if k=='acquisition.stop':self._stop();return {'phase':self.phase}
        if k=='needle.clean':
            if not self._safe() or not self.vent:raise ActionRejected('RESIDUAL_ENERGY')
            for i in range(4*a['group'],4*a['group']+4):
                for j in range(2):self.waste[j]+=self.cones[i][j]
                self.cones[i]=[0.,0.]
            return {'cleaned':True}
        if k=='product.collect':
            if not self._safe() or not self.door or not self.collector_present or any(sum(x)>1e-15 for x in self.cones):raise ActionRejected('RESIDUAL_ENERGY')
            for x in self.mat:
                for j in range(2):self.collected[j]+=x[j]
            self.mat=[[0.,0.] for _ in range(32)];self.mat_volume=[0.]*32;self.collector_present=False;self.diameter_moment=0.;self.fiber_mass=0.
            return dict(recipient_id=a['recipient_id'],attachment_event='host_release_fiber_collector')
        raise ActionRejected('UNKNOWN_CAPABILITY')
    def step(self,dt):
        validate(number('s',0,36000),dt)
        if dt==0:return
        hmax=min(.02,self.plant['thermal_capacity_j_k']/(2*(self.plant['controller_w_k']+self.plant['loss_w_k']))) if self.plant else .02
        n=math.ceil(dt/hmax);h=dt/n
        for _ in range(n):self._substep(h);self.time_s+=h
        self.revision+=1
    def _substep(self,h):
        p=self.plant
        if not p:return
        for g in range(2):
            target=self.method['voltage_v'][g] if self.hv and self.powered and self.vent and not self.door and not self.latched and self.method else 0.
            before=.5*p['voltage_capacitance_f']*self.volts[g]**2
            old=self.volts[g];self.volts[g]=target+(old-target)*math.exp(-h/p['voltage_tau_s'])
            after=.5*p['voltage_capacitance_f']*self.volts[g]**2
            # Passive RC with fixed supply voltage: source work C*Vs*dV; residual is resistor heat.
            work=p['voltage_capacitance_f']*target*(self.volts[g]-old)
            self.hv_source+=max(0.,work);self.hv_dissipated+=max(0.,work-(after-before))
        desired=self.method['rpm'] if self.phase=='running' and self.powered else 0.
        oldrpm=self.rpm;self.rpm=desired+(oldrpm-desired)*math.exp(-h/p['rotor_tau_s'])
        turns=(desired*h+(oldrpm-desired)*p['rotor_tau_s']*(1-math.exp(-h/p['rotor_tau_s'])))/60
        self.angle=(self.angle+turns*2*math.pi)%(2*math.pi)
        power=min(p['heater_w'],max(0.,p['controller_w_k']*(self.target_k-self.temperature))) if self.powered and self.climate and not self.latched else 0.
        loss=p['loss_w_k']*(self.temperature-p['ambient_k']);self.temperature+=(power-loss)*h/p['thermal_capacity_j_k'];self.heater_input+=power*h;self.thermal_loss+=loss*h
        rh_target=self.target_rh if self.powered and self.climate else p['ambient_rh'];self.rh=rh_target+(self.rh-rh_target)*math.exp(-h/p['humidity_tau_s'])
        feeding=self.phase=='running';active_h=min(h,max(0.,self.method['duration_s']-self.elapsed_s)) if feeding else 0.
        if feeding:
            for g in range(2):
                lo,hi=self.method['needle_low_m'][g],self.method['needle_high_m'][g]
                old=self.needle[g];travel=p['needle_speed_m_s']*active_h
                if old<lo:z=min(lo,old+travel);self.direction[g]=1.
                elif old>hi:z=max(hi,old-travel);self.direction[g]=-1.
                elif hi==lo:z=lo
                else:
                    width=hi-lo;phase=(old-lo if self.direction[g]>0 else 2*width-(old-lo));phase=(phase+travel)%(2*width)
                    z=lo+(phase if phase<=width else 2*width-phase);self.direction[g]=1. if phase<width else -1.
                self.needle[g]=max(0.,min(.5,z))
        for i,c in enumerate(self.solutions):
            if c is None:continue
            inflow=[0.,0.];q=self.method['flows_m3_s'][i] if self.method else 0.
            volume=self._volume(self.syringes[i],c)
            if feeding and q>0 and volume>0 and self.method['needle_low_m'][i//4]<=self.needle[i//4]<=self.method['needle_high_m'][i//4]:
                f=min(1.,q*active_h/volume)
                for j in range(2):inflow[j]=self.syringes[i][j]*f;self.syringes[i][j]-=inflow[j]
                if f>=1:self.fault='SYRINGE_EMPTY';self._stop('failed')
            # Exact well-mixed residence for constant source over h; no negative masses.
            out=[0.,0.];decay=math.exp(-h/p['cone_residence_s'])
            for j in range(2):
                new=self.cones[i][j]*decay+(inflow[j]/h)*p['cone_residence_s']*(1-decay)
                out[j]=max(0.,self.cones[i][j]+inflow[j]-new);self.cones[i][j]=new
            g=i//4;field=self.volts[g]/self.method['gap_m'][g] if self.method else 0.
            ratio=.5*EPS0*(p['field_enhancement']*field)**2/(c['surface_tension_n_m']/p['needle_radius_m'])
            self.instability[i]=math.sqrt(max(0.,ratio))
            tip_z=self.needle[g]+.035  # actual reference needle endpoint above moving carriage datum
            if ratio>=1 and self.collector_present and not self.door and 0<=tip_z<=self.collector_cfg['length_m']:
                idx=min(31,int(tip_z/self.collector_cfg['length_m']*32));evap=out[0]*p['jet_solvent_evap_fraction'];self.evaporated[0]+=evap
                self.mat[idx][0]+=out[0]-evap;self.mat[idx][1]+=out[1];self.mat_volume[idx]+=out[1]/c['polymer_density_kg_m3']
                ref=self.templates[c['reference_id']];cond=ref['conditions'];er=cond['voltage_v']/cond['distance_m']
                dia=ref['value']*math.sqrt(max(q,1e-20)/cond['flow_m3_s'])*(er/max(field,1e-9))
                self.fiber_mass+=out[1];self.diameter_moment+=out[1]*dia
            else:
                for j in range(2):self.waste[j]+=out[j]
        evapfrac=1-math.exp(-h/p['mat_evap_tau_s'])
        for x in self.mat:evap=x[0]*evapfrac;x[0]-=evap;self.evaporated[0]+=evap
        if feeding:
            self.elapsed_s+=active_h
            if self.phase=='running' and self.elapsed_s>=self.method['duration_s']-1e-10:self.phase='succeeded'

class LinariRtadvancedSession:
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
        m=export_linari_rtadvanced_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('spinning.measure'))
    def cancel(self,operation_id,request_id):
        self.poll(operation_id);return self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)





def export_linari_rtadvanced_contract_metadata():
    caps={};slots={}
    for k,(s,u,o,v,c,w) in SLOTS.items():
        slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'LinariRtadvancedSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    evolving=[k for k,v in SLOTS.items() if v[2] in ['scene','derived','process_truth']]
    for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
        asynchronous=k in ['acquisition.start'];query=kind=='query'
        caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.linari_rtadvanced.LinariRtadvancedSession.invoke',invocation=f'session.invoke(instance_id="rtadvanced_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),
          preconditions=[] if query else [dict(description_zh='电源、门、排风、残留高压/转速、有限针筒、收集器与宿主身份检查',state_ids=['power.enabled','vent.enabled','door.opened','collector.present','cycle.phase'],enforced_by='runtime',check='Runtime.invoke atomic guards; Session trusted-principal filtering')],
          state_effects=[] if query else [dict(state_id=s,timing='immediate',description_zh='设定/附着事件/有限装料变化') for s in effects]+[dict(state_id=s,timing='on_step',description_zh='宿主唯一时钟演化') for s in evolving],
          observes=[],model_effects=dict(mode='none',description_zh='模型实际求值证据尚待独立核验',binding_ids=[]),
          execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',completion_criterion='运行时长结束，不等于得到合格纤维' if asynchronous else '事务返回；物理量由宿主step演化',
            completion_state_ids=['cycle.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',retry='same_request_id',resources=['host_clock','syringes','collector','request_cache'],
            partial_failure_zh='取消停泵但锥残留继续沉积或滴落；高压和转子有限衰减。仅进程内请求去重。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','PRECONDITION_FAILED','BUSY','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','SYRINGE_CAPACITY','SYRINGE_EMPTY','RESIDUAL_ENERGY','GEOMETRY_STROKE_LIMIT','COLLECTOR_RANGE']],
          example_arguments=copy.deepcopy(ex),verification=[],limitations=['降阶电纺教学近似，非厂家固件、标定、安全或真实材料配方建议'],source_ids=['manufacturer_primary','simulation_assumption'])
    mech=dict(component_ids=['needle_group_0','needle_group_1','collector','hv_0','hv_1','uta_port'],state_ids=list(slots),meaning_zh='双高压/八有限针筒/锥体停留/应力门限/文献直径缩放/轴向纤维质量',
      implementation='lumped',equations=['V(t+h)=Vs+(V-Vs)*exp(-h/tau); E_HV=C*sum(V^2)/2; source_work=C*Vs*dV; dissipated=source_work-dE',
      'rpm(t+h)=rpm_target+(rpm-rpm_target)*exp(-h/tau_rotor); integrate angle analytically',
      'Cthermal*dT/dt=Pheater-loss*(T-Ta); RH(t+h)=RHtarget+(RH-RHtarget)*exp(-h/tau_RH); prescribed humidity, NOT moisture mass balance',
      'Q_i<=finite syringe remaining/dt; cone dM/dt=inflow-M/tau; exact constant-inflow update; residual survives cancel',
      'stress_ratio=epsilon0*(beta*V/gap)^2/(2*gamma/r_needle); ratio>=1 deposits, else drips; instability_proxy=sqrt(ratio) is not calibrated jet stability',
      'fiber_diameter=measured_reference_mean*sqrt(Q/Qref)*(Vref/dref)/(V/gap); explicit uncalibrated Q/E scaling',
      'each species: initial=syringe+cone+mat+waste+evaporated+collected; solvent evaporation fraction/decay prescribed',
      'tip_z=carriage_z+0.035m; finite collector catches only0<=tip_z<=length; axial bin=floor(tip_z/length*32)',
      'axial_thickness=polymer_volume/(pi*collector_diameter*(length/32)*(1-porosity)); no radial morphology solve',
      'plunger_consumed=(loaded_volume-remaining_volume)/(pi*syringe_id^2/4)'],
      parameters={},source_ids=['manufacturer_primary','fiber_dataset','simulation_assumption'],
      limitations=['泰勒锥/射流仅停留体积、Maxwell-毛细应力门限与直径缩放，未解粘弹/电荷/弯曲不稳定性CFD。','153保守归一材料系统标签含共混、别名；1823实测文献均径不等于50纯物质或本机实测。','显式宿主密度、表面张力、热容/功率、场增强、衰减常数/孔隙率未标定。','RH规定控制，不解湿空气质量/潜热；高压电容仅教学RC不证明安全。','8有限针筒、两高压组、单滚筒；可选收集器偏置、吹纺、激光闭环、带式收集、完整GUI、硬件机器人未实现。'])
    spec=importlib.util.spec_from_file_location('_linari_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann)
    ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
    ann.annotate_export(SLUG,caps,slots,{'electrospinning':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'solution.load':(SOLUTION,SOLUTION_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
    mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'electrospinning':mech})

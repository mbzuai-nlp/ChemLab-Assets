"""CHEM-057: finite seven-cell twin-screw surrogate with species and heat balances.

v1.1: 55-record real-data template library; material.load_reference drives the model with
literature viscosity-temperature laws, reference.lookup returns exact measured conditions,
material.lookup returns any record.  Process outputs remain model predictions (synthetic)."""
from __future__ import annotations
import copy, importlib.util, json, math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SLUG='thermo_process11'; ASSET_TYPE_ID='instrument.'+SLUG; CATALOG_ID='CHEM-057'; PROTOCOL_VERSION='1.0.0'; ASSET_VERSION='1.1.0'
TEMPLATE_REL='assets/reactions/thermo_process11_extrusion_library/templates.json'
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





TAU=2*math.pi; OMEGA_MIN=10*TAU/60; OMEGA_MAX=1000*TAU/60; FEED_MIN=.02/3600; FEED_MAX=2.5/3600
OMEGA=number('rad/s',OMEGA_MIN,OMEGA_MAX); TEMP=number('K',273.15,623.15)
RATE=number('kg/s',FEED_MIN,FEED_MAX); PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
def array(s,n):return dict(type='array',items=s,minItems=n,maxItems=n)
PLANT=obj({'ambient_k':number('K',283.15,313.15),'zone_heat_capacity_j_k':number('J/K',10,10000),'heater_max_w':number('W',1,500),
'loss_w_k':number('W/K',0,20),'controller_w_k':number('W/K',.1,100),'zone_capacity_kg':number('kg',.0001,.05),
'transport_per_rev':number('1',.001,1),'drag_coefficient_m3':number('m^3',1e-9,.001),'acceleration_rad_s2':number('rad/s^2',.1,500),
'cover_speed_m_s':number('m/s',.001,.1),'accept_uncalibrated_model':BOOL})
PLANT_EXAMPLE=dict(ambient_k=298.15,zone_heat_capacity_j_k=20.,heater_max_w=100.,loss_w_k=.01,controller_w_k=20.,
zone_capacity_kg=.002,transport_per_rev=.15,drag_coefficient_m3=1e-5,acceleration_rad_s2=50.,cover_speed_m_s=.05,accept_uncalibrated_model=True)
METHOD=obj({'duration_s':number('s',.01,3600),'speed_rad_s':OMEGA,'feed_kg_s':RATE,'die_radius_m':number('m',.0001,.005),'die_length_m':number('m',.0001,.1),'mixing_factor':number('1',.1,10)})
METHOD_EXAMPLE=dict(duration_s=20.,speed_rad_s=200*TAU/60,feed_kg_s=.5/3600,die_radius_m=.001,die_length_m=.005,mixing_factor=1.)
MATERIAL=obj({'reference_id':TEXT,'base_kg':number('kg',0,.05),'additive_kg':number('kg',0,.05),'density_kg_m3':number('kg/m^3',100,5000),
'heat_capacity_j_kg_k':number('J/(kg*K)',100,10000),'viscosity_ref_pa_s':number('Pa*s',.01,1e6),'reference_temperature_k':TEMP,
'activation_j_mol':number('J/mol',0,1000000),'minimum_processing_k':TEMP,'accept_uncalibrated_newtonian':BOOL})
MATERIAL_EXAMPLE=dict(reference_id='pcm30_aff70',base_kg=.014,additive_kg=.006,density_kg_m3=1200.,heat_capacity_j_kg_k=1800.,
viscosity_ref_pa_s=1000.,reference_temperature_k=438.15,activation_j_mol=15000.,minimum_processing_k=413.15,accept_uncalibrated_newtonian=True)
PRODUCT_FIELDS={loc+'_'+sp+'_kg':number('kg',0) for loc in ['initial','hopper','barrel','product','collected','waste'] for sp in ['base','additive']}
PRODUCT_FIELDS.update(zone_mass_kg=array(number('kg',0),7),heater_energy_j=number('J',0),mechanical_energy_j=number('J',0),
loss_energy_j=number('J'),exported_enthalpy_j=number('J'),stored_enthalpy_j=number('J'),energy_residual_j=number('J'),
mean_residence_s=number('s',0),residence_available=BOOL,evidence=TEXT,template_source=TEXT)
PRODUCT=obj(PRODUCT_FIELDS)
RESULT=obj({'phase':PHASE,'elapsed_s':number('s',0),'speed_rad_s':number('rad/s',0,OMEGA_MAX),
'torque_per_shaft_nm':number('N*m',0),'die_pressure_gauge_pa':number('Pa',0),'flow_kg_s':number('kg/s',0),
'zone_temperature_k':array(number('K',1),7),'fault':TEXT,'evidence':TEXT,'template_source':TEXT})
def nullable(s):s=copy.deepcopy(s);s['type']=[s['type'],'null'];return s
NTEXT={'type':['string','null']}
REFERENCE=obj({'reference_id':TEXT,'temperature_k':TEMP,'torque_total_range_nm':nullable(array(number('N*m',0),2)),
'pressure_range_pa':nullable(array(number('Pa',0),2)),'outlier_pressure_pa':dict(type='array',items=number('Pa',0)),
'evidence':TEXT,'source':TEXT,'template_source':TEXT,'scope':TEXT,
'variant':NTEXT,'torque_percent_range':nullable(array(number('%',0),2)),'torque_reported_range_nm':nullable(array(number('N*m',0),2)),'torque_basis':NTEXT,
'screw_speed_rad_s':nullable(number('rad/s',0)),'feed_kg_s':nullable(number('kg/s',0)),'melt_temperature_k':nullable(number('K',0)),
'viscosity_pa_s':nullable(number('Pa*s',0)),'sme_j_kg':nullable(number('J/kg',0)),'extruder':TEXT,'licence_flag':TEXT})
REFERENCE_IN=dict(type='object',properties={'reference_id':TEXT,'temperature_k':TEMP,'screw_speed_rad_s':number('rad/s',0,OMEGA_MAX),'variant':TEXT},required=['reference_id','temperature_k'],additionalProperties=False)
LAW=nullable(obj({'form':dict(type='string',enum=['arrhenius','wlf_exp']),'viscosity_ref_pa_s':number('Pa*s',0),'reference_temperature_k':number('K',0),
'activation_j_mol':nullable(number('J/mol',0)),'c1':nullable(number('1')),'c2_k':nullable(number('K')),'evidence':TEXT,'valid_range_k':nullable(array(number('K',0),2))}))
MATERIAL_INFO=obj({'reference_id':TEXT,'record_type':dict(type='string',enum=['material_reference','process_outcome']),'name_en':TEXT,'material_class':TEXT,'cas':NTEXT,
'runtime_usage':dict(type='string',enum=['process_model','exact_condition_lookup','reference_only']),'tg_k':nullable(number('K',0)),'tm_k':nullable(number('K',0)),
'degradation_onset_k':nullable(number('K',0)),'extrudable_window_k':nullable(array(number('K',0),2)),'minimum_processing_k':nullable(number('K',0)),
'viscosity_law':LAW,'density_kg_m3':nullable(number('kg/m^3',0)),'heat_capacity_j_kg_k':nullable(number('J/(kg*K)',0)),'neat_extrudable':{'type':['boolean','null']},
'lookup_temperatures_k':dict(type='array',items=number('K',0)),'condition_count':dict(type='integer',minimum=0,**{'x-unit':'1'}),
'evidence':TEXT,'source':TEXT,'licence_flag':TEXT,'template_source':TEXT})
LOAD_REF=dict(type='object',properties={'reference_id':TEXT,'mass_kg':number('kg',1e-6,.05),'density_kg_m3':number('kg/m^3',100,5000),
'heat_capacity_j_kg_k':number('J/(kg*K)',100,10000),'accept_uncalibrated_newtonian':BOOL},required=['reference_id','mass_kg','accept_uncalibrated_newtonian'],additionalProperties=False)
LOAD_REF_EXAMPLE={'reference_id':'kollidon_va64','mass_kg':.02,'accept_uncalibrated_newtonian':True}
CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=()):CAPS[k]=(i,o,c,z,kind,vis,ex or {},list(effects))
for key,s,ex,vis,c in [('plant',PLANT,PLANT_EXAMPLE,'evaluator','body'),('method',METHOD,METHOD_EXAMPLE,'agent','display_digits'),('material',MATERIAL,MATERIAL_EXAMPLE,'evaluator','feed_port')]:
    cap(key+'.configure',s,obj({'configured':BOOL}),[c],'宿主配置显式参数或单次有限装料','configuration',vis,ex,[key+'.configured'])
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['switch_power'],'主电源目标；断电停止驱动及加热',ex={'enabled':True},effects=['power.enabled','drive.enabled','thermal.enabled','cycle.phase'])
cap('power.toggle',obj({}),obj({'enabled':BOOL}),['switch_power'],'主电源目标翻转',effects=['power.enabled','drive.enabled','thermal.enabled','cycle.phase'])
cap('coolant.set_connected',obj({'connected':BOOL}),obj({'connected':BOOL}),['coolant_in','coolant_out'],'宿主确认进料段冷却水回路，非流量传感器','configuration','evaluator',{'connected':True},['coolant.connected','drive.enabled','cycle.phase'])
cap('barrel.set_open',obj({'opened':BOOL}),obj({'target_m':number('m',0,.15)}),['barrel_upper'],'宿主拆卸或回装上半筒目标；非机载升降','configuration','agent',{'opened':True},['barrel.target_m'])
cap('drive.set_speed',obj({'speed_rad_s':OMEGA}),obj({'speed_rad_s':OMEGA}),['motor','screw_a','screw_b'],'双螺杆同向速度目标',ex={'speed_rad_s':200*TAU/60},effects=['drive.target_rad_s'])
cap('drive.set_enabled',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['motor','screw_a','screw_b'],'空转或热料驱动，冷却水/闭筒/功率互锁',ex={'enabled':True},effects=['drive.enabled','cycle.phase'])
cap('feed.set_rate',obj({'mass_rate_kg_s':RATE}),obj({'mass_rate_kg_s':RATE}),['feed_port'],'宿主外置定量进料速率，不声称内置称重传感器',ex={'mass_rate_kg_s':.5/3600},effects=['feed.target_kg_s'])
cap('thermal.set_zone',obj({'zone':dict(type='integer',minimum=0,maximum=6,**{'x-unit':'1'}),'temperature_k':TEMP}),obj({'zone':dict(type='integer',minimum=0,maximum=6,**{'x-unit':'1'}),'temperature_k':TEMP}),['heater_'+str(i) for i in range(7)],'七段热区目标，实际温度仅随step变化',ex={'zone':0,'temperature_k':438.15},effects=['thermal.targets_k'])
cap('thermal.set_enabled',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['heater_'+str(i) for i in range(7)],'独立加热开关；停止进料不瞬时冷却',ex={'enabled':True},effects=['thermal.enabled'])
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['display_digits','feed_port','motor'],'定时有限进料；先预热并启动螺杆',effects=['cycle.phase','cycle.elapsed_s'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['display_digits','motor'],'停止进料和驱动；保留筒内残料及热量',effects=['cycle.phase','drive.enabled'])
cap('emergency.stop',obj({}),obj({'latched':BOOL}),['button_emergency'],'宿主急停锁存，停止热源/驱动/进料',effects=['safety.pressed','safety.latched','safety.fault','drive.enabled','thermal.enabled','cycle.phase'])
cap('emergency.release',obj({}),obj({'released':BOOL}),['button_emergency'],'仅释放急停，不自动复位故障','configuration','agent',{},['safety.pressed'])
cap('fault.reset',obj({}),obj({'reset':BOOL}),['display_digits'],'电源关、急停已释放且不过温时人工复位；教学互锁','configuration','agent',{},['safety.latched','safety.fault'])
cap('extrusion.measure',obj({}),RESULT,['display_digits'],'理想扭矩/压力/七区温度读数，非材料实测曲线','query')
cap('product.read',obj({}),PRODUCT,['die','receiver'],'双组分与热能/平均年龄账本','query','evaluator')
cap('product.collect',obj({'recipient_id':TEXT,'accept_hot_output':BOOL}),obj({'recipient_id':TEXT,'attachment_event':TEXT}),['receiver'],'宿主接收已排出的材料，显式处理热料','configuration','evaluator',{'recipient_id':'extrudate','accept_hot_output':True},['truth.ledger'])
cap('material.clear',obj({'accept_hot_residue':BOOL}),obj({'emptied':BOOL}),['feed_port','barrel_lower','waste_port'],'宿主停止后移除料斗/筒内残料；非自动清洗','configuration','evaluator',{'accept_hot_residue':True},['truth.ledger'])
cap('reference.lookup',REFERENCE_IN,REFERENCE,['display_digits'],'原论文特定工况实测摘要（扭矩/压力/黏度/熔温）；仅精确温度（及可选转速/变体）匹配，不外推、不当作标准口模校准','query','agent',{'reference_id':'pcm30_aff70','temperature_k':438.15})
cap('material.lookup',obj({'reference_id':TEXT}),MATERIAL_INFO,['display_digits'],'查询库中任一真实材料/工况记录的转变温度、黏度律、密度热容、许可标记与运行时用途','query','agent',{'reference_id':'soluplus'})
cap('material.load_reference',LOAD_REF,obj({'configured':BOOL,'parameter_evidence':TEXT}),['feed_port'],'宿主单次装入一种库内 process_model 材料：黏度-温度律/最低加工温度/降解温度取自文献；缺真实密度或热容时必须显式给出并记为假设','configuration','evaluator',LOAD_REF_EXAMPLE,['material.configured','truth.ledger'])
CAPS['plant.configure'][7].extend(['thermal.targets_k']+['thermal.zone_'+str(i)+'_k' for i in range(7)])
CAPS['material.configure'][7].append('truth.ledger')
CAPS['method.configure'][7].extend(['drive.target_rad_s','feed.target_kg_s'])
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='body',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
for key in ['plant','method','material']:slot(key+'.configured',BOOL,'1',key+'是否已由宿主配置','setpoint')
slot('power.enabled',BOOL,'1','主电源目标','setpoint',c='switch_power',w=('power.set','enabled'))
slot('coolant.connected',BOOL,'1','宿主确认永久冷却的进料区水路，不是流量计','setpoint',c='coolant_in')
slot('barrel.target_m',number('m',0,.15),'m','宿主拆装上半筒Z向目标，非机载运动','setpoint',c='barrel_upper')
slot('barrel.position_m',number('m',0,.15),'m','上半筒相对闭合位置的宿主位移代理','scene',c='barrel_upper')
slot('drive.enabled',BOOL,'1','驱动命令；实际速度由step推进','setpoint',c='motor',w=('drive.set_enabled','enabled'))
slot('drive.target_rad_s',OMEGA,'rad/s','螺杆10–1000rpm换算SI的速度目标','setpoint',c='motor',w=('drive.set_speed','speed_rad_s'))
slot('drive.speed_rad_s',number('rad/s',0,OMEGA_MAX),'rad/s','理想实际同向角速度，未标定加速度','derived',c='display_digits')
for x in ['a','b']:slot('screw.'+x+'_angle_rad',number('rad',0,TAU),'rad','同向绕-X螺杆相位，模2π显示，非转数','scene',c='screw_'+x)
slot('feed.target_kg_s',RATE,'kg/s','宿主进料器质量流目标，厂家材料相关吞吐边界','setpoint',c='feed_port',w=('feed.set_rate','mass_rate_kg_s'))
slot('feed.actual_kg_s',number('kg/s',0,FEED_MAX),'kg/s','受料斗有限库存和区段容量约束的实际进料量','derived',c='display_digits')
slot('thermal.targets_k',array(TEMP,7),'K','七加热区目标；冷却进料段不计为电热区','setpoint',c='barrel_lower')
slot('thermal.enabled',BOOL,'1','七区加热命令','setpoint',c='barrel_lower',w=('thermal.set_enabled','enabled'))
for i in range(7):slot('thermal.zone_'+str(i)+'_k',number('K',1),'K','第'+str(i+1)+'区筒体/物料瞬时等温假设理想读数','derived',c='heater_'+str(i))
slot('extrusion.torque_per_shaft_nm',number('N*m',0),'N*m','每轴合成负载，限值6Nm，不与总轴12Nm混用','derived',c='display_digits')
slot('extrusion.die_pressure_gauge_pa',number('Pa',0),'Pa','口模相对外部环境压差；100bar按教学表压解释','derived',c='pressure_sensor')
slot('extrusion.output_kg_s',number('kg/s',0),'kg/s','出口实际有限质量流量，非实机称量','derived',c='die')
slot('cycle.phase',PHASE,'1','定时程序状态，成功不等于所有料已排净',c='display_digits')
slot('cycle.elapsed_s',number('s',0),'s','当前定时进料操作宿主累计时间',c='display_digits')
for k,z in [('pressed','宿主急停按下'),('latched','教学故障锁存')]:slot('safety.'+k,BOOL,'1',z,c='button_emergency')
slot('safety.fault',TEXT,'1','压力/扭矩/过热、冷料或文献降解起始温度故障代码',c='display_digits')
slot('truth.ledger',PRODUCT,'1','有限双组分库存及整体能量和年龄账本','process_truth','evaluator','receiver')

class ThermoProcess11Runtime:
    def __init__(self,instance_id='process11_01'):
        self.instance_id=instance_id;self.templates=json.loads((ROOT/TEMPLATE_REL).read_text())['templates']
        self.time_s=0.;self.revision=0;self.powered=False;self.coolant=False;self.drive=False;self.heating=False
        self.plant=None;self.method=None;self.material=None;self.cover=0.;self.cover_target=0.
        self.speed_target=200*TAU/60;self.speed=0.;self.angle=0.;self.feed_target=.5/3600;self.actual_feed=0.
        self.temps=[298.15]*7;self.targets=[298.15]*7;self.torque=0.;self.pressure=0.;self.output_rate=0.
        self.phase='idle';self.elapsed_s=0.;self.pressed=False;self.latched=False;self.fault=''
        self.initial=[0.,0.];self.hopper=[0.,0.];self.zones=[[0.,0.] for _ in range(7)];self.product=[0.,0.];self.collected=[0.,0.];self.waste=[0.,0.]
        self.age_mass=[0.]*7;self.output_age_mass=0.;self.heater_energy=0.;self.mechanical_energy=0.;self.loss_energy=0.;self.exported_enthalpy=0.
        self.operation_id='';self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
    def register_principal(self,principal_id,visibility='agent'):
        if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id='__public_agent__'):
        if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return ThermoProcess11Session(self,principal_id)
    def describe(self):return self.session().describe(instance_id=self.instance_id)
    def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k!='templates'})
    def _stored(self):
        if not self.plant:return 0.
        cp=self.material['heat_capacity_j_kg_k'] if self.material else 0.
        return sum((self.plant['zone_heat_capacity_j_k']+sum(m)*cp)*(t-self.plant['ambient_k']) for m,t in zip(self.zones,self.temps))
    def _ledger(self):
        barrel=[sum(z[j] for z in self.zones) for j in range(2)]
        d={loc+'_'+sp+'_kg':m[j] for loc,m in [('initial',self.initial),('hopper',self.hopper),('barrel',barrel),('product',self.product),('collected',self.collected),('waste',self.waste)] for j,sp in enumerate(['base','additive'])}
        produced=sum(self.product)+sum(self.collected);stored=self._stored()
        d.update(zone_mass_kg=[sum(z) for z in self.zones],heater_energy_j=self.heater_energy,mechanical_energy_j=self.mechanical_energy,
            loss_energy_j=self.loss_energy,exported_enthalpy_j=self.exported_enthalpy,stored_enthalpy_j=stored,
            energy_residual_j=self.heater_energy+self.mechanical_energy-self.loss_energy-self.exported_enthalpy-stored,
            mean_residence_s=self.output_age_mass/produced if produced else 0.,residence_available=produced>0,
            evidence=self._evidence('synthetic_seven_CSTR_Newtonian_uncalibrated'),template_source=TEMPLATE_REL)
        return d
    def _measure(self):
        return dict(phase=self.phase,elapsed_s=self.elapsed_s,speed_rad_s=self.speed,torque_per_shaft_nm=self.torque,
            die_pressure_gauge_pa=self.pressure,flow_kg_s=self.output_rate,zone_temperature_k=self.temps.copy(),fault=self.fault,
            evidence=self._evidence('synthetic_not_measured_trace'),template_source=TEMPLATE_REL)
    def read(self,visibility='agent'):
        if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
        d={'plant.configured':self.plant is not None,'method.configured':self.method is not None,'material.configured':self.material is not None,
        'power.enabled':self.powered,'coolant.connected':self.coolant,'barrel.target_m':self.cover_target,'barrel.position_m':self.cover,
        'drive.enabled':self.drive,'drive.target_rad_s':self.speed_target,'drive.speed_rad_s':self.speed,
        'screw.a_angle_rad':self.angle,'screw.b_angle_rad':(self.angle+math.pi/2)%TAU,
        'feed.target_kg_s':self.feed_target,'feed.actual_kg_s':self.actual_feed,'thermal.targets_k':self.targets.copy(),'thermal.enabled':self.heating,
        'extrusion.torque_per_shaft_nm':self.torque,'extrusion.die_pressure_gauge_pa':self.pressure,'extrusion.output_kg_s':self.output_rate,
        'cycle.phase':self.phase,'cycle.elapsed_s':self.elapsed_s,'safety.pressed':self.pressed,'safety.latched':self.latched,'safety.fault':self.fault}
        d.update({'thermal.zone_'+str(i)+'_k':v for i,v in enumerate(self.temps)})
        if visibility!='agent':d['truth.ledger']=self._ledger()
        return copy.deepcopy(d)
    def _idle(self):
        if self.drive or self.phase=='running':raise ActionRejected('BUSY')
    def _ready(self):
        if not self.plant or not self.powered or not self.coolant or self.latched or self.cover>1e-9 or self.cover_target>1e-9:raise ActionRejected('PRECONDITION_FAILED')
    def _halt(self,status='cancelled'):
        self.drive=False
        if self.phase=='running':self.phase=status
    def _trip(self,code):
        self.fault=code;self.latched=True;self.heating=False;self._halt('failed')
    def _evidence(self,base):
        m=self.material or {}
        return base+'_with_literature_material_law:'+m['reference_id'] if m.get('law') else base
    def _viscosity(self,t):
        m=self.material
        if m.get('law')=='wlf_exp':
            d=t-m['reference_temperature_k'];x=-m['c1']*d/max(1e-6,m['c2_k']+d)
        else:x=m['activation_j_mol']/8.314462618*(1/t-1/m['reference_temperature_k'])
        return m['viscosity_ref_pa_s']*math.exp(max(-50,min(50,x)))
    OUTCOME_KEYS=('torque_percent_range','torque_reported_range_nm','torque_total_range_nm','pressure_range_pa','viscosity_pa_s','melt_temperature_k')
    def _reference(self,rid,t,speed=None,variant=None):
        if rid not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
        rec=self.templates[rid];rows=[r for r in rec['conditions'] if r.get('temperature_k') is not None and abs(r['temperature_k']-t)<1e-8 and any(r.get(k) is not None for k in self.OUTCOME_KEYS)]
        if speed is not None:rows=[r for r in rows if r.get('screw_speed_rad_s') is not None and abs(r['screw_speed_rad_s']-speed)<1e-8]
        if variant is not None:rows=[r for r in rows if r.get('variant')==variant]
        if not rows:raise ActionRejected('REFERENCE_OUT_OF_RANGE')
        if len(rows)>1:raise ActionRejected('AMBIGUOUS_REFERENCE')
        row=rows[0];cp=lambda k:copy.deepcopy(row.get(k))
        legacy=rid=='pcm30_aff70'
        return dict(reference_id=rid,temperature_k=t,torque_total_range_nm=cp('torque_total_range_nm'),
            pressure_range_pa=cp('pressure_range_pa'),outlier_pressure_pa=list(row.get('outlier_pressure_pa') or []),
            evidence='measured_literature_summary_NOT_time_series',source=rec['source'],template_source=TEMPLATE_REL,
            scope='30wt% PCM/70wt% Affinisol HPMC15LV; custom printer die; not this standard round die calibration' if legacy else rec['name_en']+'; '+row.get('extruder','')+'; '+row.get('temperature_basis','')+'; exact published condition only, not this round-die calibration',
            variant=row.get('variant'),torque_percent_range=cp('torque_percent_range'),torque_reported_range_nm=cp('torque_reported_range_nm'),torque_basis=row.get('torque_basis'),
            screw_speed_rad_s=row.get('screw_speed_rad_s'),feed_kg_s=row.get('feed_kg_s'),melt_temperature_k=row.get('melt_temperature_k'),viscosity_pa_s=row.get('viscosity_pa_s'),
            sme_j_kg=row.get('sme_j_kg'),extruder=row.get('extruder',''),licence_flag=rec.get('licence_flag','permissive'))
    def _material_info(self,rid):
        if rid not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
        r=self.templates[rid];p=r.get('properties') or {};law=r.get('viscosity_law')
        return copy.deepcopy(dict(reference_id=rid,record_type=r['record_type'],name_en=r['name_en'],material_class=r.get('material_class',''),cas=r.get('cas'),runtime_usage=r['runtime_usage'],
            tg_k=p.get('tg_k'),tm_k=p.get('tm_k'),degradation_onset_k=r.get('degradation_onset_k'),extrudable_window_k=r.get('extrudable_window_k'),
            minimum_processing_k=(r.get('minimum_processing') or {}).get('temperature_k'),
            viscosity_law=None if not law else dict(form=law['form'],viscosity_ref_pa_s=law['viscosity_ref_pa_s'],reference_temperature_k=law['reference_temperature_k'],
                activation_j_mol=law.get('activation_j_mol'),c1=law.get('c1'),c2_k=law.get('c2_k'),evidence=law['evidence'],valid_range_k=law.get('valid_range_k')),
            density_kg_m3=(r.get('density') or {}).get('value_kg_m3'),heat_capacity_j_kg_k=(r.get('heat_capacity') or {}).get('value_j_kg_k'),neat_extrudable=r.get('neat_extrudable'),
            lookup_temperatures_k=r.get('lookup_temperatures_k',[]),condition_count=len(r.get('conditions',[])),evidence=r.get('evidence','measured_literature'),source=r['source'],
            licence_flag=r.get('licence_flag','permissive'),template_source=TEMPLATE_REL))
    def _load_reference(self,a):
        rid=a['reference_id']
        if rid not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
        r=self.templates[rid]
        if r['runtime_usage']!='process_model':raise ActionRejected('REFERENCE_NOT_RUNTIME_USABLE')
        if not self.plant or self.material is not None or not a['accept_uncalibrated_newtonian']:raise ActionRejected('PRECONDITION_FAILED')
        law=r['viscosity_law'];tmin=r['minimum_processing']['temperature_k'];dens=(r.get('density') or {}).get('value_kg_m3');cp=(r.get('heat_capacity') or {}).get('value_j_kg_k')
        # Real values are never overridden; missing ones must be supplied explicitly by the host (assumption).
        if (dens is None)!=('density_kg_m3' in a) or (cp is None)!=('heat_capacity_j_kg_k' in a) or tmin<self.plant['ambient_k']:raise ActionRejected('INVALID_ARGUMENT')
        ev=('density:'+('literature' if dens is not None else 'host_assumption')+';heat_capacity:'+('literature' if cp is not None else 'host_assumption')+';viscosity_law:'+law['form']+'('+law['evidence']+')')
        self.material=dict(reference_id=rid,base_kg=a['mass_kg'],additive_kg=0.,density_kg_m3=dens if dens is not None else a['density_kg_m3'],
            heat_capacity_j_kg_k=cp if cp is not None else a['heat_capacity_j_kg_k'],viscosity_ref_pa_s=law['viscosity_ref_pa_s'],reference_temperature_k=law['reference_temperature_k'],
            activation_j_mol=law.get('activation_j_mol') or 0.,c1=law.get('c1'),c2_k=law.get('c2_k'),law=law['form'],minimum_processing_k=tmin,
            degradation_onset_k=r.get('degradation_onset_k'),accept_uncalibrated_newtonian=True,parameter_evidence=ev)
        self.initial=[a['mass_kg'],0.];self.hopper=self.initial.copy()
        return {'configured':True,'parameter_evidence':ev}
    def invoke(self,capability,**a):
        if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability][0],a);c=capability
        if c=='extrusion.measure':return self._measure()
        if c=='product.read':return self._ledger()
        if c=='reference.lookup':return self._reference(a['reference_id'],a['temperature_k'],a.get('screw_speed_rad_s'),a.get('variant'))
        if c=='material.lookup':return self._material_info(a['reference_id'])
        if c=='plant.configure':
            if self.plant is not None or not a['accept_uncalibrated_model']:raise ActionRejected('PRECONDITION_FAILED')
            self.plant=copy.deepcopy(a);self.temps=[a['ambient_k']]*7;self.targets=self.temps.copy();r={'configured':True}
        elif c=='method.configure':
            self._idle()
            if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
            self.method=copy.deepcopy(a);self.speed_target=a['speed_rad_s'];self.feed_target=a['feed_kg_s'];r={'configured':True}
        elif c=='material.configure':
            self._idle()
            if not self.plant or self.material is not None or not a['accept_uncalibrated_newtonian'] or sum([a['base_kg'],a['additive_kg']])<=0 or a['reference_id'] not in self.templates:raise ActionRejected('PRECONDITION_FAILED')
            # The one supported formulation is 70/30 by mass; properties remain explicit assumptions.
            if abs(a['additive_kg']/(a['base_kg']+a['additive_kg'])-.3)>1e-9 or a['minimum_processing_k']<self.plant['ambient_k']:raise ActionRejected('INVALID_ARGUMENT')
            self.material=copy.deepcopy(a);self.initial=[a['base_kg'],a['additive_kg']];self.hopper=self.initial.copy();r={'configured':True}
        elif c=='material.load_reference':
            self._idle();r=self._load_reference(a)
        elif c in ['power.set','power.toggle']:
            value=a['enabled'] if c=='power.set' else not self.powered
            if value and (not self.plant or self.latched):raise ActionRejected('PRECONDITION_FAILED')
            self.powered=value
            if not value:self.heating=False;self._halt()
            r={'enabled':value}
        elif c=='coolant.set_connected':
            self.coolant=a['connected']
            if not self.coolant:self._halt()
            r={'connected':self.coolant}
        elif c=='barrel.set_open':
            self._idle()
            if not self.plant or self.powered or max(self.temps)>323.15 or any(sum(z)>0 for z in self.zones):raise ActionRejected('PRECONDITION_FAILED')
            self.cover_target=.15 if a['opened'] else 0.;r={'target_m':self.cover_target}
        elif c=='drive.set_speed':
            self.speed_target=a['speed_rad_s'];r=copy.deepcopy(a)
        elif c=='drive.set_enabled':
            if a['enabled']:
                self._ready()
                if any(sum(z)>0 for z in self.zones) and min(self.temps)<self.material['minimum_processing_k']:raise ActionRejected('COLD_MATERIAL')
                self.drive=True
            else:self._halt()
            r={'enabled':self.drive}
        elif c=='feed.set_rate':
            self.feed_target=a['mass_rate_kg_s'];r=copy.deepcopy(a)
        elif c=='thermal.set_zone':
            if not self.plant or a['temperature_k']<self.plant['ambient_k']:raise ActionRejected('INVALID_ARGUMENT')
            self.targets[a['zone']]=a['temperature_k'];r=copy.deepcopy(a)
        elif c=='thermal.set_enabled':
            if a['enabled']:self._ready()
            self.heating=a['enabled'];r={'enabled':self.heating}
        elif c=='acquisition.start':
            self._ready()
            if self.phase=='running':raise ActionRejected('BUSY')
            if not self.drive or not self.method or not self.material or sum(self.hopper)<=0:raise ActionRejected('PRECONDITION_FAILED')
            if min(self.temps)<self.material['minimum_processing_k']:raise ActionRejected('COLD_MATERIAL')
            self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);self.phase='running';self.elapsed_s=0.
            r={'phase':self.phase,'operation_id':self.operation_id}
        elif c=='acquisition.stop':
            self._halt();r={'phase':self.phase}
        elif c=='emergency.stop':
            self.pressed=True;self._trip('EMERGENCY_STOP');r={'latched':True}
        elif c=='emergency.release':
            self.pressed=False;r={'released':True}
        elif c=='fault.reset':
            if self.powered or self.pressed or max(self.temps)>623.15:raise ActionRejected('PRECONDITION_FAILED')
            self.fault='';self.latched=False;r={'reset':True}
        elif c=='product.collect':
            self._idle()
            if not a['recipient_id'] or not a['accept_hot_output']:raise ActionRejected('PRECONDITION_FAILED')
            for j in range(2):self.collected[j]+=self.product[j];self.product[j]=0.
            r={'recipient_id':a['recipient_id'],'attachment_event':'host_transfer_extrudate'}
        elif c=='material.clear':
            self._idle()
            if not a['accept_hot_residue']:raise ActionRejected('PRECONDITION_FAILED')
            cp=self.material['heat_capacity_j_kg_k'] if self.material else 0.;amb=self.plant['ambient_k'] if self.plant else 298.15
            self.exported_enthalpy+=sum(sum(z)*cp*(t-amb) for z,t in zip(self.zones,self.temps))
            for j in range(2):
                self.waste[j]+=self.hopper[j]+sum(z[j] for z in self.zones);self.hopper[j]=0.
                for z in self.zones:z[j]=0.
            self.age_mass=[0.]*7;r={'emptied':True}
        else:raise ActionRejected('UNKNOWN_CAPABILITY')
        self.revision+=1;return r

    def _advance(self,dt):
        p=self.plant
        if not p:return
        dc=self.cover_target-self.cover;self.cover+=math.copysign(min(abs(dc),p['cover_speed_m_s']*dt),dc) if dc else 0.
        masses=[sum(z) for z in self.zones];cp=self.material['heat_capacity_j_kg_k'] if self.material else 0.;rho=self.material['density_kg_m3'] if self.material else 1.
        amb=p['ambient_k'];C=p['zone_heat_capacity_j_k'];energies=[(C+m*cp)*(t-amb) for m,t in zip(masses,self.temps)]
        age=[a+m*dt for a,m in zip(self.age_mass,masses)]
        enabled=self.drive and self.powered and self.coolant and not self.latched and self.cover<1e-9 and self.cover_target<1e-9
        if enabled and self.material and any(masses) and any(m>0 and t<self.material['minimum_processing_k'] for m,t in zip(masses,self.temps)):
            self._trip('COLD_MATERIAL');enabled=False
        old_speed=self.speed
        self.speed=(old_speed+math.copysign(min(abs(self.speed_target-old_speed),p['acceleration_rad_s2']*dt),self.speed_target-old_speed)) if enabled else 0.
        omega=(old_speed+self.speed)/2 if enabled else 0.
        self.actual_feed=0.;self.output_rate=0.;self.torque=0.;self.pressure=0.;work=0.
        dm=[0.]*7;feed=0.
        if enabled and self.material and self.method:
            method=self.method;mat=self.material
            mu=[self._viscosity(t) for t in self.temps]
            fraction=min(1.,p['transport_per_rev']*omega/TAU/method['mixing_factor']*dt)
            dm[-1]=masses[-1]*fraction
            for i in range(5,-1,-1):dm[i]=min(masses[i]*fraction,max(0.,p['zone_capacity_kg']-masses[i+1]+dm[i+1]))
            if self.phase=='running':feed=min(self.feed_target*dt,sum(self.hopper),max(0.,p['zone_capacity_kg']-masses[0]+dm[0]))
            Q=dm[-1]/dt/rho;pressure=8*mu[-1]*method['die_length_m']*Q/(math.pi*method['die_radius_m']**4)
            drag=p['drag_coefficient_m3']*omega*sum(v*m/p['zone_capacity_kg'] for v,m in zip(mu,masses))/7*method['mixing_factor']
            torque=drag+(pressure*Q/(2*omega) if omega else 0.)
            self.torque=torque;self.pressure=pressure
            if pressure>1e7 or torque>6.:
                self._trip('PRESSURE_LIMIT' if pressure>1e7 else 'TORQUE_LIMIT');self.speed=0.;omega=0.;dm=[0.]*7;feed=0.
            else:work=2*torque*omega*dt
        self.angle=(self.angle+omega*dt)%TAU
        moved=[[z[j]*(d/m) if m else 0. for j in range(2)] for z,d,m in zip(self.zones,dm,masses)]
        age_out=[a*d/m if m else 0. for a,d,m in zip(age,dm,masses)]
        h_out=[d*cp*(t-amb) for d,t in zip(dm,self.temps)]
        hs=sum(self.hopper);feed_species=[v*feed/hs if hs else 0. for v in self.hopper]
        for j in range(2):
            self.hopper[j]-=feed_species[j];self.product[j]+=moved[-1][j]
            for i in range(7):self.zones[i][j]+=((feed_species[j] if i==0 else moved[i-1][j])-moved[i][j])
        self.actual_feed=feed/dt;self.output_rate=dm[-1]/dt
        self.output_age_mass+=age_out[-1];self.exported_enthalpy+=h_out[-1];self.mechanical_energy+=work
        self.age_mass=[age[i]-age_out[i]+(age_out[i-1] if i else 0.) for i in range(7)]
        for i in range(7):
            heat=min(p['heater_max_w'],max(0.,p['controller_w_k']*(self.targets[i]-self.temps[i]))) if self.heating and self.powered and not self.latched else 0.
            loss=p['loss_w_k']*(self.temps[i]-amb)
            energies[i]+=heat*dt-loss*dt+work/7+(h_out[i-1] if i else 0.)-h_out[i]
            self.heater_energy+=heat*dt;self.loss_energy+=loss*dt
            self.temps[i]=amb+energies[i]/(C+sum(self.zones[i])*cp)
        if max(self.temps)>623.15:self._trip('OVERTEMPERATURE')
        elif self.material and self.material.get('degradation_onset_k') and any(sum(z)>0 and t>self.material['degradation_onset_k'] for z,t in zip(self.zones,self.temps)):self._trip('DEGRADATION_ONSET')
        if self.phase=='running':
            self.elapsed_s+=dt
            if self.elapsed_s>=self.method['duration_s']-1e-10:
                self.elapsed_s=self.method['duration_s'];self._halt('succeeded')
    def step(self,dt_s):
        if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or not 0<dt_s<=3600:raise ActionRejected('INVALID_ARGUMENT')
        left=dt_s
        while left>1e-12:
            dt=min(left,.01)
            if self.phase=='running':dt=min(dt,self.method['duration_s']-self.elapsed_s)
            if dt<=1e-12:self._halt('succeeded');continue
            self._advance(dt);self.time_s+=dt;left-=dt
        self.revision+=1;return self.read('evaluator')
class ThermoProcess11Session:
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
        m=export_thermo_process11_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('extrusion.measure'))
    def cancel(self,operation_id,request_id):
        self.poll(operation_id);return self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)




def export_thermo_process11_contract_metadata():
    caps={};slots={}
    for k,(s,u,o,v,c,w) in SLOTS.items():
        slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,
            reference_conditions='相对外部环境的口模压差；厂家100bar基准不详，见conflicts' if 'pressure' in k else None,
            origin=o,visibility=v,access='read_write' if w else 'read_only',
            read_binding=dict(backend='host',artifact_id='runtime',accessor=f'ThermoProcess11Session.read(state_ids=[{k!r}])'),
            write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,
            sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    evolving=[k for k,v in SLOTS.items() if v[2] in ['scene','derived','process_truth']]
    for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
        asynchronous=k=='acquisition.start';query=kind=='query'
        caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='implemented',
            input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
            implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
            binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.thermo_process11.ThermoProcess11Session.invoke',
                invocation=f'session.invoke(instance_id="process11_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),
            preconditions=[] if query else [dict(description_zh='电源/冷却水/闭筒/预热/热料/静止拆装与有限单次装料，宿主身份',
                state_ids=['power.enabled','coolant.connected','barrel.position_m','drive.enabled','safety.latched','cycle.phase'],enforced_by='runtime',check='Runtime.invoke guards; Session trusted principal policy')],
            state_effects=[] if query else [dict(state_id=s,timing='immediate',description_zh='目标/事务/有限料宿主转移') for s in effects]+[dict(state_id=s,timing='on_step',description_zh='宿主唯一时钟推进机械、传质与热量') for s in evolving],
            observes=[],model_effects=dict(mode='none',description_zh='须独立验证求值模型及Matterix同步',binding_ids=[]),
            execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',
                completion_criterion='定时时间到；不等于筒内排净或合格产品' if asynchronous else '事务完成，物理量在宿主step演化',
                completion_state_ids=['cycle.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',
                retry='same_request_id',resources=['host_clock','finite_charge','barrel','request_cache'],
                partial_failure_zh='取消/故障保留已排出与筒内物质，热量不回滚；断电后仍散热；没有跨重启幂等'),
            errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','PRECONDITION_FAILED','BUSY','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','REFERENCE_OUT_OF_RANGE','AMBIGUOUS_REFERENCE','REFERENCE_NOT_RUNTIME_USABLE','COLD_MATERIAL']],
            example_arguments=copy.deepcopy(ex),verification=[],limitations=['外置喂料和拆装由宿主；真实固件、安全认证、材料标定未完成'],source_ids=['manufacturer_primary','simulation_assumption'])
    mech=dict(component_ids=['motor','screw_a','screw_b','barrel_lower','barrel_upper','feed_port','die','receiver'],
        state_ids=list(slots),meaning_zh='双螺杆七有限完全混合区、圆口模牛顿压降和轴功/电热/带料焓平衡',implementation='lumped',
        equations=['dm_i/dt=in_i-out_i; out_i<=k*m_i, k=transport_per_rev*omega/(2*pi*mixing_factor), upstream limited by recipient free capacity',
        'finite hopper feeds fixed70/30 mixture; product/collected/waste retain both species',
        'mu(T)=mu_ref*exp(Ea/R*(1/T-1/Tref)) (explicit parameters or library Arrhenius law) or mu_ref*exp(-C1*(T-Tr)/(C2+T-Tr)) (library WLF law); Newtonian, zero-shear/complex viscosity used as-is',
        'material.load_reference: law, minimum processing T and TGA degradation onset from the library record; density/cp from the record or explicit host assumption',
        'any filled zone above the record degradation onset temperature latches DEGRADATION_ONSET after the energy step',
        'pressure_gauge=8*mu_last*die_length*Q/(pi*die_radius^4); Q=out_mass_rate/rho',
        'torque_per_shaft=drag_coeff*omega*mean(mu_i*fill_i)*mixing_factor+pressure*Q/(2*omega)',
        'pressure>10MPa or per-shaft torque>6Nm causes latched stop before proposed transfer; thermal>623.15K trips after energy step',
        'E_i=(C_barrel+cp*m_i)*(T_i-Ta); dE_i/dt=heater_i-loss_i+2*tau*omega/7+enthalpy_in-enthalpy_out',
        'sum(E_i)+exported_enthalpy+integral(loss)=integral(heaters)+integral(2*tau*omega)',
        'age_mass_i increases by m_i*dt, transfers proportionally; mean output residence=exported_age_mass/exported_mass',
        'co-rotating phases q_a=q mod2pi; q_b=(q+pi/2)mod2pi, same -X axis; positive speed target10–1000rpm',
        'top-half barrel detached manually by host along +Z150mm proxy, not a motor or real hinge'],
        parameters={},source_ids=['manufacturer_primary','manufacturer_process','measured_reference','simulation_assumption'],
        limitations=['七区CSTR不是实际螺槽三维输送、返混/停留时间分布或螺杆元件CAD；mixing_factor不是厂家元件组合。','material.configure 仅支持70/30 PCM-Affinisol 显式假设参数；material.load_reference 支持13种文献黏度律材料（2种密度/热容全真实，其余需宿主显式假设）；模型仍不能代表实测药物工艺或用于剂型制备。','库共55条真实数据记录，运行时可用37条（<50目标）；工况多来自Pharma 11/ZE12/ZSE12同级机及专用口模，只按原工况精确返回，不用于标定当前圆口模。','无剪切变稀/黏弹性/熔融潜热/降解/挥发/返流/泄漏/溶解结晶；混合仅为两组分理想均匀账本。','七区瞬时等温且轴功均匀沉积，进料区水冷只为宿主边界；无冷却水温量或真实温控辨识。','控制界面菜单未复刻，完整安全联锁/机器人/硬件/接触力/部署隔离均not_run。'])
    spec=importlib.util.spec_from_file_location('_process11_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann)
    ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
    ann.annotate_export(SLUG,caps,slots,{'extrusion':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'material.configure':(MATERIAL,MATERIAL_EXAMPLE)})
    mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'extrusion':mech})

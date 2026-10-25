"""CHEM-057: finite seven-cell twin-screw surrogate with species and heat balances.

v1.1: real-data template library; material.load_reference drives the model with literature
viscosity-temperature laws, reference.lookup returns exact measured conditions, material.lookup
returns any record.  v1.2: library v3 (commodity/engineering thermoplastics and more pharma
carriers), Cross-WLF laws and measured shear-thinning parameters (Cross / Carreau-Yasuda) applied
at the die apparent wall shear rate.  Process outputs remain model predictions (synthetic)."""
from __future__ import annotations
import copy, importlib.util, json, math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SLUG='thermo_process11'; ASSET_TYPE_ID='instrument.'+SLUG; CATALOG_ID='CHEM-057'; PROTOCOL_VERSION='1.0.0'; ASSET_VERSION='1.4.1'
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
LAW=nullable(obj({'form':dict(type='string',enum=['arrhenius','wlf_exp','cross_wlf','exponential']),'viscosity_ref_pa_s':number('Pa*s',0),'reference_temperature_k':number('K',0),
'activation_j_mol':nullable(number('J/mol',0)),'c1':nullable(number('1')),'c2_k':nullable(number('K')),'evidence':TEXT,'valid_range_k':nullable(array(number('K',0),2)),
'shear_model':{'type':['string','null'],'enum':['cross','carreau_yasuda',None]},'n':nullable(number('1',0,1)),'tau_star_pa':nullable(number('Pa',0)),
'lambda_s':nullable(number('s',0)),'a_cy':nullable(number('1',0)),'alpha_per_k':nullable(number('1/K',0))}))
MATERIAL_INFO=obj({'reference_id':TEXT,'record_type':dict(type='string',enum=['material_reference','process_outcome']),'name_en':TEXT,'material_class':TEXT,'cas':NTEXT,
'runtime_usage':dict(type='string',enum=['process_model','exact_condition_lookup','reference_only']),'tg_k':nullable(number('K',0)),'tm_k':nullable(number('K',0)),
'degradation_onset_k':nullable(number('K',0)),'extrudable_window_k':nullable(array(number('K',0),2)),'minimum_processing_k':nullable(number('K',0)),
'viscosity_law':LAW,'density_kg_m3':nullable(number('kg/m^3',0)),'heat_capacity_j_kg_k':nullable(number('J/(kg*K)',0)),'neat_extrudable':{'type':['boolean','null']},
'density_evidence':NTEXT,'heat_capacity_evidence':NTEXT,'processing_window_k':nullable(array(number('K',0),2)),
'lookup_temperatures_k':dict(type='array',items=number('K',0)),'condition_count':dict(type='integer',minimum=0,**{'x-unit':'1'}),
'evidence':TEXT,'source':TEXT,'licence_flag':TEXT,'template_source':TEXT})
LOAD_REF=dict(type='object',properties={'reference_id':TEXT,'mass_kg':number('kg',1e-6,.05),'density_kg_m3':number('kg/m^3',100,5000),
'heat_capacity_j_kg_k':number('J/(kg*K)',100,10000),'accept_uncalibrated_newtonian':BOOL},required=['reference_id','mass_kg','accept_uncalibrated_newtonian'],additionalProperties=False)
LOAD_REF_EXAMPLE={'reference_id':'kollidon_va64','mass_kg':.02,'accept_uncalibrated_newtonian':True}
CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=()):CAPS[k]=(i,o,c,z,kind,vis,ex or {},list(effects))
for key,s,ex,vis,c in [('plant',PLANT,PLANT_EXAMPLE,'evaluator','body'),('method',METHOD,METHOD_EXAMPLE,'agent','display_digits'),('material',MATERIAL,MATERIAL_EXAMPLE,'evaluator','feed_port')]:
    cap(key+'.configure',s,obj({'configured':BOOL}),[c],'宿主配置显式参数或单次有限装料'+('；螺杆转动（drive.enabled）或运行中时返回BUSY：先停螺杆再改方法' if key=='method' else ''),'configuration',vis,ex,[key+'.configured'])   # 1.4.1: BUSY stated
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
cap('extrusion.measure',obj({}),RESULT,['display_digits'],'理想扭矩/七区温度读数与口模压力探头读数（含探头零漂与去皮，同HMI），非材料实测曲线','query')
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
slot('extrusion.die_pressure_gauge_pa',number('Pa',0),'Pa','口模压力探头读数（相对外部环境；探头零漂与去皮零点作用于此读数，与HMI压力相同）；100bar按教学表压解释','derived',c='pressure_sensor')
slot('extrusion.output_kg_s',number('kg/s',0),'kg/s','出口实际有限质量流量，非实机称量','derived',c='die')
slot('cycle.phase',PHASE,'1','定时程序状态，成功不等于所有料已排净',c='display_digits')
slot('cycle.elapsed_s',number('s',0),'s','当前定时进料操作宿主累计时间',c='display_digits')
for k,z in [('pressed','宿主急停按下'),('latched','教学故障锁存')]:slot('safety.'+k,BOOL,'1',z,c='button_emergency')
slot('safety.fault',TEXT,'1','压力/扭矩/过热、冷料或文献降解起始温度故障代码',c='display_digits')
slot('truth.ledger',PRODUCT,'1','有限双组分库存及整体能量和年龄账本','process_truth','evaluator','receiver')
BASE_CAPS=list(CAPS);BASE_SLOTS=list(SLOTS)

# ---- touch-screen HMI model (operating manual ch.6): die heating zone, feeder control and set-up, pressure probe, alarms, recipes, trends,
# torque unit, clocks.  Every control of panel_ui.json calls one of these or an existing command.
HMI_ZONES=['Zone 2','Zone 3','Zone 4','Zone 5','Zone 6','Zone 7','Zone 8','Die']   # runtime zones 0..6 = HMI zones 2..8, then the die heater
ALARM_DEFAULT_K=15.;TEMP_ENTRY_MAX_K=553.15;PRESSURE_MAX_PA=100e5;TARE_MAX_PA=5e5;DEVIATION_WINDOW_S=60.;TREND_DT_S=1.;TREND_MAX=3600
EPOCH='2026-10-19T08:00:00'
FEED_MODES=['gravimetric','volumetric','gravimetric_discharge','discharge']
# feeder (manual 6.13): the feed screw moves a fixed volume per turn, so its mass rate is screw speed x screw volume x the powder's true bulk density.
# The nominal screw volume is chosen so that the factory bulk density 500 kg/m3 gives the model's maximum feed rate at 100 % screw speed (assumption).
# Gravimetric mode: feed-forward screw speed = setpoint / stored maximum output, plus an integral correction on the weighed (loss-in-weight) rate whose
# time constant is FEED_LOOP_S / control gain (gain 1 by default, 0 = no correction, 2 = most aggressive; manual p.6-31).
BULK_DEFAULT=500.;FEED_LOOP_S=10.;CALC_S=60.;OUTPUT_MAX=FEED_MAX*3000/BULK_DEFAULT
FREE={'type':'object'};INT1=lambda lo,hi:{'type':'integer','minimum':lo,'maximum':hi,'x-unit':'1'}
def opt(p):return dict(type='object',properties=p,required=[],additionalProperties=False)
FEEDER_CFG=dict(mode=dict(type='string',enum=FEED_MODES),setpoint_pct=number('%',0,100),min_fill_kg=number('kg',0,.05),max_fill_kg=number('kg',0,.05),
    max_deviation_pct=number('%',0,100),bulk_density_kg_m3=number('kg/m^3',50,3000),gain=number('1',0,2),max_output_kg_s=number('kg/s',FEED_MIN,OUTPUT_MAX))
APP_ERRORS=['EXTRUDER_NOT_RUNNING','PRESSURE_TOO_HIGH','EMPTY_RECIPE','BAD_CLOCK','NOT_RUNNING']
cap('die.set_temperature',obj({'temperature_k':TEMP}),obj({'temperature_k':TEMP}),['die'],'口模加热区设定（HMI温度区Die；与七个筒体区同一控温开关）',ex={'temperature_k':438.15},effects=['die.target_k'])
cap('feeder.set_running',obj({'running':BOOL,'test':BOOL}),obj({'running':BOOL}),['feed_port'],'喂料器启停（喂料器图标）；挤出机未运转时拒绝，除设置页的试运行（test：出料进收集容器）',ex={'running':True,'test':False},effects=['feeder.state'])
cap('feeder.configure',opt(FEEDER_CFG),obj({'configured':BOOL}),['feed_port'],'喂料器设置：模式（重量/容积/重量排空到下限/全部排空）、容积设定%（设定螺杆转速）、料斗下/上限、最大偏差、堆密度（未同时给最大产量时，控制器按名义螺杆容积×堆密度估算最大产量）、控制增益（重量模式螺杆转速校正环的放大倍数0–2）、最大产量（100 %螺杆转速的质量流量，重量模式前馈用）',ex={'mode':'volumetric'},effects=['feeder.state'])
cap('feeder.action',obj({'action':dict(type='string',enum=['tare','reset_throughput','refill','calc_max_output'])}),obj({'action':TEXT,'value':number('1')}),['feed_port'],'喂料器操作：去皮、产量清零、加料暂停/继续、计算最大产量（以设定螺杆转速向容器喂料1 min，称得的质量流量折算到100 %转速后写入最大产量；value为剩余秒数）',ex={'action':'tare'},effects=['feeder.state'])
cap('pressure.tare',obj({}),obj({'offset_pa':number('Pa')}),['pressure_sensor'],'压力探头在工作温度下去皮（未去皮探头读数过高时拒绝）；去皮零点作用于探头的全部读数：HMI压力、Die pressure high报警、extrusion.die_pressure_gauge_pa与extrusion.measure（100 bar跳闸仍按真实压力）',effects=['pressure.offset_pa','pressure.display_pa','extrusion.die_pressure_gauge_pa'])
cap('pressure.set_limit',obj({'limit_pa':number('Pa',0,PRESSURE_MAX_PA)}),obj({'limit_pa':number('Pa',0)}),['pressure_sensor'],'口模工作压力上限（超过时报警）',ex={'limit_pa':50e5},effects=['pressure.limit_pa'])
cap('alarms.acknowledge',obj({}),obj({'acknowledged':INT1(0,1024)}),['display_digits'],'Mute/Reset：确认报警并关闭报警消息框（锁存故障仍需fault.reset）',effects=['alarms.active'])
cap('recipe.save',obj({'index':INT1(1,10),'name':dict(type='string',maxLength=24),'speed_rad_s':OMEGA,'temperatures_k':array(TEMP,8),'alarm_deviation_k':array(number('K',0,100),8),'feed_kg_s':RATE}),obj({'index':INT1(1,10)}),['display_digits'],'配方编辑器保存：名称、转速、七个筒体区与口模温度、各区报警偏差、喂料速率',ex={'index':1,'name':'PVA 165C','speed_rad_s':200*TAU/60,'temperatures_k':[438.15]*8,'alarm_deviation_k':[15.]*8,'feed_kg_s':.5/3600},effects=['recipes'])
cap('recipe.write',obj({'index':INT1(1,10)}),obj({'index':INT1(1,10)}),['display_digits'],'把配方写入PLC：为安全先停螺杆和喂料，再写入转速、各区与口模温度、报警偏差和喂料速率',ex={'index':1},effects=['recipe.loaded','drive.enabled','drive.target_rad_s','thermal.targets_k','die.target_k','alarm.deviation_k','feed.target_kg_s','feeder.state','cycle.phase'])
cap('display.configure',obj({'torque_unit':dict(type='string',enum=['Nm','%','kW'])}),obj({'torque_unit':TEXT}),['display_digits'],'扭矩显示单位（触摸扭矩区在Nm/%/kW间切换）',ex={'torque_unit':'Nm'},effects=['display.torque_unit'])
cap('trend.control',obj({'action':dict(type='string',enum=['clear','pause','resume'])}),obj({'action':TEXT}),['display_digits'],'趋势记录：LOG CLR清除、暂停/继续记录',ex={'action':'pause'},effects=['trend.paused'])
cap('clock.set',opt({'hmi_time':TEXT,'hmi_date':TEXT}),obj({'hmi':TEXT}),['display_digits'],'设定HMI时间（hh.mm.ss）或日期（dd.mm.yy）；PLC时钟不自动同步',ex={'hmi_time':'09.30.00'},effects=['clock'])
cap('clock.sync',obj({}),obj({'plc':TEXT}),['display_digits'],'Sync HMI/PLC Clocks：PLC时钟取HMI时间',effects=['clock'])
cap('sensor.configure_error',opt({'pressure_zero_drift_pa_per_k':number('Pa/K',-10000,10000)}),obj({'pressure_zero_drift_pa_per_k':number('Pa/K')}),['pressure_sensor'],'评测者设置压力探头随口模升温的零点漂移（Pa/K×（口模温度−环境温度）；默认0=理想探头）；在工作温度下去皮可补偿（手册6.14）','configuration','evaluator',{'pressure_zero_drift_pa_per_k':500.},['pressure.display_pa','extrusion.die_pressure_gauge_pa','truth.die_pressure_pa'])
cap('feeder.configure_material',obj({'bulk_density_kg_m3':number('kg/m^3',50,3000)}),obj({'bulk_density_kg_m3':number('kg/m^3')}),['feed_port'],'评测者设置料斗中粉料的真实堆密度（默认500 kg/m³，等于出厂堆密度设定）：螺杆质量流量=转速×名义螺杆容积×真实堆密度','configuration','evaluator',{'bulk_density_kg_m3':650.},['feeder.state','feed.actual_kg_s','truth.bulk_density_kg_m3'])
APP_CAPS=[k for k in CAPS if k not in BASE_CAPS]
# Alarm Help (operating manual 6.19, p.6-44..6-48): every documented alarm code.  Codes with a cause in the model come from it (PE001 over torque =
# TORQUE_LIMIT, PE011 temperature reached, PE100 die pressure, EE001 emergency stop); the others are evaluator-only fault.inject {code} and
# fault.clear {code} (the remedy: an electrician resets the breaker, the battery is replaced, ...).  Kinds: 'drive' stops the extruder (a timed run
# fails) and refuses a drive start until cleared; 'heat' / 'die_heat' cut the barrel / die heater power (breaker) until cleared; 'feeder' stops
# feeder 1 and refuses its start until cleared; 'alarm' raises the alarm only.  ack=True: Mute/Reset also clears it (the manual says "press Mute/
# Reset to continue" and names no repair).  The alarm text on the status bar is the code and the manual's headline.
ALARM_CODES={
    'ME001':('drive',False,'Extruder drive alarm'),'ME002':('drive',False,'Extruder drive circuit breaker tripped'),
    'ME003':('heat',False,'Heating circuit breaker tripped'),'ME004':('drive',False,'Barrel safety switch tripped'),
    'ME005':('die_heat',False,'Die heating circuit breaker tripped'),'PE002':('drive',True,'Extruder drive user settable torque limit alarm'),
    'FE001':('feeder',False,'Feeder 1 drive alarm'),'FE002':('feeder',True,'Gravimetric feeder 1 alarm'),
    **{f'PE00{z+1}':('alarm',True,f'Zone {z} temperature alarm') for z in range(2,9)},'PE010':('alarm',True,'Die zone temperature alarm'),
    'PE012':('alarm',False,'Thermocouple fault'),'PE101':('alarm',False,'Torque transducer alarm'),'HE001':('alarm',False,'PLC low battery alarm'),
    'HE002':('alarm',False,'HMI low battery alarm'),'HE003':('alarm',False,'Extruder calibration/service due')}
cap('fault.inject',obj({'code':dict(type='string',enum=sorted(ALARM_CODES))}),obj({'latched':dict(type='array',items=TEXT,maxItems=32)}),['display_digits'],
    '评测者注入手册6.19报警（ME001-ME005、PE002、FE001-FE002、PE003-PE010、PE012、PE101、HE001-HE003）：状态栏显示代码与标题；驱动类停螺杆（定时运行失败）并在清除前拒绝启动，'
    '加热断路器类切断筒体/口模加热，喂料器类停喂料器1并拒绝启动，其余只报警','simulation','evaluator',{'code':'ME002'},['alarms.active','drive.enabled','cycle.phase','feeder.state'])
cap('fault.clear',obj({'code':dict(type='string',enum=sorted(ALARM_CODES))}),obj({'latched':dict(type='array',items=TEXT,maxItems=32)}),['display_digits'],
    '评测者清除报警原因（电工复位断路器、换电池等）；状态栏消息仍需Mute/Reset确认','simulation','evaluator',{'code':'ME002'},['alarms.active'])
APP_CAPS=[k for k in CAPS if k not in BASE_CAPS]
RUN_GUARDED={'acquisition.stop','method.configure','drive.set_enabled','drive.set_speed','feed.set_rate','thermal.set_zone','thermal.set_enabled','die.set_temperature',
             'feeder.set_running','feeder.configure','feeder.action','pressure.tare','pressure.set_limit','recipe.write'}   # see ThermoProcess11Runtime._guard
for _k in ['power.set','power.toggle','coolant.set_connected','drive.set_enabled','acquisition.stop','emergency.stop']:   # the feeder stops with the drive; latched faults are alarms
    CAPS[_k][7].extend(['feeder.state','alarms.active','alarms.log','alarms.summary'])
slot('die.target_k',TEMP,'K','口模加热区设定','setpoint',c='die')
slot('die.temperature_k',number('K',1),'K','口模加热区理想化温度（与筒体区同一加热参数，不进入熔体模型）',c='die')
slot('heater.power_fraction',array(number('1',0,1),8),'1','七个筒体区与口模的加热功率占比（温度曲线页功率指示）',c='barrel_lower')
slot('feeder.state',FREE,'1','喂料器：运行、模式、试运行、加料暂停、料斗读数、实际/设定、偏差、产量、限值、堆密度、增益、最大产量','setpoint',c='feed_port')
slot('pressure.display_pa',number('Pa'),'Pa','HMI压力读数：探头读数（真实压力+零漂）减去皮零点，与extrusion.die_pressure_gauge_pa相同',c='pressure_sensor')
slot('pressure.offset_pa',number('Pa'),'Pa','压力探头去皮零点','setpoint',c='pressure_sensor')
slot('pressure.limit_pa',number('Pa',0),'Pa','口模工作压力报警上限','setpoint',c='pressure_sensor')
slot('alarm.deviation_k',array(number('K',0),8),'K','各区允许温度偏差（配方写入；默认15 K）','setpoint',c='display_digits')
slot('alarms.active',{'type':['string','null']},'1','当前未确认的报警消息',c='display_digits')
slot('alarms.log',dict(type='array',items=FREE),'1','报警记录（最近1024条）',c='display_digits')
slot('alarms.summary',FREE,'1','各报警出现次数',c='display_digits')
slot('recipes',dict(type='array',items=FREE),'1','10个配方（空配方名为空）','setpoint',c='display_digits')
slot('recipe.loaded',{'type':['integer','null']},'1','已写入PLC的配方号','setpoint',c='display_digits')
slot('display.torque_unit',dict(type='string',enum=['Nm','%','kW']),'1','扭矩显示单位','setpoint',c='display_digits')
slot('trend.paused',BOOL,'1','趋势记录暂停','setpoint',c='display_digits')
slot('clock',FREE,'1','HMI与PLC时钟（文字）',c='display_digits')
slot('truth.die_pressure_pa',number('Pa',0),'Pa','口模真实压差（评测者；探头读数=真实+零漂−去皮零点）','process_truth','evaluator','pressure_sensor')
slot('truth.bulk_density_kg_m3',number('kg/m^3',0),'kg/m^3','料斗粉料真实堆密度（评测者；默认500）','process_truth','evaluator','feed_port')

def _panel_engine():
    try:
        from . import _panel_ui as engine                    # imported as asset_runtime.thermo_process11 (tests, bridges)
        return engine
    except ImportError:                                      # loaded from its file path
        import sys
        if '_chemsim_panel_ui' not in sys.modules:
            spec=importlib.util.spec_from_file_location('_chemsim_panel_ui',Path(__file__).with_name('_panel_ui.py'));sys.modules['_chemsim_panel_ui']=importlib.util.module_from_spec(spec);spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
        return sys.modules['_chemsim_panel_ui']
import os
_panel_ui=_panel_engine();ASSET_DIR=ROOT/'assets/expansion_batch04/t5_automation'/SLUG
PANEL_SPEC=None if os.environ.get('PROCESS11_PANEL_BUILD') else _panel_ui.load_spec(ASSET_DIR/'panel_ui.json',capabilities=set(CAPS))
PANEL_CAPS=_panel_ui.capability_definitions(PANEL_SPEC) if PANEL_SPEC else {}
_PANEL_EFFECTS=list(dict.fromkeys(e for c in (_panel_ui.spec_capabilities(PANEL_SPEC) if PANEL_SPEC else []) for e in CAPS[c][7]))
for _k,_d in PANEL_CAPS.items():cap(_k,_d['input_schema'],_d['output_schema'],_d['component_ids'],_d['function_zh'],_d['kind'],_d['visibility'],_d['example_arguments'],[] if _k in ('panel.capture','panel.frame') else _PANEL_EFFECTS)
class _Panel(_panel_ui.PanelUI):
    """The panel's UI state belongs to the instrument: two panels are equal when their UI states are (state snapshots compare runtimes)."""
    def __eq__(self,o):return isinstance(o,_panel_ui.PanelUI) and self.ui==o.ui
    __hash__=None
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
        self.operation_id='';self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._caller=None
        self._app_init()
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
            die_pressure_gauge_pa=self._pressure_reading(),flow_kg_s=self.output_rate,zone_temperature_k=self.temps.copy(),fault=self.fault,
            evidence=self._evidence('synthetic_not_measured_trace'),template_source=TEMPLATE_REL)
    def read(self,visibility='agent'):
        if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
        d={'plant.configured':self.plant is not None,'method.configured':self.method is not None,'material.configured':self.material is not None,
        'power.enabled':self.powered,'coolant.connected':self.coolant,'barrel.target_m':self.cover_target,'barrel.position_m':self.cover,
        'drive.enabled':self.drive,'drive.target_rad_s':self.speed_target,'drive.speed_rad_s':self.speed,
        'screw.a_angle_rad':self.angle,'screw.b_angle_rad':(self.angle+math.pi/2)%TAU,
        'feed.target_kg_s':self.feed_target,'feed.actual_kg_s':self.actual_feed,'thermal.targets_k':self.targets.copy(),'thermal.enabled':self.heating,
        'extrusion.torque_per_shaft_nm':self.torque,'extrusion.die_pressure_gauge_pa':self._pressure_reading(),'extrusion.output_kg_s':self.output_rate,
        'cycle.phase':self.phase,'cycle.elapsed_s':self.elapsed_s,'safety.pressed':self.pressed,'safety.latched':self.latched,'safety.fault':self.fault}
        d.update({'thermal.zone_'+str(i)+'_k':v for i,v in enumerate(self.temps)})
        d.update(self._app_read())
        if visibility!='agent':d.update({'truth.ledger':self._ledger(),'truth.die_pressure_pa':self.pressure,'truth.bulk_density_kg_m3':self.bulk_true})
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
    def _viscosity(self,t,shear_rate=None):
        m=self.material
        if m.get('law') in ('wlf_exp','cross_wlf'):
            d=t-m['reference_temperature_k'];x=-m['c1']*d/max(1e-6,m['c2_k']+d)
        elif m.get('law')=='exponential':x=-m['alpha_per_k']*(t-m['reference_temperature_k'])
        else:x=m['activation_j_mol']/8.314462618*(1/t-1/m['reference_temperature_k'])
        # numeric guard only; Cross-WLF D1 can be ~1e23 Pa*s so the exponent reaches about -50 inside the processing window
        eta0=m['viscosity_ref_pa_s']*math.exp(max(-300,min(300,x)));sh=m.get('shear')
        if not sh or not shear_rate or shear_rate<=0:return eta0
        if sh['model']=='cross':return eta0/(1+(eta0*shear_rate/sh['tau_star_pa'])**(1-sh['n']))
        # Carreau-Yasuda with time-temperature superposition of the relaxation time: lambda(T)=lambda_ref*eta0(T)/eta0(Tref)
        lam=sh['lambda_s']*eta0/sh['eta0_ref_pa_s'];return eta0*(1+(lam*shear_rate)**sh['a_cy'])**((sh['n']-1)/sh['a_cy'])
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
                activation_j_mol=law.get('activation_j_mol'),c1=law.get('c1'),c2_k=law.get('c2_k'),evidence=law['evidence'],valid_range_k=law.get('valid_range_k'),
                shear_model=(law.get('shear') or {}).get('model'),n=(law.get('shear') or {}).get('n'),tau_star_pa=(law.get('shear') or {}).get('tau_star_pa'),
                lambda_s=(law.get('shear') or {}).get('lambda_s'),a_cy=(law.get('shear') or {}).get('a'),alpha_per_k=law.get('alpha_per_k')),
            density_kg_m3=(r.get('density') or {}).get('value_kg_m3'),heat_capacity_j_kg_k=(r.get('heat_capacity') or {}).get('value_j_kg_k'),neat_extrudable=r.get('neat_extrudable'),
            density_evidence=(r.get('density') or {}).get('evidence'),heat_capacity_evidence=(r.get('heat_capacity') or {}).get('evidence'),processing_window_k=r.get('processing_window_k'),
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
        sh=law.get('shear')
        ev=('density:'+('literature' if dens is not None else 'host_assumption')+';heat_capacity:'+('literature' if cp is not None else 'host_assumption')+';viscosity_law:'+law['form']+'('+law['evidence']+')'
            +(';shear:'+sh['model']+'('+sh.get('evidence','measured_literature')+')' if sh else ';shear:none(Newtonian)'))
        self.material=dict(reference_id=rid,base_kg=a['mass_kg'],additive_kg=0.,density_kg_m3=dens if dens is not None else a['density_kg_m3'],
            heat_capacity_j_kg_k=cp if cp is not None else a['heat_capacity_j_kg_k'],viscosity_ref_pa_s=law['viscosity_ref_pa_s'],reference_temperature_k=law['reference_temperature_k'],
            activation_j_mol=law.get('activation_j_mol') or 0.,c1=law.get('c1'),c2_k=law.get('c2_k'),alpha_per_k=law.get('alpha_per_k'),law=law['form'],minimum_processing_k=tmin,
            shear=None if not sh else dict(model=sh['model'],n=sh['n'],tau_star_pa=sh.get('tau_star_pa'),lambda_s=sh.get('lambda_s'),a_cy=sh.get('a'),eta0_ref_pa_s=sh.get('eta0_ref_pa_s')),
            degradation_onset_k=r.get('degradation_onset_k'),accept_uncalibrated_newtonian=True,parameter_evidence=ev)
        self.initial=[a['mass_kg'],0.];self.hopper=self.initial.copy()
        return {'configured':True,'parameter_evidence':ev}
    def invoke(self,capability,**a):
        if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability][0],a);c=capability
        if c in PANEL_CAPS:return self._panel_cmd(c,a)
        if c in APP_CAPS:
            r=self._app_invoke(c,a);self._app_sync();self.revision+=1;return r
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
            if value and not self.powered:self.power_epoch+=1
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
                if any(ALARM_CODES[x][0]=='drive' for x in getattr(self,'alarm_latched',[])):raise ActionRejected('PRECONDITION_FAILED')
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
            if self.feeder['calculating']:raise ActionRejected('BUSY')
            self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);self.phase='running';self.elapsed_s=0.;self.feeder['corr_kg_s']=0.
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
        self._app_sync();self.revision+=1;return r

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
            if self.phase=='running' or self._feeding():
                feed=min(self._screw_rate()*dt,sum(self.hopper),max(0.,p['zone_capacity_kg']-masses[0]+dm[0]))
                if self._gravimetric():self._feeder_loop(feed/dt,dt)
            Q=dm[-1]/dt/rho
            # die: apparent wall shear rate 4Q/(pi R^3); shear thinning only when the library record carries measured Cross/Carreau-Yasuda parameters
            mu_die=self._viscosity(self.temps[-1],4*Q/(math.pi*method['die_radius_m']**3)) if mat.get('shear') else mu[-1]
            pressure=8*mu_die*method['die_length_m']*Q/(math.pi*method['die_radius_m']**4)
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
            heat=min(p['heater_max_w'],max(0.,p['controller_w_k']*(self.targets[i]-self.temps[i]))) if self.heating and self.powered and not self.latched and 'ME003' not in self.alarm_latched else 0.
            self.heat_frac[i]=heat/p['heater_max_w']
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
        left=dt_s;h0=sum(self.hopper)
        while left>1e-12:
            dt=min(left,.01)
            if self.phase=='running':dt=min(dt,self.method['duration_s']-self.elapsed_s)
            if dt<=1e-12:self._halt('succeeded');continue
            self._advance(dt);self.time_s+=dt;left-=dt;self._trend_sample()
        self._app_step(dt_s,h0-sum(self.hopper));self.revision+=1;return self.read('evaluator')
    # ---- touch-screen HMI model (operating manual ch.6); panel_ui.json calls these commands
    def _app_init(self):
        self.die_target=298.15;self.die_k=298.15;self.heat_frac=[0.]*8;self.power_epoch=0;self._last_fault=''
        self.feeder=dict(running=False,test=False,mode='gravimetric',refill=False,setpoint_pct=50.,tare_kg=0.,throughput_kg=0.,min_fill_kg=.001,max_fill_kg=.05,
            max_deviation_pct=10.,bulk_density_kg_m3=BULK_DEFAULT,gain=1.,max_output_kg_s=FEED_MAX,deviation_s=0.,deviation_pct=0.,low_warned=False,high_warned=False,
            corr_kg_s=0.,calculating=False,calc_s=0.,calc_kg=0.)
        self.bulk_true=BULK_DEFAULT;self.press_drift=0.
        self.press_offset=0.;self.press_limit=PRESSURE_MAX_PA;self.deviation_k=[ALARM_DEFAULT_K]*8;self.reached=[False]*8;self.temp_reached=False
        self.alarm_log=[];self.alarm_summary={};self.alarm_active=None;self.alarm_latched=[]
        self.recipes=[dict(index=i,name='',speed_rad_s=None,temperatures_k=None,alarm_deviation_k=None,feed_kg_s=None) for i in range(1,11)];self.recipe_loaded=None
        self.torque_unit='%';self.trend=[];self.trend_paused=False;self._trend_t=None;self.hmi_offset=0.;self.plc_offset=0.
        self.panel=_Panel(PANEL_SPEC,self) if PANEL_SPEC else None
    def _hmi_temps(self):return self.temps+[self.die_k]
    def _wall(self,offset):
        import datetime
        return datetime.datetime.fromisoformat(EPOCH)+datetime.timedelta(seconds=self.time_s+offset)
    def _feeder_view(self):
        f=self.feeder;w=sum(self.hopper)-f['tare_kg'];run=(f['running'] and not f['refill']) or f['calculating']
        rate=(self.actual_feed if not f['test'] and f['mode'] in ('gravimetric','volumetric') and not f['calculating'] else self._screw_rate()) if run else 0.
        return dict({k:v for k,v in f.items() if k not in ('deviation_s','low_warned','high_warned','corr_kg_s','calc_kg')},hopper_kg=w,actual_kg_s=rate,setpoint_kg_s=self.feed_target,
            screw_speed_pct=100*self._screw_fraction() if run else 0.,plugged=True)
    def _pressure_raw(self):
        """Untared pressure-probe signal: the true die pressure plus the probe's zero drift with the die heating (evaluator, default 0)."""
        amb=self.plant['ambient_k'] if self.plant else 298.15
        return self.pressure+self.press_drift*(self.die_k-amb)
    def _pressure_reading(self):
        """The device's die-pressure reading (HMI, alarm, extrusion.die_pressure_gauge_pa, extrusion.measure): probe signal minus the tare."""
        return max(0.,self._pressure_raw()-self.press_offset)
    def _app_read(self):
        return {'die.target_k':self.die_target,'die.temperature_k':self.die_k,'heater.power_fraction':list(self.heat_frac),'feeder.state':self._feeder_view(),
            'pressure.display_pa':self._pressure_reading(),'pressure.offset_pa':self.press_offset,'pressure.limit_pa':self.press_limit,'alarm.deviation_k':list(self.deviation_k),
            'alarms.active':self.alarm_active,'alarms.log':copy.deepcopy(self.alarm_log[-1024:]),'alarms.summary':dict(self.alarm_summary),'recipes':copy.deepcopy(self.recipes),
            'recipe.loaded':self.recipe_loaded,'display.torque_unit':self.torque_unit,'trend.paused':self.trend_paused,
            'clock':dict(hmi=self._wall(self.hmi_offset).strftime('%H.%M.%S %d.%m.%y'),plc=self._wall(self.plc_offset).strftime('%H.%M.%S %d.%m.%y'))}
    def _alarm(self,text):
        self.alarm_log=(self.alarm_log+[dict(time_s=self.time_s,text=text,when=self._wall(self.hmi_offset).strftime('%d.%m.%y %H:%M:%S'))])[-1024:]
        self.alarm_summary[text]=self.alarm_summary.get(text,0)+1;self.alarm_active=text
    FAULT_TEXT={'TORQUE_LIMIT':'Extruder torque above 100 %','PRESSURE_LIMIT':'Die pressure above 100 bar','OVERTEMPERATURE':'Barrel over temperature',
        'COLD_MATERIAL':'Material below processing temperature','DEGRADATION_ONSET':'Material degradation temperature reached','EMERGENCY_STOP':'Emergency stop pressed'}
    def _feeding(self):
        f=self.feeder;return f['running'] and not f['refill'] and not f['test'] and f['mode'] in ('gravimetric','volumetric')
    def _gravimetric(self):
        """The screw speed is set by the gravimetric controller: a timed run (acquisition.start) or gravimetric feeding into the extruder."""
        f=self.feeder;return self.phase=='running' or (self._feeding() and f['mode']=='gravimetric')
    def _screw_full(self):
        return FEED_MAX*(self.bulk_true/BULK_DEFAULT)                   # mass rate at 100 % screw speed: nominal screw volume x true bulk density
    def _grav_command(self):
        f=self.feeder;return min(f['max_output_kg_s'],max(0.,self.feed_target+f['corr_kg_s']))   # commanded rate, screw speed = command / maximum output
    def _screw_fraction(self):
        f=self.feeder
        if f['calculating']:return f['setpoint_pct']/100
        if self._gravimetric():return self._grav_command()/f['max_output_kg_s']
        return f['setpoint_pct']/100 if f['mode']=='volumetric' else 1.  # volumetric: the set screw speed; test run in gravimetric mode and discharge: full speed
    def _screw_rate(self):
        """Mass rate the feed screw delivers now (before the hopper / barrel limits)."""
        f=self.feeder
        if self._gravimetric() and not f['calculating']:return self._grav_command()*(self._screw_full()/f['max_output_kg_s'])
        return self._screw_full()*self._screw_fraction()
    def _feeder_loop(self,measured,dt):
        """Gravimetric control: integral screw-speed correction on the weighed feed rate, scaled by the control gain (manual p.6-31)."""
        f=self.feeder
        f['corr_kg_s']=min(f['max_output_kg_s']-self.feed_target,max(-self.feed_target,f['corr_kg_s']+f['gain']*dt/FEED_LOOP_S*(self.feed_target-measured)))
    def _app_sync(self):
        # the feeders stop with the extruder drive (test runs from the set-up page excepted); new latched faults become alarms
        f=self.feeder
        if f['running'] and not f['test'] and f['mode'] in ('gravimetric','volumetric') and not (self.drive and self.powered):f['running']=False
        if not self.powered:f.update(running=False,calculating=False,calc_s=0.,calc_kg=0.)
        if self.fault!=self._last_fault:
            if self.fault:self._alarm(self.FAULT_TEXT.get(self.fault,self.fault))
            self._last_fault=self.fault
    def _app_step(self,dt,fed):
        p=self.plant;f=self.feeder
        if p:                                                  # die heater: the zone parameters, no material in the die model
            n=max(1,int(math.ceil(dt/.5)));h=dt/n;C=p['zone_heat_capacity_j_k'];amb=p['ambient_k']
            for _ in range(n):
                heat=min(p['heater_max_w'],max(0.,p['controller_w_k']*(self.die_target-self.die_k))) if self.heating and self.powered and not self.latched and 'ME005' not in self.alarm_latched else 0.
                self.heat_frac[7]=heat/p['heater_max_w'];self.die_k+=(heat-p['loss_w_k']*(self.die_k-amb))*h/C;self.heater_energy+=heat*h;self.loss_energy+=p['loss_w_k']*(self.die_k-amb)*h
        if f['calculating']:                                   # Calc.Max.Output: one minute at the set screw speed into a container, weighed
            run=min(dt,f['calc_s']);take=min(self._screw_rate()*run,sum(self.hopper));hs=sum(self.hopper)
            for j in range(2):
                q=self.hopper[j]*take/hs if hs else 0.;self.hopper[j]-=q;self.waste[j]+=q
            fed+=take;f['calc_kg']+=take;f['calc_s']-=run
            if f['calc_s']<=1e-9:                              # result scaled to 100 % screw speed
                f.update(calculating=False,calc_s=0.,max_output_kg_s=min(OUTPUT_MAX,max(FEED_MIN,f['calc_kg']/CALC_S/(f['setpoint_pct']/100))),calc_kg=0.)
        elif f['running'] and not f['refill'] and (f['test'] or f['mode'] in ('gravimetric_discharge','discharge')):   # test run / discharge: into a container
            take=min(self._screw_rate()*dt,sum(self.hopper));hs=sum(self.hopper)
            for j in range(2):
                q=self.hopper[j]*take/hs if hs else 0.;self.hopper[j]-=q;self.waste[j]+=q
            fed+=take
            w=sum(self.hopper)-f['tare_kg']
            if (f['mode']=='gravimetric_discharge' and w<=f['min_fill_kg']+1e-12) or sum(self.hopper)<=1e-12:
                f.update(running=False,mode='gravimetric')
        f['throughput_kg']+=max(0.,fed)
        if self._feeding() and self.drive and f['mode']=='gravimetric' and self.feed_target>0:   # deviation control: stop after 60 s outside the limit
            dev=100*(self.actual_feed-self.feed_target)/self.feed_target;f['deviation_pct']=dev
            f['deviation_s']=f['deviation_s']+dt if abs(dev)>f['max_deviation_pct'] else 0.
            if f['deviation_s']>=DEVIATION_WINDOW_S:f.update(running=False,deviation_s=0.);self._alarm('Feeder 1 deviation alarm')
        w=sum(self.hopper)-f['tare_kg']
        if w<=f['min_fill_kg'] and not f['low_warned'] and self.material:f['low_warned']=True;self._alarm('Feeder 1 hopper at minimum fill level')
        elif w>f['min_fill_kg']:f['low_warned']=False
        if w>=f['max_fill_kg'] and not f['high_warned']:f['high_warned']=True;self._alarm('Feeder 1 hopper at maximum fill level')
        elif w<f['max_fill_kg']:f['high_warned']=False
        if self.heating and self.powered and not self.latched:  # temperature alarms: each zone once it has reached its setpoint
            tg=self.targets+[self.die_target];tt=self._hmi_temps()
            for i in range(8):
                d=abs(tt[i]-tg[i])
                if d<=self.deviation_k[i]:self.reached[i]=True
                elif self.reached[i]:self.reached[i]=False;self._alarm(HMI_ZONES[i]+' temperature deviation')
            if all(self.reached) and not self.temp_reached:self.temp_reached=True;self._alarm('Temperature reached')
        else:self.reached=[False]*8;self.temp_reached=False
        if self._pressure_reading()>self.press_limit+1e-6 and self.alarm_active!='Die pressure high':self._alarm('Die pressure high')
        self._app_sync()
    def _trend_sample(self):
        if self.drive and self.powered and not self.trend_paused:      # trend log: recorded while the extruder screws run, one sample per second
            t=self.time_s
            if self._trend_t is None or t-self._trend_t>=TREND_DT_S-1e-9:
                self._trend_t=t;tq=200*self.torque/12.
                self.trend=(self.trend+[[t]+[round(x-273.15,3) for x in self._hmi_temps()]+[self.speed*60/TAU,tq,100*self.actual_feed/self.feeder['max_output_kg_s']]])[-TREND_MAX:]
    def _app_invoke(self,c,a):
        f=self.feeder
        if c=='sensor.configure_error':
            if 'pressure_zero_drift_pa_per_k' in a:self.press_drift=a['pressure_zero_drift_pa_per_k']
            return {'pressure_zero_drift_pa_per_k':self.press_drift}
        if c=='feeder.configure_material':self.bulk_true=a['bulk_density_kg_m3'];return dict(a)
        if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
        if c=='die.set_temperature':
            if a['temperature_k']<self.plant['ambient_k']:raise ActionRejected('INVALID_ARGUMENT')
            self.die_target=a['temperature_k'];self.reached[7]=False;self.temp_reached=False;return dict(a)
        if c in ('clock.set','clock.sync','display.configure','trend.control','alarms.acknowledge'):
            if c=='alarms.acknowledge':
                self.alarm_latched=[x for x in self.alarm_latched if not ALARM_CODES[x][1]]   # 'press Mute/Reset to continue'
                n=1 if self.alarm_active else 0;self.alarm_active=None;return {'acknowledged':n}
            if c=='display.configure':self.torque_unit=a['torque_unit'];return dict(a)
            if c=='trend.control':
                if a['action']=='clear':self.trend=[];self._trend_t=None
                else:self.trend_paused=a['action']=='pause'
                return dict(a)
            if c=='clock.sync':self.plc_offset=self.hmi_offset;return {'plc':self._wall(self.plc_offset).strftime('%H.%M.%S %d.%m.%y')}
            import datetime
            w=self._wall(self.hmi_offset)
            try:
                if 'hmi_time' in a:
                    hh,mm,ss=(int(x) for x in a['hmi_time'].split('.'));w=w.replace(hour=hh,minute=mm,second=ss)
                if 'hmi_date' in a:
                    dd,mo,yy=(int(x) for x in a['hmi_date'].split('.'));w=w.replace(year=2000+yy,month=mo,day=dd)
            except (ValueError,TypeError):raise ActionRejected('BAD_CLOCK')
            if not a:raise ActionRejected('INVALID_ARGUMENT')
            self.hmi_offset=(w-datetime.datetime.fromisoformat(EPOCH)).total_seconds()-self.time_s;return {'hmi':self._wall(self.hmi_offset).strftime('%H.%M.%S %d.%m.%y')}
        if not self.powered:raise ActionRejected('PRECONDITION_FAILED')
        if c in ('fault.inject','fault.clear'):
            code=a['code'];kind=ALARM_CODES[code][0]
            if c=='fault.clear':
                self.alarm_latched=[x for x in self.alarm_latched if x!=code];return {'latched':list(self.alarm_latched)}
            if code not in self.alarm_latched:self.alarm_latched.append(code)
            self._alarm(code+' '+ALARM_CODES[code][2])
            if kind=='drive':self._halt('failed')
            if kind=='feeder':f.update(running=False,calculating=False,calc_s=0.,calc_kg=0.)
            return {'latched':list(self.alarm_latched)}
        if c=='feeder.set_running':
            if a['running'] and any(ALARM_CODES[x][0]=='feeder' for x in self.alarm_latched):raise ActionRejected('PRECONDITION_FAILED')
            if a['running'] and not a['test'] and not self.drive:raise ActionRejected('EXTRUDER_NOT_RUNNING')
            if a['running'] and (not self.material or sum(self.hopper)<=0):raise ActionRejected('PRECONDITION_FAILED')
            f.update(running=a['running'],test=a['test'] and a['running'],refill=False,deviation_s=0.,corr_kg_s=0.,calculating=False,calc_s=0.,calc_kg=0.)
            if not a['running'] and f['mode'] in ('gravimetric_discharge','discharge'):f['mode']='gravimetric'
            return {'running':f['running']}
        if c=='feeder.configure':
            if not a:raise ActionRejected('INVALID_ARGUMENT')
            g=dict(f,**a)
            if g['min_fill_kg']>=g['max_fill_kg']:raise ActionRejected('INVALID_ARGUMENT')
            if 'bulk_density_kg_m3' in a and 'max_output_kg_s' not in a:      # the controller's estimate until Calc.Max.Output (or a typed value) replaces it
                g['max_output_kg_s']=min(OUTPUT_MAX,max(FEED_MIN,FEED_MAX*(a['bulk_density_kg_m3']/BULK_DEFAULT)))
            if 'mode' in a and a['mode']!=f['mode']:g['corr_kg_s']=0.
            if a.get('mode') in ('gravimetric_discharge','discharge'):
                if f['mode']==a['mode'] and f['running']:g.update(running=False,mode='gravimetric')        # pressed again: stop
                elif not self.material or sum(self.hopper)<=0:raise ActionRejected('PRECONDITION_FAILED')
                else:g.update(running=True,test=False,refill=False)
            self.feeder.update(g);return {'configured':True}
        if c=='feeder.action':
            x=a['action'];v=0.
            if x=='tare':f['tare_kg']=sum(self.hopper);v=f['tare_kg']
            elif x=='reset_throughput':f['throughput_kg']=0.
            elif x=='refill':f['refill']=not f['refill'];v=float(f['refill'])
            else:                                                               # one minute at the set screw speed (volumetric setpoint) into a container
                if (f['running'] and not f['test']) or self.phase=='running' or f['setpoint_pct']<=0 or not self.material or sum(self.hopper)<=0:raise ActionRejected('PRECONDITION_FAILED')
                f.update(running=False,test=False,refill=False,calculating=True,calc_s=CALC_S,calc_kg=0.);v=CALC_S
            return {'action':x,'value':v}
        if c=='pressure.tare':
            raw=self._pressure_raw()
            if raw>TARE_MAX_PA:raise ActionRejected('PRESSURE_TOO_HIGH')
            self.press_offset=raw;return {'offset_pa':self.press_offset}
        if c=='pressure.set_limit':self.press_limit=a['limit_pa'];return dict(a)
        if c=='recipe.save':
            self.recipes[a['index']-1]=copy.deepcopy(a);return {'index':a['index']}
        if c=='recipe.write':
            r=self.recipes[a['index']-1]
            if r['speed_rad_s'] is None:raise ActionRejected('EMPTY_RECIPE')
            if min(r['temperatures_k'])<self.plant['ambient_k']:raise ActionRejected('INVALID_ARGUMENT')
            self._halt();f.update(running=False,refill=False)                                   # extruder and feeder speeds to zero for safety
            self.speed_target=r['speed_rad_s'];self.targets=list(r['temperatures_k'][:7]);self.die_target=r['temperatures_k'][7]
            self.deviation_k=list(r['alarm_deviation_k']);self.feed_target=r['feed_kg_s'];self.reached=[False]*8;self.temp_reached=False;self.recipe_loaded=a['index']
            return {'index':a['index']}
        raise ActionRejected('UNKNOWN_CAPABILITY')
    # ---- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
    def panel_read(self):
        out=self.read('agent');t=self.trend;tq=200*self.torque/12.
        out.update({'host.powered':self.powered,'host.power_epoch':self.power_epoch,'host.temps_c':[x-273.15 for x in self._hmi_temps()],
            'host.targets_c':[x-273.15 for x in self.targets+[self.die_target]],'host.torque_text':{'Nm':f'{2*self.torque:.2f} Nm','%':f'{tq:.1f} %','kW':f'{2*self.torque*self.speed/1000:.3f} kW'}[self.torque_unit],
            'host.melt_c':self.temps[-1]-273.15,'host.rpm':self.speed*60/TAU,'host.set_rpm':self.speed_target*60/TAU,'host.serial':self.instance_id,
            'host.tr':dict({f'z{i}':[[r[0]/60,r[1+i]] for r in t] for i in range(8)},speed=[[r[0]/60,r[9]/10] for r in t],torque=[[r[0]/60,r[10]] for r in t],feed=[[r[0]/60,r[11]] for r in t]),
            'host.trend_end':(t[-1][0]/60 if t else 0.),'host.torque_pct':tq,'host.feed_pct':100*self.actual_feed/self.feeder['max_output_kg_s'],
            'host.alarms_newest':list(reversed(self.alarm_log[-200:])),'host.alarm_summary':[dict(text=k,count=v) for k,v in sorted(self.alarm_summary.items())]})
        return out
    def _guard(self,k,principal):
        """Caller rule (final sync 2026-10-19): a timed run belongs to the session that started it; another agent may not stop it or change its drive,
        feed, temperatures, feeder, pressure probe or recipe while it runs (direct call or HMI gesture).  The isolator and the emergency stop are
        never refused."""
        if principal is not None and self._principals.get(principal)=='agent' and k in RUN_GUARDED and self.phase=='running':
            owner=self._owners.get(self.operation_id)
            if owner is not None and owner!=principal:raise ActionRejected('ACCESS_DENIED')
    def panel_invoke(self,capability_id,arguments):
        """A panel action calls the same command a direct call would (each command checks before it changes anything); a gesture made through a
        session keeps that caller's access rules (_guard)."""
        try:
            self._guard(capability_id,self._caller)
            return self.invoke(capability_id,**arguments)
        except ActionRejected as exc:raise _panel_ui.PanelRejected(str(exc)) from None
    def panel_time(self):return self.time_s
    def _panel_cmd(self,k,a):
        p=self.panel
        try:
            if k=='panel.switch':r={'accepted':bool(p.switch(a['input_id'],a['position']))}
            elif k=='panel.touch':r={'accepted':bool(p.touch(a['display_id'],a['x'],a['y'],a.get('duration_s',.1),(a['to_x'],a['to_y']) if 'to_x' in a and 'to_y' in a else None))}
            elif k=='panel.frame':return {'frame':p.frame()}
            else:
                import hashlib,tempfile
                data,w,hh=p.png_bytes(a['display_id']);sha=hashlib.sha256(data).hexdigest()
                d=Path(getattr(self,'panel_capture_dir',None) or Path(tempfile.gettempdir())/'chemistry_sim_panel'/self.instance_id);d.mkdir(parents=True,exist_ok=True)
                path=d/f"{a['display_id']}_{sha[:16]}.png"
                if not path.exists():path.write_bytes(data)
                return dict(display_id=a['display_id'],time_s=self.time_s,image=dict(path=str(path),format='png',width=w,height=hh,sha256=sha))
        except _panel_ui.PanelError:raise ActionRejected('INVALID_ARGUMENT') from None
        self.revision+=1;return r
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
            h._guard(capability_id,self._principal);h._caller=self._principal   # an HMI gesture acts as this session's caller
            try:result=h.invoke(capability_id,**arguments)
            finally:h._caller=None
            op=result.get('operation_id')
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




def _library_limitations():
    sm=json.loads((ROOT/TEMPLATE_REL).read_text())['summary']
    return ['七区CSTR不是实际螺槽三维输送、返混/停留时间分布或螺杆元件CAD；mixing_factor不是厂家元件组合。',
        f"material.configure 仅支持70/30 PCM-Affinisol 显式假设参数；material.load_reference 支持{sm['process_model_records']}种文献黏度律材料（{sm['process_model_with_real_density_and_cp']}种密度/热容全真实，其余缺项需宿主显式假设）；模型仍不能代表实测药物/塑料工艺或用于剂型制备。",
        f"库共{sm['distinct_records']}条真实数据记录，运行时可用{sm['runtime_usable_records']}条（{'达到' if sm['target_50_met'] else '低于'}50条目标）；工况多来自Pharma 11/ZE12/ZSE12同级机及专用口模，只按原工况精确返回，不用于标定当前圆口模。",
        '剪切变稀只在口模按表观壁面剪切率施加且仅限有实测Cross/Carreau-Yasuda参数的记录，无Rabinowitsch修正；筒内拖曳仍用零剪切黏度；无黏弹性/熔融潜热/降解动力学/挥发/返流/泄漏/溶解结晶；混合仅为两组分理想均匀账本。',
        '七区瞬时等温且轴功均匀沉积，进料区水冷只为宿主边界；无冷却水温量或真实温控辨识。','触摸屏HMI由前面板层（panel_ui.json）驱动本运行层；完整安全联锁/机器人/硬件/接触力/部署隔离均not_run。']
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
            errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','PRECONDITION_FAILED','BUSY','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','REFERENCE_OUT_OF_RANGE','AMBIGUOUS_REFERENCE','REFERENCE_NOT_RUNTIME_USABLE','COLD_MATERIAL']+(APP_ERRORS if k in APP_CAPS else [])],
            example_arguments=copy.deepcopy(ex),verification=[],limitations=['外置喂料和拆装由宿主；真实固件、安全认证、材料标定未完成'],source_ids=['manufacturer_primary','simulation_assumption'])
    mech=dict(component_ids=['motor','screw_a','screw_b','barrel_lower','barrel_upper','feed_port','die','receiver'],
        state_ids=list(slots),meaning_zh='双螺杆七有限完全混合区、圆口模牛顿压降和轴功/电热/带料焓平衡',implementation='lumped',
        equations=['dm_i/dt=in_i-out_i; out_i<=k*m_i, k=transport_per_rev*omega/(2*pi*mixing_factor), upstream limited by recipient free capacity',
        'finite hopper feeds fixed70/30 mixture; product/collected/waste retain both species',
        'mu0(T)=mu_ref*exp(Ea/R*(1/T-1/Tref)) (explicit parameters or library Arrhenius law), mu_ref*exp(-C1*(T-Tr)/(C2+T-Tr)) (library WLF law; Cross-WLF: mu_ref=D1, Tr=D2 (D3*p neglected), C1=A1, C2=A2) or mu_ref*exp(-alpha*(T-Tref)) (library exponential shift)',
        'die only, records with measured shear parameters: gamma_a=4Q/(pi*R^3); Cross mu=mu0/(1+(mu0*gamma_a/tau*)^(1-n)); Carreau-Yasuda mu=mu0*(1+(lambda*aT*gamma_a)^a)^((n-1)/a), aT=mu0(T)/mu0(Tref); no Rabinowitsch correction; barrel drag keeps mu0',
        'material.load_reference: law, minimum processing T and TGA degradation onset from the library record; density/cp from the record or explicit host assumption',
        'any filled zone above the record degradation onset temperature latches DEGRADATION_ONSET after the energy step',
        'pressure_gauge=8*mu_die*die_length*Q/(pi*die_radius^4); Q=out_mass_rate/rho; mu_die=mu0(T_last) unless shear parameters exist',
        'torque_per_shaft=drag_coeff*omega*mean(mu_i*fill_i)*mixing_factor+pressure*Q/(2*omega)',
        'pressure>10MPa or per-shaft torque>6Nm causes latched stop before proposed transfer; thermal>623.15K trips after energy step',
        'E_i=(C_barrel+cp*m_i)*(T_i-Ta); dE_i/dt=heater_i-loss_i+2*tau*omega/7+enthalpy_in-enthalpy_out',
        'sum(E_i)+exported_enthalpy+integral(loss)=integral(heaters)+integral(2*tau*omega)',
        'age_mass_i increases by m_i*dt, transfers proportionally; mean output residence=exported_age_mass/exported_mass',
        'co-rotating phases q_a=q mod2pi; q_b=(q+pi/2)mod2pi, same -X axis; positive speed target10–1000rpm',
        'top-half barrel detached manually by host along +Z150mm proxy, not a motor or real hinge'],
        parameters={},source_ids=['manufacturer_primary','manufacturer_process','measured_reference','simulation_assumption'],
        limitations=_library_limitations())
    spec=importlib.util.spec_from_file_location('_process11_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann)
    ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
    ann.annotate_export(SLUG,caps,slots,{'extrusion':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'material.configure':(MATERIAL,MATERIAL_EXAMPLE)})
    mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'extrusion':mech})

"""CHEM-056 V2: finite metered supply, moving stage and lumped thermal balance."""
from __future__ import annotations
import copy, importlib.util, json, math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SLUG='ossila_slotdie'; ASSET_TYPE_ID='instrument.'+SLUG; CATALOG_ID='CHEM-056'; PROTOCOL_VERSION='1.0.0'; ASSET_VERSION='1.3.1'
ASSET_DIR=ROOT/'assets/expansion_batch04/t5_automation/ossila_slotdie'
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
cap('stage.home',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['button_return','motor','carriage'],'规定速度回回零位置（默认后端，Settings的Stage Home可改为本次运行的当前位置）；空料无膜时才允许',effects=['cycle.phase','stage.target_m'])
cap('stage.move_to',obj({'position_m':POS,'speed_m_s':SPEED}),obj({'phase':PHASE,'operation_id':TEXT}),['carriage','motor'],'无料轴定位，非自动涂布',ex={'position_m':.05,'speed_m_s':.02},effects=['cycle.phase','stage.target_m'])
cap('gap.set',obj({'left_m':GAP,'right_m':GAP}),obj({'left_m':GAP,'right_m':GAP}),['head','gauge_left','gauge_right'],'宿主手动双端调隙目标；非机载电动执行','configuration','agent',{'left_m':.0001,'right_m':.0001},['gap.left_target_m','gap.right_target_m'])
cap('emergency.stop',obj({}),obj({'latched':BOOL}),['button_emergency'],'急停锁存；驱动停止且回零失效',effects=['safety.pressed','safety.latched','power.enabled','stage.homed','cycle.phase'])
cap('emergency.release',obj({}),obj({'released':BOOL}),['button_emergency'],'宿主顺时针释放按钮，尚未重置驱动故障','configuration','agent',{},['safety.pressed'])
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['button_start','motor','carriage','head','plunger'],'运行所选程序：各步先平台延时（供液、平台不动）再按长度与速度移动基板并定量供液（流量按所选注射器规格换算柱塞速度）；有限注射器与已预充管路',effects=['cycle.phase','cycle.elapsed_s','stage.target_m'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['button_stop','motor'],'硬停止；保留部分成膜和残余料',effects=['cycle.phase','stage.target_m'])
cap('coating.measure',obj({}),RESULT,['display_digits','gauge_left','gauge_right'],'理想位置/速度/间隙读数；不是膜厚仪','query')
cap('product.read',obj({}),PRODUCT,['substrate','syringe_barrel','waste_tray'],'有限注射器/管路/液桥/膜/废料/蒸发与能量账本','query','evaluator')
cap('product.collect',obj({'recipient_id':TEXT}),obj({'recipient_id':TEXT,'attachment_event':TEXT}),['substrate'],'停止后宿主释放基板并转移膜账本','device_command','evaluator',{'recipient_id':'coated_slide'},['substrate.configured','truth.ledger'])
cap('fluid.drain',obj({}),obj({'emptied':BOOL}),['syringe_barrel','waste_tray'],'停止后宿主将注射器/管路/液桥余料转至废料，非自驱清洗','configuration','evaluator',{},['ink.configured','truth.ledger'])
cap('reference.lookup',obj({'substance_id':TEXT,'temperature_k':number('K',1)}),obj({'vapor_pressure_pa':number('Pa',0),'evidence':TEXT,'source':TEXT,'template_source':TEXT}),['syringe_barrel'],'NIST有效原始温区蒸气压参考','query','agent',{'substance_id':'ethanol','temperature_k':298.15})
REF_T=298.15;REF_TOL=.5
FIT_EVIDENCE={'nist_srd69_saturation_fit':'derived_NIST_SRD69_saturation_Antoine_fit_no_extrapolation','thermoml_vapour_pressure_fit':'derived_ThermoML_article_Antoine_fit_no_extrapolation','clausius_clapeyron_local':'derived_local_Clausius_Clapeyron_PubChem_point_NIST_dHvap_no_extrapolation'}
PROPS_OUT=obj({'temperature_k':number('K',0),'density_kg_m3':number('kg/m^3',0),'viscosity_pa_s':number('Pa*s',0),'surface_tension_n_m':number('N/m',0),'vapor_pressure_pa':number('Pa',0),'normal_boiling_point_k':number('K',0),'molar_mass_kg_mol':number('kg/mol',0),'evidence':TEXT,'source':TEXT,'template_source':TEXT})
cap('reference.properties',obj({'substance_id':TEXT}),PROPS_OUT,['syringe_barrel'],'298.15K真实纯溶剂物性（密度/黏度/表面张力/蒸气压/沸点；NIST SRD69、ThermoML实测中位数、PubChem），记录不完整则拒绝','query','agent',{'substance_id':'toluene'})
INK_REF=obj({k:v for k,v in INK['properties'].items() if k not in ('solvent_density_kg_m3', 'viscosity_pa_s', 'surface_tension_n_m', 'latent_heat_j_kg')})
INK_REF_EXAMPLE=dict({k:v for k,v in INK_EXAMPLE.items() if k in INK_REF['properties']},solvent_id='toluene')
cap('ink'+'.configure_from_reference',INK_REF,obj(dict({k:number(INK['properties'][k]['x-unit'],0) for k in ('solvent_density_kg_m3', 'viscosity_pa_s', 'surface_tension_n_m', 'latent_heat_j_kg')},configured=BOOL,evidence=TEXT)),['syringe_barrel'],'墨液溶剂密度/黏度/表面张力/汽化潜热取自库内298.15K真实物性；环境温度须为298.15±0.5K，加热时这些值保持298.15K值','configuration','evaluator',INK_REF_EXAMPLE,['ink.configured', 'truth.ledger'])

cap('pump.dispense',obj({'volume_m3':number('m^3',1e-9,5e-6),'flow_m3_s':FLOW}),obj({'phase':PHASE,'operation_id':TEXT}),['plunger','syringe_barrel','feed_tube'],'有限注射器定量预充管路/液桥，余量去废料；无反抽',ex={'volume_m3':1e-7,'flow_m3_s':2e-8},effects=['cycle.phase','pump.flow_m3_s','pump.position_m'])
cap('thermal.set_temperature',obj({'temperature_k':number('K',278.15,413.15)}),obj({'temperature_k':number('K',278.15,413.15)}),['hotplate'],'写入140℃以内温度目标，非瞬时升温',ex={'temperature_k':313.15},effects=['thermal.target_k'])
cap('thermal.set_enabled',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['hotplate'],'独立热台开关；运动停止不自动关热',ex={'enabled':True},effects=['thermal.enabled'])
cap('thermal.set_offset',obj({'offset_k':number('K',-10,10)}),obj({'offset_k':number('K',-10,10)}),['display_digits'],'热台温度偏置±10 K（手册p.23）：控制器把内部传感器调到设定值+偏置，使表面达到设定温度；显示的是传感器温度，正偏置时高于设定值（需外部表面温度计确定偏置）','configuration','agent',{'offset_k':1.},['thermal.offset_k'])
# Immediate effects are distinct from later physical evolution.
for key in ['power.set','power.toggle','emergency.stop']:CAPS[key][7].extend(['thermal.enabled','pump.flow_m3_s','stage.speed_m_s'])
CAPS['acquisition.stop'][7].extend(['pump.flow_m3_s','stage.speed_m_s'])
CAPS['ink.configure'][7].append('pump.position_m')
CAPS['plant.configure'][7].extend(['thermal.target_k','thermal.indicated_k'])
CAPS['fluid.drain'][7].append('safety.fault')
# front panel 1.2.0 (manual 8.1-8.2, 10, 11.1): syringe size setting, stage home, manual stage / syringe jog, 20 programs of up to 50 steps
# (coating length, coating speed, dispense rate, stage delay), the two digital height gauges; the selected program is the method that runs
TRAVEL=.15;PROGRAMS=20;MAX_STEPS=50
SYRINGES={'1':(.00461,.79e-12,84e-9),'2':(.00965,3.5e-12,232e-9),'5':(.0103,4.0e-12,265e-9),'10':(.0159,9.5e-12,632e-9),'20':(.02005,15.8e-12,1e-6),
          '50':(.0326,39.8e-12,2.7e-6)}                                      # manual Table 11.1: diameter, minimum and maximum flow rate
MANUAL_PLUNGER=86.378e-9/PISTON_AREA                                         # manual p.26 picture: 086.378 ul/s with the 5 ml syringe (plunger ~1.04 mm/s)
def INT(lo,hi):return dict(type='integer',minimum=lo,maximum=hi,**{'x-unit':'1'})
def opt(p,req=()):return dict(type='object',properties=p,required=list(req),additionalProperties=False)
def _nul(x):return {'anyOf':[x,{'type':'null'}]}
RATE=number('m^3/s',0,1e-5)
SETTINGS_FIELDS=dict(syringe=dict(type='string',enum=list(SYRINGES)+['cust']),diameter_m=number('m',0,.05),manual_speed_m_s=SPEED,manual_rate_m3_s=RATE)
SETTINGS_VIEW=obj(dict(SETTINGS_FIELDS,home_m=POS,rate_min_m3_s=RATE,rate_max_m3_s=RATE,usable=BOOL,warning=_nul(TEXT)))
STEP_FIELDS={'length_m':POS,'speed_m_s':number('m/s',0,.075),'rate_m3_s':RATE,'delay_s':number('s',0,999.999)}
STEP_ROW=obj(dict(step=INT(1,MAX_STEPS),**STEP_FIELDS))
PROGRAM_VIEW=obj({'program':INT(1,PROGRAMS),'count':INT(1,MAX_STEPS),'steps':dict(type='array',minItems=1,maxItems=MAX_STEPS,items=STEP_ROW),'speed_set':BOOL,
                  'total_length_m':number('m',0,TRAVEL),'warning':_nul(TEXT)})
GAUGE=obj({'on':BOOL,'zero_m':GAP,'reading_m':number('m',-.013,.013)})
GAUGES_VIEW=obj({'left':GAUGE,'right':GAUGE})
PROGRAM_CAPS={}
def pcap(k,i,o,c,z,ex,effects,kind='configuration'):PROGRAM_CAPS[k]=(i,o,c,z,kind,'agent',ex,effects)
pcap('settings.configure',opt(SETTINGS_FIELDS),SETTINGS_VIEW,['display_digits'],
     '面板设定：注射器规格（1/2/5/10/20/50 mL或Cust自定内径；固件按所选规格把流量换算为柱塞速度，实际装的是5 mL/10.3 mm注射器）、手动模式平台速度与注射器流量（超过所选规格最大流量时修正为最大并警告）',
     {'syringe':'5'},['settings.state','method.configured'])
pcap('stage.set_home',obj({}),SETTINGS_VIEW,['button_return','carriage'],'Settings的Stage Home行：按Return把当前平台位置设为本次运行的回零位置（断电复位为后端）',{},['settings.state'])
pcap('program.select',obj({'program':INT(1,PROGRAMS)}),PROGRAM_VIEW,['display_digits'],'选择程序1–20；所选程序即运行的方法（各步：涂布长度、涂布速度、流量、平台延时）',{'program':2},['programs.state','method.configured'])
pcap('program.set_steps_count',obj({'count':INT(1,MAX_STEPS)}),PROGRAM_VIEW,['display_digits'],'所选程序的步数1–50（增加的步全为0未设定，减少即删去末尾步）',{'count':2},['programs.state','method.configured'])
pcap('program.set_step',opt(dict(step=INT(1,MAX_STEPS),**STEP_FIELDS),['step']),PROGRAM_VIEW,['display_digits'],
     '所选程序某一步：涂布长度（超过剩余行程时设为剩余长度并警告）、涂布速度0.05–75 mm/s、流量（所选注射器的最小–最大，超过最大时修正并警告）、平台延时（延时期间供液、平台不动）',
     {'step':1,'length_m':.05,'speed_m_s':.01,'rate_m3_s':2e-8},['programs.state','method.configured'])
pcap('stage.jog',obj({'direction':dict(type='string',enum=['forward','back']),'duration_s':number('s',.02,60)}),obj({'phase':PHASE,'operation_id':TEXT}),['carriage','motor'],
     '手动模式Stage选中时左右键：按住时间×手动速度移动平台（forward离开后端），限0–150 mm；与stage.move_to同样的前提',{'direction':'forward','duration_s':1.},['cycle.phase','stage.target_m'],'device_command')
pcap('pump.jog',obj({'direction':dict(type='string',enum=['push','retract']),'duration_s':number('s',.02,60)}),obj({'phase':PHASE,'operation_id':TEXT}),['plunger','syringe_barrel','feed_tube'],
     '手动模式Syringe选中时左右键：按住时间×手动柱塞速度推进（经管路供液）或回抽（夹住的柱塞把管路液体吸回注射器，其余为空气）；面板体积按所选规格计',
     {'direction':'push','duration_s':1.},['cycle.phase','pump.flow_m3_s','pump.position_m'],'device_command')
pcap('gauge.button',obj({'gauge':dict(type='string',enum=['left','right']),'button':dict(type='string',enum=['on','zero'])}),GAUGES_VIEW,['gauge_keys_left','gauge_keys_right'],
     '数字高度计按键：OFF/ON开关，Zero把当前读数置零（读数=间隙−零点）；高度计用LR44电池，与主机电源无关',{'gauge':'left','button':'on'},['gauges.state'],'device_command')

# 1.3.0 (sync audit), evaluator only: the hotplate surface may differ from the internal sensor (manual p.23: the temperature offset compensates it), and
# the troubleshooting table's "Crash Warning" (manual p.43: the secondary safety switch) through a fault injection - an accidental trigger is cleared by
# switching off and on; the plunger pusher on the switch (rotate the lead screw by hand) or a faulty switch keep it until the cause is removed (fault.clear)
FAULT_CAUSES=['triggered','plunger_pusher','switch_fault']
SIM_CAPS={}
def scap(k,i,o,c,z,ex,effects):SIM_CAPS[k]=(i,o,c,z,'simulation','evaluator',ex,effects)
scap('sensor.configure_error',obj({'surface_rise_error':number('1',-.2,.2)}),obj({'surface_rise_error':number('1',-.2,.2)}),['hotplate'],
     '评测者：热台表面温度与内部传感器的偏差（表面−环境=(1+e)×(传感器−环境)；默认0即表面=传感器）；手册p.23的温度偏置用来补偿它（须外部温度计）',{'surface_rise_error':-.05},['truth.surface_k'])
scap('fault.inject',obj({'code':dict(type='string',enum=['crash_warning']),'cause':dict(type='string',enum=FAULT_CAUSES)}),obj({'code':TEXT,'phase':PHASE}),['motor','pump_motor','display_digits'],
     '评测者注入手册故障排除表的Crash Warning（p.43）：triggered=次级安全开关被意外触发（关开电源即消除）、plunger_pusher=柱塞推块压着次级开关（须手转丝杠把推块移向行程中部）、'
     'switch_fault=开关内部故障；平台与注射泵运动立即停止（运行失败），屏幕显示Crash Warning，回零/移动/点动/启动/预充/回抽被拒',{'code':'crash_warning','cause':'triggered'},
     ['safety.crash_warning','truth.secondary_switch','cycle.phase'])
scap('fault.clear',obj({}),obj({'cause':{'anyOf':[TEXT,{'type':'null'}]}}),['pump_motor'],'评测者：原因已排除（手转丝杠移开推块，或开关已修复）；Crash Warning仍须关开电源才消失',{},['truth.secondary_switch'])

def _panel_engine():
    try:
        from . import _panel_ui as engine                                   # imported as asset_runtime.ossila_slotdie
        return engine
    except ImportError:                                                      # loaded from its file path
        import sys
        if '_chemsim_panel_ui' not in sys.modules:
            spec=importlib.util.spec_from_file_location('_chemsim_panel_ui',Path(__file__).with_name('_panel_ui.py'));sys.modules['_chemsim_panel_ui']=importlib.util.module_from_spec(spec);spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
        return sys.modules['_chemsim_panel_ui']
_panel_ui=_panel_engine()
PANEL_SPEC=_panel_ui.load_spec(ASSET_DIR/'panel_ui.json',capabilities=set(CAPS)|set(PROGRAM_CAPS))
PANEL_CAPS={k:(d['input_schema'],d['output_schema'],d['component_ids'],d['function_zh'],d['kind'],d['visibility'],d['example_arguments'],[]) for k,d in _panel_ui.capability_definitions(PANEL_SPEC).items()}
ALL_CAPS={**CAPS,**PROGRAM_CAPS,**SIM_CAPS,**PANEL_CAPS}

class _Panel(_panel_ui.PanelUI):
    """Front-panel UI whose state dict lives in the runtime (panel_ui), so copies and snapshots of a runtime include the screen it shows."""
    def __init__(self,host):
        saved=host.__dict__.get('panel_ui');super().__init__(PANEL_SPEC,host)
        if saved is not None:self.ui=saved
    @property
    def ui(self):return self.host.panel_ui
    @ui.setter
    def ui(self,value):self.host.panel_ui=value

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
    slot('gap.'+end+'_m',GAP,'m',end+'端模头实际升起量（相对基板的绝对间隙，场景值；未标定热态平整度）；高度计屏上的读数是相对其零点的gauges.state.reading_m（按Zero后=间隙−零点）','scene',c='gauge_'+end)
slot('gap.mean_m',GAP,'m','模头平均升起量，非湿膜厚度','scene',c='head_lift')
slot('gap.tilt_rad',number('rad',-.131,.131),'rad','模头绕Y倾斜角，右高为负','scene',c='head')
slot('safety.pressed',BOOL,'1','急停按钮宿主事件，不是机器人触发证明','scene',c='button_emergency')
slot('safety.latched',BOOL,'1','必须释放后关开电源的故障锁存',c='button_emergency')
slot('cycle.phase',PHASE,'1','回零/定位/涂布异步状态',c='display_digits')
slot('cycle.stage',TEXT,'1','homing/manual/delay/coating/priming/retracting/stopped/idle（delay为程序步的平台延时，retracting为手动回抽）',c='display_digits')
slot('cycle.elapsed_s',number('s',0),'s','本次操作宿主累计时间',c='display_digits')
slot('pump.position_m',number('m',0,PLUNGER_MAX),'m','5mL/10.3mm内径柱塞位移，不是质量比例','scene',c='plunger')
slot('pump.flow_m3_s',number('m^3/s',0,Q_MAX),'m^3/s','理想实际液体体积流量，不是额外流量传感器',c='display_digits')
slot('pump.delivered_m3',number('m^3',0,5e-6),'m^3','累计柱塞排出体积，含预充与废料',c='display_digits')
slot('thermal.target_k',number('K',278.15,413.15),'K','热台设定值，上限140℃','setpoint',c='hotplate',w=('thermal.set_temperature','temperature_k'))
slot('thermal.enabled',BOOL,'1','独立加热开关','setpoint',c='hotplate',w=('thermal.set_enabled','enabled'))
slot('thermal.offset_k',number('K',-10,10),'K','热台温度偏置：控制目标=设定值+偏置（手册p.23）','setpoint',c='display_digits',w=('thermal.set_offset','offset_k'))
slot('thermal.indicated_k',number('K',1,450),'K','热台内部传感器温度（屏上显示值；控制器把它调到设定值+偏置）；表面温度可与之不同（评测者sensor.configure_error），不是实测基板温度',c='display_digits')
slot('safety.fault',TEXT,'1','空注射器或越出模型温区故障标记',c='display_digits')
slot('truth.surface_k',number('K',1,450),'K','热台表面温度（外部温度计读到的值；= 环境 + (1+surface_rise_error)×(传感器−环境)）','process_truth','evaluator','hotplate')
slot('safety.crash_warning',BOOL,'1','屏幕上的Crash Warning（次级安全开关，手册p.43）；关开电源后若开关已正常则消失',c='display_digits')
slot('truth.secondary_switch',{'anyOf':[dict(type='string',enum=['plunger_pusher','switch_fault']),{'type':'null'}]},'1','仍存在的Crash Warning原因（柱塞推块压着次级开关或开关故障；fault.clear前每次开机都显示）；无为null','process_truth','evaluator','pump_motor')
slot('truth.ledger',PRODUCT,'1','64条带薄膜/有限浆料/废料/蒸发/收集物种账本','process_truth','evaluator','substrate')
slot('settings.state',SETTINGS_VIEW,'1','面板设定：注射器规格/自定内径及所选规格的流量范围、手动平台速度与流量、本次回零位置、最近的设定警告','setpoint',c='display_digits')
slot('programs.state',PROGRAM_VIEW,'1','面板所选程序（1–20）及其各步（长度、速度、流量、延时）、是否都设了速度、总长度、最近的程序警告','setpoint',c='display_digits')
slot('gauges.state',GAUGES_VIEW,'1','两只数字高度计：开关、零点与读数（间隙−零点）',c='gauge_display_left')
SLOTS['gauges.state']=SLOTS['gauges.state'][:4]+(['gauge_display_left','gauge_display_right'],None)

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
        self.settings=dict(syringe='5',diameter_m=0.,home_m=0.,warning=None);self.manual_plunger=MANUAL_PLUNGER;self.manual_volume=0.
        self.programs={str(n):[[0.,0.,0.,0.]] for n in range(1,PROGRAMS+1)};self.program_no=1;self.run_program=1;self.prog_warning=None
        self.plan=[];self.plan_index=0;self.run_delivered0=0.
        self.gauges={g:dict(on=False,zero_m=0.) for g in ('left','right')}
        self.power_epoch=0;self.panel_ui=None;self.panel_capture_dir=None;_Panel(self)
        self.surface_rise_error=0.   # 1.3.0 evaluator: the surface temperature rise differs from the sensor's (manual p.23; default 0 = surface reads as the sensor)
        self.crash_warning=False;self.secondary=None   # 1.3.0: Crash Warning on the screen / a cause that persists through a power cycle
        self._caller=None   # principal of the session call in progress (set by OssilaSlotdieSession.invoke; panel keys act as that caller)
    @property
    def panel(self):return _Panel(self)
    def _surface_k(self,sensor_k=None):
        """Hotplate surface temperature (what an external thermometer reads): its rise above ambient is (1 + surface_rise_error) x the sensor's."""
        t=self.temp if sensor_k is None else sensor_k;amb=self.plant['ambient_k'] if self.plant else t
        return amb+(1.+self.surface_rise_error)*(t-amb)
    def _control_k(self):return self.target_temp+self.offset   # manual p.23: the offset moves the controlled sensor temperature so the surface gets the set value
    @staticmethod
    def _grid(value,quantum):
        if abs(value/quantum-round(value/quantum))>1e-6:raise ActionRejected('INVALID_ARGUMENT')
    # ---- panel settings, program store and gauges (manual 8.2, 11.1)
    def _sel_area(self):
        st=self.settings;d=st['diameter_m'] if st['syringe']=='cust' else SYRINGES[st['syringe']][0]
        return math.pi*d**2/4
    def _rate_limits(self):
        st=self.settings
        if st['syringe']!='cust':return SYRINGES[st['syringe']][1:]
        return self._sel_area()*6e-6,self._sel_area()*.0075                 # Cust: the syringe-speed range of Table 3.1 (assumed)
    def _settings_view(self):
        lo,hi=self._rate_limits();a=self._sel_area()
        return dict(syringe=self.settings['syringe'],diameter_m=self.settings['diameter_m'],manual_speed_m_s=self.manual_speed,manual_rate_m3_s=round(self.manual_plunger*a,15),
                    home_m=self.settings['home_m'],rate_min_m3_s=lo,rate_max_m3_s=hi,usable=a>0,warning=self.settings['warning'])
    def _program_view(self):
        steps=self.programs[str(self.program_no)]
        return dict(program=self.program_no,count=len(steps),steps=[dict(step=i+1,length_m=a,speed_m_s=b,rate_m3_s=c,delay_s=d) for i,(a,b,c,d) in enumerate(steps)],
                    speed_set=all(x[1]>0 for x in steps),total_length_m=round(sum(x[0] for x in steps),9),warning=self.prog_warning)
    def _load_program(self):
        steps=self.programs[str(self.program_no)]
        if all(x[1]>0 for x in steps) and self._sel_area()>0:
            self.method=dict(distance_m=round(sum(x[0] for x in steps),9),speed_m_s=steps[0][1],flow_m3_s=steps[0][2],stage_delay_s=steps[0][3],steps=[list(x) for x in steps])
        else:self.method=None
    def _gauges_view(self):
        return {g:dict(on=v['on'],zero_m=v['zero_m'],reading_m=round(self.gaps[i]-v['zero_m'],9)) for i,(g,v) in enumerate(self.gauges.items())}
    def _begin_step(self,i):
        """Start plan step i: its stage delay (the syringe dispenses, the stage waits), then its coating length; past the last step the run has finished."""
        if i>=len(self.plan):
            self.phase='succeeded';self.stage='idle';self.speed=0.;self.flow=0.;return
        self.plan_index=i;length,speed,flow,delay=self.plan[i];self.target=min(TRAVEL,round(self.q+length,12))
        if delay>0:self.stage='delay';self.delay_left=delay
        else:self.stage='coating'
    def _arrived(self,stage):
        if stage=='coating':self._begin_step(self.plan_index+1)
        else:self.homed=self.homed or stage=='homing';self.phase='succeeded';self.stage='idle';self.speed=0.;self.flow=0.
    def _retract(self,volume):
        """The clamped plunger moves back: liquid of the line is drawn into the syringe and the bead refills the line; the rest is air."""
        moved=0.
        for src,dst in ((self.line,self.syringe),(self.bead,self.line)):
            vol=self._volume(src);f=min(1.,(volume if dst is self.syringe else moved)/vol) if vol>1e-30 else 0.
            for i in range(2):m=src[i]*f;src[i]-=m;dst[i]+=m
            if dst is self.syringe:moved=vol*f
        self.pump_q=max(0.,self.pump_q-volume/PISTON_AREA)
    # ---- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
    def panel_read(self):
        """What the panel firmware draws: the agent slots plus power, lamps, temperatures in degC, the syringe label and the running program's numbers ('host.*')."""
        v=self.read('agent');run=self.phase=='running' and self.stage in ('delay','coating') and bool(self.plan)
        cur=self.plan[self.plan_index] if run else [0.,0.,0.,0.];ratio=self._sel_area()/PISTON_AREA
        total=sum(d+(l/sp if sp>0 else 0.) for l,sp,_,d in self.plan) if run else 0.
        frac=min(1.,self.elapsed_s/total) if run and total>0 else 0.
        v.update({'host.powered':self.powered,'host.power_epoch':self.power_epoch,'host.coating':run,'host.heating':self.heating and self.powered,
                  'host.heater_at_temp':abs(self.temp-self._control_k())<2.,'host.temp_c':self.temp-273.15,'host.set_c':self.target_temp-273.15,
                  'host.syringe_label':'Cust' if self.settings['syringe']=='cust' else self.settings['syringe']+' ml','host.manual_volume_m3':self.manual_volume,
                  'host.run':dict(program=self.run_program,step=self.plan_index+1,count=len(self.plan),speed_m_s=cur[1],length_m=cur[0],rate_m3_s=cur[2]*ratio,
                                  volume_m3=(self.delivered-self.run_delivered0)*ratio,progress=frac,bar='#'*int(round(frac*20))+'-'*(20-int(round(frac*20))),
                                  delay=self.stage=='delay',elapsed=f'{int(self.elapsed_s//60):02d}:{self.elapsed_s%60:04.1f}'),
                  'host.fault':self.fault})
        return v
    def panel_invoke(self,capability_id,arguments):
        try:return self.invoke(capability_id,**arguments)
        except ActionRejected as exc:raise _panel_ui.PanelRejected(str(exc)) from None
    def panel_time(self):return self.time_s
    def _gesture(self,fn,*args):
        try:return {'accepted':bool(fn(*args))}
        except _panel_ui.PanelError:raise ActionRejected('INVALID_ARGUMENT') from None
    def _capture(self,display_id):
        import hashlib,tempfile
        try:data,w,h=self.panel.png_bytes(display_id)
        except _panel_ui.PanelError:raise ActionRejected('INVALID_ARGUMENT') from None
        sha=hashlib.sha256(data).hexdigest();d=Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir())/'chemistry_sim_panel'/self.instance_id;d.mkdir(parents=True,exist_ok=True);path=d/f'{display_id}_{sha[:16]}.png'
        if not path.exists():path.write_bytes(data)
        return dict(display_id=display_id,time_s=self.time_s,image=dict(path=str(path),format='png',width=w,height=h,sha256=sha))
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
        f=fits[0];kind=f.get('kind','nist_antoine');return dict(vapor_pressure_pa=1e5*10**(f['A']-f['B_k']/(t+f['C_k'])),evidence=FIT_EVIDENCE.get(kind,'derived_NIST_Antoine_no_extrapolation'),source=r['source'] if kind=='nist_antoine' else f.get('source',r['source']),template_source=TEMPLATE_REL)
    def _properties(self,sid):
        if sid not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
        r=self.templates[sid];p=r.get('properties_298_15K') or {};keys=('density_kg_m3','viscosity_pa_s','surface_tension_n_m')
        if not r.get('usable_at_298_15K') or any(k not in p for k in keys) or not r.get('normal_boiling_point'):raise ActionRejected('REFERENCE_INCOMPLETE')
        vp=self._reference(sid,REF_T)
        return dict(temperature_k=REF_T,density_kg_m3=p['density_kg_m3']['value'],viscosity_pa_s=p['viscosity_pa_s']['value'],surface_tension_n_m=p['surface_tension_n_m']['value'],vapor_pressure_pa=vp['vapor_pressure_pa'],
                    normal_boiling_point_k=r['normal_boiling_point']['value_k'],molar_mass_kg_mol=r['molar_mass_kg_mol'],
                    evidence=';'.join(k+':'+p[k]['evidence'] for k in keys)+';vapor_pressure:'+vp['evidence'],
                    source=';'.join(dict.fromkeys(str(p[k].get('source_name')) for k in keys)),template_source=TEMPLATE_REL)
    def _latent(self,sid):
        h=(self.templates[sid].get('vaporization_enthalpy') or {}).get('value_j_mol')
        if not h:raise ActionRejected('REFERENCE_INCOMPLETE')
        return h/self.templates[sid]['molar_mass_kg_mol']


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
        'pump.position_m':self.pump_q,'pump.flow_m3_s':self.flow,'pump.delivered_m3':self.delivered,'thermal.target_k':self.target_temp,'thermal.enabled':self.heating,'thermal.offset_k':self.offset,'thermal.indicated_k':self.temp,'truth.surface_k':self._surface_k(),'safety.crash_warning':self.crash_warning,'truth.secondary_switch':self.secondary,'truth.ledger':self._product(),
        'settings.state':self._settings_view(),'programs.state':self._program_view(),'gauges.state':self._gauges_view()}
        return copy.deepcopy({k:x for k,x in v.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
    def _foreign_run(self):
        """An agent may not stop (Stop key, acquisition.stop), switch off (power switch, power.set / power.toggle) or change the hotplate (thermal.*) of another
        agent's running operation (program, homing, stage or syringe jog, priming); the same check for the direct call and the panel key, because both run
        with the session caller in _caller.  Unowned operations, the evaluator and the emergency stop are not restricted."""
        c=self._caller;o=self._owners.get(self.operation_id)
        return self.phase=='running' and c is not None and self._principals.get(c)=='agent' and o is not None and o!=c
    def _stop(self,result='cancelled'):
        if self.phase=='running':self.phase=result;self.stage='stopped';self.target=self.q
        self.speed=0.;self.flow=0.
    def _new_operation(self,stage,target):
        self.phase='running';self.stage=stage;self.target=target;self.elapsed_s=0.;self._operation_count+=1
        self.operation_id=self.instance_id+':motion:'+str(self._operation_count)
        return dict(phase=self.phase,operation_id=self.operation_id)
    def _bead_capacity(self):return .1*sum(self.gaps)/2*self.plant['bead_length_m']
    def _model_check(self,plan):
        if self.sample['width_m']!=.1 or min(self.gaps)<=0:raise ActionRejected('MODEL_OUT_OF_RANGE')
        for length,speed,flow,delay in plan:                                 # every coating step inside the metered-film window
            if length<=0:continue
            if flow<=0:raise ActionRejected('MODEL_OUT_OF_RANGE')
            h=flow/(.1*speed)
            if not all(self.plant['min_gap_film_ratio']<=g/h<=self.plant['max_gap_film_ratio'] for g in self.gaps):raise ActionRejected('MODEL_OUT_OF_RANGE')
        if self._volume(self.line)<self.plant['line_dead_volume_m3']*(1-1e-8) or self._volume(self.bead)<self._bead_capacity()*(1-1e-8):raise ActionRejected('NOT_PRIMED')
    def _fit_for(self,config,target):
        """The ink's vapour-pressure fit must cover the surface temperatures of the run: now, ambient and the surface at the controlled target (set + offset)."""
        if config['solvent_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
        temps=[self._surface_k(),self.plant['ambient_k'],self._surface_k(target)];low=min(temps);high=max(temps)
        matches=[f for f in self.templates[config['solvent_id']]['fits'] if f['temperature_range_k'][0]<=low and high<=f['temperature_range_k'][1]]
        if not matches:raise ActionRejected('REFERENCE_OUT_OF_RANGE')
        return copy.deepcopy(matches[0])
    def invoke(self,capability,**a):
        if capability not in ALL_CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(ALL_CAPS[capability][0],a);orig=capability;used=None
        if capability=='panel.frame':return {'frame':self.panel.frame()}
        if capability=='panel.capture':return self._capture(a['display_id'])
        if capability=='panel.press':out=self._gesture(self.panel.press,a['input_id'],float(a.get('duration_s',.1)));self.revision+=1;return out
        if capability=='panel.switch':out=self._gesture(self.panel.switch,a['input_id'],a['position']);self.revision+=1;return out
        if capability=='ink.configure_from_reference':
            if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
            if abs(self.plant['ambient_k']-REF_T)>REF_TOL:raise ActionRejected('REFERENCE_OUT_OF_RANGE')
            props=self._properties(a['solvent_id']);fill={'solvent_density_kg_m3':props['density_kg_m3'],'viscosity_pa_s':props['viscosity_pa_s'],'surface_tension_n_m':props['surface_tension_n_m'],'latent_heat_j_kg':self._latent(a['solvent_id'])}
            used=dict(fill,evidence='library_298.15K:'+props['evidence']);a=dict(a,**fill);validate(INK,a);capability='ink.configure'
        if capability=='plant.configure':
            if self.plant or not a['accept_uncalibrated_model'] or a['min_gap_film_ratio']>=a['max_gap_film_ratio']:raise ActionRejected('PRECONDITION_FAILED')
            self.plant=a.copy();self.temp=a['ambient_k'];self.target_temp=self.temp;out={'configured':True}
        elif capability=='method.configure':
            if self.phase=='running':raise ActionRejected('BUSY')
            self.programs[str(self.program_no)]=[[a['distance_m'],a['speed_m_s'],a['flow_m3_s'],a['stage_delay_s']]]   # also stored as the selected program
            self.method=dict(a,steps=[[a['distance_m'],a['speed_m_s'],a['flow_m3_s'],a['stage_delay_s']]]);out={'configured':True}
        elif capability=='substrate.configure':
            if self.phase=='running' or self.sample_present or self.q!=0 or not self.homed or not a['accept_host_attachment'] or self._surface_k()>323.15:raise ActionRejected('PRECONDITION_FAILED')
            if a['start_m']+a['length_m']>.15:raise ActionRejected('INVALID_GEOMETRY')
            self.sample=a.copy();self.sample_present=True;out={'configured':True}
        elif capability=='ink.configure':
            if not self.plant or self.ink_cfg or self.phase=='running' or not a['accept_newtonian_and_connections']:raise ActionRejected('PRECONDITION_FAILED')
            fit=self._fit_for(a,self._control_k());volume=a['solvent_kg']/a['solvent_density_kg_m3']+a['solute_kg']/a['solute_density_kg_m3']
            if volume>5e-6:raise ActionRejected('SYRINGE_OVERFLOW')
            self.ink_cfg=a.copy();self.fit=fit;self.syringe=[a['solvent_kg'],a['solute_kg']];self.initial=self.syringe.copy();self.pump_q=(5e-6-volume)/PISTON_AREA;out={'configured':True}
        elif capability in ('power.set','power.toggle'):
            enabled=a['enabled'] if capability=='power.set' else not self.power_switch
            # lead rule 10-19: the rear power switch (power.set / power.toggle model it) is a hard switch - physical, never refused (a power loss)
            if enabled and self.latched and (self.pressed or not self.reset_off):raise ActionRejected('PRECONDITION_FAILED')
            if enabled and not self.powered:self.power_epoch+=1;self.manual_volume=0.;self.crash_warning=self.secondary is not None   # manual p.43: switch off and on again
            self.power_switch=enabled
            if not enabled:self.powered=False;self.homed=False;self.heating=False;self._stop();self.reset_off=self.latched and not self.pressed;self.settings['home_m']=0.
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
            if self._foreign_run():raise ActionRejected('ACCESS_DENIED')   # lead rule 10-19: no changing another agent's run (the hotplate under its coating)
            if capability in ('thermal.set_temperature','thermal.set_offset'):
                target=a['temperature_k']+self.offset if capability=='thermal.set_temperature' else self.target_temp+a['offset_k']
                fit=self._fit_for(self.ink_cfg,target) if self.ink_cfg and sum(self.syringe+self.line+self.bead)+sum(map(sum,self.film))>1e-15 else self.fit
                self.fit=fit
                if capability=='thermal.set_temperature':self.target_temp=a['temperature_k']
                else:self.offset=a['offset_k']
            elif capability=='thermal.set_enabled':
                if a['enabled'] and (not self.powered or self.latched):raise ActionRejected('PRECONDITION_FAILED')
                self.heating=a['enabled']
            out=a.copy()
        elif capability in ('stage.home','stage.move_to','acquisition.start','pump.dispense'):
            if not self.plant or not self.powered or self.latched or self.fault or self.crash_warning or self.phase=='running':raise ActionRejected('PRECONDITION_FAILED')
            if capability=='acquisition.start':
                if not all([self.method,self.ink_cfg,self.sample_present,self.homed]) or any(abs(a-b)>1e-12 for a,b in zip(self.gaps,self.gap_targets)):raise ActionRejected('PRECONDITION_FAILED')
                if self.q+self.method['distance_m']>.15+1e-12:raise ActionRejected('INVALID_GEOMETRY')
                if self._volume(self.syringe)<=1e-15:raise ActionRejected('SYRINGE_EMPTY')
                ratio=PISTON_AREA/self._sel_area()                           # the firmware drives the plunger for the selected syringe size
                plan=[[l,v,r*ratio,d] for l,v,r,d in self.method['steps']];self._model_check(plan)
                self.plan=plan;self.run_program=self.program_no;self.run_delivered0=self.delivered;out=self._new_operation('coating',self.q);self._begin_step(0)
            elif capability=='pump.dispense':
                if not self.ink_cfg or not self.homed or min(self.gaps)<=0:raise ActionRejected('PRECONDITION_FAILED')
                if a['volume_m3']>self._volume(self.syringe)+1e-15:raise ActionRejected('SYRINGE_EMPTY')
                self.dose_left=a['volume_m3'];self.dose_flow=a['flow_m3_s'];out=self._new_operation('priming',self.q)
            else:
                if (self._volume(self.bead)>0 or sum(map(sum,self.film))>1e-15) and min(self.gaps)<.001:raise ActionRejected('PRECONDITION_FAILED')
                if capability=='stage.home':out=self._new_operation('homing',self.settings['home_m'])
                else:
                    if not self.homed:raise ActionRejected('PRECONDITION_FAILED')
                    self.manual_speed=a['speed_m_s'];out=self._new_operation('manual',a['position_m'])
        elif capability=='acquisition.stop':
            if self._foreign_run():raise ActionRejected('ACCESS_DENIED')
            self._stop();out={'phase':self.phase}
        elif capability=='sensor.configure_error':
            self.surface_rise_error=a['surface_rise_error'];out=a.copy()
        elif capability=='fault.inject':
            self.crash_warning=True;self.secondary=None if a['cause']=='triggered' else a['cause'];self._stop('failed');out={'code':a['code'],'phase':self.phase}
        elif capability=='fault.clear':
            out={'cause':self.secondary};self.secondary=None
        elif capability=='settings.configure':
            busy={'syringe','diameter_m','manual_speed_m_s','manual_rate_m3_s'}&a.keys()
            if self.phase=='running' and busy:raise ActionRejected('BUSY')
            if 'diameter_m' in a:self._grid(a['diameter_m'],1e-6)
            if 'manual_speed_m_s' in a:self._grid(a['manual_speed_m_s'],1e-6)
            new=dict(self.settings,**{k:a[k] for k in ('syringe','diameter_m') if k in a},warning=None)
            old=self.settings;self.settings=new;area=self._sel_area();lo,hi=self._rate_limits();self.settings=old
            plunger=self.manual_plunger
            if 'manual_rate_m3_s' in a:
                r=a['manual_rate_m3_s'];self._grid(r,1e-12)
                if area<=0:raise ActionRejected('PRECONDITION_FAILED')                                     # Cust without a diameter
                if 0<r<lo*(1-1e-9):raise ActionRejected('INVALID_ARGUMENT')
                if r>hi*(1+1e-9):r=hi;new['warning']='rate_corrected'                                      # manual p.29: corrected to the maximum
                plunger=r/area
            if new['syringe']=='cust' and new['diameter_m']<=0:new['warning']='diameter_not_set'             # manual p.24
            self.settings=new;self.manual_plunger=plunger
            if 'manual_speed_m_s' in a:self.manual_speed=a['manual_speed_m_s']
            self._load_program();out=self._settings_view()
        elif capability=='stage.set_home':
            if not self.powered or self.latched or self.crash_warning or self.phase=='running' or not self.homed:raise ActionRejected('PRECONDITION_FAILED')
            self.settings['home_m']=self.q;out=self._settings_view()
        elif capability=='program.select':
            if self.phase=='running':raise ActionRejected('BUSY')
            self.program_no=a['program'];self.prog_warning=None;self._load_program();out=self._program_view()
        elif capability=='program.set_steps_count':
            if self.phase=='running':raise ActionRejected('BUSY')
            steps=self.programs[str(self.program_no)];self.programs[str(self.program_no)]=(steps+[[0.,0.,0.,0.] for _ in range(MAX_STEPS)])[:a['count']]
            self.prog_warning=None;self._load_program();out=self._program_view()
        elif capability=='program.set_step':
            if self.phase=='running':raise ActionRejected('BUSY')
            steps=self.programs[str(self.program_no)]
            if a['step']>len(steps):raise ActionRejected('PRECONDITION_FAILED')
            row=list(steps[a['step']-1]);warning=None
            if 'length_m' in a:
                self._grid(a['length_m'],1e-6);rest=round(TRAVEL-sum(x[0] for i,x in enumerate(steps) if i!=a['step']-1),9)
                row[0]=a['length_m']
                if row[0]>rest+1e-12:row[0]=max(0.,rest);warning='length_corrected'                         # manual p.28: the remaining allowed length
            if 'speed_m_s' in a:
                self._grid(a['speed_m_s'],1e-6)
                if 0<a['speed_m_s']<5e-5-1e-12:raise ActionRejected('INVALID_ARGUMENT')                     # 0 (not set) or 0.05-75 mm/s
                row[1]=a['speed_m_s']
            if 'rate_m3_s' in a:
                r=a['rate_m3_s'];self._grid(r,1e-12);lo,hi=self._rate_limits()
                if r>0 and self._sel_area()<=0:raise ActionRejected('PRECONDITION_FAILED')
                if 0<r<lo*(1-1e-9):raise ActionRejected('INVALID_ARGUMENT')
                if r>hi*(1+1e-9):r=hi;warning='rate_corrected'                                             # manual p.29
                row[2]=r
            if 'delay_s' in a:self._grid(a['delay_s'],1e-3);row[3]=a['delay_s']
            steps[a['step']-1]=row;self.prog_warning=warning;self._load_program();out=self._program_view()
        elif capability=='stage.jog':
            if not self.plant or not self.powered or self.latched or self.fault or self.crash_warning or self.phase=='running' or not self.homed:raise ActionRejected('PRECONDITION_FAILED')
            if (self._volume(self.bead)>0 or sum(map(sum,self.film))>1e-15) and min(self.gaps)<.001:raise ActionRejected('PRECONDITION_FAILED')
            sign=1 if a['direction']=='forward' else -1
            out=self._new_operation('manual',min(TRAVEL,max(0.,round(self.q+sign*self.manual_speed*a['duration_s'],12))))
        elif capability=='pump.jog':
            if not self.plant or not self.powered or self.latched or self.fault or self.crash_warning or self.phase=='running' or not self.ink_cfg or self._sel_area()<=0:raise ActionRejected('PRECONDITION_FAILED')
            flow=self.manual_plunger*PISTON_AREA;volume=flow*a['duration_s']
            if a['direction']=='push':
                if not self.homed or min(self.gaps)<=0:raise ActionRejected('PRECONDITION_FAILED')
                if self._volume(self.syringe)<=1e-15:raise ActionRejected('SYRINGE_EMPTY')
                self.dose_left=min(volume,self._volume(self.syringe));self.dose_flow=flow;out=self._new_operation('priming',self.q)
            else:
                if self.pump_q<=1e-12:raise ActionRejected('PRECONDITION_FAILED')                         # the plunger is fully back
                self.dose_left=min(volume,self.pump_q*PISTON_AREA);self.dose_flow=flow;out=self._new_operation('retracting',self.q)
            self.manual_volume+=(1 if a['direction']=='push' else -1)*self.manual_plunger*self._sel_area()*a['duration_s']
        elif capability=='gauge.button':
            g=self.gauges[a['gauge']]
            if a['button']=='on':g['on']=not g['on']
            elif not g['on']:raise ActionRejected('PRECONDITION_FAILED')                                     # a switched-off gauge ignores Zero
            else:g['zero_m']=self.gaps[0 if a['gauge']=='left' else 1]
            out=self._gauges_view()
        elif capability=='coating.measure':
            return dict(phase=self.phase,stage=self.stage,position_m=self.q,speed_m_s=self.speed,left_gap_m=self.gaps[0],right_gap_m=self.gaps[1],temperature_k=self.temp,flow_m3_s=self.flow,elapsed_s=self.elapsed_s,evidence='ideal_panel_NOT_thickness_sensor',template_source=TEMPLATE_REL)
        elif capability=='product.read':return self._product()
        elif capability=='reference.lookup':return self._reference(a['substance_id'],a['temperature_k'])
        elif capability=='reference.properties':return self._properties(a['substance_id'])
        elif capability=='product.collect':
            if self.phase=='running' or not self.sample_present or self._surface_k()>323.15:raise ActionRejected('PRECONDITION_FAILED')
            for i in range(2):self.collected[i]+=sum(f[i] for f in self.film)
            self.film=[[0.,0.] for _ in range(64)];self.sample_present=False;out=dict(recipient_id=a['recipient_id'],attachment_event='host_release_substrate')
        elif capability=='fluid.drain':
            if self.phase=='running':raise ActionRejected('PRECONDITION_FAILED')
            for masses in [self.syringe,self.line,self.bead]:
                for i in range(2):self.waste[i]+=masses[i];masses[i]=0.
            self.fault='';out={'emptied':True}
        else:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(ALL_CAPS[capability][1],out)
        if used is not None:out=dict(out,**used);validate(CAPS[orig][1],out)
        self.revision+=1;return copy.deepcopy(out)
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
        p=self.plant;heat=min(p['heater_max_w'],max(0.,p['controller_w_k']*(self._control_k()-self.temp))) if self.heating and self.powered else 0.   # the controller acts on its own sensor
        loss=p['loss_w_k']*(self.temp-p['ambient_k']);evaps=[0.]*64
        if self.ink_cfg and self.sample and sum(f[0] for f in self.film)>1e-20:
            low,high=self.fit['temperature_range_k'];ts=self._surface_k()   # the film evaporates at the surface temperature
            if not low-1e-9<=ts<=high+1e-9:
                self.fault='REFERENCE_OUT_OF_RANGE';self.heating=False;self._stop('failed');heat=0.
            else:
                ref=self._reference(self.ink_cfg['solvent_id'],max(low,min(high,ts)))
                flux=p['mass_transfer_m_s']*ref['vapor_pressure_pa']*self.templates[self.ink_cfg['solvent_id']]['molar_mass_kg_mol']/(8.314462618*ts)
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
            if self.phase=='running' and stage=='retracting':                # manual retraction of the clamped plunger
                dt=min(dt,self.dose_left/self.dose_flow);vol=self.dose_flow*dt;self._retract(vol);self.dose_left=max(0.,self.dose_left-vol)
                self.flow=0.;self.speed=0.;self.elapsed_s+=dt
                if self.dose_left<1e-15 or self.pump_q<=1e-12:self.phase='succeeded';self.stage='idle'
            elif self.phase=='running':
                if stage in ['homing','manual','coating']:
                    distance=self.target-self.q
                    if abs(distance)<1e-12:
                        self.q=self.target;self._arrived(stage);continue
                    speed=self.plan[self.plan_index][1] if stage=='coating' else .01 if stage=='homing' else self.manual_speed
                    dt=min(dt,abs(distance)/speed);speed=math.copysign(speed,distance)
                if stage in ['delay','coating','priming']:
                    flow=self.dose_flow if stage=='priming' else self.plan[self.plan_index][2]
                    if flow>0:
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
                    self.q=self.target;self._arrived(stage)
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
        m=export_ossila_slotdie_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=h.time_s,sample_window_s=None) for k in ids})
    def invoke(self,*,instance_id,capability_id,arguments,request_id):
        h=self._host
        try:
            self._target(instance_id)
            if not isinstance(capability_id,str) or capability_id not in ALL_CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
            if self._role=='agent' and ALL_CAPS[capability_id][5]!='agent':raise ActionRejected('ACCESS_DENIED')
            if not isinstance(request_id,str) or not request_id:raise ActionRejected('INVALID_ARGUMENT')
            validate(ALL_CAPS[capability_id][0],arguments);key=(self._principal,instance_id,request_id);digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
            if key in h._requests:
                old,result=h._requests[key]
                if old!=digest:raise ActionRejected('REQUEST_CONFLICT')
                return copy.deepcopy(result)
            before=h.operation_id;h._caller=self._principal
            try:result=h.invoke(capability_id,**arguments)
            finally:h._caller=None
            op=result.get('operation_id')
            if op:h._owners[op]=self._principal
            elif capability_id in PANEL_CAPS and h.operation_id and h.operation_id!=before:h._owners[h.operation_id]=self._principal   # a run, homing, jog or priming started by a key belongs to the caller
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
    evolving=['stage.position_m','stage.speed_m_s','stage.homed','gap.left_m','gap.right_m','gap.mean_m','gap.tilt_rad','cycle.phase','cycle.stage','cycle.elapsed_s','pump.position_m','pump.flow_m3_s','pump.delivered_m3','thermal.indicated_k','truth.surface_k','truth.ledger','safety.fault']
    for k,(inp,out,c,z,kind,vis,ex,effects) in ALL_CAPS.items():
        asynchronous=k in ['acquisition.start','stage.home','stage.move_to','pump.dispense','stage.jog','pump.jog'];query=kind=='query'
        caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
            implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
            binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.ossila_slotdie.OssilaSlotdieSession.invoke',invocation=f'session.invoke(instance_id="slotdie_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),
            preconditions=[] if query else [dict(description_zh='停止/上电/回零/急停复位/有限料/计量稳定窗口/预充/NIST温区条件，宿主身份',state_ids=['cycle.phase','power.enabled','stage.homed','safety.latched'],enforced_by='runtime',check='Runtime.invoke guards; Session trusted principal policy')],
            state_effects=[] if query else [dict(state_id=s,timing='immediate',description_zh='事务配置、目标或有限样品附着/释放') for s in effects]+[dict(state_id=s,timing='on_step',description_zh='唯一宿主时钟推进轴、手动调隙代理和有限材料') for s in evolving],
            observes=[],model_effects=dict(mode='none',description_zh='过程存在不等于已验证几何绑定',binding_ids=[]),
            execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',completion_criterion='轴达到目标；不保证干燥/膜质量' if asynchronous else '事务完成，物理时钟未推进',
                completion_state_ids=['cycle.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',retry='same_request_id',
                resources=['host_clock','finite_charge','substrate_attachment','request_cache'],partial_failure_zh='停止/故障保留已沉积材料和挥发，残余液体继续蒸发；不回滚，不提供跨进程幂等'),
            errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','PRECONDITION_FAILED','BUSY','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','REFERENCE_OUT_OF_RANGE','REFERENCE_INCOMPLETE','INVALID_GEOMETRY','RESERVOIR_OVERFLOW','RESERVOIR_EXHAUSTED','MODEL_OUT_OF_RANGE']],
            example_arguments=copy.deepcopy(ex),verification=[],limitations=['无膜厚传感器；急停是教学锁存，不是实机安全认证；调隙需宿主手动行为'],source_ids=['manufacturer_primary','simulation_assumption'])


    mech=dict(component_ids=['motor','carriage','head','syringe_barrel','plunger','feed_tube','substrate','hotplate'],state_ids=list(slots),
        meaning_zh='有限注射供液/预充/液桥/移动基板计量膜与集总热平衡',implementation='lumped',
        equations=['dq/dt=commanded speed; q in [0,.150]m along -Y','Q=A_piston*v_piston; A=pi*(.0103m)^2/4; v in [6e-6,.0075]m/s; capacity5e-6m3',
        'line fills declared dead volume, then bead width*mean_gap*declared_length; excess -> film overlap or waste',
        'steady primed film thickness=Q/(width*stage_speed), inventory limited; static dispense -> local puddle/waste, never divide by zero',
        'head mean=(gL+gR)/2; tilt_y=-asin((gR-gL)/.1m); manual host authority',
        'P_sat=100000*10^(A-B/(Ts+C)), original fit interval only; film evaporation=k_mass*area*P_sat*M/(R*Ts)*solvent_mass_fraction at the surface temperature Ts',
        'C*dT/dt=P_control-K*(T-Ta)-L*dm_evap/dt; P_control=clamp(kp*(set+offset-T),0,Pmax) on the hotplate sensor T (1.3.0: the temperature offset moves the controlled target, manual p.23), disabled on poweroff/emergency',
        'Ts=Ta+(1+e_surface)*(T-Ta): surface vs sensor (evaluator sensor.configure_error, default e_surface=0); display and thermal.indicated_k show T',
        'evaporation bounded by inventory and energy down to the valid NIST lower-temperature boundary',
          'P_sat from the first library fit whose own temperature interval covers ambient/current/target (NIST Antoine rows first; NIST SRD69 saturation fit, ThermoML article fit or local Clausius-Clapeyron otherwise); never extrapolated',
          'ink.configure_from_reference: density/viscosity/surface tension = library values at 298.15 K, latent_heat_j_kg = NIST dHvap(298.15 K)/M; requires ambient 298.15+/-0.5 K; held constant when heated (assumption)',
        'initial_species=syringe+line+bead+film+waste+evaporated+collected; solute evaporation=0',
        'C*(T-Ta)=integral(heater_power)-integral(loss)-evaporation_latent_heat'],
        parameters={},source_ids=['manufacturer_primary','manufacturer_manual','nist_vapor_pressure','simulation_assumption'],
        limitations=['程序为20个×最多50步（长度、速度、流量、平台延时）逐步运行；注射器规格设定只改变固件换算（实际装5 mL/10.3 mm注射器），步进电机速度取整未建模；只选100mm标准头，未做高黏度变体。','稳定窗口是宿主显式假设，不解弯月面自由界面、接触角、空气卷入、压降、背压或非Newtonian流变。','注射器/管路/液桥/废料视为封闭存量，仅热台上膜挥发；停机管路渗漏/被动滴落未求解。','单温度整体热台/膜，不代表实测表面温度、热膨胀调平；热容/功率/散热/潜热需材料实测。','库内溶剂298.15K真实物性（密度/黏度/表面张力/蒸气压/沸点/汽化焓）不等于实测狭缝涂膜；从库装料后加热时密度与汽化潜热仍取298.15K值（假设）；80nm匿名染料示例不作为确定物质模板。','加载液体后目标温度必须处于一个覆盖当前/环境/目标的有效NIST温区；不外推，干台可设置140℃。','小示例100×120mm基板、固定几何；动态膜/湿润前缘/变尺寸网格未做。','理想位置/泵流量/温度/数字间隙示值，无真实传感器噪声、精度或力标定。','零件质量/惯量/摩擦未知；机器人、真实安全接触、硬件和部署文件隔离均未验证。'])
    spec=importlib.util.spec_from_file_location('_slot_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann)
    ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'slot_die':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'ink.configure':(INK,INK_EXAMPLE),'substrate.configure':(SAMPLE,SAMPLE_EXAMPLE)})
    mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'slot_die':mech})



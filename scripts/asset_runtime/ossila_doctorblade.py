"""CHEM-055 Current100mm glass configuration. Finite bead, blade film, SI host clock; not calibrated."""
from __future__ import annotations
import copy, importlib.util, json, math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SLUG='ossila_doctorblade'; ASSET_TYPE_ID='instrument.'+SLUG; CATALOG_ID='CHEM-055'; PROTOCOL_VERSION='1.0.0'; ASSET_VERSION='1.3.2'
ASSET_DIR=ROOT/'assets/expansion_batch04/t5_automation/ossila_doctorblade'
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
METHOD=obj({'distance_m':number('m',.001,.29),'speed_m_s':dict(SPEED,multipleOf=1e-5)})   # 1.3.2: the panel grids in the schemas (0.01 mm/s)
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
cap('stage.home',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['button_return','motor','carriage'],'规定速度回回零位置（回零参考端+回零偏移，默认后端）；空料无膜时才允许',effects=['cycle.phase','stage.target_m'])
cap('stage.move_to',obj({'position_m':POS,'speed_m_s':dict(SPEED,multipleOf=1e-5)}),obj({'phase':PHASE,'operation_id':TEXT}),['carriage','motor'],'无料轴定位，非自动涂布',ex={'position_m':.05,'speed_m_s':.02},effects=['cycle.phase','stage.target_m'])
cap('gap.set',obj({'left_m':dict(GAP,multipleOf=1e-6),'right_m':dict(GAP,multipleOf=1e-6)}),obj({'left_m':GAP,'right_m':GAP}),['blade','gauge_left','gauge_right'],'宿主手动双端调隙目标；非机载电动执行','configuration','agent',{'left_m':.0001,'right_m':.0001},['gap.left_target_m','gap.right_target_m'])
cap('emergency.stop',obj({}),obj({'latched':BOOL}),['button_emergency'],'急停锁存；驱动停止且回零失效',effects=['safety.pressed','safety.latched','power.enabled','stage.homed','cycle.phase'])
cap('emergency.release',obj({}),obj({'released':BOOL}),['button_emergency'],'宿主顺时针释放按钮，尚未重置驱动故障','configuration','agent',{},['safety.pressed'])
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['button_start','motor','carriage','blade'],'运行所选程序：从回零位置按各步长度与速度分段恒速刮涂（延时步停留），离开回零端方向；有限浆料',effects=['cycle.phase','cycle.elapsed_s','stage.target_m'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['button_stop','motor'],'停止：HARD立即停，SOFT在0.3 s内线性减速停（停止方式见settings.configure）；保留部分成膜和残余料',effects=['cycle.phase','stage.target_m'])
cap('coating.measure',obj({}),RESULT,['display_digits','gauge_left','gauge_right'],'理想位置/速度/间隙读数；不是膜厚仪','query')
cap('product.read',obj({}),PRODUCT,['substrate','reservoir','waste_tray'],'有限浆料、膜、废料、蒸发评价账本','query','evaluator')
cap('product.collect',obj({'recipient_id':TEXT}),obj({'recipient_id':TEXT,'attachment_event':TEXT}),['substrate'],'停止后宿主释放基板并转移膜账本','device_command','evaluator',{'recipient_id':'coated_slide'},['substrate.configured','truth.ledger'])
cap('reservoir.empty',obj({}),obj({'emptied':BOOL}),['reservoir','waste_tray'],'停止后宿主清空残余料至废料，非自驱清洗','configuration','evaluator',{},['charge.configured','truth.ledger'])
cap('reference.lookup',obj({'substance_id':TEXT,'temperature_k':number('K',1)}),obj({'vapor_pressure_pa':number('Pa',0),'evidence':TEXT,'source':TEXT,'template_source':TEXT}),['reservoir'],'NIST有效原始温区蒸气压参考','query','agent',{'substance_id':'ethanol','temperature_k':298.15})
REF_T=298.15;REF_TOL=.5
FIT_EVIDENCE={'nist_srd69_saturation_fit':'derived_NIST_SRD69_saturation_Antoine_fit_no_extrapolation','thermoml_vapour_pressure_fit':'derived_ThermoML_article_Antoine_fit_no_extrapolation','clausius_clapeyron_local':'derived_local_Clausius_Clapeyron_PubChem_point_NIST_dHvap_no_extrapolation'}
PROPS_OUT=obj({'temperature_k':number('K',0),'density_kg_m3':number('kg/m^3',0),'viscosity_pa_s':number('Pa*s',0),'surface_tension_n_m':number('N/m',0),'vapor_pressure_pa':number('Pa',0),'normal_boiling_point_k':number('K',0),'molar_mass_kg_mol':number('kg/mol',0),'evidence':TEXT,'source':TEXT,'template_source':TEXT})
cap('reference.properties',obj({'substance_id':TEXT}),PROPS_OUT,['reservoir'],'298.15K真实纯溶剂物性（密度/黏度/表面张力/蒸气压/沸点；NIST SRD69、ThermoML实测中位数、PubChem），记录不完整则拒绝','query','agent',{'substance_id':'toluene'})
CHARGE_REF=obj({k:v for k,v in CHARGE['properties'].items() if k not in ('solvent_density_kg_m3', 'viscosity_pa_s')})
CHARGE_REF_EXAMPLE=dict({k:v for k,v in CHARGE_EXAMPLE.items() if k in CHARGE_REF['properties']},solvent_id='toluene')
cap('charge'+'.configure_from_reference',CHARGE_REF,obj(dict({k:number(CHARGE['properties'][k]['x-unit'],0) for k in ('solvent_density_kg_m3', 'viscosity_pa_s')},configured=BOOL,evidence=TEXT)),['reservoir'],'装料溶剂密度/黏度取自库内298.15K真实纯溶剂物性；宿主温度须为298.15±0.5K','configuration','evaluator',CHARGE_REF_EXAMPLE,['charge.configured', 'truth.ledger'])
# front panel 1.2.0 (manual 9.1-9.2, quick guide): settings, 20 programs of up to 100 steps (coating length + speed, or a delay step), manual jog,
# home reference / offset, HARD / SOFT stop and the two digital micrometer gauges of the applicator; the selected program is the method that runs
TRAVEL=.29;PROGRAMS=20;MAX_STEPS=100;SOFT_STOP_S=.3                         # SOFT: linear deceleration to rest in 0.3 s (assumed; manual: 'a short period')
def INT(lo,hi):return dict(type='integer',minimum=lo,maximum=hi,**{'x-unit':'1'})
def opt(p,req=()):return dict(type='object',properties=p,required=list(req),additionalProperties=False)
SETTINGS_FIELDS=dict(brightness_pct=INT(5,100),home_reference=dict(type='string',enum=['back','front']),stop_mode=dict(type='string',enum=['hard','soft']),manual_speed_m_s=SPEED)
SETTINGS_VIEW=obj(dict(SETTINGS_FIELDS,home_offset_m=POS))
SETTINGS_DEFAULT=dict(brightness_pct=100,home_reference='back',stop_mode='hard',home_offset_m=0.)
STEP_FIELDS={'length_m':POS,'speed_m_s':number('m/s',0,.25),'delay_s':number('s',0,999.99)}
STEP_ROW=obj(dict(step=INT(1,MAX_STEPS),**STEP_FIELDS))
PROGRAM_VIEW=obj({'program':INT(1,PROGRAMS),'count':INT(1,MAX_STEPS),'steps':dict(type='array',minItems=1,maxItems=MAX_STEPS,items=STEP_ROW),'runnable':BOOL})
PROGRAM_DEFAULT=[[TRAVEL,.1,0.]]                                             # manual p.19 Program 01: 290.00 mm, 100.00 mm/s, delay 000.00 s
GAUGE=obj({'on':BOOL,'unit':dict(type='string',enum=['mm','in']),'zero_m':GAP,'reading_m':number('m',-.01,.01)})
GAUGES_VIEW=obj({'left':GAUGE,'right':GAUGE})
PROGRAM_CAPS={}
def pcap(k,i,o,c,z,ex,effects,kind='configuration'):PROGRAM_CAPS[k]=(i,o,c,z,kind,'agent',ex,effects)
pcap('settings.configure',opt(dict(SETTINGS_FIELDS,brightness_pct=dict(INT(5,100),multipleOf=5),manual_speed_m_s=dict(SPEED,multipleOf=1e-6))),SETTINGS_VIEW,['display_digits'],'面板设定：LCD亮度（5%步进）、回零参考（后端BACK/前端FRONT：改变离开回零端的行进方向并清零回零偏移）、停止方式（HARD立即停/SOFT短时减速停）、手动模式速度',
     {'stop_mode':'soft'},['settings.state'])
pcap('stage.set_home_offset',obj({}),SETTINGS_VIEW,['button_return','carriage'],'手动模式按住Return：回零偏移设为当前位置距回零参考端的距离，此后回零与程序起点都在该位置',{},['settings.state'])
pcap('program.select',obj({'program':INT(1,PROGRAMS)}),PROGRAM_VIEW,['display_digits'],'选择程序1–20；所选程序即运行的方法（每步为涂布长度+速度，或延时步）',{'program':2},['programs.state','method.configured'])
pcap('program.set_steps_count',obj({'count':INT(1,MAX_STEPS)}),PROGRAM_VIEW,['display_digits'],'所选程序的步数1–100（增加的步为0 mm/0 mm/s/0 s未编程，减少即删去末尾步）',{'count':2},['programs.state','method.configured'])
pcap('program.set_step',opt(dict(step=INT(1,MAX_STEPS),length_m=dict(POS,multipleOf=1e-5),speed_m_s=dict(STEP_FIELDS['speed_m_s'],multipleOf=1e-5),delay_s=dict(STEP_FIELDS['delay_s'],multipleOf=.01)),['step']),PROGRAM_VIEW,['display_digits'],
     '所选程序某一步：涂布长度0–290 mm与速度0.05–250 mm/s（网格0.01 mm、0.01 mm/s），或延时0.01–999.99 s（延时步的长度与速度为0；设长度或速度即清除延时）',
     {'step':1,'length_m':.2,'speed_m_s':.02},['programs.state','method.configured'])
pcap('stage.jog',obj({'direction':dict(type='string',enum=['forward','back']),'duration_s':number('s',.02,60)}),obj({'phase':PHASE,'operation_id':TEXT}),['carriage','motor'],
     '手动模式左右键：按住时间×手动速度移动推杆（forward离开回零端，back回向回零端），限0–290 mm行程；与stage.move_to同样只在已回零、无料无膜时允许',
     {'direction':'forward','duration_s':1.},['cycle.phase','stage.target_m'],'device_command')
pcap('gauge.button',obj({'gauge':dict(type='string',enum=['left','right']),'button':dict(type='string',enum=['on','unit','origin'])}),GAUGES_VIEW,['gauge_keys_left','gauge_keys_right'],
     '数字测微器按键：on开/关，mm/in切换单位，按住origin把当前间隙设为零点（读数=间隙−零点）；测微器自带电池，与主机电源无关',{'gauge':'left','button':'on'},['gauges.state'],'device_command')
# 1.3.0 (sync audit): the troubleshooting table's "Crash Warning" (manual p.24: the secondary safety switch has been triggered) through an evaluator
# fault injection; an accidental trigger is cleared by switching the unit off and on, a faulty switch keeps the warning until it is repaired (fault.clear)
FAULT_CAUSES=['triggered','switch_fault']
FAULT_CAPS={}
def fcap(k,i,o,c,z,ex,effects):FAULT_CAPS[k]=(i,o,c,z,'simulation','evaluator',ex,effects)
fcap('fault.inject',obj({'code':dict(type='string',enum=['crash_warning']),'cause':dict(type='string',enum=FAULT_CAUSES)}),obj({'code':TEXT,'phase':PHASE}),['motor','display_digits'],
     '评测者注入手册故障排除表的Crash Warning（p.24）：triggered=次级安全开关被意外触发（关开电源即消除），switch_fault=次级安全开关内部故障（修复即fault.clear前每次开机都显示）；'
     '运动立即停止（运行失败），屏幕显示Crash Warning，回零/移动/点动/启动/回零偏移被拒',{'code':'crash_warning','cause':'triggered'},['safety.crash_warning','truth.secondary_switch','cycle.phase'])
fcap('fault.clear',obj({}),obj({'cause':{'anyOf':[TEXT,{'type':'null'}]}}),['motor'],'评测者：次级安全开关已修复；Crash Warning仍须关开电源才消失',{},['truth.secondary_switch'])

def _panel_engine():
    try:
        from . import _panel_ui as engine                                   # imported as asset_runtime.ossila_doctorblade
        return engine
    except ImportError:                                                      # loaded from its file path
        import sys
        if '_chemsim_panel_ui' not in sys.modules:
            spec=importlib.util.spec_from_file_location('_chemsim_panel_ui',Path(__file__).with_name('_panel_ui.py'));sys.modules['_chemsim_panel_ui']=importlib.util.module_from_spec(spec);spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
        return sys.modules['_chemsim_panel_ui']
_panel_ui=_panel_engine()
PANEL_SPEC=_panel_ui.load_spec(ASSET_DIR/'panel_ui.json',capabilities=set(CAPS)|set(PROGRAM_CAPS))
PANEL_CAPS={k:(d['input_schema'],d['output_schema'],d['component_ids'],d['function_zh'],d['kind'],d['visibility'],d['example_arguments'],[]) for k,d in _panel_ui.capability_definitions(PANEL_SPEC).items()}
ALL_CAPS={**CAPS,**PROGRAM_CAPS,**FAULT_CAPS,**PANEL_CAPS}

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
for key in ['plant','method','charge','substrate']:slot(key+'.configured',BOOL,'1',key+'配置或存在标记','setpoint')
slot('power.enabled',BOOL,'1','驱动电源有效；急停时为否',c='switch_power',w=('power.set','enabled'))
slot('stage.homed',BOOL,'1','本次通电已回零，断电急停失效',c='carriage')
slot('stage.target_m',POS,'m','后端为零，沿-Y前进的目标','setpoint',c='carriage')
slot('stage.position_m',POS,'m','沿-Y运动学位移理想示值','scene',c='carriage')
slot('stage.speed_m_s',number('m/s',-.25,.25),'m/s','前向正后向负理想速度',c='display_digits')
for end in ['left','right']:
    slot('gap.'+end+'_target_m',GAP,'m','手动调节'+end+'端目标，1µm网格','setpoint',c='gauge_'+end)
    slot('gap.'+end+'_m',GAP,'m',end+'端刮刀实际升起量（相对基片的绝对间隙，场景值；未标定3µm精度）；测微器屏上的读数是相对其零点的gauges.state.reading_m（按住origin后=间隙−零点）','scene',c='gauge_'+end)
slot('gap.mean_m',GAP,'m','刮刀平均升起量，非湿膜厚度','scene',c='blade_lift')
slot('gap.tilt_rad',number('rad',-.1,.1),'rad','刮刀绕Y倾斜角，右高为负','scene',c='blade')
slot('safety.pressed',BOOL,'1','急停按钮宿主事件，不是机器人触发证明','scene',c='button_emergency')
slot('safety.latched',BOOL,'1','必须释放后关开电源的故障锁存',c='button_emergency')
slot('cycle.phase',PHASE,'1','回零/定位/涂布异步状态',c='display_digits')
slot('cycle.stage',TEXT,'1','homing/manual/coating/delay/stopping/stopped/idle（delay为程序延时步，stopping为SOFT减速停止）',c='display_digits')
slot('cycle.elapsed_s',number('s',0),'s','本次操作宿主累计时间',c='display_digits')
slot('truth.ledger',PRODUCT,'1','64条带薄膜/有限浆料/废料/蒸发/收集物种账本','process_truth','evaluator','substrate')
slot('settings.state',SETTINGS_VIEW,'1','面板设定：LCD亮度、回零参考与回零偏移、停止方式、手动模式速度','setpoint',c='display_digits')
slot('programs.state',PROGRAM_VIEW,'1','面板所选程序（1–20）及其各步（长度、速度、延时）与能否运行','setpoint',c='display_digits')
slot('gauges.state',GAUGES_VIEW,'1','两只数字测微器：开关、单位、零点与读数（间隙−零点）',c='gauge_display_left')
slot('safety.crash_warning',BOOL,'1','屏幕上的Crash Warning（次级安全开关，手册p.24）；关开电源后若开关已正常则消失',c='display_digits')
slot('truth.secondary_switch',{'anyOf':[dict(type='string',enum=['switch_fault']),{'type':'null'}]},'1','仍存在的Crash Warning原因（评测者注入的次级开关故障；fault.clear前每次开机都显示）；无为null','process_truth','evaluator','motor')
SLOTS['gauges.state']=SLOTS['gauges.state'][:4]+(['gauge_display_left','gauge_display_right'],None)

class OssilaDoctorbladeRuntime:
    """Trusted host controls time and manual attachment; no hardware/robot driver."""
    def __init__(self,instance_id='doctorblade_01'):
        self.instance_id=instance_id;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']}
        self.time_s=0.;self.revision=0;self.power_switch=False;self.powered=False;self.homed=False;self.pressed=False;self.latched=False;self.reset_off=False
        self.plant=None;self.method=None;self.charge_cfg=None;self.sample=None;self.sample_present=False
        self.q=0.;self.target=0.;self.speed=0.;self.manual_speed=.02;self.gaps=[0.,0.];self.gap_targets=[0.,0.]
        self.phase='idle';self.stage='idle';self.elapsed_s=0.;self.initial=[0.,0.];self.bead=[0.,0.];self.waste=[0.,0.];self.evaporated=[0.,0.];self.collected=[0.,0.];self.film=[[0.,0.] for _ in range(64)];self.evap_flux=0.
        self.operation_id='';self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
        self.settings=dict(SETTINGS_DEFAULT);self.programs={str(n):[list(x) for x in PROGRAM_DEFAULT] for n in range(1,PROGRAMS+1)};self.program_no=1
        self.plan=[];self.plan_index=0;self.wait_left=0.;self.stop_v=0.;self.stop_left=0.;self.stop_from=''
        self.gauges={g:dict(on=False,unit='mm',zero_m=0.) for g in ('left','right')}
        self.power_epoch=0;self.panel_ui=None;self.panel_capture_dir=None;_Panel(self)
        self.crash_warning=False;self.secondary=None   # 1.3.0: Crash Warning on the screen / a cause that persists through a power cycle
        self._caller=None   # principal of the session call in progress (set by OssilaDoctorbladeSession.invoke; panel keys act as that caller)
    @property
    def panel(self):return _Panel(self)
    # ---- panel settings, program store and gauges (manual 9.2, quick guide)
    def _home_end(self):return 0. if self.settings['home_reference']=='back' else TRAVEL
    def _dir(self):return 1. if self.settings['home_reference']=='back' else -1.
    def _home_pos(self):return self._home_end()+self._dir()*self.settings['home_offset_m']
    def _settings_view(self):return dict(self.settings,manual_speed_m_s=self.manual_speed)
    @staticmethod
    def _step_ok(st):
        length,speed,delay=st
        return (length>0 and speed>=5e-5-1e-12 and delay==0) or (delay>0 and length==0 and speed==0)
    def _runnable(self,steps):return all(map(self._step_ok,steps)) and sum(x[0] for x in steps)>=.001-1e-12
    def _program_view(self):
        steps=self.programs[str(self.program_no)]
        return dict(program=self.program_no,count=len(steps),steps=[dict(step=i+1,length_m=a,speed_m_s=b,delay_s=c) for i,(a,b,c) in enumerate(steps)],runnable=self._runnable(steps))
    def _load_program(self):
        steps=self.programs[str(self.program_no)]
        if self._runnable(steps):
            first=next(x for x in steps if x[0]>0)
            self.method=dict(distance_m=sum(x[0] for x in steps),speed_m_s=first[1],steps=[list(x) for x in steps])
        else:self.method=None
    def _gauges_view(self):
        return {g:dict(on=v['on'],unit=v['unit'],zero_m=v['zero_m'],reading_m=round(self.gaps[i]-v['zero_m'],9)) for i,(g,v) in enumerate(self.gauges.items())}
    def _begin_step(self,i):
        """Start plan step i (a coating move from the current position, or a delay); past the last step the program has finished."""
        if i>=len(self.plan):
            self.phase='succeeded';self.stage='idle';self.speed=0.;return
        self.plan_index=i;length,speed,delay=self.plan[i]
        if delay>0:self.stage='delay';self.wait_left=delay;self.target=self.q;self.speed=0.
        else:self.stage='coating';self.target=min(TRAVEL,max(0.,round(self.q+self._dir()*length,12)))
    def _arrived(self):
        if self.stage=='coating':self._begin_step(self.plan_index+1)
        else:self.homed=self.homed or self.stage=='homing';self.phase='succeeded';self.stage='idle';self.speed=0.
    # ---- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
    def panel_read(self):
        """What the panel firmware draws: the agent slots plus power, the Coating lamp, the position from the home end and the board temperature ('host.*')."""
        v=self.read('agent')
        v.update({'host.powered':self.powered,'host.power_epoch':self.power_epoch,
                  'host.coating':self.phase=='running' and (self.stage in ('coating','delay') or (self.stage=='stopping' and self.stop_from=='coating')),
                  'host.position_m':abs(self.q-self._home_end()),'host.board_temp_c':round(self.plant['temperature_k']-273.15,1) if self.plant else None,
                  'host.moving':0 if abs(self.speed)<1e-12 else (1 if self.speed*self._dir()>0 else -1)})
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
        return OssilaDoctorbladeSession(self,principal_id)
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
        v={'plant.configured':self.plant is not None,'method.configured':self.method is not None,'charge.configured':self.charge_cfg is not None,'substrate.configured':self.sample_present,'power.enabled':self.powered,'stage.homed':self.homed,'stage.target_m':self.target,'stage.position_m':self.q,'stage.speed_m_s':self.speed,'gap.left_target_m':self.gap_targets[0],'gap.right_target_m':self.gap_targets[1],'gap.left_m':self.gaps[0],'gap.right_m':self.gaps[1],'gap.mean_m':sum(self.gaps)/2,'gap.tilt_rad':-math.atan2(self.gaps[1]-self.gaps[0],.1),'safety.pressed':self.pressed,'safety.latched':self.latched,'safety.crash_warning':self.crash_warning,'truth.secondary_switch':self.secondary,'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.elapsed_s':self.elapsed_s,'truth.ledger':self._product(),
           'settings.state':self._settings_view(),'programs.state':self._program_view(),'gauges.state':self._gauges_view()}
        return copy.deepcopy({k:x for k,x in v.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
    def _foreign_run(self):
        """An agent may not stop (Stop key, acquisition.stop) or switch off (power switch, power.set / power.toggle) another agent's running operation (program,
        homing, jog); the same check for the direct call and the panel key, because both run with the session caller in _caller.  Unowned operations, the
        evaluator and the emergency stop are not restricted."""
        c=self._caller;o=self._owners.get(self.operation_id)
        return self.phase=='running' and c is not None and self._principals.get(c)=='agent' and o is not None and o!=c
    def _stop(self,result='cancelled'):
        if self.phase=='running':self.phase=result;self.stage='stopped';self.target=self.q;self.speed=0.
    def _new_operation(self,stage,target):
        self._archive_op();self.phase='running';self.stage=stage;self.target=target;self.elapsed_s=0.;self._operation_count+=1
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
        if capability not in ALL_CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(ALL_CAPS[capability][0],a);orig=capability;used=None
        if capability=='panel.frame':return {'frame':self.panel.frame()}
        if capability=='panel.capture':return self._capture(a['display_id'])
        if capability=='panel.press':out=self._gesture(self.panel.press,a['input_id'],float(a.get('duration_s',.1)));self.revision+=1;return out
        if capability=='panel.switch':out=self._gesture(self.panel.switch,a['input_id'],a['position']);self.revision+=1;return out
        if capability=='charge.configure_from_reference':
            if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
            if abs(self.plant['temperature_k']-REF_T)>REF_TOL:raise ActionRejected('REFERENCE_OUT_OF_RANGE')
            props=self._properties(a['solvent_id']);fill={'solvent_density_kg_m3':props['density_kg_m3'],'viscosity_pa_s':props['viscosity_pa_s']}
            used=dict(fill,evidence='library_298.15K:'+props['evidence']);a=dict(a,**fill);validate(CHARGE,a);capability='charge.configure'
        if capability=='plant.configure':
            if self.plant or not a['accept_uncalibrated_model']:raise ActionRejected('PRECONDITION_FAILED')
            self.plant=copy.deepcopy(a);out={'configured':True}
        elif capability=='method.configure':
            if self.phase=='running':raise ActionRejected('BUSY')
            self._grid(a['speed_m_s'],.00001);self.programs[str(self.program_no)]=[[a['distance_m'],a['speed_m_s'],0.]]
            self.method=dict(a,steps=[[a['distance_m'],a['speed_m_s'],0.]]);out={'configured':True}           # also stored as the selected program
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
            # lead rule 10-19: the rear power switch (power.set / power.toggle model it) is a hard switch - physical, never refused (a power loss)
            if enabled and self.latched and (self.pressed or not self.reset_off):raise ActionRejected('PRECONDITION_FAILED')
            if enabled and not self.powered:self.power_epoch+=1;self.crash_warning=self.secondary is not None   # manual p.24: switch the unit off and on again
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
        elif capability=='fault.inject':
            self.crash_warning=True;self.secondary=None if a['cause']=='triggered' else a['cause'];self._stop('failed');out={'code':a['code'],'phase':self.phase}
        elif capability=='fault.clear':
            out={'cause':self.secondary};self.secondary=None
        elif capability in ('stage.home','stage.move_to','acquisition.start'):
            if not self.plant or not self.powered or self.latched or self.crash_warning or self.phase=='running':raise ActionRejected('PRECONDITION_FAILED')
            if capability=='acquisition.start':
                if not all([self.method,self.charge_cfg,self.sample_present,self.homed]) or abs(self.q-self._home_pos())>1e-12 or any(abs(a-b)>1e-12 for a,b in zip(self.gaps,self.gap_targets)):raise ActionRejected('PRECONDITION_FAILED')
                if self.bead[0]<=1e-15:raise ActionRejected('RESERVOIR_EXHAUSTED')
                self._model_check();end=self.q+self._dir()*self.method['distance_m']
                if not -1e-12<=end<=TRAVEL+1e-12:raise ActionRejected('INVALID_GEOMETRY')   # the program's travel from the home position
                self.plan=[list(x) for x in self.method['steps']];out=self._new_operation('coating',self.q);self._begin_step(0)
            else:
                if sum(self.bead)>1e-15 or sum(map(sum,self.film))>1e-15:raise ActionRejected('PRECONDITION_FAILED')
                if capability=='stage.home':out=self._new_operation('homing',self._home_pos())
                else:
                    if not self.homed:raise ActionRejected('PRECONDITION_FAILED')
                    self._grid(a['speed_m_s'],.00001);self.manual_speed=a['speed_m_s'];out=self._new_operation('manual',a['position_m'])
        elif capability=='acquisition.stop':
            if self._foreign_run():raise ActionRejected('ACCESS_DENIED')
            if self.settings['stop_mode']=='soft' and self.phase=='running' and self.stage in ('coating','homing','manual') and abs(self.speed)>1e-12:
                self.stop_from=self.stage;self.stop_v=self.speed;self.stop_left=SOFT_STOP_S;self.stage='stopping'   # SOFT: decelerates to rest
            elif self.stage!='stopping':self._stop()
            out={'phase':self.phase}
        elif capability=='settings.configure':
            if self.phase=='running' and ({'home_reference','manual_speed_m_s'}&a.keys()):raise ActionRejected('BUSY')
            if a.get('brightness_pct',5)%5:raise ActionRejected('INVALID_ARGUMENT')                        # manual p.20: steps of 5 %
            if 'manual_speed_m_s' in a:self._grid(a['manual_speed_m_s'],1e-6)                              # Manual Mode shows 0.001 mm/s
            new=dict(self.settings,**{k:a[k] for k in ('brightness_pct','home_reference','stop_mode') if k in a})
            if new['home_reference']!=self.settings['home_reference']:new['home_offset_m']=0.             # manual p.20: resets the home offset
            self.settings=new
            if 'manual_speed_m_s' in a:self.manual_speed=a['manual_speed_m_s']
            out=self._settings_view()
        elif capability=='stage.set_home_offset':
            if not self.powered or self.latched or self.crash_warning or self.phase=='running' or not self.homed:raise ActionRejected('PRECONDITION_FAILED')
            self.settings['home_offset_m']=round(abs(self.q-self._home_end()),9);out=self._settings_view()
        elif capability=='program.select':
            if self.phase=='running':raise ActionRejected('BUSY')
            self.program_no=a['program'];self._load_program();out=self._program_view()
        elif capability=='program.set_steps_count':
            if self.phase=='running':raise ActionRejected('BUSY')
            steps=self.programs[str(self.program_no)];self.programs[str(self.program_no)]=(steps+[[0.,0.,0.] for _ in range(MAX_STEPS)])[:a['count']]
            self._load_program();out=self._program_view()
        elif capability=='program.set_step':
            if self.phase=='running':raise ActionRejected('BUSY')
            steps=self.programs[str(self.program_no)]
            if a['step']>len(steps):raise ActionRejected('PRECONDITION_FAILED')
            length,speed,delay=steps[a['step']-1]
            if 'length_m' in a:self._grid(a['length_m'],1e-5)
            if 'speed_m_s' in a:
                self._grid(a['speed_m_s'],1e-5)
                if 0<a['speed_m_s']<5e-5-1e-12:raise ActionRejected('INVALID_ARGUMENT')                   # 0 or 0.05-250 mm/s
            if 'delay_s' in a:self._grid(a['delay_s'],.01)
            if a.get('delay_s',0)>0 and (a.get('length_m',0)>0 or a.get('speed_m_s',0)>0):raise ActionRejected('INVALID_ARGUMENT')
            if a.get('delay_s',0)>0:length,speed,delay=0.,0.,a['delay_s']                                # a delay step: length and speed zero (p.23)
            else:
                length,speed=a.get('length_m',length),a.get('speed_m_s',speed)
                if 'delay_s' in a or a.get('length_m',0)>0 or a.get('speed_m_s',0)>0:delay=0.
            steps[a['step']-1]=[length,speed,delay];self._load_program();out=self._program_view()
        elif capability=='stage.jog':
            if not self.plant or not self.powered or self.latched or self.crash_warning or self.phase=='running' or not self.homed:raise ActionRejected('PRECONDITION_FAILED')
            if sum(self.bead)>1e-15 or sum(map(sum,self.film))>1e-15:raise ActionRejected('PRECONDITION_FAILED')
            sign=self._dir()*(1 if a['direction']=='forward' else -1)
            out=self._new_operation('manual',min(TRAVEL,max(0.,self.q+sign*self.manual_speed*a['duration_s'])))
        elif capability=='gauge.button':
            g=self.gauges[a['gauge']]
            if a['button']=='on':g['on']=not g['on']
            elif not g['on']:raise ActionRejected('PRECONDITION_FAILED')                                     # a switched-off gauge ignores its keys
            elif a['button']=='unit':g['unit']='in' if g['unit']=='mm' else 'mm'
            else:g['zero_m']=self.gaps[0 if a['gauge']=='left' else 1]
            out=self._gauges_view()
        elif capability=='coating.measure':
            return dict(phase=self.phase,stage=self.stage,position_m=self.q,speed_m_s=self.speed,left_gap_m=self.gaps[0],right_gap_m=self.gaps[1],elapsed_s=self.elapsed_s,evidence='ideal_panel_and_gauges_NOT_thickness_sensor',template_source=TEMPLATE_REL)
        elif capability=='product.read':return self._product()
        elif capability=='reference.lookup':return self._reference(a['substance_id'],a['temperature_k'])
        elif capability=='reference.properties':return self._properties(a['substance_id'])
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
        validate(ALL_CAPS[capability][1],out)
        if used is not None:out=dict(out,**used);validate(CAPS[orig][1],out)
        self.revision+=1;return copy.deepcopy(out)
    def _deposit(self,lo,hi,speed):
        reverse=hi<lo;lo,hi=min(lo,hi),max(lo,hi)                            # travel towards the back (home reference FRONT): strips in reverse order
        if hi<=lo or not self.charge_cfg or self._volume(self.bead)<=1e-30:return
        c=self.charge_cfg;s=self.sample;dl=s['length_m']/64;cuts=[lo]+[s['start_m']+i*dl for i in range(65) if lo<s['start_m']+i*dl<hi]+[hi]
        left,right=self.gaps;mean=(left+right)/2;cube=(left**3+left**2*right+left*right**2+right**3)/4
        a=.1*mean/2
        for start,end in (reversed(list(zip(cuts,cuts[1:]))) if reverse else zip(cuts,cuts[1:])):
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
    def _archive_op(self):
        # 2026-10-22 (chemlab findings B D4): the final record (status and coating.measure) of a finished operation is kept, so an older operation of a
        # caller stays pollable after a newer one started (poll used to accept only the latest operation)
        op=self.operation_id;arch=self.__dict__.setdefault('_op_final',{})
        if op and self.phase not in ('running',) and op not in arch:arch[op]=dict(status=self.phase,result=self.invoke('coating.measure'))
    def step(self,dt_s):
        validate(number('s',1e-12,1000),dt_s);remaining=float(dt_s)
        while remaining>1e-12:
            dt=min(.02,remaining)
            if self.phase=='running' and self.stage=='delay':                # a delay step of the program: the stage rests
                dt=min(dt,self.wait_left);self.wait_left-=dt;self.elapsed_s+=dt;self.speed=0.
                if self.wait_left<=1e-12:self._begin_step(self.plan_index+1)
            elif self.phase=='running' and self.stage=='stopping':           # SOFT stop: linear deceleration over SOFT_STOP_S
                dt=min(dt,self.stop_left);v0=self.stop_v*self.stop_left/SOFT_STOP_S;self.stop_left-=dt;v1=self.stop_v*max(0.,self.stop_left)/SOFT_STOP_S
                before=self.q;self.q=min(TRAVEL,max(0.,self.q+(v0+v1)/2*dt));self.speed=v1;self.elapsed_s+=dt
                if self.stop_from=='coating' and self.q!=before:self._deposit(before,self.q,abs(v0+v1)/2)
                if self.stop_left<=1e-12:self.phase='cancelled';self.stage='stopped';self.target=self.q;self.speed=0.
            elif self.phase=='running':
                distance=self.target-self.q
                if abs(distance)<1e-12:
                    self.q=self.target;self._arrived();continue
                speed=self.plan[self.plan_index][1] if self.stage=='coating' else .02 if self.stage=='homing' else self.manual_speed
                dt=min(dt,abs(distance)/speed);before=self.q;self.q+=math.copysign(speed*dt,distance);self.speed=math.copysign(speed,distance)
                if self.stage=='coating':self._deposit(before,self.q,speed)
                self.elapsed_s+=dt
                if abs(self.q-self.target)<1e-11:
                    self.q=self.target;self._arrived()
                    if self.phase!='running':self.speed=0.
            else:self.speed=0.
            if self.plant:
                for i in range(2):
                    dx=self.plant['manual_gap_speed_m_s']*dt;self.gaps[i]+=max(-dx,min(dx,self.gap_targets[i]-self.gaps[i]))
            self._evaporate(dt);self.time_s+=dt;remaining-=dt;self._archive_op()
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
        m=export_ossila_doctorblade_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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
            if ALL_CAPS[capability_id][4]!='query':h._archive_op()
            op=result.get('operation_id')
            if op:h._owners[op]=self._principal
            elif capability_id in PANEL_CAPS and h.operation_id and h.operation_id!=before:h._owners[h.operation_id]=self._principal   # a run, homing or jog started by a key belongs to the caller
            response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=op,status='accepted' if op else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None);h._requests[key]=(digest,copy.deepcopy(response));return response
        except ActionRejected as exc:return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,status='rejected',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))
    def write(self,*,instance_id,state_id,value,request_id):
        self._target(instance_id)
        if state_id not in SLOTS or not SLOTS[state_id][5]:raise ActionRejected('ACCESS_DENIED')
        cap,arg=SLOTS[state_id][5];return self.invoke(instance_id=instance_id,capability_id=cap,arguments={arg:value},request_id=request_id)
    def poll(self,operation_id):
        h=self._host
        if h._owners.get(operation_id)!=self._principal:raise ActionRejected('ACCESS_DENIED')
        if operation_id!=h.operation_id:                                    # an older operation of this caller: its final record (2026-10-22)
            rec=h.__dict__.get('_op_final',{}).get(operation_id)
            if rec is None:raise ActionRejected('ACCESS_DENIED')
            return dict(operation_id=operation_id,status=rec['status'],result=copy.deepcopy(rec['result']))
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('coating.measure'))
    def cancel(self,operation_id,request_id):
        r=self.poll(operation_id)
        if operation_id!=self._host.operation_id:return r                 # finished before a newer operation started: nothing to cancel
        return self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)



def export_ossila_doctorblade_contract_metadata():
    caps={};slots={}
    for k,(s,u,o,v,c,w) in SLOTS.items():
        slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',
            read_binding=dict(backend='host',artifact_id='runtime',accessor=f'OssilaDoctorbladeSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,
            sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    evolving=['stage.position_m','stage.speed_m_s','stage.homed','gap.left_m','gap.right_m','gap.mean_m','gap.tilt_rad','cycle.phase','cycle.stage','cycle.elapsed_s','truth.ledger']
    for k,(inp,out,c,z,kind,vis,ex,effects) in ALL_CAPS.items():
        asynchronous=k in ['acquisition.start','stage.home','stage.move_to','stage.jog'];query=kind=='query'
        caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
            implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
            binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.ossila_doctorblade.OssilaDoctorbladeSession.invoke',invocation=f'session.invoke(instance_id="doctorblade_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),
            preconditions=[] if query else [dict(description_zh='停止/上电/回零/急停复位/有限料/薄隙Newtonian条件，宿主身份',state_ids=['cycle.phase','power.enabled','stage.homed','safety.latched'],enforced_by='runtime',check='Runtime.invoke guards; Session trusted principal policy')],
            state_effects=[] if query else [dict(state_id=s,timing='immediate',description_zh='事务配置、目标或有限样品附着/释放') for s in effects]+[dict(state_id=s,timing='on_step',description_zh='唯一宿主时钟推进轴、手动调隙代理和有限材料') for s in evolving],
            observes=[],model_effects=dict(mode='none',description_zh='过程存在不等于已验证几何绑定',binding_ids=[]),
            execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',completion_criterion='轴达到目标；不保证干燥/膜质量' if asynchronous else '事务完成，物理时钟未推进',
                completion_state_ids=['cycle.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',retry='same_request_id',
                resources=['host_clock','finite_charge','substrate_attachment','request_cache'],partial_failure_zh='停止/故障保留已沉积材料和挥发，残余液体继续蒸发；不回滚，不提供跨进程幂等'),
            errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','PRECONDITION_FAILED','BUSY','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','REFERENCE_OUT_OF_RANGE','REFERENCE_INCOMPLETE','INVALID_GEOMETRY','RESERVOIR_OVERFLOW','RESERVOIR_EXHAUSTED','MODEL_OUT_OF_RANGE']],
            example_arguments=copy.deepcopy(ex),verification=[],limitations=['无膜厚传感器；急停是教学锁存，不是实机安全认证；调隙需宿主手动行为'],source_ids=['manufacturer_primary','simulation_assumption'])

    mech=dict(component_ids=['motor','carriage','blade','reservoir','substrate','waste_tray'],state_ids=list(slots),meaning_zh='有限浆料/移动刮刀/固定基板/64条带薄隙流/蒸发',
        implementation='lumped',equations=[
          'dq/dt=commanded signed speed; q in [0,.290]m along -Y; no torque/acceleration solver',
          'gap mean=(g_left+g_right)/2; tilt_y=-atan2(g_right-g_left,.1m)',
          'mean_cube=(g_left^3+g_left^2*g_right+g_left*g_right^2+g_right^3)/4',
          'H=bead_volume/reservoir_area; deltaP=rho*g*H; Q/width=U*gap_mean/2+deltaP*mean_cube/(12*mu*contact_length)',
          'dV/dx=-a-b*V; a=width*gap_mean/2; b=width*rho*g*mean_cube/(12*mu*length*U*reservoir_area); exact positive inventory-limited segment integral',
          'film receives overlap strips; outside substrate -> waste; species transfer by bead mass fraction',
          'P_sat=100000*10^(A-B/(T+C)); first library fit whose own temperature interval contains T (NIST Antoine rows first; NIST SRD69 saturation fit, ThermoML article fit or local Clausius-Clapeyron where NIST has no row at that T); never extrapolated',
          'charge.configure_from_reference: liquid properties = library values at 298.15 K (NIST SRD69 / median of ThermoML articles / PubChem HSDB), only at host temperature 298.15+/-0.5 K',
          'evap_kg_s=k_mass*exposed_area*P_sat*M/(R*T)*solvent_mass_fraction, inventory limited',
          'initial_species=bead+film+waste+evaporated+collected; nonvolatile solute evaporation=0'],
        parameters={},source_ids=['manufacturer_primary','nist_vapor_pressure','simulation_assumption'],
        limitations=['平行板Couette-Poiseuille教学近似；g/刀口长度<=.1，Newtonian液体，无自由液面/弯月面/屈服应力/剪切变稀/颗粒/润湿标定。','库内溶剂298.15K真实物性（密度/黏度/表面张力/蒸气压/沸点）只替代宿主假设的液体物性，不等于实测刮涂膜厚；浆料流变仍需宿主实测。','选择100mm刮刀、8mm固定玻璃台；200mm、真空台、加热、注液、气刀没有实现。','程序为分段恒速涂布（20个程序×最多100步：长度+速度，或延时步）；回零参考后端/前端、回零偏移、HARD/SOFT停止按手册9.2（SOFT为0.3 s线性减速，假设）；无加速度/转矩模型。','64条带，不沿宽度解析膜剖面；不建结晶、流平、干燥收缩、挥发冷却或CFD。','理想测微器示值不证明厂家3µm精度；手动调隙为宿主运动学，不是电动执行或力反馈。','急停释放且关开电源才可恢复，需重新回零；未验证硬件断电、制动距离或安全等级。','几何固定100×200mm示例基板；宿主不同基板参数不自动改网格。','废料接收器假设覆盖，不计算废料蒸发；单次装料不可补液。','代理API过滤不等于文件/进程隔离；原始Runtime仅供受信任宿主。'])
    spec=importlib.util.spec_from_file_location('_blade_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann)
    ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'doctor_blade':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'charge.configure':(CHARGE,CHARGE_EXAMPLE),'substrate.configure':(SAMPLE,SAMPLE_EXAMPLE)})
    mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'doctor_blade':mech})



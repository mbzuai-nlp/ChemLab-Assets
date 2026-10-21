"""CHEM-054 manual2.0.A. Finite bath, LLD film, SI host clock; not calibrated."""
from __future__ import annotations
import copy, importlib.util, json, math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SLUG='ossila_dipcoater'; ASSET_TYPE_ID='instrument.'+SLUG; CATALOG_ID='CHEM-054'; PROTOCOL_VERSION='1.0.0'; ASSET_VERSION='1.3.1'
ASSET_DIR=ROOT/'assets/expansion_batch04/t5_automation/ossila_dipcoater'
TEMPLATE_REL='assets/reactions/ossila_dipcoater_film_library/templates.json'
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


SPEED=number('m/s',.00005,.125);POS=number('m',0,.175)
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
PLANT=obj({'temperature_k':number('K',278.15,313.15),'mass_transfer_m_s':number('m/s',0,.1),'clamp_speed_m_s':number('m/s',.001,.2),'accept_uncalibrated_model':BOOL})
PLANT_EXAMPLE=dict(temperature_k=298.15,mass_transfer_m_s=.0002,clamp_speed_m_s=.02,accept_uncalibrated_model=True)
METHOD=obj({'coating_length_m':number('m',.0001,.17),'immersion_speed_m_s':SPEED,'withdrawal_initial_m_s':SPEED,'withdrawal_final_m_s':SPEED,'dwell_s':number('s',0,359999),'dry_s':number('s',0,359999),'drying_gap_m':number('m',0,.05),'cycles':dict(type='integer',minimum=1,maximum=1000,**{'x-unit':'1'})})
METHOD_EXAMPLE=dict(coating_length_m=.04,immersion_speed_m_s=.02,withdrawal_initial_m_s=.002,withdrawal_final_m_s=.004,dwell_s=.3,dry_s=.5,drying_gap_m=.005,cycles=2)
BATH=obj({'solvent_id':TEXT,'solvent_kg':number('kg',1e-6,2),'solute_kg':number('kg',0,.5),'solvent_density_kg_m3':number('kg/m^3',100,3000),'solute_density_kg_m3':number('kg/m^3',100,10000),'viscosity_pa_s':number('Pa*s',.00001,10),'surface_tension_n_m':number('N/m',.001,.5),'cross_section_m2':number('m^2',.001,.02),'bottom_z_m':number('m',.05,.2),'height_m':number('m',.02,.25),'accept_complete_wetting_and_compatibility':BOOL})
BATH_EXAMPLE=dict(solvent_id='ethanol',solvent_kg=.45,solute_kg=.0045,solvent_density_kg_m3=789.,solute_density_kg_m3=1200.,viscosity_pa_s=.0012,surface_tension_n_m=.0223,cross_section_m2=.0072,bottom_z_m=.06,height_m=.14,accept_complete_wetting_and_compatibility=True)
SAMPLE=obj({'width_m':number('m',.005,.1),'length_m':number('m',.005,.175),'thickness_m':number('m',.0001,.008),'clamp_zero_z_m':number('m',.10,.25),'accept_host_attachment':BOOL})
SAMPLE_EXAMPLE=dict(width_m=.025,length_m=.075,thickness_m=.001,clamp_zero_z_m=.145,accept_host_attachment=True)
PRODUCT_FIELDS={loc+'_'+sp+'_kg':number('kg',0) for loc in ('initial','bath','film','solid','evaporated','collected') for sp in ('solvent','solute')}
PRODUCT_FIELDS.update(wet_thickness_m=number('m',0),dry_equivalent_thickness_m=number('m',0),surface_z_m=number('m',0),coated_area_m2=number('m^2',0),film_profile_m=dict(type='array',items=number('m',0),minItems=64,maxItems=64),evidence=TEXT,template_source=TEXT)
PRODUCT=obj(PRODUCT_FIELDS)
RESULT=obj({'phase':PHASE,'stage':TEXT,'position_m':POS,'speed_m_s':number('m/s',-.125,.125),'cycle_index':dict(type='integer',minimum=0,maximum=1000,**{'x-unit':'1'}),'elapsed_s':number('s',0),'evidence':TEXT,'template_source':TEXT})
CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=()):CAPS[k]=(i,o,c,z,kind,vis,ex or {},list(effects))
for key,s,ex,vis,c in [('plant',PLANT,PLANT_EXAMPLE,'evaluator','motor'),('method',METHOD,METHOD_EXAMPLE,'agent','display_digits'),('bath',BATH,BATH_EXAMPLE,'evaluator','bath'),('substrate',SAMPLE,SAMPLE_EXAMPLE,'evaluator','substrate')]:
    cap(key+'.configure',s,obj({'configured':BOOL}),[c],'配置/有限液浴/基片宿主附着','configuration',vis,ex,[key+'.configured']+(['truth.ledger'] if key in ['bath','substrate'] else []))
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['switch_power'],'电源；断电停止且回零标定失效',ex={'enabled':True},effects=['power.enabled','stage.homed','cycle.phase'])
cap('power.toggle',obj({}),obj({'enabled':BOOL}),['switch_power'],'电源开关翻转',effects=['power.enabled','stage.homed','cycle.phase'])
cap('stage.home',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['button_return','motor','carriage'],'受信任宿主时钟驱动回零；不是瞬移',effects=['cycle.phase','stage.target_m'])
cap('stage.move_to',obj({'position_m':POS,'speed_m_s':SPEED}),obj({'phase':PHASE,'operation_id':TEXT}),['carriage','motor'],'手动模式有限行程规定运动',ex={'position_m':.10,'speed_m_s':.02},effects=['cycle.phase','stage.target_m'])
cap('clamp.set_gap',obj({'gap_m':number('m',0,.012)}),obj({'gap_m':number('m',0,.012)}),['clamp_jaw'],'宿主手动夹口目标；已附着基片不可直接打开',ex={'gap_m':.012},effects=['clamp.target_m'])
cap('safety.set_crash',obj({'triggered':BOOL}),obj({'triggered':BOOL}),['crash_switch'],'宿主输入防撞开关事件，非真实接触感知','simulation','evaluator',{'triggered':True},['safety.crash','safety.fault','cycle.phase'])
cap('fault.acknowledge',obj({}),obj({'acknowledged':BOOL}),['crash_switch'],'障碍开关复位后确认故障',effects=['safety.fault','cycle.phase'])
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['button_start','motor','carriage'],'浸入/停留/变速提拉/干燥/循环',effects=['cycle.phase','cycle.index','cycle.elapsed_s','stage.target_m'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['button_stop','motor'],'立即停止教学运动，保留材料部分效果',effects=['cycle.phase','stage.target_m'])
cap('coating.measure',obj({}),RESULT,['display_digits'],'理想位移/速度/循环面板摘要，非膜厚测量','query')
cap('product.read',obj({}),PRODUCT,['substrate','bath'],'有限浴/分段液膜/固体/蒸发评价账本','query','evaluator')
cap('product.collect',obj({'recipient_id':TEXT}),obj({'recipient_id':TEXT,'attachment_event':TEXT}),['substrate','clamp_jaw'],'露出液面且停止后的宿主释放事务','device_command','evaluator',{'recipient_id':'output_slide'},['substrate.configured','truth.ledger','clamp.target_m'])
cap('reference.lookup',obj({'substance_id':TEXT,'temperature_k':number('K',1)}),obj({'vapor_pressure_pa':number('Pa',0),'evidence':TEXT,'source':TEXT,'template_source':TEXT}),['bath'],'NIST原始温区蒸气压参考','query','agent',{'substance_id':'ethanol','temperature_k':298.15})
REF_T=298.15;REF_TOL=.5
FIT_EVIDENCE={'nist_srd69_saturation_fit':'derived_NIST_SRD69_saturation_Antoine_fit_no_extrapolation','thermoml_vapour_pressure_fit':'derived_ThermoML_article_Antoine_fit_no_extrapolation','clausius_clapeyron_local':'derived_local_Clausius_Clapeyron_PubChem_point_NIST_dHvap_no_extrapolation'}
PROPS_OUT=obj({'temperature_k':number('K',0),'density_kg_m3':number('kg/m^3',0),'viscosity_pa_s':number('Pa*s',0),'surface_tension_n_m':number('N/m',0),'vapor_pressure_pa':number('Pa',0),'normal_boiling_point_k':number('K',0),'molar_mass_kg_mol':number('kg/mol',0),'evidence':TEXT,'source':TEXT,'template_source':TEXT})
cap('reference.properties',obj({'substance_id':TEXT}),PROPS_OUT,['bath'],'298.15K真实纯溶剂物性（密度/黏度/表面张力/蒸气压/沸点；NIST SRD69、ThermoML实测中位数、PubChem），记录不完整则拒绝','query','agent',{'substance_id':'toluene'})
BATH_REF=obj({k:v for k,v in BATH['properties'].items() if k not in ('solvent_density_kg_m3', 'viscosity_pa_s', 'surface_tension_n_m')})
BATH_REF_EXAMPLE=dict({k:v for k,v in BATH_EXAMPLE.items() if k in BATH_REF['properties']},solvent_id='toluene')
cap('bath'+'.configure_from_reference',BATH_REF,obj(dict({k:number(BATH['properties'][k]['x-unit'],0) for k in ('solvent_density_kg_m3', 'viscosity_pa_s', 'surface_tension_n_m')},configured=BOOL,evidence=TEXT)),['bath'],'液浴溶剂密度/黏度/表面张力取自库内298.15K真实物性（LLD用）；宿主温度须为298.15±0.5K','configuration','evaluator',BATH_REF_EXAMPLE,['bath.configured', 'truth.ledger'])
# front panel 1.2.0 (manual 8.2-8.4): Settings (coating length, ruler positions, Advanced), Manual (speeds, jog, Free Motion), Program mode with 20
# stored programs; the selected program plus the coating length and drying gap compose the method that runs
PROGRAMS=20
SETTINGS_DEFAULT=dict(coating_length_m=.05,drying_gap_m=.005,substrate_position_m=None,solution_height_m=None,speed_unit='mm/s',default_page='settings',
                      default_program='last',brightness_pct=70,manual_immersion_speed_m_s=.01,manual_withdrawal_speed_m_s=.01,free_speed_m_s=.01)
PROGRAM_DEFAULT=dict(cycles=1,immersion_speed_m_s=.01,dwell_s=0.,withdrawal_initial_m_s=.001,withdrawal_final_m_s=.001,dry_s=0.)
RULER=number('m',0,.3)
def _nul(x):return {'anyOf':[x,{'type':'null'}]}
SETTINGS_IN=dict(type='object',properties=dict(coating_length_m=number('m',.0001,.175),drying_gap_m=number('m',0,.05),substrate_position_m=RULER,solution_height_m=RULER,
                 speed_unit=dict(type='string',enum=['mm/s','mm/min']),default_page=dict(type='string',enum=['settings','program','manual']),
                 default_program=dict(type='string',enum=['last','first']),brightness_pct=dict(type='integer',minimum=0,maximum=100,**{'x-unit':'1'}),
                 manual_immersion_speed_m_s=SPEED,manual_withdrawal_speed_m_s=SPEED,free_speed_m_s=SPEED),required=[],additionalProperties=False)
SETTINGS_VIEW=obj(dict(coating_length_m=number('m',0,.175),drying_gap_m=number('m',0,.05),substrate_position_m=_nul(RULER),solution_height_m=_nul(RULER),speed_unit=TEXT,
                       default_page=TEXT,default_program=TEXT,brightness_pct=dict(type='integer',minimum=0,maximum=100,**{'x-unit':'1'}),manual_immersion_speed_m_s=SPEED,
                       manual_withdrawal_speed_m_s=SPEED,free_speed_m_s=SPEED,warning=_nul(TEXT)))
PROGRAM_FIELDS=dict(cycles=dict(type='integer',minimum=1,maximum=1000,**{'x-unit':'1'}),immersion_speed_m_s=SPEED,dwell_s=number('s',0,359999),withdrawal_initial_m_s=SPEED,
                    withdrawal_final_m_s=SPEED,dry_s=number('s',0,359999))
PROGRAM_VIEW=obj(dict(program=dict(type='integer',minimum=1,maximum=PROGRAMS,**{'x-unit':'1'}),last_run=dict(type='integer',minimum=1,maximum=PROGRAMS,**{'x-unit':'1'}),**PROGRAM_FIELDS))
PROGRAM_CAPS={}
def pcap(k,i,o,c,z,ex,effects,kind='configuration'):PROGRAM_CAPS[k]=(i,o,c,z,kind,'agent',ex,effects)
pcap('settings.configure',SETTINGS_IN,SETTINGS_VIEW,['display_digits'],'面板设定：涂覆长度（与干燥间隙之和超过175 mm时自动修正并警告）、基片位置与液面高度（标尺读数；两者都设后按其计算浸入行程）、单位、默认页/程序、亮度、手动与自由速度',
     {'coating_length_m':.05},['settings.state','method.configured'])
pcap('program.select',obj({'program':dict(type='integer',minimum=1,maximum=PROGRAMS,**{'x-unit':'1'})}),PROGRAM_VIEW,['display_digits'],'选择程序1–20（所选程序与涂覆长度、干燥间隙组成运行的方法）',
     {'program':2},['programs.state','method.configured'])
pcap('program.configure',dict(type='object',properties=PROGRAM_FIELDS,required=[],additionalProperties=False),PROGRAM_VIEW,['display_digits'],
     '所选程序的循环数、浸入速度、停留时间、初始/最终提拉速度与干燥时间',{'cycles':5,'immersion_speed_m_s':.065},['programs.state','method.configured'])
pcap('stage.jog',obj({'direction':dict(type='string',enum=['up','down']),'duration_s':number('s',.02,60),'mode':dict(type='string',enum=['manual','free'])}),
     obj({'phase':PHASE,'operation_id':TEXT}),['carriage','motor'],'手动模式点动：按住方向键的时间×设定速度（手动：上=提拉速度、下=浸入速度；自由运动：自由速度），限175 mm行程',
     {'direction':'up','duration_s':1.,'mode':'manual'},['cycle.phase','stage.target_m'],'device_command')
# 1.3.0 (sync audit): the troubleshooting table's "System error" (manual p.34-35: the secondary safety switch was triggered) through an evaluator
# fault injection; the error stays on the screen until the cause is removed (the arm moved towards the centre of travel by hand, or the switch
# repaired: fault.clear) and the unit is switched off and on again
FAULT_CAPS={}
def fcap(k,i,o,c,z,ex,effects):FAULT_CAPS[k]=(i,o,c,z,'simulation','evaluator',ex,effects)
fcap('fault.inject',obj({'code':dict(type='string',enum=['system_error'])}),obj({'code':TEXT,'phase':PHASE}),['motor','display_digits'],
     '评测者注入手册故障排除表的System error（次级安全开关被触发，p.34-35）：运动立即停止（运行失败），屏幕显示System error，回零/移动/点动/启动被拒，直到原因排除（fault.clear）且关开电源',
     {'code':'system_error'},['safety.system_error','truth.secondary_switch','cycle.phase'])
fcap('fault.clear',obj({}),obj({'cause':_nul(TEXT)}),['motor'],'评测者：原因已排除（手动把浸渍臂移向行程中部，或次级安全开关已修复）；System error仍须关开电源才消失（手册p.35）',{},['truth.secondary_switch'])

def _panel_engine():
    try:
        from . import _panel_ui as engine                                   # imported as asset_runtime.ossila_dipcoater
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
for key in ['plant','method','bath','substrate']:slot(key+'.configured',BOOL,'1',key+'配置或存在标记','setpoint')
slot('power.enabled',BOOL,'1','电源开关','setpoint',c='switch_power',w=('power.set','enabled'))
slot('stage.homed',BOOL,'1','本次通电已回零；断电失效',c='carriage')
slot('stage.target_m',POS,'m','上升位置目标相对机械低位','setpoint',c='carriage')
slot('stage.position_m',POS,'m','运动学轴位移，正向向上，理想位置示值','scene',c='carriage')
slot('stage.speed_m_s',number('m/s',-.125,.125),'m/s','正上负下理想轴速度',c='display_digits')
slot('clamp.target_m',number('m',0,.012),'m','手动夹具开度目标','setpoint',c='clamp_jaw',w=('clamp.set_gap','gap_m'))
slot('clamp.gap_m',number('m',0,.012),'m','夹口位移；数值代理非夹持力','scene',c='clamp_jaw')
slot('safety.crash',BOOL,'1','宿主输入防撞开关或几何底碰事件','scene',c='crash_switch')
slot('safety.fault',BOOL,'1','停止故障锁存',c='crash_switch')
slot('cycle.phase',PHASE,'1','程序/手动/回零操作状态',c='display_digits')
slot('cycle.stage',TEXT,'1','浸入/停留/抽离/干燥等阶段',c='display_digits')
slot('cycle.index',dict(type='integer',minimum=0,maximum=1000,**{'x-unit':'1'}),'1','当前循环编号',c='display_digits')
slot('cycle.elapsed_s',number('s',0),'s','当前操作的宿主累计时间',c='display_digits')
slot('truth.ledger',PRODUCT,'1','有限浴、64段双面膜、干固体和环境账本；不提供给代理','process_truth','evaluator','substrate')
slot('settings.state',SETTINGS_VIEW,'1','面板设定（涂覆长度、干燥间隙、标尺上的基片位置与液面高度、单位、默认页/程序、亮度、手动/自由速度）与最近的设定警告','setpoint',c='display_digits')
slot('programs.state',PROGRAM_VIEW,'1','面板所选程序（1–20）及其参数、上次运行的程序','setpoint',c='display_digits')
slot('safety.system_error',BOOL,'1','屏幕上的System error（次级安全开关触发，手册p.34）；原因排除后关开电源才消失',c='display_digits')
slot('truth.secondary_switch',BOOL,'1','次级安全开关仍被触发（评测者注入的原因；fault.clear排除前每次开机都显示System error）','process_truth','evaluator','motor')

class OssilaDipcoaterRuntime:
    """A trusted host owns time, attachments and collision events. No device truth leaks."""
    def __init__(self,instance_id='dipcoater_01'):
        self.instance_id=instance_id;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']}
        self.time_s=0.;self.revision=0;self.powered=False;self.homed=False;self.plant=None;self.method=None;self.bath_cfg=None;self.sample=None;self.sample_present=False
        self.q=.175;self.target=.175;self.speed=0.;self.manual_speed=.02;self.gap=0.;self.gap_target=0.;self.crash=False;self.fault=False
        self.phase='idle';self.stage='idle';self.cycle_index=0;self.elapsed_s=0.;self.wait_elapsed=0.;self.withdraw_start=0.;self.coated_area=0.
        self.initial=[0.,0.];self.bath=[0.,0.];self.evaporated=[0.,0.];self.collected=[0.,0.]
        self.film=[[0.,0.] for _ in range(64)];self.solid=[0.]*64;self.submerged=[0.]*64;self.evap_flux=0.
        self.operation_id='';self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
        self.settings=dict(SETTINGS_DEFAULT,warning=None);self.programs={str(n):dict(PROGRAM_DEFAULT) for n in range(1,PROGRAMS+1)};self.program_no=1;self.last_run=1
        self.power_epoch=0;self.panel_ui=None;self.panel_capture_dir=None;_Panel(self)
        self.system_error=False;self.secondary=False   # 1.3.0: System error on the screen / secondary safety switch still triggered
        self._caller=None   # principal of the session call in progress (set by OssilaDipcoaterSession.invoke; panel keys act as that caller)
    @property
    def panel(self):return _Panel(self)
    # ---- panel settings and program store (manual 8.4): the selected program + coating length + drying gap compose the method
    def _compose(self):
        p=self.programs[str(self.program_no)];st=self.settings
        m=dict(coating_length_m=st['coating_length_m'],immersion_speed_m_s=p['immersion_speed_m_s'],withdrawal_initial_m_s=p['withdrawal_initial_m_s'],
               withdrawal_final_m_s=p['withdrawal_final_m_s'],dwell_s=p['dwell_s'],dry_s=p['dry_s'],drying_gap_m=st['drying_gap_m'],cycles=p['cycles'])
        try:validate(METHOD,m);self.method=m
        except ActionRejected:self.method=None
    def _program_view(self):return dict(program=self.program_no,last_run=self.last_run,**copy.deepcopy(self.programs[str(self.program_no)]))
    def _settings_warning(self):
        st=self.settings;pos,sol=st['substrate_position_m'],st['solution_height_m']
        if pos is None or sol is None:return None
        if pos<sol:return 'below_solution'                                   # manual 8.4.2 note: substrate position below the solution height
        if pos-sol+st['coating_length_m']+st['drying_gap_m']>.175+1e-12:return 'travel_position'
        return None
    # ---- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
    def panel_read(self):
        v=self.read('agent');v.update({'host.powered':self.powered,'host.power_epoch':self.power_epoch,'host.coating':self.phase=='running' and self.stage not in ('homing','manual','idle')})
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
        return OssilaDipcoaterSession(self,principal_id)
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
    def _volume(self,masses):
        b=self.bath_cfg
        return masses[0]/b['solvent_density_kg_m3']+masses[1]/b['solute_density_kg_m3'] if b else 0.
    def _bottom(self,q=None):return self.sample['clamp_zero_z_m']+(self.q if q is None else q)-self.sample['length_m']
    def _surface(self,q=None):
        if not self.bath_cfg:return 0.
        b=self.bath_cfg;area=b['cross_section_m2'];vol=self._volume(self.bath);z=b['bottom_z_m'];level=z+vol/area
        if not self.sample_present or level<=self._bottom(q):return level
        footprint=self.sample['width_m']*self.sample['thickness_m'];length=self.sample['length_m'];bottom=self._bottom(q)
        high=z+(vol+footprint*length)/area
        return high if high>=bottom+length else (vol+area*z-footprint*bottom)/(area-footprint)
    def _fractions(self):
        if not self.sample_present:return [0.]*64
        dl=self.sample['length_m']/64.;height=self._surface()-self._bottom()
        return [max(0.,min(1.,height/dl-i)) for i in range(64)]
    def _lld(self,speed):
        b=self.bath_cfg;ca=b['viscosity_pa_s']*abs(speed)/b['surface_tension_n_m']
        if ca>.02+1e-15:raise ActionRejected('MODEL_OUT_OF_RANGE')
        volume=self._volume(self.bath);rho=sum(self.bath)/max(volume,1e-30)
        if rho<=0:raise ActionRejected('BATH_EXHAUSTED')
        return .94*math.sqrt(b['surface_tension_n_m']/(rho*9.80665))*ca**(2/3)
    def _targets(self):
        s=self.sample;m=self.method;zero=s['clamp_zero_z_m']-s['length_m']
        pos,sol=self.settings['substrate_position_m'],self.settings['solution_height_m']
        if pos is not None and sol is not None:                               # 1.2.0: the ruler readings entered on the panel (substrate bottom at the top position)
            return .175-(pos-sol+m['coating_length_m']),.175-(pos-sol)+m['drying_gap_m']
        low=self._surface()-zero-m['coating_length_m']
        # Exact bath-surface/displacement fixed point; positive footprint << bath area.
        for _ in range(8):low=self._surface(low)-zero-m['coating_length_m']
        high=self._surface()-zero+m['drying_gap_m']
        return low,high
    def _product(self):
        films=[sum(f[i] for f in self.film) for i in range(2)];solid=[0.,sum(self.solid)]
        p={loc+'_'+sp+'_kg':m[i] for loc,m in [('initial',self.initial),('bath',self.bath),('film',films),('solid',solid),('evaporated',self.evaporated),('collected',self.collected)] for i,sp in enumerate(['solvent','solute'])}
        area=2*self.sample['width_m']*self.sample['length_m'] if self.sample else 1.;rho=self.bath_cfg['solute_density_kg_m3'] if self.bath_cfg else 1.
        profile=[self._volume(f)/(area/64)+v/rho/(area/64) for f,v in zip(self.film,self.solid)]
        p.update(wet_thickness_m=sum(profile)/64,dry_equivalent_thickness_m=(films[1]+solid[1])/rho/area,surface_z_m=self._surface(),coated_area_m2=self.coated_area,film_profile_m=profile,
            evidence='synthetic_64strip_LLD_complete_wetting;real_NIST_reference;NOT_measured_thickness',template_source=TEMPLATE_REL);return p
    def read(self,visibility='agent'):
        if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
        v={'settings.state':copy.deepcopy(self.settings),'programs.state':self._program_view(),'plant.configured':self.plant is not None,'method.configured':self.method is not None,'bath.configured':self.bath_cfg is not None,'substrate.configured':self.sample_present,'power.enabled':self.powered,'stage.homed':self.homed,'stage.target_m':self.target,'stage.position_m':self.q,'stage.speed_m_s':self.speed,'clamp.target_m':self.gap_target,'clamp.gap_m':self.gap,'safety.crash':self.crash,'safety.fault':self.fault,'safety.system_error':self.system_error,'truth.secondary_switch':self.secondary,'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.index':self.cycle_index,'cycle.elapsed_s':self.elapsed_s,'truth.ledger':self._product()}
        return copy.deepcopy({k:x for k,x in v.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
    def _foreign_run(self):
        """An agent may not stop (Stop key, acquisition.stop) or switch off (power switch, power.set / power.toggle) another agent's running operation (program,
        homing, jog); the same check for the direct call and the panel key, because both run with the session caller in _caller.  Unowned operations and
        the evaluator are not restricted."""
        c=self._caller;o=self._owners.get(self.operation_id)
        return self.phase=='running' and c is not None and self._principals.get(c)=='agent' and o is not None and o!=c
    def _stop(self,result='cancelled'):
        if self.phase=='running':self.phase=result;self.stage='stopped';self.target=self.q;self.speed=0.
    def _new_operation(self,stage,target):
        self.phase='running';self.stage=stage;self.target=target;self.elapsed_s=0.;self.wait_elapsed=0.;self._operation_count+=1
        self.operation_id=self.instance_id+':motion:'+str(self._operation_count);return dict(phase=self.phase,operation_id=self.operation_id)
    def invoke(self,capability,**a):
        if capability not in ALL_CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(ALL_CAPS[capability][0],a);orig=capability;used=None
        if capability=='panel.frame':return {'frame':self.panel.frame()}
        if capability=='panel.capture':return self._capture(a['display_id'])
        if capability=='panel.press':out=self._gesture(self.panel.press,a['input_id'],float(a.get('duration_s',.1)));self.revision+=1;return out
        if capability=='panel.switch':out=self._gesture(self.panel.switch,a['input_id'],a['position']);self.revision+=1;return out
        if capability=='bath.configure_from_reference':
            if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
            if abs(self.plant['temperature_k']-REF_T)>REF_TOL:raise ActionRejected('REFERENCE_OUT_OF_RANGE')
            props=self._properties(a['solvent_id']);fill={'solvent_density_kg_m3':props['density_kg_m3'],'viscosity_pa_s':props['viscosity_pa_s'],'surface_tension_n_m':props['surface_tension_n_m']}
            used=dict(fill,evidence='library_298.15K:'+props['evidence']);a=dict(a,**fill);validate(BATH,a);capability='bath.configure'
        if (capability.endswith('.configure') or capability=='program.select') and self.phase=='running':raise ActionRejected('BUSY')
        if capability=='plant.configure':
            if self.plant or not a['accept_uncalibrated_model']:raise ActionRejected('PRECONDITION_FAILED')
            self.plant=copy.deepcopy(a);out={'configured':True}
        elif capability=='method.configure':
            self.method=copy.deepcopy(a);self.settings.update(coating_length_m=a['coating_length_m'],drying_gap_m=a['drying_gap_m'])   # also stored as the panel shows it
            self.programs[str(self.program_no)].update({k:a[k] for k in ('cycles','immersion_speed_m_s','dwell_s','withdrawal_initial_m_s','withdrawal_final_m_s','dry_s')});out={'configured':True}
        elif capability=='settings.configure':
            new=dict(self.settings,**a)
            if 'brightness_pct' in a and a['brightness_pct']%5:raise ActionRejected('INVALID_ARGUMENT')            # manual 8.4.2: steps of 5
            new['warning']=None
            if new['coating_length_m']+new['drying_gap_m']>.175+1e-12:new['coating_length_m']=round(.175-new['drying_gap_m'],9);new['warning']='coating_corrected'   # auto-corrected
            self.settings=new;self.settings['warning']=self.settings['warning'] or self._settings_warning();self._compose();out=copy.deepcopy(self.settings)
        elif capability=='program.select':
            self.program_no=a['program'];self._compose();out=self._program_view()
        elif capability=='program.configure':
            self.programs[str(self.program_no)].update(a);self._compose();out=self._program_view()
        elif capability=='bath.configure':
            if not self.plant or self.bath_cfg or self.sample_present or not a['accept_complete_wetting_and_compatibility']:raise ActionRejected('PRECONDITION_FAILED')
            pressure=self._reference(a['solvent_id'],self.plant['temperature_k'])['vapor_pressure_pa']
            volume=a['solvent_kg']/a['solvent_density_kg_m3']+a['solute_kg']/a['solute_density_kg_m3']
            if volume>.8*a['cross_section_m2']*a['height_m']:raise ActionRejected('BATH_OVERFLOW')
            self.bath_cfg=copy.deepcopy(a);self.bath=[a['solvent_kg'],a['solute_kg']];self.initial=self.bath.copy()
            self.evap_flux=self.plant['mass_transfer_m_s']*pressure*self.templates[a['solvent_id']]['molar_mass_kg_mol']/(8.314462618*self.plant['temperature_k']);out={'configured':True}
        elif capability=='substrate.configure':
            if not all([self.plant,self.bath_cfg,self.homed,a['accept_host_attachment']]) or self.sample is not None or abs(self.q-.175)>1e-8 or self.gap<.009:raise ActionRejected('PRECONDITION_FAILED')
            if a['width_m']*a['thickness_m']>=.5*self.bath_cfg['cross_section_m2'] or a['clamp_zero_z_m']+.175-a['length_m']<=self._surface():raise ActionRejected('INVALID_GEOMETRY')
            self.sample=copy.deepcopy(a);self.sample_present=True;out={'configured':True}
        elif capability in ('power.set','power.toggle'):
            # lead rule 10-19: the rear power switch (power.set / power.toggle model it) is a hard switch - physical, never refused (a power loss)
            was=self.powered;self.powered=a['enabled'] if capability=='power.set' else not self.powered
            if not self.powered:self._stop();self.homed=False
            if was and not self.powered and self.settings['drying_gap_m']!=.005:   # manual 8.4.2: the drying gap resets to 5 mm when switched off
                self.settings['drying_gap_m']=.005;self._compose() if self.method is not None else None
            if self.powered and not was:
                self.power_epoch+=1;self.system_error=self.secondary          # manual p.35: switch off and on to see if the warning remains
                if self.settings['default_program']=='first' and self.program_no!=1:self.program_no=1;self._compose() if self.method is not None else None
            out={'enabled':self.powered}
        elif capability=='clamp.set_gap':
            if not self.plant or self.phase=='running' or (self.sample_present and a['gap_m']>self.sample['thickness_m']):raise ActionRejected('PRECONDITION_FAILED')
            self.gap_target=a['gap_m'];out=a.copy()
        elif capability=='safety.set_crash':
            self.crash=a['triggered']
            if self.crash:self.fault=True;self._stop('failed')
            out=a.copy()
        elif capability=='fault.inject':
            self.secondary=True;self.system_error=True;self._stop('failed');out={'code':a['code'],'phase':self.phase}
        elif capability=='fault.clear':
            out={'cause':'system_error' if self.secondary else None};self.secondary=False
        elif capability=='fault.acknowledge':
            if self.crash or self.phase=='running':raise ActionRejected('PRECONDITION_FAILED')
            self.fault=False;self.phase='idle';out={'acknowledged':True}
        elif capability=='stage.jog':
            if not self.plant or not self.powered or self.crash or self.fault or self.system_error or self.phase=='running' or not self.homed:raise ActionRejected('PRECONDITION_FAILED')
            st=self.settings;up=a['direction']=='up'
            speed=st['free_speed_m_s'] if a['mode']=='free' else st['manual_withdrawal_speed_m_s' if up else 'manual_immersion_speed_m_s']
            if self.sample_present:self._lld(speed)
            self.manual_speed=speed;out=self._new_operation('manual',min(.175,max(0.,self.q+(speed if up else -speed)*a['duration_s'])))
        elif capability in ['stage.home','stage.move_to','acquisition.start']:
            if not self.plant or not self.powered or self.crash or self.fault or self.system_error or self.phase=='running':raise ActionRejected('PRECONDITION_FAILED')
            if capability=='stage.home':
                if self.sample_present:raise ActionRejected('PRECONDITION_FAILED')
                out=self._new_operation('homing',.175)
            elif capability=='stage.move_to':
                if not self.homed:raise ActionRejected('PRECONDITION_FAILED')
                if self.sample_present:self._lld(a['speed_m_s'])
                self.manual_speed=a['speed_m_s'];out=self._new_operation('manual',a['position_m'])
            else:
                if not all([self.method,self.bath_cfg,self.sample_present,self.homed]) or self.gap>self.sample['thickness_m']+1e-9 or self.gap_target>self.sample['thickness_m']+1e-9:raise ActionRejected('PRECONDITION_FAILED')
                lo,hi=self._targets();b=self.bath_cfg;m=self.method
                if not 0<=lo<hi<=.175 or m['coating_length_m']>self.sample['length_m'] or self._bottom(lo)<=b['bottom_z_m']+.001:raise ActionRejected('INVALID_GEOMETRY')
                if self.bath[0]<=1e-12:raise ActionRejected('BATH_EXHAUSTED')
                self._lld(max(m['withdrawal_initial_m_s'],m['withdrawal_final_m_s']))
                self.cycle_index=1;self.last_run=self.program_no;out=self._new_operation('immersing',lo)
        elif capability=='acquisition.stop':
            if self._foreign_run():raise ActionRejected('ACCESS_DENIED')
            self._stop();out={'phase':self.phase}
        elif capability=='coating.measure':return dict(phase=self.phase,stage=self.stage,position_m=self.q,speed_m_s=self.speed,cycle_index=self.cycle_index,elapsed_s=self.elapsed_s,evidence='ideal_panel_proxy_not_thickness_sensor',template_source=TEMPLATE_REL)
        elif capability=='product.read':return self._product()
        elif capability=='reference.lookup':return self._reference(a['substance_id'],a['temperature_k'])
        elif capability=='reference.properties':return self._properties(a['substance_id'])
        elif capability=='product.collect':
            if not self.sample_present or self.phase=='running' or self._bottom()<self._surface()+.0001:raise ActionRejected('PRECONDITION_FAILED')
            self.collected[0]+=sum(f[0] for f in self.film);self.collected[1]+=sum(f[1] for f in self.film)+sum(self.solid)
            self.film=[[0.,0.] for _ in range(64)];self.solid=[0.]*64;self.sample_present=False;self.gap_target=.012
            out=dict(recipient_id=a['recipient_id'],attachment_event='host_release_substrate')
        else:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(ALL_CAPS[capability][1],out)
        if used is not None:out=dict(out,**used);validate(CAPS[orig][1],out)
        self.revision+=1;return copy.deepcopy(out)

    def _film_step(self,dt,up_speed):
        if not self.bath_cfg:return
        b=self.bath_cfg
        if self.sample_present:
            s=self.sample;fractions=self._fractions();area=2*s['width_m']*s['length_m']/64.
            # Only wet material re-enters on immersion; dry deposit is an explicitly insoluble-layer assumption.
            for i,(previous,current) in enumerate(zip(self.submerged,fractions)):
                if current>previous and previous<1:
                    fraction=min(1.,(current-previous)/max(1e-15,1-previous))
                    for species in range(2):
                        mass=self.film[i][species]*fraction;self.film[i][species]-=mass;self.bath[species]+=mass
            if up_speed>0 and sum(self.bath)>1e-20:
                thickness=self._lld(up_speed)
                for i,(previous,current) in enumerate(zip(self.submerged,fractions)):
                    newly_dry=max(0.,previous-current);vol=area*newly_dry*thickness
                    available=self._volume(self.bath);fraction=min(1.,vol/max(1e-30,available))
                    if newly_dry>0:
                        for species in range(2):
                            mass=self.bath[species]*fraction;self.bath[species]-=mass;self.film[i][species]+=mass
                        self.coated_area+=area*newly_dry
            self.submerged=fractions
            for i,f in enumerate(self.film):
                exposed=1-fractions[i];solvent_fraction=f[0]/max(sum(f),1e-30)
                ev=min(f[0],self.evap_flux*area*exposed*solvent_fraction*dt);f[0]-=ev;self.evaporated[0]+=ev
                if f[0]<=1e-18:
                    self.evaporated[0]+=f[0];f[0]=0.;self.solid[i]+=f[1];f[1]=0.
        bath_area=b['cross_section_m2']
        if self.sample_present and 0<sum(self.submerged)<64:bath_area-=self.sample['width_m']*self.sample['thickness_m']
        ev=min(self.bath[0],self.evap_flux*bath_area*self.bath[0]/max(sum(self.bath),1e-30)*dt)
        self.bath[0]-=ev;self.evaporated[0]+=ev
    def _next_stage(self):
        if self.stage=='homing':self.homed=True;self.phase='succeeded';self.stage='idle'
        elif self.stage=='manual':self.phase='succeeded';self.stage='idle'
        elif self.stage=='immersing':self.stage='dwelling';self.wait_elapsed=0.
        elif self.stage=='dwelling':self.stage='withdrawing';self.withdraw_start=self.q;self.target=self._targets()[1]
        elif self.stage=='withdrawing':self.stage='drying';self.wait_elapsed=0.
        elif self.stage=='drying':
            if self.cycle_index>=self.method['cycles']:self.phase='succeeded';self.stage='idle'
            else:self.cycle_index+=1;self.stage='immersing';self.target=self._targets()[0]
        self.speed=0.
    def step(self,dt_s):
        validate(number('s',1e-12,1000),dt_s);left=float(dt_s)
        while left>1e-12:
            dt=min(.01,left);speed=0.;moving=False
            if self.phase=='running':
                if not self.powered or self.fault:self._stop('failed')
                elif self.stage in ['dwelling','drying']:
                    limit=self.method['dwell_s' if self.stage=='dwelling' else 'dry_s']
                    if self.wait_elapsed>=limit-1e-12:self._next_stage();continue
                    dt=min(dt,limit-self.wait_elapsed)
                else:
                    moving=True
                    if self.stage=='immersing':self.target=self._targets()[0];speed=self.method['immersion_speed_m_s']
                    elif self.stage=='withdrawing':
                        self.target=self._targets()[1];m=self.method;fraction=max(0.,min(1.,(self.q-self.withdraw_start)/max(1e-15,self.target-self.withdraw_start)))
                        speed=m['withdrawal_initial_m_s']+(m['withdrawal_final_m_s']-m['withdrawal_initial_m_s'])*fraction
                    else:speed=.02 if self.stage=='homing' else self.manual_speed
                    if not 0<=self.target<=.175 or (self.bath_cfg and self.bath[0]<=1e-16):
                        self.fault=True;self._stop('failed');continue
                    distance=self.target-self.q
                    if abs(distance)<1e-12:self._next_stage();continue
                    speed=math.copysign(speed,distance);dt=min(dt,abs(distance)/abs(speed))
            was_running=self.phase=='running';before=self.q
            if self.plant:
                gap_step=self.plant['clamp_speed_m_s']*dt;self.gap+=max(-gap_step,min(gap_step,self.gap_target-self.gap))
            if was_running and moving:
                next_q=self.q+speed*dt
                if self.sample_present and self.bath_cfg and self._bottom(next_q)<self.bath_cfg['bottom_z_m']+.001:
                    next_q=self.bath_cfg['bottom_z_m']+.001-self.sample['clamp_zero_z_m']+self.sample['length_m']
                    self.crash=True;self.fault=True;self._stop('failed')
                self.q=max(0.,min(.175,next_q));self.speed=(self.q-before)/dt if self.phase=='running' else 0.
            else:self.speed=0.
            self._film_step(dt,max(0.,(self.q-before)/dt))
            if was_running:
                self.elapsed_s+=dt
                if self.phase=='running' and self.stage in ['dwelling','drying']:self.wait_elapsed+=dt
                if self.phase=='running' and moving and abs(self.q-self.target)<1e-11:self._next_stage()
            self.time_s+=dt;left-=dt
        self.revision+=1;return self.read('evaluator')

class OssilaDipcoaterSession:
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
        m=export_ossila_dipcoater_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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
            elif capability_id in PANEL_CAPS and h.operation_id and h.operation_id!=before:h._owners[h.operation_id]=self._principal   # a run, homing or jog started by a key belongs to the caller
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



def export_ossila_dipcoater_contract_metadata():
    caps={};slots={}
    for k,(s,u,o,v,c,w) in SLOTS.items():
        slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',
            read_binding=dict(backend='host',artifact_id='runtime',accessor=f'OssilaDipcoaterSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,
            sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    evolving=['stage.position_m','stage.speed_m_s','stage.target_m','stage.homed','clamp.gap_m','cycle.phase','cycle.stage','cycle.index','cycle.elapsed_s','truth.ledger','safety.crash','safety.fault']
    for k,(inp,out,c,z,kind,vis,ex,effects) in ALL_CAPS.items():
        asynchronous=k in ['acquisition.start','stage.home','stage.move_to','stage.jog'];query=kind=='query'
        caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
            implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
            binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.ossila_dipcoater.OssilaDipcoaterSession.invoke',invocation=f'session.invoke(instance_id="dipcoater_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),
            preconditions=[] if query else [dict(description_zh='停止/上电/回零/夹持/有限浴/合法几何/低Ca条件，宿主身份',state_ids=['cycle.phase','power.enabled','stage.homed','safety.fault'],enforced_by='runtime',check='Runtime.invoke guards; Session trusted principal policy')],
            state_effects=[] if query else [dict(state_id=s,timing='immediate',description_zh='事务配置、目标或有限样品附着/释放') for s in effects]+[dict(state_id=s,timing='on_step',description_zh='唯一宿主时钟推进轴、夹口、液面和有限材料') for s in evolving],
            observes=[],model_effects=dict(mode='none',description_zh='过程存在不等于已验证几何绑定',binding_ids=[]),
            execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',completion_criterion='轴达到目标或全部循环计时完成；不保证干燥/膜质量' if asynchronous else '事务完成，物理时钟未推进',
                completion_state_ids=['cycle.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',retry='same_request_id',
                resources=['host_clock','finite_bath','substrate_attachment','request_cache'],partial_failure_zh='停止/故障保留已沉积材料和挥发，残余液体继续蒸发；不回滚，不提供跨进程幂等'),
            errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','PRECONDITION_FAILED','BUSY','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','REFERENCE_OUT_OF_RANGE','REFERENCE_INCOMPLETE','INVALID_GEOMETRY','BATH_OVERFLOW','BATH_EXHAUSTED','MODEL_OUT_OF_RANGE']],
            example_arguments=copy.deepcopy(ex),verification=[],limitations=['无膜厚传感器；崩碰由宿主/几何事件输入，不是实机安全认证'],source_ids=['manufacturer_primary','simulation_assumption'])
    mech=dict(component_ids=['motor','carriage','substrate','bath','clamp_jaw'],state_ids=list(slots),meaning_zh='有限浴/变液面/LLD弯月面沉积/64段双面液膜/蒸发',
        implementation='lumped',equations=[
          'dq/dt=commanded signed speed; q in [0,.175]m; no torque/acceleration solver',
          'bath_area*(surface-bottom)=bath_volume+width*thickness*clamp(surface-substrate_bottom,0,length)',
          'Ca=mu*U/gamma; h_LLD=.94*sqrt(gamma/(rho*g))*Ca^(2/3), Ca<=.02, complete wetting Newtonian assumption',
          'new film volume=2*width*strip_length*emerged_fraction*h_LLD, limited by finite bath inventory',
          'wet film re-immersed returns proportionally to bath; dry solid is assumed insoluble and retained',
          'P_sat=100000*10^(A-B/(T+C)); first library fit whose own temperature interval contains T (NIST Antoine rows first; NIST SRD69 saturation fit, ThermoML article fit or local Clausius-Clapeyron where NIST has no row at that T); never extrapolated',
          'bath.configure_from_reference: liquid properties = library values at 298.15 K (NIST SRD69 / median of ThermoML articles / PubChem HSDB), only at host temperature 298.15+/-0.5 K',
          'evap_kg_s=k_mass*exposed_area*P_sat*M/(R*T)*solvent_mass_fraction, inventory limited',
          'initial_species=bath+film+solid+evaporated+collected; nonvolatile solute evaporation=0'],
        parameters={},source_ids=['manufacturer_primary','nist_vapor_pressure','lld_theory','simulation_assumption'],
        limitations=['LLD只适用低Ca、完全润湿Newtonian流体；未标定动态接触角、重力排液、流变/凝胶化/再溶解、结晶、挥发冷却或CFD。','库内溶剂298.15K真实物性（密度/黏度/表面张力/蒸气压/沸点）只替代宿主假设的液体物性，不等于实测浸涂膜厚；LLD厚度仍为synthetic，公开LLD实测数据仅见图形（未数字化）。','几何固定示例25×75mm基片和0.0072m²槽，其他宿主几何参数不自动缩放网格。','变速规则按位移线性插值是假设；菜单、20程序/用户存储、重复性未复刻或校准。','夹口与崩碰开关属宿主权威，没有机器人/硬件接触验证。','64空间条带会造成网格误差；多次湿膜回浴和不溶干层只是教学近似。','代理API过滤不等于文件/进程部署隔离；原始Runtime仅供受信任宿主。'])
    spec=importlib.util.spec_from_file_location('_dip_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann)
    ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'dip_coating':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'bath.configure':(BATH,BATH_EXAMPLE),'substrate.configure':(SAMPLE,SAMPLE_EXAMPLE)})
    mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'dip_coating':mech})

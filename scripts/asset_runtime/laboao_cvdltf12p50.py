"""CHEM-063 single-zone P CVD: finite gases, NASA7 energy and explicit uncalibrated CH4 deposition.
Front panel (1.1.0; panel_ui.json beside the contract, run by the shared engine _panel_ui.py; approximate: the controllers' manuals are not public): the
five-key PV/SV program controller (set value, control mode, curve, steps, segment target / ramp / hold, PV correction), the green / red buttons, the
MFC channel selectors (close / control / purge) with their flow displays, the vacuum controller (set value, auto / manual, pump) and the manual valves."""
from __future__ import annotations
import copy,hashlib,importlib.util,json,math,os,tempfile,weakref
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
SLUG='laboao_cvdltf12p50';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-063';PROTOCOL_VERSION='1.0.0';ASSET_VERSION='1.1.3'
TEMPLATE_REL='assets/reactions/laboao_cvdltf12p50_thermo_library/templates.json'
R=8.31446261815324;STD_T=273.15;STD_P=101325.;SPECIES=['CH4','H2','N2','AR','O2'];ELEMENTS=['C','H','N','Ar','O']
ATOMS={'CH4':[1,4,0,0,0],'H2':[0,2,0,0,0],'N2':[0,0,2,0,0],'AR':[0,0,0,1,0],'O2':[0,0,0,0,2]};CARBON_KG_MOL=.012011
BOOL={'type':'boolean'};TEXT={'type':'string'}
class ActionRejected(ValueError):pass
def _rejected(code,detail):
    e=ActionRejected(code);e.detail=detail;return e   # 1.1.3: a refusal whose message names the cause
def number(unit,low=None,high=None):
    s={'type':'number','x-unit':unit}
    if low is not None:s['minimum']=low
    if high is not None:s['maximum']=high
    return s
def obj(p):return dict(type='object',properties=p,required=list(p),additionalProperties=False)
def arr(s,n=None):
    x=dict(type='array',items=s)
    if n is not None:x.update(minItems=n,maxItems=n)
    return x
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
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
GAS=dict(type='string',enum=['CH4','H2','N2','AR'])
GASMAP=obj({s:number('mol',0) for s in SPECIES})
PLANT=obj({'ambient_k':number('K',303.15,318.15),'ambient_pa':number('Pa',90000,110000),'oven_capacity_j_k':number('J/K',500,1e6),'body_capacity_j_k':number('J/K',500,1e6),'oven_body_w_k':number('W/K',.001,2),'body_ambient_w_k':number('W/K',.01,100),'fan_extra_w_k':number('W/K',0,100),'heated_fraction':number('1',.1,1),'effective_pump_m3_s':number('m^3/s',1e-5,.004),'vent_tau_s':number('s',.1,60),'mfc_tau_s':number('s',.05,4),'pi_kp_w_k':number('W/K',.1,1000),'pi_ki_w_ks':number('W/(K*s)',0,100),'high_band_limit_k_s':number('K/s',.001,20/60),'oxygen_limit_fraction':number('1',1e-8,.001),'pressure_hysteresis_pa':number('Pa',.1,5),'pump_timeout_s':number('s',10,3600),'rate_ref_s_1':number('1/s',1e-8,10),'activation_j_mol':number('J/mol',0,5e5),'rate_ref_k':number('K',573.15,1473.15),'capture_fraction':number('1',0,1),'film_density_kg_m3':number('kg/m^3',100,4000),'initial_oven_k':number('K',303.15,1473.15),'initial_body_k':number('K',303.15,573.15),'initial_pressure_pa':number('Pa',10,110000),'initial_warm_snapshot':BOOL,'accept_uncalibrated_model':BOOL})
PLANT_EXAMPLE=dict(ambient_k=303.15,ambient_pa=101325.,oven_capacity_j_k=12000.,body_capacity_j_k=15000.,oven_body_w_k=.12,body_ambient_w_k=2.,fan_extra_w_k=8.,heated_fraction=.42,effective_pump_m3_s=.004,vent_tau_s=.5,mfc_tau_s=.7,pi_kp_w_k=80.,pi_ki_w_ks=2.,high_band_limit_k_s=2/60,oxygen_limit_fraction=1e-4,pressure_hysteresis_pa=1.,pump_timeout_s=600.,rate_ref_s_1=.01,activation_j_mol=8e4,rate_ref_k=1173.15,capture_fraction=.4,film_density_kg_m3=2000.,initial_oven_k=303.15,initial_body_k=303.15,initial_pressure_pa=101325.,initial_warm_snapshot=False,accept_uncalibrated_model=True)
WARM_EXAMPLE=dict(PLANT_EXAMPLE,initial_oven_k=1173.15,initial_body_k=340.,initial_pressure_pa=30.,initial_warm_snapshot=True)
# 1.1.3: one source channel holds at most one full 50 L water-capacity cylinder filled to 200 bar (gauge) at 15 C:
# n = rho(T=288.15 K, p=201.01 bar abs) x 50 L with the densities of NIST Chemistry WebBook SRD 69 (Thermophysical Properties of Fluid
# Systems; tables in outputs/expansion_batch04/t5_automation/laboao_cvdltf12p50/sources/nist_fluid_288K_201bar.json).  Was 100 mol.
CYLINDER_WATER_CAPACITY_M3=.050
CYLINDER_DENSITY_MOL_M3={'N2':8001.8,'AR':8954.5,'CH4':10502.,'H2':7444.2}
CYLINDER_MOL={k:round(v*CYLINDER_WATER_CAPACITY_M3,1) for k,v in CYLINDER_DENSITY_MOL_M3.items()}   # N2 400.1, AR 447.7, CH4 525.1, H2 372.2
GASES=obj({'species':arr(GAS,3),'initial_mol':arr(number('mol',1e-8,max(CYLINDER_MOL.values())),3),'inlet_gauge_pa':arr(number('Pa',50000,300000),3),'full_scale_std_m3_s':arr(number('m^3/s',1e-10,1e-3),3),'actual_gas_factor':arr(number('1',.01,10),3),'accept_host_supply':BOOL})
GASES_EXAMPLE=dict(species=['N2','AR','CH4'],initial_mol=[.05,.05,.05],inlet_gauge_pa=[100000.]*3,full_scale_std_m3_s=[5e-6]*3,actual_gas_factor=[1.]*3,accept_host_supply=True)
SAMPLE=obj({'sample_id':TEXT,'area_m2':number('m^2',1e-8,.043*.21),'heat_capacity_j_k':number('J/K',.001,10000),'accept_host_attachment':BOOL,'accept_initial_hot_snapshot':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='substrate_01',area_m2=1e-4,heat_capacity_j_k=1.,accept_host_attachment=True,accept_initial_hot_snapshot=False)
HOT_SAMPLE_EXAMPLE=dict(SAMPLE_EXAMPLE,accept_initial_hot_snapshot=True)
SEGMENT=obj({'target_k':number('K',573.15,1473.15),'ramp_k_s':number('K/s',0,20/60),'hold_s':number('s',0,999.9*3600)})
SEGMENTS=arr(SEGMENT);SEGMENTS.update(minItems=1,maxItems=40)
METHOD=obj({'curve_slot':dict(number('1',0,3),type='integer'),'mode':dict(type='string',enum=['fixed','program']),'segments':SEGMENTS,'flow_std_m3_s':arr(number('m^3/s',0,1e-3),3),'pressure_pa':number('Pa',10,100),'pump_mode':dict(type='string',enum=['manual','auto']),'manual_valve_fraction':number('1',.001,1),'purge_s':number('s',.1,3600)})
METHOD_EXAMPLE=dict(curve_slot=0,mode='program',segments=[dict(target_k=1173.15,ramp_k_s=5/60,hold_s=5.)],flow_std_m3_s=[2e-7,2e-7,3e-7],pressure_pa=30.,pump_mode='manual',manual_valve_fraction=1.,purge_s=1.)
MEASURE=obj({'phase':PHASE,'stage':TEXT,'oven_k':number('K',0),'display_k':number('K',0),'setpoint_k':number('K',0),'pressure_pa':number('Pa',0),'flow_std_m3_s':arr(number('m^3/s',0),3),'elapsed_s':number('s',0),'deposition_s':number('s',0),'evidence':TEXT,'template_source':TEXT,'calibrated':BOOL})
THERMO=obj({'substance_id':TEXT,'temperature_k':number('K',1),'cp_j_molk':number('J/(mol*K)',0),'h_j_mol':number('J/mol'),'s_j_molk':number('J/(mol*K)'),'u_j_mol':number('J/mol'),'phase':TEXT,'evidence':TEXT,'source':TEXT,'template_source':TEXT})
LEDGER=obj({'source_remaining_mol':arr(number('mol',0),3),'gas_initial_mol':GASMAP,'gas_chamber_mol':GASMAP,'gas_exhaust_mol':GASMAP,'gas_from_air_mol':GASMAP,'film_carbon_mol':number('mol',0),'wall_carbon_mol':number('mol',0),'collected_carbon_mol':number('mol',0),'initial_energy_j':number('J'),'stored_energy_j':number('J'),'electrical_j':number('J',0),'inlet_enthalpy_j':number('J'),'outlet_enthalpy_j':number('J'),'environment_j':number('J'),'collected_energy_j':number('J'),'energy_residual_j':number('J'),'atom_residual_mol':arr(number('mol'),5),'thickness_m':number('m',0),'evidence':TEXT})
CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},[])
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body'],'显式热/泵/沉积假设及一次性t0初始快照','configuration','evaluator',PLANT_EXAMPLE)
cap('gas.install',GASES,obj({'installed':BOOL,'attachment_event':TEXT}),['gas_inlet_0','gas_inlet_1','gas_inlet_2'],'三路有限气源和未知满量程显式赋值；每路初始量不超过该气体一只满瓶（50 L水容积、200 bar表压、15 °C）的含量：N2 400.1、Ar 447.7、CH4 525.1、H2 372.2 mol（NIST WebBook SRD 69密度），超过则INVALID_ARGUMENT','configuration','evaluator',GASES_EXAMPLE)
cap('sample.install',SAMPLE,obj({'loaded':BOOL,'attachment_event':TEXT}),['sample_boat'],'冷态法兰开口装样；热态仅t0显式宿主快照','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.collect',obj({'recipient_id':TEXT}),obj({'recipient_id':TEXT,'attachment_event':TEXT}),['sample_boat'],'冷却回常压卸样，膜和显热转移不消失','device_command','evaluator',{'recipient_id':'holder'})
cap('method.configure',METHOD,obj({'configured':BOOL}),['control_panel'],'P型4曲线合计40段；3流量/压力/吹扫与分段升温','configuration','agent',METHOD_EXAMPLE)
cap('program.select',obj({'curve_slot':dict(number('1',0,3),type='integer')}),obj({'curve_slot':dict(number('1',0,3),type='integer')}),['control_panel'],'选择已配置曲线，不代表完整五键菜单','configuration','agent',{'curve_slot':0})
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['mains_switch'],'掉电停热停泵停气，保留余热和物质',ex={'enabled':True})
cap('furnace.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['furnace_lid'],'约100°翻盖切加热，但密封炉管不自动泄压',ex={'opened':False})
cap('flange.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['flange_left','flange_right'],'两端法兰代理：冷态回常压后才可拆装',ex={'opened':False})
cap('vacuum.set',obj({'enabled':BOOL,'mode':dict(type='string',enum=['manual','auto']),'pressure_pa':number('Pa',10,100),'manual_valve_fraction':number('1',.001,1)}),obj({'enabled':BOOL}),['vacuum_valve','pump_motor'],'泵启动10秒后开电磁阀；自动模式是电机启停',ex={'enabled':True,'mode':'manual','pressure_pa':30.,'manual_valve_fraction':1.})
cap('vacuum.vent',obj({}),obj({'venting':BOOL}),['vent_valve'],'停热/气/泵，按气量回常压，不瞬间置压')
cap('acquisition.start',obj({'mode':dict(type='string',enum=['purge','cvd'])}),obj({'phase':PHASE,'operation_id':TEXT}),['control_panel','heater','mfc_0','mfc_1','mfc_2'],'宿主时钟异步抽空/吹扫/温程/保温/有限沉积',ex={'mode':'cvd'})
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['control_panel'],'保留部分膜和残余气体；风机按炉体余温运行')
cap('process.measure',obj({}),MEASURE,['display_temperature','display_pressure','mfc_0','mfc_1','mfc_2'],'理想温压流量时间日志；真实膜厚不可直接从面板读出','query')
cap('product.read',obj({}),LEDGER,['sample_boat'],'私有元素/能量账本和模拟膜厚','query','evaluator')
# front panel (1.1.0): the controller parameters an operator sets on the keys (each rewrites the selected curve through method.configure)
INT=lambda lo,hi:{'type':'integer','minimum':lo,'maximum':hi,'x-unit':'1'}
CHANNEL_MODE=dict(type='string',enum=['close','control','purge'])
cap('controller.set_sv',obj({'target_k':number('K',573.15,1473.15)}),obj({'target_k':number('K')}),['control_panel','display_setpoint'],'定值运行时▲/▼改SV（定值曲线唯一一段的目标温度；300–1200°C）',ex={'target_k':1173.15})
cap('controller.set_mode',obj({'mode':dict(type='string',enum=['fixed','program'])}),obj({'mode':TEXT}),['control_panel'],'控制参数CtrL：定值/程序运行；转定值时保留程序段，转回程序时恢复',ex={'mode':'program'})
cap('controller.set_steps',obj({'steps':INT(1,40)}),obj({'steps':INT(1,40)}),['control_panel'],'程序段数StEP（4条曲线合计≤40段）：增加的段复制最后一段',ex={'steps':2})
cap('controller.set_segment',obj({'index':INT(0,39),'target_k':number('K',573.15,1473.15),'ramp_k_s':number('K/s',0,20/60),'hold_s':number('s',0,999.9*3600)}),obj({'index':INT(0,39)}),['control_panel','display_setpoint'],'程序段参数t/r/h：目标温度、升温速率（0–20°C/min）、保温时间（0–999.9h）',ex={'index':0,'target_k':1173.15,'ramp_k_s':5/60,'hold_s':60.})
cap('controller.set_correction',obj({'offset_k':number('K',-50,50)}),obj({'offset_k':number('K')}),['control_panel','display_temperature'],'修正参数Pb：PV示值修正（加到热电偶读数上，控制按修正后的PV）；默认0',ex={'offset_k':0.})
cap('gas.set_channel',obj({'channel':INT(0,2),'mode':CHANNEL_MODE}),obj({'channel':INT(0,2),'mode':TEXT}),['gas_valve_0','gas_valve_1','gas_valve_2'],'每路MFC选择钮：关闭（流量0）/阀控（跟随设定）/清洗（阀全开，满量程×气体系数）',ex={'channel':0,'mode':'control'})
PANEL_DEVICE={'controller.set_sv','controller.set_mode','controller.set_steps','controller.set_segment','controller.set_correction','gas.set_channel'}
cap('thermo.evaluate',obj({'substance_id':TEXT,'temperature_k':number('K',1,6000)}),THERMO,['control_panel'],'54条NASA7离线热物性；不执行其他物质沉积','query','agent',{'substance_id':'gri_CH4','temperature_k':1173.15})
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='body',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
for k in ['plant','method']:slot(k+'.configured',BOOL,'1',k+'配置完成','setpoint')
slot('power.enabled',BOOL,'1','总电源','setpoint',c='mains_switch',w=('power.set','enabled'))
slot('controller.correction_k',number('K',-50,50),'K','温控器PV修正Pb（默认0）','setpoint',c='control_panel')
slot('gas.channel_modes',arr(CHANNEL_MODE,3),'1','三路MFC选择钮位置（close/control/purge）','setpoint',c='gas_valve_0')
slot('furnace.opened',BOOL,'1','翻盖炉盖','scene',c='furnace_lid',w=('furnace.set','opened'))
slot('flange.opened',BOOL,'1','两端炉管密封法兰','scene',c='flange_left',w=('flange.set','opened'))
slot('lid.angle_rad',number('rad',0,math.radians(100)),'rad','炉盖后翻100°代理','scene',c='furnace_lid')
slot('flange.shift_m',number('m',0,.12),'m','两端法兰对称外移假设行程','scene',c='flange_left')
slot('boat.shift_m',number('m',0,.65),'m','装载位0、卸载位+X0.65m代理','scene',c='sample_boat')
slot('mains.angle_rad',number('rad',0,math.pi/2),'rad','总电源旋转代理','scene',c='mains_switch')
slot('vacuum.angle_rad',number('rad',0,math.pi/2),'rad','手动挡板阀开度','scene',c='vacuum_valve')
slot('vent.angle_rad',number('rad',0,math.pi/2),'rad','回气阀代理','scene',c='vent_valve')
slot('pump.enabled',BOOL,'1','抽气请求','setpoint',c='pump_motor')
slot('pump.motor_on',BOOL,'1','实际电机启停','scene',c='pump_motor')
slot('pump.valve_open',BOOL,'1','10秒延时电磁阀','scene',c='solenoid')
slot('pump.start_elapsed_s',number('s',0),'s','本次连续电机启动时间')
slot('vacuum.pressure_pa',number('Pa',0),'Pa','炉管绝对压力；不是-0.1MP3表压')
slot('oven.temperature_k',number('K',0),'K','单热区集总真实模型温度，不含测量噪声')
slot('oven.display_k',number('K',0),'K','以摄氏1度量化的理想PV')
slot('oven.setpoint_k',number('K',0),'K','程序受限斜坡设定值','setpoint')
slot('body.temperature_k',number('K',0),'K','独立炉体温度，仅用于模型风机逻辑','process_truth','evaluator')
slot('heater.power_w',number('W',0,3000),'W','单温区功率上限3kW')
slot('fan.enabled',BOOL,'1','炉体≥60℃时余冷风机状态')
for i in range(3):
    slot('gas.flow_'+str(i)+'_std_m3_s',number('m^3/s',0),'m^3/s','第'+str(i+1)+'路实际标准体积流率；标准273.15K101325Pa',c='mfc_'+str(i))
    slot('gas.valve_'+str(i)+'_rad',number('rad',0,math.pi/2),'rad','MFC归一化开度视觉代理，非真实控制阀芯位置','scene',c='gas_valve_'+str(i))
slot('cycle.phase',PHASE,'1','操作生命周期')
slot('cycle.stage',TEXT,'1','抽空/吹扫/升温/保温阶段')
slot('cycle.elapsed_s',number('s',0),'s','本次操作总时间')
slot('cycle.deposition_s',number('s',0),'s','满足温压/O2模型条件的反应时间')
slot('safety.warning',TEXT,'1','模型告警，不是安全认证')
slot('truth.ledger',LEDGER,'1','有限气源/元素/能量与膜厚真值','process_truth','evaluator','sample_boat')
for k in CAPS:
    if CAPS[k][4]!='query':CAPS[k][7].extend(SLOTS)
def _panel_engine():
    try:
        from . import _panel_ui as engine                                   # imported as asset_runtime.laboao_cvdltf12p50 (tests, Matterix bridge)
        return engine
    except ImportError:                                                      # loaded from its file path
        import sys
        if '_chemsim_panel_ui' not in sys.modules:
            s=importlib.util.spec_from_file_location('_chemsim_panel_ui',Path(__file__).with_name('_panel_ui.py'));sys.modules['_chemsim_panel_ui']=importlib.util.module_from_spec(s);s.loader.exec_module(sys.modules['_chemsim_panel_ui'])
        return sys.modules['_chemsim_panel_ui']
_panel_ui=_panel_engine();ASSET=ROOT/'assets/expansion_batch04/t5_automation'/SLUG
if os.environ.get('LTF12P50_PANEL_BUILD'):PANEL_SPEC,_PANEL_DEFS=None,{}   # spec builder: runtime constants without the spec being written
else:PANEL_SPEC=_panel_ui.load_spec(ASSET/'panel_ui.json',capabilities=set(CAPS));_PANEL_DEFS=_panel_ui.capability_definitions(PANEL_SPEC)
PANEL_CAPS={k:(d['input_schema'],d['output_schema'],list(d['component_ids']),d['function_zh'],d['kind'],d['visibility'],d['example_arguments'],[]) for k,d in _PANEL_DEFS.items()}   # kept out of CAPS
ALL_CAPS=dict(CAPS,**PANEL_CAPS);_PANELS=weakref.WeakKeyDictionary()
class LaboaoCvdltf12p50Runtime:
    VOLUME=math.pi*(.043/2)**2*1.
    def __init__(self,instance_id='cvdltf12p50_01'):
        self.instance_id=instance_id;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']}
        self.time_s=0.;self.initialized_at_s=None;self.revision=0;self.plant=None;self.gases=None;self.sample=None;self.method=None;self.programs={}
        self.oven=303.15;self.body=303.15;self.setpoint=self.oven;self.integral=0.;self.heater=0.;self.fan=False;self.powered=False;self.opened=True;self.flange=True;self.vent=False
        self.gas={s:0. for s in SPECIES};self.initial_gas=copy.deepcopy(self.gas);self.exhaust=copy.deepcopy(self.gas);self.air=copy.deepcopy(self.gas);self.remaining=[0.]*3;self.flow=[0.]*3
        self.film=0.;self.wall=0.;self.collected=0.;self.initial_energy=0.;self.electric=0.;self.hin=0.;self.hout=0.;self.environment=0.;self.collected_energy=0.
        self.pump=False;self.motor=False;self.solenoid=False;self.motor_elapsed=0.;self.pump_mode='manual';self.pressure_set=30.;self.manual_valve=1.
        self.phase='idle';self.stage='idle';self.warning='';self.elapsed_s=0.;self.deposit_s=0.;self.stage_elapsed=0.;self.segment=0;self.mode='cvd'
        self.operation_id='';self._operation_count=0;self.log=[];self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._sample_ids=set()
        self.correction=0.;self.channel_modes=['control']*3;self.program_backup={};self.panel_capture_dir=None
        self._caller=None   # principal of the session call in progress (set by the session; panel keys act as that caller)
    def register_principal(self,principal_id,visibility='agent'):
        if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id='__public_agent__'):
        if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return LaboaoCvdltf12p50Session(self,principal_id)
    def describe(self):return self.session().describe(instance_id=self.instance_id)
    def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k!='templates'})
    def thermo(self,sid,t):
        if sid not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
        r=self.templates[sid];bounds=r['conditions']['temperature_ranges_k']
        if not bounds[0]<=t<=bounds[-1]:raise ActionRejected('THERMO_RANGE')
        a=r['values']['coefficients'][0 if t<=bounds[1] else 1]
        cp=R*(a[0]+a[1]*t+a[2]*t*t+a[3]*t**3+a[4]*t**4)
        h=R*t*(a[0]+a[1]*t/2+a[2]*t*t/3+a[3]*t**3/4+a[4]*t**4/5+a[5]/t)
        s=R*(a[0]*math.log(t)+a[1]*t+a[2]*t*t/2+a[3]*t**3/3+a[4]*t**4/4+a[6])
        return cp,h,s,h-R*t if r['phase']=='gas' else h
    def _tg(self,t=None):return self.plant['ambient_k']+self.plant['heated_fraction']*((self.oven if t is None else t)-self.plant['ambient_k']) if self.plant else self.oven
    def _p(self):return sum(self.gas.values())*R*self._tg()/self.VOLUME
    def _energy(self,t=None):
        if not self.plant:return 0.
        t=self.oven if t is None else t;a=self.plant['ambient_k'];ct=self.plant['oven_capacity_j_k']+(self.sample['heat_capacity_j_k'] if self.sample else 0.)
        return ct*(t-a)+sum(n*self.thermo('gri_'+s,self._tg(t))[3] for s,n in self.gas.items())+(self.film+self.wall)*self.thermo('solid_C(gr)',t)[1]
    def _total_energy(self):return self._energy()+self.plant['body_capacity_j_k']*(self.body-self.plant['ambient_k']) if self.plant else 0.
    def _solve_energy(self,target):
        t=self.oven
        for _ in range(8):
            f=self._energy(t)-target
            if abs(f)<1e-8:break
            cap=self.plant['oven_capacity_j_k']+(self.sample['heat_capacity_j_k'] if self.sample else 0.)
            cap+=self.plant['heated_fraction']*sum(n*(self.thermo('gri_'+s,self._tg(t))[0]-R) for s,n in self.gas.items())+(self.film+self.wall)*self.thermo('solid_C(gr)',t)[0]
            t-=f/cap
        self.oven=t
    def _atoms(self,d):return [sum(d[s]*ATOMS[s][i] for s in SPECIES) for i in range(5)]
    def _ledger(self):
        incoming={s:self.initial_gas[s]+self.air[s] for s in SPECIES};outgoing={s:self.gas[s]+self.exhaust[s] for s in SPECIES}
        if self.gases:
            for i,s in enumerate(self.gases['species']):incoming[s]+=self.gases['initial_mol'][i];outgoing[s]+=self.remaining[i]
        ai=self._atoms(incoming);ao=self._atoms(outgoing);ao[0]+=self.film+self.wall+self.collected
        stored=self._total_energy()
        return dict(source_remaining_mol=list(self.remaining),gas_initial_mol=copy.deepcopy(self.initial_gas),gas_chamber_mol=copy.deepcopy(self.gas),gas_exhaust_mol=copy.deepcopy(self.exhaust),gas_from_air_mol=copy.deepcopy(self.air),film_carbon_mol=self.film,wall_carbon_mol=self.wall,collected_carbon_mol=self.collected,initial_energy_j=self.initial_energy,stored_energy_j=stored,electrical_j=self.electric,inlet_enthalpy_j=self.hin,outlet_enthalpy_j=self.hout,environment_j=self.environment,collected_energy_j=self.collected_energy,energy_residual_j=self.initial_energy+self.electric+self.hin-self.hout-self.environment-self.collected_energy-stored,atom_residual_mol=[x-y for x,y in zip(ai,ao)],thickness_m=self.film*CARBON_KG_MOL/self.sample['area_m2']/self.plant['film_density_kg_m3'] if self.sample else 0.,evidence='synthetic_CH4_mass_energy_model_with_published_reference_thermochemistry_not_instrument_calibration')
    def read(self,visibility='agent'):
        if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
        v={'plant.configured':self.plant is not None,'method.configured':self.method is not None,'power.enabled':self.powered,'furnace.opened':self.opened,'flange.opened':self.flange,'lid.angle_rad':math.radians(100) if self.opened else 0.,'flange.shift_m':.12 if self.flange else 0.,'boat.shift_m':0. if self.sample else .65,'mains.angle_rad':math.pi/2 if self.powered else 0.,'vacuum.angle_rad':self.manual_valve*math.pi/2,'vent.angle_rad':math.pi/2 if self.vent else 0.,'pump.enabled':self.pump,'pump.motor_on':self.motor,'pump.valve_open':self.solenoid,'pump.start_elapsed_s':self.motor_elapsed,'vacuum.pressure_pa':self._p(),'oven.temperature_k':self.oven,'oven.display_k':round(self._pv()-273.15)+273.15,'controller.correction_k':self.correction,'gas.channel_modes':list(self.channel_modes),'oven.setpoint_k':self.setpoint,'body.temperature_k':self.body,'heater.power_w':self.heater,'fan.enabled':self.fan,'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.elapsed_s':self.elapsed_s,'cycle.deposition_s':self.deposit_s,'safety.warning':self.warning,'truth.ledger':self._ledger()}
        for i in range(3):
            v['gas.flow_'+str(i)+'_std_m3_s']=self.flow[i]
            v['gas.valve_'+str(i)+'_rad']=min(1.,self.flow[i]/self.gases['full_scale_std_m3_s'][i]/self.gases['actual_gas_factor'][i])*math.pi/2 if self.gases else 0.
        return copy.deepcopy({k:x for k,x in v.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
    def invoke(self,capability_id,**a):
        if capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability_id][0],a);old=self.debug_snapshot()
        try:
            result=self._invoke(capability_id,a);validate(CAPS[capability_id][1],result)
            if CAPS[capability_id][4]!='query':self.revision+=1;self.log.append(dict(time_s=self.time_s,event=capability_id,stage=self.stage,phase=self.phase))
            return copy.deepcopy(result)
        except Exception:
            templates=self.templates;self.__dict__.clear();self.__dict__.update(old);self.templates=templates;raise
    def _idle(self):
        if self.phase=='running':raise ActionRejected('BUSY')
    def _cold_access(self):
        self._idle()
        if not self.flange or self.powered or self.oven>333.15 or self._p()<.99*self.plant['ambient_pa']:raise ActionRejected('COLD_VENTED_ACCESS_REQUIRED')
    def _stop(self,phase='cancelled',warning=''):
        if self.phase=='running':self.phase=phase;self.stage='finished'
        self.heater=0.;self.flow=[0.]*3;self.warning=warning
        self.fan=bool(self.powered and self.body>=333.15)
    def _foreign_run(self):
        """True when an agent caller would end another agent's running cycle (same check for the direct call and the panel key: both run with the
        session caller in _caller); evaluators and unowned runs are not restricted.  Hand actions (vent valve, furnace lid, rear mains rocker) are
        never refused (1.1.2): the device reacts itself (venting, the open lid and the power loss stop heating, gas and the run)."""
        c=self._caller;o=self._owners.get(self.operation_id)
        return self.phase=='running' and c is not None and self._principals.get(c)=='agent' and o is not None and o!=c
    def _invoke(self,k,a):
        if k=='acquisition.stop' or (k=='vacuum.set' and not a['enabled']):
            if self._foreign_run():raise ActionRejected('ACCESS_DENIED')
        if k=='thermo.evaluate':
            t=a['temperature_k'];cp,h,s,u=self.thermo(a['substance_id'],t);r=self.templates[a['substance_id']]
            return dict(substance_id=a['substance_id'],temperature_k=t,cp_j_molk=cp,h_j_mol=h,s_j_molk=s,u_j_mol=u,phase=r['phase'],evidence=r['evidence'],source=r['source']['url']+'#'+r['source']['locator'],template_source=TEMPLATE_REL)
        if k=='process.measure':return dict(phase=self.phase,stage=self.stage,oven_k=self.oven,display_k=round(self._pv()-273.15)+273.15,setpoint_k=self.setpoint,pressure_pa=self._p(),flow_std_m3_s=list(self.flow),elapsed_s=self.elapsed_s,deposition_s=self.deposit_s,evidence='synthetic_uncalibrated',template_source=TEMPLATE_REL,calibrated=False)
        if k=='product.read':return self._ledger()
        if k=='plant.configure':
            if self.plant or self.powered or not a['accept_uncalibrated_model']:raise ActionRejected('INITIALIZATION_ONLY')
            if not a['initial_warm_snapshot'] and (a['initial_oven_k']!=a['ambient_k'] or a['initial_body_k']!=a['ambient_k'] or a['initial_pressure_pa']!=a['ambient_pa']):raise ActionRejected('EXPLICIT_WARM_SNAPSHOT_REQUIRED')
            self.plant=copy.deepcopy(a);self.initialized_at_s=self.time_s;self.oven=a['initial_oven_k'];self.body=a['initial_body_k'];self.setpoint=self.oven
            n=a['initial_pressure_pa']*self.VOLUME/(R*self._tg())
            self.gas['N2']=n*(1. if a['initial_warm_snapshot'] else .79);self.gas['O2']=n*(0. if a['initial_warm_snapshot'] else .21)
            self.initial_gas=copy.deepcopy(self.gas);self.opened=not a['initial_warm_snapshot'];self.flange=self.opened;self.initial_energy=self._total_energy();return {'configured':True}
        if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
        if k=='gas.install':
            if self.gases or self.time_s!=self.initialized_at_s or self.powered or not a['accept_host_supply']:raise ActionRejected('INITIALIZATION_ONLY')
            if not any(s in ['N2','AR'] for s in a['species']):raise ActionRejected('INERT_PURGE_REQUIRED')
            for s,n in zip(a['species'],a['initial_mol']):   # 1.1.3: over the content of one full cylinder of that gas
                if n>CYLINDER_MOL[s]+1e-9:raise _rejected('INVALID_ARGUMENT','%s channel: %.1f mol is more than one full 50 L / 200 bar cylinder holds (%.1f mol)'%(s,n,CYLINDER_MOL[s]))
            self.gases=copy.deepcopy(a);self.remaining=list(a['initial_mol']);return dict(installed=True,attachment_event='host_supply_attach')
        if k=='sample.install':
            self._idle()
            if self.sample or a['sample_id'] in self._sample_ids or not a['sample_id'] or not a['accept_host_attachment']:raise ActionRejected('PRECONDITION_FAILED')
            hot=a['accept_initial_hot_snapshot'] and self.plant['initial_warm_snapshot'] and self.time_s==self.initialized_at_s and not self.powered
            if not hot:self._cold_access()
            if a['accept_initial_hot_snapshot'] and not hot:raise ActionRejected('INITIALIZATION_ONLY')
            self.sample=copy.deepcopy(a);self._sample_ids.add(a['sample_id']);delta=a['heat_capacity_j_k']*(self.oven-self.plant['ambient_k'])
            if hot:self.initial_energy+=delta
            else:self.hin+=delta
            return dict(loaded=True,attachment_event='host_initial_preloaded_snapshot' if hot else 'host_substrate_attach')
        if k=='sample.collect':
            self._cold_access()
            if not self.sample or not a['recipient_id']:raise ActionRejected('PRECONDITION_FAILED')
            self.collected_energy+=self.sample['heat_capacity_j_k']*(self.oven-self.plant['ambient_k'])+self.film*self.thermo('solid_C(gr)',self.oven)[1]
            self.collected+=self.film;self.film=0.;self.sample=None;return dict(recipient_id=a['recipient_id'],attachment_event='host_substrate_release')
        if k=='method.configure':
            self._idle()
            if not self.gases:raise ActionRejected('PRECONDITION_FAILED')
            if any(q>fs for q,fs in zip(a['flow_std_m3_s'],self.gases['full_scale_std_m3_s'])):raise ActionRejected('MFC_FULL_SCALE')
            if sum(len(v['segments']) for i,v in self.programs.items() if i!=a['curve_slot'])+len(a['segments'])>40:raise ActionRejected('TOTAL_40_STEPS')
            if a['mode']=='fixed' and len(a['segments'])!=1:raise ActionRejected('FIXED_ONE_SEGMENT')
            previous=self.setpoint
            for seg in a['segments']:
                if seg['ramp_k_s']==0 and abs(seg['target_k']-previous)>1e-6:raise ActionRejected('ZERO_RAMP_TRANSITION')
                previous=seg['target_k']
            if not any(q>0 and s in ['N2','AR'] for q,s in zip(a['flow_std_m3_s'],self.gases['species'])):raise ActionRejected('INERT_PURGE_REQUIRED')
            self.programs[a['curve_slot']]=copy.deepcopy(a);self.method=copy.deepcopy(a);return {'configured':True}
        if k=='program.select':
            self._idle()
            if a['curve_slot'] not in self.programs:raise ActionRejected('UNCONFIGURED_CURVE')
            self.method=copy.deepcopy(self.programs[a['curve_slot']]);return dict(curve_slot=a['curve_slot'])
        if k=='power.set':
            self.powered=a['enabled']
            if not self.powered:self._stop();self.pump=False;self.motor=False;self.solenoid=False;self.motor_elapsed=0.;self.fan=False
            return dict(enabled=self.powered)
        if k=='furnace.set':
            self.opened=a['opened']
            if self.opened:self._stop(warning='FURNACE_OPEN_HEATER_OFF')
            return dict(opened=self.opened)
        if k=='flange.set':
            if a['opened']:
                if self.oven>333.15 or self._p()<.99*self.plant['ambient_pa'] or self.powered:raise ActionRejected('COLD_VENTED_ACCESS_REQUIRED')
                self._stop();self.pump=False
            self.flange=a['opened'];return dict(opened=self.flange)
        if k=='vacuum.set':
            if a['enabled'] and (not self.powered or self.flange or self.vent):raise ActionRejected('PRECONDITION_FAILED')
            self.pump=a['enabled'];self.pump_mode=a['mode'];self.pressure_set=a['pressure_pa'];self.manual_valve=a['manual_valve_fraction']
            if not self.pump:self.motor=False;self.solenoid=False;self.motor_elapsed=0.;self._stop(warning='PUMP_OFF')
            return dict(enabled=self.pump)
        if k=='vacuum.vent':
            if self.flange:raise ActionRejected('PRECONDITION_FAILED')
            self._stop();self.pump=False;self.motor=False;self.solenoid=False;self.motor_elapsed=0.;self.vent=True;return dict(venting=True)
        if k=='acquisition.start':
            self._idle()
            if not self.powered or self.opened or self.flange or self.vent or not self.gases or not self.method or (a['mode']=='cvd' and not self.sample):raise ActionRejected('PRECONDITION_FAILED')
            previous=self.setpoint
            for seg in self.method['segments']:
                if seg['ramp_k_s']==0 and abs(seg['target_k']-previous)>1e-6:raise ActionRejected('ZERO_RAMP_TRANSITION')
                previous=seg['target_k']
            self.mode=a['mode'];self.phase='running';self.stage='evacuate';self.elapsed_s=0.;self.deposit_s=0.;self.stage_elapsed=0.;self.segment=0;self.warning='';self.integral=0.
            self._operation_count+=1;self.operation_id=f'{self.instance_id}:{self._operation_count}';self.pump=True
            self.pump_mode=self.method['pump_mode'];self.pressure_set=self.method['pressure_pa'];self.manual_valve=self.method['manual_valve_fraction']
            self.fan=True;return dict(phase=self.phase,operation_id=self.operation_id)
        if k=='acquisition.stop':self._stop();return dict(phase=self.phase)
        if k in PANEL_DEVICE:return getattr(self,'_cmd_'+k.replace('.','_'))(**a)
        raise ActionRejected('UNKNOWN_CAPABILITY')
    # ---- front panel (1.1.0): controller parameters, PV correction, MFC selectors
    def _pv(self):return self.oven+self.correction if self.correction else self.oven
    def _rewrite(self,**change):
        self._idle()
        if not self.method:raise ActionRejected('PRECONDITION_FAILED')
        new=dict(copy.deepcopy(self.method),**change);self._invoke('method.configure',new)
    def _cmd_controller_set_sv(self,target_k):
        if not self.method or self.method['mode']!='fixed':raise ActionRejected('PROGRAM_MODE')
        seg=dict(self.method['segments'][0],target_k=float(target_k));self._rewrite(segments=[seg]);return {'target_k':float(target_k)}
    def _cmd_controller_set_mode(self,mode):
        if not self.method:raise ActionRejected('PRECONDITION_FAILED')
        slot=self.method['curve_slot']
        if mode=='fixed' and self.method['mode']!='fixed':
            backup=copy.deepcopy(self.method['segments']);self._rewrite(mode='fixed',segments=backup[:1]);self.program_backup[slot]=backup
        elif mode=='program' and self.method['mode']!='program':
            self._rewrite(mode='program',segments=self.program_backup.get(slot) or copy.deepcopy(self.method['segments']));self.program_backup.pop(slot,None)
        return {'mode':self.method['mode']}
    def _cmd_controller_set_steps(self,steps):
        if not self.method:raise ActionRejected('PRECONDITION_FAILED')
        if self.method['mode']=='fixed':raise ActionRejected('FIXED_ONE_SEGMENT')
        seg=self.method['segments'];self._rewrite(segments=seg[:steps]+[copy.deepcopy(seg[-1]) for _ in range(steps-len(seg))]);return {'steps':steps}
    def _cmd_controller_set_segment(self,index,target_k,ramp_k_s,hold_s):
        if not self.method or index>=len(self.method['segments']):raise ActionRejected('PRECONDITION_FAILED')
        seg=copy.deepcopy(self.method['segments']);seg[index]=dict(target_k=float(target_k),ramp_k_s=float(ramp_k_s),hold_s=float(hold_s));self._rewrite(segments=seg);return {'index':index}
    def _cmd_controller_set_correction(self,offset_k):
        if not self.powered:raise ActionRejected('PRECONDITION_FAILED')
        self.correction=float(offset_k);return {'offset_k':self.correction}
    def _cmd_gas_set_channel(self,channel,mode):
        if not self.gases:raise ActionRejected('PRECONDITION_FAILED')
        self.channel_modes[channel]=mode;return {'channel':channel,'mode':mode}
    # ---- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
    @property
    def panel(self):
        p=_PANELS.get(self)
        if p is None and PANEL_SPEC:p=_PANELS[self]=_panel_ui.PanelUI(PANEL_SPEC,self)
        return p
    def panel_read(self):
        out=self.read('agent');m=self.method
        out.update({'host.powered':self.powered,'host.method':None if m is None else dict(mode=m['mode'],curve_slot=m['curve_slot'],steps=len(m['segments'])),
                    'host.segments':[] if m is None else [dict(i=i,target_c=round(s['target_k']-273.15,6),ramp_c_min=round(s['ramp_k_s']*60,6),hold_min=round(s['hold_s']/60,6),target_k=s['target_k'],ramp_k_s=s['ramp_k_s'],hold_s=s['hold_s']) for i,s in enumerate(m['segments'])],
                    'host.vacuum':dict(enabled=self.pump,mode=self.pump_mode,pressure_pa=self.pressure_set,manual_valve_fraction=self.manual_valve),
                    'host.flows_sccm':[f*6e7 for f in self.flow],'host.curves':sorted(self.programs)})
        return out
    def panel_invoke(self,capability_id,arguments):
        try:return self.invoke(capability_id,**arguments)
        except ActionRejected as exc:raise _panel_ui.PanelRejected(str(exc)) from None
    def panel_time(self):return self.time_s
    def _panel_entry(self,capability_id,arguments):
        validate(PANEL_CAPS[capability_id][0],arguments);out=getattr(self,'_cmd_'+capability_id.replace('.','_'))(**arguments)
        if PANEL_CAPS[capability_id][4]=='device_command':self.revision+=1
        return out
    def _panel(self,gesture,*args):
        try:return dict(accepted=bool(gesture(*args)))
        except _panel_ui.PanelError:raise ActionRejected('INVALID_ARGUMENT') from None
    def _cmd_panel_press(self,input_id,duration_s=.1):return self._panel(self.panel.press,input_id,float(duration_s))
    def _cmd_panel_turn(self,input_id,detents):return self._panel(self.panel.turn,input_id,int(detents))
    def _cmd_panel_switch(self,input_id,position):return self._panel(self.panel.switch,input_id,position)
    def _cmd_panel_touch(self,display_id,x,y,duration_s=.1,to_x=None,to_y=None):
        if (to_x is None)!=(to_y is None):raise ActionRejected('INVALID_ARGUMENT')
        return self._panel(self.panel.touch,display_id,int(x),int(y),float(duration_s),None if to_x is None else (int(to_x),int(to_y)))
    def _cmd_panel_capture(self,display_id):
        try:data,w,h=self.panel.png_bytes(display_id)
        except _panel_ui.PanelError:raise ActionRejected('INVALID_ARGUMENT') from None
        sha=hashlib.sha256(data).hexdigest();folder=Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir())/'chemistry_sim_panel'/self.instance_id;folder.mkdir(parents=True,exist_ok=True);path=folder/f'{display_id}_{sha[:16]}.png'
        if not path.exists():path.write_bytes(data)
        return dict(display_id=display_id,time_s=self.time_s,image=dict(path=str(path),format='png',width=w,height=h,sha256=sha))
    def _cmd_panel_frame(self):return dict(frame=self.panel.frame())
    def _gas_in(self,s,n,temp):
        self.gas[s]+=n;h=n*self.thermo('gri_'+s,temp)[1];self.hin+=h;return h
    def _gas_out(self,fraction):
        energy=0.;tg=self._tg()
        for s in SPECIES:
            n=self.gas[s]*fraction;self.gas[s]-=n;self.exhaust[s]+=n;energy+=n*self.thermo('gri_'+s,tg)[1]
        self.hout+=energy;return energy
    def _ramp_limit(self):
        c=self.setpoint-273.15
        return min(20/60,(5 if c<500 else 10 if c<800 else 5)/60 if c<1000 else self.plant['high_band_limit_k_s'])
    def step(self,dt):
        validate(number('s',0,86400),dt)
        if not self.plant:
            if dt:self.time_s+=dt;self.revision+=1
            return
        remaining=dt
        while remaining>1e-12:
            h=min(.05,remaining);remaining-=h;self._step(h);self.time_s+=h
        if dt:self.revision+=1
    def _step(self,dt):
        p=self.plant;before=self._energy();flow_energy=0.;self.heater=0.
        if self.powered and self.pump and not self.flange and not self.vent:
            desired=True
            if self.pump_mode=='auto' and not (self.phase=='running' and self.stage=='evacuate'):
                desired=self.motor
                if self._p()>self.pressure_set+p['pressure_hysteresis_pa']:desired=True
                elif self._p()<self.pressure_set-p['pressure_hysteresis_pa']:desired=False
            if desired:
                if not self.motor:self.motor_elapsed=0.
                self.motor=True;self.motor_elapsed+=dt;self.solenoid=self.motor_elapsed>=10.-1e-10
            else:self.motor=False;self.solenoid=False;self.motor_elapsed=0.
        else:self.motor=False;self.solenoid=False;self.motor_elapsed=0.
        active=self.phase=='running'
        if active:
            self.elapsed_s+=dt
            hold_valid=self.stage!='hold' or (10<=self._p()<=100 and abs(self._pv()-self.method['segments'][self.segment]['target_k'])<=1.)
            if hold_valid:self.stage_elapsed+=dt
            if self.elapsed_s>p['pump_timeout_s'] and self.stage in ['evacuate','purge']:self._stop('failed','PUMP_OR_PURGE_TIMEOUT');active=False
            if self.stage=='evacuate' and self.solenoid and self._p()<=100.:self.stage='purge';self.stage_elapsed=0.
            oxygen=self.gas['O2']/max(sum(self.gas.values()),1e-300)
            if self.stage=='purge' and self.stage_elapsed>=self.method['purge_s'] and oxygen<=p['oxygen_limit_fraction']:
                if self.mode=='purge':self._stop('succeeded');active=False
                else:self.stage='ramp';self.stage_elapsed=0.
            if self.stage=='ramp':
                seg=self.method['segments'][self.segment];delta=seg['target_k']-self.setpoint
                speed=min(seg['ramp_k_s'],self._ramp_limit());self.setpoint+=math.copysign(min(abs(delta),speed*dt),delta)
                if abs(self.setpoint-seg['target_k'])<1e-7 and abs(self._pv()-seg['target_k'])<=1.:self.stage='hold';self.stage_elapsed=0.
            if self.stage=='hold' and self.stage_elapsed>=self.method['segments'][self.segment]['hold_s']:
                self.segment+=1
                if self.segment==len(self.method['segments']):self._stop('succeeded');active=False
                else:self.stage='ramp';self.stage_elapsed=0.
        if active and not self.opened and self.stage in ['ramp','hold']:
            error=self.setpoint-self._pv();candidate=self.integral+error*dt
            raw=p['pi_kp_w_k']*error+p['pi_ki_w_ks']*candidate
            if 0<=raw<=3000 or (raw>3000 and error<0) or (raw<0 and error>0):self.integral=candidate
            self.heater=min(3000.,max(0.,p['pi_kp_w_k']*error+p['pi_ki_w_ks']*self.integral))
        self.fan=bool(self.powered and (active or self.body>=333.15))
        for i in range(3):
            if not self.gases:continue
            s=self.gases['species'][i];target=0.
            if active and self.stage in ['purge','ramp','hold'] and (s in ['N2','AR'] or (self.stage=='hold' and self.solenoid)):
                if s!='CH4' or (10<=self._p()<=100 and self.gas['O2']/max(sum(self.gas.values()),1e-300)<=p['oxygen_limit_fraction']):target=self.method['flow_std_m3_s'][i]*self.gases['actual_gas_factor'][i]
            if self.channel_modes[i]=='close':target=0.                                # MFC selector: valve closed
            elif self.channel_modes[i]=='purge':target=self.gases['full_scale_std_m3_s'][i]*self.gases['actual_gas_factor'][i] if self.powered else 0.   # valve fully open
            gauge=(self.gases['inlet_gauge_pa'][i]+p['ambient_pa'])*self.remaining[i]/self.gases['initial_mol'][i]-p['ambient_pa']
            if target and gauge<50000:self._stop('failed','SOURCE_UNDERPRESSURE');active=False;target=0.
            # A hard close upstream of the MFC stops commanded/cancelled flow; no unmodeled line inventory.
            self.flow[i]=(target+(self.flow[i]-target)*math.exp(-dt/p['mfc_tau_s'])) if target else 0.
            n=min(self.remaining[i],self.flow[i]*STD_P/(R*STD_T)*dt);self.remaining[i]-=n
            if n:flow_energy+=self._gas_in(s,n,p['ambient_k'])
        if self.solenoid:
            n=sum(self.gas.values());base=.3*self.VOLUME/(R*self._tg());fraction=(1-math.exp(-p['effective_pump_m3_s']*self.manual_valve*dt/self.VOLUME))*max(0.,n-base)/max(n,1e-300)
            flow_energy-=self._gas_out(fraction)
        if self.vent:
            target=p['ambient_pa']*self.VOLUME/(R*self._tg());delta=(target-sum(self.gas.values()))*(1-math.exp(-dt/p['vent_tau_s']))
            if delta>=0:
                for s,frac in [('N2',.79),('O2',.21)]:n=delta*frac;self.air[s]+=n;flow_energy+=self._gas_in(s,n,p['ambient_k'])
            else:flow_energy-=self._gas_out(-delta/max(sum(self.gas.values()),1e-300))
            if abs(self._p()-p['ambient_pa'])<1.:self.vent=False
        reaction=active and self.stage=='hold' and self.sample and 10<=self._p()<=100 and 573.15<=self.oven<=1473.15 and abs(self.oven-self.method['segments'][self.segment]['target_k'])<=1. and self.gas['O2']/max(sum(self.gas.values()),1e-300)<=p['oxygen_limit_fraction']
        if reaction:
            rate=p['rate_ref_s_1']*math.exp(-p['activation_j_mol']/R*(1/self.oven-1/p['rate_ref_k']))
            extent=self.gas['CH4']*(1-math.exp(-min(rate*dt,700)));self.gas['CH4']-=extent;self.gas['H2']+=2*extent
            self.film+=extent*p['capture_fraction'];self.wall+=extent*(1-p['capture_fraction']);self.deposit_s+=dt
        coupling=p['oven_body_w_k']*(self.oven-self.body)*dt;loss=(p['body_ambient_w_k']+(p['fan_extra_w_k'] if self.fan else 0.))*(self.body-p['ambient_k'])*dt
        self.body+=(coupling-loss)/p['body_capacity_j_k'];self.environment+=loss;heat=self.heater*dt;self.electric+=heat
        self._solve_energy(before+flow_energy+heat-coupling)
        if self.oven>1474.15 and self.phase=='running':self._stop('failed','MODEL_OVERTEMPERATURE')
        if self._p()>p['ambient_pa']*1.01 and self.phase=='running':self._stop('failed','MODEL_OVERPRESSURE')

class LaboaoCvdltf12p50Session:
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
        m=export_laboao_cvdltf12p50_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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
            try:result=h._panel_entry(capability_id,arguments) if capability_id in PANEL_CAPS else h.invoke(capability_id,**arguments)
            finally:h._caller=None
            op=result.get('operation_id')
            if op:h._owners[op]=self._principal
            elif capability_id in PANEL_CAPS and h.operation_id and h.operation_id!=before:h._owners[h.operation_id]=self._principal   # a run started with R/S or the green button belongs to the caller
            response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=op,status='accepted' if op else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None);h._requests[key]=(digest,copy.deepcopy(response));return response
        except ActionRejected as exc:return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,status='rejected',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result={},observations={},error=dict(code=str(exc),message=getattr(exc,'detail',None) or str(exc),retryable=False,side_effects='none'))
    def write(self,*,instance_id,state_id,value,request_id):
        self._target(instance_id)
        if state_id not in SLOTS or not SLOTS[state_id][5]:raise ActionRejected('ACCESS_DENIED')
        cap,arg=SLOTS[state_id][5];return self.invoke(instance_id=instance_id,capability_id=cap,arguments={arg:value},request_id=request_id)
    def poll(self,operation_id):
        h=self._host
        if operation_id!=h.operation_id or h._owners.get(operation_id)!=self._principal:raise ActionRejected('ACCESS_DENIED')
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('process.measure'))
    def cancel(self,operation_id,request_id):
        self.poll(operation_id);return self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)








PANEL_EFFECTS={'controller.set_sv':['method.configured'],'controller.set_mode':['method.configured'],'controller.set_steps':['method.configured'],'controller.set_segment':['method.configured'],
               'controller.set_correction':['controller.correction_k','oven.display_k'],'gas.set_channel':['gas.channel_modes']}
def export_laboao_cvdltf12p50_contract_metadata():
    caps={};slots={}
    for k,(s,u,o,v,c,w) in SLOTS.items():
        slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',
        read_binding=dict(backend='host',artifact_id='runtime',accessor=f'LaboaoCvdltf12p50Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    evolving=[k for k,v in SLOTS.items() if v[2] in ['scene','derived','process_truth']]
    for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
        asynchronous=k=='acquisition.start';query=kind=='query'
        caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
        implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
        binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.laboao_cvdltf12p50.LaboaoCvdltf12p50Session.invoke',invocation=f'session.invoke(instance_id="cvdltf12p50_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),
        preconditions=[] if query else [dict(description_zh='电源、独立炉盖/真空法兰、有限气源/基底、温程和宿主身份',state_ids=['power.enabled','furnace.opened','flange.opened','cycle.phase'],enforced_by='runtime',check='atomic Runtime.invoke and trusted-principal Session')],
        state_effects=[] if query else [dict(state_id=s,timing='immediate',description_zh='受检设定和宿主装载事件') for s in effects]+[dict(state_id=s,timing='on_step',description_zh='唯一宿主时钟推进有限气体/反应/热量') for s in evolving],
        observes=[],model_effects=dict(mode='none',description_zh='等待实际场景证据',binding_ids=[]),
        execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',completion_criterion='吹扫达到时间与模型O2阈值，或温程各段实际温度进入±1K再保温结束；并不表示冷却可卸样' if asynchronous else '事务完成，不瞬间设置物理温度或真空',completion_state_ids=['cycle.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',retry='same_request_id',resources=['host_clock','finite_gases','substrate','furnace','pump','request_cache'],partial_failure_zh='取消停热和进气但保留真空/余热/部分沉积；请求去重限进程内。'),
        errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','PRECONDITION_FAILED','BUSY','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','THERMO_RANGE','INITIALIZATION_ONLY','EXPLICIT_WARM_SNAPSHOT_REQUIRED','COLD_VENTED_ACCESS_REQUIRED','MFC_FULL_SCALE','TOTAL_40_STEPS','FIXED_ONE_SEGMENT','ZERO_RAMP_TRANSITION','INERT_PURGE_REQUIRED','UNCONFIGURED_CURVE','PROGRAM_MODE']],
        example_arguments=copy.deepcopy(ex),verification=[],limitations=['集总CH4沉积教学模型，非原厂固件/实测生长规律/安全控制建议'],source_ids=['manufacturer_primary','simulation_assumption'])
    for k,(eff) in PANEL_EFFECTS.items():caps[k]['state_effects']=[dict(state_id=s,timing='immediate',description_zh='面板设定立即改变的状态') for s in eff]+caps[k]['state_effects']
    called=_panel_ui.spec_capabilities(PANEL_SPEC) if PANEL_SPEC else []
    for k,(inp,out,c,z,kind,vis,ex,effects) in PANEL_CAPS.items():
        gesture=kind=='device_command'
        caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
        implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
        binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.laboao_cvdltf12p50.LaboaoCvdltf12p50Session.invoke',invocation=f'session.invoke(instance_id="cvdltf12p50_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),
        preconditions=[dict(description_zh='按panel_ui.json的面板规则：总电源关时只有总电源开关与手动阀有效；被调用的命令各自检查前提，被拒时显示Err与代码且状态不变',state_ids=['power.enabled','cycle.phase'],enforced_by='runtime',check='LaboaoCvdltf12p50Runtime._panel_entry（面板引擎_panel_ui.py）')] if gesture else [],
        state_effects=([dict(state_id=s,timing='immediate',description_zh='手势调用的命令立即改变的状态') for s in sorted({e for x in called for e in PANEL_EFFECTS.get(x,[])})]+[dict(state_id=s,timing='on_step',description_zh='手势调用的命令在之后的step中推进的状态') for s in evolving]) if gesture else [],
        observes=[],model_effects=dict(mode='none',description_zh='显示画面由panel.capture提供；3D读数纹理与按键/旋钮关节触发手势属后续Matterix步骤',binding_ids=[]),
        execution=dict(mode='synchronous',time_domain='simulation',completion_criterion='手势瞬时完成（不推进仿真时钟）；被调用命令的过程由step推进',completion_state_ids=[],timeout_s=None,cancellation='unsupported',retry='same_request_id',resources=['panel'],partial_failure_zh='一个手势可调用多个命令：已被接受的前序命令保留，被拒的命令不改变状态' if gesture else '只读'),
        errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','REQUEST_CONFLICT','ACCESS_DENIED']],
        example_arguments=copy.deepcopy(ex),verification=[],limitations=['面板层（近似）：温控器与真空控制器的说明书未公开，按键功能与参数代码按同类国产程序温控器的常见布置重建，见panel_ui.json的assumptions'],source_ids=['manufacturer_primary','simulation_assumption'])
    mech=dict(component_ids=['heater','quartz_tube','sample_boat','mfc_0','mfc_1','mfc_2','pump_motor','vacuum_valve','solenoid'],state_ids=list(slots),meaning_zh='三路有限气体、单温区/炉体双热节点、NASA7热化学及有限碳沉积',implementation='lumped',
    equations=['P=sum(n_i)*R*Tgas/V; V=pi*(0.043/2)^2*1; Tgas=Tamb+host_heated_fraction*(Toven-Tamb)',
    'dn_in=actual_standard_volume_flow*101325/(R*273.15)*dt; source reservoirs finite and inlet pressure decreases; MFC first-order tau explicitly assumed',
    'Motor starts and holds 10s before solenoid opens; manual flapper modulates effective S<=0.004m3/s; gas removal exponential toward standalone pump base0.3Pa; auto motor hysteresis may cycle',
    'CH4 -> C(gr)+2H2; extent=n_CH4*(1-exp(-host_kref*exp(-host_Ea/R*(1/T-1/Tref))*dt)); film=host_capture*extent, rest wall',
    'Uoven=Coven*(T-Ta)+Csample*(T-Ta)+sum(n*u_i(Tgas))+(nfilm+nwall)*h_graphite(T); includes chemical formation energies',
    'Total initial_U+electrical+Hin-Hout-environment-collected=stored_U; solve temperature after each finite species/energy transfer; no double-counted reaction enthalpy',
    'NASA7 Cp/R=a0+a1*T+a2*T²+a3*T³+a4*T⁴; integrate h and s per source, each record range checked',
    '3kW PI heater; program nominal0–20K/min plus installed-tube piecewise slew caps5/10/5K/min and explicitly assumed high-band cap; P stores4 curves total40 segments',
    'Flipping clamshell cuts heater only; sealed tube remains sealed. Flange opening/unloading requires cold and atmospheric pressure; fan stop threshold uses separate body temperature333.15K',
    'Host.step sole clock, integration<=0.05s; zero duration no change; no per-asset hidden wall-clock or environment cross-talk'],
    parameters={},source_ids=['manufacturer_primary','thermo_gri','thermo_graphite','thermo_equations','simulation_assumption'],
    limitations=['54 thermochemical reference states are not54 compatible CVD precursors or50 measured deposition curves.',
    'Only CH4 carbon deposition instantiated; Cu(111) graphene apparent activation enthalpies retained separately and not used to calibrate rate_ref/Ea.',
    'Single lumped zone and body node; no spatial temperature/CFD/nucleation/roughness/growth chemistry/film characterization.',
    'Effective pumping, MFC tau/full scales/correction factors, thermal constants and kinetics all explicit host assumptions; no calibrated controller or 1%FS hardware validation.',
    'No O2/H2/CH4 combustion, toxic/pyrophoric handling, certified safety or real piping/leak/overtemperature trip hardware.',
    'Full P five-key menu, firmware EEPROM, physical quartz seals, host grasp/contact and real hardware absent; T touchscreen/USB intentionally absent.'])
    spec=importlib.util.spec_from_file_location('_laboao_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann)
    ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
    ann.annotate_export(SLUG,caps,slots,{'cvd':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'gas.install':(GASES,GASES_EXAMPLE),'sample.install':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
    for k,v in slots.items():
        v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'，非实机标定。'
        if v['unit']=='Pa':v['reference_conditions']='绝对压；系统10–100Pa，泵0.3Pa极限不同于工艺工作范围'
        if v['unit']=='m^3/s':v['reference_conditions']='标准体积：273.15K，101325Pa，实际气体校正显式输入；厂家满量程未知'
    mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'cvd':mech})


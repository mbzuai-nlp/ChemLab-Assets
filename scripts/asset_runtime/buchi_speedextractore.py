"""E916 six-cell conservative extraction teaching model, NOT hardware control.

1.1: the firmware functions behind the front-panel menus (positions, preheat setpoint / EcoMode, method editor fields and the method store,
SKIP and the three STOP presses, leak test, flush, instrument settings, solvent list, service functions) and the front panel itself
(panel.press / turn / switch / capture / frame; panel_ui.json beside the contract, generic engine _panel_ui.py)."""
from __future__ import annotations
import copy,json,math
from asset_runtime import _annotations as _ann
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.antonpaar_abbematadvanced import validate
ROOT=Path(__file__).resolve().parents[2];ASSET_TYPE_ID='instrument.buchi_speedextractore';CATALOG_ID='CHEM-028';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/buchi_speedextractore_extraction_library/templates.json';LIB=ROOT/Path(TEMPLATE_REL).parent
PHASE={'type':'string','enum':['idle','running','succeeded','cancelled','failed']}
def arr(s):return {'type':'array','items':s}
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
MASS=number('kg',0);VOL=number('m^3',0);PRESS=number('Pa',0);TEMP=number('K',273.15,473.15);FRAC=number('1',0,1)
SOLVENTS=['acetone','ethanol','methanol','n_hexane'];INT=lambda lo,hi:{'type':'integer','minimum':lo,'maximum':hi,'x-unit':'1'}
PLANT=obj({'ambient_k':number('K',288.15,303.15),'ambient_pa':number('Pa',80000,110000),'reservoir_m3':arr(number('m^3',0,.02)),'nitrogen_gauge_pa':number('Pa',6e5,1e6),'nitrogen_mol':number('mol',.001,1000),'nitrogen_mol_s_per_cell':number('mol/s',1e-6,.01),'exhaust_connected':BOOL,'dry_matrix_reviewed':BOOL,'thermal_tau_s':number('s',.5,300),'pressure_tau_s':number('s',.1,30),'discharge_m3_s_per_cell':number('m^3/s',1e-9,1e-5),'residual_holdup_fraction':number('1',0,.2),'vial_capacity_m3':number('m^3',60e-6,240e-6),'coefficient_evidence':TEXT})
EXAMPLE=dict(ambient_k=295.15,ambient_pa=101325.,reservoir_m3=[.001]*4,nitrogen_gauge_pa=7e5,nitrogen_mol=10.,nitrogen_mol_s_per_cell=.001,exhaust_connected=True,dry_matrix_reviewed=True,thermal_tau_s=10.,pressure_tau_s=1.,discharge_m3_s_per_cell=2e-6,residual_holdup_fraction=.02,vial_capacity_m3=240e-6,coefficient_evidence='synthetic_host_heat_hydraulic_transfer_and_holdup; NOT factory calibration')
METHOD=obj({'temperature_k':number('K',303.15,473.15),'pressure_gauge_pa':number('Pa',5e6,15e6),'cycles':INT(1,10),'hold_s':number('s',0,3600),'discharge_s':number('s',0,3600),'pump_total_m3_s':number('m^3/s',1e-6/60,50e-6/60),'solvent_fractions':arr(FRAC),'solvent_flush_s':number('s',0,540),'nitrogen_flush_s':number('s',60,1800)})
METHOD_EXAMPLE=dict(temperature_k=373.15,pressure_gauge_pa=10e6,cycles=2,hold_s=30.,discharge_s=30.,pump_total_m3_s=50e-6/60,solvent_fractions=[.5,.5,0.,0.],solvent_flush_s=0.,nitrogen_flush_s=60.)
SAMPLE=obj({'position':INT(0,5),'sample_id':TEXT,'substance_id':TEXT,'cell_volume_m3':{'type':'number','enum':[10e-6,20e-6,40e-6],'x-unit':'m^3'},'matrix_mass_kg':number('kg',1e-9,.1),'matrix_solid_volume_m3':number('m^3',1e-10,39e-6),'target_mass_kg':number('kg',1e-15,.001),'kd_m3_kg_by_solvent':arr(number('m^3/kg',1e-8,1e3)),'mass_transfer_s_inv':number('1/s',1e-6,10),'activation_energy_j_mol':number('J/mol',0,1e5),'adsorption_energy_j_mol':number('J/mol',0,1e5),'solubility_kg_m3':number('kg/m^3',1e-9,1000),'degradation_s_inv':number('1/s',0,1),'gripper_confirmed':BOOL,'parameters_evidence':TEXT})
SAMPLE_EXAMPLE=dict(position=0,sample_id='soil_1',substance_id='phenol',cell_volume_m3=20e-6,matrix_mass_kg=.005,matrix_solid_volume_m3=5e-6,target_mass_kg=1e-6,kd_m3_kg_by_solvent=[.0001,.0002,.00015,.01],mass_transfer_s_inv=.02,activation_energy_j_mol=10000.,adsorption_energy_j_mol=5000.,solubility_kg_m3=10.,degradation_s_inv=0.,gripper_confirmed=True,parameters_evidence='synthetic_host_soil_partition_transfer; NOT measured E916 recovery')
RESULT=obj({'phase':PHASE,'stage':TEXT,'fault':TEXT,'temperature_k':nullable(TEMP),'max_pressure_gauge_pa':nullable(PRESS),'cycle':INT(0,10),'stage_elapsed_s':number('s',0),'evidence':TEXT,'template_source':TEXT})
ITEM=obj({'position':INT(0,5),'sample_id':TEXT,'substance_id':TEXT,'extract_target_kg':MASS,'residual_target_kg':MASS,'degraded_target_kg':MASS,'matrix_mass_kg':MASS,'extract_solvent_m3':VOL,'residual_solvent_m3':VOL,'modeled_absolute_recovery_fraction':FRAC,'measured_e916_recovery_fraction':{'type':'null'},'reference_ase_soxtec_ratio':number('1',0),'reference_instrument':TEXT})
PRODUCT=obj({'items':arr(ITEM),'evidence':TEXT,'template_source':TEXT})
REFERENCE=obj({'substance_id':TEXT,'name':TEXT,'ase_soxtec_ratio':number('1',0),'measured_e916_recovery_fraction':{'type':'null'},'source':TEXT,'conditions':TEXT,'evidence':TEXT,'template_source':TEXT})
# ---------------------------------------------------------------- device functions behind the front panel (E-916 operation manual 093218, 'om p.N' = PDF page)
# Positions, preheat setpoint / EcoMode, the method editor fields (vial, cell, solvent names, per-cycle hold / discharge / vial change / port), the
# method store (100), SKIP and the three STOP presses, leak test, flush, instrument settings, solvent list and the service functions (valves,
# sensors, pump, flow test, lifts, fans, operating hours).  The 1.0 commands keep their behaviour: with positions never set every loaded sample
# cell takes part, as before.
MLMIN=1e-6/60;BAR=1e5
CELL_VOLUMES=[10e-6,20e-6,40e-6];VIALS={'60':60e-6,'150':150e-6,'220':220e-6,'240':240e-6,'unspecified':None,'waste':None}
DEFAULT_SOLVENTS=['Acetone','Acetonitrile','Cyclohexane','Dichloromethane','Hexane','Isopropyl alcohol','Methanol','Petrolether','Toluene','Water']   # om p.38 (entry 010 hidden: assumed)
LANGUAGES=['English','Deutsch','Francais','Italiano','Espanol']
SETTINGS={'language':dict(type='enum',options=LANGUAGES,default='English'),'contrast':dict(type='int',min=10,max=100,default=70),
          'beep_key':dict(type='bool',default=False),'beep_end':dict(type='bool',default=True),'preheat_demand':dict(type='bool',default=False)}   # om p.40 figure
DEFAULT_METHOD=dict(temperature_k=373.15,pressure_gauge_pa=10e6,cycles=1,hold_s=120.,discharge_s=120.,pump_total_m3_s=50*MLMIN,solvent_fractions=[1.,0.,0.,0.],
                    solvent_flush_s=60.,nitrogen_flush_s=180.,vial='240',cell_volume_m3=20e-6,solvent_names=['Acetone','Ethanol','Methanol','Hexane'],
                    cycle_steps=[dict(hold_s=120.,discharge_s=120.,vial_change=False,port=0)])          # om p.55-56 defaults; cell and names assumed
LEAK_DEFAULT=dict(temperature_k=373.15,pressure_gauge_pa=10e6,solvent_fractions=[1.,0.,0.,0.],hold_s=240.,flush_into='vial',gas_flush_s=120.)   # om p.43 reference
FLUSH_DEFAULT=dict(solvent_s=120.,flush_into='waste',flow_m3_s=50*MLMIN,gas_s=180.,solvent_fractions=[1.,0.,0.,0.])                                # om p.44 E-916 table
FLOW_DEFAULT=dict(flush_into='vial',port=1,position=1)
VALVES=['solvent_1','solvent_2','solvent_3','solvent_4','media_solvent','media_gas']+['position_%d'%i for i in range(1,7)]+['outlet_vial','outlet_waste','solvent_all','position_all']
LIFT_SPEED=.01;LIFT_CURRENT_A=.35;OVERALL_HOLD=.97;BACK_PA_PER_M3S=3*BAR/(50*MLMIN)          # assumptions: lift motor current while moving; 3 bar back pressure per line at 50 mL/min
FAN_FACTORY=30;FAN_LOSS=.1    # extraction fan (om p.88: factory 30 %, 100 % in a fault, the setting changes the heating-block temperature): extra loss
                              # conductance FAN_LOSS*(fan-30 %)/100 relative to the heater control (assumption: 100 % fan ~5 K low at 100 C)
NAME_CHARS=set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 .,-+*:%&/()=?!_')
UNIT_INFO=dict(line_voltage='230V',firmware='01.03',serial='')                   # om p.88 figure (serial number blank there)
FRAC4={'type':'array','items':FRAC,'minItems':4,'maxItems':4};POS6={'type':'array','items':BOOL,'minItems':6,'maxItems':6};FREE={'type':'object'}
VIAL_ENUM={'type':'string','enum':list(VIALS)};INTO={'type':'string','enum':['vial','waste']};CELL_ENUM={'type':'number','enum':CELL_VOLUMES,'x-unit':'m^3'}
STEP=obj({'hold_s':number('s',0,3600),'discharge_s':number('s',0,3600),'vial_change':BOOL,'port':INT(0,4)})
METHOD_EXTRA={'vial':VIAL_ENUM,'cell_volume_m3':CELL_ENUM,'solvent_names':{'type':'array','items':TEXT,'minItems':4,'maxItems':4},'cycle_steps':{'type':'array','items':STEP,'minItems':1,'maxItems':10}}
NAMED=obj({'slot':INT(1,100),'name':TEXT});SLOT_ONLY=obj({'slot':INT(1,100)})
LEAK=obj({'temperature_k':number('K',303.15,473.15),'pressure_gauge_pa':number('Pa',5e6,15e6),'solvent_fractions':FRAC4,'hold_s':number('s',60,1800),'flush_into':INTO,'gas_flush_s':number('s',60,1800)},optional=('temperature_k','pressure_gauge_pa','solvent_fractions','hold_s','flush_into','gas_flush_s'))
FLUSH=obj({'solvent_s':number('s',0,540),'flush_into':INTO,'flow_m3_s':number('m^3/s',1*MLMIN,50*MLMIN),'gas_s':number('s',60,1800),'solvent_fractions':FRAC4},optional=('solvent_s','flush_into','flow_m3_s','gas_s','solvent_fractions'))
FLOW=obj({'flush_into':INTO,'port':INT(1,4),'position':INT(1,6)},optional=('flush_into','port','position'))
STATUS_OUT=obj({'status':FREE})
POSITIONS=[f'position_valve_{i}' for i in range(6)];SENSORS=[f'pressure_sensor_{i}' for i in range(6)];CELLS=[f'cell_{i}' for i in range(6)]
DEVICE_CAPS={
 'positions.set':(obj({'active':POS6,'position':INT(1,6),'enabled':BOOL},optional=('active','position','enabled')),obj({'active':POS6}),POSITIONS,'激活/停用萃取位置（OCCUPIED POSITIONS：全部、或单个位置；停用位置的位置阀全程关闭，只冲洗激活位置；om p.40-41）','device_command','agent',{'active':[True]*6},['positions.active']),
 'method.save':(NAMED,obj({'slot':INT(1,100),'name':TEXT}),['panel'],'把当前方法存入方法表（1–100号，名称≤16字符；覆盖已占用号；之后主屏标题为该名且不再划掉；om p.55）','configuration','agent',{'slot':1,'name':'PAH-01'},['method.library','method.name','method.modified']),
 'method.open':(SLOT_ONLY,obj({'slot':INT(1,100),'name':TEXT}),['panel','pump','heater_lift'],'打开方法表中的方法作为当前方法（与method.configure同样的前提：装样与首次运行之前；om p.57）','configuration','agent',{'slot':1},['method.configured','thermal.target_k','method.current','method.name','method.modified']),
 'method.delete':(SLOT_ONLY,obj({'slot':INT(1,100)}),['panel'],'删除方法表中的一个方法（当前方法不变；om p.27）','configuration','agent',{'slot':1},['method.library']),
 'thermal.set_eco_mode':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['heater_lift'],'EcoMode：下一次萃取结束后自动关加热；关机再开机自动取消（om p.45）','device_command','agent',{'enabled':True},['thermal.eco_mode']),
 'acquisition.skip':(obj({}),obj({'stage':TEXT}),['pump','media_valve','outlet_valve'],'SKIP：跳过当前萃取步骤（Hold→排液、溶剂冲洗→氮吹；氮吹在至少60 s安全期且压力≤1 bar后才可跳到结束；om p.28、58）','device_command','agent',{},['cycle.stage','cycle.stage_elapsed_s']),
 'acquisition.halt':(obj({}),obj({'phase':PHASE,'stage':TEXT}),['pump','media_valve','outlet_valve','heater_lift','collection_rack'],'第三次按STOP：立即停止，各组件停在当时位置（压力、液体与升降架保持；之后只能用服务菜单手动泄压、降架；om p.26、59）','device_command','agent',{},['cycle.phase','cycle.stage']),
 'leak_test.configure':(LEAK,obj({'settings':FREE}),['panel'],'检漏参数（上次所用值保存在仪器中）：温度、压力、四溶剂比例、保持时间、冲洗去向（瓶/废液）、氮吹时间（om p.41-43）','configuration','agent',{'hold_s':240.},['leak_test.settings']),
 'leak_test.start':(obj({'start_anyway':BOOL},optional=('start_anyway',)),STATUS_OUT,['pump','heater_lift','collection_rack','outlet_valve']+SENSORS,'检漏（功能键START，不是绿色START）：密封、氮气7 bar气密检查、泵入溶剂升到设定压力、关阀保持观察各位压力、排液与氮吹、降架；结果按手册判据（各位差≤15 bar、≥95 %设定）给出（om p.41-43）','device_command','agent',{},['leak_test.status','service.counters']),
 'leak_test.stop':(obj({}),STATUS_OUT,['outlet_valve'],'检漏中按SKIP：提前结束，出液阀打开排液、氮吹，测得压力保留在屏上（om p.42）','device_command','agent',{},['leak_test.status']),
 'flush.configure':(FLUSH,obj({'settings':FREE}),['panel'],'冲洗参数：溶剂冲洗时间、去向（废液/收集瓶）、流量1–50 mL/min、氮吹时间、四溶剂比例（om p.44-45）','configuration','agent',{'flush_into':'waste'},['flush.settings']),
 'flush.start':(obj({}),STATUS_OUT,['pump','mixer','media_valve','outlet_valve','heater_lift'],'冲洗（功能键START）：密封、气密检查池是否在位、按设定总流量冲洗激活位置（停用位置不降低流量）、氮吹、降架（om p.44-45）','device_command','agent',{},['flush.status']),
 'flush.stop':(obj({}),STATUS_OUT,['pump','heater_lift'],'冲洗菜单中按STOP：停止冲洗，泵停、降架（池内残液保留；om p.45）','device_command','agent',{},['flush.status']),
 'flow_test.configure':(FLOW,obj({'settings':FREE}),['panel'],'流动测试参数：去向、溶剂口1–4、位置1–6（om p.86）','configuration','agent',{'position':1},['flow_test.settings']),
 'flow_test.start':(obj({}),STATUS_OUT,['pump']+POSITIONS+SENSORS,'流动测试：泵以50 mL/min经所选位置送入空池并排出，显示背压与时间（未堵约3 bar，手册上限8 bar）；须先用LIFT手动关闭池与瓶升降架（om p.86-87）','device_command','agent',{},['flow_test.status']),
 'flow_test.stop':(obj({}),STATUS_OUT,['pump'],'停止流动测试','device_command','agent',{},['flow_test.status']),
 'pump.run':(obj({'enabled':BOOL,'flow_m3_s':number('m^3/s',1*MLMIN,50*MLMIN)},optional=('flow_m3_s',)),obj({'pump':FREE}),['pump'],'服务菜单泵：独立于方法以1–50 mL/min运行（实际值收敛到设定）；必须有通路（溶剂阀、介质阀溶剂位、位置阀、出液阀），否则泵压过高（Error 51）停泵（om p.85）','device_command','agent',{'enabled':True,'flow_m3_s':10*MLMIN},['service.pump']),
 'valve.set':(obj({'valve':{'type':'string','enum':VALVES},'open':BOOL}),obj({'valves':FREE}),['mixer','media_valve','outlet_valve']+POSITIONS,'服务菜单阀门：溶剂阀1–4、介质旋转阀（溶剂/氮气）、位置阀1–6、出液旋转阀（收集瓶/废液），可单开单关或全开全关（om p.84）；开位置阀与出液阀可手动泄压','device_command','agent',{'valve':'solvent_1','open':True},['service.valves']),
 'valve.calibrate':(obj({'valve':{'type':'string','enum':['media','outlet']}}),obj({'valves':FREE}),['media_valve','outlet_valve'],'旋转阀回到定义的初始位置（关闭；om p.83-84）','device_command','agent',{'valve':'media'},['service.valves']),
 'pressure.calibrate':(obj({}),obj({'sensors':FREE}),['divider']+SENSORS,'压力传感器校零：把七个传感器当前读数记为0 bar（必须先打开加热块与位置阀使仪器不带压，否则零点被记错；om p.85）','device_command','agent',{},['pressure.sensors']),
 'sensor.configure_error':(obj({'sensor':INT(0,6),'offset_pa':number('Pa',-2e6,2e6)}),obj({'sensors':FREE}),['divider']+SENSORS,'评测者：压力传感器偏差（0为泵压，1–6为位置；默认0即理想；之后由校零修正）','configuration','evaluator',{'sensor':1,'offset_pa':0.},['pressure.sensors']),
 'seal.configure_leak':(obj({'position':INT(1,6),'leak_pa_s':number('Pa/s',0,1e6)}),obj({'position':INT(1,6),'leak_pa_s':number('Pa/s',0)}),[f'lower_seal_{i}' for i in range(6)],'评测者：某位杯形密封的泄漏率（默认0即密封完好；检漏保持阶段该位压力按此下降）','configuration','evaluator',{'position':1,'leak_pa_s':0.},['leak_test.status']),
 'lift.move':(obj({'lift':{'type':'string','enum':['cell','vial']},'direction':{'type':'string','enum':['up','down','stop']}}),obj({'lifts':FREE}),['heater_lift','collection_rack'],'服务菜单：池升降（加热块）或瓶升降（收集架）上升/下降/停止；保护屏未关不动（Error 10），加热块不在中位不动（Error 9）；带压降下加热块会泄压喷出池内液体（om p.88）','device_command','agent',{'lift':'cell','direction':'up'},['service.lifts','heater.lift_m','rack.lift_m']),
 'service.reset_current_max':(obj({}),obj({'counters':FREE}),['heater_lift','collection_rack'],'运行时间页Reset：清除升降电机最大电流记录（om p.88）','device_command','agent',{},['service.counters']),
 'fan.set':(obj({'fan':{'type':'string','enum':['extraction','electronic']},'percent':INT(0,100)}),obj({'fans':FREE}),['rear_fan','tower_fan'],'风扇功率（出厂30 %；手册不建议改，会影响加热块温度；om p.88）','device_command','agent',{'fan':'extraction','percent':30},['service.fans']),
 'settings.configure':(obj({'key':{'type':'string','enum':list(SETTINGS)},'value':{'anyOf':[TEXT,{'type':'number','x-unit':'1'},BOOL]}}),obj({'settings':FREE}),['panel'],'仪器设定：语言、显示对比度、按键蜂鸣、结束蜂鸣、开机预热询问（om p.40）','configuration','agent',{'key':'preheat_demand','value':True},['device.settings']),
 'solvents.set_name':(obj({'index':INT(1,20),'name':TEXT}),obj({'solvents':{'type':'array','items':TEXT}}),['panel'],'溶剂表（20条）改名或新增；空名删除该条（om p.38-39）','configuration','agent',{'index':11,'name':'Chlorobenzene'},['solvents.list']),
 'solvents.load_default':(obj({}),obj({'solvents':{'type':'array','items':TEXT}}),['panel'],'DEFAULT：前10条恢复为出厂溶剂表，011–020不变（om p.39）','configuration','agent',{},['solvents.list']),
 'vials.exchange':(obj({'gripper_confirmed':BOOL}),obj({'exchanged':BOOL}),['collection_rack']+[f'collection_vial_{i}' for i in range(6)],'宿主换收集瓶（瓶架降下时；换瓶暂停或运行结束后）：取走瓶中萃取液，放入空瓶（om p.54）','configuration','evaluator',{'gripper_confirmed':True},['truth.removed_target_kg','truth.removed_solvent_m3','truth.extract_solvent_m3']),
 'cell.insert_empty':(obj({'position':INT(1,6),'cell_volume_m3':CELL_ENUM,'gripper_confirmed':BOOL}),obj({'occupancy':{'type':'array','items':nullable(TEXT)}}),['heater_lift']+CELLS,'宿主放入空萃取池（检漏、冲洗、流动测试与停用位置用；开屏抽块，夹具确认；om p.41、44、49）','configuration','evaluator',{'position':1,'cell_volume_m3':20e-6,'gripper_confirmed':True},['cells.occupancy']),
 'cell.remove_empty':(obj({'position':INT(1,6),'gripper_confirmed':BOOL}),obj({'occupancy':{'type':'array','items':nullable(TEXT)}}),['heater_lift']+CELLS,'宿主取出空池（不带压、开屏抽块；池内残液随池取走）','configuration','evaluator',{'position':1,'gripper_confirmed':True},['cells.occupancy','truth.removed_solvent_m3'])}
DEVICE_ERRORS={'NO_POSITION_SELECTED':'没有激活位置（Error 58）','SHIELD_OPEN':'保护屏未关（Error 10）','HEATER_NOT_IN_POSITION':'加热块不在中位（Error 9）',
 'HEATER_TEMPERATURE_OUT_OF_RANGE':'加热块温度与设定差超过30 °C（Error 2）','HEATER_TOO_HOT':'加热块比设定高3 °C以上，须确认仍要开始（Error 3）',
 'PUMP_PRESSURE_TOO_HIGH':'泵对着关闭的阀运行（Error 51）','NO_CELL':'所选位置没有萃取池（Error 45–50）','LIFT_OPEN':'池或瓶升降架未关闭','SLOT_EMPTY':'方法表该号为空',
 'SKIP_NOT_AVAILABLE':'当前步骤不能跳过','RATIO_SUM':'溶剂比例之和不是100 %','SETTING_INVALID':'设定值不在范围或选项内','NAME_INVALID':'名称为空、超过16字符或含不可输入字符'}
NEW_PRE={'positions.set':('不在萃取/检漏/冲洗过程中',['cycle.phase','cycle.stage']),'method.open':('与method.configure相同：装样与首次运行之前，方法完整且通过液相检查',['sample.configured','cycle.phase']),
 'method.save':('名称1–16个可输入字符',[]),'thermal.set_eco_mode':('通电',['power.enabled']),'acquisition.skip':('萃取运行中且处于Hold、溶剂冲洗或（≥60 s且压力≤1 bar的）氮吹',['cycle.stage','cycle.stage_elapsed_s','pressure.max_gauge_pa']),
 'acquisition.halt':('萃取过程已开始且未结束/未急停',['cycle.stage']),'leak_test.start':('通电、无萃取过程、屏关、加热块中位、有激活位置；加热块温度与检漏温度差≤30 °C，高于3 °C以上须start_anyway',['power.enabled','shield.lift_m','drawer.extension_m','positions.active','thermal.indicated_k']),
 'leak_test.stop':('检漏处于气密、升压或保持阶段',['leak_test.status']),'flush.start':('通电、无萃取过程、屏关、加热块中位、有激活位置',['power.enabled','shield.lift_m','drawer.extension_m','positions.active']),
 'flush.stop':('冲洗进行中',['flush.status']),'flow_test.start':('通电、空闲、池与瓶升降架已关闭、所选位置有池',['power.enabled','heater.lift_m','rack.lift_m','cells.occupancy']),'flow_test.stop':('流动测试进行中',['flow_test.status']),
 'pump.run':('通电、空闲；开泵时必须有通路',['power.enabled','service.valves']),'valve.set':('通电、萃取未在运行、无检漏/冲洗/流动测试',['power.enabled','cycle.phase']),'valve.calibrate':('通电、空闲',['power.enabled']),
 'pressure.calibrate':('通电、空闲（零点记为当前读数）',['power.enabled']),'lift.move':('通电、空闲；上升须屏关（池升降另需加热块中位）',['power.enabled','shield.lift_m','drawer.extension_m']),
 'vials.exchange':('瓶架降下、萃取未在运行',['rack.lift_m','cycle.phase']),'cell.insert_empty':('屏全开、加热块抽出、该位空、夹具确认',['shield.lift_m','drawer.extension_m','cells.occupancy']),
 'cell.remove_empty':('屏全开、加热块抽出、该位为空池、不带压、夹具确认',['shield.lift_m','drawer.extension_m','cells.occupancy'])}
NEW_CAP_ERRORS={'positions.set':['BUSY','PRECONDITION_FAILED'],'method.save':['NAME_INVALID'],'method.open':['SLOT_EMPTY','PRECONDITION_FAILED','LIQUID_PHASE_GUARD','BUSY'],'method.delete':['SLOT_EMPTY'],
 'thermal.set_eco_mode':['PRECONDITION_FAILED'],'acquisition.skip':['SKIP_NOT_AVAILABLE'],'acquisition.halt':['PRECONDITION_FAILED'],'leak_test.configure':['BUSY'],
 'leak_test.start':['BUSY','PRECONDITION_FAILED','NO_POSITION_SELECTED','SHIELD_OPEN','HEATER_NOT_IN_POSITION','HEATER_TEMPERATURE_OUT_OF_RANGE','HEATER_TOO_HOT','RATIO_SUM'],
 'leak_test.stop':['PRECONDITION_FAILED'],'flush.configure':['BUSY'],'flush.start':['BUSY','PRECONDITION_FAILED','NO_POSITION_SELECTED','SHIELD_OPEN','HEATER_NOT_IN_POSITION','RATIO_SUM'],
 'flush.stop':['PRECONDITION_FAILED'],'flow_test.configure':['BUSY'],'flow_test.start':['BUSY','PRECONDITION_FAILED','LIFT_OPEN','NO_CELL'],'flow_test.stop':['PRECONDITION_FAILED'],
 'pump.run':['BUSY','PRECONDITION_FAILED','PUMP_PRESSURE_TOO_HIGH'],'valve.set':['BUSY','PRECONDITION_FAILED'],'valve.calibrate':['BUSY','PRECONDITION_FAILED'],'pressure.calibrate':['BUSY','PRECONDITION_FAILED'],
 'sensor.configure_error':[],'seal.configure_leak':[],'lift.move':['BUSY','PRECONDITION_FAILED','SHIELD_OPEN','HEATER_NOT_IN_POSITION'],'service.reset_current_max':[],'fan.set':['PRECONDITION_FAILED'],
 'settings.configure':['SETTING_INVALID'],'solvents.set_name':['NAME_INVALID'],'solvents.load_default':[],'vials.exchange':['PRECONDITION_FAILED'],'cell.insert_empty':['PRECONDITION_FAILED'],'cell.remove_empty':['PRECONDITION_FAILED']}
CHANGES_PROCESS={'fan.set','thermal.set_eco_mode','thermal.preheat','valve.set','valve.calibrate','pump.run','lift.move','pressure.calibrate'}   # caller rules
RUN_OK={'power.set','acquisition.stop','acquisition.abort','acquisition.skip','acquisition.halt','method.save','method.delete','settings.configure','solvents.set_name','solvents.load_default',
        'sensor.configure_error','seal.configure_leak','thermal.set_eco_mode','leak_test.stop','flush.stop','flow_test.stop','fan.set','service.reset_current_max'}
STAGE_TEXT={'standby':'Ready','sealing':'Close lift','tightness':'Tightness','fill':'Fill','heatup_1':'Heat up','heatup_2':'Heat up','heatup_3':'Heat up','hold':'Hold',
            'discharge':'Discharge','solvent_flush':'Flush solv.','nitrogen_flush':'Flush gas','release':'Open lift','complete':'Ready','vial_change':'Vial change','halted':'Stop',
            'pump':'Pump','solvent':'Flush solv.','gas':'Flush gas','running':'Running'}


def _name_ok(name,empty=False):
    return isinstance(name,str) and (bool(name.strip()) or empty) and len(name)<=16 and set(name)<=NAME_CHARS


class _E916Device:
    """State and commands of the E-916 firmware functions reached from the menus (mixed into BuchiSpeedextractoreRuntime)."""
    def _device_init(self):
        self.positions=None;self.blanks={};self.method_name='DEFAULT';self.method_modified=False;self.library={};self.preheat_k=None;self.eco=False
        self.aux=None;self.aux_last={};self.leak=copy.deepcopy(LEAK_DEFAULT);self.flush_set=copy.deepcopy(FLUSH_DEFAULT);self.flow_set=copy.deepcopy(FLOW_DEFAULT)
        self.valves=dict(solvent=[False]*4,media=None,position=[False]*6,outlet=None);self.calibrations=dict(media=0,outlet=0)
        self.pump=dict(on=False,set_m3_s=1*MLMIN,actual_m3_s=0.);self.pump_p=0.;self.lift_cmd=dict(cell=None,vial=None);self.current_max=dict(cell=0.,vial=0.)
        self.fans=dict(extraction=30,electronic=30);self.counters=dict(extractions=0,leak_tests=0,operating_s=0.,heater_s=0.,max_temperature_k=None,max_pressure_pa=0.)
        self.sensor_error=[0.]*7;self.sensor_zero=[0.]*7;self.seal_leak=[0.]*6;self.settings={k:v['default'] for k,v in SETTINGS.items()}
        self.solvent_list=list(DEFAULT_SOLVENTS)+['']*10;self.notice=None;self._notices=0;self.end_beeps=0;self.exchanged=[0.]*4;self.removed_blank=[0.]*4;self.power_epoch=0

    # ---- cells and positions
    def _free(self,c):return c['volume'] if c.get('blank') else c['spec']['cell_volume_m3']-c['spec']['matrix_solid_volume_m3']
    def _records(self):
        out={i:c for i,c in self.blanks.items() if not c['removed']};out.update({i:c for i,c in self.cells.items() if not c['removed']});return out
    def _active(self):
        if self.positions is None:return sorted(i for i,c in self.cells.items() if not c['removed'])     # 1.0 API: every loaded sample cell takes part
        return [i for i,on in enumerate(self.positions) if on]
    def _run_cells(self):
        r=self._records();return [r[i] for i in self._active() if i in r]
    def _missing(self):
        r=self._records();return [i for i in self._active() if i not in r]
    def _occupancy(self):
        r=self._records();return [None if i not in r else ('empty' if r[i].get('blank') else 'sample') for i in range(6)]
    def _notify(self,code):
        self._notices+=1;self.notice=dict(id=self._notices,code=code,time_s=self.time_s)
    def _in_operation(self):
        return self._operation_count>0 and self.stage not in ('complete','standby')
    def _idle(self):
        return self.phase!='running' and self.aux is None

    # ---- method helpers
    def _full_method(self):
        m=copy.deepcopy(self.method or DEFAULT_METHOD)
        m.setdefault('vial','unspecified');m.setdefault('cell_volume_m3',None);m.setdefault('solvent_names',list(DEFAULT_METHOD['solvent_names']))
        m.setdefault('cycle_steps',[dict(hold_s=m['hold_s'],discharge_s=m['discharge_s'],vial_change=False,port=0) for _ in range(m['cycles'])]);return m
    def _step(self,key):
        steps=(self.method or {}).get('cycle_steps')
        return steps[min(max(self.cycle,1),len(steps))-1][key] if steps else self.method[key]
    def _fractions(self):
        steps=(self.method or {}).get('cycle_steps')
        port=steps[min(max(self.cycle,1),len(steps))-1]['port'] if steps else 0
        return [1. if i==port-1 else 0. for i in range(4)] if port else self.method['solvent_fractions']
    def _cell_volume(self,cells):
        if self.method and self.method.get('cell_volume_m3'):return self.method['cell_volume_m3']
        c=cells[0] if cells else next(iter(self.cells.values()))
        return c['volume'] if c.get('blank') else c['spec']['cell_volume_m3']
    def _vial_change_due(self,cells):
        steps=self.method.get('cycle_steps')
        if steps and steps[min(self.cycle,len(steps))-1]['vial_change']:return True
        cap=VIALS.get(self.method.get('vial'))                                  # Error 4: the declared vial would overflow in the next cycle
        return bool(cap) and any(sum(c['vial'])+self._free(c)>cap+1e-12 for c in cells)
    def _overflow_possible(self):
        m=self._full_method();cap=VIALS.get(m['vial']);cell=m['cell_volume_m3']
        if not cap or not cell:return False
        n=0
        for s in m['cycle_steps'][:m['cycles']]:
            n+=1
            if n*cell>cap+1e-12:return True
            if s['vial_change']:n=0
        return False
    def _check_method_extras(self,a):
        n=a['cycles']
        if 'cycle_steps' in a:
            if len(a['cycle_steps'])!=n:raise ActionRejected('PRECONDITION_FAILED')
            for s in a['cycle_steps']:
                if isinstance(s['port'],bool) or not isinstance(s['port'],int) or not 0<=s['port']<=4:raise ActionRejected('INVALID_ARGUMENT')
                if s['port'] and a['pressure_gauge_pa']/3+101325<1.1*self.saturation_pressure(a['temperature_k'],[1. if i==s['port']-1 else 0. for i in range(4)]):raise ActionRejected('LIQUID_PHASE_GUARD')
        if 'solvent_names' in a and any(len(x)>20 for x in a['solvent_names']):raise ActionRejected('INVALID_ARGUMENT')

    # ---- commands (called from BuchiSpeedextractoreRuntime.invoke)
    def _device_invoke(self,cap,a):
        if cap=='positions.set':
            if self.aux or self._in_operation():raise ActionRejected('BUSY' if self.phase=='running' or self.aux else 'PRECONDITION_FAILED')
            if ('active' in a)==('position' in a) or ('position' in a)!=('enabled' in a):raise ActionRejected('INVALID_ARGUMENT')
            act=list(a['active']) if 'active' in a else list(self.positions or [i in self._active() for i in range(6)])
            if 'position' in a:act[a['position']-1]=a['enabled']
            self.positions=act;return dict(active=list(act))
        if cap=='method.save':
            if not _name_ok(a['name']):raise ActionRejected('NAME_INVALID')
            self.library[a['slot']]=dict(name=a['name'],method=copy.deepcopy(self.method or DEFAULT_METHOD));self.method_name=a['name'];self.method_modified=False
            return dict(slot=a['slot'],name=a['name'])
        if cap=='method.open':
            e=self.library.get(a['slot'])
            if e is None:raise ActionRejected('SLOT_EMPTY')
            self.invoke('method.configure',**e['method']);self.method_name=e['name'];self.method_modified=False;return dict(slot=a['slot'],name=e['name'])
        if cap=='method.delete':
            if a['slot'] not in self.library:raise ActionRejected('SLOT_EMPTY')
            del self.library[a['slot']];return dict(slot=a['slot'])
        if cap=='thermal.set_eco_mode':
            if not self.powered:raise ActionRejected('PRECONDITION_FAILED')
            self.eco=a['enabled'];return dict(enabled=self.eco)
        if cap=='acquisition.skip':
            if self.phase!='running' or self.stage not in ('hold','solvent_flush','nitrogen_flush') or (self.stage=='nitrogen_flush' and (self.stage_elapsed<60 or self._measured_max()>BAR)):raise ActionRejected('SKIP_NOT_AVAILABLE')
            self._next({'hold':'discharge','solvent_flush':'nitrogen_flush','nitrogen_flush':'release'}[self.stage]);return dict(stage=self.stage)
        if cap=='acquisition.halt':
            if not self._operation_count or self.stage in ('standby','complete','halted'):raise ActionRejected('PRECONDITION_FAILED')
            self.phase='cancelled';self.aborting=False;self._next('halted');return dict(phase=self.phase,stage=self.stage)
        if cap in ('leak_test.configure','flush.configure','flow_test.configure'):
            if not a:raise ActionRejected('INVALID_ARGUMENT')
            for k in ('port','position'):
                if k in a and (isinstance(a[k],bool) or not isinstance(a[k],int)):raise ActionRejected('INVALID_ARGUMENT')
            store={'leak_test.configure':self.leak,'flush.configure':self.flush_set,'flow_test.configure':self.flow_set}[cap]
            if self.aux and self.aux['settings'] is store:raise ActionRejected('BUSY')
            store.update(copy.deepcopy(a));return dict(settings=copy.deepcopy(store))
        if cap in ('leak_test.start','flush.start'):
            self._check_aux_start()
            if abs(sum((self.leak if cap=='leak_test.start' else self.flush_set)['solvent_fractions'])-1)>1e-9:raise ActionRejected('RATIO_SUM')
            if cap=='leak_test.start':
                dt=self.temperature-self.leak['temperature_k']
                if abs(dt)>30:raise ActionRejected('HEATER_TEMPERATURE_OUT_OF_RANGE')
                if dt>3 and not a.get('start_anyway'):raise ActionRejected('HEATER_TOO_HOT')
                self.counters['leak_tests']+=1
            kind='leak_test' if cap=='leak_test.start' else 'flush'
            self.aux=dict(kind=kind,stage='sealing',stage_elapsed=0.,elapsed=0.,settings=self.leak if kind=='leak_test' else self.flush_set,fault=None,result=None,hold_elapsed=0.,
                          owner=self.__dict__.get('_caller'))
            self.valves=dict(solvent=[False]*4,media=None,position=[False]*6,outlet=None);self.pump['on']=False
            return dict(status=self._aux_status())
        if cap=='leak_test.stop':
            if not self.aux or self.aux['kind']!='leak_test' or self.aux['stage'] not in ('tightness','pump','hold'):raise ActionRejected('PRECONDITION_FAILED')
            if self.aux['stage']=='hold':self._leak_result(stopped=True)
            self._aux_next('discharge');return dict(status=self._aux_status())
        if cap=='flush.stop':
            if not self.aux or self.aux['kind']!='flush':raise ActionRejected('PRECONDITION_FAILED')
            self.aux['stopped']=True;self._aux_next('release');return dict(status=self._aux_status())
        if cap=='flow_test.start':
            if not self.powered or not self.plant:raise ActionRejected('PRECONDITION_FAILED')
            if self.phase=='running' or self.aux:raise ActionRejected('BUSY')
            if self._in_operation():raise ActionRejected('PRECONDITION_FAILED')
            if self.heater<.035-1e-9 or self.rack<.035-1e-9:raise ActionRejected('LIFT_OPEN')
            if self.flow_set['position']-1 not in self._records():raise ActionRejected('NO_CELL')
            self.aux=dict(kind='flow_test',stage='running',stage_elapsed=0.,elapsed=0.,settings=self.flow_set,fault=None,result=None,owner=self.__dict__.get('_caller'));self.pump['on']=False
            return dict(status=self._aux_status())
        if cap=='flow_test.stop':
            if not self.aux or self.aux['kind']!='flow_test':raise ActionRejected('PRECONDITION_FAILED')
            self._aux_done();return dict(status=self._aux_status())
        if cap=='pump.run':
            self._check_service()
            if 'flow_m3_s' in a:self.pump['set_m3_s']=a['flow_m3_s']
            if a['enabled'] and not self._pump_path():raise ActionRejected('PUMP_PRESSURE_TOO_HIGH')
            self.pump['on']=a['enabled']
            if not a['enabled']:self.pump['actual_m3_s']=0.
            return dict(pump=copy.deepcopy(self.pump))
        if cap=='valve.set':
            self._check_service();v,op=a['valve'],a['open'];V=self.valves
            if v=='solvent_all':V['solvent']=[op]*4
            elif v=='position_all':V['position']=[op]*6
            elif v.startswith('solvent_'):V['solvent'][int(v[-1])-1]=op
            elif v.startswith('position_'):V['position'][int(v[-1])-1]=op
            elif v.startswith('media_'):V['media']=v[6:] if op else (None if V['media']==v[6:] else V['media'])     # rotary: one path at a time
            else:V['outlet']=v[7:] if op else (None if V['outlet']==v[7:] else V['outlet'])
            if self.pump['on'] and not self._pump_path():self.pump.update(on=False,actual_m3_s=0.);self._notify('pump_pressure_too_high')
            return dict(valves=copy.deepcopy(V))
        if cap=='valve.calibrate':
            self._check_service();self.valves[a['valve']]=None;self.calibrations[a['valve']]+=1
            if self.pump['on'] and not self._pump_path():self.pump.update(on=False,actual_m3_s=0.);self._notify('pump_pressure_too_high')
            return dict(valves=copy.deepcopy(self.valves))
        if cap=='pressure.calibrate':
            self._check_service();self.sensor_zero=self._raw_pressures();return dict(sensors=self._sensors())
        if cap=='sensor.configure_error':
            self.sensor_error[a['sensor']]=float(a['offset_pa']);return dict(sensors=self._sensors())
        if cap=='seal.configure_leak':
            self.seal_leak[a['position']-1]=float(a['leak_pa_s']);return dict(position=a['position'],leak_pa_s=float(a['leak_pa_s']))
        if cap=='lift.move':
            self._check_service()
            if a['direction']=='up':
                if self.shield>1e-9:raise ActionRejected('SHIELD_OPEN')
                if a['lift']=='cell' and self.drawer>1e-9:raise ActionRejected('HEATER_NOT_IN_POSITION')
            self.lift_cmd[a['lift']]=None if a['direction']=='stop' else a['direction'];return dict(lifts=self._lifts())
        if cap=='service.reset_current_max':
            self.current_max=dict(cell=0.,vial=0.);return dict(counters=self._counters())
        if cap=='fan.set':
            if not self.powered:raise ActionRejected('PRECONDITION_FAILED')
            self.fans[a['fan']]=a['percent'];return dict(fans=dict(self.fans))
        if cap=='settings.configure':
            f=SETTINGS[a['key']];v=a['value']
            ok=(isinstance(v,bool) if f['type']=='bool' else v in f['options'] if f['type']=='enum' else
                (not isinstance(v,bool) and isinstance(v,(int,float)) and float(v).is_integer() and f['min']<=v<=f['max']))
            if not ok:raise ActionRejected('SETTING_INVALID')
            self.settings[a['key']]=int(v) if f['type']=='int' else v;return dict(settings=dict(self.settings))
        if cap=='solvents.set_name':
            if len(a['name'])>20 or not set(a['name'])<=NAME_CHARS:raise ActionRejected('NAME_INVALID')
            self.solvent_list[a['index']-1]=a['name'].strip();return dict(solvents=list(self.solvent_list))
        if cap=='solvents.load_default':
            self.solvent_list[:10]=list(DEFAULT_SOLVENTS);return dict(solvents=list(self.solvent_list))
        if cap=='vials.exchange':
            if self.rack>1e-9 or self.phase=='running' or not a['gripper_confirmed']:raise ActionRejected('PRECONDITION_FAILED')
            for c in list(self.cells.values())+list(self.blanks.values()):
                for i in range(4):self.exchanged[i]+=c['vial'][i];c['vial'][i]=0.
                c['collected']+=c['extract'];c['extract']=0.
            return dict(exchanged=True)
        if cap=='cell.insert_empty':
            i=a['position']-1
            if self.shield<.179 or self.drawer<.159 or not a['gripper_confirmed'] or i in self._records():raise ActionRejected('PRECONDITION_FAILED')
            self.blanks[i]=dict(blank=True,volume=a['cell_volume_m3'],liquid=[0.]*4,vial=[0.]*4,pressure=0.,dissolved=0.,extract=0.,wasted=0.,collected=0.,removed=False)
            return dict(occupancy=self._occupancy())
        if cap=='cell.remove_empty':
            i=a['position']-1;c=self.blanks.get(i)
            if self.shield<.179 or self.drawer<.159 or not a['gripper_confirmed'] or c is None or c['removed'] or c['pressure']>BAR:raise ActionRejected('PRECONDITION_FAILED')
            for k in range(4):self.removed_blank[k]+=c['liquid'][k]+c['vial'][k]
            del self.blanks[i];return dict(occupancy=self._occupancy())
        raise ActionRejected('UNKNOWN_CAPABILITY')

    def _check_service(self):
        if not self.powered or not self.plant:raise ActionRejected('PRECONDITION_FAILED')
        if self.phase=='running' or self.aux:raise ActionRejected('BUSY')
    def _check_aux_start(self):
        if not self.powered or not self.plant:raise ActionRejected('PRECONDITION_FAILED')
        if self.phase=='running' or self.aux:raise ActionRejected('BUSY')
        if self._in_operation():raise ActionRejected('PRECONDITION_FAILED')
        if self.shield>1e-9:raise ActionRejected('SHIELD_OPEN')
        if self.drawer>1e-9:raise ActionRejected('HEATER_NOT_IN_POSITION')
        if not self._active():raise ActionRejected('NO_POSITION_SELECTED')

    # ---- supplies (aux and service paths: shortfalls do not fail the extraction)
    def _supply(self,c,vol,f):
        v=min([vol]+[self.reservoir[i]/x for i,x in enumerate(f) if x>0])
        for i,x in enumerate(f):self.reservoir[i]-=v*x;c['liquid'][i]+=v*x
        return v+1e-15>=vol
    def _gas_use(self,n,h):
        req=self.plant['nitrogen_mol_s_per_cell']*n*h;x=min(self.nitrogen,req);self.nitrogen-=x;self.nitrogen_out+=x;return x+1e-12>=req
    def _drain_to(self,c,vol,holdup,target):
        if target=='waste':
            v=sum(c['liquid']);x=min(max(0.,v-holdup),vol);frac=x/v if v else 0.;m=c['dissolved']*frac;c['dissolved']-=m;c['wasted']+=m
            for i in range(4):y=c['liquid'][i]*frac;c['liquid'][i]-=y;self.waste[i]+=y
            return True
        v=sum(c['liquid']);x=min(max(0.,v-holdup),vol);space=self.plant['vial_capacity_m3']-sum(c['vial']);ok=x<=space+1e-15;x=min(x,max(0.,space))
        frac=x/v if v else 0.;m=c['dissolved']*frac;c['dissolved']-=m;c['extract']+=m
        for i in range(4):y=c['liquid'][i]*frac;c['liquid'][i]-=y;c['vial'][i]+=y
        return ok
    def _spill(self,c):
        """The cell lift opens on a pressurised cell (no safety checks in the service menu): the content sprays out (to the waste ledger)."""
        self._drain_to(c,sum(c['liquid']),0.,'waste');c['pressure']=0.

    # ---- leak test / flush / flow test
    def _aux_next(self,stage):
        self.aux['stage']=stage;self.aux['stage_elapsed']=0.
    def _aux_fail(self,code):
        self.aux['fault']=code;self._notify(code);self._aux_next('release' if self.aux['kind']!='flow_test' else 'done')
        if self.aux['kind']=='flow_test':self._aux_done()
    def _aux_done(self):
        a=self.aux;self.aux_last[a['kind']]=self._aux_status();self.aux=None
        if self.settings['beep_end']:self.end_beeps+=1
    def _aux_status(self,a=None):
        a=a or self.aux
        if a is None:return dict(active=False)
        return dict(active=True,kind=a['kind'],stage=a['stage'],stage_elapsed_s=a['stage_elapsed'],elapsed_s=a['elapsed'],fault=a['fault'],result=copy.deepcopy(a['result']),
                    hold_elapsed_s=a.get('hold_elapsed',0.),pressures_pa=self._readings()[1:],pump_pressure_pa=self._readings()[0])
    def _leak_result(self,stopped=False):
        rd=self._readings();act=self._active();pos=[rd[1+i] for i in act];setp=self.leak['pressure_gauge_pa']
        diff=max(pos)-min(pos) if pos else 0.;low=min(pos+[rd[0]])/setp if pos else 0.
        self.aux['result']=dict(positions=[i+1 for i in act],pressures_pa=pos,overall_pa=rd[0],max_pa=max(pos) if pos else 0.,max_difference_pa=diff,min_fraction_of_set=low,
                                passed=bool(pos) and diff<=15*BAR+1e-6 and low>=.95-1e-12,completed=not stopped)   # om p.43 criteria (first two)
    def _aux_tick(self,h):
        a=self.aux;p=self.plant;s=a['settings'];cells=self._run_cells();n=max(1,len(cells))
        a['elapsed']+=h;a['stage_elapsed']+=h;st=a['stage']
        if a['kind']=='flow_test':
            c=self._records().get(s['position']-1);v=50*MLMIN*h;f=[1. if i==s['port']-1 else 0. for i in range(4)]
            if c is None:return self._aux_fail('position_pressure_too_low:'+str(s['position']))
            ok=self._supply(c,v,f);self._drain_to(c,v,0.,s['flush_into']);c['pressure']=BACK_PA_PER_M3S*50*MLMIN;self.pump_p=c['pressure']
            if not ok:self._aux_fail('solvent_supply_empty')
            return
        if st=='sealing':
            self.heater=min(.035,self.heater+.01*h);self.rack=min(.035,self.rack+.01*h)
            if self.heater>=.035 and self.rack>=.035:self._aux_next('tightness')
        elif st=='tightness':                                                     # nitrogen pre-pressure 7 bar checks that the cells are there
            if not self._gas_use(n,h):return self._aux_fail('nitrogen_supply_empty')
            for c in cells:c['pressure']+=(7*BAR-c['pressure'])*-math.expm1(-h/p['pressure_tau_s'])
            if a['stage_elapsed']>=10:
                miss=self._missing()
                if miss:return self._aux_fail('position_pressure_too_low:'+str(miss[0]+1))
                self._aux_next('pump' if a['kind']=='leak_test' else 'solvent')
        elif st=='pump':
            for c in cells:
                if not self._supply(c,min(50*MLMIN/6*h,max(0.,self._free(c)-sum(c['liquid']))),s['solvent_fractions']):return self._aux_fail('solvent_supply_empty')
            if all(abs(sum(c['liquid'])-self._free(c))<1e-12 for c in cells):
                idx=self._run_indexed()                                           # the pump controls on the position sensors (their error and zero)
                for i,c in idx:c['pressure']+=(max(0.,s['pressure_gauge_pa']-self._offset(i))-c['pressure'])*-math.expm1(-h/p['pressure_tau_s'])
                self.pump_p=max(c['pressure'] for c in cells);rd=self._readings()
                if all(abs(rd[1+i]-s['pressure_gauge_pa'])<1e4 for i,_ in idx):self._aux_next('hold')
        elif st=='hold':
            a['hold_elapsed']=a['stage_elapsed']
            for i,c in self._records().items():
                if i in self._active():c['pressure']=max(0.,c['pressure']-self.seal_leak[i]*h)
            if a['stage_elapsed']>=s['hold_s']:self._leak_result();self._aux_next('discharge')
        elif st=='discharge':
            for c in cells:c['pressure']*=math.exp(-h/p['pressure_tau_s']);self._drain_to(c,p['discharge_m3_s_per_cell']*h,self._free(c)*p['residual_holdup_fraction'],s['flush_into'])
            if a['stage_elapsed']>=10 and self._measured_max()<=BAR and all(sum(c['liquid'])<=self._free(c)*p['residual_holdup_fraction']+1e-12 for c in cells):self._aux_next('gas')
        elif st=='solvent':                                                       # FLUSH: the set flow is not reduced when positions are off
            v=s['flow_m3_s']/n*h
            for c in cells:
                fill=max(0.,self._free(c)-sum(c['liquid']))
                if not self._supply(c,v,s['solvent_fractions']):return self._aux_fail('solvent_supply_empty')
                self._drain_to(c,max(0.,v-fill),0.,s['flush_into'])
            if a['stage_elapsed']>=s['solvent_s']:self._aux_next('gas')
        elif st=='gas':
            if not self._gas_use(n,h):return self._aux_fail('nitrogen_supply_empty')
            gas_s=s['gas_flush_s'] if a['kind']=='leak_test' else s['gas_s']
            for c in cells:self._drain_to(c,p['discharge_m3_s_per_cell']*h,0.,s['flush_into']);c['pressure']*=math.exp(-h/p['pressure_tau_s'])
            if a['stage_elapsed']>=gas_s and self._measured_max()<=BAR:self._aux_next('release')
        elif st=='release':
            for c in self._records().values():
                if c['pressure']>BAR and self.heater<.035:self._spill(c)
            self.heater=max(0.,self.heater-.01*h);self.rack=max(0.,self.rack-.01*h)
            if self.heater<=1e-12 and self.rack<=1e-12:self.heater=self.rack=0.;self._aux_done()
        if self.aux is not None and st=='hold':                                     # position valves closed: the overall pressure drops a little (om p.43)
            self.pump_p+=(OVERALL_HOLD*s['pressure_gauge_pa']-self.pump_p)*-math.expm1(-h/60.)
        elif self.aux is not None and st!='pump':self.pump_p*=math.exp(-h/p['pressure_tau_s'])

    # ---- service functions (instrument idle)
    def _pump_path(self):
        V=self.valves;return any(V['solvent']) and V['media']=='solvent' and any(V['position']) and V['outlet'] is not None
    def _service_tick(self,h):
        p=self.plant;V=self.valves;recs=self._records();sealed=self.heater>=.035-1e-9
        for lift in ('cell','vial'):
            cmd=self.lift_cmd[lift];attr='heater' if lift=='cell' else 'rack';x=getattr(self,attr)
            if cmd:
                nx=min(.035,x+LIFT_SPEED*h) if cmd=='up' else max(0.,x-LIFT_SPEED*h);setattr(self,attr,nx);self.current_max[lift]=max(self.current_max[lift],LIFT_CURRENT_A)
                if nx in (0.,.035):self.lift_cmd[lift]=None
        if self.heater<.035-1e-9:
            for c in recs.values():
                if c['pressure']>BAR:self._spill(c)
        open_pos=[i for i in range(6) if V['position'][i]]
        if V['outlet'] and sealed:                                                 # an open position valve and outlet valve relieve that cell
            gas=V['media']=='gas'
            if gas and open_pos and not self._gas_use(len(open_pos),h):self._notify('nitrogen_supply_empty');V['media']=None
            for i in open_pos:
                c=recs.get(i)
                if c is None:continue
                c['pressure']*=math.exp(-h/p['pressure_tau_s']);self._drain_to(c,p['discharge_m3_s_per_cell']*h,0. if gas else self._free(c)*p['residual_holdup_fraction'],V['outlet'])
        P=self.pump
        if P['on']:
            if not self._pump_path():P.update(on=False,actual_m3_s=0.);self._notify('pump_pressure_too_high');return
            P['actual_m3_s']+=(P['set_m3_s']-P['actual_m3_s'])*-math.expm1(-h/2.)
            opn=[i for i in range(4) if V['solvent'][i]];f=[1./len(opn) if i in opn else 0. for i in range(4)];per=P['actual_m3_s']/len(open_pos)*h
            for i in open_pos:
                c=recs.get(i)
                if c is not None and sealed:
                    ok=self._supply(c,per,f);fill=max(0.,self._free(c)-sum(c['liquid']));self._drain_to(c,per,0.,V['outlet']) if not fill else None
                    c['pressure']=BACK_PA_PER_M3S*P['actual_m3_s']/len(open_pos)
                else:                                                             # no cell or lift open: the solvent runs into the instrument
                    tmp=dict(liquid=[0.]*4,dissolved=0.,wasted=0.);ok=self._supply(tmp,per,f);self._drain_to(tmp,per,0.,'waste')
                if not ok:P.update(on=False,actual_m3_s=0.);self._notify('solvent_supply_empty');break
            self.pump_p=BACK_PA_PER_M3S*P['actual_m3_s']/max(1,len(open_pos))
        else:
            self.pump_p*=math.exp(-h/p['pressure_tau_s'])

    # ---- readings and views
    def _raw_pressures(self):
        r=self._records();true=[self.pump_p]+[r[i]['pressure'] if i in r else 0. for i in range(6)]
        return [t+e for t,e in zip(true,self.sensor_error)]
    def _readings(self):
        return [x-z for x,z in zip(self._raw_pressures(),self.sensor_zero)]
    def _offset(self,i):
        """Reading minus true pressure of the sensor of position i (0-5): evaluator error minus the stored service zero."""
        return self.sensor_error[1+i]-self.sensor_zero[1+i]
    def _measured_max(self):
        """Highest position pressure as the instrument measures it (sensors 1-6 with error and zero, not below 0): the display, pressure.max_gauge_pa,
        extraction.measure, the operating-hours peak and the device's own decisions (skip, release, control) use it; _maxp() stays the true cell
        pressure (physics, host interlocks and the truth slot)."""
        return max(0.,max(self._readings()[1:]))
    def _run_indexed(self):
        r=self._records();return [(i,r[i]) for i in self._active() if i in r]
    def _sensors(self):
        return dict(readings_pa=self._readings(),zero_pa=list(self.sensor_zero))
    def _lifts(self):
        return dict(cell=self.heater,vial=self.rack,moving=dict(self.lift_cmd),current_a={k:(LIFT_CURRENT_A if v else 0.) for k,v in self.lift_cmd.items()})
    def _counters(self):
        return dict(self.counters,current_max_a=dict(self.current_max))
    def _counters_tick(self,h,heat):
        c=self.counters
        if self.powered:
            c['operating_s']+=h
            if heat:c['heater_s']+=h
            c['max_temperature_k']=max(c['max_temperature_k'] or 0.,self.temperature)
        c['max_pressure_pa']=max(c['max_pressure_pa'],self._measured_max())
    def _valve_view(self):
        """Valves as the STATUS diagram shows them: the automatic sequence while a process runs, else the service-menu states."""
        st=self.aux['stage'] if self.aux else (self.stage if self.phase=='running' else None)
        if st is None:return copy.deepcopy(self.valves)
        kind=self.aux['kind'] if self.aux else 'extraction';act=self._active()
        f=self.aux['settings'].get('solvent_fractions',[0.]*4) if self.aux else self._fractions() if self.method else [0.]*4
        if kind=='flow_test':
            s=self.aux['settings'];return dict(solvent=[i==s['port']-1 for i in range(4)],media='solvent',position=[i==s['position']-1 for i in range(6)],outlet=s['flush_into'])
        liquid=st in ('fill','heatup_1','heatup_2','heatup_3','solvent_flush','pump','solvent');gas=st in ('tightness','nitrogen_flush','gas')
        into=(self.aux['settings']['flush_into'] if self.aux else ('waste' if (self.method or {}).get('vial')=='waste' else 'vial'))
        return dict(solvent=[liquid and f[i]>0 for i in range(4)],media='solvent' if liquid else 'gas' if gas else None,
                    position=[i in act and st not in ('hold','sealing','release') for i in range(6)],
                    outlet=into if st in ('discharge','solvent_flush','nitrogen_flush','solvent','gas') else None)
    def _pumping(self):
        st=self.aux['stage'] if self.aux else (self.stage if self.phase=='running' else None)
        return self.pump['on'] or st in ('fill','heatup_1','heatup_2','heatup_3','solvent_flush','pump','solvent','running')
    def _status_text(self):
        if not self.powered:return ''
        if self.aux:return {'leak_test':'Leak test','flush':'Flush','flow_test':'Flow test'}[self.aux['kind']]
        if self.phase=='failed':return 'Error'
        if self._operation_count and self.stage not in ('standby','complete'):
            if self.stage=='halted':return 'Stop'
            if self.phase=='cancelled':return 'Vial change' if self.stage=='vial_change' else 'Pause'
            if self.aborting:return 'Abort'
            return STAGE_TEXT.get(self.stage,self.stage)
        tgt=self.preheat_k if self.preheat_k is not None else (self.method['temperature_k'] if self.method else None)
        return 'Preheat' if self.preheat and tgt is not None and abs(self.temperature-tgt)>1 else 'Ready'
    def _plan(self):
        """Rough stage plan of the current method for the remaining-time displays (the device also estimates; om p.52)."""
        m=self._full_method();cells=self._run_cells() or list(self.cells.values());n=max(1,len(cells))
        free=max([self._free(c) for c in cells] or [(m['cell_volume_m3'] or 20e-6)*.75]);vol=m['cell_volume_m3'] or (self._cell_volume(cells) if cells else 20e-6)
        fill=free/(m['pump_total_m3_s']/6);dwell=10*vol/20e-6;plan=[('sealing',0,3.5),('tightness',0,10.)]
        for k in range(1,m['cycles']+1):
            s=m['cycle_steps'][k-1];plan+=[('fill',k,fill),('heatup_1',k,dwell),('heatup_2',k,dwell),('heatup_3',k,dwell),('hold',k,s['hold_s']),('discharge',k,max(s['discharge_s'],free*n/(self.plant['discharge_m3_s_per_cell'] if self.plant else 2e-6)/n))]
            if k<m['cycles'] and s['vial_change']:plan.append(('vial_change',k,7.))
        if m['solvent_flush_s']>0:plan.append(('solvent_flush',m['cycles'],m['solvent_flush_s']))
        return plan+[('nitrogen_flush',m['cycles'],m['nitrogen_flush_s']),('release',m['cycles'],3.5)]
    def _times(self):
        """(time to the end of the cycle, time to the next vial change, time to the end) in s; None when nothing runs."""
        if not (self._operation_count and self.stage not in ('standby','complete','halted')) or self.method is None:return None,None,None
        plan=self._plan();cyc=max(1,self.cycle)
        idx=next((j for j,(s,k,_) in enumerate(plan) if s==self.stage and (k==cyc or k==0 or s in ('solvent_flush','nitrogen_flush','release'))),None)
        if idx is None:return None,None,None
        rest=[max(0.,plan[idx][2]-self.stage_elapsed)]+[d for _,_,d in plan[idx+1:]];names=[s for s,_,_ in plan[idx:]];cycles=[k for _,k,_ in plan[idx:]]
        total=sum(rest);end_c=sum(d for d,s,k in zip(rest,names,cycles) if k<=cyc and s not in ('solvent_flush','nitrogen_flush','release','vial_change'))
        vc=next((sum(rest[:j]) for j,s in enumerate(names) if s=='vial_change' and j>0),None)
        return end_c,vc,total


CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet'],'电源；断电关闭主动阀，不消除残压','device_command','agent',{'enabled':True},['power.enabled','cycle.phase','thermal.indicated_k','pressure.max_gauge_pa','thermal.eco_mode','service.pump','service.lifts','leak_test.status','flush.status','flow_test.status']),
 'shield.set_lift':(obj({'lift_m':number('m',0,.18)}),obj({'lift_m':number('m',0,.18)}),['shield'],'上推屏；只在非运行、脱压、两升降架降下时移动','device_command','agent',{'lift_m':.18},['shield.lift_m']),
 'drawer.set_extension':(obj({'extension_m':number('m',0,.16)}),obj({'extension_m':number('m',0,.16)}),['heater_drawer'],'上推屏后向前抽加热块','device_command','agent',{'extension_m':.16},['drawer.extension_m']),
 'plant.configure':(PLANT,obj({'configured':BOOL}),['pump','mixer','nitrogen_inlet','exhaust'],'有限四溶剂/氮气、宿主工程系数；仅首次','configuration','evaluator',EXAMPLE,['plant.configured']),
 'method.configure':(METHOD,obj({'configured':BOOL}),['panel','pump','heater_lift'],'温压循环时间、单泵总流量、四溶剂体积分数、氮吹；可选vial（收集瓶60/150/220/240 mL、unspecified、waste）、cell_volume_m3、solvent_names与cycle_steps（各周期保持/排液/换瓶/溶剂口，EDIT METHOD，om p.52-56）','configuration','agent',METHOD_EXAMPLE,['method.configured','thermal.target_k','method.current','method.modified']),
 'thermal.preheat':(obj({'temperature_k':number('K',303.15,473.15),'enabled':BOOL},optional=('temperature_k','enabled')),obj({'enabled':BOOL}),['heater_lift'],'装池前预热；step推进，不能装冷池后密封加热；可选temperature_k（PREHEAT菜单的预热温度，缺省跟随方法温度）与enabled=false（OFF，随时可关；带temperature_k只存设定）','device_command','agent',{},['thermal.preheat_enabled','thermal.preheat_target_k']),
 'sample.configure':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['heater_lift','cell_0','cell_1','cell_2','cell_3','cell_4','cell_5'],'宿主装入干燥/已审查固体；六位置必须同容量','configuration','evaluator',SAMPLE_EXAMPLE,['sample.configured','cells.occupancy']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['pump','media_valve','outlet_valve','heater_lift','collection_rack'],'密封、气密代理、充液、分级升压、保持、排液、最终冲洗/氮吹、解封','device_command','agent',{},['cycle.phase','cycle.stage','service.valves','service.pump']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['media_valve','pump'],'暂停/取消主动动作；保留残压/库存，热和传质继续','device_command','agent',{},['cycle.phase']),
 'acquisition.abort':(obj({}),obj({'phase':PHASE}),['media_valve','outlet_valve'],'宿主有序终止：排液、氮吹、解封；不是第三次急停固件','device_command','agent',{},['cycle.phase','cycle.stage']),
 'extraction.measure':(obj({}),RESULT,['display_digits','pressure_sensor_0'],'仪器读数及阶段：压力为位置传感器读数的最大值（含评测者偏差与服务校零，默认理想）；不是校准仪表','query','agent',{},[]),
 'product.read':(obj({}),PRODUCT,['collection_rack','heater_lift'],'宿主条件回收率与真实跨仪器参考比值分开','query','evaluator',{},[]),
 'reference.lookup':(obj({'substance_id':TEXT}),REFERENCE,['display_digits'],'54真实ASE/Soxtec比较数据，不能当本机绝对回收率','query','agent',{'substance_id':'phenol'},[]),
 'sample.unload':(obj({'position':INT(0,5),'sample_id':TEXT,'gripper_confirmed':BOOL}),obj({'unloaded':BOOL,'attachment_event':TEXT}),['heater_lift','collection_rack'],'完整或有序终止后，开屏抽块，用夹具取池','configuration','evaluator',{'position':0,'sample_id':'soil_1','gripper_confirmed':True},['truth.removed_target_kg','cells.occupancy'])}
METHOD['properties'].update(METHOD_EXTRA);CAPS.update(DEVICE_CAPS)
SLOTS={}
def slot(k,s,u,o='derived',v='agent',c='display_digits',w=None):SLOTS[k]=(s,u,o,v,[c],w)
slot('power.enabled',BOOL,'1','setpoint',c='power_inlet',w=('power.set','enabled'));slot('shield.lift_m',number('m',0,.18),'m','scene',c='shield',w=('shield.set_lift','lift_m'));slot('drawer.extension_m',number('m',0,.16),'m','scene',c='heater_drawer',w=('drawer.set_extension','extension_m'))
for k,c in [('heater.lift_m','heater_lift'),('rack.lift_m','collection_rack')]:slot(k,number('m',0,.035),'m','scene',c=c)
for k,s,u in [('plant.configured',BOOL,'1'),('sample.configured',BOOL,'1'),('method.configured',BOOL,'1'),('thermal.preheat_enabled',BOOL,'1'),('thermal.target_k',nullable(TEMP),'K'),('thermal.indicated_k',nullable(TEMP),'K'),('pressure.max_gauge_pa',nullable(PRESS),'Pa'),('cycle.phase',PHASE,'1'),('cycle.stage',TEXT,'1'),('cycle.fault',TEXT,'1'),('cycle.index',INT(0,10),'1'),('cycle.elapsed_s',number('s',0),'s'),('cycle.stage_elapsed_s',number('s',0),'s')]:slot(k,s,u)
for k,u in [('initial_target_kg','kg'),('sorbed_target_kg','kg'),('dissolved_target_kg','kg'),('extract_target_kg','kg'),('degraded_target_kg','kg'),('removed_target_kg','kg'),('initial_solvent_m3','m^3'),('reservoir_solvent_m3','m^3'),('cell_solvent_m3','m^3'),('extract_solvent_m3','m^3'),('waste_solvent_m3','m^3'),('removed_solvent_m3','m^3'),('initial_nitrogen_mol','mol'),('remaining_nitrogen_mol','mol'),('exhaust_nitrogen_mol','mol'),('max_pressure_gauge_pa','Pa')]:slot('truth.'+k,number(u,0),u,'process_truth','evaluator','heater_lift')
NEW_SLOT_ZH={'positions.active':'激活位置（6个布尔；null=从未设定，1.0接口：装了样品池的位置都参与）','cells.occupancy':'各位置所放的池：sample样品池、empty空池或无',
 'method.current':'当前方法全部参数（未设定时为DEFAULT方法；含瓶、池、溶剂名与各周期参数）','method.name':'当前方法名（主屏标题）','method.modified':'当前方法改过且未保存（主屏标题划掉）',
 'method.library':'方法表（号与名称，最多100个）','thermal.preheat_target_k':'PREHEAT菜单的预热温度（null=跟随方法温度）','thermal.eco_mode':'EcoMode：下一次萃取结束后关加热',
 'leak_test.settings':'检漏参数（上次所用）','leak_test.status':'检漏状态：阶段、保持时间、各位压力读数、结果（各位最大差、最低占设定比例、是否符合手册判据）',
 'flush.settings':'冲洗参数','flush.status':'冲洗状态','flow_test.settings':'流动测试参数','flow_test.status':'流动测试状态（背压、时间）',
 'service.valves':'服务菜单阀门状态（溶剂阀、介质阀、位置阀、出液阀）','service.pump':'服务菜单泵：开关、设定与实际流量','service.lifts':'池/瓶升降位置、运动方向与电机电流',
 'service.fans':'两个风扇的功率设定（%）','service.counters':'运行次数与时间：萃取、检漏、运行/加热时间、最高温度/压力、升降电机最大电流','pressure.sensors':'七个压力传感器读数与零点（泵压+6位置）',
 'device.settings':'仪器设定：语言、对比度、按键蜂鸣、结束蜂鸣、开机预热询问','solvents.list':'溶剂表（20条名称）'}
for k,s,u,o,c,w in [('positions.active',nullable(POS6),'1','setpoint','heater_lift',None),('cells.occupancy',{'type':'array','items':nullable(TEXT)},'1','scene','heater_lift',None),
                    ('method.current',FREE,'1','setpoint','panel',None),('method.name',TEXT,'1','setpoint','panel',None),('method.modified',BOOL,'1','derived','panel',None),
                    ('method.library',{'type':'array','items':FREE},'1','setpoint','panel',None),('thermal.preheat_target_k',nullable(TEMP),'K','setpoint','heater_lift',None),
                    ('thermal.eco_mode',BOOL,'1','setpoint','heater_lift',('thermal.set_eco_mode','enabled')),('leak_test.settings',FREE,'1','setpoint','panel',None),
                    ('leak_test.status',FREE,'1','derived','pressure_sensor_0',None),('flush.settings',FREE,'1','setpoint','panel',None),('flush.status',FREE,'1','derived','pump',None),
                    ('flow_test.settings',FREE,'1','setpoint','panel',None),('flow_test.status',FREE,'1','derived','pump',None),('service.valves',FREE,'1','setpoint','media_valve',None),
                    ('service.pump',FREE,'1','derived','pump',None),('service.lifts',FREE,'1','derived','heater_lift',None),('service.fans',FREE,'1','setpoint','rear_fan',None),
                    ('service.counters',FREE,'1','derived','panel',None),('pressure.sensors',FREE,'Pa','derived','pressure_sensor_0',None),('device.settings',FREE,'1','setpoint','panel',None),
                    ('solvents.list',{'type':'array','items':TEXT},'1','setpoint','panel',None)]:slot(k,s,u,o,c=c,w=w)
NEW_SLOTS=list(NEW_SLOT_ZH)

# ---------------------------------------------------------------- front panel (panel_ui.json beside the contract, generic engine _panel_ui.py)
ASSET_VERSION='1.2.0';ASSET_DIR=ROOT/'assets/expansion_batch03/t2_vacuum/buchi_speedextractore'
NOTICE_TEXT={'solvent_supply_empty':('Error 66',['Pressure not reached. Pump timeout.','Please check solvent reservoir.','Please refer to user manual.']),
             'nitrogen_supply_empty':('Error 59',['Nitrogen inlet pressure out of range.','Please check nitrogen supply.','']),
             'collection_vial_full':('Error 4',['Change vials.','','']),'change_vials':('Error 4',['Change vials.','Press START to continue.','']),
             'pump_pressure_too_high':('Error 51',['Pump pressure too high.','Please refer to user manual.',''])}   # om p.70-74


def _notice_text(code):
    if code.startswith('position_pressure_too_low:'):
        n=int(code.split(':')[1]);return f'Error {44+n}',[f'Position {n}: Pressure too low.','Please check whether a cell','is inserted.']
    return NOTICE_TEXT.get(code,('Error',[code,'','']))


def _panel_engine():
    try:
        from . import _panel_ui as engine                                   # imported as asset_runtime.buchi_speedextractore
        return engine
    except ImportError:                                                      # loaded from its file path
        import importlib.util,sys
        if '_chemsim_panel_ui' not in sys.modules:
            spec=importlib.util.spec_from_file_location('_chemsim_panel_ui',Path(__file__).with_name('_panel_ui.py'));sys.modules['_chemsim_panel_ui']=importlib.util.module_from_spec(spec);spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
        return sys.modules['_chemsim_panel_ui']


# hooks of the panel spec: compose whole-method / list arguments from one edited value (the commands themselves check everything)
LOCKED=[{'do':'set_var','var':'msg','value':{'const':'locked'}},{'do':'goto','screen':'message'}]
def _write_method(panel,m,ctx):
    return panel._invoke('method.configure',m,{'PRECONDITION_FAILED':LOCKED},ctx)
def _hook_method_set(panel,ctx,a):
    h=panel.host;m=h._method_args();v=panel._src(a['value'],ctx);f=a['field']
    if f=='cycles':
        n=int(round(v));steps=m['cycle_steps'];m['cycle_steps']=(steps+[copy.deepcopy(steps[-1]) for _ in range(n)])[:n];m['cycles']=n
    elif v is None:m.pop(f,None)
    else:m[f]=v
    return _write_method(panel,h._sync_steps(m),ctx)
def _hook_method_default(panel,ctx,a):
    return _write_method(panel,copy.deepcopy(DEFAULT_METHOD),ctx)
def _hook_cycle_value(panel,ctx,s):
    k=int(panel.ui['vars'].get('cyc') or 1);steps=panel.host._full_method()['cycle_steps'];return steps[min(k,len(steps))-1][s['param']]
def _hook_cycle_set(panel,ctx,a):
    h=panel.host;m=h._method_args();k=int(panel.ui['vars'].get('cyc') or 1);v=panel._src(a['value'],ctx)
    if k>m['cycles']:return False
    m['cycle_steps'][k-1][a['key']]=int(v) if a['key']=='port' else bool(v) if a['key']=='vial_change' else float(v)
    return _write_method(panel,h._sync_steps(m),ctx)
def _hook_cycle_paste(panel,ctx,a):
    h=panel.host;m=h._method_args();src=panel.ui['vars'].get('clip');it=panel._current_item()[0]
    if not src or not it or it.get('cycle',99)>m['cycles'] or src>m['cycles']:return False
    m['cycle_steps'][it['cycle']-1]=copy.deepcopy(m['cycle_steps'][src-1]);return _write_method(panel,h._sync_steps(m),ctx)
def _hook_solvent_open(panel,ctx,a):
    m=panel.host._full_method();V=panel.ui['vars']
    for k in range(4):V[f'sol_n{k+1}']=m['solvent_names'][k];V[f'sol_r{k+1}']=int(round(m['solvent_fractions'][k]*100))
    return True
def _write_solvents(panel,ctx):
    V=panel.ui['vars'];r=[V[f'sol_r{k}'] for k in range(1,5)]
    if sum(r)!=100:return True                                                 # kept on the screen until the ratios sum to 100 % (om p.53)
    h=panel.host;m=h._method_args();m['solvent_fractions']=[x/100. for x in r];m['solvent_names']=[V[f'sol_n{k}'] for k in range(1,5)]
    return _write_method(panel,h._sync_steps(m),ctx)
def _hook_solvent_store(panel,ctx,a):
    V=panel.ui['vars']
    for fid,val in (ctx.get('buffer') or {}).items():
        if fid.startswith('type'):V['sol_n'+fid[4:]]=val
        elif fid.startswith('ratio'):V['sol_r'+fid[5:]]=int(round(val))
    return _write_solvents(panel,ctx)
def _hook_solvent_name(panel,ctx,s):
    return panel.ui['vars'].get(f"sol_n{panel.ui['vars'].get('name_port') or 1}",'')
def _hook_solvent_rename(panel,ctx,a):
    V=panel.ui['vars'];V[f"sol_n{V.get('name_port') or 1}"]=V.get('name_buf') or '';return _write_solvents(panel,ctx)
def _hook_list_name(panel,ctx,s):
    return panel.host.solvent_list[int(panel.ui['vars'].get('list_index') or 1)-1]
def _hook_slot_name(panel,ctx,s):
    it=panel._current_item()[0];h=panel.host
    return h.library[it['slot']]['name'] if it and it.get('slot') in h.library else h.method_name
def _hook_grid_select(panel,ctx,a):
    V=panel.ui['vars'];ch=a['chars'][int(V.get('grid_pos') or 0)];buf=V.get('name_buf') or ''
    limit=16 if V.get('name_target')=='method' else 20
    if ch=='BACKSPACE':V['name_buf']=buf[:-1];return True
    if len(buf)>=limit:panel._accepted=False;return True
    V['name_buf']=buf+ch;return True
def _hook_aux_ratio(panel,ctx,a):
    cap=a['capability'];h=panel.host;store=h.leak if cap=='leak_test.configure' else h.flush_set
    f=list(store['solvent_fractions']);f[a['port']-1]=max(0.,min(1.,panel._src(a['value'],ctx)/100.))
    return panel._invoke(cap,dict(solvent_fractions=f),None,ctx)
PANEL_HOOKS={k[6:]:v for k,v in dict(globals()).items() if k.startswith('_hook_')}

_panel_ui=_panel_engine()
PANEL_SPEC=_panel_ui.load_spec(ASSET_DIR/'panel_ui.json',capabilities=set(CAPS)) if (ASSET_DIR/'panel_ui.json').is_file() else None
PANEL_CAPS=_panel_ui.capability_definitions(PANEL_SPEC) if PANEL_SPEC else {}
_PANEL_FX=list(dict.fromkeys(e for c in (_panel_ui.spec_capabilities(PANEL_SPEC) if PANEL_SPEC else []) for e in CAPS[c][7]))
for _k,_d in PANEL_CAPS.items():
    CAPS[_k]=(_d['input_schema'],_d['output_schema'],_d['component_ids'],_d['function_zh'],_d['kind'],_d['visibility'],_d['example_arguments'],[] if _d['kind']=='query' else list(_PANEL_FX))


def _hms(s):
    if s is None:return '--:--:--'
    s=int(round(s));return f'{s//3600:02d}:{s%3600//60:02d}:{s%60:02d}'
def _bar(pa):return f'{max(0.,pa)/BAR:.1f} bar'


class _E916Panel:
    """Panel-host protocol of the engine: what the controller firmware shows, and the panel.* commands."""
    def _method_args(self):
        m=self._full_method();return {k:v for k,v in m.items() if v is not None}
    def _sync_steps(self,m):
        s=m['cycle_steps'][0];m['hold_s']=float(s['hold_s']);m['discharge_s']=float(s['discharge_s']);return m
    def panel_time(self):return self.time_s
    def panel_invoke(self,capability_id,arguments):
        try:
            validate(CAPS[capability_id][0],arguments);return self.invoke(capability_id,**arguments)
        except ActionRejected as exc:
            raise _panel_ui.PanelRejected(str(exc)) from None
    def _panel_cmd(self,cap,a):
        err=_panel_ui.check_arguments(CAPS[cap][0],a)
        if err:raise ActionRejected('INVALID_ARGUMENT')
        if cap=='panel.frame':return dict(frame=self.panel.frame())
        if cap=='panel.capture':
            import hashlib,tempfile
            data,w,h=self.panel.png_bytes(a['display_id']);sha=hashlib.sha256(data).hexdigest()
            d=Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir())/'chemistry_sim_panel'/self.instance_id;d.mkdir(parents=True,exist_ok=True)
            path=d/f"{a['display_id']}_{sha[:16]}.png"
            if not path.exists():path.write_bytes(data)
            return dict(display_id=a['display_id'],time_s=self.time_s,image=dict(path=str(path),format='png',width=w,height=h,sha256=sha))
        try:
            if cap=='panel.press':ok=self.panel.press(a['input_id'],float(a.get('duration_s',.1)))
            elif cap=='panel.turn':ok=self.panel.turn(a['input_id'],int(a['detents']))
            else:ok=self.panel.switch(a['input_id'],a['position'])
        except _panel_ui.PanelError:
            raise ActionRejected('INVALID_ARGUMENT') from None
        if ok and cap=='panel.press' and self.settings['beep_key']:self.key_beeps+=1
        return dict(accepted=bool(ok))
    def panel_read(self):
        out=self.read('agent');m=self._full_method();rd=self._readings();act=self._active();occ=self._occupancy()
        end_c,vc,total=self._times() if self.plant else (None,None,None)
        V=self._valve_view();on=lambda b:'[x]' if b else '[ ]'  # noqa: E731
        skip=(self.phase=='running' and (self.stage in ('hold','solvent_flush') or (self.stage=='nitrogen_flush' and self.stage_elapsed>=60 and self._measured_max()<=BAR)))
        skip_leak=bool(self.aux and self.aux['kind']=='leak_test' and self.aux['stage'] in ('tightness','pump','hold'))
        cells=self._run_cells();vol=self._cell_volume(cells) if (cells or self.cells or (self.method or {}).get('cell_volume_m3')) else (m['cell_volume_m3'] or 20e-6)
        rows=[]
        for k in range(1,11):
            if k>m['cycles']:rows.append(f'{k:>3}    n/a     n/a     n/a    n/a  Mix');continue
            s=m['cycle_steps'][k-1];mn=lambda x:(f'{x/60:.0f} min' if abs(x/60-round(x/60))<1e-9 else f'{x/60:.1f} min')  # noqa: E731
            rows.append(f"{k:>3} {max(1,math.ceil(10*vol/20e-6/60)):>3} min {mn(s['hold_s']):>7} {mn(s['discharge_s']):>9}  {on(s['vial_change'])}  {'P'+str(s['port']) if s['port'] else 'Mix'}")
        lt=self.aux if (self.aux and self.aux['kind']=='leak_test') else None;last=self.aux_last.get('leak_test')
        lr_pos=[rd[1+i] for i in range(6)]
        names=[n for n in self.solvent_list if n]+[n for n in m['solvent_names'] if n and n not in self.solvent_list]
        notice=None
        if self.notice:
            t,ls=_notice_text(self.notice['code']);notice=dict(id=self.notice['id'],title=t,lines=ls)
        c=self.counters;hm=lambda s:f'{int(s)//3600}:{int(s)%3600//60:02d} h:m'  # noqa: E731
        lifts=self._lifts();amb=self.plant['ambient_k'] if self.plant else 295.15
        stage=(STAGE_TEXT.get(lt['stage'],lt['stage']) if lt else ('Finished' if last else 'Ready'))
        out.update({
            'host.power_epoch':self.power_epoch,'host.head':f"{self.cycle} / {m['cycles']}",'host.name':self.method_name,'host.modified':self.method_modified,
            'host.remain':_hms(total),'host.status':self._status_text(),'host.heating':bool(self.powered and self.preheat),'host.pumping':bool(self.powered and self._pumping()),
            'host.active':[i in act for i in range(6)],'host.any_active':bool(act),'host.temp_c':round(self.temperature-273.15) if self.powered else None,
            'host.max_bar':max(0.,max(rd[1:]))/BAR,'host.skip':bool(skip or skip_leak),'host.skip_leak':skip_leak,'host.in_operation':self._in_operation(),
            'host.aborting':bool(self.aborting),'host.ready':bool(self.method and abs(self.temperature-self.method['temperature_k'])<=1),
            'host.progress':[_hms(end_c),_hms(vc),_hms(total)],'host.overflow':self._overflow_possible(),'host.cycle_rows':rows,
            'host.heatup_text':f'{max(1,math.ceil(10*vol/20e-6/60))}min','host.library':[self.library[k]['name'] if k in self.library else '' for k in range(1,101)],
            'host.library_slots':sorted(self.library),'host.solvent_options':[{'name':n} for n in names],
            'host.diagram':[
                'Solvent valves  '+' '.join(f'{i+1}{on(V["solvent"][i])}' for i in range(4)),
                'Media valve     '+{'solvent':'Solvent','gas':'Gas',None:'closed'}[V['media']],
                'Pump            '+(f"on {round((self.pump['actual_m3_s'] if self.pump['on'] else (self.method or m)['pump_total_m3_s'])*6e7)} mL/min" if self._pumping() else 'off'),
                'Position valves '+' '.join(f'{i+1}{on(V["position"][i])}' for i in range(6)),
                'Cells           '+' '.join({'sample':'S','empty':'E',None:'-'}[occ[i]] for i in range(6)),
                'Pressure [bar]  '+' '.join(f'{max(0.,x)/BAR:.0f}' for x in rd[1:]),
                'Outlet valve    '+{'vial':'Vial','waste':'Waste',None:'closed'}[V['outlet']],
                'Total pressure  '+_bar(rd[0]),'',''],
            'host.leak_run':dict(stage=stage,overall=f'{max(0.,rd[0])/BAR:.0f}',cells='  '.join(f'[{i+1}]' if i in act else ' -- ' for i in range(6)),
                                 pressures='  '.join(f'{max(0.,x)/BAR:3.0f}' if i in act else '   ' for i,x in enumerate(lr_pos)),
                                 hold=(f"{int(lt['hold_elapsed'])//60:02d}:{int(lt['hold_elapsed'])%60:02d}" if lt else '00:00'),
                                 max=f'{max([lr_pos[i] for i in act] or [0.])/BAR:.0f} bar'),
            'host.valve_text':{**{f'solvent_{i+1}':'[open]' if self.valves['solvent'][i] else '[close]' for i in range(4)},
                               **{f'position_{i+1}':'[open]' if self.valves['position'][i] else '[close]' for i in range(6)},
                               'media_solvent':'[open]' if self.valves['media']=='solvent' else '[close]','media_gas':'[open]' if self.valves['media']=='gas' else '[close]',
                               'outlet_vial':'[open]' if self.valves['outlet']=='vial' else '[close]','outlet_waste':'[open]' if self.valves['outlet']=='waste' else '[close]'},
            'host.lights':{'cell_upper':'[X]' if self.heater>=.035-1e-9 else '[ ]','cell_lower':'[X]' if self.heater<=1e-9 else '[ ]',
                           'vial_upper':'[X]' if self.rack>=.035-1e-9 else '[ ]','vial_lower':'[X]' if self.rack<=1e-9 else '[ ]',
                           'heater_middle':'[X]' if self.drawer<=1e-9 else '[ ]','shield_upper':'[X]' if self.shield>=.179 else '[ ]',
                           'shield_lower':'[X]' if self.shield<=1e-9 else '[ ]','rack_present':'[X]'},
            'host.pressures':[_bar(x) for x in rd],'host.temps':dict(heater=f'{self.temperature-273.15:.0f}°C',electronic=f'{amb-273.15+5:.0f}°C' if self.powered else '---'),
            'host.pump':dict(actual=f"{self.pump['actual_m3_s']*6e7:.0f} mL/min",pressure=_bar(rd[0])),
            'host.flow':dict(pressure=_bar(rd[self.flow_set['position']]) if (self.aux and self.aux['kind']=='flow_test') else _bar(0.),
                             time=f"{int(self.aux['elapsed'])} s" if (self.aux and self.aux['kind']=='flow_test') else '0 s'),
            'host.lift_current':f"{max(lifts['current_a'].values()):.3f} A",
            'host.counters':dict(extractions=str(c['extractions']),leak_tests=str(c['leak_tests']),operating=hm(c['operating_s']),heater=hm(c['heater_s']),
                                 cell_ma=f"{self.current_max['cell']*1000:.0f} mA",vial_ma=f"{self.current_max['vial']*1000:.0f} mA",
                                 max_t=f"{c['max_temperature_k']-273.15:.0f} °C" if c['max_temperature_k'] else '---',max_p=_bar(c['max_pressure_pa'])),
            'host.unit':dict(UNIT_INFO),'host.notice':notice})
        return out


class BuchiSpeedextractoreRuntime(_E916Device,_E916Panel):
 def __init__(self,instance_id='speedextractor_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.shield=0.;self.drawer=0.;self.heater=0.;self.rack=0.;self.plant=None;self.method=None;self.cells={};self.phase='idle';self.stage='standby';self.fault='';self.elapsed_s=0.;self.stage_elapsed=0.;self.cycle=0;self.operation_id=None;self._operation_count=0;self.preheat=False;self.temperature=295.15;self.reservoir=[0.]*4;self.waste=[0.]*4;self.nitrogen=0.;self.nitrogen_out=0.;self.aborting=False;self.history=[];self.next_sample=0.;self.templates={x['substance_id']:x for x in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.solvents={x['substance_id']:x for x in json.loads((LIB/'solvents.json').read_text())['solvents']};self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._caller=None
  self._device_init();self.key_beeps=0;self.panel_capture_dir=None;self.panel=_panel_ui.PanelUI(PANEL_SPEC,self,PANEL_HOOKS) if PANEL_SPEC else None
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return BuchiSpeedextractoreSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _maxp(self):return max((c['pressure'] for c in self._records().values()),default=0.)
 def _inventory(self):
  active=[c for c in self.cells.values() if not c['removed']];removed=[c for c in self.cells.values() if c['removed']];blanks=list(self.blanks.values())
  return dict(initial_target_kg=sum(c['spec']['target_mass_kg'] for c in self.cells.values()),sorbed_target_kg=sum(c['sorbed'] for c in active),dissolved_target_kg=sum(c['dissolved'] for c in active),extract_target_kg=sum(c['extract'] for c in active),degraded_target_kg=sum(c['degraded'] for c in active),removed_target_kg=sum(c['sorbed']+c['dissolved']+c['extract']+c['degraded'] for c in removed)+sum(c['wasted']+c['collected'] for c in self.cells.values()),initial_solvent_m3=sum(self.plant['reservoir_m3']) if self.plant else 0.,reservoir_solvent_m3=sum(self.reservoir),cell_solvent_m3=sum(sum(c['liquid']) for c in active+blanks),extract_solvent_m3=sum(sum(c['vial']) for c in active+blanks),waste_solvent_m3=sum(self.waste),removed_solvent_m3=sum(sum(c['liquid'])+sum(c['vial']) for c in removed)+sum(self.exchanged)+sum(self.removed_blank),initial_nitrogen_mol=self.plant['nitrogen_mol'] if self.plant else 0.,remaining_nitrogen_mol=self.nitrogen,exhaust_nitrogen_mol=self.nitrogen_out,max_pressure_gauge_pa=self._maxp())
 def _values(self):
  v={'power.enabled':self.powered,'shield.lift_m':self.shield,'drawer.extension_m':self.drawer,'heater.lift_m':self.heater,'rack.lift_m':self.rack,'plant.configured':self.plant is not None,'sample.configured':bool(self.cells),'method.configured':self.method is not None,'thermal.preheat_enabled':self.preheat,'thermal.target_k':self.method['temperature_k'] if self.method else None,'thermal.indicated_k':self.temperature if self.powered else None,'pressure.max_gauge_pa':self._measured_max() if self.powered else None,'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.fault':self.fault,'cycle.index':self.cycle,'cycle.elapsed_s':self.elapsed_s,'cycle.stage_elapsed_s':self.stage_elapsed};v.update({'truth.'+k:x for k,x in self._inventory().items()});v.update(self._device_values());return v
 def _device_values(self):
  return {'positions.active':list(self.positions) if self.positions is not None else None,'cells.occupancy':self._occupancy(),'method.current':self._full_method(),'method.name':self.method_name,
          'method.modified':self.method_modified,'method.library':[dict(slot=k,name=e['name']) for k,e in sorted(self.library.items())],'thermal.preheat_target_k':self.preheat_k,
          'thermal.eco_mode':self.eco,'leak_test.settings':copy.deepcopy(self.leak),'leak_test.status':self._aux_status(self.aux if self.aux and self.aux['kind']=='leak_test' else None) if self.aux and self.aux['kind']=='leak_test' else dict(self.aux_last.get('leak_test',dict(active=False)),active=False),
          'flush.settings':copy.deepcopy(self.flush_set),'flush.status':self._aux_status() if self.aux and self.aux['kind']=='flush' else dict(self.aux_last.get('flush',dict(active=False)),active=False),
          'flow_test.settings':copy.deepcopy(self.flow_set),'flow_test.status':self._aux_status() if self.aux and self.aux['kind']=='flow_test' else dict(self.aux_last.get('flow_test',dict(active=False)),active=False),
          'service.valves':copy.deepcopy(self.valves),'service.pump':copy.deepcopy(self.pump),'service.lifts':self._lifts(),'service.fans':dict(self.fans),'service.counters':self._counters(),
          'pressure.sensors':self._sensors(),'device.settings':dict(self.settings),'solvents.list':list(self.solvent_list)}
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _measure(self):return dict(phase=self.phase,stage=self.stage,fault=self.fault,temperature_k=self.temperature if self.powered else None,max_pressure_gauge_pa=self._measured_max() if self.powered else None,cycle=self.cycle,stage_elapsed_s=self.stage_elapsed,evidence='synthetic_host_lumped_partition_and_control; NOT validated recovery or safety',template_source=TEMPLATE_REL)
 def _product(self):
  items=[]
  for i,c in self.cells.items():
   s=c['spec'];ref=self.templates[s['substance_id']];items.append(dict(position=i,sample_id=s['sample_id'],substance_id=s['substance_id'],extract_target_kg=c['extract']+c['collected'],residual_target_kg=c['sorbed']+c['dissolved'],degraded_target_kg=c['degraded'],matrix_mass_kg=s['matrix_mass_kg'],extract_solvent_m3=sum(c['vial']),residual_solvent_m3=sum(c['liquid']),modeled_absolute_recovery_fraction=min(1.,(c['extract']+c['collected'])/s['target_mass_kg']),measured_e916_recovery_fraction=None,reference_ase_soxtec_ratio=ref['values']['ase_to_soxtec_recovery_ratio'],reference_instrument=ref['conditions']['instrument']))
  return dict(items=items,evidence='synthetic_absolute_yield_from_host_Kd_transfer; measured_literature_relative_ASE200_to_Soxtec_is_NOT_E916_yield',template_source=TEMPLATE_REL)
 def saturation_pressure(self,temperature,volume_fractions):
  total=0.;parts=[]
  for sid,f in zip(SOLVENTS,volume_fractions):
   s=self.solvents[sid];points=s['vapor_pressure_reference_points'];t=temperature
   if not points[0]['temperature_k']<=t<=points[-1]['temperature_k']:raise ActionRejected('TEMPLATE_RANGE_UNAVAILABLE')
   j=min(len(points)-2,int((t-points[0]['temperature_k'])/5));lo,hi=points[j:j+2];weight=(t-lo['temperature_k'])/(hi['temperature_k']-lo['temperature_k']);ps=math.exp((1-weight)*math.log(lo['pressure_pa'])+weight*math.log(hi['pressure_pa']));moles=f*s['density_298_15_kg_m3']/s['molar_mass_kg_mol'];parts.append((moles,ps));total+=moles
  return sum(n*p for n,p in parts)/total if total else 0.
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if capability in PANEL_CAPS:
   out=self._panel_cmd(capability,a)
   if CAPS[capability][4]!='query':self.revision+=1
   return out
  if capability in DEVICE_CAPS:
   if _panel_ui.check_arguments(CAPS[capability][0],a):raise ActionRejected('INVALID_ARGUMENT')
  else:
   for k in ('position','cycles'):
    if k in a and (isinstance(a[k],bool) or not isinstance(a[k],int) or not (1<=a[k]<=10 if k=='cycles' else 0<=a[k]<=5)):raise ActionRejected('INVALID_ARGUMENT')
  if self._foreign_process(capability,a):raise ActionRejected('ACCESS_DENIED')
  if self.phase=='running' and CAPS[capability][4]!='query' and capability not in RUN_OK:raise ActionRejected('BUSY')
  if capability in DEVICE_CAPS:
   out=self._device_invoke(capability,a);self.revision+=1;return copy.deepcopy(out)
  if capability=='power.set':
   if a['enabled'] and not self.powered:self.power_epoch+=1
   if not a['enabled'] and self.powered:
    self.eco=False;self.pump.update(on=False,actual_m3_s=0.);self.lift_cmd=dict(cell=None,vial=None)
    if self.aux:self.aux['fault']='power_off';self.aux_last[self.aux['kind']]=self._aux_status();self.aux=None
   self.powered=a['enabled']
   if not self.powered and self.phase=='running':self.phase='cancelled'
   out=a
  elif capability in ('shield.set_lift','drawer.set_extension'):
   if self._maxp()>1e5 or self.heater>1e-9 or self.rack>1e-9:raise ActionRejected('PRECONDITION_FAILED')
   if capability=='shield.set_lift':
    if a['lift_m']<.179 and self.drawer>1e-9:raise ActionRejected('PRECONDITION_FAILED')
    self.shield=a['lift_m']
   else:
    if self.shield<.179:raise ActionRejected('PRECONDITION_FAILED')
    self.drawer=a['extension_m']
   out=a
  elif capability=='plant.configure':
   if self.plant or not a['exhaust_connected'] or not a['dry_matrix_reviewed'] or len(a['reservoir_m3'])!=4 or not a['coefficient_evidence']:raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);self.reservoir=list(a['reservoir_m3']);self.nitrogen=a['nitrogen_mol'];self.temperature=a['ambient_k'];out={'configured':True}
  elif capability=='method.configure':
   f=a['solvent_fractions']
   if self._operation_count or self.cells or len(f)!=4 or abs(sum(f)-1)>1e-9 or self.aux:raise ActionRejected('PRECONDITION_FAILED')
   if a['pressure_gauge_pa']/3+101325<1.1*self.saturation_pressure(a['temperature_k'],f):raise ActionRejected('LIQUID_PHASE_GUARD')
   self._check_method_extras(a)
   self.method=copy.deepcopy(a);self.method_modified=True;out={'configured':True}
  elif capability=='thermal.preheat':
   if not a.get('enabled',True):
    if 'temperature_k' in a:self.preheat_k=a['temperature_k']
    self.preheat=False;out={'enabled':False}
   else:
    if not self.powered or not self.plant or (not self.method and 'temperature_k' not in a) or self.cells or self.heater>0:raise ActionRejected('PRECONDITION_FAILED')
    self.preheat_k=a.get('temperature_k');self.preheat=True;out={'enabled':True}
  elif capability=='sample.configure':
   if not self.plant or not self.method or not self.preheat or abs(self.temperature-self.method['temperature_k'])>1 or self.shield<.179 or self.drawer<.159 or not a['gripper_confirmed'] or self._operation_count or a['position'] in self.cells or a['position'] in self.blanks or not a['sample_id'] or any(c['spec']['sample_id']==a['sample_id'] for c in self.cells.values()) or a['matrix_solid_volume_m3']>=a['cell_volume_m3']*.9 or a['target_mass_kg']>a['matrix_mass_kg']*.01 or len(a['kd_m3_kg_by_solvent'])!=4 or not a['parameters_evidence']:raise ActionRejected('PRECONDITION_FAILED')
   if a['substance_id'] not in self.templates:raise ActionRejected('TEMPLATE_RANGE_UNAVAILABLE')
   if any(c['spec']['cell_volume_m3']!=a['cell_volume_m3'] for c in self.cells.values()):raise ActionRejected('PRECONDITION_FAILED')
   self.cells[a['position']]=dict(spec=copy.deepcopy(a),pressure=0.,liquid=[0.]*4,vial=[0.]*4,sorbed=a['target_mass_kg'],dissolved=0.,extract=0.,degraded=0.,removed=False,wasted=0.,collected=0.);out=dict(configured=True,attachment_event='host_attach:'+a['sample_id'])
  elif capability=='acquisition.start':
   if not self.powered or not self.plant or not self.method or not self.cells or any(c['removed'] for c in self.cells.values()) or self.shield>1e-9 or self.drawer>1e-9 or self.phase not in ('idle','cancelled') or self.stage in ('complete','halted') or abs(self.temperature-self.method['temperature_k'])>1 or not self._active():raise ActionRejected('PRECONDITION_FAILED')
   if self.aux:raise ActionRejected('BUSY')
   if not self._operation_count:self._next('sealing');self.cycle=1;self.counters['extractions']+=1
   self.valves=dict(solvent=[False]*4,media=None,position=[False]*6,outlet=None);self.pump.update(on=False,actual_m3_s=0.);self.lift_cmd=dict(cell=None,vial=None)
   self.phase='running';self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('PRECONDITION_FAILED')
   self.phase='cancelled';out=dict(phase=self.phase)
  elif capability=='acquisition.abort':
   if not self.powered or not self._operation_count or self.phase not in ('running','cancelled') or self.stage in ('complete','halted'):raise ActionRejected('PRECONDITION_FAILED')
   self.aborting=True;self.phase='running';self._next('discharge');out=dict(phase=self.phase)
  elif capability=='extraction.measure':return self._measure()
  elif capability=='product.read':return self._product()
  elif capability=='reference.lookup':
   q=self.templates.get(a['substance_id'])
   if q is None:raise ActionRejected('TEMPLATE_RANGE_UNAVAILABLE')
   return dict(substance_id=q['substance_id'],name=q['name'],ase_soxtec_ratio=q['values']['ase_to_soxtec_recovery_ratio'],measured_e916_recovery_fraction=None,source=q['source'],conditions=json.dumps(q['conditions'],ensure_ascii=False),evidence=q['evidence'],template_source=TEMPLATE_REL)
  elif capability=='sample.unload':
   c=self.cells.get(a['position'])
   if not c or c['spec']['sample_id']!=a['sample_id'] or c['removed'] or self.stage not in ('complete','halted') or self._maxp()>1e5 or self.shield<.179 or self.drawer<.159 or not a['gripper_confirmed']:raise ActionRejected('PRECONDITION_FAILED')
   c['removed']=True;out=dict(unloaded=True,attachment_event='host_release:'+a['sample_id'])
  self.revision+=1;return copy.deepcopy(out)
 def _fan_percent(self):
  """Extraction fan performance: the service setting, 100 % while a fault event is active (om p.88: to get rid of leaking solvent)."""
  return 100 if (self.phase=='failed' or (self.aux is not None and self.aux['fault'])) else self.fans['extraction']
 def _foreign_process(self,k,a):
  """An agent may not stop, skip, resume or change (heater, fan, EcoMode, service valves / pump / lifts / sensor zero) another agent's
  extraction (in progress, also while paused) or its leak test / flush / flow test: direct calls and the same commands from the panel keys (the
  session sets _caller around the call); unowned processes and evaluators are not restricted.  1.2.0 (lead rule 10-19): the green mains rocker
  switch (and power.set, which models it) is a hard switch - a physical action that is never refused; switching off acts as a power loss."""
  c=self.__dict__.get('_caller')
  if c is None or self._principals.get(c)!='agent':return False
  off=False;change=k in CHANGES_PROCESS
  if self._in_operation() and (off or change or k in ('acquisition.start','acquisition.stop','acquisition.abort','acquisition.halt','acquisition.skip')):
   o=self._owners.get(self.operation_id)
   if o is not None and o!=c:return True
  if self.aux and (off or change or k in ('leak_test.stop','flush.stop','flow_test.stop')):
   o=self.aux.get('owner')
   if o is not None and o!=c:return True
  return False
 def _next(self,stage):
  self.stage=stage;self.stage_elapsed=0.;self.history.append(dict(time_s=self.time_s,stage=stage,cycle=self.cycle))
 def _fail(self,reason):self.phase='failed';self.fault=reason;self._notify(reason)
 def _feed(self,c,vol):
  f=self._fractions();v=min([vol]+[self.reservoir[i]/x for i,x in enumerate(f) if x>0])
  for i,x in enumerate(f):self.reservoir[i]-=v*x;c['liquid'][i]+=v*x
  if v+1e-15<vol:self._fail('solvent_supply_empty')
 def _drain(self,c,vol,holdup=0.):
  if (self.method or {}).get('vial')=='waste':return self._drain_to(c,vol,holdup,'waste')    # EDIT METHOD vial 'waste': the extract goes to the waste bottle
  v=sum(c['liquid']);x=min(max(0.,v-holdup),vol);space=self.plant['vial_capacity_m3']-sum(c['vial'])
  if x>space+1e-15:self._fail('collection_vial_full');x=max(0.,space)
  frac=x/v if v else 0.;m=c['dissolved']*frac;c['dissolved']-=m;c['extract']+=m
  for i in range(4):x=c['liquid'][i]*frac;c['liquid'][i]-=x;c['vial'][i]+=x
 def _transfer(self,c,h):
  if c['removed']:return
  s=c['spec'];v=sum(c['liquid']);t=self.temperature;total=c['sorbed']+c['dissolved']
  if v>1e-15:
   kd=math.exp(sum(c['liquid'][i]/v*math.log(s['kd_m3_kg_by_solvent'][i]) for i in range(4)))*math.exp(s['adsorption_energy_j_mol']/8.314462618*(1/t-1/298.15));eq=min(total*v/(v+kd*s['matrix_mass_kg']),s['solubility_kg_m3']*v);k=s['mass_transfer_s_inv']*math.exp(s['activation_energy_j_mol']/8.314462618*(1/298.15-1/t));delta=(eq-c['dissolved'])*-math.expm1(-k*h);delta=max(-c['dissolved'],min(c['sorbed'],delta));c['sorbed']-=delta;c['dissolved']+=delta
  d=-math.expm1(-s['degradation_s_inv']*h)
  for key in ('sorbed','dissolved'):x=c[key]*d;c[key]-=x;c['degraded']+=x
 def _gas(self,h):
  requested=self.plant['nitrogen_mol_s_per_cell']*len(self._run_cells())*h;x=min(self.nitrogen,requested);self.nitrogen-=x;self.nitrogen_out+=x
  if x+1e-12<requested:self._fail('nitrogen_supply_empty')
 def _tick(self,h):
  p=self.plant;m=self.method;heat=bool(self.powered and self.preheat and (self.preheat_k is not None or m))
  target=(self.preheat_k if self.preheat_k is not None else m['temperature_k']) if heat else p['ambient_k']
  g=FAN_LOSS*(self._fan_percent()-FAN_FACTORY)/100. if self.powered else 0.   # extraction fan: more (less) cooling of the heating block than at 30 % (om p.88)
  t_ss=(target+g*p['ambient_k'])/(1+g);self.temperature+=(t_ss-self.temperature)*-math.expm1(-h*(1+g)/p['thermal_tau_s'])
  self._counters_tick(h,heat);running=self.powered and self.phase=='running'
  if running:self._extraction_tick(h)
  elif self.stage=='vial_change':self.rack=max(0.,self.rack-.01*h)              # Error 4 / vial change: the vial lift goes down for the exchange
  if self.powered and self.aux:self._aux_tick(h)
  elif self.powered and not running:self._service_tick(h)
  for c in self.cells.values():self._transfer(c,h)
  self.time_s+=h
 def _extraction_tick(self,h):
  p=self.plant;m=self.method;cells=self._run_cells()
  self.elapsed_s+=h;self.stage_elapsed+=h
  if self.stage=='sealing':
   self.heater=min(.035,self.heater+.01*h);self.rack=min(.035,self.rack+.01*h)
   if self.heater>=.035 and self.rack>=.035:self._next('tightness')
  elif self.stage=='tightness':
   self._gas(h)
   if self.stage_elapsed>=10 and self.phase=='running':
    miss=self._missing()
    if miss:self._fail('position_pressure_too_low:'+str(miss[0]+1))                 # Error 45-50: an active position without a cell
    else:self._next('fill')
  elif self.stage=='fill':
   for c in cells:self._feed(c,min(m['pump_total_m3_s']/6*h,max(0.,self._free(c)-sum(c['liquid']))))
   if self.phase=='running' and all(abs(sum(c['liquid'])-self._free(c))<1e-12 for c in cells):self._next('heatup_1')
  elif self.stage.startswith('heatup_') or self.stage=='hold':
   fraction={'heatup_1':1/3,'heatup_2':2/3,'heatup_3':1.,'hold':1.}[self.stage]
   idx=self._run_indexed()                                                   # the pump holds the position sensors at the set point (their error and zero)
   for i,c in idx:c['pressure']+=(max(0.,m['pressure_gauge_pa']*fraction-self._offset(i))-c['pressure'])*-math.expm1(-h/p['pressure_tau_s'])
   self.pump_p=self.pump_p+(OVERALL_HOLD*m['pressure_gauge_pa']-self.pump_p)*-math.expm1(-h/60.) if self.stage=='hold' else max([c['pressure'] for c in cells]+[0.])
   dwell=10*self._cell_volume(cells)/20e-6;rd=self._readings()
   if self.stage.startswith('heatup_') and self.stage_elapsed>=dwell and all(abs(rd[1+i]-m['pressure_gauge_pa']*fraction)<1e4 for i,_ in idx) and abs(self.temperature-m['temperature_k'])<=1:self._next({'heatup_1':'heatup_2','heatup_2':'heatup_3','heatup_3':'hold'}[self.stage])
   elif self.stage=='hold' and self.stage_elapsed>=self._step('hold_s'):self._next('discharge')
  elif self.stage=='discharge':
   self.rack=min(.035,self.rack+.01*h)
   for c in cells:
    c['pressure']*=math.exp(-h/p['pressure_tau_s']);free=self._free(c);self._drain(c,p['discharge_m3_s_per_cell']*h,free*p['residual_holdup_fraction'])
   drained=all(sum(c['liquid'])<=self._free(c)*p['residual_holdup_fraction']+1e-12 for c in cells)
   if self.phase=='running' and drained and self._measured_max()<=1e5 and self.stage_elapsed>=self._step('discharge_s'):
    if self.cycle<m['cycles'] and not self.aborting:
     if self._vial_change_due(cells):self._next('vial_change');self.phase='cancelled';self._notify('change_vials')    # VC or Error 4: wait for START
     else:self.cycle+=1;self._next('fill')
    else:self._next('solvent_flush' if m['solvent_flush_s']>0 and not self.aborting else 'nitrogen_flush')
  elif self.stage=='vial_change':
   self.rack=min(.035,self.rack+.01*h)
   if self.rack>=.035:self.cycle+=1;self._next('fill')
  elif self.stage=='solvent_flush':
   for c in cells:
    v=m['pump_total_m3_s']/6*h;self._feed(c,v);self._drain(c,v)
   if self.stage_elapsed>=m['solvent_flush_s'] and self.phase=='running':self._next('nitrogen_flush')
  elif self.stage=='nitrogen_flush':
   self.rack=min(.035,self.rack+.01*h);self._gas(h)
   for c in cells:self._drain(c,p['discharge_m3_s_per_cell']*h);c['pressure']*=math.exp(-h/p['pressure_tau_s'])
   if self.stage_elapsed>=m['nitrogen_flush_s'] and self.phase=='running' and all(sum(c['liquid'])<1e-15 for c in cells) and self._measured_max()<=1e5:self._next('release')
  elif self.stage=='release':
   self.heater=max(0.,self.heater-.01*h);self.rack=max(0.,self.rack-.01*h)
   if self.heater<=1e-12 and self.rack<=1e-12:self.heater=self.rack=0.;self._next('complete');self.phase='cancelled' if self.aborting else 'succeeded';self._run_end()
  if not (self.stage.startswith('heatup_') or self.stage=='hold'):self.pump_p*=math.exp(-h/p['pressure_tau_s'])
 def _run_end(self):
  if self.eco:self.preheat=False;self.eco=False                                 # EcoMode: heater off after the run (om p.45)
  if self.settings['beep_end']:self.end_beeps+=1
 def step(self,dt):
  if isinstance(dt,bool) or not isinstance(dt,(int,float)) or not math.isfinite(dt) or not 0<dt<=3600:raise ActionRejected('INVALID_ARGUMENT')
  n=math.ceil(dt/.25);h=dt/n
  for _ in range(n):
   if self.plant:self._tick(h)
   else:self.time_s+=h
  self.revision+=1;return self.read()

class BuchiSpeedextractoreSession:
    """Host registers principals; public calls have no visibility override."""
    def __init__(self,host,principal_id): self._host=host; self._principal=principal_id
    @property
    def _role(self): return self._host._principals[self._principal]
    def _target(self,i):
        if i!=self._host.instance_id: raise ActionRejected('UNKNOWN_INSTANCE')

    def describe(self,*,instance_id=None,asset_type_id=None):
        if (instance_id is None)==(asset_type_id is None): raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None: self._target(instance_id)
        if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID: raise ActionRejected('UNKNOWN_INSTANCE')
        meta=export_buchi_speedextractore_contract_metadata()
        return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,
          capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},
          state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})

    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id); h=self._host; vals=h.read(self._role); ids=list(vals) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids): raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in vals for k in ids): raise ActionRejected('ACCESS_DENIED')
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},
          observations={k:dict(value=copy.deepcopy(vals[k]),unit=SLOTS[k][1],quality='unavailable' if vals[k] is None else ('estimated' if SLOTS[k][2]=='derived' else 'valid'),sample_time_s=h.time_s,sample_window_s=None) for k in ids})

    def invoke(self,*,instance_id,capability_id,arguments,request_id):
        try:
            return self._invoke(instance_id=instance_id,capability_id=capability_id,arguments=arguments,request_id=request_id)
        except ActionRejected as exc:
            return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,
                status='rejected',state_revision=self._host.revision,clock={'domain':'simulation','time_s':self._host.time_s},
                result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))

    def _invoke(self,*,instance_id,capability_id,arguments,request_id):
        self._target(instance_id); h=self._host
        if not isinstance(capability_id,str) or capability_id not in CAPS: raise ActionRejected('UNKNOWN_CAPABILITY')
        if self._role=='agent' and CAPS[capability_id][5]!='agent': raise ActionRejected('ACCESS_DENIED')
        if not isinstance(request_id,str) or not request_id: raise ActionRejected('INVALID_ARGUMENT')
        validate(CAPS[capability_id][0],arguments)
        key=(self._principal,instance_id,request_id); digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
        if key in h._requests:
            old,result=h._requests[key]
            if old!=digest: raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(result)
        before=h.operation_id; h._caller=self._principal
        try: result=h.invoke(capability_id,**arguments)
        finally: h._caller=None
        op=result.get('operation_id')
        if op: h._owners[op]=self._principal
        elif capability_id in PANEL_CAPS and h.operation_id and h.operation_id!=before: h._owners[h.operation_id]=self._principal   # an extraction started with START belongs to the caller
        response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=op,
          status='accepted' if op else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None)
        h._requests[key]=(digest,copy.deepcopy(response)); return response

    def write(self,*,instance_id,state_id,value,request_id):
        self._target(instance_id)
        if state_id not in SLOTS or not SLOTS[state_id][5]: raise ActionRejected('ACCESS_DENIED')
        cap,arg=SLOTS[state_id][5]
        return self.invoke(instance_id=instance_id,capability_id=cap,arguments={arg:value},request_id=request_id)

    def poll(self,operation_id):
        h=self._host
        if operation_id!=h.operation_id or h._owners.get(operation_id)!=self._principal: raise ActionRejected('ACCESS_DENIED')
        return dict(operation_id=operation_id,status=h.phase,result=h._measure())

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])

def export_buchi_speedextractore_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'BuchiSpeedextractoreSession.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
        if key in NEW_SLOT_ZH:slots[key]['meaning_zh']=NEW_SLOT_ZH[key];slots[key]['source_ids']=['manufacturer_manual_menus','simulation_assumption']
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key=='acquisition.start'; pre=[]
        conditions={
         'acquisition.start':('供电、预热达温、已装同容量池，屏/滑轨闭合，首次或暂停续作',['power.enabled','sample.configured','thermal.indicated_k','shield.lift_m','drawer.extension_m','cycle.phase'],'powered and loaded and preheated and closed and resumable'),
         'sample.configure':('装池前预热，开屏抽块，夹具确认，首次且同容量，目标量<=基质1%',['thermal.preheat_enabled','thermal.indicated_k','shield.lift_m','drawer.extension_m'],'preheated_before_loading and gripper and equal_cell_capacity'),
         'method.configure':('装样/运行前，四比例合计1，整数周期，分级升压保守液相检查',['sample.configured','cycle.phase'],'before_loading and ratios_sum_one and liquid_guard'),
         'sample.unload':('完整或有序终止后，残压<=1bar、开屏抽块，夹具和身份确认',['cycle.stage','pressure.max_gauge_pa','shield.lift_m','drawer.extension_m'],'complete and depressurized and open and matching_identity')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.buchi_speedextractore.BuchiSpeedextractoreSession.invoke',invocation=f'session.invoke(instance_id="speedextractor_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded且完成排液/氮吹/降架；暂停保留库存和残压' if async_ else '设定或查询不推进时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝无修改；暂停关闭主动阀但不冻结热/传质/降解，有限供应或满瓶失败保留物料；不是实机安全认证。去重仅进程内有效'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    evolving=[k for k in slots if k not in ('power.enabled','shield.lift_m','drawer.extension_m') and (k not in NEW_SLOT_ZH or k in ('pressure.sensors','service.counters','service.lifts'))]
    for key in ['acquisition.start','plant.configure','sample.configure','sample.unload','acquisition.abort','thermal.preheat','leak_test.start','flush.start','flow_test.start','pump.run','valve.set','lift.move','acquisition.skip','acquisition.halt']:
        caps[key]['state_effects'] += [dict(state_id=k,timing='on_step' if key in ('acquisition.start','acquisition.abort','thermal.preheat','leak_test.start','flush.start','flow_test.start','pump.run','valve.set','lift.move','acquisition.skip','acquisition.halt') else 'immediate',description_zh='六位置守恒传质与阶段过程') for k in evolving if k not in [e['state_id'] for e in caps[key]['state_effects']]]
    for key in ['shield.set_lift','drawer.set_extension']:
        caps[key]['preconditions'].append(dict(description_zh='非运行、表压<=1bar、热块和瓶架降下；抽块另需屏完全上推',state_ids=['cycle.phase','pressure.max_gauge_pa','heater.lift_m','rack.lift_m','shield.lift_m'],enforced_by='runtime',check='not_running and pressure_low and lifts_down and clearance'))
    for key in ['reference.lookup','sample.configure']:caps[key]['errors'].append(dict(code='TEMPLATE_RANGE_UNAVAILABLE',meaning_zh='参考物质或纯溶剂公式范围不可用',side_effects='none'))
    caps['method.configure']['errors'].append(dict(code='LIQUID_PHASE_GUARD',meaning_zh='保守液相模型范围：目标表压1/3+大气压需高于理想蒸气压1.1倍',side_effects='none'))
    for key,(desc,states) in NEW_PRE.items():caps[key]['preconditions']=[dict(description_zh=desc,state_ids=states,enforced_by='runtime',check='BuchiSpeedextractoreRuntime._device_invoke guards')]
    for key,codes in NEW_CAP_ERRORS.items():
        have={e['code'] for e in caps[key]['errors']}
        caps[key]['errors']+=[dict(code=c,meaning_zh=DEVICE_ERRORS.get(c,c),side_effects='none') for c in codes if c not in have]
    for key in DEVICE_CAPS:
        caps[key]['source_ids']=['manufacturer_manual_menus','simulation_assumption']
        caps[key]['limitations']=['菜单背后的固件功能，按操作手册093218的最小模型实现；系数（背压、电机电流、升降速度）为教学假设，未按厂家标定。']
    for key in ('leak_test.start','flush.start','flow_test.start','pump.run'):
        caps[key]['execution']['completion_criterion']='命令立即返回；过程随step演化，状态见对应status槽（检漏/冲洗结束后自动降架）'
    for key,d in PANEL_CAPS.items():
        e=caps[key];e['source_ids']=['manufacturer_manual_menus','simulation_assumption']
        if d['kind']!='query':
            e['preconditions']=[dict(description_zh='通电后按键与旋钮才起作用（断电时只有主开关）；每个界面规则按panel_ui.json（手册页码证据）',state_ids=['power.enabled'],enforced_by='runtime',check='panel_ui.json gates')]
            e['execution']['completion_criterion']='手势瞬时完成（不推进仿真时钟）：界面状态与被调用能力的效果立即生效；结果只说明是否被仪器接受，屏幕须用panel.capture查看'
        e['limitations']=['前面板只调用本运行层的同名能力（没有第二份仪器逻辑）；画面按手册图以5x7文字近似绘制，不是像素级屏幕。']
    called=_panel_ui.spec_capabilities(PANEL_SPEC) if PANEL_SPEC else []
    fx=list(dict.fromkeys(e['state_id'] for c in called for e in caps[c]['state_effects']))
    for key,d in PANEL_CAPS.items():
        if d['kind']!='query':caps[key]['state_effects']=[dict(state_id=k,timing='immediate',description_zh='手势按界面规则调用的能力所改变的状态') for k in fx]
    mechanism=dict(component_ids=['pump','mixer','heater_lift','media_valve','outlet_valve','collection_rack'],state_ids=evolving,meaning_zh='同容量六池有限溶剂与目标物守恒、条件分配与传质',implementation='lumped',equations=['four stock liquid volume ledgers: initial=reservoir+cell+vial+waste+removed; reference density fixed, thermal expansion neglected','target_initial=sorbed+dissolved+extract+degraded+removed, independently per cell','one total pump1..50mL/min divided by6; inactive positions reduce effective method flow, exact factory algorithm unknown','ideal Raoult vapor pressure: x_i=(volume_fraction*rho_i/M_i)/sum; p=sum(x_i*log_interpolated_35point_CoolProp7.2.0_EOS_i(T))','q_solid=Kd*C_liquid; M_liquid_eq=min(M_total*V/(V+Kd*matrix_mass),solubility*V)','mixture Kd=geometric_volume_mean(Kd_i)*exp(Eads/R*(1/T-1/298.15)); k=k_ref*exp(Ea/R*(1/298.15-1/T))','delta_M=(M_liquid_eq-M_liquid)*(1-exp(-k*dt)); clamp to available material','host degradation first order conservatively moves sorbed/dissolved target to degraded ledger; extract cooled proxy no degradation','preheat first; seal3.5s, nitrogen tightness proxy10s, finite fill, staged pressure1/3,2/3,full; intrinsic dwell proxy10s*(cell_volume/20mL)','hold and discharge each0..3600s;1..10cycles; discharge NO nitrogen, final optional solvent0..540s and mandatory N2 60..1800s','finite host nitrogen molar stock flows only tightness/final nitrogen, never creates liquid; nitrogen push moves residual liquid to collection not evaporation','pressure hydraulic first-order target and discharge decay, not mass-derived compressible EOS; pressure<=1bar before automatic release','pause closes active flows but thermal/partition/degradation continue; ordered abort drains/purges/releases then cancelled complete'],parameters={},source_ids=['manufacturer_manual','simulation_assumption'],limitations=['54measured AN317 ratios compare Dionex ASE200 with Soxtec and may exceed1; NOT E916 absolute recoveries,partition coefficients or transfer constants. No measured E916 calibration available.','54names have unresolved CAS; two ambiguous source names excluded. Original DCM/acetone reference recipe is not the four-solvent runtime subset.','Host supplies Kd,transfer,activation,solubility,degradation,thermal lag,pressure lag,discharge and holdup. These are synthetic assumptions,not inferred from recovery ratios.','Four-port E916 selected,10/20/40mL equal-capacity runtime groups; current geometry only20mL proxy and separately purchased240mL bottles. Mixed capacities and changing vials between cycles absent.','140pure-solvent EOS nodes and136heldout midpoint log-interpolation tests; ideal mole-fraction Raoult only; nonideal mixing,boiling,gas dissolution,thermal expansion and complete energy balance not implemented. Fill/discharge transient two-phase behavior absent; model does not certify real safety.','Pressure lag is independent of fluid compressibility; per-cell pressure sensors ideal by default (evaluator offsets settable, zeroed by the service CALIBRATE), not manufacturer accuracy/noise calibration. The initial nitrogen tightness test only checks that the active positions hold cells; the separate LEAK TEST (pump to set pressure, hold, discharge, gas flush) is implemented with ideal seals unless an evaluator seal leak is set.','Six cells equal nominal division total/6 is an explicit flow assumption; factory active-position flow algorithm not independently known. The stand-alone FLUSH uses the set total flow (1-50 mL/min) for the active positions without reduction; the service pump and flow test use an assumed line back pressure of 3 bar at 50 mL/min per line (no clogging modelled).','All product-directed liquid captured in the bottle until its finite capacity, or sent to the waste ledger when the method vial is waste, a flush / leak test goes to waste, or the service outlet valve is set to waste; a declared vial size triggers the vial-change stop (Error 4). N2 transports residual liquid,not solvent evaporation or aerosol entrainment.','No cell cleaning,wet-sample drying,polymer precipitation,clogging,needle puncture,thread torque,real pressure seal contact,robot pickup or hardware USB/control. One batch per instance.','STOP once pauses, twice runs the ordered abort, three times stops immediately with every assembly left in place (recovery only through the service valves and lifts); the presses are counted by state, not by a firmware time window. No automatic recovery from failed supply/vial/pressure safety guards.','Cold loading forbidden; hot unload requires host gripper confirmation only,not a simulated thermal-contact safety guarantee. All safety interlocks are teaching subset,not certified.','Four joint strokes and all local component dimensions/masses unknown estimates. 90kg/98kg and datasheet dimension conflicts retained; actual total mass null.','Agent truth filtering and nativeSession async are tested separately from Matterix outer ack; no deployment isolation or robot constraint.'])
    _ann.annotate_export('buchi_speedextractore',caps,slots,{'pressurized_extraction':mechanism},inputs={'plant.configure':(PLANT,EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'pressurized_extraction':mechanism})

"""CHEM141 M91: traditional current/field reversal, external magnet and vdP.

Only observed voltages/currents/fields enter inverse Hall/vdP fits. A host-controlled
external field boundary evolves in time; M91 does not create a magnetic field.
"""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
import numpy as np
from scipy.integrate import solve_ivp
PROTOCOL_VERSION='1.0.0';SLUG='lakeshore_measurereadym91';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-141'
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch04/t1_thermal'/SLUG;LIBRARY_PATH=ASSET/'hall_library.json'

class ActionRejected(ValueError):pass

def number(unit='1',lo=None,hi=None):
    s={'type':'number','x-unit':unit}
    if lo is not None:s['minimum']=lo
    if hi is not None:s['maximum']=hi
    return s

def obj(properties):return dict(type='object',properties=properties,required=list(properties),additionalProperties=False)
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
def array(s,minimum=0,maximum=200001):return dict(type='array',items=s,minItems=minimum,maxItems=maximum)
def enum(items):return {'type':'string','enum':items}
BOOL={'type':'boolean'};TEXT={'type':'string'};PHASE=enum(['idle','running','succeeded','cancelled','failed'])

def validate(s,v):
    if 'anyOf' in s:
        for o in s['anyOf']:
            try:validate(o,v);return
            except ActionRejected:pass
        raise ActionRejected('INVALID_ARGUMENT')
    k=s.get('type')
    if k in ('number','integer'):
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or k=='integer' and type(v) is not int or not s.get('minimum',-math.inf)<=v<=s.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
    elif k=='object':
        if not isinstance(v,dict) or set(v)!=set(s['properties']):raise ActionRejected('INVALID_ARGUMENT')
        for key,value in v.items():validate(s['properties'][key],value)
    elif k=='array':
        if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
        for x in v:validate(s['items'],x)
    elif k=='boolean' and type(v) is not bool:raise ActionRejected('INVALID_ARGUMENT')
    elif k=='string' and not isinstance(v,str):raise ActionRejected('INVALID_ARGUMENT')
    elif k=='null' and v is not None:raise ActionRejected('INVALID_ARGUMENT')
    if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')

from scipy.linalg import expm
from scipy.optimize import brentq
ELEMENTARY_CHARGE_C=1.602176634e-19
WARMUP_S=1800.
CURRENT_RANGES=[1e-6,1e-5,1e-4,1e-3,1e-2,1e-1]
VOLTAGE_RANGES=[.001,.01,.1,1.,10.]
VOLTAGE_EXCITATION_RANGES=[.01,.1,1.,10.]
CURRENT_MEASUREMENT_RANGES=[1e-8,1e-5,.01,.1]
CONTACTS=['vdp_a','vdp_b','hall']

def library():return {x['id']:x for x in json.loads(LIBRARY_PATH.read_text())['templates']}
def transport(record,temperature,clamp=False):
    curves=record['curves'];values={};lo=-math.inf;hi=math.inf
    for key,field in [('hall_coefficient','value_m3_C'),('resistivity','value_ohm_m')]:
        rows=curves[key];tt=np.array([v['temperature_K'] for v in rows]);vv=np.array([v[field] for v in rows]);lo=max(lo,float(tt[0]));hi=min(hi,float(tt[-1]));values[key]=float(np.interp(temperature,tt,vv))
    allowed=record.get('valid_temperature_range_K',[lo,hi]);lo=max(lo,allowed[0]);hi=min(hi,allowed[1]);valid=lo-1e-6<=temperature<=hi+1e-6
    if not valid and not clamp:return None
    return dict(hall_coefficient_m3_c=values['hall_coefficient'],resistivity_ohm_m=values['resistivity'],valid=bool(valid),domain_k=[lo,hi])

def vdp_resistances(sheet_ohm,asymmetry):return sheet_ohm*np.logaddexp(0.,np.array([asymmetry,-asymmetry]))/math.pi

def external_field_at(initial,target,dt,slew,tau):
    error=target-initial;edge=slew*tau
    if abs(error)<=edge:return target-error*math.exp(-dt/tau)
    linear_time=(abs(error)-edge)/slew
    if dt<=linear_time:return initial+math.copysign(slew*dt,error)
    return target-math.copysign(edge,error)*math.exp(-(dt-linear_time)/tau)

def _linear_fit(X,y):
    coefficient,_,rank,_=np.linalg.lstsq(X,y,rcond=None)
    if rank<X.shape[1]:raise ActionRejected('ANALYSIS_UNIDENTIFIABLE')
    return coefficient,float(np.sqrt(np.mean((np.asarray(y)-X@coefficient)**2)))

def infer_hall_vdp(observations,thickness_m,hall_geometry_factor,hall_factor,field_symmetry_relative,accept_single_carrier_model):
    if not accept_single_carrier_model:raise ActionRejected('ASSUMPTION_NOT_ACCEPTED')
    t=np.array([x['host_time_s'] for x in observations]);I=np.array([x['current_a'] for x in observations]);B=np.array([x['field_t'] for x in observations]);V=np.array([x['voltage_v'] for x in observations]);configs=np.array([x['contact_configuration'] for x in observations]);iscale=max(np.max(abs(I)),1e-30);bscale=max(np.max(abs(B)),1e-30);times=(t-np.mean(t))/max(np.ptp(t),1e-12)
    if not np.any(B>1e-6) or not np.any(B < -1e-6):raise ActionRejected('FIELD_REVERSAL_REQUIRED')
    bp=float(np.mean(B[B>0]));bn=float(np.mean(B[B<0]))
    if abs(bp+bn)/max(abs(bp),abs(bn))>field_symmetry_relative:raise ActionRejected('FIELD_PAIR_ASYMMETRY')
    resistance=[];rms=[]
    for name in CONTACTS[:2]:
        idx=configs==name
        if sum(idx)<4:raise ActionRejected('ANALYSIS_DATA')
        X=np.column_stack([I[idx]/iscale,np.ones(sum(idx)),times[idx]]);fit,error=_linear_fit(X,V[idx]);R=float(fit[0]/iscale)
        if not .01<=R<=1e7:raise ActionRejected('RESISTANCE_RANGE')
        resistance.append(R);rms.append(error)
    idx=configs=='hall'
    if sum(idx)<6:raise ActionRejected('ANALYSIS_DATA')
    X=np.column_stack([I[idx]/iscale,I[idx]*B[idx]/(iscale*bscale),np.ones(sum(idx)),times[idx]]);fit,error=_linear_fit(X,V[idx]);rms.append(error);RH=float(fit[1]/(iscale*bscale)*thickness_m/hall_geometry_factor)
    if abs(RH)<1e-30:raise ActionRejected('HALL_UNRESOLVED')
    ra,rb=resistance;sheet=brentq(lambda rs:math.exp(-math.pi*ra/rs)+math.exp(-math.pi*rb/rs)-1,min(ra,rb)*1e-6,math.pi*(ra+rb)*100,xtol=1e-13,rtol=1e-13);rho=sheet*thickness_m;nH=1/(ELEMENTARY_CHARGE_C*abs(RH));muH=abs(RH)/rho
    return dict(hall_coefficient_m3_c=RH,resistivity_ohm_m=rho,sheet_resistance_ohm=sheet,vdp_resistance_a_ohm=ra,vdp_resistance_b_ohm=rb,hall_carrier_density_m3=nH,hall_mobility_m2_v_s=muH,assumed_single_carrier_density_m3=hall_factor*nH,assumed_drift_mobility_m2_v_s=muH/hall_factor,carrier_type='hole_like_positive_hall' if RH>0 else 'electron_like_negative_hall',fit_rms_v=max(rms),evidence='external_current_and_field_reversal_voltage_fit; scalar_van_der_Pauw_and_single_carrier_Hall_factor_assumptions; not_proprietary_FastHall_algorithm')

PLANT_EXAMPLE=dict(ambient_k=300.,contact_asymmetry=.2,hall_misalignment_fraction=.02,hall_geometry_factor=1.,drive_geometry_factor=1.,current_contact_resistance_ohm=20.,compliance_voltage_v=10.,voltage_offset_a_v=2e-6,voltage_offset_b_v=-1e-6,voltage_offset_hall_v=3e-6,voltage_drift_v_s=1e-10,voltage_gain=1.,voltage_resolution_fraction=1e-7,voltage_response_tau_s=.002,temperature_resolution_k=1e-4,clamp_speed_m_s=.006,ui_tilt_speed_rad_s=.5,accept_engineering=True)
PLANT=obj({'ambient_k':number('K',283.15,313.15),'contact_asymmetry':number('1',-5,5),'hall_misalignment_fraction':number('1',-.5,.5),'hall_geometry_factor':number('1',.1,2),'drive_geometry_factor':number('1',.1,100),'current_contact_resistance_ohm':number('ohm',0,1e9),'compliance_voltage_v':number('V',1,10),'voltage_offset_a_v':number('V',-.1,.1),'voltage_offset_b_v':number('V',-.1,.1),'voltage_offset_hall_v':number('V',-.1,.1),'voltage_drift_v_s':number('V/s',-1e-3,1e-3),'voltage_gain':number('1',.1,10),'voltage_resolution_fraction':number('1',1e-12,.01),'voltage_response_tau_s':number('s',1e-5,10),'temperature_resolution_k':number('K',1e-6,1),'clamp_speed_m_s':number('m/s',.00001,.1),'ui_tilt_speed_rad_s':number('rad/s',.0001,1.),'accept_engineering':BOOL})
GEOMETRY_EXAMPLE=dict(side_m=.01,thickness_m=.0005)
GEOMETRY=obj({'side_m':number('m',.001,.1),'thickness_m':number('m',1e-7,.01)})
THERMAL_EXAMPLE=dict(minimum_temperature_k=290.,maximum_temperature_k=350.,closed_conductance_w_k=.2,open_conductance_w_k=.02,controller_gain_w_k=.5,heating_limit_w=5.,cooling_limit_w=5.,accept_external_thermal_boundary=True)
THERMAL=obj({'minimum_temperature_k':number('K',1,1500),'maximum_temperature_k':number('K',1,1500),'closed_conductance_w_k':number('W/K',1e-6,1000),'open_conductance_w_k':number('W/K',0,1000),'controller_gain_w_k':number('W/K',1e-6,1e5),'heating_limit_w':number('W',.001,1e5),'cooling_limit_w':number('W',.001,1e5),'accept_external_thermal_boundary':BOOL})
FIELD_EXAMPLE=dict(maximum_abs_t=2.,slew_t_s=1.,response_tau_s=.1,probe_gain=1.,probe_offset_t=0.,probe_resolution_t=1e-7,accept_external_magnet=True)
FIELD=obj({'maximum_abs_t':number('T',.001,100),'slew_t_s':number('T/s',1e-6,100),'response_tau_s':number('s',.001,1e5),'probe_gain':number('1',.1,10),'probe_offset_t':number('T',-1,1),'probe_resolution_t':number('T',1e-12,.1),'accept_external_magnet':BOOL})
SAMPLE_EXAMPLE=dict(reference_id='starry_40927',density_kg_m3=6000.,specific_heat_capacity_j_kg_k=300.,initial_temperature_k=300.,accept_directional_scalar_and_thermal_assumptions=True)
SAMPLE=obj({'reference_id':TEXT,'density_kg_m3':number('kg/m^3',1,30000),'specific_heat_capacity_j_kg_k':number('J/kg/K',1,10000),'initial_temperature_k':number('K',1,1500),'accept_directional_scalar_and_thermal_assumptions':BOOL})
METHOD_EXAMPLE=dict(current_range_a=.001,current_a=.0005,voltage_range_v=.001,phase_duration_s=.1,repeats=1,field_settling_tolerance_t=1e-4)
METHOD=obj({'current_range_a':dict(type='number',enum=CURRENT_RANGES,**{'x-unit':'A'}),'current_a':number('A',0,.1),'voltage_range_v':dict(type='number',enum=VOLTAGE_RANGES,**{'x-unit':'V'}),'phase_duration_s':number('s',.001,60),'repeats':dict(type='integer',minimum=1,maximum=100,**{'x-unit':'1'}),'field_settling_tolerance_t':number('T',1e-8,.1)})
VOLTAGE_EXAMPLE=dict(voltage_range_v=.1,voltage_v=.01,current_measurement_range_a=.01,current_limit_a=.1,current_resolution_fraction=1e-7)
VOLTAGE=obj({'voltage_range_v':dict(type='number',enum=VOLTAGE_EXCITATION_RANGES,**{'x-unit':'V'}),'voltage_v':number('V',0,10),'current_measurement_range_a':dict(type='number',enum=CURRENT_MEASUREMENT_RANGES,**{'x-unit':'A'}),'current_limit_a':number('A',1e-7,.1),'current_resolution_fraction':number('1',1e-12,.01)})
CALIBRATION_EXAMPLE=dict(voltage_gain_correction=1.,voltage_offset_v=0.,field_gain_correction=1.,field_offset_t=0.)
CALIBRATION=obj({'voltage_gain_correction':number('1',.1,10),'voltage_offset_v':number('V',-.1,.1),'field_gain_correction':number('1',.1,10),'field_offset_t':number('T',-1,1)})
CONTACT_SCHEMA=enum(CONTACTS)
ROW=obj({'time_s':number('s',0),'host_time_s':number('s',0),'contact_configuration':CONTACT_SCHEMA,'polarity':enum(['zero','positive','negative']),'current_a':nullable(number('A')),'voltage_v':nullable(number('V')),'field_t':nullable(number('T')),'temperature_k':nullable(number('K')),'quality':enum(['estimated','unavailable']),'error_code':nullable(TEXT)})
ANALYSIS_ROW=obj({'host_time_s':number('s',0),'contact_configuration':CONTACT_SCHEMA,'current_a':number('A'),'voltage_v':number('V'),'field_t':number('T')})
ANALYSIS_INPUT=obj({'observations':array(ANALYSIS_ROW,18,2000),'thickness_m':number('m',1e-7,.01),'hall_geometry_factor':number('1',.1,2),'hall_factor':number('1',.1,10),'field_symmetry_relative':number('1',1e-6,.1),'accept_single_carrier_model':BOOL})
INFERENCE=obj({'hall_coefficient_m3_c':number('m^3/C'),'resistivity_ohm_m':number('ohm*m',0),'sheet_resistance_ohm':number('ohm',0),'vdp_resistance_a_ohm':number('ohm',0),'vdp_resistance_b_ohm':number('ohm',0),'hall_carrier_density_m3':number('1/m^3',0),'hall_mobility_m2_v_s':number('m^2/V/s',0),'assumed_single_carrier_density_m3':number('1/m^3',0),'assumed_drift_mobility_m2_v_s':number('m^2/V/s',0),'carrier_type':enum(['hole_like_positive_hall','electron_like_negative_hall']),'fit_rms_v':number('V',0),'evidence':TEXT})
_ex=[]
for block,B in enumerate([.5,-.5]):
 for j,name in enumerate(CONTACTS):
  for k,I in enumerate([0.,.0005,-.0005]):
   tm=block*5+j*.3+k*.1;R=[.2,.3,.001*B/.0005+.03][j];_ex.append(dict(host_time_s=tm,contact_configuration=name,current_a=I,voltage_v=I*R+2e-6+1e-8*tm,field_t=B))
ANALYSIS_EXAMPLE=dict(observations=_ex,thickness_m=.0005,hall_geometry_factor=1.,hall_factor=1.,field_symmetry_relative=.01,accept_single_carrier_model=True)
RESULT=obj({'operation_id':nullable(TEXT),'phase':PHASE,'elapsed_s':number('s',0),'rows':array(ROW,0,900),'template_source':nullable(TEXT),'evidence':TEXT,'error_code':nullable(TEXT)})
CAPS={};SLOTS={}
def cap(k,i,o,c,z,kind='device_command',visibility='agent',example=None):CAPS[k]=dict(input_schema=i,output_schema=o,component_ids=c,function_zh=z,kind=kind,visibility=visibility,example_arguments=copy.deepcopy(example or {}),effects=[])
def slot(k,s,u,z,origin='derived',visibility='agent',component='controller'):SLOTS[k]=dict(schema=s,unit=u,meaning=z,origin=origin,visibility=visibility,component=component,write=None)
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller','contact_carriage'],'宿主显式接触/顺从电压/传感/几何误差参数，非OEM精度常数','configuration','evaluator',PLANT_EXAMPLE)
cap('geometry.configure',GEOMETRY,obj({'configured':BOOL}),['sample_sheet'],'方形vdP薄片几何，四角接点；装样后锁定质量体积','configuration','evaluator',GEOMETRY_EXAMPLE)
cap('thermal.configure',THERMAL,obj({'configured':BOOL}),['thermal_port'],'宿主外部工程热边界，非M91加热器或OEM温控系统','configuration','evaluator',THERMAL_EXAMPLE)
cap('field.configure',FIELD,obj({'configured':BOOL}),['field_reference'],'宿主外部磁体边界及场探头，磁体参数不冒充M91能力','configuration','evaluator',FIELD_EXAMPLE)
cap('field.set_target',obj({'field_t':number('T',-100,100)}),obj({'target_t':number('T')}),['field_reference'],'仅宿主设置外场目标；实际B受slew/τ随step演化，M91本体不造磁','simulation','evaluator',{'field_t':.5})
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_sheet'],'装入带真实RH/rho实验曲线的样品，热参数与标量方向明确接受','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.remove',obj({}),obj({'loaded':BOOL}),['sample_sheet'],'卸下整片及当前显热，保留已焦耳耗散账','configuration','evaluator')
cap('sample.set_clamp',obj({'closed':BOOL}),obj({'target_m':number('m',0,.012)}),['contact_carriage'],'宿主手动四针整体抬起/接触，有限移动代理','simulation','evaluator',{'closed':True})
cap('ui.set_tilt',obj({'angle_rad':number('rad',0,.26)}),obj({'target_rad':number('rad',0,.26)}),['controller'],'TiltView人工调角有限速度代理，范围为工程假设','simulation','evaluator',{'angle_rad':.13})
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_button'],'控制器电源；每次重新上电需宿主累计1800s预热，短按动画仅状态代理',example={'enabled':True})
cap('thermal.set_temperature',obj({'temperature_k':number('K',1,1500)}),obj({'target_k':number('K')}),['thermal_port'],'宿主外部热控制目标，温度只随step变化','simulation','evaluator',{'temperature_k':300.})
cap('thermal.disable',obj({}),obj({'enabled':BOOL}),['thermal_port'],'停止外部主动调温，已有显热和焦耳过程不回滚','simulation','evaluator')
cap('method.configure',METHOD,obj({'configured':BOOL,'programmed_current_a':number('A',0,.1)}),['controller'],'电流六档及每档1e-5分辨率，电压量程是满量程非最小信号阈值','configuration',example=METHOD_EXAMPLE)
cap('options.configure',obj({'high_resistance_firmware_enabled':BOOL}),obj({'high_resistance_firmware_enabled':BOOL}),['controller'],'宿主明确选择M9-ADD-HR固件以开放电压激励，默认未选；不宣称200GΩ全域计量','configuration','evaluator',{'high_resistance_firmware_enabled':True})
cap('excitation.voltage.configure',VOLTAGE,obj({'configured':BOOL,'programmed_voltage_v':number('V',0,10),'current_limit_a':number('A',1e-7,.1)}),['controller'],'HR固件下配置并选择电压激励：电压编程1e-5满档；真实负载电流、独立电流量程和显式分辨率模型','configuration',example=VOLTAGE_EXAMPLE)
cap('sensor.calibrate',CALIBRATION,obj({'configured':BOOL}),['controller','field_reference'],'使用外部标准给出的电压和场探头校正，不读取真值','configuration',example=CALIBRATION_EXAMPLE)
cap('acquisition.start',obj({}),obj({'operation_id':TEXT,'expected_duration_s':number('s',0)}),['controller','contact_carriage'],'在一个已稳定外场下依次采vdP-A/vdP-B/Hall的零/正/负激励观测；current模式±I、voltage模式±V')
cap('acquisition.stop',obj({}),obj({'phase':PHASE,'elapsed_s':number('s',0)}),['controller'],'停止激励并冻结已完成相，外部场和已有显热继续演化')
cap('acquisition.result',obj({}),RESULT,['controller'],'公开反转原始观测，所有会话统一隐去选中来源','query')
cap('analysis.hall_vdp',ANALYSIS_INPUT,INFERENCE,['controller'],'从两种相反外场实测代理拟合电流/磁场反转项并数值解vdP；非FastHall私有算法','analysis',example=ANALYSIS_EXAMPLE)
cap('provenance.read',obj({}),obj({'reference_id':nullable(TEXT),'template_source':nullable(TEXT),'evidence':TEXT}),['controller'],'仅宿主读取当前真实RH/rho曲线记录','query','evaluator')
LEDGER_UNITS=dict(mass_loaded_kg='kg',mass_present_kg='kg',mass_removed_kg='kg',mass_error_kg='kg',volume_loaded_m3='m^3',volume_present_m3='m^3',volume_removed_m3='m^3',volume_error_m3='m^3',source_input_j='J',sample_joule_j='J',contact_heat_export_j='J',electrical_error_j='J',controller_input_j='J',ambient_loss_j='J',loaded_sensible_j='J',removed_sensible_j='J',thermal_stored_j='J',thermal_energy_error_j='J')
cap('ledger.read',obj({}),obj({k:number(u) for k,u in LEDGER_UNITS.items()}),['controller'],'样品质量/体积/显热/焦耳、接触热外流与外部温控账；不计外部磁体供电','query','evaluator')
cap('sim.step',obj({'dt_s':number('s',0,86400)}),obj({'time_s':number('s',0)}),['controller'],'单一宿主时间推进真实1800s预热、外部磁场、温度与反转采样','simulation','evaluator',{'dt_s':10.})
UNSUPPORTED=['hardware.connect','robot.load','measurement.fasthall_proprietary','measurement.hall_bar','measurement.high_resistance_200gohm_range','measurement.multicarrier_tensor','noise.empirical','accuracy.certification']
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'未实现：'+k)
for k,c in [('plant.configured','controller'),('geometry.configured','sample_sheet'),('thermal.configured','thermal_port'),('field.configured','field_reference'),('sample.present','sample_sheet'),('power.enabled','power_button'),('warmup.ready','controller'),('thermal.enabled','thermal_port'),('clamp.moving','contact_carriage')]:slot(k,BOOL,'1','当前配置/运行状态',component=c)
for k,z in [('clamp.position_m','四针当前上提量'),('clamp.target_m','四针目标上提量')]:slot(k,number('m',0,.012),'m',z+'；0接触/.012装卸开放','setpoint' if 'target' in k else 'scene',component='contact_carriage')
for k,z in [('ui.tilt_rad','TiltView当前手动代理角'),('ui.target_tilt_rad','TiltView目标手动代理角')]:slot(k,number('rad',0,.26),'rad',z+'，范围和速度为工程假设','setpoint' if 'target' in k else 'scene',component='controller')
slot('power.position_m',number('m',0,.0015),'m','电源按键100ms三角按下释放姿态代理，不把通电状态当永久按住','scene',component='power_button')
slot('warmup.elapsed_s',number('s',0,1800),'s','当前连续供电累计预热，1800s封顶，掉电清零')
slot('thermal.target_k',number('K'),'K','宿主外部热边界目标','setpoint',component='thermal_port')
slot('thermal.indicated_k',nullable(number('K')),'K','样品与固定0.1J/K夹具集中节点量化观测','sensor',component='sample_sheet')
slot('field.target_t',number('T'),'T','宿主外部磁体目标，不是M91磁场输出','setpoint',component='field_reference')
slot('field.indicated_t',nullable(number('T')),'T','外部探头增益/偏置/量化后的垂直样品场观测','sensor',component='field_reference')
slot('excitation.programmed_current_a',number('A',0,.1),'A','经当前量程1e-5分辨率量化的电流幅度','setpoint')
slot('options.high_resistance_firmware_enabled',BOOL,'1','宿主明确配置的M9-ADD-HR固件选型，默认关闭')
slot('excitation.mode',enum(['current','voltage']),'1','当前激励模式；method.configure回到电流模式，电压模式须已选HR固件','setpoint')
slot('excitation.programmed_voltage_v',number('V',0,10),'V','电压模式经1e-5满档量化的源幅度，电流模式为零','setpoint')
slot('excitation.current_measurement_range_a',nullable(number('A',1e-8,.1)),'A','电压模式下独立电流观测满量程；不会据此限制实际电流','setpoint')
slot('excitation.source_voltage_v',nullable(number('V',-10,10)),'V','当前实际源端电压模型估计，触发限流或顺从后按真实负载计算，不等于四接点输入电压','derived')
slot('measured.current_a',nullable(number('A')),'A','电流模式为实际源电流估计；电压模式为显式分辨率量化电流观测，独立测量档溢出不可用','sensor')
slot('measured.voltage_v',nullable(number('V')),'V','当前接点配置的量化电压观测；不把所有相都叫Hall电压','sensor')
slot('acquisition.contact_configuration',enum(CONTACTS+['idle']),'1','当前真实测量接点配置')
slot('acquisition.polarity',enum(['zero','positive','negative']),'1','当前电流相，停止时为零')
slot('clock.time_s',number('s',0),'s','唯一宿主时钟')
slot('acquisition.phase',PHASE,'1','异步反转采样状态')
slot('acquisition.elapsed_s',number('s',0),'s','本次真实采样时长')
slot('acquisition.points',{'type':'integer','minimum':0,'maximum':900,'x-unit':'1'},'1','实际完成相数')
for k,u in [('truth.sample_k','K'),('truth.field_t','T'),('truth.sample_mass_kg','kg')]:slot(k,nullable(number(u)),u,'宿主真实样品/外部场状态，非代理可见真值','process_truth','evaluator')

class LakeShoreMeasurereadyM91Runtime:
    def __init__(self,instance_id='m91_01'):
        self.instance_id=instance_id;self.plant=None;self.geometry=None;self.thermal=None;self.field=None;self.sample=None;self.record=None;self.power=False;self._power_started_ns=None;self._button_started_ns=-1000000000;self.thermal_enabled=False;self.target=300.;self.field_actual=0.;self.field_target=0.;self.axes={'clamp':dict(position=.012,target=.012,start=.012,start_ns=0,arrival_ns=0),'ui':dict(position=0.,target=0.,start=0.,start_ns=0,arrival_ns=0)};self.x=np.zeros(7);self.time_ns=0;self._clock_remainder_ns=0.;self.revision=0;self.method=copy.deepcopy(METHOD_EXAMPLE);self.programmed_current=METHOD_EXAMPLE['current_a'];self.calibration=copy.deepcopy(CALIBRATION_EXAMPLE);self.high_resistance_firmware_enabled=False;self.excitation_mode='current';self.voltage=copy.deepcopy(VOLTAGE_EXAMPLE);self.programmed_voltage=VOLTAGE_EXAMPLE['voltage_v'];self.current_limit=VOLTAGE_EXAMPLE['current_limit_a'];self.phase='idle';self.elapsed=0.;self.rows=[];self.error=None;self.operation_id=None;self._counter=0;self._start_ns=0;self._phase_index=0;self.expected_duration=0.;self.records={};self._principals={};self._requests={};self._owners={};self._cancel_operations={};self._source=None;self._reference_id=None;self._evidence='no_sample';self._operation_source=None;self._operation_evidence='no_measurement';self.mass_loaded=self.mass_removed=self.volume_loaded=self.volume_removed=self.loaded_sensible=self.removed_sensible=0.
    @property
    def time_s(self):return self.time_ns/1e9
    def register_principal(self,principal_id,visibility='agent'):
        if not isinstance(principal_id,str) or not principal_id or principal_id in self._principals or visibility not in ('agent','debug','evaluator'):raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id):
        if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return LakeShoreMeasurereadyM91Session(self,principal_id)
    def describe(self):return export_lakeshore_measurereadym91_contract_metadata()
    def _warmup(self):return min(WARMUP_S,(self.time_ns-self._power_started_ns)/1e9) if self.power and self._power_started_ns is not None else 0.
    def _moving(self,key):return self.time_ns<self.axes[key]['arrival_ns']
    def _closed(self):return self.axes['clamp']['position']<=1e-12 and self.axes['clamp']['target']==0.
    def _volume(self):return self.geometry['side_m']**2*self.geometry['thickness_m'] if self.sample else 0.
    def _mass(self):return self._volume()*self.sample['density_kg_m3'] if self.sample else 0.
    def _capacity(self):return .1+(self._mass()*self.sample['specific_heat_capacity_j_kg_k'] if self.sample else 0.)
    def _temperature(self):return self.plant['ambient_k']+float(self.x[0]) if self.plant else None
    def _contact(self):return CONTACTS[(self._phase_index//3)%3] if self.phase=='running' else 'idle'
    def _polarity(self):return ['zero','positive','negative'][self._phase_index%3] if self.phase=='running' else 'zero'
    def _button_position(self):
        t=(self.time_ns-self._button_started_ns)/1e9
        return .0015*(1-abs(t-.05)/.05) if 0<=t<=.1 else 0.
    def _field_at(self,t):return external_field_at(self.field_actual,self.field_target,t,self.field['slew_t_s'],self.field['response_tau_s']) if self.field else self.field_actual
    def _field_observed(self,B=None):
        if not self.field:return None
        B=self.field_actual if B is None else B;f=self.field;c=self.calibration;v=(f['probe_gain']*B+f['probe_offset_t']-c['field_offset_t'])*c['field_gain_correction'];return float(round(v/f['probe_resolution_t'])*f['probe_resolution_t'])
    def _electrical(self,T=None,B=None):
        if not self.sample or not self.power or self.phase!='running' or not self._closed():return dict(current=0.,requested=0.,voltage=0.,source_voltage=0.,sample_power=0.,contact_power=0.,valid=True,compliance=False,resistance=0.)
        T=self._temperature() if T is None else T;B=self.field_actual if B is None else B;v=transport(self.record,T,clamp=True);sheet=v['resistivity_ohm_m']/self.geometry['thickness_m'];ra,rb=vdp_resistances(sheet,self.plant['contact_asymmetry']);bulk=sheet*self.plant['drive_geometry_factor'];contact=self.plant['current_contact_resistance_ohm'];sign={'zero':0.,'positive':1.,'negative':-1.}[self._polarity()]
        if self.excitation_mode=='voltage':
            requested=self.programmed_voltage*sign/(bulk+contact);I=math.copysign(min(abs(requested),self.current_limit),requested)
        else:
            requested=self.programmed_current*sign;I=math.copysign(min(abs(requested),self.plant['compliance_voltage_v']/(bulk+contact)),requested)
        configuration=self._contact();R=float(ra if configuration=='vdp_a' else rb if configuration=='vdp_b' else v['hall_coefficient_m3_c']*B/self.geometry['thickness_m']*self.plant['hall_geometry_factor']+self.plant['hall_misalignment_fraction']*math.sqrt(ra*rb));return dict(current=I,requested=requested,voltage=I*R,source_voltage=I*(bulk+contact),sample_power=I*I*bulk,contact_power=I*I*contact,valid=v['valid'],compliance=abs(I-requested)>max(abs(requested)*1e-10,1e-18),resistance=R)
    def _observed_current(self,e=None):
        if not self.power or self.phase!='running':return None
        e=self._electrical() if e is None else e
        if self.excitation_mode=='current':return e['current']
        full=self.voltage['current_measurement_range_a']
        if abs(e['current'])>full:return None
        q=full*self.voltage['current_resolution_fraction'];return float(round(e['current']/q)*q)
    def _observed_voltage(self):
        if not self.power or self.phase!='running':return None
        c=self.calibration;voltage=(self.x[1]-c['voltage_offset_v'])*c['voltage_gain_correction']
        if abs(voltage)>self.method['voltage_range_v']:return None
        q=self.method['voltage_range_v']*self.plant['voltage_resolution_fraction'];return float(round(voltage/q)*q)
    def read(self,role='evaluator'):
        if role not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
        active=self.power and self.phase=='running';e=self._electrical();d={'plant.configured':self.plant is not None,'geometry.configured':self.geometry is not None,'thermal.configured':self.thermal is not None,'field.configured':self.field is not None,'sample.present':self.sample is not None,'power.enabled':self.power,'warmup.ready':self._warmup()>=WARMUP_S,'thermal.enabled':self.thermal_enabled,'clamp.moving':self._moving('clamp'),'clamp.position_m':self.axes['clamp']['position'],'clamp.target_m':self.axes['clamp']['target'],'ui.tilt_rad':self.axes['ui']['position'],'ui.target_tilt_rad':self.axes['ui']['target'],'power.position_m':self._button_position(),'warmup.elapsed_s':self._warmup(),'thermal.target_k':self.target,'thermal.indicated_k':float(round(self._temperature()/self.plant['temperature_resolution_k'])*self.plant['temperature_resolution_k']) if self.plant and self.thermal else None,'field.target_t':self.field_target,'field.indicated_t':self._field_observed(),'excitation.programmed_current_a':self.programmed_current,'measured.current_a':self._observed_current(e),'options.high_resistance_firmware_enabled':self.high_resistance_firmware_enabled,'excitation.mode':self.excitation_mode,'excitation.programmed_voltage_v':self.programmed_voltage if self.excitation_mode=='voltage' else 0.,'excitation.current_measurement_range_a':self.voltage['current_measurement_range_a'] if self.excitation_mode=='voltage' else None,'excitation.source_voltage_v':e['source_voltage'] if active else None,'measured.voltage_v':self._observed_voltage(),'acquisition.contact_configuration':self._contact(),'acquisition.polarity':self._polarity(),'clock.time_s':self.time_s,'acquisition.phase':self.phase,'acquisition.elapsed_s':self.elapsed,'acquisition.points':len(self.rows),'truth.sample_k':self._temperature(),'truth.field_t':self.field_actual if self.field else None,'truth.sample_mass_kg':self._mass() if self.sample else None}
        return {k:v for k,v in d.items() if role!='agent' or SLOTS[k]['visibility']=='agent'}
    def _propagate(self,ns):
        if not ns:return
        dt=ns/1e9;field_end=self._field_at(dt)
        if not self.plant or not self.thermal:self.field_actual=field_end;return
        p=self.plant;th=self.thermal;C=self._capacity();G=th['closed_conductance_w_k'] if self._closed() else th['open_conductance_w_k'];K=th['controller_gain_w_k'] if self.thermal_enabled and self._closed() else 0.;target=self.target-p['ambient_k'];active=self.power and self.phase=='running'
        def thermal_control(delta):return min(th['heating_limit_w'],max(-th['cooling_limit_w'],(K+G)*target-K*delta)) if K else 0.
        if active:
            name=self._contact();offset=p[{'vdp_a':'voltage_offset_a_v','vdp_b':'voltage_offset_b_v','hall':'voltage_offset_hall_v'}[name]];start_time=self.time_s
            def rhs(t,y):
                e=self._electrical(p['ambient_k']+y[0],self._field_at(t));controller=thermal_control(y[0]);ambient=G*y[0];signal=p['voltage_gain']*e['voltage']+offset+p['voltage_drift_v_s']*(start_time+t);return [(controller-ambient+e['sample_power'])/C,(signal-y[1])/p['voltage_response_tau_s'],e['sample_power'],e['contact_power'],e['current']*e['source_voltage'],controller,ambient]
            sol=solve_ivp(rhs,(0.,dt),self.x,method='Radau',rtol=1e-10,atol=[1e-11,1e-14,1e-15,1e-15,1e-15,1e-12,1e-12])
            if not sol.success:raise RuntimeError('INTEGRATION_FAILED')
            self.x=sol.y[:,-1]
        else:
            initial=float(self.x[0]);low=min(initial,0.,target);high=max(initial,0.,target);saturated=K and ((K+G)*target-K*low>th['heating_limit_w'] or (K+G)*target-K*high < -th['cooling_limit_w'])
            if saturated:
                def rhs(_t,y):
                    control=thermal_control(y[0]);ambient=G*y[0];return [(control-ambient)/C,control,ambient]
                sol=solve_ivp(rhs,(0.,dt),[self.x[0],self.x[5],self.x[6]],method='Radau',rtol=1e-10,atol=1e-11)
                if not sol.success:raise RuntimeError('THERMAL_INTEGRATION_FAILED')
                self.x[[0,5,6]]=sol.y[:,-1]
            else:
                A=np.zeros((4,4));A[0,0]=-(G+K)/C;A[0,3]=(K+G)*target/C if K else 0.;A[1,0]=-K;A[1,3]=(K+G)*target if K else 0.;A[2,0]=G;self.x[[0,5,6]]=(expm(A*dt)@np.array([self.x[0],self.x[5],self.x[6],1.]))[:3]
            self.x[1]*=math.exp(-dt/p['voltage_response_tau_s'])
        self.field_actual=field_end
    def _ledger(self):
        stored=self._capacity()*float(self.x[0]);joule,contact,source,control,ambient=self.x[2:];return dict(mass_loaded_kg=self.mass_loaded,mass_present_kg=self._mass(),mass_removed_kg=self.mass_removed,mass_error_kg=self.mass_loaded-self._mass()-self.mass_removed,volume_loaded_m3=self.volume_loaded,volume_present_m3=self._volume(),volume_removed_m3=self.volume_removed,volume_error_m3=self.volume_loaded-self._volume()-self.volume_removed,source_input_j=float(source),sample_joule_j=float(joule),contact_heat_export_j=float(contact),electrical_error_j=float(source-joule-contact),controller_input_j=float(control),ambient_loss_j=float(ambient),loaded_sensible_j=self.loaded_sensible,removed_sensible_j=self.removed_sensible,thermal_stored_j=stored,thermal_energy_error_j=float(control+joule-ambient+self.loaded_sensible-self.removed_sensible-stored))
    def _result(self):return dict(operation_id=self.operation_id,phase=self.phase,elapsed_s=self.elapsed,rows=copy.deepcopy(self.rows),template_source=self._operation_source,evidence=self._operation_evidence,error_code=self.error)
    def _finish(self,phase,error=None):
        self.phase=phase;self.error=error
        if self.operation_id:self.records[self.operation_id]=self._result()
    def _record(self):
        e=self._electrical();voltage=self._observed_voltage();current=self._observed_current(e);error='REFERENCE_TEMPERATURE_RANGE' if not e['valid'] else 'SOURCE_COMPLIANCE' if e['compliance'] else 'CURRENT_MEASUREMENT_OVERFLOW' if current is None else 'VOLTAGE_OVERFLOW' if voltage is None else None
        if not error and self._contact()!='hall' and self._polarity()!='zero' and not .01<=abs(e['resistance'])<=1e7:error='RESISTANCE_RANGE'
        self.rows.append(dict(time_s=self.elapsed,host_time_s=self.time_s,contact_configuration=self._contact(),polarity=self._polarity(),current_a=current,voltage_v=voltage,field_t=self._field_observed(),temperature_k=self.read()['thermal.indicated_k'],quality='unavailable' if error else 'estimated',error_code=error));self._phase_index+=1
        if error:self._finish('failed',error)
        elif self._phase_index>=9*self.method['repeats']:self._finish('succeeded')
    def invoke(self,capability,**a):
        if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability]['input_schema'],a)
        if capability in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
        if capability=='analysis.hall_vdp':return infer_hall_vdp(**a)
        if capability=='acquisition.result':return self._result()
        if capability=='provenance.read':return dict(reference_id=self._reference_id,template_source=self._source,evidence=self._evidence)
        if capability=='ledger.read':return self._ledger()
        if capability=='sim.step':self.step(a['dt_s']);return {'time_s':self.time_s}
        if self.phase=='running' and capability not in ('acquisition.stop','power.set'):raise ActionRejected('BUSY')
        if capability=='plant.configure':
            if self.plant or not a['accept_engineering']:raise ActionRejected('PRECONDITION_FAILED')
            self.plant=copy.deepcopy(a);self.target=a['ambient_k'];out={'configured':True}
        elif capability=='geometry.configure':
            if self.sample:raise ActionRejected('PRECONDITION_FAILED')
            self.geometry=copy.deepcopy(a);out={'configured':True}
        elif capability=='thermal.configure':
            if not self.plant or self.thermal or not a['accept_external_thermal_boundary'] or not a['minimum_temperature_k']<=self.plant['ambient_k']<=a['maximum_temperature_k'] or a['minimum_temperature_k']>=a['maximum_temperature_k']:raise ActionRejected('THERMAL_ENVELOPE')
            self.thermal=copy.deepcopy(a);out={'configured':True}
        elif capability=='field.configure':
            if self.field or not a['accept_external_magnet']:raise ActionRejected('PRECONDITION_FAILED')
            self.field=copy.deepcopy(a);out={'configured':True}
        elif capability=='field.set_target':
            if not self.field or abs(a['field_t'])>self.field['maximum_abs_t']:raise ActionRejected('FIELD_ENVELOPE')
            self.field_target=a['field_t'];out={'target_t':self.field_target}
        elif capability=='sample.load':
            if not self.plant or not self.thermal or not self.geometry or self.sample or self._moving('clamp') or self.axes['clamp']['position']<.011 or not a['accept_directional_scalar_and_thermal_assumptions']:raise ActionRejected('PRECONDITION_FAILED')
            record=library().get(a['reference_id'])
            if record is None:raise ActionRejected('UNKNOWN_REFERENCE')
            if transport(record,a['initial_temperature_k']) is None:raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
            if not self.thermal['minimum_temperature_k']<=a['initial_temperature_k']<=self.thermal['maximum_temperature_k']:raise ActionRejected('THERMAL_ENVELOPE')
            old=self._capacity();self.sample=copy.deepcopy(a);self.record=copy.deepcopy(record);new=self._capacity();delta=a['initial_temperature_k']-self.plant['ambient_k'];self.x[0]=(old*self.x[0]+(new-old)*delta)/new;self.loaded_sensible+=(new-old)*delta;self.mass_loaded+=self._mass();self.volume_loaded+=self._volume();self._reference_id=a['reference_id'];self._source=json.dumps(record['source'],ensure_ascii=False,sort_keys=True);self._evidence='experimental_signed_Hall_and_resistivity_digitized_curves; scalar_directional_and_thermal_parameters_explicit_assumptions';out={'loaded':True}
        elif capability=='sample.remove':
            if not self.sample or self._moving('clamp') or self.axes['clamp']['position']<.011:raise ActionRejected('PRECONDITION_FAILED')
            self.mass_removed+=self._mass();self.volume_removed+=self._volume();self.removed_sensible+=self._mass()*self.sample['specific_heat_capacity_j_kg_k']*float(self.x[0]);self.sample=self.record=None;self._source=self._reference_id=None;self._evidence='no_sample';out={'loaded':False}
        elif capability in ('sample.set_clamp','ui.set_tilt'):
            if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
            key='clamp' if capability=='sample.set_clamp' else 'ui';target=(0. if a['closed'] else .012) if key=='clamp' else a['angle_rad'];speed=self.plant['clamp_speed_m_s' if key=='clamp' else 'ui_tilt_speed_rad_s'];axis=self.axes[key];axis.update(target=target,start=axis['position'],start_ns=self.time_ns,arrival_ns=self.time_ns+round(abs(target-axis['position'])/speed*1e9));out={'target_m' if key=='clamp' else 'target_rad':target}
        elif capability=='power.set':
            if not a['enabled'] and self.phase=='running':self._finish('cancelled','POWER_OFF')
            if a['enabled']!=self.power:self._button_started_ns=self.time_ns;self._power_started_ns=self.time_ns if a['enabled'] else None
            self.power=a['enabled'];out={'enabled':self.power}
        elif capability=='thermal.set_temperature':
            if not self.thermal or not self.thermal['minimum_temperature_k']<=a['temperature_k']<=self.thermal['maximum_temperature_k']:raise ActionRejected('THERMAL_ENVELOPE')
            self.target=a['temperature_k'];self.thermal_enabled=True;out={'target_k':self.target}
        elif capability=='thermal.disable':self.thermal_enabled=False;out={'enabled':False}
        elif capability=='method.configure':
            if a['current_a']>a['current_range_a'] or abs(a['phase_duration_s']*1e9-round(a['phase_duration_s']*1e9))>1e-4:raise ActionRejected('CURRENT_OR_TIME_RANGE')
            quantum=a['current_range_a']*1e-5;programmed=float(round(a['current_a']/quantum)*quantum);self.method=copy.deepcopy(a);self.programmed_current=programmed;self.excitation_mode='current';out={'configured':True,'programmed_current_a':programmed}
        elif capability=='options.configure':
            if not a['high_resistance_firmware_enabled'] and self.excitation_mode=='voltage':raise ActionRejected('EXCITATION_MODE_ACTIVE')
            self.high_resistance_firmware_enabled=a['high_resistance_firmware_enabled'];out={'high_resistance_firmware_enabled':self.high_resistance_firmware_enabled}
        elif capability=='excitation.voltage.configure':
            if not self.high_resistance_firmware_enabled:raise ActionRejected('HIGH_RESISTANCE_FIRMWARE_REQUIRED')
            if a['voltage_v']>a['voltage_range_v']:raise ActionRejected('VOLTAGE_EXCITATION_RANGE')
            voltage_quantum=a['voltage_range_v']*1e-5;programmed=float(round(a['voltage_v']/voltage_quantum)*voltage_quantum);limit_full=next(fs for fs in CURRENT_RANGES if fs>=a['current_limit_a']);limit_quantum=limit_full*.001;limit=float(round(a['current_limit_a']/limit_quantum)*limit_quantum)
            self.voltage=copy.deepcopy(a);self.programmed_voltage=programmed;self.current_limit=limit;self.excitation_mode='voltage';out={'configured':True,'programmed_voltage_v':programmed,'current_limit_a':limit}
        elif capability=='sensor.calibrate':self.calibration=copy.deepcopy(a);out={'configured':True}
        elif capability=='acquisition.start':
            if not self.power or not self.plant or not self.geometry or not self.thermal or not self.field or not self.sample or not self._closed() or self._moving('clamp'):raise ActionRejected('PRECONDITION_FAILED')
            if self._warmup()<WARMUP_S:raise ActionRejected('WARMUP_REQUIRED')
            if (self.programmed_current if self.excitation_mode=='current' else self.programmed_voltage)<=0:raise ActionRejected('ZERO_EXCITATION')
            if self.excitation_mode=='voltage' and not self.high_resistance_firmware_enabled:raise ActionRejected('HIGH_RESISTANCE_FIRMWARE_REQUIRED')
            if abs(self.field_target-self.field_actual)>self.method['field_settling_tolerance_t']:raise ActionRejected('FIELD_NOT_SETTLED')
            if transport(self.record,self._temperature()) is None:raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
            self._counter+=1;self.operation_id=f'{self.instance_id}:acquisition:{self._counter}';self._operation_source=self._source;self._operation_evidence=self._evidence;self.phase='running';self.elapsed=0.;self.rows=[];self.error=None;self._start_ns=self.time_ns;self._phase_index=0;self.expected_duration=9*self.method['repeats']*round(self.method['phase_duration_s']*1e9)/1e9;out={'operation_id':self.operation_id,'expected_duration_s':self.expected_duration}
        elif capability=='acquisition.stop':
            if self.phase!='running':raise ActionRejected('NOT_RUNNING')
            self._finish('cancelled','USER_CANCEL');out={'phase':self.phase,'elapsed_s':self.elapsed}
        else:raise ActionRejected('UNKNOWN_CAPABILITY')
        self.revision+=1;return copy.deepcopy(out)
    def step(self,dt_s):
        validate(number('s',0,86400),dt_s);total=math.fsum((self._clock_remainder_ns,dt_s*1e9));ns=round(total);self._clock_remainder_ns=total-ns
        if not ns:return
        end=self.time_ns+ns
        while self.time_ns<end:
            boundary=end
            for key in self.axes:
                if self._moving(key):boundary=min(boundary,self.axes[key]['arrival_ns'])
            if self.phase=='running':boundary=min(boundary,self._start_ns+(self._phase_index+1)*round(self.method['phase_duration_s']*1e9))
            self._propagate(boundary-self.time_ns);self.time_ns=boundary
            if self.plant:
                for key,axis in self.axes.items():
                    speed=self.plant['clamp_speed_m_s' if key=='clamp' else 'ui_tilt_speed_rad_s'];axis['position']=axis['target'] if self.time_ns>=axis['arrival_ns'] else axis['start']+math.copysign(speed*(self.time_ns-axis['start_ns'])/1e9,axis['target']-axis['start'])
            if self.phase=='running':
                self.elapsed=(self.time_ns-self._start_ns)/1e9
                if self.time_ns==self._start_ns+(self._phase_index+1)*round(self.method['phase_duration_s']*1e9):self._record()
        self.revision+=1

class LakeShoreMeasurereadyM91Session:
    def __init__(self, host, principal_id): self._host = host; self._principal = principal_id
    @property
    def _role(self): return self._host._principals[self._principal]
    def _target(self, instance_id):
        if instance_id != self._host.instance_id: raise ActionRejected('UNKNOWN_INSTANCE')
    def describe(self, *, instance_id=None, asset_type_id=None):
        if (instance_id is None) == (asset_type_id is None): raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None: self._target(instance_id)
        if asset_type_id is not None and asset_type_id != ASSET_TYPE_ID: raise ActionRejected('UNKNOWN_INSTANCE')
        meta = export_lakeshore_measurereadym91_contract_metadata()
        return dict(protocol_version=PROTOCOL_VERSION, asset_type_id=ASSET_TYPE_ID, asset_version='1.0.0', capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'}, state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self, *, instance_id, state_ids=None):
        self._target(instance_id); h=self._host; values=h.read(self._role); ids=list(values) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids): raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in values for k in ids): raise ActionRejected('ACCESS_DENIED')
        return dict(instance_id=instance_id, status='succeeded', state_revision=h.revision, clock={'domain':'simulation','time_s':h.time_s}, observations={k:dict(value=copy.deepcopy(values[k]),unit=SLOTS[k]['unit'],quality='unavailable' if values[k] is None else 'estimated' if SLOTS[k]['origin'] in ('sensor','derived') else 'valid',sample_time_s=h.time_s,sample_window_s=None) for k in ids})
    def _filter_result(self, result):
        result = copy.deepcopy(result)
        # A bridge may dispatch agent-visible capabilities through a privileged
        # session. Public acquisition payloads therefore use the SAME redaction
        # for every role. Full provenance has its own evaluator-only capability.
        if 'rows' in result and 'template_source' in result:
            result['template_source'] = str(LIBRARY_PATH.relative_to(ROOT)) + '; selected record provenance available only through evaluator provenance.read' if result['template_source'] is not None else None
            result['evidence'] = 'observed_contact_voltages_current_and_external_field_with_explicit_scalar_Hall_vdP_assumptions; selected template details require evaluator provenance.read' if result['operation_id'] is not None else 'no_measurement'
        return result
    def invoke(self, *, instance_id, capability_id, arguments, request_id):
        h=self._host
        try:
            self._target(instance_id)
            if not isinstance(capability_id,str) or capability_id not in CAPS: raise ActionRejected('UNKNOWN_CAPABILITY')
            if self._role=='agent' and CAPS[capability_id]['visibility']!='agent': raise ActionRejected('ACCESS_DENIED')
            if not isinstance(request_id,str) or not request_id: raise ActionRejected('INVALID_ARGUMENT')
            validate(CAPS[capability_id]['input_schema'],arguments); key=(self._principal,request_id); digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
            if key in h._requests:
                old,response=h._requests[key]
                if old!=digest: raise ActionRejected('REQUEST_CONFLICT')
                return copy.deepcopy(response)
            if capability_id in ('acquisition.stop','acquisition.result') and self._role=='agent' and h.operation_id and h._owners.get(h.operation_id)!=self._principal: raise ActionRejected('ACCESS_DENIED')
            if capability_id=='power.set' and not arguments['enabled'] and h.phase=='running' and self._role=='agent' and h._owners.get(h.operation_id)!=self._principal: raise ActionRejected('ACCESS_DENIED')
            result=self._filter_result(h.invoke(capability_id,**arguments)); operation=result.get('operation_id') if capability_id=='acquisition.start' else None
            if operation: h._owners[operation]=self._principal
            response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=operation,status='accepted' if operation else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None)
            h._requests[key]=(digest,copy.deepcopy(response)); return response
        except ActionRejected as exc:
            return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,status='unsupported' if str(exc)=='UNSUPPORTED' else 'rejected',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))
    def write(self, *, instance_id, state_id, value, request_id):
        self._target(instance_id); entry=SLOTS.get(state_id)
        if not entry or not entry['write']: raise ActionRejected('ACCESS_DENIED')
        capability,argument=entry['write']; return self.invoke(instance_id=instance_id,capability_id=capability,arguments={argument:value},request_id=request_id)
    def poll(self, operation_id):
        h=self._host
        if h._owners.get(operation_id)!=self._principal: raise ActionRejected('ACCESS_DENIED')
        result=h._result() if operation_id==h.operation_id and h.phase=='running' else h.records.get(operation_id)
        if result is None: raise ActionRejected('UNKNOWN_OPERATION')
        return dict(operation_id=operation_id,status=result['phase'],result=self._filter_result(result))
    def cancel(self, operation_id, request_id):
        self.poll(operation_id); h=self._host
        if not isinstance(request_id,str) or not request_id: raise ActionRejected('INVALID_ARGUMENT')
        key=(self._principal,request_id)
        if key in h._cancel_operations:
            if h._cancel_operations[key]!=operation_id: raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(h._requests[key][1])
        if key in h._requests: raise ActionRejected('REQUEST_CONFLICT')
        if operation_id!=h.operation_id or h.phase!='running': raise ActionRejected('NOT_RUNNING')
        result=self.invoke(instance_id=h.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        if result['status']=='succeeded': h._cancel_operations[key]=operation_id
        return result

EFFECTS={
 'plant.configure':['plant.configured','thermal.target_k','truth.sample_k'],
 'geometry.configure':['geometry.configured'],
 'thermal.configure':['thermal.configured','thermal.indicated_k'],
 'field.configure':['field.configured','field.indicated_t','truth.field_t'],
 'field.set_target':['field.target_t'],
 'sample.load':['sample.present','truth.sample_mass_kg','truth.sample_k','thermal.indicated_k'],
 'sample.remove':['sample.present','truth.sample_mass_kg'],
 'sample.set_clamp':['clamp.target_m','clamp.moving'],
 'ui.set_tilt':['ui.target_tilt_rad'],
 'power.set':['power.enabled','power.position_m','warmup.ready','warmup.elapsed_s','acquisition.phase','measured.current_a','measured.voltage_v','acquisition.contact_configuration','acquisition.polarity','excitation.source_voltage_v'],
 'thermal.set_temperature':['thermal.target_k','thermal.enabled'],'thermal.disable':['thermal.enabled'],
 'method.configure':['excitation.programmed_current_a','excitation.mode','excitation.programmed_voltage_v','excitation.current_measurement_range_a'],
 'options.configure':['options.high_resistance_firmware_enabled'],
 'excitation.voltage.configure':['excitation.mode','excitation.programmed_voltage_v','excitation.current_measurement_range_a'],
 'sensor.calibrate':['field.indicated_t','measured.voltage_v'],
 'acquisition.start':['acquisition.phase','acquisition.elapsed_s','acquisition.points','acquisition.contact_configuration','acquisition.polarity','measured.current_a','measured.voltage_v','excitation.source_voltage_v'],
 'acquisition.stop':['acquisition.phase','measured.current_a','measured.voltage_v','acquisition.contact_configuration','acquisition.polarity','excitation.source_voltage_v'],
 'sim.step':list(SLOTS)}
for k,v in EFFECTS.items():CAPS[k]['effects']=v
ERRORS=sorted(set(__import__('re').findall(r"ActionRejected\('([A-Z_0-9]+)'",Path(__file__).read_text()))|{'POWER_OFF','USER_CANCEL','SOURCE_COMPLIANCE','CURRENT_MEASUREMENT_OVERFLOW','VOLTAGE_OVERFLOW','REFERENCE_TEMPERATURE_RANGE','RESISTANCE_RANGE'})

def export_lakeshore_measurereadym91_contract_metadata():
    slots={};caps={}
    for k,v in SLOTS.items():
        slots[k]=dict(meaning_zh=v['meaning'],component_ids=[v['component']],value_schema=copy.deepcopy(v['schema']),unit=v['unit'],reference_conditions='SI; four-corner thin uniform scalar sheet; external normal B; conventional current and external-field reversal.',origin=v['origin'],visibility=v['visibility'],access='read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'LakeShoreMeasurereadyM91Session.read(state_ids=[{k!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description='Only explicit host step advances field, thermal, motion, detector response and 1800s warmup. Each zero/positive/negative excitation phase records its endpoint after declared dwell: current mode uses +/-I and voltage mode uses +/-V.'),source_ids=['simulation_assumption'])
    limits=[
      '本体是M91 Hall控制器；磁体、样品夹具、场探头和温控边界由宿主明确组合，不虚构机内磁场或样品加热器。外部磁体供电/磁能不在本体电热账内。',
      '本模型实现M91实际支持的传统电流/外部磁场反转vdP子集。FastHall无需物理反磁私有算法、Hall bar及200GΩ全域高阻计量未实现；电压激励明确依赖宿主选择M9-ADD-HR固件；不把unsupported负例称实现验证。',
      '上电后只由真实宿主时间累计1800s预热，每次掉电清零；read/配置不推进时间，重复power.set(True)不重置已连续预热。',
      '电流源六量程1µA至100mA，每档编程步长1e-5×满量程；1µA档10pA是分辨率，1µA不是可编程电流下限。电压观测五量程1mV至10V是满量程，微伏信号不因小于1mV而拒绝。',
      '电压源四满量程10mV/100mV/1V/10V，编程步长1e-5×量程，10mV档可编程100nV而非最小输出10mV。仅M9-ADD-HR固件明确启用后可切换电压模式；method.configure切回电流模式。',
      '电压模式实际I=Vset/(Rdrive+Rcontact)，源电流limit按0.1%满档量化，自动满档采用最小可容纳限值电流档的显式工程规则（厂家未公开该自动选档细节），SCPI保守范围100nA至100mA；概述0至100mA与SCPI下限不同已记录。限流后实际Vsource=Iactual*(Rdrive+Rcontact)，并保留功率后标compliance失败。',
      '电压模式电流观测四满量程10nA/10µA/10mA/100mA依手册，官网不同分组未混用；current_resolution_fraction仅宿主工程量化模型，非OEM精度/噪声。测量档溢出不限制实际电流，观测current_a为null并失败，不允许null进入反演。',
      '基础电阻范围10mΩ至10MΩ。电流源宿主顺从电压1至10V；达到顺从时实际电流按样品与两电流接触总阻抗降额，保留真实焦耳热并将测量标失败，不能假称目标电流已达到。',
      '默认10×10mm、厚0.5mm四角薄片；geometry.configure只改变过程几何，静态示例mesh不自动缩放。装样时质量=ρside²d，载入后禁止改几何；卸样带走全部样品质量/体积及显热。',
      '真实库提供实验数字化带符号RH(T)和rho(T)，不同温网格分别线性插值；有效域与原论文保守温区相交。未由Seebeck推Hall符号，无域外材料外推有效读数。',
      '密度、Cp、接触电阻、接触非对称、Hall几何因子、驱动几何因子和传感增益/偏置/τ/分辨率为宿主明确假设，不能当64种样品实测参数或OEM精度常数。',
      '薄片均匀、简单连通、四角小接触、标量方向代表是假设；不求解有限接触电流密度、张量各向异性、磁阻、非线性I-V或多载流子输运。vdP前向RA/RB满足指数恒等式但接触非对称不来自3D电流场求解。',
      '反演只使用外部观测I,V,B及时间，分别拟合电流奇项、I×B项、常偏置与线性漂移，再数值解vdP。两种B需方向相反且幅度满足显式容差，未宣称免反磁FastHall。',
      'nH=1/(e|RH|)、muH=|RH|/rho是Hall派生量；额外n=rH*nH、mu=muH/rH依赖宿主显式单载流子Hall因子。ZrNiSn等来源可能多带，不把这些派生值冒充真实总载流子浓度。',
      '每个外场操作为vdP-A、vdP-B和Hall接点各零/正/负激励（电流模式±I、电压模式±V），按实际phase_duration等待并求一阶电压传感响应；有限响应或温漂会产生可见拟合偏差，非瞬时真值直抄。',
      '外部磁场满足有限slew和一阶响应，set_target只写目标；切换场时拒绝正在采样的操作。M91断电不关闭独立外部场/温控，取消仅停止测量激励并冻结已有相，已有热量继续交换。',
      '样品与固定0.1J/K夹具热容共一个工程集中节点。闭合夹具由外部热控制器调温，开夹转为环境热交换；显式加热/冷却功率上限与热导未知必须输入，不借用主机35W供电当加热功率。',
      '源电能分为样品I²R热和接触热导出环境，样品热与外部控制器/环境/装卸显热闭合；未计外部磁体功耗、Peltier/Thomson或磁化能，热账仅此明确电阻性子模型。',
      'TiltView可手动倾斜，角范围/速度是工程代理；电源为100ms短按三角姿态不保持压下，低帧率可能看不到完整按键动作。显示覆盖为任意当前接点配置电压，并非每相都是Hall电压。',
      '所有Session公开result/poll即使evaluator转发也统一去除所选来源，privileged provenance.read单独返回当前样品溯源；操作结果保存采样时来源快照，Python/磁盘共享不是OS安全隔离。',
      '真实硬件校准、OEM精度/噪声、FastHall性能、机器人装样、完整电磁场/接触求解、外部温控与磁体硬件联调not_run。']
    for k,v in CAPS.items():
        un=k in UNSUPPORTED;async_=k=='acquisition.start';caps[k]={key:copy.deepcopy(v[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']}
        caps[k].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ('scene','robot','hardware') else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.lakeshore_measurereadym91.LakeShoreMeasurereadyM91Session.invoke',invocation=f'session.invoke(instance_id="m91_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),preconditions=[] if un or v['kind'] in ('query','analysis') else [dict(description_zh='运行入口原子检查明确外场/热条件、夹持、真实1800s预热、参考温域、量程和异步所有权。',state_ids=['power.enabled','warmup.ready','sample.present','clamp.position_m','field.configured','acquisition.phase'],enforced_by='runtime',check='LakeShoreMeasurereadyM91Runtime.invoke guards before commit')],state_effects=[dict(state_id=s,timing='immediate',description_zh='命令实际即时状态变化') for s in v['effects']],observes=[],model_effects=dict(mode='none',description_zh='只输出可映射状态，实际场景验证单独登记。',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='各接点配置的零/正/负激励相按真实停留时间采样，全部完成或测量范围错误终止' if async_ else '原子命令；预热/外场/电热/机械/采样仅宿主step推进',completion_state_ids=['acquisition.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','external_magnet','four_contact_fixture','programmable_source','voltmeter'],partial_failure_zh='取消/断电停止电流并冻结已完成测量相；外部场和独立温控、已积显热继续。范围错误保留不可用相后停止激励。'),errors=[dict(code=e,meaning_zh=e,side_effects='partial' if async_ and e in ('POWER_OFF','USER_CANCEL','SOURCE_COMPLIANCE','CURRENT_MEASUREMENT_OVERFLOW','VOLTAGE_OVERFLOW','REFERENCE_TEMPERATURE_RANGE','RESISTANCE_RANGE') else 'none') for e in ERRORS],verification=[],limitations=limits,source_ids=['manufacturer_primary','simulation_assumption','template_library'])
        if k in ('thermal.set_temperature','thermal.disable','sample.set_clamp','ui.set_tilt','field.set_target','acquisition.start','power.set'):
            caps[k]['state_effects'] += [dict(state_id=s,timing='on_step',description_zh='宿主时间推进外场、预热、电热、机械或实际测量相') for s in SLOTS if s not in v['effects'] and s not in ('plant.configured','geometry.configured','thermal.configured','field.configured','sample.present','power.enabled')]
    mechanism=dict(component_ids=['controller','contact_carriage','sample_sheet','thermal_port','field_reference'],state_ids=list(slots),meaning_zh='传统外部磁场与电流反转的四接点Hall及van der Pauw观测反演，真实预热、有限场响应与电热质量账',implementation='lumped',equations=[
      'Rs=rho(T)/d; RA=Rs*log(1+exp(u))/pi; RB=Rs*log(1+exp(-u))/pi. Therefore exp(-pi*RA/Rs)+exp(-pi*RB/Rs)=1. u is declared contact-asymmetry proxy.',
      'Current step=range*1e-5; Current mode:Iactual=sign(Iset)*min(abs(Iset),Vcompliance/(Rs*drive_geometry_factor+Rcontact)),1<=Vcompliance<=10V. Voltage mode:Irequested=Vset/(Rdrive+Rcontact),Iactual=sign(Irequested)*min(abs(Irequested),Ilimit),Vactual=Iactual*(Rdrive+Rcontact);Ilimit100nA..100mA with0.1%FS programming and explicitly assumed smallest-containing-FS selection. Voltage ranges limit maximum magnitude, not minimum resolvable measured signal.',
      'V_A=I*RA; V_B=I*RB; V_H=I*(RH(T)*B/d*hall_geometry_factor+misalignment*sqrt(RA*RB)). Gain/offset/linear drift feed explicit first-order detector dz/dt=(Vsignal-z)/tau.',
      'dB/dt=clip((Btarget-B)/tau,-slew,+slew), integrated analytically through transition. Field and thermal source are external boundaries and continue independently of M91 logical power.',
      'External inverse fits V=R*I+offset+drift*t for A/B and V=Rmisalign*I+(RH*g/d)*I*B+offset+drift*t for Hall. A bracketing root solves vdP sheet resistance using observed RA/RB only.',
      'nH=1/(e*abs(RH)),muH=abs(RH)/rho; assumed single-carrier n=rH*nH,mu=muH/rH. Hall sign labels Hall response only; multiband true carrier populations are not inferred.',
      'Psource=I*Vsource=I²*(Rdrive+Rcontact); Psample=I²*Rdrive; Pcontact_export=I²*Rcontact. Csample_total*dT/dt=Pcontroller+Psample-G*(T-Ta), Csample_total=0.1J/K+rho_mass*volume*Cp.',
      'Pcontroller=clip((K+G)*(Ttarget-Ta)-K*(T-Ta),-cooling_limit,+heating_limit) when clamped and external controller enabled. Open clamp uses ambient Gopen and no active controller. Incoming/outgoing sample sensible heat is ledgered.',
      'Idle linear thermal states use exact matrix exponential; active detector/temperature-dependent electrical model uses Radau integration with sampling-event boundaries; nanosecond residual carries fractional host timestep (e.g.60Hz). Warmup is1800 host seconds after last powerup.'],parameters={},source_ids=['simulation_assumption','template_library'],limitations=limits)
    spec=importlib.util.spec_from_file_location('_m91_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()});ann.annotate_export(SLUG,caps,slots,{'hall_van_der_pauw':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'geometry.configure':(GEOMETRY,GEOMETRY_EXAMPLE),'thermal.configure':(THERMAL,THERMAL_EXAMPLE),'field.configure':(FIELD,FIELD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'excitation.voltage.configure':(VOLTAGE,VOLTAGE_EXAMPLE)})
    for key,value in SLOTS.items():slots[key]['meaning_zh']=value['meaning']
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'hall_van_der_pauw':mechanism})

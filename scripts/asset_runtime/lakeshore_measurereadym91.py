"""CHEM141 M91: traditional current/field reversal, external magnet and vdP.

Only observed voltages/currents/fields enter inverse Hall/vdP fits. A host-controlled
external field boundary evolves in time; M91 does not create a magnetic field.
Front panel (1.1.0; panel_ui.json beside the contract, run by the shared engine _panel_ui.py): the power button (ON / SLEEP) and the TiltView
touchscreen with the measurement cards (display only: measurements start over SCPI) and the System settings (About, Connectivity, Ethernet IP
configuration, Display and sound, Legal, Update, Privacy, Date and time, Reset); the settings, the real-time clock and the SCPI front-panel lock
(SYST:KLOC) are kept by the runtime.
Measurement types (1.2.0; manual 3.7-3.8 and the CCHeck / RESistivity / FASThall / FWIRe commands): contact check (manual sweep, or optimizing
auto check that probes each pair and generates the setup), resistivity (vdP geometries A and B with current reversal, F value), FastHall (Hall
without field reversal: the two reciprocal diagonal configurations, Onsager R_ab,cd(B)=R_cd,ab(-B), cancel the misalignment voltage) and four
wire, all on the same scalar vdP sample, detector and energy model as the DC Hall acquisition, current excitation, blanking + sampling per phase.
1.3.0 (sweep 2026-10-19): evaluator fault.inject / fault.clear for the error messages of manual table 6-1 and 6.3.2 (Internal communication
interrupted: the output is disabled, running measurements fail and new ones are refused until the cause is cleared; Invalid calibration Default /
Uninitialized / Corrupt / Not Passed: reported on the front panel at power-up, operation continues with the default constants), the message is
confirmed with OK on the touchscreen (fault.acknowledge); with Virtual serial port off only the remote commands are refused, reads and poll stay
allowed.
"""
from __future__ import annotations
import copy,datetime,hashlib,importlib.util,json,math,os,re,tempfile,weakref
from pathlib import Path
import numpy as np
from scipy.integrate import solve_ivp
PROTOCOL_VERSION='1.0.0';ASSET_VERSION='1.3.0';SLUG='lakeshore_measurereadym91';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-141'
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
        if not isinstance(v,dict) or not set(s.get('required',s['properties']))<=set(v)<=set(s['properties']):raise ActionRejected('INVALID_ARGUMENT')   # optional arguments: panel.* only
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
# front panel (1.1.0): System settings of the touchscreen (manual 3.9.6, 4.4.2, 6.3.1) and the SCPI front-panel lock
LEVELS=[0,25,50,75,100];IPV4={'type':'string','pattern':r'^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$','maxLength':15}
INT=lambda lo,hi:{'type':'integer','minimum':lo,'maximum':hi,'x-unit':'1'};LEVEL={'type':'integer','enum':LEVELS,'x-unit':'%'}
SETTING_FIELDS={'connectivity':dict(scpi_tcp=BOOL,virtual_serial=BOOL),'ethernet':dict(ip_mode=enum(['dhcp','static']),ip_address=IPV4,gateway=IPV4,subnet=IPV4,dns=IPV4),
                'display':dict(brightness=LEVEL,volume=LEVEL),'update':dict(notify_updates=BOOL),'privacy':dict(usage_statistics=BOOL),
                'datetime':dict(auto_datetime=BOOL,timezone_min=dict(INT(-720,840),**{'x-unit':'min'}))}
SETTINGS_DEFAULT=dict(scpi_tcp=True,virtual_serial=True,ip_mode='dhcp',ip_address='192.168.0.128',gateway='192.168.0.1',subnet='255.255.255.0',dns='192.168.0.1',
                      brightness=75,volume=75,notify_updates=True,usage_statistics=False,auto_datetime=False,timezone_min=0)
ABOUT=dict(model='M91',product='FastHall',serial='M91-000001',firmware='1.0.0',calibration_date='2026-01-01 00:00',calibration_temperature_c=23.0)
CLOCK_EPOCH='2026-01-01T00:00:00'
SETTINGS=obj({k:x for f in SETTING_FIELDS.values() for k,x in f.items()})
cap('system.configure',obj({'section':enum(list(SETTING_FIELDS)),'values':{'anyOf':[obj(f) for f in SETTING_FIELDS.values()]}}),obj({'section':TEXT,'values':{'type':'object'}}),['display_glass','controller'],'System settings各页：Connectivity（SCPI over TCP、虚拟串口）、Ethernet（DHCP/Static与IP、网关、子网、DNS）、Display and sound（亮度Off/Darkest/Darker/Suggested/Brightest、音量Off–100%）、Update（有更新时通知）、Privacy（使用统计与崩溃报告）、Date and time（自动日期时间、时区）；仿真中无网络，只保存设定并显示',example={'section':'display','values':{'brightness':50,'volume':25}})
cap('system.set_clock',obj({'year':INT(2000,2099),'month':INT(1,12),'day':INT(1,31),'hour':INT(0,23),'minute':INT(0,59)}),obj({'datetime':TEXT}),['display_glass'],'Date and time页：设定实时时钟的日期与时间（SYST:DATE/SYST:TIME）',example={'year':2026,'month':3,'day':14,'hour':19,'minute':20})
cap('system.reset',obj({}),obj({'reset':BOOL}),['display_glass'],'Reset instrumentation settings：系统设定恢复出厂默认（校准常数保留，手册6.3.1）')
cap('system.lock',obj({'locked':BOOL}),obj({'locked':BOOL}),['controller'],'SYST:KLOC：远程锁定/解锁前面板触摸屏（锁定时触摸无效）',example={'locked':True})
# instrument errors of manual table 6-1 (Internal communication interrupted) and 6.3.2 (Invalid calibration: the NOVRAM check at power-up) that only
# an evaluator can cause (1.3.0); the two range-conflict messages of table 6-1 cannot occur in the simulated ranges (no 100 V range, compliance <= 10 V)
FAULTS={'internal_communication':'Internal communication interrupted','calibration_default':'Invalid calibration: Default',
        'calibration_uninitialized':'Invalid calibration: Uninitialized','calibration_corrupt':'Invalid calibration: Corrupt',
        'calibration_not_passed':'Invalid calibration: Not Passed'}
CAL_STATES=['valid','default','uninitialized','corrupt','not_passed']
cap('fault.inject',obj({'code':enum(list(FAULTS))}),obj({'injected':array(TEXT,0,5)}),['controller'],'评测者：注入手册表6-1/6.3.2的故障：internal_communication（主处理器与模拟板通信中断：输出自动关闭，运行中的测量失败，原因在时新测量被拒）；calibration_*（NOVRAM校准常数Default/Uninitialized/Corrupt/Not Passed：上电时前面板报告Invalid calibration，用默认常数继续运行但不保证指标）','simulation','evaluator',{'code':'internal_communication'})
cap('fault.clear',obj({'code':enum(list(FAULTS))}),obj({'injected':array(TEXT,0,5)}),['controller'],'评测者：消除故障原因（服务修理或重新校准，手册6.5）；前面板消息仍须OK确认','simulation','evaluator',{'code':'internal_communication'})
cap('fault.acknowledge',obj({}),obj({'acknowledged':nullable(TEXT)}),['display_glass'],'前面板故障消息的OK：关闭消息；原因仍在时内部通信故障继续关闭输出、拒绝新测量')
FAULT_CAPS={'fault.inject','fault.clear','fault.acknowledge'}
PANEL_DEVICE={'system.configure','system.set_clock','system.reset','system.lock'}
# measurement types (1.2.0): manual 3.7-3.8 and 4.5 CCHeck / RESistivity / FASThall / FWIRe; current excitation on the four corner contacts
MEAS_TYPES=['contact_check','resistivity','fasthall','four_wire'];MEAS_PHASES=['not_run','running','succeeded','failed','cancelled']
PAIRS=[(1,2),(2,3),(3,4),(4,1)];BLANKING_S=.002;SAMPLING_S=1/60.;MEAS_PHASE_NS=round((BLANKING_S+SAMPLING_S)*1e9)   # manual DEFault blanking 2 ms, sampling 1/60 s
CC_PROBE_A=1e-6;CC_TARGET_FRACTION=.01          # auto check: 1 uA probe per pair; excitation so that the largest pair voltage stays below 1 % of the max voltage
GEOMETRY_A=[(2,1,3,4),(3,2,4,1)];GEOMETRY_B=[(4,3,1,2),(1,4,2,3)];FASTHALL_CONFIGS=[(1,3,2,4),(2,4,3,1)]
def four_terminal(a,b,c,d):
    """V(c)-V(d) per current a->b on the square vdP sample (corners 1-2-3-4): ('two', sign) two-terminal (drive + contact resistance), ('a'|'b',
    sign) edge resistance RA (edges 1-2, 3-4) or RB (2-3, 4-1), ('hall', sign, h) sign*(h*H+M) on the diagonals (Onsager: swapping the current and
    voltage pairs reverses the Hall term); None for a three-terminal configuration."""
    if {c,d}=={a,b}:return ('two',1. if (c,d)==(a,b) else -1.)
    if {a,b}&{c,d}:return None
    if (b-a)%4 in (1,3):
        na=a%4+1 if a%4+1!=b else (a-2)%4+1;nb=b%4+1 if b%4+1!=a else (b-2)%4+1
        return ('a' if {a,b} in ({1,2},{3,4}) else 'b',1. if (c,d)==(na,nb) else -1.)
    if {a,b}=={1,3}:return ('hall',(1. if a==1 else -1.)*(1. if c==2 else -1.),1.)
    return ('hall',(1. if c==1 else -1.)*(1. if a==2 else -1.),-1.)
def offset_key(contacts):
    kind=four_terminal(*contacts)[0];a,b=contacts[:2]
    if kind=='two':kind='hall' if (b-a)%4==2 else 'a' if {a,b} in ({1,2},{3,4}) else 'b'
    return {'a':'voltage_offset_a_v','b':'voltage_offset_b_v','hall':'voltage_offset_hall_v'}[kind]
def vdp_sheet(ra,rb):return brentq(lambda rs:math.exp(-math.pi*ra/rs)+math.exp(-math.pi*rb/rs)-1,min(ra,rb)*1e-6,math.pi*(ra+rb)*100,xtol=1e-13,rtol=1e-13)
def stats(values):
    x=np.array(values,dtype=float);n=len(x);mean=float(np.mean(x));se=float(np.std(x,ddof=1)/math.sqrt(n)) if n>=2 else None
    return mean,se,(abs(mean)/se if se else None)
CRANGE={'type':'number','enum':CURRENT_RANGES,'x-unit':'A'};VRANGE={'type':'number','enum':VOLTAGE_RANGES,'x-unit':'V'};COMPLIANCE=number('V',1,10)
SAMPLES=INT(1,1000);SNR=number('1',0,1000);THICK=number('m',0,.01);NUM=nullable(number());CONTACT_POINTS=array(INT(1,6),4,4)
SETUP=obj(dict(excitation_type=enum(['current']),excitation_start_a=NUM,excitation_end_a=NUM,excitation_range_a=NUM,voltage_range_v=NUM,compliance_v=NUM,
               points=nullable(INT(1,1000)),minimum_r_squared=NUM,minimum_snr=NUM,field_t=NUM,resistivity=NUM,thickness_m=NUM,contacts=nullable(CONTACT_POINTS),
               reversal=nullable(BOOL),max_current_a=NUM,max_voltage_v=NUM,linked=BOOL,optimized=BOOL,blanking_s=number('s',0),sampling_s=number('s',0)))
MROW=obj(dict(time_s=number('s',0),host_time_s=number('s',0),configuration=TEXT,excitation_a=number('A'),current_a=NUM,voltage_v=NUM,field_t=NUM,temperature_k=NUM,in_compliance=BOOL,error_code=nullable(TEXT)))
CC_PAIR=obj(dict(pair=array(INT(1,4),2,2),slope_ohm=NUM,offset_v=NUM,r_squared=NUM,r_squared_pass=BOOL,in_compliance=BOOL,voltage_overload=BOOL,current_overload=BOOL))
GEOM=obj(dict(sheet_resistance_ohm=number('ohm',0),f_value=number('1',0,1)))
CC_SUMMARY=obj(dict(pairs=array(CC_PAIR,4,4),success=BOOL,warnings=array(TEXT)))
RES_SUMMARY=obj(dict(samples=INT(1,1000),sheet_resistance_ohm=number('ohm',0),sheet_standard_error_ohm=NUM,resistivity_ohm_m=NUM,resistivity_standard_error_ohm_m=NUM,snr=NUM,
                     geometry_a=GEOM,geometry_b=GEOM,field_t=NUM,temperature_k=NUM,warnings=array(TEXT)))
FH_SUMMARY=obj(dict(samples=INT(1,1000),hall_voltage_v=number('V'),hall_voltage_standard_error_v=NUM,hall_voltage_snr=NUM,carrier_type=enum(['p','n']),p_type_count=INT(0,1000),n_type_count=INT(0,1000),
                    sheet_hall_coefficient_m2_c=number('m^2/C'),sheet_hall_coefficient_standard_error_m2_c=NUM,hall_coefficient_m3_c=NUM,hall_coefficient_standard_error_m3_c=NUM,
                    sheet_carrier_concentration_m2=number('1/m^2',0),sheet_carrier_concentration_standard_error_m2=NUM,carrier_concentration_m3=NUM,carrier_concentration_standard_error_m3=NUM,
                    mobility_m2_v_s=NUM,mobility_standard_error_m2_v_s=NUM,warnings=array(TEXT)))
FW_SUMMARY=obj(dict(samples=INT(1,1000),resistance_ohm=number('ohm'),resistance_standard_error_ohm=NUM,resistance_snr=NUM,current_a=number('A'),current_standard_error_a=NUM,voltage_v=number('V'),
                    voltage_standard_error_v=NUM,warnings=array(TEXT)))
MRESULT=obj(dict(type=enum(MEAS_TYPES),operation_id=nullable(TEXT),phase=enum(MEAS_PHASES),started=nullable(TEXT),elapsed_s=number('s',0),setup=nullable(SETUP),
                 summary={'anyOf':[CC_SUMMARY,RES_SUMMARY,FH_SUMMARY,FW_SUMMARY,{'type':'null'}]},rows=array(MROW,0,8000),error_code=nullable(TEXT)))
START_OUT=obj({'operation_id':TEXT,'expected_duration_s':number('s',0)});MC=['controller','contact_carriage']
CC_MANUAL=dict(excitation_start_a=1e-5,excitation_end_a=-1e-5,excitation_range_a=1e-5,voltage_range_v=.1,compliance_v=10.,points=11,minimum_r_squared=.9999)
cap('contact_check.auto',obj(dict(max_current_a=number('A',1e-6,.1),max_voltage_v=number('V',1,10),points=INT(2,100),minimum_r_squared=number('1',0,1))),START_OUT,MC,'CCHeck:STARt（自动优化）：每对接点先以1µA探测两端电阻，再按最大电压1%（假设）选电流量程与测量量程，正负对称扫描四对接点1-2、2-3、3-4、4-1并线性拟合R²；结果即Optimization卡片',example=dict(max_current_a=.1,max_voltage_v=10.,points=11,minimum_r_squared=.9999))
cap('contact_check.manual',obj(dict(excitation_start_a=number('A',-.1,.1),excitation_end_a=number('A',-.1,.1),excitation_range_a=CRANGE,voltage_range_v=VRANGE,compliance_v=COMPLIANCE,points=INT(2,100),minimum_r_squared=number('1',0,1))),START_OUT,MC,'CCHeck:STARt:MANual（电流激励）：四对接点各从起点到终点等距扫描points点，拟合斜率、截距与R²，报告顺从与过载',example=CC_MANUAL)
RES_MANUAL=dict(excitation_a=1e-4,excitation_range_a=1e-4,voltage_range_v=.1,compliance_v=1.5,samples=4,thickness_m=0.,minimum_snr=0.)
cap('resistivity.start',obj(dict(excitation_a=number('A',1e-9,.1),excitation_range_a=CRANGE,voltage_range_v=VRANGE,compliance_v=COMPLIANCE,samples=SAMPLES,thickness_m=THICK,minimum_snr=SNR)),START_OUT,MC,'RESistivity:STARt（vdP，电流激励）：每个样本测几何A（R21,34、R32,41）与B（R43,12、R14,23）并电流反转，数值解vdP得方块电阻、F值；厚度>0给体电阻率；minimum_snr>0时达到即停（0=INF）',example=RES_MANUAL)
cap('resistivity.start_link',obj(dict(voltage_range_v=VRANGE,thickness_m=THICK,samples=SAMPLES,minimum_snr=SNR)),START_OUT,MC,'RESistivity:STARt:LINk：沿用最近一次完成的接触检查的激励（起止绝对值较大者）、量程与顺从',example=dict(voltage_range_v=.1,thickness_m=0.,samples=10,minimum_snr=0.))
FH_MANUAL=dict(excitation_a=1e-3,excitation_range_a=1e-3,voltage_range_v=.01,compliance_v=10.,field_t=.5,samples=4,resistivity=0.,thickness_m=0.,minimum_snr=0.)
cap('fasthall.start',obj(dict(excitation_a=number('A',1e-9,.1),excitation_range_a=CRANGE,voltage_range_v=VRANGE,compliance_v=COMPLIANCE,field_t=number('T',-100,100),samples=SAMPLES,resistivity=number('ohm*m',0,1e9),thickness_m=THICK,minimum_snr=SNR)),START_OUT,MC,'FASThall:STARt：不反转外场，在当前场下测两组互易对角配置（1→3/2-4与2→4/3-1）并电流反转，互易消去失配电压得Hall电压；用户输入场值计算Hall系数、载流子浓度、类型；给resistivity（体Ω·m或厚度0时方块Ω/□，0=未给）才算迁移率',example=FH_MANUAL)
cap('fasthall.start_link',obj(dict(field_t=number('T',-100,100),voltage_range_v=VRANGE,samples=SAMPLES,minimum_snr=SNR)),START_OUT,MC,'FASThall:STARt:LINk：沿用最近完成的接触检查的激励与顺从，及最近完成的电阻率测量的方块电阻与厚度（无电阻率测量则不算迁移率）',example=dict(field_t=.5,voltage_range_v=.01,samples=10,minimum_snr=0.))
FW_EXAMPLE=dict(contacts=[1,2,4,3],excitation_a=1e-3,excitation_range_a=1e-3,voltage_range_v=.001,compliance_v=1.5,samples=4,reversal=True,minimum_snr=0.)
cap('four_wire.start',obj(dict(contacts=CONTACT_POINTS,excitation_a=number('A',1e-9,.1),excitation_range_a=CRANGE,voltage_range_v=VRANGE,compliance_v=COMPLIANCE,samples=SAMPLES,reversal=BOOL,minimum_snr=SNR)),START_OUT,MC,'FWIRe:STARt：激励接点1→2、测量接点3-4（vdP样品只有接点1–4；5、6为Hall bar接点），四端（边或对角）或同一对两端；可选电流反转；报告电阻、电压、电流平均与标准误差',example=FW_EXAMPLE)
cap('measurement.result',obj({'type':enum(MEAS_TYPES)}),MRESULT,['controller'],'CCHeck/RESistivity/FASThall/FWIRe:RESult：某测量类型最近一次的设定、汇总结果与原始读数（未运行为not_run）','query',example={'type':'resistivity'})
cap('measurement.reset',obj({'type':enum(MEAS_TYPES)}),obj({'type':TEXT,'reset':BOOL}),['controller'],'CCHeck/RESistivity/FASThall/FWIRe:RESet：取消该类型正在运行的测量并清除其数据（回到Not run）',example={'type':'contact_check'})
MEAS_STARTS={'contact_check.auto':'contact_check','contact_check.manual':'contact_check','resistivity.start':'resistivity','resistivity.start_link':'resistivity','fasthall.start':'fasthall','fasthall.start_link':'fasthall','four_wire.start':'four_wire'}
ASYNC_STARTS={'acquisition.start'}|set(MEAS_STARTS)
SI_PREFIX={-15:'f',-12:'p',-9:'n',-6:'µ',-3:'m',0:'',3:'k',6:'M',9:'G'}
def si(v,unit,digits=6):
    """A value with an SI prefix and six significant digits, as the M91 result screens show it (e.g. 163.645 mΩ/□, 10.0000 mA)."""
    if v is None:return '--'
    if v==0:return f'0 {unit}'
    e=max(-15,min(9,3*math.floor(math.log10(abs(v))/3)));m=v/10**e;d=max(0,digits-1-math.floor(math.log10(abs(m))+1e-12))
    return f'{m:.{d}f} {SI_PREFIX[e]}{unit}'
def sci(v,unit):return '--' if v is None else f'{v:.5e} {unit}'
TITLES={'contact_check':'Van Der Pauw contact check results','resistivity':'Resistivity results','fasthall':'FastHall results','four_wire':'Four wire measurement results'}
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
slot('system.settings',SETTINGS,'1','System settings（连接、IP、亮度/音量、更新通知、隐私、自动日期与时区）','setpoint',component='display_glass')
slot('system.datetime',TEXT,'1','实时时钟（YYYY-MM-DD HH:MM，含时区）',component='display_glass')
slot('system.locked',BOOL,'1','前面板触摸屏被SYST:KLOC锁定','setpoint',component='display_glass')
slot('measurement.type',enum(['idle']+MEAS_TYPES),'1','正在运行的测量类型（接触检查、电阻率、FastHall、四线；DC Hall见acquisition.*）')
slot('measurement.status',obj({t:enum(MEAS_PHASES) for t in MEAS_TYPES}),'1','各测量类型已存数据的状态（Not run、运行、完成、失败、取消）')
slot('measurement.progress',nullable(number('1',0,1)),'1','正在运行的测量完成的相数比例')
slot('fault.injected',array(TEXT,0,5),'1','评测者注入且尚未消除的故障原因（fault.inject）','setpoint','evaluator')
slot('fault.message',nullable(TEXT),'1','前面板显示、待OK确认的故障消息（Internal communication interrupted / Invalid calibration: …）；无为null',component='display_glass')
slot('calibration.state',enum(CAL_STATES),'1','上电NOVRAM检查得到的校准常数状态（valid或Default/Uninitialized/Corrupt/Not Passed：用默认常数，仍可运行，不保证指标；手册6.3.2）')
for k,u in [('truth.sample_k','K'),('truth.field_t','T'),('truth.sample_mass_kg','kg')]:slot(k,nullable(number(u)),u,'宿主真实样品/外部场状态，非代理可见真值','process_truth','evaluator')

def _panel_engine():
    try:
        from . import _panel_ui as engine                                   # imported as asset_runtime.lakeshore_measurereadym91 (tests, Matterix bridge)
        return engine
    except ImportError:                                                      # loaded from its file path
        import sys
        if '_chemsim_panel_ui' not in sys.modules:
            s=importlib.util.spec_from_file_location('_chemsim_panel_ui',Path(__file__).with_name('_panel_ui.py'));sys.modules['_chemsim_panel_ui']=importlib.util.module_from_spec(s);s.loader.exec_module(sys.modules['_chemsim_panel_ui'])
        return sys.modules['_chemsim_panel_ui']
_panel_ui=_panel_engine()
if os.environ.get('M91_PANEL_BUILD'):PANEL_SPEC,_PANEL_DEFS=None,{}          # spec builder: runtime constants without the spec being written
else:PANEL_SPEC=_panel_ui.load_spec(ASSET/'panel_ui.json',capabilities=set(CAPS));_PANEL_DEFS=_panel_ui.capability_definitions(PANEL_SPEC)
PANEL_CAPS={k:dict(input_schema=d['input_schema'],output_schema=d['output_schema'],component_ids=list(d['component_ids']),function_zh=d['function_zh'],kind=d['kind'],visibility=d['visibility'],example_arguments=d['example_arguments'],effects=[]) for k,d in _PANEL_DEFS.items()}   # kept out of CAPS
ALL_CAPS=dict(CAPS,**PANEL_CAPS);_PANELS=weakref.WeakKeyDictionary()
class LakeShoreMeasurereadyM91Runtime:
    def __init__(self,instance_id='m91_01'):
        self.instance_id=instance_id;self.plant=None;self.geometry=None;self.thermal=None;self.field=None;self.sample=None;self.record=None;self.power=False;self._power_started_ns=None;self._button_started_ns=-1000000000;self.thermal_enabled=False;self.target=300.;self.field_actual=0.;self.field_target=0.;self.axes={'clamp':dict(position=.012,target=.012,start=.012,start_ns=0,arrival_ns=0),'ui':dict(position=0.,target=0.,start=0.,start_ns=0,arrival_ns=0)};self.x=np.zeros(7);self.time_ns=0;self._clock_remainder_ns=0.;self.revision=0;self.method=copy.deepcopy(METHOD_EXAMPLE);self.programmed_current=METHOD_EXAMPLE['current_a'];self.calibration=copy.deepcopy(CALIBRATION_EXAMPLE);self.high_resistance_firmware_enabled=False;self.excitation_mode='current';self.voltage=copy.deepcopy(VOLTAGE_EXAMPLE);self.programmed_voltage=VOLTAGE_EXAMPLE['voltage_v'];self.current_limit=VOLTAGE_EXAMPLE['current_limit_a'];self.phase='idle';self.elapsed=0.;self.rows=[];self.error=None;self.operation_id=None;self._counter=0;self._start_ns=0;self._phase_index=0;self.expected_duration=0.;self.records={};self._principals={};self._requests={};self._owners={};self._cancel_operations={};self._source=None;self._reference_id=None;self._evidence='no_sample';self._operation_source=None;self._operation_evidence='no_measurement';self.mass_loaded=self.mass_removed=self.volume_loaded=self.volume_removed=self.loaded_sensible=self.removed_sensible=0.
        self.settings=copy.deepcopy(SETTINGS_DEFAULT);self.clock_offset_s=0.;self.locked=False;self.panel_capture_dir=None
        self.fault_causes=[];self.fault_message=None;self.cal_state='valid'   # evaluator faults (1.3.0)
        self.mop=None;self.mdata={t:None for t in MEAS_TYPES};self.mrecords={};self._mcounter=0
        self._caller=None   # principal of the session call in progress (set by the session; panel gestures act as that caller)
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
    def _mstep(self):return self.mop['steps'][self.mop['index']] if self.mop and self.mop['phase']=='running' else None
    def _active(self):return self.phase=='running' or self._mstep() is not None
    def _polarity(self):return ['zero','positive','negative'][self._phase_index%3] if self.phase=='running' else 'zero'
    def _button_position(self):
        t=(self.time_ns-self._button_started_ns)/1e9
        return .0015*(1-abs(t-.05)/.05) if 0<=t<=.1 else 0.
    def _field_at(self,t):return external_field_at(self.field_actual,self.field_target,t,self.field['slew_t_s'],self.field['response_tau_s']) if self.field else self.field_actual
    def _field_observed(self,B=None):
        if not self.field:return None
        B=self.field_actual if B is None else B;f=self.field;c=self.calibration;v=(f['probe_gain']*B+f['probe_offset_t']-c['field_offset_t'])*c['field_gain_correction'];return float(round(v/f['probe_resolution_t'])*f['probe_resolution_t'])
    def _electrical(self,T=None,B=None):
        if not self.sample or not self.power or not self._active() or not self._closed():return dict(current=0.,requested=0.,voltage=0.,source_voltage=0.,sample_power=0.,contact_power=0.,valid=True,compliance=False,resistance=0.)
        T=self._temperature() if T is None else T;B=self.field_actual if B is None else B;v=transport(self.record,T,clamp=True);sheet=v['resistivity_ohm_m']/self.geometry['thickness_m'];ra,rb=vdp_resistances(sheet,self.plant['contact_asymmetry']);bulk=sheet*self.plant['drive_geometry_factor'];contact=self.plant['current_contact_resistance_ohm'];sign={'zero':0.,'positive':1.,'negative':-1.}[self._polarity()]
        if self.excitation_mode=='voltage':
            requested=self.programmed_voltage*sign/(bulk+contact);I=math.copysign(min(abs(requested),self.current_limit),requested)
        else:
            requested=self.programmed_current*sign;I=math.copysign(min(abs(requested),self.plant['compliance_voltage_v']/(bulk+contact)),requested)
        step=self._mstep()
        if step:                                                             # measurement types: the step's contacts and signed excitation current
            requested=step['current'];I=math.copysign(min(abs(requested),self.mop['setup']['compliance_v']/(bulk+contact)),requested);kind=four_terminal(*step['contacts'])
            H=v['hall_coefficient_m3_c']*B/self.geometry['thickness_m']*self.plant['hall_geometry_factor'];M=self.plant['hall_misalignment_fraction']*math.sqrt(ra*rb)
            R=float(kind[1]*(bulk+contact if kind[0]=='two' else ra if kind[0]=='a' else rb if kind[0]=='b' else kind[2]*H+M))
            return dict(current=I,requested=requested,voltage=I*R,source_voltage=I*(bulk+contact),sample_power=I*I*bulk,contact_power=I*I*contact,valid=v['valid'],compliance=abs(I-requested)>max(abs(requested)*1e-10,1e-18),resistance=R)
        configuration=self._contact();R=float(ra if configuration=='vdp_a' else rb if configuration=='vdp_b' else v['hall_coefficient_m3_c']*B/self.geometry['thickness_m']*self.plant['hall_geometry_factor']+self.plant['hall_misalignment_fraction']*math.sqrt(ra*rb));return dict(current=I,requested=requested,voltage=I*R,source_voltage=I*(bulk+contact),sample_power=I*I*bulk,contact_power=I*I*contact,valid=v['valid'],compliance=abs(I-requested)>max(abs(requested)*1e-10,1e-18),resistance=R)
    def _observed_current(self,e=None):
        if not self.power or not self._active():return None
        e=self._electrical() if e is None else e
        if self.excitation_mode=='current' or self._mstep():return e['current']
        full=self.voltage['current_measurement_range_a']
        if abs(e['current'])>full:return None
        q=full*self.voltage['current_resolution_fraction'];return float(round(e['current']/q)*q)
    def _observed_voltage(self):
        if not self.power or not self._active():return None
        c=self.calibration;voltage=(self.x[1]-c['voltage_offset_v'])*c['voltage_gain_correction'];full=self.mop['setup']['voltage_range_v'] if self._mstep() else self.method['voltage_range_v']
        if abs(voltage)>full:return None
        q=full*self.plant['voltage_resolution_fraction'];return float(round(voltage/q)*q)
    def read(self,role='evaluator'):
        if role not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
        active=self.power and self._active();e=self._electrical();d={'plant.configured':self.plant is not None,'geometry.configured':self.geometry is not None,'thermal.configured':self.thermal is not None,'field.configured':self.field is not None,'sample.present':self.sample is not None,'power.enabled':self.power,'warmup.ready':self._warmup()>=WARMUP_S,'thermal.enabled':self.thermal_enabled,'clamp.moving':self._moving('clamp'),'clamp.position_m':self.axes['clamp']['position'],'clamp.target_m':self.axes['clamp']['target'],'ui.tilt_rad':self.axes['ui']['position'],'ui.target_tilt_rad':self.axes['ui']['target'],'power.position_m':self._button_position(),'warmup.elapsed_s':self._warmup(),'thermal.target_k':self.target,'thermal.indicated_k':float(round(self._temperature()/self.plant['temperature_resolution_k'])*self.plant['temperature_resolution_k']) if self.plant and self.thermal else None,'field.target_t':self.field_target,'field.indicated_t':self._field_observed(),'excitation.programmed_current_a':self.programmed_current,'measured.current_a':self._observed_current(e),'options.high_resistance_firmware_enabled':self.high_resistance_firmware_enabled,'excitation.mode':self.excitation_mode,'excitation.programmed_voltage_v':self.programmed_voltage if self.excitation_mode=='voltage' else 0.,'excitation.current_measurement_range_a':self.voltage['current_measurement_range_a'] if self.excitation_mode=='voltage' else None,'excitation.source_voltage_v':e['source_voltage'] if active else None,'measured.voltage_v':self._observed_voltage(),'acquisition.contact_configuration':self._contact(),'acquisition.polarity':self._polarity(),'clock.time_s':self.time_s,'acquisition.phase':self.phase,'acquisition.elapsed_s':self.elapsed,'acquisition.points':len(self.rows),'truth.sample_k':self._temperature(),'truth.field_t':self.field_actual if self.field else None,'truth.sample_mass_kg':self._mass() if self.sample else None,'system.settings':copy.deepcopy(self.settings),'system.datetime':self._date_text(),'system.locked':self.locked,
           'measurement.type':self.mop['type'] if self._mstep() else 'idle','measurement.status':{t:self.mdata[t]['phase'] if self.mdata[t] else 'not_run' for t in MEAS_TYPES},
           'measurement.progress':self.mop['index']/len(self.mop['steps']) if self._mstep() else None,
           'fault.injected':list(self.fault_causes),'fault.message':self.fault_message,'calibration.state':self.cal_state}
        return {k:v for k,v in d.items() if role!='agent' or SLOTS[k]['visibility']=='agent'}
    def _propagate(self,ns):
        if not ns:return
        dt=ns/1e9;field_end=self._field_at(dt)
        if not self.plant or not self.thermal:self.field_actual=field_end;return
        p=self.plant;th=self.thermal;C=self._capacity();G=th['closed_conductance_w_k'] if self._closed() else th['open_conductance_w_k'];K=th['controller_gain_w_k'] if self.thermal_enabled and self._closed() else 0.;target=self.target-p['ambient_k'];active=self.power and self._active()
        def thermal_control(delta):return min(th['heating_limit_w'],max(-th['cooling_limit_w'],(K+G)*target-K*delta)) if K else 0.
        if active:
            step=self._mstep();offset=p[step['offset'] if step else {'vdp_a':'voltage_offset_a_v','vdp_b':'voltage_offset_b_v','hall':'voltage_offset_hall_v'}[self._contact()]];start_time=self.time_s
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
    def _agent_caller(self):return self._caller is not None and self._principals.get(self._caller)=='agent'
    def _foreign(self,operation_id):return self._agent_caller() and self._owners.get(operation_id) not in (None,self._caller)
    def _guard(self,capability,a):
        """Ownership rules of an agent caller, the same for a direct call and for a panel gesture (both run with the session caller in _caller):
        the legacy acquisition is stopped / read only by its owner; the power button does not switch off during another agent's acquisition or
        measurement-type run; measurement.reset does not cancel or delete another agent's measurement."""
        if not self._agent_caller():return
        if capability in ('acquisition.stop','acquisition.result') and self.operation_id and self._owners.get(self.operation_id)!=self._caller:raise ActionRejected('ACCESS_DENIED')
        if capability=='power.set' and not a['enabled'] and ((self.phase=='running' and self._owners.get(self.operation_id)!=self._caller) or (self._mstep() and self._foreign(self.mop['id']))):raise ActionRejected('ACCESS_DENIED')
        if capability=='measurement.reset' and self.mdata[a['type']] and self._foreign(self.mdata[a['type']]['id']):raise ActionRejected('ACCESS_DENIED')
    def invoke(self,capability,**a):
        if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability]['input_schema'],a)
        if capability in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
        self._guard(capability,a)
        if capability=='analysis.hall_vdp':return infer_hall_vdp(**a)
        if capability=='acquisition.result':return self._result()
        if capability=='provenance.read':return dict(reference_id=self._reference_id,template_source=self._source,evidence=self._evidence)
        if capability=='ledger.read':return self._ledger()
        if capability=='sim.step':self.step(a['dt_s']);return {'time_s':self.time_s}
        if capability=='measurement.result':return self._mresult(a['type'])
        if capability=='measurement.reset':out=self._mreset(a['type']);self.revision+=1;return copy.deepcopy(out)
        if capability in PANEL_DEVICE:out=getattr(self,'_cmd_'+capability.replace('.','_'))(**a);self.revision+=1;return copy.deepcopy(out)
        if capability in FAULT_CAPS:out=self._fault(capability,a);self.revision+=1;return copy.deepcopy(out)
        if self._active() and capability not in ('acquisition.stop','power.set'):raise ActionRejected('BUSY')
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
            if not a['enabled'] and self._mstep():self._mfinish('cancelled','POWER_OFF')
            if a['enabled']!=self.power:self._button_started_ns=self.time_ns;self._power_started_ns=self.time_ns if a['enabled'] else None
            if a['enabled'] and not self.power:self.fault_message=self._fault_text() or self.fault_message   # an error still present is reported again
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
            if 'internal_communication' in self.fault_causes:raise ActionRejected('INTERNAL_COMMUNICATION')
            if self._warmup()<WARMUP_S:raise ActionRejected('WARMUP_REQUIRED')
            if (self.programmed_current if self.excitation_mode=='current' else self.programmed_voltage)<=0:raise ActionRejected('ZERO_EXCITATION')
            if self.excitation_mode=='voltage' and not self.high_resistance_firmware_enabled:raise ActionRejected('HIGH_RESISTANCE_FIRMWARE_REQUIRED')
            if abs(self.field_target-self.field_actual)>self.method['field_settling_tolerance_t']:raise ActionRejected('FIELD_NOT_SETTLED')
            if transport(self.record,self._temperature()) is None:raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
            self._counter+=1;self.operation_id=f'{self.instance_id}:acquisition:{self._counter}';self._operation_source=self._source;self._operation_evidence=self._evidence;self.phase='running';self.elapsed=0.;self.rows=[];self.error=None;self._start_ns=self.time_ns;self._phase_index=0;self.expected_duration=9*self.method['repeats']*round(self.method['phase_duration_s']*1e9)/1e9;out={'operation_id':self.operation_id,'expected_duration_s':self.expected_duration}
        elif capability in MEAS_STARTS:out=self._mstart(capability,a)
        elif capability=='acquisition.stop':
            if self.phase!='running':raise ActionRejected('NOT_RUNNING')
            self._finish('cancelled','USER_CANCEL');out={'phase':self.phase,'elapsed_s':self.elapsed}
        else:raise ActionRejected('UNKNOWN_CAPABILITY')
        self.revision+=1;return copy.deepcopy(out)
    # ---- measurement types (1.2.0): contact check, resistivity, FastHall, four wire
    def _mprecheck(self):
        if not self.power or not self.plant or not self.geometry or not self.thermal or not self.field or not self.sample or not self._closed() or self._moving('clamp'):raise ActionRejected('PRECONDITION_FAILED')
        if 'internal_communication' in self.fault_causes:raise ActionRejected('INTERNAL_COMMUNICATION')
        if self._warmup()<WARMUP_S:raise ActionRejected('WARMUP_REQUIRED')
        if transport(self.record,self._temperature()) is None:raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
    def _quantize(self,current,full):
        if abs(current)>full*(1+1e-12):raise ActionRejected('CURRENT_OR_TIME_RANGE')
        q=full*1e-5;return float(round(current/q)*q)
    def _linked(self,kind):
        op=self.mdata[kind]
        if not op or op['phase']!='succeeded':raise ActionRejected('CONTACT_CHECK_REQUIRED' if kind=='contact_check' else 'RESISTIVITY_REQUIRED')
        return op
    def _mstart(self,capability,a):
        self._mprecheck();kind=MEAS_STARTS[capability];steps=[]
        setup=dict(excitation_type='current',excitation_start_a=None,excitation_end_a=None,excitation_range_a=None,voltage_range_v=None,compliance_v=None,points=None,minimum_r_squared=None,
                   minimum_snr=None,field_t=None,resistivity=None,thickness_m=None,contacts=None,reversal=None,max_current_a=None,max_voltage_v=None,linked=capability.endswith('_link'),
                   optimized=capability=='contact_check.auto',blanking_s=BLANKING_S,sampling_s=SAMPLING_S)
        if capability=='contact_check.auto':
            setup.update(max_current_a=a['max_current_a'],max_voltage_v=a['max_voltage_v'],points=a['points'],minimum_r_squared=a['minimum_r_squared'],compliance_v=a['max_voltage_v'],voltage_range_v=10.,excitation_range_a=CURRENT_RANGES[0])
            steps=[dict(contacts=(p,q,p,q),current=CC_PROBE_A,probe=True) for p,q in PAIRS]
        elif capability=='contact_check.manual':
            if a['excitation_start_a']==a['excitation_end_a']:raise ActionRejected('INVALID_ARGUMENT')
            start=self._quantize(a['excitation_start_a'],a['excitation_range_a']);end=self._quantize(a['excitation_end_a'],a['excitation_range_a'])
            setup.update(excitation_start_a=start,excitation_end_a=end,excitation_range_a=a['excitation_range_a'],voltage_range_v=a['voltage_range_v'],compliance_v=a['compliance_v'],points=a['points'],minimum_r_squared=a['minimum_r_squared'])
            steps=self._sweep(setup)
        else:
            if kind=='four_wire':
                c=a['contacts']
                if c[0]==c[1] or c[2]==c[3]:raise ActionRejected('INVALID_ARGUMENT')
                if max(c)>4:raise ActionRejected('CONTACT_NOT_PRESENT')
                if four_terminal(*c) is None:raise ActionRejected('CONTACT_CONFIGURATION')
            if capability.endswith('_link'):
                cc=self._linked('contact_check')['setup'];current=self._quantize(max(abs(cc['excitation_start_a']),abs(cc['excitation_end_a'])),cc['excitation_range_a']);full=cc['excitation_range_a'];compliance=cc['compliance_v']
            else:
                full=a['excitation_range_a'];current=self._quantize(a['excitation_a'],full);compliance=a['compliance_v']
            if current<=0:raise ActionRejected('ZERO_EXCITATION')
            setup.update(excitation_start_a=current,excitation_end_a=current,excitation_range_a=full,voltage_range_v=a['voltage_range_v'],compliance_v=compliance,points=a['samples'],minimum_snr=a['minimum_snr'] or None)
            if kind=='resistivity':setup.update(thickness_m=a['thickness_m']);configs=[(cfg,s) for cfg in GEOMETRY_A+GEOMETRY_B for s in (1.,-1.)]
            elif kind=='fasthall':
                if a['field_t']==0:raise ActionRejected('INVALID_ARGUMENT')
                if capability=='fasthall.start':setup.update(field_t=a['field_t'],resistivity=a['resistivity'] or None,thickness_m=a['thickness_m'])
                else:
                    res=self.mdata['resistivity'];done=res is not None and res['phase']=='succeeded';d=res['setup']['thickness_m'] if done else 0.
                    setup.update(field_t=a['field_t'],thickness_m=d,resistivity=(res['summary']['resistivity_ohm_m'] if d else res['summary']['sheet_resistance_ohm']) if done else None)
                configs=[(cfg,s) for cfg in FASTHALL_CONFIGS for s in (1.,-1.)]
            else:setup.update(contacts=list(a['contacts']),reversal=a['reversal']);configs=[(tuple(a['contacts']),s) for s in ((1.,-1.) if a['reversal'] else (1.,))]
            steps=[dict(contacts=cfg,current=s*current,sample=k) for k in range(a['samples']) for cfg,s in configs]
        for s in steps:s['offset']=offset_key(s['contacts'])
        if self.mop and self.mop['phase']=='running':raise ActionRejected('BUSY')
        self._mcounter+=1;op=dict(type=kind,id=f'{self.instance_id}:{kind}:{self._mcounter}',phase='running',started=self._date_text(),start_ns=self.time_ns,elapsed=0.,index=0,steps=steps,
                                setup=setup,rows=[],probe=[],summary=None,error=None,per_sample=len(steps)//max(1,setup['points']) if kind!='contact_check' else None)
        self.mop=op;self.mdata[kind]=op;self.mrecords[op['id']]=None
        n=len(steps)+(len(PAIRS)*setup['points'] if setup['optimized'] else 0)                 # auto check: the probes, then the generated sweeps
        return {'operation_id':op['id'],'expected_duration_s':n*MEAS_PHASE_NS/1e9}
    def _sweep(self,setup):
        start,end,n=setup['excitation_start_a'],setup['excitation_end_a'],setup['points'];q=setup['excitation_range_a']*1e-5
        return [dict(contacts=(p,r,p,r),current=float(round((start+(end-start)*k/(n-1))/q)*q),pair=i) for i,(p,r) in enumerate(PAIRS) for k in range(n)]
    def _cc_generate(self):
        """Auto contact check: the setup from the probe readings (observed V/I per pair; a pair in compliance or overload counts as at the limit)."""
        op=self.mop;s=op['setup'];limit=s['max_voltage_v']/CC_PROBE_A
        r2=[limit if row['voltage_v'] is None or row['in_compliance'] or not row['current_a'] else abs(row['voltage_v']/row['current_a']) for row in op['probe']];rmax=max(max(r2),1e-3)
        target=min(s['max_current_a'],CC_TARGET_FRACTION*s['max_voltage_v']/rmax);full=max([x for x in CURRENT_RANGES if x<=target*(1+1e-9)] or [CURRENT_RANGES[0]])
        vr=min([x for x in VOLTAGE_RANGES if x>=2*full*rmax] or [VOLTAGE_RANGES[-1]])
        s.update(excitation_start_a=full,excitation_end_a=-full,excitation_range_a=full,voltage_range_v=vr);op['steps']+=[dict(x,offset=offset_key(x['contacts'])) for x in self._sweep(s)]
    def _mrecord(self):
        op=self.mop;step=op['steps'][op['index']];e=self._electrical();V=self._observed_voltage();I=self._observed_current(e);cc=op['type']=='contact_check'
        error='REFERENCE_TEMPERATURE_RANGE' if not e['valid'] else 'SOURCE_COMPLIANCE' if e['compliance'] and not cc else 'VOLTAGE_OVERFLOW' if V is None and not cc else None
        c=step['contacts'];row=dict(time_s=op['elapsed'],host_time_s=self.time_s,configuration=f'I{c[0]}{c[1]} V{c[2]}{c[3]}',excitation_a=step['current'],current_a=I,voltage_v=V,field_t=self._field_observed(),
                               temperature_k=self.read()['thermal.indicated_k'],in_compliance=bool(e['compliance']),error_code=error)
        (op['probe'] if step.get('probe') else op['rows']).append(row);op['index']+=1
        if error:return self._mfinish('failed',error)
        if cc and op['setup']['optimized'] and op['index']==len(PAIRS) and len(op['steps'])==len(PAIRS):self._cc_generate()
        done=op['index']>=len(op['steps'])
        if not done and op['per_sample'] and op['setup']['minimum_snr'] and len(op['rows'])%op['per_sample']==0 and len(op['rows'])//op['per_sample']>=2:
            try:_,_,snr=stats([x['value'] for x in self._sample_values(op)]);done=snr is None or snr>=op['setup']['minimum_snr']   # no scatter: SNR infinite
            except ActionRejected:done=True                                                                                     # the summary reports the error
        if done:
            try:op['summary']=self._msummary(op)
            except ActionRejected as exc:return self._mfinish('failed',str(exc))
            self._mfinish('succeeded')
    def _pair_r(self,rows):
        """Configuration resistance of a +I / -I row pair (current reversal removes the offsets)."""
        p,n=rows
        if p['voltage_v'] is None or n['voltage_v'] is None or p['current_a']==n['current_a']:raise ActionRejected('ANALYSIS_DATA')
        return (p['voltage_v']-n['voltage_v'])/(p['current_a']-n['current_a'])
    def _samples(self,op):k=op['per_sample'];return [op['rows'][i:i+k] for i in range(0,len(op['rows'])//k*k,k)]
    def _sample_values(self,op):
        out=[]
        for rows in self._samples(op):
            if op['type']=='resistivity':
                r=[self._pair_r(rows[i:i+2]) for i in range(0,8,2)]
                if not all(.01<=x<=1e7 for x in r):raise ActionRejected('RESISTANCE_RANGE')
                out.append(dict(a=vdp_sheet(r[0],r[1]),b=vdp_sheet(r[2],r[3]),fa=vdp_sheet(r[0],r[1])*math.log(2)/(math.pi*(r[0]+r[1])/2),fb=vdp_sheet(r[2],r[3])*math.log(2)/(math.pi*(r[2]+r[3])/2)))
                out[-1]['value']=(out[-1]['a']+out[-1]['b'])/2
            elif op['type']=='fasthall':
                rh=(self._pair_r(rows[0:2])+self._pair_r(rows[2:4]))/2;out.append(dict(value=rh*op['setup']['excitation_start_a'],r=rh))
            else:
                if op['setup']['reversal']:r=self._pair_r(rows);I=(rows[0]['current_a']-rows[1]['current_a'])/2;V=(rows[0]['voltage_v']-rows[1]['voltage_v'])/2
                else:
                    I=rows[0]['current_a'];V=rows[0]['voltage_v']
                    if V is None or not I:raise ActionRejected('ANALYSIS_DATA')
                    r=V/I
                out.append(dict(value=r,current=I,voltage=V))
        return out
    def _msummary(self,op):
        s=op['setup'];warnings=[]
        if op['type']=='contact_check':
            pairs=[]
            for i,(p,q) in enumerate(PAIRS):
                rows=op['rows'][i*s['points']:(i+1)*s['points']];ok=[r for r in rows if r['voltage_v'] is not None];slope=offset=r2=None
                if len(ok)>=2:
                    x=np.array([r['current_a'] for r in ok]);y=np.array([r['voltage_v'] for r in ok]);slope,offset=(float(v) for v in np.polyfit(x,y,1));tot=float(np.sum((y-y.mean())**2))
                    r2=1-float(np.sum((y-(slope*x+offset))**2))/tot if tot>0 else None
                comp=any(r['in_compliance'] for r in rows);over=len(ok)<len(rows)
                pairs.append(dict(pair=[p,q],slope_ohm=slope,offset_v=offset,r_squared=r2,r_squared_pass=r2 is not None and r2>=s['minimum_r_squared'],in_compliance=comp,voltage_overload=over,current_overload=False))
                if comp:warnings.append(f'Contact pair {p}-{q} in compliance')
                if over:warnings.append(f'Contact pair {p}-{q} voltage overload')
            return dict(pairs=pairs,success=all(x['r_squared_pass'] and not x['in_compliance'] and not x['voltage_overload'] for x in pairs),warnings=warnings)
        d=self._sample_values(op);field=[r['field_t'] for r in op['rows'] if r['field_t'] is not None];temp=[r['temperature_k'] for r in op['rows'] if r['temperature_k'] is not None]
        if op['type']=='resistivity':
            mean,se,snr=stats([x['value'] for x in d]);t=s['thickness_m']
            if field and abs(float(np.mean(field)))>1e-3:warnings.append('Field not zero: resistivity should be measured at zero field')
            return dict(samples=len(d),sheet_resistance_ohm=mean,sheet_standard_error_ohm=se,resistivity_ohm_m=mean*t if t else None,resistivity_standard_error_ohm_m=se*t if t and se is not None else None,snr=snr,
                        geometry_a=dict(sheet_resistance_ohm=stats([x['a'] for x in d])[0],f_value=stats([x['fa'] for x in d])[0]),geometry_b=dict(sheet_resistance_ohm=stats([x['b'] for x in d])[0],f_value=stats([x['fb'] for x in d])[0]),
                        field_t=float(np.mean(field)) if field else None,temperature_k=float(np.mean(temp)) if temp else None,warnings=warnings)
        if op['type']=='fasthall':
            B=s['field_t'];t=s['thickness_m'];vh,vse,vsnr=stats([x['value'] for x in d]);rhs=[x['r']/B for x in d]
            if not any(rhs):raise ActionRejected('HALL_UNRESOLVED')
            rs,rse,_=stats(rhs);ns,nse,_=stats([1/(ELEMENTARY_CHARGE_C*abs(x)) for x in rhs if x]);sheet_rho=(s['resistivity']/t if t else s['resistivity']) if s['resistivity'] else None
            mu=stats([abs(x)/sheet_rho for x in rhs]) if sheet_rho else (None,None,None)
            if not s['resistivity']:warnings.append('No resistivity: mobility not calculated')
            return dict(samples=len(d),hall_voltage_v=vh,hall_voltage_standard_error_v=vse,hall_voltage_snr=vsnr,carrier_type='p' if rs>0 else 'n',p_type_count=sum(x>0 for x in rhs),n_type_count=sum(x<0 for x in rhs),
                        sheet_hall_coefficient_m2_c=rs,sheet_hall_coefficient_standard_error_m2_c=rse,hall_coefficient_m3_c=rs*t if t else None,hall_coefficient_standard_error_m3_c=rse*t if t and rse is not None else None,
                        sheet_carrier_concentration_m2=ns,sheet_carrier_concentration_standard_error_m2=nse,carrier_concentration_m3=ns/t if t else None,carrier_concentration_standard_error_m3=nse/t if t and nse is not None else None,
                        mobility_m2_v_s=mu[0],mobility_standard_error_m2_v_s=mu[1],warnings=warnings)
        r,rse,rsnr=stats([x['value'] for x in d]);i,ise,_=stats([x['current'] for x in d]);v,vse,_=stats([x['voltage'] for x in d])
        return dict(samples=len(d),resistance_ohm=r,resistance_standard_error_ohm=rse,resistance_snr=rsnr,current_a=i,current_standard_error_a=ise,voltage_v=v,voltage_standard_error_v=vse,warnings=warnings)
    def _mresult_of(self,op):
        return dict(type=op['type'],operation_id=op['id'],phase=op['phase'],started=op['started'],elapsed_s=op['elapsed'],setup=copy.deepcopy(op['setup']),summary=copy.deepcopy(op['summary']),
                    rows=copy.deepcopy(op['rows']),error_code=op['error'])
    def _mresult(self,kind):
        op=self.mdata[kind]
        return self._mresult_of(op) if op else dict(type=kind,operation_id=None,phase='not_run',started=None,elapsed_s=0.,setup=None,summary=None,rows=[],error_code=None)
    def _mfinish(self,phase,error=None):
        op=self.mop;op['phase']=phase;op['error']=error;self.mrecords[op['id']]=self._mresult_of(op)
    def _mreset(self,kind):
        if self._mstep() and self.mop['type']==kind:self._mfinish('cancelled','USER_CANCEL')
        self.mdata[kind]=None;return {'type':kind,'reset':True}
    def _mpoll(self,operation_id):
        if operation_id not in self.mrecords:return None
        return self._mresult_of(self.mop) if self.mop and self.mop['id']==operation_id and self.mop['phase']=='running' else self.mrecords[operation_id]
    # ---- instrument faults (1.3.0): manual table 6-1 and 6.3.2, caused by the evaluator
    def _fault_text(self):
        if 'internal_communication' in self.fault_causes:return FAULTS['internal_communication']
        return None if self.cal_state=='valid' else FAULTS['calibration_'+self.cal_state]
    def _fault(self,capability,a):
        if capability=='fault.acknowledge':
            out={'acknowledged':self.fault_message};self.fault_message=None;return out
        code=a['code']
        if capability=='fault.inject':
            if code not in self.fault_causes:self.fault_causes.append(code)
            if code=='internal_communication':
                if self.phase=='running':self._finish('failed','INTERNAL_COMMUNICATION')       # the output is disabled automatically
                if self._mstep():self._mfinish('failed','INTERNAL_COMMUNICATION')
            else:self.cal_state=code[len('calibration_'):]                                      # NOVRAM constants invalid: default constants apply
            if self.power:self.fault_message=FAULTS[code]                                     # reported on the front panel
        else:
            if code in self.fault_causes:self.fault_causes.remove(code)
            if code!='internal_communication' and self.cal_state==code[len('calibration_'):]:self.cal_state='valid'   # recalibrated by service
        return {'injected':list(self.fault_causes)}
    # ---- front panel (1.1.0): System settings, real-time clock, front-panel lock
    def _date(self):return datetime.datetime.fromisoformat(CLOCK_EPOCH)+datetime.timedelta(seconds=math.floor(self.time_s+self.clock_offset_s+1e-6)+60*self.settings['timezone_min'])
    def _date_text(self):return self._date().strftime('%Y-%m-%d %H:%M')
    def _cmd_system_configure(self,section,values):
        validate(obj(SETTING_FIELDS[section]),values)
        if section=='ethernet' and any(not re.fullmatch(IPV4['pattern'],values[k]) for k in ('ip_address','gateway','subnet','dns')):raise ActionRejected('INVALID_IP')
        self.settings.update(values);return {'section':section,'values':copy.deepcopy(values)}
    def _cmd_system_set_clock(self,year,month,day,hour,minute):
        try:t=datetime.datetime(year,month,day,hour,minute)
        except ValueError:raise ActionRejected('INVALID_DATE') from None
        local=datetime.datetime.fromisoformat(CLOCK_EPOCH)+datetime.timedelta(seconds=math.floor(self.time_s+1e-6)+60*self.settings['timezone_min'])
        self.clock_offset_s=(t-local).total_seconds();return {'datetime':self._date_text()}
    def _cmd_system_reset(self):self.settings=copy.deepcopy(SETTINGS_DEFAULT);return {'reset':True}
    def _cmd_system_lock(self,locked):self.locked=bool(locked);return {'locked':self.locked}
    # ---- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
    @property
    def panel(self):
        p=_PANELS.get(self)
        if p is None and PANEL_SPEC:p=_PANELS[self]=_panel_ui.PanelUI(PANEL_SPEC,self)
        return p
    def panel_read(self):
        out=self.read('agent');d=self._date()
        out.update({'host.powered':self.power,'host.about':dict(ABOUT),'host.clock':dict(year=d.year,month=d.month,day=d.day,hour=d.hour,minute=d.minute),
                    'host.rows':[dict(i=i,contact=r['contact_configuration'],polarity=r['polarity'],current_a=r['current_a'],voltage_v=r['voltage_v'],field_t=r['field_t']) for i,r in enumerate(self.rows)],
                    'host.progress':min(1.,len(self.rows)/max(1,9*self.method['repeats'])) if self.phase=='running' and self.method else None,'host.error':self.error,
                    'host.field_waiting':self.field is not None and abs(self.field_actual-self.field_target)>(self.method['field_settling_tolerance_t'] if self.method else 1e-4),
                    'host.cards':self._panel_cards(),'host.results':{t:self._panel_result(t) for t in MEAS_TYPES}})   # external field still moving to its target (DC Hall 'waiting')
        return out
    def _card_status(self,op):
        if op is None:return 'Not run'
        if op['phase']=='running':return f"Running  {int(100*op['index']/max(1,len(op['steps'])))} %"
        return f"Failed: {op['error']}" if op['phase']=='failed' else 'Cancelled' if op['phase']=='cancelled' else None
    def _panel_cards(self):
        """Home screen cards of the measurement types (manual figures 3-1, 3-7, 3-8): status while running, high-level result when complete."""
        cc,res,fh,fw=(self.mdata[t] for t in MEAS_TYPES);out={t:self._card_status(self.mdata[t]) for t in MEAS_TYPES}
        out['optimization']='Not run' if cc is None else 'Parameters input manually' if not cc['setup']['optimized'] else out['contact_check'] or f"Excitation: {cc['setup']['excitation_start_a']*1e3:.4f} mA\nType: Standard (Current)"
        if cc and cc['phase']=='succeeded':out['contact_check']=('Pass' if cc['summary']['success'] else 'Fail')+'\nComplete'
        if res and res['phase']=='succeeded':m=res['summary'];out['resistivity']='ρ average\n'+(si(m['resistivity_ohm_m'],'Ω·m') if m['resistivity_ohm_m'] is not None else si(m['sheet_resistance_ohm'],'Ω/□'))
        if fh and fh['phase']=='succeeded':
            m=fh['summary'];out['fasthall']=f"Mobility average\n{m['mobility_m2_v_s']*1e4:.1f} cm²/(V·s)" if m['mobility_m2_v_s'] is not None else 'Hall coefficient\n'+(sci(m['hall_coefficient_m3_c'],'m³/C') if m['hall_coefficient_m3_c'] is not None else sci(m['sheet_hall_coefficient_m2_c'],'m²/C'))
        if fw and fw['phase']=='succeeded':out['four_wire']='Resistance\n'+si(fw['summary']['resistance_ohm'],'Ω')
        return out
    def _panel_result(self,kind):
        """Result screen of one measurement type: title, the four pair tiles (contact check) and the Setup / Warnings / Results lines."""
        op=self.mdata[kind];tiles={f'p{i}':'' for i in range(4)};passed={f'p{i}':None for i in range(4)}
        if op is None:return dict(title=TITLES[kind],tiles=tiles,passed=passed,lines=[dict(k='No results.',v=''),dict(k='Measurements are started over SCPI',v='(MeasureLINK).')])
        s=op['setup'];m=op['summary'];L=[dict(k='Setup',v='')];kv=lambda k,v:L.append(dict(k=k,v=v))                       # noqa: E731
        title=TITLES[kind] if kind in ('contact_check','four_wire') else f"{TITLES[kind]} ({op['started'].replace(' ',' at ')}, {round(op['elapsed']*1000)} ms)"
        if kind=='four_wire':kv('Excitation contact pair',f"{s['contacts'][0]}-{s['contacts'][1]}");kv('Sense contact pair',f"{s['contacts'][2]}-{s['contacts'][3]}")
        if kind=='fasthall':kv('Field',si(s['field_t'],'T'));kv('Resistivity (ρ)',si(s['resistivity'],'Ω·m' if s['thickness_m'] else 'Ω/□') if s['resistivity'] else 'not given')
        kv('Excitation type','Current')
        if kind=='contact_check':kv('Excitation start value',si(s['excitation_start_a'],'A'));kv('Excitation end value',si(s['excitation_end_a'],'A'))
        else:kv('Excitation value',si(s['excitation_start_a'],'A'))
        kv('Excitation range',si(s['excitation_range_a'],'A'));kv('Measurement range',si(s['voltage_range_v'],'V'));kv('Compliance limit',si(s['compliance_v'],'V'))
        kv('Number of samples',str(s['points']))
        if kind=='contact_check':kv('Minimum R²',f"{s['minimum_r_squared']:.6f}");kv('Optimized','Yes' if s['optimized'] else 'No (manual)')
        else:kv('Minimum SNR','INF' if s['minimum_snr'] is None else f"{s['minimum_snr']:g}")
        if kind in ('resistivity','fasthall'):kv('Sample thickness',si(s['thickness_m'],'m') if s['thickness_m'] else '0 m (sheet)')
        if kind=='four_wire':kv('Excitation reversal','Yes' if s['reversal'] else 'No')
        if s['linked']:kv('Linked','contact check'+(' + resistivity' if kind=='fasthall' and s['resistivity'] else ''))
        kv('Blanking time',si(s['blanking_s'],'s',3))
        status=self._card_status(op)
        if status:L.append(dict(k='Status',v=status));return dict(title=title,tiles=tiles,passed=passed,lines=L)
        L.append(dict(k='Warnings',v='(none)' if not m['warnings'] else m['warnings'][0]));L+=[dict(k='',v=w) for w in m['warnings'][1:]];L.append(dict(k='Results',v=''))
        if kind=='contact_check':
            for i,x in enumerate(m['pairs']):
                flags=' '.join(f for f,on in (('Compliance',x['in_compliance']),('Overload',x['voltage_overload'])) if on)
                tiles[f'p{i}']=f"{'Pass' if x['r_squared_pass'] else 'Fail'}\n{x['pair'][0]}-{x['pair'][1]}\nR²\n"+('--' if x['r_squared'] is None else f"{x['r_squared']:.6f}")+(f'\n{flags}' if flags else '');passed[f'p{i}']=x['r_squared_pass']
                kv(f"Pair {x['pair'][0]}-{x['pair'][1]} slope",si(x['slope_ohm'],'Ω'));kv(f"Pair {x['pair'][0]}-{x['pair'][1]} offset",si(x['offset_v'],'V'))
        elif kind=='resistivity':
            bulk=m['resistivity_ohm_m'] is not None;u='Ω·m' if bulk else 'Ω/□'
            kv('ρ average',si(m['resistivity_ohm_m'] if bulk else m['sheet_resistance_ohm'],u));kv('ρ standard error',si(m['resistivity_standard_error_ohm_m'] if bulk else m['sheet_standard_error_ohm'],u))
            kv('SNR','--' if m['snr'] is None else f"{m['snr']:.2f}");kv('Samples taken',str(m['samples']))
            for g in ('a','b'):x=m['geometry_'+g];kv(f'Geometry {g.upper()} ρ',si(x['sheet_resistance_ohm'],'Ω/□'));kv(f'Geometry {g.upper()} F value',f"{x['f_value']:.6f}")
        elif kind=='fasthall':
            kv('Hall voltage',si(m['hall_voltage_v'],'V'));kv('Hall voltage SNR','--' if m['hall_voltage_snr'] is None else f"{m['hall_voltage_snr']:.2f}")
            kv('Carrier type',f"{m['carrier_type']}-type ({m['p_type_count']} p / {m['n_type_count']} n)");kv('Sheet Hall coefficient',sci(m['sheet_hall_coefficient_m2_c'],'m²/C'))
            if m['hall_coefficient_m3_c'] is not None:kv('Hall coefficient',sci(m['hall_coefficient_m3_c'],'m³/C'))
            kv('Sheet carrier conc.',sci(m['sheet_carrier_concentration_m2'],'m⁻²'))
            if m['carrier_concentration_m3'] is not None:kv('Carrier concentration',sci(m['carrier_concentration_m3'],'m⁻³'))
            kv('Mobility','--' if m['mobility_m2_v_s'] is None else f"{m['mobility_m2_v_s']:.6g} m²/(V·s)")
        else:
            kv('Resistance',si(m['resistance_ohm'],'Ω'));kv('Resistance standard error',si(m['resistance_standard_error_ohm'],'Ω'));kv('Resistance SNR','--' if m['resistance_snr'] is None else f"{m['resistance_snr']:.2f}")
            kv('Current',si(m['current_a'],'A'));kv('Current standard error',si(m['current_standard_error_a'],'A'));kv('Voltage',si(m['voltage_v'],'V'));kv('Voltage standard error',si(m['voltage_standard_error_v'],'V'))
        return dict(title=title,tiles=tiles,passed=passed,lines=L)
    def panel_invoke(self,capability_id,arguments):
        try:return self.invoke(capability_id,**arguments)
        except ActionRejected as exc:raise _panel_ui.PanelRejected(str(exc)) from None
    def panel_time(self):return self.time_s
    def _panel_entry(self,capability_id,arguments):
        validate(PANEL_CAPS[capability_id]['input_schema'],arguments);out=getattr(self,'_cmd_'+capability_id.replace('.','_'))(**arguments)
        if PANEL_CAPS[capability_id]['kind']=='device_command':self.revision+=1
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
    def step(self,dt_s):
        validate(number('s',0,86400),dt_s);total=math.fsum((self._clock_remainder_ns,dt_s*1e9));ns=round(total);self._clock_remainder_ns=total-ns
        if not ns:return
        end=self.time_ns+ns
        while self.time_ns<end:
            boundary=end
            for key in self.axes:
                if self._moving(key):boundary=min(boundary,self.axes[key]['arrival_ns'])
            if self.phase=='running':boundary=min(boundary,self._start_ns+(self._phase_index+1)*round(self.method['phase_duration_s']*1e9))
            if self._mstep():boundary=min(boundary,self.mop['start_ns']+(self.mop['index']+1)*MEAS_PHASE_NS)
            self._propagate(boundary-self.time_ns);self.time_ns=boundary
            if self.plant:
                for key,axis in self.axes.items():
                    speed=self.plant['clamp_speed_m_s' if key=='clamp' else 'ui_tilt_speed_rad_s'];axis['position']=axis['target'] if self.time_ns>=axis['arrival_ns'] else axis['start']+math.copysign(speed*(self.time_ns-axis['start_ns'])/1e9,axis['target']-axis['start'])
            if self.phase=='running':
                self.elapsed=(self.time_ns-self._start_ns)/1e9
                if self.time_ns==self._start_ns+(self._phase_index+1)*round(self.method['phase_duration_s']*1e9):self._record()
            if self._mstep():
                self.mop['elapsed']=(self.time_ns-self.mop['start_ns'])/1e9
                if self.time_ns==self.mop['start_ns']+(self.mop['index']+1)*MEAS_PHASE_NS:self._mrecord()
        self.revision+=1

# remote SCPI commands (USB virtual serial port): the agent instrument commands; not the power button (front panel), the offline Hall analysis,
# the unsupported entries or the evaluator / host entries
REMOTE_CAPS=frozenset(k for k,v in CAPS.items() if v['visibility']=='agent' and k not in UNSUPPORTED and k not in ('power.set','analysis.hall_vdp')
                      and v['kind']!='query')   # reads (queries, poll) stay allowed with the remote interface off (1.3.0, lead rule 5)
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
        return dict(protocol_version=PROTOCOL_VERSION, asset_type_id=ASSET_TYPE_ID, asset_version=ASSET_VERSION, capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'}, state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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
            if not isinstance(capability_id,str) or capability_id not in ALL_CAPS: raise ActionRejected('UNKNOWN_CAPABILITY')
            if self._role=='agent' and ALL_CAPS[capability_id]['visibility']!='agent': raise ActionRejected('ACCESS_DENIED')
            if not isinstance(request_id,str) or not request_id: raise ActionRejected('INVALID_ARGUMENT')
            validate(ALL_CAPS[capability_id]['input_schema'],arguments); key=(self._principal,request_id); digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
            if key in h._requests:
                old,response=h._requests[key]
                if old!=digest: raise ActionRejected('REQUEST_CONFLICT')
                return copy.deepcopy(response)
            self._remote(capability_id)
            h._caller=self._principal                                     # ownership guards in the runtime (_guard) see this caller, also behind a panel gesture
            try: result=h._panel_entry(capability_id,arguments) if capability_id in PANEL_CAPS else self._filter_result(h.invoke(capability_id,**arguments)); operation=result.get('operation_id') if capability_id in ASYNC_STARTS else None
            finally: h._caller=None
            if operation: h._owners[operation]=self._principal
            response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=operation,status='accepted' if operation else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None)
            h._requests[key]=(digest,copy.deepcopy(response)); return response
        except ActionRejected as exc:
            return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,status='unsupported' if str(exc)=='UNSUPPORTED' else 'rejected',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))
    def write(self, *, instance_id, state_id, value, request_id):
        self._target(instance_id); entry=SLOTS.get(state_id)
        if not entry or not entry['write']: raise ActionRejected('ACCESS_DENIED')
        capability,argument=entry['write']; return self.invoke(instance_id=instance_id,capability_id=capability,arguments={argument:value},request_id=request_id)
    def _remote(self, capability_id):
        """The session's direct commands stand for SCPI over the USB interface, which the M91 implements as a virtual serial port (manual 4.4.1,
        table 3-4): with Virtual serial port off an agent's remote commands get no answer (REMOTE_DISABLED); panel gestures are local and still work;
        reads (queries, poll) stay allowed (1.3.0).
        SCPI over TCP gates only the Ethernet interface, which is not connected in the simulation."""
        if self._role=='agent' and capability_id in REMOTE_CAPS and not self._host.settings['virtual_serial']: raise ActionRejected('REMOTE_DISABLED')
    def poll(self, operation_id):
        h=self._host
        if h._owners.get(operation_id)!=self._principal: raise ActionRejected('ACCESS_DENIED')
        result=h._result() if operation_id==h.operation_id and h.phase=='running' else h.records.get(operation_id)
        if result is None:result=h._mpoll(operation_id)
        if result is None: raise ActionRejected('UNKNOWN_OPERATION')
        return dict(operation_id=operation_id,status=result['phase'],result=self._filter_result(result))
    def cancel(self, operation_id, request_id):
        self._remote('acquisition.stop'); self.poll(operation_id); h=self._host
        if not isinstance(request_id,str) or not request_id: raise ActionRejected('INVALID_ARGUMENT')
        key=(self._principal,request_id)
        if key in h._cancel_operations:
            if h._cancel_operations[key]!=operation_id: raise ActionRejected('REQUEST_CONFLICT')
            return copy.deepcopy(h._requests[key][1])
        if key in h._requests: raise ActionRejected('REQUEST_CONFLICT')
        meas=h.mop if h.mop and h.mop['id']==operation_id else None
        if meas is not None:
            if meas['phase']!='running': raise ActionRejected('NOT_RUNNING')
            result=self.invoke(instance_id=h.instance_id,capability_id='measurement.reset',arguments={'type':meas['type']},request_id=request_id)
            if result['status']=='succeeded': h._cancel_operations[key]=operation_id
            return result
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
 'system.configure':['system.settings','system.datetime'],
 **{k:['measurement.type','measurement.status','measurement.progress','measured.current_a','measured.voltage_v','excitation.source_voltage_v'] for k in MEAS_STARTS},'measurement.reset':['measurement.type','measurement.status','measurement.progress','measured.current_a','measured.voltage_v','excitation.source_voltage_v'],'system.set_clock':['system.datetime'],'system.reset':['system.settings','system.datetime'],'system.lock':['system.locked'],
 'sim.step':list(SLOTS)}
for k,v in EFFECTS.items():CAPS[k]['effects']=v
CAPS['power.set']['effects']+=['measurement.type','measurement.status','measurement.progress','fault.message']
CAPS['fault.inject']['effects']=['fault.injected','fault.message','calibration.state','acquisition.phase','measurement.type','measurement.status','measurement.progress']
CAPS['fault.clear']['effects']=['fault.injected','calibration.state'];CAPS['fault.acknowledge']['effects']=['fault.message']
ERRORS=sorted(set(__import__('re').findall(r"ActionRejected\('([A-Z_0-9]+)'",Path(__file__).read_text()))|{'POWER_OFF','USER_CANCEL','SOURCE_COMPLIANCE','CURRENT_MEASUREMENT_OVERFLOW','VOLTAGE_OVERFLOW','REFERENCE_TEMPERATURE_RANGE','RESISTANCE_RANGE'})

def export_lakeshore_measurereadym91_contract_metadata():
    slots={};caps={}
    for k,v in SLOTS.items():
        slots[k]=dict(meaning_zh=v['meaning'],component_ids=[v['component']],value_schema=copy.deepcopy(v['schema']),unit=v['unit'],reference_conditions='SI; four-corner thin uniform scalar sheet; external normal B; conventional current and external-field reversal.',origin=v['origin'],visibility=v['visibility'],access='read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'LakeShoreMeasurereadyM91Session.read(state_ids=[{k!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description='Only explicit host step advances field, thermal, motion, detector response and 1800s warmup. Each zero/positive/negative excitation phase records its endpoint after declared dwell: current mode uses +/-I and voltage mode uses +/-V.'),source_ids=['simulation_assumption'])
    limits=[
      '本体是M91 Hall控制器；磁体、样品夹具、场探头和温控边界由宿主明确组合，不虚构机内磁场或样品加热器。外部磁体供电/磁能不在本体电热账内。',
      '本模型实现传统电流/外部磁场反转vdP（DC Hall）及接触检查、电阻率、FastHall、四线测量（1.2.0）。FastHall按Onsager互易（两组互易对角配置电流反转）近似实现，厂家私有算法与电压补偿采样未实现；Hall bar及200GΩ全域高阻计量未实现；新测量类型只用电流激励；电压激励明确依赖宿主选择M9-ADD-HR固件；不把unsupported负例称实现验证。',
      '测量类型（1.2.0）假设：每相=消隐2ms+采样1/60s，在相末读检测器（一阶响应τ，不按采样时间平均）；接触为欧姆接触，接触检查R²只反映顺从、过载、量化、检测器滞后与漂移；任意一对接点两端电阻=驱动+接触电阻；三端配置未建模；vdP样品无接点5、6；自动接触检查规则（1µA探测、两端电压不超过最大电压1%、测量量程≥2倍两端电压）为工程假设，厂家算法未公开；最小SNR按已完成样本的平均/标准误判停；标准误差来自确定性样本间差异（无随机噪声模型）。',
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
        un=k in UNSUPPORTED;async_=k in ASYNC_STARTS;caps[k]={key:copy.deepcopy(v[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']}
        caps[k].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ('scene','robot','hardware') else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.lakeshore_measurereadym91.LakeShoreMeasurereadyM91Session.invoke',invocation=f'session.invoke(instance_id="m91_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),preconditions=[] if un or v['kind'] in ('query','analysis') else [dict(description_zh='运行入口原子检查明确外场/热条件、夹持、真实1800s预热、参考温域、量程和异步所有权。',state_ids=['power.enabled','warmup.ready','sample.present','clamp.position_m','field.configured','acquisition.phase'],enforced_by='runtime',check='LakeShoreMeasurereadyM91Runtime.invoke guards before commit')],state_effects=[dict(state_id=s,timing='immediate',description_zh='命令实际即时状态变化') for s in v['effects']],observes=[],model_effects=dict(mode='none',description_zh='只输出可映射状态，实际场景验证单独登记。',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion=('各接点配置的零/正/负激励相按真实停留时间采样，全部完成或测量范围错误终止' if k=='acquisition.start' else '各相按消隐+采样时间（2ms+1/60s）依次采样，全部完成、达到最小SNR或错误终止') if async_ else '原子命令；预热/外场/电热/机械/采样仅宿主step推进',completion_state_ids=(['acquisition.phase'] if k=='acquisition.start' else ['measurement.status']) if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','external_magnet','four_contact_fixture','programmable_source','voltmeter'],partial_failure_zh='取消/断电停止电流并冻结已完成测量相；外部场和独立温控、已积显热继续。范围错误保留不可用相后停止激励。'),errors=[dict(code=e,meaning_zh=e,side_effects='partial' if async_ and e in ('POWER_OFF','USER_CANCEL','SOURCE_COMPLIANCE','CURRENT_MEASUREMENT_OVERFLOW','VOLTAGE_OVERFLOW','REFERENCE_TEMPERATURE_RANGE','RESISTANCE_RANGE') else 'none') for e in ERRORS],verification=[],limitations=limits,source_ids=['manufacturer_primary','simulation_assumption','template_library'])
        if k in ('thermal.set_temperature','thermal.disable','sample.set_clamp','ui.set_tilt','field.set_target','acquisition.start','power.set') or k in MEAS_STARTS:
            caps[k]['state_effects'] += [dict(state_id=s,timing='on_step',description_zh='宿主时间推进外场、预热、电热、机械或实际测量相') for s in SLOTS if s not in v['effects'] and s not in ('plant.configured','geometry.configured','thermal.configured','field.configured','sample.present','power.enabled','system.settings','system.datetime','system.locked')]
    called=_panel_ui.spec_capabilities(PANEL_SPEC) if PANEL_SPEC else []
    for k,v in PANEL_CAPS.items():
        gesture=v['kind']=='device_command';caps[k]={key:copy.deepcopy(v[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']}
        caps[k].update(availability='implemented',implementation={layer:'implemented' if layer in ('process','host') else 'absent' for layer in ['process','scene','robot','hardware','host']},
            binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.lakeshore_measurereadym91.LakeShoreMeasurereadyM91Session.invoke',invocation=f'session.invoke(instance_id="m91_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),
            preconditions=[dict(description_zh='按panel_ui.json的面板规则：SLEEP时只有电源键有效，SYST:KLOC锁定时触摸无效；被调用的命令各自检查前提，被拒时显示消息且状态不变',state_ids=['power.enabled','system.locked'],enforced_by='runtime',check='LakeShoreMeasurereadyM91Runtime._panel_entry（面板引擎_panel_ui.py）')] if gesture else [],
            state_effects=[dict(state_id=s,timing='immediate',description_zh='手势调用的命令立即改变的状态') for s in sorted({e for x in called for e in CAPS[x]['effects']})] if gesture else [],
            observes=[],model_effects=dict(mode='none',description_zh='屏幕画面由panel.capture提供；3D屏幕纹理与按键关节触发手势属后续Matterix步骤',binding_ids=[]),
            execution=dict(mode='synchronous',time_domain='simulation',completion_criterion='手势瞬时完成（不推进仿真时钟）',completion_state_ids=[],timeout_s=None,cancellation='unsupported',retry='same_request_id',resources=['panel'],partial_failure_zh='一个手势可调用多个命令：已被接受的前序命令保留，被拒的命令不改变状态' if gesture else '只读'),
            errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','REQUEST_CONFLICT','ACCESS_DENIED']],verification=[],
            limitations=['面板层：panel_ui.json（M91 FastHall用户手册图3-1、3-9–3-19、4-6、6-5与3.2、3.7、3.9.6、4.4.2、6.3节）解释手势并调用本运行层的命令；触摸屏只显示结果，测量由SCPI启动'],source_ids=['manufacturer_primary','simulation_assumption'])
    mechanism=dict(component_ids=['controller','contact_carriage','sample_sheet','thermal_port','field_reference'],state_ids=list(slots),meaning_zh='传统外部磁场与电流反转的四接点Hall及van der Pauw观测反演，真实预热、有限场响应与电热质量账',implementation='lumped',equations=[
      'Rs=rho(T)/d; RA=Rs*log(1+exp(u))/pi; RB=Rs*log(1+exp(-u))/pi. Therefore exp(-pi*RA/Rs)+exp(-pi*RB/Rs)=1. u is declared contact-asymmetry proxy.',
      'Current step=range*1e-5; Current mode:Iactual=sign(Iset)*min(abs(Iset),Vcompliance/(Rs*drive_geometry_factor+Rcontact)),1<=Vcompliance<=10V. Voltage mode:Irequested=Vset/(Rdrive+Rcontact),Iactual=sign(Irequested)*min(abs(Irequested),Ilimit),Vactual=Iactual*(Rdrive+Rcontact);Ilimit100nA..100mA with0.1%FS programming and explicitly assumed smallest-containing-FS selection. Voltage ranges limit maximum magnitude, not minimum resolvable measured signal.',
      'V_A=I*RA; V_B=I*RB; V_H=I*(RH(T)*B/d*hall_geometry_factor+misalignment*sqrt(RA*RB)). Gain/offset/linear drift feed explicit first-order detector dz/dt=(Vsignal-z)/tau.',
      'dB/dt=clip((Btarget-B)/tau,-slew,+slew), integrated analytically through transition. Field and thermal source are external boundaries and continue independently of M91 logical power.',
      'External inverse fits V=R*I+offset+drift*t for A/B and V=Rmisalign*I+(RH*g/d)*I*B+offset+drift*t for Hall. A bracketing root solves vdP sheet resistance using observed RA/RB only.',
      'nH=1/(e*abs(RH)),muH=abs(RH)/rho; assumed single-carrier n=rH*nH,mu=muH/rH. Hall sign labels Hall response only; multiband true carrier populations are not inferred.',
      'Psource=I*Vsource=I²*(Rdrive+Rcontact); Psample=I²*Rdrive; Pcontact_export=I²*Rcontact. Csample_total*dT/dt=Pcontroller+Psample-G*(T-Ta), Csample_total=0.1J/K+rho_mass*volume*Cp.',
      'Pcontroller=clip((K+G)*(Ttarget-Ta)-K*(T-Ta),-cooling_limit,+heating_limit) when clamped and external controller enabled. Open clamp uses ambient Gopen and no active controller. Incoming/outgoing sample sensible heat is ledgered.',
      'Measurement types: four-terminal V(c)-V(d)=I*R, R=+-RA (edges 1-2,3-4) or +-RB (2-3,4-1) by orientation; diagonals R=s*(h*H+M), H=RH*B*g/d, M=misalignment*sqrt(RA*RB), h=-1 when current and voltage pairs are exchanged (Onsager R_ab,cd(B)=R_cd,ab(-B)); two-terminal R=Rdrive+Rcontact. Resistivity: geometry A from (R_21,34,R_32,41), B from (R_43,12,R_14,23), each with current reversal, Rs by vdP, F=Rs*ln2/(pi*(Rv+Rh)/2). FastHall: R_Hall=(R_13,24+R_24,31)/2, R_Hs=R_Hall/B_user, n_s=1/(e|R_Hs|), mu=|R_Hs|/R_s. Contact check: least-squares V=slope*I+offset per pair, R2=1-SSres/SStot.',
      'Idle linear thermal states use exact matrix exponential; active detector/temperature-dependent electrical model uses Radau integration with sampling-event boundaries; nanosecond residual carries fractional host timestep (e.g.60Hz). Warmup is1800 host seconds after last powerup.'],parameters={},source_ids=['simulation_assumption','template_library'],limitations=limits)
    spec=importlib.util.spec_from_file_location('_m91_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()});ann.annotate_export(SLUG,caps,slots,{'hall_van_der_pauw':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'geometry.configure':(GEOMETRY,GEOMETRY_EXAMPLE),'thermal.configure':(THERMAL,THERMAL_EXAMPLE),'field.configure':(FIELD,FIELD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'excitation.voltage.configure':(VOLTAGE,VOLTAGE_EXAMPLE)})
    for key,value in SLOTS.items():slots[key]['meaning_zh']=value['meaning']
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'hall_van_der_pauw':mechanism})

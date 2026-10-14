"""QuadPro2-DAC0 with Keithley2450: timed four-contact thin-film wafer mapping."""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
import numpy as np
from scipy.integrate import solve_ivp
PROTOCOL_VERSION='1.0.0';SLUG='signatone_quadpro2';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-142'
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch04/t1_thermal'/SLUG;LIBRARY_PATH=ASSET/'sheet_resistance_library.json'

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
CURRENT_RANGES=[10.**p for p in range(-8,1)]
VOLTAGE_RANGES=[.02,.2,2.,20.,200.]
PROBE_SPACINGS=[.001016,.00127,.0015875]
CONFIGURATIONS={'a_14_23':(0,3,1,2),'b_13_24':(0,2,1,3)}
WARMUP_S=3600.;CALIBRATION_S=180.;PROBE_RAISED_M=.01

def library():return {v['id']:v for v in json.loads(LIBRARY_PATH.read_text())['templates']}
def reference_rho(record,T,allow_invalid=False):
    rows=record['curves']['resistivity'];lo=max(rows[0]['temperature_K'],record['valid_temperature_range_K'][0]);hi=min(rows[-1]['temperature_K'],record['valid_temperature_range_K'][1]);valid=lo-1e-6<=T<=hi+1e-6
    if not valid and not allow_invalid:return None
    return float(np.interp(T,[p['temperature_K'] for p in rows],[p['value_ohm_m'] for p in rows])),valid

def probe_points(x_m,y_m,spacing_m,orientation_rad=0.):
    direction=complex(math.cos(orientation_rad),math.sin(orientation_rad));center=complex(x_m,y_m)
    return [center+v*spacing_m*direction for v in [-1.5,-.5,.5,1.5]]
def disk_kernel(z,a,radius_m,contact_radius_m=None):
    # Neumann disk source kernel, modulo constants which cancel for source/sink.
    distance=abs(z-a) if contact_radius_m is None else max(abs(z-a),contact_radius_m)
    return math.log(distance/radius_m)+math.log(abs(1-z*a.conjugate()/radius_m**2))
def disk_transfer_coefficient(diameter_m,spacing_m,x_m,y_m,configuration='a_14_23',orientation_rad=0.,contact_radius_m=0.):
    R=diameter_m/2;pts=probe_points(x_m,y_m,spacing_m,orientation_rad)
    if any(abs(p)+max(contact_radius_m,1e-10)>=R for p in pts):raise ActionRejected('PROBE_OUTSIDE_WAFER')
    a,b,c,d=CONFIGURATIONS[configuration]
    potential=lambda z:-(disk_kernel(z,pts[a],R)-disk_kernel(z,pts[b],R))/(2*math.pi)
    value=potential(pts[c])-potential(pts[d])
    if not value>0:raise ActionRejected('GEOMETRY_UNRESOLVED')
    return float(value)
def disk_drive_coefficient(diameter_m,spacing_m,x_m,y_m,configuration,contact_radius_m):
    R=diameter_m/2;pts=probe_points(x_m,y_m,spacing_m);a,b,_,_=CONFIGURATIONS[configuration]
    potential=lambda z:-(disk_kernel(z,pts[a],R,contact_radius_m)-disk_kernel(z,pts[b],R,contact_radius_m))/(2*math.pi)
    return float(potential(pts[a])-potential(pts[b]))

def infer_sheet_resistance(diameter_m,spacing_m,x_m,y_m,observations,calibration_gain,thickness_prior_m):
    estimates=[]
    for configuration in CONFIGURATIONS:
        rows=[p for p in observations if p['configuration']==configuration]
        if len(rows)!=2 or rows[0]['current_a']*rows[1]['current_a']>=0:raise ActionRejected('CURRENT_REVERSAL_REQUIRED')
        dI=rows[0]['current_a']-rows[1]['current_a'];dV=rows[0]['voltage_v']-rows[1]['voltage_v']
        if abs(dI)<1e-20:raise ActionRejected('CURRENT_UNRESOLVED')
        coefficient=disk_transfer_coefficient(diameter_m,spacing_m,x_m,y_m,configuration);value=calibration_gain*dV/dI/coefficient
        if not math.isfinite(value) or value<=0:raise ActionRejected('NONPOSITIVE_SHEET_RESISTANCE')
        estimates.append(value)
    mean=float(np.mean(estimates));spread=abs(estimates[0]-estimates[1])/mean
    return dict(sheet_resistance_ohm_sq=mean,resistivity_ohm_m=None if thickness_prior_m is None else mean*thickness_prior_m,configuration_a_ohm_sq=estimates[0],configuration_b_ohm_sq=estimates[1],configuration_disagreement_fraction=spread,evidence='external_reversed_VI_and_finite_disk_Neumann_correction; rho_only_with_explicit_thickness_prior; not_OEM_ASTM_certification')

PLANT_EXAMPLE=dict(ambient_k=300.,voltage_gain=1.03,voltage_offset_v=2e-6,voltage_response_tau_s=.01,current_gain=1.,electrical_contact_radius_m=10e-6,current_contact_resistance_ohm=20.,thermal_conductance_w_k=.05,local_thermal_capacity_j_k=.1,xy_speed_m_s=.04,z_speed_m_s=.005,hood_speed_rad_s=.5,temperature_resolution_k=.001,rear_triax_connected=True,accept_engineering=True)
PLANT=obj({'ambient_k':number('K',283.15,313.15),'voltage_gain':number('1',.1,10),'voltage_offset_v':number('V',-.1,.1),'voltage_response_tau_s':number('s',1e-5,10),'current_gain':number('1',.1,10),'electrical_contact_radius_m':number('m',1e-7,4e-5),'current_contact_resistance_ohm':number('ohm',0,1e9),'thermal_conductance_w_k':number('W/K',0,1000),'local_thermal_capacity_j_k':number('J/K',.001,100),'xy_speed_m_s':number('m/s',1e-5,.3),'z_speed_m_s':number('m/s',1e-5,.03),'hood_speed_rad_s':number('rad/s',.001,2),'temperature_resolution_k':number('K',1e-5,1),'rear_triax_connected':BOOL,'accept_engineering':BOOL})
WAFER_EXAMPLE=dict(diameter_m=.1,substrate_thickness_m=.0005)
WAFER=obj({'diameter_m':number('m',.01,.3),'substrate_thickness_m':number('m',1e-5,.01)})
PROBE_EXAMPLE=dict(spacing_m=.00127,force_per_pin_n=.83356525,tip_radius_m=40.64e-6,tip_material='tungsten_carbide')
PROBE=obj({'spacing_m':dict(type='number',enum=PROBE_SPACINGS,**{'x-unit':'m'}),'force_per_pin_n':dict(type='number',enum=[.44129925,.83356525,1.765197],**{'x-unit':'N'}),'tip_radius_m':dict(type='number',enum=[40.64e-6,127e-6,254e-6],**{'x-unit':'m'}),'tip_material':enum(['tungsten_carbide','osmium'])})
THERMAL_SAMPLE=dict(conducting_thickness_m=1e-6,film_density_kg_m3=6000.,film_cp_j_kg_k=300.,substrate_density_kg_m3=2330.,substrate_cp_j_kg_k=700.,initial_temperature_k=300.,accept_thin_layer_and_thermal_assumptions=True)
THERMAL_PROPERTIES={'conducting_thickness_m':number('m',1e-9,5e-5),'film_density_kg_m3':number('kg/m^3',1,30000),'film_cp_j_kg_k':number('J/kg/K',1,10000),'substrate_density_kg_m3':number('kg/m^3',1,30000),'substrate_cp_j_kg_k':number('J/kg/K',1,10000),'initial_temperature_k':number('K',1,363.14),'accept_thin_layer_and_thermal_assumptions':BOOL}
SAMPLE_EXAMPLE=dict(THERMAL_SAMPLE,reference_id='starry_40927',gradient_x_fraction=.1,gradient_y_fraction=-.05,radial_fraction=.03,accept_synthetic_map_and_bulk_reference=True)
SAMPLE=obj(dict(THERMAL_PROPERTIES,reference_id=TEXT,gradient_x_fraction=number('1',-.2,.2),gradient_y_fraction=number('1',-.2,.2),radial_fraction=number('1',-.2,.2),accept_synthetic_map_and_bulk_reference=BOOL))
STANDARD_EXAMPLE=dict(THERMAL_SAMPLE,actual_sheet_resistance_ohm_sq=100.,accept_host_standard_not_certified=True)
STANDARD=obj(dict(THERMAL_PROPERTIES,actual_sheet_resistance_ohm_sq=number('ohm/sq',1e-12,1e15),accept_host_standard_not_certified=BOOL))
POINT=obj({'x_m':number('m',-.15,.15),'y_m':number('m',-.15,.15)})
MAP_EXAMPLE=dict(points=[dict(x_m=x,y_m=y) for x,y in [(0,0),(.02,0),(0,.02),(-.02,0),(0,-.02),(.049,0)]],edge_exclusion_m=.003,thickness_prior_m=1e-6)
MAP=obj({'points':array(POINT,1,14000),'edge_exclusion_m':number('m',0,.15),'thickness_prior_m':nullable(number('m',1e-9,5e-5))})
METHOD_EXAMPLE=dict(auto_current=True,current_min_a=1e-8,current_max_a=.001,fixed_current_a=.0001,voltage_range_v=.2,voltage_compliance_v=20.,target_voltage_min_v=.005,target_voltage_max_v=.08,phase_duration_s=.3,settling_s=.2,require_calibration=True,require_warmup=False)
METHOD=obj({'auto_current':BOOL,'current_min_a':dict(type='number',enum=CURRENT_RANGES,**{'x-unit':'A'}),'current_max_a':dict(type='number',enum=CURRENT_RANGES,**{'x-unit':'A'}),'fixed_current_a':number('A',0,1),'voltage_range_v':dict(type='number',enum=VOLTAGE_RANGES,**{'x-unit':'V'}),'voltage_compliance_v':number('V',.002,200),'target_voltage_min_v':number('V',1e-8,100),'target_voltage_max_v':number('V',1e-7,200),'phase_duration_s':number('s',.3,30),'settling_s':number('s',0,60),'require_calibration':BOOL,'require_warmup':BOOL})
OBSERVATION=obj({'configuration':enum(list(CONFIGURATIONS)),'current_a':number('A'),'voltage_v':number('V'),'host_time_s':number('s',0)})
OBS_INPUT=obj({'configuration':enum(list(CONFIGURATIONS)),'current_a':number('A'),'voltage_v':number('V')})
INFERENCE=obj({'sheet_resistance_ohm_sq':number('ohm/sq',0),'resistivity_ohm_m':nullable(number('ohm*m',0)),'configuration_a_ohm_sq':number('ohm/sq',0),'configuration_b_ohm_sq':number('ohm/sq',0),'configuration_disagreement_fraction':number('1',0),'evidence':TEXT})
ANALYSIS_INPUT=obj({'diameter_m':number('m',.01,.3),'spacing_m':dict(type='number',enum=PROBE_SPACINGS,**{'x-unit':'m'}),'x_m':number('m',-.15,.15),'y_m':number('m',-.15,.15),'observations':array(OBS_INPUT,4,4),'calibration_gain':number('1',.1,10),'thickness_prior_m':nullable(number('m',1e-9,5e-5))})
ANALYSIS_EXAMPLE=dict(diameter_m=.1,spacing_m=.00127,x_m=0.,y_m=0.,observations=[dict(configuration=c,current_a=I,voltage_v=I*100*disk_transfer_coefficient(.1,.00127,0,0,c)+2e-6) for c in CONFIGURATIONS for I in [.0001,-.0001]],calibration_gain=1.,thickness_prior_m=None)
MAP_ROW=obj({'point_index':dict(type='integer',minimum=0,maximum=13999,**{'x-unit':'1'}),'x_m':number('m'),'y_m':number('m'),'time_s':number('s',0),'host_time_s':number('s',0),'observations':array(OBSERVATION,0,4),'sheet_resistance_ohm_sq':nullable(number('ohm/sq')),'resistivity_ohm_m':nullable(number('ohm*m')),'configuration_disagreement_fraction':nullable(number('1')),'quality':enum(['estimated','unavailable']),'error_code':nullable(TEXT)})
STATS=obj({'count':dict(type='integer',minimum=0,maximum=14000,**{'x-unit':'1'}),'mean_ohm_sq':nullable(number('ohm/sq')),'std_ohm_sq':nullable(number('ohm/sq')),'minimum_ohm_sq':nullable(number('ohm/sq')),'maximum_ohm_sq':nullable(number('ohm/sq')),'relative_std_percent':nullable(number('%'))})
AUTO_ROW=obj({'requested_current_a':number('A'),'measured_current_positive_a':number('A'),'measured_current_negative_a':number('A'),'voltage_positive_v':nullable(number('V')),'voltage_negative_v':nullable(number('V')),'reversal_signal_v':nullable(number('V')),'time_s':number('s',0),'source_compliance':BOOL})
EXCLUDED=obj({'point_index':dict(type='integer',minimum=0,maximum=13999,**{'x-unit':'1'}),'x_m':number('m'),'y_m':number('m'),'reason':TEXT})
RESULT=obj({'operation_id':nullable(TEXT),'operation_kind':nullable(enum(['mapping','calibration'])),'phase':PHASE,'elapsed_s':number('s',0),'rows':array(MAP_ROW,0,14000),'excluded_points':array(EXCLUDED,0,14000),'auto_trials':array(AUTO_ROW,0,9),'selected_current_a':number('A',0,1),'statistics':STATS,'calibration_gain':nullable(number('1')),'template_source':nullable(TEXT),'evidence':TEXT,'error_code':nullable(TEXT)})
CAPS={};SLOTS={}
def cap(k,i,o,c,z,kind='device_command',visibility='agent',example=None):CAPS[k]=dict(input_schema=i,output_schema=o,component_ids=c,function_zh=z,kind=kind,visibility=visibility,example_arguments=copy.deepcopy(example or {}),effects=[])
def slot(k,s,u,z,origin='derived',visibility='agent',component='controller'):SLOTS[k]=dict(schema=s,unit=u,meaning=z,origin=origin,visibility=visibility,component=component,write=None)
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller','source_meter','sample_stage'],'宿主明确误差、运动速度、被动热参数及2450 rear triax连接，不注入厂家准确度','configuration','evaluator',PLANT_EXAMPLE)
cap('wafer.configure',WAFER,obj({'configured':BOOL}),['sample_wafer'],'10至300mm圆片和绝缘支承基底厚度，载入后锁定几何','configuration','evaluator',WAFER_EXAMPLE)
cap('probe.configure',PROBE,obj({'configured':BOOL}),['probe_head'],'选择SP4间距、tip/载荷并使既有校准失效；不伪装Pa压力或动态重建3D探针','configuration',example=PROBE_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_wafer'],'装载真实rho文献参考驱动的薄膜+基底；地图/膜厚/热参数明确宿主假设','configuration','evaluator',SAMPLE_EXAMPLE)
cap('standard.load',STANDARD,obj({'loaded':BOOL}),['sample_wafer'],'宿主装入独立给定实际Rs的薄膜标样代理，证书/NIST真实性未验证','configuration','evaluator',STANDARD_EXAMPLE)
cap('sample.remove',obj({}),obj({'loaded':BOOL}),['sample_wafer'],'卸除全部膜/基底质量和显热，既有测量与耗散账不回滚','configuration','evaluator')
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_switch','source_meter'],'整系统源与自动运动电源，2450额定精度预热状态随真实时间累计',example={'enabled':True})
cap('stage.move',POINT,obj({'target_x_m':number('m'),'target_y_m':number('m')}),['stage_x','sample_stage'],'安全抬针后移动真实XY机械坐标，测点坐标与样品台位移取反',example={'x_m':.01,'y_m':-.01})
cap('probe.set_contact',obj({'contact':BOOL}),obj({'target_m':number('m',0,PROBE_RAISED_M)}),['probe_head'],'有限速度下降接触或抬针，不跳跃针尖姿态',example={'contact':True})
cap('enclosure.set_open',obj({'opened':BOOL}),obj({'target_rad':number('rad',0,1.1)}),['hood'],'宿主手动暗箱翻盖有限速度代理，打开装样、关闭测量','simulation','evaluator',{'opened':False})
cap('map.configure',MAP,obj({'configured':BOOL,'accepted_points':dict(type='integer',minimum=0,maximum=14000,**{'x-unit':'1'}),'excluded_points':dict(type='integer',minimum=0,maximum=14000,**{'x-unit':'1'})}),['sample_stage','controller'],'最多14000自定义点及边缘排除；无导电厚度先验时仅报告Rs','configuration',example=MAP_EXAMPLE)
cap('method.configure',METHOD,obj({'configured':BOOL}),['source_meter','controller'],'AutoRange只首点逐档真试测，后续地图固定该电流；每点双接线正反四相≥1.2s','configuration',example=METHOD_EXAMPLE)
cap('calibration.start',obj({'reference_sheet_resistance_ohm_sq':number('ohm/sq',1e-12,1e15)}),obj({'operation_id':TEXT,'minimum_duration_s':number('s',180)}),['source_meter','probe_head','controller'],'外部给定标样值与真实4相观测决定校正；实际累计至少180s，换针后重做',example={'reference_sheet_resistance_ohm_sq':100.})
cap('calibration.clear',obj({}),obj({'calibrated':BOOL}),['controller'],'显式清除既有校正','configuration')
cap('acquisition.start',obj({}),obj({'operation_id':TEXT,'accepted_points':dict(type='integer',minimum=1,maximum=14000,**{'x-unit':'1'})}),['source_meter','stage_x','sample_stage','probe_head','controller'],'自动抬针/XY移动/下针/等待/首点量程/双接线正反四相，逐点建立实际地图')
cap('acquisition.stop',obj({}),obj({'phase':PHASE,'elapsed_s':number('s',0)}),['controller','source_meter','probe_head'],'切断激励、停止当前运动并保留完成点及已生热；不瞬移回零')
cap('acquisition.result',obj({}),RESULT,['controller'],'公开地图/统计/量程试测/原始VI，所有角色统一去除所选参考身份','query')
cap('analysis.sheet_resistance',ANALYSIS_INPUT,INFERENCE,['controller'],'仅外部VI/几何/校正反演Rs，rho必须有独立导电膜厚先验','analysis',example=ANALYSIS_EXAMPLE)
cap('provenance.read',obj({}),obj({'reference_id':nullable(TEXT),'template_source':nullable(TEXT),'evidence':TEXT}),['controller'],'仅宿主读取当前参考曲线或输入标样来源','query','evaluator')
LEDGER_UNITS=dict(mass_loaded_kg='kg',mass_present_kg='kg',mass_removed_kg='kg',mass_error_kg='kg',volume_loaded_m3='m^3',volume_present_m3='m^3',volume_removed_m3='m^3',volume_error_m3='m^3',source_input_j='J',sample_joule_j='J',contact_heat_export_j='J',electrical_error_j='J',ambient_heat_export_j='J',loaded_sensible_j='J',removed_sensible_j='J',thermal_stored_j='J',thermal_energy_error_j='J')
cap('ledger.read',obj({}),obj({k:number(u) for k,u in LEDGER_UNITS.items()}),['controller'],'薄膜/基底质量体积、被动温度、样品焦耳热/接触热外流账','query','evaluator')
cap('sim.step',obj({'dt_s':number('s',0,86400)}),obj({'time_s':number('s',0)}),['controller'],'唯一宿主时钟，有限自动运动、首点量程、180s校准、预热/采样/被动热','simulation','evaluator',{'dt_s':10.})
UNSUPPORTED=['hardware.connect','robot.load','temperature.tcr_control','measurement.thick_bulk_wafer','analysis.joint_unknown_thickness_and_resistivity','measurement.astm_certification','noise.empirical','accuracy.certification']
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'本配置未实现：'+k)
for k,c in [('plant.configured','controller'),('wafer.configured','sample_wafer'),('probe.configured','probe_head'),('sample.present','sample_wafer'),('power.enabled','power_switch'),('source.warmup_ready','source_meter'),('calibration.valid','controller'),('stage.moving','sample_stage')]:slot(k,BOOL,'1','当前配置/运行状态',component=c)
for k,u,z,c in [('stage.x_m','m','真实X台位置','stage_x'),('stage.y_m','m','真实Y台位置','sample_stage'),('stage.target_x_m','m','X台目标','stage_x'),('stage.target_y_m','m','Y台目标','sample_stage'),('probe.position_m','m','探针当前上提量；0接触','probe_head'),('probe.target_m','m','探针目标上提量；0接触','probe_head')]:slot(k,number(u,-.15,.15),u,z+'；运动只随step改变','setpoint' if 'target' in k else 'scene',component=c)
slot('hood.angle_rad',number('rad',0,1.1),'rad','暗箱翻盖当前角，0闭合/1.1打开','scene',component='hood')
slot('hood.target_angle_rad',number('rad',0,1.1),'rad','暗箱翻盖目标角','setpoint',component='hood')
slot('power.angle_rad',number('rad',0,.28),'rad','电源开关姿态代理','scene',component='power_switch')
slot('source.warmup_elapsed_s',number('s',0,3600),'s','2450当前连续上电预热，3600s封顶；不构成额定准确度认证',component='source_meter')
slot('thermal.indicated_k',nullable(number('K')),'K','薄膜/基底及局部显式热容节点的量化温度，仅被动环境热交换','sensor',component='sample_wafer')
slot('measurement.selected_current_a',number('A',0,1),'A','本图首点AutoRange选定后固定的电流幅度','derived',component='source_meter')
slot('measured.current_a',nullable(number('A')),'A','2450实际电流经显式增益及厂家测量分辨率量化后的观测','sensor',component='source_meter')
slot('measured.voltage_v',nullable(number('V')),'V','四针输入电压增益/偏置/一阶响应及2450测量分辨率量化观测','sensor',component='source_meter')
slot('measurement.last_sheet_resistance_ohm_sq',nullable(number('ohm/sq')),'ohm/sq','最后实际完成点的几何修正Rs，未完成点不输出')
slot('display.sheet_resistance_ohm_sq',nullable(number('ohm/sq')),'ohm/sq','通电时PC显示最后已测点Rs；断电返回不可用，历史测量值仍保留')
slot('measurement.mean_sheet_resistance_ohm_sq',nullable(number('ohm/sq')),'ohm/sq','已完成地图点的算术平均Rs，非隐藏全图真值')
slot('calibration.gain',nullable(number('1')),'1','由外部参考Rs与实际观测比值形成的校正，换针失效')
slot('acquisition.phase',PHASE,'1','异步地图/校准运行状态')
slot('acquisition.stage',TEXT,'1','真实自动序列阶段：运动、接触、量程、四相、抬针')
slot('acquisition.points',{'type':'integer','minimum':0,'maximum':14000,'x-unit':'1'},'1','实际完成有效或失败测点行数')
slot('acquisition.elapsed_s',number('s',0),'s','当前作业实际宿主历时')
slot('clock.time_s',number('s',0),'s','唯一宿主时间')
for k,u,z in [('truth.sample_k','K','薄膜/基底集中节点真温'),('truth.sample_mass_kg','kg','薄膜及基底总质量'),('truth.conducting_thickness_m','m','隐藏导电膜厚，不等于基底厚度')]:slot(k,nullable(number(u)),u,z,'process_truth','evaluator','sample_wafer')

class SignatoneQuadpro2Runtime:
    def __init__(self,instance_id='quadpro2_01'):
        self.instance_id=instance_id;self.plant=self.wafer=self.probe=self.sample=self.record=None;self.sample_kind=None;self.map=None;self.accepted=[];self.excluded=[];self.method=copy.deepcopy(METHOD_EXAMPLE);self.power=False;self.power_started_ns=None;self.time_ns=0;self._clock_remainder_ns=0.;self.revision=0
        self.axes={k:dict(position=v,target=v,start=v,start_ns=0,arrival_ns=0) for k,v in [('x',0.),('y',0.),('z',PROBE_RAISED_M),('hood',1.1)]};self.x=np.zeros(6) # theta,voltage detector,sourceJ,sampleJ,contactJ,ambientJ
        self.calibration_gain=None;self.phase='idle';self.stage='idle';self.operation_kind=None;self.operation_id=None;self.elapsed=0.;self.rows=[];self.auto_trials=[];self.selected_current=0.;self.error=None;self._counter=0;self._start_ns=0;self._event_ns=None;self._job_points=[];self._job_excluded=[];self._site=0;self._measure_phase=0;self._partial=[];self._auto_candidates=[];self._auto_index=0;self._auto_positive=None;self._calibration_reference=None;self._last_sheet=None
        self.records={};self._principals={};self._requests={};self._owners={};self._cancel_operations={};self._source=self._reference_id=None;self._evidence='no_sample';self._operation_source=None;self._operation_evidence='no_measurement';self.mass_loaded=self.mass_removed=self.volume_loaded=self.volume_removed=self.loaded_sensible=self.removed_sensible=0.
    @property
    def time_s(self):return self.time_ns/1e9
    def register_principal(self,principal_id,visibility='agent'):
        if not isinstance(principal_id,str) or not principal_id or principal_id in self._principals or visibility not in ('agent','debug','evaluator'):raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id):
        if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return SignatoneQuadpro2Session(self,principal_id)
    def describe(self):return export_signatone_quadpro2_contract_metadata()
    def _warmup(self):return min(WARMUP_S,(self.time_ns-self.power_started_ns)/1e9) if self.power and self.power_started_ns is not None else 0.
    def _moving(self,key=None):return any(self.time_ns<self.axes[k]['arrival_ns'] for k in ([key] if key else ['x','y','z']))
    def _raised(self):return self.axes['z']['position']>=PROBE_RAISED_M-1e-12 and not self._moving('z')
    def _closed(self):return self.axes['hood']['position']<=1e-12 and self.axes['hood']['target']==0 and not self._moving('hood')
    def _volume(self):return math.pi*(self.wafer['diameter_m']/2)**2*(self.wafer['substrate_thickness_m']+self.sample['conducting_thickness_m']) if self.sample else 0.
    def _masses(self):
        if not self.sample:return 0.,0.
        A=math.pi*(self.wafer['diameter_m']/2)**2;return A*self.wafer['substrate_thickness_m']*self.sample['substrate_density_kg_m3'],A*self.sample['conducting_thickness_m']*self.sample['film_density_kg_m3']
    def _mass(self):return sum(self._masses())
    def _sample_capacity(self):
        if not self.sample:return 0.
        ms,mf=self._masses();return ms*self.sample['substrate_cp_j_kg_k']+mf*self.sample['film_cp_j_kg_k']
    def _capacity(self):return (self.plant['local_thermal_capacity_j_k'] if self.plant else .1)+self._sample_capacity()
    def _temperature(self):return self.plant['ambient_k']+float(self.x[0]) if self.plant else None
    def _axis_speed(self,k):return self.plant['hood_speed_rad_s' if k=='hood' else 'z_speed_m_s' if k=='z' else 'xy_speed_m_s']
    def _move(self,**targets):
        for k,target in targets.items():
            a=self.axes[k];a.update(target=target,start=a['position'],start_ns=self.time_ns,arrival_ns=self.time_ns+round(abs(target-a['position'])/self._axis_speed(k)*1e9))
    def _stop_motion(self):
        for k in ['x','y','z']:
            a=self.axes[k];a.update(target=a['position'],start=a['position'],start_ns=self.time_ns,arrival_ns=self.time_ns)
    def _set_stage(self,stage,wait_s=None):self.stage=stage;self._event_ns=self.time_ns+round(wait_s*1e9) if wait_s is not None else max(self.axes[k]['arrival_ns'] for k in ['x','y','z'])
    def _point(self):return self._job_points[self._site] if self._job_points else dict(point_index=0,x_m=-self.axes['x']['position'],y_m=-self.axes['y']['position'])
    def _electrically_active(self):return self.power and self.phase=='running' and self.stage in ('auto_positive','auto_negative','measure') and self.axes['z']['position']<=1e-12 and self._closed()
    def _configuration(self):return 'a_14_23' if self.stage.startswith('auto') or self._measure_phase<2 else 'b_13_24'
    def _requested_current(self):
        if not self._electrically_active():return 0.
        if self.stage.startswith('auto'):return self._auto_candidates[self._auto_index]*(1 if self.stage=='auto_positive' else -1)
        return self.selected_current*(1 if self._measure_phase%2==0 else -1)
    def _current_range(self,I=None):
        value=abs(self._requested_current() if I is None else I);return next((v for v in CURRENT_RANGES if v>=value*(1-1e-12)),1.)
    def _local_sheet(self,T):
        if self.sample_kind=='standard':return self.sample['actual_sheet_resistance_ohm_sq'],T<363.15
        raw=reference_rho(self.record,T,allow_invalid=True);p=self._point();R=self.wafer['diameter_m']/2;xx=p['x_m']/R;yy=p['y_m']/R;factor=1+self.sample['gradient_x_fraction']*xx+self.sample['gradient_y_fraction']*yy+self.sample['radial_fraction']*(xx*xx+yy*yy)
        return raw[0]/self.sample['conducting_thickness_m']*factor,raw[1] and T<363.15
    def _electrical(self,T=None):
        if not self._electrically_active():return dict(current=0.,voltage=0.,source_voltage=0.,sample_power=0.,contact_power=0.,compliance=False,valid=True)
        T=self._temperature() if T is None else T;Rs,valid=self._local_sheet(T);p=self._point();c=self._configuration();geo=(self.wafer['diameter_m'],self.probe['spacing_m'],p['x_m'],p['y_m']);K=disk_transfer_coefficient(*geo,c);drive=Rs*disk_drive_coefficient(*geo,c,self.plant['electrical_contact_radius_m']);contact=self.plant['current_contact_resistance_ohm'];total=drive+contact;requested=self._requested_current();I=math.copysign(min(abs(requested),self.method['voltage_compliance_v']/total,math.sqrt(20./total),.1 if self.method['voltage_compliance_v']>20 else 1.),requested)
        return dict(current=I,voltage=I*Rs*K,source_voltage=I*total,sample_power=I*I*drive,contact_power=I*I*contact,compliance=abs(I-requested)>max(abs(requested)*1e-10,1e-18),valid=bool(valid))
    def _observed(self,e=None):
        if not self._electrically_active():return None,None
        e=self._electrical() if e is None else e;v=float(self.x[1]);full=self.method['voltage_range_v'];qv=full*5e-7;qi=self._current_range()*1e-6;return float(round(e['current']*self.plant['current_gain']/qi)*qi),None if abs(v)>full else float(round(v/qv)*qv)
    def _stats(self):
        values=[x['sheet_resistance_ohm_sq'] for x in self.rows if x['sheet_resistance_ohm_sq'] is not None]
        if not values:return dict(count=0,mean_ohm_sq=None,std_ohm_sq=None,minimum_ohm_sq=None,maximum_ohm_sq=None,relative_std_percent=None)
        mean=float(np.mean(values));std=float(np.std(values));return dict(count=len(values),mean_ohm_sq=mean,std_ohm_sq=std,minimum_ohm_sq=min(values),maximum_ohm_sq=max(values),relative_std_percent=100*std/mean)
    def read(self,role='evaluator'):
        if role not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
        current,voltage=self._observed();d={'plant.configured':self.plant is not None,'wafer.configured':self.wafer is not None,'probe.configured':self.probe is not None,'sample.present':self.sample is not None,'power.enabled':self.power,'source.warmup_ready':self._warmup()>=WARMUP_S,'calibration.valid':self.calibration_gain is not None,'stage.moving':self._moving(),'stage.x_m':self.axes['x']['position'],'stage.y_m':self.axes['y']['position'],'stage.target_x_m':self.axes['x']['target'],'stage.target_y_m':self.axes['y']['target'],'probe.position_m':self.axes['z']['position'],'probe.target_m':self.axes['z']['target'],'hood.angle_rad':self.axes['hood']['position'],'hood.target_angle_rad':self.axes['hood']['target'],'power.angle_rad':.28 if self.power else 0.,'source.warmup_elapsed_s':self._warmup(),'thermal.indicated_k':float(round(self._temperature()/self.plant['temperature_resolution_k'])*self.plant['temperature_resolution_k']) if self.plant else None,'measurement.selected_current_a':self.selected_current,'measured.current_a':current,'measured.voltage_v':voltage,'measurement.last_sheet_resistance_ohm_sq':self._last_sheet,'display.sheet_resistance_ohm_sq':self._last_sheet if self.power else None,'measurement.mean_sheet_resistance_ohm_sq':self._stats()['mean_ohm_sq'],'calibration.gain':self.calibration_gain,'acquisition.phase':self.phase,'acquisition.stage':self.stage,'acquisition.points':len(self.rows),'acquisition.elapsed_s':self.elapsed,'clock.time_s':self.time_s,'truth.sample_k':self._temperature(),'truth.sample_mass_kg':self._mass() if self.sample else None,'truth.conducting_thickness_m':self.sample['conducting_thickness_m'] if self.sample else None}
        return {k:v for k,v in d.items() if role!='agent' or SLOTS[k]['visibility']=='agent'}
    def _propagate(self,ns):
        if not ns or not self.plant:return
        dt=ns/1e9;p=self.plant;C=self._capacity();G=p['thermal_conductance_w_k']
        if self._electrically_active():
            def rhs(_t,y):
                e=self._electrical(p['ambient_k']+y[0]);loss=G*y[0];return [(e['sample_power']-loss)/C,(p['voltage_gain']*e['voltage']+p['voltage_offset_v']-y[1])/p['voltage_response_tau_s'],e['current']*e['source_voltage'],e['sample_power'],e['contact_power'],loss]
            sol=solve_ivp(rhs,(0.,dt),self.x,method='Radau',rtol=1e-10,atol=[1e-11,1e-13,1e-13,1e-13,1e-13,1e-12])
            if not sol.success:raise RuntimeError('ELECTROTHERMAL_INTEGRATION_FAILED')
            self.x=sol.y[:,-1]
        else:
            initial=self.x[0];self.x[0]*=math.exp(-G*dt/C);self.x[5]+=C*(initial-self.x[0]);self.x[1]*=math.exp(-dt/p['voltage_response_tau_s'])
    def _ledger(self):
        source,sample,contact,ambient=self.x[2:];stored=self._capacity()*float(self.x[0]);return dict(mass_loaded_kg=self.mass_loaded,mass_present_kg=self._mass(),mass_removed_kg=self.mass_removed,mass_error_kg=self.mass_loaded-self._mass()-self.mass_removed,volume_loaded_m3=self.volume_loaded,volume_present_m3=self._volume(),volume_removed_m3=self.volume_removed,volume_error_m3=self.volume_loaded-self._volume()-self.volume_removed,source_input_j=float(source),sample_joule_j=float(sample),contact_heat_export_j=float(contact),electrical_error_j=float(source-sample-contact),ambient_heat_export_j=float(ambient),loaded_sensible_j=self.loaded_sensible,removed_sensible_j=self.removed_sensible,thermal_stored_j=stored,thermal_energy_error_j=float(sample-ambient+self.loaded_sensible-self.removed_sensible-stored))
    def _result(self):
        if self.phase!='running' and self.operation_id in self.records:return copy.deepcopy(self.records[self.operation_id])
        return dict(operation_id=self.operation_id,operation_kind=self.operation_kind,phase=self.phase,elapsed_s=self.elapsed,rows=copy.deepcopy(self.rows),excluded_points=copy.deepcopy(self._job_excluded),auto_trials=copy.deepcopy(self.auto_trials),selected_current_a=self.selected_current,statistics=self._stats(),calibration_gain=self.calibration_gain,template_source=self._operation_source,evidence=self._operation_evidence,error_code=self.error)
    def _finish(self,phase,error=None):
        self.phase=phase;self.stage='idle';self.error=error;self._event_ns=None;self._stop_motion()
        if self.operation_id:self.records[self.operation_id]=self._result()
    def _capture(self):
        e=self._electrical();I,V=self._observed(e);error='REFERENCE_TEMPERATURE_RANGE' if not e['valid'] else 'SOURCE_COMPLIANCE' if e['compliance'] else 'VOLTAGE_OVERFLOW' if V is None else None
        return I,V,error,e
    def _failure_row(self,error):
        p=self._point();self.rows.append(dict(point_index=p['point_index'],x_m=p['x_m'],y_m=p['y_m'],time_s=self.elapsed,host_time_s=self.time_s,observations=copy.deepcopy(self._partial),sheet_resistance_ohm_sq=None,resistivity_ohm_m=None,configuration_disagreement_fraction=None,quality='unavailable',error_code=error));self._finish('failed',error)
    def _begin_measure(self):
        self._measure_phase=0;self._partial=[];dwell=max(self.method['phase_duration_s'],CALIBRATION_S/4) if self.operation_kind=='calibration' else self.method['phase_duration_s'];self._set_stage('measure',dwell)
    def _begin_point(self):
        self._partial=[];self._move(z=PROBE_RAISED_M);self._set_stage('raise')
    def _advance_event(self):
        if self.stage=='raise':
            p=self._point();self._move(x=-p['x_m'],y=-p['y_m']);self._set_stage('move')
        elif self.stage=='move':self._move(z=0.);self._set_stage('lower')
        elif self.stage=='lower':self._set_stage('settle',self.method['settling_s'])
        elif self.stage=='settle':
            if self._site==0 and self.method['auto_current']:
                self._auto_index=0;self._set_stage('auto_positive',.6)
            else:self._begin_measure()
        elif self.stage=='auto_positive':
            self._auto_positive=self._capture();self._set_stage('auto_negative',.6)
        elif self.stage=='auto_negative':
            Ip,Vp,ep,physical_p=self._auto_positive;In,Vn,en,physical_n=self._capture();signal=None if Vp is None or Vn is None else abs(Vp-Vn)/2;current=self._auto_candidates[self._auto_index];self.auto_trials.append(dict(requested_current_a=current,measured_current_positive_a=Ip,measured_current_negative_a=In,voltage_positive_v=Vp,voltage_negative_v=Vn,reversal_signal_v=signal,time_s=self.elapsed,source_compliance=physical_p['compliance'] or physical_n['compliance']))
            if ep or en:
                previous=self.auto_trials[-2] if len(self.auto_trials)>1 else None
                if (ep or en)=='REFERENCE_TEMPERATURE_RANGE' or previous is None or previous['reversal_signal_v'] is None or previous['reversal_signal_v']<=10*self.method['voltage_range_v']*5e-7:self._failure_row(ep or en);return
                self.selected_current=previous['requested_current_a'];self._begin_measure()
            elif signal<self.method['target_voltage_min_v'] and self._auto_index+1<len(self._auto_candidates):self._auto_index+=1;self._set_stage('auto_positive',.6)
            else:
                if signal is None or signal<=10*self.method['voltage_range_v']*5e-7:self._failure_row('AUTORANGE_UNRESOLVED');return
                self.selected_current=self.auto_trials[-2]['requested_current_a'] if signal>self.method['target_voltage_max_v'] and len(self.auto_trials)>1 else current;self._begin_measure()
        elif self.stage=='measure':
            I,V,error,_e=self._capture()
            if error:self._failure_row(error);return
            self._partial.append(dict(configuration=self._configuration(),current_a=I,voltage_v=V,host_time_s=self.time_s));self._measure_phase+=1
            if self._measure_phase<4:
                self._set_stage('measure',max(self.method['phase_duration_s'],CALIBRATION_S/4) if self.operation_kind=='calibration' else self.method['phase_duration_s']);return
            p=self._point();prior=None if self.operation_kind=='calibration' else self.map['thickness_prior_m'];args=dict(diameter_m=self.wafer['diameter_m'],spacing_m=self.probe['spacing_m'],x_m=p['x_m'],y_m=p['y_m'],observations=[{k:x[k] for k in OBS_INPUT['properties']} for x in self._partial],calibration_gain=1. if self.operation_kind=='calibration' else self.calibration_gain or 1.,thickness_prior_m=prior)
            try:inferred=infer_sheet_resistance(**args)
            except ActionRejected as exc:self._failure_row(str(exc));return
            self.rows.append(dict(point_index=p['point_index'],x_m=p['x_m'],y_m=p['y_m'],time_s=self.elapsed,host_time_s=self.time_s,observations=copy.deepcopy(self._partial),sheet_resistance_ohm_sq=inferred['sheet_resistance_ohm_sq'],resistivity_ohm_m=inferred['resistivity_ohm_m'],configuration_disagreement_fraction=inferred['configuration_disagreement_fraction'],quality='estimated',error_code=None));self._last_sheet=inferred['sheet_resistance_ohm_sq']
            if self.operation_kind=='calibration':
                gain=self._calibration_reference/inferred['sheet_resistance_ohm_sq']
                if not .1<=gain<=10:self._finish('failed','CALIBRATION_RANGE');return
                self.calibration_gain=gain
            self._move(z=PROBE_RAISED_M);self._set_stage('raise_after')
        elif self.stage=='raise_after':
            self._site+=1
            if self._site>=len(self._job_points):self._finish('succeeded')
            else:self._begin_point()
        else:raise RuntimeError('UNKNOWN_SEQUENCE_STAGE')
    def _filter_points(self,a):
        accepted=[];excluded=[];R=self.wafer['diameter_m']/2
        for i,p in enumerate(a['points']):
            reason=None
            if math.hypot(p['x_m'],p['y_m'])>R-a['edge_exclusion_m']+1e-12:reason='EDGE_EXCLUSION'
            elif max(abs(p['x_m']),abs(p['y_m']))>.13:reason='STAGE_TRAVEL'
            else:
                try:disk_transfer_coefficient(self.wafer['diameter_m'],self.probe['spacing_m'],p['x_m'],p['y_m'],contact_radius_m=self.plant['electrical_contact_radius_m'])
                except ActionRejected:reason='PROBE_FOOTPRINT_OUTSIDE_WAFER'
            row=dict(point_index=i,**p)
            if reason:excluded.append(dict(row,reason=reason))
            else:accepted.append(row)
        return accepted,excluded
    def _load(self,a,standard):
        if not self.plant or not self.wafer or not self.probe or self.sample or not self._raised() or self._moving() or self.axes['hood']['position']<1.0 or self._moving('hood') or max(abs(self.axes['x']['position']),abs(self.axes['y']['position']))>1e-12 or not a['accept_thin_layer_and_thermal_assumptions']:raise ActionRejected('PRECONDITION_FAILED')
        if a['conducting_thickness_m']/self.probe['spacing_m']>.05:raise ActionRejected('THIN_LAYER_DOMAIN')
        if standard:
            if not a['accept_host_standard_not_certified']:raise ActionRejected('ASSUMPTION_NOT_ACCEPTED')
            record=None
        else:
            if not a['accept_synthetic_map_and_bulk_reference']:raise ActionRejected('ASSUMPTION_NOT_ACCEPTED')
            record=library().get(a['reference_id'])
            if record is None:raise ActionRejected('UNKNOWN_REFERENCE')
            if reference_rho(record,a['initial_temperature_k']) is None:raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
        old=self._capacity();self.sample=copy.deepcopy(a);self.sample_kind='standard' if standard else 'material';self.record=copy.deepcopy(record);new=self._capacity();delta=a['initial_temperature_k']-self.plant['ambient_k'];self.x[0]=(old*self.x[0]+(new-old)*delta)/new;self.loaded_sensible+=(new-old)*delta;self.mass_loaded+=self._mass();self.volume_loaded+=self._volume();self._reference_id=None if standard else a['reference_id'];self._source='host_input_thin_film_standard_no_certificate' if standard else json.dumps(record['source'],ensure_ascii=False,sort_keys=True);self._evidence='explicit_host_standard_and_thermal_assumptions' if standard else 'measured_literature_rho; assumed_bulk_to_thin_film_and_synthetic_spatial_map';return {'loaded':True}
    def invoke(self,capability,**a):
        if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability]['input_schema'],a)
        if capability in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
        if capability=='analysis.sheet_resistance':return infer_sheet_resistance(**a)
        if capability=='acquisition.result':return self._result()
        if capability=='provenance.read':return dict(reference_id=self._reference_id,template_source=self._source,evidence=self._evidence)
        if capability=='ledger.read':return self._ledger()
        if capability=='sim.step':self.step(a['dt_s']);return {'time_s':self.time_s}
        if self.phase=='running' and capability not in ('acquisition.stop','power.set'):raise ActionRejected('BUSY')
        if capability=='plant.configure':
            if self.plant or not a['accept_engineering']:raise ActionRejected('PRECONDITION_FAILED')
            self.plant=copy.deepcopy(a);out={'configured':True}
        elif capability=='wafer.configure':
            if self.sample:raise ActionRejected('PRECONDITION_FAILED')
            self.wafer=copy.deepcopy(a);self.map=None;self.accepted=[];self.excluded=[];out={'configured':True}
        elif capability=='probe.configure':
            if not self.plant or not self._raised() or self._moving() or self.axes['hood']['position']<1.0 or self._moving('hood'):raise ActionRejected('PRECONDITION_FAILED')
            if self.sample and self.sample['conducting_thickness_m']/a['spacing_m']>.05:raise ActionRejected('THIN_LAYER_DOMAIN')
            self.probe=copy.deepcopy(a);self.calibration_gain=None;self.map=None;self.accepted=[];self.excluded=[];out={'configured':True}
        elif capability in ('sample.load','standard.load'):out=self._load(a,capability=='standard.load')
        elif capability=='sample.remove':
            if not self.sample or not self._raised() or self._moving() or self.axes['hood']['position']<1.0 or self._moving('hood') or max(abs(self.axes['x']['position']),abs(self.axes['y']['position']))>1e-12:raise ActionRejected('PRECONDITION_FAILED')
            self.mass_removed+=self._mass();self.volume_removed+=self._volume();self.removed_sensible+=self._sample_capacity()*float(self.x[0]);self.sample=self.record=self.sample_kind=None;self._source=self._reference_id=None;self._evidence='no_sample';out={'loaded':False}
        elif capability=='power.set':
            if not a['enabled']:
                if self.phase=='running':self._finish('cancelled','POWER_OFF')
                else:self._stop_motion()
            if self.power!=a['enabled']:self.power_started_ns=self.time_ns if a['enabled'] else None
            self.power=a['enabled'];out={'enabled':self.power}
        elif capability=='enclosure.set_open':
            if not self.plant or self._moving() or not self._raised():raise ActionRejected('PRECONDITION_FAILED')
            target=1.1 if a['opened'] else 0.;self._move(hood=target);out={'target_rad':target}
        elif capability=='stage.move':
            if not self.power or not self.plant or not self._raised() or self._moving('hood') or max(abs(a['x_m']),abs(a['y_m']))>.13:raise ActionRejected('STAGE_TRAVEL_OR_CLEARANCE')
            self._move(x=a['x_m'],y=a['y_m']);out={'target_x_m':a['x_m'],'target_y_m':a['y_m']}
        elif capability=='probe.set_contact':
            if not self.power or not self.plant or self._moving('x') or self._moving('y') or self._moving('hood') or a['contact'] and (not self.sample or not self.probe or not self._closed()):raise ActionRejected('PRECONDITION_FAILED')
            if a['contact']:disk_transfer_coefficient(self.wafer['diameter_m'],self.probe['spacing_m'],-self.axes['x']['position'],-self.axes['y']['position'],contact_radius_m=self.plant['electrical_contact_radius_m'])
            target=0. if a['contact'] else PROBE_RAISED_M;self._move(z=target);out={'target_m':target}
        elif capability=='map.configure':
            if not self.wafer or not self.probe or not self.plant:raise ActionRejected('PRECONDITION_FAILED')
            if a['edge_exclusion_m']>=self.wafer['diameter_m']/2:raise ActionRejected('EDGE_EXCLUSION_RANGE')
            if a['edge_exclusion_m']+1e-12<max(0.,self.wafer['diameter_m']/2-.13):raise ActionRejected('STAGE_TRAVEL_ENVELOPE')
            accepted,excluded=self._filter_points(a);self.map=copy.deepcopy(a);self.accepted=accepted;self.excluded=excluded;out={'configured':True,'accepted_points':len(accepted),'excluded_points':len(excluded)}
        elif capability=='method.configure':
            if a['voltage_compliance_v']>20 and a['current_max_a']>.1:raise ActionRejected('SOURCE_RANGE_ENVELOPE')
            if a['current_min_a']>a['current_max_a'] or a['fixed_current_a']>a['current_max_a'] or a['target_voltage_min_v']>=a['target_voltage_max_v'] or a['target_voltage_max_v']>a['voltage_range_v']:raise ActionRejected('METHOD_RANGE')
            if any(abs(a[k]*1e9-round(a[k]*1e9))>1e-4 for k in ['phase_duration_s','settling_s']):raise ActionRejected('TIME_GRID')
            self.method=copy.deepcopy(a);out={'configured':True}
        elif capability=='calibration.clear':self.calibration_gain=None;out={'calibrated':False}
        elif capability in ('acquisition.start','calibration.start'):
            if not self.power or not self.plant or not self.wafer or not self.probe or not self.sample or not self._closed() or self._moving():raise ActionRejected('PRECONDITION_FAILED')
            if self.method['require_warmup'] and self._warmup()<WARMUP_S:raise ActionRejected('WARMUP_REQUIRED')
            if self.sample_kind=='material' and reference_rho(self.record,self._temperature()) is None:raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
            if self._temperature()>=363.15:raise ActionRejected('PROBE_TEMPERATURE_RANGE')
            if not self.plant['rear_triax_connected'] and (self.method['current_min_a']<1e-6 if self.method['auto_current'] else self._current_range(self.method['fixed_current_a'])<1e-6):raise ActionRejected('REAR_TRIAX_REQUIRED')
            iscal=capability=='calibration.start'
            if iscal and self.sample_kind!='standard':raise ActionRejected('STANDARD_REQUIRED')
            if not iscal and (not self.map or not self.accepted):raise ActionRejected('MAP_REQUIRED')
            if not iscal and self.method['require_calibration'] and self.calibration_gain is None:raise ActionRejected('CALIBRATION_REQUIRED')
            if not self.method['auto_current'] and self.method['fixed_current_a']<=0:raise ActionRejected('ZERO_EXCITATION')
            points=[dict(point_index=0,x_m=0.,y_m=0.)] if iscal else self.accepted
            for p in points:disk_transfer_coefficient(self.wafer['diameter_m'],self.probe['spacing_m'],p['x_m'],p['y_m'],contact_radius_m=self.plant['electrical_contact_radius_m'])
            self._counter+=1;self.operation_id=f'{self.instance_id}:acquisition:{self._counter}';self.operation_kind='calibration' if iscal else 'mapping';self._operation_source=self._source;self._operation_evidence=self._evidence;self.phase='running';self.elapsed=0.;self.rows=[];self.auto_trials=[];self.error=None;self._start_ns=self.time_ns;self._site=0;self._job_points=copy.deepcopy(points);self._job_excluded=[] if iscal else copy.deepcopy(self.excluded);self._calibration_reference=a['reference_sheet_resistance_ohm_sq'] if iscal else None;self._auto_candidates=[v for v in CURRENT_RANGES if self.method['current_min_a']<=v<=self.method['current_max_a']];q=self._current_range(self.method['fixed_current_a'])*5e-5;self.selected_current=0. if self.method['auto_current'] else float(round(self.method['fixed_current_a']/q)*q);self._begin_point();out={'operation_id':self.operation_id,'minimum_duration_s':CALIBRATION_S} if iscal else {'operation_id':self.operation_id,'accepted_points':len(points)}
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
            for k in self.axes:
                if self._moving(k):boundary=min(boundary,self.axes[k]['arrival_ns'])
            if self.phase=='running':boundary=min(boundary,self._event_ns)
            self._propagate(boundary-self.time_ns);self.time_ns=boundary
            if self.plant:
                for k,a in self.axes.items():a['position']=a['target'] if self.time_ns>=a['arrival_ns'] else a['start']+math.copysign(self._axis_speed(k)*(self.time_ns-a['start_ns'])/1e9,a['target']-a['start'])
            if self.phase=='running':
                self.elapsed=(self.time_ns-self._start_ns)/1e9
                if self.time_ns==self._event_ns:self._advance_event()
        self.revision+=1

class SignatoneQuadpro2Session:
    def __init__(self, host, principal_id): self._host = host; self._principal = principal_id
    @property
    def _role(self): return self._host._principals[self._principal]
    def _target(self, instance_id):
        if instance_id != self._host.instance_id: raise ActionRejected('UNKNOWN_INSTANCE')
    def describe(self, *, instance_id=None, asset_type_id=None):
        if (instance_id is None) == (asset_type_id is None): raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None: self._target(instance_id)
        if asset_type_id is not None and asset_type_id != ASSET_TYPE_ID: raise ActionRejected('UNKNOWN_INSTANCE')
        meta = export_signatone_quadpro2_contract_metadata()
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
            result['evidence'] = 'observed_four_probe_VI_with_finite_disk_thin_layer_and_explicit_synthetic_map_assumptions; selected template details require evaluator provenance.read' if result['operation_id'] is not None else 'no_measurement'
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
            result=self._filter_result(h.invoke(capability_id,**arguments)); operation=result.get('operation_id') if capability_id in ('acquisition.start','calibration.start') else None
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
 'plant.configure':['plant.configured','thermal.indicated_k','truth.sample_k'],
 'wafer.configure':['wafer.configured'],
 'probe.configure':['probe.configured','calibration.valid','calibration.gain'],
 'sample.load':['sample.present','thermal.indicated_k','truth.sample_k','truth.sample_mass_kg','truth.conducting_thickness_m'],
 'standard.load':['sample.present','thermal.indicated_k','truth.sample_k','truth.sample_mass_kg','truth.conducting_thickness_m'],
 'sample.remove':['sample.present','truth.sample_mass_kg','truth.conducting_thickness_m'],
 'power.set':['display.sheet_resistance_ohm_sq','power.enabled','power.angle_rad','source.warmup_ready','source.warmup_elapsed_s','stage.moving','stage.target_x_m','stage.target_y_m','probe.target_m','acquisition.phase','acquisition.stage','measured.current_a','measured.voltage_v'],
 'stage.move':['stage.target_x_m','stage.target_y_m','stage.moving'],
 'probe.set_contact':['probe.target_m','stage.moving'],
 'enclosure.set_open':['hood.target_angle_rad'],
 'calibration.clear':['calibration.valid','calibration.gain'],
 'acquisition.start':['acquisition.phase','acquisition.stage','acquisition.elapsed_s','acquisition.points','measurement.mean_sheet_resistance_ohm_sq','measurement.selected_current_a','probe.target_m','stage.moving','measured.current_a','measured.voltage_v'],
 'calibration.start':['acquisition.phase','acquisition.stage','acquisition.elapsed_s','acquisition.points','measurement.mean_sheet_resistance_ohm_sq','measurement.selected_current_a','probe.target_m','stage.moving','measured.current_a','measured.voltage_v'],
 'acquisition.stop':['acquisition.phase','acquisition.stage','stage.moving','stage.target_x_m','stage.target_y_m','probe.target_m','measured.current_a','measured.voltage_v'],
 'sim.step':list(SLOTS)}
for k,v in EFFECTS.items():CAPS[k]['effects']=v
ERRORS=sorted(set(__import__('re').findall(r"ActionRejected\('([A-Z_0-9]+)'",Path(__file__).read_text()))|{'POWER_OFF','USER_CANCEL','SOURCE_COMPLIANCE','VOLTAGE_OVERFLOW','REFERENCE_TEMPERATURE_RANGE','CALIBRATION_RANGE','AUTORANGE_UNRESOLVED'})

def export_signatone_quadpro2_contract_metadata():
    slots={};caps={}
    for k,v in SLOTS.items():slots[k]=dict(meaning_zh=v['meaning'],component_ids=[v['component']],value_schema=copy.deepcopy(v['schema']),unit=v['unit'],reference_conditions='SI; QuadPro2-DAC0/Keithley2450/SP4; thin conducting film on insulating circular substrate, passive thermal boundary.',origin=v['origin'],visibility=v['visibility'],access='read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'SignatoneQuadpro2Session.read(state_ids=[{k!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description='Motion/electrothermal/warmup/sample clocks advance only with host step. Measurements record endpoint after explicit settled positive/negative current dwell.'),source_ids=['simulation_assumption'])
    limits=[
      '固定QuadPro2-DAC0：D为Keithley2450，A自动型，C300mm晶片，0无TCR；不是用其它源表参数替代，基础配置没有主动温控。SP4几何默认50085TRQ(1.27mm、85gf/针、WC、40.64µm tip radius、Q适配)。',
      'wafer直径10至300mm。默认100mm直径、0.5mm绝缘基底上1µm导电膜；导电厚度/密度/Cp/空间分布是宿主假设。geometry.configure式变形未实现，wafer.configure/probe.configure改变过程几何不自动重建静态3D示例。',
      '导电层厚度必须不超过针距5%，采用二维薄片模型；0.5mm基底厚度不能被当成导电厚度。厚块晶片、体硅有限厚度、有限接触电流场、各向异性张量未实现。',
      '电气接触半径是独立宿主工程输入，不等于厂家针尖球面曲率半径。点源Green用于电压探针；驱动电阻在实际电流接触处按声明接触半径截断对数奇点，附加接触阻抗导热至外界。',
      '有限圆片绝缘边界采用Neumann Green同号镜像：g(z,a)=ln(|z-a|/R)+ln|1-z*conj(a)/R²|；源汇差的法向导数在圆周为零。中心大圆极限Rs=(π/ln2)V/I；用Smits1958表及独立边界/互易性检查，不宣称复刻ASTM或厂商内部算法。',
      '每点两接线A=I14/V23、B=I13/V24，各正负电流共4次端点观测。这里明确实现这两种接线与圆片校正，不假称已验证OEM Dual Configuration具体私有处理。',
      '库提供64种真实实验数字化rho(T)，独立插值且限制原论文有效温域；无真实二维map、薄膜厚度或薄膜尺寸效应数据。rho由bulk方向参考转换为薄膜是明确假设。',
      '空间梯度/径向项仅局部均匀近似：以测点附近Rs标量缩放均匀圆盘Green，不是解非均匀电导全域PDE，输出地图标synthetic spatial assumption。',
      '反演仅使用观察到的±I/±V及几何/外部校正，先得到每配置Rs再平均；提供额外厚度先验才计算rho=Rs*t_prior。不同时从同一VI反演未知rho和未知厚度。',
      'AutoRange只在每幅图的第一个接受测点按实际正反试测逐档寻找电流；全部后续site固定同一选定电流，不每点重新量程。自定义地图最多14000点，不采用另一页5至1400摘要限制；冲突在source_evidence。',
      '4相每相至少0.3s，总电测驻留至少1.2s/点，外加真实抬针、XY、下针和settling；首点每个AutoRange档还要实际1.2s。不能用host大dt把这些时长省略。',
      'SP4换针使校准失效。calibration.start用宿主装入的薄膜标样和单独输入参考Rs，实际四相每相至少45s、合计180s，再加移动/量程；增益仅由外部参考/观测比值得出。不把厂家VLSI508µm硅标准硬套本薄膜模型，也不冒充实际NIST证书。',
      'Keithley2450源电流档10nA至1A，每档源分辨率FS*5e-5、观测电流分辨率FS*1e-6；电压观测档20mV至200V，分辨率FS*5e-7。工程增益/偏置/τ不代表厂家精度或噪声分布。10/100nA档明确要求rear triax配置。',
      '源量程与电压观测档分离；源compliance≤20V档可至1A，>20V至200V档最多100mA，并同时≤20W；compliance至少源电压档10%；达到compliance/功率限制时实际I下降，电能依实际端压/电流累计并标观测失败，不能组合1A和200V。未利用105%过量程能力。',
      'Signatone D系统原表10µΩ至100MΩ只写Ω，未明确Ω/sq；本模型不把它偷换成面电阻硬范围。输入标样1e-12至1e15Ω/sq仅工程数值域，反演要求正有限，实际读数可解析性由2450电流/电压档、分辨率和compliance限制。',
      '2450到额定精度需真实3600s连续上电，warmup状态真实累计。默认require_warmup=False为工程估算，设True会在3600s前拒绝测量/校准；任何模式均不承诺OEM额定精度或系统<1%。',
      '本几何工程XY行程±130mm；300mm片的圆形采区须至少20mm边缘排除，属于当前结构代理的安全域而非厂家限制。所有四针接点还须在边界内；被排除点有原因，不伪装为实际测量。',
      '机械stage实际位移=-测点坐标，固定世界探针落到wafer指定点；针抬起后才能XY，暗箱合上测量。取消/断电在当前姿态停止，不瞬移归零；仅已完成点输出，热量和质量留账。',
      '膜与基底共被动集中热节点，局部附加热容由宿主输入（默认0.1J/K，非整机218kg或夹具质量热容）。源电能=样品焦耳热+接触热外流；载入/移出两种材料质量体积和当前显热明确守恒。',
      '所有Session公开result/poll均隐藏所选参考身份，内部evaluator转发同样脱敏；privileged provenance.read单独访问。共享Python/磁盘不是OS隔离。',
      '真实仪器、机器人装样、TCR温控、真实标样证书/漂移/OEM准确度、完整非均匀PDE和厚晶片计量、急停硬件电路not_run。']
    for k,v in CAPS.items():
        un=k in UNSUPPORTED;async_=k in ('acquisition.start','calibration.start');caps[k]={key:copy.deepcopy(v[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']}
        caps[k].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ('scene','robot','hardware') else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.signatone_quadpro2.SignatoneQuadpro2Session.invoke',invocation=f'session.invoke(instance_id="quadpro2_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),preconditions=[] if un or v['kind'] in ('query','analysis') else [dict(description_zh='运行入口原子核对薄层/参考温域、选型、暗箱/针尖/移动安全域、量程、校准与可选预热。',state_ids=['power.enabled','sample.present','probe.position_m','hood.angle_rad','calibration.valid','source.warmup_ready','acquisition.phase'],enforced_by='runtime',check='SignatoneQuadpro2Runtime.invoke guards before commit')],state_effects=[dict(state_id=s,timing='immediate',description_zh='命令实际即时状态变化') for s in v['effects']],observes=[],model_effects=dict(mode='none',description_zh='仅输出独立可绑定状态；真实姿态/显示联动由场景报告取证。',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='真实>=180s校准4相及抬针完成' if k=='calibration.start' else '所有接受点依真实运动/等待/首点量程/四相测量后抬针完成' if async_ else '原子命令；物理推进仅由宿主step',completion_state_ids=['acquisition.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','xy_stage','sp4_probe','keithley2450_source_meter'],partial_failure_zh='取消/断电截断激励并停当前XY/Z，保留已测点、完成的试量程及已积耗散/显热；不能回滚样品质量或已生热。'),errors=[dict(code=e,meaning_zh=e,side_effects='partial' if async_ and e in ('POWER_OFF','USER_CANCEL','SOURCE_COMPLIANCE','VOLTAGE_OVERFLOW','REFERENCE_TEMPERATURE_RANGE','AUTORANGE_UNRESOLVED') else 'none') for e in ERRORS],verification=[],limitations=limits,source_ids=['manufacturer_primary','simulation_assumption','template_library'])
        if k in ('stage.move','probe.set_contact','enclosure.set_open','acquisition.start','calibration.start','power.set'):
            caps[k]['state_effects'] += [dict(state_id=s,timing='on_step',description_zh='宿主推进有限运动、真实等待、热量、预热与逐点观测') for s in SLOTS if s not in v['effects'] and s not in ('plant.configured','wafer.configured','probe.configured','sample.present','power.enabled')]
    mechanism=dict(component_ids=['controller','source_meter','stage_x','sample_stage','probe_head','sample_wafer','hood'],state_ids=list(slots),meaning_zh='有限圆片薄膜四探针Green几何、首点固定图量程、实际标样反演校准、双接线正反测绘与被动电热质量账',implementation='lumped',equations=[
      'g(z,a)=ln(|z-a|/R)+ln|1-z*conj(a)/R²|; phi(z)=-(Rs*I/(2*pi))*(g(z,a_source)-g(z,a_sink)). For a source/sink pair the circular boundary has zero normal current. V=phi(c)-phi(d).',
      'For center collinear equally-spaced probes and R/s→infinity, K=ln(2)/pi and Rs=(pi/ln2)*V/I. Finite disk K is evaluated per site/configuration, not an arbitrary edge multiplier.',
      'Configurations A=(source1,sink4,V2-V3),B=(source1,sink3,V2-V4). Rtransfer=(Vpositive-Vnegative)/(Ipositive-Inegative); Rs_config=calibration_gain*Rtransfer/K_config; Rs is the mean of two configurations.',
      'rho_output=Rs*thickness_prior only when an independent thickness prior is supplied; no thickness is inferred from a single four-probe resistance experiment.',
      'Rs_forward(T,x,y)=rho_reference(T)/t_film * [1+gx*x/R+gy*y/R+gr*(x²+y²)/R²]. This is an explicitly local homogeneous approximation to a synthetic spatial profile, not a global nonuniform-conductivity PDE solve.',
      'Rdrive regularizes the same Green source potentials at a declared finite current-contact radius. Iactual=sign(Irequested)*min(abs(Irequested),Vcompliance/(Rdrive+Rcontact),sqrt(20W/(Rdrive+Rcontact)),Isource_range_max), where range max is1A for compliance<=20V and0.1A for>20V through200V; measurement voltage range is independent.',
      'Psource=Iactual²*(Rdrive+Rcontact); Psample=Iactual²*Rdrive; Pcontact_export=Iactual²*Rcontact. Ctotal*dT/dt=Psample-G*(T-Ta); Ctotal=Clocal+mfilm*Cpfilm+msubstrate*Cpsubstrate.',
      'Detector dz/dt=(gain*V+offset-z)/tau, observed voltage/current follow declared2450 resolution steps; source resolutionFS*5e-5, measured IFS*1e-6 and measured VFS*5e-7.',
      'Motion axes advance with explicit finite speed. Stage_xy=-sample_point_xy. Each point spends4*dwell>=1.2s measuring plus motion/settling; initial auto-range trials each use1.2s before locking current for entire map.',
      'Calibration waits4*max(dwell,45s)>=180s and obtains gain=external_standard_Rs/observed_uncorrected_Rs. No hidden gain or reference material property is read by inverse.',
      'Only host step advances axes, powerup3600s warmup clock, sequence events and electrothermal ODE. Fractional nanoseconds carry across normal60Hz floating timesteps. Samples carry independently ledgered sensible heat when loaded or removed.'],parameters={},source_ids=['simulation_assumption','template_library'],limitations=limits)
    spec=importlib.util.spec_from_file_location('_quadpro_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()});ann.annotate_export(SLUG,caps,slots,{'four_probe_mapping':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'wafer.configure':(WAFER,WAFER_EXAMPLE),'probe.configure':(PROBE,PROBE_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'standard.load':(STANDARD,STANDARD_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'map.configure':(MAP,MAP_EXAMPLE)})
    for key,value in SLOTS.items():slots[key]['meaning_zh']=value['meaning']
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'four_probe_mapping':mechanism})

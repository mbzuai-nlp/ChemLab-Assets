"""Lake Shore8604, room-temperature86-LC/standard poles/ExactGAP2.
SI magnetic moment A m2 (1 emu=1e-3 A m2). Rate-limited external B,
explicit interval-mean lock-in observations, synthetic Preisach memory when
requested, and separately conserved electrical/magnetic/passive-thermal energy.
"""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
import numpy as np
PROTOCOL_VERSION='1.0.0';SLUG='lakeshore_8604vsm';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-143'
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch04/t1_thermal'/SLUG;LIBRARY_PATH=ASSET/'magnetic_library.json'
MU0=4e-7*math.pi;MAX_FIELD_T=2.37;FIELD_RESOLUTION_T=1e-7;MIN_MOMENT_A_M2=2.5e-11;MAX_MOMENT_A_M2=1.
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


def library():return {x['id']:x for x in json.loads(LIBRARY_PATH.read_text())['templates']}
def quant(value,resolution):return float(round(value/resolution)*resolution)
def weighted_exponential_integrals(dt,tau):
    x=dt/tau
    if x<.5:
        out=[]
        for n in range(3):
            term=1/(n+1);total=term
            for k in range(1,30):term*=-x/(n+k+1);total+=term
            out.append(dt**(n+1)*total)
        return out
    j0=-tau*math.expm1(-x);j1=tau*dt-tau*j0;return j0,j1,tau*dt*dt-2*tau*j1

def infer_moment(voltage_v,sensitivity_v_per_a_m2,calibration_gain,calibration_offset_v):
    if sensitivity_v_per_a_m2<=0 or calibration_gain<=0:raise ActionRejected('CALIBRATION_INVALID')
    return (voltage_v-calibration_offset_v)*calibration_gain/sensitivity_v_per_a_m2

def analyze_loop(rows):
    if len(rows)<3 or any(x['moment_a_m2'] is None for x in rows):raise ActionRejected('INVALID_OBSERVATIONS')
    crossings=[];remanence=[]
    for a,b in zip(rows,rows[1:]):
        b0,b1=a['field_t'],b['field_t'];m0,m1=a['moment_a_m2'],b['moment_a_m2'];direction='ascending' if b1>b0 else 'descending'
        if b0==b1:continue
        if m0*m1<=0 and m0!=m1:
            value=b0-m0*(b1-b0)/(m1-m0);crossings.append(dict(field_t=value,external_h_a_m=value/MU0,direction=direction))
        if b0*b1<=0:
            value=m0-b0*(m1-m0)/(b1-b0);remanence.append(dict(moment_a_m2=value,direction=direction))
    area=float(sum((a['field_t']+b['field_t'])*.5*(b['moment_a_m2']-a['moment_a_m2']) for a,b in zip(rows,rows[1:])))
    return dict(coercive_crossings=crossings,remanence_estimates=remanence,observed_b_dm_j=area,evidence='external_observations_linear_zero_crossings; applied_B_over_mu0_is_external_H_not_demagnetization_corrected_internal_H; open_path_integral_not_necessarily_cycle_loss')

def analyze_forc(rows):
    valid=[r for r in rows if r['reversal_field_t'] is not None]
    if len(valid)<6 or any(r['moment_a_m2'] is None for r in valid):raise ActionRejected('INVALID_OBSERVATIONS')
    x=np.array([[r['field_t'],r['reversal_field_t']] for r in valid]);y=np.array([r['moment_a_m2'] for r in valid]);scale=float(np.ptp(x,axis=0).max())
    if scale<=0 or len(set(x[:,1]))<3:raise ActionRejected('FORC_GRID_REQUIRED')
    out=[]
    for i,point in enumerate(x):
        ids=np.argsort(np.linalg.norm(x-point,axis=1))[:min(15,len(x))];q=(x[ids]-point)/scale;A=np.column_stack([np.ones(len(q)),q[:,0],q[:,1],q[:,0]**2,q[:,0]*q[:,1],q[:,1]**2])
        if np.linalg.matrix_rank(A)<6:continue
        c=np.linalg.lstsq(A,y[ids],rcond=None)[0];out.append(dict(field_t=float(point[0]),reversal_field_t=float(point[1]),coercive_coordinate_t=float((point[0]-point[1])/2),interaction_coordinate_t=float((point[0]+point[1])/2),density_a_m2_per_t2=float(-.5*c[4]/scale**2),fit_residual_a_m2=float(np.sqrt(np.mean((A@c-y[ids])**2))),neighbours=len(ids)))
    if not out:raise ActionRejected('FORC_GRID_REQUIRED')
    return dict(distribution=out,evidence='local_quadratic_fit_to_external_FORC_observations; rho=-0.5*d2m/(dB*dBr); SI_A_m2_per_T2; not_unique_material_microstructure_or_FORCinel_reproduction')

PLANT_EXAMPLE=dict(ambient_k=300.,field_per_current_t_a=.04,coil_inductance_h=.05,coil_resistance_ohm=.25,magnet_thermal_capacity_j_k=5000.,magnet_cooling_conductance_w_k=50.,sample_local_capacity_j_k=.01,sample_conductance_w_k=.01,pickup_coupling_wb_per_a_m3=.1,voltage_gain=1.03,voltage_offset_v=2e-7,detector_tau_s=.01,lift_speed_m_s=.1,rotation_speed_rad_s=1.,drawer_speed_m_s=.1,accept_engineering=True)
PLANT=obj({k:(BOOL if type(v) is bool else number({'ambient_k':'K','field_per_current_t_a':'T/A','coil_inductance_h':'H','coil_resistance_ohm':'ohm','magnet_thermal_capacity_j_k':'J/K','magnet_cooling_conductance_w_k':'W/K','sample_local_capacity_j_k':'J/K','sample_conductance_w_k':'W/K','pickup_coupling_wb_per_a_m3':'Wb/(A*m^3)','voltage_gain':'1','voltage_offset_v':'V','detector_tau_s':'s','lift_speed_m_s':'m/s','rotation_speed_rad_s':'rad/s','drawer_speed_m_s':'m/s'}[k], -1. if k=='voltage_offset_v' else 1e-12)) for k,v in PLANT_EXAMPLE.items()})
PLANT['properties']['ambient_k']=number('K',285,303)
VIBRATION_EXAMPLE=dict(enabled=True,amplitude_m=.0005,frequency_hz=82.)
VIBRATION=obj(dict(enabled=BOOL,amplitude_m=number('m',1e-6,.001),frequency_hz=number('Hz',1,200)))
COIL_EXAMPLE=dict(nominal_coupling_wb_per_a_m3=.1,moment_range_a_m2=.01)
COIL=obj(dict(nominal_coupling_wb_per_a_m3=number('Wb/(A*m^3)',1e-10,1e6),moment_range_a_m2=number('A*m^2',MIN_MOMENT_A_M2,MAX_MOMENT_A_M2)))
RELAY=obj(dict(coercive_field_t=number('T',1e-8,10),bias_field_t=number('T',-10,10),weight=number('1',1e-12,1),initial_sign={'type':'integer','enum':[-1,1],'x-unit':'1'}))
RELAYS_EXAMPLE=[dict(coercive_field_t=v,bias_field_t=0.,weight=1/5,initial_sign=-1) for v in [.02,.035,.05,.065,.08]]
SAMPLE_EXAMPLE=dict(reference_id='kuleuven_cf_300k',mass_kg=5e-5,volume_m3=math.pi*(.001)**2*.002,specific_heat_capacity_j_kg_k=650.,initial_temperature_k=300.,temperature_tolerance_k=.5,linear_valid_max_field_t=.6,magnetic_model='preisach_proxy',proxy_specific_moment_a_m2_kg=None,relays=RELAYS_EXAMPLE,accept_engineering_assumptions=True)
SAMPLE=obj(dict(reference_id=TEXT,mass_kg=number('kg',0,.01),volume_m3=number('m^3',1e-15,1e-5),specific_heat_capacity_j_kg_k=number('J/(kg*K)',1,1e5),initial_temperature_k=number('K',285,303),temperature_tolerance_k=number('K',0,5),linear_valid_max_field_t=number('T',1e-7,MAX_FIELD_T),magnetic_model=enum(['linear_susceptibility','preisach_proxy']),proxy_specific_moment_a_m2_kg=nullable(number('A*m^2/kg',0,1e5)),relays=array(RELAY,1,1000),accept_engineering_assumptions=BOOL))
STANDARD_EXAMPLE=dict(mass_kg=.00006316,volume_m3=math.pi/6*.002383**3,specific_heat_capacity_j_kg_k=440.,initial_temperature_k=298.,reference_field_t=MU0*398000.,reference_moment_a_m2=.00347,accept_host_standard_proxy=True)
STANDARD=obj(dict(mass_kg=number('kg',0,.01),volume_m3=number('m^3',1e-15,1e-5),specific_heat_capacity_j_kg_k=number('J/(kg*K)',1,1e5),initial_temperature_k=number('K',285,303),reference_field_t=number('T',1e-7,MAX_FIELD_T),reference_moment_a_m2=number('A*m^2',MIN_MOMENT_A_M2,MAX_MOMENT_A_M2),accept_host_standard_proxy=BOOL))
METHOD_EXAMPLE=dict(field_points_t=[.2,.1,.075,.05,.025,0.,-.025,-.05,-.075,-.1,-.2,-.1,-.075,-.05,-.025,0.,.025,.05,.075,.1,.2],ramp_rate_t_s=1.,averaging_time_s=.05,settling_time_s=.05,require_calibration=False)
METHOD=obj(dict(field_points_t=array(number('T',-MAX_FIELD_T,MAX_FIELD_T),1,50000),ramp_rate_t_s=number('T/s',1e-6,1),averaging_time_s=number('s',.01,3600),settling_time_s=number('s',0,3600),require_calibration=BOOL))
FORC_EXAMPLE=dict(saturation_field_t=.2,reversal_fields_t=[-.12,-.08,-.04,0.],points_per_curve=9,ramp_rate_t_s=1.,averaging_time_s=.05,settling_time_s=.05,require_calibration=False)
FORC=obj(dict(saturation_field_t=number('T',1e-7,MAX_FIELD_T),reversal_fields_t=array(number('T',-MAX_FIELD_T,MAX_FIELD_T),3,1000),points_per_curve={'type':'integer','minimum':3,'maximum':1000,'x-unit':'1'},ramp_rate_t_s=number('T/s',1e-6,1),averaging_time_s=number('s',.01,3600),settling_time_s=number('s',0,3600),require_calibration=BOOL))
ROW=obj(dict(point_index={'type':'integer','x-unit':'1'},field_t=number('T'),external_h_a_m=number('A/m'),in_phase_voltage_v=number('V'),moment_a_m2=nullable(number('A*m^2')),angle_rad=number('rad'),window_start_s=number('s'),window_end_s=number('s'),reversal_field_t=nullable(number('T')),quality=enum(['estimated','below_documented_dynamic_range','overflow','reference_temperature_invalid'])))
RESULT=obj(dict(operation_id=nullable(TEXT),phase=PHASE,kind=TEXT,elapsed_s=number('s'),rows=array(ROW),error=nullable(TEXT),template_source=nullable(TEXT),evidence=TEXT))
LEDGER_KEYS=['loaded_mass_kg','removed_mass_kg','mass_kg','mass_residual_kg','loaded_volume_m3','removed_volume_m3','volume_m3','volume_residual_m3','source_energy_j','coil_joule_j','magnetic_work_j','hysteresis_heat_j','sample_sensible_loaded_j','sample_sensible_removed_j','magnetic_loaded_j','magnetic_removed_j','coil_stored_j','magnetic_stored_j','sample_thermal_j','magnet_thermal_j','sample_heat_export_j','magnet_heat_export_j','energy_residual_j']
LEDGER=obj({k:number('kg' if k.endswith('_kg') else 'm^3' if k.endswith('_m3') else 'J') for k in LEDGER_KEYS})
CROSS=obj(dict(field_t=number('T'),external_h_a_m=number('A/m'),direction=enum(['ascending','descending'])))
LOOP_OUTPUT=obj(dict(coercive_crossings=array(CROSS),remanence_estimates=array(obj(dict(moment_a_m2=number('A*m^2'),direction=enum(['ascending','descending'])))),observed_b_dm_j=number('J'),evidence=TEXT))
FORC_OUTPUT=obj(dict(distribution=array(obj(dict(field_t=number('T'),reversal_field_t=number('T'),coercive_coordinate_t=number('T'),interaction_coordinate_t=number('T'),density_a_m2_per_t2=number('A*m^2/T^2'),fit_residual_a_m2=number('A*m^2'),neighbours={'type':'integer','x-unit':'1'}))),evidence=TEXT))
CAPS={}
def cap(k,s,o,example,component,meaning,visibility='agent',kind='device_command'):CAPS[k]=dict(input_schema=s,output_schema=o,example_arguments=example,component_ids=[component],function_zh=meaning,visibility=visibility,kind=kind,effects=[])
for key,schema,example,component in [('plant.configure',PLANT,PLANT_EXAMPLE,'magnet'),('coil.configure',COIL,COIL_EXAMPLE,'controller'),('method.configure',METHOD,METHOD_EXAMPLE,'controller'),('forc.configure',FORC,FORC_EXAMPLE,'controller')]:cap(key,schema,obj(dict(configured=BOOL)),example,component,'设置明确工程参数或采集方法；不推进时间','evaluator' if key=='plant.configure' else 'agent','configuration')
cap('sample.load',SAMPLE,obj(dict(present=BOOL)),SAMPLE_EXAMPLE,'sample','载入真实磁性参考与显式磁滞/热假设','evaluator')
cap('standard.load',STANDARD,obj(dict(present=BOOL)),STANDARD_EXAMPLE,'sample','宿主装入已知磁矩标准代理；真实标准证书并非对本仿真认证','evaluator')
cap('sample.remove',obj({}),obj(dict(removed_mass_kg=number('kg'),removed_volume_m3=number('m^3'))),{},'sample','取走磁性库存、质量和当前显热','evaluator')
cap('power.set',obj(dict(enabled=BOOL)),obj(dict(enabled=BOOL)),{'enabled':True},'power_switch','控制电源；非零场拒绝断电以免瞬间销毁储能')
cap('field.set_target',obj(dict(field_t=number('T',-MAX_FIELD_T,MAX_FIELD_T),ramp_rate_t_s=number('T/s',1e-6,1))),obj(dict(target_t=number('T'))),dict(field_t=.1,ramp_rate_t_s=1.),'power_supply','只写量化目标，B由step以有限速率推进')
cap('sample.set_lift',obj(dict(lift_m=number('m',0,.35))),obj(dict(target_m=number('m'))),dict(lift_m=0.),'head_lift','电动测量头有限速率升降，需零场/振动关闭')
cap('sample.set_angle',obj(dict(angle_rad=number('rad',-1e6,1e6))),obj(dict(target_rad=number('rad'))),dict(angle_rad=math.pi/2),'sample_rotor','零外场、关闭振动后以0.1度设置分辨有限旋转')
cap('storage.set_open',obj(dict(opened=BOOL)),obj(dict(target_m=number('m'))),dict(opened=True),'storage_drawer','手工抽屉工程有限运动代理','evaluator')
cap('vibration.configure',VIBRATION,obj(dict(enabled=BOOL)),VIBRATION_EXAMPLE,'vibration_carriage','配置振动工程频率幅度；解析采样姿态与锁相包络，非完整波形求解')
cap('calibration.start',obj(dict(reference_moment_a_m2=number('A*m^2',MIN_MOMENT_A_M2,1),field_t=number('T',1e-6,MAX_FIELD_T),averaging_time_s=number('s',.01,3600))),obj(dict(operation_id=TEXT)),dict(reference_moment_a_m2=.00347,field_t=MU0*398000.,averaging_time_s=1.),'controller','实际正负场积分观测拟合增益/零偏，不读隐藏真实增益')
cap('calibration.clear',obj({}),obj(dict(valid=BOOL)),{},'controller','清除校准')
cap('acquisition.start',obj({}),obj(dict(operation_id=TEXT)),{},'controller','依有限扫场、等待、真实积分采集loop或FORC')
cap('acquisition.stop',obj({}),obj(dict(phase=PHASE,elapsed_s=number('s'))),{},'controller','保留完成读数及磁记忆/热账，停止扫场于实际值')
cap('acquisition.result',obj({}),RESULT,{},'controller','返回实际区间观测及明确合成模型标记','agent','query')
cap('analysis.loop',obj(dict(rows=array(ROW,3))),LOOP_OUTPUT,dict(rows=[]),'controller','从外部曲线估计零场剩磁、零矩外场矫顽与路径功','agent','analysis')
cap('analysis.forc',obj(dict(rows=array(ROW,6))),FORC_OUTPUT,dict(rows=[]),'controller','从外部FORC局部二次拟合混合导数','agent','analysis')
cap('analysis.moment',obj(dict(voltage_v=number('V'),sensitivity_v_per_a_m2=number('V/(A*m^2)',1e-12),calibration_gain=number('1',1e-12),calibration_offset_v=number('V'))),obj(dict(moment_a_m2=number('A*m^2'))),dict(voltage_v=.001,sensitivity_v_per_a_m2=1.,calibration_gain=1.,calibration_offset_v=0.),'controller','仅按外部电压/校准/灵敏度反演磁矩','agent','analysis')
cap('provenance.read',obj({}),obj(dict(current_sample=nullable(TEXT),operation_source=nullable(TEXT))),{},'controller','选中来源与测量来源快照仅限宿主','evaluator','query')
cap('ledger.read',obj({}),LEDGER,{},'controller','受限样品库存及有范围的电磁热能账','evaluator','query')
cap('sim.step',obj(dict(dt_s=number('s',0,86400))),obj(dict(time_s=number('s'))),dict(dt_s=.02),'controller','唯一宿主时钟','evaluator','simulation')
UNSUPPORTED={'hardware.connect':'没有真实驱动','robot.load':'没有机器人装样','thermal.set_temperature':'未选温控附件，不能给基础仪器假加热器','magnet.change_gap':'固定标准极帽Gap2，无在线极距/高场帽切换','measurement.vector':'未选矢量线圈','noise.empirical':'无独立实测噪声模型，不从15nemu规格虚构分布','accuracy.certification':'未验证厂家条件准确度','analysis.unique_forc_from_major_loop':'majorloop不能唯一决定FORC或微结构'}
for k,v in UNSUPPORTED.items():cap(k,obj({}),obj({}),{},'controller',v)
SLOTS={}
def slot(k,s,unit,origin,component,meaning,visibility='agent'):SLOTS[k]=dict(schema=s,unit=unit,origin=origin,component=component,meaning=meaning,visibility=visibility,write=None)
slot('clock.time_s',number('s'),'s','derived','controller','宿主统一仿真时钟，read不推进')
for k in ['plant.configured','coil.configured','method.configured','sample.present','power.enabled','vibration.enabled','calibration.valid']:slot(k,BOOL,'1','setpoint','controller','已配置/存在/启用状态；不是完整计量认证')
for k in ['sample.lift_m','sample.target_lift_m','storage.position_m','storage.target_m']:slot(k,number('m'),'m','scene' if 'target' not in k else 'setpoint','head_lift' if k.startswith('sample') else 'storage_drawer','实际有限运动位置或目标，单位m；只由宿主step推进')
for k in ['sample.angle_rad','sample.target_angle_rad','sample.orientation_rad']:slot(k,number('rad'),'rad','scene' if 'target' not in k else 'setpoint','sample_rotor','无界累计转角/目标及周期等价场景姿态；不是磁性各向异性实测参数')
slot('sample.vibration_z_m',number('m'),'m','scene','vibration_carriage','工程正弦振动解析时刻姿态；场景采样可混叠，不声称解析全频运动')
slot('power.angle_rad',number('rad'),'rad','scene','power_switch','开关显示姿态代理')
for k in ['field.target_t','field.indicated_t']:slot(k,nullable(number('T')) if 'indicated' in k else number('T'),'T','sensor' if 'indicated' in k else 'setpoint','hall_probe','Gap2外施空气场B或量化目标；100nT分辨；不是样品内部B')
slot('field.external_h_a_m',nullable(number('A/m')),'A/m','derived','hall_probe','外场B/μ0，未做样品退磁场修正')
slot('moment.indicated_a_m2',nullable(number('A*m^2')),'A*m^2','sensor','controller','锁相包络估算磁矩；1emu=1e-3 A m²，掉电/未开振动不可用')
slot('detector.in_phase_v',nullable(number('V')),'V','sensor','controller','有符号锁相同相电压包络含工程一阶响应，不是负RMS幅值')
for k in ['thermal.sample_k','thermal.magnet_k']:slot(k,nullable(number('K')),'K','sensor','sample' if 'sample' in k else 'magnet','模型被动集中热节点；不是温控附件或整机热容')
slot('acquisition.phase',PHASE,'1','derived','controller','异步操作终态或运行态')
slot('acquisition.stage',TEXT,'1','derived','controller','ramping/settling/averaging，均等待真实host时间')
slot('acquisition.elapsed_s',number('s'),'s','derived','controller','操作实际已耗时间')
slot('acquisition.points',{'type':'integer','x-unit':'1'},'1','derived','controller','已完成积分观测数')
slot('calibration.gain',number(),'1','derived','controller','外部±已知磁矩与实际观测拟合出的乘性校正')
slot('calibration.offset_v',number('V'),'V','derived','controller','外部双极标准观测拟合出的电压零偏')
for k,u in [('truth.field_t','T'),('truth.moment_a_m2','A*m^2'),('truth.mass_kg','kg'),('truth.sample_k','K')]:slot(k,number(u),u,'process_truth','sample','受限过程真值，公共结果不透出材料身份','evaluator')

class LakeShore8604VsmRuntime:
    def __init__(self,instance_id='vsm_01'):
        self.instance_id=instance_id;self.time_ns=0;self._clock_remainder_ns=0.;self.revision=0;self.power=False;self.plant=None;self.coil=None;self.method=None;self.sample=None;self.record=None;self.source=None
        self.axes={k:dict(position=v,target=v,start=v,start_ns=0,arrival_ns=0) for k,v in [('field',0.),('lift',.35),('angle',0.),('drawer',0.)]};self.ramp_rate=1.
        self.vibration=dict(enabled=False,amplitude_m=.0005,frequency_hz=82.);self.sensor=0.;self.sample_k=300.;self.magnet_k=300.;self.linear_coefficient=0.;self.relays=[];self.amplitudes=[]
        self.calibration_valid=False;self.calibration_gain=1.;self.calibration_offset=0.;self.phase='idle';self.stage='idle';self.elapsed=0.;self.rows=[];self.operation_id=None;self.operation_kind='none';self.error=None;self._counter=0;self._operation_source=None;self.records={};self._principals={};self._requests={};self._owners={};self._cancel_operations={};self._event_ns=None;self._sequence=[];self._index=0;self._v_integral=0.;self._b_integral=0.
        self.book={k:0. for k in ['loaded_mass_kg','removed_mass_kg','loaded_volume_m3','removed_volume_m3','source_energy_j','coil_joule_j','magnetic_work_j','hysteresis_heat_j','sample_sensible_loaded_j','sample_sensible_removed_j','magnetic_loaded_j','magnetic_removed_j','sample_heat_export_j','magnet_heat_export_j']}
    @property
    def time_s(self):return self.time_ns/1e9
    def register_principal(self,name,visibility):
        if not isinstance(name,str) or not name or visibility not in ('agent','evaluator','debug') or name in self._principals:raise ActionRejected('INVALID_ARGUMENT')
        self._principals[name]=visibility
    def session(self,name):
        if name not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return LakeShore8604VsmSession(self,name)
    def _pos(self,k):return self.axes[k]['position']
    def _moving(self,k):return self.time_ns<self.axes[k]['arrival_ns']
    def _speed(self,k):return self.ramp_rate if k=='field' else self.plant[{'lift':'lift_speed_m_s','angle':'rotation_speed_rad_s','drawer':'drawer_speed_m_s'}[k]]
    def _target(self,k,value):
        a=self.axes[k];distance=abs(value-a['position']);a.update(start=a['position'],target=float(value),start_ns=self.time_ns,arrival_ns=self.time_ns+int(math.ceil(distance/self._speed(k)*1e9)))
    def _rate(self,k):
        a=self.axes[k]
        return (a['target']-a['start'])/((a['arrival_ns']-a['start_ns'])/1e9) if self._moving(k) else 0.
    def _positions(self):
        for a in self.axes.values():a['position']=a['target'] if self.time_ns>=a['arrival_ns'] else a['start']+(a['target']-a['start'])*(self.time_ns-a['start_ns'])/(a['arrival_ns']-a['start_ns'])
    def _capacity(self):return self.plant['sample_local_capacity_j_k']+(self.sample['mass_kg']*self.sample['specific_heat_capacity_j_kg_k'] if self.sample else 0.)
    def _sensitivity(self,actual=False):
        if not self.coil:return 0.
        coupling=self.plant['pickup_coupling_wb_per_a_m3'] if actual else self.coil['nominal_coupling_wb_per_a_m3']
        return coupling*self.vibration['amplitude_m']*2*math.pi*self.vibration['frequency_hz']/math.sqrt(2)
    def _moment(self,B=None):
        B=self._pos('field') if B is None else B;c=math.cos(self._pos('angle'))
        return self.linear_coefficient*B+sum(a*r['state'] for a,r in zip(self.amplitudes,self.relays))*c
    def _magnetic_energy(self):return .5*self.linear_coefficient*self._pos('field')**2+sum(a*r['bias_field_t']*r['state'] for a,r in zip(self.amplitudes,self.relays))
    def _temperature_valid(self):
        if not self.sample or self.sample.get('standard'):return True
        nominal=self.record.get('reference_temperature_K');interval=self.record.get('valid_temperature_range_K')
        if nominal is not None and abs(self.sample_k-nominal)>self.sample['temperature_tolerance_k']+1e-9:return False
        if interval and interval[1]-interval[0]>1e-9 and not interval[0]-1e-9<=self.sample_k<=interval[1]+1e-9:return False
        return abs(self.sample_k-self.sample['initial_temperature_k'])<=self.sample['temperature_tolerance_k']+1e-9
    def ledger(self):
        d=copy.deepcopy(self.book);mass=self.sample['mass_kg'] if self.sample else 0.;volume=self.sample['volume_m3'] if self.sample else 0.;d.update(mass_kg=mass,mass_residual_kg=d['loaded_mass_kg']-d['removed_mass_kg']-mass,volume_m3=volume,volume_residual_m3=d['loaded_volume_m3']-d['removed_volume_m3']-volume,coil_stored_j=0.,magnetic_stored_j=self._magnetic_energy(),sample_thermal_j=0.,magnet_thermal_j=0.)
        if self.plant:d.update(coil_stored_j=.5*self.plant['coil_inductance_h']*(self._pos('field')/self.plant['field_per_current_t_a'])**2,sample_thermal_j=self._capacity()*(self.sample_k-self.plant['ambient_k']),magnet_thermal_j=self.plant['magnet_thermal_capacity_j_k']*(self.magnet_k-self.plant['ambient_k']))
        d['energy_residual_j']=d['source_energy_j']+d['sample_sensible_loaded_j']+d['magnetic_loaded_j']-d['sample_sensible_removed_j']-d['magnetic_removed_j']-sum(d[k] for k in ['coil_stored_j','magnetic_stored_j','sample_thermal_j','magnet_thermal_j','sample_heat_export_j','magnet_heat_export_j']);return d
    def read(self,visibility='agent'):
        indicated=None
        if self.power and self.vibration['enabled'] and self.coil:
            inferred=infer_moment(self.sensor,self._sensitivity(),self.calibration_gain,self.calibration_offset)
            if abs(inferred)<=self.coil['moment_range_a_m2'] and self._temperature_valid():indicated=inferred
        field=quant(self._pos('field'),FIELD_RESOLUTION_T) if self.power else None
        d={'clock.time_s':self.time_s,'plant.configured':self.plant is not None,'coil.configured':self.coil is not None,'method.configured':self.method is not None,'sample.present':self.sample is not None,'power.enabled':self.power,'vibration.enabled':self.vibration['enabled'],'calibration.valid':self.calibration_valid,'sample.lift_m':self._pos('lift'),'sample.target_lift_m':self.axes['lift']['target'],'storage.position_m':self._pos('drawer'),'storage.target_m':self.axes['drawer']['target'],'sample.angle_rad':self._pos('angle'),'sample.target_angle_rad':self.axes['angle']['target'],'sample.orientation_rad':math.atan2(math.sin(self._pos('angle')),math.cos(self._pos('angle'))),'sample.vibration_z_m':self.vibration['amplitude_m']*math.sin(2*math.pi*self.vibration['frequency_hz']*self.time_s) if self.power and self.vibration['enabled'] else 0.,'power.angle_rad':.28 if self.power else 0.,'field.target_t':self.axes['field']['target'],'field.indicated_t':field,'field.external_h_a_m':None if field is None else field/MU0,'moment.indicated_a_m2':indicated,'detector.in_phase_v':self.sensor if self.power and self.vibration['enabled'] else None,'thermal.sample_k':self.sample_k if self.plant else None,'thermal.magnet_k':self.magnet_k if self.plant else None,'acquisition.phase':self.phase,'acquisition.stage':self.stage,'acquisition.elapsed_s':self.elapsed,'acquisition.points':len(self.rows),'calibration.gain':self.calibration_gain,'calibration.offset_v':self.calibration_offset,'truth.field_t':self._pos('field'),'truth.moment_a_m2':self._moment(),'truth.mass_kg':self.sample['mass_kg'] if self.sample else 0.,'truth.sample_k':self.sample_k}
        return {k:copy.deepcopy(v) for k,v in d.items() if visibility!='agent' or SLOTS[k]['visibility']=='agent'}
    def _propagate(self,ns):
        dt=ns/1e9
        if dt<=0 or not self.plant:return
        p=self.plant;B0=self._pos('field');rate=self._rate('field');B1=B0+rate*dt;I0=B0/p['field_per_current_t_a'];I1=B1/p['field_per_current_t_a'];r=p['coil_resistance_ohm']/p['field_per_current_t_a']**2
        joule=r*(B0*B0*dt+B0*rate*dt*dt+rate*rate*dt**3/3);dcoil=.5*p['coil_inductance_h']*(I1*I1-I0*I0);dmag=.5*self.linear_coefficient*(B1*B1-B0*B0)
        self.book['coil_joule_j']+=joule;self.book['magnetic_work_j']+=dmag;self.book['source_energy_j']+=joule+dcoil+dmag
        C=p['magnet_thermal_capacity_j_k'];G=p['magnet_cooling_conductance_w_k'];tau=C/G;before=C*(self.magnet_k-p['ambient_k']);j0,j1,j2=weighted_exponential_integrals(dt,tau);after=before*math.exp(-dt/tau)+r*(B0*B0*j0+2*B0*rate*j1+rate*rate*j2);self.magnet_k=p['ambient_k']+after/C;self.book['magnet_heat_export_j']+=joule-(after-before)
        C=self._capacity();before=C*(self.sample_k-p['ambient_k']);after=before*math.exp(-p['sample_conductance_w_k']*dt/C);self.sample_k=p['ambient_k']+after/C;self.book['sample_heat_export_j']+=before-after
        active=self.power and self.vibration['enabled'];gain=p['voltage_gain']*self._sensitivity(True) if active else 0.;f0=gain*self._moment(B0)+(p['voltage_offset_v'] if active else 0.);f1=gain*self.linear_coefficient*rate;old=self.sensor;tau=p['detector_tau_s'];self.sensor=f0+f1*(dt-tau)+(old-f0+f1*tau)*math.exp(-dt/tau)
        if self.phase=='running' and self.stage=='averaging':self._v_integral+=f0*dt+.5*f1*dt*dt-tau*(self.sensor-old);self._b_integral+=B0*dt+.5*rate*dt*dt
    def _next_switch(self):
        B=self._pos('field');c=math.cos(self._pos('angle'));v=self._rate('field')*c
        if not v:return None
        times=[]
        for r in self.relays:
            if v>0 and r['state']<0:threshold=r['bias_field_t']+r['coercive_field_t']
            elif v<0 and r['state']>0:threshold=r['bias_field_t']-r['coercive_field_t']
            else:continue
            dt=(threshold-B*c)/v
            if dt>=-1e-12:times.append(self.time_ns+max(1,int(math.ceil(dt*1e9-1e-7))))
        return min(times) if times else None
    def _switch(self):
        B=self._pos('field');c=math.cos(self._pos('angle'));effective=B*c
        for amplitude,r in zip(self.amplitudes,self.relays):
            state=1 if effective>=r['bias_field_t']+r['coercive_field_t']-1e-12 else -1 if effective<=r['bias_field_t']-r['coercive_field_t']+1e-12 else r['state']
            if state==r['state']:continue
            dm=amplitude*(state-r['state']);work=effective*dm;stored=r['bias_field_t']*dm;heat=work-stored
            if heat<-1e-12:raise RuntimeError('Nonpassive relay switch')
            r['state']=state;self.book['magnetic_work_j']+=work;self.book['source_energy_j']+=work;self.book['hysteresis_heat_j']+=heat;self.sample_k+=heat/self._capacity()
    def _result(self):
        if self.phase!='running' and self.operation_id in self.records:return copy.deepcopy(self.records[self.operation_id])
        return dict(operation_id=self.operation_id,phase=self.phase,kind=self.operation_kind,elapsed_s=self.elapsed,rows=copy.deepcopy(self.rows),error=self.error,template_source=self._operation_source,evidence='synthetic_forward_model; actual_host_time_interval_observations; measured_reference_anchor_is_not_measured_this_loop_or_FORC')
    def _finish(self,phase,error=None):
        self.phase=phase;self.stage='idle';self.error=error;self._event_ns=None
        if self.operation_id:self.records[self.operation_id]=dict(operation_id=self.operation_id,phase=phase,kind=self.operation_kind,elapsed_s=self.elapsed,rows=copy.deepcopy(self.rows),error=error,template_source=self._operation_source,evidence='synthetic_forward_model; actual_host_time_interval_observations; measured_reference_anchor_is_not_measured_this_loop_or_FORC')
    def _begin_point(self):
        point=self._sequence[self._index];self.ramp_rate=self._job_method['ramp_rate_t_s'];self._target('field',point['field_t']);self.stage='ramping';self._event_ns=self.axes['field']['arrival_ns']
    def _advance(self):
        while self.phase=='running' and self._event_ns<=self.time_ns:
            if self.stage=='ramping':self.stage='settling';self._event_ns=self.time_ns+round(self._job_method['settling_time_s']*1e9)
            elif self.stage=='settling':
                if not self._sequence[self._index]['record']:
                    self._index+=1;self._begin_point();continue
                self.stage='averaging';self._window_start_ns=self.time_ns;self._v_integral=0.;self._b_integral=0.;self._event_ns=self.time_ns+round(self._job_method['averaging_time_s']*1e9)
            else:
                dt=(self.time_ns-self._window_start_ns)/1e9;v=self._v_integral/dt;B=quant(self._b_integral/dt,FIELD_RESOLUTION_T);moment=infer_moment(v,self._sensitivity(),self.calibration_gain,self.calibration_offset);quality='estimated';error=None
                if abs(moment)>self.coil['moment_range_a_m2']:quality='overflow';error='MOMENT_OVERFLOW';moment=None
                elif not self._temperature_valid():quality='reference_temperature_invalid';error='REFERENCE_TEMPERATURE_RANGE';moment=None
                elif abs(moment)<MIN_MOMENT_A_M2:quality='below_documented_dynamic_range'
                self.rows.append(dict(point_index=len(self.rows),field_t=B,external_h_a_m=B/MU0,in_phase_voltage_v=v,moment_a_m2=moment,angle_rad=self._pos('angle'),window_start_s=self._window_start_ns/1e9,window_end_s=self.time_s,reversal_field_t=self._sequence[self._index]['reversal_field_t'],quality=quality))
                if error:self._finish('failed',error);break
                self._index+=1
                if self._index==len(self._sequence):
                    if self.operation_kind=='calibration':
                        vplus,vminus=[r['in_phase_voltage_v'] for r in self.rows];den=vplus-vminus
                        if den<=0:self._finish('failed','CALIBRATION_INVALID');break
                        self.calibration_gain=2*self._cal_reference*self._sensitivity()/den;self.calibration_offset=(vplus+vminus)/2;self.calibration_valid=True
                    self._finish('succeeded');break
                self._begin_point()
    def _load_guard(self):
        if not self.plant:raise ActionRejected('NOT_CONFIGURED')
        if self.sample:raise ActionRejected('SAMPLE_PRESENT')
        if self._pos('lift')<.34 or self._moving('lift') or self._moving('angle') or self.vibration['enabled'] or abs(self._pos('field'))>1e-10 or self._moving('field'):raise ActionRejected('LOADING_POSITION_REQUIRED')
    def _field_guard(self,values):
        if self.sample and not self.sample.get('standard') and any(abs(x)>self.sample['linear_valid_max_field_t']+1e-12 for x in values):raise ActionRejected('HOST_MAGNETIC_VALIDITY_RANGE')
    def invoke(self,capability,**a):
        if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability]['input_schema'],a)
        if capability in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
        if capability=='acquisition.result':return self._result()
        if capability=='ledger.read':return self.ledger()
        if capability=='provenance.read':return dict(current_sample=self.source,operation_source=self._operation_source)
        if capability=='analysis.loop':return analyze_loop(a['rows'])
        if capability=='analysis.forc':return analyze_forc(a['rows'])
        if capability=='analysis.moment':return dict(moment_a_m2=infer_moment(**a))
        if capability=='sim.step':self.step(a['dt_s']);return dict(time_s=self.time_s)
        if self.phase=='running' and capability not in ('acquisition.stop','power.set'):raise ActionRejected('BUSY')
        if capability=='plant.configure':
            if self.plant or self.sample or self.power:raise ActionRejected('ALREADY_CONFIGURED')
            if not a['accept_engineering']:raise ActionRejected('EXPLICIT_ASSUMPTIONS_REQUIRED')
            I=MAX_FIELD_T/a['field_per_current_t_a'];V=a['coil_resistance_ohm']*I+a['coil_inductance_h']/a['field_per_current_t_a']
            if I>70+1e-12 or V>35+1e-12 or I*V>2450+1e-12:raise ActionRejected('POWER_SUPPLY_ENVELOPE')
            self.plant=copy.deepcopy(a);self.sample_k=self.magnet_k=a['ambient_k'];out=dict(configured=True)
        elif capability=='coil.configure':self.coil=copy.deepcopy(a);self.calibration_valid=False;self.calibration_gain=1.;self.calibration_offset=0.;out=dict(configured=True)
        elif capability in ('method.configure','forc.configure'):
            if capability=='forc.configure':
                quantized=[quant(x,FIELD_RESOLUTION_T) for x in a['reversal_fields_t']];saturation=quant(a['saturation_field_t'],FIELD_RESOLUTION_T)
                if any(x>=saturation for x in quantized) or len(set(quantized))!=len(quantized) or len(quantized)*(a['points_per_curve']+1)>50000:raise ActionRejected('FORC_GRID_REQUIRED')
                a=copy.deepcopy(a);a['reversal_fields_t']=quantized;a['saturation_field_t']=saturation
            self.method=copy.deepcopy(a);self.method['kind']='forc' if capability=='forc.configure' else 'points';out=dict(configured=True)
        elif capability in ('sample.load','standard.load'):
            self._load_guard();standard=capability=='standard.load';record=None;relays=[];amplitudes=[];linear=0.
            if standard:
                if not a['accept_host_standard_proxy']:raise ActionRejected('EXPLICIT_ASSUMPTIONS_REQUIRED')
                linear=a['reference_moment_a_m2']/a['reference_field_t'];source=json.dumps(dict(kind='host_linear_calibrant_proxy',numerical_example_anchor='NIST772a298K398kA_m_not_certificate_validation'),sort_keys=True)
            else:
                if not a['accept_engineering_assumptions']:raise ActionRejected('EXPLICIT_ASSUMPTIONS_REQUIRED')
                record=library().get(a['reference_id'])
                if record is None:raise ActionRejected('UNKNOWN_REFERENCE')
                temp=record.get('reference_temperature_K');domain=record.get('valid_temperature_range_K')
                if temp is not None and abs(a['initial_temperature_k']-temp)>a['temperature_tolerance_k'] or domain and domain[1]-domain[0]>1e-9 and not domain[0]<=a['initial_temperature_k']<=domain[1]:raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
                chi=record.get('molar_susceptibility_m3_mol');molar_mass=record.get('molar_mass_kg_mol')
                if chi is not None and molar_mass is not None:linear=a['mass_kg']/molar_mass*chi/MU0
                if a['magnetic_model']=='linear_susceptibility' and (chi is None or molar_mass is None):raise ActionRejected('REFERENCE_PROPERTY_MISSING')
                if a['magnetic_model']=='preisach_proxy':
                    value=a['proxy_specific_moment_a_m2_kg']
                    if value is None:value=record.get('measured_specific_moment_max_abs_a_m2_kg')
                    if value is None:raise ActionRejected('EXPLICIT_PROXY_AMPLITUDE_REQUIRED')
                    if not math.isclose(sum(r['weight'] for r in a['relays']),1.,rel_tol=0,abs_tol=1e-10):raise ActionRejected('RELAY_WEIGHTS')
                    for r in a['relays']:
                        if r['bias_field_t']+r['coercive_field_t']<=0 and r['initial_sign']<0 or r['bias_field_t']-r['coercive_field_t']>=0 and r['initial_sign']>0:raise ActionRejected('INCONSISTENT_INITIAL_MAGNETIC_STATE')
                    relays=[dict(r,state=r['initial_sign']) for r in a['relays']];amplitudes=[a['mass_kg']*value*r['weight'] for r in relays]
                source=json.dumps(record.get('source'),sort_keys=True,ensure_ascii=False)
            p=copy.deepcopy(a);p['standard']=standard;C0=self._capacity();old=C0*(self.sample_k-self.plant['ambient_k']);incoming=a['mass_kg']*a['specific_heat_capacity_j_kg_k']*(a['initial_temperature_k']-self.plant['ambient_k']);self.sample=p;self.record=record;self.source=source;self.linear_coefficient=linear;self.relays=relays;self.amplitudes=amplitudes;self.sample_k=self.plant['ambient_k']+(old+incoming)/self._capacity();self.book['sample_sensible_loaded_j']+=incoming;self.book['magnetic_loaded_j']+=self._magnetic_energy();self.book['loaded_mass_kg']+=a['mass_kg'];self.book['loaded_volume_m3']+=a['volume_m3'];out=dict(present=True)
        elif capability=='sample.remove':
            if not self.sample:raise ActionRejected('NO_SAMPLE')
            if self._pos('lift')<.34 or self._moving('lift') or self.vibration['enabled'] or abs(self._pos('field'))>1e-10 or self._moving('field'):raise ActionRejected('LOADING_POSITION_REQUIRED')
            m=self.sample['mass_kg'];v=self.sample['volume_m3'];self.book['removed_mass_kg']+=m;self.book['removed_volume_m3']+=v;self.book['sample_sensible_removed_j']+=m*self.sample['specific_heat_capacity_j_kg_k']*(self.sample_k-self.plant['ambient_k']);self.book['magnetic_removed_j']+=self._magnetic_energy();self.sample=None;self.record=None;self.source=None;self.linear_coefficient=0.;self.relays=[];self.amplitudes=[];out=dict(removed_mass_kg=m,removed_volume_m3=v)
        elif capability=='power.set':
            if not self.plant:raise ActionRejected('NOT_CONFIGURED')
            if not a['enabled'] and (abs(self._pos('field'))>1e-10 or self._moving('field')):raise ActionRejected('ZERO_FIELD_REQUIRED')
            if not a['enabled']:
                if self.phase=='running':self._finish('cancelled','POWER_OFF')
                self.vibration['enabled']=False
                for k in self.axes:self._target(k,self._pos(k))
            self.power=a['enabled'];out=dict(enabled=self.power)
        elif capability=='field.set_target':
            if not self.power:raise ActionRejected('POWER_OFF')
            if self._pos('lift')>1e-9 and self.sample or self._moving('lift') or self._moving('angle'):raise ActionRejected('MEASUREMENT_POSITION_REQUIRED')
            target=quant(a['field_t'],FIELD_RESOLUTION_T);self._field_guard([target]);self.ramp_rate=a['ramp_rate_t_s'];self._target('field',target);out=dict(target_t=target)
        elif capability in ('sample.set_lift','sample.set_angle','storage.set_open'):
            if not self.plant or not self.power:raise ActionRejected('POWER_OFF')
            if capability!='storage.set_open' and (abs(self._pos('field'))>1e-10 or self._moving('field') or self.vibration['enabled']):raise ActionRejected('ZERO_FIELD_AND_VIBRATION_OFF_REQUIRED')
            k='lift' if capability=='sample.set_lift' else 'angle' if capability=='sample.set_angle' else 'drawer';value=a['lift_m'] if k=='lift' else quant(a['angle_rad'],math.pi/1800) if k=='angle' else .2 if a['opened'] else 0.;self._target(k,value);out={'target_rad' if k=='angle' else 'target_m':value}
        elif capability=='vibration.configure':
            if not self.power or not self.coil:raise ActionRejected('NOT_CONFIGURED')
            if a['enabled'] and (self._pos('lift')>1e-9 or self._moving('lift') or self._moving('angle')):raise ActionRejected('MEASUREMENT_POSITION_REQUIRED')
            if any(a[k]!=self.vibration[k] for k in ('amplitude_m','frequency_hz')):self.calibration_valid=False
            self.vibration=copy.deepcopy(a);out=dict(enabled=a['enabled'])
        elif capability=='calibration.clear':self.calibration_valid=False;self.calibration_gain=1.;self.calibration_offset=0.;out=dict(valid=False)
        elif capability in ('acquisition.start','calibration.start'):
            iscal=capability=='calibration.start'
            if not self.power or not self.plant or not self.coil or not self.vibration['enabled']:raise ActionRejected('NOT_CONFIGURED')
            if not self.sample:raise ActionRejected('NO_SAMPLE')
            if self._pos('lift')>1e-9 or self._moving('lift') or self._moving('angle'):raise ActionRejected('MEASUREMENT_POSITION_REQUIRED')
            if not self._temperature_valid():raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
            if iscal:
                method=dict(ramp_rate_t_s=1.,averaging_time_s=a['averaging_time_s'],settling_time_s=max(.05,10*self.plant['detector_tau_s']));sequence=[dict(field_t=quant(B,FIELD_RESOLUTION_T),record=True,reversal_field_t=None) for B in [a['field_t'],-a['field_t']]]
            else:
                if not self.method:raise ActionRejected('NOT_CONFIGURED')
                if self.method['require_calibration'] and not self.calibration_valid:raise ActionRejected('CALIBRATION_REQUIRED')
                method=copy.deepcopy(self.method);sequence=[]
                if method['kind']=='points':sequence=[dict(field_t=quant(B,FIELD_RESOLUTION_T),record=True,reversal_field_t=None) for B in method['field_points_t']]
                else:
                    saturation=method['saturation_field_t']
                    for Br in method['reversal_fields_t']:
                        sequence.append(dict(field_t=quant(saturation,FIELD_RESOLUTION_T),record=False,reversal_field_t=Br))
                        sequence.extend(dict(field_t=quant(float(B),FIELD_RESOLUTION_T),record=True,reversal_field_t=Br) for B in np.linspace(Br,saturation,method['points_per_curve']))
            self._field_guard([p['field_t'] for p in sequence])
            if not iscal and method['kind']=='forc' and self.relays and method['saturation_field_t']*abs(math.cos(self._pos('angle')))<max(r['coercive_field_t']+abs(r['bias_field_t']) for r in self.relays):raise ActionRejected('MODEL_SATURATION_UNREACHABLE')
            self._counter+=1;self.operation_id=f'{self.instance_id}:acquisition:{self._counter}';self._operation_source=self.source;self.operation_kind='calibration' if iscal else method['kind'];self.phase='running';self.elapsed=0.;self.rows=[];self.error=None;self._start_ns=self.time_ns;self._job_method=method;self._sequence=sequence;self._index=0;self._cal_reference=a['reference_moment_a_m2'] if iscal else None;self._begin_point();out=dict(operation_id=self.operation_id)
        elif capability=='acquisition.stop':
            if self.phase!='running':raise ActionRejected('NOT_RUNNING')
            self._target('field',self._pos('field'));self._finish('cancelled','USER_CANCEL');out=dict(phase=self.phase,elapsed_s=self.elapsed)
        else:raise ActionRejected('UNKNOWN_CAPABILITY')
        self.revision+=1;return copy.deepcopy(out)
    def step(self,dt_s):
        validate(number('s',0,86400),dt_s);total=math.fsum((self._clock_remainder_ns,dt_s*1e9));ns=round(total);self._clock_remainder_ns=total-ns
        if not ns:return
        end=self.time_ns+ns
        while self.time_ns<end:
            if self.phase=='running' and self._event_ns<=self.time_ns:self._advance()
            boundary=end
            for k in self.axes:
                if self._moving(k):boundary=min(boundary,self.axes[k]['arrival_ns'])
            switch=self._next_switch()
            if switch is not None:boundary=min(boundary,switch)
            if self.phase=='running':boundary=min(boundary,self._event_ns)
            self._propagate(boundary-self.time_ns);self.time_ns=boundary;self._positions();self._switch()
            if self.phase=='running':
                self.elapsed=(self.time_ns-self._start_ns)/1e9
                if self._event_ns<=self.time_ns:self._advance()
        self.revision+=1

class LakeShore8604VsmSession:
    def __init__(self, host, principal_id): self._host = host; self._principal = principal_id
    @property
    def _role(self): return self._host._principals[self._principal]
    def _target(self, instance_id):
        if instance_id != self._host.instance_id: raise ActionRejected('UNKNOWN_INSTANCE')
    def describe(self, *, instance_id=None, asset_type_id=None):
        if (instance_id is None) == (asset_type_id is None): raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None: self._target(instance_id)
        if asset_type_id is not None and asset_type_id != ASSET_TYPE_ID: raise ActionRejected('UNKNOWN_INSTANCE')
        meta = export_lakeshore_8604vsm_contract_metadata()
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
            result['evidence'] = 'synthetic_forward_model; observed_interval_mean_lockin_voltage; measured_library_reference_anchor_not_original_this_loop_or_FORC; selected record restricted' if result['operation_id'] is not None else 'no_measurement'
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

for i,B in enumerate([-.1,0.,.1]):
    row=dict(point_index=i,field_t=B,external_h_a_m=B/MU0,in_phase_voltage_v=B*.001,moment_a_m2=B*.001,angle_rad=0.,window_start_s=float(i),window_end_s=i+.1,reversal_field_t=None,quality='estimated')
    CAPS['analysis.loop']['example_arguments']['rows'].append(row)
for Br in [-.1,-.05,0.]:
    for B in [0.,.05,.1]:
        row=copy.deepcopy(CAPS['analysis.loop']['example_arguments']['rows'][0]);row.update(field_t=B,external_h_a_m=B/MU0,moment_a_m2=.001*(B-Br*B),reversal_field_t=Br)
        CAPS['analysis.forc']['example_arguments']['rows'].append(row)
EFFECTS={
 'plant.configure':['plant.configured','thermal.sample_k','thermal.magnet_k','truth.sample_k'],
 'coil.configure':['coil.configured','calibration.valid','calibration.gain','calibration.offset_v'],
 'method.configure':['method.configured'],'forc.configure':['method.configured'],
 'sample.load':['sample.present','thermal.sample_k','truth.sample_k','truth.mass_kg','truth.moment_a_m2'],
 'standard.load':['sample.present','thermal.sample_k','truth.sample_k','truth.mass_kg','truth.moment_a_m2'],
 'sample.remove':['sample.present','truth.mass_kg','truth.moment_a_m2'],
 'power.set':['power.enabled','power.angle_rad','field.indicated_t','field.external_h_a_m','moment.indicated_a_m2','detector.in_phase_v','vibration.enabled','sample.vibration_z_m','sample.target_lift_m','sample.target_angle_rad','storage.target_m','field.target_t','acquisition.phase','acquisition.stage'],
 'field.set_target':['field.target_t'],'sample.set_lift':['sample.target_lift_m'],'sample.set_angle':['sample.target_angle_rad'],'storage.set_open':['storage.target_m'],
 'vibration.configure':['vibration.enabled','sample.vibration_z_m','moment.indicated_a_m2','detector.in_phase_v','calibration.valid'],
 'calibration.clear':['calibration.valid','calibration.gain','calibration.offset_v'],
 'acquisition.start':['acquisition.phase','acquisition.stage','acquisition.elapsed_s','acquisition.points','field.target_t'],
 'calibration.start':['acquisition.phase','acquisition.stage','acquisition.elapsed_s','acquisition.points','field.target_t'],
 'acquisition.stop':['acquisition.phase','acquisition.stage','field.target_t'],
 'sim.step':list(SLOTS)}
for k,v in EFFECTS.items():CAPS[k]['effects']=v
ERRORS=sorted(set(__import__('re').findall(r"ActionRejected\('([A-Z_0-9]+)'",Path(__file__).read_text()))|{'POWER_OFF','USER_CANCEL','MOMENT_OVERFLOW','REFERENCE_TEMPERATURE_RANGE'})
LIMITATIONS=[
 '固定Lake Shore8604/86-LC单轴线圈/标准极帽ExactGAP2，样品空间8mm，外施空气场±2.37T；高场帽2.76T、其它gap、矢量线圈与温控附件未选。样品0..10g，磁矩1emu=1e-3 A m²。',
 '公共field_t指外施空气磁感应B，H_external=B/μ0；样品内部B、H与退磁张量未求解，不把外场矫顽值冒充内禀矫顽力。',
 '厂家动态范围25nemu至1000emu只限定可配置满量程；零交叉和低于25nemu仍可返回估计并明确below_documented_dynamic_range，不宣称低场准确度。15nemu RMS@10s与100nT场分辨是不同规格；未将RMS当任意积分时长下的随机分布。',
 '真实数据包含原文室温285..300K的CRC molarχ、300K的SQUID铁氧体majorloops与Ni校准证书锚；证据/原始条件逐条保留，真实SQUID回线不冒充本8604原生输出。',
 'linear_susceptibility: m=(mass/molar_mass)*χmol*B/μ0，低场线性/无退磁/温度容差为宿主接受的近似。Preisach使用真实高场比磁矩端点或显式host幅度，再加显式coercivity/bias/weights，属于synthetic_forward_model；端点不自动叫饱和Ms，±6T源major不可截成±2.37T实测饱和loop。',
 '独立矩形Preisach hysterons保留反转记忆；FORC小回线由显式假设驱动，不能从实测majorloop唯一推导。旋转投影m_x=m_axis*cosθ，加各向同性线性项；不是全Stoner-Wohlfarth、矢量磁畴或退磁场模型。',
 '磁化开关是有限能量的理想瞬时事件；W=B_eff*Δm、ΔU=bias*Δm、Q=(B_eff-bias)*Δm≥0，线性可逆能量U=.5*a*B²（抗磁参考可为负相对量）。未求开关脉冲峰值功率。',
 '磁体工程常数B=kI、V=RI+L*dI/dt；plant只允许在完整±2.37T及1T/s线性工作包络内满足70A/35V/2450W。绕组Joule、L储能、磁化功与冷却输出独立留账；4250W整机冷却要求不等于本模型线圈耗散。',
 '样品与局部夹持热容、磁体局部绕组热容均为宿主工程输入，非183/429/612kg整机热容；基础常温配置没有主动样品加热。载入取出质量体积、当前显热与磁态能量随库存转移。',
 '为保持已实现能量边界，断电只允许零外场，非零场请求原子拒绝；停止采集只停止当前扫场并保留实际场，磁体电流及Joule耗散继续，需另设零场后才能断电。',
 '锁相V=m*pickup_coupling*A*2πf/√2，含显式工程增益/零偏和一阶包络响应；average_time≥.01s真实区间积分，无随机噪声。解析振动姿态是宿主时刻采样，可混叠，不表示场景碰撞全频求解。',
 '位移/角度/抽屉有限速率，样品仅在零场和振动关闭时移动；0.1°为真实设置分辨率，实际转角保留多圈，orientation仅提供周期等价场景姿态。',
 '校准用外部给定的±已知标准磁矩及真实±场积分电压拟合零偏/增益，不读隐藏plant；defaultNi数值锚仅在原证书条件成立，host线性calibrant proxy不声称真实Ni全场响应或NIST认证。',
 '分析仅读外部rows：分支线性零点给Hc/Mr，∫Bdm对于未闭合路径不是cycleloss；FORC用最多15邻点的局部二次混合导数，不假称FORCinel或真实微结构唯一识别。',
 '所有角色普通acquisition.result/poll同样去身份，完整来源独立evaluator provenance.read；共享磁盘/Python不是OS隔离。硬件、机器人、真实噪声/准确度与附件温控not_run。']

def export_lakeshore_8604vsm_contract_metadata():
    slots={};caps={}
    for k,v in SLOTS.items():slots[k]=dict(meaning_zh=v['meaning'],component_ids=[v['component']],value_schema=copy.deepcopy(v['schema']),unit=v['unit'],reference_conditions='SI, fixed standard poles ExactGAP2/86-LC, room temperature; magnetic field is externally applied B in air.',origin=v['origin'],visibility=v['visibility'],access='read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'LakeShore8604VsmSession.read(state_ids=[{k!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description='Reads do not advance time; acquisition rows separately give actual integration windows, instantaneous detector state is not an interval mean.'),source_ids=['simulation_assumption'])
    for k,v in CAPS.items():
        un=k in UNSUPPORTED;async_=k in ('acquisition.start','calibration.start');caps[k]={key:copy.deepcopy(v[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']}
        caps[k].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ('scene','robot','hardware') else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.lakeshore_8604vsm.LakeShore8604VsmSession.invoke',invocation=f'session.invoke(instance_id="vsm_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),preconditions=[] if un or v['kind'] in ('query','analysis') else [dict(description_zh='入口原子核对量程、选型、温域、供磁包络、零场机械移动和真实采集前提。',state_ids=['power.enabled','sample.present','sample.lift_m','field.target_t','vibration.enabled','acquisition.phase'],enforced_by='runtime',check='LakeShore8604VsmRuntime.invoke')],state_effects=[dict(state_id=s,timing='on_step' if k=='sim.step' else 'immediate',description_zh='声明状态变化；实际物理与观测只在step推进') for s in v['effects']],observes=[],model_effects=dict(mode='none',description_zh='运行层仅输出可绑定状态，实际几何和读数联动由独立scene证据验证。',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='所有有限扫场、等待与真实积分窗口完成' if async_ else '原子命令；仅step推进实际物理',completion_state_ids=['acquisition.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','magnet_power_supply','vsm_head','lockin_detector'],partial_failure_zh='取消保留完成点、实际B和磁态、已积磁功/Joule热；停止记录不清空剩磁或热量。'),errors=[dict(code=e,meaning_zh=e,side_effects='partial' if async_ and e in ('MOMENT_OVERFLOW','REFERENCE_TEMPERATURE_RANGE','USER_CANCEL') else 'none') for e in ERRORS],verification=[],limitations=LIMITATIONS,source_ids=['manufacturer_primary','simulation_assumption','template_library'])
        if k in ('acquisition.start','calibration.start','field.set_target','sample.set_lift','sample.set_angle','storage.set_open','vibration.configure'):
            caps[k]['state_effects'] += [dict(state_id=s,timing='on_step',description_zh='由宿主显式时间推进物理或实际积分读数') for s in SLOTS if s not in v['effects'] and SLOTS[s]['origin'] in ('sensor','derived','scene','process_truth')]
    mechanism=dict(component_ids=['magnet','power_supply','sample','sample_rotor','head_lift','vibration_carriage','controller','sense_coil_left','sense_coil_right'],state_ids=list(slots),meaning_zh='外磁场有限扫场、显式Preisach记忆、VSM锁相包络实际积分和电磁被动热守恒',implementation='lumped',equations=['H_external=B_applied/mu0; m_linear=(sample_mass/molar_mass)*chi_molar*H_external.','Preisach: state flips at B*cos(theta)=bias +/- coercivity; m_x=sum(moment_weight*state)*cos(theta)+a*B.','Switch: W=B_eff*dm, dU=bias*dm, Q=(B_eff-bias)*dm>=0; reversible linear U=0.5*a*B^2.','I=B/k; V=R*I+L*dI/dt; P_R=I^2*R, E_L=0.5*L*I^2. Electrical input=dE_L+Joule+magnetic work.','Cmag*dTmag/dt=I^2*R-Gmag*(Tmag-Tambient); Csample*dTsample/dt=-Gsample*(Tsample-Tambient)+hysteretic event heat.','V_inphase=engineering_gain*pickup_coupling*amplitude*2*pi*frequency*m/sqrt(2)+offset; detector tau*dV/dt=V_inphase-V.','Recorded Vbar=(integral V dt)/actual_window; observed moment=(Vbar-cal_offset)*cal_gain/nominal_sensitivity.','Calibration gain=2*external_reference_moment*nominal_sensitivity/(Vplus-Vminus); offset=(Vplus+Vminus)/2.','FORC rho=-0.5*d2(moment)/(dB*dBr), evaluated by local quadratic fit to external observations only.','Host step is sole clock, with fractional-nanosecond carry; switching/ramp/axis/integration events split propagation analytically.'],parameters={},source_ids=['simulation_assumption','template_library'],limitations=LIMITATIONS)
    spec=importlib.util.spec_from_file_location('_vsm_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()});ann.annotate_export(SLUG,caps,slots,{'vsm_magnetometry':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'coil.configure':(COIL,COIL_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'standard.load':(STANDARD,STANDARD_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'forc.configure':(FORC,FORC_EXAMPLE),'vibration.configure':(VIBRATION,VIBRATION_EXAMPLE)})
    for k,v in SLOTS.items():slots[k]['meaning_zh']=v['meaning']
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'vsm_magnetometry':mechanism})

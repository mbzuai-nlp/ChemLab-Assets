"""HIT5P / ISO179 .5J: real-time nonlinear pendulum and observed empty correction.
Manual raising / host catching are external engineering actions, not OEM motors.
"""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
import numpy as np
from scipy.integrate import solve_ivp
PROTOCOL_VERSION='1.0.0';SLUG='zwick_hit5p';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-145'
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch04/t1_thermal'/SLUG;LIBRARY_PATH=ASSET/'impact_library.json'
RELEASE_ANGLE_RAD=math.radians(135.72);ENCODER_RESOLUTION_RAD=math.radians(.018);NOMINAL_ENERGY_J=.5;STRIKE_RADIUS_M=.25;NOMINAL_SPEED_M_S=2.9
GRAVITY_COEFFICIENT_NM=NOMINAL_ENERGY_J/(1-math.cos(RELEASE_ANGLE_RAD));INERTIA_KG_M2=2*NOMINAL_ENERGY_J/(NOMINAL_SPEED_M_S/STRIKE_RADIUS_M)**2;SUPPORT_SPAN_M=.062
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

def library():return {r['id']:r for r in json.loads(LIBRARY_PATH.read_text())['templates']}
def encoder(angle):return round(angle/ENCODER_RESOLUTION_RAD)*ENCODER_RESOLUTION_RAD
PLANT_EXAMPLE=dict(ambient_k=296.15,friction_torque_nm=.001,host_raise_speed_rad_s=1.,shield_speed_rad_s=2.,bearing_heat_capacity_j_k=5.,bearing_conductance_w_k=.2,local_sample_heat_capacity_j_k=.01,sample_conductance_w_k=.03,sample_heat_fraction=.8,accept_engineering=True)
PLANT=obj(dict(ambient_k=number('K',273.15,323.15),friction_torque_nm=number('N*m',0,.1),host_raise_speed_rad_s=number('rad/s',.01,2),shield_speed_rad_s=number('rad/s',.01,5),bearing_heat_capacity_j_k=number('J/K',.001,1e5),bearing_conductance_w_k=number('W/K',1e-9,1e4),local_sample_heat_capacity_j_k=number('J/K',1e-6,100),sample_conductance_w_k=number('W/K',1e-9,1e4),sample_heat_fraction=number('1',0,1),accept_engineering=BOOL))
FIXTURE_EXAMPLE=dict(standard='ISO179_Charpy',pendulum_energy_j=.5,strike_radius_m=.25,support_span_m=.062,release_angle_rad=RELEASE_ANGLE_RAD,accept_effective_inertia=True)
FIXTURE=obj(dict(standard=enum(['ISO179_Charpy']),pendulum_energy_j=dict(type='number',enum=[.5],**{'x-unit':'J'}),strike_radius_m=dict(type='number',enum=[.25],**{'x-unit':'m'}),support_span_m=dict(type='number',enum=[.062],**{'x-unit':'m'}),release_angle_rad=dict(type='number',enum=[RELEASE_ANGLE_RAD],**{'x-unit':'rad'}),accept_effective_inertia=BOOL))
SAMPLE_EXAMPLE=dict(reference_id='basf_ultradur_b4520',notch='A',length_m=.08,width_m=.01,thickness_m=.004,notch_depth_m=.002,volume_m3=3.192e-6,mass_kg=.00415,specific_heat_capacity_j_kg_k=1200.,initial_temperature_k=296.15,temperature_tolerance_k=2.,host_charpy_strength_j_m2=None,behavior='energy_threshold',no_break_restitution=.2,accept_reference_transfer_and_fracture_proxy=True)
SAMPLE=obj(dict(reference_id=TEXT,notch=enum(['A','unnotched']),length_m=number('m',.063,.15),width_m=number('m',.001,.03),thickness_m=number('m',.0005,.02),notch_depth_m=number('m',0,.025),volume_m3=number('m^3',1e-10,.001),mass_kg=number('kg',1e-7,.2),specific_heat_capacity_j_kg_k=number('J/(kg*K)',1,1e5),initial_temperature_k=number('K',200,400),temperature_tolerance_k=number('K',0,30),host_charpy_strength_j_m2=nullable(number('J/m^2',1,1e7)),behavior=enum(['energy_threshold','unbreakable_proxy']),no_break_restitution=number('1',0,1),accept_reference_transfer_and_fracture_proxy=BOOL))
METHOD_EXAMPLE=dict(sample_interval_s=.002,timeout_s=10.,remaining_area_prior_m2=3.2e-5)
METHOD=obj(dict(sample_interval_s=number('s',.001,.1),timeout_s=number('s',2,60),remaining_area_prior_m2=nullable(number('m^2',1e-12,.1))))
ROW=obj(dict(point_index={'type':'integer','x-unit':'1'},time_s=number('s'),encoder_angle_rad=nullable(number('rad')),kind=enum(['sample','impact','apex']),broken=BOOL))
VALIDITY=enum(['not_completed','valid','below_working_range','above_working_range','no_break','invalid_speed','temperature_invalid','negative_corrected_energy'])
ANALYSIS=obj(dict(release_angle_rad=number('rad',0,math.pi),rebound_angle_rad=number('rad',-math.pi,math.pi),friction_torque_nm=number('N*m',0,1),remaining_area_prior_m2=nullable(number('m^2',1e-12,.1)),outcome=enum(['break','no_break'])))
ANALYSIS_OUTPUT=obj(dict(raw_energy_j=number('J'),corrected_energy_j=number('J'),impact_strength_j_m2=nullable(number('J/m^2')),validity=VALIDITY,evidence=TEXT))
RESULT=obj(dict(operation_id=nullable(TEXT),phase=PHASE,mode=TEXT,elapsed_s=number('s'),rows=array(ROW),release_angle_rad=nullable(number('rad')),rebound_angle_rad=nullable(number('rad')),raw_energy_j=nullable(number('J')),corrected_energy_j=nullable(number('J')),impact_strength_j_m2=nullable(number('J/m^2')),observed_impact_speed_m_s=nullable(number('m/s')),outcome=enum(['pending','empty','break','no_break']),validity=VALIDITY,terminal_reason=nullable(TEXT),template_source=nullable(TEXT),evidence=TEXT))
LEDGER_KEYS=['loaded_mass_kg','removed_mass_kg','sample_mass_kg','mass_residual_kg','loaded_volume_m3','removed_volume_m3','sample_volume_m3','volume_residual_m3','host_work_j','pendulum_kinetic_j','pendulum_potential_j','friction_heat_j','bearing_thermal_j','bearing_heat_export_j','sample_absorbed_j','sample_heat_j','sample_damage_stored_j','sample_thermal_j','sample_heat_export_j','sensible_loaded_j','sensible_removed_j','internal_removed_j','energy_residual_j']
LEDGER=obj({k:number('kg' if k.endswith('_kg') else 'm^3' if k.endswith('_m3') else 'J') for k in LEDGER_KEYS})
CAPS={}
def cap(k,s,o,e,c,meaning,visibility='agent',kind='device_command'):CAPS[k]=dict(input_schema=s,output_schema=o,example_arguments=e,component_ids=[c],function_zh=meaning,visibility=visibility,kind=kind,effects=[])
for k,s,e,c in [('plant.configure',PLANT,PLANT_EXAMPLE,'pendulum'),('fixture.configure',FIXTURE,FIXTURE_EXAMPLE,'fixture_base'),('method.configure',METHOD,METHOD_EXAMPLE,'controller')]:cap(k,s,obj(dict(configured=BOOL)),e,c,'配置工程过程、固定ISO179 .5J/62mm夹具或真实观测采样方法','agent' if k=='method.configure' else 'evaluator','configuration')
cap('sample.load',SAMPLE,obj(dict(present=BOOL)),SAMPLE_EXAMPLE,'sample_left','载入Charpy真实参考及显式温度/几何/破坏代理','evaluator')
cap('sample.remove',obj({}),obj(dict(removed_mass_kg=number('kg'),removed_volume_m3=number('m^3'))),{},'sample_left','取出整条或全部收集碎片，带走质量/显热/损伤能','evaluator')
cap('power.set',obj(dict(enabled=BOOL)),obj(dict(enabled=BOOL)),dict(enabled=True),'power_switch','启停电子显示/记录；掉电不删除自由摆机械能')
cap('shield.set_open',obj(dict(opened=BOOL)),obj(dict(target_angle_rad=number('rad'))),dict(opened=True),'shield','宿主有限速开合已选可选Charpy局部防碎片罩','evaluator')
cap('pendulum.raise',obj({}),obj(dict(target_angle_rad=number('rad'))),{},'pendulum','外部手动抬摆有限速代理；非HIT5P电机或自动回摆','evaluator')
cap('pendulum.catch',obj({}),obj(dict(requested=BOOL)),{},'pendulum','请求宿主在下一实际转折点接住，非仪器自带刹车','evaluator')
cap('calibration.start',obj({}),obj(dict(operation_id=TEXT)),{},'latch','真实空摆至首个回升峰值，以量化角度估计有效摩擦')
cap('calibration.clear',obj({}),obj(dict(valid=BOOL)),{},'controller','清除摩擦校正，不改变机械物理')
cap('acquisition.start',obj({}),obj(dict(operation_id=TEXT)),{},'latch','机械释放后的有限时间Charpy冲击和真实角度记录')
cap('acquisition.stop',obj({}),obj(dict(phase=PHASE,elapsed_s=number('s'))),{},'controller','只停记录，自由摆/撞击/摩擦仍随step继续，不回滚损伤')
cap('acquisition.result',obj({}),RESULT,{},'controller','公开角色统一去选中材料身份的能量与破坏状态','agent','query')
cap('analysis.impact',ANALYSIS,ANALYSIS_OUTPUT,dict(release_angle_rad=RELEASE_ANGLE_RAD,rebound_angle_rad=-1.8,friction_torque_nm=.001,remaining_area_prior_m2=3.2e-5,outcome='break'),'controller','从外部量化角度/独立面积先验反演J与J/m²，非Izod J/m','agent','analysis')
cap('ledger.read',obj({}),LEDGER,{},'controller','受限机械/热/损伤/质量体积账','evaluator','query')
cap('provenance.read',obj({}),obj(dict(current_sample=nullable(TEXT),operation_source=nullable(TEXT))),{},'controller','受限当前和已启动操作来源快照','evaluator','query')
cap('sim.step',obj(dict(dt_s=number('s',0,86400))),obj(dict(time_s=number('s'))),dict(dt_s=.02),'controller','唯一宿主时间推进','evaluator','simulation')
UNSUPPORTED={'hardware.connect':'无真实仪器通信','robot.load':'无机器人装样或机械抓取验证','measurement.izod':'选ISO179 Charpy，未配Izod','measurement.tensile_impact':'未配拉伸冲击夹具/摆锤','measurement.instrumented_force':'无仪器化锤/高速力电子学','pendulum.oem_motor_return':'本HIT5P配置无已证自动回摆','brake.oem_disc':'本HIT5P无已配盘式制动','thermal.set_temperature':'未选冷却/加热附件','accuracy.certification':'没有标准认证或实测噪声数据'}
for k,v in UNSUPPORTED.items():cap(k,obj({}),obj({}),{},'controller',v)
SLOTS={}
def slot(k,s,u,o,c,meaning,vis='agent'):SLOTS[k]=dict(schema=s,unit=u,origin=o,component=c,meaning=meaning,visibility=vis,write=None)
slot('clock.time_s',number('s'),'s','derived','controller','唯一宿主时钟，read不推进')
for k in ['plant.configured','fixture.configured','method.configured','sample.present','sample.broken','sample.spent','power.enabled','pendulum.held','latch.engaged','catch.requested','calibration.valid']:slot(k,BOOL,'1','derived','controller','配置/样品/机械边界状态；不是硬件安全认证')
slot('pendulum.angle_rad',number('rad'),'rad','scene','pendulum','精确场景角：0竖下，+135.72°释放侧；世界轴−Y，受限不可绕过encoder','evaluator')
slot('pendulum.target_angle_rad',number('rad'),'rad','setpoint','pendulum','外部手动抬摆目标，不代表已到达')
slot('encoder.angle_rad',nullable(number('rad')),'rad','sensor','controller','0.018°量化角度，分辨率不代表准确度或随机噪声')
for k in ['shield.angle_rad','shield.target_angle_rad']:slot(k,number('rad'),'rad','scene' if '.angle' in k else 'setpoint','shield','局部可选防碎片罩角度，0闭合/1.1打开')
slot('latch.position_m',number('m'),'m','scene','latch','释放锁扣显示代理，0在正释放角锁入/.004退出，非实测锁扣运动')
slot('power.angle_rad',number('rad'),'rad','scene','power_switch','电子电源显示姿态0/.28')
slot('fragments.offset_m',number('m'),'m','scene','sample_right','破断后两半视觉分離示意.006m，不是碎片弹道或新增能量','evaluator')
slot('calibration.friction_torque_nm',nullable(number('N*m')),'N*m','derived','controller','由实际空摆量化α/β求等效Coulomb摩擦，非隐藏plant参数')
slot('measurement.energy_j',nullable(number('J')),'J','sensor','controller','最后外部角度反演校正吸能，量程有效性另读validity；掉电不亮')
slot('measurement.validity',VALIDITY,'1','derived','controller','当前测量有效性，.5J所选工作范围.05.. .4J')
slot('acquisition.phase',PHASE,'1','derived','controller','异步记录状态，完成/取消不等于机械停摆')
slot('acquisition.elapsed_s',number('s'),'s','derived','controller','记录实际时间')
slot('acquisition.points',{'type':'integer','x-unit':'1'},'1','derived','controller','采样及明确impact/apex事件行数')
for k,u in [('truth.angular_velocity_rad_s','rad/s'),('truth.sample_absorbed_j','J'),('truth.sample_temperature_k','K'),('truth.bearing_temperature_k','K'),('truth.sample_mass_kg','kg')]:slot(k,number(u),u,'process_truth','pendulum','受限非线性摆锤/热库存真值','evaluator')

def analyze_impact(release_angle_rad,rebound_angle_rad,friction_torque_nm,remaining_area_prior_m2,outcome):
    alpha=abs(release_angle_rad);beta=abs(rebound_angle_rad);raw=GRAVITY_COEFFICIENT_NM*(math.cos(beta)-math.cos(alpha));corrected=raw-friction_torque_nm*(alpha+beta)
    validity='negative_corrected_energy' if corrected<0 else 'below_working_range' if corrected<.05 else 'above_working_range' if corrected>.4 else 'no_break' if outcome=='no_break' else 'valid'
    return dict(raw_energy_j=raw,corrected_energy_j=corrected,impact_strength_j_m2=corrected/remaining_area_prior_m2 if remaining_area_prior_m2 is not None and validity=='valid' else None,validity=validity,evidence='external_quantized_angles_and_empty_swing_correction; independent_remaining_area_prior; ISO179_J_per_m2_not_Izod_J_per_m; not_certified_standard_compliance')

class ZwickHit5pRuntime:
    def __init__(self,instance_id='hit_01'):
        self.instance_id=instance_id;self.time_ns=0;self._clock_remainder_ns=0.;self.revision=0;self.power=False;self.plant=None;self.fixture=None;self.method=None;self.sample=None;self.reference=None;self.source=None
        self.theta=0.;self.omega=0.;self.mechanical='held';self.raise_target=0.;self._raise_start_ns=0;self._raise_end_ns=0;self._raise_angle0=0.;self._raise_started=False;self._raise_rate=0.;self.shield=1.1;self.shield_target=1.1;self.shield_start=1.1;self.shield_start_ns=0;self.shield_end_ns=0;self.catch_requested=False;self._segment=None;self._pending_impact=False;self._first_apex=False
        self.broken=False;self.spent=False;self.absorbed=0.;self.damage=0.;self.bearing_U=0.;self.sample_U=0.;self.calibration=None;self.phase='idle';self.operation_id=None;self.mode='none';self.elapsed=0.;self.rows=[];self._counter=0;self.terminal_reason=None;self._operation_source=None;self._release_observed=None;self._rebound_observed=None;self._observed_speed=None;self._outcome='pending';self._validity='not_completed';self._analysis=None;self.last_energy=None;self.records={};self._principals={};self._requests={};self._owners={};self._cancel_operations={}
        self.book={k:0. for k in ['loaded_mass_kg','removed_mass_kg','loaded_volume_m3','removed_volume_m3','host_work_j','friction_heat_j','bearing_heat_export_j','sample_absorbed_j','sample_heat_j','sample_heat_export_j','sensible_loaded_j','sensible_removed_j','internal_removed_j']}
    @property
    def time_s(self):return self.time_ns/1e9
    def register_principal(self,name,visibility):
        if not isinstance(name,str) or not name or visibility not in ('agent','evaluator','debug') or name in self._principals:raise ActionRejected('INVALID_ARGUMENT')
        self._principals[name]=visibility
    def session(self,name):
        if name not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return ZwickHit5pSession(self,name)
    def _mass(self):return self.sample['mass_kg'] if self.sample else 0.
    def _capacity(self):return self.plant['local_sample_heat_capacity_j_k']+(self._mass()*self.sample['specific_heat_capacity_j_kg_k'] if self.sample else 0.)
    def _temperature(self):return self.plant['ambient_k']+self.sample_U/self._capacity() if self.plant else 296.15
    def _energy(self):return .5*INERTIA_KG_M2*self.omega**2+GRAVITY_COEFFICIENT_NM*(1-math.cos(self.theta))
    def _latch(self):return self.mechanical=='held' and abs(self.theta-RELEASE_ANGLE_RAD)<1e-10
    def _shield_moving(self):return self.time_ns<self.shield_end_ns
    def _temperature_valid(self):
        if not self.sample:return True
        t=self._temperature();ref=self.reference.get('reference_temperature_K');domain=self.reference.get('valid_temperature_range_K');tol=self.sample['temperature_tolerance_k']
        return (ref is None or abs(t-ref)<=tol+1e-9) and (not domain or abs(domain[1]-domain[0])<1e-9 or domain[0]<=t<=domain[1]) and abs(t-self.sample['initial_temperature_k'])<=tol+1e-9
    def read(self,visibility='agent'):
        d={'clock.time_s':self.time_s,'plant.configured':self.plant is not None,'fixture.configured':self.fixture is not None,'method.configured':self.method is not None,'sample.present':self.sample is not None,'sample.broken':self.broken,'sample.spent':self.spent,'power.enabled':self.power,'pendulum.held':self.mechanical=='held','latch.engaged':self._latch(),'catch.requested':self.catch_requested,'calibration.valid':self.calibration is not None,'pendulum.angle_rad':self.theta,'pendulum.target_angle_rad':self.raise_target,'encoder.angle_rad':encoder(self.theta) if self.power else None,'shield.angle_rad':self.shield,'shield.target_angle_rad':self.shield_target,'latch.position_m':0. if self._latch() else .004,'power.angle_rad':.28 if self.power else 0.,'fragments.offset_m':.006 if self.broken and self.sample else 0.,'calibration.friction_torque_nm':self.calibration,'measurement.energy_j':self.last_energy if self.power else None,'measurement.validity':self._validity,'acquisition.phase':self.phase,'acquisition.elapsed_s':self.elapsed,'acquisition.points':len(self.rows),'truth.angular_velocity_rad_s':self.omega,'truth.sample_absorbed_j':self.absorbed,'truth.sample_temperature_k':self._temperature(),'truth.bearing_temperature_k':self.plant['ambient_k']+self.bearing_U/self.plant['bearing_heat_capacity_j_k'] if self.plant else 296.15,'truth.sample_mass_kg':self._mass()}
        return {k:copy.deepcopy(v) for k,v in d.items() if visibility!='agent' or SLOTS[k]['visibility']=='agent'}
    def ledger(self):
        b=copy.deepcopy(self.book);m=self._mass();v=self.sample['volume_m3'] if self.sample else 0.;b.update(sample_mass_kg=m,mass_residual_kg=b['loaded_mass_kg']-b['removed_mass_kg']-m,sample_volume_m3=v,volume_residual_m3=b['loaded_volume_m3']-b['removed_volume_m3']-v,pendulum_kinetic_j=.5*INERTIA_KG_M2*self.omega**2,pendulum_potential_j=GRAVITY_COEFFICIENT_NM*(1-math.cos(self.theta)),bearing_thermal_j=self.bearing_U,sample_damage_stored_j=self.damage,sample_thermal_j=self.sample_U)
        b['energy_residual_j']=b['host_work_j']+b['sensible_loaded_j']-b['sensible_removed_j']-b['internal_removed_j']-b['bearing_heat_export_j']-b['sample_heat_export_j']-self._energy()-self.bearing_U-self.sample_U-self.damage;return b
    def _record(self,kind='sample'):
        self.rows.append(dict(point_index=len(self.rows),time_s=self.time_s,encoder_angle_rad=encoder(self.theta) if self.power else None,kind=kind,broken=self.broken))
    def _finish(self,phase,reason):
        self.phase=phase;self.terminal_reason=reason
        if self.operation_id:self.records[self.operation_id]=self._result(use_cache=False)
    def _result(self,use_cache=True):
        if use_cache and self.phase!='running' and self.operation_id in self.records:return copy.deepcopy(self.records[self.operation_id])
        a=self._analysis or {};return dict(operation_id=self.operation_id,phase=self.phase,mode=self.mode,elapsed_s=self.elapsed,rows=copy.deepcopy(self.rows),release_angle_rad=self._release_observed,rebound_angle_rad=self._rebound_observed,raw_energy_j=a.get('raw_energy_j'),corrected_energy_j=a.get('corrected_energy_j'),impact_strength_j_m2=a.get('impact_strength_j_m2'),observed_impact_speed_m_s=self._observed_speed,outcome=self._outcome,validity=self._validity,terminal_reason=self.terminal_reason,template_source=self._operation_source,evidence='synthetic_forward_model; actual_quantized_encoder_trajectory; ISO179_manufacturer_material_reference_plus_explicit_energy_threshold_no_break_thermal_transfer_assumptions; not_native_material_curve')
    def _cool(self,dt,bearing_power=0.):
        if not self.plant:return
        C=self.plant['bearing_heat_capacity_j_k'];G=self.plant['bearing_conductance_w_k'];old=self.bearing_U;new=old*math.exp(-G*dt/C)+bearing_power*C/G*(-math.expm1(-G*dt/C));self.bearing_U=new;self.book['bearing_heat_export_j']+=old+bearing_power*dt-new
        C=self._capacity();G=self.plant['sample_conductance_w_k'];old=self.sample_U;self.sample_U=old*math.exp(-G*dt/C);self.book['sample_heat_export_j']+=old-self.sample_U
    def _new_segment(self):
        if self.mechanical!='free':return
        torque=self.plant['friction_torque_nm'];gravity=-GRAVITY_COEFFICIENT_NM*math.sin(self.theta)
        if abs(self.omega)<1e-10 and abs(gravity)<=torque+1e-12:self.omega=0.;self.mechanical='held';self.catch_requested=False;self._segment=None;return
        direction=math.copysign(1,self.omega if abs(self.omega)>1e-10 else gravity);C_b=self.plant['bearing_heat_capacity_j_k'];G_b=self.plant['bearing_conductance_w_k'];C_s=self._capacity();G_s=self.plant['sample_conductance_w_k']
        def rhs(t,y):
            P=torque*direction*y[1];return [y[1],(-GRAVITY_COEFFICIENT_NM*math.sin(y[0])-torque*direction)/INERTIA_KG_M2,P,P-G_b*y[3]/C_b,-G_s*y[4]/C_s,G_b*y[3]/C_b,G_s*y[4]/C_s]
        event_kind='impact' if self._pending_impact else 'apex'
        def event(t,y):return y[0] if event_kind=='impact' else y[1]
        event.terminal=True;event.direction=-1 if event_kind=='impact' or direction>0 else 1
        y0=np.array([self.theta,self.omega,0.,self.bearing_U,self.sample_U,0.,0.]);sol=solve_ivp(rhs,[0,10],y0,events=event,dense_output=True,method='DOP853',rtol=2e-12,atol=2e-14,max_step=.02)
        if not sol.success or not len(sol.t_events[0]):raise RuntimeError('Pendulum event integration failed')
        duration=float(sol.t_events[0][0]);end=self.time_ns+max(1,round(duration*1e9));self._segment=dict(start_ns=self.time_ns,end_ns=end,duration=duration,solution=sol.sol,last_y=y0,event=event_kind)
    def _propagate_free(self,nxt):
        s=self._segment;t=s['duration'] if nxt==s['end_ns'] else (nxt-s['start_ns'])/1e9;y=s['solution'](t);old=s['last_y'];self.theta=float(y[0]);self.omega=float(y[1]);self.book['friction_heat_j']+=float(y[2]-old[2]);self.bearing_U=float(y[3]);self.sample_U=float(y[4]);self.book['bearing_heat_export_j']+=float(y[5]-old[5]);self.book['sample_heat_export_j']+=float(y[6]-old[6]);s['last_y']=y
    def _physical_impact(self):
        self.theta=0.;self._pending_impact=False;self._first_apex=True
        if self.phase=='running':
            previous=[r for r in self.rows if r['kind']=='sample' and r['encoder_angle_rad'] is not None][-2:]
            if len(previous)==2:self._observed_speed=abs((previous[-1]['encoder_angle_rad']-previous[-2]['encoder_angle_rad'])/(previous[-1]['time_s']-previous[-2]['time_s']))*STRIKE_RADIUS_M
            self._record('impact')
        if self.sample and not self.spent:
            available=.5*INERTIA_KG_M2*self.omega**2;required=self.break_energy
            if required is not None and available>=required:
                lost=required;self.omega=-math.sqrt(max(0,2*(available-lost)/INERTIA_KG_M2));self.broken=True;self._outcome='break'
            else:
                retained=available*self.sample['no_break_restitution']**2;lost=available-retained;self.omega=math.sqrt(max(0,2*retained/INERTIA_KG_M2));self._outcome='no_break'
            heat=lost*self.plant['sample_heat_fraction'];self.absorbed+=lost;self.book['sample_absorbed_j']+=lost;self.book['sample_heat_j']+=heat;self.sample_U+=heat;self.damage+=lost-heat;self.spent=True
        else:
            if self.phase=='running':self._outcome='empty'
        if abs(self.omega)<1e-10:self.mechanical='held';self._physical_apex()
    def _physical_apex(self):
        self.omega=0.
        if self._first_apex:
            self._first_apex=False
            if self.phase=='running':
                self._rebound_observed=encoder(self.theta);self._record('apex')
                if self.mode=='calibration':
                    alpha=abs(self._release_observed);beta=abs(self._rebound_observed);estimate=GRAVITY_COEFFICIENT_NM*(math.cos(beta)-math.cos(alpha))/(alpha+beta)
                    if estimate<0:self._finish('failed','INVALID_EMPTY_SWING')
                    else:self.calibration=estimate;self._validity='valid';self._analysis=analyze_impact(alpha,beta,estimate,None,'no_break');self._finish('succeeded','empty_swing_completed')
                else:
                    self._analysis=analyze_impact(self._release_observed,self._rebound_observed,self.calibration,self.method['remaining_area_prior_m2'],self._outcome);self._validity=self._analysis['validity'];self.last_energy=self._analysis['corrected_energy_j']
                    if not self._temperature_valid():self._validity='temperature_invalid';self._analysis['impact_strength_j_m2']=None
                    elif self._observed_speed is None or not 2.9*.9<=self._observed_speed<=2.9*1.1:self._validity='invalid_speed';self._analysis['impact_strength_j_m2']=None
                    self._finish('succeeded' if self._validity in ('valid','no_break') else 'failed',self._validity)
        if self.catch_requested:self.mechanical='held';self.catch_requested=False
        self._segment=None
    def invoke(self,capability_id,**arguments):
        if capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability_id]['input_schema'],arguments);k=capability_id;a=copy.deepcopy(arguments)
        if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
        if k=='acquisition.result':return self._result()
        if k=='ledger.read':return self.ledger()
        if k=='provenance.read':return dict(current_sample=self.source,operation_source=self._operation_source)
        if k=='analysis.impact':return analyze_impact(**a)
        if k=='sim.step':self.step(a['dt_s']);return dict(time_s=self.time_s)
        if self.phase=='running' and k not in ('acquisition.stop','power.set','pendulum.catch'):raise ActionRejected('BUSY')
        if k=='plant.configure':
            if self.plant or self.power or self.sample:raise ActionRejected('PLANT_ALREADY_CONFIGURED')
            if not a['accept_engineering']:raise ActionRejected('ENGINEERING_ACCEPTANCE_REQUIRED')
            if a['bearing_heat_capacity_j_k']/a['bearing_conductance_w_k']<.001 or a['local_sample_heat_capacity_j_k']/a['sample_conductance_w_k']<.001:raise ActionRejected('THERMAL_TIME_CONSTANT_OUTSIDE_NUMERICAL_DOMAIN')
            self.plant=a;out=dict(configured=True)
        elif k=='fixture.configure':
            if self.sample or self.mechanical!='held':raise ActionRejected('SAMPLE_PRESENT_OR_MOVING')
            if not a['accept_effective_inertia']:raise ActionRejected('ENGINEERING_ACCEPTANCE_REQUIRED')
            self.fixture=a;self.calibration=None;out=dict(configured=True)
        elif k=='method.configure':
            nanos=a['sample_interval_s']*1e9
            if abs(nanos-round(nanos))>1e-4:raise ActionRejected('SAMPLING_GRID_RESOLUTION')
            self.method=a;out=dict(configured=True)
        elif k=='sample.load':
            if not self.plant or not self.fixture:raise ActionRejected('CONFIGURATION_REQUIRED')
            if self.sample:raise ActionRejected('SAMPLE_PRESENT')
            if not self._latch() or self.shield<1.1-1e-12 or self._shield_moving():raise ActionRejected('LOADING_GEOMETRY')
            if not a['accept_reference_transfer_and_fracture_proxy']:raise ActionRejected('ENGINEERING_ACCEPTANCE_REQUIRED')
            if a['notch_depth_m']>=a['width_m'] or a['notch']=='unnotched' and a['notch_depth_m']!=0 or a['notch']=='A' and a['notch_depth_m']<=0:raise ActionRejected('INVALID_NOTCH_GEOMETRY')
            if a['volume_m3']>a['length_m']*a['width_m']*a['thickness_m']+1e-12:raise ActionRejected('INVALID_VOLUME')
            ref=library().get(a['reference_id'])
            if ref is None:raise ActionRejected('UNKNOWN_REFERENCE')
            nominal=ref.get('reference_temperature_K');domain=ref.get('valid_temperature_range_K');T=a['initial_temperature_k'];tol=a['temperature_tolerance_k']
            if nominal is not None and abs(T-nominal)>tol+1e-9 or domain and domain[1]-domain[0]>1e-9 and not domain[0]<=T<=domain[1]:raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
            strength=a['host_charpy_strength_j_m2'];field='charpy_notched_impact_strength_J_m2' if a['notch']=='A' else 'charpy_unnotched_impact_strength_J_m2'
            if strength is None:strength=ref.get(field)
            if a['behavior']=='energy_threshold' and strength is None:raise ActionRejected('MISSING_CHARPY_REFERENCE_OR_PRIOR')
            incoming=a['mass_kg']*a['specific_heat_capacity_j_kg_k']*(T-self.plant['ambient_k']);self.sample=a;self.reference=ref;self.source=json.dumps(dict(reference_id=a['reference_id'],source=ref.get('source'),evidence=ref.get('evidence')),sort_keys=True);self.break_energy=None if a['behavior']=='unbreakable_proxy' else strength*(a['width_m']-a['notch_depth_m'])*a['thickness_m'];self.broken=self.spent=False;self.absorbed=self.damage=0.;self.sample_U+=incoming;self.book['sensible_loaded_j']+=incoming;self.book['loaded_mass_kg']+=a['mass_kg'];self.book['loaded_volume_m3']+=a['volume_m3'];out=dict(present=True)
        elif k=='sample.remove':
            if not self.sample:raise ActionRejected('NO_SAMPLE')
            if not self._latch() or self.shield<1.1-1e-12 or self._shield_moving():raise ActionRejected('LOADING_GEOMETRY')
            mass=self._mass();volume=self.sample['volume_m3'];sensible=mass*self.sample['specific_heat_capacity_j_kg_k']*(self._temperature()-self.plant['ambient_k']);self.book['sensible_removed_j']+=sensible;self.book['internal_removed_j']+=self.damage;self.book['removed_mass_kg']+=mass;self.book['removed_volume_m3']+=volume;self.sample_U-=sensible;self.sample=self.reference=self.source=None;self.broken=self.spent=False;self.absorbed=self.damage=0.;out=dict(removed_mass_kg=mass,removed_volume_m3=volume)
        elif k=='power.set':
            if not self.plant:raise ActionRejected('PLANT_REQUIRED')
            if not a['enabled'] and self.phase=='running':self._finish('cancelled','POWER_OFF')
            self.power=a['enabled'];out=dict(enabled=self.power)
        elif k=='shield.set_open':
            if not self.plant:raise ActionRejected('PLANT_REQUIRED')
            if self.mechanical!='held':raise ActionRejected('PENDULUM_MOVING')
            target=1.1 if a['opened'] else 0.;self.shield_start=self.shield;self.shield_target=target;self.shield_start_ns=self.time_ns;self.shield_end_ns=self.time_ns+math.ceil(abs(target-self.shield)/self.plant['shield_speed_rad_s']*1e9);out=dict(target_angle_rad=target)
        elif k=='pendulum.raise':
            if not self.plant or not self.fixture:raise ActionRejected('CONFIGURATION_REQUIRED')
            if self.mechanical!='held':raise ActionRejected('PENDULUM_MOVING')
            if self.shield>1e-12 or self._shield_moving():raise ActionRejected('SHIELD_OPEN')
            if self.sample and not self.spent and not self._latch():raise ActionRejected('FRESH_SAMPLE_IN_PATH')
            self.raise_target=RELEASE_ANGLE_RAD;distance=RELEASE_ANGLE_RAD-self.theta;self._raise_start_ns=self.time_ns;self._raise_end_ns=self.time_ns+math.ceil(abs(distance)/self.plant['host_raise_speed_rad_s']*1e9);self._raise_angle0=self.theta;self._raise_started=False;self._raise_rate=distance/((self._raise_end_ns-self.time_ns)/1e9) if distance else 0.;self.mechanical='raising' if distance else 'held';out=dict(target_angle_rad=self.raise_target)
        elif k=='pendulum.catch':
            if self.mechanical!='free':raise ActionRejected('NOT_FREE_SWING')
            self.catch_requested=True;out=dict(requested=True)
        elif k=='calibration.clear':self.calibration=None;out=dict(valid=False)
        elif k in ('calibration.start','acquisition.start'):
            if not self.power:raise ActionRejected('POWER_OFF')
            if not self.plant or not self.fixture or not self.method:raise ActionRejected('CONFIGURATION_REQUIRED')
            if not self._latch():raise ActionRejected('NOT_ARMED')
            if self.shield>1e-12 or self._shield_moving():raise ActionRejected('SHIELD_OPEN')
            if k=='calibration.start' and self.sample:raise ActionRejected('EMPTY_REQUIRED')
            if k=='acquisition.start':
                if not self.sample:raise ActionRejected('NO_SAMPLE')
                if self.spent:raise ActionRejected('SAMPLE_SPENT')
                if self.calibration is None:raise ActionRejected('CALIBRATION_REQUIRED')
                if not self._temperature_valid():raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
            self._counter+=1;self.operation_id=f'{self.instance_id}:swing:{self._counter}';self.phase='running';self.mode='calibration' if k=='calibration.start' else 'impact';self.elapsed=0.;self.rows=[];self.terminal_reason=None;self._operation_source=self.source;self._release_observed=encoder(self.theta);self._rebound_observed=self._observed_speed=None;self._outcome='pending';self._validity='not_completed';self._analysis=None;self._start_ns=self.time_ns;self._end_ns=self.time_ns+round(self.method['timeout_s']*1e9);self._period_ns=round(self.method['sample_interval_s']*1e9);self._sample_ns=self.time_ns+self._period_ns;self.mechanical='free';self._pending_impact=True;self._first_apex=False;self.catch_requested=False;self._segment=None;self._record();out=dict(operation_id=self.operation_id)
        elif k=='acquisition.stop':
            if self.phase!='running':raise ActionRejected('NOT_RUNNING')
            self._finish('cancelled','USER_CANCEL');out=dict(phase=self.phase,elapsed_s=self.elapsed)
        else:raise ActionRejected('UNKNOWN_CAPABILITY')
        self.revision+=1;return copy.deepcopy(out)
    def step(self,dt_s):
        validate(number('s',0,86400),dt_s);raw=dt_s*1e9+self._clock_remainder_ns;ns=round(raw);self._clock_remainder_ns=raw-ns
        if not ns:return
        end=self.time_ns+ns
        while self.time_ns<end:
            if self.mechanical=='free' and self._segment is None:self._new_segment()
            if self.mechanical=='raising' and not self._raise_started:
                old=self._energy();self.omega=self._raise_rate;self.book['host_work_j']+=self._energy()-old;self._raise_started=True
            nexts=[end]
            if self._shield_moving():nexts.append(self.shield_end_ns)
            if self.mechanical=='free':nexts.append(self._segment['end_ns'])
            if self.mechanical=='raising':nexts.append(self._raise_end_ns)
            if self.phase=='running':nexts.extend([self._sample_ns,self._end_ns])
            nxt=min(t for t in nexts if t>self.time_ns);dt=(nxt-self.time_ns)/1e9
            if self.mechanical=='free':self._propagate_free(nxt)
            elif self.mechanical=='raising':
                old=self._energy();self.theta=self._raise_angle0+self._raise_rate*(nxt-self._raise_start_ns)/1e9;heat=self.plant['friction_torque_nm']*abs(self._raise_rate)*dt;self.book['friction_heat_j']+=heat;self.book['host_work_j']+=self._energy()-old+heat;self._cool(dt,heat/dt)
            else:self._cool(dt)
            self.time_ns=nxt
            self.shield=self.shield_target if self.time_ns>=self.shield_end_ns else self.shield_start+(self.shield_target-self.shield_start)*(self.time_ns-self.shield_start_ns)/(self.shield_end_ns-self.shield_start_ns)
            if self.phase=='running':self.elapsed=(self.time_ns-self._start_ns)/1e9
            if self.mechanical=='raising' and self.time_ns==self._raise_end_ns:
                old=self._energy();self.theta=RELEASE_ANGLE_RAD;self.omega=0.;self.book['host_work_j']+=self._energy()-old;self.mechanical='held'
            if self.mechanical=='free' and self._segment and self.time_ns==self._segment['end_ns']:
                kind=self._segment['event'];self._segment=None
                if kind=='impact':self._physical_impact()
                else:self._physical_apex()
            if self.phase=='running' and self.time_ns==self._sample_ns:self._record();self._sample_ns+=self._period_ns
            if self.phase=='running' and self.time_ns>=self._end_ns:self._finish('failed','OBSERVATION_TIMEOUT')
        self.revision+=1

class ZwickHit5pSession:
    def __init__(self, host, principal_id): self._host = host; self._principal = principal_id
    @property
    def _role(self): return self._host._principals[self._principal]
    def _target(self, instance_id):
        if instance_id != self._host.instance_id: raise ActionRejected('UNKNOWN_INSTANCE')
    def describe(self, *, instance_id=None, asset_type_id=None):
        if (instance_id is None) == (asset_type_id is None): raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None: self._target(instance_id)
        if asset_type_id is not None and asset_type_id != ASSET_TYPE_ID: raise ActionRejected('UNKNOWN_INSTANCE')
        meta = export_zwick_hit5p_contract_metadata()
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
            result['evidence'] = 'synthetic_forward_model; quantized_encoder_angles_and_observed_empty_swing_correction; manufacturer_ISO179_scalar_reference_and_explicit_fracture_thermal_proxy; selected record restricted' if result['operation_id'] is not None else 'no_measurement'
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
 'plant.configure':['plant.configured','truth.sample_temperature_k','truth.bearing_temperature_k'],
 'fixture.configure':['fixture.configured','calibration.valid','calibration.friction_torque_nm'],
 'method.configure':['method.configured'],
 'sample.load':['sample.present','sample.broken','sample.spent','truth.sample_mass_kg','truth.sample_temperature_k','truth.sample_absorbed_j','fragments.offset_m'],
 'sample.remove':['sample.present','sample.broken','sample.spent','truth.sample_mass_kg','truth.sample_temperature_k','truth.sample_absorbed_j','fragments.offset_m'],
 'power.set':['power.enabled','power.angle_rad','encoder.angle_rad','measurement.energy_j','acquisition.phase'],
 'shield.set_open':['shield.target_angle_rad'],
 'pendulum.raise':['pendulum.target_angle_rad','pendulum.held','latch.engaged','latch.position_m'],
 'pendulum.catch':['catch.requested'],
 'calibration.clear':['calibration.valid','calibration.friction_torque_nm'],
 'acquisition.start':['acquisition.phase','acquisition.elapsed_s','acquisition.points','measurement.validity','pendulum.held','latch.engaged','latch.position_m','catch.requested'],
 'calibration.start':['acquisition.phase','acquisition.elapsed_s','acquisition.points','measurement.validity','pendulum.held','latch.engaged','latch.position_m','catch.requested'],
 'acquisition.stop':['acquisition.phase'],
 'sim.step':list(SLOTS)}
for k,v in EFFECTS.items():CAPS[k]['effects']=v
ERRORS=sorted(set(__import__('re').findall(r"ActionRejected\('([A-Z_0-9]+)'",Path(__file__).read_text()))|{'USER_CANCEL','POWER_OFF','OBSERVATION_TIMEOUT','INVALID_EMPTY_SWING'})
LIMITATIONS=[
 '固定HIT5P / ISO179 Charpy .5J摆锤010930 / 62mm夹具010961，0.5J不是整机最大5J；释放135.72°、摆长250mm、名义2.9m/s±10%、encoder .018°。厂家工作范围10..80%，即.05.. .4J；范围外可报告估计J但不给有效标准化强度。',
 '本HIT5P配置为机械释放，不配自动回摆和盘式制动。pendulum.raise为外部手动有限速代理，pendulum.catch请求宿主在下一转折点接住并保持；它们不是OEM电机/刹车，也未验证机器人抓取/人手安全。',
 '当前几何摆轴−Y，0竖下、正角释放侧在+X，负角速度撞击向−X；刀口半径.25m不是所有质量的COM。重力矩K=.5/(1−cosα)，有效惯量I=2*.5/(2.9/.25)²由名义规格约束推导，不是独立部件惯量实测。厂家.119kg仅名义总摆锤，70kg整机不当摆锤或热容。',
 '自由摆I*θddot=−K*sinθ−τeff*sign(ω)。恒定Coulomb有效摩擦为明确空气/轴承合并近似，不解频率相关空气阻力或轴承温变；实际摩擦功τeff*|dθ|进入局部轴承工程热节点。',
 '空摆首回升峰值提供τcal=K*(cosβ−cosα)/(α+β)，完全来自量化encoder角度及名义K，不读取hidden friction。样品Eobs=K*(cosβ−cosα)−τcal*(α+β)；角度分辨不等于OEM准确度，无经验noise分布。',
 '真实厂家61牌号ISO179标量来自各明确温度/缺口工况；N不破、未知值与零吸能不同，绝不把Izod J/m换成Charpy J/m²。原始force/displacement/time曲线缺失，当前前向摆动曲线是synthetic_forward_model。',
 '宿主接受的energy-threshold代理在实际θ=0时移除参考Charpy强度×剩余截面积的能量；不解接触力峰值、颈缩、裂纹扩展或支点应变。能量不足或unbreakable代理按显式restitution反弹，partial吸能可记录但不能当完整断裂强度。',
 '瞬时冲击是集总离散事件，不声称全带宽碰撞/力传感；破断角速度由真实能量扣除得到。β比例进入样品热，其余进入损伤/新表面库存；所有碎片留在收集边界，取样才携出mass、显热与损伤能。',
 '显式非线性/热耦合积分的工程计算域要求bearing C/G及未装样local C/G≥1ms；过快热节点原子拒绝，不把该数值稳定边界当OEM物理限制。',
 '宿主抬摆轨迹有限速、理想速度约束；启动/停止的惯性能量计入有符号host work，可回收机械能，不假造有限峰值电机功率。被动换热参数为宿主工程输入，无主动温控附件。',
 '采样按宿主定义时间网格，impact/apex额外事件独立标记；首峰值完成记录后摆锤继续自由摆，cancel/掉电亦不删机械能。没有宿主catch时继续至Coulomb静摩擦可支撑位置，自然停摆不强制回到0。',
 '默认80×10×4mm、A缺口深2mm，剩余面积32mm²。载入其它过程尺寸不会自动重建固定3D示例；sample mass、volume、Cp为独立宿主先验。破后分离是受限视觉示意，不是碎片弹道或连续变形碰撞表面。',
 '单温度参考仅在宿主显式容差内作常数近似，不进行不同温度/缺口/速率数据暗中插值。Coulomb校正、工程移植和代理结果不构成ISO179认证。',
 '精确场景摆角/片段位置及过程能量受限evaluator，agent使用量化encoder；所有角色普通结果/poll都隐藏选中牌号/来源。完整来源仅provenance.read；共享磁盘/Python不构成OS隔离。']
def export_zwick_hit5p_contract_metadata():
    slots={};caps={}
    for k,v in SLOTS.items():slots[k]=dict(meaning_zh=v['meaning'],component_ids=[v['component']],value_schema=copy.deepcopy(v['schema']),unit=v['unit'],reference_conditions='SI; selected HIT5P ISO179 Charpy0.5J / .25m /135.72deg /62mm span; external manual raise/catch.',origin=v['origin'],visibility=v['visibility'],access='read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'ZwickHit5pSession.read(state_ids=[{k!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description='Pure read; encoder quantized0.018deg; rows on method time grid with separate impact/apex events.'),source_ids=['simulation_assumption'])
    for k,v in CAPS.items():
        un=k in UNSUPPORTED;async_=k in ('acquisition.start','calibration.start');caps[k]={key:copy.deepcopy(v[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']}
        caps[k].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ('scene','robot','hardware') else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.zwick_hit5p.ZwickHit5pSession.invoke',invocation=f'session.invoke(instance_id="hit_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),preconditions=[] if un or v['kind'] in ('query','analysis') else [dict(description_zh='入口原子核对ISO179 .5J选型、抬摆锁止、罩位置、空摆校准和实际样品前提。',state_ids=['power.enabled','sample.present','pendulum.held','latch.engaged','shield.angle_rad','acquisition.phase'],enforced_by='runtime',check='ZwickHit5pRuntime.invoke')],state_effects=[dict(state_id=s,timing='on_step' if k=='sim.step' else 'immediate',description_zh='设定或操作标志；真实机械/温度/角度读数仅step推进') for s in v['effects']],observes=[],model_effects=dict(mode='none',description_zh='运行层输出供scene独立绑定验证的状态，不直接写几何',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='释放、实际冲击和首回升角观测完成；记录结束不代表机械停摆' if async_ else '原子配置/外部动作请求，实际状态由step推进',completion_state_ids=['acquisition.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','pendulum','ISO179_fixture','encoder'],partial_failure_zh='停止记录保留全部已点/机械能/破坏/热与库存；自由摆继续至外部catch或自然静止。'),errors=[dict(code=e,meaning_zh=e,side_effects='partial' if async_ and e in ('USER_CANCEL','POWER_OFF','OBSERVATION_TIMEOUT','INVALID_EMPTY_SWING') else 'none') for e in ERRORS],verification=[],limitations=LIMITATIONS,source_ids=['manufacturer_primary','simulation_assumption','template_library'])
        if k in ('acquisition.start','calibration.start','pendulum.raise','pendulum.catch','shield.set_open'):
            caps[k]['state_effects'] += [dict(state_id=s,timing='on_step',description_zh='由统一宿主时钟推进非线性摆动、摩擦/冲击/被动热及量化encoder') for s in SLOTS if s not in v['effects'] and SLOTS[s]['origin'] in ('sensor','derived','scene','process_truth')]
    mechanism=dict(component_ids=['pendulum','striker','latch','fixture_base','sample_left','sample_right','controller'],state_ids=list(slots),meaning_zh='ISO179 .5J非线性摆动、实际空摆角度摩擦校正、能量阈值破坏/反弹和可守恒热库存',implementation='lumped',equations=['Kgrav=.5/(1-cos(alpha)); I=2*.5/(2.9/.25)^2; alpha=135.72deg.','Emechanical=0.5*I*omega²+Kgrav*(1-cos(theta)); I*domega/dt=-Kgrav*sin(theta)-tau*sign(omega).','Pfriction=tau*abs(omega); passive bearing/sample heat nodes retain and export energy.','At theta=0: break iff kinetic>=explicit threshold aCharpy*Aremaining; omega_after=-sqrt(2*(kinetic-threshold)/I).','No-break: omega_after=+restitution*abs(omega_before), absorbed=(1-restitution²)*kinetic; no complete-break strength inferred.','Qsample=heat_fraction*absorbed; remaining absorbed stored as damage/surface energy.','Calibration from observed empty alpha,beta: tau_est=Kgrav*(cos(beta)-cos(alpha))/(alpha+beta).','Observed absorbed energy=Kgrav*(cos(beta)-cos(alpha))-tau_est*(alpha+beta); aCharpy=Eobs/Aremaining_prior only for complete valid break and supplied independent area.','External host raising: dWhost=dEmechanical+dQfriction, including ideal velocity boundary kinetic change; at-turn catch imposes hold at omega0 without energy deletion.','Host sole clock; free-flight dense ODE segments anchored at physical events and integer-nanosecond sampling grid with fractional-nanosecond carry.'],parameters={},source_ids=['simulation_assumption','template_library'],limitations=LIMITATIONS)
    spec=importlib.util.spec_from_file_location('_hit_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()});ann.annotate_export(SLUG,caps,slots,{'charpy_pendulum':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'fixture.configure':(FIXTURE,FIXTURE_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
    for k,v in SLOTS.items():slots[k]['meaning_zh']=v['meaning']
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'charpy_pendulum':mechanism})

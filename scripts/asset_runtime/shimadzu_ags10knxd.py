"""AGS-10kNXD / high-precision10kNcell / real5kNSCG / TRAPEZIUM X.
One-dimensional tensile elastoplastic proxy with separate frame compliance,
clip-extensometer observations, fixed-rate sampling/control and irreversible
plastic/fracture energy. No universal-test-mode or OEM accuracy certification.
"""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
import numpy as np
PROTOCOL_VERSION='1.0.0';SLUG='shimadzu_ags10knxd';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-144'
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch04/t1_thermal'/SLUG;LIBRARY_PATH=ASSET/'mechanical_library.json'
LOAD_CELL_MAX_N=10000.;FIXTURE_MAX_N=5000.;MIN_SPEED_M_S=.001e-3/60;MAX_SPEED_M_S=1000e-3/60;POSITION_RESOLUTION_M=1e-6;CONTROL_PERIOD_NS=10_000_000
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
def quant(x,res):return float(round(x/res)*res)
PLANT_EXAMPLE=dict(ambient_k=292.65,frame_stiffness_n_m=1e8,force_gain=1.002,force_offset_n=.02,force_response_tau_s=.002,force_resolution_n=.01,extensometer_gain=1.,extensometer_offset_m=0.,extensometer_resolution_m=1e-7,local_thermal_capacity_j_k=.01,sample_conductance_w_k=.1,plastic_heat_fraction=.9,fracture_heat_fraction=.5,grip_speed_m_s=.01,force_control_gain_m_per_n_s=5e-7,strain_control_gain_m_s=.01,accept_engineering=True)
PLANT=obj(dict(ambient_k=number('K',278.15,313.15),frame_stiffness_n_m=number('N/m',1e7,1e12),force_gain=number('1',.01,100),force_offset_n=number('N',-100,100),force_response_tau_s=number('s',1e-6,10),force_resolution_n=number('N',1e-9,100),extensometer_gain=number('1',.01,100),extensometer_offset_m=number('m',-.001,.001),extensometer_resolution_m=number('m',1e-12,.001),local_thermal_capacity_j_k=number('J/K',1e-6,1e5),sample_conductance_w_k=number('W/K',1e-9,1e5),plastic_heat_fraction=number('1',0,1),fracture_heat_fraction=number('1',0,1),grip_speed_m_s=number('m/s',1e-6,.1),force_control_gain_m_per_n_s=number('m/(N*s)',1e-12,1),strain_control_gain_m_s=number('m/s',1e-9,1),accept_engineering=BOOL))
FIXTURE_EXAMPLE=dict(rated_force_n=5000.,software_trapezium_x=True,extensometer_enabled=True,extensometer_gauge_m=.025,accept_engineering_extensometer=True)
FIXTURE=obj(dict(rated_force_n={'type':'number','const':5000.,'enum':[5000.],'x-unit':'N'},software_trapezium_x=BOOL,extensometer_enabled=BOOL,extensometer_gauge_m=number('m',.001,.25),accept_engineering_extensometer=BOOL))
SAMPLE_EXAMPLE=dict(reference_id='epfl_s355_j2m_hem320_web',gauge_length_m=.025,initial_area_m2=2e-6,volume_m3=1.1e-7,density_kg_m3=7850.,specific_heat_capacity_j_kg_k=470.,initial_temperature_k=292.65,temperature_tolerance_k=10.,host_elastic_modulus_pa=None,host_yield_strength_pa=None,hardening_modulus_pa=1e9,engineering_strain_at_break=.05,accept_bilinear_geometry_temperature_fracture_assumptions=True)
SAMPLE=obj(dict(reference_id=TEXT,gauge_length_m=number('m',.001,.2),initial_area_m2=number('m^2',1e-10,1e-3),volume_m3=number('m^3',1e-12,.01),density_kg_m3=number('kg/m^3',1,5e4),specific_heat_capacity_j_kg_k=number('J/(kg*K)',1,1e5),initial_temperature_k=number('K',200,500),temperature_tolerance_k=number('K',0,100),host_elastic_modulus_pa=nullable(number('Pa',1e3,1e13)),host_yield_strength_pa=nullable(number('Pa',1,1e11)),hardening_modulus_pa=number('Pa',0,1e12),engineering_strain_at_break=number('1',1e-6,.5),accept_bilinear_geometry_temperature_fracture_assumptions=BOOL))
METHOD_EXAMPLE=dict(mode='speed',speed_m_s=.0005,target_crosshead_m=.003,target_force_n=400.,target_strain=.01,sample_rate_hz=1000,duration_s=10.,force_stop_n=5000.,stop_on_break=True)
METHOD=obj(dict(mode=enum(['speed','force','strain']),speed_m_s=number('m/s',MIN_SPEED_M_S,MAX_SPEED_M_S),target_crosshead_m=number('m',0,.3),target_force_n=number('N',0,10000),target_strain=number('1',0,.5),sample_rate_hz={'type':'integer','enum':[1,10,50,100,200,500,1000],'x-unit':'Hz'},duration_s=number('s',.001,3600),force_stop_n=number('N',1,10000),stop_on_break=BOOL))
ROW=obj(dict(point_index={'type':'integer','x-unit':'1'},time_s=number('s'),kind=enum(['sample','fracture_pre','fracture_post']),force_n=nullable(number('N')),crosshead_m=number('m'),extensometer_extension_m=nullable(number('m')),broken=BOOL,quality=enum(['estimated','below_documented_accuracy_range','overload','temperature_invalid'])))
ANALYSIS=obj(dict(rows=array(ROW,3),area_prior_m2=nullable(number('m^2',1e-12,1)),gauge_prior_m=nullable(number('m',1e-9,1)),frame_compliance_prior_m_n=nullable(number('m/N',0,1)),elastic_fit_min_strain=number('1',0,.5),elastic_fit_max_strain=number('1',0,.5)))
ANALYSIS_OUTPUT=obj(dict(points=array(obj(dict(time_s=number('s'),stress_pa=nullable(number('Pa')),strain=nullable(number('1'))))),elastic_modulus_pa=nullable(number('Pa')),ultimate_observed_stress_pa=nullable(number('Pa')),maximum_observed_force_n=number('N'),observed_break_strain=nullable(number('1')),observed_break_extension_m=nullable(number('m')),evidence=TEXT))
RESULT=obj(dict(operation_id=nullable(TEXT),phase=PHASE,mode=TEXT,elapsed_s=number('s'),rows=array(ROW),terminal_reason=nullable(TEXT),template_source=nullable(TEXT),evidence=TEXT))
LEDGER_KEYS=['loaded_mass_kg','removed_mass_kg','mass_kg','mass_residual_kg','loaded_volume_m3','removed_volume_m3','volume_m3','volume_residual_m3','actuator_work_j','plastic_work_j','plastic_heat_j','plastic_defect_stored_j','frame_elastic_j','sample_elastic_j','hardening_stored_j','fracture_surface_j','fracture_heat_j','machine_damping_export_j','sensible_loaded_j','sensible_removed_j','internal_removed_j','sample_thermal_j','heat_export_j','energy_residual_j']
LEDGER=obj({k:number('kg' if k.endswith('_kg') else 'm^3' if k.endswith('_m3') else 'J') for k in LEDGER_KEYS})
CAPS={}
def cap(k,s,o,example,component,meaning,visibility='agent',kind='device_command'):CAPS[k]=dict(input_schema=s,output_schema=o,example_arguments=example,component_ids=[component],function_zh=meaning,visibility=visibility,kind=kind,effects=[])
for k,s,e,component in [('plant.configure',PLANT,PLANT_EXAMPLE,'crosshead'),('fixture.configure',FIXTURE,FIXTURE_EXAMPLE,'upper_grip'),('method.configure',METHOD,METHOD_EXAMPLE,'controller')]:cap(k,s,obj(dict(configured=BOOL)),e,component,'配置过程/夹具/软件或试验程序；不推进时间','evaluator' if k in ('plant.configure','fixture.configure') else 'agent','configuration')
cap('sample.load',SAMPLE,obj(dict(present=BOOL)),SAMPLE_EXAMPLE,'specimen_lower','载入真实材料参考与显式均匀拉伸/断裂/热参数','evaluator')
cap('sample.remove',obj({}),obj(dict(removed_mass_kg=number('kg'),removed_volume_m3=number('m^3'))),{},'specimen_lower','取出全部试样/碎片与塑性库存、当前显热','evaluator')
cap('power.set',obj(dict(enabled=BOOL)),obj(dict(enabled=BOOL)),dict(enabled=True),'power_switch','启停控制器；停止驱动不清除塑性、断裂或储能')
cap('grip.set_closed',obj(dict(which=enum(['upper','lower']),closed=BOOL)),obj(dict(target_opening_m=number('m'))),dict(which='upper',closed=True),'upper_jaw','手工SCG夹紧/松开有限运动代理，非夹压力实测','evaluator')
CAPS['grip.set_closed']['component_ids'].append('lower_jaw')
cap('crosshead.move_to',obj(dict(position_m=number('m',0,.3),speed_m_s=number('m/s',MIN_SPEED_M_S,MAX_SPEED_M_S))),obj(dict(target_m=number('m'))),dict(position_m=.001,speed_m_s=.0005),'crosshead','设置有限速横梁目标；带样会真实加载，含独立5kN夹具限值')
cap('emergency.engage',obj({}),obj(dict(engaged=BOOL)),{},'emergency_stop','停止有限驱动并保留机械/热状态，不宣称硬件安全认证')
cap('emergency.reset',obj({}),obj(dict(engaged=BOOL)),{},'emergency_stop','宿主手动释放急停；不自动继续试验','evaluator')
cap('force.zero',obj({}),obj(dict(zero_n=number('N'))),{},'load_cell','卸载条件下以当前实际传感器观测置零')
cap('force.calibrate',obj(dict(reference_force_n=number('N',20,10000))),obj(dict(gain=number('1'))),dict(reference_force_n=100.),'load_cell','由外部已知载荷与实际观测拟合增益，不读取隐藏gain')
cap('acquisition.start',obj({}),obj(dict(operation_id=TEXT)),{},'controller','按独立采样/控制时钟运行有限拉伸，记录实际力位移与引伸计')
cap('acquisition.stop',obj({}),obj(dict(phase=PHASE,elapsed_s=number('s'))),{},'controller','停止横梁与采样，保留塑性应变、破断和已产生热量')
cap('acquisition.result',obj({}),RESULT,{},'controller','冻结完成记录，公开结果所有角色隐藏选中材料身份','agent','query')
cap('analysis.tensile',ANALYSIS,ANALYSIS_OUTPUT,dict(rows=[],area_prior_m2=2e-6,gauge_prior_m=.025,frame_compliance_prior_m_n=None,elastic_fit_min_strain=.0002,elastic_fit_max_strain=.0015),'controller','由外部观测及独立面积/标距先验分析应力应变与模量','agent','analysis')
cap('provenance.read',obj({}),obj(dict(current_sample=nullable(TEXT),operation_source=nullable(TEXT))),{},'controller','独立受限当前/记录来源','evaluator','query')
cap('ledger.read',obj({}),LEDGER,{},'controller','受限质量体积、弹性/塑性/破断与热能账','evaluator','query')
cap('sim.step',obj(dict(dt_s=number('s',0,86400))),obj(dict(time_s=number('s'))),dict(dt_s=.02),'controller','唯一宿主时钟','evaluator','simulation')
UNSUPPORTED={'hardware.connect':'没有真实仪器驱动','robot.load':'没有机器人装样','measurement.compression':'本SCG平条一维拉伸子集不实现压缩','measurement.bending':'未配弯曲夹具','measurement.peel':'未配剥离夹具','thermal.set_temperature':'未选温控附件，样品仅被动换热和耗散发热','noise.empirical':'没有实测noise模型，±0.5%不是随机分布','accuracy.certification':'没有OEM/ISO/ASTM计量认证','analysis.strain_without_prior_or_extensometer':'横梁位移不能无条件替代试样应变'}
for k,v in UNSUPPORTED.items():cap(k,obj({}),obj({}),{},'controller',v)
SLOTS={}
def slot(k,s,u,o,c,meaning,vis='agent'):SLOTS[k]=dict(schema=s,unit=u,origin=o,component=c,meaning=meaning,visibility=vis,write=None)
slot('fixture.extensometer_enabled',BOOL,'1','setpoint','extensometer','已配置工程引伸计硬件存在标志，不随断电隐去')
slot('clock.time_s',number('s'),'s','derived','controller','统一宿主时间，只由step推进')
for k in ['plant.configured','fixture.configured','method.configured','sample.present','sample.broken','power.enabled','emergency.engaged']:slot(k,BOOL,'1','derived' if k=='sample.broken' else 'setpoint','controller','配置/存在/不可逆破断/启用状态，不等于全部硬件验证')
for k in ['crosshead.position_m','crosshead.target_m']:slot(k,number('m'),'m','scene' if 'position' in k else 'setpoint','crosshead','横梁绝对参考增量，+Z拉伸；不是试样单独伸长')
slot('crosshead.indicated_m',nullable(number('m')),'m','sensor','controller','横梁光编码器显示代理，1µm分辨，不声称±.01mm位置准确度已验证')
slot('frame.deflection_m',number('m'),'m','scene','compliance_carriage','串联机架/夹具工程顺应位移F/K，upper夹持点位移=横梁−该值')
for side in ['upper','lower']:
    slot('grip.'+side+'_opening_m',number('m'),'m','scene',side+'_jaw','单侧jaw开口增量，0夹紧/.006m打开；手工有限运动代理')
    slot('grip.target_'+side+'_opening_m',number('m'),'m','setpoint',side+'_jaw','夹具单侧开口目标，只在step运动')
slot('sample.visual_extension_m',number('m'),'m','scene','specimen_lower','未断样品实体伸长；断后两片残余塑性总长度增量，非连续体有限元网格')
slot('sample.fracture_gap_m',number('m'),'m','scene','specimen_upper','断后夹点分离扣除残余材料长度的裂口，断前0')
slot('power.angle_rad',number('rad'),'rad','scene','power_switch','电源开关显示姿态代理')
slot('emergency.position_m',number('m'),'m','scene','emergency_stop','急停手动闩锁显示位置，0或.004m，不冒安全认证')
slot('force.indicated_n',nullable(number('N')),'N','sensor','load_cell','含工程传感器响应/量化/校准的拉力读数，20N以下不承诺厂家高精度')
slot('extensometer.extension_m',nullable(number('m')),'m','sensor','extensometer','独立工程夹持引伸计观测伸长；未配则null，非横梁改名')
slot('extensometer.strain',nullable(number('1')),'1','derived','extensometer','独立引伸计伸长除其明确配置标距')
slot('thermal.sample_k',nullable(number('K')),'K','sensor','specimen_lower','试样和宿主局部节点被动温度，85kg整机不代入热容')
for k in ['calibration.force_gain','calibration.force_zero_n']:slot(k,number('N' if k.endswith('_n') else '1'),'N' if k.endswith('_n') else '1','derived','load_cell','从当前外部观测与已知载荷获得的校正')
slot('acquisition.phase',PHASE,'1','derived','controller','异步采集状态')
slot('acquisition.elapsed_s',number('s'),'s','derived','controller','试验实际耗时')
slot('acquisition.points',{'type':'integer','x-unit':'1'},'1','derived','controller','实际采样与明确破断事件行数')
for k,u in [('truth.force_n','N'),('truth.plastic_strain','1'),('truth.total_strain','1'),('truth.mass_kg','kg'),('truth.sample_k','K')]:slot(k,number(u),u,'process_truth','specimen_lower','受限一维本构过程真值','evaluator')

# Exact constitutive / scene drive coordinates are host-only. Public strain
# must come from the configured observable extensometer or independent priors.
for _private_drive in ['frame.deflection_m','sample.visual_extension_m','sample.fracture_gap_m']:
    SLOTS[_private_drive]['visibility']='evaluator'

def analyze_tensile(rows,area_prior_m2,gauge_prior_m,frame_compliance_prior_m_n,elastic_fit_min_strain,elastic_fit_max_strain):
    if len(rows)<3 or any(x['force_n'] is None for x in rows) or elastic_fit_max_strain<=elastic_fit_min_strain:raise ActionRejected('INVALID_OBSERVATIONS')
    points=[];fit=[];break_e=None;break_x=None
    for row in rows:
        F=row['force_n'];stress=None if area_prior_m2 is None else F/area_prior_m2;extension=row['extensometer_extension_m']
        if extension is None and frame_compliance_prior_m_n is not None and not row['broken']:extension=row['crosshead_m']-F*frame_compliance_prior_m_n
        strain=None if gauge_prior_m is None or extension is None else extension/gauge_prior_m
        points.append(dict(time_s=row['time_s'],stress_pa=stress,strain=strain))
        if stress is not None and strain is not None and elastic_fit_min_strain<=strain<=elastic_fit_max_strain and not row['broken']:fit.append((strain,stress))
        if row['kind']=='fracture_pre':break_e=strain;break_x=extension
    modulus=None
    if len(fit)>=3 and np.ptp(np.array(fit)[:,0])>1e-12:modulus=float(np.polyfit(np.array(fit)[:,0],np.array(fit)[:,1],1)[0])
    return dict(points=points,elastic_modulus_pa=modulus,ultimate_observed_stress_pa=None if area_prior_m2 is None else max(x['force_n'] for x in rows)/area_prior_m2,maximum_observed_force_n=max(x['force_n'] for x in rows),observed_break_strain=break_e,observed_break_extension_m=break_x,evidence='external_force_and_extensometer_or_explicit_frame_compliance_correction; independent_area_gauge_priors; maximum_observed_stress_is_not_certified_material_UTS; fracture_pre_is_event_observation_not_hidden_breakstrain')

class ShimadzuAgs10knxdRuntime:
    def __init__(self,instance_id='ags_01'):
        self.instance_id=instance_id;self.time_ns=0;self._clock_remainder_ns=0.;self.revision=0;self.power=False;self.emergency=False;self.plant=None;self.fixture=None;self.method=None;self.sample=None;self.reference=None;self.source=None
        self.axes={k:dict(position=v,target=v,start=v,start_ns=0,arrival_ns=0) for k,v in [('crosshead',0.),('upper',.006),('lower',.006)]};self.drive_speed=MIN_SPEED_M_S;self.force=0.;self.plastic=0.;self.broken=False;self.defect_stored=0.;self.fracture_surface=0.;self.temperature=292.65;self.sensor_force=0.;self.force_gain=1.;self.force_zero=0.;self.last_stop_reason=None
        self.phase='idle';self.operation_id=None;self.mode='none';self.elapsed=0.;self.rows=[];self.terminal_reason=None;self._counter=0;self._operation_source=None;self.records={};self._principals={};self._requests={};self._owners={};self._cancel_operations={};self._sample_ns=None;self._control_ns=None
        self.book={k:0. for k in ['loaded_mass_kg','removed_mass_kg','loaded_volume_m3','removed_volume_m3','actuator_work_j','plastic_work_j','plastic_heat_j','fracture_heat_j','machine_damping_export_j','sensible_loaded_j','sensible_removed_j','internal_removed_j','heat_export_j']}
    @property
    def time_s(self):return self.time_ns/1e9
    def register_principal(self,name,visibility):
        if not isinstance(name,str) or not name or visibility not in ('agent','evaluator','debug') or name in self._principals:raise ActionRejected('INVALID_ARGUMENT')
        self._principals[name]=visibility
    def session(self,name):
        if name not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return ShimadzuAgs10knxdSession(self,name)
    def _pos(self,k):return self.axes[k]['position']
    def _moving(self,k):return self.time_ns<self.axes[k]['arrival_ns']
    def _target(self,k,value,speed=None):
        v=speed if k=='crosshead' else self.plant['grip_speed_m_s'];a=self.axes[k];a.update(start=a['position'],target=float(value),start_ns=self.time_ns,arrival_ns=self.time_ns+int(math.ceil(abs(value-a['position'])/v*1e9)))
    def _rate(self):
        a=self.axes['crosshead'];return (a['target']-a['start'])/((a['arrival_ns']-a['start_ns'])/1e9) if self._moving('crosshead') else 0.
    def _positions(self):
        for a in self.axes.values():a['position']=a['target'] if self.time_ns>=a['arrival_ns'] else a['start']+(a['target']-a['start'])*(self.time_ns-a['start_ns'])/(a['arrival_ns']-a['start_ns'])
    def _held(self):return self.sample is not None and all(self._pos(k)<1e-12 and not self._moving(k) for k in ['upper','lower'])
    def _mass(self):return self.sample['volume_m3']*self.sample['density_kg_m3'] if self.sample else 0.
    def _capacity(self):return self.plant['local_thermal_capacity_j_k']+(self._mass()*self.sample['specific_heat_capacity_j_kg_k'] if self.sample else 0.)
    def _free_extension(self):return self.sample['gauge_length_m']*self.plastic if self.sample else 0.
    def _strain(self):return self.plastic+(self.force/self.sample['initial_area_m2']/self.E if self.sample and not self.broken else 0.)
    def _compliance(self):return self.sample['gauge_length_m']/(self.sample['initial_area_m2']*self.E)+1/self.plant['frame_stiffness_n_m']
    def _solve(self,x):
        if not self._held() or self.broken:return 0.,self.plastic
        L=self.sample['gauge_length_m'];A=self.sample['initial_area_m2'];H=self.sample['hardening_modulus_pa'];C=self._compliance();trial=max(0.,(x-L*self.plastic)/C);yield_force=A*(self.Y+H*self.plastic)
        plastic=self.plastic if trial<=yield_force else max(self.plastic,(x-C*A*self.Y)/(L+C*A*H));return max(0.,(x-L*plastic)/C),plastic
    def _stored(self,F=None,plastic=None):
        F=self.force if F is None else F;plastic=self.plastic if plastic is None else plastic
        frame=.5*F*F/self.plant['frame_stiffness_n_m'] if self.plant else 0.;elastic=hardening=0.
        if self.sample:
            V=self.sample['initial_area_m2']*self.sample['gauge_length_m'];elastic=.5*(F/self.sample['initial_area_m2'])**2/self.E*V;hardening=.5*self.sample['hardening_modulus_pa']*plastic*plastic*V
        return frame,elastic,hardening
    def _temperature_valid(self):
        if not self.sample:return True
        nominal=self.reference.get('reference_temperature_K');domain=self.reference.get('valid_temperature_range_K');tol=self.sample['temperature_tolerance_k']
        if nominal is not None and abs(self.temperature-nominal)>tol+1e-9:return False
        if domain and domain[1]-domain[0]>1e-9 and not domain[0]-1e-9<=self.temperature<=domain[1]+1e-9:return False
        return abs(self.temperature-self.sample['initial_temperature_k'])<=tol+1e-9
    def _observed_force(self):
        if not self.power:return None
        result=quant((self.sensor_force-self.force_zero)*self.force_gain,self.plant['force_resolution_n'])
        return result if abs(result)<=LOAD_CELL_MAX_N and self._temperature_valid() else None
    def _observed_extension(self):
        if not self.power or not self.fixture or not self.fixture['extensometer_enabled'] or not self.sample or self.broken:return None
        p=self.plant;return quant(self._strain()*self.fixture['extensometer_gauge_m']*p['extensometer_gain']+p['extensometer_offset_m'],p['extensometer_resolution_m'])
    def ledger(self):
        d=copy.deepcopy(self.book);mass=self._mass();volume=self.sample['volume_m3'] if self.sample else 0.;frame,elastic,hardening=self._stored();thermal=self._capacity()*(self.temperature-self.plant['ambient_k']) if self.plant else 0.;d.update(mass_kg=mass,mass_residual_kg=d['loaded_mass_kg']-d['removed_mass_kg']-mass,volume_m3=volume,volume_residual_m3=d['loaded_volume_m3']-d['removed_volume_m3']-volume,plastic_defect_stored_j=self.defect_stored,frame_elastic_j=frame,sample_elastic_j=elastic,hardening_stored_j=hardening,fracture_surface_j=self.fracture_surface,sample_thermal_j=thermal)
        d['energy_residual_j']=d['actuator_work_j']+d['sensible_loaded_j']-d['sensible_removed_j']-d['internal_removed_j']-d['heat_export_j']-d['machine_damping_export_j']-sum(d[k] for k in ['plastic_defect_stored_j','frame_elastic_j','sample_elastic_j','hardening_stored_j','fracture_surface_j','sample_thermal_j']);return d
    def read(self,visibility='agent'):
        extension=self._free_extension() if self.broken else self._strain()*self.sample['gauge_length_m'] if self.sample else 0.;frame=self.force/self.plant['frame_stiffness_n_m'] if self.plant else 0.;gap=max(0.,self._pos('crosshead')-extension) if self.sample and self.broken else 0.;ext=self._observed_extension()
        d={'clock.time_s':self.time_s,'plant.configured':self.plant is not None,'fixture.configured':self.fixture is not None,'fixture.extensometer_enabled':bool(self.fixture and self.fixture['extensometer_enabled']),'method.configured':self.method is not None,'sample.present':self.sample is not None,'sample.broken':self.broken,'power.enabled':self.power,'emergency.engaged':self.emergency,'crosshead.position_m':self._pos('crosshead'),'crosshead.target_m':self.axes['crosshead']['target'],'crosshead.indicated_m':quant(self._pos('crosshead'),POSITION_RESOLUTION_M) if self.power else None,'frame.deflection_m':frame,'sample.visual_extension_m':extension,'sample.fracture_gap_m':gap,'power.angle_rad':.28 if self.power else 0.,'emergency.position_m':.004 if self.emergency else 0.,'force.indicated_n':self._observed_force(),'extensometer.extension_m':ext,'extensometer.strain':None if ext is None else ext/self.fixture['extensometer_gauge_m'],'thermal.sample_k':self.temperature if self.plant else None,'calibration.force_gain':self.force_gain,'calibration.force_zero_n':self.force_zero,'acquisition.phase':self.phase,'acquisition.elapsed_s':self.elapsed,'acquisition.points':len(self.rows),'truth.force_n':self.force,'truth.plastic_strain':self.plastic,'truth.total_strain':self._strain(),'truth.mass_kg':self._mass(),'truth.sample_k':self.temperature}
        for side in ['upper','lower']:d['grip.'+side+'_opening_m']=self._pos(side);d['grip.target_'+side+'_opening_m']=self.axes[side]['target']
        return {k:copy.deepcopy(v) for k,v in d.items() if visibility!='agent' or SLOTS[k]['visibility']=='agent'}
    def _propagate(self,ns):
        dt=ns/1e9
        if dt<=0 or not self.plant:return
        x1=self._pos('crosshead')+self._rate()*dt;F0=self.force;p0=self.plastic;F1,p1=self._solve(x1);before=sum(self._stored(F0,p0));after=sum(self._stored(F1,p1));plasticwork=0.
        if self.sample:plasticwork=self.Y*(p1-p0)*self.sample['initial_area_m2']*self.sample['gauge_length_m']
        heat=plasticwork*self.plant['plastic_heat_fraction'];self.book['actuator_work_j']+=after-before+plasticwork;self.book['plastic_work_j']+=plasticwork;self.book['plastic_heat_j']+=heat;self.defect_stored+=plasticwork-heat;self.force=F1;self.plastic=p1
        C=self._capacity();G=self.plant['sample_conductance_w_k'];u0=C*(self.temperature-self.plant['ambient_k']);decay=math.exp(-G*dt/C);u1=u0*decay+(heat/dt)*C/G*(-math.expm1(-G*dt/C));self.temperature=self.plant['ambient_k']+u1/C;self.book['heat_export_j']+=heat-(u1-u0)
        gain=self.plant['force_gain'] if self.power else 0.;f0=gain*F0+(self.plant['force_offset_n'] if self.power else 0.);slope=gain*(F1-F0)/dt;tau=self.plant['force_response_tau_s'];self.sensor_force=f0+slope*(dt-tau)+(self.sensor_force-f0+slope*tau)*math.exp(-dt/tau)
    def _fracture_position(self):
        ep=self.sample['engineering_strain_at_break'];A=self.sample['initial_area_m2'];L=self.sample['gauge_length_m'];H=self.sample['hardening_modulus_pa'];sigma=self.E*(ep-self.plastic)
        if sigma>self.Y+H*self.plastic:sigma=self.E*(self.Y+H*ep)/(self.E+H)
        return L*ep+A*max(0,sigma)/self.plant['frame_stiffness_n_m']
    def _force_position(self,load):
        L=self.sample['gauge_length_m'];A=self.sample['initial_area_m2'];H=self.sample['hardening_modulus_pa'];plastic=self.plastic
        if load>A*(self.Y+H*plastic):
            if H<=0:return math.inf
            plastic=(load/A-self.Y)/H
        return self._compliance()*load+L*plastic
    def _next_material_event(self):
        rate=self._rate();x=self._pos('crosshead')
        if rate<=0 or not self._held() or self.broken:return None
        A=self.sample['initial_area_m2'];yield_x=self._compliance()*A*(self.Y+self.sample['hardening_modulus_pa']*self.plastic)+self._free_extension();limit=min(FIXTURE_MAX_N,self.method['force_stop_n'] if self.phase=='running' else FIXTURE_MAX_N);positions=[(self._fracture_position(),'fracture'),(self._force_position(limit),'force_limit')]
        if yield_x>x+1e-12:positions.append((yield_x,'yield'))
        candidates=[]
        for target,kind in positions:
            if math.isfinite(target) and target>=x-1e-12:candidates.append((self.time_ns+max(1,int(math.ceil((target-x)/rate*1e9-1e-7))),kind))
        return min(candidates) if candidates else None
    def _record(self,kind='sample'):
        F=self._observed_force();quality='estimated'
        if not self._temperature_valid():quality='temperature_invalid'
        elif F is None:quality='overload'
        elif abs(F)<20:quality='below_documented_accuracy_range'
        self.rows.append(dict(point_index=len(self.rows),time_s=self.time_s,kind=kind,force_n=F,crosshead_m=quant(self._pos('crosshead'),POSITION_RESOLUTION_M),extensometer_extension_m=self._observed_extension(),broken=self.broken,quality=quality))
        return F is not None
    def _break(self):
        if self.broken:return
        valid_pre=self._record('fracture_pre') if self.phase=='running' else True
        frame,elastic,_=self._stored();heat=elastic*self.plant['fracture_heat_fraction'];self.fracture_surface+=elastic-heat;self.book['fracture_heat_j']+=heat;self.book['machine_damping_export_j']+=frame;self.temperature+=heat/self._capacity();self.force=0.;self.broken=True
        if self.phase=='running':
            valid_post=self._record('fracture_post')
            if not valid_pre or not valid_post:self._finish('failed','REFERENCE_TEMPERATURE_RANGE' if not self._temperature_valid() else 'LOAD_CELL_OVERFLOW')
            elif self.method['stop_on_break'] or self.mode!='speed':self._finish('succeeded','fracture')
    def _stop_drive(self):self._target('crosshead',self._pos('crosshead'),MIN_SPEED_M_S)
    def _finish(self,phase,reason):
        self._stop_drive();self.phase=phase;self.terminal_reason=reason;self.last_stop_reason=reason;self._sample_ns=self._control_ns=None
        if self.operation_id:self.records[self.operation_id]=dict(operation_id=self.operation_id,phase=phase,mode=self.mode,elapsed_s=self.elapsed,rows=copy.deepcopy(self.rows),terminal_reason=reason,template_source=self._operation_source,evidence='synthetic_forward_model; literature_E_yield_reference_and_explicit_bilinear_hardening_fracture_geometry_thermal_assumptions; actual_external_observations')
    def _result(self):
        if self.phase!='running' and self.operation_id in self.records:return copy.deepcopy(self.records[self.operation_id])
        return dict(operation_id=self.operation_id,phase=self.phase,mode=self.mode,elapsed_s=self.elapsed,rows=copy.deepcopy(self.rows),terminal_reason=self.terminal_reason,template_source=self._operation_source,evidence='synthetic_forward_model; actual_time_samples_and_explicit_fracture_events; not_original_material_test')
    def _control(self):
        if self.mode=='speed':return
        if self.mode=='force':
            observed=self._observed_force()
            if observed is None:self._finish('failed','REFERENCE_TEMPERATURE_RANGE' if not self._temperature_valid() else 'LOAD_CELL_OVERFLOW');return
            error=self.method['target_force_n']-observed;v=self.plant['force_control_gain_m_per_n_s']*error
        else:
            ext=self._observed_extension()
            if ext is None:self._finish('failed','EXTENSOMETER_UNAVAILABLE');return
            error=self.method['target_strain']-ext/self.fixture['extensometer_gauge_m'];v=self.plant['strain_control_gain_m_s']*error
        speed=min(abs(v),self.method['speed_m_s']);target=self.method['target_crosshead_m'] if v>0 else self._free_extension()
        if speed<MIN_SPEED_M_S:self._stop_drive()
        else:self._target('crosshead',target,speed)
    def invoke(self,capability_id,**arguments):
        if capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability_id]['input_schema'],arguments);k=capability_id;a=copy.deepcopy(arguments)
        if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
        if k=='acquisition.result':return self._result()
        if k=='provenance.read':return dict(current_sample=self.source,operation_source=self._operation_source)
        if k=='ledger.read':return self.ledger()
        if k=='analysis.tensile':return analyze_tensile(**a)
        if k=='sim.step':self.step(a['dt_s']);return dict(time_s=self.time_s)
        if self.phase=='running' and k not in ('acquisition.stop','emergency.engage','power.set'):raise ActionRejected('BUSY')
        if k=='plant.configure':
            if self.plant is not None or self.sample or self.power:raise ActionRejected('PLANT_ALREADY_CONFIGURED')
            if not a['accept_engineering']:raise ActionRejected('ENGINEERING_ACCEPTANCE_REQUIRED')
            self.plant=a;self.temperature=a['ambient_k'];result=dict(configured=True)
        elif k=='fixture.configure':
            if self.sample or any(self._moving(x) for x in self.axes):raise ActionRejected('SAMPLE_PRESENT_OR_MOVING')
            if a['extensometer_enabled'] and not a['accept_engineering_extensometer']:raise ActionRejected('ENGINEERING_ACCEPTANCE_REQUIRED')
            self.fixture=a;self.method=None;result=dict(configured=True)
        elif k=='method.configure':
            if not self.fixture:raise ActionRejected('FIXTURE_REQUIRED')
            if a['mode']!='speed' and a['sample_rate_hz']>100:raise ActionRejected('AUTOMATIC_CONTROL_MAX_100HZ')
            if not self.fixture['software_trapezium_x'] and (a['sample_rate_hz']>10 or a['mode']!='speed'):raise ActionRejected('SOFTWARE_OPTION_REQUIRED')
            if a['mode']=='strain' and not self.fixture['extensometer_enabled']:raise ActionRejected('EXTENSOMETER_REQUIRED')
            if a['force_stop_n']>FIXTURE_MAX_N or a['target_force_n']>FIXTURE_MAX_N:raise ActionRejected('FIXTURE_FORCE_LIMIT')
            self.method=a;result=dict(configured=True)
        elif k=='sample.load':
            if not self.plant or not self.fixture:raise ActionRejected('CONFIGURATION_REQUIRED')
            if self.sample:raise ActionRejected('SAMPLE_PRESENT')
            if any(self._moving(x) for x in self.axes) or self._pos('crosshead')>1e-12 or any(self._pos(x)<.006-1e-12 for x in ['upper','lower']):raise ActionRejected('LOADING_GEOMETRY')
            if not a['accept_bilinear_geometry_temperature_fracture_assumptions']:raise ActionRejected('ENGINEERING_ACCEPTANCE_REQUIRED')
            ref=library().get(a['reference_id'])
            if ref is None:raise ActionRejected('UNKNOWN_REFERENCE')
            E=a['host_elastic_modulus_pa'] if a['host_elastic_modulus_pa'] is not None else ref.get('elastic_modulus_Pa');Y=a['host_yield_strength_pa'] if a['host_yield_strength_pa'] is not None else ref.get('yield_strength_Pa')
            if E is None or Y is None or E<=0 or Y<=0:raise ActionRejected('MISSING_CONSTITUTIVE_PRIOR')
            if a['volume_m3']<a['gauge_length_m']*a['initial_area_m2']:raise ActionRejected('VOLUME_BELOW_GAUGE')
            if self.fixture['extensometer_enabled'] and self.fixture['extensometer_gauge_m']>a['gauge_length_m']+1e-12:raise ActionRejected('EXTENSOMETER_GAUGE_OUTSIDE_SAMPLE')
            T=a['initial_temperature_k'];domain=ref.get('valid_temperature_range_K');nominal=ref.get('reference_temperature_K');tol=a['temperature_tolerance_k']
            if nominal is not None and abs(T-nominal)>tol+1e-9 or domain and domain[1]-domain[0]>1e-9 and not domain[0]<=T<=domain[1]:raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
            incoming=a['volume_m3']*a['density_kg_m3']*a['specific_heat_capacity_j_kg_k']*(T-self.plant['ambient_k']);old=self._capacity()*(self.temperature-self.plant['ambient_k']);self.sample=a;self.reference=ref;self.E=float(E);self.Y=float(Y);self.source=json.dumps(dict(reference_id=a['reference_id'],source=ref.get('source'),evidence=ref.get('evidence')),sort_keys=True);self.plastic=0.;self.force=0.;self.broken=False;self.defect_stored=0.;self.fracture_surface=0.;self.temperature=self.plant['ambient_k']+(old+incoming)/self._capacity();self.book['loaded_mass_kg']+=self._mass();self.book['loaded_volume_m3']+=a['volume_m3'];self.book['sensible_loaded_j']+=incoming;result=dict(present=True)
        elif k=='sample.remove':
            if not self.sample:raise ActionRejected('NO_SAMPLE')
            if any(self._moving(x) for x in self.axes) or any(self._pos(x)<.006-1e-12 for x in ['upper','lower']) or abs(self.force)>1e-6:raise ActionRejected('UNLOAD_REQUIRED')
            mass=self._mass();volume=self.sample['volume_m3'];self.book['removed_mass_kg']+=mass;self.book['removed_volume_m3']+=volume;self.book['sensible_removed_j']+=mass*self.sample['specific_heat_capacity_j_kg_k']*(self.temperature-self.plant['ambient_k']);self.book['internal_removed_j']+=sum(self._stored())+self.defect_stored+self.fracture_surface;self.sample=self.reference=self.source=None;self.plastic=0.;self.broken=False;self.force=0.;self.defect_stored=self.fracture_surface=0.;result=dict(removed_mass_kg=mass,removed_volume_m3=volume)
        elif k=='power.set':
            if not self.plant:raise ActionRejected('PLANT_REQUIRED')
            if not a['enabled']:
                if self.phase=='running':self._finish('cancelled','POWER_OFF')
                for axis in self.axes:self._target(axis,self._pos(axis),MIN_SPEED_M_S if axis=='crosshead' else None)
            self.power=a['enabled'];result=dict(enabled=self.power)
        elif k=='grip.set_closed':
            if not self.plant:raise ActionRejected('PLANT_REQUIRED')
            if self._moving('crosshead') or abs(self.force)>1e-6:raise ActionRejected('UNLOAD_REQUIRED')
            if self.sample and a['closed'] and abs(self._pos('crosshead')-self._free_extension())>1e-10:raise ActionRejected('GRIP_PRESTRAIN_UNSUPPORTED')
            target=0. if a['closed'] else .006;self._target(a['which'],target);result=dict(target_opening_m=target)
        elif k=='crosshead.move_to':
            if not self.power:raise ActionRejected('POWER_OFF')
            if self.emergency:raise ActionRejected('EMERGENCY_ENGAGED')
            if self.sample and not self._held():raise ActionRejected('GRIPS_NOT_CLOSED')
            if self.sample and a['position_m']<self._free_extension()-1e-12:raise ActionRejected('COMPRESSION_OR_SLACK_UNSUPPORTED')
            self._target('crosshead',a['position_m'],a['speed_m_s']);result=dict(target_m=a['position_m'])
        elif k=='emergency.engage':
            if self.phase=='running':self._finish('cancelled','EMERGENCY_STOP')
            if self.plant:
                for axis in self.axes:self._target(axis,self._pos(axis),MIN_SPEED_M_S if axis=='crosshead' else None)
            self.emergency=True;result=dict(engaged=True)
        elif k=='emergency.reset':self.emergency=False;result=dict(engaged=False)
        elif k=='force.zero':
            if not self.power:raise ActionRejected('POWER_OFF')
            if abs(self.force)>1e-6 or any(self._moving(x) for x in self.axes):raise ActionRejected('UNLOAD_REQUIRED')
            self.force_zero=self.sensor_force;result=dict(zero_n=self.force_zero)
        elif k=='force.calibrate':
            if not self.power:raise ActionRejected('POWER_OFF')
            if any(self._moving(x) for x in self.axes):raise ActionRejected('MOVING')
            if self.sample and a['reference_force_n']>FIXTURE_MAX_N:raise ActionRejected('FIXTURE_FORCE_LIMIT')
            observation=self.sensor_force-self.force_zero
            if observation<=0:raise ActionRejected('ZERO_CALIBRATION_SIGNAL')
            gain=a['reference_force_n']/observation
            if not math.isfinite(gain) or not .001<=gain<=1000:raise ActionRejected('INVALID_CALIBRATION')
            self.force_gain=gain;result=dict(gain=gain)
        elif k=='acquisition.start':
            if not self.power:raise ActionRejected('POWER_OFF')
            if self.emergency:raise ActionRejected('EMERGENCY_ENGAGED')
            if not self.method or not self.fixture or not self.plant:raise ActionRejected('CONFIGURATION_REQUIRED')
            if not self.sample:raise ActionRejected('NO_SAMPLE')
            if self.broken:raise ActionRejected('SAMPLE_BROKEN')
            if not self._held():raise ActionRejected('GRIPS_NOT_CLOSED')
            if any(self._moving(x) for x in self.axes):raise ActionRejected('MOVING')
            if not self._temperature_valid():raise ActionRejected('REFERENCE_TEMPERATURE_RANGE')
            if self.force>=self.method['force_stop_n']:raise ActionRejected('METHOD_FORCE_STOP')
            if self._observed_force() is None:raise ActionRejected('LOAD_CELL_OVERFLOW')
            if self.method['target_crosshead_m']<self._free_extension()-1e-12:raise ActionRejected('COMPRESSION_OR_SLACK_UNSUPPORTED')
            self._counter+=1;self.operation_id=f'{self.instance_id}:test:{self._counter}';self.phase='running';self.mode=self.method['mode'];self.elapsed=0.;self.rows=[];self.terminal_reason=None;self._operation_source=self.source;self._start_ns=self.time_ns;self._end_ns=self.time_ns+round(self.method['duration_s']*1e9);self._period_ns=1_000_000_000//self.method['sample_rate_hz'];self._sample_ns=self.time_ns+self._period_ns;self._control_ns=self.time_ns+CONTROL_PERIOD_NS if self.mode!='speed' else None
            self._record()
            if self.mode=='speed':self._target('crosshead',self.method['target_crosshead_m'],self.method['speed_m_s'])
            else:self._control()
            result=dict(operation_id=self.operation_id)
        elif k=='acquisition.stop':
            if self.phase!='running':raise ActionRejected('NOT_RUNNING')
            self._finish('cancelled','USER_CANCEL');result=dict(phase=self.phase,elapsed_s=self.elapsed)
        else:raise ActionRejected('UNKNOWN_CAPABILITY')
        self.revision+=1;return copy.deepcopy(result)
    def step(self,dt_s):
        validate(number('s',0,86400),dt_s)
        raw=float(dt_s)*1e9+self._clock_remainder_ns;nanos=round(raw);self._clock_remainder_ns=raw-nanos
        if not nanos:return
        end=self.time_ns+nanos
        while self.time_ns<end:
            candidates=[end]+[a['arrival_ns'] for a in self.axes.values() if a['arrival_ns']>self.time_ns];event=self._next_material_event()
            if event:candidates.append(event[0])
            if self.phase=='running':candidates.extend(x for x in [self._sample_ns,self._control_ns,self._end_ns] if x is not None and x>self.time_ns)
            nxt=min(candidates);self._propagate(nxt-self.time_ns);self.time_ns=nxt;self._positions()
            if self.phase=='running':self.elapsed=(self.time_ns-self._start_ns)/1e9
            if event and event[0]==self.time_ns:
                if event[1]=='fracture':self._break()
                elif event[1]=='force_limit':
                    self.last_stop_reason='FIXTURE_FORCE_LIMIT' if self.force>=FIXTURE_MAX_N-1e-4 else 'METHOD_FORCE_STOP'
                    if self.phase=='running':self._record();self._finish('failed',self.last_stop_reason)
                    else:self._stop_drive()
            if self.phase=='running' and self._sample_ns==self.time_ns:
                valid=self._record();self._sample_ns+=self._period_ns
                if not valid:self._finish('failed','REFERENCE_TEMPERATURE_RANGE' if not self._temperature_valid() else 'LOAD_CELL_OVERFLOW')
            if self.phase=='running' and self._control_ns==self.time_ns:self._control();self._control_ns=self.time_ns+CONTROL_PERIOD_NS if self.phase=='running' else None
            if self.phase=='running' and (self.time_ns>=self._end_ns or self.mode=='speed' and not self._moving('crosshead')):
                valid=self._record() if not self.rows or self.rows[-1]['time_s']!=self.time_s else self.rows[-1]['force_n'] is not None
                if not valid:self._finish('failed','REFERENCE_TEMPERATURE_RANGE' if not self._temperature_valid() else 'LOAD_CELL_OVERFLOW')
                else:self._finish('succeeded','duration_limit' if self.time_ns>=self._end_ns else 'target_reached')
        self.revision+=1

class ShimadzuAgs10knxdSession:
    def __init__(self, host, principal_id): self._host = host; self._principal = principal_id
    @property
    def _role(self): return self._host._principals[self._principal]
    def _target(self, instance_id):
        if instance_id != self._host.instance_id: raise ActionRejected('UNKNOWN_INSTANCE')
    def describe(self, *, instance_id=None, asset_type_id=None):
        if (instance_id is None) == (asset_type_id is None): raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None: self._target(instance_id)
        if asset_type_id is not None and asset_type_id != ASSET_TYPE_ID: raise ActionRejected('UNKNOWN_INSTANCE')
        meta = export_shimadzu_ags10knxd_contract_metadata()
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
            result['evidence'] = 'synthetic_forward_model; actual_force_crosshead_extensometer_observations; literature_reference_and_explicit_bilinear_geometry_thermal_fracture_assumptions; selected record restricted' if result['operation_id'] is not None else 'no_measurement'
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
            result=self._filter_result(h.invoke(capability_id,**arguments)); operation=result.get('operation_id') if capability_id in ('acquisition.start',) else None
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

for i,strain in enumerate([.0002,.0005,.001]):
    CAPS['analysis.tensile']['example_arguments']['rows'].append(dict(point_index=i,time_s=i*.1,kind='sample',force_n=2e11*strain*2e-6,crosshead_m=strain*.025+(2e11*strain*2e-6)/1e8,extensometer_extension_m=strain*.025,broken=False,quality='estimated'))
EFFECTS={
 'plant.configure':['plant.configured','thermal.sample_k','truth.sample_k'],
 'fixture.configure':['fixture.configured','fixture.extensometer_enabled','method.configured'],
 'method.configure':['method.configured'],
 'sample.load':['sample.present','sample.broken','thermal.sample_k','truth.sample_k','truth.mass_kg','extensometer.extension_m','extensometer.strain'],
 'sample.remove':['sample.present','sample.broken','truth.mass_kg','truth.plastic_strain','truth.total_strain','sample.visual_extension_m','sample.fracture_gap_m','extensometer.extension_m','extensometer.strain'],
 'power.set':['power.enabled','power.angle_rad','force.indicated_n','crosshead.indicated_m','extensometer.extension_m','extensometer.strain','crosshead.target_m','grip.target_upper_opening_m','grip.target_lower_opening_m','acquisition.phase'],
 'grip.set_closed':['grip.target_upper_opening_m','grip.target_lower_opening_m'],
 'crosshead.move_to':['crosshead.target_m'],
 'emergency.engage':['emergency.engaged','emergency.position_m','crosshead.target_m','grip.target_upper_opening_m','grip.target_lower_opening_m','acquisition.phase'],
 'emergency.reset':['emergency.engaged','emergency.position_m'],
 'force.zero':['calibration.force_zero_n','force.indicated_n'],
 'force.calibrate':['calibration.force_gain','force.indicated_n'],
 'acquisition.start':['acquisition.phase','acquisition.elapsed_s','acquisition.points','crosshead.target_m'],
 'acquisition.stop':['acquisition.phase','crosshead.target_m'],
 'sim.step':list(SLOTS)}
for k,v in EFFECTS.items():CAPS[k]['effects']=v
ERRORS=sorted(set(__import__('re').findall(r"ActionRejected\('([A-Z_0-9]+)'",Path(__file__).read_text()))|{'USER_CANCEL','EMERGENCY_STOP','LOAD_CELL_OVERFLOW','METHOD_FORCE_STOP'})
LIMITATIONS=[
 '配置AGS-10kNXD高精度10kN传感器、真实SCG5kN手动拉伸夹具、TRAPEZIUM X软件；5000N夹具限值独立于10000N传感器量程，不实现弯曲/剥离/压缩和温控附件。',
 '试验速度0.001..1000mm/min；最大1500mm/min回程不是本试验速度范围。1µm是位移显示分辨，不是定位准确度；±0.5% indicated仅20..10000N厂家条件，不自动变模拟噪声或已测精度。',
 '有选定TRAPEZIUM软件时速度模式最多1000Hz真实采样；厂家自动force/stress或strain控制10ms，因此力/应变控制最多100Hz，工程反馈也严格每10ms更新。无软件选项仅保守10Hz速度模式，不声称OEM所有无软件模式。',
 '材料库真实金属processed true/log曲线及E/0.2%yield和独立聚合物汇总标量是参考，不是本机器原生输出。真实E/Y驱动显式一维双线性代理；硬化H、工程断裂ε、温域常数近似、尺寸迁移和热参数须宿主接受。真实局部断口true strain不冒充engineering break elongation，截断曲线末点不是UTS或断裂。',
 '均匀有效标距采用engineering strain、固定initial area的小变形一维代理，不解颈缩、面积演变、局部损伤或全场应变；源true-stress标量迁移到该代理是工程假设。rate-independent plasticity不是真实全速率材料响应。',
 '横梁q=L*(elastic_strain+plastic_strain)+F/K；K包含机架/夹具串联顺应，不能把q/L直接当试样应变。工程夹持引伸计独立输出标距伸长，可显式不配；面积和标距均需独立先验才分析stress/strain。',
 'H使用储能硬化变量：Uhard=.5*H*ep²*A*L；不可逆plasticwork=Y*d(ep)*A*L，β变热其余保留缺陷能。显式断裂阈值释放sampleelastic至热/新表面，frameelastic导出至机架阻尼；残余塑性及硬化/缺陷能随碎片保留。',
 '准静态一维力平衡不解横梁惯性或真实破断应力波。理想瞬时断裂时F物理突降，传感器仍有一阶响应；断裂pre/post是额外明确事件记录，不能冒充1000Hz时间网格中的额外独立采样。',
 '被动热节点=宿主输入完整样品质量*Cp+局部接触热容，不使用85kg整机质量。塑性只对有效AL积分，完整体积/密度是独立先验并包含未变形tabs；视觉tabs形状不作为精确体积计量。没有主动温控器。',
 '取消/急停/断电停止位移并按自锁丝杆假设保留静载弹性能，之后仍被动换热；不会重置塑性或断裂。带样不允许横梁进入低于残余自由长度的压缩/松弛区域，开夹须F≈0。夹具动作是有限时间手工代理，不声称真实电动SCG。',
 '校准从当前实际传感器观测和外部已知力先验求zero/gain；不存在未接加载标准的自动已知真值，外部力先验没有证书即非计量认证。仅输出校准参数，不声称闭环准确度达标。',
 '固定3D示例自由标距25mm、2mm×1mm，两个15mm夹持tabs。过程可配置其它尺寸但当前视觉仍是固定示例，不声称自动匹配任意试样。sample.present=false须隐藏试样，卸样后q可非零。',
 '所有普通Session acquisition.result/poll跨角色统一隐藏选中材料身份，独立provenance.read仅evaluator；共享磁盘/Python不是操作系统隔离。实机驱动、机器人、经验噪声与OEM/ASTM/ISO认证均not_run。']

def export_shimadzu_ags10knxd_contract_metadata():
    slots={};caps={}
    for k,v in SLOTS.items():slots[k]=dict(meaning_zh=v['meaning'],component_ids=[v['component']],value_schema=copy.deepcopy(v['schema']),unit=v['unit'],reference_conditions='SI; 10kN high-precision load cell / 5kN SCG tensile fixture / configured software and engineering extensometer.',origin=v['origin'],visibility=v['visibility'],access='read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'ShimadzuAgs10knxdSession.read(state_ids=[{k!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description='Pure read; sampling at configured fixed time grid with separately labelled fracture event observations.'),source_ids=['simulation_assumption'])
    for k,v in CAPS.items():
        un=k in UNSUPPORTED;async_=k=='acquisition.start';caps[k]={key:copy.deepcopy(v[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']}
        caps[k].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ('scene','robot','hardware') else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.shimadzu_ags10knxd.ShimadzuAgs10knxdSession.invoke',invocation=f'session.invoke(instance_id="ags_01",capability_id={k!r},arguments={v["example_arguments"]!r},request_id="unique")'),preconditions=[] if un or v['kind'] in ('query','analysis') else [dict(description_zh='入口原子核对夹具/软件、载荷、夹持和独立材料/热/断裂先验；采样与控制必须显式step。',state_ids=['power.enabled','sample.present','fixture.configured','emergency.engaged','acquisition.phase'],enforced_by='runtime',check='ShimadzuAgs10knxdRuntime.invoke')],state_effects=[dict(state_id=s,timing='on_step' if k=='sim.step' else 'immediate',description_zh='声明设置或观测变化，真实驱动/热/传感器过程由step推进') for s in v['effects']],observes=[],model_effects=dict(mode='none',description_zh='运行层输出绑定状态，实际几何、双片变形和读数联动由独立scene验证。',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='实际有限位移/时限/明确破断条件完成；力/应变控制为限时反馈，完成不等于误差为零' if async_ else '原子命令，step为唯一物理时钟',completion_state_ids=['acquisition.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','crosshead','tensile_fixture','load_cell','extensometer'],partial_failure_zh='保留已采样点、真实位移/力、塑性、断裂和全部热/弹性/缺陷能账。'),errors=[dict(code=e,meaning_zh=e,side_effects='partial' if async_ and e in ('USER_CANCEL','EMERGENCY_STOP','LOAD_CELL_OVERFLOW','REFERENCE_TEMPERATURE_RANGE','FIXTURE_FORCE_LIMIT','METHOD_FORCE_STOP') else 'none') for e in ERRORS],verification=[],limitations=LIMITATIONS,source_ids=['manufacturer_primary','simulation_assumption','template_library'])
        if k in ('acquisition.start','crosshead.move_to','grip.set_closed','power.set','emergency.engage'):
            caps[k]['state_effects'] += [dict(state_id=s,timing='on_step',description_zh='由统一宿主时钟推进准静态本构、被动热和实际传感器响应') for s in SLOTS if s not in v['effects'] and SLOTS[s]['origin'] in ('sensor','derived','scene','process_truth')]
    mechanism=dict(component_ids=['crosshead','compliance_carriage','load_cell','upper_grip','lower_grip','specimen_lower','specimen_upper','extensometer','controller'],state_ids=list(slots),meaning_zh='串联顺应的拉伸双线性塑性、明确破断与可核算热/储能；独立传感器、固定采样和10ms反馈时钟',implementation='lumped',equations=['q=L*(ep+F/(A*E))+F/K; engineering strain=ep+F/(A*E).','yield stress=Y+H*ep; tensile return mapping enforces irreversible ep and quasi-static series equilibrium.','Uelastic=F²/(2K)+0.5*(F/A)²/E*A*L; Uhardening=0.5*H*ep²*A*L.','dWactuator=d(Uelastic+Uhardening)+Y*A*L*d(ep); Qplastic=beta*Y*A*L*d(ep), remaining work stored as defect energy.','At host-assumed fracture strain, sample elastic energy partitions into heat/surface; frame elastic energy exports as damping. ep and hardening/defect/surface energy remain with fragments.','C*dT/dt=-G*(T-Tambient)+Pplastic; sample load/remove transfers physical mass, volume, sensible heat and internal inventory.','tau*dFdet/dt=gain*F+offset-Fdet; Fobs=round((Fdet-zero)*cal_gain/res)*res.','Calibration cal_gain=external_known_force/(actual_detector_observation-zero); no hidden truth is used.','External analysis: sigma=Fobs/Aprior; strain=extension_observed/Lprior or (qobs-Fobs*Cframe_prior)/Lprior; no joint inversion of unknown geometry.','Sampling grid up to1000Hz with selected software; automatic force/strain feedback and associated sampling max100Hz. Fractional-nanosecond carry and yield/fracture/axis events maintain partition invariance.'],parameters={},source_ids=['simulation_assumption','template_library'],limitations=LIMITATIONS)
    spec=importlib.util.spec_from_file_location('_ags_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()});ann.annotate_export(SLUG,caps,slots,{'uniaxial_tension':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'fixture.configure':(FIXTURE,FIXTURE_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
    for k,v in SLOTS.items():slots[k]['meaning_zh']=v['meaning']
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'uniaxial_tension':mechanism})

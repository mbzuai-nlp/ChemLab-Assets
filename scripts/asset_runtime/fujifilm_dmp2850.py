"""CHEM-058 S/SAMBA: serial step-and-fire, finite ink, ballistic drops; explicitly uncalibrated."""
from __future__ import annotations
import copy, importlib.util, json, math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SLUG='fujifilm_dmp2850'; ASSET_TYPE_ID='instrument.'+SLUG; CATALOG_ID='CHEM-058'; PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/fujifilm_dmp2850_ink_library/templates.json'
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





PITCH=.0254/75;DROP_VOLUME=2.4e-15;G=9.80665
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
COUNT=dict(type='integer',minimum=0,maximum=10000000,**{'x-unit':'1'})
INDEX=dict(type='integer',minimum=0,maximum=11,**{'x-unit':'1'})
X=number('m',-.23,.23);Y=number('m',-.16,.16);Z=number('m',0,.03);ANGLE=number('rad',0,math.pi/2)
PLANT=obj({'ambient_k':number('K',288.15,313.15),'axis_speed_m_s':number('m/s',.001,.5),'manual_speed_m_s':number('m/s',.0001,.1),'manual_angle_rad_s':number('rad/s',.001,2),'platen_capacity_j_k':number('J/K',10,10000),'head_capacity_j_k':number('J/K',10,10000),'platen_heater_w':number('W',.1,300),'head_heater_w':number('W',.1,75),'loss_w_k':number('W/K',0,10),'controller_w_k':number('W/K',.1,100),'mass_transfer_m_s':number('m/s',0,.01),'cleaning_flow_m3_s':number('m^3/s',1e-15,1e-7),'accept_uncalibrated_model':BOOL})
PLANT_EXAMPLE=dict(ambient_k=298.15,axis_speed_m_s=.1,manual_speed_m_s=.01,manual_angle_rad_s=1.,platen_capacity_j_k=100.,head_capacity_j_k=20.,platen_heater_w=60.,head_heater_w=10.,loss_w_k=.1,controller_w_k=5.,mass_transfer_m_s=.0001,cleaning_flow_m3_s=1e-10,accept_uncalibrated_model=True)
INK=obj({'solvent_id':TEXT,'solvent_kg':number('kg',1e-15,.005),'solute_kg':number('kg',0,.005),'solvent_density_kg_m3':number('kg/m^3',100,3000),'solute_density_kg_m3':number('kg/m^3',100,10000),'viscosity_pa_s':number('Pa*s',.004,.008),'surface_tension_n_m':number('N/m',.028,.032),'contact_angle_rad':number('rad',.1,2.9),'reference_voltage_v':number('V',1,40),'reference_pulse_s':number('s',1e-7,1e-3),'reference_volume_m3':number('m^3',1e-16,1e-13),'reference_velocity_m_s':number('m/s',.1,20),'accept_compatibility_and_calibration_assumption':BOOL})
INK_EXAMPLE=dict(solvent_id='ethanol',solvent_kg=789e-7,solute_kg=1200e-8,solvent_density_kg_m3=789.,solute_density_kg_m3=1200.,viscosity_pa_s=.006,surface_tension_n_m=.030,contact_angle_rad=math.pi/3,reference_voltage_v=24.,reference_pulse_s=6e-6,reference_volume_m3=DROP_VOLUME,reference_velocity_m_s=5.,accept_compatibility_and_calibration_assumption=True)
SAMPLE=obj({'width_m':number('m',.001,.208),'length_m':number('m',.001,.308),'thickness_m':number('m',.00005,.025),'accept_host_attachment':BOOL})
SAMPLE_EXAMPLE=dict(width_m=.1,length_m=.1,thickness_m=.0002,accept_host_attachment=True)
ROWS=dict(type='array',items=TEXT,minItems=1,maxItems=16)
PATTERN=obj({'name':TEXT,'rows':ROWS,'x_m':number('m',0,.208),'y_m':number('m',0,.308)})
PATTERN_EXAMPLE=dict(name='binary_cross',rows=['010','111','010'],x_m=.05,y_m=.05)
METHOD=obj({'drop_spacing_m':number('m',1e-6,PITCH),'gap_m':number('m',.0001,.003),'nozzles':dict(type='array',items=INDEX,minItems=1,maxItems=12),'voltage_v':number('V',1,40),'rise_s':number('s',1e-7,1e-3),'dwell_s':number('s',1e-7,1e-3),'fall_s':number('s',1e-7,1e-3),'frequency_hz':number('Hz',1,15000),'clean_every_drops':dict(type='integer',minimum=0,maximum=256,**{'x-unit':'1'}),'clean_duration_s':number('s',.001,60)})
METHOD_EXAMPLE=dict(drop_spacing_m=20e-6,gap_m=.001,nozzles=[0,5,11],voltage_v=24.,rise_s=2e-6,dwell_s=4e-6,fall_s=2e-6,frequency_hz=1000.,clean_every_drops=0,clean_duration_s=.05)
RESULT=obj({'phase':PHASE,'stage':TEXT,'x_m':X,'y_m':Y,'z_m':Z,'angle_rad':ANGLE,'platen_k':number('K',1,400),'cartridge_k':number('K',1,400),'drops':COUNT,'cleanings':COUNT,'evidence':TEXT,'template_source':TEXT})
PRODUCT_FIELDS={loc+'_'+sp+'_kg':number('kg',0) for loc in ['initial','cartridge','flight','deposit','waste','evaporated','collected'] for sp in ['solvent','solute']}
SPOT=obj({'x_m':number('m'),'y_m':number('m'),'radius_m':number('m',0),'solvent_kg':number('kg',0),'solute_kg':number('kg',0),'nozzle':INDEX})
PRODUCT_FIELDS.update(spots=dict(type='array',items=SPOT,maxItems=256),heater_input_j=number('J',0),thermal_loss_j=number('J'),sensible_j=number('J'),evidence=TEXT,template_source=TEXT)
PRODUCT=obj(PRODUCT_FIELDS)
CAMERA=obj({'width':dict(type='integer',minimum=64,maximum=64,**{'x-unit':'1'}),'height':dict(type='integer',minimum=64,maximum=64,**{'x-unit':'1'}),'pixels':dict(type='array',items=dict(type='integer',minimum=0,maximum=255,**{'x-unit':'1'}),minItems=4096,maxItems=4096),'pixel_size_m':number('m',1e-9),'delay_s':number('s',0),'drop_diameter_m':number('m',0),'center_z_m':number('m',0),'evidence':TEXT,'template_source':TEXT})
CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=()):CAPS[k]=(i,o,c,z,kind,vis,ex or {},list(effects))
for key,s,ex,v,c in [('plant',PLANT,PLANT_EXAMPLE,'evaluator','body'),('ink',INK,INK_EXAMPLE,'evaluator','cartridge'),('substrate',SAMPLE,SAMPLE_EXAMPLE,'evaluator','substrate'),('pattern',PATTERN,PATTERN_EXAMPLE,'agent','data_port'),('method',METHOD,METHOD_EXAMPLE,'agent','cartridge')]:
    cap(key+'.configure',s,obj({'configured':BOOL}),[c],'显式宿主配置/有限装料/二值图案/波形','configuration',v,ex,[key+'.configured']+(['truth.ledger'] if key in ['ink','substrate'] else []))
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet'],'主机电源宿主边界；无伪造实体电源按钮',ex={'enabled':True},effects=['power.enabled','cycle.phase','thermal.enabled','vacuum.enabled','safety.fault'])
cap('vacuum.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['platen','vacuum_pump'],'宿主吸附事件，不等于真实漏气与接触约束','configuration','evaluator',{'enabled':True},['vacuum.enabled'])
cap('axis.move_to',obj({'x_m':X,'y_m':Y,'z_m':Z}),obj({'phase':PHASE,'operation_id':TEXT}),['head_carriage','head_lift','platen'],'宿主空程定位代理，运动不瞬时完成',ex={'x_m':.03,'y_m':-.02,'z_m':.006},effects=['axis.x_target_m','axis.y_target_m','axis.z_target_m','cycle.phase'])
cap('cartridge.set_angle',obj({'angle_rad':ANGLE}),obj({'angle_rad':ANGLE}),['cartridge'],'手动墨盒旋转目标，非真实电动关节','configuration','agent',{'angle_rad':math.asin(20e-6/PITCH)},['cartridge.target_angle_rad'])
cap('thermal.set_temperature',obj({'platen_k':number('K',288.15,333.15),'cartridge_k':number('K',288.15,333.15)}),obj({'platen_k':number('K',288.15,333.15),'cartridge_k':number('K',288.15,333.15)}),['platen','cartridge'],'室温至60℃双温度目标，非瞬时加热',ex={'platen_k':313.15,'cartridge_k':308.15},effects=['thermal.platen_target_k','thermal.cartridge_target_k'])
cap('thermal.set_enabled',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['platen','cartridge'],'双节点加热开关，关机继续被动散热',ex={'enabled':True},effects=['thermal.enabled'])
for k,c,z in [('acquisition.start',['cartridge','nozzle_plate','substrate'],'二值图案串行逐点喷射，不冒充12喷嘴并行调度'),('cleaning.start',['clean_station','waste_receiver'],'移到维护站后有限清洗耗墨'),('dropwatch.start',['dropwatch_camera','cartridge'],'维护站单滴观察；耗墨落入废料')]:
    cap(k,obj({}),obj({'phase':PHASE,'operation_id':TEXT}),c,z,effects=['cycle.phase','cycle.stage','cycle.elapsed_s','axis.x_target_m','axis.y_target_m','axis.z_target_m','cartridge.target_angle_rad'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['head_carriage','platen','cartridge'],'停止保留已喷物料；在飞液滴继续落下',effects=['cycle.phase','axis.x_target_m','axis.y_target_m','axis.z_target_m','cartridge.target_angle_rad'])
cap('printing.measure',obj({}),RESULT,['display_digits'],'理想运动/温度/计数读数，不是实测定位或膜厚仪','query')
cap('dropwatch.observe',obj({'delay_s':number('s',0,.02)}),CAMERA,['dropwatch_camera'],'最后实际发射滴的虚拟频闪投影；不推进时钟','query','agent',{'delay_s':.0001})
cap('product.read',obj({}),PRODUCT,['substrate','cartridge','waste_receiver'],'有限墨料双物种账本及合成沉积圆斑','query','evaluator')
cap('product.collect',obj({'recipient_id':TEXT}),obj({'recipient_id':TEXT,'attachment_event':TEXT}),['substrate'],'静止且无在飞液滴后宿主释放基片','device_command','evaluator',{'recipient_id':'patterned_slide'},['substrate.configured','vacuum.enabled','truth.ledger'])
cap('ink.drain',obj({}),obj({'emptied':BOOL}),['cartridge','waste_receiver'],'将墨盒余料转废料；不可无限补墨','configuration','evaluator',{},['truth.ledger'])
cap('reference.lookup',obj({'substance_id':TEXT,'temperature_k':number('K',1)}),obj({'vapor_pressure_pa':number('Pa',0),'evidence':TEXT,'source':TEXT,'template_source':TEXT}),['cartridge'],'真实NIST纯溶剂参考，不外推','query','agent',{'substance_id':'ethanol','temperature_k':298.15})
REF_T=298.15;REF_TOL=.5
FIT_EVIDENCE={'nist_srd69_saturation_fit':'derived_NIST_SRD69_saturation_Antoine_fit_no_extrapolation','thermoml_vapour_pressure_fit':'derived_ThermoML_article_Antoine_fit_no_extrapolation','clausius_clapeyron_local':'derived_local_Clausius_Clapeyron_PubChem_point_NIST_dHvap_no_extrapolation'}
PROPS_OUT=obj({'temperature_k':number('K',0),'density_kg_m3':number('kg/m^3',0),'viscosity_pa_s':number('Pa*s',0),'surface_tension_n_m':number('N/m',0),'vapor_pressure_pa':number('Pa',0),'normal_boiling_point_k':number('K',0),'molar_mass_kg_mol':number('kg/mol',0),'evidence':TEXT,'source':TEXT,'template_source':TEXT})
cap('reference.properties',obj({'substance_id':TEXT}),PROPS_OUT,['cartridge'],'298.15K真实纯溶剂物性（密度/黏度/表面张力/蒸气压/沸点；NIST SRD69、ThermoML实测中位数、PubChem），记录不完整则拒绝','query','agent',{'substance_id':'toluene'})
PRINT_IN=obj({'substance_id':TEXT,'characteristic_length_m':number('m',1e-6,1e-3),'drop_velocity_m_s':number('m/s',.1,20)})
PRINT_OUT=obj({'reynolds':number('1',0),'weber':number('1',0),'ohnesorge':number('1',0),'z_number':number('1',0),'density_kg_m3':number('kg/m^3',0),'viscosity_pa_s':number('Pa*s',0),'surface_tension_n_m':number('N/m',0),'within_cartridge_viscosity_window':BOOL,'within_cartridge_surface_tension_window':BOOL,'within_z_1_to_10':BOOL,'evidence':TEXT,'source':TEXT,'template_source':TEXT})
cap('ink.printability',PRINT_IN,PRINT_OUT,['cartridge'],'由库内298.15K真实物性计算Re/We/Oh/Z及SAMBA理想物性窗口；Z∈(1,10)为文献经验判据，非本机实测可喷性','query','agent',{'substance_id':'ethylene_glycol','characteristic_length_m':round((6*DROP_VOLUME/math.pi)**(1/3),9),'drop_velocity_m_s':5.})
CAPS['plant.configure'][7].extend(['thermal.platen_k','thermal.cartridge_k','thermal.platen_target_k','thermal.cartridge_target_k'])
CAPS['power.set'][7].extend(['cycle.stage','axis.x_target_m','axis.y_target_m','axis.z_target_m','cartridge.target_angle_rad'])
CAPS['acquisition.stop'][7].append('cycle.stage')
CAPS['axis.move_to'][7].extend(['cycle.stage','cycle.elapsed_s'])
for key in ['acquisition.start','dropwatch.start']:CAPS[key][7].append('jet.active_nozzle')
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='body',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
for k in ['plant','ink','substrate','pattern','method']:slot(k+'.configured',BOOL,'1',k+'配置或存在标志','setpoint')
slot('power.enabled',BOOL,'1','主机电源宿主边界','setpoint',c='power_inlet',w=('power.set','enabled'))
slot('vacuum.enabled',BOOL,'1','宿主已接受吸附事件，非真空压力传感器','scene',c='platen')
for k,s,c in [('x',X,'head_carriage'),('y',Y,'platen'),('z',Z,'head_lift')]:
    slot('axis.'+k+'_m',s,'m','运动学'+k+'位移示值；X向右/Y向前/Z向上','scene',c=c)
    slot('axis.'+k+'_target_m',s,'m',k+'目标位移','setpoint',c=c)
slot('cartridge.angle_rad',ANGLE,'rad','SAMBA阵列绕Z角度，喷嘴局部X节距投影到Y','scene',c='cartridge')
slot('cartridge.target_angle_rad',ANGLE,'rad','宿主手动墨盒角度目标','setpoint',c='cartridge',w=('cartridge.set_angle','angle_rad'))
for name in ['platen','cartridge']:
    slot('thermal.'+name+'_k',number('K',1,400),'K','集总'+name+'理想读数，不含校准误差',c='platen' if name=='platen' else 'cartridge')
    slot('thermal.'+name+'_target_k',number('K',288.15,333.15),'K',name+'温度目标','setpoint')
slot('thermal.enabled',BOOL,'1','两个热节点加热使能','setpoint',w=('thermal.set_enabled','enabled'))
slot('cycle.phase',PHASE,'1','异步操作状态')
slot('cycle.stage',TEXT,'1','移动/等待/飞行/清洗子阶段')
slot('cycle.elapsed_s',number('s',0),'s','当前异步操作宿主累计时间')
slot('jet.drop_count',COUNT,'1','实际发射滴计数，清洗连续耗料不计为滴')
slot('jet.active_nozzle',INDEX,'1','最近发射喷嘴号，无滴时默认0')
slot('cleaning.count',COUNT,'1','完成清洗周期数')
slot('safety.fault',TEXT,'1','有限墨量耗尽或参考温区异常；不是真实固件错误码')
slot('truth.ledger',PRODUCT,'1','有限单墨盒双组分及沉积坐标、集总加热账本','process_truth','evaluator','substrate')

class FujifilmDmp2850Runtime:
    def __init__(self,instance_id='dmp2850_01'):
        self.instance_id=instance_id;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']}
        self.time_s=0.;self.revision=0;self.plant=None;self.ink_cfg=None;self.sample=None;self.sample_present=False;self.pattern=None;self.method=None
        self.powered=False;self.vacuum=False;self.heating=False;self.q=[0.,0.,0.,0.];self.target=self.q[:];self.temps=[298.15,298.15];self.temp_targets=self.temps[:]
        self.heater_input=0.;self.thermal_loss=0.;self.initial=[0.,0.];self.cartridge=[0.,0.];self.waste=[0.,0.];self.evaporated=[0.,0.];self.collected=[0.,0.];self.spots=[];self.flight=None;self.last_drop=None
        self.phase='idle';self.stage='idle';self.purpose='';self.elapsed_s=0.;self.wait_left=0.;self.clean_left=0.;self.drop_count=0;self.cleanings=0;self.nozzle=0;self.fault='';self.points=[];self.point_index=0;self.job_drops=0
        self.operation_id='';self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
    def register_principal(self,principal_id,visibility='agent'):
        if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id='__public_agent__'):
        if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return FujifilmDmp2850Session(self,principal_id)
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
    def _printability(self,a):
        p=self._properties(a['substance_id']);rho,mu,sig=p['density_kg_m3'],p['viscosity_pa_s'],p['surface_tension_n_m'];L=a['characteristic_length_m'];v=a['drop_velocity_m_s']
        oh=mu/math.sqrt(rho*sig*L)
        return dict(reynolds=rho*v*L/mu,weber=rho*v*v*L/sig,ohnesorge=oh,z_number=1/oh,density_kg_m3=rho,viscosity_pa_s=mu,surface_tension_n_m=sig,
                    within_cartridge_viscosity_window=.004<=mu<=.008,within_cartridge_surface_tension_window=.028<=sig<=.032,within_z_1_to_10=1<1/oh<10,
                    evidence='derived_dimensionless_groups_from_library_298.15K_properties;Z_window_is_literature_rule_of_thumb_not_SAMBA_measurement;'+p['evidence'],source=p['source'],template_source=TEMPLATE_REL)
    def _volume(self,m):
        c=self.ink_cfg
        return m[0]/c['solvent_density_kg_m3']+m[1]/c['solute_density_kg_m3'] if c else 0.
    def _product(self):
        deposited=[sum(s['m'][i] for s in self.spots) for i in range(2)]
        vals={loc+'_'+sp+'_kg':m[i] for loc,m in [('initial',self.initial),('cartridge',self.cartridge),('flight',self.flight['m'] if self.flight else [0.,0.]),('deposit',deposited),('waste',self.waste),('evaporated',self.evaporated),('collected',self.collected)] for i,sp in enumerate(['solvent','solute'])}
        vals.update(spots=[dict(x_m=s['x'],y_m=s['y'],radius_m=s['radius'],solvent_kg=s['m'][0],solute_kg=s['m'][1],nozzle=s['n']) for s in self.spots],
          heater_input_j=self.heater_input,thermal_loss_j=self.thermal_loss,sensible_j=sum(C*(t-self.plant['ambient_k']) for C,t in zip([self.plant['platen_capacity_j_k'],self.plant['head_capacity_j_k']],self.temps)) if self.plant else 0.,
          evidence='synthetic_drop_flight_spherical_cap;real_NIST_pure_solvent_reference;NOT_measured_ink',template_source=TEMPLATE_REL)
        return vals
    def read(self,visibility='agent'):
        if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
        v={'plant.configured':self.plant is not None,'ink.configured':self.ink_cfg is not None,'substrate.configured':self.sample_present,'pattern.configured':self.pattern is not None,'method.configured':self.method is not None,
          'power.enabled':self.powered,'vacuum.enabled':self.vacuum,'cartridge.angle_rad':self.q[3],'cartridge.target_angle_rad':self.target[3],'thermal.enabled':self.heating,
          'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.elapsed_s':self.elapsed_s,'jet.drop_count':self.drop_count,'jet.active_nozzle':self.nozzle,'cleaning.count':self.cleanings,'safety.fault':self.fault,'truth.ledger':self._product()}
        for j,a in enumerate(['x','y','z']):v['axis.'+a+'_m']=self.q[j];v['axis.'+a+'_target_m']=self.target[j]
        for j,a in enumerate(['platen','cartridge']):v['thermal.'+a+'_k']=self.temps[j];v['thermal.'+a+'_target_k']=self.temp_targets[j]
        return copy.deepcopy({k:x for k,x in v.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
    def _stop(self,phase='cancelled'):
        if self.phase=='running':self.phase=phase;self.stage='stopped'
        self.target=self.q[:]
    def _start(self,purpose,stage):
        self.phase='running';self.purpose=purpose;self.stage=stage;self.elapsed_s=0.;self._operation_count+=1;self.operation_id=self.instance_id+':job:'+str(self._operation_count)
        return dict(phase=self.phase,operation_id=self.operation_id)
    def _factor(self,m=None,c=None):
        m=m or self.method;c=c or self.ink_cfg
        return m['voltage_v']*(m['dwell_s']+(m['rise_s']+m['fall_s'])/2)/(c['reference_voltage_v']*c['reference_pulse_s'])
    def _validate_pattern(self,p,m,s):
        if p is None or m is None or s is None:return
        xs=[p['x_m']+i*m['drop_spacing_m'] for row in p['rows'] for i,v in enumerate(row) if v=='1'];ys=[p['y_m']+j*m['drop_spacing_m'] for j,row in enumerate(p['rows']) if '1' in row]
        length=min(s['length_m'],.308 if s['thickness_m']<.0005 else .253)
        if not xs or min(xs)<0 or max(xs)>s['width_m'] or min(ys)<0 or max(ys)>length:raise ActionRejected('PATTERN_OUT_OF_BOUNDS')
    def _schedule_point(self):
        x,y,n=self.points[self.point_index];angle=math.asin(self.method['drop_spacing_m']/PITCH);d=(n-5.5)*PITCH
        self.nozzle=n;self.target=[x-.104-d*math.cos(angle),y-.154-d*math.sin(angle),self.sample['thickness_m']+self.method['gap_m'],angle];self.stage='move'
    def _schedule_station(self,stage):
        self.target=[-.2,0.,self.method['gap_m'],math.asin(self.method['drop_spacing_m']/PITCH)];self.stage=stage
    def invoke(self,capability_id,**arguments):
        if capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability_id][0],arguments);a=copy.deepcopy(arguments);k=capability_id
        if k=='reference.lookup':return self._reference(a['substance_id'],a['temperature_k'])
        if k=='reference.properties':return self._properties(a['substance_id'])
        if k=='ink.printability':return self._printability(a)
        if k=='product.read':return self._product()
        if k=='printing.measure':return dict(phase=self.phase,stage=self.stage,x_m=self.q[0],y_m=self.q[1],z_m=self.q[2],angle_rad=self.q[3],platen_k=self.temps[0],cartridge_k=self.temps[1],drops=self.drop_count,cleanings=self.cleanings,evidence='synthetic_ideal_kinematics_and_lumped_thermal',template_source=TEMPLATE_REL)
        if k=='dropwatch.observe':return self._camera(a['delay_s'])
        if k=='plant.configure':
            if self.plant is not None or not a['accept_uncalibrated_model']:raise ActionRejected('PRECONDITION_FAILED')
            self.plant=a;self.temps=[a['ambient_k']]*2;self.temp_targets=self.temps[:];r=dict(configured=True)
        elif k=='power.set':
            if a['enabled'] and self.plant is None:raise ActionRejected('PRECONDITION_FAILED')
            self.powered=a['enabled']
            if not self.powered:self._stop();self.heating=False;self.vacuum=False
            else:self.fault=''
            r=dict(enabled=self.powered)
        elif k=='acquisition.stop':self._stop();r=dict(phase=self.phase)
        else:
            if self.plant is None:raise ActionRejected('PRECONDITION_FAILED')
            if self.phase=='running':raise ActionRejected('BUSY')
            if k=='ink.configure':
                if self.ink_cfg is not None or self.flight or not a['accept_compatibility_and_calibration_assumption']:raise ActionRejected('PRECONDITION_FAILED')
                if a['solvent_kg']/a['solvent_density_kg_m3']+a['solute_kg']/a['solute_density_kg_m3']>1.5e-6:raise ActionRejected('CARTRIDGE_CAPACITY')
                for t in self.temps+self.temp_targets:self._reference(a['solvent_id'],t)
                if self.method and not .25<=self._factor(c=a)<=2:raise ActionRejected('UNCALIBRATED_WAVEFORM_RANGE')
                self.ink_cfg=a;self.initial=[a['solvent_kg'],a['solute_kg']];self.cartridge=self.initial[:];r=dict(configured=True)
            elif k=='substrate.configure':
                if self.sample_present or self.flight or not a['accept_host_attachment'] or a['length_m']>(.308 if a['thickness_m']<.0005 else .253):raise ActionRejected('PRECONDITION_FAILED')
                self._validate_pattern(self.pattern,self.method,a)
                self.sample=a;self.sample_present=True;r=dict(configured=True)
            elif k=='pattern.configure':
                rows=a['rows']
                if any(not row or len(row)>16 or set(row)-{'0','1'} or len(row)!=len(rows[0]) for row in rows) or not any('1' in row for row in rows):raise ActionRejected('INVALID_PATTERN')
                self._validate_pattern(a,self.method,self.sample if self.sample_present else None)
                self.pattern=a;r=dict(configured=True)
            elif k=='method.configure':
                if len(set(a['nozzles']))!=len(a['nozzles']) or sum(a[x] for x in ['rise_s','dwell_s','fall_s'])*a['frequency_hz']>1:raise ActionRejected('INVALID_ARGUMENT')
                if self.ink_cfg and not .25<=self._factor(m=a)<=2:raise ActionRejected('UNCALIBRATED_WAVEFORM_RANGE')
                self._validate_pattern(self.pattern,a,self.sample if self.sample_present else None)
                self.method=a;r=dict(configured=True)
            elif k=='vacuum.set':
                if a['enabled'] and (not self.powered or not self.sample_present):raise ActionRejected('PRECONDITION_FAILED')
                if self.flight:raise ActionRejected('PRECONDITION_FAILED')
                self.vacuum=a['enabled'];r=dict(enabled=self.vacuum)
            elif k=='cartridge.set_angle':
                if self.flight:raise ActionRejected('PRECONDITION_FAILED')
                self.target[3]=a['angle_rad'];r=a
            elif k=='axis.move_to':
                if not self.powered or self.flight:raise ActionRejected('PRECONDITION_FAILED')
                if self.sample_present and a['z_m']<self.sample['thickness_m']+.0001:raise ActionRejected('PRECONDITION_FAILED')
                self.target[:3]=[a['x_m'],a['y_m'],a['z_m']];r=self._start('position','move')
            elif k=='thermal.set_temperature':
                ts=[a['platen_k'],a['cartridge_k']]
                if min(ts)<self.plant['ambient_k']:raise ActionRejected('PRECONDITION_FAILED')
                if self.ink_cfg:
                    for t in ts:self._reference(self.ink_cfg['solvent_id'],t)
                self.temp_targets=ts;r=a
            elif k=='thermal.set_enabled':
                if a['enabled'] and not self.powered:raise ActionRejected('PRECONDITION_FAILED')
                self.heating=a['enabled'];r=a
            elif k in ['acquisition.start','dropwatch.start','cleaning.start']:
                if not self.powered or self.method is None or self.ink_cfg is None or self.flight or self.fault:raise ActionRejected('PRECONDITION_FAILED')
                if self._volume(self.cartridge)<=0:raise ActionRejected('INK_EMPTY')
                if k=='acquisition.start':
                    if not self.vacuum or not self.sample_present or self.pattern is None or self.spots:raise ActionRejected('PRECONDITION_FAILED')
                    self._validate_pattern(self.pattern,self.method,self.sample)
                    self.points=[(self.pattern['x_m']+col*self.method['drop_spacing_m'],self.pattern['y_m']+row*self.method['drop_spacing_m'],self.method['nozzles'][row%len(self.method['nozzles'])]) for row,chars in enumerate(self.pattern['rows']) for col,val in enumerate(chars) if val=='1']
                    self.point_index=0;self.job_drops=0;r=self._start('printing','move');self._schedule_point()
                else:
                    purpose='dropwatch' if k=='dropwatch.start' else 'cleaning'
                    if self.sample_present and self.q[2]<self.sample['thickness_m']+.0001:raise ActionRejected('PRECONDITION_FAILED')
                    r=self._start(purpose,'station_move');self._schedule_station('station_move');self.nozzle=self.method['nozzles'][0]
            elif k=='ink.drain':
                if self.flight or self.ink_cfg is None:raise ActionRejected('PRECONDITION_FAILED')
                self.waste=[x+y for x,y in zip(self.waste,self.cartridge)];self.cartridge=[0.,0.];r=dict(emptied=True)
            elif k=='product.collect':
                if self.flight or not self.sample_present or not a['recipient_id']:raise ActionRejected('PRECONDITION_FAILED')
                for s in self.spots:self.collected=[x+y for x,y in zip(self.collected,s['m'])]
                self.spots=[];self.sample_present=False;self.vacuum=False;r=dict(recipient_id=a['recipient_id'],attachment_event='host_release_substrate')
            else:raise ActionRejected('UNKNOWN_CAPABILITY')
        self.revision+=1;return copy.deepcopy(r)
    def _take_volume(self,volume):
        total=self._volume(self.cartridge);fraction=min(1.,volume/total) if total>0 else 0.
        m=[x*fraction for x in self.cartridge];self.cartridge=[max(0.,x-y) for x,y in zip(self.cartridge,m)];return m
    def _emit(self,to_waste):
        factor=self._factor();volume=self.ink_cfg['reference_volume_m3']*factor
        if self._volume(self.cartridge)<volume*(1-1e-12):self.fault='INK_EMPTY';self._stop('failed');return
        velocity=self.ink_cfg['reference_velocity_m_s']*factor;gap=self.method['gap_m']
        flight_s=2*gap/(velocity+math.sqrt(velocity*velocity+2*G*gap))
        x,y,n=self.points[self.point_index] if not to_waste else (0.,0.,self.nozzle)
        m=self._take_volume(volume);self.flight=dict(m=m,left=flight_s,x=x,y=y,n=n,to_waste=to_waste,volume=volume)
        self.last_drop=dict(volume=volume,velocity=velocity,gap=gap,flight_s=flight_s,launch_s=self.time_s)
        self.drop_count+=1;self.job_drops+=int(not to_waste);self.wait_left=1/self.method['frequency_hz'];self.stage='flight'
    def _land(self):
        f=self.flight
        if f['to_waste']:self.waste=[a+b for a,b in zip(self.waste,f['m'])]
        else:
            c=math.cos(self.ink_cfg['contact_angle_rad']);si=math.sin(self.ink_cfg['contact_angle_rad'])
            radius=(3*f['volume']*si**3/(math.pi*(2-3*c+c**3)))**(1/3)
            self.spots.append(dict(x=f['x'],y=f['y'],n=f['n'],m=f['m'],radius=radius))
        self.flight=None
    def _camera(self,delay):
        if self.last_drop is None:raise ActionRejected('NO_DROP_CAPTURE')
        d=self.last_drop;diam=(6*d['volume']/math.pi)**(1/3);z=d['velocity']*delay+.5*G*delay**2;px=2e-6
        # Virtual field follows the selected strobe delay; past-impact exposures contain no flying sphere.
        center=32.;radius=diam/(2*px);visible=0<=delay<d['flight_s']
        pixels=[32 if visible and (x+.5-center)**2+(y+.5-center)**2<=radius**2 else 240 for y in range(64) for x in range(64)]
        return dict(width=64,height=64,pixels=pixels,pixel_size_m=px,delay_s=delay,drop_diameter_m=diam,center_z_m=z,evidence='synthetic_ballistic_strobe_projection_NOT_camera_measurement',template_source=TEMPLATE_REL)
    def _settled(self):return all(abs(a-b)<1e-12 for a,b in zip(self.q,self.target))
    def _events(self):
        if self.flight and self.flight['left']<=1e-12:self._land()
        if self.phase!='running':return
        if self.stage in ['move','station_move','clean_move'] and self._settled():
            if self.purpose=='position':self.phase='succeeded';self.stage='idle';return
            if self.stage=='clean_move' or self.purpose=='cleaning':self.stage='clean';self.clean_left=self.method['clean_duration_s']
            else:self._emit(self.purpose=='dropwatch')
        if self.stage=='flight' and self.flight is None and self.wait_left<=1e-12:
            if self.purpose=='dropwatch':self.phase='succeeded';self.stage='idle'
            else:
                self.point_index+=1
                if self.point_index>=len(self.points):self.phase='succeeded';self.stage='idle'
                elif self.method['clean_every_drops'] and self.job_drops%self.method['clean_every_drops']==0:self._schedule_station('clean_move')
                else:self._schedule_point()
        if self.stage=='clean' and self.clean_left<=1e-12:
            self.cleanings+=1
            if self.purpose=='cleaning':self.phase='succeeded';self.stage='idle'
            else:self._schedule_point()
    def _advance(self,dt):
        p=self.plant
        if not p:return
        speeds=[p['axis_speed_m_s'] if self.powered else 0.]*2+[p['manual_speed_m_s'],p['manual_angle_rad_s']]
        for i,speed in enumerate(speeds):self.q[i]+=max(-speed*dt,min(speed*dt,self.target[i]-self.q[i]))
        if self.flight:self.flight['left']=max(0.,self.flight['left']-dt)
        self.wait_left=max(0.,self.wait_left-dt)
        if self.phase=='running':
            self.elapsed_s+=dt
            if self.stage=='clean':
                requested=p['cleaning_flow_m3_s']*dt;available=self._volume(self.cartridge);m=self._take_volume(requested)
                self.waste=[a+b for a,b in zip(self.waste,m)];self.clean_left=max(0.,self.clean_left-dt)
                if available+1e-24<requested:self.fault='INK_EMPTY';self._stop('failed')
        for i,(capacity,maxpower) in enumerate(zip([p['platen_capacity_j_k'],p['head_capacity_j_k']],[p['platen_heater_w'],p['head_heater_w']])):
            heat=min(maxpower,max(0.,p['controller_w_k']*(self.temp_targets[i]-self.temps[i]))) if self.heating and self.powered else 0.
            loss=p['loss_w_k']*(self.temps[i]-p['ambient_k']);self.temps[i]+=(heat-loss)*dt/capacity
            self.heater_input+=heat*dt;self.thermal_loss+=loss*dt
        if self.spots and self.ink_cfg:
            try:pv=self._reference(self.ink_cfg['solvent_id'],self.temps[0])['vapor_pressure_pa']
            except ActionRejected:self.fault='REFERENCE_OUT_OF_RANGE';self._stop('failed');return
            r=self.templates[self.ink_cfg['solvent_id']];mw=r['molar_mass_kg_mol']
            for s in self.spots:
                dm=min(s['m'][0],p['mass_transfer_m_s']*math.pi*s['radius']**2*pv*mw/(8.314462618*self.temps[0])*dt)
                s['m'][0]-=dm;self.evaporated[0]+=dm
    def step(self,dt_s):
        if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or not 0<dt_s<=3600:raise ActionRejected('INVALID_ARGUMENT')
        left=float(dt_s)
        while left>1e-12:
            self._events();dt=min(left,.005)
            if self.plant:
                speeds=[self.plant['axis_speed_m_s'] if self.powered else 0.]*2+[self.plant['manual_speed_m_s'],self.plant['manual_angle_rad_s']]
                times=[abs(a-b)/v for a,b,v in zip(self.q,self.target,speeds) if v and abs(a-b)>1e-12]
                if times:dt=min(dt,min(times))
            if self.flight and self.flight['left']>1e-12:dt=min(dt,self.flight['left'])
            if self.phase=='running' and self.stage=='flight' and self.wait_left>1e-12:dt=min(dt,self.wait_left)
            if self.phase=='running' and self.stage=='clean' and self.clean_left>1e-12:dt=min(dt,self.clean_left)
            self._advance(dt);self.time_s+=dt;left-=dt;self._events()
        self.revision+=1;return self.read('evaluator')
class FujifilmDmp2850Session:
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
        m=export_fujifilm_dmp2850_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.1.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=h.time_s,sample_window_s=None) for k in ids})
    def invoke(self,*,instance_id,capability_id,arguments,request_id):
        h=self._host
        try:
            self._target(instance_id)
            if not isinstance(capability_id,str) or capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
            if self._role=='agent' and CAPS[capability_id][5]!='agent':raise ActionRejected('ACCESS_DENIED')
            if not isinstance(request_id,str) or not request_id:raise ActionRejected('INVALID_ARGUMENT')
            validate(CAPS[capability_id][0],arguments);key=(self._principal,instance_id,request_id);digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
            if key in h._requests:
                old,result=h._requests[key]
                if old!=digest:raise ActionRejected('REQUEST_CONFLICT')
                return copy.deepcopy(result)
            result=h.invoke(capability_id,**arguments);op=result.get('operation_id')
            if op:h._owners[op]=self._principal
            response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=op,status='accepted' if op else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None);h._requests[key]=(digest,copy.deepcopy(response));return response
        except ActionRejected as exc:return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,status='rejected',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))
    def write(self,*,instance_id,state_id,value,request_id):
        self._target(instance_id)
        if state_id not in SLOTS or not SLOTS[state_id][5]:raise ActionRejected('ACCESS_DENIED')
        cap,arg=SLOTS[state_id][5];return self.invoke(instance_id=instance_id,capability_id=cap,arguments={arg:value},request_id=request_id)
    def poll(self,operation_id):
        h=self._host
        if operation_id!=h.operation_id or h._owners.get(operation_id)!=self._principal:raise ActionRejected('ACCESS_DENIED')
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('printing.measure'))
    def cancel(self,operation_id,request_id):
        self.poll(operation_id);return self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)




def export_fujifilm_dmp2850_contract_metadata():
    caps={};slots={}
    for k,(s,u,o,v,c,w) in SLOTS.items():
        slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'FujifilmDmp2850Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    evolving=[k for k,v in SLOTS.items() if v[2] in ['scene','derived','process_truth']]
    for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
        asynchronous=k in ['axis.move_to','acquisition.start','dropwatch.start','cleaning.start'];query=kind=='query'
        caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.fujifilm_dmp2850.FujifilmDmp2850Session.invoke',invocation=f'session.invoke(instance_id="dmp2850_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),
          preconditions=[] if query else [dict(description_zh='电源、有限墨量、基片吸附、有效图案、波形面积和温区、静止拆装与宿主身份检查',state_ids=['power.enabled','vacuum.enabled','ink.configured','substrate.configured','cycle.phase'],enforced_by='runtime',check='Runtime.invoke atomic guards; Session trusted-principal filtering')],
          state_effects=[] if query else [dict(state_id=s,timing='immediate',description_zh='设定/附着事件/有限装料变化') for s in effects]+[dict(state_id=s,timing='on_step',description_zh='宿主唯一时钟演化') for s in evolving],
          observes=[],model_effects=dict(mode='none',description_zh='模型实际求值证据尚待独立核验',binding_ids=[]),
          execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',completion_criterion='定位到目标或图案/单滴/清洗结束；不等于得到合格器件' if asynchronous else '事务返回；物理量由宿主step演化',
            completion_state_ids=['cycle.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',retry='same_request_id',resources=['host_clock','cartridge','substrate','request_cache'],
            partial_failure_zh='取消保留已喷和在飞液滴；在飞滴以后落下；停机不回滚热量/物料。仅进程内请求去重。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','PRECONDITION_FAILED','BUSY','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','REFERENCE_OUT_OF_RANGE','REFERENCE_INCOMPLETE','CARTRIDGE_CAPACITY','INVALID_PATTERN','PATTERN_OUT_OF_BOUNDS','UNCALIBRATED_WAVEFORM_RANGE','INK_EMPTY','NO_DROP_CAPTURE']],
          example_arguments=copy.deepcopy(ex),verification=[],limitations=['串行停走喷射教学近似，非厂家固件、标定、安全或真实材料配方建议'],source_ids=['manufacturer_primary','simulation_assumption'])
    mech=dict(component_ids=['head_carriage','head_lift','cartridge','nozzle_plate','platen','substrate','clean_station','dropwatch_camera'],state_ids=list(slots),meaning_zh='有限墨盒双组分、逐点喷射与重力飞行、平衡球冠近似、蒸发参考和双温度节点',implementation='lumped',
      equations=['pitch=0.0254/75m; angle=asin(drop_spacing/pitch); nozzle i offsets=(i-5.5)*pitch*(cos(angle),sin(angle))',
      'x_head=pattern_x-0.104-nozzle_dx; q_y=pattern_y-0.154-nozzle_dy, platen moves along -Y; printing stops motion before each fire',
      'pulse_factor=V*(dwell+(rise+fall)/2)/(Vref*pulse_ref); Vdrop=Vref_drop*pulse_factor; vdrop=vref*pulse_factor; explicit uncalibrated linear surrogate',
      '1pL=1e-15m3; 2.4pL nominal sphere diameter=(6V/pi)^(1/3), not 30um substrate dot diameter',
      'flight_time=2*gap/(v+sqrt(v^2+2*g*gap)); z=v*t+g*t^2/2; no air drag; strobe image is synthetic circle projection',
      'radius^3=3*V*sin(theta)^3/(pi*(2-3*cos(theta)+cos(theta)^3)); independent equilibrium caps, no overlap dynamics',
      'finite cartridge -> flying drop -> substrate/waste; cleaning at station transfers finite ink volume to waste; each species conserved including evaporated and collected',
      'dm_evap/dt=k*pi*r^2*p_sat(T)*M/(R*T), limited by remaining solvent; p_sat from the first library fit whose own range contains T (NIST Antoine first; NIST SRD69 / ThermoML / local Clausius-Clapeyron fits otherwise), never extrapolated; zero ambient vapor boundary',
      'ink.printability: Re=rho*v*L/mu, We=rho*v^2*L/sigma, Oh=mu/sqrt(rho*sigma*L), Z=1/Oh with library 298.15 K properties; host L (default 2.4 pL drop-equivalent diameter) and v; 1<Z<10 is the Reis & Derby / Derby (Annu. Rev. Mater. Res. 40 (2010) 395) rule of thumb, not a SAMBA measurement',
      'C_i*dT_i/dt=min(Pmax_i,max(0,Kp*(target_i-T_i)))-loss*(T_i-Ta); heater energy and node sensible/loss balance',
      'evaporation latent heat and carried sensible enthalpy omitted; externally prescribed thermal bath for mass transfer, not whole-system energy conservation'],
      parameters={},source_ids=['manufacturer_primary','manufacturer_samba','manufacturer_faq','nist_vapor_pressure','simulation_assumption'],
      limitations=['两轴及高度/墨盒角度为照片运动学代理，非厂家CAD、真实轴动态或±25um精度标定。','12孔几何/选择已实现，但仅串行停走喷射，无并行喷头调度。','波形只用梯形面积线性代理；滴形、空气阻力、卫星滴、润湿铺展、咖啡环、合滴、喷嘴堵塞/清洗效果均未标定。','库内溶剂298.15K真实物性只覆盖纯溶剂，不等于实测墨水/打印结果；库内纯溶剂没有一种同时落在SAMBA 4–8mPa·s/28–32mN/m理想窗口，故不提供从库装墨，功能墨水物性和接触角仍须宿主提供；ink.printability只做可喷性筛查；未找到可许可复用的SAMBA实测可喷性表格。','有限单墨盒、单层二值图案、清洗有限耗料；完整GUI、队列、外部相机识别、真实真空/接触和硬件未实现。','诊断数字叠层非真实内置面板；robot/hardware/部署文件隔离/CFD/真实相机标定not_run。'])
    spec=importlib.util.spec_from_file_location('_dmp_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann)
    ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
    ann.annotate_export(SLUG,caps,slots,{'inkjet':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'ink.configure':(INK,INK_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
    mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'inkjet':mech})

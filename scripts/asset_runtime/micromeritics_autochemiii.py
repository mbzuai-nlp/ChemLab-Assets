"""CHEM-122 finite AutoChem III teaching process; no OEM or empirical kinetics claim."""
from pathlib import Path
import copy, json, math, importlib.util
PROTOCOL_VERSION='1.0.0';SLUG='micromeritics_autochemiii';CATALOG_ID='CHEM-122';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch03/t4_measure'/SLUG
R=8.31446261815324;PREF=101325.;TREF=273.15;DT=.1;NREF=PREF/(R*TREF)
SPECIES=('n2','he','ar','h2','o2','co','co2','nh3','h2o');ELEMENTS=('n','he','ar','h','o','c')
ATOMIC=(.0140067,.004002602,.039948,.00100794,.0159994,.0120107)
ATOMS=((2,0,0,0,0,0),(0,1,0,0,0,0),(0,0,1,0,0,0),(0,0,0,2,0,0),(0,0,0,0,2,0),(0,0,0,0,1,1),(0,0,0,0,2,1),(1,0,0,3,0,0),(0,0,0,2,1,0))
BANKS=('pretreatment','carrier','loop');MODES=('tpd','tpr','tpo','pulse')
class ActionRejected(ValueError):pass
def number(unit='1',lo=None,hi=None):
 s={'type':'number','x-unit':unit}
 if lo is not None:s['minimum']=lo
 if hi is not None:s['maximum']=hi
 return s
def integer(lo=0,hi=1000000):return dict(type='integer',minimum=lo,maximum=hi,**{'x-unit':'1'})
def obj(properties):return dict(type='object',properties=properties,required=list(properties),additionalProperties=False)
def array(items,minimum=0,maximum=10000):return dict(type='array',items=items,minItems=minimum,maxItems=maximum)
def enum(items):return dict(type='string',enum=list(items))
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
BOOL={'type':'boolean'};TEXT={'type':'string'};PHASE=enum(['idle','running','succeeded','failed','cancelled'])
def validate(s,v):
 if 'anyOf' in s:
  for option in s['anyOf']:
   try:validate(option,v);return
   except ActionRejected:pass
  raise ActionRejected('INVALID_ARGUMENT')
 kind=s.get('type')
 if kind in ('number','integer'):
  if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or kind=='integer' and type(v) is not int or not s.get('minimum',-math.inf)<=v<=s.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
 elif kind=='boolean' and type(v) is not bool:raise ActionRejected('INVALID_ARGUMENT')
 elif kind=='string' and not isinstance(v,str):raise ActionRejected('INVALID_ARGUMENT')
 elif kind=='null' and v is not None:raise ActionRejected('INVALID_ARGUMENT')
 elif kind=='object':
  if not isinstance(v,dict) or set(v)!=set(s['properties']):raise ActionRejected('INVALID_ARGUMENT')
  for key,value in v.items():validate(s['properties'][key],value)
 elif kind=='array':
  if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
  for value in v:validate(s['items'],value)
 if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')
def empty_gas():return [0.]*len(SPECIES)
def elements(gas):return [sum(n*a[i] for n,a in zip(gas,ATOMS)) for i in range(len(ELEMENTS))]
def library():
 path=ASSET/'chem_template_library.json'
 return json.loads(path.read_text()) if path.exists() else {'substances':{}}
def temperature_valid(k,ambient):
 if not ambient-5-1e-9<=k<=1473.15+1e-9:raise ActionRejected('TEMPERATURE_RANGE')
 if abs((k-273.15)*10-round((k-273.15)*10))>1e-7:raise ActionRejected('TEMPERATURE_INCREMENT')
def ramp_limit(k):return 100/60 if k<=1073.15+1e-9 else 50/60 if k<=1273.15+1e-9 else 25/60
PLANT_EXAMPLE=dict(accept_engineering=True,ambient_k=298.15,thermal_capacity_j_k=20.,heater_limit_w=2000.,cooler_limit_w=30.,heat_loss_w_k=.2,controller_w_k=50.,sensor_tau_s=.5,reactor_volume_m3=1e-6,gas_cp_j_mol_k=29.,tcd_tau_s=.3,tcd_sensitivity_v=[0.,.15,.05,-.2,.02,.03,.06,.1,.08])
PLANT=obj({'accept_engineering':BOOL,'ambient_k':number('K',273.15,323.15),'thermal_capacity_j_k':number('J/K',1,10000),'heater_limit_w':number('W',1,10000),'cooler_limit_w':number('W',0,10000),'heat_loss_w_k':number('W/K',0,100),'controller_w_k':number('W/K',.1,1000),'sensor_tau_s':number('s',.1,100),'reactor_volume_m3':number('m^3',1e-8,1e-3),'gas_cp_j_mol_k':number('J/(mol*K)',1,100),'tcd_tau_s':number('s',.1,100),'tcd_sensitivity_v':array(number('V',-100,100),9,9)})
SAMPLE_EXAMPLE=dict(label='explicit TPD teaching sample',reference_id=None,accept_unknown_conditions=True,accept_engineering_kinetics=True,mode='tpd',inert_mass_kg=1e-4,particle_diameter_m=.001,capacity_mol=2e-5,initial_bound_mol=1e-5,adsorbate='nh3',preexponential_s=1000.,activation_j_mol=35000.,reaction_enthalpy_j_mol=40000.)
SAMPLE=obj({'label':TEXT,'reference_id':nullable(TEXT),'accept_unknown_conditions':BOOL,'accept_engineering_kinetics':BOOL,'mode':enum(MODES),'inert_mass_kg':number('kg',1e-9,.01),'particle_diameter_m':number('m',0,.009),'capacity_mol':number('mol',1e-12,.1),'initial_bound_mol':number('mol',0,.1),'adsorbate':enum(['h2','co','nh3','co2']),'preexponential_s':number('1/s',0,1e15),'activation_j_mol':number('J/mol',0,500000),'reaction_enthalpy_j_mol':number('J/mol',-1e6,1e6)})
GAS_EXAMPLE=dict(bank='carrier',inlet=1,amount_mol=.01,mole_fractions=[1.,0.,0.,0.,0.,0.,0.,0.,0.],n2_flow_correction=1.)
GAS=obj({'bank':enum(BANKS),'inlet':integer(1,6),'amount_mol':number('mol',1e-9,1000),'mole_fractions':array(number('1',0,1),9,9),'n2_flow_correction':number('1',.1,10)})
SEGMENT=obj({'target_k':number('K',268.15,1473.15),'ramp_k_s':number('K/s',1/60,100/60),'hold_s':number('s',0,36000)})
METHOD_EXAMPLE=dict(mode='tpd',segments=[dict(target_k=313.15,ramp_k_s=100/60,hold_s=1.)],sample_interval_s=.5,maximum_duration_s=60.,pulse_count=3,pulse_interval_s=5.,pulse_volume_ref_m3=1e-7)
METHOD=obj({'mode':enum(MODES),'segments':array(SEGMENT,1,20),'sample_interval_s':number('s',.1,100),'maximum_duration_s':number('s',.1,86400),'pulse_count':integer(1,1000),'pulse_interval_s':number('s',.1,1000),'pulse_volume_ref_m3':number('m^3',1e-10,1e-5)})
ROW=obj({'time_s':number('s'),'temperature_k':number('K'),'target_k':number('K'),'tcd_v':number('V'),'flow_ref_m3_s':number('m^3/s'),'pulse_number':integer(),'stage':TEXT})
RESULT=obj({'operation_id':nullable(TEXT),'phase':PHASE,'mode':TEXT,'stage':TEXT,'elapsed_s':number('s'),'rows':array(ROW,0,1000000),'integral_v_s':number('V*s'),'estimated_amount_mol':nullable(number('mol')),'template_source':nullable(TEXT),'evidence':TEXT,'error_code':nullable(TEXT)})
LEDGER=obj({'loaded_mass_kg':number('kg'),'present_mass_kg':number('kg'),'exhaust_and_waste_mass_kg':number('kg'),'mass_error_kg':number('kg'),'element_error_mol':array(number('mol'),6,6),'heat_input_j':number('J'),'heat_out_j':number('J'),'reaction_heat_j':number('J'),'stored_heat_j':number('J'),'heat_error_j':number('J')})
CAPS={};SLOTS={}
def cap(k,i,o,c,z,kind='device_command',visibility='agent',example=None,effects=None):CAPS[k]=dict(input_schema=i,output_schema=o,component_ids=c,function_zh=z,kind=kind,visibility=visibility,example_arguments=example or {},effects=effects or [])
def slot(k,s,u,z,origin='derived',visibility='agent',component='controller',write=None):SLOTS[k]=dict(schema=s,unit=u,meaning=z,origin=origin,visibility=visibility,component=component,write=write)
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller','furnace','tcd'],'宿主输入有效热学、气路和TCD系数并显式接受工程近似','configuration','evaluator',PLANT_EXAMPLE,['plant.configured','truth.temperature_k','thermal.target_k','thermal.programmed_k','thermal.indicated_k','path.target_k','path.indicated_k','truth.cell_mol'])
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_tube'],'宿主装样；粉末/颗粒最大直径9mm；未知动力学必须显式输入','configuration','evaluator',SAMPLE_EXAMPLE,['sample.present','truth.bound_mol'])
cap('sample.remove',obj({}),obj({'removed':BOOL}),['sample_tube'],'冷态开放炉体并关闭流路时取样入废物账本','configuration','evaluator',effects=['sample.present','truth.bound_mol'])
cap('gas.load',GAS,obj({'loaded':BOOL}),['gas_manifold'],'宿主向六乘三有限入口登记混合气；校正系数是显式输入','configuration','evaluator',GAS_EXAMPLE,['truth.stock_mol'])
cap('gas.unload',obj({'bank':enum(BANKS),'inlet':integer(1,6)}),obj({'unloaded':BOOL}),['gas_manifold'],'宿主关闭流路后卸下气源并记入回收账本','configuration','evaluator',dict(bank='carrier',inlet=1),['truth.stock_mol'])
for k,arg,schema,c,z,example,effect in [('power.set','enabled',BOOL,'power_switch','设置供电',True,'power.enabled'),('service.set','distance_m',number('m',0,.100),'service_cover','断电维护后盖的工程位移',0.,'service.slide_m'),('furnace.open','opened',BOOL,'furnace','冷态炉体开放代理；不是机器人操作',True,'furnace.opened'),('gas.open','enabled',BOOL,'gas_manifold','开启或关闭选定连续流路',True,'gas.enabled')]:cap(k,obj({arg:schema}),obj({arg:schema}),[c],z,example={arg:example},effects=[effect])
cap('gas.select',obj({'bank':enum(BANKS),'inlet':integer(1,6)}),obj({'bank':enum(BANKS),'inlet':integer(1,6)}),['gas_manifold'],'关闭气流时选择各六口之一',example=dict(bank='carrier',inlet=1),effects=['gas.pretreatment_inlet','gas.carrier_inlet','gas.loop_inlet'])
cap('gas.route',obj({'bank':enum(['carrier','pretreatment'])}),obj({'bank':enum(['carrier','pretreatment'])}),['gas_manifold'],'连续载气或预处理路径；loop仅脉冲时注入',example={'bank':'carrier'},effects=['gas.route'])
cap('gas.set_flow',obj({'reference_m3_s':number('m^3/s',0,100e-6/60)}),obj({'reference_m3_s':number('m^3/s')}),['gas_manifold'],'自动N2标定流量1–100mL/min；0为关闭目标，其它混合气须独立校正',example={'reference_m3_s':30e-6/60},effects=['flow.target_ref_m3_s'])
cap('thermal.set_temperature',obj({'temperature_k':number('K',268.15,1473.15),'ramp_k_s':number('K/s',1/60,100/60)}),obj({'temperature_k':number('K'),'ramp_k_s':number('K/s')}),['furnace'],'0.1°C设定，RevC分段1–100/50/25°C/min；只写目标',example=dict(temperature_k=373.15,ramp_k_s=100/60),effects=['thermal.target_k','thermal.ramp_k_s'])
cap('path.set_temperature',obj({'temperature_k':number('K',273.15,423.15)}),obj({'temperature_k':number('K')}),['gas_manifold'],'气路阀温上限150°C；只写目标',example={'temperature_k':323.15},effects=['path.target_k'])
cap('method.configure',METHOD,METHOD,['controller','furnace','gas_manifold'],'TPD/TPR/TPO或有限脉冲程序，显式最大运行时限','configuration',example=METHOD_EXAMPLE,effects=['method.mode'])
cap('detector.calibrate',obj({'known_moles_mol':array(number('mol',1e-15,.1),2,100),'observed_areas_v_s':array(number('V*s'),2,100)}),obj({'mol_per_v_s':number('mol/(V*s)')}),['tcd'],'独立已知进样量与实测积分零截距线性标定；不读取样品真值','configuration',example=dict(known_moles_mol=[1e-6,2e-6],observed_areas_v_s=[.1,.2]),effects=['detector.calibrated'])
cap('acquisition.start',obj({}),obj({'operation_id':TEXT}),['controller','furnace','tcd'],'按宿主时钟执行升温/保温/有限脉冲并记录TCD',effects=['acquisition.phase','acquisition.stage','acquisition.elapsed_s','acquisition.points','acquisition.pulses','thermal.target_k','thermal.ramp_k_s'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['controller'],'取消保留已有样品变化、热量和气体；关闭流路并回到环境目标',effects=['acquisition.phase','gas.enabled','thermal.target_k'])
cap('acquisition.result',obj({}),RESULT,['tcd'],'只读当前或已冻结结果及模板来源','query')
cap('analysis.integrate',obj({'times_s':array(number('s'),2,100000),'signals_v':array(number('V'),2,100000),'baseline_v':number('V'),'mol_per_v_s':number('mol/(V*s)')}),obj({'area_v_s':number('V*s'),'amount_mol':number('mol')}),['tcd'],'只对外部观测做梯形积分和独立校准换算','analysis',example=dict(times_s=[0.,1.,2.],signals_v=[0.,.1,0.],baseline_v=0.,mol_per_v_s=1e-5))
cap('analysis.dispersion',obj({'adsorbed_mol':number('mol',0,.1),'metal_mass_kg':number('kg',1e-12,1),'metal_molar_mass_kg_mol':number('kg/mol',.001,.3),'adsorbate_per_surface_atom':number('1',.01,10)}),obj({'fraction':number('1',0,1)}),['controller'],'外部校准吸附量与金属质量/化学计量推算分散度','analysis',example=dict(adsorbed_mol=1e-6,metal_mass_kg=1e-5,metal_molar_mass_kg_mol=.0586934,adsorbate_per_surface_atom=1.))
cap('reference.query',obj({'reference_id':TEXT}),obj({'reference_json':TEXT,'template_source':TEXT,'evidence':TEXT}),['controller'],'读取独立公开实验参考；不将静态参考自动当作样品动力学','query',example={'reference_id':'parlett2023_nio_sio2'})
cap('ledger.read',obj({}),LEDGER,['controller'],'宿主元素/总质量/有效热量守恒账本','query','evaluator')
cap('sim.step',obj({'dt_s':number('s',0,3600)}),obj({'time_s':number('s')}),['controller'],'唯一宿主时钟入口','simulation','evaluator',{'dt_s':.1})
UNSUPPORTED=['cryocooler.enable','ms.connect','detector.empirical_noise','fault.empirical','calibration.oem_certificate','hardware.connect','robot.manipulate']
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'缺少附件、实测或原厂驱动：'+k)
for k,z,c in [('plant.configured','显式工程输入已登记','controller'),('power.enabled','供电','power_switch'),('furnace.opened','冷态炉体开放','furnace'),('sample.present','样品在位','sample_tube'),('gas.enabled','连续气路开启','gas_manifold'),('detector.calibrated','已输入独立积分标定','tcd')]:slot(k,BOOL,'1',z,component=c)
slot('service.slide_m',number('m',0,.100),'m','后盖工程拆卸位移','scene',component='service_cover',write=('service.set','distance_m'))
slot('power.angle_rad',number('rad',0,.28),'rad','开关位置代理','scene',component='power_switch')
for k,u,z,c in [('thermal.target_k','K','最终炉温目标','furnace'),('thermal.programmed_k','K','按程序速率渐进的控制目标','furnace'),('thermal.ramp_k_s','K/s','当前目标斜率上限','furnace'),('path.target_k','K','气路阀温目标','gas_manifold'),('flow.target_ref_m3_s','m^3/s','N2标定参考流量目标','gas_manifold')]:slot(k,number(u),u,z,'setpoint',component=c)
for k,u,z,c in [('thermal.indicated_k','K','一阶响应及0.1K量化的炉温工程读数','furnace'),('path.indicated_k','K','一阶气路温度工程显示','gas_manifold'),('flow.indicated_ref_m3_s','m^3/s','有限气源供给的参考流量工程显示','gas_manifold'),('detector.tcd_v','V','组分响应及一阶滤波的合成TCD电压','tcd')]:slot(k,nullable(number(u)),u,z,'derived',component=c)
slot('gas.route',enum(['carrier','pretreatment']),'1','当前连续气路','setpoint',component='gas_manifold')
for bank in BANKS:slot('gas.'+bank+'_inlet',integer(1,6),'1',bank+'选中的六口编号','setpoint',component='gas_manifold')
slot('method.mode',enum(MODES),'1','程序模式','setpoint')
for k,u,z in [('truth.temperature_k','K','有效炉/样品热节点真温'),('truth.stock_mol','mol','18入口总剩余摩尔库存'),('truth.cell_mol','mol','反应池内总摩尔数'),('truth.bound_mol','mol','表面吸附或可反应固相基元摩尔数')]:slot(k,number(u,0),u,z,'process_truth','evaluator')
slot('clock.time_s',number('s',0),'s','宿主物理时钟');slot('acquisition.phase',PHASE,'1','异步终态');slot('acquisition.stage',TEXT,'1','升温/保温程序阶段');slot('acquisition.elapsed_s',number('s',0),'s','运行用时');slot('acquisition.points',integer(),'1','已记录TCD点数');slot('acquisition.pulses',integer(),'1','已实际注入脉冲数')
PHYSICS_EFFECTS=['clock.time_s','thermal.programmed_k','thermal.indicated_k','path.indicated_k','flow.indicated_ref_m3_s','detector.tcd_v','truth.temperature_k','truth.stock_mol','truth.cell_mol','truth.bound_mol','acquisition.phase','acquisition.stage','acquisition.elapsed_s','acquisition.points','acquisition.pulses','gas.enabled','thermal.target_k','thermal.ramp_k_s']
CAPS['furnace.set_open']=copy.deepcopy(CAPS['furnace.open'])
CAPS['furnace.set_open']['component_ids']=['furnace_door']
CAPS['furnace.open']['component_ids']=['furnace_door']
for capability in ['furnace.open','furnace.set_open']:CAPS[capability]['effects']+=['furnace.angle_rad']
for k,arg,maximum,component,state,meaning in [('sample.set_position','distance_m',.120,'sample_tube','sample.withdraw_m','冷态炉门打开且释放杠杆后人工拔管展示'),('front_cover.set','distance_m',.160,'front_cover','front.cover_m','断开连续气流后人工拆下前罩'),('sample.release','angle_rad',.60,'sample_release','sample.release_rad','冷态打开炉门后手动释放样品管夹')]:
 cap(k,obj({arg:number('rad' if arg=='angle_rad' else 'm',0,maximum)}),obj({arg:number('rad' if arg=='angle_rad' else 'm')}),[component],meaning,example={arg:0.},effects=[state])
 slot(state,number('rad' if arg=='angle_rad' else 'm',0,maximum),'rad' if arg=='angle_rad' else 'm',meaning,'scene',component=component,write=(k,arg))
slot('furnace.angle_rad',number('rad',0,1.90),'rad','右侧竖轴炉门的开放代理','scene',component='furnace_door')
CAPS['gas.set_flow']['input_schema']['properties']['reference_m3_s']['x-reference-conditions']='N2-calibrated volume at assumed273.15K/101325Pa; explicit mixture correction is evaluator input'
CAPS['sim.step']['effects']=PHYSICS_EFFECTS
CAPS['power.set']['effects']+=['power.angle_rad','gas.enabled','thermal.target_k','thermal.indicated_k','path.indicated_k','flow.indicated_ref_m3_s','detector.tcd_v']
class MicromeriticsAutochemiiiRuntime:
 def __init__(self,instance_id='autochemiii_01'):
  self.instance_id=instance_id;self.plant=None;self.sample=None;self.method=copy.deepcopy(METHOD_EXAMPLE);self.power=False;self.service=0.;self.opened=True;self.sample_withdraw=0.;self.sample_release=0.;self.front_cover=0.;self.gas_enabled=False;self.route='carrier';self.selected={bank:1 for bank in BANKS};self.stocks={};self.temperature=298.15;self.sensor=298.15;self.target=298.15;self.programmed=298.15;self.ramp=100/60;self.path_target=298.15;self.path_sensor=298.15;self.flow_target=30e-6/60;self.flow_actual=0.;self.tcd=0.;self.bound=0.;self.cell=empty_gas();self.exhaust=empty_gas();self.input_elements=[0.]*6;self.waste_elements=[0.]*6;self.inert_loaded=0.;self.inert_waste=0.;self.heat_in=0.;self.heat_out=0.;self.reaction_heat=0.;self.calibration=None;self.time_s=0.;self._ticks=0;self._remainder=0.;self.revision=0;self.phase='idle';self.stage='idle';self.elapsed=0.;self.rows=[];self.pulses=0;self.segment=0;self.hold_elapsed=0.;self.integral=0.;self.previous_signal=None;self.source=None;self.error=None;self.operation_id=None;self._counter=0;self.records={};self._owners={};self._requests={};self._principals={'agent':'agent','evaluator':'evaluator'}
 def register_principal(self,principal_id,visibility='agent'):
  if not isinstance(principal_id,str) or not principal_id or principal_id in self._principals or visibility not in ('agent','debug','evaluator'):raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='agent'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return MicromeriticsAutochemiiiSession(self,principal_id)
 def describe(self):return export_micromeritics_autochemiii_contract_metadata()
 def read(self,role='evaluator'):
  d={'plant.configured':self.plant is not None,'power.enabled':self.power,'power.angle_rad':.28 if self.power else 0.,'furnace.opened':self.opened,'furnace.angle_rad':1.90 if self.opened else 0.,'sample.withdraw_m':self.sample_withdraw,'sample.release_rad':self.sample_release,'front.cover_m':self.front_cover,'service.slide_m':self.service,'sample.present':self.sample is not None,'gas.enabled':self.gas_enabled,'gas.route':self.route,'detector.calibrated':self.calibration is not None,'thermal.target_k':self.target,'thermal.programmed_k':self.programmed,'thermal.ramp_k_s':self.ramp,'path.target_k':self.path_target,'flow.target_ref_m3_s':self.flow_target,'thermal.indicated_k':round((self.sensor-273.15)*10)/10+273.15 if self.power else None,'path.indicated_k':round((self.path_sensor-273.15)*10)/10+273.15 if self.power else None,'flow.indicated_ref_m3_s':self.flow_actual if self.power else None,'detector.tcd_v':self.tcd if self.power else None,'method.mode':self.method['mode'],'truth.temperature_k':self.temperature,'truth.stock_mol':sum(sum(s['gas']) for s in self.stocks.values()),'truth.cell_mol':sum(self.cell),'truth.bound_mol':self.bound,'clock.time_s':self.time_s,'acquisition.phase':self.phase,'acquisition.stage':self.stage,'acquisition.elapsed_s':self.elapsed,'acquisition.points':len(self.rows),'acquisition.pulses':self.pulses}
  d.update({'gas.'+bank+'_inlet':self.selected[bank] for bank in BANKS});return {k:copy.deepcopy(v) for k,v in d.items() if role!='agent' or SLOTS[k]['visibility']=='agent'}
 def _bound_elements(self,n):
  if not self.sample:return [0.]*6
  if self.sample['mode']=='tpr':return [0.,0.,0.,0.,n,0.]
  if self.sample['mode']=='tpo':return [0.,0.,0.,0.,0.,n]
  return [n*a for a in ATOMS[SPECIES.index(self.sample['adsorbate'])]]
 def _ledger(self):
  present=elements([self.cell[i]+sum(s['gas'][i] for s in self.stocks.values()) for i in range(9)]);present=[a+b for a,b in zip(present,self._bound_elements(self.bound))];out=[a+b for a,b in zip(elements(self.exhaust),self.waste_elements)];error=[a-b-c for a,b,c in zip(self.input_elements,present,out)];inert=self.sample['inert_mass_kg'] if self.sample else 0.;loaded=sum(a*b for a,b in zip(self.input_elements,ATOMIC))+self.inert_loaded;remain=sum(a*b for a,b in zip(present,ATOMIC))+inert;waste=sum(a*b for a,b in zip(out,ATOMIC))+self.inert_waste;stored=self.plant['thermal_capacity_j_k']*(self.temperature-self.plant['ambient_k']) if self.plant else 0.
  return dict(loaded_mass_kg=loaded,present_mass_kg=remain,exhaust_and_waste_mass_kg=waste,mass_error_kg=loaded-remain-waste,element_error_mol=error,heat_input_j=self.heat_in,heat_out_j=self.heat_out,reaction_heat_j=self.reaction_heat,stored_heat_j=stored,heat_error_j=self.heat_in-self.heat_out+self.reaction_heat-stored)
 def _result(self):
  if self.phase!='running' and self.operation_id in self.records:return copy.deepcopy(self.records[self.operation_id])
  return dict(operation_id=self.operation_id,phase=self.phase,mode=self.method['mode'],stage=self.stage,elapsed_s=self.elapsed,rows=copy.deepcopy(self.rows),integral_v_s=self.integral,estimated_amount_mol=None if self.calibration is None else self.integral*self.calibration,template_source=self.source,evidence='Synthetic finite-gas/Arrhenius/thermal/TCD response from explicitly accepted coefficients; attached literature reference is independent and is not empirical kinetics.',error_code=self.error)
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _ready(self):
  if not self.plant or not self.power or self.service>1e-9:raise ActionRejected('NOT_READY')
 def _sample_access(self):
  if not self.opened or self.temperature>323.15 or self.gas_enabled:raise ActionRejected('SAMPLE_ACCESS')
 def _stock(self,bank):return self.stocks.get((bank,self.selected[bank]))
 def _temperature(self,target,rate,start):
  if not self.plant:raise ActionRejected('NOT_READY')
  temperature_valid(target,self.plant['ambient_k'])
  if rate>ramp_limit(max(target,start))+1e-12:raise ActionRejected('RAMP_LIMIT')
 def invoke(self,capability,**arguments):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if capability in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[capability]['input_schema'],arguments)
  if capability=='sim.step':self.step(arguments['dt_s']);return {'time_s':self.time_s}
  before=copy.deepcopy(self.__dict__)
  try:
   result=self._invoke(capability,arguments);validate(CAPS[capability]['output_schema'],result)
   if CAPS[capability]['kind'] not in ('query','analysis'):self.revision+=1
   return result
  except Exception:self.__dict__.clear();self.__dict__.update(before);raise
 def _invoke(self,k,a):
  if k=='acquisition.result':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='reference.query':
   record=library()['substances'].get(a['reference_id'])
   if record is None:raise ActionRejected('INVALID_REFERENCE')
   return dict(reference_json=json.dumps(record,sort_keys=True),template_source=json.dumps(record['source'],sort_keys=True),evidence=record['evidence'])
  if k=='analysis.integrate':
   t=a['times_s'];y=a['signals_v']
   if len(t)!=len(y) or any(b<=c for c,b in zip(t,t[1:])):raise ActionRejected('INVALID_ARGUMENT')
   area=sum((b-c)*(y[i]+y[i+1]-2*a['baseline_v'])/2 for i,(c,b) in enumerate(zip(t,t[1:])));return dict(area_v_s=area,amount_mol=area*a['mol_per_v_s'])
  if k=='analysis.dispersion':
   f=a['adsorbed_mol']*a['metal_molar_mass_kg_mol']/(a['metal_mass_kg']*a['adsorbate_per_surface_atom'])
   if f>1:raise ActionRejected('INCONSISTENT_DISPERSION')
   return {'fraction':f}
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self.gas_enabled=False;self.target=self.plant['ambient_k'];self._finish('cancelled');return {'phase':self.phase}
  self._idle()
  if k=='plant.configure':
   if self.plant is not None:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   self.plant=copy.deepcopy(a);self.temperature=self.sensor=self.target=self.programmed=self.path_target=self.path_sensor=a['ambient_k'];self.cell[0]=PREF*a['reactor_volume_m3']/(R*a['ambient_k']);self.input_elements=elements(self.cell);return {'configured':True}
  if k=='power.set':
   if a['enabled'] and (not self.plant or self.service>1e-9):raise ActionRejected('NOT_READY')
   self.power=a['enabled']
   if not self.power:self.gas_enabled=False;self.target=self.plant['ambient_k'] if self.plant else self.temperature
   return a
  if k=='service.set':
   if self.power:raise ActionRejected('POWER_INTERLOCK')
   self.service=a['distance_m'];return a
  if k in ('furnace.open','furnace.set_open'):
   if self.temperature>323.15 or self.gas_enabled:raise ActionRejected('HOT_OR_FLOWING')
   if not a['opened'] and (self.sample_withdraw>1e-9 or self.sample_release>1e-9):raise ActionRejected('SAMPLE_CONNECTION')
   self.opened=a['opened'];return a
  if k=='front_cover.set':
   if self.gas_enabled:raise ActionRejected('GAS_ACCESS')
   self.front_cover=a['distance_m'];return a
  if k=='sample.release':
   self._sample_access()
   if a['angle_rad']<.4 and self.sample_withdraw>1e-9:raise ActionRejected('SAMPLE_CONNECTION')
   self.sample_release=a['angle_rad'];return a
  if k=='sample.set_position':
   self._sample_access()
   if self.sample_release<.4:raise ActionRejected('SAMPLE_CONNECTION')
   self.sample_withdraw=a['distance_m'];return a
  if k=='sample.load':
   self._sample_access()
   if not self.plant or self.sample:raise ActionRejected('SAMPLE_PRESENT')
   if not a['accept_engineering_kinetics']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   if a['initial_bound_mol']>a['capacity_mol']:raise ActionRejected('CAPACITY')
   if a['reference_id'] is not None:
    record=library()['substances'].get(a['reference_id'])
    if record is None:raise ActionRejected('INVALID_REFERENCE')
    if not a['accept_unknown_conditions'] and any(v is None for v in record.get('conditions',{}).values()):raise ActionRejected('UNKNOWN_CONDITIONS')
   elif not a['accept_unknown_conditions']:raise ActionRejected('UNKNOWN_CONDITIONS')
   self.sample=copy.deepcopy(a);self.bound=a['initial_bound_mol'];self.inert_loaded+=a['inert_mass_kg'];self.input_elements=[x+y for x,y in zip(self.input_elements,self._bound_elements(self.bound))];return {'loaded':True}
  if k=='sample.remove':
   self._sample_access()
   if not self.sample:raise ActionRejected('NO_SAMPLE')
   self.inert_waste+=self.sample['inert_mass_kg'];self.waste_elements=[x+y for x,y in zip(self.waste_elements,self._bound_elements(self.bound))];self.sample=None;self.bound=0.;return {'removed':True}
  if k in ('gas.load','gas.unload'):
   if not self.plant or self.gas_enabled:raise ActionRejected('GAS_ACCESS')
   key=(a['bank'],a['inlet'])
   if k=='gas.unload':
    stock=self.stocks.pop(key,None)
    if stock:self.waste_elements=[x+y for x,y in zip(self.waste_elements,elements(stock['gas']))]
    return {'unloaded':True}
   if key in self.stocks and sum(self.stocks[key]['gas'])>0:raise ActionRejected('STOCK_PRESENT')
   if abs(sum(a['mole_fractions'])-1)>1e-12:raise ActionRejected('INVALID_COMPOSITION')
   gas=[a['amount_mol']*v for v in a['mole_fractions']];self.stocks[key]={'gas':gas,'correction':a['n2_flow_correction']};self.input_elements=[x+y for x,y in zip(self.input_elements,elements(gas))];return {'loaded':True}
  if k=='gas.select':
   if self.gas_enabled:raise ActionRejected('GAS_ACCESS')
   self.selected[a['bank']]=a['inlet'];return a
  if k=='gas.route':
   if self.gas_enabled:raise ActionRejected('GAS_ACCESS')
   self.route=a['bank'];return a
  if k=='gas.set_flow':
   q=a['reference_m3_s']
   if 0<q<1e-6/60:raise ActionRejected('FLOW_RANGE')
   self.flow_target=q;return a
  if k=='gas.open':
   if a['enabled']:
    self._ready();stock=self._stock(self.route)
    if self.opened or self.flow_target<=0 or stock is None or sum(stock['gas'])<=0:raise ActionRejected('GAS_NOT_READY')
   self.gas_enabled=a['enabled'];return a
  if k=='thermal.set_temperature':
   self._temperature(a['temperature_k'],a['ramp_k_s'],self.programmed);self.target=a['temperature_k'];self.ramp=a['ramp_k_s'];return a
  if k=='path.set_temperature':
   if not self.plant or a['temperature_k']<self.plant['ambient_k']:raise ActionRejected('TEMPERATURE_RANGE')
   temperature_valid(a['temperature_k'],self.plant['ambient_k']);self.path_target=a['temperature_k'];return a
  if k=='method.configure':
   previous=self.programmed
   for segment in a['segments']:self._temperature(segment['target_k'],segment['ramp_k_s'],previous);previous=segment['target_k']
   self.method=copy.deepcopy(a);return a
  if k=='detector.calibrate':
   n=a['known_moles_mol'];v=a['observed_areas_v_s']
   if len(n)!=len(v) or sum(x*x for x in v)<1e-24 or any(x==0 for x in v) or min(v)<0<max(v):raise ActionRejected('INVALID_CALIBRATION')
   self.calibration=sum(x*y for x,y in zip(n,v))/sum(x*x for x in v);return {'mol_per_v_s':self.calibration}
  if k=='acquisition.start':
   self._ready()
   if self.opened or not self.sample or not self.gas_enabled or self.sample['mode']!=self.method['mode']:raise ActionRejected('ACQUISITION_NOT_READY')
   stock=self._stock(self.route)
   if stock is None or sum(stock['gas'])<=0:raise ActionRejected('GAS_NOT_READY')
   if self.method['mode']=='pulse':
    loop=self._stock('loop');required=self.method['pulse_volume_ref_m3']*NREF*self.method['pulse_count']
    if loop is None or sum(loop['gas'])<required:raise ActionRejected('FINITE_INVENTORY')
   previous=self.programmed
   for segment in self.method['segments']:self._temperature(segment['target_k'],segment['ramp_k_s'],previous);previous=segment['target_k']
   ref=self.sample['reference_id'];self.source=json.dumps(library()['substances'][ref]['source'],sort_keys=True) if ref else None;self._counter+=1;self.operation_id=self.instance_id+':'+str(self._counter);self.phase='running';self.stage='ramp';self.elapsed=0.;self.rows=[];self.pulses=0;self.segment=0;self.hold_elapsed=0.;self.integral=0.;self.previous_signal=None;self.error=None;self.target=self.method['segments'][0]['target_k'];self.ramp=self.method['segments'][0]['ramp_k_s'];return {'operation_id':self.operation_id}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _finish(self,phase,error=None):
  self.phase=phase;self.error=error
  if self.operation_id:self.records[self.operation_id]=self._result()
 def _feed(self,stock,wanted):
  total=sum(stock['gas']) if stock else 0.;take=min(total,wanted)
  if take>0:
   for i in range(9):amount=stock['gas'][i]*(take/total);stock['gas'][i]-=amount;self.cell[i]+=amount
  return take
 def _reaction(self):
  if not self.sample:return 0.
  s=self.sample;fraction=-math.expm1(-s['preexponential_s']*math.exp(-s['activation_j_mol']/(R*self.temperature))*DT);mode=s['mode'];amount=0.;energy_limit=self.plant['thermal_capacity_j_k']*max(0.,self.temperature-1.)/s['reaction_enthalpy_j_mol'] if s['reaction_enthalpy_j_mol']>0 else math.inf
  if mode=='tpd':
   amount=min(self.bound*fraction,energy_limit);self.bound-=amount;self.cell[SPECIES.index(s['adsorbate'])]+=amount
  elif mode in ('tpr','tpo'):
   reagent=SPECIES.index('h2' if mode=='tpr' else 'o2');product=SPECIES.index('h2o' if mode=='tpr' else 'co2');amount=min(self.bound*fraction,self.cell[reagent],energy_limit);self.bound-=amount;self.cell[reagent]-=amount;self.cell[product]+=amount
  else:
   index=SPECIES.index(s['adsorbate']);amount=min(min(self.cell[index],s['capacity_mol']-self.bound)*fraction,energy_limit);self.cell[index]-=amount;self.bound+=amount
  heat=-amount*s['reaction_enthalpy_j_mol'];self.reaction_heat+=heat;return heat
 def _observe(self):
  if self.previous_signal is not None:self.integral+=(self.time_s-self.previous_signal[0])*(self.tcd+self.previous_signal[1])/2
  self.previous_signal=(self.time_s,self.tcd);self.rows.append(dict(time_s=self.time_s,temperature_k=self.read()['thermal.indicated_k'],target_k=self.programmed,tcd_v=self.tcd,flow_ref_m3_s=self.flow_actual,pulse_number=self.pulses,stage=self.stage))
 def _tick(self):
  if not self.plant:return
  p=self.plant;ambient=p['ambient_k'];flow_mol=0.;self.flow_actual=0.;failure=None
  if self.gas_enabled and self.power:
   stock=self._stock(self.route);wanted=self.flow_target*NREF*(stock['correction'] if stock else 1.)*DT;flow_mol=self._feed(stock,wanted);self.flow_actual=flow_mol/(NREF*DT)
   if flow_mol<wanted:self.gas_enabled=False;failure='GAS_EXHAUSTED';self.target=ambient
  if self.phase=='running' and not failure and self.method['mode']=='pulse' and self.pulses<self.method['pulse_count'] and self.elapsed+1e-12>=self.pulses*self.method['pulse_interval_s']:
   wanted=self.method['pulse_volume_ref_m3']*NREF;delivered=self._feed(self._stock('loop'),wanted);flow_mol+=delivered;self.pulses+=1
   if delivered<wanted:self.gas_enabled=False;failure='GAS_EXHAUSTED';self.target=ambient
  self.programmed+=math.copysign(min(abs(self.target-self.programmed),self.ramp*DT),self.target-self.programmed)
  # Backward-Euler thermal node with the proportional controller's saturation solved implicitly.
  control=p['controller_w_k'] if self.power and not self.opened else 0.;loss_gain=p['heat_loss_w_k']+flow_mol*p['gas_cp_j_mol_k']/DT;capacity=p['thermal_capacity_j_k'];old=self.temperature
  candidate=(capacity*old+DT*(control*self.programmed+loss_gain*ambient))/(capacity+DT*(control+loss_gain));requested=control*(self.programmed-candidate);heater=max(-p['cooler_limit_w'],min(p['heater_limit_w'],requested))
  if heater!=requested:candidate=(capacity*old+DT*(heater+loss_gain*ambient))/(capacity+DT*loss_gain)
  self.temperature=candidate;qin=heater*DT;loss=loss_gain*(candidate-ambient)*DT;self.heat_in+=qin;self.heat_out+=loss;self.temperature+=self._reaction()/capacity
  self.sensor=self.temperature+(self.sensor-self.temperature)*math.exp(-DT/p['sensor_tau_s']);path=self.path_target if self.power else ambient;self.path_sensor=path+(self.path_sensor-path)*math.exp(-DT/2.)
  total=sum(self.cell);gas_capacity=PREF*p['reactor_volume_m3']/(R*self.temperature);excess=max(0.,total-gas_capacity)
  if excess:
   for i in range(9):amount=self.cell[i]*(excess/total);self.cell[i]-=amount;self.exhaust[i]+=amount
  total=sum(self.cell);signal=sum(n*k for n,k in zip(self.cell,p['tcd_sensitivity_v']))/total if total else 0.;self.tcd=signal+(self.tcd-signal)*math.exp(-DT/p['tcd_tau_s'])
  if self.phase!='running':return
  self.elapsed+=DT
  if self.temperature<1. or self.temperature>1478.15:failure='THERMAL_LIMIT'
  if self.elapsed>self.method['maximum_duration_s']+1e-9:failure='TIMEOUT'
  every=max(1,round(self.method['sample_interval_s']/DT))
  if round(self.elapsed/DT)%every==0 or failure:self._observe()
  if failure:self.gas_enabled=False;self.target=ambient;self._finish('failed',failure);return
  segment=self.method['segments'][self.segment]
  if abs(self.programmed-self.target)<1e-9 and abs(self.sensor-self.target)<=2.:
   self.stage='hold';self.hold_elapsed+=DT
   if self.hold_elapsed+1e-9>=segment['hold_s']:
    if self.segment+1<len(self.method['segments']):self.segment+=1;segment=self.method['segments'][self.segment];self.target=segment['target_k'];self.ramp=segment['ramp_k_s'];self.hold_elapsed=0.;self.stage='ramp'
    elif self.method['mode']!='pulse' or self.pulses>=self.method['pulse_count']:
     if self.previous_signal is None or self.previous_signal[0]!=self.time_s:self._observe()
     self._finish('succeeded')
 def step(self,dt_s):
  validate(number('s',0,3600),dt_s);total=self._remainder+dt_s;ticks=int((total+1e-12)/DT);self._remainder=total-ticks*DT
  for _ in range(ticks):self._ticks+=1;self.time_s=self._ticks*DT;self._tick()
  self.revision+=ticks
class MicromeriticsAutochemiiiSession:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,instance_id):
  if instance_id!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  meta=export_micromeritics_autochemiii_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
 def read(self,*,instance_id,state_ids=None):
  self._target(instance_id);h=self._host;values=h.read(self._role);ids=list(values) if state_ids is None else state_ids
  if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
  if any(k not in values for k in ids):raise ActionRejected('ACCESS_DENIED')
  return dict(instance_id=instance_id,status='succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=values[k],unit=SLOTS[k]['unit'],quality='unavailable' if values[k] is None else 'estimated' if SLOTS[k]['origin']=='derived' else 'valid',sample_time_s=h.time_s,sample_window_s=None) for k in ids})
 def invoke(self,*,instance_id,capability_id,arguments,request_id):
  h=self._host
  try:
   self._target(instance_id)
   if not isinstance(capability_id,str) or capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
   if self._role=='agent' and CAPS[capability_id]['visibility']!='agent':raise ActionRejected('ACCESS_DENIED')
   if not isinstance(request_id,str) or not request_id:raise ActionRejected('INVALID_ARGUMENT')
   validate(CAPS[capability_id]['input_schema'],arguments);key=(self._principal,request_id);digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
   if key in h._requests:
    old,result=h._requests[key]
    if old!=digest:raise ActionRejected('REQUEST_CONFLICT')
    return copy.deepcopy(result)
   if capability_id=='acquisition.stop' and self._role=='agent' and h._owners.get(h.operation_id)!=self._principal:raise ActionRejected('ACCESS_DENIED')
   result=h.invoke(capability_id,**arguments);operation=result.get('operation_id') if capability_id=='acquisition.start' else None
   if operation:h._owners[operation]=self._principal
   response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=operation,status='accepted' if operation else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None);h._requests[key]=(digest,copy.deepcopy(response));return response
  except ActionRejected as exc:return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,status='unsupported' if str(exc)=='UNSUPPORTED' else 'rejected',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))
 def write(self,*,instance_id,state_id,value,request_id):
  self._target(instance_id);entry=SLOTS.get(state_id)
  if not entry or not entry['write']:raise ActionRejected('ACCESS_DENIED')
  capability,argument=entry['write'];return self.invoke(instance_id=instance_id,capability_id=capability,arguments={argument:value},request_id=request_id)
 def poll(self,operation_id):
  h=self._host
  if h._owners.get(operation_id)!=self._principal:raise ActionRejected('ACCESS_DENIED')
  result=h._result() if operation_id==h.operation_id and h.phase=='running' else h.records.get(operation_id)
  if result is None:raise ActionRejected('UNKNOWN_OPERATION')
  return dict(operation_id=operation_id,status=result['phase'],result=copy.deepcopy(result))
 def cancel(self,operation_id,request_id):
  self.poll(operation_id)
  if operation_id!=self._host.operation_id:raise ActionRejected('NOT_RUNNING')
  return self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
def export_micromeritics_autochemiii_contract_metadata():
 slots={};caps={}
 for k,v in SLOTS.items():slots[k]=dict(meaning_zh=v['meaning'],component_ids=[v['component']],value_schema=copy.deepcopy(v['schema']),unit=v['unit'],reference_conditions='SI; gas reference273.15K/101325Pa; N2-calibrated flow and explicit gas correction; temperature kelvin',origin=v['origin'],visibility=v['visibility'],access='read_write' if v['write'] else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'MicromeriticsAutochemiiiSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=v['write'][0],argument=v['write'][1],fixed_arguments={}) if v['write'] else None,sampling=dict(mode='instant',time_domain='simulation',window_description='Fixed0.1s host ticks; TCD and temperature are explicitly assumed filtered displays'),source_ids=['simulation_assumption'])
 errors=['UNKNOWN_CAPABILITY','UNKNOWN_INSTANCE','UNKNOWN_OPERATION','INVALID_ARGUMENT','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','BUSY','NOT_READY','NOT_RUNNING','INITIALIZATION_ONLY','MODEL_NOT_ACCEPTED','SAMPLE_ACCESS','SAMPLE_PRESENT','SAMPLE_CONNECTION','NO_SAMPLE','CAPACITY','INVALID_REFERENCE','UNKNOWN_CONDITIONS','GAS_ACCESS','STOCK_PRESENT','INVALID_COMPOSITION','GAS_NOT_READY','FLOW_RANGE','TEMPERATURE_RANGE','TEMPERATURE_INCREMENT','RAMP_LIMIT','POWER_INTERLOCK','HOT_OR_FLOWING','INVALID_CALIBRATION','INCONSISTENT_DISPERSION','ACQUISITION_NOT_READY','FINITE_INVENTORY','GAS_EXHAUSTED','THERMAL_LIMIT','TIMEOUT']
 for k,c in CAPS.items():
  un=k in UNSUPPORTED;async_=k=='acquisition.start';caps[k]={key:copy.deepcopy(c[key]) for key in ['input_schema','output_schema','component_ids','function_zh','kind','visibility','example_arguments']};caps[k].update(availability='unsupported' if un else 'implemented',implementation={layer:'absent' if un or layer in ['scene','robot','hardware'] else 'implemented' for layer in ['process','scene','robot','hardware','host']},binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.micromeritics_autochemiii.MicromeriticsAutochemiiiSession.invoke',invocation=f'session.invoke(instance_id="autochemiii_01",capability_id={k!r},arguments={c["example_arguments"]!r},request_id="unique")'),preconditions=[] if c['kind'] in ['query','analysis'] or un else [dict(description_zh='实际参数、权限、库存、炉温、气路及访问检查；命令拒绝原子，非原厂安全认证',state_ids=['power.enabled','plant.configured','acquisition.phase'],enforced_by='runtime',check='atomic invoke and registered Session')],state_effects=[dict(state_id=x,timing='immediate',description_zh='实际命令变化') for x in c['effects']],observes=[],model_effects=dict(mode='none',description_zh='场景映射需独立验证',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='所有温程和有限脉冲完成或明确失败/取消' if async_ else '命令完成但不推进物理时钟',completion_state_ids=['acquisition.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['host_clock','furnace','gas_inventory','sample','tcd'],partial_failure_zh='保留已消耗/排出气体、表面变化、热量与记录；终态结果冻结，后续真实热弛豫仍由宿主推进'),errors=[dict(code=x,meaning_zh=x,side_effects='partial' if async_ and x in ['GAS_EXHAUSTED','THERMAL_LIMIT','TIMEOUT'] else 'none') for x in errors],verification=[],limitations=['原厂噪声/故障/误差/压力控制数据未知','TCD系数和样品动力学是显式工程输入'],source_ids=['manufacturer_specs','simulation_assumption'])
  if async_ or k in ['gas.open','thermal.set_temperature','path.set_temperature','power.set']:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='宿主步进热学、有限气流、反应与观测') for x in PHYSICS_EFFECTS if x!='clock.time_s']
 mech=dict(component_ids=['controller','furnace','sample_tube','gas_manifold','tcd','cold_trap'],state_ids=list(slots),meaning_zh='有限气体/反应基元与热节点的明确工程过程；独立实验参考不构成实测动力学',implementation='lumped',equations=['PV=nRT; finite9species inventory at18ports; open reactor vents positive excess above101325Pa; belowreferencepressure possible during cooldown','TPD:A_bound->A_gas; TPR:H2+O_bound->H2O; TPO:O2+C_bound->CO2; pulse:A_gas->A_bound; finite sites and reagent limit each update','k=A*exp(-Ea/RT); integrated fraction1-exp(-k*dt); explicit effective engineering coefficients','BackwardEuler thermalnode solves bounded proportionalheater implicitly: C*dT=heater_heat-convective_loss-feedgas_sensible_heat+reaction_heat; DeltaH evaluator supplied; endothermic extent limited to available thermalenergy above1K; stored=C*(T-Tambient)','TCD=sum(speciesmolefraction*explicit_sensitivity), firstorder response; displayedtemperature firstorder+0.1Kquantization; noOEM accuracyclaim','Independent trapezoidalTCDintegration and externalmoles/observedarea calibration; externalstoichiometry metaldispersion','RevC:ambient-5..1200C withoutCryo;0.1Csetpoint;1C/min minimum;100C/min to800C,50to1000C,25to1200C; gaspath<=150C','Only hoststep advances time; terminal snapshots retained; query ownership and evaluator process truth separated'],parameters={},source_ids=['manufacturer_specs','simulation_assumption'],limitations=['Reaction kinetics, thermal coefficients and TCD response are explicit assumptions; empirical mechanism/noise/fault validation not_run','Gas volume reference273.15K/101325Pa is simulation convention; N2calibrated1–100mL/min applies only with explicit mixturecorrection','Reference library entries preserve original instrument/conditions; not automatically converted into AutoChem kinetic curves','Cooling and gaspath firstorder responses are local engineering proxies, not manufacturer rate claims; heat ledger covers only furnace/sample effective node, not separate path heater','Cold sample-access323.15K, absolute-temperature floor1K and thermal trip1478.15K are simulation guards, not OEM certified limits','Gasphase-only model: coldtrap condensation, gasline adsorption, pressure regulator dynamics and full gas-path thermal energy are not modeled','No wallprocess/filesystem truth-isolation claim; noOEM robot/hardware or CryoCooler/MS option implementation'])
 spec=importlib.util.spec_from_file_location('_autochem_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update({k:v['meaning'] for k,v in SLOTS.items()});ann.annotate_export(SLUG,caps,slots,{'chemisorption':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'chemisorption':mech})

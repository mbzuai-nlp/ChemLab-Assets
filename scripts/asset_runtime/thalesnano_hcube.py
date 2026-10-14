"""H-Cube Pro: explicit external pump, water/H2/O2 ledger and conditional dilute hydrogenation."""
from __future__ import annotations
import copy,json,math
from asset_runtime import _annotations as _ann
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT,PHASE
from asset_runtime.antonpaar_abbematadvanced import validate
ROOT=Path(__file__).resolve().parents[2];ASSET_TYPE_ID='instrument.thalesnano_hcube';CATALOG_ID='CHEM-020';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/thalesnano_hcube_hydrogenation_library/templates.json';REFERENCE_REL='assets/reactions/thalesnano_hcube_hydrogenation_library/conversion_reference.json'
R=8.314462618;PN=101325.;TN=273.15;MLMIN=1e-6/60;MH=.00201588;MO=.0319988;MW=MH+.5*MO;MA=.134178;MB=MA+MH;RHO_W=997.
CARTS={'30mm':(296e-9,.0236),'70mm':(799e-9,.0636)}
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
def arr(s,n=None):
 a={'type':'array','items':s}
 if n is not None:a.update(minItems=n,maxItems=n)
 return a
FRAC=number('1',0,1);TEMP=number('K',283.15,423.15);PRESS=number('Pa',PN,1e7);FLOW=number('m^3/s',.3*MLMIN,3*MLMIN);H2FLOW=number('m^3/s',0,60*MLMIN);LIFT=number('m',0,.05);WATERLIFT=number('m',0,.03)
INLET={'type':'string','enum':['solvent','sample']};OUTLET={'type':'string','enum':['waste','product']}
PLANT=obj({'solvent_id':TEXT,'fit_id':TEXT,'cartridge_id':TEXT,'cartridge_size':{'type':'string','enum':list(CARTS)},'external_pump_connected':BOOL,'materials_and_recipe_reviewed':BOOL,'pure_carrier_confirmed':BOOL,'pre_equilibrated':BOOL,'density_kg_m3':number('kg/m^3',1,5000),'viscosity_pa_s':number('Pa*s',1e-5,.1),'initial_temperature_k':TEMP,'porosity':number('1',.1,.9),'particle_diameter_m':number('m',1e-5,.002),'dead_volumes_m3':arr(number('m^3',1e-9,1e-6),2),'feed_volume_m3':number('m^3',1e-7,.001),'solvent_volume_m3':number('m^3',1e-7,.001),'substrate_concentration_mol_m3':number('mol/m^3',0,500),'henry_mol_m3_pa':number('mol/m^3/Pa',0,.01),'kla_s_inv':number('1/s',0,100),'gas_drag_multiplier':number('1',0,10),'thermal_tau_s':number('s',.01,10000)})
EXAMPLE=dict(solvent_id='ethanol',fit_id='1',cartridge_id='catcart_001',cartridge_size='70mm',external_pump_connected=True,materials_and_recipe_reviewed=True,pure_carrier_confirmed=True,pre_equilibrated=True,density_kg_m3=790.,viscosity_pa_s=.001,initial_temperature_k=313.15,porosity=.4,particle_diameter_m=.0005,dead_volumes_m3=[1e-8]*2,feed_volume_m3=20e-6,solvent_volume_m3=20e-6,substrate_concentration_mol_m3=100.,henry_mol_m3_pa=2e-5,kla_s_inv=5.,gas_drag_multiplier=.1,thermal_tau_s=10.)
KINETICS=obj({'k_ref_s_inv':number('1/s',0,100),'reference_temperature_k':TEMP,'activation_energy_j_mol':number('J/mol',0,120000),'deactivation_s_inv':number('1/s',0,1),'initial_activity':FRAC,'calibration_evidence':TEXT})
KINETIC_EXAMPLE=dict(k_ref_s_inv=.5,reference_temperature_k=313.15,activation_energy_j_mol=30000.,deactivation_s_inv=.001,initial_activity=1.,calibration_evidence='synthetic_host_parameters; not fitted to a real catalyst')
METHOD=obj({'liquid_flow_m3_s':FLOW,'hydrogen_reference_flow_m3_s':H2FLOW,'temperature_k':TEMP,'pressure_abs_pa':PRESS,'duration_s':number('s',.01,86400)})
METHOD_EXAMPLE=dict(liquid_flow_m3_s=2*MLMIN,hydrogen_reference_flow_m3_s=42*MLMIN,temperature_k=313.15,pressure_abs_pa=4e6,duration_s=4.)
POINT=obj({'time_s':number('s',0),'elapsed_s':number('s',0),'temperature_k':nullable(TEMP),'pressure_abs_pa':PRESS,'liquid_flow_m3_s':number('m^3/s',0),'hydrogen_reference_flow_m3_s':H2FLOW,'route':OUTLET,'phase':PHASE})
RESULT=obj({'phase':PHASE,'fault':TEXT,'elapsed_s':number('s',0),'temperature_k':nullable(TEMP),'pressure_abs_pa':PRESS,'liquid_flow_m3_s':number('m^3/s',0),'hydrogen_reference_flow_m3_s':H2FLOW,'product_volume_m3':number('m^3',0),'waste_volume_m3':number('m^3',0),'real_conversion_fraction':{'type':'null'},'isolated_yield_fraction':{'type':'null'},'template_source':TEXT,'evidence':TEXT})
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet'],'供电；断电停止流量和电解，不回滚库存','device_command','agent',{'enabled':True},['power.enabled','cycle.phase']),
 'clamp.set_lift':(obj({'lift_m':LIFT}),obj({'lift_m':LIFT}),['cartridge_clamp'],'虚拟柱夹升降；运行/模型高压或高温时禁止打开','device_command','agent',{'lift_m':.05},['clamp.lift_m']),
 'water_lid.set_lift':(obj({'lift_m':WATERLIFT}),obj({'lift_m':WATERLIFT}),['water_lid'],'水箱盖教学升降，运行中禁止','device_command','agent',{'lift_m':.03},['water_lid.lift_m']),
 'water.fill':(obj({'volume_m3':number('m^3',1e-9,300e-6),'conductivity_s_m':number('S/m',0,7.1e-6)}),obj({'filled':BOOL}),['water_inlet'],'宿主打开水盖补DI水，容量300mL；水质量记账','configuration','evaluator',{'volume_m3':100e-6,'conductivity_s_m':1e-6},['water.volume_m3','truth.input_mass_kg']),
 'plant.configure':(PLANT,obj({'configured':BOOL,'attachment_event':TEXT}),['cartridge','external_pump','feed_port'],'宿主一次装柱/预充/外接泵；同载液下的肉桂醇形式氢化模型','configuration','evaluator',EXAMPLE,['plant.configured','truth.input_mass_kg','truth.initial_substrate_mol']),
 'kinetics.configure':(KINETICS,obj({'configured':BOOL}),['cartridge'],'首次运行前明确输入条件性速率与衰减，非厂家内置真实动力学','configuration','evaluator',KINETIC_EXAMPLE,['kinetics.configured','truth.catalyst_activity']),
 'method.configure':(METHOD,obj({'configured':BOOL}),['display_digits','external_pump','electrolyzer','heater'],'流量、参考供氢量、温压目标、时长；只写目标','configuration','agent',METHOD_EXAMPLE,['flow.target_m3_s','hydrogen.target_reference_m3_s','temperature.target_k','pressure.target_abs_pa','method.duration_s']),
 'inlet.set':(obj({'route':INLET}),obj({'route':INLET}),['inlet_valve'],'切换外接泵供液：纯溶剂或底物溶液','device_command','agent',{'route':'sample'},['inlet.route','inlet.angle_rad']),
 'outlet.set':(obj({'route':OUTLET}),obj({'route':OUTLET}),['outlet_valve'],'输出路由产品或废液，保留先前收集库存','device_command','agent',{'route':'product'},['outlet.route','outlet.angle_rad']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['external_pump','electrolyzer','cartridge'],'水盖关闭/柱夹闭合、供电/外接泵/配方确认；单相压力边界通过','device_command','agent',{},['cycle.phase','cycle.elapsed_s','cycle.fault']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['external_pump','electrolyzer'],'取消输送与产氢，残液/溶解氢仍可继续条件性反应','device_command','agent',{},['cycle.phase']),
 'hydrogenation.measure':(obj({}),RESULT,['display_digits'],'读取流温压工程观测，真实转化率和分离收率未知','query','agent',{},[]),
 'history.read':(obj({}),obj({'records':arr(POINT),'truncated':BOOL,'template_source':TEXT,'evidence':TEXT}),['display_digits'],'最多1000条1秒过程记录；不伪造实测仪器日志','query','agent',{},[]),
 'product.read':(obj({}),obj({'substrate_mol':number('mol',0),'formal_product_mol':number('mol',0),'dissolved_hydrogen_mol':number('mol',0),'conditional_conversion_fraction':nullable(FRAC),'evidence':TEXT}),['product_port'],'宿主读取条件性C9H10O+H2→C9H12O库存，不等于鉴定/真实收率','query','evaluator',{},[]),
 'thermo.lookup':(obj({'substance_id':TEXT,'fit_id':TEXT,'temperature_k':TEMP}),obj({'vapor_pressure_pa':number('Pa',0),'carrier_not_recommended':BOOL,'template_source':TEXT,'evidence':TEXT}),['display_digits'],'纯物质文献温段内蒸气压；材料/反应兼容性不由此保证','query','agent',{'substance_id':'ethanol','fit_id':'1','temperature_k':313.15},[]),
 'calibration.reference':(obj({'liquid_flow_m3_s':{'type':'number','enum':[MLMIN,2*MLMIN,3*MLMIN],'x-unit':'m^3/s'}}),obj({'reported_conversion_fraction':FRAC,'temperature_k':TEMP,'reported_pressure_pa':PRESS,'hydrogen_reference_flow_m3_s':H2FLOW,'substrate_concentration_mol_m3':number('mol/m^3',0),'conditions_zh':TEXT,'template_source':TEXT,'evidence':TEXT}),['display_digits'],'肉桂醇特定配置3个真实稳态参考标签，仅返回原条件，不外推/不隐式标定实例','query','agent',{'liquid_flow_m3_s':2*MLMIN},[]),
 'collector.unload':(obj({'container_id':TEXT}),obj({'unloaded':BOOL,'attachment_event':TEXT}),['product_port'],'停泵移走产品瓶及其库存，宿主记录释放事件','configuration','evaluator',{'container_id':'product_01'},['truth.removed_mass_kg','collection.product_m3']),
}
SLOTS={}
def slot(k,s,u,o='derived',v='agent',c='display_digits',w=None):SLOTS[k]=(s,u,o,v,[c],w)
slot('power.enabled',BOOL,'1','setpoint',c='power_inlet',w=('power.set','enabled'));slot('clamp.lift_m',LIFT,'m','scene',c='cartridge_clamp',w=('clamp.set_lift','lift_m'));slot('water_lid.lift_m',WATERLIFT,'m','scene',c='water_lid',w=('water_lid.set_lift','lift_m'))
slot('inlet.route',INLET,'1','setpoint',c='inlet_valve',w=('inlet.set','route'));slot('outlet.route',OUTLET,'1','setpoint',c='outlet_valve',w=('outlet.set','route'))
for k,s,u in [('plant.configured',BOOL,'1'),('kinetics.configured',BOOL,'1'),('cycle.phase',PHASE,'1'),('cycle.elapsed_s',number('s',0),'s'),('cycle.fault',TEXT,'1'),('method.duration_s',number('s',0),'s'),('flow.target_m3_s',FLOW,'m^3/s'),('hydrogen.target_reference_m3_s',H2FLOW,'m^3/s'),('temperature.target_k',TEMP,'K'),('pressure.target_abs_pa',PRESS,'Pa'),('flow.indicated_m3_s',number('m^3/s',0),'m^3/s'),('hydrogen.indicated_reference_m3_s',H2FLOW,'m^3/s'),('temperature.indicated_k',nullable(TEMP),'K'),('pressure.indicated_abs_pa',PRESS,'Pa'),('water.volume_m3',number('m^3',0,300e-6),'m^3'),('collection.product_m3',number('m^3',0),'m^3'),('collection.waste_m3',number('m^3',0),'m^3'),('inlet.angle_rad',number('rad',0,math.pi/2),'rad'),('outlet.angle_rad',number('rad',0,math.pi/2),'rad')]:slot(k,s,u)
for k in ['input','retained','product','waste','removed','vent']:slot('truth.'+k+'_mass_kg',number('kg',0),'kg','process_truth','evaluator','cartridge')
for k in ['initial_substrate','remaining_substrate','formal_product','generated_hydrogen','dissolved_hydrogen','reacted_hydrogen','vented_hydrogen','vented_oxygen']:slot('truth.'+k+'_mol',number('mol',0),'mol','process_truth','evaluator','cartridge')
slot('truth.catalyst_activity',FRAC,'1','process_truth','evaluator','cartridge');slot('truth.cells',arr(arr(number('mol',0),3),6),'mol','process_truth','evaluator','cartridge');slot('truth.bed_pressure_drop_pa',number('Pa',0),'Pa','process_truth','evaluator','cartridge')
class ThalesnanoHcubeRuntime:
 def __init__(self,instance_id='hcube_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.lift=0.;self.water_lift=0.;self.water_kg=0.;self.water_input_kg=0.;self.plant=None;self.kinetics=None;self.method=copy.deepcopy(METHOD_EXAMPLE);self.temp_k=313.15;self.pressure=PN;self.activity=0.;self.inlet='solvent';self.outlet='waste';self.phase='idle';self.elapsed_s=0.;self.fault='';self.operation_id=None;self._operation_count=0;self.history=[];self.history_truncated=False;self.next_record_s=0.;self.feed=0.;self.solvent=0.;self.initial_liquid=0.;self.initial_a=0.;self.cells=[[0.,0.,0.] for _ in range(6)];self.product=[0.,[0.,0.,0.]];self.waste=[0.,[0.,0.,0.]];self.removed=[0.,[0.,0.,0.]];self.h2_generated=0.;self.h2_reacted=0.;self.h2_vented=0.;self.o2_vented=0.;self.templates={x['substance_id']:x for x in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.reference=json.loads((ROOT/REFERENCE_REL).read_text());self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return ThalesnanoHcubeSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _fit(self,sid,fid,t):
  record=self.templates.get(sid);f=next((f for f in record['fits'] if f['fit_id']==fid),None) if record else None
  if not f or not f['runtime_temperature_range_k'][0]<=t<=f['runtime_temperature_range_k'][1]:raise ActionRejected('TEMPLATE_RANGE_UNAVAILABLE')
  return 1e5*10**(f['A']-f['B_k']/(t+f['C_k']))
 def vapor(self,t):return self._fit(self.plant['solvent_id'],self.plant['fit_id'],t) if self.plant else 0.
 def dp(self):
  if not self.plant:return 0.
  p=self.plant;m=self.method;u=m['liquid_flow_m3_s']/(math.pi*.004**2/4);eps=p['porosity'];d=p['particle_diameter_m'];length=CARTS[p['cartridge_size']][1];liquid=(150*p['viscosity_pa_s']*(1-eps)**2/(eps**3*d*d)*u+1.75*p['density_kg_m3']*(1-eps)/(eps**3*d)*u*u)*length;gas_actual=m['hydrogen_reference_flow_m3_s']*PN/m['pressure_abs_pa']*self.temp_k/TN
  return liquid*(1+p['gas_drag_multiplier']*gas_actual/m['liquid_flow_m3_s'])
 def _guard(self):
  if self.method['pressure_abs_pa']+self.dp()>1e7:return 'bed_overpressure'
  if self.method['pressure_abs_pa']<=max(self.vapor(self.temp_k),self.vapor(self.method['temperature_k'])):return 'carrier_vapor_risk'
  return ''
 def _pool_mass(self,pool):return pool[0]*(self.plant['density_kg_m3'] if self.plant else 0)+sum(n*m for n,m in zip(pool[1],[MA,MB,MH]))
 def _inventory(self):
  if not self.plant:return dict(input=self.water_input_kg,retained=self.water_kg,product=0.,waste=0.,removed=0.,vent=0.)
  rho=self.plant['density_kg_m3'];feed_a=self.feed*self.plant['substrate_concentration_mol_m3'];cell=[sum(c[i] for c in self.cells) for i in range(3)];retained=self.water_kg+(self.feed+self.solvent+sum(self.volumes))*rho+feed_a*MA+sum(x*m for x,m in zip(cell,[MA,MB,MH]));return dict(input=self.water_input_kg+self.initial_liquid*rho+self.initial_a*MA,retained=retained,product=self._pool_mass(self.product),waste=self._pool_mass(self.waste),removed=self._pool_mass(self.removed),vent=self.h2_vented*MH+self.o2_vented*MO)
 def _species(self,i):return sum(c[i] for c in self.cells)+sum(p[1][i] for p in [self.product,self.waste,self.removed])+(self.feed*self.plant['substrate_concentration_mol_m3'] if i==0 and self.plant else 0.)
 def _values(self):
  m=self.method;v={'power.enabled':self.powered,'clamp.lift_m':self.lift,'water_lid.lift_m':self.water_lift,'inlet.route':self.inlet,'outlet.route':self.outlet,'plant.configured':self.plant is not None,'kinetics.configured':self.kinetics is not None,'cycle.phase':self.phase,'cycle.elapsed_s':self.elapsed_s,'cycle.fault':self.fault,'method.duration_s':m['duration_s'],'flow.target_m3_s':m['liquid_flow_m3_s'],'hydrogen.target_reference_m3_s':m['hydrogen_reference_flow_m3_s'],'temperature.target_k':m['temperature_k'],'pressure.target_abs_pa':m['pressure_abs_pa'],'flow.indicated_m3_s':m['liquid_flow_m3_s'] if self.phase=='running' else 0.,'hydrogen.indicated_reference_m3_s':m['hydrogen_reference_flow_m3_s'] if self.phase=='running' else 0.,'temperature.indicated_k':round(self.temp_k-273.15,1)+273.15 if self.powered and self.plant else None,'pressure.indicated_abs_pa':min(1e7,max(PN,round(self.pressure/1e4)*1e4)),'water.volume_m3':self.water_kg/RHO_W,'collection.product_m3':self.product[0],'collection.waste_m3':self.waste[0],'inlet.angle_rad':math.pi/2 if self.inlet=='sample' else 0.,'outlet.angle_rad':math.pi/2 if self.outlet=='product' else 0.,'truth.initial_substrate_mol':self.initial_a,'truth.remaining_substrate_mol':self._species(0),'truth.formal_product_mol':self._species(1),'truth.generated_hydrogen_mol':self.h2_generated,'truth.dissolved_hydrogen_mol':self._species(2),'truth.reacted_hydrogen_mol':self.h2_reacted,'truth.vented_hydrogen_mol':self.h2_vented,'truth.vented_oxygen_mol':self.o2_vented,'truth.catalyst_activity':self.activity,'truth.cells':self.cells,'truth.bed_pressure_drop_pa':self.dp()}
  v.update({'truth.'+k+'_mass_kg':x for k,x in self._inventory().items()});return v
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('evaluator','debug')})
 def debug_snapshot(self):return dict(state=self.read('evaluator'),plant=copy.deepcopy(self.plant),history=copy.deepcopy(self.history))
 def _measure(self):
  v=self._values();return dict(phase=self.phase,fault=self.fault,elapsed_s=self.elapsed_s,temperature_k=v['temperature.indicated_k'],pressure_abs_pa=v['pressure.indicated_abs_pa'],liquid_flow_m3_s=v['flow.indicated_m3_s'],hydrogen_reference_flow_m3_s=v['hydrogen.indicated_reference_m3_s'],product_volume_m3=self.product[0],waste_volume_m3=self.waste[0],real_conversion_fraction=None,isolated_yield_fraction=None,template_source=TEMPLATE_REL,evidence='synthetic_conditioned_flow_pressure_temperature; real_NIST_carrier_vapor_fits; separate_measured_conversion_reference')
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if self.phase=='running' and capability not in ('power.set','acquisition.stop','inlet.set','outlet.set','hydrogenation.measure','history.read','product.read','thermo.lookup','calibration.reference'):raise ActionRejected('BUSY')
  if capability=='power.set':
   self.powered=a['enabled']
   if not self.powered and self.phase=='running':self.phase='cancelled'
   out=dict(a)
  elif capability=='clamp.set_lift':
   if a['lift_m']>self.lift and (self.pressure>PN+1 or (self.plant and self.temp_k>323.15)):raise ActionRejected('PRECONDITION_FAILED')
   self.lift=a['lift_m'];out=dict(a)
  elif capability=='water_lid.set_lift':self.water_lift=a['lift_m'];out=dict(a)
  elif capability=='water.fill':
   if self.water_lift<.029 or self.water_kg/RHO_W+a['volume_m3']>300e-6+1e-15:raise ActionRejected('PRECONDITION_FAILED')
   mass=a['volume_m3']*RHO_W;self.water_kg+=mass;self.water_input_kg+=mass;out={'filled':True}
  elif capability=='plant.configure':
   t=self.templates.get(a['solvent_id'])
   if self.plant or self.lift<.049 or not t or t['manufacturer_carrier_not_recommended'] or not all(a[k] for k in ['external_pump_connected','materials_and_recipe_reviewed','pure_carrier_confirmed','pre_equilibrated']):raise ActionRejected('PRECONDITION_FAILED')
   # Review flag does not turn documented incompatible neat carrier materials into compatible ones.
   if a['solvent_id'] in ('benzene','ethyl_acetate','acetic_anhydride','carbon_tetrachloride'):raise ActionRejected('PRECONDITION_FAILED')
   self._fit(a['solvent_id'],a['fit_id'],a['initial_temperature_k']);self._fit(a['solvent_id'],a['fit_id'],self.method['temperature_k']);p=copy.deepcopy(a);volume=CARTS[p['cartridge_size']][0]*p['porosity'];self.plant=p;self.volumes=[p['dead_volumes_m3'][0]]+[volume/4]*4+[p['dead_volumes_m3'][1]];self.temp_k=p['initial_temperature_k'];self.feed=p['feed_volume_m3'];self.solvent=p['solvent_volume_m3'];self.initial_liquid=self.feed+self.solvent+sum(self.volumes);self.initial_a=self.feed*p['substrate_concentration_mol_m3'];out=dict(configured=True,attachment_event='host_attach:'+p['cartridge_id'])
  elif capability=='kinetics.configure':
   if self.kinetics is not None or self.operation_id or not self.plant:raise ActionRejected('PRECONDITION_FAILED')
   self.kinetics=copy.deepcopy(a);self.activity=a['initial_activity'];out={'configured':True}
  elif capability=='method.configure':
   if self.plant:self.vapor(a['temperature_k'])
   self.method=copy.deepcopy(a);out={'configured':True}
  elif capability=='inlet.set':self.inlet=a['route'];out=dict(a)
  elif capability=='outlet.set':self.outlet=a['route'];out=dict(a)
  elif capability=='acquisition.start':
   if not self.powered or self.lift>1e-5 or self.water_lift>1e-5 or not self.plant or not self.kinetics or (self.method['hydrogen_reference_flow_m3_s']>0 and self.water_kg<=0):raise ActionRejected('PRECONDITION_FAILED')
   if self._guard():raise ActionRejected('PRECONDITION_FAILED')
   self._operation_count+=1;self.operation_id=f'{self.instance_id}:hydrogenate:{self._operation_count}';self.phase='running';self.elapsed_s=0.;self.fault='';self.history=[];self.history_truncated=False;self.next_record_s=self.time_s+1.;out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase=='running':self.phase='cancelled'
   out={'phase':self.phase}
  elif capability=='collector.unload':
   if a['container_id']!='product_01' or self.product[0]<=0:raise ActionRejected('PRECONDITION_FAILED')
   self.removed[0]+=self.product[0];self.removed[1]=[self.removed[1][i]+self.product[1][i] for i in range(3)];self.product=[0.,[0.,0.,0.]];out=dict(unloaded=True,attachment_event='host_release:product_01')
  elif capability=='thermo.lookup':return dict(vapor_pressure_pa=self._fit(a['substance_id'],a['fit_id'],a['temperature_k']),carrier_not_recommended=self.templates[a['substance_id']]['manufacturer_carrier_not_recommended'],template_source=TEMPLATE_REL,evidence='derived_from_NIST_literature_fit; not_whole_recipe_compatibility')
  elif capability=='calibration.reference':
   row=next(x for x in self.reference['values'] if abs(x['liquid_flow_m3_s']-a['liquid_flow_m3_s'])<1e-16);return dict(reported_conversion_fraction=row['reported_conversion_fraction'],temperature_k=313.15,reported_pressure_pa=4e6,hydrogen_reference_flow_m3_s=42*MLMIN,substrate_concentration_mol_m3=500.,conditions_zh='H-Cube Pro2.2/10%Pd-C；肉桂醇甲醇0.5M；压力基准未明、柱尺寸未知；100%为报道标签，不自动标定本实例。',template_source=REFERENCE_REL,evidence='measured_literature_steady_state_labels; no_extrapolation')
  elif capability=='history.read':return dict(records=copy.deepcopy(self.history),truncated=self.history_truncated,template_source=TEMPLATE_REL,evidence='synthetic_process_trace_not_measured_HCube_run')
  elif capability=='product.read':
   aa,bb,hh=self.product[1];return dict(substrate_mol=aa,formal_product_mol=bb,dissolved_hydrogen_mol=hh,conditional_conversion_fraction=bb/(aa+bb) if aa+bb>0 else None,evidence='conditional_host_kinetics; not_identified_product_or_isolated_yield')
  else:return self._measure()
  self.revision+=1;return copy.deepcopy(out)
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<=0:raise ValueError('finite positive dt required')
  left=float(dt_s)
  while left>1e-10:
   h=min(.02,left);p=self.plant;m=self.method;active=self.phase=='running';q=m['liquid_flow_m3_s'];gas=0.
   if p:h=min(h,*[.2*x/q for x in self.volumes])
   if active:
    h=min(h,m['duration_s']-self.elapsed_s);needed_h2=m['hydrogen_reference_flow_m3_s']*PN/(R*TN)*h;available=self.feed if self.inlet=='sample' else self.solvent;fault=self._guard()
    if available<q*h-1e-15:fault='liquid_feed_empty'
    if needed_h2*MW>self.water_kg+1e-15:fault='water_empty'
    if (self.product if self.outlet=='product' else self.waste)[0]+q*h>100e-6+1e-15:fault='collector_full'
    if fault:self.phase='failed';self.fault=fault;active=False
    else:
     gas=needed_h2;self.h2_generated+=gas;self.o2_vented+=gas/2;self.water_kg=max(0.,self.water_kg-gas*MW);self.pressure=m['pressure_abs_pa']+self.dp();old=copy.deepcopy(self.cells);outflows=[[n/self.volumes[i]*q*h for n in old[i]] for i in range(6)];feed=[q*h*p['substrate_concentration_mol_m3'] if self.inlet=='sample' else 0.,0.,0.]
     if self.inlet=='sample':self.feed=max(0.,self.feed-q*h)
     else:self.solvent=max(0.,self.solvent-q*h)
     self.cells=[[max(0.,old[i][j]+(feed[j] if i==0 else outflows[i-1][j])-outflows[i][j]) for j in range(3)] for i in range(6)];dest=self.product if self.outlet=='product' else self.waste;dest[0]+=q*h;dest[1]=[dest[1][j]+outflows[-1][j] for j in range(3)];self.elapsed_s+=h
     if self.elapsed_s>=m['duration_s']-1e-9:self.phase='succeeded'
   if not active:self.pressure=PN
   if p:
    target=m['temperature_k'] if self.powered else self.temp_k;self.temp_k=target+(self.temp_k-target)*math.exp(-h/p['thermal_tau_s']);ph=max(0.,self.pressure-self.vapor(self.temp_k));csat=p['henry_mol_m3_pa']*ph
    for i in range(1,5):
     c=self.cells[i];transfer=(csat-c[2]/self.volumes[i])*self.volumes[i]*(-math.expm1(-p['kla_s_inv']*h));transfer=min(gas,transfer) if transfer>=0 else max(-c[2],transfer);c[2]+=transfer;gas-=transfer
     if self.kinetics:
      k=self.kinetics['k_ref_s_inv']*self.activity*math.exp(-self.kinetics['activation_energy_j_mol']/R*(1/self.temp_k-1/self.kinetics['reference_temperature_k']));xi=min(c[0]*(-math.expm1(-k*h)),c[2]);c[0]-=xi;c[1]+=xi;c[2]-=xi;self.h2_reacted+=xi
    self.h2_vented+=max(0.,gas)
    if self.kinetics:self.activity*=math.exp(-self.kinetics['deactivation_s_inv']*h)
   self.time_s+=h;left-=h
   if self.operation_id and (self.time_s>=self.next_record_s-1e-9 or (active and self.phase!='running')):
    v=self._values();self.history.append(dict(time_s=self.time_s,elapsed_s=self.elapsed_s,temperature_k=v['temperature.indicated_k'],pressure_abs_pa=v['pressure.indicated_abs_pa'],liquid_flow_m3_s=v['flow.indicated_m3_s'],hydrogen_reference_flow_m3_s=v['hydrogen.indicated_reference_m3_s'],route=self.outlet,phase=self.phase));self.next_record_s=self.time_s+1.
    if len(self.history)>1000:self.history.pop(0);self.history_truncated=True
  self.revision+=1;return self.read('evaluator')
class ThalesnanoHcubeSession:
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
        meta=export_thalesnano_hcube_contract_metadata()
        return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',
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
        result=h.invoke(capability_id,**arguments); op=result.get('operation_id')
        if op: h._owners[op]=self._principal
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

def export_thalesnano_hcube_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'ThalesnanoHcubeSession.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key=='acquisition.start'; pre=[]
        conditions={
         'acquisition.start':('供电夹紧、水盖闭合、宿主外接泵/预充/配方确认，压降和汽化边界允许',['power.enabled','clamp.lift_m','water_lid.lift_m','plant.configured','kinetics.configured'],'powered and clamped and closed and configured and hydraulic_guard'),
         'plant.configure':('首次开夹装柱，宿主确认外泵/材料/液态，源温段支持',['plant.configured','clamp.lift_m'],'not configured and open and valid_pure_carrier'),
         'clamp.set_lift':('停泵；模型压力接近大气压、模型柱温不超过50C才允许打开；非安全认证',['cycle.phase','pressure.indicated_abs_pa','temperature.indicated_k'],'not running and ambient_model_pressure and temperature<=323.15'),
         'water.fill':('停泵、水盖打开、DI水导电率和总容量有效',['cycle.phase','water_lid.lift_m','water.volume_m3'],'not running and lid_open and total_volume<=300mL'),
         'method.configure':('停泵，选定载液拟合支持目标温度',['cycle.phase'],'not running and valid_fit_temperature'),
         'collector.unload':('停泵，有产品液且容器身份匹配',['cycle.phase','collection.product_m3'],'not running and product_nonempty and expected_container')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.thalesnano_hcube.ThalesnanoHcubeSession.invoke',invocation=f'session.invoke(instance_id="hcube_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；达到输送时长；取消保留残液、溶解氢和条件性产物库存' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电不回滚已经输送/收集的液体/水/气体和形式物种库存。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    evolving=[k for k in slots if k.startswith('truth.')]+['cycle.elapsed_s','cycle.phase','cycle.fault','temperature.indicated_k','pressure.indicated_abs_pa','flow.indicated_m3_s','hydrogen.indicated_reference_m3_s','water.volume_m3','collection.product_m3','collection.waste_m3']
    caps['acquisition.start']['state_effects'] += [dict(state_id=k,timing='on_step',description_zh='step推进守恒输送、电解、有限气液传质、条件性反应与衰减') for k in evolving if k not in [e['state_id'] for e in caps['acquisition.start']['state_effects']]]
    for key in ['plant.configure','collector.unload','water.fill']:
        caps[key]['state_effects'] += [dict(state_id=k,timing='immediate',description_zh='装入/移除物料影响库存') for k in evolving if k.startswith('truth.') and k not in [e['state_id'] for e in caps[key]['state_effects']]]
    caps['method.configure']['state_effects'].append(dict(state_id='temperature.indicated_k',timing='on_step',description_zh='一阶未标定设定响应，无能量账本'))
    mechanism=dict(component_ids=['external_pump','electrolyzer','water_tank','mixer','cartridge','heater','outlet_valve'],state_ids=evolving,meaning_zh='固定密度稀载液+形式肉桂醇氢化；气液混合与柱衰减均为宿主参数条件模型',implementation='lumped',equations=['Psat=1e5*10**(A-B/(T+C)); inside selected NIST interval only','Vliquid=empty_CatCart_volume*host_porosity; inlet CSTR -> 4 packed-bed CSTRs -> outlet CSTR','dN_i=Nin-Nout; Nout=C_old*Q*dt; dt<=0.2*V/Q','H2_generated=Q_N*101325/(R*273.15)*dt; H2O -> H2 + 0.5 O2','Ergun dP=L*(150*mu*(1-eps)^2*u/(eps^3*dp^2)+1.75*rho*(1-eps)*u^2/(eps^3*dp))','dP_two_phase=dP_liquid*(1+host_drag*Qgas_actual/Qliquid); Ptarget+dP<=1e7 Pa absolute assumption','Csat=Henry*max(0,P-Psat); gas/liquid transfer=(Csat-C)*V*(1-exp(-kLa*dt)), limited by available H2','A+H2 -> B; extent=min(A*(1-exp(-k*dt)),dissolved_H2); M_B=M_A+M_H2','k=kref*activity*exp(-Ea/R*(1/T-1/Tref)); activity*=exp(-kd*dt)','input mass=retained+product+waste+removed+vent mass; total initial A=remaining A+B; generated H2=dissolved+reacted+vented','T=Ttarget+(Told-Ttarget)*exp(-dt/tau) while powered; unpowered holds temperature (no ambient model)'],parameters={},source_ids=['manufacturer_primary','manufacturer_catcart','vapor_templates','conversion_reference','simulation_assumption'],limitations=['No empirically identified Henry/kLa/drag/kinetics/deactivation or reaction heat. Supplied parameters explicitly conditional.','3 measured conversion labels at specific H-Cube Pro2.2/10%Pd-C/methanol conditions are separate reference queries, not a calibrated universal yield function.','Pressure is algebraic when pumping and returns to atmospheric on next idle step; no compressible gas holdup/real depressurization dynamics or safety certification.','Pure carrier/dilute formal A and B with additive masses but no partial molar volumes; no analytical product identification.','Pre-primed single cartridge; no live swap, drainage, purge, autosampler or external NMR/separator.','Compatibility ratings retain steel/PTFE distinctions and manufacturer bans, but host review flag is not a complete chemical compatibility or safety assessment.','Sequential gas contact and excess vent; catalyst ages while wet including pauses; off temperature held, no energy balance.'])
    # Cross-cutting side effects remain explicit even after a cancellation.
    for key in ['power.set','acquisition.stop']:
        for state in ['flow.indicated_m3_s','hydrogen.indicated_reference_m3_s']:
            if state not in [e['state_id'] for e in caps[key]['state_effects']]:caps[key]['state_effects'].append(dict(state_id=state,timing='immediate',description_zh='停泵/电解时读数归零，库存不回滚'))
        for state in ['pressure.indicated_abs_pa','temperature.indicated_k']+[k for k in slots if k.startswith('truth.')]:
            if state not in [e['state_id'] for e in caps[key]['state_effects']]:caps[key]['state_effects'].append(dict(state_id=state,timing='on_step',description_zh='下一步代数降压；残氢传质/反应和湿柱衰减继续，无实机泄压/热量标定'))
    for key in ['method.configure','plant.configure','kinetics.configure']:
        for state in ['truth.bed_pressure_drop_pa','truth.catalyst_activity']:
            if state not in [e['state_id'] for e in caps[key]['state_effects']]:caps[key]['state_effects'].append(dict(state_id=state,timing='immediate',description_zh='显式参数影响条件模型派生量'))
    for key in ['plant.configure','method.configure','thermo.lookup']:
        if 'TEMPLATE_RANGE_UNAVAILABLE' not in [e['code'] for e in caps[key]['errors']]:caps[key]['errors'].append(dict(code='TEMPLATE_RANGE_UNAVAILABLE',meaning_zh='物质/拟合不存在或超出原文献温段',side_effects='none'))
    for key,slot in slots.items():
        if key.startswith('hydrogen.'):slot['reference_conditions']='Reference volume 273.15 K / 101325 Pa; model convention, manufacturer normal datum unspecified'
        if key.startswith('pressure.'):slot['reference_conditions']='Absolute pressure convention; manufacturer gauge/absolute datum unspecified'
        if '.target' in key or key=='method.duration_s':slot['origin']='setpoint'
    _ann.annotate_export('thalesnano_hcube',caps,slots,{'conditional_hydrogenation':mechanism},inputs={'plant.configure':(PLANT,EXAMPLE),'kinetics.configure':(KINETICS,KINETIC_EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'conditional_hydrogenation':mechanism})

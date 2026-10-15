"""CHEM-116 finite mercury intrusion with measured branches and explicit transfer uncertainty."""
from pathlib import Path
import copy,math,json,importlib.util
PROTOCOL_VERSION='1.0.0';SLUG='antonpaar_poremaster60';CATALOG_ID='CHEM-116';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch03/t4_measure'/SLUG
class ActionRejected(ValueError):pass
def number(unit='1',lo=None,hi=None):
 d={'type':'number','x-unit':unit}
 if lo is not None:d['minimum']=lo
 if hi is not None:d['maximum']=hi
 return d
def obj(p):return dict(type='object',properties=p,required=list(p),additionalProperties=False)
def arr(v,n=None):
 d=dict(type='array',items=v)
 if n is not None:d.update(minItems=n,maxItems=n)
 return d
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
def enum(v):return {'type':'string','enum':v}
TEXT={'type':'string'};BOOL={'type':'boolean'};PHASE=enum(['idle','running','succeeded','cancelled','failed'])
def validate(s,v):
 if 'anyOf' in s:
  for q in s['anyOf']:
   try:validate(q,v);return
   except ActionRejected:pass
  raise ActionRejected('INVALID_ARGUMENT')
 t=s.get('type')
 if t in ('number','integer'):
  if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or (t=='integer' and type(v) is not int) or not s.get('minimum',-math.inf)<=v<=s.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='boolean' and type(v) is not bool:raise ActionRejected('INVALID_ARGUMENT')
 elif t=='string' and not isinstance(v,str):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='null' and v is not None:raise ActionRejected('INVALID_ARGUMENT')
 elif t=='object':
  if not isinstance(v,dict) or not set(s.get('required',[]))<=v.keys() or set(v)-set(s['properties']):raise ActionRejected('INVALID_ARGUMENT')
  for k,x in v.items():validate(s['properties'][k],x)
 elif t=='array':
  if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
  for x in v:validate(s['items'],x)
 if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')
import bisect
DT=.1;R=8.314462618;PSI=6894.757293168;AMBIENT=101325.;P_MAX=60000*PSI
LIB=json.loads((ASSET/'porosimetry_library.json').read_text());SID='hemp_shiv'
INT=lambda lo,hi:dict(number('1',lo,hi),type='integer')
PLANT=obj({'temperature_k':number('K',283.15,313.15),'source_volume_m3':number('m^3',1e-5,.1),'source_pressure_pa':number('Pa',50*PSI,1e6),'gas_rate_s':number('1/s',.1,20),'pump_rate_s':number('1/s',.1,20),'chamber_volume_m3':number('m^3',1e-6,.01),'oil_volume_m3':number('m^3',1e-7,.01),'hp_dead_volume_m3':number('m^3',1e-6,.001),'oil_bulk_modulus_pa':number('Pa',1e8,1e10),'oil_flow_m3_s':number('m^3/s',1e-10,1e-4),'intrusion_rate_s':number('1/s',.01,10),'hg_density_kg_m3':number('kg/m^3',13000,14000),'hg_bulk_modulus_pa':number('Pa',1e9,1e11),'glass_compliance_m3_pa':number('m^3/Pa',0,1e-15),'capacitance_volume_factor_m3_f':number('m^3/F',1000,100000),'capacitance_offset_f':number('F',-1e-9,1e-9),'pressure_gain':arr(number('1',.8,1.2),3),'pressure_offset_pa':arr(number('Pa',-1e5,1e5),3),'accept_engineering_model':BOOL})
PLANT_EXAMPLE=dict(temperature_k=292.05,source_volume_m3=.002,source_pressure_pa=600000.,gas_rate_s=5.,pump_rate_s=8.,chamber_volume_m3=.0001,oil_volume_m3=.0002,hp_dead_volume_m3=.0001,oil_bulk_modulus_pa=1.4e9,oil_flow_m3_s=2e-6,intrusion_rate_s=2.,hg_density_kg_m3=13548.9,hg_bulk_modulus_pa=2.85e10,glass_compliance_m3_pa=1e-18,capacitance_volume_factor_m3_f=20000.,capacitance_offset_f=1e-12,pressure_gain=[1.]*3,pressure_offset_pa=[0.]*3,accept_engineering_model=True)
SAMPLE=obj({'sample_id':TEXT,'reference_id':TEXT,'true_dry_mass_kg':number('kg',1e-8,.1),'true_bulk_volume_m3':number('m^3',1e-10,18e-6),'dry':BOOL,'accept_cross_instrument_transfer':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='hemp_01',reference_id=SID,true_dry_mass_kg=.0000655,true_bulk_volume_m3=.0000655/306.1,dry=True,accept_cross_instrument_transfer=True)
METHOD=obj({'stage':enum(['low','high']),'pressures_pa':arr(number('Pa',.2*PSI,P_MAX)),'pressure_rate_pa_s':number('Pa/s',1,2e8),'equilibration_s':number('s',.2,300),'pressure_tolerance_pa':number('Pa',.1,30000),'max_volume_drift_m3_s':number('m^3/s',1e-15,1e-7),'point_timeout_s':number('s',1,7200),'entered_mass_kg':number('kg',1e-8,.1),'closure_m3_kg':number('m^3/kg',0,.1),'surface_tension_n_m':number('N/m',.4,.6),'advancing_angle_rad':number('rad',math.pi/2+.01,math.pi),'receding_angle_rad':number('rad',math.pi/2+.01,math.pi)})
METHOD_EXAMPLE=dict(stage='low',pressures_pa=[3*PSI,10*PSI,30*PSI,50*PSI],pressure_rate_pa_s=50000.,equilibration_s=2.,pressure_tolerance_pa=15.,max_volume_drift_m3_s=1e-12,point_timeout_s=180.,entered_mass_kg=.0000655,closure_m3_kg=0.,surface_tension_n_m=.48,advancing_angle_rad=140*math.pi/180,receding_angle_rad=140*math.pi/180)
PCAL=obj({'range_index':INT(0,2),'reference_pa':arr(number('Pa',0,P_MAX),2),'measured_pa':arr(number('Pa',-1e5,1.3*P_MAX),2)})
PCAL_EXAMPLE=dict(range_index=0,reference_pa=[0.,50*PSI],measured_pa=[0.,50*PSI])
VCAL=obj({'reference_volume_m3':arr(number('m^3',0,2e-6),2),'measured_capacitance_f':arr(number('F',-1e-9,1e-9),2)})
VCAL_EXAMPLE=dict(reference_volume_m3=[0.,.2e-6],measured_capacitance_f=[1e-12,11e-12])
BLANK=obj({'pressures_pa':arr(number('Pa',0,P_MAX)),'volumes_m3':arr(number('m^3',-1e-6,1e-6))})
POINT=obj({'time_s':number('s',0),'stage':enum(['low','high']),'branch':enum(['intrusion','extrusion']),'pressure_pa':number('Pa',0),'intrusion_volume_m3':number('m^3'),'specific_volume_m3_kg':number('m^3/kg'),'corrected_pore_volume_m3_kg':number('m^3/kg',0),'diameter_m':nullable(number('m',0))})
PSD=obj({'diameter_m':arr(number('m',0)),'incremental_volume_m3_kg':arr(number('m^3/kg')),'differential_log_volume_m3_kg':arr(number('m^3/kg')),'status':TEXT})
RESULT=obj({'template_source':TEXT,'evidence':TEXT,'phase':PHASE,'stage':TEXT,'elapsed_s':number('s',0),'points':arr(POINT),'distribution':PSD,'max_pore_volume_m3_kg':nullable(number('m^3/kg',0)),'warnings':arr(TEXT)})
LEDGER=obj({'hg_added_kg':number('kg',0),'hg_stock_kg':number('kg',0),'hg_free_in_cell_kg':number('kg',0),'hg_intruded_kg':number('kg',0),'hg_waste_kg':number('kg',0),'hg_returned_with_sample_kg':number('kg',0),'hg_residual_kg':number('kg'),'solid_loaded_kg':number('kg',0),'solid_present_kg':number('kg',0),'solid_returned_kg':number('kg',0),'solid_residual_kg':number('kg'),'gas_source_mol':number('mol',0),'gas_chamber_mol':number('mol',0),'gas_exhaust_mol':number('mol',0),'gas_residual_mol':number('mol'),'oil_reservoir_m3':number('m^3',0),'oil_pressurized_inventory_m3':number('m^3',0),'oil_residual_m3':number('m^3'),'hydraulic_stored_energy_j':number('J',0),'hydraulic_pump_work_j':number('J',0),'hydraulic_release_work_j':number('J',0),'capillary_work_j':number('J'),'work_balance_error_j':number('J')})
def interp(xs,ys,p):
 if p<=xs[0]:return ys[0]
 if p>=xs[-1]:return ys[-1]
 i=bisect.bisect_right(xs,p)-1;return ys[i]+(ys[i+1]-ys[i])*(p-xs[i])/(xs[i+1]-xs[i])
def curve(ref,branch):
 a={}
 for p in LIB['substances'][ref]['value']:
  if p['branch']==branch:a.setdefault(p['pressure_pa'],[]).append(p['volume_m3_kg'])
 rows=sorted((p,sum(v)/len(v)) for p,v in a.items());blocks=[]
 for p,v in rows:
  blocks.append([max(v,0.),1])
  while len(blocks)>1 and blocks[-2][0]/blocks[-2][1]>blocks[-1][0]/blocks[-1][1]:b=blocks.pop();blocks[-1][0]+=b[0];blocks[-1][1]+=b[1]
 values=[s/n for s,n in blocks for _ in range(n)];return [p for p,v in rows],values
CURVES={(k,b):curve(k,b) for k in LIB['substances'] for b in ['intrusion','extrusion']}
def meta(ref):
 d=LIB['substances'][ref];return dict(material_group=d['material_group'],material_class=d.get('material_class') or 'unspecified',pressure_axis=d.get('pressure_axis') or 'measured',volume_basis=d.get('volume_basis') or 'measured_cumulative',licence=d.get('licence') or d['source'].get('license') or 'see source')
MATERIAL_GROUPS=sorted({d['material_group'] for d in LIB['substances'].values()})
COUNT_NOTE=f"{len(LIB['substances'])}references/{len(MATERIAL_GROUPS)}materialgroups; specimens of one group are not independent materials; cross-instrument calibration transfer assumed"
def distribution(points):
 a=[p for p in points if p['branch']=='intrusion' and p['diameter_m'] and p['pressure_pa']>0];a=sorted(a,key=lambda p:p['pressure_pa']);merged=[]
 def resolution(p):return (.000763 if p['stage']=='low' else .0229 if p['pressure_pa']<=1500*PSI else .916)*PSI
 for p in a:
  if merged and p['pressure_pa']-merged[-1]['pressure_pa']<=.5*(resolution(p)+resolution(merged[-1])):
   if resolution(p)<resolution(merged[-1]):merged[-1]=p
  else:merged.append(p)
 a=merged;d=[];v=[];dv=[]
 for x,y in zip(a,a[1:]):
  if y['diameter_m']>=x['diameter_m']:continue
  diff=y['corrected_pore_volume_m3_kg']-x['corrected_pore_volume_m3_kg'];d.append(math.sqrt(x['diameter_m']*y['diameter_m']));v.append(diff);dv.append(diff/math.log10(x['diameter_m']/y['diameter_m']))
 return dict(diameter_m=d,incremental_volume_m3_kg=v,differential_log_volume_m3_kg=dv,status='apparent_connected_throat_Washburn' if d else 'insufficient_points')
CAPS={};SLOTS={};SLOT_ZH={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},effects or [])
def slot(k,s,u,z,o='derived',v='agent',c='controller',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller','oil_pump'],'宿主选择有限气源、液压系统、响应及传感器假设','configuration','evaluator',PLANT_EXAMPLE,['plant.configured'])
cap('cell.configure',obj({'bulb_volume_m3':dict(number('m^3'),enum=[3.2e-6,6.6e-6,18e-6]),'stem_capacity_m3':dict(number('m^3'),enum=[.5e-6,2e-6])}),obj({'configured':BOOL}),['cell_z','cell_stem'],'选择原厂样品球泡及计量毛细管容量；几何固定3.2cc示意','configuration',ex={'bulb_volume_m3':3.2e-6,'stem_capacity_m3':.5e-6})
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['cell_z','sample_solid'],'宿主装入有限干固体，明确曲线跨仪器迁移','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.unload',obj({}),obj({'returned_dry_mass_kg':number('kg',0),'retained_hg_kg':number('kg',0)}),['cell_z','sample_solid'],'自由汞排出后取回干样和滞留汞，保留耗材账本',vis='evaluator')
cap('mercury.fill',obj({'mass_kg':number('kg',1e-5,1)}),obj({'stock_kg':number('kg',0)}),['hg_reservoir'],'宿主加入有限汞库存，压入液不凭空生成','configuration','evaluator',{'mass_kg':.1})
cap('cell.drain',obj({}),obj({'waste_hg_kg':number('kg',0)}),['cell_z','hg_reservoir'],'卸压低压位人工排出自由汞；孔内滞留汞随样品返回',vis='evaluator')
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_switch','status_lamp'],'开关电源；断电保留压力和耗材，后续step卸压',ex={'enabled':True},effects=['power.enabled','power.angle_rad','lamp.power'])
for i in range(2):cap('guard'+str(i)+'.set',obj({'angle_rad':number('rad',0,1.4)}),obj({'angle_rad':number('rad',0,1.4)}),['guard'+str(i)],'低压位蓝罩人工开合；压力和运行互锁',ex={'angle_rad':0.},effects=['guard'+str(i)+'.angle_rad'])
cap('cap.set',obj({'lift_m':number('m',0,.19)}),obj({'lift_m':number('m',0,.19)}),['hp_cap'],'标准型手动高压盖拆装，非GT自动气动升降',ex={'lift_m':0.},effects=['cap.lift_m'])
cap('cell.transfer',obj({'destination':enum(['low0','low1','high'])}),obj({'position':TEXT}),['cell_x','cell_y','cell_z'],'受检人工转移同一有限样品管；两低压位可选，一高压位',vis='evaluator',ex={'destination':'high'},effects=['cell.x_m','cell.y_m','cell.z_m','cell.position'])
cap('method.configure',METHOD,obj({'configured':BOOL}),['controller'],'低高压分阶段扫描、平衡、输入质量、空隙闭合修正和Washburn参数','configuration',ex=METHOD_EXAMPLE)
cap('pressure.calibrate',PCAL,obj({'calibrated':BOOL}),['pressure_sensor_0','pressure_sensor_1','pressure_sensor_2'],'独立外部两点压力标定，三量程分别标定','configuration',ex=PCAL_EXAMPLE)
cap('volume.calibrate',VCAL,obj({'factor_m3_f':number('m^3/F',0),'zero_f':number('F')}),['cell_contact','controller'],'用独立外部已知体积和电容反演仪器体积标定','configuration',ex=VCAL_EXAMPLE)
cap('blank.calibrate',BLANK,obj({'points':INT(2,100000)}),['cell_stem','controller'],'保存空池/汞压缩/玻璃形变的外部实测或明确工程空白','configuration',ex={'pressures_pa':[0.,P_MAX],'volumes_m3':[0.,0.]})
cap('acquisition.start',obj({}),obj({'operation_id':TEXT,'phase':PHASE}),['controller','lp_manifold_0','lp_manifold_1','hp_vessel'],'低压抽空充汞或高压有限泵送，升降压测量，结束自动卸压',effects=['acquisition.phase','acquisition.stage'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['controller'],'取消保留压力与全部物质，step继续卸压',effects=['acquisition.phase'])
cap('emergency.stop',obj({}),obj({'phase':PHASE}),['front_estop'],'紧急停止升压；有限卸压需要后续仿真时钟',effects=['acquisition.phase'])
cap('process.measure',obj({}),RESULT,['controller'],'只读累计压入/退出点、孔容和孔喉分布','query')
cap('ledger.read',obj({}),LEDGER,['controller'],'有限汞/样品/N2/油及限定机械功账本','query','evaluator')
cap('template.reference',obj({'substance_id':TEXT}),obj({'name':TEXT,'evidence':TEXT,'source':TEXT,'intrusion_points':INT(0,100000),'extrusion_points':INT(0,100000),'material_group':TEXT,'material_class':TEXT,'pressure_axis':TEXT,'volume_basis':TEXT,'licence':TEXT}),['controller'],'参考曲线原始证据、材料组、压力轴/体积来源、许可和分支计数','query',ex={'substance_id':SID})
UNSUPPORTED={'hardware.connect','fault.probability_model','robot.load','low.dual_parallel','analysis.permeability','analysis.true_pore_network','coldtrap.vapour_model'}
for k in sorted(UNSUPPORTED):cap(k,obj({}),obj({}),['controller'],'未实现；不生成伪数据')
slot('plant.configured',BOOL,'1','宿主模型已配置','setpoint');slot('power.enabled',BOOL,'1','电源开关','setpoint',w=('power.set','enabled'));slot('power.angle_rad',number('rad',0,.32),'rad','前部电源摇臂位置','scene',c='power_switch');slot('lamp.power',BOOL,'1','前部电源灯','derived',c='status_lamp')
for i in range(2):slot('guard'+str(i)+'.angle_rad',number('rad',0,1.4),'rad','低压保护罩转角','scene',c='guard'+str(i),w=('guard'+str(i)+'.set','angle_rad'))
slot('cap.lift_m',number('m',0,.19),'m','手动高压盖移开高度','scene',c='hp_cap',w=('cap.set','lift_m'))
for a,h in [('x',.328),('y',.185),('z',.265)]:slot('cell.'+a+'_m',number('m',0,h),'m','同一玻璃管人工移位'+a,'scene',c='cell_'+a)
for k,s,u,z in [('position',TEXT,'1','玻璃管所处分析位'),('filled',BOOL,'1','样品管已充汞'),('loaded',BOOL,'1','样品管已装入固体')]:slot('cell.'+k,s,u,z,c='cell_z')
for k,s,u,z in [('phase',PHASE,'1','当前异步操作状态'),('stage',TEXT,'1','抽空/填充/压力平衡/卸压阶段'),('elapsed_s',number('s',0),'s','异步操作耗时'),('failure',TEXT,'1','运行失败原因')]:slot('acquisition.'+k,s,u,z)
slot('pressure.target_pa',number('Pa',0,P_MAX),'Pa','设定压力','setpoint');slot('pressure.measured_pa',number('Pa',0),'Pa','当前量程标定压力读数','sensor');slot('volume.measured_m3',nullable(number('m^3')),'m^3','独立电容标定和空白校正后的体积','sensor');slot('capacitance.measured_f',number('F'),'F','电容读数代理','sensor',c='cell_contact');slot('sample.identity',TEXT,'1','样品私有身份','process_truth','evaluator','sample_solid');slot('sample.mass_kg',number('kg',0),'kg','真实干质量','process_truth','evaluator','sample_solid');slot('sample.intruded_m3',number('m^3',0),'m^3','真实汞侵入量','process_truth','evaluator','sample_solid');slot('sample.pressure_pa',number('Pa',0),'Pa','真实毛细管驱动绝压','process_truth','evaluator');slot('results.point_count',INT(0,100000),'1','当前已完成的测量点数');slot('mercury.stock_kg',number('kg',0),'kg','汞库存余量','sensor',c='hg_reservoir')
# Public observable changes from synchronous commands, including derived sensor values.
# Conditional effects are listed even when a particular invocation leaves that value unchanged.
IMMEDIATE_EFFECTS={
 'plant.configure':['plant.configured','pressure.measured_pa','capacitance.measured_f'],
 'cell.configure':['volume.measured_m3'],
 'sample.load':['cell.loaded','sample.identity','sample.mass_kg','sample.intruded_m3','results.point_count'],
 'sample.unload':['cell.loaded','sample.identity','sample.mass_kg','sample.intruded_m3','capacitance.measured_f','volume.measured_m3'],
 'mercury.fill':['mercury.stock_kg'],
 'cell.drain':['cell.filled','capacitance.measured_f','volume.measured_m3'],
 'power.set':['power.enabled','power.angle_rad','lamp.power','acquisition.phase','acquisition.stage','pressure.target_pa'],
 'cell.transfer':['cell.x_m','cell.y_m','cell.z_m','cell.position','acquisition.stage','pressure.target_pa','sample.pressure_pa','pressure.measured_pa','capacitance.measured_f','volume.measured_m3'],
 'pressure.calibrate':['pressure.measured_pa','volume.measured_m3'],
 'volume.calibrate':['volume.measured_m3'],
 'blank.calibrate':['volume.measured_m3'],
 'acquisition.start':['acquisition.phase','acquisition.stage','acquisition.elapsed_s','acquisition.failure','pressure.target_pa'],
 'acquisition.stop':['acquisition.phase','acquisition.stage','pressure.target_pa'],
 'emergency.stop':['acquisition.phase','acquisition.stage','pressure.target_pa'],
}
STEP_EFFECTS=['pressure.target_pa','pressure.measured_pa','volume.measured_m3','capacitance.measured_f','sample.pressure_pa','sample.intruded_m3','mercury.stock_kg','cell.filled','acquisition.elapsed_s','acquisition.phase','acquisition.stage','acquisition.failure','results.point_count']
class AntonpaarPoremaster60Runtime:
 def __init__(self,instance_id='poremaster60_01'):
  self.instance_id=instance_id;self.time_s=0.;self.pending=0.;self.revision=0;self.plant=None;self.powered=False;self.guard=[0.,0.];self.cap=0.;self.position='low0';self.xyz=[0.,0.,0.];self.bulb=3.2e-6;self.capacity=.5e-6;self.sample=None;self.filled=False;self.hg_added=0.;self.hg_stock=0.;self.hg_cell=0.;self.hg_waste=0.;self.hg_returned=0.;self.v=0.;self.loaded=0.;self.returned=0.;self.gas=0.;self.source=0.;self.gas_initial=0.;self.exhaust=0.;self.oil=0.;self.oil_x=0.;self.p=AMBIENT;self.target=AMBIENT;self.peak=0.;self.pcal=[None]*3;self.vcal=None;self.blank=None;self.method=None;self.phase='idle';self.stage='idle';self.failure='';self.elapsed=0.;self.index=0;self.stable=0.;self.point_time=0.;self.points=[];self.all_points=[];self.serial=0;self.operation_id=None;self.records={};self._principals={};self._owners={};self._requests={};self.pump_work=0.;self.release_work=0.;self.capillary_work=0.;self.work_error=0.;self.branch='intrusion';self.result_source=''
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ['agent','debug','evaluator'] or principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return AntonpaarPoremaster60Session(self,principal_id)
 def describe(self):return export_antonpaar_poremaster60_contract_metadata()
 def range_index(self):return 0 if self.position!='high' else 1 if self.p<=1500*PSI else 2
 def resolution(self):return [.000763,.0229,.916][self.range_index()]*PSI
 def indicated(self):
  if not self.plant:return self.p
  i=self.range_index();v=self.p*self.plant['pressure_gain'][i]+self.plant['pressure_offset_pa'][i]
  if self.pcal[i]:v=(v-self.pcal[i][1])/self.pcal[i][0]
  return max(0.,round(v/self.resolution())*self.resolution())
 def blank_truth(self,p):return self.hg_cell/self.plant['hg_density_kg_m3']*(1-math.exp(-p/self.plant['hg_bulk_modulus_pa']))+self.plant['glass_compliance_m3_pa']*p if self.plant and self.filled else 0.
 def capacitance(self):return (self.v+self.blank_truth(self.p))/self.plant['capacitance_volume_factor_m3_f']+self.plant['capacitance_offset_f'] if self.plant else 0.
 def measured(self):
  if not self.vcal:return None
  v=(self.capacitance()-self.vcal[1])*self.vcal[0];v-=interp(*self.blank,self.indicated()) if self.blank else 0.;res=3e-11 if self.capacity==.5e-6 else 9e-11;return round(v/res)*res
 def storage(self,p):return self.plant['hp_dead_volume_m3']*(-math.expm1(-max(p-AMBIENT,0)/self.plant['oil_bulk_modulus_pa']))
 def pressure_hp(self,x,v):
  f=max(0.,x-v)/self.plant['hp_dead_volume_m3'];return AMBIENT-self.plant['oil_bulk_modulus_pa']*math.log1p(-min(f,.999999))
 def energy(self):
  if not self.plant or self.position!='high':return 0.
  d=max(self.p-AMBIENT,0);z=d/self.plant['oil_bulk_modulus_pa'];return self.plant['hp_dead_volume_m3']*self.plant['oil_bulk_modulus_pa']*(1-(1+z)*math.exp(-z))
 def equilibrium(self,p,branch=None):
  if not self.sample or not self.filled:return self.v
  branch=branch or self.branch;xs,ys=CURVES[(self.sample['reference_id'],branch)]
  if not xs:return self.v
  q=interp([0.]+xs,[0.]+ys,p)*self.sample['true_dry_mass_kg']
  return max(self.v,q) if branch=='intrusion' else min(self.v,q)
 def read(self,visibility='agent'):
  d={'plant.configured':bool(self.plant),'power.enabled':self.powered,'power.angle_rad':.32 if self.powered else 0.,'lamp.power':self.powered,'guard0.angle_rad':self.guard[0],'guard1.angle_rad':self.guard[1],'cap.lift_m':self.cap,'cell.x_m':self.xyz[0],'cell.y_m':self.xyz[1],'cell.z_m':self.xyz[2],'cell.position':self.position,'cell.filled':self.filled,'cell.loaded':bool(self.sample),'acquisition.phase':self.phase,'acquisition.stage':self.stage,'acquisition.elapsed_s':self.elapsed,'acquisition.failure':self.failure,'pressure.target_pa':self.target,'pressure.measured_pa':self.indicated(),'volume.measured_m3':self.measured(),'capacitance.measured_f':self.capacitance(),'sample.identity':self.sample['sample_id'] if self.sample else '', 'sample.mass_kg':self.sample['true_dry_mass_kg'] if self.sample else 0.,'sample.intruded_m3':self.v,'sample.pressure_pa':self.p,'results.point_count':len(self.all_points),'mercury.stock_kg':self.hg_stock}
  return copy.deepcopy({k:v for k,v in d.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _result(self):
  points=copy.deepcopy(self.all_points);return dict(template_source=self.result_source,evidence=(LIB['substances'][self.sample['reference_id']]['evidence'] if self.sample else 'measured_literature')+'_reference_with_explicit_engineering_transfer',phase=self.phase,stage=self.stage,elapsed_s=self.elapsed,points=points,distribution=distribution(points),max_pore_volume_m3_kg=max((x['corrected_pore_volume_m3_kg'] for x in points),default=None),warnings=['apparent_connected_throat_Washburn; heterogeneous samples and compressibility may bias PSD',COUNT_NOTE,'extrusion only where the source measured a descent branch; source-peak cycle required; other references never fabricate descent']+([f"reference pressure axis {meta(self.sample['reference_id'])['pressure_axis']}: Washburn inversion of source diameters with assumed gamma/theta, absolute pressure scale uncertain"] if self.sample and meta(self.sample['reference_id'])['pressure_axis']=='washburn_assumed_params' else [])+([f"reference volume basis {meta(self.sample['reference_id'])['volume_basis']}: derived from source data (see library processing)"] if self.sample and meta(self.sample['reference_id'])['volume_basis'] not in ('measured_cumulative',) else [])+['monotone projection and duplicate-pressure means for runtime only; rawdata immutable; no measured-target extrapolation','one movable sample, two selectable LPports and one HPport; no dual-LP simultaneous process','finite gas/Hg/oil; constantT and lumped ideal liquid compliance; whole heat/mercuryvapour/robot/seal/OEMnoise not_run'])
 def _ledger(self):
  m=self.sample['true_dry_mass_kg'] if self.sample else 0.;intr=self.v*self.plant['hg_density_kg_m3'] if self.plant else 0.
  return dict(hg_added_kg=self.hg_added,hg_stock_kg=self.hg_stock,hg_free_in_cell_kg=max(0.,self.hg_cell-intr),hg_intruded_kg=intr,hg_waste_kg=self.hg_waste,hg_returned_with_sample_kg=self.hg_returned,hg_residual_kg=self.hg_added-self.hg_stock-self.hg_cell-self.hg_waste-self.hg_returned,solid_loaded_kg=self.loaded,solid_present_kg=m,solid_returned_kg=self.returned,solid_residual_kg=self.loaded-m-self.returned,gas_source_mol=self.source,gas_chamber_mol=self.gas,gas_exhaust_mol=self.exhaust,gas_residual_mol=self.gas_initial-self.source-self.gas-self.exhaust,oil_reservoir_m3=self.oil,oil_pressurized_inventory_m3=self.oil_x,oil_residual_m3=(self.plant['oil_volume_m3'] if self.plant else 0)-self.oil-self.oil_x,hydraulic_stored_energy_j=self.energy(),hydraulic_pump_work_j=self.pump_work,hydraulic_release_work_j=self.release_work,capillary_work_j=self.capillary_work,work_balance_error_j=self.work_error)
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[k][0],a);before=copy.deepcopy(self.__dict__)
  try:
   r=self._invoke(k,a);validate(CAPS[k][1],r)
   if CAPS[k][4] not in ['query','analysis']:self.revision+=1
   return r
  except Exception:self.__dict__.clear();self.__dict__.update(before);raise
 def _invoke(self,k,a):
  if k=='process.measure':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='template.reference':
   d=LIB['substances'].get(a['substance_id'])
   if not d:raise ActionRejected('UNKNOWN_REFERENCE')
   return dict(name=d['name'],evidence=d['evidence'],source=d['source']['url'],intrusion_points=sum(p['branch']=='intrusion' for p in d['value']),extrusion_points=sum(p['branch']=='extrusion' for p in d['value']),**meta(a['substance_id']))
  if k=='plant.configure':
   if self.plant or self.powered or not a['accept_engineering_model']:raise ActionRejected('INITIALIZATION_ONLY')
   self.plant=copy.deepcopy(a);self.gas=AMBIENT*a['chamber_volume_m3']/(R*a['temperature_k']);self.source=a['source_pressure_pa']*a['source_volume_m3']/(R*a['temperature_k']);self.gas_initial=self.gas+self.source;self.oil=a['oil_volume_m3'];return {'configured':True}
  if k=='power.set':
   self.powered=a['enabled']
   if not self.powered and self.phase=='running':self._end('cancelled')
   return a
  if not self.plant:raise ActionRejected('NOT_CONFIGURED')
  if k in ['acquisition.stop','emergency.stop']:
   if self.phase!='running' and k=='acquisition.stop':raise ActionRejected('NOT_RUNNING')
   if self.phase=='running':self._end('cancelled')
   self.target=AMBIENT;return {'phase':self.phase}
  if self.phase=='running':raise ActionRejected('BUSY')
  if k.startswith('guard'):
   i=int(k[5]);v=a['angle_rad']
   if abs(self.p-AMBIENT)>30:raise ActionRejected('PRESSURE_ACCESS_INTERLOCK')
   self.guard[i]=v;return a
  if k=='cap.set':
   if abs(self.p-AMBIENT)>30:raise ActionRejected('PRESSURE_ACCESS_INTERLOCK')
   self.cap=a['lift_m'];return a
  if k=='cell.configure':
   if self.sample or self.hg_cell:raise ActionRejected('LOAD_PRECONDITION')
   self.bulb=float(a['bulb_volume_m3']);self.capacity=float(a['stem_capacity_m3']);return {'configured':True}
  if k=='mercury.fill':self.hg_stock+=a['mass_kg'];self.hg_added+=a['mass_kg'];return {'stock_kg':self.hg_stock}
  if k=='sample.load':
   if self.sample or self.hg_cell or self.position=='high' or self.guard[int(self.position[-1])]<1.2 or abs(self.p-AMBIENT)>30:raise ActionRejected('LOAD_PRECONDITION')
   if not a['dry'] or not a['accept_cross_instrument_transfer']:raise ActionRejected('TRANSFER_NOT_ACCEPTED')
   if a['reference_id'] not in LIB['substances']:raise ActionRejected('UNKNOWN_REFERENCE')
   if a['true_bulk_volume_m3']>=self.bulb or max(CURVES[(a['reference_id'],'intrusion')][1])*a['true_dry_mass_kg']>min(self.capacity,a['true_bulk_volume_m3']):raise ActionRejected('CAPACITY')
   self.sample=copy.deepcopy(a);self.result_source=LIB['substances'][a['reference_id']]['source']['url'];self.v=0.;self.peak=0.;self.loaded+=a['true_dry_mass_kg'];self.all_points=[];return {'loaded':True}
  if k=='sample.unload':
   if not self.sample or self.position=='high' or self.guard[int(self.position[-1])]<1.2 or self.filled or abs(self.p-AMBIENT)>30:raise ActionRejected('LOAD_PRECONDITION')
   m=self.sample['true_dry_mass_kg'];hg=self.hg_cell;self.returned+=m;self.hg_returned+=hg;self.hg_cell=0.;self.v=0.;self.sample=None;self.method=None;return dict(returned_dry_mass_kg=m,retained_hg_kg=hg)
  if k=='cell.drain':
   if self.position=='high' or abs(self.p-AMBIENT)>30 or self.guard[int(self.position[-1])]<1.2 or not self.filled:raise ActionRejected('LOAD_PRECONDITION')
   keep=self.v*self.plant['hg_density_kg_m3'];self.hg_waste+=max(0.,self.hg_cell-keep);self.hg_cell=keep;self.filled=False;return {'waste_hg_kg':self.hg_waste}
  if k=='cell.transfer':
   dest=a['destination']
   if abs(self.p-AMBIENT)>30 or not self.sample or dest==self.position:raise ActionRejected('TRANSFER_INTERLOCK')
   for pos in [self.position,dest]:
    if pos.startswith('low') and self.guard[int(pos[-1])]<1.2:raise ActionRejected('TRANSFER_INTERLOCK')
   if 'high' in [self.position,dest] and self.cap<.18:raise ActionRejected('TRANSFER_INTERLOCK')
   if dest=='high':
    if not self.filled or not self.all_points:raise ActionRejected('LOW_STAGE_REQUIRED')
    if self.oil<self.v:raise ActionRejected('OIL_EXHAUSTED')
    self.oil-=self.v;self.oil_x=self.v
   elif self.position=='high':self.oil+=self.oil_x;self.oil_x=0.
   self.position=dest;self.xyz={'low0':[0.,0.,0.],'low1':[.328,0.,0.],'high':[.164,.185,.265]}[dest];self.method=None
   if dest!='high':
    self.p=self.gas*R*self.plant['temperature_k']/(self.plant['chamber_volume_m3']+self.v)
    if abs(self.p-AMBIENT)>1:self.stage='vent';self.target=AMBIENT
   return {'position':dest}
  if k=='pressure.calibrate':
   x=a['reference_pa'];y=a['measured_pa']
   if x[1]<=x[0] or y[1]<=y[0]:raise ActionRejected('INVALID_CALIBRATION')
   gain=(y[1]-y[0])/(x[1]-x[0]);self.pcal[a['range_index']]=(gain,y[0]-gain*x[0]);return {'calibrated':True}
  if k=='volume.calibrate':
   x=a['reference_volume_m3'];y=a['measured_capacitance_f']
   if x[1]<=x[0] or y[1]<=y[0]:raise ActionRejected('INVALID_CALIBRATION')
   factor=(x[1]-x[0])/(y[1]-y[0]);zero=y[0]-x[0]/factor;self.vcal=(factor,zero);return dict(factor_m3_f=factor,zero_f=zero)
  if k=='blank.calibrate':
   xs=a['pressures_pa'];ys=a['volumes_m3']
   if len(xs)<2 or len(xs)!=len(ys) or any(b<=a for a,b in zip(xs,xs[1:])):raise ActionRejected('INVALID_CALIBRATION')
   self.blank=(xs.copy(),ys.copy());return {'points':len(xs)}
  if k=='method.configure':
   xs=a['pressures_pa'];high=a['stage']=='high';lo,hi=(20*PSI,P_MAX) if high else (.2*PSI,50*PSI)
   if not xs or len(xs)>2000 or any(not lo<=p<=hi for p in xs) or any(x==y for x,y in zip(xs,xs[1:])):raise ActionRejected('INVALID_PRESSURE_SEQUENCE')
   peak=xs.index(max(xs))
   if any(y<x for x,y in zip(xs[:peak+1],xs[1:peak+1])) or any(y>x for x,y in zip(xs[peak:],xs[peak+1:])):raise ActionRejected('INVALID_PRESSURE_SEQUENCE')
   if not self.sample:raise ActionRejected('NOT_READY')
   for i,p in enumerate(xs):
    branch='intrusion' if i<=peak else 'extrusion';xp,_=CURVES[(self.sample['reference_id'],branch)]
    if not xp:raise ActionRejected('EXTRUSION_UNAVAILABLE')
    if p<xp[0]*(1-1e-5) or p>xp[-1]*(1+1e-5):raise ActionRejected('TEMPLATE_RANGE')
   if peak<len(xs)-1:
    xp,_=CURVES[(self.sample['reference_id'],'extrusion')]
    if abs(max(xs)-max(xp))/max(xp)>.0001:raise ActionRejected('SOURCE_PEAK_REQUIRED')
   if a['pressure_tolerance_pa']<( .916*PSI if high and max(xs)>1500*PSI else .0229*PSI if high else .000763*PSI)/2:raise ActionRejected('BELOW_RESOLUTION')
   self.method=copy.deepcopy(a);return {'configured':True}
  if k=='acquisition.start':
   if self.stage=='vent' or abs(self.p-AMBIENT)>30 or not self.powered or not self.sample or not self.method or not self.vcal or not self.blank:raise ActionRejected('NOT_READY')
   high=self.method['stage']=='high'
   if high!=(self.position=='high') or high and self.cap>.0001 or not high and self.guard[int(self.position[-1])]>.0001:raise ActionRejected('POSITION_INTERLOCK')
   if (high and any(x is None for x in self.pcal[1:])) or (not high and self.pcal[0] is None):raise ActionRejected('CALIBRATION_REQUIRED')
   if not high and self.filled:raise ActionRejected('FRESH_LOW_STAGE_REQUIRED')
   if high and not self.filled:raise ActionRejected('LOW_STAGE_REQUIRED')
   xs=self.method['pressures_pa']
   if self.blank[0][0]>min(xs) or self.blank[0][-1]<max(xs):raise ActionRejected('BLANK_RANGE')
   if self.operation_id and self.operation_id not in self.records:self.records[self.operation_id]=self._result()
   self.serial+=1;self.operation_id=self.instance_id+':micp:'+str(self.serial);self.phase='running';self.stage='pressure' if high else 'vacuum';self.elapsed=0.;self.stable=0.;self.index=0;self.point_time=0.;self.points=[];self.failure='';self.target=xs[0] if high else 3.333;self.branch='intrusion';return {'operation_id':self.operation_id,'phase':self.phase}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _end(self,phase,reason=''):
  self.phase=phase;self.failure=reason;self.stage='vent';self.target=AMBIENT
  if self.operation_id:self.records[self.operation_id]=self._result()
 def step(self,dt):
  if isinstance(dt,bool) or not isinstance(dt,(int,float)) or not math.isfinite(dt) or dt<0 or dt>86400:raise ActionRejected('INVALID_ARGUMENT')
  self.pending+=dt
  while self.pending+1e-10>=DT:
   self.pending-=DT;self.time_s+=DT;self._tick();self.revision+=1
  if abs(self.pending)<1e-10:self.pending=0.
 def _tick(self):
  if not self.plant:return
  running=self.phase=='running';oldp=self.p;oldv=self.v;P=self.plant
  if running:self.elapsed+=DT;self.point_time+=DT
  if self.stage=='vacuum' and running:
   dn=self.gas*(-math.expm1(-P['pump_rate_s']*DT));self.gas-=dn;self.exhaust+=dn;self.p=self.gas*R*P['temperature_k']/P['chamber_volume_m3']
   if self.p<=3.333:self.stage='fill';self.target=self.p
  elif self.stage=='fill' and running:
   desired=(self.bulb-self.sample['true_bulk_volume_m3']+self.capacity)*P['hg_density_kg_m3'];dm=min(.01*DT,max(0.,desired-self.hg_cell),self.hg_stock);self.hg_cell+=dm;self.hg_stock-=dm
   if desired-self.hg_cell<1e-12:self.filled=True;self.stage='pressure';self.target=self.method['pressures_pa'][0];self.point_time=0.
   elif self.hg_stock<1e-12:self._end('failed','MERCURY_EXHAUSTED')
  elif self.stage in ['pressure','vent']:
   vent=self.stage=='vent';target=AMBIENT if vent else self.method['pressures_pa'][self.index];self.target=target;rate=(2e8 if self.position=='high' else 5e5) if vent else self.method['pressure_rate_pa_s'];ramped=self.p+max(-rate*DT,min(rate*DT,target-self.p));a=-math.expm1(-P['intrusion_rate_s']*DT)
   # After scan ends, retained Hg is held; no unsupported extrusion is invented during vent.
   branch=self.branch;vnew=self.v if vent else self.v+a*(self.equilibrium(ramped,branch)-self.v)
   if vnew>self.capacity+1e-14 or vnew*P['hg_density_kg_m3']>self.hg_cell+1e-12:self._end('failed','MERCURY_OR_STEM_CAPACITY');return
   if self.position=='high':
    desired=self.storage(ramped)+vnew;dx=max(-P['oil_flow_m3_s']*DT,min(P['oil_flow_m3_s']*DT,desired-self.oil_x));dx=min(dx,self.oil);x=self.oil_x+dx
    vnew=min(vnew,x);self.oil-=dx;self.oil_x=x;self.v=max(0.,vnew);self.p=self.pressure_hp(x,self.v);pavg=(max(oldp-AMBIENT,0)+max(self.p-AMBIENT,0))/2;dw=pavg*dx;dc=pavg*(self.v-oldv)
    if dw>=0:self.pump_work+=dw
    else:self.release_work-=dw
    self.capillary_work+=dc;self.work_error=self.pump_work-self.release_work-self.capillary_work-self.energy()
    if not vent and self.oil<1e-14 and target>self.p+100:self._end('failed','OIL_EXHAUSTED')
   else:
    self.v=max(0.,vnew);V=P['chamber_volume_m3']+self.v;desired=ramped*V/(R*P['temperature_k']);dn=(desired-self.gas)*(-math.expm1(-P['gas_rate_s']*DT))
    if dn>=0:
     ps=self.source*R*P['temperature_k']/P['source_volume_m3'];equal=max(0.,(ps-self.p)/(R*P['temperature_k']*(1/P['source_volume_m3']+1/V)));dn=min(dn,self.source,equal);self.source-=dn
    else:self.exhaust-=dn
    self.gas+=dn;self.p=self.gas*R*P['temperature_k']/V
    if not vent and self.source*R*P['temperature_k']/P['source_volume_m3']<target and abs(self.p-target)>100:self._end('failed','GAS_EXHAUSTED')
   self.peak=max(self.peak,self.p)
   if self.p>P_MAX*1.001:self._end('failed','OVERPRESSURE')
   if vent:
    if abs(self.p-AMBIENT)<1.:
     if self.phase=='running':self.phase='succeeded'
     self.stage='idle';self.target=AMBIENT
     if self.phase=='succeeded' and self.operation_id:self.records[self.operation_id]=self._result()
   else:
    okay=abs(self.indicated()-target)<=self.method['pressure_tolerance_pa'] and abs(self.v-oldv)/DT<=self.method['max_volume_drift_m3_s'];self.stable=self.stable+DT if okay else 0.
    if self.stable+1e-9>=self.method['equilibration_s']:
     p=self.indicated();v=self.measured();q=v/self.method['entered_mass_kg'];angle=self.method['advancing_angle_rad' if self.branch=='intrusion' else 'receding_angle_rad'];d=-4*self.method['surface_tension_n_m']*math.cos(angle)/p if p>0 else None;point=dict(time_s=self.time_s,stage=self.method['stage'],branch=self.branch,pressure_pa=p,intrusion_volume_m3=v,specific_volume_m3_kg=q,corrected_pore_volume_m3_kg=max(0.,q-self.method['closure_m3_kg']),diameter_m=d);self.points.append(point);self.all_points.append(point);self.index+=1;self.stable=0.;self.point_time=0.
     if self.index==len(self.method['pressures_pa']):self.stage='vent';self.target=AMBIENT
     elif self.method['pressures_pa'][self.index]<self.method['pressures_pa'][self.index-1]:self.branch='extrusion'
  if self.phase=='running' and self.point_time>self.method['point_timeout_s']:self._end('failed','POINT_TIMEOUT')

class AntonpaarPoremaster60Session:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,i):
  if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  m=export_antonpaar_poremaster60_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
 def read(self,*,instance_id,state_ids=None):
  self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
  if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
  if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
  return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='unavailable' if v[k] is None else 'estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=h.time_s,sample_window_s=None) for k in ids})

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
  except ActionRejected as exc:return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,status='unsupported' if str(exc)=='UNSUPPORTED' else 'rejected',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))
 def write(self,*,instance_id,state_id,value,request_id):
  self._target(instance_id)
  if state_id not in SLOTS or not SLOTS[state_id][5]:raise ActionRejected('ACCESS_DENIED')
  cap,arg=SLOTS[state_id][5];return self.invoke(instance_id=instance_id,capability_id=cap,arguments={arg:value},request_id=request_id)
 def poll(self,operation_id):
  h=self._host
  if h._owners.get(operation_id)!=self._principal:raise ActionRejected('ACCESS_DENIED')
  record=h.records.get(operation_id)
  if operation_id==h.operation_id and h.phase=='running':record=h._result()
  if record is None:raise ActionRejected('UNKNOWN_OPERATION')
  return dict(operation_id=operation_id,status=record['phase'],result=copy.deepcopy(record))
 def cancel(self,operation_id,request_id):
  self.poll(operation_id)
  if operation_id!=self._host.operation_id:raise ActionRejected('NOT_RUNNING')
  return self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)





def export_antonpaar_poremaster60_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions='SI; absolute pressure axis per original PoreMaster brochure; constant externally supplied temperature; actual sample identity private',origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'AntonpaarPoremaster60Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description='fixed0.1s integration; pure reads never advance'),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNKNOWN_OPERATION','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','INITIALIZATION_ONLY','BUSY','NOT_READY','NOT_RUNNING','NOT_CONFIGURED','UNKNOWN_REFERENCE','PRESSURE_ACCESS_INTERLOCK','LOAD_PRECONDITION','TRANSFER_INTERLOCK','TRANSFER_NOT_ACCEPTED','CAPACITY','OIL_EXHAUSTED','GAS_EXHAUSTED','MERCURY_EXHAUSTED','MERCURY_OR_STEM_CAPACITY','POSITION_INTERLOCK','LOW_STAGE_REQUIRED','INVALID_PRESSURE_SEQUENCE','INVALID_CALIBRATION','EXTRUSION_UNAVAILABLE','SOURCE_PEAK_REQUIRED','TEMPLATE_RANGE','BELOW_RESOLUTION','CALIBRATION_REQUIRED','FRESH_LOW_STAGE_REQUIRED','BLANK_RANGE','OVERPRESSURE','POINT_TIMEOUT']
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  un=k in UNSUPPORTED;asyn=k=='acquisition.start';caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if un else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if un else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if un else 'implemented'),binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.antonpaar_poremaster60.AntonpaarPoremaster60Session.invoke',invocation=f'session.invoke(instance_id="poremaster60_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if kind in ['query','analysis'] or un else [dict(description_zh='有限干样/汞/气源/液压油；宿主接受响应迁移；独立标定；受检压力和机械访问',state_ids=['power.enabled','plant.configured','acquisition.phase','cell.position','cap.lift_m','guard0.angle_rad','guard1.angle_rad'],enforced_by='runtime',check='atomic invoke and host-authenticated Session; advance only step')],state_effects=[dict(state_id=x,timing='immediate',description_zh='经互锁检查更新状态') for x in IMMEDIATE_EFFECTS.get(k,effects)],observes=[],model_effects=dict(mode='none',description_zh='场景需独立验证',binding_ids=[]),execution=dict(mode='asynchronous' if asyn else 'synchronous',time_domain='simulation',completion_criterion='压力点完成且卸压接近环境绝压' if asyn else '命令接受，过程由step推进',completion_state_ids=['acquisition.phase'] if asyn else [],timeout_s=None,cancellation='supported' if asyn else 'unsupported',retry='same_request_id',resources=['host_clock','one_finite_sample','mercury','gas','hydraulic_oil','request_cache'],partial_failure_zh='已消耗的汞/气源/液压状态、已采点保留，取消不瞬间归零压力；之后step有限卸压'),errors=[dict(code=x,meaning_zh=x,side_effects='none') for x in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=['非厂家软件/实机协议；误差参数是独立宿主假设；分辨率不是噪声精度实证'],source_ids=['manufacturer_specs','simulation_assumption'])
  if asyn:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='有限汞/气源/液压油及压力平衡更新') for x in STEP_EFFECTS]
 mech=dict(component_ids=['controller','oil_pump','hg_reservoir','lp_manifold_0','lp_manifold_1','hp_vessel','cell_z','cell_stem','cell_contact','coldtrap'],state_ids=list(slots),meaning_zh='分阶段有限汞压入、真实分支曲线迁移、独立压力/电容/空白标定和连通孔喉反演',implementation='lumped',equations=['Washburn D=-4*gamma*cos(theta)/P; gamma and advancing/receding angles are independently entered method conditions','Raw experimental q(P) retained in library; runtime duplicate-P means + isotonic projection + explicitly assumed zero-pressure bridge; measured targets reject out-of-support extrapolation','v_new=v+(1-exp(-k*dt))*(m_true*q_equilibrium(P)-v); intrusion monotone, extrusion only two measured hemp references at recorded source-peak cycle','LP P=nRT/(Vchamber+Vintruded); finite N2 source gradient/equalization cap; vacuum and vent book exhaust; constantT explicit','HP oil_compression=V0*(1-exp(-(P-Patm)/K)); oil_inventory=oil_compression+Vintruded; finite oil flow and reservoir; P=Patm-K*ln(1-(X-Vintruded)/V0)','Hg(stock)+Hg(cell free)+Hg(pore)+Hg(waste)+Hg(sample returned)=Hg externally added; solid loaded=present+returned','Capacitance=(Vintruded+Hgcompression+glassCompliance*P)/true_m3_per_F+offset_F; volume_calibrated=(C-zero_cal)*factor_cal-blank(Pmeasured); quantized3e-11m3(0.5cc) or9e-11m3(2cc)','Pressure indicated=quantize((gain*P+offset-cal_offset)/cal_gain,range_resolution); three calibration ranges separate','Reported specific volume=calibrated_volume/entered_mass; pore volume=max(0,specific_volume-entered_closure); never copy true mass into reported normalization','LP/HP overlap points within combined pressure quantization half-width are merged, keeping higher pressure resolution; dV/dlog10D only over distinguishable pressure intervals at geometric-mean diameter; source missing extrusion remains unavailable','Hydraulic stored gauge work=V0*K*(1-(1+z)*exp(-z)),z=(P-Patm)/K; trapezoidal pump/return/capillary work reports residual; excludes absolute ambient work, lowpressuregas thermal energy, heat and vapour'],parameters={},source_ids=['manufacturer_specs','manufacturer_brochure','dggs_micp','limestone_data','hemp_data','simulation_assumption'],limitations=[f"{len(LIB['substances'])} reference specimens in {len(MATERIAL_GROUPS)} distinct material groups (rocks, clays, cements/mortars/screeds, ceramics, membranes, polymer/carbon foams, glass, soil, plant fibre); source instruments AutoPore/WIN9400/Pascal/PoreSizer/unspecified, not PoreMaster60 reproducibility data; {sum(1 for d in LIB['substances'].values() if d.get('pressure_axis')=='washburn_assumed_params')} references have an assumption-flagged Washburn pressure axis.",f"{sum(1 for k in LIB['substances'] if not CURVES[(k,'extrusion')][0])} references lack extrusion; only the {sum(1 for k in LIB['substances'] if CURVES[(k,'extrusion')][0])} with a measured descent branch can run a cycle. Small loops, pore-network hysteresis and irreversible sample compression not validated.",'One movable physical cell, two selectable LPports and oneHP; parallel LP throughput unsupported. Semantic manual relocation verifies position, not robot trajectory/contact/sealing.','3.2/6.6/18cc bulb and0.5/2cc stem semantic choices; displayed glass is3.2cc proxy; exact bore CAD/part masses/inertia unknown.','Standard180kg historical manufacturer body; GT lift absent; current revision CAD unknown;1000VA not heat power.','Calibrations use externally supplied records; example blanks are explicitly engineering records, not empirical OEM blank validation.','Oil compliance and transfer rates engineering; constanttemperature, mercury vapour/coldtrap chemistry, gas/pump/sample/whole-machine enthalpy, OEMnoise/fault probability not_run.','Fixture pressure integrity, physical robot/grasp/contact, real hardware and filesystem truth isolation not_run.'])
 spec=importlib.util.spec_from_file_location('_poremaster_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'mercury_porosimetry':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'。'
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'mercury_porosimetry':mech})

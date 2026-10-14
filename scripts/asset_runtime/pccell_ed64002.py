"""BED1-2 Basic, ten pairs; dilute binary electrolyte with explicit host boundaries."""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.antonpaar_abbematadvanced import validate as _validate
ROOT=Path(__file__).resolve().parents[2];SLUG='pccell_ed64002';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-035';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/pccell_ed64002_ion_library/templates.json'
AREA=.0064;GAP=.0005;PAIRS=10;F=96485.33212331001;HOLDUP=AREA*GAP*PAIRS
def validate(s,v):
 if s.get('type')=='integer':
  if type(v) is not int or not s.get('minimum',-math.inf)<=v<=s.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
 if s.get('type')=='object' and isinstance(v,dict):
  for k,x in v.items():
   if k in s.get('properties',{}):validate(s['properties'][k],x)
 if s.get('type')=='array' and isinstance(v,list):
  for x in v:validate(s['items'],x)
 _validate(s,v)
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
PLANT=obj({'reference_conditions_accepted':BOOL,'external_electrode_loop_confirmed':BOOL,'gas_heat_boundary_confirmed':BOOL,'temperature_k':dict(type='number',const=298.15,**{'x-unit':'K'}),'max_voltage_v':number('V',.01,100),'max_current_a':number('A',1e-6,10),'max_flow_m3_s':number('m^3/s',1e-9,1e-4),'max_concentration_mol_m3':number('mol/m^3',.001,1),'mass_transfer_m_s':number('m/s',1e-7,.001),'polarization_fraction':number('1',.05,.95),'series_resistance_ohm':number('ohm',.01,1e4),'coefficient_evidence':TEXT})
EXAMPLE=dict(reference_conditions_accepted=True,external_electrode_loop_confirmed=True,gas_heat_boundary_confirmed=True,temperature_k=298.15,max_voltage_v=24.,max_current_a=.2,max_flow_m3_s=25e-6,max_concentration_mol_m3=1.,mass_transfer_m_s=2e-5,polarization_fraction=.8,series_resistance_ohm=25.,coefficient_evidence='host_assumption_not_manufacturer_limits_or_calibration')
MEMBRANE=obj({'pairs':dict(type='integer',minimum=10,maximum=10,**{'x-unit':'1'}),'compatibility_reviewed':BOOL,'cation_selectivity':number('1',0,1),'anion_selectivity':number('1',0,1),'salt_permeability_m_s':number('m/s',0,1e-5),'water_drag_mol_per_equivalent':number('1',0,100),'coefficient_evidence':TEXT})
MEMBRANE_EXAMPLE=dict(pairs=10,compatibility_reviewed=True,cation_selectivity=.95,anion_selectivity=.93,salt_permeability_m_s=1e-9,water_drag_mol_per_equivalent=5.,coefficient_evidence='assumed_effective_selectivity_backdiffusion_drag_not_specific_membrane')
METHOD=obj({'mode':dict(type='string',enum=['constant_current','constant_voltage']),'voltage_v':number('V',0,100),'current_a':number('A',0,10),'flow_m3_s':number('m^3/s',1e-9,1e-4),'duration_s':number('s',.01,86400),'timeout_s':number('s',.01,86400),'endpoint_conductivity_s_m':number('S/m',0,1)})
METHOD_EXAMPLE=dict(mode='constant_current',voltage_v=20.,current_a=.01,flow_m3_s=10e-6,duration_s=120.,timeout_s=150.,endpoint_conductivity_s_m=0.)
SAMPLE=obj({'sample_id':TEXT,'substance_id':TEXT,'diluate_volume_m3':number('m^3',HOLDUP+1e-6,.002),'concentrate_volume_m3':number('m^3',HOLDUP+1e-6,.002),'diluate_amount_mol':number('mol',0,.002),'concentrate_amount_mol':number('mol',0,.002),'single_stable_dilute_electrolyte_confirmed':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='nacl_25C',substance_id='nacl',diluate_volume_m3=.001,concentrate_volume_m3=.001,diluate_amount_mol=.0003,concentrate_amount_mol=.0001,single_stable_dilute_electrolyte_confirmed=True)
RESULT=obj({'phase':PHASE,'fault':TEXT,'voltage_v':number('V',0),'current_a':number('A',0),'limiting_current_a':number('A',0),'diluate_conductivity_s_m':nullable(number('S/m',0)),'concentrate_conductivity_s_m':nullable(number('S/m',0)),'energy_j':number('J',0),'evidence':TEXT,'template_source':TEXT})
POINT=obj({'time_s':number('s',0),'voltage_v':number('V',0),'current_a':number('A',0),'diluate_conductivity_s_m':number('S/m',0),'concentrate_conductivity_s_m':number('S/m',0)})
REF=obj({'substance_id':TEXT,'formula':TEXT,'limiting_molar_conductivity_s_m2_mol':number('S*m^2/mol',0),'temperature_k':number('K',298.15,298.15),'evidence':TEXT,'source':TEXT,'template_source':TEXT})
PRODUCT=obj({'substance_id':nullable(TEXT),'diluate_amount_mol':number('mol',0),'concentrate_amount_mol':number('mol',0),'diluate_volume_m3':number('m^3',0),'concentrate_volume_m3':number('m^3',0),'evidence':TEXT,'template_source':TEXT})
# input,output,components,meaning,kind,visibility,example,effects
CAPS={
 'plant.configure':(PLANT,obj({'configured':BOOL}),['power_supply','pump_block','port_rinse_in'],'宿主指定额定值未知时的教学电源/流动/电极/恒温边界','configuration','evaluator',EXAMPLE,['plant.configured']),
 'membrane.configure':(MEMBRANE,obj({'configured':BOOL}),['stack_core'],'10膜对标准；选择性/反扩散/水拖曳显式假设','configuration','evaluator',MEMBRANE_EXAMPLE,['membrane.configured']),
 'method.configure':(METHOD,obj({'configured':BOOL}),['power_supply','pump_block'],'恒流/恒压、循环流量与宿主终点；不是Basic在线软件','configuration','agent',METHOD_EXAMPLE,['method.configured','electrical.voltage_target_v','electrical.current_target_a','flow.target_m3_s','panel.voltage_angle_rad','panel.current_angle_rad','panel.flow_angle_rad']),
 'sample.configure':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['tank_diluate','tank_concentrate'],'初始化完整双循环有限库存；宿主装料，不是机器人已执行','configuration','evaluator',SAMPLE_EXAMPLE,['sample.configured']),
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_supply'],'断电停止电渗析，已分离盐和水不回滚','device_command','agent',{'enabled':True},['power.enabled','cycle.phase','electrical.current_a','electrical.voltage_v','flow.actual_m3_s']),
 'electrical.set_voltage':(obj({'voltage_v':number('V',0,100)}),obj({'voltage_v':number('V',0,100)}),['knob_voltage','power_supply'],'电压目标/恒流合规电压，只写设定','device_command','agent',{'voltage_v':12.},['electrical.voltage_target_v','panel.voltage_angle_rad']),
 'electrical.set_current':(obj({'current_a':number('A',0,10)}),obj({'current_a':number('A',0,10)}),['knob_current','power_supply'],'电流目标/恒压合规电流，只写设定','device_command','agent',{'current_a':.005},['electrical.current_target_a','panel.current_angle_rad']),
 'flow.set_rate':(obj({'flow_m3_s':number('m^3/s',1e-9,1e-4)}),obj({'flow_m3_s':number('m^3/s',1e-9,1e-4)}),['knob_flow','pump_block'],'两回路同流率目标；单区充分混合代理','device_command','agent',{'flow_m3_s':5e-6},['flow.target_m3_s','panel.flow_angle_rad']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['stack_core','pump_block','power_supply'],'启动宿主协调ED，仅step推进盐转移','device_command','agent',{},['cycle.phase']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['stack_core','pump_block'],'停止并保留有限库存和积分电能','device_command','agent',{},['cycle.phase','electrical.current_a','electrical.voltage_v','flow.actual_m3_s']),
 'electrodialysis.measure':(obj({}),RESULT,['display_digits','stack_core'],'U/I教学示值与外部理想电导；Basic无内置电导传感器','query','agent',{},[]),
 'product.read':(obj({}),PRODUCT,['tank_diluate','tank_concentrate'],'分离后的盐量和体积真值，仅受信任评估者','query','evaluator',{},[]),
 'history.read':(obj({}),obj({'records':dict(type='array',items=POINT),'evidence':TEXT}),['display_digits'],'宿主记录的电流/电压/理想电导曲线','query','agent',{},[]),
 'reference.lookup':(obj({'substance_id':TEXT}),REF,['stack_core'],'真实离子D25换算的52种极限摩尔电导，不是ED实测曲线','query','agent',{'substance_id':'nacl'},[])}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,zh,o='derived',v='agent',c='display_digits',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=zh
for k,zh in [('plant','宿主外部边界'),('membrane','标准10膜对'),('method','方法'),('sample','双循环液体')]:slot(k+'.configured',BOOL,'1',zh+'配置状态')
slot('power.enabled',BOOL,'1','虚拟电源开关','setpoint',c='power_supply',w=('power.set','enabled'))
slot('cycle.phase',PHASE,'1','宿主协调作业状态');slot('cycle.fault',TEXT,'1','最近终止原因');slot('cycle.elapsed_s',number('s',0),'s','本次作业累计时间')
for k,u,zh,o,c,w in [('electrical.voltage_target_v','V','电压目标或合规电压','setpoint','knob_voltage',('electrical.set_voltage','voltage_v')),('electrical.current_target_a','A','电流目标或合规电流','setpoint','knob_current',('electrical.set_current','current_a')),('flow.target_m3_s','m^3/s','双回路目标液体体积流率','setpoint','knob_flow',('flow.set_rate','flow_m3_s')),('flow.actual_m3_s','m^3/s','充分混合模型运行流率','derived','pump_block',None),('electrical.voltage_v','V','模型电压代理，非实测ADC','derived','display_digits',None),('electrical.current_a','A','模型电流代理，非实测ADC','derived','display_digits',None),('electrical.limiting_current_a','A','宿主传质系数推算极化限流','derived','stack_core',None),('electrical.charge_c','C','电极电路积分电荷，盐转移另乘10膜对','derived','power_supply',None),('electrical.energy_j','J','积分电能，热量排出为外部边界','derived','power_supply',None)]:slot(k,number(u,0),u,zh,o,c=c,w=w)
for name in ['voltage','current','flow']:slot('panel.'+name+'_angle_rad',number('rad',-2.6,2.6),'rad','教学旋钮角度；宿主限定量程归一化，非厂家标定','derived',c='knob_'+name)
for i,name in enumerate(['diluate','concentrate']):
 slot('conductivity.'+name+'_s_m',nullable(number('S/m',0)),'S/m','25°C极限电导外部理想观测；Basic无此传感器',c='stack_core')
 slot('truth.'+name+'_amount_mol',number('mol',0),'mol','完整'+name+'循环的盐化学式单元摩尔数','process_truth','evaluator','tank_'+name)
 slot('truth.'+name+'_volume_m3',number('m^3',0),'m^3','完整'+name+'循环体积，含未单列持液','process_truth','evaluator','tank_'+name)
slot('truth.initial_amount_mol',number('mol',0),'mol','两循环初始总盐量','process_truth','evaluator','stack_core');slot('truth.initial_volume_m3',number('m^3',0),'m^3','两循环初始总液体体积','process_truth','evaluator','stack_core')
class PccellEd64002Runtime:
 def __init__(self,instance_id='ed_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.plant=None;self.membrane=None;self.method=None;self.sample=None;self.powered=False;self.phase='idle';self.fault='';self.elapsed_s=0.;self.operation_id=None;self._operation_count=0;self.vol=[0.,0.];self.amount=[0.,0.];self.initial_amount=0.;self.initial_volume=0.;self.current=0.;self.voltage=0.;self.ilim=0.;self.flow=0.;self.charge=0.;self.energy=0.;self.history=[];self.templates={x['substance_id']:x for x in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return PccellEd64002Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _kappa(self):
  if not self.sample:return [None,None]
  lm=self.templates[self.sample['substance_id']]['value'];return [lm*n/v for n,v in zip(self.amount,self.vol)]
 def _values(self):
  k=self._kappa();m=self.method or {};p=self.plant or {};v={name+'.configured':bool(getattr(self,name)) for name in ['plant','membrane','method','sample']}
  v.update({'power.enabled':self.powered,'cycle.phase':self.phase,'cycle.fault':self.fault,'cycle.elapsed_s':self.elapsed_s,'electrical.voltage_target_v':m.get('voltage_v',0.),'electrical.current_target_a':m.get('current_a',0.),'flow.target_m3_s':m.get('flow_m3_s',0.),'flow.actual_m3_s':self.flow,'electrical.voltage_v':self.voltage,'electrical.current_a':self.current,'electrical.limiting_current_a':self.ilim,'electrical.charge_c':self.charge,'electrical.energy_j':self.energy,'truth.initial_amount_mol':self.initial_amount,'truth.initial_volume_m3':self.initial_volume})
  for name,key,limit in [('voltage','voltage_v','max_voltage_v'),('current','current_a','max_current_a'),('flow','flow_m3_s','max_flow_m3_s')]:v['panel.'+name+'_angle_rad']=2.6*(2*m[key]/p[limit]-1) if m and p else 0.
  for i,name in enumerate(['diluate','concentrate']):v.update({'conductivity.'+name+'_s_m':k[i],'truth.'+name+'_amount_mol':self.amount[i],'truth.'+name+'_volume_m3':self.vol[i]})
  return v
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('debug','evaluator')})
 def debug_snapshot(self):return copy.deepcopy(dict(state=self.read('evaluator'),plant=self.plant,membrane=self.membrane,method=self.method,sample=self.sample,clock=self.time_s,revision=self.revision,operation=self.operation_id,history=self.history))
 def _stop(self,phase,fault=''):self.phase=phase;self.fault=fault;self.current=0.;self.voltage=0.;self.flow=0.
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if self.phase=='running' and capability not in ['power.set','acquisition.stop'] and CAPS[capability][4]!='query':raise ActionRejected('BUSY')
  if capability=='plant.configure':
   if self.plant or not all(a[x] for x in ['reference_conditions_accepted','external_electrode_loop_confirmed','gas_heat_boundary_confirmed']):raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);out={'configured':True}
  elif capability=='membrane.configure':
   if self.membrane or self.sample or not a['compatibility_reviewed']:raise ActionRejected('PRECONDITION_FAILED')
   self.membrane=copy.deepcopy(a);out={'configured':True}
  elif capability=='method.configure':
   if not self.plant or self.sample:raise ActionRejected('PRECONDITION_FAILED')
   if any(a[k]>self.plant['max_'+k] for k in ['voltage_v','current_a','flow_m3_s']):raise ActionRejected('HOST_ENVELOPE')
   self.method=copy.deepcopy(a);out={'configured':True}
  elif capability=='sample.configure':
   if self.sample or not self.method or not self.membrane or not a['single_stable_dilute_electrolyte_confirmed']:raise ActionRejected('PRECONDITION_FAILED')
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   volumes=[a[k+'_volume_m3'] for k in ['diluate','concentrate']];amounts=[a[k+'_amount_mol'] for k in ['diluate','concentrate']]
   if any(n/v>self.plant['max_concentration_mol_m3'] for n,v in zip(amounts,volumes)):raise ActionRejected('REFERENCE_RANGE')
   self.vol=volumes;self.amount=amounts;self.initial_amount=sum(amounts);self.initial_volume=sum(volumes);self.sample=copy.deepcopy(a);out=dict(configured=True,attachment_event='host_initialization_not_robot_attachment')
  elif capability=='power.set':
   self.powered=a['enabled']
   if not self.powered and self.phase=='running':self._stop('cancelled')
   out=a
  elif capability in ['electrical.set_voltage','electrical.set_current','flow.set_rate']:
   if not self.method:raise ActionRejected('PRECONDITION_FAILED')
   key=next(iter(a))
   if a[key]>self.plant['max_'+key]:raise ActionRejected('HOST_ENVELOPE')
   self.method[key]=a[key];out=a
  elif capability=='acquisition.start':
   if not self.powered or not self.sample or self.phase not in ['idle','cancelled']:raise ActionRejected('PRECONDITION_FAILED')
   self.phase='running';self.fault='';self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase=='running':self._stop('cancelled')
   out=dict(phase=self.phase)
  elif capability=='electrodialysis.measure':
   k=self._kappa();out=dict(phase=self.phase,fault=self.fault,voltage_v=self.voltage,current_a=self.current,limiting_current_a=self.ilim,diluate_conductivity_s_m=k[0],concentrate_conductivity_s_m=k[1],energy_j=self.energy,evidence='derived_reference_ideal_external_conductivity_uncalibrated_ED',template_source=TEMPLATE_REL)
  elif capability=='product.read':out=dict(substance_id=self.sample['substance_id'] if self.sample else None,diluate_amount_mol=self.amount[0],concentrate_amount_mol=self.amount[1],diluate_volume_m3=self.vol[0],concentrate_volume_m3=self.vol[1],evidence='simulation_truth_not_measured_recovery',template_source=TEMPLATE_REL)
  elif capability=='history.read':out=dict(records=copy.deepcopy(self.history),evidence='host_recorded_synthetic_ED_with_derived_reference_conductivity')
  elif capability=='reference.lookup':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   r=self.templates[a['substance_id']];out=dict(substance_id=a['substance_id'],formula=r['formula'],limiting_molar_conductivity_s_m2_mol=r['value'],temperature_k=298.15,evidence='derived',source=r['source'],template_source=TEMPLATE_REL)
  validate(CAPS[capability][1],out)
  if CAPS[capability][4]!='query':self.revision+=1
  return copy.deepcopy(out)
 def step(self,dt_s):
  validate(number('s',0,86400),dt_s)
  remaining=float(dt_s)
  while remaining>1e-12 and self.phase=='running':
   m=self.method;p=self.plant;mem=self.membrane;k=self._kappa();cd,cc=[n/v for n,v in zip(self.amount,self.vol)]
   if k[0]<=m['endpoint_conductivity_s_m'] or self.elapsed_s>=m['duration_s']-1e-10:self._stop('succeeded');break
   if self.elapsed_s>=m['timeout_s']-1e-10:self._stop('failed','TIMEOUT');break
   eq=self.templates[self.sample['substance_id']]['equivalents_per_mol'];self.ilim=F*AREA*p['mass_transfer_m_s']*eq*cd
   resistance=p['series_resistance_ohm']+PAIRS*GAP/AREA*(1/max(k[0],1e-12)+1/max(k[1],1e-12))
   self.current=min(m['current_a'],m['voltage_v']/resistance,p['polarization_fraction']*self.ilim);self.voltage=self.current*resistance;self.flow=m['flow_m3_s']
   efficiency=min(mem['cation_selectivity'],mem['anion_selectivity']);equiv_rate=efficiency*PAIRS*self.current/F
   salt_rate=equiv_rate/eq-mem['salt_permeability_m_s']*AREA*PAIRS*(cc-cd)
   water_rate=mem['water_drag_mol_per_equivalent']*equiv_rate*18e-6
   h=min(remaining,.25,m['duration_s']-self.elapsed_s,m['timeout_s']-self.elapsed_s)
   if salt_rate>0:h=min(h,.1*self.amount[0]/salt_rate)
   elif salt_rate<0:h=min(h,.1*self.amount[1]/-salt_rate)
   if water_rate>0:h=min(h,max(0.,self.vol[0]-HOLDUP)/water_rate,max(0.,.002-self.vol[1])/water_rate)
   # Do not step outside the dilute reference envelope; truncate at its first boundary.
   excess_rate=salt_rate-p['max_concentration_mol_m3']*water_rate
   if excess_rate>0:h=min(h,max(0.,p['max_concentration_mol_m3']*self.vol[1]-self.amount[1])/excess_rate)
   dil_excess=-salt_rate+p['max_concentration_mol_m3']*water_rate
   if dil_excess>0:h=min(h,max(0.,p['max_concentration_mol_m3']*self.vol[0]-self.amount[0])/dil_excess)
   if h<1e-10:self._stop('failed','VOLUME_OR_REFERENCE_BOUNDARY');break
   dn=salt_rate*h;dv=water_rate*h;self.amount[0]-=dn;self.amount[1]+=dn;self.vol[0]-=dv;self.vol[1]+=dv;self.charge+=self.current*h;self.energy+=self.current*self.voltage*h;self.elapsed_s+=h;self.time_s+=h;remaining-=h;self.revision+=1
   k=self._kappa()
   if not self.history or self.elapsed_s-self.history[-1]['time_s']>=1.-1e-9 or h==remaining:self.history.append(dict(time_s=self.elapsed_s,voltage_v=self.voltage,current_a=self.current,diluate_conductivity_s_m=k[0],concentrate_conductivity_s_m=k[1]))
   if self.elapsed_s>=m['duration_s']-1e-10 or k[0]<=m['endpoint_conductivity_s_m']:self._stop('succeeded')
   elif self.elapsed_s>=m['timeout_s']-1e-10:self._stop('failed','TIMEOUT')
  self.time_s+=remaining
class PccellEd64002Session:
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
        meta=export_pccell_ed64002_contract_metadata()
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('electrodialysis.measure'))

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])

def export_pccell_ed64002_contract_metadata():
    caps={};slots={}
    for k,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions='25°C水相无限稀释近似；电导是宿主外部理想观测' if 'conductivity' in k else ('25°C液体实际体积流率，非标况气体' if unit=='m^3/s' else None),origin=origin,visibility=visibility,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'PccellEd64002Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for k,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=k=='acquisition.start'
        # Configuration can initialize several derived/truth slots; declare the actual view changes.
        if k in ['plant.configure','membrane.configure','method.configure','sample.configure']:effects=list(slots)
        if async_:effects=list(slots)
        caps[k]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.pccell_ed64002.PccellEd64002Session.invoke',invocation=f'session.invoke(instance_id="ed_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if kind=='query' else [dict(description_zh='首次边界/膜/装液确认，运行中拒绝重配；start须通电就绪且未完成；拒绝不改状态',state_ids=['cycle.phase','plant.configured','sample.configured','power.enabled'],enforced_by='runtime',check='PccellEd64002Runtime.invoke capability-specific guards')],state_effects=[dict(state_id=i,timing='on_step' if async_ else 'immediate',description_zh='仅代码声明的配置和有限库存变化；不推进时间的设定不改变物理库存') for i in effects],observes=[],model_effects=dict(mode='none',description_zh='几何是否联动由实际场景验证后登记；此处不假定联动',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='宿主时长或外部理想电导终点；超时/液量/浓度边界失败' if async_ else '同步配置或纯查询，不推进时钟',completion_state_ids=['cycle.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['one_ten_pair_stack','two_finite_liquid_loops','external_electrode_and_heat_boundary','principal_request_cache'],partial_failure_zh='停止、断电和超时均保留已转移盐、水、电荷和电能；原生异步去重仅本进程，非真实设备安全保证。'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT','UNKNOWN_REFERENCE','REFERENCE_RANGE','HOST_ENVELOPE']],example_arguments=copy.deepcopy(example),verification=[],limitations=['宿主文件/进程隔离未部署；Basic无内置电导测量或在线控制软件。'],source_ids=['manufacturer_primary','manufacturer_secondary','simulation_assumption'])
        if async_:caps[k]['errors'] += [dict(code=e,meaning_zh=e,side_effects='partial') for e in ['TIMEOUT','VOLUME_OR_REFERENCE_BOUNDARY']]
    mechanism=dict(component_ids=['stack_core','pump_block','tank_diluate','tank_concentrate','power_supply','electrode_anode','electrode_cathode'],state_ids=list(slots),meaning_zh='10膜对双循环二元稀电解质；法拉第盐转移、反扩散、水拖曳、宿主极化限流',implementation='lumped',equations=['Lambda0=F^2/(R*298.15)*sum(nu*z^2*D25); kappa=Lambda0*n/V','R=R_host+N*gap/A*(1/max(kappa_d,1e-12)+1/max(kappa_c,1e-12)); 1e-12 is only numerical regularization','I_lim=F*A*k_mass_transfer*equivalents_per_mol*c_d; I=min(I_set,V_set/R,host_fraction*I_lim); U=I*R','salt_rate=min(cation_selectivity,anion_selectivity)*N*I/(F*eq)-P_salt*A*N*(c_c-c_d)','water_rate=drag*min(selectivity)*N*I/F*18e-6; dilute loses andconcentrate gains equal water volume','n_d+n_c=n_initial; V_d+V_c=V_initial; each salt transfer carries charge-neutral stoichiometric cations and anions','charge=integral(I*dt); energy=integral(U*I*dt); electrode/gas/heat external boundaries not closed','adaptive steps<=0.25s and<=10%donor salt; cap at duration,timeout,2Lcapacity,32mLholdup anddilute-reference envelope','step is sole time advancement; read/describe do not run physics; stopped loops frozen,off-state diffusion not modeled'],parameters={},source_ids=['manufacturer_primary','manufacturer_secondary','diffusion_database','conductivity_equation','simulation_assumption'],limitations=['52derived conductivity templates fromexplicit realD25; not52experimentalEDcurves. Originaldatabaseestimatedvalues notrelabeledmeasured.','Only onefullydissociated stable dilute salt at25C; no multisolute selectivity,ionassociation,pH,complexation,oxidation,precipitation,concentrationactivitycorrection. Hostreview required.','Only standard10pairs; maximum20isdocumentedcapacity,notmodeledgeometry or alternateconfiguration.','Selectivity coefficients areeffectivecurrentefficiencies,mincoupling preservesneutrality,not aresolvedDonnan/NernstPlanckmembranemodel; saltpermeability andwaterdrag explicitassumptions.','One well-mixed controlvolume perloop; wholeloopvolume includesunresolved32mLstackholdup,hoses andpumps; flow ispermissivecirculation boundary,notresolvedhydrodynamics orresidencetimes.','PSUmaxV/maxI,pumpmaxflow,overallsize/massunknownmanufacturer; finiteinputenvelopes andassemblyproxies arehostassumptions.','Actualcurrent/potentiallimitedbysharedcompliancecaps inbothCC/CVmodes; modeassignssetpointversuscompliance semantics,notseparatefirmwarebehavior.','Electroderinse hasconfirmedexternalreservoir/circulation/gasoutlet; noelectrodereactioninventory,nogaschemistry,noelectrodedegradation. Energytrackedbuttemperatureheld25Cexternally.','Basic lacks conductivitymeters/onlinesoftware; measuredreadingsareidealexternalhostobservations,historyandendpointaresimulationhostfunctions.','Salt/watervolumes additive; no osmotic backflow,swelling,viscosity,heatcapacity,membranefouling orwater-splitting.','Stopfreezestransport; off-statediffusion/discharge/doublelayerrelaxationnotmodeled. No current/saltconsumption fabricatedbeyondboundary.','NativeSessionidentity/deduplication/asynchronous testedseparately; Matterix ack doesnotconfirmcompletion; filesandbox,robots,realhardwareunsupported.'])
    spec=importlib.util.spec_from_file_location('_pccell_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
    ann.PARAM_ZH.update({'max_voltage_v':'宿主限定电压，非厂家额定值','max_current_a':'宿主限定电流，非厂家额定值','max_flow_m3_s':'宿主循环流量上限，非厂家额定值','max_concentration_mol_m3':'宿主稀溶液模型有效域上限','mass_transfer_m_s':'假设极化边界有效传质系数','polarization_fraction':'低于极限电流的宿主比例','series_resistance_ohm':'未知膜电极等效串联电阻假设','cation_selectivity':'假设阳膜有效电流效率','anion_selectivity':'假设阴膜有效电流效率','salt_permeability_m_s':'中性盐对有效反扩散渗透率假设','water_drag_mol_per_equivalent':'每电荷当量拖曳水的假设摩尔数','pairs':'Basic标准10膜对，非自由扩展配置'})
    ann.annotate_export(SLUG,caps,slots,{'electrodialysis':mechanism},inputs={'plant.configure':(PLANT,EXAMPLE),'membrane.configure':(MEMBRANE,MEMBRANE_EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'electrodialysis':mechanism})

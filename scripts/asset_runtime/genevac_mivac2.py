"""miVac 2 Duo Rev00101/25: six aggregate plates, explicit external boundaries.

Host thermal control, ideal-Raoult boiling-only mass transfer and trap efficiency
are assumptions. This is neither calibrated firmware nor a hardware safety model.
"""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.pccell_ed64002 import validate
ROOT=Path(__file__).resolve().parents[2];SLUG='genevac_mivac2';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-036';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/genevac_mivac2_evaporation_library/templates.json'
MAX_SPEED=1465*2*math.pi/60;AMBIENT=101325.
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
PLANT=obj({'external_vacuum_confirmed':BOOL,'external_trap_confirmed':BOOL,'compatibility_reviewed':BOOL,'ventilation_clearance_m':number('m',.05,10),'pressure_controller_installed':BOOL,'ultimate_pressure_pa':number('Pa',1,10000),'pressure_tau_s':number('s',.1,1000),'vent_tau_s':number('s',.1,1000),'temperature_tau_s':number('s',.1,10000),'initial_temperature_k':number('K',303.15,353.15),'mass_transfer_mol_s_pa':number('mol/(s*Pa)',1e-14,1e-5),'trap_efficiency':number('1',0,1),'trap_capacity_kg':number('kg',1e-6,100),'acceleration_rad_s2':number('rad/s^2',.1,100),'balance_tolerance_kg':number('kg',1e-9,.001),'program_period_s':number('s',1,3600),'water_pump_duty':number('1',.05,1),'alcohol_pump_duty':number('1',.05,1),'coefficient_evidence':TEXT})
PLANT_EXAMPLE=dict(external_vacuum_confirmed=True,external_trap_confirmed=True,compatibility_reviewed=True,ventilation_clearance_m=.05,pressure_controller_installed=True,ultimate_pressure_pa=100.,pressure_tau_s=3.,vent_tau_s=2.,temperature_tau_s=20.,initial_temperature_k=303.15,mass_transfer_mol_s_pa=1e-8,trap_efficiency=.95,trap_capacity_kg=.01,acceleration_rad_s2=20.,balance_tolerance_kg=.00001,program_period_s=60.,water_pump_duty=.8,alcohol_pump_duty=.9,coefficient_evidence='explicit_host_assumptions_not_manufacturer_firmware_or_calibration')
METHOD=obj({'temperature_k':number('K',303.15,353.15),'heating_time_s':number('s',0,86400),'duration_s':number('s',.1,86400),'timeout_s':number('s',.1,86400),'program':dict(type='string',enum=['water','alcohol','other']),'target_pressure_pa':nullable(number('Pa',100,110000)),'endpoint_remaining_fraction':number('1',0,.99)})
METHOD_EXAMPLE=dict(temperature_k=313.15,heating_time_s=120.,duration_s=120.,timeout_s=150.,program='other',target_pressure_pa=1000.,endpoint_remaining_fraction=.01)
SPECIES=obj({'substance_id':TEXT,'fit_id':TEXT,'amount_mol':number('mol',1e-12,10)})
PLATE=obj({'plate_index':dict(type='integer',minimum=0,maximum=5,**{'x-unit':'1'}),'species':dict(type='array',items=SPECIES,minItems=1,maxItems=4),'nonvolatile_amount_mol':number('mol',0,10),'nonvolatile_mass_kg':number('kg',0,.1),'tare_mass_kg':number('kg',.001,.2)})
SAMPLE=obj({'sample_id':TEXT,'plates':dict(type='array',items=PLATE,minItems=6,maxItems=6),'uniform_plate_contents_confirmed':BOOL})
def example_sample():return dict(sample_id='six_matched_water_ethanol_plates',uniform_plate_contents_confirmed=True,plates=[dict(plate_index=i,species=[dict(substance_id='water',fit_id='3',amount_mol=.03),dict(substance_id='ethanol',fit_id='0',amount_mol=.015)],nonvolatile_amount_mol=.0005,nonvolatile_mass_kg=.0001,tare_mass_kg=.03) for i in range(6)])
MEASURE=obj({'phase':PHASE,'fault':TEXT,'temperature_k':number('K',303.15,353.15),'pressure_pa':number('Pa',0),'speed_rad_s':number('rad/s',0,MAX_SPEED),'remaining_fraction':nullable(number('1',0,1)),'captured_mass_kg':number('kg',0),'exhaust_mass_kg':number('kg',0),'lid_locked':BOOL,'evidence':TEXT,'template_source':TEXT})
PRODUCT_ROW=obj({'plate_index':dict(type='integer',minimum=0,maximum=5,**{'x-unit':'1'}),'substance_id':TEXT,'liquid_mol':number('mol',0),'captured_mol':number('mol',0),'exhaust_mol':number('mol',0),'initial_mol':number('mol',0)})
POINT=obj({'time_s':number('s',0),'temperature_k':number('K',303.15,353.15),'pressure_pa':number('Pa',0),'speed_rad_s':number('rad/s',0,MAX_SPEED),'remaining_fraction':number('1',0,1)})
CAPS={
 'plant.configure':(PLANT,obj({'configured':BOOL}),['port_vacuum','port_gas_vent'],'宿主明确外接真空/冷阱与未知传质、热、转速、通气边界','configuration','evaluator',PLANT_EXAMPLE),
 'rotor.configure':(obj({'configuration':dict(type='string',enum=['two_swing_six_shallow']),'accessory_compatibility_reviewed':BOOL}),obj({'configured':BOOL}),['rotor','basket_left','basket_right'],'选定双摆篮和四叠板件；管转子未实现','configuration','evaluator',dict(configuration='two_swing_six_shallow',accessory_compatibility_reviewed=True)),
 'method.configure':(METHOD,obj({'configured':BOOL}),['panel','display_digits'],'温度/加热时间/运行时间/溶剂程序；压力目标需可选控制器','configuration','agent',METHOD_EXAMPLE),
 'sample.configure':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['plate_'+str(i) for i in range(6)],'宿主装入六板聚合库存；不是机器人已插入576孔','configuration','evaluator',example_sample()),
 'lid.set_open':(obj({'open':BOOL}),obj({'open':BOOL}),['lid'],'安全停转且接近大气压才能开盖，宿主锁止代理','device_command','agent',dict(open=False)),
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['panel'],'虚拟供电；关电取消蒸发并继续由step减速和通气','device_command','agent',dict(enabled=True)),
 'temperature.set':(obj({'temperature_k':number('K',303.15,353.15)}),obj({'temperature_k':number('K',303.15,353.15)}),['display_digits'],'只写温度目标，不把设定瞬间当真实升温','device_command','agent',dict(temperature_k=313.15)),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['rotor','chamber_wall'],'启动离心与减压；唯一物理推进入口是step','device_command','agent',{}),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['rotor','port_gas_vent'],'取消后保留浓缩和捕集结果；减速/通气不是瞬时完成','device_command','agent',{}),
 'evaporation.measure':(obj({}),MEASURE,['display_digits'],'理想化宿主观测，不声称有称重或溶剂在线传感器','query','agent',{}),
 'product.read':(obj({}),obj({'records':dict(type='array',items=PRODUCT_ROW),'nonvolatile_mass_kg':number('kg',0),'evidence':TEXT,'template_source':TEXT}),['plate_'+str(i) for i in range(6)],'保留溶剂、冷阱和尾气的逐物质真值','query','evaluator',{}),
 'history.read':(obj({}),obj({'records':dict(type='array',items=POINT),'evidence':TEXT}),['display_digits'],'宿主时间序列，不是厂家软件数据格式','query','agent',{}),
 'reference.lookup':(obj({'substance_id':TEXT,'fit_id':TEXT,'temperature_k':number('K',303.15,353.15)}),obj({'pressure_pa':number('Pa',0),'source':TEXT,'evidence':TEXT,'template_source':TEXT}),['chamber_wall'],'查询真实NIST温区内蒸气压，不外推','query','agent',dict(substance_id='water',fit_id='0',temperature_k=313.15))}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,zh,o='derived',v='agent',c='display_digits',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=zh
for x in ['plant','rotor','method','sample']:slot(x+'.configured',BOOL,'1',x+'配置状态')
slot('power.enabled',BOOL,'1','虚拟供电','setpoint',w=('power.set','enabled'));slot('lid.open',BOOL,'1','顶盖开合目标','setpoint',c='lid',w=('lid.set_open','open'))
slot('lid.locked',BOOL,'1','基于转速/压力的宿主开盖锁止',c='lid');slot('lid.angle_rad',number('rad',0,1.45),'rad','顶盖教学角度',c='lid')
slot('cycle.phase',PHASE,'1','异步作业状态');slot('cycle.fault',TEXT,'1','终止原因');slot('cycle.elapsed_s',number('s',0),'s','本次作业累计时间')
slot('thermal.target_k',number('K',303.15,353.15),'K','腔体温度目标','setpoint',w=('temperature.set','temperature_k'));slot('thermal.temperature_k',number('K',303.15,353.15),'K','受宿主热边界控制的共同温度，不是实测传感器')
slot('vacuum.pressure_pa',number('Pa',0),'Pa','外部泵/通气一阶模型绝对压力');slot('rotor.speed_rad_s',number('rad/s',0,MAX_SPEED),'rad/s','转速斜坡代理',c='rotor');slot('rotor.angle_rad',number('rad',-math.pi,math.pi),'rad','转子累计角折返，非全速PhysX认证',c='rotor');slot('basket.angle_rad',number('rad',0,1.45),'rad','摆篮角度代理',c='basket_left')
slot('output.remaining_fraction',nullable(number('1',0,1)),'1','宿主理想剩余挥发物摩尔比例，非设备直接测量');slot('output.captured_mass_kg',number('kg',0),'kg','外部捕集边界累计质量');slot('output.exhaust_mass_kg',number('kg',0),'kg','未捕集溶剂累计外流量')
slot('truth.liquid_mol',number('mol',0),'mol','六板全部挥发物库存','process_truth','evaluator','rotor');slot('truth.initial_mol',number('mol',0),'mol','最初挥发物摩尔数','process_truth','evaluator','rotor')
class GenevacMivac2Runtime:
 def __init__(self,instance_id='mivac_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.plant=None;self.rotor=None;self.method=None;self.sample=None;self.powered=False;self.open=True;self.phase='idle';self.fault='';self.elapsed_s=0.;self.temperature=303.15;self.pressure=AMBIENT;self.speed=0.;self.angle=0.;self.records=[];self.history=[];self.operation_id=None;self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ['agent','evaluator','debug']:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return GenevacMivac2Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _fit(self,sid,fid):
  if sid not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
  for f in self.templates[sid]['fits']:
   if f['fit_id']==fid:return f
  raise ActionRejected('UNKNOWN_REFERENCE')
 def _psat(self,sid,fid,t):
  f=self._fit(sid,fid);lo,hi=f['runtime_temperature_range_k']
  if not lo-1e-10<=t<=hi+1e-10:raise ActionRejected('REFERENCE_RANGE')
  return 1e5*10**(f['A']-f['B_k']/(t+f['C_k']))
 def _fraction(self):return sum(r['liquid_mol'] for r in self.records)/sum(r['initial_mol'] for r in self.records) if self.records else None
 def _mass(self,key):return sum(r[key]*self.templates[r['substance_id']]['molar_mass_kg_mol'] for r in self.records)
 def _locked(self):return self.phase=='running' or self.speed>.01 or abs(self.pressure-AMBIENT)>100.
 def _values(self):
  v={n+'.configured':bool(getattr(self,n)) for n in ['plant','rotor','method','sample']};v.update({'power.enabled':self.powered,'lid.open':self.open,'lid.locked':self._locked(),'lid.angle_rad':1.45 if self.open else 0.,'cycle.phase':self.phase,'cycle.fault':self.fault,'cycle.elapsed_s':self.elapsed_s,'thermal.target_k':self.method['temperature_k'] if self.method else 303.15,'thermal.temperature_k':self.temperature,'vacuum.pressure_pa':self.pressure,'rotor.speed_rad_s':self.speed,'rotor.angle_rad':self.angle,'basket.angle_rad':1.45*self.speed/MAX_SPEED,'output.remaining_fraction':self._fraction(),'output.captured_mass_kg':self._mass('captured_mol'),'output.exhaust_mass_kg':self._mass('exhaust_mol'),'truth.liquid_mol':sum(r['liquid_mol'] for r in self.records),'truth.initial_mol':sum(r['initial_mol'] for r in self.records)});return v
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ['debug','evaluator']})
 def debug_snapshot(self):return copy.deepcopy(dict(values=self._values(),plant=self.plant,rotor=self.rotor,method=self.method,sample=self.sample,records=self.records,history=self.history,time=self.time_s,revision=self.revision,operation=self.operation_id))
 def _valid_temperature_path(self,target,plates=None):
  for p in plates if plates is not None else (self.sample or {}).get('plates',[]):
   for s in p['species']:
    for t in [self.temperature,target]:self._psat(s['substance_id'],s['fit_id'],t)
 def _balanced(self):
  if not self.sample:return False
  masses=[]
  for p in self.sample['plates']:masses.append(p['tare_mass_kg']+p['nonvolatile_mass_kg']+sum(r['liquid_mol']*self.templates[r['substance_id']]['molar_mass_kg_mol'] for r in self.records if r['plate_index']==p['plate_index']))
  return all(abs(masses[i]-masses[i+3])<=self.plant['balance_tolerance_kg'] for i in range(3))
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if self.phase=='running' and capability not in ['power.set','acquisition.stop'] and CAPS[capability][4]!='query':raise ActionRejected('BUSY')
  if capability=='plant.configure':
   if self.plant or not all(a[x] for x in ['external_vacuum_confirmed','external_trap_confirmed','compatibility_reviewed']):raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);self.temperature=a['initial_temperature_k'];out=dict(configured=True)
  elif capability=='rotor.configure':
   if self.rotor or not self.open or not a['accessory_compatibility_reviewed']:raise ActionRejected('PRECONDITION_FAILED')
   self.rotor=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='method.configure':
   if not self.plant or self.sample:raise ActionRejected('PRECONDITION_FAILED')
   if a['target_pressure_pa'] is not None and (not self.plant['pressure_controller_installed'] or not self.plant['ultimate_pressure_pa']<=a['target_pressure_pa']<=AMBIENT):raise ActionRejected('PRESSURE_CONTROLLER_REQUIRED')
   self.method=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='sample.configure':
   if self.sample or not self.rotor or not self.method or not self.open or not a['uniform_plate_contents_confirmed']:raise ActionRejected('PRECONDITION_FAILED')
   if sorted(p['plate_index'] for p in a['plates'])!=list(range(6)):raise ActionRejected('INVALID_ARGUMENT')
   self._valid_temperature_path(self.method['temperature_k'],a['plates']);records=[]
   for p in a['plates']:
    if len({s['substance_id'] for s in p['species']})!=len(p['species']):raise ActionRejected('INVALID_ARGUMENT')
    for s in p['species']:records.append(dict(plate_index=p['plate_index'],substance_id=s['substance_id'],fit_id=s['fit_id'],liquid_mol=s['amount_mol'],captured_mol=0.,exhaust_mol=0.,initial_mol=s['amount_mol']))
   self.sample=copy.deepcopy(a);self.sample['plates'].sort(key=lambda p:p['plate_index']);self.records=records;out=dict(configured=True,attachment_event='host_initialization_not_robot_attachment')
  elif capability=='lid.set_open':
   if a['open'] and self._locked():raise ActionRejected('LID_INTERLOCK')
   self.open=a['open'];out=a
  elif capability=='power.set':
   self.powered=a['enabled']
   if not self.powered and self.phase=='running':self.phase='cancelled'
   out=a
  elif capability=='temperature.set':
   if not self.method:raise ActionRejected('PRECONDITION_FAILED')
   self._valid_temperature_path(a['temperature_k']);self.method['temperature_k']=a['temperature_k'];out=a
  elif capability=='acquisition.start':
   if not self.powered or self.open or not self.sample or self.phase not in ['idle','cancelled']:raise ActionRejected('PRECONDITION_FAILED')
   if not self._balanced():raise ActionRejected('ROTOR_IMBALANCE')
   self.phase='running';self.fault='';self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase=='running':self.phase='cancelled'
   out=dict(phase=self.phase)
  elif capability=='evaporation.measure':out=dict(phase=self.phase,fault=self.fault,temperature_k=self.temperature,pressure_pa=self.pressure,speed_rad_s=self.speed,remaining_fraction=self._fraction(),captured_mass_kg=self._mass('captured_mol'),exhaust_mass_kg=self._mass('exhaust_mol'),lid_locked=self._locked(),evidence='derived_Psat_synthetic_dynamics_ideal_host_observations',template_source=TEMPLATE_REL)
  elif capability=='product.read':out=dict(records=[{k:v for k,v in r.items() if k!='fit_id'} for r in self.records],nonvolatile_mass_kg=sum(p['nonvolatile_mass_kg'] for p in (self.sample or {}).get('plates',[])),evidence='simulation_truth_not_assayed_dryness',template_source=TEMPLATE_REL)
  elif capability=='history.read':out=dict(records=copy.deepcopy(self.history),evidence='synthetic_engineering_time_curve_with_real_NIST_Psat')
  elif capability=='reference.lookup':out=dict(pressure_pa=self._psat(a['substance_id'],a['fit_id'],a['temperature_k']),source=self.templates[a['substance_id']]['source'],evidence='derived',template_source=TEMPLATE_REL)
  validate(CAPS[capability][1],out)
  if CAPS[capability][4]!='query':self.revision+=1
  return copy.deepcopy(out)
 def step(self,dt_s):
  validate(number('s',0,86400),dt_s);remaining=float(dt_s)
  while remaining>1e-12:
   h=min(remaining,.1);p=self.plant;m=self.method;running=self.phase=='running'
   if running:
    if self.elapsed_s>=m['duration_s']-1e-9 or self._fraction()<=m['endpoint_remaining_fraction']:self.phase='succeeded';running=False
    elif self.elapsed_s>=m['timeout_s']-1e-9:self.phase='failed';self.fault='TIMEOUT';running=False
    else:h=min(h,m['duration_s']-self.elapsed_s,m['timeout_s']-self.elapsed_s)
   if p:
    target_speed=MAX_SPEED if running else 0.;old_speed=self.speed;self.speed+=max(-p['acceleration_rad_s2']*h,min(p['acceleration_rad_s2']*h,target_speed-self.speed));self.speed=min(MAX_SPEED,max(0.,self.speed));self.angle=(self.angle+.5*(old_speed+self.speed)*h+math.pi)%(2*math.pi)-math.pi
    pump=False
    if running and self.speed>=.95*MAX_SPEED:
     duty=1. if m['program']=='other' else p[m['program']+'_pump_duty'];pump=(self.elapsed_s%p['program_period_s'])/p['program_period_s']<duty
    target_p=(m['target_pressure_pa'] if m['target_pressure_pa'] is not None else p['ultimate_pressure_pa']) if pump else AMBIENT
    tau=p['pressure_tau_s'] if pump else p['vent_tau_s'];self.pressure=target_p+(self.pressure-target_p)*math.exp(-h/tau)
    # Ideal external heat boundary balances latent heat; no false energy-closure claim.
    target_t=m['temperature_k'] if running and self.elapsed_s<m['heating_time_s'] else self.temperature
    next_t=target_t+(self.temperature-target_t)*math.exp(-h/p['temperature_tau_s'])
    try:self._valid_temperature_path(next_t)
    except ActionRejected:self.phase='failed';self.fault='REFERENCE_RANGE';running=False;next_t=self.temperature
    self.temperature=next_t
    if running and pump:
     for plate in self.sample['plates']:
      rr=[r for r in self.records if r['plate_index']==plate['plate_index']];den=sum(r['liquid_mol'] for r in rr)+plate['nonvolatile_amount_mol']
      partial=[r['liquid_mol']/max(den,1e-30)*self._psat(r['substance_id'],r['fit_id'],self.temperature) for r in rr];bubble=sum(partial);factor=max(0.,1-self.pressure/max(bubble,1e-30))
      proposed=[min(r['liquid_mol'],p['mass_transfer_mol_s_pa']*pp*factor*h) for r,pp in zip(rr,partial)]
      # Capture is a single external finite reservoir; update sequentially without loss of species balance.
      for r,dn in zip(rr,proposed):
       mm=self.templates[r['substance_id']]['molar_mass_kg_mol'];free=max(0.,p['trap_capacity_kg']-self._mass('captured_mol'));captured=min(p['trap_efficiency']*dn,free/mm);r['liquid_mol']-=dn;r['captured_mol']+=captured;r['exhaust_mol']+=dn-captured
     if not self._balanced():self.phase='failed';self.fault='ROTOR_IMBALANCE';running=False
    if self.phase=='running':self.elapsed_s+=h
   self.time_s+=h;remaining-=h;self.revision+=1
   if self.sample:self.history.append(dict(time_s=self.time_s,temperature_k=self.temperature,pressure_pa=self.pressure,speed_rad_s=self.speed,remaining_fraction=self._fraction()))
  if self.phase=='running':
   if self.elapsed_s>=self.method['duration_s']-1e-9 or self._fraction()<=self.method['endpoint_remaining_fraction']:self.phase='succeeded'
   elif self.elapsed_s>=self.method['timeout_s']-1e-9:self.phase='failed';self.fault='TIMEOUT'
  return self.read()
class GenevacMivac2Session:
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
        meta=export_genevac_mivac2_contract_metadata()
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('evaporation.measure'))

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])

def export_genevac_mivac2_contract_metadata():
    caps={};slots={}
    for k,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions='绝对压力；宿主理想观测，非原厂传感器' if 'pressure' in k else None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'GenevacMivac2Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for k,(inp,out,components,meaning,kind,vis,example) in CAPS.items():
        async_=k=='acquisition.start';effects=[] if kind=='query' else list(slots)
        caps[k]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.genevac_mivac2.GenevacMivac2Session.invoke',invocation=f'session.invoke(instance_id="mivac_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if kind=='query' else [dict(description_zh='边界与附件确认；六板装载；温区与压力控制器保护；start须通电/关盖/平衡；开盖须低转速且接近大气；拒绝不改状态',state_ids=['cycle.phase','plant.configured','sample.configured','power.enabled','lid.locked'],enforced_by='runtime',check='GenevacMivac2Runtime.invoke capability-specific guards')],state_effects=[dict(state_id=i,timing='on_step' if async_ else 'immediate',description_zh='实际配置/库存效应；查询不推进时钟；取消保留已蒸发量') for i in effects],observes=[],model_effects=dict(mode='none',description_zh='需真实场景求值证据才登记联动',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='运行时长或宿主剩余比例终点；超时/失衡失败；停止不等于已安全开盖' if async_ else '同步返回，不推进世界',completion_state_ids=['cycle.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['six_aggregate_plates','external_vacuum_and_finite_trap','host_heat_boundary','principal_request_cache'],partial_failure_zh='取消、断电、超时保留液体/捕集/尾气与非挥发物；step继续减速通气。'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT','UNKNOWN_REFERENCE','REFERENCE_RANGE','LID_INTERLOCK','ROTOR_IMBALANCE','PRESSURE_CONTROLLER_REQUIRED']],example_arguments=copy.deepcopy(example),verification=[],limitations=['非真实固件/安全认证；宿主文件进程沙箱、机器人操作与化学兼容性未验证。'],source_ids=['manufacturer_primary','manufacturer_secondary','simulation_assumption'])
        if async_:caps[k]['errors'] += [dict(code=e,meaning_zh=e,side_effects='partial') for e in ['TIMEOUT','RUNTIME_IMBALANCE']]
    mechanism=dict(component_ids=['rotor','basket_left','basket_right','chamber_wall','port_vacuum','port_gas_vent'],state_ids=list(slots),meaning_zh='六板聚合库存、理想混合减压沸腾代理与外部有限捕集边界',implementation='lumped',equations=['Psat_i=100000*10**(A_i-B_i/(T_K+C_i)); selected NIST fit range only','x_i=n_i/(sum(n_volatile)+n_nonvolatile); P_bubble=sum(x_i*Psat_i)','dn_i=min(n_i,k_host*x_i*Psat_i*max(0,1-P/P_bubble)*dt); boiling-only surrogate; sub-boiling diffusion unmodeled','capture_i=min(eta_host*dn_i,free_trap_mass/M_i); exhaust_i=dn_i-capture_i','each species initial=remaining_liquid+trap+exhaust; nonvolatile solute mass and moles unchanged','pressure follows exact first-order external pump/vent boundary; no resolved chamber vapor holdup','temperature follows prescribed host first-order heat boundary; latent heat supplied externally, no claimed energy closure','speed slope limited by explicit host acceleration, max1465rpm; basket_angle=1.45*speed/max_speed is geometry proxy','step<=0.1s; read is pure; stop freezes evaporation but step continues mechanical spin-down and venting'],parameters={},source_ids=['manufacturer_primary','manufacturer_secondary','nist_antoine','simulation_assumption'],limitations=['65real-substance NIST fits, not65measured miVac curves; no range extrapolation or inferred solvent compatibility.','Six shallow plates treated as homogeneous aggregate inventories, not576independent wells. Only two-swing/four-stacker configuration implemented; tube/alternative rotors unsupported.','Ideal Raoult boiling-only mass-transfer surrogate; no sub-boiling diffusion, activity coefficients, azeotropes, decomposition, crystallization, entrainment or calibrated bumping.','External controller optional; target_pressure only allowed if installed. Water/alcohol pump duty and period explicitly host assumptions, not manufacturer firmware timing.','External trap captured mass finite and uncaptured vapor accounted as exhaust; no trap thermal dynamics, chamber gas holdup, pump chemistry or resolved hoses.','Uniform prescribed sample/chamber temperature; all heat including latent heat external unresolved. Heater-off holds current temperature; passive cooling is not modeled.','Initial and setpoint temperatures must fit every chosen substance interval; no universal cold-start or thermal-range coverage claim.','Balance uses corresponding aggregate plate mass and host tolerance; not manufacturer rotor load/safety validation, no independent well-level loading or torque dynamics.','250g is nominal manufacturer maximum, not assigned to every sample or inferred from the proxy rotor radius.','Lid angle and swing angle are assumptions. High-speed rotor scene phase wrapping is only a visualization, not full-speed PhysX validation.','Endpoint is residual volatile mole fraction, not an assayed dry-sample certificate. Nonvolatile solute identity/compatibility are host inputs.','Public JSON identity filtering does not constitute deployed file/process isolation; raw Runtime is host/debug only. Native asynchronous completion separate from Matterix acknowledgments.'])
    spec=importlib.util.spec_from_file_location('_mivac_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
    ann.annotate_export(SLUG,caps,slots,{'evaporation':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'evaporation':mechanism})

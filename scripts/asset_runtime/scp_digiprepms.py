"""DigiPREP MS: bounded historical thermal proxy,48 finite sample/acid ledgers.

No validated digestion kinetics, full aqueous speciation or factory controller.
Public sessions filter truth; trusted host alone configures samples and advances time.
"""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.pccell_ed64002 import validate
ROOT=Path(__file__).resolve().parents[2];SLUG='scp_digiprepms';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-039';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/scp_digiprepms_digestion_library/templates.json'
def integer(lo,hi):return dict(type='integer',minimum=lo,maximum=hi,**{'x-unit':'1'})
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
SIDE=dict(type='string',enum=['left','right'])
PLANT=obj({'controller_confirmed':BOOL,'fume_extraction_reviewed':BOOL,'tube_compatibility_reviewed':BOOL,'ambient_k':number('K',293.15,313.15),'power_w':number('W',1,2000),'block_heat_capacity_j_k':number('J/K',1000,100000),'loss_w_k':number('W/K',.01,20),'contact_w_k':number('W/K',.01,10),'controller_gain_w_k':number('W/K',10,5000),'tube_limit_k':number('K',293.15,453.15),'coefficient_evidence':TEXT})
PLANT_EXAMPLE=dict(controller_confirmed=True,fume_extraction_reviewed=True,tube_compatibility_reviewed=True,ambient_k=293.15,power_w=925.,block_heat_capacity_j_k=-2*2100/math.log(1-2*75/925),loss_w_k=2.,contact_w_k=1.5,controller_gain_w_k=1000.,tube_limit_k=388.15,coefficient_evidence='historical925W35min_empty_proxy_current_rating_unknown; all_other_coefficients_host_assumptions')
KNOT=obj({'time_s':number('s',0,86400),'temperature_k':number('K',293.15,453.15)})
METHOD=obj({'knots':dict(type='array',items=KNOT,minItems=1,maxItems=24),'hold_s':number('s',0,86400),'timeout_s':number('s',.5,172800)})
METHOD_EXAMPLE=dict(knots=[dict(time_s=0.,temperature_k=368.15)],hold_s=60.,timeout_s=7200.)
TUBE=obj({'position':integer(0,47),'substance_id':TEXT,'solid_mol':number('mol',1e-12,1),'acid_mol':number('mol',1e-12,10),'volume_m3':number('m^3',1e-6,50e-6),'density_kg_m3':number('kg/m^3',500,3000),'heat_capacity_j_kg_k':number('J/kg/K',100,10000),'rate_298_s':number('1/s',0,1),'activation_j_mol':number('J/mol',0,200000),'initial_log10_q':number('1',-300,300),'acid_evaporation_298_s':number('1/s',0,.01),'blank_reservoir_mol':number('mol',0,1),'blank_rate_298_s':number('1/s',0,.01)})
SAMPLE=obj({'sample_id':TEXT,'tubes':dict(type='array',items=TUBE,minItems=1,maxItems=48)})
def example_sample():return dict(sample_id='teaching_gibbsite',tubes=[dict(position=0,substance_id='gibbsite',solid_mol=.0001,acid_mol=.003,volume_m3=20e-6,density_kg_m3=1000.,heat_capacity_j_kg_k=4180.,rate_298_s=.00001,activation_j_mol=25000.,initial_log10_q=-10.,acid_evaporation_298_s=.000001,blank_reservoir_mol=1e-9,blank_rate_298_s=.000001)])
MEASURE=obj({'phase':PHASE,'fault':TEXT,'block_temperature_k':number('K',293.15,453.15),'indicated_temperature_k':number('K',293.15,453.15),'target_k':number('K',293.15,453.15),'elapsed_s':number('s',0),'hold_elapsed_s':number('s',0),'evidence':TEXT,'template_source':TEXT})
PRODUCT_ROW=obj({'position':integer(0,47),'substance_id':TEXT,'initial_solid_mol':number('mol',0),'solid_mol':number('mol',0),'dissolved_mol':number('mol',0),'initial_acid_mol':number('mol',0),'acid_mol':number('mol',0),'acid_consumed_mol':number('mol',0),'acid_exhaust_mol':number('mol',0),'initial_blank_mol':number('mol',0),'blank_remaining_mol':number('mol',0),'blank_dissolved_mol':number('mol',0),'temperature_k':number('K',293.15,453.15)})
POINT=obj({'time_s':number('s',0),'block_temperature_k':number('K',293.15,453.15),'indicated_temperature_k':number('K',293.15,453.15),'target_k':number('K',293.15,453.15),'heater_w':number('W',0,2000)})
CAPS={
 'plant.configure':(PLANT,obj({'configured':BOOL}),['heater','internal_probe','port_controller'],'宿主外置控制器/通风/管材审查与热学系数，不冒充现行额定','configuration','evaluator',PLANT_EXAMPLE),
 'method.configure':(METHOD,obj({'configured':BOOL}),['port_controller','heater'],'严格递增时间节点的线性温度程序及达温保温时间','configuration','agent',METHOD_EXAMPLE),
 'sample.configure':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['rack_left','rack_right'],'宿主初始化最多48管、每管50mL有限样品；不是机器人装管','configuration','evaluator',example_sample()),
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['port_controller'],'虚拟供电目标，断电取消并保留样品','device_command','agent',dict(enabled=True)),
 'thermal.set_temperature':(obj({'temperature_k':number('K',293.15,453.15)}),obj({'temperature_k':number('K',293.15,453.15)}),['heater','port_controller'],'未运行时改为恒温程序目标；不瞬时加热','device_command','agent',dict(temperature_k=368.15)),
 'probe.configure':(obj({'mode':dict(type='string',enum=['block','external']),'position':integer(0,47),'connected':BOOL}),obj({'mode':TEXT,'position':integer(0,47)}),['internal_probe','port_probe'],'外接DigiPROBE选配边界；不虚构已建实体探头','configuration','evaluator',dict(mode='block',position=0,connected=False)),
 'rack.set':(obj({'side':SIDE,'lift_m':number('m',0,.12)}),obj({'side':SIDE,'lift_m':number('m',0,.12)}),['rack_left','rack_right'],'宿主手动提架目标；热/运行时互锁，非自驱动或机器人','configuration','evaluator',dict(side='left',lift_m=.1)),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['heater','block','port_controller'],'运行热程序；完成不等于消解完全','device_command','agent',{}),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['heater','port_controller'],'取消保留所有部分转移；温度继续冷却','device_command','agent',{}),
 'digestion.measure':(obj({}),MEASURE,['internal_probe','port_probe'],'理想温度历史观察，不输出隐藏样品标签/真值','query','agent',{}),
 'product.read':(obj({}),obj({'records':dict(type='array',items=PRODUCT_ROW),'evidence':TEXT,'template_source':TEXT}),['rack_left','rack_right'],'评价器逐管矿物当量/酸当量/空白账本，不是实验回收率','query','evaluator',{}),
 'history.read':(obj({}),obj({'records':dict(type='array',items=POINT),'evidence':TEXT}),['port_controller'],'合成宿主温度记录，不是厂家原生文件格式','query','agent',{}),
 'reference.lookup':(obj({'substance_id':TEXT,'temperature_k':number('K',293.15,453.15)}),obj({'log10_k':number('1'),'acid_equivalents_per_mol':number('1',0),'source':TEXT,'evidence':TEXT,'template_source':TEXT}),['block'],'真实矿物反应热力学参考；原温区内求值，不外推','query','agent',dict(substance_id='gibbsite',temperature_k=368.15))}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,zh,o='derived',v='agent',c='port_controller',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=zh
for n in ['plant','method','sample']:slot(n+'.configured',BOOL,'1',n+'配置状态')
slot('power.enabled',BOOL,'1','虚拟供电','setpoint',w=('power.set','enabled'))
slot('cycle.phase',PHASE,'1','热程序作业状态，不是消解化学完成');slot('cycle.fault',TEXT,'1','终止原因');slot('cycle.elapsed_s',number('s',0),'s','程序累计时间');slot('cycle.hold_elapsed_s',number('s',0),'s','达到最终目标±1K期间累计的保温时间')
slot('thermal.target_k',number('K',293.15,453.15),'K','程序当前目标；设定不等于实际温度','setpoint',c='heater',w=('thermal.set_temperature','temperature_k'))
slot('thermal.indicated_temperature_k',number('K',293.15,453.15),'K','当前所选块或外探头理想读数，非精度认证',c='internal_probe')
slot('thermal.block_temperature_k',number('K',293.15,453.15),'K','内置块温理想读数',c='internal_probe');slot('thermal.heater_w',number('W',0,2000),'W','显热模型实际供热功率，非真实电气测量',c='heater')
slot('probe.mode',TEXT,'1','内置块温或外置样品探头选择','setpoint',c='port_probe');slot('probe.position',integer(0,47),'1','外探头所在管位','setpoint',c='port_probe')
for side in ['left','right']:
 slot('rack_'+side+'.target_m',number('m',0,.12),'m','手动管架位移目标','setpoint',c='rack_'+side)
 slot('rack_'+side+'.lift_m',number('m',0,.12),'m','宿主推进后的管架位移，非机器人抓取','scene',c='rack_'+side)
for k,u,zh in [('solid_mol','mol','剩余固体矿物当量'),('dissolved_mol','mol','已溶解矿物当量，非实验完全消解证明'),('acid_mol','mol','剩余酸当量'),('sensible_energy_j','J','块+溶液相对环境显热'),('input_energy_j','J','模型加热输入能量'),('loss_energy_j','J','模型向环境散热')]:slot('truth.'+k,number(u,0),u,zh,'process_truth','evaluator',c='block')

class ScpDigiprepmsRuntime:
 def __init__(self,instance_id='digiprep_01'):
  self.instance_id=instance_id;self.plant=self.method=self.sample=None;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.powered=False;self.phase='idle';self.fault='';self.time_s=self.elapsed_s=self.hold_s=0.;self.block_t=self.target=293.15;self.heater_w=self.input_j=self.loss_j=0.;self.probe_mode='block';self.probe_position=0;self.lift=[0.,0.];self.lift_target=[0.,0.];self.tubes=[];self.history=[];self.revision=0;self.operation_id='';self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility):
  if visibility not in ['agent','debug','evaluator'] or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return ScpDigiprepmsSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _logk(self,sid,t):
  if sid not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
  r=self.templates[sid];lo,hi=r['runtime_temperature_range_k']
  if not lo<=t<=hi:raise ActionRejected('REFERENCE_RANGE')
  a=r['analytic_coefficients'];return a[0]+a[1]*t+a[2]/t+a[3]*math.log10(t)+a[4]/t**2+a[5]*t*t
 def _indicated(self):return next((r['temperature_k'] for r in self.tubes if r['position']==self.probe_position),self.block_t) if self.probe_mode=='external' else self.block_t
 def _energy(self):return 0. if not self.plant else self.plant['block_heat_capacity_j_k']*(self.block_t-self.plant['ambient_k'])+sum(t['_c']*(t['temperature_k']-self.plant['ambient_k']) for t in self.tubes)
 def _values(self):return {**{n+'.configured':bool(getattr(self,n)) for n in ['plant','method','sample']},'power.enabled':self.powered,'cycle.phase':self.phase,'cycle.fault':self.fault,'cycle.elapsed_s':self.elapsed_s,'cycle.hold_elapsed_s':self.hold_s,'thermal.target_k':self.target,'thermal.block_temperature_k':self.block_t,'thermal.indicated_temperature_k':self._indicated(),'thermal.heater_w':self.heater_w,'probe.mode':self.probe_mode,'probe.position':self.probe_position,**{'rack_'+s+'.'+k:v for i,s in enumerate(['left','right']) for k,v in [('target_m',self.lift_target[i]),('lift_m',self.lift[i])]},'truth.solid_mol':sum(t['solid_mol'] for t in self.tubes),'truth.dissolved_mol':sum(t['dissolved_mol'] for t in self.tubes),'truth.acid_mol':sum(t['acid_mol'] for t in self.tubes),'truth.sensible_energy_j':max(0,self._energy()),'truth.input_energy_j':self.input_j,'truth.loss_energy_j':self.loss_j}
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ['debug','evaluator']})
 def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k!='templates'})
 def _check_method(self,m):
  if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
  ks=m['knots'];p=self.plant
  if ks[0]['time_s']!=0 or any(b['time_s']<=a['time_s'] for a,b in zip(ks,ks[1:])):raise ActionRejected('PROGRAM_ORDER')
  if any(not p['ambient_k']<=k['temperature_k']<=p['tube_limit_k'] for k in ks):raise ActionRejected('TUBE_TEMPERATURE_LIMIT')
  for t in self.tubes:
   for k in ks:self._logk(t['substance_id'],k['temperature_k'])
 def _program(self,t):
  ks=self.method['knots']
  for a,b in zip(ks,ks[1:]):
   if t<=b['time_s']:return a['temperature_k']+(b['temperature_k']-a['temperature_k'])*(t-a['time_s'])/(b['time_s']-a['time_s'])
  return ks[-1]['temperature_k']
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if self.phase=='running' and capability in ['plant.configure','method.configure','sample.configure','probe.configure','thermal.set_temperature','rack.set']:raise ActionRejected('BUSY')
  if capability=='plant.configure':
   if self.plant or not all(a[k] for k in ['controller_confirmed','fume_extraction_reviewed','tube_compatibility_reviewed']) or a['tube_limit_k']<a['ambient_k']:raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);self.block_t=self.target=a['ambient_k'];out=dict(configured=True)
  elif capability=='method.configure':
   self._check_method(a)
   if self.elapsed_s>0:raise ActionRejected('CYCLE_ALREADY_STARTED')
   self.method=copy.deepcopy(a);self.target=a['knots'][0]['temperature_k'];out=dict(configured=True)
  elif capability=='sample.configure':
   if self.sample or not self.method or self.phase!='idle' or self.block_t>323.15:raise ActionRejected('PRECONDITION_FAILED')
   if len({x['position'] for x in a['tubes']})!=len(a['tubes']):raise ActionRejected('DUPLICATE_POSITION')
   rows=[]
   for x in a['tubes']:
    self._logk(x['substance_id'],self.plant['ambient_k'])
    for k in self.method['knots']:self._logk(x['substance_id'],k['temperature_k'])
    rows.append(dict(position=x['position'],substance_id=x['substance_id'],initial_solid_mol=x['solid_mol'],solid_mol=x['solid_mol'],dissolved_mol=0.,initial_acid_mol=x['acid_mol'],acid_mol=x['acid_mol'],acid_consumed_mol=0.,acid_exhaust_mol=0.,initial_blank_mol=x['blank_reservoir_mol'],blank_remaining_mol=x['blank_reservoir_mol'],blank_dissolved_mol=0.,temperature_k=self.plant['ambient_k'],_input=copy.deepcopy(x),_c=x['volume_m3']*x['density_kg_m3']*x['heat_capacity_j_kg_k']))
   self.tubes=rows;self.sample=copy.deepcopy(a);out=dict(configured=True,attachment_event='host_sample_ledger_initialization_not_robot')
  elif capability=='power.set':
   self.powered=a['enabled']
   if not self.powered and self.phase=='running':self.phase='cancelled'
   out=a
  elif capability=='thermal.set_temperature':
   if not self.method or self.elapsed_s>0:raise ActionRejected('PRECONDITION_FAILED')
   m=copy.deepcopy(self.method);m['knots']=[dict(time_s=0.,temperature_k=a['temperature_k'])];self._check_method(m);self.method=m;self.target=a['temperature_k'];out=a
  elif capability=='probe.configure':
   if a['mode']=='external' and (not a['connected'] or not any(t['position']==a['position'] for t in self.tubes)):raise ActionRejected('PROBE_NOT_CONNECTED')
   self.probe_mode=a['mode'];self.probe_position=a['position'];out=dict(mode=a['mode'],position=a['position'])
  elif capability=='rack.set':
   if max([self.block_t]+[t['temperature_k'] for t in self.tubes])>323.15:raise ActionRejected('HOT_RACK')
   self.lift_target[0 if a['side']=='left' else 1]=a['lift_m'];out=a
  elif capability=='acquisition.start':
   if not self.powered or not self.method or self.phase not in ['idle','cancelled'] or any(abs(q)>1e-8 for q in self.lift+self.lift_target) or self.elapsed_s>=self.method['timeout_s']:raise ActionRejected('PRECONDITION_FAILED')
   self._check_method(self.method);self.phase='running';self.fault='';self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase=='running':self.phase='cancelled'
   out=dict(phase=self.phase)
  elif capability=='digestion.measure':out=dict(phase=self.phase,fault=self.fault,block_temperature_k=self.block_t,indicated_temperature_k=self._indicated(),target_k=self.target,elapsed_s=self.elapsed_s,hold_elapsed_s=self.hold_s,evidence='synthetic_thermal_history_real_LLNL_reference_not_experimental_digestion',template_source=TEMPLATE_REL)
  elif capability=='product.read':out=dict(records=[{k:v for k,v in t.items() if not k.startswith('_')} for t in self.tubes],evidence='simulation_mineral_acid_blank_equivalent_ledgers_not_measured_recovery',template_source=TEMPLATE_REL)
  elif capability=='history.read':out=dict(records=copy.deepcopy(self.history),evidence='synthetic_host_temperature_history')
  elif capability=='reference.lookup':out=dict(log10_k=self._logk(a['substance_id'],a['temperature_k']),acid_equivalents_per_mol=self.templates[a['substance_id']]['acid_equivalents_per_mol'],source=self.templates[a['substance_id']]['source'],evidence='derived',template_source=TEMPLATE_REL)
  validate(CAPS[capability][1],out)
  if CAPS[capability][4]!='query':self.revision+=1
  return copy.deepcopy(out)
 def _react(self,h):
  # Precompute all tube increments: unknown/out-of-range template cannot partially apply a batch.
  increments=[]
  for t in self.tubes:
   x=t['_input'];temp=t['temperature_k'];lk=self._logk(t['substance_id'],temp);nu=self.templates[t['substance_id']]['acid_equivalents_per_mol'];acid=t['acid_mol'];logq=x['initial_log10_q']+nu*math.log10(t['initial_acid_mol']/max(acid,1e-300));drive=max(0.,1.-10**min(0.,logq-lk));rate=x['rate_298_s']*math.exp(min(50.,x['activation_j_mol']/8.314462618*(1/298.15-1/temp)));dn=min(t['solid_mol']*(-math.expm1(-rate*drive*h)),acid/nu);rest=max(0.,acid-nu*dn);factor=math.exp(min(10.,(temp-298.15)/30));ev=rest*(-math.expm1(-x['acid_evaporation_298_s']*factor*h));blank=t['blank_remaining_mol']*(-math.expm1(-x['blank_rate_298_s']*factor*h));increments.append((dn,nu*dn,ev,blank))
  for t,(dn,consumed,ev,blank) in zip(self.tubes,increments):t['solid_mol']-=dn;t['dissolved_mol']+=dn;t['acid_mol']=max(0.,t['acid_mol']-consumed-ev);t['acid_consumed_mol']+=consumed;t['acid_exhaust_mol']+=ev;t['blank_remaining_mol']-=blank;t['blank_dissolved_mol']+=blank
 def step(self,dt_s):
  validate(number('s',0,172800),dt_s);remaining=float(dt_s)
  while remaining>1e-10:
   h=min(remaining,.5);p=self.plant;running=self.phase=='running'
   if running:
    h=min(h,self.method['timeout_s']-self.elapsed_s)
    if h<=1e-10:self.phase='failed';self.fault='TIMEOUT';continue
    self.target=self._program(self.elapsed_s+h*.5)
   self.heater_w=0.
   if p:
    c=p['block_heat_capacity_j_k'];ua=p['loss_w_k'];ta=p['ambient_k']
    if running:
     requested=max(0.,ua*(self.target-ta)+p['controller_gain_w_k']*(self.target-self._indicated()));self.heater_w=min(p['power_w'],requested,max(0.,c*(453.15-self.block_t)/h+ua*(self.block_t-ta)))
    before=self.block_t;equilibrium=ta+self.heater_w/ua;self.block_t=equilibrium+(self.block_t-equilibrium)*math.exp(-ua*h/c);self.input_j+=self.heater_w*h;self.loss_j+=self.heater_w*h-c*(self.block_t-before)
    for t in self.tubes:
     side=0 if t['position']%8<4 else 1;k=p['contact_w_k'] if self.lift[side]<1e-5 else 0.;inv=1/c+1/t['_c'];q=(self.block_t-t['temperature_k'])*(-math.expm1(-h*k*inv))/inv;self.block_t-=q/c;t['temperature_k']+=q/t['_c']
    if running:
     try:self._react(h)
     except ActionRejected as exc:self.phase='failed';self.fault=str(exc)
   if running:
    self.elapsed_s+=h
    if self.elapsed_s>=self.method['knots'][-1]['time_s'] and abs(self._indicated()-self.method['knots'][-1]['temperature_k'])<=1.:self.hold_s+=h
    if self.phase=='running':
     if self.elapsed_s>=self.method['knots'][-1]['time_s'] and self.hold_s>=self.method['hold_s'] and abs(self._indicated()-self.method['knots'][-1]['temperature_k'])<=1.:self.phase='succeeded'
     elif self.elapsed_s>=self.method['timeout_s']-1e-9:self.phase='failed';self.fault='TIMEOUT'
    self.target=self._program(self.elapsed_s)
   for i in range(2):self.lift[i]+=max(-.02*h,min(.02*h,self.lift_target[i]-self.lift[i]))
   self.time_s+=h;remaining-=h;self.revision+=1
   if p and (not self.history or self.time_s-self.history[-1]['time_s']>=1.-1e-9):self.history.append(dict(time_s=self.time_s,block_temperature_k=self.block_t,indicated_temperature_k=self._indicated(),target_k=self.target,heater_w=self.heater_w))
  return self.read()

class ScpDigiprepmsSession:
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
        meta=export_scp_digiprepms_contract_metadata()
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('digestion.measure'))

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])

def export_scp_digiprepms_contract_metadata():
    caps={};slots={}
    for k,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'ScpDigiprepmsSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    immediate={'plant.configure':['plant.configured','thermal.block_temperature_k','thermal.indicated_temperature_k','thermal.target_k'],'method.configure':['method.configured','thermal.target_k'],'sample.configure':['sample.configured','truth.solid_mol','truth.acid_mol'],'power.set':['power.enabled','cycle.phase'],'thermal.set_temperature':['thermal.target_k'],'probe.configure':['probe.mode','probe.position','thermal.indicated_temperature_k'],'rack.set':['rack_left.target_m','rack_right.target_m'],'acquisition.start':['cycle.phase','cycle.fault'],'acquisition.stop':['cycle.phase']}
    advancing=['cycle.phase','cycle.fault','cycle.elapsed_s','cycle.hold_elapsed_s','thermal.target_k','thermal.block_temperature_k','thermal.indicated_temperature_k','thermal.heater_w','truth.solid_mol','truth.dissolved_mol','truth.acid_mol','truth.sensible_energy_j','truth.input_energy_j','truth.loss_energy_j']
    for k,(inp,out,components,meaning,kind,vis,example) in CAPS.items():
        async_=k=='acquisition.start'
        caps[k]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.scp_digiprepms.ScpDigiprepmsSession.invoke',invocation=f'session.invoke(instance_id="digiprep_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if kind=='query' else [dict(description_zh='外置控制器/管材/通风宿主确认，50mL×48容量、单调程序、原数据温区、冷热管架互锁；拒绝不变状态',state_ids=['cycle.phase','plant.configured','sample.configured','power.enabled'],enforced_by='runtime',check='ScpDigiprepmsRuntime.invoke capability-specific guards')],state_effects=[dict(state_id=i,timing='immediate',description_zh='配置/目标或操作状态，不推进时间') for i in immediate.get(k,[])]+([dict(state_id=i,timing='on_step',description_zh='宿主步进显热交换、有限酸/矿物/空白当量账本与程序') for i in advancing] if async_ else [])+([dict(state_id='rack_'+side+'.lift_m',timing='on_step',description_zh='宿主管架速度0.02m/s教学代理') for side in ['left','right']] if k=='rack.set' else []),observes=[],model_effects=dict(mode='none',description_zh='本体没有一体式面板；只有经过独立求值验证的管架绑定才能升格为联动',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='最终目标±1K内累计保温达到hold_s；程序完成不保证完全消解' if async_ else '同步返回；目标不是已达物理状态',completion_state_ids=['cycle.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['48_finite_tubes','two_manual_racks','host_controller_boundary','principal_request_cache'],partial_failure_zh='取消保留全部矿物/酸/空白库存并继续显热冷却；冻结化学账本不是热酸安全状态证明。'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT','UNKNOWN_REFERENCE','REFERENCE_RANGE','PROGRAM_ORDER','TUBE_TEMPERATURE_LIMIT','CYCLE_ALREADY_STARTED','DUPLICATE_POSITION','PROBE_NOT_CONNECTED','HOT_RACK']],example_arguments=copy.deepcopy(example),verification=[],limitations=['宿主教学控制器与手动提架代理；不是原厂固件、机器人或硬件；公开会话不代替文件/进程隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
        if async_:caps[k]['errors'].append(dict(code='TIMEOUT',meaning_zh='超时终止，保留部分效果',side_effects='partial'))
    mechanism=dict(component_ids=['block','heater','internal_probe','port_controller','port_probe','rack_left','rack_right'],state_ids=list(slots),meaning_zh='历史配置功率/升温时间热代理，48管显热负载，LLNL矿物温变logK，宿主有限酸与空白代理',implementation='lumped',equations=['Historical proxy Cb=-UA*2100/log(1-UA*(368.15-293.15)/925); UA=2W/K assumed, not current model calibration','Cb*dTb/dt=Pheater-UA*(Tb-Ta)-sum(Ki*(Tb-Ti)); Ci=Vreference*rho_host*Cp_host','Exact block source/loss exponential and sequential energy-conserving block/tube pair exchange; dt<=0.5s; no latent/reaction heat','Target is piecewise-linear program; final hold counts only while selected sensor within1K; start/stop do not advance time','log10K=A1+A2*T+A3/T+A4*log10(T)+A5/T^2+A6*T^2 within each LLNL source interval','logQ=logQ0_host+nu_H*log10(H0/H); fixed product-activity proxy, NOT aqueous speciation','dn=min(nsolid*(1-exp(-k298_host*exp(Ea_host/R*(1/298.15-1/T))*max(0,1-Q/K)*dt)),H/nu_H)','acid evaporation and blank release use finite first-order host rates scaled by exp((T-298.15)/30); removed equivalents enter explicit ledgers','nsolid+ndissolved=n0; Hremaining+Hconsumed+Hexhaust=H0; blank_remaining+blank_dissolved=blank0','Only step advances physics, including manual rack position at0.02m/s; cold interlock323.15K is simulation guard, not safe handling instruction'],parameters={},source_ids=['manufacturer_primary','manufacturer_old_en','llnl_phases','simulation_assumption'],limitations=['Current power,mass,heatup unknown;925W35min historical reference is not current configuration calibration.','296 mineral reaction references are not measured digestion recoveries or a validated acid procedure. Some phases polymorphs; preserve exact reaction/name.','No complete aqueous speciation, variable activities, precipitation, redox, aerosol, chemistry heat, latent heat, solvent loss or elemental/water balance.','Energy closes only the deliberately limited sensible-heat model; fixed reference-volume density/Cp are host coefficients.','Mineral equivalents,acid equivalents and blank reservoir conserve separately; do not call this total chemical mass/volume conservation.','Temperature uniformity±1K is manufacturer nominal information, not derived from a measured48-position field.','Program completion is not complete dissolution; all reaction,volatilization,blank kinetics explicitly synthetic.','No integral display/buttons,optional controller/probe remain host boundary; physical probe/cable or robot attachment not built.','Cancellation freezes chemistry while sensible cooling continues; not validated safe-state or post-stop acid behavior.','No factory UI,hardware,safety approval or file/process isolation; Matterixack differs from native operation completion.'])
    spec=importlib.util.spec_from_file_location('_digiprep_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'digestion':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'digestion':mechanism})

"""PT100+DR100 finite rotating-sector divider; not DEM or factory firmware."""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.pccell_ed64002 import validate
ROOT=Path(__file__).resolve().parents[2];SLUG='retsch_pt100';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-042';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/retsch_pt100_particle_library/templates.json'
OMEGA=110*math.tau/60;TIMES=[60.,180.,300.,600.,1200.,1800.,2400.,3000.,3600.];N=10
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled'])
def array(s,n=None):return dict(type='array',items=s,minItems=n or 0,**({'maxItems':n} if n else {}))
INDEX=dict(type='integer',minimum=0,maximum=99,**{'x-unit':'1'})
PLANT=obj({'acceleration_rad_s2':number('rad/s^2',.1,100),'flow_at_reference_m3_s':number('m^3/s',1e-10,5/60000),'reference_intensity':dict(type='integer',minimum=1,maximum=99,**{'x-unit':'1'}),'gap_velocity_m_s':number('m/s',.001,.1),'vibration_amplitude_m':number('m',0,.001),'vibration_frequency_hz':dict(type='number',enum=[50,60],**{'x-unit':'Hz'}),'free_flowing_reviewed':BOOL,'coefficient_evidence':TEXT})
PLANT_EXAMPLE=dict(acceleration_rad_s2=6.,flow_at_reference_m3_s=1e-5,reference_intensity=60,gap_velocity_m_s=.02,vibration_amplitude_m=.0001,vibration_frequency_hz=50,free_flowing_reviewed=True,coefficient_evidence='host_linear_index_calibration_and_motion_assumptions_not_factory')
METHOD=obj({'duration_s':dict(type='number',enum=TIMES,**{'x-unit':'s'}),'intensity':INDEX})
METHOD_EXAMPLE=dict(duration_s=60.,intensity=60)
PORTION=obj({'substance_id':TEXT,'mass_kg':number('kg',1e-10,100),'packing_fraction':number('1',.05,1),'maximum_grain_m':number('m',1e-6,.01),'capture_probability':number('1',0,1)})
SAMPLE=obj({'sample_id':TEXT,'portions':dict(type='array',items=PORTION,minItems=1,maxItems=16),'feed_order_evidence':TEXT})
def example_sample():return dict(sample_id='quartz_then_aragonite',portions=[dict(substance_id=s,mass_kg=m,packing_fraction=.55,maximum_grain_m=.001,capture_probability=.99) for s,m in [('quartz',.3),('aragonite',.2)]],feed_order_evidence='host_FIFO_portions_not_predicted_segregation')
MEASURE=obj({'phase':PHASE,'mode':TEXT,'elapsed_s':number('s',0),'remaining_s':number('s',0),'rotation_rad_s':number('rad/s',0,OMEGA),'intensity':INDEX,'feeder_running':BOOL,'evidence':TEXT,'template_source':TEXT})
PRODUCT=obj({'sample_id':TEXT,'substance_ids':array(TEXT),'recipient_mass_kg':array(array(number('kg',0)),10),'hopper_mass_kg':array(number('kg',0)),'lost_mass_kg':array(number('kg',0)),'overflow_mass_kg':array(number('kg',0)),'collected_mass_kg':array(number('kg',0)),'initial_mass_kg':array(number('kg',0)),'recipient_volume_m3':array(number('m^3',0,.0005),10),'evidence':TEXT,'template_source':TEXT})
CAPS={
 'plant.configure':(PLANT,obj({'configured':BOOL}),['housing','feeder_housing'],'宿主送料标定、启停响应与自由流动物料审查','configuration','evaluator',PLANT_EXAMPLE),
 'method.configure':(METHOD,obj({'configured':BOOL}),['panel','rotor'],'厂家离散定时与DR100无量纲强度设置','configuration','agent',METHOD_EXAMPLE),
 'sample.configure':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['feed_hopper','rotor'],'宿主装入3.5L以内有限物料；非机器人装料','configuration','evaluator',example_sample()),
 'sample.refill':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['feed_hopper'],'停转后宿主补料，总批次不超过5L、料斗不超过3.5L','configuration','evaluator',example_sample()),
 'mount.configure':(obj({'all_bottles_attached':BOOL}),obj({'all_bottles_attached':BOOL}),['rotor','bottle_0'],'宿主确认10个瓶已接牢，非力/接触传感器','configuration','evaluator',dict(all_bottles_attached=True)),
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['main_switch','housing'],'PT100主电源；断电取消过程并停料','device_command','agent',dict(enabled=True)),
 'feeder.set_power':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['feeder_switch','feeder_housing'],'DR100电源，标准同步模式；不带错误恢复固件','device_command','agent',dict(enabled=True)),
 'hopper.set_gap':(obj({'gap_m':number('m',.003,.04)}),obj({'gap_m':number('m',.003,.04)}),['feed_hopper'],'停转手工升降间隙目标，step推进；非自动电机','device_command','agent',dict(gap_m=.03)),
 'feeder.set_intensity':(obj({'intensity':INDEX}),obj({'intensity':INDEX}),['knob_intensity','feeder_housing'],'无量纲强度，不伪称L/min传感器','device_command','agent',dict(intensity=50)),
 'timer.adjust':(obj({'direction':dict(type='integer',enum=[-1,1],**{'x-unit':'1'})}),obj({'duration_s':dict(type='number',enum=TIMES,**{'x-unit':'s'})}),['button_time_minus','button_time_plus'],'在原厂离散分钟档位中单步切换；连续co未实现','device_command','agent',dict(direction=1)),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['button_start','rotor'],'启动或恢复同一异步分样作业；不重置已接料','device_command','agent',{}),
 'acquisition.pause':(obj({}),obj({'mode':TEXT}),['button_stop','rotor'],'暂停冻结计时、立刻停料，分样头有限减速','device_command','agent',{}),
 'acquisition.resume':(obj({}),obj({'mode':TEXT}),['button_start','rotor'],'恢复暂停，达到110rpm后继续送料','device_command','agent',{}),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['button_stop','rotor'],'取消保留全部物料，有限减速','device_command','agent',{}),
 'acquisition.press_stop':(obj({}),obj({'phase':PHASE,'mode':TEXT}),['button_stop'],'物理STOP第一次暂停，第二次取消进入待机','device_command','agent',{}),
 'dividing.measure':(obj({}),MEASURE,['display_time','display_intensity','rotor'],'面板计时/强度和理想转速；不返回隐藏分样质量','query','agent',{}),
 'product.read':(obj({}),PRODUCT,['bottle_0','rotor'],'评价器逐物质逐瓶有限质量账本','query','evaluator',{}),
 'product.collect':(obj({'recipient_index':dict(type='integer',minimum=0,maximum=9,**{'x-unit':'1'}),'fraction':number('1',0,1),'recipient_id':TEXT}),obj({'transferred_kg':number('kg',0),'recipient_id':TEXT}),['bottle_0','rotor'],'停转后有限物料转移，非机器人拆瓶或倾倒','device_command','evaluator',dict(recipient_index=0,fraction=.5,recipient_id='host_recipient')),
 'weighing.record':(obj({'recipient_mass_kg':array(number('kg',0),10),'original_mass_kg':number('kg',1e-10),'measurement_source':TEXT}),obj({'recorded':BOOL}),['panel'],'导入外部天平实测/夹具数据，不由隐藏真值生成','configuration','evaluator',dict(recipient_mass_kg=[.01]*10,original_mass_kg=.5,measurement_source='external_fixture_not_integral_sensor')),
 'weighing.read':(obj({}),obj({'available':BOOL,'recipient_mass_kg':array(number('kg',0)),'recovery_fraction':dict(anyOf=[number('1',0),dict(type='null')]),'measurement_source':TEXT,'evidence':TEXT}),['panel'],'只读宿主导入外部称量；无数据则明确缺测','query','agent',{}),
 'reference.lookup':(obj({'substance_id':TEXT}),obj({'density_kg_m3':number('kg/m^3',0),'molar_volume_m3_mol':number('m^3/mol',0),'source':TEXT,'evidence':TEXT,'template_source':TEXT}),['panel'],'真实Vm推导骨架密度，不是PT100准确度模板','query','agent',dict(substance_id='quartz')),
}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,zh,o='derived',v='agent',c='panel',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=zh
for n in ['plant','method','sample']:slot(n+'.configured',BOOL,'1','宿主'+n+'配置已存在','setpoint')
for k,c,cap,zh in [('power.enabled','main_switch','power.set','PT100电源'),('feeder.powered','feeder_switch','feeder.set_power','DR100电源')]:slot(k,BOOL,'1',zh,'setpoint',c=c,w=(cap,'enabled'))
slot('mount.ready',BOOL,'1','宿主确认10瓶接牢，非传感器','setpoint',c='rotor')
slot('cycle.phase',PHASE,'1','异步分样状态');slot('cycle.mode',TEXT,'1','加速/分样、暂停、收尾减速或待机')
for k,zh in [('elapsed_s','总定时含加速不含手动暂停'),('remaining_s','剩余分样定时')]:slot('cycle.'+k,number('s',0),'s',zh)
slot('rotor.angle_rad',number('rad',0,math.tau),'rad','分样头场景角度，物料按连续未折返角度积分','scene',c='rotor')
slot('rotor.speed_rad_s',number('rad/s',0,OMEGA),'rad/s','理想分样头角速度；非校准传感器',c='rotor')
slot('feeder.intensity',INDEX,'1','无量纲强度显示，不是体积流量','setpoint',c='knob_intensity',w=('feeder.set_intensity','intensity'))
slot('feeder.running',BOOL,'1','标准同步模式名义转速联锁后的教学送料状态',c='feeder_chute')
slot('feeder.displacement_m',number('m',-.001,.001),'m','50/60Hz电磁槽微位移教学代理','scene',c='feeder_chute')
slot('hopper.gap_m',number('m',.003,.04),'m','料斗出口距槽底间隙','scene',c='feed_hopper')
slot('hopper.target_gap_m',number('m',.003,.04),'m','手工间隙目标','setpoint',c='feed_hopper',w=('hopper.set_gap','gap_m'))
slot('panel.time_min',number('1',0,60),'1','剩余分钟教学显示')
slot('truth.recipient_mass_kg',array(array(number('kg',0)),10),'kg','10瓶逐段物质质量','process_truth','evaluator',c='rotor')
for k,u,zh in [('hopper_mass_kg','kg','料斗逐段剩余质量'),('lost_mass_kg','kg','未捕集质量'),('overflow_mass_kg','kg','超过500mL接收瓶的溢料质量'),('collected_mass_kg','kg','已被宿主收走质量'),('initial_mass_kg','kg','累计投入质量')]:slot('truth.'+k,array(number(u,0)),u,zh,'process_truth','evaluator',c='rotor')
slot('truth.recipient_volume_m3',array(number('m^3',0,.0005),10),'m^3','骨架密度与宿主堆积率推导的瓶内体积','process_truth','evaluator',c='rotor')

class RetschPt100Runtime:
 def __init__(self,instance_id='pt100_01'):
  self.instance_id=instance_id;self.plant=self.method=self.sample=None;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.powered=self.feeder_powered=self.mounted=False;self.phase=self.mode='idle';self.time_s=self.elapsed_s=self.angle=self.speed=self.vibration_phase=self.vibration=0.;self.gap=self.gap_target=.03;self.portions=[];self.initial=[];self.remaining=[];self.lost=[];self.overflow=[];self.collected=[];self.ledger=[[] for _ in range(N)];self.bulk_density=[];self.total_batch_volume=0.;self.weighings=None;self.revision=0;self.operation_id='';self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility):
  if visibility not in ['agent','debug','evaluator'] or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return RetschPt100Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _remaining_time(self):return max(0.,self.method['duration_s']-self.elapsed_s) if self.method else 0.
 def _feeding(self):return bool(self.phase=='running' and self.mode=='dividing' and self.powered and self.feeder_powered and self.speed>=OMEGA-1e-10 and self.method) # no invented empty-hopper sensor
 def _volumes(self):return [sum(m/rho for m,rho in zip(row,self.bulk_density)) for row in self.ledger]
 def _values(self):return {**{n+'.configured':bool(getattr(self,n)) for n in ['plant','method','sample']},'power.enabled':self.powered,'feeder.powered':self.feeder_powered,'mount.ready':self.mounted,'cycle.phase':self.phase,'cycle.mode':self.mode,'cycle.elapsed_s':self.elapsed_s,'cycle.remaining_s':self._remaining_time(),'rotor.angle_rad':self.angle%math.tau,'rotor.speed_rad_s':self.speed,'feeder.intensity':self.method['intensity'] if self.method else 0,'feeder.running':self._feeding(),'feeder.displacement_m':self.vibration,'hopper.gap_m':self.gap,'hopper.target_gap_m':self.gap_target,'panel.time_min':self._remaining_time()/60,'truth.recipient_mass_kg':self.ledger,'truth.hopper_mass_kg':self.remaining,'truth.lost_mass_kg':self.lost,'truth.overflow_mass_kg':self.overflow,'truth.collected_mass_kg':self.collected,'truth.initial_mass_kg':self.initial,'truth.recipient_volume_m3':self._volumes()}
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ['debug','evaluator']})
 def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k!='templates'})
 def _can_start(self):return all([self.plant,self.method,self.sample,self.powered,self.feeder_powered,self.mounted]) and abs(self.gap-self.gap_target)<1e-10 and self.gap+1e-12>=3*max(p['maximum_grain_m'] for p in self.portions)
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if (self.speed>1e-10 or (self.phase=='running' and self.mode!='paused')) and capability in ['plant.configure','method.configure','sample.configure','sample.refill','mount.configure','hopper.set_gap','timer.adjust','product.collect','weighing.record']:raise ActionRejected('BUSY')
  if capability=='plant.configure':
   if self.plant or not a['free_flowing_reviewed']:raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='method.configure':
   if self.elapsed_s or not self.plant:raise ActionRejected('PRECONDITION_FAILED')
   self.method=copy.deepcopy(a);out=dict(configured=True)
  elif capability in ['sample.configure','sample.refill']:
   if not self.plant or (capability=='sample.configure' and self.sample) or (capability=='sample.refill' and (not self.sample or a['sample_id']!=self.sample['sample_id'])):raise ActionRejected('PRECONDITION_FAILED')
   if len(self.portions)+len(a['portions'])>32:raise ActionRejected('INVALID_ARGUMENT')
   if any(p['substance_id'] not in self.templates for p in a['portions']):raise ActionRejected('UNKNOWN_REFERENCE')
   rho=[self.templates[p['substance_id']]['value']*p['packing_fraction'] for p in a['portions']];vol=sum(p['mass_kg']/d for p,d in zip(a['portions'],rho));in_hopper=sum(m/d for m,d in zip(self.remaining,self.bulk_density))
   if vol+in_hopper>.0035+1e-12 or self.total_batch_volume+vol>.005+1e-12:raise ActionRejected('CAPACITY_LIMIT')
   if not self.sample:self.sample=copy.deepcopy(a)
   self.portions+=copy.deepcopy(a['portions']);self.bulk_density+=rho;mass=[p['mass_kg'] for p in a['portions']];self.initial+=mass;self.remaining+=mass;self.lost+=[0.]*len(mass);self.overflow+=[0.]*len(mass);self.collected+=[0.]*len(mass)
   for row in self.ledger:row.extend([0.]*len(mass))
   self.total_batch_volume+=vol;out=dict(configured=True,attachment_event='host_finite_loading_not_robot')
  elif capability=='mount.configure':self.mounted=a['all_bottles_attached'];out=a
  elif capability=='power.set':
   self.powered=a['enabled']
   if not self.powered and self.phase=='running':self.phase='cancelled';self.mode='stopping'
   out=a
  elif capability=='feeder.set_power':self.feeder_powered=a['enabled'];out=a
  elif capability=='hopper.set_gap':
   if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
   self.gap_target=a['gap_m'];out=a
  elif capability=='feeder.set_intensity':
   if not self.method:raise ActionRejected('PRECONDITION_FAILED')
   self.method['intensity']=a['intensity'];out=a
  elif capability=='timer.adjust':
   if not self.method or self.elapsed_s or self.phase=='running':raise ActionRejected('PRECONDITION_FAILED')
   i=min(len(TIMES)-1,max(0,TIMES.index(self.method['duration_s'])+a['direction']));self.method['duration_s']=TIMES[i];out=dict(duration_s=TIMES[i])
  elif capability in ['acquisition.start','acquisition.resume']:
   resume=self.phase=='running' and self.mode=='paused'
   if not self._can_start() or (not resume and (self.phase!='idle' or capability=='acquisition.resume')):raise ActionRejected('PRECONDITION_FAILED')
   if not resume:self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);self.phase='running'
   self.mode='dividing';out=dict(phase=self.phase,operation_id=self.operation_id) if capability=='acquisition.start' else dict(mode=self.mode)
  elif capability in ['acquisition.pause','acquisition.stop','acquisition.press_stop']:
   if self.phase!='running' or (capability=='acquisition.pause' and self.mode!='dividing'):raise ActionRejected('PRECONDITION_FAILED')
   pause=capability=='acquisition.pause' or (capability=='acquisition.press_stop' and self.mode=='dividing')
   if pause:self.mode='paused'
   else:self.phase='cancelled';self.mode='stopping'
   out=dict(mode=self.mode) if capability=='acquisition.pause' else dict(phase=self.phase,**(dict(mode=self.mode) if capability=='acquisition.press_stop' else {}))
  elif capability=='dividing.measure':return dict(phase=self.phase,mode=self.mode,elapsed_s=self.elapsed_s,remaining_s=self._remaining_time(),rotation_rad_s=self.speed,intensity=self.method['intensity'] if self.method else 0,feeder_running=self._feeding(),evidence='idealized_panel_and_rotation_not_measured_sampling_accuracy',template_source=TEMPLATE_REL)
  elif capability=='product.read':return dict(sample_id=self.sample['sample_id'] if self.sample else '',substance_ids=[p['substance_id'] for p in self.portions],recipient_mass_kg=copy.deepcopy(self.ledger),hopper_mass_kg=self.remaining.copy(),lost_mass_kg=self.lost.copy(),overflow_mass_kg=self.overflow.copy(),collected_mass_kg=self.collected.copy(),initial_mass_kg=self.initial.copy(),recipient_volume_m3=self._volumes(),evidence='host_sector_model_with_real_density_not_measured_subsample_composition',template_source=TEMPLATE_REL)
  elif capability=='product.collect':
   if not self.sample or self.phase=='running':raise ActionRejected('PRECONDITION_FAILED')
   row=self.ledger[a['recipient_index']];moved=[m*a['fraction'] for m in row]
   for j,m in enumerate(moved):row[j]-=m;self.collected[j]+=m
   out=dict(transferred_kg=sum(moved),recipient_id=a['recipient_id'])
  elif capability=='weighing.record':self.weighings=copy.deepcopy(a);out=dict(recorded=True)
  elif capability=='weighing.read':
   w=self.weighings;return dict(available=w is not None,recipient_mass_kg=w['recipient_mass_kg'].copy() if w else [],recovery_fraction=sum(w['recipient_mass_kg'])/w['original_mass_kg'] if w else None,measurement_source=w['measurement_source'] if w else '',evidence='externally_supplied_not_integral_balance')
  elif capability=='reference.lookup':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   r=self.templates[a['substance_id']];return dict(density_kg_m3=r['value'],molar_volume_m3_mol=r['molar_volume_cm3_mol']*1e-6,source=r['source'],evidence=r['evidence'],template_source=TEMPLATE_REL)
  self.revision+=1;return copy.deepcopy(out)
 @staticmethod
 def sector_arc(theta,sector):
  # Cumulative angular length of this equal window, exact across full rotations.
  width=math.tau/N;k=math.floor(theta/math.tau);rem=theta-k*math.tau
  return k*width+min(width,max(0.,rem-sector*width))
 def _feed(self,h,theta):
  q=self.plant['flow_at_reference_m3_s']*self.method['intensity']/self.plant['reference_intensity'];cursor=0.
  if q<=0:return # zero throughput does not assert zero electromagnetic motion
  for j,m in enumerate(self.remaining):
   if m<=0:continue
   exhaustion=m/(self.bulk_density[j]*q);duration=min(h-cursor,exhaustion)
   if duration<=0:break
   raw_mass=m if duration==exhaustion else min(m,duration*q*self.bulk_density[j]);start=(theta+OMEGA*cursor+math.pi/2)%math.tau;end=start+OMEGA*duration;p=self.portions[j]['capture_probability'];self.remaining[j]-=raw_mass;self.lost[j]+=raw_mass*(1-p)
   arcs=[max(0.,self.sector_arc(end,k)-self.sector_arc(start,k)) for k in range(N)];norm=sum(arcs)
   if norm==0.:arcs[int(start/(math.tau/N))%N]=1.;norm=1. # point-limit only below floating angular resolution
   # Normalization only corrects floating roundoff of exact angular partition.
   for k,arc in enumerate(arcs):
    i=(-k)%N;candidate=raw_mass*p*arc/norm;available=max(0.,.0005-sum(x/d for x,d in zip(self.ledger[i],self.bulk_density)));accepted=min(candidate,available*self.bulk_density[j]);self.ledger[i][j]+=accepted;self.overflow[j]+=candidate-accepted
   cursor+=duration
   if cursor>=h-1e-14:break
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<0 or dt_s>3600:raise ActionRejected('INVALID_ARGUMENT')
  left=float(dt_s)
  while left>1e-12:
   h=min(.01,left);active=self.phase=='running' and self.mode=='dividing';target=OMEGA if active else 0.;acc=self.plant['acceleration_rad_s2'] if self.plant else 6.
   if active:h=min(h,self._remaining_time())
   reach=abs(target-self.speed)/acc
   if reach>1e-12:h=min(h,reach)
   if h<=1e-12:
    if active:self.mode='finishing'
    continue
   old_speed=self.speed;old_angle=self.angle
   self.speed=min(target,self.speed+acc*h) if target>=self.speed else max(target,self.speed-acc*h)
   if self._feeding() and old_speed>=OMEGA-1e-10:self._feed(h,old_angle)
   self.angle+=(old_speed+self.speed)*h/2
   if active:
    self.elapsed_s+=h
    if self._remaining_time()<1e-10:self.elapsed_s=self.method['duration_s'];self.mode='finishing'
   if self.speed<1e-12:
    self.speed=0.
    if self.mode=='finishing':self.phase='succeeded';self.mode='idle'
    elif self.mode=='stopping':self.mode='idle'
   gap_step=(self.plant['gap_velocity_m_s'] if self.plant else .02)*h;self.gap+=min(gap_step,max(-gap_step,self.gap_target-self.gap))
   if self._feeding():self.vibration_phase+=math.tau*self.plant['vibration_frequency_hz']*h;self.vibration=self.plant['vibration_amplitude_m']*math.sin(self.vibration_phase)
   else:self.vibration=0.
   self.time_s+=h;left-=h;self.revision+=1

class RetschPt100Session:
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
        meta=export_retsch_pt100_contract_metadata()
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
        if op: h._owners.setdefault(op,self._principal)
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('dividing.measure'))

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])

def export_retsch_pt100_contract_metadata():
 caps={};slots={}
 for k,(schema,unit,origin,visibility,components,write) in SLOTS.items():
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'RetschPt100Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
 immediate={'plant.configure':['plant.configured'],'method.configure':['method.configured','feeder.intensity','panel.time_min','cycle.remaining_s'],'sample.configure':['sample.configured']+[k for k in slots if k.startswith('truth.')],'sample.refill':[k for k in slots if k.startswith('truth.')],'mount.configure':['mount.ready'],'power.set':['power.enabled','cycle.phase','cycle.mode'],'feeder.set_power':['feeder.powered'],'hopper.set_gap':['hopper.target_gap_m'],'feeder.set_intensity':['feeder.intensity'],'timer.adjust':['cycle.remaining_s','panel.time_min'],'acquisition.start':['cycle.phase','cycle.mode'],'acquisition.pause':['cycle.mode'],'acquisition.resume':['cycle.mode'],'acquisition.stop':['cycle.phase','cycle.mode'],'acquisition.press_stop':['cycle.phase','cycle.mode'],'product.collect':['truth.recipient_mass_kg','truth.collected_mass_kg','truth.recipient_volume_m3']}
 advancing=['cycle.phase','cycle.mode','cycle.elapsed_s','cycle.remaining_s','panel.time_min','rotor.angle_rad','rotor.speed_rad_s','feeder.running','feeder.displacement_m']+[k for k in slots if k.startswith('truth.')]
 for k,(inp,out,components,meaning,kind,vis,example) in CAPS.items():
  async_=k=='acquisition.start'
  caps[k]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.retsch_pt100.RetschPt100Session.invoke',invocation=f'session.invoke(instance_id="pt100_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if kind=='query' else [dict(description_zh='固定10瓶、有限3.5L料斗/5L批量、粒径/间隙、停转配置与宿主审查；拒绝无副作用',state_ids=['cycle.phase','sample.configured','power.enabled','feeder.powered','mount.ready','hopper.gap_m'],enforced_by='runtime',check='RetschPt100Runtime.invoke capability-specific guards')],state_effects=[dict(state_id=i,timing='immediate',description_zh='同步配置/目标/有限物料转移') for i in immediate.get(k,[])]+([dict(state_id=i,timing='on_step',description_zh='有限加减速、计时、逐角度接料与质量守恒') for i in advancing] if async_ else [])+([dict(state_id='hopper.gap_m',timing='on_step',description_zh='宿主手工升降速度代理')] if k=='hopper.set_gap' else []),observes=[],model_effects=dict(mode='none',description_zh='仅实际场景求值通过才升格；不是机器人操作',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='定时完成且分样头停转，不保证样品清空或统计代表性' if async_ else '配置/目标同步返回；物理只有step推进',completion_state_ids=['cycle.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['ten_500ml_bottles','finite_multispecies_ledger','external_weighing','principal_request_cache'],partial_failure_zh='暂停/取消保留已接料、剩余、损失与溢料，不能回滚。'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT','UNKNOWN_REFERENCE','CAPACITY_LIMIT']],example_arguments=copy.deepcopy(example),verification=[],limitations=['非厂家固件、DEM、统计代表性保证、机器人或真实硬件控制；会话权限不替代文件系统隔离。'],source_ids=['manufacturer_primary','manufacturer_manual','feeder_manual','simulation_assumption'])
 mechanism=dict(component_ids=['rotor','bottle_0','feed_hopper','feeder_chute','panel'],state_ids=list(slots),meaning_zh='固定110rpm分样头逐口接收连续FIFO送料，真实密度驱动容量账本',implementation='lumped',equations=['omega_target=110*2pi/60 rad/s; finite host acceleration; feed only at nominal speed','q=host_reference_flow*intensity/reference_intensity; not a manufacturer calibration or flow sensor','Each portion is uniform and FIFO; exact angular window integral partitions material among10rotating outlets','For window k: C_k(theta)=floor(theta/2pi)*(2pi/10)+clip(theta mod2pi-k*2pi/10,0,2pi/10)','m_capture=dm*host_capture_probability; uncaptured→loss; volume beyond500mL→overflow','rho_solid=M/Vm at25C; rho_bulk=rho_solid*host_packing; bottle_volume=sum(m/rho_bulk)','For every portion: hopper+sum10bottles+loss+overflow+collected=total_loaded','3.5L instantaneous hopper;5L cumulative batch; refill only after rotation stopped','Time counts acceleration and constant rotation, excludes manual pause; finish after finite deceleration','Public weighing is only externally supplied data; hidden ledger is evaluator/debug'],parameters={},source_ids=['manufacturer_primary','manufacturer_manual','feeder_primary','feeder_manual','usgs_sit','phreeqc_definition','simulation_assumption'],limitations=['Fixed10outletGL55+500mL;6/8heads,30/100/250mL adapters not implemented.','183 independent real solid Vm-derived densities; no183 measured sampling curves or calibrated bulk densities.','FIFO feed order, capture probability, flow/index relation and motor transient are explicit host assumptions.','No DEM, granular contact, self-generated segregation, flight-time distribution, tube holdup, electrostatics or particle fragmentation.','No statistical representativity certification; finite/end-phase composition may differ among bottles.','No integral balance, flow meter or mounting/gap sensor; host guards are not hardware safety controls.','No continuousco, exact LED/error firmware or >5s speed-fault recovery.','All local geometry/masses/inertias uncertain;33.5kg is whole assembly reference, not per-component data.'])
 spec=importlib.util.spec_from_file_location('_pt100_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'dividing':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE)})
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'dividing':mechanism})

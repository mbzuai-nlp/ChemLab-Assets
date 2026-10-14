"""PILODIST104: finite batch equilibrium stages, ideal VLE, explicit host coefficients.

Constant molar overflow and prescribed boilup are engineering assumptions, NOT
an energy balance, calibrated HETP, DCD4001 firmware, or a safety controller.
"""
from __future__ import annotations
import copy, importlib.util, json, math
from pathlib import Path
import numpy as np
from asset_runtime.mt_hx204 import ActionRejected, number, obj, BOOL, TEXT
from asset_runtime.pccell_ed64002 import validate
ROOT=Path(__file__).resolve().parents[2]; SLUG='pilodist_pilodist104'; ASSET_TYPE_ID='instrument.'+SLUG; CATALOG_ID='CHEM-037'; PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/pilodist_pilodist104_vle_library/templates.json'
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
def integer(lo,hi):return dict(type='integer',minimum=lo,maximum=hi,**{'x-unit':'1'})
def nullable(x):return {'anyOf':[x,{'type':'null'}]}
PLANT=obj({'external_vacuum_confirmed':BOOL,'external_cooling_confirmed':BOOL,'vent_boundary_confirmed':BOOL,'ideal_mixture_reviewed':BOOL,'pressure_tau_s':number('s',.1,1000),'bath_tau_s':number('s',.1,10000),'reflux_tau_s':number('s',.1,1000),'initial_temperature_k':number('K',293.15,523.15),'boilup_coefficient_mol_s_k':number('mol/(s*K)',1e-8,.1),'max_boilup_mol_s':number('mol/s',1e-6,1),'selector_speed_rad_s':number('rad/s',.01,2),'coefficient_evidence':TEXT})
PLANT_EXAMPLE=dict(external_vacuum_confirmed=True,external_cooling_confirmed=True,vent_boundary_confirmed=True,ideal_mixture_reviewed=True,pressure_tau_s=1.,bath_tau_s=3.,reflux_tau_s=2.,initial_temperature_k=303.15,boilup_coefficient_mol_s_k=.0005,max_boilup_mol_s=.02,selector_speed_rad_s=.5,coefficient_evidence='explicit_host_assumptions_not_PILODIST_calibration')
COLUMN=obj({'column_type':dict(type='string',enum=['concentric_tube','wire_gauze']),'effective_stages':integer(1,90),'stage_holdup_mol':number('mol',1e-5,1),'condenser_holdup_mol':number('mol',1e-5,1),'configuration_reviewed':BOOL})
COLUMN_EXAMPLE=dict(column_type='concentric_tube',effective_stages=3,stage_holdup_mol=.02,condenser_holdup_mol=.05,configuration_reviewed=True)
METHOD=obj({'bath_target_k':number('K',293.15,523.15),'pressure_pa':number('Pa',10,100000),'reflux_ratio':number('1',0,1000),'total_reflux':BOOL,'cut_volume_m3':number('m^3',1e-7,.001),'duration_s':number('s',.1,86400),'timeout_s':number('s',.1,86400),'min_boiler_volume_m3':number('m^3',1e-7,.001)})
METHOD_EXAMPLE=dict(bath_target_k=360.,pressure_pa=30000.,reflux_ratio=2.,total_reflux=False,cut_volume_m3=.0001,duration_s=120.,timeout_s=150.,min_boiler_volume_m3=.00005)
SPECIES=obj({'substance_id':TEXT,'fit_id':TEXT,'amount_mol':number('mol',1e-10,1000),'reference_molar_volume_m3_mol':number('m^3/mol',1e-6,.01)})
SAMPLE=obj({'sample_id':TEXT,'species':dict(type='array',items=SPECIES,minItems=1,maxItems=4),'reference_temperature_k':number('K',293.15,523.15),'prewetted_initialization_confirmed':BOOL})
def example_sample():return dict(sample_id='ideal_hexane_heptane',species=[dict(substance_id='hexane',fit_id='0',amount_mol=4.,reference_molar_volume_m3_mol=.00013),dict(substance_id='heptane',fit_id='1',amount_mol=4.,reference_molar_volume_m3_mol=.000146)],reference_temperature_k=303.15,prewetted_initialization_confirmed=True)
MEASURE=obj({'phase':PHASE,'fault':TEXT,'bath_temperature_k':number('K',293.15,523.15),'boiler_temperature_k':number('K',293.15,523.15),'top_temperature_k':number('K',293.15,523.15),'pressure_pa':number('Pa',10,101325),'reflux_fraction':number('1',0,1),'boilup_mol_s':number('mol/s',0),'receiver_index':integer(0,5),'evidence':TEXT,'template_source':TEXT})
PRODUCT_ROW=obj({'substance_id':TEXT,'initial_mol':number('mol',0),'boiler_mol':number('mol',0),'column_mol':number('mol',0),'condenser_mol':number('mol',0),'fractions_mol':dict(type='array',items=number('mol',0),minItems=6,maxItems=6)})
POINT=obj({'time_s':number('s',0),'boiler_temperature_k':number('K',293.15,523.15),'top_temperature_k':number('K',293.15,523.15),'pressure_pa':number('Pa',10,101325),'receiver_index':integer(0,5)})
CAPS={
 'plant.configure':(PLANT,obj({'configured':BOOL}),['port_vacuum','port_cooling_in','port_gas_vent'],'宿主声明外部真空/冷却/通气与未标定系数','configuration','evaluator',PLANT_EXAMPLE),
 'column.configure':(COLUMN,obj({'configured':BOOL}),['column_concentric','column_wiregauze'],'配置一种DN25柱与显式有效级数/持液量；不是机器人换柱','configuration','evaluator',COLUMN_EXAMPLE),
 'method.configure':(METHOD,obj({'configured':BOOL}),['pc_screen','reflux_head'],'设置加热、压力、回流、分段收集；真空目标运行前固定','configuration','agent',METHOD_EXAMPLE),
 'sample.configure':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['flask'],'初始化100–4000mL混合物并分配有限预润湿持液；不凭空加料','configuration','evaluator',example_sample()),
 'bath.set_engaged':(obj({'engaged':BOOL}),obj({'engaged':BOOL}),['bath_lift'],'宿主手动升降架目标；实际运动仅step，非机器自驱动','configuration','evaluator',dict(engaged=True)),
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['button_power'],'虚拟供电；断电取消但保留分离结果','device_command','agent',dict(enabled=True)),
 'temperature.set':(obj({'temperature_k':number('K',293.15,523.15)}),obj({'temperature_k':number('K',293.15,523.15)}),['pc_screen'],'只改浴温目标，不瞬时改变温度','device_command','agent',dict(temperature_k=360.)),
 'reflux.set':(obj({'ratio':number('1',0,1000),'total_reflux':BOOL}),obj({'ratio':number('1',0,1000),'total_reflux':BOOL}),['reflux_plunger'],'回流目标经一阶响应，柱持液使组分延迟','device_command','agent',dict(ratio=4.,total_reflux=False)),
 'fraction.select':(obj({'index':integer(0,5)}),obj({'index':integer(0,5)}),['collector_selector'],'选择接收瓶；路由头运动期间全回流，六瓶固定','device_command','agent',dict(index=1)),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['flask','column_concentric','column_wiregauze'],'开启宿主分馏作业；read不推进物理','device_command','agent',{}),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['reflux_head'],'取消后各容器库存保留；停机不虚构排空柱','device_command','agent',{}),
 'distillation.measure':(obj({}),MEASURE,['display_digits'],'理想温压观测；无噪声/校准传感器模型','query','agent',{}),
 'product.read':(obj({}),obj({'records':dict(type='array',items=PRODUCT_ROW),'evidence':TEXT,'template_source':TEXT}),['flask','collector_selector'],'各馏分/釜残液/柱持液逐物质真值；仅evaluator','query','evaluator',{}),
 'history.read':(obj({}),obj({'records':dict(type='array',items=POINT),'evidence':TEXT}),['display_digits'],'宿主温压时间记录，不是DCD4001原生文件','query','agent',{}),
 'reference.lookup':(obj({'substance_id':TEXT,'fit_id':TEXT,'temperature_k':number('K',293.15,523.15)}),obj({'pressure_pa':number('Pa',0),'source':TEXT,'evidence':TEXT,'template_source':TEXT}),['flask'],'查询NIST原区间内纯物质饱和蒸气压','query','agent',dict(substance_id='hexane',fit_id='0',temperature_k=313.15))}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,zh,o='derived',v='agent',c='display_digits',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=zh
for n in ['plant','column','method','sample']:slot(n+'.configured',BOOL,'1',n+'配置状态')
slot('power.enabled',BOOL,'1','虚拟供电','setpoint',w=('power.set','enabled'))
slot('cycle.phase',PHASE,'1','异步作业状态');slot('cycle.fault',TEXT,'1','终止原因');slot('cycle.elapsed_s',number('s',0),'s','作业累计模拟时间')
slot('thermal.target_k',number('K',293.15,523.15),'K','油浴温度目标','setpoint',w=('temperature.set','temperature_k'))
for k,zh in [('bath','一阶油浴温度代理'),('boiler','理想釜温，稳态沸腾时为混合物泡点'),('top','理想柱顶温度，非真实校准探头')]:slot('thermal.'+k+'_temperature_k',number('K',293.15,523.15),'K',zh)
slot('vacuum.pressure_pa',number('Pa',10,101325),'Pa','绝对压力；外部调压假设')
slot('reflux.target_ratio',number('1',0,1000),'1','液体回流L与采出D之比目标','setpoint',c='reflux_plunger')
slot('reflux.total_reflux',BOOL,'1','全回流目标；实际回流分率仍由step响应','setpoint',c='reflux_plunger')
slot('reflux.fraction',number('1',0,1),'1','经一阶响应的回流分率L/V',c='reflux_plunger')
slot('reflux.stroke_m',number('m',0,.006),'m','平均回流分率对应演示柱塞行程，不是真实脉冲阀',c='reflux_plunger')
slot('bath.lift_m',number('m',0,.15),'m','宿主手动升降目标的限速姿态',c='bath_lift')
slot('bath.engaged',BOOL,'1','宿主手动浴架的工作位目标','setpoint',v='evaluator',c='bath_lift',w=('bath.set_engaged','engaged'))
slot('fraction.index',integer(0,5),'1','目标接收瓶索引',c='collector_selector');slot('fraction.angle_rad',number('rad',0,5*math.pi/3),'rad','选择头角度，接收瓶不转动',c='collector_selector')
slot('column.boilup_mol_s',number('mol/s',0),'mol/s','宿主温差驱动的常摩尔流蒸发率')
slot('truth.boiler_mol',number('mol',0),'mol','釜中总物质的量','process_truth','evaluator','flask');slot('truth.fractions_mol',number('mol',0),'mol','六接收瓶总物质的量','process_truth','evaluator','collector_selector')

class PilodistPilodist104Runtime:
 def __init__(self,instance_id='pilodist_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.plant=None;self.column=None;self.method=None;self.sample=None;self.powered=False;self.engaged=False;self.lift=0.;self.phase='idle';self.fault='';self.elapsed_s=0.;self.bath_t=303.15;self.boiler_t=303.15;self.top_t=303.15;self.pressure=101325.;self.reflux=0.;self.boilup=0.;self.receiver=0;self.angle=0.;self.inventory=np.zeros((0,0));self.fractions=np.zeros((6,0));self.initial=np.zeros(0);self.vm=np.zeros(0);self.history=[];self.operation_id=None;self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ['agent','evaluator','debug']:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return PilodistPilodist104Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _fit(self,sid,fid):
  if sid not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
  for f in self.templates[sid]['fits']:
   if f['fit_id']==fid:return f
  raise ActionRejected('UNKNOWN_REFERENCE')
 def _psat(self,sid,fid,t):
  f=self._fit(sid,fid);lo,hi=f['runtime_temperature_range_k']
  if not lo<=t<=hi:raise ActionRejected('REFERENCE_RANGE')
  return 1e5*10**(f['A']-f['B_k']/(t+f['C_k']))
 def _values(self):
  return {**{n+'.configured':bool(getattr(self,n)) for n in ['plant','column','method','sample']},'power.enabled':self.powered,'cycle.phase':self.phase,'cycle.fault':self.fault,'cycle.elapsed_s':self.elapsed_s,'thermal.target_k':self.method['bath_target_k'] if self.method else 303.15,'thermal.bath_temperature_k':self.bath_t,'thermal.boiler_temperature_k':self.boiler_t,'thermal.top_temperature_k':self.top_t,'vacuum.pressure_pa':self.pressure,'reflux.target_ratio':self.method['reflux_ratio'] if self.method else 0.,'reflux.total_reflux':self.method['total_reflux'] if self.method else False,'reflux.fraction':self.reflux,'reflux.stroke_m':.006*self.reflux,'bath.lift_m':self.lift,'bath.engaged':self.engaged,'fraction.index':self.receiver,'fraction.angle_rad':self.angle,'column.boilup_mol_s':self.boilup,'truth.boiler_mol':float(self.inventory[0].sum()) if self.sample else 0.,'truth.fractions_mol':float(self.fractions.sum())}
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ['debug','evaluator']})
 def debug_snapshot(self):return copy.deepcopy(dict(values=self._values(),plant=self.plant,column=self.column,method=self.method,sample=self.sample,inventory=self.inventory.tolist(),fractions=self.fractions.tolist(),history=self.history,time=self.time_s,revision=self.revision,operation=self.operation_id))
 def _equilibrium(self):
  # Every pure component must bracket target P within COMMON fit domain at load.
  x=self.inventory[:-1]/self.inventory[:-1].sum(axis=1,keepdims=True);lo=np.full(len(x),self.fit_lo);hi=np.full(len(x),self.fit_hi);p=self.method['pressure_pa']
  for _ in range(38):
   mid=(lo+hi)*.5;ps=1e5*10**(self.coeff[:,0]-self.coeff[:,1]/(mid[:,None]+self.coeff[:,2]));below=(x*ps).sum(axis=1)<p;lo=np.where(below,mid,lo);hi=np.where(below,hi,mid)
  t=(lo+hi)*.5;v=x*1e5*10**(self.coeff[:,0]-self.coeff[:,1]/(t[:,None]+self.coeff[:,2]));v/=v.sum(axis=1,keepdims=True);return t,x,v
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if self.phase=='running' and capability not in ['power.set','acquisition.stop','temperature.set','reflux.set','fraction.select'] and CAPS[capability][4]!='query':raise ActionRejected('BUSY')
  if capability=='plant.configure':
   if self.plant or not all(a[k] for k in ['external_vacuum_confirmed','external_cooling_confirmed','vent_boundary_confirmed','ideal_mixture_reviewed']):raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);self.bath_t=self.boiler_t=self.top_t=a['initial_temperature_k'];out=dict(configured=True)
  elif capability=='column.configure':
   if self.sample or not a['configuration_reviewed']:raise ActionRejected('PRECONDITION_FAILED')
   self.column=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='method.configure':
   if not self.plant or self.sample:raise ActionRejected('PRECONDITION_FAILED')
   self.method=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='sample.configure':
   if self.sample or not self.column or not self.method or not a['prewetted_initialization_confirmed']:raise ActionRejected('PRECONDITION_FAILED')
   ss=a['species'];n=np.array([s['amount_mol'] for s in ss]);vm=np.array([s['reference_molar_volume_m3_mol'] for s in ss]);volume=float(n@vm);c=self.column;hold=c['effective_stages']*c['stage_holdup_mol']+c['condenser_holdup_mol']
   if len({s['substance_id'] for s in ss})!=len(ss) or not .0001<=volume<=.004 or n.sum()<=hold or (n.sum()-hold)*(n@vm/n.sum())<=self.method['min_boiler_volume_m3']:raise ActionRejected('CHARGE_OR_HOLDUP')
   fits=[self._fit(s['substance_id'],s['fit_id']) for s in ss];lo=max(f['runtime_temperature_range_k'][0] for f in fits);hi=min(f['runtime_temperature_range_k'][1] for f in fits);p=self.method['pressure_pa']
   if lo>=hi or any(not self._psat(s['substance_id'],s['fit_id'],lo)<=p<=self._psat(s['substance_id'],s['fit_id'],hi) for s in ss):raise ActionRejected('REFERENCE_RANGE')
   amounts=[n.sum()-hold]+[c['stage_holdup_mol']]*c['effective_stages']+[c['condenser_holdup_mol']]
   self.sample=copy.deepcopy(a);self.initial=n;self.vm=vm;self.inventory=np.array(amounts)[:,None]*(n/n.sum());self.fractions=np.zeros((6,len(n)));self.fit_lo=lo;self.fit_hi=hi;self.coeff=np.array([[f['A'],f['B_k'],f['C_k']] for f in fits]);out=dict(configured=True,attachment_event='finite_prewetted_host_initialization_not_robot_attachment')
  elif capability=='bath.set_engaged':
   if self.phase=='running' or self.bath_t>313.15:raise ActionRejected('BATH_INTERLOCK')
   self.engaged=a['engaged'];out=a
  elif capability=='power.set':
   self.powered=a['enabled']
   if not self.powered and self.phase=='running':self.phase='cancelled'
   out=a
  elif capability=='temperature.set':
   if not self.method:raise ActionRejected('PRECONDITION_FAILED')
   self.method['bath_target_k']=a['temperature_k'];out=a
  elif capability=='reflux.set':
   if not self.method:raise ActionRejected('PRECONDITION_FAILED')
   self.method.update(reflux_ratio=a['ratio'],total_reflux=a['total_reflux']);out=a
  elif capability=='fraction.select':
   if not self.sample or float(self.fractions[a['index']]@self.vm)>=self.method['cut_volume_m3']-1e-12:raise ActionRejected('RECEIVER_FULL')
   self.receiver=a['index'];out=a
  elif capability=='acquisition.start':
   if not self.powered or not self.sample or self.lift<.15-1e-9 or not self.engaged or self.phase not in ['idle','cancelled']:raise ActionRejected('PRECONDITION_FAILED')
   if self.elapsed_s>=min(self.method['duration_s'],self.method['timeout_s']):raise ActionRejected('PRECONDITION_FAILED')
   self.phase='running';self.fault='';self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase=='running':self.phase='cancelled'
   out=dict(phase=self.phase)
  elif capability=='distillation.measure':out=dict(phase=self.phase,fault=self.fault,bath_temperature_k=self.bath_t,boiler_temperature_k=self.boiler_t,top_temperature_k=self.top_t,pressure_pa=self.pressure,reflux_fraction=self.reflux,boilup_mol_s=self.boilup,receiver_index=self.receiver,evidence='real_NIST_Psat_ideal_VLE_synthetic_host_dynamics',template_source=TEMPLATE_REL)
  elif capability=='product.read':out=dict(records=[dict(substance_id=s['substance_id'],initial_mol=float(self.initial[i]),boiler_mol=float(self.inventory[0,i]),column_mol=float(self.inventory[1:-1,i].sum()),condenser_mol=float(self.inventory[-1,i]),fractions_mol=self.fractions[:,i].tolist()) for i,s in enumerate((self.sample or {}).get('species',[]))],evidence='simulation_truth_not_measured_PILODIST_fractions',template_source=TEMPLATE_REL)
  elif capability=='history.read':out=dict(records=copy.deepcopy(self.history),evidence='synthetic_temperature_pressure_history')
  elif capability=='reference.lookup':out=dict(pressure_pa=self._psat(a['substance_id'],a['fit_id'],a['temperature_k']),source=self.templates[a['substance_id']]['source'],evidence='derived',template_source=TEMPLATE_REL)
  validate(CAPS[capability][1],out)
  if CAPS[capability][4]!='query':self.revision+=1
  return copy.deepcopy(out)
 def step(self,dt_s):
  validate(number('s',0,86400),dt_s);remaining=float(dt_s)
  while remaining>1e-12:
   h=min(remaining,.1);p=self.plant;m=self.method;running=self.phase=='running';self.boilup=0.
   if running:
    if self.elapsed_s>=m['duration_s']-1e-9:self.phase='succeeded';running=False
    elif self.elapsed_s>=m['timeout_s']-1e-9:self.phase='failed';self.fault='TIMEOUT';running=False
    elif float(self.inventory[0]@self.vm)<=m['min_boiler_volume_m3']+1e-12:self.phase='succeeded';running=False
    else:h=min(h,m['duration_s']-self.elapsed_s,m['timeout_s']-self.elapsed_s)
   if p:
    lift_target=.15 if self.engaged else 0.;self.lift+=max(-.03*h,min(.03*h,lift_target-self.lift));target_angle=self.receiver*math.pi/3;self.angle+=max(-p['selector_speed_rad_s']*h,min(p['selector_speed_rad_s']*h,target_angle-self.angle))
    pressure_target=m['pressure_pa'] if running else 101325.;self.pressure=pressure_target+(self.pressure-pressure_target)*math.exp(-h/p['pressure_tau_s'])
    conditioned=running and abs(self.pressure-pressure_target)<=max(.01,.001*pressure_target)
    if conditioned:self.pressure=pressure_target # ideal regulated plateau after explicit conditioning tolerance
    bath_target=m['bath_target_k'] if conditioned else p['initial_temperature_k'];self.bath_t=bath_target+(self.bath_t-bath_target)*math.exp(-h/p['bath_tau_s'])
    target_reflux=(1. if m['total_reflux'] else m['reflux_ratio']/(1+m['reflux_ratio'])) if running else 0.;self.reflux=target_reflux+(self.reflux-target_reflux)*math.exp(-h/p['reflux_tau_s'])
    if conditioned:
     temps,x,y=self._equilibrium();self.boiler_t=float(min(self.bath_t,temps[0]));self.top_t=float(temps[-1]) if self.bath_t>=temps[0] else self.boiler_t
     v=min(p['max_boilup_mol_s'],p['boilup_coefficient_mol_s_k']*max(0.,self.bath_t-temps[0]));fraction=1. if abs(self.angle-target_angle)>1e-9 else self.reflux;l=v*fraction;d=v-l
     # Paired transfers: boiler vapor -> stages -> total condenser; liquid flows down.
     all_x=self.inventory/self.inventory.sum(axis=1,keepdims=True);up=v*y;down=l*all_x[1:];take=d*all_x[-1];outgoing=np.zeros_like(self.inventory);outgoing[:-1]+=up;outgoing[1:]+=down;outgoing[-1]+=take
     scale=min(1.,float(np.min(np.divide(.8*self.inventory,h*outgoing,out=np.full_like(self.inventory,np.inf),where=outgoing>0))))
     net_boiler=up[0]-down[0];vol_loss=float(net_boiler@self.vm)*h
     if vol_loss>0:scale=min(scale,max(0.,(float(self.inventory[0]@self.vm)-m['min_boiler_volume_m3'])/vol_loss))
     cut=float(take@self.vm)*h
     if cut>0:scale=min(scale,max(0.,(m['cut_volume_m3']-float(self.fractions[self.receiver]@self.vm))/cut))
     up*=h*scale;down*=h*scale;take*=h*scale;delta=np.zeros_like(self.inventory);delta[:-1]-=up;delta[1:]+=up;delta[1:]-=down;delta[:-1]+=down;delta[-1]-=take;self.inventory+=delta;self.fractions[self.receiver]+=take;self.boilup=v*scale
     if float(self.fractions[self.receiver]@self.vm)>=m['cut_volume_m3']-1e-12:
      candidates=[i for i in range(self.receiver+1,6) if float(self.fractions[i]@self.vm)<m['cut_volume_m3']-1e-12]
      if candidates:self.receiver=candidates[0]
      else:self.phase='succeeded'
    if running:self.elapsed_s+=h
   self.time_s+=h;self.revision+=1;remaining-=h
   if self.sample:self.history.append(dict(time_s=self.time_s,boiler_temperature_k=self.boiler_t,top_temperature_k=self.top_t,pressure_pa=self.pressure,receiver_index=self.receiver))
  if self.phase=='running':
   if self.elapsed_s>=self.method['duration_s']-1e-9:self.phase='succeeded'
   elif self.elapsed_s>=self.method['timeout_s']-1e-9:self.phase='failed';self.fault='TIMEOUT'
  return self.read()

class PilodistPilodist104Session:
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
        meta=export_pilodist_pilodist104_contract_metadata()
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('distillation.measure'))

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])

def export_pilodist_pilodist104_contract_metadata():
    caps={};slots={}
    for k,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions='绝对压力；宿主理想观测，非原厂传感器' if 'pressure' in k else None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'PilodistPilodist104Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for k,(inp,out,components,meaning,kind,vis,example) in CAPS.items():
        async_=k=='acquisition.start';effects=[] if kind=='query' else list(slots)
        caps[k]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.pilodist_pilodist104.PilodistPilodist104Session.invoke',invocation=f'session.invoke(instance_id="pilodist_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if kind=='query' else [dict(description_zh='显式外部边界；选柱/方法/有限装料；共同NIST温区涵盖所有纯组分泡点；浴升至工作位才能启动；拒绝不改状态',state_ids=['cycle.phase','plant.configured','sample.configured','power.enabled','column.configured'],enforced_by='runtime',check='PilodistPilodist104Runtime.invoke capability-specific guards')],state_effects=[dict(state_id=i,timing='on_step' if async_ else 'immediate',description_zh='实际配置/库存效应；查询不推进时钟；取消保留已分离库存') for i in effects],observes=[],model_effects=dict(mode='none',description_zh='需真实场景求值证据才登记联动',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='时长、最小釜剩液或接收瓶满终点；超时失败；取消保留釜/柱/冷凝器/馏分' if async_ else '同步返回，不推进世界',completion_state_ids=['cycle.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['finite_prewetted_column','external_vacuum_and_cooling','prescribed_CMO_heat_boundary','principal_request_cache'],partial_failure_zh='取消、断电、超时保留各级与六瓶组分；step继续通气、浴温向宿主环境衰减，不声称完整冷却能量平衡。'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT','UNKNOWN_REFERENCE','REFERENCE_RANGE','BATH_INTERLOCK','CHARGE_OR_HOLDUP','RECEIVER_FULL']],example_arguments=copy.deepcopy(example),verification=[],limitations=['非真实固件/安全认证；宿主文件进程沙箱、机器人操作与化学兼容性未验证。'],source_ids=['manufacturer_primary','manufacturer_secondary','simulation_assumption'])
        if async_:caps[k]['errors'] += [dict(code=e,meaning_zh=e,side_effects='partial') for e in ['TIMEOUT']]
    immediate={'plant.configure':['plant.configured','thermal.bath_temperature_k','thermal.boiler_temperature_k','thermal.top_temperature_k'],'column.configure':['column.configured'],'method.configure':['method.configured','thermal.target_k','reflux.target_ratio','reflux.total_reflux'],'sample.configure':['sample.configured','truth.boiler_mol','truth.fractions_mol'],'bath.set_engaged':['bath.engaged'],'power.set':['power.enabled','cycle.phase'],'temperature.set':['thermal.target_k'],'reflux.set':['reflux.target_ratio','reflux.total_reflux'],'fraction.select':['fraction.index'],'acquisition.start':['cycle.phase','cycle.fault'],'acquisition.stop':['cycle.phase']}
    advancing=['cycle.phase','cycle.fault','cycle.elapsed_s','thermal.bath_temperature_k','thermal.boiler_temperature_k','thermal.top_temperature_k','vacuum.pressure_pa','reflux.fraction','reflux.stroke_m','bath.lift_m','fraction.index','fraction.angle_rad','column.boilup_mol_s','truth.boiler_mol','truth.fractions_mol']
    for k,cap in caps.items():
        cap['state_effects']=[dict(state_id=i,timing='immediate',description_zh='只写此配置/目标/操作状态；不隐式推进时间') for i in immediate.get(k,[])]
        if k=='acquisition.start':cap['state_effects'] += [dict(state_id=i,timing='on_step',description_zh='仅宿主step推进有限级物理和唯一权威姿态') for i in advancing]
    mechanism=dict(component_ids=['flask','column_concentric','column_wiregauze','reflux_head','collector_selector'],state_ids=list(slots),meaning_zh='有限预润湿批式理想平衡级；恒摩尔流、明确宿主热边界与回流响应',implementation='lumped',equations=['Psat=100000*10**(A-B/(T+C)); selected real NIST fits; no extrapolation','sum(x_i*Psat_i(T))=P; y_i=x_i*Psat_i/P; ideal Raoult bubble-point bisection','dB_i/dt=L*x_1i-V*y_Bi','dH_ji/dt=V*y_(j-1)i+L*x_(j+1)i-V*y_ji-L*x_ji','dC_i/dt=V*y_top_i-(L+D)*x_Ci; dn_receiver_i/dt=D*x_Ci; D=V-L','R=L/D; reflux fraction follows first-order host response; selector moving forces total reflux','V=min(Vmax,k_host*max(Tbath-Tbubble,0)); prescribed molar boilup NOT energy closure','finite donor-limited paired transfers conserve each species and reference additive volume','all initial prewet inventories deducted from100–4000mL charge; no fresh matter at startup','pressure conditioning first-order then ideal regulated plateau at0.1percent tolerance; no vacuum gas holdup','read is pure; only step<=0.1s advances heat, pressure, routing, reflux and mass'],parameters={},source_ids=['manufacturer_primary','manufacturer_secondary','nist_antoine','stage_mass_balance','simulation_assumption'],limitations=['81 real NIST reference substances,80 with any device pure-boiling overlap; not81 measured distillation runs. Chosen mixtures require every pure component bubble point inside common chosen fit domain.','Ideal activity coefficients and constant molar overflow; no azeotropes, nonideal VLE, entrainment, reaction, decomposition, flooding, pressure drop or thermodynamic energy closure.','Column type is a configuration identifier; effective stages and holdups are explicit host inputs, never inferred from geometry rings or assumed90-stage performance. Both column types need experimental calibration.','Prewetted startup initialization deducts all holdup from sample; actual dry-column wetting, dynamic total holdup and hydraulic transport delays are not resolved.','Bath temperature first-order; boilup coefficient and molar volume are host assumptions. Instantaneous equilibrium-stage temperatures during boiling, no sensible/latent/condenser heat balance.','Pressure target fixed before sample load; pressure transient conditions before boilup. Ideal total condenser has finite liquid holdup, no vapor inventory or thermal-capacity model.','Reference additive volumes at declared sample reference temperature, not temperature-dependent physical density.','Manual bath jack and collector selection animation are host proxies; no robotic manipulation or real DCD4001 graphical firmware. Six optional1L fixed bottles only, not9 receivers.','Reflux plunger shows average fraction; actual valve pulse timing/stroke unknown. Stage-count physical90plate envelope not experimentally validated.','Public session filtering is not deployed file/process isolation. Matterix acknowledgment separate from native asynchronous completion.'])
    spec=importlib.util.spec_from_file_location('_pilodist_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
    ann.annotate_export(SLUG,caps,slots,{'distillation':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'column.configure':(COLUMN,COLUMN_EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'distillation':mechanism})

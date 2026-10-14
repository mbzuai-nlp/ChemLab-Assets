"""LeNeo finite fusion teaching model; host coefficients, not factory calibration."""
from __future__ import annotations
import copy,importlib.util,json,math
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.pccell_ed64002 import validate
ROOT=Path(__file__).resolve().parents[4] if 'outputs' in str(__file__) else Path(__file__).resolve().parents[2]
SLUG='malvern_leneo';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-050';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/malvern_leneo_fusion_library/templates.json'
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed']);OUTPUT=dict(type='string',enum=['glass_disk','borate_solution'])
def array(s):return dict(type='array',items=s)
PLANT=obj({'ambient_K':number('K',298.15,313.15),'supply_V':number('V',208,240),'heater_W':number('W',1,4800),'chamber_C_J_K':number('J/K',100,5000),'crucible_C_J_K':number('J/K',10,500),'receiver_C_J_K':number('J/K',10,500),'chamber_UA_W_K':number('W/K',.01,10),'charge_UA_W_K':number('W/K',.1,30),'receiver_UA_W_K':number('W/K',.01,20),'fan_UA_W_K':number('W/K',0,30),'controller_W_K':number('W/K',1,1000),'flux_cp_J_kg_K':number('J/kg/K',100,5000),'solidus_K':number('K',400,1300),'liquidus_K':number('K',450,1400),'latent_J_kg':number('J/kg',0,1e6),'dissolution_rate_s':number('1/s',0,10),'loss_rate_s':number('1/s',0,.01),'viscosity_Pa_s':number('Pa*s',.001,1e4),'viscosity_activation_K':number('K',0,20000),'pour_kg_s':number('kg/s',1e-7,.01),'residual_fraction':number('1',0,.5),'solution_rate_s':number('1/s',0,10),'motion_rad_s':number('rad/s',.1,10),'unlock_K':number('K',298.15,333.15),'accept_uncalibrated_model':BOOL,'coefficient_evidence':TEXT})
PLANT_EXAMPLE=dict(ambient_K=298.15,supply_V=230.,heater_W=3500.,chamber_C_J_K=1200.,crucible_C_J_K=30.,receiver_C_J_K=80.,chamber_UA_W_K=1.,charge_UA_W_K=4.,receiver_UA_W_K=.3,fan_UA_W_K=3.,controller_W_K=150.,flux_cp_J_kg_K=1200.,solidus_K=1000.,liquidus_K=1100.,latent_J_kg=250000.,dissolution_rate_s=.02,loss_rate_s=.000001,viscosity_Pa_s=2.,viscosity_activation_K=2500.,pour_kg_s=.001,residual_fraction=.02,solution_rate_s=.1,motion_rad_s=2.,unlock_K=323.15,accept_uncalibrated_model=True,coefficient_evidence='explicit_host_assumptions_not_LeNeo_calibration')
SEG=obj({'duration_s':number('s',.1,7200),'end_temperature_K':number('K',298.15,1473.15)})
PROFILE=obj({'angle_rad':number('rad',.8,2.3),'duration_s':number('s',.1,300)})
METHOD=obj({'segments':array(SEG),'rocking_rad':number('rad',0,.35),'rocking_Hz':number('Hz',0,2),'pour_mode':dict(type='integer',minimum=1,maximum=4,**{'x-unit':'1'}),'host_pour_profiles':array(PROFILE),'cooling_air_fraction':number('1',0,1),'minimum_cooling_s':number('s',0,1800),'output':OUTPUT,'accept_host_profiles_not_factory_methods':BOOL})
METHOD_EXAMPLE=dict(segments=[dict(duration_s=600.,end_temperature_K=1338.15),dict(duration_s=540.,end_temperature_K=1338.15)],rocking_rad=.18,rocking_Hz=.3,pour_mode=1,host_pour_profiles=[dict(angle_rad=v,duration_s=20.) for v in [1.6,1.8,2.,2.2]],cooling_air_fraction=1.,minimum_cooling_s=300.,output='glass_disk',accept_host_profiles_not_factory_methods=True)
SAMPLE=obj({'substance_id':TEXT,'sample_kg':number('kg',1e-7,.01),'flux_kg':number('kg',1e-6,.05),'crucible_present':BOOL,'receiver_present':BOOL,'receiver_diameter_m':number('m',.02,.05),'solvent_kg':number('kg',0,.3),'accept_reference_not_fusion_calibration':BOOL})
SAMPLE_EXAMPLE=dict(substance_id='janaf_al_096',sample_kg=.0006,flux_kg=.006,crucible_present=True,receiver_present=True,receiver_diameter_m=.032,solvent_kg=0.,accept_reference_not_fusion_calibration=True)
MEASURE=obj({'phase':PHASE,'stage':TEXT,'elapsed_s':number('s',0),'chamber_temperature_K':number('K',0),'target_temperature_K':number('K',0),'evidence':TEXT,'template_source':TEXT})
PRODUCT=obj({**{k:number('kg',0) for k in ['initial_sample_kg','initial_flux_kg','initial_solvent_kg','crucible_sample_kg','crucible_flux_kg','receiver_sample_kg','receiver_flux_kg','receiver_solvent_kg','lost_sample_kg','collected_sample_kg','collected_flux_kg','collected_solvent_kg','undissolved_crucible_kg','undissolved_receiver_kg']},**{k:number('J') for k in ['electrical_J','storage_J','loss_J','advected_loss_J','collected_enthalpy_J']},'liquid_fraction':number('1',0,1),'receiver_temperature_K':number('K',0),'output':TEXT,'evidence':TEXT,'template_source':TEXT})
CAPS={
 'plant.configure':(PLANT,obj({'configured':BOOL}),['heater'],'显式宿主热/相变/黏度/损失参数','configuration','evaluator',PLANT_EXAMPLE),
 'method.configure':(METHOD,obj({'configured':BOOL}),['display_digits'],'分段线性程序、摇动、4个宿主倾倒编号及冷却','configuration','agent',METHOD_EXAMPLE),
 'sample.configure':(SAMPLE,obj({'configured':BOOL}),['crucible','mold'],'有限粉体/熔剂、坩埚和接收器','configuration','evaluator',SAMPLE_EXAMPLE),
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['button_power'],'主电源','device_command','agent',dict(enabled=True)),
 'power.toggle':(obj({}),obj({'enabled':BOOL}),['button_power'],'主电源事件代理','device_command','agent',{}),
 'door.set_open':(obj({'open':BOOL}),obj({'open':BOOL}),['door'],'冷态开门目标，实际角度仅step推进','device_command','agent',dict(open=False)),
 'thermal.set_temperature':(obj({'temperature_K':number('K',298.15,1473.15)}),obj({'temperature_K':number('K',298.15,1473.15)}),['heater'],'只设目标；空闲不自动加热，程序运行会覆盖目标','device_command','agent',dict(temperature_K=1338.15)),
 'cooling.set_air':(obj({'fraction':number('1',0,1)}),obj({'fraction':number('1',0,1)}),['fan'],'风扇控制比例，不伪称实际流量','device_command','agent',dict(fraction=1.)),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['button_touch_start','heater','crucible'],'自动锁门、程序熔融、有限倾倒和冷却','device_command','agent',{}),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['display_digits'],'取消加热，热态仍锁门并保留部分产物','device_command','agent',{}),
 'emergency.stop':(obj({}),obj({'phase':PHASE}),['button_emergency'],'急停教学代理，断热且不伪造冷却完成','device_command','agent',{}),
 'fusion.measure':(obj({}),MEASURE,['display_digits'],'理想R型腔温显示与程序计时，不是样品传感器','query','agent',{}),
 'product.read':(obj({}),PRODUCT,['mold'],'评价器有限玻璃片/溶液和质量能量账本','query','evaluator',{}),
 'product.collect':(obj({'fraction':number('1',0,1),'recipient_id':TEXT}),obj({'transferred_kg':number('kg',0),'recipient_id':TEXT}),['mold'],'冷态开门后有限转移，不是机器人动作','device_command','evaluator',dict(fraction=.5,recipient_id='external_XRF')),
 'reference.lookup':(obj({'substance_id':TEXT,'temperature_K':number('K',0)}),obj({'cp_J_kg_K':number('J/kg/K',0),'enthalpy_J_kg':number('J/kg'),'source':TEXT,'evidence':TEXT,'template_source':TEXT}),['crucible'],'真实晶相热力学参考，源温区内插值','query','agent',dict(substance_id='janaf_al_096',temperature_K=1000.)),
}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,zh,o='derived',v='agent',c='body',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=zh
for n in ['plant','method','sample']:slot(n+'.configured',BOOL,'1',n+'配置','setpoint')
slot('power.enabled',BOOL,'1','主电源','setpoint',c='button_power',w=('power.set','enabled'));slot('door.open_target',BOOL,'1','门开目标','setpoint',c='door',w=('door.set_open','open'));slot('door.angle_rad',number('rad',0,1.6),'rad','照片估计门角','scene',c='door');slot('door.locked',BOOL,'1','宿主冷到冷安全锁','derived',c='door')
slot('thermal.target_k',number('K',298.15,1473.15),'K','程序腔温目标','setpoint',c='heater',w=('thermal.set_temperature','temperature_K'));slot('cooling.air_fraction',number('1',0,1),'1','冷却气流相对设定','setpoint',c='fan',w=('cooling.set_air','fraction'))
for k,c,lo,hi in [('rock.angle_rad','rocker',-.35,.35),('pour.angle_rad','pour_axis',0,2.3)]:slot(k,number('rad',lo,hi),'rad','宿主权威照片估计关节','scene',c=c)
slot('cycle.phase',PHASE,'1','异步状态');slot('cycle.stage',TEXT,'1','程序阶段');slot('cycle.elapsed_s',number('s',0),'s','程序秒')
for k,u,zh in [('temperature_c','degC','理想R热电偶腔温'),('target_c','degC','当前腔温目标'),('elapsed_s','s','程序秒')]:slot('panel.'+k,number(u),u,zh,'derived',c='display_digits')
slot('truth.charge_temperature_k',number('K',0),'K','样品真值非仪器读数','process_truth','evaluator','crucible');slot('truth.receiver_temperature_k',number('K',0),'K','接收器真值','process_truth','evaluator','mold');slot('truth.ledger',PRODUCT,'1','质量能量账本','process_truth','evaluator','mold')
class MalvernLeneoRuntime:
 def __init__(self,instance_id='leneo_01'):
  self.instance_id=instance_id;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.plant=self.method=self.sample=None;self.powered=False;self.open_target=True;self.door=1.6;self.rock=self.pour=0.;self.phase='idle';self.stage='idle';self.time_s=self.elapsed_s=self.cool_s=self.pour_s=0.;self.target=self.chamber=self.charge=self.receiver=298.15;self.air=0.;self.cp_sample=0.;self.ms=self.mf=self.rs=self.rf=self.solvent=self.undissolved=self.rundissolved=0.;self.lost=self.collected_s=self.collected_f=self.collected_solvent=0.;self.Ec=self.Er=self.electrical=self.loss=self.advected=self.collected_energy=0.;self.revision=0;self.operation_id='';self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility):
  if visibility not in ['agent','debug','evaluator'] or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return MalvernLeneoSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def locked(self):return self.phase=='running' or bool(self.plant and max(self.chamber,self.charge,self.receiver)>self.plant['unlock_K']+1e-9)
 def liquid(self,t):
  if not self.plant:return 0.
  p=self.plant;return min(1.,max(0.,(t-p['solidus_K'])/(p['liquidus_K']-p['solidus_K'])))
 def specific(self,t,cp):return cp*(t-self.plant['ambient_K'])+self.plant['latent_J_kg']*self.liquid(t)
 def enthalpy(self,t,ms,mf,solvent,base):return base*(t-self.plant['ambient_K'])+ms*self.specific(t,self.cp_sample)+mf*self.specific(t,self.plant['flux_cp_J_kg_K'])+solvent*4180*(t-self.plant['ambient_K'])
 def temperature(self,E,ms,mf,solvent,base):
  p=self.plant;C=base+ms*self.cp_sample+mf*p['flux_cp_J_kg_K']+solvent*4180;L=(ms+mf)*p['latent_J_kg'];a=p['solidus_K'];b=p['liquidus_K'];lo=C*(a-p['ambient_K']);hi=C*(b-p['ambient_K'])+L
  return p['ambient_K']+E/C if E<=lo else (p['ambient_K']+(E-L)/C if E>=hi else a+(E-lo)/(C+L/(b-a)))
 def reference(self,i,t):
  if i not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
  r=self.templates[i];ps=r['points']
  if t<ps[0]['temperature_K'] or t>ps[-1]['temperature_K']:raise ActionRejected('OUT_OF_REFERENCE_RANGE')
  exact=[x for x in ps if x['temperature_K']==t]
  if len(exact)>1:raise ActionRejected('AMBIGUOUS_PHASE_TRANSITION')
  if exact:a=b=exact[0];f=0.
  else:
   a=next(x for x in reversed(ps) if x['temperature_K']<t);b=next(x for x in ps if x['temperature_K']>t);f=(t-a['temperature_K'])/(b['temperature_K']-a['temperature_K'])
  val=lambda k:(a[k]+(b[k]-a[k])*f)/r['molar_mass_kg_mol']
  return dict(cp_J_kg_K=val('cp_J_mol_K'),enthalpy_J_kg=val('enthalpy_relative_J_mol'),source=r['source'],evidence='measured_literature_interpolated_pure_crystal_NOT_fusion_measurement',template_source=TEMPLATE_REL)
 def _product(self):
  s=self.sample or {};return dict(initial_sample_kg=s.get('sample_kg',0.),initial_flux_kg=s.get('flux_kg',0.),initial_solvent_kg=s.get('solvent_kg',0.),crucible_sample_kg=self.ms,crucible_flux_kg=self.mf,receiver_sample_kg=self.rs,receiver_flux_kg=self.rf,receiver_solvent_kg=self.solvent,lost_sample_kg=self.lost,collected_sample_kg=self.collected_s,collected_flux_kg=self.collected_f,collected_solvent_kg=self.collected_solvent,undissolved_crucible_kg=self.undissolved,undissolved_receiver_kg=self.rundissolved,electrical_J=self.electrical,storage_J=(self.plant['chamber_C_J_K']*(self.chamber-self.plant['ambient_K']) if self.plant else 0.)+self.Ec+self.Er,loss_J=self.loss,advected_loss_J=self.advected,collected_enthalpy_J=self.collected_energy,liquid_fraction=self.liquid(self.receiver),receiver_temperature_K=self.receiver,output=(self.method or {}).get('output','unconfigured'),evidence='synthetic_conservative_lumped_fusion_NOT_XRF_ICP_or_factory_validation',template_source=TEMPLATE_REL)
 def _values(self):return {'plant.configured':bool(self.plant),'method.configured':bool(self.method),'sample.configured':bool(self.sample),'power.enabled':self.powered,'door.open_target':self.open_target,'door.angle_rad':self.door,'door.locked':self.locked(),'thermal.target_k':self.target,'cooling.air_fraction':self.air,'rock.angle_rad':self.rock,'pour.angle_rad':self.pour,'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.elapsed_s':self.elapsed_s,'panel.temperature_c':self.chamber-273.15,'panel.target_c':self.target-273.15,'panel.elapsed_s':self.elapsed_s,'truth.charge_temperature_k':self.charge,'truth.receiver_temperature_k':self.receiver,'truth.ledger':self._product()}
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ['debug','evaluator']})
 def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k!='templates'})
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if self.phase=='running' and capability in ['plant.configure','method.configure','sample.configure','product.collect']:raise ActionRejected('BUSY')
  if capability=='plant.configure':
   if self.plant or not a['accept_uncalibrated_model'] or a['heater_W']>a['supply_V']*20 or a['liquidus_K']<=a['solidus_K'] or a['unlock_K']<a['ambient_K']:raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);self.target=self.chamber=self.charge=self.receiver=a['ambient_K'];out=dict(configured=True)
  elif capability=='method.configure':
   if self.phase!='idle' or not a['segments'] or len(a['segments'])>20 or len(a['host_pour_profiles'])!=4 or not a['accept_host_profiles_not_factory_methods']:raise ActionRejected('PRECONDITION_FAILED')
   self.method=copy.deepcopy(a);self.air=a['cooling_air_fraction'];out=dict(configured=True)
  elif capability=='sample.configure':
   if not self.plant or self.sample or self.phase!='idle' or self.door<1.59 or not all(a[k] for k in ['crucible_present','receiver_present','accept_reference_not_fusion_calibration']):raise ActionRejected('PRECONDITION_FAILED')
   cp=self.reference(a['substance_id'],298.15)['cp_J_kg_K'];self.sample=copy.deepcopy(a);self.cp_sample=cp;self.ms=self.undissolved=a['sample_kg'];self.mf=a['flux_kg'];self.solvent=a['solvent_kg'];out=dict(configured=True)
  elif capability in ['power.set','power.toggle']:
   self.powered=a['enabled'] if capability=='power.set' else not self.powered
   if not self.powered and self.phase=='running':self.phase='cancelled';self.stage='power_interrupted'
   out=dict(enabled=self.powered)
  elif capability=='door.set_open':
   if a['open'] and self.locked():raise ActionRejected('PRECONDITION_FAILED')
   self.open_target=a['open'];out=a
  elif capability=='thermal.set_temperature':self.target=a['temperature_K'];out=a
  elif capability=='cooling.set_air':self.air=a['fraction'];out=a
  elif capability=='acquisition.start':
   if not self.plant or not self.method or not self.sample or not self.powered or self.phase!='idle' or self.open_target or self.door>1e-9 or self.method['output']=='borate_solution' and self.solvent<=0 or self.method['output']=='glass_disk' and self.solvent!=0:raise ActionRejected('PRECONDITION_FAILED')
   self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);self.phase='running';self.stage='heating';out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability in ['acquisition.stop','emergency.stop']:
   if capability=='acquisition.stop' and self.phase!='running':raise ActionRejected('PRECONDITION_FAILED')
   self.phase='cancelled' if capability=='acquisition.stop' else 'failed';self.stage='cancelled' if capability=='acquisition.stop' else 'emergency';out=dict(phase=self.phase)
  elif capability=='fusion.measure':return dict(phase=self.phase,stage=self.stage,elapsed_s=self.elapsed_s,chamber_temperature_K=self.chamber,target_temperature_K=self.target,evidence='synthetic_ideal_chamber_thermocouple_R_NOT_charge_temperature',template_source=TEMPLATE_REL)
  elif capability=='product.read':return self._product()
  elif capability=='reference.lookup':return self.reference(a['substance_id'],a['temperature_K'])
  elif capability=='product.collect':
   if self.phase!='succeeded' or self.locked() or self.door<1.59:raise ActionRejected('PRECONDITION_FAILED')
   f=a['fraction'];ms=self.rs*f;mf=self.rf*f;sol=self.solvent*f;E=ms*self.specific(self.receiver,self.cp_sample)+mf*self.specific(self.receiver,self.plant['flux_cp_J_kg_K'])+sol*4180*(self.receiver-self.plant['ambient_K']);self.collected_s+=ms;self.collected_f+=mf;self.collected_solvent+=sol;self.collected_energy+=E;self.Er-=E;self.rs-=ms;self.rf-=mf;self.solvent-=sol;self.rundissolved*=1-f;out=dict(transferred_kg=ms+mf+sol,recipient_id=a['recipient_id'])
  self.revision+=1;return copy.deepcopy(out)
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or not 0<=dt_s<=7200:raise ActionRejected('INVALID_ARGUMENT')
  left=float(dt_s)
  while left>1e-10:
   h=min(.02,left);p=self.plant
   if p:
    amb=p['ambient_K'];active=self.phase=='running' and self.stage=='heating';P=0.
    if active:
     total=0.;prev=amb
     for seg in self.method['segments']:
      if self.elapsed_s<total+seg['duration_s']-1e-9:
       h=min(h,total+seg['duration_s']-self.elapsed_s);self.target=prev+(seg['end_temperature_K']-prev)*(self.elapsed_s-total)/seg['duration_s'];break
      total+=seg['duration_s'];prev=seg['end_temperature_K']
     P=min(p['heater_W'],max(0.,p['controller_W_K']*(self.target-self.chamber))) if self.powered else 0.
    fan=self.air*p['fan_UA_W_K'] if self.stage=='cooling' or self.phase!='running' else 0.
    lc=(p['chamber_UA_W_K']+fan)*(self.chamber-amb)*h;qc=p['charge_UA_W_K']*(self.chamber-self.charge)*h if active else 0.;lq=(.1+fan)*(self.charge-amb)*h if not active else 0.;lr=(p['receiver_UA_W_K']+fan)*(self.receiver-amb)*h;dh=P*h-lc-qc;extra=max(0.,p['chamber_C_J_K']*(self.chamber-1473.15)+dh);dh-=extra;self.chamber+=dh/p['chamber_C_J_K'];self.Ec+=qc-lq;self.Er-=lr;self.electrical+=P*h;self.loss+=lc+lq+lr+extra
    self.charge=self.temperature(self.Ec,self.ms,self.mf,0.,p['crucible_C_J_K']);self.receiver=self.temperature(self.Er,self.rs,self.rf,self.solvent,p['receiver_C_J_K'])
    if self.phase=='running':
     if self.stage=='heating':
      liq=self.liquid(self.charge);self.undissolved*=math.exp(-p['dissolution_rate_s']*liq*(1+abs(self.rock))*h);dm=self.ms*(-math.expm1(-p['loss_rate_s']*liq*h));f=dm/self.ms if self.ms else 0.;E=dm*self.specific(self.charge,self.cp_sample);self.ms-=dm;self.undissolved*=1-f;self.lost+=dm;self.Ec-=E;self.advected+=E;self.elapsed_s+=h
      if self.elapsed_s>=sum(s['duration_s'] for s in self.method['segments'])-1e-8:
       if self.liquid(self.charge)<.95 or self.undissolved>self.ms*.05:self.phase='failed';self.stage='incomplete_fusion'
       else:self.stage='pouring'
     elif self.stage=='pouring':
      prof=self.method['host_pour_profiles'][self.method['pour_mode']-1];self.pour_s+=h;mu=p['viscosity_Pa_s']*math.exp(min(40,p['viscosity_activation_K']*(1/max(self.charge,1)-1/1338.15)));mass=self.ms+self.mf;rem=(self.sample['sample_kg']+self.sample['flux_kg'])*p['residual_fraction'];dm=min(max(0.,mass-rem),p['pour_kg_s']*max(0.,math.sin(self.pour-.5))*self.liquid(self.charge)/mu*h);f=dm/mass if mass else 0.;ds=self.ms*f;df=self.mf*f;du=self.undissolved*f;E=ds*self.specific(self.charge,self.cp_sample)+df*self.specific(self.charge,p['flux_cp_J_kg_K']);self.ms-=ds;self.mf-=df;self.rs+=ds;self.rf+=df;self.undissolved-=du;self.rundissolved+=du;self.Ec-=E;self.Er+=E
      if self.pour_s>=prof['duration_s']-1e-9:self.stage='cooling'
     elif self.stage=='cooling':
      self.cool_s+=h
      if self.method['output']=='borate_solution':self.rundissolved*=math.exp(-p['solution_rate_s']*h)
      if self.cool_s>=self.method['minimum_cooling_s'] and max(self.chamber,self.charge,self.receiver)<=p['unlock_K']:
       self.phase='succeeded' if self.rs+self.rf>0 and (self.method['output']=='glass_disk' and self.liquid(self.receiver)==0 or self.method['output']=='borate_solution' and self.rundissolved<max(1e-12,self.rs*.001)) else 'failed';self.stage='cold_product' if self.phase=='succeeded' else 'empty_or_undissolved'
    self.charge=self.temperature(self.Ec,self.ms,self.mf,0.,p['crucible_C_J_K']);self.receiver=self.temperature(self.Er,self.rs,self.rf,self.solvent,p['receiver_C_J_K'])
    if self.solvent>0 and self.receiver>=373.15 and self.phase=='running':self.phase='failed';self.stage='unsupported_solvent_boiling'
   speed=p['motion_rad_s'] if p else 2.;rock_target=self.method['rocking_rad']*math.sin(math.tau*self.method['rocking_Hz']*self.elapsed_s) if self.phase=='running' and self.stage=='heating' else 0.;pour_target=self.method['host_pour_profiles'][self.method['pour_mode']-1]['angle_rad'] if self.phase=='running' and self.stage=='pouring' else 0.
   for k,t in [('door',1.6 if self.open_target and not self.locked() else 0.),('rock',rock_target),('pour',pour_target)]:v=getattr(self,k);setattr(self,k,v+max(-speed*h,min(speed*h,t-v)))
   self.time_s+=h;left-=h;self.revision+=1

class MalvernLeneoSession:
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
        meta=export_malvern_leneo_contract_metadata()
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('fusion.measure'))

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])





def export_malvern_leneo_contract_metadata():
 caps={};slots={}
 for k,(schema,unit,origin,visibility,components,write) in SLOTS.items():slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'MalvernLeneoSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
 immediate={'plant.configure':['plant.configured','thermal.target_k','panel.target_c','panel.temperature_c','truth.charge_temperature_k','truth.receiver_temperature_k','truth.ledger'],'method.configure':['method.configured','cooling.air_fraction','truth.ledger'],'sample.configure':['sample.configured','truth.ledger'],'power.set':['power.enabled','cycle.phase','cycle.stage','door.locked'],'power.toggle':['power.enabled','cycle.phase','cycle.stage','door.locked'],'door.set_open':['door.open_target'],'thermal.set_temperature':['thermal.target_k','panel.target_c'],'cooling.set_air':['cooling.air_fraction'],'acquisition.start':['cycle.phase','cycle.stage','door.locked'],'acquisition.stop':['cycle.phase','cycle.stage','door.locked'],'emergency.stop':['cycle.phase','cycle.stage','door.locked'],'product.collect':['truth.ledger']}
 evolving=[k for k in slots if k not in ['plant.configured','method.configured','sample.configured','power.enabled','door.open_target','cooling.air_fraction']]
 for k,(inp,out,components,meaning,kind,vis,example) in CAPS.items():
  async_=k=='acquisition.start';query=kind=='query';caps[k]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.malvern_leneo.MalvernLeneoSession.invoke',invocation=f'session.invoke(instance_id="leneo_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if query else [dict(description_zh='单工位、有限库存、显式模型认可、关门/冷态/程序与输出模式守卫',state_ids=['cycle.phase','door.locked','door.angle_rad'],enforced_by='runtime',check='MalvernLeneoRuntime.invoke guards')],state_effects=[] if query else [dict(state_id=i,timing='immediate',description_zh='目标或有限配置事务') for i in immediate.get(k,[])]+[dict(state_id=i,timing='on_step',description_zh='step推进热量、有限倾倒、固化和机械') for i in evolving],observes=[],model_effects=dict(mode='none',description_zh='实际求值后升格',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='实际冷态且玻璃片或溶液接收器非空' if async_ else '目标或查询事务完成',completion_state_ids=['cycle.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['finite_sample_flux_receiver','host_clock','principal_request_cache'],partial_failure_zh='取消/急停保留部分熔体和热量，热态不允许开门'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT','UNKNOWN_REFERENCE','OUT_OF_REFERENCE_RANGE','AMBIGUOUS_PHASE_TRANSITION']],example_arguments=copy.deepcopy(example),verification=[],limitations=['热/流/相变和传感器为未标定教学近似；不是原厂10方法和4模式曲线'],source_ids=['manufacturer_primary','simulation_assumption'])
 mech=dict(component_ids=['heater','crucible','mold','fan'],state_ids=list(slots),meaning_zh='三节点分段程序、混合相变焓、有限摇动溶解/倾倒、风冷玻璃或接收液',implementation='lumped',equations=['P=min(host_heater_W,Kp*(target-Tchamber));P<=20A*supply_V (upper bound,not measured W)','Cchamber*dT/dt=P-UA*(T-Ta)-Qcharge','H=(Ccontainer+m_sample*Cp298_real+m_flux*Cp_host+m_solvent*4180)*(T-Ta)+(m_sample+m_flux)*L_host*liquid_fraction(T)','target is piecewise linear; only step advances heat and geometry','U_next=U*exp(-k_host*liquid_fraction*(1+abs(rock))*dt)','viscosity=mu_host*exp(A_host*(1/T-1/1338.15));finite flow<=available_mass-residual','sample_initial=crucible+receiver+lost+collected;flux_initial=crucible+receiver+collected','electrical=stored+ambient_loss+advected_loss+collected_enthalpy'],parameters={},source_ids=['manufacturer_primary','nist_janaf','simulation_assumption'],limitations=['75真实晶相Cp/H参考和2厂家基体组成，不是50本机熔融曲线。','Cp298常热容外推近似只作为显式模型假设，不把它当高温实测；reference.lookup不外推源表。','208–240V20A不提供实际功率或升温时间；默认3500W和热容非反推标定。','10方法数量已知内容null；4倾倒模式用宿主自定义编号，不伪称原厂曲线。','溶解率、黏度、均匀样品损失、相变焓及冷却阈值未知。','溶液采用单有限水库存和固相溶解代理，过氧化物、溶剂汽化/反应热未实现；高温接液应判定不支持。','粉体应预氧化，未模拟金属/硫化物氧化。','真实元素选择性损失、玻璃结构、XRF/ICP分析、仪器N型内层温度、触屏菜单未实现。','照片估计开门/倾倒轴，非实际机械结构标定；无机器人/硬件在环。'])
 spec=importlib.util.spec_from_file_location('_leneo_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'fusion':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'sample.configure':(SAMPLE,SAMPLE_EXAMPLE)});mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'fusion':mech})

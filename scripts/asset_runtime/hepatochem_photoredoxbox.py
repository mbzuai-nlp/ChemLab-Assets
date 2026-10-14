"""Four-source, multi-vial absorption/dose and fan-only thermal proxy; no yield prediction."""
from __future__ import annotations
from asset_runtime import _annotations as _ann
import bisect,copy,json,math
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT,PHASE
from asset_runtime.antonpaar_abbematadvanced import validate
ROOT=Path(__file__).resolve().parents[2];ASSET_TYPE_ID='instrument.hepatochem_photoredoxbox';CATALOG_ID='CHEM-017';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/hepatochem_photoredoxbox_absorption_library/templates.json'
H=6.62607015e-34;C=299792458.;NA=6.02214076e23;RPM=2*math.pi/60
WAVELENGTHS_NM=[254,275,300,310,340,365,380,390,405,425,427,440,450,467,475,505,525,550,650,690,740,808]
RACKS={'8x4ml':(8,4e-6),'4x20ml':(4,20e-6)}
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
def arr(s,n=None):
 a={'type':'array','items':s}
 if n is not None:a.update(minItems=n,maxItems=n)
 return a
INDEX={'type':'integer','minimum':0,'maximum':7,'x-unit':'1'};FRAC=number('1',0,1);LIFT=number('m',0,.12);TEMP=number('K',273.15,333.15);SPEED=number('rad/s',0,1200*RPM)
LED=obj({'wavelength_m':{'type':'number','enum':[float(str(x)+'e-9') for x in WAVELENGTHS_NM],'x-unit':'m'},'radiant_power_w':number('W',0,25),'intensity':FRAC})
LEDSET=obj({'sources':arr(LED,4),'capture_fraction':arr(arr(FRAC),4)})
THERMAL=obj({'ambient_k':number('K',283.15,308.15),'chamber_capacity_j_k':number('J/K',10,10000),'passive_ua_w_k':number('W/K',0,10),'fan_ua_w_k':number('W/K',0,10),'led_waste_heat_at_full_w':number('W',0,100)})
SAMPLE=obj({'slot':INDEX,'container_id':TEXT,'substance_id':TEXT,'solvent':TEXT,'volume_m3':number('m^3',1e-7,20e-6),'mass_kg':number('kg',1e-5,.1),'concentration_mol_m3':number('mol/m^3',0,100),'path_length_m':number('m',.0001,.05),'container_transmission':FRAC,'heat_capacity_j_k':number('J/K',.1,500),'sample_ua_w_k':number('W/K',0,1),'heat_fraction':FRAC,'initial_temperature_k':TEMP,'compatible_vial':BOOL})
EXAMPLE=dict(slot=0,container_id='photo4ml_01',substance_id='photochemcad_H01',solvent='ethanol',volume_m3=2e-6,mass_kg=.0016,concentration_mol_m3=.1,path_length_m=.01,container_transmission=.9,heat_capacity_j_k=5.,sample_ua_w_k=.05,heat_fraction=.8,initial_temperature_k=298.15,compatible_vial=True)
LED_EXAMPLE=dict(sources=[dict(wavelength_m=275e-9,radiant_power_w=1.,intensity=.5) for _ in range(4)],capture_fraction=[[.05]*8 for _ in range(4)])
POINT=obj({'time_s':number('s',0),'elapsed_s':number('s',0),'indicated_temperature_k':nullable(number('K',0)),'incident_energy_j':number('J',0),'phase':PHASE})
RESULT=obj({'phase':PHASE,'fault':TEXT,'elapsed_s':number('s',0),'indicated_temperature_k':nullable(number('K',0)),'emitted_energy_j':number('J',0),'incident_energy_j':number('J',0),'stir_speed_rad_s':SPEED,'conversion_fraction':{'type':'null'},'yield_fraction':{'type':'null'},'template_source':TEXT,'evidence':TEXT})
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','button_power'],'供电/断电；取消照射，已受光样品及热量保留','device_command','agent',{'enabled':True},['power.enabled','cycle.phase','stir.actual_rad_s']),
 'lid.set_lift':(obj({'lift_m':LIFT}),obj({'lift_m':LIFT}),['lid'],'顶盖虚拟竖直开合；照射中禁止打开','device_command','agent',{'lift_m':.12},['lid.lift_m']),
 'rack.configure':(obj({'mode':{'type':'string','enum':list(RACKS)}}),obj({'configured':BOOL}),['sample_rack'],'空仓开盖更换8×4mL或4×20mL架；宿主选择对应静态几何','configuration','evaluator',{'mode':'8x4ml'},['rack.mode','optics.configured']),
 'led.configure':(LEDSET,obj({'configured':BOOL}),['led_0','led_1','led_2','led_3'],'四个离散LED模块的显式辐射功率/强度/捕获矩阵，不是厂家光场','configuration','evaluator',LED_EXAMPLE,['optics.configured','optics.source_wavelengths_m','optics.intensities']),
 'thermal.configure':(THERMAL,obj({'configured':BOOL}),['cooling_fan','ir_probe'],'空仓初始化环境和显式热参数；无加热器','configuration','evaluator',dict(ambient_k=298.15,chamber_capacity_j_k=500.,passive_ua_w_k=.2,fan_ua_w_k=2.,led_waste_heat_at_full_w=8.),['thermal.configured','truth.chamber_temperature_k','temperature.target_k']),
 'sample.configure':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['sample_rack'],'宿主开盖装瓶；原溶剂吸收模板和热/光学边界显式给定','configuration','evaluator',EXAMPLE,['sample.loaded_count','truth.samples','sample.input_kg','sample.retained_kg','sample.input_m3','sample.retained_m3']),
 'sample.unload':(obj({'slot':INDEX}),obj({'unloaded':BOOL,'attachment_event':TEXT}),['sample_rack'],'停照射停搅拌后开盖移瓶；仅记录宿主事件','configuration','evaluator',{'slot':0},['sample.loaded_count','truth.samples','sample.removed_kg','sample.retained_kg','sample.removed_m3','sample.retained_m3']),
 'method.configure':(obj({'duration_s':number('s',.01,86400)}),obj({'configured':BOOL}),['display_digits'],'设定照射时长；不隐式推进','configuration','agent',{'duration_s':2.},['method.duration_s']),
 'temperature.set':(obj({'target_k':TEMP}),obj({'target_k':TEMP}),['knob_encoder','cooling_fan'],'设风冷目标，低于环境拒绝；不足光热时不能主动升温','device_command','agent',{'target_k':303.15},['temperature.target_k']),
 'stir.set_speed':(obj({'speed_rad_s':SPEED}),obj({'speed_rad_s':SPEED}),['stir_motor'],'0–1200rpm是本模型工程边界，厂家转速范围未知','device_command','agent',{'speed_rad_s':600*RPM},['stir.target_rad_s']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['led_0','led_1','led_2','led_3','ir_probe'],'供电闭盖装瓶且模板覆盖全部点光源时开始照射','device_command','agent',{},['cycle.phase','cycle.elapsed_s','cycle.fault','optics.emitted_energy_j','optics.incident_energy_j']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['led_0','led_1','led_2','led_3'],'取消保留本瓶累计剂量与热量；不回滚','device_command','agent',{},['cycle.phase']),
 'irradiation.measure':(obj({}),RESULT,['display_digits','ir_probe'],'读取照射设定积分和IR温度代理，不报告化学产率','query','agent',{},[]),
 'history.read':(obj({}),obj({'records':arr(POINT),'truncated':BOOL,'template_source':TEXT,'evidence':TEXT}),['display_digits'],'1秒记录最多1000条，纯读不推进','query','agent',{},[]),
 'product.read':(obj({}),obj({'samples':arr(obj({'slot':INDEX,'container_id':TEXT,'substance_id':TEXT,'absorbed_energy_j':number('J',0),'absorbed_photons_mol':number('mol',0),'temperature_k':number('K'),'conversion_fraction':{'type':'null'},'yield_fraction':{'type':'null'}})),'evidence':TEXT}),['sample_rack'],'宿主读取条件性吸收能量与光子账本，真实产品未知','query','evaluator',{},[]),
}
SLOTS={}
def slot(k,s,u,o='derived',v='agent',c='display_digits',w=None):SLOTS[k]=(s,u,o,v,[c],w)
slot('power.enabled',BOOL,'1','setpoint',c='power_inlet',w=('power.set','enabled'));slot('lid.lift_m',LIFT,'m','scene',c='lid',w=('lid.set_lift','lift_m'))
slot('temperature.target_k',TEMP,'K','setpoint',c='cooling_fan',w=('temperature.set','target_k'));slot('stir.target_rad_s',SPEED,'rad/s','setpoint',c='stir_motor',w=('stir.set_speed','speed_rad_s'))
for k,s,u in [('cycle.phase',PHASE,'1'),('cycle.elapsed_s',number('s',0),'s'),('cycle.fault',TEXT,'1'),('rack.mode',TEXT,'1'),('sample.loaded_count',number('1',0,8),'1'),('optics.configured',BOOL,'1'),('thermal.configured',BOOL,'1'),('optics.source_wavelengths_m',arr(number('m'),4),'m'),('optics.intensities',arr(FRAC,4),'1'),('temperature.indicated_k',nullable(number('K',0)),'K'),('stir.actual_rad_s',SPEED,'rad/s'),('fan.fraction',FRAC,'1'),('method.duration_s',number('s',.01,86400),'s'),('optics.emitted_energy_j',number('J',0),'J'),('optics.incident_energy_j',number('J',0),'J')]:slot(k,s,u)
SAMPLE_TRUTH=obj({'slot':INDEX,'temperature_k':number('K'),'mass_kg':number('kg',0),'volume_m3':number('m^3',0),'incident_energy_j':number('J',0),'absorbed_energy_j':number('J',0),'absorbed_photons_mol':number('mol',0)})
slot('truth.samples',arr(SAMPLE_TRUTH),'1','process_truth','evaluator','sample_rack')
for k,u in [('chamber_temperature_k','K'),('thermal_input_j','J'),('ambient_loss_j','J'),('stored_thermal_j','J'),('emitted_energy_j','J'),('absorbed_energy_j','J'),('container_loss_j','J'),('transmitted_energy_j','J'),('uncaptured_energy_j','J'),('nonthermal_energy_j','J')]:slot('truth.'+k,number(u),u,'process_truth','evaluator','sample_rack')
for ax,u in [('kg','kg'),('m3','m^3')]:
 for k in ['input','retained','removed']:slot(f'sample.{k}_{ax}',number(u,0),u,'process_truth','evaluator','sample_rack')
class HepatochemPhotoredoxboxRuntime:
 def __init__(self,instance_id='photoredoxbox_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.lift=0.;self.rack='8x4ml';self.leds=None;self.thermal=None;self.samples={};self.target_k=298.15;self.chamber_k=298.15;self.target_speed=0.;self.actual_speed=0.;self.fan=0.;self.duration_s=2.;self.phase='idle';self.fault='';self.elapsed_s=0.;self.run_start_s=0.;self.operation_id=None;self._operation_count=0;self.history=[];self.history_truncated=False;self.next_record_s=0.;self.run_emitted=0.;self.run_incident=0.;self.input_kg=0.;self.removed_kg=0.;self.input_m3=0.;self.removed_m3=0.;self.removed_stored_j=0.
  self.energy={k:0. for k in ['emitted','absorbed','container_loss','transmitted','uncaptured','nonthermal','thermal_input','ambient_loss']};self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return HepatochemPhotoredoxboxSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def epsilon(self,substance_id,wavelength_nm):
  t=self.templates[substance_id];xs=t['values']['wavelength_nm'];ys=t['values']['epsilon_m2_mol']
  if wavelength_nm<xs[0] or wavelength_nm>xs[-1]:raise ActionRejected('SPECTRAL_RANGE_UNAVAILABLE')
  i=max(0,min(len(xs)-2,bisect.bisect_right(xs,wavelength_nm)-1));return max(0.,ys[i]+(ys[i+1]-ys[i])*(wavelength_nm-xs[i])/(xs[i+1]-xs[i]))
 def _stored(self):
  return (self.thermal['chamber_capacity_j_k']*(self.chamber_k-self.thermal['ambient_k']) if self.thermal else 0.)+sum(s['heat_capacity_j_k']*(s['temperature_k']-s['initial_temperature_k']) for s in self.samples.values())+self.removed_stored_j
 def _values(self):
  vals={'power.enabled':self.powered,'lid.lift_m':self.lift,'temperature.target_k':self.target_k,'stir.target_rad_s':self.target_speed,'cycle.phase':self.phase,'cycle.elapsed_s':self.elapsed_s,'cycle.fault':self.fault,'rack.mode':self.rack,'sample.loaded_count':len(self.samples),'optics.configured':self.leds is not None,'thermal.configured':self.thermal is not None,'optics.source_wavelengths_m':[s['wavelength_m'] for s in self.leds['sources']] if self.leds else [0.]*4,'optics.intensities':[s['intensity'] for s in self.leds['sources']] if self.leds else [0.]*4,'temperature.indicated_k':round(self.chamber_k-273.15,1)+273.15 if self.powered and self.thermal else None,'stir.actual_rad_s':self.actual_speed,'fan.fraction':self.fan,'method.duration_s':self.duration_s,'optics.emitted_energy_j':self.run_emitted,'optics.incident_energy_j':self.run_incident,'truth.chamber_temperature_k':self.chamber_k,'truth.stored_thermal_j':self._stored(),'truth.samples':[dict(slot=i,**{k:s[k] for k in ['temperature_k','mass_kg','volume_m3','incident_energy_j','absorbed_energy_j','absorbed_photons_mol']}) for i,s in sorted(self.samples.items())]}
  for k in self.energy:vals['truth.'+k+('_j' if k in ('thermal_input','ambient_loss','container_loss') else '_energy_j')]=self.energy[k]
  for ax,k in [('kg','mass_kg'),('m3','volume_m3')]:vals['sample.input_'+ax]=getattr(self,'input_'+ax);vals['sample.removed_'+ax]=getattr(self,'removed_'+ax);vals['sample.retained_'+ax]=sum(s[k] for s in self.samples.values())
  return vals
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('debug','evaluator')})
 def debug_snapshot(self):return dict(state=self.read('evaluator'),samples=copy.deepcopy(self.samples),history=copy.deepcopy(self.history))
 def _measure(self):return dict(phase=self.phase,fault=self.fault,elapsed_s=self.elapsed_s,indicated_temperature_k=self._values()['temperature.indicated_k'],emitted_energy_j=self.run_emitted,incident_energy_j=self.run_incident,stir_speed_rad_s=self.actual_speed,conversion_fraction=None,yield_fraction=None,template_source=TEMPLATE_REL,evidence='synthetic_conditioned_dose_and_temperature; measured_literature_absorption_shapes_and_anchors')
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if 'slot' in a and (type(a['slot']) is not int or not 0<=a['slot']<=7):raise ActionRejected('INVALID_ARGUMENT')
  if self.phase=='running' and capability not in ('power.set','acquisition.stop','irradiation.measure','history.read','product.read','temperature.set','stir.set_speed'):raise ActionRejected('BUSY')
  if capability=='power.set':
   self.powered=a['enabled']
   if not self.powered:
    if self.phase=='running':self.phase='cancelled'
    self.actual_speed=0.;self.fan=0.
   out=dict(a)
  elif capability=='lid.set_lift':self.lift=a['lift_m'];out=dict(a)
  elif capability=='rack.configure':
   if self.samples or self.lift<.119:raise ActionRejected('PRECONDITION_FAILED')
   self.rack=a['mode'];self.leds=None;out={'configured':True}
  elif capability=='led.configure':
   if self.lift<.119 or any(len(row)!=RACKS[self.rack][0] or sum(row)>1+1e-12 for row in a['capture_fraction']):raise ActionRejected('INVALID_ARGUMENT')
   total=sum(s['radiant_power_w'] for s in a['sources'])
   if self.thermal and total+self.thermal['led_waste_heat_at_full_w']>100+1e-9:raise ActionRejected('INVALID_ARGUMENT')
   self.leds=copy.deepcopy(a);out={'configured':True}
  elif capability=='thermal.configure':
   if self.samples or self.lift<.119 or self.thermal is not None:raise ActionRejected('PRECONDITION_FAILED')
   if self.leds and sum(s['radiant_power_w'] for s in self.leds['sources'])+a['led_waste_heat_at_full_w']>100+1e-9:raise ActionRejected('INVALID_ARGUMENT')
   self.thermal=dict(a);self.chamber_k=a['ambient_k'];self.target_k=a['ambient_k'];out={'configured':True}
  elif capability=='sample.configure':
   t=self.templates.get(a['substance_id'])
   if self.lift<.119 or a['slot']>=RACKS[self.rack][0] or a['slot'] in self.samples or not self.thermal or not a['compatible_vial'] or a['volume_m3']>RACKS[self.rack][1] or not t or a['solvent']!=t['conditions']['solvent'] or any(s['container_id']==a['container_id'] for s in self.samples.values()):raise ActionRejected('PRECONDITION_FAILED')
   self.samples[a['slot']]=dict(a,temperature_k=a['initial_temperature_k'],incident_energy_j=0.,absorbed_energy_j=0.,absorbed_photons_mol=0.);self.input_kg+=a['mass_kg'];self.input_m3+=a['volume_m3'];out=dict(configured=True,attachment_event='host_attach:'+a['container_id'])
  elif capability=='sample.unload':
   if a['slot'] not in self.samples or self.lift<.119 or self.actual_speed>1e-9:raise ActionRejected('PRECONDITION_FAILED')
   s=self.samples.pop(a['slot']);self.removed_kg+=s['mass_kg'];self.removed_m3+=s['volume_m3'];self.removed_stored_j+=s['heat_capacity_j_k']*(s['temperature_k']-s['initial_temperature_k']);out=dict(unloaded=True,attachment_event='host_release:'+s['container_id'])
  elif capability=='method.configure':self.duration_s=a['duration_s'];out={'configured':True}
  elif capability=='temperature.set':
   if not self.thermal or a['target_k']<self.thermal['ambient_k']:raise ActionRejected('PRECONDITION_FAILED')
   self.target_k=a['target_k'];out=dict(a)
  elif capability=='stir.set_speed':self.target_speed=a['speed_rad_s'];out=dict(a)
  elif capability=='acquisition.start':
   if not self.powered or self.lift>1e-5 or not self.samples or not self.leds or not self.thermal or max([self.chamber_k]+[s['temperature_k'] for s in self.samples.values()])>=333.15:raise ActionRejected('PRECONDITION_FAILED')
   # Validate every loaded sample for every emitting source before making any change.
   for source in self.leds['sources']:
    if source['radiant_power_w']*source['intensity']>0:
     for s in self.samples.values():self.epsilon(s['substance_id'],round(source['wavelength_m']*1e9,6))
   self._operation_count+=1;self.operation_id=f'{self.instance_id}:light:{self._operation_count}';self.phase='running';self.fault='';self.elapsed_s=0.;self.run_emitted=0.;self.run_incident=0.;self.run_start_s=self.time_s;self.history=[];self.history_truncated=False;self.next_record_s=self.time_s+1.;out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase=='running':self.phase='cancelled'
   out={'phase':self.phase}
  elif capability=='history.read':return dict(records=copy.deepcopy(self.history),truncated=self.history_truncated,template_source=TEMPLATE_REL,evidence='synthetic_time_series; not measured PhotoRedOx trace')
  elif capability=='product.read':return dict(samples=[dict(slot=i,**{k:s[k] for k in ['container_id','substance_id','absorbed_energy_j','absorbed_photons_mol','temperature_k']},conversion_fraction=None,yield_fraction=None) for i,s in sorted(self.samples.items())],evidence='conditional_absorption_and_photon_ledger; no_product_identification')
  else:return self._measure()
  self.revision+=1;return copy.deepcopy(out)
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<=0:raise ValueError('finite positive dt required')
  left=float(dt_s)
  while left>1e-10:
   h=min(.02,left);self.actual_speed=self.target_speed if self.powered else 0.;active=self.phase=='running';run_h=min(h,max(0.,self.duration_s-self.elapsed_s)) if active else 0.;heat={i:0. for i in self.samples};waste=0.
   if active:
    for j,src in enumerate(self.leds['sources']):
     emitted=src['radiant_power_w']*src['intensity']*run_h;captured=0.;self.energy['emitted']+=emitted;self.run_emitted+=emitted
     for i,s in self.samples.items():
      incident=emitted*self.leds['capture_fraction'][j][i];captured+=incident;passed=incident*s['container_transmission'];epsilon=self.epsilon(s['substance_id'],round(src['wavelength_m']*1e9,6)) if emitted else 0.;absorbance=epsilon*s['concentration_mol_m3']*s['path_length_m'];absorbed=passed*(-math.expm1(-math.log(10)*absorbance));photons=absorbed*src['wavelength_m']/(NA*H*C)
      s['incident_energy_j']+=incident;s['absorbed_energy_j']+=absorbed;s['absorbed_photons_mol']+=photons;self.run_incident+=incident;self.energy['absorbed']+=absorbed;self.energy['container_loss']+=incident-passed;self.energy['transmitted']+=passed-absorbed;heat[i]+=absorbed*s['heat_fraction'];self.energy['nonthermal']+=absorbed*(1-s['heat_fraction'])
     self.energy['uncaptured']+=emitted-captured
    waste=self.thermal['led_waste_heat_at_full_w']*sum(s['intensity'] for s in self.leds['sources'])/4*run_h;self.elapsed_s+=run_h
    if self.elapsed_s>=self.duration_s-1e-9:self.phase='succeeded'
   if self.thermal:
    t=self.thermal;self.fan=max(0.,min(1.,(self.chamber_k-self.target_k)*.2)) if self.powered else 0.;ambient=(t['passive_ua_w_k']+t['fan_ua_w_k']*self.fan)*(self.chamber_k-t['ambient_k'])*h;transfer=0.
    for i,s in self.samples.items():
     q=s['sample_ua_w_k']*(self.chamber_k-s['temperature_k'])*h;transfer+=q;s['temperature_k']+=(heat[i]+q)/s['heat_capacity_j_k']
    self.chamber_k+=(waste-transfer-ambient)/t['chamber_capacity_j_k'];self.energy['thermal_input']+=waste+sum(heat.values());self.energy['ambient_loss']+=ambient
    if self.phase=='running' and max([self.chamber_k]+[s['temperature_k'] for s in self.samples.values()])>=333.15:self.phase='failed';self.fault='ideal_overtemperature_cutoff_not_certified'
   self.time_s+=h;left-=h
   if self.operation_id and (self.time_s>=self.next_record_s-1e-9 or (active and self.phase!='running')):
    self.history.append(dict(time_s=self.time_s,elapsed_s=self.elapsed_s,indicated_temperature_k=self._values()['temperature.indicated_k'],incident_energy_j=self.run_incident,phase=self.phase));self.next_record_s=self.time_s+1.
    if len(self.history)>1000:self.history.pop(0);self.history_truncated=True
  self.revision+=1;return self.read('evaluator')
class HepatochemPhotoredoxboxSession:
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
        meta=export_hepatochem_photoredoxbox_contract_metadata()
        return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',
          capabilities={k:v for k,v in meta['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},
          state_slots={k:v for k,v in meta['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})

    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id); h=self._host; vals=h.read(self._role); ids=list(vals) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids): raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in vals for k in ids): raise ActionRejected('ACCESS_DENIED')
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},
          observations={k:dict(value=copy.deepcopy(vals[k]),unit=SLOTS[k][1],quality='unavailable' if vals[k] is None else ('estimated' if SLOTS[k][2]=='derived' else 'valid'),sample_time_s=h.time_s,sample_window_s=[h.run_start_s,h.run_start_s+h.elapsed_s] if k in ('optics.emitted_energy_j','optics.incident_energy_j') else None) for k in ids})

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

def export_hepatochem_photoredoxbox_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'HepatochemPhotoredoxboxSession.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key=='acquisition.start'; pre=[]
        conditions={
         'acquisition.start':('供电闭盖、装瓶、四源和热边界已配置，所有发光波长有模板数据',['power.enabled','lid.lift_m','sample.loaded_count','optics.configured','thermal.configured'],'powered and closed and loaded and configured and spectra_supported and below_333.15K'),
         'sample.configure':('宿主开盖、有效空槽、容量/原溶剂匹配且已初始化热边界',['lid.lift_m','rack.mode','thermal.configured'],'open and compatible and empty_slot and volume<=rack_capacity and exact_source_solvent'),
         'sample.unload':('宿主开盖有瓶、停照射停搅拌',['lid.lift_m','cycle.phase','stir.actual_rad_s'],'open and loaded_slot and not_running and speed==0'),
         'rack.configure':('空仓开盖',['lid.lift_m','sample.loaded_count'],'open and empty'),
         'thermal.configure':('空仓开盖、只初始化一次',['lid.lift_m','sample.loaded_count','thermal.configured'],'open and empty and not configured'),
         'temperature.set':('环境已配置且目标不低于环境',['thermal.configured'],'configured and target>=ambient'),
         'lid.set_lift':('照射未运行',['cycle.phase'],'phase!=running')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.hepatochem_photoredoxbox.HepatochemPhotoredoxboxSession.invoke',invocation=f'session.invoke(instance_id="photoredoxbox_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；达到照射时长；取消保留样品吸收剂量与热量' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电不回滚已经发生的照射剂量与热量。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    for k in ('optics.emitted_energy_j','optics.incident_energy_j'):
        slots[k]['sampling']=dict(mode='integral',time_domain='simulation',window_description='本次照射开始至已照射elapsed_s；由显式功率计算，非硬件辐射计测量')
    truth=[k for k in slots if k.startswith('truth.')]
    for key,states,timing in [('acquisition.start',truth+['temperature.indicated_k','fan.fraction'],'on_step'),('sample.configure',truth,'immediate'),('sample.unload',truth,'immediate'),('thermal.configure',truth,'immediate'),('stir.set_speed',['stir.actual_rad_s'],'on_step'),('temperature.set',['fan.fraction'],'on_step'),('power.set',['temperature.indicated_k','fan.fraction'],'immediate')]:
        existing={e['state_id'] for e in caps[key]['state_effects']}
        caps[key]['state_effects'] += [dict(state_id=k,timing=timing,description_zh='操作引起的立即变化或后续step热/光学变化，读本身不推进') for k in states if k not in existing]
    mechanism=dict(component_ids=['sample_rack','led_0','led_1','led_2','led_3','ir_probe','cooling_fan'],state_ids=truth,meaning_zh='原溶剂吸收谱驱动的四源光子/能量账本与风冷热模型；不预测真实合成产物',implementation='lumped',equations=['epsilon_SI=0.1*epsilon_L_mol_cm; linear interpolation only inside measured spectrum, negative baseline clipped zero','A=epsilon_SI*c_mol_m3*path_m; transmitted_fraction=10**(-A)','Eincident_ij=Pj*intensity_j*capture_ij*dt; no redistribution of empty-slot light','Eabsorbed=Eincident*container_transmission*(1-10**(-A)); nphoton=Eabsorbed*lambda/(NA*h*c)','Eemitted=Eabsorbed+Econtainer_loss+Etransmitted+Euncaptured','Eabsorbed=heat_fraction*Eabsorbed+nonthermal_energy; nonthermal is not chemical yield','Cs*dTs=Qabsorbed_heat+UA_sample*(Tchamber-Tsample)*dt','Cc*dTc=LED_waste_heat*dt-sum(sample_transfer)-UA_fan*(Tc-Tambient)*dt','fan=clip(0.2*(Tc-Ttarget),0,1) if powered; no heater or chiller','Ethermal_input=stored_delta_U+removed_delta_U+ambient_loss; retained+removed=loaded mass/volume'],parameters={},source_ids=['absorption_templates','manufacturer_primary','simulation_assumption'],limitations=['Actual spectrum/bandwidth, optical power, irradiance, thermal capacity/UA and all motion dimensions are uncalibrated host inputs or stated proxies.','Four monochromatic modules, solvent exact match; no temperature/concentration dependence, photobleaching, scattering, chemical pathways or yields.','IR holder modeled as chamber lump, not actual liquid truth; fan-only target cannot force heating.','Nominal vial capacities are constraints, not safety fill recommendations.','Default8x4mL Matterix mesh;4x20mL alternative mesh is separately delivered, no automatic scene mesh swap.'])
    _ann.annotate_export('hepatochem_photoredoxbox',caps,slots,{'photon_and_heat_balance':mechanism},inputs={'thermal.configure':(THERMAL,EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'photon_and_heat_balance':mechanism})

"""AS200 finite sieving ledger; host-clock only, not DEM or factory firmware."""
from __future__ import annotations
import bisect,copy,importlib.util,json,math
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.pccell_ed64002 import validate
ROOT=Path(__file__).resolve().parents[2];SLUG='retsch_as200';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-041';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/retsch_as200_particle_library/templates.json'
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled']);LIFT=number('m',.0002,.003)
def array(s,n=None):return dict(type='array',items=s,minItems=n or 0,**({'maxItems':n} if n else {}))
PLANT=obj({'frequency_hz':number('Hz',35,65),'twist_amplitude_rad':number('rad',0,.03),'lift_ramp_m_s':number('m/s',.0001,.05),'dry_material_reviewed':BOOL,'coefficient_evidence':TEXT})
PLANT_EXAMPLE=dict(frequency_hz=47.5,twist_amplitude_rad=.012,lift_ramp_m_s=.005,dry_material_reviewed=True,coefficient_evidence='explicit_host_parameters_not_factory_load_response')
METHOD=obj({'total_lift_m':LIFT,'duration_s':number('s',60,5940),'interval_enabled':BOOL,'interval_drive_s':number('s',1,99)})
METHOD_EXAMPLE=dict(total_lift_m=.0012,duration_s=60.,interval_enabled=True,interval_drive_s=3.)
BIN=obj({'diameter_m':number('m',1e-6,.025),'mass_fraction':number('1',0,1)})
SAMPLE=obj({'sample_id':TEXT,'substance_id':TEXT,'mass_kg':number('kg',1e-9,3),'bins':dict(type='array',items=BIN,minItems=1,maxItems=30),'sieve_apertures_m':array(number('m',40e-6,.025),6),'stack_mass_kg':number('kg',.1,6),'packing_fraction':number('1',.05,1),'maximum_sieve_load_m3':number('m^3',1e-9,.001),'passage_rate_s':number('1/s',0,5),'relative_humidity':number('1',0,1),'humidity_exponent':number('1',0,10),'kinetic_evidence':TEXT})
def example_sample():return dict(sample_id='host_quartz',substance_id='quartz',mass_kg=.03,bins=[dict(diameter_m=d,mass_fraction=f) for d,f in zip([50e-6,100e-6,200e-6,400e-6,800e-6,.0016,.003],[.1,.15,.15,.2,.15,.15,.1])],sieve_apertures_m=[.002,.001,.0005,.00025,.000125,.000063],stack_mass_kg=2.,packing_fraction=.55,maximum_sieve_load_m3=.000126,passage_rate_s=.4,relative_humidity=.2,humidity_exponent=2.,kinetic_evidence='host_discrete_PSD_and_passage_assumptions_not_measured_sieving')
MEASURE=obj({'phase':PHASE,'mode':TEXT,'elapsed_s':number('s',0),'driven_s':number('s',0),'remaining_s':number('s',0),'target_total_lift_m':LIFT,'indicated_total_lift_m':number('m',0,.003),'indicated_acceleration_m_s2':number('m/s^2',0),'evidence':TEXT,'template_source':TEXT})
PRODUCT=obj({'sample_id':TEXT,'substance_id':TEXT,'fraction_mass_kg':array(number('kg',0),7),'collected_mass_kg':number('kg',0),'sample_mass_kg':number('kg',0),'skeletal_volume_m3':number('m^3',0),'bulk_volume_m3':number('m^3',0),'evidence':TEXT,'template_source':TEXT})
CAPS={
 'plant.configure':(PLANT,obj({'configured':BOOL}),['housing','shaker'],'宿主频率/扭动/响应参数与干筛材料审查','configuration','evaluator',PLANT_EXAMPLE),
 'method.configure':(METHOD,obj({'configured':BOOL}),['panel','shaker'],'峰峰行程、60–5940s与固定1s间歇','configuration','agent',METHOD_EXAMPLE),
 'sample.configure':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['cover','pan','sieve_5'],'宿主装入有限样品与六级孔径；不是机器人装筛','configuration','evaluator',example_sample()),
 'mount.configure':(obj({'clamped':BOOL}),obj({'clamped':BOOL}),['cover','tightener_0','tightener_1'],'宿主确认筛组夹紧，不冒充夹紧力传感器','configuration','evaluator',dict(clamped=True)),
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['main_switch','housing'],'主电源教学状态；断电取消未完成筛分','device_command','agent',dict(enabled=True)),
 'cover.set':(obj({'lift_m':number('m',0,.14)}),obj({'lift_m':number('m',0,.14)}),['cover'],'手动盖升降目标；只能停振并解除夹紧','device_command','agent',dict(lift_m=.14)),
 'sieving.set_lift':(obj({'total_lift_m':LIFT}),obj({'total_lift_m':LIFT}),['panel','shaker'],'只改峰峰行程目标，实际包络在step演化','device_command','agent',dict(total_lift_m=.0015)),
 'sieving.set_acceleration':(obj({'acceleration_m_s2':number('m/s^2',9.81,15.1*9.81)}),obj({'total_lift_m':LIFT}),['panel','shaker'],'依据宿主频率将峰值加速度换算总行程，仍受3mm上限','device_command','agent',dict(acceleration_m_s2=50.)),
 'sieving.adjust_lift':(obj({'direction':dict(type='integer',enum=[-1,1],**{'x-unit':'1'})}),obj({'total_lift_m':LIFT}),['button_amplitude_minus','button_amplitude_plus','shaker'],'膜按键单次10µm总行程教学增减；不实现长按固件','device_command','agent',dict(direction=1)),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['button_start','platform'],'夹紧停振闭盖后启动异步筛分','device_command','agent',{}),
 'acquisition.pause':(obj({}),obj({'mode':TEXT}),['button_pause','platform'],'手动暂停冻结总计时，停止穿筛并衰减运动','device_command','agent',{}),
 'acquisition.resume':(obj({}),obj({'mode':TEXT}),['button_start','platform'],'恢复手动暂停；不重置样品或作业所有权','device_command','agent',{}),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['button_stop','platform'],'取消保留分级残留，运动包络随后衰减','device_command','agent',{}),
 'sieving.measure':(obj({}),MEASURE,['panel','display_amplitude_digits','shaker'],'理想化振动读数/时间；没有机内天平','query','agent',{}),
 'product.read':(obj({}),PRODUCT,['sieve_5','pan'],'评价器有限分级质量，不向agent泄漏真值','query','evaluator',{}),
 'product.collect':(obj({'fraction_index':dict(type='integer',minimum=0,maximum=6,**{'x-unit':'1'}),'fraction':number('1',0,1),'recipient_id':TEXT}),obj({'transferred_kg':number('kg',0),'recipient_id':TEXT}),['pan','sieve_5','cover'],'评价器显式有限物料转移；不声称机器人倾倒','device_command','evaluator',dict(fraction_index=6,fraction=.5,recipient_id='host_receiver')),
 'weighing.record':(obj({'fraction_mass_kg':array(number('kg',0,3),7),'original_mass_kg':number('kg',1e-9,3),'measurement_source':TEXT}),obj({'recorded':BOOL}),['pan','panel'],'宿主导入外部天平读数，不由内部真值生成','configuration','evaluator',dict(fraction_mass_kg=[.001]*7,original_mass_kg=.03,measurement_source='external_balance_fixture_not_a_sensor_in_AS200')),
 'weighing.read':(obj({}),obj({'available':BOOL,'fraction_mass_kg':array(number('kg',0)),'recovery_fraction':dict(anyOf=[number('1',0),dict(type='null')]),'measurement_source':TEXT,'evidence':TEXT}),['panel'],'只读实际导入的外部称量；未导入返回缺测','query','agent',{}),
 'reference.lookup':(obj({'substance_id':TEXT}),obj({'density_kg_m3':number('kg/m^3',0),'molar_volume_m3_mol':number('m^3/mol',0),'source':TEXT,'evidence':TEXT,'template_source':TEXT}),['panel'],'真实矿物Vm推导骨架密度，非松装密度','query','agent',dict(substance_id='quartz')),
 'reference.cdf':(obj({'reference_id':TEXT,'diameter_m':number('m',1e-9,.025)}),obj({'cumulative_mass_fraction':number('1',0,1),'source':TEXT,'evidence':TEXT,'template_source':TEXT}),['panel'],'NIST认证CDF区间内插值；禁止外推补尾','query','agent',dict(reference_id='nist_srm_1017b',diameter_m=.0002)),
}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,zh,o='derived',v='agent',c='panel',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=zh
for n in ['plant','method','sample']:slot(n+'.configured',BOOL,'1','宿主'+n+'配置存在','setpoint')
slot('power.enabled',BOOL,'1','主电源教学状态','setpoint',w=('power.set','enabled'));slot('mount.clamped',BOOL,'1','宿主确认的筛组夹紧，不是力传感器','setpoint',c='cover')
slot('cycle.phase',PHASE,'1','异步筛分阶段');slot('cycle.mode',TEXT,'1','筛分、间歇、手动暂停、停止包络或待机')
for k,zh in [('elapsed_s','总运行时间包含1s间歇，不包含手动暂停'),('driven_s','实际驱动时段累计，不包含间歇'),('remaining_s','总设定减已计时')]:slot('cycle.'+k,number('s',0),'s',zh)
slot('sieving.target_total_lift_m',LIFT,'m','振幅设定=峰峰总行程SH，非单侧幅值','setpoint',c='shaker',w=('sieving.set_lift','total_lift_m'))
slot('sieving.indicated_total_lift_m',number('m',0,.003),'m','理想包络读数，非已标定闭环传感器',c='shaker')
slot('sieving.indicated_acceleration_m_s2',number('m/s^2',0),'m/s^2','正弦运动由宿主频率推导的峰值加速度',c='shaker')
slot('shaker.z_m',number('m',-.0015,.0015),'m','筛台相对静止位的垂直位移','scene',c='shaker');slot('shaker.twist_rad',number('rad',-.03,.03),'rad','宿主假设的扭动幅值','scene',c='platform')
slot('cover.lift_m',number('m',0,.14),'m','盖相对静止筛组的位置','scene',c='cover');slot('cover.target_m',number('m',0,.14),'m','手动盖目标','setpoint',c='cover',w=('cover.set','lift_m'))
slot('panel.time_min',number('1',0,99),'1','总计时剩余分钟教学显示');slot('panel.interval_s',number('s',1,99),'s','间歇驱动秒数设定')
slot('truth.fraction_mass_kg',array(number('kg',0),7),'kg','六张筛自顶向下及底盘有限质量','process_truth','evaluator',c='pan')
for k,u,zh in [('sample_mass_kg','kg','装料质量守恒基准'),('collected_mass_kg','kg','已由宿主移到接收器的质量'),('skeletal_volume_m3','m^3','mass/真实参考骨架密度'),('bulk_volume_m3','m^3','骨架体积/宿主堆积率')]:slot('truth.'+k,number(u,0),u,zh,'process_truth','evaluator',c='pan')

class RetschAs200Runtime:
 def __init__(self,instance_id='as200_01'):
  self.instance_id=instance_id;self.plant=self.method=self.sample=None;lib=json.loads((ROOT/TEMPLATE_REL).read_text());self.templates={r['substance_id']:r for r in lib['templates']};self.references={r['substance_id']:r for r in lib['certified_psd_references']};self.powered=self.clamped=False;self.phase='idle';self.mode='idle';self.time_s=self.elapsed_s=self.driven_s=self.segment_s=0.;self.total_lift=self.wave_phase=self.z=self.twist=0.;self.cover=self.cover_target=.14;self.mass=self.collected=self.skeletal_volume=self.bulk_volume=0.;self.ledger=[[0.] for _ in range(7)];self.rates=[];self.weighings=None;self.revision=0;self.operation_id='';self._operation_count=0;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility):
  if visibility not in ['agent','debug','evaluator'] or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return RetschAs200Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _target(self):return self.method['total_lift_m'] if self.method else .0002
 def _remaining(self):return max(0,self.method['duration_s']-self.elapsed_s) if self.method else 0.
 def _values(self):return {**{n+'.configured':bool(getattr(self,n)) for n in ['plant','method','sample']},'power.enabled':self.powered,'mount.clamped':self.clamped,'cycle.phase':self.phase,'cycle.mode':self.mode,'cycle.elapsed_s':self.elapsed_s,'cycle.driven_s':self.driven_s,'cycle.remaining_s':self._remaining(),'sieving.target_total_lift_m':self._target(),'sieving.indicated_total_lift_m':self.total_lift,'sieving.indicated_acceleration_m_s2':(math.tau*self.plant['frequency_hz'])**2*self.total_lift/2 if self.plant else 0.,'shaker.z_m':self.z,'shaker.twist_rad':self.twist,'cover.lift_m':self.cover,'cover.target_m':self.cover_target,'panel.time_min':self._remaining()/60,'panel.interval_s':self.method['interval_drive_s'] if self.method else 1.,'truth.fraction_mass_kg':[sum(x) for x in self.ledger],'truth.sample_mass_kg':self.mass,'truth.collected_mass_kg':self.collected,'truth.skeletal_volume_m3':self.skeletal_volume,'truth.bulk_volume_m3':self.bulk_volume}
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ['debug','evaluator']})
 def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in ['templates','references']})
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if (self.phase=='running' or self.total_lift>1e-12) and capability in ['plant.configure','method.configure','sample.configure','mount.configure','cover.set','product.collect','weighing.record']:raise ActionRejected('BUSY')
  if capability=='plant.configure':
   if self.plant or not a['dry_material_reviewed']:raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='method.configure':
   if self.elapsed_s or not self.plant:raise ActionRejected('PRECONDITION_FAILED')
   if a['duration_s']%60 or a['interval_drive_s']%1:raise ActionRejected('INVALID_ARGUMENT')
   if (math.tau*self.plant['frequency_hz'])**2*a['total_lift_m']/2>15.1*9.81+1e-9:raise ActionRejected('REFERENCE_RANGE')
   self.method=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='sample.configure':
   if self.sample or not self.plant or self.cover<.139 or self.clamped:raise ActionRejected('PRECONDITION_FAILED')
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   aps=a['sieve_apertures_m'];bins=a['bins'];density=self.templates[a['substance_id']]['value'];sv=a['mass_kg']/density;bv=sv/a['packing_fraction']
   if any(aps[i]<=aps[i+1] for i in range(5)) or abs(sum(x['mass_fraction'] for x in bins)-1)>1e-10 or any(bins[i]['diameter_m']>=bins[i+1]['diameter_m'] for i in range(len(bins)-1)):raise ActionRejected('INVALID_ARGUMENT')
   if bv>a['maximum_sieve_load_m3']:raise ActionRejected('SIEVE_LOAD_LIMIT')
   self.sample=copy.deepcopy(a);self.mass=a['mass_kg'];self.skeletal_volume=sv;self.bulk_volume=bv;self.ledger=[[self.mass*x['mass_fraction'] for x in bins]]+[[0.]*len(bins) for _ in range(6)];self.rates=[[a['passage_rate_s']*max(0,1-x['diameter_m']/ap)**2*(1-a['relative_humidity'])**a['humidity_exponent'] for x in bins] for ap in aps];out=dict(configured=True,attachment_event='host_ledger_initialization_not_robot_loading')
  elif capability=='mount.configure':
   if not self.sample or (a['clamped'] and (self.cover>1e-9 or self.cover_target>1e-9)):raise ActionRejected('PRECONDITION_FAILED')
   self.clamped=a['clamped'];out=a
  elif capability=='power.set':
   self.powered=a['enabled']
   if not self.powered and self.phase=='running':self.phase='cancelled';self.mode='stopping'
   out=a
  elif capability=='cover.set':
   if self.clamped:raise ActionRejected('PRECONDITION_FAILED')
   self.cover_target=a['lift_m'];out=a
  elif capability in ['sieving.set_lift','sieving.set_acceleration','sieving.adjust_lift']:
   if not self.method or not self.plant:raise ActionRejected('PRECONDITION_FAILED')
   v=a['total_lift_m'] if capability=='sieving.set_lift' else (2*a['acceleration_m_s2']/(math.tau*self.plant['frequency_hz'])**2 if capability=='sieving.set_acceleration' else min(.003,max(.0002,self._target()+a['direction']*.00001)))
   if not .0002<=v<=.003:raise ActionRejected('REFERENCE_RANGE')
   if (math.tau*self.plant['frequency_hz'])**2*v/2>15.1*9.81+1e-9:raise ActionRejected('REFERENCE_RANGE')
   self.method['total_lift_m']=v;out=dict(total_lift_m=v)
  elif capability=='acquisition.start':
   if not all([self.plant,self.method,self.sample,self.powered,self.clamped]) or self.phase!='idle' or self.cover>1e-9 or self.cover_target>1e-9 or self.total_lift>1e-9:raise ActionRejected('PRECONDITION_FAILED')
   self.phase='running';self.mode='sieving';self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.pause':
   if self.phase!='running' or self.mode not in ['sieving','interval']:raise ActionRejected('PRECONDITION_FAILED')
   self.mode='paused';out=dict(mode=self.mode)
  elif capability=='acquisition.resume':
   if self.phase!='running' or self.mode!='paused' or not self.powered:raise ActionRejected('PRECONDITION_FAILED')
   self.mode='interval' if self.method['interval_enabled'] and self.segment_s>=self.method['interval_drive_s'] else 'sieving';out=dict(mode=self.mode)
  elif capability=='acquisition.stop':
   if self.phase=='running':self.phase='cancelled';self.mode='stopping'
   out=dict(phase=self.phase)
  elif capability=='sieving.measure':out=dict(phase=self.phase,mode=self.mode,elapsed_s=self.elapsed_s,driven_s=self.driven_s,remaining_s=self._remaining(),target_total_lift_m=self._target(),indicated_total_lift_m=self.total_lift,indicated_acceleration_m_s2=self._values()['sieving.indicated_acceleration_m_s2'],evidence='ideal_motion_proxy_no_integral_balance_or_calibrated_sensor',template_source=TEMPLATE_REL)
  elif capability=='product.read':
   if not self.sample:raise ActionRejected('PRECONDITION_FAILED')
   out=dict(sample_id=self.sample['sample_id'],substance_id=self.sample['substance_id'],fraction_mass_kg=[sum(x) for x in self.ledger],collected_mass_kg=self.collected,sample_mass_kg=self.mass,skeletal_volume_m3=self.skeletal_volume,bulk_volume_m3=self.bulk_volume,evidence='synthetic_finite_sieving_ledger_real_reference_density_not_measured_fraction_weights',template_source=TEMPLATE_REL)
  elif capability=='product.collect':
   if not self.sample or self.cover<.139 or self.clamped or not a['recipient_id']:raise ActionRejected('PRECONDITION_FAILED')
   i=a['fraction_index'];amount=sum(self.ledger[i])*a['fraction'];self.ledger[i]=[m*(1-a['fraction']) for m in self.ledger[i]];self.collected+=amount;self.weighings=None;out=dict(transferred_kg=amount,recipient_id=a['recipient_id'])
  elif capability=='weighing.record':
   if not self.sample or not a['measurement_source']:raise ActionRejected('PRECONDITION_FAILED')
   self.weighings=copy.deepcopy(a);out=dict(recorded=True)
  elif capability=='weighing.read':out=dict(available=self.weighings is not None,fraction_mass_kg=copy.deepcopy(self.weighings['fraction_mass_kg']) if self.weighings else [],recovery_fraction=sum(self.weighings['fraction_mass_kg'])/self.weighings['original_mass_kg'] if self.weighings else None,measurement_source=self.weighings['measurement_source'] if self.weighings else '',evidence='host_supplied_external_balance_values_no_integral_sensor')
  elif capability=='reference.lookup':
   if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
   r=self.templates[a['substance_id']];out=dict(density_kg_m3=r['value'],molar_volume_m3_mol=r['molar_volume_cm3_mol']*1e-6,source=r['source'],evidence='derived_from_real_PHREEQC_solid_Vm',template_source=TEMPLATE_REL)
  elif capability=='reference.cdf':
   if a['reference_id'] not in self.references:raise ActionRejected('UNKNOWN_REFERENCE')
   r=self.references[a['reference_id']];vs=r['values'];ds=[x['diameter_m'] for x in vs];d=a['diameter_m']
   if not ds[0]<=d<=ds[-1]:raise ActionRejected('REFERENCE_RANGE')
   i=min(len(ds)-2,max(0,bisect.bisect_right(ds,d)-1));v=vs[i]['cumulative_mass_fraction']+(vs[i+1]['cumulative_mass_fraction']-vs[i]['cumulative_mass_fraction'])*(d-ds[i])/(ds[i+1]-ds[i]);out=dict(cumulative_mass_fraction=v,source=r['source'],evidence='derived_linear_interpolation_of_certified_NIST_mass_CDF_no_tail_extrapolation',template_source=TEMPLATE_REL)
  validate(CAPS[capability][1],out)
  if CAPS[capability][4]!='query':self.revision+=1
  return copy.deepcopy(out)
 def step(self,dt_s):
  validate(number('s',0,360000),dt_s);remaining=float(dt_s)
  while remaining>1e-10:
   h=min(.01,remaining);timed=self.phase=='running' and self.mode in ['sieving','interval'];drive=timed and self.mode=='sieving';p=self.plant
   if timed:
    left=self._remaining()
    if left<=1e-9:self.mode='stopping';continue
    h=min(h,left)
    if self.method['interval_enabled']:
     end=self.method['interval_drive_s']+(1. if self.mode=='interval' else 0.);h=min(h,max(0,end-self.segment_s))
     if h<1e-10:
      if self.mode=='sieving':self.mode='interval'
      else:self.mode='sieving';self.segment_s=0.
      continue
   old=self.total_lift;target=self._target() if drive and self.powered else 0.;ramp=p['lift_ramp_m_s'] if p else .005;self.total_lift+=max(-ramp*h,min(ramp*h,target-self.total_lift));avg=(old+self.total_lift)/2
   if drive and self.sample:
    factor=(avg/.001)**2;changes=[[m*(-math.expm1(-k*factor*h)) for m,k in zip(layer,rs)] for layer,rs in zip(self.ledger[:6],self.rates)]
    for i in range(6):
     for j,m in enumerate(changes[i]):self.ledger[i][j]-=m;self.ledger[i+1][j]+=m
    self.driven_s+=h;self.weighings=None
   if timed:self.elapsed_s+=h;self.segment_s+=h
   if p and self.total_lift>0:self.wave_phase=(self.wave_phase+math.tau*p['frequency_hz']*h)%math.tau
   self.z=self.total_lift/2*math.sin(self.wave_phase);self.twist=p['twist_amplitude_rad']*(self.total_lift/.003)*math.sin(self.wave_phase+math.pi/2) if p else 0.
   self.cover+=max(-.1*h,min(.1*h,self.cover_target-self.cover));self.time_s+=h;remaining-=h;self.revision+=1
   if self.phase=='running' and self.mode=='stopping' and self.total_lift<=1e-12:self.phase='succeeded';self.mode='idle'
   elif self.phase!='running' and self.total_lift<=1e-12:self.mode='idle'
  return self.read()

class RetschAs200Session:
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
        meta=export_retsch_as200_contract_metadata()
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('sieving.measure'))

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])

def export_retsch_as200_contract_metadata():
    caps={};slots={}
    for k,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'RetschAs200Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    immediate={'plant.configure':['plant.configured'],'method.configure':['method.configured','sieving.target_total_lift_m','cycle.remaining_s','panel.time_min','panel.interval_s'],'sample.configure':['sample.configured','truth.fraction_mass_kg','truth.sample_mass_kg','truth.skeletal_volume_m3','truth.bulk_volume_m3'],'mount.configure':['mount.clamped'],'power.set':['power.enabled','cycle.phase','cycle.mode'],'cover.set':['cover.target_m'],'sieving.set_lift':['sieving.target_total_lift_m'],'sieving.set_acceleration':['sieving.target_total_lift_m'],'sieving.adjust_lift':['sieving.target_total_lift_m'],'acquisition.start':['cycle.phase','cycle.mode'],'acquisition.pause':['cycle.mode'],'acquisition.resume':['cycle.mode'],'acquisition.stop':['cycle.phase','cycle.mode'],'product.collect':['truth.fraction_mass_kg','truth.collected_mass_kg']}
    advancing=['cycle.phase','cycle.mode','cycle.elapsed_s','cycle.driven_s','cycle.remaining_s','panel.time_min','sieving.indicated_total_lift_m','sieving.indicated_acceleration_m_s2','shaker.z_m','shaker.twist_rad','truth.fraction_mass_kg']
    for k,(inp,out,components,meaning,kind,vis,example) in CAPS.items():
        async_=k=='acquisition.start'
        caps[k]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.retsch_as200.RetschAs200Session.invoke',invocation=f'session.invoke(instance_id="as200_01",capability_id={k!r},arguments={example!r},request_id="unique")'),preconditions=[] if kind=='query' else [dict(description_zh='宿主干筛审查、6筛+盘有限装料、停振操作、闭盖夹紧、孔径排序与参考范围；拒绝无副作用',state_ids=['cycle.phase','sample.configured','power.enabled','cover.lift_m','mount.clamped'],enforced_by='runtime',check='RetschAs200Runtime.invoke capability-specific guards')],state_effects=[dict(state_id=i,timing='immediate',description_zh='配置或目标写入，不推进物理时间') for i in immediate.get(k,[])]+([dict(state_id=i,timing='on_step',description_zh='有限穿筛、运动包络、间歇计时') for i in advancing] if async_ else [])+([dict(state_id='cover.lift_m',timing='on_step',description_zh='0.1m/s宿主手动升盖教学代理')] if k=='cover.set' else []),observes=[],model_effects=dict(mode='none',description_zh='唯有实际求值验证后升格；按键与数字区是有限教学子集',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='含间歇总时间完成且振动包络归零，不保证筛分终点' if async_ else '同步返回目标/配置，状态仅step推进',completion_state_ids=['cycle.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['six_200mm_sieves','finite_mass_ledger','host_external_weighing','principal_request_cache'],partial_failure_zh='取消保留已转移分级质量，随后停止运动；不回滚到原始PSD。'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT','UNKNOWN_REFERENCE','REFERENCE_RANGE','SIEVE_LOAD_LIMIT']],example_arguments=copy.deepcopy(example),verification=[],limitations=['非厂家固件、DEM、机器人/硬件控制或安全认证；会话权限不替代文件系统隔离。'],source_ids=['manufacturer_primary','manufacturer_manual','simulation_assumption'])
    mechanism=dict(component_ids=['shaker','platform','cover','pan','sieve_5','panel'],state_ids=list(slots),meaning_zh='峰峰振幅/扭动与有限质量逐层穿筛，真实参考密度及独立NIST曲线',implementation='lumped',equations=['z=SH/2*sin(2*pi*f_host*t); a_peak=(2*pi*f_host)^2*SH/2; envelope approaches setpoint at host ramp','theta=theta_host*(SH/3mm)*cos(phase); kinematic teaching motion, not contact dynamics','rate_ij=k_host*max(0,1-d_j/aperture_i)^2*(1-RH_host)^humidity_exponent*(SH/1mm)^2','Each dt<=.01s transfers old_layer_mass*(1-exp(-rate*dt)) to next layer simultaneously; nonnegative and exactly mass conservative, finite timestep approximation','sum(all_sieves_and_pan)+collected=initial_mass; skeletal_volume=mass/reference_density; bulk_volume=skeletal_volume/packing_fraction_host','Timer counts interval_drive plus fixed1s pause; manual pause freezes timer; stop preserves ledger','Real density=M/Vm from USGS solid phases at25C; not a measured PSD or bulk density','Public reference.cdf interpolates only within certified NIST mass-finer table domain; no fabricated tails','Weighing read returns only externally supplied values; never derives sensor data from hidden ledger'],parameters={},source_ids=['manufacturer_primary','manufacturer_manual','usgs_sit','phreeqc_definition','nist_particle','simulation_assumption'],limitations=['Six fixed200mm sieves+pan; no wet sieving/aids, sieve swapping robot or full620mmstack/22fractions.','183 independent mineral formulas with real Vm-derived density, not183 measured sieve curves; two NIST CDFs share soda-limeglass chemistry.','Passage probability, humidity, packing fraction, frequency, torsion and transient response require calibration; no DEM, mesh contact, agglomeration or breakage.','Current dry subset aperture>=40um; manufacturer20um applies with conditions not implemented.','Host maximum sieve load volume must be reviewed for each method;3kg ceiling alone is not safe fine-sieve loading.','No integral balance; external values may disagree with model mass and are not auto-corrected to hidden truth.','Finite-step conservative transfer is not an exact continuous-time chain solver; no certified sieve analysis or hardware safety claims.','Manufacturer load-dependent achievable amplitude is not calibrated; prescribed waveform is an ideal teaching target.','Body35kg excludes clamp/sieves; assembly total mass and real component inertia unknown.','No USB/EasySieve driver, full99SOP menu, automatic long-press/continuous99min-plus firmware.'])
    spec=importlib.util.spec_from_file_location('_as200_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'sieving':mechanism},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'sieving':mechanism})

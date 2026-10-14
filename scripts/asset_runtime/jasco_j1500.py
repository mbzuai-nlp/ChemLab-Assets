"""J-1500-150 standard-PMT teaching process, measured SP175 CD and separately supplied UV."""
from __future__ import annotations
import copy,hashlib,importlib.util,json,math
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2];SLUG='jasco_j1500';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-065';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/jasco_j1500_cd_library/templates.json'
R=8.31446261815324;STD_T=273.15;STD_P=101325.;MDEG=math.pi/180000;CD_FACTOR=math.log(10)/4;CD_LIMIT=8000*MDEG
BOOL={'type':'boolean'};TEXT={'type':'string'}
class ActionRejected(ValueError):pass
def number(unit,low=None,high=None):
 s={'type':'number','x-unit':unit}
 if low is not None:s['minimum']=low
 if high is not None:s['maximum']=high
 return s
def obj(p):return dict(type='object',properties=p,required=list(p),additionalProperties=False)
def arr(s,n=None):
 x=dict(type='array',items=s)
 if n is not None:x.update(minItems=n,maxItems=n)
 return x
def integer(low,high):return dict(number('1',low,high),type='integer')
def validate(s,v):
 t=s.get('type')
 if t in ('number','integer'):
  if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):raise ActionRejected('INVALID_ARGUMENT')
  if t=='integer' and type(v) is not int:raise ActionRejected('INVALID_ARGUMENT')
  if not s.get('minimum',-math.inf)<=v<=s.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='boolean' and type(v) is not bool:raise ActionRejected('INVALID_ARGUMENT')
 elif t=='string' and not isinstance(v,str):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='object':
  if not isinstance(v,dict) or not set(s['required'])<=v.keys():raise ActionRejected('INVALID_ARGUMENT')
  for k,x in v.items():
   if k not in s['properties']:raise ActionRejected('INVALID_ARGUMENT')
   validate(s['properties'][k],x)
 elif t=='array':
  if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
  for x in v:validate(s['items'],x)
 if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])

def nullable(s):return {'anyOf':[s,{'type':'null'}]}
PLANT=obj({'ambient_k':number('K',288.15,303.15),'ambient_pa':number('Pa',90000,110000),'purge_volume_m3':number('m^3',1e-5,.1),'initial_n2_mol':number('mol',0,10000),'initial_air_fraction':number('1',0,1),'accept_purged_snapshot':BOOL,'lamp_warmup_s':number('s',.01,3600),'step_settle_s':number('s',0,10),'noise_multiplier':number('1',0,100),'cd_offset_rad':number('rad',-CD_LIMIT,CD_LIMIT),'seed':integer(0,2147483647),'accept_uncalibrated_model':BOOL})
PLANT_EXAMPLE=dict(ambient_k=298.15,ambient_pa=101325.,purge_volume_m3=.001,initial_n2_mol=2.,initial_air_fraction=1.,accept_purged_snapshot=False,lamp_warmup_s=5.,step_settle_s=.01,noise_multiplier=1.,cd_offset_rad=.002*MDEG,seed=65,accept_uncalibrated_model=True)
FAST_PLANT=dict(PLANT_EXAMPLE,initial_air_fraction=.001,accept_purged_snapshot=True,noise_multiplier=0.)
SOLUTION=obj({'sample_id':TEXT,'kind':dict(type='string',enum=['blank','sample']),'template_id':TEXT,'residue_concentration_mol_m3':number('mol/m^3',0,1000),'path_length_m':number('m',.0001,.020),'cell_shape':dict(type='string',enum=['rectangular','cylindrical']),'volume_m3':number('m^3',1e-10,.00001),'solvent_id':TEXT,'mean_epsilon_m2_mol':arr(number('m^2/mol',0,1e7),66),'solvent_alpha_m_inv':arr(number('m^-1',0,1e7),66),'solvent_delta_alpha_m_inv':arr(number('m^-1',-1e7,1e7),66),'accept_synthetic_uv':BOOL,'accept_unknown_reference_conditions':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='sample_01',kind='sample',template_id='sp175_hsa',residue_concentration_mol_m3=.2,path_length_m=.001,cell_shape='rectangular',volume_m3=2e-7,solvent_id='buffer_01',mean_epsilon_m2_mol=[600.]*66,solvent_alpha_m_inv=[50.]*66,solvent_delta_alpha_m_inv=[.05]*66,accept_synthetic_uv=True,accept_unknown_reference_conditions=True)
BLANK_EXAMPLE=dict(SAMPLE_EXAMPLE,sample_id='blank_01',kind='blank',template_id='',residue_concentration_mol_m3=0.,mean_epsilon_m2_mol=[0.]*66)
METHOD=obj({'start_m':number('m',163e-9,950e-9),'stop_m':number('m',163e-9,950e-9),'interval_m':number('m',.025e-9,20e-9),'scan_mode':dict(type='string',enum=['continuous','step','auto_dit']),'speed_m_s':number('m/s',1e-9/60,10000e-9/60),'bandwidth_m':number('m',.01e-9,16e-9),'integration_s':number('s',.0001,30),'accumulations':integer(1,20)})
METHOD_EXAMPLE=dict(start_m=230e-9,stop_m=190e-9,interval_m=1e-9,scan_mode='step',speed_m_s=100e-9/60,bandwidth_m=1e-9,integration_s=.05,accumulations=1)
POINT=obj({'wavelength_m':number('m',163e-9,950e-9),'ellipticity_rad':number('rad'),'absorbance':number('1'),'raw_ellipticity_rad':number('rad'),'raw_absorbance':number('1'),'transmittance':number('1',0),'sample_time_s':number('s',0),'integration_s':number('s',.0001,30),'accumulation':integer(1,20)})
RESULT=obj({'phase':PHASE,'kind':TEXT,'complete':BOOL,'points':arr(POINT),'warning':TEXT,'template_source':TEXT,'cd_evidence':TEXT,'absorbance_evidence':TEXT,'limitations':arr(TEXT)})
MEASURE=obj({'phase':PHASE,'wavelength_m':nullable(number('m')),'ellipticity_rad':nullable(number('rad')),'absorbance':nullable(number('1')),'elapsed_s':number('s',0),'completed_points':integer(0,20480),'warning':TEXT})
FIT=obj({'fractions':obj({k:number('1',0,1) for k in ['Helix','Sheet','Turn','Disorder']}),'weights':arr(number('1',0,1),71),'reference_ids':arr(TEXT,71),'rms_delta_epsilon_m2_mol':number('m^2/mol',0),'iterations':integer(0,20000),'converged':BOOL,'method':TEXT,'evidence':TEXT,'template_source':TEXT,'limitations':arr(TEXT)})
LEDGER=obj({'n2_initial_mol':number('mol',0),'n2_remaining_mol':number('mol',0),'n2_throughput_mol':number('mol',0),'purge_label_in_chamber_mol':number('mol',0),'purge_label_initial_mol':number('mol',0),'purge_label_exhaust_mol':number('mol',0),'nitrogen_source_residual_mol':number('mol'),'purge_label_residual_mol':number('mol'),'solution_registered_m3':number('m^3',0),'solution_attached_m3':number('m^3',0),'solution_external_m3':number('m^3',0),'solution_residual_m3':number('m^3'),'active_template_id':TEXT,'air_fraction':number('1',0,1),'evidence':TEXT})
TEMPLATE=obj({'substance_id':TEXT,'wavelength_m':arr(number('m'),66),'delta_epsilon_m2_mol':arr(number('m^2/mol'),66),'unit':TEXT,'source':TEXT,'evidence':TEXT,'conditions_json':TEXT})
CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},[])
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body'],'显式一次性氮气/环境/噪声/暖机代理','configuration','evaluator',PLANT_EXAMPLE)
cap('solution.register',SOLUTION,obj({'registered':BOOL}),['cell_holder'],'宿主注册已填充样品池与独立UV输入','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.load',obj({'sample_id':TEXT}),obj({'loaded':BOOL,'attachment_event':TEXT}),['cell_holder','sample_cell'],'开盖由宿主挂接预填池，不自动移液','device_command','evaluator',{'sample_id':'sample_01'})
cap('sample.unload',obj({'recipient_id':TEXT}),obj({'unloaded':BOOL,'attachment_event':TEXT}),['cell_holder','sample_cell'],'开盖卸下池，全部液体回外部账本','device_command','evaluator',{'recipient_id':'external_rack'})
cap('method.configure',METHOD,obj({'configured':BOOL}),['monochromator','host_screen'],'波长/扫描/带宽/DIT/累积；修改使空白失效','configuration','agent',METHOD_EXAMPLE)
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet'],'供电逻辑，关机停灯关快门并使空白失效',ex={'enabled':True})
cap('purge.set',obj({'flow_std_m3_s':number('m^3/s',0,30e-3/60)}),obj({'flow_std_m3_s':number('m^3/s',0,30e-3/60)}),['nitrogen_inlet'],'有限氮气；标准体积参考273.15K/101325Pa为显式宿主假设',ex={'flow_std_m3_s':2e-3/60})
cap('lid.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['sample_lid'],'开盖取消扫描、关闭快门；单区吹扫污染重置',ex={'opened':False})
cap('lamp.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['xe_lamp'],'150W灯逻辑，暖机由step推进；未模拟真实光子通量',ex={'enabled':True})
cap('shutter.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['shutter'],'光学快门工程行程；开盖/未准备不能开启',ex={'opened':True})
cap('acquisition.start',obj({'kind':dict(type='string',enum=['blank','sample'])}),obj({'phase':PHASE,'operation_id':TEXT}),['sample_cell','pem','pmt','host_screen'],'空白或样品异步扫描，两条偏振吸收通道计算CD，独立平均吸收',ex={'kind':'sample'})
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['shutter','host_screen'],'关闭快门并保留已采集点；取消不伪造完整谱')
cap('process.measure',obj({}),MEASURE,['host_screen'],'只读当前最后实际采集点，不推进时间','query')
cap('spectrum.read',obj({}),RESULT,['host_screen'],'已采集点/空白扣除/证据与边界','query')
cap('structure.fit',obj({'residue_concentration_mol_m3':number('mol/m^3',1e-12,1000),'path_length_m':number('m',.0001,.02)}),FIT,['host_screen'],'以调用者提供浓度光程作非负单纯形参考组合；不读取隐藏身份','analysis','agent',{'residue_concentration_mol_m3':.2,'path_length_m':.001})
cap('template.read',obj({'substance_id':TEXT}),TEMPLATE,['host_screen'],'读取公共参考谱，不揭示当前样品模板身份','query','agent',{'substance_id':'sp175_hsa'})
cap('product.read',obj({}),LEDGER,['cell_holder','nitrogen_inlet'],'私有气源与挂接液体守恒账本','query','evaluator')
cap('thermal.set_temperature',obj({'temperature_k':number('K',243.15,403.15)}),obj({}),['cell_holder'],'标准CH434无主动温控；PTC517未安装，明确UNSUPPORTED','configuration','agent',{'temperature_k':298.15})
UNSUPPORTED={'thermal.set_temperature'}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='host_screen',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
slot('plant.configured',BOOL,'1','宿主显式初始化','setpoint',c='body')
slot('method.configured',BOOL,'1','有效方法设定','setpoint')
slot('power.enabled',BOOL,'1','逻辑供电','setpoint',c='power_inlet',w=('power.set','enabled'))
slot('lid.opened',BOOL,'1','样品室盖开启','scene',c='sample_lid',w=('lid.set','opened'))
slot('lid.angle_rad',number('rad',0,math.pi/2),'rad','向前绕-X开启90°工程代理','scene',c='sample_lid')
slot('lamp.enabled',BOOL,'1','氙灯逻辑使能','setpoint',c='xe_lamp',w=('lamp.set','enabled'))
slot('lamp.ready',BOOL,'1','显式宿主暖机时间已达标；非厂家实测','derived',c='xe_lamp')
slot('shutter.opened',BOOL,'1','快门逻辑开启','scene',c='shutter',w=('shutter.set','opened'))
slot('shutter.travel_m',number('m',0,.025),'m','快门沿+X25mm代理','scene',c='shutter')
slot('sample.loaded',BOOL,'1','宿主已填充池挂接状态','scene',c='sample_cell')
slot('sample.path_length_m',nullable(number('m',.0001,.02)),'m','池标称光程；不是隐藏浓度','setpoint',c='cell_holder')
slot('sample.temperature_k',number('K',288.15,303.15),'K','标准无温控架，模型平衡环境温度；非实时温度传感器','derived',c='cell_holder')
slot('purge.flow_std_m3_s',number('m^3/s',0,30e-3/60),'m^3/s','氮气设定标准流量，有限源耗尽后实际归零','setpoint',c='nitrogen_inlet',w=('purge.set','flow_std_m3_s'))
slot('purge.actual_std_m3_s',number('m^3/s',0,30e-3/60),'m^3/s','当有限气源还有气时的模型流量','derived',c='nitrogen_inlet')
slot('purge.ready',BOOL,'1','单区空气示踪比例≤0.02，工程阈值','derived',c='nitrogen_inlet')
slot('blank.ready',BOOL,'1','同方法/光程/溶剂空白完整可用')
slot('scan.phase',PHASE,'1','异步操作生命周期')
slot('scan.kind',TEXT,'1','blank或sample')
slot('scan.elapsed_s',number('s',0),'s','本次采集宿主经过时间')
slot('scan.completed_points',integer(0,20480),'1','已完成波长点数，取消保留')
slot('scan.wavelength_m',nullable(number('m')),'m','最后实际采集波长；无数据null')
slot('scan.ellipticity_rad',nullable(number('rad')),'rad','模板经双通道/带宽/DIT噪声与空白扣除的CD角，不是UV')
slot('scan.absorbance',nullable(number('1')),'1','独立host吸收系数的估计吸光度；非CD反推')
slot('safety.warning',TEXT,'1','预先过滤的不包含样品身份的状态告警')
slot('truth.ledger',LEDGER,'1','有限源、液体位置和活动模板私有真值','process_truth','evaluator','cell_holder')
for k in CAPS:
 if CAPS[k][4]!='query' and k not in UNSUPPORTED:CAPS[k][7].extend(SLOTS)
class JascoJ1500Runtime:
 def __init__(self,instance_id='j1500_01'):
  self.instance_id=instance_id;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.time_s=0.;self.revision=0;self.plant=None;self.method=None;self.samples={};self.active=None
  self.powered=False;self.opened=True;self.lamp=False;self.lamp_age=0.;self.shutter=False;self.flow=0.;self.n2=0.;self.throughput=0.;self.air=1.;self.initial_label=0.;self.exhaust_label=0.;self.blank=None;self.phase='idle';self.kind='';self.elapsed_s=0.;self.points=[];self.warning='';self.operation_id='';self._op_count=0
  self._schedule=[];self._due=0.;self._index=0;self._filter=0.;self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._rng=np.random.default_rng(0)
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return JascoJ1500Session(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def debug_snapshot(self):
  v=copy.deepcopy({k:x for k,x in self.__dict__.items() if k not in ['templates','_rng']});v['_rng_state']=copy.deepcopy(self._rng.bit_generator.state);return v
 def _sample(self):return self.samples.get(self.active)
 def _gas_inventory(self):return self.plant['ambient_pa']*self.plant['purge_volume_m3']/(R*self.plant['ambient_k']) if self.plant else 0.
 def _ledger(self):
  n=self._gas_inventory();inside=n*(1-self.air);initial=self.plant['initial_n2_mol'] if self.plant else 0.;volume=sum(s['volume_m3'] for s in self.samples.values());attached=self._sample()['volume_m3'] if self.active else 0.
  return dict(n2_initial_mol=initial,n2_remaining_mol=self.n2,n2_throughput_mol=self.throughput,purge_label_in_chamber_mol=inside,purge_label_initial_mol=self.initial_label,purge_label_exhaust_mol=self.exhaust_label,nitrogen_source_residual_mol=initial-self.n2-self.throughput,purge_label_residual_mol=self.initial_label+self.throughput-inside-self.exhaust_label,solution_registered_m3=volume,solution_attached_m3=attached,solution_external_m3=volume-attached,solution_residual_m3=volume-attached-(volume-attached),active_template_id=self._sample()['template_id'] if self.active else '',air_fraction=self.air,evidence='finite nitrogen source and tagged purge-gas ledger; prefilled-cell location/volume conserved, no liquid consumption, no chemical reaction or enthalpy model')
 def _fingerprint(self):
  s=self._sample();return json.dumps([self.method,s['path_length_m'],s['solvent_id'],s['cell_shape']],sort_keys=True)
 def _blank_ok(self):return bool(self.blank and self.active and self.blank['fingerprint']==self._fingerprint())
 def read(self,visibility='agent'):
  if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
  p=self.points[-1] if self.points else {};v={'plant.configured':bool(self.plant),'method.configured':bool(self.method),'power.enabled':self.powered,'lid.opened':self.opened,'lid.angle_rad':math.pi/2 if self.opened else 0.,'lamp.enabled':self.lamp,'lamp.ready':bool(self.lamp and self.plant and self.lamp_age>=self.plant['lamp_warmup_s']),'shutter.opened':self.shutter,'shutter.travel_m':.025 if self.shutter else 0.,'sample.loaded':bool(self.active),'sample.path_length_m':self._sample()['path_length_m'] if self.active else None,'sample.temperature_k':self.plant['ambient_k'] if self.plant else 298.15,'purge.flow_std_m3_s':self.flow,'purge.actual_std_m3_s':self.flow if self.n2>1e-14 else 0.,'purge.ready':bool(self.plant and self.air<=.02),'blank.ready':self._blank_ok(),'scan.phase':self.phase,'scan.kind':self.kind,'scan.elapsed_s':self.elapsed_s,'scan.completed_points':len(self.points),'scan.wavelength_m':p.get('wavelength_m'),'scan.ellipticity_rad':p.get('ellipticity_rad'),'scan.absorbance':p.get('absorbance'),'safety.warning':self.warning,'truth.ledger':self._ledger()}
  return copy.deepcopy({k:x for k,x in v.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _invalidate(self):self.blank=None;self.points=[];self.phase='idle';self.kind='';self.elapsed_s=0.;self.warning=''
 def _stop(self,phase,warning):
  if self.phase=='running':self.phase=phase
  self.shutter=False;self.warning=warning
 def _ready(self):
  if not self.plant or not self.powered or self.opened or not self.lamp or self.lamp_age<self.plant['lamp_warmup_s'] or self.air>.02 or self.n2<=1e-14:raise ActionRejected('OPTICS_NOT_READY')
 def _minimum_flow(self,w):
  if w>185e-9+1e-16:return 2e-3/60
  if w>180e-9+1e-16:return 5e-3/60
  if w>175e-9+1e-16:return 15e-3/60
  if w>170e-9+1e-16:return 15.001e-3/60
  raise ActionRejected('PURGE_REQUIREMENT_UNKNOWN')
 def _grid(self):
  m=self.method;n=int(round(abs(m['stop_m']-m['start_m'])/m['interval_m']));return np.linspace(m['start_m'],m['stop_m'],n+1).tolist()
 def _spectral(self,wavelength_m):
  """Two incoherent intensity channels integrated under a normalized Gaussian bandpass."""
  s=self._sample();m=self.method;sig=m['bandwidth_m']/2.354820045;lo=wavelength_m-3*sig;hi=wavelength_m+3*sig
  if lo<175e-9-1e-16 or hi>240e-9+1e-16:raise ActionRejected('TEMPLATE_RANGE')
  nodes=np.linspace(lo,hi,61);weight=np.exp(-.5*((nodes-wavelength_m)/sig)**2);weight/=weight.sum();x=np.arange(175,241)*1e-9
  delta=np.zeros(66) if s['kind']=='blank' else np.asarray(self.templates[s['template_id']]['delta_epsilon_m2_mol'])
  c=s['residue_concentration_mol_m3'];a=(c*np.asarray(s['mean_epsilon_m2_mol'])+s['solvent_alpha_m_inv'])*s['path_length_m'];d=(c*delta+s['solvent_delta_alpha_m_inv'])*s['path_length_m']
  left=np.dot(weight,10.**(-np.interp(nodes,x,a+d/2)));right=np.dot(weight,10.**(-np.interp(nodes,x,a-d/2)))
  if min(left,right)<=1e-300:raise ActionRejected('DETECTOR_SATURATED')
  theta=math.atan((math.sqrt(right)-math.sqrt(left))/(math.sqrt(right)+math.sqrt(left)));absorbance=-math.log10((left+right)/2)
  return theta,absorbance
 def _schedule_scan(self):
  self._schedule=[];m=self.method;grid=self._grid()
  for r in range(m['accumulations']):
   for i,w in enumerate(grid):
    theta,a=self._spectral(w);dit=m['integration_s']
    if m['scan_mode']=='auto_dit':dit=min(30.,dit*10**min(a,3.))
    duration=(dit if i==0 else abs(grid[i]-grid[i-1])/m['speed_m_s']) if m['scan_mode']=='continuous' else dit+self.plant['step_settle_s']
    self._schedule.append(dict(w=w,theta=theta,a=a,dit=dit,duration=duration,acc=r+1,index=i))
 def invoke(self,capability_id,**arguments):
  if capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability_id][0],arguments)
  if capability_id in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  old=copy.deepcopy(self.__dict__)
  try:
   result=self._invoke(capability_id,copy.deepcopy(arguments))
   if CAPS[capability_id][4] not in ['query','analysis']:self.revision+=1
   return copy.deepcopy(result)
  except Exception:
   self.__dict__.clear();self.__dict__.update(old);raise
 def _invoke(self,k,a):
  if k=='template.read':
   row=self.templates.get(a['substance_id'])
   if row is None:raise ActionRejected('UNKNOWN_REFERENCE')
   return dict(substance_id=row['substance_id'],wavelength_m=row['wavelength_m'],delta_epsilon_m2_mol=row['delta_epsilon_m2_mol'],unit=row['unit'],source=row['source'],evidence=row['evidence'],conditions_json=json.dumps(row['conditions'],ensure_ascii=False))
  if k=='product.read':return self._ledger()
  if k=='process.measure':
   v=self.read();return dict(phase=self.phase,wavelength_m=v['scan.wavelength_m'],ellipticity_rad=v['scan.ellipticity_rad'],absorbance=v['scan.absorbance'],elapsed_s=self.elapsed_s,completed_points=len(self.points),warning=self.warning)
  if k=='spectrum.read':return dict(phase=self.phase,kind=self.kind,complete=self.phase=='succeeded',points=self.points,warning=self.warning,template_source='PCDDB DichroIDPs/SP175 commit adc1094ffbfe9c6310c47be9563bfd0885297744; active reference identity withheld',cd_evidence='measured_literature CD basis + synthetic instrument and blank transformations',absorbance_evidence='synthetic_host_supplied_independent_mean_absorption',limitations=['175–240nm only, 1nm original spacing; arbitrary denser sampling is not higher optical resolution','unknown reference temperature/buffer; no temperature folding kinetics','Gaussian bandpass/DIT/normal noise are teaching assumptions, not calibrated PMT response','No real UV templates, HT calibration, LD/ORD/fluorescence/BeStSel or hardware'])
  if k=='structure.fit':return self._fit(a)
  if k=='plant.configure':
   if self.plant or self.powered or not a['accept_uncalibrated_model']:raise ActionRejected('INITIALIZATION_ONLY')
   if a['initial_air_fraction']<1 and not a['accept_purged_snapshot']:raise ActionRejected('EXPLICIT_PURGED_SNAPSHOT_REQUIRED')
   self.plant=a;self.n2=a['initial_n2_mol'];self.air=a['initial_air_fraction'];self.initial_label=self._gas_inventory()*(1-self.air);self._rng=np.random.default_rng(a['seed']);return {'configured':True}
  if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
  if self.phase=='running' and k in ['solution.register','sample.load','sample.unload','method.configure','structure.fit']:raise ActionRejected('BUSY')
  if k=='solution.register':
   if a['sample_id'] in self.samples or not a['sample_id']:raise ActionRejected('REQUEST_CONFLICT')
   if not a['accept_synthetic_uv'] or not a['accept_unknown_reference_conditions']:raise ActionRejected('EVIDENCE_ACK_REQUIRED')
   if a['cell_shape']=='rectangular' and a['path_length_m']<.001:raise ActionRejected('CELL_PATH_RANGE')
   capacity=a['path_length_m']*(.01*.035 if a['cell_shape']=='rectangular' else math.pi*.01**2)
   if a['volume_m3']>capacity:raise ActionRejected('CELL_VOLUME_PROXY')
   if a['kind']=='blank':
    if a['template_id'] or a['residue_concentration_mol_m3']!=0:raise ActionRejected('INVALID_ARGUMENT')
    delta=np.zeros(66)
   else:
    if a['template_id'] not in self.templates or a['residue_concentration_mol_m3']<=0:raise ActionRejected('UNKNOWN_REFERENCE')
    delta=np.asarray(self.templates[a['template_id']]['delta_epsilon_m2_mol'])
   if np.any(np.asarray(a['mean_epsilon_m2_mol'])+1e-15<abs(delta)/2) or np.any(np.asarray(a['solvent_alpha_m_inv'])+1e-15<abs(np.asarray(a['solvent_delta_alpha_m_inv']))/2):raise ActionRejected('NEGATIVE_POLARIZATION_ABSORPTION')
   for prior in self.samples.values():
    if prior['solvent_id']==a['solvent_id'] and any(prior[k]!=a[k] for k in ['solvent_alpha_m_inv','solvent_delta_alpha_m_inv']):raise ActionRejected('SOLVENT_DEFINITION_CONFLICT')
   self.samples[a['sample_id']]=a;return {'registered':True}
  if k=='sample.load':
   if not self.opened or self.active or a['sample_id'] not in self.samples:raise ActionRejected('SAMPLE_ACCESS_REQUIRED')
   self.active=a['sample_id'];return dict(loaded=True,attachment_event='host_attach_prefilled_cell')
  if k=='sample.unload':
   if not self.opened or not self.active or not a['recipient_id']:raise ActionRejected('SAMPLE_ACCESS_REQUIRED')
   self.active=None;return dict(unloaded=True,attachment_event='host_detach_intact_prefilled_cell')
  if k=='method.configure':
   span=abs(a['stop_m']-a['start_m']);n=span/a['interval_m']
   if not 1<=round(n)<=1023 or abs(n-round(n))>1e-5:raise ActionRejected('GRID_INTERVAL_MISMATCH')
   self.method=a;self._invalidate();return {'configured':True}
  if k=='power.set':
   self.powered=a['enabled']
   if not self.powered:
    self._stop('cancelled','POWER_OFF');self.lamp=False;self.lamp_age=0.;self.blank=None
   return a
  if k=='purge.set':
   self.flow=a['flow_std_m3_s']
   if self.phase=='running' and self.flow+1e-15<self._minimum_flow(min(self._grid())):self._stop('failed','INSUFFICIENT_PURGE')
   if self.flow==0:self.lamp=False;self.lamp_age=0.;self.shutter=False
   return a
  if k=='lid.set':
   if a['opened'] and not self.opened:self._stop('cancelled','LID_OPEN');self.exhaust_label+=self._gas_inventory()*(1-self.air);self.air=1.
   self.opened=a['opened'];return a
  if k=='lamp.set':
   if a['enabled'] and (not self.powered or self.flow<=0 or self.n2<=0 or self.air>.02):raise ActionRejected('PURGE_NOT_READY')
   if self.lamp!=a['enabled']:self.lamp_age=0.
   self.lamp=a['enabled']
   if not self.lamp:self._stop('cancelled','LAMP_OFF')
   return a
  if k=='shutter.set':
   if a['opened']:self._ready()
   elif self.phase=='running':self._stop('cancelled','SHUTTER_CLOSED')
   self.shutter=a['opened'];return a
  if k=='acquisition.start':
   if self.phase=='running':raise ActionRejected('BUSY')
   self._ready()
   if not self.method or not self.active or self._sample()['kind']!=a['kind']:raise ActionRejected('SAMPLE_METHOD_REQUIRED')
   if self.flow+1e-15<self._minimum_flow(min(self._grid())):raise ActionRejected('INSUFFICIENT_PURGE')
   if a['kind']=='sample' and not self._blank_ok():raise ActionRejected('BLANK_REQUIRED')
   self._schedule_scan()
   if a['kind']=='blank':self.blank=None
   self.points=[];self.elapsed_s=0.;self.kind=a['kind'];self.phase='running';self.warning='';self._op_count+=1;self.operation_id=self.instance_id+':'+str(self._op_count);self._index=0;self._filter=0.;self._due=self._schedule[0]['duration'];self.shutter=True
   return dict(phase=self.phase,operation_id=self.operation_id)
  if k=='acquisition.stop':self._stop('cancelled','USER_CANCEL');return {'phase':self.phase}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _record(self):
  row=self._schedule[self._index];m=self.method;theta=row['theta']
  # Continuous-mode sample-and-hold response approximation; step/auto use stationary integration.
  if m['scan_mode']=='continuous':
   self._filter=theta+(self._filter-theta)*math.exp(-row['duration']/row['dit']);theta=self._filter
  baseline=self.plant['cd_offset_rad']
  rms=float(np.interp(row['w']*1e9,[185,200,500],[.004,.007,.007]))*MDEG*math.sqrt(8/row['dit'])*math.sqrt(1e-9/m['bandwidth_m'])*self.plant['noise_multiplier']
  raw=theta+baseline+float(self._rng.normal(0,rms));raw=round(raw/(1e-5*MDEG))*(1e-5*MDEG);a=row['a']
  if abs(raw)>CD_LIMIT or not 0<=a<=5:self._stop('failed','DETECTOR_SATURATED');return
  cd=raw;uv=a
  if self.kind=='sample':
   b=self.blank['averaged'][row['index']];cd-=b['cd'];uv-=b['a']
  self.points.append(dict(wavelength_m=row['w'],ellipticity_rad=cd,absorbance=uv,raw_ellipticity_rad=raw,raw_absorbance=a,transmittance=10**(-uv),sample_time_s=self.time_s,integration_s=row['dit'],accumulation=row['acc']))
  self._index+=1
  if self._index==len(self._schedule):
   self.phase='succeeded';self.shutter=False
   if self.kind=='blank':
    n=len(self._grid());averaged=[{'cd':sum(p['raw_ellipticity_rad'] for p in self.points[i::n])/m['accumulations'],'a':sum(p['raw_absorbance'] for p in self.points[i::n])/m['accumulations']} for i in range(n)];self.blank={'fingerprint':self._fingerprint(),'averaged':averaged}
  else:self._due+=self._schedule[self._index]['duration']
 def step(self,dt_s):
  validate(number('s',0,86400),dt_s)
  if dt_s==0:return
  remaining=dt_s
  while remaining>1e-12:
   dt=remaining
   if self.phase=='running':dt=min(dt,max(0.,self._due-self.elapsed_s))
   flow=self.flow if self.n2>1e-14 else 0.;rate=flow*STD_P/(R*STD_T)
   if rate:dt=min(dt,self.n2/rate)
   if dt>1e-13:
    used=min(self.n2,rate*dt);self.n2=max(0.,self.n2-used);self.throughput+=used
    if self.plant:
     n=self._gas_inventory();old_air=self.air;self.air*=math.exp(-used/n);self.exhaust_label+=used-n*(old_air-self.air)
    if self.lamp:self.lamp_age+=dt
    self.time_s+=dt;remaining-=dt
    if self.phase=='running':self.elapsed_s+=dt
   if self.n2<=1e-14 and self.lamp:
    self._stop('failed','NITROGEN_EMPTY');self.lamp=False;self.lamp_age=0.
   if self.phase=='running' and self.elapsed_s+1e-12>=self._due:self._record()
   if dt<=1e-13 and self.phase!='running' and not rate:
    self.time_s+=remaining;remaining=0.
  self.revision+=1
 def _fit(self,a):
  if self.phase!='succeeded' or self.kind!='sample' or not self.points:raise ActionRejected('COMPLETE_SAMPLE_REQUIRED')
  grid=self._grid();n=len(grid)
  if n<20 or min(grid)>190e-9+1e-16 or max(grid)<230e-9-1e-16:raise ActionRejected('FIT_WAVELENGTH_COVERAGE')
  y=np.asarray([sum(p['ellipticity_rad'] for p in self.points[i::n])/self.method['accumulations'] for i in range(n)])/(CD_FACTOR*a['residue_concentration_mol_m3']*a['path_length_m'])
  ids=list(self.templates);x=np.arange(175,241)*1e-9;sig=self.method['bandwidth_m']/2.354820045;matrix=[]
  for w in grid:
   nodes=np.linspace(w-3*sig,w+3*sig,61);weights=np.exp(-.5*((nodes-w)/sig)**2);weights/=weights.sum()
   matrix.append([float(weights@np.interp(nodes,x,self.templates[k]['delta_epsilon_m2_mol'])) for k in ids])
  A=np.asarray(matrix);lip=float(np.linalg.norm(A,2)**2);w=np.zeros(71);w[int(np.argmin(np.mean((A-y[:,None])**2,axis=0)))]=1.
  def simplex(v):
   u=np.sort(v)[::-1];c=np.cumsum(u)-1;rho=np.where(u-c/np.arange(1,len(u)+1)>0)[0][-1];return np.maximum(v-c[rho]/(rho+1),0.)
  converged=False
  for it in range(20000):
   q=simplex(w-A.T@(A@w-y)/max(lip,1e-30))
   if np.max(abs(q-w))<1e-8:converged=True;w=q;break
   w=q
  F=np.asarray([self.templates[k]['secondary_structure_fractions'] for k in ids]).T;f=F@w;f/=f.sum()
  return dict(fractions=dict(zip(['Helix','Sheet','Turn','Disorder'],f.tolist())),weights=w.tolist(),reference_ids=ids,rms_delta_epsilon_m2_mol=float(np.sqrt(np.mean((A@w-y)**2))),iterations=it+1,converged=converged,method='projected-gradient nonnegative simplex fit over 71 SP175 references; stationary small-angle CD approximation',evidence='derived_estimate_not_OEM_or_independent_validation',template_source='PCDDB DichroIDPs SP175 pinned reference matrices',limitations=['Not BeStSel or SELCON3','Uses only measured spectrum and caller-provided concentration/path; never hidden sample identity','Not a molecular identity assignment or experimentally validated unknown-protein structure','Temperature, scatter, LD, high-OD nonlinear effects and continuous-response deconvolution not fitted','Reference composition may be non-identifiable; convergence flag and residual must be inspected'])

class JascoJ1500Session:
    """Only the trusted host assigns roles; returned data obey the same boundary."""
    def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
    @property
    def _role(self):return self._host._principals[self._principal]
    def _target(self,i):
        if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
    def describe(self,*,instance_id=None,asset_type_id=None):
        if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
        if instance_id is not None:self._target(instance_id)
        if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
        m=export_jasco_j1500_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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
        if operation_id!=h.operation_id or h._owners.get(operation_id)!=self._principal:raise ActionRejected('ACCESS_DENIED')
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('process.measure'))
    def cancel(self,operation_id,request_id):
        self.poll(operation_id);return self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)

def export_jasco_j1500_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():
  slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'JascoJ1500Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  asynchronous=k=='acquisition.start';query=kind in ['query','analysis'];unsupported=k in UNSUPPORTED
  caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if unsupported else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if unsupported else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if unsupported else 'implemented'),binding=None if unsupported else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.jasco_j1500.JascoJ1500Session.invoke',invocation=f'session.invoke(instance_id="j1500_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if query or unsupported else [dict(description_zh='宿主显式配置/光学准备/有限氮气/样品池/同方法空白/类型和身份',state_ids=['power.enabled','lamp.ready','lid.opened','purge.ready','blank.ready','scan.phase'],enforced_by='runtime',check='atomic Runtime.invoke and trusted-principal Session')],
  state_effects=[] if query or unsupported else [dict(state_id=x,timing='immediate',description_zh='受检设定/宿主挂接/取消保留') for x in effects]+[dict(state_id=x,timing='on_step',description_zh='采集时间、氮气与测量点仅宿主step推进') for x in SLOTS if SLOTS[x][2] in ['derived','process_truth']],
  observes=[],model_effects=dict(mode='none',description_zh='未安装附件无主动变化' if unsupported else '需独立实际求值场景验证',binding_ids=[]),
  execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',completion_criterion='所设波长点与全部累积完成，快门自动关闭' if asynchronous else '事务完成；读和分析不推进时间，温控附件不支持',completion_state_ids=['scan.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',retry='same_request_id',resources=['host_clock','finite_nitrogen','cell_holder','request_cache'],partial_failure_zh='取消/开盖/断灯/气尽保留已采集点和已消耗氮气，不伪造完成谱；去重仅进程内。'),
  errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNSUPPORTED','PRECONDITION_FAILED','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','INITIALIZATION_ONLY','EXPLICIT_PURGED_SNAPSHOT_REQUIRED','SAMPLE_ACCESS_REQUIRED','SAMPLE_METHOD_REQUIRED','EVIDENCE_ACK_REQUIRED','CELL_PATH_RANGE','CELL_VOLUME_PROXY','NEGATIVE_POLARIZATION_ABSORPTION','SOLVENT_DEFINITION_CONFLICT','GRID_INTERVAL_MISMATCH','OPTICS_NOT_READY','PURGE_NOT_READY','INSUFFICIENT_PURGE','PURGE_REQUIREMENT_UNKNOWN','BLANK_REQUIRED','TEMPLATE_RANGE','DETECTOR_SATURATED','BUSY','COMPLETE_SAMPLE_REQUIRED','FIT_WAVELENGTH_COVERAGE']],
  example_arguments=copy.deepcopy(ex),verification=[],limitations=['教学模型；不代表原厂安全互锁/温控性能/PMT噪声/校准曲线/硬件；UV输入为显式synthetic'],source_ids=['manufacturer_primary','simulation_assumption'])
 mech=dict(component_ids=['xe_lamp','monochromator','pem','sample_cell','cell_holder','pmt','shutter','nitrogen_inlet','host_screen'],state_ids=list(slots),meaning_zh='独立左右偏振通道CD、独立平均吸收、实测模板与异步空白/扫描',implementation='lumped',
 equations=['delta_epsilon_SI=0.1*SP175_delta_epsilon_L_mol_cm; c is mol of residues per m3, not protein-molecule molarity',
 'Amean=(epsilon_mean*c+solvent_alpha)*path; deltaA=(delta_epsilon*c+solvent_delta_alpha)*path; AL/R=Amean +/- deltaA/2; host input requires both >=0',
 'IL/R=normalized Gaussian-weighted integral(10^(-AL/R)); theta=atan((sqrt(IR)-sqrt(IL))/(sqrt(IR)+sqrt(IL))); A=-log10((IL+IR)/2); no CD-to-UV shortcut',
 'Gaussian sigma=bandwidth/2.354820045; 61 quadrature nodes over +/-3sigma; support fully inside175–240nm; no extrapolation',
 'Stationary step: dt=DIT+host_settle; autoDIT=min(30s,DIT*10^min(A,3)) plus host_settle; continuous first point DIT then abs(delta_lambda)/speed with theta_filter=theta+(old-theta)*exp(-dt/DIT) sample-and-hold approximation',
 'noise sigma=host_multiplier*interpolated manufacturer anchor_rms(185:.004,200:.007,500:.007mdeg)*sqrt(8s/DIT)*sqrt(1nm/bandwidth); distribution/interpolation/extrapolation assumptions; not calibrated stochastic response',
 'CD quantization1e-5mdeg; detector fails outside +/-8000mdeg or0–5Abs without clipping into false valid data; sample result subtracts completed blank on identical grid/method/path/solvent',
 'dn=N2flow_std*101325/(R*273.15)*dt; finite source clamps exact exhaustion event; constant-volume tagged gas replacement air*=exp(-dn/Nchamber); opening lid replaces chamber with ambient air',
 'structure fit min ||Aweights-query||^2, weights>=0,sum=1 via projected gradient; fractions=Fweights; explicitly reports convergence/residual and input normalization; no hidden sample ID',
 'Host.step sole clock, clips exactly at acquisition/exhaust events; read/describe/poll/template/fit are pure; sample load/unload conserve entire prefilled cell volume'],
 parameters={},source_ids=['manufacturer_primary','manufacturer_sheet','pcddb_primary','cd_units','simulation_assumption'],
 limitations=['模板实测71蛋白CD，范围175–240nm；光程/缓冲/温度个体元数据未提供为null；无真实成对UV库。','标准CH434无主动温控，PTC517附件控制为unsupported；选件波长扩展/LD/ORD/荧光/HT/汞灯自动校准未实现。','连续扫描零阶保持响应、auto-DIT、Gaussian带宽和噪声统计均教学假设；没有实测波长/光度/基线/故障概率校准。','拟合是非负参考组合，不是SELCON3/BeStSel，不足以独立识别未知分子或验证构象。','N2单区空气示踪不求解真实三路管网、氧/臭氧化学、UV光热或能量守恒；气体标准温压由宿主明确指定。','真实组件质量/惯量/摩擦未知；外观近似，内部光学元件仅功能拓扑，不作raytrace。','独立遥测板是simulation_only，不是本机内置屏幕；机器人接触、真实样品转移、USB驱动与原厂GUI未实现。'])
 spec=importlib.util.spec_from_file_location('_j1500_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
 ann.annotate_export(SLUG,caps,slots,{'cd_spectrometry':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'solution.register':(SOLUTION,SAMPLE_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
 for k,v in slots.items():
  v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'，不是实机标定。'
  if v['unit']=='m^3/s':v['reference_conditions']='273.15K、101325Pa标准体积；厂家L/min未给参考状态，此映射为宿主假设'
 mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'cd_spectrometry':mech})


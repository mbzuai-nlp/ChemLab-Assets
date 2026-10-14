"""Selekt SEL-2SV: sequential channels, finite carriers, conservative conditional chromatograms."""
from __future__ import annotations
import copy,importlib.util,json,math,sys
from pathlib import Path
import numpy as np
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.antonpaar_abbematadvanced import validate as _shared_validate
def validate(schema,value):
 """Retain shared finite-value validation and enforce nested integer schemas locally."""
 if schema.get('type')=='integer':
  if type(value) is not int or not schema.get('minimum',-math.inf)<=value<=schema.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
 if schema.get('type')=='object' and isinstance(value,dict):
  for key,child in value.items():
   sub=schema.get('properties',{}).get(key,schema.get('additionalProperties',True))
   if isinstance(sub,dict):validate(sub,child)
 if schema.get('type')=='array' and isinstance(value,list):
  for child in value:validate(schema['items'],child)
 _shared_validate(schema,value)
ROOT=Path(__file__).resolve().parents[2];SLUG='biotage_selekt';ASSET_TYPE_ID='instrument.'+SLUG;CATALOG_ID='CHEM-034';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/biotage_selekt_uv_library/templates.json';N=12
def arr(s,n=None):return dict(type='array',items=s,**(dict(minItems=n,maxItems=n) if n else {}))
def integer(lo,hi):return dict(type='integer',minimum=lo,maximum=hi,**{'x-unit':'1'})
CHANNEL=integer(0,1);PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
PLANT=obj({'solvent_compatibility_confirmed':BOOL,'fume_hood_confirmed':BOOL,'unused_channel_connected':BOOL,'spectral_transfer_assumption_accepted':BOOL,'solvent_volumes_m3':arr(number('m^3',1e-9,.1),4),'waste_capacity_m3':number('m^3',1e-9,.1),'mixer_volume_m3':number('m^3',1e-7,1e-5),'detector_volume_m3':number('m^3',1e-7,1e-5),'optical_path_m':number('m',1e-5,.01),'uv_response_scale':number('1',.001,1000),'collector_speed_m_s':number('m/s',.01,1),'release_tau_s':number('s',.1,100),'ambient_pa':number('Pa',8e4,1.2e5),'coefficient_evidence':TEXT})
EXAMPLE=dict(solvent_compatibility_confirmed=True,fume_hood_confirmed=True,unused_channel_connected=True,spectral_transfer_assumption_accepted=True,solvent_volumes_m3=[.002]*4,waste_capacity_m3=.002,mixer_volume_m3=1e-6,detector_volume_m3=5e-7,optical_path_m=.001,uv_response_scale=1.,collector_speed_m_s=.2,release_tau_s=2.,ambient_pa=101325.,coefficient_evidence='uncalibrated_host_geometry_response_transport')
COLUMN=obj({'channel':CHANNEL,'void_volume_m3':number('m^3',1e-6,100e-6),'resistance_pa_s_m3':number('Pa*s/m^3',1e8,1e14),'strong_solvent_resistance_factor':number('1',.1,10),'pressure_limit_pa':number('Pa',1e4,3e6),'max_sample_amount_mol':number('mol',1e-9,.01),'conditioned_strong_fraction':number('1',0,1),'solvent_pair':arr(integer(0,3),2),'column_compatibility_confirmed':BOOL,'coefficient_evidence':TEXT})
COLUMN_EXAMPLE=dict(channel=0,void_volume_m3=12e-6,resistance_pa_s_m3=2e12,strong_solvent_resistance_factor=1.,pressure_limit_pa=3e6,max_sample_amount_mol=1e-4,conditioned_strong_fraction=0.,solvent_pair=[0,1],column_compatibility_confirmed=True,coefficient_evidence='synthetic12cell_linear_equilibrium_column_not_fitted')
SPECIES=obj({'substance_id':TEXT,'amount_mol':number('mol',1e-12,.001),'k0':number('1',0,10000),'solvent_strength_slope':number('1',0,30)})
SAMPLE=obj({'channel':CHANNEL,'sample_id':TEXT,'species':dict(type='array',items=SPECIES,minItems=1,maxItems=8),'dilute_linear_load_confirmed':BOOL})
SAMPLE_EXAMPLE=dict(channel=0,sample_id='two_aromatics',species=[dict(substance_id='pcad_022',amount_mol=2e-6,k0=2.,solvent_strength_slope=3.),dict(substance_id='pcad_034',amount_mol=1e-6,k0=6.,solvent_strength_slope=4.)],dilute_linear_load_confirmed=True)
NODE=obj({'time_s':number('s',0,7200),'strong_fraction':number('1',0,1)})
METHOD=obj({'channel':CHANNEL,'flow_m3_s':number('m^3/s',1e-6/60,300e-6/60),'gradient':dict(type='array',items=NODE,minItems=2,maxItems=20),'wavelengths_m':arr(number('m',200e-9,400e-9),2),'collection_mode':dict(type='string',enum=['all','threshold']),'threshold_absorbance':number('1',0,6.4),'fraction_volume_m3':number('m^3',1e-8,9e-6),'timeout_s':number('s',1,21600)})
METHOD_EXAMPLE=dict(channel=0,flow_m3_s=30e-6/60,gradient=[dict(time_s=0.,strong_fraction=0.),dict(time_s=240.,strong_fraction=.8)],wavelengths_m=[300e-9,350e-9],collection_mode='threshold',threshold_absorbance=.02,fraction_volume_m3=5e-6,timeout_s=1200.)
RACK=obj({'positions':integer(1,144),'vessel_volume_m3':number('m^3',1e-8,9e-6)})
OBS=obj({'phase':PHASE,'stage':TEXT,'fault':TEXT,'channel':CHANNEL,'pressure_gauge_pa':number('Pa',0),'flow_m3_s':number('m^3/s',0),'strong_fraction':number('1',0,1),'uv_absorbance':arr(number('1',0,6.4),2),'fraction_number':integer(0,144),'evidence':TEXT,'template_source':TEXT})
PRODUCT=obj({'substance_ids':arr(TEXT),'initial_amount_mol':arr(number('mol',0)),'column_amount_mol':arr(number('mol',0)),'detector_amount_mol':arr(number('mol',0)),'waste_amount_mol':arr(number('mol',0)),'fraction_amount_mol':arr(arr(number('mol',0))),'fraction_volume_m3':arr(number('m^3',0)),'waste_volume_m3':number('m^3',0),'measured_Selekt_recovery':{'type':'null'},'evidence':TEXT,'template_source':TEXT})
HREC=obj({'time_s':number('s',0),'channel':CHANNEL,'elapsed_s':number('s',0),'uv1':number('1',0,6.4),'uv2':number('1',0,6.4),'pressure_pa':number('Pa',0),'strong_fraction':number('1',0,1),'fraction_number':integer(0,144),'stage':TEXT})
REF=obj({'substance_id':TEXT,'wavelength_m':number('m',200e-9,400e-9),'epsilon_m2_mol':number('m^2/mol'),'solvent_description':TEXT,'evidence':TEXT,'template_source':TEXT})
CAPS={
 'plant.configure':(PLANT,obj({'configured':BOOL}),EXAMPLE,['pump','uv_cell'],'configuration','evaluator','配置外部有限溶剂与假设机理'),
 'column.configure':(COLUMN,obj({'configured':BOOL}),COLUMN_EXAMPLE,['column_0','column_1'],'configuration','evaluator','登记已平衡柱与流阻'),
 'sample.configure':(SAMPLE,obj({'configured':BOOL}),SAMPLE_EXAMPLE,['column_0','column_1'],'configuration','evaluator','宿主将有限稀负载装入柱首单元'),
 'method.configure':(METHOD,obj({'configured':BOOL}),METHOD_EXAMPLE,['pump','uv_cell','selector_valve'],'device_command','agent','设置流速、梯度与双UV收集阈值'),
 'rack.configure':(RACK,obj({'configured':BOOL}),dict(positions=144,vessel_volume_m3=9e-6),['tray'],'configuration','evaluator','登记管架位置数及有限每管容量'),
 'tray.set_extension':(obj({'extension_m':number('m',0,.12)}),obj({'extension_m':number('m',0,.12)}),dict(extension_m=.12),['tray'],'configuration','evaluator','宿主手动托盘位姿'),
 'channel.select':(obj({'channel':CHANNEL}),obj({'channel':CHANNEL}),dict(channel=0),['selector_valve'],'device_command','agent','选择下一个顺序柱通道'),
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),dict(enabled=True),['pump','touchscreen'],'device_command','agent','控制电源，断电停止但保留库存和残压'),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),{},['pump','uv_cell','collector_x','collector_y'],'device_command','agent','启动选中柱有限时长梯度洗脱与自动卸压'),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),{},['pump'],'device_command','agent','取消当前作业并保留部分效果'),
 'pressure.release':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),{},['selector_valve'],'device_command','agent','显式卸压，不删除柱残留量'),
 'chromatography.measure':(obj({}),OBS,{},['uv_cell','touchscreen'],'query','agent','读取条件模型色谱观测'),
 'product.read':(obj({}),PRODUCT,{},['tray','column_0','column_1'],'query','evaluator','读取各物种与144管产物账本'),
 'history.read':(obj({}),obj({'records':arr(HREC),'evidence':TEXT}),{},['uv_cell'],'query','agent','读取观测历史，无组成真值'),
 'reference.lookup':(obj({'substance_id':TEXT,'wavelength_m':number('m',200e-9,400e-9)}),REF,dict(substance_id='pcad_022',wavelength_m=350e-9),['uv_cell'],'query','agent','按实测光谱支持范围查参考消光系数')}
SLOTS={};SLOT_ZH={}
def slot(k,s,u,zh,o='derived',vis='agent',component='pump',write=None):SLOTS[k]=(s,u,o,vis,[component],write);SLOT_ZH[k]=zh
for k,s,u,zh in [('power.enabled',BOOL,'1','控制电源'),('plant.configured',BOOL,'1','宿主边界配置完成'),('rack.configured',BOOL,'1','有限管架配置完成'),('channel.selected',CHANNEL,'1','当前顺序柱通道'),('cycle.phase',PHASE,'1','作业状态'),('cycle.stage',TEXT,'1','过程阶段'),('cycle.fault',TEXT,'1','最近过程故障'),('cycle.elapsed_s',number('s',0),'s','当前作业时长，含卸压'),('cycle.gradient_s',number('s',0),'s','当前柱实际梯度流动时间'),('flow.target_m3_s',number('m^3/s',0),'m^3/s','液体流率目标'),('flow.actual_m3_s',number('m^3/s',0),'m^3/s','当前模型液体流率'),('gradient.target_fraction',number('1',0,1),'1','当前方法梯度目标强溶剂比例'),('gradient.mixer_fraction',number('1',0,1),'1','混合器实际强溶剂体积分数'),('pressure.gauge_pa',number('Pa',0),'Pa','所选柱模型表压'),('uv.absorbance_1',number('1',0,6.4),'1','UV1预测十进吸光度，非原机校准测量'),('uv.absorbance_2',number('1',0,6.4),'1','UV2预测十进吸光度'),('collection.fraction_number',integer(0,144),'1','当前收集管编号；零为废液/移动'),('collection.positions_used',integer(0,144),'1','已接收液体的管数'),('collector.moving',BOOL,'1','收集针尚在移动')]:slot(k,s,u,zh,o='setpoint' if 'target' in k else 'derived')
for i in range(2):
 for name,s,u,zh in [('column_ready',BOOL,'1','柱已配置'),('sample_loaded',BOOL,'1','柱已装样'),('method_ready',BOOL,'1','柱方法已配置'),('pressure_pa',number('Pa',0),'Pa','该柱保留的表压')]:slot(f'channel.c{i}.{name}',s,u,str(i+1)+'通道'+zh,component='column_'+str(i))
for k,hi,zh,c in [('tray.extension_m',.12,'手动托盘前伸位移','tray'),('collector.x_m',.126,'收集针横向位移','collector_x'),('collector.y_m',.33,'收集针前后位移','collector_y')]:slot(k,number('m',0,hi),'m',zh,o='scene',component=c)
for k,zh in [('initial','初始输入'),('column','两柱总残留'),('detector','检测池残留'),('fractions','全部收集管中'),('waste','废液中')]:slot('truth.'+k+'_amount_mol',number('mol',0),'mol',zh+'溶质物质的量',o='process_truth',vis='evaluator')
for i in range(4):
 for k,zh in [('initial','初始总计'),('reservoir','进料瓶'),('holdup','管路及两柱持液'),('fractions','收集管'),('waste','废液')]:slot(f'truth.solvent_{i}_{k}_m3',number('m^3',0),'m^3',str(i+1)+'号液体溶剂'+zh+'体积',o='process_truth',vis='evaluator')
class BiotageSelektRuntime:
 def __init__(self,instance_id='selekt_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.plant=None;self.columns=[None,None];self.samples=[None,None];self.methods=[None,None];self.rack=None;self.powered=False;self.selected=0;self.phase='idle';self.stage='standby';self.fault='';self.elapsed_s=0.;self.gradient_s=np.zeros(2);self.done=[False,False];self.pressure=np.zeros(2);self.flow=0.;self.tray=0.;self.x=0.;self.y=.33;self.moving=False;self.fraction=0;self.tube=0;self.uv=np.zeros(2);self.operation_id=None;self._operation_count=0;self.history=[];self.next_record=0.;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self.ids=list(self.templates);self.ns=len(self.ids);self.reservoir=np.zeros(4);self.mixer=np.zeros(4);self.detfluid=np.zeros(4);self.colfluid=np.zeros((2,4,N));self.fractionfluid=np.zeros((144,4));self.wastefluid=np.zeros(4);self.initialfluid=np.zeros(4);self.colmol=np.zeros((2,self.ns,N));self.detmol=np.zeros(self.ns);self.fractionmol=np.zeros((144,self.ns));self.wastemol=np.zeros(self.ns);self.initialmol=np.zeros(self.ns);self.k0=np.zeros((2,self.ns));self.slope=np.zeros((2,self.ns));self.epsilon=np.zeros((2,self.ns));self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return BiotageSelektSession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 def _target_phi(self):
  m=self.methods[self.selected]
  return float(np.interp(self.gradient_s[self.selected],[r['time_s'] for r in m['gradient']],[r['strong_fraction'] for r in m['gradient']])) if m else 0.
 def _values(self):
  i=self.selected;m=self.methods[i];v={'power.enabled':self.powered,'plant.configured':bool(self.plant),'rack.configured':bool(self.rack),'channel.selected':i,'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.fault':self.fault,'cycle.elapsed_s':self.elapsed_s,'cycle.gradient_s':float(self.gradient_s[i]),'flow.target_m3_s':m['flow_m3_s'] if m else 0.,'flow.actual_m3_s':self.flow,'gradient.target_fraction':self._target_phi(),'gradient.mixer_fraction':float(self.mixer[self.columns[i]['solvent_pair'][1]]/self.plant['mixer_volume_m3']) if self.plant and self.columns[i] else 0.,'pressure.gauge_pa':float(self.pressure[i]),'uv.absorbance_1':float(self.uv[0]),'uv.absorbance_2':float(self.uv[1]),'collection.fraction_number':self.fraction,'collection.positions_used':int(np.count_nonzero(self.fractionfluid.sum(axis=1)>0)),'collector.moving':self.moving,'tray.extension_m':self.tray,'collector.x_m':self.x,'collector.y_m':self.y,'truth.initial_amount_mol':float(self.initialmol.sum()),'truth.column_amount_mol':float(self.colmol.sum()),'truth.detector_amount_mol':float(self.detmol.sum()),'truth.fractions_amount_mol':float(self.fractionmol.sum()),'truth.waste_amount_mol':float(self.wastemol.sum())}
  for k in range(2):v.update({f'channel.c{k}.column_ready':bool(self.columns[k]),f'channel.c{k}.sample_loaded':bool(self.samples[k]),f'channel.c{k}.method_ready':bool(self.methods[k]),f'channel.c{k}.pressure_pa':float(self.pressure[k])})
  holdup=self.mixer+self.detfluid+self.colfluid.sum(axis=(0,2))
  for k in range(4):
   for name,a in [('initial',self.initialfluid),('reservoir',self.reservoir),('holdup',holdup),('fractions',self.fractionfluid.sum(axis=0)),('waste',self.wastefluid)]:v[f'truth.solvent_{k}_{name}_m3']=float(a[k])
  return v
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('debug','evaluator')})
 def debug_snapshot(self):return copy.deepcopy(dict(state=self.read('evaluator'),plant=self.plant,columns=self.columns,samples=self.samples,methods=self.methods,rack=self.rack,clock=self.time_s,revision=self.revision,products=self._products(),history=self.history))
 def _products(self):return dict(substance_ids=self.ids,initial_amount_mol=self.initialmol.tolist(),column_amount_mol=self.colmol.sum(axis=(0,2)).tolist(),detector_amount_mol=self.detmol.tolist(),waste_amount_mol=self.wastemol.tolist(),fraction_amount_mol=self.fractionmol.tolist(),fraction_volume_m3=self.fractionfluid.sum(axis=1).tolist(),waste_volume_m3=float(self.wastefluid.sum()),measured_Selekt_recovery=None,evidence='experimental UV reference with assumed retention andsolvent transfer;finite inventories not measured Selekt recovery',template_source=TEMPLATE_REL)
 def _epsilon(self,sid,wavelength):
  if sid not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
  r=self.templates[sid];x=r['values']['wavelength_m'];y=r['values']['molar_decadic_absorptivity_m2_mol']
  if wavelength<x[0]-1e-15 or wavelength>x[-1]+1e-15:raise ActionRejected('REFERENCE_RANGE')
  return float(np.interp(wavelength,x,y))
 def _can_service(self):return self.phase!='running' and max(self.pressure)<1000
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if self.phase=='running' and CAPS[capability][4]!='query' and capability not in ['power.set','acquisition.stop']:raise ActionRejected('BUSY')
  i=a.get('channel',self.selected)
  if capability=='plant.configure':
   if self.plant or not all(a[k] for k in ['solvent_compatibility_confirmed','fume_hood_confirmed','unused_channel_connected','spectral_transfer_assumption_accepted']):raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);self.reservoir=np.array(a['solvent_volumes_m3']);self.mixer[0]=a['mixer_volume_m3'];self.detfluid[0]=a['detector_volume_m3'];self.initialfluid=self.reservoir+self.mixer+self.detfluid;out=dict(configured=True)
  elif capability=='column.configure':
   if not self.plant or self.columns[i] or not self._can_service() or not a['column_compatibility_confirmed'] or a['solvent_pair'] not in ([0,1],[2,3]):raise ActionRejected('PRECONDITION_FAILED')
   self.columns[i]=copy.deepcopy(a);p=a['solvent_pair'];v=a['void_volume_m3']/N;phi=a['conditioned_strong_fraction'];self.colfluid[i,p[0]]=v*(1-phi);self.colfluid[i,p[1]]=v*phi;self.initialfluid+=self.colfluid[i].sum(axis=1);out=dict(configured=True)
  elif capability=='sample.configure':
   if not self.columns[i] or self.samples[i] or self.methods[i] or not self._can_service() or not a['dilute_linear_load_confirmed']:raise ActionRejected('PRECONDITION_FAILED')
   if sum(r['amount_mol'] for r in a['species'])>self.columns[i]['max_sample_amount_mol'] or len({r['substance_id'] for r in a['species']})!=len(a['species']):raise ActionRejected('LOAD_ENVELOPE')
   if any(r['substance_id'] not in self.templates for r in a['species']):raise ActionRejected('UNKNOWN_REFERENCE')
   for r in a['species']:
    j=self.ids.index(r['substance_id']);self.colmol[i,j,0]=r['amount_mol'];self.initialmol[j]+=r['amount_mol'];self.k0[i,j]=r['k0'];self.slope[i,j]=r['solvent_strength_slope']
   self.samples[i]=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='method.configure':
   if not self.samples[i] or self.gradient_s[i]>0 or not self._can_service():raise ActionRejected('PRECONDITION_FAILED')
   times=[r['time_s'] for r in a['gradient']];mlmin=a['flow_m3_s']*60e6
   if abs(mlmin-round(mlmin))>1e-7 or times[0]!=0. or any(y<=x for x,y in zip(times,times[1:])) or a['timeout_s']<=times[-1]:raise ActionRejected('METHOD_ENVELOPE')
   for sid,n in zip(self.ids,self.initialmol):
    if n>0:
     for wl in a['wavelengths_m']:self._epsilon(sid,wl)
   self.methods[i]=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='rack.configure':
   if self.rack or self.phase=='running' or self.tray<.1:raise ActionRejected('PRECONDITION_FAILED')
   self.rack=copy.deepcopy(a);out=dict(configured=True)
  elif capability=='tray.set_extension':
   if not self._can_service():raise ActionRejected('PRECONDITION_FAILED')
   self.tray=a['extension_m'];out=a.copy()
  elif capability=='channel.select':
   if not self._can_service() or self.done[i]:raise ActionRejected('PRECONDITION_FAILED')
   self.selected=i;self.phase='idle' if self.gradient_s[i]==0 else 'cancelled';self.stage='standby';self.elapsed_s=0.;self.fault='';out=a.copy()
  elif capability=='power.set':
   self.powered=a['enabled']
   if not self.powered and self.phase=='running':self.phase='cancelled';self.flow=0.;self.fraction=0
   out=a.copy()
  elif capability in ('acquisition.start','pressure.release'):
   if not self.powered or not self.plant or self.tray>0 or self.phase=='running':raise ActionRejected('PRECONDITION_FAILED')
   if capability=='acquisition.start':
    if not self.methods[i] or not self.rack or self.done[i] or self.phase not in ('idle','cancelled') or self.gradient_s[i]>=self.methods[i]['gradient'][-1]['time_s']:raise ActionRejected('PRECONDITION_FAILED')
    eps=np.zeros_like(self.epsilon)
    for j,n in enumerate(self.initialmol):
     if n>0:
      for w,wl in enumerate(self.methods[i]['wavelengths_m']):eps[w,j]=self._epsilon(self.ids[j],wl)
    self.epsilon=eps;self.stage='gradient'
   else:
    if self.pressure[i]<=1.:raise ActionRejected('PRECONDITION_FAILED')
    self.stage='purging'
   self.phase='running';self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase=='running':self.phase='cancelled';self.flow=0.;self.fraction=0
   out=dict(phase=self.phase)
  elif capability=='chromatography.measure':out=dict(phase=self.phase,stage=self.stage,fault=self.fault,channel=self.selected,pressure_gauge_pa=float(self.pressure[self.selected]),flow_m3_s=self.flow,strong_fraction=self._values()['gradient.mixer_fraction'],uv_absorbance=self.uv.tolist(),fraction_number=self.fraction,evidence='conditional transport and BeerLambert using measured reference absorption;not calibrated Selekt chromatogram',template_source=TEMPLATE_REL)
  elif capability=='product.read':out=self._products()
  elif capability=='history.read':out=dict(records=copy.deepcopy(self.history),evidence='derived chromatographic observations, not measured instrument traces')
  else:
   eps=self._epsilon(a['substance_id'],a['wavelength_m']);r=self.templates[a['substance_id']];out=dict(substance_id=a['substance_id'],wavelength_m=a['wavelength_m'],epsilon_m2_mol=eps,solvent_description=r['conditions']['solvent_description'],evidence='measured_literature scaled spectrum with linear wavelength interpolation,not chromatographic solvent calibration',template_source=TEMPLATE_REL)
  validate(CAPS[capability][1],out)
  if CAPS[capability][4]!='query':self.revision+=1
  return copy.deepcopy(out)
 def _fail(self,code):self.phase='failed';self.fault=code;self.flow=0.;self.fraction=0
 def _advance(self,dt):
  self.flow=0.;self.fraction=0
  if self.phase!='running':return
  i=self.selected;self.elapsed_s+=dt;m=self.methods[i]
  if self.stage=='purging':
   self.pressure[i]*=math.exp(-dt/self.plant['release_tau_s'])
   if self.pressure[i]<1.:self.pressure[i]=0.;self.phase='succeeded';self.stage='finished_residual_solute';self.done[i]=bool(m and self.gradient_s[i]>=m['gradient'][-1]['time_s']-1e-8)
   return
  if self.elapsed_s>m['timeout_s']:self._fail('TIMEOUT');return
  c=self.columns[i];q=m['flow_m3_s'];phi=self._target_phi();pair=c['solvent_pair'];supply=np.zeros(4);supply[pair[0]]=q*(1-phi);supply[pair[1]]=q*phi;cv=c['void_volume_m3']/N;vm=self.plant['mixer_volume_m3'];vd=self.plant['detector_volume_m3']
  resistance=c['resistance_pa_s_m3']*(1+(c['strong_solvent_resistance_factor']-1)*float(self.colfluid[i,pair[1]].sum()/c['void_volume_m3']));self.pressure[i]=q*resistance
  if self.pressure[i]>min(3e6,c['pressure_limit_pa']):self._fail('OVERPRESSURE');return
  self.uv=np.clip(self.epsilon@self.detmol/vd*self.plant['optical_path_m']*self.plant['uv_response_scale'],0,6.4)
  want=m['collection_mode']=='all' or max(self.uv)>=m['threshold_absorbance']
  if want and self.tube>=self.rack['positions']:self._fail('COLLECTION_FULL');return
  target=(.018*(self.tube%8),.018*(self.tube//8)) if want else (0.,.33);distance=math.hypot(target[0]-self.x,target[1]-self.y);h=min(dt,m['gradient'][-1]['time_s']-self.gradient_s[i],.2*min(vm,vd,cv)/q)
  for k in range(4):
   if supply[k]>0:h=min(h,self.reservoir[k]/supply[k])
  collecting=want and distance<1e-9
  free=min(m['fraction_volume_m3'],self.rack['vessel_volume_m3'])-self.fractionfluid[self.tube].sum() if collecting else self.plant['waste_capacity_m3']-self.wastefluid.sum();h=min(h,max(0.,free)/q)
  if h<=1e-12:
   if any(self.reservoir[k]<=1e-15 and supply[k]>0 for k in range(4)):self._fail('SOLVENT_EMPTY')
   elif collecting:self.tube+=1;self.elapsed_s-=dt;return 0.
   else:self._fail('WASTE_FULL')
   return
  if distance>0:
   f=min(1.,self.plant['collector_speed_m_s']*h/distance);self.x+=f*(target[0]-self.x);self.y+=f*(target[1]-self.y)
  self.moving=distance>1e-9
  mixout=self.mixer/vm*q*h;colout=self.colfluid[i]/cv*q*h;detout=self.detfluid/vd*q*h
  strong=self.colfluid[i,pair[1]]/cv;k=self.k0[i,:,None]*np.exp(-self.slope[i,:,None]*strong[None,:]);soluteout=self.colmol[i]/(cv*(1+k))*q*h;detmolout=self.detmol/vd*q*h
  self.reservoir=np.maximum(0.,self.reservoir-supply*h);self.mixer+=supply*h-mixout;self.colfluid[i]-=colout;self.colfluid[i,:,0]+=mixout;self.colfluid[i,:,1:]+=colout[:,:-1];self.detfluid+=colout[:,-1]-detout
  self.colmol[i]-=soluteout;self.colmol[i,:,1:]+=soluteout[:,:-1];self.detmol+=soluteout[:,-1]-detmolout
  if collecting:
   self.fractionfluid[self.tube]+=detout;self.fractionmol[self.tube]+=detmolout;self.fraction=self.tube+1
   if self.fractionfluid[self.tube].sum()>=min(m['fraction_volume_m3'],self.rack['vessel_volume_m3'])-1e-15:self.tube+=1
  else:self.wastefluid+=detout;self.wastemol+=detmolout
  self.flow=q;self.gradient_s[i]+=h
  self.elapsed_s-=dt-h
  if self.gradient_s[i]>=m['gradient'][-1]['time_s']-1e-9:self.stage='purging';self.flow=0.;self.fraction=0
  elif any(self.reservoir[k]<=1e-15 and supply[k]>0 for k in range(4)):self._fail('SOLVENT_EMPTY')
  elif collecting and self.tube>=self.rack['positions']:self._fail('COLLECTION_FULL')
  elif self.wastefluid.sum()>=self.plant['waste_capacity_m3']-1e-15:self._fail('WASTE_FULL')
  return h
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or not 0<dt_s<=3600:raise ActionRejected('INVALID_ARGUMENT')
  remaining=float(dt_s)
  while remaining>1e-10:
   d=min(.1,remaining)
   if self.phase=='running' and self.stage=='gradient':
    i=self.selected;d=min(d,.2*min(self.plant['mixer_volume_m3'],self.plant['detector_volume_m3'],self.columns[i]['void_volume_m3']/N)/self.methods[i]['flow_m3_s'])
   used=self._advance(d);d=d if used is None else used;self.time_s+=d;self.revision+=1;remaining-=d
   if self.time_s>=self.next_record:
    self.history.append(dict(time_s=self.time_s,channel=self.selected,elapsed_s=float(self.gradient_s[self.selected]),uv1=float(self.uv[0]),uv2=float(self.uv[1]),pressure_pa=float(self.pressure[self.selected]),strong_fraction=self._target_phi(),fraction_number=self.fraction,stage=self.stage));self.next_record=self.time_s+1.
class BiotageSelektSession:
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
        meta=export_biotage_selekt_contract_metadata()
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('chromatography.measure'))

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])

def export_biotage_selekt_contract_metadata():
    slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(meaning_zh=SLOT_ZH[key],component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,origin=origin,visibility=visibility,access='read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'BiotageSelektSession.read(state_ids=[{key!r}])'),write_via=None,sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    caps={}
    for key,(inp,out,example,components,kind,vis,meaning) in CAPS.items():
        async_=key in ('acquisition.start','pressure.release')
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.biotage_selekt.BiotageSelektSession.invoke',invocation=f'session.invoke(instance_id="selekt_01",capability_id={key!r},arguments={example!r},request_id="unique")'),preconditions=[] if kind=='query' else [dict(description_zh='参数、资源与前提由本机invoke分支执行；是教学流程，不代表厂家互锁',state_ids=['cycle.phase'],enforced_by='runtime',check='BiotageSelektRuntime.invoke capability-specific guards')],state_effects=[] if kind=='query' else [dict(state_id=k,timing='on_step' if async_ else 'immediate',description_zh='仅按实际代码改变相应配置、库存、压力和收集状态；过程守恒') for k in slots],observes=[],model_effects=dict(mode='none',description_zh='运行层声明；几何同步在合同构建时依据实际验证登记',binding_ids=[]),execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',completion_criterion='梯度计时完成再卸压；柱与检测池残留不清零' if key=='acquisition.start' else ('选中柱表压降至零，库存保留' if async_ else '同步配置或纯查询，不推进时钟'),completion_state_ids=['cycle.phase'] if async_ else [],timeout_s=None,cancellation='supported' if async_ else 'unsupported',retry='same_request_id',resources=['selected_column','shared_pump_detector_collector','principal_request_cache'],partial_failure_zh='取消/耗尽/满收集/压力超限保留已发生转移和残压。去重仅当前进程，非硬件安全认证。'),errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT','UNKNOWN_REFERENCE','REFERENCE_RANGE','LOAD_ENVELOPE','METHOD_ENVELOPE']],example_arguments=copy.deepcopy(example),verification=[],limitations=['代理只读理想UV观测，真实组成限evaluator；文件隔离需另行部署。'],source_ids=['manufacturer_primary','manufacturer_manual','simulation_assumption'])
        if async_:caps[key]['errors'] += [dict(code=e,meaning_zh=e,side_effects='partial') for e in ['TIMEOUT','OVERPRESSURE','SOLVENT_EMPTY','WASTE_FULL','COLLECTION_FULL']]
    narrow={'tray.set_extension':['tray.extension_m'],'rack.configure':['rack.configured'],'power.set':['power.enabled','cycle.phase','flow.actual_m3_s','collection.fraction_number'],'acquisition.stop':['cycle.phase','flow.actual_m3_s','collection.fraction_number'],'channel.select':['channel.selected','cycle.phase','cycle.stage','cycle.fault','cycle.elapsed_s','cycle.gradient_s','pressure.gauge_pa','flow.target_m3_s','gradient.target_fraction','gradient.mixer_fraction']}
    for key,ids in narrow.items():caps[key]['state_effects']=[e for e in caps[key]['state_effects'] if e['state_id'] in ids]
    mechanism=dict(component_ids=['pump','selector_valve','column_0','column_1','uv_cell','collector_x','collector_y','tray'],state_ids=list(slots),meaning_zh='两柱顺序共享泵、混合器、UV池与收集针；四载液和逐物种摩尔守恒',implementation='lumped',equations=['12well-mixed cells percolumn;linear dilute equilibrium k=k0*exp(-S*local_strong_fraction);k0,Sassumed','solute_out=cell_total_mol/(mobile_cell_volume*(1+k))*Q*dt;all outgoing amounts transferred once,nonnegative CFL<=0.2','carrier_out=cell_carrier_volume/mobile_cell_volume*Q*dt;4carrier species tracked across mixer/columns/detector/144tubes/waste','linear interpolation of method nodes givespump inletstrongfraction;finite mixedvolume createsgradientdelay','A_lambda=sum(epsilon_measured_reference(lambda)*n_detector/V_detector)*host_path*host_response_scale;clip0..6.4;no wavelengthextrapolation','reference epsilon[M^-1cm^-1]*0.1 -> SI m2/mol;originalsolventconditions retained;cross-solvent transfer is assumption','P_gauge=host_column_resistance(strong_fraction)*Q;overpressure check before transfer;cancel traps indicatedP;purge firstorder to0 withnegligiblecompressibility approximation','fraction threshold=max(UV1,UV2);volume cutoff;finite XY transit routes to waste;only settledneedle can collect','initial species=bothcolumns+detector+allfractions+waste;initialeachcarrier=reservoir+mixer+bothcolumns+detector+allfractions+waste','read doesnot advance time;hoststep clips at CFL,finite source/sink,methodend;manual tray hostcommands,automatic needle processauthority'],parameters={},source_ids=['manufacturer_spec','manufacturer_manual','experimental_uv','simulation_assumption'],limitations=['60experimentalabsorptionreferencesarenot60Selektchromatograms;no measuredretention,columnperformance,recovery calibration.','Eachreferenceoriginalsolventandunresolvedtemperature retained;hostmustaccept UV response transfer assumption,notabsolutequantitationinrealgradienteluents.','12cell linear equilibrium retention is low-order teaching approximation;no measuredplatecount,nonlinearcompetitiveadsorption,ionization,reaction,precipitationor thermodynamic solventmixing.','Sampleloadingisinstant host placementinto firstcell,notmeasured injectionplug;host capacity rejects overloaded samples rather than simulating physicalsaturation.','Four liquid reservoirs finite;volumeadditivityassumed andsolutevolume neglected.No externalhosegasvacuumorthermalnetwork.','Bothchannels sequentially sharepump/mixer/detector/collector;othercolumn remains stopped.Prepduringrunning notsupported.Shared detectorretainedsolute carriesover conservatively onnextchannel.','Only binary pairsA/B orC/D;no automatic solventconversion/flush;initialconditioning is hostprovided.','Only two UV wavelengths andall/threshold collection;Spektra,ELSD,externaldetector,valleyslopefractionation,peakhold andfulltouchscreenmenus unsupported.','Finite XY motion usesassumed speed;waste diverted duringtravel,notcalibrateddelay-tubing/valvetiming.PhysXcontact andmotioncollisionplanning absent.','P=R*Q isquasisteadyhydraulicproxy;canceltrapsP,firstorderpurge neglectscompressibility/expelledvolume. Notpressure controlleror safetyvalvecertification.','Gradient±2% andUV±5mAU aredocumentedclaims,notmodelcalibratederrorbounds.No realnoise/calibration/lampwarming.','144tube geometry3x48 documentedcapacity9mL;onlythisracklayout,not3840mLbottlerack/extendedbed.Rackreplace/emptyingduringrun unsupported.','Traymanualpose isnotmotorizedcapability;no QR/RFID scanning,roboticcolumnmount or high-pressuresealmechanics.','Selected23kg from2018singlebedbrochure;series23–25kgrange retained;localmass/inertia/friction unknown,URDF proxiesnotmeasured.','NativeSession identity/asynchronous tested separately fromMatterix ack;deploymentfile/process isolation notprovided.'])
    spec=importlib.util.spec_from_file_location('_selekt_local_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
    ann.PARAM_ZH.update({'mixer_volume_m3':'假设梯度混合器持液体积','detector_volume_m3':'假设检测池混合持液体积','uv_response_scale':'原参考溶剂到洗脱剂的假设光学响应比例','collector_speed_m_s':'假设XY收集针速度','release_tau_s':'假设卸压时间常数','void_volume_m3':'所选柱假设流动相持液体积','resistance_pa_s_m3':'假设柱液力流阻','strong_solvent_resistance_factor':'假设强溶剂相对流阻','max_sample_amount_mol':'宿主限定线性稀负载上限，非实测吸附容量','conditioned_strong_fraction':'宿主预平衡强溶剂比例','pressure_limit_pa':'所选柱压力限值，不能超越系统30bar'})
    for key in slots:
        if slots[key]['unit']=='Pa':slots[key]['reference_conditions']='相对plant.ambient_pa的表压；不是绝压。'
        if slots[key]['unit']=='m^3/s':slots[key]['reference_conditions']='宿主给定实验条件下液体实际体积流率；非气体标准量。'
    ann.annotate_export(SLUG,caps,slots,{'flash_chromatography':mechanism},inputs={'plant.configure':(PLANT,EXAMPLE),'column.configure':(COLUMN,COLUMN_EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'flash_chromatography':mechanism})

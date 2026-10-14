"""CHEM-119 finite liquid/dry/freefall image morphology with real silhouette references."""
from pathlib import Path
import copy,math,json,importlib.util
PROTOCOL_VERSION='1.0.0';SLUG='antonpaar_litesizerdia';CATALOG_ID='CHEM-119';ASSET_TYPE_ID='instrument.'+SLUG
ROOT=Path(__file__).resolve().parents[2];ASSET=ROOT/'assets/expansion_batch03/t4_measure'/SLUG
class ActionRejected(ValueError):pass
def number(unit='1',lo=None,hi=None):
 d={'type':'number','x-unit':unit}
 if lo is not None:d['minimum']=lo
 if hi is not None:d['maximum']=hi
 return d
def obj(p):return dict(type='object',properties=p,required=list(p),additionalProperties=False)
def arr(v,n=None):
 d=dict(type='array',items=v)
 if n is not None:d.update(minItems=n,maxItems=n)
 return d
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
def enum(v):return {'type':'string','enum':v}
TEXT={'type':'string'};BOOL={'type':'boolean'};PHASE=enum(['idle','running','succeeded','cancelled','failed'])
def validate(s,v):
 if 'anyOf' in s:
  for q in s['anyOf']:
   try:validate(q,v);return
   except ActionRejected:pass
  raise ActionRejected('INVALID_ARGUMENT')
 t=s.get('type')
 if t in ('number','integer'):
  if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or (t=='integer' and type(v) is not int) or not s.get('minimum',-math.inf)<=v<=s.get('maximum',math.inf):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='boolean' and type(v) is not bool:raise ActionRejected('INVALID_ARGUMENT')
 elif t=='string' and not isinstance(v,str):raise ActionRejected('INVALID_ARGUMENT')
 elif t=='null' and v is not None:raise ActionRejected('INVALID_ARGUMENT')
 elif t=='object':
  if not isinstance(v,dict) or not set(s.get('required',[]))<=v.keys() or set(v)-set(s['properties']):raise ActionRejected('INVALID_ARGUMENT')
  for k,x in v.items():validate(s['properties'][k],x)
 elif t=='array':
  if not isinstance(v,list) or not s.get('minItems',0)<=len(v)<=s.get('maxItems',math.inf):raise ActionRejected('INVALID_ARGUMENT')
  for x in v:validate(s['items'],x)
 if 'enum' in s and v not in s['enum']:raise ActionRejected('INVALID_ARGUMENT')
import numpy as np,cv2
from PIL import Image
from scipy.spatial.distance import pdist
from functools import lru_cache
DT=.01;RGAS=8.314462618;LIB=json.loads((ASSET/'image_library.json').read_text());INT=lambda lo=0,hi=1000000:dict(type='integer',minimum=lo,maximum=hi,**{'x-unit':'1'})
def descriptors(mask,pixel_m):
 mask=np.asarray(mask,dtype=np.uint8);yy,xx=np.nonzero(mask)
 if len(xx)<9:raise ActionRejected('IMAGE_TOO_SMALL')
 contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
 if len(contours)!=1:raise ActionRejected('MULTIPLE_COMPONENTS')
 pts=contours[0][:,0,:].astype(float);corners=np.concatenate([pts+[i,j] for i,j in [(-.5,-.5),(-.5,.5),(.5,-.5),(.5,.5)]]);h=cv2.convexHull(corners.astype(np.float32))[:,0,:].astype(float);edges=np.roll(h,-1,axis=0)-h;norms=np.column_stack([-edges[:,1],edges[:,0]]);norms/=np.linalg.norm(norms,axis=1)[:,None];proj=h@norms.T;fmin=float(np.min(np.ptp(proj,axis=0)));fmax=float(np.max(pdist(h)));a=float(mask.sum());p=np.pad(mask.astype(bool),1);n1=np.count_nonzero(p[1:]!=p[:-1]);n2=np.count_nonzero(p[:,1:]!=p[:,:-1]);n3=np.count_nonzero(p[1:,1:]!=p[:-1,:-1]);n4=np.count_nonzero(p[1:,:-1]!=p[:-1,1:]);per=math.pi/8*(n1+n2+(n3+n4)/math.sqrt(2));form=4*math.pi*a/per**2
 return dict(area_m2=a*pixel_m**2,perimeter_m=per*pixel_m,equivalent_diameter_m=2*math.sqrt(a/math.pi)*pixel_m,feret_min_m=fmin*pixel_m,feret_max_m=fmax*pixel_m,aspect_ratio=fmin/fmax,circularity=math.sqrt(form),form_factor=form,foreground_pixels=int(a))
def rle(mask):
 a=np.flatnonzero(np.asarray(mask,dtype=bool).ravel());starts=a[np.r_[True,np.diff(a)>1]];ends=a[np.r_[np.diff(a)>1,True]]+1;return [[int(x),int(y-x)] for x,y in zip(starts,ends)]
def decode_rle(shape,runs):
 a=np.zeros(int(np.prod(shape)),np.uint8)
 for start,n in runs:a[start:start+n]=1
 return a.reshape(shape)
def quantile(q,u,positive=True):
 vals=([q[0]**2/q[1],*q,q[2]**2/q[1]] if positive else [max(.03,q[0]-(q[1]-q[0])),*q,min(.99,q[2]+q[2]-q[1])]);return float(np.exp(np.interp(u,[0,.1,.5,.9,1],np.log(vals)))) if positive else float(np.interp(u,[0,.1,.5,.9,1],vals))
@lru_cache(maxsize=512)
def particle(sid,index):
 v=LIB['substances'][sid]
 if v['kind']=='real_binary_images':
  rec=v['value']['images'][index%len(v['value']['images'])];mask=(np.array(Image.open(ASSET/rec['path']).convert('L'))<128).astype(np.uint8);pitch=rec['pixel_size_m'];return mask,pitch,rec['source'],'measured_binary_outline; lengthscale derived_assumption',rec['metadata']['EQPC']
 rng=np.random.default_rng(119+index);q=v['value'];d=quantile(q['diameter_quantiles_m'],float(rng.random()));ar=quantile(q['aspect_ratio_quantiles'],float(rng.random()),False);sp=quantile(q['qicpic_sphericity_quantiles'],float(rng.random()),False);theta=np.arange(512)*2*math.pi/512
 def outline(amp):
  p=np.column_stack([np.cos(theta),np.sin(theta)])*(1+amp*np.cos(8*theta))[:,None];p[:,1]*=ar/np.ptp(p[:,1])*np.ptp(p[:,0]);area=.5*abs(np.sum(p[:,0]*np.roll(p[:,1],-1)-np.roll(p[:,0],-1)*p[:,1]));per=np.linalg.norm(np.roll(p,-1,axis=0)-p,axis=1).sum();return p,2*math.sqrt(math.pi*area)/per
 lo,hi=0.,.45
 for _ in range(24):
  mid=(lo+hi)/2;_,val=outline(mid)
  if val>sp:lo=mid
  else:hi=mid
 p,_=outline((lo+hi)/2);p-=p.min(axis=0);p*=220/p.max();mask=np.zeros((int(np.ceil(p[:,1].max()))+5,225),np.uint8);cv2.fillPoly(mask,[np.round(p+2).astype(np.int32)],1);pitch=d/(2*np.sqrt(mask.sum()/math.pi));return mask,pitch,v['source'],'synthetic_outline_from_measured_marginal_quantiles; independent joint distribution assumed',d

PLANT_EXAMPLE=dict(accept_engineering=True,true_pixel_at_1x_m=3e-6,liquid_density_kg_m3=998.20715,liquid_cp_j_kg_k=4184.0509,ambient_k=293.15,heat_loss_w_k=.3,ultrasound_absorption=.6,pump_heat_w=3.,pump_flow_m3_s=1e-5,drain_flow_m3_s=3e-5,air_flow_std_m3_s=.001,air_reference_pa=101325.,air_reference_k=293.15,dry_velocity_m_s=5.,liquid_velocity_m_s=.05,freefall_height_m=.05,optical_blur_pixel=.0,pixel_noise_sd=0.,seed=119)
PLANT=obj({k:(BOOL if isinstance(v,bool) else INT(0,2147483647) if k=='seed' else number({'true_pixel_at_1x_m':'m','liquid_density_kg_m3':'kg/m^3','liquid_cp_j_kg_k':'J/kg/K','ambient_k':'K','heat_loss_w_k':'W/K','pump_heat_w':'W','pump_flow_m3_s':'m^3/s','drain_flow_m3_s':'m^3/s','air_flow_std_m3_s':'m^3/s','air_reference_pa':'Pa','air_reference_k':'K','dry_velocity_m_s':'m/s','liquid_velocity_m_s':'m/s','freefall_height_m':'m'}.get(k,'1'),0)) for k,v in PLANT_EXAMPLE.items()})
METHOD_EXAMPLE=dict(mode='liquid',magnification=1.,pixel_size_m=3e-6,fps=220.,exposure_s=1e-6,threshold=.5,pump_speed_rad_s=40*math.pi,ultrasound_w=0.,feed_particles_s=8.,target_images=32,duration_s=6.,accept_shape_reconstruction=False,accept_inferred_source_scale=True)
METHOD=obj({'mode':enum(['liquid','dry','freefall']),'magnification':dict(number('1'),enum=[.3,1.,6.]),'pixel_size_m':number('m',1e-8,1e-4),'fps':number('1/s',1,220),'exposure_s':number('s',1e-9,.001),'threshold':number('1',.05,.95),'pump_speed_rad_s':number('rad/s',0,80*math.pi),'ultrasound_w':number('W',0,50),'feed_particles_s':number('1/s',.1,1000),'target_images':INT(1,256),'duration_s':number('s',.01,300),'accept_shape_reconstruction':BOOL,'accept_inferred_source_scale':BOOL})
SAMPLE_EXAMPLE=dict(substance_id='grain_capecod',particle_count=128,density_kg_m3=2650.);SAMPLE=obj({'substance_id':TEXT,'particle_count':INT(1,2000),'density_kg_m3':number('kg/m^3',100,25000)})
DESC=obj({k:(INT(0) if k=='foreground_pixels' else number('m^2' if k=='area_m2' else 'm' if k in ['perimeter_m','equivalent_diameter_m','feret_min_m','feret_max_m'] else '1',0)) for k in ['area_m2','perimeter_m','equivalent_diameter_m','feret_min_m','feret_max_m','aspect_ratio','circularity','form_factor','foreground_pixels']})
PARTICLE_RESULT=obj({'sequence':INT(),'pass_index':INT(),'magnification':number('1'),'descriptors':DESC,'crop_shape':arr(INT(1,2448),2),'mask_rle':arr(arr(INT(0,10000000),2)),'pixel_size_m':number('m'),'template_source':TEXT,'evidence':TEXT})
SUMMARY=obj({'count':INT(),'d10_m':number('m'),'d50_m':number('m'),'d90_m':number('m'),'median_aspect_ratio':number('1'),'median_circularity':number('1'),'diameter_definition':TEXT})
RESULT=obj({'phase':PHASE,'stage':TEXT,'operation_id':nullable(TEXT),'elapsed_s':number('s'),'frames':INT(),'rejected_images':INT(),'throughput_missed':INT(),'particles':arr(PARTICLE_RESULT),'summary':nullable(SUMMARY),'template_source':nullable(TEXT),'evidence':nullable(TEXT),'error_code':nullable(TEXT)})
LEDGER=obj({k:number(u) for k,u in [('solid_loaded_kg','kg'),('solid_present_kg','kg'),('solid_collected_kg','kg'),('solid_returned_kg','kg'),('solid_waste_kg','kg'),('solid_error_kg','kg'),('particles_loaded','1'),('particles_present','1'),('particles_collected','1'),('particles_returned','1'),('particles_waste','1'),('particles_error','1'),('liquid_loaded_kg','kg'),('liquid_present_kg','kg'),('liquid_waste_kg','kg'),('liquid_error_kg','kg'),('air_added_mol','mol'),('air_present_mol','mol'),('air_consumed_mol','mol'),('air_error_mol','mol'),('heat_input_j','J'),('heat_out_j','J'),('heat_stored_j','J'),('heat_error_j','J')]})
CAPS={};SLOTS={};SLOT_ZH={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},effects or [])
def slot(k,s,u,z,o='derived',v='agent',c='controller',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller'],'宿主显式提供光学尺度、流量、噪声和热参数','configuration','evaluator',PLANT_EXAMPLE,['plant.configured'])
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_switch'],'交流电源；不虚构激光预热',ex={'enabled':True},effects=['power.enabled','power.angle_rad','lamp.tank'])
for k,arg,u,hi,part,sid in [('lid.set','angle_rad','rad',1.55,'tank_lid','lid.angle_rad'),('module.withdraw','distance_m','m',.32,'module_carriage','module.withdraw_m'),('latch.set','angle_rad','rad',.65,'module_latch','latch.angle_rad'),('service.set','distance_m','m',.16,'service_cover','service.slide_m')]:cap(k,obj({arg:number(u,0,hi)}),obj({arg:number(u)}),[part],'手动受检状态代理 '+k,ex={arg:hi},effects=[sid])
cap('module.select',obj({'mode':enum(['liquid','dry','freefall'])}),obj({'mode':TEXT}),['module_carriage'],'停机退出并排空后人工选择分散模块；Matterix只实现液流配置',ex={'mode':'liquid'},effects=['module.kind'])
cap('method.configure',METHOD,obj({'configured':BOOL}),['objective_turret','controller'],'选择倍率、相机曝光、阈值、帧率和进样；不推进物理',ex=METHOD_EXAMPLE,effects=['objective.angle_rad','camera.magnification'])
cap('liquid.fill',obj({'volume_m3':number('m^3',1e-6,.0006)}),obj({'volume_m3':number('m^3')}),['wet_tank'],'有限载液从宿主转入；只适配液流','configuration','evaluator',{'volume_m3':.0003},['truth.liquid_volume_m3'])
cap('air.add',obj({'amount_mol':number('mol',.001,100)}),obj({'stock_mol':number('mol')}),['air_in'],'接入有限压缩空气摩尔库存','configuration','evaluator',{'amount_mol':1.},['truth.air_mol'])
cap('vacuum.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['vacuum_out'],'设置外部抽吸可用状态，非自造抽真空泵',ex={'enabled':True},effects=['vacuum.ready'])
cap('sample.load',SAMPLE,obj({'particles':INT(),'mass_kg':number('kg')}),['module_carriage'],'宿主转入有限真实图像模板颗粒集合，质量用独立输入密度与等效球假设','configuration','evaluator',SAMPLE_EXAMPLE,['truth.solid_kg'])
cap('sample.recover',obj({}),obj({'mass_kg':number('kg'),'particles':INT()}),['module_carriage'],'停机开访问口回收剩余及自由落体收集样品；真实质量和颗粒数限宿主评估器',vis='evaluator',effects=['truth.solid_kg'])
cap('background.capture',obj({}),obj({'ready':BOOL}),['camera_0'],'无颗粒空白检查；没有厂商规定时长则不伪造',effects=['background.ready'])
for k,z in [('acquisition.start','基于时间推进的有限进样、相机成像与独立轮廓量算'),('liquid.drain','有限载液与悬浮颗粒排入废液')]:cap(k,obj({}),obj({'operation_id':TEXT}),['controller','pump_rotor'],z,effects=['acquisition.phase','acquisition.stage'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['controller'],'取消并保留已拍图像、耗气、分流粉体和温升',effects=['acquisition.phase'])
cap('acquisition.result',obj({}),RESULT,['controller'],'读取逐颗粒图像、Feret、圆度和长径比及数量分布','query')
cap('calibration.pixel',obj({'reference_length_m':number('m',1e-7,.01),'observed_pixels':number('1',1,2448)}),obj({'pixel_size_m':number('m')}),['objective_turret'],'独立外部标尺与实测像素数校准当前倍率，不能读宿主真值自校',ex={'reference_length_m':.000300,'observed_pixels':100.})
cap('ledger.read',obj({}),LEDGER,['controller'],'评估器读取颗粒/载液/空气/热账本','query','evaluator')
cap('sim.step',obj({'dt_s':number('s',0,3600)}),obj({'time_s':number('s')}),['controller'],'宿主唯一物理推进','simulation','evaluator',{'dt_s':.1},['clock.time_s'])
UNSUPPORTED=['analysis.oem_fusion','analysis.fiber_geodesic','accessory.small_flow','accessory.autosampler','robot.module_exchange','hardware.connect','fault.empirical','accuracy.empirical']
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'未实现：'+k)
for k,s,u,z,o,v,c,w in [
 ('plant.configured',BOOL,'1','模型已配置','derived','agent','controller',None),('power.enabled',BOOL,'1','交流电源设定','setpoint','agent','power_switch',('power.set','enabled')),('power.angle_rad',number('rad'),'rad','后部电源摇臂','scene','agent','power_switch',None),('lamp.tank',BOOL,'1','液流槽照明代理','derived','agent','tank_illumination',None),('clock.time_s',number('s'),'s','宿主时间','derived','agent','controller',None),('lid.angle_rad',number('rad'),'rad','液流盖角；干粉盖访问仅过程代理','scene','agent','tank_lid',('lid.set','angle_rad')),('module.withdraw_m',number('m'),'m','QuickClick模块沿-Y退出','scene','agent','module_carriage',('module.withdraw','distance_m')),('latch.angle_rad',number('rad'),'rad','模块手动锁扣角','scene','agent','module_latch',('latch.set','angle_rad')),('service.slide_m',number('m'),'m','后检修板沿+Y退出','scene','agent','service_cover',('service.set','distance_m')),('pump.angle_rad',number('rad'),'rad','液流离心泵叶轮角','scene','agent','pump_rotor',None),('objective.angle_rad',number('rad'),'rad','0.3/1/6倍率选择转台代理','scene','agent','objective_turret',None),('module.kind',TEXT,'1','当前实际安装模块的宿主配置','setpoint','agent','module_carriage',None),('camera.magnification',number('1'),'1','设定倍率','setpoint','agent','objective_turret',None),('camera.pixel_size_m',number('m'),'m','当前外部标定物方像素长度','setpoint','agent','controller',None),('vacuum.ready',BOOL,'1','外部抽吸可用声明','setpoint','agent','vacuum_out',('vacuum.set','enabled')),('background.ready',BOOL,'1','空白检查已完成','derived','agent','camera_0',None),('liquid.level_ok',BOOL,'1','超声前液体量大于150mL的逻辑代理','derived','agent','level_sensor',None),('truth.solid_kg',number('kg'),'kg','仪器内样品有限质量','process_truth','evaluator','module_carriage',None),('truth.particles',INT(),'1','尚未流出的真实颗粒数','process_truth','evaluator','module_carriage',None),('truth.liquid_volume_m3',number('m^3'),'m^3','载液真实体积','process_truth','evaluator','wet_tank',None),('truth.air_mol',number('mol'),'mol','有限压缩空气真值','process_truth','evaluator','air_in',None),('truth.temperature_k',number('K'),'K','液体等效温度，无虚构面板温度计','process_truth','evaluator','wet_tank',None),('acquisition.phase',PHASE,'1','测量阶段','derived','agent','controller',None),('acquisition.stage',TEXT,'1','成像/排液','derived','agent','controller',None),('acquisition.elapsed_s',number('s'),'s','操作已过时间','derived','agent','controller',None),('camera.frames',INT(),'1','帧时钟累计计数','derived','agent','camera_0',None),('particles.accepted',INT(),'1','已经解析的颗粒图像数，可含湿法复测','derived','agent','controller',None),('particles.rejected',INT(),'1','边界/低像素/分割拒收计数','derived','agent','controller',None),('diameter.d50_m',nullable(number('m')),'m','数量加权面积等效中位径','derived','agent','controller',None),('shape.circularity',nullable(number('1')),'1','2sqrt(piA)/P圆度，无三维球形度声称','derived','agent','controller',None),('shape.aspect_ratio',nullable(number('1')),'1','最小/最大Feret比','derived','agent','controller',None)]:slot(k,s,u,z,o,v,c,w)

# Explicit command effects include derived observations and private state changes.
# sim.step is the host command that advances all physical and frame accounting.
PHYSICS_EFFECTS=['clock.time_s','pump.angle_rad','truth.solid_kg','truth.particles','truth.liquid_volume_m3','liquid.level_ok','truth.air_mol','truth.temperature_k','acquisition.phase','acquisition.elapsed_s','camera.frames','particles.accepted','particles.rejected','diameter.d50_m','shape.circularity','shape.aspect_ratio']
IMMEDIATE_EFFECTS={
 'plant.configure':['plant.configured','truth.temperature_k'],
 'module.withdraw':['module.withdraw_m','background.ready'],
 'module.select':['module.kind','background.ready'],
 'method.configure':['objective.angle_rad','camera.magnification','camera.pixel_size_m','background.ready'],
 'liquid.fill':['truth.liquid_volume_m3','liquid.level_ok','truth.temperature_k'],
 'sample.load':['truth.solid_kg','truth.particles'],
 'sample.recover':['truth.solid_kg','truth.particles'],
 'calibration.pixel':['camera.pixel_size_m'],
 'acquisition.start':['acquisition.phase','acquisition.stage','acquisition.elapsed_s','camera.frames','particles.accepted','particles.rejected','diameter.d50_m','shape.circularity','shape.aspect_ratio'],
 'liquid.drain':['acquisition.phase','acquisition.stage','acquisition.elapsed_s'],
 'acquisition.stop':['acquisition.phase','diameter.d50_m','shape.circularity','shape.aspect_ratio'],
 'sim.step':PHYSICS_EFFECTS,
}
STEP_EFFECTS={
 'acquisition.start':['acquisition.elapsed_s','acquisition.phase','camera.frames','particles.accepted','particles.rejected','truth.solid_kg','truth.particles','truth.air_mol','truth.temperature_k','pump.angle_rad','diameter.d50_m','shape.circularity','shape.aspect_ratio'],
 'liquid.drain':['acquisition.elapsed_s','acquisition.phase','truth.solid_kg','truth.particles','truth.liquid_volume_m3','liquid.level_ok','truth.temperature_k','pump.angle_rad'],
 'power.set':['truth.temperature_k'],
}

class AntonpaarLitesizerdiaRuntime:
 def __init__(self,instance_id='litesizerdia_01'):
  self.instance_id=instance_id;self.plant=None;self.method=copy.deepcopy(METHOD_EXAMPLE);self.mode='liquid';self.power=False;self.lid=0.;self.withdraw=0.;self.latch=0.;self.service=0.;self.pump=0.;self.time_s=0.;self.revision=0;self._ticks=0;self._remainder=0.;self.temperature=293.15;self.liquid=0.;self.liquid_loaded=0.;self.liquid_waste=0.;self.heat_in=0.;self.heat_out=0.;self.air=0.;self.air_added=0.;self.air_used=0.;self.vacuum=False;self.bg=False;self.sid=None;self.result_sid=None;self.population=[];self.collected=[];self.loaded_m=0.;self.loaded_n=0;self.returned_m=0.;self.returned_n=0;self.waste_m=0.;self.waste_n=0.;self.phase='idle';self.stage='idle';self.elapsed=0.;self.frames=0;self.frame_fraction=0.;self.feed_fraction=0.;self.frame_credit=0;self.missed=0;self.drain_credit=0.;self.pass_index=0;self.rejected=0;self.images=[];self.summary=None;self.error=None;self.operation_id=None;self._counter=0;self._requests={};self._owners={};self._principals={'agent':'agent','evaluator':'evaluator'};self.records={};self.rng=np.random.default_rng(119)
 def register_principal(self,principal_id,visibility='agent'):
  if not isinstance(principal_id,str) or not principal_id or principal_id in self._principals or visibility not in ['agent','debug','evaluator']:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='agent'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return AntonpaarLitesizerdiaSession(self,principal_id)
 def describe(self):return export_antonpaar_litesizerdia_contract_metadata()
 def read(self,role='evaluator'):
  d={'plant.configured':self.plant is not None,'power.enabled':self.power,'power.angle_rad':.32 if self.power else 0.,'lamp.tank':self.power and self.mode=='liquid' and self.withdraw<1e-6,'clock.time_s':self.time_s,'lid.angle_rad':self.lid,'module.withdraw_m':self.withdraw,'latch.angle_rad':self.latch,'service.slide_m':self.service,'pump.angle_rad':self.pump,'objective.angle_rad':{.3:0.,1.:2*math.pi/3,6.:4*math.pi/3}[self.method['magnification']],'module.kind':self.mode,'camera.magnification':self.method['magnification'],'camera.pixel_size_m':self.method['pixel_size_m'],'vacuum.ready':self.vacuum,'background.ready':self.bg,'liquid.level_ok':self.liquid>=.00015-1e-10,'truth.solid_kg':sum(x['mass'] for x in self.population+self.collected),'truth.particles':len(self.population),'truth.liquid_volume_m3':self.liquid,'truth.air_mol':self.air,'truth.temperature_k':self.temperature,'acquisition.phase':self.phase,'acquisition.stage':self.stage,'acquisition.elapsed_s':self.elapsed,'camera.frames':self.frames,'particles.accepted':len(self.images),'particles.rejected':self.rejected,'diameter.d50_m':None if not self.summary else self.summary['d50_m'],'shape.circularity':None if not self.summary else self.summary['median_circularity'],'shape.aspect_ratio':None if not self.summary else self.summary['median_aspect_ratio']}
  return {k:copy.deepcopy(v) for k,v in d.items() if role!='agent' or SLOTS[k][3]=='agent'}
 def _result(self):return dict(phase=self.phase,stage=self.stage,operation_id=self.operation_id,elapsed_s=self.elapsed,frames=self.frames,rejected_images=self.rejected,throughput_missed=self.missed,particles=copy.deepcopy(self.images),summary=copy.deepcopy(self.summary),template_source=None if not self.result_sid else LIB['substances'][self.result_sid]['source'],evidence=None if not self.result_sid else 'measured_literature reference; derived pixel observation or explicitly synthetic marginal reconstruction',error_code=self.error)
 def _ledger(self):
  p=self.plant or PLANT_EXAMPLE;pm=sum(x['mass'] for x in self.population);cm=sum(x['mass'] for x in self.collected);lm=self.liquid*p['liquid_density_kg_m3'];heat=lm*p['liquid_cp_j_kg_k']*(self.temperature-p['ambient_k'])
  return dict(solid_loaded_kg=self.loaded_m,solid_present_kg=pm,solid_collected_kg=cm,solid_returned_kg=self.returned_m,solid_waste_kg=self.waste_m,solid_error_kg=self.loaded_m-pm-cm-self.returned_m-self.waste_m,particles_loaded=self.loaded_n,particles_present=len(self.population),particles_collected=len(self.collected),particles_returned=self.returned_n,particles_waste=self.waste_n,particles_error=self.loaded_n-len(self.population)-len(self.collected)-self.returned_n-self.waste_n,liquid_loaded_kg=self.liquid_loaded,liquid_present_kg=lm,liquid_waste_kg=self.liquid_waste,liquid_error_kg=self.liquid_loaded-lm-self.liquid_waste,air_added_mol=self.air_added,air_present_mol=self.air,air_consumed_mol=self.air_used,air_error_mol=self.air_added-self.air-self.air_used,heat_input_j=self.heat_in,heat_out_j=self.heat_out,heat_stored_j=heat,heat_error_j=self.heat_in-self.heat_out-heat)
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _ready(self):
  if not self.plant or not self.power:raise ActionRejected('NOT_READY')
  if self.withdraw>1e-6 or self.latch>.01 or self.service>1e-6 or self.lid>.01:raise ActionRejected('CLOSURE_INTERLOCK')
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[k][0],a)
  if k=='sim.step':self.step(a['dt_s']);return dict(time_s=self.time_s)
  state=copy.deepcopy(self.__dict__)
  try:
   out=self._invoke(k,a);validate(CAPS[k][1],out)
   if CAPS[k][4]!='query':self.revision+=1
   return out
  except Exception:self.__dict__.clear();self.__dict__.update(state);raise
 def _invoke(self,k,a):
  if k=='acquisition.result':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='plant.configure':
   if self.plant is not None or self.power or self.population:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   if min(a[x] for x in ['true_pixel_at_1x_m','liquid_density_kg_m3','liquid_cp_j_kg_k','ambient_k','air_reference_pa','air_reference_k','drain_flow_m3_s'])<=0 or a['ultrasound_absorption']>1:raise ActionRejected('INVALID_ARGUMENT')
   self.plant=copy.deepcopy(a);self.temperature=a['ambient_k'];self.rng=np.random.default_rng(a['seed']);return dict(configured=True)
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self._finish('cancelled',None);return dict(phase=self.phase)
  self._idle()
  if k=='power.set':
   if a['enabled'] and (not self.plant or self.withdraw>1e-6 or self.service>1e-6 or self.latch>.01):raise ActionRejected('NOT_READY')
   self.power=a['enabled'];return dict(enabled=self.power)
  if k=='lid.set':self.lid=a['angle_rad'];return a
  if k=='service.set':
   if self.power:raise ActionRejected('POWER_INTERLOCK')
   self.service=a['distance_m'];return a
  if k=='latch.set':
   if self.power or self.population or self.liquid:raise ActionRejected('POWER_OR_SAMPLE_INTERLOCK')
   self.latch=a['angle_rad'];return a
  if k=='module.withdraw':
   if self.power or self.population or self.collected or self.liquid or self.latch<.5:raise ActionRejected('MODULE_INTERLOCK')
   self.withdraw=a['distance_m'];self.bg=False;return a
  if k=='module.select':
   if self.power or self.withdraw<.3 or self.population or self.collected or self.liquid:raise ActionRejected('MODULE_INTERLOCK')
   self.mode=a['mode'];self.method['mode']=self.mode;self.bg=False;return a
  if k=='method.configure':
   if a['mode']!=self.mode:raise ActionRejected('MODE_INCOMPATIBLE')
   if a['exposure_s']>=1/a['fps']:raise ActionRejected('INVALID_ARGUMENT')
   changed=any(a[x]!=self.method[x] for x in ['magnification','threshold','exposure_s']);self.method=copy.deepcopy(a)
   if changed:self.bg=False
   return dict(configured=True)
  if k=='liquid.fill':
   if not self.plant or self.mode!='liquid' or self.lid<1.:raise ActionRejected('SAMPLE_ACCESS')
   if self.liquid+a['volume_m3']>.0006+1e-12:raise ActionRejected('TANK_CAPACITY')
   old=self.liquid;self.liquid+=a['volume_m3'];self.liquid_loaded+=a['volume_m3']*self.plant['liquid_density_kg_m3'];self.temperature=self.plant['ambient_k']+(self.temperature-self.plant['ambient_k'])*old/self.liquid;return dict(volume_m3=self.liquid)
  if k=='air.add':self.air+=a['amount_mol'];self.air_added+=a['amount_mol'];return dict(stock_mol=self.air)
  if k=='vacuum.set':self.vacuum=a['enabled'];return a
  if k=='sample.load':
   if not self.plant or self.lid<1. or self.population or self.collected:raise ActionRejected('SAMPLE_ACCESS')
   if a['substance_id'] not in LIB['substances'] or not LIB['substances'][a['substance_id']]['runtime_eligible']:raise ActionRejected('INVALID_REFERENCE')
   if self.mode=='liquid' and self.liquid<.00015:raise ActionRejected('LIQUID_LEVEL')
   ref=LIB['substances'][a['substance_id']]
   if ref['kind']=='real_binary_images' and not self.method['accept_inferred_source_scale']:raise ActionRejected('SOURCE_SCALE_NOT_ACCEPTED')
   if ref['kind']=='quantile_reference' and not self.method['accept_shape_reconstruction']:raise ActionRejected('RECONSTRUCTION_NOT_ACCEPTED')
   population=[]
   for i in range(a['particle_count']):
    _,_,_,_,d=particle(a['substance_id'],i)
    if not .5e-6<=d<={'liquid':.0025,'dry':.005,'freefall':.008}[self.mode]:raise ActionRejected('SIZE_RANGE')
    population.append(dict(index=i,mass=math.pi/6*d**3*a['density_kg_m3']))
   self.sid=a['substance_id'];self.population=population;mass=sum(x['mass'] for x in population);self.loaded_m+=mass;self.loaded_n+=len(population);return dict(particles=len(population),mass_kg=mass)
  if k=='sample.recover':
   if self.power or self.lid<1. or self.liquid>0:raise ActionRejected('SAMPLE_ACCESS')
   rows=self.population+self.collected;m=sum(x['mass'] for x in rows);n=len(rows);self.returned_m+=m;self.returned_n+=n;self.population=[];self.collected=[];return dict(mass_kg=m,particles=n)
  if k=='background.capture':
   self._ready()
   if self.population or self.collected:raise ActionRejected('BLANK_NOT_EMPTY')
   self.bg=True;return dict(ready=True)
  if k=='calibration.pixel':
   pitch=a['reference_length_m']/a['observed_pixels']
   if not 1e-8<=pitch<=1e-4:raise ActionRejected('INVALID_CALIBRATION')
   self.method['pixel_size_m']=pitch;return dict(pixel_size_m=pitch)
  if k in ['acquisition.start','liquid.drain']:
   self._ready()
   if k=='liquid.drain':
    if self.mode!='liquid' or self.liquid<=0:raise ActionRejected('LIQUID_LEVEL')
   else:
    if not self.bg:raise ActionRejected('NO_BACKGROUND')
    if not self.population:raise ActionRejected('NO_SAMPLE')
    if self.mode=='liquid' and (self.liquid<.00015 or self.method['pump_speed_rad_s']<=0):raise ActionRejected('LIQUID_LEVEL')
    if self.mode=='dry' and (not self.vacuum or self.air<=0):raise ActionRejected('SUCTION_OR_AIR')
   self._counter+=1;self.operation_id=self.instance_id+':'+str(self._counter);self.phase='running';self.stage='imaging' if k=='acquisition.start' else 'draining';self.elapsed=0.;self.error=None;self.drain_credit=0.
   if self.stage=='imaging':self.result_sid=self.sid;self.images=[];self.summary=None;self.frames=0;self.frame_fraction=0.;self.feed_fraction=0.;self.frame_credit=0;self.pass_index=0;self.rejected=0;self.missed=0
   return dict(operation_id=self.operation_id)
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _summarize(self):
  if not self.images:self.summary=None;return
  d=[x['descriptors']['equivalent_diameter_m'] for x in self.images];q=np.quantile(d,[.1,.5,.9]);self.summary=dict(count=len(d),d10_m=float(q[0]),d50_m=float(q[1]),d90_m=float(q[2]),median_aspect_ratio=float(np.median([x['descriptors']['aspect_ratio'] for x in self.images])),median_circularity=float(np.median([x['descriptors']['circularity'] for x in self.images])),diameter_definition='number-weighted equivalent projected-area diameter; wet recirculation can resample same particle')
 def _finish(self,phase,error):
  self.phase=phase;self.error=error;self._summarize();self.records[self.operation_id]=copy.deepcopy(self._result())
 def step(self,dt_s):
  validate(number('s',0,3600),dt_s);total=self._remainder+dt_s;n=int((total+1e-10)/DT);self._remainder=total-n*DT
  for _ in range(n):self._ticks+=1;self.time_s=self._ticks*DT;self._tick(DT);self.revision+=1
 def _capture(self,rec):
  raw,source_pitch,source,evidence,d=particle(self.sid,rec['index']);p=self.plant;m=self.method;true_pitch=p['true_pixel_at_1x_m']/m['magnification'];factor=source_pitch/true_pitch;h=max(1,round(raw.shape[0]*factor));w=max(1,round(raw.shape[1]*factor))
  if h>2048-8 or w>2448-8:self.rejected+=1;return
  mask=cv2.resize(raw.astype(float),(w,h),interpolation=cv2.INTER_AREA if factor<1 else cv2.INTER_LINEAR);mask=np.pad(mask,4)
  speed=p['liquid_velocity_m_s'] if self.mode=='liquid' else p['dry_velocity_m_s'] if self.mode=='dry' else math.sqrt(2*9.80665*p['freefall_height_m']);blur=math.sqrt(p['optical_blur_pixel']**2+(speed*m['exposure_s']/true_pitch)**2/12)
  if blur>.05:mask=cv2.GaussianBlur(mask,(0,0),min(50.,blur))
  if p['pixel_noise_sd']:mask+=self.rng.normal(0,p['pixel_noise_sd'],mask.shape)
  binary=(mask>=m['threshold']).astype(np.uint8)
  try:desc=descriptors(binary,m['pixel_size_m'])
  except ActionRejected:self.rejected+=1;return
  self.images.append(dict(sequence=len(self.images),pass_index=self.pass_index,magnification=m['magnification'],descriptors=desc,crop_shape=list(binary.shape),mask_rle=rle(binary),pixel_size_m=m['pixel_size_m'],template_source=source,evidence=evidence+'; optical resampling/blur/threshold derived model'))
 def _tick(self,dt):
  if not self.plant:return
  p=self.plant;m=self.method;active=self.power and self.phase=='running';imaging=active and self.stage=='imaging'
  if self.liquid>0:
   heat=(-p['heat_loss_w_k']*(self.temperature-p['ambient_k'])+(p['pump_heat_w']*m['pump_speed_rad_s']/(80*math.pi)+m['ultrasound_w']*p['ultrasound_absorption'] if imaging and self.mode=='liquid' else 0))*dt;self.temperature+=heat/(self.liquid*p['liquid_density_kg_m3']*p['liquid_cp_j_kg_k']);self.heat_in+=heat
  if not active:return
  self.elapsed+=dt
  if self.stage=='draining':
   old=self.liquid;v=min(old,p['drain_flow_m3_s']*dt);f=v/old;dm=v*p['liquid_density_kg_m3'];self.heat_out+=dm*p['liquid_cp_j_kg_k']*(self.temperature-p['ambient_k']);self.liquid-=v;self.liquid_waste+=dm
   self.drain_credit+=len(self.population)*f;number=min(len(self.population),int(self.drain_credit+1e-10));self.drain_credit-=number
   for _ in range(number):
    row=self.population.pop(0);self.waste_m+=row['mass'];self.waste_n+=1
   self.pump=(self.pump+20*math.pi*dt)%(2*math.pi)
   if self.liquid<1e-12:self.waste_m+=sum(x['mass'] for x in self.population);self.waste_n+=len(self.population);self.population=[];self.liquid=0.;self._finish('succeeded',None)
   return
  if self.mode=='liquid':
   self.pump=(self.pump+m['pump_speed_rad_s']*dt)%(2*math.pi)
   if self.temperature>=313.15:self._finish('failed','TEMPERATURE_LIMIT');return
  if self.mode=='dry':
   amount=p['air_reference_pa']*p['air_flow_std_m3_s']*dt/(RGAS*p['air_reference_k']);used=min(amount,self.air);self.air-=used;self.air_used+=used
   if used+1e-12<amount:self._finish('failed','AIR_EXHAUSTED');return
  self.frame_fraction+=m['fps']*dt;frames=int(self.frame_fraction+1e-10);self.frame_fraction-=frames;self.frames+=frames;self.frame_credit=frames
  rate=m['feed_particles_s']
  if self.mode=='liquid':rate*=min(1.,p['pump_flow_m3_s']*m['pump_speed_rad_s']/(40*math.pi)/1e-5)
  self.feed_fraction+=rate*dt;events=int(self.feed_fraction+1e-10);self.feed_fraction-=events
  for _ in range(events):
   if not self.population:self._finish('succeeded' if self.images else 'failed',None if self.images else 'NO_VALID_IMAGES');return
   rec=self.population[self.pass_index%len(self.population)] if self.mode=='liquid' else self.population.pop(0);self.pass_index+=1
   if self.mode=='dry':self.waste_m+=rec['mass'];self.waste_n+=1
   elif self.mode=='freefall':self.collected.append(rec)
   if self.frame_credit>0:self.frame_credit-=1;self._capture(rec)
   else:self.missed+=1
   if len(self.images)>=m['target_images']:self._finish('succeeded',None);return
  if self.elapsed>=m['duration_s']-1e-10:self._finish('succeeded' if self.images else 'failed',None if self.images else 'NO_VALID_IMAGES')

class AntonpaarLitesizerdiaSession:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,i):
  if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  m=export_antonpaar_litesizerdia_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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
   result=h.invoke(capability_id,**arguments);op=result.get('operation_id') if capability_id in ['acquisition.start','liquid.drain'] else None
   if op:h._owners[op]=self._principal
   response=dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=op,status='accepted' if op else 'succeeded',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result=result,observations={},error=None);h._requests[key]=(digest,copy.deepcopy(response));return response
  except ActionRejected as exc:return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,status='unsupported' if str(exc)=='UNSUPPORTED' else 'rejected',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))
 def write(self,*,instance_id,state_id,value,request_id):
  self._target(instance_id)
  if state_id not in SLOTS or not SLOTS[state_id][5]:raise ActionRejected('ACCESS_DENIED')
  cap,arg=SLOTS[state_id][5];return self.invoke(instance_id=instance_id,capability_id=cap,arguments={arg:value},request_id=request_id)
 def poll(self,operation_id):
  h=self._host
  if h._owners.get(operation_id)!=self._principal:raise ActionRejected('ACCESS_DENIED')
  record=h.records.get(operation_id)
  if operation_id==h.operation_id and h.phase=='running':record=h._result()
  if record is None:raise ActionRejected('UNKNOWN_OPERATION')
  return dict(operation_id=operation_id,status=record['phase'],result=copy.deepcopy(record))
 def cancel(self,operation_id,request_id):
  self.poll(operation_id)
  if operation_id!=self._host.operation_id:raise ActionRejected('NOT_RUNNING')
  return self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)





def export_antonpaar_litesizerdia_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions='SI; size is diameter; gas reference temperature and pressure explicit in plant; magnification dimensionless',origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'AntonpaarLitesizerdiaSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description='fixed0.01s flow/frame accounting; image observations perparticle'),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNKNOWN_OPERATION','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','INITIALIZATION_ONLY','BUSY','NOT_READY','NOT_RUNNING','MODEL_NOT_ACCEPTED','SAMPLE_ACCESS','CLOSURE_INTERLOCK','POWER_INTERLOCK','POWER_OR_SAMPLE_INTERLOCK','MODULE_INTERLOCK','MODE_INCOMPATIBLE','TANK_CAPACITY','LIQUID_LEVEL','INVALID_REFERENCE','SOURCE_SCALE_NOT_ACCEPTED','RECONSTRUCTION_NOT_ACCEPTED','SIZE_RANGE','BLANK_NOT_EMPTY','INVALID_CALIBRATION','NO_BACKGROUND','NO_SAMPLE','SUCTION_OR_AIR','IMAGE_TOO_SMALL','MULTIPLE_COMPONENTS']
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  un=k in UNSUPPORTED;asyn=k in ['acquisition.start','liquid.drain'];caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if un else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if un else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if un else 'implemented'),binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.antonpaar_litesizerdia.AntonpaarLitesizerdiaSession.invoke',invocation=f'session.invoke(instance_id="litesizerdia_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if kind=='query' or un else [dict(description_zh='访问权限、有限样品、操作互斥、模块/盖/锁扣/液位/背景检查',state_ids=['power.enabled','plant.configured','acquisition.phase','lid.angle_rad','module.withdraw_m','background.ready'],enforced_by='runtime',check='atomic invoke; registered Session; host step only')],state_effects=[dict(state_id=x,timing='immediate',description_zh='命令直接状态') for x in IMMEDIATE_EFFECTS.get(k,effects)],observes=[],model_effects=dict(mode='none',description_zh='是否场景联动须单独验证；干喷/自由落体仅过程与独立几何',binding_ids=[]),execution=dict(mode='asynchronous' if asyn else 'synchronous',time_domain='simulation',completion_criterion='达到图像数量/时长/有限料耗尽，或排液完成' if asyn else '接受命令，物理只由step推进',completion_state_ids=['acquisition.phase'] if asyn else [],timeout_s=None,cancellation='supported' if asyn else 'unsupported',retry='same_request_id',resources=['host_clock','sample','carrier','camera','compressed_air'],partial_failure_zh='已拍图片、耗气、排出粉体和载液、温升保留；取消不返还物质'),errors=[dict(code=x,meaning_zh=x,side_effects='none') for x in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=['实机校准误差与故障统计未取得','仿真图像不是厂家实测响应；原始模板、尺度推定和合成重建分开标记'],source_ids=['manufacturer_specs','simulation_assumption'])
  if k in STEP_EFFECTS:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='宿主时间推进有限库存、热量、流转或像素采集') for x in STEP_EFFECTS[k]]
 mech=dict(component_ids=['controller','camera_0','camera_1','camera_2','objective_turret','cell','wet_tank','ultrasound','pump_rotor','module_carriage'],state_ids=list(slots),meaning_zh='有限液流/干喷/自由落体的逐颗粒动态轮廓成像',implementation='lumped',equations=['Source binary masks retain original foreground; infer single export pixel pitch median(dEQ/sqrt(4A/pi)) across384images because source4um conflicts with actual raster; inference requires explicit acceptance','Image resampling at true_pixel_1x/magnification with integration blur sigma=sqrt(optical_blur²+(v*exposure/pixel)²/12), optional explicit Gaussian noise, threshold segmentation; blur isotropic approximation','A=foreground_pixels*pixel²; 4-direction Crofton P; circularity=2sqrt(piA)/P; formfactor=4piA/P²; no substitution for3D sphericity','Feret maximum=max pairwise convex-hull distances using pixel squares; minimum=min hull-edge normal width; aspectratio=Fmin/Fmax','Powder marginalD10/50/90, SPHT, AR measuredQICPIC; missing joint shapes generated as8fold corrugated ellipses with explicit independent marginal sampling; cannot reconstruct exact original particles','Finite population with perparticle sphere-equivalent mass rho*pi*dEQ³/6; wet recirculation repeats images without creating mass; dry feed to waste, freefall feed to collection; drain transfers whole particles and sensible liquid enthalpy','Frame clock fps<=220 independently limits retained images; sample feeding faster than oneparticle/frame marks missed throughput and conserves consumed mass','Liquid mCp*dT=absorbed ultrasound+pumpheat-H*(T-Tambient); no liquid no ultrasound. Finite compressed air decreases PstdQstd*dt/(RTstd); FreeFall speed=sqrt(2gh)','Calibration uses independent reference length and observed pixels; cannot fetch host true pixel scale; no read/describe advancement','Manual QuickClick release/withdraw/exchange dock interlocks; liquid geometry and7joint Matterix support; dry/freefall independent geometry variants not runtime mesh swaps'],parameters={},source_ids=['manufacturer_specs','manufacturer_brochure','grain_data','powder_data','nist_water','simulation_assumption'],limitations=['118references:97valid named powder/formulation rows+6sand types,15excluded rows retained; not50distinctnativeDIA700chemicalrawimage sets','6000original particle descriptors;384raw masks only, sampling bysortedfilename not unbiased whole PSD','GRAIN4um/pixel conflicts with raster/metadata effective6.2125um; metadata scale inference not independent validation of absolute EQPC; independentFeret/AR comparison separate','Pixel outlines limit subpixel precision; microscope3D shape, original greyscale, camera blur/noise, OEM segmentation andlens overlap fusion not validated','Number weighting only; surface/volume distribution and fibergeodesic/pore/overlap separation not implemented','Liquid unit150–600mL/2400rpm/50W documented; pumpflow, heatloss, optical PSF, detector noise, perparticle density andspherevolume inputs explicitly assumed','Dry5mm instrument range per task; newer8mm brochure differs. Freefall>8mm not supported because manufacturer restrictions unclear','41kg task nominal numerical budget; actual module mass/configuration ambiguity and real part inertias unknown','Realrobot contact, hardware, deployment filesystem/process truth isolation not_run'])
 spec=importlib.util.spec_from_file_location('_dia_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'dynamic_image_analysis':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'。'
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'dynamic_image_analysis':mech})

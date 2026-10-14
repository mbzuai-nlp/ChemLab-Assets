"""CHEM-121 finite optical sessile-drop imaging; explicit assumptions, literature references."""
from pathlib import Path
import copy,math,json,importlib.util
PROTOCOL_VERSION='1.0.0';SLUG='biolin_thetaflex';CATALOG_ID='CHEM-121';ASSET_TYPE_ID='instrument.'+SLUG
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
import numpy as np,cv2,base64,io
from PIL import Image
from scipy.integrate import solve_ivp
from scipy.optimize import brentq,nnls
from functools import lru_cache
DT=.01;G=9.80665;W=1984;H=1264;STAGE_Z_MAX_M=.049;INT=lambda lo=0,hi=1000000:dict(type='integer',minimum=lo,maximum=hi,**{'x-unit':'1'})
LIB=json.loads((ASSET/'contact_library.json').read_text())
def cap_radius(volume,theta):return (3*volume/(math.pi*(2-3*math.cos(theta)+math.cos(theta)**3)))**(1/3)
def cap_theta(volume,base):
 h=brentq(lambda h:math.pi*h*(3*base*base+h*h)/6-volume,1e-12,.1,xtol=1e-15);return 2*math.atan2(h,base)
@lru_cache(maxsize=4096)
def profile(volume,theta,rho,gamma,spherical=False):
 if volume<=0 or not .025<theta<math.pi-.025:raise ActionRejected('DROP_SHAPE_RANGE')
 rguess=cap_radius(volume,theta);p0=1e-5
 if spherical:
  ph=np.linspace(0,theta,600);return rguess*np.sin(ph),rguess*(np.cos(ph)-math.cos(theta)),rguess
 def integrate(apex,dense=False):
  bo=rho*G*apex*apex/gamma
  def f(p,y):
   r,z,v=y;k=2+bo*z-math.sin(p)/max(r,1e-15)
   if k<=0:raise ActionRejected('DROP_SHAPE_RANGE')
   return [math.cos(p)/k,math.sin(p)/k,math.pi*r*r*math.sin(p)/k]
  q=solve_ivp(f,(p0,theta),[math.sin(p0),1-math.cos(p0),math.pi*p0**4/4],rtol=2e-7,atol=1e-10,max_step=.07,dense_output=dense)
  if not q.success:raise ActionRejected('DROP_SHAPE_RANGE')
  return q
 low=rguess*.05;high=rguess*8
 apex=brentq(lambda v:integrate(v).y[2,-1]*v**3-volume,low,high,xtol=1e-12)
 q=integrate(apex,True);raw=q.sol(np.linspace(p0,theta,600));return raw[0]*apex,(raw[1,-1]-raw[1])*apex,apex

def render_drop(volume,theta,rho,gamma,width,focus,illumination,spherical=False):
 r,z,_=profile(float(volume),float(theta),float(rho),float(gamma),bool(spherical));pitch=width/W;base=int(H*.81);xs=np.r_[-r[::-1],r];ys=np.r_[z[::-1],z];xy=np.c_[W/2+xs/pitch,base-ys/pitch]
 if xy[:,0].min()<4 or xy[:,0].max()>W-5 or xy[:,1].min()<4:raise ActionRejected('FIELD_OF_VIEW')
 mask=np.full((H,W),int(80+165*illumination),np.uint8);cv2.fillPoly(mask,[np.rint(xy).astype(np.int32)],25);mask[base+1:]=85
 blur=.5+800*abs(focus-.006);mask=cv2.GaussianBlur(mask,(0,0),blur);out=io.BytesIO();Image.fromarray(mask).save(out,format='PNG');return base64.b64encode(out.getvalue()).decode(),base,pitch

def image_fit(image_png_base64,baseline_px,pixel_pitch_m,fit='polynomial'):
 if len(image_png_base64)>8000000:raise ActionRejected('INVALID_IMAGE')
 try:
  raw=base64.b64decode(image_png_base64,validate=True);im=Image.open(io.BytesIO(raw))
  if im.width>4096 or im.height>4096 or im.width*im.height>8000000:raise ActionRejected('INVALID_IMAGE')
  a=np.array(im.convert('L'))
 except Exception as e:raise ActionRejected('INVALID_IMAGE') from e
 hh,ww=a.shape
 if not 15<baseline_px<hh-2 or pixel_pitch_m<=0:raise ActionRejected('INVALID_IMAGE')
 # Otsu threshold is determined solely from pixels above the supplied baseline.
 threshold,binary=cv2.threshold(a[:baseline_px],0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU);cs,_=cv2.findContours(binary,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_NONE)
 if not cs:raise ActionRejected('NO_DROP_EDGE')
 c=max(cs,key=cv2.contourArea)
 if cv2.contourArea(c)<80:raise ActionRejected('NO_DROP_EDGE')
 selected=np.zeros_like(binary);cv2.drawContours(selected,[c],-1,255,-1);points=[]
 for row in range(baseline_px-2,-1,-1):
  xx=np.flatnonzero(selected[row])
  if len(xx)>1:points.append((baseline_px-row,float(xx[0]),float(xx[-1])))
 p=np.array(points)
 if len(p)<15 or p[0,0]>5:raise ActionRejected('BASELINE_OR_EDGE')
 h=p[:,0];left=p[:,1];right=p[:,2];height=h[-1];edge=np.c_[np.r_[left,right],np.r_[baseline_px-h,baseline_px-h]]
 if fit=='circle':
  x=edge[:,0]-ww/2;y=baseline_px-edge[:,1];mat=np.c_[2*x,2*y,np.ones(len(x))];coef=np.linalg.lstsq(mat,x*x+y*y,rcond=None)[0];cx,cy,d=coef;radius=math.sqrt(max(1e-30,d+cx*cx+cy*cy));cost=-cy/radius
  if not -1<cost<1:raise ActionRejected('FIT_FAILED')
  lt=rt=math.acos(cost);residual=float(np.sqrt(np.mean((np.sqrt((x-cx)**2+(y-cy)**2)-radius)**2)))
 elif fit=='polynomial':
  window=max(24,min(height*.50,180.));take=(h<=window)&(h>=3);v=h[take]/window
  if sum(take)<12:raise ActionRejected('EDGE_RESOLUTION')
  lcoef=np.polyfit(v,left[take],3);rcoef=np.polyfit(v,right[take],3);dl=lcoef[-2]/window;dr=rcoef[-2]/window;lt=math.atan2(1.,dl);rt=math.atan2(1.,-dr);residual=float(math.sqrt(np.mean(np.r_[(np.polyval(lcoef,v)-left[take])**2,(np.polyval(rcoef,v)-right[take])**2])))
 else:raise ActionRejected('INVALID_ARGUMENT')
 # Axisymmetric volume uses the observed half-width profile. No reference-angle lookup.
 radius=(right-left+1)/2;measured_volume=float(math.pi*np.trapz(np.r_[radius[0]**2,radius**2,0.],np.r_[0,h,h[-1]+1])*pixel_pitch_m**3)
 return dict(left_rad=lt,right_rad=rt,mean_rad=(lt+rt)/2,volume_m3=measured_volume,residual_px=residual,edge_points=len(edge),fit=fit,threshold=int(threshold),image_width_px=ww,image_height_px=hh)

def surface_energy(observations):
 if len(observations)<2:raise ActionRejected('INSUFFICIENT_PROBES')
 gd=np.array([p['liquid_dispersive_j_m2'] for p in observations]);gp=np.array([p['liquid_polar_j_m2'] for p in observations]);theta=np.array([p['angle_rad'] for p in observations]);mat=2*np.c_[np.sqrt(gd),np.sqrt(gp)];y=(gd+gp)*(1+np.cos(theta))
 if np.linalg.matrix_rank(mat)<2 or np.linalg.cond(mat)>1e5:raise ActionRejected('DEGENERATE_PROBES')
 q,res=nnls(mat,y);pred=mat@q
 return dict(dispersive_j_m2=float(q[0]**2),polar_j_m2=float(q[1]**2),total_j_m2=float(sum(q*q)),residuals_n_m=(pred-y).tolist(),method='OWRK nonnegative least squares; angle observations and liquid component energies supplied independently')
PLANT_EXAMPLE=dict(accept_engineering=True,density_kg_m3=998.20715,surface_tension_n_m=.0728,syringe_capacity_m3=5e-7,advancing_rad=math.radians(80),receding_rad=math.radians(25),accept_dynamic_proxy=True)
PLANT=obj({'accept_engineering':BOOL,'density_kg_m3':number('kg/m^3',100,5000),'surface_tension_n_m':number('N/m',.001,2),'syringe_capacity_m3':number('m^3',1e-8,5e-6),'advancing_rad':nullable(number('rad',.03,3.11)),'receding_rad':nullable(number('rad',.03,3.11)),'accept_dynamic_proxy':BOOL})
METHOD_EXAMPLE=dict(mode='static',field_width_m=.0035,frame_rate_hz=5.,frames=5,flow_m3_s=1e-10,stage_speed_m_s=.004,fit='polynomial')
METHOD=obj({'mode':enum(['static','dynamic']),'field_width_m':number('m',.00144,.0323),'frame_rate_hz':number('Hz',1,20),'frames':INT(1,100),'flow_m3_s':number('m^3/s',1e-12,1e-8),'stage_speed_m_s':number('m/s',.0001,.010),'fit':enum(['circle','polynomial'])})
SAMPLE_EXAMPLE=dict(reference_id='al_ctab_a_row7',accept_unknown_conditions=True)
SAMPLE=obj({'reference_id':TEXT,'accept_unknown_conditions':BOOL})
FIT_RESULT=obj({'left_rad':number('rad',0,math.pi),'right_rad':number('rad',0,math.pi),'mean_rad':number('rad',0,math.pi),'volume_m3':number('m^3',0),'residual_px':number('1',0),'edge_points':INT(),'fit':TEXT,'threshold':INT(0,255),'image_width_px':INT(1,4096),'image_height_px':INT(1,4096)})
IMAGE_INPUT=obj({'image_png_base64':TEXT,'baseline_px':INT(16,4094),'pixel_pitch_m':number('m',1e-9,.01),'fit':enum(['circle','polynomial'])})
ROW=obj({'time_s':number('s'),'transferred_m3':number('m^3'),'observed_volume_m3':number('m^3'),'left_rad':number('rad'),'right_rad':number('rad'),'mean_rad':number('rad'),'branch':TEXT,'fit_residual_px':number('1')})
RESULT=obj({'phase':PHASE,'stage':TEXT,'operation_id':nullable(TEXT),'elapsed_s':number('s'),'rows':arr(ROW),'image_png_base64':nullable(TEXT),'template_source':nullable(TEXT),'evidence':nullable(TEXT),'error_code':nullable(TEXT)})
LEDGER=obj({k:number(u) for k,u in [('loaded_m3','m^3'),('syringe_m3','m^3'),('drop_m3','m^3'),('waste_m3','m^3'),('volume_error_m3','m^3'),('mass_error_kg','kg')]})
CAPS={};SLOTS={};SLOT_ZH={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},effects or [])
def slot(k,s,u,z,o='derived',v='agent',c='controller',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
cap('plant.configure',PLANT,obj({'configured':BOOL}),['camera','syringe'],'宿主显式接受有效物性、针筒容量、滞后端点和模型近似','configuration','evaluator',PLANT_EXAMPLE,['plant.configured'])
for k,arg,schema,c,z,effect in [('power.set','enabled',BOOL,'power_switch','切换供电','power.enabled'),('service.set','distance_m',number('m',0,.09),'service_cover','断电拆开后盖','service.slide_m'),('focus.set','position_m',number('m',0,.02),'focus','手动调焦的过程位置代理','focus.position_m'),('illumination.set','fraction',number('1',.2,1),'led_diffuser','设置背景照明强度','illumination.fraction')]:
 cap(k,obj({arg:schema}),obj({arg:schema}),[c],z,ex={arg:False if arg=='enabled' else .6 if arg=='fraction' else 0.},effects=[effect])
cap('method.configure',METHOD,METHOD,['camera','dispenser'],'选择静态/动态、FOV、采样频率及拟合；20Hz是本模拟限制','configuration',ex=METHOD_EXAMPLE)
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_plate'],'宿主放入文献表面/液体配对；未知条件须确认','configuration','evaluator',SAMPLE_EXAMPLE,['sample.present'])
cap('sample.remove',obj({}),obj({'removed':BOOL}),['sample_plate'],'宿主取出已清除液滴的样片','configuration','evaluator',effects=['sample.present'])
STOCK=obj({'reference_id':TEXT,'volume_m3':number('m^3',1e-10,5e-6)})
cap('stock.load',STOCK,obj({'loaded':BOOL}),['syringe'],'宿主装入与样片参考匹配的有限探针液体','configuration','evaluator',dict(reference_id=SAMPLE_EXAMPLE['reference_id'],volume_m3=2e-7),['truth.syringe_m3','plunger.position_m'])
cap('stock.unload',obj({}),obj({'unloaded':BOOL}),['syringe'],'宿主回收剩余针筒液体并入账','configuration','evaluator',effects=['truth.syringe_m3'])
cap('drop.clean',obj({}),obj({'cleaned':BOOL}),['sample_plate'],'宿主吸走样片上的液滴入回收账本','configuration','evaluator',effects=['truth.drop_m3'])
CAL=obj({'known_length_m':number('m',1e-5,.05),'observed_pixels':number('1',10,4096)})
cap('camera.calibrate',CAL,obj({'pixel_pitch_m':number('m')}),['camera','calibration_socket'],'独立外部长度/图像像素标定；不代替C216证书','configuration',ex=dict(known_length_m=.001,observed_pixels=.001/(.0035/W)),effects=['camera.calibrated'])
for k,i,e,c,z in [('stage.move',obj({'x_m':number('m',0,.08),'y_m':number('m',0,.04),'z_m':number('m',0,STAGE_Z_MAX_M)}),dict(x_m=.04,y_m=.02,z_m=.025),'stage_z','按受限速度移动手动XYZ台，过程代理'),('dispenser.move',obj({'position_m':number('m',0,.035)}),dict(position_m=.010),'dispenser','异步上下移动自动滴放支架'),('liquid.transfer',obj({'volume_m3':number('m^3',-1e-8,1e-8)}),dict(volume_m3=2e-10),'syringe','有限针筒滴放或回吸，动态模式保留针下液滴，体积守恒'),('acquisition.start',obj({}),{},'camera','按方法频率捕获有限帧数并独立拟合轮廓')]:cap(k,i,obj({'operation_id':TEXT}),[c],z,ex=e,effects=['acquisition.phase'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['controller'],'取消保留已移动位置、已转移液体与图像',effects=['acquisition.phase'])
cap('acquisition.result',obj({}),RESULT,['camera'],'只读最后异步操作结果与像素图像','query')
cap('image.capture',obj({}),obj({'image_png_base64':TEXT,'baseline_px':INT(0,H),'pixel_pitch_m':number('m'),'measurement':FIT_RESULT}),['camera'],'返回由物理轮廓形成的像素图像及图像拟合；模拟图像明确标注','analysis')
cap('analysis.image',IMAGE_INPUT,FIT_RESULT,['camera'],'只根据外部图像、外部基线与像素尺度求左右接触角和体积','analysis',ex=dict(image_png_base64='',baseline_px=1000,pixel_pitch_m=.0035/W,fit='polynomial'))
OBS=obj({'angle_rad':number('rad',0,math.pi),'liquid_dispersive_j_m2':number('J/m^2',0,2),'liquid_polar_j_m2':number('J/m^2',0,2)})
SFE=obj({'dispersive_j_m2':number('J/m^2',0),'polar_j_m2':number('J/m^2',0),'total_j_m2':number('J/m^2',0),'residuals_n_m':arr(number('N/m')),'method':TEXT})
cap('analysis.surface_energy',obj({'observations':arr(OBS)}),SFE,['controller'],'外部两种以上探针液观测的OWRK非负拟合','analysis',ex={'observations':[dict(angle_rad=1.,liquid_dispersive_j_m2=.0218,liquid_polar_j_m2=.051),dict(angle_rad=.6,liquid_dispersive_j_m2=.0508,liquid_polar_j_m2=0.)]})
cap('analysis.hysteresis',obj({'advancing_rad':arr(number('rad',0,math.pi)),'receding_rad':arr(number('rad',0,math.pi))}),obj({'advancing_rad':number('rad'),'receding_rad':number('rad'),'hysteresis_rad':number('rad')}),['controller'],'从外部进角/退角序列的极值得到滞后，不读取物性真值','analysis',ex=dict(advancing_rad=[1.2,1.3],receding_rad=[.6,.7]))
cap('ledger.read',obj({}),LEDGER,['syringe','sample_plate'],'宿主体积/质量守恒审计','query','evaluator')
cap('sim.step',obj({'dt_s':number('s',0,3600)}),obj({'time_s':number('s')}),['controller'],'唯一宿主时钟推进','simulation','evaluator',dict(dt_s=.1))
UNSUPPORTED=['camera.high_speed_3009fps','calibration.oem_certificate','drop.pendant_tension','drop.captive_bubble','stage.tilt','topography.roughness','rheology.measure','hardware.connect','robot.manipulate','fault.empirical','noise.empirical']
for k in UNSUPPORTED:cap(k,obj({}),obj({}),['controller'],'缺少附件/原厂实现或数据：'+k)
for axis,hi in [('x',.08),('y',.04),('z',STAGE_Z_MAX_M)]:
 slot('stage.'+axis+'_m',number('m',0,hi),'m','实际'+axis.upper()+'台位移','scene',c='stage_'+axis);slot('stage.target_'+axis+'_m',number('m',0,hi),'m','目标'+axis.upper()+'位置','setpoint',c='stage_'+axis)
for k,hi,z,c,w in [('dispenser.z_m',.035,'自动滴放支架实际下移','dispenser',None),('dispenser.target_m',.035,'滴放目标下移','dispenser',('dispenser.move','position_m')),('plunger.position_m',.04,'针筒体积映射到假设40mm活塞行程','plunger',None),('focus.position_m',.02,'手动对焦位置','focus',('focus.set','position_m')),('service.slide_m',.09,'维护板后移','service_cover',('service.set','distance_m'))]:slot(k,number('m',0,hi),'m',z,'setpoint' if w else 'scene',c=c,w=w)
slot('power.angle_rad',number('rad',0,.32),'rad','电源按钮位置代理','scene',c='power_switch')
for k,z,c in [('plant.configured','显式工程输入已给定','controller'),('power.enabled','供电状态','power_switch'),('lamp.measurement','测量或动态传输指示灯','measurement_lamp'),('sample.present','样片在位','sample_plate'),('camera.calibrated','外部长度标定已输入','camera')]:slot(k,BOOL,'1',z,c=c)
slot('illumination.fraction',number('1',.2,1),'1','背景照明设定','setpoint',c='led_diffuser',w=('illumination.set','fraction'))
slot('camera.pixel_pitch_m',nullable(number('m')),'m','独立标定像素尺度；FOV变动失效','derived',c='camera')
slot('method.mode',enum(['static','dynamic']),'1','静态Y-L轮廓或小Bond动态球冠模型','setpoint')
for k,u,z in [('angle.indicated_rad','rad','从图像边缘拟合的平均接触角'),('angle.left_rad','rad','从像素轮廓拟合左接触角'),('angle.right_rad','rad','从像素轮廓拟合右接触角'),('volume.indicated_m3','m^3','由图像半宽积分及像素标定估计液滴体积')]:slot(k,nullable(number(u)),u,z,c='camera')
for k,u,z in [('truth.drop_m3','m^3','有限样片液滴体积'),('truth.syringe_m3','m^3','有限针筒库存'),('truth.contact_angle_rad','rad','有效接触角真值'),('truth.contact_radius_m','m','模型接触线半径')]:slot(k,nullable(number(u)),u,z,'process_truth','evaluator')
slot('clock.time_s',number('s'),'s','宿主时间');slot('acquisition.phase',PHASE,'1','异步操作状态');slot('acquisition.stage',TEXT,'1','移动/滴液/回吸/成像阶段');slot('acquisition.elapsed_s',number('s'),'s','本操作用时');slot('acquisition.points',INT(),'1','实际像素测量帧数')
ASYNC=['stage.move','dispenser.move','liquid.transfer','acquisition.start']
IMAGE_STATES=['angle.indicated_rad','angle.left_rad','angle.right_rad','volume.indicated_m3']
STAGE_TARGETS=['stage.target_'+axis+'_m' for axis in 'xyz']
OPERATION_EFFECTS=['acquisition.phase','acquisition.stage','acquisition.elapsed_s','acquisition.points','lamp.measurement']
STEP_EFFECTS={
 'stage.move':['stage.'+axis+'_m' for axis in 'xyz']+['acquisition.elapsed_s','acquisition.phase'],
 'dispenser.move':['dispenser.z_m','acquisition.elapsed_s','acquisition.phase'],
 'liquid.transfer':['plunger.position_m','truth.drop_m3','truth.syringe_m3','truth.contact_angle_rad','truth.contact_radius_m','acquisition.elapsed_s','acquisition.phase','acquisition.points','lamp.measurement']+IMAGE_STATES,
 'acquisition.start':['acquisition.elapsed_s','acquisition.phase','acquisition.points','lamp.measurement']+IMAGE_STATES,
}
IMMEDIATE_EFFECTS={
 'power.set':['power.enabled','power.angle_rad','lamp.measurement']+IMAGE_STATES,
 'method.configure':['method.mode','camera.calibrated','camera.pixel_pitch_m']+IMAGE_STATES,
 'sample.load':['sample.present','truth.contact_angle_rad'],
 'sample.remove':['sample.present','truth.contact_angle_rad','truth.contact_radius_m']+IMAGE_STATES,
 'stock.unload':['truth.syringe_m3','plunger.position_m'],
 'drop.clean':['truth.drop_m3','truth.contact_angle_rad','truth.contact_radius_m']+IMAGE_STATES,
 'camera.calibrate':['camera.calibrated','camera.pixel_pitch_m'],
 'stage.move':OPERATION_EFFECTS+STAGE_TARGETS,
 'dispenser.move':OPERATION_EFFECTS+['dispenser.target_m'],
 'liquid.transfer':OPERATION_EFFECTS,
 'acquisition.start':OPERATION_EFFECTS,
 'acquisition.stop':['acquisition.phase','lamp.measurement','dispenser.target_m']+STAGE_TARGETS,
 'sim.step':['clock.time_s']+list(dict.fromkeys(state for effects in STEP_EFFECTS.values() for state in effects)),
}
class BiolinThetaFlexRuntime:
 def __init__(self,instance_id='thetaflex_01'):
  self.instance_id=instance_id;self.plant=None;self.method=copy.deepcopy(METHOD_EXAMPLE);self.power=False;self.service=0.;self.q=[0.,0.,0.];self.target=self.q[:];self.dispenser=0.;self.dispenser_target=0.;self.focus=.006;self.illumination=.8;self.pitch=None;self.sid=None;self.stock_sid=None;self.stock=0.;self.volume=0.;self.loaded=0.;self.waste=0.;self.theta=None;self.radius=None;self.last_fit=None;self.last_fit_time=None;self.last_image=None;self.time_s=0.;self._ticks=0;self._remainder=0.;self.revision=0;self.phase='idle';self.stage='idle';self.elapsed=0.;self.rows=[];self.transfer_remaining=0.;self.transferred=0.;self.result_source=None;self.result_evidence=None;self.error=None;self.operation_id=None;self._counter=0;self._requests={};self._owners={};self.records={};self._principals={'agent':'agent','evaluator':'evaluator'}
 def register_principal(self,principal_id,visibility='agent'):
  if not isinstance(principal_id,str) or not principal_id or principal_id in self._principals or visibility not in ['agent','debug','evaluator']:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='agent'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return BiolinThetaFlexSession(self,principal_id)
 def describe(self):return export_biolin_thetaflex_contract_metadata()
 def read(self,role='evaluator'):
  d={'plant.configured':self.plant is not None,'power.enabled':self.power,'power.angle_rad':.32 if self.power else 0.,'lamp.measurement':self.power and self.phase=='running' and self.stage in ['capture','advance','recede'],'service.slide_m':self.service,'dispenser.z_m':self.dispenser,'dispenser.target_m':self.dispenser_target,'plunger.position_m':.04*(1-self.stock/self.plant['syringe_capacity_m3']) if self.plant else .04,'focus.position_m':self.focus,'illumination.fraction':self.illumination,'sample.present':self.sid is not None,'camera.calibrated':self.pitch is not None,'camera.pixel_pitch_m':self.pitch,'method.mode':self.method['mode'],'truth.drop_m3':self.volume,'truth.syringe_m3':self.stock,'truth.contact_angle_rad':self.theta,'truth.contact_radius_m':self.radius,'clock.time_s':self.time_s,'acquisition.phase':self.phase,'acquisition.stage':self.stage,'acquisition.elapsed_s':self.elapsed,'acquisition.points':len(self.rows)}
  for i,x in enumerate('xyz'):d['stage.'+x+'_m']=self.q[i];d['stage.target_'+x+'_m']=self.target[i]
  for s,k in [('angle.indicated_rad','mean_rad'),('angle.left_rad','left_rad'),('angle.right_rad','right_rad'),('volume.indicated_m3','volume_m3')]:d[s]=self.last_fit[k] if self.last_fit and self.power else None
  return {k:copy.deepcopy(v) for k,v in d.items() if role!='agent' or SLOTS[k][3]=='agent'}
 def _result(self):
  if self.phase!='running' and self.operation_id in self.records:return copy.deepcopy(self.records[self.operation_id])
  return dict(phase=self.phase,stage=self.stage,operation_id=self.operation_id,elapsed_s=self.elapsed,rows=copy.deepcopy(self.rows),image_png_base64=self.last_image,template_source=self.result_source,evidence=self.result_evidence,error_code=self.error)
 def _ledger(self):
  error=self.loaded-self.stock-self.volume-self.waste;return dict(loaded_m3=self.loaded,syringe_m3=self.stock,drop_m3=self.volume,waste_m3=self.waste,volume_error_m3=error,mass_error_kg=error*(self.plant['density_kg_m3'] if self.plant else 0.))
 def _idle(self):
  if self.phase=='running':raise ActionRejected('BUSY')
 def _ready(self):
  if not self.plant or not self.power or self.service>1e-9:raise ActionRejected('NOT_READY')
 def _aligned(self):
  if max(abs(a-b) for a,b in zip(self.q,[.04,.02,.025]))>.001:raise ActionRejected('SAMPLE_ALIGNMENT')
 def _capture(self):
  self._ready();self._aligned()
  if not self.volume or self.pitch is None:raise ActionRejected('NO_DROP_OR_CALIBRATION')
  s,b,p=render_drop(self.volume,self.theta,self.plant['density_kg_m3'],self.plant['surface_tension_n_m'],self.method['field_width_m'],self.focus,self.illumination,self.method['mode']=='dynamic');f=image_fit(s,b,self.pitch,self.method['fit']);return dict(image_png_base64=s,baseline_px=b,pixel_pitch_m=self.pitch,measurement=f)
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[k][0],a)
  if k=='sim.step':self.step(a['dt_s']);return dict(time_s=self.time_s)
  before=copy.deepcopy(self.__dict__)
  try:
   r=self._invoke(k,a);validate(CAPS[k][1],r)
   if CAPS[k][4] not in ['query','analysis']:self.revision+=1
   return r
  except Exception:self.__dict__.clear();self.__dict__.update(before);raise
 def _invoke(self,k,a):
  if k=='acquisition.result':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='analysis.image':return image_fit(**a)
  if k=='analysis.surface_energy':return surface_energy(a['observations'])
  if k=='analysis.hysteresis':
   if not a['advancing_rad'] or not a['receding_rad']:raise ActionRejected('INSUFFICIENT_PROBES')
   adv=max(a['advancing_rad']);rec=min(a['receding_rad'])
   if adv<rec:raise ActionRejected('INVALID_HYSTERESIS')
   return dict(advancing_rad=adv,receding_rad=rec,hysteresis_rad=adv-rec)
  if k=='image.capture':return self._capture()
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self.target=self.q[:];self.dispenser_target=self.dispenser;self._finish('cancelled');return {'phase':self.phase}
  self._idle()
  if k=='plant.configure':
   if self.plant is not None or self.power:raise ActionRejected('INITIALIZATION_ONLY')
   if not a['accept_engineering']:raise ActionRejected('MODEL_NOT_ACCEPTED')
   if (a['advancing_rad'] is None)!=(a['receding_rad'] is None) or (a['advancing_rad'] is not None and a['advancing_rad']<a['receding_rad']):raise ActionRejected('INVALID_HYSTERESIS')
   self.plant=copy.deepcopy(a);return dict(configured=True)
  if k=='power.set':
   if a['enabled'] and (not self.plant or self.service>1e-9):raise ActionRejected('NOT_READY')
   self.power=a['enabled'];return a
  if k=='service.set':
   if self.power:raise ActionRejected('POWER_INTERLOCK')
   self.service=a['distance_m'];return a
  if k=='focus.set':self.focus=a['position_m'];return a
  if k=='illumination.set':self.illumination=a['fraction'];return a
  if k=='method.configure':
   if a['mode']=='dynamic' and (not self.plant or not self.plant['accept_dynamic_proxy'] or self.plant['advancing_rad'] is None):raise ActionRejected('DYNAMIC_INPUTS_REQUIRED')
   if a['field_width_m']!=self.method['field_width_m']:self.pitch=None;self.last_fit=None;self.last_image=None
   self.method=copy.deepcopy(a);return a
  if k=='sample.load':
   if self.sid or self.volume or self.stock:raise ActionRejected('SAMPLE_PRESENT')
   if a['reference_id'] not in LIB['substances']:raise ActionRejected('INVALID_REFERENCE')
   s=LIB['substances'][a['reference_id']];theta=s['values']['contact_angle_rad']
   if s['conditions'].get('temperature_k') is None and not a['accept_unknown_conditions']:raise ActionRejected('UNKNOWN_CONDITIONS')
   if not .025<theta<math.pi-.025:raise ActionRejected('COMPLETE_WETTING_UNSUPPORTED')
   self.sid=a['reference_id'];self.theta=theta;return dict(loaded=True)
  if k=='sample.remove':
   if self.volume or self.stock:raise ActionRejected('LIQUID_PRESENT')
   if not self.sid:raise ActionRejected('NO_SAMPLE')
   self.sid=None;self.theta=None;self.radius=None;self.last_fit=None;self.last_image=None;return dict(removed=True)
  if k=='stock.load':
   if not self.plant or self.stock or a['reference_id']!=self.sid:raise ActionRejected('STOCK_OR_REFERENCE')
   if a['volume_m3']>self.plant['syringe_capacity_m3']:raise ActionRejected('CAPACITY')
   self.stock=a['volume_m3'];self.stock_sid=a['reference_id'];self.loaded+=self.stock;return dict(loaded=True)
  if k=='stock.unload':
   self.waste+=self.stock;self.stock=0.;self.stock_sid=None;return dict(unloaded=True)
  if k=='drop.clean':
   self.waste+=self.volume;self.volume=0.;self.radius=None;self.last_fit=None;self.last_image=None;self.theta=LIB['substances'][self.sid]['values']['contact_angle_rad'] if self.sid else None;return dict(cleaned=True)
  if k=='camera.calibrate':
   self._ready();self.pitch=a['known_length_m']/a['observed_pixels'];return dict(pixel_pitch_m=self.pitch)
  if k in ASYNC:
   self._ready()
   if k=='stage.move':
    if self.dispenser>.008:raise ActionRejected('NEEDLE_INTERLOCK')
    if a['z_m']+self.dispenser>STAGE_Z_MAX_M+1e-12:raise ActionRejected('NEEDLE_COLLISION')
    self.target=[a[x+'_m'] for x in 'xyz'];self.stage='move'
   elif k=='dispenser.move':
    if a['position_m']+self.q[2]>STAGE_Z_MAX_M+1e-12:raise ActionRejected('NEEDLE_COLLISION')
    self.dispenser_target=a['position_m'];self.stage='dispenser'
   elif k=='liquid.transfer':
    self._aligned();v=a['volume_m3']
    if not self.sid or self.stock_sid!=self.sid:raise ActionRejected('NO_SAMPLE')
    if v==0 or v>self.stock or -v>self.volume or self.stock-v>self.plant['syringe_capacity_m3'] or self.volume+v>1e-8:raise ActionRejected('FINITE_INVENTORY')
    if self.dispenser<.014:raise ActionRejected('NEEDLE_POSITION')
    if v<0 and (self.method['mode']!='dynamic' or self.dispenser<.024):raise ActionRejected('WITHDRAW_REQUIRES_NEEDLE_CONTACT')
    if self.method['mode']=='dynamic':
     rec=self.plant['receding_rad'];adv=self.plant['advancing_rad']
     if not rec<=self.theta<=adv:raise ActionRejected('DYNAMIC_INPUTS_REQUIRED')
     maxr=cap_radius(max(self.volume,self.volume+v),rec)*math.sin(rec);bo=self.plant['density_kg_m3']*G*maxr*maxr/self.plant['surface_tension_n_m']
     if bo>.1:raise ActionRejected('DYNAMIC_BOND_LIMIT')
    self.transfer_remaining=v;self.stage='advance' if v>0 else 'recede'
   else:
    self._aligned()
    if not self.volume or self.pitch is None:raise ActionRejected('NO_DROP_OR_CALIBRATION')
    if self.dispenser>.010 and self.method['mode']=='static':raise ActionRejected('NEEDLE_IN_IMAGE')
    self.stage='capture'
   self._counter+=1;self.operation_id=self.instance_id+':'+str(self._counter);self.phase='running';self.elapsed=0.;self.rows=[];self.transferred=0.;self.error=None;self.last_image=None;self.result_source=json.dumps(LIB['substances'][self.sid]['source'],sort_keys=True) if self.sid else None;self.result_evidence='Synthetic pixel image from Young-Laplace sessile profile' if self.method['mode']=='static' else 'Synthetic pixel image, quasistatic pinned small-Bond spherical cap; adv/rec endpoints explicit engineering input';return dict(operation_id=self.operation_id)
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _finish(self,p,error=None):
  self.phase=p;self.error=error
  if self.operation_id:self.records[self.operation_id]=self._result()
 def _observe(self):
  c=self._capture();self.last_fit=c['measurement'];self.last_fit_time=self.time_s;self.last_image=c['image_png_base64'];v=self.last_fit;self.rows.append(dict(time_s=self.time_s,transferred_m3=self.transferred,observed_volume_m3=v['volume_m3'],left_rad=v['left_rad'],right_rad=v['right_rad'],mean_rad=v['mean_rad'],branch=self.stage,fit_residual_px=v['residual_px']))
 def _tick(self):
  if self.phase!='running':return
  self.elapsed+=DT;done=False;exhausted=False
  if self.stage=='move':
   for i in range(3):self.q[i]+=math.copysign(min(abs(self.target[i]-self.q[i]),self.method['stage_speed_m_s']*DT),self.target[i]-self.q[i])
   done=max(abs(a-b) for a,b in zip(self.q,self.target))<1e-12
   if done:self.q=self.target[:]
  elif self.stage=='dispenser':
   self.dispenser+=math.copysign(min(abs(self.dispenser_target-self.dispenser),self.method['stage_speed_m_s']*DT),self.dispenser_target-self.dispenser);done=abs(self.dispenser-self.dispenser_target)<1e-12
   if done:self.dispenser=self.dispenser_target
  elif self.stage in ['advance','recede']:
   available=self.stock if self.transfer_remaining>0 else min(self.volume,self.plant['syringe_capacity_m3']-self.stock)
   v=math.copysign(min(abs(self.transfer_remaining),self.method['flow_m3_s']*DT,max(0.,available)),self.transfer_remaining);self.volume+=v;self.stock-=v;self.transferred+=v;self.transfer_remaining-=v
   if self.volume==0:self.radius=None
   elif self.method['mode']=='dynamic' and self.radius is not None:
    new=cap_theta(self.volume,self.radius);self.theta=min(max(new,self.plant['receding_rad']),self.plant['advancing_rad']);self.radius=cap_radius(self.volume,self.theta)*math.sin(self.theta)
   else:self.radius=cap_radius(self.volume,self.theta)*math.sin(self.theta)
   done=self.transfer_remaining==0.;exhausted=not done and abs(v)>=max(0.,available)
  n=max(1,round(1/(self.method['frame_rate_hz']*DT)));sample_tick=round(self.elapsed/DT)%n==0
  if self.stage=='capture' or self.method['mode']=='dynamic' and self.stage in ['advance','recede']:
   if (sample_tick or done) and (self.stage=='capture' or self.volume>5e-12) and self.pitch is not None:
    try:self._observe()
    except ActionRejected as e:self._finish('failed',str(e));return
   if self.stage=='capture':done=len(self.rows)>=self.method['frames']
  if exhausted:self._finish('failed','INVENTORY_EXHAUSTED')
  elif done:self._finish('succeeded')
 def step(self,dt_s):
  validate(number('s',0,3600),dt_s);self._remainder+=dt_s;n=int((self._remainder+1e-12)/DT);self._remainder-=n*DT
  for _ in range(n):self._ticks+=1;self.time_s=self._ticks*DT;self._tick()
  if n:self.revision+=1
class BiolinThetaFlexSession:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,i):
  if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  m=export_biolin_thetaflex_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
 def read(self,*,instance_id,state_ids=None):
  self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
  if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
  if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
  return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='unavailable' if v[k] is None else 'estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=h.last_fit_time if k.startswith('angle.') or k=='volume.indicated_m3' else h.time_s,sample_window_s=None) for k in ids})

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
   if capability_id=='acquisition.stop' and self._role=='agent' and h._owners.get(h.operation_id,self._principal)!=self._principal:raise ActionRejected('ACCESS_DENIED')
   result=h.invoke(capability_id,**arguments);op=result.get('operation_id') if capability_id in ASYNC else None
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





def export_biolin_thetaflex_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions='SI; contact angle measured through liquid; axisymmetric sessile droplet; camera baseline independently supplied',origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'BiolinThetaFlexSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description='10ms physics ticks; frame period rounded to ticks; requested rate1–20Hz emulator scope, not OEM maximum'),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','INVALID_IMAGE','NO_DROP_EDGE','BASELINE_OR_EDGE','EDGE_RESOLUTION','FIT_FAILED','FIELD_OF_VIEW','DROP_SHAPE_RANGE','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNKNOWN_OPERATION','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','INITIALIZATION_ONLY','BUSY','NOT_READY','NOT_RUNNING','MODEL_NOT_ACCEPTED','SAMPLE_ALIGNMENT','POWER_INTERLOCK','SAMPLE_PRESENT','NO_SAMPLE','STOCK_OR_REFERENCE','CAPACITY','LIQUID_PRESENT','INVALID_REFERENCE','UNKNOWN_CONDITIONS','COMPLETE_WETTING_UNSUPPORTED','DYNAMIC_INPUTS_REQUIRED','DYNAMIC_BOND_LIMIT','NEEDLE_INTERLOCK','NEEDLE_COLLISION','NEEDLE_POSITION','WITHDRAW_REQUIRES_NEEDLE_CONTACT','NEEDLE_IN_IMAGE','NO_DROP_OR_CALIBRATION','FINITE_INVENTORY','INVENTORY_EXHAUSTED','INSUFFICIENT_PROBES','DEGENERATE_PROBES','INVALID_HYSTERESIS']
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  un=k in UNSUPPORTED;asyn=k in ASYNC;caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if un else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if un else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if un else 'implemented'),binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.biolin_thetaflex.BiolinThetaFlexSession.invoke',invocation=f'session.invoke(instance_id="thetaflex_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if kind in ['query','analysis'] or un else [dict(description_zh='原子参数、供电/库存/针位/台位/基线/标定互锁；非原厂硬件认证',state_ids=['power.enabled','plant.configured','acquisition.phase','sample.present'],enforced_by='runtime',check='atomic invoke;registered sessions;host step only')],state_effects=[dict(state_id=x,timing='immediate',description_zh='命令直接状态') for x in IMMEDIATE_EFFECTS.get(k,effects)],observes=[],model_effects=dict(mode='none',description_zh='场景姿态另行检验',binding_ids=[]),execution=dict(mode='asynchronous' if asyn else 'synchronous',time_domain='simulation',completion_criterion='位置到达、指定液体转移完成或拍摄指定帧数' if asyn else '同步操作返回；不推进时钟',completion_state_ids=['acquisition.phase'] if asyn else [],timeout_s=None,cancellation='supported' if asyn else 'unsupported',retry='same_request_id',resources=['host_clock','camera','sample_stage','dispenser','liquid'],partial_failure_zh='保留已转移液体、位置和图像，取消不回滚物理部分效果'),errors=[dict(code=x,meaning_zh=x,side_effects='partial' if asyn and x in ['INVENTORY_EXHAUSTED','INVALID_IMAGE','NO_DROP_EDGE','BASELINE_OR_EDGE','EDGE_RESOLUTION','FIT_FAILED','FIELD_OF_VIEW','DROP_SHAPE_RANGE'] else 'none') for x in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=['±0.1度为厂家指标，当前图像算法未通过该准确度认证','真实相机响应/噪声/真机故障/动态摩擦未验证'],source_ids=['manufacturer_specs','simulation_assumption'])
  if k in STEP_EFFECTS:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='固定时步推进物料/执行器/图像观测') for x in STEP_EFFECTS[k]]
 mech=dict(component_ids=['camera','objective','led_diffuser','sample_plate','stage_x','stage_y','stage_z','syringe','needle','plunger'],state_ids=list(slots),meaning_zh='有穷滴液及准静态润湿轮廓、成像、独立接触角与表面能分析',implementation='lumped',equations=['Finite transfer dVdrop=-dVsyringe=Qdt clipped to requested signed volume; cleaning/unloading enters waste; total loaded=syringe+drop+waste','Sessile Young-Laplace: dr/ds=cos(phi), dz/ds=sin(phi), dphi/ds=2/Rapex+rho*g*z/gamma-sin(phi)/r; z downward from apex; shootRapex to finite volume, stop at reference contact angle','Dynamic only Bo_base<=0.1: pinned spherical cap V=pi*h*(3a^2+h^2)/6,theta=2atan(h/a); hold contact radius until explicitly supplied advancing/receding limits; contact line then moves; no hydrodynamic transient or needle meniscus model','1984x1264 uint8 projected polygon, optical blur from explicit focus model, background intensity; no empirical noise','Camera field_width is horizontal effectiveFOV engineering mapping of manufacturer diameter range; certified magnification/diameter-to-sensor relation unknown','Pixel Otsu segmentation selects largest connected droplet; known baseline, cubic side-edge polynomial tangent or circle least squares; neither reads reference contact angle','Observed volume=pi*integral(half_width_pixels^2)*calibrated_pitch^3; length calibration independent external length/pixels, FOV change invalidates it','OWRK gammaL*(1+cos(theta))=2*(sqrt(gammaSd*gammaLd)+sqrt(gammaSp*gammaLp)); nonnegative least squares from independent liquid components and observedangles; reject rank deficiency','Hysteresis=max(externaladvancingangles)-min(externalrecedingangles); data-backed static reference never silently turned into empirical hysteresis','Only hoststep advances time; pure imageanalysis/read/result queries cannot mutate physics or take operation ownership'],parameters={},source_ids=['manufacturer_specs','manufacturer_brochure','aluminum_data','bursik_data','dropletlab_data','simulation_assumption'],limitations=['168 experimentalresponse rows:90Alformulations/45PTFEpure rows/33validbinarymixture rows; no50distinctpurechemical species; source anomalies excluded','127processedDropometerimages retained as real reference images, not nativeThetaFlexcamera data; independent fullrawimage metrology not_run','Static YoungLaplace numerical model, dynamic smallBond sphere approximation with explicitly assumedadvrec endpoints; full dynamic NavierStokes/contactline/needle distortion not_run','Certified0.1degree accuracy,3009fpsROI/fullresolutionbandwidth,CCDdistortion/noise/illuminationcalibration not_run','Localgeometry/actuatorrates/stroke/capacity andallocated26kg are numericalengineering proxies','Auto1 sampleXYZ commands represent manualpositioning; autonomous3axisOEMstage is not claimed','Pendantdrop/captivebubble/tilt/highpressure/topography/interfacialrheology/OEMcertifiedcalibration/hardware/robot not_run','Nominal3Ddrop size is fixed; quantitative changing drop pixels are API/outputPNG, not PhysXfluid mesh','Stage Z<=0.049m and stage Z+dispenser descent<=0.049m enforce local scene needle clearance0.1mm; engineering envelope, not manufacturer stroke','No filesystem/process sandbox truth-isolation claim'])
 spec=importlib.util.spec_from_file_location('_theta_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'optical_tensiometry':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'。'
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'optical_tensiometry':mech})

"""CHEM-115 finite volumetric N2 physisorption with explicit source-branch uncertainty."""
from pathlib import Path
import copy,math,json,importlib.util
PROTOCOL_VERSION='1.0.0';SLUG='antonpaar_autosorb6100';CATALOG_ID='CHEM-115';ASSET_TYPE_ID='instrument.'+SLUG
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
import bisect
DT=.1;R=8.314462618;NA=6.02214076e23;LIB=json.loads((ASSET/'adsorption_library.json').read_text());N2=LIB['nitrogen_77k'];P0=N2['p0_pa'];MW=N2['molar_mass_kg_mol'];VLIQ=MW/N2['liquid_density_kg_m3'];SID=next(k for k,v in LIB['substances'].items() if v['name']=='PAF-1')
INT=lambda lo,hi:dict(number('1',lo,hi),type='integer')
STATION=INT(0,2);DEGAS=INT(0,5)
PLANT=obj({'ambient_k':number('K',288.15,308.15),'manifold_initial_k':number('K',288.15,323.15),'manifold_target_k':number('K',308.15,323.15),'source_volume_m3':number('m^3',.00001,.1),'source_pressure_pa':number('Pa',100000,1000000),'source_conductance':number('mol/(s*Pa)',1e-11,1e-6),'pump_conductance':number('mol/(s*Pa)',1e-11,1e-6),'adsorption_rate_s':number('1/s',.001,10),'warm_volume_m3':arr(number('m^3',1e-7,1e-3),3),'cold_volume_m3':arr(number('m^3',1e-7,1e-3),3),'pressure_gain':arr(number('1',.8,1.2),3),'pressure_offset_pa':arr(number('Pa',-100,100),3),'p0_gain':number('1',.99,1.01),'heat_capacity_j_k':number('J/K',50,10000),'heat_loss_w_k':number('W/K',.01,10),'heater_w':number('W',1,1000),'ln2_heat_leak_w':number('W',0,100),'adsorption_heat_j_mol':number('J/mol',0,50000),'degas_heat_capacity_j_k':number('J/K',20,5000),'degas_power_w':number('W',1,1000),'degas_loss_w_k':number('W/K',.01,10),'trap_capacity_kg':number('kg',1e-6,.5),'accept_engineering_model':BOOL})
PLANT_EXAMPLE=dict(ambient_k=298.15,manifold_initial_k=308.15,manifold_target_k=308.15,source_volume_m3=.002,source_pressure_pa=300000.,source_conductance=1e-8,pump_conductance=1e-6,adsorption_rate_s=.5,warm_volume_m3=[10e-6]*3,cold_volume_m3=[10e-6]*3,pressure_gain=[1.]*3,pressure_offset_pa=[0.]*3,p0_gain=1.,heat_capacity_j_k=1500.,heat_loss_w_k=.2,heater_w=200.,ln2_heat_leak_w=1.,adsorption_heat_j_mol=6000.,degas_heat_capacity_j_k=400.,degas_power_w=200.,degas_loss_w_k=.1,trap_capacity_kg=.02,accept_engineering_model=True)
METHOD=obj({'station':STATION,'gas':enum(['N2']),'temperature_k':number('K',77,77),'relative_pressures':arr(number('1',1e-6,.997)),'equilibration_s':number('s',.1,120),'pressure_tolerance_pa':number('Pa',.00266644,100),'max_drift_pa_s':number('Pa/s',.0001,100),'point_timeout_s':number('s',1,7200),'entered_mass_kg':number('kg',1e-8,.1)})
METHOD_EXAMPLE=dict(station=0,gas='N2',temperature_k=77.,relative_pressures=[.002,.005,.01,.02,.03,.05,.07,.10,.15,.20,.25,.30,.40,.50,.60,.70,.80,.90],equilibration_s=2.,pressure_tolerance_pa=.5,max_drift_pa_s=.05,point_timeout_s=600.,entered_mass_kg=.0001)
SAMPLE=obj({'station':STATION,'sample_id':TEXT,'reference_id':TEXT,'true_dry_mass_kg':number('kg',1e-8,.1),'predegassed':BOOL,'accept_branch_and_equilibrium_transfer':BOOL})
SAMPLE_EXAMPLE=dict(station=0,sample_id='private_paf',reference_id=SID,true_dry_mass_kg=.0001,predegassed=True,accept_branch_and_equilibrium_transfer=True)
VCAL=obj({'station':STATION,'warm_temperature_k':number('K',288.15,323.15),'warm_dosed_mol':number('mol',1e-10,.1),'warm_pressure_pa':number('Pa',1,146655.61),'cold_dosed_mol':number('mol',1e-10,.1),'cold_pressure_pa':number('Pa',1,146655.61)})
VCAL_EXAMPLE=dict(station=0,warm_temperature_k=308.15,warm_dosed_mol=50000*20e-6/(R*308.15),warm_pressure_pa=50000.,cold_dosed_mol=50000/R*(10e-6/308.15+10e-6/77.),cold_pressure_pa=50000.)
PCAL=obj({'station':STATION,'reference_pa':arr(number('Pa',0,146655.61),2),'measured_pa':arr(number('Pa',-100,160000),2)})
PCAL_EXAMPLE=dict(station=0,reference_pa=[0.,100000.],measured_pa=[0.,100000.])
POINT=obj({'time_s':number('s',0),'pressure_pa':number('Pa',0),'relative_pressure':number('1',0),'loading_mol_kg':number('mol/kg'),'p0_pa':number('Pa',0),'temperature_k':number('K',77,323.15)})
BET=obj({'status':TEXT,'area_m2_kg':nullable(number('m^2/kg',0)),'C':nullable(number('1',0)),'monolayer_mol_kg':nullable(number('mol/kg',0)),'r_squared':nullable(number('1',0,1)),'pressure_range':nullable(arr(number('1',0,1),2)),'validity':arr(TEXT)})
PSD=obj({'status':TEXT,'diameter_m':arr(number('m',0)),'pore_volume_m3_kg':arr(number('m^3/kg')),'distribution_m2_kg':arr(number('m^2/kg')),'warnings':arr(TEXT)})
RESULT=obj({'phase':PHASE,'stage':TEXT,'elapsed_s':number('s',0),'stations':arr(obj({'station':STATION,'phase':PHASE,'points':arr(POINT),'bet':BET,'total_pore_volume_m3_kg':nullable(number('m^3/kg',0)),'pore_volume_status':TEXT,'psd':PSD})),'trace':arr(obj({'time_s':number('s',0),'pressures_pa':arr(number('Pa',0),3),'manifold_k':number('K',280,730),'ln2_m3':number('m^3',0)})),'warnings':arr(TEXT)})
LEDGER=obj({'source_mol':number('mol',0),'station_total_mol':arr(number('mol',0),3),'adsorbed_mol':arr(number('mol',0),3),'pumped_mol':number('mol',0),'n2_residual_mol':number('mol'),'sample_loaded_kg':number('kg',0),'sample_present_kg':number('kg',0),'sample_returned_kg':number('kg',0),'sample_residual_kg':number('kg'),'ln2_remaining_kg':number('kg',0),'ln2_boiloff_kg':number('kg',0),'ln2_residual_kg':number('kg'),'degas_water_remaining_kg':number('kg',0),'trap_water_kg':number('kg',0),'degas_water_returned_kg':number('kg',0),'water_residual_kg':number('kg'),'heater_energy_j':number('J',0),'manifold_energy_residual_j':number('J'),'degas_energy_residual_j':number('J'),'cold_load_energy_j':number('J',0)})
def interp(x,y,p):
 if p<=x[0]:return y[0]
 if p>=x[-1]:return y[-1]
 j=bisect.bisect_right(x,p)-1;return y[j]+(y[j+1]-y[j])*(p-x[j])/(x[j+1]-x[j])
def bet_fit(points,mass=None,branch_confirmed=False):
 rows=[(p['relative_pressure'],p['loading_mol_kg']) for p in points if 0<p['relative_pressure']<.5 and p['loading_mol_kg']>0];best=None
 for i in range(len(rows)-3):
  for j in range(i+4,len(rows)+1):
   a=rows[i:j];x=[z[0] for z in a];q=[z[1] for z in a];roq=[b*(1-a) for a,b in zip(x,q)]
   if any(b<a for a,b in zip(roq,roq[1:])):continue
   y=[a/(b*(1-a)) for a,b in zip(x,q)];xm=sum(x)/len(x);ym=sum(y)/len(y);den=sum((v-xm)**2 for v in x)
   if den<=0:continue
   slope=sum((v-xm)*(w-ym) for v,w in zip(x,y))/den;inter=ym-slope*xm
   if inter<=0 or slope+inter<=0:continue
   qm=1/(slope+inter);C=1+slope/inter
   if C<=0 or not q[0]<qm<q[-1]:continue
   predicted=1/(1+math.sqrt(C));actual=interp(q,x,qm)
   if not x[0]<predicted<x[-1] or abs(actual-predicted)/predicted>.2:continue
   var=sum((v-ym)**2 for v in y);r2=max(0.,min(1.,1-sum((b-slope*a-inter)**2 for a,b in zip(x,y))/var)) if var else 0.
   if r2<.995:continue
   area=qm*NA*N2['cross_section_m2'];candidate=dict(status='valid_estimate' if branch_confirmed else 'conditional_branch_unspecified',area_m2_kg=area,C=C,monolayer_mol_kg=qm,r_squared=r2,pressure_range=[x[0],x[-1]],validity=['Cpositive','q(1-x)increasing','monolayer_in_window','monolayer_pressure_agreement20pct','R2>=0.995','N2cross_section0.162nm2'])
   if mass is not None and area*mass<.1:candidate['status']='below_documented_absolute_area_threshold';candidate['validity'].append('0.1m2N2threshold_not_empirically_validated')
   score=(j-i,r2)
   if best is None or score>best[0]:best=score,candidate
 return best[1] if best else dict(status='invalid_or_insufficient_BET_window',area_m2_kg=None,C=None,monolayer_mol_kg=None,r_squared=None,pressure_range=None,validity=['no_valid_Rouquerol_window'])
def bjh(points,branch):
 if branch!='desorption':return dict(status='not_available_branch_unspecified',diameter_m=[],pore_volume_m3_kg=[],distribution_m2_kg=[],warnings=['BJHrequiresidentifieddesorption; noDFTkernel'])
 rows=sorted([(p['relative_pressure'],p['loading_mol_kg']*VLIQ) for p in points if .35<=p['relative_pressure']<.997],reverse=True)
 if len(rows)<3 or any(rows[j+1][1]>rows[j][1] or rows[j+1][0]>=rows[j][0] for j in range(len(rows)-1)):return dict(status='invalid_desorption_data',diameter_m=[],pore_volume_m3_kg=[],distribution_m2_kg=[],warnings=['atleast3monotonepointsrequired'])
 ts=[.354e-9*(5/(-math.log(x)))**(1/3) for x,q in rows];ks=[-2*N2['surface_tension_n_m']*VLIQ/(R*77*math.log(x)) for x,q in rows];rp=[a+b for a,b in zip(ts,ks)];rads=[];vols=[];areas=[];dist=[];warn=['classicalcylindricalBJH/Halsey; notmicroporeDFT; surfacefilm/blocking/cavitationuncertainty']
 for i in range(len(rows)-1):
  rad=(rp[i]+rp[i+1])/2;dt=ts[i]-ts[i+1];t=(ts[i]+ts[i+1])/2;kr=(ks[i]+ks[i+1])/2;film=dt*sum((old-t)/old*area for old,area in zip(rads,areas));v=(rows[i][1]-rows[i+1][1]-film)*(rad/(kr+dt))**2;rads.append(rad);vols.append(v);areas.append(2*v/rad);dist.append(v/(2*(rp[i]-rp[i+1])))
  if v<0:warn.append('negative_bin_retained_invalid')
 use=[i for i,r in enumerate(rads) if 2.5e-9<=2*r<=500e-9];return dict(status='invalid_negative_bins' if any(v<0 for v in vols) else 'engineering_BJH',diameter_m=[2*rads[i] for i in use],pore_volume_m3_kg=[vols[i] for i in use],distribution_m2_kg=[dist[i] for i in use],warnings=warn)
CAPS={};SLOTS={};SLOT_ZH={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=None):CAPS[k]=(i,o,c,z,kind,vis,ex or {},effects or [])
def slot(k,s,u,z,o='derived',v='agent',c='controller',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
cap('plant.configure',PLANT,obj({'configured':BOOL}),['controller','manifold'],'初始化有限气源、三个死体积、热量/动力学假设','configuration','evaluator',PLANT_EXAMPLE,['plant.configured'])
cap('method.configure',METHOD,obj({'configured':BOOL}),['controller'],'独立分析位N277K压力点和质量输入','configuration',ex=METHOD_EXAMPLE)
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_switch','status_lamp'],'电源；断电取消分析和脱气并保留资源账本',ex={'enabled':True},effects=['power.enabled','power.angle_rad','lamp.power'])
MOTIONS=[('dewar','dewar_lift','lift_m',.29,'m'),('guard','guard','angle_rad',1.5,'rad')]+[('sample'+str(i),'sample_'+str(i),'drop_m',.18,'m') for i in range(3)]+[('heater'+str(i),'heater_'+str(i),'drop_m',.16,'m') for i in range(2)]
for k,part,arg,hi,u in MOTIONS:
 cap(k+'.set',obj({arg:number(u,0,hi)}),obj({arg:number(u,0,hi)}),[part],'受检人工位置动作，实际抓取/密封未实现',ex={arg:0.},effects=[k+'.'+arg]);slot(k+'.'+arg,number(u,0,hi),u,'升降/门/玻璃管人工位姿','scene',c=part,w=(k+'.set',arg))
cap('sample.load',SAMPLE,obj({'loaded':BOOL}),['sample_0','sample_1','sample_2'],'宿主装入有限干固体并明确接受实测曲线迁移','configuration','evaluator',SAMPLE_EXAMPLE)
cap('sample.unload',obj({'station':STATION}),obj({'returned_mass_kg':number('kg',0)}),['sample_0','sample_1','sample_2'],'宿主领回样品；气体须先抽至卸样条件',vis='evaluator',ex={'station':0})
cap('coolant.fill',obj({'volume_m3':number('m^3',1e-6,.003)}),obj({'volume_m3':number('m^3',0,.003)}),['dewar'],'宿主加入有限液氮，最多3L','configuration','evaluator',{'volume_m3':.003})
cap('pressure.calibrate',PCAL,obj({'calibrated':BOOL}),['pressure_transducer_0','pressure_transducer_1','pressure_transducer_2'],'各分析位独立两点线性压力标定','configuration',ex=PCAL_EXAMPLE)
cap('volume.calibrate',VCAL,obj({'warm_volume_m3':number('m^3',0),'cold_volume_m3':number('m^3',0)}),['manifold','sample_0','sample_1','sample_2'],'外部非吸附气体两温度剂量与压力数据反演冷热自由空间','configuration',ex=VCAL_EXAMPLE)
cap('acquisition.start',obj({'stations':arr(STATION)}),obj({'operation_id':TEXT,'phase':PHASE}),['controller','manifold','dewar'],'选定分析位并行抽空、有限剂量、平衡、逐点反演，完成后抽空',ex={'stations':[0]},effects=['acquisition.phase','acquisition.stage'])
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['controller','manifold'],'取消保留已完成点和耗材；后续step有限抽空',effects=['acquisition.phase'])
cap('process.measure',obj({}),RESULT,['controller'],'只读已采集等温线、条件BET、孔容及缺失孔分布原因','query')
cap('analysis.bjh',obj({'points':arr(POINT),'branch':enum(['desorption','unspecified'])}),PSD,['controller'],'显式脱附数据的圆柱BJH/Halsey计算，负值不掩盖','analysis',ex={'points':[],'branch':'unspecified'})
cap('ledger.read',obj({}),LEDGER,['controller'],'有限N2、固体、水、液氮和定义热系统守恒账本','query','evaluator')
cap('template.reference',obj({'substance_id':TEXT}),obj({'name':TEXT,'evidence':TEXT,'source':TEXT,'branch':nullable(TEXT),'point_count':INT(0,100000)}),['controller'],'原始NIST曲线来源元数据','query',ex={'substance_id':SID})
DLOAD=obj({'slot':DEGAS,'dry_mass_kg':number('kg',1e-8,.1),'water_kg':number('kg',0,.02),'water_rate_at_373_s':number('1/s',1e-6,1),'activation_j_mol':number('J/mol',0,100000)})
DLOAD_EXAMPLE=dict(slot=0,dry_mass_kg=.0001,water_kg=.00001,water_rate_at_373_s=.03,activation_j_mol=20000.)
DPROGRAM=obj({'steps':arr(obj({'temperature_k':number('K',298.15,723.15),'hold_s':number('s',.1,86400)})),'ramp_k_s':number('K/s',.01,20),'remaining_fraction_limit':number('1',.000001,.1)})
DPROGRAM_EXAMPLE=dict(steps=[{'temperature_k':373.15,'hold_s':120.}],ramp_k_s=1.,remaining_fraction_limit=.01)
cap('degas.load',DLOAD,obj({'loaded':BOOL}),['degas_bulb_'+str(i) for i in range(6)],'宿主装入脱气样品；可移除水分为有限显式假设','configuration','evaluator',DLOAD_EXAMPLE)
cap('degas.unload',obj({'slot':DEGAS}),obj({'dry_mass_kg':number('kg',0),'water_kg':number('kg',0)}),['degas_bulb_'+str(i) for i in range(6)],'冷却后取出脱气样品；不暗中转移到分析位',vis='evaluator',ex={'slot':0})
cap('degas.start',DPROGRAM,obj({'phase':PHASE}),['heater_0','heater_1','cold_trap'],'两温区有限热量、多步升温保持、水分解吸并汇入有限冷阱；非厂家动力学',ex=DPROGRAM_EXAMPLE,effects=['degas.phase'])
cap('degas.stop',obj({}),obj({'phase':PHASE}),['heater_0','heater_1'],'取消脱气后功率归零，后续step冷却，水和固体保留',effects=['degas.phase'])
UNSUPPORTED={'hardware.connect','fault.probability_model','calibration.acquire_he','pore.nldft','chemisorption.run','cryosync.multitemperature','robot.load'}
for k in sorted(UNSUPPORTED):cap(k,obj({}),obj({}),['controller'],'未实现；不返回伪测量')
slot('plant.configured',BOOL,'1','宿主工程参数已初始化','setpoint');slot('power.enabled',BOOL,'1','电源','setpoint',w=('power.set','enabled'));slot('power.angle_rad',number('rad',0,.6),'rad','侧面电源开关转角','scene',c='power_switch');slot('lamp.power',BOOL,'1','侧面电源指示灯','derived',c='status_lamp')
for k in ['phase','stage','failure']:slot('acquisition.'+k,PHASE if k=='phase' else TEXT,'1','当前分析操作'+k)
slot('acquisition.elapsed_s',number('s',0),'s','分析操作耗时');slot('thermal.manifold_k',number('K',280,730),'K','集总歧管温度','sensor');slot('thermal.target_k',number('K',308.15,323.15),'K','歧管设定温度','setpoint');slot('pressure.p0_pa',nullable(number('Pa',0)),'Pa','液氮充足时独立p0测量位读数','sensor',c='p0_port');slot('coolant.volume_m3',number('m^3',0,.003),'m^3','有限液氮余量','sensor',c='dewar')
for i in range(3):
 for k,s,u,o,v,z in [('pressure_pa',number('Pa',0),'Pa','sensor','agent','分析位压力读数'),('phase',PHASE,'1','derived','agent','分析位状态'),('loaded',BOOL,'1','derived','agent','分析管是否装样'),('points',INT(0,100000),'1','derived','agent','已采集点数'),('bet_m2_kg',nullable(number('m^2/kg',0)),'m^2/kg','derived','agent','压力收支反演的条件BET面积'),('identity',TEXT,'1','process_truth','evaluator','样品身份'),('mass_kg',number('kg',0),'kg','process_truth','evaluator','实际干质量'),('adsorbed_mol',number('mol',0),'mol','process_truth','evaluator','实际吸附量')]:slot('station'+str(i)+'.'+k,s,u,z,o,v,'sample_'+str(i))
slot('degas.phase',PHASE,'1','两温区脱气程序状态');slot('degas.target_k',number('K',280,730),'K','脱气程序当前温度目标','setpoint')
for i in range(2):slot('degas'+str(i)+'.temperature_k',number('K',280,730),'K','脱气加热套温度','sensor',c='heater_'+str(i))
slot('degas.water_kg',number('kg',0),'kg','当前脱气水分真值','process_truth','evaluator');slot('degas.dry_enough',BOOL,'1','达到宿主水分阈值，非经验脱气完成判据')
cap('sample.backfill',obj({'station':STATION}),obj({'started':BOOL}),['sample_0','sample_1','sample_2'],'杜瓦放下后用有限N2回填至环境压力，step完成后才能拆管',ex={'station':0})
class AntonpaarAutosorb6100Runtime:
 def __init__(self,instance_id='autosorb6100_01'):
  self.instance_id=instance_id;self.time_s=0.;self.pending=0.;self.revision=0;self.plant=None;self.powered=False;self.motion={k:0. for k,p,a,h,u in MOTIONS};self.temp=308.15;self.target=308.15;self.source=0.;self.source_initial=0.;self.n_initial=0.;self.pumped=0.;self.mass_loaded=0.;self.mass_returned=0.;self.ln2=0.;self.ln2_added=0.;self.boiled=0.;self.heat=0.;self.loss=0.;self.cold_energy=0.;self.phase='idle';self.stage='idle';self.failure='';self.elapsed=0.;self.active=[];self.serial=0;self.operation_id=None;self.records={};self.trace=[];self._principals={};self._owners={};self._requests={};self.stations=[dict(sample=None,method=None,pcal=None,vcal=None,n=0.,ads=0.,p=0.,phase='idle',stage='idle',time=0.,stable=0.,index=0,points=[],baseline=0.,admitted=0.,removed=0.,mass_run=None,bet=bet_fit([])) for _ in range(3)];self.degas=[None]*6;self.dtemps=[298.15]*2;self.dstart=[298.15]*2;self.dheat=[0.]*2;self.dloss=[0.]*2;self.degas_phase='idle';self.dprogram=None;self.dindex=0;self.dhold=0.;self.dtarget=298.15;self.water_loaded=0.;self.water_returned=0.;self.trap=0.
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ['agent','debug','evaluator'] or principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return AntonpaarAutosorb6100Session(self,principal_id)
 def describe(self):return export_antonpaar_autosorb6100_contract_metadata()
 def cold_k(self):return 77. if self.motion['dewar']>=.28 and self.ln2>0 else self.plant['ambient_k'] if self.plant else 298.15
 def alpha(self,i,cal=False):
  s=self.stations[i];w,c=s['vcal'] if cal and s['vcal'] else (self.plant['warm_volume_m3'][i],self.plant['cold_volume_m3'][i]);return (w/self.temp+c/self.cold_k())/R
 def pressure(self,i):
  if not self.plant:return 101325.
  s=self.stations[i];return max(0.,(s['n']-s['ads'])/self.alpha(i))
 def indicated(self,i):
  if not self.plant:return 101325.
  s=self.stations[i];v=self.pressure(i)*self.plant['pressure_gain'][i]+self.plant['pressure_offset_pa'][i]
  if s['pcal']:v=(v-s['pcal'][1])/s['pcal'][0]
  return max(0.,round(v/.00266644)*.00266644)
 def pzero(self):return P0*self.plant['p0_gain'] if self.plant and self.ln2>0 else None
 def read(self,visibility='agent'):
  d={'plant.configured':bool(self.plant),'power.enabled':self.powered,'power.angle_rad':.6 if self.powered else 0.,'lamp.power':self.powered,'acquisition.phase':self.phase,'acquisition.stage':self.stage,'acquisition.failure':self.failure,'acquisition.elapsed_s':self.elapsed,'thermal.manifold_k':self.temp,'thermal.target_k':self.target,'pressure.p0_pa':self.pzero(),'coolant.volume_m3':self.ln2/N2['liquid_density_kg_m3'],'degas.phase':self.degas_phase,'degas.target_k':self.dtarget,'degas0.temperature_k':self.dtemps[0],'degas1.temperature_k':self.dtemps[1],'degas.water_kg':sum(x['water_kg'] for x in self.degas if x),'degas.dry_enough':bool(self.dprogram) and all(not x or x['water_kg']<=x['initial_water_kg']*self.dprogram['remaining_fraction_limit']+1e-15 for x in self.degas)}
  d.update({k+'.'+a:self.motion[k] for k,p,a,h,u in MOTIONS})
  for i,s in enumerate(self.stations):
   prefix='station'+str(i)+'.';v={'pressure_pa':self.indicated(i),'phase':s['phase'],'loaded':bool(s['sample']),'points':len(s['points']),'bet_m2_kg':s['bet']['area_m2_kg'],'identity':s['sample']['sample_id'] if s['sample'] else '', 'mass_kg':s['sample']['true_dry_mass_kg'] if s['sample'] else 0.,'adsorbed_mol':s['ads']};d.update({prefix+k:x for k,x in v.items()})
  return copy.deepcopy({k:v for k,v in d.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
 def _result(self):
  rows=[]
  for i,s in enumerate(self.stations):
   if s['points'] or s['method']:
    near=[p for p in s['points'] if .95<=p['relative_pressure']<.997];v=near[-1]['loading_mol_kg']*VLIQ if near else None;rows.append(dict(station=i,phase=s['phase'],points=copy.deepcopy(s['points']),bet=copy.deepcopy(s['bet']),total_pore_volume_m3_kg=max(v,0.) if v is not None else None,pore_volume_status='conditional_liquid_filling_at_recorded_p_relative' if v is not None else 'not_available_no_point_p_relative>=0.95',psd=bjh(s['points'],'unspecified')))
  return dict(phase=self.phase,stage=self.stage,elapsed_s=self.elapsed,stations=rows,trace=copy.deepcopy(self.trace),warnings=['NISTexperimentalN277Ksourcebranchesandoriginalp0unspecified; isotonic/interpolationtransferexplicitengineeringassumption','pressureandfree-spacecalibrationindependent; reportedloadingfromfiniteN2inventoryminusfreegas/enteredmass','BETconditional; 0.1m2thresholdand2e-5Torrquantizationnotempiricalaccuracyvalidation','BJHrequiresidentifieddesorption; 0.35nmDFTand500nmfullrangeunvalidated','77KsamplebathboundaryidealwhenDewarraised; gas/supply/pump/samplewholeenthalpynotsolved; manifoldandheaterenergyledgersbounded','NoOEMhardware/noise/robot/sealvalidation'])
 def _ledger(self):
  mass=sum(s['sample']['true_dry_mass_kg'] for s in self.stations if s['sample'])+sum(x['dry_mass_kg'] for x in self.degas if x);water=sum(x['water_kg'] for x in self.degas if x);p=self.plant
  return dict(source_mol=self.source,station_total_mol=[s['n'] for s in self.stations],adsorbed_mol=[s['ads'] for s in self.stations],pumped_mol=self.pumped,n2_residual_mol=self.source_initial+self.n_initial-self.source-sum(s['n'] for s in self.stations)-self.pumped,sample_loaded_kg=self.mass_loaded,sample_present_kg=mass,sample_returned_kg=self.mass_returned,sample_residual_kg=self.mass_loaded-self.mass_returned-mass,ln2_remaining_kg=self.ln2,ln2_boiloff_kg=self.boiled,ln2_residual_kg=self.ln2_added-self.ln2-self.boiled,degas_water_remaining_kg=water,trap_water_kg=self.trap,degas_water_returned_kg=self.water_returned,water_residual_kg=self.water_loaded-water-self.water_returned-self.trap,heater_energy_j=self.heat+sum(self.dheat),manifold_energy_residual_j=(p['heat_capacity_j_k']*(self.temp-p['manifold_initial_k'])-self.heat+self.loss) if p else 0.,degas_energy_residual_j=sum(p['degas_heat_capacity_j_k']*(t-t0)-h+l for t,t0,h,l in zip(self.dtemps,self.dstart,self.dheat,self.dloss)) if p else 0.,cold_load_energy_j=self.cold_energy)
 def invoke(self,k,**a):
  if k not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  if k in UNSUPPORTED:raise ActionRejected('UNSUPPORTED')
  validate(CAPS[k][0],a);before=copy.deepcopy(self.__dict__)
  try:
   r=self._invoke(k,a);validate(CAPS[k][1],r)
   if CAPS[k][4] not in ['query','analysis']:self.revision+=1
   return r
  except Exception:self.__dict__.clear();self.__dict__.update(before);raise
 def _invoke(self,k,a):
  if k=='process.measure':return self._result()
  if k=='ledger.read':return self._ledger()
  if k=='analysis.bjh':return bjh(a['points'],a['branch'])
  if k=='template.reference':
   d=LIB['substances'].get(a['substance_id'])
   if d is None:raise ActionRejected('UNKNOWN_REFERENCE')
   return dict(name=d['name'],evidence=d['evidence'],source=d['source']['url'],branch=d['conditions']['branch'],point_count=len(d['value']))
  if k=='plant.configure':
   if self.plant or self.powered or not a['accept_engineering_model']:raise ActionRejected('INITIALIZATION_ONLY')
   self.plant=copy.deepcopy(a);self.temp=a['manifold_initial_k'];self.target=a['manifold_target_k'];self.source=a['source_pressure_pa']*a['source_volume_m3']/(R*a['ambient_k']);self.source_initial=self.source;self.dtemps=[a['ambient_k']]*2;self.dstart=self.dtemps.copy();self.dtarget=a['ambient_k']
   for i,s in enumerate(self.stations):s['n']=101325*self.alpha(i)
   self.n_initial=sum(s['n'] for s in self.stations);return {'configured':True}
  if k=='power.set':
   if not a['enabled']:
    if self.phase=='running':self._end('cancelled')
    if self.degas_phase=='running':self.degas_phase='cancelled'
   self.powered=a['enabled'];return a
  if not self.plant:raise ActionRejected('NOT_CONFIGURED')
  if k=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('NOT_RUNNING')
   self._end('cancelled');return {'phase':self.phase}
  if k=='degas.stop':
   if self.degas_phase!='running':raise ActionRejected('NOT_RUNNING')
   self.degas_phase='cancelled';return {'phase':self.degas_phase}
  if k=='coolant.fill':
   m=a['volume_m3']*N2['liquid_density_kg_m3']
   if self.ln2+m>.003*N2['liquid_density_kg_m3']+1e-12:raise ActionRejected('CAPACITY')
   self.ln2+=m;self.ln2_added+=m;return {'volume_m3':self.ln2/N2['liquid_density_kg_m3']}
  if k in [m+'.set' for m,_,_,_,_ in MOTIONS]:
   n=k[:-4];v=next(iter(a.values()))
   if n.startswith('heater'):
    if self.degas_phase=='running' or self.dtemps[int(n[-1])]>323.15:raise ActionRejected('HOT_INTERLOCK')
   else:
    if self.phase=='running':raise ActionRejected('BUSY')
    if n=='guard' and self.motion['dewar']>.01 and v>.01:raise ActionRejected('DEWAR_INTERLOCK')
    if n=='dewar' and v>.01 and (self.motion['guard']>.01 or any(self.motion['sample'+str(i)]>.001 for i in range(3))):raise ActionRejected('TUBE_INTERLOCK')
    if n=='dewar' and v<.28 and self.cold_k()==77.:
     warm=[(self.plant['warm_volume_m3'][i]/self.temp+self.plant['cold_volume_m3'][i]/self.plant['ambient_k'])/R for i in range(3)]
     if any(st['n']/a>146655.6079 for st,a in zip(self.stations,warm)):raise ActionRejected('WARMUP_PRESSURE_INTERLOCK')
    if n.startswith('sample'):
     i=int(n[-1])
     if self.motion['guard']<1.4 or self.motion['dewar']>.01 or abs(self.pressure(i)-101325)>30.:raise ActionRejected('PRESSURE_ACCESS_INTERLOCK')
   self.motion[n]=v;return a
  if k in ['sample.load','sample.unload','method.configure','pressure.calibrate','volume.calibrate','sample.backfill']:
   i=a['station'];s=self.stations[i]
   if self.phase=='running' or s['stage']=='backfill':raise ActionRejected('BUSY')
   if k=='sample.load':
    if s['sample'] or self.motion['sample'+str(i)]<.17 or not a['predegassed'] or not a['accept_branch_and_equilibrium_transfer']:raise ActionRejected('LOAD_PRECONDITION')
    if a['reference_id'] not in LIB['substances']:raise ActionRejected('UNKNOWN_REFERENCE')
    s['sample']=copy.deepcopy(a);self.mass_loaded+=a['true_dry_mass_kg'];return {'loaded':True}
   if k=='sample.unload':
    if not s['sample'] or self.motion['sample'+str(i)]<.17 or abs(self.pressure(i)-101325)>30.:raise ActionRejected('LOAD_PRECONDITION')
    m=s['sample']['true_dry_mass_kg'];s['sample']=None;s['ads']=0.;self.mass_returned+=m;return {'returned_mass_kg':m}
   if k=='method.configure':
    xs=a['relative_pressures']
    if not 4<=len(xs)<=500 or any(b<=x for x,b in zip(xs,xs[1:])):raise ActionRejected('INVALID_PRESSURE_SEQUENCE')
    s['method']=copy.deepcopy(a);return {'configured':True}
   if k=='pressure.calibrate':
    x,y=a['reference_pa'];u,v=a['measured_pa']
    if y-x<10000:raise ActionRejected('INVALID_CALIBRATION')
    gain=(v-u)/(y-x);offset=u-gain*x
    if not .8<=gain<=1.2:raise ActionRejected('INVALID_CALIBRATION')
    s['pcal']=(gain,offset);return {'calibrated':True}
   if k=='volume.calibrate':
    T=a['warm_temperature_k'];b1=R*a['warm_dosed_mol']/a['warm_pressure_pa'];b2=R*a['cold_dosed_mol']/a['cold_pressure_pa'];cold=(b2-b1)/(1/77.-1/T);warm=(b1-cold/T)*T
    if not 1e-7<=warm<=1e-3 or not 1e-7<=cold<=1e-3:raise ActionRejected('INVALID_CALIBRATION')
    s['vcal']=(warm,cold);return dict(warm_volume_m3=warm,cold_volume_m3=cold)
   if k=='sample.backfill':
    if not self.powered or self.motion['dewar']>.01 or self.motion['guard']>.01 or any(self.motion['sample'+str(j)]>.001 for j in range(3)):raise ActionRejected('BACKFILL_INTERLOCK')
    s['stage']='backfill';return {'started':True}
  if k=='acquisition.start':
   if self.phase=='running':raise ActionRejected('BUSY')
   ids=a['stations']
   if not ids or len(ids)!=len(set(ids)) or not self.powered or self.motion['guard']>.01 or self.motion['dewar']<.28 or self.ln2<=0 or abs(self.temp-self.target)>.05:raise ActionRejected('NOT_READY')
   for i in ids:
    s=self.stations[i]
    if not s['sample'] or not s['method'] or not s['pcal'] or not s['vcal'] or self.motion['sample'+str(i)]>.001:raise ActionRejected('NOT_READY')
    lo,hi=LIB['substances'][s['sample']['reference_id']]['runtime_equilibrium']['measured_pressure_support_pa'];points=s['method']['relative_pressures']
    if points[0]*self.pzero()<lo or points[-1]*self.pzero()>hi:raise ActionRejected('TEMPLATE_RANGE')
   self.active=ids.copy();self.phase='running';self.stage='evacuate';self.elapsed=0.;self.failure='';self.trace=[];self.serial+=1;self.operation_id=self.instance_id+':'+str(self.serial)
   for i in ids:
    s=self.stations[i];s.update(phase='running',stage='evacuate',time=0.,stable=0.,index=0,points=[],baseline=s['n'],admitted=0.,removed=0.,mass_run=s['method']['entered_mass_kg'],bet=bet_fit([]))
   return dict(operation_id=self.operation_id,phase=self.phase)
  if k=='degas.load':
   i=a['slot'];bank=i//3
   if self.degas_phase=='running' or self.degas[i] or self.motion['heater'+str(bank)]<.15 or self.dtemps[bank]>323.15:raise ActionRejected('LOAD_PRECONDITION')
   self.degas[i]=copy.deepcopy(a);self.degas[i]['initial_water_kg']=a['water_kg'];self.mass_loaded+=a['dry_mass_kg'];self.water_loaded+=a['water_kg'];return {'loaded':True}
  if k=='degas.unload':
   i=a['slot'];bank=i//3;s=self.degas[i]
   if self.degas_phase=='running' or not s or self.motion['heater'+str(bank)]<.15 or self.dtemps[bank]>323.15:raise ActionRejected('LOAD_PRECONDITION')
   self.mass_returned+=s['dry_mass_kg'];self.water_returned+=s['water_kg'];self.degas[i]=None;return dict(dry_mass_kg=s['dry_mass_kg'],water_kg=s['water_kg'])
  if k=='degas.start':
   if not self.powered or self.degas_phase=='running' or not any(self.degas) or self.ln2<=0 or self.trap>=self.plant['trap_capacity_kg'] or any(self.motion['heater'+str(b)]>.001 for b in range(2)):raise ActionRejected('NOT_READY')
   if not a['steps'] or len(a['steps'])>20:raise ActionRejected('INVALID_ARGUMENT')
   self.dprogram=copy.deepcopy(a);self.dindex=0;self.dhold=0.;self.dtarget=min(self.dtemps);self.degas_phase='running';return {'phase':self.degas_phase}
  raise ActionRejected('UNKNOWN_CAPABILITY')
 def _end(self,phase,reason=''):
  self.phase=phase;self.failure=reason;self.stage='pump_after_'+phase
  for i in self.active:
   s=self.stations[i]
   if s['phase']=='running':s['phase']=phase
   s['stage']='pump'
  if self.operation_id:self.records[self.operation_id]=self._result()
 def _fill(self,i,target):
  s=self.stations[i];p=self.pressure(i);sp=self.source*R*self.plant['ambient_k']/self.plant['source_volume_m3'];alpha=self.alpha(i);delta=min(target-p,sp-p)
  if delta<=0:return
  dn=min(self.source,max(0.,self.plant['source_conductance']*(sp-p)*DT),max(0.,delta*alpha),max(0.,(sp-p)/(1/alpha+R*self.plant['ambient_k']/self.plant['source_volume_m3'])));self.source-=dn;s['n']+=dn;s['admitted']+=dn
 def _pump(self,i):
  s=self.stations[i];dn=min(max(0.,s['n']-s['ads']),self.plant['pump_conductance']*self.pressure(i)*DT);s['n']-=dn;s['removed']+=dn;self.pumped+=dn
 def _relax(self,i,pump=0.):
  s=self.stations[i];old=s['ads'];a=-math.expm1(-self.plant['adsorption_rate_s']*DT);alpha0=self.alpha(i);alpha=alpha0+pump*DT
  if not s['sample'] or self.cold_k()!=77.:
   s['ads']*=1-a;p=max(0.,(s['n']-s['ads'])/alpha)
  else:
   d=LIB['substances'][s['sample']['reference_id']]['runtime_equilibrium'];x=d['pressure_pa'];y=d['loading_mol_kg'];m=s['sample']['true_dry_mass_kg'];b=max(0.,s['n']-(1-a)*old);lo=0;hi=len(x)-1
   while hi-lo>1:
    mid=(hi+lo)//2
    if alpha*x[mid]+a*m*y[mid]>b:hi=mid
    else:lo=mid
   if b>=alpha*x[-1]+a*m*y[-1]:p=(b-a*m*y[-1])/alpha
   else:
    slope=(y[hi]-y[lo])/(x[hi]-x[lo]);p=(b-a*m*(y[lo]-slope*x[lo]))/(alpha+a*m*slope)
   s['ads']=max(0.,min(s['n'],s['n']-alpha*max(p,0.)))
  dn=max(0.,pump*DT*p);s['n']-=dn;s['removed']+=dn;self.pumped+=dn
  return max(0.,s['ads']-old)*self.plant['adsorption_heat_j_mol']
 def _station_tick(self,i):
  s=self.stations[i];previous=self.pressure(i);stage=s['stage'];s['time']+=DT
  if stage=='backfill' and self.powered:self._fill(i,101325.)
  elif stage=='dose' and self.phase=='running':self._fill(i,s['method']['relative_pressures'][s['index']]*self.pzero())
  cold=self._relax(i,self.plant['pump_conductance'] if stage in ['evacuate','finish','pump'] else 0.);now=self.indicated(i)
  if stage=='backfill' and abs(self.pressure(i)-101325.)<10.:s['stage']='idle'
  if stage=='pump' and s['n']<1e-10:s['stage']='idle'
  if self.phase=='running' and i in self.active and s['phase']=='running':
   if s['time']>s['method']['point_timeout_s']:self._end('failed','POINT_TIMEOUT');return cold
   if stage in ['evacuate','finish'] and s['n']<1e-10:
    s['time']=0.;s['stable']=0.
    if stage=='evacuate':s['stage']='dose'
    else:s['stage']='idle';s['phase']='succeeded'
   elif stage=='dose':
    target=s['method']['relative_pressures'][s['index']]*self.pzero();ok=abs(now-target)<=s['method']['pressure_tolerance_pa'] and abs(self.pressure(i)-previous)/DT<=s['method']['max_drift_pa_s'];s['stable']=s['stable']+DT if ok else 0.
    if s['stable']+1e-10>=s['method']['equilibration_s']:
     loading=(s['baseline']+s['admitted']-s['removed']-now*self.alpha(i,True))/s['mass_run'];s['points'].append(dict(time_s=self.time_s,pressure_pa=now,relative_pressure=now/self.pzero(),loading_mol_kg=loading,p0_pa=self.pzero(),temperature_k=77.));s['bet']=bet_fit(s['points'],s['mass_run']);s['index']+=1;s['stable']=0.;s['time']=0.
     if s['index']==len(s['method']['relative_pressures']):s['stage']='finish'
  return cold
 def _tick_degas(self):
  p=self.plant;ambient=p['ambient_k'];active=self.degas_phase=='running'
  if active:
   goal=self.dprogram['steps'][self.dindex]['temperature_k'];change=self.dprogram['ramp_k_s']*DT;self.dtarget+=max(-change,min(change,goal-self.dtarget))
  else:self.dtarget=ambient
  for b in range(2):
   T=self.dtemps[b];loss=p['degas_loss_w_k']*(T-ambient);heat=min(p['degas_power_w'],max(0.,20*(self.dtarget-T)+p['degas_loss_w_k']*(self.dtarget-ambient))) if active else 0.;self.dtemps[b]+=(heat-loss)*DT/p['degas_heat_capacity_j_k'];self.dheat[b]+=heat*DT;self.dloss[b]+=loss*DT
  if active:
   if self.ln2<=0:self.degas_phase='failed';return
   for i,s in enumerate(self.degas):
    if s:
     T=self.dtemps[i//3];rate=s['water_rate_at_373_s']*math.exp(-s['activation_j_mol']/R*(1/T-1/373.15));removed=min(s['water_kg']*(-math.expm1(-rate*DT)),max(0.,p['trap_capacity_kg']-self.trap));s['water_kg']-=removed;self.trap+=removed
   if self.trap>=p['trap_capacity_kg']-1e-15:self.degas_phase='failed';return
   stable=all(abs(t-goal)<.3 for b,t in enumerate(self.dtemps) if any(self.degas[3*b:3*b+3]));self.dhold=self.dhold+DT if stable else 0.
   if self.dhold>=self.dprogram['steps'][self.dindex]['hold_s']:
    self.dindex+=1;self.dhold=0.
    if self.dindex==len(self.dprogram['steps']):self.degas_phase='succeeded' if all(not s or s['water_kg']<=s['initial_water_kg']*self.dprogram['remaining_fraction_limit']+1e-15 for s in self.degas) else 'failed'
 def _tick(self):
  if not self.plant:return
  p=self.plant;loss=p['heat_loss_w_k']*(self.temp-p['ambient_k']);heat=max(0.,min(p['heater_w'],40*(self.target-self.temp)+p['heat_loss_w_k']*(self.target-p['ambient_k']))) if self.powered else 0.;self.temp+=(heat-loss)*DT/p['heat_capacity_j_k'];self.heat+=heat*DT;self.loss+=loss*DT
  if self.phase=='running' and (not self.powered or self.ln2<=0 or self.cold_k()!=77.):self._end('failed','COOLANT_OR_POWER')
  cold=p['ln2_heat_leak_w']*DT if self.ln2>0 else 0.
  for i in range(3):cold+=self._station_tick(i)
  self._tick_degas();self.cold_energy+=cold;boil=min(self.ln2,cold/N2['latent_heat_j_kg']);self.ln2-=boil;self.boiled+=boil
  if self.phase=='running':
   self.elapsed+=DT;self.stage=';'.join(str(i)+':'+self.stations[i]['stage'] for i in self.active)
   if round(self.elapsed/DT)%10==0:self.trace.append(dict(time_s=self.time_s,pressures_pa=[self.indicated(i) for i in range(3)],manifold_k=self.temp,ln2_m3=self.ln2/N2['liquid_density_kg_m3']))
   if all(self.stations[i]['phase']=='succeeded' for i in self.active):self.phase='succeeded';self.stage='complete';self.records[self.operation_id]=self._result()
 def step(self,dt_s):
  if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<0 or dt_s>86400:raise ActionRejected('INVALID_ARGUMENT')
  self.pending+=dt_s
  while self.pending+1e-12>=DT:self.pending=max(0.,self.pending-DT);self.time_s+=DT;self._tick();self.revision+=1

class AntonpaarAutosorb6100Session:
 def __init__(self,host,principal_id):self._host=host;self._principal=principal_id
 @property
 def _role(self):return self._host._principals[self._principal]
 def _target(self,i):
  if i!=self._host.instance_id:raise ActionRejected('UNKNOWN_INSTANCE')
 def describe(self,*,instance_id=None,asset_type_id=None):
  if (instance_id is None)==(asset_type_id is None):raise ActionRejected('INVALID_ARGUMENT')
  if instance_id is not None:self._target(instance_id)
  if asset_type_id is not None and asset_type_id!=ASSET_TYPE_ID:raise ActionRejected('UNKNOWN_INSTANCE')
  m=export_antonpaar_autosorb6100_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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
  if h._owners.get(operation_id)!=self._principal:raise ActionRejected('ACCESS_DENIED')
  record=h.records.get(operation_id)
  if operation_id==h.operation_id:record=h._result()
  if record is None:raise ActionRejected('UNKNOWN_OPERATION')
  return dict(operation_id=operation_id,status=record['phase'],result=copy.deepcopy(record))
 def cancel(self,operation_id,request_id):
  self.poll(operation_id)
  if operation_id!=self._host.operation_id:raise ActionRejected('NOT_RUNNING')
  return self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)





def export_antonpaar_autosorb6100_contract_metadata():
 caps={};slots={}
 for k,(s,u,o,v,c,w) in SLOTS.items():slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions='SI absolute pressures; N277K P0 from NIST97152.26Pa; STP loading273.15K101325Pa; source branch unlabelled; external ambient101325Pa assumed',origin=o,visibility=v,access='read_write' if w else 'read_only',read_binding=dict(backend='host',artifact_id='runtime',accessor=f'AntonpaarAutosorb6100Session.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,sampling=dict(mode='instant',time_domain='simulation',window_description='fixed0.1s integration;1s traces; queries pure'),source_ids=['simulation_assumption'])
 errors=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','UNKNOWN_OPERATION','UNSUPPORTED','ACCESS_DENIED','REQUEST_CONFLICT','INITIALIZATION_ONLY','BUSY','UNKNOWN_REFERENCE','NOT_READY','NOT_RUNNING','NOT_CONFIGURED','CAPACITY','HOT_INTERLOCK','DEWAR_INTERLOCK','TUBE_INTERLOCK','PRESSURE_ACCESS_INTERLOCK','LOAD_PRECONDITION','INVALID_PRESSURE_SEQUENCE','INVALID_CALIBRATION','BACKFILL_INTERLOCK','TEMPLATE_RANGE','POINT_TIMEOUT','COOLANT_OR_POWER','WARMUP_PRESSURE_INTERLOCK']
 for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
  un=k in UNSUPPORTED;asyn=k=='acquisition.start';caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='unsupported' if un else 'implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),implementation=dict(process='absent' if un else 'implemented',scene='absent',robot='absent',hardware='absent',host='absent' if un else 'implemented'),binding=None if un else dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.antonpaar_autosorb6100.AntonpaarAutosorb6100Session.invoke',invocation=f'session.invoke(instance_id="autosorb6100_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),preconditions=[] if kind in ['query','analysis'] or un else [dict(description_zh='有限样品/气源和已接受的曲线迁移；受检压力/温度/机械访问；非同步写入不推进时间',state_ids=['power.enabled','plant.configured','acquisition.phase','dewar.lift_m','guard.angle_rad'],enforced_by='runtime',check='atomic validated invoke; host-authenticated Session; completion only on step')],state_effects=[dict(state_id=x,timing='immediate',description_zh='受检动作更新状态') for x in effects],observes=[],model_effects=dict(mode='none',description_zh='待独立场景验证',binding_ids=[]),execution=dict(mode='asynchronous' if asyn else 'synchronous',time_domain='simulation',completion_criterion='全部指定分析位完成压力点且抽空' if asyn else '命令接受；升温/脱气/回填通过step和状态槽判断完成',completion_state_ids=['acquisition.phase'] if asyn else [],timeout_s=None,cancellation='supported' if asyn else 'unsupported',retry='same_request_id',resources=['host_clock','analysis_stations','source_gas','sample_solids','ln2','degas_water','cold_trap','request_cache'],partial_failure_zh='已消耗N2/液氮和已脱附水不会回滚；取消分析保留数据和样品，后续step抽空；脱气取消后自然冷却'),errors=[dict(code=x,meaning_zh=x,side_effects='none') for x in errors],example_arguments=copy.deepcopy(ex),verification=[],limitations=['原始分支/p0未标，近似明确；未验证厂家精度/噪声和全孔径；协议不等于真机通信'],source_ids=['manufacturer_specs','simulation_assumption'])
  if asyn:caps[k]['state_effects'] += [dict(state_id=x,timing='on_step',description_zh='有限吸附/气路求解和热过程推进') for x in ['station0.pressure_pa','station1.pressure_pa','station2.pressure_pa','coolant.volume_m3','acquisition.elapsed_s','acquisition.phase','acquisition.stage']]
 mech=dict(component_ids=['controller','manifold','sample_0','sample_1','sample_2','dewar','cold_trap','heater_0','heater_1'],state_ids=list(slots),meaning_zh='有限N2气体容量法、三独立分析位、实测曲线的显式数值迁移与六位双温区脱气',implementation='lumped',equations=['Ntotal=alpha*P+n_ads; alpha=(Vwarm/Tmanifold+Vcold/Tbath)/R; nonideal EOS omitted','nads_new=(1-a)nads_old+a*mtrue*q_equilibrium(Pnew),a=1-exp(-k*dt); exact piecewise-linear implicit solution at conserved Ntotal','q_equilibrium monotone projection of actual NISTN277K data, branch unspecified; origin bridge explicit; acquisition rejects targets outside measured pressure support','Finite source P=Nsource*R*Tamb/Vsource; N2valve dn limited by pressure gradient, finite source and pressure equalization; pump removes only available gas','q_report=(Nbaseline+Ndosed-Npumped-Pindicated*alpha_cal)/entered_mass; no direct reading of true material loading in returned measurement','Two-temperature inert-dose free-space calibration: Vcold=(R*n_cold/Pcold-R*n_warm/Pwarm)/(1/77-1/Twarm); Vwarm=R*n_warm*Twarm/Pwarm-Vcold','Pindicated=quantize((gain*P+offset-cal_offset)/cal_gain,2.66644e-3Pa); saturation reference=P0_77K*p0_gain','BET y=x/(q*(1-x)); qm=1/(slope+intercept),C=1+slope/intercept,area=qm*NA*0.162nm2;positiveC,monolayerinrange,monotonicq(1-x),20pctpressureagreement,R2>=.995','Conditional pore volume=q(P/P0>=.95)*M_N2/rho_liquid77K; no extrapolated near-saturation points','BJH explicit desorption only: rk=-2gamma*Vliq/(RTlnx),t=.354nm*(5/-lnx)^(1/3),rp=rk+t; prior pore film correction and negative bins retained;2.5–500nm domain','Manifold/heater C*dT=Qinput-U*(T-Tambient)*dt; each reported energy ledger covers its defined thermal block only','N2 initial+source=gas+adsorbate+pumped; dry-solid loaded=present+returned; LN2added=remaining+boiloff','Cold load=dNads_positive*explicit_adsorption_heat+heat_leak*dt; LN2boiloff=min(remaining,Qcold/Lvap_NIST); source/pump/gas/sample whole enthalpy omitted','Degas water removal=water*(1-exp(-k373*exp(-Ea/R*(1/T-1/373.15))*dt)); trapped finite water,2heater zones,6samples; kinetics and water latent thermal effect unvalidated'],parameters={},source_ids=['manufacturer_specs','nist_isodb','nist_nitrogen','bet_bjh','simulation_assumption'],limitations=['100distinctNISTmaterials measured literature curves; exact source instrument and original branch/p0 generally unknown; transfer is an engineering assumption.','Two-temperature He calibration consumes host-supplied experimental records; automatic He acquisition not implemented; only finiteN2 runtime gas.','Three independent method sequences at common standard77K; other gases/multitemperature/CryoSync/chemisorption/TCD/MS unsupported.','Current unknown-branch source curves do not generate a claimed BJH/DFT distribution; separate BJH analysis accepts explicitly identified desorption data.','0.35–2.5nm micropore DFT kernel and full500nm performance unavailable; empirical errors/precision/repeatability/probabilistic faults not calibrated.','Ideal77K bath and manifold+heater block energy only; gas/sample thermal transients, sorbate/volatile full enthalpy, cryogenic trapwater heat, pump and supply-bottle energy not solved.','Local geometries/travel/part masses/inertias unknown; robot/sealing/contact/hardware and filesystem truth isolation not_run.'])
 spec=importlib.util.spec_from_file_location('_autosorb_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann);ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH);ann.annotate_export(SLUG,caps,slots,{'volumetric_physisorption':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE),'sample.load':(SAMPLE,SAMPLE_EXAMPLE),'degas.load':(DLOAD,DLOAD_EXAMPLE),'degas.start':(DPROGRAM,DPROGRAM_EXAMPLE)})
 for k,v in slots.items():v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'。'
 return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'volumetric_physisorption':mech})

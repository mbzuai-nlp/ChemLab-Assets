"""CHEM-060 Ignite NIN0001; finite fluids, honest unknown chip/particle physics."""
from __future__ import annotations
import copy, importlib.util, json, math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SLUG='cytiva_nanoassemblrignite'; ASSET_TYPE_ID='instrument.'+SLUG; CATALOG_ID='CHEM-060'; PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/cytiva_nanoassemblrignite_lnp_library/templates.json'
BOOL={'type':'boolean'}; TEXT={'type':'string'}

class ActionRejected(ValueError):
    """Rejected operations leave process state unchanged."""

def number(unit,low=None,high=None):
    s={'type':'number','x-unit':unit}
    if low is not None:s['minimum']=low
    if high is not None:s['maximum']=high
    return s

def obj(p):return dict(type='object',properties=p,required=list(p),additionalProperties=False)

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







CHANNELS=['left','center','right']; PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
CH=dict(type='integer',minimum=0,maximum=2,**{'x-unit':'1'})
def arr(s,n):return dict(type='array',items=s,minItems=n,maxItems=n)
PLANT=obj({'ambient_k':number('K',288.15,308.15),'mixer_volume_m3':number('m^3',1e-10,1e-6),'dilution_volume_m3':number('m^3',1e-10,1e-6),'mixing_time_s':number('s',.001,100),'thermal_capacity_j_k':number('J/K',1,10000),'heater_w_per_channel':number('W',.1,150),'loss_w_k':number('W/K',.01,50),'cross_w_k':number('W/K',0,50),'controller_w_k':number('W/K',.1,100),'paddle_speed_m_s':number('m/s',.0001,.1),'accept_uncalibrated_model':BOOL})
PLANT_EXAMPLE=dict(ambient_k=298.15,mixer_volume_m3=20e-9,dilution_volume_m3=10e-9,mixing_time_s=.1,thermal_capacity_j_k=50.,heater_w_per_channel=30.,loss_w_k=.3,cross_w_k=.05,controller_w_k=8.,paddle_speed_m_s=.01,accept_uncalibrated_model=True)
SYRINGE=obj({'channel':CH,'source_tag':TEXT,'volume_m3':number('m^3',1e-12,30e-6),'capacity_m3':number('m^3',1e-6,30e-6),'inner_diameter_m':number('m',.002,.03),'density_kg_m3':number('kg/m^3',100,3000),'solute_kg_m3':number('kg/m^3',0,1000),'accept_host_compatibility':BOOL})
SYRINGE_EXAMPLES=[dict(channel=i,source_tag=['diluent','aqueous_reagent','ethanol_lipids'][i],volume_m3=[10e-6,3e-6,1e-6][i],capacity_m3=[10e-6,3e-6,1e-6][i],inner_diameter_m=[.0145,.009,.0047][i],density_kg_m3=[1000.,1000.,789.][i],solute_kg_m3=[0.,.1,10.][i],accept_host_compatibility=True) for i in range(3)]
METHOD=obj({'flow_m3_s':number('m^3/s',.1e-6/60,20e-6/60),'flow_ratio_c_r':number('1',1,10),'volume_m3':number('m^3',1e-6,20e-6),'dilution_ratio':number('1',0,10),'start_waste_equiv_m3':number('m^3',0,20e-6),'end_waste_equiv_m3':number('m^3',0,20e-6)})
METHOD_EXAMPLE=dict(flow_m3_s=4e-6/60,flow_ratio_c_r=3.,volume_m3=2e-6,dilution_ratio=1.,start_waste_equiv_m3=.2e-6,end_waste_equiv_m3=.05e-6)
MEASURE=obj({'phase':PHASE,'stage':TEXT,'flow_m3_s':number('m^3/s',0),'flow_ratio_c_r':number('1',0),'dispensed_base_m3':number('m^3',0),'sample_m3':number('m^3',0),'waste_m3':number('m^3',0),'temperature_k':arr(number('K',1,400),2),'mixing_index':number('1',0,1),'evidence':TEXT,'particle_size_mapping_available':BOOL})
LOCS=['initial','syringe','mixer','dilution','sample','waste','discarded','collected_sample','collected_waste']
PF={loc+'_'+q:arr(number(unit,0),3) for loc in LOCS for q,unit in [('m3','m^3'),('kg','kg'),('solute_kg','kg')]}
PF.update(heater_input_j=number('J',0),thermal_loss_j=number('J'),sensible_j=number('J'),evidence=TEXT,template_source=TEXT,particle_size_mapping_available=BOOL)
PRODUCT=obj(PF)
CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=()):CAPS[k]=(i,o,c,z,kind,vis,ex or {},list(effects))
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body'],'显式宿主有限芯片/热块/驱动数值参数','configuration','evaluator',PLANT_EXAMPLE)
cap('cartridge.install',obj({'dilution':BOOL,'cartridge_id':TEXT,'accept_host_attachment':BOOL}),obj({'installed':BOOL}),['cartridge'],'装一次性NxGen或NxGenDilution；不支持Plus500芯片','configuration','evaluator',{'dilution':True,'cartridge_id':'NxGenD_01','accept_host_attachment':True})
cap('cartridge.remove',obj({}),obj({'attachment_event':TEXT}),['cartridge'],'移走芯片并将残留记入废弃，不能抹掉质量','device_command','evaluator')
cap('syringe.load',SYRINGE,obj({'loaded':BOOL}),['syringe_'+c for c in CHANNELS],'相容液体/针筒由宿主负责；同一会话同路来源性质固定','configuration','evaluator',SYRINGE_EXAMPLES[1])
cap('syringe.unload',obj({'channel':CH}),obj({'attachment_event':TEXT}),['syringe_'+c for c in CHANNELS],'装卸剩余试剂转discarded，保存全过程物料账本','device_command','evaluator',{'channel':1})
cap('tubes.install',obj({'sample_capacity_m3':number('m^3',15e-6,50e-6),'accept_host_attachment':BOOL}),obj({'installed':BOOL}),['sample_tube','waste_tube'],'宿主安装15mL废液管及15或50mL样品管','configuration','evaluator',{'sample_capacity_m3':50e-6,'accept_host_attachment':True})
cap('tubes.collect',obj({'recipient_id':TEXT}),obj({'recipient_id':TEXT,'attachment_event':TEXT}),['sample_tube','waste_tube'],'分离回收样品和废液，不退回针筒','device_command','evaluator',{'recipient_id':'lnp_batch'})
cap('method.configure',METHOD,obj({'configured':BOOL}),['touchscreen'],'混合前体积与Q(C+R)、C:R、下游L比例，废液为稀释前等效量','configuration','agent',METHOD_EXAMPLE)
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet'],'掉电立即中断驱动和加热，保留部分物料',ex={'enabled':True})
cap('lid.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['lid'],'允许开盖中断；不是拒绝开盖的虚构锁死',ex={'opened':False})
cap('block.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['rotating_block'],'开盖后旋转加载块；运动轴与角为照片代理',ex={'opened':False})
cap('heating.configure',obj({'installed':BOOL,'accept_host_attachment':BOOL}),obj({'installed':BOOL}),['heater_center','heater_right'],'可选Ignite加热，不混用Plus65℃','configuration','evaluator',{'installed':True,'accept_host_attachment':True})
cap('heating.set',obj({'enabled':BOOL,'targets_k':arr(number('K',288.15,348.15),2)}),obj({'enabled':BOOL,'effective_targets_k':arr(number('K',288.15,348.15),2)}),['heater_center','heater_right','touchscreen'],'最高75℃；冷通道可达目标不低于热目标-30K',ex={'enabled':True,'targets_k':[298.15,348.15]})
for k,z in [('acquisition.start','异步寻位→有限配制→推杆回零；完成不等于合格LNP'),('cartridge.prime','可选约6mm物理活塞priming，全部到废液'),('cycle.reset','开盖中断后闭盖复位，推杆回零不退回试剂')]:
    cap(k,obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['paddle_'+c for c in CHANNELS]+['cartridge','sample_arm'],z)
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['touchscreen'],'立即停泵保留部分试剂和芯片残留')
cap('formulation.measure',obj({}),MEASURE,['touchscreen'],'流量/体积/热块理想示值，不是内置DLS','query')
cap('product.read',obj({}),PRODUCT,['cartridge','sample_tube','waste_tube'],'三源液体体积/质量/溶质及热块显热账本','query','evaluator')
REF_ITEM=obj({'value':number('m',0),'uncertainty_known':BOOL,'uncertainty_m':number('m',0),'method':TEXT})
cap('reference.lookup',obj({'substance_id':TEXT}),obj({'substance_id':TEXT,'measurements':dict(type='array',items=REF_ITEM),'evidence':TEXT,'source':TEXT,'template_source':TEXT,'conditions':TEXT}),['touchscreen'],'离线真实文献粒径，TDA/DLS分开，无方法或缺失时不臆填','query','agent',{'substance_id':'lnp_atlas_913'})
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='body',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
for k in ['plant','method']:slot(k+'.configured',BOOL,'1',k+'已配置','setpoint')
slot('power.enabled',BOOL,'1','主电源','setpoint',c='power_inlet',w=('power.set','enabled'))
for k,c in [('lid','lid'),('block','rotating_block')]:
    slot(k+'.opened',BOOL,'1','顶盖/加载块开合','scene',c=c,w=(k+'.set','opened'))
    slot(k+'.angle_rad',number('rad',0,1.4),'rad','向后上翻角','scene',c=c)
for k,z in [('cartridge.present','芯片已附着'),('cartridge.used','一次性配制已使用'),('cartridge.dilution','下游稀释入口存在'),('tubes.present','宿主样品/废液管已安装'),('heating.installed','可选双热块已安装'),('heating.enabled','加热使能')]:slot(k,BOOL,'1',z,'scene' if k.endswith(('present','installed')) else 'setpoint')
for i,c in enumerate(CHANNELS):
    slot('paddle.'+c+'_m',number('m',0,.16),'m','推杆位置，回零不改变活塞消耗','scene',c='paddle_'+c)
    slot('plunger.'+c+'_m',number('m',0,.1),'m','有限耗液活塞位移','scene',c='plunger_'+c)
for i,c in enumerate(CHANNELS[1:]):
    slot('thermal.'+c+'_k',number('K',1,400),'K','加热块温度',c='heater_'+c)
    slot('thermal.target_'+c+'_k',number('K',288.15,348.15),'K','用户请求温度','setpoint',c='heater_'+c)
slot('arm.position_m',number('m',0,.04),'m','换废液/样品的理想横移代理','scene',c='sample_arm')
slot('cycle.phase',PHASE,'1','异步状态')
slot('cycle.stage',TEXT,'1','寻位/配制/priming/回零/中断子状态')
slot('cycle.elapsed_s',number('s',0),'s','本次操作模拟时间')
slot('flow.total_m3_s',number('m^3/s',0),'m^3/s','C+R实际运行流量')
slot('flow.ratio_c_r',number('1',0),'1','配制设定C:R','setpoint')
slot('volume.dispensed_m3',number('m^3',0),'m^3','本次稀释前累计配制量')
slot('volume.sample_m3',number('m^3',0),'m^3','样品管累计体积')
slot('volume.waste_m3',number('m^3',0),'m^3','废液管累计体积')
slot('mixing.index',number('1',0,1),'1','未知芯片参数给出的混合指标，非实测')
slot('safety.warning',TEXT,'1','手册装载加热超过3min提示，非厂家安全联锁')
slot('truth.ledger',PRODUCT,'1','私有三源液体及热账本','process_truth','evaluator','cartridge')
for k in CAPS:
    if CAPS[k][4]!='query':CAPS[k][7].extend(SLOTS)

class CytivaNanoassemblrigniteRuntime:
    def __init__(self,instance_id='ignite_01'):
        self.instance_id=instance_id;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']}
        self.time_s=0.;self.revision=0;self.plant=None;self.method=None;self.cartridge=None;self.used=False;self.primed=False;self.used_cartridge_ids=[]
        self.syringes=[None]*3;self.sources=[None]*3;self.vols={loc:[0.,0.,0.] for loc in LOCS};self.loaded=[0.]*3;self.paddle=[0.]*3
        self.powered=False;self.lid=True;self.block=True;self.tubes=False;self.sample_capacity=50e-6;self.heater_installed=False;self.heating=False
        self.temperature=[298.15]*2;self.targets=[298.15]*2;self.effective=[298.15]*2;self.heater_input=0.;self.thermal_loss=0.
        self.phase='idle';self.stage='idle';self.elapsed_s=0.;self.dispensed=0.;self.outlet=0.;self.maxout=0.;self.arm=0.;self.mixing=0.;self.warning='';self.warm_loaded_s=0.
        self.task='';self.remaining=[0.]*3;self.targets_displacement=[0.]*3;self.operation_id='';self._operation_count=0;self.log=[]
        self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
    def register_principal(self,principal_id,visibility='agent'):
        if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id='__public_agent__'):
        if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return CytivaNanoassemblrigniteSession(self,principal_id)
    def describe(self):return self.session().describe(instance_id=self.instance_id)
    def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k!='templates'})
    def _area(self,i):return math.pi*self.syringes[i]['inner_diameter_m']**2/4 if self.syringes[i] else 1.
    def _consumed(self,i):return max(0.,self.loaded[i]-self.vols['syringe'][i])/self._area(i) if self.syringes[i] else 0.
    def _access(self):
        if self.phase=='running':raise ActionRejected('BUSY')
        if not self.lid:raise ActionRejected('PRECONDITION_FAILED')
    def _product(self):
        out={}
        for loc in LOCS:
            out[loc+'_m3']=list(self.vols[loc]);out[loc+'_kg']=[v*(s['density_kg_m3'] if s else 0.) for v,s in zip(self.vols[loc],self.sources)];out[loc+'_solute_kg']=[v*(s['solute_kg_m3'] if s else 0.) for v,s in zip(self.vols[loc],self.sources)]
        out.update(heater_input_j=self.heater_input,thermal_loss_j=self.thermal_loss,sensible_j=sum(t-self.plant['ambient_k'] for t in self.temperature)*self.plant['thermal_capacity_j_k'] if self.plant else 0.,evidence='synthetic_finite_volume_and_block_heat_model_not_LNP_size_prediction',template_source=TEMPLATE_REL,particle_size_mapping_available=False)
        return out
    def _flow(self):return self.method['flow_m3_s'] if self.phase=='running' and self.stage=='dispensing' else 0.
    def read(self,visibility='agent'):
        if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
        v={'plant.configured':self.plant is not None,'method.configured':self.method is not None,'power.enabled':self.powered,'lid.opened':self.lid,'lid.angle_rad':1.4 if self.lid else 0.,'block.opened':self.block,'block.angle_rad':1.35 if self.block else 0.,'cartridge.present':self.cartridge is not None,'cartridge.used':self.used,'cartridge.dilution':bool(self.cartridge and self.cartridge['dilution']),'tubes.present':self.tubes,'heating.installed':self.heater_installed,'heating.enabled':self.heating,'arm.position_m':self.arm,'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.elapsed_s':self.elapsed_s,'flow.total_m3_s':self._flow(),'flow.ratio_c_r':self.method['flow_ratio_c_r'] if self.method else 0.,'volume.dispensed_m3':self.dispensed,'volume.sample_m3':sum(self.vols['sample']),'volume.waste_m3':sum(self.vols['waste']),'mixing.index':self.mixing,'safety.warning':self.warning,'truth.ledger':self._product()}
        for i,c in enumerate(CHANNELS):v['paddle.'+c+'_m']=self.paddle[i];v['plunger.'+c+'_m']=self._consumed(i)
        for i,c in enumerate(CHANNELS[1:]):v['thermal.'+c+'_k']=self.temperature[i];v['thermal.target_'+c+'_k']=self.targets[i]
        return copy.deepcopy({k:x for k,x in v.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
    def invoke(self,capability_id,**a):
        if capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability_id][0],a);old=self.debug_snapshot()
        try:
            result=self._invoke(capability_id,a);validate(CAPS[capability_id][1],result)
            if CAPS[capability_id][4]!='query':self.revision+=1;self.log.append(dict(time_s=self.time_s,event=capability_id,phase=self.phase,stage=self.stage))
            return copy.deepcopy(result)
        except Exception:
            templates=self.templates;self.__dict__.clear();self.__dict__.update(old);self.templates=templates;raise
    def _stop(self):
        if self.phase=='running':self.phase='cancelled';self.stage='interrupted'
    def _move(self,src,dst):
        for i in range(3):self.vols[dst][i]+=self.vols[src][i];self.vols[src][i]=0.
    def _invoke(self,k,a):
        if k=='formulation.measure':return dict(phase=self.phase,stage=self.stage,flow_m3_s=self._flow(),flow_ratio_c_r=self.method['flow_ratio_c_r'] if self.method else 0.,dispensed_base_m3=self.dispensed,sample_m3=sum(self.vols['sample']),waste_m3=sum(self.vols['waste']),temperature_k=list(self.temperature),mixing_index=self.mixing,evidence='synthetic_ideal_readout_not_online_DLS',particle_size_mapping_available=False)
        if k=='product.read':return self._product()
        if k=='reference.lookup':
            if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
            t=self.templates[a['substance_id']]
            return dict(substance_id=t['substance_id'],measurements=[dict(value=x['value'],uncertainty_known=x['uncertainty'] is not None,uncertainty_m=x['uncertainty'] or 0.,method=x['method']) for x in t['values']['particle_size']],evidence=t['evidence'],source=t['source']['paper_doi'],template_source=TEMPLATE_REL,conditions=json.dumps(t['conditions'],ensure_ascii=False))
        if k=='power.set':
            self.powered=a['enabled']
            if not self.powered:self._stop();self.heating=False
            return dict(enabled=self.powered)
        if k=='lid.set':
            if not a['opened'] and self.block:raise ActionRejected('PRECONDITION_FAILED')
            if a['opened']:self._stop()
            self.lid=a['opened'];return dict(opened=self.lid)
        if k=='block.set':
            self._access();self.block=a['opened'];return dict(opened=self.block)
        if k=='acquisition.stop':self._stop();return dict(phase=self.phase)
        if k=='plant.configure':
            if self.phase=='running' or sum(self.vols['initial'])>0 or not a['accept_uncalibrated_model']:raise ActionRejected('PRECONDITION_FAILED')
            self.plant=copy.deepcopy(a);self.temperature=[a['ambient_k']]*2;self.targets=list(self.temperature);self.effective=list(self.temperature);self.heater_input=0.;self.thermal_loss=0.;return dict(configured=True)
        if k=='cartridge.install':
            self._access()
            if not self.block or not a['accept_host_attachment'] or self.cartridge is not None or not a['cartridge_id'] or a['cartridge_id'] in self.used_cartridge_ids:raise ActionRejected('PRECONDITION_FAILED')
            self.cartridge=copy.deepcopy(a);self.used=False;self.primed=False;return dict(installed=True)
        if k=='cartridge.remove':
            self._access()
            if not self.block or self.cartridge is None:raise ActionRejected('PRECONDITION_FAILED')
            self._move('mixer','discarded');self._move('dilution','discarded');self.cartridge=None;return dict(attachment_event='cartridge.release_and_dispose')
        if k=='syringe.load':
            self._access();i=a['channel']
            if not self.block or not self.plant or not a['accept_host_compatibility'] or self.syringes[i] is not None or not a['source_tag']:raise ActionRejected('PRECONDITION_FAILED')
            allowed=[1,3,5,10,20,30] if i==0 else [1,3,5,10]
            if not any(abs(a['capacity_m3']-x*1e-6)<1e-14 for x in allowed) or a['volume_m3']>a['capacity_m3']+1e-15:raise ActionRejected('SYRINGE_CAPACITY')
            if a['volume_m3']/(math.pi*a['inner_diameter_m']**2/4)>.1:raise ActionRejected('GEOMETRY_STROKE_LIMIT')
            if a['solute_kg_m3']>a['density_kg_m3']:raise ActionRejected('INVALID_ARGUMENT')
            if self.sources[i] and any(a[x]!=self.sources[i][x] for x in ['source_tag','density_kg_m3','solute_kg_m3']):raise ActionRejected('SOURCE_REBIND_FORBIDDEN')
            self.sources[i]=copy.deepcopy(a);self.syringes[i]=copy.deepcopy(a);self.loaded[i]=a['volume_m3'];self.vols['syringe'][i]=a['volume_m3'];self.vols['initial'][i]+=a['volume_m3'];self.paddle[i]=0.;return dict(loaded=True)
        if k=='syringe.unload':
            self._access();i=a['channel']
            if not self.block or self.syringes[i] is None:raise ActionRejected('PRECONDITION_FAILED')
            self.vols['discarded'][i]+=self.vols['syringe'][i];self.vols['syringe'][i]=0.;self.syringes[i]=None;self.loaded[i]=0.;self.paddle[i]=0.;return dict(attachment_event='syringe.release_remaining_to_discarded')
        if k=='tubes.install':
            self._access()
            if self.tubes or not a['accept_host_attachment'] or not any(abs(a['sample_capacity_m3']-x)<1e-14 for x in [15e-6,50e-6]):raise ActionRejected('PRECONDITION_FAILED')
            self.tubes=True;self.sample_capacity=a['sample_capacity_m3'];return dict(installed=True)
        if k=='tubes.collect':
            self._access()
            if not self.tubes or not a['recipient_id']:raise ActionRejected('PRECONDITION_FAILED')
            self._move('sample','collected_sample');self._move('waste','collected_waste');self.tubes=False;return dict(recipient_id=a['recipient_id'],attachment_event='tubes.release_separate_sample_and_waste')
        if k=='heating.configure':
            self._access()
            if not a['accept_host_attachment'] or self.heating or max(self.temperature)>313.15:raise ActionRejected('PRECONDITION_FAILED')
            self.heater_installed=a['installed'];return dict(installed=self.heater_installed)
        if k=='heating.set':
            if not self.plant or not self.heater_installed or (a['enabled'] and (not self.powered or self.block)):raise ActionRejected('PRECONDITION_FAILED')
            if min(a['targets_k'])<self.plant['ambient_k']:raise ActionRejected('PRECONDITION_FAILED')
            self.targets=list(a['targets_k']);high=max(self.targets);self.effective=[max(t,high-30.) for t in self.targets];self.heating=a['enabled'];return dict(enabled=self.heating,effective_targets_k=list(self.effective))
        if k=='method.configure':
            if self.phase=='running':raise ActionRejected('BUSY')
            if a['flow_m3_s']>=20e-6/60:raise ActionRejected('FLOW_STRICT_BOUNDARY')
            if a['start_waste_equiv_m3']+a['end_waste_equiv_m3']>a['volume_m3']+1e-15:raise ActionRejected('WASTE_RANGE')
            ql=a['dilution_ratio']*a['flow_m3_s']
            if ql and not .1e-6/60<=ql<=48e-6/60:raise ActionRejected('DILUTION_FLOW_RANGE')
            self.method=copy.deepcopy(a);return dict(configured=True)
        if k in ['acquisition.start','cartridge.prime','cycle.reset']:
            if self.phase=='running':raise ActionRejected('BUSY')
            if not self.powered or self.lid or self.block or not self.plant:raise ActionRejected('PRECONDITION_FAILED')
            self.task=k
            if k=='cycle.reset':self.stage='resetting'
            else:
                if not self.cartridge or self.used or not self.tubes or not self.method:raise ActionRejected('PRECONDITION_FAILED')
                d=self.method['dilution_ratio']
                if d and not self.cartridge['dilution']:raise ActionRejected('CARTRIDGE_TOPOLOGY')
                active=[1,2]+([0] if d else [])
                if any(self.syringes[i] is None for i in active):raise ActionRejected('PRECONDITION_FAILED')
                if any(p>1e-8 for p in self.paddle):raise ActionRejected('RESET_REQUIRED')
                v=self.method['volume_m3'];r=self.method['flow_ratio_c_r'];need=[v*d,v*r/(1+r),v/(1+r)]
                if k=='cartridge.prime':need=[.006*self._area(i) if i in active else 0. for i in range(3)]
                if any(x>self.vols['syringe'][i]+1e-14 for i,x in enumerate(need)):raise ActionRejected('SYRINGE_EMPTY')
                missing=self.plant['mixer_volume_m3']-sum(self.vols['mixer'])
                if self.cartridge['dilution']:missing+=self.plant['dilution_volume_m3']-sum(self.vols['dilution'])
                self.maxout=max(0.,sum(need)-missing)
                waste=self.maxout if k=='cartridge.prime' else min(self.maxout,(self.method['start_waste_equiv_m3']+self.method['end_waste_equiv_m3'])*(1+d))
                sample=self.maxout-waste
                if sum(self.vols['waste'])+waste>15e-6+1e-14 or sum(self.vols['sample'])+sample>self.sample_capacity+1e-14:raise ActionRejected('TUBE_CAPACITY')
                self.remaining=list(need);self.targets_displacement=[.06+self._consumed(i) if i in active else 0. for i in range(3)]
                self.stage='seeking';self.dispensed=0.;self.outlet=0.;self.arm=0.
                if k=='acquisition.start':self.used=True;self.used_cartridge_ids.append(self.cartridge['cartridge_id'])
            self.phase='running';self.elapsed_s=0.;self._operation_count+=1;self.operation_id=self.instance_id+':op:'+str(self._operation_count)
            return dict(phase=self.phase,operation_id=self.operation_id)
        raise ActionRejected('UNKNOWN_CAPABILITY')
    def _tank(self,name,inp,capacity):
        old=self.vols[name];v=sum(inp)
        if v<=0:return [0.]*3
        fill=min(v,max(0.,capacity-sum(old)));portion=fill/v
        now=[old[i]+inp[i]*portion for i in range(3)];rest=[x*(1-portion) for x in inp];rv=v-fill
        if rv<=1e-25:self.vols[name]=now;return [0.]*3
        decay=math.exp(-rv/capacity);new=[now[i]*decay+rest[i]/rv*capacity*(1-decay) for i in range(3)]
        out=[max(0.,now[i]+rest[i]-new[i]) for i in range(3)];self.vols[name]=new;return out
    def _feed(self,volumes):
        for i,v in enumerate(volumes):self.vols['syringe'][i]=max(0.,self.vols['syringe'][i]-v);self.remaining[i]=max(0.,self.remaining[i]-v);self.paddle[i]=.06+self._consumed(i) if volumes[i] else self.paddle[i]
        mixed=self._tank('mixer',[0.,volumes[1],volumes[2]],self.plant['mixer_volume_m3'])
        if self.cartridge['dilution']:
            mixed[0]+=volumes[0];out=self._tank('dilution',mixed,self.plant['dilution_volume_m3'])
        else:out=mixed
        v=sum(out);samplev=0.
        if self.task=='acquisition.start':
            d=self.method['dilution_ratio'];lo=self.method['start_waste_equiv_m3']*(1+d);hi=max(lo,self.maxout-self.method['end_waste_equiv_m3']*(1+d))
            samplev=max(0.,min(self.outlet+v,hi)-max(self.outlet,lo));samplev=min(v,samplev)
            if self.method['start_waste_equiv_m3']==0. and self.method['end_waste_equiv_m3']==0.:samplev=v
        f=samplev/v if v else 0.
        for i in range(3):self.vols['sample'][i]+=out[i]*f;self.vols['waste'][i]+=out[i]*(1-f)
        self.outlet+=v;self.arm=.04 if f>.5 else 0.;self.dispensed+=volumes[1]+volumes[2]
    def step(self,dt):
        validate(number('s',0,3600),dt)
        left=float(dt)
        while left>1e-12:
            h=min(left,.02)
            if self.plant:
                p=self.plant;h=min(h,p['thermal_capacity_j_k']/(2*(p['controller_w_k']+p['loss_w_k']+2*p['cross_w_k'])))
                a,b=self.temperature;cross=p['cross_w_k']*(b-a);net=[]
                for i,t in enumerate([a,b]):
                    target=self.effective[i];other=self.effective[1-i]
                    power=max(0.,min(p['heater_w_per_channel'],p['controller_w_k']*(target-t)+p['loss_w_k']*(target-p['ambient_k'])+p['cross_w_k']*(target-other))) if self.heating and self.powered else 0.
                    loss=p['loss_w_k']*(t-p['ambient_k']);net.append(power-loss+(cross if i==0 else -cross));self.heater_input+=power*h;self.thermal_loss+=loss*h
                self.temperature=[t+h*x/p['thermal_capacity_j_k'] for t,x in zip([a,b],net)]
                if self.heating and any(self.syringes):self.warm_loaded_s+=h
                else:self.warm_loaded_s=0.
                self.warning='heated_loaded_over_180s_manual_caution_not_firmware_interlock' if self.warm_loaded_s>180 else ''
            if self.phase=='running':
                self.elapsed_s+=h;speed=self.plant['paddle_speed_m_s']
                if self.stage in ['returning','resetting']:
                    self.paddle=[max(0.,x-speed*h) for x in self.paddle]
                    if max(self.paddle)<=1e-12:self.phase='succeeded';self.stage='idle';self.arm=0.
                elif self.stage=='seeking':
                    self.paddle=[min(target,x+speed*h) for x,target in zip(self.paddle,self.targets_displacement)]
                    if all(abs(a-b)<1e-12 for a,b in zip(self.paddle,self.targets_displacement)):self.stage='priming' if self.task=='cartridge.prime' else 'dispensing'
                else:
                    if self.stage=='priming':volumes=[min(v,self._area(i)*.001*h) if v else 0. for i,v in enumerate(self.remaining)]
                    else:
                        m=self.method;q=m['flow_m3_s'];r=m['flow_ratio_c_r'];flows=[q*m['dilution_ratio'],q*r/(1+r),q/(1+r)]
                        volumes=[min(v,f*h) for v,f in zip(self.remaining,flows)];self.mixing=1-math.exp(-self.plant['mixer_volume_m3']/q/self.plant['mixing_time_s'])
                    self._feed(volumes)
                    if max(self.remaining)<=1e-18:
                        self.stage='returning'
                        if self.task=='cartridge.prime':self.primed=True
            self.time_s+=h;left-=h
        if dt>0:self.revision+=1
        return self.read()

class CytivaNanoassemblrigniteSession:
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
        m=export_cytiva_nanoassemblrignite_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=h.time_s,sample_window_s=None) for k in ids})
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
        except ActionRejected as exc:return dict(protocol_version=PROTOCOL_VERSION,instance_id=instance_id,request_id=request_id,operation_id=None,status='rejected',state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},result={},observations={},error=dict(code=str(exc),message=str(exc),retryable=False,side_effects='none'))
    def write(self,*,instance_id,state_id,value,request_id):
        self._target(instance_id)
        if state_id not in SLOTS or not SLOTS[state_id][5]:raise ActionRejected('ACCESS_DENIED')
        cap,arg=SLOTS[state_id][5];return self.invoke(instance_id=instance_id,capability_id=cap,arguments={arg:value},request_id=request_id)
    def poll(self,operation_id):
        h=self._host
        if operation_id!=h.operation_id or h._owners.get(operation_id)!=self._principal:raise ActionRejected('ACCESS_DENIED')
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('formulation.measure'))
    def cancel(self,operation_id,request_id):
        self.poll(operation_id);return self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)





def export_cytiva_nanoassemblrignite_contract_metadata():
    caps={};slots={}
    for k,(s,u,o,v,c,w) in SLOTS.items():
        slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'CytivaNanoassemblrigniteSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    evolving=[k for k,v in SLOTS.items() if v[2] in ['scene','derived','process_truth']]
    for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
        asynchronous=k in ['acquisition.start','cartridge.prime','cycle.reset'];query=kind=='query'
        caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.cytiva_nanoassemblrignite.CytivaNanoassemblrigniteSession.invoke',invocation=f'session.invoke(instance_id="ignite_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),
          preconditions=[] if query else [dict(description_zh='电源、顶盖/加载块、有限针筒、芯片/收集管与宿主身份检查',state_ids=['power.enabled','lid.opened','cartridge.present','tubes.present','cycle.phase'],enforced_by='runtime',check='Runtime.invoke atomic guards; Session trusted-principal filtering')],
          state_effects=[] if query else [dict(state_id=s,timing='immediate',description_zh='设定/附着事件/有限装料变化') for s in effects]+[dict(state_id=s,timing='on_step',description_zh='宿主唯一时钟演化') for s in evolving],
          observes=[],model_effects=dict(mode='none',description_zh='模型实际求值证据尚待独立核验',binding_ids=[]),
          execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',completion_criterion='有限配制/priming完成并且推杆回零，不等于合格LNP' if asynchronous else '事务返回；物理量由宿主step演化',
            completion_state_ids=['cycle.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',retry='same_request_id',resources=['host_clock','syringes','cartridge','tubes','request_cache'],
            partial_failure_zh='取消保留部分料及芯片残留，RESET仅推杆回零，不恢复液量；仅进程内请求去重。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','PRECONDITION_FAILED','BUSY','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','SYRINGE_CAPACITY','SYRINGE_EMPTY','GEOMETRY_STROKE_LIMIT','SOURCE_REBIND_FORBIDDEN','FLOW_STRICT_BOUNDARY','DILUTION_FLOW_RANGE','WASTE_RANGE','TUBE_CAPACITY','CARTRIDGE_TOPOLOGY','RESET_REQUIRED']],
          example_arguments=copy.deepcopy(ex),verification=[],limitations=['有限流体/双热块模型，非厂家固件、安全认证或真实材料配方建议'],source_ids=['manufacturer_primary','simulation_assumption'])
    mech=dict(component_ids=['cartridge','heater_center','heater_right','sample_arm']+['paddle_'+c for c in CHANNELS],state_ids=list(slots),meaning_zh='有限三源液体、C/R主混合后L稀释、等效废液分段、推杆寻位/回零、双热块',implementation='lumped',
      equations=['QC=Q*FRR/(1+FRR); QR=Q/(1+FRR); QL=dilution_ratio*Q; batch_volume=VC+VR before dilution',
      'empty tank first fills finite capacity; remaining inflow deltaV gives Mnew=Mold*exp(-deltaV/V)+Cin*V*(1-exp(-deltaV/V)); out=Mold+in-Mnew',
      'C/R enter mixer; L enters only downstream dilution tank; source volumes, total source mass and solute mass conserved',
      'start/end waste API uses pre-dilution-equivalent volume scaled by1+dilution_ratio; exact overlap splitting at outlet volume boundaries',
      'plunger_consumed=(loaded-remaining)/A; paddle seeks0.06m+consumed, follows while pumping then returns0 without restoring fluid',
      'C*dTi/dt=Pi-Kloss*(Ti-Ta)+Kcross*(Tj-Ti); Pi>=0 bounded; sum cross heat cancels; cold achievable target=max(requested,hot-30K)',
      'mixing_index=1-exp(-Vmixer/(Qmix*host_mixing_time)); not measured RTD or particle size',
      'initial = syringe+mixer+dilution+sample+waste+discarded+collected_sample+collected_waste for every source'],
      parameters={},source_ids=['manufacturer_primary','manufacturer_manual','lnp_dataset','simulation_assumption'],
      limitations=['芯片CAD/真实RTD/配方到粒径映射未知；不伪造粒径预测或内置DLS。','文献928个可解析记录/254结构不是50种Ignite标定配方；AI提取/结构身份未逐篇核验。','未知参数由宿主接受；可达30K差是稳态能力，不宣称任意切换瞬态满足。','同路来源密度/溶质浓度在会话中固定，避免更换来源重解释旧账本。','换管位置理想瞬时切换；真实流体压力、滴落、容积非加性、溶质反应和固件全流程未解。'])
    spec=importlib.util.spec_from_file_location('_ignite_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann)
    ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
    ann.annotate_export(SLUG,caps,slots,{'microfluidics':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'syringe.load':(SYRINGE,SYRINGE_EXAMPLES[1]),'method.configure':(METHOD,METHOD_EXAMPLE)})
    mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'microfluidics':mech})


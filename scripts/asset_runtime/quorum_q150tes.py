"""CHEM-062 Q150T ES Plus: finite source/gas/thermal ledgers, not deposition calibration."""
from __future__ import annotations
import copy, importlib.util, json, math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SLUG='quorum_q150tes'; ASSET_TYPE_ID='instrument.'+SLUG; CATALOG_ID='CHEM-062'; PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/quorum_q150tes_coating_library/templates.json'
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









def arr(s,n=None):
    r=dict(type='array',items=s)
    if n is not None:r.update(minItems=n,maxItems=n)
    return r
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
KIND=dict(type='string',enum=['sputter','carbon'])
PLANT=obj({'ambient_k':number('K',280,315),'ambient_pa':number('Pa',90000,110000),'pump_m3_s':number('m^3/s',1e-6,.067),'outgas_pa_m3_s':number('Pa*m^3/s',0,1),'vent_tau_s':number('s',.1,60),
'sputter_yield_kg_c':number('kg/C',1e-12,1e-3),'sputter_voltage_v':number('V',1,1000),'carbon_resistance_ohm':number('ohm',.001,1),'carbon_heat_capacity_j_k':number('J/K',.001,10),'carbon_loss_w_k':number('W/K',0,1),'carbon_sublimation_k':number('K',2000,5000),'carbon_latent_j_kg':number('J/kg',1e6,1e8),'source_heat_fraction':number('1',.01,.9),'sample_heat_fraction':number('1',0,.1),'sample_heat_capacity_j_k':number('J/K',.01,100),'sample_loss_w_k':number('W/K',0,10),'sample_trip_k':number('K',316,1000),'source_offset_m':number('m',-.025,.025),'qcm_area_m2':number('m^2',1e-6,.0001),'cool_after_s':number('s',1,300),'cool_duration_s':number('s',.1,300),'pump_timeout_s':number('s',1,600),'accept_uncalibrated_model':BOOL})
PLANT_EXAMPLE=dict(ambient_k=298.15,ambient_pa=101325.,pump_m3_s=.004,outgas_pa_m3_s=.00002,vent_tau_s=.5,sputter_yield_kg_c=3e-7,sputter_voltage_v=400.,carbon_resistance_ohm=.06,carbon_heat_capacity_j_k=.04,carbon_loss_w_k=.004,carbon_sublimation_k=3500.,carbon_latent_j_kg=5e7,source_heat_fraction=.5,sample_heat_fraction=.02,sample_heat_capacity_j_k=1.,sample_loss_w_k=.01,sample_trip_k=373.15,source_offset_m=.012,qcm_area_m2=5e-5,cool_after_s=20.,cool_duration_s=5.,pump_timeout_s=60.,accept_uncalibrated_model=True)
SOURCE=obj({'kind':KIND,'substance_id':TEXT,'target_thickness_m':number('m',.0001,.001),'carbon_piece_length_m':number('m',.005,.04),'film_density_kg_m3':number('kg/m^3',100,25000),'accept_host_attachment':BOOL})
SOURCE_EXAMPLE=dict(kind='sputter',substance_id='ic6_052',target_thickness_m=.0005,carbon_piece_length_m=.03,film_density_kg_m3=7200.,accept_host_attachment=True)
CARBON_EXAMPLE=dict(SOURCE_EXAMPLE,kind='carbon',substance_id='ic6_031',film_density_kg_m3=2000.)
SAMPLES=obj({'sample_ids':arr(TEXT,6),'area_m2':number('m^2',1e-6,math.pi*.00635**2),'accept_host_attachment':BOOL})
SAMPLES_EXAMPLE=dict(sample_ids=['stub_'+str(i) for i in range(6)],area_m2=math.pi*.006**2,accept_host_attachment=True)
METHOD=obj({'sputter_current_a':number('A',.001,.150),'working_pressure_pa':number('Pa',.5,10),'carbon_current_a':number('A',1,60),'carbon_ramp_a_s':number('A/s',.01,20),'preclean_s':number('s',0,60),'duration_s':number('s',.02,3600),'rotation_enabled':BOOL,'rotation_rpm':number('rpm',8,20),'stage_lift_m':number('m',0,.015),'ftm_enabled':BOOL,'thickness_endpoint':BOOL,'endpoint_m':number('m',1e-10,1e-6),'tooling':number('1',.1,20)})
METHOD_EXAMPLE=dict(sputter_current_a=.05,working_pressure_pa=1.,carbon_current_a=46.,carbon_ramp_a_s=1.,preclean_s=2.,duration_s=30.,rotation_enabled=True,rotation_rpm=12.,stage_lift_m=0.,ftm_enabled=True,thickness_endpoint=False,endpoint_m=20e-9,tooling=1.)
REFERENCE=obj({'substance_id':TEXT,'name':TEXT,'formula_raw':TEXT,'density_kg_m3':number('kg/m^3',0),'z_display':number('1',0),'z_established':BOOL,'evidence':TEXT,'source':TEXT,'template_source':TEXT,'conditions':TEXT})
CONVERT=obj(dict(REFERENCE['properties'],thickness_m=number('m',0)))
MEASURE=obj({'phase':PHASE,'stage':TEXT,'pressure_pa':number('Pa',0),'current_a':number('A',0,60),'deposition_elapsed_s':number('s',0),'ftm_enabled':BOOL,'thickness_m':number('m',0),'sample_temperature_k':arr(number('K',1),6),'evidence':TEXT,'template_source':TEXT,'calibrated':BOOL})
LOT_FIELDS={k:number('kg',0) for k in ['initial_kg','remaining_kg','stock_kg','returned_kg','wall_kg','shutter_kg','crystal_kg','waste_kg']}
LOT_FIELDS.update(lot_id=TEXT,substance_id=TEXT,kind=KIND,film_density_kg_m3=number('kg/m^3',0),sample_kg=arr(number('kg',0),6),collected_kg=arr(number('kg',0),6),source_sensible_j=number('J',0))
LEDGER=obj({'lots':arr(obj(LOT_FIELDS)),'gas_initial_mol':number('mol',0),'gas_chamber_mol':number('mol',0),'gas_outgas_mol':number('mol',0),'gas_argon_mol':number('mol',0),'gas_vent_mol':number('mol',0),'gas_pumped_mol':number('mol',0),'electrical_j':number('J',0),'environment_j':number('J',0),'carbon_latent_j':number('J',0),'sample_sensible_j':arr(number('J',0),6),'collected_sensible_j':number('J',0),'evidence':TEXT})
CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=()):CAPS[k]=(i,o,c,z,kind,vis,ex or {},list(effects))
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body'],'有效泵速/气负荷、产额与热参数显式宿主假设','configuration','evaluator',PLANT_EXAMPLE)
cap('source.install',SOURCE,obj({'lot_id':TEXT,'attachment_event':TEXT}),['sputter_head','carbon_head'],'两源互换；余靶返还及碳棒余料保留，不改变旧膜身份','configuration','evaluator',SOURCE_EXAMPLE)
cap('samples.load',SAMPLES,obj({'loaded':BOOL,'attachment_event':TEXT}),['sample_stage'],'6有限面积样品位，宿主负责实际夹持','configuration','evaluator',SAMPLES_EXAMPLE)
cap('samples.collect',obj({'recipient_id':TEXT}),obj({'recipient_id':TEXT,'attachment_event':TEXT}),['sample_stage'],'开盖卸样，保留已沉积膜与热量','device_command','evaluator',{'recipient_id':'sem_holder'})
cap('service.clean',obj({'accept_host_cleaning':BOOL}),obj({'cleaned':BOOL}),['chamber_wall','qcm_sensor','shutter'],'宿主离线清洁，把壁/晶体/挡板残留移至废料','device_command','evaluator',{'accept_host_cleaning':True})
cap('method.configure',METHOD,obj({'configured':BOOL}),['control_panel'],'溅射/渐升碳棒、8–20rpm、选配FTM与tooling','configuration','agent',METHOD_EXAMPLE)
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['mains_switch'],'掉电中止；不瞬间泄压或清空沉积',ex={'enabled':True})
cap('chamber.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['lid'],'先回大气才开盖，开盖中止源功率/抽气',ex={'opened':False})
cap('vacuum.pump',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['vacuum_port'],'独立抽气需封闭腔与电源，不等于已有膜厚',ex={'enabled':True})
cap('vacuum.vent',obj({}),obj({'venting':BOOL}),['vent_port'],'中止源与泵，step按显式通气常数回大气')
cap('stage.set_height',obj({'lift_m':number('m',0,.015)}),obj({'lift_m':number('m',0,.015)}),['stage_lift'],'开盖手调高度代理，不代表厂家标尺',ex={'lift_m':.005})
cap('rotation.set',obj({'enabled':BOOL,'rpm':number('rpm',8,20)}),obj({'enabled':BOOL,'rpm':number('rpm',8,20)}),['sample_stage'],'8–20rpm，仅运行过程推进角度',ex={'enabled':True,'rpm':12.})
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['sputter_head','carbon_head','shutter'],'异步抽空→稳定/预清→沉积与冷却；超时可失败')
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['control_panel'],'立即停源与旋转，保持真空与部分样品')
cap('coating.measure',obj({}),MEASURE,['control_panel','qcm_sensor'],'理想真空/电流/FTM读出，膜厚由参考密度换算','query')
cap('product.read',obj({}),LEDGER,['sample_stage','sputter_head','carbon_head'],'私有逐源批次质量/能量/气体账本','query','evaluator')
cap('reference.lookup',obj({'substance_id':TEXT}),REFERENCE,['control_panel'],'255材料离线参考，非Q150兼容清单','query','agent',{'substance_id':'ic6_052'})
cap('qcm.convert',obj({'substance_id':TEXT,'areal_mass_kg_m2':number('kg/m^2',0,10),'tooling':number('1',.1,20)}),CONVERT,['qcm_sensor'],'离线质量面密度/参考密度×tooling，不假设真实FTM频率型号','query','agent',{'substance_id':'ic6_052','areal_mass_kg_m2':.000072,'tooling':1.})
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='body',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
for k in ['plant','method']:slot(k+'.configured',BOOL,'1',k+'已配置','setpoint')
slot('power.enabled',BOOL,'1','电源状态','setpoint',c='mains_switch',w=('power.set','enabled'))
slot('chamber.opened',BOOL,'1','开盖状态','scene',c='lid',w=('chamber.set','opened'))
slot('lid.angle_rad',number('rad',0,math.pi/2),'rad','后铰链-X90度代理','scene',c='lid')
for k in ['sputter','carbon']:slot('head.'+k+'_shift_m',number('m',0,.28),'m','源盒安装/旁置代理，非机器人换头','scene',c=k+'_head')
slot('shutter.shift_m',number('m',0,.07),'m','预清挡板关闭，沉积开启代理','scene',c='shutter')
slot('stage.lift_m',number('m',0,.015),'m','手调台高代理','scene',c='stage_lift',w=('stage.set_height','lift_m'))
slot('stage.angle_rad',number('rad',0,2*math.pi),'rad','旋转台实际过程角','scene',c='sample_stage')
slot('mains.angle_rad',number('rad',0,math.pi/2),'rad','后部电源开关代理','scene',c='mains_switch')
slot('source.kind',KIND,'1','当前已安装源种类','scene',c='sputter_head')
slot('source.substance_id',TEXT,'1','宿主安装源身份，不等于分析未知样品','setpoint')
slot('vacuum.pump_enabled',BOOL,'1','泵使能','setpoint',c='vacuum_port',w=('vacuum.pump','enabled'))
slot('vacuum.venting',BOOL,'1','回大气通气状态')
slot('vacuum.pressure_pa',number('Pa',0),'Pa','腔绝对压力理想值')
slot('rotation.enabled',BOOL,'1','样品旋转设定','setpoint')
slot('rotation.rpm',number('rpm',8,20),'rpm','样品转速设定','setpoint')
slot('source.current_a',number('A',0,60),'A','当前溅射/碳棒电流')
slot('ftm.enabled',BOOL,'1','膜厚监测选件配置','setpoint')
slot('ftm.thickness_m',number('m',0),'m','本次晶体增重/参考密度×tooling，非样品实际膜厚')
slot('sample.maximum_k',number('K',1),'K','6样品最高模拟温度；非厂家内置传感器')
slot('cycle.phase',PHASE,'1','异步操作状态')
slot('cycle.stage',TEXT,'1','抽空/稳压/预清/沉积/冷却阶段')
slot('cycle.elapsed_s',number('s',0),'s','本操作总时间')
slot('cycle.deposition_s',number('s',0),'s','有效沉积时间，不含自动冷却')
slot('safety.warning',TEXT,'1','模拟警告，不作为实机安全认证')
slot('truth.ledger',LEDGER,'1','逐源质量/热量/气体真值','process_truth','evaluator','sample_stage')
for k in CAPS:
    if CAPS[k][4]!='query':CAPS[k][7].extend(SLOTS)
TARGET_CODES={0,3,11,50,52,61,72,104,119,127,149,164,169,191,199,212,233}
class QuorumQ150tesRuntime:
    VOLUME=math.pi*.075**2*.127;R=8.314462618
    def __init__(self,instance_id='q150tes_01'):
        self.instance_id=instance_id;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']}
        self.time_s=0.;self.revision=0;self.plant=None;self.method=None;self.source=None;self.samples=None;self.lots=[];self.sample_energy=[0.]*6;self.collected_energy=0.;self.electric=0.;self.environment=0.;self.latent=0.
        self.gas_initial=101325*self.VOLUME/(self.R*298.15);self.gas_chamber=self.gas_initial;self.gas_outgas=0.;self.gas_argon=0.;self.gas_vent=0.;self.gas_pumped=0.
        self.powered=False;self.opened=True;self.pump=False;self.vent=False;self.angle=0.;self.lift=0.;self.current=0.;self.phase='idle';self.stage='idle';self.warning='';self.elapsed_s=0.;self.deposit_s=0.;self.stage_elapsed=0.;self.burst=0.;self.qcm_zero=0.
        self.operation_id='';self._operation_count=0;self.log=[];self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._sample_ids=set()
    def register_principal(self,principal_id,visibility='agent'):
        if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id='__public_agent__'):
        if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return QuorumQ150tesSession(self,principal_id)
    def describe(self):return self.session().describe(instance_id=self.instance_id)
    def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k!='templates'})
    def _p(self):return self.gas_chamber*self.R*(self.plant['ambient_k'] if self.plant else 298.15)/self.VOLUME
    def _temps(self):return [(self.plant['ambient_k'] if self.plant else 298.15)+v/(self.plant['sample_heat_capacity_j_k'] if self.plant else 1.) for v in self.sample_energy]
    def _lot(self):return self.lots[-1] if self.lots else None
    def _ftm(self):
        if not self.method or not self.method['ftm_enabled'] or not self.source:return 0.
        return max(0.,self._lot()['crystal_kg']-self.qcm_zero)/self.plant['qcm_area_m2']/self.templates[self.source['substance_id']]['values']['density_kg_m3']*self.method['tooling']
    def _ledger(self):
        return dict(lots=copy.deepcopy(self.lots),gas_initial_mol=self.gas_initial,gas_chamber_mol=self.gas_chamber,gas_outgas_mol=self.gas_outgas,gas_argon_mol=self.gas_argon,gas_vent_mol=self.gas_vent,gas_pumped_mol=self.gas_pumped,electrical_j=self.electric,environment_j=self.environment,carbon_latent_j=self.latent,sample_sensible_j=list(self.sample_energy),collected_sensible_j=self.collected_energy,evidence='synthetic_conservative_lumped_model_not_Q150_calibration')
    def read(self,visibility='agent'):
        if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
        m=self.method or METHOD_EXAMPLE;k=self.source['kind'] if self.source else 'sputter'
        v={'plant.configured':self.plant is not None,'method.configured':self.method is not None,'power.enabled':self.powered,'chamber.opened':self.opened,'lid.angle_rad':math.pi/2 if self.opened else 0.,'head.sputter_shift_m':0. if k=='sputter' else .28,'head.carbon_shift_m':0. if k=='carbon' else .28,'shutter.shift_m':.07 if self.phase=='running' and self.stage=='deposition' else 0.,'stage.lift_m':self.lift,'stage.angle_rad':self.angle,'mains.angle_rad':math.pi/2 if self.powered else 0.,'source.kind':k,'source.substance_id':self.source['substance_id'] if self.source else '',
        'vacuum.pump_enabled':self.pump,'vacuum.venting':self.vent,'vacuum.pressure_pa':self._p(),'rotation.enabled':m['rotation_enabled'],'rotation.rpm':m['rotation_rpm'],'source.current_a':self.current,'ftm.enabled':bool(self.method and m['ftm_enabled']),'ftm.thickness_m':self._ftm(),'sample.maximum_k':max(self._temps()),'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.elapsed_s':self.elapsed_s,'cycle.deposition_s':self.deposit_s,'safety.warning':self.warning,'truth.ledger':self._ledger()}
        return copy.deepcopy({k:x for k,x in v.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
    def invoke(self,capability_id,**a):
        if capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability_id][0],a);old=self.debug_snapshot()
        try:
            result=self._invoke(capability_id,a);validate(CAPS[capability_id][1],result)
            if CAPS[capability_id][4]!='query':self.revision+=1;self.log.append(dict(time_s=self.time_s,event=capability_id,stage=self.stage,phase=self.phase))
            return copy.deepcopy(result)
        except Exception:
            templates=self.templates;self.__dict__.clear();self.__dict__.update(old);self.templates=templates;raise
    def _idle(self):
        if self.phase=='running':raise ActionRejected('BUSY')
    def _access(self):
        self._idle()
        if not self.opened or self.powered:raise ActionRejected('PRECONDITION_FAILED')
    def _stop(self,phase='cancelled',warning=''):
        if self.phase=='running':self.phase=phase;self.stage='finished'
        self.current=0.;self.warning=warning
    def _ref(self,sid):
        if sid not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
        r=self.templates[sid];v=r['values']
        return dict(substance_id=sid,name=r['name'],formula_raw=r['formula_raw'],density_kg_m3=v['density_kg_m3'],z_display=v['source_z_display'],z_established=v['z_established'],evidence=r['evidence'],source=r['source']['url']+'#'+r['source']['locator'],template_source=TEMPLATE_REL,conditions=r['conditions'])
    def _invoke(self,k,a):
        if k=='reference.lookup':return self._ref(a['substance_id'])
        if k=='qcm.convert':
            r=self._ref(a['substance_id']);return dict(r,thickness_m=a['areal_mass_kg_m2']/r['density_kg_m3']*a['tooling'])
        if k=='product.read':return self._ledger()
        if k=='coating.measure':return dict(phase=self.phase,stage=self.stage,pressure_pa=self._p(),current_a=self.current,deposition_elapsed_s=self.deposit_s,ftm_enabled=bool(self.method and self.method['ftm_enabled']),thickness_m=self._ftm(),sample_temperature_k=self._temps(),evidence='synthetic_mass_deposition_with_documented_reference_density',template_source=TEMPLATE_REL,calibrated=False)
        if k=='plant.configure':
            self._access()
            if self.plant is not None or self.lots or not a['accept_uncalibrated_model']:raise ActionRejected('PRECONDITION_FAILED')
            if a['source_heat_fraction']+a['sample_heat_fraction']>1:raise ActionRejected('INVALID_ARGUMENT')
            self.plant=copy.deepcopy(a);self.gas_initial=a['ambient_pa']*self.VOLUME/(self.R*a['ambient_k']);self.gas_chamber=self.gas_initial;return {'configured':True}
        if k=='source.install':
            self._access()
            if not self.plant or self.samples or not a['accept_host_attachment']:raise ActionRejected('PRECONDITION_FAILED')
            r=self._ref(a['substance_id']);code=self.templates[a['substance_id']]['source_code']
            if (a['kind']=='sputter' and code not in TARGET_CODES) or (a['kind']=='carbon' and code!=31):raise ActionRejected('UNSUPPORTED_SOURCE')
            if code==52 and a['target_thickness_m']!=.0005:raise ActionRejected('FROZEN_CR_TARGET')
            if self.lots:
                old=self._lot();old['returned_kg']+=old['remaining_kg']+old['stock_kg'];old['remaining_kg']=old['stock_kg']=0.
            rho=r['density_kg_m3'];initial=math.pi*.0285**2*a['target_thickness_m']*rho if a['kind']=='sputter' else math.pi*(.00305/2)**2*.3*rho
            active=initial if a['kind']=='sputter' else initial*2*a['carbon_piece_length_m']/.3
            lot={k:0. for k in LOT_FIELDS if k.endswith('_kg')}
            lot.update(lot_id='source_'+str(len(self.lots)+1),substance_id=a['substance_id'],kind=a['kind'],film_density_kg_m3=a['film_density_kg_m3'],initial_kg=initial,remaining_kg=active,stock_kg=initial-active,sample_kg=[0.]*6,collected_kg=[0.]*6,source_sensible_j=0.)
            self.lots.append(lot);self.source=copy.deepcopy(a);self.qcm_zero=0.;return dict(lot_id=lot['lot_id'],attachment_event='source_exchange_by_host')
        if k=='samples.load':
            self._access()
            if not self.source or self.samples or not a['accept_host_attachment'] or len(set(a['sample_ids']))!=6 or any(not s for s in a['sample_ids']) or set(a['sample_ids']) & self._sample_ids:raise ActionRejected('PRECONDITION_FAILED')
            self.samples=copy.deepcopy(a);self._sample_ids.update(a['sample_ids']);return dict(loaded=True,attachment_event='six_sample_stubs_attached_by_host')
        if k=='samples.collect':
            self._access()
            if not self.samples or not a['recipient_id']:raise ActionRejected('PRECONDITION_FAILED')
            for lot in self.lots:
                lot['collected_kg']=[x+y for x,y in zip(lot['collected_kg'],lot['sample_kg'])];lot['sample_kg']=[0.]*6
            self.collected_energy+=sum(self.sample_energy);self.sample_energy=[0.]*6;self.samples=None;return dict(recipient_id=a['recipient_id'],attachment_event='samples_released_by_host')
        if k=='service.clean':
            self._access()
            if self.samples or not a['accept_host_cleaning']:raise ActionRejected('PRECONDITION_FAILED')
            for lot in self.lots:
                for loc in ['wall_kg','shutter_kg','crystal_kg']:lot['waste_kg']+=lot[loc];lot[loc]=0.
            self.qcm_zero=0.;return dict(cleaned=True)
        if k=='method.configure':
            self._idle()
            if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
            if a['thickness_endpoint'] and not a['ftm_enabled']:raise ActionRejected('FTM_OPTION_REQUIRED')
            if not self.opened and a['stage_lift_m']!=self.lift:raise ActionRejected('OPEN_TO_ADJUST_STAGE')
            self.method=copy.deepcopy(a);self.lift=a['stage_lift_m'];return {'configured':True}
        if k=='stage.set_height':
            self._access()
            if not self.method:raise ActionRejected('PRECONDITION_FAILED')
            self.lift=a['lift_m'];self.method['stage_lift_m']=self.lift;return dict(lift_m=self.lift)
        if k=='rotation.set':
            if not self.method:raise ActionRejected('PRECONDITION_FAILED')
            self.method['rotation_enabled']=a['enabled'];self.method['rotation_rpm']=a['rpm'];return dict(a)
        if k=='power.set':
            if a['enabled'] and not self.plant:raise ActionRejected('PRECONDITION_FAILED')
            if not a['enabled']:self._stop();self.pump=False
            self.powered=a['enabled'];return dict(enabled=self.powered)
        if k=='chamber.set':
            if a['opened']:
                pa=self.plant['ambient_pa'] if self.plant else 101325.
                if self._p()<.999*pa:raise ActionRejected('VENT_BEFORE_OPEN')
                self._stop();self.pump=False;self.vent=False
            self.opened=a['opened'];return dict(opened=self.opened)
        if k=='vacuum.pump':
            if a['enabled'] and (not self.powered or self.opened):raise ActionRejected('PRECONDITION_FAILED')
            if not a['enabled']:self._stop()
            self.pump=a['enabled'];self.vent=False;return dict(enabled=self.pump)
        if k=='vacuum.vent':
            if not self.plant or not self.powered or self.opened:raise ActionRejected('PRECONDITION_FAILED')
            self._stop();self.pump=False;self.vent=True;return dict(venting=True)
        if k=='acquisition.start':
            self._idle()
            if not self.powered or self.opened or not self.samples or not self.source or not self.method:raise ActionRejected('PRECONDITION_FAILED')
            if self._lot()['remaining_kg']<=1e-16:raise ActionRejected('SOURCE_EMPTY')
            self._operation_count+=1;self.operation_id=self.instance_id+':coat:'+str(self._operation_count);self.phase='running';self.stage='pumpdown';self.elapsed_s=0.;self.stage_elapsed=0.;self.deposit_s=0.;self.burst=0.;self.current=0.;self.warning='';self.pump=True;self.vent=False;self.qcm_zero=self._lot()['crystal_kg']
            return dict(phase=self.phase,operation_id=self.operation_id)
        if k=='acquisition.stop':self._stop();return dict(phase=self.phase)
        raise ActionRejected('UNKNOWN_CAPABILITY')
    def _gas_step(self,dt):
        if not self.plant:return
        p=self.plant;rt=self.R*p['ambient_k'];old=self.gas_chamber
        if self.opened or self.vent:
            target=p['ambient_pa']*self.VOLUME/rt
            new=target if self.opened else target+(old-target)*math.exp(-dt/p['vent_tau_s'])
            if new>=old:self.gas_vent+=new-old
            else:self.gas_pumped+=old-new
            self.gas_chamber=new
        elif self.pump and self.powered:
            out=max(p['outgas_pa_m3_s'],p['pump_m3_s']*.005)/rt*dt
            argon=max(0.,p['pump_m3_s']*self.method['working_pressure_pa']-max(p['outgas_pa_m3_s'],p['pump_m3_s']*.005))/rt*dt if self.phase=='running' and self.source['kind']=='sputter' and self.stage!='pumpdown' else 0.
            rate=(out+argon)/dt;neq=rate*self.VOLUME/p['pump_m3_s'];new=neq+(old-neq)*math.exp(-p['pump_m3_s']/self.VOLUME*dt)
            self.gas_outgas+=out;self.gas_argon+=argon;self.gas_pumped+=max(0.,old+out+argon-new);self.gas_chamber=new
    def _next_stage(self,stage):self.stage=stage;self.stage_elapsed=0.;self.current=0.
    def _fractions(self):
        p=self.plant;h=.105-self.lift;off=p['source_offset_m'];A=self.samples['area_m2'];f=[]
        for i in range(6):
            a=self.angle+2*math.pi*i/6;x=.016*math.cos(a);y=.016*math.sin(a);r2=(x-off)**2+y*y
            f.append(A*h*h/(math.pi*(h*h+r2)**2))
        q=p['qcm_area_m2']*.108**2/(math.pi*(.108**2+(.045-off)**2)**2) if self.method['ftm_enabled'] else 0.
        return f,q
    def _thermal(self,dt,power,carbon):
        p=self.plant;lot=self._lot();self.electric+=power*dt;sample_power=power*p['sample_heat_fraction'] if self.samples else 0.;source_power=power*p['source_heat_fraction'] if carbon else 0.
        self.environment+=(power-sample_power-source_power)*dt
        for i,E in enumerate(self.sample_energy):
            loss=p['sample_loss_w_k'];capacity=p['sample_heat_capacity_j_k'];qin=sample_power/6
            new=E+qin*dt if loss==0 else E*math.exp(-loss*dt/capacity)+qin*capacity/loss*(1-math.exp(-loss*dt/capacity))
            self.environment+=E+qin*dt-new;self.sample_energy[i]=new
        eroded=0.
        for item in self.lots:
            E=item['source_sensible_j'];power_here=source_power if item is lot else 0.;C=p['carbon_heat_capacity_j_k'];K=p['carbon_loss_w_k']
            new=E+power_here*dt if K==0 else E*math.exp(-K*dt/C)+power_here*C/K*(1-math.exp(-K*dt/C))
            self.environment+=E+power_here*dt-new
            limit=C*(p['carbon_sublimation_k']-p['ambient_k'])
            if item is lot and carbon and new>limit:
                eroded=min(item['remaining_kg'],(new-limit)/p['carbon_latent_j_kg']);energy=eroded*p['carbon_latent_j_kg'];new-=energy;self.latent+=energy
            item['source_sensible_j']=new
        return eroded
    def step(self,dt_s):
        validate(number('s',0,3600),dt_s)
        remaining=float(dt_s)
        while remaining>1e-12:
            dt=min(.02,remaining)
            if self.phase=='running' and self.stage=='deposition' and self.deposit_s<self.method['duration_s']:
                dt=min(dt,self.method['duration_s']-self.deposit_s)
            if dt<1e-12:self._stop('succeeded');continue
            remaining-=dt;before=(self.phase,self.stage);self._advance(dt);self.time_s+=dt;self.revision+=1
            if before!=(self.phase,self.stage):self.log.append(dict(time_s=self.time_s,event='stage_transition',phase=self.phase,stage=self.stage))
    def _advance(self,dt):
        if not self.plant:return
        self._gas_step(dt);p=self.plant;m=self.method;active=self.phase=='running'
        if active:
            self.elapsed_s+=dt;self.stage_elapsed+=dt
            if self.opened or not self.powered or not self.pump:self._stop('failed','PROCESS_INTERLOCK');active=False
            elif self.stage=='pumpdown':
                if self._p()<=.01:self._next_stage('stabilizing' if self.source['kind']=='sputter' else 'preclean')
                elif self.elapsed_s>=p['pump_timeout_s']:self._stop('failed','PUMP_TIMEOUT')
            elif self.stage=='stabilizing':
                if abs(self._p()-m['working_pressure_pa'])<=max(.02,.02*m['working_pressure_pa']):self._next_stage('preclean')
                elif self.elapsed_s>=p['pump_timeout_s']:self._stop('failed','PRESSURE_TIMEOUT')
            elif self.stage=='preclean' and self.stage_elapsed>=m['preclean_s']:self._next_stage('deposition')
            elif self.stage=='cooling' and self.stage_elapsed>=p['cool_duration_s']:self.burst=0.;self._next_stage('deposition')
        generating=self.phase=='running' and self.stage in ['preclean','deposition']
        carbon=bool(self.source and self.source['kind']=='carbon');self.current=0. if not generating else (min(m['carbon_current_a'],20.+m['carbon_ramp_a_s']*self.stage_elapsed) if carbon else m['sputter_current_a'])
        if generating and ((carbon and self._p()>.02) or (not carbon and not .5<=self._p()<=10.2)):
            self._stop('failed','VACUUM_SOURCE_GUARD');generating=False;self.current=0.
        power=self.current**2*p['carbon_resistance_ohm'] if carbon else self.current*p['sputter_voltage_v']
        eroded=self._thermal(dt,power,carbon and generating)
        if generating:
            lot=self._lot()
            if not carbon:eroded=min(lot['remaining_kg'],self.current*p['sputter_yield_kg_c']*dt)
            lot['remaining_kg']-=eroded
            if self.stage=='preclean':lot['shutter_kg']+=eroded
            else:
                fractions,q=self._fractions()
                for i,f in enumerate(fractions):lot['sample_kg'][i]+=eroded*f
                lot['crystal_kg']+=eroded*q;lot['wall_kg']+=eroded*(1-sum(fractions)-q)
                self.deposit_s=min(m['duration_s'],self.deposit_s+dt);self.burst+=dt
                if self.deposit_s>=m['duration_s']-1e-10 or (m['thickness_endpoint'] and self._ftm()>=m['endpoint_m']):self._stop('succeeded')
                elif self.burst>=p['cool_after_s']-1e-10:self._next_stage('cooling')
            if lot['remaining_kg']<=1e-16 and self.phase=='running':self._stop('failed','SOURCE_EMPTY')
        if self.phase=='running' and m['rotation_enabled'] and self.stage in ['preclean','deposition','cooling']:self.angle=(self.angle+m['rotation_rpm']*2*math.pi/60*dt)%(2*math.pi)
        if max(self._temps())>p['sample_trip_k'] and self.phase=='running':self._stop('failed','SAMPLE_OVERHEAT')

class QuorumQ150tesSession:
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
        m=export_quorum_q150tes_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('coating.measure'))
    def cancel(self,operation_id,request_id):
        self.poll(operation_id);return self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)






def export_quorum_q150tes_contract_metadata():
    caps={};slots={}
    for k,(s,u,o,v,c,w) in SLOTS.items():
        slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'QuorumQ150tesSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    evolving=[k for k,v in SLOTS.items() if v[2] in ['scene','derived','process_truth']]
    for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
        asynchronous=k in ['acquisition.start'];query=kind=='query'
        caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.quorum_q150tes.QuorumQ150tesSession.invoke',invocation=f'session.invoke(instance_id="q150tes_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),
          preconditions=[] if query else [dict(description_zh='电源、真空、开盖、有限源材料/样品及宿主身份检查',state_ids=['power.enabled','chamber.opened','vacuum.pump_enabled','cycle.phase'],enforced_by='runtime',check='Runtime.invoke atomic guards; Session trusted-principal filtering')],
          state_effects=[] if query else [dict(state_id=s,timing='immediate',description_zh='设定/附着事件/有限装料变化') for s in effects]+[dict(state_id=s,timing='on_step',description_zh='宿主唯一时钟演化') for s in evolving],
          observes=[],model_effects=dict(mode='none',description_zh='模型实际求值证据尚待独立核验',binding_ids=[]),
          execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',completion_criterion='有效沉积时间或FTM终点达到；不等于实测膜厚/均匀性' if asynchronous else '事务返回；物理量由宿主step演化',
            completion_state_ids=['cycle.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',retry='same_request_id',resources=['host_clock','source','samples','vacuum','request_cache'],
            partial_failure_zh='取消保留已沉积样品/源消耗与热量，不能回滚物质；仅进程内请求去重。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','PRECONDITION_FAILED','BUSY','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','UNSUPPORTED_SOURCE','FROZEN_CR_TARGET','FTM_OPTION_REQUIRED','OPEN_TO_ADJUST_STAGE','VENT_BEFORE_OPEN','SOURCE_EMPTY']],
          example_arguments=copy.deepcopy(ex),verification=[],limitations=['有限逐源质量/气体/电热模型；非厂家固件/标定/配方建议'],source_ids=['manufacturer_primary','simulation_assumption'])


    mech=dict(component_ids=['sputter_head','carbon_head','sample_stage','qcm_sensor','vacuum_port'],state_ids=list(slots),meaning_zh='有限逐源质量、理想气体真空与集总电热沉积；物性参考不等于机型标定',implementation='lumped',
      equations=['Vacuum: dn/dt=outgas/(R*T)+argon/(R*T)-S*n/V; V=pi*0.075²*0.127; pressure=nRT/V, limiting gas load at least S*0.005Pa',
      'Per source lot: initial=remaining+stock+returned+wall+shutter+crystal+waste+sum(sample)+sum(collected); identities never reinterpreted on exchange',
      'Sputter eroded_kg=min(remaining,current_A*host_yield_kg_C*dt); preclean intercepted by shutter',
      'Carbon electrical power=I²R; explicit source thermal capacitance/loss and host sublimation temperature; excess energy/host_latent gives finite evaporation',
      'Lambert point-source capture_i=A_i*h²/[pi*(h²+r_i²)²]; h=0.105-stage_lift; stationary crystal h=0.108m; eccentricity and rotating six stubs; residual mass to wall',
      'electrical_J=environment_J+carbon_latent_J+sum(source_sensible_J)+sum(sample_sensible_J)+collected_sensible_J',
      'FTM indicated thickness=(crystal_delta_mass/crystal_area)/reference_density*tooling; true model sample thickness=sample_mass/(sample_area*host_film_density)',
      'Host.step sole time; finite duration excludes cooling; FTM endpoint tested each <=0.02s interval (bounded discrete overshoot, not ideal exact physical endpoint)'],
      parameters={},source_ids=['manufacturer_primary','manufacturer_2021','qcm_reference','simulation_assumption'],
      limitations=['252 distinct raw formulae/255 real reference records, 117 established Z; no claimed 50-material Q150 deposition calibration.',
      'Only supplier-listed pure-element sputter targets and graphite rods; no alloys/ITO/cord source without independent properties.',
      'Point-source small-area collection does not resolve plasma/target racetrack/scattering/film roughness or real surface adhesion.',
      'FTM ideal mass-density conversion, no frequency/Z acoustic correction, drift or physical sensor calibration; actual sample film density remains explicit assumption.',
      'Carbon chopped short rods plus remaining300mm stock, lumped ramp not pulsed/cord or certified safety controls.',
      'Full touchscreen/recipe memory/USB firmware, robot, physical attachment/collision/seal and real hardware absent.'])
    spec=importlib.util.spec_from_file_location('_quorum_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann)
    ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
    ann.annotate_export(SLUG,caps,slots,{'coating':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'source.install':(SOURCE,SOURCE_EXAMPLE),'samples.load':(SAMPLES,SAMPLES_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
    for k,v in slots.items():
        v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'，非实机标定。'
        if v['unit']=='Pa':v['reference_conditions']='腔体绝对压力；5e-5mbar=0.005Pa极限，不是工作气压'
        if v['unit']=='m':v['reference_conditions']='SI实际几何位移或基于晶体质量的理想膜厚，见语义'
    mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'coating':mech})


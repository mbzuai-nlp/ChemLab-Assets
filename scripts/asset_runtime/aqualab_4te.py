"""4TE cold-mirror water activity: measured solution points, conservative water ledger."""
from __future__ import annotations
import copy,json,math
from asset_runtime import _annotations as _ann
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT,PHASE
from asset_runtime.antonpaar_abbematadvanced import validate
ROOT=Path(__file__).resolve().parents[2];ASSET_TYPE_ID='instrument.aqualab_4te';CATALOG_ID='CHEM-007';PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/aqualab_4te_water_activity_library/templates.json'
TEMP=number('K',288.15,323.15);AW=number('1',.03,1.);NULL_AW={'anyOf':[AW,{'type':'null'}]};ANGLE=number('rad',0,math.pi/2);NULL_NUM={'anyOf':[number('1',0),{'type':'null'}]}
MODE={'type':'string','enum':['single','continuous','custom','low_emitting']}
RESULT=obj({'water_activity':NULL_AW,'temperature_k':TEMP,'equilibrated':BOOL,'phase':PHASE,'quality':TEXT,'evidence':TEXT,'template_source':TEXT,'moisture_dry_basis':NULL_NUM,'moisture_evidence':TEXT})
CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet','button_power'],'电源设定，断电取消；预热状态单独读出','device_command','agent',{'enabled':True},['power.enabled','cycle.phase','warmup.elapsed_s']),
 'thermal.set_temperature':(obj({'temperature_k':TEMP}),obj({'temperature_k':TEMP}),['sample_peltier','mirror_peltier'],'15–50C、0.1C步进目标，设置不推进温度','device_command','agent',{'temperature_k':298.15},['thermal.target_k','water_activity.indicated']),
 'lid.set_angle':(obj({'angle_rad':ANGLE}),obj({'angle_rad':ANGLE}),['lid'],'锁杆释放后才能开盖；被动盖宿主姿态，不是机器人技能','device_command','agent',{'angle_rad':math.pi/2},['lid.angle_rad']),
 'latch.set_angle':(obj({'angle_rad':ANGLE}),obj({'angle_rad':ANGLE}),['lever_lock'],'闭盖后锁定READ；释放锁杆取消采集','device_command','agent',{'angle_rad':0.},['latch.angle_rad','cycle.phase']),
 'sample.configure':(obj({'substance_id':TEXT,'record_index':number('1',0),'mass_kg':number('kg',1e-5,.02),'volume_m3':number('m^3',1e-7,7e-6),'matrix_id':TEXT,'initial_temperature_k':TEMP,'equilibration_tau_s':number('s',5,600),'volatile_interference':BOOL}),obj({'configured':BOOL}),['sample_cup','cup_interface'],'宿主装样，选择实测溶液组成；质量和体积为独立输入，非隐含密度换算','configuration','evaluator',{'substance_id':'sodium_chloride','record_index':0,'mass_kg':.003,'volume_m3':3e-6,'matrix_id':'salt','initial_temperature_k':298.15,'equilibration_tau_s':35.,'volatile_interference':False},['sample.loaded','sample.input_kg','sample.retained_kg','water_activity.indicated']),
 'sample.unload':(obj({}),obj({'unloaded':BOOL}),['sample_cup','cup_interface'],'开盖后宿主移出样品，守恒账本保留实际水分交换','configuration','evaluator',{},['sample.loaded','sample.retained_kg','sample.removed_kg']),
 'method.configure':(obj({'mode':MODE,'stability_delta':number('1',.0003,.02),'temperature_tolerance_k':number('K',.1,4.),'consecutive_readings':number('1',1,9)}),obj({'configured':BOOL}),['display_digits'],'单次、连续、自定义与低释放模式的平衡代理，非厂家完整算法','configuration','agent',{'mode':'single','stability_delta':.0005,'temperature_tolerance_k':.5,'consecutive_readings':1},['method.mode','equilibrium.stable']),
 'calibration.configure':(obj({'offset_aw':number('1',-.03,.03),'source':TEXT}),obj({'offset_aw':number('1',-.03,.03)}),['chilled_mirror','display_digits'],'有来源的线性偏置代理，不宣称真实工厂校准','configuration','agent',{'offset_aw':0.,'source':'synthetic zero-offset example'},['calibration.offset_aw']),
 'calibration.verify':(obj({'reference_aw':AW}),obj({'deviation_aw':number('1',-1,1),'within_vendor_tolerance':BOOL}),['chilled_mirror'],'完成预热且最近平衡后对已知标准核查，不自动调参','query','agent',{'reference_aw':.92},[]),
 'isotherm.configure':(obj({'matrix_id':TEXT,'temperature_k':TEMP,'source':TEXT,'points':{'type':'array','minItems':2,'maxItems':128,'items':obj({'water_activity':AW,'moisture_dry_basis':number('1',0)})}}),obj({'configured':BOOL}),['display_digits'],'仅输入有来源的特定产品等温线，适用温度内插，不提供通用含水率','configuration','agent',{'matrix_id':'demo','temperature_k':298.15,'source':'synthetic test only','points':[{'water_activity':.1,'moisture_dry_basis':.01},{'water_activity':.99,'moisture_dry_basis':.4}]},['isotherm.configured']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['chilled_mirror','optical_sensor','thermopile','chamber_fan','display_digits'],'闭盖锁紧后按实际稳定判据采集；典型5min非固定周期','device_command','agent',{},['cycle.phase','cycle.elapsed_s','equilibrium.stable']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['display_digits'],'停止采集，水分转移和热过程不回滚','device_command','agent',{},['cycle.phase']),
 'water_activity.measure':(obj({}),RESULT,['chilled_mirror','thermopile','display_digits'],'纯读水活度、样温和平衡状态；不泄露样品真值','query','agent',{},[]),
}
SLOTS={
 'power.enabled':(BOOL,'1','setpoint','agent',['power_inlet'],('power.set','enabled')),
 'thermal.target_k':(TEMP,'K','setpoint','agent',['sample_peltier'],('thermal.set_temperature','temperature_k')),
 'thermal.block_k':(TEMP,'K','process_truth','evaluator',['sample_block'],None),
 'thermal.sample_k':(TEMP,'K','process_truth','evaluator',['sample_cup'],None),
 'thermal.indicated_temperature_k':(TEMP,'K','derived','agent',['thermopile','display_digits'],None),
 'lid.angle_rad':(ANGLE,'rad','scene','agent',['lid'],('lid.set_angle','angle_rad')),
 'latch.angle_rad':(ANGLE,'rad','scene','agent',['lever_lock'],('latch.set_angle','angle_rad')),
 'warmup.elapsed_s':(number('s',0),'s','derived','agent',['electronics'],None),
 'warmup.ready':(BOOL,'1','derived','agent',['electronics'],None),
 'method.mode':(MODE,'1','setpoint','agent',['display_digits'],None),
 'cycle.phase':(PHASE,'1','derived','agent',['display_digits'],None),
 'cycle.elapsed_s':(number('s',0),'s','derived','agent',['display_digits'],None),
 'equilibrium.stable':(BOOL,'1','derived','agent',['display_digits'],None),
 'water_activity.indicated':(NULL_AW,'1','derived','agent',['chilled_mirror','display_digits'],None),
 'dewpoint.indicated_k':({'anyOf':[number('K',200,323.15),{'type':'null'}]},'K','derived','agent',['chilled_mirror'],None),
 'calibration.offset_aw':(number('1',-.03,.03),'1','setpoint','agent',['chilled_mirror'],None),
 'isotherm.configured':(BOOL,'1','derived','agent',['display_digits'],None),
 'sample.loaded':(BOOL,'1','derived','agent',['sample_cup'],None),
 'sample.input_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_cup'],None),
 'sample.retained_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_cup'],None),
 'sample.removed_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_cup'],None),
 'water.input_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_cup'],None),
 'water.sample_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_cup'],None),
 'water.headspace_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_block'],None),
 'water.removed_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_cup'],None),
 'water.ambient_in_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_block'],None),
 'water.ambient_out_kg':(number('kg',0),'kg','process_truth','evaluator',['sample_block'],None),
}
def psat(t):
    c=t-273.15;return 610.94*math.exp(17.625*c/(243.04+c))
def dewpoint(p):
    x=math.log(max(p,1e-12)/610.94);return 273.15+243.04*x/(17.625-x)
class Aqualab4teRuntime:
    def __init__(self,instance_id='aqualab4te_01'):
        self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.warmup_s=0.
        self.target_k=298.15;self.block_k=298.15;self.sample_k=298.15;self.lid_angle=0.;self.latch_angle=0.
        self.mode='single';self.delta=.0005;self.temp_tolerance=.5;self.consecutive=1;self.offset=0.;self.isotherm=None
        self.phase='idle';self.elapsed_s=0.;self.operation_id=None;self._operation_count=0;self.stable=False;self.indicated_aw=None;self.indicated_dew=None;self.history=[];self._stable_count=0
        self.templates=json.loads((ROOT/TEMPLATE_REL).read_text())['substances'];self.sample=None
        self.input_kg=0.;self.removed_kg=0.;self.water_input=0.;self.water_removed=0.;self.sample_water=0.;self.sample_dry=0.
        self.vapor=psat(298.15)*.5*20e-6*.01801528/(8.314462618*298.15);self.ambient_in=self.vapor;self.ambient_out=0.
        self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
    def register_principal(self,principal_id,visibility='agent'):
        if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id='__public_agent__'):
        if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return Aqualab4teSession(self,principal_id)
    def describe(self):return self.session().describe(instance_id=self.instance_id)
    def _values(self):
        return {'power.enabled':self.powered,'thermal.target_k':self.target_k,'thermal.block_k':self.block_k,'thermal.sample_k':self.sample_k,'thermal.indicated_temperature_k':round(self.sample_k-273.15,2)+273.15,'lid.angle_rad':self.lid_angle,'latch.angle_rad':self.latch_angle,'warmup.elapsed_s':self.warmup_s,'warmup.ready':self.warmup_s>=900,'method.mode':self.mode,'cycle.phase':self.phase,'cycle.elapsed_s':self.elapsed_s,'equilibrium.stable':self.stable,'water_activity.indicated':self.indicated_aw,'dewpoint.indicated_k':self.indicated_dew,'calibration.offset_aw':self.offset,'isotherm.configured':self.isotherm is not None,'sample.loaded':self.sample is not None,'sample.input_kg':self.input_kg,'sample.retained_kg':self.sample_water+self.sample_dry,'sample.removed_kg':self.removed_kg,'water.input_kg':self.water_input,'water.sample_kg':self.sample_water,'water.headspace_kg':self.vapor,'water.removed_kg':self.water_removed,'water.ambient_in_kg':self.ambient_in,'water.ambient_out_kg':self.ambient_out}
    def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('evaluator','debug')})
    def debug_snapshot(self):return dict(state=self.read('evaluator'),sample=copy.deepcopy(self.sample))
    def _measure(self):
        moisture=None
        if self.sample and self.isotherm and self.indicated_aw is not None and self.isotherm['matrix_id']==self.sample['matrix_id'] and abs(self.sample_k-self.isotherm['temperature_k'])<.05:
            for a,b in zip(self.isotherm['points'],self.isotherm['points'][1:]):
                if a['water_activity']<=self.indicated_aw<=b['water_activity']:
                    moisture=a['moisture_dry_basis']+(b['moisture_dry_basis']-a['moisture_dry_basis'])*(self.indicated_aw-a['water_activity'])/(b['water_activity']-a['water_activity']);break
        quality='unavailable' if self.indicated_aw is None else 'warmup_incomplete' if self.warmup_s<900 else 'suspect_volatile' if self.sample and self.sample['volatile_interference'] else 'estimated'
        return dict(water_activity=self.indicated_aw,temperature_k=self._values()['thermal.indicated_temperature_k'],equilibrated=self.stable,phase=self.phase,quality=quality,evidence='derived_measured_reference_with_synthetic_dynamics',template_source=TEMPLATE_REL,moisture_dry_basis=moisture,moisture_evidence='user_supplied_isotherm' if moisture is not None else 'unavailable')
    def invoke(self,capability,**a):
        if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability][0],a)
        if self.phase=='running' and capability not in ('power.set','latch.set_angle','acquisition.stop','water_activity.measure','calibration.verify'):raise ActionRejected('BUSY')
        if capability=='power.set':
            if not self.powered and a['enabled']:self.warmup_s=0.
            self.powered=a['enabled']
            if not self.powered:
                self.stable=False
                if self.phase=='running':self.phase='cancelled'
            r=dict(a)
        elif capability=='thermal.set_temperature':
            if abs((a['temperature_k']-273.15)*10-round((a['temperature_k']-273.15)*10))>1e-7:raise ActionRejected('INVALID_ARGUMENT')
            self.target_k=a['temperature_k'];self.indicated_aw=None;self.stable=False;r=dict(a)
        elif capability=='lid.set_angle':
            if a['angle_rad']>1e-4 and self.latch_angle>.1:raise ActionRejected('PRECONDITION_FAILED')
            self.lid_angle=a['angle_rad'];self.stable=False;r=dict(a)
        elif capability=='latch.set_angle':
            if a['angle_rad']>.1 and self.lid_angle>1e-4:raise ActionRejected('PRECONDITION_FAILED')
            self.latch_angle=a['angle_rad']
            if self.latch_angle<1.4:
                self.stable=False
                if self.phase=='running':self.phase='cancelled'
            r=dict(a)
        elif capability=='sample.configure':
            if not a['matrix_id'].strip():raise ActionRejected('INVALID_ARGUMENT')
            row=self.templates.get(a['substance_id']);index=a['record_index']
            if self.lid_angle<1.4 or self.sample or not row or not row['runtime_eligible'] or index!=int(index) or index>=len(row['records']):raise ActionRejected('PRECONDITION_FAILED')
            record=row['records'][int(index)];self.sample=copy.deepcopy(a);self.sample['reference']=copy.deepcopy(record)
            self.sample_water=a['mass_kg']*record['water_mass_fraction'];self.sample_dry=a['mass_kg']-self.sample_water;self.input_kg+=a['mass_kg'];self.water_input+=self.sample_water;self.sample_k=a['initial_temperature_k'];self.indicated_aw=None;self.indicated_dew=None;self.stable=False;r={'configured':True}
        elif capability=='sample.unload':
            if self.lid_angle<1.4 or not self.sample:raise ActionRejected('PRECONDITION_FAILED')
            self.removed_kg+=self.sample_water+self.sample_dry;self.water_removed+=self.sample_water;self.sample=None;self.sample_water=0.;self.sample_dry=0.;self.indicated_aw=None;self.indicated_dew=None;self.stable=False;r={'unloaded':True}
        elif capability=='method.configure':
            n=a['consecutive_readings'];mode=a['mode'];delta=a['stability_delta']
            if n!=int(n) or (mode=='custom' and (n<2 or delta<.0005)) or (mode=='low_emitting' and delta>.003):raise ActionRejected('INVALID_ARGUMENT')
            self.mode=mode;self.delta=delta;self.temp_tolerance=a['temperature_tolerance_k'];self.consecutive=int(n);self.stable=False;r={'configured':True}
        elif capability=='calibration.configure':
            if not a['source'].strip():raise ActionRejected('INVALID_ARGUMENT')
            self.offset=a['offset_aw'];self.indicated_aw=None;self.stable=False;r={'offset_aw':self.offset}
        elif capability=='calibration.verify':
            if not self.powered or self.indicated_aw is None or not self.stable or self.warmup_s<900:raise ActionRejected('PRECONDITION_FAILED')
            delta=self.indicated_aw-a['reference_aw'];return {'deviation_aw':delta,'within_vendor_tolerance':abs(delta)<=.003}
        elif capability=='isotherm.configure':
            p=a['points']
            if not a['source'].strip() or not a['matrix_id'].strip() or any(b['water_activity']<=x['water_activity'] or b['moisture_dry_basis']<x['moisture_dry_basis'] for x,b in zip(p,p[1:])):raise ActionRejected('INVALID_ARGUMENT')
            self.isotherm=copy.deepcopy(a);r={'configured':True}
        elif capability=='acquisition.start':
            if not self.powered or not self.sample or self.lid_angle>1e-4 or self.latch_angle<1.4 or abs(self.target_k-self.sample['reference']['temperature_k'])>.050001:raise ActionRejected('PRECONDITION_FAILED')
            self._operation_count+=1;self.operation_id=f'{self.instance_id}:aw:{self._operation_count}';self.phase='running';self.elapsed_s=0.;self.history=[];self._stable_count=0;self.stable=False;self.indicated_aw=None;r={'phase':'running','operation_id':self.operation_id}
        elif capability=='acquisition.stop':
            if self.phase=='running':self.phase='cancelled'
            r={'phase':self.phase}
        else:return self._measure()
        self.revision+=1;return copy.deepcopy(r)
    def step(self,dt_s):
        if isinstance(dt_s,bool) or not isinstance(dt_s,(int,float)) or not math.isfinite(dt_s) or dt_s<=0:raise ValueError('finite positive dt_s required')
        left=float(dt_s)
        while left>1e-10:
            active=self.phase=='running';h=min(left,1.)
            if active:h=min(h,(math.floor(self.elapsed_s/10+1e-8)+1)*10-self.elapsed_s)
            if self.powered:self.warmup_s+=h
            target=self.target_k if self.powered else 298.15;self.block_k=target+(self.block_k-target)*math.exp(-h/25.);self.sample_k=self.block_k+(self.sample_k-self.block_k)*math.exp(-h/35.)
            closed=self.lid_angle<1e-4 and self.latch_angle>=1.4
            if self.sample and closed:
                aw=self.sample['reference']['value'];eq=aw*psat(self.sample_k)*20e-6*.01801528/(8.314462618*self.sample_k);transfer=(eq-self.vapor)*(1-math.exp(-h/self.sample['equilibration_tau_s']))
                transfer=max(-self.vapor,min(self.sample_water,transfer));self.sample_water-=transfer;self.vapor+=transfer
            elif not closed:
                eq=.5*psat(self.sample_k)*20e-6*.01801528/(8.314462618*self.sample_k);transfer=(eq-self.vapor)*(1-math.exp(-h/5.));self.vapor+=transfer
                if transfer>=0:self.ambient_in+=transfer
                else:self.ambient_out-=transfer
            if active:
                self.elapsed_s+=h;p=self.vapor*8.314462618*self.sample_k/(20e-6*.01801528);true_aw=p/psat(self.sample_k)
                indicated=round(true_aw+self.offset+(.005 if self.sample['volatile_interference'] else 0.),4);self.indicated_aw=indicated if .03<=indicated<=1 else None;self.indicated_dew=round(dewpoint(p)-273.15,2)+273.15
                if abs(self.elapsed_s/10-round(self.elapsed_s/10))<1e-7:
                    self.history.append((self.elapsed_s,indicated));self.history=self.history[-2:]
                    stable=self.elapsed_s>=60 and len(self.history)==2 and abs(self.history[-1][1]-self.history[-2][1])<=self.delta and abs(self.sample_k-self.block_k)<=self.temp_tolerance and self.indicated_aw is not None
                    self._stable_count=self._stable_count+1 if stable else 0;self.stable=self._stable_count>=self.consecutive
                    if self.stable and self.mode!='continuous':self.phase='succeeded'
            self.time_s+=h;left-=h
        self.revision+=1;return self.read('evaluator')
class Aqualab4teSession:
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
        meta=export_aqualab_4te_contract_metadata()
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
        return dict(operation_id=operation_id,status=h.phase,result=h._measure())

    def cancel(self,operation_id,request_id):
        self.poll(operation_id)
        response=self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)
        return dict(operation_id=operation_id,status=self._host.phase,result=response['result'])

def export_aqualab_4te_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'Aqualab4teSession.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key=='acquisition.start'; pre=[]
        conditions={
         'acquisition.start':('供电、闭盖锁紧、已装样，目标温度匹配实测记录',['power.enabled','lid.angle_rad','latch.angle_rad','sample.loaded','thermal.target_k'],'powered and lid<=1e-4 and latch>=1.4 and loaded and abs(target-reference_T)<=0.050001'),
         'sample.configure':('受信任宿主、开盖、空杯、记录可用',['lid.angle_rad','sample.loaded'],'lid>=1.4 and empty and eligible and integral record index'),
         'sample.unload':('受信任宿主、开盖且有样',['lid.angle_rad','sample.loaded'],'lid>=1.4 and loaded'),
         'lid.set_angle':('无采集且锁杆已释放才能开盖',['cycle.phase','latch.angle_rad'],'not running and (angle<=1e-4 or latch<=0.1)'),
         'latch.set_angle':('只有盖闭时可锁；释放取消采集',['lid.angle_rad'],'angle<=0.1 or lid<=1e-4'),
         'calibration.verify':('已供电预热15分钟并有平衡读数',['power.enabled','warmup.ready','equilibrium.stable'],'powered and warmup>=900 and stable and indicated is not None')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.aqualab_4te.Aqualab4teSession.invoke',invocation=f'session.invoke(instance_id="aqualab4te_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；取消保留热状态和水分转移；连续模式须显式停止' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电不回滚已经发生的水分转移和热过程。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    mechanism=dict(component_ids=['sample_cup','sample_block','chilled_mirror','thermopile','sample_peltier','mirror_peltier','chamber_fan'],
      state_ids=['thermal.block_k','thermal.sample_k','water.input_kg','water.sample_kg','water.headspace_kg','water.removed_kg','water.ambient_in_kg','water.ambient_out_kg'],
      meaning_zh='实测二元水溶液活度参考点驱动头空间传质、露点与温控代理，闭合水质量账本',implementation='lumped',
      equations=['aw=p_vapor/p_sat(T_sample)', 'p_sat=610.94*exp(17.625*T_C/(243.04+T_C)) Pa (Magnus engineering approximation)',
      'm_eq=aw_reference*p_sat*V_headspace*M_water/(R*T); dm=(m_eq-m_vapor)*(1-exp(-dt/tau))',
      'water_input+ambient_in=sample_water+headspace_water+removed_water+ambient_out',
      'open chamber vapor relaxes toward assumed ambient RH=0.5; direct open-sample evaporation omitted',
      'moisture=piecewise linear supplied product isotherm only at matching matrix/temperature, no extrapolation'],
      parameters={'headspace_volume':dict(value=20e-6,unit='m^3',meaning_zh='未知真实体积的传质代理',evidence='assumption',source_ids=['simulation_assumption'],uncertainty=None)},
      source_ids=['aw_templates','simulation_assumption'],limitations=['平衡时间、热滞后和Magnus露点均engineering_assumption，不保证厂家准确度。','传质中以原始溶液活度近似不变；未求组成变化后的活度及开盖样品蒸发。','未实现真实凝结液膜、光路/冷镜伺服、挥发物补偿、ISO18787或完整厂家算法。','没有实测产品吸附等温线时含水率不可用；公开水溶液平衡点不是产品吸附等温线。'])
    _ann.annotate_export('aqualab_4te',caps,slots,{'water_activity':mechanism},inputs={})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'water_activity':mechanism})

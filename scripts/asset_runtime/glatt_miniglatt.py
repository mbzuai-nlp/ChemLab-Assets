"""CHEM-061 Mini-Glatt2012: finite bed, solvent and explicitly uncalibrated granulation."""
from __future__ import annotations
import copy, importlib.util, json, math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SLUG='glatt_miniglatt'; ASSET_TYPE_ID='instrument.'+SLUG; CATALOG_ID='CHEM-061'; PROTOCOL_VERSION='1.0.0'
TEMPLATE_REL='assets/reactions/glatt_miniglatt_granulation_library/templates.json'
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








def arr(s,n):return dict(type='array',items=s,minItems=n,maxItems=n)
PHASE=dict(type='string',enum=['idle','running','succeeded','cancelled','failed'])
MODE=dict(type='string',enum=['drying','granulation','coating']);SPRAY=dict(type='string',enum=['top','bottom'])
PLANT=obj({'ambient_k':number('K',288.15,308.15),'pressure_pa':number('Pa',90000,110000),'power_factor':number('1',.1,1),'auxiliary_w':number('W',0,1000),'heater_w':number('W',1,3300),'bed_area_m2':number('m^2',.0001,.04),'base_heat_capacity_j_k':number('J/K',10,2000),'solid_cp_j_kg_k':number('J/(kg*K)',100,5000),'water_cp_j_kg_k':number('J/(kg*K)',1000,5000),'latent_j_kg':number('J/kg',1e6,3e6),'loss_w_k':number('W/K',0,10),'air_heat_efficiency':number('1',.01,1),'drying_rate_s_inv':number('1/s',0,1),'water_capacity_kg_per_normal_m3':number('kg/m^3',0,.2),'entrainment_s_inv':number('1/s',0,.05),'filter_resistance_pa_per_kg':number('Pa/kg',1,1e8),'filter_trip_pa':number('Pa',100,20000),'blowback_efficiency':number('1',0,1),'atomizing_normal_m3_s':number('m^3/s',0,20/3600),'pump_displacement_m3_rev':number('m^3',1e-9,1e-5),'accept_uncalibrated_model':BOOL})
PLANT_EXAMPLE=dict(ambient_k=298.15,pressure_pa=101325.,power_factor=.9,auxiliary_w=200.,heater_w=2500.,bed_area_m2=.00785,base_heat_capacity_j_k=200.,solid_cp_j_kg_k=1400.,water_cp_j_kg_k=4180.,latent_j_kg=2.4e6,loss_w_k=.4,air_heat_efficiency=.65,drying_rate_s_inv=.006,water_capacity_kg_per_normal_m3=.02,entrainment_s_inv=.0002,filter_resistance_pa_per_kg=100000.,filter_trip_pa=3000.,blowback_efficiency=.85,atomizing_normal_m3_s=3/3600,pump_displacement_m3_rev=1e-6,accept_uncalibrated_model=True)
POWDER=obj({'source_tag':TEXT,'dry_mass_kg':number('kg',.0025,.375),'water_mass_kg':number('kg',0,.2),'particle_density_kg_m3':number('kg/m^3',200,5000),'seed_diameter_m':number('m',5e-6,.003),'voidage':number('1',.2,.9),'sphericity':number('1',.2,1),'aggregation_s_inv':number('1/s',0,1),'breakage_s_inv':number('1/s',0,1),'accept_host_material':BOOL})
POWDER_EXAMPLE=dict(source_tag='synthetic_mcc_equivalent',dry_mass_kg=.08,water_mass_kg=.004,particle_density_kg_m3=1500.,seed_diameter_m=100e-6,voidage=.5,sphericity=.8,aggregation_s_inv=.015,breakage_s_inv=.00005,accept_host_material=True)
LIQUID=obj({'source_tag':TEXT,'volume_m3':number('m^3',1e-9,1e-3),'density_kg_m3':number('kg/m^3',800,1500),'binder_mass_fraction':number('1',0,.5),'accept_water_only':BOOL})
LIQUID_EXAMPLE=dict(source_tag='synthetic_aqueous_binder',volume_m3=30e-6,density_kg_m3=1000.,binder_mass_fraction=.06,accept_water_only=True)
METHOD=obj({'operation':MODE,'spray_position':SPRAY,'air_normal_m3_s':number('m^3/s',0,100/3600),'temperature_k':number('K',288.15,353.15),'spray_m3_s':number('m^3/s',0,100e-6/60),'duration_s':number('s',.02,14400),'spray_duration_s':number('s',0,14400)})
METHOD_EXAMPLE=dict(operation='granulation',spray_position='top',air_normal_m3_s=12/3600,temperature_k=333.15,spray_m3_s=3.5e-6/60,duration_s=60.,spray_duration_s=45.)
MEASURE=obj({'phase':PHASE,'elapsed_s':number('s',0),'air_normal_m3_s':number('m^3/s',0),'inlet_k':number('K',1,400),'bed_k':number('K',1,400),'spray_m3_s':number('m^3/s',0),'bed_dp_pa':number('Pa',0),'filter_dp_pa':number('Pa',0),'fluidized':BOOL,'mean_diameter_m':number('m',0),'evidence':TEXT,'size_calibrated':BOOL})
LOCS=['initial','tank','bed','filter','vapor','collected'];SPECIES=['core','binder','water']
PF={loc+'_kg':arr(number('kg',0),3) for loc in LOCS}
PF.update({k:number('J') for k in ['heater_input_j','exhaust_sensible_j','loss_j','latent_j','vapor_sensible_j','bed_sensible_j','filter_sensible_j','collected_sensible_j']})
PF.update(bed_particle_count=number('1',0),filter_particle_count=number('1',0),collected_particle_count=number('1',0),air_normal_used_m3=number('m^3',0),evidence=TEXT,template_source=TEXT)
PRODUCT=obj(PF);CAPS={}
def cap(k,i,o,c,z,kind='device_command',vis='agent',ex=None,effects=()):CAPS[k]=(i,o,c,z,kind,vis,ex or {},list(effects))
cap('plant.configure',PLANT,obj({'configured':BOOL}),['body'],'显式宿主床/热/过滤数值参数，校验3.3kVA预算','configuration','evaluator',PLANT_EXAMPLE)
cap('kit.configure',obj({'micro':BOOL,'micro_maximum_kg':number('kg',.0025,.375),'accept_host_attachment':BOOL}),obj({'configured':BOOL}),['process_cup','wurster_insert'],'Micro仅底喷、2.5g起点，未知上限由宿主确认','configuration','evaluator',{'micro':False,'micro_maximum_kg':.025,'accept_host_attachment':True})
cap('powder.load',POWDER,obj({'loaded':BOOL}),['process_cup'],'有限粉体；加载一次材料属性不重释旧账本','configuration','evaluator',POWDER_EXAMPLE)
cap('liquid.load',LIQUID,obj({'loaded':BOOL}),['liquid_reservoir'],'有限水基溶液，宿主确认相容性，无有机溶剂安全模型','configuration','evaluator',LIQUID_EXAMPLE)
cap('product.collect',obj({'recipient_id':TEXT}),obj({'recipient_id':TEXT,'attachment_event':TEXT}),['process_cup'],'开塔停气取料，保留滤芯残留与显热','device_command','evaluator',{'recipient_id':'granules_01'})
cap('method.configure',METHOD,obj({'configured':BOOL}),['control_panel'],'干燥/团聚/包衣、顶/底喷及有限持续时间','configuration','agent',METHOD_EXAMPLE)
cap('power.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['mains_switch'],'掉电停止泵/热/气，保留部分产物',ex={'enabled':True})
cap('tower.set',obj({'opened':BOOL}),obj({'opened':BOOL}),['tower_cap','process_cup','clamp_top','clamp_mid','clamp_bottom'],'宿主拆装代理，开塔取消过程并停气热',ex={'opened':False})
cap('air.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['air_inlet'],'气体使能，关闭则中止运行',ex={'enabled':True})
cap('heating.set',obj({'enabled':BOOL}),obj({'enabled':BOOL}),['heater'],'气流/电源存在才加热，目标写入不直接改床温',ex={'enabled':True})
for k,c in [('air','button_air'),('heating','button_heat')]:cap(k+'.toggle',obj({}),obj({'enabled':BOOL}),[c],'照片图标推定面板开关；映射为模拟演示而非固件证据')
cap('fluidization.set_flow',obj({'flow_normal_m3_s':number('m^3/s',0,100/3600)}),obj({'flow_normal_m3_s':number('m^3/s',0,100/3600)}),['air_dial'],'床流量正常态，额外雾化/反吹另计',ex={'flow_normal_m3_s':12/3600})
cap('thermal.set_temperature',obj({'temperature_k':number('K',288.15,353.15)}),obj({'temperature_k':number('K',288.15,353.15)}),['heater'],'进气设定最高80℃，实际受未知加热功率限制',ex={'temperature_k':333.15})
cap('spray.set_rate',obj({'flow_m3_s':number('m^3/s',0,100e-6/60)}),obj({'flow_m3_s':number('m^3/s',0,100e-6/60)}),['spray_dial'],'可选泵最大100mL/min，剩余有限料和流化条件共同约束',ex={'flow_m3_s':3.5e-6/60})
cap('acquisition.start',obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['pump_rotor','process_cup'],'异步喷雾阶段及后干燥；完成不意味着合格PSD')
cap('acquisition.stop',obj({}),obj({'phase':PHASE}),['control_panel'],'立即停止喷液/气/热；不回滚已转移物料')
cap('filter.clean',obj({}),obj({'scheduled':BOOL}),['filter_cartridges','blowback_port','button_filter'],'停床气后定时反吹；必须由step推进，不即刻清空滤饼')
cap('granulation.measure',obj({}),MEASURE,['control_panel'],'理想过程显示，d30为合成等效粒径非内置实测粒度','query')
cap('product.read',obj({}),PRODUCT,['process_cup','filter_cartridges'],'私有三物种/热量账本','query','evaluator')
cap('reference.lookup',obj({'substance_id':TEXT}),obj({'substance_id':TEXT,'mass_fraction':arr(number('1',0,1),7),'finite_upper_edges_m':arr(number('m',0),6),'last_bin_open':BOOL,'median_interval_m':arr(number('m',0),2),'evidence':TEXT,'source':TEXT,'template_source':TEXT,'conditions':TEXT}),['control_panel'],'离线真实筛分分率，API再分散DLS不当颗粒PSD','query','agent',{'substance_id':'mutylo_g1'})
SLOTS={};SLOT_ZH={}
def slot(k,s,u,z,o='derived',v='agent',c='body',w=None):SLOTS[k]=(s,u,o,v,[c],w);SLOT_ZH[k]=z
for k in ['plant','method']:slot(k+'.configured',BOOL,'1',k+'已配置','setpoint')
for k,c in [('power','mains_switch'),('air','air_inlet'),('heating','heater')]:slot(k+'.enabled',BOOL,'1',k+'使能','setpoint',c=c,w=(k+'.set','enabled'))
slot('tower.opened',BOOL,'1','宿主拆装状态','scene',c='tower_cap',w=('tower.set','opened'))
slot('tower.cap_lift_m',number('m',0,.12),'m','塔盖抬升代理','scene',c='tower_cap')
slot('tower.cup_shift_m',number('m',0,.16),'m','取杯水平代理','scene',c='process_cup')
slot('tower.clamp_angle_rad',number('rad',0,1.2),'rad','三夹箍解锁代理','scene',c='clamp_top')
slot('mains.angle_rad',number('rad',0,math.pi/2),'rad','主电源旋转90度代理','scene',c='mains_switch')
slot('air.dial_rad',number('rad',0,2.5),'rad','过程流量映射转角','scene',c='air_dial')
slot('spray.dial_rad',number('rad',0,2.5),'rad','喷量映射转角','scene',c='spray_dial')
slot('pump.angle_rad',number('rad',0,2*math.pi),'rad','每转排量宿主假设的累计泵转角','scene',c='pump_rotor')
slot('kit.micro',BOOL,'1','MicroKit安装标志','scene',c='wurster_insert')
slot('method.operation',MODE,'1','干燥/团聚/包衣','setpoint')
slot('method.spray_position',SPRAY,'1','顶喷或底喷','setpoint')
slot('flow.target_normal_m3_s',number('m^3/s',0,100/3600),'m^3/s','床气正常态设定','setpoint',c='air_dial',w=('fluidization.set_flow','flow_normal_m3_s'))
slot('flow.actual_normal_m3_s',number('m^3/s',0,100/3600),'m^3/s','实际床气正常态；不含雾化/反吹')
slot('flow.utility_normal_m3_s',number('m^3/s',0,100/3600),'m^3/s','压缩空气总预算，含床气/雾化/反吹')
slot('thermal.target_k',number('K',288.15,353.15),'K','进气温度目标','setpoint',c='heater',w=('thermal.set_temperature','temperature_k'))
slot('thermal.inlet_k',number('K',1,400),'K','功率受限进气理想示值')
slot('thermal.bed_k',number('K',1,400),'K','集总粉床温度理想示值')
slot('spray.target_m3_s',number('m^3/s',0,100e-6/60),'m^3/s','液体实际体积设定','setpoint',c='spray_dial',w=('spray.set_rate','flow_m3_s'))
slot('spray.actual_m3_s',number('m^3/s',0,100e-6/60),'m^3/s','本步实际体积喷液率')
slot('bed.fluidized',BOOL,'1','Ergun等效床判断；非厂家安全认证')
slot('bed.pressure_drop_pa',number('Pa',0),'Pa','床两端差压，不是绝压')
slot('filter.pressure_drop_pa',number('Pa',0),'Pa','宿主滤饼阻力代理差压')
slot('bed.mean_diameter_m',number('m',0),'m','干固体质量/粒数推得d30，不是d50或实测在线PSD')
slot('filter.pulse_remaining_s',number('s',0,.5),'s','反吹剩余时间')
slot('cycle.phase',PHASE,'1','异步运行状态')
slot('cycle.elapsed_s',number('s',0),'s','当前运行累计时间')
slot('safety.warning',TEXT,'1','明确模拟警报，不替代厂家安全系统')
slot('truth.ledger',PRODUCT,'1','私有core/binder/water与显热/汽化/空气账本','process_truth','evaluator','process_cup')
for k in CAPS:
    if CAPS[k][4]!='query':CAPS[k][7].extend(SLOTS)

def _reference_notes(schema):
    for key,value in schema.get('properties',{}).items():
        if 'normal_m3' in key:value['x-reference-conditions']='Gas volume at273.15K and101325Pa, explicit simulation assumption; manufacturer N reference unspecified.'
        if key=='pressure_pa':value['x-reference-conditions']='Absolute bed gas pressure, not gauge or differential.'
        _reference_notes(value)
    if 'items' in schema:_reference_notes(schema['items'])
for item in CAPS.values():_reference_notes(item[0]);_reference_notes(item[1])
class GlattMiniglattRuntime:
    def __init__(self,instance_id='miniglatt_01'):
        self.instance_id=instance_id;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']}
        self.time_s=0.;self.revision=0;self.plant=None;self.powder=None;self.liquid=None;self.method=None;self.micro=False;self.micro_max=.025
        self.mass={loc:[0.,0.,0.] for loc in LOCS};self.particles={loc:0. for loc in ['bed','filter','collected']};self.n_initial=0.
        self.sensible={'bed':0.,'filter':0.,'collected':0.};self.heater_input=0.;self.exhaust=0.;self.loss=0.;self.latent=0.;self.vapor_sensible=0.;self.air_used=0.
        self.powered=False;self.air=False;self.heating=False;self.opened=True;self.pulse=0.;self.angle=0.;self.actual_spray=0.;self.elapsed_s=0.;self.phase='idle';self.warning=''
        self.operation_id='';self._operation_count=0;self.log=[];self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
    def register_principal(self,principal_id,visibility='agent'):
        if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id='__public_agent__'):
        if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return GlattMiniglattSession(self,principal_id)
    def describe(self):return self.session().describe(instance_id=self.instance_id)
    def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k!='templates'})
    def _capacity(self):
        if not self.plant:return 1.
        p=self.plant;m=self.mass['bed'];return p['base_heat_capacity_j_k']+(m[0]+m[1])*p['solid_cp_j_kg_k']+m[2]*p['water_cp_j_kg_k']
    def _temperature(self):return (self.plant['ambient_k'] if self.plant else 298.15)+self.sensible['bed']/self._capacity()
    def _diameter(self):
        return (6*sum(self.mass['bed'][:2])/(math.pi*self.powder['particle_density_kg_m3']*self.particles['bed']))**(1/3) if self.powder and self.particles['bed']>1e-20 else 0.
    def _gas(self):
        ta=self.plant['ambient_k'] if self.plant else 298.15;q=self.method['air_normal_m3_s'] if self.method and self.air and self.powered and not self.opened and self.pulse==0 else 0.
        if not self.plant:return q,ta,0.
        mdot=q*1.293;power=min(self.plant['heater_w'],mdot*1005*max(0.,self.method['temperature_k']-ta)) if mdot and self.heating else 0.
        return q,ta+power/(mdot*1005) if mdot else ta,power
    def _hydraulics(self):
        q,tin,_=self._gas()
        if not self.plant or not self.powder or not sum(self.mass['bed']):return dict(u=0.,umf=0.,fluidized=False,dp=0.,filter_dp=0.)
        p=self.plant;mat=self.powder;rho=1.293*273.15/tin*p['pressure_pa']/101325.;mu=1.81e-5*(tin/293.15)**.7;u=q*tin/273.15*101325./p['pressure_pa']/p['bed_area_m2']
        d=max(self._diameter(),1e-9)*mat['sphericity'];eps=mat['voidage'];a=150*(1-eps)*mu/(eps**3*d*d);b=1.75*rho/(eps**3*d);rhs=(mat['particle_density_kg_m3']-rho)*9.80665
        umf=2*rhs/(a+math.sqrt(a*a+4*b*rhs));height=sum(self.mass['bed'])/(mat['particle_density_kg_m3']*(1-eps)*p['bed_area_m2'])
        packed=height*(1-eps)*(a*u+b*u*u);weight=sum(self.mass['bed'])*9.80665*(1-rho/mat['particle_density_kg_m3'])/p['bed_area_m2']
        return dict(u=u,umf=umf,fluidized=u>=umf and q>0,dp=min(packed,weight),filter_dp=sum(self.mass['filter'][:2])*p['filter_resistance_pa_per_kg'])
    def _utility(self):
        q,_,_=self._gas()
        if self.pulse>0:return 100/3600 if self.powered and not self.opened else 0.
        atom=self.plant['atomizing_normal_m3_s'] if self.plant and self.phase=='running' and self.method['operation']!='drying' and self.elapsed_s<self.method['spray_duration_s']-1e-10 and self.method['spray_m3_s']>0 and sum(self.mass['tank'])>1e-15 else 0.
        return q+atom
    def _product(self):
        p={loc+'_kg':list(v) for loc,v in self.mass.items()}
        p.update(heater_input_j=self.heater_input,exhaust_sensible_j=self.exhaust,loss_j=self.loss,latent_j=self.latent,vapor_sensible_j=self.vapor_sensible,**{k+'_sensible_j':v for k,v in self.sensible.items()},**{k+'_particle_count':v for k,v in self.particles.items()},air_normal_used_m3=self.air_used,evidence='synthetic_conservative_lumped_moment_model_not_calibrated_PSD',template_source=TEMPLATE_REL)
        return p
    def read(self,visibility='agent'):
        if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
        q,tin,_=self._gas();hyd=self._hydraulics();m=self.method or METHOD_EXAMPLE
        v={'plant.configured':self.plant is not None,'method.configured':self.method is not None,'power.enabled':self.powered,'air.enabled':self.air,'heating.enabled':self.heating,'tower.opened':self.opened,'tower.cap_lift_m':.12 if self.opened else 0.,'tower.cup_shift_m':.16 if self.opened else 0.,'tower.clamp_angle_rad':1.2 if self.opened else 0.,'mains.angle_rad':math.pi/2 if self.powered else 0.,'air.dial_rad':m['air_normal_m3_s']/(100/3600)*2.5,'spray.dial_rad':m['spray_m3_s']/(100e-6/60)*2.5,'pump.angle_rad':self.angle,'kit.micro':self.micro,'method.operation':m['operation'],'method.spray_position':m['spray_position'],'flow.target_normal_m3_s':m['air_normal_m3_s'],'flow.actual_normal_m3_s':q,'flow.utility_normal_m3_s':self._utility(),'thermal.target_k':m['temperature_k'],'thermal.inlet_k':tin,'thermal.bed_k':self._temperature(),'spray.target_m3_s':m['spray_m3_s'],'spray.actual_m3_s':self.actual_spray,'bed.fluidized':hyd['fluidized'],'bed.pressure_drop_pa':hyd['dp'],'filter.pressure_drop_pa':hyd['filter_dp'],'bed.mean_diameter_m':self._diameter(),'filter.pulse_remaining_s':self.pulse,'cycle.phase':self.phase,'cycle.elapsed_s':self.elapsed_s,'safety.warning':self.warning,'truth.ledger':self._product()}
        return copy.deepcopy({k:x for k,x in v.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
    def invoke(self,capability_id,**a):
        if capability_id not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(CAPS[capability_id][0],a);old=self.debug_snapshot()
        try:
            result=self._invoke(capability_id,a);validate(CAPS[capability_id][1],result)
            if CAPS[capability_id][4]!='query':self.revision+=1;self.log.append(dict(time_s=self.time_s,event=capability_id,phase=self.phase))
            return copy.deepcopy(result)
        except Exception:
            templates=self.templates;self.__dict__.clear();self.__dict__.update(old);self.templates=templates;raise
    def _stop(self,phase='cancelled'):
        if self.phase=='running':self.phase=phase
        self.air=False;self.heating=False;self.actual_spray=0.;self.pulse=0.
    def _access(self):
        if self.phase=='running' or self.pulse>0:raise ActionRejected('BUSY')
        if not self.opened or self.air:raise ActionRejected('PRECONDITION_FAILED')
    def _check_method(self,a):
        if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
        if self.micro and a['spray_position']!='bottom':raise ActionRejected('MICRO_BOTTOM_ONLY')
        if a['spray_duration_s']>a['duration_s'] or a['temperature_k']<self.plant['ambient_k']:raise ActionRejected('INVALID_ARGUMENT')
        if a['operation']=='drying' and (a['spray_m3_s']>0 or a['spray_duration_s']>0):raise ActionRejected('DRYING_NO_SPRAY')
        atom=self.plant['atomizing_normal_m3_s'] if a['operation']!='drying' and a['spray_m3_s'] else 0.
        if a['air_normal_m3_s']+atom>100/3600+1e-15:raise ActionRejected('TOTAL_AIR_BUDGET')
    def _invoke(self,k,a):
        if k=='granulation.measure':
            r=self.read();return dict(phase=self.phase,elapsed_s=self.elapsed_s,air_normal_m3_s=r['flow.actual_normal_m3_s'],inlet_k=r['thermal.inlet_k'],bed_k=r['thermal.bed_k'],spray_m3_s=self.actual_spray,bed_dp_pa=r['bed.pressure_drop_pa'],filter_dp_pa=r['filter.pressure_drop_pa'],fluidized=r['bed.fluidized'],mean_diameter_m=r['bed.mean_diameter_m'],evidence='synthetic_ideal_indicators_not_measured_PSD',size_calibrated=False)
        if k=='product.read':return self._product()
        if k=='reference.lookup':
            if a['substance_id'] not in self.templates:raise ActionRejected('UNKNOWN_REFERENCE')
            t=self.templates[a['substance_id']]
            return dict(substance_id=t['substance_id'],mass_fraction=[b['mass_fraction'] for b in t['values']['sieve_bins']],finite_upper_edges_m=[90e-6,125e-6,180e-6,250e-6,355e-6,500e-6],last_bin_open=True,median_interval_m=t['values']['median_interval_m'],evidence=t['evidence'],source=t['source']['paper_doi'],template_source=TEMPLATE_REL,conditions=json.dumps(t['conditions'],ensure_ascii=False))
        if k=='plant.configure':
            if self.phase=='running' or sum(self.mass['initial']) or self.plant is not None or not a['accept_uncalibrated_model']:raise ActionRejected('PRECONDITION_FAILED')
            if a['heater_w']+a['auxiliary_w']>3300*a['power_factor']:raise ActionRejected('APPARENT_POWER_BUDGET')
            self.plant=copy.deepcopy(a);return dict(configured=True)
        if k=='kit.configure':
            self._access()
            if sum(self.mass['bed']) or sum(self.mass['filter']) or not a['accept_host_attachment']:raise ActionRejected('PRECONDITION_FAILED')
            if a['micro'] and self.method and self.method['spray_position']!='bottom':raise ActionRejected('MICRO_BOTTOM_ONLY')
            self.micro=a['micro'];self.micro_max=a['micro_maximum_kg'];return dict(configured=True)
        if k=='powder.load':
            self._access()
            if not self.plant or self.powder or not a['accept_host_material'] or not a['source_tag']:raise ActionRejected('PRECONDITION_FAILED')
            lo,hi=(.0025,self.micro_max) if self.micro else (.025,.375)
            if not lo<=a['dry_mass_kg']<=hi:raise ActionRejected('BATCH_RANGE')
            self.powder=copy.deepcopy(a);self.mass['bed']=[a['dry_mass_kg'],0.,a['water_mass_kg']];self.mass['initial']=[a['dry_mass_kg'],0.,a['water_mass_kg']]
            self.n_initial=a['dry_mass_kg']/(a['particle_density_kg_m3']*math.pi*a['seed_diameter_m']**3/6);self.particles['bed']=self.n_initial;return dict(loaded=True)
        if k=='liquid.load':
            self._access()
            if not self.powder or self.liquid or not a['accept_water_only'] or not a['source_tag']:raise ActionRejected('PRECONDITION_FAILED')
            self.liquid=copy.deepcopy(a);m=a['volume_m3']*a['density_kg_m3'];self.mass['tank']=[0.,m*a['binder_mass_fraction'],m*(1-a['binder_mass_fraction'])]
            for i in range(3):self.mass['initial'][i]+=self.mass['tank'][i]
            return dict(loaded=True)
        if k=='product.collect':
            self._access()
            if not a['recipient_id'] or not sum(self.mass['bed']):raise ActionRejected('PRECONDITION_FAILED')
            cp=self._capacity();solid_cp=cp-self.plant['base_heat_capacity_j_k'];energy=self.sensible['bed']*solid_cp/cp
            self.sensible['bed']-=energy;self.sensible['collected']+=energy
            for i in range(3):self.mass['collected'][i]+=self.mass['bed'][i];self.mass['bed'][i]=0.
            self.particles['collected']+=self.particles['bed'];self.particles['bed']=0.
            return dict(recipient_id=a['recipient_id'],attachment_event='host.release_granules_not_filter_residue')
        if k=='power.set':
            self.powered=a['enabled']
            if not self.powered:self._stop()
            return dict(enabled=self.powered)
        if k=='tower.set':
            if a['opened']:self._stop()
            self.opened=a['opened'];return dict(opened=self.opened)
        if k in ['air.toggle','heating.toggle']:
            field='air' if k=='air.toggle' else 'heating';return self._invoke(field+'.set',{'enabled':not getattr(self,field)})
        if k=='air.set':
            if a['enabled'] and (not self.powered or self.opened or not self.plant or not self.method or self.pulse>0):raise ActionRejected('PRECONDITION_FAILED')
            self.air=a['enabled']
            if not self.air:self._stop()
            return dict(enabled=self.air)
        if k=='heating.set':
            if a['enabled'] and (not self.powered or not self.air or self.opened):raise ActionRejected('PRECONDITION_FAILED')
            self.heating=a['enabled'];return dict(enabled=self.heating)
        if k=='method.configure':
            if self.phase=='running' or self.pulse>0:raise ActionRejected('BUSY')
            self._check_method(a);self.method=copy.deepcopy(a);return dict(configured=True)
        for cid,arg,field in [('fluidization.set_flow','flow_normal_m3_s','air_normal_m3_s'),('thermal.set_temperature','temperature_k','temperature_k'),('spray.set_rate','flow_m3_s','spray_m3_s')]:
            if k==cid:
                if not self.method:raise ActionRejected('PRECONDITION_FAILED')
                new=dict(self.method,**{field:a[arg]});self._check_method(new);self.method=new;return {arg:a[arg]}
        if k=='acquisition.stop':self._stop();return dict(phase=self.phase)
        if k=='filter.clean':
            if self.phase=='running' or self.air or self.opened or not self.powered or not self.plant or self.pulse>0:raise ActionRejected('PRECONDITION_FAILED')
            self.pulse=.5;self.warning='';return dict(scheduled=True)
        if k=='acquisition.start':
            if self.phase=='running' or self.pulse>0:raise ActionRejected('BUSY')
            if not self.powered or self.opened or not self.plant or not self.powder or not self.method or not sum(self.mass['bed']):raise ActionRejected('PRECONDITION_FAILED')
            self._check_method(self.method);self.air=True
            if not self._hydraulics()['fluidized']:raise ActionRejected('NOT_FLUIDIZED')
            if self._hydraulics()['filter_dp']>=self.plant['filter_trip_pa']:raise ActionRejected('FILTER_CLOGGED')
            if self.method['operation']!='drying' and self.method['spray_m3_s']>0 and sum(self.mass['tank'])<=1e-15:raise ActionRejected('LIQUID_EMPTY')
            self.heating=True;self.phase='running';self.elapsed_s=0.;self.warning='';self._operation_count+=1;self.operation_id=self.instance_id+':op:'+str(self._operation_count)
            return dict(phase=self.phase,operation_id=self.operation_id)
        raise ActionRejected('UNKNOWN_CAPABILITY')
    def _tick(self,dt):
        p=self.plant;ta=p['ambient_k'];self.actual_spray=0.
        # Blowback is finite duration, isolated from process/atomizing gas. Return dry captured solids and their sensible energy.
        utility=self._utility()
        if self.pulse>0:
            frac=1-(1-p['blowback_efficiency'])**(dt/.5)
            for i in range(3):m=self.mass['filter'][i]*frac;self.mass['filter'][i]-=m;self.mass['bed'][i]+=m
            n=self.particles['filter']*frac;self.particles['filter']-=n;self.particles['bed']+=n
            en=self.sensible['filter']*frac;self.sensible['filter']-=en;self.sensible['bed']+=en
            self.pulse=max(0.,self.pulse-dt)
        q,tin,power=self._gas();hyd=self._hydraulics()
        if self.phase=='running':
            if not hyd['fluidized'] or hyd['filter_dp']>=p['filter_trip_pa']:
                self.warning='NOT_FLUIDIZED' if not hyd['fluidized'] else 'FILTER_CLOGGED';self._stop('failed');q=0.;power=0.;utility=0.
            else:
                if self.elapsed_s<self.method['spray_duration_s']-1e-10 and self.method['operation']!='drying' and self.liquid:
                    dm=min(sum(self.mass['tank']),self.method['spray_m3_s']*self.liquid['density_kg_m3']*dt);tank=sum(self.mass['tank'])
                    if tank:
                        for i in range(3):m=dm*self.mass['tank'][i]/tank;self.mass['tank'][i]-=m;self.mass['bed'][i]+=m
                    dv=dm/self.liquid['density_kg_m3'];self.actual_spray=dv/dt;self.angle=(self.angle+dv/p['pump_displacement_m3_rev']*2*math.pi)%(2*math.pi)
                    if tank<=dm+1e-15:self.warning='LIQUID_EXHAUSTED_DRYING_CONTINUES'
                if self.method['operation']=='granulation':
                    wet=self.mass['bed'][2]/max(sum(self.mass['bed']),1e-15);n=self.particles['bed']
                    agg=self.powder['aggregation_s_inv']*wet*n/max(self.n_initial,1.);br=self.powder['breakage_s_inv']*max(0.,hyd['u']/hyd['umf']-1.)
                    self.particles['bed']=max(self.n_initial*1e-8,min(self.n_initial*100,n*math.exp(max(-20.,min(20.,(br-agg)*dt)))))
                self.elapsed_s+=dt
        # Convective sensible energy, air exhaust, latent loss and all solid/water inventories are closed.
        temp=self._temperature();qc=q*1.293*1005*p['air_heat_efficiency']*(tin-temp)
        loss=p['loss_w_k']*(temp-ta);available=max(0.,self.sensible['bed']+(qc-loss)*dt)
        evap=min(self.mass['bed'][2],self.mass['bed'][2]*(1-math.exp(-p['drying_rate_s_inv']*dt)) if q and hyd['fluidized'] else 0.,p['water_capacity_kg_per_normal_m3']*q*dt,available/(p['latent_j_kg']+p['water_cp_j_kg_k']*max(0.,temp-ta)))
        vapor_heat=evap*p['water_cp_j_kg_k']*max(0.,temp-ta);self.vapor_sensible+=vapor_heat
        self.sensible['bed']+=(qc-loss)*dt-evap*p['latent_j_kg']-vapor_heat;self.mass['bed'][2]-=evap;self.mass['vapor'][2]+=evap
        self.heater_input+=power*dt;self.exhaust+=(power-qc)*dt;self.loss+=loss*dt;self.latent+=evap*p['latent_j_kg'];self.air_used+=utility*dt
        if q and hyd['fluidized']:
            frac=1-math.exp(-p['entrainment_s_inv']*dt);dry=sum(self.mass['bed'][:2]);energy=dry*frac*p['solid_cp_j_kg_k']*(self._temperature()-ta)
            for i in [0,1]:m=self.mass['bed'][i]*frac;self.mass['bed'][i]-=m;self.mass['filter'][i]+=m
            self.sensible['bed']-=energy;self.sensible['filter']+=energy;n=self.particles['bed']*frac;self.particles['bed']-=n;self.particles['filter']+=n
        if self.phase=='running' and self.elapsed_s>=self.method['duration_s']-1e-10:self.elapsed_s=self.method['duration_s'];self._stop('succeeded')
    def step(self,dt_s):
        validate(number('s',0,86400),dt_s)
        if dt_s==0:return
        left=dt_s
        while left>1e-12:
            dt=min(.02,left)
            if self.pulse>1e-12:dt=min(dt,self.pulse)
            if self.phase=='running':
                dt=min(dt,self.method['duration_s']-self.elapsed_s)
                boundary=self.method['spray_duration_s']-self.elapsed_s
                if boundary>1e-10:dt=min(dt,boundary)
            if dt<=1e-12:self._stop('succeeded');continue
            if self.plant:self._tick(dt)
            self.time_s+=dt;left-=dt
        self.revision+=1
class GlattMiniglattSession:
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
        m=export_glatt_miniglatt_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version='1.0.0',capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
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
        return dict(operation_id=operation_id,status=h.phase,result=h.invoke('granulation.measure'))
    def cancel(self,operation_id,request_id):
        self.poll(operation_id);return self.invoke(instance_id=self._host.instance_id,capability_id='acquisition.stop',arguments={},request_id=request_id)





def export_glatt_miniglatt_contract_metadata():
    caps={};slots={}
    for k,(s,u,o,v,c,w) in SLOTS.items():
        slots[k]=dict(meaning_zh=SLOT_ZH[k],component_ids=c,value_schema=copy.deepcopy(s),unit=u,reference_conditions=None,origin=o,visibility=v,access='read_write' if w else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'GlattMiniglattSession.read(state_ids=[{k!r}])'),write_via=dict(capability_id=w[0],argument=w[1],fixed_arguments={}) if w else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
    evolving=[k for k,v in SLOTS.items() if v[2] in ['scene','derived','process_truth']]
    for k,(inp,out,c,z,kind,vis,ex,effects) in CAPS.items():
        asynchronous=k in ['acquisition.start'];query=kind=='query'
        caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.glatt_miniglatt.GlattMiniglattSession.invoke',invocation=f'session.invoke(instance_id="miniglatt_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),
          preconditions=[] if query else [dict(description_zh='电源、拆装、有限粉液、气/功率预算及宿主身份检查',state_ids=['power.enabled','tower.opened','air.enabled','cycle.phase'],enforced_by='runtime',check='Runtime.invoke atomic guards; Session trusted-principal filtering')],
          state_effects=[] if query else [dict(state_id=s,timing='immediate',description_zh='设定/附着事件/有限装料变化') for s in effects]+[dict(state_id=s,timing='on_step',description_zh='宿主唯一时钟演化') for s in evolving],
          observes=[],model_effects=dict(mode='none',description_zh='模型实际求值证据尚待独立核验',binding_ids=[]),
          execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',completion_criterion='指定时间已走完并停气/热/喷雾；不等于合格粒径/含湿量' if asynchronous else '事务返回；物理量由宿主step演化',
            completion_state_ids=['cycle.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',retry='same_request_id',resources=['host_clock','powder','liquid','filter','request_cache'],
            partial_failure_zh='取消保留床/滤芯/水汽/溶液，不能回滚物质；仅进程内请求去重。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','PRECONDITION_FAILED','BUSY','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','BATCH_RANGE','MICRO_BOTTOM_ONLY','TOTAL_AIR_BUDGET','APPARENT_POWER_BUDGET','DRYING_NO_SPRAY','NOT_FLUIDIZED','FILTER_CLOGGED','LIQUID_EMPTY']],
          example_arguments=copy.deepcopy(ex),verification=[],limitations=['有限三物种、热量、粒数矩模型；非厂家固件/标定/配方建议'],source_ids=['manufacturer_primary','simulation_assumption'])

    mech=dict(component_ids=['process_cup','filter_cartridges','heater','pump_rotor','wurster_insert'],state_ids=list(slots),meaning_zh='固定等效面积气固床、有限粉体/水基喷液、滤芯反吹、热量及零阶粒数矩',implementation='lumped',
      equations=['Normal gas reference273.15K/101325Pa explicitly assumed; Qactual=QN*Tin/TN*PN/P; total utility=fluidizing+atomizing OR isolated100Nm3/h blowback',
      'Ergun: a=150(1-eps)mu/(eps^3*(phi*d)^2), b=1.75rho_g/(eps^3*phi*d); umf=2(rho_p-rho_g)g/(a+sqrt(a^2+4b(rho_p-rho_g)g)); dP=min(packed,buoyant_bed_weight/A)',
      '0<=heater_W+auxiliary_W<=3300VA*host_power_factor; inlet_T=Ta+min(heater_W,mdot*cp*(Tset-Ta))/(mdot*cp)',
      'core/binder/water: initial=tank+bed+filter+vapor+collected; finite pump=min(Q*rho*dt,tankmass); displacement integrates actual pumped volume only',
      'bed_E=C(m)*(T-Ta); Qconv=mdot*cp*eta*(Tin-T); evaporation=min(water,water*(1-exp(-kdt)),host_capacity*QNdt,availableE/L)',
      'heater_input=exhaust_sensible+ambient_loss+vapor_latent+vapor_sensible+bed_sensible+filter_sensible+collected_sensible',
      'dN/dt=N*(kbreak*max(u/umf-1,0)-kagg*wet_fraction*N/N0) in granulation; coating/drying conserve N except physical transfer; mean_d30=(6Msolid/(pi*rho*N))^(1/3)',
      'dry_filter_capture=bed_dry*(1-exp(-entrainment_rate*dt)); particle_count and sensible heat transfer with dry mass; 0.5s blowback fraction=1-(1-efficiency)^(dt/0.5)',
      'read/invoke_queries never advance; host.step is sole clock; cap/cup/clamps/pump/dials process-authority proxies'],
      parameters={},source_ids=['manufacturer_primary','granulation_dataset','ergun','simulation_assumption'],
      limitations=['只有3个MiniGlatt真实复合配方筛分模板，未达到50；后处理筛分不是床内在线PSD。','Ergun等效均匀固定面积不解锥床/Wurster循环或湿黏结；团聚破碎/干燥/过滤系数未知。','d30为零阶粒数闭合合成输出，不是实测d50；不拟合3条数据为任意材料预测。','空气参考温压、泵每转排量、功率因数和热参数为宿主假设；3.3kVA非加热功率。','只水基示例，未建溶剂化学/危险性/完整湿空气/部件接触/实机驱动。'])
    spec=importlib.util.spec_from_file_location('_glatt_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann)
    ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
    ann.annotate_export(SLUG,caps,slots,{'fluidized_bed':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'powder.load':(POWDER,POWDER_EXAMPLE),'liquid.load':(LIQUID,LIQUID_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
    for k,v in slots.items():
        v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'，非实机标定。'
        if 'normal_m3_s' in k:v['reference_conditions']='正常态气体273.15K、101325Pa；为显式模拟约定，厂家N参考未公开'
        elif v['unit']=='m^3/s':v['reference_conditions']='水基喷液实际体积；density由宿主给定'
        elif v['unit']=='Pa':v['reference_conditions']='床层/滤芯两端差压，非绝压或表压'
    mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'fluidized_bed':mech})

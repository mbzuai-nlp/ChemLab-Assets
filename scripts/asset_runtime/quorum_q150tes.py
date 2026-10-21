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
        if not isinstance(v,dict) or not set(s.get('required',[]))<=v.keys():raise ActionRejected('INVALID_ARGUMENT')
        if 'properties' not in s:return                                        # free object (panel.frame)
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
PROFILE_METHOD=copy.deepcopy(METHOD);PROFILE_METHOD['properties']['preclean_s']['maximum']=120.   # profile runs: ramped-carbon Out Gas Time up to 120 s (table 27)
METHOD_EXAMPLE=dict(sputter_current_a=.05,working_pressure_pa=1.,carbon_current_a=46.,carbon_ramp_a_s=1.,preclean_s=2.,duration_s=30.,rotation_enabled=True,rotation_rpm=12.,stage_lift_m=0.,ftm_enabled=True,thickness_endpoint=False,endpoint_m=20e-9,tooling=1.)
REFERENCE=obj({'substance_id':TEXT,'name':TEXT,'formula_raw':TEXT,'density_kg_m3':number('kg/m^3',0),'z_display':number('1',0),'z_established':BOOL,'evidence':TEXT,'source':TEXT,'template_source':TEXT,'conditions':TEXT})
CONVERT=obj(dict(REFERENCE['properties'],thickness_m=number('m',0)))
MEASURE=obj({'phase':PHASE,'stage':TEXT,'pressure_pa':number('Pa',0),'current_a':number('A',0,120),'deposition_elapsed_s':number('s',0),'ftm_enabled':BOOL,'thickness_m':number('m',0),'sample_temperature_k':arr(number('K',1),6),'evidence':TEXT,'template_source':TEXT,'calibrated':BOOL})
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
slot('source.current_a',number('A',0,120),'A','当前溅射/碳棒电流（碳棒脉冲电流最高120 A，手册表25）')
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

# ---------------------------------------------------------------- touch terminal (1.1.0)
# The Q150T touch software as the instruction manual (10473 Issue 7) describes it: stored process profiles with the parameter tables of
# appendix 9.1, the materials database (table 3 / 4), the System Editor (tables 13-16), user levels and groups (6.3), the clock (6.5), the FTM
# status (6.4) and Run Profile with its automatic vent (3.2).  A profile run maps the profile onto the same coating model as method.configure /
# acquisition.start; the panel layer (panel_ui.json) drives these commands.  CAPS above keep their 1.0.0 meaning.
ASSET_VERSION='1.2.0'
ASSET_DIR=ROOT/'assets/expansion_batch04/t5_automation/quorum_q150tes'
PROCESS_LABELS={'timed_sputter':'Timed sputter','ftm_sputter':'FTM terminated sputter','aperture_cleaning':'Aperture cleaning','metal_evaporation':'Metal evaporation',
'pulse_cord':'Pulse cord evaporation','controlled_pulse':'Controlled pulse cord','pulse_rod':'Pulse rod evaporation','ramped_carbon':'Ramped carbon',
'vacuum_shutdown':'Vacuum shutdown','vent_chamber':'Vent chamber'}
PROCESSES=list(PROCESS_LABELS)
def _p(key,label,default,lo=None,hi=None,unit='',admin=False,kind='number'):return dict(key=key,label=label,default=default,min=lo,max=hi,unit=unit,admin=admin,type=kind)
def _b(key,label,default,admin=False):return _p(key,label,default,admin=admin,kind='bool')
_FLUSH=_b('flush_chamber','Flush chamber',False,True)
def _pulse(cur,volt,outgas):
    return [_p('material','Material','Carbon',kind='fixed'),_p('pulse_current','Pulse current (A)',cur,0,120,'A'),_p('max_pulse_voltage','Max Pulse Voltage (V)',volt,0,20,'V',True),
            _p('pulse_length','Pulses Length (s)',1.,.02,10,'s'),_p('number_of_pulses','Number of pulses',1,1,5),_p('pulse_interval','Pulse interval (s)',10.,1,30,'s'),
            _p('outgas_time','Outgas time (s)',10.,2,60,'s'),_p('outgas_current','Outgas current (A)',outgas,0,40,'A'),_p('max_outgas_current','Max Outgas current (A)',40.,0,50,'A'),
            _p('max_outgas_voltage','Max Outgas voltage (V)',10.,0,20,'V'),_b('outgas_source','Outgas source',True,True),_p('tooling','Tooling factor',5.,.1,10),
            _b('pump_hold','Pump hold',False,True),_FLUSH]
PARAMS={
 'timed_sputter':[_p('material','Material','Gold',kind='material'),_p('sputter_current','Sputter Current (mA)',20.,1,50,'mA'),_p('sputter_time','Sputter Time (seconds)',120.,1,3600,'s'),
                  _p('tooling','Tooling Factor',2.3,.1,10),_b('clean_target','Clean target',False,True),_FLUSH],
 'ftm_sputter':[_p('material','Material','Gold',kind='material'),_p('sputter_current','Sputter Current (mA)',20.,1,50,'mA'),_p('terminate_thickness','Terminate Thickness (nm)',10.,1,2000,'nm'),
                _p('max_sputter_time','Maximum Sputter Time (minutes)',4.,0,60,'min'),_p('tooling','Tooling Factor',2.3,.1,10),_b('clean_target','Clean target',False,True),_FLUSH],
 'pulse_cord':_pulse(60.,20.,30.),'pulse_rod':_pulse(75.,10.,20.),
 'controlled_pulse':[_p('terminate_thickness','Terminate thickness (nm)',5.,.5,30,'nm'),_p('pulse_resolution','Pulse Resolution (nm)',.4,.2,2,'nm'),_p('current_step_rate','Current Step Rate (A)',.4,.1,4,'A'),
                     _p('pulse_current','Pulse Current (A)',54.,0,70,'A'),_p('max_pulse_voltage','Max Pulse Voltage (V)',11.,9,16,'V'),_p('pulse_length','Pulse Length (Second)',1.,.02,10,'s'),
                     _p('maximum_pulses','Maximum Pulses',25,1,50),_p('pulse_interval','Pulse Interval (s)',10.,5,30,'s'),_p('outgas_time','Out Gas Time (s)',30.,2,60,'s'),
                     _p('outgas_current','Out Gas current (A)',30.,0,50,'A'),_p('max_outgas_recovery_time','Max outgas recovery time (s)',60.,0,300,'s',True),_FLUSH,
                     _b('pump_hold','Pump hold',True),_p('tooling','Tooling factor',2.25,.1,10)],
 'ramped_carbon':[_p('evaporation_current','Evaporation Current (A)',46.,5,60,'A'),_p('evaporation_time','Evaporation time (min)',1.5,.1,4,'min'),_p('outgas_time','Out Gas Time (s)',10.,2,120,'s'),
                  _p('outgas_current','Outgas Current (A)',20.,2,50,'A'),_p('pre_evap_rate','Pre Evap Rate (A/S)',1.,.5,20,'A/s',True),_p('pre_evap_end_current','PreEvap End current (A)',32.,0,60,'A'),
                  _p('current_ramp_rate','Current Ramp Rate (A/S)',.2,.01,20,'A/s'),_p('tooling','Tooling Factor',1.,.1,10),_b('pump_hold','Pump hold',False,True),_FLUSH],
 'metal_evaporation':[_p('material','Material','Carbon',kind='material'),_p('evaporation_current','Evaporation Current (A)',0.,0,50,'A'),_p('evaporation_time','Evaporation time (Minutes)',1.,.1,4,'min'),
                      _p('outgas_time','Out Gas Time (Seconds)',10.,2,60,'s'),_p('outgas_current','Out Gas Current (A)',20.,0,40,'A'),_p('max_outgas_current','Max Out Gas Current (A)',40.,0,50,'A'),
                      _p('max_outgas_voltage','Max Out Gas Voltage (V)',10.,0,20,'V'),_b('outgas_source','Outgas source',True),_p('tooling','Tooling Factor',5.,.1,10),
                      _b('pump_hold','Pump hold',False),_b('flush_chamber','Flush chamber',False)],
 'aperture_cleaning':[_p('evaporation_current','Evaporation Current (A)',0.,0,100,'A'),_p('evaporation_time','Evaporation time (Minutes)',1.,.1,4,'min'),
                      _p('max_evaporation_voltage','Max Evaporation Voltage(V)',20.,1,20,'V'),_p('tooling','Tooling Factor',5.,.1,10),_b('pump_hold','Pump hold',False),_b('flush_chamber','Flush chamber',False)],
 'vacuum_shutdown':[_p('shutdown_vacuum','Shut down Vacuum (mBar)',1.,.01,100,'mbar')],
 'vent_chamber':[]}
OVERRIDES=[('gas_bleed_time','generic_sputter'),('bleed_vacuum','generic_sputter'),('clean_current','generic_sputter'),('clean_time','generic_sputter'),
           ('stage_rotate','hardware'),('stage_rotate_speed','hardware')]                         # table 31: system properties a profile may override
SYSTEM={
 'network':[_b('dhcp','Automatic IP (DHCP)',True),_p('ip_address','IP address','192.168.1.150',kind='text'),_p('subnet_mask','Subnet mask','255.255.255.0',kind='text')],
 'generic_sputter':[_p('gas_bleed_time','Gas Bleed Time (Seconds)',15.,0,20,'s',True),_p('bleed_vacuum','Bleed Vacuum (mBar)',1e-2,5e-3,5e-1,'mbar',True),
                    _p('clean_current','Clean Current (mA)',150.,100,150,'mA',True),_p('clean_time','Clean Time (Seconds)',30.,0,60,'s',True),
                    _p('clean_discharge_time','Clean Discharge time (Seconds)',20.,15,90,'s',True),_p('max_pulse_clean_cycles','Max pulse clean cycles',2,1,15,'',True)],
 'vacuum':[_p('operational_vacuum','Operational Vacuum (mBar)',1e-3,5e-4,1e-1,'mbar',True),_p('vent_time','Vent Time (Seconds)',70.,10,240,'s',True),
           _p('flush_time','Flush Time (Seconds)',70.,10,240,'s',True),_p('flush_vac_threshold','Flush Vac Threshold (mBar)',8.,.1,100,'mbar',True),
           _p('extra_pump_time','Extra Pump time',120.,1,600,'s',True),_p('pump_hold_time','Pump Hold Time (Hours)',2.,.1,24,'h',True),
           dict(_p('max_pumpdown_time','Max Pumpdown Time',600.,60,600,'s'),admin='engineer'),_p('safe_outgas_threshold','Safe outgas threshold',1.,5e-2,2,'mbar')],
 'maintenance':[_b('service_reminders','Service reminders enabled',True,True),_p('service_interval','Service interval (Days)',365.,1,730,'d',True),
                _p('service_reminder_sleep','Service reminder sleep time',7.,1,180,'d',True),_b('cleaning_reminders','Cleaning reminders enabled',True,True),
                _p('cleaning_interval','Cleaning interval (Hours)',30.,.1,60,'h',True),_p('cleaning_reminder_sleep','Cleaning reminder sleep time',15.,1,120,'min')],
 'hardware':[_p('target_a','Target A','Chromium',kind='material'),_p('target_b','Target B','',kind='material'),_p('target_c','Target C','',kind='material'),
             _p('vacuum_gauge_type','Vacuum Gauge Type','Full range (Pirani / Penning)',kind='readonly'),_b('use_turbo_pumping','Use Turbo Pumping',True,True),
             _b('ftm_present','FTM Present',True),_b('turbo_present','Turbo Present',True),_b('stage_rotate','Stage Rotate',True),
             _p('stage_rotate_speed','Stage Rotate Speed (rpm)',12.,8,20,'rpm'),_p('available_targets','Available Targets',1,1,3,'',True)]}
MATERIAL_PARAMS=[_p('density','Density (g/cm3)',10.,1,30,'g/cm3'),_p('material_type','Material Type','oxidising',kind='choice'),_p('sputter_current','Sputter Current (mA)',50.,1,150,'mA'),
                 _p('clean_current','Clean Current (mA)',150.,100,150,'mA'),_p('material_tool','Material Tool',1.,.1,10),_p('sputter_cleaning_mode','Sputter cleaning mode','Timed',kind='choice',admin=True),
                 _p('sputter_stop_cleaning_v','Sputter stop cleaning at (Volts)',250.,90,600,'V',True)]
CHOICES={'material_type':['noble','oxidising'],'sputter_cleaning_mode':['Timed','Pulse above','Pulse below']}
MATERIALS=[('Aluminium',2.70),('Carbon',2.25),('Titanium',4.54),('Chromium',7.19),('Nickel',8.90),('Copper',8.96),('Silver',10.50),('Palladium',12.02),('Gold/Palladium',17.86),
           ('Tungsten',19.30),('Gold',19.32),('Platinum/Palladium',19.56),('Platinum',21.45),('Iridium',22.40)]
NOBLE={'Gold','Platinum','Palladium','Gold/Palladium','Platinum/Palladium','Iridium','Silver'}
DEFAULT_PROFILES=[('QT Time Chromium','timed_sputter',{'material':'Chromium'}),('QT Chromium 5nm','ftm_sputter',{'material':'Chromium','terminate_thickness':5.}),
                  ('QT Metal evaporation','metal_evaporation',{}),('QT Aperture cleaning','aperture_cleaning',{}),('QT Pulse rod evap','pulse_rod',{}),
                  ('QT Pulse cord evap','pulse_cord',{}),('QT Controlled pulse','controlled_pulse',{}),('QT Vent chamber','vent_chamber',{}),('QT Vacuum shutdown','vacuum_shutdown',{})]
LEVELS=['User','Administration']; ADMIN_CODE='admin'; NAME_MAX=19; MAX_GROUPS=20
CLOCK_EPOCH='2026-01-01T00:00:00'                     # device clock at simulation time 0 (Service | Set Time moves it)
INIT_S=5.; TURBO_UP_S=60.; TURBO_DOWN_S=120.; FTM_LIFE_KG_M2=.01; PULSE_SPUTTER_S=240.
MBAR=100.
ROTARY_M3_S=5./3600.; ROTARY_BASE_PA=5e-2*MBAR      # rotary stage alone: 5 m3/h two-stage pump (Plus brochure); 5x10-2 mbar (Q-series brochure, rotary-pumped Q150R Plus)
PULSE_MAX_A=120.                                    # pulse current maximum (table 25)
def _defaults(defs):return {d['key']:d['default'] for d in defs if d['type']!='readonly'}
def _schema_of(defs):
    out={}
    for d in defs:
        t={'number':'number','bool':'boolean'}.get(d['type'],'string')
        out[d['key']]={'type':t,'x-unit':d['unit'] or '1'} if t=='number' else {'type':t}    # menu settings keep the units the terminal shows
    return out
def _all_keys(tables):
    out={}
    for defs in tables:out.update(_schema_of(defs))
    return out
def opt(p):return dict(type='object',properties=p,required=[],additionalProperties=False)
PROFILE_PARAMS=opt(_all_keys(PARAMS.values()))
PROFILE_SYSTEM=opt({k:_schema_of([d for d in SYSTEM[c] if d['key']==k])[k] for k,c in OVERRIDES})
SYSTEM_PARAMS=opt(_all_keys(SYSTEM.values()))
MATERIAL_SCHEMA=opt(_schema_of(MATERIAL_PARAMS))
PROFILE=obj(dict(name=TEXT,process={'type':'string','enum':PROCESSES},parameters=PROFILE_PARAMS,system=PROFILE_SYSTEM))
MATERIAL=obj(dict(name=TEXT,parameters=MATERIAL_SCHEMA))
GROUP=obj(dict(name=TEXT,level={'type':'string','enum':LEVELS}))
RUN=obj(dict(profile=TEXT,process={'type':'string','enum':PROCESSES},stage=TEXT,progress=number('1',0,1),requested_current_a=number('A',0,120),hold=BOOL,venting=BOOL,message=TEXT))
TERM_ERRORS=['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','REQUEST_CONFLICT','BUSY','PRECONDITION_FAILED']
TERM_CAPS={}
def tcap(k,i,o,c,z,errors=(),effects=(),kind='device_command',ex=None):TERM_CAPS[k]=(i,o,c,z,kind,'agent',ex or {},list(effects));TERM_ERRORS_OF[k]=TERM_ERRORS+list(errors)
TERM_ERRORS_OF={}
_SEL=['touch_surface']
tcap('profile.select',obj({'name':TEXT}),obj({'active_profile':TEXT}),_SEL,'待机画面的配方下拉框：选择要运行的配方',['UNKNOWN_PROFILE'],['terminal.active_profile'],ex={'name':'QT Time Chromium'})
tcap('profile.create',obj({'name':TEXT,'process':{'type':'string','enum':PROCESSES}}),obj({'name':TEXT}),_SEL,'Edit | Profiles → New：按所选工艺类型与名称（≤19字符）以默认参数新建配方',
     ['NAME_TOO_LONG','NAME_EXISTS'],['terminal.profiles'],ex={'name':'My carbon','process':'ramped_carbon'})
tcap('profile.edit',obj({'name':TEXT,'parameters':PROFILE_PARAMS,'system':PROFILE_SYSTEM}),obj({'name':TEXT}),_SEL,
     '配方参数编辑（附录9.1各表的取值范围；带*参数需Administration）与本配方覆盖的系统参数（表31）；OK保存',['UNKNOWN_PROFILE','OUT_OF_RANGE','ACCESS_LEVEL','UNKNOWN_MATERIAL'],['terminal.profiles'],
     ex={'name':'QT Time Chromium','parameters':{'sputter_time':60.},'system':{}})
tcap('profile.delete',obj({'name':TEXT}),obj({'name':TEXT}),_SEL,'Edit | Profiles → Delete：删除配方（删除当前配方时改选列表第一项）',['UNKNOWN_PROFILE'],['terminal.profiles','terminal.active_profile'],ex={'name':'My carbon'})
tcap('profile.run',obj({}),obj({'profile':TEXT,'phase':TEXT,'operation_id':TEXT}),_SEL+['sputter_head','carbon_head'],
     'Run Profile：按当前配方与系统设置抽真空、镀膜（或放气/真空关机），结束后自动放气回到待机',
     ['TOP_COVER_OPEN','SPUTTER_HEAD_REQUIRED','CARBON_ROD_INSERT_NOT_FOUND','CARBON_STRING_INSERT_NOT_FOUND','EVAPORATION_INSERT_NOT_FOUND','PROCESS_NOT_SUPPORTED',
      'MATERIAL_NOT_FOUND','FTM_TOOLING_LOW','FTM_TOOLING_HIGH','FTM_CRYSTAL_FAILED','CANNOT_RUN_PROCESS'],
     ['terminal.run','cycle.phase','cycle.stage','vacuum.pump_enabled','vacuum.venting','method.configured','rotation.enabled','rotation.rpm','ftm.enabled'])
tcap('profile.cancel',obj({}),obj({'cancelled':BOOL}),_SEL,'运行画面的Cancel（或放气对话框的Abort）：停止当前配方运行；镀膜被取消后照常自动放气，放气被中止时关闭放气阀',
     ['NOT_RUNNING'],['terminal.run','cycle.phase','cycle.stage','vacuum.pump_enabled','vacuum.venting','source.current_a'])
tcap('coating.set_current',obj({'current_a':number('A',.001,120)}),obj({'requested_current_a':number('A',0,120)}),_SEL,'运行画面Requested电流的上下箭头：运行中改变请求电流（溅射1–150 mA，渐升碳1–60 A，脉冲碳棒1–120 A）',
     ['NOT_RUNNING'],['terminal.run'],ex={'current_a':.03})
tcap('coating.continue',obj({}),obj({'continued':BOOL}),_SEL,'Pump hold等待时的继续（配方Pump hold=Yes：抽到可除气状态后等待用户）',['NOT_RUNNING'],['terminal.run','cycle.stage'])
tcap('material.create',obj({'name':TEXT}),obj({'name':TEXT}),_SEL,'Edit | Materials → New：以默认参数新建材料（名称≤19字符）',['NAME_TOO_LONG','NAME_EXISTS'],['terminal.materials'],ex={'name':'Tin'})
tcap('material.edit',obj({'name':TEXT,'parameters':MATERIAL_SCHEMA}),obj({'name':TEXT}),_SEL,'材料参数（表4：密度、类型、溅射电流、清洗电流、材料tooling、清洗方式）',
     ['UNKNOWN_MATERIAL','OUT_OF_RANGE','ACCESS_LEVEL'],['terminal.materials'],ex={'name':'Tin','parameters':{'density':7.3}})
tcap('material.delete',obj({'name':TEXT}),obj({'name':TEXT}),_SEL,'Edit | Materials → Delete',['UNKNOWN_MATERIAL'],['terminal.materials'],ex={'name':'Tin'})
tcap('system.configure',obj({'category':{'type':'string','enum':list(SYSTEM)},'parameters':SYSTEM_PARAMS}),obj({'category':TEXT}),_SEL,
     'Edit | System：Network、Generic Sputter、Vacuum、Maintenance、Hardware（表13–16；*项需Administration，**项需工程师）',['OUT_OF_RANGE','ACCESS_LEVEL'],['terminal.system','rotation.enabled','rotation.rpm'],
     ex={'category':'hardware','parameters':{'stage_rotate':False}})
tcap('user.set_level',obj({'group':TEXT,'access_code':TEXT}),obj({'user_level':TEXT}),_SEL,'Edit | User | Level：切换用户组（Administration口令admin；User无口令）',['ACCESS_CODE_INVALID','UNKNOWN_GROUP'],
     ['terminal.user_level'],ex={'group':'Administration','access_code':'admin'})
tcap('user.group_save',obj({'name':TEXT,'level':{'type':'string','enum':LEVELS},'access_code':TEXT,'original_name':TEXT}),obj({'name':TEXT}),_SEL,
     'Edit | User | Groups → New / Edit：组名（≤19字符、不重名）、权限级别与口令（须Administration；最多20组）',['INVALID_GROUP_NAME','MAX_GROUPS','ACCESS_LEVEL','UNKNOWN_GROUP'],['terminal.groups'],
     ex={'name':'Lab A','level':'User','access_code':'a1','original_name':''})
tcap('user.group_delete',obj({'name':TEXT}),obj({'name':TEXT}),_SEL,'Edit | User | Groups → Delete（须Administration）',['ACCESS_LEVEL','UNKNOWN_GROUP'],['terminal.groups','terminal.user_level'],ex={'name':'Lab A'})
tcap('clock.set',obj({'date':TEXT,'time':TEXT}),obj({'clock':TEXT}),_SEL,'Service | Set Time：日、月、年与时间（hh:mm）',[],['terminal.clock'],ex={'date':'2026-10-18','time':'10:30'})
TERM_ERROR_ZH={'UNKNOWN_PROFILE':'没有该名称的配方','NAME_TOO_LONG':'Name is too long：名称超过19字符','NAME_EXISTS':'Name already exists：名称已存在或为空',
 'OUT_OF_RANGE':'超出手册允许范围（对话框显示的允许值）','ACCESS_LEVEL':'灰色参数：当前用户组权限不足（Administration或工程师）','UNKNOWN_MATERIAL':'材料库中没有该材料',
 'TOP_COVER_OPEN':'Please close top cover. Press OK when done','SPUTTER_HEAD_REQUIRED':'Please fit sputter head and close the Top cover. Press Ok when done',
 'CARBON_ROD_INSERT_NOT_FOUND':'Carbon Rod insert not found','CARBON_STRING_INSERT_NOT_FOUND':'Carbon String insert not found（本配置无碳绳插件）',
 'EVAPORATION_INSERT_NOT_FOUND':'Evaporation insert not found（本配置无金属蒸发/光阑清洗插件）','PROCESS_NOT_SUPPORTED':'This instrument does not support this process',
 'MATERIAL_NOT_FOUND':'Selected material not found in material list cannot continue with coating','FTM_TOOLING_LOW':'FTM Tooling Factor less than 0.1',
 'FTM_TOOLING_HIGH':'FTM Tooling Factor greater than 20','FTM_CRYSTAL_FAILED':'FTM crystal not oscillating. Please check or replace','CANNOT_RUN_PROCESS':'Cannot run process',
 'NOT_RUNNING':'没有正在运行的配方','ACCESS_CODE_INVALID':'口令错误','UNKNOWN_GROUP':'没有该用户组','INVALID_GROUP_NAME':'Invalid group name','MAX_GROUPS':'Maximum groups Reached',
 'BUSY':'运行中','PRECONDITION_FAILED':'前提不满足'}
TERM_PRE={'profile.run':'已通电、顶盖关闭、无运行中的配方；溅射配方须装溅射头且材料在材料库中，碳配方须装碳棒插件；FTM tooling乘积0.1–20；样品与源由宿主装载（否则Cannot run process）',
 'profile.cancel':'配方正在运行（镀膜、放气或真空关机）；另一智能体启动的运行只有其启动者或评测者可取消','coating.set_current':'配方镀膜运行中；另一智能体的运行不可修改',
 'coating.continue':'配方运行停在Pump hold；另一智能体的运行不可修改',
 'profile.edit':'带*参数与系统覆盖参数改值须Administration；取值在手册范围内','system.configure':'带*项改值须Administration，**项（Max Pumpdown Time）须工程师（不可用）',
 'user.group_save':'须Administration；组名≤19字符且不重名，最多20组','user.group_delete':'须Administration'}
TERM_LIMITS={'profile.run':['配方运行映射到1.0.0的镀膜模型：溅射（定时/FTM终止）、渐升碳与脉冲碳棒；抽真空到Operational Vacuum后再抽Extra Pump time、N2冲洗、Pump hold、气体预充时间与清洗电流按系统设置；'
 '渐升碳按手册图5-7与表27的四段电流：Outgas Current出气Out Gas Time，开挡板后按Pre Evap Rate升到PreEvap End current，再按Current Ramp Rate升到Evaporation Current并保持，'
 'Evaporation time自达到PreEvap End current起算（PreEvap End高于Evaporation Current时以后者为终点）；脉冲碳棒按表25：Outgas source=Yes时以Outgas current出气（受Max Outgas current与Max Outgas voltage/棒电阻限制），'
 '再给Number of pulses个Pulse current脉冲（Pulses Length，起始间隔Pulse interval；电流受Max Pulse Voltage/棒电阻限制），一次脉冲蒸发按4分钟溅射计入清洗提醒（表15）；'
 '1.0.0的自动冷却间歇只用于溅射与渐升碳（脉冲间隔本身即冷却）；FTM膜厚按材料库中运行材料的Density换算（碳配方用Carbon条目，缺该条目时用参考密度）；'
 '脉冲碳绳、受控脉冲、金属蒸发与光阑清洗不运行（插件未装）；手册所说60–65 A以上脉冲触发电源保护的阈值未给出，未建模。',
 '运行属于启动它的调用者（面板手势或直接调用，返回operation_id可poll/cancel）；另一智能体在面板或直接调用中停止、放气、关泵/断电或修改该运行（Cancel、Abort、电流箭头、Continue、旋转）均以ACCESS_DENIED拒绝且状态不变；评测者不受限。'],
 'clock.set':['仪器时钟=2026-01-01T00:00:00（仿真时间0）+仿真时间+偏移；秒忽略。'],
 'system.configure':['Network只保存（无网络连接）；Maintenance提醒按溅射时间与仿真天数；Hardware的Use Turbo Pumping=No或Turbo Present=No时分子泵不启动，腔体只由前级旋片泵抽气'
 '（有效抽速取宿主泵速与5 m³/h中较小者，极限5×10⁻² mbar：Plus样本的5 m³/h双级旋片泵、Q系列样本的旋片泵型Q150R Plus），达不到Operational Vacuum时运行在Max Pumpdown Time后失败'
 '（System failed to achieve operational Vacuum）；直接vacuum.pump与acquisition.start用同一抽气模型。'],
 'material.edit':['Density供FTM换算运行材料的膜厚显示与FTM终止（真实样品膜厚仍按宿主薄膜密度）；Material Type经配方的Use Materials Defaults=Yes复制为配方的Clean target'
 '（oxidising=Yes、noble=No；手册p.9氧化性靶需溅射清洗、p.78表23），User级也可；Sputter Current经同一对话框复制到配方；材料的Clean Current只保存（手册未说明其作用，清洗电流按System/配方覆盖值）。'],
 'profile.edit':['改选材料时（Use Materials Defaults=Yes）复制的Clean target等于该材料类型的默认值时不需要Administration（手册p.78：选No时可能需管理员才能设清洗）。']}
for _k in ('profile.run','profile.cancel'):TERM_CAPS[_k][7].extend(['cycle.elapsed_s','cycle.deposition_s','safety.warning','ftm.thickness_m','source.current_a'])
# instrument error messages of manual table 21 behind a hardware fault (1.2.0 sweep): evaluator fault.inject / fault.clear.  A present cause refuses
# the next coating run it applies to with the manual text, and ends a running one (the run screen shows the text); the cord messages need the absent
# carbon cord insert and stay unsupported.  safety_interlock and psu_not_powered clear when the instrument is switched off for 10 s (table 21).
FAULTS={'evaporation_psu':('EVAPORATION_PSU_NOT_FOUND','Evaporation PSU not found','carbon'),
        'sputter_psu':('SPUTTER_PSU_NOT_FOUND','Sputter PSU not found','sputter'),
        'vacuum_interlock':('VACUUM_INTERLOCK_NOT_OPERATED','Vacuum interlock not operated. Can not proceed',None),
        'ftm_crystal':('FTM_CRYSTAL_STOPPED','Process stopped due to FTM crystal failure','ftm'),
        'sputter_short':('SPUTTER_SHORT','Short on Sputter output. Please check target','sputter'),
        'safety_interlock':('SAFETY_INTERLOCK','Please contact your supplier a problem has been found with the safety interlock.',None),
        'psu_overtemperature':('PSU_OVERTEMPERATURE','System PSU overtemperature Pleaes turn off system and alow to cool down',None),
        'rod_voltage':('ROD_VOLTAGE_LOW','Rod voltage less than 4V. Process terminated','carbon'),
        'psu_not_powered':('PSU_NOT_POWERED','Interlock Present PSU not powered',None)}
FAULT_CODES=[c for c,_,_ in FAULTS.values()];POWER_CYCLE_FAULTS={'safety_interlock','psu_not_powered'};POWER_CYCLE_S=10.
_FAULT_IN=obj({'code':{'type':'string','enum':list(FAULTS)}})
TERM_CAPS['fault.inject']=(_FAULT_IN,obj({'injected':arr(TEXT)}),_SEL,'评测者：注入手册表21的仪器故障（蒸发/溅射电源未找到、真空联锁未动作、镀膜中FTM晶体失效、溅射输出短路、安全联锁故障、电源过热、碳棒电压低于4 V、联锁在但电源未上电）：正在进行且适用的镀膜运行失败并显示手册文字，原因在时适用的下一次运行被拒（显示同一文字）',
                           'simulation','evaluator',{'code':'sputter_short'},['terminal.faults','cycle.phase','cycle.stage','safety.warning','source.current_a'])
TERM_CAPS['fault.clear']=(_FAULT_IN,obj({'injected':arr(TEXT)}),_SEL,'评测者：手册措施已完成（更换晶体、清洁靶屏蔽、重装碳棒并清洁插件、电源冷却或服务修理）：消除故障原因；安全联锁与电源未上电也可由关机10 s后再开清除',
                          'simulation','evaluator',{'code':'sputter_short'},['terminal.faults'])
for _k in ('fault.inject','fault.clear'):TERM_ERRORS_OF[_k]=list(TERM_ERRORS)
TERM_ERRORS_OF['profile.run']+=FAULT_CODES
TERM_ERROR_ZH.update({c:x for c,x,_ in FAULTS.values()})
slot('terminal.faults',arr(TEXT),'1','评测者注入且尚未消除的仪器故障原因（fault.inject）','setpoint','evaluator',c='touch_surface')
slot('terminal.profiles',arr(PROFILE),'1','触摸屏配方库（名称、工艺类型、参数、本配方覆盖的系统参数）','setpoint',c='touch_surface')
slot('terminal.active_profile',TEXT,'1','待机画面所选配方','setpoint',c='touch_surface')
slot('terminal.materials',arr(MATERIAL),'1','触摸屏材料库（Edit | Materials）','setpoint',c='touch_surface')
slot('terminal.system',obj({cat:opt(_schema_of(defs)) for cat,defs in SYSTEM.items()}),'1','System Editor设置（Network、Generic Sputter、Vacuum、Maintenance、Hardware）','setpoint',c='touch_surface')
slot('terminal.user_level',TEXT,'1','当前用户组（开机为User）','setpoint',c='touch_surface')
slot('terminal.groups',arr(GROUP),'1','自建用户组（名称与权限级别；口令不公开）','setpoint',c='touch_surface')
slot('terminal.clock',TEXT,'1','仪器时钟（ISO日期时间；Service | Set Time可改）',c='touch_surface')
slot('terminal.run',RUN,'1','配方运行状态（配方、阶段文字、进度0–1、请求电流、Pump hold等待、自动放气）',c='touch_surface')
slot('terminal.reminder',TEXT,'1','到期的维护提醒（cleaning、service或空）',c='touch_surface')
slot('ftm.crystal_used_percent',number('1',0,100),'1','FTM晶体已用百分比（Service | FTM；按晶体累计面密度/寿命估计）',c='qcm_sensor')
slot('vacuum.turbo_percent',number('1',0,100),'1','分子泵转速百分比（待机与运行画面的Turbo Speed条）',c='vacuum_port')

def _panel_engine():
    try:
        from . import _panel_ui as engine                                   # imported as asset_runtime.quorum_q150tes (tests, Matterix bridge)
        return engine
    except ImportError:                                                      # loaded from its file path
        import sys
        if '_chemsim_panel_ui' not in sys.modules:
            spec=importlib.util.spec_from_file_location('_chemsim_panel_ui',Path(__file__).with_name('_panel_ui.py'))
            sys.modules['_chemsim_panel_ui']=importlib.util.module_from_spec(spec);spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
        return sys.modules['_chemsim_panel_ui']
_panel_ui=_panel_engine()
PANEL_SPEC=_panel_ui.load_spec(ASSET_DIR/'panel_ui.json',capabilities=set(CAPS)|set(TERM_CAPS))
PANEL_CAPS=_panel_ui.capability_definitions(PANEL_SPEC)                   # panel.switch / touch / capture / frame (CAPS stay the 1.0.0 command set)
_PANEL_FX=list(dict.fromkeys(x for c in _panel_ui.spec_capabilities(PANEL_SPEC) for x in (CAPS.get(c) or TERM_CAPS[c])[7]))
PANEL_TUPLES={k:(v['input_schema'],v['output_schema'],v['component_ids'],v['function_zh'],v['kind'],v['visibility'],v['example_arguments'],[] if v['kind']=='query' else list(_PANEL_FX))
              for k,v in PANEL_CAPS.items()}
ALL_CAPS=dict(CAPS,**TERM_CAPS,**PANEL_TUPLES)
RUN_GUARDED={'acquisition.stop','profile.cancel','coating.set_current','coating.continue','rotation.set','vacuum.vent'}   # + power.set / vacuum.pump off
class QuorumQ150tesRuntime:
    VOLUME=math.pi*.075**2*.127;R=8.314462618
    def __init__(self,instance_id='q150tes_01'):
        self.instance_id=instance_id;self.templates={r['substance_id']:r for r in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']}
        self.time_s=0.;self.revision=0;self.plant=None;self.method=None;self.source=None;self.samples=None;self.lots=[];self.sample_energy=[0.]*6;self.collected_energy=0.;self.electric=0.;self.environment=0.;self.latent=0.
        self.gas_initial=101325*self.VOLUME/(self.R*298.15);self.gas_chamber=self.gas_initial;self.gas_outgas=0.;self.gas_argon=0.;self.gas_vent=0.;self.gas_pumped=0.
        self.powered=False;self.opened=True;self.pump=False;self.vent=False;self.angle=0.;self.lift=0.;self.current=0.;self.phase='idle';self.stage='idle';self.warning='';self.elapsed_s=0.;self.deposit_s=0.;self.stage_elapsed=0.;self.burst=0.;self.qcm_zero=0.
        self.operation_id='';self._operation_count=0;self.log=[];self._principals={'__public_agent__':'agent'};self._requests={};self._owners={};self._sample_ids=set()
        self._caller=None                                                    # principal of the session whose call (or panel gesture) is running
        self._terminal_init();self.panel=_panel_ui.PanelUI(PANEL_SPEC,self)
    def register_principal(self,principal_id,visibility='agent'):
        if visibility not in ('agent','debug','evaluator') or not isinstance(principal_id,str) or not principal_id:raise ActionRejected('INVALID_ARGUMENT')
        if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
        self._principals[principal_id]=visibility
    def session(self,principal_id='__public_agent__'):
        if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
        return QuorumQ150tesSession(self,principal_id)
    def describe(self):return self.session().describe(instance_id=self.instance_id)
    def debug_snapshot(self):return copy.deepcopy({k:v for k,v in self.__dict__.items() if k not in ('templates','panel')})
    def _p(self):return self.gas_chamber*self.R*(self.plant['ambient_k'] if self.plant else 298.15)/self.VOLUME
    def _temps(self):return [(self.plant['ambient_k'] if self.plant else 298.15)+v/(self.plant['sample_heat_capacity_j_k'] if self.plant else 1.) for v in self.sample_energy]
    def _lot(self):return self.lots[-1] if self.lots else None
    def _ftm(self):
        """FTM indicated thickness: crystal mass gain / density x tooling; a profile run uses the density of its material in the materials list
        (Materials editor), the direct method the reference density of the installed source."""
        if not self.method or not self.method['ftm_enabled'] or not self.source:return 0.
        rho=self.ftm_density or self.templates[self.source['substance_id']]['values']['density_kg_m3']
        return max(0.,self._lot()['crystal_kg']-self.qcm_zero)/self.plant['qcm_area_m2']/rho*self.method['tooling']
    def _pumping(self):
        """Effective pumping speed and limiting pressure: turbo-backed (host speed, 0.005 Pa ultimate) or, with Use Turbo Pumping = No or
        Turbo Present = No (System | Hardware, table 16), the rotary stage alone."""
        hw=self.system['hardware'];S=self.plant['pump_m3_s']
        return (S,.005) if hw['use_turbo_pumping'] and hw['turbo_present'] else (min(S,ROTARY_M3_S),ROTARY_BASE_PA)
    def _foreign_run(self):
        """True when an agent session acts on a run (profile run or direct operation) that another principal started."""
        if self._caller is None or self._principals.get(self._caller)!='agent':return False
        c=self.run_cfg
        owner=c.get('owner') if c is not None else self._owners.get(self.operation_id) if self.phase=='running' else None
        return owner is not None and owner!=self._caller
    def _ledger(self):
        return dict(lots=copy.deepcopy(self.lots),gas_initial_mol=self.gas_initial,gas_chamber_mol=self.gas_chamber,gas_outgas_mol=self.gas_outgas,gas_argon_mol=self.gas_argon,gas_vent_mol=self.gas_vent,gas_pumped_mol=self.gas_pumped,electrical_j=self.electric,environment_j=self.environment,carbon_latent_j=self.latent,sample_sensible_j=list(self.sample_energy),collected_sensible_j=self.collected_energy,evidence='synthetic_conservative_lumped_model_not_Q150_calibration')
    def read(self,visibility='agent'):
        if visibility not in ('agent','debug','evaluator'):raise ActionRejected('ACCESS_DENIED')
        m=self.method or METHOD_EXAMPLE;k=self.source['kind'] if self.source else 'sputter'
        v={'plant.configured':self.plant is not None,'method.configured':self.method is not None,'power.enabled':self.powered,'chamber.opened':self.opened,'lid.angle_rad':math.pi/2 if self.opened else 0.,'head.sputter_shift_m':0. if k=='sputter' else .28,'head.carbon_shift_m':0. if k=='carbon' else .28,'shutter.shift_m':.07 if self.phase=='running' and self.stage=='deposition' else 0.,'stage.lift_m':self.lift,'stage.angle_rad':self.angle,'mains.angle_rad':math.pi/2 if self.powered else 0.,'source.kind':k,'source.substance_id':self.source['substance_id'] if self.source else '',
        'vacuum.pump_enabled':self.pump,'vacuum.venting':self.vent,'vacuum.pressure_pa':self._p(),'rotation.enabled':m['rotation_enabled'],'rotation.rpm':m['rotation_rpm'],'source.current_a':self.current,'ftm.enabled':bool(self.method and m['ftm_enabled']),'ftm.thickness_m':self._ftm(),'sample.maximum_k':max(self._temps()),'cycle.phase':self.phase,'cycle.stage':self.stage,'cycle.elapsed_s':self.elapsed_s,'cycle.deposition_s':self.deposit_s,'safety.warning':self.warning,'truth.ledger':self._ledger(),
        'terminal.profiles':copy.deepcopy(self.profiles),'terminal.active_profile':self.active_profile,'terminal.materials':copy.deepcopy(self.materials),'terminal.system':copy.deepcopy(self.system),
        'terminal.user_level':self.user_level,'terminal.groups':[dict(name=g['name'],level=g['level']) for g in self.groups],'terminal.clock':self._clock(),'terminal.run':self._run_view(),
        'terminal.reminder':self._reminder(),'ftm.crystal_used_percent':self._ftm_used(),'vacuum.turbo_percent':100.*self.turbo,'terminal.faults':list(self.faults)}
        return copy.deepcopy({k:x for k,x in v.items() if visibility!='agent' or SLOTS[k][3]=='agent'})
    def invoke(self,capability_id,**a):
        if capability_id not in ALL_CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
        validate(ALL_CAPS[capability_id][0],a)
        if (capability_id in RUN_GUARDED or (capability_id=='vacuum.pump' and not a['enabled'])) and self._foreign_run():
            raise ActionRejected('ACCESS_DENIED')                            # another agent's run: stop / vent / pump off / change refused on every path
            # (the rear mains switch, power.set, is a hard switch: never refused, switching off ends the run as a power loss; 1.2.0 sweep)
        old=self.debug_snapshot()
        try:
            result=self._invoke(capability_id,a);validate(ALL_CAPS[capability_id][1],result)
            if ALL_CAPS[capability_id][4]!='query':self.revision+=1;self.log.append(dict(time_s=self.time_s,event=capability_id,stage=self.stage,phase=self.phase))
            return copy.deepcopy(result)
        except Exception:
            templates,panel=self.templates,self.panel;self.__dict__.clear();self.__dict__.update(old);self.templates=templates;self.panel=panel;raise
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
        if k in TERM_CAPS:return self._terminal(k,a)
        if k in PANEL_CAPS:return getattr(self,'_cmd_'+k.replace('.','_'))(**a)
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
            if not a['enabled']:self._stop();self.pump=False;self.run_cfg=None
            if a['enabled'] and not self.powered:
                self.power_t=self.time_s;self.power_epoch+=1;self.user_level='User'   # the terminal starts in User mode
                if self.off_t is not None and self.time_s-self.off_t>=POWER_CYCLE_S-1e-9:self.faults=[f for f in self.faults if f not in POWER_CYCLE_FAULTS]
            if not a['enabled'] and self.powered:self.off_t=self.time_s
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
            self._fault_gate(self.source['kind'],bool(self.method.get('ftm_enabled')))
            if self._lot()['remaining_kg']<=1e-16:raise ActionRejected('SOURCE_EMPTY')
            self._operation_count+=1;self.operation_id=self.instance_id+':coat:'+str(self._operation_count);self.phase='running';self.stage='pumpdown';self.elapsed_s=0.;self.stage_elapsed=0.;self.deposit_s=0.;self.burst=0.;self.current=0.;self.warning='';self.pump=True;self.vent=False;self.qcm_zero=self._lot()['crystal_kg']
            self.ftm_density=None
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
            S,floor=self._pumping();load=max(p['outgas_pa_m3_s'],S*floor)
            out=load/rt*dt
            argon=max(0.,S*self.method['working_pressure_pa']-load)/rt*dt if self.phase=='running' and self.source['kind']=='sputter' and self.stage not in ('pumpdown','flush_pump','flush_vent','extra_pump','pump_hold') else 0.
            rate=(out+argon)/dt;neq=rate*self.VOLUME/S;new=neq+(old-neq)*math.exp(-S/self.VOLUME*dt)
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
    # ---- touch terminal (1.1.0): stored profiles, materials, system editor, users, clock and the profile run
    def _fault_applies(self,code,kind,ftm):
        need=FAULTS[code][2]
        return need is None or need==kind or (need=='ftm' and ftm)
    def _fault_gate(self,kind,ftm):
        """A fault cause still present refuses a coating run it applies to, with the manual text (table 21)."""
        for code in self.faults:
            if self._fault_applies(code,kind,ftm):raise ActionRejected(FAULTS[code][0])
    def _fault(self,k,code):
        if k=='fault.clear':
            if code in self.faults:self.faults.remove(code)
            return dict(injected=list(self.faults))
        if code not in self.faults:self.faults.append(code)
        coating=self.phase=='running' and (self.run_cfg is None or self.run_cfg.get('kind') not in ('vent','shutdown'))
        if coating and self.source and self._fault_applies(code,self.source['kind'],bool(self.method and self.method.get('ftm_enabled'))):
            self._stop('failed',FAULTS[code][0])                            # the process stops with the manual text on the run screen
        return dict(injected=list(self.faults))
    def _terminal_init(self):
        self.faults=[];self.off_t=None                                     # evaluator faults (1.2.0 sweep)
        self.profiles=[dict(name=n,process=p,parameters=dict(_defaults(PARAMS[p]),**v),system={}) for n,p,v in DEFAULT_PROFILES]
        self.active_profile=self.profiles[0]['name']
        self.materials=[dict(name=n,parameters=dict(_defaults(MATERIAL_PARAMS),density=d,material_type='noble' if n in NOBLE else 'oxidising')) for n,d in MATERIALS]
        self.system={cat:_defaults(defs) for cat,defs in SYSTEM.items()}
        self.user_level='User';self.groups=[];self.clock_offset_s=0.;self.turbo=0.;self.run_cfg=None;self.sputter_s=0.
        self.power_t=None;self.power_epoch=0;self.panel_capture_dir=None
        self.ftm_density=None                                                # density (kg/m3) of the run material from the materials list (profile runs)
    def _admin(self):
        if self.user_level=='Administration':return True
        return any(g['name']==self.user_level and g['level']=='Administration' for g in self.groups)
    def _profile(self,name):
        for p in self.profiles:
            if p['name']==name:return p
        raise ActionRejected('UNKNOWN_PROFILE')
    def _material(self,name):
        for m in self.materials:
            if m['name']==name:return m
        raise ActionRejected('UNKNOWN_MATERIAL')
    def _new_name(self,name,pool):
        if len(name)>NAME_MAX:raise ActionRejected('NAME_TOO_LONG')
        if not name.strip() or any(x['name']==name for x in pool):raise ActionRejected('NAME_EXISTS')
    def _check_values(self,defs,old,new,extra=None,exempt=()):
        """Values of one editor page: known keys, types, the manual's ranges, choices and privileges (only values that change need them;
        keys in exempt were copied by Use Materials Defaults and need no privilege)."""
        by={d['key']:d for d in defs}
        for k,v in new.items():
            d=by.get(k)
            if d is None or d['type'] in ('fixed','readonly'):raise ActionRejected('INVALID_ARGUMENT')
            if d['type']=='number':
                hi=d['max']
                if extra and k in extra:hi=extra[k]
                if not d['min']-1e-12<=v<=hi+1e-12:raise ActionRejected('OUT_OF_RANGE')
            if d['type']=='material' and v and not any(m['name']==v for m in self.materials):raise ActionRejected('UNKNOWN_MATERIAL')
            if d['type']=='choice' and v not in CHOICES[k]:raise ActionRejected('OUT_OF_RANGE')
            if d['type']=='text' and k in ('ip_address','subnet_mask'):
                parts=v.split('.')
                if len(parts)!=4 or not all(x.isdigit() and int(x)<=255 for x in parts):raise ActionRejected('OUT_OF_RANGE')
            if v!=old.get(k) and k not in exempt and (d['admin']=='engineer' or (d['admin'] and not self._admin())):raise ActionRejected('ACCESS_LEVEL')
    def _clock(self):
        import datetime
        t=datetime.datetime.fromisoformat(CLOCK_EPOCH)+datetime.timedelta(seconds=int(math.floor(self.time_s+self.clock_offset_s)))
        return t.isoformat()
    def _setting(self,prof,key,cat):
        return prof['system'].get(key,self.system[cat][key])
    def _ftm_used(self):
        if not self.plant:return 0.
        kg=sum(l['crystal_kg'] for l in self.lots)
        return min(100.,100.*kg/self.plant['qcm_area_m2']/FTM_LIFE_KG_M2)
    def _reminder(self):
        mt=self.system['maintenance']
        if mt['cleaning_reminders'] and self.sputter_s>=mt['cleaning_interval']*3600:return 'cleaning'
        if mt['service_reminders'] and self.time_s>=mt['service_interval']*86400:return 'service'
        return ''
    def _run_view(self):
        c=self.run_cfg
        if c is None:return dict(profile='',process='vent_chamber',stage='Standby',progress=0.,requested_current_a=0.,hold=False,venting=False,message='')
        m=self.method or METHOD_EXAMPLE;carbon=c['kind']=='carbon'
        req=c['pulse_a'] if c.get('pulse_a') is not None else m['carbon_current_a'] if carbon else m['sputter_current_a'] if c['kind']=='sputter' else 0.
        venting=c.get('vent') is not None
        if venting:
            stage='Venting';prog=0. if c['vent']=='wait_turbo' else min(1.,c['vent_t']/c['vent_s'])
        elif c['kind']=='shutdown':
            stage='Vacuum shutdown';prog=min(1.,max(0.,math.log10(self.plant['ambient_pa']/max(self._p(),1e-9))/max(1e-9,math.log10(self.plant['ambient_pa']/c['shutdown_pa']))))
        else:
            names={'flush_pump':'Flush pumping','flush_vent':'Flushing chamber','pumpdown':'Pumping','extra_pump':'Pumping','pump_hold':'Pump hold','stabilizing':'Gas bleed',
                   'preclean':'Outgassing' if carbon else 'Cleaning target','deposition':'Coating Sample','cooling':'Cooling','finished':'Finished'}
            stage=names.get(self.stage,self.stage)
            prog=0.
            if self.stage in ('deposition','cooling') and m['duration_s']>0:
                prog=min(1.,self.deposit_s/m['duration_s'])
                if m['thickness_endpoint']:prog=max(prog,min(1.,self._ftm()/m['endpoint_m']))
        return dict(profile=c['profile'],process=c['process'],stage=stage,progress=prog,requested_current_a=req,hold=self.stage=='pump_hold' and self.phase=='running',venting=venting,message=c.get('message',''))
    def _terminal(self,k,a):
        if k in ('fault.inject','fault.clear'):return self._fault(k,a['code'])
        if k=='profile.select':
            self._profile(a['name']);self.active_profile=a['name'];return dict(active_profile=self.active_profile)
        if k=='profile.create':
            self._new_name(a['name'],self.profiles)
            self.profiles.append(dict(name=a['name'],process=a['process'],parameters=_defaults(PARAMS[a['process']]),system={}));return dict(name=a['name'])
        if k=='profile.edit':
            p=self._profile(a['name']);new=dict(p['parameters'],**a['parameters'])
            extra={'evaporation_current':100. if new.get('max_evaporation_voltage',20.)<2 else 50.} if p['process']=='aperture_cleaning' else None
            exempt=()
            if 'clean_target' in new and new.get('material')!=p['parameters'].get('material'):   # Use Materials Defaults: oxidising targets are cleaned (p.9, p.78)
                mat=next((x for x in self.materials if x['name']==new['material']),None)
                if mat is not None and new['clean_target']==(mat['parameters']['material_type']=='oxidising'):exempt=('clean_target',)
            self._check_values(PARAMS[p['process']],p['parameters'],a['parameters'],extra,exempt)
            if 'pulse_interval' in new and 'pulse_length' in new and new['pulse_interval']<=new['pulse_length']:raise ActionRejected('OUT_OF_RANGE')
            sysdefs=[dict(d,admin=True) for cat in ('generic_sputter','hardware') for d in SYSTEM[cat] if d['key'] in dict(OVERRIDES)]
            self._check_values(sysdefs,{key:self._setting(p,key,cat) for key,cat in OVERRIDES},a['system'])
            p['parameters']=new;p['system']=dict(p['system'],**a['system']);return dict(name=p['name'])
        if k=='profile.delete':
            p=self._profile(a['name']);self.profiles.remove(p)
            if self.active_profile==a['name']:self.active_profile=self.profiles[0]['name'] if self.profiles else ''
            return dict(name=a['name'])
        if k=='material.create':
            self._new_name(a['name'],self.materials);self.materials.append(dict(name=a['name'],parameters=_defaults(MATERIAL_PARAMS)));return dict(name=a['name'])
        if k=='material.edit':
            m=self._material(a['name']);self._check_values(MATERIAL_PARAMS,m['parameters'],a['parameters']);m['parameters']=dict(m['parameters'],**a['parameters']);return dict(name=m['name'])
        if k=='material.delete':
            m=self._material(a['name']);self.materials.remove(m);return dict(name=a['name'])
        if k=='system.configure':
            cat=a['category'];self._check_values(SYSTEM[cat],self.system[cat],a['parameters'])
            self.system[cat]=dict(self.system[cat],**a['parameters']);return dict(category=cat)
        if k=='user.set_level':
            g=a['group']
            if g=='User':pass
            elif g=='Administration':
                if a['access_code']!=ADMIN_CODE:raise ActionRejected('ACCESS_CODE_INVALID')
            else:
                grp=next((x for x in self.groups if x['name']==g),None)
                if grp is None:raise ActionRejected('UNKNOWN_GROUP')
                if a['access_code']!=grp['access_code']:raise ActionRejected('ACCESS_CODE_INVALID')
            self.user_level=g;return dict(user_level=g)
        if k=='user.group_save':
            if not self._admin():raise ActionRejected('ACCESS_LEVEL')
            name,orig=a['name'],a['original_name']
            if orig and not any(x['name']==orig for x in self.groups):raise ActionRejected('UNKNOWN_GROUP')
            if not name.strip() or len(name)>NAME_MAX or name in LEVELS or any(x['name']==name and x['name']!=orig for x in self.groups):raise ActionRejected('INVALID_GROUP_NAME')
            if orig:
                g=next(x for x in self.groups if x['name']==orig);g.update(name=name,level=a['level'],access_code=a['access_code'] or g['access_code'])
                if self.user_level==orig:self.user_level=name
            else:
                if len(self.groups)>=MAX_GROUPS:raise ActionRejected('MAX_GROUPS')
                self.groups.append(dict(name=name,level=a['level'],access_code=a['access_code']))
            return dict(name=name)
        if k=='user.group_delete':
            if not self._admin():raise ActionRejected('ACCESS_LEVEL')
            g=next((x for x in self.groups if x['name']==a['name']),None)
            if g is None:raise ActionRejected('UNKNOWN_GROUP')
            self.groups.remove(g)
            if self.user_level==a['name']:self.user_level='User'
            return dict(name=a['name'])
        if k=='clock.set':
            import datetime
            try:target=datetime.datetime.fromisoformat(a['date']+'T'+a['time'])
            except ValueError:raise ActionRejected('INVALID_ARGUMENT') from None
            self.clock_offset_s=(target-datetime.datetime.fromisoformat(CLOCK_EPOCH)).total_seconds()-math.floor(self.time_s);return dict(clock=self._clock())
        if k=='profile.run':return self._run_profile()
        if k=='profile.cancel':
            c=self.run_cfg
            if self.phase=='running':self._stop()
            elif c is not None and c.get('vent') is not None:self.vent=False;self.run_cfg=None
            elif c is not None and c['kind']=='shutdown':self.pump=False;self.run_cfg=None
            else:raise ActionRejected('NOT_RUNNING')
            return dict(cancelled=True)
        if k=='coating.set_current':
            if self.phase!='running' or not self.run_cfg:raise ActionRejected('NOT_RUNNING')
            if self.run_cfg.get('pulse_a') is not None:                       # pulse rod: the pulse current (table 25: up to 120 A)
                if not 1.-1e-12<=a['current_a']<=PULSE_MAX_A+1e-12:raise ActionRejected('OUT_OF_RANGE')
                self.run_cfg['pulse_a']=float(a['current_a']);return dict(requested_current_a=self.run_cfg['pulse_a'])
            key='carbon_current_a' if self.run_cfg['kind']=='carbon' else 'sputter_current_a'
            lo,hi=(1.,60.) if key=='carbon_current_a' else (.001,.150)
            if not lo-1e-12<=a['current_a']<=hi+1e-12:raise ActionRejected('OUT_OF_RANGE')
            self.method[key]=float(a['current_a']);return dict(requested_current_a=self.method[key])
        if k=='coating.continue':
            if self.phase!='running' or self.stage!='pump_hold':raise ActionRejected('NOT_RUNNING')
            self._next_stage('stabilizing' if self.source['kind']=='sputter' else 'preclean');return dict(continued=True)
        raise ActionRejected('UNKNOWN_CAPABILITY')
    def _run_profile(self):
        """Run Profile: the stored profile and the system settings mapped onto the coating model (method.configure + acquisition.start), or the
        vent / vacuum shutdown cycles; the run vents the chamber at its end (manual 3.2)."""
        self._idle()
        if self.run_cfg is not None:raise ActionRejected('BUSY')
        if not self.plant or not self.powered:raise ActionRejected('PRECONDITION_FAILED')
        if self.opened:raise ActionRejected('TOP_COVER_OPEN')
        prof=self._profile(self.active_profile);proc=prof['process'];P=prof['parameters'];V=self.system['vacuum'];hw=self.system['hardware']
        if proc in ('pulse_cord','controlled_pulse'):raise ActionRejected('CARBON_STRING_INSERT_NOT_FOUND')
        if proc in ('metal_evaporation','aperture_cleaning'):raise ActionRejected('EVAPORATION_INSERT_NOT_FOUND')
        base=dict(profile=prof['name'],process=proc,vent=None,vent_t=0.,vent_s=V['vent_time'],owner=self._caller)   # the run belongs to its caller
        if proc=='vent_chamber':
            self.pump=False;self.run_cfg=dict(base,kind='vent',vent='wait_turbo');return dict(profile=prof['name'],phase=self.phase,operation_id='')
        if proc=='vacuum_shutdown':
            self.pump=True;self.vent=False;self.run_cfg=dict(base,kind='shutdown',shutdown_pa=P['shutdown_vacuum']*MBAR);return dict(profile=prof['name'],phase=self.phase,operation_id='')
        sputter=proc in ('timed_sputter','ftm_sputter')
        if not self.source or self.source['kind']!=('sputter' if sputter else 'carbon'):
            raise ActionRejected('SPUTTER_HEAD_REQUIRED' if sputter else 'CARBON_ROD_INSERT_NOT_FOUND')
        tool=P['tooling']
        if sputter:
            mat=next((m for m in self.materials if m['name']==P['material']),None)
            if mat is None:raise ActionRejected('MATERIAL_NOT_FOUND')
            tool*=mat['parameters']['material_tool']
        else:mat=next((m for m in self.materials if m['name']=='Carbon'),None)   # carbon processes: the Carbon entry of the materials list (FTM density)
        ftm=bool(hw['ftm_present'])
        if ftm and tool<.1-1e-12:raise ActionRejected('FTM_TOOLING_LOW')
        if ftm and tool>20+1e-12:raise ActionRejected('FTM_TOOLING_HIGH')
        if proc=='ftm_sputter' and not ftm:raise ActionRejected('PROCESS_NOT_SUPPORTED')
        if proc=='ftm_sputter' and self._ftm_used()>=100.:raise ActionRejected('FTM_CRYSTAL_FAILED')
        self._fault_gate('sputter' if sputter else 'carbon',ftm)                # an injected fault (table 21) refuses the run with its text
        m=dict(METHOD_EXAMPLE,stage_lift_m=self.lift,ftm_enabled=ftm,tooling=tool,thickness_endpoint=proc=='ftm_sputter',
               rotation_enabled=bool(self._setting(prof,'stage_rotate','hardware')),rotation_rpm=float(self._setting(prof,'stage_rotate_speed','hardware')))
        carbon=None                                                           # the documented rod current profile of a carbon process
        if sputter:
            clean=bool(P['clean_target'])
            m.update(sputter_current_a=P['sputter_current']/1000.,working_pressure_pa=self._setting(prof,'bleed_vacuum','generic_sputter')*MBAR,
                     preclean_s=float(self._setting(prof,'clean_time','generic_sputter')) if clean else 0.)
            if proc=='timed_sputter':m.update(duration_s=float(P['sputter_time']))
            else:m.update(duration_s=float(P['max_sputter_time'])*60. or 3600.,endpoint_m=P['terminate_thickness']*1e-9)
        elif proc=='ramped_carbon':                                         # four phases (p.36 Figure 5-7, table 27)
            og=float(P['outgas_current']);evap=float(P['evaporation_current']);pre_end=max(og,min(float(P['pre_evap_end_current']),evap))
            pre_s=(pre_end-og)/float(P['pre_evap_rate'])                      # evaporation time starts at PreEvap End current
            m.update(carbon_current_a=evap,carbon_ramp_a_s=float(P['current_ramp_rate']),preclean_s=float(P['outgas_time']),duration_s=pre_s+float(P['evaporation_time'])*60.)
            carbon=dict(kind='ramped',outgas_a=og,pre_rate=float(P['pre_evap_rate']),pre_end_a=pre_end,pre_s=pre_s)
        else:                                                                 # pulse rod (table 25, section 5.6)
            R=self.plant['carbon_resistance_ohm'];outgas=bool(P['outgas_source']);n=int(P['number_of_pulses'])
            m.update(preclean_s=float(P['outgas_time']) if outgas else 0.,duration_s=(n-1)*float(P['pulse_interval'])+float(P['pulse_length']))
            carbon=dict(kind='pulse',outgas_a=min(float(P['outgas_current']),float(P['max_outgas_current']),float(P['max_outgas_voltage'])/R) if outgas else 0.,
                        pulses=n,length_s=float(P['pulse_length']),interval_s=float(P['pulse_interval']),max_v=float(P['max_pulse_voltage']),counted=False)
        try:
            validate(PROFILE_METHOD,m);self._invoke('method.configure',m);self._invoke('acquisition.start',{})
        except ActionRejected:raise ActionRejected('CANNOT_RUN_PROCESS') from None
        self.ftm_density=mat['parameters']['density']*1000. if mat else None          # g/cm3 -> kg/m3
        flush=bool(P.get('flush_chamber'))
        self.run_cfg=dict(base,kind='sputter' if sputter else 'carbon',operational_pa=V['operational_vacuum']*MBAR,extra_s=V['extra_pump_time'],max_pump_s=V['max_pumpdown_time'],
                          hold=bool(P.get('pump_hold')),hold_s=V['pump_hold_time']*3600.,flush=flush,flush_pa=V['flush_vac_threshold']*MBAR,flush_s=V['flush_time'],
                          bleed_s=float(self._setting(prof,'gas_bleed_time','generic_sputter')) if sputter else 0.,
                          clean_a=float(self._setting(prof,'clean_current','generic_sputter'))/1000. if sputter and P['clean_target'] else None,
                          carbon=carbon,pulse_a=float(P['pulse_current']) if proc=='pulse_rod' else None)
        if flush:self.stage='flush_pump'
        return dict(profile=prof['name'],phase=self.phase,operation_id=self.operation_id)
    def _cfg_stage(self):
        """Stages of a profile run that the 1.0.0 cycle does not have (N2 flush, operational vacuum + extra pump time, pump hold, gas bleed);
        True when this stage was handled here."""
        c=self.run_cfg;s=self.stage
        if s=='flush_pump':
            if self._p()<=c['flush_pa']:self._next_stage('flush_vent');self.pump=False;self.vent=True
            elif self.stage_elapsed>=c['max_pump_s']:self._stop('failed','PUMP_TIMEOUT')
        elif s=='flush_vent':
            if self.stage_elapsed>=c['flush_s']:self._next_stage('pumpdown');self.pump=True;self.vent=False
        elif s=='pumpdown':
            if self._p()<=c['operational_pa']:self._next_stage('extra_pump')
            elif self.stage_elapsed>=c['max_pump_s']:self._stop('failed','PUMP_TIMEOUT')
        elif s=='extra_pump':
            if self.stage_elapsed>=c['extra_s']:self._next_stage('pump_hold' if c['hold'] else 'stabilizing' if c['kind']=='sputter' else 'preclean')
        elif s=='pump_hold':
            if self.stage_elapsed>=c['hold_s']:self._stop('cancelled','PUMP_HOLD_TIMEOUT')
        elif s=='stabilizing':
            m=self.method
            if self.stage_elapsed>=c['bleed_s'] and abs(self._p()-m['working_pressure_pa'])<=max(.02,.02*m['working_pressure_pa']):self._next_stage('preclean')
            elif self.stage_elapsed>=c['max_pump_s']:self._stop('failed','PRESSURE_TIMEOUT')
        else:return False
        return True
    def _terminal_step(self,dt):
        """Clock-driven terminal state: turbo speed, the automatic vent after a profile run, the vacuum shutdown cycle, sputter hours."""
        hw=self.system['hardware'];V=self.system['vacuum']
        spin=self.powered and self.pump and hw['use_turbo_pumping'] and hw['turbo_present'] and self._p()<=V['safe_outgas_threshold']*MBAR
        self.turbo=min(1.,self.turbo+dt/TURBO_UP_S) if spin else max(0.,self.turbo-dt/TURBO_DOWN_S)
        if self.phase=='running' and self.stage in ('preclean','deposition') and self.source and self.source['kind']=='sputter':self.sputter_s+=dt
        rod=(self.run_cfg or {}).get('carbon')
        if rod and rod['kind']=='pulse' and not rod['counted'] and self.phase=='running' and self.stage=='deposition':
            rod['counted']=True;self.sputter_s+=PULSE_SPUTTER_S                 # one pulse evaporation counts as 4 min of sputtering (table 15)
        c=self.run_cfg
        if c is None:return
        if not self.powered or self.opened:self.run_cfg=None;return
        if c['kind']=='shutdown':
            if self._p()<=c['shutdown_pa']:self.pump=False;self.run_cfg=None
            return
        if c['kind'] in ('sputter','carbon') and self.phase=='running':return
        if c['vent'] is None:c['vent']='wait_turbo';self.pump=False
        if c['vent']=='wait_turbo':
            if self.turbo<=.5:c['vent']='venting';self.vent=True;self.pump=False
        else:
            c['vent_t']+=dt
            if c['vent_t']>=c['vent_s']-1e-9 and self._p()>=.999*self.plant['ambient_pa']:self.vent=False;self.run_cfg=None
    # ---- front panel: host protocol of the engine (_panel_ui.py) and the panel.* commands
    def panel_read(self):
        out=self.read('agent')
        pw=self.powered and self.plant is not None
        out.update({'host.powered':pw,'host.power_epoch':self.power_epoch,'host.initialising':bool(pw and self.power_t is not None and self.time_s-self.power_t<INIT_S),
                    'host.admin':self._admin(),'host.pressure_mbar':self._p()/MBAR,'host.pressure_log':min(1.,max(0.,(math.log10(max(self._p()/MBAR,1e-9))+5.)/8.)),
                    'host.failure':self.warning if self.phase=='failed' else '','host.operation':self.operation_id,'host.pressure_text':self._pressure_text(),
                    'host.ftm_rate':self._ftm()*1e9/(self.deposit_s/60.) if self.deposit_s>0 else 0.,'host.level_options':[dict(name=n) for n in LEVELS+[g['name'] for g in self.groups]]})
        prof=next((p for p in self.profiles if p['name']==(self.run_cfg or {}).get('profile')),None)
        mat=prof and prof['parameters'].get('material')
        out['host.run_material']=mat or ('Carbon' if prof else '')
        out['host.run_density']=next((m['parameters']['density'] for m in self.materials if m['name']==out['host.run_material']),0.)
        return out
    def _pressure_text(self):
        """Chamber pressure as the meter prints it: one significant digit and the decade, e.g. 1x10-2 (mbar)."""
        v=max(self._p()/MBAR,1e-9);e=int(math.floor(math.log10(v)));m=int(round(v/10**e))
        if m>=10:m,e=1,e+1
        return f'{m}x10{e:+d}'
    def panel_invoke(self,capability_id,arguments):
        try:return self.invoke(capability_id,**arguments)
        except ActionRejected as exc:raise _panel_ui.PanelRejected(str(exc)) from None
    def panel_time(self):return self.time_s
    def _panel(self,gesture,*args):
        if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
        try:return dict(accepted=bool(gesture(*args)))
        except _panel_ui.PanelError:raise ActionRejected('INVALID_ARGUMENT') from None
    def _cmd_panel_switch(self,input_id,position):return self._panel(self.panel.switch,input_id,position)
    def _cmd_panel_touch(self,display_id,x,y,duration_s=.1,to_x=None,to_y=None):
        if (to_x is None)!=(to_y is None):raise ActionRejected('INVALID_ARGUMENT')
        return self._panel(self.panel.touch,display_id,int(x),int(y),float(duration_s),None if to_x is None else (int(to_x),int(to_y)))
    def _cmd_panel_capture(self,display_id):
        import hashlib,tempfile
        if not self.plant:raise ActionRejected('PRECONDITION_FAILED')
        data,w,h=self.panel.png_bytes(display_id);sha=hashlib.sha256(data).hexdigest()
        d=Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir())/'chemistry_sim_panel'/self.instance_id
        d.mkdir(parents=True,exist_ok=True);path=d/f'{display_id}_{sha[:16]}.png'
        if not path.exists():path.write_bytes(data)
        return dict(display_id=display_id,time_s=self.time_s,image=dict(path=str(path),format='png',width=w,height=h,sha256=sha))
    def _cmd_panel_frame(self):return dict(frame=self.panel.frame())
    def _pulsed(self):
        rod=(self.run_cfg or {}).get('carbon');return bool(rod and rod['kind']=='pulse')
    def _carbon_current(self):
        """Rod current: the 1.0.0 lumped ramp (method.configure) or the documented current profile of a profile run.  Ramped carbon (p.36
        Figure 5-7, table 27): Outgas Current for the Out Gas Time; with the shutter open it rises at Pre Evap Rate to PreEvap End current, then at
        Current Ramp Rate to Evaporation Current, which is held.  Pulse rod (table 25): Outgas current, then Number of pulses pulses of Pulse current
        for Pulses Length every Pulse interval, limited by Max Pulse Voltage over the rod resistance.  The evaporation clock is the deposition time
        (shutter open), so the automatic cooling pauses of the coating model do not restart the ramp."""
        m=self.method;rod=(self.run_cfg or {}).get('carbon')
        if rod is None:return min(m['carbon_current_a'],20.+m['carbon_ramp_a_s']*self.stage_elapsed)
        if self.stage=='preclean':return rod['outgas_a']
        t=self.deposit_s
        if rod['kind']=='pulse':
            k=int(math.floor(t/rod['interval_s']+1e-9))
            on=k<rod['pulses'] and t-k*rod['interval_s']<rod['length_s']-1e-9
            return min(self.run_cfg['pulse_a'],rod['max_v']/self.plant['carbon_resistance_ohm']) if on else 0.
        ramp=rod['outgas_a']+rod['pre_rate']*t if t<rod['pre_s'] else rod['pre_end_a']+m['carbon_ramp_a_s']*(t-rod['pre_s'])
        return min(m['carbon_current_a'],ramp)
    def _pulse_edge(self):
        """Time from now to the next pulse start or end (steps end on the edges so each pulse lasts exactly its length)."""
        if not self._pulsed():return math.inf
        rod=self.run_cfg['carbon'];t=self.deposit_s;T=rod['interval_s'];k=math.floor(t/T+1e-9)
        return next((e-t for e in (k*T+rod['length_s'],(k+1)*T) if e-t>1e-9),math.inf)
    def step(self,dt_s):
        validate(number('s',0,3600),dt_s)
        remaining=float(dt_s)
        while remaining>1e-12:
            dt=min(.02,remaining)
            if self.phase=='running' and self.stage=='deposition' and self.deposit_s<self.method['duration_s']:
                dt=min(dt,self.method['duration_s']-self.deposit_s,self._pulse_edge())
            if dt<1e-12:self._stop('succeeded');continue
            remaining-=dt;before=(self.phase,self.stage);self._advance(dt);self.time_s+=dt;self.revision+=1
            if before!=(self.phase,self.stage):self.log.append(dict(time_s=self.time_s,event='stage_transition',phase=self.phase,stage=self.stage))
    def _advance(self,dt):
        if not self.plant:return
        self._gas_step(dt);p=self.plant;m=self.method;active=self.phase=='running'
        if active:
            self.elapsed_s+=dt;self.stage_elapsed+=dt
            if self.opened or not self.powered or (not self.pump and not (self.run_cfg and self.stage=='flush_vent')):self._stop('failed','PROCESS_INTERLOCK');active=False
            elif self.run_cfg and self._cfg_stage():pass                       # profile-run stages (1.1.0)
            elif self.stage=='pumpdown':
                if self._p()<=.01:self._next_stage('stabilizing' if self.source['kind']=='sputter' else 'preclean')
                elif self.elapsed_s>=p['pump_timeout_s']:self._stop('failed','PUMP_TIMEOUT')
            elif self.stage=='stabilizing':
                if abs(self._p()-m['working_pressure_pa'])<=max(.02,.02*m['working_pressure_pa']):self._next_stage('preclean')
                elif self.elapsed_s>=p['pump_timeout_s']:self._stop('failed','PRESSURE_TIMEOUT')
            elif self.stage=='preclean' and self.stage_elapsed>=m['preclean_s']:self._next_stage('deposition')
            elif self.stage=='cooling' and self.stage_elapsed>=p['cool_duration_s']:self.burst=0.;self._next_stage('deposition')
        generating=self.phase=='running' and self.stage in ['preclean','deposition']
        carbon=bool(self.source and self.source['kind']=='carbon');self.current=0. if not generating else (self._carbon_current() if carbon else m['sputter_current_a'])
        if generating and not carbon and self.stage=='preclean' and self.run_cfg and self.run_cfg.get('clean_a'):self.current=self.run_cfg['clean_a']   # target clean current
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
                elif self.burst>=p['cool_after_s']-1e-10 and not self._pulsed():self._next_stage('cooling')   # pulses: the interval is the cooling
            if lot['remaining_kg']<=1e-16 and self.phase=='running':self._stop('failed','SOURCE_EMPTY')
        if self.phase=='running' and m['rotation_enabled'] and self.stage in ['preclean','deposition','cooling']:self.angle=(self.angle+m['rotation_rpm']*2*math.pi/60*dt)%(2*math.pi)
        if max(self._temps())>p['sample_trip_k'] and self.phase=='running':self._stop('failed','SAMPLE_OVERHEAT')
        self._terminal_step(dt)

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
        m=export_quorum_q150tes_contract_metadata();return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,capabilities={k:v for k,v in m['capabilities'].items() if v['visibility']=='agent' or self._role!='agent'},state_slots={k:v for k,v in m['state_slots'].items() if v['visibility']=='agent' or self._role!='agent'})
    def read(self,*,instance_id,state_ids=None):
        self._target(instance_id);h=self._host;v=h.read(self._role);ids=list(v) if state_ids is None else state_ids
        if not isinstance(ids,list) or any(not isinstance(k,str) for k in ids):raise ActionRejected('INVALID_ARGUMENT')
        if any(k not in v for k in ids):raise ActionRejected('ACCESS_DENIED')
        return dict(status='succeeded',instance_id=instance_id,state_revision=h.revision,clock={'domain':'simulation','time_s':h.time_s},observations={k:dict(value=v[k],unit=SLOTS[k][1],quality='estimated' if SLOTS[k][2]=='derived' else 'valid',sample_time_s=h.time_s,sample_window_s=None) for k in ids})
    def invoke(self,*,instance_id,capability_id,arguments,request_id):
        h=self._host
        try:
            self._target(instance_id)
            if not isinstance(capability_id,str) or capability_id not in ALL_CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
            if self._role=='agent' and ALL_CAPS[capability_id][5]!='agent':raise ActionRejected('ACCESS_DENIED')
            if not isinstance(request_id,str) or not request_id:raise ActionRejected('INVALID_ARGUMENT')
            validate(ALL_CAPS[capability_id][0],arguments);key=(self._principal,instance_id,request_id);digest=json.dumps([capability_id,arguments],sort_keys=True,allow_nan=False)
            if key in h._requests:
                old,result=h._requests[key]
                if old!=digest:raise ActionRejected('REQUEST_CONFLICT')
                return copy.deepcopy(result)
            before=h.operation_id;h._caller=self._principal                  # a panel gesture acts as this session's caller (access and ownership)
            try:result=h.invoke(capability_id,**arguments)
            finally:h._caller=None
            op=result.get('operation_id')
            if op:h._owners[op]=self._principal
            elif capability_id in PANEL_CAPS and h.operation_id and h.operation_id!=before:h._owners[h.operation_id]=self._principal   # a run started by a panel gesture belongs to the caller
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
    for k,(inp,out,c,z,kind,vis,ex,effects) in ALL_CAPS.items():
        asynchronous=k in ['acquisition.start','profile.run'];query=kind=='query';term=k in TERM_CAPS;pan=k in PANEL_CAPS
        caps[k]=dict(function_zh=z,kind=kind,visibility=vis,component_ids=c,availability='implemented',input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.quorum_q150tes.QuorumQ150tesSession.invoke',invocation=f'session.invoke(instance_id="q150tes_01",capability_id={k!r},arguments={ex!r},request_id="unique")'),
          preconditions=[] if query else [dict(description_zh=TERM_PRE.get(k,'电源、真空、开盖、有限源材料/样品及宿主身份检查'),state_ids=['power.enabled','chamber.opened','vacuum.pump_enabled','cycle.phase']+(['terminal.user_level'] if term or pan else []),enforced_by='runtime',check='Runtime.invoke atomic guards; Session trusted-principal filtering')],
          state_effects=[] if query else [dict(state_id=s,timing='immediate',description_zh='设定/附着事件/有限装料变化') for s in effects]+[dict(state_id=s,timing='on_step',description_zh='宿主唯一时钟演化') for s in evolving],
          observes=[],model_effects=dict(mode='none',description_zh='模型实际求值证据尚待独立核验',binding_ids=[]),
          execution=dict(mode='asynchronous' if asynchronous else 'synchronous',time_domain='simulation',completion_criterion='有效沉积时间或FTM终点达到；不等于实测膜厚/均匀性' if asynchronous else '事务返回；物理量由宿主step演化',
            completion_state_ids=['cycle.phase'] if asynchronous else [],timeout_s=None,cancellation='supported' if asynchronous else 'unsupported',retry='same_request_id',resources=['host_clock','source','samples','vacuum','request_cache'],
            partial_failure_zh='取消保留已沉积样品/源消耗与热量，不能回滚物质；仅进程内请求去重。'),
          errors=[dict(code=e,meaning_zh=TERM_ERROR_ZH.get(e,e) if term or pan else e,side_effects='none') for e in (TERM_ERRORS_OF[k] if term else ['PRECONDITION_FAILED','INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','REQUEST_CONFLICT'] if pan else ['INVALID_ARGUMENT','UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','PRECONDITION_FAILED','BUSY','ACCESS_DENIED','REQUEST_CONFLICT','UNKNOWN_REFERENCE','UNSUPPORTED_SOURCE','FROZEN_CR_TARGET','FTM_OPTION_REQUIRED','OPEN_TO_ADJUST_STAGE','VENT_BEFORE_OPEN','SOURCE_EMPTY']+(FAULT_CODES if k=='acquisition.start' else []))],
          example_arguments=copy.deepcopy(ex),verification=[],limitations=TERM_LIMITS.get(k,['面板层按panel_ui.json（手册页码证据）解释手势并调用本运行层的同名能力；手势不推进仿真时钟。'] if pan else ['有限逐源质量/气体/电热模型；非厂家固件/标定/配方建议']),
          source_ids=['manufacturer_manual_issue7','simulation_assumption'] if term or pan else ['manufacturer_primary','simulation_assumption'])


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
      'Carbon chopped short rods plus remaining300mm stock: lumped ramp (method.configure) or the manual ramped-carbon and pulse-rod current profiles (profile runs); no carbon cord, no certified safety controls, power-supply trip above 60-65 A pulses not modelled.',
      'Touchscreen software 1.1.1: profiles, materials, system editor, user levels, clock and Run Profile after the Issue 7 manual (the Plus redesign is not documented locally); pulse cord, controlled pulse, metal evaporation and aperture cleaning processes are not run (inserts not fitted); USB firmware, robot, physical attachment/collision/seal and real hardware absent.'])
    spec=importlib.util.spec_from_file_location('_quorum_annotations',ROOT/'scripts/asset_runtime/_annotations.py');ann=importlib.util.module_from_spec(spec);spec.loader.exec_module(ann)
    ann.KEY_ZH.update(SLOT_ZH);ann.TRUTH_ZH.update(SLOT_ZH)
    ann.annotate_export(SLUG,caps,slots,{'coating':mech},inputs={'plant.configure':(PLANT,PLANT_EXAMPLE),'source.install':(SOURCE,SOURCE_EXAMPLE),'samples.load':(SAMPLES,SAMPLES_EXAMPLE),'method.configure':(METHOD,METHOD_EXAMPLE)})
    for k,v in slots.items():
        v['meaning_zh']=SLOT_ZH[k]+'；来源='+v['origin']+'，非实机标定。'
        if v['unit']=='Pa':v['reference_conditions']='腔体绝对压力；5e-5mbar=0.005Pa极限，不是工作气压'
        if v['unit']=='m':v['reference_conditions']='SI实际几何位移或基于晶体质量的理想膜厚，见语义'
    mech['parameters']={k.lower():v for k,v in mech['parameters'].items()}
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'coating':mech})


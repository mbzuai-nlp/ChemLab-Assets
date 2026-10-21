"""S-300 Basic water-only drying: conservative cohorts, explicitly uncalibrated.

1.1: the controller functions of the touch screen (control-screen tiles with their own set values and start / stop, deblock, run records,
pump calibration, settings, date and time, notifications and logs, counters) and the front panel itself (panel.touch / press / turn / switch /
capture / frame; panel_ui.json beside the contract, generic engine _panel_ui.py)."""
from __future__ import annotations
import copy,json,math
from asset_runtime import _annotations as _ann
from pathlib import Path
from asset_runtime.mt_hx204 import ActionRejected,number,obj,BOOL,TEXT
from asset_runtime.antonpaar_abbematadvanced import validate
ROOT=Path(__file__).resolve().parents[2];ASSET_TYPE_ID='instrument.buchi_minispray';CATALOG_ID='CHEM-026';PROTOCOL_VERSION='1.0.0';TEMPLATE_REL='assets/reactions/buchi_minispray_drying_library/templates.json'
PHASE={'type':'string','enum':['idle','running','succeeded','cancelled','failed']}
def arr(s):return {'type':'array','items':s}
def nullable(s):return {'anyOf':[s,{'type':'null'}]}
MASS=number('kg',0);FRAC=number('1',0,1);TEMP=number('K',200,700);FLOW=number('m^3/s',.1e-6/60,30e-6/60)
PLANT=obj({'ambient_k':number('K',278.15,313.15),'gas_reference_k':number('K',273.15,313.15),'gas_reference_pa':number('Pa',80000,120000),'gas_density_kg_m3':number('kg/m^3',.5,2),'gas_cp_j_kg_k':number('J/kg/K',700,1500),'heater_power_w':number('W',1,2300),'auxiliary_power_w':number('W',1,2300),'thermal_tau_s':number('s',.5,600),'chamber_volume_m3':number('m^3',.0001,.03),'water_density_kg_m3':number('kg/m^3',900,1100),'latent_j_kg':number('J/kg',2e6,2.6e6),'max_humidity_gain_kg_kg':number('kg/kg',.001,.5),'d2_rate_m2_s':number('m^2/s',1e-13,1e-7),'wall_rate_s_inv':number('1/s',0,10),'cyclone_d50_m':number('m',1e-7,1e-4),'filter_efficiency':FRAC,'filter_resistance_pa_kg':number('Pa/kg',0,1e10),'gas_supply_pa':number('Pa',6.5e5,7e5),'gas_pressure_basis':{'type':'string','enum':['manufacturer_unspecified']},'gas_connected':BOOL,'recipe_reviewed':BOOL,'coefficient_evidence':TEXT})
EXAMPLE=dict(ambient_k=298.15,gas_reference_k=298.15,gas_reference_pa=101325.,gas_density_kg_m3=1.184,gas_cp_j_kg_k=1005.,heater_power_w=2000.,auxiliary_power_w=300.,thermal_tau_s=3.,chamber_volume_m3=.008,water_density_kg_m3=997.,latent_j_kg=2.3e6,max_humidity_gain_kg_kg=.1,d2_rate_m2_s=3e-9,wall_rate_s_inv=.04,cyclone_d50_m=4e-6,filter_efficiency=.95,filter_resistance_pa_kg=1e7,gas_supply_pa=6.7e5,gas_pressure_basis='manufacturer_unspecified',gas_connected=True,recipe_reviewed=True,coefficient_evidence='synthetic_host_parameters_not_S300_calibration')
SAMPLE=obj({'sample_id':TEXT,'substance_id':TEXT,'solvent':{'type':'string','enum':['water']},'volume_m3':number('m^3',1e-9,.01),'solid_mass_fraction':number('1',.001,.5),'solid_density_kg_m3':number('kg/m^3',200,5000),'damage_rate_ref_s_inv':number('1/s',0,100),'damage_reference_k':TEMP,'damage_activation_j_mol':number('J/mol',0,2e5),'sticky_temperature_k':TEMP,'homogeneous_feed_reviewed':BOOL})
SAMPLE_EXAMPLE=dict(sample_id='feed_1',substance_id='d_lactose',solvent='water',volume_m3=50e-6,solid_mass_fraction=.1,solid_density_kg_m3=1500.,damage_rate_ref_s_inv=.001,damage_reference_k=350.,damage_activation_j_mol=40000.,sticky_temperature_k=360.,homogeneous_feed_reviewed=True)
METHOD=obj({'inlet_target_k':number('K',303.15,493.15),'feed_m3_s':FLOW,'drying_gas_m3_s':number('m^3/s',1e-4,35/3600),'spray_gas_m3_s':number('m^3/s',80e-3/3600,1800e-3/3600),'nozzle':{'type':'string','enum':['two_fluid']},'needle_diameter_m':{'type':'number','enum':[.0005,.0007,.0014,.002],'x-unit':'m'},'atom_d_ref_m':number('m',1e-6,200e-6),'atom_reference_feed_m3_s':FLOW,'atom_reference_gas_m3_s':number('m^3/s',80e-3/3600,1800e-3/3600)})
METHOD_EXAMPLE=dict(inlet_target_k=423.15,feed_m3_s=5e-6/60,drying_gas_m3_s=25/3600,spray_gas_m3_s=600e-3/3600,nozzle='two_fluid',needle_diameter_m=.0007,atom_d_ref_m=30e-6,atom_reference_feed_m3_s=5e-6/60,atom_reference_gas_m3_s=600e-3/3600)
POINT=obj({'time_s':number('s',0),'inlet_k':TEMP,'outlet_k':TEMP,'feed_m3_s':number('m^3/s',0),'evaporation_kg_s':number('kg/s',0),'filter_delta_pa':number('Pa',0)})
RESULT=obj({'phase':PHASE,'fault':TEXT,'inlet_k':nullable(TEMP),'outlet_k':nullable(TEMP),'product_gas_temperature_k':{'type':'null'},'drying_gas_m3_s':number('m^3/s',0),'evaporation_kg_s':number('kg/s',0),'filter_delta_pa':number('Pa',0),'filter_maintenance_due':BOOL,'evidence':TEXT})
PRODUCT=obj({'sample_id':TEXT,'dry_solids_kg':MASS,'water_kg':MASS,'yield_dry_solid_fraction':nullable(FRAC),'active_fraction':nullable(FRAC),'water_mass_fraction':nullable(FRAC),'mass_weighted_particle_diameter_m':nullable(number('m',0)),'wet_reject_kg':MASS,'wall_mass_kg':MASS,'filter_mass_kg':MASS,'exhaust_particle_mass_kg':MASS,'evidence':TEXT,'template_source':TEXT})
REFERENCE=obj({'substance_id':TEXT,'glass_former':BOOL,'stable_on_storage':nullable(BOOL),'glass_transition_k':TEMP,'melting_k':TEMP,'tg_evidence':TEXT,'conditions':TEXT,'source':TEXT,'template_source':TEXT,'s300_yield_fraction':{'type':'null'},'s300_particle_diameter_m':{'type':'null'},'evidence':TEXT})
# ---------------------------------------------------------------- device functions behind the touch screen (operation manual S-300 11594255 C, 'om p.N')
# Control-screen tiles with their own set values and start / stop (drying gas, inlet temperature, dispersion gas, pump 1, deblock), run
# records (Runs panel), pump calibration, system / customize settings, date and time, notifications and logs, counters.  The 1.0 commands keep
# their behaviour (drying.prepare still starts drying gas, dispersion gas and heating together).
import datetime as _dt
LMIN=1e-6/60;LH=1e-3/3600;M3H=1/3600
CLOCK_EPOCH=_dt.datetime(2026,1,1,8,0)                                         # the controller clock at the first plug-in (assumption)
LANGUAGES=['English','Deutsch','Francais','Italiano','Espanol']
FOCUS=['Product Temperature','Spray Gas','Drying Gas','Filter Blockage','Filter Pressure','Outlet Temperature','Inlet Temperature']   # om p.33
TIME_ZONES=[f'UTC{h:+d}' if h else 'UTC' for h in range(-12,15)]
DIMMER=['Off','1 min','5 min','10 min','30 min']
DIM_FRACTION=.3                                                                 # Dimmer: brightness after the idle time, fraction of the setting (assumption)
TZ_FACTORY_H=1                                                                  # CLOCK_EPOCH and the controller clock are local time of the factory zone UTC+1
SETTINGS={'display.dark_mode':dict(type='bool',default=False),'display.brightness':dict(type='int',min=10,max=100,default=80),
          'display.dimmer':dict(type='enum',options=DIMMER,default='5 min'),'sound.volume':dict(type='int',min=0,max=100,default=50),
          'sound.key_clicks':dict(type='bool',default=True),'datetime.automatic':dict(type='bool',default=False),'datetime.time_zone':dict(type='enum',options=TIME_ZONES,default='UTC+1'),
          'localization.language':dict(type='enum',options=LANGUAGES,default='English'),'units.temperature':dict(type='enum',options=['C','F'],default='C'),
          'units.pressure':dict(type='enum',options=['metric','imperial'],default='metric'),'panel.show_pressure':dict(type='enum',options=['Filter Blockage','Filter Pressure'],default='Filter Blockage'),
          'focus.1':dict(type='enum',options=FOCUS,default='Inlet Temperature'),'focus.2':dict(type='enum',options=FOCUS,default='Outlet Temperature'),
          'focus.3':dict(type='enum',options=FOCUS,default='Drying Gas'),'report.company_address':dict(type='text',max_len=120,default='')}   # om p.31-34
DEFAULT_SP=dict(drying_gas_m3_s=25*M3H,inlet_target_k=423.15,spray_gas_m3_s=600*LH,feed_m3_s=5*LMIN)    # tiles before a recipe is written (= METHOD_EXAMPLE)
BLOCKAGE_FULL_PA=2000.                                                          # 100 % filter blockage = the maintenance level of the model (assumption)
FREE={'type':'object'};INT=lambda lo,hi:{'type':'integer','minimum':lo,'maximum':hi,'x-unit':'1'}
GAS_SP=number('m^3/s',1e-4,35*M3H);INLET_SP=number('K',303.15,493.15);SPRAY_SP=number('m^3/s',80*LH,1800*LH)
DEVICE_CAPS={
 'drying_gas.set':(obj({'drying_gas_m3_s':GAS_SP,'enabled':BOOL,'keep_recording':BOOL},optional=('drying_gas_m3_s','enabled','keep_recording')),obj({'drying_gas':FREE}),['aspirator'],'控制屏Drying gas：抽气机流量设定（m3/h）与启停；停止规则同airflow.stop（加热关、无飞行液滴、入口已冷却）；keep_recording=true为“停气但不停止记录”（om p.30、56-57）','device_command','agent',{'drying_gas_m3_s':25*M3H,'enabled':True},['airflow.enabled','flow.drying_m3_s','tiles.setpoints','runs.list']),
 'heating.set':(obj({'inlet_target_k':INLET_SP,'enabled':BOOL},optional=('inlet_target_k','enabled')),obj({'heating':FREE}),['heater'],'控制屏Inlet T：入口温度设定与加热启停；只在抽气机运行时可开加热（om p.30、56）','device_command','agent',{'inlet_target_k':423.15,'enabled':True},['heater.enabled','tiles.setpoints']),
 'dispersion_gas.set':(obj({'spray_gas_m3_s':SPRAY_SP,'enabled':BOOL},optional=('spray_gas_m3_s','enabled')),obj({'dispersion_gas':FREE}),['nozzle'],'控制屏Disper. gas：雾化气流量设定（L/h）与启停；雾化气关时泵送的液体从喷嘴滴落（湿废料）（om p.30、67）','device_command','agent',{'spray_gas_m3_s':600*LH,'enabled':True},['tiles.setpoints','tiles.dispersion_on']),
 'pump.set':(obj({'feed_m3_s':FLOW}),obj({'feed_m3_s':FLOW}),['feed_pump'],'控制屏Pump 1：蠕动泵流量设定（mL/min，按泵校准修正）；泵的启停为acquisition.start / acquisition.stop（om p.30）','device_command','agent',{'feed_m3_s':5*LMIN},['tiles.setpoints','flow.feed_m3_s']),
 'deblock.set':(obj({'per_min':INT(0,60)}),obj({'per_min':INT(0,60)}),['nozzle'],'控制屏Deblock：喷嘴清洁频率（次/min，0=关），雾化气开时计脉冲（om p.30）；喷嘴堵塞未建模','device_command','agent',{'per_min':10},['deblock.per_min','deblock.pulses']),
 'pump.calibrate':(obj({'volume_m3':number('m^3',1e-6,100e-6),'time_s':number('s',10,600)}),obj({'calibration':FREE}),['feed_pump'],'泵校准（Instrument > Calibrations）：按校准体积/时间运行泵，把校准液送入量筒（不进入干燥器）；屏幕显示按目标体积/时间的进度，实际送入量只能在量筒上读（calibration.cylinder_m3）（om p.61-62）','device_command','agent',{'volume_m3':10e-6,'time_s':60.},['pump.calibration','calibration.cylinder_m3']),
 'pump.set_calibration':(obj({'difference_m3':number('m^3',-100e-6,100e-6)}),obj({'calibration':FREE}),['feed_pump'],'输入目标值与实际值之差（目标−实际，按量筒读数）：仪器据此修正泵流量（om p.62）','device_command','agent',{'difference_m3':0.},['pump.calibration']),
 'pump.configure_error':(obj({'delivery_factor':number('1',.5,1.5)}),obj({'delivery_factor':number('1')}),['feed_pump'],'评测者：泵管实际输送系数（默认1即理想；由泵校准修正）','configuration','evaluator',{'delivery_factor':1.},['pump.calibration']),
 'runs.save':(obj({}),obj({'run':FREE}),['display_panel'],'保存（结束）当前运行记录（om p.57“If necessary save the run”）','device_command','agent',{},['runs.list']),
 'runs.delete':(obj({'run_id':INT(1,100000)}),obj({'deleted':BOOL}),['display_panel'],'Runs > Options > Delete：删除一条运行记录（om p.60）','device_command','agent',{'run_id':1},['runs.list']),
 'settings.configure':(obj({'key':{'type':'string','enum':list(SETTINGS)},'value':{'anyOf':[TEXT,{'type':'number','x-unit':'1'},BOOL]}}),obj({'settings':FREE}),['display_panel'],'System与Customize设定：显示（暗色、亮度、调暗）、声音、日期时间（自动、时区）、语言与单位、控制屏压力显示与聚焦参数、报告地址（om p.31-34）','configuration','agent',{'key':'units.temperature','value':'C'},['device.settings']),
 'clock.set':(obj({'date':TEXT,'time':TEXT}),obj({'datetime':TEXT}),['display_panel'],'Set Date：设定控制器日期（YYYY-MM-DD）与时间（HH:MM）（只在自动日期时间关时显示；om p.32）','device_command','agent',{'date':'2026-10-19','time':'09:30'},['controller.datetime']),
 'notifications.acknowledge':(obj({}),obj({'acknowledged':INT(0,1000)}),['display_panel'],'确认通知（通知图标消失，记录保留在Logs）','device_command','agent',{},['notifications.active'])}
NEW_ERRORS={'NOT_RUNNING':'aspirator未运行（加热需要气流）','NO_RECIPE':'宿主雾化/喷嘴配方（method.configure）尚未声明','SETTING_INVALID':'设定值不在范围或选项内','NO_RUN':'没有该运行记录或没有打开的记录','CALIBRATION_BUSY':'校准进行中或尚未完成'}
NEW_CAP_ERRORS={'drying_gas.set':['PRECONDITION_FAILED','NO_RECIPE'],'heating.set':['NOT_RUNNING','PRECONDITION_FAILED'],'dispersion_gas.set':['PRECONDITION_FAILED'],'pump.set':[],
                'deblock.set':[],'pump.calibrate':['BUSY','CALIBRATION_BUSY','PRECONDITION_FAILED'],'pump.set_calibration':['CALIBRATION_BUSY'],'pump.configure_error':[],'runs.save':['NO_RUN'],
                'runs.delete':['NO_RUN','BUSY'],'settings.configure':['SETTING_INVALID'],'clock.set':['INVALID_ARGUMENT','PRECONDITION_FAILED'],'notifications.acknowledge':[]}
NEW_PRE={'drying_gas.set':('启动：供电、宿主配方已声明、夹具与集粉瓶闭合；停止：加热已关、无飞行液滴、入口已冷却到环境+5 K',['power.enabled','method.configured','clamp.lift_m','vessel.lower_m','heater.enabled']),
         'heating.set':('开加热：供电且抽气机运行',['power.enabled','airflow.enabled']),'dispersion_gas.set':('开雾化气：供电、气源已连接',['power.enabled','plant.configured']),
         'pump.calibrate':('供电、未在进料运行、无进行中的校准',['power.enabled','cycle.phase','pump.calibration']),'pump.set_calibration':('校准已运行完毕',['pump.calibration']),
         'runs.delete':('记录存在且不是正在记录的运行',['runs.list']),'clock.set':('自动日期时间关、日期有效',['device.settings'])}
RUN_SETPOINTS={'drying_gas.set','heating.set','dispersion_gas.set','pump.set','deblock.set'}   # tiles that start, stop or change a run (caller rules)
RUN_OK={'power.set','acquisition.stop','drying.shutdown','drying_gas.set','heating.set','dispersion_gas.set','pump.set','deblock.set','runs.save','settings.configure','clock.set',
        'notifications.acknowledge','pump.configure_error'}
NOTICE_TEXT={'filter_blockage':'Filter blockage high: clean the outlet filter','calibration_done':'Pump calibration finished: enter the difference'}


class _S300Device:
    """Controller state and commands of the S-300 touch screen (mixed into BuchiMinisprayRuntime)."""
    def _device_init(self):
        self.sp=dict(DEFAULT_SP);self.spray=False;self.deblock=0;self.deblock_pulses=0.;self.pump_factor=1.;self.pump_corr=1.
        self.cal=dict(state='idle',volume_m3=None,time_s=None,elapsed_s=0.,delivered_m3=0.);self.settings={k:v['default'] for k,v in SETTINGS.items()}
        self.clock=dict(base_s=0.,ref_t=0.);self.runs=[];self._run_id=0;self.run_open=None;self.notices=[];self.logs=[];self._notice_id=0
        self.counters=dict(operating_s=0.,aspirator_s=0.,heater_s=0.,pump_s=0.);self.power_epoch=0;self._dp_high=False
        self.cylinder_m3=0.;self._run_owners={};self._last_touch=0.;self._caller=None
    # ---- clock, notifications, runs
    def _tz_h(self):
        z=self.settings['datetime.time_zone'];return int(z[3:]) if len(z)>3 else 0
    def _clock_now(self):
        """Local controller time: the clock runs in the factory zone UTC+1; Select Time Zone shifts the shown time by the zone difference (om p.32)."""
        return CLOCK_EPOCH+_dt.timedelta(seconds=self.clock['base_s']+self.time_s-self.clock['ref_t']+3600*(self._tz_h()-TZ_FACTORY_H))
    def _clock_text(self):
        return self._clock_now().strftime('%Y-%m-%dT%H:%M')
    def _notify(self,code):
        self._notice_id+=1;e=dict(id=self._notice_id,code=code,text=NOTICE_TEXT[code],time=self._clock_text());self.notices.append(e);self.logs.append(dict(e))
        self.logs=self.logs[-200:]
    def _run_start(self):
        if self.run_open is not None:return
        self._run_id+=1;self.runs.append(dict(run_id=self._run_id,start=self._clock_text(),end=None,duration_s=0.,max_inlet_k=self.inlet,max_outlet_k=self.outlet,feed_m3=0.))
        if self.__dict__.get('_caller') is not None:self._run_owners[self._run_id]=self._caller          # the record belongs to the session that started it
        self.run_open=len(self.runs)-1
        if len(self.runs)>100:self.runs.pop(0);self.run_open-=1                   # the instrument keeps the last 100 runs (assumption)
    def _run_end(self):
        if self.run_open is not None:self.runs[self.run_open]['end']=self._clock_text();self.run_open=None
    def _device_tick(self,h,fed):
        c=self.counters
        if self.powered:
            c['operating_s']+=h;c['aspirator_s']+=h if self.airflow else 0.;c['heater_s']+=h if self.heating else 0.;c['pump_s']+=h if (self.phase=='running' or self.cal['state']=='running') else 0.
        if self.spray and self.deblock:self.deblock_pulses+=self.deblock*h/60.
        if self.run_open is not None:
            r=self.runs[self.run_open];r['duration_s']+=h;r['max_inlet_k']=max(r['max_inlet_k'],self.inlet);r['max_outlet_k']=max(r['max_outlet_k'],self.outlet);r['feed_m3']+=fed
        high=self.dp>BLOCKAGE_FULL_PA
        if high and not self._dp_high:self._notify('filter_blockage')
        self._dp_high=high
        if self.cal['state']=='running':
            self.cal['elapsed_s']+=h;self.cal['delivered_m3']=self.cal['volume_m3']*min(1.,self.cal['elapsed_s']/self.cal['time_s'])   # the controller's target progress
            self.cylinder_m3+=self.cal['volume_m3']/self.cal['time_s']*self.pump_factor*self.pump_corr*h                       # what the measuring cylinder receives
            if self.cal['elapsed_s']>=self.cal['time_s']-1e-9:self.cal['state']='done';self._notify('calibration_done')
    def _feed_factor(self):
        return self.pump_factor*self.pump_corr
    # ---- commands (called from BuchiMinisprayRuntime.invoke)
    def _setpoint(self,key,v):
        self.sp[key]=v
        if self.method:self.method[key]=v
    def _device_invoke(self,cap,a):
        if cap=='drying_gas.set':
            if 'drying_gas_m3_s' in a:self._setpoint('drying_gas_m3_s',a['drying_gas_m3_s'])
            if a.get('enabled') is True and not self.airflow:
                if not self.powered or not self.plant or self.lift>1e-9 or self.lower>1e-9:raise ActionRejected('PRECONDITION_FAILED')
                if not self.method:raise ActionRejected('NO_RECIPE')
                self.airflow=True;self._run_start()
            elif a.get('enabled') is False and self.airflow:
                if self.heating or self.bins or (self.plant and self.inlet>self.plant['ambient_k']+5):raise ActionRejected('PRECONDITION_FAILED')
                self.airflow=False
                if not a.get('keep_recording'):self._run_end()
            return dict(drying_gas=dict(enabled=self.airflow,drying_gas_m3_s=self.sp['drying_gas_m3_s']))
        if cap=='heating.set':
            if a.get('enabled') is True and not self.heating and (not self.powered or not self.airflow):raise ActionRejected('NOT_RUNNING')
            if 'inlet_target_k' in a:self._setpoint('inlet_target_k',a['inlet_target_k'])
            if 'enabled' in a:self.heating=a['enabled']
            return dict(heating=dict(enabled=self.heating,inlet_target_k=self.sp['inlet_target_k']))
        if cap=='dispersion_gas.set':
            if a.get('enabled') is True and not self.spray and (not self.powered or not self.plant or not self.plant['gas_connected']):raise ActionRejected('PRECONDITION_FAILED')
            if 'spray_gas_m3_s' in a:self._setpoint('spray_gas_m3_s',a['spray_gas_m3_s'])
            if 'enabled' in a:self.spray=a['enabled']
            return dict(dispersion_gas=dict(enabled=self.spray,spray_gas_m3_s=self.sp['spray_gas_m3_s']))
        if cap=='pump.set':
            self._setpoint('feed_m3_s',a['feed_m3_s']);return dict(feed_m3_s=a['feed_m3_s'])
        if cap=='deblock.set':
            if isinstance(a['per_min'],bool) or not isinstance(a['per_min'],int):raise ActionRejected('INVALID_ARGUMENT')
            self.deblock=a['per_min'];return dict(per_min=self.deblock)
        if cap=='pump.calibrate':
            if not self.powered:raise ActionRejected('PRECONDITION_FAILED')
            if self.phase=='running':raise ActionRejected('BUSY')
            if self.cal['state']=='running':raise ActionRejected('CALIBRATION_BUSY')
            self.cal=dict(state='running',volume_m3=a['volume_m3'],time_s=a['time_s'],elapsed_s=0.,delivered_m3=0.);self.cylinder_m3=0.   # an empty measuring cylinder
            return dict(calibration=copy.deepcopy(self.cal))
        if cap=='pump.set_calibration':
            if self.cal['state']!='done':raise ActionRejected('CALIBRATION_BUSY')
            actual=self.cal['volume_m3']-a['difference_m3']
            if actual<=0:raise ActionRejected('INVALID_ARGUMENT')
            self.pump_corr*=self.cal['volume_m3']/actual;self.cal['state']='calibrated';return dict(calibration=dict(copy.deepcopy(self.cal),correction=self.pump_corr))
        if cap=='pump.configure_error':
            self.pump_factor=float(a['delivery_factor']);return dict(delivery_factor=self.pump_factor)
        if cap=='runs.save':
            if self.run_open is None:raise ActionRejected('NO_RUN')
            r=self.runs[self.run_open];self._run_end();return dict(run=copy.deepcopy(r))
        if cap=='runs.delete':
            i=next((j for j,r in enumerate(self.runs) if r['run_id']==a['run_id']),None)
            if i is None:raise ActionRejected('NO_RUN')
            if i==self.run_open:raise ActionRejected('BUSY')
            del self.runs[i]
            if self.run_open is not None and i<self.run_open:self.run_open-=1
            return dict(deleted=True)
        if cap=='settings.configure':
            f=SETTINGS[a['key']];v=a['value']
            ok=(isinstance(v,bool) if f['type']=='bool' else v in f['options'] if f['type']=='enum' else isinstance(v,str) and len(v)<=f['max_len'] if f['type']=='text' else
                (not isinstance(v,bool) and isinstance(v,(int,float)) and float(v).is_integer() and f['min']<=v<=f['max']))
            if not ok:raise ActionRejected('SETTING_INVALID')
            self.settings[a['key']]=int(v) if f['type']=='int' else v;return dict(settings=dict(self.settings))
        if cap=='clock.set':
            if self.settings['datetime.automatic']:raise ActionRejected('PRECONDITION_FAILED')
            try:t=_dt.datetime.strptime(a['date']+' '+a['time'][:5],'%Y-%m-%d %H:%M')
            except (TypeError,ValueError):raise ActionRejected('INVALID_ARGUMENT') from None
            if not 2000<=t.year<=2099:raise ActionRejected('INVALID_ARGUMENT')
            self.clock=dict(base_s=(t-CLOCK_EPOCH).total_seconds()-3600*(self._tz_h()-TZ_FACTORY_H),ref_t=self.time_s);return dict(datetime=self._clock_text())   # local time in the selected zone
        if cap=='notifications.acknowledge':
            n=len(self.notices);self.notices=[];return dict(acknowledged=n)
        raise ActionRejected('UNKNOWN_CAPABILITY')
    def _device_values(self):
        def run(r):return dict(r,open=self.run_open is not None and self.runs[self.run_open] is r)
        return {'tiles.setpoints':dict(self.sp),'tiles.dispersion_on':self.spray,'deblock.per_min':self.deblock,'deblock.pulses':self.deblock_pulses,
                'pump.calibration':dict(self.cal,correction=self.pump_corr),'calibration.cylinder_m3':self.cylinder_m3,'runs.list':[run(r) for r in self.runs],'device.settings':dict(self.settings),
                'controller.datetime':self._clock_text() if self.powered else None,'notifications.active':copy.deepcopy(self.notices),'notifications.logs':copy.deepcopy(self.logs),
                'instrument.counters':dict(self.counters)}


CAPS={
 'power.set':(obj({'enabled':BOOL}),obj({'enabled':BOOL}),['power_inlet'],'供电；断电停泵停气但库存不消失','device_command','agent',{'enabled':True},['power.enabled','cycle.phase','airflow.enabled','heater.enabled','thermal.inlet_k','thermal.outlet_k','flow.drying_m3_s','flow.feed_m3_s']),
 'clamp.set_lift':(obj({'lift_m':number('m',0,.03)}),obj({'lift_m':number('m',0,.03)}),['cylinder_clamp'],'教学夹具行程，冷却停气后才允许拆装','device_command','agent',{'lift_m':.03},['clamp.lift_m']),
 'vessel.set_lower':(obj({'lower_m':number('m',0,.05)}),obj({'lower_m':number('m',0,.05)}),['collection_vessel'],'教学集粉瓶拆卸行程','device_command','agent',{'lower_m':.05},['vessel.lower_m']),
 'plant.configure':(PLANT,obj({'configured':BOOL}),['heater','aspirator','outlet_filter'],'宿主首次物理系数与气源审查','configuration','evaluator',EXAMPLE,['plant.configured']),
 'sample.configure':(SAMPLE,obj({'configured':BOOL,'attachment_event':TEXT}),['feed_port'],'宿主水系单固体样品，一实例一批次','configuration','evaluator',SAMPLE_EXAMPLE,['sample.configured']),
 'method.configure':(METHOD,obj({'configured':BOOL}),['feed_pump','nozzle','heater'],'Basic两流体方法；雾化粒径系数为宿主假设','configuration','evaluator',METHOD_EXAMPLE,['method.configured']),
 'drying.prepare':(obj({}),obj({'preparing':BOOL}),['heater','aspirator','nozzle'],'启动气流和加热，必须step达到温度','device_command','agent',{},['airflow.enabled','heater.enabled']),
 'acquisition.start':(obj({}),obj({'phase':PHASE,'operation_id':TEXT}),['feed_pump','spray_cylinder','cyclone'],'启动/续作，料液耗尽后排空飞行颗粒才完成','device_command','agent',{},['cycle.phase']),
 'acquisition.stop':(obj({}),obj({'phase':PHASE}),['feed_pump'],'取消进料，不冻结热过程或飞行颗粒','device_command','agent',{},['cycle.phase']),
 'drying.shutdown':(obj({}),obj({'cooling':BOOL}),['heater','aspirator'],'停加热保留抽气冷却，不自动清洗','device_command','agent',{},['heater.enabled','cycle.phase']),
 'airflow.stop':(obj({}),obj({'stopped':BOOL}),['aspirator'],'冷却且无飞行颗粒才能停止抽气','device_command','agent',{},['airflow.enabled']),
 'drying.measure':(obj({}),RESULT,['display_panel'],'当前气温流量压差；不把可选产品气温当颗粒温度','query','agent',{},[]),
 'product.read':(obj({}),PRODUCT,['collection_vessel'],'未标定产率/湿度/粒径/活性真值；非实测','query','evaluator',{},[]),
 'history.read':(obj({}),obj({'records':arr(POINT),'truncated':BOOL,'evidence':TEXT}),['display_panel'],'每秒过程记录，读取不推进','query','agent',{},[]),
 'reference.lookup':(obj({'substance_id':TEXT}),REFERENCE,['display_panel'],'50物质原条件参考，不外推S300粒度产率','query','agent',{'substance_id':'d_lactose'},[]),
 'product.unload':(obj({'sample_id':TEXT}),obj({'unloaded':BOOL,'attachment_event':TEXT}),['collection_vessel'],'冷却、停气、降瓶后宿主取出产品','configuration','evaluator',{'sample_id':'feed_1'},['truth.removed_mass_kg'])}
CAPS.update(DEVICE_CAPS)
SLOTS={}
def slot(k,s,u,o='derived',v='agent',c='display_panel',w=None):SLOTS[k]=(s,u,o,v,[c],w)
slot('power.enabled',BOOL,'1','setpoint',c='power_inlet',w=('power.set','enabled'));slot('clamp.lift_m',number('m',0,.03),'m','scene',c='cylinder_clamp',w=('clamp.set_lift','lift_m'));slot('vessel.lower_m',number('m',0,.05),'m','scene',c='collection_vessel',w=('vessel.set_lower','lower_m'))
for k,s,u in [('plant.configured',BOOL,'1'),('sample.configured',BOOL,'1'),('method.configured',BOOL,'1'),('cycle.phase',PHASE,'1'),('cycle.fault',TEXT,'1'),('cycle.elapsed_s',number('s',0),'s'),('airflow.enabled',BOOL,'1'),('heater.enabled',BOOL,'1'),('thermal.inlet_k',nullable(TEMP),'K'),('thermal.outlet_k',nullable(TEMP),'K'),('flow.drying_m3_s',number('m^3/s',0),'m^3/s'),('flow.feed_m3_s',number('m^3/s',0),'m^3/s'),('evaporation.kg_s',number('kg/s',0),'kg/s'),('filter.delta_pa',number('Pa',0),'Pa')]:slot(k,s,u)
for k in ['input','retained','vapor','removed','product','wall','filter','exhaust','wet_reject']:slot('truth.'+k+'_mass_kg',MASS,'kg','process_truth','evaluator','spray_cylinder')
NEW_SLOT_ZH={'tiles.setpoints':'控制屏四个设定值：干燥气、入口温度、雾化气、泵（SI单位；宿主配方声明后同步到方法）','tiles.dispersion_on':'雾化气（Disper. gas）开关',
 'deblock.per_min':'喷嘴清洁频率设定（次/min，0=关）','deblock.pulses':'累计清洁脉冲数（雾化气开时）','pump.calibration':'泵校准：状态、校准体积/时间、已送入量筒体积、修正系数',
 'runs.list':'运行记录（Runs面板）：编号、开始/结束时间、时长、最高入口/出口温度、进料体积、是否正在记录','device.settings':'System与Customize设定',
 'controller.datetime':'控制器日期时间（ISO，随仿真时钟走、按所选时区显示；断电时为空）','calibration.cylinder_m3':'泵校准量筒中的液体体积（操作者读刻度；控制器不知道）','notifications.active':'未确认的通知','notifications.logs':'通知历史（Logs）','instrument.counters':'运行时间计数：通电、抽气机、加热、泵'}
for k,sch,o,c,w in [('tiles.setpoints',FREE,'setpoint','display_panel',None),('tiles.dispersion_on',BOOL,'setpoint','nozzle',('dispersion_gas.set','enabled')),('deblock.per_min',INT(0,60),'setpoint','nozzle',('deblock.set','per_min')),
                    ('deblock.pulses',number('1',0),'derived','nozzle',None),('pump.calibration',FREE,'derived','feed_pump',None),('runs.list',{'type':'array','items':FREE},'derived','display_panel',None),
                    ('device.settings',FREE,'setpoint','display_panel',None),('controller.datetime',nullable(TEXT),'derived','display_panel',None),('notifications.active',{'type':'array','items':FREE},'derived','display_panel',None),
                    ('notifications.logs',{'type':'array','items':FREE},'derived','display_panel',None),('instrument.counters',FREE,'derived','display_panel',None)]:slot(k,sch,'1',o,c=c,w=w)
slot('calibration.cylinder_m3',number('m^3',0),'m^3','scene',c='feed_pump')
# ---------------------------------------------------------------- front panel (panel_ui.json beside the contract, generic engine _panel_ui.py)
ASSET_VERSION='1.2.0';ASSET_DIR=ROOT/'assets/expansion_batch03/t2_vacuum/buchi_minispray'


def _panel_engine():
    try:
        from . import _panel_ui as engine                                   # imported as asset_runtime.buchi_minispray
        return engine
    except ImportError:                                                      # loaded from its file path
        import importlib.util,sys
        if '_chemsim_panel_ui' not in sys.modules:
            spec=importlib.util.spec_from_file_location('_chemsim_panel_ui',Path(__file__).with_name('_panel_ui.py'));sys.modules['_chemsim_panel_ui']=importlib.util.module_from_spec(spec);spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
        return sys.modules['_chemsim_panel_ui']


_panel_ui=_panel_engine()
PANEL_SPEC=_panel_ui.load_spec(ASSET_DIR/'panel_ui.json',capabilities=set(CAPS)) if (ASSET_DIR/'panel_ui.json').is_file() else None
PANEL_CAPS=_panel_ui.capability_definitions(PANEL_SPEC) if PANEL_SPEC else {}
_PANEL_FX=list(dict.fromkeys(e for c in (_panel_ui.spec_capabilities(PANEL_SPEC) if PANEL_SPEC else []) for e in CAPS[c][7]))
for _k,_d in PANEL_CAPS.items():
    CAPS[_k]=(_d['input_schema'],_d['output_schema'],_d['component_ids'],_d['function_zh'],_d['kind'],_d['visibility'],_d['example_arguments'],[] if _d['kind']=='query' else list(_PANEL_FX))


class _S300Panel:
    """Panel-host protocol of the engine (what the S-300 touch screen shows) and the panel.* commands."""
    def panel_time(self):return self.time_s
    def panel_invoke(self,capability_id,arguments):
        try:
            validate(CAPS[capability_id][0],arguments);return self.invoke(capability_id,**arguments)
        except ActionRejected as exc:
            raise _panel_ui.PanelRejected(str(exc)) from None
    def _panel_cmd(self,cap,a):
        if _panel_ui.check_arguments(CAPS[cap][0],a):raise ActionRejected('INVALID_ARGUMENT')
        if cap=='panel.frame':return dict(frame=self.panel.frame())
        if cap=='panel.capture':
            import hashlib,tempfile
            data,w,h=self.panel.png_bytes(a['display_id']);sha=hashlib.sha256(data).hexdigest()
            d=Path(self.panel_capture_dir) if self.panel_capture_dir else Path(tempfile.gettempdir())/'chemistry_sim_panel'/self.instance_id;d.mkdir(parents=True,exist_ok=True)
            path=d/f"{a['display_id']}_{sha[:16]}.png"
            if not path.exists():path.write_bytes(data)
            return dict(display_id=a['display_id'],time_s=self.time_s,image=dict(path=str(path),format='png',width=w,height=h,sha256=sha))
        try:
            if cap=='panel.touch':ok=self.panel.touch(a['display_id'],int(a['x']),int(a['y']),float(a.get('duration_s',.1)),(int(a['to_x']),int(a['to_y'])) if 'to_x' in a else None)
            elif cap=='panel.press':ok=self.panel.press(a['input_id'],float(a.get('duration_s',.1)))
            elif cap=='panel.turn':ok=self.panel.turn(a['input_id'],int(a['detents']))
            else:ok=self.panel.switch(a['input_id'],a['position'])
        except _panel_ui.PanelError:
            raise ActionRejected('INVALID_ARGUMENT') from None
        self._last_touch=self.time_s                                       # any gesture on the screen or the controls restarts the dimmer time
        if ok and cap in ('panel.touch','panel.press') and self.settings['sound.key_clicks']:self.key_clicks+=1
        return dict(accepted=bool(ok))
    def _brightness(self):
        """Shown brightness (%): the Brightness setting, reduced to DIM_FRACTION of it once nothing has been touched for the Dimmer time (om p.31)."""
        if not self.powered:return 0
        b,d=self.settings['display.brightness'],self.settings['display.dimmer']
        return int(round(b*DIM_FRACTION)) if d!='Off' and self.time_s-self._last_touch>=60*int(d.split()[0])-1e-9 else b
    def _t(self,k):
        if k is None:return None
        return (k-273.15)*1.8+32 if self.settings['units.temperature']=='F' else k-273.15
    def panel_read(self):
        out=self.read('agent');F=self.settings['units.temperature']=='F';imp=self.settings['units.pressure']=='imperial';sp=self.sp
        tu='°F' if F else '°C';fmt_t=lambda k:'---' if (k is None or not self.powered) else f'{self._t(k):.0f} {tu}'  # noqa: E731
        dp=self.dp;press=f'{dp/6894.757:.2f} psi' if imp else f'{dp/100:.1f} mbar';block=f'{min(100.,dp/BLOCKAGE_FULL_PA*100):.0f} %'
        filt=block if self.settings['panel.show_pressure']=='Filter Blockage' else press
        focus_val={'Product Temperature':'---','Spray Gas':f"{sp['spray_gas_m3_s']/LH:.0f} L/h",'Drying Gas':f"{sp['drying_gas_m3_s']/M3H:.1f} m3/h",
                   'Filter Blockage':block,'Filter Pressure':press,'Outlet Temperature':fmt_t(self.outlet),'Inlet Temperature':fmt_t(self.inlet)}
        hist=self.history[-600:]
        now=self._clock_now()
        out.update({'host.power_epoch':self.power_epoch,'host.brightness':self._brightness(),'host.clock':now.strftime('%H:%M'),'host.date':now.strftime('%Y-%m-%d'),
                    'host.tiles':dict(drying=f"{sp['drying_gas_m3_s']/M3H:.1f}",inlet=f"{self._t(sp['inlet_target_k']):.0f}",spray=f"{sp['spray_gas_m3_s']/LH:.0f}",
                                      pump=f"{sp['feed_m3_s']/LMIN:.1f}",deblock=str(self.deblock),outlet=fmt_t(self.outlet),inlet_actual=fmt_t(self.inlet),filter=filt,
                                      temp_unit=tu,product='---'),
                    'host.sp_display':dict(drying=sp['drying_gas_m3_s']/M3H,inlet=round(self._t(sp['inlet_target_k']),1),spray=sp['spray_gas_m3_s']/LH,pump=sp['feed_m3_s']/LMIN),
                    'host.pumping':self.phase=='running','host.recording':self.run_open is not None,
                    'host.focus':[dict(name=self.settings[f'focus.{i}'],value=focus_val[self.settings[f'focus.{i}']]) for i in (1,2,3)],
                    'host.graph':dict(inlet=[[p['time_s'],self._t(p['inlet_k'])] for p in hist],outlet=[[p['time_s'],self._t(p['outlet_k'])] for p in hist]),
                    'host.runs':[dict(run_id=r['run_id'],start=r['start'].replace('T',' '),duration=f"{int(r['duration_s'])//60} min",max_inlet=fmt_t(r['max_inlet_k']),
                                      feed=f"{r['feed_m3']*1e6:.1f} mL",state='recording' if (self.run_open is not None and self.runs[self.run_open] is r) else 'saved') for r in reversed(self.runs)],
                    'host.notice_count':len(self.notices),'host.calibration':dict(self.cal,delivered=f"{self.cal['delivered_m3']*1e6:.2f} mL",correction=f'{self.pump_corr:.3f}'),
                    'host.counters':{k:f'{int(v)//3600} h {int(v)%3600//60:02d} min' for k,v in self.counters.items()},'host.temp_unit':tu})
        return out


class BuchiMinisprayRuntime(_S300Device,_S300Panel):
 def __init__(self,instance_id='minispray_01'):
  self.instance_id=instance_id;self.time_s=0.;self.revision=0;self.powered=False;self.lift=0.;self.lower=0.;self.plant=None;self.sample=None;self.method=None;self.phase='idle';self.fault='';self.elapsed_s=0.;self.operation_id=None;self._operation_count=0;self.airflow=False;self.heating=False;self.inlet=298.15;self.outlet=298.15;self.stock={'s':0.,'w':0.,'a':0.};self.bins=[];self.pools={k:dict(s=0.,w=0.,a=0.) for k in ['product','wall','filter','exhaust','wet_reject','removed']};self.input_mass=0.;self.initial_solid=0.;self.vapor=0.;self.evap_rate=0.;self.dp=0.;self.diameter_sum=0.;self.unloaded=False;self.history=[];self.history_truncated=False;self.next_sample=0.;self.templates={t['substance_id']:t for t in json.loads((ROOT/TEMPLATE_REL).read_text())['templates']};self._principals={'__public_agent__':'agent'};self._requests={};self._owners={}
  self._device_init();self.key_clicks=0;self._fed_m3=0.;self.panel_capture_dir=None;self.panel=_panel_ui.PanelUI(PANEL_SPEC,self) if PANEL_SPEC else None
 def register_principal(self,principal_id,visibility='agent'):
  if visibility not in ('agent','evaluator','debug'):raise ActionRejected('INVALID_ARGUMENT')
  if principal_id in self._principals:raise ActionRejected('REQUEST_CONFLICT')
  self._principals[principal_id]=visibility
 def session(self,principal_id='__public_agent__'):
  if principal_id not in self._principals:raise ActionRejected('ACCESS_DENIED')
  return BuchiMinispraySession(self,principal_id)
 def describe(self):return self.session().describe(instance_id=self.instance_id)
 @staticmethod
 def mass(p):return p['s']+p['w']
 def _inventory(self):
  v={k:self.mass(p) for k,p in self.pools.items()};return dict(input=self.input_mass,vapor=self.vapor,retained=self.mass(self.stock)+sum(self.mass(b) for b in self.bins)+sum(v[k] for k in ('product','wall','filter','wet_reject')),**{k:v[k] for k in v})
 def _values(self):
  v={'power.enabled':self.powered,'clamp.lift_m':self.lift,'vessel.lower_m':self.lower,'plant.configured':self.plant is not None,'sample.configured':self.sample is not None,'method.configured':self.method is not None,'cycle.phase':self.phase,'cycle.fault':self.fault,'cycle.elapsed_s':self.elapsed_s,'airflow.enabled':self.airflow,'heater.enabled':self.heating,'thermal.inlet_k':self.inlet if self.powered else None,'thermal.outlet_k':self.outlet if self.powered else None,'flow.drying_m3_s':self.method['drying_gas_m3_s'] if self.airflow else 0.,'flow.feed_m3_s':self.method['feed_m3_s'] if self.phase=='running' and self.mass(self.stock)>1e-18 else 0.,'evaporation.kg_s':self.evap_rate,'filter.delta_pa':self.dp};v.update({'truth.'+k+'_mass_kg':x for k,x in self._inventory().items()});v.update(self._device_values());return v
 def read(self,visibility='agent'):return copy.deepcopy({k:v for k,v in self._values().items() if SLOTS[k][3]=='agent' or visibility in ('evaluator','debug')})
 def debug_snapshot(self):return copy.deepcopy(dict(state=self.read('evaluator'),stock=self.stock,bins=self.bins,pools=self.pools))
 def _measure(self):return dict(phase=self.phase,fault=self.fault,inlet_k=self.inlet if self.powered else None,outlet_k=self.outlet if self.powered else None,product_gas_temperature_k=None,drying_gas_m3_s=self.method['drying_gas_m3_s'] if self.airflow else 0.,evaporation_kg_s=self.evap_rate,filter_delta_pa=self.dp,filter_maintenance_due=self.dp>2000.,evidence='synthetic_host_drying_model; gas temperatures, no optional product sensor')
 def _product(self):
  p=self.pools['removed'] if self.unloaded else self.pools['product'];m=self.mass(p);return dict(sample_id=self.sample['sample_id'] if self.sample else '',dry_solids_kg=p['s'],water_kg=p['w'],yield_dry_solid_fraction=p['s']/self.initial_solid if self.initial_solid else None,active_fraction=p['a']/p['s'] if p['s'] else None,water_mass_fraction=p['w']/m if m else None,mass_weighted_particle_diameter_m=self.diameter_sum/p['s'] if p['s'] else None,wet_reject_kg=self.mass(self.pools['wet_reject']),wall_mass_kg=self.mass(self.pools['wall']),filter_mass_kg=self.mass(self.pools['filter']),exhaust_particle_mass_kg=self.mass(self.pools['exhaust']),evidence='synthetic_conservative_cohort_model; NOT S300 measured yield or morphology',template_source=TEMPLATE_REL)
 def invoke(self,capability,**a):
  if capability not in CAPS:raise ActionRejected('UNKNOWN_CAPABILITY')
  validate(CAPS[capability][0],a)
  if capability in PANEL_CAPS:
   out=self._panel_cmd(capability,a)
   if CAPS[capability][4]!='query':self.revision+=1
   return out
  if capability in DEVICE_CAPS and _panel_ui.check_arguments(CAPS[capability][0],a):raise ActionRejected('INVALID_ARGUMENT')
  self._caller_guard(capability,a)
  if self.phase=='running' and CAPS[capability][4]!='query' and capability not in RUN_OK:raise ActionRejected('BUSY')
  if capability in DEVICE_CAPS:
   out=self._device_invoke(capability,a);self.revision+=1;return copy.deepcopy(out)
  if capability=='power.set':
   if a['enabled'] and not self.powered:self.power_epoch+=1;self._last_touch=self.time_s
   if not a['enabled'] and self.powered:
    self.spray=False;self._run_end()
    if self.cal['state']=='running':self.cal['state']='idle'
   self.powered=a['enabled']
   if not self.powered:
    self.airflow=False;self.heating=False
    if self.phase=='running':self.phase='cancelled'
   out=a
  elif capability in ('clamp.set_lift','vessel.set_lower'):
   ta=self.plant['ambient_k'] if self.plant else 298.15
   if self.airflow or self.heating or self.bins or self.inlet>ta+5:raise ActionRejected('PRECONDITION_FAILED')
   if capability=='clamp.set_lift':self.lift=a['lift_m']
   else:self.lower=a['lower_m']
   out=a
  elif capability=='plant.configure':
   if self.plant or self.sample or not a['recipe_reviewed'] or not a['gas_connected'] or a['heater_power_w']+a['auxiliary_power_w']>2300 or not a['coefficient_evidence']:raise ActionRejected('PRECONDITION_FAILED')
   self.plant=copy.deepcopy(a);self.inlet=self.outlet=a['ambient_k'];out={'configured':True}
  elif capability=='sample.configure':
   if not self.plant or self.sample or self.phase!='idle' or not a['sample_id'] or a['substance_id'] not in self.templates or not a['homogeneous_feed_reviewed']:raise ActionRejected('PRECONDITION_FAILED')
   f=a['solid_mass_fraction'];rho=1/(f/a['solid_density_kg_m3']+(1-f)/self.plant['water_density_kg_m3']);m=rho*a['volume_m3'];self.stock=dict(s=m*f,w=m*(1-f),a=m*f);self.input_mass=m;self.initial_solid=m*f;self.sample=copy.deepcopy(a);out=dict(configured=True,attachment_event='host_attach:'+a['sample_id'])
  elif capability=='method.configure':
   if not self.plant or self.airflow or self.bins or self._operation_count:raise ActionRejected('PRECONDITION_FAILED')
   self.method=copy.deepcopy(a);self.sp={k:a[k] for k in DEFAULT_SP};out={'configured':True}
  elif capability=='drying.prepare':
   if not self.powered or not self.plant or not self.method or self.lift>1e-9 or self.lower>1e-9:raise ActionRejected('PRECONDITION_FAILED')
   self.airflow=True;self.heating=True;self.spray=True;self._run_start();out={'preparing':True}
  elif capability=='acquisition.start':
   if not self.powered or not self.heating or not self.airflow or not self.sample or self.unloaded or self.lift>1e-9 or self.lower>1e-9 or self.phase=='failed' or abs(self.inlet-self.method['inlet_target_k'])>3 or (self.mass(self.stock)<1e-18 and not self.bins):raise ActionRejected('PRECONDITION_FAILED')
   if self.cal['state']=='running':raise ActionRejected('BUSY')
   self.phase='running';self._operation_count+=1;self.operation_id=self.instance_id+':'+str(self._operation_count);out=dict(phase=self.phase,operation_id=self.operation_id)
  elif capability=='acquisition.stop':
   if self.phase!='running':raise ActionRejected('PRECONDITION_FAILED')
   self.phase='cancelled';out=dict(phase=self.phase)
  elif capability=='drying.shutdown':
   self.heating=False
   if self.phase=='running':self.phase='cancelled'
   out=dict(cooling=self.airflow)
  elif capability=='airflow.stop':
   if self.heating or self.bins or (self.plant and self.inlet>self.plant['ambient_k']+5):raise ActionRejected('PRECONDITION_FAILED')
   self.airflow=False;self.spray=False;self._run_end();out={'stopped':True}
  elif capability=='drying.measure':return self._measure()
  elif capability=='product.read':return self._product()
  elif capability=='history.read':return dict(records=copy.deepcopy(self.history),truncated=self.history_truncated,evidence='synthetic model 1s snapshots; not manufacturer recorded trace')
  elif capability=='reference.lookup':
   t=self.templates.get(a['substance_id'])
   if not t:raise ActionRejected('TEMPLATE_RANGE_UNAVAILABLE')
   v=t['values'];return dict(substance_id=t['substance_id'],glass_former=v['glass_former'],stable_on_storage=v['stable_on_storage'],glass_transition_k=v['glass_transition_k'],melting_k=v['melting_k'],tg_evidence=t['quantity_evidence']['glass_transition_k'],conditions=json.dumps(t['conditions'],sort_keys=True),source=t['source'],template_source=TEMPLATE_REL,s300_yield_fraction=None,s300_particle_diameter_m=None,evidence=t['evidence'])
  elif capability=='product.unload':
   if not self.sample or a['sample_id']!=self.sample['sample_id'] or self.unloaded or self.phase not in ('succeeded','cancelled') or self.airflow or self.heating or self.bins or self.lower<.049 or self.inlet>self.plant['ambient_k']+5:raise ActionRejected('PRECONDITION_FAILED')
   self.pools['removed']=self.pools['product'];self.pools['product']=dict(s=0.,w=0.,a=0.);self.unloaded=True;out=dict(unloaded=True,attachment_event='host_release:'+a['sample_id'])
  self.revision+=1;return copy.deepcopy(out)
 def _foreign(self,owner):
  c=self.__dict__.get('_caller');return owner is not None and c is not None and c!=owner and self._principals.get(c)=='agent'
 def _caller_guard(self,k,a):
  """An agent may not stop or shut down (touch-screen shutdown) another agent's feed run, end (save, stop the aspirator) or delete another agent's run
  record: direct calls and the same commands from the touch screen (the session sets _caller); unowned runs and evaluators are not restricted.
  1.2.0 (lead rule 10-19): the On/Off master switch (and power.set, which models it) is a hard switch - a physical action that is never refused;
  switching off acts as a power loss."""
  off=False;change=k in RUN_SETPOINTS                                               # set values of the running drying (tiles) also belong to its owner
  if self.phase=='running' and (off or change or k in ('acquisition.stop','drying.shutdown')) and self._foreign(self._owners.get(self.operation_id)):raise ActionRejected('ACCESS_DENIED')
  if self.run_open is not None and (off or change or k in ('runs.save','airflow.stop','drying.shutdown')) \
     and self._foreign(self._run_owners.get(self.runs[self.run_open]['run_id'])):raise ActionRejected('ACCESS_DENIED')
  if k=='runs.delete' and self._foreign(self._run_owners.get(a['run_id'])):raise ActionRejected('ACCESS_DENIED')
 def _diameter(self,b):return (6*(b['w']/self.plant['water_density_kg_m3']+b['s']/self.sample['solid_density_kg_m3'])/(math.pi*b['n']))**(1/3) if b['n'] else 0.
 def _transfer(self,b,key,f):
  p=self.pools[key];f=min(1.,max(0.,f));d=self._diameter(b)
  if key=='product':self.diameter_sum+=b['s']*f*d
  for k in ['s','w','a']:x=b[k]*f;p[k]+=x;b[k]-=x
  b['n']*=1-f
 def _tick(self,h):
  p=self.plant;ta=p['ambient_k'];m=self.method;gas=(m['drying_gas_m3_s'] if self.airflow else 0.)+(m['spray_gas_m3_s'] if self.spray else 0.);self._fed_m3=0.
  pumped=m['feed_m3_s']*h if self.phase=='running' else 0.                  # what the controller counts: set flow x pumping time (it cannot see the real delivery)
  mdot=gas*p['gas_density_kg_m3'];cp=p['gas_cp_j_kg_k'];target=min(m['inlet_target_k'],ta+p['heater_power_w']/max(mdot*cp,1e-12)) if self.heating and self.airflow else ta
  self.inlet+=(target-self.inlet)*-math.expm1(-h/p['thermal_tau_s']);self.evap_rate=0.
  if self.phase=='running' and self.mass(self.stock)>1e-18:
   rho=1/(self.sample['solid_mass_fraction']/self.sample['solid_density_kg_m3']+(1-self.sample['solid_mass_fraction'])/p['water_density_kg_m3']);fraction=min(1.,m['feed_m3_s']*self._feed_factor()*rho*h/self.mass(self.stock));feed={k:self.stock[k]*fraction for k in ['s','w','a']}
   for k in feed:self.stock[k]-=feed[k]
   dref=m['atom_d_ref_m']*(m['feed_m3_s']/m['atom_reference_feed_m3_s'])**.25*(m['atom_reference_gas_m3_s']/m['spray_gas_m3_s'])**.35*(m['needle_diameter_m']/.0007)**.5
   self._fed_m3=(feed['s']+feed['w'])/rho
   for ds,f in ([(.65,.25),(1.,.5),(1.6,.25)] if self.spray else []):
    b={k:feed[k]*f for k in feed};b.update(age=0.,n=6*(b['w']/p['water_density_kg_m3']+b['s']/self.sample['solid_density_kg_m3'])/(math.pi*(dref*ds)**3));self.bins.append(b)
   if not self.spray:                                                        # no dispersion gas: the feed drips from the nozzle (om p.67)
    for k in feed:self.pools['wet_reject'][k]+=feed[k]
  proposals=[]
  for b in self.bins:
   d=self._diameter(b);ddry=(6*b['s']/self.sample['solid_density_kg_m3']/math.pi/b['n'])**(1/3) if b['n'] else 0.;dnew=math.sqrt(max(ddry**2,d*d-p['d2_rate_m2_s']*max(0.,self.inlet-ta)/50*h)) if self.airflow else d
   wnew=max(0.,(dnew**3-ddry**3)*math.pi*b['n']/6*p['water_density_kg_m3']);proposals.append(min(b['w'],max(0.,b['w']-wnew)))
  requested=sum(proposals);energy=mdot*cp*max(0.,self.inlet-ta)*h;limit=min(energy/p['latent_j_kg'],mdot*p['max_humidity_gain_kg_kg']*h);scale=min(1.,limit/requested) if requested else 0.;evap=requested*scale;self.vapor+=evap;self.evap_rate=evap/h;self.outlet=self.inlet-evap*p['latent_j_kg']/max(h*mdot*cp,1e-12) if self.airflow else self.inlet
  keep=[];qactual=gas*self.inlet/p['gas_reference_k'];residence=p['chamber_volume_m3']/qactual if qactual else math.inf
  for b,e in zip(self.bins,proposals):
   b['w']-=e*scale;wet=b['w']/max(self.mass(b),1e-30);tp=ta+(self.outlet-ta)*(.3 if wet>.02 else 1.);s=self.sample;rate=s['damage_rate_ref_s_inv']*math.exp(max(-60.,min(60.,s['damage_activation_j_mol']/8.314462618*(1/s['damage_reference_k']-1/tp))));b['a']*=math.exp(-rate*h)
   if self.airflow:
    b['age']+=h;wall=p['wall_rate_s_inv']*(1+4*(wet>.15)+2*(tp>s['sticky_temperature_k']));self._transfer(b,'wall',-math.expm1(-wall*h))
    if b['age']>=residence:
     if wet>.15:self._transfer(b,'wet_reject',1.)
     else:
      d=self._diameter(b);cut=p['cyclone_d50_m']/math.sqrt(m['drying_gas_m3_s']/(25/3600));eta=d*d/(d*d+cut*cut);self._transfer(b,'product',eta);self._transfer(b,'filter',p['filter_efficiency']);self._transfer(b,'exhaust',1.)
     continue
   if self.mass(b)>1e-30:keep.append(b)
  self.bins=keep;self.dp=p['filter_resistance_pa_kg']*self.mass(self.pools['filter'])*(m['drying_gas_m3_s']/(25/3600)) if self.airflow else 0.
  if self.phase=='running':
   self.elapsed_s+=h
   if self.mass(self.stock)<=1e-18 and not self.bins:self.phase='succeeded'
  self._device_tick(h,pumped)
  self.time_s+=h
  if self.time_s+1e-12>=self.next_sample:
   point=dict(time_s=self.time_s,inlet_k=self.inlet,outlet_k=self.outlet,feed_m3_s=self._values()['flow.feed_m3_s'],evaporation_kg_s=self.evap_rate,filter_delta_pa=self.dp)
   if len(self.history)<10000:self.history.append(point)
   else:self.history_truncated=True
   self.next_sample=self.time_s+1.
 def step(self,dt):
  if isinstance(dt,bool) or not isinstance(dt,(int,float)) or not math.isfinite(dt) or not 0<dt<=3600:raise ValueError('finite dt in(0,3600] required')
  left=float(dt)
  while left>1e-12:
   h=min(left,.05)
   if self.plant and self.method:self._tick(h)
   else:self._device_tick(h,0.);self.time_s+=h
   left-=h
  self.revision+=1;return self.read()
class BuchiMinispraySession:
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
        meta=export_buchi_minispray_contract_metadata()
        return dict(protocol_version=PROTOCOL_VERSION,asset_type_id=ASSET_TYPE_ID,asset_version=ASSET_VERSION,
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
        before=h.operation_id; h._caller=self._principal
        try: result=h.invoke(capability_id,**arguments)
        finally: h._caller=None
        op=result.get('operation_id')
        if op: h._owners[op]=self._principal
        elif capability_id in PANEL_CAPS and h.operation_id and h.operation_id!=before: h._owners[h.operation_id]=self._principal   # a feed run started with the Pump 1 tile belongs to the caller
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

def export_buchi_minispray_contract_metadata():
    caps={}; slots={}
    for key,(schema,unit,origin,visibility,components,write) in SLOTS.items():
        slots[key]=dict(component_ids=components,value_schema=copy.deepcopy(schema),unit=unit,reference_conditions=None,
          meaning_zh=None,origin=origin,visibility=visibility,access='read_write' if write else 'read_only',
          read_binding=dict(backend='host',artifact_id='runtime',accessor=f'BuchiMinispraySession.read(state_ids=[{key!r}])'),
          write_via=dict(capability_id=write[0],argument=write[1],fixed_arguments={}) if write else None,
          sampling=dict(mode='instant',time_domain='simulation',window_description=None),source_ids=['simulation_assumption'])
        if key in NEW_SLOT_ZH:slots[key]['meaning_zh']=NEW_SLOT_ZH[key];slots[key]['source_ids']=['manufacturer_manual_menus','simulation_assumption']
    for key,(inp,out,components,meaning,kind,vis,example,effects) in CAPS.items():
        async_=key=='acquisition.start'; pre=[]
        conditions={
         'acquisition.start':('供电气流加热开启，夹/瓶闭合，入口到设定±3K，未卸样且有剩余库存',['power.enabled','airflow.enabled','heater.enabled','clamp.lift_m','vessel.lower_m','sample.configured'],'powered and warm and assembled and resumable'),
         'plant.configure':('首次、气源和配方审查通过，热+辅机预算<=2300W',['plant.configured'],'not configured and reviewed and power_budget_valid'),
         'sample.configure':('首次水系单物质、宿主确认均匀供液',['sample.configured','plant.configured'],'plant_configured and not sample and water_only'),
         'method.configure':('供气未开且运行尚未开始；Basic两流体',['method.configured','airflow.enabled'],'not flowing and not started'),
         'product.unload':('批次完成或取消、已冷却停气排空、瓶降到位、样品身份正确',['cycle.phase','airflow.enabled','heater.enabled','vessel.lower_m'],'done_or_cancelled and cool and drained and matching_identity')}
        if key in conditions:
            description,states,check=conditions[key]
            pre=[dict(description_zh=description,state_ids=states,enforced_by='runtime',check=check)]
        caps[key]=dict(function_zh=meaning,kind=kind,visibility=vis,component_ids=components,availability='implemented',
          input_schema=copy.deepcopy(inp),output_schema=copy.deepcopy(out),
          implementation=dict(process='implemented',scene='absent',robot='absent',hardware='absent',host='implemented'),
          binding=dict(backend='host',artifact_id='runtime',entrypoint='asset_runtime.buchi_minispray.BuchiMinispraySession.invoke',invocation=f'session.invoke(instance_id="minispray_01", capability_id={key!r}, arguments={example!r}, request_id="unique")'),
          preconditions=pre,state_effects=[dict(state_id=k,timing='immediate',description_zh=meaning) for k in effects],observes=[],
          model_effects=dict(mode='none',description_zh='此处仅声明运行过程；几何联动由宿主另行绑定验证。',binding_ids=[]),
          execution=dict(mode='asynchronous' if async_ else 'synchronous',time_domain='simulation',
            completion_criterion='cycle.phase=succeeded；进料耗尽且所有飞行颗粒排出；取消停止进料但继续残余干燥和冷却' if async_ else '设定写入或观测返回；不推进物理时钟',
            completion_state_ids=['cycle.phase'] if async_ else effects,timeout_s=None,cancellation='supported' if async_ else 'unsupported',
            retry='same_request_id',resources=['target_instance','principal_request_cache'],partial_failure_zh='拒绝不改过程；取消/断电不回滚水/固体；气流开启时剩余液滴继续演化，断电环境冷却但无主动蒸发。去重只在当前进程内存有效。'),
          errors=[dict(code=e,meaning_zh=e,side_effects='none') for e in ['UNKNOWN_INSTANCE','UNKNOWN_CAPABILITY','ACCESS_DENIED','INVALID_ARGUMENT','PRECONDITION_FAILED','BUSY','REQUEST_CONFLICT']],
          example_arguments=copy.deepcopy(example),verification=[],limitations=['工程代理，无真实硬件连接；进程内过滤不能替代文件和进程部署隔离。'],source_ids=['manufacturer_primary','simulation_assumption'])
    evolving=[k for k in slots if k.startswith(('truth.','cycle.','thermal.','flow.','evaporation.','filter.'))]
    for key in ['acquisition.start','plant.configure','sample.configure','product.unload']:
        caps[key]['state_effects'] += [dict(state_id=k,timing='on_step' if key=='acquisition.start' else 'immediate',description_zh='保守液滴水/固体账本；未标定') for k in evolving if k not in [e['state_id'] for e in caps[key]['state_effects']]]
    for key in ['clamp.set_lift','vessel.set_lower']:
        caps[key]['preconditions'].append(dict(description_zh='无气流、无加热、无飞行液滴且入口近环境温度',state_ids=['airflow.enabled','heater.enabled','thermal.inlet_k'],enforced_by='runtime',check='cool and off and drained'))
    caps['reference.lookup']['errors'].append(dict(code='TEMPLATE_RANGE_UNAVAILABLE',meaning_zh='物质没有公开原条件参考',side_effects='none'))
    for key,(desc,states) in NEW_PRE.items():caps[key]['preconditions']=[dict(description_zh=desc,state_ids=states,enforced_by='runtime',check='BuchiMinisprayRuntime._device_invoke guards')]
    for key,codes in NEW_CAP_ERRORS.items():
        have={e['code'] for e in caps[key]['errors']}
        caps[key]['errors']+=[dict(code=c,meaning_zh=NEW_ERRORS.get(c,c),side_effects='none') for c in codes if c not in have]
    dyn=['runs.list','instrument.counters','notifications.active','notifications.logs','deblock.pulses','pump.calibration','controller.datetime']
    for key in ('drying_gas.set','heating.set','dispersion_gas.set','pump.calibrate'):
        caps[key]['state_effects']+=[dict(state_id=k,timing='on_step',description_zh='气流/加热/雾化/校准随step演化') for k in evolving+dyn if k not in [e['state_id'] for e in caps[key]['state_effects']]]
    for key,extra in (('power.set',['tiles.dispersion_on','runs.list','pump.calibration','controller.datetime']),('drying.prepare',['tiles.dispersion_on','runs.list']),
                      ('airflow.stop',['tiles.dispersion_on','runs.list']),('method.configure',['tiles.setpoints'])):
        caps[key]['state_effects']+=[dict(state_id=k,timing='immediate',description_zh='1.1控制屏状态') for k in extra]
    caps['acquisition.start']['state_effects']+=[dict(state_id=k,timing='on_step',description_zh='运行记录、计数与通知') for k in dyn if k not in ('pump.calibration',)]
    for key in DEVICE_CAPS:
        caps[key]['source_ids']=['manufacturer_manual_menus','simulation_assumption'];caps[key]['limitations']=['触摸屏控制器功能的最小模型（操作手册S-300 11594255 C）；喷嘴堵塞、氧含量与闭式模式未建模。']
    called=_panel_ui.spec_capabilities(PANEL_SPEC) if PANEL_SPEC else []
    fx=list(dict.fromkeys(e['state_id'] for c in called for e in caps[c]['state_effects']))
    for key,d in PANEL_CAPS.items():
        e=caps[key];e['source_ids']=['manufacturer_manual_menus','simulation_assumption'];e['limitations']=['前面板只调用本运行层的同名能力（没有第二份仪器逻辑）；画面按手册以平面控件近似绘制，不是像素级屏幕。']
        if d['kind']!='query':
            e['state_effects']=[dict(state_id=k,timing='immediate',description_zh='手势按界面规则调用的能力所改变的状态') for k in fx]
            e['preconditions']=[dict(description_zh='通电后触摸屏与导航旋钮才起作用（断电时只有主开关）；界面规则按panel_ui.json（手册证据）',state_ids=['power.enabled'],enforced_by='runtime',check='panel_ui.json gates')]
            e['execution']['completion_criterion']='手势瞬时完成（不推进仿真时钟）：界面状态与被调用能力的效果立即生效；结果只说明是否被仪器接受，屏幕须用panel.capture查看'
    mechanism=dict(component_ids=['heater','aspirator','feed_pump','nozzle','spray_cylinder','cyclone','outlet_filter','collection_vessel','separation_flask'],state_ids=evolving,meaning_zh='Basic水系开式抽吸三粒径液滴分组，显式宿主系数',implementation='lumped',equations=['rho_feed=1/(wsolid/rho_solid+(1-wsolid)/rho_water)','d0=dref*(Qfeed/Qref)^0.25*(Qgasref/Qgas)^0.35*(needle/0.7mm)^0.5; diameter factors[0.65,1,1.6], mass weights[0.25,0.5,0.25]','Tin_target=min(setpoint,ambient+heater_power/(gas_massflow*cp)); exponential approach with host tau','d_new^2=max(d_dry^2,d^2-K*(Tin-ambient)/50*dt)','evaporated=min(d2_proposals,gas_sensible_energy/latent,gas_massflow*host_humidity_gain*dt)','Tout=Tin-evaporation*latent/(gas_massflow*cp*dt)','active_solid*=exp(-k_ref*exp(E/R*(1/Tref-1/Tparticle))*dt); active/inactive are labels, total solid unchanged','wall_fraction=1-exp(-k_wall*(1+4*(water_fraction>0.15)+2*(Tp>Tsticky))*dt)','age>=Vchamber/Qactual: wet_fraction>0.15 -> separation flask; otherwise cyclone eta=d^2/(d^2+d50_effective^2), residual -> filter -> exhaust','input_feed=stock+flying+wall+wet_reject+product+filter+vapor+exhaust_particles+removed','filter_pressure_increase=host_resistance*filter_mass*Qdry/Qref'],parameters={},source_ids=['manufacturer_manual','simulation_assumption'],limitations=['One water-only batch per instance,homogeneous single solid solution/suspension,not solubility/emulsion prediction.','No organic/inert loop,no ultrasonic/three-fluid nozzle,no second pump,no optional product gas-temperature sensor.','Atomization,D2 evaporation,humidity ceiling,wall deposition,cyclone d50,filter resistance and Arrhenius parameters are host assumptions,not calibrated S300.','Gas volume reference temperature/pressure is explicit host metadata; manual volume reference conditions unknown.','Gas thermal system is lumped; heat/gas mixing and sensible feed enthalpy are approximated, no complete energy balance or certified heater efficiency.','Wet particle temperature=ambient+0.3*(outlet-ambient),dry=outlet: engineering approximation,not actual particle-core sensor.','Age-based residence,3size cohorts,no CFD,porosity/hollowness/agglomeration or full particle-size distribution.','Wall/product/filter/wet-reject deposits keep their captured masses and activity; postdeposition evaporation/degradation is not modeled.','Cancel stops feed only; remaining cohorts continue while airflow is on. Poweroff has no active vaporization/transport; environmental cooling and in-flight damage still evolve.','Cyclone dry-solid yield is captured/initial supplied solid,not guaranteed manufacturer up-to70percent yield.','Advertised1–60um family range not forced on synthetic output; standard two-fluid product comparison1–25um distinct.','50 reference GFA/thermal rows are B290 ethanol/acetone inert-loop outcomes,not measured S300 water yields/size; only original-condition lookup is supported.','Host attach/release events do not create robot constraints; no real compressor dynamics,explosion protection,gas certification,cleaning/flush chemistry or hardware communication.'])
    _ann.annotate_export('buchi_minispray',caps,slots,{'water_spray_drying':mechanism},inputs={'plant.configure':(PLANT,EXAMPLE)})
    return dict(asset_type_id=ASSET_TYPE_ID,catalog_id=CATALOG_ID,capabilities=caps,state_slots=slots,mechanisms={'water_spray_drying':mechanism})
